"""Viral Clips (tool id "viral"): find short-form clips (TikTok / Reels / Shorts) inside the sequence.

analyze (docs/research/product_ai.md 3.3 + AI research notes 5b; port of docs/research/proto_product_ai/viral.py + rerank.py)
  1. transcript on the timeline (cache v3) -> display words -> utterance units (ac.ai.text), Whisper outro
     hallucinations dropped, limited to the scope (whole sequence / In-Out / selected clips).
  2. AI (optional, only on the user's click): ~7-min windows with 1-min overlap asked in parallel (at most
     AI_MAX_PARALLEL in flight) for up to k clips each. The model copies "mm:ss" + verbatim quotes (never ids);
     every answer is cached per window, so a re-run costs no AI quota.
  3. local post-processing: quote-anchored boundaries (prompts.resolve inside the window), filler openers
     trimmed ("nah / oke / baik / jadi ..."), whole units grown/shrunk into [min, max] ending at a natural pause,
     padded into the pauses, numbers in title/hook that the clip never says flagged (grounding), loudness
     "energy" from the envelope (the Emotion-AI stand-in), topic match.
  4. overlapping candidates (overlapping windows find the same moment) are merged, then ONE global rescore call
     ranks all candidates against each other (calibrated scores, better hooks, short why).
  5. without AI (proxy down, AI switched off, or failed windows): sentence-aligned windows scored by hook
     keywords, loudness, speech density and topic, labelled "no AI" (src "rule").
  Writes <workdir>/viral_review.json: items kind "clip" sorted best first; the best `count` are on.

apply: edited review -> {"plan": markers plan (tag [Klipora-VR], colour by score), "clips": sub-sequence list
  ("Viral 01 - <title>", best first, frame-snapped), "bin": "Klipora Viral"}. The panel creates the sub-sequences
  with panel/host/36_viral.jsx (bac_viral_subseq) and then adds the markers on the source sequence (the old
  [AC-VR] markers are cleared too: bac_clearMarkersByTag knows both tags).

User-facing text (stage labels, warnings, errors, review notes, marker comments, summaries) follows the job
language (ac.i18n, keys viral.*). AI prompts are content and stay as they are. Sequence names "Viral 01 - <title>"
and the bin name are Premiere names: the same in every language.
"""
from __future__ import annotations

import math
import re
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait

from .. import review as RV
from ..i18n import dec, tr
from ..timeline import Timeline
from ..util import EngineError, fmt_sec, read_json, setting, workdir

TITLE = "Viral Clips"   # developer listing (cli.py tools); the panel UI title is the locale key viral.title
DESCRIPTION = "Find the best moments for Shorts, TikTok and Reels, with a score for each."

TAG = "[Klipora-VR]"      # the panel/host also clear the old "[AC-VR]" markers
BIN = "Klipora Viral"     # the host also finds the old bin "AutoCut Viral"
WIN_VERSION = "viral-2026-10-05.2"     # part of the AI cache keys: bump when that prompt changes
RESCORE_VERSION = "viral-rescore-2026-10-05.4"
WINDOW_S, OVERLAP_S = 420.0, 60.0      # 7-min windows, 1-min overlap (product_ai.md 3.3)
MAX_RESCORE = 20                        # candidates in the global rescore call (letters A..T)

PRESETS = {  # id -> (name, style line for the prompt, extra fallback keywords, energy weight); UI labels: panel viral.style*
    "default": ("Default", "Moments that make a viewer stop scrolling: a strong first line and a clear payoff.",
                (), 0.10),
    "edukasi": ("Edukasi", "Stand-alone tips or steps with ONE clear lesson the viewer can apply right away.",
                ("cara", "caranya", "langkah", "tips", "trik", "pertama", "kedua", "pastikan", "contoh", "fungsi"),
                0.10),
    "lucu": ("Lucu", "Funny, spontaneous moments: slips, reactions, jokes, playful banter, surprised laughter.",
             ("wkwk", "haha", "ketawa", "lucu", "gila", "anjir", "parah", "astaga", "ngakak", "kocak"), 0.20),
    "jualan": ("Jualan", "Moments that explain benefits, results, prices or promos and make viewers want to buy "
               "or sign up.", ("harga", "promo", "diskon", "murah", "untung", "beli", "daftar", "gratis",
                               "modal", "cuan", "profit", "jual", "jualan", "bonus"), 0.10),
    "cerita": ("Cerita", "A mini story: a situation or problem, tension, and a resolution or lesson.",
               ("waktu", "dulu", "pernah", "ceritanya", "akhirnya", "ternyata", "tiba-tiba", "awalnya"), 0.15),
    "custom": ("Custom", "", (), 0.10),
}

# Fallback keyword cues (Indonesian tutorials / talking heads). Normalised words (lowercase, no punctuation).
HOOK_WORDS = {"gimana", "bagaimana", "kenapa", "mengapa", "berapa", "cara", "caranya", "tips", "trik", "rahasia",
              "jangan", "wajib", "penting", "salah", "kesalahan", "gratis", "murah", "untung", "cuan", "profit",
              "hasil", "berhasil", "langsung", "mudah", "gampang", "cepat", "otomatis", "terbaik", "harus",
              "ternyata", "masalah", "solusi", "error", "gagal", "bahaya", "aman", "promo", "diskon", "harga",
              "modal", "duit", "uang", "juta", "ribu", "rb", "rp", "pertama", "rugi", "hemat", "bisa", "nih"}
QUESTION_STARTS = {"apa", "gimana", "bagaimana", "kenapa", "mengapa", "berapa", "mana", "siapa", "kapan", "bisakah"}
EMOTION_WORDS = {"wkwk", "wkwkwk", "haha", "hahaha", "gila", "anjir", "wow", "serius", "mantap", "keren", "astaga",
                 "parah", "asli", "beneran", "luar", "biasa", "senang", "kaget", "sedih", "kesel"}
# Words that open a sentence without saying anything ("Nah, jadi ..."): a hook never starts with them.
OPENERS = {"nah", "oke", "ok", "okay", "okey", "baik", "jadi", "ya", "yah", "eh", "em", "emm", "ehm", "hmm", "hm",
           "terus", "lalu", "nih", "sip", "oh", "anyway", "guys", "gaes", "teman-teman", "temen-temen", "dan",
           "nah,", "selanjutnya", "kemudian", "tadi", "gitu", "pokoknya", "intinya", "baiklah", "alright", "so"}

_NUM = re.compile(r"\d+(?:[.,]\d+)*")
_WORD = re.compile(r"[^\w\-]+")


# ====================================================================== small helpers

def _w(text):
    """Normalised word: lowercase, punctuation stripped (keeps '-' for teman-teman)."""
    return _WORD.sub("", str(text).lower()).strip("-")


def _words(text):
    return [x for x in (_w(t) for t in str(text).split()) if x]


def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


def _int(v, default=0):
    try:
        return int(round(float(v)))
    except (TypeError, ValueError):
        return default


def score_color(score):
    """Premiere marker colour index: 0 green (>= 80), 4 yellow (60-79), 3 orange (< 60)."""
    return 0 if score >= 80 else 4 if score >= 60 else 3


def clip_name(k, title):
    """Sequence name "Viral 01 - <title>" (control characters removed, max ~80 chars)."""
    t = re.sub(r"[\x00-\x1f\x7f]+", " ", str(title or "")).strip()
    t = re.sub(r"\s+", " ", t)[:70].rstrip(" -.,") or tr("viral.clipWord")
    return f"Viral {k:02d} - {t}"


def norm_params(p, duration):
    """User params -> {min_s, max_s, target, count (0 = auto), preset, prompt, topic, use_ai}."""
    lo = _clamp(float(p.get("min", 20) or 20), 5.0, 170.0)
    hi = _clamp(float(p.get("max", 60) or 60), 10.0, 180.0)
    if hi < lo + 5:
        hi = min(180.0, lo + 5)
    preset = str(p.get("preset") or "default").lower()
    if preset not in PRESETS:
        preset = "default"
    prompt = re.sub(r"\s+", " ", str(p.get("prompt") or "")).strip()[:400]
    if preset == "custom" and not prompt:
        preset = "default"
    count = _int(p.get("count"), 0)
    return {"min_s": lo, "max_s": hi, "target": lo + 0.4 * (hi - lo), "count": _clamp(count, 0, 30),
            "preset": preset, "prompt": prompt, "topic": re.sub(r"\s+", " ", str(p.get("topic") or "")).strip()[:120],
            "use_ai": p.get("ai", True) is not False, "model": p.get("model") or None}


def auto_count(dur, target):
    """About one clip per 3.5 min (max 20); short videos get what fits without overlap."""
    n = int(round(dur / 210.0))
    return int(_clamp(max(n, min(3, int(dur // (target * 1.2)))), 1, 20))


def style_line(prm):
    if prm["preset"] == "custom":
        return "User instructions: " + prm["prompt"]
    return PRESETS[prm["preset"]][1]


# ====================================================================== transcript units

def build_units(words, scope_ranges, max_s):
    """-> (dwords, units, groups). units = utterance units inside the scope, each with grp/filler/bad flags;
    groups = [(lo, hi, r0, r1)] unit index spans that a clip may not cross (scope range or hallucinated unit)."""
    from ..ai.text import display_words, sentences
    from ..transcript import HALLU_RE
    dwords = display_words(words)
    sents = sentences(dwords, max_gap=1.0, max_dur=min(20.0, max_s / 2.0), seg_words=6)
    units, groups = [], []
    cur = None
    for s in sents:
        mid = (s["start"] + s["end"]) / 2
        g = next((k for k, r in enumerate(scope_ranges) if r[0] - 1e-6 <= mid < r[1] + 1e-6), None)
        bad = bool(HALLU_RE.search(s["text"]))
        if g is None or bad:
            cur = None
            continue
        toks = _words(s["text"])
        u = dict(s, i=len(units), grp=g, filler=bool(toks) and len(toks) <= 3 and all(t in OPENERS for t in toks),
                 gap_before=s["start"] - (units[-1]["end"] if units else scope_ranges[g][0]))
        if cur is None or cur[4] != g:
            cur = [len(units), len(units), scope_ranges[g][0], scope_ranges[g][1], g]
            groups.append(cur)
        cur[1] = len(units)
        units.append(u)
    return dwords, units, [tuple(g[:4]) for g in groups]


def group_of(groups, i):
    for g in groups:
        if g[0] <= i <= g[1]:
            return g
    return None


def windows(units, groups, size=WINDOW_S, overlap=OVERLAP_S):
    """Index spans [(a, b)] of ~size seconds (step size - overlap) inside each group."""
    out = []
    for lo, hi, _, _ in groups:
        t = units[lo]["start"]
        end = units[hi]["end"]
        while True:
            idx = [i for i in range(lo, hi + 1) if t <= units[i]["start"] < t + size]
            if idx:
                out.append((idx[0], idx[-1]))
            if t + size >= end:
                break
            t += size - overlap
    return out


# ====================================================================== clip geometry

def trim_opener(units, dwords, a, b):
    """Skip filler-only units and up to 3 leading filler words ("Nah, jadi ..."). -> (a, first word index)."""
    while a < b and units[a]["filler"]:
        a += 1
    k, w1 = units[a]["w0"], units[a]["w1"]
    skipped = 0
    while k < w1 and skipped < 3 and _w(dwords[k]["text"]) in OPENERS and w1 - k >= 2:
        k += 1
        skipped += 1
    return a, k


def fit(units, a, b, lo, hi, min_s, max_s, target, keep_end=False):
    """Grow/shrink [a..b] by whole units (inside lo..hi) so the clip lasts min_s..max_s and ends at a natural
    pause. keep_end=True keeps a model-chosen end that is already in range (only a mid-sentence end is
    extended). -> (a, b) or None."""
    def dur(x, y):
        return units[y]["end"] - units[x]["start"]

    def gap_after(y):
        return units[y + 1]["start"] - units[y]["end"] if y < hi else 99.0

    b = max(a, min(b, hi))
    while b > a and dur(a, b) > max_s:
        b -= 1
    if not (keep_end and min_s <= dur(a, b) <= max_s):
        while dur(a, b) < min_s:
            if b < hi and dur(a, b + 1) <= max_s:
                b += 1
            elif a > lo and dur(a - 1, b) <= max_s and not units[a - 1]["filler"]:
                a -= 1
            else:
                break
        while b < hi and dur(a, b + 1) <= max_s and dur(a, b) < target and gap_after(b) < 0.8:
            b += 1
    while b < hi and dur(a, b + 1) <= max_s and gap_after(b) < 0.35:   # never stop mid-sentence
        b += 1
    d = dur(a, b)
    if d < min_s * 0.8 or d > max_s + 0.01:
        return None
    return a, b


def bounds(units, dwords, a, k, b, grp):
    """Clip [t0, t1] in sequence seconds: first kept word - small pad, last unit end + pad into the pause,
    clamped to the scope range. -> (t0, t1, text)."""
    lo_i, hi_i, r0, r1 = grp
    t0 = dwords[k]["start"]
    prev_end = dwords[k - 1]["end"] if k > units[lo_i]["w0"] else r0
    t0 = max(r0, t0 - min(0.15, max(0.0, t0 - prev_end) / 2))
    t1 = units[b]["end"]
    nxt = units[b + 1]["start"] if b < hi_i else min(r1, t1 + 0.7)
    t1 = min(r1, t1 + min(0.35, max(0.0, nxt - t1) / 2))
    text = " ".join(dwords[i]["text"] for i in range(k, units[b]["w1"] + 1)).strip()
    return round(t0, 3), round(t1, 3), text


def iou(a, b):
    inter = max(0.0, min(a["t1"], b["t1"]) - max(a["t0"], b["t0"]))
    union = max(a["t1"], b["t1"]) - min(a["t0"], b["t0"])
    return inter / union if union > 0 else 0.0


def overlaps(a, b, frac=0.25):
    """True when two clips share more than `frac` of the shorter one (or IoU >= 0.3)."""
    inter = max(0.0, min(a["t1"], b["t1"]) - max(a["t0"], b["t0"]))
    short = min(a["t1"] - a["t0"], b["t1"] - b["t0"]) or 1.0
    return inter / short > frac or iou(a, b) >= 0.3


def nms(cands):
    """Best-first non-maximum suppression (overlapping windows propose the same moment twice)."""
    kept = []
    for c in sorted(cands, key=lambda c: (-c["score"], c["t0"])):
        if all(not overlaps(c, k) for k in kept):
            kept.append(c)
    return kept


# ====================================================================== scoring features

def energy_stats(db):
    """Loudness reference of the scope: (threshold, median, p95) over voiced 10 ms frames, or None."""
    import numpy as np
    from .. import media as M
    if db is None or len(db) == 0:
        return None
    thr = M.otsu_db(db)
    v = db[db >= thr]
    if len(v) < 50:
        return None
    return float(thr), float(np.median(v)), float(np.percentile(v, 95))


def energy(db, t0, t1, st):
    """0..100 "emotion" proxy without a model: 0.7 loudness (p75 of voiced frames vs the video median, scaled
    to p95) + 0.3 dynamics (std / 8 dB). Tutorials measure 45-65; 50 when unknown."""
    import numpy as np
    from .. import media as M
    if st is None:
        return 50
    thr, med, p95 = st
    seg = M.env_slice(db, t0, t1)
    v = seg[seg >= thr]
    if len(v) < 20:
        return 0
    loud = _clamp((float(np.percentile(v, 75)) - med) / max(p95 - med, 1.0) * 100, 0, 100)
    dyn = _clamp(float(np.std(v)) / 8.0 * 100, 0, 100)
    return int(round(0.7 * loud + 0.3 * dyn))


def topic_terms(topic):
    from ..ai.prompts import STOP
    return [t for t in dict.fromkeys(_words(topic)) if len(t) >= 3 and t not in STOP]


def topic_match(text, terms):
    """Share of topic terms said in the clip (prefix or fuzzy match, misheard words tolerated). None = no topic."""
    if not terms:
        return None
    import difflib
    ws = set(_words(text))
    hit = 0
    for t in terms:
        if t in ws or any(w.startswith(t[:5]) and len(t) >= 4 for w in ws) \
                or any(difflib.SequenceMatcher(None, t, w).ratio() >= 0.82 for w in ws if abs(len(w) - len(t)) <= 3):
            hit += 1
    return round(hit / len(terms), 2)


def numbers(text):
    return {re.sub(r"[.,]", "", n) for n in _NUM.findall(str(text or ""))}


def ungrounded(title, hook, text):
    """Numbers in the title/hook that the clip never says (the model invents prices / counts)."""
    said = numbers(text)
    return sorted(n for n in numbers(f"{title} {hook}") if n not in said)


def keyword_hits(text, extra=()):
    ws = _words(text)
    pool = HOOK_WORDS | EMOTION_WORDS | set(extra)
    return sum(1 for w in ws if w in pool or _NUM.fullmatch(w)) + str(text).count("?")


def hook_strength(text, extra=()):
    """0..100 for the first seconds: question openers, hook words, numbers, emotion."""
    ws = _words(text)
    if not ws:
        return 0
    s = 30 if ws[0] in QUESTION_STARTS or "?" in text else 0
    s += 22 * keyword_hits(text, extra)
    return int(_clamp(s, 0, 100))


# ====================================================================== fallback (no AI)

def rule_candidates(units, dwords, groups, spans, prm, db, est, terms):
    """Sentence-aligned windows scored by keyword cues + loudness + speech density (+ topic). Every start after
    a pause (or with a hook word) is tried; NMS happens later. -> candidate dicts (src "rule")."""
    extra = PRESETS[prm["preset"]][2]
    out = []
    for a0, b0 in spans:
        grp = group_of(groups, a0)
        if grp is None:
            continue
        for i in range(a0, min(b0, grp[1]) + 1):
            u = units[i]
            if u["filler"]:
                continue
            first = _words(u["text"])[:3]
            if not (i == grp[0] or u["gap_before"] >= 0.6 or any(w in HOOK_WORDS or w in QUESTION_STARTS for w in first)):
                continue
            a, k = trim_opener(units, dwords, i, i)
            ab = fit(units, a, a, grp[0], grp[1], prm["min_s"], prm["max_s"], prm["target"])
            if not ab:
                continue
            a, b = ab
            if a != i:
                continue  # fit walked backwards: that start is tried on its own
            t0, t1, text = bounds(units, dwords, a, k, b, grp)
            d = t1 - t0
            if d < prm["min_s"] * 0.8:
                continue
            head = " ".join(dwords[j]["text"] for j in range(k, units[a]["w1"] + 1))
            j = a + 1
            while j <= b and units[j]["start"] < t0 + 8.0:
                head += " " + units[j]["text"]
                j += 1
            hook = hook_strength(head, extra)
            value = int(_clamp(keyword_hits(text, extra) / max(d / 30.0, 0.5) * 22, 0, 100))
            wps = len(text.split()) / max(d, 1.0)
            dens = int(_clamp((wps - 1.2) / 2.0 * 100, 0, 100))
            en = energy(db, t0, t1, est)
            tm = topic_match(text, terms)
            w_e = 0.1 + PRESETS[prm["preset"]][3]                       # energy matters more for lucu/cerita
            raw = 0.35 * hook + 0.25 * value + w_e * en + (0.4 - w_e) * dens
            score = 20 + 0.6 * raw + (15 * tm - (12 if tm == 0 else 0) if tm is not None else 0)
            title = _title_from(text)
            why = tr("viral.ruleWhy", n=keyword_hits(text, extra), energy=en, wps=dec(wps, 1))
            out.append({"t0": t0, "t1": t1, "text": text, "units": [a, b], "src": "rule",
                        "scores": {"hook": hook, "value": value, "energy": en, "density": dens},
                        "score": int(_clamp(round(score), 1, 85)), "title": title, "hook": "", "why": why,
                        "topic": tm})
    return out


def _title_from(text, n=60):
    t = re.sub(r"\s+", " ", str(text)).strip()
    if len(t) > n:
        t = t[:n].rsplit(" ", 1)[0]
    t = t.rstrip(" ,.")
    return (t[:1].upper() + t[1:]) if t else tr("viral.clipWord")


# ====================================================================== AI path

WIN_SCHEMA = {"type": "object", "required": ["clips"], "properties": {
    "clips": {"type": "array", "maxItems": 12, "items": {
        "type": "object", "required": ["start", "start_quote", "title"],
        "properties": {
            "start": {"type": "string", "minLength": 2}, "end": {"type": "string"},
            "start_quote": {"type": "string"}, "end_quote": {"type": "string"},
            "title": {"type": "string", "minLength": 2, "maxLength": 140},
            "hook": {"type": "string", "maxLength": 140}, "scores": {"type": "object"},
            "score": {"type": "number"}, "reason": {"type": "string"}}}}}}

RESCORE_SCHEMA = {"type": "object", "required": ["clips"], "properties": {
    "clips": {"type": "array", "items": {"type": "object", "required": ["c", "score"], "properties": {
        "c": {"type": "string", "minLength": 1, "maxLength": 3}, "score": {"type": "number", "minimum": 0, "maximum": 100},
        "title": {"type": "string", "maxLength": 140}, "hook": {"type": "string", "maxLength": 140},
        "why": {"type": "string"}}}}}}


def window_messages(lines, k, prm, part, total, glossary=()):
    from ..ai.prompts import BASE
    gl = f"\nKnown names (correct spelling): {', '.join(glossary)}." if glossary else ""
    topic = (f"\n6. The user is searching for clips about: \"{prm['topic']}\". Prefer clips about it; if this part "
             "never talks about it, return only clearly strong clips.") if prm["topic"] else ""
    sys_ = (
        f"""{BASE}\n"""  # i18n-ignore (AI prompt)
        f"""Task: pick the best short-form clips (TikTok / Reels / Shorts) inside ONE PART of a longer Indonesian video.\n"""  # i18n-ignore (AI prompt)
        f"""Input: one transcript line per row: start-end text (times mm:ss).\n"""  # i18n-ignore (AI prompt)
        f"""A clip is a run of consecutive lines: from the start of its first line to the end of its last line.\n"""  # i18n-ignore (AI prompt)
        f"""Rules:\n"""  # i18n-ignore (AI prompt)
        f"""1. Duration {prm['min_s']:.0f}-{prm['max_s']:.0f} seconds (ideal about {prm['target']:.0f} s): extend over several consecutive lines. Duration = end minus start, e.g. 02:59 -> 03:41 = 42 s.\n"""  # i18n-ignore (AI prompt)
        f"""2. Self-contained: a viewer understands it without the rest of the video. Start at the beginning of a thought, never mid-sentence and never with filler ("nah", "oke", "baik", "jadi tadi"). End after the point is complete.\n"""  # i18n-ignore (AI prompt)
        f"""3. The first line is the hook: a question, a bold claim, a surprising result, a clear promise, a mistake to avoid or a useful tip.\n"""  # i18n-ignore (AI prompt)
        f"""4. Avoid greetings, outros and pure "klik ini, klik itu" narration.\n"""  # i18n-ignore (AI prompt)
        f"""5. Style wanted: {style_line(prm)}{topic}\n"""  # i18n-ignore (AI prompt)
        f"""7. Up to {k} clips that do not overlap, best first. Return fewer (or none) when this part has no good clip.\n"""  # i18n-ignore (AI prompt)
        f"""8. start / end: copy the times shown (start of the first line, end of the last line). start_quote: the first 4-8 words of the first line; end_quote: the last 4-8 words of the last line; copy them exactly.\n"""  # i18n-ignore (AI prompt)
        f"""9. scores 0-100 each: hook (the first 3 seconds hold the viewer), flow (complete arc with an ending), value (useful, entertaining or emotional), trend (relevant for Indonesian viewers now). Be honest: an ordinary line scores 30-60; 80+ only for genuinely strong moments.\n"""  # i18n-ignore (AI prompt)
        f"""10. title: Indonesian Shorts title, 4-9 words, max 60 characters, curiosity but honest, only facts said in the clip (e.g. "Cara Pasang Name Server Biar Web Aman"). hook: on-screen text for the first seconds, Indonesian, 3-7 words, different from the title, makes the viewer curious, only facts said in the clip (e.g. "Web kamu belum aman?"). reason: Indonesian, max 15 words.{gl}\n"""  # i18n-ignore (AI prompt)
        f"""Output: {{"clips":[{{"start":"00:00","end":"00:00","start_quote":"...","end_quote":"...","scores":{{"hook":0,"flow":0,"value":0,"trend":0}},"title":"...","hook":"...","reason":"..."}}]}}"""  # i18n-ignore (AI prompt)
    )
    from ..ai.text import ts
    user = f"Bagian {ts(part[0])}-{ts(part[1])} dari video {ts(total)}.\n" + "\n".join(lines)  # i18n-ignore (AI prompt)
    return [{"role": "system", "content": sys_}, {"role": "user", "content": user}]


def rescore_messages(cands, prm, glossary=()):
    from ..ai.prompts import BASE
    gl = f"\nKnown names (correct spelling): {', '.join(glossary)}." if glossary else ""
    topic = f" The user is searching for clips about \"{prm['topic']}\": rank those higher." if prm["topic"] else ""
    rows = [f"{chr(65 + k)} ({c['t1'] - c['t0']:.0f} s): {c['text'][:600]}" for k, c in enumerate(cands)]
    sys_ = (
        f"""{BASE}\n"""  # i18n-ignore (AI prompt)
        f"""Task: you are a strict Indonesian Shorts / TikTok curator. Compare ALL candidate clips below with each other (they come from the same video).\n"""  # i18n-ignore (AI prompt)
        f"""For EVERY letter give a calibrated viral score 0-100: at most one clip >= 85, most clips 40-75, greetings / filler / cut-off clips < 40.\n"""  # i18n-ignore (AI prompt)
        f"""Style wanted: {style_line(prm)}{topic}\n"""  # i18n-ignore (AI prompt)
        f"""Also give: title (Indonesian Shorts title, 4-9 words, max 60 characters, honest curiosity, only facts said in the clip, e.g. "Cara Pasang Name Server Biar Web Aman"), hook (on-screen text for the first seconds, Indonesian, 3-7 words, different from the title, never invent facts; write a stronger one when the first line is weak, e.g. "Web kamu belum aman?"), why (Indonesian, max 12 words: why it works or why it is weak).\n"""  # i18n-ignore (AI prompt)
        f"""Write title, hook and why in Bahasa Indonesia (casual creator language), never in English.{gl}\n"""  # i18n-ignore (AI prompt)
        f"""Output: {{"clips":[{{"c":"A","score":0,"hook":"...","title":"...","why":"..."}}]}}"""  # i18n-ignore (AI prompt)
    )
    return [{"role": "system", "content": sys_}, {"role": "user", "content": "\n".join(rows)}]


def window_score(sc, w_e=0.1):
    """Per-window composite (product_ai.md 3.3): 0.35 hook + 0.20 flow + 0.25 value + 0.10 trend, scaled to
    1 - w_e, + w_e energy (0.1 for tutorials, more for funny / story presets)."""
    base = (0.35 * sc["hook"] + 0.2 * sc["flow"] + 0.25 * sc["value"] + 0.1 * sc["trend"]) / 0.9
    return int(round((1 - w_e) * base + w_e * sc.get("energy", 50)))


def _sub_scores(c):
    sc = c.get("scores") if isinstance(c.get("scores"), dict) else {}
    base = _int(c.get("score"), 50)
    return {k: int(_clamp(_int(sc.get(k), base), 0, 100)) for k in ("hook", "flow", "value", "trend")}


def ai_candidates(obj, units, dwords, groups, win, prm, db, est, terms, stats):
    """Window answer -> candidates: resolve quotes inside the window, trim openers, fit, bounds, scores."""
    from ..ai.prompts import resolve
    a0, b0 = win
    local = units[a0:b0 + 1]
    out = []
    for c in obj.get("clips") or []:
        if not isinstance(c, dict):
            continue
        a, ha = resolve(local, c.get("start"), c.get("start_quote", ""), True)
        stats["resolve"][ha] = stats["resolve"].get(ha, 0) + 1
        if a is None:
            stats["dropped"] += 1
            continue
        b, hb = (None, "none")
        if c.get("end") or c.get("end_quote"):
            b, hb = resolve(local, c.get("end") or "", c.get("end_quote", ""), False, after=a - 1)
        a, b = a + a0, (b + a0 if b is not None else a + a0)
        grp = group_of(groups, a)
        a, _ = trim_opener(units, dwords, a, max(a, b))
        ab = fit(units, a, max(a, b), grp[0], grp[1], prm["min_s"], prm["max_s"], prm["target"],
                 keep_end=hb != "none")
        if not ab:
            stats["dropped"] += 1
            continue
        a, k = trim_opener(units, dwords, ab[0], ab[1])   # fit may have walked back onto a filler opener
        b = ab[1]
        t0, t1, text = bounds(units, dwords, a, k, b, grp)
        if t1 - t0 < prm["min_s"] * 0.8:
            stats["dropped"] += 1
            continue
        sc = _sub_scores(c)
        sc["energy"] = energy(db, t0, t1, est)
        title = re.sub(r"\s+", " ", str(c.get("title") or "")).strip()[:70] or _title_from(text)
        hook = re.sub(r"\s+", " ", str(c.get("hook") or "")).strip()[:60]
        out.append({"t0": t0, "t1": t1, "text": text, "units": [a, b], "src": "ai", "scores": sc,
                    "score": window_score(sc, PRESETS[prm["preset"]][3]),
                    "title": title, "hook": hook, "why": re.sub(r"\s+", " ", str(c.get("reason") or "")).strip()[:160],
                    "topic": topic_match(text, terms)})
    return out


def _run_parallel(fn, items, emit, label):
    """fn over items with at most AI_MAX_PARALLEL threads; the main thread keeps the cancel point alive and
    reports "n/m" progress. -> list of results (an Exception object per failed item)."""
    from ..ai import client as ai
    if not items:
        return []
    ex = ThreadPoolExecutor(max_workers=max(1, int(ai.config()["max_parallel"])))
    futs = {ex.submit(fn, it): i for i, it in enumerate(items)}
    res = [None] * len(items)
    try:
        pending, last = set(futs), -1
        while pending:
            done, pending = wait(pending, timeout=0.4, return_when=FIRST_COMPLETED)
            for f in done:
                try:
                    res[futs[f]] = f.result()
                except Exception as e:  # noqa: BLE001 - one failed window falls back to rules
                    res[futs[f]] = e
            n = len(items) - len(pending)
            if n != last:
                last = n
                emit.progress(100.0 * n / len(items), note=f"{label} {n}/{len(items)}")
            else:
                emit.check_cancel()
    finally:
        ex.shutdown(wait=False, cancel_futures=True)
    return res


# ====================================================================== analyze stages

def ai_find(units, dwords, groups, prm, db, est, terms, count, model, glossary, emit, stats):
    """Ask every window in parallel (cached per window). -> (candidates, failed window spans)."""
    from ..ai import client as ai
    from ..ai.text import ts
    wins = windows(units, groups)
    stats["windows"] = len(wins)
    k = int(_clamp(math.ceil(1.5 * count / max(1, len(wins))) + 1, 2, 5))
    store = ai.Cache()
    total = units[-1]["end"]

    def ask(win):
        a0, b0 = win
        lines = [f"{ts(u['start'])}-{ts(u['end'])} {u['text']}" for u in units[a0:b0 + 1]]
        key = store.key("viral_win", WIN_VERSION, model, style_line(prm), prm["topic"], prm["min_s"], prm["max_s"], k,
                        glossary, lines)
        hit = store.get(key)
        if hit is not None:
            return hit, True
        obj = ai.chat_json(window_messages(lines, k, prm, (units[a0]["start"], units[b0]["end"]), total, glossary),
                           schema=WIN_SCHEMA, model=model, tag="viral")
        store.put(key, obj)
        return obj, False

    emit.log(f"AI viral: {len(wins)} windows, up to {k} clips each ({model})")
    cands, failed = [], []
    for win, r in zip(wins, _run_parallel(ask, wins, emit, tr("viral.note.parts"))):
        if isinstance(r, Exception):
            stats["failed"] += 1
            failed.append(win)
            emit.log(f"window {win} failed: {type(r).__name__}: {r}")
            continue
        obj, cached = r
        stats["cached" if cached else "ai_calls"] += 1
        cands += ai_candidates(obj, units, dwords, groups, win, prm, db, est, terms, stats)
    return cands, failed


def rescore_sane(obj, n):
    """A rescore answer is usable unless it is degenerate: every score 0 (seen live from grok-auto: 15 x
    "score": 0, all "viewer skip") or one flat value for 4+ clips. Such an answer must not replace the window
    scores, and must never be cached."""
    sc = [_int(it.get("score"), -1) for it in (obj or {}).get("clips") or [] if isinstance(it, dict)]
    sc = [v for v in sc if 0 <= v <= 100]
    if not sc or max(sc) <= 5:
        return False
    return not (n >= 4 and len(sc) >= 4 and len(set(sc)) == 1)


def rescore(keep, prm, model, glossary, emit, stats, warnings):
    """One global call ranks every candidate against the others (cached). Updates keep in place."""
    from ..ai import client as ai
    store = ai.Cache()
    key = store.key("viral_rescore", RESCORE_VERSION, model, style_line(prm), prm["topic"], glossary,
                    [[c["t0"], c["t1"], c["text"][:600]] for c in keep])
    obj = store.get(key)
    if obj is not None and not rescore_sane(obj, len(keep)):
        obj = None                      # a degenerate answer cached by an older version: ask again
    if obj is None:
        emit.progress(5, note=tr("viral.note.compare"))
        r = _run_parallel(lambda msgs: ai.chat_json(msgs, schema=RESCORE_SCHEMA, model=model, tag="viral_rescore"),
                          [rescore_messages(keep, prm, glossary)], emit, tr("viral.note.rescore"))[0]
        if isinstance(r, Exception):
            warnings.append(tr("viral.warn.rescoreFailed", err=f"{type(r).__name__}: {r}"))
            emit.warn(warnings[-1])
            return
        stats["ai_calls"] += 1
        if not rescore_sane(r, len(keep)):
            warnings.append(tr("viral.warn.rescoreOdd"))
            emit.warn(warnings[-1])
            return
        obj = r
        store.put(key, obj)
    else:
        stats["cached"] += 1
    w_e = PRESETS[prm["preset"]][3]
    for it in obj.get("clips") or []:
        kk = ord((str(it.get("c", "?")).strip().upper() or "?")[:1]) - 65
        if not 0 <= kk < len(keep):
            continue
        c = keep[kk]
        # the global call is the calibrated judge; the window score only breaks ties
        c["scores"]["ai"] = int(_clamp(_int(it.get("score")), 0, 100))
        c["score"] = int(round((0.9 - w_e) * c["scores"]["ai"] + 0.1 * c["score"]
                               + w_e * c["scores"].get("energy", 50)))
        prev_hook = c.get("hook", "")
        for f, n in (("title", 70), ("hook", 60), ("why", 160)):
            v = re.sub(r"\s+", " ", str(it.get(f) or "")).strip()[:n]
            if v:
                c[f] = v
        if _w(c["hook"].replace(" ", "")) == _w(c["title"].replace(" ", "")):   # grok-auto likes to echo the title
            c["hook"] = prev_hook if _w(prev_hook.replace(" ", "")) != _w(c["title"].replace(" ", "")) else ""
        c["rescored"] = True
        c["src"] = "ai"
        stats["rescored"] += 1


def finish(keep):
    """Topic bonus, grounding penalty (AI text only), clamp, final NMS. -> best-first list."""
    for c in keep:
        if c["src"] == "ai":
            if c.get("topic"):
                c["score"] += int(round(8 * c["topic"]))
            c["ungrounded"] = ungrounded(c["title"], c["hook"], c["text"])
            if c["ungrounded"]:
                c["score"] -= 10
        c["score"] = int(_clamp(c["score"], 1, 99))
    return nms(keep)


def review_items(keep, count):
    items = []
    for rank, c in enumerate(keep, 1):
        if c.get("ungrounded"):
            note = {"type": "warn", "text": tr("viral.note.ungrounded", nums=", ".join(c["ungrounded"]))}
        elif c["src"] == "ai":
            note = {"type": "ai", "text": c.get("why") or ""}
        else:
            note = {"type": "info", "text": c.get("why") or tr("viral.noAi")}
        snip = c["text"] if len(c["text"]) <= 160 else c["text"][:160].rsplit(" ", 1)[0] + " ..."
        items.append(RV.item(c["t0"], c["t1"], "clip", on=rank <= count, conf=c["score"] / 100.0, label=c["title"],
                             ctx=snip, note=note, score=c["score"], scores=c["scores"], title=c["title"],
                             hook=c.get("hook", ""), why=c.get("why", ""), text=c["text"][:1200], src=c["src"],
                             topic=c.get("topic"), ungrounded=c.get("ungrounded", []), rank=rank,
                             rescored=bool(c.get("rescored")), dur=round(c["t1"] - c["t0"], 2)))
    return items


# ====================================================================== actions

def analyze(job, emit):
    p = job.get("params") or {}
    tl = Timeline.from_json(job.get("seq"))
    scope = tl.scope_ranges(p.get("scope"))
    scope_dur = sum(b - a for a, b in scope)
    prm = norm_params(p, scope_dur)
    from ..ai import client as ai
    use_ai = prm["use_ai"] and ai.available()
    cfg = ai.config()
    ai_why = "" if use_ai else (tr("viral.why.offTool") if not prm["use_ai"] else
                                tr("viral.why.offSettings") if cfg["disabled"] else
                                tr("viral.why.noKey") if not cfg["has_key"] else tr("viral.why.down"))
    emit.plan([("words", tr("viral.stage.words"), 0.25), ("audio", tr("viral.stage.audio"), 0.1),
               ("find", tr("viral.stage.findAi") if use_ai else tr("viral.stage.findRule"), 0.45 if use_ai else 0.2),
               ("rank", tr("viral.stage.rankAi") if use_ai else tr("viral.stage.rank"), 0.15),
               ("review", tr("viral.stage.review"), 0.05)])
    t_start = time.time()

    with emit.step("words"):
        words = tl.words_on_timeline(emit=emit)
        if not words:
            raise EngineError("NO_WORDS", tr("viral.err.noWords"), tr("viral.err.noWordsHint"))
        dwords, units, groups = build_units(words, scope, prm["max_s"])
        if not units:
            raise EngineError("NO_WORDS", tr("viral.err.noWordsScope"), tr("viral.err.noWordsScopeHint"))
        emit.done_note(tr("viral.note.sentences", n=len(units)))

    with emit.step("audio"):
        try:
            db = tl.envelope_on_timeline(emit=emit)
        except EngineError as e:  # energy is a bonus feature: never block on it
            emit.log(f"voice energy skipped: {e.msg}")
            db = None
        est = energy_stats(db)
        emit.progress(100)

    count = prm["count"] or auto_count(scope_dur, prm["target"])
    want = count + max(2, count // 2)
    terms = topic_terms(prm["topic"])
    stats = {"windows": 0, "ai_calls": 0, "cached": 0, "failed": 0, "resolve": {}, "dropped": 0, "rescored": 0}
    warnings = []
    model = prm["model"] or "grok-auto"
    source = "fallback"
    glossary = [g for g in (p.get("glossary") or setting("glossary", []) or []) if isinstance(g, str) and g][:30]
    whole = [(g[0], g[1]) for g in groups]

    with emit.step("find"):
        cands, failed = [], []
        if use_ai:
            cands, failed = ai_find(units, dwords, groups, prm, db, est, terms, count, model, glossary, emit, stats)
            if stats["failed"] == stats["windows"]:
                use_ai, ai_why = False, tr("viral.why.allFailed")
            elif failed:
                warnings.append(tr("viral.warn.partsFailed", n=len(failed), total=stats["windows"]))
                emit.warn(warnings[-1])
                source = "mixed"
            else:
                source = "ai"
        if not use_ai:
            warnings.append(tr("viral.warn.noAi", why=ai_why))
            emit.warn(warnings[-1])
            cands = rule_candidates(units, dwords, groups, whole, prm, db, est, terms)
        else:
            if failed:
                cands += rule_candidates(units, dwords, groups, failed, prm, db, est, terms)
            have = nms(cands)
            if len(have) < want:  # the model was strict: fill the pool with rule picks, the rescore judges them
                extra = [c for c in nms(rule_candidates(units, dwords, groups, whole, prm, db, est, terms))
                         if all(not overlaps(c, h) for h in have)]
                cands = have + extra[:want - len(have)]
                stats["rule_fill"] = len(cands) - len(have)
        emit.progress(100)
        emit.done_note(tr("viral.note.candidates", n=len(cands)))

    with emit.step("rank"):
        keep = nms(cands)[:min(MAX_RESCORE, want)]
        if use_ai and keep:
            rescore(keep, prm, model, glossary, emit, stats, warnings)
        keep = finish(keep)
        emit.progress(100)

    with emit.step("review"):
        if not keep:
            raise EngineError("NO_CLIPS", tr("viral.err.noClips", range=f"{prm['min_s']:.0f}-{prm['max_s']:.0f}"),
                              tr("viral.err.noClipsHint"))
        st = dict(stats, source=source if use_ai else "fallback", model=model if use_ai else None,
                  count=count, ms=int((time.time() - t_start) * 1000), candidates=len(keep))
        doc = RV.new("viral", review_items(keep, count), tl.duration, seq=tl, params=p, stats=st, fps=tl.fps,
                     source=st["source"], ai=bool(use_ai), warnings=warnings, tag=TAG,
                     settings={k: prm[k] for k in ("min_s", "max_s", "count", "preset", "topic")})
        doc["items"].sort(key=lambda it: (-it["score"], it["t0"]))   # best first (the panel can sort by time)
        path = RV.path_for(workdir(job, tl.name), "viral")
        old = read_json(path)
        if isinstance(old, dict) and old.get("tool") == "viral" and old.get("settings") == doc["settings"]:
            n = RV.carry_over(doc, old)
            if n:
                emit.log(f"{n} manual choices kept from the previous analysis")
        doc["stats"].update(RV.summary(doc))
        RV.save(doc, path)
        emit.progress(100)
    on = sum(1 for it in doc["items"] if it["on"])
    summary = tr("viral.sum.analyze", n=len(doc["items"]), on=on, best=doc["items"][0]["score"])
    if not use_ai:
        summary += tr("viral.sum.noAi")
    return {"review": str(path), "source": doc["source"], "count": count, "warnings": warnings,
            "summary": summary,
            "top": [{"t0": it["t0"], "t1": it["t1"], "score": it["score"], "title": it["title"]} for it in doc["items"][:3]]}


def apply(job, emit):
    """Edited review -> markers plan + sub-sequence list (best first, frame-snapped outwards)."""
    with emit.step("plan", tr("viral.stage.plan")):
        doc = RV.from_job(job)
        sel = sorted(RV.selected(doc), key=lambda it: (-_int(it.get("score")), it["t0"]))
        if not sel:
            raise EngineError("NO_CLIPS", tr("viral.err.noneSelected"), tr("viral.err.noneSelectedHint"))
        fps = 0.0
        seq = job.get("seq")
        if isinstance(seq, dict) and (seq.get("video") or seq.get("audio")):
            fps = Timeline.from_json(seq).fps
        fps = fps or float(doc.get("fps") or 0)
        clips, markers = [], []
        for k, it in enumerate(sel, 1):
            t0, t1 = float(it["t0"]), float(it["t1"])
            if fps > 0:
                t0, t1 = math.floor(t0 * fps + 1e-6) / fps, math.ceil(t1 * fps - 1e-6) / fps
            score = _int(it.get("score"))
            title = it.get("title") or it.get("label") or tr("viral.clipWord")
            sc = it.get("scores") or {}
            detail = ", ".join(f"{tr('viral.score.' + key)} {sc[key]}" for key in ("hook", "flow", "value", "trend", "energy")
                               if key in sc)
            comment = "\n".join(x for x in (
                tr("viral.marker.hook", hook=it["hook"]) if it.get("hook") else "",
                it.get("why") or "",
                tr("viral.marker.score", score=score) + (f" ({detail})" if detail else "")
                + ("" if it.get("src") == "ai" else tr("viral.marker.noAi")))
                if x)
            color = score_color(score)
            clips.append({"id": it["id"], "t0": round(t0, 6), "t1": round(t1, 6), "name": clip_name(k, title),
                          "title": title, "hook": it.get("hook", ""), "score": score, "rank": k, "color": color})
            markers.append({"t": round(t0, 6), "end": round(t1, 6), "name": f"Viral {k:02d} ({score}) {title}"[:120],
                            "comment": comment, "color": color, "type": "Comment"})
        total = sum(c["t1"] - c["t0"] for c in clips)
        emit.progress(100)
    return {"plan": {"kind": "markers", "tag": TAG, "markers": markers}, "clips": clips, "bin": BIN, "tag": TAG,
            "summary": tr("viral.sum.apply", n=len(clips), total=fmt_sec(total, 0))}


ACTIONS = {"analyze": analyze, "apply": apply}
