"""Style from image ("Tiru gaya dari gambar"): turn a screenshot of a caption the user likes into a draft caption
style for our engine (docs/CAPTIONS_API.md section 12).

Pipeline
  1. load + downscale the image (path, bytes or data URL; a copy is kept under <workdir>/stylist/)
  2. LOCAL analysis (always, no AI, no OCR): find the caption block (edge-dense word blobs, biggest text wins),
     background box (uniform ring + bounded flood fill), fill / active-word colours (k-means over low-gradient
     glyph pixels), stroke (colour + width from dilation rings around the fill), case (cap-line coverage of the
     glyph tops), lines, words per line (column gaps), weight (distance transform), width (glyph boxes), size
     (cap height), position (block centre when the image looks like a full frame)
  3. AI (optional, vision slot of ac.ai.client, one request, cached by image hash): a strict JSON description of
     observable attributes (colours, stroke, shadow, background, case, font category / weight / width, position,
     size class, words per line, highlight style, animation guess, visible text)
  4. merge: measured colours win when AI and local agree (they are exact), AI wins for semantic attributes
     (case, font category, highlight, animation) and for colours when they disagree strongly
  5. map deterministically onto our style schema: closest built-in template as `base` + an explicit doc.style
     patch (font from the catalog by category + weight + width, sizes in px@1080, colours, box, highlight, layout,
     timing, animation; Gaya Pro effects off unless seen)
  6. render our draft over a blurred copy of their image at the same aspect (side-by-side compare)

Every attribute carries a confidence 0..1 and its source (ai / lokal / ai+lokal). Without AI the result is
flagged `fidelity: "rendah"`.
"""
from __future__ import annotations

import base64
import contextlib
import io
import math
import re
from pathlib import Path

import numpy as np

from ..i18n import tr

STYLIST_V = 1
LOCAL_MAX = 1100           # px, long side for the local analysis
KEEP_INPUTS = 12           # copies of user images kept in <workdir>/stylist
PROMPT_V = 2

# attribute id -> locale key of its label (translated per job language by attr_label)
ATTR_KEYS = {
    "fill": "cap.stylist.attrFill", "active": "cap.stylist.attrActive", "stroke": "cap.stylist.attrStroke",
    "stroke_width": "cap.stylist.attrStrokeWidth", "shadow": "cap.stylist.attrShadow", "box": "cap.stylist.attrBox",
    "box_color": "cap.stylist.attrBoxColor", "case": "cap.stylist.attrCase", "font": "cap.stylist.attrFont",
    "weight": "cap.stylist.attrWeight", "size": "cap.stylist.attrSize", "position": "cap.stylist.attrPosition",
    "words": "cap.stylist.attrWords", "lines": "cap.stylist.attrLines", "highlight": "cap.stylist.attrHighlight",
    "anim": "cap.stylist.attrAnim",
}


def attr_label(k):
    """User-facing label of an attribute id in the job language (unknown ids come back as they are)."""
    return tr(ATTR_KEYS[k]) if k in ATTR_KEYS else k
ANIM_IN = ("none", "fade", "pop", "scale", "zoom", "slide_up", "slide_down", "slide_left", "slide_right", "bounce",
           "blur", "zoom_blur", "rotate", "drop", "typewriter")
FRAME_ASPECTS = (16 / 9, 9 / 16, 4 / 3, 3 / 4, 1.0, 4 / 5, 21 / 9, 2292 / 960, 2.35, 1.85)


# ====================================================================== image io

def clipboard_image():
    """PNG bytes of the image on the Windows clipboard (a copied picture, or a copied image file)."""
    from ..util import EngineError
    try:
        from PIL import ImageGrab
        g = ImageGrab.grabclipboard()
    except Exception as e:  # noqa: BLE001 - clipboard locked / not available
        raise EngineError("NO_CLIPBOARD", tr("cap.stylist.clipboardRead"), str(e)[:200]) from None
    if isinstance(g, list):
        g = next((x for x in g if str(x).lower().endswith((".png", ".jpg", ".jpeg", ".webp", ".bmp"))), None)
        if g:
            return str(g)
    if g is None or not hasattr(g, "save"):
        raise EngineError("NO_CLIPBOARD", tr("cap.stylist.clipboardEmpty"), tr("cap.stylist.clipboardEmptyHint"))
    buf = io.BytesIO()
    g.save(buf, "PNG")
    return buf.getvalue()


def _decode(src):
    """bytes of a path / bytes / data URL."""
    if isinstance(src, (bytes, bytearray)):
        return bytes(src)
    s = str(src)
    if s.startswith("data:"):
        try:
            return base64.b64decode(s.split(",", 1)[1])
        except (IndexError, ValueError) as e:
            raise ValueError(f"data URL rusak: {e}") from None
    return Path(s).read_bytes()


def load_image(src):
    """PIL RGB image (transparent pixels composited over mid grey)."""
    from PIL import Image
    im = Image.open(io.BytesIO(_decode(src)))
    im.load()
    if im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info):
        im = im.convert("RGBA")
        bg = Image.new("RGBA", im.size, (90, 90, 90, 255))
        bg.alpha_composite(im)
        im = bg
    return im.convert("RGB")


def keep_input(im, folder):
    """Save a downscaled copy (<= 1600 px) of the user's image -> (path, hash)."""
    from ..util import data_hash
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    small = im.copy()
    small.thumbnail((1600, 1600))
    buf = io.BytesIO()
    small.save(buf, "PNG", optimize=False)
    raw = buf.getvalue()
    h = data_hash([len(raw), base64.b64encode(raw[::97]).decode("ascii"), small.size])[:16]
    out = folder / f"src_{h}.png"
    if not out.is_file():
        out.write_bytes(raw)
    olds = sorted(folder.glob("src_*.png"), key=lambda p: p.stat().st_mtime, reverse=True)
    for p in olds[KEEP_INPUTS:]:
        with contextlib.suppress(OSError):
            p.unlink()
    return out, h


# ====================================================================== colour helpers

def hex_of(rgb):
    r, g, b = (int(round(max(0, min(255, float(v))))) for v in list(rgb)[:3])
    return f"#{r:02X}{g:02X}{b:02X}"


def rgb_of(hx):
    s = str(hx or "").strip().lstrip("#")
    if not re.fullmatch(r"[0-9a-fA-F]{6}([0-9a-fA-F]{2})?", s):
        raise ValueError(f"bad colour {hx!r}")
    return int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16)


def norm_hex(hx):
    """'#rgb' / 'rrggbb' / '#RRGGBBAA' -> '#RRGGBB' or None."""
    if not isinstance(hx, str):
        return None
    s = hx.strip().lstrip("#")
    if re.fullmatch(r"[0-9a-fA-F]{3}", s):
        s = "".join(c * 2 for c in s)
    if re.fullmatch(r"[0-9a-fA-F]{6}([0-9a-fA-F]{2})?", s):
        return "#" + s[:6].upper()
    return None


def lab(rgb):
    """sRGB uint8 (..., 3) -> CIELAB (D65) float32."""
    c = np.asarray(rgb, dtype=np.float32) / 255.0
    c = np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)
    m = np.array([[0.4124, 0.3576, 0.1805], [0.2126, 0.7152, 0.0722], [0.0193, 0.1192, 0.9505]], np.float32)
    xyz = c @ m.T / np.array([0.95047, 1.0, 1.08883], np.float32)
    f = np.where(xyz > 0.008856, np.cbrt(xyz), 7.787 * xyz + 16 / 116)
    return np.stack([116 * f[..., 1] - 16, 500 * (f[..., 0] - f[..., 1]), 200 * (f[..., 1] - f[..., 2])], -1)


def delta_e(a, b):
    """CIE76 distance between two colours (hex strings or RGB triples)."""
    a = rgb_of(a) if isinstance(a, str) else a
    b = rgb_of(b) if isinstance(b, str) else b
    la, lb = lab(np.array([a], np.uint8))[0], lab(np.array([b], np.uint8))[0]
    return float(np.linalg.norm(la - lb))


def luminance(rgb):
    c = np.asarray(rgb, np.float32) / 255.0
    c = np.where(c <= 0.03928, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)
    return float(0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2])


def kmeans(x, k, iters=12, seed=7):
    """Deterministic k-means (k-means++ init) on (n, d) float rows -> (centers, labels)."""
    n = len(x)
    if n == 0:
        return np.zeros((0, x.shape[1] if x.ndim == 2 else 3), np.float32), np.zeros(0, int)
    k = max(1, min(k, n))
    rng = np.random.default_rng(seed)
    if n > 12000:
        x_fit = x[rng.choice(n, 12000, replace=False)]
    else:
        x_fit = x
    cen = [x_fit[rng.integers(len(x_fit))]]
    for _ in range(1, k):
        d = np.min(((x_fit[:, None, :] - np.array(cen)[None]) ** 2).sum(-1), 1)
        if d.sum() <= 0:
            break
        cen.append(x_fit[rng.choice(len(x_fit), p=d / d.sum())])
    cen = np.array(cen, np.float32)
    for _ in range(iters):
        lab_ = np.argmin(((x_fit[:, None, :] - cen[None]) ** 2).sum(-1), 1)
        new = np.array([x_fit[lab_ == i].mean(0) if (lab_ == i).any() else cen[i] for i in range(len(cen))], np.float32)
        if np.allclose(new, cen, atol=0.05):
            cen = new
            break
        cen = new
    labels = np.argmin(((x[:, None, :] - cen[None]) ** 2).sum(-1), 1)
    return cen, labels


# ====================================================================== local analysis (no AI, no OCR)

def _attr(value, conf, src="lokal"):
    return {"value": value, "conf": round(float(max(0.0, min(1.0, conf))), 2), "src": src}


def frame_like(w, h):
    r = w / max(1, h)
    return any(abs(r - a) / a < 0.03 for a in FRAME_ASPECTS)


def _blobs(L, chroma):
    """Word-sized blobs of strong edges -> (None, list of dict, gradient magnitude). Two passes: the usual edge
    threshold, and a hard one (outlined captions have the hardest edges in a frame) so a caption that touches a
    busy picture is not swallowed by it."""
    from scipy import ndimage as ndi
    h, w = L.shape
    mag = np.hypot(ndi.sobel(L, 0), ndi.sobel(L, 1)) + 0.6 * np.hypot(ndi.sobel(chroma[..., 0], 0),
                                                                       ndi.sobel(chroma[..., 0], 1))
    kx = max(3, int(round(min(h, w) / 45)))
    short = min(h, w)
    out = []
    for k, thr in enumerate((max(40.0, float(np.percentile(mag, 88))), 230.0)):
        strong = mag > thr
        if strong.sum() < 20:
            continue
        closed = ndi.binary_closing(strong, structure=np.ones((max(3, kx // 2), kx)), iterations=1)
        closed = ndi.binary_fill_holes(closed)
        lbl, n = ndi.label(closed)
        if not n:
            continue
        objs = ndi.find_objects(lbl)
        cnt = ndi.sum(mag > 40.0, lbl, index=np.arange(1, n + 1))
        for i, sl in enumerate(objs):
            if sl is None:
                continue
            y0, y1, x0, x1 = sl[0].start, sl[0].stop, sl[1].start, sl[1].stop
            bh, bw = y1 - y0, x1 - x0
            if bh < 0.022 * short or bh > 0.42 * short or bw < 0.5 * bh or bw > 0.98 * w:
                continue
            dens = cnt[i] / max(1, bh * bw)
            if dens < 0.06:
                continue
            sub = mag[y0:y1, x0:x1][lbl[y0:y1, x0:x1] == i + 1]
            contrast = float(np.percentile(sub, 92)) if sub.size else 0.0
            b = {"i": (k + 1) * 100000 + i + 1, "y0": y0, "y1": y1, "x0": x0, "x1": x1, "h": bh, "w": bw,
                 "e": float(cnt[i]), "c": contrast,
                 "score": float(cnt[i]) * math.sqrt(bh) * (min(contrast, 400.0) / 400.0) ** 4}
            dup = None
            for j, o in enumerate(out):
                iy = max(0, min(y1, o["y1"]) - max(y0, o["y0"]))
                ix = max(0, min(x1, o["x1"]) - max(x0, o["x0"]))
                inter = iy * ix
                if inter > 0.5 * min(bh * bw, o["h"] * o["w"]):
                    dup = j
                    break
            if dup is None:
                out.append(b)
            elif b["h"] * b["w"] < out[dup]["h"] * out[dup]["w"] and b["score"] > 0.5 * out[dup]["score"]:
                out[dup] = b          # the tighter blob of the hard pass wins (caption cut loose from a picture)
    return None, out, mag


def _text_block(blobs, W, H):
    """Group blobs into lines and pick the caption block (the biggest, densest text) -> bbox + lines."""
    if not blobs:
        return None
    blobs = sorted(blobs, key=lambda b: -b["score"])
    used = set()
    lines = []
    for b in blobs:
        if b["i"] in used:
            continue
        line = [b]
        used.add(b["i"])
        changed = True
        while changed:
            changed = False
            y0 = min(x["y0"] for x in line)
            y1 = max(x["y1"] for x in line)
            lh = y1 - y0
            xs0 = min(x["x0"] for x in line)
            xs1 = max(x["x1"] for x in line)
            for c in blobs:
                if c["i"] in used:
                    continue
                ov = min(y1, c["y1"]) - max(y0, c["y0"])
                if ov < 0.5 * min(lh, c["h"]) or not (0.55 * lh <= c["h"] <= 1.8 * lh):
                    continue
                gap = max(c["x0"] - xs1, xs0 - c["x1"])
                if gap > 2.6 * lh:
                    continue
                line.append(c)
                used.add(c["i"])
                changed = True
        ln = {"y0": min(x["y0"] for x in line), "y1": max(x["y1"] for x in line),
              "x0": min(x["x0"] for x in line), "x1": max(x["x1"] for x in line),
              "score": sum(x["score"] for x in line), "n": len(line)}
        # captions sit centred, in the middle / lower part of the frame
        cx, cy = (ln["x0"] + ln["x1"]) / 2 / W, (ln["y0"] + ln["y1"]) / 2 / H
        ln["score"] *= (1.5 if abs(cx - 0.5) < 0.12 else 1.0) * (1.3 if 0.4 <= cy <= 0.93 else 1.0)
        lines.append(ln)
    lines.sort(key=lambda ln: -ln["score"])
    seed = lines[0]
    block = [seed]
    for ln in lines[1:]:
        lh = seed["y1"] - seed["y0"]
        h2 = ln["y1"] - ln["y0"]
        if not (0.6 * lh <= h2 <= 1.6 * lh):
            continue
        by0, by1 = min(x["y0"] for x in block), max(x["y1"] for x in block)
        gap = max(ln["y0"] - by1, by0 - ln["y1"])
        hov = min(ln["x1"], max(x["x1"] for x in block)) - max(ln["x0"], min(x["x0"] for x in block))
        if gap <= 0.9 * lh and hov > 0 and len(block) < 4 and ln["score"] > 0.15 * seed["score"]:
            block.append(ln)
    block.sort(key=lambda ln: ln["y0"])
    return {"y0": min(x["y0"] for x in block), "y1": max(x["y1"] for x in block),
            "x0": min(x["x0"] for x in block), "x1": max(x["x1"] for x in block), "lines": block,
            "lh": float(np.median([x["y1"] - x["y0"] for x in block]))}


def _ring(shape, box, a, b):
    """Mask of the rectangular ring between box grown by a and by b px."""
    h, w = shape
    y0, y1, x0, x1 = box
    m = np.zeros(shape, bool)
    m[max(0, y0 - b):min(h, y1 + b), max(0, x0 - b):min(w, x1 + b)] = True
    m[max(0, y0 - a):min(h, y1 + a), max(0, x0 - a):min(w, x1 + a)] = False
    return m


def _runs(row):
    """[(start, end)] runs of True in a 1-D bool array."""
    d = np.diff(np.concatenate([[0], row.astype(np.int8), [0]]))
    return list(zip(np.where(d == 1)[0], np.where(d == -1)[0]))


def analyze_local(im):
    """Measured attributes of the caption in a PIL image -> {attr: {value, conf, src}, '_block': ...}."""
    from scipy import ndimage as ndi
    W0, H0 = im.size
    sc = min(1.0, LOCAL_MAX / max(W0, H0))
    small = im.resize((max(8, int(W0 * sc)), max(8, int(H0 * sc)))) if sc < 1 else im
    rgb = np.asarray(small, np.uint8)
    h, w = rgb.shape[:2]
    lb = lab(rgb)
    L = lb[..., 0]
    chroma = lb[..., 1:]
    full = frame_like(W0, H0)
    out = {"_frame": full, "_size": [W0, H0]}
    _, blobs, mag = _blobs(L, chroma)
    blk = _text_block(blobs, w, h)
    if blk is None:
        out["_block"] = None
        return out
    lh = blk["lh"]
    by0, by1, bx0, bx1 = blk["y0"], blk["y1"], blk["x0"], blk["x1"]
    pad = int(round(0.35 * lh))
    Y0, Y1, X0, X1 = max(0, by0 - pad), min(h, by1 + pad), max(0, bx0 - pad), min(w, bx1 + pad)

    # ---------------- local background around the block: flat (one colour) or busy (picture)?
    ring = _ring((h, w), (by0, by1, bx0, bx1), max(2, int(0.15 * lh)), max(5, int(0.45 * lh)))
    ring_px = lb[ring]
    uniform, bg_lab = False, None
    if len(ring_px) > 20:
        med = np.median(ring_px, 0)
        uniform = float((np.linalg.norm(ring_px - med, axis=1) < 9).mean()) >= 0.72
        if uniform:
            bg_lab = med
    blk_mask = np.zeros((h, w), bool)
    blk_mask[Y0:Y1, X0:X1] = True
    area = float(blk_mask.sum())
    # ---------------- box: one flat colour filling a big part of the block that is not the outside background
    box = None
    bpx = lb[blk_mask]
    cen_b, lab_b = kmeans(bpx, 4)
    cnt_b = np.bincount(lab_b, minlength=len(cen_b)).astype(float)
    for i in np.argsort(cnt_b)[::-1][:2]:
        c = cen_b[i]
        if cnt_b[i] / area < 0.28:
            break
        if bg_lab is not None and np.linalg.norm(c - bg_lab) < 9:
            continue
        near = np.linalg.norm(lb - c, axis=-1) < 10
        lbl, n = ndi.label(near & ndi.binary_dilation(blk_mask, iterations=max(2, int(0.6 * lh))))
        if not n:
            continue
        sizes = np.bincount(lbl.ravel())
        sizes[0] = 0
        keep = np.where(sizes >= 0.02 * area)[0]
        region = np.isin(lbl, keep)
        filled = ndi.binary_fill_holes(region)
        ys, xs = np.where(region)
        if not len(ys):
            continue
        ext = (int(ys.min()), int(ys.max()) + 1, int(xs.min()), int(xs.max()) + 1)
        er = ndi.binary_erosion(region)
        flat = float((mag[er if er.any() else region] < 30).mean())
        # a box has a sharp outer edge; a glow / soft shadow fades out
        rim = ndi.binary_dilation(filled) & ~filled
        sharp = float(np.median(mag[rim])) if rim.any() else 0.0
        if flat > 0.6 and sharp > 18 and (ext[1] - ext[0]) < 0.6 * h and region.sum() < 0.5 * h * w and \
                (filled & ~region & blk_mask).sum() > 0.04 * area:
            box = {"region": region, "filled": filled, "ext": ext, "lab": c}
            break
    out_bg_lab = bg_lab
    if box is not None:
        bg_lab = box["lab"]
        uniform = True
        # the text block = what is inside the box
        inside = box["filled"] & ~box["region"]
        ys, xs = np.where(inside)
        if len(ys):
            by0, by1, bx0, bx1 = int(ys.min()), int(ys.max()) + 1, int(xs.min()), int(xs.max()) + 1
            pad = max(2, int(0.15 * lh))
            Y0, Y1, X0, X1 = max(0, by0 - pad), min(h, by1 + pad), max(0, bx0 - pad), min(w, bx1 + pad)
            blk_mask = np.zeros((h, w), bool)
            blk_mask[Y0:Y1, X0:X1] = True
    # ---------------- ink = block pixels that are not background
    if box is not None:
        ink = blk_mask & box["filled"] & (np.linalg.norm(lb - bg_lab, axis=-1) > 16)
    elif uniform:
        ink = blk_mask & (np.linalg.norm(lb - bg_lab, axis=-1) > 16)
    else:
        rc, rl = kmeans(ring_px, 5)
        rcnt = np.bincount(rl, minlength=len(rc)) / max(1, len(rl))
        bgc = rc[rcnt >= 0.15]
        dmin = np.min(np.linalg.norm(lb[..., None, :] - bgc[None, None], axis=-1), -1) if len(bgc) else 99
        ink = blk_mask & (dmin > 14)
    ink = ndi.binary_opening(ink, iterations=1) if ink.sum() > 400 else ink
    depth = ndi.distance_transform_edt(ink)
    core = ink & (mag < max(30.0, float(np.percentile(mag[blk_mask], 60))))
    if core.sum() < 30:
        core = ink
    pts = lb[core]
    if len(pts) < 30:
        out["_block"] = {"bbox": [bx0 / w, by0 / h, bx1 / w, by1 / h]}
        return out
    cen, labs = kmeans(pts, 4)
    counts = np.bincount(labs, minlength=len(cen)).astype(float)
    dep = np.array([depth[core][labs == i].mean() if counts[i] else 0 for i in range(len(cen))])
    frac = counts / counts.sum()
    rgb_cen = [np.median(rgb[core][labs == i], 0) if counts[i] else np.array([128, 128, 128]) for i in range(len(cen))]
    cand = [i for i in range(len(cen)) if frac[i] >= 0.1] or [int(np.argmax(counts))]
    fi = max(cand, key=lambda i: dep[i] * math.sqrt(frac[i]))
    # a colour that is wrapped by another one is the fill (the wrapper is the outline)
    cand_w = [i for i in range(len(cen)) if frac[i] >= 0.06]
    masks = {i: ink & (np.linalg.norm(lb - cen[i], axis=-1) < 16) for i in cand_w}
    wrapped = []
    for i in cand_w:
        bi = masks[i] & ~ndi.binary_erosion(masks[i])
        nb = ndi.binary_dilation(bi, iterations=2) & ~masks[i]
        if nb.sum() < 10:
            continue
        for j in cand_w:
            if j == i:
                continue
            share = float((nb & masks[j]).sum()) / float(nb.sum())
            if share >= 0.4 and np.linalg.norm(cen[i] - cen[j]) > 30 and frac[j] >= 0.08:
                neutral = 1.3 if float(np.hypot(cen[i][1], cen[i][2])) < 20 else 1.0
                wrapped.append((share * frac[i] * neutral, i, j))
    wrapper = None
    if wrapped:
        _, fi, wrapper = max(wrapped)
    fill_mask = ink & (np.linalg.norm(lb - cen[fi], axis=-1) < 16)
    out["fill"] = _attr(hex_of(rgb_cen[fi]), 0.8 if uniform else 0.55)
    # colour right around the fill (outline / shadow candidate)
    band0 = ndi.binary_dilation(fill_mask, iterations=max(2, int(0.08 * lh))) & ~fill_mask & ink
    edge_lab = np.median(lb[band0], 0) if band0.sum() > 20 else None
    # ---------------- active word: another deep, distinct colour (text colour or a pill behind the word)
    active_mask = np.zeros_like(fill_mask)
    pill = False
    for j in np.argsort(-frac):
        j = int(j)
        if j == fi or j == wrapper or frac[j] < 0.03:
            continue
        if edge_lab is not None and np.linalg.norm(cen[j] - edge_lab) < 25 and dep[j] < 0.8 * dep[fi]:
            continue
        de = float(np.linalg.norm(cen[j] - cen[fi]))
        chroma_j = float(np.hypot(cen[j][1], cen[j][2]))
        if de < 28 or (chroma_j < 22 and abs(cen[j][0] - cen[fi][0]) < 40):
            continue
        m = ink & (np.linalg.norm(lb - cen[j], axis=-1) < 16)
        hole = ndi.binary_fill_holes(ndi.binary_closing(m, iterations=2)) & ~m
        # a pill holds part of the fill (one word), an outline would hold all of it
        inner = (hole & fill_mask).sum()
        is_pill = (inner > 0.25 * max(1, hole.sum()) and hole.sum() > 0.1 * m.sum()
                   and inner < 0.6 * fill_mask.sum() and dep[j] >= 0.5 * dep[fi])
        if dep[j] >= 0.6 * dep[fi] or is_pill:
            out["active"] = _attr(hex_of(rgb_cen[j]), 0.6 if uniform else 0.45)
            active_mask = m
            pill = is_pill
            break
    out["highlight"] = _attr("pill" if pill else "color" if active_mask.any() else "none", 0.45 if pill else 0.35)
    text_mask = fill_mask | (active_mask if not pill else np.zeros_like(active_mask))
    # ---------------- stroke / shadow: a shallow colour around the glyphs
    short = min(h, w)
    out["_stroke_rel"] = 0.0
    out["stroke"] = _attr(None, 0.5 if uniform else 0.3)
    out["stroke_width"] = _attr(0, 0.5 if uniform else 0.3)
    out["shadow"] = _attr("none", 0.3)
    glyph = fill_mask | active_mask
    edge_band = ndi.binary_dilation(glyph, iterations=max(2, int(0.25 * lh))) & ~glyph & ink
    if edge_band.sum() > 20:
        epx = lb[edge_band]
        med = np.median(epx, 0)
        coh = float((np.linalg.norm(epx - med, axis=1) < 22).mean())
        # anti-aliased edge pixels lie on the fill -> background line in Lab: not an outline
        aa = False
        if bg_lab is not None:
            seg = bg_lab - cen[fi]
            tt = float(np.clip(np.dot(med - cen[fi], seg) / max(1e-6, float(np.dot(seg, seg))), 0, 1))
            aa = float(np.linalg.norm(med - (cen[fi] + tt * seg))) < 12 and 0.15 < tt < 0.85
        if coh > 0.5 and np.linalg.norm(med - cen[fi]) > 30 and not aa:
            s_mask = edge_band & (np.linalg.norm(lb - med, axis=-1) < 24)
            # symmetric ring = outline; mostly below / right = drop shadow
            k = max(2, int(0.12 * lh))
            below = (s_mask & np.roll(glyph, k, 0)).sum()
            above = (s_mask & np.roll(glyph, -k, 0)).sum()
            sym = min(above, below) / max(1, max(above, below))
            col = np.median(rgb[s_mask], 0) if s_mask.any() else np.zeros(3)
            # width = stroke area / glyph perimeter (robust to touching letters and corners)
            gb = fill_mask & ~ndi.binary_erosion(fill_mask)
            s_near = s_mask & ndi.binary_dilation(fill_mask, iterations=max(2, int(0.2 * lh)))
            sw_px = float(s_near.sum()) / max(1.0, float(gb.sum())) * 0.92 if gb.any() else 0.0
            soft = float((mag[s_mask] > 40).mean()) < 0.25 if s_mask.any() else False
            if sym > 0.45 and sw_px >= 0.8:
                out["stroke"] = _attr(hex_of(col), 0.65 if uniform else 0.45)
                out["stroke_width"] = _attr(round(sw_px / short * 1080, 1) if full else round(sw_px / max(1.0, lh), 3),
                                            0.55 if full else 0.3)
                out["_stroke_rel"] = sw_px / max(1.0, lh)
            elif below > 2 * max(1, above):
                out["shadow"] = _attr("soft" if soft else "hard", 0.45)
                out["stroke"] = _attr(None, 0.45)
    # ---------------- box attributes
    if box is not None:
        reg = box["region"]
        box_rgb = np.median(rgb[reg], 0)
        per = "line"
        ln = blk["lines"]
        lbl_r, nr = ndi.label(reg)
        big = [s for s in np.bincount(lbl_r.ravel())[1:] if s >= 0.02 * area]
        if len(big) >= 3 or (len(big) >= 2 and len(ln) == 1):
            per = "word"
        elif len(ln) >= 2:
            per = "page" if len(big) <= 1 else "line"
        opa = 1.0
        if out_bg_lab is not None:
            outer = _ring((h, w), box["ext"], 2, max(6, int(0.4 * lh))) & ~reg
            if outer.any():
                yb, yo = luminance(box_rgb), luminance(np.median(rgb[outer], 0))
                if yb < 0.25 and yo > yb + 0.01:
                    opa = max(0.45, min(1.0, 1 - yb / max(yo, 1e-3)))
        col = np.array([0, 0, 0]) if opa < 0.95 and luminance(box_rgb) < 0.08 else box_rgb
        out["box"] = _attr(per, 0.7)
        out["box_color"] = _attr(hex_of(col), 0.65)
        out["box_opacity"] = _attr(round(opa, 2), 0.4)
    else:
        out["box"] = _attr("none", 0.65 if uniform else 0.45)
    rgb_bg = None
    if out_bg_lab is not None:
        sel = ring & (np.linalg.norm(lb - out_bg_lab, axis=-1) < 8)
        rgb_bg = np.median(rgb[sel], 0) if sel.any() else None

    # ---------------- lines, case, size, words, weight, width
    rows = text_mask[Y0:Y1, X0:X1].sum(1) > max(2, 0.01 * (X1 - X0))
    segs = [(a + Y0, b + Y0) for a, b in _runs(rows) if b - a >= 0.3 * lh]
    if not segs:
        segs = [(by0, by1)]
    ratios, caps, words_n, widths, thick = [], [], [], [], []
    for a, b in segs:
        m = text_mask[a:b, X0:X1]
        cols = np.where(m.any(0))[0]
        if len(cols) < 4:
            continue
        dens = m[:, cols[0]:cols[-1] + 1].mean(1)
        if dens.max() <= 0:
            continue
        rows_on = np.where(dens > 0.02)[0]
        top = int(rows_on[0])
        strong = np.where(dens >= 0.5 * dens.max())[0]
        base = int(strong[-1]) + 1
        ch = base - top
        if ch < 4:
            continue
        # caps fill the top quarter of the cap height; lower case only has ascenders there
        hi = dens[top:top + max(1, int(0.25 * ch))].mean()
        mid = dens[top + int(0.4 * ch):top + max(int(0.4 * ch) + 1, int(0.9 * ch))].mean()
        ratios.append(float(hi / max(mid, 1e-3)))
        caps.append(float(ch))
        # word gaps: empty column runs inside the line wider than ~0.3 cap
        inner = m.any(0)[cols[0]:cols[-1] + 1]
        gaps = [r for r in _runs(~inner) if r[1] - r[0] >= max(2, 0.3 * ch)]
        words_n.append(len(gaps) + 1)
        lblc, nc = ndi.label(fill_mask[a:b, X0:X1])
        if nc:
            sl = ndi.find_objects(lblc)
            ws = [q[1].stop - q[1].start for q in sl if q is not None and (q[0].stop - q[0].start) > 0.5 * ch]
            if ws:
                widths.append(float(np.median(ws)) / ch)
        dt = ndi.distance_transform_edt(fill_mask[a:b, X0:X1])
        if (dt > 0).any():
            thick.append(2 * float(np.percentile(dt[dt > 0], 92)) / ch)
    if ratios:
        up = float(np.median(ratios))
        out["case"] = _attr("upper" if up >= 0.6 else "as_is", 0.7 if up >= 0.72 or up <= 0.4 else 0.4)
        out["_upper_ratio"] = round(up, 3)
    cap_px = float(np.median(caps)) if caps else 0.0
    out["lines"] = _attr(max(1, min(4, len(segs))), 0.55)
    if words_n:
        out["words"] = _attr(int(round(float(np.median(words_n)))), 0.45)
    if thick:
        t = float(np.median(thick))
        weight = 400 if t < 0.13 else 600 if t < 0.165 else 700 if t < 0.21 else 800 if t < 0.26 else 900
        out["weight"] = _attr(weight, 0.4)
        out["_thick"] = round(t, 3)
    if widths:
        wr = float(np.median(widths))
        out["width"] = _attr("condensed" if wr < 0.5 else "wide" if wr > 0.95 else "normal", 0.3)
        out["_glyph_w"] = round(wr, 3)
    if full and cap_px:
        out["cap_rel"] = _attr(round(cap_px / short, 4), 0.55)
        cy = ((segs[0][0] + segs[-1][1]) / 2) / h * 100
        out["position"] = _attr(round(cy, 1), 0.8)
        cx = ((bx0 + bx1) / 2) / w
        out["align"] = _attr("left" if cx < 0.36 and bx0 / w < 0.2 else "right" if cx > 0.64 and bx1 / w > 0.8
                             else "center", 0.4)
    out["_block"] = {"bbox": [round(bx0 / w, 4), round(by0 / h, 4), round(bx1 / w, 4), round(by1 / h, 4)],
                     "lines": len(segs), "uniform_bg": uniform, "cap_px": round(cap_px / sc, 1)}
    out["_bg"] = hex_of(rgb_bg) if rgb_bg is not None else None
    return out


# ====================================================================== AI (vision slot)

AI_SCHEMA = {"type": "object", "required": ["text_color"], "properties": {
    "text": {"type": ["string", "null"]},
    "text_color": {"type": "string"},
    "active_word_color": {"type": ["string", "null"]},
    "active_word_text": {"type": ["string", "null"]},
    "stroke_color": {"type": ["string", "null"]},
    "stroke_width": {"type": ["string", "null"]},
    "shadow": {"type": ["string", "null"]},
    "background": {"type": ["string", "null"]},
    "background_color": {"type": ["string", "null"]},
    "background_opacity": {"type": ["number", "null"]},
    "background_shape": {"type": ["string", "null"]},
    "case": {"type": ["string", "null"]},
    "font_category": {"type": ["string", "null"]},
    "font_weight": {"type": ["string", "null"]},
    "font_width": {"type": ["string", "null"]},
    "italic": {"type": ["boolean", "null"]},
    "position_y_pct": {"type": ["number", "null"]},
    "align": {"type": ["string", "null"]},
    "size": {"type": ["string", "null"]},
    "words_per_line": {"type": ["integer", "number", "null"]},  # i18n-ignore
    "lines": {"type": ["integer", "number", "null"]},
    "highlight_style": {"type": ["string", "null"]},
    "animation_guess": {"type": ["string", "null"]},
    "full_frame": {"type": ["boolean", "null"]},
}}

AI_PROMPT = (  # AI prompt: content, not UI text (kept in English)
    """You analyse the on-screen CAPTION (subtitle) style in this image so it can be recreated.
Describe ONLY what you can see in the caption text itself (ignore other UI text, logos, the video content).
Return exactly one JSON object with these keys:
- text: the caption words exactly as shown (null if unreadable)
- text_color: main fill colour of the caption letters, "#RRGGBB"
- active_word_color: colour of a word that is coloured differently from the rest (karaoke active word), "#RRGGBB" or null
- active_word_text: that word, or null
- stroke_color: outline colour around the letters, "#RRGGBB" or null when there is no outline
- stroke_width: "none" | "thin" | "medium" | "thick"
- shadow: "none" | "soft" | "hard" | "long"
- background: "none" | "per_line" (a box behind each line) | "block" (one panel behind all lines) | "per_word" (a box behind every word) | "active_word" (a box only behind the highlighted word)
- background_color: "#RRGGBB" or null
- background_opacity: 0..1 or null
- background_shape: "rect" | "rounded" | "pill" | "marker" (rough highlighter) | "banner" | null
- case: "upper" (ALL CAPS) | "lower" | "title" (Each Word Capitalised) | "sentence"
- font_category: "sans" | "condensed" | "rounded" | "serif" | "handwritten" | "display" | "comic" | "mono"
- font_weight: "regular" | "semibold" | "bold" | "black"
- font_width: "condensed" | "normal" | "wide"
- italic: true | false
- position_y_pct: vertical centre of the caption block in % of the image height (0 = top)
- align: "left" | "center" | "right"
- size: caption letter size relative to the frame: "small" | "medium" | "large" | "huge"
- words_per_line: number of words in the longest caption line
- lines: number of caption lines
- highlight_style: how the active word stands out: "none" | "color" | "pill" | "underline" | "scale" | "glow" | "marker"
- animation_guess: most likely entrance animation for this style: "none" | "fade" | "pop" | "slide_up" | "bounce" | "typewriter" | "zoom"
- full_frame: true when the image is a whole video frame, false when it is a crop around the caption
No markdown, no prose."""
)

ZOOM_NOTE = (" Image 1 is the whole picture. Image 2 is a zoomed crop around the caption: read colours, outline, "
             "case and font from image 2, position and size from image 1.")

_SIZE_CAP = {"small": 0.028, "medium": 0.04, "large": 0.055, "huge": 0.075}     # cap height / short side
_STROKE_PX = {"none": 0, "thin": 3, "medium": 6, "thick": 10}                  # px@1080
_WEIGHT = {"regular": 400, "semibold": 600, "bold": 700, "black": 900, "heavy": 900, "extrabold": 800}
_BG = {"none": "none", "per_line": "line", "line": "line", "block": "page", "panel": "page", "page": "page",  # i18n-ignore
       "per_word": "word", "word": "word", "active_word": "active"}  # i18n-ignore
_CASE = {"upper": "upper", "lower": "lower", "title": "title", "sentence": "sentence", "mixed": "as_is",
         "as_is": "as_is"}
_HL = ("none", "color", "pill", "underline", "scale", "glow", "marker")
_ANIM = {"none": "none", "fade": "fade", "pop": "pop", "slide_up": "slide_up", "slide": "slide_up", "bounce": "bounce",
         "typewriter": "typewriter", "zoom": "zoom", "scale": "scale"}


def _one(v, allowed, default=None):
    s = str(v or "").strip().lower().replace("-", "_").replace(" ", "_")
    return s if s in allowed else default


def _jpeg(im, side, q=88):
    small = im.copy()
    small.thumbnail((side, side))
    buf = io.BytesIO()
    small.convert("RGB").save(buf, "JPEG", quality=q)
    return buf.getvalue()


def analyze_ai(im, use_cache=True, emit=None, bbox=None):
    """Vision request (one call, cached by image) -> (normalized dict | None, warning | None, meta).
    bbox (fractions x0, y0, x1, y1 of the caption block found locally) adds a zoomed crop as a 2nd image, which
    makes colours and letter shapes much easier to read for the model."""
    from ..ai import client as ai
    from ..util import redact
    data = _jpeg(im, 1024)
    imgs = [data]
    if bbox:
        W, H = im.size
        x0, y0, x1, y1 = bbox
        bw, bh = (x1 - x0) * W, (y1 - y0) * H
        pad = max(bw * 0.08, bh * 0.5)
        crop = im.crop((int(max(0, x0 * W - pad)), int(max(0, y0 * H - pad)), int(min(W, x1 * W + pad)),
                        int(min(H, y1 * H + pad))))
        if crop.size[0] >= 24 and crop.size[1] >= 12:
            sc = max(1.0, 640 / max(crop.size))
            if sc > 1:
                crop = crop.resize((int(crop.size[0] * sc), int(crop.size[1] * sc)))
            imgs.append(_jpeg(crop, 900))
    store = ai.Cache()
    ident = ai.cache_identity("vision")
    key = store.key("stylist", PROMPT_V, list(ident) if ident else None, base64.b64encode(data[::53]).decode("ascii"),
                    len(data), len(imgs))
    if use_cache:
        hit = store.get(key)
        if hit:
            return hit["obj"], None, {"source": "cache", "profile": hit.get("profile"), "model": hit.get("model")}
    trace = {}
    try:
        obj = ai.chat_vision(AI_PROMPT + (ZOOM_NOTE if len(imgs) > 1 else ""), imgs, schema=AI_SCHEMA, repair=0,
                             retries=0, tag="stylist", trace=trace, timeout=90)
    except ai.AIError as e:
        return None, redact(str(e))[:200], {"source": "fallback"}
    except Exception as e:  # noqa: BLE001 - a broken provider must never break the local result
        return None, redact(f"{type(e).__name__}: {e}")[:200], {"source": "fallback"}
    meta = {"source": "ai", "profile": trace.get("profile"), "model": trace.get("model")}
    store.put(key, {"obj": obj, "profile": meta["profile"], "model": meta["model"]})
    return obj, None, meta


def norm_ai(o):
    """AI JSON -> attribute dict like analyze_local (src 'ai'). Unknown values are dropped."""
    if not isinstance(o, dict):
        return {}
    out = {}
    fill = norm_hex(o.get("text_color"))
    if fill:
        out["fill"] = _attr(fill, 0.8, "ai")
    act = norm_hex(o.get("active_word_color"))
    if act and (not fill or delta_e(act, fill) > 12):
        out["active"] = _attr(act, 0.7, "ai")
    sw = _one(o.get("stroke_width"), _STROKE_PX)
    st = norm_hex(o.get("stroke_color"))
    if sw == "none" or (sw is None and not st):
        out["stroke"] = _attr(None, 0.65, "ai")
        out["stroke_width"] = _attr(0, 0.65, "ai")
    elif st:
        out["stroke"] = _attr(st, 0.7, "ai")
        out["stroke_width"] = _attr(_STROKE_PX.get(sw or "medium", 6), 0.5, "ai")
    sh = _one(o.get("shadow"), ("none", "soft", "hard", "long"))
    if sh:
        out["shadow"] = _attr(sh, 0.55, "ai")
    bg = _BG.get(_one(o.get("background"), _BG) or "")
    if bg:
        out["box"] = _attr(bg, 0.75, "ai")
        bc = norm_hex(o.get("background_color"))
        if bc and bg != "none":
            out["box_color"] = _attr(bc, 0.7, "ai")
        bo = o.get("background_opacity")
        if isinstance(bo, (int, float)) and not isinstance(bo, bool) and bg != "none":
            out["box_opacity"] = _attr(round(max(0.2, min(1.0, float(bo))), 2), 0.45, "ai")
        shp = _one(o.get("background_shape"), ("rect", "rounded", "pill", "marker", "banner"))
        if shp and bg != "none":
            out["box_shape"] = _attr(shp, 0.5, "ai")
    cs = _CASE.get(_one(o.get("case"), _CASE) or "")
    if cs:
        out["case"] = _attr(cs, 0.85, "ai")
    fc = _one(o.get("font_category"), ("sans", "condensed", "rounded", "serif", "handwritten", "display", "comic",
                                       "mono"))
    if fc:
        out["font_cat"] = _attr(fc, 0.6, "ai")
    fw = _WEIGHT.get(_one(o.get("font_weight"), _WEIGHT) or "")
    if fw:
        out["weight"] = _attr(fw, 0.6, "ai")
    fwd = _one(o.get("font_width"), ("condensed", "normal", "wide"))
    if fwd:
        out["width"] = _attr(fwd, 0.5, "ai")
    if isinstance(o.get("italic"), bool):
        out["italic"] = _attr(o["italic"], 0.5, "ai")
    py = o.get("position_y_pct")
    if isinstance(py, (int, float)) and not isinstance(py, bool) and 0 <= py <= 100:
        out["position"] = _attr(round(float(py), 1), 0.6, "ai")
    al = _one(o.get("align"), ("left", "center", "right"))
    if al:
        out["align"] = _attr(al, 0.5, "ai")
    sz = _one(o.get("size"), _SIZE_CAP)
    if sz:
        out["cap_rel"] = _attr(_SIZE_CAP[sz], 0.4, "ai")
    for k, key in (("words_per_line", "words"), ("lines", "lines")):  # i18n-ignore
        v = o.get(k)
        if isinstance(v, (int, float)) and not isinstance(v, bool) and 1 <= v <= 12:
            out[key] = _attr(int(round(v)), 0.6, "ai")
    hl = _one(o.get("highlight_style"), _HL)
    if hl:
        out["highlight"] = _attr(hl, 0.55, "ai")
    an = _ANIM.get(_one(o.get("animation_guess"), _ANIM) or "")
    if an:
        out["anim"] = _attr(an, 0.3, "ai")
    if isinstance(o.get("full_frame"), bool):
        out["_frame"] = o["full_frame"]
    txt = o.get("text")
    if isinstance(txt, str) and txt.strip():
        out["_text"] = re.sub(r"\s+", " ", txt.strip())[:80]
    aw = o.get("active_word_text")
    if isinstance(aw, str) and aw.strip():
        out["_active_text"] = aw.strip()[:30]
    return out


COLOR_ATTRS = ("fill", "active", "stroke", "box_color")


def merge(local, aiattrs):
    """Combine local measurements and the AI description -> attrs (+ notes of disagreements)."""
    if not aiattrs:
        return {k: v for k, v in local.items() if not k.startswith("_")}, []
    out, notes = {}, []
    keys = [k for k in dict.fromkeys(list(aiattrs) + list(local)) if not k.startswith("_")]
    for k in keys:
        a, b = aiattrs.get(k), local.get(k)
        if a is None:
            out[k] = b
            continue
        if b is None or b.get("value") is None and k not in ("stroke",):
            out[k] = a
            continue
        if k in COLOR_ATTRS and a["value"] and b["value"]:
            de = delta_e(a["value"], b["value"])
            if de < 30:
                out[k] = _attr(b["value"], max(a["conf"], b["conf"]) + 0.1, "ai+lokal")
            elif b["conf"] >= 0.6:
                # vision models are weak at exact colours; a measurement on a clean background wins
                out[k] = _attr(b["value"], b["conf"] - 0.1, "lokal")
                notes.append(tr("cap.stylist.noteMeasured", label=attr_label(k), ai=a["value"], measured=b["value"]))
            else:
                out[k] = _attr(a["value"], a["conf"] - 0.15, "ai")
                notes.append(tr("cap.stylist.noteAi", label=attr_label(k), ai=a["value"], measured=b["value"]))
        elif k in ("position", "cap_rel", "stroke_width") and isinstance(a["value"], (int, float)) \
                and isinstance(b["value"], (int, float)):
            # measured numbers are exact when the image is a full frame
            out[k] = _attr(b["value"], max(a["conf"], b["conf"]), "ai+lokal" if b["conf"] >= 0.5 else "ai")
            if b["conf"] < 0.5:
                out[k] = a
        elif k == "box" and b["value"] != "none" and a["value"] == "none" and b["conf"] >= 0.6:
            out[k] = _attr(b["value"], b["conf"], "lokal")       # a measured box (flat colour + sharp edge) is real
            notes.append(tr("cap.stylist.noteBox", label=attr_label("box"), measured=b["value"]))
        elif a["value"] == b["value"]:
            out[k] = _attr(a["value"], max(a["conf"], b["conf"]) + 0.1, "ai+lokal")
        else:
            out[k] = a if a["conf"] >= b["conf"] else b
    return out, notes


# ====================================================================== mapping onto our style schema

FONT_PREF = {
    "sans": ["Montserrat", "Poppins", "Plus Jakarta Sans", "Inter", "Archivo", "Outfit", "Sora", "Manrope", "Kanit"],
    "condensed": ["Anton", "Barlow Condensed", "Oswald", "Bebas Neue"],  # i18n-ignore
    "wide": ["Archivo", "Russo One", "Bungee", "Montserrat"],
    "rounded": ["Nunito", "Rubik", "Fredoka", "Baloo 2", "Paytone One"],
    "serif": ["DM Serif Display", "Abril Fatface"],
    "handwritten": ["Caveat Brush", "Gochi Hand", "Pacifico", "Knewave"],
    "display": ["Lilita One", "Titan One", "Passion One", "Righteous", "Russo One", "Bungee"],
    "comic": ["Bangers", "Knewave", "Lilita One", "Titan One"],
    "mono": ["Inter", "Sora"],
}
CAT_TO_CATALOG = {"sans": ("standar", "tebal"), "condensed": ("kondensasi",), "wide": ("tebal",),
                  "rounded": ("bulat",), "serif": ("serif",), "handwritten": ("tulisan_tangan",),
                  "display": ("dekoratif",), "comic": ("dekoratif",), "mono": ("standar",)}


def pick_font(cat="sans", weight=700, width="normal", catalog=None, system=False):
    """Closest font from the catalog by category, width and weight (bundled preferred) -> catalog row."""
    from . import layout as L
    rows = catalog if catalog is not None else L.font_catalog(system=system)["fonts"]
    cat = cat if cat in FONT_PREF else "sans"
    if cat == "sans" and width == "condensed":
        cat = "condensed"
    elif cat == "sans" and width == "wide":
        cat = "wide"
    prefs = FONT_PREF[cat]
    want = CAT_TO_CATALOG.get(cat, ("standar",))
    best, best_s = None, None
    for r in rows:
        base = r.get("base") or L.family_base(r["family"])
        rank = prefs.index(base) if base in prefs else None
        cats = set(r.get("categories") or [])
        if rank is None and not (cats & set(want)):
            continue
        s = (rank if rank is not None else 8 + (2 if r.get("source") == "system" else 0))
        s += abs(int(r.get("weight") or 400) - int(weight)) / 150.0
        if cat in ("sans", "wide") and "kondensasi" in cats:
            s += 3
        if r.get("source") == "system":
            s += 1.5
        if best_s is None or s < best_s:
            best, best_s = r, s
    if best is None:
        best = next((r for r in rows if r["family"] == L.DEFAULT_FONT), rows[0])
    return best


def _features(tpl):
    hl = tpl.get("highlight") or {}
    kind = ("pill" if hl.get("pill") else "underline" if hl.get("underline") else
            "color" if hl.get("color") else "none")
    box = tpl["box"]["per"] if tpl["box"].get("enabled") else "none"
    up = (tpl["font"].get("case") or ("upper" if tpl["font"].get("uppercase") else "as_is")) == "upper"
    return {"box": box, "upper": up, "hl": kind, "mode": tpl["timing"]["mode"]}


def base_template(attrs, W, H):
    """Closest built-in template id by box / case / highlight (ties: the aspect default)."""
    from . import templates as TP
    want_box = (attrs.get("box") or {}).get("value") or "none"
    if want_box == "active":
        want_box = "none"
    want_up = (attrs.get("case") or {}).get("value") == "upper"
    hl = (attrs.get("highlight") or {}).get("value")
    if hl is None:
        hl = "color" if (attrs.get("active") or {}).get("value") else "none"
    hl = {"marker": "pill", "scale": "color", "glow": "color"}.get(hl, hl)
    portrait = H > W
    best, best_s = TP.default_id(W, H), None
    for tid in TP.builtin_ids():
        try:
            t = TP.load(tid, W, H)
        except Exception:  # noqa: BLE001
            continue
        f = _features(t)
        s = 3 * (f["box"] != want_box) + 2 * (f["upper"] != want_up) + 1.5 * (f["hl"] != hl)
        s += 0.5 * (f["mode"] not in ("karaoke", "line"))
        asp = t.get("aspect") or []
        s += 0.4 * (("9:16" in asp) != portrait)
        s += 0.3 * bool(t["style"].get("gradient") or t["style"].get("extrude") or t["style"].get("long_shadow"))
        if best_s is None or s < best_s - 1e-9:
            best, best_s = tid, s
    return best


def _v(attrs, k, default=None):
    a = attrs.get(k)
    return default if not a or a.get("value") is None else a["value"]


def to_style(attrs, W, H, catalog=None):
    """attrs -> (base template id, doc.style patch). The patch sets every key it is confident about explicitly
    (and switches Gaya Pro text effects off), so applying it over `base` reproduces the look."""
    from . import templates as TP
    base = base_template(attrs, W, H)
    tpl = TP.load(base, W, H)
    fill = _v(attrs, "fill", "#FFFFFF")
    st = {"font": {}, "style": {"gradient": None, "extrude": None, "long_shadow": None, "outline2": 0,
                                "glow": None, "color_cycle": None, "outline_opacity": None},
          "box": {}, "highlight": {}, "layout": {}, "timing": {}, "anim": {}}
    # font
    cat = _v(attrs, "font_cat", "sans")
    weight = int(_v(attrs, "weight", 700) or 700)
    width = _v(attrs, "width", "normal")
    fr = pick_font(cat, weight, width, catalog)
    st["font"]["family"] = fr["family"]
    cs = _v(attrs, "case")
    if cs:
        st["font"]["case"] = cs
        st["font"]["uppercase"] = cs == "upper"
    if _v(attrs, "italic") is not None:
        st["font"]["italic"] = bool(_v(attrs, "italic"))
    st["font"]["skew"] = 0
    cap_rel = _v(attrs, "cap_rel")
    if cap_rel:
        cap = float(fr.get("cap") or 0.47)
        st["font"]["size"] = int(max(28, min(180, round(cap_rel * 1080 / cap / 2) * 2)))
    # colours
    st["style"]["fill"] = fill
    sw = _v(attrs, "stroke_width", 0) or 0
    stroke = _v(attrs, "stroke")
    if stroke and sw:
        st["style"]["outline"] = int(max(1, min(16, round(float(sw)))))
        st["style"]["outline_color"] = stroke
    elif (attrs.get("stroke") or {}).get("conf", 0) >= 0.45:
        st["style"]["outline"] = 0
    sh = _v(attrs, "shadow")
    if sh == "none":
        st["style"].update({"shadow": 0, "shadow_blur": 0})
    elif sh == "soft":
        st["style"].update({"shadow": 4, "shadow_x": 0, "shadow_y": 4, "shadow_blur": 8, "shadow_color": "#000000A6"})
    elif sh == "hard":
        st["style"].update({"shadow": 5, "shadow_x": 0, "shadow_y": 5, "shadow_blur": 0, "shadow_color": "#000000"})
    elif sh == "long":
        st["style"]["long_shadow"] = {"length": 30, "angle": 45, "color": "#000000B3", "fade": True, "steps": 0}
    # background
    box = _v(attrs, "box", "none")
    bc = _v(attrs, "box_color")
    if box in ("line", "page", "word"):
        opa = _v(attrs, "box_opacity")
        shape = _v(attrs, "box_shape", "rounded")
        st["box"].update({"enabled": True, "per": box, "filled": True, "border": 0, "tilt": 0, "gradient": None,
                          "shape": "marker" if shape == "marker" else "banner" if shape == "banner" else "rect",
                          "radius": 40 if shape == "pill" or box == "word" else 18 if shape == "rounded" else 4})
        if bc:
            st["box"]["color"] = bc
            st["box"]["opacity"] = float(opa) if opa is not None else (0.72 if luminance(rgb_of(bc)) < 0.08 else 1.0)
    elif (attrs.get("box") or {}).get("conf", 0) >= 0.45:
        st["box"]["enabled"] = False
    # active word
    act = _v(attrs, "active")
    hl = _v(attrs, "highlight") or ("color" if act else None)
    if box == "active":
        hl = "pill"
    hlp = {"glow": None, "wiggle": 0, "pill_shape": "rect", "future_color": None, "sweep": False, "scale": 1.0}
    if hl == "pill" or hl == "marker":
        hlp.update({"pill": bc if box == "active" and bc else act or "#7C3AED", "color": fill if box == "active" else
                    ("#FFFFFF" if act and luminance(rgb_of(act)) < 0.4 else "#111111") if act else fill,
                    "underline": None, "pill_shape": "marker" if hl == "marker" else "rect", "pill_slide": True})
    elif hl == "underline":
        hlp.update({"underline": act or "#FFD400", "color": None, "pill": None})
    elif hl == "glow":
        hlp.update({"color": act or "#FFFFFF", "pill": None, "underline": None, "scale": 1.06,
                    "glow": {"color": (act or "#FFE600") + "CC", "width": 10, "blur": 12, "strength": 1.4}})
    elif hl == "scale":
        hlp.update({"color": act, "pill": None, "underline": None, "scale": 1.12})
    elif hl == "color" or act:
        hlp.update({"color": act or "#FFE600", "pill": None, "underline": None})
    else:
        hlp.update({"color": None, "pill": None, "underline": None})
    st["highlight"] = hlp
    if hl in (None, "none") and not act:
        st["timing"]["mode"] = "line" if tpl["timing"]["mode"] in ("karaoke", "line") else tpl["timing"]["mode"]
    elif tpl["timing"]["mode"] not in ("karaoke", "reveal", "word"):
        st["timing"]["mode"] = "karaoke"
    # layout
    pos = _v(attrs, "position")
    if pos is not None and (attrs.get("position") or {}).get("conf", 0) >= 0.4:
        st["layout"]["y"] = int(round(max(8, min(92, float(pos)))))
    al = _v(attrs, "align")
    if al:
        st["layout"]["align"] = al
    lines = _v(attrs, "lines")
    words = _v(attrs, "words")
    if lines:
        st["layout"]["max_lines"] = int(max(1, min(3, lines)))
    if words:
        st["layout"]["max_words"] = int(max(1, min(10, words * max(1, min(3, lines or 1)))))
        if st["layout"]["max_words"] <= 1:
            st["timing"]["mode"] = "word"
    st["layout"]["rotate"] = 0
    st["layout"]["rotate_jitter"] = 0
    an = _v(attrs, "anim")
    if an in ANIM_IN:
        st["anim"]["in"] = an
    return base, {k: v for k, v in st.items() if v}


# ====================================================================== compare render

DEMO_TEXT = "Rahasia cuan jualan online"


def _words_for(text, tpl, active=None):
    from . import layout as L
    toks = [t for t in re.split(r"\s+", (text or DEMO_TEXT).strip()) if t][:12] or DEMO_TEXT.split()
    out, t = [], 0.0
    for i, tok in enumerate(toks):
        d = 0.32 + 0.03 * len(tok)
        out.append(L.RWord(text=L.transform_text(tok, tpl), start=round(t, 3), end=round(t + d, 3), id=f"st:{i}",
                           raw=tok, cap=i == 0, seg_end=1 if i == len(toks) - 1 else 0))
        t += d
    ai = 1
    if active:
        key = re.sub(r"\W", "", active.lower())
        for i, w in enumerate(out):
            if re.sub(r"\W", "", w.raw.lower()) == key:
                ai = i
                break
    return out, min(ai, len(out) - 1)


def render_style(tpl_full, W, H, out_png, text=None, active=None, bg=None, out_w=720):
    """PNG of the template's first caption page over `bg` (path) or a neutral gradient, at W x H scaled to out_w
    wide. The active word is `active` (or the 2nd word). -> Path"""
    from . import compile as C
    from . import layout as L
    from . import preview as P
    from . import render as R
    tpl = L.prep_template(dict(tpl_full))
    words, ai = _words_for(text, tpl, active)
    caps = L.paginate(words, tpl, W, H)
    if not caps:
        raise ValueError("template made no caption")
    cap = next((c for c in caps if any(w.id == words[ai].id for w in c.words)), caps[0])
    wa = next((w for w in cap.words if w.id == words[ai].id), cap.words[min(1, len(cap.words) - 1)])
    t = wa.start + 0.6 * (wa.end - wa.start) if tpl["timing"]["mode"] != "line" else cap.start + 0.45
    t = max(cap.start + 0.3, min(t, cap.end - 0.04)) if cap.end - cap.start > 0.4 else (cap.start + cap.end) / 2
    out_png = Path(out_png)
    work = out_png.parent
    work.mkdir(parents=True, exist_ok=True)
    ass = work / (out_png.stem + ".ass")
    C.write_ass(ass, [(c, tpl) for c in caps], W, H)
    ow = int(out_w) // 2 * 2
    oh = int(round(ow * H / W)) // 2 * 2
    src = Path(bg) if bg else P._bg(ow, oh)
    tmp = out_png.with_name(out_png.stem + "_tmp.png")
    try:
        P._ff_cwd(["-i", str(src), "-frames:v", "1", "-vf",
                   f"scale={ow}:{oh},setsar=1,setpts={t:.3f}/TB,ass='{ass.name}':fontsdir='{R.fonts_rel(work, ass)}'"
                   ":shaping=complex", "-y", tmp.name], work)
        tmp.replace(out_png)
    finally:
        for p in (ass, tmp):
            with contextlib.suppress(OSError):
                p.unlink()
    return out_png


def _blur_bg(im, folder, key, ow, oh):
    """Blurred, darkened copy of the user's image as the compare background."""
    from PIL import ImageEnhance, ImageFilter
    p = Path(folder) / f"bg_{key}_{ow}x{oh}.png"
    if not p.is_file():
        b = im.resize((ow, oh)).filter(ImageFilter.GaussianBlur(max(6, ow // 30)))
        ImageEnhance.Brightness(b).enhance(0.62).save(p)
    return p


def zoom_pair(theirs, ours, bbox, folder, key, out_w=480):
    """Crops of the same caption region (fractions x0, y0, x1, y1) from both frames -> {theirs_zoom, ours_zoom}."""
    from PIL import Image
    x0, y0, x1, y1 = bbox
    out = {}
    for name, src in (("theirs_zoom", theirs), ("ours_zoom", ours)):
        im = Image.open(src).convert("RGB")
        W, H = im.size
        bhp, cx, cy = (y1 - y0) * H, (x0 + x1) / 2 * W, (y0 + y1) / 2 * H
        half_h = 0.75 * bhp + 6
        half_w = min(W / 2, max(0.5 * (x1 - x0) * W + 0.5 * bhp, 2.1 * half_h))   # a wide strip around the caption
        cx = min(max(cx, half_w), W - half_w)
        box = (int(cx - half_w), int(max(0, cy - half_h)), int(cx + half_w), int(min(H, cy + half_h)))
        if box[2] - box[0] < 16 or box[3] - box[1] < 8:
            return {}
        crop = im.crop(box)
        crop = crop.resize((out_w, max(8, int(round(out_w * crop.size[1] / crop.size[0])))), Image.LANCZOS)
        p = Path(folder) / f"zoom_{name[:1]}_{key}_{Path(src).stem[-8:]}.png"
        crop.save(p)
        out[name] = str(p)
    return out


def frame_size(w, h):
    """Our render frame for an image of w x h: short side 1080, same aspect (clamped 1:3..3:1)."""
    r = max(1 / 3, min(3.0, w / max(1, h)))
    return (int(round(1080 * r / 2)) * 2, 1080) if r >= 1 else (1080, int(round(1080 / r / 2)) * 2)


# ====================================================================== action

CHIP_ORDER = ("fill", "active", "stroke", "box", "case", "font", "size", "position", "words", "highlight", "anim")


def _chips(attrs, style, font_row):
    def val(k):
        a = attrs.get(k) or {}
        v = a.get("value")
        if k == "box":
            key = {"none": "cap.stylist.none", "line": "cap.stylist.boxLine", "page": "cap.stylist.boxPage",
                   "word": "cap.stylist.boxWord", "active": "cap.stylist.boxActive"}.get(v)
            return tr(key) if key else v
        if k == "case":
            key = {"upper": "cap.stylist.caseUpper", "lower": "cap.stylist.caseLower", "title": "cap.stylist.caseTitle",
                   "sentence": "cap.stylist.caseSentence", "as_is": "cap.stylist.caseAsIs"}.get(v)
            return tr(key) if key else v
        if k == "position":
            return f"{int(round(v))} %" if v is not None else None
        if k == "highlight":
            key = {"none": "cap.stylist.none", "color": "cap.stylist.hlColor", "pill": "cap.stylist.hlPill",
                   "underline": "cap.stylist.hlUnderline", "scale": "cap.stylist.hlScale", "glow": "cap.stylist.hlGlow",
                   "marker": "cap.stylist.hlMarker"}.get(v)
            return tr(key) if key else v
        if k == "anim":
            return v
        return v
    rows = []
    for k in CHIP_ORDER:
        if k == "font":
            a = attrs.get("font_cat") or attrs.get("weight") or {}
            rows.append({"id": "font", "label": attr_label("font"), "value": font_row["family"],
                         "conf": round(min(0.9, max((attrs.get("font_cat") or {}).get("conf", 0.2),
                                                    0.5 * (attrs.get("weight") or {}).get("conf", 0.3))), 2),
                         "src": a.get("src", "lokal")})
            continue
        if k == "size":
            a = attrs.get("cap_rel")
            if a:
                rows.append({"id": "size", "label": attr_label("size"), "value": f"{style['font'].get('size')} px",
                             "conf": a["conf"], "src": a["src"]})
            continue
        a = attrs.get(k)
        if not a:
            continue
        v = val(k)
        if k == "stroke" and a.get("value") is None:
            v = tr("cap.stylist.none")
        if k == "active" and a.get("value") is None:
            continue
        rows.append({"id": k, "label": attr_label(k), "value": v, "conf": a["conf"], "src": a["src"],
                     "color": a["value"] if k in COLOR_ATTRS else None})
    return rows


def style_from_image(job, emit):
    """Engine action `style_from_image` (docs/CAPTIONS_API.md 12.1)."""
    from ..util import EngineError, workdir
    from . import layout as L
    from . import templates as TP
    p = job["params"]
    wd = Path(job.get("workdir") or workdir(job))
    folder = wd / "stylist"
    emit.plan([("load", tr("cap.stylist.stageLoad"), 0.05), ("local", tr("cap.stylist.stageLocal"), 0.25),
               ("ai", tr("cap.stylist.stageAi"), 0.5), ("map", tr("cap.stylist.stageMap"), 0.05),
               ("render", tr("cap.stylist.stageRender"), 0.15)])
    with emit.step("load"):
        src = p.get("image") or p.get("path")
        if p.get("clipboard"):
            src = clipboard_image()
        if p.get("frame"):
            from . import preview as P
            from . import _timeline
            tl = _timeline(job)
            t = float(p.get("t") if p.get("t") is not None else (tl.player or 0.0))
            src = str(P.frame_png(tl, t, wd, 1.0, p.get("fit", "cover"))[0])
        if not src:
            raise EngineError("BAD_PARAMS", tr("cap.stylist.noImage"), tr("cap.stylist.noImageHint"))
        try:
            im = load_image(src)
        except FileNotFoundError:
            raise EngineError("NO_IMAGE", tr("cap.stylist.fileMissing"), str(src)[:200]) from None
        except Exception as e:  # noqa: BLE001
            raise EngineError("BAD_IMAGE", tr("cap.stylist.badImage"), tr("cap.stylist.badImageHint", err=str(e))[:300]) from None
        if min(im.size) < 48:
            raise EngineError("BAD_IMAGE", tr("cap.stylist.tooSmall"), tr("cap.stylist.tooSmallHint"))
        in_path, key = keep_input(im, folder)
    with emit.step("local"):
        loc = analyze_local(im)
    warnings = []
    meta = {"source": "off"}
    aiattrs = {}
    with emit.step("ai"):
        if p.get("ai", True) is not False:
            obj, why, meta = analyze_ai(im, use_cache=p.get("cache", True) is not False, emit=emit,
                                        bbox=(loc.get("_block") or {}).get("bbox"))
            if obj is not None:
                aiattrs = norm_ai(obj)
            else:
                warnings.append(tr("cap.stylist.aiUnused", why=why or ""))
                emit.warn(tr("cap.stylist.aiMissing"))
        else:
            emit.done_note(tr("cap.stylist.aiOff"))
    with emit.step("map"):
        if loc.get("_block") is None and not aiattrs:
            raise EngineError("NO_CAPTION_FOUND", tr("cap.stylist.notFound"), tr("cap.stylist.notFoundHint"))
        attrs, notes = merge(loc, aiattrs)
        full = aiattrs.get("_frame", loc.get("_frame")) if aiattrs else loc.get("_frame")
        if not full:
            for k in ("position", "cap_rel", "align"):
                if k in attrs and attrs[k]["src"] == "lokal":
                    attrs.pop(k)
            if "position" in attrs and attrs["position"]["src"] == "ai":
                attrs.pop("position")
        W, H = frame_size(*im.size) if full else ((1080, 1920) if p.get("portrait") else (1920, 1080))
        if p.get("size") and not full:          # a crop: draw our version in the user's sequence frame
            W, H = int(p["size"][0]), int(p["size"][1])
        catalog = L.font_catalog(system=bool(p.get("system_fonts")))["fonts"]
        base, style = to_style(attrs, W, H, catalog)
        font_row = next(r for r in catalog if r["family"] == style["font"]["family"])
        tpl_full = L.deep_merge(TP.load(base, W, H), style)
        template = {k: v for k, v in tpl_full.items() if k not in ("id", "source") and not k.startswith("_")}
        template["name"] = p.get("name") or tr("cap.stylist.name")
        template["description"] = tr("cap.stylist.descAi" if aiattrs else "cap.stylist.descLocal")
        template.pop("default_for", None)
        source = "mixed" if aiattrs and loc.get("_block") else "ai" if aiattrs else "local"
        chips = _chips(attrs, style, font_row)
    compare = None
    with emit.step("render"):
        if p.get("render", True) is not False:
            ow = 640 if W >= H else 360
            oh = int(round(ow * H / W)) // 2 * 2
            ours = folder / f"ours_{key}_{TP.slug(base)}_{abs(hash(str(style))) % 10 ** 8}.png"
            try:
                bg = _blur_bg(im, folder, key, ow, oh) if full else None
                render_style(tpl_full, W, H, ours, text=aiattrs.get("_text"), active=aiattrs.get("_active_text"),
                             bg=bg, out_w=ow)
                compare = {"theirs": str(in_path), "ours": str(ours), "w": W, "h": H}
                bb = (loc.get("_block") or {}).get("bbox")
                if full and bb:                     # the same region of both frames, zoomed on the caption
                    compare.update(zoom_pair(in_path, ours, bb, folder, key))
            except Exception as e:  # noqa: BLE001 - a compare failure must not lose the style
                warnings.append(tr("cap.stylist.compareFail", msg=str(getattr(e, "msg", e)))[:200])
            for pat in ("ours_*.png", "zoom_*.png", "bg_*.png"):
                for old in sorted(folder.glob(pat), key=lambda q: q.stat().st_mtime, reverse=True)[KEEP_INPUTS * 2:]:
                    with contextlib.suppress(OSError):
                        old.unlink()
    conf = round(float(np.mean([c["conf"] for c in chips])) if chips else 0.0, 2)
    fidelity = "tinggi" if aiattrs and conf >= 0.6 else "sedang" if aiattrs else "rendah"
    return {"style": style, "base": base, "template": template, "attrs": attrs, "chips": chips, "source": source,
            "ai": {"used": bool(aiattrs), "source": meta.get("source"), "profile": meta.get("profile"),
                   "model": meta.get("model")},
            "fidelity": fidelity, "conf": conf, "notes": notes, "warnings": warnings, "image": str(in_path),
            "text": aiattrs.get("_text"), "full_frame": bool(full), "frame": [W, H], "compare": compare,
            "local": {k: v for k, v in loc.items() if k.startswith("_") and k not in ("_frame",)},
            "summary": tr("cap.stylist.summaryAi" if aiattrs else "cap.stylist.summaryLocal", font=font_row["family"],
                          n=len(chips))}
