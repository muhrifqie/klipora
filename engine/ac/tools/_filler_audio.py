"""Acoustic + lexical filler detector in SOURCE seconds (helper of tools/fillers.py; "_" = not a tool).

Port of the verified prototype proto/fillers/acoustics.py + fillers.py + review.py (Snapper/word_cut),
measured in docs/research/fillers_repeat.md sections 4-6. Changes vs the prototype: no Silero VAD (it was
only reported as evidence, never scored), stability computed in the same chunked pass as the other features,
features cached per media (`<stem>_fillers.npz`), candidates carry a tier, and overlap merging is a separate
step so disabled tiers never swallow enabled ones.

Whisper (large-v3-turbo) almost never writes "eee/emm", so three detectors are fused:
  lexicon   a hesitation token in the production transcript                           (Transkrip)
  listener  a hesitation token in the small-model "listener" pass                      (Pendengar)
  orphan    a voiced, steady sound island that no word claims ("eee" Whisper dropped)  (Akustik)
  prefix    a held sound at the start of an island, before the words ("eee saya")    (Akustik)
  mid       a held sound between words                                                 (Akustik)
  drawl     a held word-final vowel ("dulu~~"), never pre-checked                      (Akustik)
Tokens are snapped onto the real sound (mean boundary error 0.20 s -> 0.06 s) and every cut removes the
filler plus the SHORTER neighbouring pause, edges snapped to the quietest 10 ms frame. A cut end glued to
the next word that is still loud follows the filler's decay first (`Analysis.tail`, from the live Premiere check).
"""
from __future__ import annotations

import contextlib

import numpy as np

from ..util import cache_path, file_hash
from . import _filler_lexicon as LX

SR = 16000
HOP = 160                 # 10 ms frames
FPS = SR / HOP            # 100 frames per second
FEAT_V = 1
_MEL = {}


# --------------------------------------------------------------------------- features

def _frames(x, win, n):
    """Frame i covers x[i*HOP : i*HOP+win] (zero padded at the end) so all window sizes align."""
    pad = np.pad(x, (0, max(0, (n - 1) * HOP + win - len(x))))
    idx = np.arange(win)[None, :] + HOP * np.arange(n)[:, None]
    return pad[idx]


def _mel_fb(n_fft=512, n_mels=40, fmin=60, fmax=7600):
    if "fb" not in _MEL:
        hz2mel = lambda f: 2595 * np.log10(1 + f / 700)  # noqa: E731
        mel2hz = lambda m: 700 * (10 ** (m / 2595) - 1)  # noqa: E731
        pts = mel2hz(np.linspace(hz2mel(fmin), hz2mel(fmax), n_mels + 2))
        bins = np.floor((n_fft + 1) * pts / SR).astype(int)
        fb = np.zeros((n_mels, n_fft // 2 + 1), np.float32)
        for i in range(n_mels):
            a, b, c = bins[i], bins[i + 1], bins[i + 2]
            if b > a:
                fb[i, a:b] = (np.arange(a, b) - a) / (b - a)
            if c > b:
                fb[i, b:c] = (c - np.arange(b, c)) / (c - b)
        _MEL["fb"] = fb
    return _MEL["fb"]


def _chunk_features(x, n):
    """db (25 ms RMS dBFS), f0 (autocorrelation pitch, 0 = unvoiced), flat (spectral flatness 125-4000 Hz)
    and raw stability (log-mel change across +-50 ms: ~1 in running speech, < 0.5 on a held 'eee')."""
    fr = _frames(x, 400, n)
    db = (10 * np.log10(np.mean(fr ** 2, axis=1) + 1e-10)).astype(np.float32)
    del fr
    win = _frames(x, 512, n) * np.hanning(512).astype(np.float32)
    spec = np.abs(np.fft.rfft(win, axis=1)) ** 2 + 1e-12
    del win
    band = spec[:, 4:129]
    flat = (np.exp(np.mean(np.log(band), axis=1)) / np.mean(band, axis=1)).astype(np.float32)
    mel = np.log(spec @ _mel_fb().T + 1e-9)
    del spec
    mel -= mel.mean(axis=1, keepdims=True)
    k = np.ones(5) / 5
    mel = np.apply_along_axis(lambda c: np.convolve(c, k, "same"), 0, mel)
    stab = np.full(n, 9.0, np.float32)
    if n > 10:
        stab[5:-5] = np.sqrt(np.mean((mel[10:] - mel[:-10]) ** 2, axis=1))
    del mel
    w = _frames(x, 640, n)
    w = w - w.mean(axis=1, keepdims=True)
    ac = np.fft.irfft(np.abs(np.fft.rfft(w, 1280, axis=1)) ** 2, axis=1)[:, :641]
    del w
    ac = ac / (ac[:, :1] + 1e-9)
    lo, hi = SR // 400, SR // 70
    lag = lo + np.argmax(ac[:, lo:hi], axis=1)
    nccf = ac[np.arange(n), lag]
    voiced = (nccf > 0.45) & (db > -50)
    f0 = np.where(voiced, SR / lag, 0.0).astype(np.float32)
    return db, f0, flat, stab


def features(x, progress=None, chunk=12000, margin=10):
    """Frame features of mono 16 kHz float32 audio, chunked (2 min of frames per pass, ~0.25 GB peak) with a
    10-frame margin so the +-7 frame stability context is identical to a single pass.
    -> {"db", "f0", "flat", "stab"} float32 arrays of len(x) // HOP frames."""
    x = np.asarray(x, np.float32)
    n = max(1, len(x) // HOP)
    out = {k: np.empty(n, np.float32) for k in ("db", "f0", "flat", "stab")}
    for f0 in range(0, n, chunk):
        f1 = min(n, f0 + chunk)
        a, b = max(0, f0 - margin), min(n, f1 + margin)
        part = _chunk_features(x[a * HOP:min(len(x), b * HOP + 640)], b - a)
        for k, v in zip(("db", "f0", "flat", "stab"), part):
            out[k][f0:f1] = v[f0 - a:f0 - a + (f1 - f0)]
        if progress:
            progress(100.0 * f1 / n)
    return out


def feature_path(media):
    return cache_path(media, "_fillers.npz")


def cached_features(media):
    """Feature arrays from `<stem>_fillers.npz` when the media hash and version match, else None."""
    p = feature_path(media)
    if not p.is_file():
        return None
    try:
        with np.load(p, allow_pickle=False) as z:
            if int(z["v"]) != FEAT_V or str(z["hash"]) != file_hash(media):
                return None
            return {k: z[k] for k in ("db", "f0", "flat", "stab")}
    except (OSError, ValueError, KeyError):
        return None


def save_features(media, F):
    """Atomic write of the feature cache (34.6 min = 207 k frames, ~2 MB). Failure is not fatal."""
    p = feature_path(media)
    tmp = p.with_name(p.stem + ".tmp.npz")
    with contextlib.suppress(OSError):
        np.savez_compressed(tmp, v=np.int32(FEAT_V), hash=np.str_(file_hash(media)), **F)
        tmp.replace(p)
        return p
    with contextlib.suppress(OSError):
        tmp.unlink()
    return None


# --------------------------------------------------------------------------- analysis

def islands(mask, gap=0.10, min_len=0.06):
    """Runs of True frames, merging holes <= gap seconds -> [(s, e)] in seconds."""
    idx = np.flatnonzero(np.diff(np.r_[0, np.asarray(mask).astype(np.int8), 0]))
    out = []
    for a, b in zip(idx[::2], idx[1::2]):
        s, e = int(a) / FPS, int(b) / FPS
        if out and s - out[-1][1] <= gap:
            out[-1][1] = e
        else:
            out.append([s, e])
    return [(s, e) for s, e in out if e - s >= min_len]


def _fr(t):
    return int(round(t * FPS))


def snap_quiet(db, t, radius=0.03):
    """Quietest 10 ms frame within +-radius (frame start time); avoids clicks mid-syllable."""
    i, r = int(round(t * FPS)), int(radius * FPS)
    lo, hi = max(0, i - r), min(len(db), i + r + 1)
    if hi <= lo:
        return t
    return (lo + int(np.argmin(db[lo:hi]))) / FPS


def expected_dur(text):
    """Rough spoken length of a word: Indonesian ~14 letters/s, floor 0.12 s."""
    return max(0.12, len(LX.norm(text)) / 14.0)


class Analysis:
    """Speech mask, held-sound measure and islands of one media file (source time)."""

    def __init__(self, F, words):
        self.words = words
        self.db, self.f0, self.flat = F["db"], F["f0"], F["flat"]
        loud = self.db[self.db > np.percentile(self.db, 10) + 10]
        self.ref = float(np.percentile(loud, 90)) if len(loud) else float(np.max(self.db))
        self.thr = self.ref - 22          # speech vs breath/room: -45/-47 dBFS on the test recordings
        self.sp = self.db > self.thr
        st = F["stab"]
        self.stab = st / (float(np.median(st[self.sp])) if self.sp.any() else 1.0)
        self.voiced = (self.f0 > 0) & self.sp
        self.isl = islands(self.sp)

    def seg(self, s, e):
        return slice(_fr(s), max(_fr(s) + 1, _fr(e)))

    def held_runs(self, s, e, stab_max=0.55, min_len=0.22):
        """Held (steady, voiced) sub-runs inside [s, e]."""
        sl = self.seg(s, e)
        ok = (self.stab[sl] < stab_max) & self.voiced[sl]
        ok = np.convolve(ok.astype(float), np.ones(3) / 3, "same") > 0.5
        return [(s + a, s + b) for a, b in islands(ok, gap=0.03, min_len=min_len)]

    def stats(self, s, e):
        sl = self.seg(s, e)
        f0 = self.f0[sl]
        v = f0[f0 > 0]
        return {"dur": round(e - s, 3), "voiced": round(float(np.mean(self.voiced[sl])), 2),
                "stab": round(float(np.median(self.stab[sl])), 2),
                "f0_cv": round(float(np.std(v) / np.mean(v)), 3) if len(v) > 3 else 1.0,
                "flat": round(float(np.mean(self.flat[sl])), 3),
                "db": round(float(np.mean(self.db[sl]) - self.ref), 1)}

    def neighbours(self, s, e):
        """End of speech before s and start of speech after e (== s / e when glued to a word)."""
        sp = self.sp
        i = _fr(s) - 1
        while i >= 0 and not sp[i]:
            i -= 1
        j = _fr(e)
        while j < len(sp) and not sp[j]:
            j += 1
        return ((i + 1) / FPS if i >= 0 else None), (j / FPS if j < len(sp) else None)

    def cut_range(self, s, e, prev_end, next_start, guard=0.04, snap=0.02):
        """Range to delete for a filler at [s, e]: the filler plus the SHORTER neighbouring pause (minus a
        40 ms guard next to the word), so the speaker's longer, natural pause survives:
        "kita [0.4] eee [0.3] buka" -> "kita [0.4] buka". Edges snap to the quietest 10 ms frame."""
        gb = s - prev_end if prev_end is not None else 9.0
        ga = next_start - e if next_start is not None else 9.0
        if gb >= ga:
            c0, c1 = s, e + max(0.0, ga - guard)
        else:
            c0, c1 = s - max(0.0, gb - guard), e
        c0, c1 = snap_quiet(self.db, c0, snap), snap_quiet(self.db, c1, snap)
        tight = min(gb, ga) < 0.04      # glued to a word: a hard cut may click or clip a consonant
        if ga < 0.04:                   # glued to the NEXT word: let the filler's decay go too
            c1 = self.tail(c1, float(np.median(self.db[self.seg(s, e)])))
        return round(float(c0), 3), round(float(c1), 3), bool(tight)

    def tail(self, t, level, reach=0.10, drop=6.0, rise=1.0, snap=0.01):
        """Cut END glued to the next word and still inside the filler's sound: the held-run end (stability) stops
        where the vowel starts to change, often 30-60 ms before its energy fades. Premiere's QE extract is a hard
        cut (no crossfade), so that edge keeps an audible "e" blip with a click (live check: "eee|gunakan" resumed
        at -22 dB; 9 of 16 pre-checked glued cuts on the 34,6 min test video). Follow the decay forward until the
        level is `drop` dB under the filler, then snap to the quietest frame within +-`snap`. Stopping there (not
        at the deepest dip) keeps a following quiet fricative ("eh sorry") intact. Keeps t when the edge is
        already quiet, or the level stops falling within `reach` (vowel-initial word: the spectral edge is the
        better boundary). Forward only: walking back from a cut START descends the previous word's own syllable
        ramp ("delay|ya" would lose "lay")."""
        db, i = self.db, _fr(t)
        if not 0 <= i < len(db) or db[i] <= level - drop:
            return t
        lo = float(db[i])
        for k in range(i + 1, min(len(db), i + int(round(reach * FPS)) + 1)):
            if db[k] > lo + rise:
                return t
            if db[k] <= level - drop:
                return snap_quiet(db, k / FPS, snap)
            lo = min(lo, float(db[k]))
        return t


def claim(an):
    """Assign each word to the island(s) it really sits on. Whisper stretches word STARTS back into the
    preceding pause, so a word spanning several islands claims them from its END backwards until ~60 % of
    its expected length is covered. An earlier island that is mostly a held sound is never swallowed once
    the word already has audio. -> claimed[k] = [word indexes] per island."""
    claimed = [[] for _ in an.isl]
    if not an.isl:
        return claimed
    starts = np.array([s for s, _ in an.isl])
    held_frac = []
    for s, e in an.isl:
        h = an.held_runs(s, e, min_len=0.12)
        held_frac.append(sum(b - a for a, b in h) / (e - s))
    for wi, w in enumerate(an.words):
        ws, we = w[1], w[2]
        hits = [k for k in range(min(len(an.isl) - 1, int(np.searchsorted(starts, we))), -1, -1)
                if an.isl[k][1] > ws + 0.02 and an.isl[k][0] < we - 0.02]
        need, got, mine = expected_dur(w[0]), 0.0, []
        for k in hits:  # newest island first
            s, e = max(an.isl[k][0], ws), min(an.isl[k][1], we)
            if mine and (got >= 0.6 * need or held_frac[k] > 0.5) and (an.isl[mine[-1]][0] - an.isl[k][1]) > 0.12:
                break
            mine.append(k)
            got += e - s
        for k in mine:
            claimed[k].append(wi)
    return claimed


SUB_RANK = {"lexicon": 0, "listener": 0, "orphan": 1, "prefix": 2, "mid": 3, "drawl": 4}


def detect(an, listener=None, opts=None, progress=None):
    """All raw filler candidates of one media (before tier filtering and overlap merging).
    -> [{"sub", "tier", "start", "end", "cut": [c0, c1], "text", "confidence", "tight", "note"}] seconds."""
    o = {"min_orphan": 0.15, "max_orphan": 2.0, "min_drawl": 0.45, **(opts or {})}
    words, listener = an.words, listener or []
    claimed = claim(an)
    if progress:
        progress(40)
    cands = []
    sp = an.sp

    def grow(a, b, lo, hi, stab_max=0.8):
        """Extend a held run over neighbouring steady voiced frames (runs fragment on pitch jitter)."""
        i, j = _fr(a), _fr(b)
        lo, hi = _fr(lo), _fr(hi)
        steady = sp & (an.stab < stab_max)
        while i - 1 >= lo and (steady[i - 1] or (i - 2 >= lo and steady[i - 2])):
            i -= 1
        while j < hi and (steady[j] or (j + 1 < hi and steady[j + 1])):
            j += 1
        return i / FPS, j / FPS

    orphans = [an.isl[k] for k in range(len(an.isl)) if not claimed[k]]

    def localize(t0, t1):
        """Whisper puts hesitation timestamps 0.1-0.5 s off. Snap to an unclaimed voiced island near the
        token, else the held sound nearest it, else the voiced frames under it -> (s, e, steady) or None."""
        lo, hi = t0 - 0.35, t1 + 0.15
        isl = [(a, b) for a, b in orphans if a < hi and b > lo and b - a <= 2.0]
        if isl:
            a, b = max(isl, key=lambda r: min(r[1], t1) - max(r[0], t0))
            return a, b, True
        runs = an.held_runs(lo, hi, min_len=0.08)
        if runs:
            a, b = max(runs, key=lambda r: (min(r[1], t1) - max(r[0], t0), -abs((r[0] + r[1]) / 2 - (t0 + t1) / 2)))
            a, b = grow(a, b, t0 - 0.5, t1 + 0.3)
            for i0, i1 in an.isl:  # snap to the island edge when only an onset/offset is left over
                if i0 <= a < i1:
                    a = i0 if a - i0 < 0.25 else a
                    b = i1 if 0 < i1 - b < 0.12 else b
                    break
            return a, b, True
        lo_f, hi_f = _fr(max(0, t0 - 0.1)), _fr(t1)
        v = np.flatnonzero(sp[lo_f:hi_f])
        if len(v) < 5:
            return None
        return (lo_f + v[0]) / FPS, (lo_f + v[-1] + 1) / FPS, False

    def add_token(text, t0, t1, src):
        loc = localize(t0, t1)
        if loc is None:            # no audio under the token: stretched into silence or hallucinated
            return
        s, e, steady = loc
        pe, ns = an.neighbours(s, e)
        c0, c1, tight = an.cut_range(s, e, pe, ns)
        # unsteady = placed only by Whisper's timestamp (mean error 0.2 s): fine in a pause, risky when glued
        conf = (0.92 - (0.12 if tight else 0.0)) if steady else (0.70 - (0.15 if tight else 0.0))
        cands.append({"sub": src, "tier": LX.tier(text), "start": round(float(s), 3), "end": round(float(e), 3),
                      "cut": [c0, c1], "text": LX.norm(text), "confidence": round(conf, 2), "tight": tight,
                      "note": ""})

    # 1) hesitation words in the transcript and in the listener pass
    for w in words:
        if LX.is_safe(w[0]):
            add_token(w[0], w[1], w[2], "lexicon")
    for i in LX.listener_hits(listener):
        w = listener[i]
        add_token(w[0], w[1], w[2], "listener")
    if progress:
        progress(60)

    # 2) orphan voiced islands
    for k, (s, e) in enumerate(an.isl):
        if claimed[k] or not (o["min_orphan"] <= e - s <= o["max_orphan"]):
            continue
        st = an.stats(s, e)
        if st["voiced"] < 0.35:
            continue  # breath, mouse/keyboard click, noise
        held = an.held_runs(s, e, min_len=0.12)
        held_frac = sum(b - a for a, b in held) / (e - s)
        # filler = voiced + steady; breath/clicks are unvoiced, missed words are unsteady
        conf = 0.15 + 0.45 * min(1, st["voiced"] / 0.7) * min(1, held_frac / 0.5) \
            + 0.2 * (st["stab"] < 0.7) + 0.2 * (st["f0_cv"] < 0.08)
        note = ""
        heard = [w[0].strip() for w in listener if not LX.is_safe(w[0])
                 and min(w[2], e) - max(w[1], s) > 0.3 * (e - s)]
        if heard:  # the listener heard a real word here: transcript hole, not a filler
            conf, note = min(conf, 0.35), "kata:" + " ".join(heard)
        elif st["db"] < -12:  # quiet murmur/hum: often below the silence threshold anyway
            conf, note = min(conf, 0.6), "pelan"
        pe, ns = an.neighbours(s, e)
        c0, c1, tight = an.cut_range(s, e, pe, ns)
        cands.append({"sub": "orphan", "tier": "acoustic", "start": round(s, 3), "end": round(e, 3),
                      "cut": [c0, c1], "text": "", "confidence": round(float(min(conf, 0.9)), 2), "tight": tight,
                      "note": note})
    if progress:
        progress(80)

    # 3) held sound inside a claimed island: before the words (prefix), between words (mid) or a drawn-out
    #    last vowel (drawl). Acoustics alone cannot tell "eee" from a long vowel, so these stay low.
    for k, (s, e) in enumerate(an.isl):
        if not claimed[k]:
            continue
        need = sum(expected_dur(words[i][0]) for i in claimed[k])
        for a, b in an.held_runs(s, e, min_len=0.2):
            rest = (e - s) - (b - a)  # voiced time left for the words
            pe, _ = an.neighbours(s, e)
            if a - s < 0.25 and e - b > 0.15 and rest >= 0.6 * need:
                c0, _, _ = an.cut_range(s, b, pe, b)
                cands.append({"sub": "prefix", "tier": "acoustic", "start": round(s, 3), "end": round(b, 3),
                              "cut": [c0, round(b - 0.01, 3)], "text": "", "tight": True, "note": "",
                              "confidence": round(min(0.6, 0.2 + 0.4 * (b - a)), 2)})
            elif e - b < 0.08 and b - a >= o["min_drawl"] and rest >= 0.6 * need:
                cands.append({"sub": "drawl", "tier": "acoustic", "start": round(a, 3), "end": round(b, 3),
                              "cut": [round(a + 0.15, 3), round(b, 3)], "text": "", "tight": True, "note": "",
                              "confidence": round(min(0.5, 0.1 + 0.4 * (b - a)), 2)})
            elif a - s >= 0.25 and e - b >= 0.08 and b - a >= 0.3 and rest >= 0.8 * need:
                cands.append({"sub": "mid", "tier": "acoustic", "start": round(a, 3), "end": round(b, 3),
                              "cut": [round(float(a), 3), round(float(b), 3)], "text": "", "tight": True,
                              "note": "", "confidence": round(min(0.45, 0.1 + 0.3 * (b - a)), 2)})
    if progress:
        progress(100)
    return cands


def merge(cands):
    """Drop overlaps (lexicon/listener > orphan > prefix > mid > drawl); a second independent detector on the
    same sound adds +0.05 (max 0.97). Adds "sources" (list of subs). Returns a new list sorted by start."""
    out = []
    for c in sorted(cands, key=lambda c: (SUB_RANK[c["sub"]], -c["confidence"])):  # stable: input order
        same = [d for d in out if not (c["end"] <= d["start"] or c["start"] >= d["end"])]
        if not same:
            out.append({**c, "sources": [c["sub"]]})
        elif c["sub"] not in same[0]["sources"]:
            d = same[0]
            d["sources"].append(c["sub"])
            if c["confidence"] >= 0.3:
                d["confidence"] = round(min(0.97, max(d["confidence"], c["confidence"]) + 0.05), 2)
    out.sort(key=lambda c: c["start"])
    return out


# --------------------------------------------------------------------------- habit-word cuts

class Snapper:
    """Acoustic word boundaries (Whisper word starts are stretched back into pauses)."""

    def __init__(self, an):
        self.an, self.sp = an, an.sp

    def onset(self, t, look=1.0):
        """Real start of the word Whisper says starts at t. In a pause: first speech frame after t. In speech:
        a pause <= 80 ms earlier is the true onset; else the quietest frame within +-50 ms."""
        sp = self.sp
        i = min(_fr(t), len(sp) - 1)
        if sp[i]:
            k = i
            while k > 0 and sp[k - 1] and i - k < 8:
                k -= 1
            if k > 0 and not sp[k - 1]:
                return k / FPS
            return snap_quiet(self.an.db, t, 0.05)
        j = i
        while j < len(sp) and not sp[j] and (j - i) / FPS < look:
            j += 1
        return j / FPS if j < len(sp) and sp[j] else t

    def offset(self, t, look=1.0):
        sp = self.sp
        i = min(_fr(t), len(sp) - 1)
        if sp[i]:
            k = i
            while k + 1 < len(sp) and sp[k + 1] and k - i < 8:
                k += 1
            if k + 1 < len(sp) and not sp[k + 1]:
                return (k + 1) / FPS
            return snap_quiet(self.an.db, t, 0.05)
        j = i
        while j > 0 and not sp[j] and (i - j) / FPS < look:
            j -= 1
        return (j + 1) / FPS if sp[j] else t


def habit_cands(an, enabled):
    """Habit-word candidates (rules in _filler_lexicon.habit_find) with filler-style cuts."""
    words = an.words
    if not words or not len(an.db):
        return []
    sn = Snapper(an)
    out = []
    for i, j, kind, key in LX.habit_find(words, enabled):
        s = sn.onset(words[i][1])
        e = max(s + 0.05, sn.offset(words[j - 1][2]))
        pe, ns = an.neighbours(s, e)
        c0, c1, tight = an.cut_range(s, e, pe, ns)
        if c1 - c0 < 0.06:
            continue
        out.append({"sub": "habit", "tier": "habit", "kind": kind, "key": key, "start": round(float(s), 3),
                    "end": round(float(e), 3), "cut": [c0, c1],
                    "text": " ".join(LX.norm(w[0]) for w in words[i:j]),
                    "confidence": round(LX.HABIT_CONF[kind] - (0.25 if tight else 0.0), 2), "tight": tight,
                    "note": "", "sources": ["lexicon"]})
    return out
