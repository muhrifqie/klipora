"""Caption layout: libass-exact font metrics, safe zones, pagination and absolute word boxes.

Port of proto/caption_render/{fontmetrics,render_ass}.py (verified against libass: advance +-1-2 px, baseline
<= 0.5 px). libass sizes a face so that OS/2 usWinAscent + usWinDescent == the ASS font size in px and shapes with
HarfBuzz (kerning only with `Kerning: yes`, no kerning/ligatures when \\fsp != 0); FontInfo reproduces that, so our
own word-by-word layout lands exactly where libass would draw it.

Pipeline: RWord list (model.render_words) -> paginate() -> [Caption] -> layout(cap) -> [WBox] + block bbox.
Sizes in templates are px at a 1080-px short side (k = min(W, H) / 1080).
"""
from __future__ import annotations

import copy
import math
import re
from dataclasses import dataclass, field
from functools import lru_cache
from itertools import combinations
from pathlib import Path

from . import glossary as G
from ..i18n import tr


class LabelMap(dict):
    """id -> UI label, translated on every read (tr(prefix + id)) so module-level label tables follow the job
    language. The stored values are the Indonesian fallbacks (what a plain dict copy / json.dumps would see)."""

    def __init__(self, prefix, data):
        super().__init__(data)
        self.prefix = prefix

    def _t(self, k, v):
        key = self.prefix + str(k)
        out = tr(key)
        return v if out == key else out

    def __getitem__(self, k):
        return self._t(k, super().__getitem__(k))

    def get(self, k, default=None):
        return self._t(k, super().__getitem__(k)) if k in self else default

    def items(self):
        return [(k, self._t(k, v)) for k, v in super().items()]

    def values(self):
        return [self._t(k, v) for k, v in super().items()]


HERE = Path(__file__).resolve().parent
FONTS_DIR = HERE / "fonts"
REF = 1080.0
PUNCT = ".,!?;:\u2026\"\u201c\u201d'\u2018\u2019"
DEFAULT_FONT = "Plus Jakarta Sans Bold"

# ---------------------------------------------------------------- fonts


class FontInfo:
    """Metrics of one font file (face `index` of a .ttc), measured the way libass draws it."""

    def __init__(self, path, family=None, bundled=True, index=0):
        import uharfbuzz as hb
        from fontTools.ttLib import TTFont
        self.path = str(path)
        self.bundled = bundled
        self.index = int(index or 0)
        tt = TTFont(path, lazy=True, fontNumber=self.index)
        os2, hhea = tt["OS/2"], tt["hhea"]
        self.family = family or tt["name"].getDebugName(1)
        self.ps_name = tt["name"].getDebugName(6) or self.family.replace(" ", "")
        self.weight = int(getattr(os2, "usWeightClass", 400) or 400)
        self.upem = tt["head"].unitsPerEm
        asc, desc = os2.usWinAscent, os2.usWinDescent
        if asc + desc == 0:
            asc, desc = hhea.ascent, -hhea.descent
        self.asc_u, self.desc_u = asc, desc
        self.cap_u = getattr(os2, "sCapHeight", 0) or int(asc * 0.7)
        self.cmap = tt.getBestCmap() or {}
        blob = hb.Blob.from_file_path(self.path)
        self.hb_font = hb.Font(hb.Face(blob, self.index))
        self.hb_font.scale = (self.upem, self.upem)
        self._hb = hb
        self._cache = {}

    def ppu(self, size):
        """Pixels per font unit at ASS font size `size`."""
        return size / (self.asc_u + self.desc_u)

    def shape(self, text, spaced=False):
        buf = self._hb.Buffer()
        buf.add_str(text)
        buf.guess_segment_properties()
        on = not spaced
        self._hb.shape(self.hb_font, buf, {"kern": on, "liga": on, "clig": on})
        return buf.glyph_positions

    def advance(self, text, size, spacing=0.0, scale_x=1.0):
        """Advance width in px of `text` as libass lays it out (\\fs size, \\fsp spacing, \\fscx)."""
        key = (text, bool(spacing))
        hit = self._cache.get(key)
        if hit is None:
            pos = self.shape(text, spaced=bool(spacing))
            hit = self._cache[key] = (sum(p.x_advance for p in pos), len(pos))
            if len(self._cache) > 20000:
                self._cache.clear()
        units, n = hit
        return (units * self.ppu(size) + spacing * n) * scale_x

    def metrics(self, size, scale_y=1.0):
        """(ascent, descent, cap_height) px; the libass line box = ascent + descent = size."""
        k = self.ppu(size) * scale_y
        return self.asc_u * k, self.desc_u * k, self.cap_u * k

    def has_glyphs(self, text):
        return all(ord(c) in self.cmap or c.isspace() for c in text)


_WEIGHT_RE = re.compile(r"\s+(Thin|Hairline|ExtraLight|UltraLight|Light|Regular|Book|Normal|Medium|SemiBold|Semibold"
                        r"|DemiBold|Demibold|Bold|ExtraBold|UltraBold|Black|Heavy|ExtraBlack)$")
FONT_CATEGORIES = LabelMap("cap.layout.cat.", {  # i18n-ignore (Indonesian fallbacks; UI text: cap.layout.cat.*)
    "tebal": "Tebal", "standar": "Standar", "bulat": "Bulat", "kondensasi": "Kondensasi",  # i18n-ignore
    "serif": "Serif", "tulisan_tangan": "Tulisan tangan", "dekoratif": "Dekoratif"})  # i18n-ignore
# bundled family base -> categories (first = main). Every bundled file is a static OFL font (fonts/LICENSES).
BUNDLED_CATEGORIES = {
    "Anton": ["kondensasi", "tebal"], "Archivo": ["tebal"], "Bangers": ["dekoratif"], "Bebas Neue": ["kondensasi"],  # i18n-ignore (font names)
    "Inter": ["standar", "tebal"], "Lilita One": ["bulat", "dekoratif"], "Montserrat": ["tebal"],
    "Plus Jakarta Sans": ["standar", "tebal"], "Poppins": ["tebal"], "Titan One": ["bulat", "dekoratif"],
    "Rubik": ["tebal", "bulat"], "Nunito": ["bulat", "tebal"], "Fredoka": ["bulat"], "Baloo 2": ["bulat", "tebal"],
    "Oswald": ["kondensasi", "tebal"], "Barlow Condensed": ["kondensasi", "tebal"], "Sora": ["tebal", "standar"],
    "Outfit": ["tebal", "standar"], "Manrope": ["standar", "tebal"], "Kanit": ["tebal"],
    "Righteous": ["dekoratif"], "Paytone One": ["tebal", "bulat"], "Passion One": ["tebal", "dekoratif"],
    "Russo One": ["dekoratif", "tebal"], "Black Ops One": ["dekoratif"], "Bungee": ["dekoratif", "tebal"],
    "Pacifico": ["tulisan_tangan"], "Caveat Brush": ["tulisan_tangan"], "Knewave": ["tulisan_tangan", "dekoratif"],
    "Gochi Hand": ["tulisan_tangan"], "DM Serif Display": ["serif"], "Abril Fatface": ["serif", "dekoratif"],
}


# visual weight where the file says otherwise (Archivo Black ships as usWeightClass 400; it is Archivo's black)
BUNDLED_WEIGHTS = {"Archivo Black": 900}


def family_base(family):
    """'Montserrat ExtraBold' -> 'Montserrat', 'Barlow Condensed Black' -> 'Barlow Condensed' (weight words only;
    'Black Ops One' stays)."""
    return _WEIGHT_RE.sub("", str(family or "")).strip() or str(family or "")


def _stamp(paths):
    out = []
    for p in paths:
        try:
            s = p.stat()
            out.append([p.name, s.st_size, s.st_mtime_ns])
        except OSError:
            continue
    return out


@lru_cache(maxsize=None)
def bundled_index():
    """family -> {"path", "file", "weight"} for every .ttf in fonts/ (unique family names by construction). Names
    are read once and cached in %LOCALAPPDATA%\\AutoCutBOT\\captions\\fonts_bundled.json (keyed by size/mtime), so
    a job process does not open 50 font files just to list them; FontInfo is loaded only when a font is used."""
    from ..util import local_dir, read_json, write_json
    files = sorted(FONTS_DIR.glob("*.ttf"))
    stamp = _stamp(files)
    cache = None
    try:
        cache = local_dir("captions") / "fonts_bundled.json"
        data = read_json(cache)
    except OSError:
        data = None
    if not isinstance(data, dict) or data.get("stamp") != stamp or data.get("v") != 2:
        from fontTools.ttLib import TTFont
        fonts = {}
        for p in files:
            try:
                tt = TTFont(p, lazy=True, fontNumber=0)
                fam = tt["name"].getDebugName(1)
                fonts[fam] = {"file": p.name, "weight": int(getattr(tt["OS/2"], "usWeightClass", 400) or 400),
                              "cap": _cap_ratio(tt)}
            except Exception:  # noqa: BLE001 - a broken file must not break captions
                continue
        data = {"v": 2, "stamp": stamp, "fonts": fonts}
        if cache is not None:
            try:
                write_json(cache, data)
            except OSError:
                pass
    return {fam: dict(v, path=str(FONTS_DIR / v["file"]), weight=BUNDLED_WEIGHTS.get(fam, v["weight"]))
            for fam, v in data["fonts"].items()}


def _cap_ratio(tt):
    """Cap height as a fraction of the libass size cell (winAscent + winDescent): visual size of font.size.
    Switching font at the same visual size: new_size = size * old_cap / new_cap."""
    os2 = tt["OS/2"]
    cell = (os2.usWinAscent + os2.usWinDescent) or (tt["hhea"].ascent - tt["hhea"].descent) or 1
    cap = getattr(os2, "sCapHeight", 0) or int(os2.usWinAscent * 0.7)
    return round(cap / cell, 4)


_FI = {}


def _font_info(path, family, bundled, index=0):
    key = (str(path), int(index or 0), family)
    fi = _FI.get(key)
    if fi is None:
        fi = _FI[key] = FontInfo(path, family=family, bundled=bundled, index=index)
    return fi


def font_index():
    """family name -> FontInfo for every bundled font (loads all of them; prefer get_font / bundled_index)."""
    return {fam: _font_info(v["path"], fam, True) for fam, v in bundled_index().items()}


def font_families():
    """Bundled families as [{family, weight, file, bundled, base, categories}] (UI font picker)."""
    idx = bundled_index()
    rows = []
    for fam, v in idx.items():
        base = family_base(fam)
        rows.append({"family": fam, "weight": v["weight"], "file": v["file"], "bundled": True, "base": base,
                     "categories": list(BUNDLED_CATEGORIES.get(base) or ["tebal"]), "cap": v.get("cap")})
    return sorted(rows, key=lambda r: (r["family"].split(" ")[0], r["weight"]))


_SYS = {}
SYS_V = 3


def _font_dirs():
    import os
    return [Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts",
            Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "Windows" / "Fonts"]


def _face_row(tt, path, index):
    """One installed face -> catalog row (names, weight, style, panose, Latin coverage)."""
    n, os2 = tt["name"], tt["OS/2"]
    fam = n.getDebugName(1)
    sub = (n.getDebugName(2) or "").strip()
    if not fam:
        return None
    ur1, cp1 = int(getattr(os2, "ulUnicodeRange1", 0) or 0), int(getattr(os2, "ulCodePageRange1", 0) or 0)
    if ur1 or cp1:                    # OS/2 says Basic Latin and not a symbol font (no cmap parse: fast scan)
        latin = bool(ur1 & 1) and not (cp1 & (1 << 31))
    else:
        cmap = tt.getBestCmap() or {}
        latin = all(ord(c) in cmap for c in "AZaz09")
    pan = getattr(os2, "panose", None)
    sel = int(getattr(os2, "fsSelection", 0) or 0)
    return {"family": fam, "sub": sub, "weight": int(getattr(os2, "usWeightClass", 400) or 400),
            "italic": bool(sel & 1) or "italic" in sub.lower() or "oblique" in sub.lower(),
            "bold": bool(sel & 0x20) or "bold" in sub.lower(), "path": str(path), "index": index,
            "variable": "fvar" in tt, "latin": latin, "cap": _cap_ratio(tt),
            "panose": [pan.bFamilyType, pan.bSerifStyle, pan.bWeight, pan.bProportion] if pan is not None else None}


def system_faces(refresh=False):
    """Every installed Windows face (C:\\Windows\\Fonts + per-user fonts; .ttf/.otf/.ttc) as rows
    {family (nameID 1, what libass/DirectWrite match), sub, weight, italic, bold, path, index (.ttc face), variable,
    latin, panose}. Cached in %LOCALAPPDATA%\\AutoCutBOT\\captions\\sysfonts_v2.json (keyed by folder mtimes)."""
    from ..util import local_dir, read_json, write_json
    dirs = _font_dirs()
    stamp = [d.stat().st_mtime_ns if d.is_dir() else 0 for d in dirs]
    if _SYS.get("stamp") == stamp and not refresh:
        return _SYS["faces"]
    cache = local_dir("captions") / "sysfonts_v2.json"
    data = None if refresh else read_json(cache)
    if not isinstance(data, dict) or data.get("stamp") != stamp or data.get("v") != SYS_V:
        from fontTools.ttLib import TTCollection, TTFont
        faces = []
        for d in dirs:
            if not d.is_dir():
                continue
            for p in sorted(d.iterdir()):
                ext = p.suffix.lower()
                if ext not in (".ttf", ".otf", ".ttc"):
                    continue
                try:
                    if ext == ".ttc":
                        fonts = list(enumerate(TTCollection(p, lazy=True).fonts))
                    else:
                        fonts = [(0, TTFont(p, lazy=True, fontNumber=0))]
                    for i, tt in fonts:
                        row = _face_row(tt, p, i)
                        if row:
                            faces.append(row)
                except Exception:  # noqa: BLE001 - broken/odd font files are skipped
                    continue
        data = {"v": SYS_V, "stamp": stamp, "faces": faces}
        try:
            write_json(cache, data)
        except OSError:
            pass
    _SYS.update(stamp=stamp, faces=data["faces"], fams=None)
    return data["faces"]


_SKIP_SYS = ("emoji", "mdl2", "fluent icons", "symbol", "marlett", "wingdings", "webdings", "bookshelf")


def _sys_families():
    """{family: {"regular": row, "bold": row|None, "italic": row|None, "bold_italic": row|None}} for usable
    installed families (Latin coverage, static, upright face present); the upright face closest to 400 is the
    metrics face for the plain family."""
    faces = system_faces()
    if _SYS.get("fams") is not None:
        return _SYS["fams"]
    by = {}
    for f in faces:
        if f.get("latin") and not f.get("variable") and not any(x in f["family"].lower() for x in _SKIP_SYS):
            by.setdefault(f["family"], []).append(f)
    out = {}
    bundled = bundled_index()
    for fam, rows in by.items():
        if fam in bundled:
            continue
        up = [r for r in rows if not r["italic"]]
        if not up:
            continue
        reg = min(up, key=lambda r: (abs(r["weight"] - 400), r["bold"]))
        heavy = [r for r in up if r is not reg and (r["bold"] or r["weight"] >= 600)]
        bold = max(heavy, key=lambda r: (r["bold"], -abs(r["weight"] - 700))) if heavy else None
        its = [r for r in rows if r["italic"]]
        ital = min((r for r in its if not r["bold"]), key=lambda r: abs(r["weight"] - reg["weight"]), default=None)
        bital = next((r for r in its if r["bold"]), None)
        out[fam] = {"regular": reg, "bold": bold, "italic": ital, "bold_italic": bital}
    _SYS["fams"] = out
    return out


def system_fonts(refresh=False):
    """Installed Windows families usable by libass: {family: path of the regular face} (compat with v1)."""
    if refresh:
        system_faces(True)
    return {fam: v["regular"]["path"] for fam, v in _sys_families().items()}


def get_font(family, bold=False, italic=False):
    """FontInfo for a bundled family, else an installed system family (its bold / italic face when asked, so
    widths match what libass draws for \\b1 / \\i1), else the default font."""
    idx = bundled_index()
    v = idx.get(family)
    if v is not None:
        return _font_info(v["path"], family, True)
    if family:
        fams = _sys_families()
        s = fams.get(family)
        if s is not None:
            row = ((s["bold_italic"] if bold and italic else None) or (s["bold"] if bold else None)
                   or (s["italic"] if italic else None) or s["regular"])
            try:
                return _font_info(row["path"], family, False, row.get("index", 0))
            except Exception:  # noqa: BLE001 - unreadable face: fall back to the default font
                pass
    d = idx.get(DEFAULT_FONT) or next(iter(idx.values()))
    return _font_info(d["path"], DEFAULT_FONT if DEFAULT_FONT in idx else next(iter(idx)), True)


def heavier(family):
    """Bold override -> heaviest bundled weight of the same family ('Montserrat Bold' -> 'Montserrat Black').
    System families stay as they are (libass picks their bold face for \\b1)."""
    idx = bundled_index()
    if family not in idx:
        return family
    base = family_base(family)
    cands = [(v["weight"], f) for f, v in idx.items() if family_base(f) == base]
    return max(cands)[1] if cands else family


# ---------------------------------------------------------------- font catalog (editor font picker)

_HAND = ("script", "hand", "brush", "marker", "print", "ink free", "gabriola", "mistral", "vivaldi", "freestyle",
         "kristen", "comic", "bradley", "edwardian", "pristina", "rage", "viner", "informal", "tempus", "lucida calli")
_DECO = ("chiller", "jokerman", "magneto", "papyrus", "stencil", "algerian", "broadway", "showcard", "snap",
         "ravie", "curlz", "jokerman", "harrington", "playbill", "old english", "goudy stout", "castellar",
         "bauhaus", "wide latin", "matura", "niagara", "elephant", "forte", "gigi", "juice", "kunstler")
_COND = ("condensed", "narrow", "compressed", "cond ", "agency")


def system_categories(fam, row):
    """Category ids for an installed family from its name + OS/2 panose (heuristic)."""
    name = fam.lower()
    pan = row.get("panose") or [0, 0, 0, 0]
    cats = []
    if pan[0] == 3 or any(k in name for k in _HAND):
        cats.append("tulisan_tangan")
    if pan[0] == 4 or any(k in name for k in _DECO):
        cats.append("dekoratif")
    if any(k in name for k in _COND) or pan[3] in (6, 8):
        cats.append("kondensasi")
    if "round" in name or (pan[0] == 2 and pan[1] == 15):
        cats.append("bulat")
    if pan[0] == 2 and 2 <= pan[1] <= 10 and "sans" not in name:
        cats.append("serif")
    if row["weight"] >= 700 or any(k in name for k in ("black", "heavy", "bold", "ultra")):
        cats.append("tebal")
    if not cats:
        cats.append("standar")
    return list(dict.fromkeys(cats))


def font_catalog(system=True, refresh=False):
    """Every font the captions can use: bundled OFL files + (optionally) installed Windows fonts (libass finds them
    through DirectWrite; layout metrics come from the same file with fontTools/HarfBuzz).
    -> {fonts: [{family, base, weight, source, categories, category, file|path, cap, bold_face, italic_face,
                 preview}], families: [{base, source, category, faces: [{family, weight}]}],
        categories: [{id, label}]}. `family` is the value for font.family / word style font."""
    rows = []
    for r in font_families():
        rows.append({"family": r["family"], "base": r["base"], "weight": r["weight"], "source": "bundled",
                     "categories": r["categories"], "category": r["categories"][0], "file": r["file"],
                     "cap": r.get("cap"), "bold_face": True, "italic_face": False, "preview": True})
    if system:
        if refresh:
            system_faces(True)
        for fam, v in sorted(_sys_families().items()):
            reg = v["regular"]
            cats = system_categories(fam, reg)
            rows.append({"family": fam, "base": family_base(fam), "weight": reg["weight"], "source": "system",
                         "categories": cats, "category": cats[0], "path": reg["path"], "cap": reg.get("cap"),
                         "bold_face": v["bold"] is not None, "italic_face": v["italic"] is not None,
                         "preview": True})
    fams, order = {}, []
    for r in rows:
        key = (r["source"], r["base"])
        if key not in fams:
            fams[key] = {"base": r["base"], "source": r["source"], "category": r["category"], "faces": []}
            order.append(key)
        fams[key]["faces"].append({"family": r["family"], "weight": r["weight"]})
    for g in fams.values():
        g["faces"].sort(key=lambda f: f["weight"])
    return {"fonts": rows, "families": [fams[k] for k in order],
            "categories": [{"id": k, "label": v} for k, v in FONT_CATEGORIES.items()]}


PREVIEW_TEXT = "Halo Teman 123"


def font_preview(family, text=None, size=(360, 72)):
    """Sample PNG (white text, dark outline, transparent background) of one family for the font picker.
    Cached in %LOCALAPPDATA%\\AutoCutBOT\\captions\\fontprev. -> Path"""
    from PIL import Image, ImageDraw, ImageFont
    from ..util import data_hash, local_dir
    text = (text or PREVIEW_TEXT)[:40]
    fi = get_font(family)
    out = local_dir("captions", "fontprev") / f"{data_hash([fi.path, fi.index, text, list(size), 1])[:16]}.png"
    if out.is_file():
        return out
    W, H = int(size[0]), int(size[1])
    px = H * 0.8 * fi.upem / max(1, fi.asc_u + fi.desc_u)           # libass-like: the win cell fits the height
    font = ImageFont.truetype(fi.path, max(8, int(px)), index=fi.index)
    while px > 10 and font.getlength(text) > W - 16:
        px *= 0.9
        font = ImageFont.truetype(fi.path, max(8, int(px)), index=fi.index)
    im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(im).text((W / 2, H / 2), text, font=font, anchor="mm", fill=(255, 255, 255, 255),
                            stroke_width=max(1, int(px * 0.05)), stroke_fill=(0, 0, 0, 255))
    im.save(out)
    return out


def font_previews(families, text=None, size=(360, 72)):
    """{family: png path | None} for many families (8 threads)."""
    from concurrent.futures import ThreadPoolExecutor
    fams = list(dict.fromkeys(f for f in families or [] if isinstance(f, str)))[:400]
    out = {}
    with ThreadPoolExecutor(8) as ex:
        futs = {f: ex.submit(font_preview, f, text, size) for f in fams}
        for f, fu in futs.items():
            try:
                out[f] = str(fu.result())
            except Exception:  # noqa: BLE001 - one bad font must not fail the picker
                out[f] = None
    return out


# ---------------------------------------------------------------- per-ASS font folders (libass start-up cost)

_FN_RE = re.compile(r"\\fn([^\\}]+)")
_SET_LOCK = None


def fontset_dir(ass_text):
    """Folder with only the bundled font files an ASS uses (hard links, else copies), so every ffmpeg/libass start
    loads 1-3 fonts instead of all of fonts/ (~0,5 ms CPU each). Unknown families (installed Windows fonts) are
    found by libass through DirectWrite. Cached per font set under %LOCALAPPDATA%\\AutoCutBOT\\captions\\fontsets."""
    import os
    import shutil
    import threading
    from ..util import data_hash, local_dir
    global _SET_LOCK
    if _SET_LOCK is None:
        _SET_LOCK = threading.Lock()
    idx = bundled_index()
    files = sorted({idx[f]["file"] for f in set(_FN_RE.findall(ass_text or "")) if f in idx})
    srcs = [FONTS_DIR / f for f in files]
    key = data_hash(_stamp(srcs) or ["none"])[:16]
    root = local_dir("captions", "fontsets")
    d = root / key
    with _SET_LOCK:
        d.mkdir(parents=True, exist_ok=True)
        for s in srcs:
            dst = d / s.name
            if dst.is_file():
                continue
            try:
                os.link(s, dst)
            except FileExistsError:
                pass
            except OSError:
                tmp = d / (s.name + ".part")
                shutil.copyfile(s, tmp)
                os.replace(tmp, dst)
        sets = sorted((p for p in root.iterdir() if p.is_dir()), key=lambda p: p.stat().st_mtime)
        for old in sets[:-40]:
            if old != d:
                shutil.rmtree(old, ignore_errors=True)
    return d


# ---------------------------------------------------------------- safe zones
# Fractions of the frame kept clear of platform UI (docs/research/vision.md 5.1). Third-party measurements;
# the caption block is clamped inside. "auto" = tiktok for portrait, square for ~1:1, youtube for landscape.
SAFE_ZONES = {
    "none": {"top": 0.0, "bottom": 0.0, "left": 0.0, "right": 0.0},
    "title": {"top": 0.05, "bottom": 0.05, "left": 0.05, "right": 0.05},
    "youtube": {"top": 0.05, "bottom": 0.10, "left": 0.05, "right": 0.05},
    "tiktok": {"top": 130 / 1920, "bottom": 484 / 1920, "left": 44 / 1080, "right": 140 / 1080},
    "reels": {"top": 0.14, "bottom": 0.35, "left": 0.06, "right": 0.06},
    "shorts": {"top": 180 / 1920, "bottom": 390 / 1920, "left": 60 / 1080, "right": 120 / 1080},
    "square": {"top": 0.05, "bottom": 0.05, "left": 0.05, "right": 0.05},
    "feed45": {"top": 0.05, "bottom": 0.05, "left": 0.05, "right": 0.05},
}
SAFE_ZONES["vertical_universal"] = {k: max(SAFE_ZONES[p][k] for p in ("tiktok", "reels", "shorts"))
                                    for k in ("top", "bottom", "left", "right")}
# default caption centre y (fraction of H) per platform: as low as is safe for 2 lines
DEFAULT_ANCHOR = {"tiktok": 0.68, "reels": 0.58, "shorts": 0.68, "vertical_universal": 0.58, "youtube": 0.86,
                  "square": 0.82, "feed45": 0.80, "title": 0.86, "none": 0.88}
SAFE_LABELS = LabelMap("cap.layout.safe.", {  # UI text: cap.layout.safe.* (values = Indonesian fallbacks)
    "auto": "Otomatis", "youtube": "YouTube (16:9)", "tiktok": "TikTok", "reels": "Instagram Reels",  # i18n-ignore
    "shorts": "YouTube Shorts", "vertical_universal": "Semua vertikal", "square": "Kotak 1:1",  # i18n-ignore
    "feed45": "Feed 4:5", "title": "Title safe", "none": "Tanpa batas"})  # i18n-ignore


def aspect_kind(W, H):
    r = W / max(1, H)
    return "portrait" if r < 0.85 else "square" if r < 1.2 else "landscape"


def resolve_safe(name, W, H):
    if name in (None, "", "auto"):
        kind = aspect_kind(W, H)
        return "tiktok" if kind == "portrait" else "square" if kind == "square" else "youtube"
    return name if name in SAFE_ZONES else "none"


def safe_rect(name, W, H):
    z = SAFE_ZONES[resolve_safe(name, W, H)]
    return (W * z["left"], H * z["top"], W * (1 - z["right"]), H * (1 - z["bottom"]))


# ---------------------------------------------------------------- template helpers

def deep_merge(base, over):
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


# ---------------------------------------------------------------- render words / pages

WORD_STYLE_KEYS = ("color", "active_color", "outline_color", "font", "scale", "bold", "italic", "pill", "underline",
                   "emoji", "uppercase", "glow", "case", "rotate")
CASES = ("as_is", "upper", "lower", "title", "sentence")
BOX_SHAPES = ("rect", "marker", "banner", "ribbon", "sticker")


@dataclass
class RWord:
    """A word as the renderer sees it: display text (already case/punct-transformed), sequence times, resolved
    per-word style overrides and page/line break flags."""
    text: str
    start: float
    end: float
    style: dict = field(default_factory=dict)
    id: str = ""
    seg_end: int = 0
    brk: str | None = None          # "page" | "line" | "join" (user break flags)
    sentence_end: bool = False
    comma: bool = False
    raw: str = ""                   # display text before template transforms (keyword / number matching)
    cap: bool | None = None         # sentence start (font.case "sentence"); None = unknown -> case kept
    keep: bool = False              # keep the word's own case in "sentence" mode (glossary / user-typed text)


@dataclass
class Line:
    words: list


@dataclass
class Caption:
    """One page (the words on screen together). start/end = visibility times; style = page template overrides.
    index = page index in the document (stable across previews and render chunks: colour cycle, tilt, stickers)."""
    lines: list
    start: float = 0.0
    end: float = 0.0
    style: dict = field(default_factory=dict)
    id: str = ""
    index: int = -1

    @property
    def words(self):
        return [w for ln in self.lines for w in ln.words]

    @property
    def text(self):
        return "\n".join(" ".join(w.text for w in ln.words) for ln in self.lines)


class Frame:
    def __init__(self, W, H, tpl):
        self.W, self.H = W, H
        self.k = min(W, H) / REF
        lay = tpl["layout"]
        self.safe = safe_rect(lay.get("safe"), W, H)
        self.max_w = min(W * lay["width"] / 100, self.safe[2] - self.safe[0])
        self.x, self.y = W * lay["x"] / 100, H * lay["y"] / 100
        self.align = lay["align"]


def _core(w):
    return (w.raw or w.text).strip(PUNCT).lower()


def wstyle(w, tpl):
    """Template emphasis rules (numbers / keywords / emoji map) under the word's explicit overrides."""
    em, s = tpl["emphasis"], {}
    core = _core(w)
    kws = tpl.get("_kw_set")
    if kws is None:
        kws = {x.lower() for x in em.get("keywords") or []}
    if (em.get("numbers") and any(ch.isdigit() for ch in w.text)) or (core and core in kws):
        for key in ("color", "scale", "font"):
            if em.get(key) not in (None, 1.0):
                s[key] = em[key]
        if em.get("pill"):
            s["pill"] = em["pill"]
    emj = tpl.get("emoji") or {}
    if emj.get("enabled"):
        mp = tpl.get("_emoji_map")
        if mp is None:
            mp = {kk.lower(): v for kk, v in (emj.get("map") or {}).items()}
        e = mp.get(core)
        if e:
            s["emoji"] = e
    s.update(w.style)
    return s


def word_font(w, tpl, ws=None):
    ws = wstyle(w, tpl) if ws is None else ws
    fam = ws.get("font") or tpl["font"]["family"]
    bold = bool(ws.get("bold"))
    if bold:
        fam = heavier(fam)
    return get_font(fam, bold, bool(ws.get("italic") or tpl["font"].get("italic")))


def word_size(w, tpl, k, ws=None):
    ws = wstyle(w, tpl) if ws is None else ws
    try:
        sc = float(ws.get("scale", 1.0) or 1.0)
    except (TypeError, ValueError):
        sc = 1.0
    return tpl["font"]["size"] * k * sc


def measure(w, tpl, k, fit=1.0):
    ws = wstyle(w, tpl)
    fi = word_font(w, tpl, ws)
    size = word_size(w, tpl, k, ws) * fit
    sp = tpl["font"]["letter_spacing"] * k * fit
    return fi, size, sp, fi.advance(w.text, size, sp)


def emoji_size(tpl, k, fit=1.0):
    return tpl["font"]["size"] * k * fit * tpl["emoji"]["size"] * 1.15


def emoji_after_w(w, tpl, k, fit=1.0):
    """Layout space reserved after a word whose emoji sits inline ("after")."""
    if tpl["emoji"]["position"] != "after" or not wstyle(w, tpl).get("emoji"):
        return 0.0
    return emoji_size(tpl, k, fit) * 1.2


def _num(v, default=0.0):
    try:
        return float(v) if v is not None else default
    except (TypeError, ValueError):
        return default


def outline2_px(tpl):
    """Second (outer) stroke width at 1080 (style.outline2), 0 when off."""
    return max(0.0, _num((tpl.get("style") or {}).get("outline2")))


def box_per_word(tpl):
    box = tpl.get("box") or {}
    return bool(box.get("enabled")) and (box.get("per") == "word" or box.get("shape") == "sticker")


def box_slant(tpl, h):
    """Horizontal run of a banner / ribbon end for a box of height h (px)."""
    shape = (tpl.get("box") or {}).get("shape")
    return 0.32 * h if shape in ("banner", "ribbon") else 0.0


def pill_pad(w, tpl, k, fit=1.0, ws=None):
    """Room kept on each side of a word with its own pill (word style / emphasis pill) so it never covers the
    neighbours."""
    ws = wstyle(w, tpl) if ws is None else ws
    pad = 0.0
    if ws.get("pill"):
        pad = (tpl["highlight"]["pill_pad_x"] * 0.8 + tpl["style"]["outline"] + outline2_px(tpl)) * k * fit
    if ws.get("italic") and not tpl["font"].get("italic"):     # libass shears italic glyphs to the right
        pad = max(pad, 0.06 * tpl["font"]["size"] * k * fit)
    skew = _num(tpl["font"].get("skew"))
    if skew:                                                   # \fax shears around the line centre
        pad = max(pad, abs(skew) * 0.45 * tpl["font"]["size"] * k * fit)
    return pad


def space_w(tpl, k, fit=1.0):
    """Gap between words: the font's space * word_spacing, plus most of the outline so thick outlines don't
    swallow the gap (Inter/Poppins spaces are only ~0.1-0.16 em)."""
    fi = get_font(tpl["font"]["family"])
    size = tpl["font"]["size"] * k * fit
    sp = tpl["font"]["letter_spacing"] * k * fit
    gap = fi.advance(" ", size, sp) * tpl["font"].get("word_spacing", 1.0) + 0.9 * tpl["style"]["outline"] * k * fit
    o2 = outline2_px(tpl)
    if o2:
        gap += 0.9 * o2 * k * fit
    ex = (tpl.get("style") or {}).get("extrude")
    if isinstance(ex, dict) and _num(ex.get("depth")) > 0:     # 3D depth sticks out towards the next word
        gap += 0.5 * max(0.0, math.cos(math.radians(_num(ex.get("angle"), 45.0)))) * _num(ex.get("depth")) * k * fit
    box = tpl.get("box") or {}
    if box_per_word(tpl):                                      # per-word pills / stickers must not overlap
        gap = max(gap, (2 * box.get("pad_x", 0) + 8 + 2 * _num(box.get("border"))) * k * fit)
        bh = size * 0.85 + 2 * box.get("pad_y", 0) * k * fit
        gap += 2 * box_slant(tpl, bh) + bh * abs(math.sin(math.radians(_num(box.get("tilt")))))
    return gap


def prep_template(tpl):
    """Precompute lookup sets used per word (keyword set, emoji map). Returns the same dict."""
    tpl["_kw_set"] = {x.lower() for x in (tpl.get("emphasis") or {}).get("keywords") or []}
    tpl["_emoji_map"] = {kk.lower(): v for kk, v in ((tpl.get("emoji") or {}).get("map") or {}).items()}
    return tpl


class _Widths:
    """Per-word width cache for one pagination run (fit = 1)."""

    def __init__(self, tpl, fr):
        self.tpl, self.fr, self.cache = tpl, fr, {}

    def __call__(self, w):
        key = id(w)
        v = self.cache.get(key)
        if v is None:
            v = self.cache[key] = (measure(w, self.tpl, self.fr.k)[3] + emoji_after_w(w, self.tpl, self.fr.k)
                                   + 2 * pill_pad(w, self.tpl, self.fr.k))
        return v


def best_lines(words, tpl, fr, widths=None, fit=1.0):
    """Line breaks for one page: fewest lines that fit width/char limits, then most balanced (slight pyramid
    preference), comma-aware, never ending a line on an Indonesian function word when avoidable, user line
    breaks forced. Returns (list of word lists, cost) or (None, inf)."""
    lay, k = tpl["layout"], fr.k
    n = len(words)
    if n == 0:
        return [], 0.0
    if widths is None:
        widths_l = [measure(w, tpl, k, fit)[3] + emoji_after_w(w, tpl, k, fit) + 2 * pill_pad(w, tpl, k, fit)
                    for w in words]
    else:
        widths_l = [widths(w) * fit for w in words]
    sw = space_w(tpl, k, fit)
    forced = {i for i in range(1, n) if words[i].brk == "line"}
    max_lines = max(1, int(lay["max_lines"]))
    best = (None, math.inf)
    lo = len(forced) + 1
    free = [i for i in range(1, n) if i not in forced]
    for nl in range(lo, min(max_lines, n) + 1):     # a forced line break past max_lines -> no fit (page break)
        for extra in combinations(free, nl - 1 - len(forced)):
            cuts = sorted(set(extra) | forced)
            idx = [0] + cuts + [n]
            lines = [list(range(idx[i], idx[i + 1])) for i in range(nl)]
            lw = [sum(widths_l[j] for j in ln) + sw * (len(ln) - 1) for ln in lines]
            chars = [sum(len(words[j].text) for j in ln) + len(ln) - 1 for ln in lines]
            if max(lw) > fr.max_w or max(chars) > lay["max_chars"]:
                continue
            cost = max(lw) + 0.15 * (max(lw) - min(lw))
            cost += sum(8 * k for a, b in zip(lw, lw[1:]) if a > b)
            cost -= sum(40 * k for ln in lines[:-1] if words[ln[-1]].comma)
            cost += sum(120 * k for ln in lines[:-1] if not G.break_ok(words[ln[-1]].raw or words[ln[-1]].text))
            if cost < best[1]:
                best = ([[words[j] for j in ln] for ln in lines], cost)
        if best[0]:
            return best
    return best


def paginate(words, tpl, W, H):
    """Group words into pages by width, char, word, pause, duration and sentence limits; honours user break
    flags (brk 'page' = new page before this word, 'join' = never break before it) and never ends a page on a
    function word when the break was only forced by size."""
    lay, tim = dict(tpl["layout"]), tpl["timing"]
    if tim["mode"] == "word":
        lay["max_words"] = 1
        lay["max_lines"] = 1
    tpl_eff = prep_template(deep_merge(tpl, {"layout": lay}))
    fr = Frame(W, H, tpl_eff)
    widths = _Widths(tpl_eff, fr)
    max_words = max(1, int(lay["max_words"]))
    pages, cur = [], []

    def fits(ws):
        return len(ws) <= max_words and best_lines(ws, tpl_eff, fr, widths)[0] is not None

    for w in words:
        if cur:
            prev = cur[-1]
            if w.brk == "join":
                brk, soft = False, False
            elif w.brk == "page":
                brk, soft = True, False
            else:
                hard = (w.start - prev.end > tim["max_gap"] or prev.sentence_end)
                soft = not hard and (w.end - cur[0].start > tim["max_dur"] or not fits(cur + [w]))
                brk = hard or soft
            if brk:
                carry = []
                # don't end a page on "di/ke/yang/untuk..." when we broke only because the page was full
                if soft and len(cur) > 1 and not G.break_ok(prev.raw or prev.text) and prev.brk != "join":
                    carry = [cur.pop()]
                    if not fits(carry + [w]):
                        cur.append(carry.pop())
                pages.append(cur)
                cur = carry
        cur.append(w)
    if cur:
        pages.append(cur)
    # orphan fix: a 1-word page right after a full page in the same breath borrows the previous last word
    for i in range(1, len(pages)):
        a, b = pages[i - 1], pages[i]
        if (len(b) == 1 and len(a) >= 3 and max_words > 1 and not a[-1].sentence_end and b[0].brk != "page"
                and a[-1].brk != "page" and b[0].start - a[-1].end < 0.3 and fits([a[-1]] + b)):
            b.insert(0, a.pop())
    caps = []
    for ws in pages:
        if not ws:
            continue
        lines, _ = best_lines(ws, tpl_eff, fr, widths)
        if lines is None:  # wider than the frame even split: keep it, auto-fit shrinks it in layout()
            ml = max(1, int(lay["max_lines"]))
            per = int(math.ceil(len(ws) / ml))
            lines = [ws[i:i + per] for i in range(0, len(ws), per)]
        caps.append(Caption([Line(ln) for ln in lines], ws[0].start, ws[-1].end, id="p:" + ws[0].id))
    retime(caps, tim)
    return caps


def retime(caps, tim):
    """Page visibility: lead/hold, fill short gaps, min duration, never overlapping the next page."""
    for i, c in enumerate(caps):
        ws = c.words
        c.start, c.end = ws[0].start, max(w.end for w in ws)
    for i, c in enumerate(caps):
        nxt = caps[i + 1].start if i + 1 < len(caps) else math.inf
        c.start = max(0.0, c.start - tim.get("lead", 0.0))
        end = c.end + tim["hold"]
        if nxt - end < tim["fill_gaps"]:
            end = nxt
        c.end = round(min(max(end, c.start + tim["min_dur"]), nxt), 3)
        c.start = round(c.start, 3)
    return caps


@dataclass
class WBox:  # laid-out word
    w: RWord
    fi: object
    size: float
    sp: float
    x: float      # left of the advance box
    width: float
    base: float   # baseline y
    asc: float
    desc: float
    cap: float
    idx: int      # index in page
    ws: dict = field(default_factory=dict)   # resolved word style

    @property
    def cx(self):
        return self.x + self.width / 2

    @property
    def cy(self):  # \an5 anchor y that puts the baseline at self.base
        return self.base - (self.asc - self.desc) / 2


def layout(cap, tpl, W, H):
    """Absolute word boxes for one page + block bbox (x0, y0, x1, y1) + fit. Auto-fit shrinks a page whose line
    overflows the safe width; the block is clamped inside the safe zone."""
    fr = Frame(W, H, tpl)
    k = fr.k
    fit = 1.0
    sw = space_w(tpl, k)
    wws = [[measure(w, tpl, k)[3] + emoji_after_w(w, tpl, k) + 2 * pill_pad(w, tpl, k) for w in ln.words]
           for ln in cap.lines]
    lw = [sum(x) + sw * (len(x) - 1) for x in wws]
    if not lw:
        return [], (0, 0, 0, 0), 1.0
    if tpl["layout"].get("auto_fit", True):
        # fit the drawn block (text + outline / box padding) inside the safe width, with room for the active-word
        # pop (scale up to ~1.19x of highlight.scale - 1), so a clamped page stays centred and never hits the edge
        bx_ = tpl["box"]
        pad0 = (tpl["style"]["outline"] + (bx_["pad_x"] if bx_["enabled"] else 0)
                + outline2_px(tpl) + (_num(bx_.get("border")) if bx_["enabled"] else 0.0)) * k
        hl = tpl["highlight"]
        hs = _num(hl.get("scale"), 1.0)
        hlmode = tpl["timing"]["mode"] in ("karaoke", "word", "reveal")
        grow = 1 + (hs - 1) * 1.19 if hs > 1 and hlmode else 1.0
        hp = 0.0                      # active-word pill: its own padding + the 1.09x pop-in overshoot
        if hl.get("pill") and tpl["timing"]["mode"] != "line":
            hp = (_num(hl.get("pill_pad_x")) + tpl["style"]["outline"] + outline2_px(tpl)) * k
        peak = grow * (1.09 if hp else 1.0)
        need = max(lw[i] + max(((x + 2 * hp) * peak - x) for x in xs) for i, xs in enumerate(wws) if xs)
        avail = max(1.0, min(fr.max_w, fr.safe[2] - fr.safe[0] - 2 * pad0))
        if max(lw) > fr.max_w or need > avail:
            fit = min(fr.max_w / max(lw), avail / need)
    sw *= fit
    lw = [x * fit for x in lw]
    base_size = tpl["font"]["size"] * k * fit
    gap = base_size * tpl["font"]["line_spacing"]
    caps_h = [max(word_font(w, tpl).metrics(word_size(w, tpl, k) * fit)[2] for w in ln.words) for ln in cap.lines]
    nb = len(cap.lines)
    total = caps_h[0] + sum(max(gap, gap - caps_h[0] + caps_h[i]) for i in range(1, nb))
    top = fr.y - total / 2           # block vertical centre at fr.y (cap-height extents)
    boxes, y, idx = [], top, 0
    for li, ln in enumerate(cap.lines):
        y = y + caps_h[0] if li == 0 else y + max(gap, gap - caps_h[0] + caps_h[li])
        if fr.align == "left":
            x = fr.x
        elif fr.align == "right":
            x = fr.x - lw[li]
        else:
            x = fr.x - lw[li] / 2
        for w in ln.words:
            ws = wstyle(w, tpl)
            fi = word_font(w, tpl, ws)
            size = word_size(w, tpl, k, ws) * fit
            sp = tpl["font"]["letter_spacing"] * k * fit
            adv = fi.advance(w.text, size, sp)
            asc, desc, capH = fi.metrics(size)
            pp = pill_pad(w, tpl, k, fit, ws)
            x += pp
            boxes.append(WBox(w, fi, size, sp, x, adv, y, asc, desc, capH, idx, ws))
            x += adv + pp + emoji_after_w(w, tpl, k, fit) + sw
            idx += 1
    box = tpl["box"]
    pad = (tpl["style"]["outline"] + (box["pad_x"] if box["enabled"] else 0)) * k
    pady = (tpl["style"]["outline"] + (box["pad_y"] if box["enabled"] else 0)) * k
    extra = outline2_px(tpl) + (_num(box.get("border")) if box["enabled"] else 0.0)
    if extra:
        pad += extra * k
        pady += extra * k
    if box["enabled"] and box.get("shape") in ("banner", "ribbon"):
        pad += box_slant(tpl, base_size * 0.85 + 2 * box["pad_y"] * k)
    x0 = min(b.x for b in boxes) - pad
    x1 = max(b.x + b.width + emoji_after_w(b.w, tpl, k, fit) for b in boxes) + pad
    y0 = min(b.base - b.cap for b in boxes) - pady
    if tpl["emoji"]["enabled"] or any(b.ws.get("emoji") for b in boxes):
        if tpl["emoji"]["position"] == "above" and any(b.ws.get("emoji") for b in boxes):
            y0 -= emoji_size(tpl, k, fit) * 1.1
    y1 = max(b.base + b.desc * 0.6 for b in boxes) + pady
    sx0, sy0, sx1, sy1 = fr.safe
    dx = (sx0 - x0) if x0 < sx0 else (sx1 - x1) if x1 > sx1 else 0
    dy = (sy0 - y0) if y0 < sy0 else (sy1 - y1) if y1 > sy1 else 0
    for b in boxes:
        b.x += dx
        b.base += dy
    return boxes, (x0 + dx, y0 + dy, x1 + dx, y1 + dy), fit


def text_case(tpl, ws=None):
    """Effective case mode: word style case > word style uppercase > font.case > font.uppercase."""
    ws = ws or {}
    c = ws.get("case")
    if c in CASES:
        return c
    if ws.get("uppercase") is not None:
        return "upper" if ws["uppercase"] else "as_is"
    c = tpl["font"].get("case")
    if c in CASES:
        return c
    return "upper" if tpl["font"].get("uppercase") else "as_is"


def _cap_first(t):
    for i, ch in enumerate(t):
        if ch.isalpha():
            return t[:i] + ch.upper() + t[i + 1:]
        if ch.isdigit():
            return t
    return t


def apply_case(t, mode, start=None, keep=False):
    """upper | lower | title (each word capitalised, the rest kept) | sentence (sentence starts capitalised,
    other words lower-cased unless they are acronyms / camel case / glossary or user-typed text; start=None
    = unknown position -> word kept) | as_is."""
    if mode == "upper":
        return t.upper()
    if mode == "lower":
        return t.lower()
    if mode == "title":
        return _cap_first(t)
    if mode == "sentence" and start is not None:
        if not keep and sum(1 for ch in t if ch.isupper()) <= 1:
            t = t.lower()
        return _cap_first(t) if start else t
    return t


def transform_text(text, tpl, ws=None, start=None, keep=False):
    """Template display transforms: punctuation (keep | strip | soft) and case (font.case / uppercase)."""
    t = text.strip()
    punct = tpl["font"].get("punct", "keep")
    if punct == "strip":
        t = t.strip(PUNCT) or t
    elif punct == "soft":
        t = t.rstrip(".,;:") or t
    return apply_case(t, text_case(tpl, ws), start, keep)


_EMOJI_RE = re.compile("[\U0001F000-\U0001FAFF\u2600-\u27BF\u2B50\u2B55\u203C\u2049\uFE0F\u200D]+")


def split_emoji(text):
    """'keren😎' -> ('keren', '😎'); emoji typed into a word become its emoji style (drawn in colour)."""
    found = _EMOJI_RE.findall(text)
    if not found:
        return text, None
    return _EMOJI_RE.sub("", text).strip(), "".join(found)
