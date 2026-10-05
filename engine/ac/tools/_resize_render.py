"""Pixels for Auto Resize (helper of ac.tools.resize): layout geometry, one-frame compose (crop / focus + blur),
the preview contact sheet PNG and the MP4 render (decoded with ffmpeg, composed with OpenCV, encoded with NVENC,
audio mixed from the sequence's heard audio clips).

Layouts (product_visual.md 4.2):
  crop   full-height window of the target aspect following the action (what Premiere keyframes can reproduce);
  focus  blurred, darkened full frame as background + a sharp window following the action (1:1 for 9:16, 4:3 for
         4:5, 16:10 for 1:1, the whole frame for 16:9), title zone above, caption zone below. A 9:16 crop of the
         2292x960 tutorials is only 23.6 % of the width and cuts forms in half; the focus window shows 42 %.
"""
from __future__ import annotations

import bisect
import os
import subprocess
import threading
import time
from pathlib import Path

import numpy as np

from .. import media as M
from ..i18n import tr
from ..util import EngineError, ensure_free, ffmpeg_exe, run_ffmpeg
from . import _resize_camera as CAM
from . import _resize_vision as V

TARGETS = {
    "9x16": {"size": (1080, 1920), "label": "9:16", "ratio": (9, 16)},
    "1x1": {"size": (1080, 1080), "label": "1:1", "ratio": (1, 1)},
    "4x5": {"size": (1080, 1350), "label": "4:5", "ratio": (4, 5)},
    "16x9": {"size": (1920, 1080), "label": "16:9", "ratio": (16, 9)},
}
FOCUS_ASPECT = {"9x16": 1.0, "4x5": 4 / 3, "1x1": 1.6, "16x9": None}     # None = the whole source frame
ACCENT = (62, 132, 247)                 # ember #f7843e in BGR
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def geometry(target, layout, src_w, src_h, zoom=1.0):
    """-> {layout, out: [W, H], cw, ch (window size as a fraction of the source), win: [x, y, w, h] in output px,
    scale (crop: Premiere Motion scale factor, 1 = native pixels)}."""
    wo, ho = TARGETS[target]["size"]
    zoom = max(1.0, float(zoom or 1.0))
    if layout == "focus":
        src_a = src_w / src_h
        a = min(FOCUS_ASPECT[target] or src_a, src_a)
        dw, dh = wo, int(round(wo / a))
        if dh > ho:
            dw, dh = int(round(ho * a)), ho
        top = int(round((ho - dh) * (0.5 if target == "16x9" else 0.45)))
        cw = min(1.0, a * src_h / (zoom * src_w))
        return {"layout": "focus", "out": [wo, ho], "cw": cw, "ch": 1.0 / zoom, "win": [(wo - dw) // 2, top, dw, dh],
                "scale": dw / (cw * src_w)}
    s = max(wo / src_w, ho / src_h) * zoom
    return {"layout": "crop", "out": [wo, ho], "cw": min(1.0, wo / (src_w * s)), "ch": min(1.0, ho / (src_h * s)),
            "win": [0, 0, wo, ho], "scale": s}


# ---------------------------------------------------------------- one frame

def _bg(cv2, img, wo, ho, mode):
    if mode == "dark":
        return np.full((ho, wo, 3), 18, np.uint8)
    h, w = img.shape[:2]
    small = cv2.resize(img, (max(8, w // 8), max(8, h // 8)), interpolation=cv2.INTER_AREA)
    small = cv2.GaussianBlur(small, (0, 0), 5)
    sh, sw = small.shape[:2]
    k = max(wo / sw, ho / sh)                                  # cover the output
    cw_, ch_ = wo / k, ho / k
    x0, y0 = (sw - cw_) / 2, (sh - ch_) / 2
    m = np.float32([[k, 0, -x0 * k], [0, k, -y0 * k]])
    bg = cv2.warpAffine(small, m, (wo, ho), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
    return cv2.convertScaleAbs(bg, alpha=0.42)


def compose(img, geom, cx, cy, bg="blur"):
    """Output frame (H, W, 3 uint8) for a source frame (BGR, any size) with the window centred at (cx, cy)
    (normalised source coords, clamped so the window stays inside the frame). Sub-pixel accurate (warpAffine), so
    slow pans do not step."""
    cv2 = V._cv2()
    h, w = img.shape[:2]
    wo, ho = geom["out"]
    ww, wh = geom["cw"] * w, geom["ch"] * h
    x0 = min(max(cx * w - ww / 2, 0.0), w - ww)
    y0 = min(max(cy * h - wh / 2, 0.0), h - wh)
    dx, dy, dw, dh = geom["win"]
    s = dw / ww
    flags = cv2.INTER_CUBIC if s > 1.4 else (cv2.INTER_AREA if s < 0.8 else cv2.INTER_LINEAR)
    m = np.float32([[s, 0, -x0 * s], [0, dh / wh, -y0 * dh / wh]])
    if geom["layout"] == "crop":
        if flags == cv2.INTER_AREA:
            return cv2.resize(img[int(y0):int(y0 + wh), int(x0):int(x0 + ww)], (wo, ho), interpolation=flags)
        return cv2.warpAffine(img, m, (wo, ho), flags=flags, borderMode=cv2.BORDER_REPLICATE)
    out = _bg(cv2, img, wo, ho, bg)
    if flags == cv2.INTER_AREA:
        win = cv2.resize(img[int(y0):int(y0 + wh), int(x0):int(x0 + ww)], (dw, dh), interpolation=flags)
    else:
        win = cv2.warpAffine(img, m, (dw, dh), flags=flags, borderMode=cv2.BORDER_REPLICATE)
    out[dy:dy + dh, dx:dx + dw] = win
    if dh < ho:                                                # thin shadow lines so the window reads as a card
        if dy >= 2:
            out[dy - 2:dy] = (out[dy - 2:dy] * 0.5).astype(np.uint8)
        if dy + dh + 2 <= ho:
            out[dy + dh:dy + dh + 2] = (out[dy + dh:dy + dh + 2] * 0.5).astype(np.uint8)
    return out


class CamPath:
    """Fast camera lookup over shots (sequence time) -> (cx, cy)."""

    def __init__(self, shots):
        self.shots = shots
        self.starts = [s["t0"] for s in shots]

    def at(self, t):
        if not self.shots:
            return 0.5, 0.5
        i = max(0, bisect.bisect_right(self.starts, t + 1e-9) - 1)
        sh = self.shots[i]
        return CAM.value_at(sh["kx"], t), CAM.value_at(sh["ky"], t)

    def arrays(self, times):
        cx = np.full(len(times), 0.5)
        cy = np.full(len(times), 0.5)
        if not self.shots:
            return cx, cy
        idx = np.clip(np.searchsorted(self.starts, np.asarray(times) + 1e-9, side="right") - 1, 0, len(self.shots) - 1)
        for i in np.unique(idx):
            sel = idx == i
            sh = self.shots[int(i)]
            kx, ky = np.asarray(sh["kx"], float), np.asarray(sh["ky"], float)
            cx[sel] = np.interp(times[sel], kx[:, 0], kx[:, 1])
            cy[sel] = np.interp(times[sel], ky[:, 0], ky[:, 1])
        return cx, cy


def clip_at(clips, starts, t):
    i = bisect.bisect_right(starts, t + 1e-9) - 1
    if 0 <= i < len(clips) and clips[i].start - 1e-9 <= t < clips[i].end - 1e-9:
        return clips[i]
    return None


# ---------------------------------------------------------------- contact sheet

def _fmt_t(t):
    t = max(0.0, t)
    m, s = int(t // 60), t - 60 * int(t // 60)
    return f"{m}:{s:04.1f}".replace(".", ",")


def sheet_times(items, t0, t1, k):
    """Tile times: the middle of evenly picked camera events, topped up with evenly spaced times."""
    dur = max(1e-3, t1 - t0)
    mids = [((it["t0"] + min(it["t1"], it["t0"] + 3.0)) / 2) for it in items]
    if len(mids) >= k:
        pick = [mids[int(round(i))] for i in np.linspace(0, len(mids) - 1, k)]
    else:
        pick = list(mids)
        for x in np.linspace(t0 + dur * 0.06, t1 - dur * 0.06, k + 2):
            if len(pick) >= k:
                break
            if all(abs(x - p) > dur * 0.04 for p in pick):
                pick.append(float(x))
    return sorted(min(max(t0, p), t1 - 1e-3) for p in pick)[:k]


def contact_sheet(out, clips, shots, geoms, target, t0, t1, items, bg="blur", width=960, prog=None):
    """Preview PNG: camera path strip (crop band over the source width) + tiles of the real output layout with a
    mini source map (crop box) and the time. Returns {path, times, w, h}."""
    cv2 = V._cv2()
    wo, ho = TARGETS[target]["size"]
    cols = 4 if ho / wo > 1.1 else (3 if ho / wo > 0.9 else 2)
    k = cols * 2
    gap, strip_h, lab_h = 10, 112, 22
    tile_w = (width - gap * (cols + 1)) // cols
    tile_h = int(round(tile_w * ho / wo))
    ref = geoms[clips[0].path] if clips else None
    sw_, sh_ = ref["src"] if ref else (16, 9)
    mini_h = int(round(tile_w * sh_ / sw_))
    times = sheet_times(items, t0, t1, k)
    rows = (len(times) + cols - 1) // cols or 1
    H = gap + strip_h + gap + rows * (tile_h + mini_h + lab_h + gap)
    img = np.full((H, width, 3), (30, 28, 28), np.uint8)
    cam = CamPath(shots)
    starts = [c.start for c in clips]
    # ---- path strip: band = window over the source width, line = centre, red ticks = camera cuts
    x_of = lambda t: int(round(gap + (width - 2 * gap - 1) * (t - t0) / max(1e-6, t1 - t0)))  # noqa: E731
    y0s = gap
    cv2.rectangle(img, (gap, y0s), (width - gap - 1, y0s + strip_h - 1), (48, 44, 44), -1)
    ts = np.linspace(t0, t1, width - 2 * gap)
    cx, _cy = cam.arrays(ts)
    cwf = ref["cw"] if ref else 1.0
    band = img.copy()
    for i, (t, c) in enumerate(zip(ts, cx)):
        a, b = c - cwf / 2, c + cwf / 2
        ya, yb = int(y0s + a * (strip_h - 1)), int(y0s + b * (strip_h - 1))
        cv2.line(band, (gap + i, ya), (gap + i, yb), ACCENT, 1)
    img = cv2.addWeighted(band, 0.35, img, 0.65, 0)
    pts = np.array([[gap + i, int(y0s + c * (strip_h - 1))] for i, c in enumerate(cx)], np.int32)
    cv2.polylines(img, [pts], False, ACCENT, 2, cv2.LINE_AA)
    for sh in shots[1:]:
        if sh.get("reason") in ("page", "speaker") or sh.get("jump"):
            x = x_of(sh["t0"])
            cv2.line(img, (x, y0s), (x, y0s + 8), (90, 90, 230), 1)
    for n, t in enumerate(times):
        x = x_of(t)
        cv2.line(img, (x, y0s + strip_h - 10), (x, y0s + strip_h - 1), (230, 230, 230), 1)
        cv2.putText(img, str(n + 1), (min(width - gap - 12, x + 3), y0s + strip_h - 3), cv2.FONT_HERSHEY_SIMPLEX, 0.38,
                    (230, 230, 230), 1, cv2.LINE_AA)
    # ---- tiles
    for n, t in enumerate(times):
        r, c_ = divmod(n, cols)
        x = gap + c_ * (tile_w + gap)
        y = gap + strip_h + gap + r * (tile_h + mini_h + lab_h + gap)
        clip = clip_at(clips, starts, t)
        src = None
        if clip is not None:
            src = V.grab(clip.path, clip.seq_to_src(t))
        if src is None:
            cv2.rectangle(img, (x, y), (x + tile_w - 1, y + tile_h - 1), (0, 0, 0), -1)
        else:
            g = geoms[clip.path]
            ccx, ccy = cam.at(t)
            tile = cv2.resize(compose(src, g, ccx, ccy, bg), (tile_w, tile_h), interpolation=cv2.INTER_AREA)
            img[y:y + tile_h, x:x + tile_w] = tile
            mini = cv2.resize(src, (tile_w, mini_h), interpolation=cv2.INTER_AREA)
            mw, mh = g["cw"] * tile_w, g["ch"] * mini_h
            bx = min(max(ccx * tile_w - mw / 2, 0), tile_w - mw)
            by = min(max(ccy * mini_h - mh / 2, 0), mini_h - mh)
            mini = (mini * 0.55).astype(np.uint8)
            x0i, y0i, x1i, y1i = int(bx), int(by), int(round(bx + mw)), int(round(by + mh))
            mini[y0i:y1i, x0i:x1i] = cv2.resize(src, (tile_w, mini_h), interpolation=cv2.INTER_AREA)[y0i:y1i, x0i:x1i]
            cv2.rectangle(mini, (x0i, y0i), (max(x0i, x1i - 1), max(y0i, y1i - 1)), ACCENT, 2)
            img[y + tile_h:y + tile_h + mini_h, x:x + tile_w] = mini
        cv2.putText(img, f"{n + 1}  {_fmt_t(t)}", (x + 2, y + tile_h + mini_h + 16), cv2.FONT_HERSHEY_SIMPLEX, 0.45,
                    (225, 225, 225), 1, cv2.LINE_AA)
        if prog:
            prog(100.0 * (n + 1) / max(1, len(times)))
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    ok, buf = cv2.imencode(".png", img, [cv2.IMWRITE_PNG_COMPRESSION, 6])
    if not ok:
        raise EngineError("PREVIEW", tr("resize.previewFail"))
    tmp = Path(str(out) + ".tmp")
    tmp.write_bytes(buf.tobytes())
    os.replace(tmp, out)
    return {"path": str(out), "times": [round(t, 3) for t in times], "w": width, "h": H}


# ---------------------------------------------------------------- render: encoder

_NVENC = {}


def nvenc_ok():
    """True when h264_nvenc can encode here (tiny test encode, cached per process)."""
    if "ok" not in _NVENC:
        try:
            r = run_ffmpeg(["-f", "lavfi", "-i", "color=c=black:s=256x256:d=0.1:r=30", "-c:v", "h264_nvenc",
                            "-f", "null", "-"], check=False, timeout=30)
            _NVENC["ok"] = r.returncode == 0
        except (EngineError, subprocess.TimeoutExpired):
            _NVENC["ok"] = False
    return _NVENC["ok"]


def _video_args(fps, maxrate_m=10):
    g = str(int(round(fps * 2)))
    if nvenc_ok():
        return ["-c:v", "h264_nvenc", "-preset", "p5", "-rc", "vbr", "-cq", "21", "-b:v", "0",
                "-maxrate", f"{maxrate_m}M", "-bufsize", f"{2 * maxrate_m}M", "-g", g, "-profile:v", "high"], "h264_nvenc"
    return ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-maxrate", f"{maxrate_m}M",
            "-bufsize", f"{2 * maxrate_m}M", "-g", g], "libx264"


def estimate_bytes(dur, maxrate_m=10):
    """Conservative output size: screen content averages well below maxrate, assume 60 % of it + AAC 160 kbps."""
    return int(dur * (maxrate_m * 1e6 * 0.6 + 160e3) / 8)


# ---------------------------------------------------------------- render: audio

def _pcm(path, s0, dur, sr=48000):
    """Stereo float32 PCM of [s0, s0+dur) source seconds (exact length)."""
    n = int(round(dur * sr))
    if n <= 0:
        return np.zeros((0, 2), np.float32)
    r = run_ffmpeg(["-ss", f"{max(0.0, s0):.5f}", "-i", str(path), "-t", f"{dur + 0.05:.5f}", "-vn", "-sn",
                    "-ac", "2", "-ar", str(sr), "-f", "s16le", "-"], check=False)
    a = np.frombuffer(r.stdout or b"", np.int16)
    a = a[:len(a) - len(a) % 2].reshape(-1, 2).astype(np.float32)
    if len(a) >= n:
        return a[:n]
    return np.vstack([a, np.zeros((n - len(a), 2), np.float32)])


def heard_clips(tl, t0, t1):
    """Audio clips that are heard in [t0, t1) (unmuted tracks, enabled, with media), stereo twins removed."""
    out, seen = [], set()
    for c in tl.audio_clips():
        if c.end <= t0 or c.start >= t1 or not Path(c.path).is_file():
            continue
        info = M.probe(c.path)
        if not info["has_audio"]:
            continue
        key = (c.path.lower(), round(c.start, 3), round(c.src_in, 3))
        if key in seen:
            continue
        seen.add(key)
        out.append(c)
    return sorted(out, key=lambda c: (c.start, c.track))


def build_audio(tl, t0, t1, out, prog=None, sr=48000, window=30.0):
    """Mix of every heard audio clip in [t0, t1) into an AAC .m4a (exact length t1 - t0). Clip volume keyframes and
    audio effects are not applied (the Premiere mix stays the reference). -> path or None when nothing is heard.
    Works in 30 s windows (constant memory); neighbouring clips of the same file are decoded in one ffmpeg call."""
    clips = heard_clips(tl, t0, t1)
    if not clips:
        return None
    enc = subprocess.Popen([ffmpeg_exe(), "-hide_banner", "-nostdin", "-loglevel", "error", "-y", "-f", "s16le",
                            "-ar", str(sr), "-ac", "2", "-i", "-", "-c:a", "aac", "-b:a", "160k", str(out)],
                           stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                           creationflags=_NO_WINDOW)
    err = []
    th = threading.Thread(target=lambda: err.append(enc.stderr.read()), daemon=True)
    th.start()
    total = int(round((t1 - t0) * sr))
    written = 0
    try:
        w0 = t0
        while w0 < t1 - 1e-9:
            w1 = min(t1, w0 + window)
            n = int(round((w1 - t0) * sr)) - written
            buf = np.zeros((n, 2), np.float32)
            parts = [(c, max(c.start, w0), min(c.end, w1)) for c in clips if c.start < w1 and c.end > w0]
            # group neighbours: same file, speed 1, source continuing within 2 s -> one decode
            groups = []
            for c, a, b in parts:
                s0 = c.seq_to_src(a)
                g = groups[-1] if groups else None
                if g and g["path"] == c.path and c.speed == 1 and g["speed"] == 1 and 0 <= s0 - g["s1"] <= 2.0:
                    g["items"].append((c, a, b))
                    g["s1"] = c.seq_to_src(b)
                else:
                    groups.append({"path": c.path, "speed": c.speed, "s0": s0, "s1": c.seq_to_src(b), "items": [(c, a, b)]})
            for g in groups:
                if g["speed"] != 1:
                    for c, a, b in g["items"]:
                        src = _pcm(c.path, c.seq_to_src(a), (b - a) * c.speed, sr)
                        m = int(round((b - a) * sr))
                        if len(src) and m:
                            xi = np.linspace(0, len(src) - 1, m)
                            src = np.stack([np.interp(xi, np.arange(len(src)), src[:, ch]) for ch in (0, 1)], 1)
                        _add(buf, src, int(round((a - t0) * sr)) - written)
                    continue
                pcm = _pcm(g["path"], g["s0"], g["s1"] - g["s0"], sr)
                for c, a, b in g["items"]:
                    o = int(round((c.seq_to_src(a) - g["s0"]) * sr))
                    m = int(round((b - a) * sr))
                    _add(buf, pcm[o:o + m], int(round((a - t0) * sr)) - written)
            enc.stdin.write(np.clip(buf, -32768, 32767).astype("<i2").tobytes())
            written += n
            w0 = w1
            if prog:
                prog(100.0 * written / max(1, total))
        enc.stdin.close()
        enc.wait(timeout=120)
    except BaseException:
        enc.kill()
        enc.wait()
        try:
            os.unlink(out)
        except OSError:
            pass
        raise
    th.join(timeout=5)
    if enc.returncode != 0:
        msg = (err[0] if err else b"").decode("utf-8", "replace").strip().splitlines()
        raise EngineError("FFMPEG", tr("resize.audioFail", msg=msg[-1][:200] if msg else f"exit {enc.returncode}"))
    return str(out)


def _add(buf, src, off):
    if off < 0:
        src, off = src[-off:], 0
    m = min(len(src), len(buf) - off)
    if m > 0:
        buf[off:off + m] += src[:m]


# ---------------------------------------------------------------- render: video

class _Reader:
    """Background decoder thread (cv2 and pipe reads release the GIL, so decode overlaps compose + encode)."""

    def __init__(self, gen, depth=6):
        import queue
        self._empty = queue.Empty
        self.q = queue.Queue(maxsize=depth)
        self.gen = gen
        self.stop = False
        self.err = None
        self.th = threading.Thread(target=self._run, daemon=True)
        self.th.start()

    def _run(self):
        try:
            for item in self.gen:
                if self.stop:
                    break
                self.q.put(item)
        except BaseException as e:  # noqa: BLE001 - re-raised in the consumer
            self.err = e
        finally:
            try:
                self.gen.close()               # same thread that runs the generator: kills its ffmpeg
            except Exception:  # noqa: BLE001
                pass
            self.q.put(None)

    def next(self):
        item = self.q.get()
        if item is None:
            self.q.put(None)                   # keep the end marker for later calls
            if self.err is not None:
                raise self.err
            raise StopIteration
        return item

    def close(self):
        self.stop = True
        while self.th.is_alive():              # drain so a blocked put() returns and the thread can exit
            try:
                self.q.get(timeout=0.1)
            except self._empty:
                pass


def render_video(tl, clips, shots, geoms, out, t0, t1, fps=30, bg="blur", audio=None, prog=None, maxrate_m=10):
    """Render [t0, t1) of the main video track with the camera path into an H.264 MP4 (+ audio file if given).
    -> {path, frames, secs, fps, codec, w, h}. Deletes the partial file on any error / cancel."""
    cv2 = V._cv2()
    geom0 = geoms[clips[0].path]
    wo, ho = geom0["out"]
    n = max(1, int(round((t1 - t0) * fps)))
    times = t0 + np.arange(n) / fps + 1e-6
    cam = CamPath(shots)
    cx, cy = cam.arrays(times)
    starts = [c.start for c in clips]
    plan = []                                 # [(frame k, clip or None, source time)]
    for k, t in enumerate(times):
        c = clip_at(clips, starts, t)
        plan.append((k, c, c.seq_to_src(t) if c is not None else None))
    runs, cur = [], None
    for k, c, s in plan:
        if c is None:
            cur = None
            runs.append({"clip": None, "frames": [(k, None)]})
            continue
        ok = (cur is not None and cur["clip"] is not None and cur["path"] == c.path and cur["speed"] == c.speed
              and s >= cur["last"] - 1e-6 and s - cur["last"] <= 3.0)
        if not ok:
            cur = {"clip": c, "path": c.path, "speed": c.speed, "s0": s, "last": s, "frames": []}
            runs.append(cur)
        cur["frames"].append((k, s))
        cur["last"] = s
    vargs, codec = _video_args(fps, maxrate_m)
    cmd = [ffmpeg_exe(), "-hide_banner", "-nostdin", "-loglevel", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24",
           "-s", f"{wo}x{ho}", "-r", str(fps), "-i", "-"]
    if audio:
        cmd += ["-i", str(audio)]
    cmd += ["-map", "0:v:0"] + (["-map", "1:a:0", "-c:a", "copy"] if audio else [])
    # setparams: ffmpeg 8 takes primaries/trc from the frames (unknown for rawvideo) over -color_primaries/-color_trc
    vf = "scale=out_color_matrix=bt709:out_range=tv,format=yuv420p,setparams=color_primaries=bt709:color_trc=bt709:colorspace=bt709:range=tv"
    cmd += vargs + ["-vf", vf, "-colorspace", "bt709",
                    "-color_primaries", "bt709", "-color_trc", "bt709", "-movflags", "+faststart", str(out)]
    enc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                           creationflags=_NO_WINDOW)
    err = []
    th = threading.Thread(target=lambda: err.append(enc.stderr.read()), daemon=True)
    th.start()
    black = np.zeros((ho, wo, 3), np.uint8)
    t_start = time.perf_counter()
    done = 0
    reader = None
    try:
        for run in runs:
            if run["clip"] is None:
                for k, _ in run["frames"]:
                    enc.stdin.write(black.tobytes())
                    done += 1
                continue
            info = M.probe(run["path"])
            g = geoms[run["path"]]
            # decode size: native, or smaller when the window would be downscaled anyway (4K sources)
            f = min(1.0, g["win"][3] / max(1.0, g["ch"] * info["height"]) * 1.15)
            dw, dh = V.out_size(info["width"], info["height"], int(info["width"] * f) if f < 1 else None)
            fdec = fps * max(1.0, run["speed"])
            span = run["last"] - run["s0"] + 3.0 / fdec
            reader = _Reader(V.frames(run["path"], fdec, start=run["s0"], duration=span, size=(dw, dh)))
            j_cur, img = -1, None
            for k, s in run["frames"]:
                j = int(round((s - run["s0"]) * fdec))
                while j_cur < j:
                    try:
                        _, img = reader.next()
                    except StopIteration:
                        break
                    j_cur += 1
                frame = compose(img, g, cx[k], cy[k], bg) if img is not None else black
                enc.stdin.write(np.ascontiguousarray(frame).tobytes())
                done += 1
                if prog and done % 10 == 0:
                    prog(100.0 * done / n)
            reader.close()
            reader = None
        enc.stdin.close()
        enc.wait(timeout=600)
    except BaseException:
        if reader is not None:
            reader.close()
        enc.kill()
        enc.wait()
        try:
            os.unlink(out)
        except OSError:
            pass
        raise
    th.join(timeout=5)
    if enc.returncode != 0 or not Path(out).is_file():
        msg = (err[0] if err else b"").decode("utf-8", "replace").strip().splitlines()
        try:
            os.unlink(out)
        except OSError:
            pass
        raise EngineError("FFMPEG", tr("resize.renderFail", msg=msg[-1][:240] if msg else f"exit {enc.returncode}"),
                          "\n".join(msg[-5:])[:800])
    if prog:
        prog(100)
    return {"path": str(out), "frames": done, "secs": round(time.perf_counter() - t_start, 2), "fps": fps,
            "codec": codec, "w": wo, "h": ho}


def free_check(dur, folder, maxrate_m=10):
    """DISK_FULL error unless the render (estimated) + 500 MB margin fits on the output drive."""
    need = estimate_bytes(dur, maxrate_m) + int(dur * 48000 * 4 * 0.02)
    ensure_free(need, folder)
    return need
