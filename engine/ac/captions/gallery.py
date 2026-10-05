"""Template library helpers: picker metadata (groups, swatches, labels), animated gallery sprite sheets, and the
template_mix / template_random style generators (docs/CAPTIONS_API.md 3.3, 3.13, 3.14, 6.7).

Animated thumbnails: one ffmpeg run per template renders `frames` frames of a ~2 s demo loop (libass, the same
compiler as preview/render) over a neutral frame or the user's own frame, crops them to the caption area and tiles
them into ONE horizontal PNG strip. The panel animates it with CSS `steps()` (Chrome 99, no video decoding).
Cached by template content + frame content + size under %LOCALAPPDATA%\\Klipora\\captions\\anim, LRU-pruned to
ANIM_CACHE_MB.
"""
from __future__ import annotations

import contextlib
import copy
import hashlib
import os
import random
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from ..i18n import tr
from ..util import EngineError, data_hash, local_dir
from . import templates as TP

# ---------------------------------------------------------------- groups / metadata

GROUPS = [(g, "cap.gallery.group." + g) for g in (     # (id, label locale key)
    "shorts_viral", "tutorial", "podcast", "jualan", "sinematik", "edukasi", "minimal", "fun", "gaming", "berita",
    "aesthetic")]
GROUP_LABELS = dict(GROUPS)
# the 19 templates that existed before the Gaya Pro library (their files carry no `group`; kept byte-identical)
CLASSIC_IDS = ("ali_abdaal", "bold_pop", "boxed", "garis_bawah", "hijau_viral", "hormozi_kuning", "jualan", "karaoke",  # i18n-ignore
               "komik", "minimal_putih", "mr_beast", "neon", "podcast", "satu_kata", "sinematik", "subtitle_klasik",  # i18n-ignore
               "tutorial_bersih", "tutorial_sorot", "typewriter")  # i18n-ignore
CLASSIC_GROUPS = {
    "ali_abdaal": ["edukasi", "minimal"], "bold_pop": ["shorts_viral"], "boxed": ["shorts_viral", "berita"],
    "garis_bawah": ["shorts_viral", "edukasi"], "hijau_viral": ["shorts_viral"], "hormozi_kuning": ["shorts_viral"],  # i18n-ignore
    "jualan": ["jualan", "shorts_viral"], "karaoke": ["fun", "edukasi"], "komik": ["fun"], "minimal_putih": ["minimal"],
    "mr_beast": ["shorts_viral", "fun"], "neon": ["gaming", "aesthetic"], "podcast": ["podcast"],
    "satu_kata": ["shorts_viral", "sinematik"], "sinematik": ["sinematik"], "subtitle_klasik": ["minimal", "tutorial"],  # i18n-ignore
    "tutorial_bersih": ["tutorial"], "tutorial_sorot": ["tutorial", "edukasi"], "typewriter": ["tutorial", "edukasi"],  # i18n-ignore
}
EFFECT_LABELS = {k: "cap.gallery.fx." + k for k in ("gradient", "extrude", "long_shadow", "outline2", "glow", "box", "shape",
                                                    "sticker", "rotate", "skew", "wiggle", "emoji")}   # locale keys


def _hex(c):
    """'#RRGGBB(AA)' -> '#RRGGBB' (None when not a colour)."""
    if not isinstance(c, str) or not c.startswith("#") or len(c) not in (7, 9):
        return None
    return c[:7].upper()


def groups_of(tpl):
    """Group ids of a template: its own `group` + group ids in tags, else the classic mapping, else by tags."""
    tid = tpl.get("id", "")
    out = []
    if tpl.get("group") in GROUP_LABELS:
        out.append(tpl["group"])
    out += [t for t in tpl.get("tags") or [] if t in GROUP_LABELS]
    if tid in CLASSIC_GROUPS and tpl.get("source", "builtin") == "builtin":
        out += CLASSIC_GROUPS[tid]
    if not out:                                 # user templates: guess from the legacy tags
        tags = set(tpl.get("tags") or [])
        for g, keys in (("tutorial", {"tutorial", "subtitle"}), ("shorts_viral", {"shorts", "portrait", "hook"}),
                        ("podcast", {"podcast", "interview"}), ("fun", {"fun", "emoji"}), ("gaming", {"gaming"}),
                        ("sinematik", {"cinematic"}), ("minimal", {"clean"}), ("edukasi", {"education"})):
            if tags & keys:
                out.append(g)
    return list(dict.fromkeys(out))


def swatches(tpl, n=4):
    """Up to n distinct opaque colours that describe the look (for swatch dots)."""
    st, hl, bx = tpl["style"], tpl["highlight"], tpl["box"]
    cand = []
    grad = st.get("gradient")
    if isinstance(grad, dict) and grad.get("colors"):
        cand += grad["colors"][:2]
    else:
        cand.append(st.get("fill"))
    cand += [hl.get("pill"), hl.get("color"), hl.get("underline")]
    if bx.get("enabled"):
        cand.append(bx.get("color") if bx.get("filled", True) else bx.get("border_color"))
    cand += list(st.get("color_cycle") or [])[1:3]
    for k in ("extrude", "long_shadow"):
        if isinstance(st.get(k), dict):
            cand.append(st[k].get("color"))
    if st.get("outline2"):
        cand.append(st.get("outline2_color"))
    if (st.get("outline") or 0) >= 3:
        cand.append(st.get("outline_color"))
    em = tpl.get("emphasis") or {}
    cand += [em.get("pill"), em.get("color")]
    out = []
    for c in cand:
        h = _hex(c)
        if h and h not in out:
            out.append(h)
    return out[:n]


def effects(tpl):
    """Labels (job language) of the notable effects a template uses (shown as chips)."""
    st, bx, hl, lay, fo = tpl["style"], tpl["box"], tpl["highlight"], tpl["layout"], tpl["font"]
    out = []
    if st.get("gradient"):
        out.append("gradient")
    if st.get("extrude"):
        out.append("extrude")
    if st.get("long_shadow"):
        out.append("long_shadow")
    if st.get("outline2"):
        out.append("outline2")
    if st.get("glow") or hl.get("glow"):
        out.append("glow")
    if bx.get("enabled"):
        out.append("sticker" if bx.get("shape") == "sticker" else "shape" if bx.get("shape") not in (None, "rect")
                   else "box")
    if lay.get("rotate") or lay.get("rotate_jitter"):
        out.append("rotate")
    if fo.get("skew"):
        out.append("skew")
    if hl.get("wiggle"):
        out.append("wiggle")
    if (tpl.get("emoji") or {}).get("enabled"):
        out.append("emoji")
    return [{"id": e, "label": tr(EFFECT_LABELS[e])} for e in out]


def aspect_fit(tpl):
    a = set(tpl.get("aspect") or [])
    port, land = "9:16" in a, bool(a & {"16:9", "21:9"})
    return "both" if port and land else "9:16" if port else "16:9" if land else "both"


def meta(tpl):
    """Extra picker fields added to the `templates` rows (docs/CAPTIONS_API.md 3.2)."""
    gs = groups_of(tpl)
    tim = tpl["timing"]
    hl_label = TP.tl(TP.MODES.get(tim["mode"], tim["mode"]))
    return {"group": gs[0] if gs else None, "groups": gs,
            "group_label": tr(GROUP_LABELS[gs[0]]) if gs else None,
            "aspect_fit": aspect_fit(tpl), "colors": swatches(tpl), "effects": effects(tpl),
            "font_size": tpl["font"].get("size"),
            "anim_in_label": TP.anim_label(tpl["anim"]["in"]),
            "anim_out_label": TP.anim_label(tpl["anim"]["out"]),
            "word_in_label": (TP.anim_label(tpl["anim"].get("word_in")) if tpl["anim"].get("word_in") else None)
            if tim["mode"] == "reveal" else None,
            "mode_label": hl_label,
            "is_new": tpl.get("source", "builtin") == "builtin" and tpl.get("id") not in CLASSIC_IDS,
            "pro": bool(effects(tpl) and any(e["id"] in ("gradient", "extrude", "long_shadow", "outline2", "shape",
                                                         "sticker", "rotate", "skew", "wiggle") for e in effects(tpl)))}


def enrich_rows(rows, tpls):
    by = {t["id"]: t for t in tpls}
    for r in rows:
        t = by.get(r["id"])
        if t is not None:
            r.update(meta(t))
    return rows


def group_list(rows):
    cnt = {}
    for r in rows:
        for g in r.get("groups") or []:
            cnt[g] = cnt.get(g, 0) + 1
    return [{"id": g, "label": tr(lab), "count": cnt.get(g, 0)} for g, lab in GROUPS]


# ---------------------------------------------------------------- animated sprite sheets

ANIM_V = 1
ANIM_CACHE_MB = 140                   # LRU bound for the sprite cache (< 150 MB)
ANIM_FRAMES = 16
ANIM_FPS = 7                          # 16 frames = 2,29 s loop
ANIM_SIZE = (320, 160)
# demo phrase (sequence seconds); starts after the first frame so each loop begins clean and shows the entrance
ANIM_DEMO = [("Rahasia", 0.14, 0.5), ("cuan", 0.5, 0.82), ("jualan", 0.82, 1.2), ("online", 1.2, 1.55),  # i18n-ignore
             ("100%", 1.55, 1.9), ("gratis!", 1.9, 2.2)]


def anim_dir():
    return local_dir("captions", "anim")


def _demo_words(tpl):
    from . import layout as L
    out = []
    for i, (t, a, b) in enumerate(ANIM_DEMO):
        out.append(L.RWord(text=L.transform_text(t, tpl, start=(i == 0)), start=a, end=b, id=f"demo:{i}", raw=t,
                           sentence_end=t.endswith("!"), seg_end=1 if i == len(ANIM_DEMO) - 1 else 0,
                           cap=(i == 0)))
    return out


def _content_hash(path):
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for blk in iter(lambda: f.read(1 << 20), b""):
            h.update(blk)
    return h.hexdigest()[:16]


def anim_key(tpl, W, H, size, frames, fps, bg_hash):
    return data_hash([ANIM_V, {k: v for k, v in tpl.items() if not k.startswith("_") and k != "source"}, W, H,
                      list(size), frames, fps, bg_hash])


def _info(out, size, frames, fps, cached):
    return {"png": str(out), "frames": frames, "fps": fps, "w": int(size[0]), "h": int(size[1]),
            "cols": frames, "rows": 1, "sheet_w": int(size[0]) * frames, "sheet_h": int(size[1]),
            "dur": round(frames / fps, 3), "cached": cached}


def anim_cached(tpl, W, H, size=ANIM_SIZE, frames=ANIM_FRAMES, fps=ANIM_FPS, bg_hash="neutral"):
    out = anim_dir() / f"{tpl['id']}_{anim_key(tpl, W, H, size, frames, fps, bg_hash)[:16]}.png"
    if out.is_file():
        with contextlib.suppress(OSError):
            os.utime(out)                               # LRU: touch on use
        return out
    return None


def anim_sheet(tpl, W, H, size=ANIM_SIZE, frames=ANIM_FRAMES, fps=ANIM_FPS, bg=None, bg_hash="neutral",
               force=False):
    """Sprite strip (frames x 1) of the demo loop for one template -> info dict (png, frames, fps, w, h, ...)."""
    from . import compile as C
    from . import layout as L
    from . import render as R
    from .preview import _bg, _even, _ff_cwd
    hit = None if force else anim_cached(tpl, W, H, size, frames, fps, bg_hash)
    if hit is not None:
        return _info(hit, size, frames, fps, True)
    key = anim_key(tpl, W, H, size, frames, fps, bg_hash)
    out = anim_dir() / f"{tpl['id']}_{key[:16]}.png"
    t_ = L.prep_template(copy.deepcopy(tpl))
    caps = L.paginate(_demo_words(t_), t_, W, H)
    if not caps:
        raise EngineError("THUMB", tr("cap.gallery.errNoCaption", id=tpl["id"]))
    for i, c in enumerate(caps):
        c.index = i
    loop = frames / fps
    pairs = [(c, t_) for c in caps]
    vis = [p for p in pairs if p[0].start < loop] or pairs[:1]
    bx, by, bw, bh = C.caption_band(vis, W, H)
    work = anim_dir() / "work"
    work.mkdir(exist_ok=True)
    ass = work / f"{tpl['id']}_{key[:8]}.ass"
    C.write_ass(ass, pairs, W, H)
    hw, hh = _even(W / 2), _even(H / 2)
    src = Path(bg) if bg else _bg(hw, hh)
    # crop (half-res coordinates) with the target aspect around the caption area, like the static thumbnail
    ar = size[0] / size[1]
    cx, cy = (bx + bw / 2) / 2, (by + bh / 2) / 2
    half_w = min(max(bw / 4 + 20, (bh / 4 + 12) * ar, 120), hw / 2)
    half_h = half_w / ar
    if half_h > hh / 2:
        half_h = hh / 2
        half_w = half_h * ar
    cx = min(max(cx, half_w), hw - half_w)
    cy = min(max(cy, half_h), hh - half_h)
    cw, ch = int(2 * half_w) // 2 * 2, int(2 * half_h) // 2 * 2
    x0, y0 = int(cx - cw / 2), int(cy - ch / 2)
    tmp = work / f"{tpl['id']}_{key[:8]}.png"
    vf = (f"scale={hw}:{hh},format=rgb24,ass='{ass.name}':fontsdir='{R.fonts_rel(work, ass)}':shaping=complex,"
          f"crop={cw}:{ch}:{x0}:{y0},scale={int(size[0])}:{int(size[1])}:flags=lanczos,tile={frames}x1")
    try:
        _ff_cwd(["-loop", "1", "-framerate", str(fps), "-t", f"{loop:.4f}", "-i", str(src), "-vf", vf,
                 "-frames:v", "1", "-compression_level", "9", "-y", tmp.name], work)
        os.replace(tmp, out)
    finally:
        for p in (ass, tmp):
            with contextlib.suppress(OSError):
                p.unlink()
    return _info(out, size, frames, fps, False)


def cache_size():
    return sum(p.stat().st_size for p in anim_dir().glob("*.png"))


def prune(limit_mb=ANIM_CACHE_MB, protect=()):
    """Delete the least recently used sprite sheets until the cache is below limit_mb. -> files removed"""
    keep = {str(Path(p)) for p in protect}
    files = sorted(anim_dir().glob("*.png"), key=lambda p: p.stat().st_mtime)
    total = sum(p.stat().st_size for p in files)
    n = 0
    for p in files:
        if total <= limit_mb * 1024 ** 2:
            break
        if str(p) in keep:
            continue
        sz = p.stat().st_size
        with contextlib.suppress(OSError):
            p.unlink()
            total -= sz
            n += 1
    return n


def gallery_anim(tpls, W=None, H=None, size=ANIM_SIZE, frames=ANIM_FRAMES, fps=ANIM_FPS, bg=None, force=False,
                 limit=None, cached_only=False, threads=4, emit=None):
    """Sprite sheets for many templates. Cached ones return at once; at most `limit` new ones are rendered per
    call (the rest are listed in `pending`, so the panel can ask again: lazy batches).
    -> {anims: {id: info}, pending: [ids], errors: {id: msg}}"""
    bg_hash = _content_hash(bg) if bg else "neutral"
    anims, todo, errs = {}, [], {}
    for tp in tpls:
        w_, h_ = (W, H) if W and H else _pref(tp)
        hit = None if force else anim_cached(tp, w_, h_, size, frames, fps, bg_hash)
        if hit is not None:
            anims[tp["id"]] = _info(hit, size, frames, fps, True)
        else:
            todo.append((tp, w_, h_))
    pending = []
    if cached_only:
        pending = [tp["id"] for tp, _, _ in todo]
        todo = []
    elif limit is not None and len(todo) > int(limit):
        pending = [tp["id"] for tp, _, _ in todo[int(limit):]]
        todo = todo[:int(limit)]
    if todo:
        from ..util import ensure_free
        ensure_free(len(todo) * 2 * 1024 ** 2, anim_dir())
        with ThreadPoolExecutor(max(1, threads)) as ex:
            futs = {tp["id"]: ex.submit(anim_sheet, tp, w_, h_, size, frames, fps, bg, bg_hash, force)
                    for tp, w_, h_ in todo}
            for k, (tid, f) in enumerate(futs.items()):
                try:
                    anims[tid] = f.result()
                except Exception as e:  # noqa: BLE001 - one broken template must not stop the batch
                    errs[tid] = str(getattr(e, "msg", e))[:200]
                if emit is not None:
                    emit.progress(100 * (k + 1) / len(futs), note=f"{k + 1}/{len(futs)}")
        prune(protect=[a["png"] for a in anims.values()])
    return {"anims": anims, "pending": pending, "errors": errs}


def _pref(tpl):
    from .preview import preferred_size
    return preferred_size(tpl)


def act_gallery_anim(job, emit, timeline):
    """`gallery` with params.anim (docs/CAPTIONS_API.md 3.3.1)."""
    from ..util import workdir
    from . import preview as P
    p = job["params"]
    tpls = P.templates_for_gallery(p.get("ids"))
    if p.get("ids"):                                    # keep the caller's order (visible cards first)
        order = {TP.slug(i): k for k, i in enumerate(p["ids"])}
        tpls.sort(key=lambda t: order.get(t["id"], 1e9))
    bg, W, H = None, None, None
    if p.get("frame"):
        if not Path(p["frame"]).is_file():
            raise EngineError("NO_FRAME", tr("cap.gallery.errNoFrame"), str(p["frame"]))
        tl = timeline(job, required=False)
        bg = str(p["frame"])
        if tl is not None:
            W, H = tl.width, tl.height
    elif p.get("t") is not None and job.get("seq"):
        tl = timeline(job)
        W, H = tl.width, tl.height
        bg = str(P.frame_png(tl, float(p["t"]), workdir(job), 0.5, p.get("fit", "cover"))[0])
    if bg and not (W and H):
        from PIL import Image
        with Image.open(bg) as im:
            W, H = im.width * 2, im.height * 2
    size = tuple(int(v) for v in (p.get("size") or ANIM_SIZE))
    frames = max(4, min(32, int(p.get("frames") or ANIM_FRAMES)))
    fps = max(2.0, min(30.0, float(p.get("fps") or ANIM_FPS)))
    if fps == int(fps):
        fps = int(fps)
    emit.plan([("anim", tr("cap.gallery.stageAnim"), 1.0)])
    with emit.step("anim"):
        r = gallery_anim(tpls, W, H, size, frames, fps, bg, bool(p.get("force")), p.get("limit"),
                         bool(p.get("cached_only")), emit=emit)
    r.update({"size": list(size), "frames": frames, "fps": fps, "frame": "user" if bg else "neutral",
              "cache_mb": round(cache_size() / 1024 ** 2, 1)})
    return r


# ---------------------------------------------------------------- readability (contrast + size sanity)

def _rgb(c):
    h = _hex(c) or "#FFFFFF"
    return tuple(int(h[i:i + 2], 16) / 255 for i in (1, 3, 5))


def _alpha(c):
    return int(c[7:9], 16) / 255 if isinstance(c, str) and len(c) == 9 else 1.0


def luminance(c):
    def ch(v):
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = (ch(v) for v in _rgb(c))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _dist(a, b):
    """Plain RGB distance 0..1.73 (hue aware, unlike the WCAG ratio: yellow vs white is 1,07:1 but distinct)."""
    return sum((x - y) ** 2 for x, y in zip(_rgb(a), _rgb(b))) ** 0.5


def contrast(a, b):
    la, lb = sorted((luminance(a), luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def _best_bw(c):
    return "#111111" if contrast(c, "#111111") >= contrast(c, "#FFFFFF") else "#FFFFFF"


SIZE_CAP = {"min": 18.0, "max": 120.0, "word_max": 170.0}   # cap height px @1080 (font.size x font cap ratio)
ACCENTS = ["#FFE600", "#22E58B", "#38BDF8", "#FF3B5C", "#FF8A00", "#A78BFA", "#C2410C", "#1D4ED8", "#B91C1C",
           "#6D28D9", "#FFFFFF", "#111111"]


def _cap_ratio(family):
    from . import layout as L
    try:
        fi = L.get_font(family)
        return fi.cap_u / max(1, fi.asc_u + fi.desc_u)          # cap height per ASS size unit (libass cell)
    except Exception:  # noqa: BLE001 - unknown family: assume a typical sans
        return 0.45


def _box_bg(tpl):
    """Colour behind the text when an opaque-enough box is drawn, else None."""
    bx = tpl["box"]
    if not bx.get("enabled") or not bx.get("filled", True):
        return None
    op = bx.get("opacity")
    op = _alpha(bx.get("color")) if op is None else float(op)
    return bx.get("color") if op >= 0.55 else None


def ensure_readable(tpl):
    """Fix a generated style in place so it stays readable on any video. -> list of fixes (job language)."""
    fixes = []
    st, hl, fo = tpl["style"], tpl["highlight"], tpl["font"]
    bg = _box_bg(tpl)
    if bg is not None:
        if contrast(st["fill"], bg) < 4.5:
            st["fill"] = _best_bw(bg)
            st["gradient"] = None
            fixes.append(tr("cap.gallery.fixFill"))
        elif st.get("gradient"):
            cols = st["gradient"].get("colors") or []
            if any(contrast(c, bg) < 3 for c in cols):
                st["gradient"] = None
                fixes.append(tr("cap.gallery.fixGradient"))
        if (st.get("outline") or 0) > 0 and contrast(st["fill"], st["outline_color"]) < 2:
            st["outline"] = 0
        ref = bg
    else:
        halo = (st.get("outline") or 0) >= 2 or st.get("glow") or st.get("extrude") or st.get("long_shadow") \
            or (st.get("shadow") and st.get("shadow_blur"))
        if not halo:
            st["outline"] = 4
            st["outline_color"] = _best_bw(st["fill"])
            fixes.append(tr("cap.gallery.fixOutlineAdd"))
        elif (st.get("outline") or 0) >= 2 and contrast(st["fill"], st["outline_color"]) < 3:
            st["outline_color"] = _best_bw(st["fill"])
            fixes.append(tr("cap.gallery.fixOutlineColor"))
        # a thick stroke is what surrounds the active word; otherwise judge against the video (dark/light)
        ref = st["outline_color"] if (st.get("outline") or 0) >= 4 else _best_bw(st["fill"])
    # active word colour against what is behind it (pill, box or outline)
    behind = hl.get("pill") or ref
    if hl.get("color") and contrast(hl["color"], behind) < 3:
        best = max(ACCENTS, key=lambda c: (contrast(c, behind) >= 3, contrast(c, st["fill"]) >= 1.5,
                                           contrast(c, behind)))
        hl["color"] = best
        fixes.append(tr("cap.gallery.fixActive"))
    marked = (hl.get("pill") or hl.get("glow") or hl.get("underline") or hl.get("future_color")
              or float(hl.get("future_alpha") or 1) < 0.95 or float(hl.get("scale") or 1) > 1.04)
    text_cols = (st.get("gradient") or {}).get("colors") or [st["fill"]]
    if hl.get("color") and min(_dist(hl["color"], c) for c in text_cols) < 0.25 and not marked:
        hl["color"] = max(ACCENTS, key=lambda c: (contrast(c, behind) >= 3, _dist(c, st["fill"])))
        fixes.append(tr("cap.gallery.fixActiveDistinct"))
    if hl.get("pill") and hl.get("color") is None and contrast(st["fill"], hl["pill"]) < 3:
        hl["color"] = _best_bw(hl["pill"])
        fixes.append(tr("cap.gallery.fixActivePill"))
    if hl.get("future_alpha") is not None and float(hl["future_alpha"]) < 0.4:
        hl["future_alpha"] = 0.45
    # size sanity: visual cap height between SIZE_CAP bounds
    cap = _cap_ratio(fo["family"])
    word_mode = tpl["timing"]["mode"] == "word" or tpl["layout"].get("max_words", 6) <= 1
    hi = SIZE_CAP["word_max"] if word_mode else SIZE_CAP["max"]
    px = float(fo["size"]) * cap
    if px < SIZE_CAP["min"] or px > hi:
        fo["size"] = int(round(min(max(px, SIZE_CAP["min"]), hi) / cap))
        fixes.append(tr("cap.gallery.fixSize"))
    return fixes


# ---------------------------------------------------------------- template_mix / template_random

MIX_PARTS = {k: "cap.gallery.part." + k for k in ("font", "style", "box", "highlight", "anim", "layout")}  # ordered
_PACE = ("max_gap", "max_dur", "min_dur", "hold", "fill_gaps", "lead")


def _take(dst, src, part):
    """Copy one part group from template src into dst (both full templates)."""
    if part == "font":
        dst["font"] = copy.deepcopy(src["font"])
    elif part == "style":
        dst["style"] = copy.deepcopy(src["style"])
    elif part == "box":
        dst["box"] = copy.deepcopy(src["box"])
    elif part == "highlight":
        dst["highlight"] = copy.deepcopy(src["highlight"])
        dst["timing"]["mode"] = src["timing"]["mode"]
        dst["anim"]["word_in"] = src["anim"].get("word_in", "none")
        dst["anim"]["word_in_dur"] = src["anim"].get("word_in_dur", 0.14)
    elif part == "anim":
        for k in ("in", "in_dur", "out", "out_dur"):
            dst["anim"][k] = src["anim"][k]
    elif part == "layout":
        lay = copy.deepcopy(src["layout"])
        lay["y"] = dst["layout"].get("y", lay.get("y"))       # keep the base position (aspect)
        dst["layout"] = lay
        for k in _PACE:
            if k in src["timing"]:
                dst["timing"][k] = src["timing"][k]


def style_diff(full, base):
    """doc.style that turns template `base` into `full` (explicit nulls kept: send with doc_style replace)."""
    out = {}
    for k, v in full.items():
        if k in ("id", "source", "name", "description", "aspect", "default_for", "tags", "group") or k.startswith("_"):
            continue
        b = base.get(k)
        if isinstance(v, dict) and isinstance(b, dict):
            sub = style_diff(v, b)
            if sub:
                out[k] = sub
        elif v != b:
            out[k] = copy.deepcopy(v)
    return out


def _clean(t):
    return {k: v for k, v in t.items() if not k.startswith("_") and k not in ("source",)}


def _result(full, base_id, base, name, extra):
    fixes = ensure_readable(full)
    full["name"] = name
    st = style_diff(full, base)
    ops = [{"op": "template", "id": base_id}, {"op": "doc_style", "style": st, "replace": True}]
    return dict({"template": base_id, "style": st, "full": _clean(full), "name": name, "fixes": fixes, "ops": ops,
                 "summary": tr("cap.gallery.sumFixes", name=name, n=len(fixes)) if fixes else name}, **extra)


def template_mix(parts, base=None, W=1920, H=1080):
    """New draft look from groups of different templates. parts = {font|style|box|highlight|anim|layout: id}."""
    if not isinstance(parts, dict) or not parts:
        raise EngineError("BAD_PARAMS", tr("cap.gallery.errMixEmpty"))
    bad = [k for k in parts if k not in MIX_PARTS]
    if bad:
        raise EngineError("BAD_PARAMS", tr("cap.gallery.errMixPart", parts=", ".join(bad)),
                          tr("cap.gallery.errMixPartHint"))
    base_id = TP.slug(base or next(iter(parts.values())))
    if TP.raw(base_id)[0] is None:
        raise EngineError("BAD_TEMPLATE", tr("cap.gallery.errNoTemplate", id=base_id))
    base_t = TP.load(base_id, W, H)
    full = copy.deepcopy(base_t)
    names = []
    for part in MIX_PARTS:                              # fixed order: font before style etc.
        tid = parts.get(part)
        if not tid:
            continue
        tid = TP.slug(tid)
        if TP.raw(tid)[0] is None:
            raise EngineError("BAD_TEMPLATE", tr("cap.gallery.errNoTemplate", id=tid))
        src = TP.load(tid, W, H)
        _take(full, src, part)
        names.append(TP.display_name(src) or tid)
    name = tr("cap.gallery.mixName", names=" + ".join(dict.fromkeys(names or [TP.display_name(base_t) or base_id])))
    return _result(full, base_id, base_t, name[:60], {"parts": {k: TP.slug(v) for k, v in parts.items() if v}})


# curated pieces for template_random (each stays readable on its own; ensure_readable checks the combination)
RANDOM_FONTS = ["Montserrat Black", "Poppins Black", "Anton", "Archivo Black", "Rubik Black", "Lilita One",
                "Titan One", "Bebas Neue", "Russo One", "Kanit Black", "Outfit Black", "Nunito Black",  # i18n-ignore
                "Plus Jakarta Sans ExtraBold", "Inter Black", "Barlow Condensed Black", "Passion One",
                "Paytone One", "Sora ExtraBold", "Bangers", "Fredoka Bold"]
PALETTES = [  # (fill, accent, outline)
    ("#FFFFFF", "#FFE600", "#000000"), ("#FFFFFF", "#22E58B", "#000000"), ("#FFFFFF", "#38BDF8", "#0B1220"),
    ("#FFFFFF", "#FF3B5C", "#000000"), ("#FFF7E6", "#FF8A00", "#1A0A00"), ("#FFFFFF", "#A78BFA", "#1E0B3A"),
    ("#FFE600", "#FFFFFF", "#000000"), ("#B6FF3B", "#FFFFFF", "#062B00"),
]
RANDOM_EFFECTS = [
    (None, 4), ("extrude", 2), ("outline2", 2), ("gradient", 2), ("long_shadow", 1), ("glow", 1),
]
RANDOM_BOXES = [(None, 6), ("line", 2), ("marker", 1), ("banner", 1), ("sticker", 1), ("page", 1)]
RANDOM_HL = [("color", 3), ("scale", 3), ("box_pop", 2), ("marker", 1), ("glow", 1), ("wiggle", 1),
             ("underline", 1), ("reveal", 1)]
RANDOM_ANIM = [("pop", "fade"), ("scale", "fade"), ("bounce", "scale"), ("slide_up", "fade"), ("zoom_blur", "blur"),
               ("blur", "blur"), ("fade", "fade"), ("rotate", "pop"), ("slide_left", "fade")]
GRADIENTS = [["#FFF35C", "#FF8A00"], ["#67E8F9", "#A855F7"], ["#FFD1E8", "#C4B5FD"], ["#FFFFFF", "#9CA3AF"],
             ["#B6FF3B", "#22C55E"], ["#FFB347", "#FF5E8A"]]


def _pick(rng, weighted):
    items, w = zip(*weighted)
    return rng.choices(items, weights=w, k=1)[0]


def template_random(seed=None, W=1080, H=1920, base=None, keep=()):
    """Curated random look that stays readable. keep = parts of the base template to keep as they are."""
    from . import layout as L
    seed = int(seed) if seed is not None else random.randrange(1, 10 ** 9)
    rng = random.Random(seed)
    portrait = L.aspect_kind(W, H) == "portrait"
    base_id = TP.slug(base) if base else TP.default_id(W, H)
    if TP.raw(base_id)[0] is None:
        raise EngineError("BAD_TEMPLATE", tr("cap.gallery.errNoTemplate", id=base_id))
    base_t = TP.load(base_id, W, H)
    t = copy.deepcopy(base_t)
    keep = set(keep or ())
    fill, accent, outline = rng.choice(PALETTES)
    if "font" not in keep:
        fam = rng.choice(RANDOM_FONTS)
        cap = _cap_ratio(fam)
        target = rng.uniform(40, 48) if portrait else rng.uniform(24, 29)     # cap height px @1080
        t["font"] = dict(t["font"], family=fam, size=int(round(target / cap)), letter_spacing=0, skew=0.0,
                         uppercase=portrait or rng.random() < 0.3, case=None, italic=False,
                         line_spacing=1.05 if portrait else 1.2, punct="strip" if portrait else "soft")
        if t["font"]["uppercase"]:
            t["font"]["case"] = "upper"
    if "style" not in keep:
        st = t["style"]
        st.update({"fill": fill, "outline": rng.choice([5, 6, 8]) if portrait else rng.choice([3, 4]),
                   "outline_color": outline, "outline_opacity": None, "shadow": rng.choice([0, 4, 6]),
                   "shadow_x": 0, "shadow_y": None, "shadow_blur": 0, "shadow_color": "#000000B3", "glow": None,
                   "color_cycle": None, "outline2": 0, "gradient": None, "extrude": None, "long_shadow": None})
        st["shadow_y"] = st["shadow"] or None
        eff = _pick(rng, RANDOM_EFFECTS)
        if eff == "extrude":
            st["extrude"] = {"depth": rng.choice([8, 10, 12]), "angle": rng.choice([60, 70, 90]),
                             "color": outline, "color_end": None, "steps": 0}
        elif eff == "outline2":
            st["outline2"] = rng.choice([4, 6])
            st["outline2_color"] = accent if contrast(accent, outline) >= 3 else "#FFFFFF"
        elif eff == "gradient":
            st["gradient"] = {"colors": rng.choice(GRADIENTS), "dir": rng.choice(["vertical", "horizontal"]),
                              "span": "word", "bands": 6}
        elif eff == "long_shadow":
            st["long_shadow"] = {"length": 40, "angle": 45, "color": "#000000CC", "fade": True, "steps": 0}
        elif eff == "glow":
            st["glow"] = {"color": accent + "B3", "width": 10, "blur": 12, "active_only": False, "strength": 1.2}
    if "box" not in keep:
        shape = _pick(rng, RANDOM_BOXES if portrait else [(None, 4), ("line", 4), ("page", 2), ("marker", 1)])
        if shape is None:
            t["box"] = copy.deepcopy(TP.DEFAULTS["box"])
        else:
            patch = (TP.BOX_SHAPES.get(shape) or TP.BOX_PRESETS["line"])["patch"]["box"]
            t["box"] = L.deep_merge(TP.DEFAULTS["box"], patch)
            t["box"]["color"] = rng.choice(["#000000", "#0B0F19", "#FFFFFF", "#FFD60A", "#E11D48", "#7C3AED"])
            t["box"]["opacity"] = 0.85 if shape in ("line", "page") else 1.0
            t["style"]["outline"] = 0
            t["style"]["outline2"] = 0
            t["style"]["extrude"] = None
            t["style"]["long_shadow"] = None
            t["style"]["fill"] = _best_bw(t["box"]["color"])
    if "highlight" not in keep:
        hid = _pick(rng, RANDOM_HL)
        patch = copy.deepcopy(TP.HIGHLIGHT_PRESETS[hid]["patch"])
        hl = patch.get("highlight") or {}
        if "color" in hl and hl["color"] not in (None, "#FFFFFF"):
            hl["color"] = accent
        if hid == "underline":
            hl["underline"] = accent
        t["highlight"] = L.deep_merge(TP.DEFAULTS["highlight"], hl)
        t["timing"]["mode"] = (patch.get("timing") or {}).get("mode", "karaoke")
        t["anim"]["word_in"] = (patch.get("anim") or {}).get("word_in", "none")
    if "anim" not in keep:
        a_in, a_out = rng.choice(RANDOM_ANIM)
        t["anim"].update({"in": a_in, "out": a_out, "in_dur": 0.3 if a_in == "bounce" else 0.18, "out_dur": 0.1})
    if "layout" not in keep:
        lay = t["layout"]
        if portrait:
            lay.update({"max_words": rng.choice([2, 3, 3, 4]), "max_chars": 16, "max_lines": 2,
                        "rotate": rng.choice([0, 0, 0, -3, 3]), "rotate_jitter": 0})
        else:
            lay.update({"max_words": rng.choice([6, 7, 8]), "max_chars": 30, "max_lines": 2, "rotate": 0,
                        "rotate_jitter": 0})
    name = tr("cap.gallery.randomName", num=f"{seed % 10000:04d}")
    return _result(t, base_id, base_t, name, {"seed": seed})
