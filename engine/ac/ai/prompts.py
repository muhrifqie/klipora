"""AI prompts for AutoCut (port of proto/ai/prompts.py) on top of ac.ai.client: prompts, schemas,
validators, post-processing and fallbacks. Inputs are ac.ai.text display words / sentences.

Every task returns {"source": "ai"|"fallback", "result": ..., "warnings": [...], "stats": {...}} and never
raises: if the proxy is down / quota is gone / output is unusable, the deterministic fallback runs.

Robustness rules learned while testing (see internal AI research notes):
- Long transcripts are shown as "mm:ss text" lines WITHOUT integer ids. Grok copies the mm:ss + a short
  quote reliably; bracketed ids were garbage on a 480-line transcript (all 0, or 0,1,2,3...).
- Every time reference comes with a QUOTE; resolve() snaps to the unit whose time AND text match, else
  searches the quote globally (monotonic), else nearest time. Final times always come from Whisper words.
- Word-level tasks return the OLD text next to the new one; edits are re-anchored and rejected when the
  old text cannot be found or the change rewrites content instead of fixing it.
"""
from __future__ import annotations

import difflib
import re
import unicodedata

from ..i18n import tr
from . import client as ai
from .text import caption_lines, norm, parse_ts, ts

PROMPT_VERSION = "2026-10-05.2"

BASE = ("You are the AI module of a video-editing plugin (Adobe Premiere) for Indonesian content creators. "
        "Transcripts come from speech recognition and can contain misheard words. Follow the rules exactly.")

STOP = set("""yang di ke dari dan atau ini itu ya nah sih aja saja kok deh dong lah kan tuh nih guys kita kamu
saya aku kami mereka dia untuk dengan pada juga lagi akan bisa ada adalah udah sudah mau nanti dulu tadi
gitu gini kayak apa oke ok baik terus jadi kalau kalo misalkan karena tapi tetapi biar supaya agar dalam oleh
seperti sama banget emang memang pun para si sang the a an of to and or is are be in on for it this that
silahkan silakan mungkin harus sebenernya sebenarnya pokoknya intinya teman-teman temanteman kalian cara
semuanya nantinya rencananya""".split())


# ====================================================================== shared helpers

def _sim(a, b):
    return difflib.SequenceMatcher(None, norm(a), norm(b)).ratio()


def _quote_score(quote, text, at_start=True):
    """How well a short quote matches the start (or end) of a unit text, 0..1."""
    q = norm(quote)
    t = norm(text)
    if not q:
        return 0.0
    if q in t:
        return 1.0 if (t.startswith(q) if at_start else t.endswith(q)) else 0.9
    seg = t[:len(q) + 4] if at_start else t[-(len(q) + 4):]
    return difflib.SequenceMatcher(None, q, seg).ratio()


def resolve(sents, time_txt, quote, at_start=True, after=-1, tol=3.0, accept=0.72):
    """Find the transcript unit a model answer points at. -> (index | None, how).

    The model copies the "mm:ss" shown in front of each line (reliable: max drift 2.2 s in tests) and a
    short quote of the line (reliable). Bracketed integer ids were NOT reliable with grok-fast on long lists
    (it returned 0 for every chapter; grok-auto returned 0,1,2,3...), so prompts carry no ids at all.
    how: 'ok' (time + quote agree), 'quote' (found by quote elsewhere), 'time' (quote not found), 'none'."""
    key = "start" if at_start else "end"
    if isinstance(time_txt, str) and "-" in time_txt:  # model copied a whole "start-end" range
        parts = [x.strip() for x in time_txt.split("-") if x.strip()]
        time_txt = parts[0] if at_start else parts[-1]
    try:
        t = parse_ts(time_txt)
    except (ValueError, TypeError):
        t = None
    if t is not None:
        near = [i for i, s in enumerate(sents) if i > after and abs(s[key] - t) <= tol + 1.0]
        if near:
            if quote:
                sc, i = max((_quote_score(quote, sents[i]["text"], at_start), i) for i in near)
                if sc >= accept:
                    return i, "ok"
            else:
                return min(near, key=lambda i: abs(sents[i][key] - t)), "ok"
    if quote:  # global search, monotonic, closest to the claimed time on ties
        best, best_sc = None, 0.0
        for i in range(after + 1, len(sents)):
            sc = _quote_score(quote, sents[i]["text"], at_start)
            if t is not None:
                sc -= min(abs(sents[i][key] - t), 600) / 6000  # <= 0.1 penalty for distance
            if sc > best_sc:
                best, best_sc = i, sc
        if best is not None and best_sc >= accept:
            return best, "quote"
    if t is not None:
        cand = list(range(after + 1, len(sents)))
        if cand:
            i = min(cand, key=lambda i: abs(sents[i][key] - t))
            if abs(sents[i][key] - t) <= 15:
                return i, "time"
    return None, "none"


def _run(name, messages, schema, check, post, fallback, model=None, trace=None, timeout=None):
    stats = {"task": name}
    if not ai.available():
        return {"source": "fallback", "result": fallback(), "warnings": [tr("ai.offline")],
                "stats": stats}
    try:
        obj = ai.chat_json(messages, schema=schema, check=check, model=model, trace=trace, tag=name,
                           timeout=timeout)
    except ai.AIError as e:
        return {"source": "fallback", "result": fallback(), "warnings": [tr("ai.failed", type=type(e).__name__, err=str(e))],
                "stats": stats}
    result, warnings, st = post(obj)
    stats.update(st)
    return {"source": "ai", "result": result, "warnings": warnings, "stats": stats}


def _is_emoji(s):
    s = (s or "").strip()
    if not s:
        return True
    if len(s) > 8:
        return False
    return all(unicodedata.category(ch) in ("So", "Sk", "Mn", "Cf") or ch in "‍️" for ch in s)


def _lines(sents, with_end=False):
    """Prompt rendering without ids: 'mm:ss text' or 'mm:ss-mm:ss text'."""
    return [f"{ts(s['start'])}{'-' + ts(s['end']) if with_end else ''} {s['text']}" for s in sents]


TS_PATTERN = r"^\d{1,2}:\d{2}(:\d{2})?(\s*-\s*\d{1,2}:\d{2}(:\d{2})?)?$"  # a copied range is tolerated

# ====================================================================== (a) chapters

CHAPTERS_SCHEMA = {"type": "object", "required": ["chapters"], "properties": {
    "chapters": {"type": "array", "minItems": 1, "maxItems": 40, "items": {
        "type": "object", "required": ["start", "quote", "title"], "properties": {
            "start": {"type": "string", "pattern": TS_PATTERN}, "quote": {"type": "string"},
            "title": {"type": "string", "minLength": 2, "maxLength": 80}}}}}}


def chapter_targets(duration):
    target = max(3, min(15, round(duration / 240)))
    hi = max(4, min(20, int(duration // 90)))
    return target, 3, hi


def chapters_messages(sents, duration, min_len=10, glossary=()):
    target, lo, hi = chapter_targets(duration)
    gl = f"\nKnown names (correct spelling): {', '.join(glossary)}." if glossary else ""
    sys_ = f"""{BASE}
Task: split the video into YouTube chapters.
Input: one transcript line per row: mm:ss text   (mm:ss = time the line starts). Video length {ts(duration)}.
Rules:
1. The first chapter starts at 00:00.
2. Start a new chapter only where the topic or tutorial step clearly changes. About {target} chapters (min {lo}, max {hi}).
3. Every chapter lasts at least {min_len} seconds; spread chapters over the whole video, the last part included.
4. start: copy the mm:ss of the line where the chapter begins, exactly as shown.
5. quote: copy the first 3-6 words of that same line exactly as written.
6. title: Indonesian, 2-6 words, specific to what happens in that part (e.g. "Daftar Akun Reseller", "Hubungkan Domain Sendiri"), Title Case, no numbering, no emoji, no quotes, no trailing period. Correct misheard brand names.{gl}
Output: {{"chapters":[{{"start":"00:00","quote":"...","title":"..."}}]}}"""
    return [{"role": "system", "content": sys_}, {"role": "user", "content": "\n".join(_lines(sents))}]


def chapters_post(obj, sents, duration, min_len=10):
    warns, st = [], {"raw_n": len(obj["chapters"]), "ok": 0, "quote": 0, "time": 0, "none": 0}
    items, after = [], -1
    raw_times = []
    for c in obj["chapters"]:
        try:
            raw_times.append(parse_ts(c["start"]))
        except ValueError:
            pass
        i, how = resolve(sents, c["start"], c.get("quote", ""), True, after)
        st[how] += 1
        if i is None:
            continue
        after = i
        title = re.sub(r"^\s*(\d+[.):-]\s*|bab\s*\d+\s*[:.-]\s*)", "", c["title"].strip(), flags=re.I)
        items.append({"i": i, "title": title.strip(" .\"'")[:60]})
    st["raw_first_zero"] = bool(raw_times) and raw_times[0] == 0
    st["raw_sorted"] = raw_times == sorted(raw_times)
    st["raw_min_gap_ok"] = all(b - a >= min_len for a, b in zip(raw_times, raw_times[1:]))
    out = []
    for it in items:  # min chapter length; first chapter forced to 00:00
        start = 0.0 if not out else sents[it["i"]]["start"]
        if out and start - out[-1]["start"] < min_len:
            continue
        out.append({"start": round(start, 2), "title": it["title"], "unit": it["i"]})
    while len(out) > 1 and duration - out[-1]["start"] < min_len:
        out.pop()
    hi = chapter_targets(duration)[2]
    while len(out) > hi:  # too many: merge the shortest chapter into its predecessor
        ends = [c["start"] for c in out[1:]] + [duration]
        k = min(range(1, len(out)), key=lambda k: ends[k] - out[k]["start"])
        out.pop(k)
    st["final_n"] = len(out)
    if len(out) < 3:
        warns.append("YouTube butuh minimal 3 chapter (masing-masing >= 10 detik)")
    return {"chapters": out, "youtube": "\n".join(f"{ts(c['start'])} {c['title']}" for c in out)}, warns, st


def chapters_fallback(sents, duration, min_len=10):
    """No AI: cut at the longest pauses, title = first content words of the chapter."""
    target, _, _ = chapter_targets(duration)
    gaps = sorted(((sents[i]["start"] - sents[i - 1]["end"], i) for i in range(1, len(sents))), reverse=True)
    cuts = [0]
    spacing = max(min_len, duration / (target * 2))
    for _, i in gaps:
        if len(cuts) >= target:
            break
        if all(abs(sents[i]["start"] - sents[c]["start"]) >= spacing for c in cuts):
            cuts.append(i)
    cuts.sort()
    out = []
    for n, i in enumerate(cuts):
        words = [w for w in re.findall(r"[\w-]+", sents[i]["text"]) if w.lower() not in STOP][:4]
        out.append({"start": 0.0 if n == 0 else round(sents[i]["start"], 2), "unit": i,
                    "title": " ".join(words).title() or f"Bagian {n + 1}"})
    return {"chapters": out, "youtube": "\n".join(f"{ts(c['start'])} {c['title']}" for c in out)}


def chapters(sents, duration, model=None, trace=None, glossary=()):
    return _run("chapters", chapters_messages(sents, duration, glossary=glossary), CHAPTERS_SCHEMA, None,
                lambda o: chapters_post(o, sents, duration), lambda: chapters_fallback(sents, duration),
                model, trace)


# ====================================================================== (b) viral clips

VIRAL_SCHEMA = {"type": "object", "required": ["clips"], "properties": {
    "clips": {"type": "array", "maxItems": 20, "items": {
        "type": "object", "required": ["start", "end", "start_quote", "end_quote", "title", "hook", "score"],
        "properties": {
            "start": {"type": "string", "pattern": TS_PATTERN}, "end": {"type": "string", "pattern": TS_PATTERN},
            "start_quote": {"type": "string"}, "end_quote": {"type": "string"},
            "title": {"type": "string", "minLength": 3, "maxLength": 100},
            "hook": {"type": "string", "minLength": 2, "maxLength": 80},
            "score": {"type": "number", "minimum": 0, "maximum": 100},
            "reason": {"type": "string"}}}}}}


def viral_messages(sents, n=5, min_s=30, max_s=90, glossary=()):
    gl = f"\nKnown names (correct spelling): {', '.join(glossary)}." if glossary else ""
    sys_ = f"""{BASE}
Task: find the best short-form clips (TikTok / Reels / Shorts) inside a longer Indonesian video.
Input: one transcript line per row: start-end text   (times mm:ss).
A clip is a contiguous run of lines: from the start of its first line to the end of its last line.
Rules:
1. Duration between {min_s} and {max_s} seconds (ideal 40-60). Clips under {min_s} s are rejected, so extend over several consecutive lines. Compute it: end minus start, e.g. 02:59 -> 03:41 = 42 s, and put it in "seconds".
2. Self-contained: understandable without the rest of the video. Begin at the start of a thought (not "jadi tadi...", not mid-sentence); end after the point is complete.
3. Strong first 3 seconds: a question, bold claim, surprising result, clear promise, mistake to avoid or useful tip.
4. Prefer useful tips, results/payoffs, warnings, emotional or funny moments. Avoid greetings, outros, pure "klik ini klik itu" narration.
5. Clips must not overlap. Return up to {n} clips, best first; fewer if there are not {n} good ones.
6. start = start time of the first line, end = end time of the last line, both copied exactly as shown.
7. start_quote = first 3-6 words of the first line; end_quote = last 3-6 words of the last line; copy exactly.
8. score 0-100 = expected short-form performance; be strict (80+ only for genuinely strong clips).
9. title: Indonesian, max 60 chars, curiosity-driven but honest. hook: on-screen text for the first seconds, Indonesian, max 8 words. reason: Indonesian, max 20 words.{gl}
Every clip needs ALL fields.
Output: {{"clips":[{{"start":"00:00","end":"00:00","seconds":0,"start_quote":"...","end_quote":"...","title":"...","hook":"...","score":0,"reason":"..."}}]}}"""
    return [{"role": "system", "content": sys_}, {"role": "user", "content": "\n".join(_lines(sents, True))}]


def _fit_duration(sents, a, b, min_s, max_s, target=45.0, pause=0.8):
    """Make a clip [a..b] (unit indices) last min_s..max_s and end at a natural pause.
    The model reliably picks a good START (hook) but often returns 7-25 s clips, ignoring the minimum, so the
    end is extended locally: reach min_s, keep going while the speaker continues the same thought
    (gap < pause) up to `target`, and never stop right before a short gap (mid-sentence)."""
    dur = lambda a, b: sents[b]["end"] - sents[a]["start"]  # noqa: E731
    gap_after = lambda b: sents[b + 1]["start"] - sents[b]["end"] if b + 1 < len(sents) else 99  # noqa: E731
    while dur(a, b) > max_s and b > a:
        b -= 1
    while dur(a, b) < min_s:
        if b + 1 < len(sents) and dur(a, b + 1) <= max_s:
            b += 1
        elif a > 0 and dur(a - 1, b) <= max_s:
            a -= 1
        else:
            break
    while b + 1 < len(sents) and dur(a, b + 1) <= max_s and (
            (dur(a, b) < target and gap_after(b) < pause * 2) or gap_after(b) < pause * 0.5):
        b += 1
    return a, b


def viral_post(obj, sents, min_s=30, max_s=90, pad=(0.15, 0.3)):
    st = {"raw_n": len(obj["clips"]), "raw_dur_ok": 0, "resolve": {}, "fixed_dur": 0, "dropped": 0}
    clips = []
    for c in obj["clips"]:
        a, ha = resolve(sents, c["start"], c.get("start_quote", ""), True)
        b, hb = resolve(sents, c["end"], c.get("end_quote", ""), False, after=(a - 1 if a is not None else -1))
        for h in (ha, hb):
            st["resolve"][h] = st["resolve"].get(h, 0) + 1
        if a is None or b is None or b < a:
            st["dropped"] += 1
            continue
        try:
            d_claim = parse_ts(c["end"]) - parse_ts(c["start"])
        except ValueError:
            d_claim = -1
        if min_s <= d_claim <= max_s:
            st["raw_dur_ok"] += 1
        d0 = sents[b]["end"] - sents[a]["start"]
        if not min_s <= d0 <= max_s:
            a, b = _fit_duration(sents, a, b, min_s, max_s)
            st["fixed_dur"] += 1
        d = sents[b]["end"] - sents[a]["start"]
        if not (min_s * 0.8 <= d <= max_s):
            st["dropped"] += 1
            continue
        start = max(sents[a]["start"] - pad[0], sents[a - 1]["end"] if a else 0.0)
        end = min(sents[b]["end"] + pad[1], sents[b + 1]["start"] if b + 1 < len(sents) else sents[b]["end"] + pad[1])
        clips.append({"start": round(start, 2), "end": round(end, 2), "duration": round(end - start, 1),
                      "units": [a, b], "title": c["title"][:70], "hook": c["hook"][:60],
                      "score": int(round(c["score"])), "reason": c.get("reason", "")[:200],
                      "text": " ".join(s["text"] for s in sents[a:b + 1])})
    clips.sort(key=lambda c: -c["score"])
    kept = []
    for c in clips:  # non-maximum suppression on overlap
        if all(c["end"] <= k["start"] or c["start"] >= k["end"] for k in kept):
            kept.append(c)
        else:
            st["dropped"] += 1
    st["final_n"] = len(kept)
    return {"clips": kept}, ([] if kept else ["Tidak ada clip 30-90 detik yang valid"]), st


def viral_fallback(sents, n=5, min_s=30, max_s=90):
    """No AI: windows of dense speech that start after a pause (topic start), clearly marked unscored."""
    out = []
    for i in range(len(sents)):
        if i and sents[i]["start"] - sents[i - 1]["end"] < 1.5:
            continue
        a, b = _fit_duration(sents, i, i, min_s, max_s)
        d = sents[b]["end"] - sents[a]["start"]
        if min_s <= d <= max_s:
            words = sum(len(s["text"].split()) for s in sents[a:b + 1])
            out.append({"start": sents[a]["start"], "end": sents[b]["end"], "duration": round(d, 1),
                        "units": [a, b], "title": sents[a]["text"][:60], "hook": "", "score": 0,
                        "density": round(words / d, 2), "reason": "fallback: kepadatan bicara"})
    out.sort(key=lambda c: -c["density"])
    kept = []
    for c in out:
        if all(c["end"] <= k["start"] or c["start"] >= k["end"] for k in kept):
            kept.append(c)
        if len(kept) >= n:
            break
    return {"clips": kept}


RESCORE_SCHEMA = {"type": "object", "required": ["clips"], "properties": {
    "clips": {"type": "array", "items": {"type": "object", "required": ["c", "score", "title", "hook"], "properties": {
        "c": {"type": "string", "maxLength": 2}, "score": {"type": "number", "minimum": 0, "maximum": 100},
        "title": {"type": "string", "minLength": 3, "maxLength": 100},
        "hook": {"type": "string", "minLength": 2, "maxLength": 80}, "reason": {"type": "string"}}}}}}


def rescore_messages(clips, glossary=()):
    gl = f"\nKnown names (correct spelling): {', '.join(glossary)}." if glossary else ""
    rows = [f"{chr(65 + k)} ({c['duration']:.0f} s): {c['text']}" for k, c in enumerate(clips)]
    sys_ = f"""{BASE}
Task: judge these candidate short-form clips (TikTok / Reels / Shorts) cut from an Indonesian video.
For EVERY clip letter give: score 0-100 (calibration: 0-20 unusable, 20-40 weak, 40-60 decent tip, 60-80 strong hook and self-contained, 80+ exceptional), title (Indonesian, max 60 chars, honest curiosity), hook (on-screen text for the first seconds, Indonesian, max 8 words), reason (Indonesian, max 15 words).{gl}
Output: {{"clips":[{{"c":"A","score":0,"title":"...","hook":"...","reason":"..."}}]}}"""
    return [{"role": "system", "content": sys_}, {"role": "user", "content": "\n".join(rows)}]


def viral(sents, n=5, min_s=30, max_s=90, model=None, trace=None, glossary=(), rescore=True):
    """Two calls: (1) pick hooks/topics over the whole transcript, (2) after local 30-90 s expansion,
    re-judge the FINAL clip texts (titles/hooks/scores then match what is actually in the clip).
    Call 2 is skipped when nothing was expanded or rescore=False; its failure keeps call-1 values."""
    r = _run("viral", viral_messages(sents, n, min_s, max_s, glossary), VIRAL_SCHEMA, None,
             lambda o: viral_post(o, sents, min_s, max_s), lambda: viral_fallback(sents, n, min_s, max_s),
             model, trace)
    clips = r["result"]["clips"]
    if r["source"] != "ai" or not clips or not rescore or not r["stats"].get("fixed_dur"):
        return r
    try:
        obj = ai.chat_json(rescore_messages(clips, glossary), schema=RESCORE_SCHEMA, model=model, tag="viral_rescore")
    except ai.AIError as e:
        r["warnings"].append(tr("ai.rescoreFailed", err=str(e)))
        return r
    got = 0
    for it in obj["clips"]:
        k = ord(it["c"].strip().upper()[:1] or "?") - 65
        if 0 <= k < len(clips):
            clips[k].update(score=int(round(it["score"])), title=it["title"][:70], hook=it["hook"][:60],
                            reason=it.get("reason", "")[:200], rescored=True)
            got += 1
    clips.sort(key=lambda c: -c["score"])
    r["stats"]["rescored"] = got
    return r


# ====================================================================== (c) emphasis words (+ emoji, zoom)
# v1 numbered caption lines "L<n>: ..." -> grok-fast re-chunked the lines (L2 answered with words of L3),
# returned 3-4 words for "max 2", and never used emoji/zoom. v2 works per sentence with mm:ss anchors, matches
# keywords inside the resolved sentence, then projects highlights onto caption lines (max 2 per line).

EMPH_SCHEMA = {"type": "object", "required": ["s"], "properties": {
    "s": {"type": "array", "items": {"type": "object", "required": ["t", "k"], "properties": {
        "t": {"type": "string", "pattern": TS_PATTERN}, "k": {"type": "array", "items": {"type": "string"}},
        "emoji": {"type": ["string", "null"]}, "zoom": {"type": ["boolean", "null"]}}}}}}


def emphasis_messages(sents):
    n_zoom = max(1, round(len(sents) / 8))
    n_emoji = max(1, round(len(sents) / 5))
    sys_ = f"""{BASE}
Task: pick the words to highlight in animated captions (colored / bigger word), plus a few emoji and punch-in zooms.
Input: mm:ss text, one spoken sentence per row ({len(sents)} rows).
For EVERY row return:
- t: the mm:ss copied exactly.
- k: 1-3 key words copied exactly from that row (single words as written): key nouns, numbers, prices, brand/product names, strong verbs, warnings ("jangan", "wajib", "gratis"), results. Never function words or fillers (yang, di, ke, dan, ini, itu, ya, nah, sih, aja, guys, kita, kamu, saya, oke, baik, teman-teman). Empty list if nothing deserves it.
- emoji: give exactly {n_emoji} rows in total one emoji that clearly fits (money 💰, done ✅, warning ⚠️, phone 📱, email 📧, shop 🛒, fast ⚡, link 🔗); all other rows "".
- zoom: set true on exactly {n_zoom} rows in total - the strongest moments (key point, warning, result, punchline); all other rows false.
Output: {{"s":[{{"t":"00:00","k":["word"],"emoji":"","zoom":false}}]}}"""
    return [{"role": "system", "content": sys_}, {"role": "user", "content": "\n".join(_lines(sents))}]


def _find_word(dwords, w0, w1, word):
    key = norm(word)
    for i in range(w0, w1 + 1):
        if norm(dwords[i]["text"]) == key:
            return i
    best = max(((_sim(dwords[i]["text"], word), i) for i in range(w0, w1 + 1)), default=(0, None))
    return best[1] if best[0] >= 0.8 else None


def emphasis_post(obj, dwords, sents, lines, per_line=2):
    st = {"sents": len(sents), "returned": 0, "unresolved": 0, "kw_total": 0, "kw_matched": 0, "kw_stop": 0,
          "emoji": 0, "zoom": 0, "bad_emoji": 0}
    hl, emoji_at, zoom_at, seen = set(), {}, set(), set()
    for e in obj["s"]:
        k, how = resolve(sents, e["t"], " ".join(e.get("k", [])[:1]), True, tol=1.0, accept=0.0)
        if k is None:
            st["unresolved"] += 1
            continue
        if k in seen:
            continue
        seen.add(k)
        st["returned"] += 1
        s = sents[k]
        first = None
        for word in e.get("k", [])[:3]:
            st["kw_total"] += 1
            i = _find_word(dwords, s["w0"], s["w1"], word)
            if i is None:
                continue
            st["kw_matched"] += 1
            if norm(dwords[i]["text"]) in STOP:
                st["kw_stop"] += 1
                continue
            hl.add(i)
            first = i if first is None else first
        em = (e.get("emoji") or "").strip()
        if em and not _is_emoji(em):
            st["bad_emoji"] += 1
            em = ""
        anchor_w = first if first is not None else s["w0"]
        if em:
            emoji_at[anchor_w] = em
            st["emoji"] += 1
        if e.get("zoom"):
            zoom_at.add(anchor_w)
            st["zoom"] += 1
    res = []
    for n, ln in enumerate(lines):
        picked = [i for i in ln if i in hl]
        picked = sorted(picked, key=lambda i: -len(norm(dwords[i]["text"])))[:per_line]  # cap per caption line
        res.append({"l": n, "words": ln, "hl": sorted(picked), "emoji": next((emoji_at[i] for i in ln if i in emoji_at), ""),
                    "zoom": any(i in zoom_at for i in ln),
                    "zoom_t": next((dwords[i]["start"] for i in ln if i in zoom_at), None)})
    st["coverage"] = round(st["returned"] / max(1, len(sents)), 3)
    empty = [r for r in res if not r["hl"]]
    if empty:  # lines whose sentence got nothing: heuristic fill so every line can still animate
        fb = emphasis_fallback(dwords, [r["words"] for r in empty])["lines"]
        for r, f in zip(empty, fb):
            r["hl"], r["filled_by_fallback"] = f["hl"], True
    st["lines_filled_by_fallback"] = len(empty)
    return {"lines": res}, [], st


def emphasis_fallback(dwords, lines, offset=0):
    res = []
    for n, ln in enumerate(lines):
        best = None
        for i in ln:
            t = dwords[i]["text"]
            k = norm(t)
            if k in STOP or len(k) < 4:
                continue
            score = len(k) + (6 if any(ch.isdigit() for ch in k) else 0) + (3 if t[:1].isupper() and i != ln[0] else 0)
            if best is None or score > best[0]:
                best = (score, i)
        res.append({"l": n + offset, "words": ln, "hl": [best[1]] if best else [], "emoji": "", "zoom": False,
                    "zoom_t": None})
    return {"lines": res}


def emphasis(dwords, sents, lines=None, model=None, trace=None, chunk=120):
    """Per-sentence keywords (chunks of `chunk` sentences, run in parallel) projected onto caption lines."""
    lines = lines or caption_lines(dwords)
    parts = [sents[i:i + chunk] for i in range(0, len(sents), chunk)]

    def one(part):
        return _run("emphasis", emphasis_messages(part), EMPH_SCHEMA, None, lambda o: (o, [], {}),
                    lambda: {"s": []}, model, trace if len(parts) == 1 else None)
    merged, warns, srcs = {"s": []}, [], set()
    for r in (ai.map_parallel(one, parts) if len(parts) > 1 else [one(parts[0])]):
        if isinstance(r, Exception):
            warns.append(str(r))
            srcs.add("fallback")
            continue
        merged["s"] += r["result"]["s"]
        warns += r["warnings"]
        srcs.add(r["source"])
    res, w2, st = emphasis_post(merged, dwords, sents, lines)
    src = srcs.pop() if len(srcs) == 1 else "mixed"
    return {"source": src, "result": res, "warnings": warns + w2, "stats": st}


# ---------------------------------------------------------------------- (c2) zoom / emoji moments
# Grok ignores "emoji on 1 of 5 lines / zoom on 1 of 8 lines" inside a per-line task (1 emoji, 1 zoom in 480
# sentences). As a separate "pick the N strongest moments" selection it behaves like chapters/viral.

MOMENTS_SCHEMA = {"type": "object", "required": ["m"], "properties": {
    "m": {"type": "array", "items": {"type": "object", "required": ["t", "quote"], "properties": {
        "t": {"type": "string", "pattern": TS_PATTERN}, "quote": {"type": "string"},
        "emoji": {"type": ["string", "null"]}, "kind": {"type": "string"}, "why": {"type": "string"}}}}}}


def moments_messages(sents, n, spacing):
    sys_ = f"""{BASE}
Task: pick the {n} strongest moments of this video for a punch-in zoom (AutoZoom) with an optional emoji pop-up.
Input: mm:ss text, one spoken sentence per row.
Good moments: the key point of a step, a warning ("jangan", "wajib"), a result or payoff, a price/number, a joke, a strong opinion. Spread them over the whole video, at least {spacing} seconds apart.
For each moment:
- t: the mm:ss of the row, copied exactly.
- quote: the 1-4 most important consecutive words of that row, copied exactly (the zoom lands on them).
- emoji: one emoji that clearly fits (💰 ✅ ⚠️ 📱 📧 🛒 ⚡ 🔗 🔥 😅 👉) or "".
- kind: point | warning | result | number | joke.
- why: Indonesian, max 8 words.
Output: {{"m":[{{"t":"00:00","quote":"...","emoji":"","kind":"point","why":"..."}}]}}"""
    return [{"role": "system", "content": sys_}, {"role": "user", "content": "\n".join(_lines(sents))}]


def moments_post(obj, dwords, sents, spacing, n=999):
    st = {"raw_n": len(obj["m"]), "resolved": 0, "quote_found": 0, "too_close": 0, "emoji": 0}
    out = []
    for m in obj["m"]:
        k, how = resolve(sents, m["t"], m.get("quote", ""), True, tol=1.0, accept=0.0)
        if k is None:
            continue
        st["resolved"] += 1
        s = sents[k]
        words = m.get("quote", "").split()
        hit = [_find_word(dwords, s["w0"], s["w1"], w) for w in words]
        hit = [i for i in hit if i is not None]
        if hit:
            st["quote_found"] += 1
            a, b = min(hit), max(hit)
        else:
            a, b = s["w0"], s["w1"]
        start, end = dwords[a]["start"], dwords[b]["end"]
        if any(abs(start - o["start"]) < spacing for o in out):
            st["too_close"] += 1
            continue
        em = (m.get("emoji") or "").strip()
        em = em if _is_emoji(em) else ""
        st["emoji"] += bool(em)
        out.append({"start": round(start, 2), "end": round(max(end, start + 0.6) + 0.4, 2), "w0": a, "w1": b,
                    "words": " ".join(dwords[i]["text"] for i in range(a, b + 1)), "emoji": em,
                    "kind": m.get("kind", "point"), "why": m.get("why", "")[:80]})
    out.sort(key=lambda o: o["start"])
    if len(out) > n:  # grok-auto returned 40 for n=23: keep an evenly spread subset
        out = [out[int(i * len(out) / n)] for i in range(n)]
    st["final_n"] = len(out)
    return {"moments": out}, [], st


def moments(dwords, sents, n=None, spacing=20.0, model=None, trace=None):
    dur = sents[-1]["end"] - sents[0]["start"] if sents else 0
    n = n or max(3, min(25, round(dur / 90)))
    return _run("moments", moments_messages(sents, n, int(spacing)), MOMENTS_SCHEMA, None,
                lambda o: moments_post(o, dwords, sents, spacing, n), lambda: {"moments": []}, model, trace)


# ====================================================================== (d) b-roll keywords

BROLL_SCHEMA = {"type": "object", "required": ["items"], "properties": {
    "items": {"type": "array", "items": {"type": "object", "required": ["t", "q", "score"], "properties": {
        "t": {"type": "string", "pattern": TS_PATTERN}, "w": {"type": "string"},
        "q": {"type": "string", "maxLength": 80}, "alt": {"type": "string", "maxLength": 80},
        "score": {"type": "number", "minimum": 0, "maximum": 100}}}}}}


def broll_messages(sents):
    sys_ = f"""{BASE}
Task: suggest stock-footage B-roll for each transcript line (search on Pexels/Pixabay/Storyblocks).
Input: mm:ss text  (Indonesian), one line per row.
Rules for EVERY line:
1. t: the mm:ss of the line, copied exactly. w: the first 2-3 words of the line, copied exactly.
2. q: English search query, 2-5 words, a concrete filmable visual (people, objects, places, actions), e.g. "woman typing on laptop", "online shopping smartphone", "money transfer app". Never UI words like "click button" or "dashboard menu"; never brand names.
3. alt: a second, different English query (2-5 words).
4. score 0-100 = how much this moment benefits from B-roll (abstract benefits, emotions, money, business, results = high; greetings, fillers, "klik ini", reading or filling form fields, on-screen steps = low, often 0). Be selective: most lines 0-40, only about 1 in 5 lines above 60.
Output: {{"items":[{{"t":"00:00","w":"...","q":"...","alt":"...","score":0}}]}}"""
    return [{"role": "system", "content": sys_}, {"role": "user", "content": "\n".join(_lines(sents))}]


def broll_post(obj, sents_part, brands=()):
    st = {"given": len(sents_part), "returned": 0, "bad_q": 0, "dupe": 0, "unresolved": 0, "brand": 0}
    bad_words = {norm(b) for b in brands} | {"whatsapp", "cloudflare", "telegram", "tiktok", "shopee", "google"}
    seen, items, after = set(), [], -1
    for it in obj["items"]:
        k, how = resolve(sents_part, it["t"], it.get("w", ""), True, after=-1, tol=1.0)
        if k is None:
            st["unresolved"] += 1
            continue
        sid = sents_part[k]["id"]
        if sid in seen:
            st["dupe"] += 1
            continue
        seen.add(sid)
        st["returned"] += 1
        q = re.sub(r"\s+", " ", it["q"]).strip().strip(".\"'").lower()
        if any(norm(w) in bad_words for w in q.split()):
            st["brand"] += 1
            q = " ".join(w for w in q.split() if norm(w) not in bad_words)
        if not q or not q.isascii() or not 1 <= len(q.split()) <= 7:
            st["bad_q"] += 1
            continue
        items.append({"id": sid, "q": q, "alt": (it.get("alt") or "").lower().strip()[:80],
                      "score": int(round(it["score"]))})
    st["coverage"] = round(st["returned"] / max(1, st["given"]), 3)
    return {"items": items}, [], st


def broll_pick(items, sents, min_score=60, min_spacing=8.0, per_minute=2.0, clip_len=(2.5, 5.0)):
    """Choose where B-roll actually goes: best scores first, >= min_spacing s apart, at most per_minute
    inserts per minute of speech (Grok inflates scores, so the density cap is what really limits it)."""
    by_id = {s["id"]: s for s in sents}
    span = (sents[-1]["end"] - sents[0]["start"]) if sents else 0
    cap = max(1, int(span / 60 * per_minute))
    chosen = []
    for it in sorted(items, key=lambda x: -x["score"]):
        if it["score"] < min_score or len(chosen) >= cap:
            break
        s = by_id[it["id"]]
        if all(abs(s["start"] - c["start"]) >= min_spacing for c in chosen):
            dur = min(max(s["end"] - s["start"], clip_len[0]), clip_len[1])
            chosen.append({**it, "start": round(s["start"], 2), "end": round(s["start"] + dur, 2)})
    return sorted(chosen, key=lambda c: c["start"])


def broll(sents, model=None, trace=None, chunk=120, brands=()):
    parts = [sents[i:i + chunk] for i in range(0, len(sents), chunk)]

    def one(part):
        return _run("broll", broll_messages(part), BROLL_SCHEMA, None, lambda o: broll_post(o, part, brands),
                    lambda: {"items": []}, model, trace if len(parts) == 1 else None)
    res = ai.map_parallel(one, parts) if len(parts) > 1 else [one(parts[0])]
    items, warns, stats = [], [], []
    for r in res:
        if isinstance(r, Exception):
            warns.append(str(r))
            continue
        items += r["result"]["items"]
        warns += r["warnings"]
        stats.append(r["stats"])
    src = "ai" if items else "fallback"
    if not items:
        warns.append("B-roll butuh AI (offline): tidak ada saran")
    return {"source": src, "result": {"items": items, "picks": broll_pick(items, sents)}, "warnings": warns,
            "stats": stats}


# ====================================================================== (e) repeated / bad takes

def repeat_candidates(dwords, sents, window=3, max_span_s=40.0, sim_min=0.45):
    """Deterministic detection. Returns groups: {kind, takes:[{w0,w1,text,start,end}]}.
    kind = 'restart' (similar consecutive units), 'stutter' (same 1-3 words repeated back-to-back),
    'loop' (same n-gram >=3x: Whisper hallucination in silence or real stammer)."""
    groups, used = [], set()
    toks = [[norm(w) for w in s["text"].split()] for s in sents]
    for i in range(len(sents)):
        if i in used or len(toks[i]) < 2:
            continue
        grp = [i]
        for j in range(i + 1, min(len(sents), i + 1 + window)):
            if sents[j]["start"] - sents[grp[-1]]["end"] > max_span_s:
                break
            a, b = toks[grp[-1]], toks[j]
            r = difflib.SequenceMatcher(None, a, b).ratio()
            k = min(len(a), len(b), 4)
            prefix = k >= 2 and a[:k] == b[:k]
            sorry = any(t in ("sorry", "maaf", "salah", "ulang") for t in b[:3])
            if r >= sim_min or prefix or (sorry and r >= 0.3):
                grp.append(j)
        if len(grp) > 1:
            used.update(grp)
            groups.append({"kind": "restart", "takes": [
                {"w0": sents[k]["w0"], "w1": sents[k]["w1"], "text": sents[k]["text"],
                 "start": sents[k]["start"], "end": sents[k]["end"]} for k in grp]})
    # word level: back-to-back repeated n-grams (n=1..3); pick the n that covers the most words, ties -> small n
    i, nw = 0, len(dwords)
    while i < nw:
        hit = None
        for n in (1, 2, 3):
            seq = [norm(dwords[k]["text"]) for k in range(i, min(nw, i + n))]
            if len(seq) < n or not all(seq):
                continue
            reps, j = 1, i + n
            while j + n <= nw and [norm(dwords[k]["text"]) for k in range(j, j + n)] == seq                     and dwords[j]["start"] - dwords[j - 1]["end"] < 2.0:
                reps += 1
                j += n
            if reps >= 2 and (hit is None or n * reps > hit[0] * hit[1]):
                hit = (n, reps)
        if hit:
            n, reps = hit
            first, second = dwords[i], dwords[i + n]
            # "lihat di bawah ini. Ini adalah ...": same word closing one sentence and opening the next
            boundary = (n == 1 and reps == 2 and (first["seg_end"] or first["text"][-1:] in ".?!,")
                        and second["text"][:1].isupper() and not first["text"][:1].isupper())
            if not boundary:
                takes = [{"w0": i + r * n, "w1": i + r * n + n - 1,
                          "text": " ".join(dwords[k]["text"] for k in range(i + r * n, i + r * n + n)),
                          "start": dwords[i + r * n]["start"], "end": dwords[i + r * n + n - 1]["end"]}
                         for r in range(reps)]
                groups.append({"kind": "loop" if reps >= 3 else "stutter", "takes": takes})
            i += n * reps
        else:
            i += 1
    groups.sort(key=lambda g: g["takes"][0]["start"])
    return groups


REPEAT_SCHEMA = {"type": "object", "required": ["groups"], "properties": {
    "groups": {"type": "array", "items": {"type": "object", "required": ["g", "keep", "drop"], "properties": {
        "g": {"type": "integer", "minimum": 0},
        "keep": {"type": "array", "items": {"type": "string", "maxLength": 2}},
        "drop": {"type": "array", "items": {"type": "string", "maxLength": 2}},
        "why": {"type": "string"}}}}}}


def _ctx(dwords, w0, w1, n=8):
    before = " ".join(w["text"] for w in dwords[max(0, w0 - n):w0])
    after = " ".join(w["text"] for w in dwords[w1 + 1:w1 + 1 + n])
    return before, after


def repeats_messages(dwords, groups, offset=0):
    rows = []
    for g, grp in enumerate(groups):
        before, after = _ctx(dwords, grp["takes"][0]["w0"], grp["takes"][-1]["w1"])
        rows.append(f"G{g} ({grp['kind']}) before: \"…{before}\"")
        for k, t in enumerate(grp["takes"]):
            rows.append(f"  {chr(65 + k)} {ts(t['start'], True)}-{ts(t['end'], True)} \"{t['text']}\"")
        rows.append(f"  after: \"{after}…\"")
    sys_ = f"""{BASE}
Task: the speaker sometimes repeats himself (restarts a sentence, corrects himself, stutters). Decide which takes to cut.
Input: candidate groups G<n> with takes A, B, C... in time order, plus the words before and after.
Rules (decide each group separately, read the before/after context):
1. Real repeat = the speaker says the same thing again: restart, self-correction ("sorry", "eh maksudnya"), stutter ("gak usah gak usah"). Keep the ONE best take and drop the others. Best = complete + fluent + correct. A later FRAGMENT ("Ini saya", "di bagian ini") is NOT better than an earlier complete take: then keep the complete one.
2. NOT a repeat -> keep all, drop []: the takes carry different information (e.g. "sudah bayar, cek status" vs "refresh lagi karena sudah bayar"), intentional emphasis ("tolong, tolong banget"), Indonesian reduplication, a list, or the same word ending one sentence and starting the next ("lihat di bawah ini. Ini adalah ...").
3. Same words 3+ times in a row with no meaning (speech-recognition loop or stammer) -> keep only the last one if it fits the sentence, else keep none.
4. Every take letter appears in exactly one of keep / drop.
5. Write "why" FIRST (Indonesian, max 12 words, specific to this group), then decide.
Output: {{"groups":[{{"g":0,"why":"...","keep":["B"],"drop":["A"]}}]}}"""
    return [{"role": "system", "content": sys_}, {"role": "user", "content": "\n".join(rows)}]


def repeats_post(obj, groups, offset=0):
    st = {"groups": len(groups), "returned": 0, "incomplete": 0, "cuts": 0}
    dec = {}
    for g in obj["groups"]:
        k = g["g"]  # labels are local to the chunk
        if not 0 <= k < len(groups):
            continue
        labels = {chr(65 + n) for n in range(len(groups[k]["takes"]))}
        keep = [x.upper() for x in g["keep"] if x.upper() in labels]
        drop = [x.upper() for x in g["drop"] if x.upper() in labels and x.upper() not in keep]
        if set(keep) | set(drop) != labels:
            st["incomplete"] += 1
            drop = [x for x in sorted(labels) if x not in keep] if keep else drop
        st["returned"] += 1
        keep, drop, fixed = _fragment_guard(groups[k], keep, drop)
        st["guard_swaps"] = st.get("guard_swaps", 0) + fixed
        dec[k] = {"keep": keep, "drop": drop, "why": g.get("why", "")[:120]}
    out = []
    for k, grp in enumerate(groups):
        d = dec.get(k) or repeats_fallback([grp])["groups"][0]
        cuts = [{"w0": t["w0"], "w1": t["w1"], "start": t["start"], "end": t["end"], "text": t["text"]}
                for n, t in enumerate(grp["takes"]) if chr(65 + n) in d["drop"]]
        st["cuts"] += len(cuts)
        out.append({"g": k + offset, "kind": grp["kind"], "takes": grp["takes"], "keep": d["keep"],
                    "drop": d["drop"], "why": d.get("why", ""), "cuts": cuts,
                    "auto": _auto_ok(grp)})  # False -> show as suggestion, user confirms in the panel
    return {"groups": out}, [], st


def _fragment_guard(grp, keep, drop):
    """Models (grok-fast / grok-3 especially) often keep a trailing fragment ("Ini saya") over the complete
    earlier take. If a kept take's words are all inside a dropped take that is >= 1.5x longer, swap them."""
    if grp["kind"] != "restart" or len(keep) != 1 or not drop:
        return keep, drop, 0
    tk = {chr(65 + n): [norm(w) for w in t["text"].split()] for n, t in enumerate(grp["takes"])}
    k = keep[0]
    for dlab in drop:
        if len(tk[dlab]) >= 1.5 * len(tk[k]) and set(tk[k]) <= set(tk[dlab]):
            return [dlab], [x for x in drop if x != dlab] + [k], 1
    return keep, drop, 0


def _auto_ok(grp):
    """Safe to apply without review: stutters/loops, or restarts that are near-identical / explicitly 'sorry'."""
    if grp["kind"] in ("stutter", "loop"):
        return True
    a, b = grp["takes"][0]["text"], grp["takes"][-1]["text"]
    return _sim(a, b) >= 0.6 or any(norm(w) in ("sorry", "maaf") for t in grp["takes"] for w in t["text"].split())


def repeats_fallback(groups):
    """No AI: stutters/loops keep the last repetition; restarts keep the last take only when nearly identical."""
    out = []
    for k, grp in enumerate(groups):
        labels = [chr(65 + n) for n in range(len(grp["takes"]))]
        if grp["kind"] in ("stutter", "loop"):
            keep = labels[-1:]
        else:
            a, b = grp["takes"][0]["text"], grp["takes"][-1]["text"]
            keep = labels[-1:] if _sim(a, b) >= 0.7 or norm(b).startswith(norm(a)[:12]) else labels
        drop = [x for x in labels if x not in keep]
        cuts = [{"w0": t["w0"], "w1": t["w1"], "start": t["start"], "end": t["end"], "text": t["text"]}
                for n, t in enumerate(grp["takes"]) if labels[n] in drop]
        out.append({"g": k, "kind": grp["kind"], "takes": grp["takes"], "keep": keep, "drop": drop,
                    "why": "fallback", "cuts": cuts})
    return {"groups": out}


def repeats(dwords, sents, model=None, trace=None, chunk=40):
    groups = repeat_candidates(dwords, sents)
    if not groups:
        return {"source": "none", "result": {"groups": []}, "warnings": [], "stats": [{"groups": 0}]}
    out, warns, stats, src = [], [], [], "ai"
    for off in range(0, len(groups), chunk):
        part = groups[off:off + chunk]
        r = _run("repeats", repeats_messages(dwords, part, off), REPEAT_SCHEMA, None,
                 lambda o, p=part, f=off: repeats_post(o, p, f),
                 lambda p=part, f=off: {"groups": [dict(g, g=g["g"] + f) for g in repeats_fallback(p)["groups"]]},
                 model, trace)
        out += r["result"]["groups"]
        warns += r["warnings"]
        stats.append(r["stats"])
        src = src if r["source"] == "ai" else "fallback"
    return {"source": src, "result": {"groups": out}, "warnings": warns, "stats": stats}


# ====================================================================== (f) contextual filler words

FILLER_ALWAYS = {"eh", "ehm", "em", "emm", "hmm", "hm", "uh", "um", "umm", "eee", "ee", "eeh", "mmm", "anu"}
# Verbal tics: grok-auto labelled 100% of them filler on the 34.6-min test (ya 72/72, guys 61/61, oke 50/50,
# nah 23/23, nih 23/23, kan 19/19, sih 7/7). Sending them to the AI costs quota and adds nothing: the panel
# shows one toggle per tic word instead. Only AMBIGUOUS words go to the AI.
FILLER_TICS = {"ya", "yah", "guys", "oke", "ok", "nah", "nih", "kan", "sih", "deh", "dong", "tuh"}
FILLER_CONTEXT = {"nah", "ya", "yah", "jadi", "gitu", "gini", "kayak", "apa", "oke", "ok", "sih", "deh", "kan",
                  "tuh", "nih", "terus", "pokoknya", "intinya", "sebenarnya", "sebenernya", "guys", "dong",
                  "misalkan", "basically", "istilahnya", "maksudnya", "sorry", "baik", "gimana"}
FILLER_BIGRAMS = {("apa", "namanya"), ("kayak", "gitu"), ("ya", "kan"), ("gimana", "ya"), ("apa", "ya"),
                  ("oke", "ya"), ("nah", "ini")}


def filler_candidates(dwords):
    out, i = [], 0
    while i < len(dwords):
        a = norm(dwords[i]["text"])
        b = norm(dwords[i + 1]["text"]) if i + 1 < len(dwords) else ""
        if (a, b) in FILLER_BIGRAMS:
            out.append({"w0": i, "w1": i + 1, "always": False, "tier": "ambiguous"})
            i += 2
            continue
        if a in FILLER_ALWAYS or a in FILLER_CONTEXT:
            out.append({"w0": i, "w1": i, "always": a in FILLER_ALWAYS})
            out[-1]["tier"] = "always" if a in FILLER_ALWAYS else ("tic" if a in FILLER_TICS else "ambiguous")
        i += 1
    for c in out:
        w0, w1 = c["w0"], c["w1"]
        c["text"] = " ".join(dwords[k]["text"] for k in range(w0, w1 + 1))
        c["start"], c["end"] = dwords[w0]["start"], dwords[w1]["end"]
        c["gap_before"] = round(c["start"] - dwords[w0 - 1]["end"], 2) if w0 else 9.0
        c["gap_after"] = round(dwords[w1 + 1]["start"] - c["end"], 2) if w1 + 1 < len(dwords) else 9.0
    return out


FILLER_SCHEMA = {"type": "object", "required": ["d"], "properties": {
    "d": {"type": "array", "items": {"type": "object", "required": ["c", "w", "label"], "properties": {
        "c": {"type": "integer", "minimum": 0}, "w": {"type": "string"},
        "label": {"type": "string", "enum": ["filler", "meaning"]}}}}}}


def filler_messages(dwords, cands, offset=0, style="natural"):
    rows = []
    for k, c in enumerate(cands):
        before, after = _ctx(dwords, c["w0"], c["w1"], 7)
        rows.append(f"c{k}: …{before} [[{c['text']}]] {after}… "
                    f"(pause before {c['gap_before']:.1f}s, after {c['gap_after']:.1f}s)")
    strict = ("Style AGGRESSIVE: label every discourse word that adds no information as filler."
              if style == "aggressive" else "Style NATURAL: when unsure, label it meaning (keep). Casual tone is fine.")
    sys_ = f"""{BASE}
Task: label each marked word [[...]] as "filler" (safe to cut out of the audio) or "meaning" (must stay).
Test: read the sentence without the marked word. If it still says exactly the same thing and sounds natural -> filler. If the meaning, grammar or question changes -> meaning.
Examples:
- "Gabung [[jadi]] reseller" -> meaning ("jadi" = become)
- "Untuk [[apa?]] Untuk nanti masuk ke grup" -> meaning (real question)
- "[[Jadi]] kita akan masuk ke pengaturan" -> filler
- "isi email yang aktif [[ya]] karena nanti" -> filler
- "terus kamu klik [[nah]] ini dia tombolnya" -> filler
- "kalau [[misalkan]] kalian sudah punya akun" -> filler ("kalau" alone keeps the meaning)
- "[[Baik]], pertama kali kita akan daftar" -> filler (opening word)
- "isi semuanya dengan [[baik]]" -> meaning ("dengan baik" = properly)
- "harganya [[kayak]] gini" -> meaning ("like this")
- "nomornya wajib aktif [[guys]]" -> filler
{strict}
Return one entry for EVERY candidate c<n>, in order; "w" = the marked word(s) copied exactly.
Output: {{"d":[{{"c":0,"w":"nah","label":"filler"}}]}}"""
    return [{"role": "system", "content": sys_}, {"role": "user", "content": "\n".join(rows)}]


def filler_post(obj, cands, offset=0):
    st = {"cands": len(cands), "returned": 0, "cut": 0, "realigned": 0, "rejected": 0}
    dec = {}
    for d in obj["d"]:
        k = d["c"]  # labels are local to the chunk
        if not 0 <= k < len(cands):
            continue
        if norm(d.get("w", "")) != norm(cands[k]["text"]):  # numbering drift: look for the echoed word nearby
            near = [j for j in (k - 1, k + 1, k - 2, k + 2) if 0 <= j < len(cands)
                    and norm(cands[j]["text"]) == norm(d.get("w", "")) and j not in dec]
            if not near:
                st["rejected"] += 1
                continue
            k = near[0]
            st["realigned"] += 1
        if k not in dec:
            dec[k] = d["label"] == "filler"
            st["returned"] += 1
    out = []
    for k, c in enumerate(cands):
        cut = dec.get(k, c["always"])
        st["cut"] += cut
        out.append({**c, "c": k + offset, "cut": cut, "decided_by": "ai" if k in dec else "rule"})
    st["coverage"] = round(st["returned"] / max(1, len(cands)), 3)
    return {"candidates": out}, [], st


def filler_cut_ranges(cands, dwords, pad=0.02, min_word=0.12, min_pause=0.25):
    """Turn cut decisions into source time ranges; absorb the pause on the side that has one.
    Words shorter than min_word with no pause >= min_pause next to them are left in: a 60 ms cut only
    creates a jump cut / audio click and saves nothing (Whisper often gives "ya" 0.06 s)."""
    ranges = []
    for c in cands:
        if not c["cut"]:
            continue
        if c["end"] - c["start"] < min_word and max(c["gap_before"], c["gap_after"]) < min_pause:
            continue
        s, e = c["start"] - pad, c["end"] + pad
        if c["gap_after"] > c["gap_before"]:
            e = c["end"] + min(c["gap_after"], 0.6) - 0.08
        else:
            s = c["start"] - min(c["gap_before"], 0.6) + 0.08
        ranges.append((round(max(0, s), 3), round(e, 3)))
    return ranges


def fillers(dwords, model=None, trace=None, style="natural", chunk=60, cut_tics=("ya", "guys", "oke", "nah", "nih"),
            ai_tics=False):
    """Tiered: 'always' (eh/em/anu) -> cut by rule; 'tic' -> cut if the word is in cut_tics (panel toggles);
    'ambiguous' (jadi, apa, kayak, baik, terus, misalkan, gitu, sorry...) -> AI decides from context.
    ai_tics=True sends tics to the AI too (the old behaviour; ~4x more requests)."""
    all_c = filler_candidates(dwords)
    if not all_c:
        return {"source": "none", "result": {"candidates": [], "ranges": []}, "warnings": [], "stats": []}
    tics = {norm(t) for t in cut_tics}
    rule = [] if ai_tics else [{**c, "cut": c["tier"] == "always" or norm(c["text"]) in tics, "decided_by": "rule"}
                               for c in all_c if c["tier"] in ("always", "tic")]
    cands = all_c if ai_tics else [c for c in all_c if c["tier"] == "ambiguous"]
    if not cands:
        return {"source": "rule", "result": {"candidates": rule, "ranges": filler_cut_ranges(rule, dwords)},
                "warnings": [], "stats": []}
    out, warns, stats, srcs = [], [], [], set()
    parts = [(off, cands[off:off + chunk]) for off in range(0, len(cands), chunk)]

    def one(p):
        off, part = p
        return _run("filler", filler_messages(dwords, part, off, style), FILLER_SCHEMA, None,
                    lambda o: filler_post(o, part, off),
                    lambda: {"candidates": [{**c, "c": off + k, "cut": c["always"], "decided_by": "rule"}
                                            for k, c in enumerate(part)]},
                    model, trace if len(parts) == 1 else None)
    for r in (ai.map_parallel(one, parts) if len(parts) > 1 else [one(parts[0])]):
        if isinstance(r, Exception):
            warns.append(str(r))
            continue
        out += r["result"]["candidates"]
        warns += r["warnings"]
        stats.append(r["stats"])
        srcs.add(r["source"])
    src = srcs.pop() if len(srcs) == 1 else "mixed"
    out = sorted(out + rule, key=lambda c: c["w0"])
    return {"source": src, "result": {"candidates": out, "ranges": filler_cut_ranges(out, dwords)},
            "warnings": warns, "stats": stats}


# ====================================================================== (g) caption text cleanup (timing-safe)

CLEAN_SCHEMA = {"type": "object", "required": ["edits"], "properties": {
    "edits": {"type": "array", "items": {"type": "object", "required": ["from", "to", "old", "new"], "properties": {
        "from": {"type": "integer", "minimum": 0}, "to": {"type": "integer", "minimum": 0},
        "old": {"type": "string"}, "new": {"type": "string", "minLength": 1, "maxLength": 80}}}}}}


def cleanup_messages(dwords, w0, w1, glossary=()):
    rows, cur = [], []
    for k in range(w0, w1 + 1):
        cur.append(f"{k - w0}:{dwords[k]['text']}")
        if dwords[k]["seg_end"] or len(cur) >= 16:
            rows.append(" ".join(cur))
            cur = []
    if cur:
        rows.append(" ".join(cur))
    gl = ", ".join(glossary) if glossary else "(none)"
    sys_ = f"""{BASE}
Task: clean up caption text WITHOUT changing what was said. Words are numbered: <index>:<word>.
Fix only:
- capitalization (sentence starts, names, brands) and punctuation (, . ?) attached to words;
- obvious speech-recognition mistakes, using context and this glossary of correct names: {gl};
- standard spelling of known terms (WhatsApp, Cloudflare, Telegram, DNS, SSL, API, QRIS).
Do NOT: formalize casual words (udah, gak, aja, guys, kamu, banget stay), translate, remove fillers, reorder, summarize.
An edit replaces the words from..to (inclusive, max 4 words) with "new"; "old" = the exact original words from..to.
Merging words is allowed when they are one misheard name (e.g. "toko Kitah" -> "TokoKita").
List ONLY changed words; if nothing needs fixing return {{"edits":[]}}.
Output: {{"edits":[{{"from":0,"to":0,"old":"...","new":"..."}}]}}"""
    return [{"role": "system", "content": sys_}, {"role": "user", "content": "\n".join(rows)}]


def _strip_punct(t):
    return re.sub(r"[^\w-]+", "", t.lower())


def cleanup_post(obj, dwords, w0, w1, glossary=()):
    gl = {norm(g) for g in glossary}
    st = {"edits": len(obj["edits"]), "applied": 0, "moved": 0, "rejected_anchor": 0, "rejected_rewrite": 0,
          "rejected_overlap": 0, "case_punct": 0, "word_fix": 0, "merge": 0}
    span_words = lambda a, b: " ".join(dwords[k]["text"] for k in range(a, b + 1))  # noqa: E731
    accepted, taken = [], set()
    for e in sorted(obj["edits"], key=lambda e: e["from"]):
        a, b = e["from"] + w0, e["to"] + w0
        if not (w0 <= a <= b <= w1) or b - a > 4 or norm(span_words(a, b)) != norm(e["old"]):
            hit = None  # re-anchor by the old text near the claimed position
            n = len(e["old"].split()) or 1
            for d in sorted(range(-8, 9), key=abs):
                aa = e["from"] + w0 + d
                if w0 <= aa and aa + n - 1 <= w1 and norm(span_words(aa, aa + n - 1)) == norm(e["old"]):
                    hit = (aa, aa + n - 1)
                    break
            if not hit:
                st["rejected_anchor"] += 1
                continue
            a, b = hit
            st["moved"] += 1
        old, new = span_words(a, b), e["new"].strip()
        if old == new:
            continue
        if any(k in taken for k in range(a, b + 1)):
            st["rejected_overlap"] += 1
            continue
        same_letters = _strip_punct(old).replace("-", "") == _strip_punct(new).replace("-", "")
        if not same_letters and norm(new) not in gl and _sim(old, new) < 0.5:
            st["rejected_rewrite"] += 1
            continue
        if b > a and len(new.split()) < b - a + 1 and not same_letters and norm(new) not in gl:
            st["rejected_rewrite"] += 1  # dropping a spoken word ("Puncat daftar jadi" -> "Daftar jadi")
            continue
        kind = "case_punct" if same_letters else ("merge" if b > a else "word_fix")
        st[kind] += 1
        st["applied"] += 1
        taken.update(range(a, b + 1))
        accepted.append({"from": a, "to": b, "old": old, "new": new, "kind": kind})
    return {"edits": accepted}, [], st


def apply_edits(dwords, edits):
    """New display-word list with the same timeline: a span edit keeps [start(from), end(to)]; when the new
    text has the same word count as the span the original per-word timings are kept, otherwise the span
    time is split proportionally to character length."""
    by_from = {e["from"]: e for e in edits}
    out, k = [], 0
    while k < len(dwords):
        e = by_from.get(k)
        if not e:
            out.append(dict(dwords[k]))
            k += 1
            continue
        span = dwords[e["from"]:e["to"] + 1]
        parts = e["new"].split()
        if len(parts) == len(span):
            out += [{**w, "text": p} for w, p in zip(span, parts)]
        else:
            s, t_end = span[0]["start"], span[-1]["end"]
            total = sum(len(p) for p in parts) or 1
            acc = s
            for p in parts:
                d = (t_end - s) * len(p) / total
                out.append({"i": None, "text": p, "start": round(acc, 3), "end": round(acc + d, 3),
                            "seg_end": 0, "toks": []})
                acc += d
            out[-1]["seg_end"] = span[-1]["seg_end"]
        k = e["to"] + 1
    for n, w in enumerate(out):
        w["i"] = n
    return out


def cleanup(dwords, glossary=(), model=None, trace=None, chunk=350):
    parts = [(a, min(len(dwords) - 1, a + chunk - 1)) for a in range(0, len(dwords), chunk)]

    def one(p):
        a, b = p
        return _run("cleanup", cleanup_messages(dwords, a, b, glossary), CLEAN_SCHEMA, None,
                    lambda o: cleanup_post(o, dwords, a, b, glossary), lambda: {"edits": []}, model,
                    trace if len(parts) == 1 else None)
    edits, warns, stats, src = [], [], [], "ai"
    for r in (ai.map_parallel(one, parts) if len(parts) > 1 else [one(parts[0])]):
        if isinstance(r, Exception):
            warns.append(str(r))
            continue
        edits += r["result"]["edits"]
        warns += r["warnings"]
        stats.append(r["stats"])
        src = src if r["source"] == "ai" else "mixed"
    return {"source": src, "result": {"edits": edits}, "warnings": warns, "stats": stats}


# ---------------------------------------------------------------------- (g2) cleanup v2: rewrite rows + local alignment
# LLMs are better at rewriting a line than at listing sparse edits (v1 returned 0-2 edits on a transcript
# full of errors). v2 asks for the corrected text of every row and aligns it word-by-word locally, so the
# Whisper timing never comes from the model. Deleted / inserted words are rejected (spoken words stay).

CLEAN2_SCHEMA = {"type": "object", "required": ["lines"], "properties": {
    "lines": {"type": "array", "items": {"type": "string"}}}}


def _sent_rows(dwords, w0, w1, sents):
    """Rows = sentence units (from transcript.sentences) clipped to [w0, w1]."""
    return [list(range(max(s["w0"], w0), min(s["w1"], w1) + 1)) for s in sents if s["w1"] >= w0 and s["w0"] <= w1]


def cleanup2_messages(dwords, rows, glossary=()):
    gl = ", ".join(glossary) if glossary else "(none)"
    body = "\n".join(" ".join(dwords[k]["text"] for k in row) for row in rows)
    sys_ = f"""{BASE}
Task: proofread auto-generated Indonesian captions. Input: one spoken phrase per line (a line can stop mid-sentence and continue on the next line).
Return the corrected text, one output line per input line, same order:
- Sentence case: capital letter only at the start of a sentence and for names/brands; lowercase words that were capitalized mid-sentence ("Dan didengarkan Baik-baik" -> "dan didengarkan baik-baik").
- Punctuation: add commas, periods and question marks only where a clause or sentence really ends; do not put a period at the end of a line that continues on the next line.
- Fix words the speech recognizer misheard, using context: e.g. "pecet daftar" -> "pencet daftar", "whatsappnya" -> "WhatsApp-nya", brand names in this glossary: {gl}. Standard spelling for WhatsApp, YouTube, Google, Cloudflare, Telegram, DNS, SSL, API, QRIS. If unsure what was said, leave the word unchanged.
- Keep EVERY spoken word in the same order, including casual words and fillers (udah, gak, aja, guys, ya, nah, misalkan). Do not formalize, translate, summarize, add or remove words.
Output: {{"lines":["...", "..."]}}"""
    return [{"role": "system", "content": sys_}, {"role": "user", "content": body}]


def _align(dwords, idx, new_text, gl, st):
    """Global difflib alignment of original words (display idx list) vs corrected text -> span edits.
    Line structure of the reply is ignored, so line drift / merged lines cannot misplace an edit."""
    old_w = [dwords[k]["text"] for k in idx]
    new_w = new_text.split()
    sm = difflib.SequenceMatcher(None, [_strip_punct(w) for w in old_w], [_strip_punct(w) for w in new_w],
                                 autojunk=False)
    edits = []
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == "equal":
            for i, j in zip(range(i1, i2), range(j1, j2)):
                if old_w[i] != new_w[j]:
                    edits.append({"from": idx[i], "to": idx[i], "old": old_w[i], "new": new_w[j], "kind": "case_punct"})
        elif op == "replace":
            if i2 - i1 == j2 - j1:  # 1:1 word fixes
                for i, j in zip(range(i1, i2), range(j1, j2)):
                    same = _strip_punct(old_w[i]).replace("-", "") == _strip_punct(new_w[j]).replace("-", "")
                    if same or norm(new_w[j]) in gl or _sim(old_w[i], new_w[j]) >= 0.5:
                        edits.append({"from": idx[i], "to": idx[i], "old": old_w[i], "new": new_w[j],
                                      "kind": "case_punct" if same else "word_fix"})
                    else:
                        st["rejected_rewrite"] += 1
            else:
                old_s, new_s = " ".join(old_w[i1:i2]), " ".join(new_w[j1:j2])
                same = norm(old_s) == norm(new_s)
                if same or norm(new_s) in gl or (i2 - i1 <= 4 and j2 - j1 >= 1 and _sim(old_s, new_s) >= 0.8):
                    edits.append({"from": idx[i1], "to": idx[i2 - 1], "old": old_s, "new": new_s,
                                  "kind": "case_punct" if same else "merge"})
                else:
                    st["rejected_rewrite"] += 1
        elif op == "delete":
            st["rejected_delete"] += i2 - i1
        elif op == "insert":
            st["rejected_insert"] += j2 - j1
    return edits


def cleanup2_post(obj, dwords, rows, glossary=()):
    gl = {norm(g) for g in glossary}
    st = {"rows": len(rows), "lines_back": len(obj["lines"]), "applied": 0, "case_punct": 0, "word_fix": 0,
          "merge": 0, "rejected_rewrite": 0, "rejected_delete": 0, "rejected_insert": 0}
    idx = [k for row in rows for k in row]
    edits = _align(dwords, idx, " ".join(obj["lines"]), gl, st)
    for e in edits:
        st[e["kind"]] += 1
        st["applied"] += 1
    st["word_coverage"] = round(1 - st["rejected_delete"] / max(1, len(idx)), 3)
    return {"edits": edits}, [], st


def cleanup2(dwords, sents, glossary=(), model=None, trace=None, words_per_call=400):
    parts, a = [], 0
    while a < len(dwords):
        b = min(len(dwords) - 1, a + words_per_call - 1)
        if b < len(dwords) - 1:  # end the chunk at a sentence boundary
            ends = [s["w1"] for s in sents if a <= s["w1"] <= b]
            b = ends[-1] if ends else b
        parts.append(_sent_rows(dwords, a, b, sents))
        a = b + 1

    def one(rows):
        r = None
        for _ in range(2):  # grok-fast sometimes echoes the input unchanged (1 of 2 runs on the 49 s clip)
            r = _run("cleanup2", cleanup2_messages(dwords, rows, glossary), CLEAN2_SCHEMA, None,
                     lambda o: cleanup2_post(o, dwords, rows, glossary), lambda: {"edits": []}, model,
                     trace if len(parts) == 1 else None)
            if r["source"] != "ai" or r["stats"].get("applied") or sum(len(x) for x in rows) < 40:
                break
            r["warnings"].append(tr("ai.noChangeRetry"))
        return r
    edits, warns, stats, srcs = [], [], [], set()
    for r in (ai.map_parallel(one, parts) if len(parts) > 1 else [one(parts[0])]):
        if isinstance(r, Exception):
            warns.append(str(r))
            continue
        edits += r["result"]["edits"]
        warns += r["warnings"]
        stats.append(r["stats"])
        srcs.add(r["source"])
    return {"source": srcs.pop() if len(srcs) == 1 else "mixed", "result": {"edits": edits}, "warnings": warns,
            "stats": stats}


# ====================================================================== (h) title / description / hashtags

META_SCHEMA = {"type": "object", "required": ["titles", "description", "hashtags", "tags"], "properties": {
    "titles": {"type": "array", "minItems": 3, "maxItems": 5, "items": {"type": "string", "minLength": 10, "maxLength": 100}},
    "description": {"type": "string", "minLength": 80, "maxLength": 1500},
    # No "^#\w+$" pattern: grok-fast often drops the "#" (seen live: 'shopee', 'gpt') even after the repair
    # request, which failed the whole call. meta_post() normalises every hashtag to "#" + word characters.
    "hashtags": {"type": "array", "minItems": 3, "maxItems": 15, "items": {"type": "string", "minLength": 1, "maxLength": 60}},
    "tags": {"type": "array", "minItems": 5, "maxItems": 20, "items": {"type": "string", "maxLength": 40}}}}


def _digest(sents, max_chars=30000):
    """Plain transcript text, evenly sampled down to max_chars for very long videos."""
    lines = [s["text"] for s in sents]
    total = sum(len(x) + 1 for x in lines)
    if total <= max_chars:
        return "\n".join(lines)
    step = total / max_chars
    return "\n".join(lines[int(i * step)] for i in range(int(len(lines) / step)))


def meta_messages(sents, chapters_list=None, platform="youtube", glossary=()):
    ch = ""
    if chapters_list:
        ch = "\nChapters:\n" + "\n".join(f"{ts(c['start'])} {c['title']}" for c in chapters_list)
    gl = f"\nKnown names (correct spelling): {', '.join(glossary)}." if glossary else ""
    sys_ = f"""{BASE}
Task: write {platform} publishing metadata for this Indonesian video.
Rules:
1. titles: 3 options, Indonesian, max 70 characters each, main keyword early, specific and honest (no fake claims), different angles (how-to, benefit, curiosity).
2. description: Indonesian, 2-4 short paragraphs, max 800 characters: what the viewer learns, who it is for, a call to action. Do not include chapters or hashtags in it.
3. hashtags: 5-10, each "#" + one word without spaces, lowercase, relevant (Indonesian and common English mix).
4. tags: 8-15 search keywords/phrases, lowercase, Indonesian.{gl}
Output: {{"titles":["..."],"description":"...","hashtags":["#..."],"tags":["..."]}}"""
    return [{"role": "system", "content": sys_},
            {"role": "user", "content": "Transcript:\n" + _digest(sents) + ch}]


def meta_post(obj, chapters_list=None):
    st = {"titles_over_70": sum(len(t) > 70 for t in obj["titles"])}
    tags, seen = [], set()
    for h in obj["hashtags"]:
        h = "#" + re.sub(r"[^\w]", "", h.lower())
        if len(h) > 1 and h not in seen:
            seen.add(h)
            tags.append(h)
    desc = obj["description"].strip()
    if chapters_list:
        desc += "\n\n" + "\n".join(f"{ts(c['start'])} {c['title']}" for c in chapters_list)
    desc += "\n\n" + " ".join(tags[:3])  # YouTube shows the first 3 hashtags above the title
    kw = [t.strip().lower() for t in obj["tags"] if t.strip()]
    while sum(len(t) + 1 for t in kw) > 500:  # YouTube tag field limit
        kw.pop()
    return {"titles": [t.strip()[:100] for t in obj["titles"]], "description": desc, "hashtags": tags,
            "tags": kw}, [], st


def meta_fallback(sents, name="Video"):
    return {"titles": [name], "description": "", "hashtags": [], "tags": []}


def meta(sents, chapters_list=None, platform="youtube", model=None, trace=None, glossary=(), name="Video"):
    return _run("meta", meta_messages(sents, chapters_list, platform, glossary), META_SCHEMA, None,
                lambda o: meta_post(o, chapters_list), lambda: meta_fallback(sents, name), model, trace)
