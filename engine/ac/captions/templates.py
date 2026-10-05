"""Caption templates: defaults, built-in JSON files, user templates, option catalogue for the editor UI.

A template file stores only the keys that differ from DEFAULTS (deep-merged on load). Built-in templates live in
engine/ac/captions/templates/<id>.json; user templates in %APPDATA%\\Klipora\\caption_templates\\<id>.json
(a user template with the same id as a built-in one shadows it). Style resolution for one page:
    template -> doc.style -> range styles -> page style (+ page y)  and per word: emphasis rules -> word style.
"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

from .. import i18n
from ..i18n import tr
from .layout import (DEFAULT_FONT, FONT_CATEGORIES, SAFE_LABELS, WORD_STYLE_KEYS, aspect_kind, deep_merge,
                     font_families)

HERE = Path(__file__).resolve().parent
BUILTIN_DIR = HERE / "templates"
DEFAULT_LANDSCAPE = "tutorial_bersih"
DEFAULT_PORTRAIT = "hormozi_kuning"

DEFAULTS = {
    "name": "Default",
    "description": "",
    "aspect": [],
    "default_for": [],
    "tags": [],
    "font": {"family": DEFAULT_FONT, "size": 80, "uppercase": False, "italic": False, "letter_spacing": 0.0,
             "word_spacing": 1.0, "line_spacing": 1.08, "punct": "keep",           # punct: keep | strip | soft
             "case": None,             # null = follow uppercase | as_is | upper | lower | title | sentence
             "skew": 0.0},             # forward slant (\fax), -0.5..0.5
    "style": {"fill": "#FFFFFF", "outline": 6, "outline_color": "#000000",
              "shadow": 0, "shadow_x": None, "shadow_y": None, "shadow_color": "#00000099", "shadow_blur": 0,
              "glow": None,            # {"color": "#FF2BD6CC", "width": 10, "blur": 8, "active_only": false, "strength": 1}
              "color_cycle": None,     # ["#FFFFFF", "#FFE600"] colours words in turn
              "outline_opacity": None,     # 0..1 (null = the outline colour's own alpha)
              "outline2": 0, "outline2_color": "#FFFFFF",   # second outer stroke (double outline), px@1080
              "gradient": None,        # {"colors": [c1, c2(, c3)], "dir": "vertical"|"horizontal", "span": "word"|"line", "bands": 6}
              "extrude": None,         # 3D: {"depth": 10, "angle": 60, "color": "#000000", "color_end": null, "steps": 0}
              "long_shadow": None},    # {"length": 40, "angle": 45, "color": "#000000B3", "fade": true, "steps": 0}
    "layout": {"max_words": 6, "max_chars": 24, "max_lines": 2, "x": 50, "y": 72, "width": 84,
               "align": "center", "safe": "auto", "auto_fit": True,
               "rotate": 0, "rotate_jitter": 0},     # page tilt (deg) and alternating +-jitter per page
    "timing": {"mode": "karaoke",      # line | reveal | karaoke | word
               "max_gap": 0.7, "max_dur": 3.2, "min_dur": 0.45, "hold": 0.15, "fill_gaps": 0.4, "lead": 0.0},
    "box": {"enabled": False, "per": "line", "color": "#000000B3", "opacity": None, "radius": 18, "pad_x": 24,
            "pad_y": 12,               # per: line | page | word
            "shape": "rect",           # rect | marker | banner | ribbon | sticker (= per word, tilted)
            "filled": True, "border": 0, "border_color": "#FFFFFF",   # filled false + border = outline-only box
            "gradient": None,          # {"colors": [c1, c2(, c3)], "bands": 8} (vertical)
            "shadow": None,            # {"x": 0, "y": 6, "blur": 8, "color": "#00000099"}
            "tilt": 0},                # deg (sticker: alternating +-tilt per word)
    "highlight": {"color": "#FFE600", "scale": 1.0, "pill": None, "pill_radius": 16, "pill_pad_x": 16,
                  "pill_pad_y": 10, "pill_slide": True, "underline": None, "past_color": None,
                  "future_alpha": 1.0, "outline_color": None, "pop_dur": 0.09, "sweep": False,
                  "future_color": None,    # colour of words not spoken yet (karaoke)
                  "pill_shape": "rect",    # rect | marker | banner | ribbon
                  "glow": None,            # active-word glow {"color", "width", "blur", "strength"}
                  "wiggle": 0},            # active word tilts +-deg (alternating)
    "anim": {"in": "pop", "in_dur": 0.2, "out": "fade", "out_dur": 0.12, "word_in": "none", "word_in_dur": 0.14},
    "emphasis": {"numbers": False, "keywords": [], "color": None, "scale": 1.0, "font": None, "pill": None},
    "emoji": {"enabled": False, "map": {}, "position": "above", "size": 1.05},
}

# One-click patches for the editor (applied to doc.style with a deep merge).
HIGHLIGHT_PRESETS = {
    "none": {"label": "cap.tpl.hl.none", "patch": {"timing": {"mode": "line"}, "highlight": {"color": None, "scale": 1.0,
             "pill": None, "underline": None, "sweep": False, "future_alpha": 1.0}}},
    "color": {"label": "cap.tpl.hl.color", "patch": {"timing": {"mode": "karaoke"}, "highlight": {"color": "#FFE600",
              "scale": 1.0, "pill": None, "underline": None, "sweep": False}}},
    "scale": {"label": "cap.tpl.hl.scale", "patch": {"timing": {"mode": "karaoke"}, "highlight": {
              "color": "#FFE600", "scale": 1.12, "pill": None, "underline": None, "sweep": False}}},
    "pill": {"label": "cap.tpl.hl.pill", "patch": {"timing": {"mode": "karaoke"}, "highlight": {"color": "#FFFFFF",
             "pill": "#7C3AED", "pill_slide": True, "underline": None, "sweep": False}}},
    "sweep": {"label": "cap.tpl.hl.sweep", "patch": {"timing": {"mode": "karaoke"}, "highlight": {"color": "#FFD400",
              "sweep": True, "pill": None, "underline": None, "scale": 1.0}}},
    "underline": {"label": "cap.tpl.hl.underline", "patch": {"timing": {"mode": "karaoke"}, "highlight": {"color": None,
                  "underline": "#FFD400", "pill": None, "sweep": False}}},
    "dim": {"label": "cap.tpl.hl.dim", "patch": {"timing": {"mode": "karaoke"}, "highlight": {"color": None,
            "future_alpha": 0.4, "pill": None, "underline": None, "sweep": False}}},
    "reveal": {"label": "cap.tpl.hl.reveal", "patch": {"timing": {"mode": "reveal"}, "anim": {"word_in": "pop"}}},
    "one_word": {"label": "cap.tpl.hl.oneWord", "patch": {"timing": {"mode": "word"}}},
    # Gaya Pro
    "box_pop": {"label": "cap.tpl.hl.boxPop", "patch": {"timing": {"mode": "karaoke"}, "highlight": {
                "color": "#FFFFFF", "pill": "#FF3B5C", "pill_slide": False, "pill_shape": "rect", "scale": 1.08,
                "underline": None, "sweep": False}}},
    "marker": {"label": "cap.tpl.hl.marker", "patch": {"timing": {"mode": "karaoke"}, "highlight": {
               "color": None, "pill": "#FFD60AE6", "pill_slide": True, "pill_shape": "marker", "underline": None,
               "sweep": False}}},
    "color_underline": {"label": "cap.tpl.hl.colorUnderline", "patch": {"timing": {"mode": "karaoke"}, "highlight": {
                        "color": "#FFE600", "underline": "#FFE600", "pill": None, "sweep": False}}},
    "glow": {"label": "cap.tpl.hl.glow", "patch": {"timing": {"mode": "karaoke"}, "highlight": {
             "color": "#FFFFFF", "scale": 1.06, "pill": None, "underline": None, "sweep": False,
             "glow": {"color": "#FFE600CC", "width": 10, "blur": 12, "strength": 1.4}}}},
    "wiggle": {"label": "cap.tpl.hl.wiggle", "patch": {"timing": {"mode": "karaoke"}, "highlight": {
               "color": "#FFE600", "scale": 1.1, "pill": None, "underline": None, "sweep": False, "wiggle": 7}}},
}
CLASSIC_HIGHLIGHT = ("none", "color", "scale", "pill", "sweep", "underline", "dim", "reveal", "one_word")
# every highlight preset switches the Gaya Pro highlight extras off unless it sets them itself
_HL_RESET = {"glow": None, "wiggle": 0, "pill_shape": "rect", "future_color": None}
for _p in HIGHLIGHT_PRESETS.values():
    _h = _p["patch"].get("highlight")
    if _h is not None:
        for _k, _v in _HL_RESET.items():
            _h.setdefault(_k, _v)

_SHAPE_RESET = {"shape": "rect", "filled": True, "border": 0, "tilt": 0}
BOX_PRESETS = {
    "none": {"label": "cap.tpl.box.none", "patch": {"box": {"enabled": False}}},
    "line": {"label": "cap.tpl.box.line", "patch": {"box": {"enabled": True, "per": "line", "radius": 4}}},
    "rounded": {"label": "cap.tpl.box.rounded", "patch": {"box": {"enabled": True, "per": "line", "radius": 18}}},
    "page": {"label": "cap.tpl.box.page", "patch": {"box": {"enabled": True, "per": "page", "radius": 22}}},
    "word": {"label": "cap.tpl.box.word", "patch": {"box": {"enabled": True, "per": "word", "radius": 40, "pad_x": 14,
                                                         "pad_y": 8}}},
}
for _p in BOX_PRESETS.values():
    if _p["patch"]["box"].get("enabled"):
        for _k, _v in _SHAPE_RESET.items():
            _p["patch"]["box"].setdefault(_k, _v)
# All background shapes (Gaya Pro): the 5 classic presets + new shapes. Colour, opacity, gradient and shadow are
# separate controls; a shape preset only sets the shape and its geometry.
BOX_SHAPES = dict(BOX_PRESETS)
BOX_SHAPES.update({
    "marker": {"label": "cap.tpl.box.marker", "patch": {"box": {"enabled": True, "per": "line", "shape": "marker", "filled": True,
                                                     "border": 0, "radius": 0, "pad_x": 14, "pad_y": 6, "tilt": -1.5}}},
    "banner": {"label": "cap.tpl.box.banner", "patch": {"box": {"enabled": True, "per": "line", "shape": "banner", "filled": True,
                                                     "border": 0, "radius": 0, "pad_x": 16, "pad_y": 8, "tilt": 0}}},
    "ribbon": {"label": "cap.tpl.box.ribbon", "patch": {"box": {"enabled": True, "per": "line", "shape": "ribbon", "filled": True,
                                                  "border": 0, "radius": 0, "pad_x": 22, "pad_y": 8, "tilt": 0}}},
    "outline": {"label": "cap.tpl.box.outline", "patch": {"box": {"enabled": True, "per": "line", "shape": "rect", "filled": False,
                                                      "border": 4, "radius": 14, "tilt": 0}}},
    "sticker": {"label": "cap.tpl.box.sticker", "patch": {"box": {"enabled": True, "per": "word", "shape": "sticker", "filled": True,
                                                     "border": 0, "radius": 10, "pad_x": 14, "pad_y": 8, "tilt": 4}}},
})
# One-click looks that combine several Gaya Pro options (deep-merged into doc.style like the presets above).
EFFECT_PRESETS = {
    "polos": {"label": "cap.tpl.fx.polos", "patch": {
        "font": {"skew": 0}, "layout": {"rotate": 0, "rotate_jitter": 0},
        "style": {"gradient": None, "extrude": None, "long_shadow": None, "outline2": 0, "outline_opacity": None}}},
    "tebal_3d": {"label": "cap.tpl.fx.tebal3d", "patch": {"style": {  # i18n-ignore
        "outline": 6, "outline_color": "#000000", "extrude": {"depth": 10, "angle": 60, "color": "#000000",
                                                             "color_end": None, "steps": 0}}}},
    "bayangan_panjang": {"label": "cap.tpl.fx.bayanganPanjang", "patch": {"style": {  # i18n-ignore
        "long_shadow": {"length": 42, "angle": 45, "color": "#000000B3", "fade": True, "steps": 0}}}},
    "garis_ganda": {"label": "cap.tpl.fx.garisGanda", "patch": {"style": {  # i18n-ignore
        "outline": 6, "outline_color": "#000000", "outline2": 7, "outline2_color": "#FFFFFF"}}},
    "gradasi_api": {"label": "cap.tpl.fx.gradasiApi", "patch": {"style": {
        "gradient": {"colors": ["#FFF35C", "#FF8A00"], "dir": "vertical", "span": "word", "bands": 6}}}},
    "gradasi_pelangi": {"label": "cap.tpl.fx.gradasiPelangi", "patch": {"style": {
        "gradient": {"colors": ["#FF4D8D", "#FFE600", "#22E58B"], "dir": "horizontal", "span": "line",
                     "bands": 10}}}},
    "neon": {"label": "cap.tpl.fx.neon", "patch": {"style": {
        "outline": 3, "outline_color": "#00B7FF", "glow": {"color": "#00E5FFCC", "width": 12, "blur": 14,
                                                           "strength": 1.6, "active_only": False}}}},
    "stabilo": {"label": "cap.tpl.fx.stabilo", "patch": {
        "style": {"fill": "#111111", "outline": 0, "shadow": 0}, "highlight": {"color": "#C2410C"},
        "box": {"enabled": True, "per": "line", "shape": "marker", "color": "#FFD60A", "opacity": 0.92,
                "filled": True, "border": 0, "radius": 0, "pad_x": 14, "pad_y": 6, "tilt": -1.5}}},
    "spanduk_merah": {"label": "cap.tpl.fx.spandukMerah", "patch": {
        "style": {"fill": "#FFFFFF", "outline": 0}, "highlight": {"color": "#FFE600"},
        "box": {"enabled": True, "per": "line", "shape": "banner", "color": "#E11D48", "opacity": 1.0,
                "filled": True, "border": 0, "pad_x": 16, "pad_y": 8, "tilt": 0,
                "shadow": {"x": 0, "y": 6, "blur": 4, "color": "#00000080"}}}},
    "stiker": {"label": "cap.tpl.fx.stiker", "patch": {
        "style": {"fill": "#111111", "outline": 0, "shadow": 0}, "highlight": {"color": "#E11D48"},
        "box": {"enabled": True, "per": "word", "shape": "sticker", "color": "#FFFFFF", "opacity": 1.0,
                "filled": True, "border": 0, "radius": 10, "pad_x": 14, "pad_y": 8, "tilt": 4,
                "shadow": {"x": 3, "y": 5, "blur": 2, "color": "#00000099"}}}},
    "miring": {"label": "cap.tpl.fx.miring", "patch": {"layout": {"rotate": -3, "rotate_jitter": 2}}},
}
MODES = {k: "cap.tpl.mode." + k for k in ("line", "karaoke", "reveal", "word")}

# ---------------------------------------------------------------- style schema (generic editor controls)
# Every template key the editor may expose, with type, range, default (from DEFAULTS), Indonesian label and group,
# so the UI builds controls generically (docs/CAPTIONS_API.md 6.5). Types: number | int | bool | color | colors |
# enum | font | object (nullable effect object: null = off, `default_on` = value when switched on). `show_if`:
# {path: value | [values] | "$on" (truthy / not null)}; `options_ref` = a list in `options` (anim_in, ...).
CASE_LABELS = {k: "cap.tpl.case." + _c for k, _c in (("as_is", "asIs"), ("upper", "upper"), ("lower", "lower"),
                                                        ("title", "title"), ("sentence", "sentence"))}
BOX_SHAPE_LABELS = {k: "cap.tpl.shape." + k for k in ("rect", "marker", "banner", "ribbon", "sticker")}
STYLE_GROUPS = [(k, "cap.tpl.group." + k) for k in ("huruf", "isi", "garis", "bayangan", "cahaya", "latar", "sorot",
                                                     "posisi", "animasi")]
_ON = "$on"


def _opts(d):
    return [{"id": k, "label": v} for k, v in d.items()]


def _camel(s):
    parts = str(s).replace(".", "_").split("_")
    return parts[0] + "".join(x[:1].upper() + x[1:] for x in parts[1:])


def _f(path, typ, group, **kw):
    """One schema field. Labels are locale keys (cap.tpl.f.<camelPath>, help cap.tpl.h.*, null cap.tpl.null.*,
    inline enum options cap.tpl.o.<camelPath>.<camelId>), translated per job language by style_schema()."""
    c = _camel(path)
    f = dict({"path": path, "type": typ, "label": "cap.tpl.f." + c, "group": group}, **kw)
    if f.get("help") is True:
        f["help"] = "cap.tpl.h." + c
    if f.get("null_label") is True:
        f["null_label"] = "cap.tpl.null." + c
    for o in f.get("options") or []:
        o.setdefault("label", f"cap.tpl.o.{c}.{_camel(o['id'])}")
    return f


STYLE_SCHEMA = [
    # huruf
    _f("font.family", "font", "huruf", help=True),
    _f("font.size", "number", "huruf", min=24, max=320, step=2, unit="px", decimals=0,
       help=True),
    _f("font.case", "enum", "huruf", options=_opts(CASE_LABELS), nullable=True,
       null_label=True),
    _f("font.italic", "bool", "huruf"),
    _f("font.skew", "number", "huruf", min=-0.4, max=0.4, step=0.02, decimals=2,
       help=True),
    _f("font.letter_spacing", "number", "huruf", min=-4, max=30, step=0.5, unit="px", decimals=1),
    _f("font.word_spacing", "number", "huruf", min=0.5, max=3, step=0.05, unit="x", decimals=2),
    _f("font.line_spacing", "number", "huruf", min=0.7, max=2, step=0.02, unit="x", decimals=2),
    _f("font.punct", "enum", "huruf", options=[{"id": "keep"},
                                                            {"id": "soft"},
                                                            {"id": "strip"}]),
    # isi
    _f("style.fill", "color", "isi", alpha=True),
    _f("style.gradient", "object", "isi",
       default_on={"colors": ["#FFF35C", "#FF8A00"], "dir": "vertical", "span": "word", "bands": 6}),
    _f("style.gradient.colors", "colors", "isi", min_items=2, max_items=3, alpha=True,
       show_if={"style.gradient": _ON}),
    _f("style.gradient.dir", "enum", "isi", show_if={"style.gradient": _ON},
       options=[{"id": "vertical"}, {"id": "horizontal"}]),
    _f("style.gradient.span", "enum", "isi", show_if={"style.gradient.dir": "horizontal"},
       options=[{"id": "word"}, {"id": "line"}]),
    _f("style.gradient.bands", "int", "isi", min=2, max=16, step=1,
       show_if={"style.gradient": _ON}, help=True),
    _f("style.color_cycle", "colors", "isi", min_items=2, max_items=5, nullable=True),
    _f("highlight.past_color", "color", "isi", nullable=True),
    _f("highlight.future_color", "color", "isi", nullable=True),
    _f("highlight.future_alpha", "number", "isi", min=0.1, max=1, step=0.05,
       decimals=2, format="percent"),
    # garis
    _f("style.outline", "number", "garis", min=0, max=24, step=1, unit="px", decimals=0),
    _f("style.outline_color", "color", "garis", alpha=True),
    _f("style.outline_opacity", "number", "garis", min=0, max=1, step=0.05, decimals=2,
       format="percent", nullable=True),
    _f("style.outline2", "number", "garis", min=0, max=24, step=1, unit="px", decimals=0),
    _f("style.outline2_color", "color", "garis", alpha=True, show_if={"style.outline2": _ON}),
    # bayangan
    _f("style.shadow", "number", "bayangan", min=0, max=20, step=1, unit="px", decimals=0),
    _f("style.shadow_x", "number", "bayangan", min=-20, max=20, step=1, unit="px", decimals=0,
       nullable=True, show_if={"style.shadow": _ON}),
    _f("style.shadow_y", "number", "bayangan", min=-20, max=20, step=1, unit="px", decimals=0,
       nullable=True, show_if={"style.shadow": _ON}),
    _f("style.shadow_blur", "number", "bayangan", min=0, max=20, step=1, unit="px", decimals=0,
       show_if={"style.shadow": _ON}),
    _f("style.shadow_color", "color", "bayangan", alpha=True, show_if={"style.shadow": _ON}),
    _f("style.extrude", "object", "bayangan",
       default_on={"depth": 10, "angle": 60, "color": "#000000", "color_end": None, "steps": 0},
       help=True),
    _f("style.extrude.depth", "number", "bayangan", min=2, max=40, step=1, unit="px", decimals=0,
       show_if={"style.extrude": _ON}),
    _f("style.extrude.angle", "number", "bayangan", min=-180, max=180, step=5, unit="°", decimals=0,
       show_if={"style.extrude": _ON}, help=True),
    _f("style.extrude.color", "color", "bayangan", alpha=True, show_if={"style.extrude": _ON}),
    _f("style.extrude.color_end", "color", "bayangan", alpha=True, nullable=True,
       show_if={"style.extrude": _ON}),
    _f("style.extrude.steps", "number", "bayangan", min=0, max=32, step=1, decimals=0,
       show_if={"style.extrude": _ON}, help=True),
    _f("style.long_shadow", "object", "bayangan",
       default_on={"length": 42, "angle": 45, "color": "#000000B3", "fade": True, "steps": 0}),
    _f("style.long_shadow.length", "number", "bayangan", min=8, max=120, step=2, unit="px",
       decimals=0, show_if={"style.long_shadow": _ON}),
    _f("style.long_shadow.angle", "number", "bayangan", min=-180, max=180, step=5, unit="°",
       decimals=0, show_if={"style.long_shadow": _ON}),
    _f("style.long_shadow.color", "color", "bayangan", alpha=True,
       show_if={"style.long_shadow": _ON}),
    _f("style.long_shadow.fade", "bool", "bayangan", show_if={"style.long_shadow": _ON}),
    _f("style.long_shadow.steps", "number", "bayangan", min=0, max=24, step=1, decimals=0,
       show_if={"style.long_shadow": _ON}, help=True),
    # cahaya
    _f("style.glow", "object", "cahaya",
       default_on={"color": "#FF2BD6CC", "width": 10, "blur": 10, "strength": 1.0, "active_only": False}),
    _f("style.glow.color", "color", "cahaya", alpha=True, show_if={"style.glow": _ON}),
    _f("style.glow.width", "number", "cahaya", min=2, max=30, step=1, unit="px", decimals=0,
       show_if={"style.glow": _ON}),
    _f("style.glow.blur", "number", "cahaya", min=0, max=30, step=1, unit="px", decimals=0,
       show_if={"style.glow": _ON}),
    _f("style.glow.strength", "number", "cahaya", min=0.2, max=3, step=0.1, decimals=1,
       show_if={"style.glow": _ON}),
    _f("style.glow.active_only", "bool", "cahaya", show_if={"style.glow": _ON}),
    # latar
    _f("box.enabled", "bool", "latar"),
    _f("box.shape", "enum", "latar", options=_opts(BOX_SHAPE_LABELS), show_if={"box.enabled": True}),
    _f("box.per", "enum", "latar", show_if={"box.enabled": True, "box.shape": ["rect", "marker",
                                                                                            "banner", "ribbon"]},
       options=[{"id": "line"}, {"id": "page"},
                {"id": "word"}]),
    _f("box.color", "color", "latar", alpha=True, show_if={"box.enabled": True}),
    _f("box.opacity", "number", "latar", min=0, max=1, step=0.05, decimals=2, format="percent",
       nullable=True, show_if={"box.enabled": True}),
    _f("box.filled", "bool", "latar", show_if={"box.enabled": True},
       help=True),
    _f("box.radius", "number", "latar", min=0, max=60, step=1, unit="px", decimals=0,
       show_if={"box.enabled": True, "box.shape": ["rect", "sticker"]}),
    _f("box.pad_x", "number", "latar", min=0, max=60, step=1, unit="px", decimals=0,
       show_if={"box.enabled": True}),
    _f("box.pad_y", "number", "latar", min=0, max=40, step=1, unit="px", decimals=0,
       show_if={"box.enabled": True}),
    _f("box.border", "number", "latar", min=0, max=16, step=1, unit="px", decimals=0,
       show_if={"box.enabled": True}),
    _f("box.border_color", "color", "latar", alpha=True,
       show_if={"box.enabled": True, "box.border": _ON}),
    _f("box.tilt", "number", "latar", min=-12, max=12, step=0.5, unit="°", decimals=1,
       show_if={"box.enabled": True}, help=True),
    _f("box.gradient", "object", "latar", default_on={"colors": ["#7C3AED", "#DB2777"], "bands": 8},
       show_if={"box.enabled": True}),
    _f("box.gradient.colors", "colors", "latar", min_items=2, max_items=3, alpha=True,
       show_if={"box.enabled": True, "box.gradient": _ON}),
    _f("box.shadow", "object", "latar", default_on={"x": 0, "y": 6, "blur": 8, "color": "#00000099"},
       show_if={"box.enabled": True}),
    _f("box.shadow.x", "number", "latar", min=-20, max=20, step=1, unit="px", decimals=0,
       show_if={"box.enabled": True, "box.shadow": _ON}),
    _f("box.shadow.y", "number", "latar", min=-20, max=20, step=1, unit="px", decimals=0,
       show_if={"box.enabled": True, "box.shadow": _ON}),
    _f("box.shadow.blur", "number", "latar", min=0, max=30, step=1, unit="px", decimals=0,
       show_if={"box.enabled": True, "box.shadow": _ON}),
    _f("box.shadow.color", "color", "latar", alpha=True,
       show_if={"box.enabled": True, "box.shadow": _ON}),
    # sorot (active word)
    _f("timing.mode", "enum", "sorot", options=_opts(MODES)),
    _f("highlight.color", "color", "sorot", nullable=True, null_label=True),
    _f("highlight.outline_color", "color", "sorot", nullable=True),
    _f("highlight.scale", "number", "sorot", min=1, max=1.4, step=0.02, decimals=2,
       format="percent"),
    _f("highlight.pill", "color", "sorot", nullable=True, alpha=True, null_label=True),
    _f("highlight.pill_shape", "enum", "sorot", show_if={"highlight.pill": _ON},
       options=[{"id": "rect"}, {"id": "marker"},
                {"id": "banner"}, {"id": "ribbon"}]),
    _f("highlight.pill_slide", "bool", "sorot", show_if={"highlight.pill": _ON},
       help=True),
    _f("highlight.pill_radius", "number", "sorot", min=0, max=40, step=1, unit="px", decimals=0,
       show_if={"highlight.pill": _ON}),
    _f("highlight.underline", "color", "sorot", nullable=True, alpha=True),
    _f("highlight.sweep", "bool", "sorot"),
    _f("highlight.glow", "object", "sorot",
       default_on={"color": "#FFE600CC", "width": 10, "blur": 12, "strength": 1.4}),
    _f("highlight.glow.color", "color", "sorot", alpha=True, show_if={"highlight.glow": _ON}),
    _f("highlight.glow.strength", "number", "sorot", min=0.2, max=3, step=0.1, decimals=1,
       show_if={"highlight.glow": _ON}),
    _f("highlight.wiggle", "number", "sorot", min=0, max=15, step=1, unit="°", decimals=0),
    _f("highlight.pop_dur", "number", "sorot", min=0.04, max=0.3, step=0.01, unit="sec",
       decimals=2),
    # posisi
    _f("layout.rotate", "number", "posisi", min=-15, max=15, step=0.5, unit="°", decimals=1),
    _f("layout.rotate_jitter", "number", "posisi", min=0, max=10, step=0.5, unit="°",
       decimals=1, help=True),
    _f("layout.y", "number", "posisi", min=5, max=95, step=1, unit="%", decimals=0),
    _f("layout.x", "number", "posisi", min=5, max=95, step=1, unit="%", decimals=0),
    _f("layout.width", "number", "posisi", min=30, max=100, step=2, unit="%", decimals=0),
    _f("layout.align", "enum", "posisi", options=[{"id": "center"},
                                                          {"id": "left"},
                                                          {"id": "right"}]),
    _f("layout.safe", "enum", "posisi", options_ref="safe_zones"),
    _f("layout.max_lines", "int", "posisi", min=1, max=5, step=1),
    _f("layout.max_words", "int", "posisi", min=1, max=15, step=1),
    _f("layout.max_chars", "int", "posisi", min=6, max=60, step=1),
    # animasi (lists come from options.anim_in / anim_out / word_in)
    _f("anim.in", "enum", "animasi", options_ref="anim_in"),
    _f("anim.in_dur", "number", "animasi", min=0.04, max=0.6, step=0.02, unit="sec", decimals=2),
    _f("anim.out", "enum", "animasi", options_ref="anim_out"),
    _f("anim.out_dur", "number", "animasi", min=0, max=0.6, step=0.02, unit="sec", decimals=2),
    _f("anim.word_in", "enum", "animasi", options_ref="word_in",
       show_if={"timing.mode": "reveal"}),
    _f("anim.word_in_dur", "number", "animasi", min=0.04, max=0.4, step=0.02, unit="sec",
       decimals=2, show_if={"timing.mode": "reveal"}),
]
WORD_STYLE_SCHEMA = [
    {"key": "color", "type": "color", "alpha": True},
    {"key": "active_color", "type": "color", "alpha": True},
    {"key": "outline_color", "type": "color", "alpha": True},
    {"key": "font", "type": "font"},
    {"key": "scale", "type": "number", "min": 0.5, "max": 2.5, "step": 0.05, "format": "percent"},
    {"key": "bold", "type": "bool"},
    {"key": "italic", "type": "bool"},
    {"key": "case", "type": "enum", "options": _opts(CASE_LABELS), "nullable": True},
    {"key": "uppercase", "type": "bool"},
    {"key": "rotate", "type": "number", "min": -30, "max": 30, "step": 1, "unit": "°"},
    {"key": "pill", "type": "color", "alpha": True},
    {"key": "underline", "type": "color", "alpha": True},
    {"key": "glow", "type": "object", "default_on": {"color": "#FFE600CC", "width": 10, "blur": 10}},
    {"key": "emoji", "type": "emoji"},
]
for _w in WORD_STYLE_SCHEMA:
    _w["label"] = "cap.tpl.w." + _camel(_w["key"])


def tl(v):
    """Translate a label that is a locale key ("cap.*", "unit.*"); anything else (user text, ids) as is."""
    if isinstance(v, str) and (v.startswith("cap.") or v.startswith("unit.")) and i18n.has(v):
        return tr(v)
    return v


def _tl_field(f):
    g = dict(f)
    for k in ("label", "help", "null_label"):
        if k in g:
            g[k] = tl(g[k])
    if g.get("unit") == "sec":
        g["unit"] = tr("unit.sec")
    if g.get("options"):
        g["options"] = [dict(o, label=tl(o.get("label"))) for o in g["options"]]
    return g


def _labels(items):
    """[{id, label(key), ...}] -> translated copies."""
    return [dict(o, label=tl(o.get("label"))) for o in items]


def builtin_text(tpl, field):
    """Name / description of a template in the job language: built-ins from the locale (cap.tpl.<id>.name|desc),
    user templates keep the user's own text."""
    raw_v = tpl.get("name" if field == "name" else "description") or ""
    if tpl.get("source", "builtin") != "builtin":
        return raw_v
    key = f"cap.tpl.{tpl.get('id')}.{'name' if field == 'name' else 'desc'}"
    return tr(key) if i18n.has(key) else raw_v


def display_name(tpl):
    return builtin_text(tpl, "name") or tpl.get("id") or ""


def _get_path(d, path):
    for part in path.split("."):
        if not isinstance(d, dict) or part not in d:
            return None
        d = d[part]
    return d


def style_schema():
    """STYLE_SCHEMA with each field's `default` filled from DEFAULTS (object sub-fields: from `default_on`)."""
    out = []
    objs = {f["path"]: f.get("default_on") for f in STYLE_SCHEMA if f["type"] == "object"}
    for f in STYLE_SCHEMA:
        g = dict(f)
        parent = f["path"].rsplit(".", 1)[0]
        if parent in objs and objs[parent] is not None:
            g["default"] = (objs[parent] or {}).get(f["path"].rsplit(".", 1)[1])
            g["parent"] = parent
        else:
            g["default"] = _get_path(DEFAULTS, f["path"])
        out.append(_tl_field(g))
    return out


def user_dir():
    from ..util import appdata_dir
    d = appdata_dir() / "caption_templates"
    d.mkdir(parents=True, exist_ok=True)
    return d


def slug(name):
    s = re.sub(r"[^a-z0-9]+", "_", str(name or "").lower()).strip("_")
    return s[:48] or "template"


def _read(p):
    try:
        data = json.loads(Path(p).read_text(encoding="utf-8-sig"))
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


@lru_cache(maxsize=64)
def _builtin_raw(tid):
    return _read(BUILTIN_DIR / f"{tid}.json")


def builtin_ids():
    return sorted(p.stem for p in BUILTIN_DIR.glob("*.json"))


def user_ids():
    try:
        return sorted(p.stem for p in user_dir().glob("*.json"))
    except OSError:
        return []


def raw(tid):
    """Stored (partial) template dict and its source ('user' | 'builtin'), or (None, None)."""
    tid = slug(tid)
    up = None
    try:
        up = user_dir() / f"{tid}.json"
    except OSError:
        pass
    if up is not None and up.is_file():
        d = _read(up)
        if d is not None:
            return d, "user"
    d = _builtin_raw(tid)
    return (dict(d), "builtin") if d is not None else (None, None)


def default_id(W, H):
    from ..util import setting
    s = slug(setting("captionTemplate") or "")
    if s and raw(s)[0] is not None:
        return s
    return DEFAULT_PORTRAIT if aspect_kind(W, H) == "portrait" else DEFAULT_LANDSCAPE


def load(tid, W=1920, H=1080):
    """Full template (DEFAULTS merged) for an id; unknown ids fall back to the aspect default."""
    d, src = raw(tid) if tid else (None, None)
    if d is None:
        tid = default_id(W, H)
        d, src = raw(tid)
    base = d.get("base")            # user templates may extend another template
    full = deep_merge(load(base, W, H) if base and slug(base) != slug(tid) else DEFAULTS, d)
    full["id"] = slug(tid)
    full["source"] = src or "builtin"
    full.pop("base", None)
    return full


def summary(tpl, thumb=None):
    """Metadata row for the template picker."""
    return {"id": tpl["id"], "name": display_name(tpl), "description": builtin_text(tpl, "description"),
            "source": tpl.get("source", "builtin"), "aspect": tpl.get("aspect") or [],
            "default_for": tpl.get("default_for") or [], "tags": tpl.get("tags") or [],
            "font": tpl["font"]["family"], "uppercase": bool(tpl["font"].get("uppercase")),
            "mode": tpl["timing"]["mode"], "anim_in": tpl["anim"]["in"], "anim_out": tpl["anim"]["out"],
            "box": tpl["box"]["per"] if tpl["box"]["enabled"] else None,
            "highlight": tpl["highlight"].get("color"), "emoji": bool(tpl["emoji"].get("enabled")),
            "thumb": thumb}


def list_all(W=1920, H=1080):
    ids = list(dict.fromkeys(builtin_ids() + user_ids()))
    out = []
    for tid in ids:
        try:
            out.append(load(tid, W, H))
        except Exception:  # noqa: BLE001 - one broken user file must not hide the others
            continue
    return out


def strip_defaults(full, base=None):
    """Keys of `full` that differ from `base` (default DEFAULTS): what a template file stores."""
    base = DEFAULTS if base is None else base
    out = {}
    for k, v in full.items():
        if k in ("id", "source") or k.startswith("_"):
            continue
        b = base.get(k) if isinstance(base, dict) else None
        if isinstance(v, dict) and isinstance(b, dict):
            sub = strip_defaults(v, b)
            if sub:
                out[k] = sub
        elif v != b:
            out[k] = v
    return out


def save_user(tpl, tid=None, name=None):
    """Write a user template (full or partial dict). Returns the loaded full template."""
    from ..util import write_json
    data = dict(tpl or {})
    name = name or data.get("name") or tr("cap.tpl.mine")
    tid = slug(tid or data.get("id") or name)
    if tid in builtin_ids() and not tid.startswith("user_"):
        tid = "user_" + tid            # never shadow a built-in by accident
    data["name"] = name
    full = deep_merge(DEFAULTS, {k: v for k, v in data.items() if k not in ("id", "source")})
    write_json(user_dir() / f"{tid}.json", strip_defaults(full), indent=2)
    return load(tid)


def delete_user(tid):
    p = user_dir() / f"{slug(tid)}.json"
    if p.is_file():
        p.unlink()
        return True
    return False


def anim_label(k):
    """Label of a classic page / word animation id (compile.py owns the list) in the job language."""
    from . import compile as C
    fn = getattr(C, "anim_label", None)
    if callable(fn):
        return fn(k)
    return tl(getattr(C, "ANIM_LABELS", {}).get(k, k))


def options():
    """Everything the style editor offers (values + labels in the job language)."""
    from .compile import IN, OUT, WORD_IN

    def presets(d, only=None):
        return [{"id": k, "label": tl(v["label"]), "patch": v["patch"]} for k, v in d.items() if only is None or k in only]
    return {
        "fonts": font_families(),
        "anim_in": [{"id": k, "label": anim_label(k)} for k in IN],
        "anim_out": [{"id": k, "label": anim_label(k)} for k in OUT],
        "word_in": [{"id": k, "label": anim_label(k)} for k in WORD_IN],
        "modes": [{"id": k, "label": tl(v)} for k, v in MODES.items()],
        # classic 9 (current Animasi tab), and every active-word look incl. the Gaya Pro ones
        "highlight_presets": presets(HIGHLIGHT_PRESETS, CLASSIC_HIGHLIGHT),
        "highlight_presets_all": presets(HIGHLIGHT_PRESETS),
        "box_presets": presets(BOX_PRESETS),
        "box_shapes": presets(BOX_SHAPES),
        "effect_presets": presets(EFFECT_PRESETS),
        "cases": _labels(_opts(CASE_LABELS)),
        "font_categories": [{"id": k, "label": tl(v)} for k, v in FONT_CATEGORIES.items()],
        "groups": [{"id": k, "label": tl(v)} for k, v in STYLE_GROUPS],
        "schema": style_schema(),
        "word_schema": [_tl_field(w) for w in WORD_STYLE_SCHEMA],
        "safe_zones": [{"id": k, "label": tl(v)} for k, v in SAFE_LABELS.items()],
        "punct": _labels([{"id": k, "label": "cap.tpl.punct." + k} for k in ("keep", "soft", "strip")]),
        "align": _labels([{"id": k, "label": "cap.tpl.o.layoutAlign." + k} for k in ("center", "left", "right")]),
        "emoji_position": _labels([{"id": k, "label": "cap.tpl.emojiPos." + k} for k in ("above", "after")]),
        "word_style_keys": list(WORD_STYLE_KEYS),
        "censor_modes": _labels([{"id": "auto", "label": "cap.tpl.censor.auto"}, {"id": "off", "label": "cap.tpl.censor.off"},
                                 {"id": "stars", "label": "a****g"},
                                 {"id": "first", "label": "a*****"}, {"id": "full", "label": "******"},
                                 {"id": "bleep", "label": "[sensor]"}]),
        "defaults": DEFAULTS,
    }
