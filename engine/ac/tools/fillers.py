"""Hapus Filler: find hesitations ("eee", "emm", "hmm", "anu") and opt-in habit words ("ya", "oke", "gitu",
"apa namanya", ...) on the timeline and cut them into a new sequence.

Pipeline per heard media file (docs/research/fillers_repeat.md, proto/fillers):
  1. production words (ac.transcript.words, cache v3) + the small "listener" pass that actually writes
     "eee/hmm" (ac.transcript.listener_words, cached `<stem>_listen.json`). Both run under ONE GPU lock when a
     cache is missing (one Whisper job at a time); with caches nothing touches the GPU.
  2. frame features (pitch, held-sound measure, flatness), cached `<stem>_fillers.npz`, and the fused detector
     in _filler_audio (lexicon + listener + acoustic islands), snapped onto the real sound.
  3. habit words by deterministic phrase-boundary rules (_filler_lexicon), only for the words the user enabled.
     Grok is NOT used for per-word decisions (run-to-run accuracy 17-86 % in the research).
  4. source detections -> sequence time through the Timeline clips (a detection belongs to a clip when its
     sound midpoint lies inside the clip's [in, out)), scope applied, context from the timeline words.
  5. optional "Sekalian potong jeda": the silence tool's own analyze action (if installed) adds its gaps.

Actions
  analyze  -> {"review": path, "summary", "counts", "habit_counts", "silence"}   review items in SEQUENCE seconds:
              {id, t0, t1 (= the cut), kind filler|habit|sound|gap, on, conf, label, ctx {pre, post}, note,
               sound [s0, s1], tier always|anu|habit|acoustic|gap, src, srcs, tight, sub, src_t}
  apply    -> {"plan": {"kind": "remove_ranges", "ranges", "timebase": "sequence"}, "summary", "counts"}

Params (all optional): always (True), anu (True), habits ([] of _filler_lexicon.HABITS keys), acoustic (True),
threshold (0.65: items at or above it are pre-checked), with_silence (False), silence ({} = the silence tool's
settings for with_silence), drawl (False), scope (panel).
"""
from __future__ import annotations

import bisect
import contextlib
from pathlib import Path

from .. import media as M, ranges as R, review as RV, transcript as T
from ..i18n import dec, tr
from ..timeline import Timeline
from ..util import Cancelled, EngineError, file_hash, gpu_lock, read_json, setting, workdir
from . import _filler_audio as FA, _filler_lexicon as LX

TITLE = "Remove Fillers"          # developer listing (cli.py tools); UI text: tr("fillers.title")
DESCRIPTION = "Remove uhs and ums found in the transcript, the listener pass and wordless hesitation sounds."

DEFAULTS = {"always": True, "anu": True, "habits": [], "acoustic": True, "threshold": 0.65,
            "with_silence": False, "drawl": False}
SRC_OF = {"lexicon": "transcript", "listener": "listener", "habit": "transcript",
          "orphan": "acoustic", "prefix": "acoustic", "mid": "acoustic", "drawl": "acoustic"}
KIND_OF = {"always": "filler", "anu": "filler", "habit": "habit", "acoustic": "sound"}
# Row labels of wordless detections and the reasons shown under a row: locale keys fillers.label.<sub> and
# fillers.reason.<sub|rule>, translated per job.
LABEL_OF = ("orphan", "prefix", "mid", "drawl")
REASON = ("lexicon", "listener", "orphan", "prefix", "mid", "drawl", "transition", "tag", "search")
FILLER_KINDS = ("filler", "habit", "sound")


# --------------------------------------------------------------------------- params

def params(p):
    """Job params merged over DEFAULTS and validated (unknown habit keys dropped, threshold 0.3..0.95)."""
    out = dict(DEFAULTS)
    p = p if isinstance(p, dict) else {}
    for k in ("always", "anu", "acoustic", "with_silence", "drawl"):
        if k in p:
            out[k] = bool(p[k])
    try:
        out["threshold"] = min(0.95, max(0.3, float(p.get("threshold", DEFAULTS["threshold"]))))
    except (TypeError, ValueError):
        pass
    hs = p.get("habits") or []
    out["habits"] = [h for h in LX.HABITS if h in set(str(x).strip().lower() for x in hs)]
    out["scope"] = p.get("scope")
    out["silence"] = p.get("silence") if isinstance(p.get("silence"), dict) else {}
    return out


# --------------------------------------------------------------------------- helpers

class _Part:
    """Progress of item k of n inside the current stage (nested helpers report 0..100)."""

    def __init__(self, emit, k, n):
        self._e, self._b, self._s = emit, 100.0 * k / max(1, n), 100.0 / max(1, n)

    def progress(self, pct, note=None, force=False):
        self._e.progress(self._b + self._s * max(0.0, min(100.0, float(pct))) / 100, note=note, force=force)

    def __getattr__(self, name):
        return getattr(self._e, name)


class _Nested:
    """Emitter proxy to run another tool's action inside ONE of our stages: its plan/stages are swallowed,
    its per-stage progress is spread over our stage, log/warn/cancel pass through."""

    def __init__(self, emit):
        self._e, self._n, self._k = emit, 1, -1

    def plan(self, stages):
        self._n = max(1, len(stages))

    def stage(self, id, label=None, **kw):
        self._k += 1
        self._e.check_cancel()

    def stage_done(self, *a, **kw):
        pass

    def done_note(self, note):
        pass

    @contextlib.contextmanager
    def step(self, id, label=None, note=None, **kw):
        self.stage(id)
        yield self

    def progress(self, pct, note=None, force=False):
        k = min(max(0, self._k), self._n - 1)
        self._e.progress(100.0 * (k + max(0.0, min(100.0, float(pct))) / 100) / self._n, note=note, force=force)

    def sub(self, lo, hi):
        return lambda p: self.progress(lo + (hi - lo) * max(0.0, min(100.0, p)) / 100)

    def __call__(self, ev, **fields):
        if ev in ("log", "warn"):
            self._e(ev, **fields)

    def __getattr__(self, name):
        return getattr(self._e, name)


def _words_cached(path):
    model = setting("whisperModel", T.DEFAULT_MODEL) or T.DEFAULT_MODEL
    return T.load_doc(path, model, setting("lang", "id") or "id") is not None


def _listen_cached(path):
    doc = read_json(T.listen_path(path))
    try:
        return isinstance(doc, dict) and doc.get("v") == T.LISTEN_V and doc.get("hash") == file_hash(path) \
            and doc.get("model") == T.LISTEN_MODEL and doc.get("prompt") == T.LISTEN_PROMPT
    except OSError:
        return False


def _overlap_share(a, b):
    """Overlap of two [c0, c1] ranges as a share of the shorter one."""
    ov = R.overlap(a[0], a[1], b[0], b[1])
    return ov / max(1e-6, min(a[1] - a[0], b[1] - b[0]))


def source_candidates(F, words, listener, P, progress=None):
    """Detections of one media file in SOURCE seconds, filtered by the enabled tiers and merged."""
    an = FA.Analysis(F, words)
    raw = FA.detect(an, listener, progress=progress)
    keep = []
    for c in raw:
        if c["tier"] == "always" and not P["always"]:
            continue
        if c["tier"] == "anu" and not P["anu"]:
            continue
        if c["tier"] == "acoustic" and (not P["acoustic"] or (c["sub"] == "drawl" and not P["drawl"])):
            continue
        keep.append(c)
    out = FA.merge(keep)
    for h in FA.habit_cands(an, P["habits"]):
        if not any(_overlap_share(h["cut"], c["cut"]) > 0.5 for c in out):
            out.append(h)
    out.sort(key=lambda c: c["start"])
    return out


def _note(c):
    if c.get("tight"):
        return {"type": "warn", "text": tr("fillers.note.tight")}
    note = c.get("note") or ""
    if note.startswith("kata:"):
        return {"type": "info", "text": tr("fillers.note.heard", word=note[5:].strip())}
    if note == "pelan":
        return {"type": "info", "text": tr("fillers.note.quiet")}
    why = c.get("kind") if c["sub"] == "habit" else c["sub"]
    text = tr("fillers.reason." + why) if why in REASON else ""
    if text and c["sub"] in ("lexicon", "listener") and any(s in ("orphan", "prefix", "mid") for s in c.get("sources", [])):
        text = text[:-1] + tr("fillers.reason.alsoAudio")
    return {"type": "info", "text": text} if text else None


def to_timeline(tl, per_media, scope=None):
    """Map source detections onto the sequence. per_media = {path: [cand]} -> review items (no ids)."""
    rs = tl.scope_ranges(scope)
    items, seen = [], set()
    for clip in tl.audio_clips():
        cands = per_media.get(clip.path)
        if not cands:
            continue
        lo, hi = min(clip.src_in, clip.src_out), max(clip.src_in, clip.src_out)
        for c in cands:
            mid = (c["start"] + c["end"]) / 2
            if not lo <= mid < hi:
                continue
            c0, c1 = max(c["cut"][0], lo), min(c["cut"][1], hi)
            if c1 - c0 < 0.03:
                continue
            t0, t1 = clip.src_to_seq(c0), clip.src_to_seq(c1)
            s0, s1 = clip.src_to_seq(max(c["start"], lo)), clip.src_to_seq(min(c["end"], hi))
            sm = (s0 + s1) / 2
            rng = next((r for r in rs if r[0] - 1e-6 <= sm < r[1] + 1e-6), None)
            if rng is None:
                continue
            t0, t1 = max(t0, rng[0]), min(t1, rng[1])
            key = (clip.path, round(c["start"], 3), round(t0, 3))
            if t1 - t0 < 0.03 or key in seen:          # stereo twins on A1 + A2 count once
                continue
            seen.add(key)
            srcs = list(dict.fromkeys(SRC_OF[s] for s in c.get("sources", [c["sub"]])))
            worded = c["tier"] in ("always", "anu", "habit") and c["text"]
            label = c["text"] if worded else (tr("fillers.label." + c["sub"]) if c["sub"] in LABEL_OF else "eee")
            it = RV.item(t0, t1, KIND_OF[c["tier"]], conf=c["confidence"], label=label,
                         note=_note(c), sound=[round(s0, 3), round(s1, 3)], tier=c["tier"], src=srcs[0], srcs=srcs,
                         tight=bool(c.get("tight")), sub=c["sub"], src_t=[c0, c1])
            if c["sub"] == "habit":
                it["key"], it["rule"] = c["key"], c["kind"]
            items.append(it)
    items.sort(key=lambda it: (it["t0"], it["t1"]))
    return items


def add_context(items, tl_words, n=6):
    """ctx {pre, post}: timeline words before the cut and after it. Uses word END times: Whisper stretches
    starts back into pauses (a word starting inside the cut may still be spoken after it), ends are reliable."""
    ends = [w["t1"] for w in tl_words]
    for it in items:
        a = bisect.bisect_right(ends, it["t0"] + 0.05)
        b = max(a, bisect.bisect_right(ends, it["t1"] - 0.02))
        pre = " ".join(w["text"].strip() for w in tl_words[max(0, a - n):a]).strip()
        post = " ".join(w["text"].strip() for w in tl_words[b:b + n]).strip()
        it["ctx"] = {"pre": pre, "post": post}
    return items


def habit_counts(tl_words):
    """How often each habit key appears at a phrase boundary in the sequence (hints next to the toggles)."""
    rows = [[w["text"], w["t0"], w["t1"], w.get("seg", 0)] for w in tl_words]
    out = {}
    for _, _, _, key in LX.habit_find(rows, LX.HABITS):
        out[key] = out.get(key, 0) + 1
    return out


SILENCE_KEYS = ("preset", "offset", "threshold", "min_silence", "pad_before", "pad_after", "min_talk", "guard")


def silence_items(job, emit, scope, settings=None, wd=None):
    """Gaps from the silence tool's own analyze action (if installed), with the user's silence settings
    (params.silence: preset/offset/threshold/min_silence/pads/min_talk/guard; missing = its defaults).
    Its review goes to <workdir>/_fillers_silence so the user's own silence review is never overwritten.
    -> (items, info)."""
    from . import get, names
    if "silence" not in names():
        return [], {"ok": False, "msg": tr("fillers.silence.missing")}
    try:
        fn = get("silence").ACTIONS.get("analyze")
        if fn is None:
            raise EngineError("NO_ACTION", tr("fillers.silence.noAction"))
        sp = {k: v for k, v in (settings or {}).items() if k in SILENCE_KEYS}
        sub = {**job, "tool": "silence", "action": "analyze", "params": {**sp, "scope": scope},
               "workdir": str(Path(wd or workdir(job)) / "_fillers_silence")}
        res = fn(sub, _Nested(emit)) or {}
        doc = RV.load(res["review"])
    except Cancelled:
        raise
    except Exception as e:  # noqa: BLE001 - another tool's failure must not sink the filler result
        msg = getattr(e, "msg", None) or f"{type(e).__name__}: {e}"
        return [], {"ok": False, "msg": tr("fillers.silence.failed", msg=msg)}
    items = []
    for it in doc.get("items", []):
        try:
            g = RV.item(it["t0"], it["t1"], "gap", on=bool(it.get("on", True)), conf=it.get("conf"),
                        label=it.get("label") or tr("fillers.gapLabel"), ctx=it.get("ctx"), note=it.get("note"), tier="gap",
                        src="silence", srcs=["silence"], tight=False, sub="silence")
        except (KeyError, TypeError, ValueError):
            continue
        if isinstance(it.get("cut"), (list, tuple)) and len(it["cut"]) == 2:
            g["cut"] = [round(float(it["cut"][0]), 3), round(float(it["cut"][1]), 3)]
        items.append(g)
    on = [it.get("cut") or [it["t0"], it["t1"]] for it in items if it["on"]]
    return items, {"ok": True, "n": len(items), "on": len(on), "review": res.get("review"),
                   "sec_on": round(R.total(on), 3)}


# --------------------------------------------------------------------------- actions

def analyze(job, emit):
    P = params(job.get("params"))
    tl = Timeline.from_json(job.get("seq"))
    if not tl.audio_clips():
        raise EngineError("NO_AUDIO", tr("fillers.err.noAudio"), tr("fillers.err.noAudioHint"))
    paths = [p for p in tl.media_paths("audio") if Path(p).is_file()]
    if not paths:
        raise EngineError("NO_MEDIA", tr("fillers.err.noMedia"), tr("fillers.err.noMediaHint"))
    stages = [("words", tr("fillers.stage.words"), 0.4), ("listen", tr("fillers.stage.listen"), 0.2),
              ("sound", tr("fillers.stage.sound"), 0.25)]
    if P["with_silence"]:
        stages.append(("gaps", tr("fillers.stage.gaps"), 0.1))
    stages.append(("review", tr("fillers.stage.review"), 0.05))
    emit.plan(stages)

    audio = {}

    def pcm(p):
        if p not in audio:
            audio[p] = M.load_audio(p)
        return audio[p]

    words, listen = {}, {}
    missing = [p for p in paths if not (_words_cached(p) and _listen_cached(p))]
    with contextlib.ExitStack() as gpu:
        with emit.step("words"):
            if missing:   # one Whisper job at a time: hold the lock for both passes
                gpu.enter_context(gpu_lock(emit, label=f"filler {Path(missing[0]).name}"))
            for k, p in enumerate(paths):
                need = not _words_cached(p)
                words[p] = T.words(p, emit=_Part(emit, k, len(paths)), audio=pcm(p) if need else None)
        with emit.step("listen"):
            for k, p in enumerate(paths):
                need = not _listen_cached(p)
                try:
                    listen[p] = T.listener_words(p, emit=_Part(emit, k, len(paths)), audio=pcm(p) if need else None)
                except EngineError as e:      # the listener only adds recall: degrade, never block
                    if e.code in ("CANCELLED", "GPU_BUSY", "NO_MEDIA", "NO_AUDIO"):
                        raise
                    listen[p] = []
                    emit.warn(tr("fillers.warn.listenFailed", msg=e.msg))

    per_media, cached = {}, 0
    with emit.step("sound"):
        for k, p in enumerate(paths):
            part = _Part(emit, k, len(paths))
            F = FA.cached_features(p)
            if F is None:
                F = FA.features(pcm(p), progress=lambda v: part.progress(0.7 * v))
                FA.save_features(p, F)
            else:
                cached += 1
            audio.pop(p, None)
            per_media[p] = source_candidates(F, words[p], listen[p], P,
                                             progress=lambda v: part.progress(70 + 0.3 * v))
        if cached == len(paths):
            emit.done_note(tr("fillers.note.cached"))

    gap_items, sil = [], None
    if P["with_silence"]:
        with emit.step("gaps"):
            gap_items, sil = silence_items(job, emit, P["scope"], P["silence"], workdir(job, tl.name))
            if not sil["ok"]:
                emit.warn(sil["msg"])

    with emit.step("review"):
        items = to_timeline(tl, per_media, P["scope"])
        tl_words = tl.words_on_timeline(transcribe=False)
        add_context(items, tl_words)
        for it in items:
            it["on"] = it["conf"] >= P["threshold"] - 1e-9 and it["sub"] != "drawl"
        counts = {t: sum(1 for it in items if it["tier"] == t) for t in ("always", "anu", "habit", "acoustic")}
        counts["gap"] = len(gap_items)
        hc = habit_counts(tl_words)
        path = RV.path_for(workdir(job, tl.name), "fillers")
        old = read_json(path)
        doc = RV.new("fillers", items + gap_items, tl.duration, seq=tl,
                     params={k: v for k, v in P.items() if k not in ("scope", "silence")}, fps=tl.fps,
                     threshold=P["threshold"], counts=counts, habit_counts=hc, silence=sil,
                     media=[Path(p).name for p in paths])
        if isinstance(old, dict) and old.get("tool") == "fillers" and (old.get("seq") or {}).get("id") == tl.id:
            RV.carry_over(doc, old)
            doc["stats"] = RV.summary(doc)
        RV.save(doc, path)
        emit.progress(100)
    return {"review": str(path), "summary": RV.summary(doc), "counts": counts, "habit_counts": hc,
            "silence": sil, "threshold": P["threshold"]}


def apply(job, emit):
    with emit.step("plan", tr("fillers.stage.plan")):
        doc = RV.from_job(job)
        if doc.get("tool") != "fillers":
            raise EngineError("BAD_REVIEW", tr("fillers.err.badReview"), tr("fillers.err.badReviewHint"))
        rng = RV.selected_ranges(doc)
        fps = float(doc.get("fps") or 0)
        if fps <= 0 and isinstance(job.get("seq"), dict):
            fps = float(job["seq"].get("fps") or 0)
        if fps > 0:
            rng = R.to_frames(rng, fps, "inner")       # never eat a kept frame (consonants)
        n_fill = len(RV.selected(doc, FILLER_KINDS))
        n_gap = len(RV.selected(doc, ("gap",)))
        sec = R.total(rng)
        emit.progress(100)
    sec_s = f"{dec(sec + 1e-9, 1)} {tr('unit.sec')}"
    parts = [tr("fillers.count.filler", n=n_fill)] + ([tr("fillers.count.gap", n=n_gap)] if n_gap else [])
    summary = tr("fillers.summary", what=" + ".join(parts), sec=sec_s)
    return {"plan": {"kind": "remove_ranges", "ranges": R.round_list(rng, 4), "timebase": "sequence"},
            "summary": summary,
            "counts": {"filler": n_fill, "gap": n_gap, "ranges": len(rng)}}


ACTIONS = {"analyze": analyze, "apply": apply}
