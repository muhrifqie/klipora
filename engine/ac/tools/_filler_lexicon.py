"""Indonesian filler lexicon in tiers + deterministic habit-word rules (helper of tools/fillers.py).

Port of proto/fillers/lexicon.py + context_rules.py (docs/research/fillers_repeat.md section 7). The leading
underscore keeps this module out of the tool registry (ac.tools.names() skips "_*").

Tiers
  always  pure hesitation sounds (eh, ehm, em, emm, eee, hmm, uh, um, ...): never carry meaning, cut by rule
  anu     "anu" (a filler word, but some speakers use it as "whatchamacallit"): own toggle
  habit   verbal tics (ya, oke, guys, gitu, kan, sih, nah, apa namanya, ...): per-word toggles, OFF by default,
          flagged only at phrase boundaries where an audio cut is clean (98 % precision, 84 % recall on 70
          hand-labelled hits). Grok is NOT used per word: identical calls gave different answers (17-86 %).
"""
from __future__ import annotations

# i18n-ignore-file: Indonesian speech data (lexicon, regexes, habit words) is transcript content, not UI text.

import re

PUNCT = ".,!?;:\"'“”‘’…()[]"

# Hesitation sounds as Whisper spells them: e, ee, eee, eh, ehm, em, emm, hmm, mm, um, uh, ah, eng, euh, anu
SAFE_RE = re.compile(r"^(e+|e+h+|(e+h*m+)+|h+m+|m{2,}|u+m+|u+h+|a+h+|a+m+|e+ng|e+u+h*|anu+|ng+|hu+m+)$")
ANU_RE = re.compile(r"^anu+$")

# What the panel lists under "Selalu" (the regex above covers their spelling variants too).
ALWAYS = ["eh", "ehm", "em", "emm", "eee", "hmm", "uh", "um"]

# Habit words: toggle key -> rule kind. Multi-word keys are searched as phrases.
OPEN = {"nah": "nah", "oke": "oke", "okey": "oke", "okay": "oke", "baik": "baik", "guys": "guys"}
CLOSE = {"ya": "ya", "gitu": "gitu", "kan": "kan", "sih": "sih", "deh": "deh", "nih": "nih", "tuh": "tuh",
         "dong": "dong", "lho": "lho", "loh": "lho", "guys": "guys"}
SEARCH = [["apa", "namanya"], ["apa", "ya"], ["apa", "sih"], ["gimana", "ya"], ["istilahnya"], ["pokoknya"]]
# A tag word right after these is part of a phrase ("kayak gitu", "apa ya"), never a tic.
NEVER_AFTER = {"apa", "kayak", "kaya"}
HABITS = ["ya", "oke", "guys", "gitu", "kan", "sih", "deh", "nah", "nih", "tuh", "dong", "lho", "baik",
          "apa namanya", "apa ya", "apa sih", "gimana ya", "istilahnya", "pokoknya"]
# Rule confidence = how sure we are this occurrence is a removable tic once the user says the word is one
# of their tics (research: 98 % precision at phrase boundaries). Glued to a neighbour word: -0.25.
HABIT_CONF = {"search": 0.8, "transition": 0.75, "tag": 0.7}


def norm(t):
    """'Eee,' -> 'eee'; ' E-mail' -> 'email' (hyphens dropped like the prototype)."""
    return str(t).strip().strip(PUNCT).lower().replace("-", "")


def is_safe(t):
    n = norm(t)
    return bool(n) and bool(SAFE_RE.match(n))


def tier(t):
    """'always' | 'anu' for hesitation tokens, else None."""
    n = norm(t)
    if not n or not SAFE_RE.match(n):
        return None
    return "anu" if ANU_RE.match(n) else "always"


def listener_hits(listener):
    """Indexes of hesitation tokens in the listener pass, skipping split words (' E' + '-mail': a measured
    false hit of the small model)."""
    out = []
    for i, w in enumerate(listener or []):
        nxt = listener[i + 1][0] if i + 1 < len(listener) else " "
        if is_safe(w[0]) and nxt[:1] in (" ", "") and not nxt.startswith("-"):
            out.append(i)
    return out


def _bounds(words, i, j, pause=0.25):
    """(phrase starts at word i, phrase ends after word j-1): a pause > 0.25 s, a Whisper segment end or
    punctuation counts as a boundary. words = v3 rows [text, start, end, seg_end, ...]."""
    before = words[i][1] - words[i - 1][2] if i > 0 else 9.0
    after = words[j][1] - words[j - 1][2] if j < len(words) else 9.0
    starts = i == 0 or before > pause or (len(words[i - 1]) > 3 and bool(words[i - 1][3])) \
        or str(words[i - 1][0]).strip()[-1:] in ".?!,"
    ends = j == len(words) or after > pause or (len(words[j - 1]) > 3 and bool(words[j - 1][3])) \
        or str(words[j - 1][0]).strip()[-1:] in ".?!,"
    return starts, ends


def habit_find(words, enabled):
    """Habit-word hits at phrase boundaries for the enabled toggle keys.
    -> [(i, j, kind, key)] word index span [i, j), kind transition|tag|search, key = toggle key."""
    on = set(enabled or ())
    if not on or not words:
        return []
    toks = [norm(w[0]) for w in words]
    n, out, i = len(words), [], 0
    while i < n:
        hit = None
        for p in SEARCH:
            key = " ".join(p)
            if key in on and toks[i:i + len(p)] == p and i + len(p) < n and _bounds(words, i, i + len(p))[0]:
                hit = (i, i + len(p), "search", key)
                break
        t = toks[i]
        if not hit and t in OPEN and OPEN[t] in on and i + 1 < n and _bounds(words, i, i + 1)[0]:
            nxt = toks[i + 1]
            if nxt not in OPEN:
                hit = (i, i + 1, "transition", OPEN[t])
            elif nxt == "nah" and OPEN[t] in ("oke", "baik") and "nah" in on and i + 2 < n:
                hit = (i, i + 2, "transition", OPEN[t])  # "oke nah" opening a phrase
            # else "oke oke" / "nah oke": a stutter or a run of openers, left to the repeat tool
        if not hit and t in CLOSE and CLOSE[t] in on and i > 0 and toks[i - 1] not in NEVER_AFTER:
            if t == "gitu" and i + 1 < n and toks[i + 1] == "ya":
                if "ya" in on and _bounds(words, i, i + 2)[1]:
                    hit = (i, i + 2, "tag", "gitu")      # "gitu ya" closing a phrase
                # else: "gitu" is not at the boundary; "ya" alone is checked on the next word
            elif _bounds(words, i, i + 1)[1]:
                hit = (i, i + 1, "tag", CLOSE[t])
        if hit:
            out.append(hit)
            i = hit[1]
        else:
            i += 1
    return out


if __name__ == "__main__":
    for t in ["Eee,", " emm", "Hmm...", "eh", "anu", "ehm", "Em.", "ya", "e-e", "mm", "uh", "makan", " E"]:
        print(repr(t), tier(t))
