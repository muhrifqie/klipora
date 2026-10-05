"""Review files: what an analyze action found, for the user to check before apply (ux_design.md section 11.5).

    {"v": 1, "tool": "silence", "timebase": "sequence", "duration": 48.925, "created": "...+07:00",
     "seq": {"id": "...", "name": "..."}, "params": {...}, "stats": {...},
     "items": [{"id": "g1150", "t0": 11.5, "t1": 15.17, "kind": "gap", "on": true, "conf": 0.9,
                "label": "Pause 3.7 s", "ctx": {"pre": "...", "post": "..."},
                "note": {...}, "cut": [11.56, 15.02], ...tool extras}]}

Rules: times are SEQUENCE seconds (timebase "sequence"; "source" only for legacy single-file flows);
items sorted by t0; ids are stable for the same finding (kind initial + t0 in centiseconds), so a re-run
keeps the user's choices (`carry_over`). The panel only flips `on` (and sets `touched: true`); apply actions
read the edited file back with `load()` and use `selected_ranges()`. Optional `cut` = the exact range to
remove when it differs from the displayed t0..t1. Item label/note texts are UI text (job language, ac.i18n).
"""
from __future__ import annotations

from pathlib import Path

from . import ranges as R
from .i18n import tr
from .util import EngineError, now_iso, read_json, write_json

REVIEW_V = 1


def item(t0, t1, kind, on=True, conf=None, label="", ctx=None, note=None, **extra):
    """One review row. conf in 0..1 (None = not scored); ctx = {"pre","post"} text or a string."""
    it = {"id": None, "t0": round(float(t0), 3), "t1": round(float(t1), 3), "kind": str(kind), "on": bool(on)}
    if conf is not None:
        it["conf"] = round(float(conf), 3)
    if label:
        it["label"] = label
    if ctx is not None:
        it["ctx"] = ctx
    if note is not None:
        it["note"] = note
    it.update(extra)
    return it


def assign_ids(items, prefix=None):
    """Stable ids: <prefix or kind initial><round(t0*100)>, '-2', '-3' on collisions. Keeps given ids."""
    used = {it["id"] for it in items if it.get("id")}
    for it in items:
        if it.get("id"):
            continue
        base = f"{prefix or (it.get('kind') or 'x')[:1]}{int(round(it['t0'] * 100))}"
        nid, k = base, 2
        while nid in used:
            nid, k = f"{base}-{k}", k + 1
        it["id"] = nid
        used.add(nid)
    return items


def new(tool, items, duration, seq=None, params=None, stats=None, timebase="sequence", **meta):
    """Build a review document (items sorted, ids assigned, stats filled with counts if not given)."""
    items = sorted(items, key=lambda it: (it["t0"], it["t1"]))
    assign_ids(items)
    seq_info = None
    if seq is not None:
        seq_info = {"id": getattr(seq, "id", None) or (seq.get("id") if isinstance(seq, dict) else None),
                    "name": getattr(seq, "name", None) or (seq.get("name") if isinstance(seq, dict) else None)}
    doc = {"v": REVIEW_V, "tool": tool, "timebase": timebase, "duration": round(float(duration), 3),
           "created": now_iso(), "seq": seq_info, "params": params or {}, "stats": stats or {}, "items": items}
    doc["stats"].setdefault("n", len(items))
    doc["stats"].setdefault("on", sum(1 for it in items if it["on"]))
    doc["stats"].setdefault("sec_on", round(R.total(_cut_ranges(it for it in items if it["on"])), 3))
    doc.update(meta)
    return doc


def validate(doc):
    """-> list of error strings (empty = valid)."""
    errs = []
    if not isinstance(doc, dict):
        return ["review is not an object"]
    for k in ("tool", "timebase", "duration", "items"):
        if k not in doc:
            errs.append(f"missing '{k}'")
    if doc.get("timebase") not in (None, "sequence", "source"):
        errs.append("timebase must be 'sequence' or 'source'")
    ids = set()
    for i, it in enumerate(doc.get("items") or []):
        if not isinstance(it, dict):
            errs.append(f"items[{i}] not an object")
            continue
        for k in ("id", "t0", "t1", "kind", "on"):
            if k not in it:
                errs.append(f"items[{i}] missing '{k}'")
        if it.get("id") in ids:
            errs.append(f"items[{i}] duplicate id {it.get('id')}")
        ids.add(it.get("id"))
        try:
            if float(it["t1"]) < float(it["t0"]):
                errs.append(f"items[{i}] t1 < t0")
        except (KeyError, TypeError, ValueError):
            errs.append(f"items[{i}] bad t0/t1")
        if len(errs) > 20:
            break
    return errs


def save(doc, path):
    errs = validate(doc)
    if errs:
        raise EngineError("BAD_REVIEW", tr("err.badReview.invalid", detail="; ".join(errs[:3])))
    return write_json(path, doc, indent=1)


def load(path):
    """Read + validate an (edited) review file. EngineError('BAD_REVIEW') when missing/invalid."""
    doc = read_json(path)
    if doc is None:
        raise EngineError("BAD_REVIEW", tr("err.badReview.notFound", name=Path(str(path)).name),
                          tr("err.badReview.hint"))
    errs = validate(doc)
    if errs:
        raise EngineError("BAD_REVIEW", tr("err.badReview.broken", detail="; ".join(errs[:3])), tr("err.badReview.hint"))
    return doc


def from_job(job):
    """The review an apply action should use: job["review"] (edited by the panel)."""
    p = job.get("review")
    if not p:
        raise EngineError("BAD_JOB", tr("err.badReview.needFile"), tr("err.badReview.needFileHint"))
    return load(p)


def path_for(workdir, tool):
    return Path(workdir) / f"{tool}_review.json"


def selected(doc, kinds=None):
    return [it for it in doc.get("items", []) if it.get("on") and (kinds is None or it.get("kind") in kinds)]


def _cut_ranges(items):
    out = []
    for it in items:
        c = it.get("cut")
        out.append([float(c[0]), float(c[1])] if isinstance(c, (list, tuple)) and len(c) == 2
                   else [float(it["t0"]), float(it["t1"])])
    return out


def selected_ranges(doc, kinds=None):
    """Merged ranges of the checked items (their `cut` when present, else t0..t1)."""
    return R.norm(_cut_ranges(selected(doc, kinds)))


def apply_edits(doc, edits):
    """Copy the user's `on` flags into doc. edits = edited review doc, list of items, or {id: bool}.
    Unknown ids are ignored. Returns the number of changed items."""
    if isinstance(edits, dict) and "items" in edits:
        edits = edits["items"]
    if isinstance(edits, list):
        edits = {it.get("id"): bool(it.get("on")) for it in edits if isinstance(it, dict)}
    n = 0
    for it in doc.get("items", []):
        if it["id"] in edits and bool(edits[it["id"]]) != it["on"]:
            it["on"] = bool(edits[it["id"]])
            it["touched"] = True
            n += 1
    return n


def carry_over(new_doc, old_doc):
    """After a re-run: keep the user's manual choices (items with touched=true in the old review) for ids
    that still exist. Returns the number carried."""
    if not old_doc:
        return 0
    prev = {it.get("id"): it for it in old_doc.get("items", []) if it.get("touched")}
    n = 0
    for it in new_doc.get("items", []):
        old = prev.get(it["id"])
        if old is not None:
            it["on"], it["touched"] = bool(old.get("on")), True
            n += 1
    return n


def summary(doc):
    on = selected(doc)
    return {"n": len(doc.get("items", [])), "on": len(on), "sec_on": round(R.total(_cut_ranges(on)), 3)}
