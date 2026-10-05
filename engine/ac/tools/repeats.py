"""Potong Pengulangan (tool id "repeats"): bad takes found on the Whisper transcript, LAST take kept.

Kinds (docs/research/fillers_repeat.md section 8, product_cutting.md section 3, port of proto/fillers/retakes.py):
  stutter     the same 1-3 words said back to back: "gak usah, gak usah pakai hosting", "sorry sorry sorry"
  restart     false start: the first words are abandoned and the next words start the same way
              ("jam kerja ya | Jam kerja itu palingan")
  correction  a phrase followed by a correction marker ("sorry", "eh salah", "maksudnya") and said again
  retake      phrase level: an earlier sentence is (almost) contained in a later one within `window` seconds
Whisper hallucination runs ("ini ini ini ..." over silence, "Terima kasih telah menonton.") are FLAGS
("Transkrip ngaco"), never cuts; repeat candidates touching them are suppressed.

Every candidate is one review item = one group: the removed attempt(s) and the kept take, a confidence and an
`auto` flag. Pre-checked (`on`) = auto-safe kind (stutter, near-identical restart/retake, "sorry" correction)
AND conf >= min_on. Cuts run from the acoustic onset of the removed attempt to the onset of the kept take (the
pause before the attempt survives); an edge that is neither in a pause nor in a >= 8 dB dip is `tight` (-0.15,
listed with "dengarkan dulu", rarely pre-checked). Checked by re-transcribing the edited audio (3,5 s each side):
8 of the 10 default cuts on the 34,6 min tutorial read back as intended, 2 unclear (one-word stutters in fast
speech: "untuk untuk", "sorry sorry sorry"); ASR is only a proxy, listen before trusting it.
A pause longer than max_join (2 s) before the kept take is left alone: only the attempt is removed (long pauses may
hold screen actions; trimming them is Potong Silence's job).

Optional AI ("Minta pendapat AI", grok-auto): the candidate groups are judged once; the AI only adds a verdict
and a reason (`ai`), it never changes `on` or the cut (internal AI research notes: the human decides).

Actions:
  analyze  -> {"review", "summary", "flags", "stats", "ai"}; review file <workdir>/repeats_review.json
  apply    -> {"plan": {"kind": "remove_ranges", "ranges", "timebase": "sequence"}, "summary"}
  preview  -> {"path": wav, "dur", ...}: the audio around one cut WITH the cut applied (hear the edit)
"""
from __future__ import annotations

import difflib
import re
import time
from pathlib import Path

from .. import media as M
from .. import ranges as R
from .. import review as RV
from ..i18n import dec, tr
from ..timeline import Timeline
from ..util import EngineError, ensure_free, read_json, setting, workdir

TITLE = "Cut Repeats"          # developer listing (cli.py tools); UI text: tr("repeat.title")
DESCRIPTION = "Find repeated phrases (stutters, restarts, corrections, retakes); the last take is kept."

TOOL = "repeats"
KINDS = ("stutter", "restart", "correction", "retake")
ID_PREFIX = {"stutter": "s", "restart": "m", "correction": "k", "retake": "t"}
DEFAULTS = {"min_on": 0.65, "min_show": 0.45, "sim": 0.8, "window": 20.0, "max_join": 2.0, "ai": False}
AI_VERSION = 2             # bump when the AI input (takes, extra note) changes: invalidates the AI label cache
AI_NOTE = '\n\nTulis "why" dalam Bahasa Indonesia (maks 12 kata).'   # i18n-ignore: AI prompt, not UI
TIGHT_PENALTY = 0.15       # cut edge inside running sound (no >= 8 dB dip): listed, rarely pre-checked

# --------------------------------------------------------------------------- lexicon
STOP = {"ya", "yang", "di", "ke", "dan", "ini", "itu", "nah", "oke", "kita", "kamu", "guys", "aja", "dulu", "nih",
        "tuh", "kan", "sih", "jadi", "terus", "lalu", "nanti", "ada", "untuk", "dari", "kalau", "kalo"}
CONJ = {"dan", "lalu", "terus", "tapi", "atau", "kemudian", "sama", "serta"}
CLOSERS = {"gitu", "guys", "ya", "nih", "deh", "kan", "sih"}
MARKERS = [["eh", "sorry"], ["sorry", "salah"], ["eh", "salah"], ["eh", "maksudnya"], ["maksud", "saya"],
           ["sorry"], ["maaf"], ["ralat"], ["salah"], ["bukan", "bukan"], ["eh", "bukan"]]
SORRY = {"sorry", "maaf", "sori", "ralat"}
# single words that are said twice on purpose / as tics, or Indonesian reduplication written with a space
LEGIT_DOUBLE = {"ya", "nah", "oke", "ok", "okay", "baik", "iya", "sip", "terus", "lagi", "nih", "tuh", "deh", "dong",
                "sih", "kan", "ayo", "yuk", "halo", "hai", "wah", "aduh", "yah", "lho", "loh", "eh", "hmm", "oh"}
REDUP = {"pelan", "hati", "sama", "masing", "kira", "lama", "jalan", "cepat", "cepet", "baik", "ramai", "beda",
         "macam", "apa", "siapa", "mana", "kapan", "bener", "benar", "tiba", "sering", "sedikit", "dikit", "banyak",
         "satu", "dua", "tiga", "berapa", "orang", "anak", "teman", "temen", "kawan", "barang", "buru", "lain",
         "coba", "pagi", "malam", "bolak", "kadang", "mata", "jaga", "main", "tolong", "makan", "lihat", "liat"}
DEMONSTR = {"ini", "itu", "ada", "sini", "situ", "sana", "gitu", "gini", "begitu", "begini"}
HES_RE = re.compile(r"^(e+|e+h+|(e+h*m+)+|h+m+|m{2,}|u+m+|u+h+|a+h+|a+m+|e+ng|e+u+h*|anu+|ng+|hu+m+)$")


def norm(t):
    """Comparable token: lower case, punctuation and hyphens removed ("Teman-teman," -> "temanteman")."""
    return re.sub(r"[^\w]+", "", str(t).lower())


def is_hes(tok):
    return bool(tok) and bool(HES_RE.match(tok))


def _sec(x):
    return f"{dec(x, 1)} {tr('unit.sec')}"


def kind_label(kind):
    """Review label of a repeat kind in the job language ("Gagap" / "Stutter")."""
    return tr("repeat.kind." + kind)


def _why(key, notes=(), **kw):
    """Row reason in the job language; `notes` = repeat.why.* keys appended after a colon."""
    s = tr("repeat.reason." + key, **kw)
    return s + (": " + ", ".join(tr("repeat.why." + k) for k in notes) if notes else "")


# --------------------------------------------------------------------------- word model
class Word:
    __slots__ = ("text", "t0", "t1", "seg", "tok", "id", "k")

    def __init__(self, text, t0, t1, seg=0, wid=None, k=0):
        self.text = str(text)
        self.t0, self.t1 = float(t0), float(t1)
        self.seg = int(seg or 0)
        self.tok = norm(text)
        self.id = wid
        self.k = k

    def ends_sentence(self):
        return self.text.strip()[-1:] in ".?!"


def make_words(rows):
    """Timeline word dicts {text,t0,t1,seg,id} or rows [text, t0, t1, seg] -> [Word] (sorted, empty tokens dropped)."""
    out = []
    for r in rows:
        if isinstance(r, dict):
            w = Word(r.get("text", ""), r.get("t0", 0.0), r.get("t1", 0.0), r.get("seg", 0), r.get("id"))
        else:
            w = Word(r[0], r[1], r[2], r[3] if len(r) > 3 else 0)
        if w.tok:
            out.append(w)
    out.sort(key=lambda w: (w.t0, w.t1))
    for k, w in enumerate(out):
        w.k = k
    return out


def phrases(W, pause=0.6):
    """[(i, j)] word ranges split at Whisper segment ends, sentence punctuation and pauses >= `pause`."""
    out, start = [], 0
    for k, w in enumerate(W):
        nxt = W[k + 1].t0 if k + 1 < len(W) else None
        if nxt is None or w.seg or w.ends_sentence() or nxt - w.t1 >= pause:
            out.append((start, k + 1))
            start = k + 1
    return out


def _content(toks):
    return [t for t in toks if t not in STOP and not is_hes(t)]


def containment(a, b):
    """Share of a's content tokens that reappear, in order, in b (1.0 = a fully repeated)."""
    ac, bc = _content(a) or [t for t in a if not is_hes(t)], _content(b) or [t for t in b if not is_hes(t)]
    if not ac or not bc:
        return 0.0
    sm = difflib.SequenceMatcher(None, ac, bc, autojunk=False)
    return sum(m.size for m in sm.get_matching_blocks()) / len(ac)


# --------------------------------------------------------------------------- detectors (word indexes)
def _cand(kind, i, j, conf, why, **extra):
    """Removed words W[i:j]; the kept take starts at W[j]."""
    return dict(kind=kind, i=i, j=j, conf=conf, why=why, **extra)


def find_stutters(W, max_gap=1.0, max_n=3):
    """Back-to-back n-gram repeats (n = 1..3, the n covering most words wins; ties -> smaller n). All but the
    last repetition are removed. Sentence-boundary echoes ("di bawah ini. Ini adalah") are skipped or penalised."""
    toks = [w.tok for w in W]
    out, i, n_w = [], 0, len(W)
    while i < n_w:
        hit = None
        for n in range(1, max_n + 1):
            gram = toks[i:i + n]
            if len(gram) < n or any(is_hes(t) for t in gram):
                continue
            reps, j = 1, i + n
            while j + n <= n_w and toks[j:j + n] == gram and W[j].t0 - W[j - 1].t1 < max_gap:
                reps += 1
                j += n
            if reps >= 2 and (hit is None or n * reps > hit[0] * hit[1]):
                hit = (n, reps)
        if not hit:
            i += 1
            continue
        n, reps = hit
        j = i + n * (reps - 1)                       # kept take = last repetition
        gram = toks[i:i + n]
        first, second = W[i + n - 1], W[i + n]
        boundary = (first.seg or first.ends_sentence()) and second.text.strip()[:1].isupper() \
            and not W[i].text.strip()[:1].isupper()
        continues = j + n < n_w and W[j + n].t0 - W[j + n - 1].t1 < 1.0
        skip = False
        if n == 1:
            t = gram[0]
            if t in LEGIT_DOUBLE or (t in REDUP and reps == 2) or t.isdigit() or len(t) < 2:
                skip = True
            elif boundary and (t in STOP or t in DEMONSTR):
                skip = True                            # "di bawah ini. Ini adalah ...": two sentences
        elif all(t in LEGIT_DOUBLE for t in gram):
            skip = True
        if not skip:
            conf = 0.85 if n >= 2 else 0.75
            if reps >= 3:
                conf += 0.05
            auto = True
            notes = []
            if n == 1 and gram[0] in DEMONSTR:
                conf, auto = 0.5, False                # "ada ada", "ini ini": often a new sentence, listen first
                notes.append("demonstr")
            if boundary:
                conf -= 0.3
                notes.append("boundary")
            if not continues:
                conf -= 0.15                           # "..., tolong tolong." at the end: maybe emphasis
                notes.append("sentenceEnd")
            why = _why("stutter", notes, n=n) if reps == 2 else _why("stutterReps", notes, n=n, reps=str(reps))
            out.append(_cand("stutter", i, j, conf, why, auto=auto, n=n, reps=reps))
        i = i + n * reps if not skip else i + 1
    return out


def find_restarts(W, max_abandon=8, max_gap=1.5):
    """Word-level false starts: W[i:i+k] reappears at j (i+k < j <= i+max_abandon); W[i:j] is the abandoned try.
    Immediate repeats (j == i+k) are stutters (find_stutters)."""
    toks = [w.tok for w in W]
    out, i, n = [], 0, len(W)
    while i < n - 1:
        best = None
        for j in range(i + 2, min(n, i + max_abandon + 1)):
            if W[j].t0 - W[j - 1].t1 > max_gap:
                break                                  # a long pause ends the attempt window (retake handles it)
            k = 0
            while j + k < n and i + k < j and toks[i + k] == toks[j + k]:
                k += 1
            if k == 0 or j - i == k:
                continue
            content = _content(toks[i:i + k])
            between = toks[i + k:j]
            pause = W[j].t0 - W[j - 1].t1
            continues = j + k < n and W[j + k].t0 - W[j + k - 1].t1 < 1.0
            if k >= 2 and (content or k >= 3) and continues:
                conf = 0.85 if k >= 3 else 0.75
            elif k == 1 and len(toks[i]) >= 5 and toks[i] not in STOP and continues and \
                    (pause > 0.25 or all(is_hes(t) for t in between)):
                conf = 0.55
            else:
                continue
            if toks[j - 1] in CONJ:
                continue                               # "udah saya tinjau nih DAN udah saya setujui": coordination
            rest = _content(toks[i + k:j])
            if j - i > k + 4:
                conf -= 0.15                           # long abandoned tail: maybe two sentences that start alike
            ends_phrase = any(W[m].seg or W[m + 1].t0 - W[m].t1 > 0.3 for m in range(i + k, j - 1)) \
                or (j - 1 >= i + k and toks[j - 1] in CLOSERS)
            pen = []
            if len(rest) >= 2 and ends_phrase:
                conf -= 0.3                            # abandoned part is a finished clause
                pen.append("clause")
            follow = _content(toks[j + k:j + k + max(1, len(rest))])
            marker = any(toks[m:m + len(p)] == p for p in MARKERS for m in range(i + k, j))
            if rest and follow and len(rest) <= 2 and not set(rest) & set(follow) and not marker:
                conf -= 0.35                           # parallel list / Q&A: "stok | akun", "SBS | site"
                pen.append("list")
            sorry = any(t in SORRY for t in toks[i:j])
            cand = _cand("restart", i, j, conf, _why("restart", pen, n=k), parallel="list" in pen, sorry=sorry, k=k)
            if best is None or cand["conf"] > best["conf"]:
                best = cand
        if best:
            out.append(best)
            i = best["j"]
        else:
            i += 1
    return out


def find_corrections(W, phr):
    """Correction marker after a phrase: drop that phrase + the marker, keep what follows."""
    toks = [w.tok for w in W]
    owner = {}
    for a, b in phr:
        for k in range(a, b):
            owner[k] = (a, b)
    out, i = [], 0
    while i < len(W):
        m = next((p for p in MARKERS if toks[i:i + len(p)] == p), None)
        if not m or i == 0:
            i += 1
            continue
        end = i + len(m)
        a = owner[i - 1][0]
        if end < len(toks) and m[-1] in ("salah", "sorry") and toks[end] not in STOP and \
                W[end].t0 - W[end - 1].t1 < 0.2 and toks[end] != toks[a]:
            i = end                                    # "sorry salah akun": narrating a mistake on screen
            continue
        if a == i:                                     # marker opens the phrase: the attempt is the previous one
            a = owner[a - 1][0] if a > 0 else a
        prev = [t for t in toks[a:i] if not is_hes(t)]
        nxt = [t for t in toks[end:min(len(W), end + max(4, len(prev) + 2))] if not is_hes(t)]
        sim = difflib.SequenceMatcher(None, prev, nxt, autojunk=False).ratio() if prev and nxt else 0.0
        if 0 < len(prev) <= 12 and end < len(W):
            conf = 0.5 + 0.3 * min(1.0, sim / 0.5)
            out.append(_cand("correction", a, end, conf, _why("correction", marker=" ".join(m), pct=f"{sim:.0%}"),
                             sorry=bool(set(m) & SORRY), marker=" ".join(m), sim=round(sim, 3)))
        i = end
    return out


def find_retakes(W, phr, window=20.0, lookahead=4, min_tokens=3):
    """Phrase level: the span from phrase x up to a later phrase y vs an equally long span starting at y; the
    earlier attempt must be (almost) CONTAINED in the later one, and the later one must START with the match."""
    toks = [w.tok for w in W]

    def tk(i, j):
        return [t for t in toks[i:j] if not is_hes(t)]

    out, x = [], 0
    while x < len(phr):
        a, b = phr[x]
        if len(tk(a, b)) < min_tokens and x + 1 < len(phr):
            b = phr[x + 1][1]                          # too short alone: carry the next phrase
        best = None
        for y in range(x + 1, min(len(phr), x + 1 + lookahead)):
            c, _ = phr[y]
            if c < b or W[c].t0 - W[c - 1].t1 > window:
                continue
            A = tk(a, c)
            if len(A) < min_tokens or len(A) > 25:
                continue
            B = tk(c, min(len(W), c + len(A) + 3))
            Ac, Bc = [t for t in A if t not in STOP], [t for t in B if t not in STOP]
            if len(Ac) < 2:
                continue
            sm = difflib.SequenceMatcher(None, Ac, Bc, autojunk=False)
            blocks = sm.get_matching_blocks()
            cover = sum(m.size for m in blocks) / len(Ac)
            block = max((m.size for m in blocks), default=0)
            starts_here = blocks[0].size and blocks[0].b <= 1
            if cover >= 0.6 and block >= 2 and starts_here and (best is None or cover > best[1]):
                best = (y, cover, A, B, Ac, Bc)
        if best:
            y, cover, A, B, Ac, Bc = best
            c, _ = phr[y]
            diff_a = [t for t in Ac if t not in Bc]
            diff_b = [t for t in Bc[:len(Ac)] if t not in Ac]
            pen, why = 0.0, []
            parallel = 1 <= len(diff_a) <= 2 and 1 <= len(diff_b) <= 3 and not any(t in ("eh", "sorry") for t in A + B)
            if parallel:
                pen += 0.35
                why.append("list")
            if toks[a] in CONJ:
                pen += 0.3
                why.append("conj")
            if any(t in ("kalau", "kalo", "jika") for t in B[:2]) and not any(t in ("kalau", "kalo", "jika") for t in A[:2]):
                pen += 0.25
                why.append("explain")
            gap = W[c].t0 - W[c - 1].t1
            conf = 0.4 + 0.5 * cover - pen - (0.1 if len(A) > 12 else 0)
            out.append(_cand("retake", a, c, conf, _why("retake", why, sec=_sec(gap), pct=f"{cover:.0%}"),
                             parallel=parallel, cover=round(cover, 3)))
            x = y
        else:
            x += 1
    return out


def find_loops(W, on=None, hop=M.HOP):
    """Whisper loops that survived the transcript filter: an n-gram (n = 1..3) said >= 3x back to back over
    mostly quiet audio (< 50 % sounding frames). -> [(i, j)] word ranges. Over clear sound it is a real
    stammer and find_stutters handles it."""
    toks = [w.tok for w in W]
    out, i = [], 0
    while i < len(W):
        hit = None
        for n in (1, 2, 3):
            gram = toks[i:i + n]
            if len(gram) < n:
                continue
            reps, j = 1, i + n
            while j + n <= len(W) and toks[j:j + n] == gram:
                reps += 1
                j += n
            if reps >= 3 and (hit is None or j > hit[1]):
                hit = (i, j)
        if hit and (on is None or _sounding(W[hit[0]].t0, W[hit[1] - 1].t1, on, hop) < 0.5):
            out.append(hit)
            i = hit[1]
        else:
            i += 1
    return out


def _sounding(t0, t1, on, hop=M.HOP):
    a, b = max(0, int(t0 / hop)), max(int(t0 / hop) + 1, int(t1 / hop))
    seg = on[a:b]
    return float(seg.mean()) if len(seg) else 0.0


def detect(W, phr=None, kinds=KINDS, window=20.0):
    """All candidates, overlaps resolved (higher confidence wins; the loser is noted in `also`)."""
    phr = phr if phr is not None else phrases(W)
    cands = []
    if "stutter" in kinds:
        cands += find_stutters(W)
    if "restart" in kinds:
        cands += find_restarts(W)
    if "correction" in kinds:
        cands += find_corrections(W, phr)
    if "retake" in kinds:
        cands += find_retakes(W, phr, window=window)
    cands.sort(key=lambda c: (-c["conf"], c["i"]))
    out = []
    for c in cands:
        hit = [d for d in out if not (c["j"] <= d["i"] or c["i"] >= d["j"])]
        if hit:
            hit[0].setdefault("also", []).append(c["kind"])
        else:
            out.append(c)
    out.sort(key=lambda c: c["i"])
    return out


# --------------------------------------------------------------------------- acoustic cut points
class Snapper:
    """Cut points on the sequence envelope (10 ms dB frames). Whisper starts are de-stretched in cache v3, but
    words can still be glued; onset/offset look for the real sound edge (port of proto/fillers/review.py)."""

    def __init__(self, db, thr=None):
        self.db = db
        self.n = len(db)
        self.thr = (word_thr(db) if self.n else -45.0) if thr is None else thr
        self.sp = M.gate(db, self.thr) if self.n else db

    def fr(self, t):
        return max(0, min(self.n - 1, int(round(t / M.HOP))))

    def onset(self, t, look=1.0):
        if not self.n:
            return t
        i, sp = self.fr(t), self.sp
        if sp[i]:
            k = i
            while k > 0 and sp[k - 1] and i - k < 8:
                k -= 1
            if k > 0 and not sp[k - 1]:
                return k * M.HOP
            return M.snap_quiet(self.db, t, 0.05)
        j = i
        while j < self.n and not sp[j] and (j - i) * M.HOP < look:
            j += 1
        return j * M.HOP if j < self.n and sp[j] else t

    def offset(self, t, look=1.0):
        if not self.n:
            return t
        i, sp = self.fr(t), self.sp
        if sp[i]:
            k = i
            while k + 1 < self.n and sp[k + 1] and k - i < 8:
                k += 1
            if k + 1 < self.n and not sp[k + 1]:
                return (k + 1) * M.HOP
            return M.snap_quiet(self.db, t, 0.05)
        j = i
        while j > 0 and not sp[j] and (i - j) * M.HOP < look:
            j -= 1
        return (j + 1) * M.HOP if sp[j] else t

    def speech_at(self, t):
        return bool(self.n) and bool(self.sp[self.fr(t)])

    def quiet(self, t, r=0.02):
        return M.snap_quiet(self.db, t, r) if self.n else t

    def dip(self, t, r=0.15):
        """How far (dB) the frame at t sits below the loudest frame within +-r: >= 8 dB = a joint between sounds."""
        if not self.n:
            return 99.0
        k, w = self.fr(t), int(round(r / M.HOP))
        return float(self.db[max(0, k - w):k + w + 1].max() - self.db[k])

    def joint_ok(self, t):
        """A hard cut at t will not chop a sound: the frame is below the word threshold or in a >= 8 dB dip."""
        return not self.n or float(self.db[self.fr(t)]) <= self.thr or self.dip(t) >= 8.0


def word_thr(db):
    """Speech threshold for word edges: p90 of the non-silent frames - 22 dB (proto/fillers acoustics), never
    below the Otsu valley. -42,7 dB on the 34,6 min tutorial (Otsu -53,5 counts word tails as speech)."""
    import numpy as np
    db = np.asarray(db)
    nf = db[db > M.FLOOR_DB + 5]
    otsu = M.otsu_db(db)
    if len(nf) < 50:
        return otsu
    return max(otsu, float(np.percentile(nf, 90)) - 22.0)


def cut_for(sn, W, i, j, max_join=2.0):
    """Cut [c0, c1] removing W[i:j] before the kept take W[j]. -> (c0, c1, tight, long_gap or None)."""
    s = sn.onset(W[i].t0)
    e = max(s + 0.05, sn.offset(W[j - 1].t1))
    glued_s = sn.speech_at(s - 0.02)
    c0 = s if glued_s else sn.quiet(s - 0.02)
    if j >= len(W):                                     # nothing after: remove the attempt + a short tail
        return round(c0, 3), round(max(e, sn.quiet(e + 0.05)), 3), glued_s, None
    nxt = max(sn.onset(W[j].t0), e)
    if nxt - e > max_join:                              # long pause before the new take: leave it to Potong Silence
        c1 = sn.quiet(e + 0.1)
        return round(c0, 3), round(max(e, c1), 3), glued_s, round(nxt - e, 2)
    glued_n = sn.speech_at(nxt - 0.02)
    if glued_n:  # kept take glued to the removed words: end the cut in the nearest pause before it (keeps a word)
        k = sn.fr(nxt) - 1
        lo = max(sn.fr(e) - 20, sn.fr(s) + 10)
        sp = sn.sp
        while k > lo and (sp[k] or sp[k - 1] or sp[k - 2] or sp[k - 3]):
            k -= 1
        if k > lo and nxt - k * M.HOP <= 0.6:
            nxt, glued_n = (k + 1) * M.HOP + 0.01, False
    c1 = nxt if glued_n else sn.quiet(nxt - 0.03)
    return round(c0, 3), round(max(c1, e), 3), bool(glued_s or glued_n), None


# --------------------------------------------------------------------------- flags (Whisper hallucinations)
def transcript_flags(tl, include_muted=False):
    """Hallucinations the transcript filter dropped (cache `issues`), mapped to sequence time."""
    from .. import transcript as T
    out, seen = [], set()
    docs = {}
    for c in tl.audio_clips(include_muted):
        if c.path not in docs:
            docs[c.path] = T.load_doc(c.path) if Path(c.path).is_file() else None
        doc = docs[c.path]
        if not doc:
            continue
        lo, hi = min(c.src_in, c.src_out), max(c.src_in, c.src_out)
        for it in doc.get("issues") or []:
            a, b = max(float(it["t0"]), lo), min(float(it["t1"]), hi)
            if b <= a:
                continue
            t0, t1 = round(c.src_to_seq(a), 3), round(c.src_to_seq(b), 3)
            key = (c.path, round(a, 2), round(b, 2), round(t0, 2))
            if key in seen:
                continue
            seen.add(key)
            out.append({"t0": t0, "t1": t1, "text": str(it.get("text", ""))[:160], "kind": it.get("kind", "loop"),
                        "src": "transcript"})
    return out


def flag_label(kind):
    return tr("repeat.flag." + {"phrase": "phrase", "loop": "loop", "dense": "loop", "segloop": "loop"}.get(kind, "other"))


# --------------------------------------------------------------------------- AI labels (optional)
def ai_labels(items, W, emit=None, model=None, chunk=40):
    """Ask the AI to judge the groups. Adds item["ai"] = {verdict: "cut"|"keep"|"other", reason, keep, drop}.
    Never changes `on`/cut. Returns {"used", "source", "model", "warnings", "n"}."""
    from ..ai import client as ai
    from ..ai import prompts as P
    from ..ai import tasks
    model = model or setting("aiModel") or tasks.DEFAULT_MODEL["repeats"]
    info = {"used": True, "source": "none", "model": model, "warnings": [], "n": 0}
    if not items:
        return info
    dw = [{"i": k, "text": w.text.strip(), "start": w.t0, "end": w.t1, "seg_end": w.seg} for k, w in enumerate(W)]
    groups = []
    for it in items:
        takes = []
        spans = list(it["_drops"])
        j, _ = it["_keep"]
        if it["kind"] != "stutter":
            # the kept take runs to its sentence end (>= the attempt + 3 words), so the model does not mistake a
            # truncated kept take for a fragment and "keep the complete earlier take"
            need = j + max(4, spans[-1][1] - spans[0][0] + 3)
            end = j + 1
            while end < len(W) and end - j < 20 and (end < need or not (W[end - 1].seg or W[end - 1].ends_sentence())):
                end += 1
            spans.append((j, end))
        else:
            spans.append(it["_keep"])
        for (a, b) in spans:
            takes.append({"w0": a, "w1": b - 1, "text": " ".join(dw[k]["text"] for k in range(a, b)),
                          "start": W[a].t0, "end": W[b - 1].t1})
        groups.append({"kind": "stutter" if it["kind"] == "stutter" else "restart", "takes": takes})
    store = ai.Cache()
    key = store.key("repeats-labels", AI_VERSION, P.PROMPT_VERSION, model,
                    [[[t["text"], round(t["start"], 2)] for t in g["takes"]] for g in groups])
    hit = store.get(key)
    if hit:
        res, info["source"] = hit, "cache"
        if emit:
            emit.done_note(tr("repeat.note.aiCache"))
    else:
        parts = [groups[o:o + chunk] for o in range(0, len(groups), chunk)]

        def one(part):
            msgs = P.repeats_messages(dw, part)
            msgs[-1]["content"] += AI_NOTE
            return P._run("repeats", msgs, P.REPEAT_SCHEMA, None,
                          lambda o, p=part: P.repeats_post(o, p), lambda: {"groups": []}, model)

        outs = ai.map_parallel(one, parts) if len(parts) > 1 else [one(parts[0])]
        res, srcs = [], set()
        for part, r in zip(parts, outs):
            if isinstance(r, Exception):
                info["warnings"].append(tr("repeat.warn.aiFailed", err=type(r).__name__))
                srcs.add("fallback")
                res.append(None)
                continue
            srcs.add(r["source"])
            info["warnings"] += r.get("warnings", [])
            got = {g["g"]: g for g in r["result"].get("groups", [])} if r["source"] == "ai" else {}
            res += [got.get(k) for k in range(len(part))]
        info["source"] = "ai" if srcs == {"ai"} else ("mixed" if "ai" in srcs else "fallback")
        if info["source"] == "ai":
            store.put(key, res)
    for it, g in zip(items, res):
        if not g:
            continue
        labels = [chr(65 + n) for n in range(len(it["_drops"]) + 1)]
        keep, drop = list(g.get("keep") or []), list(g.get("drop") or [])
        same = it["kind"] == "stutter" and len({norm(t["text"]) for t in g.get("takes") or []}) == 1
        if not drop:
            verdict = "keep"
        elif (keep == labels[-1:] and sorted(drop) == labels[:-1]) or (same and len(keep) == 1):
            verdict = "cut"                            # identical stutter takes: which one is kept does not matter
        else:
            verdict = "other"
        it["ai"] = {"verdict": verdict, "reason": str(g.get("why") or "")[:160], "keep": keep, "drop": drop}
        info["n"] += 1
    return info


# --------------------------------------------------------------------------- analyze
def _params(p):
    out = dict(DEFAULTS)
    for k in ("min_on", "min_show", "sim", "window", "max_join"):
        if p.get(k) is not None:
            out[k] = float(p[k])
    if out["sim"] > 1.0:
        out["sim"] /= 100.0                            # panel slider sends 60..95 (%)
    if out["min_on"] > 1.0:
        out["min_on"] /= 100.0
    if out["min_show"] > 1.0:
        out["min_show"] /= 100.0
    kinds = p.get("kinds")
    out["kinds"] = tuple(k for k in (kinds or KINDS) if k in KINDS) or KINDS
    out["ai"] = bool(p.get("ai"))
    return out


def _text(W, a, b, limit=None):
    ws = W[a:b] if limit is None else W[a:min(b, a + limit)]
    s = " ".join(w.text.strip() for w in ws)
    return s + (" ..." if limit is not None and b - a > limit else "")


def build_items(W, cands, sn, prm, scope=None, flags=()):
    """Candidates -> review items (sequence seconds). Drops what is below min_show, outside the scope or touches
    a flagged hallucination; sets conf (tight -TIGHT_PENALTY), auto, on."""
    items = []
    flag_r = [[f["t0"] - 0.3, f["t1"] + 0.3] for f in flags]
    for c in cands:
        i, j = c["i"], c["j"]
        if j <= i:
            continue
        c0, c1, _glued, long_gap = cut_for(sn, W, i, j, prm["max_join"])
        if c1 - c0 < 0.06:
            continue
        tight = not (sn.joint_ok(c0) and sn.joint_ok(c1))
        span_end = W[j].t0 if j < len(W) else W[j - 1].t1
        if any(R.overlap(W[i].t0, span_end, a, b) > 0 for a, b in flag_r):
            continue
        if scope and not any(c0 >= a - 0.05 and c1 <= b + 0.05 for a, b in scope):
            continue
        conf = c["conf"] - (TIGHT_PENALTY if tight else 0.0)
        if conf < prm["min_show"]:
            continue
        kind = c["kind"]
        n_keep = max(3, min(10, (j - i) + 2))
        keep_end = min(len(W), j + n_keep)
        for m in range(j, keep_end):                   # the kept take ends at its phrase end
            if m > j and (W[m - 1].seg or W[m - 1].ends_sentence()):
                keep_end = m
                break
        drop_toks, keep_toks = [w.tok for w in W[i:j]], [w.tok for w in W[j:j + (j - i) + 3]]
        sim = 1.0 if kind == "stutter" else c["sim"] if kind == "correction" else containment(drop_toks, keep_toks)
        if kind == "stutter":
            auto = c.get("auto", True)
        elif kind == "correction":     # "sorry" + the next words echo the attempt, or a near-identical redo
            auto = (c.get("sorry", False) and sim >= 0.3) or sim >= prm["sim"]
        elif kind == "restart":
            auto = (sim >= prm["sim"] or c.get("sorry", False)) and not c.get("parallel")
        else:
            auto = sim >= prm["sim"] and not c.get("parallel")
        if kind == "stutter" and c.get("reps", 2) > 2:
            n = c["n"]
            drops = [(i + r * n, i + (r + 1) * n) for r in range(c["reps"] - 1)]
        else:
            drops = [(i, j)]
        why = c["why"]
        note = None
        if tight:
            note = {"type": "warn", "text": tr("repeat.note.tight")}
        elif long_gap:
            note = {"type": "info", "text": tr("repeat.note.longGap", sec=_sec(long_gap))}
        it = RV.item(c0, c1, kind, on=False, conf=max(0.0, min(1.0, conf)),
                     label=_text(W, i, j, 14), ctx={"pre": _text(W, max(0, i - 4), i), "post": _text(W, j, keep_end)},
                     note=note, src="rule",
                     drop=_text(W, i, j), keep=_text(W, j, keep_end), after=_text(W, keep_end, keep_end + 3),
                     why=why, kind_label=kind_label(kind), auto=bool(auto), tight=bool(tight),
                     sim=round(sim, 3), words=[W[i].id, W[j - 1].id] if W[i].id else None,
                     keep_t=[round(W[j].t0, 3), round(W[keep_end - 1].t1, 3)] if j < len(W) else None,
                     takes=[{"t0": round(W[a].t0, 3), "t1": round(W[b - 1].t1, 3), "text": _text(W, a, b), "role": "drop"}
                            for a, b in drops] + ([{"t0": round(W[j].t0, 3), "t1": round(W[keep_end - 1].t1, 3),
                                                     "text": _text(W, j, keep_end), "role": "keep"}] if j < len(W) else []))
        if long_gap:
            it["long_gap"] = long_gap
        if c.get("also"):
            it["also"] = sorted(set(c["also"]))
        it["_drops"], it["_keep"] = drops, (j, keep_end)
        it["on"] = bool(auto) and it["conf"] >= prm["min_on"] and kind in prm["kinds"]
        items.append(it)
    return items


def _note_ai(it):
    a = it.get("ai")
    if not a:
        return
    if a["verdict"] == "keep":
        it["note"] = {"type": "warn", "text": tr("repeat.ai.keep", reason=a["reason"]).strip()}
    elif a["verdict"] == "other":
        it["note"] = {"type": "warn", "text": tr("repeat.ai.other", reason=a["reason"]).strip()}
    elif not it.get("tight"):
        it["note"] = {"type": "ai", "text": tr("repeat.ai.cut", reason=a["reason"] or tr("repeat.ai.agree"))}


def analyze(job, emit):
    p = job.get("params") or {}
    prm = _params(p)
    tl = Timeline.from_json(job["seq"])
    if not tl.audio_clips():
        raise EngineError("NO_AUDIO", tr("repeat.err.noAudio"), tr("repeat.err.noAudioHint"))
    stages = [("words", tr("repeat.stage.words"), 0.6), ("audio", tr("repeat.stage.audio"), 0.1),
              ("find", tr("repeat.stage.find"), 0.15)]
    if prm["ai"]:
        stages.append(("ai", tr("repeat.stage.ai"), 0.2))
    stages.append(("review", tr("repeat.stage.review"), 0.05))
    emit.plan(stages)
    t_start = time.time()
    with emit.step("words"):
        rows = tl.words_on_timeline(emit=emit)
        emit.progress(100)
    with emit.step("audio"):
        db = tl.envelope_on_timeline(emit=emit)
        sn = Snapper(db)
        emit.done_note(tr("repeat.note.threshold", thr=f"{sn.thr:.0f}"))
        emit.progress(100)
    with emit.step("find"):
        W = make_words(rows)
        flags = transcript_flags(tl)
        loops = find_loops(W, sn.sp if sn.n else None)
        for a, b in loops:
            flags.append({"t0": round(W[a].t0, 3), "t1": round(W[b - 1].t1, 3), "text": _text(W, a, b, 12),
                          "kind": "loop", "src": "repeat"})
        if loops:
            gone = {k for a, b in loops for k in range(a, b)}
            W = [w for w in W if w.k not in gone]
            for k, w in enumerate(W):
                w.k = k
        emit.progress(30)
        scope = tl.scope_ranges(p.get("scope"))
        full = len(scope) == 1 and scope[0][0] <= 0.01 and scope[0][1] >= tl.duration - 0.01
        cands = detect(W, kinds=prm["kinds"], window=prm["window"])
        emit.progress(70)
        items = build_items(W, cands, sn, prm, None if full else scope, flags)
        flags.sort(key=lambda f: f["t0"])
        for f in flags:
            f["label"] = flag_label(f["kind"])
        emit.progress(100)
        emit.done_note(tr("repeat.note.found", n=len(items)))
    ai_info = {"used": False}
    if prm["ai"]:
        with emit.step("ai"):
            ai_info = ai_labels(items, W, emit=emit)
            if ai_info["source"] in ("fallback", "none") and items:
                why = (ai_info["warnings"] or [tr("repeat.warn.aiMissing")])[0]
                emit.warn(tr("repeat.warn.aiUnused", why=why))
            elif ai_info["source"] == "mixed":
                emit.warn(tr("repeat.warn.aiPartial"))
            for it in items:
                _note_ai(it)
            emit.progress(100)
    with emit.step("review"):
        for it in items:
            it.pop("_drops", None)
            it.pop("_keep", None)
        for kind in KINDS:
            RV.assign_ids([it for it in items if it["kind"] == kind], prefix=ID_PREFIX[kind])
        by_kind = {k: sum(1 for it in items if it["kind"] == k) for k in KINDS}
        stats = {"by_kind": by_kind, "flags": len(flags), "words": len(W), "secs": round(time.time() - t_start, 2)}
        params = {k: prm[k] for k in ("min_on", "min_show", "sim", "window", "max_join", "ai")}
        params["kinds"] = list(prm["kinds"])
        if p.get("scope") is not None:
            params["scope"] = p.get("scope")
        doc = RV.new(TOOL, items, tl.duration, seq=tl, params=params, stats=stats, fps=tl.fps, flags=flags,
                     ai={k: v for k, v in ai_info.items() if k != "warnings"})
        path = RV.path_for(workdir(job, tl.name), TOOL)
        old = read_json(path)
        if isinstance(old, dict) and (old.get("seq") or {}).get("id") == tl.id:
            carried = RV.carry_over(doc, old)
            if carried:
                emit.log(f"{carried} manual choices carried over from the previous analysis")
        doc["stats"].update(RV.summary(doc))
        RV.save(doc, path)
        emit.progress(100)
    return {"review": str(path), "summary": RV.summary(doc), "flags": flags, "stats": doc["stats"],
            "ai": doc["ai"], "duration": tl.duration}


# --------------------------------------------------------------------------- apply
def apply(job, emit):
    with emit.step("plan", tr("repeat.stage.plan")):
        doc = RV.from_job(job)
        seq = job.get("seq") or {}
        if isinstance(seq, dict) and seq.get("id") and seq.get("id") == (doc.get("seq") or {}).get("id"):
            try:
                cur = Timeline.from_json(seq).duration
            except EngineError:
                cur = None
            if cur and abs(cur - float(doc.get("duration") or 0)) > 0.05:
                raise EngineError("SEQ_CHANGED", tr("repeat.err.seqChanged"), tr("repeat.err.seqChangedHint"))
        fps = float(doc.get("fps") or 0) or 30.0
        rng = R.to_frames(RV.selected_ranges(doc), fps, "inner")
        rng = [r for r in rng if r[1] - r[0] >= 1.0 / fps - 1e-6]
        emit.progress(100)
    summ = RV.summary(doc)
    summ["count"] = len(rng)
    return {"plan": {"kind": "remove_ranges", "ranges": R.round_list(rng, 6), "timebase": "sequence"},
            "summary": summ, "seq": doc.get("seq")}


# --------------------------------------------------------------------------- preview (hear the edit)
def _seq_audio(tl, a, b, sr):
    """Mono float32 of what is heard in sequence range [a, b) (lowest audio track wins), silence elsewhere."""
    import numpy as np
    n = max(0, int(round((b - a) * sr)))
    out = np.zeros(n, np.float32)
    clips = sorted(tl.audio_clips(), key=lambda c: c.track, reverse=True)   # lowest track written last
    for c in clips:
        s, e = max(a, c.start), min(b, c.end)
        if e - s <= 1e-3 or not Path(c.path).is_file():
            continue
        x = M.load_audio(c.path, sr=sr, start=max(0.0, c.seq_to_src(s)), dur=(e - s) * (c.speed or 1.0))
        if (c.speed or 1.0) != 1.0 and len(x):
            idx = np.linspace(0, len(x) - 1, int(round((e - s) * sr))).astype(np.int64)
            x = x[idx]
        k = int(round((s - a) * sr))
        m = min(len(x), n - k)
        if m > 0:
            out[k:k + m] = x[:m]
    return out


def preview(job, emit):
    """params: {cut: [c0, c1], pre: 2.0, post: 1.5, id}. Writes <workdir>/repeat_preview/<id>.wav (the
    audio before the cut, a 10 ms equal-power crossfade, the audio after) and returns its path."""
    import wave

    import numpy as np
    p = job.get("params") or {}
    tl = Timeline.from_json(job["seq"])
    try:
        c0, c1 = float(p["cut"][0]), float(p["cut"][1])
    except (KeyError, TypeError, ValueError, IndexError):
        raise EngineError("BAD_JOB", tr("repeat.err.badCut"), tr("repeat.err.badCutHint"))
    pre, post, sr = float(p.get("pre", 2.0)), float(p.get("post", 1.5)), 48000
    with emit.step("preview", tr("repeat.stage.preview")):
        out_dir = workdir(job, tl.name) / "repeat_preview"
        out_dir.mkdir(parents=True, exist_ok=True)
        ensure_free(4 * 1024 ** 2, out_dir)
        a = _seq_audio(tl, max(0.0, c0 - pre), c0, sr)
        b = _seq_audio(tl, c1, min(tl.duration, c1 + post), sr)
        fade = int(0.01 * sr)
        if len(a) > fade and len(b) > fade:
            t = np.linspace(0, np.pi / 2, fade, dtype=np.float32)
            mid = a[-fade:] * np.cos(t) + b[:fade] * np.sin(t)
            y = np.concatenate([a[:-fade], mid, b[fade:]])
        else:
            y = np.concatenate([a, b])
        pcm = (np.clip(y, -1.0, 1.0) * 32767).astype("<i2")
        name = re.sub(r"[^\w-]+", "_", str(p.get("id") or f"{c0:.2f}"))[:40]
        old = sorted(out_dir.glob("*.wav"), key=lambda f: f.stat().st_mtime)
        for f in old[:-5]:                               # keep the last few, never pile up
            try:
                f.unlink()
            except OSError:
                pass
        path = out_dir / f"{name}_{int(time.time() * 1000) % 100000}.wav"
        with wave.open(str(path), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sr)
            wf.writeframes(pcm.tobytes())
        emit.progress(100)
    return {"path": str(path), "dur": round(len(y) / sr, 3), "cut": [c0, c1], "pre": round(len(a) / sr, 3),
            "post": round(len(b) / sr, 3)}


ACTIONS = {"analyze": analyze, "apply": apply, "preview": preview}

