"""The captions document: one JSON per sequence (<workdir>\\captions.json), the single source of truth for the
editor UI, previews, renders, SRT and MOGRT exports. Schema and every edit op: docs/CAPTIONS_API.md.

Design:
  * doc.words = one entry per Whisper word on the timeline, id = "<media hash10>:<cache row>" (stable across
    re-runs and timeline edits). Deterministic fixes (glossary, numbers), filler and profanity flags are
    recomputed on every build; the USER layer (word.edit, word.style, doc.page_over, doc.ranges, doc.style,
    doc.template) is carried over by id, so re-running never loses manual work.
  * doc.pages is DERIVED (pagination by the doc template): rebuilt by every `doc` call. Renders, previews and
    exports read doc.pages, so what the editor shows is exactly what is rendered.
  * Style resolution per page: template -> doc.style -> ranges -> page_over[..].style (+ y); per word: template
    emphasis rules -> word.style.
"""
from __future__ import annotations

import copy
import re
import time
from pathlib import Path

from ..i18n import tr
from ..util import EngineError, data_hash, now_iso, read_json, workdir, write_json
from . import glossary as G
from . import layout as L
from . import templates as TP

DOC_V = 1
DOC_FILE = "captions.json"
CENSOR_MODES = ("auto", "off", "stars", "first", "full", "bleep")
DEFAULT_PARAMS = {"hide_fillers": True, "censor": "auto", "numbers": True, "glossary": [], "min_word": 0.08,
                  "tracks": None, "scope": "all"}
_LIB_STYLE = {"stars": "stars", "full": "full", "bip": "bleep", "off": "off"}   # profanity library captionStyle
EDIT_KEYS = ("text", "hide", "censor", "merged", "brk", "src")
FAST_CPS = 17.0          # reading-speed warning (chars/s), subtitle heuristic


# ---------------------------------------------------------------- files

def doc_path(job):
    p = (job.get("params") or {}).get("doc")
    return Path(p) if p else workdir(job) / DOC_FILE


def load(path, required=True):
    d = read_json(path)
    if not isinstance(d, dict) or not isinstance(d.get("words"), list):
        if required:
            raise EngineError("NO_CAPTIONS", tr("cap.model.noDoc"), tr("cap.model.buildFirst"))
        return None
    d.setdefault("page_over", {})
    d.setdefault("ranges", [])
    d.setdefault("style", {})
    d.setdefault("params", dict(DEFAULT_PARAMS))
    d.setdefault("pages", [])
    return d


def save(doc, path):
    doc["updated"] = now_iso()
    return write_json(path, doc)


def from_job(job, required=True):
    """(doc, path) for jobs that work on an existing document (preview/render/srt/...)."""
    p = doc_path(job)
    return load(p, required), p


# ---------------------------------------------------------------- patches

def merge_patch(base, patch):
    """Deep merge where None deletes a key (JSON merge patch). Returns a new dict."""
    out = copy.deepcopy(base) if isinstance(base, dict) else {}
    for k, v in (patch or {}).items():
        if v is None:
            out.pop(k, None)
        elif isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = merge_patch(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


# ---------------------------------------------------------------- timeline -> words

def clips_hash(tl):
    """Changes when the audio edit changes (re-cut, moved, trimmed): the panel shows "Perbarui caption"."""
    rows = [[c.path, round(c.start, 3), round(c.end, 3), round(c.src_in, 3), round(c.src_out, 3)]
            for c in tl.audio_clips()]
    return data_hash(rows)


def seq_info(tl):
    return {"id": tl.id, "name": tl.name, "w": tl.width, "h": tl.height, "fps": tl.fps, "duration": tl.duration,
            "clipsHash": clips_hash(tl)}


def censor_mode(doc):
    """Effective caption censor style: the doc param, or with "auto" the profanity tool's caption switch
    ("Sensor juga caption", stored in info at build time), else stars."""
    m = ((doc.get("params") or {}).get("censor") or "auto")
    if m == "auto":
        m = (doc.get("info") or {}).get("censor_style") or "stars"
    return m if m in CENSOR_MODES and m != "auto" else "stars"


def profanity_flags(words, job=None, tl=None, tl_words=None):
    """Indices of words to censor + (source, library caption style). Order: the profanity tool's
    hits_for_timeline() (its reviewed decisions per media word, else its lexicon rules; no AI/GPU), else the
    review file <workdir>/profanity_review.json (items with on=true), else a small built-in list."""
    try:
        from ..tools import profanity as P  # noqa: PLC0415 - optional tool (another agent's module)
    except Exception:  # noqa: BLE001
        P = None
    if P is not None and tl is not None and hasattr(P, "hits_for_timeline"):
        try:
            lib_style = _LIB_STYLE.get((P.load_library() or {}).get("captionStyle"), "stars")
            hits = P.hits_for_timeline(tl, words=tl_words, style="stars")
            ids = {h["id"] for h in hits}
            return {i for i, w in enumerate(words) if w["id"].split("~")[0] in ids}, "profanity", lib_style
        except Exception:  # noqa: BLE001 - never let the optional tool break captions
            pass
    out = set()
    if job is not None:
        try:
            rv = read_json(workdir(job) / "profanity_review.json")
        except OSError:
            rv = None
        if isinstance(rv, dict) and isinstance(rv.get("items"), list):
            spans = [(float(it["t0"]), float(it["t1"])) for it in rv["items"] if it.get("on") and "t0" in it]
            for i, w in enumerate(words):
                mid = (w["t0"] + w["t1"]) / 2
                if any(a - 0.02 <= mid <= b + 0.02 for a, b in spans):
                    out.add(i)
            if spans:
                return out, "review", None
    return {i for i, w in enumerate(words) if G.norm(w["raw"]) in G.PROFANITY_ID}, "builtin", None


def build_words(tl_words, old=None, params=None, job=None, glossary=None, tl=None):
    """Doc word list from Timeline.words_on_timeline() rows, carrying user edits/styles from `old` by id.
    glossary = terms for the deterministic fixes (default params['glossary'])."""
    params = {**DEFAULT_PARAMS, **(params or {})}
    old_by = {w["id"]: w for w in (old or {}).get("words", [])} if old else {}
    words, seen = [], {}
    for r in tl_words:
        base = str(r["id"])
        n = seen.get(base, 0) + 1
        seen[base] = n
        wid = base if n == 1 else f"{base}~{n}"
        raw = str(r["text"]).strip()
        if not raw:
            continue
        w = {"id": wid, "t0": round(float(r["t0"]), 3), "t1": round(float(r["t1"]), 3), "raw": raw,
             "p": round(float(r.get("p", 1.0)), 3), "seg": int(r.get("seg", 0) or 0), "clip": r.get("clip"),
             "track": r.get("track")}
        o = old_by.get(wid)
        if o:
            if o.get("edit"):
                w["edit"] = copy.deepcopy(o["edit"])
            if o.get("style"):
                w["style"] = copy.deepcopy(o["style"])
        words.append(w)
    # deterministic fixes (glossary first, then numbers) as span fixes on the RAW text
    rows = [{"text": w["raw"]} for w in words]
    gl = [g for g in (params.get("glossary") if glossary is None else glossary) or [] if g]
    fixes = G.merge_fixes(G.glossary_fixes(rows, gl), G.number_fixes(rows) if params.get("numbers") else [])
    for f in fixes:
        head = words[f["i"]]
        head["auto"] = {"text": f["text"], "kind": f["kind"], "n": f["n"], "old": f["old"]}
        for k in range(1, f["n"]):
            words[f["i"] + k]["auto_merged"] = head["id"]
    for w in words:
        if G.is_filler(w["raw"]):
            w["filler"] = True
    prof, src, lib_style = profanity_flags(words, job, tl, tl_words)
    for i in prof:
        words[i]["profane"] = True
    info = {"fixes": len(fixes), "profane": len(prof), "profanity_source": src}
    if lib_style:
        info["censor_style"] = lib_style
    return words, info


def resolve_words(doc):
    """Effective text / hide / censor / merged for every word (user edit > auto > raw)."""
    prm = {**DEFAULT_PARAMS, **(doc.get("params") or {})}
    censor_on = censor_mode(doc) != "off"
    ids = {w["id"] for w in doc["words"]}
    for w in doc["words"]:
        e = w.get("edit") or {}
        if "text" in e and e["text"] is not None:
            w["text"] = str(e["text"])
        elif w.get("auto"):
            w["text"] = w["auto"]["text"]
        else:
            w["text"] = w["raw"]
        w["merged"] = e["merged"] if "merged" in e else w.get("auto_merged")
        if not w["merged"] or w["merged"] not in ids or w["merged"] == w["id"]:
            w.pop("merged", None)
        w["hide"] = bool(e["hide"]) if e.get("hide") is not None else bool(w.get("filler") and prm["hide_fillers"])
        w["censor"] = bool(e["censor"]) if e.get("censor") is not None else bool(w.get("profane") and censor_on)
    return doc


# ---------------------------------------------------------------- templates per doc / page

def base_template(doc):
    s = doc.get("seq") or {}
    W, H = int(s.get("w") or 1920), int(s.get("h") or 1080)
    tpl = TP.load(doc.get("template"), W, H)
    if doc.get("style"):
        tpl = L.deep_merge(tpl, doc["style"])
    return L.prep_template(tpl)


def _range_for(doc, t):
    out = []
    for r in doc.get("ranges") or []:
        try:
            if float(r["t0"]) <= t < float(r["t1"]):
                out.append(r)
        except (KeyError, TypeError, ValueError):
            continue
    return out


def page_template(doc, page, base, cache=None):
    """Resolved template for one page (shares `base` when the page has no overrides)."""
    over = (doc.get("page_over") or {}).get(page.get("over") or "")
    rngs = _range_for(doc, float(page["t0"]))
    if not over and not rngs:
        return base
    key = data_hash([over, rngs])
    if cache is not None and key in cache:
        return cache[key]
    tpl = base
    s = doc.get("seq") or {}
    for r in rngs:
        st = dict(r.get("style") or {})
        if st.get("template"):
            tpl = TP.load(st.pop("template"), int(s.get("w") or 1920), int(s.get("h") or 1080))
        tpl = L.deep_merge(tpl, st)
    if over:
        if over.get("auto_style"):          # automatic contrast box (smart.autoposition contrast=true)
            tpl = L.deep_merge(tpl, over["auto_style"])
        if over.get("style"):
            tpl = L.deep_merge(tpl, over["style"])
        if over.get("y") is not None:
            tpl = L.deep_merge(tpl, {"layout": {"y": float(over["y"])}})
    tpl = L.prep_template(tpl)
    if cache is not None:
        cache[key] = tpl
    return tpl


# ---------------------------------------------------------------- render words + pagination

def render_words(doc, tpl):
    """Visible display words (hidden/merged removed, merged spans joined, censor applied, template case/punct
    applied) -> [RWord] in time order."""
    prm = {**DEFAULT_PARAMS, **(doc.get("params") or {})}
    mode = censor_mode(doc)
    words = doc["words"]
    followers = {}
    for w in words:
        if w.get("merged"):
            followers.setdefault(w["merged"], []).append(w)
    out = []
    min_word = float(prm.get("min_word", 0.08) or 0.0)
    start = True                     # sentence start (font.case "sentence")
    for w in words:
        if w.get("merged") or w.get("hide"):
            continue
        group = [w] + followers.get(w["id"], [])
        text = (w.get("text") or "").strip()
        if not text:
            continue
        cap, start = start, text.rstrip("\"')”’")[-1:] in ".?!"
        keep = bool((w.get("edit") or {}).get("text")) or (w.get("auto") or {}).get("kind") == "glossary"
        if w.get("censor"):
            text = G.mask(text, mode if mode != "off" else "stars")   # a manual censor still masks
        style = {k: v for k, v in (w.get("style") or {}).items() if v is not None and k in L.WORD_STYLE_KEYS}
        text, emj = L.split_emoji(text)
        if emj and not style.get("emoji"):
            style["emoji"] = emj
        if not text:
            text = " "           # emoji-only word: keep a thin slot so the emoji has an anchor
        t0 = w["t0"]
        t1 = max(x["t1"] for x in group)
        t1 = max(t1, t0 + min_word)
        seg = group[-1].get("seg", 0)
        shown = L.transform_text(text, tpl, style, cap, keep)
        out.append(L.RWord(text=shown or text, start=t0, end=round(t1, 3), style=style, id=w["id"], seg_end=seg,
                           brk=(w.get("edit") or {}).get("brk"), sentence_end=text[-1:] in ".?!" or bool(seg),
                           comma=text[-1:] in ",;:", raw=text, cap=cap, keep=keep))
    out.sort(key=lambda r: r.start)
    return out


def repaginate(doc):
    """Recompute doc['pages'] (and stats) from words + template + user flags. Returns doc."""
    t = time.perf_counter()
    resolve_words(doc)
    s = doc.get("seq") or {}
    W, H = int(s.get("w") or 1920), int(s.get("h") or 1080)
    tpl = base_template(doc)
    rws = render_words(doc, tpl)
    caps = L.paginate(rws, tpl, W, H)
    by_id = {w["id"]: w for w in doc["words"]}
    over = doc.get("page_over") or {}
    pages, tcache = [], {}
    for i, c in enumerate(caps):
        ids = [[rw.id for rw in ln.words] for ln in c.lines]
        flat = [x for ln in ids for x in ln]
        okey = next((x for x in flat if x in over), None)
        page = {"id": c.id, "i": i, "t0": c.start, "t1": c.end, "lines": ids,
                "text": "\n".join(" ".join(rw.text for rw in ln.words) for ln in c.lines)}
        if okey:
            page["over"] = okey
        ptpl = page_template(doc, page, tpl, tcache)
        page["y"] = round(float(ptpl["layout"]["y"]), 2)
        o = over.get(okey) if okey else None
        page["pos"] = None if not o or o.get("y") is None else ("auto" if o.get("auto") else "manual")
        chars = sum(len(rw.raw) for ln in c.lines for rw in ln.words)
        dur = max(0.01, max(rw.end for rw in c.words) - c.words[0].start)
        page["cps"] = round(chars / dur, 1)
        if page["cps"] > FAST_CPS and len(flat) > 1:
            page["fast"] = True
        low = [x for x in flat if (by_id.get(x) or {}).get("p", 1.0) < 0.5]
        if low:
            page["low"] = low
        pages.append(page)
    doc["pages"] = pages
    doc["stats"] = stats(doc)
    doc["stats"]["paginate_ms"] = round((time.perf_counter() - t) * 1000)
    doc["hash"] = data_hash([doc.get("template"), doc.get("style"), doc.get("ranges"), over,
                             [(p["t0"], p["t1"], p["lines"]) for p in pages],
                             [(w["id"], w.get("text"), w.get("style"), w.get("censor")) for w in doc["words"]
                              if not w.get("hide")], doc.get("seq", {}).get("w"), doc.get("seq", {}).get("h")])
    return doc


def stats(doc):
    ws = doc["words"]
    pages = doc.get("pages") or []
    return {"words": len(ws), "pages": len(pages), "hidden": sum(1 for w in ws if w.get("hide")),
            "censored": sum(1 for w in ws if w.get("censor") and not w.get("hide")),
            "fixes": sum(1 for w in ws if w.get("auto")), "edited": sum(1 for w in ws if w.get("edit") or w.get("style")),
            "low_conf": sum(1 for w in ws if w.get("p", 1) < 0.5 and not w.get("hide")),
            "fast_pages": sum(1 for p in pages if p.get("fast")),
            "start": pages[0]["t0"] if pages else None, "end": pages[-1]["t1"] if pages else None}


def page_pairs(doc, pages=None, t=None, window=0.0):
    """[(Caption, resolved template)] for compile.py from doc['pages'] (no re-pagination). `t` limits to
    pages visible at t (+- window); `pages` = explicit page dicts."""
    s = doc.get("seq") or {}
    W, H = int(s.get("w") or 1920), int(s.get("h") or 1080)
    base = base_template(doc)
    tpl_rw = base
    rws = {rw.id: rw for rw in render_words(resolve_words(doc), tpl_rw)}
    sel = pages if pages is not None else doc.get("pages") or []
    if t is not None:
        sel = [p for p in sel if p["t0"] - window <= t <= p["t1"] + window]
    out, tcache = [], {}
    for p in sel:
        ptpl = page_template(doc, p, base, tcache)
        lines = []
        for ln in p["lines"]:
            ws = []
            for wid in ln:
                rw = rws.get(wid)
                if rw is None:
                    continue
                if ptpl is not base:      # page template may change case/punct
                    rw = copy.copy(rw)
                    rw.text = L.transform_text(rw.raw, ptpl, rw.style, rw.cap, rw.keep) or rw.text
                ws.append(rw)
            if ws:
                lines.append(L.Line(ws))
        if lines:
            out.append((L.Caption(lines, float(p["t0"]), float(p["t1"]), id=p["id"], index=int(p.get("i", -1))),
                        ptpl))
    return out, W, H


# ---------------------------------------------------------------- build / update

def build(tl, tl_words, old=None, params=None, job=None, template=None, glossary=None):
    """New or updated doc for a Timeline. `old` = previous doc (user layer carried over). `glossary` = all
    terms for the deterministic fixes (settings + doc); params['glossary'] keeps only the doc's own list."""
    params = {**DEFAULT_PARAMS, **((old or {}).get("params") or {}), **{k: v for k, v in (params or {}).items()
                                                                      if k in DEFAULT_PARAMS}}
    words, info = build_words(tl_words, old, params, job, glossary, tl)
    W, H = tl.width, tl.height
    doc = {"v": DOC_V, "kind": "autocut.captions", "seq": seq_info(tl),
           "created": (old or {}).get("created") or now_iso(), "updated": now_iso(), "lang": "id",
           "template": TP.slug(template) if template else ((old or {}).get("template") or TP.default_id(W, H)),
           "style": copy.deepcopy((old or {}).get("style") or {}), "params": params,
           "page_over": copy.deepcopy((old or {}).get("page_over") or {}),
           "ranges": copy.deepcopy((old or {}).get("ranges") or []),
           "render": copy.deepcopy((old or {}).get("render") or {}), "words": words, "pages": [], "info": info}
    if TP.raw(doc["template"])[0] is None:
        doc["template"] = TP.default_id(W, H)
    return repaginate(doc)


# ---------------------------------------------------------------- edit ops

def _ids(doc, op):
    ids = op.get("ids")
    if ids is None and op.get("id"):
        ids = [op["id"]]
    idx = {w["id"]: i for i, w in enumerate(doc["words"])}
    bad = [x for x in ids or [] if x not in idx]
    if bad or not ids:
        raise EngineError("BAD_OP", tr("cap.model.wordNotFound", ids=", ".join(map(str, bad[:3])) or tr("cap.model.empty")),
                          tr("cap.model.reloadRetry"))
    return sorted(ids, key=lambda x: idx[x]), idx


def _edit(w, **kv):
    e = dict(w.get("edit") or {})
    for k, v in kv.items():
        if v is None:
            e.pop(k, None)
        else:
            e[k] = v
    if e:
        w["edit"] = e
    else:
        w.pop("edit", None)


def _page_key(doc, op):
    """page_over key for an op: explicit word id, or a page id 'p:<word id>' / page index."""
    if op.get("id"):
        return str(op["id"])
    pg = op.get("page")
    if isinstance(pg, int):
        pages = doc.get("pages") or []
        if 0 <= pg < len(pages):
            return pages[pg].get("over") or pages[pg]["lines"][0][0]
    if isinstance(pg, str):
        for p in doc.get("pages") or []:
            if p["id"] == pg:
                return p.get("over") or p["lines"][0][0]
        if pg.startswith("p:"):
            return pg[2:]
    raise EngineError("BAD_OP", tr("cap.model.pageNotFound"), tr("cap.model.reloadDoc"))


def text_edit(doc, ids, text, src="user"):
    """Replace the display text of a consecutive span. Same word count -> per-word texts (own timing);
    otherwise the first word carries the whole text and the others merge into it (timing = whole span)."""
    by = {w["id"]: w for w in doc["words"]}
    parts = str(text).split()
    span = [by[i] for i in ids]
    if not parts:
        for w in span:
            _edit(w, hide=True, src=src)
        return
    if len(parts) == len(span):
        for w, p in zip(span, parts):
            _edit(w, text=p, merged="", src=src, hide=None)
    else:
        _edit(span[0], text=" ".join(parts), merged="", src=src, hide=None)
        for w in span[1:]:
            _edit(w, merged=span[0]["id"], src=src)


def apply_ops(doc, ops):
    """Apply editor operations in order (docs/CAPTIONS_API.md section 4). Returns a list of notes."""
    notes = []
    for op in ops or []:
        kind = op.get("op")
        if kind in ("text", "accept"):
            edits = op.get("edits") if kind == "accept" else [op]
            for e in edits or []:
                ids, _ = _ids(doc, e)
                text_edit(doc, ids, e.get("text", e.get("new", "")), src="ai" if kind == "accept" else "user")
        elif kind == "reset":
            ids, _ = _ids(doc, op)
            fields = op.get("fields")
            for wid in ids:
                w = next(x for x in doc["words"] if x["id"] == wid)
                if not fields:
                    w.pop("edit", None)
                    w.pop("style", None)
                else:
                    if "style" in fields:
                        w.pop("style", None)
                    _edit(w, **{f: None for f in fields if f in EDIT_KEYS})
        elif kind == "style":
            ids, _ = _ids(doc, op)
            patch = {k: v for k, v in (op.get("style") or {}).items() if k in L.WORD_STYLE_KEYS}
            for wid in ids:
                w = next(x for x in doc["words"] if x["id"] == wid)
                st = merge_patch(w.get("style") or {}, patch)
                if st:
                    w["style"] = st
                else:
                    w.pop("style", None)
        elif kind in ("hide", "censor"):
            ids, _ = _ids(doc, op)
            val = op.get("on")
            for wid in ids:
                w = next(x for x in doc["words"] if x["id"] == wid)
                _edit(w, **{kind: None if val is None else bool(val)})
        elif kind == "break":
            ids, _ = _ids(doc, op)
            val = op.get("kind")
            if val not in (None, "page", "line", "join"):
                raise EngineError("BAD_OP", tr("cap.model.badBreak", kind=val))
            for wid in ids:
                _edit(next(x for x in doc["words"] if x["id"] == wid), brk=val)
        elif kind in ("page_style", "page_pos"):
            key = _page_key(doc, op)
            over = doc.setdefault("page_over", {})
            cur = dict(over.get(key) or {})
            if kind == "page_style":
                st = merge_patch(cur.get("style") or {}, op.get("style") or {}) if op.get("style") is not None else {}
                if st:
                    cur["style"] = st
                else:
                    cur.pop("style", None)
            else:
                if op.get("y") is None:
                    cur.pop("y", None)
                    cur.pop("auto", None)
                    cur.pop("why", None)
                else:
                    cur["y"] = round(max(0.0, min(100.0, float(op["y"]))), 2)
                    cur["auto"] = False
                    cur.pop("why", None)
            if cur:
                over[key] = cur
            else:
                over.pop(key, None)
        elif kind == "template":
            tid = TP.slug(op.get("id") or op.get("template") or "")
            if TP.raw(tid)[0] is None:
                raise EngineError("BAD_TEMPLATE", tr("cap.model.tplNotFound", id=tid), tr("cap.model.pickTpl"))
            doc["template"] = tid
            if op.get("keep_style") is not True:
                doc["style"] = {}
        elif kind == "doc_style":
            doc["style"] = (op.get("style") or {}) if op.get("replace") else merge_patch(doc.get("style") or {},
                                                                                         op.get("style") or {})
        elif kind == "range_style":
            t0, t1 = float(op["t0"]), float(op["t1"])
            doc.setdefault("ranges", [])
            doc["ranges"] = [r for r in doc["ranges"] if not (abs(r["t0"] - t0) < 1e-3 and abs(r["t1"] - t1) < 1e-3)]
            if op.get("style"):
                doc["ranges"].append({"t0": round(t0, 3), "t1": round(t1, 3), "style": op["style"]})
                doc["ranges"].sort(key=lambda r: r["t0"])
        elif kind == "range_clear":
            doc["ranges"] = []
        elif kind == "replace":
            n = find_replace(doc, op.get("find", ""), op.get("replace", ""), bool(op.get("case")),
                             op.get("whole", True) is not False)
            notes.append({"op": "replace", "n": n})
        elif kind == "params":
            prm = dict(doc.get("params") or DEFAULT_PARAMS)
            for k in DEFAULT_PARAMS:
                if k in op:
                    prm[k] = op[k]
            if prm.get("censor") not in CENSOR_MODES:
                prm["censor"] = "auto"
            doc["params"] = prm
        elif kind == "auto_emoji":
            notes.append({"op": "auto_emoji", "n": auto_emoji(doc, op.get("density", "normal"), op.get("map"))})
        elif kind == "clear_emoji":
            for w in doc["words"]:
                if (w.get("style") or {}).get("emoji"):
                    st = {k: v for k, v in w["style"].items() if k != "emoji"}
                    if st:
                        w["style"] = st
                    else:
                        w.pop("style", None)
        elif kind == "clear_auto_pos":
            over = doc.get("page_over") or {}
            for k in list(over):
                if over[k].get("auto"):
                    over[k] = {kk: vv for kk, vv in over[k].items() if kk not in ("y", "auto", "why")}
                    if not over[k]:
                        over.pop(k)
        else:
            raise EngineError("BAD_OP", tr("cap.model.badOp", op=kind), tr("cap.model.updatePanel"))
    return notes


EMOJI_GAP = {"sedikit": 15.0, "normal": 8.0, "banyak": 4.0}


def auto_emoji(doc, density="normal", extra=None):
    """Give Indonesian keywords an emoji (word style) at most every N s (sedikit 15 / normal 8 / banyak 4).
    Words that already have an emoji count as placed. Returns how many were added."""
    from . import emoji as E
    gap = EMOJI_GAP.get(density, 8.0)
    resolve_words(doc)
    last, n = -1e9, 0
    for w in doc["words"]:
        if w.get("hide") or w.get("merged"):
            continue
        if (w.get("style") or {}).get("emoji"):
            last = w["t0"]
            continue
        e = E.suggest(w.get("text") or "", extra)
        if e and w["t0"] - last >= gap:
            w["style"] = dict(w.get("style") or {}, emoji=e)
            last = w["t0"]
            n += 1
    return n


def find_replace(doc, find, repl, case=False, whole=True):
    """Find & replace over the display words (multi-word find allowed). Returns the number of replacements."""
    resolve_words(doc)
    target = [x for x in str(find).split() if x]
    if not target:
        return 0
    norm = (lambda s: re.sub(r"[^\w-]", "", s)) if case else (lambda s: re.sub(r"[^\w-]", "", s.lower()))
    tgt = [norm(x) for x in target]
    vis = [w for w in doc["words"] if not w.get("merged") and not w.get("hide")]
    n, i = 0, 0
    while i + len(tgt) <= len(vis):
        win = vis[i:i + len(tgt)]
        cand = [norm(w["text"]) for w in win]
        hit = cand == tgt if whole else (len(tgt) == 1 and tgt[0] in cand[0])
        if hit:
            last = win[-1]["text"].strip()
            tail = last[len(last.rstrip(",.?!;:")):]
            if whole:
                new = repl + tail if repl else ""
            else:
                flags = 0 if case else re.I
                new = re.sub(re.escape(target[0]), repl, win[0]["text"], flags=flags)
            text_edit(doc, [w["id"] for w in win], new)
            n += 1
            i += len(tgt)
        else:
            i += 1
    return n


# ---------------------------------------------------------------- slim view for the UI

def slim(doc):
    """Doc without private/heavy fields (what `doc` returns inline)."""
    keep_w = ("id", "t0", "t1", "raw", "text", "p", "seg", "auto", "merged", "filler", "profane", "hide", "censor",
              "edit", "style")
    out = {k: v for k, v in doc.items() if k != "words"}
    out["words"] = [{k: w[k] for k in keep_w if k in w} for w in doc["words"]]
    return out
