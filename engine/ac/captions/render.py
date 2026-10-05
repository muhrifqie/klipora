"""ASS -> transparent overlay video with EXACT straight alpha (verified pixel-identical in Premiere 26.2.2,
docs/research/premiere_graphics_api.md 1.2) + versioned output files.

FFmpeg's `ass ... alpha=1` squares the alpha of semi-transparent colours and leaves RGB premultiplied, so we
render the ASS twice, over opaque black (B) and opaque white (W): alpha = 255 - (W - B), colour = B * 255 / alpha
(lut2 per plane). Sources are generated as gbrp INSIDE lavfi (FFmpeg 8.1.1 swscale yuv420p -> gbrp zeroes the
last W mod 16 columns, which became an opaque stripe through the matte). FFmpeg runs with cwd = the .ass folder
and relative names, so Windows drive colons never reach the filtergraph.
"""
from __future__ import annotations

import contextlib
import math
import os
import re
import subprocess
import threading
import time
from pathlib import Path

from ..i18n import secs, tr
from ..util import EngineError, ensure_free, ffmpeg_exe
from .layout import FONTS_DIR

C_EXPR = r"if(lt(y-x\,255)\,clip(x*255/(255-(y-x))\,0\,255)\,0)"
A_EXPR = "255-(y-x)"

CODECS = {
    # mb_per_min: measured at 1080x1920 30 fps with dense animated captions (research 7.1 / lab 1.4)
    "qtrle": {"args": ["-c:v", "qtrle", "-pix_fmt", "argb"], "ext": ".mov", "mb_per_min": 30, "label": "QuickTime Animation"},  # i18n-ignore
    "png": {"args": ["-c:v", "png", "-pix_fmt", "rgba", "-pred", "mixed"], "ext": ".mov", "mb_per_min": 45,  # i18n-ignore
            "label": "PNG in MOV"},
    # ProRes is YUV: BT.709 conversion + tags or Premiere shifts colours (pure red came back 255,25,0)
    "prores": {"args": ["-c:v", "prores_ks", "-profile:v", "4444", "-alpha_bits", "16", "-vendor", "apl0",
                        "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709", "-color_range", "tv"],
               "ext": ".mov", "mb_per_min": 130, "label": "ProRes 4444", "post": ",scale=out_color_matrix=bt709:out_range=tv,format=yuva444p10le"},  # i18n-ignore
}
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def fonts_rel(ass_dir, ass=None):
    """fontsdir relative to the ffmpeg cwd (the .ass folder): no drive colon in the filtergraph. With `ass` (the
    .ass path or its text) only the bundled fonts it uses are linked into a small cached folder
    (layout.fontset_dir): libass start-up cost grows with every font in fontsdir (~0,5 ms CPU each)."""
    target = FONTS_DIR
    if ass is not None:
        try:
            from .layout import fontset_dir
            text = ass if isinstance(ass, str) and "\n" in ass else Path(ass).read_text(encoding="utf-8")
            target = fontset_dir(text)
        except Exception:  # noqa: BLE001 - fall back to every bundled font
            target = FONTS_DIR
    try:
        return os.path.relpath(target, ass_dir).replace("\\", "/")
    except ValueError:            # different drive: copy-free fallback is impossible, use the escaped absolute path
        return str(target).replace("\\", "/").replace(":", "\\:")


def _ass_filter(ass_name, fonts):
    return f"ass='{ass_name}':fontsdir='{fonts}':shaping=complex"


def matte_args(ass_name, fonts, w, h, fps, dur, codec="qtrle", start=0.0):
    """ffmpeg arguments (without the executable / output) for a straight-alpha overlay of [start, start+dur]."""
    cd = CODECS[codec]
    ass = _ass_filter(ass_name, fonts)
    shift = f"setpts=PTS+{start:.3f}/TB," if start else ""
    back = f",setpts=PTS-{start:.3f}/TB" if start else ""
    src = f"s={w}x{h}:r={fps}:d={dur:.3f}"
    fc = (f"[0]{shift}{ass},split[b1][b2];[1]{shift}{ass},split[w1][w2];"
          f"[b1][w1]lut2=c0='{C_EXPR}':c1='{C_EXPR}':c2='{C_EXPR}'[rgb];"
          f"[b2][w2]lut2=c0='{A_EXPR}':c1='{A_EXPR}':c2='{A_EXPR}',extractplanes=g[a];"
          f"[rgb][a]alphamerge{back}{cd.get('post', '')}[v]")
    return ["-f", "lavfi", "-i", f"color=c=black:{src},format=gbrp",
            "-f", "lavfi", "-i", f"color=c=white:{src},format=gbrp",
            "-filter_complex", fc, "-map", "[v]", *cd["args"]]


def estimate_bytes(w, h, dur, codec="qtrle"):
    """Generous output size estimate (dense captions, scaled by pixel count), never below 20 MB."""
    mb = CODECS[codec]["mb_per_min"] * (w * h) / (1080 * 1920) * dur / 60 * 1.6  # i18n-ignore
    return int(max(20, mb) * 1024 ** 2)


def run_ffmpeg_progress(args, cwd, total, emit=None, lo=0.0, hi=100.0, timeout=None):
    """Run ffmpeg with -progress on stdout; maps out_time onto lo..hi of the current stage. Cancel (emit raises
    Cancelled) kills ffmpeg. Raises EngineError('FFMPEG') with the stderr tail on failure."""
    cmd = [ffmpeg_exe(), "-hide_banner", "-nostdin", "-loglevel", "error", "-y", "-progress", "pipe:1", "-nostats",
           *[str(a) for a in args]]
    p = subprocess.Popen(cmd, cwd=str(cwd), stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL,
                         creationflags=_NO_WINDOW)
    err = []
    th = threading.Thread(target=lambda: err.extend(p.stderr.read().decode("utf-8", "replace").splitlines()),
                          daemon=True)
    th.start()
    t0 = time.time()
    try:
        for raw in p.stdout:
            line = raw.decode("utf-8", "replace").strip()
            if line.startswith("out_time_us=") or line.startswith("out_time_ms="):
                try:
                    sec = int(line.split("=", 1)[1]) / 1e6
                except ValueError:
                    continue
                if emit is not None and total > 0:
                    emit.progress(lo + (hi - lo) * max(0.0, min(1.0, sec / total)))
            if timeout and time.time() - t0 > timeout:
                raise EngineError("FFMPEG", tr("cap.render.timeout"), tr("cap.render.timeoutHint", limit=secs(timeout)))
        p.wait()
    except BaseException:
        with contextlib.suppress(OSError):
            p.kill()
        p.wait()
        raise
    th.join(2)
    if p.returncode != 0:
        raise EngineError("FFMPEG", tr("cap.render.ffmpeg", err=err[-1][:300] if err else f"exit {p.returncode}"),
                          "\n".join(err[-5:])[:800])
    return time.time() - t0


def render_overlay(ass_path, out, w, h, fps=30, dur=None, codec="qtrle", emit=None, lo=0.0, hi=100.0, start=0.0):
    """Render an .ass (PlayRes w x h) to a straight-alpha overlay file. Returns seconds."""
    ass_path = Path(ass_path).resolve()
    out = Path(out).resolve()
    args = matte_args(ass_path.name, fonts_rel(ass_path.parent, ass_path), w, h, fps, dur, codec, start) + [str(out)]
    try:
        return run_ffmpeg_progress(args, ass_path.parent, dur, emit, lo, hi)
    except BaseException:
        with contextlib.suppress(OSError):
            out.unlink()
        raise


# ---------------------------------------------------------------- chunked + cached render

RENDER_V = 1            # bump when the render pipeline changes (invalidates cached chunks)
CHUNK_SEC = 60.0


def chunk_bounds(pages_t, dur, fps, chunk=CHUNK_SEC, search=10.0):
    """Frame-aligned chunk edges [(f0, f1)] covering [0, dur): about every `chunk` seconds, moved into a moment
    where no page is visible when one exists within +-search s (so an edit usually dirties one chunk)."""
    total = int(math.ceil(dur * fps))
    edges = [0]
    gaps = [(pages_t[i][1], pages_t[i + 1][0]) for i in range(len(pages_t) - 1)
            if pages_t[i + 1][0] - pages_t[i][1] > 2.0 / fps]
    search = min(search, chunk / 3)
    t = chunk
    while t < dur - chunk * 0.3:
        best, bd = t, None
        for g0, g1 in gaps:                        # nearest moment without any caption on screen
            mid = (g0 + g1) / 2
            if abs(mid - t) <= search and (bd is None or abs(mid - t) < bd):
                best, bd = mid, abs(mid - t)
        f = int(round(best * fps))
        if f > edges[-1] + fps and f < total:
            edges.append(f)
        t = best + chunk
    edges.append(total)
    return list(zip(edges, edges[1:]))


def render_chunked(pairs, W, H, band, fps, dur, codec, folder, out, emit=None, workers=None, chunk=CHUNK_SEC):
    """Render the overlay as cached frame-aligned chunks in parallel, then concatenate (stream copy) into `out`.
    A chunk is keyed by the exact ASS events it contains, so after an edit only the dirty chunks re-render.
    Returns {seconds, chunks, rendered, reused, keys}."""
    from concurrent.futures import ThreadPoolExecutor
    from ..util import data_hash
    from . import compile as C
    t_start = time.time()
    folder = Path(folder)
    cdir = folder / "chunks"
    cdir.mkdir(parents=True, exist_ok=True)
    bx, by, bw, bh = band
    use_band = (bw, bh) != (W, H)
    pt = sorted((c.start, c.end) for c, _ in pairs)
    bounds = chunk_bounds(pt, dur, fps, chunk)
    jobs = []
    for f0, f1 in bounds:
        a, b = f0 / fps, f1 / fps
        sel = [(c, tp) for c, tp in pairs if c.end > a - 0.05 and c.start < b + 0.05]
        text, n = C.ass_text(sel, W, H, band if use_band else None, title="Klipora caption chunk")
        key = data_hash([RENDER_V, text, f0, f1, bw, bh, fps, codec])
        jobs.append({"f0": f0, "f1": f1, "key": key, "text": text, "path": cdir / f"{key}{CODECS[codec]['ext']}"})
    todo = [j for j in jobs if not j["path"].is_file()]
    prog = {id(j): 0.0 for j in todo}
    total_frames = sum(j["f1"] - j["f0"] for j in todo) or 1
    cancel = threading.Event()
    procs = {}

    def one(j):
        ass = cdir / f"{j['key']}.ass"
        ass.write_text(j["text"], encoding="utf-8")
        tmp = j["path"].with_name(j["key"] + ".part" + CODECS[codec]["ext"])
        nfr = j["f1"] - j["f0"]
        args = matte_args(ass.name, fonts_rel(cdir, j["text"]), bw, bh, fps, nfr / fps, codec, j["f0"] / fps)
        cmd = [ffmpeg_exe(), "-hide_banner", "-nostdin", "-loglevel", "error", "-y", "-progress", "pipe:1", "-nostats",
               *args, "-frames:v", str(nfr), tmp.name]
        p = subprocess.Popen(cmd, cwd=str(cdir), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             stdin=subprocess.DEVNULL, creationflags=_NO_WINDOW)
        procs[id(j)] = p
        err = []
        th = threading.Thread(target=lambda: err.extend(p.stderr.read().decode("utf-8", "replace").splitlines()),
                              daemon=True)
        th.start()
        for raw in p.stdout:
            if cancel.is_set():
                break
            line = raw.decode("utf-8", "replace").strip()
            if line.startswith("frame="):
                with contextlib.suppress(ValueError):
                    prog[id(j)] = min(nfr, int(line.split("=", 1)[1]))
        if cancel.is_set():
            with contextlib.suppress(OSError):
                p.kill()
        p.wait()
        th.join(2)
        with contextlib.suppress(OSError):
            ass.unlink()
        if cancel.is_set():
            with contextlib.suppress(OSError):
                tmp.unlink()
            return
        if p.returncode != 0:
            with contextlib.suppress(OSError):
                tmp.unlink()
            raise EngineError("FFMPEG", tr("cap.render.ffmpeg", err=err[-1][:300] if err else f"exit {p.returncode}"),
                              "\n".join(err[-5:])[:800])
        os.replace(tmp, j["path"])
        prog[id(j)] = nfr

    workers = workers or max(2, min(8, (os.cpu_count() or 4) // 2))
    if todo:
        with ThreadPoolExecutor(workers) as ex:
            futs = [ex.submit(one, j) for j in todo]
            try:
                while not all(f.done() for f in futs):
                    if emit is not None:
                        emit.progress(min(97.0, 97.0 * sum(prog.values()) / total_frames))
                    time.sleep(0.15)
                for f in futs:
                    f.result()
            except BaseException:
                cancel.set()
                for p in list(procs.values()):
                    with contextlib.suppress(OSError):
                        p.kill()
                raise
    # concatenate (stream copy: qtrle/png/prores frames are all intra)
    lst = cdir / f"concat_{int(time.time() * 1000)}.txt"
    lst.write_text("".join(f"file '{j['path'].name}'\n" for j in jobs), encoding="utf-8")
    tmp_out = Path(out).with_name(Path(out).stem + ".part" + Path(out).suffix)
    try:
        cmd = [ffmpeg_exe(), "-hide_banner", "-nostdin", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0",
               "-i", lst.name, "-c", "copy", "-map", "0:v", str(Path(tmp_out).resolve())]
        r = subprocess.run(cmd, cwd=str(cdir), capture_output=True, stdin=subprocess.DEVNULL, creationflags=_NO_WINDOW)
        if r.returncode != 0:
            err = (r.stderr or b"").decode("utf-8", "replace").strip().splitlines()
            raise EngineError("FFMPEG", tr("cap.render.concat", err=err[-1][:300] if err else ""),
                              "\n".join(err[-5:])[:800])
        os.replace(tmp_out, out)
    finally:
        with contextlib.suppress(OSError):
            lst.unlink()
        with contextlib.suppress(OSError):
            Path(tmp_out).unlink()
    if emit is not None:
        emit.progress(100)
    return {"seconds": round(time.time() - t_start, 2), "chunks": len(jobs), "rendered": len(todo),
            "reused": len(jobs) - len(todo), "keys": [j["key"] for j in jobs]}


def prune_chunks(folder, keep_keys):
    """Delete cached chunks not used by the renders we keep (best-effort)."""
    keep = set(keep_keys or [])
    n = 0
    for p in (Path(folder) / "chunks").glob("*"):
        if p.suffix.lower() in (".mov", ".ass", ".txt") and p.stem.split(".")[0] not in keep:
            with contextlib.suppress(OSError):
                p.unlink()
                n += 1
    return n


# ---------------------------------------------------------------- versioned files

def versions(folder, stem):
    """[(n, Path)] of existing '<stem>_v<n>.<ext>' files, ascending."""
    rx = re.compile(re.escape(stem) + r"_v(\d+)\.[A-Za-z0-9]+$", re.I)
    out = []
    try:
        for p in Path(folder).iterdir():
            m = rx.match(p.name)
            if m:
                out.append((int(m.group(1)), p))
    except OSError:
        pass
    return sorted(out)


def next_version(folder, stem, ext=".mov", floor=0):
    """Next free version number (never reuses a name Premiere may hold open)."""
    have = versions(folder, stem)
    n = max([floor] + [v for v, _ in have]) + 1
    return n, Path(folder) / f"{stem}_v{n}{ext}"


def cleanup_versions(folder, stem, keep=2, protect=()):
    """Delete old versions best-effort (Premiere keeps linked files open: WinError 32 is ignored)."""
    prot = {str(Path(p).resolve()).lower() for p in protect if p}
    have = versions(folder, stem)
    removed = []
    for n, p in have[:-keep] if keep else have:
        if str(p.resolve()).lower() in prot:
            continue
        try:
            p.unlink()
            removed.append(p.name)
        except OSError:
            pass
    return removed


def check_disk(folder, w, h, dur, codec):
    need = estimate_bytes(w, h, dur, codec)
    ensure_free(need, folder)
    return need
