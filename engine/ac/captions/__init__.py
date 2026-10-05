"""Auto Caption (tool id "captions"): animated word-by-word captions for the active Premiere sequence.

Actions (job protocol, docs/CAPTIONS_API.md):
  doc           build / update the captions document from the timeline words (+ editor ops)
  templates     built-in + user templates with metadata and the editor option catalogue
  gallery       template thumbnails (PNG, cached; anim: sprite-sheet loops)
  preview       PNG of the captions at sequence time t over the real frame / transparent overlay PNG
  autoposition  per-page y avoiding faces / UI text / action, inside the platform safe zone
  render        straight-alpha qtrle overlay (.mov, versioned) -> plan {kind: overlay}
  srt           SRT / VTT for a native caption track -> plan {kind: caption_track}
  burn          short MP4 with burned-in captions (animation / sync check, <= 30 s)
  mogrt         optional native editable MOGRTs (short videos) -> plan {kind: mogrt}
  cleanup       AI text cleanup as a reviewable diff list (applied only on accept)
  save_template save / delete a user template
  fonts         font catalog (bundled OFL + installed Windows fonts, categories) + sample PNGs
  template_mix  new draft style from parts (font/style/box/highlight/anim/layout) of different templates
  template_random curated random style that stays readable
Heavy imports (uharfbuzz, cv2, Pillow) stay inside the action functions: `cli.py tools` imports every tool.
Action functions are named act_<action> so they never shadow the submodules (templates, preview, render, ...).
"""
from __future__ import annotations

from ..i18n import dec, secs, tr

TITLE = "Auto Captions"                 # developer listing (cli.py tools); UI texts: tr("cap.*") at run time
DESCRIPTION = ("Animated word-by-word captions: 50+ templates, per-word editing, "  # i18n-ignore (English dev listing)
               "fast preview, transparent overlay, SRT.")

# Premiere video track of the overlay (same in every UI language). Projects from before the rename have
# "AutoCut Captions": the host finds and reuses that track (gxFindVideoTrack / acNameAlts), it is not renamed.
OVERLAY_TRACK = "Klipora Captions"
FIX_KINDS = ("case_punct", "word_fix", "merge")    # AI cleanup kinds with a label (cap.fix.*)


def _timeline(job, required=True):
    from ..timeline import Timeline
    from ..util import EngineError
    if job.get("seq"):
        return Timeline.from_json(job["seq"])
    if required:
        raise EngineError("NO_SEQ", tr("cap.err.noSeq"), tr("cap.err.noSeqHint"))
    return None


def _terms(src):
    """Glossary terms from a list or a 'a, b; c' / multi-line string."""
    if isinstance(src, str):
        src = src.replace(";", ",").replace("\n", ",").split(",")
    return [x.strip() for x in src or [] if isinstance(x, str) and x.strip()]


def _glossary(doc_terms=()):
    """Settings glossary (panel) + the document's own terms, de-duplicated, order kept."""
    from ..util import setting
    return list(dict.fromkeys(_terms(setting("glossary")) + _terms(doc_terms)))


def _doc_params(p, old):
    from .model import DEFAULT_PARAMS
    out = {k: p[k] for k in DEFAULT_PARAMS if k in p}
    terms = p.get("glossary") if "glossary" in p else ((old or {}).get("params") or {}).get("glossary")
    out["glossary"] = _terms(terms)
    return out


def _is_num(x):
    import math
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def _doc_line(d):
    """History line for a doc result (job language)."""
    st = d.get("stats") or {}
    parts = [tr("cap.line.pages", n=st.get("pages", 0)), tr("cap.line.words", n=st.get("words", 0))]
    if st.get("fixes"):
        parts.append(tr("cap.line.fixes", n=st["fixes"]))
    if st.get("censored"):
        parts.append(tr("cap.line.censored", n=st["censored"]))
    return ", ".join(parts)


# ---------------------------------------------------------------- doc

def act_doc(job, emit):
    """Build or update the captions document. Without job.seq only the edit ops / template / params apply."""
    from ..ranges import norm as rnorm
    from ..util import EngineError
    from . import model as M
    p = job["params"]
    path = M.doc_path(job)
    old = None if p.get("rebuild") else M.load(path, required=False)
    tl = _timeline(job, required=old is None)
    notes = []
    if tl is not None and p.get("check_only"):
        if old is None:
            return {"doc": None, "exists": False, "stale": True, "summary": tr("cap.noCaptions")}
        os_ = old.get("seq") or {}
        stale = M.clips_hash(tl) != os_.get("clipsHash") or [tl.width, tl.height] != [os_.get("w"), os_.get("h")]
        return {"doc": str(path), "exists": True, "stale": stale, "stats": old.get("stats"),
                "template": old.get("template"), "summary": tr("cap.timelineChanged") if stale else tr("cap.inSync")}
    if tl is not None:
        emit.plan([("words", tr("cap.stage.words"), 0.75), ("doc", tr("cap.stage.doc"), 0.25)])
        with emit.step("words"):
            words = tl.words_on_timeline(tracks=p.get("tracks"), transcribe=p.get("transcribe", True) is not False,
                                         emit=emit)
            scope = rnorm(tl.scope_ranges(p.get("scope")))
            if scope != [[0.0, tl.duration]]:
                words = [w for w in words if any(a <= (w["t0"] + w["t1"]) / 2 < b for a, b in scope)]
            if not words:
                raise EngineError("NO_WORDS", tr("cap.err.noWords"), tr("cap.err.noWordsHint"))
        with emit.step("doc"):
            prm = _doc_params(p, old)
            d = M.build(tl, words, old, prm, job, template=p.get("template"), glossary=_glossary(prm["glossary"]))
            if old and p.get("template") and d["template"] != old.get("template") and not p.get("keep_style"):
                d["style"] = {}                     # a new template starts clean (like the template op)
            if p.get("style") is not None:
                d["style"] = M.merge_patch(d.get("style") or {}, p["style"])
            notes = M.apply_ops(d, p.get("ops"))
            M.repaginate(d)
    else:
        emit.plan([("doc", tr("cap.stage.update"), 1.0)])
        with emit.step("doc"):
            d = old
            if p.get("template"):
                notes += M.apply_ops(d, [{"op": "template", "id": p["template"],
                                          "keep_style": bool(p.get("keep_style"))}])
            if p.get("style") is not None:
                d["style"] = M.merge_patch(d.get("style") or {}, p["style"])
            notes += M.apply_ops(d, p.get("ops"))
            M.repaginate(d)
    M.save(d, path)
    out = {"doc": str(path), "summary": _doc_line(d), "stats": d["stats"], "template": d["template"],
           "pages": len(d["pages"]),
           "notes": notes, "info": d.get("info", {}), "hash": d.get("hash"),
           "rendered": bool(d.get("render", {}).get("hash") == d.get("hash"))}
    if p.get("inline"):
        out["data"] = M.slim(d)
    return out


# ---------------------------------------------------------------- templates / gallery / save_template

def act_templates(job, emit):
    from . import templates as TP
    p = job["params"]
    seq = job.get("seq") or {}
    W, H = int(seq.get("width") or 1920), int(seq.get("height") or 1080)
    from . import gallery as G
    tpls = TP.list_all(W, H)
    rows = G.enrich_rows([TP.summary(tpl) for tpl in tpls], tpls)    # + group, colors, labels, is_new
    out = {"groups": G.group_list(rows), "templates": rows, "default": TP.default_id(W, H), "options": TP.options() if p.get("options", True) else None}
    if p.get("system_fonts"):                        # installed Windows families (Regular faces), cached index
        from .layout import system_fonts
        out["system_fonts"] = sorted(system_fonts())
    if p.get("full"):
        out["full"] = {t["id"]: {k: v for k, v in t.items() if not k.startswith("_")} for t in TP.list_all(W, H)}
    return out


def act_fonts(job, emit):
    """Font catalog (bundled OFL + installed Windows fonts) with categories, + optional sample PNGs
    (docs/CAPTIONS_API.md 3.12)."""
    from . import layout as L
    p = job["params"]
    out = L.font_catalog(system=p.get("system", True) is not False, refresh=bool(p.get("refresh")))
    if p.get("preview"):
        fams = p["preview"]
        if fams is True or fams == "all":
            fams = [f["family"] for f in out["fonts"]]
        size = tuple(p.get("size") or (360, 72))
        out["previews"] = L.font_previews(fams if isinstance(fams, list) else [fams], p.get("text"), size)
    return out


def act_gallery(job, emit):
    from ..util import workdir
    from . import preview as P
    p = job["params"]
    if p.get("anim"):                                # animated sprite sheets (gallery.py)
        from . import gallery as G
        return G.act_gallery_anim(job, emit, _timeline)
    emit.plan([("thumbs", tr("cap.stage.thumbs"), 1.0)])
    with emit.step("thumbs"):
        tpls = P.templates_for_gallery(p.get("ids"))
        bg, W, H = None, None, None
        if p.get("t") is not None and job.get("seq"):
            tl = _timeline(job)
            W, H = tl.width, tl.height
            bg = str(P.frame_png(tl, float(p["t"]), workdir(job), 0.5, p.get("fit", "cover"))[0])
        size = tuple(p.get("size") or (360, 180))
        thumbs, errs = P.gallery(tpls, W, H, size, bool(p.get("force")), bg, emit=emit)
    return {"thumbs": thumbs, "errors": errs, "size": list(size)}


def act_save_template(job, emit):
    from ..util import EngineError
    from . import model as M
    from . import preview as P
    from . import templates as TP
    p = job["params"]
    if p.get("delete"):
        ok = TP.delete_user(p["delete"])
        return {"deleted": ok, "id": TP.slug(p["delete"]), "summary": tr("cap.tplDeleted") if ok else tr("cap.tplMissing")}
    base = {}
    if p.get("from_doc"):
        d, _ = M.from_job(job)
        base = TP.load(d.get("template"))
        data = TP.deep_merge({k: v for k, v in base.items() if k not in ("id", "source") and not k.startswith("_")},
                             d.get("style") or {})     # doc.style semantics: an explicit null overrides the template
        data = M.merge_patch(data, p.get("template") or {})
    elif isinstance(p.get("template"), dict):
        data = p["template"]
    else:
        raise EngineError("BAD_TEMPLATE", tr("cap.err.noTplData"), tr("cap.err.noTplDataHint"))
    data = dict(data)
    data.pop("default_for", None)                   # user templates never become the automatic default
    if p.get("description") or p.get("from_doc"):
        data["description"] = p.get("description") or tr("cap.tplDescFrom", name=base.get("name", "template"))
    data.setdefault("description", tr("cap.tplDesc"))
    full = TP.save_user(data, p.get("id"), p.get("name"))
    thumb = None
    try:
        thumb = str(P.thumb(full, *P.preferred_size(full)))
    except Exception:  # noqa: BLE001 - a thumbnail failure must not lose the saved template
        pass
    return {"template": TP.summary(full, thumb), "id": full["id"], "summary": tr("cap.tplSaved", name=full["name"])}


# ---------------------------------------------------------------- preview / burn

def act_preview(job, emit):
    from ..util import EngineError, workdir
    from . import model as M
    from . import preview as P
    p = job["params"]
    d, _ = M.from_job(job)
    tl = _timeline(job, required=False)
    t = p.get("t")
    if t is None:
        t = tl.player if tl is not None else (d["pages"][0]["t0"] if d.get("pages") else 0.0)
    mode = p.get("mode", "frame")
    if mode not in ("frame", "overlay", "both"):
        raise EngineError("BAD_PARAMS", tr("cap.err.previewMode", mode=mode))
    with emit.step("preview", tr("cap.stage.preview")):
        r = P.preview(d, tl, float(t), workdir(job), mode, float(p.get("scale", 0.5)), p.get("fit", "cover"),
                      p.get("frame"))
    return r


def act_burn(job, emit):
    from ..util import workdir
    from . import model as M
    from . import preview as P
    p = job["params"]
    d, _ = M.from_job(job)
    tl = _timeline(job)
    t0 = float(p["t0"]) if p.get("t0") is not None else float(tl.player or 0.0)
    t1 = float(p["t1"]) if p.get("t1") is not None else t0 + float(p.get("dur", 3.0))
    emit.plan([("burn", tr("cap.stage.burn"), 1.0)])
    with emit.step("burn"):
        return P.burn(d, tl, t0, t1, workdir(job), float(p.get("scale", 0.5)), p.get("fit", "cover"),
                      p.get("audio", True) is not False, emit)


# ---------------------------------------------------------------- autoposition

def act_autoposition(job, emit):
    from ..util import EngineError
    from . import model as M
    from . import smart as S
    p = job["params"]
    d, path = M.from_job(job)
    tl = _timeline(job)
    if not d.get("pages"):
        raise EngineError("NO_CAPTIONS", tr("cap.err.nothingToPosition"), tr("cap.model.buildFirst"))
    avoid = tuple(p.get("avoid") or ("faces", "text", "action"))
    emit.plan([("analyze", tr("cap.stage.analyze"), 0.95), ("save", tr("cap.stage.savePos"), 0.05)])
    with emit.step("analyze"):
        if p.get("clear"):
            M.apply_ops(d, [{"op": "clear_auto_pos"}])
            res = {"pages": len(d["pages"]), "moved": 0, "boxed": 0, "items": []}
        else:
            res = S.autoposition(d, tl, p.get("platform"), avoid, bool(p.get("contrast")), bool(p.get("reset_manual")),
                                 emit, p.get("fit", "cover"))
    with emit.step("save"):
        M.repaginate(d)
        M.save(d, path)
    res["doc"] = str(path)
    res["summary"] = tr("cap.posMoved", n=res["moved"], pages=res["pages"]) + (
        tr("cap.posBoxed", n=res["boxed"]) if res.get("boxed") else "")
    return res


# ---------------------------------------------------------------- render

def act_render(job, emit):
    import contextlib
    import os
    from pathlib import Path
    from ..util import EngineError, now_iso, safe_name, setting, workdir
    from . import compile as C
    from . import model as M
    from . import render as R
    p = job["params"]
    d, path = M.from_job(job)
    if not d.get("pages"):
        raise EngineError("NO_CAPTIONS", tr("cap.render.nothing"), tr("cap.model.buildFirst"))
    codec = p.get("codec", "qtrle")
    if codec not in R.CODECS:
        raise EngineError("BAD_PARAMS", tr("cap.render.badCodec", codec=codec), tr("cap.render.badCodecHint"))
    seqfps = float((d.get("seq") or {}).get("fps") or 30)
    fps = max(10.0, min(60.0, float(p.get("fps") or min(30.0, seqfps))))
    prev = d.get("render") or {}
    if (not p.get("force") and prev.get("hash") == d.get("hash") and prev.get("codec") == codec
            and prev.get("fps") == fps and prev.get("path") and os.path.isfile(prev["path"])):
        return {"path": prev["path"], "skipped": True, "plan": prev.get("plan"), "version": prev.get("version"),
                "summary": tr("cap.render.upToDate", v=prev.get("version"))}
    emit.plan([("prep", tr("cap.render.stagePrep"), 0.05), ("render", tr("cap.render.stage"), 0.95)])
    with C.page_cache():                                   # band pass + render chunks compile each page once
        with emit.step("prep"):
            pairs, W, H = M.page_pairs(d)
            end = max(c.end for c, _ in pairs) + 0.5
            if p.get("until"):
                end = min(end, float(p["until"]))
            mode = p.get("band", "auto")
            band = (C.caption_band(pairs, W, H, on_page=lambda i, n: emit.progress(100.0 * i / n))
                    if mode != "off" else (0, 0, W, H))
            use_band = mode == "on" or (mode == "auto" and band[2] * band[3] <= 0.6 * W * H)
            bx, by, bw, bh = band if use_band else (0, 0, W, H)
            out_dir = Path(p.get("out_dir") or setting("outputDir") or (workdir(job) / "captions_render"))
            out_dir.mkdir(parents=True, exist_ok=True)
            stem = safe_name((d.get("seq") or {}).get("name") or "sequence", 48) + "_captions"
            n, out = R.next_version(out_dir, stem, R.CODECS[codec]["ext"], floor=int(prev.get("version") or 0))
            R.check_disk(out_dir, bw, bh, end * 2, codec)          # chunk cache + concatenated file
        with emit.step("render"):
            info = R.render_chunked(pairs, W, H, (bx, by, bw, bh), fps, end, codec, out_dir, out, emit,
                                    chunk=float(p.get("chunk") or R.CHUNK_SEC))
    removed = R.cleanup_versions(out_dir, stem, keep=3, protect=[prev.get("path"), str(out)])
    R.prune_chunks(out_dir, info["keys"] + list(prev.get("chunks") or []))
    for old_ass in out_dir.glob(stem + "_v*.ass"):            # v1 renders wrote a full .ass next to the movie
        with contextlib.suppress(OSError):
            old_ass.unlink()
    size = out.stat().st_size
    plan = {"kind": "overlay", "path": str(out), "track": OVERLAY_TRACK, "start": 0.0, "duration": round(end, 3),
            "frame": [W, H], "band": [bx, by, bw, bh], "full": not use_band,
            "xNorm": round((bx + bw / 2) / W, 5), "yNorm": round((by + bh / 2) / H, 5), "scale": 100,
            "alpha": "straight", "codec": codec, "fps": fps, "version": n, "previous": prev.get("path"),
            # relink in place only when the clip geometry is unchanged (same band size/position and codec);
            # otherwise the host replaces the clip (changeMediaPath to another frame size is UNVERIFIED)
            "relink": bool(prev.get("path")) and list(prev.get("band") or []) == [bx, by, bw, bh]
            and prev.get("codec") == codec}
    d["render"] = {"version": n, "path": str(out), "codec": codec, "fps": fps, "band": [bx, by, bw, bh],
                   "full": not use_band, "duration": round(end, 3), "hash": d.get("hash"), "rendered": now_iso(),
                   "chunks": info["keys"], "plan": plan, "previous": prev.get("path")}
    M.save(d, path)
    return {"path": str(out), "plan": plan, "seconds": info["seconds"], "size_mb": round(size / 1024 ** 2, 1),
            "summary": tr("cap.render.ready", v=n, mb=dec(size / 1024 ** 2, 1), sec=secs(info["seconds"])),
            "chunks": info["chunks"], "rendered_chunks": info["rendered"], "reused_chunks": info["reused"],
            "removed": removed, "version": n, "speed": round(end / max(info["seconds"], 1e-3), 1)}


# ---------------------------------------------------------------- srt / mogrt

def act_srt(job, emit):
    from ..util import safe_name, workdir
    from . import model as M
    from . import render as R
    from . import srt as S
    p = job["params"]
    d, _ = M.from_job(job)
    fmt = "vtt" if p.get("format") == "vtt" else "srt"
    with emit.step("srt", tr("cap.srt.stage", fmt=fmt.upper())):
        if p.get("out"):
            out = p["out"]
        else:
            folder = workdir(job) / "captions_render"
            folder.mkdir(parents=True, exist_ok=True)
            stem = safe_name((d.get("seq") or {}).get("name") or "sequence", 48) + "_subtitle"
            out = R.next_version(folder, stem, "." + fmt)[1]
        path, n = S.write(d, out, fmt, p.get("mode", "page"), bool(p.get("tags")), bool(p.get("upper")))
    return {"path": str(path), "cues": n, "format": fmt, "summary": tr("cap.srt.done", n=n, fmt=fmt.upper()),
            "plan": {"kind": "caption_track", "path": str(path), "format": fmt}}


def act_mogrt(job, emit):
    from ..util import EngineError, setting, workdir
    from . import model as M
    from . import mogrt as MG
    p = job["params"]
    if not (p.get("enable") or setting("captionMogrt")):
        raise EngineError("MOGRT_OFF", tr("cap.mogrt.off"), tr("cap.mogrt.offHint"))
    d, _ = M.from_job(job)
    emit.plan([("mogrt", tr("cap.mogrt.stage"), 1.0)])
    with emit.step("mogrt"):
        r = MG.export(d, workdir(job), bool(p.get("states")), int(p.get("max_clips") or MG.MAX_CLIPS),
                      bool(p.get("single_line")), emit)
    for w in r["warnings"]:
        emit.warn(w)
    return r


# ---------------------------------------------------------------- cleanup (AI)

def act_cleanup(job, emit):
    from .. import review as RV
    from ..ai import tasks
    from ..util import workdir
    from . import model as M
    p = job["params"]
    d, path = M.from_job(job)
    if p.get("mode") == "apply":
        rv = RV.from_job(job)
        edits = [{"ids": it["ids"], "new": it["new"]} for it in rv["items"] if it.get("on") and it.get("ids")]
        with emit.step("apply", tr("cap.stage.applyFixes")):
            M.apply_ops(d, [{"op": "accept", "edits": edits}])
            M.repaginate(d)
            M.save(d, path)
        return {"doc": str(path), "applied": len(edits), "summary": tr("cap.fixesApplied", n=len(edits)),
                "stats": d["stats"]}
    M.resolve_words(d)
    vis = [w for w in d["words"] if not w.get("hide") and not w.get("merged")]
    rows = [{"text": " " + (w.get("text") or "").strip(), "t0": w["t0"], "t1": w["t1"], "seg": w.get("seg", 0)}
            for w in vis]
    gl = _glossary((d.get("params") or {}).get("glossary"))
    emit.plan([("ai", tr("cap.stage.ai"), 0.95), ("review", tr("cap.stage.review"), 0.05)])
    with emit.step("ai"):
        r = tasks.run("cleanup", rows, glossary=gl, emit=emit)
    with emit.step("review"):
        items = []
        for e in (r.get("result") or {}).get("edits") or []:
            a, b = int(e["from"]), int(e["to"])
            if not (0 <= a <= b < len(vis)):
                continue
            ids = [w["id"] for w in vis[a:b + 1]]
            old = " ".join((w.get("text") or "").strip() for w in vis[a:b + 1])
            new = str(e.get("new") or "").strip()
            if not new or new == old:
                continue
            kind = e.get("kind", "word_fix")
            pre = " ".join((w.get("text") or "") for w in vis[max(0, a - 4):a])
            post = " ".join((w.get("text") or "") for w in vis[b + 1:b + 5])
            items.append(RV.item(vis[a]["t0"], vis[b]["t1"], kind, on=True,
                                 conf={"case_punct": 0.95, "word_fix": 0.7, "merge": 0.6}.get(kind, 0.6),
                                 label=f"{old} → {new}", ctx={"pre": pre, "post": post}, ids=ids, old=old, new=new,
                                 note={"type": "ai" if r.get("source") in ("ai", "cache", "mixed") else "info",
                                       "text": tr("cap.fix." + kind) if kind in FIX_KINDS else kind}))
        doc_rv = RV.new("captions_cleanup", items, (d.get("seq") or {}).get("duration") or 0.0,
                        seq={"id": (d.get("seq") or {}).get("id"), "name": (d.get("seq") or {}).get("name")},
                        params={"glossary": gl}, source=r.get("source"))
        rv_path = RV.save(doc_rv, RV.path_for(workdir(job), "captions_cleanup"))
    return {"review": str(rv_path), "summary": RV.summary(doc_rv), "source": r.get("source"),
            "warnings": r.get("warnings", [])[:5], "model": r.get("model"),
            "edits": [{"id": it["id"], "ids": it["ids"], "old": it["old"], "new": it["new"], "kind": it["kind"]}
                      for it in doc_rv["items"]]}


ACTIONS = {"doc": act_doc, "templates": act_templates, "gallery": act_gallery, "preview": act_preview,
           "autoposition": act_autoposition, "render": act_render, "srt": act_srt, "burn": act_burn,
           "mogrt": act_mogrt, "cleanup": act_cleanup, "save_template": act_save_template, "fonts": act_fonts}


# ---------------------------------------------------------------- template library (gallery.py)

def act_template_mix(job, emit):
    """New draft style from groups of different templates (docs/CAPTIONS_API.md 3.13)."""
    from . import gallery as G
    p = job["params"]
    seq = job.get("seq") or {}
    return G.template_mix(p.get("parts"), p.get("base"), int(seq.get("width") or 1920), int(seq.get("height") or 1080))


def act_template_random(job, emit):
    """Curated random readable style (docs/CAPTIONS_API.md 3.14)."""
    from . import gallery as G
    p = job["params"]
    seq = job.get("seq") or {}
    W, H = int(seq.get("width") or p.get("w") or 1080), int(seq.get("height") or p.get("h") or 1920)
    return G.template_random(p.get("seed"), W, H, p.get("base"), p.get("keep") or ())


ACTIONS.update({"template_mix": act_template_mix, "template_random": act_template_random})


# ---------------------------------------------------------------- animation studio + sound effects (anim.py, sfx.py)
# anim          preset library (in / active / out / loop), user animations, tracks, easings (docs/CAPTIONS_API.md 11)
# anim_save     save / delete a custom animation (%APPDATA%\AutoCutBOT\caption_anims.json)
# anim_sprites  frame-strip PNGs of animations on a demo phrase (cached) for the preset grid / Studio preview
# sfx_library   sound list (12 built-in, synthesized; + user sounds), sfx_add / sfx_delete user sounds
# sfx_plan      rule hits over the doc -> [{t, sound, gain}], sfx_render -> mixed WAV + plan {kind: "audio_track"}

def act_anim(job, emit):
    from . import anim as A
    return A.catalog()


def act_anim_save(job, emit):
    from . import anim as A
    p = job["params"]
    if p.get("delete"):
        ok = A.delete_user(str(p["delete"]))
        return {"deleted": ok, "id": p["delete"], "user": A.catalog()["user"],
                "summary": tr("cap.animDeleted") if ok else tr("cap.animMissing")}
    sp = A.save_user(p.get("anim") or {}, p.get("name"), p.get("id"))
    return {"anim": sp, "id": sp["id"], "user": A.catalog()["user"], "summary": tr("cap.animSaved", name=sp["label"])}


def act_anim_sprites(job, emit):
    """params: ids (list | "all"), specs {key: spec}, frames (16), size ([176, 64]), force."""
    from . import anim as A
    p = job["params"]
    items = {}
    ids = p.get("ids")
    if ids == "all" or ids is True:
        ids = [x["id"] for x in A.PRESETS_LIST] + list(A.user_anims())
    for i in ids or []:
        items[str(i)] = str(i)
    for k, v in (p.get("specs") or {}).items():
        items[str(k)] = v
    size = tuple(p.get("size") or (176, 64))
    res, errs = A.sprites(items, int(p.get("frames") or 16), size, bool(p.get("force")))
    return {"sprites": res, "errors": errs}


def act_sfx_library(job, emit):
    from . import sfx as X
    return {"sounds": X.library(), "rules": X.rules_catalog(),
            "defaults": X.DEFAULT_PARAMS, "track": X.TRACK}


def act_sfx_add(job, emit):
    from . import sfx as X
    p = job["params"]
    if p.get("delete"):
        ok = X.delete_user(str(p["delete"]))
        return {"deleted": ok, "sounds": X.library(), "summary": tr("cap.soundDeleted") if ok else tr("cap.soundMissing")}
    row = X.add_user(p.get("path"), p.get("name"))
    return {"sound": row, "sounds": X.library(), "summary": tr("cap.soundAdded", name=row["label"])}


def act_sfx_plan(job, emit):
    from ..util import EngineError
    from . import model as M
    from . import sfx as X
    d, _ = M.from_job(job)
    if not d.get("pages"):
        raise EngineError("NO_CAPTIONS", tr("cap.model.noDoc"), tr("cap.model.buildFirst"))
    with emit.step("sfx_plan", tr("cap.stage.sfxPlan")):
        r = X.plan(d, job["params"], emit)
    n = r["stats"]["hits"]
    r["summary"] = tr("cap.sfxCount", n=n) + (f" (AI: {r['ai']})" if r.get("ai") else "")
    return r


def act_sfx_render(job, emit):
    """params: plan params (rules, volume, ...) or hits (edited list), mode mixed|clips, out_dir."""
    from pathlib import Path
    from ..util import EngineError, ensure_free, safe_name, setting, workdir
    from . import model as M
    from . import sfx as X
    p = job["params"]
    d, _ = M.from_job(job)
    emit.plan([("sfx_plan", tr("cap.stage.sfxPlan"), 0.2), ("sfx_mix", tr("cap.stage.sfxMix"), 0.8)])
    with emit.step("sfx_plan"):
        hits = p.get("hits")
        info = None
        if not isinstance(hits, list):
            if not d.get("pages"):
                raise EngineError("NO_CAPTIONS", tr("cap.model.noDoc"), tr("cap.model.buildFirst"))
            info = X.plan(d, p, emit)
            hits = info["hits"]
        if not hits:
            raise EngineError("NO_SFX", tr("cap.err.noSfx"), tr("cap.err.noSfxHint"))
        sounds = X.sound_map()                       # edited / stale hits may name a sound deleted since planning
        n_in = len(hits)
        hits = [h for h in hits if isinstance(h, dict) and h.get("sound") in sounds and _is_num(h.get("t"))]
        dropped = n_in - len(hits)
        if not hits:
            raise EngineError("NO_SFX", tr("cap.err.sfxGone"), tr("cap.err.sfxGoneHint"))
    out_dir = Path(p.get("out_dir") or setting("outputDir") or (workdir(job) / "captions_render"))
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = safe_name((d.get("seq") or {}).get("name") or "sequence", 48) + "_sfx"
    prev = (d.get("sfx") or {}) if isinstance(d.get("sfx"), dict) else {}
    with emit.step("sfx_mix"):
        span = max(h["t"] for h in hits) - min(h["t"] for h in hits) + 2
        ensure_free(int(span * X.SR * 2) + 1, out_dir)
        if p.get("mode") == "clips":
            n, out = X.versioned(out_dir, stem)
            clips = X.hit_clips(hits, out_dir / f"{stem}_clips_v{n}")
            plan = {"kind": "audio_clips", "track": X.TRACK, "items": clips, "version": n}
            res = {"clips": len(clips)}
        else:
            n, out = X.versioned(out_dir, stem)
            res = X.mix(hits, out)
            if res is None:
                raise EngineError("NO_SFX", tr("cap.err.sfxGone"), tr("cap.err.sfxGoneHint"))
            plan = {"kind": "audio_track", "track": X.TRACK, "path": res["path"], "start": res["start"],
                    "duration": res["duration"], "version": n, "previous": prev.get("path")}
    removed = X.cleanup(out_dir, stem, keep=2, protect=[prev.get("path"), plan.get("path")])
    if plan.get("path"):
        d["sfx"] = {"version": n, "path": plan["path"], "hits": len(hits), "start": plan["start"]}
        M.save(d, M.doc_path(job))
    return {"plan": plan, "hits": hits, "stats": (info or {}).get("stats"), "removed": removed,
            "summary": tr("cap.sfxReady", v=n, n=len(hits)) + (
                tr("cap.sfxDropped", n=dropped) if dropped else ""), "dropped": dropped,
            **{k: v for k, v in res.items() if k not in ("path", "hits")}}


ACTIONS.update({"anim": act_anim, "anim_save": act_anim_save, "anim_sprites": act_anim_sprites,
                "sfx_library": act_sfx_library, "sfx_add": act_sfx_add, "sfx_plan": act_sfx_plan,
                "sfx_render": act_sfx_render})


# ---------------------------------------------------------------- style from image + brand kit (stylist.py, brandkit.py)

def act_style_from_image(job, emit):
    """Draft style from a screenshot of a caption (docs/CAPTIONS_API.md 12.1)."""
    from .stylist import style_from_image
    return style_from_image(job, emit)


def act_brandkit(job, emit):
    """Brand Kit get / set / delete / activate / apply (docs/CAPTIONS_API.md 12.2)."""
    from .brandkit import action
    return action(job, emit)


def act_brand_emphasis(job, emit):
    """Per-word emphasis suggestions from AI + brand words (rule fallback) (docs/CAPTIONS_API.md 12.3)."""
    from .brandkit import emphasis_action
    return emphasis_action(job, emit)


ACTIONS.update({"style_from_image": act_style_from_image, "brandkit": act_brandkit,
                "brand_emphasis": act_brand_emphasis})


# ---------------------------------------------------------------- "Gaya Saya" library + last used style (library.py)
# library     list | save | rename | duplicate | favorite | delete | export | import user templates (CAPTIONS_API 13.1)
# last_style  get | set | clear the last used look per aspect class (vertical / horizontal / square) (13.2)

def act_library(job, emit):
    from .library import action_library
    return action_library(job, emit)


def act_last_style(job, emit):
    from .library import action_last_style
    return action_last_style(job, emit)


ACTIONS.update({"library": act_library, "last_style": act_last_style})
