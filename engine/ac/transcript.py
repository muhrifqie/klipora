"""Whisper transcripts: production words cache v3 + listener (disfluency) pass.

Words cache v3, next to the media as <stem>_words.json (util.cache_path):
  {"v": 3, "model": "large-v3-turbo", "lang": "id", "hash": "<util.file_hash>", "duration": 2076.74,
   "words": [[text, start, end, seg_end, prob], ...],          # source seconds, sorted, non-overlapping
   "issues": [{"t0","t1","text","kind"}],                     # dropped hallucinations
   "repair": {"spans": [[t0, t1, n_added]], "added": n},      # hole repair report
   "destretch": {"moved": n}, "segs": [[t0, t1, avg_logprob, no_speech_prob, compression_ratio]], ...}

Word rows: text keeps Whisper's leading space (" teman-teman,") so "".join(texts) rebuilds the transcript;
continuation tokens are already merged ("-teman", "-nya", ".com", ".000", "%"), so ONE ROW = ONE WORD and the
row index is a stable word id (pair it with the media hash). seg_end=1 marks Whisper's phrase end.
prob = Whisper word probability (1.0 for words upgraded from a v2 cache, which had none).

Pipeline (measured on the 34.6 min tutorial, see docs/ENGINE_API.md):
  decode 16 kHz -> envelope -> large-v3-turbo sequential (VAD on, condition_on_previous_text=False:
  no 94-113 s hole, no "Terima kasih telah menonton" loop, 70 s instead of 125 s)
  -> merge continuations -> hallucination filter -> word-start de-stretch (envelope)
  -> hole repair (voiced spans with no words are re-transcribed one by one).

Listener pass (fillers): `small` + BatchedInferencePipeline + disfluency prompt, cached <stem>_listen.json
  {"v": 1, "model": "small", "hash", "prompt", "words": [[text, start, end, prob]]}.
"""
from __future__ import annotations

import re
import threading
import time
from pathlib import Path

from . import media as M
from .i18n import tr
from .util import (EngineError, cache_path, cuda_setup, file_hash, gpu_lock, log, now_iso, read_json, setting,
                   write_json)

CACHE_V = 3
LISTEN_V = 1
DEFAULT_MODEL = "large-v3-turbo"
LISTEN_MODEL = "small"
LISTEN_PROMPT = "Eee, jadi, emm, ya... hmm, anu, eh, gitu."
PUNCT = ".,!?;:\"'“”‘’…()[]"

# --------------------------------------------------------------------------------- hallucinations

# Whisper's stock phrases on silence / music / file end (Indonesian + English). Measured on the test media.
HALLU_RE = re.compile(
    r"terima kasih (telah|sudah|udah) menonton\.?|selamat menikmati\.?|sampai jumpa (lagi )?di video\w*( berikutnya| selanjutnya)?\.?|"  # i18n-ignore
    r"(bicara\. ?){2,}|jangan lupa (like|subscribe|share)\w*|thanks? (you )?for watching\.?|subtitles? by[^.]*|"  # i18n-ignore
    r"amara\.org|terima kasih telah mendengarkan\.?", re.I)  # i18n-ignore
OUTRO_PHRASES = ("terima kasih telah menonton", "terima kasih sudah menonton", "sampai jumpa di video",  # i18n-ignore
                 "jangan lupa subscribe", "thanks for watching", "thank you for watching", "selamat menikmati")  # i18n-ignore


def _norm(t):
    return re.sub(r"[^\w]+", "", t.lower())


def merge_continuations(rows):
    """Glue Whisper continuation tokens onto the previous word: any token without a leading space
    (' teman' + '-teman,' -> ' teman-teman,'; ' warungku' + '.com'; ' 1' + '.000'; ' 50' + '%').
    A capitalised token right after a phrase end stays a new word. Rows: [text, start, end, seg, prob...].
    Merged word: first start, last end, seg_end = max, prob = min. Every text gets a leading space."""
    out = []
    for r in rows:
        text = str(r[0])
        cont = (out and text and not text[0].isspace()
                and not (out[-1][3] and text[:1].isupper()))
        if cont:
            p = out[-1]
            p[0] += text
            p[2] = max(p[2], float(r[2]))
            p[3] = max(int(p[3]), int(r[3]) if len(r) > 3 else 0)
            if len(p) > 4 and len(r) > 4:
                p[4] = min(p[4], r[4])
        else:
            nr = list(r)
            if not text.startswith(" "):
                nr[0] = " " + text.lstrip()
            out.append(nr)
    return out


def _loops(rows, toks):
    """Consecutive repeats of an n-gram: n=1 x>=4, n=2..4 x>=3 -> set of row indexes."""
    bad = set()
    n_rows = len(toks)
    for n, need in ((1, 4), (2, 3), (3, 3), (4, 3)):
        i = 0
        while i + n <= n_rows:
            gram = toks[i:i + n]
            if not all(gram):
                i += 1
                continue
            k = 1
            while i + (k + 1) * n <= n_rows and toks[i + k * n:i + (k + 1) * n] == gram:
                k += 1
            if k >= need:
                bad.update(range(i, i + k * n))
                i += k * n
            else:
                i += 1
    return bad


def _spoken(t0, t1, on, hop):
    """Mostly sounding frames under [t0, t1) (Whisper squeezes word times, so no speaking-rate test)."""
    a, b = int(t0 / hop), max(int(t0 / hop) + 1, int(t1 / hop))
    seg = on[a:b]
    return len(seg) > 0 and seg.mean() >= 0.6


def drop_hallucinations(rows, on=None, hop=M.HOP):
    """Remove Whisper hallucinations. Returns (clean rows, issues [{t0,t1,text,kind}]).
      phrase   stock outro phrases (regex over the joined text), and phrase fragments that form a whole
               Whisper segment ("Terima kasih telah", "menonton.")
      loop     n-gram repeated back to back (1 word x4, 2-4 words x3): "Tenggak Tenggak Tenggak Tenggak"
      dense    one word (>= 4 letters) >= 4 times inside 12 words / 15 s with <= 5 distinct words
      segloop  a segment where one word (>= 4 letters) occurs >= 4 times and is > 50 % of the words
    `on` (envelope gate mask, optional): a stock phrase that occurs ONCE over clear sound (>= 60 % sounding
    frames) is kept: the speaker really said "sampai jumpa di video selanjutnya" at the end of the 34-min
    test file. Repeated phrases (x2+) and phrases over silence are always dropped."""
    if not rows:
        return [], []
    kind = {}
    text, owner = "", []
    for k, r in enumerate(rows):
        text += r[0]
        owner += [k] * len(r[0])
    hits = [(sorted(set(owner[m.start():m.end()])), _norm(m.group(0))) for m in HALLU_RE.finditer(text)]
    for ks, key in hits:
        repeated = sum(1 for _, k2 in hits if k2 == key) >= 2   # "Terima kasih telah menonton." x7 = loop
        if on is not None and ks and not repeated and _spoken(rows[ks[0]][1], rows[ks[-1]][2], on, hop):
            continue
        for k in ks:
            kind.setdefault(k, "phrase")
    toks = [_norm(r[0]) for r in rows]
    # segments
    segs, start = [], 0
    for k, r in enumerate(rows):
        if r[3] or k == len(rows) - 1:
            segs.append((start, k + 1))
            start = k + 1
    for a, b in segs:
        words = [t for t in toks[a:b] if t]
        bare = " ".join(words)
        if bare and (("menonton" in bare or "jumpa" in bare or len(words) >= 3)
                     and any(bare in p for p in OUTRO_PHRASES)):
            for k in range(a, b):
                kind.setdefault(k, "phrase")
        if len(words) >= 4:
            top = max(set(words), key=words.count)
            # real stutters stay: "ya ya ya oke" (short word), "eh sorry sorry sorry" (only 3x)
            if len(top) >= 4 and top.isalpha() and words.count(top) >= 4 and words.count(top) / len(words) > 0.5:
                for k in range(a, b):
                    kind.setdefault(k, "segloop")
    for k in _loops(rows, toks):
        kind.setdefault(k, "loop")
    for k, t in enumerate(toks):
        if len(t) >= 4 and t.isalpha():  # numbers repeat for real ("Rp1.000, Rp1.000, eh Rp1.000")
            win = [m for m in range(k, min(len(rows), k + 12)) if rows[m][1] - rows[k][1] < 15]
            near = [m for m in win if toks[m] == t]
            if len(near) >= 4 and len({toks[m] for m in win if m <= near[-1]}) <= 5:
                for m in range(k, near[-1] + 1):
                    kind.setdefault(m, "dense")
    issues, clean, run = [], [], None
    for k, r in enumerate(rows):
        if k in kind:
            if run and run["kind"] == kind[k] and k == run["last"] + 1:
                run["t1"], run["text"], run["last"] = r[2], run["text"] + r[0], k
            else:
                run = {"t0": r[1], "t1": r[2], "text": r[0], "kind": kind[k], "last": k}
                issues.append(run)
        else:
            clean.append(r)
    for it in issues:
        it.pop("last", None)
        it["text"] = it["text"].strip()
        it["t0"], it["t1"] = round(it["t0"], 3), round(it["t1"], 3)
    return clean, issues


# --------------------------------------------------------------------------------- envelope fixes

def destretch(rows, on, hop=M.HOP, onset_pad=0.03, min_shift=0.05, min_dur=0.04):
    """Whisper turbo glues the preceding pause onto the NEXT word's start (14 % of words start > 0.3 s
    early; "Kamu" 24.88-25.84 with audio at 25.50). For each word whose start frame is silent in the gate
    mask `on`, move the start to the first sounding frame inside the word minus onset_pad. Ends are kept.
    Words with no sounding frame are left alone. Returns (rows, moved)."""
    moved = 0
    n = len(on)
    for r in rows:
        s, e = float(r[1]), float(r[2])
        i0, i1 = int(s / hop + 1e-9), min(n, int(e / hop + 1e-9) + 1)
        if i0 >= n or i1 <= i0 or on[i0]:
            continue
        seg = on[i0:i1]
        hit = seg.argmax() if seg.any() else -1
        if hit <= 0:
            continue
        ns = max(s, (i0 + int(hit)) * hop - onset_pad)
        ns = min(ns, e - min_dur)
        if ns - s >= min_shift:
            r[1] = round(ns, 3)
            moved += 1
    return rows, moved


def coverage_mask(rows, n, hop=M.HOP):
    import numpy as np
    cov = np.zeros(n, bool)
    for r in rows:
        a, b = int(r[1] / hop + 1e-9), int(r[2] / hop + 1e-9) + 1
        cov[max(0, a):max(0, min(n, b))] = True
    return cov


def find_holes(rows, on, hop=M.HOP, min_span=2.0, min_voiced=1.0, bridge=0.6, max_spans=80):
    """Voiced regions the transcript does not cover (Whisper skipped a window: the 57 s hole at 94-152 s of
    the 34-min test file). -> [[t0, t1, voiced_sec]] spans >= min_span long with >= min_voiced s of sound."""
    unc = on & ~coverage_mask(rows, len(on), hop)
    spans = M.runs(unc, hop, min_gap=bridge)
    out = []
    for a, b in spans:
        v = float(unc[int(a / hop):int(b / hop)].sum()) * hop
        if b - a >= min_span and v >= min_voiced:
            out.append([round(a, 2), round(b, 2), round(v, 2)])
    return out[:max_spans]


# --------------------------------------------------------------------------------- models

_MODELS = {}
_MLOCK = threading.Lock()


def _device_pref():
    return "cuda" if setting("gpu", True) else "cpu"


def get_model(name, device="cuda"):
    """Cached faster_whisper.WhisperModel (worker keeps it warm). device 'cuda' (float16) or 'cpu' (int8)."""
    key = (name, device)
    with _MLOCK:
        if key not in _MODELS:
            if device == "cuda":
                cuda_setup()
            from faster_whisper import WhisperModel
            _MODELS[key] = WhisperModel(name, device=device,
                                        compute_type="float16" if device == "cuda" else "int8")
        return _MODELS[key]


def unload_models():
    """Free Whisper models (VRAM). The worker calls it after being idle."""
    with _MLOCK:
        n = len(_MODELS)
        _MODELS.clear()
    if n:
        import gc
        gc.collect()
    return n


def _run_whisper(fn, device, emit, label):
    """Run fn(device) under the GPU lock; on a CUDA failure fall back to CPU once (with a warning)."""
    if device == "cuda":
        try:
            with gpu_lock(emit, label=label):
                return fn("cuda"), "cuda"
        except EngineError:
            raise
        except (RuntimeError, OSError, ValueError) as e:
            msg = str(e)
            log().warning("whisper cuda failed: %s", msg)
            if emit:
                emit.warn(tr("transcript.gpuFallback", detail=msg[:120]))
    try:
        return fn("cpu"), "cpu"
    except EngineError:
        raise
    except (RuntimeError, OSError, ValueError) as e:
        log().error("whisper cpu failed: %s", e)
        raise EngineError("WHISPER", tr("err.whisper.msg", detail=f"{type(e).__name__}: {str(e)[:200]}"),
                          tr("err.whisper.hint")) from e


def _segments_to_rows(segs, offset, total, emit, p0, p1, keep_seg=None):
    rows, meta = [], []
    for s in segs:
        if emit:
            emit.progress(p0 + (p1 - p0) * min(1.0, max(0.0, s.end / max(total, 1e-6))))
        if keep_seg is not None and not keep_seg(s):
            continue
        ws = [[w.word, round(w.start + offset, 3), round(max(w.end, w.start + 0.01) + offset, 3), 0,
               round(float(w.probability), 3)] for w in (s.words or [])]
        if ws:
            ws[-1][3] = 1
            rows += ws
        meta.append([round(s.start + offset, 2), round(s.end + offset, 2), round(s.avg_logprob, 3),
                     round(s.no_speech_prob, 3), round(s.compression_ratio, 2)])
    return rows, meta


STOCK_SEG = re.compile(r"^\W*(terima kasih|sampai jumpa|selamat (menikmati|menonton)|jangan lupa)\b", re.I)  # i18n-ignore


def _real_seg(s, min_logprob=-0.7, min_prob=0.5):
    """Strict filter for segments transcribed inside a repaired hole. Short windows over non-speech make
    Whisper invent "Terima kasih.", "Sampai jumpa di video selanjutnya.", "selamat menikmati" or garble  # i18n-ignore
    background audio (avg_logprob -1.2 .. -1.9, word prob 0.0-0.4). Measured on the 34-min file: every
    one of 24 uncovered sound spans near the end was such junk; real skipped speech (v2 cache, 94-113 s)
    decodes with avg_logprob > -0.5."""
    if s.no_speech_prob > 0.6 and s.avg_logprob < -1.0:
        return False
    if s.compression_ratio > 2.4 or s.avg_logprob < min_logprob:
        return False
    probs = [w.probability for w in (s.words or [])]
    if not probs or sum(probs) / len(probs) < min_prob:
        return False
    return not STOCK_SEG.match(s.text or "")


# --------------------------------------------------------------------------------- cache

def words_path(path):
    return cache_path(path, "_words.json")


def listen_path(path):
    return cache_path(path, "_listen.json")


def load_doc(path, model=None, lang=None):
    """The valid v3 cache document for this media (hash matches; model/lang match when given), else None."""
    doc = read_json(words_path(path))
    if not isinstance(doc, dict) or doc.get("v") != CACHE_V:
        return None
    try:
        if doc.get("hash") != file_hash(path):
            return None
    except OSError:
        return None
    if (model and doc.get("model") != model) or (lang and doc.get("lang") != lang):
        return None
    return doc


def cached_words(path, model=None, lang=None):
    """Words from a valid v3 cache, or None (never transcribes). Use it for optional features
    (silence word-guard) that must not trigger a GPU job."""
    doc = load_doc(path, model, lang)
    return doc["words"] if doc else None


def has_words(path):
    return cached_words(path) is not None


def postprocess(rows, db, thr=None):
    """merge continuations -> drop hallucinations -> de-stretch starts. Returns (rows, info)."""
    thr = M.otsu_db(db) if thr is None else thr
    on = M.gate(db, thr)
    rows = merge_continuations(rows)
    rows, issues = drop_hallucinations(rows, on)
    rows, moved = destretch(rows, on)
    rows = fix_overlaps(rows)
    return rows, {"issues": issues, "destretch": {"moved": moved}, "thr": round(thr, 1), "on": on}


def fix_overlaps(rows, min_dur=0.01):
    """Sort and make spans strictly sequential: end_i <= start_i+1, every word >= min_dur long.
    Whisper sometimes gives two words the same start ("kita" 1154.64-1154.65, "cek" 1154.64-...)."""
    rows.sort(key=lambda r: (r[1], r[2]))
    for a, b in zip(rows, rows[1:]):
        if b[1] < a[2]:
            a[2] = round(b[1], 3)
        if a[2] - a[1] < min_dur:
            a[2] = round(a[1] + min_dur, 3)
            if b[1] < a[2]:
                b[1] = a[2]
        if b[2] - b[1] < min_dur:
            b[2] = round(b[1] + min_dur, 3)
    return rows


def _duplicate(r, rows, reach=1.0):
    """The re-transcribed word is the neighbouring existing word heard again ("meninjam" right before
    "Meninjau"): similar text within `reach` seconds."""
    import difflib
    n = _norm(r[0])
    if not n:
        return True
    for x in rows:
        if x[1] > r[2] + reach:
            break
        if x[2] >= r[1] - reach and difflib.SequenceMatcher(None, n, _norm(x[0])).ratio() >= 0.75:
            return True
    return False


def repair_holes(rows, audio, on, model, lang, emit=None, p0=0.0, p1=100.0):
    """Re-transcribe each uncovered voiced span alone (vad off, no conditioning, junk segments dropped,
    hallucinations filtered) and insert the words that land inside it. Returns (rows, report)."""
    holes = find_holes(rows, on)
    report = {"spans": [], "added": 0, "checked": len(holes)}
    for k, (a, b, _v) in enumerate(holes):
        if emit:
            emit.progress(p0 + (p1 - p0) * k / max(1, len(holes)))
        pieces = []
        t = a
        while t < b - 0.05:  # windows <= 28 s (Whisper's 30 s context)
            pieces.append((t, min(b, t + 28.0)))
            t += 28.0
        added = []
        for w0, w1 in pieces:
            c0, c1 = max(0.0, w0 - 0.3), min(len(audio) / M.SR, w1 + 0.3)
            seg_audio = audio[int(c0 * M.SR):int(c1 * M.SR)]
            if len(seg_audio) < M.SR // 4:
                continue
            segs, _ = model.transcribe(seg_audio, language=lang, word_timestamps=True, vad_filter=False,
                                       condition_on_previous_text=False, beam_size=5)
            new, _meta = _segments_to_rows(segs, c0, c1 - c0, None, 0, 0, keep_seg=_real_seg)
            new = merge_continuations(new)
            new, _iss = drop_hallucinations(new)   # no "spoken" exemption inside holes
            for r in new:
                mid = (r[1] + r[2]) / 2
                if w0 - 0.1 <= mid <= w1 + 0.1:
                    added.append(r)
        if added:
            cov = coverage_mask(rows, len(on))
            keep = []
            for r in added:
                i0, i1 = int(r[1] / M.HOP), max(int(r[1] / M.HOP) + 1, int(r[2] / M.HOP))
                if cov[i0:i1].mean() < 0.5 and not _duplicate(r, rows):
                    keep.append(r)
            rows = fix_overlaps(rows + keep)
            if keep:
                keep[-1][3] = 1
            report["added"] += len(keep)
            report["spans"].append([a, b, len(keep), "".join(r[0] for r in keep).strip()[:120]])
        else:
            report["spans"].append([a, b, 0, ""])
    return rows, report


def _upgrade_v2(path):
    old = read_json(words_path(path))
    if isinstance(old, dict) and old.get("v") == 2 and isinstance(old.get("words"), list):
        return [[w[0], float(w[1]), float(w[2]), int(w[3]) if len(w) > 3 else 0, 1.0] for w in old["words"]]
    return None


def words(path, model=None, lang=None, emit=None, refresh=False, device=None, audio=None, repair=True):
    """Production word list for a media file (source seconds), transcribing once and caching v3.

    model/lang default to settings (whisperModel, lang). device None = settings 'gpu' (cuda -> cpu fallback).
    A matching v2 cache (engine/caption.py) is upgraded without a full re-transcription (post-processing +
    hole repair only). emit: progress 0..100 inside the caller's current stage; emit.done_note(tr('note.cached')).
    Raises EngineError('NO_AUDIO'/'NO_MEDIA'/'GPU_BUSY'/'CANCELLED')."""
    path = str(path)
    model = model or setting("whisperModel", DEFAULT_MODEL) or DEFAULT_MODEL
    lang = lang or setting("lang", "id") or "id"
    if not refresh:
        doc = load_doc(path, model, lang)
        if doc:
            if emit:
                emit.done_note(tr("note.cached"))
                emit.progress(100)
            return doc["words"]
    if not Path(path).is_file():
        raise EngineError("NO_MEDIA", tr("err.noMedia.msg", path=str(path)), tr("err.noMedia.hint"))
    t_start = time.time()
    device = device or _device_pref()
    info = M.probe(path)
    if audio is None:
        audio = M.load_audio(path)
    if emit:
        emit.progress(2)
    db = M.envelope(path, audio=audio)
    dur = len(audio) / M.SR
    legacy = None if refresh else _upgrade_v2(path)
    seg_meta = []
    state = {}

    def job(dev):
        if not refresh:  # another process may have finished it while we waited for the GPU
            doc = load_doc(path, model, lang)
            if doc:
                state["doc"] = doc
                return None
        m = get_model(model, dev)
        if legacy is not None:
            rows = [list(r) for r in legacy]
        else:
            segs, _ = m.transcribe(audio, language=lang, word_timestamps=True, vad_filter=True,
                                   condition_on_previous_text=False)
            rows, meta = _segments_to_rows(segs, 0.0, dur, emit, 3, 85)
            seg_meta.extend(meta)
        rows, pinfo = postprocess(rows, db)
        report = {"spans": [], "added": 0, "checked": 0}
        if repair:
            rows, report = repair_holes(rows, audio, pinfo["on"], m, lang, emit, 85, 99)
            if report["added"]:
                rows, _ = destretch(rows, pinfo["on"])
        return rows, pinfo, report

    out, dev = _run_whisper(job, device, emit, f"transkripsi {Path(path).name}")
    if out is None:
        if emit:
            emit.done_note(tr("note.cached"))
        return state["doc"]["words"]
    rows, pinfo, report = out
    secs = round(time.time() - t_start, 1)
    doc = {"v": CACHE_V, "model": model, "lang": lang, "hash": file_hash(path), "media": path,
           "duration": round(info.get("duration") or dur, 3), "created": now_iso(), "device": dev, "secs": secs,
           "settings": ({"from_v2_cache": True} if legacy is not None else
                        {"vad": True, "condition_on_previous_text": False, "word_timestamps": True}),
           "upgraded_from": 2 if legacy is not None else None, "thr": pinfo["thr"],
           "words": rows, "issues": pinfo["issues"], "destretch": pinfo["destretch"], "repair": report,
           "segs": seg_meta}
    try:
        write_json(words_path(path), doc)
    except OSError as e:
        log().warning("words cache not written: %s", e)
    log().info("transcribed %s: %d words, %s s, %s, holes added %d", path, len(rows), secs, dev, report["added"])
    if emit:
        emit.progress(100)
        if legacy is not None:
            emit.done_note(tr("note.cacheRefreshed"))
    return rows


def listener_words(path, emit=None, refresh=False, device=None, audio=None, batch=8):
    """Disfluency listener pass for filler detection: `small` + BatchedInferencePipeline + LISTEN_PROMPT.
    Measured 2.6 s per 5 min (21.7 s for 34.6 min) on the RTX 5050; hears 7/8 labelled "eee/hmm".
    -> [[text, start, end, prob]] (continuations merged, stock hallucinations dropped), cached
    <stem>_listen.json. Timestamps are rough (+-0.2 s): snap them with the envelope."""
    path = str(M.require(path))
    cp = listen_path(path)
    h = file_hash(path)
    if not refresh:
        doc = read_json(cp)
        if isinstance(doc, dict) and doc.get("v") == LISTEN_V and doc.get("hash") == h \
                and doc.get("model") == LISTEN_MODEL and doc.get("prompt") == LISTEN_PROMPT:
            if emit:
                emit.done_note(tr("note.cached"))
                emit.progress(100)
            return doc["words"]
    t0 = time.time()
    if audio is None:
        audio = M.load_audio(path)
    dur = len(audio) / M.SR
    device = device or _device_pref()

    def job(dev):
        from faster_whisper import BatchedInferencePipeline
        pipe = BatchedInferencePipeline(get_model(LISTEN_MODEL, dev))
        segs, _ = pipe.transcribe(audio, language="id", initial_prompt=LISTEN_PROMPT, word_timestamps=True,
                                  batch_size=batch)
        rows = []
        for s in segs:
            if emit:
                emit.progress(min(99.0, 100 * s.end / max(dur, 1e-6)))
            rows += [[w.word, round(w.start, 3), round(max(w.end, w.start + 0.01), 3), 0,
                      round(float(w.probability), 3)] for w in (s.words or [])]
            if rows:
                rows[-1][3] = 1
        rows = merge_continuations(rows)
        rows, issues = drop_hallucinations(rows)
        return [[r[0], r[1], r[2], r[4]] for r in rows], issues

    (rows, issues), dev = _run_whisper(job, device, emit, f"listener {Path(path).name}")
    doc = {"v": LISTEN_V, "model": LISTEN_MODEL, "prompt": LISTEN_PROMPT, "hash": h, "media": path,
           "created": now_iso(), "device": dev, "secs": round(time.time() - t0, 1), "issues": issues, "words": rows}
    try:
        write_json(cp, doc)
    except OSError as e:
        log().warning("listen cache not written: %s", e)
    if emit:
        emit.progress(100)
    return rows


def text_of(rows):
    return "".join(r[0] for r in rows).strip()
