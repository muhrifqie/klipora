"""AI tasks with a result cache: one call per feature, always returns (AI result or rule fallback).

    from ac.ai import tasks
    r = tasks.run("chapters", words, glossary=["TokoKita"], emit=emit)
    r -> {"source": "ai"|"fallback"|"mixed"|"cache"|"none", "result": {...}, "warnings": [...], "stats": {...},
          "model": "grok-fast", "profile": "grok_local"}

words = cache v3 rows or Timeline.words_on_timeline() dicts (then every output time is SEQUENCE time).
Models are SLOTS (DEFAULT_MODEL): "fast" for extraction, "smart" for judgment; every provider profile maps the
slot to its own model (local proxy preset: grok-fast / grok-auto). A concrete name passed as `model` still works.
Only source == "ai" results are cached (%LOCALAPPDATA%/Klipora/ai_cache, key = task + PROMPT_VERSION +
profile + model + params + words), so a second click costs no quota and a fallback is retried next time.
The local proxy profile (grok_local) keeps the old key layout (no profile part), so answers cached before profiles existed still hit.
Lookup tries the provider that would answer first, then every other provider in the route (no paid call while an
answer from a fallback provider is cached); the answer is stored under the provider that really answered.
Remember: AI output is a suggestion; validate locally and show it for review.
"""
from __future__ import annotations

from ..i18n import tr
from . import client as ai
from . import prompts as P
from . import providers as PV
from .text import caption_lines, display_words, sentences

DEFAULT_MODEL = {  # slots; measured during development: judgment tasks better on grok-auto (smart), extraction on grok-fast
    "chapters": "fast", "viral": "smart", "emphasis": "fast", "broll": "fast",
    "repeats": "smart", "filler": "smart", "cleanup": "fast", "meta": "fast",
    "moments": "fast"}
TASKS = tuple(DEFAULT_MODEL)


def prepare(words):
    """-> (display words, sentence units, duration) for prompts."""
    d = display_words(words)
    return d, sentences(d), (d[-1]["end"] if d else 0.0)


def _compact(words):
    out = []
    for w in words:
        if isinstance(w, dict):
            out.append([w.get("text", ""), round(float(w.get("t0", 0)), 2), round(float(w.get("t1", 0)), 2)])
        else:
            out.append([w[0], round(float(w[1]), 2), round(float(w[2]), 2)])
    return out


def _model_part(ident, model):
    """Cache-key part: "grok-fast" for the default local proxy profile (same keys as before profiles existed),
    "<profile>:<model>" for every other provider."""
    if not ident:
        return model
    pid, m = ident[0], ident[1]
    return m if pid == "grok_local" else f"{pid}:{m}"


def run(task, words, model=None, glossary=(), style="natural", n=5, name="Video", platform="youtube",
        cache=True, emit=None, chapters=None):
    """Run one AI task. Never raises for AI problems (fallback instead); ValueError for an unknown task."""
    if task not in DEFAULT_MODEL:
        raise ValueError(f"unknown AI task {task!r}; one of {', '.join(TASKS)}")
    glossary = [g for g in (glossary or []) if g]
    d, s, dur = prepare(words)
    if not d:
        return {"source": "none", "result": {}, "warnings": [tr("ai.noWords")], "stats": {}, "model": None}
    model = model or DEFAULT_MODEL[task]
    store = ai.Cache()
    compact = _compact(words)

    def key_for(ident):
        return store.key(task, P.PROMPT_VERSION, _model_part(ident, model), glossary, style, n, platform, name,
                         chapters, compact)
    ident = ai.cache_identity(model)
    if cache:
        hit = None
        for cand in [ident] + [c for c in ai.cache_identities(model) if c != ident]:
            hit = store.get(key_for(cand))
            if hit:
                break
        if hit:
            hit["source_original"], hit["source"] = hit.get("source"), "cache"
            if emit:
                emit.done_note(tr("ai.fromCache"))
            return hit
    if emit:
        emit.log(f"AI {task} ({ident[1] if ident else model}{', ' + ident[0] if ident else ''})")
    mark = ai.metrics_mark()
    jobs = {
        "chapters": lambda: P.chapters(s, dur, model=model, glossary=glossary),
        "viral": lambda: P.viral(s, n=n, model=model, glossary=glossary),
        "emphasis": lambda: P.emphasis(d, s, caption_lines(d), model=model),
        "broll": lambda: P.broll(s, model=model, brands=glossary),
        "moments": lambda: P.moments(d, s, model=model),
        "repeats": lambda: P.repeats(d, s, model=model),
        "filler": lambda: P.fillers(d, model=model, style=style),
        "cleanup": lambda: P.cleanup2(d, s, glossary, model=model),
        "meta": lambda: P.meta(s, chapters if chapters is not None else
                               P.chapters(s, dur, model=model, glossary=glossary)["result"]["chapters"],
                               platform=platform, model=model, glossary=glossary, name=name),
    }
    out = jobs[task]()
    served = ai.served_since(mark) if out.get("source") in ("ai", "mixed") else None
    note = ""
    if served:
        out["profile"], out["model"] = served[0], served[1]
        if served[2]:
            names = {p["id"]: PV.display_name(p) for p in PV.profiles()}
            note = tr("ai.answeredBy", failed=", ".join(names.get(x, x) for x in served[2]),
                      name=names.get(served[0], served[0]))
    else:
        out["profile"], out["model"] = (ident[0], ident[1]) if ident else (None, model)
    if emit:
        for w in out.get("warnings", [])[:2]:
            if out["source"] != "ai":
                emit.warn(tr("ai.rulesUsed", why=w))
    if cache and out.get("source") == "ai":
        store.put(key_for(served[:2] if served else ident), out)   # stored without the one-off provider note
    if note:
        out.setdefault("warnings", []).append(note)
        if emit:
            emit.log(note)
    return out


def health():
    return ai.health()
