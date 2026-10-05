"""Auto Resize (tool id "resize"): a 9:16 / 1:1 / 4:5 / 16:9 version of the active sequence whose framing follows
the action (screen recordings: clicks, typing, menus, page changes) or the faces / active speaker (talking heads).
The original sequence is never touched.

Actions (job protocol, docs/SPEC.md section 2):
  analyze  read the timeline's main video track, analyse each media once (cached next to it), plan a camera path in
           SEQUENCE time (shots = clip boundaries + page changes + speaker switches; static vs hybrid tracking per
           shot; speed preset), write a review file (one row per camera move, `on` = keep it, off = hold the previous
           framing) and a preview contact sheet PNG. -> {review, preview, summary, stats, content, advice, ...}
  apply    "Pintar" engine: edited review -> plan {"kind": "reframe"} = new frame size + per clip Motion Scale and
           Position (static value or keyframes in sequence seconds; the host converts to clip media time).
           The panel applies it with panel/host/38_resize.jsx (clone -> tlSetFrame -> keys) .
  render   "Fokus + blur" engine (or any layout): renders the section to MP4 (NVENC, audio mixed from the heard
           clips) -> plan {"kind": "import_render", "path"}; the host imports it into a new sequence.
The third engine, Premiere's own Auto Reframe (seq.autoReframeSequence), needs no engine job.

Params: target 9x16|1x1|4x5|16x9, layout crop|focus, speed slow|normal|fast|none, subject auto|screen|face,
zoom 1..2 (window tighter than full height), bands (ignore taskbar/toasts), fps (render), bg blur|dark, scope.
Research: docs/research/vision.md 1-2, 4, 6.3; product_visual.md 4.2; premiere_timeline_api.md 4, 6, 8.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

import numpy as np

from .. import media as M
from .. import review as RV
from ..i18n import dec, secs, tr
from ..timeline import Timeline
from ..util import EngineError, data_hash, read_json, safe_name, workdir, write_json
from . import _resize_camera as CAM
from . import _resize_render as R
from . import _resize_vision as V

TITLE = "Auto Resize"
DESCRIPTION = "Versi 9:16, 1:1, 4:5 atau 16:9 dengan kamera yang mengikuti aksi layar atau wajah."   # i18n-ignore (fallback of tool.resize.desc)

TARGETS = R.TARGETS
LAYOUTS = ("crop", "focus")
SUBJECTS = ("auto", "screen", "face")
STILL_EXT = {".png", ".jpg", ".jpeg", ".bmp", ".gif", ".tif", ".tiff", ".psd", ".webp", ".ai"}
DEFAULTS = {"target": "9x16", "layout": "crop", "speed": "normal", "subject": "auto", "zoom": 1.0, "bands": True,
            "fps": 30, "bg": "blur"}
PATH_V = 1
HZ = 10.0                     # camera planning grid (timeline samples per second)
MASS_MIN = 60.0               # windowed activity mass below this = no target (hold)
FACE_RATIO = 0.35             # share of probe frames with a big face -> talking head


# ---------------------------------------------------------------- params / timeline

def params(job):
    p = dict(DEFAULTS)
    p.update({k: v for k, v in (job.get("params") or {}).items() if v is not None})
    p["target"] = str(p["target"]).replace(":", "x")
    if p["target"] not in TARGETS:
        raise EngineError("BAD_PARAM", tr("resize.badFormat", fmt=p["target"]), tr("resize.badFormatHint"))
    if p["layout"] not in LAYOUTS:
        p["layout"] = "crop"
    if p["speed"] not in CAM.SPEEDS:
        p["speed"] = "normal"
    if p["subject"] not in SUBJECTS:
        p["subject"] = "auto"
    p["zoom"] = float(min(2.0, max(1.0, float(p.get("zoom") or 1.0))))
    p["fps"] = 60 if int(float(p.get("fps") or 30)) >= 50 else 30
    p["bands"] = bool(p["bands"])
    p["bg"] = "dark" if p.get("bg") == "dark" else "blur"
    return p


def is_still(path):
    return Path(str(path)).suffix.lower() in STILL_EXT


def main_track(tl):
    """Video track carrying the most media time (V1 for screen tutorials). -> (track or None, clips)."""
    best, best_d = None, 0.0
    for trk in tl.video:
        d = sum(c.dur for c in trk.clips if c.has_media and not c.disabled)
        if d > best_d + 1e-9:
            best, best_d = trk, d
    if best is None:
        return None, []
    clips = [c for c in best.clips if c.has_media and not c.disabled and Path(c.path).is_file()]
    return best, sorted(clips, key=lambda c: c.start)


def scope_of(tl, p):
    rs = tl.scope_ranges(p.get("scope"))
    t0, t1 = max(0.0, rs[0][0]), min(tl.duration, rs[-1][1])
    if t1 - t0 < 0.1:
        t0, t1 = 0.0, tl.duration
    return round(t0, 4), round(t1, 4)


def clip_sig(clips):
    return [[c.path, round(c.start, 4), round(c.end, 4), round(c.src_in, 4), round(c.speed, 4)] for c in clips]


def geometries(clips, p):
    """Layout geometry per media path (sizes can differ between clips)."""
    out = {}
    for c in clips:
        if c.path in out:
            continue
        info = M.probe(c.path)
        w, h = info["width"] or 1920, info["height"] or 1080
        g = R.geometry(p["target"], p["layout"], w, h, p["zoom"])
        g["src"] = [w, h]
        out[c.path] = g
    return out


def _same_aspect(src_w, src_h, target):
    a, b = TARGETS[target]["ratio"]
    return abs(src_w / src_h - a / b) < 0.02


# ---------------------------------------------------------------- analysis per media

def analyse_media(paths, subject, emit, cache_dir=None):
    """-> {path: {"kind": screen|face|none, "act", "faces", "probe", "cached"}} with progress over the stage."""
    durs = {pa: max(1.0, M.probe(pa)["duration"]) for pa in paths}
    tot = sum(durs.values()) or 1.0
    out, base, all_cached = {}, 0.0, True
    for pa in paths:
        share = 100.0 * durs[pa] / tot
        sub = (lambda lo, sh: (lambda pct: emit.progress(lo + sh * pct / 100.0)))(base, share)
        rec = {"kind": "none", "act": None, "faces": None, "probe": None, "cached": True}
        if is_still(pa) or not M.probe(pa)["has_video"]:
            out[pa] = rec
            base += share
            continue
        kind = subject
        if subject == "auto":
            rec["probe"] = V.probe_faces(pa, cache_dir=cache_dir)
            sub(4)
            kind = "face" if rec["probe"]["ratio"] >= FACE_RATIO else "screen"
        rec["kind"] = kind
        p2 = (lambda lo, sh: (lambda pct: emit.progress(lo + sh * (0.05 + 0.95 * pct / 100.0))))(base, share)
        if kind == "screen":
            rec["act"] = V.activity(pa, prog=p2, cache_dir=cache_dir)
            rec["cached"] = bool(getattr(rec["act"], "cached", False))
        else:
            rec["faces"] = V.faces(pa, prog=p2, cache_dir=cache_dir)
            rec["cached"] = bool(rec["faces"].get("cached"))
        all_cached = all_cached and rec["cached"]
        out[pa] = rec
        base += share
        emit.progress(base)
    if all_cached and paths:
        emit.done_note(tr("resize.fromCache"))
    return out


# ---------------------------------------------------------------- targets per clip -> sub-shots

def _screen_subshots(c, a, b, act, weights):
    """Sub-shots of clip c inside [a, b) split at page changes, each with grid times + action centre (NaN = none).
    -> [(s0, s1, ts, xs, ys, reason, conf)]"""
    n = max(1, int(round((b - a) * HZ)))
    ts = a + (np.arange(n) + 0.5) * (b - a) / n
    js = act.index(c.src_in + (ts - c.start) * c.speed)
    kind = act.kind[js]
    m = np.zeros(n)
    mx = np.zeros(n)
    my = np.zeros(n)
    memo = {}
    for i, j in enumerate(js):
        if kind[i] != V.LOCAL:
            continue
        if j not in memo:
            memo[j] = act.mass(int(j), weights)
        m[i], mx[i], my[i] = memo[j]
    cuts = [ts[i] - 0.5 / HZ for i in range(n) if kind[i] == V.GLOBAL and (i == 0 or kind[i - 1] != V.GLOBAL)]
    cuts = [t for t in CAM.merge_close(cuts, 1.0) if a + 0.3 < t < b - 0.3]
    bounds = [a] + cuts + [b]
    w = int(round(0.6 * HZ))
    out = []
    for k, (s0, s1) in enumerate(zip(bounds, bounds[1:])):
        sel = (ts >= s0) & (ts < s1)
        if not sel.any():
            continue
        cs = lambda v: np.concatenate([[0.0], np.cumsum(v[sel])])  # noqa: E731
        Mc, Xc, Yc = cs(m), cs(mx), cs(my)
        L = int(sel.sum())
        lo = np.clip(np.arange(L) - w, 0, L)
        hi = np.clip(np.arange(L) + w + 1, 0, L)
        mm = Mc[hi] - Mc[lo]
        with np.errstate(invalid="ignore", divide="ignore"):
            tx = np.where(mm >= MASS_MIN, (Xc[hi] - Xc[lo]) / mm, np.nan)
            ty = np.where(mm >= MASS_MIN, (Yc[hi] - Yc[lo]) / mm, np.nan)
        # The animation right before a page change (window opening, link click) and the page load right after it
        # are transitions, not something to look at: a camera glide there is cut off by the page change anyway.
        tt = ts[sel]
        trim = np.zeros(L, bool)
        if s1 < b - 1e-6:
            trim |= tt > s1 - 0.8
        if k:
            trim |= tt < s0 + 0.3
        if (~np.isnan(tx) & ~trim).any():
            tx[trim], ty[trim] = np.nan, np.nan
        conf = float(np.mean(~np.isnan(tx))) if L else 0.0
        out.append((s0, s1, ts[sel], tx, ty, "page" if k else "clip", conf))
    return out


def _face_subshots(c, a, b, F, cw):
    """Sub-shots split at hard cuts and speaker switches. -> [(s0, s1, ts, xs, ys, reason, conf)]"""
    fps = float(F["fps"])
    s_lo, s_hi = c.seq_to_src(a), c.seq_to_src(b)
    smp = [(c.src_to_seq(s[0]), s[1]) for s in F["samples"] if s_lo - 1e-6 <= s[0] < s_hi]
    cuts = [c.src_to_seq(x) for x in F.get("cuts", []) if s_lo + 0.2 < x < s_hi - 0.2]
    bounds = [a] + CAM.merge_close(cuts, 0.5) + [b]
    out = []
    for k, (s0, s1) in enumerate(zip(bounds, bounds[1:])):
        part = [s for s in smp if s0 - 1e-6 <= s[0] < s1]
        if not part:
            out.append((s0, s1, np.array([]), np.array([]), np.array([]), "cut" if k else "clip", 0.0))
            continue
        ts, xs, ys, who, nt, mode = CAM.choose_target(part, cw, "auto", fps / max(1e-6, c.speed))
        conf = float(np.mean(~np.isnan(xs))) if len(xs) else 0.0
        bi = CAM.speaker_bounds(who) if mode == "speaker" else [0]
        bi = bi + [len(ts)]
        for q, (i0, i1) in enumerate(zip(bi, bi[1:])):
            ss = s0 if q == 0 else max(s0, ts[i0] - 0.5 / fps)
            ee = s1 if q == len(bi) - 2 else max(ss, ts[i1] - 0.5 / fps)
            out.append((ss, ee, ts[i0:i1], xs[i0:i1], ys[i0:i1],
                        ("cut" if k else "clip") if q == 0 else "speaker", conf))
    return out


def build_shots(clips, ana, geoms, p, t0, t1):
    """Camera shots over [t0, t1) of the main track. Shot = {t0, t1, kind, reason, mode, kx, ky, conf, clip}."""
    shots, prev = [], [0.5, 0.5]
    for c in clips:
        a, b = max(c.start, t0), min(c.end, t1)
        if b - a < 1e-3:
            continue
        rec = ana.get(c.path) or {"kind": "none"}
        g = geoms[c.path]
        kind = rec["kind"]
        if kind == "screen" and rec.get("act") is not None:
            subs = _screen_subshots(c, a, b, rec["act"], rec["act"].band_weights(p["bands"]))
        elif kind == "face" and rec.get("faces") is not None:
            subs = _face_subshots(c, a, b, rec["faces"], g["cw"])
        else:
            subs = [(a, b, np.array([]), np.array([]), np.array([]), "clip", 0.0)]
        sp = CAM.speed_params(p["speed"], kind)
        ex, ey = 1.0 / g["src"][0], 1.0 / g["src"][1]
        for s0, s1, ts, xs, ys, reason, conf in subs:
            if s1 - s0 < 1e-3:
                continue
            if kind == "none":
                kx, mx = [[round(s0, 4), 0.5]], "static"
                ky, my = [[round(s0, 4), 0.5]], "static"
            elif s1 - s0 < 0.25:
                kx, mx = [[round(s0, 4), CAM.finite(prev[0])]], "hold"
                ky, my = [[round(s0, 4), CAM.finite(prev[1])]], "hold"
            else:
                kx, mx = CAM.plan_axis(ts, xs, round(s0, 4), s1, g["cw"], sp, kind, prev[0], ex)
                if g["ch"] < 0.999:
                    ky, my = CAM.plan_axis(ts, ys, round(s0, 4), s1, g["ch"], sp, kind, prev[1], ey)
                else:
                    ky, my = [[round(s0, 4), 0.5]], "full"
            mode = "track" if "track" in (mx, my) else ("hold" if mx == "hold" else "static")
            # inherit = "camera stays where it was" (hold, or a cut the hysteresis absorbed): after the user unchecks an
            # earlier move this shot must follow the camera's ACTUAL position, not the planned one
            inherit = len(kx) == 1 and len(ky) == 1 and abs(kx[0][1] - prev[0]) < 1e-9 and abs(ky[0][1] - prev[1]) < 1e-9
            shots.append({"t0": round(s0, 4), "t1": round(s1, 4), "kind": kind, "reason": reason, "mode": mode,
                          "kx": kx, "ky": ky, "conf": round(conf, 3), "clip": c.index, "path": c.path, "inherit": inherit})
            prev = [kx[-1][1], ky[-1][1]]
    return shots


# ---------------------------------------------------------------- review rows

def _where(x, y, ch):
    h = "left" if x < 0.36 else ("right" if x > 0.64 else "center")
    if ch < 0.999:
        v = "top" if y < 0.4 else ("bottom" if y > 0.6 else "")
        if v:
            return tr("resize.pos.combo", h=tr("resize.pos." + h), v=tr("resize.pos." + v)) if h != "center" else tr("resize.pos." + v)
    return tr("resize.pos." + h)


def _dir(dx, dy, ch):
    parts = []
    if abs(dx) >= 0.01:
        parts.append(tr("resize.dir.right" if dx > 0 else "resize.dir.left"))
    if ch < 0.999 and abs(dy) >= 0.01:
        parts.append(tr("resize.dir.down" if dy > 0 else "resize.dir.up"))
    return tr("resize.and").join(parts) or tr("resize.dir.slight")


_WHY = ("page", "clip", "cut", "speaker")   # shot reasons, labels: resize.why.<reason>


def _why(reason, fallback):
    return tr("resize.why." + reason) if reason in _WHY else tr(fallback)


def review_items(shots, geoms):
    """One row per camera change: 'move' (camera pans inside the shot) or 'jump' (camera cuts to a new framing at
    the start of the shot). Unchecking a row holds the previous framing instead."""
    items, prev = [], None
    for i, sh in enumerate(shots):
        g = geoms.get(sh["path"]) or {"ch": 1.0, "cw": 1.0}
        x0, y0 = sh["kx"][0][1], sh["ky"][0][1]
        x1, y1 = sh["kx"][-1][1], sh["ky"][-1][1]
        dur = sh["t1"] - sh["t0"]
        face = sh["kind"] == "face"
        src = tr("resize.src.face" if face else "resize.src.screen")
        tag = tr("resize.tag.face" if face else "resize.tag.screen")
        if sh["mode"] == "track":
            nk = len(sh["kx"]) + (len(sh["ky"]) if g["ch"] < 0.999 else 0)
            label = tr("resize.rowMove", src=src, dir=_dir(x1 - x0, y1 - y0, g["ch"]))
            ctx = tr("resize.rowMoveCtx", dur=_fmt_sec(dur), n=nk, end=_where(x1, y1, g["ch"]))
            items.append(RV.item(sh["t0"], sh["t1"], "move", on=True, conf=sh["conf"] or None, label=label, ctx=ctx,
                                 note={"type": "info", "text": tr("resize.rowMoveNote", why=_why(sh["reason"], "resize.why.shot"), src=src)},
                                 shot=i, src=tag))
        elif prev is not None and sh["mode"] in ("static",) and (abs(x0 - prev[0]) > 1e-4 or abs(y0 - prev[1]) > 1e-4):
            label = tr("resize.rowJump", to=_where(x0, y0, g["ch"]))
            ctx = tr("resize.rowJumpCtx", why=_why(sh["reason"], "resize.why.newShot"), dur=_fmt_sec(dur))
            items.append(RV.item(sh["t0"], sh["t1"], "jump", on=True, conf=sh["conf"] or None, label=label, ctx=ctx,
                                 note={"type": "info", "text": tr("resize.rowJumpNote", src=src)}, shot=i, src=tag))
        prev = (x1, y1)
    return items


def _fmt_sec(sec):
    """3.2 -> '3,2 dtk' / '3.2 s' (one decimal, UI language)."""
    return f"{dec(sec, 1)} {tr('unit.sec')}"


def apply_toggles(shots, doc):
    """Shots with the user's choices: an unchecked row holds the camera where the previous shot ended."""
    off = {it.get("shot") for it in (doc or {}).get("items", []) if not it.get("on") and it.get("shot") is not None}
    out, prev = [], None
    for i, sh in enumerate(shots):
        s = dict(sh)
        if prev is not None and (i in off or sh.get("inherit")):
            s["kx"], s["ky"] = [[sh["t0"], prev[0]]], [[sh["t0"], prev[1]]]
            if i in off:
                s["mode"], s["held"] = "hold", True
        out.append(s)
        prev = (s["kx"][-1][1], s["ky"][-1][1])
    return out


# ---------------------------------------------------------------- Premiere plan (crop layout as Motion keys)

def premiere_clips(clips, shots, p, fps_seq, scope):
    """Per clip Motion values for a frame of the target size. Position is NORMALISED to the frame (Premiere 26.x,
    verified in premiere_timeline_api.md 4); keys are sequence seconds (the host converts to clip media time).
      s = max(Wf/Wm, Hf/Hm) * zoom          Scale % = 100 s
      pos = 0.5 + (0.5 - c) * Wm * s / Wf    (c = normalised source point that should sit in the frame centre)
    A camera cut inside a clip = two keys one frame apart (linear interpolation, no API-specific hold). Cut times are
    snapped to the sequence frame grid: an unaligned pair leaves one frame half way between the two framings
    (verified live: frame 2989 of the 49 s clip showed x -0.33 -> 0.54 -> 0.56)."""
    wf, hf = TARGETS[p["target"]]["size"]
    fr = 1.0 / max(1.0, fps_seq or 30.0)
    out, n_keys = [], 0
    for c in clips:
        info = M.probe(c.path)
        wm, hm = info["width"] or wf, info["height"] or hf
        s = max(wf / wm, hf / hm) * p["zoom"]
        cw, ch = min(1.0, wf / (wm * s)), min(1.0, hf / (hm * s))
        own = [sh for sh in shots if sh["t1"] > c.start + 1e-6 and sh["t0"] < c.end - 1e-6] if shots else []
        pts = []
        for sh in own:
            lo, hi = max(c.start, sh["t0"]), min(c.end, sh["t1"])
            if lo > c.start + 1e-6:
                lo = min(c.end, _on_grid(lo, fr))
            if hi < c.end - 1e-6:
                hi = max(lo, _on_grid(hi, fr))
            if hi - lo < fr * 0.5:
                continue
            end = max(lo, hi - fr)
            tt = {round(lo, 6), round(end, 6)}
            tt.update(round(k[0], 6) for k in sh["kx"] + sh["ky"] if lo < k[0] < end)
            for t in sorted(tt):
                pts.append([t, CAM.value_at(sh["kx"], t), CAM.value_at(sh["ky"], t)])
        if not pts:                                    # outside the analysed scope: centre crop
            pts = [[round(c.start, 4), 0.5, 0.5]]
        lo_x, hi_x, lo_y, hi_y = cw / 2, 1 - cw / 2, ch / 2, 1 - ch / 2
        keys = []
        for t, x, y in pts:
            x = min(max(x, lo_x), hi_x) if cw < 1 else 0.5
            y = min(max(y, lo_y), hi_y) if ch < 1 else 0.5
            px = round(0.5 + (0.5 - x) * wm * s / wf, 5)
            py = round(0.5 + (0.5 - y) * hm * s / hf, 5)
            if keys and t - keys[-1][0] < 1e-4:
                keys[-1] = [t, px, py]
            else:
                keys.append([t, px, py])
        keys = _drop_flat(keys, tol=0.25 / max(wf, hf))
        item = {"track": c.track, "index": c.index, "start": round(c.start, 4), "end": round(c.end, 4),
                "scale": round(100.0 * s, 3)}
        if len(keys) == 1:
            item["pos"] = keys[0][1:]
        else:
            item["keys"] = keys
            n_keys += len(keys)
        out.append(item)
    return out, n_keys


def _on_grid(t, fr):
    """Nearest sequence frame boundary (sequence time 0 = frame 0)."""
    return round(round(t / fr) * fr, 6)


def _drop_flat(keys, tol):
    """Remove interior keys of constant runs (keeps run ends so linear interpolation stays exact)."""
    if len(keys) <= 2:
        if len(keys) == 2 and abs(keys[0][1] - keys[1][1]) <= tol and abs(keys[0][2] - keys[1][2]) <= tol:
            return keys[:1]
        return keys
    out = [keys[0]]
    for i in range(1, len(keys) - 1):
        a, b, c = out[-1], keys[i], keys[i + 1]
        same_prev = abs(a[1] - b[1]) <= tol and abs(a[2] - b[2]) <= tol
        same_next = abs(b[1] - c[1]) <= tol and abs(b[2] - c[2]) <= tol
        if same_prev and same_next:
            continue
        out.append(b)
    out.append(keys[-1])
    if all(abs(k[1] - out[0][1]) <= tol and abs(k[2] - out[0][2]) <= tol for k in out):
        return out[:1]
    return out


# ---------------------------------------------------------------- shared setup

def _setup(job, emit, with_scan=True):
    """Timeline, main clips, geometry, analyses and the shot plan (cached in <workdir>/resize/path.json)."""
    p = params(job)
    tl = Timeline.from_json(job.get("seq"))
    track, clips = main_track(tl)
    if not clips:
        raise EngineError("NO_VIDEO", tr("resize.noVideo"), tr("resize.noVideoHint"))
    t0, t1 = scope_of(tl, p)
    geoms = geometries(clips, p)
    first = geoms[clips[0].path]["src"]
    if _same_aspect(first[0], first[1], p["target"]):
        raise EngineError("SAME_ASPECT", tr("resize.sameAspect", fmt=TARGETS[p["target"]]["label"]),
                          tr("resize.sameAspectHint"))
    wd = workdir(job, tl.name)
    rdir = wd / "resize"
    rdir.mkdir(parents=True, exist_ok=True)
    key = data_hash({"v": PATH_V, "clips": clip_sig(clips), "scope": [t0, t1],
                     "p": {k: p[k] for k in ("target", "layout", "speed", "subject", "zoom", "bands")}})
    return {"p": p, "tl": tl, "track": track, "clips": clips, "t0": t0, "t1": t1, "geoms": geoms, "wd": wd,
            "rdir": rdir, "key": key, "cache_dir": job.get("cache_dir")}


def _plan(S, emit, scan_step="scan"):
    """Analyse + plan (always recomputed for analyze; apply/render reuse path.json when the key matches)."""
    used = [c for c in S["clips"] if c.end > S["t0"] and c.start < S["t1"]]
    paths = list(dict.fromkeys(c.path for c in used))
    with emit.step(scan_step):
        ana = analyse_media(paths, S["p"]["subject"], emit, cache_dir=S["cache_dir"])
        emit.progress(100)
    with emit.step("path"):
        shots = build_shots(S["clips"], ana, S["geoms"], S["p"], S["t0"], S["t1"])
        emit.progress(100)
    return ana, shots


def _load_path(S):
    d = read_json(S["rdir"] / "path.json")
    if isinstance(d, dict) and d.get("key") == S["key"] and isinstance(d.get("shots"), list):
        return d
    return None


# ---------------------------------------------------------------- actions

def analyze(job, emit):
    emit.plan([("read", tr("resize.st.read"), 0.04), ("scan", tr("resize.st.scan"), 0.74), ("path", tr("resize.st.path"), 0.07),
               ("preview", tr("resize.st.preview"), 0.15)])
    with emit.step("read"):
        S = _setup(job, emit)
        emit.progress(100)
    p, tl, clips, geoms = S["p"], S["tl"], S["clips"], S["geoms"]
    ana, shots = _plan(S, emit)
    items = review_items(shots, geoms)
    n_move = sum(1 for it in items if it["kind"] == "move")
    n_jump = len(items) - n_move
    stats = {"shots": len(shots), "moves": n_move, "jumps": n_jump,
             "keys": sum(len(s["kx"]) + len(s["ky"]) for s in shots if s["mode"] == "track")}
    doc = RV.new("resize", items, tl.duration, seq=tl, params=p, stats=stats,
                 target=p["target"], layout=p["layout"], scope=[S["t0"], S["t1"]])
    for it in doc["items"]:
        shots[it["shot"]]["item"] = it["id"]
    rpath = RV.path_for(S["wd"], "resize")
    old = read_json(rpath)
    if isinstance(old, dict) and old.get("params", {}).get("target") == p["target"] \
            and old.get("params", {}).get("layout") == p["layout"]:
        RV.carry_over(doc, old)
    stats["held"] = sum(1 for it in doc["items"] if not it["on"])   # rows kept off by the user's earlier choices
    RV.save(doc, rpath)
    g0 = geoms[clips[0].path]
    write_json(S["rdir"] / "path.json", {"v": PATH_V, "key": S["key"], "t0": S["t0"], "t1": S["t1"], "shots": shots,
                                         "target": p["target"], "layout": p["layout"]})
    content = []
    for pa, rec in ana.items():
        why = ""
        if rec.get("probe"):
            why = tr("resize.faceWhy", big=rec["probe"]["big"], n=rec["probe"]["n"])
        content.append({"path": pa, "name": Path(pa).name, "kind": rec["kind"], "why": why})
    warnings = []
    others = sum(len([c for c in trk.clips if not c.disabled]) for trk in tl.video if trk is not S["track"])
    if others:
        warnings.append(tr("resize.warnOthers", n=others))
    skipped = len([c for c in S["track"].clips if not c.disabled]) - len(clips)
    if skipped > 0:
        warnings.append(tr("resize.warnSkipped", n=skipped))
    for w in warnings:
        emit.warn(w)
    advice = None
    kinds = {r["kind"] for r in ana.values()}
    if p["layout"] == "crop" and "screen" in kinds and g0["cw"] < 0.35:
        advice = {"layout": "focus",
                  "text": tr("resize.advice", fmt=TARGETS[p["target"]]["label"], pct=round(g0["cw"] * 100))}
    with emit.step("preview"):
        for old_png in sorted(S["rdir"].glob("preview_*.png"))[:-2]:
            try:
                old_png.unlink()
            except OSError:
                pass
        png = S["rdir"] / f"preview_{time.strftime('%H%M%S')}_{p['target']}_{p['layout']}.png"
        sheet = R.contact_sheet(png, [c for c in clips if c.end > S["t0"] and c.start < S["t1"]],
                                apply_toggles(shots, doc), geoms, p["target"], S["t0"], S["t1"], doc["items"],
                                bg=p["bg"], prog=emit.progress)
    dur = S["t1"] - S["t0"]
    label = TARGETS[p["target"]]["label"]
    summary = tr("resize.summary", fmt=label, moves=n_move, jumps=n_jump, n=len(shots))
    return {"review": str(rpath), "preview": sheet["path"], "preview_times": sheet["times"], "summary": summary,
            "stats": dict(stats, per_min=round((n_move + n_jump) / max(dur / 60.0, 1e-6), 1)),
            "content": content, "target": p["target"], "label": label, "size": list(TARGETS[p["target"]]["size"]),
            "layout": p["layout"], "window": {"cw": round(g0["cw"], 4), "ch": round(g0["ch"], 4)},
            "scope": [S["t0"], S["t1"]], "duration": round(dur, 3), "advice": advice, "warnings": warnings}


PART_SUFFIX = ".part.mp4"


def _drop_partials(rdir):
    """Leftovers of a render that was killed (the panel cancels with taskkill /F, so no finally runs): unfinished
    videos (*.part.mp4) and temp audio. Finished renders never carry these names."""
    for f in list(rdir.glob("*" + PART_SUFFIX)) + list(rdir.glob("audio_*.m4a")):
        try:
            f.unlink()
        except OSError:
            pass


def _shots_for(job, emit, S):
    d = _load_path(S)
    if d is not None:
        with emit.step("scan", note=tr("resize.fromAnalysis")):    # close the planned stage too (else it stays "waiting")
            emit.progress(100)
        with emit.step("path"):
            emit.done_note(tr("resize.fromAnalysis"))
            emit.progress(100)
        return d["shots"]
    _ana, shots = _plan(S, emit)
    doc = RV.from_job(job) if job.get("review") else None
    if doc:   # re-attach item ids (stable ids: kind initial + t0)
        items = review_items(shots, S["geoms"])
        RV.assign_ids(items)
        for it in items:
            shots[it["shot"]]["item"] = it["id"]
    return shots


def _toggled(job, shots):
    if not job.get("review"):
        return shots
    doc = RV.from_job(job)
    by_id = {it.get("id"): it for it in doc.get("items", [])}
    off = set()
    for i, sh in enumerate(shots):
        it = by_id.get(sh.get("item"))
        if it is not None and not it.get("on"):
            off.add(i)
    return apply_toggles(shots, {"items": [{"shot": i, "on": False} for i in off]})


def apply(job, emit):
    """'Smart' (Pintar) engine: reframe plan for the host (new sequence + Motion Scale/Position per clip)."""
    emit.plan([("read", tr("resize.st.read"), 0.05), ("scan", tr("resize.st.scan"), 0.6), ("path", tr("resize.st.path"), 0.1),
               ("plan", tr("resize.st.keys"), 0.25)])
    with emit.step("read"):
        S = _setup(job, emit)
        emit.progress(100)
    p = S["p"]
    if p["layout"] != "crop":
        p = dict(p, layout="crop")
        S["geoms"] = geometries(S["clips"], p)
    shots = _shots_for(job, emit, S)
    with emit.step("plan"):
        shots = _toggled(job, shots)
        items, n_keys = premiere_clips(S["clips"], shots, p, S["tl"].fps, (S["t0"], S["t1"]))
        emit.progress(100)
    w, h = TARGETS[p["target"]]["size"]
    label = TARGETS[p["target"]]["label"]
    full = S["t0"] <= 1e-3 and S["t1"] >= S["tl"].duration - 1e-3
    plan = {"kind": "reframe", "engine": "smart", "target": p["target"], "label": label, "w": w, "h": h,
            "name": f"{S['tl'].name} ({p['target']})", "seq_id": S["tl"].id, "track": S["track"].index, "clips": items,
            "inout": None if full else [S["t0"], S["t1"]], "fps": S["tl"].fps}
    keyed = sum(1 for it in items if "keys" in it)
    return {"plan": plan, "summary": tr("resize.applySummary", fmt=label, n=len(items), keyed=keyed, keys=n_keys),
            "stats": {"clips": len(items), "keyed": keyed, "keys": n_keys}}


def render(job, emit):
    """'Focus + blur' (Fokus + blur) engine (any layout works): MP4 of the scope with the camera path, for import as a new
    sequence. Output: <workdir>/resize/<seq>_<target>_<time>.mp4 (new name every run: Premiere locks imported files)."""
    emit.plan([("read", tr("resize.st.read"), 0.03), ("scan", tr("resize.st.scan"), 0.2), ("path", tr("resize.st.path"), 0.02),
               ("audio", tr("resize.st.audio"), 0.05), ("render", tr("resize.st.render"), 0.7)])
    with emit.step("read"):
        S = _setup(job, emit)
        emit.progress(100)
    p, tl = S["p"], S["tl"]
    dur = S["t1"] - S["t0"]
    _drop_partials(S["rdir"])
    R.free_check(dur, S["rdir"])
    shots = _toggled(job, _shots_for(job, emit, S))
    stamp = time.strftime("%H%M%S")
    out = S["rdir"] / f"{safe_name(tl.name, 60)}_{p['target']}_{stamp}.mp4"
    part = out.with_name(out.stem + PART_SUFFIX)          # final name only once complete (panel cancel = kill -9)
    tmp_audio = S["rdir"] / f"audio_{stamp}.m4a"
    audio = None
    try:
        with emit.step("audio"):
            audio = R.build_audio(tl, S["t0"], S["t1"], tmp_audio, prog=emit.progress)
            if audio is None:
                emit.done_note(tr("resize.noAudio"))
        with emit.step("render"):
            used = [c for c in S["clips"] if c.end > S["t0"] and c.start < S["t1"]]
            if not used:
                raise EngineError("NO_VIDEO", tr("resize.noVideoScope"), tr("resize.noVideoScopeHint"))
            res = R.render_video(tl, used, shots, S["geoms"], part, S["t0"], S["t1"], fps=p["fps"], bg=p["bg"],
                                 audio=audio, prog=emit.progress)
            os.replace(part, out)
            res["path"] = str(out)
            emit.done_note(f"{res['codec']}, {secs(round(res['secs']))}")
    finally:
        try:
            tmp_audio.unlink()
        except OSError:
            pass
    label = TARGETS[p["target"]]["label"]
    size_mb = round(out.stat().st_size / 1024 ** 2, 1)
    name = f"{tl.name} ({p['target']} {tr('resize.seqFocus') if p['layout'] == 'focus' else 'crop'})"
    return {"path": str(out), "size_mb": size_mb, "render": res, "duration": round(dur, 3),
            "plan": {"kind": "import_render", "path": str(out), "name": name, "w": res["w"], "h": res["h"],
                     "fps": p["fps"]},
            "summary": tr("resize.renderSummary", fmt=label, dur=_fmt_sec(dur), mb=dec(size_mb, 1))}


ACTIONS = {"analyze": analyze, "apply": apply, "render": render}
