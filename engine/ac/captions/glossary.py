"""Deterministic Indonesian caption text rules (no AI): fuzzy glossary, filler detection, number/Rupiah
formatting, line-break rules and profanity masking. Port of docs/research/proto_product_captions/text_rules.py.

Every rule works on lists of word dicts {"text", ...} and returns *span fixes* instead of rewriting the list:
    {"i": first index, "n": words spanned, "text": new display text, "kind": "glossary" | "number"}
so the captions document keeps one entry per Whisper word (stable ids, original timing).
"""
from __future__ import annotations

import difflib
import re

FILLERS = {"eh", "ehm", "em", "emm", "hmm", "hm", "ee", "eee", "uh", "um", "mm", "umm", "eem", "aa"}
# A line/page should not END on these words (they belong to the next one).
NO_END = set("di ke dari yang dan atau untuk dengan pada dalam oleh si sang para kalau kalo karena agar supaya "  # i18n-ignore
             "biar tapi tetapi namun jadi lalu terus serta maupun bahwa sebagai seperti ada nih sama buat".split())  # i18n-ignore
_TRAIL = ",.?!;:"


def norm(s):
    return re.sub(r"[^\w]", "", str(s).lower())


_PHON = (("oe", "u"), ("dj", "j"), ("tj", "c"), ("sj", "sy"), ("nj", "ny"), ("ph", "f"), ("q", "k"), ("x", "ks"))


def phon(s):
    """Loose Indonesian sound-alike key: old spelling (oe -> u, dj -> j, tj -> c) so 'Djoeragan' ~ 'juragan'."""
    s = norm(s)
    for a, b in _PHON:
        s = s.replace(a, b)
    return s


def ratio(a, b):
    return difflib.SequenceMatcher(a=a, b=b).ratio()


def break_ok(word_text):
    """True if a line/page may end after this word."""
    return norm(word_text) not in NO_END


def is_filler(word_text):
    return norm(word_text) in FILLERS


def _tail(text):
    t = text.rstrip()
    core = t.rstrip(_TRAIL)
    return t[len(core):]


def glossary_fixes(words, glossary, thresh=0.75, max_n=3):
    """Span fixes for 1..max_n word windows whose letters look like a glossary term (timing-safe).

    1 word : case-insensitive exact -> recase (only when the term has capitals, so a sentence-initial
             "Dashboard" is not lowercased); else ratio >= 0.8, same first letter, length diff <= 1
             (so "seller" is NOT turned into "reseller").
    2-3 words: only when the term is spread over the words ("toko kitah" -> "TokoKita"): no edge function
             word / filler, and no single word of the window already resembles the term (< 0.7).
    """
    terms = [(g.strip(), norm(g), phon(g)) for g in glossary or [] if norm(g)]
    if not terms:
        return []
    texts = [norm(w["text"]) for w in words]
    phons = [phon(w["text"]) for w in words]
    out, i = [], 0
    while i < len(words):
        best = None
        for n in range(1, max_n + 1):
            if i + n > len(words):
                break
            win = texts[i:i + n]
            if not all(win):
                break
            if n > 1 and (win[0] in NO_END or win[-1] in NO_END or win[0] in FILLERS):
                continue
            joined = "".join(win)
            jp = "".join(phons[i:i + n])
            for g, gn, gp in terms:
                if n == 1:
                    exact = joined == gn
                    if exact and g == g.lower():
                        continue          # same letters, term has no capitals: nothing to recase
                    r = 1.0 if exact or jp == gp else ratio(jp, gp)
                    ok = r >= 1.0 or (jp[:1] == gp[:1] and abs(len(jp) - len(gp)) <= 1 and r >= 0.8)
                else:
                    if abs(len(jp) - len(gp)) > max(2, len(gp) // 3) or gn in win:
                        continue
                    r = ratio(jp, gp)
                    # the window must explain the term better than any single word of it ("ke dashboard" stays)
                    ok = r >= thresh and r >= max(ratio(x, gp) for x in phons[i:i + n]) + 0.02
                if ok and (best is None or r > best[0]):
                    best = (r, n, g)
        if best:
            r, n, g = best
            cur = " ".join(w["text"].strip() for w in words[i:i + n])
            new = g + _tail(words[i + n - 1]["text"])
            if cur.rstrip(_TRAIL) != g:
                out.append({"i": i, "n": n, "text": new, "kind": "glossary", "old": cur})
            i += n
        else:
            i += 1
    return out


_NUM = re.compile(r"^\d+([.,]\d+)?$")
_MULT = {"ribu": 1000, "rb": 1000, "juta": 1_000_000, "jt": 1_000_000, "miliar": 1_000_000_000}


def _fmt_id(n):
    return f"{n:,}".replace(",", ".")


def number_fixes(words):
    """'50 ribu' -> '50.000', '2 juta' -> '2.000.000', 'Rp15.000' -> 'Rp 15.000' (span fixes)."""
    out, i = [], 0
    while i < len(words):
        t = words[i]["text"].strip()
        core = t.rstrip(_TRAIL)
        m = re.match(r"^Rp\.?(\d[\d.,]*)$", core)
        if m:
            out.append({"i": i, "n": 1, "text": "Rp " + m.group(1) + _tail(t), "kind": "number", "old": t})
            i += 1
            continue
        if _NUM.match(core) and i + 1 < len(words):
            nxt = words[i + 1]["text"].strip()
            mult = _MULT.get(nxt.rstrip(_TRAIL).lower())
            if mult and not core.endswith(_TRAIL):
                try:
                    val = float(core.replace(",", "."))
                except ValueError:
                    val = None
                if val is not None and val * mult == int(val * mult):
                    out.append({"i": i, "n": 2, "text": _fmt_id(int(val * mult)) + _tail(nxt), "kind": "number",
                                "old": f"{t} {nxt}"})
                    i += 2
                    continue
        i += 1
    return out


def merge_fixes(*lists):
    """Combine fix lists; earlier lists win on overlapping spans."""
    taken, out = set(), []
    for lst in lists:
        for f in lst:
            span = set(range(f["i"], f["i"] + f["n"]))
            if span & taken:
                continue
            taken |= span
            out.append(f)
    return sorted(out, key=lambda f: f["i"])


# ---------------------------------------------------------------- profanity

# Small built-in fallback list (used only when ac.tools.profanity is not available). Tier 1 = always masked.
PROFANITY_ID = {"anjing", "anjir", "anjrit", "bangsat", "babi", "kontol", "memek", "ngentot", "goblok", "tolol",
                "bajingan", "brengsek", "kampret", "asu", "jancok", "jancuk", "fuck", "shit", "bitch", "tai",
                "keparat", "bego", "pantek", "titit", "pepek"}


def mask(text, mode="stars"):
    """Censor display text. modes: stars ('a****g'), full ('******'), first ('a*****'), bleep ('[sensor]')."""
    t = text.strip()
    core = t.rstrip(_TRAIL)
    tail = t[len(core):]
    if len(core) <= 1:
        return t
    if mode == "full":
        m = "*" * len(core)
    elif mode == "first":
        m = core[0] + "*" * (len(core) - 1)
    elif mode == "bleep":
        m = "[sensor]"   # caption content (part of the doc hash), the same in every UI language
    else:
        m = core[0] + "*" * (len(core) - 2) + core[-1] if len(core) > 2 else core[0] + "*"
    return m + tail
