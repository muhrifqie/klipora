"""Potong Silence (tool id "silence"): find pauses in the WHOLE active sequence and cut, mark or mute them.

Algorithm (Silences v2, docs/research/product_cutting.md section 2.4), all in SEQUENCE time on 10 ms frames:
  1. Envelope = max dB over the chosen audio tracks (`Timeline.envelope_on_timeline`), so a frame is silent only
     when EVERY included track is silent. Default tracks = all unmuted audio tracks.
  2. Threshold = floor(Otsu valley of the source media envelopes) + user offset, an INTEGER dB. With integer
     thresholds `db >= thr` equals `floor(db) >= thr`, so the panel's live detector (panel/js/tools/silence.js,
     fed the floored envelope from `preview`) reproduces this plan exactly.
  3. Hysteresis gate (a run of frames >= thr-4 dB counts as sound when it holds a frame >= thr).
  4. Word guard (on by default, transcript cache only unless params.transcribe): frames inside transcript words
     (every word OVERLAPPING a clip, see timeline_words) that are >= thr-6 dB are forced to sound, dilated by
     3 frames (no bridging between words).
     Protected spans (included audio clips without a readable file, video-only spans) are forced to sound too.
  5. Pauses < min_silence are bridged; sound runs < min_talk are dropped unless they hold guarded frames.
  6. Keep = run padded by pad_before / pad_after; removed = scope minus keeps, snapped inward to frames,
     removals < 0.05 s dropped.

Actions:
  preview  fast data for the live panel: floored envelope (base64 uint8, dB+90), auto threshold, word spans,
           protected spans, scope, presets, plus the engine's own count for the given params (self-check).
  analyze  review file (gaps with context words and guard notes) -> {"review", "summary", "stats"}.
  apply    edited review -> plan by params.mode: "remove" (remove_ranges, ripple on a clone),
           "markers" (markers tagged [Klipora-SIL]), "mute" (mute_ranges: a clone with hold-key volume dips,
           applied by panel/host/30_silence.jsx).
"""
from __future__ import annotations

import base64
import bisect
import math
import time
from pathlib import Path

import numpy as np

from .. import media as M
from .. import ranges as R
from .. import review as RV
from ..timeline import Timeline
from ..i18n import dec, secs, tr
from ..util import EngineError, workdir

TITLE = "Cut Silences"          # developer listing (cli.py tools); UI text: tr("silence.title")
DESCRIPTION = "Remove silent pauses from the whole sequence. Automatic threshold, words stay intact."

# min_silence, pad_before, pad_after, min_talk (seconds). Measured on the 34.6 min tutorial: 0 words cut.
PRESETS = {
    "santai": {"min_silence": 0.80, "pad_before": 0.10, "pad_after": 0.25, "min_talk": 0.20},
    "natural": {"min_silence": 0.50, "pad_before": 0.06, "pad_after": 0.15, "min_talk": 0.15},
    "cepat": {"min_silence": 0.30, "pad_before": 0.04, "pad_after": 0.10, "min_talk": 0.12},
    "kilat": {"min_silence": 0.20, "pad_before": 0.02, "pad_after": 0.06, "min_talk": 0.10},
}
DEFAULT_PRESET = "natural"
LIMITS = {"min_silence": (0.05, 5.0), "pad_before": (0.0, 1.0), "pad_after": (0.0, 1.0), "min_talk": (0.0, 1.0)}
HOP = M.HOP
HYST = 4            # dB: a sound run continues while frames stay >= thr - HYST
GUARD_BELOW = 6     # dB: word frames >= thr - 6 are protected
GUARD_DILATE = 3    # frames (30 ms) of slack around protected word frames
MIN_CUT = 0.05      # s: shorter removals are dropped
VIDEO_ONLY_MIN = 0.1
THR_RANGE = (-85, -10)
OFFSET_RANGE = (-30, 30)
TAG = "[Klipora-SIL]"         # old projects: "[AC-SIL]" (cleared by bac_clearMarkersByTag too)
MODES = ("remove", "markers", "mute")
ENV_FLOOR = -90


# ---------------------------------------------------------------- settings

def _num(v, default=None):
    try:
        f = float(v)
        return default if math.isnan(f) or math.isinf(f) else f
    except (TypeError, ValueError):
        return default


def settings(p):
    """Effective settings from job params: preset values, overridden by explicit numbers (clamped)."""
    p = p or {}
    preset = p.get("preset") if p.get("preset") in PRESETS else DEFAULT_PRESET
    s = dict(PRESETS[preset], preset=preset)
    for k, (lo, hi) in LIMITS.items():
        v = _num(p.get(k))
        if v is not None:
            s[k] = round(min(hi, max(lo, v)), 4)
    if any(abs(s[k] - PRESETS[preset][k]) > 1e-9 for k in LIMITS):
        s["preset"] = "custom"
    s["offset"] = int(round(min(OFFSET_RANGE[1], max(OFFSET_RANGE[0], _num(p.get("offset"), 0.0)))))
    thr = _num(p.get("threshold"))
    s["threshold"] = None if thr is None else int(math.floor(min(THR_RANGE[1], max(THR_RANGE[0], thr))))
    s["guard"] = p.get("guard", True) is not False
    s["transcribe"] = bool(p.get("transcribe"))
    s["mode"] = p.get("mode") if p.get("mode") in MODES else "remove"
    return s


def nframes(sec):
    """Seconds -> whole 10 ms frames (round half up; mirrored in JS)."""
    return int(math.floor(sec / HOP + 0.5))


def fidx(t):
    """Time -> frame index (floor), identical to media.idx and the JS port."""
    return int(math.floor(t / HOP + 1e-9))


# ---------------------------------------------------------------- core detector (pure; mirrored in silence.js)

def _dilate(mask, k):
    out = mask.copy()
    for s in range(1, k + 1):
        out[s:] |= mask[:-s]
        out[:-s] |= mask[s:]
    return out


def _span_mask(spans, n):
    m = np.zeros(n, bool)
    for a, b in spans or []:
        i, j = max(0, fidx(a)), min(n, fidx(b) + 1)
        if j > i:
            m[i:j] = True
    return m


def speech_mask(db, thr, words=None, protect=None, guard=True):
    """-> (on, forced): on = sound frames (gate | forced); forced = guarded word frames | protected spans."""
    db = np.asarray(db)
    n = len(db)
    on = M.gate(db, thr, HYST)
    forced = np.zeros(n, bool)
    if guard and words:
        forced = _dilate(_span_mask(words, n) & (db >= thr - GUARD_BELOW), GUARD_DILATE)
    if protect:
        forced |= _span_mask(protect, n)
    return on | forced, forced


def _runs(mask):
    d = np.diff(np.concatenate([[0], np.asarray(mask, np.int8), [0]]))
    return list(zip(np.flatnonzero(d == 1).tolist(), np.flatnonzero(d == -1).tolist()))


def _complement(keeps, lo, hi):
    out, cur = [], lo
    for a, b in keeps:
        if a > cur:
            out.append([cur, a])
        cur = max(cur, b)
    if hi > cur:
        out.append([cur, hi])
    return out


def _intersect(a, b):
    out, i, j = [], 0, 0
    while i < len(a) and j < len(b):
        lo, hi = max(a[i][0], b[j][0]), min(a[i][1], b[j][1])
        if hi > lo:
            out.append([lo, hi])
        if a[i][1] < b[j][1]:
            i += 1
        else:
            j += 1
    return out


def plan(db, thr, cfg, words=None, protect=None, scope=None, dur=None, fps=None, guard=None):
    """Silence plan for one envelope. cfg = settings() dict (min_silence, pad_before, pad_after, min_talk).
    -> {"cuts": [[a, b]] removed sequence seconds (frame-snapped), "keeps": [[a, b]], "talks": n}."""
    db = np.asarray(db)
    n = len(db)
    dur = float(dur if dur is not None else n * HOP)
    guard = cfg.get("guard", True) if guard is None else guard
    on, forced = speech_mask(db, int(thr), words, protect, guard)
    ms, mt = nframes(cfg["min_silence"]), nframes(cfg["min_talk"])
    merged = []
    for a, b in _runs(on):
        if merged and a - merged[-1][1] < ms:
            merged[-1][1] = b
        else:
            merged.append([a, b])
    fc = np.concatenate([[0], np.cumsum(forced, dtype=np.int64)])
    pb, pa = float(cfg["pad_before"]), float(cfg["pad_after"])
    keeps = []
    talks = 0
    for a, b in merged:
        if b - a < mt and fc[b] - fc[a] == 0:
            continue
        talks += 1
        x0, x1 = max(0.0, a * HOP - pb), min(dur, b * HOP + pa)
        if keeps and x0 <= keeps[-1][1]:
            keeps[-1][1] = max(keeps[-1][1], x1)
        else:
            keeps.append([x0, x1])
    scope = [[max(0.0, a), min(dur, b)] for a, b in (scope or [[0.0, dur]]) if min(dur, b) > max(0.0, a)]
    cuts = []
    for a, b in _intersect(_complement(keeps, 0.0, dur), scope):
        if fps:
            a, b = math.ceil(a * fps - 1e-6) / fps, math.floor(b * fps + 1e-6) / fps
        if b - a >= MIN_CUT - 1e-9:
            cuts.append([a, b])
    return {"cuts": cuts, "keeps": keeps, "talks": talks}


# ---------------------------------------------------------------- timeline inputs

def _track_choice(tl, p):
    """-> (audio track indexes used for detection, include_muted). An explicit params.tracks list wins
    (muted tracks allowed); default = every unmuted audio track."""
    want = p.get("tracks")
    if isinstance(want, list) and want:
        ids = set()
        for v in want:
            f = _num(v)
            if f is not None:
                ids.add(int(f))
        return [t.index for t in tl.audio if t.index in ids], True
    return [t.index for t in tl.audio if not t.muted], False


def _no_audio(tl, include_muted, msg, hint):
    """NO_AUDIO error. When the default track choice (unmuted tracks) is empty because every track holding
    audio is muted in Premiere, say so instead of the generic `msg`."""
    muted = [t.name or f"A{t.index + 1}" for t in tl.audio if t.muted and t.clips]
    if muted and not include_muted:
        return EngineError("NO_AUDIO", tr("silence.err.allMuted", tracks=", ".join(muted)),
                           tr("silence.err.allMutedHint"))
    return EngineError("NO_AUDIO", msg, hint)


def _cover(clips):
    return R.norm([[c.start, c.end] for c in clips if c.end > c.start])


def signal(tl, tracks, include_muted, emit=None):
    """Envelope + protected spans for the chosen tracks. Raises NO_AUDIO / NO_MEDIA when nothing is readable."""
    clips = tl.clips("audio", tracks, include_muted=include_muted)
    if not clips:
        raise _no_audio(tl, include_muted, tr("silence.err.tracksEmpty"), tr("silence.err.tracksEmptyHint"))
    readable = [c for c in clips if c.has_media and Path(c.path).is_file()]
    ok = {id(c) for c in readable}
    unreadable = [c for c in clips if id(c) not in ok]
    if not readable:
        raise EngineError("NO_MEDIA", tr("silence.err.noMedia"), tr("silence.err.noMediaHint"))
    db = tl.envelope_on_timeline(tracks=tracks, include_muted=include_muted, emit=emit)
    audio_cov = _cover(clips)
    video_only = [r for r in R.subtract(_cover(tl.video_clips()), audio_cov) if r[1] - r[0] >= VIDEO_ONLY_MIN]
    bad = _cover(unreadable)
    return db, {"protect": R.round_list(R.union(bad, video_only), 4), "unreadable": len(unreadable),
                "unreadable_sec": round(R.total(bad), 3), "video_only": len(video_only),
                "video_only_sec": round(R.total(video_only), 3),
                "paths": list(dict.fromkeys(c.path for c in readable))}


def timeline_words(tl, tracks, include_muted, transcribe=False, emit=None):
    """Transcript words in SEQUENCE time for the word guard, plus how many media files have a transcript.

    Unlike Timeline.words_on_timeline (word kept only when its source MIDPOINT is inside the clip), every word
    that OVERLAPS a clip is mapped, clamped to the clip: Whisper spans stretch into pauses, so on an already
    cut sequence the midpoint of a fully audible word often sits in removed time (35 min Kilat cut: 211 of 2803
    words). Dropping those would unguard their audible frames and a re-run would clip them.
    -> ([{"text", "t0", "t1", "mid": midpoint inside the clip (context words), "key"}] sorted, stats)"""
    from .. import transcript as T
    if transcribe:      # builds the caches (GPU lock, progress inside the current stage); mapping below
        tl.words_on_timeline(tracks=tracks, include_muted=include_muted, transcribe=True, emit=emit)
    clips = [c for c in tl.audio_clips(include_muted, tracks) if Path(c.path).is_file()]
    rows_by = {}
    for path in dict.fromkeys(c.path for c in clips):
        rows = T.cached_words(path)
        if rows:
            rows_by[path] = (rows, [float(r[1]) for r in rows])
    out, seen = [], set()
    for c in clips:
        if c.path not in rows_by or c.end <= c.start:
            continue
        rows, starts = rows_by[c.path]
        lo, hi = min(c.src_in, c.src_out), max(c.src_in, c.src_out)
        for i in range(bisect.bisect_left(starts, lo - 30.0), len(rows)):
            s0, s1 = float(rows[i][1]), float(rows[i][2])
            if s0 >= hi:
                break
            if s1 <= lo:
                continue
            t0 = round(max(c.start, min(c.end, c.src_to_seq(max(s0, lo)))), 3)
            t1 = round(max(min(c.end, c.src_to_seq(min(s1, hi))), t0 + 0.01), 3)
            key = (c.path, i, t0)
            if key in seen:            # stereo twins (same media on A1 + A2)
                continue
            seen.add(key)
            out.append({"text": rows[i][0], "t0": t0, "t1": t1, "mid": lo <= (s0 + s1) / 2 < hi, "key": f"{i}"})
    out.sort(key=lambda w: (w["t0"], w["t1"]))
    return out, {"cached": len(rows_by), "total": len({c.path for c in clips})}


def auto_threshold(paths, db=None):
    """Otsu valley over the WHOLE source envelopes of the media in use (cached), not over the timeline:
    a sequence that was already cut has little silence left and its own valley drifts into the speech mode
    (49 s file: -53 dB on the raw media, -47 dB on its Natural cut, -42 dB on its Kilat cut), which is the
    "aggressive preset eats words" failure. Falls back to the timeline envelope when no source loads."""
    envs = []
    for p in paths or []:
        try:
            envs.append(np.asarray(M.envelope(p)))
        except EngineError:
            pass
    sel = np.concatenate(envs) if envs else np.asarray(db if db is not None else [])
    return float(M.otsu_db(sel))


def _threshold(s, auto):
    if s["threshold"] is not None:
        return s["threshold"]
    return int(min(THR_RANGE[1], max(THR_RANGE[0], math.floor(auto) + s["offset"])))


def _inputs(job, emit, transcribe=False):
    """Shared by preview and analyze: timeline, settings, tracks, envelope, words, scope, threshold."""
    p = job.get("params") or {}
    s = settings(p)
    tl = Timeline.from_json(job.get("seq"))
    if tl.duration <= 0:
        raise EngineError("NO_SEQ", tr("silence.err.emptySeq"), tr("silence.err.emptySeqHint"))
    tracks, inc_muted = _track_choice(tl, p)
    if not tracks:
        raise _no_audio(tl, inc_muted, tr("silence.err.noTracks"), tr("silence.err.noTracksHint"))
    ctx = {"p": p, "s": s, "tl": tl, "tracks": tracks, "inc_muted": inc_muted}
    with emit.step("audio"):
        db, info = signal(tl, tracks, inc_muted, emit)
        emit.done_note(tr("silence.note.audio", n=len(info["paths"]),
                          tracks=", ".join("A" + str(t + 1) for t in tracks)))
        emit.progress(100)
    ctx.update(db=db, info=info)
    with emit.step("words"):
        try:
            words, wm = timeline_words(tl, tracks, inc_muted, transcribe and s["guard"], emit)
        except EngineError:
            if transcribe:
                raise
            words, wm = [], {"cached": 0, "total": len(info["paths"])}
        emit.done_note(tr("silence.note.words", n=sum(1 for w in words if w["mid"])) if words
                       else tr("silence.note.noTranscript"))
        emit.progress(100)
    scope = [[round(max(0.0, a), 6), round(min(tl.duration, b), 6)] for a, b in tl.scope_ranges(p.get("scope"))]
    scope = [r for r in scope if r[1] > r[0]] or [[0.0, tl.duration]]
    auto = auto_threshold(info["paths"], db)
    ctx.update(gwords=words, words=[w for w in words if w["mid"]], spans=[[w["t0"], w["t1"]] for w in words],
               wm=wm, scope=scope, auto=auto, base=int(math.floor(auto)), thr=_threshold(s, auto))
    return ctx


# ---------------------------------------------------------------- notes / context

def _audible_loss(spans, db, thr, cuts):
    """Fraction of each word's audible frames (>= thr-6 dB) that `cuts` remove; None when barely audible."""
    n = len(db)
    rem = np.zeros(n, bool)
    for a, b in cuts:
        i, j = max(0, fidx(a)), min(n, fidx(b))
        if j > i:
            rem[i:j] = True
    aud = np.asarray(db) >= thr - GUARD_BELOW
    out = []
    for a, b in spans:
        i, j = max(0, fidx(a)), min(n, fidx(b) + 1)
        L = aud[i:j]
        k = int(L.sum())
        out.append(None if k < 3 else float((L & rem[i:j]).sum()) / k)
    return out


def _gap_dist(w, it):
    return max(0.0, it[0] - w[1], w[0] - it[1])


def _text(ws):
    return "".join(w["text"] for w in ws).strip()


def _sec(d):
    """Pause length for labels: '0,42 dtk' / '1,5 dtk' (en: '0.42 s')."""
    return f"{dec(d, 2 if d < 1 else 1)} {tr('unit.sec')}"


def _quote(words):
    return ", ".join(f'"{w}"' for w in words)


def build_items(c, cuts):
    """Review items for the removed ranges, with context words and guard / lost-word notes.
    -> (items, notes_stats)."""
    words, gw, spans, db, thr = c["words"], c["gwords"], c["spans"], c["db"], c["thr"]   # gw[k] <-> spans[k]
    s = c["s"]
    starts = [w["t0"] for w in words]
    protected, lost = {}, {}
    if gw:
        loss_now = _audible_loss(spans, db, thr, cuts)
        if s["guard"]:
            bare = plan(db, thr, s, words=None, protect=c["info"]["protect"], scope=c["scope"],
                        dur=c["tl"].duration, fps=c["tl"].fps, guard=False)["cuts"]
            loss_bare = _audible_loss(spans, db, thr, bare)
            for k, (lb, ln) in enumerate(zip(loss_bare, loss_now)):
                if lb is not None and lb > 0.05 and ln is not None and ln <= 0.05:
                    protected[k] = True
        for k, ln in enumerate(loss_now):
            if ln is not None and ln > 0.4:
                lost[k] = True
    attach_g, attach_l = {}, {}
    for k in protected:
        best, bd = None, 0.6
        for i, it in enumerate(cuts):
            d = _gap_dist(spans[k], it)
            if d < bd:
                best, bd = i, d
        if best is not None:
            attach_g.setdefault(best, []).append(k)
    for k in lost:
        for i, it in enumerate(cuts):
            if R.overlap(spans[k][0], spans[k][1], it[0], it[1]) > 0:
                attach_l.setdefault(i, []).append(k)
                break
    items = []
    for i, (a, b) in enumerate(cuts):
        d = b - a
        j = bisect.bisect_left(starts, a)
        pre = _text(words[max(0, j - 3):j]) if words else ""
        q = bisect.bisect_left(starts, b)
        post = _text(words[q:q + 3]) if words else ""
        note = None
        if i in attach_l:
            ws = list(dict.fromkeys(gw[k]["text"].strip() for k in attach_l[i]))
            note = {"type": "warn", "words": ws, "text": tr("silence.item.lost", words=_quote(ws), n=len(ws))}
        elif i in attach_g:
            ws = list(dict.fromkeys(gw[k]["text"].strip() for k in attach_g[i]))
            note = {"type": "guard", "words": ws,
                    "text": tr("silence.item.guardOne" if len(ws) == 1 else "silence.item.guardMany",
                               words=_quote(ws), n=len(ws))}
        items.append(RV.item(a, b, "gap", on=True, label=tr("silence.item.label", sec=_sec(d)),
                             ctx={"pre": pre, "post": post} if words else None, note=note,
                             cut=[round(a, 6), round(b, 6)]))
    stats = {"protected": len(protected),
             "protected_words": [gw[k]["text"].strip() for k in sorted(protected)][:12],
             "lost": len(lost), "lost_words": [gw[k]["text"].strip() for k in sorted(lost)][:12],
             "near_words": len(attach_g)}
    return items, stats


# ---------------------------------------------------------------- actions

def _env_b64(db):
    u = (np.clip(np.floor(np.asarray(db, np.float32)), ENV_FLOOR, 0) - ENV_FLOOR).astype(np.uint8)
    return base64.b64encode(u.tobytes()).decode("ascii")


def _track_rows(tl, used):
    rows = []
    for t in tl.audio:
        clips = [c for c in t.clips if not c.disabled]
        rows.append({"index": t.index, "name": t.name, "muted": t.muted, "locked": t.locked, "clips": len(clips),
                     "media": sum(1 for c in clips if c.has_media), "used": t.index in used})
    return rows


def preview(job, emit):
    t0 = time.time()
    emit.plan([("audio", tr("silence.stage.audio"), 0.8), ("words", tr("silence.stage.words"), 0.2)])
    c = _inputs(job, emit)
    tl, s = c["tl"], c["s"]
    chk = plan(c["db"], c["thr"], s, words=c["spans"], protect=c["info"]["protect"], scope=c["scope"],
               dur=tl.duration, fps=tl.fps)
    return {"v": 1, "seq": {"id": tl.id, "name": tl.name}, "dur": tl.duration, "fps": tl.fps, "hop": HOP,
            "n": int(len(c["db"])), "env": _env_b64(c["db"]), "floor": ENV_FLOOR,
            "auto": round(c["auto"], 2), "base": c["base"], "thr": c["thr"],
            "algo": {"hyst": HYST, "guard_below": GUARD_BELOW, "guard_dilate": GUARD_DILATE, "min_cut": MIN_CUT},
            "presets": PRESETS, "words": c["spans"], "words_n": len(c["words"]), "has_words": bool(c["words"]),
            "words_media": c["wm"],
            "protect": c["info"]["protect"], "video_only": c["info"]["video_only"],
            "unreadable": c["info"]["unreadable"], "scope": c["scope"],
            "tracks": _track_rows(tl, c["tracks"]), "used": c["tracks"],
            "check": {"n": len(chk["cuts"]), "sec": round(R.total(chk["cuts"]), 3), "thr": c["thr"],
                      "settings": {k: s[k] for k in PRESETS[DEFAULT_PRESET]}, "guard": s["guard"]},
            "ms": int((time.time() - t0) * 1000)}


def analyze(job, emit):
    t_start = time.time()
    p = job.get("params") or {}
    want_tx = bool(p.get("transcribe")) and p.get("guard", True) is not False
    emit.plan([("audio", tr("silence.stage.audio"), 0.3),
               ("words", tr("silence.stage.transcribe") if want_tx else tr("silence.stage.words"),
                0.6 if want_tx else 0.1),
               ("detect", tr("silence.stage.detect"), 0.2), ("review", tr("silence.stage.review"), 0.1)])
    c = _inputs(job, emit, transcribe=want_tx)
    tl, s = c["tl"], c["s"]
    with emit.step("detect"):
        res = plan(c["db"], c["thr"], s, words=c["spans"], protect=c["info"]["protect"], scope=c["scope"],
                   dur=tl.duration, fps=tl.fps)
        items, nstats = build_items(c, res["cuts"])
        emit.done_note(tr("silence.note.found", n=len(items), thr=str(c["thr"])))
        emit.progress(100)
    with emit.step("review"):
        sec = R.total(res["cuts"])
        scope_sec = R.total(c["scope"])
        eff = {k: s[k] for k in ("preset", "min_silence", "pad_before", "pad_after", "min_talk", "offset",
                                 "guard", "mode")}
        eff.update(threshold=c["thr"], auto=round(c["auto"], 2), tracks=c["tracks"], scope=p.get("scope"))
        stats = {"thr": c["thr"], "auto": round(c["auto"], 2), "base": c["base"], "offset": s["offset"],
                 "preset": s["preset"], "guard": s["guard"], "has_words": bool(c["words"]),
                 "words": len(c["words"]), "words_media": c["wm"], "dur": round(tl.duration, 3),
                 "after": round(tl.duration - sec, 3),
                 "save_pct": round(100.0 * sec / tl.duration, 1) if tl.duration else 0,
                 "scope": R.round_list(c["scope"], 3), "scope_sec": round(scope_sec, 3), "tracks": c["tracks"],
                 "talks": res["talks"], "protect": len(c["info"]["protect"]), "video_only": c["info"]["video_only"],
                 "unreadable": c["info"]["unreadable"], **nstats}
        doc = RV.new("silence", items, tl.duration, seq=tl, params=eff, stats=stats)
        path = RV.path_for(workdir(job, tl.name), "silence")
        try:
            old = RV.load(path) if path.is_file() else None
        except EngineError:
            old = None
        if old and p.get("carry", True) is not False and (old.get("seq") or {}).get("id") == tl.id:
            carried = RV.carry_over(doc, old)
            if carried:
                doc["stats"].update(RV.summary(doc), carried=carried)
                emit.log(f"{carried} manual choices carried over from the previous review")
        RV.save(doc, path)
        emit.progress(100)
    if c["info"]["unreadable"]:
        emit.warn(tr("silence.warn.unreadable", n=c["info"]["unreadable"]))
    if s["guard"] and not c["words"]:
        emit.warn(tr("silence.warn.noTranscript"))
    if nstats["lost"]:
        emit.warn(tr("silence.warn.lost", n=nstats["lost"]))
    doc["stats"]["ms"] = int((time.time() - t_start) * 1000)
    return {"review": str(path), "summary": RV.summary(doc), "stats": doc["stats"]}


def _ctx_text(it):
    cx = it.get("ctx")
    if isinstance(cx, dict):
        return " ... ".join(x for x in (cx.get("pre"), cx.get("post")) if x)
    return cx if isinstance(cx, str) else ""


def apply(job, emit):
    with emit.step("plan", tr("silence.stage.plan")):
        doc = RV.from_job(job)
        if doc.get("tool") not in (None, "silence"):
            raise EngineError("BAD_REVIEW", tr("silence.err.badReview"), tr("silence.err.badReviewHint"))
        p = job.get("params") or {}
        mode = p.get("mode") if p.get("mode") in MODES else (doc.get("params") or {}).get("mode") or "remove"
        sel = RV.selected(doc)
        rng = RV.selected_ranges(doc)
        sec = R.total(rng)
        dur = float(doc.get("duration") or 0)
        seq = doc.get("seq") or {}
        if mode == "markers":
            marks = []
            for it in sel:
                a, b = (it.get("cut") or [it["t0"], it["t1"]])[:2]
                marks.append({"t": round(float(a), 4), "end": round(float(b), 4), "tag": TAG, "color": 1,
                              "name": tr("silence.marker", sec=_sec(b - a)), "comment": _ctx_text(it),
                              "type": "Comment"})
            out = {"kind": "markers", "tag": TAG, "markers": marks, "seq": seq}
        elif mode == "mute":
            out = {"kind": "mute_ranges", "ranges": R.round_list(rng, 6), "timebase": "sequence",
                   "tracks": (doc.get("params") or {}).get("tracks") or [], "seq": seq}
        else:
            out = {"kind": "remove_ranges", "ranges": R.round_list(rng, 6), "timebase": "sequence", "seq": seq}
        emit.progress(100)
    after = dur if mode != "remove" else max(0.0, dur - sec)
    return {"plan": out, "mode": mode,
            "summary": tr("silence.summary." + mode, n=len(rng), sec=secs(sec)),
            "stats": {"n": len(rng), "sec": round(sec, 3), "before": round(dur, 3), "after": round(after, 3),
                      "projected": round(max(0.0, dur - sec), 3)}}


ACTIONS = {"preview": preview, "analyze": analyze, "apply": apply}
