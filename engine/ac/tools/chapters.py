"""Auto Chapters (tool id "chapters"): YouTube chapters + sequence chapter markers from the transcript.

Actions
  analyze  timeline words (scope aware) -> sentence units -> hallucination filter -> ~30 s blocks ->
           ~10-min parts asked IN PARALLEL (grok-fast, "mm:ss text" lines; the model answers mm:ss + quote,
           resolved locally to Whisper sentence starts) -> YouTube rules (first at 00:00, >= 10 s, sorted) ->
           merge_to_count (deterministic: the count preset always holds) -> one title polish call when the
           video had > 1 part (consistent style) -> review file chapters_review.json.
           Without AI (proxy down, setting off, params.ai false): topic segmentation from pauses, vocabulary
           shift and cue words ("selanjutnya", "langkah", ...); titles = distinctive keywords, flagged
           `generic` so the panel asks the user to check them.
  apply    edited review (job.review: items t0/title/on) -> validated list, YouTube text, <workdir>/
           chapters_youtube.txt, plan {"kind": "markers"} (type Chapter, cyan, tag [Klipora-CH], end = next start;
           the host also replaces/clears markers of older versions tagged [AC-CH]).
  meta     one AI call: 3 titles, description (+ chapters + 3 hashtags), hashtags, tags (ac.ai.tasks "meta").
  retitle  one AI call: rewrite the titles of the edited list (style + instruction), same count and order.

Params (analyze): count "less"|"normal"|"more"|int, hint (str), style "langkah"|"netral"|"menarik",
  min_len (s, default 10), ai (bool, default true), polish (bool, default true), scope (panel dict).
Times are SEQUENCE seconds everywhere; the YouTube text is relative to the scope start (`offset`).
User-facing text (stage labels, warnings, issues, errors, summaries, default titles such as the first chapter
"Pembuka" / "Intro") follows the job language (ac.i18n, keys chapters.*). AI prompts are content and stay as they are.
Research: docs/research/product_ai.md section 2 (split parts + merge_to_count, measured 6/6 full coverage on the
34.6 min tutorial), internal AI research notes sections 1-5 (mm:ss + quote, never integer ids).
"""
from __future__ import annotations

import math
import re
import time
from collections import Counter
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait

from .. import ranges as R
from .. import review as RV
from .. import transcript as T
from ..ai import client as ai
from ..ai.prompts import STOP, resolve
from ..ai.text import display_words, parse_ts, sentences as make_sentences, ts
from ..i18n import tr
from ..timeline import Timeline
from ..util import EngineError, setting, workdir

TITLE = "Auto Chapters"   # developer listing (cli.py tools); the panel UI title is the locale key chapters.title
DESCRIPTION = "Chapter titles and timestamps ready to paste into YouTube, plus chapter markers on the sequence."

TAG = "[Klipora-CH]"
TAG_ALTS = ("[Klipora-CH]", "[AC-CH]")   # markers written by older versions carry the old tag
COLOR = 7                 # Premiere marker colour index 7 = cyan (premiere_timeline_api.md section 3)
MARKER_TYPE = "Chapter"
YT_MIN = 10.0             # YouTube: every chapter >= 10 s, >= 3 chapters, first at 00:00
BLOCK_SEC = 30.0          # one prompt line ~ 30 s of speech (a 35-min talk = ~70 lines)
PART_SEC = 600.0          # ~10-min parts asked in parallel (fixes grok-fast front-loading)
PROMPT_V = "chapters-2026-10-05.1"
DEFAULT_MODEL = "grok-fast"
STYLES = {  # prompt text for the AI (content, not UI)
    "langkah": "judul langkah tutorial yang diawali kata kerja (contoh: \"Daftar Akun Reseller\", \"Atur Harga Produk\")",  # i18n-ignore (AI prompt)
    "netral": "judul deskriptif dan netral tentang isi bagian itu (contoh: \"Pengaturan Domain\", \"Harga dan Panel Reseller\")",  # i18n-ignore (AI prompt)
    "menarik": "judul menarik yang bikin penasaran tapi tetap jujur sesuai isi (contoh: \"Domain Sendiri Tanpa Ribet\", "  # i18n-ignore (AI prompt)
               "\"Trik Harga Reseller\")",
}
# Words that never make a good keyword title (on top of ac.ai.prompts.STOP). Spoken Indonesian tutorial filler,
# function words and generic verbs; nouns ("domain", "produk", "telegram") are what the fallback titles need.
_DULL = set((
    "klik pilih sini situ sana nanti terus kalian teman temen contoh contohnya misalnya bagian tinggal langsung "  # i18n-ignore (Indonesian speech lexicon)
    "dulu udah yaitu yakni bikin buat pakai pake gimana bagaimana sebelumnya selanjutnya berikutnya sekarang kemudian setelah "  # i18n-ignore (Indonesian speech lexicon)
    "biasanya kemarin tadinya kayaknya caranya gampang mudah bener benar banget sekali paling tetap lebih kurang masih belum "  # i18n-ignore (Indonesian speech lexicon)
    "sendiri semua setiap seperti sesuai hanya cuma saja tidak nggak enggak engga bukan iya oke okay okey yaudah yang mana "  # i18n-ignore (Indonesian speech lexicon)
    "apakah kenapa karena dengan tersebut itulah inilah begitu begini sampai tanpa sorry maaf butuh bakal istilah selesai "  # i18n-ignore (Indonesian speech lexicon)
    "dibaca kali juga ingin perlu pasti dapat dapet punya telah sedang lalu mirip beda lain lainnya doang terserah pokok "  # i18n-ignore (Indonesian speech lexicon)
    "bagi sebab akibat maka namun serta hingga sejak pada dalam luar atas bawah depan belakang samping antara besok hari "  # i18n-ignore (Indonesian speech lexicon)
    "waktu saat ketika selama sebelum sesudah segera cepat lambat pernah sering jarang kadang selalu jangan mantap siap betul "  # i18n-ignore (Indonesian speech lexicon)
    "salah coba cobain lihat liat tunggu bentar sebentar silakan tolong mohon terima kasih makasih cara orang kami mereka "  # i18n-ignore (Indonesian speech lexicon)
    "aku gue anda bro sis kak mbak bapak ibu yaa yah deh dong kalo kalau ngga gitu gini kayak udah sudah lagi masuk keluar "  # i18n-ignore (Indonesian speech lexicon)
    "ambil kasih taruh tulis ketik isi isikan masukkan masukin tekan buka tutup simpan save copy paste yang ini itu nya "  # i18n-ignore (Indonesian speech lexicon)
    "mungkin memang emang sebenarnya sebenernya beberapa banyak sedikit pertama kedua ketiga terakhir disini disana kesini "  # i18n-ignore (Indonesian speech lexicon)
    "tadi barusan habis abis kemudian makanya soalnya tuh nih sih kok aja saja juga apalagi seharusnya harusnya "  # i18n-ignore (Indonesian speech lexicon)
).split())
_CUE_STRONG = re.compile(r"^(selanjutnya|berikutnya|setelah itu|langkah|step|pertama|kedua|ketiga|keempat|kelima|"  # i18n-ignore (Indonesian speech cues)
                         r"terakhir|lanjut|masuk ke|sekarang kita|nah sekarang|oke sekarang|yang (kedua|ketiga|terakhir|"  # i18n-ignore (Indonesian speech cues)
                         r"selanjutnya|berikutnya))\b", re.I)
_CUE_WEAK = re.compile(r"^(oke|ok|baik|nah|jadi|terus|sekarang|kemudian)\b", re.I)


def has_tag(text):
    """True when a marker name/comment carries our chapter tag (new [Klipora-CH] or old [AC-CH])."""
    return any(t in str(text or "") for t in TAG_ALTS)


# ====================================================================== small helpers

def target_count(dur, mode="normal"):
    """Chapters for `dur` seconds of speech: ~1 per 4 min (3..15); Sedikit x0.6 (>= 3), Banyak x1.6 (<= 25)."""
    if isinstance(mode, (int, float)) and not isinstance(mode, bool) or str(mode).strip().isdigit():
        return max(1, min(50, int(mode)))
    base = max(3, min(15, round(dur / 240)))
    m = str(mode or "normal").lower()
    if m in ("less", "sedikit"):
        return max(3, round(base * 0.6))
    if m in ("more", "banyak"):
        return min(25, round(base * 1.6))
    return base


def clean_title(t, limit=70):
    """Model/user title -> display title: no numbering ("1.", "Bab 2:"), quotes, emoji-only, trailing period."""
    t = str(t or "")
    t = re.sub(r"^\s*(\d+\s*[.):-]\s*|(bab|chapter|bagian)\s*\d+\s*[:.)-]\s*)", "", t, flags=re.I)  # i18n-ignore (numbering in model titles)
    t = t.replace("—", "-").replace("–", "-").replace("\n", " ")
    t = " ".join(t.split()).strip(" .\"'`*#:;-“”‘’")
    return t[:limit].rstrip(" ,;:-")


def yt_time(t, hours=False):
    """YouTube timestamp: mm:ss, h:mm:ss when the video is >= 1 hour (all lines in the same format)."""
    t = int(max(0.0, t) + 1e-6)
    h, m, s = t // 3600, t // 60 % 60, t % 60
    return f"{h}:{m:02d}:{s:02d}" if hours else f"{m:02d}:{s:02d}"


def youtube_text(chs, offset=0.0):
    """'00:00 Judul' lines (times relative to the scope start)."""
    if not chs:
        return ""
    hours = chs[-1]["t"] - offset >= 3600
    return "\n".join(f"{yt_time(c['t'] - offset, hours)} {c['title']}" for c in chs)


def youtube_issues(chs, lo, hi):
    """YouTube chapter rules -> [{code, msg, i?}] (job language). Empty list = chapters will show on YouTube."""
    out = []
    if len(chs) < 3:
        out.append({"code": "FEW", "msg": tr("chapters.issueFew", n=len(chs))})
    if chs and chs[0]["t"] - lo > 0.5:
        out.append({"code": "FIRST", "msg": tr("chapters.issueFirst")})
    for k, c in enumerate(chs):
        end = chs[k + 1]["t"] if k + 1 < len(chs) else hi
        if end - c["t"] < YT_MIN - 1e-6:
            out.append({"code": "SHORT", "i": k, "msg": tr("chapters.issueShort", title=c["title"] or tr("chapters.untitled"),
                                                           s=int(round(max(0.0, end - c["t"]))))})
        if not c["title"].strip():
            out.append({"code": "EMPTY", "i": k, "msg": tr("chapters.issueEmpty", t=yt_time(c["t"] - lo))})
    return out


def _glossary():
    g = setting("glossary", []) or []
    if isinstance(g, str):
        g = re.split(r"[;,\n]", g)
    return [str(x).strip() for x in g if str(x).strip()][:40]


def _model():
    return setting("aiModel", "") or DEFAULT_MODEL


def _is_real(text):
    """False for Whisper outro hallucinations ("Terima kasih telah menonton"), their fragments and word loops."""
    low = text.lower()
    toks = [t.strip(".,!?") for t in low.split()]
    bare = " ".join(toks)
    if T.HALLU_RE.search(low):
        return False
    if len(bare) >= 6 and len(toks) <= 4 and any(bare in h for h in T.OUTRO_PHRASES):
        return False
    if len(toks) >= 4 and max(toks.count(t) for t in set(toks)) / len(toks) > 0.5:
        return False
    return True


def _words_in(words, scope_rs):
    return [w for w in words if R.contains(scope_rs, (w["t0"] + w["t1"]) / 2)]


def prepare(words, lo):
    """Timeline words -> real sentence units [{start, end, text, gap}] (hallucinations dropped)."""
    sents = make_sentences(display_words(words))
    prev, out = lo, []
    for s in sents:
        s["gap"] = max(0.0, s["start"] - prev)
        prev = s["end"]
        if _is_real(s["text"]):
            out.append({"start": s["start"], "end": s["end"], "text": s["text"], "gap": s["gap"]})
    return out


def make_blocks(S, target=BLOCK_SEC):
    """Merge sentences into ~target-second prompt lines; break at a pause (> 0.4 s) or past 1.5x target."""
    out, cur = [], []
    for i, s in enumerate(S):
        if cur and s["end"] - S[cur[0]]["start"] > target and (
                s["gap"] > 0.4 or s["end"] - S[cur[0]]["start"] > target * 1.5):
            out.append(cur)
            cur = []
        cur.append(i)
    if cur:
        out.append(cur)
    return [{"s0": b[0], "s1": b[-1], "start": S[b[0]]["start"], "end": S[b[-1]]["end"],
             "text": " ".join(S[i]["text"] for i in b)} for b in out]


def make_parts(blocks, dur, part_sec=PART_SEC):
    n = max(1, min(len(blocks), round(dur / part_sec)))
    size = -(-len(blocks) // n)
    return [blocks[a:a + size] for a in range(0, len(blocks), size)]


def validate(picks, S, lo, hi):
    """Sentence picks -> chapters [{t, i, title, ...}]: sorted, unique, first at lo, each start snapped to its
    sentence start minus a small pre-roll, >= 10 s apart, last chapter >= 10 s before the end."""
    out = []
    for c in sorted(picks, key=lambda c: c["i"]):
        s = S[c["i"]]
        t = lo if not out else max(lo, s["start"] - min(0.3, s["gap"] / 2))
        if out and (c["i"] == out[-1]["i"] or t - out[-1]["t"] < YT_MIN):
            continue
        out.append(dict(c, t=round(t, 3)))
    if out:
        out[0]["t"] = lo
    while len(out) > 1 and hi - out[-1]["t"] < YT_MIN:
        out.pop()
    return out


def merge_to_count(chs, end, n, min_len):
    """The LLM ignores the requested count: fold the shortest chapter into its shorter neighbour (keeping the
    longer one's title) until count <= n + 1 and every chapter >= min_len. Never below 3."""
    chs = [dict(c) for c in chs]

    def length(j):
        return (chs[j + 1]["t"] if j + 1 < len(chs) else end) - chs[j]["t"]

    while len(chs) > 3 and (len(chs) > n + 1 or min(length(j) for j in range(len(chs))) < min_len):
        j = min(range(len(chs)), key=length)
        k = 1 if j == 0 else j - 1 if j == len(chs) - 1 or length(j - 1) <= length(j + 1) else j + 1
        a, b = sorted((j, k))
        keep = chs[a] if length(a) >= length(b) else chs[b]
        merged = dict(chs[a], title=keep.get("title"), generic=keep.get("generic", False), src=keep.get("src"))
        chs[a] = merged
        del chs[b]
    return chs


def _tokens(text):
    """Content words: lower case, reduplication folded ("produk-produk" -> "produk"), "-nya" stripped."""
    for w in re.findall(r"[a-z][a-z0-9-]{2,}", text.lower()):
        w = w.split("-")[0] if "-" in w else w
        if w.endswith("nya") and len(w) > 6:
            w = w[:-3]
        if len(w) >= 4 and w not in STOP and w not in _DULL and not w.isdigit():
            yield w


def _bag(texts):
    return Counter(w for t in texts for w in _tokens(t))


def _cos(a, b):
    if not a or not b:
        return 0.0
    dot = sum(v * b.get(k, 0) for k, v in a.items())
    return dot / math.sqrt(sum(v * v for v in a.values()) * sum(v * v for v in b.values()))


# ====================================================================== rule-based fallback (no AI)

def boundary_scores(S, win=60.0):
    """[(score, sentence index)] for every possible chapter start (i >= 1): pause before (<= 4 s) + vocabulary
    shift between the 60 s before and after (1 - cosine of content-word bags) + cue words."""
    out = []
    for i in range(1, len(S)):
        t = S[i]["start"]
        before = _bag(s["text"] for s in S[max(0, i - 40):i] if s["start"] >= t - win)
        after = _bag(s["text"] for s in S[i:i + 40] if s["start"] < t + win)
        shift = 1.0 - _cos(before, after)
        cue = 0.35 if _CUE_STRONG.match(S[i]["text"]) else 0.12 if _CUE_WEAK.match(S[i]["text"]) else 0.0
        out.append((0.45 * min(S[i]["gap"], 4.0) / 4.0 + 0.35 * shift + cue, i))
    return out


def fallback_picks(S, n, lo, end, min_len, existing=None):
    """Topic boundaries without AI: greedy by boundary_scores() with spacing >= max(min_len, len/(2n)) and the last
    start >= min_len before the end. -> picks [{i, src: 'rule'}] (titles added later), sentence 0 included.
    existing = sentence indexes already chosen (AI picks): only NEW picks are returned, up to n chapters in total."""
    if not S:
        return []
    spacing = max(min_len, (end - lo) / (2 * max(1, n)))
    picks = sorted(set(existing or []) | {0})
    fixed = set(picks)
    for _, i in sorted(boundary_scores(S), reverse=True):
        if len(picks) >= n:
            break
        t = S[i]["start"]
        if end - t < min_len or i in fixed:
            continue
        if all(abs(t - S[j]["start"]) >= spacing for j in picks):
            picks.append(i)
    keep = [i for i in sorted(picks) if existing is None or i not in fixed]
    return [{"i": i, "src": "rule"} for i in keep]


def keyword_titles(chs, S, end):
    """Chapters without an AI title (rule picks) get the 3 most distinctive words of their part (tf-idf over all
    chapters) as title, flagged generic so the panel asks the user to check them."""
    bags = []
    for k, c in enumerate(chs):
        t1 = chs[k + 1]["t"] if k + 1 < len(chs) else end + 1
        bags.append(_bag(s["text"] for s in S if c["t"] - 0.5 <= s["start"] < t1))
    df = Counter(w for b in bags for w in b)
    total = Counter()
    for b in bags:
        total.update(b)
    n = len(bags)
    for k, c in enumerate(chs):
        if c.get("title") and c.get("src") != "rule":
            continue
        # distinctive = frequent here AND concentrated here (share of all its uses), rare in other chapters
        sc = sorted(((tf * (tf / total[w]) * (1.0 + math.log(n / df[w])), len(w), w) for w, tf in bags[k].items()
                     if tf >= 2 and (n == 1 or tf / total[w] >= 0.4)), reverse=True)
        words = [w for _, _, w in sc[:3]]
        c["title"] = ", ".join(w.capitalize() for w in words) if words else tr("chapters.partTitle", n=k + 1)
        c["generic"] = True
        c["keywords"] = words
    return chs


# ====================================================================== AI (parallel parts)

PART_SCHEMA = {"type": "object", "required": ["chapters"], "properties": {
    "chapters": {"type": "array", "maxItems": 40, "items": {
        "type": "object", "required": ["start", "title"], "properties": {
            "start": {"type": "string"}, "quote": {"type": "string"}, "title": {"type": "string", "minLength": 1}}}}}}

TITLES_SCHEMA = {"type": "object", "required": ["chapters"], "properties": {
    "chapters": {"type": "array", "minItems": 1, "items": {
        "type": "object", "required": ["title"], "properties": {
            "start": {"type": "string"}, "title": {"type": "string", "minLength": 1}}}}}}


def _gl_line(glossary):
    return f"\nNama yang benar (pakai ejaan ini): {', '.join(glossary)}." if glossary else ""  # i18n-ignore (AI prompt)


def part_messages(part, k, nparts, end, quota, hint="", style="langkah", glossary=()):
    first = ("Bab pertama video selalu baris pertama bagian ini." if k == 0 else  # i18n-ignore (AI prompt)
             "Kalau baris pertama bagian ini masih melanjutkan topik sebelumnya, JANGAN tandai baris itu.")  # i18n-ignore (AI prompt)
    sys_ = (
        f"""Kamu editor YouTube untuk kreator Indonesia. Ini SATU BAGIAN dari transkrip video yang lebih panjang.\n"""  # i18n-ignore (AI prompt)
        f"""Tiap baris kira-kira 30 detik: "mm:ss teks" (mm:ss = waktu baris itu mulai). Transkrip dari pengenal suara, bisa ada kata yang salah dengar.\n"""  # i18n-ignore (AI prompt)
        f"""Tugas: tandai baris tempat topik atau langkah BARU dimulai di bagian ini dan beri judul bab.\n"""  # i18n-ignore (AI prompt)
        f"""Aturan:\n"""  # i18n-ignore (AI prompt)
        f"""1. {first}\n"""  # i18n-ignore (AI prompt)
        f"""2. Pakai seluruh bagian sampai baris terakhir, jangan menumpuk bab di awal. Bab baru hanya kalau topik/langkah jelas berganti.\n"""  # i18n-ignore (AI prompt)
        f"""3. start: salin mm:ss baris itu persis seperti tertulis.\n"""  # i18n-ignore (AI prompt)
        f"""4. quote: salin 3-6 kata pertama baris itu persis seperti tertulis.\n"""  # i18n-ignore (AI prompt)
        f"""5. title: bahasa Indonesia, 2-6 kata, {STYLES.get(style, STYLES['langkah'])}. Spesifik sesuai isi, tanpa emoji, tanpa nomor, tanpa tanda kutip, tanpa titik di akhir. Perbaiki nama merek yang salah dengar.{_gl_line(glossary)}\n"""  # i18n-ignore (AI prompt)
        f"""Balas JSON saja: {{"chapters":[{{"start":"mm:ss","quote":"...","title":"..."}}]}}"""  # i18n-ignore (AI prompt)
    )
    user = (f"Bagian {ts(part[0]['start'])}-{ts(part[-1]['end'])} dari video {ts(end)} (bagian {k + 1} dari {nparts}). "  # i18n-ignore (AI prompt)
            f"Sekitar {quota} bab di bagian ini.")  # i18n-ignore (AI prompt)
    if hint:
        user += f"\nInstruksi user: {hint.strip()[:300]}"
    user += "\n\n" + "\n".join(f"{ts(b['start'])} {b['text']}" for b in part)
    return [{"role": "system", "content": sys_}, {"role": "user", "content": user}]


def titles_messages(chs, previews, style="langkah", hint="", glossary=()):
    sys_ = (
        f"""Kamu editor YouTube untuk kreator Indonesia. Ini daftar bab sebuah video: "mm:ss | judul sementara | awal isi bab".\n"""  # i18n-ignore (AI prompt)
        f"""Tulis ulang SEMUA judul supaya gayanya seragam: {STYLES.get(style, STYLES['langkah'])}.\n"""  # i18n-ignore (AI prompt)
        f"""Aturan: bahasa Indonesia, 2-6 kata, spesifik sesuai isi bab, tanpa emoji, tanpa nomor, tanpa tanda kutip, tanpa titik di akhir.\n"""  # i18n-ignore (AI prompt)
        f"""Jangan menggabung, menghapus, atau menambah bab. Urutan dan jumlah harus sama. Perbaiki nama merek yang salah dengar.{_gl_line(glossary)}\n"""  # i18n-ignore (AI prompt)
        f"""Balas JSON saja: {{"chapters":[{{"start":"mm:ss","title":"..."}}]}}"""  # i18n-ignore (AI prompt)
    )
    user = "\n".join(f"{ts(c['t'])} | {c['title']} | {previews[k][:160]}" for k, c in enumerate(chs))
    if hint:
        user = f"Instruksi user: {hint.strip()[:300]}\n\n" + user
    return [{"role": "system", "content": sys_}, {"role": "user", "content": user}]


def _cached_chat(cache, msgs, schema, model, tag, timeout=120):
    key = cache.key(tag, PROMPT_V, model, msgs)
    hit = cache.get(key)
    if hit is not None:
        return hit, True
    obj = ai.chat_json(msgs, schema=schema, model=model, tag=tag, timeout=timeout)
    cache.put(key, obj)
    return obj, False


def ai_picks(S, parts, n, end, hint, style, glossary, model, emit):
    """Ask every part in parallel (<= AI_MAX_PARALLEL in flight). -> (picks, stats); a failed part falls back
    to rule picks for that part only."""
    cache = ai.Cache()
    total = sum(p[-1]["end"] - p[0]["start"] for p in parts) or 1.0
    jobs = []
    for k, part in enumerate(parts):
        quota = max(1, round(n * (part[-1]["end"] - part[0]["start"]) / total))
        jobs.append((k, part, quota, part_messages(part, k, len(parts), end, quota, hint, style, glossary)))
    st = {"parts": len(parts), "ok": 0, "failed": 0, "cached": 0, "resolved": Counter(), "raw": 0, "secs": []}
    results = {}
    ex = ThreadPoolExecutor(max_workers=max(1, ai.config()["max_parallel"]))
    try:   # progress + cancel stay on this thread; a cancel never waits for the HTTP calls still in flight
        futs = {}
        for k, part, quota, msgs in jobs:
            futs[ex.submit(_cached_chat, cache, msgs, PART_SCHEMA, model, "chapters_part")] = (k, time.time())
        pending = set(futs)
        while pending:
            done, pending = wait(pending, timeout=0.25, return_when=FIRST_COMPLETED)
            for f in done:
                k, t0 = futs[f]
                try:
                    obj, hit = f.result()
                    results[k] = obj
                    st["ok"] += 1
                    st["cached"] += int(hit)
                    st["secs"].append(round(time.time() - t0, 1))
                except Exception as e:  # noqa: BLE001  - AI is optional: any failure = rule picks for that part
                    results[k] = e
                    st["failed"] += 1
                    emit.log(f"AI part {k + 1} failed ({type(e).__name__}): {e}")
            emit.progress(100.0 * len(results) / len(jobs), note=tr("chapters.partsNote", done=len(results), total=len(jobs)),
                          force=True)
    finally:
        ex.shutdown(wait=False, cancel_futures=True)
    picks = []
    for k, part, quota, _ in jobs:
        s0, s1 = part[0]["s0"], part[-1]["s1"]
        obj = results.get(k)
        if isinstance(obj, Exception) or obj is None:
            seg = S[s0:s1 + 1]
            sub = fallback_picks(seg, quota + (1 if k else 0), seg[0]["start"], seg[-1]["end"], YT_MIN * 3)
            picks += [dict(p, i=p["i"] + s0) for p in sub if k == 0 or p["i"] > 0]
            continue
        psents = S[s0:s1 + 1]
        after = -1
        for c in obj.get("chapters") or []:
            st["raw"] += 1
            i, how = resolve(psents, c.get("start", ""), c.get("quote", ""), True, after)
            st["resolved"][how] += 1
            title = clean_title(c.get("title", ""))
            if i is None or not title:
                continue
            after = i
            picks.append({"i": s0 + i, "title": title, "src": "ai", "how": how})
    st["resolved"] = dict(st["resolved"])
    return picks, st


def polish_titles(chs, previews, style, hint, glossary, model):
    """One small call: consistent title style across parts. Returns new titles (same length) or raises AIError."""
    obj, _ = _cached_chat(ai.Cache(), titles_messages(chs, previews, style, hint, glossary), TITLES_SCHEMA, model,
                          "chapters_titles", timeout=90)
    got = obj.get("chapters") or []
    titles = [clean_title(c.get("title", "")) for c in got]
    if len(titles) != len(chs):   # model merged/split rows: map by mm:ss, keep the rest
        by_ts = {}
        for c in got:
            try:
                by_ts[int(parse_ts(str(c.get("start", "")).split("-")[0]))] = clean_title(c.get("title", ""))
            except ValueError:
                pass
        titles = [by_ts.get(int(c["t"])) for c in chs]
    return [t if t else c["title"] for t, c in zip(titles, chs)]


# ====================================================================== shared job plumbing

def _timeline(job):
    seq = job.get("seq")
    if not seq:
        raise EngineError("NO_SEQ", tr("chapters.err.noSeq"), tr("chapters.err.noSeqHint"))
    return Timeline.from_json(seq)


def _scope(tl, p):
    rs = R.norm(tl.scope_ranges(p.get("scope"))) or [[0.0, tl.duration]]
    return rs, float(rs[0][0]), float(rs[-1][1])


def _previews(chs, words, hi, n_words=14):
    out = []
    for k, c in enumerate(chs):
        t1 = chs[k + 1]["t"] if k + 1 < len(chs) else hi
        ws = [w["text"].strip() for w in words if c["t"] - 0.35 <= w["t0"] < t1][:n_words]
        out.append(" ".join(ws))
    return out


def _words(tl, emit=None, transcribe=True):
    if not tl.audio_clips():
        raise EngineError("NO_AUDIO", tr("chapters.err.noAudio"), tr("chapters.err.noAudioHint"))
    return tl.words_on_timeline(transcribe=transcribe, emit=emit)


# ====================================================================== actions

def analyze(job, emit):
    p = job.get("params") or {}
    emit.plan([("words", tr("chapters.stage.words"), 0.3), ("units", tr("chapters.stage.units"), 0.05),
               ("find", tr("chapters.stage.findAi"), 0.55), ("check", tr("chapters.stage.check"), 0.1)])
    with emit.step("words"):
        tl = _timeline(job)
        words = _words(tl, emit=emit)
    with emit.step("units"):
        rs, lo, hi = _scope(tl, p)
        words = _words_in(words, rs)
        S = prepare(words, lo)
        if len(S) < 2 or S[-1]["end"] - lo < YT_MIN:
            raise EngineError("NO_SPEECH", tr("chapters.err.noSpeech"), tr("chapters.err.noSpeechHint"))
        content_end = min(hi, S[-1]["end"])
        dur = content_end - lo
        n = target_count(dur, p.get("count", "normal"))
        min_len = max(YT_MIN, float(p.get("min_len") or 0), 0.25 * dur / max(1, n))
        blocks = make_blocks(S)
        parts = make_parts(blocks, dur)
        emit.progress(100)
    hint, style = str(p.get("hint") or "").strip(), str(p.get("style") or "langkah")
    glossary, model = _glossary(), _model()
    use_ai = p.get("ai", True) is not False and ai.available()
    warnings, st, source = [], {}, "rule"
    with emit.step("find", None if use_ai else tr("chapters.stage.findRule")):
        if use_ai:
            emit.log(f"AI chapters: {len(parts)} parts in parallel ({model}), target {n} chapters")
            picks, st = ai_picks(S, parts, n, content_end, hint, style, glossary, model, emit)
            if st["ok"] == 0:
                use_ai = False
                warnings.append(tr("chapters.warn.aiNoAnswer"))
            else:
                source = "ai" if not st["failed"] else "mixed"
                if st["failed"]:
                    warnings.append(tr("chapters.warn.partsNoAi", n=st["failed"], total=st["parts"]))
                if st["cached"] == st["ok"] and not st["failed"]:
                    emit.done_note(tr("chapters.fromCache"))
        else:
            why = (tr("chapters.why.offTool") if p.get("ai", True) is False else
                   tr("chapters.why.offSettings") if ai.config()["disabled"] else tr("chapters.why.down"))
            warnings.append(tr("chapters.warn.noAi", why=why))
        if not use_ai:
            picks = fallback_picks(S, n, lo, content_end, min_len)
        emit.progress(100)
    with emit.step("check"):
        if not any(pk["i"] == 0 for pk in picks):   # first chapter always at the scope start
            early = [pk for pk in picks if pk["i"] <= blocks[0]["s1"]]
            if early:
                min(early, key=lambda pk: pk["i"])["i"] = 0
            else:
                picks.append({"i": 0, "title": tr("chapters.firstTitle"), "src": "ai"} if use_ai else {"i": 0, "src": "rule"})
        chs = validate(picks, S, lo, hi)
        chs = merge_to_count(chs, content_end, n, min_len)
        # The model sometimes under-segments (seen live: 1 chapter for a 2-min talk that has 3 steps). YouTube needs
        # >= 3: top up with rule boundaries (titles flagged generic) when the speech is long enough for them.
        want = min(max(3, round(0.6 * n)), int((content_end - lo) // min_len))
        if use_ai and len(chs) < want:
            extra = fallback_picks(S, want, lo, content_end, min_len, existing=[c["i"] for c in chs])
            if extra:
                n_ai = len(chs)
                chs = merge_to_count(validate(chs + extra, S, lo, hi), content_end, n, min_len)
                st["topped_up"] = len(chs) - n_ai
                warnings.append(tr("chapters.warn.toppedUp", n=n_ai, extra=len(chs) - n_ai))
        keyword_titles(chs, S, content_end)              # titles for rule picks only (generic, flagged)
        previews = _previews(chs, words, hi)
        if use_ai and len(parts) > 1 and p.get("polish", True) is not False and len(chs) >= 2:
            try:
                for c, t in zip(chs, polish_titles(chs, previews, style, hint, glossary, model)):
                    if not c.get("generic"):
                        c["title"] = t
                st["polished"] = True
            except ai.AIError as e:
                emit.log(f"title polish failed: {e}")
                st["polished"] = False
        issues = youtube_issues(chs, lo, hi)
        items = []
        for k, c in enumerate(chs):
            end = chs[k + 1]["t"] if k + 1 < len(chs) else hi
            items.append(RV.item(c["t"], end, "chapter", on=True, label=c["title"], ctx=previews[k],
                                 title=c["title"], generic=bool(c.get("generic")), src=c.get("src") or source,
                                 keywords=c.get("keywords") or []))
        yt = youtube_text(chs, lo)
        stats = {"n": len(chs), "target": n, "parts": len(parts), "blocks": len(blocks), "sentences": len(S),
                 "content_end": round(content_end, 3), "min_len": round(min_len, 1),
                 "ai": {k: v for k, v in st.items() if k != "secs"}, "ai_secs": st.get("secs", [])}
        doc = RV.new("chapters", items, tl.duration, seq=tl, params=p, stats=stats, offset=lo, end=hi,
                     source=source, warnings=warnings, issues=issues, youtube=yt, model=model if use_ai else None)
        path = RV.save(doc, RV.path_for(workdir(job, tl.name), "chapters"))
        emit.progress(100)
    for w in warnings:
        emit.warn(w)
    label = tr({"ai": "chapters.src.ai", "mixed": "chapters.src.mixed"}.get(source, "chapters.src.rule"))
    return {"review": str(path), "chapters": [{"t": it["t0"], "end": it["t1"], "title": it["title"],
                                               "generic": it["generic"]} for it in items],
            "youtube": yt, "offset": lo, "end": hi, "source": source, "warnings": warnings, "issues": issues,
            "target": n, "stats": stats, "summary": tr("chapters.sum.analyze", n=len(chs), label=label)}


def _edited(doc):
    """Chapters from an edited review doc: on items, sorted, titles cleaned, same-time duplicates dropped."""
    lo = float(doc.get("offset") or 0.0)
    hi = float(doc.get("end") or doc.get("duration") or 0.0)
    rows = []
    for it in doc.get("items") or []:
        if it.get("on", True) is False:
            continue
        t = min(max(lo, float(it["t0"])), hi)
        rows.append({"t": round(t, 3), "title": clean_title(it.get("title") or it.get("label") or "", 100),
                     "ctx": it.get("ctx") if isinstance(it.get("ctx"), str) else "", "id": it.get("id")})
    rows.sort(key=lambda r: r["t"])
    out = []
    for r in rows:
        if out and abs(r["t"] - out[-1]["t"]) < 0.01:
            continue
        out.append(r)
    return out, lo, hi


def apply(job, emit):
    emit.plan([("check", tr("chapters.stage.checkList"), 0.5), ("write", tr("chapters.stage.write"), 0.5)])
    with emit.step("check"):
        doc = RV.from_job(job)
        chs, lo, hi = _edited(doc)
        if not chs:
            raise EngineError("NO_CHAPTERS", tr("chapters.err.empty"), tr("chapters.err.emptyHint"))
        notes = []
        if chs[0]["t"] - lo > 0.5:
            notes.append(tr("chapters.note.firstMoved", t=yt_time(chs[0]["t"] - lo)))
            chs[0]["t"] = lo
        for k, c in enumerate(chs):
            if not c["title"]:
                c["title"] = tr("chapters.defaultTitle", n=k + 1)
        previews = [c["ctx"] for c in chs]
        same = not (doc.get("seq") or {}).get("id") or (job.get("seq") or {}).get("id") == doc["seq"]["id"]
        if job.get("seq") and same:   # comment = what is said at the (possibly moved) start; caches only
            try:
                words = _timeline(job).words_on_timeline(transcribe=False)
                if words:
                    previews = _previews(chs, words, hi)
            except EngineError:
                pass
        issues = youtube_issues(chs, lo, hi)
        emit.progress(100)
    with emit.step("write"):
        yt = youtube_text(chs, lo)
        txt = workdir(job) / "chapters_youtube.txt"
        txt.write_text(yt + "\n", encoding="utf-8")
        emit.progress(100)
    markers = []
    for k, c in enumerate(chs):
        end = chs[k + 1]["t"] if k + 1 < len(chs) else hi
        markers.append({"t": c["t"], "end": round(max(c["t"], end), 3), "name": c["title"], "comment": previews[k][:200],
                        "tag": TAG, "color": COLOR, "type": MARKER_TYPE})
    return {"plan": {"kind": "markers", "tag": TAG, "markers": markers, "timebase": "sequence"},
            "youtube": yt, "txt": str(txt), "notes": notes, "issues": issues, "youtube_ok": not issues,
            "n": len(chs), "seq": doc.get("seq"), "summary": tr("chapters.sum.apply", n=len(chs))}


def _chapters_param(job):
    """Chapters for meta/retitle: params.chapters [{t, title}] (the panel's live list) or the review file."""
    p = job.get("params") or {}
    if p.get("chapters"):
        chs = sorted(({"t": float(c["t"]), "title": clean_title(c.get("title", ""), 100)} for c in p["chapters"]),
                     key=lambda c: c["t"])
        lo = float(p.get("offset", chs[0]["t"] if chs else 0.0))
        return chs, lo, p
    doc = RV.from_job(job)
    chs, lo, _ = _edited(doc)
    return chs, lo, p


def meta(job, emit):
    """Title / description / hashtags in one AI call (ac.ai.tasks "meta", cached by input)."""
    from ..ai import tasks
    emit.plan([("words", tr("chapters.stage.readWords"), 0.2), ("meta", tr("chapters.stage.meta"), 0.8)])
    with emit.step("words"):
        tl = _timeline(job)
        chs, lo, p = _chapters_param(job)
        rs, slo, hi = _scope(tl, p)
        words = _words_in(_words(tl, emit=emit), rs)
        if not words:
            raise EngineError("NO_SPEECH", tr("chapters.err.noSpeechSeq"), tr("chapters.err.noSpeechSeqHint"))
    with emit.step("meta"):
        rel = [{"start": c["t"] - lo, "title": c["title"]} for c in chs]
        if p.get("ai", True) is False:
            r = {"source": "fallback", "result": {}, "warnings": [tr("chapters.why.offTool")]}
        else:
            r = tasks.run("meta", words, model=_model(), glossary=_glossary(), name=tl.name, chapters=rel, emit=emit)
        emit.progress(100)
    res = dict(r.get("result") or {})
    ok = r.get("source") in ("ai", "cache")
    if not ok:   # honest fallback: no invented titles, the description is the chapter list to build on
        res = {"titles": [], "description": youtube_text(chs, lo), "hashtags": [], "tags": []}
    return {"titles": res.get("titles") or [], "description": res.get("description") or "",
            "hashtags": res.get("hashtags") or [], "tags": res.get("tags") or [], "source": r.get("source"),
            "ai": ok, "warnings": r.get("warnings") or [],
            "summary": tr("chapters.sum.meta" if ok else "chapters.sum.metaNoAi")}


def retitle(job, emit):
    """Rewrite every title of the panel's current list in one AI call (style + instruction)."""
    emit.plan([("words", tr("chapters.stage.readWords"), 0.2), ("titles", tr("chapters.stage.retitle"), 0.8)])
    with emit.step("words"):
        tl = _timeline(job)
        chs, lo, p = _chapters_param(job)
        if not chs:
            raise EngineError("NO_CHAPTERS", tr("chapters.err.empty"), tr("chapters.err.emptyHintShort"))
        rs, slo, hi = _scope(tl, p)
        words = _words_in(_words(tl, emit=emit, transcribe=False), rs)
        previews = _previews(chs, words, hi, 30)
    with emit.step("titles"):
        if p.get("ai", True) is False or not ai.available():
            raise EngineError("NO_AI", tr("chapters.err.noAi"), tr("chapters.err.noAiHint"))
        try:
            titles = polish_titles(chs, previews, str(p.get("style") or "langkah"), str(p.get("hint") or ""),
                                   _glossary(), _model())
        except ai.AIError as e:
            raise EngineError("AI_FAILED", tr("chapters.err.aiFailed"), str(e)[:200]) from None
        emit.progress(100)
    return {"titles": titles, "chapters": [dict(c, title=t) for c, t in zip(chs, titles)],
            "summary": tr("chapters.sum.retitle", n=len(titles))}


ACTIONS = {"analyze": analyze, "apply": apply, "meta": meta, "retitle": retitle}
