"""Camera path math for Auto Resize (helper of ac.tools.resize; pure numpy, no I/O).

A shot is a stretch of the timeline the virtual camera may pan inside but never across (clip boundaries, page
changes, speaker switches): the camera CUTS between shots. Per shot and axis:
  * STATIC when the target spread is small, the shot is short or the speed is "none" (AutoFlip rule), with
    cross-cut hysteresis (a small shift at a jump cut keeps the previous framing instead of a 20 px jerk);
  * otherwise the HYBRID virtual camera operator from proto/vision/reframe.py (verified in docs/research/vision.md
    4.2: same subject coverage as Kalman/One-Euro, camera moving in ~25 % of frames, fewest keyframes):
    dead-zone hold, anticipating eased glides, Kalman/RTS follow only during sustained motion (faces only);
  * then Ramer-Douglas-Peucker keyframe reduction.
Speaker choice for several faces: mouth-motion energy + Viterbi with a switch penalty (offline), switch = hard cut.
"""
from __future__ import annotations

import math

import numpy as np

# Speed presets (product_visual.md 4.2 table + vision.md 4.2 operator defaults). dz = dead zone (fraction of the
# crop width), hold = ignore excursions shorter than this (s), look = glide target = median of the next `look` s,
# lead = start gliding this much earlier, vmax = glide speed (source widths / s), dmin/dmax = glide duration,
# spread = static when the 5..95 % target spread fits in spread * crop, hyst = keep the previous framing at a cut
# when the new one is closer than hyst * crop.
SPEEDS = {
    "slow":   {"dz": 0.30, "hold": 0.8, "look": 1.2, "lead": 0.5, "vmax": 0.25, "dmin": 0.8, "dmax": 1.8,
               "spread": 0.60, "hyst": 0.40, "min_track": 1.5},
    "normal": {"dz": 0.20, "hold": 0.5, "look": 0.8, "lead": 0.35, "vmax": 0.45, "dmin": 0.5, "dmax": 1.2,
               "spread": 0.50, "hyst": 0.35, "min_track": 1.0},
    "fast":   {"dz": 0.10, "hold": 0.25, "look": 0.5, "lead": 0.2, "vmax": 0.90, "dmin": 0.3, "dmax": 0.8,
               "spread": 0.35, "hyst": 0.25, "min_track": 0.8},
}
SPEEDS["none"] = dict(SPEEDS["normal"], static=True)


def speed_params(speed, kind="screen"):
    """Preset for a content kind. Screen recordings get a larger dead zone and hold (vision.md 6.3: the 34.6 min
    tutorial was still busy at the face defaults)."""
    p = dict(SPEEDS.get(speed) or SPEEDS["normal"])
    if kind == "screen":
        p["dz"] = min(0.4, p["dz"] * 1.25)
        p["hold"] += 0.3
    return p


# ---------------------------------------------------------------- smoothers (port of proto/vision/reframe.py)

def ease(u):
    return u * u * (3 - 2 * u)            # smoothstep


def smooth_kalman(ts, xs, q=0.02, r=0.0004):
    """Constant-velocity Kalman forward pass + Rauch-Tung-Striebel backward smoother (offline, zero lag).
    xs may contain NaN (no measurement)."""
    n = len(ts)
    X = np.zeros((n, 2))
    P = np.zeros((n, 2, 2))
    Xp, Pp = np.zeros((n, 2)), np.zeros((n, 2, 2))
    first = next((x for x in xs if not np.isnan(x)), 0.5)
    x, p = np.array([first, 0.0]), np.diag([0.01, 0.01])
    Hm = np.array([[1.0, 0.0]])
    Fs = []
    for i in range(n):
        dt = ts[i] - ts[i - 1] if i else 0.0
        F = np.array([[1, dt], [0, 1]])
        Q = q * np.array([[dt ** 3 / 3, dt ** 2 / 2], [dt ** 2 / 2, dt]])
        x, p = F @ x, F @ p @ F.T + Q
        Xp[i], Pp[i] = x, p
        Fs.append(F)
        if not np.isnan(xs[i]):
            S = (Hm @ p @ Hm.T)[0, 0] + r
            K = (p @ Hm.T)[:, 0] / S
            x = x + K * (xs[i] - x[0])
            p = p - np.outer(K, Hm @ p)
        X[i], P[i] = x, p
    for i in range(n - 2, -1, -1):
        C = P[i] @ Fs[i + 1].T @ np.linalg.inv(Pp[i + 1])
        X[i] = X[i] + C @ (X[i + 1] - Xp[i + 1])
        P[i] = P[i] + C @ (P[i + 1] - Pp[i + 1]) @ C.T
    return X[:, 0]


def follow_mask(ts, k, v_on=0.03, v_off=0.015, min_dur=1.0, min_travel=0.05):
    """Sustained-motion detector on the smoothed path (|dk/dt| > v_on source widths/s with hysteresis), kept only
    when it lasts >= min_dur s AND travels >= min_travel, so swaying / gesturing never switches to follow mode."""
    v = np.abs(np.gradient(k, ts)) if len(ts) > 1 else np.zeros(len(ts))
    m = np.zeros(len(ts), bool)
    on = False
    for i, vi in enumerate(v):
        on = vi > v_on or (on and vi > v_off)
        m[i] = on
    i = 0
    while i < len(m):
        if m[i]:
            j = i
            while j < len(m) and m[j]:
                j += 1
            if ts[j - 1] - ts[i] < min_dur or abs(k[j - 1] - k[i]) < min_travel:
                m[i:j] = False
            i = j
        else:
            i += 1
    return m


def camera_path(ts, xs, cw, follow=None, k=None, dz=0.18, hold=0.4, lead=0.35, look=0.8, vmax=0.5,
                dmin=0.45, dmax=1.2, blend=0.5, **_):
    """Virtual camera operator on a dense grid (offline). Returns the camera centre per grid time.
      HOLD   camera still while the subject stays within dz*cw of the centre (no micro-jitter at all);
      GLIDE  subject outside the dead zone for >= hold s -> smoothstep move to where the subject WILL be (median of
             the next `look` s), starting `lead` s early (look-ahead: never hits the edge);
      FOLLOW where follow[i] (sustained motion) track the Kalman/RTS path k, blending in over `blend` s."""
    ts, xs = np.asarray(ts, float), np.asarray(xs, float)
    n = len(ts)
    lo, hi = cw / 2, 1 - cw / 2

    def clamp(v):
        return float(min(max(v, lo), hi))

    out = np.empty(n)
    c = clamp(np.median(xs[ts <= ts[0] + look]))
    free_from = 0
    i = 0
    while i < n:
        if follow is not None and follow[i]:
            j = i
            while j < n and follow[j]:
                j += 1
            c0, t0 = c, ts[i]
            for m in range(i, j):
                u = ease(min(1.0, (ts[m] - t0) / blend))
                out[m] = (1 - u) * c0 + u * clamp(k[m])
            c = float(out[j - 1])
            free_from = i = j
            continue
        out[i] = c
        if abs(xs[i] - c) <= dz * cw:
            i += 1
            continue
        side = np.sign(xs[i] - c)
        j = i
        while j < n and np.sign(xs[j] - c) == side and abs(xs[j] - c) > 0.5 * dz * cw:
            j += 1
        if j < n and ts[j] - ts[i] < hold:
            i += 1                              # transient (head turn, cursor flick) -> ignore
            continue
        new = clamp(np.median(xs[(ts >= ts[i]) & (ts <= ts[i] + look)]))
        if abs(new - c) < 0.25 * dz * cw:
            i += 1
            continue
        dur = min(dmax, max(dmin, abs(new - c) / vmax))
        s_idx = max(free_from, int(np.searchsorted(ts, ts[i] - lead)))
        t_s = ts[s_idx]
        m = s_idx
        while m < n and ts[m] <= t_s + dur:
            out[m] = c + (new - c) * ease((ts[m] - t_s) / dur)
            m += 1
        c = new
        free_from = i = max(m, i + 1)
    return out


def rdp(points, eps):
    """Ramer-Douglas-Peucker on [(t, x)] (x units), iterative (long shots would overflow the recursion)."""
    n = len(points)
    if n < 3:
        return list(points)
    pts = np.asarray(points, float)
    keep = np.zeros(n, bool)
    keep[0] = keep[-1] = True
    stack = [(0, n - 1)]
    while stack:
        a, b = stack.pop()
        if b - a < 2:
            continue
        t0, x0 = pts[a]
        t1, x1 = pts[b]
        seg = pts[a + 1:b]
        xi = x0 + (x1 - x0) * (seg[:, 0] - t0) / (t1 - t0 + 1e-12)
        d = np.abs(seg[:, 1] - xi)
        k = int(np.argmax(d))
        if d[k] > eps:
            m = a + 1 + k
            keep[m] = True
            stack.append((a, m))
            stack.append((m, b))
    return [tuple(p) for p in pts[keep].tolist()]


def smooth_operator(ts, xs, cw, hybrid=True, grid=0.1, eps=1.0 / 1920, **kw):
    """Resample to a `grid` s lattice, run camera_path (optionally hybrid follow), reduce with RDP.
    -> keyframes [(t, x)]."""
    ts, xs = np.asarray(ts, float), np.asarray(xs, float)
    g = np.arange(ts[0], ts[-1] + 1e-9, grid) if len(ts) > 1 else ts.copy()
    if len(g) < 2:
        return [(float(ts[0]), float(xs[0]))]
    xg = np.interp(g, ts, xs)
    k = f = None
    if hybrid and len(g) > 3:
        k = smooth_kalman(g, xg)
        f = follow_mask(g, k)
    path = camera_path(g, xg, cw, follow=f, k=k, **kw)
    return rdp(list(zip(g.tolist(), path.tolist())), eps)


def fill_gaps(ts, xs, max_interp=1.5):
    """Linear interpolation across gaps <= max_interp s, hold at the ends; longer gaps hold the last value."""
    xs = np.array(xs, float)
    ok = ~np.isnan(xs)
    if not ok.any():
        return xs
    idx = np.where(ok)[0]
    xs[:idx[0]] = xs[idx[0]]
    xs[idx[-1]:] = xs[idx[-1]]
    for a, b in zip(idx, idx[1:]):
        if b - a > 1:
            if ts[b] - ts[a] <= max_interp:
                xs[a:b + 1] = np.interp(ts[a:b + 1], [ts[a], ts[b]], [xs[a], xs[b]])
            else:
                xs[a + 1:b] = xs[a]
    return xs


def median3(xs):
    if len(xs) < 3:
        return xs
    p = np.pad(xs, 1, mode="edge")
    return np.median(np.stack([p[:-2], p[1:-1], p[2:]]), axis=0)


# ---------------------------------------------------------------- per shot

def plan_axis(ts, xs, s0, s1, c, sp, kind, prev, eps):
    """Keyframes [[t, v]] for one axis of one shot (v = normalised source centre of the window, window size c).
    -> (keys, mode) mode in static | track | hold | full."""
    if c >= 0.999:
        return [[s0, 0.5]], "full"
    lo, hi = c / 2, 1 - c / 2
    prev = float(min(max(prev, lo), hi))
    xs = np.asarray(xs, float)
    if not len(xs) or np.isnan(xs).all():
        return [[s0, prev]], "hold"
    xs = fill_gaps(ts, xs, max_interp=1.5 if kind == "face" else 0.0)
    xs = np.clip(median3(xs), lo, hi)
    spread = float(np.percentile(xs, 95) - np.percentile(xs, 5))
    if sp.get("static") or s1 - s0 < sp["min_track"] or spread <= sp["spread"] * c or len(xs) < 4:
        v = float(np.median(xs))
        if abs(v - prev) < sp["hyst"] * c:
            v = prev
        return [[s0, round(v, 5)]], "static"
    keys = smooth_operator(ts, xs, c, hybrid=(kind == "face"), eps=eps, **sp)
    out = []
    for t, v in keys:
        t = min(max(s0, t), s1)
        v = round(float(min(max(v, lo), hi)), 5)
        if out and t - out[-1][0] < 1e-4:
            out[-1][1] = v
            continue
        out.append([round(t, 4), v])
    if out[0][0] > s0 + 1e-4:
        out.insert(0, [round(s0, 4), out[0][1]])
    if abs(out[0][1] - prev) < sp["hyst"] * c * 0.5 and all(abs(v - out[0][1]) < 1e-9 for _, v in out):
        return [[s0, prev]], "static"
    if all(abs(v - out[0][1]) < 1e-9 for _, v in out):
        return [out[0]], "static"
    return out, "track"


def value_at(keys, t):
    """Linear interpolation of [[t, v]] keys, hold outside."""
    if len(keys) == 1 or t <= keys[0][0]:
        return keys[0][1]
    if t >= keys[-1][0]:
        return keys[-1][1]
    ks = np.asarray(keys, float)
    return float(np.interp(t, ks[:, 0], ks[:, 1]))


# ---------------------------------------------------------------- faces -> target (port of proto plan logic)

def viterbi_speaker(ids, score, present, fps, switch_s=0.8):
    """Offline active-speaker segmentation: cost = 1 - score (+2 when that face is absent) and a switch penalty
    worth `switch_s` seconds of evidence. Returns the chosen track id per sample."""
    n, K = len(score), len(ids)
    pen = switch_s * fps
    cost = np.array([[1 - score[i][k] + (0 if present[i][k] else 2) for k in range(K)] for i in range(n)])
    D = cost[0].copy()
    back = np.zeros((n, K), int)
    for i in range(1, n):
        sw = D.min() + pen
        arg = int(D.argmin())
        back[i] = np.where(D <= sw, np.arange(K), arg)
        D = np.minimum(D, sw) + cost[i]
    path = [int(D.argmin())]
    for i in range(n - 1, 0, -1):
        path.append(int(back[i][path[-1]]))
    return [ids[k] for k in path[::-1]]


def choose_target(samples, cw, mode, fps):
    """samples: [(t, rows)] (rows = 16-value face rows, normalised). -> (ts, xs with NaN, ys, who, n_tracks, mode).
    auto: 1 track -> follow it; several that fit 85 % of the crop -> frame the group; else active speaker."""
    from ._resize_vision import track
    ts = np.array([s[0] for s in samples], float)
    xs = np.full(len(ts), np.nan)
    ys = np.full(len(ts), np.nan)
    who = [None] * len(ts)
    if not len(ts):
        return ts, xs, ys, who, 0, mode
    trs = track([(t, [r[:4] for r in rows]) for t, rows in samples], max_gap=1.0)
    dur = (ts[-1] - ts[0]) + 1 / fps
    keep = [trk for trk in trs if len(trk["t"]) / fps >= min(1.0, 0.3 * dur)]       # kill flicker / FP tracks
    if not keep:
        return ts, xs, ys, who, 0, mode
    look, energy = {}, {}
    for trk in keep:
        for t, b in zip(trk["t"], trk["box"]):
            look[(trk["id"], round(t, 4))] = b
    for t, rows in samples:
        for r in rows:
            for trk in keep:
                b = look.get((trk["id"], round(t, 4)))
                if b is not None and abs(b[0] - r[0]) < 1e-6 and abs(b[1] - r[1]) < 1e-6 and r[15] is not None:
                    energy[(trk["id"], round(t, 4))] = r[15]
    area = {trk["id"]: float(np.median([b[2] * b[3] for b in trk["box"]])) for trk in keep}
    largest = max(area, key=area.get)
    m = mode
    if m == "auto":
        if len(keep) == 1:
            m = "largest"
        else:
            boxes = [b for trk in keep for b in trk["box"]]
            span = max(b[0] + b[2] for b in boxes) - min(b[0] for b in boxes)
            m = "group" if span <= 0.85 * cw else "speaker"
    ids = [trk["id"] for trk in keep]
    chosen = [None] * len(ts)
    if m == "speaker" and len(ids) > 1:
        half = max(1, int(round(0.3 * fps)))
        raw = np.array([[energy.get((k, round(t, 4)), 0.0) or 0.0 for k in ids] for t in ts])
        sm = np.array([raw[max(0, i - half):i + half + 1].mean(0) for i in range(len(ts))])
        score = sm / (sm.max(1, keepdims=True) + 1e-6)
        present = [[(k, round(t, 4)) in look for k in ids] for t in ts]
        chosen = viterbi_speaker(ids, score, present, fps)
    for i, t in enumerate(ts):
        here = [k for k in ids if (k, round(t, 4)) in look]
        if not here:
            continue
        if m == "group":
            bs = [look[(k, round(t, 4))] for k in here]
            x0, x1 = min(b[0] for b in bs), max(b[0] + b[2] for b in bs)
            y0, y1 = min(b[1] for b in bs), max(b[1] + b[3] for b in bs)
            xs[i], ys[i], who[i] = (x0 + x1) / 2, (y0 + y1) / 2, "group"
            continue
        k = chosen[i] if chosen[i] in here else (largest if largest in here else max(here, key=area.get))
        b = look[(k, round(t, 4))]
        xs[i], ys[i], who[i] = b[0] + b[2] / 2, b[1] + b[3] * 0.45, k     # eyes a bit above the box centre
    return ts, xs, ys, who, len(keep), m


def speaker_bounds(who):
    """Indexes where the active subject changes (a speaker switch = internal hard cut)."""
    out, last = [0], None
    for i, w in enumerate(who):
        if w is None:
            continue
        if last is not None and w != last:
            out.append(i)
        last = w
    return out


def merge_close(times, min_gap):
    out = []
    for c in sorted(times):
        if not out or c - out[-1] >= min_gap:
            out.append(c)
    return out


def finite(v, default=0.5):
    return default if v is None or (isinstance(v, float) and math.isnan(v)) else v
