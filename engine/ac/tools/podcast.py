"""Podcast Multicam: the camera follows whoever is talking (one mic track per speaker, stacked camera tracks).

analyze  per-mic loudness in SEQUENCE time (10 ms envelope of each speaker's mic track) -> frame owner (a mic
         owns a frame when it is above its own gate and >= dom_db louder than every other mic; two or more mics
         on with nobody dominant = crosstalk) -> base shots with hysteresis (switch only after `confirm` s of
         floor, never before `min_shot`, cut `preroll` s before the speaker's first frame; crosstalk >= 1 s and
         silence >= 2,5 s go wide) -> cutaways inside long shots (listener reactions, a wide or listener shot
         every `max_shot` s) -> review file of shots (+ speaker lanes for the panel ribbon, sync offsets).
apply    edited review -> plan {"kind": "multicam"}: frame-aligned shots per camera track plus the razor points
         (only tracks whose state changes) for panel/host/40_podcast.jsx, which razors the camera tracks of a
         clone and disables the inactive clips (TrackItem.disabled). Audio is never cut.

Research and measurements: docs/research/product_cutting.md section 4 (prototypes proto_cutting/podcast_sim.py
and sync_test.py). Real multi-mic footage is UNVERIFIED: tested on a synthetic 2-mic split of real Indonesian
speech (engine/tests/test_podcast.py).

Params (analyze): speakers [{name, mic: audio track index, cam: video track index, prio: 0|1|2}] (>= 2),
wide (video track index or null), confirm 0.8, min_shot 2.0, max_shot 15 (0 = no cutaways for long shots),
react (true), cross_wide (true), silence_wide (true), dom_db 6, preroll 0.25, sync (true), scope.
"""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np

from .. import media as M, review as RV
from ..i18n import dec, tr
from ..timeline import Timeline
from ..util import EngineError, cache_path, file_hash, read_json, run_ffmpeg, workdir, write_json

TITLE = "Podcast Multicam"
DESCRIPTION = "The camera follows whoever is talking, from one mic track for each speaker."

SILENT, CROSS, UNKNOWN = -1, -2, -3      # frame labels besides speaker indexes 0..n-1
MAX_SPEAKERS = 8
PSR_MIN = 5.0                             # sync peak-to-sidelobe ratio below this = not trusted (research)

PRESETS = {"tenang": {"confirm": 1.0, "min_shot": 4.0, "max_shot": 30.0},
           "normal": {"confirm": 0.8, "min_shot": 2.0, "max_shot": 15.0},
           "dinamis": {"confirm": 0.6, "min_shot": 1.8, "max_shot": 8.0}}
DEFAULTS = {"confirm": 0.8, "min_shot": 2.0, "max_shot": 15.0, "dom_db": 6.0, "preroll": 0.25, "smooth": 0.15,
            "cross_wide": True, "cross_min": 1.0, "silence_wide": True, "silence_min": 2.5, "wide_len": 2.5,
            "react": True, "react_len": 2.0, "react_gap": 6.0, "sync": True, "sync_window": 120.0}
LIMITS = {"confirm": (0.0, 5.0), "min_shot": (0.5, 30.0), "max_shot": (0.0, 600.0), "dom_db": (1.0, 30.0),
          "preroll": (0.0, 1.0), "smooth": (0.0, 0.5), "cross_min": (0.2, 10.0), "silence_min": (0.5, 30.0),
          "wide_len": (0.5, 20.0), "react_len": (0.5, 10.0), "react_gap": (1.0, 120.0), "sync_window": (10.0, 600.0)}


# ---------------------------------------------------------------- params

def options(p):
    """Defaults <- preset <- explicit params (numbers clamped). Unknown keys are ignored."""
    o = dict(DEFAULTS)
    o.update(PRESETS.get(str(p.get("preset") or "").lower(), {}))
    for k, v in (p or {}).items():
        if k not in DEFAULTS or v is None:
            continue
        try:
            o[k] = bool(v) if isinstance(DEFAULTS[k], bool) else float(v)
        except (TypeError, ValueError):
            raise EngineError("BAD_PARAM", tr("podcast.err.badParam", k=k, v=repr(v))) from None
    for k, (lo, hi) in LIMITS.items():
        o[k] = min(hi, max(lo, float(o[k])))
    return o


def _int(v):
    try:
        return int(v) if v is not None and v != "" else None
    except (TypeError, ValueError):
        return None


def speakers_of(p, tl):
    """Validated speaker list [{name, mic, cam, prio}] (EngineError with a message in the job language)."""
    raw = [s for s in (p.get("speakers") or []) if isinstance(s, dict)][:MAX_SPEAKERS]
    if len(raw) < 2:
        raise EngineError("NEED_SPEAKERS", tr("podcast.err.needSpeakers"), tr("podcast.err.needSpeakersHint"))
    atr = {t.index: t for t in tl.audio}
    vtr = {t.index: t for t in tl.video}
    out = []
    for k, s in enumerate(raw):
        name = " ".join(str(s.get("name") or "").split())[:40] or tr("podcast.speakerN", n=k + 1)
        mic, cam = _int(s.get("mic")), _int(s.get("cam"))
        if mic not in atr:
            raise EngineError("NO_MIC", tr("podcast.err.noMic", name=name), tr("podcast.err.noMicHint"))
        if not tl.audio_clips(include_muted=True, tracks=[mic]):
            raise EngineError("NO_MIC", tr("podcast.err.micEmpty", track=atr[mic].name, name=name), tr("podcast.err.micEmptyHint"))
        if cam not in vtr:
            raise EngineError("NO_CAM", tr("podcast.err.noCam", name=name), tr("podcast.err.noCamHint"))
        if not tl.video_clips(tracks=[cam], include_disabled=True):
            raise EngineError("NO_CAM", tr("podcast.err.camEmpty", track=vtr[cam].name, name=name), tr("podcast.err.camEmptyHint"))
        out.append({"name": name, "mic": mic, "cam": cam, "prio": max(0, min(2, _int(s.get("prio")) or 0))})
    seen = {}
    for s in out:
        if s["mic"] in seen:
            raise EngineError("SAME_MIC", tr("podcast.err.sameMic", a=seen[s["mic"]], b=s["name"], track=atr[s["mic"]].name),
                              tr("podcast.err.sameMicHint"))
        seen[s["mic"]] = s["name"]
    return out


def wide_of(p, tl, spk):
    wide = _int(p.get("wide"))
    if wide is None or wide < 0:
        return None
    vtr = {t.index: t for t in tl.video}
    if wide not in vtr or not tl.video_clips(tracks=[wide], include_disabled=True):
        raise EngineError("NO_CAM", tr("podcast.err.wideEmpty"), tr("podcast.err.wideEmptyHint"))
    if any(s["cam"] == wide for s in spk):
        raise EngineError("NO_CAM", tr("podcast.err.wideUsed", track=vtr[wide].name), tr("podcast.err.wideUsedHint"))
    return wide


def cams_of(spk, wide, tl):
    """Camera tracks in display order: [{track, name, kind: 'spk'|'wide', spk: [speaker idx], track_name}]."""
    vtr = {t.index: t for t in tl.video}
    cams = []
    for k, s in enumerate(spk):
        c = next((c for c in cams if c["track"] == s["cam"]), None)
        if c:
            c["spk"].append(k)
            c["name"] += " + " + s["name"]
        else:
            cams.append({"track": s["cam"], "name": s["name"], "kind": "spk", "spk": [k],
                         "track_name": vtr[s["cam"]].name})
    if wide is not None:
        cams.append({"track": wide, "name": "Wide", "kind": "wide", "spk": [], "track_name": vtr[wide].name})
    if len(cams) < 2:
        raise EngineError("NO_SWITCH", tr("podcast.err.noSwitch"), tr("podcast.err.noSwitchHint"))
    return cams


# ---------------------------------------------------------------- mic sources (shared files -> channel / stream)

def _key(path):
    return str(Path(path)).lower()


def mic_sources(tl, spk):
    """How to read each speaker's mic media: {speaker idx: {path_key: None | ("ch", n) | ("stream", n)}} + notes.
    A media file used by the mic tracks of SEVERAL speakers (dual-mono recorder file split into two tracks in
    Premiere, or an OBS file with one audio stream per mic) is split: speakers sorted by mic track get channel
    (or stream) 0, 1, ... Plain files are read as a mono downmix (the shared envelope cache)."""
    users = {}
    for k, s in enumerate(spk):
        for c in tl.audio_clips(include_muted=True, tracks=[s["mic"]]):
            lst = users.setdefault(_key(c.path), [])
            if k not in lst:
                lst.append(k)
    how, notes = {k: {} for k in range(len(spk))}, []
    for key, ks in users.items():
        if len(ks) < 2:
            continue
        ks.sort(key=lambda k: spk[k]["mic"])
        names = f" {tr('podcast.and')} ".join(spk[k]["name"] for k in ks)
        path = next(c.path for s in spk for c in tl.audio_clips(include_muted=True, tracks=[s["mic"]])
                    if _key(c.path) == key)
        info = M.probe(path) if Path(path).is_file() else {"audio_streams": 0, "channels": 0}
        if info["audio_streams"] >= len(ks):
            mode = "stream"
            notes.append(tr("podcast.note.splitStream", names=names))
        elif info["channels"] >= len(ks):
            mode = "ch"
            notes.append(tr("podcast.note.splitCh", names=names))
        else:
            notes.append(tr("podcast.note.shared", names=names))
            continue
        for r, k in enumerate(ks):
            how[k][key] = (mode, r)
    return how, notes


def _channel_env(path, ch):
    """10 ms envelope of one channel of a media file, cached next to it (<stem>_env_ch<n>.npy + .json)."""
    npy, meta_p = cache_path(path, f"_env_ch{ch}.npy"), cache_path(path, f"_env_ch{ch}.json")
    h = file_hash(path)
    meta = read_json(meta_p, {}) or {}
    if meta.get("v") == M.ENV_VERSION and meta.get("hash") == h and npy.is_file():
        try:
            db = np.load(npy)
            if len(db) == meta.get("n"):
                return db
        except (OSError, ValueError):
            pass
    db = M.rms_db(_load_pcm(path, ("ch", ch)))
    try:
        tmp = npy.with_name(npy.stem + ".tmp.npy")
        np.save(tmp, db)
        tmp.replace(npy)
        write_json(meta_p, {"v": M.ENV_VERSION, "hash": h, "n": int(len(db)), "ch": ch, "hop": M.HOP, "win": M.WIN})
    except OSError:
        pass   # cache is an optimisation only
    return db


def _load_pcm(path, how=None, start=None, dur=None):
    """16 kHz mono float32 of a media file (whole or a window): downmix, one stream or one channel."""
    if how is None:
        return M.load_audio(path, start=start, dur=dur)
    if how[0] == "stream":
        return M.load_audio(path, stream=f"0:a:{how[1]}", start=start, dur=dur)
    if not M.probe(path)["has_audio"]:
        raise EngineError("NO_AUDIO", tr("podcast.err.noAudio", name=Path(path).name))
    args = ["-ss", f"{max(0.0, start):.3f}"] if start is not None else []
    args += ["-i", str(path)]
    if dur is not None:
        args += ["-t", f"{max(0.0, dur):.3f}"]
    args += ["-vn", "-af", f"pan=mono|c0=c{int(how[1])}", "-ar", str(M.SR), "-f", "f32le", "-"]
    return np.frombuffer(run_ffmpeg(args).stdout, np.float32)


def _paint(out, c, env):
    """Max-merge a source envelope into the sequence-time array `out` for clip c (speed aware)."""
    a, b = max(0, int(round(c.start / M.HOP))), min(len(out), int(round(c.end / M.HOP)))
    if b <= a or len(env) == 0:
        return
    ks = np.arange(a, b)
    si = np.floor((c.src_in + (ks * M.HOP - c.start) * c.speed) / M.HOP + 1e-6).astype(np.int64)
    ok = (si >= 0) & (si < len(env))
    out[ks[ok]] = np.maximum(out[ks[ok]], env[si[ok]])


def mic_envelopes(tl, spk, how, emit=None):
    """Per speaker: float32 dB array in sequence time (ceil(duration/HOP) frames, FLOOR where the mic track has
    no clip). Muted mic tracks are read too (muting a mic in Premiere does not change who talks). -> (dbs, notes)"""
    n = int(math.ceil(tl.duration / M.HOP))
    dbs, notes, envs = [], [], {}
    clips = [(k, c) for k, s in enumerate(spk) for c in tl.audio_clips(include_muted=True, tracks=[s["mic"]])]
    for k in range(len(spk)):
        dbs.append(np.full(n, M.FLOOR_DB, np.float32))
    for j, (k, c) in enumerate(clips):
        if not Path(c.path).is_file():
            notes.append(tr("podcast.note.micMissing", name=Path(c.path).name))
            continue
        h = how[k].get(_key(c.path))
        ek = (_key(c.path), h)
        if ek not in envs:
            if h is None:
                envs[ek] = M.envelope(c.path)
            elif h[0] == "stream":
                envs[ek] = M.envelope(c.path, stream=f"0:a:{h[1]}")
            else:
                envs[ek] = _channel_env(c.path, h[1])
        _paint(dbs[k], c, envs[ek])
        if emit is not None:
            emit.progress(100.0 * (j + 1) / len(clips))
    return dbs, list(dict.fromkeys(notes))


# ---------------------------------------------------------------- who talks (port of podcast_sim.activity)

def frame_owner(dbs, dom_db=6.0, smooth=0.15, prio=None):
    """Frame labels: speaker index when that mic is on (own Otsu gate) and >= dom_db louder than every other mic,
    CROSS when >= 2 mics are on and nobody dominates (a unique highest-priority mic that is on wins those),
    UNKNOWN when some mic is on without dominance, SILENT when all mics are below their gates.
    A `smooth` s moving max bridges syllable gaps first. -> (labels int8 array, thresholds dB per mic)"""
    from scipy.ndimage import maximum_filter1d
    D = np.stack([np.asarray(d, np.float32) for d in dbs])
    k = int(round(smooth / M.HOP))
    if k > 0:
        D = maximum_filter1d(D, size=2 * k + 1, axis=1, mode="nearest")
    on = np.zeros(D.shape, bool)
    thr = []
    for i, d in enumerate(D):
        sig = d[d > M.FLOOR_DB + 0.5]
        t = M.otsu_db(sig) if len(sig) >= 100 else -50.0
        thr.append(round(float(t), 1))
        on[i] = M.gate(d, t)
    lab = np.full(D.shape[1], SILENT, np.int8)
    lab[on.any(0)] = UNKNOWN
    for i in range(len(D)):
        others = np.delete(D, i, 0).max(0)
        lab[on[i] & (D[i] - others >= dom_db)] = i
    lab[(on.sum(0) >= 2) & (lab == UNKNOWN)] = CROSS
    if prio is not None and any(prio):
        xs = np.flatnonzero(lab == CROSS)
        if len(xs):
            pm = np.where(on[:, xs], np.asarray(prio)[:, None], -1)
            top = pm.max(0)
            win = (top > 0) & ((pm == top).sum(0) == 1)
            lab[xs[win]] = pm.argmax(0)[win]
    return lab, thr


def runs_of(lab):
    """Run-length encoding -> (labels, start frames, end frames)."""
    lab = np.asarray(lab)
    if len(lab) == 0:
        return lab, np.zeros(0, np.int64), np.zeros(0, np.int64)
    cut = np.flatnonzero(np.diff(lab)) + 1
    starts = np.concatenate([[0], cut]).astype(np.int64)
    ends = np.concatenate([cut, [len(lab)]]).astype(np.int64)
    return lab[starts], starts, ends


def lanes(lab, n_spk):
    """Speaker activity spans for the panel ribbon: {"0": [[t0, t1]], ..., "x": crosstalk spans}."""
    out = {}
    for k in range(n_spk):
        out[str(k)] = [[round(a, 2), round(b, 2)] for a, b in M.runs(lab == k, min_gap=0.3, min_len=0.1)]
    out["x"] = [[round(a, 2), round(b, 2)] for a, b in M.runs(lab == CROSS, min_gap=0.3, min_len=0.3)]
    return out


# ---------------------------------------------------------------- shots

def _frames(sec):
    return int(round(sec / M.HOP))


def base_shots(lab, n_spk, has_wide, o, lo, hi):
    """Speaker switching (port of podcast_sim.shots without its overflow insert, evaluated per run instead of per
    frame, same decisions): a run can switch the camera at its first frame f >= run start + need with
    f >= shot start + min_shot; the cut goes `preroll` s before the run (never before shot start + min_shot).
    Crosstalk runs >= cross_min and silent runs >= silence_min switch to the wide camera (id n_spk).
    -> [[t0, t1, cam id, why]] covering [lo, hi]."""
    hop, wide_id = M.HOP, n_spk
    i0, i1 = max(0, int(math.floor(lo / hop + 1e-9))), min(len(lab), int(math.ceil(hi / hop - 1e-9)))
    labs, starts, ends = runs_of(lab[i0:i1])
    starts, ends = starts + i0, ends + i0
    need_spk, need_x = _frames(o["confirm"]), _frames(max(o["confirm"], o["cross_min"]))
    need_s = _frames(o["silence_min"])
    shots, cur, start = [], None, lo
    for l, a, b in zip(labs.tolist(), starts.tolist(), ends.tolist()):
        if l >= 0:
            need, want, why, cutpt = need_spk, l, "bicara", a * hop - o["preroll"]
        elif l == CROSS and has_wide and o["cross_wide"]:
            need, want, why, cutpt = need_x, wide_id, "bersahutan", a * hop
        elif l == SILENT and has_wide and o["silence_wide"]:
            need, want, why, cutpt = need_s, wide_id, "diam", a * hop + 0.5
        else:
            continue
        if b - a <= need:
            continue
        if cur is None:
            shots.append([lo, None, want, "awal"])
            cur, start = want, lo
            continue
        if want == cur:
            continue
        f = max(a + need, int(math.ceil((start + o["min_shot"]) / hop - 1e-9)))
        if f >= b:
            continue
        cut = max(start + o["min_shot"], min(cutpt, f * hop))
        shots[-1][1] = cut
        shots.append([cut, None, want, why])
        cur, start = want, cut
    if not shots:
        shots = [[lo, None, wide_id if has_wide else 0, "awal"]]
    shots[-1][1] = hi
    return [s for s in shots if s[1] - s[0] > 1e-6]


def _pause_start(lab, t0, t1):
    """Start (s) of the longest non-speech run (SILENT/UNKNOWN) of >= 0.15 s inside [t0, t1], or None."""
    a, b = max(0, _frames(t0)), min(len(lab), _frames(t1))
    if b - a < 2:
        return None
    seg = (lab[a:b] == SILENT) | (lab[a:b] == UNKNOWN)
    best, best_len = None, _frames(0.15) - 1
    for r0, r1 in M.runs(seg, hop=1):
        r0, r1 = int(round(r0)), int(round(r1))
        if r1 - r0 > best_len:
            best, best_len = (a + r0) * M.HOP, r1 - r0
    return best


def add_cutaways(shots, lab, n_spk, has_wide, o):
    """Break long speaker shots: a listener reaction (another mic's short sound, shorter than `confirm`) cuts to
    that listener for react_len s; with no cutaway for `max_shot` s, cut at the best pause to the wide camera
    (or, without one, to the listener) for wide_len s. Every piece keeps >= min_shot on the speaker."""
    if not shots or (not o["react"] and not o["max_shot"]):
        return shots
    hop, wide_id, mn = M.HOP, n_spk, o["min_shot"]
    l_react, l_wide = max(mn, o["react_len"]), max(mn, o["wide_len"])
    labs, starts, ends = runs_of(lab)
    need = _frames(o["confirm"])
    bc = [(int(l), a * hop) for l, a, b in zip(labs.tolist(), starts.tolist(), ends.tolist())
          if l >= 0 and _frames(0.15) <= b - a < need] if o["react"] else []
    spk_shots = [(s[0], s[2]) for s in shots if s[2] < n_spk]
    out = []
    for s0, s1, cid, why in shots:
        if cid >= n_spk or s1 - s0 < 2 * mn + min(l_react, l_wide):
            out.append([s0, s1, cid, why])
            continue
        prev = [c for t, c in spk_shots if t < s0 and c != cid]
        nxt = [c for t, c in spk_shots if t > s0 and c != cid]
        listener = prev[-1] if prev else (nxt[0] if nxt else (cid + 1) % n_spk)
        cands = [(t, j) for j, t in bc if j != cid and s0 < t < s1]
        pos, w, cur_why = s0, why, why
        while True:
            r = next(((t - 0.3, j) for t, j in cands if t - 0.3 >= pos + max(mn, o["react_gap"])
                      and t - 0.3 + l_react <= s1 - mn), None)
            ov = None
            if o["max_shot"] and s1 - pos > o["max_shot"]:
                latest = min(pos + o["max_shot"], s1 - mn - l_wide)
                if latest >= pos + mn:
                    ps = _pause_start(lab, min(pos + max(mn, 0.6 * o["max_shot"]), latest), latest)
                    ov = ps + 0.1 if ps is not None and ps + 0.1 <= latest else latest
            if r is not None and (ov is None or r[0] <= ov):
                at, cam, ln, kind = r[0], r[1], l_react, "reaksi"
            elif ov is not None:
                at, cam, ln, kind = ov, (wide_id if has_wide else listener), l_wide, "selingan"
            else:
                break
            out.append([pos, at, cid, cur_why])
            out.append([at, at + ln, cam, kind])
            pos, cur_why = at + ln, "kembali"
        out.append([pos, s1, cid, cur_why])
    return out


def plan_shots(lab, n_spk, has_wide, o, lo, hi):
    """Base switching + cutaways, consecutive equal cameras merged. -> [[t0, t1, cam id, why]] (cam id n_spk =
    wide; a 'reaksi'/'selingan' shot with a speaker id shows that listener)."""
    shots = add_cutaways(base_shots(lab, n_spk, has_wide, o, lo, hi), lab, n_spk, has_wide, o)
    out = []
    for s in shots:
        if out and out[-1][2] == s[2]:
            out[-1][1] = s[1]
        elif s[1] - s[0] > 1e-6:
            out.append(list(s))
    return out


def to_tracks(shots, spk, wide, fps, lo, hi, min_shot):
    """Camera ids -> video track indexes, snapped to sequence frames, equal neighbours merged, a too short last
    shot folded into the previous one. -> [[t0, t1, track, cam id, why]]."""
    def snap(t):
        return round(round(t * fps) / fps, 6) if fps else round(t, 6)
    lo, hi = snap(lo), snap(hi)
    out = []
    for t0, t1, cid, why in shots:
        trk = wide if cid >= len(spk) else spk[cid]["cam"]
        a, b = max(lo, snap(t0)), min(hi, snap(t1))
        if out:
            a = out[-1][1]
        if b - a <= 1e-6:
            continue
        if out and out[-1][2] == trk:
            out[-1][1] = b
        else:
            out.append([a, b, trk, cid, why])
    while len(out) > 1 and out[-1][1] - out[-1][0] < min_shot - 1e-6:
        last = out.pop()
        out[-1][1] = last[1]
    if out:
        out[0][0], out[-1][1] = lo, hi
    return out


# ---------------------------------------------------------------- sync (port of sync_test.find_offset)

def find_offset(ref, other, sr=M.SR, max_lag=8.0):
    """PHAT cross-correlation. -> (seconds `other` is LATE relative to `ref` (negative = early), peak-to-sidelobe
    ratio). Robust to different mics, gain and EQ (research: 0.06 ms error, PSR 15, synthetic)."""
    from scipy import fft as F
    ref, other = np.asarray(ref, np.float32), np.asarray(other, np.float32)
    n = F.next_fast_len(len(ref) + len(other) - 1, real=True)
    X = F.rfft(ref, n)
    X *= np.conj(F.rfft(other, n))
    X /= np.abs(X) + 1e-9
    c = F.irfft(X, n)
    lag_max = max(1, min(int(max_lag * sr), n // 2 - 1))
    cand = np.concatenate([c[:lag_max], c[-lag_max:]])
    k = int(np.argmax(cand))
    lag = k if k < lag_max else k - 2 * lag_max
    side = np.sort(np.abs(cand))[-50:-1].mean()
    return -lag / sr, float(cand[k] / (side + 1e-12))


def _track_pcm(tl, kind, track, w0, w1, how=None):
    """16 kHz mono of one track over the sequence window [w0, w1) (zeros where no clip). Video tracks give their
    clips' own audio (camera scratch sound). Speed-changed clips are skipped."""
    n = int(round((w1 - w0) * M.SR))
    out = np.zeros(n, np.float32)
    if kind == "audio":
        clips = tl.audio_clips(include_muted=True, tracks=[track])
    else:
        clips = [c for c in tl.video_clips(tracks=[track], include_disabled=True) if c.has_media]
    for c in clips:
        a, b = max(c.start, w0), min(c.end, w1)
        if b - a < 0.5 or abs(c.speed - 1.0) > 1e-3 or not Path(c.path).is_file():
            continue
        if kind == "video" and not M.probe(c.path)["has_audio"]:
            continue
        x = _load_pcm(c.path, (how or {}).get(_key(c.path)), start=c.seq_to_src(a), dur=b - a)
        i = int(round((a - w0) * M.SR))
        m = max(0, min(len(x), n - i))
        out[i:i + m] = x[:m]
    return out


def sync_report(tl, spk, cams, how, lo, hi, o, emit=None):
    """Offsets of every other mic against the first speaker's mic, and of each camera track's own audio against
    the mic mix, over a window in the middle of [lo, hi]. -> [{kind, track, name, offset, psr, ok, warn}]"""
    span = hi - lo
    if span < 5:
        return []
    win = min(o["sync_window"], span)
    w0 = lo + (span - win) / 2
    w1, max_lag = w0 + win, min(8.0, win / 4)
    frame = 1.0 / (tl.fps or 30.0)
    jobs = len(spk) + len(cams)
    mics = []
    for k, s in enumerate(spk):
        mics.append(_track_pcm(tl, "audio", s["mic"], w0, w1, how[k]))
        if emit is not None:
            emit.progress(60.0 * (k + 1) / jobs)
    out = []

    def row(kind, track, name, x, ref):
        if not np.any(x) or not np.any(ref):
            return None
        off, psr = find_offset(ref, x, max_lag=max_lag)
        ok = psr >= PSR_MIN
        return {"kind": kind, "track": track, "name": name, "offset": round(off, 4), "psr": round(psr, 1),
                "ok": ok, "warn": bool(ok and abs(off) >= frame * 0.99)}

    for k in range(1, len(spk)):
        shared = bool(set(how[k]) & set(how[0]))
        r = ({"kind": "mic", "track": spk[k]["mic"], "name": spk[k]["name"], "offset": 0.0, "psr": None,
              "ok": True, "warn": False, "note": tr("podcast.sync.sameFile")} if shared
             else row("mic", spk[k]["mic"], spk[k]["name"], mics[k], mics[0]))
        if r:
            out.append(r)
    mix = np.sum(mics, axis=0)
    for j, c in enumerate(cams):
        x = _track_pcm(tl, "video", c["track"], w0, w1)
        r = row("cam", c["track"], c["name"], x, mix)
        if r:
            out.append(r)
        if emit is not None:
            emit.progress(60.0 + 40.0 * (j + 1) / len(cams))
    return out


def sync_text(r, ref_name):
    """Warning line (job language) for one sync row (or None)."""
    if not r.get("warn"):
        return None
    d = f"{dec(abs(r['offset']), 2)} {tr('unit.sec')}"
    what = tr("podcast.sync.mic" if r["kind"] == "mic" else "podcast.sync.cam", name=r["name"])
    ref = tr("podcast.sync.refMic", name=ref_name) if r["kind"] == "mic" else tr("podcast.sync.refMix")
    return tr("podcast.sync.late" if r["offset"] > 0 else "podcast.sync.early", what=what, d=d, ref=ref)


# ---------------------------------------------------------------- review

WHY_NOTE = ("bersahutan", "diam", "selingan", "kembali")      # shot reasons with an info note (podcast.why.<id>)


def _fmt_pct(x):
    return f"{int(round(x * 100))}%"


def build_items(tshots, lab, spk, cams, words=None):
    """Review rows, one per shot: kind spk|wide|react, cam (video track), spk (speaker idx or -1), why,
    conf (share of the shot's talk frames owned by the shown speaker), label (camera name), text (first words)."""
    n = len(spk)
    names = {c["track"]: c["name"] for c in cams}
    items = []
    wi = 0
    for t0, t1, trk, cid, why in tshots:
        a, b = _frames(t0), max(_frames(t0) + 1, _frames(t1))
        seg = lab[a:b]
        talk = seg[(seg >= 0) | (seg == CROSS)]
        if cid >= n:
            kind, sp = "wide", -1
            conf = None
            if why == "bersahutan" and len(seg):
                conf = float(np.mean(seg == CROSS))
            elif why == "diam" and len(seg):
                conf = float(np.mean(seg == SILENT))
        elif why in ("reaksi", "selingan"):
            kind, sp, conf = "react", cid, None
        else:
            kind, sp = "spk", cid
            conf = float(np.mean(talk == cid)) if len(talk) else None
        note = None
        if why == "reaksi":
            note = {"type": "info", "text": tr("podcast.note.reaction", name=spk[cid]["name"])}
        elif why in WHY_NOTE and why != "kembali":
            note = {"type": "info", "text": tr(f"podcast.why.{why}")}
        elif kind == "spk" and conf is not None and conf < 0.6:
            note = {"type": "warn", "text": tr("podcast.note.lowConf", name=spk[cid]["name"], pct=_fmt_pct(conf))}
        text = ""
        if words:
            while wi < len(words) and words[wi]["t1"] <= t0:
                wi += 1
            got = [w["text"].strip() for w in words[wi:wi + 40] if w["t0"] < t1][:10]
            text = " ".join(got)[:80]
        it = RV.item(t0, t1, kind, on=True, conf=conf, label=names.get(trk, f"V{trk + 1}"), note=note,
                     cam=trk, spk=sp, why=why, auto=trk)
        it["t0"], it["t1"] = round(t0, 6), round(t1, 6)        # exact frame times (RV.item rounds to ms)
        if text:
            it["text"] = text
        items.append(it)
    return items


def carry(doc, old):
    """Keep the user's choices from the previous review of the same sequence: `on` flags of touched rows and
    cameras picked by hand (`pick`), matched by stable id."""
    if not old or old.get("tool") != "podcast":
        return 0
    prev = {it.get("id"): it for it in old.get("items", []) if it.get("touched") or it.get("pick")}
    valid = {c["track"]: c["name"] for c in doc.get("cams", [])}
    n = 0
    for it in doc["items"]:
        o = prev.get(it["id"])
        if o is None:
            continue
        if o.get("touched"):
            it["on"], it["touched"] = bool(o.get("on")), True
        if o.get("pick") and _int(o.get("cam")) in valid:
            it["cam"], it["label"], it["pick"] = int(o["cam"]), valid[int(o["cam"])], True
        n += 1
    return n


def stats_of(tshots, spk, cams, lo, hi):
    tot = max(1e-9, hi - lo)
    share = {}
    for t0, t1, trk, cid, why in tshots:
        share[trk] = share.get(trk, 0.0) + (t1 - t0)
    lens = [t1 - t0 for t0, t1, *_ in tshots]
    wide = next((c["track"] for c in cams if c["kind"] == "wide"), None)
    return {"n": len(tshots), "on": len(tshots), "switches": max(0, len(tshots) - 1),
            "avg_shot": round(sum(lens) / len(lens), 2) if lens else 0.0,
            "min_shot": round(min(lens), 2) if lens else 0.0,
            "wide_pct": round(share.get(wide, 0.0) / tot, 3) if wide is not None else 0.0,
            "share": {str(k): round(v / tot, 3) for k, v in share.items()}, "speakers": len(spk)}


def summary_text(st):
    return tr("podcast.summary", n=st["switches"], speakers=st["speakers"])


# ---------------------------------------------------------------- actions

def analyze(job, emit):
    p = job.get("params") or {}
    tl = Timeline.from_json(job.get("seq"))
    o = options(p)
    spk = speakers_of(p, tl)
    wide = wide_of(p, tl, spk)
    cams = cams_of(spk, wide, tl)
    scope = tl.scope_ranges(p.get("scope"))
    lo, hi = max(0.0, scope[0][0]), min(tl.duration, scope[-1][1])
    if hi - lo < 1.0:
        raise EngineError("NO_RANGE", tr("podcast.err.noRange"), tr("podcast.err.noRangeHint"))
    stages = [("audio", tr("podcast.stage.audio"), 0.35), ("owner", tr("podcast.stage.owner"), 0.15)]
    if o["sync"]:
        stages.append(("sync", tr("podcast.stage.sync"), 0.35))
    stages.append(("shots", tr("podcast.stage.shots"), 0.15))
    emit.plan(stages)
    with emit.step("audio"):
        how, notes = mic_sources(tl, spk)
        dbs, more = mic_envelopes(tl, spk, how, emit)
        notes += more
        emit.done_note(tr("podcast.done.mics", n=len(spk)))
    with emit.step("owner"):
        lab, thr = frame_owner(dbs, o["dom_db"], o["smooth"], [s["prio"] for s in spk])
        a, b = _frames(lo), _frames(hi)
        talk = lab[a:b]
        talk = talk[talk != SILENT]
        owned = float(np.mean(talk >= 0)) if len(talk) else 0.0
        emit.progress(100)
        emit.done_note(tr("podcast.done.clear", pct=_fmt_pct(owned)))
    sync = []
    if o["sync"]:
        with emit.step("sync"):
            try:
                sync = sync_report(tl, spk, cams, how, lo, hi, o, emit)
            except EngineError as e:
                if e.code == "CANCELLED":
                    raise
                notes.append(tr("podcast.note.syncSkipped", msg=e.msg))
            bad = [r for r in sync if r.get("warn")]
            emit.done_note(tr("podcast.done.needMove", n=len(bad)) if bad else tr("podcast.done.inSync"))
    with emit.step("shots"):
        shots = plan_shots(lab, len(spk), wide is not None, o, lo, hi)
        tshots = to_tracks(shots, spk, wide, tl.fps, lo, hi, o["min_shot"])
        mic_tracks = [s["mic"] for s in spk]
        try:
            words = tl.words_on_timeline(tracks=mic_tracks, include_muted=True, transcribe=False)
        except Exception:   # noqa: BLE001  - words are decoration only
            words = []
        items = build_items(tshots, lab, spk, cams, words)
        st = stats_of(tshots, spk, cams, lo, hi)
        st["owned"] = round(owned, 3)
        for k, s in enumerate(spk):
            s["thr"], s["share"] = thr[k], st["share"].get(str(s["cam"]), 0.0)
        doc = RV.new("podcast", items, tl.duration, seq=tl, params=p, stats=st, fps=tl.fps,
                     scope=[tshots[0][0], tshots[-1][1]],
                     speakers=spk, cams=cams, wide=wide, lanes=lanes(lab, len(spk)), sync=sync, notes=notes)
        path = RV.path_for(workdir(job, tl.name), "podcast")
        kept = carry(doc, read_json(path))
        if kept:
            st["on"] = doc["stats"]["on"] = sum(1 for it in items if it["on"])
        RV.save(doc, path)
        emit.progress(100)
        emit.done_note(tr("podcast.done.switches", n=st["switches"]))
    for msg in notes:
        emit.warn(msg)
    for r in sync:
        msg = sync_text(r, spk[0]["name"])
        if msg:
            emit.warn(msg)
    return {"review": str(path), "summary": summary_text(st), "stats": st, "sync": sync, "notes": notes,
            "kept": kept}


def build_plan(doc):
    """Edited review -> multicam plan. A row switched off keeps the previous camera; the first row always counts.
    -> {"kind": "multicam", "timebase", "fps", "cams", "wide", "names", "shots": [[t0, t1, track]],
        "cuts": [[t, [tracks]]] (razor points: only tracks whose enabled state changes, plus every camera at the
        scope edges), "scope": [lo, hi]}"""
    items = sorted(doc.get("items") or [], key=lambda it: (float(it["t0"]), float(it["t1"])))
    if not items:
        raise EngineError("BAD_REVIEW", tr("podcast.err.noShots"), tr("podcast.err.rerun"))
    cams = [int(c["track"]) for c in doc.get("cams") or []]
    names = {str(c["track"]): c["name"] for c in doc.get("cams") or []}
    fps = float(doc.get("fps") or 30.0)

    def snap(t):
        return round(round(float(t) * fps) / fps, 6)
    eff = []
    for it in items:
        trk = _int(it.get("cam"))
        if trk not in cams:
            raise EngineError("BAD_REVIEW", tr("podcast.err.badCam", track=f"V{(trk or 0) + 1}"), tr("podcast.err.rerun"))
        if eff and not it.get("on", True):
            trk = eff[-1][2]
        a, b = snap(it["t0"]), snap(it["t1"])
        if eff:
            a = eff[-1][1]
        if b - a <= 1e-6:
            continue
        if eff and eff[-1][2] == trk:
            eff[-1][1] = b
        else:
            eff.append([a, b, trk])
    lo, hi = eff[0][0], eff[-1][1]
    cuts = []
    for prev, nxt in zip(eff, eff[1:]):
        cuts.append([nxt[0], sorted({prev[2], nxt[2]})])
    dur = snap(doc.get("duration") or hi)
    if lo > 1e-6:
        cuts.insert(0, [lo, sorted(cams)])
    if hi < dur - 1e-6:
        cuts.append([hi, sorted(cams)])
    return {"kind": "multicam", "timebase": "sequence", "fps": fps, "cams": cams, "wide": doc.get("wide"),
            "names": names, "shots": eff, "cuts": cuts, "scope": [lo, hi],
            "switches": max(0, len(eff) - 1)}


def apply(job, emit):
    with emit.step("plan", tr("podcast.stage.plan")):
        doc = RV.from_job(job)
        if doc.get("tool") != "podcast":
            raise EngineError("BAD_REVIEW", tr("podcast.err.notPodcast"), tr("podcast.err.rerun"))
        plan = build_plan(doc)
        emit.progress(100)
    tot = max(1e-9, plan["scope"][1] - plan["scope"][0])
    share = {}
    for a, b, trk in plan["shots"]:
        share[str(trk)] = round(share.get(str(trk), 0.0) + (b - a) / tot, 3)
    return {"plan": plan, "summary": tr("podcast.summaryApply", n=plan["switches"]),
            "stats": {"switches": plan["switches"], "shots": len(plan["shots"]), "razor": sum(len(c[1]) for c in plan["cuts"]),
                      "share": share}}


ACTIONS = {"analyze": analyze, "apply": apply}
