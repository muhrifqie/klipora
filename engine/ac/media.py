"""Media helpers: probe, 16 kHz mono float32 audio, 10 ms RMS dB envelope (+ cache next to the media),
Otsu valley threshold, speech gate, quiet-point snapping, UI downsampling and single-frame grab.

Envelope frames: frame i covers [i*HOP, (i+1)*HOP) seconds, value = RMS dBFS of a 30 ms window centred on
the frame middle, clamped to FLOOR_DB (-90). Same math as the measured prototypes
(docs/research/product_cutting.md section 2.4), so their thresholds carry over: 34.6 min -> 207 671 frames,
Otsu valley -53.5 dB.
"""
from __future__ import annotations

from fractions import Fraction
from pathlib import Path

import numpy as np

from .i18n import dec, tr
from .util import EngineError, cache_path, file_hash, ffprobe, read_json, run_ffmpeg, write_json

SR = 16000
HOP = 0.01
WIN = 0.03
FLOOR_DB = -90.0
ENV_VERSION = 1

_PROBES = {}


# ---------------------------------------------------------------- probe

def require(path):
    """EngineError('NO_MEDIA') with a UI-language message when the media file is gone."""
    if not Path(path).is_file():
        raise EngineError("NO_MEDIA", tr("err.noMedia.msg", path=str(path)), tr("err.noMedia.hint"))
    return Path(path)


def probe(path):
    """-> {path, duration, fps (float), fps_frac 'num/den', width, height, has_video, has_audio,
    audio_streams, sample_rate, channels, vcodec, acodec}. Cached per file hash in-process."""
    require(path)
    key = (str(Path(path)), file_hash(path))
    if key in _PROBES:
        return dict(_PROBES[key])
    data = ffprobe(path)
    streams = data.get("streams", [])
    v = next((s for s in streams if s.get("codec_type") == "video"
              and not (s.get("disposition") or {}).get("attached_pic")), None)
    auds = [s for s in streams if s.get("codec_type") == "audio"]
    a = auds[0] if auds else None
    fr = Fraction(30)
    if v:
        for k in ("r_frame_rate", "avg_frame_rate"):
            try:
                f = Fraction(v.get(k) or "0")
                if 0 < f < 1000:
                    fr = f
                    break
            except (ValueError, ZeroDivisionError):
                pass
    dur = float((data.get("format") or {}).get("duration") or (a or v or {}).get("duration") or 0.0)
    info = {
        "path": str(Path(path)), "duration": dur, "fps": float(fr), "fps_frac": f"{fr.numerator}/{fr.denominator}",
        "width": int(v["width"]) if v else 0, "height": int(v["height"]) if v else 0,
        "has_video": v is not None, "has_audio": a is not None, "audio_streams": len(auds),
        "sample_rate": int(a.get("sample_rate", 48000)) if a else 48000,
        "channels": int(a.get("channels", 2)) if a else 0,
        "vcodec": v.get("codec_name") if v else None, "acodec": a.get("codec_name") if a else None,
    }
    _PROBES[key] = info
    return dict(info)


# ---------------------------------------------------------------- audio

def load_audio(path, sr=SR, stream=None, start=None, dur=None):
    """Mono float32 PCM via an ffmpeg pipe (no temp file). stream='0:a:1' picks an audio stream
    (OBS mic-only track); start/dur in seconds cut a window (fast input seek).
    34.6 min -> 33 M samples (133 MB) in ~1.8 s. EngineError('NO_AUDIO') if the file has no audio."""
    info = probe(path)
    if not info["has_audio"]:
        raise EngineError("NO_AUDIO", tr("err.noAudio.file", name=Path(path).name), tr("err.noAudio.fileHint"))
    args = []
    if start is not None:
        args += ["-ss", f"{max(0.0, start):.3f}"]
    args += ["-i", str(path)]
    if dur is not None:
        args += ["-t", f"{max(0.0, dur):.3f}"]
    if stream:
        args += ["-map", stream]
    args += ["-vn", "-ac", "1", "-ar", str(sr), "-f", "f32le", "-"]
    raw = run_ffmpeg(args).stdout
    return np.frombuffer(raw, np.float32)


def rms_db(x, sr=SR, hop=HOP, win=WIN, floor=FLOOR_DB):
    """RMS dBFS per hop (window `win` centred on each frame middle), clamped to `floor`. Vectorised via a
    cumulative sum of squares: 35 min of audio in ~0.3 s."""
    h, w = int(round(sr * hop)), int(round(sr * win))
    n = len(x) // h
    if n == 0:
        return np.zeros(0, np.float32)
    sq = np.concatenate([[0.0], np.cumsum(np.asarray(x, np.float64) ** 2)])
    c = np.arange(n) * h + h // 2
    a, b = np.clip(c - w // 2, 0, len(x)), np.clip(c + w // 2, 0, len(x))
    db = 10 * np.log10((sq[b] - sq[a]) / np.maximum(b - a, 1) + 1e-12)
    return np.maximum(db, floor).astype(np.float32)


def _env_paths(path, stream):
    suf = "_env" + (f"_{stream.replace(':', '')}" if stream else "")
    return cache_path(path, suf + ".npy"), cache_path(path, suf + ".json")


def envelope(path, stream=None, emit=None, refresh=False, audio=None):
    """10 ms dB envelope of a media file, cached as <stem>_env.npy + <stem>_env.json (meta with the media
    hash; a stale cache is rebuilt). Pass `audio` (16 kHz mono f32) when already decoded to skip ffmpeg.
    Returns float32 array; frame i <-> time i*HOP."""
    require(path)
    npy, meta_p = _env_paths(path, stream)
    h = file_hash(path)
    if not refresh:
        meta = read_json(meta_p, {}) or {}
        if meta.get("v") == ENV_VERSION and meta.get("hash") == h and npy.is_file():
            try:
                db = np.load(npy)
                if len(db) == meta.get("n"):
                    if emit:
                        emit.done_note(tr("note.cached"))
                    return db
            except (OSError, ValueError):
                pass
    if audio is None:
        audio = load_audio(path, stream=stream)
    db = rms_db(audio)
    try:
        tmp = npy.with_name(npy.stem + ".tmp.npy")
        np.save(tmp, db)
        tmp.replace(npy)
        write_json(meta_p, {"v": ENV_VERSION, "hash": h, "hop": HOP, "win": WIN, "sr": SR, "n": int(len(db)),
                            "stream": stream, "duration": round(len(audio) / SR, 3), "floor": FLOOR_DB})
    except OSError:
        pass  # cache is an optimisation only
    return db


def idx(t, hop=HOP):
    """Time (s) -> frame index (floor)."""
    return int(np.floor(t / hop + 1e-9))


def env_slice(db, t0, t1, hop=HOP):
    return db[max(0, idx(t0, hop)):max(0, idx(t1, hop))]


def downsample(db, bars=2000, mode="max"):
    """Envelope for drawing: at most `bars` values (max-pooled by default), rounded to 0.1 dB, as a list.
    Each bar covers len(db)/bars frames; the panel maps bar k to time k*len(db)*HOP/bars."""
    db = np.asarray(db, np.float32)
    if len(db) <= bars:
        return [round(float(v), 1) for v in db]
    edges = np.linspace(0, len(db), bars + 1).astype(int)
    red = np.maximum.reduceat if mode == "max" else np.add.reduceat
    out = red(db, edges[:-1])
    if mode != "max":
        out = out / np.maximum(np.diff(edges), 1)
    return [round(float(v), 1) for v in out]


# ---------------------------------------------------------------- thresholds / gates

def otsu_db(db):
    """Otsu valley over the dB histogram (1 dB bins, -100..0): the "Hitung otomatis" threshold.
    Our recordings are bimodal (gate floor -88, speech -27): 34.6 min -> -53.5 dB, 49 s -> -52.5 dB.
    Falls back to floor+20 dB when the signal is (near) unimodal (pure silence or wall-to-wall audio)."""
    db = np.asarray(db)
    if len(db) == 0:
        return -50.0
    h, e = np.histogram(np.clip(db, -100, 0), bins=100, range=(-100, 0))
    p, c = h / max(h.sum(), 1), (e[:-1] + e[1:]) / 2
    w0, m = np.cumsum(p), np.cumsum(p * c)
    var = (m[-1] * w0 - m) ** 2 / (w0 * (1 - w0) + 1e-12)
    var[(w0 < 1e-6) | (w0 > 1 - 1e-6)] = 0
    if var.max() <= 0:
        return float(min(-20.0, np.percentile(db, 10) + 20))
    # An EMPTY valley (very clean recordings) makes the between-class variance flat across the gap and
    # argmax would pick its lowest edge (just above the noise floor). Take the middle of the max plateau:
    # identical on the test media (-53.5 / -52.5 / -53.5), -57.5 instead of -87.5 on a synthetic gap.
    k = int(np.argmax(var))
    a, b, vmax = k, k, var[k] * (1 - 1e-3)
    while a > 0 and var[a - 1] >= vmax:
        a -= 1
    while b < len(var) - 1 and var[b + 1] >= vmax:
        b += 1
    return float((c[a] + c[b]) / 2)


def gate(db, thr, hyst=4.0):
    """Hysteresis gate: a run of frames >= thr-hyst counts as sound when it contains a frame >= thr."""
    db = np.asarray(db)
    hard, soft = db >= thr, db >= thr - hyst
    on = hard.copy()
    d = np.diff(np.concatenate([[0], soft.astype(np.int8), [0]]))
    for a, b in zip(np.flatnonzero(d == 1), np.flatnonzero(d == -1)):
        if hard[a:b].any():
            on[a:b] = True
    return on


def runs(mask, hop=HOP, min_gap=0.0, min_len=0.0):
    """True-runs of a frame mask -> [[t0, t1]] seconds; gaps < min_gap are bridged, runs < min_len dropped."""
    d = np.diff(np.concatenate([[0], np.asarray(mask, np.int8), [0]]))
    out = []
    for a, b in zip(np.flatnonzero(d == 1), np.flatnonzero(d == -1)):
        a, b = round(float(a) * hop, 6), round(float(b) * hop, 6)
        if out and a - out[-1][1] < min_gap:
            out[-1][1] = b
        else:
            out.append([a, b])
    return [r for r in out if r[1] - r[0] >= min_len]


def voiced_spans(db, thr=None, min_gap=0.3, min_len=0.06, hyst=4.0):
    """Sound regions [[t0, t1]] from the envelope (threshold = Otsu valley unless given)."""
    thr = otsu_db(db) if thr is None else thr
    return runs(gate(db, thr, hyst), min_gap=min_gap, min_len=min_len)


def snap_quiet(db, t, radius=0.03, hop=HOP):
    """Move a cut point to the quietest frame within +-radius (avoids clicks mid-syllable). Returns the
    frame start time; t unchanged when outside the envelope."""
    i, r = idx(t, hop), max(0, int(round(radius / hop)))
    lo, hi = max(0, i - r), min(len(db), i + r + 1)
    if hi <= lo:
        return t
    return round((lo + int(np.argmin(db[lo:hi]))) * hop, 6)


def first_audible(db, t0, t1, thr, hop=HOP):
    """Start time of the first frame >= thr in [t0, t1), or None."""
    a, b = max(0, idx(t0, hop)), min(len(db), idx(t1, hop) + 1)
    if b <= a:
        return None
    hit = np.flatnonzero(np.asarray(db[a:b]) >= thr)
    return round((a + int(hit[0])) * hop, 6) if len(hit) else None


def last_audible(db, t0, t1, thr, hop=HOP):
    """End time of the last frame >= thr in [t0, t1), or None."""
    a, b = max(0, idx(t0, hop)), min(len(db), idx(t1, hop) + 1)
    if b <= a:
        return None
    hit = np.flatnonzero(np.asarray(db[a:b]) >= thr)
    return round((a + int(hit[-1]) + 1) * hop, 6) if len(hit) else None


# ---------------------------------------------------------------- frames

def frame_grab(path, t, out=None, width=None):
    """One video frame at `t` seconds (fast input seek, accurate to the frame).
    out=None -> RGB uint8 ndarray (H, W, 3); out='x.png' -> writes PNG and returns the Path.
    width scales the frame (height keeps the aspect, even)."""
    info = probe(path)
    if not info["has_video"]:
        raise EngineError("NO_VIDEO", tr("err.noVideo.file", name=Path(path).name))
    t = max(0.0, min(float(t), max(0.0, info["duration"] - 1.0 / max(info["fps"], 1.0))))
    vf = []
    w, h = info["width"], info["height"]
    if width:
        w = int(width)
        h = int(round(info["height"] * w / info["width"] / 2)) * 2
        vf = ["-vf", f"scale={w}:{h}:flags=bicubic"]
    args = ["-ss", f"{t:.4f}", "-i", str(path), "-frames:v", "1", "-an", *vf]
    if out is not None:
        out = Path(out)
        out.parent.mkdir(parents=True, exist_ok=True)
        run_ffmpeg(args + ["-y", str(out)])
        return out
    raw = run_ffmpeg(args + ["-f", "rawvideo", "-pix_fmt", "rgb24", "-"]).stdout
    if len(raw) < w * h * 3:
        raise EngineError("FFMPEG", tr("err.ffmpeg.frame", t=dec(t, 2)))
    return np.frombuffer(raw[:w * h * 3], np.uint8).reshape(h, w, 3)
