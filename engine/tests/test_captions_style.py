"""ac.captions "Gaya Pro" style engine: option schema + presets, case modes, fonts (bundled 50 OFL faces, installed
Windows fonts, catalog action, per-ASS font folders), every new effect compiled to the expected ASS (layers, tags,
clips, rotations), classic templates keep the classic layers, band tracking of rotated pages, and a straight-alpha
overlay with heavy effects that composites exactly like libass burning the same ASS. Offline (cached transcript of
the 49 s test file). ~15-25 s."""
import json
import math
import re
import subprocess
import time
from pathlib import Path

import _common
from _common import MEDIA_49

import numpy as np

from ac.captions import ACTIONS
from ac.captions import compile as C
from ac.captions import layout as L
from ac.captions import model as M
from ac.captions import render as R
from ac.captions import templates as TP
from ac.progress import ListEmitter
from ac.timeline import Timeline
from ac.util import ffmpeg_exe

T0 = time.perf_counter()
em = ListEmitter()

# ---------------------------------------------------------------- schema, presets, defaults
opts = ACTIONS["templates"]({"params": {}, "seq": None}, em)["options"]
for key in ("schema", "groups", "box_shapes", "effect_presets", "cases", "font_categories", "word_schema"):
    assert opts.get(key), key
groups = {g["id"] for g in opts["groups"]}
objects = {f["path"] for f in opts["schema"] if f["type"] == "object"}
TYPES = {"number", "int", "bool", "color", "colors", "enum", "font", "object"}


def has_path(d, path):
    for part in path.split("."):
        if not isinstance(d, dict) or part not in d:
            return False
        d = d[part]
    return True


for f in opts["schema"]:
    assert f["type"] in TYPES and f["group"] in groups and f["label"], f
    parent = f["path"].rsplit(".", 1)[0]
    if parent in objects:
        assert f.get("parent") == parent and f["path"].rsplit(".", 1)[1] in (next(
            g for g in opts["schema"] if g["path"] == parent)["default_on"]), f["path"]
    else:
        assert has_path(TP.DEFAULTS, f["path"]), f["path"]
        assert f["default"] == TP._get_path(TP.DEFAULTS, f["path"]), f["path"]
    if f["type"] in ("number", "int"):
        assert f["min"] < f["max"], f
    if f["type"] == "enum":
        assert f.get("options") or f.get("options_ref") in opts, f
    for k in (f.get("show_if") or {}):
        assert any(g["path"] == k for g in opts["schema"]), (f["path"], k)
assert {o["id"] for o in next(f for f in opts["schema"] if f["path"] == "font.case")["options"]} == set(L.CASES)
assert {o["id"] for o in next(f for f in opts["schema"] if f["path"] == "box.shape")["options"]} == set(L.BOX_SHAPES)


def keys_ok(patch, base, path=""):
    for k, v in patch.items():
        assert isinstance(base, dict) and k in base, f"unknown key {path}{k}"
        if isinstance(v, dict) and isinstance(base[k], dict):
            keys_ok(v, base[k], path + k + ".")


for lst in ("highlight_presets", "highlight_presets_all", "box_presets", "box_shapes", "effect_presets"):
    for p in opts[lst]:
        keys_ok(p["patch"], TP.DEFAULTS)
        assert p["label"], p
assert {"box_pop", "marker", "color_underline", "glow", "wiggle"} <= {p["id"] for p in opts["highlight_presets_all"]}
assert {"marker", "banner", "ribbon", "outline", "sticker"} <= {p["id"] for p in opts["box_shapes"]}
assert [p["id"] for p in opts["box_presets"]] == ["none", "line", "rounded", "page", "word"]   # old UI list kept

# job language "en": the same schema (paths, ids, groups) with English labels for the Gaya tab; caption content
# (the censor mask) does not follow the UI language
from ac import i18n  # noqa: E402
from ac.captions import glossary as G  # noqa: E402
_src = (Path(__file__).resolve().parents[2] / "tools" / "i18n_check.mjs").read_text(encoding="utf-8")
ID_WORDS = set(re.sub(r"'\s*\+\s*'", " ", re.search(r"ID_WORDS = \('(.*?)'\)\.split", _src, re.S).group(1)).split()) - {"per"}   # "per" is English too
with i18n.using("en"):
    opts_en = ACTIONS["templates"]({"params": {}, "seq": None, "lang": "en"}, em)["options"]
    masked_en = G.mask("anjing", "bleep")
assert [f["path"] for f in opts_en["schema"]] == [f["path"] for f in opts["schema"]]
assert [g["id"] for g in opts_en["groups"]] == [g["id"] for g in opts["groups"]]
labels_en = ([f["label"] for f in opts_en["schema"]] + [g["label"] for g in opts_en["groups"]]
             + [p["label"] for lst in ("highlight_presets_all", "box_shapes", "effect_presets") for p in opts_en[lst]])
id_left = [x for x in labels_en if any(w.lower() in ID_WORDS for w in re.split(r"[^A-Za-z]+", x))]
assert not id_left, ("Indonesian labels in the en options", id_left[:10])
assert [g["label"] for g in opts_en["groups"]] != [g["label"] for g in opts["groups"]]
assert masked_en == G.mask("anjing", "bleep") == "[sensor]"
print(f"job language en: {len(labels_en)} option labels in English, same schema")
assert [p["id"] for p in opts["highlight_presets"]] == list(TP.CLASSIC_HIGHLIGHT)                # old UI list kept
assert set(opts["word_style_keys"]) >= {"case", "rotate"} and set(L.WORD_STYLE_KEYS) >= {"case", "rotate"}
assert not any("\u2014" in json.dumps(opts[k], ensure_ascii=False) for k in opts), "no em dashes in UI copy"

# ---------------------------------------------------------------- case modes
assert L.apply_case("kita", "upper") == "KITA" and L.apply_case("Kita", "lower") == "kita"
assert L.apply_case("teman-teman", "title") == "Teman-teman" and L.apply_case("50.000", "title") == "50.000"
assert L.apply_case("Kita", "sentence", False) == "kita" and L.apply_case("halo", "sentence", True) == "Halo"
assert L.apply_case("WhatsApp", "sentence", False) == "WhatsApp" and L.apply_case("AI", "sentence", False) == "AI"
assert L.apply_case("Jakarta", "sentence", False, keep=True) == "Jakarta"
assert L.apply_case("Kita", "sentence", None) == "Kita", "unknown position: kept"
tpl_c = TP.load("hormozi_kuning")
assert L.text_case(tpl_c) == "upper" and L.text_case(L.deep_merge(tpl_c, {"font": {"case": "title"}})) == "title"
assert L.text_case(tpl_c, {"uppercase": False}) == "as_is" and L.text_case(tpl_c, {"case": "lower"}) == "lower"
mini = {"words": [{"id": f"x:{i}", "t0": i * 0.4, "t1": i * 0.4 + 0.3, "raw": r, "p": 1.0, "seg": 0}
                  for i, r in enumerate(["Halo", "teman", "semua.", "Kita", "buka", "WhatsApp", "dulu"])],
        "params": {"hide_fillers": False, "numbers": False}, "info": {}}
mini["words"][4]["edit"] = {"text": "Buka", "src": "user"}            # user-typed text keeps its case
M.resolve_words(mini)
rws = M.render_words(mini, L.prep_template(L.deep_merge(TP.load("tutorial_bersih"), {"font": {"case": "sentence"}})))
assert [r.text for r in rws] == ["Halo", "teman", "semua.", "Kita", "Buka", "WhatsApp", "dulu"], [r.text for r in rws]
assert [r.cap for r in rws][:4] == [True, False, False, True]
rws = M.render_words(mini, L.prep_template(L.deep_merge(TP.load("tutorial_bersih"), {"font": {"case": "lower"}})))
assert [r.text for r in rws][:2] == ["halo", "teman"]

# ---------------------------------------------------------------- fonts
idx = L.bundled_index()
assert len(idx) >= 48, len(idx)
new = ["Rubik Bold", "Rubik Black", "Nunito Black", "Fredoka Bold", "Baloo 2 ExtraBold", "Barlow Condensed Black",
       "Oswald Bold", "Archivo ExtraBold", "Sora ExtraBold", "Outfit Black", "Manrope ExtraBold", "Kanit Black",
       "Righteous", "Paytone One", "Passion One", "Russo One", "Black Ops One", "Bungee", "Pacifico", "Caveat Brush",
       "Knewave", "Gochi Hand", "DM Serif Display", "Abril Fatface"]
for fam in new:
    assert fam in idx, fam
    fi = L.get_font(fam)
    assert fi.family == fam and fi.bundled and fi.has_glyphs("Rp 1.500.000 Ayo daftar sekarang!"), fam
lic = L.FONTS_DIR / "LICENSES"
assert len(list(lic.glob("*-OFL.txt"))) >= 23 and (lic / "README.txt").is_file()
assert not [p.name for p in L.FONTS_DIR.iterdir() if p.is_file() and p.suffix.lower() != ".ttf"], "only .ttf in fonts/"
assert L.heavier("Rubik Bold") == "Rubik Black" and L.heavier("Archivo ExtraBold") == "Archivo Black"
assert L.heavier("Montserrat Bold") == "Montserrat Black" and L.heavier("Black Ops One") == "Black Ops One"
assert L.family_base("Baloo 2 ExtraBold") == "Baloo 2" and L.family_base("Black Ops One") == "Black Ops One"
fams = L.font_families()
assert all(set(r["categories"]) <= set(L.FONT_CATEGORIES) and r["base"] and 0.2 < r["cap"] < 0.8 for r in fams)
cat = ACTIONS["fonts"]({"params": {"preview": ["Rubik Black", "Pacifico"]}, "seq": None}, em)
assert {r["source"] for r in cat["fonts"]} >= {"bundled"} and len(cat["categories"]) == len(L.FONT_CATEGORIES)
assert all(r["category"] in L.FONT_CATEGORIES for r in cat["fonts"])
assert all(Path(p).is_file() for p in cat["previews"].values()), cat["previews"]
from PIL import Image  # noqa: E402

assert Image.open(cat["previews"]["Rubik Black"]).size == (360, 72)
assert any(g["base"] == "Rubik" and len(g["faces"]) == 2 for g in cat["families"])
sysf = {r["family"]: r for r in cat["fonts"] if r["source"] == "system"}
if "Arial" in sysf:                                   # installed Windows fonts: metrics from the real face
    assert sysf["Arial"]["bold_face"] and L.get_font("Arial", bold=True).path.lower().endswith("arialbd.ttf")
    assert L.get_font("Arial").family == "Arial" and not L.get_font("Arial").bundled
    assert "Segoe UI Emoji" not in sysf and "Wingdings" not in sysf
assert L.get_font("Font Yang Tidak Ada").family == L.DEFAULT_FONT
fs = L.fontset_dir("x {\\fnRubik Black\\fs40}A {\\fnArial\\fs40}B {\\fnPacifico}C")
assert sorted(p.name for p in fs.iterdir()) == ["Pacifico.ttf", "RubikBlack.ttf"], list(fs.iterdir())
sub = L.fontset_dir("{\\fnPacifico}")
assert R.fonts_rel(sub.parent, "x {\\fnPacifico}\n") == sub.name, "fonts_rel(dir, ass) -> the font subset folder"
assert R.fonts_rel(L.FONTS_DIR) == ".", "without an ASS: all bundled fonts (compat)"

if not _common.have(MEDIA_49):
    raise SystemExit(0)

# ---------------------------------------------------------------- compile every effect (portrait doc, real words)
tmp = _common.scratch("captions_style")


def seq(W, H):
    c = {"name": MEDIA_49.name, "path": str(MEDIA_49), "start": 0.0, "end": 48.9, "in": 0.0, "out": 48.9}
    return {"id": f"st{W}", "name": "AC Style Test", "fps": 120, "width": W, "height": H, "duration": 48.9,
            "player": 25.6, "video": [{"index": 0, "name": "V1", "clips": [c]}],
            "audio": [{"index": 0, "name": "A1", "clips": [dict(c)]}]}


tl = Timeline.from_json(seq(1080, 1920))
base_doc = M.build(tl, tl.words_on_timeline(transcribe=False), None, {}, None, template="hormozi_kuning")
assert base_doc["pages"], "cached transcript"


def styled(patch, template="hormozi_kuning", ops=()):
    d = json.loads(json.dumps(base_doc))
    M.apply_ops(d, [{"op": "template", "id": template}, {"op": "doc_style", "style": patch}] + list(ops))
    M.repaginate(d)
    pairs, W, H = M.page_pairs(d)
    text, n = C.ass_text(pairs, W, H)
    return d, pairs, text.splitlines()[text.splitlines().index("[Events]") + 2:]


def layer(ev):
    return int(ev.split(",", 2)[0].split(": ")[1])


# classic templates: classic layers only, no Gaya Pro tags (the 19 pre-Pro ids; the library templates use Pro keys)
from ac.captions.gallery import CLASSIC_IDS  # noqa: E402

for tid in CLASSIC_IDS:
    _, _, evs = styled({}, tid)
    assert evs and max(layer(e) for e in evs) <= 5, tid
    assert not any("\\fax" in e for e in evs), tid

# gradient text: band copies (bord 0, clipped) hidden while the word is active
_, pr, evs = styled({"style": {"gradient": {"colors": ["#FFF35C", "#FF6A00"], "bands": 6}}})
bands = [e for e in evs if "\\bord0\\shad0" in e and "\\clip(" in e and "\\p1" not in e]
assert bands and max(layer(e) for e in evs) <= 5, "gradient alone keeps the classic layers"
assert {"&H00EAFF&", "&H006AFF&"} & {m for e in bands for m in re.findall(r"\\1c(&H[0-9A-F]{6}&)", e)}
_, _, evs_h = styled({"style": {"gradient": {"colors": ["#FF4D8D", "#FFE600", "#22E58B"], "dir": "horizontal",
                                             "span": "line"}}})
assert any("\\clip(" in e and "\\bord0\\shad0" in e for e in evs_h)

# outer stroke + 3D + long shadow -> pro layers in the right order
d, pr, evs = styled({"style": {"outline": 6, "outline2": 8, "outline2_color": "#FF2BD6",
                               "extrude": {"depth": 12, "angle": 60, "color": "#7C2D12", "steps": 6},
                               "long_shadow": {"length": 40, "angle": 45, "color": "#000000CC", "steps": 8}}})
lay = {layer(e) for e in evs}
assert 5 in lay and 6 in lay and 7 in lay, lay
o2 = [e for e in evs if layer(e) == 6]


def bords(lst):
    return [float(x) for e in lst for x in re.findall(r"\\bord([\d.]+)", e)]


assert min(bords(o2)) > max(bords([e for e in evs if layer(e) == 7])), "outer stroke is fatter than the outline"
assert any("\\3c&HD62BFF&" in e for e in o2), "outline2 colour"
ex = [e for e in evs if layer(e) == 5]
alphas = {m for e in ex for m in re.findall(r"\\1a(&H[0-9A-F]{2}&)", e)}
assert len(alphas) >= 4, "long shadow fades towards its end"
text_ev = [e for e in evs if layer(e) == 7]
assert all("\\shad0" in e for e in text_ev) and any("\\xshad" in e for e in o2), "hard shadow moved to outline2"

# tilt + skew + wiggle
d, pr, evs = styled({"layout": {"rotate": -4, "rotate_jitter": 2}, "font": {"skew": 0.2},
                     "highlight": {"wiggle": 8, "scale": 1.1}})
fr = [float(x) for e in evs for x in re.findall(r"\\frz(-?[\d.]+)", e)]
assert any(abs(v + 6) < 0.01 for v in fr) and any(abs(v + 2) < 0.01 for v in fr), "page tilt -4 +- 2"
assert all("\\fax-0.2" in e for e in evs if "\\fn" in e), "skew on every text-like event"
assert any(abs(v) > 6.5 for v in fr if abs(v + 6) > 0.01 and abs(v + 2) > 0.01), "wiggle adds to the tilt"
# rotated page: word centres turned about the page centre, counter-clockwise like libass \frz (probe-verified)
d, pr, _ = styled({"layout": {"rotate": -4}, "anim": {"in": "none", "out": "none"}, "highlight": {"scale": 1.0}})
cap, tpl = next((c, t) for c, t in pr if len(c.words) >= 3)
boxes, bbox, _ = L.layout(cap, tpl, 1080, 1920)
pcx, pcy = (bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2
th = math.radians(-4)
for b in (boxes[0], boxes[-1]):
    dx, dy = b.cx - pcx, b.cy - pcy
    want = (pcx + dx * math.cos(th) + dy * math.sin(th), pcy - dx * math.sin(th) + dy * math.cos(th))
    ev = next(e for e in C.compile_page(0, cap, tpl, 1080, 1920)
              if layer(e) == 4 and e.endswith("}" + C.ass_escape(b.w.text)) and "\\pos(" in e)
    got = [float(v) for v in re.search(r"\\pos\(([-\d.]+),([-\d.]+)\)", ev).groups()]
    assert abs(got[0] - want[0]) < 0.06 and abs(got[1] - want[1]) < 0.06, (b.w.text, got, want)

# box shapes, border, shadow, gradient, sticker
for shape in ("marker", "banner", "ribbon", "sticker", "rect"):
    _, _, evs = styled({"box": {"enabled": True, "shape": shape, "per": "line", "border": 3, "tilt": 3,
                                "shadow": {"x": 0, "y": 6, "blur": 4, "color": "#00000099"},
                                "gradient": {"colors": ["#7C3AED", "#DB2777"], "bands": 4}}})
    boxes_ev = [e for e in evs if "\\p1" in e and layer(e) in (0, 1)]
    assert boxes_ev, shape
    assert any(layer(e) == 0 and "\\blur" in e for e in boxes_ev), (shape, "box shadow under the box")
    assert any("\\clip(" in e for e in boxes_ev if layer(e) == 1), (shape, "gradient bands")
    assert any("\\bord3" in e for e in boxes_ev), (shape, "border")
    if shape == "marker":
        assert any("}m 0 0 m " in e for e in boxes_ev), "marker anchored bbox"
    if shape == "sticker":
        rot = {round(float(x)) for e in boxes_ev for x in re.findall(r"\\frz(-?[\d.]+)", e)}
        assert {3, -3} <= rot, rot
_, _, evs = styled({"box": {"enabled": True, "filled": False, "border": 4, "border_color": "#FFE600"}})
assert any("\\1a&HFF&" in e and "\\bord4" in e and "\\3c&H00E6FF&" in e for e in evs), "outline-only box"
assert C.marker_path(200, 60, 3).startswith("m 0 0 m 200.0 60.0 ")
assert C.marker_path(260, 60, 3)[:120].split(" l ")[0] == C.marker_path(200, 60, 3)[:120].split(" l ")[0].replace(
    "200.0 60.0", "260.0 60.0"), "marker edge stable while the box grows"

# active word: box pop, marker pill, glow (only while active), colours of past / future words
hp = {p["id"]: p["patch"] for p in opts["highlight_presets_all"]}
d, pr, evs = styled(hp["box_pop"])
assert any("\\p1" in e and "\\1c&H5C3BFF&" in e for e in evs), "box pop pill"
d, pr, evs = styled(hp["marker"])
assert any("}m 0 0 m " in e and layer(e) == 1 for e in evs), "marker pill"
d, pr, evs = styled(hp["glow"])
glow_ev = [e for e in evs if layer(e) == 2]                  # hormozi has no style glow: all = active-word glow
assert glow_ev


def secs(s):
    h, m, x = s.split(":")
    return int(h) * 3600 + int(m) * 60 + float(x)


wins = []
for c, _ in pr:
    ws = c.words
    for i, w in enumerate(ws):
        a1 = ws[i + 1].start if i + 1 < len(ws) and ws[i + 1].start - w.end < 0.5 else w.end
        wins.append((w.start - 0.011, max(a1, w.start + 0.05) + 0.011))
mids = [(secs(e.split(",")[1]) + secs(e.split(",")[2])) / 2 for e in glow_ev]
assert all(any(a <= m <= b for a, b in wins) for m in mids), "active-word glow only while a word is active"
d, pr, evs = styled({"highlight": {"color": "#FFFFFF", "past_color": "#22E58B", "future_color": "#FF000080"},
                     "layout": {"max_words": 4}})
cols = {m for e in evs for m in re.findall(r"\\1c(&H[0-9A-F]{6}&)", e)}
assert {"&H8BE522&", "&H0000FF&"} <= cols, cols

# per-word style keys: case + rotate
w0 = base_doc["pages"][3]["lines"][0][0]
d, pr, evs = styled({}, ops=[{"op": "style", "ids": [w0], "style": {"case": "lower", "rotate": -12}}])
shown = [rw.text for c, _ in pr for rw in c.words if rw.id == w0]
assert shown and shown[0] == shown[0].lower(), shown
assert any(abs(float(x) + 12) < 0.01 for e in evs for x in re.findall(r"\\frz(-?[\d.]+)", e))

# band tracking: rotate-in pages turn about the page centre -> the tracked bbox includes the turned corners
S = {"x": 600.0, "y": 500.0, "s": 1.0, "rot": 90.0, "blur": 0.0}
tok = C._CTX.set({"origin": (0.0, 0.0), "bbox": [math.inf, math.inf, -math.inf, -math.inf]})
C._track(S, (10, 10), (500.0, 500.0))
bb = C._CTX.get()["bbox"]
C._CTX.reset(tok)
assert abs((bb[0] + bb[2]) / 2 - 500) < 1 and abs((bb[1] + bb[3]) / 2 - 400) < 1, bb   # (600,500) -> (500,400)

# ---------------------------------------------------------------- straight-alpha overlay with heavy effects
heavy = {"style": {"outline": 6, "outline2": 6, "outline2_color": "#FFFFFF",
                   "gradient": {"colors": ["#FFF35C", "#FF6A00"], "bands": 6},
                   "extrude": {"depth": 10, "angle": 60, "color": "#3B0764", "steps": 5}},
         "layout": {"rotate": -5}, "highlight": {"wiggle": 6},
         "box": {"enabled": True, "shape": "marker", "color": "#22E58B", "opacity": 0.6, "pad_x": 18,
                 "shadow": {"x": 0, "y": 8, "blur": 6, "color": "#00000099"}}}
d, pr, _ = styled(heavy)
page = next(p for p in d["pages"] if p["t0"] > 20 and len(p["lines"][0]) >= 2)
sel = [(c, t) for c, t in pr if c.id == page["id"]]
W, H = 1080, 1920
band = C.caption_band(sel, W, H)
fps = 30.0
t_a = round((page["t0"] + page["t1"]) / 2, 2)
info = R.render_chunked(sel, W, H, band, fps, page["t1"] + 0.2, "qtrle", tmp, tmp / "heavy.mov", chunk=60)
bx, by, bw, bh = band


def grab(args, w, h, cwd=None):
    r = subprocess.run([ffmpeg_exe(), "-v", "error", *args, "-f", "rawvideo", "-pix_fmt", "rgba", "-"],
                       capture_output=True, cwd=cwd)
    return np.frombuffer(r.stdout, np.uint8).reshape(h, w, 4).astype(float)


ovf = grab(["-ss", f"{t_a:.3f}", "-i", str(tmp / "heavy.mov"), "-frames:v", "1"], bw, bh)
assert (ovf[..., 3] > 0).sum() > 2000, "overlay has the caption"
edge = np.concatenate([ovf[0, :, 3], ovf[-1, :, 3], ovf[:, 0, 3], ovf[:, -1, 3]])
assert edge.max() == 0, ("nothing clipped by the band", edge.max())
C.write_ass(tmp / "truth.ass", sel, W, H)
vf = "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920"
frame = grab(["-ss", f"{t_a:.3f}", "-i", str(MEDIA_49), "-frames:v", "1", "-vf", vf + ",format=rgb24"], W, H)
truth = grab(["-ss", f"{t_a:.3f}", "-i", str(MEDIA_49), "-frames:v", "1", "-vf",
              f"{vf},format=rgb24,setpts={t_a:.3f}/TB,ass=truth.ass:fontsdir={R.fonts_rel(tmp, tmp / 'truth.ass')}"],
             W, H, cwd=tmp)
comp = frame[..., :3].copy()
a = ovf[..., 3:4] / 255
comp[by:by + bh, bx:bx + bw] = ovf[..., :3] * a + comp[by:by + bh, bx:bx + bw] * (1 - a)
err = np.abs(comp - truth[..., :3])[by:by + bh, bx:bx + bw]                  # same decode path outside
assert err.max() <= 4.0, ("overlay composite == libass burn", err.max())

# ---------------------------------------------------------------- preview with system + bundled fonts (font subset)
d, _, _ = styled({"font": {"family": "Rubik Black"}, "emphasis": {"font": "Pacifico", "numbers": True}})
M.save(d, tmp / "captions.json")
job = {"tool": "captions", "seq": seq(1080, 1920), "workdir": str(tmp), "params": {"t": 25.6, "mode": "both"}}
pv = ACTIONS["preview"](dict(job, action="preview"), em)
assert Path(pv["png"]).is_file() and Path(pv["overlay"]).is_file()
ass_txt = (tmp / "preview" / "prev.ass").read_text(encoding="utf-8")
assert "\\fnRubik Black" in ass_txt
assert "fontsets" in R.fonts_rel(tmp / "preview", tmp / "preview" / "prev.ass")

assert "error" not in em.kinds()
_common.cleanup("captions_style")
print("render", info["seconds"], "s, band", band, "err", round(float(err.max()), 2), "total",
      round(time.perf_counter() - T0, 1), "s")
print("ok")
