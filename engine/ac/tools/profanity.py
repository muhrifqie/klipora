"""Sensor Kata Kasar / Bleep Profanity (tool id "profanity"): find swear words in the transcript, let the user
review them, then censor them NON-DESTRUCTIVELY in a clone of the sequence (volume keyframes with 10 ms ramps on
the dialog clips + tone clips on a new audio track "Klipora Sensor"; the old "AutoCut Sensor" is still recognised), and share
the decisions with captions (hits_for_timeline).

Actions (job protocol, docs/SPEC.md section 2):
  analyze  timeline words (v3 cache / Whisper) + listener pass (Whisper tends to "clean up" swears: the 35 min
           test file has "anjir" only in the listener pass) -> tiered lexicon + the user's lists -> edges moved to
           the quiet point within +-120 ms -> AI 3-vote only for ambiguous (literal-able) words, rule cues when
           the AI is off -> review file
  apply    edited review + mode -> plan {"kind": "censor", ...} applied by panel/host/33_profanity.jsx; also
           stores the decisions next to the media (captions masking, `hits_for_timeline`)
  preview  short censored WAV around one hit (the "Dengar" button)
  hits     hits_for_timeline() for the captions tool / panel
  library  get/set the user's word lists (%APPDATA%\\Klipora\\profanity.json) + the built-in lexicon

User-facing text goes through ac.i18n.tr (keys profanity.*, Indonesian + English, per job language).

Research and measurements: docs/research/product_ai.md section 4. Tool doc: docs/tools/profanity.md.
"""
from __future__ import annotations

import re
import time
from pathlib import Path

from .. import ranges as R
from .. import review as RV
from ..i18n import tr
from ..timeline import Timeline
from ..util import EngineError, appdata_dir, cache_path, file_hash, now_iso, read_json, setting, workdir, write_json
from . import _profanity_lexicon as LX
from . import _profanity_sfx as SFX

TITLE = "Bleep Profanity"        # developer listing (cli.py tools); UI text: tr("tool.profanity.title")
DESCRIPTION = "Find swear words in the transcript, review them, then beep or mute them in a new sequence."

SENSOR_TRACK = "Klipora Sensor"
SENSOR_TRACKS = (SENSOR_TRACK, "AutoCut Sensor")     # our sensor track, incl. the name used before the rename
TAG = "[Klipora-PF]"                                  # old projects: "[AC-PF]" (the host recognises both)
SEQ_SUFFIX = " (Klipora)"
MODES = ("beep", "mute", "duck", "custom")            # labels: tr("profanity.mode.<id>")
STYLES = ("stars", "full", "bip", "off")
LEVELS = tuple(LX.LEVEL_MIN)
LIB_V = 1
DEC_V = 1
PROMPT_V = "pf-2026-10-05"
RAMP = 0.01              # volume key ramp on each side (seconds)
MERGE_GAP = 0.12         # censor ranges closer than this become one range (one tone, no 2-key flicker)
AI_BATCH = 40            # ambiguous items per AI call (3 parallel votes per batch)
AI_MAX = 120             # more ambiguous items than this: the rest are decided by rules (quota)


def tier_label(tier):
    return tr(f"profanity.tier{tier}")


def mode_label(mode):
    return tr(f"profanity.mode.{mode}")


def is_sensor_track(name):
    """Our own sensor track ("Klipora Sensor", or "AutoCut Sensor" from before the rename)."""
    return (name or "").strip() in SENSOR_TRACKS


# ================================================================ word library (shared with the panel editor)

def library_path():
    """%APPDATA%\\Klipora\\profanity.json (migrated from AutoCutBOT by util.appdata_dir),
    written by the panel's word list editor."""
    return appdata_dir() / "profanity.json"


def _clean_entries(xs, limit=500):
    out = []
    for x in xs if isinstance(xs, (list, tuple)) else []:
        s = re.sub(r"\s+", " ", str(x or "")).strip().lower()[:60]
        if s and LX.phrase_tokens(s) and s not in out:
            out.append(s)
    return out[:limit]


def normalize_library(lib):
    """{"v", "block": [...], "allow": [...], "captionStyle", "updated"}; an entry in both lists stays blocked."""
    lib = lib if isinstance(lib, dict) else {}
    block = _clean_entries(lib.get("block"))
    allow = [a for a in _clean_entries(lib.get("allow")) if a not in block]
    style = lib.get("captionStyle") if lib.get("captionStyle") in STYLES else "stars"
    return {"v": LIB_V, "block": block, "allow": allow, "captionStyle": style, "updated": lib.get("updated")}


def load_library():
    return normalize_library(read_json(library_path(), {}))


def save_library(lib):
    lib = normalize_library(lib)
    lib["updated"] = now_iso()
    write_json(library_path(), lib, indent=1)
    return lib


def builtin_lexicon():
    """{tier: [words]} of the built-in lexicon + the literal phrases (for the panel's "Kata bawaan")."""
    out = {str(t): sorted(w for w, k in LX.LEXICON.items() if k == t) for t in (3, 2, 1, 0)}
    out["literal"] = list(LX.LITERAL_PHRASES)
    return out


# ================================================================ detection

class _Index:
    """List entries (phrases) indexed by every form their first token can match, for fast run matching."""

    def __init__(self, entries):
        self.by_first = {}
        for e in entries:
            toks = LX.phrase_tokens(e)
            if toks:
                self.by_first.setdefault(toks[0], []).append((toks, e))
        for v in self.by_first.values():
            v.sort(key=lambda x: -len(x[0]))

    def runs(self, toks, lems):
        """Non-overlapping matches [(i, k, entry)] (longest entry first at each position)."""
        out, i, n = [], 0, len(toks)
        while i < n:
            keys = set(LX.variants(toks[i])) if toks[i] else set()
            if lems[i]:
                keys.add(lems[i])
            best = None
            for key in keys:
                for et, e in self.by_first.get(key, ()):
                    k = len(et)
                    if i + k <= n and (best is None or k > best[1]) and LX.tokens_match(et, toks[i:i + k],
                                                                                       lems[i:i + k]):
                        best = (i, k, e)
            if best:
                out.append(best)
                i += best[1]
            else:
                i += 1
        return out


def _tokens(words):
    toks = [LX.norm(w["text"]) for w in words]
    looks = [LX.lookup(w["text"]) for w in words]
    return toks, looks, [lk[0] if lk else None for lk in looks]


def detect(words, level="normal", block=(), allow=()):
    """Lexicon + user lists over word dicts ({"text", ...}, in time order). Priority: the user's allow list >
    the user's block list > built-in literal phrases > lexicon.
    -> (hits, below): hits = [{"i0", "i1" (inclusive word indexes), "lemma", "tier", "masked", "src"}],
    below = lexicon hits under the level's minimum tier (not listed; the panel suggests "Ketat")."""
    lo = LX.LEVEL_MIN.get(level, 2)
    toks, looks, lems = _tokens(words)
    n = len(words)
    user_ok, lit_ok, taken = [False] * n, [False] * n, [False] * n
    for i, k, _ in _Index(allow).runs(toks, lems):
        user_ok[i:i + k] = [True] * k
    for i, k, _ in _Index(LX.LITERAL_PHRASES).runs(toks, lems):
        lit_ok[i:i + k] = [True] * k
    hits, below = [], 0
    for i, k, e in _Index(block).runs(toks, lems):
        if any(user_ok[i:i + k]):
            continue
        hits.append({"i0": i, "i1": i + k - 1, "lemma": e, "tier": 3, "masked": False, "src": "user"})
        taken[i:i + k] = [True] * k
    for i in range(n):
        if taken[i] or user_ok[i] or lit_ok[i] or not looks[i]:
            continue
        lemma, tier, masked = looks[i]
        if tier == 0 or tier >= lo:
            hits.append({"i0": i, "i1": i, "lemma": lemma, "tier": tier, "masked": masked, "src": "lexicon"})
        else:
            below += 1
    hits.sort(key=lambda h: h["i0"])
    return hits, below


def _segment(words, i0, i1, span=15):
    """Whisper segment around words[i0..i1] (seg=1 marks a segment's last word), at most `span` words each way."""
    a = i0
    while a > 0 and not words[a - 1].get("seg") and i0 - a < span:
        a -= 1
    b = i1
    while b < len(words) - 1 and not words[b].get("seg") and b - i1 < span:
        b += 1
    return a, b


_PUNCT_END = re.compile(r"[,.!?;:]\W*$")


def _clause(words, i0, i1, a, b):
    """Clause bounds inside the segment [a, b]: punctuation at the end of a token closes a clause."""
    c0 = i0
    while c0 > a and not _PUNCT_END.search(words[c0 - 1]["text"].strip()) and i0 - c0 < 8:
        c0 -= 1
    c1 = i1
    while c1 < b and not _PUNCT_END.search(words[c1]["text"].strip()) and c1 - i1 < 8:
        c1 += 1
    return c0, c1


def rule_decide(words, i0, i1):
    """Swear or literal for an ambiguous word without AI: context cues in its clause (Indonesian speech).
    -> (is_swear, reason). Tuned on the research's 23 sentences (all correct); the user still reviews."""
    a, b = _segment(words, i0, i1)
    c0, c1 = _clause(words, i0, i1, a, b)
    raw = words[i1]["text"].strip()
    first = words[i0]["text"].strip()
    prev = LX.norm(words[i0 - 1]["text"]) if i0 - 1 >= c0 else None
    nxt = LX.norm(words[i1 + 1]["text"]) if i1 + 1 <= c1 else None
    insult, lit, why = 0, 0, []
    if prev == "dasar":
        insult += 3
        why.append(tr("profanity.why.before", w="dasar"))
    elif prev in LX.INSULT_BEFORE:
        insult += 2
        why.append(tr("profanity.why.before", w=prev))
    if nxt in LX.INSULT_AFTER:
        insult += 2
        why.append(tr("profanity.why.after", w=nxt))
    if LX.elongated(first):
        insult += 2
        why.append(tr("profanity.why.long"))
    if raw.endswith("!"):
        insult += 2
        why.append(tr("profanity.why.exclaim"))
    if i0 == a and i1 < b and re.search(r"[,.!]\W*$", raw):
        insult += 2
        why.append(tr("profanity.why.exclaimStart"))
    if i0 == a and i1 == b:
        insult += 2
        why.append(tr("profanity.why.alone"))
    for j in (i0 - 1, i1 + 1):
        if c0 <= j <= c1 and j not in range(i0, i1 + 1):
            lk = LX.lookup(words[j]["text"])
            if lk and lk[1] >= 1:
                insult += 1
                why.append(tr("profanity.why.nearSwear"))
                break
    near = 0
    for j in range(max(c0, i0 - 3), min(c1, i1 + 3) + 1):
        if i0 <= j <= i1:
            continue
        if any(v in LX.LITERAL_NEAR for v in LX.variants(LX.norm(words[j]["text"]))):
            near += 2 if j in (i0 - 1, i1 + 1) else 1
    if near:
        lit += min(4, near)
        why.append(tr("profanity.why.literal"))
    nx_raw = words[i1 + 1]["text"].strip() if i1 + 1 <= c1 else ""
    if first[:1].isupper() and nx_raw[:1].isupper() and nxt not in LX.INSULT_AFTER:
        lit += 2
        why.append(tr("profanity.why.name"))
    if LX.norm(first).endswith("nya"):         # possessive: "setannya", "tai-nya" (literal more often)
        lit += 1
    return insult > lit, ", ".join(why) or tr("profanity.why.none")


# ================================================================ AI context check (ambiguous words only)

_AI_SYSTEM = (                     # AI prompt: content, not UI text (stays Indonesian)
    "Kamu moderator konten Indonesia. Untuk tiap item, tentukan apakah KATA di KALIMAT itu dipakai sebagai "  # i18n-ignore (AI prompt)
    "makian/umpatan/hinaan/seruan kesal, atau makna harfiah (hewan, makanan, tahi lalat, makhluk di film).\n"  # i18n-ignore (AI prompt)
    "Contoh: 'anjing saya lucu' -> harfiah. 'anjing lu!' -> makian. 'setan banget bug ini' -> makian. "  # i18n-ignore (AI prompt)
    "'film setannya serem' -> harfiah. 'masak babi panggang' -> harfiah. 'dasar babi' -> makian.\n"  # i18n-ignore (AI prompt)
    "Isi 'arti' dulu (3-6 kata), baru 'sensor'. Nilai tiap item sendiri-sendiri.\n"  # i18n-ignore (AI prompt)
    'Balas JSON: {"r":[{"id":0,"kata":"...","arti":"...","sensor":true}]} untuk SEMUA id.')  # i18n-ignore (AI prompt)


def _ai_check(obj):
    if not isinstance(obj, dict) or not isinstance(obj.get("r"), list):
        return ['expected {"r": [...]}']
    return []


def ai_votes(items, emit=None, model=None):
    """3 parallel votes (censor when >= 2 agree), MEASURED 35/36 held-out decisions in ~7 s
    (product_ai.md 4.3). items = [{"kata", "kalimat"}]. A vote counts only when its echoed `kata` matches.
    -> ([{"votes", "of", "reason"}] aligned with items, source "ai"|"cache"). Raises ai.AIError when the AI
    cannot be used (the caller falls back to rule_decide). Cached so a re-scan costs no quota."""
    from ..ai import client as ai
    if not items:
        return [], "none"
    if ai.config()["disabled"]:                 # AI switched off: rules, even when an old AI answer is cached
        raise ai.AIUnavailable(tr("profanity.aiOff"))
    model = model or setting("aiModel") or "grok-fast"
    store = ai.Cache()
    key = store.key("profanity", PROMPT_V, model, [[it["kata"], it["kalimat"]] for it in items])
    hit = store.get(key)
    if hit and isinstance(hit.get("votes"), list) and len(hit["votes"]) == len(items):
        return hit["votes"], "cache"
    if not ai.available():
        raise ai.AIUnavailable(tr("profanity.aiNoProxy"))
    out = []
    for c0 in range(0, len(items), AI_BATCH):
        part = items[c0:c0 + AI_BATCH]
        lines = "\n".join(f'id={i} kata="{it["kata"]}" kalimat="{it["kalimat"]}"' for i, it in enumerate(part))  # i18n-ignore (AI prompt)
        msgs = [{"role": "system", "content": _AI_SYSTEM}, {"role": "user", "content": lines}]
        res = ai.map_parallel(lambda _: ai.chat_json(msgs, model=model, tag="profanity", retries=0, timeout=90,
                                                     check=_ai_check), range(3))
        good = [r for r in res if not isinstance(r, Exception)]
        if len(good) < 2:
            bad = [r for r in res if isinstance(r, Exception)]
            raise bad[0] if bad else ai.AIError(tr("profanity.aiNoAnswer"))
        votes, why_yes, why_no = [0] * len(part), [""] * len(part), [""] * len(part)
        for data in good:
            seen = set()
            for r in data.get("r", []):
                try:
                    i = int(r.get("id", -1))
                except (TypeError, ValueError):
                    continue
                if not 0 <= i < len(part) or i in seen:
                    continue
                said = str(r.get("kata", "")).lower().strip(" ,.!?\"'")
                if not said or (said not in part[i]["kata"].lower() and part[i]["kata"].lower() not in said):
                    continue
                seen.add(i)
                yes = r.get("sensor") is True or str(r.get("sensor")).lower() == "true"
                votes[i] += 1 if yes else 0
                bucket = why_yes if yes else why_no
                if not bucket[i]:
                    bucket[i] = str(r.get("arti", "")).strip()[:80]
        need = 2 if len(good) >= 3 else len(good)
        out += [{"votes": v, "of": len(good), "reason": why_yes[i] if v >= need else why_no[i]}
                for i, v in enumerate(votes)]
        if emit:
            emit.progress(100.0 * min(len(items), c0 + AI_BATCH) / len(items))
    if all(o["of"] == 3 for o in out):
        store.put(key, {"votes": out, "model": model, "at": now_iso()})
    return out, "ai"


# ================================================================ edges

def refine(t0, t1, db, wide=False, pad=0.02, min_len=0.25, hop=0.01):
    """Censor range for a word whose Whisper edges are t0..t1 (sequence seconds). Whisper edges wobble ~0.1 s
    (listener pass +-0.2 s, wide=True): each edge moves to the quiet frame NEAREST the word inside +-search
    ("quiet" = within 3 dB of the window minimum or 24 dB under the word's peak), then `pad` is added and the
    range grows to `min_len` around its centre. Never "extend while loud" (eats neighbours in fluent speech)."""
    import numpy as np
    out_s, in_s = (0.2, 0.08) if wide else (0.12, 0.04)
    n = len(db)

    def fr(t):
        return min(n, max(0, int(round(t / hop))))

    core = db[fr(t0):max(fr(t0) + 1, fr(t1))]
    peak = float(core.max()) if len(core) else -90.0
    s, e = t0, t1
    if peak > -80:
        def edge(a, b, last):
            i, j = fr(a), min(n, fr(b) + 1)
            if j <= i:
                return None
            seg = np.asarray(db[i:j])
            q = max(float(seg.min()) + 3.0, peak - 24.0)
            ks = np.flatnonzero(seg <= q)
            return (i + int(ks[-1] if last else ks[0])) * hop
        es, ee = edge(t0 - out_s, t0 + in_s, True), edge(t1 - in_s, t1 + out_s, False)
        s = es if es is not None else t0
        e = ee + hop if ee is not None else t1
    s, e = s - pad, e + pad
    if e - s < min_len:
        c = (s + e) / 2
        s, e = c - min_len / 2, c + min_len / 2
    return round(max(0.0, s), 3), round(e, 3)


# ================================================================ timeline helpers

def dialog_tracks(tl):
    """Audio track indexes to scan and to key: everything except our own sensor track ("Klipora Sensor", or
    "AutoCut Sensor" from before the rename)."""
    return [t.index for t in tl.audio if not is_sensor_track(t.name)]


def _clip_of(tl, w):
    for c in tl.audio_clips(include_muted=True, include_disabled=True):
        if c.track == w.get("track") and c.index == w.get("clip"):
            return c
    return None


def listener_on_timeline(tl, tracks, compute=True, emit=None):
    """Listener-pass words (small model, verbatim prompt; ac.transcript.listener_words) mapped to SEQUENCE
    time like Timeline.words_on_timeline: [{"text", "t0", "t1", "p", "src", "s0", "s1", "track", "clip"}].
    compute=False uses caches only."""
    from .. import transcript as T
    clips = [c for c in tl.audio_clips(tracks=tracks) if Path(c.path).is_file()]
    paths = list(dict.fromkeys(c.path for c in clips))
    rows_by = {}
    for k, p in enumerate(paths):
        if compute:
            sub = _Sub(emit, 100.0 * k / len(paths), 100.0 * (k + 1) / len(paths)) if emit else None
            rows_by[p] = T.listener_words(p, emit=sub)
        else:
            doc = read_json(T.listen_path(p))
            if isinstance(doc, dict) and doc.get("hash") == file_hash(p):
                rows_by[p] = doc.get("words") or []
    out, seen = [], set()
    for c in clips:
        rows = rows_by.get(c.path)
        if not rows:
            continue
        lo, hi = min(c.src_in, c.src_out), max(c.src_in, c.src_out)
        for i, r in enumerate(rows):
            s0, s1 = float(r[1]), float(r[2])
            if not lo <= (s0 + s1) / 2 < hi:
                continue
            key = (c.path, i)
            if key in seen:
                continue
            seen.add(key)
            t0 = c.src_to_seq(max(s0, lo))
            out.append({"text": r[0], "t0": round(t0, 3), "t1": round(max(c.src_to_seq(min(s1, hi)), t0 + 0.01), 3),
                        "p": float(r[3]) if len(r) > 3 else 1.0, "src": c.path, "s0": s0, "s1": s1,
                        "track": c.track, "clip": c.index, "seg": 0, "id": None})
    out.sort(key=lambda w: (w["t0"], w["t1"]))
    return out


class _Sub:
    """Maps a nested helper's 0..100 progress onto lo..hi of the caller's current stage (keeps notes)."""

    def __init__(self, emit, lo, hi):
        self._e, self._lo, self._hi = emit, lo, hi

    def progress(self, pct, note=None, force=False):
        pct = max(0.0, min(100.0, float(pct)))
        self._e.progress(self._lo + (self._hi - self._lo) * pct / 100, note=note, force=force)

    def __getattr__(self, name):
        return getattr(self._e, name)


def _ctx(words, i0, i1, k=6):
    a, b = _segment(words, i0, i1)
    pre = "".join(w["text"] for w in words[max(a, i0 - k):i0]).strip()
    post = "".join(w["text"] for w in words[i1 + 1:min(b, i1 + k) + 1]).strip()
    return pre, post


def _ctx_around(words, t0, t1, k=8, reach=4.0):
    pre = [w for w in words if w["t1"] <= t0 + 0.05 and w["t1"] >= t0 - reach][-k:]
    post = [w for w in words if w["t0"] >= t1 - 0.05 and w["t0"] <= t1 + reach][:k]
    return "".join(w["text"] for w in pre).strip(), "".join(w["text"] for w in post).strip()


# ================================================================ analyze

def _params(p):
    level = p.get("level") if p.get("level") in LEVELS else "normal"
    return {"level": level, "ai": p.get("ai", True) is not False, "deep": p.get("deep", True) is not False,
            "pad": min(0.1, max(0.0, float(p.get("pad", 0.02)))),
            "min_len": min(0.8, max(0.1, float(p.get("min_len", 0.25))))}


def analyze(job, emit):
    p = job.get("params") or {}
    cfg = _params(p)
    tl = Timeline.from_json(job.get("seq"))
    tracks = dialog_tracks(tl)
    if not tl.audio_clips(tracks=tracks):
        raise EngineError("NO_AUDIO", tr("profanity.err.noAudio"), tr("profanity.err.noAudioHint"))
    scope = tl.scope_ranges(p.get("scope"))
    lib = load_library()
    plan = [("words", tr("profanity.st.words"), 0.45)]
    if cfg["deep"]:
        plan.append(("listen", tr("profanity.st.listen"), 0.2))
    plan += [("scan", tr("profanity.st.scan"), 0.05), ("edges", tr("profanity.st.edges"), 0.1)]
    if cfg["ai"]:
        plan.append(("ai", tr("profanity.st.ai"), 0.15))
    plan.append(("review", tr("profanity.st.review"), 0.05))
    emit.plan(plan)

    with emit.step("words"):
        words = tl.words_on_timeline(tracks=tracks, emit=emit)
    in_scope = [w for w in words if R.contains(scope, (w["t0"] + w["t1"]) / 2)]
    listen = []
    if cfg["deep"]:
        with emit.step("listen"):
            try:
                listen = listener_on_timeline(tl, tracks, compute=True, emit=emit)
            except EngineError as e:
                if e.code == "CANCELLED":
                    raise
                emit.warn(tr("profanity.listenSkipped", msg=e.msg))
            emit.progress(100)

    with emit.step("scan"):
        hits, below = detect(words, cfg["level"], lib["block"], lib["allow"])
        found = [_main_hit(words, h) for h in hits]
        lhits, lbelow = detect(listen, cfg["level"], lib["block"], lib["allow"]) if listen else ([], 0)
        main_spans = [[f["w0"], f["w1"]] for f in found]
        for h in lhits:
            f = _listen_hit(listen, words, h)
            if any(R.overlap(f["w0"], f["w1"], a, b) > 0.3 * min(f["w1"] - f["w0"], b - a) for a, b in main_spans):
                continue
            found.append(f)
        found = [f for f in found if R.contains(scope, (f["w0"] + f["w1"]) / 2)]
        emit.progress(100)
        emit.done_note(tr("profanity.nWords", n=len(found)))

    with emit.step("edges"):
        db = tl.envelope_on_timeline(tracks=tracks)
        from .. import media as M
        thr = M.otsu_db(db) if len(db) else -50.0
        a_lo, a_hi = (M.idx(scope[0][0]), M.idx(scope[-1][1])) if scope else (0, len(db))
        speech = SFX.speech_db(db[a_lo:a_hi], thr)
        for f in found:
            f["t0"], f["t1"] = refine(f["w0"], f["w1"], db, wide=f["src"] == "listen", pad=cfg["pad"],
                                      min_len=cfg["min_len"])
        emit.progress(100)

    amb = [f for f in found if f["tier"] == 0]
    ai_src = "off"
    if cfg["ai"]:
        with emit.step("ai"):
            ai_src = _decide_ai(amb, emit)
    for f in amb:
        if "ai" not in f:
            f["on"] = f["rule"][0]
            f["conf"] = 0.6 if f["on"] else 0.3
            f["note"] = {"type": "warn", "text": tr("profanity.noteRules", why=f["rule"][1])[:140]}

    with emit.step("review"):
        items = [_item(tl, f) for f in found]
        wd = workdir(job, tl.name)
        path = RV.path_for(wd, "profanity")
        stats = {"words": len(in_scope), "below": below + lbelow, "ambiguous": len(amb), "ai": ai_src,
                 "listen": sum(1 for f in found if f["src"] == "listen"),
                 "first7": sum(1 for f in found if f.get("on") and f["t0"] < 7.0)}
        doc = RV.new("profanity", items, tl.duration, seq=tl, params=p, stats=stats, level=cfg["level"],
                     speech_db=speech, sensor_track=any(is_sensor_track(t.name) for t in tl.audio),
                     dialog_tracks=tracks, caption_style=lib["captionStyle"])
        old = read_json(path)
        if isinstance(old, dict) and old.get("tool") == "profanity":
            RV.carry_over(doc, old)
            doc["stats"].update(RV.summary(doc))
        RV.save(doc, path)
        _cleanup_previews(wd)
        emit.progress(100)
    if doc["sensor_track"]:
        old = next((t.name.strip() for t in tl.audio if is_sensor_track(t.name)), SENSOR_TRACK)
        emit.warn(tr("profanity.warnSensorTrack", track=old))
    s = RV.summary(doc)
    return {"review": str(path), "summary": tr("profanity.summaryFound", n=s["n"], on=s["on"]),
            "stats": doc["stats"], "n": s["n"], "on": s["on"], "level": cfg["level"]}


def _main_hit(words, h):
    i0, i1 = h["i0"], h["i1"]
    text = "".join(w["text"] for w in words[i0:i1 + 1]).strip()
    pre, post = _ctx(words, i0, i1)
    a, b = _segment(words, i0, i1)
    f = {"src": h["src"], "tier": h["tier"], "lemma": h["lemma"], "masked": h["masked"], "word": text,
         "w0": words[i0]["t0"], "w1": words[i1]["t1"], "pre": pre, "post": post,
         "sentence": "".join(w["text"] for w in words[a:b + 1]).strip(),
         "words": [w["id"] for w in words[i0:i1 + 1] if w.get("id")],
         "texts": [w["text"] for w in words[i0:i1 + 1]], "ref": words[i0], "p": min(w.get("p", 1.0) for w in
                                                                                    words[i0:i1 + 1])}
    if h["tier"] == 0:
        f["rule"] = rule_decide(words, i0, i1)
    return f


def _listen_hit(listen, words, h):
    i0, i1 = h["i0"], h["i1"]
    text = "".join(w["text"] for w in listen[i0:i1 + 1]).strip()
    t0, t1 = listen[i0]["t0"], listen[i1]["t1"]
    pre, post = _ctx_around(listen, t0, t1)        # what the listener heard around it (main pass has a hole)
    f = {"src": "listen", "tier": h["tier"], "lemma": h["lemma"],
         "masked": h["masked"], "word": text, "w0": t0, "w1": t1, "pre": pre, "post": post,
         "sentence": f"{pre} {text} {post}".strip(), "words": [], "texts": [], "ref": listen[i0],
         "p": min(w.get("p", 1.0) for w in listen[i0:i1 + 1])}
    if h["tier"] == 0:
        f["rule"] = rule_decide(listen, i0, i1)
    return f


def _decide_ai(amb, emit):
    """Fill f["ai"], on, conf, note for ambiguous hits via ai_votes. Returns the source tag."""
    if not amb:
        emit.done_note(tr("profanity.notNeeded"))
        emit.progress(100)
        return "none"
    from ..ai import client as ai
    send = amb[:AI_MAX]
    items = [{"kata": f["word"].strip(" ,.!?\"'"), "kalimat": f["sentence"][:300]} for f in send]
    try:
        votes, src = ai_votes(items, emit=emit)
    except ai.AIError as e:
        emit.warn(tr("profanity.aiFallback", msg=str(e)[:120]))
        return "rules"
    for f, v in zip(send, votes):
        need = 2 if v["of"] >= 3 else v["of"]
        f["ai"] = {"votes": v["votes"], "of": v["of"], "reason": v["reason"]}
        # Split vote (1 of 3) + strong context cues = swear: fixes the one measured AI miss
        # ("Anjing emang, file-nya kehapus" got 1/3 in research and here; the rules catch "emang").
        rescue = v["votes"] == 1 and f["rule"][0]
        f["on"] = v["votes"] >= need or rescue
        f["conf"] = 0.55 if rescue else {3: 0.95, 2: 0.75, 1: 0.3, 0: 0.1}.get(v["votes"], 0.5)
        verdict = tr("profanity.verdictSwear") if f["on"] else tr("profanity.verdictLiteral")
        f["note"] = {"type": "ai", "text": (tr("profanity.noteAi", v=v["votes"], of=v["of"], verdict=verdict)
                                           + (f' ({v["reason"]})' if v["reason"] else "")
                                           + (tr("profanity.noteRescue", why=f["rule"][1]) if rescue else ""))[:160]}
    if len(amb) > AI_MAX:
        emit.warn(tr("profanity.aiQuota", n=len(amb) - AI_MAX))
    emit.done_note(tr("profanity.fromCache") if src == "cache" else tr("profanity.nWords", n=len(send)))
    emit.progress(100)
    return src


def _item(tl, f):
    tier, src = f["tier"], f["src"]
    on = f.get("on", True)
    conf = f.get("conf", {3: 0.95, 2: 0.9, 1: 0.85, 0: 0.5}[tier])
    note = f.get("note")
    if src == "user":
        conf, note = 0.99, {"type": "info", "text": tr("profanity.noteUser")}
    elif f["masked"]:
        note = {"type": "info", "text": tr("profanity.noteMasked")}
    if src == "listen":
        conf = round(min(conf, 0.75 * max(0.3, f["p"])), 3)
        if f["p"] < 0.5 and tier != 0:
            on = False
        extra = tr("profanity.noteListen")
        note = {"type": "warn", "text": extra if not note else (note["text"] + ". " + extra)[:160]}
    ref = f["ref"]
    clip = _clip_of(tl, ref)
    media = ref.get("src")
    extra = {}
    if clip is not None and media:
        lo, hi = min(clip.src_in, clip.src_out), max(clip.src_in, clip.src_out)
        extra = {"ms0": round(min(hi, max(lo, clip.seq_to_src(f["t0"]))), 3),
                 "ms1": round(min(hi, max(lo, clip.seq_to_src(f["t1"]))), 3), "lo": round(lo, 3), "hi": round(hi, 3)}
    word = f["word"]
    return RV.item(f["t0"], f["t1"], "word", on=on, conf=conf, label=word, ctx={"pre": f["pre"], "post": f["post"]},
                   note=note, src="ai" if "ai" in f else ("user" if src == "user" else
                                                          ("listen" if src == "listen" else "rule")),
                   word=word, lemma=f["lemma"], tier=tier, tier_label=tier_label(tier),
                   mask=LX.mask_text(word, "stars"), w0=round(f["w0"], 3), w1=round(f["w1"], 3),
                   words=f["words"], texts=f["texts"], media=media, track=ref.get("track"),
                   ai=f.get("ai"), **extra)


def _cleanup_previews(wd, keep=30):
    d = Path(wd) / "preview"
    if not d.is_dir():
        return
    files = sorted(d.glob("pf_*.wav"), key=lambda p: p.stat().st_mtime, reverse=True)
    for p in files[keep:]:
        p.unlink(missing_ok=True)


# ================================================================ sounds, apply, preview

def _num(v, default, lo, hi):
    try:
        return min(hi, max(lo, float(v)))
    except (TypeError, ValueError):
        return default


def sound_settings(p, speech=None):
    """Normalized censor-sound params: {mode, freq, tone_db, duck_db, gain, custom, custom_max, custom_gain}."""
    mode = p.get("mode") if p.get("mode") in MODES else "beep"
    tone = p.get("tone_db", "auto")
    tone_db = SFX.auto_tone_db(speech) if tone in (None, "", "auto") else _num(tone, -20.0, -40.0, -3.0)
    duck = _num(p.get("duck_db", -20), -20.0, -60.0, -3.0)
    return {"mode": mode, "freq": int(_num(p.get("freq", 1000), 1000, 200, 4000)), "tone_db": round(tone_db, 1),
            "tone_auto": tone in (None, "", "auto"), "duck_db": duck,
            "gain": round(10 ** (duck / 20), 5) if mode == "duck" else 0.0,
            "custom": str(p.get("custom") or ""), "custom_max": _num(p.get("custom_max", 3.0), 3.0, 0.2, 10.0),
            "custom_gain": _num(p.get("custom_gain", 0.0), 0.0, -30.0, 12.0)}


def _sound(s, dur):
    """(wav path, seconds) to place for one censor range of `dur` seconds, or None (mute / duck)."""
    if s["mode"] == "beep":
        return SFX.tone_path(dur, s["freq"], s["tone_db"])
    if s["mode"] == "custom":
        return SFX.custom_path(s["custom"], s["custom_max"], s["custom_gain"])
    return None


def plan_ranges(items, gap=MERGE_GAP):
    """Checked items -> merged censor ranges [{"t0", "t1", "paths", "ids", "masks"}] (sequence seconds)."""
    out = []
    for it in sorted(items, key=lambda x: (x["t0"], x["t1"])):
        path = it.get("media")
        if out and it["t0"] - out[-1]["t1"] < gap:
            r = out[-1]
            r["t1"] = max(r["t1"], it["t1"])
        else:
            r = {"t0": it["t0"], "t1": it["t1"], "paths": [], "ids": [], "masks": []}
            out.append(r)
        if path and path not in r["paths"]:
            r["paths"].append(path)
        r["ids"].append(it["id"])
        r["masks"].append(it.get("mask") or LX.mask_text(it.get("label", ""), "stars"))
    for r in out:
        r["t0"], r["t1"] = round(r["t0"], 3), round(r["t1"], 3)
    return out


def apply(job, emit):
    p = job.get("params") or {}
    with emit.step("plan", tr("profanity.st.plan")):
        doc = RV.from_job(job)
        if doc.get("tool") not in (None, "profanity"):
            raise EngineError("BAD_REVIEW", tr("profanity.err.badReview"), tr("profanity.err.badReviewHint"))
        sel = RV.selected(doc)
        if not sel:
            raise EngineError("NO_HITS", tr("profanity.err.noHits"), tr("profanity.err.noHitsHint"))
        s = sound_settings(p, doc.get("speech_db"))
        ranges = plan_ranges(sel)
        tones = []
        for k, r in enumerate(ranges):
            snd = _sound(s, r["t1"] - r["t0"])
            if snd:
                tones.append({"t": SFX.place(r["t0"], r["t1"], snd[1]), "dur": snd[1], "path": str(snd[0])})
            emit.progress(100.0 * (k + 1) / len(ranges))
        label = mode_label(s["mode"])
        markers = []
        if p.get("markers", True) is not False:
            mname = tr("profanity.markerName")
            markers = [{"t": r["t0"], "end": r["t1"], "name": mname, "tag": TAG, "color": 1,
                        "comment": tr("profanity.markerComment", masks=", ".join(r["masks"]), mode=label)}
                       for r in ranges]
        try:
            n_dec = save_decisions(doc)
        except OSError as e:
            n_dec = 0
            emit.warn(tr("profanity.decSaveFail", msg=str(e)))
        emit.done_note(tr("profanity.nParts", n=len(ranges)))
    first7 = sum(1 for r in ranges if r["t0"] < 7.0)
    seq = doc.get("seq") or {}
    plan = {"kind": "censor", "mode": s["mode"], "mode_label": label, "seq": seq, "timebase": "sequence",
            "track_name": SENSOR_TRACK, "tag": TAG, "ramp": RAMP, "duck_db": s["duck_db"],
            "low_db": s["duck_db"] if s["mode"] == "duck" else None, "tone_db": s["tone_db"],
            "tone_auto": s["tone_auto"], "freq": s["freq"], "ranges": ranges, "tones": tones, "markers": markers,
            "name": (seq.get("name") or "Sequence") + SEQ_SUFFIX}
    return {"plan": plan, "summary": tr("profanity.summaryApplied", n=len(sel), mode=label),
            "stats": {"n": len(sel), "ranges": len(ranges), "first7": first7, "tones": len(tones),
                      "sec": round(sum(r["t1"] - r["t0"] for r in ranges), 3), "captions": n_dec,
                      "mode": s["mode"], "tone_db": s["tone_db"]}}


def preview(job, emit):
    """params: {media, s0, s1 (source seconds of the censor range), lo, hi (clip source bounds), id, mode, ...
    sound params, speech_db}. -> {"path" (WAV), "dur", "a", "b"}."""
    p = job.get("params") or {}
    media = p.get("media")
    if not media or not Path(media).is_file():
        raise EngineError("NO_MEDIA", tr("profanity.err.noMedia"), str(media or ""))
    s0, s1 = float(p.get("s0", 0)), float(p.get("s1", 0))
    if s1 <= s0:
        raise EngineError("BAD_JOB", tr("profanity.err.emptyRange"), tr("profanity.err.emptyRangeHint"))
    s = sound_settings(p, p.get("speech_db"))
    with emit.step("render", tr("profanity.st.preview")):
        snd = _sound(s, s1 - s0)
        wd = workdir(job)
        name = re.sub(r"[^\w-]", "_", str(p.get("id") or "x"))[:24]
        out = Path(wd) / "preview" / f"pf_{name}_{s['mode']}_{int(time.time() * 1000) % 100000}.wav"
        r = SFX.render_preview(media, s0, s1, out, gain=s["gain"], lo=p.get("lo"), hi=p.get("hi"), sound=snd)
        _cleanup_previews(wd)
        emit.progress(100)
    r["mode"] = s["mode"]
    return r


# ================================================================ captions: decisions per media + hits

def _decisions_path(media):
    return cache_path(media, "_profanity.json")


def _load_decisions(media):
    doc = read_json(_decisions_path(media))
    try:
        ok = isinstance(doc, dict) and doc.get("v") == DEC_V and doc.get("hash") == file_hash(media)
    except OSError:
        ok = False
    return doc if ok else None


def save_decisions(doc):
    """Store the user's reviewed choices per media word (both censored and kept) next to the media
    (<stem>_profanity.json, keyed by the v3 word index), so captions on ANY sequence cut from that media mask
    the same words. Returns the number of words written."""
    by_media = {}
    for it in doc.get("items", []):
        media = it.get("media")
        if not media or not it.get("words"):
            continue
        for wid, txt in zip(it["words"], it.get("texts") or [""] * len(it["words"])):
            h, _, idx = str(wid).partition(":")
            if idx.isdigit():
                by_media.setdefault(media, {})[idx] = {"on": bool(it.get("on")), "lemma": it.get("lemma"),
                                                       "tier": it.get("tier"), "text": txt, "h": h}
    n = 0
    for media, words in by_media.items():
        if not Path(media).is_file():
            continue
        h10 = file_hash(media)[:10]
        words = {k: {kk: vv for kk, vv in v.items() if kk != "h"} for k, v in words.items() if v["h"] == h10}
        if not words:
            continue
        cur = _load_decisions(media) or {"v": DEC_V, "media": str(media), "hash": file_hash(media), "words": {}}
        cur["words"].update(words)
        cur.update(updated=now_iso(), level=doc.get("level"))
        write_json(_decisions_path(media), cur)
        n += len(words)
    return n


def hits_for_timeline(tl, words=None, style=None, level=None, include_off=False):
    """Censored words for captions, in SEQUENCE time of `tl` (Timeline or Timeline JSON):
    [{"id": "<hash10>:<word idx>", "t0", "t1", "text", "mask", "lemma", "tier", "on", "source"}], sorted.

    Source of truth per word: the user's reviewed choice from the last "Terapkan" on any sequence of the same
    media ("review"), else the rules ("rules": lexicon + the user's lists, ambiguous words by context cues,
    no AI, no GPU: words come from the transcript caches only). style: "stars" (k****l) | "full" | "bip" |
    "off" (returns []); None = the user's setting (panel switch "Sensor juga caption").
    Captions usage: masks = {h["id"]: h["mask"] for h in profanity.hits_for_timeline(tl)}."""
    if not isinstance(tl, Timeline):
        tl = Timeline.from_json(tl)
    lib = load_library()
    style = style if style in STYLES else lib["captionStyle"]
    if style == "off":
        return []
    tracks = dialog_tracks(tl)
    if words is None:
        words = tl.words_on_timeline(tracks=tracks, transcribe=False)
    words = [w for w in words if w.get("id")]
    decs, h2media = {}, {}
    for media in dict.fromkeys(w.get("src") for w in words if w.get("src")):
        try:
            h2media[file_hash(media)[:10]] = media
        except OSError:
            continue
        d = _load_decisions(media)
        if d:
            decs[file_hash(media)[:10]] = d
    if level not in LEVELS:
        level = next((d.get("level") for d in decs.values() if d.get("level") in LEVELS), "normal")
    rule = {}
    hits, _ = detect(words, level, lib["block"], lib["allow"])
    for h in hits:
        on = True if h["tier"] != 0 else rule_decide(words, h["i0"], h["i1"])[0]
        for i in range(h["i0"], h["i1"] + 1):
            rule[i] = (on, h["lemma"], h["tier"])
    out = []
    for i, w in enumerate(words):
        h, _, idx = w["id"].partition(":")
        d = (decs.get(h) or {}).get("words", {}).get(idx)
        if d is not None and LX.norm(d.get("text", "")) != LX.norm(w["text"]):
            d = None                       # transcript was redone: indexes moved, ignore the stale choice
        if d is not None:
            on, lemma, tier, source = bool(d.get("on")), d.get("lemma"), d.get("tier"), "review"
        elif i in rule:
            (on, lemma, tier), source = rule[i], "rules"
        else:
            continue
        if not on and not include_off:
            continue
        out.append({"id": w["id"], "t0": w["t0"], "t1": w["t1"], "text": w["text"],
                    "mask": LX.mask_text(w["text"], style), "lemma": lemma, "tier": tier, "on": on, "source": source})
    return out


def mask_text(word, style="stars"):
    """' kontol,' -> ' k****l,' (see _profanity_lexicon.mask_text)."""
    return LX.mask_text(word, style)


def hits(job, emit):
    p = job.get("params") or {}
    tl = Timeline.from_json(job.get("seq"))
    with emit.step("hits", tr("profanity.st.hits")):
        out = hits_for_timeline(tl, style=p.get("style"), level=p.get("level"),
                                include_off=bool(p.get("include_off")))
        emit.progress(100)
    return {"hits": out, "n": len(out), "style": p.get("style") or load_library()["captionStyle"]}


def library(job, emit):
    """params {op: "get" | "set", block, allow, captionStyle} -> {library, builtin, path}."""
    p = job.get("params") or {}
    lib = save_library({**load_library(), **{k: p[k] for k in ("block", "allow", "captionStyle") if k in p}}) \
        if p.get("op") == "set" else load_library()
    return {"library": lib, "builtin": builtin_lexicon(), "path": str(library_path())}


ACTIONS = {"analyze": analyze, "apply": apply, "preview": preview, "hits": hits, "library": library}
