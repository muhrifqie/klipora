"""Caption animation studio: keyframe animation presets for page in / out, the active word and an idle loop,
user (custom) animations, the compile hooks used by compile.py and the preview sprite sheets.

Model (docs/CAPTIONS_API.md 11):
  template / doc / range / page style key `anim.fx` = {"in": A, "active": A, "out": A, "loop": A} (each optional,
  null = off), where A is a preset id ("pop_kata"), {"preset": id, "dur": .., "stagger": .., ...} (preset + overrides)
  or a full inline spec. A spec:
    {"id", "label", "group": in|active|out|loop, "dur": s (loop: period), "stagger": s per word, "order":
     forward|reverse|center|edges|random, "unit": word|page, "release": s (active: back to rest after the word),
     "tracks": {track: [{"t": 0..1 of dur, "v": value, "e": easing to the next key}, ...]}}
  tracks: scale (x), opacity (0..1), x / y (% of the font size, + = right / down), rot (deg, + = clockwise on
  screen), blur (px @1080), spacing (letter spacing px @1080), color (#RRGGBB or null = the word's own colour).
  easings: linear in out in_out back back_in elastic spring bounce steps:N.
`anim.fx.in` / `out` replace the classic `anim.in` / `anim.out` for that page; `active` adds to the classic
highlight (colour, pill, scale); `loop` runs while the page is on screen. Everything compiles into the existing
ASS keyframe compiler (<= 35 ms linear pieces, \\t / \\move / \\fade per piece), so preview = render.

No module-level import of compile.py (compile imports this module).
"""
from __future__ import annotations

import json
import math
import re
from pathlib import Path

from .. import i18n
from ..i18n import tr

def tl(v):
    """A label that is a locale key (built-in presets, tracks) in the job language; user text as is."""
    if isinstance(v, str) and v.startswith("cap.") and i18n.has(v):
        return tr(v)
    return v


TRACKS = ("scale", "opacity", "x", "y", "rot", "blur", "spacing", "color")
NEUTRAL = {"scale": 1.0, "opacity": 1.0, "x": 0.0, "y": 0.0, "rot": 0.0, "blur": 0.0, "spacing": 0.0, "color": None}
LIMITS = {"scale": (0.0, 5.0), "opacity": (0.0, 1.0), "x": (-1500.0, 1500.0), "y": (-1500.0, 1500.0),
          "rot": (-1080.0, 1080.0), "blur": (0.0, 60.0), "spacing": (-30.0, 120.0)}
TRACK_INFO = [
    {"id": "scale", "label": "cap.anim.track.scale", "unit": "x", "min": 0, "max": 3, "step": 0.05, "decimals": 2, "default": 1},
    {"id": "opacity", "label": "cap.anim.track.opacity", "unit": "%", "min": 0, "max": 1, "step": 0.05, "decimals": 2,
     "format": "percent", "default": 1},
    {"id": "x", "label": "cap.anim.track.x", "unit": "%", "min": -400, "max": 400, "step": 5, "decimals": 0, "default": 0,
     "help": "cap.anim.trackHelp.x"},
    {"id": "y", "label": "cap.anim.track.y", "unit": "%", "min": -400, "max": 400, "step": 5, "decimals": 0, "default": 0,
     "help": "cap.anim.trackHelp.y"},
    {"id": "rot", "label": "cap.anim.track.rot", "unit": "°", "min": -360, "max": 360, "step": 1, "decimals": 0, "default": 0},
    {"id": "blur", "label": "cap.anim.track.blur", "unit": "px", "min": 0, "max": 40, "step": 1, "decimals": 0, "default": 0},
    {"id": "spacing", "label": "cap.anim.track.spacing", "unit": "px", "min": -10, "max": 80, "step": 1, "decimals": 0,
     "default": 0},
    {"id": "color", "label": "cap.anim.track.color", "unit": "", "default": None, "type": "color"},
]
GROUPS = [(g, "cap.anim.group." + g) for g in ("in", "active", "out", "loop")]
GROUP_IDS = tuple(g for g, _ in GROUPS)
ORDERS = [(o, "cap.anim.order." + o) for o in ("forward", "reverse", "center", "edges", "random")]
EASINGS = [(e, "cap.anim.ease." + e.replace(":", "").replace("_", "")) for e in (
    "linear", "in", "out", "in_out", "back", "back_in", "elastic", "spring", "bounce", "steps:1", "steps:3", "steps:6")]
DEFAULT_DUR = {"in": 0.35, "active": 0.25, "out": 0.25, "loop": 1.6}
SAMPLE = 0.035          # max piece length (s): every animated window is sampled at least this densely
_HEX = re.compile(r"^#[0-9a-fA-F]{6}([0-9a-fA-F]{2})?$")


# ---------------------------------------------------------------- easing

def _c01(p):
    return 0.0 if p < 0 else 1.0 if p > 1 else p


def _bounce(p):
    n, d = 7.5625, 2.75
    if p < 1 / d:
        return n * p * p
    if p < 2 / d:
        p -= 1.5 / d
        return n * p * p + 0.75
    if p < 2.5 / d:
        p -= 2.25 / d
        return n * p * p + 0.9375
    p -= 2.625 / d
    return n * p * p + 0.984375


_SPRING_END = 1 - math.exp(-6.0) * math.cos(14.0)


def ease(name, p):
    """Eased progress for p in 0..1 (may overshoot for back / elastic / spring)."""
    p = _c01(p)
    name = str(name or "linear")
    if name == "linear":
        return p
    if name == "in":
        return p ** 3
    if name == "out":
        return 1 - (1 - p) ** 3
    if name == "in_out":
        return 4 * p ** 3 if p < 0.5 else 1 - (-2 * p + 2) ** 3 / 2
    if name == "back":
        s, q = 1.70158, p - 1
        return 1 + (s + 1) * q ** 3 + s * q ** 2
    if name == "back_in":
        s = 1.70158
        return (s + 1) * p ** 3 - s * p ** 2
    if name == "elastic":
        if p in (0.0, 1.0):
            return p
        return 2 ** (-10 * p) * math.sin((p * 10 - 0.75) * (2 * math.pi / 3)) + 1
    if name == "spring":
        return (1 - math.exp(-6 * p) * math.cos(14 * p)) / _SPRING_END
    if name == "bounce":
        return _bounce(p)
    if name.startswith("steps"):
        try:
            n = max(1, min(30, int(name.split(":", 1)[1])))
        except (IndexError, ValueError):
            n = 4
        return 1.0 if p >= 1 else math.floor(p * n) / n
    return p


def _is_ease(name):
    if not isinstance(name, str):
        return False
    if name.startswith("steps:"):
        return name[6:].isdigit()
    return name in {e for e, _ in EASINGS}


# ---------------------------------------------------------------- colour helpers (own copy: no compile import)

def _rgb(c):
    c = c.lstrip("#")
    return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16), (int(c[6:8], 16) if len(c) == 8 else 255)


def mix(c1, c2, f):
    """Linear RGBA mix of two #RRGGBB[AA] colours (f may overshoot: clamped per channel)."""
    a, b = _rgb(c1), _rgb(c2)
    v = [max(0, min(255, round(x + (y - x) * f))) for x, y in zip(a, b)]
    return "#%02X%02X%02X" % tuple(v[:3]) + ("%02X" % v[3] if v[3] != 255 else "")


# ---------------------------------------------------------------- presets (built-in library)

def K(*keys):
    """Compact keyframes: K((t, v[, e]), ...) -> [{t, v, e}]."""
    return [{"t": k[0], "v": k[1], "e": k[2] if len(k) > 2 else "linear"} for k in keys]


def P(pid, group, dur, tracks, stagger=0.0, order="forward", unit="word", release=0.15, desc=""):
    """Built-in preset; label (and desc) are locale keys cap.anim.p.<id> / cap.anim.pDesc.<id>, see tl()."""
    return {"id": pid, "label": "cap.anim.p." + pid, "group": group, "dur": dur, "stagger": stagger, "order": order,
            "unit": unit, "release": release, "desc": desc, "tracks": tracks, "builtin": True}


PRESETS_LIST = [
    # ---- Masuk (page entrance)
    P("pudar_halus", "in", 0.35, {"opacity": K((0, 0, "out"), (1, 1)), "y": K((0, 18, "out"), (1, 0))},  # i18n-ignore
      stagger=0.04),
    P("pop_kata", "in", 0.32, {"scale": K((0, 0.2, "back"), (1, 1)),  # i18n-ignore
                                               "opacity": K((0, 0, "out"), (0.35, 1))}, stagger=0.06),
    P("pantul_masuk", "in", 0.6, {"y": K((0, -140, "bounce"), (1, 0)), "opacity": K((0, 0), (0.15, 1))},  # i18n-ignore
      stagger=0.07),
    P("naik", "in", 0.38, {"y": K((0, 90, "out"), (1, 0)), "opacity": K((0, 0, "out"), (0.6, 1))},
      stagger=0.05),
    P("turun", "in", 0.38, {"y": K((0, -90, "out"), (1, 0)), "opacity": K((0, 0, "out"), (0.6, 1))},
      stagger=0.05),
    P("dari_kanan", "in", 0.4, {"x": K((0, 160, "out"), (1, 0)), "opacity": K((0, 0), (0.5, 1))},  # i18n-ignore
      stagger=0.05),
    P("dari_kiri", "in", 0.4, {"x": K((0, -160, "out"), (1, 0)), "opacity": K((0, 0), (0.5, 1))},  # i18n-ignore
      stagger=0.05, order="reverse"),
    P("zoom_masuk", "in", 0.4, {"scale": K((0, 2.4, "out"), (1, 1)), "blur": K((0, 14, "out"), (1, 0)),  # i18n-ignore
                                              "opacity": K((0, 0), (0.4, 1))}, stagger=0.05),
    P("blur_fokus", "in", 0.45, {"blur": K((0, 22, "out"), (1, 0)), "opacity": K((0, 0, "out"), (0.7, 1))},  # i18n-ignore
      stagger=0.04),
    P("putar_masuk", "in", 0.45, {"rot": K((0, -120, "back"), (1, 0)),  # i18n-ignore
                                                 "scale": K((0, 0.3, "back"), (1, 1)),
                                                 "opacity": K((0, 0), (0.3, 1))}, stagger=0.06),
    P("pegas_masuk", "in", 0.7, {"scale": K((0, 0.2, "elastic"), (1, 1)),  # i18n-ignore
                                          "opacity": K((0, 0), (0.15, 1))}, stagger=0.06),
    P("ketik_kata", "in", 0.05, {"opacity": K((0, 0, "steps:1"), (1, 1))}, stagger=0.1),  # i18n-ignore
    P("renggang_masuk", "in", 0.5, {"spacing": K((0, 40, "out"), (1, 0)),  # i18n-ignore
                                                  "opacity": K((0, 0, "out"), (0.7, 1))}, unit="word"),
    P("kilat_warna", "in", 0.45, {"color": K((0, "#FFE600", "in_out"), (1, None)),  # i18n-ignore
                                                 "scale": K((0, 1.35, "out"), (1, 1)),
                                                 "opacity": K((0, 0), (0.2, 1))}, stagger=0.05),
    P("jatuh_putar", "in", 0.7, {"y": K((0, -200, "bounce"), (1, 0)),  # i18n-ignore
                                                   "rot": K((0, 40, "out"), (0.7, 0)),
                                                   "opacity": K((0, 0), (0.12, 1))}, stagger=0.08),
    P("glitch_masuk", "in", 0.36, {"x": K((0, -30, "steps:1"), (0.2, 24, "steps:1"), (0.4, -14, "steps:1"),  # i18n-ignore
                                                    (0.6, 8, "steps:1"), (0.8, 0)),
                                              "opacity": K((0, 0.3, "steps:1"), (0.2, 1, "steps:1"), (0.4, 0.5, "steps:1"),
                                                           (0.6, 1)),
                                              "color": K((0, "#00F0FF", "steps:1"), (0.3, "#FF2E63", "steps:1"),
                                                         (0.6, None))}, stagger=0.03, order="random"),
    P("blok_naik", "in", 0.42, {"y": K((0, 60, "back"), (1, 0)), "opacity": K((0, 0), (0.4, 1)),  # i18n-ignore
                                             "scale": K((0, 0.9, "out"), (1, 1))}, unit="page"),
    # ---- Kata aktif (while the word is spoken)
    P("aktif_pop", "active", 0.22, {"scale": K((0, 1, "back"), (1, 1.18))}, release=0.15),  # i18n-ignore
    P("aktif_lompat", "active", 0.3, {"y": K((0, 0, "out"), (0.45, -28, "in"), (1, 0))}, release=0.1),  # i18n-ignore
    P("aktif_goyang", "active", 0.36, {"rot": K((0, 0, "in_out"), (0.25, -9, "in_out"), (0.75, 9, "in_out"),  # i18n-ignore
                                                          (1, 0))}, release=0.1),
    P("aktif_denyut", "active", 0.5, {"scale": K((0, 1, "in_out"), (0.25, 1.16, "in_out"), (0.5, 1.04, "in_out"),  # i18n-ignore
                                                           (0.75, 1.16, "in_out"), (1, 1.08))}, release=0.15),
    P("aktif_miring", "active", 0.18, {"rot": K((0, 0, "back"), (1, -7)),  # i18n-ignore
                                                 "scale": K((0, 1, "out"), (1, 1.08))}, release=0.15),
    P("aktif_pegas", "active", 0.6, {"scale": K((0, 0.85, "elastic"), (1, 1.2))}, release=0.15),  # i18n-ignore
    P("aktif_kilat", "active", 0.3, {"color": K((0, "#FFFFFF", "out"), (1, None)),
                                                    "scale": K((0, 1.25, "out"), (1, 1.1))}, release=0.12),
    P("aktif_renggang", "active", 0.25, {"spacing": K((0, 0, "out"), (1, 10)),  # i18n-ignore
                                                       "scale": K((0, 1, "out"), (1, 1.06))}, release=0.15),
    P("aktif_naik", "active", 0.2, {"y": K((0, 0, "back"), (1, -12)),  # i18n-ignore
                                                    "scale": K((0, 1, "back"), (1, 1.1))}, release=0.15),
    # ---- Keluar (page exit)
    P("keluar_pudar", "out", 0.25, {"opacity": K((0, 1, "in"), (1, 0))}),  # i18n-ignore
    P("keluar_turun", "out", 0.3, {"y": K((0, 0, "in"), (1, 70)), "opacity": K((0, 1, "in"), (1, 0))},  # i18n-ignore
      stagger=0.03),
    P("keluar_naik", "out", 0.3, {"y": K((0, 0, "in"), (1, -70)), "opacity": K((0, 1, "in"), (1, 0))},  # i18n-ignore
      stagger=0.03),
    P("keluar_kecil", "out", 0.3, {"scale": K((0, 1, "back_in"), (1, 0)),  # i18n-ignore
                                               "opacity": K((0, 1), (0.7, 1, "in"), (1, 0))}, stagger=0.04),
    P("keluar_zoom", "out", 0.3, {"scale": K((0, 1, "in"), (1, 1.9)), "opacity": K((0, 1, "in"), (1, 0)),  # i18n-ignore
                                                 "blur": K((0, 0, "in"), (1, 10))}),
    P("keluar_blur", "out", 0.3, {"blur": K((0, 0, "in"), (1, 20)), "opacity": K((0, 1, "in"), (1, 0))}),  # i18n-ignore
    P("keluar_putar", "out", 0.35, {"rot": K((0, 0, "in"), (1, 110)),  # i18n-ignore
                                                    "scale": K((0, 1, "back_in"), (1, 0)),
                                                    "opacity": K((0, 1), (0.8, 1), (1, 0))}, stagger=0.04),
    P("keluar_kiri", "out", 0.32, {"x": K((0, 0, "in"), (1, -170)), "opacity": K((0, 1, "in"), (1, 0))},  # i18n-ignore
      stagger=0.03),
    # ---- Loop (idle while the page is on screen)
    P("loop_melayang", "loop", 2.0, {"y": K((0, 0, "in_out"), (0.5, -9, "in_out"), (1, 0))}, stagger=0.0),  # i18n-ignore
    P("loop_gelombang", "loop", 1.2, {"y": K((0, 0, "in_out"), (0.25, -10, "in_out"), (0.75, 10, "in_out"),  # i18n-ignore
                                                          (1, 0))}, stagger=0.12),
    P("loop_goyang", "loop", 1.6, {"rot": K((0, 0, "in_out"), (0.25, 2.5, "in_out"),  # i18n-ignore
                                                            (0.75, -2.5, "in_out"), (1, 0))}, stagger=0.1),
    P("loop_denyut", "loop", 1.1, {"scale": K((0, 1, "in_out"), (0.5, 1.05, "in_out"), (1, 1))}),  # i18n-ignore
    P("loop_getar", "loop", 0.24, {"x": K((0, 0, "steps:1"), (0.25, 2.5, "steps:1"), (0.5, -2, "steps:1"),  # i18n-ignore
                                                   (0.75, 1.5, "steps:1"), (1, 0)),
                                             "y": K((0, 0, "steps:1"), (0.25, -1.5, "steps:1"), (0.5, 2, "steps:1"),
                                                    (0.75, -1, "steps:1"), (1, 0))}, stagger=0.05, order="random"),
    P("loop_kedip", "loop", 1.4, {"opacity": K((0, 1, "in_out"), (0.5, 0.72, "in_out"), (1, 1))},  # i18n-ignore
      stagger=0.1),
]
PRESETS = {p["id"]: p for p in PRESETS_LIST}


# ---------------------------------------------------------------- spec normalization

def _num(v, default, lo=None, hi=None):
    try:
        v = float(v)
        if not math.isfinite(v):
            raise ValueError
    except (TypeError, ValueError):
        v = float(default)
    if lo is not None:
        v = max(lo, v)
    if hi is not None:
        v = min(hi, v)
    return v


def norm_keys(name, keys):
    """Clean keyframe list for one track: sorted by t, values clamped, easing names checked. [] when unusable."""
    out = []
    for k in keys or []:
        if isinstance(k, (list, tuple)) and len(k) >= 2:
            k = {"t": k[0], "v": k[1], "e": k[2] if len(k) > 2 else "linear"}
        if not isinstance(k, dict) or "t" not in k:
            continue
        t = round(_num(k.get("t"), 0.0, 0.0, 1.0), 4)
        if name == "color":
            v = k.get("v")
            v = v.upper() if isinstance(v, str) and _HEX.match(v) else None
        else:
            lo, hi = LIMITS[name]
            v = round(_num(k.get("v"), NEUTRAL[name], lo, hi), 4)
        e = k.get("e") if _is_ease(k.get("e")) else "linear"
        out.append({"t": t, "v": v, "e": e})
    out.sort(key=lambda k: k["t"])
    return out


def norm_spec(spec, group=None):
    """Full, validated spec dict (or None). Accepts a preset id, {"preset": id, ...overrides} or an inline spec."""
    if spec is None or spec is False or spec == "" or spec == "none":
        return None
    if isinstance(spec, str):
        spec = {"preset": spec}
    if not isinstance(spec, dict):
        return None
    base = {}
    pid = spec.get("preset")
    if pid:
        base = PRESETS.get(pid) or user_anims().get(pid) or {}
        if not base:
            return None
    s = dict(base)
    s.update({k: v for k, v in spec.items() if k != "preset"})
    g = s.get("group") if s.get("group") in GROUP_IDS else (group if group in GROUP_IDS else "in")
    if group in GROUP_IDS and g != group:
        g = group                                    # a spec dropped on another role plays as that role
    tracks = {}
    for name, keys in (s.get("tracks") or {}).items():
        if name in TRACKS:
            kk = norm_keys(name, keys)
            if kk:
                tracks[name] = kk
    if not tracks:
        return None
    return {"id": str(s.get("id") or pid or "custom")[:64], "label": str(tl(s.get("label")) or tr("cap.anim.mine"))[:60],
            "group": g, "dur": round(_num(s.get("dur"), DEFAULT_DUR[g], 0.02, 6.0), 3),
            "stagger": round(_num(s.get("stagger"), 0.0, 0.0, 1.0), 3),
            "order": s.get("order") if s.get("order") in {o for o, _ in ORDERS} else "forward",
            "unit": "page" if s.get("unit") == "page" else "word",
            "release": round(_num(s.get("release"), 0.15, 0.0, 1.0), 3),
            "desc": str(s.get("desc") or "")[:200], "tracks": tracks, "builtin": bool(s.get("builtin")) and not
            any(k in spec for k in ("tracks", "dur", "stagger", "order", "unit", "release"))}


_RESOLVE_CACHE = {}


def resolve_fx(fx):
    """anim.fx -> {role: spec} with only the roles that are on (cached by content)."""
    if not isinstance(fx, dict) or not fx:
        return {}
    try:
        key = json.dumps(fx, sort_keys=True, default=str)
    except (TypeError, ValueError):
        return {}
    hit = _RESOLVE_CACHE.get(key)
    if hit is None:
        hit = {}
        for role in GROUP_IDS:
            sp = norm_spec(fx.get(role), role)
            if sp:
                hit[role] = sp
        if len(_RESOLVE_CACHE) > 256:
            _RESOLVE_CACHE.clear()
        _RESOLVE_CACHE[key] = hit
    return hit


# ---------------------------------------------------------------- evaluation

def track_at(keys, p, base_color=None):
    """Value of one track at progress p (0..1 of the spec duration; outside = held first / last key)."""
    if not keys:
        return None
    if p <= keys[0]["t"] or len(keys) == 1:
        v = keys[0]["v"]
        return (v if v is not None else base_color) if isinstance(v, str) or v is None else v
    for a, b in zip(keys, keys[1:]):
        if p < b["t"]:
            f = ease(a["e"], (p - a["t"]) / max(1e-6, b["t"] - a["t"]))
            va, vb = a["v"], b["v"]
            if isinstance(va, str) or isinstance(vb, str) or va is None or vb is None:
                ca, cb = va or base_color, vb or base_color
                if not ca or not cb:
                    return ca or cb
                return mix(ca, cb, f)
            return va + (vb - va) * f
    v = keys[-1]["v"]
    return (v or base_color) if (isinstance(v, str) or v is None) else v


def values_at(spec, p, base_color=None):
    """{track: value} of a spec at progress p (only the spec's tracks)."""
    return {name: track_at(keys, p, base_color) for name, keys in spec["tracks"].items()}


def _ranks(n, order, seed=0):
    """Animation order of n words -> rank per word index."""
    idx = list(range(n))
    if order == "reverse":
        return [n - 1 - i for i in idx]
    if order == "center":
        c = (n - 1) / 2
        r = sorted(idx, key=lambda i: (abs(i - c), i))
        return [r.index(i) for i in idx]
    if order == "edges":
        c = (n - 1) / 2
        r = sorted(idx, key=lambda i: (-abs(i - c), i))
        return [r.index(i) for i in idx]
    if order == "random":
        h = [((i + 1) * 2654435761 + seed * 40503) & 0xFFFF for i in idx]
        r = sorted(idx, key=lambda i: h[i])
        return [r.index(i) for i in idx]
    return idx


def _split(a, b, step=SAMPLE):
    """[(a, b)] cut into windows that compile.sample_times samples at <= step (it takes <= 10 samples per window)."""
    out, span = [], step * 10
    while b - a > 1e-6:
        out.append((a, min(b, a + span)))
        a += span
    return out


class PageFx:
    """Per-page animation state for compile_page (one per page that has anim.fx)."""

    def __init__(self, roles, boxes, P0, P1, mode, H, k, seed=0):
        self.roles = roles
        self.P0, self.P1, self.H, self.k, self.mode = P0, P1, H, k, mode
        self.n = n = len(boxes)
        self.size = [max(1.0, b.size) for b in boxes]
        self.psize = sorted(self.size)[n // 2] if n else 1.0
        self.wstart = [b.w.start for b in boxes]
        self.act = None
        self.hl = mode in ("karaoke", "word", "reveal")
        self.t = {}                      # role -> (dur, stagger, ranks)
        span = max(0.05, P1 - P0)
        for role, sp in roles.items():
            dur, stg = sp["dur"], sp["stagger"] if sp["unit"] == "word" else 0.0
            ranks = _ranks(n, sp["order"], seed)
            if role in ("in", "out"):
                avail = span * (0.6 if role == "in" else 0.4)
                total = dur + stg * max(0, n - 1)
                if total > avail:
                    f = avail / total
                    dur, stg = dur * f, stg * f
            self.t[role] = (max(0.02, dur), stg, ranks)
        self.page_roles = {r for r, sp in roles.items() if sp["unit"] == "page" and r in ("in", "out")}
        self.pushes = "active" in roles and any(k["v"] not in (None, 1.0) for k in roles["active"]["tracks"].get("scale", []))

    # classic page animations are replaced by fx in / out
    def override(self, a_in, a_out):
        return ("none" if "in" in self.roles else a_in), ("none" if "out" in self.roles else a_out)

    def bind(self, act):
        self.act = act

    # ---- timing per role
    def _start(self, role, i):
        dur, stg, ranks = self.t[role]
        if role == "in":
            if self.mode == "reveal" and role not in self.page_roles:
                return max(self.P0, self.wstart[i])
            return self.P0 + (0 if role in self.page_roles else ranks[i] * stg)
        if role == "out":
            last = max(ranks) if ranks else 0
            total = dur + stg * last
            return self.P1 - total + (0 if role in self.page_roles else ranks[i] * stg)
        return self.P0

    def _role_vals(self, role, i, t, base=None):
        sp = self.roles[role]
        dur, stg, ranks = self.t[role]
        if role == "in":
            s = self._start(role, i)
            return values_at(sp, min(1.0, (t - s) / dur), base)
        if role == "out":
            s = self._start(role, i)
            if t < s:
                return None
            return values_at(sp, (t - s) / dur, base)
        if role == "loop":
            ph = (t - self.P0) / dur + (ranks[i] * stg / dur if stg else 0.0)
            return values_at(sp, ph - math.floor(ph), base)
        if role == "active":
            if not self.hl or self.act is None:
                return None
            a0, a1 = self.act[i]
            if t < a0:
                return None
            if t <= a1:
                return values_at(sp, (t - a0) / dur, base)
            rel = sp["release"]
            v = values_at(sp, min(1.0, (a1 - a0) / dur), base)
            if rel <= 0 or t >= a1 + rel:
                return None
            f = ease("out", (t - a1) / rel)
            out = {}
            for kk, vv in v.items():
                if kk == "color":
                    out[kk] = mix(vv, base, f) if (vv and base) else (None if f > 0.5 else vv)
                else:
                    out[kk] = vv + (NEUTRAL[kk] - vv) * f
            return out
        return None

    def _combine(self, vals_list, size):
        d = {"s": 1.0, "dx": 0.0, "dy": 0.0, "a": 1.0, "blur": 0.0, "rot": 0.0}
        for v in vals_list:
            if not v:
                continue
            d["s"] *= max(0.0, v.get("scale", 1.0))
            d["a"] *= max(0.0, min(1.0, v.get("opacity", 1.0)))
            d["dx"] += v.get("x", 0.0) / 100 * size / self.H
            d["dy"] += v.get("y", 0.0) / 100 * size / self.H
            d["blur"] += max(0.0, v.get("blur", 0.0))
            d["rot"] -= v.get("rot", 0.0)          # spec: + = clockwise (CSS); \frz: + = counter-clockwise
        return d

    # ---- hooks used by compile_page
    def page(self, t):
        """Delta for the whole page (unit: page roles): words, boxes, pills and emoji move together."""
        if not self.page_roles:
            return {}
        return self._combine([self._role_vals(r, 0, t) for r in self.page_roles], self.psize)

    def box(self, t):
        """Extra delta for boxes / pills / emoji when the words animate one by one: they fade with the page."""
        a = 1.0
        for r in ("in", "out"):
            if r in self.roles and r not in self.page_roles:
                if r == "in":
                    vals = [self._role_vals(r, i, t) for i in range(self.n)]
                    a *= max([min(1.0, (v or {}).get("opacity", 1.0)) for v in vals] or [1.0])
                else:
                    v = self._role_vals(r, 0, t) or {}
                    a *= min(1.0, v.get("opacity", 1.0))
        return {"a": a} if a < 1.0 else {}

    def word(self, i, t):
        """Delta for word i (in / out per word, active, loop) in compile units (dx/dy = fraction of H)."""
        roles = [r for r in self.roles if r not in self.page_roles]
        return self._combine([self._role_vals(r, i, t) for r in roles], self.size[i])

    def scale(self, i, t):
        """Active-word scale (neighbours make room for it, like highlight.scale)."""
        if "active" not in self.roles:
            return 1.0
        v = self._role_vals("active", i, t) or {}
        return max(0.0, v.get("scale", 1.0))

    def color(self, i, t, base):
        """Animated text colour (or base)."""
        col = None
        for r in ("loop", "in", "out", "active"):
            sp = self.roles.get(r)
            if sp and "color" in sp["tracks"]:
                v = self._role_vals(r, 0 if r in self.page_roles else i, t, base)
                if v and v.get("color"):
                    col = v["color"]
        return col or base

    def spacing(self, i, t):
        """Extra letter spacing (px @1080)."""
        sp = 0.0
        for r in self.roles:
            if "spacing" in self.roles[r]["tracks"]:
                v = self._role_vals(r, 0 if r in self.page_roles else i, t)
                if v:
                    sp += v.get("spacing", 0.0)
        return sp

    def has_color(self):
        return any("color" in sp["tracks"] for sp in self.roles.values())

    def has_spacing(self):
        return any("spacing" in sp["tracks"] for sp in self.roles.values())

    def windows(self, i=None):
        """Animated intervals to sample (word i, or the page-level roles when i is None)."""
        out = []
        for role, sp in self.roles.items():
            if i is None and role not in self.page_roles and role != "in" and role != "out":
                continue
            dur, stg, ranks = self.t[role]
            j = 0 if (i is None or role in self.page_roles) else i
            if role in ("in", "out"):
                if i is None and role not in self.page_roles:
                    s0 = self._start(role, 0) if role == "in" else self.P1 - dur - stg * (max(ranks) if ranks else 0)
                    out += _split(s0, s0 + dur + stg * max(0, self.n - 1))
                else:
                    s = self._start(role, j)
                    out += _split(s, s + dur)
            elif role == "active" and i is not None and self.hl and self.act is not None:
                a0, a1 = self.act[i]
                out += _split(a0, min(a1, a0 + dur) + 1e-3)
                out += _split(a1, a1 + sp["release"] + 1e-3)
            elif role == "loop" and i is not None:
                out += _split(self.P0, self.P1)
        return out

    def neighbour_windows(self, i):
        """Windows of the active-scale changes of word i (neighbours re-sample while it grows)."""
        if not self.pushes or self.act is None or not self.hl:
            return []
        dur = self.t["active"][0]
        a0, a1 = self.act[i]
        return _split(a0, min(a1, a0 + dur) + 1e-3) + _split(a1, a1 + self.roles["active"]["release"] + 1e-3)


def page_fx(tpl, boxes, P0, P1, W, H, seed=0):
    """PageFx for a resolved template with anim.fx, else None (classic path, byte-identical)."""
    fx = (tpl.get("anim") or {}).get("fx")
    if not fx:
        return None
    roles = resolve_fx(fx)
    if not roles or not boxes:
        return None
    return PageFx(roles, boxes, P0, P1, tpl["timing"]["mode"], H, min(W, H) / 1080.0, seed)


# ---------------------------------------------------------------- user animations (%APPDATA%\Klipora\caption_anims.json)

def user_path():
    from ..util import appdata_dir
    return appdata_dir() / "caption_anims.json"


def user_anims():
    try:
        data = json.loads(user_path().read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {}
    items = data.get("anims") if isinstance(data, dict) else None
    out = {}
    for it in items or []:
        if isinstance(it, dict) and it.get("id") and it.get("tracks"):
            out[str(it["id"])] = it
    return out


def slug(name):
    s = re.sub(r"[^a-z0-9]+", "_", str(name or "").lower()).strip("_")
    return s[:40] or "animasi"


def save_user(spec, name=None, aid=None):
    """Validate + store a custom animation. Returns the stored spec (id "u_<slug>")."""
    from ..util import EngineError, write_json
    sp = norm_spec(dict(spec or {}, preset=None) if isinstance(spec, dict) else spec)
    if not sp:
        raise EngineError("BAD_ANIM", tr("cap.anim.errEmpty"), tr("cap.anim.errEmptyHint"))
    label = str(name or sp["label"] or tr("cap.anim.mine"))[:60]
    aid = str(aid or "")
    if not aid.startswith("u_"):
        aid = "u_" + slug(aid or label)
    sp.update({"id": aid, "label": label, "builtin": False, "user": True})
    allu = user_anims()
    allu[aid] = sp
    write_json(user_path(), {"v": 1, "kind": "autocut.caption_anims", "anims": list(allu.values())}, indent=1)
    return sp


def delete_user(aid):
    from ..util import write_json
    allu = user_anims()
    if aid not in allu:
        return False
    allu.pop(aid)
    write_json(user_path(), {"v": 1, "kind": "autocut.caption_anims", "anims": list(allu.values())}, indent=1)
    return True


def catalog():
    """Everything the Animasi tab offers."""
    users = [dict(norm_spec(s) or {}, user=True, builtin=False) for s in user_anims().values()]
    return {"groups": [{"id": g, "label": tr(lab)} for g, lab in GROUPS],
            "presets": [dict(p, label=tl(p["label"]), desc=tl(p.get("desc") or "")) for p in PRESETS_LIST],
            "user": [u for u in users if u.get("tracks")],
            "tracks": [dict(t, label=tl(t["label"]), **({"help": tl(t["help"])} if t.get("help") else {}))
                       for t in TRACK_INFO],
            "easings": [{"id": e, "label": tr(lab)} for e, lab in EASINGS],
            "orders": [{"id": o, "label": tr(lab)} for o, lab in ORDERS],
            "units": [{"id": u, "label": tr("cap.anim.unit." + u)} for u in ("word", "page")],
            "defaults": DEFAULT_DUR, "path": str(user_path())}


# ---------------------------------------------------------------- preview sprite sheets

SPRITE_V = 4
DEMO = (("Halo", 0.35, 0.75), ("teman", 0.75, 1.15), ("semua!", 1.15, 1.7))
SPRITE_BG = "0x1B2130"


def demo_template(W, H, extra=None):
    """Neutral demo look for the sprite previews (white bold text, black outline, yellow active word)."""
    from . import templates as TP
    from .layout import deep_merge
    look = {"font": {"family": "Montserrat Black", "size": 370, "uppercase": False},
            "style": {"fill": "#FFFFFF", "outline": 9, "outline_color": "#000000", "shadow": 0},
            "layout": {"y": 50, "x": 50, "max_words": 3, "max_lines": 1, "width": 96, "safe": "none"},
            "timing": {"mode": "karaoke", "lead": 0, "hold": 0, "fill_gaps": False, "min_dur": 0.2},
            "highlight": {"color": "#FFE600", "scale": 1.0, "pill": None, "underline": None, "past_color": None},
            "box": {"enabled": False}, "emoji": {"enabled": False},
            "anim": {"in": "none", "out": "none", "word_in": "none"}}
    return deep_merge(deep_merge(TP.DEFAULTS, look), extra or {})


def sprite_window(spec):
    """(t0, t1) of the demo timeline that shows a role (page = 0.3 .. 2.3 s)."""
    P0, P1 = 0.3, 2.3
    g, dur, stg = spec["group"], spec["dur"], spec["stagger"] if spec["unit"] == "word" else 0
    if g == "in":
        return P0 - 0.04, P0 + min(1.3, max(0.45, dur + stg * 2 + 0.15))
    if g == "out":
        span = min(1.0, max(0.4, dur + stg * 2 + 0.12))
        return P1 - span, P1 + 0.04
    if g == "loop":
        return P0 + 0.2, P0 + 0.2 + min(2.0, max(0.24, dur * (1 if dur >= 0.6 else 2)))
    return 0.34, 1.95


def sprite(spec, frames=16, size=(176, 64), force=False):
    """Frame strip PNG (frames x 1 cells of size) of one animation on the demo phrase. Cached. -> dict"""
    import contextlib
    from ..util import data_hash, local_dir
    from . import compile as C
    from . import layout as L
    from .preview import _ff_cwd
    from .render import fonts_rel
    sp = norm_spec(spec)
    if not sp:
        return None
    frames = max(2, min(40, int(frames)))
    cw, ch = int(size[0]) // 2 * 2, int(size[1]) // 2 * 2
    W, H = 1100, int(round(1100 * ch / cw / 2)) * 2
    key = data_hash([SPRITE_V, {k: v for k, v in sp.items() if k not in ("label", "desc", "builtin", "user")},
                     frames, cw, ch])
    d = local_dir("captions", "anim_sprites")
    out = d / f"{sp['id'][:24]}_{key[:12]}.png"
    t0, t1 = sprite_window(sp)
    info = {"png": str(out), "frames": frames, "dur": round(t1 - t0, 3), "w": cw, "h": ch, "group": sp["group"]}
    if out.is_file() and not force:
        return info
    tpl = L.prep_template(demo_template(W, H, {"anim": {"fx": {sp["group"]: sp}}}))
    words = [L.RWord(text=t, start=a, end=b, id=f"demo:{i}", raw=t) for i, (t, a, b) in enumerate(DEMO)]
    cap = L.Caption([L.Line(words)], 0.3, 2.3, id="p:demo", index=0)
    work = d / "work"
    work.mkdir(exist_ok=True)
    ass = work / f"{key[:16]}.ass"
    C.write_ass(ass, [(cap, tpl)], W, H)
    fps = frames / max(0.05, (t1 - t0))
    tmp = work / f"{key[:16]}.png"
    _ff_cwd(["-f", "lavfi", "-i", f"color=c={SPRITE_BG}:s={W}x{H}:r={fps:.5f}:d={t1 - t0 + 1:.3f}",
             "-vf", f"setpts=PTS+{t0:.3f}/TB,ass='{ass.name}':fontsdir='{fonts_rel(work, ass)}':shaping=complex,"
                    f"scale={cw}:{ch}:flags=area,tile={frames}x1",
             "-frames:v", "1", "-y", tmp.name], work)
    tmp.replace(out)
    with contextlib.suppress(OSError):
        ass.unlink()
    return info


def sprites(items, frames=16, size=(176, 64), force=False, threads=6):
    """{key: spec} -> ({key: info}, {key: error})."""
    from concurrent.futures import ThreadPoolExecutor
    res, errs = {}, {}

    def one(kv):
        k, v = kv
        try:
            return k, sprite(v, frames, size, force), None
        except Exception as e:  # noqa: BLE001 - one bad spec must not hide the others
            return k, None, str(e)[:300]
    with ThreadPoolExecutor(max_workers=max(1, threads)) as ex:
        for k, info, err in ex.map(one, list(items.items())):
            if info:
                res[k] = info
            else:
                errs[k] = err or tr("cap.anim.errSprite")
    return res, errs


def strip_png(spec, out, frames=12, size=(320, 116)):
    """Frame strip of one animation at a bigger size (visual checks / docs). -> Path"""
    info = sprite(spec, frames, size, force=True)
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    Path(info["png"]).replace(out)
    return Path(out)
