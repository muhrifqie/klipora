"""Optional "Editable di Premiere" output: one native text MOGRT per caption page (or per active-word state),
with per-word colour / font / size / stroke runs and an optional text box. Port of proto/lab/gfx/mk_mogrt.py
(VERIFIED in Premiere 26.2.2: imports via seq.importMGT, runs render; ~260 ms per clip on import).

A Premiere-authored .mogrt = zip{definition.json, project.prgraphic(zip{<x>.prproj = gzip XML}), thumb.png}.
The text layer's Source Text is base64(uint64 LE byte length + UTF-16LE JSON {"mTextParam": {...,"mStyleSheet":
{runs}}, "mVersion": 1}); run lists are [[utf16 char index, value], ...]. Rules: clientControls MUST be [] (else
Premiere flattens all runs to run 0), colours are 0xRRGGBB ints, background box = mTextParam mBackFill*.
Fonts must be INSTALLED in Windows (our bundled caption fonts are not): template fonts are mapped to an installed
face, else Arial Bold, and the substitution is reported.
"""
from __future__ import annotations

import base64
import contextlib
import glob
import gzip
import io
import json
import os
import re
import shutil
import struct
import uuid
import zipfile
from functools import lru_cache
from pathlib import Path

from ..i18n import secs, tr
from ..util import EngineError, local_dir, read_json, write_json
from . import compile as C
from . import layout as L
from . import model as M

TRACK = "Klipora Captions (Editable)"         # old projects: "AutoCut Captions (Editable)" (the host finds both)
KEYMAP = {"fill": "mFillColor", "font": "mFontName", "size": "mFontSize", "stroke": "mStrokeVisible",
          "strokeColor": "mStrokeColor", "strokeWidth": "mStrokeWidth", "bold": "mFauxBold", "italic": "mFauxItalic",
          "tracking": "mTracking", "baseline": "mBaselineShift"}
NATIVE_CY = 0.871          # text centre (fraction of H) of the bundled template, measured in 1080x1920 (lab)
MAX_CLIPS = 300


def base_mogrt():
    pats = [os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"), "Adobe", "Adobe Premiere Pro*",
                         "Essential Graphics", "Captions and Subtitles", "Bold Web Caption.mogrt")]
    hits = sorted(h for p in pats for h in glob.glob(p))
    return Path(hits[-1]) if hits else None


@lru_cache(maxsize=1)
def _template():
    src = base_mogrt()
    if src is None:
        raise EngineError("NO_MOGRT_BASE", tr("cap.mogrt.noBase"),
                          tr("cap.mogrt.noBaseHint", file="Essential Graphics\\Captions and Subtitles\\Bold Web Caption.mogrt"))
    zin = zipfile.ZipFile(src)
    pg = zipfile.ZipFile(io.BytesIO(zin.read("project.prgraphic")))
    inner = pg.namelist()[0]
    return {"definition": json.loads(zin.read("definition.json").decode("utf-8-sig")), "inner_name": inner,
            "xml": gzip.decompress(pg.read(inner)).decode("utf-8")}


def _patch_blob(b64, text, runs, extra):
    raw = base64.b64decode(b64)
    n = struct.unpack("<Q", raw[:8])[0]
    d = json.loads(raw[8:8 + n].decode("utf-16-le"))
    tp = d["mTextParam"]
    ss = tp["mStyleSheet"]
    ss["mText"] = text
    for k, v in runs.items():
        ss[KEYMAP.get(k, k)] = {"mParamValues": v}
    tp.update(extra)
    body = json.dumps(d, separators=(",", ":")).encode("utf-16-le")
    return base64.b64encode(struct.pack("<Q", len(body)) + body).decode("ascii")


def build(out, text, runs=None, extra=None, name=None, fades=False):
    """Write one native text .mogrt (verified recipe). fades=False strips the template's 0.1 s opacity fades."""
    t = _template()
    xml = t["xml"]
    m = re.search(r'(<Name>Source Text</Name>.*?<StartKeyframeValue Encoding="base64" BinaryHash=")([^"]+)(">)'
                  r'([^<]+)(</StartKeyframeValue>)', xml, re.S)
    if not m:
        raise EngineError("BAD_MOGRT_BASE", tr("cap.mogrt.badBase"))
    new_b64 = _patch_blob(m.group(4), text, runs or {}, extra or {})
    h = uuid.uuid4().hex
    new_hash = f"{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:24]}{len(base64.b64decode(new_b64)):08x}"
    xml = xml[:m.start(2)] + new_hash + m.group(3) + new_b64 + m.group(5) + xml[m.end(5):]
    if not fades:
        xml = re.sub(r'(<Name>Opacity</Name>\s*<ParameterControlType>2</ParameterControlType>\s*<StartKeyframe>[^<]*'
                     r'</StartKeyframe>)\s*<Keyframes>[^<]*</Keyframes>', r'\1', xml, count=1)
    definition = json.loads(json.dumps(t["definition"]))
    definition["capsuleID"] = str(uuid.uuid4())
    definition["capsuleName"] = name or "Klipora Caption"
    definition["clientControls"] = []          # REQUIRED
    pg_buf = io.BytesIO()
    with zipfile.ZipFile(pg_buf, "w", zipfile.ZIP_DEFLATED) as zp:
        zp.writestr(t["inner_name"], gzip.compress(xml.encode("utf-8")))
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zo:
        zo.writestr("definition.json", json.dumps(definition, ensure_ascii=False).encode("utf-8"))
        zo.writestr("project.prgraphic", pg_buf.getvalue())
    return out


# ---------------------------------------------------------------- installed fonts (Premiere needs them)

def _font_dirs():
    return [Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts",
            Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "Windows" / "Fonts"]


def installed_faces():
    """{family lower: [(weight, italic, postscript name)]} of every installed face (cached JSON)."""
    dirs = _font_dirs()
    stamp = [d.stat().st_mtime_ns if d.is_dir() else 0 for d in dirs]
    cache = local_dir("captions") / "installed_faces.json"
    data = read_json(cache)
    if data and data.get("stamp") == stamp:
        return data["faces"]
    from fontTools.ttLib import TTFont
    faces = {}
    for d in dirs:
        if not d.is_dir():
            continue
        for p in d.iterdir():
            if p.suffix.lower() not in (".ttf", ".otf"):
                continue
            try:
                tt = TTFont(p, lazy=True, fontNumber=0)
                n = tt["name"]
                fam = n.getDebugName(16) or n.getDebugName(1)
                ps = n.getDebugName(6)
                os2 = tt["OS/2"]
                w = int(os2.usWeightClass or 400)
                it = bool(os2.fsSelection & 1)
            except Exception:  # noqa: BLE001
                continue
            if fam and ps:
                faces.setdefault(fam.lower(), []).append([w, it, ps])
    with contextlib.suppress(OSError):
        write_json(cache, {"stamp": stamp, "faces": faces})
    return faces


def resolve_font(family, faces=None):
    """Installed PostScript name for a template family ('Montserrat Black' -> 'Montserrat-Black' when Montserrat
    is installed). -> (ps name, substituted: bool)"""
    faces = installed_faces() if faces is None else faces
    fi = L.get_font(family)
    weight = getattr(fi, "weight", 700)
    for cand in (family, family.split(" ")[0], " ".join(family.split(" ")[:-1])):
        lst = [f for f in faces.get(cand.lower().strip(), []) if not f[1]]
        if lst:
            return min(lst, key=lambda f: abs(f[0] - weight))[2], False
    return ("Arial-BoldMT" if weight >= 600 else "ArialMT"), True


def _rgb_int(hexstr):
    r, g, b, _ = C.parse_color(hexstr)
    return (r << 16) | (g << 8) | b


def page_runs(cap, tpl, W, H, active=None, faces=None, single_line=False):
    """(text, runs, extra, fonts used, substitutions) for one page; `active` = index of the highlighted word."""
    k = min(W, H) / L.REF
    st, hl = tpl["style"], tpl["highlight"]
    text, pos = "", 0
    fill, fonts, sizes = [], [], []
    used, subs = {}, set()
    cycle = st.get("color_cycle")
    words = [(li, w) for li, ln in enumerate(cap.lines) for w in ln.words]
    for i, (li, w) in enumerate(words):
        if i:
            sep = "\r" if (not single_line and words[i - 1][0] != li) else " "
            text += sep
            pos += 1
        ws = L.wstyle(w, tpl)
        col = cycle[i % len(cycle)] if cycle else st["fill"]
        col = ws.get("color", col)
        if active is not None and i == active and (ws.get("active_color") or hl.get("color")):
            col = ws.get("active_color") or hl["color"]
        fam = ws.get("font") or tpl["font"]["family"]
        if ws.get("bold"):
            fam = L.heavier(fam)
        ps, sub = resolve_font(fam, faces)
        used[fam] = ps
        if sub:
            subs.add(fam)
        size = tpl["font"]["size"] * k * float(ws.get("scale", 1.0) or 1.0)
        if active is not None and i == active and hl.get("scale") not in (None, 1.0):
            size *= hl["scale"]
        fill.append([pos, _rgb_int(col)])
        fonts.append([pos, ps])
        sizes.append([pos, round(size, 1)])
        text += w.text
        pos += len(w.text.encode("utf-16-le")) // 2
    runs = {"fill": fill, "font": fonts, "size": sizes}
    if st.get("outline", 0) > 0:
        runs.update({"stroke": [[0, True]], "strokeColor": [[0, _rgb_int(st["outline_color"])]],
                     "strokeWidth": [[0, round(st["outline"] * k, 1)]]})
    extra = {}
    if tpl["box"]["enabled"]:
        extra = {"mBackFillVisible": True, "mBackFillColor": _rgb_int(tpl["box"]["color"]),
                 "mBackFillSize": int(round(tpl["box"]["pad_x"] * k))}
    return text, runs, extra, used, subs


def export(doc, wd, states=False, max_clips=MAX_CLIPS, single_line=False, emit=None):
    """Write MOGRTs for every page into <wd>\\mogrt\\v<n>\\ and return the placement plan."""
    pairs, W, H = M.page_pairs(doc)
    jobs = []
    for pi, (cap, tpl) in enumerate(pairs):
        words = cap.words
        mode = tpl["timing"]["mode"]
        if states and mode != "line" and len(words) > 1:
            for i, w in enumerate(words):
                a = cap.start if i == 0 else w.start
                b = words[i + 1].start if i + 1 < len(words) else cap.end
                if b - a >= 0.04:
                    jobs.append((pi, cap, tpl, i, a, b))
        else:
            jobs.append((pi, cap, tpl, None, cap.start, cap.end))
    if len(jobs) > max_clips:
        raise EngineError("TOO_MANY_CLIPS", tr("cap.mogrt.tooMany", n=len(jobs), max=max_clips), tr("cap.mogrt.tooManyHint"))
    root = Path(wd) / "mogrt"
    root.mkdir(parents=True, exist_ok=True)
    have = sorted(int(p.name[1:]) for p in root.glob("v*") if p.name[1:].isdigit())
    ver = (have[-1] + 1) if have else 1
    out_dir = root / f"v{ver}"
    out_dir.mkdir()
    for v in have[:-1]:                              # keep the previous version (Premiere may hold it)
        shutil.rmtree(root / f"v{v}", ignore_errors=True)
    faces = installed_faces()
    items, subs, fonts = [], set(), {}
    for n, (pi, cap, tpl, act, a, b) in enumerate(jobs):
        text, runs, extra, used, sb = page_runs(cap, tpl, W, H, act, faces, single_line)
        fonts.update(used)
        subs |= sb
        p = out_dir / f"page_{pi + 1:04d}" f"{'' if act is None else f'_w{act + 1:02d}'}.mogrt"
        build(p, text, runs, extra, name=f"Klipora {pi + 1}")
        _, bb, _ = L.layout(cap, tpl, W, H)
        cy = (bb[1] + bb[3]) / 2 / H
        # Motion: anchor = the template's native text centre, position = where our layout puts the page centre
        # (gxPopIn(clip, {pivot: anchor}) then set Position = position). Native centre measured at 1080x1920.
        items.append({"path": str(p), "start": round(a, 3), "end": round(b, 3), "page": pi, "word": act,
                      "anchor": [0.5, NATIVE_CY], "position": [round((bb[0] + bb[2]) / 2 / W, 4), round(cy, 4)],
                      "pop": act is None or act == 0})
        if emit is not None:
            emit.progress(100 * (n + 1) / len(jobs))
    warn = []
    if subs:
        warn.append(tr("cap.mogrt.fontsSubst", fonts=", ".join(sorted(subs))))
    return {"plan": {"kind": "mogrt", "track": TRACK, "items": items, "seconds_estimate": round(len(items) * 0.26, 1)},
            "dir": str(out_dir), "version": ver, "fonts": fonts, "warnings": warn,
            "summary": tr("cap.mogrt.ready", n=len(items), sec=secs(max(1, round(len(items) * 0.26))))}
