"""Angle Otomatis: single-camera "virtual multicam". Every clip (cut) of the main video track gets a static
punch-in angle (Lebar / Sedang / Dekat, default 100 / 118 / 140 %) so jump cuts read as intentional camera
changes. The same angle never follows itself, screen time follows the priority weights (3 / 2 / 1), and the crop
is anchored on the on-screen action (frame-difference activity, for screen recordings) or a face (YuNet).
Optionally long clips are split at sentence starts ("Tambah potongan di awal kalimat") to get more changes;
those splits are razor points the panel applies on the CLONE only.

Actions:
  analyze  -> review file of shots (one row per clip / piece) with the chosen angle, anchor and the Motion values
              of every enabled angle (`opts`), so the panel can switch an angle without another engine run.
  apply    -> {"plan": {"kind": "keyframes", "mode": "static", "tool": "angles", "track", "splits", "items"}}
              applied by panel/host/39_angles.jsx on a clone (razor at `splits`, then static Scale/Position).
  frame    -> one JPEG of the source frame (preview of the crop boxes in the review pane; worker friendly).

Motion math (docs/research/vision.md 4.4, premiere_timeline_api.md 4): Scale % is relative to native pixels;
Position is normalized 0..1 of the sequence frame (26.x) and is where the clip centre lands. Base scale = fill
(`100 * max(seq_w / W, seq_h / H)`, 100 for same-size media), so re-running on an angled clone never compounds.
Activity analysis is ported from docs/research/proto_visual/activity.py (verified prototype), the planner from
docs/research/proto_cutting/angles_proto.py (deficit choice instead of random, plus fit and rhythm rules).
"""
from __future__ import annotations

import json
import math
import os
import subprocess
from pathlib import Path

import numpy as np

from .. import media as M
from .. import ranges as R
from .. import review as RV
from ..i18n import get_lang, secs, tr
from ..timeline import Timeline
from ..util import (EngineError, PROJECT_DIR, cache_path, ensure_free, ffmpeg_exe, file_hash, read_json,
                    workdir)

TITLE = "Angle Otomatis"   # i18n-ignore (fallback of tool.angles.title)
DESCRIPTION = "Variasi crop dari satu kamera: angle berganti di setiap potongan, tidak pernah sama berturut-turut."   # i18n-ignore

ANGLES = ("wide", "medium", "close")
class _Names:
    """NAMES[angle] -> angle name in the current UI language (angles.name.<angle>), translated when read."""

    def __getitem__(self, a):
        return tr("angles.name." + a)


NAMES = _Names()


def _num(v):
    """118 -> '118', 112.5 -> '112,5' / '112.5' (UI language decimal separator)."""
    s = f"{v:g}"
    return s if get_lang() == "en" else s.replace(".", ",")
DEFAULT_SCALES = {"wide": 100.0, "medium": 118.0, "close": 140.0}
SCALE_LIMITS = {"wide": (100.0, 150.0), "medium": (100.0, 220.0), "close": (100.0, 300.0)}
BALANCE = {"lebar": (3.0, 2.0, 1.0), "rata": (1.0, 1.0, 1.0), "dekat": (1.0, 2.0, 3.0)}

# Activity / face analysis (cached per media next to it: <stem>_angles.npz)
VISION_V = 1
AN_FPS = 4.0          # samples per second
AN_W = 640            # analysis width (YuNet sweet spot for talking heads, enough for screen activity)
BIN = 0.5             # seconds per activity bin
GW, GH = 48, 20       # activity grid (2292x960 -> ~48x48 px cells)
DIFF_THR = 18         # gray levels
STILL_FRAC, GLOBAL_FRAC, TINY_FRAC = 0.0004, 0.12, 0.002
MIN_MASS = 20.0       # changed px (analysis scale) needed before an activity anchor is trusted
FACE_EVERY = 4        # run YuNet on every 4th sample (1 per second)
CTX_BEFORE, CTX_AFTER, CTX_WEIGHT = 4.0, 2.0, 0.5   # activity around a shot that still steers its anchor
EDGE_BAND, EDGE_MIN = 0.15, 0.3                      # outer 15 % of the frame fades to 0.3 weight
CORE_MIN = 0.25                                      # share of the activity that must be inside the core


def _edge_prior():
    """(GH, GW) weights: 1 in the middle, down to EDGE_MIN at the frame edge (taskbar, toasts, tab strip)."""
    def ramp(n):
        c = (np.arange(n) + 0.5) / n
        d = np.minimum(c, 1 - c) / EDGE_BAND
        return EDGE_MIN + (1 - EDGE_MIN) * np.clip(d, 0.0, 1.0)
    return np.outer(ramp(GH), ramp(GW)).astype(np.float32)


# ================================================================ params

def _params(p):
    """Normalise the panel params. Raises BAD_PARAMS with an Indonesian message."""
    p = dict(p or {})
    sc = p.get("scales")
    if isinstance(sc, (list, tuple)):
        sc = dict(zip(ANGLES, sc))
    scales = {}
    for a in ANGLES:
        v = (sc or {}).get(a, DEFAULT_SCALES[a]) if isinstance(sc, dict) else DEFAULT_SCALES[a]
        try:
            v = float(v)
        except (TypeError, ValueError):
            v = DEFAULT_SCALES[a]
        lo, hi = SCALE_LIMITS[a]
        scales[a] = round(min(hi, max(lo, v)), 2)
    use = [a for a in ANGLES if a in (p.get("use") or ANGLES)]
    if len(use) < 2:
        raise EngineError("BAD_PARAMS", tr("angles.min2"), tr("angles.min2Hint"))
    vals = [scales[a] for a in use]
    if any(b - a < 2.0 for a, b in zip(vals, vals[1:])):
        raise EngineError("BAD_PARAMS", tr("angles.scaleOrder"), tr("angles.scaleOrderHint"))
    w = p.get("weights")
    if isinstance(w, str) or w is None:
        w = BALANCE.get(str(w or "lebar"), BALANCE["lebar"])
    if isinstance(w, dict):
        w = [w.get(a, 1.0) for a in ANGLES]
    weights = {a: max(0.0, float(x)) for a, x in zip(ANGLES, list(w) + [1.0] * 3)}
    if sum(weights[a] for a in use) <= 0:
        weights = {a: 1.0 for a in ANGLES}
    track = p.get("track", "auto")
    if track not in (None, "", "auto"):
        try:
            track = int(track)
        except (TypeError, ValueError):
            track = "auto"
    return {
        "scales": scales, "use": use, "weights": weights,
        "mode": "rhythm" if p.get("mode") == "rhythm" else "cut",
        "min_shot": min(10.0, max(0.5, float(p.get("min_shot", 2.5) or 2.5))),
        "split": bool(p.get("split", False)),
        "every": min(20.0, max(2.0, float(p.get("every", 6.0) or 6.0))),
        "anchor": p.get("anchor") if p.get("anchor") in ("auto", "activity", "face", "center") else "auto",
        "track": track if track not in (None, "") else "auto",
        "scope": p.get("scope"),
    }


# ================================================================ geometry

def base_scale(src_wh, seq_wh):
    """Scale % that makes the clip fill the sequence frame (100 for same-size media)."""
    w, h = src_wh
    sw, sh = seq_wh
    if not w or not h or not sw or not sh:
        return 100.0
    return round(100.0 * max(sw / w, sh / h), 4)


def place(ax, ay, scale_pct, base_pct, src_wh, seq_wh):
    """Static Motion values that centre normalized source point (ax, ay) at `scale_pct` of the fill size,
    clamped so the frame stays covered. -> {scale (Premiere %), pos [x, y] normalized, view [x0, y0, w, h]
    (visible part of the source, normalized)}."""
    w, h = src_wh if src_wh[0] and src_wh[1] else seq_wh
    sw, sh = seq_wh
    s = base_pct * scale_pct / 10000.0             # fraction of native pixels
    kx, ky = w * s / sw, h * s / sh                 # clip size in frame widths / heights
    px, py = 0.5 + (0.5 - ax) * kx, 0.5 + (0.5 - ay) * ky
    px = min(kx / 2, max(1 - kx / 2, px)) if kx >= 1 else 0.5
    py = min(ky / 2, max(1 - ky / 2, py)) if ky >= 1 else 0.5
    vw, vh = min(1.0, 1 / kx), min(1.0, 1 / ky)
    vx, vy = 0.5 - (px - 0.5) / kx, 0.5 - (py - 0.5) / ky
    return {"scale": round(base_pct * scale_pct / 100.0, 3), "pos": [round(px, 5), round(py, 5)],
            "view": [round(vx - vw / 2, 4), round(vy - vh / 2, 4), round(vw, 4), round(vh, 4)]}


def fits(spread, view, margin=0.97):
    """True when the action box (x0, y0, x1, y1) fits inside the visible window of an angle."""
    if not spread:
        return True
    return (spread[2] - spread[0]) <= view[2] * margin and (spread[3] - spread[1]) <= view[3] * margin


# ================================================================ vision: activity + faces (cached per media)

def _model_path():
    """YuNet model (docs/research/vision.md 3). Search the engine first, then the research prototypes."""
    names = ("face_detection_yunet_2026may.onnx", "face_detection_yunet_2023mar.onnx")
    dirs = (PROJECT_DIR / "engine" / "ac" / "assets" / "models",
            PROJECT_DIR / "engine" / "models", PROJECT_DIR / "engine" / "ac" / "models",
            PROJECT_DIR / "proto" / "vision" / "models", PROJECT_DIR / "docs" / "research" / "proto_visual" / "models")
    for d in dirs:
        for n in names:
            if (d / n).is_file():
                return d / n
    return None


def _face_detector(size):
    """cv2.FaceDetectorYN or None (no model / OpenCV without YuNet)."""
    model = _model_path()
    if model is None:
        return None
    os.environ.setdefault("OPENCV_LOG_LEVEL", "ERROR")   # silence OpenCV 5 target warnings (vision.md 1.3)
    import cv2
    if not hasattr(cv2, "FaceDetectorYN"):
        return None
    try:
        det = cv2.FaceDetectorYN.create(str(model), "", size, 0.7, 0.3, 50)
        det.setInputSize(size)
        return det
    except cv2.error:
        return None


class Vision:
    """Per-media activity grid (BIN-second bins) + face samples, analysed only where shots need them."""

    def __init__(self, path, info):
        self.path = str(path)
        self.w, self.h = int(info.get("width") or 0), int(info.get("height") or 0)
        self.dur = float(info.get("duration") or 0.0)
        self.ow = AN_W
        self.oh = max(2, int(round(self.h * AN_W / max(self.w, 1) / 2)) * 2) if self.w else 2
        nb = int(math.ceil(self.dur / BIN)) + 1
        self.grid = np.zeros((nb, GH, GW), np.float32)
        self.glob = np.zeros(nb, np.uint8)
        self.done = np.zeros(nb, bool)                           # activity analysed
        self.fdone = np.zeros(nb, bool)                          # faces analysed (or no detector)
        n10 = int(math.ceil(self.dur / 10.0)) + 1
        self.tiny = np.zeros((n10, GH, GW), np.uint16)          # tiny isolated changes per 10 s bin and cell
        self.faces = np.zeros((0, 6), np.float32)                # t, cx, cy, w, h, score (normalized)
        self.fsamp = np.zeros(0, np.float32)                     # times where YuNet ran
        self.face_ok = None                                       # None = never tried, False = no detector
        self._distr = None
        self.cache = cache_path(path, "_angles.npz")
        self.hash = file_hash(path)

    # ------------------------------------------------------------ cache
    def meta(self):
        return {"v": VISION_V, "hash": self.hash, "fps": AN_FPS, "bin": BIN, "ow": self.ow, "oh": self.oh,
                "gw": GW, "gh": GH, "dur": self.dur, "face_ok": self.face_ok}

    def load(self):
        try:
            if not self.cache.is_file():
                return False
            with np.load(self.cache, allow_pickle=False) as z:
                meta = json.loads(str(z["meta"]))
                want = self.meta()
                if any(meta.get(k) != want[k] for k in ("v", "hash", "fps", "bin", "ow", "gw", "gh")):
                    return False
                if z["grid"].shape != self.grid.shape:
                    return False
                self.grid = z["grid"].astype(np.float32)
                self.glob, self.done, self.tiny = z["glob"], z["done"].astype(bool), z["tiny"]
                self.fdone = z["fdone"].astype(bool)
                self.faces, self.fsamp = z["faces"], z["fsamp"]
                self.face_ok = meta.get("face_ok")
            return True
        except (OSError, ValueError, KeyError):
            return False

    def save(self):
        tmp = self.cache.with_name(self.cache.name + ".tmp.npz")
        try:
            ensure_free(8 * 1024 ** 2, self.cache.parent, margin=64 * 1024 ** 2)
            np.savez_compressed(tmp, meta=np.array(json.dumps(self.meta())), grid=self.grid.astype(np.float16),
                                glob=self.glob, done=self.done, fdone=self.fdone, tiny=self.tiny, faces=self.faces,
                                fsamp=self.fsamp)
            os.replace(tmp, self.cache)
            return True
        except (OSError, EngineError):
            try:
                tmp.unlink()
            except OSError:
                pass
            return False

    # ------------------------------------------------------------ analysis
    def missing(self, spans, faces):
        """Source ranges [[s0, s1]] still to analyse (merged, 10 s gaps closed)."""
        need = []
        for a, b in spans:
            b0, b1 = max(0, int(a // BIN)), min(len(self.done), int(math.ceil(b / BIN)))
            for k in range(b0, b1):
                if not self.done[k] or (faces and not self.fdone[k]):
                    need.append([k * BIN, (k + 1) * BIN])
        return R.merge_gaps(R.norm(need), 10.0)

    def analyse(self, spans, faces, progress=None):
        """Decode the missing parts (CPU, -skip_frame noref, fps=4 at 640 px) and fill the grid/faces.
        progress(p 0..100) is also the cancel point. Returns seconds analysed."""
        todo = self.missing(spans, faces)
        if not todo:
            return 0.0
        os.environ.setdefault("OPENCV_LOG_LEVEL", "ERROR")   # before the first cv2 import (vision.md 1.3)
        import cv2
        det = _face_detector((self.ow, self.oh)) if faces else None
        if faces:
            self.face_ok = det is not None
        total = sum(b - a for a, b in todo) or 1.0
        k3 = np.ones((3, 3), np.uint8)
        cell = (self.ow * self.oh) / float(GW * GH)
        was, fwas = self.done.copy(), self.fdone.copy()   # never count a bin twice (face-only re-runs)
        done_s, fs, fsamp = 0.0, [], []
        for a, b in todo:
            a0 = max(0.0, a - 0.5)                    # one extra sample so the first bin gets a diff
            prev, k = None, 0
            gen = self._frames(a0, b)
            try:
                for t, frame in gen:
                    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                    self._sample(t, a, k, frame, gray, prev, was, fwas, faces, det, cell, k3, fs, fsamp)
                    prev = gray
                    k += 1
                    if progress is not None:
                        progress(100.0 * (done_s + max(0.0, t - a)) / total)
            finally:
                gen.close()                           # kills ffmpeg on cancel / errors
            done_s += b - a
        if fs:
            self.faces = np.concatenate([self.faces, np.array(fs, np.float32)])
        if fsamp:
            self.fsamp = np.concatenate([self.fsamp, np.array(fsamp, np.float32)])
        self._distr = None
        return total

    def _sample(self, t, a, k, frame, gray, prev, was, fwas, faces, det, cell, k3, fs, fsamp):
        """One analysis sample at source time t (frame = BGR at AN_W, gray/prev = this/previous gray)."""
        import cv2
        bi = min(len(self.done) - 1, int(t // BIN))
        if prev is not None and not was[bi]:
            m = (cv2.absdiff(gray, prev) > DIFF_THR).astype(np.uint8)
            m = cv2.morphologyEx(m, cv2.MORPH_OPEN, k3)
            frac = float(m.mean())
            if frac > GLOBAL_FRAC:
                self.glob[bi] = min(255, int(self.glob[bi]) + 1)
            elif frac >= STILL_FRAC:
                g = cv2.resize(m.astype(np.float32), (GW, GH), interpolation=cv2.INTER_AREA) * cell
                g[g <= 2.0] = 0.0
                self.grid[bi] += g
                if frac < TINY_FRAC:
                    self.tiny[min(len(self.tiny) - 1, int(t // 10))] += (g > 0).astype(np.uint16)
        if t >= a:
            self.done[bi] = True
            if faces:
                self.fdone[bi] = True
        if det is not None and k % FACE_EVERY == 0 and t >= a - 1e-6 and not fwas[bi]:
            fsamp.append(t)
            try:
                _, found = det.detect(frame)
            except cv2.error:
                found = None
            for f in (found if found is not None else []):
                x, y, fw, fh, sc = float(f[0]), float(f[1]), float(f[2]), float(f[3]), float(f[-1])
                if fh / self.oh >= 0.03:
                    fs.append([t, (x + fw / 2) / self.ow, (y + fh / 2) / self.oh, fw / self.ow, fh / self.oh, sc])

    def _frames(self, a, b):
        """Yield (source time, BGR frame) at AN_FPS between a and b. ffmpeg is killed on early exit/cancel."""
        cmd = [ffmpeg_exe(), "-hide_banner", "-nostdin", "-loglevel", "error", "-skip_frame", "noref",
               "-ss", f"{a:.3f}", "-i", self.path, "-t", f"{max(0.05, b - a):.3f}", "-an", "-sn", "-dn",
               "-vf", f"fps={AN_FPS},scale={self.ow}:{self.oh}:flags=area", "-f", "rawvideo", "-pix_fmt", "bgr24", "-"]
        size = self.ow * self.oh * 3
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), bufsize=size * 4)
        try:
            k = 0
            while True:
                buf = proc.stdout.read(size)
                if len(buf) < size:
                    break
                yield a + k / AN_FPS, np.frombuffer(buf, np.uint8).reshape(self.oh, self.ow, 3)
                k += 1
        finally:
            if proc.poll() is None:
                proc.kill()
            proc.stdout.close()
            proc.wait()

    # ------------------------------------------------------------ queries
    def distractors(self):
        """Cells with tiny repeated changes all over the video (tab spinner, clock, taskbar): muted."""
        if self._distr is None:
            hit = self.tiny > 0
            cnt = self.tiny.sum(0)
            bins = hit.sum(0)
            nb = int(hit.any(axis=(1, 2)).sum())
            self._distr = (cnt >= 5) & (bins >= max(3, 0.06 * nb))
        return self._distr

    def _sum(self, s0, s1):
        b0, b1 = max(0, int(s0 // BIN)), min(len(self.grid), int(math.ceil(s1 / BIN)))
        return self.grid[b0:b1].sum(0) if b1 > b0 else np.zeros((GH, GW), np.float32)

    def focus(self, s0, s1, before=CTX_BEFORE, after=CTX_AFTER):
        """Where the action is during source [s0, s1]: weighted centre + 10..90 % box of local activity.
        The shot's own activity counts fully, the `before`/`after` seconds around it at CTX_WEIGHT (a shot with a
        notification toast only still lands on the field the user is working in), cells near the frame edge are
        damped (taskbar, toasts, tab strip) and repeated tiny changes are muted. None when too little happened.
        -> {"x", "y", "box": [x0, y0, x1, y1], "mass"} (normalized source coordinates)."""
        own = self._sum(s0, s1)
        ctx = self._sum(s0 - before, s1 + after) - own
        prior = _edge_prior()
        acc = (own + CTX_WEIGHT * np.maximum(ctx, 0.0)) * prior
        acc = np.where(self.distractors(), 0.0, acc)
        mass = float(acc.sum())
        # only edge activity (a toast, the taskbar clock) is no reason to move the crop: caller holds / centres
        if mass < MIN_MASS or float(acc[prior >= 0.999].sum()) < CORE_MIN * mass:
            return None
        acc = acc.ravel()
        idx = np.flatnonzero(acc)
        xs, ys, ws = (idx % GW + 0.5) / GW, (idx // GW + 0.5) / GH, acc[idx]

        def wpct(v, q):
            o = np.argsort(v)
            c = np.cumsum(ws[o]) / ws.sum()
            return float(v[o][min(len(o) - 1, int(np.searchsorted(c, q)))])
        box = [max(0.0, wpct(xs, 0.1) - 0.5 / GW), max(0.0, wpct(ys, 0.1) - 0.5 / GH),
               min(1.0, wpct(xs, 0.9) + 0.5 / GW), min(1.0, wpct(ys, 0.9) + 0.5 / GH)]
        return {"x": float((xs * ws).sum() / ws.sum()), "y": float((ys * ws).sum() / ws.sum()),
                "box": [round(v, 4) for v in box], "mass": round(mass, 1)}

    def face(self, s0, s1, strict=True):
        """Largest face that persists in [s0, s1] -> {"x", "y", "h", "share"} or None.
        strict (auto mode): big (>= 12 % of the height) and in >= 60 % of the samples, so the small faces in
        web-page ads of screen recordings never steer the crop (vision.md 1.8)."""
        if not len(self.fsamp):
            return None
        n = int(((self.fsamp >= s0) & (self.fsamp <= s1)).sum())
        if n == 0:
            return None
        f = self.faces[(self.faces[:, 0] >= s0) & (self.faces[:, 0] <= s1)] if len(self.faces) else self.faces
        if not len(f):
            return None
        best = {}
        for row in f:
            t = round(float(row[0]), 3)
            if t not in best or row[3] * row[4] > best[t][3] * best[t][4]:
                best[t] = row
        rows = np.array(list(best.values()))
        share = len(rows) / n
        h = float(np.median(rows[:, 4]))
        if share < (0.6 if strict else 0.3) or (strict and h < 0.12):
            return None
        return {"x": float(np.median(rows[:, 1])), "y": float(np.median(rows[:, 2])), "h": h, "share": round(share, 2)}


# ================================================================ timeline -> shots

def _usable(c):
    raw = c.raw or {}
    return c.dur > 1e-3 and not c.disabled and not raw.get("mgt") and (c.has_media or bool(raw.get("nested")))


def main_track(tl, want="auto"):
    """The camera track: `want` (0-based index) or the video track with the most media time."""
    if want not in (None, "", "auto"):
        trk = next((t for t in tl.video if t.index == int(want)), None)
        if trk is None:
            raise EngineError("NO_TRACK", tr("angles.noTrack", n=int(want) + 1), tr("angles.noTrackHint"))
        if not any(_usable(c) for c in trk.clips):
            raise EngineError("NO_VIDEO", tr("angles.trackEmpty", n=int(want) + 1), tr("angles.trackEmptyHint"))
        return trk
    cands = [t for t in tl.video if any(_usable(c) for c in t.clips)]
    if not cands:
        raise EngineError("NO_VIDEO", tr("angles.noVideo"), tr("angles.noVideoHint"))
    return max(cands, key=lambda t: (sum(c.dur for c in t.clips if _usable(c)), -t.index))


def _in_scope(t, scope):
    return any(a - 1e-6 <= t < b + 1e-6 for a, b in scope)


def sentence_starts(words, c0, c1, fps):
    """Candidate split times inside [c0, c1]: just before a word that starts a sentence/phrase.
    -> [(t, strength 0..1)] (frame-snapped)."""
    ws = [w for w in words if w["t0"] >= c0 - 1e-6 and w["t1"] <= c1 + 1e-6]
    out = []
    for prev, w in zip(ws, ws[1:]):
        gap = w["t0"] - prev["t1"]
        txt = prev["text"].strip()
        s = 0.0
        if txt[-1:] in ".?!":
            s = 1.0
        elif prev.get("seg"):
            s = 0.7
        elif txt[-1:] in ",;:":
            s = 0.4
        if gap >= 0.35:
            s = max(s, 0.5 + min(gap, 1.5) / 3.0)
        if s >= 0.4:
            t = w["t0"] - min(0.12, max(0.0, gap) / 2.0)
            out.append((round(round(t * fps) / fps, 6), round(s, 2)))
    return out


def split_points(c0, c1, cands, min_shot, every):
    """Pick split times so pieces are >= min_shot and close to `every` seconds, preferring strong sentence
    starts. Greedy left to right."""
    pts, last = [], c0
    cands = sorted(cands)
    while c1 - last >= 2 * min_shot and c1 - last > every * 1.25:
        lo, hi = last + min_shot, min(c1 - min_shot, last + 2 * every)
        opts = [(t, s) for t, s in cands if lo <= t <= hi]
        if not opts:
            later = [(t, s) for t, s in cands if hi < t <= c1 - min_shot]
            if not later:
                break
            opts = later[:1]
        aim = last + every
        t, _ = max(opts, key=lambda o: o[1] - 0.6 * abs(o[0] - aim) / every)
        pts.append(t)
        last = t
    return pts


def build_shots(tl, track, scope, words, p):
    """-> (shots, n_clips). Shot = {t0, t1, clip, cut_in, cut_out, in_scope, why} in sequence seconds.
    cut_in / cut_out = the edge is a NEW razor point (sentence split or scope edge), not a clip edge."""
    fps = tl.fps or 30.0
    clips = [c for c in track.clips if _usable(c)]
    shots = []
    for c in clips:
        edges = {round(c.start, 6): "clip", round(c.end, 6): "clip"}
        if p["split"]:
            for a, b in scope:
                for e in (a, b):
                    e = round(round(e * fps) / fps, 6)
                    if c.start + 0.25 < e < c.end - 0.25:
                        edges.setdefault(e, "scope")
        keys = sorted(edges)
        for a, b in zip(keys, keys[1:]):
            mid = (a + b) / 2
            if not _in_scope(mid, scope):
                shots.append({"t0": a, "t1": b, "clip": c, "cut_in": edges[a] != "clip", "cut_out": edges[b] != "clip",
                              "in_scope": False, "why": "scope"})
                continue
            pts = split_points(a, b, sentence_starts(words, a, b, fps), p["min_shot"], p["every"]) if p["split"] else []
            bounds = [a] + pts + [b]
            for k, (x, y) in enumerate(zip(bounds, bounds[1:])):
                shots.append({"t0": x, "t1": y, "clip": c, "in_scope": True,
                              "cut_in": (k > 0) or edges[a] != "clip", "cut_out": (k < len(pts)) or edges[b] != "clip",
                              "why": "sentence" if k > 0 else ("scope" if edges[a] != "clip" else "cut")})
    shots.sort(key=lambda s: s["t0"])
    return shots, len(clips)


# ================================================================ planner

def choose_angles(shots, p, spreads):
    """Assign an angle to every in-scope shot. Rules (product_cutting.md 5.2):
    - never the same angle twice in a row (across every cut, also under min shot in "cut" mode);
    - "rhythm" mode keeps the angle until the shot has lasted >= min_shot;
    - screen time follows the weights (largest deficit wins, ties -> wider);
    - an angle whose window cannot hold the action box is avoided when another allowed angle fits.
    spreads[i] = {angle: fits bool}. Writes s["angle"], s["held"], s["tight"]."""
    use, w = p["use"], p["weights"]
    tw = sum(w[a] for a in use) or 1.0
    share = {a: w[a] / tw for a in use}
    used = {a: 0.0 for a in use}
    total, prev, since, prev_end = 0.0, None, 0.0, None
    for i, s in enumerate(shots):
        if not s["in_scope"]:
            prev = "wide" if "wide" in use else None    # an untouched clip keeps its framing (usually 100 %)
            since, prev_end = 0.0, s["t1"]
            continue
        d = s["t1"] - s["t0"]
        if prev_end is not None and s["t0"] - prev_end > 0.5:
            since = 0.0                                  # gap on the track: the hold timer restarts
        s["held"] = s["tight"] = False
        if p["mode"] == "rhythm" and prev in use and since < p["min_shot"]:
            a = prev
            s["held"] = True
        else:
            allowed = [a for a in use if a != prev] or list(use)
            fit = [a for a in allowed if spreads[i].get(a, True)]
            pool = fit or allowed
            if not fit:
                s["tight"] = True
                pool = [min(allowed, key=lambda a: p["scales"][a])]   # least crop when nothing fits
            a = max(pool, key=lambda a: (share[a] * (total + d) - used[a], -p["scales"][a]))
            since = 0.0
        s["angle"] = a
        used[a] += d
        total += d
        since += d
        prev, prev_end = a, s["t1"]
    return shots


# ================================================================ actions

def _text(words, t0, t1, limit=110):
    txt = " ".join(w["text"].strip() for w in words if t0 - 0.05 <= (w["t0"] + w["t1"]) / 2 < t1 + 0.05).strip()
    return txt if len(txt) <= limit else txt[:limit - 1].rsplit(" ", 1)[0] + "..."


def _src_size(c, infos, seq_wh):
    """(W, H) of a clip's media (probe cached in `infos`); the sequence size for nests / missing media."""
    if c.has_media and Path(c.path).is_file():
        if c.path not in infos:
            infos[c.path] = M.probe(c.path)
        info = infos[c.path]
        return (info.get("width") or seq_wh[0], info.get("height") or seq_wh[1])
    return seq_wh


def _vision(shots, p, emit, infos):
    """Activity (+ faces) for the source ranges the shots use, cached per media. -> {path: Vision}."""
    spans = {}
    for s in shots:
        c = s["clip"]
        if c.has_media and Path(c.path).is_file():
            a, b = sorted((c.seq_to_src(s["t0"]), c.seq_to_src(s["t1"])))
            spans.setdefault(c.path, []).append([max(0.0, a - 4.0), b + 1.0])
    want_faces = p["anchor"] in ("auto", "face")
    tot = sum(R.total(R.norm(v)) for v in spans.values()) or 1.0
    done, out, analysed = 0.0, {}, 0.0
    for path, sp in spans.items():
        if path not in infos:
            infos[path] = M.probe(path)
        if not infos[path].get("has_video"):
            continue
        v = Vision(path, infos[path])
        v.load()
        sp = R.norm([[a, min(b, v.dur)] for a, b in sp])
        if v.missing(sp, want_faces):
            base, share = 100.0 * done / tot, 100.0 * R.total(sp) / tot
            analysed += v.analyse(sp, want_faces, lambda q, b=base, sh=share: emit.progress(b + sh * q / 100.0))
            if not v.save():
                emit.log("analysis cache not saved (disk?)")
        if want_faces and v.face_ok is False and p["anchor"] == "face":
            emit.warn(tr("angles.noFaceDetector"))
        out[path] = v
        done += R.total(sp)
    emit.done_note(tr("angles.analysed", dur=secs(round(analysed))) if analysed else tr("angles.fromCache"))
    return out


def _anchor(s, v, p, prev_anchor):
    """Crop centre of one shot: face (auto/face), else activity of the shot (or just before it), else the last
    action anchor of the same media (within 15 s), else the frame centre."""
    s0, s1 = s["s0"], s["s1"]
    if v is not None:
        if p["anchor"] in ("auto", "face"):
            f = v.face(s0, s1, strict=p["anchor"] == "auto")
            if f:   # headroom: keep the face a bit above the centre of the crop
                return {"x": f["x"], "y": min(1.0, f["y"] + 0.25 * f["h"]), "src": "face", "box": None}
        fo = v.focus(s0, s1)
        if fo:
            return {"x": fo["x"], "y": fo["y"], "src": "activity", "box": fo["box"]}
        old = prev_anchor.get(v.path)
        if p["anchor"] != "face" and old and abs(s0 - old[0]) < 15.0:
            return dict(old[1], src="hold", box=None)
    return {"x": 0.5, "y": 0.5, "src": "center", "box": None}


def _note(s, p):
    if s.get("tight"):
        return {"type": "warn", "text": tr("angles.noteTight")}
    if s.get("held"):
        return {"type": "info", "text": tr("angles.noteHeld", sec=_num(p["min_shot"]))}
    if s["why"] == "sentence":
        return {"type": "info", "text": tr("angles.noteSentence")}
    return None


def analyze(job, emit):
    p = _params(job.get("params"))
    tl = Timeline.from_json(job.get("seq"))
    need_vision = p["anchor"] != "center"
    emit.plan([("read", tr("angles.st.read"), 0.05),
               ("words", tr("angles.st.sentences") if p["split"] else tr("angles.st.words"), 0.3 if p["split"] else 0.05),
               ("vision", tr("angles.st.vision"), 0.55 if need_vision else 0.02), ("plan", tr("angles.st.plan"), 0.1)])
    with emit.step("read"):
        track = main_track(tl, p["track"])
        scope = tl.scope_ranges(p["scope"])
        seq_wh = (tl.width, tl.height)
        emit.progress(100)
    with emit.step("words"):
        try:   # sentence splits need the transcript (transcribes once, GPU lock); otherwise caches only (row text)
            words = tl.words_on_timeline(transcribe=p["split"], emit=emit if p["split"] else None)
        except EngineError as e:
            if p["split"]:
                raise
            emit.log(f"transcript not used: {e.msg}")
            words = []
        if p["split"] and not words:
            emit.warn(tr("angles.emptyTranscript"))
        emit.done_note(tr("angles.words", n=len(words)) if words else tr("angles.noTranscript"))
        emit.progress(100)
    shots, n_clips = build_shots(tl, track, scope, words, p)
    inside = [s for s in shots if s["in_scope"]]
    if not inside:
        raise EngineError("NO_SHOTS", tr("angles.noShots"), tr("angles.noShotsHint"))
    infos = {}
    with emit.step("vision"):
        visions = _vision(inside, p, emit, infos) if need_vision else {}
        emit.progress(100)

    with emit.step("plan"):
        prev_anchor, spreads = {}, []
        for s in shots:
            spreads.append({})
            if not s["in_scope"]:
                continue
            c = s["clip"]
            s["src_wh"] = _src_size(c, infos, seq_wh)
            s["base"] = base_scale(s["src_wh"], seq_wh)
            s["s0"], s["s1"] = sorted((c.seq_to_src(s["t0"]), c.seq_to_src(s["t1"])))
            anc = s["anchor"] = _anchor(s, visions.get(c.path), p, prev_anchor)
            if anc["src"] in ("activity", "face"):
                prev_anchor[c.path] = (s["s1"], {"x": anc["x"], "y": anc["y"]})
            s["opts"] = {}
            for a in p["use"]:
                o = place(anc["x"], anc["y"], p["scales"][a], s["base"], s["src_wh"], seq_wh)
                o["fits"] = spreads[-1][a] = fits(anc.get("box"), o["view"])
                s["opts"][a] = o
        choose_angles(shots, p, spreads)
        items = []
        for s in inside:
            a, anc, c = s["angle"], s["anchor"], s["clip"]
            items.append(RV.item(
                s["t0"], s["t1"], "shot", on=True, label=f"{NAMES[a]} {_num(p['scales'][a])}%",
                ctx=_text(words, s["t0"], s["t1"]), note=_note(s, p),
                angle=a, scale=s["opts"][a]["scale"], pos=s["opts"][a]["pos"], base=s["base"],
                anchor={"x": round(anc["x"], 4), "y": round(anc["y"], 4), "src": anc["src"], "box": anc.get("box")},
                opts=s["opts"], cut_in=bool(s["cut_in"]), cut_out=bool(s["cut_out"]), why=s["why"],
                track=track.index, clip=c.index, media=c.path if c.has_media else None,
                sm=round((s["s0"] + s["s1"]) / 2, 3), src_wh=list(s["src_wh"])))
        emit.progress(100)

    odd = sorted({Path(it["media"]).name for it in items if it["media"] and abs(it["base"] - 100.0) > 0.01})
    if odd:   # Premiere "Scale to Frame Size" would make the fill math wrong: say what we assume
        emit.warn(tr("angles.warnSize", files=", ".join(odd[:2])))
    stats = _stats(items, p, n_clips, track.index)
    path = RV.path_for(workdir(job, tl.name), "angles")
    doc = RV.new("angles", items, tl.duration, seq=tl, params=p, stats=stats, track=track.index, fps=tl.fps,
                 size=[tl.width, tl.height], angles={a: {"name": NAMES[a], "scale": p["scales"][a]} for a in p["use"]})
    carried = carry_over(doc, read_json(path))
    RV.save(doc, path)
    if n_clips < 3 and not p["split"]:
        emit.warn(tr("angles.warnFewClips", n=n_clips))
    return {"review": str(path), "summary": summary_text(doc), "stats": stats, "carried": carried,
            "track": track.index, "duration": tl.duration}


def _stats(items, p, n_clips, track):
    anchors = {}
    for it in items:
        anchors[it["anchor"]["src"]] = anchors.get(it["anchor"]["src"], 0) + 1
    return {"n": len(items), "on": len(items), "sec_on": round(sum(it["t1"] - it["t0"] for it in items), 3),
            "clips": n_clips, "splits": sum(1 for it in items if it["why"] == "sentence"),
            "by_angle": {a: sum(1 for it in items if it["angle"] == a) for a in p["use"]},
            "sec_by_angle": {a: round(sum(it["t1"] - it["t0"] for it in items if it["angle"] == a), 2)
                             for a in p["use"]},
            "anchors": anchors, "track": track,
            "repeats": sum(1 for x, y in zip(items, items[1:])
                           if x["angle"] == y["angle"] and abs(x["t1"] - y["t0"]) < 1e-3)}


def carry_over(doc, old):
    """Keep the user's manual choices from the previous review of the same sequence: `on` of touched rows and
    the angle of rows where the user picked one (`picked: true`), matched by stable id."""
    if not old or old.get("tool") != "angles":
        return 0
    prev = {it.get("id"): it for it in old.get("items", []) if it.get("touched") or it.get("picked")}
    n = 0
    for it in doc["items"]:
        o = prev.get(it["id"])
        if not o:
            continue
        if o.get("touched"):
            it["on"], it["touched"] = bool(o.get("on")), True
        if o.get("picked") and o.get("angle") in it["opts"]:
            _set_angle(it, o["angle"], doc)
            it["picked"] = True
        n += 1
    return n


def _set_angle(it, a, doc):
    o = it["opts"][a]
    it["angle"], it["scale"], it["pos"] = a, o["scale"], o["pos"]
    sc = (doc.get("angles") or {}).get(a, {}).get("scale")
    it["label"] = f"{NAMES[a]} {_num(sc)}%" if sc else NAMES[a]


def summary_text(doc):
    items = [it for it in doc.get("items", []) if it.get("on")]
    parts = []
    for a in ANGLES:
        n = sum(1 for it in items if it.get("angle") == a)
        if n:
            parts.append(f"{n} {NAMES[a]}")
    splits = sum(1 for it in items if it.get("why") == "sentence")
    return tr("angles.summary", n=len(items), parts=", ".join(parts)) + (tr("angles.summarySplits", n=splits) if splits else "")


def apply(job, emit):
    """Edited review -> static Motion plan for the host (clone, razor at `splits`, set Scale/Position)."""
    with emit.step("plan", tr("angles.st.prepare")):
        doc = RV.from_job(job)
        if doc.get("tool") != "angles":
            raise EngineError("BAD_REVIEW", tr("angles.badReview"), tr("angles.rerun"))
        items = doc["items"]
        out, splits = [], set()
        for k, it in enumerate(items):
            opts = it.get("opts") or {}
            a = it.get("angle")
            if a not in opts:
                raise EngineError("BAD_REVIEW", tr("angles.badAngle", angle=a, row=k + 1), tr("angles.rerun"))
            if not it.get("on"):
                continue
            o = opts[a]
            out.append({"id": it["id"], "t0": it["t0"], "t1": it["t1"], "angle": a, "scale": o["scale"],
                        "pos": o["pos"]})
            if it.get("cut_in"):
                splits.add(round(float(it["t0"]), 6))
            if it.get("cut_out"):
                splits.add(round(float(it["t1"]), 6))
        if not out:
            raise EngineError("NOTHING_ON", tr("angles.nothingOn"), tr("angles.nothingOnHint"))
        emit.progress(100)
    by = {a: sum(1 for x in out if x["angle"] == a) for a in ANGLES}
    plan = {"kind": "keyframes", "mode": "static", "tool": "angles", "track": int(doc.get("track", 0)),
            "fps": doc.get("fps"), "seq": doc.get("seq"), "splits": sorted(splits), "items": out}
    return {"plan": plan, "summary": summary_text(doc), "by_angle": by, "splits": len(splits)}


def frame(job, emit):
    """Preview frame: params {media, t (source s), width (480)} -> {"path": jpg, "w", "h"}. Cached in the
    workdir (angles_frames, newest 80 kept)."""
    p = job.get("params") or {}
    media, t = p.get("media"), float(p.get("t", 0.0))
    width = int(min(1280, max(160, int(p.get("width", 480)))))
    if not media or not Path(media).is_file():
        raise EngineError("NO_MEDIA", tr("angles.noMedia"), str(media or ""))
    d = Path(p.get("dir") or workdir(job)) / "angles_frames"
    out = d / f"{file_hash(media)}_{int(round(t * 1000))}_{width}.jpg"
    if not out.is_file():
        d.mkdir(parents=True, exist_ok=True)
        ensure_free(4 * 1024 ** 2, d)
        M.frame_grab(media, t, out=out, width=width)
        olds = sorted(d.glob("*.jpg"), key=lambda f: f.stat().st_mtime, reverse=True)[80:]
        for f in olds:
            try:
                f.unlink()
            except OSError:
                pass
    info = M.probe(media)
    h = int(round(info["height"] * width / max(1, info["width"]) / 2)) * 2
    return {"path": str(out), "w": width, "h": h, "t": t}


ACTIONS = {"analyze": analyze, "apply": apply, "frame": frame}
