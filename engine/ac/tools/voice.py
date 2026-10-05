"""Suara Jernih / Clear Voice (tool id "voice"): make the creator's voice pleasant to listen to, non-destructively.

Pipeline (all in SEQUENCE time; the voice track range is rendered to one WAV):
  1. Voice track = the audio track with the most transcript words (cached only), plus its "twins" (tracks that carry
     the same clips, e.g. a stereo pair split on A1+A2), which are muted with it. Tracks named "Klipora Suara"
     (our output; "AutoCut Suara" before the rename) never count; when one exists the source is a re-run and
     disabled voice clips are used again.
  2. analyze: assemble the voice audio (48 kHz mono), detect mouse/keyboard clicks and breaths in pauses (never
     inside words: transcript words + the filler listener pass are guarded), measure loudness (BS.1770, true peak),
     speech level and noise floor, classify the other tracks (music -> auto ducking). -> review file (Tinjau).
  3. apply: same audio, selected clicks/breaths attenuated with 5 ms ramps, denoise (DeepFilterNet3 ONNX on CPU,
     falls back to RNNoise (arnndn) then afftdn), FFmpeg chain (highpass, presence/warmth EQ, compressor, de-esser,
     "Tiru EQ" bands), limiter calibrated from a measurement pass, two-pass loudnorm (linear) to the platform
     target with true peak <= -1 dBTP, dual-mono 48 kHz 16-bit WAV (versioned name: Premiere locks imported
     files). Plan {kind: "voice"} for panel/host/43_voice.jsx: clone, disable the original voice clips on the clone,
     place the WAV on track "Klipora Suara", duck music clips with volume keys while the voice speaks.
  preview: 10 s "sebelum / sesudah" WAVs with the current settings. snippet: A/B WAV of one review item.
  tracks: fast track overview for the page (voice choice, music candidates). match_eq: "Tiru EQ dari contoh".
User-facing text goes through ac.i18n.tr (keys voice.*, Indonesian + English, per job language).
Doc: docs/tools/voice.md.
"""
from __future__ import annotations

import bisect
import math
import time
import wave
from pathlib import Path

import numpy as np

from .. import media as M
from .. import ranges as R
from .. import review as RV
from ..i18n import dec, tr
from ..timeline import Timeline
from ..util import EngineError, ensure_free, fmt_sec, read_json, safe_name, workdir
from . import _voice_detect as VD
from . import _voice_dsp as V

TITLE = "Clear Voice"            # developer listing (cli.py tools); UI text: tr("tool.voice.title")
DESCRIPTION = "Remove noise and clicks, and even out the voice loudness."

TRACK_NAME = "Klipora Suara"  # i18n-ignore (track name: the same in every UI language)
TRACK_NAMES = (TRACK_NAME, "AutoCut Suara")           # our output track, incl. the name used before the rename  # i18n-ignore
HELPER_PREFIXES = ("klipora ", "autocut ")             # any of our helper tracks (Klipora Sensor, AutoCut B-Roll, ...)
SR = V.SR
DEFAULTS = {"preset": "natural", "denoise": "ai", "platform": "youtube", "lufs": -14.0, "tp": -1.0,
            "clicks": True, "breaths": True, "click_db": -30.0, "breath_db": -12.0,
            "duck": True, "duck_db": -12.0, "attack": 0.25, "release": 0.6}
NUM_LIMITS = {"lufs": (-24.0, -9.0), "tp": (-3.0, -0.5), "click_db": (-60.0, -6.0), "breath_db": (-40.0, -3.0),
              "duck_db": (-30.0, -3.0), "attack": (0.05, 2.0), "release": (0.05, 3.0)}
MAX_RANGE = 3 * 3600
MUSIC_MIN = 0.5
PREVIEW_LEN = 10.0
LEAD = 3.0          # s of lead-in rendered (and dropped) before previews so denoise/compressor have settled


# ---------------------------------------------------------------- settings

def _num(v, default=None):
    try:
        f = float(v)
        return default if math.isnan(f) or math.isinf(f) else f
    except (TypeError, ValueError):
        return default


def _ints(v):
    if not isinstance(v, list):
        return None
    out = []
    for x in v:
        f = _num(x)
        if f is not None and int(f) not in out:
            out.append(int(f))
    return out


def settings(p):
    """Effective settings: preset chain values overridden by explicit numbers (clamped), plus the cleanup,
    loudness and ducking options. Unknown values fall back to DEFAULTS."""
    p = p or {}
    s = dict(DEFAULTS)
    preset = p.get("preset") if p.get("preset") in V.PRESETS else DEFAULTS["preset"]
    chain = dict(V.PRESETS[preset])
    for k, (lo, hi) in V.LIMITS.items():
        f = _num(p.get(k))
        if f is not None:
            chain[k] = round(min(hi, max(lo, f)), 3)
    s["preset"] = "custom" if any(abs(chain[k] - V.PRESETS[preset][k]) > 1e-9 for k in V.LIMITS) else preset
    s["base"] = preset
    s["chain"] = chain
    s["denoise"] = p.get("denoise") if p.get("denoise") in V.DENOISE else DEFAULTS["denoise"]
    plat = p.get("platform") if p.get("platform") in (*V.PLATFORMS, "custom") else DEFAULTS["platform"]
    s["platform"] = plat
    for k, (lo, hi) in NUM_LIMITS.items():
        f = _num(p.get(k))
        if f is not None:
            s[k] = round(min(hi, max(lo, f)), 3)
    if plat != "custom":
        s["lufs"] = V.PLATFORMS[plat]
    for k in ("clicks", "breaths", "duck"):
        if k in p:
            s[k] = p.get(k) is not False and p.get(k) not in (0, "0", "false")
    s["voice_tracks"] = _ints(p.get("voice_tracks")) or None
    s["duck_tracks"] = _ints(p.get("duck_tracks")) if isinstance(p.get("duck_tracks"), list) else None
    eq = []
    for row in p.get("eq") or []:
        if isinstance(row, (list, tuple)) and len(row) >= 2:
            f, g = _num(row[0]), _num(row[1])
            if f and 20 <= f <= 20000 and g is not None:
                eq.append([round(f), round(max(-6.0, min(6.0, g)), 1)])
    chain["eq"] = eq
    s["transcribe"] = bool(p.get("transcribe"))
    return s


# ---------------------------------------------------------------- timeline: voice / music tracks

def _ours(t):
    return (t.name or "").strip().lower() in tuple(n.lower() for n in TRACK_NAMES)


def _helper_track(t):
    return (t.name or "").strip().lower().startswith(HELPER_PREFIXES)


def _media_clips(track, include_disabled):
    return [c for c in track.clips if c.has_media and (include_disabled or not c.disabled) and c.end > c.start
            and Path(c.path).is_file()]


def _sig(clips):
    return tuple((c.path.lower(), round(c.start, 2), round(c.src_in, 2)) for c in clips)


def _word_rows(path):
    from .. import transcript as T
    try:
        return T.cached_words(path)
    except Exception:  # noqa: BLE001 - a broken cache just means "no transcript"
        return None


def _listen_rows(path):
    from .. import transcript as T
    try:
        d = read_json(T.listen_path(path), None)
    except Exception:  # noqa: BLE001
        return None
    return d.get("words") if isinstance(d, dict) and isinstance(d.get("words"), list) else None


def _count_words(clips):
    n = 0
    cache = {}
    for c in clips:
        if c.path not in cache:
            cache[c.path] = _word_rows(c.path) or []
        lo, hi = min(c.src_in, c.src_out), max(c.src_in, c.src_out)
        n += sum(1 for r in cache[c.path] if lo <= (float(r[1]) + float(r[2])) / 2 < hi)
    return n


def _music_info(clips):
    """Music score of a track's clips from the cached source envelopes (only the used source ranges)."""
    parts, words, dur = [], 0, 0.0
    for c in clips:
        try:
            env = M.envelope(c.path)
        except EngineError:
            continue
        lo, hi = min(c.src_in, c.src_out), max(c.src_in, c.src_out)
        parts.append(env[max(0, int(lo / M.HOP)): int(hi / M.HOP)])
        dur += hi - lo
    if not parts:
        return 0.0, {"active": 0.0, "mod": 0.0}
    score, info = VD.music_score(np.concatenate(parts))
    words = _count_words(clips)
    info["words_per_s"] = round(words / dur, 2) if dur > 0 else 0.0  # i18n-ignore
    if dur > 0 and words / dur > 0.8:          # transcribed speech is never ducked as music
        score = min(score, 0.2)
    return score, info


def track_plan(tl, s):
    """-> dict(voice=[idx] render tracks, mute=[idx] voice + twins, rerun, ours=[idx], music=[{...}], rows=[...])."""
    ours = [t.index for t in tl.audio if _ours(t)]
    rerun = any(t.clips for t in tl.audio if t.index in ours)
    rows = []
    for t in tl.audio:
        if t.index in ours or _helper_track(t):
            continue
        clips = _media_clips(t, include_disabled=rerun)
        if not clips:
            continue
        rows.append({"t": t, "clips": clips, "sig": _sig(clips), "words": _count_words(clips)})
    if not rows:
        raise EngineError("NO_AUDIO", tr("voice.err.noAudio"), tr("voice.err.noAudioHint"))
    want = s.get("voice_tracks")
    if want:
        voice = [r for r in rows if r["t"].index in want]
        if not voice:
            raise EngineError("NO_AUDIO", tr("voice.err.trackEmpty"), tr("voice.err.trackEmptyHint"))
        render = [voice[0]]
        mute = list(voice)
    else:
        unmuted = [r for r in rows if not r["t"].muted] or rows
        best = max(unmuted, key=lambda r: (r["words"], -r["t"].index))
        if best["words"] == 0:          # no transcript anywhere: first non-music track
            for r in unmuted:
                if _music_info(r["clips"])[0] < MUSIC_MIN:
                    best = r
                    break
        render = [best]
        mute = [r for r in rows if r is best or r["sig"] == best["sig"]]
    vidx = [r["t"].index for r in mute]
    music = []
    for r in rows:
        if r["t"].index in vidx:
            continue
        sc, info = _music_info(r["clips"])
        music.append({"track": r["t"].index, "name": r["t"].name or f"A{r['t'].index + 1}",
                      "muted": r["t"].muted, "clips": len(r["clips"]), "score": round(sc, 2),
                      "auto": sc >= MUSIC_MIN and not r["t"].muted, "info": info})
    return {"render": [r["t"].index for r in render], "mute": vidx, "rerun": rerun, "ours": ours, "music": music,
            "rows": [{"index": r["t"].index, "name": r["t"].name or f"A{r['t'].index + 1}", "muted": r["t"].muted,
                      "clips": len(r["clips"]), "words": r["words"], "voice": r["t"].index in vidx,
                      "render": r is render[0]} for r in rows]}


def voice_clips(tl, plan, scope=None):
    """Voice clips (render track) that intersect the scope, and the sequence range they cover."""
    tracks = set(plan["render"])
    clips = []
    for t in tl.audio:
        if t.index in tracks:
            clips += _media_clips(t, include_disabled=plan["rerun"])
    if scope:
        clips = [c for c in clips if any(R.overlap(c.start, c.end, a, b) > 0 for a, b in scope)]
    if not clips:
        raise EngineError("NO_AUDIO", tr("voice.err.noClipsInScope"), tr("voice.err.noClipsInScopeHint"))
    a, b = min(c.start for c in clips), max(c.end for c in clips)
    if b - a > MAX_RANGE:
        raise EngineError("TOO_LONG", tr("voice.err.tooLong"), tr("voice.err.tooLongHint"))
    return clips, a, b


def mute_clips(tl, plan, a, b):
    """Clips on the voice tracks (incl. twins) inside [a, b] that the host disables on the clone."""
    out = []
    for t in tl.audio:
        if t.index not in plan["mute"]:
            continue
        for c in _media_clips(t, include_disabled=plan["rerun"]):
            if c.start >= a - 1e-3 and c.end <= b + 1e-3:
                out.append({"track": t.index, "start": round(c.start, 6), "end": round(c.end, 6), "name": c.name})
    return out


def seq_words(clips, listen=True):
    """Transcript word spans (sequence time) of the voice clips, every word that OVERLAPS a clip (clamped), plus
    the filler listener pass ('eh', 'emm' that the main pass dropped), so nothing spoken is ever attenuated.
    -> (spans [[a, b]] sorted, words [{text, t0, t1}] main pass, has_transcript)."""
    spans, words, have = [], [], False
    cache = {}
    for c in clips:
        if c.path not in cache:
            cache[c.path] = (_word_rows(c.path) or [], (_listen_rows(c.path) or []) if listen else [])
        rows, lrows = cache[c.path]
        have = have or bool(rows)
        lo, hi = min(c.src_in, c.src_out), max(c.src_in, c.src_out)
        for r in rows:
            s0, s1 = float(r[1]), float(r[2])
            if s1 <= lo or s0 >= hi:
                continue
            t0, t1 = c.src_to_seq(max(s0, lo)), c.src_to_seq(min(s1, hi))
            spans.append([t0, max(t1, t0 + 0.01)])
            if lo <= (s0 + s1) / 2 < hi:
                words.append({"text": str(r[0]).strip(), "t0": round(t0, 3), "t1": round(t1, 3)})
        for r in lrows:                               # rough (+-0.2 s) timestamps: widen
            s0, s1 = float(r[1]) - 0.1, float(r[2]) + 0.1
            if s1 <= lo or s0 >= hi:
                continue
            t0, t1 = c.src_to_seq(max(s0, lo)), c.src_to_seq(min(s1, hi))
            spans.append([t0, max(t1, t0 + 0.01)])
    words.sort(key=lambda w: w["t0"])
    return R.norm(spans), words, have


def _env_spans(x, t0):
    """Speech spans from the envelope when there is no transcript (conservative: every loud run)."""
    db = M.rms_db(np.asarray(x[::3], np.float32), sr=16000)
    thr = M.otsu_db(db)
    return [[a + t0 - 0.05, b + t0 + 0.05] for a, b in M.voiced_spans(db, thr, min_gap=0.15, min_len=0.05)]


# ---------------------------------------------------------------- shared inputs

def _inputs(job, emit, stage_audio=True, scope_override=None):
    p = job.get("params") or {}
    s = settings(p)
    tl = Timeline.from_json(job.get("seq"))
    if tl.duration <= 0:
        raise EngineError("NO_SEQ", tr("voice.err.emptySeq"), tr("voice.err.emptySeqHint"))
    plan = track_plan(tl, s)
    scope = scope_override or [[max(0.0, a), min(tl.duration, b)] for a, b in tl.scope_ranges(p.get("scope"))]
    clips, a, b = voice_clips(tl, plan, scope)
    return {"p": p, "s": s, "tl": tl, "plan": plan, "clips": clips, "a": a, "b": b, "scope": scope}


def _detect(x, a, spans, have_words, s, emit=None):
    """Review items (clicks, breaths) for audio x starting at sequence time a."""
    items = []
    clicks_raw = VD.clicks(x, spans, t0=a, words_known=True) if s["clicks"] else []
    if emit:
        emit.progress(60)
    br_raw = []
    if s["breaths"] and have_words:
        br_raw = VD.breaths(x, spans, t0=a, exclude=[[c["t0"], c["t1"]] for c in clicks_raw])
    for c in clicks_raw:
        n = c["n"]
        label = tr("voice.click1") if n == 1 else tr("voice.typing" if n >= 4 else "voice.clicksN", n=n)
        fl = VD.floor_db(x, c["t0"], c["t1"], t0=a)
        items.append(RV.item(c["t0"], c["t1"], "click", on=True, conf=min(0.99, 0.45 + c["rise"] / 80.0),
                             label=label, db=round(max(s["click_db"], fl), 1), floor=round(fl, 1), n=n,
                             peak=round(c["peak"], 1)))
    for br in br_raw:
        conf = 0.55 + 0.4 * max(0.0, min(1.0, (0.5 - br["voiced"]) / 0.4))
        fl = VD.floor_db(x, br["t0"], br["t1"], t0=a)
        items.append(RV.item(br["t0"], br["t1"], "breath", on=True, conf=min(0.97, conf),
                             label=tr("voice.breathLabel", dur=fmt_sec(br["t1"] - br["t0"], 1)),
                             db=round(max(s["breath_db"], fl), 1),
                             floor=round(fl, 1), peak=round(br["peak"], 1)))
    return items


def item_db(it, s):
    """Attenuation for one item: the user's dB setting, never deeper than the surrounding sound (`floor`)."""
    base = s["click_db"] if it.get("kind") == "click" else s["breath_db"]
    fl = it.get("floor")
    return max(base, float(fl)) if isinstance(fl, (int, float)) else base


def _ctx(items, words):
    starts = [w["t0"] for w in words]
    for it in items:
        j = bisect.bisect_left(starts, it["t0"])
        pre = " ".join(w["text"] for w in words[max(0, j - 3):j])
        q = bisect.bisect_left(starts, it["t1"])
        post = " ".join(w["text"] for w in words[q:q + 3])
        it["ctx"] = {"pre": pre, "post": post}
    return items


def _measure(x, spans, a):
    I, lra = V.loudness(x)
    tp = V.true_peak(x)
    sp, nf = V.levels(x, spans, t0=a)
    r = lambda v, d=1: None if v is None or not math.isfinite(v) else round(v, d)  # noqa: E731
    return {"lufs": r(I), "lra": r(lra), "tp": r(tp), "speech_db": r(sp), "noise_db": r(nf)}


def _advice(m):
    out = []
    if m.get("noise_db") is not None and m["noise_db"] > -60:
        out.append({"id": "noise", "text": tr("voice.advice.noise")})
    if m.get("lufs") is not None and m["lufs"] < -24:
        out.append({"id": "quiet", "text": tr("voice.advice.quiet")})
    if m.get("tp") is not None and m["tp"] > -0.5:
        out.append({"id": "peak", "text": tr("voice.advice.peak")})
    return out


# ---------------------------------------------------------------- actions: tracks / analyze

def tracks(job, emit):
    t_start = time.time()
    p = job.get("params") or {}
    s = settings(p)
    tl = Timeline.from_json(job.get("seq"))
    plan = track_plan(tl, s)
    return {"tracks": plan["rows"], "voice": plan["render"], "mute": plan["mute"], "music": plan["music"],
            "rerun": plan["rerun"], "has_words": any(r["words"] for r in plan["rows"]),
            "ms": int((time.time() - t_start) * 1000)}


def analyze(job, emit):
    t_start = time.time()
    emit.plan([("audio", tr("voice.st.audio"), 0.35), ("words", tr("voice.st.words"), 0.1),
               ("detect", tr("voice.st.detect"), 0.3), ("measure", tr("voice.st.measure"), 0.15),
               ("review", tr("voice.st.review"), 0.1)])
    c = _inputs(job, emit)
    s, tl, plan, a, b = c["s"], c["tl"], c["plan"], c["a"], c["b"]
    with emit.step("audio"):
        x = V.render_timeline(c["clips"], a, b, progress=emit.sub(0, 100) if hasattr(emit, "sub") else None)
        emit.done_note(tr("voice.noteAudio", dur=fmt_sec(b - a),
                          tracks=", ".join("A" + str(i + 1) for i in plan["render"])))
    with emit.step("words"):
        if s["transcribe"]:
            tl.words_on_timeline(tracks=plan["render"], include_muted=True, transcribe=True, emit=emit)
        spans, words, have = seq_words(c["clips"])
        if not have:
            spans = _env_spans(x, a)
        emit.done_note(tr("voice.nWords", n=len(words)) if have else tr("voice.noTranscriptShort"))
        emit.progress(100)
    with emit.step("detect"):
        items = _detect(x, a, spans, have, s, emit)
        _ctx(items, words)
        nc = sum(1 for it in items if it["kind"] == "click")
        emit.done_note(tr("voice.noteDetect", clicks=nc, breaths=len(items) - nc))
        emit.progress(100)
    with emit.step("measure"):
        before = _measure(x, spans, a)
        emit.progress(100)
    with emit.step("review"):
        eff = {k: s[k] for k in ("preset", "denoise", "platform", "lufs", "tp", "clicks", "breaths", "click_db",
                                 "breath_db", "duck", "duck_db", "attack", "release")}
        eff["chain"] = s["chain"]
        stats = {"before": before, "range": [round(a, 3), round(b, 3)], "voice": plan["render"],
                 "mute": plan["mute"], "music": plan["music"], "rerun": plan["rerun"], "has_words": have,
                 "words": len(words), "clicks": sum(1 for it in items if it["kind"] == "click"),
                 "breaths": sum(1 for it in items if it["kind"] == "breath"), "advice": _advice(before),
                 "tracks": plan["rows"]}
        doc = RV.new("voice", items, tl.duration, seq=tl, params=eff, stats=stats)
        path = RV.path_for(workdir(job, tl.name), "voice")
        try:
            old = RV.load(path) if path.is_file() else None
        except EngineError:
            old = None
        if old and (old.get("seq") or {}).get("id") == tl.id:
            carried = RV.carry_over(doc, old)
            if carried:
                doc["stats"]["carried"] = carried
        RV.save(doc, path)
        emit.progress(100)
    if not have:
        emit.warn(tr("voice.warnNoTranscript"))
    doc["stats"]["ms"] = int((time.time() - t_start) * 1000)
    return {"review": str(path), "summary": RV.summary(doc), "stats": doc["stats"], "n": len(items)}


# ---------------------------------------------------------------- processing

def _apply_items(x, items, a):
    sel = [{"t0": float(it["t0"]), "t1": float(it["t1"]),
            "db": float(it.get("db", -30.0 if it.get("kind") == "click" else -12.0))} for it in items]
    if not sel:
        return x
    return x * VD.gain_curve(len(x), sel, t0=a)


def _items_from(job, s, kinds_db=True):
    """Selected review items (apply), with the current click/breath dB settings."""
    if not job.get("review"):
        return []
    doc = RV.from_job(job)
    if doc.get("tool") not in (None, "voice"):
        raise EngineError("BAD_REVIEW", tr("voice.err.badReview"), tr("voice.err.badReviewHint"))
    out = []
    for it in RV.selected(doc):
        if it.get("kind") == "click" and not s["clicks"]:
            continue
        if it.get("kind") == "breath" and not s["breaths"]:
            continue
        it = dict(it)
        if kinds_db:
            it["db"] = item_db(it, s)
        out.append(it)
    return out


def _write_wav(path, x, stereo=True):
    """16-bit PCM WAV (dual mono when stereo) with TPDF dither."""
    y = np.asarray(x, np.float64) * 32767.0
    y += (np.random.default_rng(0).random(len(y)) - np.random.default_rng(1).random(len(y)))
    y = np.clip(np.round(y), -32768, 32767).astype("<i2")
    if stereo:
        y = np.repeat(y, 2)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2 if stereo else 1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(y.tobytes())
    return path


def _read_wav_mono(path, start=None, dur=None):
    with wave.open(str(path), "rb") as w:
        ch, n = w.getnchannels(), w.getnframes()
        s0 = 0 if start is None else max(0, int(start * w.getframerate()))
        k = n - s0 if dur is None else min(n - s0, int(dur * w.getframerate()))
        w.setpos(s0)
        raw = w.readframes(k)
    y = np.frombuffer(raw, "<i2").astype(np.float32) / 32768.0
    return y.reshape(-1, ch).mean(1) if ch > 1 else y


def process_array(x, s, a, items, spans, emit=None, warn=None, tmpdir=None, out_wav=None, final=True):
    """Full chain on mono 48 kHz audio x (sequence start a). Writes out_wav (stereo s16) when given.
    -> (processed mono float32 or None when written to disk only, info dict)."""
    info = {}
    t0 = time.time()
    sub = (lambda lo, hi: emit.sub(lo, hi)) if emit is not None and hasattr(emit, "sub") else (lambda lo, hi: None)
    x = _apply_items(x, items, a)
    y, used = V.denoise(x, s["denoise"], s["chain"]["nr"], progress=sub(0, 45), warn=warn)
    info["denoise"] = used
    info["t_denoise"] = round(time.time() - t0, 2)
    I_mono, _ = V.loudness(y, stereo=False)
    pre = 0.0 if I_mono is None else max(-24.0, min(30.0, V.PRE_LEVEL - I_mono))
    info["pre_gain"] = round(pre, 2)
    filters = V.chain_filters(s["chain"], pre)
    info["filters"] = filters
    tmpdir = Path(tmpdir)
    tmpdir.mkdir(parents=True, exist_ok=True)
    stamp = f"{int(time.time() * 1000) % 10 ** 9}"
    f_in = tmpdir / f"_voice_in_{stamp}.f32"
    f_ch = tmpdir / f"_voice_chain_{stamp}.f32"
    dur = len(y) / SR
    try:
        np.asarray(y, np.float32).tofile(f_in)
        del y
        raw = ["-f", "f32le", "-ar", str(SR), "-ac", "1"]
        # pass 1: chain (zero-latency filters only) -> f32 file
        tp1 = time.time()
        V.run_ff([*raw, "-i", f_in, "-af", ",".join(filters + ["aformat=sample_fmts=flt:channel_layouts=mono"]) if filters
                  else "anull", "-f", "f32le", "-ac", "1", "-ar", str(SR), f_ch], dur, sub(45, 60))
        f_in.unlink(missing_ok=True)
        info["t_chain"] = round(time.time() - tp1, 2)
        tp1 = time.time()
        ch = np.memmap(f_ch, np.float32, "r")
        I_c, _ = V.loudness(ch)                                 # stereo-playback loudness of the chain output
        tp_c = V.true_peak(ch)
        del ch
        target, tp_t = s["lufs"], s["tp"]
        if I_c is None:
            raise EngineError("NO_AUDIO", tr("voice.err.tooQuiet"), tr("voice.err.tooQuietHint"))
        stereo = "pan=stereo|c0=c0|c1=c0"
        f_lim = tmpdir / f"_voice_lim_{stamp}.f32"
        # Pass 2 = limiter only (4x oversampled ~ true peak) -> f32 file, measured in numpy (BS.1770, verified equal
        # to FFmpeg ebur128). The ceiling is set so the static loudnorm gain keeps the true peak under the target
        # (loudnorm stays LINEAR); limiting lowers the loudness a little, so a second try corrects the ceiling.
        ceil_db = min(-0.1, tp_t - (target - I_c) - 1.0)
        for attempt in range(2):
            lim = (f"aresample={SR * 4},alimiter=limit={10 ** (ceil_db / 20):.5f}:attack=1:release=60:level=false:"
                   f"latency=1,aresample={SR}")
            V.run_ff([*raw, "-i", f_ch, "-af", lim, "-f", "f32le", "-ac", "1", "-ar", str(SR), f_lim], dur,
                     sub(60 + 10 * attempt, 66 + 10 * attempt))
            lm = np.memmap(f_lim, np.float32, "r")
            I_l, lra_l = V.loudness(lm)
            tp_l = V.true_peak(lm)
            del lm
            need = tp_l + (target - I_l)
            if need <= tp_t - 0.05 or attempt == 1:
                break
            ceil_db = min(-0.1, ceil_db - (need - tp_t) - 0.3)
        info["t_measure"] = round(time.time() - tp1, 2)
        info["measure_passes"] = attempt + 1
        tp1 = time.time()
        info.update(chain_lufs=round(I_c, 2), chain_tp=round(tp_c, 2), ceiling=round(ceil_db, 2),
                    limited_db=round(max(0.0, tp_c - ceil_db), 1), measured=[round(I_l, 2), round(tp_l, 2)])
        # second loudnorm pass with the measured values (linear = one static gain, true peak checked again)
        ln = (f"loudnorm=I={target}:TP={tp_t}:LRA=20:measured_I={I_l:.2f}:measured_TP={tp_l:.2f}:"
              f"measured_LRA={max(0.1, lra_l):.2f}:measured_thresh={I_l - 10:.2f}:offset=0:linear=true:"
              f"print_format=json")
        if out_wav is not None:
            ensure_free(int(dur * SR * 4) + 50 * 1024 ** 2, str(Path(out_wav).parent))
            txt2 = V.run_ff([*raw, "-i", f_lim, "-af", f"{stereo},{ln},aresample={SR}", "-ar", str(SR),
                             "-c:a", "pcm_s16le", out_wav], dur, sub(80, 97), loglevel="info")
            m2 = V.parse_loudnorm(txt2)
            info["normalization"] = m2.get("normalization_type")
            info["t_render"] = round(time.time() - tp1, 2)
            out = None
        else:
            out = V.ff_array_file(f_lim, f"{stereo},{ln},aresample={SR},pan=mono|c0=c0")
            info["normalization"] = "linear"
    finally:
        for f in (f_in, f_ch, tmpdir / f"_voice_lim_{stamp}.f32"):
            try:
                Path(f).unlink(missing_ok=True)
            except OSError:
                pass
    info["t_total"] = round(time.time() - t0, 2)
    return out, info


def _dense_window(words, a, b, length=PREVIEW_LEN):
    """Start of the window [t, t+length] inside [a, b] holding the most words."""
    if b - a <= length:
        return a
    starts = [w["t0"] for w in words]
    best, at = -1, a
    t = a
    while t + length <= b:
        k = bisect.bisect_left(starts, t + length) - bisect.bisect_left(starts, t)
        if k > best:
            best, at = k, t
        t += 2.0
    return at


def _stamp():
    return time.strftime("%H%M%S") + f"{int(time.time() * 1000) % 1000:03d}"


def _clean_previews(d, keep=6):
    files = sorted(Path(d).glob("*.wav"), key=lambda f: f.stat().st_mtime, reverse=True)
    for f in files[keep:]:
        try:
            f.unlink()
        except OSError:
            pass


# ---------------------------------------------------------------- actions: preview / snippet

def preview(job, emit):
    """10 s before/after at params.t (sequence s; default: the densest speech window) with the current settings."""
    t_start = time.time()
    emit.plan([("audio", tr("voice.st.audioShort"), 0.3), ("process", tr("voice.st.process"), 0.7)])
    c = _inputs(job, emit)
    s, tl, a, b = c["s"], c["tl"], c["a"], c["b"]
    p = c["p"]
    spans, words, have = seq_words(c["clips"])
    t = _num(p.get("t"))
    if t is None or not (a <= t < b):
        t = _dense_window(words, a, b)
    t = max(a, min(t, b - PREVIEW_LEN)) if b - a > PREVIEW_LEN else a
    t1 = min(b, t + PREVIEW_LEN)
    lead = min(LEAD, t - a)
    with emit.step("audio"):
        x = V.render_timeline(c["clips"], t - lead, t1)
        emit.progress(100)
    with emit.step("process"):
        if not have:
            spans = _env_spans(x, t - lead)
        items = _items_from(job, s) if job.get("review") else []
        if not job.get("review"):
            items = _detect(x, t - lead, spans, have, s)
        wd = workdir(job, tl.name) / "voice_preview"
        wd.mkdir(parents=True, exist_ok=True)
        y, info = process_array(x, s, t - lead, items, spans, emit=emit, tmpdir=wd, out_wav=None)
        k = int(round(lead * SR))
        stamp = _stamp()
        before = _write_wav(wd / f"sebelum_{stamp}.wav", x[k:])
        after = _write_wav(wd / f"sesudah_{stamp}.wav", y[k:k + len(x) - k])
        _clean_previews(wd)
        emit.progress(100)
    return {"before": str(before), "after": str(after), "t0": round(t, 3), "t1": round(t1, 3),
            "items": len(items), "info": {k2: info[k2] for k2 in ("denoise", "pre_gain", "normalization") if k2 in info},
            "ms": int((time.time() - t_start) * 1000)}


def snippet(job, emit):
    """A/B WAV for one review item: before, 0.35 s pause, after (only that item attenuated)."""
    p = job.get("params") or {}
    s = settings(p)
    tl = Timeline.from_json(job.get("seq"))
    plan = track_plan(tl, s)
    t0, t1 = _num(p.get("t0")), _num(p.get("t1"))
    if t0 is None or t1 is None or t1 <= t0:
        raise EngineError("BAD_JOB", tr("voice.err.badSnippet"), "")
    kind = p.get("kind") if p.get("kind") in ("click", "breath") else "click"
    db = _num(p.get("db"), s["click_db"] if kind == "click" else s["breath_db"])
    a, b = max(0.0, t0 - 0.8), min(tl.duration, t1 + 0.8)
    clips, _, _ = voice_clips(tl, plan, [[a, b]])
    x = V.render_timeline(clips, a, b)
    db = max(db, VD.floor_db(x, t0, t1, t0=a))
    y = _apply_items(x, [{"t0": t0, "t1": t1, "db": db}], a)
    gap = np.zeros(int(0.35 * SR), np.float32)
    wd = workdir(job, tl.name) / "voice_preview"
    wd.mkdir(parents=True, exist_ok=True)
    path = _write_wav(wd / f"ab_{_stamp()}.wav", np.concatenate([x, gap, y]))
    _clean_previews(wd, keep=10)
    return {"path": str(path), "dur": round((2 * len(x) + len(gap)) / SR, 3), "split": round(len(x) / SR, 3),
            "t0": round(a, 3)}


# ---------------------------------------------------------------- action: apply (render + plan)

def apply(job, emit):
    t_start = time.time()
    emit.plan([("audio", tr("voice.st.audio"), 0.1), ("process", tr("voice.st.process"), 0.75),
               ("check", tr("voice.st.check"), 0.1), ("plan", tr("voice.st.plan"), 0.05)])
    review = None
    if job.get("review"):
        review = RV.from_job(job)
    # The scope of the analysis is reused so the WAV covers exactly the analysed range.
    scope = None
    if review and isinstance((review.get("stats") or {}).get("range"), list):
        r0 = review["stats"]["range"]
        scope = [[float(r0[0]), float(r0[1])]]
    c = _inputs(job, emit, scope_override=scope)
    s, tl, plan, a, b = c["s"], c["tl"], c["plan"], c["a"], c["b"]
    wd = workdir(job, tl.name)
    out_dir = wd / "suara"
    out_dir.mkdir(parents=True, exist_ok=True)
    dur = b - a
    ensure_free(int(dur * SR * 4 * 2 + dur * SR * 4) + 100 * 1024 ** 2, str(out_dir))
    warns = []
    with emit.step("audio"):
        x = V.render_timeline(c["clips"], a, b, progress=emit.sub(0, 100) if hasattr(emit, "sub") else None)
        spans, words, have = seq_words(c["clips"])
        if not have:
            spans = _env_spans(x, a)
        before = _measure(x, spans, a)
        emit.progress(100)
    items = _items_from(job, s)
    out_wav = out_dir / f"{safe_name(tl.name, 50)}_suara_{time.strftime('%Y%m%d_%H%M%S')}.wav"  # i18n-ignore (file name)
    with emit.step("process"):
        win = _dense_window(words, a, b)
        k0, k1 = int((win - a) * SR), int((min(b, win + PREVIEW_LEN) - a) * SR)
        raw_win = np.array(x[k0:k1])
        _, info = process_array(x, s, a, items, spans, emit=emit, warn=lambda m: (warns.append(m), emit.warn(m)),
                                tmpdir=out_dir, out_wav=out_wav)
        del x
        emit.done_note(_denoise_label(info.get("denoise")))
    with emit.step("check"):
        y = _read_wav_mono(out_wav)
        after = _measure(y, spans, a)
        stamp = _stamp()
        pv = wd / "voice_preview"
        pv.mkdir(parents=True, exist_ok=True)
        pb = _write_wav(pv / f"hasil_sebelum_{stamp}.wav", raw_win)  # i18n-ignore (file name)
        pa = _write_wav(pv / f"hasil_sesudah_{stamp}.wav", y[k0:k1])  # i18n-ignore (file name)
        _clean_previews(pv, keep=8)
        exp = int(round(dur * SR))
        if abs(len(y) - exp) > SR // 100:
            warns.append(tr("voice.warnLength", dur=fmt_sec(abs(len(y) - exp) / SR, 2)))
        del y
        emit.progress(100)
    with emit.step("plan"):
        duck = None
        if s["duck"]:
            want = s["duck_tracks"] if s["duck_tracks"] is not None else [m["track"] for m in plan["music"] if m["auto"]]
            want = [i for i in want if i not in plan["mute"] and i not in plan["ours"]]
            if want:
                act = [[max(a, r0), min(b, r1)] for r0, r1 in VD.activity(spans, t0=a) if r1 > a and r0 < b]
                act = R.round_list(R.merge_gaps(act, s["attack"] + s["release"] + 0.2), 3)
                duck = {"tracks": want, "ranges": act, "db": s["duck_db"], "attack": s["attack"],
                        "release": s["release"]}
        out_plan = {"kind": "voice", "seq": {"id": tl.id, "name": tl.name}, "wav": str(out_wav),
                    "start": round(a, 6), "end": round(b, 6), "dur": round(dur, 6), "track_name": TRACK_NAME,
                    "render_tracks": plan["render"], "mute_tracks": plan["mute"],
                    "mute_clips": mute_clips(tl, plan, a, b), "duck": duck, "rerun": plan["rerun"]}
        emit.progress(100)
    size_mb = round(out_wav.stat().st_size / 1024 ** 2, 1)
    nclick = sum(1 for it in items if it.get("kind") == "click")
    summary = tr("voice.summary", dur=fmt_sec(dur), a=_lufs(before["lufs"]), b=_lufs(after["lufs"]))
    return {"plan": out_plan, "summary": summary,
            "stats": {"before": before, "after": after, "target": s["lufs"], "tp_target": s["tp"],
                      "denoise": info.get("denoise"), "normalization": info.get("normalization"),
                      "pre_gain": info.get("pre_gain"), "limited_db": info.get("limited_db"),
                      "timing": {k: info.get(k) for k in ("t_denoise", "t_chain", "t_measure", "t_render", "t_total",
                                                          "measure_passes")},
                      "clicks": nclick, "breaths": len(items) - nclick,
                      "duck_ranges": len(duck["ranges"]) if duck else 0, "duck_tracks": duck["tracks"] if duck else [],
                      "size_mb": size_mb, "secs": round(time.time() - t_start, 1), "t_denoise": info.get("t_denoise"),
                      "preview": {"before": str(pb), "after": str(pa), "t0": round(win, 3)}},
            "warnings": warns}


def _denoise_label(m):
    m = m or "off"
    return tr(f"voice.den.{m}") if m in ("ai", "rnnoise", "fft", "off") else ""


def _lufs(v):
    return "-" if v is None else dec(v, 1)


# ---------------------------------------------------------------- action: match_eq

def match_eq(job, emit):
    """"Tiru EQ dari contoh": EQ bands that move the voice towards the reference file's tonal balance."""
    p = job.get("params") or {}
    ref = p.get("ref")
    if not ref or not Path(ref).is_file():
        raise EngineError("NO_MEDIA", tr("voice.err.noRef"), tr("voice.err.noRefHint"))
    s = settings(p)
    tl = Timeline.from_json(job.get("seq"))
    plan = track_plan(tl, s)
    clips, a, b = voice_clips(tl, plan)
    spans, words, have = seq_words(clips)
    # at most 4 minutes of each side (dense speech) keeps this ~1-2 s
    w0 = _dense_window(words, a, b, length=min(240.0, b - a))
    mine = V.render_timeline(clips, w0, min(b, w0 + 240.0))
    mmask = VD.word_mask([[x - w0, y - w0] for x, y in spans], len(mine) // (SR // 100), 0.01, 0.0, guard=0.0) \
        if have else None
    info = M.probe(ref)
    rs = max(0.0, info["duration"] / 2 - 120.0) if info["duration"] > 240 else None
    refx = M.load_audio(ref, sr=SR, start=rs, dur=240.0 if rs is not None else None)
    if len(refx) < SR * 3:
        raise EngineError("BAD_MEDIA", tr("voice.err.refShort"), tr("voice.err.refShortHint"))
    bands = V.match_eq(refx, mine, mask_mine=mmask)
    return {"eq": bands, "ref": str(ref), "ref_name": Path(ref).name, "max": max(abs(g) for _, g in bands)}


ACTIONS = {"tracks": tracks, "analyze": analyze, "preview": preview, "snippet": snippet, "apply": apply,
           "match_eq": match_eq}
