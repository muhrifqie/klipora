"""DeepFilterNet3 speech enhancement on CPU with onnxruntime only (no torch, no libDF wheel for Python 3.14).

Model files: engine/ac/assets/models/deepfilternet3/{enc,erb_dec,df_dec}.onnx + config.ini, the official export
from github.com/Rikorose/DeepFilterNet (models/DeepFilterNet3_onnx.tar.gz, MIT/Apache-2.0, Hendrik Schroeter).

The DSP around the three networks is a numpy port of libDF (DFState) and df/enhance.py:
  * 48 kHz, FFT 960, hop 480, Vorbis window, analysis scaled by wnorm = 2*hop/fft^2 (synthesis unscaled), so
    analysis -> synthesis is an exact reconstruction with a delay of one hop.
  * ERB features: mean power per ERB band (erb_fb widths, 32 bands, min 2 bins), 10*log10(x+1e-10), exponential
    mean normalisation (alpha 0.99, init linspace(-60, -90)), divided by 40.
  * Complex features: first 96 bins divided by sqrt(EMA of |X|) (init linspace(0.001, 0.0001)).
  * Features are shifted 2 frames earlier (conv lookahead), the ERB mask (one gain per band) is applied to all bins,
    and the deep filter (order 5, lookahead 2: frames t-2..t+2) replaces the lowest 96 bins, computed on the
    UNMASKED noisy spectrum (DeepFilterNet3 forward).
Long audio is processed in chunks with a context lead-in so the GRUs settle; feature EMAs run continuously.
`atten_db` limits the noise reduction (noisy signal mixed back in) like deep-filter --atten-lim.
"""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np

MODEL_DIR = Path(__file__).resolve().parents[1] / "assets" / "models" / "deepfilternet3"
SR = 48000
FFT = 960
HOP = 480
NB_ERB = 32
NB_DF = 96
MIN_ERB_FREQS = 2
ORDER = 5
DF_LA = 2
CONV_LA = 2
XFADE = 25            # frames (0.25 s) of crossfade between chunks
ALPHA = 0.99          # libDF calc_norm_alpha(48000, 480, tau=1) rounded to 3 decimals
NFREQ = FFT // 2 + 1

_SESS = {}


def available():
    """True when the three ONNX files and onnxruntime are present."""
    try:
        import onnxruntime  # noqa: F401  (availability probe)
    except ImportError:
        return False
    return all((MODEL_DIR / f"{n}.onnx").is_file() for n in ("enc", "erb_dec", "df_dec"))


def _sessions(threads=None):
    key = threads or 0
    if key not in _SESS:
        import onnxruntime as ort
        so = ort.SessionOptions()
        so.log_severity_level = 3
        if threads:
            so.intra_op_num_threads = int(threads)
        _SESS[key] = tuple(ort.InferenceSession(str(MODEL_DIR / f"{n}.onnx"), so, providers=["CPUExecutionProvider"])
                           for n in ("enc", "erb_dec", "df_dec"))
    return _SESS[key]


def erb_widths(sr=SR, fft=FFT, nb=NB_ERB, min_nb=MIN_ERB_FREQS):
    """libDF erb_fb(): number of FFT bins per ERB band (sums to fft/2+1)."""
    def f2e(f):
        return 9.265 * math.log1p(f / (24.7 * 9.265))

    def e2f(e):
        return 24.7 * 9.265 * (math.exp(e / 9.265) - 1.0)

    fw = sr / fft
    lo, hi = f2e(0.0), f2e(sr / 2)
    step = (hi - lo) / nb
    out, prev, over = [0] * nb, 0, 0
    for i in range(1, nb + 1):
        fb = int(round(e2f(lo + i * step) / fw))
        n = fb - prev - over
        if n < min_nb:
            over = min_nb - n
            n = min_nb
        else:
            over = 0
        out[i - 1] = n
        prev = fb
    out[-1] += 1
    extra = sum(out) - (fft // 2 + 1)
    if extra > 0:
        out[-1] -= extra
    return out


def vorbis_window(n=FFT):
    h = n // 2
    i = np.arange(n, dtype=np.float64)
    s = np.sin(0.5 * np.pi * (i + 0.5) / h)
    return np.sin(0.5 * np.pi * s * s).astype(np.float32)


WIN = vorbis_window()
WNORM = 2.0 * HOP / (FFT * FFT)
WIDTHS = erb_widths()
BAND_OF_BIN = np.repeat(np.arange(NB_ERB), WIDTHS)
ERB_FB = np.zeros((NFREQ, NB_ERB), np.float32)          # mean-power matrix (1/width per member bin)
ERB_FB[np.arange(NFREQ), BAND_OF_BIN] = 1.0 / np.asarray(WIDTHS, np.float32)[BAND_OF_BIN]


def stft(x, first):
    """Frames first..first+n-1 of signal x where x[0] is sample (first-1)*HOP. Frame t = window *
    samples [(t-1)*HOP, (t+1)*HOP). -> complex64 [n, 481]."""
    n = (len(x) - FFT) // HOP + 1
    idx = np.arange(FFT)[None, :] + HOP * np.arange(n)[:, None]
    fr = x[idx] * WIN[None, :]
    return (np.fft.rfft(fr, axis=1) * WNORM).astype(np.complex64)


def istft_ola(spec):
    """Overlap-add of frames (unscaled inverse, windowed). Returns samples for frames 1..n-1 (each frame t yields
    HOP samples = first half of t + second half of t-1), length (n-1)*HOP."""
    fr = np.fft.irfft(spec, n=FFT, axis=1).astype(np.float32) * FFT * WIN[None, :]
    out = fr[1:, :HOP] + fr[:-1, HOP:]
    return out.reshape(-1)


def _ema(x, init):
    """y[t] = (1-a)*x[t] + a*y[t-1] along axis 0 with y[-1] = init. Returns (y, last)."""
    from scipy.signal import lfilter
    zi = (ALPHA * np.asarray(init, np.float64))[None, :]
    y, _ = lfilter([1.0 - ALPHA], [1.0, -ALPHA], np.asarray(x, np.float64), axis=0, zi=zi)
    return y.astype(np.float32), y[-1]


class _Feat:
    """Continuous feature normalisation state across chunks."""

    def __init__(self):
        self.erb_state = np.linspace(-60.0, -90.0, NB_ERB)
        self.unit_state = np.linspace(0.001, 0.0001, NB_DF)

    def compute(self, spec, keep):
        """Features for spec frames; the EMA state advances only over the first `keep` frames (the rest are
        re-processed by the next chunk)."""
        p = spec.real ** 2 + spec.imag ** 2
        db = 10.0 * np.log10(p @ ERB_FB + 1e-10)
        m, _ = _ema(db, self.erb_state)
        erb = ((db - m) / 40.0).astype(np.float32)
        mag = np.abs(spec[:, :NB_DF])
        u, _ = _ema(mag, self.unit_state)
        cs = (spec[:, :NB_DF] / np.sqrt(u)).astype(np.complex64)
        if keep > 0:
            self.erb_state = m[keep - 1].astype(np.float64)
            self.unit_state = u[keep - 1].astype(np.float64)
        return erb, cs


def _shift(a, k):
    """Drop the first k frames along axis 2 and append k zero frames (ConstantPad2d((0,0,-k,k)))."""
    if k <= 0:
        return a
    z = np.zeros_like(a[:, :, :k])
    return np.concatenate([a[:, :, k:], z], axis=2)


def _enhance_frames(spec, erb, cs, sess):
    """Run the three networks over a frame block. -> (enhanced spec [n, 481], lsnr [n])."""
    enc, erb_dec, df_dec = sess
    n = len(spec)
    fe = _shift(erb[None, None], CONV_LA)
    fs = _shift(np.stack([cs.real, cs.imag], 0)[None].astype(np.float32), CONV_LA)
    e0, e1, e2, e3, emb, c0, lsnr = enc.run(None, {"feat_erb": fe, "feat_spec": fs})
    m = erb_dec.run(None, {"emb": emb, "e3": e3, "e2": e2, "e1": e1, "e0": e0})[0][0, 0]    # [n, 32]
    coefs = df_dec.run(None, {"emb": emb, "c0": c0})[0][0]                                   # [n, 96, 10]
    coefs = coefs.reshape(n, NB_DF, ORDER, 2)
    c = (coefs[..., 0] + 1j * coefs[..., 1]).astype(np.complex64)                           # [n, 96, 5]
    out = spec * m[:, BAND_OF_BIN]
    low = spec[:, :NB_DF]
    pad = np.concatenate([np.zeros((ORDER - 1 - DF_LA, NB_DF), np.complex64), low,
                          np.zeros((DF_LA, NB_DF), np.complex64)], 0)
    y = np.zeros((n, NB_DF), np.complex64)
    for k in range(ORDER):
        y += pad[k:k + n] * c[:, :, k]
    out[:, :NB_DF] = y
    return out, lsnr[0, :, 0]


def enhance(x, atten_db=None, chunk_s=30.0, ctx_s=3.0, progress=None, threads=None):
    """Denoise mono float32 audio at 48 kHz. Returns float32 array of the same length.
    atten_db: maximum noise attenuation in dB (None/0/>=100 = unlimited). progress(pct) is called per chunk."""
    sess = _sessions(threads)
    x = np.asarray(x, np.float32)
    n_in = len(x)
    if n_in == 0:
        return x.copy()
    total_frames = int(math.ceil(n_in / HOP)) + 2                 # +2: flush the one-hop delay and lookahead
    xp = np.concatenate([np.zeros(HOP, np.float32), x, np.zeros((total_frames + 4) * HOP + FFT - n_in, np.float32)])
    # frame t uses xp[t*HOP : t*HOP+FFT] (xp[0] = sample -HOP)
    cf = max(50, int(round(chunk_s * SR / HOP)))
    ctx = int(round(ctx_s * SR / HOP))
    la = CONV_LA + DF_LA + 2
    feat = _Feat()
    out = np.zeros(total_frames * HOP, np.float32)
    lim = None
    if atten_db and 0 < atten_db < 100:
        lim = float(10 ** (-atten_db / 20.0))
    t0 = 0
    nchunks = int(math.ceil(total_frames / cf))
    ovf = min(XFADE, cf // 2)
    tail = None
    # Feature state must advance frame-continuously: chunk k computes features for [t0-ctx, ext+la) starting from
    # the state at frame t0-ctx, which the previous chunk stored (keep = frames up to its next start - ctx).
    # Each chunk also renders `ovf` frames past its end; the next chunk crossfades over them (GRU state differs
    # slightly between chunks, the crossfade hides any step in the residual noise floor).
    for k in range(nchunks):
        t1 = min(total_frames, t0 + cf)
        ext = min(total_frames, t1 + ovf)
        a, b = max(0, t0 - ctx), ext + la
        seg = xp[a * HOP: (b - 1) * HOP + FFT]
        if len(seg) < FFT + (b - a - 1) * HOP:
            seg = np.concatenate([seg, np.zeros(FFT + (b - a - 1) * HOP - len(seg), np.float32)])
        spec = stft(seg, a)
        nxt = max(0, t1 - ctx)
        erb, cs = feat.compute(spec, nxt - a)
        enh, _ = _enhance_frames(spec, erb, cs, sess)
        if lim is not None:
            enh = enh * (1.0 - lim) + spec * lim
        # synthesis needs frame t0-1 for the first output frame
        s_lo = max(a, t0 - 1)
        y = istft_ola(enh[s_lo - a: ext - a])         # samples of frames s_lo+1 .. ext-1
        first = s_lo + 1
        if tail is not None and len(tail):
            i0 = (t0 - first) * HOP
            m = min(len(tail), len(y) - i0)
            w = np.linspace(0.0, 1.0, m, dtype=np.float32)
            y[i0:i0 + m] = tail[:m] * (1.0 - w) + y[i0:i0 + m] * w
        out[first * HOP: first * HOP + len(y)] = y
        tail = y[(t1 - first) * HOP:] if ext > t1 else None
        if progress:
            progress(100.0 * (k + 1) / nchunks)
        t0 = t1
    # out[i] reconstructs xp[i] (output frame t = samples [t*HOP, (t+1)*HOP) of xp), and xp[HOP] = x[0]
    return out[HOP: HOP + n_in] if len(out) >= HOP + n_in else np.pad(out[HOP:], (0, HOP + n_in - len(out)))
