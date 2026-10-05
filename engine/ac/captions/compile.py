"""Keyframes -> ASS compiler (port of proto/caption_render/render_ass.py, verified frame by frame).

Every visual element (word text, soft-shadow layer, glow layer, karaoke sweep, pill/underline, line/page/word box,
emoji colour layers) is an Elem whose state(t) returns x, y, s, sx, sy, a, blur, rot, clip, tags. emit() samples
breakpoints (page in/out windows, word activation, neighbour pops, reveal steps; <= 35 ms pieces rounded to ASS
centiseconds) and writes one Dialogue per linear piece:
    {\\an5\\move(..)\\org(..)<static>\\1c..\\fscx..\\blur..\\frz..\\clip(..)\\t(..)\\fade(..)[\\p1]}TEXT
Every event has \\pos/\\move, so libass collision handling never moves anything.

Layers (a page uses the "classic" numbers unless it needs one of the Gaya Pro layers; pages never overlap in
time, so the two schemes never mix on screen and classic templates compile byte-identical to v1):
    classic: 0 box, 1 pill/underline, 2 glow, 3 soft shadow, 4 text (+ gradient bands, sweep), 5 emoji
    pro:     0 box shadow, 1 box (+ gradient bands), 2 pill, 3 glow, 4 soft shadow, 5 3D extrude / long shadow,
             6 outer stroke (outline2), 7 text (+ gradient bands, sweep), 8 emoji

Gaya Pro effects (docs/CAPTIONS_API.md 6.4), all libass-only, no second pass:
  * gradient text = the word drawn in the first stop colour + N-1 copies clipped to horizontal (vertical
    gradient) or vertical (horizontal gradient) bands in interpolated colours (research 4: \\1vc is not in libass);
  * outer stroke = a copy under the text with a fatter \\bord in the stroke colour; 3D extrude / long shadow =
    stacked offset copies (far -> near) with \\bord >= half the step so the stack is gap-free;
  * page tilt = word centres rotated about the page centre + \\frz on every element; active-word wiggle and
    per-word rotation = \\frz around the word; skew = \\fax;
  * box shapes (rounded, rough marker, banner, ribbon, sticker) are \\p1 drawings regenerated per piece, border =
    \\bord on the drawing (transparent fill = outline-only box), box shadow = offset/blurred copy, gradient box =
    clipped bands.

Band rendering: compile with an origin (band x, y) so the ASS canvas is just the caption band (PlayRes = band
size); layout still uses the full frame. caption_band() tracks the bbox of every emitted piece.
"""
from __future__ import annotations

import contextlib
import contextvars
import math
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from . import anim as AFX
from . import emoji as emo
from .layout import BOX_SHAPES, REF, LabelMap, _num, layout, outline2_px

# ---------------------------------------------------------------- colour / ass helpers
_HEX = re.compile(r"^#?([0-9a-fA-F]{6})([0-9a-fA-F]{2})?$")


def parse_color(hexstr, default="#FFFFFF"):
    """'#RRGGBB' / '#RRGGBBAA' (AA = opacity) -> (r, g, b, opacity 0..255)."""
    m = _HEX.match(str(hexstr or "").strip())
    if not m:
        m = _HEX.match(default)
    h = m.group(1)
    a = int(m.group(2), 16) if m.group(2) else 255
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), a


def is_color(v):
    return isinstance(v, str) and bool(_HEX.match(v.strip()))


def with_opacity(hexstr, opacity):
    """Colour with its alpha replaced by opacity 0..1 (None keeps the colour's own alpha)."""
    if opacity is None:
        return hexstr
    r, g, b, _ = parse_color(hexstr)
    return f"#{r:02X}{g:02X}{b:02X}{max(0, min(255, round(float(opacity) * 255))):02X}"


def scale_alpha(hexstr, f):
    """Colour with its alpha multiplied by f (0..1)."""
    r, g, b, a = parse_color(hexstr)
    return f"#{r:02X}{g:02X}{b:02X}{max(0, min(255, round(a * max(0.0, min(1.0, f))))):02X}"


def mix(c1, c2, f):
    """Linear mix of two '#RRGGBB[AA]' colours (f = 0 -> c1)."""
    a, b = parse_color(c1), parse_color(c2)
    v = [round(x + (y - x) * f) for x, y in zip(a, b)]
    return "#{:02X}{:02X}{:02X}{:02X}".format(*v)


def ramp(colors, f):
    """Colour at f (0..1) along 2-3 gradient stops."""
    n = len(colors) - 1
    if n <= 0:
        return colors[0]
    x = max(0.0, min(1.0, f)) * n
    i = min(int(x), n - 1)
    return mix(colors[i], colors[i + 1], x - i)


@lru_cache(maxsize=4096)
def _ass_color(hexstr):
    r, g, b, a = parse_color(hexstr)
    return f"&H{b:02X}{g:02X}{r:02X}&", f"&H{255 - a:02X}&"


def ass_color(hexstr):
    """-> ('&HBBGGRR&', '&HAA&' where 00 = opaque). Cached: called for every sampled piece of every element."""
    try:
        return _ass_color(hexstr)
    except TypeError:             # unhashable junk from a hand-edited template: parse without the cache
        return _ass_color.__wrapped__(hexstr)


def ass_time(t):
    cs = max(0, int(round(t * 100)))
    return f"{cs // 360000}:{cs // 6000 % 60:02}:{cs // 100 % 60:02}.{cs % 100:02}"


def ass_escape(text):
    return text.replace("\\", "＼").replace("{", "｛").replace("}", "｝")


def rrect(w, h, r):
    """Rounded rectangle as an ASS drawing, origin top-left (bezier corners, kappa 0.5523)."""
    w, h = max(w, 0.5), max(h, 0.5)
    r = max(0.0, min(r, w / 2, h / 2))
    k = r * 0.4477
    f = lambda v: f"{v:.1f}"  # noqa: E731
    if r < 0.5:
        return f"m 0 0 l {f(w)} 0 {f(w)} {f(h)} 0 {f(h)}"
    return (f"m {f(r)} 0 l {f(w - r)} 0 b {f(w - k)} 0 {f(w)} {f(k)} {f(w)} {f(r)} "
            f"l {f(w)} {f(h - r)} b {f(w)} {f(h - k)} {f(w - k)} {f(h)} {f(w - r)} {f(h)} "
            f"l {f(r)} {f(h)} b {f(k)} {f(h)} 0 {f(h - k)} 0 {f(h - r)} "
            f"l 0 {f(r)} b 0 {f(k)} {f(k)} 0 {f(r)} 0")


def _jit(seed, side, i):
    """Deterministic 0..1 jitter per (seed, edge, point): stable while a box grows."""
    v = (int(seed) * 73856093) ^ (side * 19349663) ^ ((i + 1) * 83492791)
    return (v * 2654435761 & 0xFFFFFFFF) / 0xFFFFFFFF


def marker_path(w, h, seed=0):
    """Rough brush / highlighter-marker rectangle: ragged top and bottom edges, slanted torn ends. Points sit at a
    fixed px spacing and jitter by index, so a growing box keeps its edge (no boiling). Anchored with
    'm 0 0 m w h' so the drawing bbox is exactly w x h (\\an5 centring)."""
    w, h = max(w, 2.0), max(h, 2.0)
    amp = max(1.0, h * 0.09)
    step = max(10.0, h * 0.45)
    f = lambda v: f"{v:.1f}"  # noqa: E731
    xs = [0.0]
    while xs[-1] + step < w - step * 0.5:
        xs.append(xs[-1] + step)
    xs.append(w)
    top = [(min(max(x, amp * 0.6), w - amp * 0.4), amp * _jit(seed, 1, i)) for i, x in enumerate(xs)]
    bot = [(min(max(x, amp * 0.4), w - amp * 0.6), h - amp * _jit(seed, 2, i)) for i, x in enumerate(xs)][::-1]
    right = [(w - amp * (0.2 + _jit(seed, 3, 0)), h * 0.36), (w - amp * 0.6 * _jit(seed, 3, 1), h * 0.7)]
    left = [(amp * (0.2 + _jit(seed, 4, 0)), h * 0.64), (amp * 0.6 * _jit(seed, 4, 1), h * 0.3)]
    pts = top + right + bot + left
    return (f"m 0 0 m {f(w)} {f(h)} m {f(pts[0][0])} {f(pts[0][1])} l "
            + " ".join(f"{f(x)} {f(y)}" for x, y in pts[1:]))


def shape_path(kind, w, h, r, seed=0):
    """Box / pill outline as an ASS drawing (origin top-left, bbox w x h): rect (rounded by r), sticker (rounded
    rect, rotated by the caller), marker (rough brush), banner (slanted ends), ribbon (notched ends)."""
    if kind == "marker":
        return marker_path(w, h, seed)
    w, h = max(w, 0.5), max(h, 0.5)
    f = lambda v: f"{v:.1f}"  # noqa: E731
    if kind == "banner":
        s = min(0.32 * h, w * 0.3)
        return f"m {f(s)} 0 l {f(w)} 0 {f(w - s)} {f(h)} 0 {f(h)}"
    if kind == "ribbon":
        n = min(0.32 * h, w * 0.3)
        return f"m 0 0 l {f(w)} 0 {f(w - n)} {f(h / 2)} {f(w)} {f(h)} 0 {f(h)} {f(n)} {f(h / 2)}"
    return rrect(w, h, r)


# ---------------------------------------------------------------- animation curves

def clamp01(v):
    return 0.0 if v < 0 else 1.0 if v > 1 else v


def e_out_cubic(p):
    return 1 - (1 - p) ** 3


def e_in_cubic(p):
    return p ** 3


def e_out_back(p, s=1.9):
    p -= 1
    return 1 + (s + 1) * p ** 3 + s * p ** 2


def e_in_back(p, s=1.7):
    return (s + 1) * p ** 3 - s * p ** 2


def e_out_bounce(p):
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


# deltas: s (scale mult), dx/dy (fraction of H), a (alpha mult 0..1), blur (px @1080), rot (deg)
IN = {
    "none": lambda p: {},
    "fade": lambda p: {"a": p},
    "pop": lambda p: {"s": max(0.0, e_out_back(p)), "a": clamp01(p * 3)},
    "scale": lambda p: {"s": 0.7 + 0.3 * e_out_cubic(p), "a": p},
    "zoom": lambda p: {"s": 1.7 - 0.7 * e_out_cubic(p), "a": clamp01(p * 2)},
    "slide_up": lambda p: {"dy": 0.045 * (1 - e_out_cubic(p)), "a": clamp01(p * 1.5)},
    "slide_down": lambda p: {"dy": -0.045 * (1 - e_out_cubic(p)), "a": clamp01(p * 1.5)},
    "slide_left": lambda p: {"dx": 0.08 * (1 - e_out_cubic(p)), "a": clamp01(p * 1.5)},
    "slide_right": lambda p: {"dx": -0.08 * (1 - e_out_cubic(p)), "a": clamp01(p * 1.5)},
    "bounce": lambda p: {"dy": -0.07 * (1 - e_out_bounce(p)), "a": clamp01(p * 4)},
    "blur": lambda p: {"blur": 16 * (1 - e_out_cubic(p)), "a": clamp01(p * 1.3), "s": 1.06 - 0.06 * e_out_cubic(p)},
    "zoom_blur": lambda p: {"blur": 14 * (1 - e_out_cubic(p)), "a": clamp01(p * 1.5), "s": 1.4 - 0.4 * e_out_cubic(p)},
    "rotate": lambda p: {"rot": -14 * (1 - e_out_back(p)), "s": max(0.0, e_out_back(p)), "a": clamp01(p * 3)},
    "drop": lambda p: {"s": 1.0 + 0.6 * (1 - e_out_bounce(p)), "a": clamp01(p * 3)},
    "typewriter": lambda p: {},  # handled with \clip (typewriter_clip)
}
OUT = {
    "none": lambda p: {},
    "fade": lambda p: {"a": 1 - p},
    "pop": lambda p: {"s": max(0.0, 1 - e_in_back(p)), "a": 1 - clamp01((p - 0.6) / 0.4)},
    "scale": lambda p: {"s": 1 - 0.25 * e_in_cubic(p), "a": 1 - p},
    "zoom": lambda p: {"s": 1 + 0.35 * e_in_cubic(p), "a": 1 - p},
    "slide_up": lambda p: {"dy": -0.04 * e_in_cubic(p), "a": 1 - p},
    "slide_down": lambda p: {"dy": 0.04 * e_in_cubic(p), "a": 1 - p},
    "blur": lambda p: {"blur": 16 * p, "a": 1 - p},
}
ALIASES_IN = {"zoom_out": "zoom", "zoom_in": "scale", "slide": "slide_up", "type": "typewriter"}
ALIASES_OUT = {"slide": "slide_down", "zoom_out": "scale", "zoom_in": "zoom"}
ANIM_LABELS = LabelMap("cap.animName.", {  # editor UI labels: cap.animName.* (values = Indonesian fallbacks)
    "none": "Tanpa", "fade": "Pudar", "pop": "Pop", "scale": "Membesar", "zoom": "Zoom", "slide_up": "Geser naik",  # i18n-ignore
    "slide_down": "Geser turun", "slide_left": "Geser kiri", "slide_right": "Geser kanan", "bounce": "Pantul",  # i18n-ignore
    "blur": "Blur", "zoom_blur": "Zoom + blur", "rotate": "Putar", "drop": "Jatuh", "typewriter": "Mesin ketik"})  # i18n-ignore
WORD_IN = ("none", "fade", "pop", "scale", "zoom", "slide_up", "slide_down", "bounce", "blur", "drop", "typewriter")


def anim_name(kind, table, aliases):
    k = aliases.get(kind, kind)
    return k if k in table else "none"


def combine(*ds):
    out = {"s": 1.0, "dx": 0.0, "dy": 0.0, "a": 1.0, "blur": 0.0, "rot": 0.0}
    for d in ds:
        out["s"] *= d.get("s", 1.0)
        out["a"] *= d.get("a", 1.0)
        for kk in ("dx", "dy", "blur", "rot"):
            out[kk] += d.get(kk, 0.0)
    return out


def anim_at(kind, table, p):
    return table.get(kind, table["none"])(clamp01(p))


# ---------------------------------------------------------------- elements and emission

@dataclass
class Elem:
    layer: int
    t0: float
    t1: float
    body: str
    static: str
    state: object            # f(t) -> dict
    windows: list            # [(a, b)] animated intervals to sample
    steps: list              # discrete change times
    drawing: bool = False
    org: tuple | None = None
    half: tuple | None = None  # (half w, half h) at scale 1 incl. outline/blur, for band tracking
    merge: bool = False        # join consecutive pieces that one linear piece reproduces (Pro copies: fewer events)


_CTX = contextvars.ContextVar("ass_ctx", default=None)


def _track(S, half, org=None):
    ctx = _CTX.get()
    if ctx is None or half is None:
        return
    hw, hh = (S["bw"] / 2, S["bh"] / 2) if "bw" in S else half
    sx, sy = S["s"] * S.get("sx", 1), S["s"] * S.get("sy", 1)
    x, y = S["x"], S["y"]
    if S["rot"]:
        if org is not None:      # rotated about another point (page rotate-in): the centre moves too
            th = math.radians(S["rot"])
            dx, dy = x - org[0], y - org[1]
            x, y = org[0] + dx * math.cos(th) + dy * math.sin(th), org[1] - dx * math.sin(th) + dy * math.cos(th)
        c, s = abs(math.cos(math.radians(S["rot"]))), abs(math.sin(math.radians(S["rot"])))
        hw, hh = hw * sx * c + hh * sy * s, hw * sx * s + hh * sy * c    # bbox of the turned rectangle
        sx = sy = 1.0
    m = 3 * S["blur"] + 2 + S.get("m", 0.0)
    bb = ctx["bbox"]
    bb[0], bb[1] = min(bb[0], x - hw * sx - m), min(bb[1], y - hh * sy - m)
    bb[2], bb[3] = max(bb[2], x + hw * sx + m), max(bb[3], y + hh * sy + m)


def r2(t):
    return round(t * 100) / 100


def sample_times(e, step=0.035):
    ts = {r2(e.t0), r2(e.t1)}
    for t in e.steps:
        if e.t0 < t < e.t1:
            ts.add(r2(t))
    for a, b in e.windows:
        a, b = max(a, e.t0), min(b, e.t1)
        if b <= a:
            continue
        n = max(1, min(10, math.ceil((b - a) / step)))
        for i in range(n + 1):
            ts.add(r2(a + (b - a) * i / n))
    return sorted(t for t in ts if r2(e.t0) <= t <= r2(e.t1))


_LIN = (("x", 0.04), ("y", 0.04), ("s", 0.0004), ("a", 0.003), ("blur", 0.04), ("rot", 0.008), ("sx", 0.0004),
        ("sy", 0.0004))


def _linear(A, B, f, M):
    """True when state M equals the linear interpolation A -> B at fraction f (what \\move, \\t and \\fade draw)
    and the colour tags are the same."""
    if M is None or M.get("tags") != A.get("tags") or M.get("xtags") != A.get("xtags"):
        return False
    for key, tol in _LIN:
        if key in A and abs(A[key] + (B[key] - A[key]) * f - M.get(key, A[key])) > tol:
            return False
    ca, cb, cm = A.get("clip"), B.get("clip"), M.get("clip")
    if ca or cb or cm:
        if not (ca and cb and cm) or any(abs(x + (y - x) * f - z) > 0.04 for x, y, z in zip(ca, cb, cm)):
            return False
    return True


def merge_times(e, ts):
    """Drop sample times inside spans that one linear piece reproduces exactly (e.g. a linear fade). States on
    both sides of every sample time are evaluated once (same convention as emit: piece start + 1e-4, end - 1e-4)."""
    n = len(ts)
    lo = [e.state(t - 1e-4) for t in ts]
    hi = [e.state(t + 1e-4) for t in ts]
    out, i = [ts[0]], 0
    while i < n - 1:
        j = i + 1
        A = hi[i]
        while (j + 1 < n and A is not None and lo[j + 1] is not None and A.get("tags") == lo[j + 1].get("tags")
               and A.get("xtags") == lo[j + 1].get("xtags")):
            B, ta, tb = lo[j + 1], ts[i], ts[j + 1]
            if not all(_linear(A, B, (ts[m] - ta) / (tb - ta), M) for m in range(i + 1, j + 2) if m < j + 1
                       for M in (lo[m], hi[m])):
                break
            j += 1
        out.append(ts[j])
        i = j
    return out


def fmt(v):
    s = f"{v:.2f}".rstrip("0").rstrip(".")
    return s if s not in ("-0", "") else "0"


def emit(e):
    """Elem -> ASS Dialogue lines (one per linear piece)."""
    out = []
    ts = sample_times(e)
    if e.merge and len(ts) > 2:
        ts = merge_times(e, ts)
    ctx = _CTX.get()
    gx, gy = ctx["origin"] if ctx else (0.0, 0.0)
    for ta, tb in zip(ts, ts[1:]):
        if tb - ta < 0.005:
            continue
        A, B, M = e.state(ta + 1e-4), e.state(tb - 1e-4), e.state((ta + tb) / 2)
        if M is None or (A["a"] <= 0.003 and B["a"] <= 0.003):
            continue
        dur = int(round((tb - ta) * 1000))
        _track(A, e.half, e.org)
        _track(B, e.half, e.org)
        tags = [r"\an5"]
        if abs(A["x"] - B["x"]) > 0.05 or abs(A["y"] - B["y"]) > 0.05:
            tags.append(rf"\move({fmt(A['x'] - gx)},{fmt(A['y'] - gy)},{fmt(B['x'] - gx)},{fmt(B['y'] - gy)})")
        else:
            tags.append(rf"\pos({fmt(A['x'] - gx)},{fmt(A['y'] - gy)})")
        if e.org or (A["rot"] or B["rot"]):
            ox, oy = e.org or (A["x"], A["y"])
            tags.append(rf"\org({fmt(ox - gx)},{fmt(oy - gy)})")
        tags.append(e.static)
        if M.get("tags"):
            tags.append(M["tags"])
        if M.get("xtags"):        # anim.fx letter spacing (after the colour tags, overrides the static \fsp)
            tags.append(M["xtags"])
        body = e.body
        if "bw" in A:  # resizable drawing: exact shape at piece start, linear stretch to its end size
            body = shape_path(A.get("shape", "rect"), A["bw"], A["bh"], A["br"], A.get("seed", 0))
            A, B = dict(A, sx=1.0, sy=1.0), dict(B, sx=B["bw"] / max(A["bw"], 0.5), sy=B["bh"] / max(A["bh"], 0.5))
        sxa, sya = A["s"] * A.get("sx", 1) * 100, A["s"] * A.get("sy", 1) * 100
        sxb, syb = B["s"] * B.get("sx", 1) * 100, B["s"] * B.get("sy", 1) * 100
        tr = []
        if abs(sxa - 100) > 0.05 or abs(sya - 100) > 0.05 or abs(sxb - sxa) > 0.05 or abs(syb - sya) > 0.05:
            tags.append(rf"\fscx{fmt(sxa)}\fscy{fmt(sya)}")
            if abs(sxb - sxa) > 0.05 or abs(syb - sya) > 0.05:
                tr.append(rf"\fscx{fmt(sxb)}\fscy{fmt(syb)}")
        ba, bb = A["blur"], B["blur"]
        if ba > 0.05 or bb > 0.05:
            tags.append(rf"\blur{fmt(ba)}")
            if abs(bb - ba) > 0.05:
                tr.append(rf"\blur{fmt(bb)}")
        if A["rot"] or B["rot"]:
            tags.append(rf"\frz{fmt(A['rot'])}")
            if abs(B["rot"] - A["rot"]) > 0.01:
                tr.append(rf"\frz{fmt(B['rot'])}")
        if M.get("clip"):
            ca, cb = A.get("clip") or M["clip"], B.get("clip") or M["clip"]
            shift = lambda c: (c[0] - gx, c[1] - gy, c[2] - gx, c[3] - gy)  # noqa: E731
            tags.append(r"\clip(%s)" % ",".join(fmt(v) for v in shift(ca)))
            if ca != cb:
                tr.append(r"\clip(%s)" % ",".join(fmt(v) for v in shift(cb)))
        if tr:
            tags.append(r"\t(%s)" % "".join(tr))
        aa, ab = 255 - round(255 * clamp01(A["a"])), 255 - round(255 * clamp01(B["a"]))
        if aa or ab:
            tags.append(rf"\fade({aa},{aa},{ab},0,0,0,{dur})")
        if e.drawing:
            tags.append(r"\p1")
        out.append(f"Dialogue: {e.layer},{ass_time(ta)},{ass_time(tb)},D,,0,0,0,,{{{''.join(tags)}}}{body}")
    return out


# ---------------------------------------------------------------- Gaya Pro effects (per page)

LAYERS_CLASSIC = {"box_shadow": 0, "box": 0, "pill": 1, "glow": 2, "shadow": 3, "extrude": 3, "outline2": 3,
                  "text": 4, "emoji": 5}
LAYERS_PRO = {"box_shadow": 0, "box": 1, "pill": 2, "glow": 3, "shadow": 4, "extrude": 5, "outline2": 6,
              "text": 7, "emoji": 8}
BIG = 4000.0


def _on(v):
    """An optional effect object that is switched on (dict, not empty, not enabled: false)."""
    return v if isinstance(v, dict) and v and v.get("enabled", True) is not False else None


def _colors(v, n_min=2, n_max=3):
    """Valid gradient stop list or None."""
    if isinstance(v, dict):
        v = v.get("colors")
    if not isinstance(v, (list, tuple)):
        return None
    out = [c for c in v if is_color(c)][:n_max]
    return out if len(out) >= n_min else None


def alt(pi, idx):
    """+1 / -1 alternating per word (stable across previews and render chunks)."""
    return 1 if (pi + idx) % 2 == 0 else -1


def page_fx(tpl, pi, k, fit):
    """Resolved Gaya Pro effects of one page (all off for classic templates)."""
    st, lay, hl, box = tpl["style"], tpl["layout"], tpl["highlight"], tpl["box"]
    tilt = _num(lay.get("rotate"))
    jit = _num(lay.get("rotate_jitter"))
    if jit:
        tilt += jit if pi % 2 == 0 else -jit
    grad = _on(st.get("gradient"))
    gcols = _colors(grad) if grad else None
    o2 = outline2_px(tpl)
    ex = _on(st.get("extrude"))
    ls = _on(st.get("long_shadow"))
    bsh = _on(box.get("shadow")) if box.get("enabled") else None
    fx = {"tilt": tilt, "skew": _num(tpl["font"].get("skew")), "o2": o2 * k * fit,
          "o2_color": st.get("outline2_color") or "#FFFFFF",
          "oc_opacity": st.get("outline_opacity"),
          "grad": None, "extrude": ex if ex and _num(ex.get("depth")) > 0 else None,
          "long": ls if ls and _num(ls.get("length")) > 0 else None,
          "hl_glow": _on(hl.get("glow")) if _on(hl.get("glow")) and is_color(hl["glow"].get("color")) else None,
          "wiggle": _num(hl.get("wiggle")), "future_color": hl.get("future_color") if is_color(
              hl.get("future_color")) else None, "box_shadow": bsh}
    if gcols:
        fx["grad"] = {"colors": gcols, "dir": "horizontal" if grad.get("dir") == "horizontal" else "vertical",
                      "span": "line" if grad.get("span") == "line" else "word",
                      "bands": int(max(2, min(16, _num(grad.get("bands"), 6))))}
    fx["pro"] = bool(o2 or fx["extrude"] or fx["long"] or bsh)
    return fx


def _steps(v, cap):
    """User copy count (0 = automatic), clamped: every copy is one ASS event per word piece."""
    n = _num(v)
    return 0 if not math.isfinite(n) or n <= 0 else max(1, min(cap, int(n)))


def stack_copies(fx, k, fit, bord):
    """3D extrude / long shadow copies, far -> near: [(dx, dy, colour, border px)]."""
    out = []
    ls = fx["long"]
    if ls:
        length = _num(ls.get("length")) * k * fit
        ang = math.radians(_num(ls.get("angle"), 45.0))
        n = _steps(ls.get("steps"), 24) or max(3, min(16, math.ceil(length / 4.0)))
        rr = max(bord, length / n * 0.6)
        col = ls.get("color") if is_color(ls.get("color")) else "#00000099"
        for i in range(n, 0, -1):
            f = i / n
            c = scale_alpha(col, 1 - 0.85 * f) if ls.get("fade", True) else col
            out.append((math.cos(ang) * length * f, math.sin(ang) * length * f, c, rr))
    ex = fx["extrude"]
    if ex:
        depth = _num(ex.get("depth")) * k * fit
        ang = math.radians(_num(ex.get("angle"), 45.0))
        n = _steps(ex.get("steps"), 32) or max(2, min(24, math.ceil(depth / 2.0)))
        rr = max(bord, depth / n * 0.6)
        c0 = ex.get("color") if is_color(ex.get("color")) else "#000000"
        c1 = ex.get("color_end") if is_color(ex.get("color_end")) else c0
        for i in range(n, 0, -1):
            f = i / n
            out.append((math.cos(ang) * depth * f, math.sin(ang) * depth * f, mix(c0, c1, f) if c1 != c0 else c0,
                        rr))
    return out


# ---------------------------------------------------------------- page compiler

def compile_pages(pages, W, H, on_page=None):
    """[(Caption, resolved template)] -> ASS Dialogue lines. Inside page_cache() every page compiles once per job
    (band pass + render chunks reuse it). on_page(done, total) reports progress."""
    lines = []
    cache = _PCACHE.get()
    for ci, (cap, tpl) in enumerate(pages):
        lines += compile_page(ci, cap, tpl, W, H) if cache is None else _cached_page(cache, ci, cap, tpl, W, H)
        if on_page is not None:
            on_page(ci + 1, len(pages))
    return lines


_PCACHE = contextvars.ContextVar("page_cache", default=None)
_SHIFT_RE = re.compile(r"\\(pos|move|org|clip)\(([-0-9., ]+)\)")


@contextlib.contextmanager
def page_cache():
    """Compile each page once inside this block: caption_band() and every render chunk's ass_text() reuse the
    events (heavy Pro templates cost ~200 ms per page). Keyed by object identity, so it only spans one job that
    keeps the same (Caption, template) pairs alive."""
    tok = _PCACHE.set({})
    try:
        yield
    finally:
        _PCACHE.reset(tok)


def _shift_nums(m, gx, gy):
    vals = m.group(2).split(",")
    n = 4 if m.group(1) in ("move", "clip") else 2
    if len(vals) < n:
        return m.group(0)
    try:
        nums = [float(v) - (gx if i % 2 == 0 else gy) for i, v in enumerate(vals[:n])]
    except ValueError:
        return m.group(0)
    return "\\%s(%s)" % (m.group(1), ",".join([fmt(v) for v in nums] + vals[n:]))


def shift_line(line, gx, gy):
    """Move one Dialogue line (compiled at origin 0, 0) to a canvas whose origin is (gx, gy): only the override
    block's pos / move / org / clip coordinates change (drawings are relative to pos)."""
    i = line.find(",,{")
    j = line.find("}", i + 3) if i >= 0 else -1
    if j < 0:
        return line
    head = _SHIFT_RE.sub(lambda m: _shift_nums(m, gx, gy), line[i:j])
    return line[:i] + head + line[j:]


def _cached_page(cache, ci, cap, tpl, W, H):
    pi = cap.index if getattr(cap, "index", -1) >= 0 else ci
    key = (id(cap), id(tpl), pi, W, H)
    hit = cache.get(key)
    if hit is None:
        tok = _CTX.set({"origin": (0.0, 0.0), "bbox": [math.inf, math.inf, -math.inf, -math.inf]})
        try:
            ev = compile_page(ci, cap, tpl, W, H)
            bb = tuple(_CTX.get()["bbox"])
        finally:
            _CTX.reset(tok)
        hit = cache[key] = (cap, tpl, ev, bb)          # keeps cap/tpl alive so their ids stay unique
    ev, bb = hit[2], hit[3]
    ctx = _CTX.get()
    if ctx is None:
        return list(ev)
    b = ctx["bbox"]
    b[0], b[1], b[2], b[3] = min(b[0], bb[0]), min(b[1], bb[1]), max(b[2], bb[2]), max(b[3], bb[3])
    gx, gy = ctx["origin"]
    if gx or gy:
        return [shift_line(x, gx, gy) for x in ev]
    return list(ev)


def compile_page(ci, cap, tpl, W, H):
    st, hl, an, tim = tpl["style"], tpl["highlight"], tpl["anim"], tpl["timing"]
    k = min(W, H) / REF
    mode = tim["mode"]
    a_in = anim_name(an["in"], IN, ALIASES_IN)
    a_out = anim_name(an["out"], OUT, ALIASES_OUT)
    w_in = anim_name(an.get("word_in", "none"), IN, ALIASES_IN)
    lines = []
    boxes, bbox, fit = layout(cap, tpl, W, H)
    if not boxes:
        return lines
    P0, P1 = cap.start, cap.end
    if P1 - P0 < 0.02:
        return lines
    pi = cap.index if getattr(cap, "index", -1) >= 0 else ci
    fx = page_fx(tpl, pi, k, fit)
    afx = AFX.page_fx(tpl, boxes, P0, P1, W, H, pi) if an.get("fx") else None   # animation studio (anim.py)
    if afx:
        a_in, a_out = afx.override(a_in, a_out)
    LY = LAYERS_PRO if fx["pro"] else LAYERS_CLASSIC
    tilt = fx["tilt"]
    tilt_pos = bool(tilt) and a_in != "rotate"          # rotate-in already turns the page about its centre
    t_cos, t_sin = math.cos(math.radians(tilt)), math.sin(math.radians(tilt))
    pcx, pcy = (bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2
    in_d = min(an["in_dur"], (P1 - P0) * 0.5) if a_in != "none" else 0.0
    out_d = min(an["out_dur"], (P1 - P0) * 0.3) if a_out != "none" else 0.0
    page_win = [(P0, P0 + in_d), (P1 - out_d, P1)]

    def page_d(t):
        return combine(anim_at(a_in, IN, (t - P0) / in_d if in_d > 0 else 1),
                       anim_at(a_out, OUT, (t - (P1 - out_d)) / out_d if out_d > 0 else 0))
    page_db = page_d                       # boxes, pills, emoji
    if afx:
        page_win = page_win + afx.windows(None)
        page_d0 = page_d

        def page_d(t):
            return combine(page_d0(t), afx.page(t))

        def page_db(t):
            return combine(page_d(t), afx.box(t))

    def place(ox, oy, pd):
        """Page-relative offset (already scaled) -> frame position, with the page tilt."""
        if tilt_pos:
            ox, oy = ox * t_cos + oy * t_sin, -ox * t_sin + oy * t_cos
        return pcx + ox + pd["dx"] * H, pcy + oy + pd["dy"] * H

    placef = place if tilt_pos else None
    tilt_org = (pcx, pcy) if (tilt and not tilt_pos) else None    # tilted + rotate-in: turn pills/emoji with the page
    ws = [b.w for b in boxes]
    act = []                       # active window per word: from its start to the next word start (or its end)
    for i, w in enumerate(ws):
        a0 = w.start
        a1 = ws[i + 1].start if i + 1 < len(ws) and ws[i + 1].start - w.end < 0.5 else w.end
        act.append((a0, max(a1, a0 + 0.05)))
    if afx:
        afx.bind(act)
    cycle = st.get("color_cycle")
    rise = hl["pop_dur"]
    hlmode = mode in ("karaoke", "word", "reveal")
    popping = hlmode and hl["scale"] not in (None, 1.0)
    wiggle = fx["wiggle"] if hlmode else 0.0

    def act_env(i, t):
        if t < act[i][0]:
            return 0.0
        a0, a1 = act[i]
        up = clamp01((t - a0) / rise)
        down = clamp01((t - a1) / rise) if t >= a1 else 0.0
        return e_out_back(up, 2.5) * (1 - e_out_cubic(down))

    def act_scale(i, t):
        if not popping or t < act[i][0]:
            return 1.0
        a0, a1 = act[i]
        up = clamp01((t - a0) / rise)
        down = clamp01((t - a1) / rise) if t >= a1 else 0.0
        return 1 + (hl["scale"] - 1) * e_out_back(up, 2.5) * (1 - e_out_cubic(down))

    rows = {}
    for bx in boxes:
        rows.setdefault(round(bx.base, 1), []).append(bx)
    row_of = {bx.idx: rows[round(bx.base, 1)] for bx in boxes}

    def push(b, t):
        """Neighbours slide aside while a word on the same line is scaled up, so pops never overlap."""
        if not popping and not (afx and afx.pushes):
            return 0.0
        dx = 0.0
        for o in row_of[b.idx]:
            if o.idx != b.idx:
                g = (act_scale(o.idx, t) * (afx.scale(o.idx, t) if afx else 1.0) - 1) * o.width / 2
                dx += g if o.idx < b.idx else -g
        return dx

    def row_wins(b):
        if afx and afx.pushes:
            return [w_ for o in row_of[b.idx] for w_ in afx.neighbour_windows(o.idx)] + row_wins0(b)
        return row_wins0(b)

    def row_wins0(b):
        return [] if not popping else [w_ for o in row_of[b.idx] for w_ in
                                       ((act[o.idx][0], act[o.idx][0] + rise), (act[o.idx][1], act[o.idx][1] + rise))]

    box_t = tpl["box"]
    sticker = bool(box_t.get("enabled")) and box_t.get("shape") == "sticker"
    sticker_tilt = _num(box_t.get("tilt"), 0.0) if sticker else 0.0
    oc_op = fx["oc_opacity"]
    fut_col = fx["future_color"]
    typewriter = (w_in == "typewriter" and mode == "reveal") or a_in == "typewriter"
    for b in boxes:
        w = b.w
        wsty = b.ws
        fill = cycle[(pi + b.idx) % len(cycle)] if cycle else st["fill"]
        grad = fx["grad"] if "color" not in wsty else None
        if grad:
            fill = grad["colors"][0]
        fill = wsty.get("color", fill)
        act_col = wsty.get("active_color", hl["color"])
        oc = wsty.get("outline_color", st["outline_color"])
        hl_oc = hl["outline_color"]
        if oc_op is not None:
            oc = with_opacity(oc, oc_op)
            hl_oc = with_opacity(hl_oc, oc_op) if hl_oc else hl_oc
        bord = st["outline"] * k * fit
        a0, a1 = act[b.idx]
        wrot = _num(wsty.get("rotate")) + (sticker_tilt * alt(pi, b.idx) if sticker_tilt else 0.0)
        wsign = alt(pi, b.idx)

        def word_d(t, b=b, w=w, wrot=wrot, wsign=wsign):
            d = [{"s": act_scale(b.idx, t), "dx": push(b, t) / H}]
            if afx:
                d.append(afx.word(b.idx, t))
            if mode == "reveal" and w_in != "typewriter":
                d.append(anim_at(w_in, IN, (t - w.start) / max(an.get("word_in_dur", 0.14), 1e-3)))
            if wrot:
                d.append({"rot": wrot})
            if wiggle:
                d.append({"rot": wsign * wiggle * act_env(b.idx, t)})
            return combine(*d)

        t0 = w.start if mode == "reveal" else P0
        t0 = max(t0, P0)
        t1 = P1
        if t1 - t0 < 0.01:
            continue
        steps = [a0, a1]

        def colour_at(t, fill=fill, act_col=act_col, oc=oc, hl_oc=hl_oc, a0=a0, a1=a1):
            """(fill, outline, plain) at t: plain = the word shows its normal fill (gradient visible)."""
            if hlmode and act_col and not hl["sweep"] and a0 <= t < a1:
                return act_col, hl_oc or oc, False
            if hlmode and t >= a1 and hl["past_color"] and not hl["sweep"]:
                return hl["past_color"], oc, False
            if fut_col and mode == "karaoke" and t < a0:
                return fut_col, oc, False
            return fill, oc, True

        def text_state(t, b=b, a0=a0, w=w, word_d=word_d, colour_at=colour_at):
            pd, wd = page_d(t), word_d(t)
            s = pd["s"] * wd["s"]
            if tilt_pos:
                x, y = place((b.cx - pcx) * pd["s"] + wd["dx"] * H, (b.cy - pcy) * pd["s"] + wd["dy"] * H, pd)
            else:
                x = pcx + (b.cx - pcx) * pd["s"] + (pd["dx"] + wd["dx"]) * H
                y = pcy + (b.cy - pcy) * pd["s"] + (pd["dy"] + wd["dy"]) * H
            a = pd["a"] * wd["a"]
            col, ocol, plain = colour_at(t)
            if afx and afx.has_color():
                c2 = afx.color(b.idx, t, col)
                if c2 != col:
                    col, plain = c2, False
            if mode == "karaoke" and t < a0 and hl["future_alpha"] < 1:
                a *= hl["future_alpha"]
            c, ca = ass_color(col)
            o, oa = ass_color(ocol)
            st_ = {"x": x, "y": y, "s": s, "a": a, "blur": (pd["blur"] + wd["blur"]) * k,
                   "rot": pd["rot"] + wd["rot"] + tilt if tilt else pd["rot"] + wd["rot"],
                   "tags": rf"\1c{c}\1a{ca}\3c{o}\3a{oa}", "plain": plain}
            if typewriter:
                st_["clip"] = typewriter_clip(b, t, caps_reveal(b, P0, in_d, boxes, an, mode, w_in), bord)
            if afx and afx.has_spacing():
                extra = afx.spacing(b.idx, t) * k * fit
                if abs(extra) > 0.05:
                    st_["xtags"] = rf"\fsp{fmt(b.sp + extra)}"
                    st_["m"] = abs(extra) * len(w.text) / 2
            return st_

        body = ass_escape(w.text)
        sh = st["shadow"] * k * fit
        font_tags = (rf"\fn{b.fi.family}\fs{fmt(b.size)}\fsp{fmt(b.sp)}"
                     + (r"\i1" if wsty.get("italic") or tpl["font"].get("italic") else "")
                     + (r"\b1" if wsty.get("bold") and not b.fi.bundled else "")
                     + (rf"\fax{fmt(-fx['skew'])}" if fx["skew"] else ""))
        static = (rf"\fn{b.fi.family}\fs{fmt(b.size)}\fsp{fmt(b.sp)}\bord{fmt(bord)}"
                  + (r"\i1" if wsty.get("italic") or tpl["font"].get("italic") else "")
                  + (r"\b1" if wsty.get("bold") and not b.fi.bundled else ""))
        if fx["skew"]:
            static += rf"\fax{fmt(-fx['skew'])}"
        hard_shadow = ""
        if sh and not st["shadow_blur"]:
            sc, sa = ass_color(st["shadow_color"])
            sxo = st["shadow_x"] if st["shadow_x"] is not None else st["shadow"]
            syo = st["shadow_y"] if st["shadow_y"] is not None else st["shadow"]
            hard_shadow = rf"\xshad{fmt(sxo * k * fit)}\yshad{fmt(syo * k * fit)}\4c{sc}\4a{sa}"
        if hard_shadow and not fx["o2"]:
            static += hard_shadow
        else:
            static += r"\shad0"
        wins = page_win + [(a0, a0 + rise), (a1, a1 + rise)] + row_wins(b)
        geo_wins = page_win + row_wins(b) + ([(a0, a0 + rise), (a1, a1 + rise)] if (popping or wiggle) else [])
        geo_steps = []
        if mode == "reveal":
            wins.append((w.start, w.start + an.get("word_in_dur", 0.14)))
            geo_wins.append((w.start, w.start + an.get("word_in_dur", 0.14)))
        if afx:
            aw = afx.windows(b.idx)
            wins += aw
            geo_wins += aw
        if typewriter:
            r0, r1 = caps_reveal(b, P0, in_d, boxes, an, mode, w_in)
            geo_steps = [r0 + (r1 - r0) * i / max(1, len(w.text)) for i in range(len(w.text) + 1)]
            steps += geo_steps
        org = (pcx, pcy) if a_in == "rotate" else None
        glow = wsty.get("glow") or st.get("glow")
        glow = glow if isinstance(glow, dict) and glow.get("color") else None
        skew_m = abs(fx["skew"]) * (b.asc + b.desc) / 2
        margin = bord + abs(sh) + (glow or {}).get("width", 0) * k + 4 + fx["o2"] + skew_m
        half = (b.width / 2 + margin, (b.asc + b.desc) / 2 + margin)

        # 3D extrude / long shadow: stacked offset copies, far -> near, under the stroke and the text
        for dx, dy, ccol, rr in stack_copies(fx, k, fit, bord):
            cc, cca = ass_color(ccol)

            def cp_state(t, f=text_state, dx=dx, dy=dy, tg=rf"\1c{cc}\1a{cca}\3c{cc}\3a{cca}"):
                s = dict(f(t))
                s["x"] += dx * s["s"]
                s["y"] += dy * s["s"]
                s["tags"] = tg
                return s
            cstatic = font_tags + rf"\bord{fmt(rr)}\shad0"
            lines += emit(Elem(LY["extrude"], t0, t1, body, cstatic, cp_state, geo_wins, geo_steps, org=org,
                               half=(half[0] - bord + rr, half[1] - bord + rr), merge=True))

        # outer stroke (outline2): fatter border in the stroke colour under the text; carries the hard shadow
        if fx["o2"]:
            o2c, o2a = ass_color(fx["o2_color"])

            def o2_state(t, f=text_state, tg=rf"\1c{o2c}\1a{o2a}\3c{o2c}\3a{o2a}"):
                s = dict(f(t))
                s["tags"] = tg
                return s
            o2static = font_tags + rf"\bord{fmt(bord + fx['o2'])}" + (hard_shadow or r"\shad0")
            lines += emit(Elem(LY["outline2"], t0, t1, body, o2static, o2_state, geo_wins, geo_steps, org=org,
                               half=half, merge=True))

        lines += emit(Elem(LY["text"], t0, t1, body, static, text_state, wins, steps, org=org, half=half))

        # gradient fill: the text above is drawn in the first stop; copies clipped to bands paint the rest
        if grad:
            lines += gradient_bands(grad, b, row_of[b.idx], text_state, font_tags, body, LY["text"], t0, t1,
                                    geo_wins, [a0, a1] + geo_steps, org, half, pcx, pd_place=lambda pd, xx: pcx + (xx - pcx) * pd["s"]
                                    + pd["dx"] * H, page_d=page_d, bord=bord)

        # karaoke sweep: active-colour copy on top, clipped left->right across the spoken duration
        if hl["sweep"] and act_col and mode in ("karaoke", "reveal"):
            sweep_end = max(w.end, a0 + 0.08)

            def sweep_state(t, f=text_state, b=b, a0=a0, se=sweep_end, bord=bord, act_col=act_col, oc=oc):
                s = dict(f(t))
                c, ca = ass_color(hl["past_color"] if (hl["past_color"] and t >= se + 0.15) else act_col)
                o, oa = ass_color(hl_oc or oc)
                s["tags"] = rf"\1c{c}\1a{ca}\3c{o}\3a{oa}"
                p = clamp01((t - a0) / (se - a0))
                left = s["x"] - (b.width / 2 + bord + 6) * s["s"]
                span = (b.width + 2 * bord + 12) * s["s"]
                s["clip"] = (left, s["y"] - b.size * 2, left + span * p, s["y"] + b.size * 2)
                return s
            if t1 > a0:
                lines += emit(Elem(LY["text"], max(a0, t0), t1, body, static, sweep_state, wins + [(a0, sweep_end)],
                                   steps, org=org, half=half))

        # soft shadow layer (separate so the outline stays crisp)
        if sh and st["shadow_blur"]:
            sc, sa = ass_color(st["shadow_color"])
            sxo = (st["shadow_x"] if st["shadow_x"] is not None else st["shadow"]) * k * fit
            syo = (st["shadow_y"] if st["shadow_y"] is not None else st["shadow"]) * k * fit

            def sh_state(t, f=text_state, sxo=sxo, syo=syo, sc=sc, sa=sa):
                s = dict(f(t))
                s["x"] += sxo * s["s"]
                s["y"] += syo * s["s"]
                s["blur"] = s["blur"] + st["shadow_blur"] * k
                s["tags"] = rf"\1c{sc}\1a{sa}\3c{sc}\3a{sa}"
                return s
            shstatic = static.replace(r"\shad0", "") + r"\shad0"
            if fx["o2"]:
                shstatic = shstatic.replace(rf"\bord{fmt(bord)}", rf"\bord{fmt(bord + fx['o2'])}", 1)
            lines += emit(Elem(LY["shadow"], t0, t1, body, shstatic, sh_state, wins, steps,
                               org=org, half=(half[0] + st["shadow_blur"] * k * 3, half[1] + st["shadow_blur"] * k * 3)))

        # glow layer: fat blurred outline under the text (style glow, optional active-only; strength > 1 adds a
        # tighter second glow), plus the active-word glow (highlight.glow)
        glows = []
        if glow:
            glows.append((glow, bool(glow.get("active_only"))))
        if fx["hl_glow"]:
            glows.append((fx["hl_glow"], True))
        for g, only_active in glows:
            strength = _num(g.get("strength"), 1.0) if "strength" in g else 1.0
            layers_g = [(g.get("width", 10), g.get("blur", 8), min(1.0, strength))]
            if strength > 1:
                layers_g.append((g.get("width", 10) * 0.55, g.get("blur", 8) * 0.5, min(1.0, strength - 1)))
            for gw_, gbl, galpha in layers_g:
                gcol = g["color"] if galpha >= 1 else scale_alpha(g["color"], galpha)
                gc, ga = ass_color(gcol)
                gb = gw_ * k * fit

                def glow_state(t, f=text_state, gc=gc, ga=ga, gbl=gbl, oa_=only_active, a0=a0, a1=a1):
                    s = dict(f(t))
                    s["blur"] = s["blur"] + gbl * k
                    s["tags"] = rf"\1c{gc}\1a{ga}\3c{gc}\3a{ga}"
                    if oa_ and not (a0 <= t < a1):
                        s["a"] = 0.0
                    return s
                gstatic = static.replace(rf"\bord{fmt(bord)}", rf"\bord{fmt(gb + (fx['o2'] if fx['o2'] else 0))}")
                g_t0, g_t1 = (max(t0, a0), min(t1, a1)) if (only_active and g is fx["hl_glow"]) else (t0, t1)
                if g_t1 - g_t0 >= 0.01:
                    lines += emit(Elem(LY["glow"], g_t0, g_t1, body, gstatic, glow_state, wins, steps, org=org,
                                       half=(half[0] + gbl * k * 3, half[1] + gbl * k * 3)))

        # always-on per-word pill (word style), active-word pill, active-word underline
        pill_col = wsty.get("pill")
        underline_col = wsty.get("underline")
        for kind, colr in (("pill", pill_col), ("hl_pill", hl["pill"] if mode != "line" else None),
                           ("underline", hl["underline"] if mode != "line" else None),
                           ("word_underline", underline_col)):
            if not colr:
                continue
            lines += pill_events(kind, colr, b, boxes, act, tpl, k, fit, page_db, pcx, pcy, H, P0, P1, mode,
                                 lambda t, b=b: push(b, t), row_wins(b), t0, layer=LY["pill"], place=placef,
                                 rot=tilt + wrot, seed=pi * 31 + b.idx, org=tilt_org)

        ech = wsty.get("emoji")
        if ech:
            lines += emoji_events(ech, b, boxes, tpl, k, fit, page_db, pcx, pcy, H, lambda t, b=b: push(b, t),
                                  a0 if mode != "line" else P0, P1, page_win, layer=LY["emoji"], place=placef,
                                  rot=tilt, org=tilt_org)

    if tpl["box"]["enabled"]:
        lines += box_events(boxes, tpl, k, fit, page_db, pcx, pcy, H, P0, P1, page_win, a_in, LY=LY, place=placef,
                            tilt=tilt, pi=pi, seed=_seed(cap.id))
    return lines


def _seed(s):
    h = 0
    for ch in str(s or ""):
        h = (h * 131 + ord(ch)) & 0xFFFFFF
    return h


def gradient_bands(grad, b, row, text_state, font_tags, body, layer, t0, t1, wins, steps, org, half, pcx,
                   pd_place, page_d, bord):
    """Copies of one word clipped to gradient bands (band 0 = the text event itself). Hidden while the word shows
    another colour (active / past / future / per-word colour)."""
    n = grad["bands"]
    cols = [ramp(grad["colors"], i / (n - 1)) for i in range(n)]
    vertical = grad["dir"] == "vertical"
    if vertical:
        lo, hi = (b.base - b.cap) - b.cy, b.base - b.cy            # cap top .. baseline, relative to the anchor
    elif grad["span"] == "line":
        lo, hi = min(o.x for o in row) - bord, max(o.x + o.width for o in row) + bord   # absolute page x
    else:
        lo, hi = b.x - bord - b.cx, b.x + b.width + bord - b.cx
    out = []
    for i in range(1, n):
        e0 = lo + (hi - lo) * i / n
        e1 = lo + (hi - lo) * (i + 1) / n if i < n - 1 else None
        if not vertical and grad["span"] == "line":              # skip bands that never touch this word
            if e0 > b.x + b.width + bord + 2 or (e1 is not None and e1 < b.x - bord - 2):
                continue
        c, ca = ass_color(cols[i])

        def st(t, e0=e0, e1=e1, tg=rf"\1c{c}\1a{ca}"):
            s = dict(text_state(t))
            if not s.pop("plain", True):
                s["a"] = 0.0
            s["tags"] = tg
            sc = s["s"]
            if vertical:
                rect = [s["x"] - BIG, s["y"] + e0 * sc, s["x"] + BIG, s["y"] + e1 * sc if e1 is not None else
                        s["y"] + BIG]
            elif grad["span"] == "line":
                pd = page_d(t)
                rect = [pd_place(pd, e0), s["y"] - BIG, pd_place(pd, e1) if e1 is not None else s["x"] + BIG,
                        s["y"] + BIG]
            else:
                rect = [s["x"] + e0 * sc, s["y"] - BIG, s["x"] + e1 * sc if e1 is not None else s["x"] + BIG,
                        s["y"] + BIG]
            tw = s.get("clip")
            if tw:                                                   # typewriter reveal: intersect
                rect = [max(rect[0], tw[0]), max(rect[1], tw[1]), min(rect[2], tw[2]), min(rect[3], tw[3])]
                if rect[2] <= rect[0]:
                    s["a"] = 0.0
                    rect[2] = rect[0] + 0.01
            s["clip"] = tuple(rect)
            return s
        out += emit(Elem(layer, t0, t1, body, font_tags + r"\bord0\shad0", st, wins, steps, org=org, half=half,
                         merge=True))
    return out


def caps_reveal(b, P0, in_d, boxes, an, mode, w_in):
    """[r0, r1] when this word's characters are typed out."""
    if mode == "reveal" and w_in == "typewriter":
        w = b.w
        return w.start, w.start + min(max(w.end - w.start, 0.05), 0.045 * len(w.text) + 0.02)
    total = sum(len(x.w.text) for x in boxes) or 1
    before = sum(len(x.w.text) for x in boxes[:b.idx])
    dur = max(in_d, 0.25)
    return P0 + dur * before / total, P0 + dur * (before + len(b.w.text)) / total


def typewriter_clip(b, t, rv, bord):
    """Rect clip revealing whole characters typed so far (character edges from HarfBuzz advances)."""
    r0, r1 = rv
    n = len(b.w.text)
    shown = n if t >= r1 else 0 if t < r0 else int((t - r0) / max(r1 - r0, 1e-3) * n)
    if shown >= n:
        return None
    xr = b.x + b.fi.advance(b.w.text[:shown], b.size, b.sp) + 1 if shown else b.x - bord - 3
    return (b.x - bord - 4, b.base - b.asc - bord - 40, xr, b.base + b.desc + bord + 40)


def pill_events(kind, colr, b, boxes, act, tpl, k, fit, page_d, pcx, pcy, H, P0, P1, mode, pushf, extra_wins, wt0,
                layer=1, place=None, rot=0.0, seed=0, org=None):
    hl = tpl["highlight"]
    bord = (tpl["style"]["outline"] + outline2_px(tpl)) * k * fit
    padx, pady = hl["pill_pad_x"] * k * fit + bord, hl["pill_pad_y"] * k * fit + bord
    upper = b.w.text.upper() == b.w.text

    def geom(bx):
        top = bx.base - bx.cap - pady
        bot = bx.base + (0 if upper else bx.desc * 0.55) + pady
        return bx.cx, (top + bot) / 2, bx.width + 2 * padx, bot - top

    cx, cy, pw, ph = geom(b)
    is_line = kind in ("underline", "word_underline")
    if is_line:
        ph = max(4.0, 0.09 * b.size)
        cy = b.base + 0.12 * b.size
        pw = b.width
        st = tpl["style"]
        drop = 0.0
        if st["shadow"] and not st["shadow_blur"]:
            drop = max(0.0, (st["shadow_y"] if st["shadow_y"] is not None else st["shadow"]) * k * fit)
        low = bord + drop + 2 + ph / 2           # keep a thick outline / hard shadow from covering the line
        if low > 0.12 * b.size:
            cy = b.base + low
    radius = min(hl["pill_radius"] * k * fit, ph / 2) if not is_line else ph / 2
    c, ca = ass_color(colr)
    shape = hl.get("pill_shape") if kind == "hl_pill" else None
    body = shape_path(shape, pw, ph, radius, seed) if shape in ("marker", "banner", "ribbon") else rrect(pw, ph, radius)
    a0, a1 = act[b.idx]
    if kind in ("pill", "word_underline"):
        t0, t1 = wt0, P1
    else:
        t0, t1 = a0, min(a1, P1)
        if mode == "word":
            t0, t1 = P0, P1
    t0 = max(t0, P0)
    if t1 - t0 < 0.01:
        return []
    sliding = kind in ("hl_pill", "underline")
    prev = boxes[b.idx - 1] if (sliding and b.idx > 0 and hl["pill_slide"]) else None
    if prev is not None and (a0 - act[b.idx - 1][1] > 0.05 or abs(prev.base - b.base) > 1):
        prev = None
    slide = 0.11
    pcx_, pcy_, pw_, _ = geom(prev) if prev is not None else (cx, cy, pw, ph)
    if is_line and prev is not None:
        pcy_, pw_ = cy, prev.width

    def state(t):
        pd = page_d(t)
        p = clamp01((t - t0) / slide) if prev is not None else 1.0
        e = e_out_cubic(p)
        x = pcx_ + (cx - pcx_) * e
        y = pcy_ + (cy - pcy_) * e
        sx = (pw_ + (pw - pw_) * e) / pw
        grow = 1.0
        if prev is None and sliding:
            grow = max(0.0, e_out_back(clamp01((t - t0) / 0.1), 1.6))
        if hl["scale"] not in (None, 1.0) and kind == "hl_pill":
            up = clamp01((t - a0) / hl["pop_dur"])
            grow *= 1 + (hl["scale"] - 1) * e_out_back(up, 2.5)
        if place is not None:
            X, Y = place((x + pushf(t) - pcx) * pd["s"], (y - pcy) * pd["s"], pd)
        else:
            X = pcx + (x + pushf(t) - pcx) * pd["s"] + pd["dx"] * H
            Y = pcy + (y - pcy) * pd["s"] + pd["dy"] * H
        return {"x": X, "y": Y, "s": pd["s"] * grow, "sx": sx, "sy": 1.0, "a": pd["a"], "blur": pd["blur"] * k,
                "rot": pd["rot"] + rot if rot else pd["rot"], "tags": ""}

    static = rf"\bord0\shad0\1c{c}\1a{ca}"
    wins = [(P0, P0 + tpl["anim"]["in_dur"]), (P1 - tpl["anim"]["out_dur"], P1), (t0, t0 + 0.12),
            (a0, a0 + hl["pop_dur"])] + list(extra_wins)
    return emit(Elem(layer, t0, t1, body, static, state, wins, [], drawing=True, org=org, half=(pw / 2, ph / 2)))


def box_events(boxes, tpl, k, fit, page_d, pcx, pcy, H, P0, P1, page_win, a_in, LY=None, place=None, tilt=0.0,
               pi=0, seed=0):
    """Background per line, per page or per word (rounded rect, rough marker, banner, ribbon, sticker; optional
    border, gradient bands and drop shadow). In reveal mode a line/page box grows with the revealed words and word
    boxes appear with their word."""
    LY = LY or LAYERS_CLASSIC
    bx = tpl["box"]
    shape = bx.get("shape") or "rect"
    shape = shape if shape in BOX_SHAPES else "rect"
    c, ca = ass_color(with_opacity(bx["color"], bx.get("opacity")))
    padx, pady = bx["pad_x"] * k * fit, bx["pad_y"] * k * fit
    rad = bx["radius"] * k * fit
    per = "word" if shape == "sticker" else bx.get("per", "line")
    reveal = tpl["timing"]["mode"] == "reveal"
    border = _num(bx.get("border")) * k * fit
    btilt = _num(bx.get("tilt"))
    gcols = _colors(bx.get("gradient"))
    if gcols and bx.get("opacity") is not None:
        gcols = [with_opacity(x, bx["opacity"]) for x in gcols]
    shadow = _on(bx.get("shadow"))
    groups = {}
    for b in boxes:
        groups.setdefault(round(b.base, 1), []).append(b)
    if per == "page":
        rows = [boxes]
    elif per == "word":
        rows = [[b] for b in boxes]
    else:
        rows = list(groups.values())
    grow = 0.12
    from .layout import box_slant, emoji_after_w

    def extent(row):
        if not row:
            return None
        upper = all(b.w.text.upper() == b.w.text for b in row)
        e = (min(b.x for b in row) - padx, min(b.base - b.cap for b in row) - pady,
             max(b.x + b.width + emoji_after_w(b.w, tpl, k, fit) for b in row) + padx,
             max(b.base + (0 if upper else b.desc * 0.55) for b in row) + pady)
        if shape in ("banner", "ribbon"):
            s = box_slant(tpl, e[3] - e[1])
            e = (e[0] - s, e[1], e[2] + s, e[3])
        return e

    fill_on = bx.get("filled", True) is not False and bool(
        parse_color(with_opacity(bx["color"], bx.get("opacity")))[3] > 0 or gcols)
    out = []
    for ri, row in enumerate(rows):
        full = extent(row)
        starts = sorted({b.w.start for b in row}) if reveal else [P0]
        t0 = max(P0, starts[0]) if reveal else P0
        if P1 - t0 < 0.01:
            continue
        rtilt = tilt + (btilt * alt(pi, row[0].idx) if shape == "sticker" else btilt)
        rseed = seed + ri * 7

        def ext_at(t, row=row, full=full):
            if not reveal or len(row) == 1:
                return full
            done = [b for b in row if b.w.start <= t]
            if not done:
                return extent(row[:1])
            last = max(b.w.start for b in done)
            cur = extent(done)
            prev = extent([b for b in row if b.w.start < last]) or cur
            e = e_out_cubic(clamp01((t - last) / grow))
            return tuple(a + (bb - a) * e for a, bb in zip(prev, cur))

        def state(t, ext_at=ext_at, rtilt=rtilt, rseed=rseed):
            pd = page_d(t)
            x0, y0, x1, y1 = ext_at(t)
            cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
            if place is not None:
                X, Y = place((cx - pcx) * pd["s"], (cy - pcy) * pd["s"], pd)
            else:
                X, Y = pcx + (cx - pcx) * pd["s"] + pd["dx"] * H, pcy + (cy - pcy) * pd["s"] + pd["dy"] * H
            d = {"x": X, "y": Y, "s": pd["s"], "a": pd["a"], "blur": pd["blur"] * k,
                 "rot": pd["rot"] + rtilt if rtilt else pd["rot"], "tags": "", "bw": x1 - x0, "bh": y1 - y0,
                 "br": rad}
            if shape != "rect":
                d["shape"], d["seed"] = shape, rseed
            if border:
                d["m"] = border
            return d
        org = (pcx, pcy) if a_in == "rotate" else None
        wins = page_win + [(s_, s_ + grow) for s_ in starts]
        body0 = shape_path(shape, full[2] - full[0], full[3] - full[1], rad, rseed)
        if shadow:
            sc, sa = ass_color(shadow.get("color") if is_color(shadow.get("color")) else "#00000099")
            sdx, sdy = _num(shadow.get("x"), 0.0) * k * fit, _num(shadow.get("y"), 6.0) * k * fit
            sbl = _num(shadow.get("blur"), 6.0) * k

            def sh_state(t, f=state, sdx=sdx, sdy=sdy, sbl=sbl):
                s = dict(f(t))
                s["x"] += sdx * s["s"]
                s["y"] += sdy * s["s"]
                s["blur"] = s["blur"] + sbl
                s["m"] = s.get("m", 0.0) + border
                return s
            out += emit(Elem(LY["box_shadow"], t0, P1, body0,
                             rf"\bord{fmt(border)}\shad0\1c{sc}\1a{sa}\3c{sc}\3a{sa}" if border else
                             rf"\bord0\shad0\1c{sc}\1a{sa}", sh_state, wins, [], drawing=True, org=org, half=(1, 1)))
        if border:
            bc, ba = ass_color(bx.get("border_color") if is_color(bx.get("border_color")) else "#FFFFFF")
            if gcols:
                c, ca = ass_color(gcols[0])
            static = rf"\bord{fmt(border)}\shad0\1c{c}\1a{ca if fill_on else '&HFF&'}\3c{bc}\3a{ba}"
        else:
            if gcols:
                c, ca = ass_color(gcols[0])
            static = rf"\bord0\shad0\1c{c}\1a{ca}"
        if fill_on or border:
            out += emit(Elem(LY["box"], t0, P1, body0, static, state, wins, [], drawing=True, org=org, half=(1, 1)))
        if gcols and fill_on:
            gopt = bx.get("gradient") if isinstance(bx.get("gradient"), dict) else {}
            n = max(2, min(16, int(_num(gopt.get("bands"), 8))))
            for i in range(1, n):
                gc, ga = ass_color(ramp(gcols, i / (n - 1)))

                def bstate(t, f=state, i=i, n=n):
                    s = dict(f(t))
                    top = s["y"] - s["bh"] * s["s"] / 2
                    hgt = s["bh"] * s["s"]
                    s["clip"] = (s["x"] - BIG, top + hgt * i / n,
                                 s["x"] + BIG, top + hgt * (i + 1) / n if i < n - 1 else s["y"] + BIG)
                    return s
                out += emit(Elem(LY["box"], t0, P1, body0, rf"\bord0\shad0\1c{gc}\1a{ga}", bstate, wins, [],
                                 drawing=True, org=org, half=(1, 1)))
    return out


def emoji_events(ech, b, boxes, tpl, k, fit, page_d, pcx, pcy, H, pushf, t0, P1, page_win, layer=5, place=None,
                 rot=0.0, org=None):
    size = tpl["font"]["size"] * k * fit * tpl["emoji"]["size"] * 1.15
    layers = emo.colr_ass_layers(ech, round(size, 1))
    if not layers or P1 - t0 < 0.01:
        return []
    if tpl["emoji"]["position"] == "after":
        ex, ey = b.x + b.width + size * 0.6, b.base - b.cap / 2
    else:  # above the block, centred over the word
        ex, ey = b.cx, min(bx.base - bx.cap for bx in boxes) - tpl["style"]["outline"] * k * fit - size * 0.62

    def state(t):
        pd = page_d(t)
        p = clamp01((t - t0) / 0.22)
        if place is not None:
            X, Y = place((ex + pushf(t) - pcx) * pd["s"], (ey - pcy) * pd["s"], pd)
        else:
            X, Y = pcx + (ex + pushf(t) - pcx) * pd["s"] + pd["dx"] * H, pcy + (ey - pcy) * pd["s"] + pd["dy"] * H
        r = -10 * (1 - e_out_back(p))
        return {"x": X, "y": Y, "s": pd["s"] * max(0.0, e_out_back(p, 2.6)), "a": pd["a"] * clamp01(p * 3),
                "blur": 0.0, "rot": r + rot if rot else r, "tags": ""}

    out = []
    for col, a, d in layers:
        out += emit(Elem(layer, t0, P1, d, rf"\bord0\shad0\1c{col}\1a{a}", state, page_win + [(t0, t0 + 0.22)], [],
                         drawing=True, org=org or (None if place is not None else (ex, ey)), half=(size / 2, size / 2)))
    return out


# ---------------------------------------------------------------- band + file

def caption_band(pages, W, H, align=8, margin=4, on_page=None):
    """Union bbox of everything the captions ever draw (pops, slides, blur, emoji, boxes), snapped outward to
    `align` px and clamped to the frame -> (x, y, w, h)."""
    tok = _CTX.set({"origin": (0.0, 0.0), "bbox": [math.inf, math.inf, -math.inf, -math.inf]})
    try:
        compile_pages(pages, W, H, on_page)
        x0, y0, x1, y1 = _CTX.get()["bbox"]
    finally:
        _CTX.reset(tok)
    if x0 == math.inf:
        return 0, 0, W, H
    x0 = max(0, int((x0 - margin) // align * align))
    y0 = max(0, int((y0 - margin) // align * align))
    x1 = min(W, int(-(-(x1 + margin) // align) * align))
    y1 = min(H, int(-(-(y1 + margin) // align) * align))
    return x0, y0, max(align, x1 - x0), max(align, y1 - y0)


def ass_text(pages, W, H, band=None, title="Klipora captions"):
    """Full .ass text. band=(x, y, w, h) shifts everything so the canvas is just that band (PlayRes = w x h).
    Returns (text, n_events)."""
    if band:
        bx, by, bw, bh = band
        tok = _CTX.set({"origin": (float(bx), float(by)), "bbox": [math.inf] * 2 + [-math.inf] * 2})
        try:
            ev = compile_pages(pages, W, H)
        finally:
            _CTX.reset(tok)
        PW, PH = bw, bh
    else:
        ev = compile_pages(pages, W, H)
        PW, PH = W, H
    hdr = (
        "[Script Info]\n"
        f"; {title}" + (f" band={list(band)}" if band else "") + "\n"
        "ScriptType: v4.00+\n"
        f"PlayResX: {PW}\nPlayResY: {PH}\n"
        "ScaledBorderAndShadow: yes\nWrapStyle: 2\nYCbCr Matrix: None\nKerning: yes\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
        "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, "
        "MarginR, MarginV, Encoding\n"
        "Style: D,Arial,60,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,5,0,0,0,1\n\n"
        "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )
    return hdr + "\n".join(ev) + "\n", len(ev)


def write_ass(path, pages, W, H, band=None, title="Klipora captions"):
    text, n = ass_text(pages, W, H, band, title)
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return n
