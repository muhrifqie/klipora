"""Video analysis for Auto Resize (helper of ac.tools.resize, not a tool itself: the leading underscore keeps it out
of the tool registry).

  frames(path, fps, width, ...)   ffmpeg pipe sampler (CPU decode, `-skip_frame noref` for analysis only)
  grab(path, t, width)            one BGR frame, frame-accurate seek
  activity(path, ...)             screen-recording action map: per 0.1 s sample the kind (still / local / global page
                                  change) + changed pixels per cell of a 48x20 grid, distractor cells (spinners,
                                  clocks, taskbar badges) muted. Cached next to the media: <stem>_resize_act.npz
  faces(path, ...)                YuNet faces (+5 landmarks) at 5 fps + mouth-motion energy per face + hard cuts.
                                  Cached next to the media: <stem>_resize_faces.json
  probe_faces(path)               ~24 keyframes -> is this a talking head (faces big enough to frame) or a screen?

Ported from proto/vision (vio.py, faces.py, reframe.py: verified, docs/research/vision.md) and
docs/research/proto_visual/activity.py (docs/research/product_visual.md 4.0). Measured there on this PC: activity
34.6 min -> ~83 s (25x realtime), YuNet 4.6 ms/frame at 640 px (OpenCV 5.0; Haar and Caffe SSD are gone in 5.0).
cv2 is imported lazily (cli.py tools imports every tool module) and OPENCV_LOG_LEVEL is set before that import.
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time
from pathlib import Path

import numpy as np

from .. import media as M
from ..i18n import tr
from ..util import EngineError, cache_path, ffmpeg_exe, file_hash, read_json, write_json

os.environ.setdefault("OPENCV_LOG_LEVEL", "ERROR")   # OpenCV 5 prints a WARN per FaceDetectorYN.create otherwise

MODEL = Path(__file__).resolve().parents[1] / "assets" / "models" / "face_detection_yunet_2026may.onnx"
ACT_V = 1
FACE_V = 1
GW, GH = 48, 20                     # activity grid (2292x960 -> ~48 px cells)
STILL, LOCAL, GLOBAL = 0, 1, 2
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _cv2():
    import cv2  # noqa: PLC0415  (heavy import, only when analysing)
    return cv2


# ---------------------------------------------------------------- frame io

def out_size(w, h, width):
    """Even output size for a target width (keeps aspect). width None/>= w -> native (even)."""
    if not width or width >= w:
        return w - w % 2, h - h % 2
    oh = int(round(h * width / w))
    return int(width) - int(width) % 2, oh - oh % 2


def frames(path, fps, width=None, gray=False, start=0.0, duration=None, skip=None, size=None):
    """Yield (t_source_seconds, ndarray) sampled at `fps`. t = start + k/fps (the fps filter emits a regular grid;
    `-ss` before `-i` resets output timestamps to 0). skip='noref' halves decode time for ANALYSIS (picks the
    nearest decodable frame, < 2 frames off at 120 fps); never use it for rendering. size=(w, h) forces a size.
    Frames are read-only views: copy before drawing on them."""
    info = M.probe(path)
    if not info["has_video"]:
        raise EngineError("NO_VIDEO", tr("resize.noVideoStream", name=Path(path).name))
    ow, oh = size if size else out_size(info["width"], info["height"], width)
    pix, ch = ("gray", 1) if gray else ("bgr24", 3)
    cmd = [ffmpeg_exe(), "-v", "error", "-nostdin"]
    if skip:
        cmd += ["-skip_frame", skip]
    if start and start > 0:
        cmd += ["-ss", f"{start:.4f}"]
    cmd += ["-i", str(path)]
    if duration:
        cmd += ["-t", f"{duration:.4f}"]
    cmd += ["-an", "-sn", "-dn", "-vf", f"fps={fps},scale={ow}:{oh}:flags=area", "-f", "rawvideo", "-pix_fmt", pix, "-"]
    n = ow * oh * ch
    # stderr discarded: closing the pipe early (consumer stops reading) makes ffmpeg print 'Broken pipe'
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
                         bufsize=n * 4, creationflags=_NO_WINDOW)
    k = 0
    try:
        while True:
            buf = p.stdout.read(n)
            if len(buf) < n:
                break
            yield (start or 0.0) + k / fps, np.frombuffer(buf, np.uint8).reshape((oh, ow) if gray else (oh, ow, ch))
            k += 1
    finally:
        try:
            p.stdout.close()
        finally:
            p.kill()
            p.wait()
    if k == 0:
        raise EngineError("FFMPEG", tr("resize.noFrames", name=Path(path).name), tr("resize.noFramesHint"))


def grab(path, t, width=None, accurate=True):
    """One BGR frame at t (seconds). accurate=False = nearest keyframe (fast probe)."""
    info = M.probe(path)
    w, h = out_size(info["width"], info["height"], width)
    t = max(0.0, min(float(t), max(0.0, info["duration"] - 2.0 / max(info["fps"], 1.0))))
    args = [ffmpeg_exe(), "-v", "error", "-nostdin"]
    if not accurate:
        args += ["-noaccurate_seek"]
    args += ["-ss", f"{t:.4f}", "-i", str(path), "-frames:v", "1", "-an", "-vf", f"scale={w}:{h}:flags=area",
             "-f", "rawvideo", "-pix_fmt", "bgr24", "-"]
    r = subprocess.run(args, capture_output=True, stdin=subprocess.DEVNULL, creationflags=_NO_WINDOW)
    if len(r.stdout) < w * h * 3:
        return None
    return np.frombuffer(r.stdout[:w * h * 3], np.uint8).reshape(h, w, 3).copy()


def scene_cut(path, t0, t1, width=160, threshold=0.25):
    """Exact hard-cut time inside [t0, t1] from ffmpeg's scene score on a tiny full-rate stream, or None."""
    import re
    t0 = max(0.0, t0)
    r = subprocess.run([ffmpeg_exe(), "-hide_banner", "-nostdin", "-ss", f"{t0:.4f}", "-i", str(path),
                        "-t", f"{max(0.05, t1 - t0):.4f}", "-an",
                        "-vf", f"scale={width}:-2,select='gt(scene,{threshold})',showinfo", "-f", "null", "-"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace",
                       stdin=subprocess.DEVNULL, creationflags=_NO_WINDOW)
    hits = [float(x) for x in re.findall(r"pts_time:([\d.]+)", r.stderr or "")]
    return round(t0 + hits[0], 4) if hits else None


# ---------------------------------------------------------------- small io helpers

def _cache_file(path, suffix, cache_dir=None):
    if cache_dir:
        return Path(cache_dir) / (Path(path).stem + suffix)
    return cache_path(path, suffix)


def _save_npz(p, **arrays):
    """Atomic np.savez_compressed (tmp in the same folder + os.replace)."""
    p = Path(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=p.stem + ".", suffix=".npz", dir=str(p.parent))
    os.close(fd)
    try:
        np.savez_compressed(tmp, **arrays)
        os.replace(tmp, p)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return p


class _Prog:
    """Maps a 0..100 helper progress onto a callback (or nothing). Calling it is the cancel point."""

    def __init__(self, fn=None):
        self.fn = fn

    def __call__(self, pct):
        if self.fn is not None:
            self.fn(max(0.0, min(100.0, pct)))


# ---------------------------------------------------------------- screen activity

class Activity:
    """Activity map of one media (source time). Sample j covers the change between frames j-1 and j at t[j]."""

    def __init__(self, d, path=None):
        self.path = path
        self.t = d["t"].astype(np.float64)
        self.frac = d["frac"].astype(np.float64)
        self.kind = d["kind"].astype(np.int8)
        self.ptr = d["ptr"].astype(np.int64)
        self.idx = d["idx"].astype(np.int64)
        self.val = d["val"].astype(np.float64)
        self.meta = json.loads(str(d["meta"]))
        self.fps = float(self.meta["fps"])
        self.gw, self.gh = int(self.meta.get("gw", GW)), int(self.meta.get("gh", GH))
        self.mute = np.zeros(self.gw * self.gh, bool)
        dis = d["dis"].astype(np.int64)
        self.mute[dis[(dis >= 0) & (dis < self.gw * self.gh)]] = True
        self.cx = (np.arange(self.gw * self.gh) % self.gw + 0.5) / self.gw
        self.cy = (np.arange(self.gw * self.gh) // self.gw + 0.5) / self.gh

    def __len__(self):
        return len(self.t)

    def index(self, s):
        """Sample index for source time(s) s (clipped)."""
        return np.clip(np.rint(np.asarray(s, float) * self.fps).astype(np.int64), 0, len(self.t) - 1)

    def band_weights(self, on=True):
        """Screen chrome counts less as a framing target: taskbar, scrollbar, notification toast corner and (a bit)
        the browser bar. Without it a Windows toast stole the 9:16 camera (product_visual.md 4.0 step 5)."""
        w = np.ones(self.gw * self.gh)
        if not on:
            return w
        cx, cy = self.cx, self.cy
        w[cy < 0.08] = 0.35                                   # tabs / address bar: real typing happens there
        w[(cx < 0.03) | (cx > 0.97)] = 0.1                    # window edges, scrollbar
        w[cy > 0.92] = 0.05                                   # taskbar
        w[(cx > 0.80) & (cy > 0.75)] = 0.02                   # notification toasts
        return w

    def mass(self, j, weights):
        """(mass, sum x*m, sum y*m) of the non-muted cells of sample j."""
        a, b = self.ptr[j], self.ptr[j + 1]
        if b <= a:
            return 0.0, 0.0, 0.0
        ii = self.idx[a:b]
        v = self.val[a:b] * weights[ii] * ~self.mute[ii]
        m = float(v.sum())
        return m, float((v * self.cx[ii]).sum()), float((v * self.cy[ii]).sum())


def activity(path, fps=10.0, width=576, prog=None, refresh=False, cache_dir=None):
    """Activity map of a screen recording (cached). -> Activity. prog(pct) is called during decoding."""
    prog = _Prog(prog)
    M.require(path)
    cfile = _cache_file(path, "_resize_act.npz", cache_dir)
    key = {"v": ACT_V, "hash": file_hash(path), "fps": float(fps), "width": int(width)}
    if not refresh and cfile.is_file():
        try:
            with np.load(cfile, allow_pickle=False) as z:
                d = {k: z[k] for k in z.files}
            meta = json.loads(str(d["meta"]))
            if meta.get("key") == key:
                a = Activity(d, path)
                a.cached = True
                return a
        except (OSError, ValueError, KeyError):
            pass
    d = _activity_compute(path, fps, width, prog)
    d["meta"] = np.array(json.dumps({"key": key, "fps": fps, "gw": GW, "gh": GH, "width": width,
                                     "secs": d.pop("_secs"), "src": [d.pop("_w"), d.pop("_h")]}))
    try:
        _save_npz(cfile, **d)
    except OSError:
        pass                                                   # read-only folder etc.: still usable this run
    a = Activity(d, path)
    a.cached = False
    return a


def _activity_compute(path, fps, width, prog):
    """Frame-difference action map (port of proto_visual/activity.py + a cursor discount):
    absdiff > 18 then 3x3 open -> changed fraction; < 0.04 % still, > 12 % global (scroll, page switch, cut),
    else local: changed pixels area-summed into the grid. Tiny compact blobs (cursor, one typed character) count
    half so a dropdown that opens beats cursor travel elsewhere."""
    cv2 = _cv2()
    info = M.probe(path)
    ow, oh = out_size(info["width"], info["height"], width)
    n_exp = max(1, int(info["duration"] * fps))
    k3 = np.ones((3, 3), np.uint8)
    k5 = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
    sc = ow / 576.0
    small_w, small_h = 14 * sc, 16 * sc
    area_k = (ow * oh) / float(GW * GH)
    ts, fr, kd, ptr, idx, val = [], [], [], [0], [], []
    prev = None
    t0 = time.perf_counter()
    k = 0
    for k, (t, g) in enumerate(frames(path, fps, ow, gray=True, skip="noref")):
        if prev is None:
            prev = g
            ts.append(t)
            fr.append(0.0)
            kd.append(STILL)
            ptr.append(len(idx))
            continue
        m = (cv2.absdiff(g, prev) > 18).astype(np.uint8)
        prev = g
        m = cv2.morphologyEx(m, cv2.MORPH_OPEN, k3)
        f = float(m.mean())
        kind = STILL if f < 0.0004 else GLOBAL if f > 0.12 else LOCAL
        if kind == LOCAL:
            n, lab, st, _ = cv2.connectedComponentsWithStats(cv2.dilate(m, k5), connectivity=8)
            fac = np.ones(n, np.float32)
            small = (st[:, 2] <= small_w) & (st[:, 3] <= small_h)
            small[0] = False
            fac[small] = 0.5
            grid = cv2.resize(m.astype(np.float32) * fac[lab], (GW, GH), interpolation=cv2.INTER_AREA) * area_k
            nz = np.flatnonzero(grid > 2)
            idx.extend(nz.tolist())
            val.extend(grid.ravel()[nz].tolist())
        ts.append(t)
        fr.append(f)
        kd.append(kind)
        ptr.append(len(idx))
        if k % 25 == 0:
            prog(98.0 * k / n_exp)
    t_arr = np.array(ts, np.float32)
    frac = np.array(fr, np.float32)
    kind = np.array(kd, np.int8)
    ptr_a = np.array(ptr, np.int32)
    idx_a = np.array(idx, np.int16)
    val_a = np.array(val, np.float32)
    dis = _distractors(t_arr, frac, kind, ptr_a, idx_a)
    prog(100)
    return {"t": t_arr, "frac": frac, "kind": kind, "ptr": ptr_a, "idx": idx_a, "val": val_a,
            "dis": np.array(dis, np.int16), "_secs": round(time.perf_counter() - t0, 2),
            "_w": info["width"], "_h": info["height"]}


def _distractors(t, frac, kind, ptr, idx):
    """UI noise (tab spinner, clock, taskbar badge) = the SAME cells lighting up in tiny isolated changes again and
    again. Threshold relative to the number of 10 s bins that had tiny events at all (an absolute count muted 31 % of
    the grid on the 35 min recording because the cursor eventually visits everything)."""
    tiny = np.flatnonzero((kind == LOCAL) & (frac < 0.002))
    if not len(tiny):
        return []
    nb = int(t[-1] // 10) + 1 if len(t) else 1
    cnt = np.zeros(GW * GH, np.int64)
    bins = np.zeros((GW * GH, nb), bool)
    for j in tiny:
        cells = idx[ptr[j]:ptr[j + 1]].astype(np.int64)
        if not len(cells):
            continue
        cnt[cells] += 1
        bins[cells, int(t[j] // 10)] = True
    used_bins = int(bins.any(axis=0).sum())
    nbins = bins.sum(axis=1)
    return np.flatnonzero((cnt >= 5) & (nbins >= max(3, 0.06 * used_bins))).tolist()


# ---------------------------------------------------------------- faces

class FaceDetector:
    """YuNet wrapper. detect(frame) -> float32 N x 15 in frame pixels: x, y, w, h, 5 landmarks (right eye, left eye,
    nose, mouth right, mouth left) as x,y pairs, score. YuNet finds faces of roughly 10..300 px: feed ~640 px frames."""

    def __init__(self, model=MODEL, score=0.7, nms=0.3, top_k=50, min_rel=0.03):
        if not Path(model).is_file():
            raise EngineError("NO_MODEL", tr("resize.noFaceModel"), tr("resize.noFaceModelHint", name=Path(model).name))
        self.model, self.score, self.nms, self.top_k, self.min_rel = str(model), score, nms, top_k, min_rel
        self.det, self.size = None, None

    def detect(self, frame):
        cv2 = _cv2()
        h, w = frame.shape[:2]
        if frame.ndim == 2:
            frame = cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)
        if self.det is None:
            self.det = cv2.FaceDetectorYN.create(self.model, "", (w, h), self.score, self.nms, self.top_k)
            self.size = (w, h)
        elif self.size != (w, h):
            self.det.setInputSize((w, h))
            self.size = (w, h)
        faces = self.det.detect(frame)[1]
        if faces is None:
            return np.zeros((0, 15), np.float32)
        return faces[faces[:, 3] >= self.min_rel * h]        # drop tiny (background / false positive) faces


def iou(a, b):
    ax2, ay2, bx2, by2 = a[0] + a[2], a[1] + a[3], b[0] + b[2], b[1] + b[3]
    iw = max(0.0, min(ax2, bx2) - max(a[0], b[0]))
    ih = max(0.0, min(ay2, by2) - max(a[1], b[1]))
    inter = iw * ih
    return inter / (a[2] * a[3] + b[2] * b[3] - inter + 1e-9)


def mouth_patch(gray, f):
    """Landmark-aligned mouth patch (32x24, z-normalised) or None. Eye distance is the scale because the mouth
    width itself changes while talking."""
    cv2 = _cv2()
    re_, le_, mr, ml = f[4:6], f[6:8], f[10:12], f[12:14]
    ed = float(np.hypot(*(le_ - re_)))
    if ed < 6:
        return None
    m = (mr + ml) / 2
    x0, x1 = int(m[0] - 0.6 * ed), int(m[0] + 0.6 * ed)
    y0, y1 = int(m[1] - 0.35 * ed), int(m[1] + 0.5 * ed)
    if x0 < 0 or y0 < 0 or x1 > gray.shape[1] or y1 > gray.shape[0] or x1 - x0 < 4 or y1 - y0 < 4:
        return None
    p = cv2.resize(gray[y0:y1, x0:x1], (32, 24), interpolation=cv2.INTER_AREA).astype(np.float32)
    return (p - p.mean()) / (p.std() + 8.0)


def faces(path, fps=5.0, width=640, prog=None, refresh=False, cache_dir=None):
    """Faces per sample + hard cuts (cached). -> {"fps", "src": [W, H], "duration", "samples": [[t, [[16 values]]]],
    "cuts": [t]}. Face row = x, y, w, h, 10 landmark coords (normalised 0..1), score, mouth energy (or None)."""
    prog = _Prog(prog)
    M.require(path)
    cfile = _cache_file(path, "_resize_faces.json", cache_dir)
    key = {"v": FACE_V, "hash": file_hash(path), "fps": float(fps), "width": int(width)}
    doc = read_json(cfile, {}) if cfile.is_file() else {}
    if not isinstance(doc, dict) or doc.get("hash") != key["hash"]:
        doc = {}
    full = doc.get("full")
    if not refresh and isinstance(full, dict) and full.get("key") == key:
        full["cached"] = True
        return full
    full = _faces_compute(path, fps, width, prog)
    full["key"] = key
    doc.update({"v": FACE_V, "hash": key["hash"], "full": full})
    try:
        write_json(cfile, doc)
    except OSError:
        pass
    full["cached"] = False
    return full


def _faces_compute(path, fps, width, prog):
    """Port of proto/vision/reframe.analyze: YuNet + mouth energy vs the best-overlapping face of the previous sample.
    Hard cuts: big mean difference between consecutive 64x36 thumbnails, refined to the exact frame with ffmpeg's
    scene score on a 0.5 s window (instead of a full-rate scene pass: that was the most expensive step, 140 s on
    the 34.6 min file)."""
    cv2 = _cv2()
    info = M.probe(path)
    det = FaceDetector(score=0.7, min_rel=0.03)
    n_exp = max(1, int(info["duration"] * fps))
    samples, prev, prev_thumb, cand = [], [], None, []
    t0 = time.perf_counter()
    last_t = 0.0
    for k, (t, fr) in enumerate(frames(path, fps, width, skip="noref")):
        h, w = fr.shape[:2]
        gray = cv2.cvtColor(fr, cv2.COLOR_BGR2GRAY)
        thumb = cv2.resize(gray, (64, 36), interpolation=cv2.INTER_AREA).astype(np.float32)
        if prev_thumb is not None and float(np.abs(thumb - prev_thumb).mean()) > 28:
            cand.append((last_t, t))
        prev_thumb = thumb
        found = det.detect(fr)
        cur = []
        for f in found:
            p = mouth_patch(gray, f)
            e = None
            best = max(prev, key=lambda q: iou(q[0][:4], f[:4]), default=None)
            if p is not None and best is not None and best[1] is not None and iou(best[0][:4], f[:4]) > 0.3:
                e = float(np.mean(np.abs(p - best[1])))
            cur.append((f, p, e))
        prev = cur
        row = []
        for f, _p, e in cur:
            nrm = f.astype(np.float64).copy()
            nrm[0:14:2] /= w
            nrm[1:14:2] /= h
            row.append([round(float(x), 5) for x in nrm[:15]] + [None if e is None else round(e, 4)])
        samples.append([round(t, 4), row])
        last_t = t
        if k % 10 == 0:
            prog(90.0 * k / n_exp)
    cuts = []
    for i, (a, b) in enumerate(cand):
        c = scene_cut(path, a - 0.05, b + 0.05)
        cuts.append(c if c is not None else round(b, 4))
        prog(90 + 10.0 * (i + 1) / len(cand))
    prog(100)
    return {"fps": fps, "src": [info["width"], info["height"]], "duration": info["duration"], "samples": samples,
            "cuts": sorted(set(cuts)), "secs": round(time.perf_counter() - t0, 2)}


def probe_faces(path, n=24, width=640, cache_dir=None):
    """Cheap content check: YuNet on ~n evenly spaced keyframes. -> {"ratio", "n", "hits", "big"}. A talking head has
    a face >= 8 % of the frame height in most probes; screen recordings only show small faces inside web pages
    (vision.md 1.8), so `ratio` stays low there. Cached inside <stem>_resize_faces.json."""
    M.require(path)
    cfile = _cache_file(path, "_resize_faces.json", cache_dir)
    h = file_hash(path)
    doc = read_json(cfile, {}) if cfile.is_file() else {}
    if isinstance(doc, dict) and doc.get("hash") == h and isinstance(doc.get("probe"), dict) \
            and doc["probe"].get("n_req") == n:
        return doc["probe"]
    info = M.probe(path)
    det = FaceDetector(score=0.75, min_rel=0.03)
    dur = info["duration"]
    times = [dur * (i + 0.5) / n for i in range(n)] if dur > 0 else [0.0]
    got, hits, big = 0, 0, 0
    for t in times:
        fr = grab(path, t, width, accurate=False)
        if fr is None:
            continue
        got += 1
        f = det.detect(fr)
        if len(f):
            hits += 1
            if float(f[:, 3].max()) >= 0.08 * fr.shape[0]:
                big += 1
    pr = {"n_req": n, "n": got, "hits": hits, "big": big, "ratio": round(big / got, 3) if got else 0.0}
    if not isinstance(doc, dict) or doc.get("hash") != h:
        doc = {"v": FACE_V, "hash": h}
    doc["probe"] = pr
    try:
        write_json(cfile, doc)
    except OSError:
        pass
    return pr


def track(samples, max_gap=1.0, min_iou=0.1, max_jump=0.25):
    """Greedy IoU/centre association across time (port of proto/vision/faces.track).
    samples: [(t, faces N x >=4 normalised)] -> tracks [{id, t: [], box: []}].
    max_jump: max centre distance (fraction of frame width) to still link when IoU is 0."""
    tracks, nid = [], 0
    by_id = {}
    for t, fs in samples:
        live = [trk for trk in tracks if t - trk["t"][-1] <= max_gap]
        used, pairs = set(), []
        for fi, f in enumerate(fs):
            for trk in live:
                b = trk["box"][-1]
                o = iou(f[:4], b)
                d = float(np.hypot(f[0] + f[2] / 2 - b[0] - b[2] / 2, f[1] + f[3] / 2 - b[1] - b[3] / 2))
                if o >= min_iou or d <= max_jump:
                    pairs.append((-(o + (max_jump - d)), fi, trk["id"]))
        pairs.sort()
        assigned = {}
        for _, fi, tid in pairs:
            if fi in assigned or tid in used:
                continue
            assigned[fi] = tid
            used.add(tid)
        for fi, f in enumerate(fs):
            tid = assigned.get(fi)
            if tid is None:
                trk = {"id": nid, "t": [], "box": []}
                tracks.append(trk)
                by_id[nid] = trk
                tid = nid
                nid += 1
            trk = by_id[tid]
            trk["t"].append(t)
            trk["box"].append([float(x) for x in f[:4]])
    return tracks
