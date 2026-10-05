"""DSP helpers for Suara Jernih (tool id "voice"): timeline audio assembly, ITU-R BS.1770 loudness and true peak
in numpy, FFmpeg filter runs with progress, denoise backends, preset chains and the "Tiru EQ" spectrum match.

All arrays are mono float32. Loudness numbers are reported for STEREO playback of dual-mono audio (what the
rendered WAV is), i.e. mono BS.1770 + 3.01 dB, so they match Premiere's loudness meter on the result clip.
"""
from __future__ import annotations

import json
import math
import re
import subprocess
import threading
import time
from pathlib import Path

import numpy as np

from .. import media as M
from ..i18n import tr
from ..util import EngineError, ffmpeg_exe

SR = 48000
STEREO_DB = 10 * math.log10(2.0)          # dual-mono stereo is 3.01 LU louder than its mono channel
MODEL_DIR = Path(__file__).resolve().parents[1] / "assets" / "models"
RNN_MODELS = {"sh": "rnnoise_sh.rnnn", "bd": "rnnoise_bd.rnnn"}
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


# ---------------------------------------------------------------- timeline assembly

def _contiguous(a, b):
    """True when clip b continues clip a seamlessly (same media, butted in sequence and source)."""
    return (a is not None and a.path == b.path and abs(a.end - b.start) < 1e-3 and abs(a.src_out - b.src_in) < 2e-3
            and abs(a.speed - b.speed) < 1e-6)


def render_timeline(clips, t0, t1, sr=SR, progress=None, fade=0.002):
    """Mono float32 audio of `clips` (sequence time) for [t0, t1). Overlapping clips are summed. Each clip gets
    2 ms edge fades except at seamless joins (cut sequences: no tick at every cut). Media used by many clips is
    decoded once. progress(pct) optional."""
    n = max(0, int(round((t1 - t0) * sr)))
    out = np.zeros(n, np.float32)
    clips = sorted(clips, key=lambda c: (c.start, c.track))
    by_path = {}
    for c in clips:
        if c.end > t0 and c.start < t1:
            by_path.setdefault(c.path, []).append(c)
    full = {}
    for p, cs in by_path.items():      # many / long pieces of one file: decode it once instead of seeking per clip
        info = M.probe(p)
        cover = sum((min(c.end, t1) - max(c.start, t0)) * (c.speed or 1.0) for c in cs)
        if len(cs) > 6 or cover > 0.5 * max(1e-6, info["duration"]):
            full[p] = None                 # decoded lazily below (once)
    prev_by_track = {}
    for k, c in enumerate(clips):
        if c.end <= t0 or c.start >= t1:
            continue
        sp = c.speed if c.speed and c.speed > 0 else 1.0
        lo, hi = max(c.start, t0), min(c.end, t1)
        s0 = c.seq_to_src(lo)
        need = (hi - lo) * sp
        if c.path in full:
            if full[c.path] is None:
                full[c.path] = M.load_audio(c.path, sr=sr)
            a = full[c.path]
            i0 = int(round(s0 * sr))
            seg = a[max(0, i0): max(0, i0) + int(round(need * sr)) + 2]
        else:
            seg = M.load_audio(c.path, sr=sr, start=s0, dur=need + 2.0 / sr)
        m = int(round((hi - lo) * sr))
        if abs(sp - 1.0) > 1e-6 and len(seg) > 1:
            xs = np.linspace(0, len(seg) - 1, m)
            seg = np.interp(xs, np.arange(len(seg)), seg).astype(np.float32)
        seg = np.asarray(seg[:m], np.float32)
        if len(seg) < m:
            seg = np.pad(seg, (0, m - len(seg)))
        seg = seg.copy()
        f = int(fade * sr)
        prev = prev_by_track.get(c.track)
        if f > 0 and len(seg) > 2 * f:
            ramp = np.linspace(0.0, 1.0, f, dtype=np.float32)
            if not _contiguous(prev, c):
                seg[:f] *= ramp
            nxt = next((d for d in clips[k + 1:] if d.track == c.track), None)
            if not (nxt is not None and _contiguous(c, nxt)):
                seg[-f:] *= ramp[::-1]
        prev_by_track[c.track] = c
        i = int(round((lo - t0) * sr))
        j = min(n, i + len(seg))
        out[i:j] += seg[: j - i]
        if progress:
            progress(100.0 * (k + 1) / len(clips))
    return out


# ---------------------------------------------------------------- loudness (BS.1770-4) and true peak

_KW = {48000: (([1.53512485958697, -2.69169618940638, 1.19839281085285], [1.0, -1.69065929318241, 0.73248077421585]),
               ([1.0, -2.0, 1.0], [1.0, -1.99004745483398, 0.99007225036621]))}


def _kweight_coefs(sr):
    if sr in _KW:
        return _KW[sr]
    # bilinear design (pyloudnorm formulas) for other rates
    f0, G, Q = 1681.974450955533, 3.999843853973347, 0.7071752369554196
    K = math.tan(math.pi * f0 / sr)
    Vh, Vb = 10 ** (G / 20), (10 ** (G / 20)) ** 0.4996667741545416
    a0 = 1 + K / Q + K * K
    b1 = [(Vh + Vb * K / Q + K * K) / a0, 2 * (K * K - Vh) / a0, (Vh - Vb * K / Q + K * K) / a0]
    a1 = [1.0, 2 * (K * K - 1) / a0, (1 - K / Q + K * K) / a0]
    f0, Q = 38.13547087602444, 0.5003270373238773
    K = math.tan(math.pi * f0 / sr)
    a2 = [1.0, 2 * (K * K - 1) / (1 + K / Q + K * K), (1 - K / Q + K * K) / (1 + K / Q + K * K)]
    return (b1, a1), ([1.0, -2.0, 1.0], a2)


def block_power(x, sr=SR):
    """Mean-square of the K-weighted signal per 100 ms step (400 ms blocks are built from 4 steps)."""
    from scipy.signal import lfilter
    (b1, a1), (b2, a2) = _kweight_coefs(sr)
    step = int(sr * 0.1)
    n = len(x) // step
    if n < 4:
        return np.zeros(0)
    out = np.empty(n)
    zi1 = np.zeros(2)
    zi2 = np.zeros(2)
    chunk = step * 600                                 # 60 s per filter call: bounded memory on long files
    k = 0
    for s in range(0, n * step, chunk):
        seg = np.asarray(x[s: min(n * step, s + chunk)], np.float64)
        y, zi1 = lfilter(b1, a1, seg, zi=zi1)
        y, zi2 = lfilter(b2, a2, y, zi=zi2)
        m = len(y) // step
        out[k:k + m] = (y[: m * step] ** 2).reshape(m, step).mean(1)
        k += m
    return out[:k]


def loudness(x, sr=SR, stereo=True):
    """Integrated loudness (LUFS, gated) and loudness range (LU) of mono audio. stereo=True reports dual-mono
    stereo playback (+3.01). -> (lufs or None when everything is below the gate, lra)."""
    p = block_power(x, sr)
    if len(p) < 4:
        return None, 0.0
    blocks = np.convolve(p, np.ones(4) / 4, mode="valid")          # 400 ms, 75 % overlap
    lk = -0.691 + 10 * np.log10(blocks + 1e-20) + (STEREO_DB if stereo else 0.0)
    g = blocks[lk > -70]
    if not len(g):
        return None, 0.0
    rel = -0.691 + 10 * np.log10(g.mean() + 1e-20) + (STEREO_DB if stereo else 0.0) - 10
    g2 = blocks[(lk > -70) & (lk > rel)]
    I = -0.691 + 10 * np.log10(g2.mean() + 1e-20) + (STEREO_DB if stereo else 0.0)
    # LRA: 3 s blocks (30 steps), 2 s overlap... approximated with 3 s windows every 1 s
    lra = 0.0
    if len(p) >= 30:
        b3 = np.convolve(p, np.ones(30) / 30, mode="valid")[::10]
        l3 = -0.691 + 10 * np.log10(b3 + 1e-20) + (STEREO_DB if stereo else 0.0)
        l3 = l3[l3 > -70]
        if len(l3):
            l3 = l3[l3 > -0.691 + 10 * np.log10(np.mean(10 ** ((l3 + 0.691) / 10))) - 20]
            if len(l3) > 1:
                lra = float(np.percentile(l3, 95) - np.percentile(l3, 10))
    return float(I), lra


def true_peak(x, sr=SR):
    """True peak in dBTP (4x oversampled, chunked). -inf for silence."""
    from scipy.signal import resample_poly
    peak = 0.0
    step = sr * 20
    for s in range(0, len(x), step):
        seg = np.asarray(x[max(0, s - 64): s + step + 64], np.float64)
        if not len(seg):
            continue
        peak = max(peak, float(np.abs(resample_poly(seg, 4, 1)).max()), float(np.abs(seg).max()))
    return 20 * math.log10(peak) if peak > 0 else float("-inf")


def levels(x, words=None, t0=0.0, sr=SR):
    """Speech level and noise floor (dBFS, 10 ms RMS) for the result card. words = [[a, b]] sequence spans
    (word frames = speech, frames >= 0.15 s from any word = pauses); without words: 90th / 15th percentile."""
    h = int(sr * 0.01)
    n = len(x) // h
    if n == 0:
        return None, None
    db = 10 * np.log10((np.asarray(x[: n * h], np.float64).reshape(n, h) ** 2).mean(1) + 1e-12)
    if words:
        wm = np.zeros(n, bool)
        for a, b in words:
            i, j = int((a - t0) * 100), int((b - t0) * 100) + 1
            if j > 0 and i < n:
                wm[max(0, i):min(n, j)] = True
        from scipy.ndimage import binary_dilation
        gap = ~binary_dilation(wm, iterations=15) if wm.any() else ~wm
        live = db > -85
        sp = db[wm & live]
        gp = db[gap & live]
        if len(sp) > 20 and len(gp) > 20:
            return float(np.percentile(sp, 75)), float(np.percentile(gp, 50))
    live = db[db > -85]
    if len(live) < 20:
        return None, None
    return float(np.percentile(live, 90)), float(np.percentile(live, 15))


# ---------------------------------------------------------------- ffmpeg runs

def ff_escape(path):
    """Path for a filter option value (forward slashes, escaped colon, quoted)."""
    s = str(path).replace("\\", "/").replace(":", "\\:").replace("'", "\\'")
    return f"'{s}'"


def run_ff(args, dur=None, progress=None, cancel=None, timeout=None, loglevel="error"):
    """Run ffmpeg with `-progress pipe:1`, calling progress(pct) from out_time. Returns stderr text.
    Raises EngineError('FFMPEG') on failure. cancel() is polled (raise inside it to stop)."""
    cmd = [ffmpeg_exe(), "-hide_banner", "-nostdin", "-loglevel", loglevel, "-y"]
    if progress and dur:
        cmd += ["-progress", "pipe:1", "-nostats"]
    cmd += [str(a) for a in args]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL,
                         creationflags=_NO_WINDOW)
    err = []
    th = threading.Thread(target=lambda: err.append(p.stderr.read()), daemon=True)
    th.start()
    t_start = time.time()
    try:
        for line in iter(p.stdout.readline, b""):
            if cancel:
                cancel()
            if progress and dur and line.startswith(b"out_time_us="):
                try:
                    us = int(line.split(b"=", 1)[1].strip() or b"0")
                    progress(min(99.0, 100.0 * us / 1e6 / dur))
                except ValueError:
                    pass
            if timeout and time.time() - t_start > timeout:
                raise EngineError("FFMPEG", tr("voice.err.ffTimeout"), "")
        p.wait()
    except BaseException:
        p.kill()
        p.wait()
        raise
    th.join(5)
    text = (err[0] if err else b"").decode("utf-8", "replace")
    if p.returncode != 0:
        lines = [ln for ln in text.strip().splitlines() if ln.strip()]
        raise EngineError("FFMPEG", tr("voice.err.ffFail", msg=lines[-1][:300] if lines else f"exit {p.returncode}"),
                          "\n".join(lines[-5:])[:800])
    return text


def ff_array(x, af, sr=SR):
    """Filter a mono float32 array through an FFmpeg -af chain (small arrays: previews, tests)."""
    cmd = [ffmpeg_exe(), "-hide_banner", "-nostdin", "-loglevel", "error", "-f", "f32le", "-ar", str(sr), "-ac", "1",
           "-i", "-", "-af", af, "-f", "f32le", "-ac", "1", "-ar", str(sr), "-"]
    p = subprocess.run(cmd, input=np.asarray(x, np.float32).tobytes(), capture_output=True, creationflags=_NO_WINDOW,
                       cwd=str(MODEL_DIR))
    if p.returncode != 0:
        tail = p.stderr.decode("utf-8", "replace").strip().splitlines()
        raise EngineError("FFMPEG", tr("voice.err.ffFail", msg=tail[-1][:300] if tail else "?"), "\n".join(tail[-5:])[:800])
    return np.frombuffer(p.stdout, np.float32).copy()


def ff_array_file(path, af, sr=SR):
    """Filter a raw mono f32le file through an FFmpeg chain into a mono float32 array (short files: previews)."""
    cmd = [ffmpeg_exe(), "-hide_banner", "-nostdin", "-loglevel", "error", "-f", "f32le", "-ar", str(sr), "-ac", "1",
           "-i", str(path), "-af", af, "-f", "f32le", "-ac", "1", "-ar", str(sr), "-"]
    p = subprocess.run(cmd, capture_output=True, stdin=subprocess.DEVNULL, creationflags=_NO_WINDOW)
    if p.returncode != 0:
        tail = p.stderr.decode("utf-8", "replace").strip().splitlines()
        raise EngineError("FFMPEG", tr("voice.err.ffFail", msg=tail[-1][:300] if tail else "?"), "\n".join(tail[-5:])[:800])
    return np.frombuffer(p.stdout, np.float32).copy()


def lag(x, y, sr=SR, max_lag=4800):
    """Samples by which y is delayed relative to x (cross-correlation on the loudest 4 s)."""
    n = min(len(x), len(y))
    if n < sr:
        return 0
    w = min(n - 2 * max_lag, sr * 4)
    if w <= sr // 4:
        return 0
    hop = sr // 2
    best, at = -1.0, max_lag
    for s in range(max_lag, n - w - max_lag, hop):
        e = float(np.dot(x[s:s + w:16], x[s:s + w:16]))
        if e > best:
            best, at = e, s
    a = np.asarray(x[at:at + w], np.float64)
    b = np.asarray(y[at - max_lag: at + w + max_lag], np.float64)
    L = 1 << int(math.ceil(math.log2(len(a) + len(b))))
    c = np.fft.irfft(np.fft.rfft(b, L) * np.conj(np.fft.rfft(a, L)), L)[: len(b) - len(a) + 1]
    return int(np.argmax(c)) - max_lag


def shift(y, d, n):
    """Undo a delay of d samples and fit to length n."""
    if d > 0:
        y = y[d:]
    elif d < 0:
        y = np.concatenate([np.zeros(-d, np.float32), y])
    if len(y) < n:
        y = np.pad(y, (0, n - len(y)))
    return np.asarray(y[:n], np.float32)


# ---------------------------------------------------------------- denoise backends

DENOISE = ("ai", "rnnoise", "fft", "off")


def rnnoise_ok():
    return (MODEL_DIR / RNN_MODELS["sh"]).is_file()


def denoise(x, method="ai", strength=1.0, progress=None, warn=None):
    """Denoise mono 48 kHz audio. method: ai (DeepFilterNet3, falls back to rnnoise then fft), rnnoise, fft, off.
    strength 0..1 = how much noise is removed (ai: attenuation limit 6..100 dB; fft: 6..30 dB; rnnoise: wet mix).
    -> (array, method actually used)."""
    from . import _voice_df as DF
    if method == "off" or strength <= 0:
        return x, "off"
    strength = max(0.05, min(1.0, float(strength)))
    if method == "ai":
        if DF.available():
            try:
                lim = 100.0 if strength >= 0.99 else 6.0 + 44.0 * strength
                return DF.enhance(x, atten_db=lim, progress=progress), "ai"
            except Exception as e:  # noqa: BLE001 - onnxruntime errors vary; fall back and say so
                if warn:
                    warn(tr("voice.warnAiDenoise", err=type(e).__name__))
        elif warn:
            warn(tr("voice.warnNoModel"))
        method = "rnnoise"
    if method == "rnnoise":
        if rnnoise_ok():
            try:
                y = _ff_long(x, f"arnndn=m={RNN_MODELS['sh']}:mix={strength:.2f}", progress)
                return shift(y, lag(x, y), len(x)), "rnnoise"
            except EngineError as e:
                if warn:
                    warn(tr("voice.warnRnnoise", msg=e.msg))
        method = "fft"
    nr = 6 + 24 * strength
    y = _ff_long(x, f"afftdn=nr={nr:.1f}:nf=-50:tn=1", progress)
    return shift(y, lag(x, y), len(x)), "fft"


def _ff_long(x, af, progress=None, chunk_s=300):
    """ff_array over long audio in 5 min chunks with 2 s overlap (keeps pipes small)."""
    n = len(x)
    if n <= SR * chunk_s * 1.2:
        y = ff_array(x, af)
        if progress:
            progress(100)
        return y
    out = np.zeros(n + SR, np.float32)
    ov = SR * 2
    step = SR * chunk_s
    starts = list(range(0, n, step))
    for k, s in enumerate(starts):
        a = max(0, s - ov)
        seg = x[a: min(n, s + step + ov)]
        y = ff_array(seg, af)
        d = lag(seg, y) if len(seg) > SR * 6 else 0
        y = shift(y, d, len(seg))
        out[s: s + min(step, n - s)] = y[s - a: s - a + min(step, n - s)]
        if progress:
            progress(100.0 * (k + 1) / len(starts))
    return out[:n]


# ---------------------------------------------------------------- chain

PRESETS = {
    # hp Hz, denoise strength, de-esser 0..1, compressor ratio, presence dB @ 3.5 kHz, warmth dB @ 180 Hz,
    # mud dB @ 350 Hz, air dB shelf @ 9 kHz
    "natural": {"hp": 80, "nr": 0.6, "deess": 0.25, "comp": 2.0, "presence": 2.0, "warmth": 0.0, "mud": -1.0, "air": 0.0},
    "podcast": {"hp": 90, "nr": 0.8, "deess": 0.4, "comp": 3.0, "presence": 3.0, "warmth": 1.5, "mud": -2.0, "air": 1.5},
    "kuat": {"hp": 100, "nr": 1.0, "deess": 0.55, "comp": 4.5, "presence": 4.0, "warmth": 1.0, "mud": -2.5, "air": 2.5},
}
LIMITS = {"hp": (0, 200), "nr": (0.0, 1.0), "deess": (0.0, 1.0), "comp": (1.0, 8.0), "presence": (-6.0, 8.0),
          "warmth": (-6.0, 6.0), "mud": (-8.0, 4.0), "air": (-6.0, 6.0)}
PLATFORMS = {"youtube": -14.0, "podcast": -16.0, "tiktok": -14.0}
PRE_LEVEL = -20.0          # mono LUFS the chain works at (compressor thresholds are set for this level)
EQ_FREQS = [125, 250, 500, 1000, 2000, 4000, 8000, 12000]


def chain_filters(cfg, pre_gain=0.0):
    """FFmpeg filter list (mono, before limiting/loudness) for the effective settings dict."""
    f = []
    if cfg.get("hp", 0) >= 20:
        f.append(f"highpass=f={cfg['hp']:.0f}:p=2")
    if abs(pre_gain) > 0.01:
        f.append(f"volume={pre_gain:.2f}dB")
    if cfg.get("mud"):
        f.append(f"equalizer=f=350:t=q:w=1.2:g={cfg['mud']:.1f}")
    if cfg.get("warmth"):
        f.append(f"equalizer=f=180:t=q:w=0.9:g={cfg['warmth']:.1f}")
    if cfg.get("comp", 1.0) > 1.05:
        r = cfg["comp"]
        thr = 10 ** ((-22.0 - 2.0 * (r - 2.0)) / 20)       # -22 dBFS at 2:1, -27 at 4.5:1
        f.append(f"acompressor=threshold={thr:.4f}:ratio={r:.2f}:attack=12:release=160:knee=3:makeup=1:detection=rms")
    if cfg.get("deess", 0) > 0.01:
        f.append(f"deesser=i={cfg['deess']:.2f}:m=0.5:f=0.5:s=o")
    if cfg.get("presence"):
        f.append(f"equalizer=f=3500:t=q:w=1.0:g={cfg['presence']:.1f}")
    if cfg.get("air"):
        f.append(f"treble=g={cfg['air']:.1f}:f=9000:t=s:w=0.7")
    for fr, g in (cfg.get("eq") or []):
        if abs(g) >= 0.2:
            f.append(f"equalizer=f={float(fr):.0f}:t=o:w=1:g={max(-6.0, min(6.0, float(g))):.1f}")
    return f


def parse_loudnorm(text):
    """Last JSON object printed by loudnorm print_format=json."""
    m = list(re.finditer(r"\{[^{}]*\"input_i\"[^{}]*\}", text, re.S))
    if not m:
        raise EngineError("FFMPEG", tr("voice.err.loudnorm"), text[-400:])
    return json.loads(m[-1].group(0))


# ---------------------------------------------------------------- "Tiru EQ dari contoh"

def avg_spectrum(x, sr=SR, mask=None, nfft=4096):
    """Mean power spectrum (dB) over speech frames. mask: bool per 10 ms frame (True = use) or None = loud frames."""
    from scipy.signal import stft
    hop = nfft // 2
    f, _, Z = stft(np.asarray(x, np.float32), fs=sr, nperseg=nfft, noverlap=nfft - hop, boundary=None, padded=False)
    P = (np.abs(Z) ** 2).astype(np.float64)
    e = 10 * np.log10(P.sum(0) + 1e-20)
    if mask is not None and len(mask):
        idx = np.clip(((np.arange(P.shape[1]) * hop + nfft // 2) / (sr * 0.01)).astype(int), 0, len(mask) - 1)
        keep = np.asarray(mask)[idx]
    else:
        keep = e > np.percentile(e, 40)
    keep &= e > (e.max() - 45)
    if keep.sum() < 10:
        keep = e > np.percentile(e, 50)
    return f, 10 * np.log10(P[:, keep].mean(1) + 1e-20)


def band_db(f, spec_db, centers=EQ_FREQS):
    """Average dB in 1-octave bands around each centre (power average)."""
    out = []
    for c in centers:
        lo, hi = c / math.sqrt(2), c * math.sqrt(2)
        sel = (f >= lo) & (f < hi)
        out.append(10 * math.log10(np.mean(10 ** (spec_db[sel] / 10)) + 1e-20) if sel.any() else -120.0)
    return np.asarray(out)


def match_eq(ref, mine, sr=SR, mask_ref=None, mask_mine=None, limit=6.0):
    """EQ curve that moves `mine` towards the tonal balance of `ref`: per-octave difference, level-neutral
    (mean removed over 250 Hz..4 kHz), smoothed with neighbours, clamped to +-limit dB. -> [[freq, gain]]."""
    f1, s1 = avg_spectrum(ref, sr, mask_ref)
    f2, s2 = avg_spectrum(mine, sr, mask_mine)
    d = band_db(f1, s1) - band_db(f2, s2)
    core = [i for i, c in enumerate(EQ_FREQS) if 250 <= c <= 4000]
    d = d - float(np.mean(d[core]))
    sm = d.copy()
    for i in range(len(d)):
        nb = [d[j] for j in (i - 1, i, i + 1) if 0 <= j < len(d)]
        sm[i] = 0.5 * d[i] + 0.5 * float(np.mean(nb))
    sm = np.clip(sm * 0.8, -limit, limit)              # 80 %: match the character, not every bump
    return [[c, round(float(g), 1)] for c, g in zip(EQ_FREQS, sm)]
