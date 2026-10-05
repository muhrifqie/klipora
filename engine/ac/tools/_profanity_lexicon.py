"""Profanity lexicon for the "Sensor Kata Kasar" tool (Indonesian + regional + English) and token matching.

Tiers (docs/research/product_ai.md section 4.3):
  3 = kasar   always censored (Longgar, Normal, Ketat)
  2 = sedang  censored at Normal and Ketat
  1 = ringan  censored only at Ketat
  0 = ambigu  a common literal meaning exists (anjing = dog, tahi lalat = mole); decided by context
              (AI 3-vote when available, else the rule cues below), at every level.

Matching works on normalized tokens: lowercase, leet only when letters and digits are mixed (b4ngsat),
non-letters dropped (Whisper's own '*' kept), letter runs collapsed (anjiiing -> anjing, cokkk -> cok), then
the token itself, the token minus one suffix (nya|lah|kah|an|in|mu|ku|lo|lu) and minus one prefix (ng|di|ke)
are looked up. Whisper sometimes self-masks ("f***", "anj*ng"): a token with '*' and >= 3 chars is a hit,
resolved to a lexicon word of the same shape when possible (checked BEFORE collapsing letters).
Underscore module name: ac.tools lists only modules without a leading underscore as tools.
"""
# i18n-ignore-file: lexicon data (Indonesian + English swear words), not UI text.
from __future__ import annotations

import re

TIER_NAMES = {3: "kasar", 2: "sedang", 1: "ringan", 0: "ambigu"}
LEVEL_MIN = {"longgar": 3, "normal": 2, "ketat": 1}

LEXICON = {
    # Indonesian, strong / sexual
    "kontol": 3, "memek": 3, "ngentot": 3, "entot": 3, "ngewe": 3, "jembut": 3, "pepek": 3, "peler": 3,
    "coli": 3, "colmek": 3, "itil": 3, "ngaceng": 3, "lonte": 3, "perek": 3, "pelacur": 3, "sundal": 3,
    "bangsat": 3, "bajingan": 3, "keparat": 3, "kampang": 3, "jablay": 2, "ngehe": 2,
    # regional (Javanese, Sundanese, Minang, Medan/Malay, Makassar)
    "jancok": 3, "jancuk": 3, "dancok": 3, "dancuk": 3, "cok": 2, "cuk": 2, "asu": 0, "tempik": 3,
    "gathel": 3, "matamu": 2, "ndasmu": 2, "raimu": 2, "pantek": 3, "puki": 3, "pukimak": 3, "kimak": 3,
    "kehed": 2, "goblog": 2, "belegug": 2, "celeng": 0, "telaso": 3, "sundala": 3,
    # insults
    "goblok": 2, "tolol": 2, "brengsek": 2, "bedebah": 2, "bacot": 2, "bego": 1, "dongo": 1, "idiot": 1,
    "geblek": 1, "kampret": 1, "sialan": 1, "kunyuk": 1, "budeg": 1, "mampus": 1, "bangke": 0, "bangkai": 0,
    # euphemisms of "anjing"
    "anjir": 1, "anjrit": 1, "anjay": 1, "anjim": 1, "anjrot": 1, "anjas": 1, "njir": 1, "njing": 2,
    # chat spellings (Whisper rarely writes them, captions/manual text might)
    "anjg": 2, "ajg": 2, "asw": 2, "bgst": 3, "kntl": 3,
    # ambiguous: literal meaning common in tutorials/vlogs
    "anjing": 0, "babi": 0, "monyet": 0, "setan": 0, "tai": 0, "taik": 2, "tahi": 0, "iblis": 0,
    # English
    "fuck": 3, "fucking": 3, "fucker": 3, "fucked": 3, "motherfucker": 3, "motherfucking": 3, "fakyu": 3,
    "shit": 2, "shitty": 2, "bullshit": 2, "bitch": 3, "asshole": 3, "cunt": 3, "slut": 3, "whore": 3,
    "bastard": 2, "goddamn": 2, "dumbass": 2, "wtf": 2, "damn": 1, "crap": 1,
    "dick": 0, "pussy": 0, "cock": 0,
}

# Built-in literal phrases (normalized tokens): never a hit. The user's allow list adds to these.
LITERAL_PHRASES = [
    "tahi lalat", "tai lalat", "anak anjing", "anjing laut", "anjing kampung", "anjing liar", "anjing pelacak",
    "anjing peliharaan", "babi hutan", "babi guling", "babi panggang", "babi kecap", "daging babi", "sate babi",
    "minyak babi", "lemak babi", "monyet ekor panjang", "setan merah", "film setan", "hantu setan",
    "asu gede", "dick grayson", "moby dick", "cock pit",
]

# Context cues for ambiguous words when the AI check is off or fails (rules decide; the user reviews).
INSULT_BEFORE = {"dasar", "lu", "lo", "elu", "loe", "kau", "kamu", "kalian", "ente", "kowe", "koen", "dancok"}
INSULT_AFTER = {"lu", "lo", "loe", "elu", "kau", "kamu", "kalian", "ente", "kowe", "koen", "kon", "banget",
                "bgt", "emang", "memang", "bener", "beneran", "amat", "tenan", "lah", "sih", "deh", "dah",
                "sialan", "bangsat", "kampret"}
LITERAL_NEAR = {  # within 3 tokens: the word is probably used literally
    "si", "seekor", "ekor", "anak", "daging", "sate", "kandang", "peliharaan", "piara", "pelihara",
    "film", "horor", "hantu", "ngusir", "usir", "pengusir", "ritual", "gambar", "foto", "boneka", "patung",
    "kebun", "binatang", "hewan", "lemak", "minyak", "tulang", "bulu", "kotoran", "pup", "masak", "masakan",
    "goreng", "panggang", "guling", "kecap", "rica", "jual", "beli", "ternak", "peternakan", "kucing", "ayam",
    "sapi", "kambing", "lalat", "hutan", "laut", "liar", "lepas", "kampung", "gonggong", "menggonggong",
    "galak", "lucu", "jinak", "komik", "kartun", "dokter", "ada", "punya", "pisang", "makan", "saya",
}

SUFFIX = re.compile(r"(nya|lah|kah|an|in|mu|ku|lo|lu)$")
PREFIX = re.compile(r"^(ng|di|ke)")
_LEET = str.maketrans({"4": "a", "@": "a", "1": "i", "!": "i", "0": "o", "3": "e", "5": "s", "$": "s", "7": "t"})
_HAS_ALPHA = re.compile(r"[a-z]")
_HAS_DIGIT = re.compile(r"[0-9]")
_ELONGATED = re.compile(r"([a-z])\1{2,}")


def clean(tok):
    """Lowercase, leet (only for mixed letter+digit tokens, so '741' never becomes 'tai'), keep [a-z*]."""
    t = str(tok or "").lower().strip()
    if _HAS_ALPHA.search(t) and (_HAS_DIGIT.search(t) or "@" in t or "$" in t):
        t = t.translate(_LEET)
    return re.sub(r"[^a-z*]", "", t)


def norm(tok):
    """Normalized token for lexicon and list lookups ('' for punctuation / numbers)."""
    t = clean(tok)
    if "*" in t:                       # Whisper's own masking: keep the shape for lookup()
        return t
    return re.sub(r"(.)\1{2,}", r"\1", re.sub(r"(.)\1+$", r"\1", t))


def elongated(tok):
    """'Anjiiing' / 'bangsaaat': emotional stretching, a strong exclamation cue."""
    return bool(_ELONGATED.search(clean(tok)))


def variants(n):
    """Lookup candidates of a normalized token: itself, minus a suffix, minus a prefix (in that order)."""
    out = [n]
    for c in (SUFFIX.sub("", n), PREFIX.sub("", n)):
        if c and c != n and len(c) >= 2 and c not in out:
            out.append(c)
    return out


def _masked(t):
    """'f***' -> ('fuck', 3) when one lexicon word has that shape, else (t, 2)."""
    rx = re.compile("^" + re.escape(t).replace(r"\*", "[a-z]") + "$")
    cands = sorted((w for w in LEXICON if rx.match(w)), key=lambda w: -LEXICON[w])
    if cands:
        return cands[0], max(2, LEXICON[cands[0]])
    return t, 2


def lookup(tok):
    """(lemma, tier, masked) for a profanity token, else None."""
    t = norm(tok)
    if not t:
        return None
    if "*" in t:
        if len(t) >= 3 and _HAS_ALPHA.search(t):
            lemma, tier = _masked(t)
            return lemma, tier, True
        return None
    for c in variants(t):
        if c in LEXICON:
            return c, LEXICON[c], False
    if t.startswith("fuck") or t.startswith("motherfuck"):
        return "fuck", 3, False
    return None


def mask_text(word, style="stars"):
    """Caption masking that keeps Whisper's leading space and punctuation:
    ' kontol,' -> ' k****l,' (stars) | ' ******,' (full) | ' [bip],' (bip). Already masked text is kept."""
    word = str(word or "")
    m = re.match(r"^(\W*)(.*?)(\W*)$", word, re.S)
    lead, w, tail = m.group(1), m.group(2), m.group(3)
    if not w or style in (None, "", "off"):
        return word
    if "*" in w and style != "bip":
        return word
    if style == "full":
        masked = "*" * len(w)
    elif style == "bip":
        masked = "[bip]"
    else:
        masked = (w[0] + "*" * (len(w) - 2) + w[-1]) if len(w) > 3 else (w[0] + "*" * (len(w) - 1))
    return lead + masked + tail


def phrase_tokens(text):
    """User list entry -> normalized tokens ('Dasar  Kampret!' -> ['dasar', 'kampret'])."""
    return [n for n in (norm(x) for x in str(text or "").split()) if n]


def tokens_match(entry, toks, lemmas):
    """Does a list entry (normalized tokens) match the token run? Each position may match the
    normalized token, any of its variants, or its lexicon lemma."""
    if len(entry) != len(toks):
        return False
    for e, t, lem in zip(entry, toks, lemmas):
        if e != t and e != lem and e not in variants(t):
            return False
    return True
