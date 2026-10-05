"""Fast previews: caption PNG over the real frame at a sequence time, transparent overlay PNG, template gallery
thumbnails, and a short burned-in MP4 loop.

Speed (research 8): decoding the source frame is the expensive part (~120 ms on the 120 fps recordings), so the
frame is cached per (media, source frame, size) under <workdir>\\preview\\frames; each style/text edit then only
rewrites a tiny ASS (visible pages only) and burns it onto the cached PNG (~60-90 ms). In the worker the module
caches stay warm between calls.
"""
from __future__ import annotations

import contextlib
import math
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from ..i18n import secs, tr as _tr
from ..util import EngineError, data_hash, file_hash, local_dir, run_ffmpeg
from . import compile as C
from . import layout as L
from . import model as M
from . import render as R
from . import templates as TP

CAPTION_TRACK_PREFIX = "Klipora Captions"
# Our caption tracks (overlay "Klipora Captions", MOGRT "Klipora Captions (Editable)") and the old AutoCut names.
CAPTION_TRACK_PREFIXES = (CAPTION_TRACK_PREFIX, "AutoCut Captions")
_PROBE = {}


def _probe(path):
    hit = _PROBE.get(path)
    if hit is None:
        from ..media import probe
        hit = _PROBE[path] = probe(path)
    return hit


def video_source_at(tl, t):
    """(clip, source seconds) of the picture under sequence time t: topmost enabled video clip with real media,
    skipping our own caption tracks/overlays. None when only gaps/graphics are there."""
    for tr in sorted(tl.video, key=lambda x: x.index, reverse=True):
        if (tr.name or "").startswith(CAPTION_TRACK_PREFIXES):
            continue
        for c in tr.clips:
            if c.disabled or not c.contains(t) or not c.has_media:
                continue
            name = Path(c.path).name.lower()
            if "_captions_v" in name or not Path(c.path).is_file():
                continue
            return c, c.seq_to_src(t)
    return None


def fit_filter(sw, sh, W, H, fit="cover"):
    """Scale/crop a source frame (sw x sh) to the sequence frame W x H (Premiere-like placement)."""
    if (sw, sh) == (W, H):
        return ""
    if fit == "contain":
        return f"scale={W}:{H}:force_original_aspect_ratio=decrease,pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color=black"
    if fit == "native":
        return f"pad=max(iw\\,{W}):max(ih\\,{H}):(ow-iw)/2:(oh-ih)/2:color=black,crop={W}:{H}"
    return f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H}"


def _even(v):
    return max(2, int(round(v / 2)) * 2)


def frame_source(tl, t, wd, scale=0.5, fit="cover"):
    """Where the sequence picture at t comes from: (cached PNG path, ffmpeg decode args or None, filter).
    When the PNG exists the args are None; otherwise decoding `args` + `vf` produces it."""
    W, H = _even(tl.width * scale), _even(tl.height * scale)
    d = Path(wd) / "preview" / "frames"
    d.mkdir(parents=True, exist_ok=True)
    hit = video_source_at(tl, t)
    if hit is None:
        out = d / f"{data_hash(['blank', W, H])}.png"
        return out, (None if out.is_file() else ["-f", "lavfi", "-i", f"color=c=0x1d2330:s={W}x{H}:d=1"]), "null"
    clip, src = hit
    info = _probe(clip.path)
    fps = info.get("fps") or 30
    fidx = int(math.floor(src * fps + 1e-6))
    out = d / f"{data_hash([file_hash(clip.path), fidx, W, H, fit, tl.width, tl.height])}.png"
    if out.is_file():
        return out, None, None
    vf = fit_filter(info.get("width") or W, info.get("height") or H, tl.width, tl.height, fit)
    vf = (vf + "," if vf else "") + f"scale={W}:{H}:flags=bicubic"
    return out, ["-ss", f"{fidx / fps:.4f}", "-i", str(clip.path)], vf


def frame_png(tl, t, wd, scale=0.5, fit="cover"):
    """Cached PNG of the sequence picture at t (W*scale x H*scale). Returns (Path, cached: bool)."""
    out, args, vf = frame_source(tl, t, wd, scale, fit)
    if args is None:
        return out, True
    run_ffmpeg([*args, "-frames:v", "1", "-vf", vf, "-compression_level", "1", "-y", str(out)])
    _trim(out.parent, 60)
    return out, False


def _trim(folder, keep):
    files = sorted(Path(folder).glob("*.png"), key=lambda p: p.stat().st_mtime)
    for p in files[:-keep]:
        with contextlib.suppress(OSError):
            p.unlink()


def _out_name(folder, prefix):
    """Unique file per call (the panel's <img> would cache a reused path); keeps the last 6."""
    folder.mkdir(parents=True, exist_ok=True)
    for p in sorted(folder.glob(prefix + "_*.png"), key=lambda p: p.stat().st_mtime)[:-5]:
        with contextlib.suppress(OSError):
            p.unlink()
    return folder / f"{prefix}_{int(time.time() * 1000) % 100000000}.png"


def preview(doc, tl, t, wd, mode="frame", scale=0.5, fit="cover", frame=None):
    """Caption preview at sequence time t. mode: frame (burned over the real frame) | overlay (transparent PNG)
    | both. frame = optional PNG exported by Premiere (bac_exportFrame) instead of decoding the media.
    -> {png?, overlay?, t, pages, ms, frame_cached}"""
    t0 = time.perf_counter()
    pairs, W, H = M.page_pairs(doc, t=t, window=0.0)
    d = Path(wd) / "preview"
    d.mkdir(parents=True, exist_ok=True)
    ass = d / "prev.ass"
    C.write_ass(ass, pairs, W, H)
    fonts = R.fonts_rel(d, ass)
    pw, ph = _even(W * scale), _even(H * scale)
    out = {"t": t, "pages": [p.id for p, _ in pairs], "w": pw, "h": ph}
    if mode in ("frame", "both"):
        burn = f"setpts={t:.3f}/TB,ass='{ass.name}':fontsdir='{fonts}':shaping=complex"
        png = _out_name(d, "prev")
        dec = None
        if frame:
            src, cached = Path(frame), True
            if not src.is_file():
                raise EngineError("NO_FRAME", _tr("cap.preview.noFrame"), str(frame))
        elif tl is not None:
            src, dec, vf = frame_source(tl, t, wd, scale, fit)
            cached = dec is None
        else:
            src, cached = None, True
        if dec is not None:
            # one ffmpeg: decode once, write the frame cache AND the preview (saves a spawn + a PNG decode)
            _ff_cwd([*dec, "-frames:v", "1", "-filter_complex", f"[0:v]{vf},split[f][c];[c]{burn}[p]",
                     "-map", "[f]", "-compression_level", "1", "-y", str(src),
                     "-map", "[p]", "-frames:v", "1", "-compression_level", "1", "-y", png.name], d)
            _trim(src.parent, 60)
        else:
            args = ["-i", str(src)] if src is not None else ["-f", "lavfi", "-i", f"color=c=0x1d2330:s={pw}x{ph}:d=1"]
            _ff_cwd([*args, "-frames:v", "1", "-vf", f"scale={pw}:{ph},{burn}", "-compression_level", "1", "-y",
                     png.name], d)
        out["png"] = str(png)
        out["frame_cached"] = cached
    if mode in ("overlay", "both"):
        ov = _out_name(d, "ovl")
        src_ = f"s={pw}x{ph}:r=30:d=0.04"
        ass_f = f"setpts=PTS+{t:.3f}/TB,ass='{ass.name}':fontsdir='{fonts}':shaping=complex"
        fc = (f"[0]{ass_f},split[b1][b2];[1]{ass_f},split[w1][w2];"
              f"[b1][w1]lut2=c0='{R.C_EXPR}':c1='{R.C_EXPR}':c2='{R.C_EXPR}'[rgb];"
              f"[b2][w2]lut2=c0='{R.A_EXPR}':c1='{R.A_EXPR}':c2='{R.A_EXPR}',extractplanes=g[a];"
              f"[rgb][a]alphamerge,format=rgba[v]")
        _ff_cwd(["-f", "lavfi", "-i", f"color=c=black:{src_},format=gbrp", "-f", "lavfi", "-i",
                 f"color=c=white:{src_},format=gbrp", "-filter_complex", fc, "-map", "[v]", "-frames:v", "1",
                 "-compression_level", "1", "-y", ov.name], d)
        out["overlay"] = str(ov)
    out["ms"] = round((time.perf_counter() - t0) * 1000)
    return out


def _ff_cwd(args, cwd):
    """run_ffmpeg with a working directory (relative ass/fontsdir names)."""
    import subprocess
    from ..util import ffmpeg_exe
    cmd = [ffmpeg_exe(), "-hide_banner", "-nostdin", "-loglevel", "error", *[str(a) for a in args]]
    r = subprocess.run(cmd, cwd=str(cwd), capture_output=True, stdin=subprocess.DEVNULL,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if r.returncode != 0:
        err = (r.stderr or b"").decode("utf-8", "replace").strip().splitlines()
        raise EngineError("FFMPEG", _tr("cap.render.ffmpeg", err=err[-1][:300] if err else f"exit {r.returncode}"),
                          "\n".join(err[-5:])[:800])
    return r


# ---------------------------------------------------------------- gallery thumbnails

DEMO = [("Rahasia", 0.0, 0.45), ("cuan", 0.45, 0.8), ("jualan", 0.8, 1.2), ("online", 1.2, 1.6),
        ("100%", 1.6, 2.1), ("gratis!", 2.1, 2.6)]
THUMB_V = 2


def _demo_words(tpl):
    out = []
    for i, (t, a, b) in enumerate(DEMO):
        out.append(L.RWord(text=L.transform_text(t, tpl), start=a, end=b, id=f"demo:{i}", raw=t,
                           sentence_end=t.endswith("!"), seg_end=1 if i == len(DEMO) - 1 else 0))
    return out


def _bg(W, H):
    """Neutral dark gradient background PNG (cached)."""
    p = local_dir("captions", "thumbs") / f"bg_{W}x{H}.png"
    if not p.is_file():
        from PIL import Image
        import numpy as np
        y = np.linspace(0, 1, H)[:, None]
        x = np.linspace(0, 1, W)[None, :]
        r = (38 + 30 * x + 10 * y).astype(np.uint8)
        g = (44 + 22 * (1 - x) + 14 * y).astype(np.uint8)
        b = (62 + 40 * y + 10 * x).astype(np.uint8)
        Image.fromarray(np.dstack([r, g, b])).save(p)
    return p


def thumb(tpl, W=1080, H=1920, size=(360, 180), force=False, bg=None):
    """Picker thumbnail of one template: demo phrase mid-animation, cropped to the caption band. Cached by
    template content. -> Path"""
    key = data_hash([THUMB_V, {k: v for k, v in tpl.items() if not k.startswith("_")}, W, H, size,
                     str(bg) if bg else None])
    d = local_dir("captions", "thumbs")
    out = d / f"{tpl['id']}_{key[:12]}.png"
    if out.is_file() and not force:
        return out
    tpl = L.prep_template(dict(tpl))
    caps = L.paginate(_demo_words(tpl), tpl, W, H)
    if not caps:
        raise EngineError("THUMB", _tr("cap.preview.noCaptions", id=tpl["id"]))
    first = caps[0]
    ws = first.words
    t = (ws[min(1, len(ws) - 1)].start + 0.14) if tpl["timing"]["mode"] != "line" else first.start + 0.45
    t = min(t, first.end - 0.05)
    pairs = [(c, tpl) for c in caps]
    work = d / "work"
    work.mkdir(exist_ok=True)
    ass = work / f"{tpl['id']}_{key[:8]}.ass"
    C.write_ass(ass, pairs, W, H)
    hw, hh = _even(W / 2), _even(H / 2)
    src = Path(bg) if bg else _bg(hw, hh)
    tmp = work / f"{tpl['id']}_{key[:8]}.png"
    _ff_cwd(["-i", str(src), "-frames:v", "1", "-vf",
             f"scale={hw}:{hh},setpts={t:.3f}/TB,ass='{ass.name}':fontsdir='{R.fonts_rel(work, ass)}':shaping=complex",
             "-y", tmp.name], work)
    bx, by, bw, bh = C.caption_band(pairs[:1], W, H)
    from PIL import Image
    full = Image.open(tmp).convert("RGB")
    cx, cy = (bx + bw / 2) / 2, (by + bh / 2) / 2
    ar = size[0] / size[1]
    half_w = min(max(bw / 4 + 24, (bh / 4 + 14) * ar, 150), full.width / 2)
    half_h = half_w / ar
    if half_h > full.height / 2:
        half_h = full.height / 2
        half_w = half_h * ar
    cx = min(max(cx, half_w), full.width - half_w)
    cy = min(max(cy, half_h), full.height - half_h)
    im = full.crop((int(cx - half_w), int(cy - half_h), int(cx + half_w), int(cy + half_h)))
    im.resize(size, Image.LANCZOS).save(out)
    for p in (ass, tmp):
        with contextlib.suppress(OSError):
            p.unlink()
    return out


def preferred_size(tpl):
    """Frame a template is designed for: first entry of its `aspect` list (16:9 -> 1920x1080, else 1080x1920)."""
    asp = (tpl.get("aspect") or ["9:16"])[0]
    return (1920, 1080) if asp in ("16:9", "21:9", "4:3") else (1080, 1080) if asp == "1:1" else (1080, 1920)


def gallery(tpls, W=None, H=None, size=(360, 180), force=False, bg=None, threads=8, emit=None):
    """Thumbnails for many templates in parallel. W/H None = each template's preferred frame.
    -> ({template id: path | None}, {id: error})"""
    out, errs = {}, {}
    with ThreadPoolExecutor(max(1, threads)) as ex:
        futs = {tp["id"]: ex.submit(thumb, tp, *((W, H) if W and H else preferred_size(tp)), size, force, bg)
                for tp in tpls}
        for k, (tid, f) in enumerate(futs.items()):
            try:
                out[tid] = str(f.result())
            except Exception as e:  # noqa: BLE001
                out[tid] = None
                errs[tid] = str(getattr(e, "msg", e))[:200]
            if emit is not None:
                emit.progress(100 * (k + 1) / len(futs))
    return out, errs


# ---------------------------------------------------------------- short burned-in loop (MP4)

def burn(doc, tl, t0, t1, wd, scale=0.5, fit="cover", audio=True, emit=None):
    """Short MP4 of [t0, t1] (<= 30 s) with the captions burned in: the picture is rebuilt from the clips under
    the range (topmost real media), so it plays exactly like the sequence. For checking animation + sync; the
    full-length export stays in Premiere (overlay track)."""
    t1 = min(t1, t0 + 30.0, tl.duration)
    if t1 - t0 < 0.2:
        raise EngineError("BAD_RANGE", _tr("cap.preview.rangeShort"))
    W, H = tl.width, tl.height
    pw, ph = _even(W * scale), _even(H * scale)
    segs, t = [], t0                      # [(path, src_in, dur, has_audio)] following the topmost clip
    while t < t1 - 1e-3:
        hit = video_source_at(tl, t + 1e-4)
        if hit is None:
            nxt = min([c.start for tr in tl.video for c in tr.clips if c.start > t + 1e-4] + [t1])
            segs.append((None, 0.0, nxt - t, False))
            t = nxt
            continue
        clip, src = hit
        end = min(clip.end, t1)
        segs.append((clip.path, src, (end - t) * clip.speed, bool(_probe(clip.path).get("has_audio")), clip.speed))
        t = end
    pairs, _, _ = M.page_pairs(doc)
    pairs = [(c, tp) for c, tp in pairs if c.end >= t0 and c.start <= t1]
    d = Path(wd) / "preview"
    d.mkdir(parents=True, exist_ok=True)
    ass = d / "burn.ass"
    C.write_ass(ass, pairs, W, H)
    args, fc, vlabels, alabels = [], [], [], []
    use_audio = audio and all(s[0] is None or s[3] for s in segs)
    for i, s in enumerate(segs):
        if s[0] is None:
            args += ["-f", "lavfi", "-t", f"{s[2]:.3f}", "-i", f"color=c=black:s={pw}x{ph}:r=30"]
            fc.append(f"[{i}:v]fps=30,format=yuv420p,setsar=1[v{i}]")
            if use_audio:
                fc.append(f"anullsrc=r=48000:cl=stereo,atrim=0:{s[2]:.3f}[a{i}]")
        else:
            path, src, dur, _, speed = s
            info = _probe(path)
            vf = fit_filter(info.get("width") or W, info.get("height") or H, W, H, fit)
            vf = (vf + "," if vf else "") + f"scale={pw}:{ph}"
            sp = f",setpts=PTS/{speed}" if abs(speed - 1) > 1e-3 else ""
            args += ["-ss", f"{src:.3f}", "-t", f"{dur:.3f}", "-i", str(path)]
            fc.append(f"[{i}:v]{vf}{sp},fps=30,format=yuv420p,setsar=1[v{i}]")
            if use_audio:
                fc.append(f"[{i}:a]aresample=48000,aformat=channel_layouts=stereo{f',atempo={speed}' if abs(speed - 1) > 1e-3 else ''}[a{i}]")
        vlabels.append(f"[v{i}]")
        alabels.append(f"[a{i}]")
    n = len(segs)
    if use_audio:
        fc.append("".join(v + a for v, a in zip(vlabels, alabels)) + f"concat=n={n}:v=1:a=1[cv][ca]")
    else:
        fc.append("".join(vlabels) + f"concat=n={n}:v=1:a=0[cv]")
    fc.append(f"[cv]setpts=PTS-STARTPTS+{t0:.3f}/TB,ass='{ass.name}':fontsdir='{R.fonts_rel(d, ass)}':shaping=complex,"
              f"setpts=PTS-STARTPTS[outv]")
    out = _out_mp4(d)
    enc = _encoder()
    cmd = [*args, "-filter_complex", ";".join(fc), "-map", "[outv]"] + (["-map", "[ca]", "-c:a", "aac", "-b:a", "128k"]
                                                                          if use_audio else ["-an"]) + enc + [out.name]
    R.run_ffmpeg_progress(cmd, d, t1 - t0, emit)
    return {"path": str(out), "t0": round(t0, 3), "t1": round(t1, 3), "w": pw, "h": ph, "audio": use_audio,
            "segments": n, "summary": _tr("cap.preview.mp4", sec=secs(t1 - t0))}


def _out_mp4(d):
    for p in sorted(d.glob("loop_*.mp4"), key=lambda p: p.stat().st_mtime)[:-2]:
        with contextlib.suppress(OSError):
            p.unlink()
    return d / f"loop_{int(time.time() * 1000) % 100000000}.mp4"


_ENC = {}


def _encoder():
    """h264_nvenc when it works on this PC (one 1-frame probe), else libx264 ultrafast."""
    if "v" not in _ENC:
        try:
            run_ffmpeg(["-f", "lavfi", "-i", "color=c=black:s=256x256:d=0.1", "-c:v", "h264_nvenc", "-f", "null", "-"],
                       timeout=20)
            _ENC["v"] = ["-c:v", "h264_nvenc", "-preset", "p2", "-cq", "26", "-pix_fmt", "yuv420p"]
        except Exception:  # noqa: BLE001
            _ENC["v"] = ["-c:v", "libx264", "-preset", "ultrafast", "-crf", "24", "-pix_fmt", "yuv420p"]
    return _ENC["v"]


def templates_for_gallery(ids=None, W=1080, H=1920):
    tpls = TP.list_all(W, H)
    if ids:
        want = {TP.slug(i) for i in ids}
        tpls = [t for t in tpls if t["id"] in want]
    return tpls
