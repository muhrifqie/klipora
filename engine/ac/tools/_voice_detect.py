"""Detectors for Suara Jernih: mouse/keyboard clicks and breaths in pauses, music vs speech per clip, and voice
activity for ducking. Everything works on mono 48 kHz audio in SEQUENCE time (index 0 = `t0`) plus transcript word
spans; words are never touched (every candidate overlapping a word +-guard is dropped).

Clicks: 2 ms high-passed (> 2 kHz) energy against a 300 ms running median of the same signal. A click is a run of
frames >= CLICK_RISE dB above that background, at most 60 ms long, with a fast onset (peak within 6 ms) and most
of its energy above 2 kHz. Clicks closer than 0.35 s are grouped (typing) into one review item.
Breaths: inside pauses between words, 10 ms frames above the pause noise floor + 6 dB for 0.12..1.2 s, quieter than
speech (peak <= speech level - 6 dB), unvoiced (normalised autocorrelation peak at 80..400 Hz < 0.5) and noise-like.
Measured on the test tutorials: see docs/tools/voice.md.
"""
from __future__ import annotations

import math

import numpy as np

SR = 48000
CLICK_HOP = 0.002
CLICK_RISE = 14.0          # dB over the running median of the HF energy
CLICK_MAX = 0.06           # s
CLICK_GROUP = 0.35         # s
CLICK_DECAY = 10.0         # dB the full-band level must fall within 6..60 ms after the click
CLICK_PAD = (0.006, 0.02)  # s before / after (decay)
WORD_GUARD = 0.04          # s around words that is never touched
BREATH_MIN, BREATH_MAX = 0.12, 1.2
NOISE_UP = 6.0             # dB over the pause floor
VOICED_MAX = 0.5
BREATH_BELOW = 8.0         # dB a breath peak stays under the speech level
BREATH_CENTROID = 1200.0   # Hz: breaths are broadband hiss, word tails / hum are low


def _frames_db(x, hop_n):
    n = len(x) // hop_n
    if n == 0:
        return np.zeros(0, np.float32)
    e = (np.asarray(x[: n * hop_n], np.float32).reshape(n, hop_n) ** 2).mean(1)
    return (10 * np.log10(e + 1e-12)).astype(np.float32)


def _runs(mask):
    d = np.diff(np.concatenate([[0], np.asarray(mask, np.int8), [0]]))
    return list(zip(np.flatnonzero(d == 1).tolist(), np.flatnonzero(d == -1).tolist()))


def word_mask(words, n, hop, t0=0.0, guard=WORD_GUARD):
    """Bool per frame: inside a word span (+-guard)."""
    m = np.zeros(n, bool)
    for a, b in words:
        i, j = int(math.floor((a - guard - t0) / hop)), int(math.ceil((b + guard - t0) / hop))
        if j > 0 and i < n:
            m[max(0, i):min(n, j)] = True
    return m


def _hp(x, fc=2000.0, sr=SR):
    from scipy.signal import butter, sosfilt
    sos = butter(4, fc, "highpass", fs=sr, output="sos")
    return sosfilt(sos, np.asarray(x, np.float32)).astype(np.float32)


def clicks(x, words, t0=0.0, sr=SR, rise=CLICK_RISE, words_known=True):
    """-> list of {"t0", "t1", "peak", "rise", "hf", "n"} (sequence seconds), grouped."""
    from scipy.ndimage import median_filter
    hop_n = int(sr * CLICK_HOP)
    hp = _hp(x, sr=sr)
    eh = _frames_db(hp, hop_n)
    ef = _frames_db(x, hop_n)
    n = len(eh)
    if n < 10:
        return []
    bg = median_filter(eh, size=151, mode="nearest")
    cand = (eh - bg >= rise) & (eh > -75)
    if words_known:
        cand &= ~word_mask(words, n, CLICK_HOP, t0)
    events = []
    maxf = int(CLICK_MAX / CLICK_HOP)
    for a, b in _runs(cand):
        if b - a > maxf:
            continue
        pk = a + int(np.argmax(eh[a:b]))
        if pk - a > 3:                                   # onset slower than 6 ms: not a click
            continue
        # HF share: the click energy is mostly above 2 kHz (speech / breath are not)
        hf = float(eh[pk] - ef[pk])                      # dB of HF relative to full band (0 = all HF)
        if hf < -10:
            continue
        # a click decays at once; sound that keeps going after it is the onset of a word / vocal sound
        fpk = float(ef[a:b].max())
        post = ef[min(n, b + 3): min(n, b + 30)]
        if len(post) and float(np.median(post)) > fpk - CLICK_DECAY:
            continue
        events.append([a * CLICK_HOP + t0, b * CLICK_HOP + t0, float(eh[pk]), float(eh[pk] - bg[pk]), hf])
    out = []
    for e in events:
        if out and e[0] - out[-1]["t1"] < CLICK_GROUP:
            g = out[-1]
            g["t1"] = e[1]
            g["n"] += 1
            g["peak"] = max(g["peak"], e[2])
            g["rise"] = max(g["rise"], e[3])
            g["hf"] = max(g["hf"], e[4])
        else:
            out.append({"t0": e[0], "t1": e[1], "peak": e[2], "rise": e[3], "hf": e[4], "n": 1})
    # pad for the decay, but never into a word guard
    for g in out:
        g["t0"] = g["t0"] - CLICK_PAD[0]
        g["t1"] = g["t1"] + CLICK_PAD[1]
    if words_known and words:
        out = _clip_to_gaps(out, words)
    return [g for g in out if g["t1"] - g["t0"] > 0.004]


def _clip_to_gaps(items, words, guard=WORD_GUARD):
    """Shrink items so they stay outside every word +- guard (items that vanish are dropped)."""
    import bisect
    starts = [w[0] for w in words]
    out = []
    for it in items:
        a, b = it["t0"], it["t1"]
        k = bisect.bisect_left(starts, b + guard)
        for j in range(max(0, k - 3), min(len(words), k + 1)):
            w0, w1 = words[j][0] - guard, words[j][1] + guard
            if w1 <= a or w0 >= b:
                continue
            if w0 <= a:
                a = max(a, w1)
            else:
                b = min(b, w0)
        if b - a > 0.004:
            it = dict(it, t0=a, t1=b)
            out.append(it)
    return out


def _voicing(seg, sr=SR):
    """Max normalised autocorrelation in the 80..400 Hz lag range (1 = clearly voiced)."""
    if len(seg) < sr // 40:
        return 0.0
    from scipy.signal import decimate
    y = decimate(np.asarray(seg, np.float64), 6)                 # 8 kHz
    fs = sr / 6
    best = 0.0
    w = int(fs * 0.04)
    for s in range(0, max(1, len(y) - w), w // 2):
        f = y[s:s + w] - y[s:s + w].mean()
        e = float(np.dot(f, f))
        if e <= 1e-12:
            continue
        ac = np.correlate(f, f, "full")[w - 1:] / e
        lo, hi = int(fs / 400), min(len(ac) - 1, int(fs / 80))
        if hi > lo:
            best = max(best, float(ac[lo:hi].max()))
    return best


def _centroid(seg, sr=SR):
    """Spectral centroid (Hz) of a segment."""
    if len(seg) < 64:
        return 0.0
    P = np.abs(np.fft.rfft(np.asarray(seg, np.float64) * np.hanning(len(seg)))) ** 2
    f = np.fft.rfftfreq(len(seg), 1.0 / sr)
    return float((P * f).sum() / (P.sum() + 1e-20))


def breaths(x, words, t0=0.0, sr=SR, speech_db=None, floor_db=None, exclude=None):
    """Breath candidates inside pauses between words. -> [{"t0","t1","peak","over","voiced"}]."""
    if not words:
        return []
    hop = 0.01
    hop_n = int(sr * hop)
    db = _frames_db(x, hop_n)
    n = len(db)
    wm = word_mask(words, n, hop, t0, guard=0.03)
    if speech_db is None:
        sp = db[word_mask(words, n, hop, t0, guard=0.0)]
        speech_db = float(np.percentile(sp, 75)) if len(sp) > 20 else float(np.percentile(db, 90))
    if floor_db is None:
        gp = db[~word_mask(words, n, hop, t0, guard=0.15)]
        gp = gp[gp > -88]
        floor_db = float(np.percentile(gp, 30)) if len(gp) > 50 else float(np.percentile(db, 15))
    thr = max(floor_db + NOISE_UP, -72.0)
    cand = (db > thr) & ~wm
    if exclude:
        cand &= ~word_mask(exclude, n, hop, t0, guard=0.0)
    out = []
    for a, b in _runs(cand):
        # bridge tiny dips inside one breath
        if out and a - out[-1][1] <= 6 and not wm[out[-1][1]:a].any():
            out[-1][1] = b
        else:
            out.append([a, b])
    res = []
    for a, b in out:
        # a run that starts right where the previous word's guard ends is that word's decay tail, not a breath
        if a > 0 and wm[a - 1] and db[a] > thr + 3:
            continue
        dur = (b - a) * hop
        if dur < BREATH_MIN or dur > BREATH_MAX:
            continue
        pk = float(db[a:b].max())
        if pk > speech_db - BREATH_BELOW:
            continue
        seg = x[a * hop_n: b * hop_n]
        v = _voicing(seg, sr)
        if v >= VOICED_MAX:
            continue
        cen = _centroid(seg, sr)
        if cen < BREATH_CENTROID:
            continue
        res.append({"t0": a * hop + t0, "t1": b * hop + t0, "peak": pk, "over": pk - floor_db, "voiced": v,
                    "centroid": cen})
    return _clip_to_gaps(res, words, guard=0.03)


def floor_db(x, a, b, t0=0.0, sr=SR, guard=0.01, span=0.12):
    """Deepest useful attenuation (dB, <= 0) for the item [a, b): down to the level of the sound around it (the
    quieter side of 120 ms before / after), so a click on top of background sound (desktop audio, room tone) is
    evened out instead of leaving a hole. Silence around it -> very negative (the dB setting decides)."""
    i0, i1 = int(round((a - t0) * sr)), int(round((b - t0) * sr))
    n = len(x)
    if i1 <= i0 or i0 >= n or i1 <= 0:
        return 0.0

    def lv(z):
        z = np.asarray(z, np.float64)
        return 10 * math.log10(float(np.mean(z * z)) + 1e-12) if len(z) else None

    item = lv(x[max(0, i0):min(n, i1)])
    sides = [v for v in (lv(x[max(0, i0 - int(span * sr)): max(0, i0 - int(guard * sr))]),
                         lv(x[min(n, i1 + int(guard * sr)): min(n, i1 + int(span * sr))])) if v is not None]
    if item is None or not sides:
        return -120.0
    return float(min(0.0, min(sides) - item))


def gain_curve(n, items, t0=0.0, sr=SR, ramp=0.005):
    """Linear gain per sample for [{"t0","t1","db"}] items (sequence s), with `ramp` s fades at each edge
    (outside the item, so the attenuated span itself is fully reduced)."""
    g = np.ones(n, np.float32)
    r = max(1, int(ramp * sr))
    for it in items:
        a, b = int(round((it["t0"] - t0) * sr)), int(round((it["t1"] - t0) * sr))
        lvl = float(10 ** (it["db"] / 20.0))
        if b <= 0 or a >= n or b <= a:
            continue
        lo, hi = max(0, a), min(n, b)
        g[lo:hi] = np.minimum(g[lo:hi], lvl)
        if a - r < n and a > 0:
            s = max(0, a - r)
            ramp_in = np.linspace(1.0, lvl, a - s + 1, dtype=np.float32)[: a - s]
            g[s:a] = np.minimum(g[s:a], ramp_in)
        if b < n:
            e = min(n, b + r)
            ramp_out = np.linspace(lvl, 1.0, e - b + 1, dtype=np.float32)[1:]
            g[b:e] = np.minimum(g[b:e], ramp_out[: e - b])
    return g


# ---------------------------------------------------------------- music vs speech, voice activity

def music_score(db, hop=0.01):
    """0..1 'music-likeness' of a 10 ms dB envelope: sustained sound (few pauses) and low syllable-rate
    modulation. Speech: many short dips, active share 0.4..0.75; music beds: active share > 0.85."""
    live = db[db > -85]
    if len(live) < 100:
        return 0.0, {"active": 0.0, "mod": 0.0}
    top = float(np.percentile(live, 95))
    act = float(np.mean(db > top - 20))
    # syllable-rate modulation: share of 1 s windows containing a dip >= 15 dB below that window's max
    w = int(1 / hop)
    k = len(db) // w
    if k < 3:
        return act, {"active": act, "mod": 0.0}
    m = db[: k * w].reshape(k, w)
    dips = np.mean((m.max(1) - m.min(1)) >= 15)
    score = max(0.0, min(1.0, (act - 0.6) / 0.3)) * (1.0 - 0.6 * float(dips))
    return float(score), {"active": round(act, 3), "mod": round(float(dips), 3)}


def activity(words, db=None, t0=0.0, hop=0.01, thr=None, hold=0.6, min_len=0.25):
    """Voice activity spans (sequence s) for ducking: words merged over gaps < hold, plus envelope runs when no
    words exist. -> [[a, b]]."""
    spans = [[float(a), float(b)] for a, b in (words or [])]
    if not spans and db is not None and len(db):
        live = db[db > -85]
        if len(live) > 50:
            th = thr if thr is not None else float(np.percentile(live, 60)) - 6
            spans = [[t0 + a * hop, t0 + b * hop] for a, b in _runs(db > th)]
    spans.sort()
    out = []
    for a, b in spans:
        if out and a - out[-1][1] < hold:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [r for r in out if r[1] - r[0] >= min_len]
