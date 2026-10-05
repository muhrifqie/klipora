"""Foundation tool: FCP7 XML jobs used by the panel's apply layer (AC.apply.removeRanges > 150 ranges).

Actions:
  cut    params {xml: exported sequence .xml, out: target .xml, ranges: [[t0, t1], ...] sequence seconds,
                 name?: new sequence name} -> {path, fps, removedFrames, newDurationFrames, keeps, warnings, ms}
  build  params {out, name, fps, width, height, clips: [{path, start, src_in, src_out, ...}], markers?}
         -> {path, clips} (see ac.xmeml.build_sequence; plan kind "xml_import")
"""
from __future__ import annotations

from pathlib import Path

from .. import xmeml as X
from ..i18n import tr
from ..util import EngineError, ensure_free

TITLE = "XML (FCP7)"               # fallback; `cli.py tools` shows tool.xmeml.title / tool.xmeml.desc
DESCRIPTION = "Potong rentang waktu dari XML sequence, atau bangun sequence baru dari daftar clip."  # i18n-ignore


def _out_path(p, default_dir, default_name):
    out = Path(p) if p else Path(default_dir) / default_name
    out.parent.mkdir(parents=True, exist_ok=True)
    ensure_free(50 * 1024 ** 2, out.parent)
    return out


def cut(job, emit):
    p = job.get("params") or {}
    src = Path(str(p.get("xml") or ""))
    if not src.is_file():
        raise EngineError("NO_XML", tr("xmeml.err.noXml", path=str(src)), tr("xmeml.err.noXmlHint"))
    ranges = p.get("ranges") or []
    if not isinstance(ranges, list) or not all(isinstance(r, (list, tuple)) and len(r) == 2 for r in ranges):
        raise EngineError("BAD_JOB", tr("xmeml.err.badRanges"))
    out = _out_path(p.get("out"), src.parent, src.stem + "_cut.xml")
    with emit.step("cut", tr("xmeml.cutStage", n=len(ranges))):
        try:
            info = X.cut_file(src, out, ranges, p.get("name"))
        except (ValueError, AttributeError, TypeError) as e:   # malformed / unexpected xmeml
            raise EngineError("BAD_XML", tr("xmeml.err.badXml", detail=f"{type(e).__name__}: {e}"),
                              tr("xmeml.err.badXmlHint")) from e
        emit.progress(100)
    for w in info["warnings"][:5]:
        emit.warn(w)
    return {"path": str(out), **info}


def build(job, emit):
    p = job.get("params") or {}
    clips = p.get("clips") or []
    if not clips:
        raise EngineError("BAD_JOB", tr("xmeml.err.noClips"))
    out = _out_path(p.get("out"), job.get("workdir") or ".", "sequence.xml")
    with emit.step("build", tr("stage.buildXml")):
        xml = X.build_sequence(p.get("name") or "Klipora", clips, float(p.get("fps") or 30), int(p.get("width") or 1920),
                               int(p.get("height") or 1080), markers=p.get("markers"))
        X.write(out, xml)
        emit.progress(100)
    return {"path": str(out), "clips": len(clips), "plan": {"kind": "xml_import", "path": str(out)}}


ACTIONS = {"cut": cut, "build": build}
