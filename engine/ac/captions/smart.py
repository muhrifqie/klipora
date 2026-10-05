"""Auto-position (and optional auto-contrast) per caption page from the real sequence frames.

Port of proto/vision/caption_pos.py (Viterbi over pages) + proto/caption_render/smart.py (contrast):
  1. sample 3 frames inside every page (one sequential ffmpeg pass per media run, frames mapped to the sequence
     frame like the preview), build 64-row grids: faces (YuNet, head-grown boxes), text/UI (morphological edge
     boxes), action (frame difference; dropped on scene changes);
  2. candidate centre y every 5 % inside the platform safe zone + the template y;
     cost = 8 face + 3 text + 4 action + 1 |y - template y| / 30 %, measured under the page's real block size;
  3. Viterbi over pages with a jump cost (cheap after a >= 1 s pause) + min-run cleanup, so captions stay put
     unless something important is underneath. Manual positions are fixed points.
Results go to doc.page_over[<first word id>] = {"y", "auto": true, "why"} (manual entries are never touched).
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import numpy as np

from ..util import ffmpeg_exe
from . import layout as L
from . import model as M
from .preview import _probe, fit_filter, video_source_at

os.environ.setdefault("OPENCV_LOG_LEVEL", "ERROR")
HERE = Path(__file__).resolve().parent
YUNET = HERE / "models" / "face_detection_yunet_2026may.onnx"
GH = 64
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


# ---------------------------------------------------------------- frames

def _pieces(tl, times, piece=120.0):
    """Group wanted sequence times by source media into decode pieces [(path, [(src t, index)])]: a new piece
    after a > 15 s jump or every `piece` seconds of source (pieces decode in parallel)."""
    want = {}
    for k, t in enumerate(times):
        hit = video_source_at(tl, t)
        if hit:
            want.setdefault(hit[0].path, []).append((hit[1], k))
    out = []
    for path, lst in want.items():
        lst.sort()
        cur = [lst[0]]
        for item in lst[1:]:
            if item[0] - cur[-1][0] > 15.0 or item[0] - cur[0][0] > piece:
                out.append((path, cur))
                cur = []
            cur.append(item)
        out.append((path, cur))
    return out


def analyze_samples(tl, times, avoid=("faces", "text", "action"), fps=4.0, max_side=640, fit="cover", emit=None,
                    lo=0.0, hi=100.0, keep_gray=False, workers=None):
    """Decode the picture at every sequence time (one ffmpeg pass per piece, pieces in parallel) and reduce
    each frame to grids at once (frames are never kept): {index: (face, text, action, gray or None)}, grid w."""
    from concurrent.futures import ThreadPoolExecutor
    W, H = tl.width, tl.height
    sc = max_side / max(W, H)
    aw, ah = max(32, int(W * sc) // 2 * 2), max(32, int(H * sc) // 2 * 2)
    gw = max(8, int(round(GH * W / H)))
    pieces = _pieces(tl, times)
    total = sum(len(x[1]) for x in pieces) or 1
    out, done = {}, [0]
    lock = __import__("threading").Lock()

    def run(piece):
        path, lst = piece
        faces = Faces((aw, ah), enabled="faces" in avoid)
        info = _probe(path)
        start = max(0.0, lst[0][0] - 0.6)
        dur = lst[-1][0] - start + 0.6
        vf = fit_filter(info.get("width") or W, info.get("height") or H, W, H, fit)
        vf = f"fps={fps}," + (vf + "," if vf else "") + f"scale={aw}:{ah}:flags=area"
        cmd = [ffmpeg_exe(), "-v", "error", "-nostdin", "-skip_frame", "noref", "-ss", f"{start:.3f}", "-i",
               str(path), "-t", f"{dur:.3f}", "-an", "-sn", "-vf", vf, "-f", "rawvideo", "-pix_fmt", "bgr24", "-"]
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
                             creationflags=_NO_WINDOW)
        size, kf, j, prev, res = aw * ah * 3, 0, 0, None, {}
        try:
            while j < len(lst):
                buf = p.stdout.read(size)
                if len(buf) < size:
                    break
                ts = start + kf / fps
                fr = np.frombuffer(buf, np.uint8).reshape(ah, aw, 3)
                hit = []
                while j < len(lst) and lst[j][0] <= ts + 0.5 / fps + 1e-6:
                    hit.append(lst[j][1])
                    j += 1
                if hit:
                    fg, tg, ag, gray = grids(fr, prev, faces, gw)
                    g = gray[::2, ::2].copy() if keep_gray else None
                    for k in hit:
                        res[k] = (fg, tg if "text" in avoid else tg * 0, ag if "action" in avoid else ag * 0, g)
                    with lock:
                        done[0] += len(hit)
                prev = fr
                kf += 1
        finally:
            p.stdout.close()
            p.kill()
            p.wait()
        return res

    workers = workers or max(2, min(6, (os.cpu_count() or 4) // 3))
    with ThreadPoolExecutor(workers) as ex:
        futs = [ex.submit(run, pc) for pc in pieces]
        import time as _t
        while not all(f.done() for f in futs):
            if emit is not None:
                emit.progress(lo + (hi - lo) * done[0] / total)
            _t.sleep(0.2)
        for f in futs:
            out.update(f.result())
    return out, (aw, ah), gw


# ---------------------------------------------------------------- grids

class Faces:
    def __init__(self, size, enabled=True):
        self.det = None
        if enabled and YUNET.is_file():
            try:
                import cv2
                self.det = cv2.FaceDetectorYN.create(str(YUNET), "", size, 0.7, 0.3, 50)
                self.det.setInputSize(size)
            except Exception:  # noqa: BLE001 - faces are optional (OpenCV without DNN etc.)
                self.det = None

    def boxes(self, bgr):
        if self.det is None:
            return []
        _, f = self.det.detect(bgr)
        if f is None:
            return []
        out = []
        for row in f:
            x, y, w, h = row[:4]
            out.append((x - 0.2 * w, y - 0.4 * h, w * 1.4, h * 1.55))   # head + hair + chin
        return out


def text_mask(gray):
    """Text/UI lines (morphological gradient + Otsu + horizontal closing); ~2-4 ms on 640 px."""
    import cv2
    h, w = gray.shape
    grad = cv2.morphologyEx(gray, cv2.MORPH_GRADIENT, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3)))
    _, bw = cv2.threshold(grad, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)
    kx = max(3, w // 160)
    closed = cv2.morphologyEx(bw, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (kx, 1)))
    n, _, stats, _ = cv2.connectedComponentsWithStats(closed, connectivity=8)
    mask = np.zeros((h, w), np.uint8)
    for x, y, bw_, bh, area in stats[1:]:
        if bh < 3 or bh > 0.12 * h or bw_ < 1.2 * bh or area / float(bw_ * bh) < 0.25:
            continue
        mask[y:y + bh, x:x + bw_] = 255
    return mask


def grids(frame, prev, faces, gw):
    import cv2
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    h, w = gray.shape
    rs = lambda m: cv2.resize(m.astype(np.float32) / 255.0, (gw, GH), interpolation=cv2.INTER_AREA)  # noqa: E731
    fm = np.zeros((h, w), np.uint8)
    for x, y, bw, bh in faces.boxes(frame):
        cv2.rectangle(fm, (int(x), int(y)), (int(x + bw), int(y + bh)), 255, -1)
    tm = text_mask(gray)
    if prev is not None:
        d = cv2.absdiff(gray, cv2.cvtColor(prev, cv2.COLOR_BGR2GRAY))
        mm = (d > 18).astype(np.uint8) * 255
        if mm.mean() / 255 > 0.5:          # page change / scene cut lights up everything: ignore
            mm[:] = 0
        mm = cv2.dilate(mm, np.ones((5, 5), np.uint8))
    else:
        mm = np.zeros_like(gray)
    return rs(fm), rs(tm), rs(mm), gray


# ---------------------------------------------------------------- placement

def place(pages, fixed, imps, boxes_px, W, H, safe, anchor, step=5.0, w_face=8.0, w_text=2.0, w_act=4.0,
          w_pref=1.5, jump=1.0, jump_gap=0.3, gap_reset=1.0, text_norm=0.25, min_run=3.0):
    """Viterbi over pages. boxes_px[i] = (x0, x1, height) of the page block; fixed[i] = manual y or None.
    -> [(y percent, why list, parts)]"""
    sy0, sy1 = safe[1] / H * 100, safe[3] / H * 100
    n = len(pages)
    ys_all = set()
    hmax = max((b[2] for b in boxes_px), default=0) / H * 100
    lo, hi = sy0 + hmax / 2, sy1 - hmax / 2
    if hi < lo:
        lo = hi = (sy0 + sy1) / 2
    anchor = round(min(max(anchor, lo), hi), 2)      # the template y as the safe zone allows it
    y = np.ceil(lo / step) * step                    # grid on multiples of `step` + the extremes + the anchor
    while y <= hi + 1e-6:
        if abs(y - anchor) >= step * 0.6:
            ys_all.add(round(float(y), 2))
        y += step
    ys_all.update({round(lo, 2), round(hi, 2), anchor})
    ys_all.update(round(v, 2) for v in fixed if v is not None)
    ys = np.array(sorted(ys_all))
    K = len(ys)
    C = np.zeros((n, K))
    parts = [[None] * K for _ in range(n)]
    for i in range(n):
        fg, tg, ag = imps[i]
        gw = fg.shape[1]
        x0, x1, hpx = boxes_px[i]
        c0 = max(0, int(x0 / W * gw))
        c1 = min(gw, max(c0 + 1, int(np.ceil(x1 / W * gw))))
        for k, yv in enumerate(ys):
            if fixed[i] is not None:
                C[i, k] = 0.0 if abs(yv - fixed[i]) < 1e-6 else 1e6
                parts[i][k] = (0.0, 0.0, 0.0)
                continue
            r0 = max(0, int((yv / 100 - hpx / H / 2) * GH))
            r1 = min(GH, max(r0 + 1, int(np.ceil((yv / 100 + hpx / H / 2) * GH))))
            fc = float(fg[r0:r1, c0:c1].mean()) if r1 > r0 else 0.0
            tc = float(min(1.0, tg[r0:r1, c0:c1].mean() / text_norm)) if r1 > r0 else 0.0
            ac = float(ag[r0:r1, c0:c1].mean()) if r1 > r0 else 0.0
            pc = abs(yv - anchor) / 30.0
            C[i, k] = w_face * fc + w_text * tc + w_act * ac + w_pref * pc
            parts[i][k] = (fc, tc, ac)
    if n == 0:
        return [], anchor
    D = C[0].copy()
    back = np.zeros((n, K), int)
    dy = np.abs(ys[:, None] - ys[None, :]) / 100
    for i in range(1, n):
        gap = pages[i]["t0"] - pages[i - 1]["t1"]
        J = jump_gap if gap >= gap_reset else jump
        T = np.where(dy > 1e-9, J + 1.5 * dy, 0.0)
        tot = D[:, None] + T
        back[i] = tot.argmin(0)
        D = tot.min(0) + C[i]
    k = int(D.argmin())
    path = [k]
    for i in range(n - 1, 0, -1):
        k = back[i][k]
        path.append(k)
    path = path[::-1]
    # min-run cleanup: short text-driven excursions that come back are not worth two eye jumps (faces stay hard)
    runs, a0 = [], 0
    for i in range(1, n + 1):
        if i == n or path[i] != path[a0]:
            runs.append((a0, i))
            a0 = i
    for r, (a0, a1) in enumerate(runs):
        if 0 < r < len(runs) - 1 and pages[a1 - 1]["t1"] - pages[a0]["t0"] < min_run:
            kp = path[runs[r - 1][1] - 1]
            if abs(ys[kp] - ys[path[runs[r + 1][0]]]) <= 3 and all(
                    fixed[i] is None and parts[i][kp][0] <= 0.02 for i in range(a0, a1)):
                for i in range(a0, a1):
                    path[i] = kp
    ka = int(np.argmin(np.abs(ys - anchor)))
    out = []
    for i, k in enumerate(path):
        yv = float(ys[k])
        why = []
        if fixed[i] is not None:
            why = ["manual"]
        elif abs(yv - anchor) > 0.6 * step:
            fa, ta, aa = parts[i][ka]
            if fa > 0.02:
                why.append("face")
            if ta > 0.35:
                why.append("text")
            if aa > 0.05:
                why.append("action")
            if not why:
                why.append("stability")
        out.append((yv, why or ["default"], parts[i][k]))
    return out, anchor


def _lum(gray_roi):
    c = gray_roi.astype(np.float32) / 255.0
    c = np.where(c <= 0.03928, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)
    return c


def contrast_ratio(a, b):
    la, lb = sorted((a, b), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def autoposition(doc, tl, platform=None, avoid=("faces", "text", "action"), contrast=False, reset_manual=False,
                 emit=None, fit="cover"):
    """Compute per-page y (and optional contrast boxes) into doc.page_over. Returns a summary dict."""
    pages = doc.get("pages") or []
    if not pages:
        return {"pages": 0, "moved": 0, "items": []}
    W, H = tl.width, tl.height
    base = M.base_template(doc)
    safe_name = platform or base["layout"].get("safe") or "auto"
    safe = L.safe_rect(safe_name, W, H)
    anchor = float(base["layout"]["y"])
    over = doc.setdefault("page_over", {})
    if reset_manual:
        for k in list(over):
            over[k] = {kk: vv for kk, vv in over[k].items() if kk not in ("y", "auto", "why")}
    # samples: 3 per page (2 below 1.5 s, 1 below 0.6 s)
    times, owner = [], []
    for i, p in enumerate(pages):
        d = p["t1"] - p["t0"]
        e = min(0.15, d / 4)
        if d < 0.6:
            ts = [p["t0"] + d / 2]
        elif d < 1.5:
            ts = [p["t0"] + e, p["t1"] - e]
        else:
            ts = [p["t0"] + e, p["t0"] + d / 2, p["t1"] - e]
        times += ts
        owner += [i] * len(ts)
    fps = 4.0 if tl.duration <= 300 else 2.0          # long timelines: half the decode work
    samples, (aw, ah), gw = analyze_samples(tl, times, avoid, fps, fit=fit, emit=emit, lo=0, hi=95,
                                            keep_gray=contrast)
    z = np.zeros((GH, gw), np.float32)
    imps = [[z, z, z] for _ in pages]
    grays = [[] for _ in pages]
    for k, i in enumerate(owner):
        smp = samples.get(k)
        if smp is None:
            continue
        fg, tg, ag, gray = smp
        f0, t0, a0 = imps[i]
        imps[i] = [np.maximum(f0, fg), np.maximum(t0, tg), np.maximum(a0, ag)]
        if gray is not None:
            grays[i].append(gray)
    faces_ok = Faces((aw, ah), enabled="faces" in avoid).det is not None
    # page block sizes at the template y (real layout, page overrides included)
    pairs, _, _ = M.page_pairs(doc)
    boxes_px, fixed = [], []
    for (cap, ptpl), p in zip(pairs, pages):
        _, bb, _ = L.layout(cap, ptpl, W, H)
        boxes_px.append((bb[0], bb[2], bb[3] - bb[1]))
        o = over.get(p.get("over") or "") or {}
        fixed.append(float(o["y"]) if o.get("y") is not None and not o.get("auto") else None)
    res, anchor_eff = place(pages, fixed, imps, boxes_px, W, H, safe, anchor)
    items, moved = [], 0
    for p, (yv, why, prt) in zip(pages, res):
        key = p.get("over") or p["lines"][0][0]
        cur = dict(over.get(key) or {})
        if why == ["manual"]:
            items.append({"page": p["id"], "y": yv, "why": why})
            continue
        if abs(yv - anchor_eff) < 1e-6:            # default spot: no override (template y keeps working)
            cur.pop("y", None)
            cur.pop("auto", None)
            cur.pop("why", None)
        else:
            cur.update(y=round(yv, 2), auto=True, why=why)
            moved += 1
        if cur:
            over[key] = cur
        else:
            over.pop(key, None)
        items.append({"page": p["id"], "y": round(yv, 2), "why": why,
                      "cost": {"face": round(prt[0], 3), "text": round(prt[1], 3), "action": round(prt[2], 3)}})
    boxed = 0
    if contrast:
        boxed = _auto_contrast(doc, pages, grays, over, W, H, aw // 2, ah // 2)
    return {"pages": len(pages), "moved": moved, "boxed": boxed, "safe": L.resolve_safe(safe_name, W, H),
            "anchor": anchor_eff, "faces_model": faces_ok, "items": items}


def _auto_contrast(doc, pages, grays, over, W, H, aw, ah, min_ratio=3.0, box="#000000A6"):
    """Pages whose text colour has < 3:1 contrast with what is behind it (75th percentile) and no outline/box get
    an automatic backing box (page_over[..].auto_style; removed again when no longer needed)."""
    M.repaginate(doc)
    pairs, _, _ = M.page_pairs(doc)
    n = 0
    for (cap, ptpl), p, gl in zip(pairs, pages, grays):
        key = p.get("over") or p["lines"][0][0]
        cur = dict(over.get(key) or {})
        cur.pop("auto_style", None)
        if not ptpl["box"]["enabled"] and ptpl["style"]["outline"] < 4 and gl:
            _, bb, _ = L.layout(cap, ptpl, W, H)
            sx, sy = aw / W, ah / H
            x0, x1 = max(0, int(bb[0] * sx)), min(aw, int(bb[2] * sx))
            y0, y1 = max(0, int(bb[1] * sy)), min(ah, int(bb[3] * sy))
            from .compile import parse_color
            r, g, b, _ = parse_color(ptpl["style"]["fill"])
            tl_ = float(_lum(np.array([[0.2126 * r + 0.7152 * g + 0.0722 * b]], np.float32))[0, 0])
            worst = min((contrast_ratio(tl_, float(np.percentile(_lum(gr[y0:y1, x0:x1]), 75 if tl_ > 0.5 else 25)))
                         for gr in gl if y1 > y0 and x1 > x0), default=99)
            if worst < min_ratio:
                cur["auto_style"] = {"box": {"enabled": True, "per": "line", "color": box, "radius": 14,
                                             "pad_x": 18, "pad_y": 10}}
                n += 1
        if cur:
            over[key] = cur
        else:
            over.pop(key, None)
    return n
