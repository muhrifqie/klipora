"""Auto Zoom (tool id "zoom"): punch-in zooms planned on the sequence, applied as Motion Scale/Position keyframes
per clip in clip MEDIA time (docs/research/premiere_timeline_api.md section 4) on a clone of the sequence.

Modes (params.mode):
  screen  "Ikuti aksi layar" (default): frame-difference activity map of every source video (10 fps, gray, half
          width, cached next to the media as <stem>_zoomact.npz), cursor dwell, clusters -> sessions -> rhythm
          budget. The zoom is complete just before the action and exits instantly on a page change
          (docs/research/vision.md section 6, port of proto/vision/autozoom.py).
  speech  "Penekanan bicara": transcript triggers (action/emphasis words, numbers, sentence starts, loudness
          z-score, timeline cuts, optional AI moments); anchor = the biggest face (YuNet) or the centre
          (docs/research/product_visual.md 4.1, port of docs/research/proto_visual/zoom_plan.py).
  rhythm  "Ritmis": every other sentence at N % (two-camera feel), jump cuts at sentence starts.

The camera is ONE curve over the sequence: keys [t, s, cx, cy, ease] (s = zoom factor, cx/cy = viewport centre in
normalised source coordinates). Between two keys Scale and the Position offset (0.5 - c) * s move with the same
eased fraction: a zoom about a fixed point, which is also what Premiere draws when both params share key times.
Per clip the curve is sampled at every breakpoint, densely (<= 0.1 s, linear keys) on ramps, plus 1-frame guard
keys around every move so Premiere's spatial Bezier on Position cannot drift during holds, then converted to
clip media time (in + (t - start) * speed).

Actions:
  analyze  -> {"review", "summary", "stats", "mode", "warnings"}; review items = zoom moments (kind "zoom") with a
              thumbnail (frame + zoom rectangle PNG in <workdir>/zoom_thumbs).
  apply    -> {"plan": {"kind": "keyframes", "path": <workdir>/zoom_keys.json, ...}} for panel/host/37_zoom.jsx.
  preview  -> {"path": <mp4>} fast preview render (NVENC, libx264 fallback) of a section with the zooms.
"""
from __future__ import annotations

import bisect
import json
import os
import re
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import numpy as np

from .. import media as M
from .. import review as RV
from ..i18n import dec, tr
from ..timeline import Timeline
from ..util import (ENGINE_DIR, PROJECT_DIR, EngineError, cache_path, data_hash, ensure_free, ffmpeg_exe,
                    file_hash, log, now_iso, read_json, workdir, write_json)

TITLE = "Auto Zoom"
DESCRIPTION = "Zoom halus ke aksi di layar, kata penting, atau ritme kalimat. Hasilnya keyframe Motion di sequence baru."   # i18n-ignore (fallback of tool.zoom.desc)

MODES = ("screen", "speech", "rhythm")
INTENSITY = {   # (zoom min, zoom max) per mode and preset (product_visual.md 4.1: screen 1.25-2.2, face 1.12-1.30)
    "screen": {"ringan": (1.2, 1.6), "sedang": (1.25, 2.2), "kuat": (1.4, 2.8)},
    "speech": {"ringan": (1.08, 1.18), "sedang": (1.12, 1.3), "kuat": (1.18, 1.45)},
    "rhythm": {"ringan": (1.1, 1.1), "sedang": (1.15, 1.15), "kuat": (1.25, 1.25)},
}
PER_MIN = {"screen": 6.0, "speech": 4.0, "rhythm": 0.0}
RAMP_SEC = {   # (in, out) seconds; screen ramps are slower than punch-ins on a talking head
    "smooth": {"screen": (0.6, 0.7), "talk": (0.45, 0.45)},
    "fast": {"screen": (0.25, 0.3), "talk": (0.2, 0.25)},
    "jump": {"screen": (0.0, 0.0), "talk": (0.0, 0.0)},
}
ZONES = {   # "Zona abaikan": activity whose centre lies here never triggers a zoom (normalised x0, y0, x1, y1)
    "taskbar": [[0.0, 0.95, 1.0, 1.0]],
    "notif": [[0.78, 0.72, 1.0, 1.0]],
    "browser": [[0.0, 0.0, 1.0, 0.11]],
    "edges": [[0.0, 0.0, 0.025, 1.0], [0.975, 0.0, 1.0, 1.0]],
}
DEFAULT_ZONES = ("taskbar", "notif")
# Indonesian tutorial cue words (lower case, punctuation stripped) - product_visual.md 4.1.
ACTION = {"klik", "pilih", "isi", "ketik", "masukin", "masukkan", "buka", "scroll", "centang", "tekan", "pencet",
          "copy", "paste", "salin", "tempel", "upload", "unggah", "download", "unduh", "daftar", "login", "masuk",
          "simpan", "kirim", "submit", "cek", "lihat", "perhatikan", "geser", "drag", "tombol", "menu"}
EMPHASIS = {"penting", "wajib", "jangan", "harus", "ingat", "gratis", "rahasia", "pertama", "kedua", "ketiga",
            "terakhir", "intinya", "kesimpulannya", "hati-hati", "awas", "promo", "diskon", "murah", "cepat",
            "mudah", "banget", "sangat", "paling", "catat", "bonus", "hemat", "untung", "cuan"}

ACT_V = 1           # activity cache version (bump when the analysis changes)
ACT_FPS = 10.0
ACT_THR = 24        # gray-level change that counts
ACT_GLOBAL = 0.18   # changed fraction above which a sample is a page change / scroll / app switch
THUMB_W = 360
PLAN_V = 1
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_END = re.compile(r"[.?!…]+[\"')\]]*$")


# ====================================================================== small helpers

def _cv2():
    os.environ.setdefault("OPENCV_LOG_LEVEL", "ERROR")   # before the first cv2 import (OpenCV 5 log spam)
    import cv2
    return cv2


def _dec(v, d=1):
    return dec(v, d)


def _zx(z):
    return _dec(z, 2).rstrip("0").rstrip(",.") + "x"


def _norm(w):
    return re.sub(r"[^\w-]", "", str(w).lower())


def _clamp(v, lo, hi):
    return lo if v < lo else hi if v > hi else v


def _in_ranges(t, ranges):
    return any(a - 1e-6 <= t <= b + 1e-6 for a, b in ranges)


class _Sub:
    """Maps a helper's 0..100 progress onto lo..hi of the current stage (other emitter calls pass through)."""

    def __init__(self, emit, lo, hi):
        self._e, self._lo, self._hi = emit, lo, hi

    def progress(self, pct, note=None, force=False):
        self._e.progress(self._lo + (self._hi - self._lo) * _clamp(float(pct), 0.0, 100.0) / 100, note=note, force=force)

    def __getattr__(self, name):
        return getattr(self._e, name)


def _params(p):
    """Normalised params: mode, zoom range, ramp, frequency, zones, interp."""
    mode = p.get("mode") or "screen"
    if mode not in MODES:
        raise EngineError("BAD_PARAM", tr("zoom.errMode", mode=mode), tr("zoom.errModeHint"))
    lo, hi = INTENSITY[mode].get(p.get("intensity") or "sedang", INTENSITY[mode]["sedang"])
    try:
        lo = float(p["zmin"]) if p.get("zmin") is not None else lo
        hi = float(p["zmax"]) if p.get("zmax") is not None else hi
    except (TypeError, ValueError):
        raise EngineError("BAD_PARAM", tr("zoom.errRange")) from None
    lo, hi = _clamp(lo, 1.02, 4.0), _clamp(hi, 1.02, 4.0)
    if hi < lo:
        lo, hi = hi, lo
    ramp = p.get("ramp") or ("jump" if mode == "rhythm" else "smooth")
    if ramp not in RAMP_SEC:
        ramp = "smooth"
    rin, rout = RAMP_SEC[ramp]["screen" if mode == "screen" else "talk"]
    if ramp != "jump" and p.get("ramp_s"):
        k = _clamp(float(p["ramp_s"]), 0.1, 1.5) / max(rin, 1e-6)
        rin, rout = rin * k, rout * k
    ease_in = {"smooth": "smooth", "fast": "out" if mode == "screen" else "back", "jump": "lin"}[ramp]
    ease_out = "lin" if ramp == "jump" else "smooth"
    per_min = float(p.get("per_min") or PER_MIN[mode])   # i18n-ignore
    zones = p.get("ignore", list(DEFAULT_ZONES) if mode == "screen" else [])
    rects = []
    for z in zones or []:
        if isinstance(z, str):
            rects += ZONES.get(z, [])
        elif isinstance(z, (list, tuple)) and len(z) == 4:
            rects.append([float(v) for v in z])
    interp = p.get("interp") if p.get("interp") in ("linear", "ease") else "linear"
    return {"mode": mode, "zmin": round(lo, 3), "zmax": round(hi, 3), "ramp": ramp, "rin": rin, "rout": rout,
            "ease_in": ease_in, "ease_out": ease_out, "per_min": per_min, "zones": rects, "interp": interp,   # i18n-ignore
            "step": _clamp(float(p.get("step") or 0.1), 1 / 60, 0.5), "ai": bool(p.get("ai"))}


# ====================================================================== source analysis (screen activity)

def _frames(path, fps, width, gray=True, start=None, dur=None, skip="noref"):
    """Yield (k, frame) sampled at `fps` from an ffmpeg pipe (CPU decode: NVDEC fails on these 120 fps files and
    is slower on static screen content; '-skip_frame noref' halves decode time, < 1 frame error)."""
    info = M.probe(path)
    W, H = info["width"], info["height"]
    ow = min(int(width), W)
    ow -= ow % 2
    oh = max(2, int(round(H * ow / W / 2)) * 2)
    ch = 1 if gray else 3
    cmd = [ffmpeg_exe(), "-hide_banner", "-nostdin", "-loglevel", "error"]
    if skip:
        cmd += ["-skip_frame", skip]
    if start:
        cmd += ["-ss", f"{start:.3f}"]
    cmd += ["-i", str(path)]
    if dur:
        cmd += ["-t", f"{dur:.3f}"]
    cmd += ["-an", "-sn", "-vf", f"fps={fps},scale={ow}:{oh}:flags=area", "-f", "rawvideo",
            "-pix_fmt", "gray" if gray else "bgr24", "-"]
    size = ow * oh * ch
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
                                creationflags=_NO_WINDOW, bufsize=size * 4)
    except FileNotFoundError as e:
        raise EngineError("NO_FFMPEG", tr("zoom.noFfmpeg"), tr("zoom.noFfmpegHint")) from e
    k = 0
    try:
        while True:
            buf = proc.stdout.read(size)
            if len(buf) < size:
                break
            yield k, np.frombuffer(buf, np.uint8).reshape((oh, ow) if gray else (oh, ow, 3))
            k += 1
    finally:
        proc.stdout.close()
        if proc.poll() is None:
            proc.kill()
        proc.wait()


def _analyze_activity(path, info, width, emit=None):
    """Port of proto/vision/autozoom.analyze: per sample pair -> changed fraction, global flag, local boxes
    [x, y, w, h, changed_px, is_cursor, ui_context] (normalised). Animated cells (spinners, clocks, video ads)
    are masked by an EMA over ~10 s."""
    cv2 = _cv2()
    gh, gw = 24, 48
    ema = np.zeros((gh, gw), np.float32)
    alpha = 1.0 / (10.0 * ACT_FPS)
    k3 = np.ones((3, 3), np.uint8)
    prev, kd, W, H = None, None, 0, 0
    T, CF, G, OFF, BX = [], [], [], [0], []
    dur = max(info["duration"], 1e-3)
    for k, g in _frames(path, ACT_FPS, width):
        t = k / ACT_FPS
        if prev is None:
            prev = g
            H, W = g.shape
            kd = cv2.getStructuringElement(cv2.MORPH_RECT, (max(9, W // 60), max(9, W // 60)))
            continue
        d = cv2.absdiff(g, prev)
        prev = g
        m = (d > ACT_THR).astype(np.uint8)
        cf = float(m.mean())
        glob = cf > ACT_GLOBAL
        if glob:
            ema *= (1 - alpha)   # a global change says nothing about animations
        else:
            cells = cv2.resize(m.astype(np.float32), (gw, gh), interpolation=cv2.INTER_AREA) > 0.002
            ema = (1 - alpha) * ema + alpha * cells
            anim = ema > 0.5
            if anim.any():
                m[cv2.resize(anim.astype(np.uint8), (W, H), interpolation=cv2.INTER_NEAREST) > 0] = 0
            if m.sum() >= 4:
                if cf > 0.02:
                    m = cv2.morphologyEx(m, cv2.MORPH_OPEN, k3)
                dil = cv2.dilate(m, kd)
                _n, _lab, st, _c = cv2.connectedComponentsWithStats(dil, connectivity=8)
                for x, y, w, h, _a in st[1:]:
                    px = int(m[y:y + h, x:x + w].sum())
                    if px < 4:
                        continue
                    cur = w <= 3 * kd.shape[1] // 2 + 30 and h <= kd.shape[0] + 30 and px < 120
                    ctx = -1.0
                    if cur:   # UI around the cursor (dwell on empty background means nothing)
                        cx_, cy_ = x + w // 2, y + h // 2
                        win = g[max(0, cy_ - 32):cy_ + 32, max(0, cx_ - 48):cx_ + 48]
                        ctx = float((cv2.Canny(win, 60, 160) > 0).mean()) if win.size else 0.0
                    BX.append([x / W, y / H, w / W, h / H, px, 1.0 if cur else 0.0, ctx])
        T.append(t)
        CF.append(cf)
        G.append(glob)
        OFF.append(len(BX))
        if emit is not None and k % 10 == 0:
            emit.progress(100.0 * t / dur)
    if prev is None:
        raise EngineError("NO_VIDEO", tr("zoom.videoUnreadable", name=Path(path).name), tr("zoom.videoUnreadableHint"))
    return {"t": np.array(T, np.float64), "cf": np.array(CF, np.float32), "glob": np.array(G, bool),
            "off": np.array(OFF, np.int64), "boxes": np.array(BX, np.float32).reshape(-1, 7)}


def activity(path, emit=None, refresh=False):
    """Screen activity of one video file, cached next to it as <stem>_zoomact.npz (key: version + file hash +
    analysis settings). -> {"t", "cf", "glob", "off", "boxes", "dur", "cached"}; boxes of sample i are
    boxes[off[i]:off[i+1]]. 49 s -> ~2 s, 34.6 min -> ~100 s (0.05x realtime), cache load ~10 ms."""
    path = str(M.require(path))
    info = M.probe(path)
    if not info["has_video"]:
        raise EngineError("NO_VIDEO", tr("zoom.noVideoStream", name=Path(path).name))
    width = min(1280, max(640, info["width"] // 2))
    meta = {"v": ACT_V, "hash": file_hash(path), "fps": ACT_FPS, "width": width, "thr": ACT_THR, "global": ACT_GLOBAL}
    cp = cache_path(path, "_zoomact.npz")
    if not refresh and cp.is_file():
        try:
            with np.load(cp, allow_pickle=False) as z:
                if json.loads(str(z["meta"])) == meta:
                    out = {k: z[k] for k in ("t", "cf", "glob", "off", "boxes")}
                    out.update(dur=info["duration"], cached=True)
                    return out
        except Exception as e:  # noqa: BLE001 - a broken cache is rebuilt
            log().info(f"zoom: activity cache unreadable ({e}), rebuilding {cp}")
    act = _analyze_activity(path, info, width, emit)
    try:
        tmp = cp.with_name(cp.name + ".tmp")
        with open(tmp, "wb") as f:
            np.savez_compressed(f, meta=np.array(json.dumps(meta)), **act)
        os.replace(tmp, cp)
    except OSError as e:
        log().info(f"zoom: activity cache not written ({e})")
    act.update(dur=info["duration"], cached=False)
    return act


# ====================================================================== timeline mapping

def _target(tl, p):
    """(track index, enabled video clips with readable media) of the track that gets the keys: params.track, else
    the lowest video track with media clips (V1 for recordings; overlays above it are left alone)."""
    want = p.get("track")
    tracks = [trk for trk in tl.video if any(c.has_media and not c.disabled for c in trk.clips)]
    if not tracks:
        raise EngineError("NO_VIDEO", tr("zoom.noVideoClips"), tr("zoom.noVideoClipsHint"))
    trk = next((t for t in tracks if t.index == want), None) if isinstance(want, int) else None
    trk = trk or min(tracks, key=lambda t: t.index)
    clips, missing = [], []
    for c in trk.clips:
        if c.disabled or not c.has_media or c.end - c.start < 1e-3:
            continue
        if not Path(c.path).is_file():
            missing.append(c.path)
            continue
        if M.probe(c.path)["has_video"]:
            clips.append(c)
    if not clips:
        raise EngineError("NO_MEDIA", tr("zoom.mediaMissing", name=Path(missing[0]).name if missing else "-"),
                          tr("zoom.mediaMissingHint"))
    return trk.index, clips, sorted(set(missing))


def _any_global(act, s0, s1):
    i0, i1 = np.searchsorted(act["t"], s0), np.searchsorted(act["t"], s1)
    return bool(act["glob"][i0:i1].any())


def _boundaries(clips, fps, acts=None):
    """Hard cuts (sequence seconds) of the target track: the picture jumps there (gap, other file, backwards in
    the source, or a page change inside the removed part). Soft boundaries (contiguous source, or a removed pause
    without a page change) let a zoom continue."""
    fr = 1.0 / fps
    hard, prev = [], None
    for c in clips:
        if prev is None:
            if c.start > fr:
                hard.append(c.start)
        elif c.start - prev.end > 1.5 * fr:
            hard += [prev.end, c.start]
        elif c.path != prev.path or c.src_in < prev.src_out - 2 * fr:
            hard.append(c.start)
        elif c.src_in - prev.src_out > 2 * fr:
            a = (acts or {}).get(c.path)
            if a is None or c.src_in - prev.src_out > 30.0 or _any_global(a, prev.src_out, c.src_in):
                hard.append(c.start)
        prev = c
    if prev is not None:
        hard.append(prev.end)
    return sorted({round(t, 6) for t in hard})


def _cuts(clips, fps):
    """Every non-contiguous boundary (for snapping jump zooms onto an existing cut)."""
    fr = 1.0 / fps
    out = []
    for a, b in zip(clips, clips[1:]):
        if b.path != a.path or abs(b.src_in - a.src_out) > 2 * fr or b.start - a.end > 1.5 * fr:
            out.append(b.start)
    return out


def _clip_at(clips, t):
    starts = [c.start for c in clips]
    i = bisect.bisect_right(starts, t + 1e-9) - 1
    if 0 <= i < len(clips) and clips[i].start - 1e-9 <= t < clips[i].end - 1e-9:
        return clips[i]
    return None


def _in_zone(b, zones):
    x, y = b[0] + b[2] / 2, b[1] + b[3] / 2
    return any(z[0] <= x <= z[2] and z[1] <= y <= z[3] for z in zones)


def _timeline_samples(clips, acts, zones, scope):
    """Source activity samples mapped onto the sequence (only inside scope). Boxes whose centre lies in an ignore
    zone get an 8th field = 1 (kept for the relative weight filter, never steer the zoom: see _weight)."""
    out = []
    for c in clips:
        a = acts.get(c.path)
        if a is None:
            continue
        lo, hi = sorted((c.src_in, c.src_out))
        i0, i1 = int(np.searchsorted(a["t"], lo)), int(np.searchsorted(a["t"], hi))
        boxes, off = a["boxes"], a["off"]
        for i in range(i0, i1):
            ts = c.src_to_seq(float(a["t"][i]))
            if not _in_ranges(ts, scope):
                continue
            bx = [[float(v) for v in b] + [1.0 if zones and _in_zone(b, zones) else 0.0] for b in boxes[off[i]:off[i + 1]]]
            out.append({"t": round(ts, 4), "global": bool(a["glob"][i]), "boxes": bx})
    out.sort(key=lambda r: r["t"])
    return out


# ====================================================================== screen planner (port of autozoom.plan)

def _weight(rec, cursor_w=0.3, min_px=10):
    """(weight, bbox [x0, y0, x1, y1]) of the meaningful local activity in one sample. Cursor travel alone counts
    at `cursor_w` of a UI change, so moving the mouse across the screen is not a reason to zoom. Boxes in an
    ignore zone (b[7] == 1) take part in the relative 10 % filter, then are dropped (removing them first would
    let noise next to a big taskbar change through)."""
    bs = [b for b in rec["boxes"] if b[4] >= min_px or b[5]]
    if not bs:
        return 0.0, None
    ws = np.array([b[4] for b in bs], float)
    keep = (ws >= 0.1 * ws.max()) & np.array([not (len(b) > 7 and b[7]) for b in bs])
    if not keep.any():
        return 0.0, None
    bs = [b for b, k in zip(bs, keep) if k]
    ws = ws[keep]
    cur = np.array([bool(b[5]) for b in bs])
    w = float(np.sqrt(ws[~cur].sum()) + cursor_w * np.sqrt(ws[cur].sum()))
    return w, [min(b[0] for b in bs), min(b[1] for b in bs), max(b[0] + b[2] for b in bs), max(b[1] + b[3] for b in bs)]


def _fits(bb, z, margin=1.35):
    return (bb[2] - bb[0]) * margin <= 1 / z and (bb[3] - bb[1]) * margin <= 1 / z


def _union(a, b):
    return [min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3])] if a else list(b)


def _add_dwell(samples, still=0.6, max_dwell=2.0, bonus=60, box=(0.03, 0.05), ctx_min=0.04):
    """Cursor dwell = attention: when the cursor stops >= `still` s on UI (edge density >= ctx_min), add a
    synthetic UI-weight box at its position for up to `max_dwell` s."""
    out = [dict(r, boxes=list(r["boxes"])) for r in samples]
    last, last_t, n = None, None, len(out)
    for i, r in enumerate(out):
        cur = [b for b in r["boxes"] if b[5]]
        if r["global"]:
            last = None
            continue
        if cur:
            b = max(cur, key=lambda b: b[4])
            ctx = b[6] if len(b) > 6 else 1.0
            zoned = len(b) > 7 and b[7]
            last, last_t = ((b[0] + b[2] / 2, b[1] + b[3] / 2) if ctx >= ctx_min and not zoned else None), r["t"]
            continue
        if last is not None and r["t"] - last_t >= still:
            j = i
            while (j < n and not out[j]["global"] and not any(b[5] for b in out[j]["boxes"])
                   and out[j]["t"] - last_t <= max_dwell):
                bw, bh = box
                out[j]["boxes"].append([last[0] - bw / 2, last[1] - bh / 2, bw, bh, bonus, 0.0, -1.0, 0.0])
                j += 1
            last = None
    return out


def _clusters(samples, z_fit=1.5, idle=1.2, w_min=3.0):
    out, cur = [], None
    for r in samples:
        if r["global"]:
            if cur:
                cur.update(end_reason="global", global_t=r["t"])
                out.append(cur)
                cur = None
            continue
        w, bb = _weight(r)
        if cur and r["t"] - cur["last"] > idle:
            cur["end_reason"] = "idle"
            out.append(cur)
            cur = None
        if w < w_min:
            continue
        if cur and not _fits(_union(cur["bbox"], bb), z_fit):
            cur["end_reason"] = "moved"
            out.append(cur)
            cur = None
        if not _fits(bb, z_fit):
            continue   # activity too spread (big panel animation) -> stay wide
        if cur is None:
            cur = {"start": r["t"], "last": r["t"], "bbox": list(bb), "weight": 0.0, "n": 0}
        cur["bbox"] = _union(cur["bbox"], bb)
        cur["last"] = r["t"]
        cur["weight"] += w
        cur["n"] += 1
    if cur:
        cur["end_reason"] = "eof"
        out.append(cur)
    return out


def _sessions(cl, glob, z_merge=1.25, merge_gap=2.7, min_weight=12.0, min_n=5, min_span=0.4):
    """Clusters merged into zoom sessions when close in time, no global change between them and the union still
    fits one viewport (the zoom stays in and pans instead of out-and-in)."""
    cl = [c for c in cl if c["weight"] >= min_weight and c["n"] >= min_n and c["last"] - c["start"] >= min_span]
    out = []
    for c in cl:
        if out:
            s = out[-1]
            between = any(s["last"] < g < c["start"] for g in glob)
            u = _union(s["bbox"], c["bbox"])
            if c["start"] - s["last"] <= merge_gap and not between and _fits(u, z_merge):
                s["bbox"], s["last"] = u, c["last"]
                s["weight"] += c["weight"]
                s["n"] += c["n"]
                s["parts"] += 1
                continue
        out.append({"start": c["start"], "last": c["last"], "bbox": list(c["bbox"]), "weight": c["weight"],
                    "n": c["n"], "parts": 1})
    return out


def _snap(t, cands, lo, hi):
    """Nearest candidate time within [t + lo, t + hi], else t (cands sorted)."""
    if not cands:
        return t
    i0, i1 = bisect.bisect_left(cands, t + lo), bisect.bisect_right(cands, t + hi)
    near = cands[i0:i1]
    return min(near, key=lambda c: abs(c - t)) if near else t


def _event(t_full, t_hold, z, cx, cy, cfg, end="ease", **extra):
    """Common zoom event: ramp in [t_in, t_full], hold [t_full, t_hold], out [t_hold, t_out] (or instant)."""
    rin, rout = cfg["rin"], cfg["rout"]
    if t_full - rin < 0:
        t_full = rin
    t_hold = max(t_hold, t_full + 0.3)
    if rout <= 0:
        end = "cut"
    z = float(z)
    cx = _clamp(float(cx), 0.5 / z, 1 - 0.5 / z)
    cy = _clamp(float(cy), 0.5 / z, 1 - 0.5 / z)
    ev = {"t_in": round(t_full - rin, 4), "t_full": round(t_full, 4), "t_hold": round(t_hold, 4),
          "t_out": round(t_hold + (0.0 if end == "cut" else rout), 4), "z": round(z, 3), "cx": round(cx, 4),
          "cy": round(cy, 4), "ease_in": cfg["ease_in"], "ease_out": cfg["ease_out"], "end": end}
    ev.update(extra)
    return ev


def _plan_screen(samples, hard, span, cfg, words, cuts, max_coverage=0.6):
    """Sessions -> events; the rhythm budget (coverage and zooms per minute) decides which start switched on."""
    glob = sorted({r["t"] for r in samples if r["global"]} | set(hard))
    z_fit = min(1.5, cfg["zmax"])
    ses = _sessions(_clusters(_add_dwell(samples), z_fit), glob, min(cfg["zmin"], z_fit))
    for s in ses:
        s["score"] = s["weight"] / max(0.5, s["last"] - s["start"])
    rin, rout, per_min = cfg["rin"], cfg["rout"], cfg["per_min"]   # i18n-ignore
    pre, post, min_hold = 0.15, 0.9, 1.0
    budget, used, chosen = max_coverage * span, 0.0, []
    for s in sorted(ses, key=lambda s: -s["score"]):
        L = s["last"] - s["start"] + rin + post + rout
        if used + L > budget:
            continue
        starts = sorted([c["start"] for c in chosen] + [s["start"]])
        if per_min > 0 and any(sum(1 for y in starts if x <= y < x + 60) > per_min for x in starts):
            continue
        s["on"] = True
        chosen.append(s)
        used += L
    starts_w = sorted(w["t0"] for w in words)
    ends_w = sorted(w["t1"] for w in words)
    top = max((s["score"] for s in ses), default=1.0) or 1.0
    events = []
    for s in sorted(ses, key=lambda s: s["start"]):
        bb = s["bbox"]
        need = max((bb[2] - bb[0]) * 1.6, (bb[3] - bb[1]) * 1.6, 1e-3)
        z = _clamp(1 / need, cfg["zmin"], cfg["zmax"])
        cx, cy = (bb[0] + bb[2]) / 2, (bb[1] + bb[3]) / 2
        t_full = _snap(s["start"] - pre, starts_w, -1.0, 0.25)
        if cfg["ramp"] == "jump":   # an instant zoom off a cut reads as a glitch: land it on a nearby cut
            t_full = _snap(t_full, cuts, -0.3, 0.15)
        hold = _snap(max(s["last"] + post, t_full + min_hold), ends_w, -0.3, 0.8)
        g = next((g for g in glob if g > s["last"]), None)
        end = "ease"
        if g is not None and g <= hold + rout + 0.3:
            end, hold = "cut", g
        ev = _event(t_full, hold, z, cx, cy, cfg, end, on=bool(s.get("on")), anchor="aksi",
                    bbox=[round(v, 4) for v in bb], conf=round(0.5 + 0.49 * min(1.0, s["score"] / top), 3),
                    why=[tr("zoom.why.screen")])
        if end == "cut" and cfg["rout"] > 0:
            ev["note"] = tr("zoom.noteCutExit")
        events.append(ev)
    return events, round(used / max(span, 1e-6), 3)


# ====================================================================== speech / rhythm planners

def _sentences(words, max_gap=0.6):
    out, cur = [], []
    for w in words:
        if cur and w["t0"] - cur[-1]["t1"] > max_gap:
            out.append(cur)
            cur = []
        cur.append(w)
        if w.get("seg") or _END.search(str(w["text"]).strip()):
            out.append(cur)
            cur = []
    if cur:
        out.append(cur)
    return [{"t0": s[0]["t0"], "t1": s[-1]["t1"], "w": s, "text": "".join(x["text"] for x in s).strip()} for s in out]


def _merge_short(sents, min_len=1.2):
    out = []
    for s in sents:
        if out and (s["t1"] - s["t0"] < min_len or out[-1]["t1"] - out[-1]["t0"] < min_len) and s["t0"] - out[-1]["t1"] < 0.8:
            p = out[-1]
            out[-1] = {"t0": p["t0"], "t1": s["t1"], "w": p["w"] + s["w"], "text": (p["text"] + " " + s["text"]).strip()}
        else:
            out.append(s)
    return out


def _loudness_z(words, env):
    """Per word: energy-mean dB over its span minus the median of the +-5 s neighbourhood, over (std + 1 dB)."""
    if env is None or not len(env) or not words:
        return np.zeros(len(words))
    db = np.empty(len(words))
    for i, w in enumerate(words):
        a, b = M.idx(w["t0"]), max(M.idx(w["t1"]), M.idx(w["t0"]) + 1)
        seg = env[a:b]
        db[i] = 10 * np.log10(np.mean(10 ** (seg / 10.0))) if len(seg) else M.FLOOR_DB
    t = np.array([w["t0"] for w in words])
    z = np.zeros(len(words))
    for i in range(len(words)):
        lo, hi = np.searchsorted(t, t[i] - 5), np.searchsorted(t, t[i] + 5)
        nb = db[lo:hi]
        z[i] = (db[i] - np.median(nb)) / (np.std(nb) + 1.0)
    return z


def _plan_speech(words, env, hard, scope, span, cfg, ai_moments=()):
    sents = _sentences(words)
    sent_of = []
    for si, s in enumerate(sents):
        sent_of += [si] * len(s["w"])
    z = _loudness_z(words, env)
    cands = []
    for i, w in enumerate(words):
        if not _in_ranges(w["t0"], scope):
            continue
        key, score, why = _norm(w["text"]), 0.0, []
        if i == 0 or sent_of[i] != sent_of[i - 1]:
            score += 1.5
            why.append(tr("zoom.why.sentenceStart"))
        if key in ACTION:
            score += 2.5
            why.append(tr("zoom.why.action", word=key))
        if key in EMPHASIS:
            score += 2.0
            why.append(tr("zoom.why.emphasis", word=key))
        if re.search(r"\d", key):
            score += 1.5
            why.append(tr("zoom.why.number"))
        if z[i] > 1.2:
            score += min(2.5, float(z[i]))
            why.append(tr("zoom.why.louder"))
        if score > 0:
            cands.append({"t": w["t0"], "i": i, "score": score, "why": why})
    for h in hard:
        if _in_ranges(h, scope) and 0.5 < h < scope[-1][1] - 1.0:
            cands.append({"t": h, "i": None, "score": 1.0, "why": [tr("zoom.why.cut")]})
    starts = [w["t0"] for w in words]
    for m in ai_moments or []:
        try:
            t = float(m.get("start"))
        except (TypeError, ValueError):
            continue
        if not _in_ranges(t, scope):
            continue
        i = max(0, min(len(words) - 1, bisect.bisect_left(starts, t - 0.05)))
        cands.append({"t": t, "i": i, "score": 5.0, "why": ["AI: " + str(m.get("why") or m.get("kind") or tr("zoom.why.aiDefault"))[:60]], "ai": True})   # "AI:" prefix: panel tag
    per_min = cfg["per_min"] or 4.0   # i18n-ignore
    gap = max(1.2, 20.0 / per_min)
    budget = max(1, round(span / 60.0 * per_min))
    chosen, extra = [], []
    for c in sorted(cands, key=lambda c: -c["score"]):
        if len(chosen) < budget and all(abs(c["t"] - o["t"]) >= gap for o in chosen):
            chosen.append(c)
        elif len(extra) < budget // 2 + 2 and all(abs(c["t"] - o["t"]) >= max(1.5, gap / 2) for o in chosen + extra):
            extra.append(c)
    for c in chosen:
        c["on"] = True
    allc = sorted(chosen + extra, key=lambda c: c["t"])
    rin, rout = cfg["rin"], cfg["rout"]
    events = []
    for n, c in enumerate(allc):
        t_full = max(rin, c["t"] - 0.04)
        hold = t_full + 1.8
        if c["i"] is not None:
            s = sents[sent_of[c["i"]]]
            hold = max(hold, s["t1"] + 0.25)
        hold = min(hold, t_full + 4.5)
        if n + 1 < len(allc):
            hold = min(hold, allc[n + 1]["t"] - 0.04 - rin - rout - 0.6)
        end = "ease"
        cut = next((h for h in hard if t_full + 1.5 <= h <= hold + rout), None)
        if cut is not None:
            end, hold = "cut", cut
        if hold - t_full < 0.8:
            continue
        zz = cfg["zmin"] + (cfg["zmax"] - cfg["zmin"]) * min(1.0, c["score"] / 6.0)
        note = ", ".join(c["why"][:3])
        events.append(_event(t_full, hold, zz, 0.5, 0.5, cfg, end, on=bool(c.get("on")), anchor="tengah",
                             conf=round(_clamp(0.45 + c["score"] / 10.0, 0.45, 0.99), 3), why=c["why"],
                             ai=bool(c.get("ai")), note=note[:1].upper() + note[1:]))
    return events


def _plan_rhythm(words, hard, cuts, scope, cfg):
    sents = [s for s in _merge_short(_sentences(words)) if _in_ranges(s["t0"], scope)]
    events = []
    for k, s in enumerate(sents):
        if k % 2 == 0:
            continue   # first sentence stays wide, then alternate
        prev_end = sents[k - 1]["t1"]
        nxt = sents[k + 1]["t0"] if k + 1 < len(sents) else s["t1"] + 0.3
        t_full = _snap(s["t0"], cuts, -0.3, 0.05)
        if t_full == s["t0"] and s["t0"] - prev_end > 0.08:
            t_full = (prev_end + s["t0"]) / 2 if cfg["ramp"] == "jump" else s["t0"] - 0.04
        t_hold = _snap(nxt, cuts, -0.3, 0.05)
        if t_hold == nxt and nxt - s["t1"] > 0.08:
            t_hold = (s["t1"] + nxt) / 2
        end = "cut" if cfg["ramp"] == "jump" else "ease"
        if any(t_full + 0.5 < h < t_hold for h in hard):
            end, t_hold = "cut", next(h for h in hard if t_full + 0.5 < h < t_hold)
        if t_hold - t_full < 0.8:
            continue
        events.append(_event(t_full, t_hold, cfg["zmax"], 0.5, 0.5, cfg, end, on=True, anchor="tengah", conf=0.8,
                             why=[tr("zoom.why.sentence", n=k + 1)]))
    return events


# ====================================================================== faces (talking head anchor)

def _yunet_model():
    for p in (ENGINE_DIR / "ac" / "assets" / "models" / "face_detection_yunet_2026may.onnx",
              ENGINE_DIR / "models" / "face_detection_yunet_2026may.onnx",
              ENGINE_DIR / "ac" / "models" / "face_detection_yunet_2026may.onnx",
              PROJECT_DIR / "proto" / "vision" / "models" / "face_detection_yunet_2026may.onnx",
              PROJECT_DIR / "proto" / "vision" / "models" / "face_detection_yunet_2023mar.onnx",
              PROJECT_DIR / "docs" / "research" / "proto_visual" / "models" / "face_detection_yunet_2023mar.onnx"):
        if p.is_file():
            return p
    return None


def _faces(path, times, emit=None):
    """Biggest real face per source time: {round(t, 2): [cx, cy, w, h] (normalised) | None}. YuNet at 640 px,
    score >= 0.7, faces smaller than 6 % of the frame height ignored (screen recordings show tiny faces in web
    ads). Cached next to the media as <stem>_zoomfaces.json."""
    model = _yunet_model()
    if model is None:
        return None
    cp = cache_path(path, "_zoomfaces.json")
    h = file_hash(path)
    doc = read_json(cp) or {}
    if doc.get("hash") != h or doc.get("model") != model.name:
        doc = {"v": 1, "hash": h, "model": model.name, "faces": {}}
    todo = [t for t in times if f"{t:.2f}" not in doc["faces"]]
    if todo:
        cv2 = _cv2()
        det = None
        for k, t in enumerate(todo):
            rgb = M.frame_grab(path, t, width=640)
            bgr = np.ascontiguousarray(rgb[:, :, ::-1])
            H, W = bgr.shape[:2]
            if det is None:
                det = cv2.FaceDetectorYN.create(str(model), "", (W, H), 0.7, 0.3, 50)
            det.setInputSize((W, H))
            faces = det.detect(bgr)[1]
            best = None
            for f in (faces if faces is not None else []):
                x, y, fw, fh = (float(v) for v in f[:4])
                if fh / H < 0.06:
                    continue
                if best is None or fw * fh > best[2] * best[3] * W * H:
                    best = [(x + fw / 2) / W, (y + fh / 2) / H, fw / W, fh / H]
            doc["faces"][f"{t:.2f}"] = [round(v, 4) for v in best] if best else None
            if emit is not None:
                emit.progress(100.0 * (k + 1) / len(todo))
        try:
            write_json(cp, doc)
        except OSError:
            pass
    return {round(t, 2): doc["faces"].get(f"{t:.2f}") for t in times}


def _anchor_faces(events, clips, emit, warns):
    """Put each talking-head event on the biggest face at its middle (eyes on the upper part of the frame)."""
    by_path = {}
    for e in events:
        c = _clip_at(clips, (e["t_full"] + e["t_hold"]) / 2)
        if c is not None:
            st = round(c.seq_to_src((e["t_full"] + e["t_hold"]) / 2), 2)
            by_path.setdefault(c.path, []).append((e, st))
    if _yunet_model() is None:
        warns.append(tr("zoom.warnNoFaceModel"))
        return 0
    n, done, total = 0, 0, max(1, len(events))
    for path, rows in by_path.items():
        sub = _Sub(emit, 100.0 * done / total, 100.0 * (done + len(rows)) / total)
        try:
            found = _faces(path, sorted({st for _, st in rows}), sub) or {}
        except EngineError as e:
            warns.append(tr("zoom.warnFaceFail", msg=e.msg))
            found = {}
        for e, st in rows:
            f = found.get(st)
            if f:
                z = e["z"]
                e["cx"] = round(_clamp(f[0], 0.5 / z, 1 - 0.5 / z), 4)
                e["cy"] = round(_clamp(f[1] + 0.08 / z, 0.5 / z, 1 - 0.5 / z), 4)
                e["anchor"] = "wajah"
                n += 1
        done += len(rows)
    return n


# ====================================================================== camera curve + keys

def _ease(u, kind):
    u = _clamp(u, 0.0, 1.0)
    if kind == "smooth":       # smoothstep ~ Premiere ease code 3 (25 % -> 14.9 %, measured)
        return u * u * (3 - 2 * u)
    if kind == "out":          # fast start, soft landing
        return 1 - (1 - u) ** 3
    if kind == "in":
        return u ** 3
    if kind == "back":         # snap-in with ~5 % overshoot
        c = 1.2
        return 1 + (c + 1) * (u - 1) ** 3 + c * (u - 1) ** 2
    return u


def camera_keys(events, fps, dur, hard=(), min_rest=1.5):
    """Camera keys [[t, s, cx, cy, ease]] (ease = easing of the segment ending at that key) for the selected
    events, times on the sequence frame grid. Calm pacing: an eased exit followed by another zoom within
    `min_rest` s becomes a direct glide from viewport to viewport (never across a hard cut); instant transitions
    are 1-frame segments."""
    fr = 1.0 / fps

    def g(t):
        return round(round(t * fps) / fps, 6)
    K = [[0.0, 1.0, 0.5, 0.5, "hold"]]
    prev = None
    for e in sorted(events, key=lambda e: e["t_full"]):
        z, cx, cy = e["z"], e["cx"], e["cy"]
        rin = max(0.0, e["t_full"] - e["t_in"])
        step_in = rin < 1.5 * fr
        crosses = prev is not None and any(prev["t_hold"] - 1e-6 <= h <= e["t_full"] + 1e-6 for h in hard)
        direct = (prev is not None and prev["end"] == "ease" and not step_in and not crosses and len(K) >= 3
                  and e["t_in"] - prev["t_out"] < min_rest and e["t_full"] > K[-3][0] + 0.7)
        if direct:
            K.pop()                                            # drop prev zoom-out, glide 0.5-1.2 s instead
            t_start = e["t_full"] - _clamp(e["t_full"] - K[-1][0], 0.5, 1.2)
            t_start = g(max(t_start, K[-2][0] + 0.3))
            K[-1][0] = t_start
            K.append([g(max(e["t_full"], t_start + 0.4)), z, cx, cy, e["ease_in"]])
        elif step_in:
            tf = g(max(e["t_full"], K[-1][0] + 2 * fr))
            K.append([g(tf - fr), 1.0, 0.5, 0.5, "hold"])
            K.append([tf, z, cx, cy, "lin"])
        else:
            ts = g(max(e["t_in"], K[-1][0] + 2 * fr))
            K.append([ts, 1.0, 0.5, 0.5, "hold"])
            K.append([g(max(e["t_full"], ts + 0.2)), z, cx, cy, e["ease_in"]])
        tf_now = K[-1][0]
        if e["end"] == "cut" or e["t_out"] - e["t_hold"] < 1.5 * fr:
            tc = g(max(e["t_hold"], tf_now + 2 * fr))
            K.append([g(tc - fr), z, cx, cy, "hold"])
            K.append([tc, 1.0, 0.5, 0.5, "lin"])
        else:
            th = g(max(e["t_hold"], tf_now + 0.3))
            K.append([th, z, cx, cy, "hold"])
            K.append([g(th + (e["t_out"] - e["t_hold"])), 1.0, 0.5, 0.5, e["ease_out"]])
        prev = e
    out = []
    for k in K:   # keep inside the sequence; a ramp cut by the end simply stops there
        if k[0] <= dur + 1e-6:
            out.append([k[0]] + [float(v) for v in k[1:4]] + [k[4]])
    if out[-1][1] != 1.0 and out[-1][0] < dur:
        out.append([round(dur, 6), out[-1][1], out[-1][2], out[-1][3], "hold"])
    return out


def state_at(K, t, _times=None):
    """(s, cx, cy) of the camera at sequence time t. Scale and offset o = (0.5 - c) * s share one eased fraction."""
    times = _times if _times is not None else [k[0] for k in K]
    if t <= times[0]:
        return K[0][1], K[0][2], K[0][3]
    i = bisect.bisect_right(times, t) - 1
    if i >= len(K) - 1:
        return K[-1][1], K[-1][2], K[-1][3]
    a, b = K[i], K[i + 1]
    span = b[0] - a[0]
    e = _ease((t - a[0]) / span if span > 1e-9 else 1.0, b[4])
    s = a[1] + (b[1] - a[1]) * e
    ox = (0.5 - a[2]) * a[1] + ((0.5 - b[2]) * b[1] - (0.5 - a[2]) * a[1]) * e
    oy = (0.5 - a[3]) * a[1] + ((0.5 - b[3]) * b[1] - (0.5 - a[3]) * a[1]) * e
    cx, cy = 0.5 - ox / s, 0.5 - oy / s
    if s >= 1.0:
        cx, cy = _clamp(cx, 0.5 / s, 1 - 0.5 / s), _clamp(cy, 0.5 / s, 1 - 0.5 / s)
    return s, cx, cy


def clip_keys(K, clip, fps, step=0.1, interp="linear"):
    """Keyframes [[media_t, s, cx, cy]] for one clip, or [] when the camera stays at 1x over it.
    linear: dense keys (<= step, >= 6 per ramp) + inner 1-frame guards on every ramp; both modes: outer 1-frame
    guards around every move (keys next to a hold carry the hold value, so Premiere's spatial Bezier on Position
    has ~zero tangents there and cannot drift). Times are snapped to the sequence frame grid."""
    fr = 1.0 / fps
    a, b = clip.start, clip.end - fr
    if b <= a:
        return []
    times, guard = {a, b}, set()
    for i in range(1, len(K)):
        ka, kb = K[i - 1], K[i]
        if kb[0] < a - 2 * fr or ka[0] > b + 2 * fr:
            continue
        times.add(ka[0])
        times.add(kb[0])
        if all(abs(ka[j] - kb[j]) < 1e-7 for j in (1, 2, 3)):
            continue
        span = kb[0] - ka[0]
        guard.update((ka[0] - fr, kb[0] + fr))
        if interp == "linear" and span > 2.5 * fr:
            guard.update((ka[0] + fr, kb[0] - fr))
            st = max(fr, min(step, span / 6))
            n = max(1, int(round(span / st)))
            times.update(ka[0] + span * j / n for j in range(1, n))

    def grid(t):
        return round(_clamp(round(t * fps) / fps, a, b), 6)
    inside = lambda t: a - fr / 2 <= t <= b + fr / 2   # noqa: E731
    keep_always = {grid(t) for t in guard if inside(t)}
    ts = sorted({grid(t) for t in times if inside(t)} | keep_always)
    kt = [k[0] for k in K]
    vals = [state_at(K, t, kt) for t in ts]
    if all(abs(v[0] - 1.0) < 1e-4 for v in vals):
        return []
    # drop keys in the middle of a constant run, but never a guard (it pins Position's spatial tangent)
    keep = [0] + [i for i in range(1, len(ts) - 1)
                  if ts[i] in keep_always or not (_same(vals[i - 1], vals[i]) and _same(vals[i], vals[i + 1]))] + [len(ts) - 1]
    sp = clip.speed or 1.0
    return [[round(clip.src_in + (ts[i] - clip.start) * sp, 6), round(vals[i][0], 5), round(vals[i][1], 5),
             round(vals[i][2], 5)] for i in keep]


def _same(u, v):
    return all(abs(x - y) < 1e-6 for x, y in zip(u, v))


# ====================================================================== thumbnails

def _thumbs(items, clips, outdir, emit, width=THUMB_W):
    """frame at the middle of every zoom + darkened outside + accent rectangle on the viewport -> PNG."""
    cv2 = _cv2()
    outdir.mkdir(parents=True, exist_ok=True)
    for f in outdir.glob("z*.png"):
        try:
            f.unlink()
        except OSError:
            pass
    stamp = time.strftime("%H%M%S")
    jobs = []
    for it in items:
        tm = (it["t_full"] + it["t_hold"]) / 2
        c = _clip_at(clips, tm)
        if c is not None:
            jobs.append((it, c.path, c.seq_to_src(tm), outdir / f"{it['id']}_{stamp}.png"))

    def one(job):
        it, path, st, out = job
        img = np.ascontiguousarray(M.frame_grab(path, st, width=width)[:, :, ::-1])
        h, w = img.shape[:2]
        z = it["z"]
        vw, vh = w / z, h / z
        x0 = int(round(_clamp(it["cx"] * w - vw / 2, 0, w - vw)))
        y0 = int(round(_clamp(it["cy"] * h - vh / 2, 0, h - vh)))
        x1, y1 = min(w, int(round(x0 + vw))), min(h, int(round(y0 + vh)))
        dim = (img.astype(np.float32) * 0.38).astype(np.uint8)
        dim[y0:y1, x0:x1] = img[y0:y1, x0:x1]
        cv2.rectangle(dim, (x0, y0), (x1 - 1, y1 - 1), (62, 132, 247), 2, cv2.LINE_AA)   # #f7843e (BGR)
        ok, buf = cv2.imencode(".png", dim, [cv2.IMWRITE_PNG_COMPRESSION, 6])
        if not ok:
            raise EngineError("THUMB", tr("zoom.thumbFail"))
        out.write_bytes(buf.tobytes())
        return it, out

    if not jobs:
        return 0
    ensure_free(len(jobs) * 200 * 1024, outdir)
    n = 0
    ex = ThreadPoolExecutor(max_workers=max(2, min(6, (os.cpu_count() or 4) // 2)))
    try:
        futs = [ex.submit(one, j) for j in jobs]
        for k, f in enumerate(as_completed(futs)):
            try:
                it, out = f.result()
                it["thumb"] = str(out)
                n += 1
            except EngineError as e:
                log().info(f"zoom: thumbnail failed: {e.msg}")
            emit.progress(100.0 * (k + 1) / len(futs))
    except BaseException:
        ex.shutdown(wait=False, cancel_futures=True)
        raise
    ex.shutdown(wait=True)
    return n


# ====================================================================== review items

def _ctx_text(words, t0, t1, max_chars=90):
    txt = "".join(w["text"] for w in words if t0 - 0.05 <= (w["t0"] + w["t1"]) / 2 <= t1).strip()
    return (txt[:max_chars - 1].rstrip() + "…") if len(txt) > max_chars else txt


ANCHORS = ("aksi", "wajah", "tengah")   # labels: zoom.anchor.<id>


def _items(events, words):
    items = []
    for e in events:
        said = _ctx_text(words, e["t_full"] - 0.2, e["t_hold"])
        why = e.get("why") or []
        note = e.get("note")
        it = RV.item(e["t_in"], e["t_out"], "zoom", on=e.get("on", True), conf=e.get("conf"),
                     label="Zoom " + _zx(e["z"]), ctx=said or "", note={"type": "ai" if e.get("ai") else "info", "text": note} if note else None,
                     **{k: e[k] for k in ("t_full", "t_hold", "z", "cx", "cy", "ease_in", "ease_out", "end", "anchor")},
                     src=tr("zoom.anchor." + e["anchor"]) if e.get("anchor") in ANCHORS else e.get("anchor"), why=why)
        if e.get("bbox"):
            it["bbox"] = e["bbox"]
        items.append(it)
    return items


def _event_of(it):
    """Back from a (possibly edited) review item to a planner event."""
    return {k: it[k] for k in ("t_full", "t_hold", "z", "cx", "cy", "ease_in", "ease_out", "end")} | \
        {"t_in": float(it["t0"]), "t_out": float(it["t1"])}


def _sig(clips):
    return data_hash([[round(c.start, 3), round(c.end, 3), round(c.src_in, 3), str(c.path).lower()] for c in clips])


# ====================================================================== actions

def analyze(job, emit):
    p = job.get("params") or {}
    cfg = _params(p)
    mode = cfg["mode"]
    tl = Timeline.from_json(job.get("seq"))
    stages = [("read", tr("zoom.st.read"), 0.03)]
    if mode == "screen":
        stages += [("activity", tr("zoom.st.activity"), 0.72), ("words", tr("zoom.st.words"), 0.03)]
    else:
        stages += [("words", tr("zoom.st.transcribe"), 0.45), ("audio", tr("zoom.st.audio"), 0.06)]
        if cfg["ai"] and mode == "speech":
            stages.append(("ai", tr("zoom.st.ai"), 0.14))
        stages.append(("faces", tr("zoom.st.faces"), 0.1))
    stages += [("plan", tr("zoom.st.plan"), 0.04), ("thumbs", tr("zoom.st.thumbs"), 0.13)]
    emit.plan(stages)
    warns = []
    with emit.step("read"):
        track, clips, missing = _target(tl, p)
        if missing:
            warns.append(tr("zoom.warnMissing", n=len(missing)))
        scope = tl.scope_ranges(p.get("scope"))
        span = sum(b - a for a, b in scope)
        emit.progress(100)
    acts, words, events, coverage = {}, [], [], None
    if mode == "screen":
        with emit.step("activity"):
            paths = list(dict.fromkeys(c.path for c in clips))
            durs = [max(1.0, M.probe(x)["duration"]) for x in paths]
            done, hits = 0.0, 0
            for path, d in zip(paths, durs):
                sub = _Sub(emit, 100.0 * done / sum(durs), 100.0 * (done + d) / sum(durs))
                acts[path] = activity(path, emit=sub)
                hits += bool(acts[path].get("cached"))
                done += d
            if hits == len(paths):
                emit.done_note(tr("zoom.fromCache"))
            emit.progress(100)
        with emit.step("words"):
            try:
                words = tl.words_on_timeline(transcribe=False)
            except EngineError:
                words = []
            if not words:
                emit.done_note(tr("zoom.noTranscript"))
            emit.progress(100)
        with emit.step("plan"):
            hard = _boundaries(clips, tl.fps, acts) + [b for _a, b in scope]
            samples = _timeline_samples(clips, acts, cfg["zones"], scope)
            samples += [{"t": h, "global": True, "boxes": [], "cut": True} for h in hard]
            samples.sort(key=lambda r: (r["t"], not r.get("cut")))
            events, coverage = _plan_screen(samples, hard, span, cfg, words, _cuts(clips, tl.fps))
            emit.progress(100)
    else:
        with emit.step("words"):
            words = tl.words_on_timeline(emit=emit)
            if not words:
                raise EngineError("NO_WORDS", tr("zoom.noWords"), tr("zoom.noWordsHint"))
        with emit.step("audio"):
            env = tl.envelope_on_timeline()
            emit.progress(100)
        hard = sorted(set(_boundaries(clips, tl.fps) + [b for _a, b in scope]))
        moments = []
        if cfg["ai"] and mode == "speech":
            with emit.step("ai"):
                from ..ai import tasks
                r = tasks.run("moments", words, emit=emit)
                moments = (r.get("result") or {}).get("moments") or []
                if r.get("source") not in ("ai", "cache"):
                    warns.append(tr("zoom.warnNoAi"))
                emit.progress(100)
        with emit.step("plan"):
            if mode == "speech":
                events = _plan_speech(words, env, hard, scope, span, cfg, moments)
            else:
                events = _plan_rhythm(words, hard, _cuts(clips, tl.fps), scope, cfg)
            emit.progress(100)
        with emit.step("faces"):
            n_faces = _anchor_faces(events, clips, emit, warns) if events else 0
            emit.done_note(tr("zoom.faces", n=n_faces) if n_faces else tr("zoom.noFaces"))
            emit.progress(100)
    items = _items(events, words)
    wd = workdir(job, tl.name)
    path = RV.path_for(wd, "zoom")
    doc = RV.new("zoom", items, tl.duration, seq=tl, params=p, mode=mode, track=track, sig=_sig(clips), fps=tl.fps,
                 width=tl.width, height=tl.height, cfg={k: cfg[k] for k in ("zmin", "zmax", "ramp", "rin", "rout", "per_min", "interp")},   # i18n-ignore
                 stats={"coverage": coverage})
    with emit.step("thumbs"):
        n_th = _thumbs(doc["items"], clips, wd / "zoom_thumbs", emit) if doc["items"] else 0
        emit.progress(100)
    old = read_json(path)
    if old and old.get("tool") == "zoom" and old.get("mode") == mode and (old.get("seq") or {}).get("id") == tl.id:
        RV.carry_over(doc, old)
    on = [it for it in doc["items"] if it["on"]]
    doc["stats"].update(n=len(doc["items"]), on=len(on), sec_on=round(sum(it["t1"] - it["t0"] for it in on), 3),
                        per_min=round(len(on) / max(span / 60.0, 1e-6), 2), thumbs=n_th)
    for w in warns:
        emit.warn(w)
    RV.save(doc, path)
    return {"review": str(path), "mode": mode, "summary": tr("zoom.summary", n=len(on), total=len(doc["items"])),
            "stats": doc["stats"], "warnings": warns, "track": track, "duration": tl.duration}


def _cached_acts(clips):
    """Activity of the target media when its cache exists (no new analysis): lets apply/preview tell soft silence
    cuts from hard cuts exactly like analyze did."""
    acts = {}
    for path in dict.fromkeys(c.path for c in clips):
        if cache_path(path, "_zoomact.npz").is_file():
            try:
                acts[path] = activity(path)
            except EngineError:
                pass
    return acts


def _plan_doc(doc, tl, p, emit):
    """Selected review items -> camera curve -> per clip keys (the host plan)."""
    track = doc.get("track") if isinstance(doc.get("track"), int) else p.get("track")
    track, clips, _missing = _target(tl, {"track": track})
    if doc.get("sig") and doc["sig"] != _sig(clips):
        emit.warn(tr("zoom.warnSeqChanged"))
    on = [it for it in doc.get("items", []) if it.get("on")]
    events = [_event_of(it) for it in on]
    interp = p.get("interp") if p.get("interp") in ("linear", "ease") else (doc.get("cfg") or {}).get("interp", "linear")
    step = _clamp(float(p.get("step") or 0.1), 1 / 60, 0.5)
    hard = _boundaries(clips, tl.fps, _cached_acts(clips) if doc.get("mode") == "screen" else None)
    K = camera_keys(events, tl.fps, tl.duration, hard) if events else [[0.0, 1.0, 0.5, 0.5, "hold"]]
    out, total = [], 0
    for c in clips:
        keys = clip_keys(K, c, tl.fps, step, interp) if events else []
        if keys:
            info = M.probe(c.path)
            out.append({"track": track, "start": round(c.start, 6), "end": round(c.end, 6), "in": round(c.src_in, 6),
                        "speed": c.speed, "name": c.name, "w": info["width"], "h": info["height"], "keys": keys})
            total += len(keys)
    fr = 1.0 / tl.fps
    n_reset = 0
    for r in p.get("reset") or []:
        try:
            rs, rt = float(r["start"]), int(r.get("track", track))
            base = {"scale": float(r["scale"]), "pos": [float(r["pos"][0]), float(r["pos"][1])]}
        except (KeyError, TypeError, ValueError, IndexError):
            continue
        hit = next((c for c in out if c["track"] == rt and abs(c["start"] - rs) < fr / 2), None)
        if hit is None:
            hit = {"track": rt, "start": round(rs, 6), "end": round(float(r.get("end", rs)), 6),
                   "in": round(float(r.get("in", 0.0)), 6), "speed": 1.0, "name": r.get("name", ""), "keys": []}
            out.append(hit)
        hit["reset"] = base
        n_reset += 1
    out.sort(key=lambda c: (c["track"], c["start"]))
    return {"v": PLAN_V, "tool": "zoom", "created": now_iso(), "interp": interp, "track": track,
            "seq": {"id": tl.id, "name": tl.name, "width": tl.width, "height": tl.height, "fps": tl.fps},
            "events": len(events), "clips": out,
            "stats": {"clips": sum(1 for c in out if c["keys"]), "keys": total, "reset": n_reset}}, K


def _write_ascii(path, obj):
    """Plan file for ExtendScript (it evals the text, ES3 has no JSON): pure ASCII, atomic."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=True, separators=(",", ":"), allow_nan=False), encoding="ascii")
    os.replace(tmp, path)
    return path


def apply(job, emit):
    """Edited review -> keyframe plan file for panel/host/37_zoom.jsx (bac_zoom_keys). params: reset (list of
    {track, start, end, in, scale, pos} = clips that carry Klipora zoom keys from an earlier run, recorded by the
    panel), interp ("linear" | "ease"), step."""
    p = job.get("params") or {}
    with emit.step("plan", tr("zoom.st.keys")):
        doc = RV.from_job(job)
        if doc.get("tool") != "zoom":
            raise EngineError("BAD_REVIEW", tr("zoom.badReview"), tr("zoom.badReviewHint"))
        tl = Timeline.from_json(job.get("seq"))
        rid = (doc.get("seq") or {}).get("id")
        if rid and tl.id and rid != tl.id:
            raise EngineError("SEQ_CHANGED", tr("zoom.seqChanged", name=(doc.get("seq") or {}).get("name")),
                              tr("zoom.seqChangedHint"))
        plan, _K = _plan_doc(doc, tl, p, emit)
        if not plan["events"] and not plan["stats"]["reset"]:
            raise EngineError("NO_ZOOM", tr("zoom.noneOn"), tr("zoom.noneOnHint"))
        path = Path(workdir(job, tl.name)) / "zoom_keys.json"
        _write_ascii(path, plan)
        emit.progress(100)
    st = plan["stats"]
    return {"plan": {"kind": "keyframes", "path": str(path), "seq": plan["seq"], "events": plan["events"],
                     "clips": st["clips"], "keys": st["keys"], "reset": st["reset"], "interp": plan["interp"],
                     "ops": len(plan["clips"])},
            "summary": tr("zoom.applySummary", n=plan["events"], clips=st["clips"], keys=st["keys"])}


# ====================================================================== preview render

def _render(pieces, K, out, size, fps=30, emit=None):
    """Decode every piece (src path, src a, src b, seq start), apply the camera per frame (warpAffine) and
    encode; audio of the same pieces is concatenated. NVENC first, libx264 when NVENC is missing or fails."""
    cv2 = _cv2()
    W, H = size
    kt = [k[0] for k in K]
    total = max(1, sum(int(round((b - a) * fps)) for _p, a, b, _s in pieces))
    has_audio = all(M.probe(pp)["has_audio"] for pp, *_r in pieces)

    def encode(codec):
        cmd = [ffmpeg_exe(), "-hide_banner", "-nostdin", "-loglevel", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24",
               "-s", f"{W}x{H}", "-r", str(fps), "-i", "-"]
        if has_audio:
            for pp, a, b, _s in pieces:
                cmd += ["-ss", f"{a:.4f}", "-t", f"{b - a:.4f}", "-i", str(pp)]
            fc = "".join(f"[{i + 1}:a]" for i in range(len(pieces))) + f"concat=n={len(pieces)}:v=0:a=1[a]"
            cmd += ["-filter_complex", fc, "-map", "0:v", "-map", "[a]", "-c:a", "aac", "-b:a", "128k"]
        else:
            cmd += ["-an"]
        cmd += (["-c:v", "h264_nvenc", "-preset", "p4", "-cq", "26"] if codec == "nvenc"
                else ["-c:v", "libx264", "-preset", "veryfast", "-crf", "26"])
        cmd += ["-pix_fmt", "yuv420p", "-movflags", "+faststart", "-shortest", str(out)]
        errf = out.with_suffix(".log")
        with open(errf, "wb") as ef:
            enc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=ef, creationflags=_NO_WINDOW)
            n = 0
            try:
                for pp, a, b, s0 in pieces:
                    for k, fr in _frames(pp, fps, W, gray=False, start=a, dur=b - a, skip=None):
                        s, cx, cy = state_at(K, s0 + k / fps, kt)
                        if fr.shape[1] != W or fr.shape[0] != H:
                            fr = cv2.resize(fr, (W, H), interpolation=cv2.INTER_AREA)
                        if s > 1.0001:
                            m = np.float32([[s, 0, W * (0.5 - cx * s)], [0, s, H * (0.5 - cy * s)]])
                            fr = cv2.warpAffine(fr, m, (W, H), flags=cv2.INTER_LINEAR)
                        enc.stdin.write(np.ascontiguousarray(fr).tobytes())
                        n += 1
                        if emit is not None and n % 15 == 0:
                            emit.progress(min(99.0, 100.0 * n / total))
                enc.stdin.close()
            except (BrokenPipeError, OSError):
                pass
            except BaseException:
                enc.kill()
                enc.wait()
                raise
            rc = enc.wait()
        err = errf.read_bytes().decode("utf-8", "replace").strip()
        errf.unlink(missing_ok=True)
        return rc, n, err

    rc, n, err = encode("nvenc")
    enc_name = "h264_nvenc"
    if rc != 0 or not out.is_file() or out.stat().st_size < 1000:
        log().info(f"zoom preview: NVENC failed ({err[-200:]}), retry libx264")
        rc, n, err = encode("x264")
        enc_name = "libx264"
    if rc != 0 or not out.is_file():
        raise EngineError("FFMPEG", tr("zoom.previewFail", msg=err.splitlines()[-1][:200] if err else f"exit {rc}"), err[-600:])
    return n, enc_name


def preview(job, emit):
    """params: t0, t1 (sequence seconds, max 60 s), ids (optional: only these review items, on or off).
    Renders the target track with the zoom curve at half size (max 1146 px wide), 30 fps."""
    p = job.get("params") or {}
    with emit.step("prep", tr("zoom.st.prep")):
        doc = RV.from_job(job)
        tl = Timeline.from_json(job.get("seq"))
        rid = (doc.get("seq") or {}).get("id")
        if rid and tl.id and rid != tl.id:
            raise EngineError("SEQ_CHANGED", tr("zoom.seqChangedShort"), tr("zoom.seqChangedPvHint"))
        track = doc.get("track") if isinstance(doc.get("track"), int) else None
        track, clips, _m = _target(tl, {"track": track})
        ids = set(p.get("ids") or [])
        its = [it for it in doc.get("items", []) if (it["id"] in ids if ids else it.get("on"))]
        hard = _boundaries(clips, tl.fps, _cached_acts(clips) if doc.get("mode") == "screen" else None)
        K = camera_keys([_event_of(it) for it in its], tl.fps, tl.duration, hard) if its else [[0.0, 1.0, 0.5, 0.5, "hold"]]
        t0 = _clamp(float(p.get("t0", its[0]["t0"] - 1.0 if its else 0.0)), 0.0, tl.duration)
        t1 = _clamp(float(p.get("t1", its[0]["t1"] + 1.0 if its else t0 + 10.0)), t0, tl.duration)
        t1 = min(t1, t0 + 60.0)
        if t1 - t0 < 0.2:
            raise EngineError("BAD_PARAM", tr("zoom.pvTooShort"))
        pieces = []
        for c in clips:
            a, b = max(t0, c.start), min(t1, c.end)
            if b - a > 1e-3:
                pieces.append((c.path, c.seq_to_src(a), c.seq_to_src(b), a))
        if not pieces:
            raise EngineError("NO_VIDEO", tr("zoom.pvNoVideo"), tr("zoom.pvNoVideoHint"))
        info = M.probe(pieces[0][0])
        W = min(1146, info["width"] - info["width"] % 2)
        H = int(round(info["height"] * W / info["width"] / 2)) * 2
        outdir = Path(workdir(job, tl.name)) / "zoom_preview"
        outdir.mkdir(parents=True, exist_ok=True)
        ensure_free(150 * 1024 ** 2, outdir)
        olds = sorted(outdir.glob("zoom_*.mp4"), key=lambda f: f.stat().st_mtime)
        for f in olds[:-2]:   # keep the last 2 previews only
            f.unlink(missing_ok=True)
        out = outdir / f"zoom_{int(t0 * 100):07d}_{int(t1 * 100):07d}_{time.strftime('%H%M%S')}.mp4"
        emit.progress(100)
    with emit.step("render", tr("zoom.st.render")):
        t_start = time.time()
        n, enc = _render(pieces, K, out, (W, H), emit=emit)
        emit.done_note(enc.replace("h264_", "").upper())
        emit.progress(100)
    return {"path": str(out), "t0": round(t0, 3), "t1": round(t1, 3), "frames": n, "encoder": enc,
            "size": [W, H], "secs": round(time.time() - t_start, 2)}


ACTIONS = {"analyze": analyze, "apply": apply, "preview": preview}
