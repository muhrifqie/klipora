"""Interval algebra on lists of [t0, t1] (seconds, half-open [t0, t1)). Pure Python, no numpy.

All functions return NEW sorted, non-overlapping lists of [float, float] (lists, JSON friendly) unless noted.
Typical silence flow:  keep = invert(remove, 0, dur);  remove = subtract([[0, dur]], keep);
frame-safe removal:    to_frames(remove, fps, mode="inner")  (never eats a kept frame / consonant).
"""
from __future__ import annotations

import bisect
import math

EPS = 1e-9


def norm(ranges, min_len=0.0):
    """Sort, drop empty/negative, merge overlapping AND touching ranges."""
    rs = sorted([float(a), float(b)] for a, b in ranges if b - a > EPS)
    out = []
    for a, b in rs:
        if out and a <= out[-1][1] + EPS:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [r for r in out if r[1] - r[0] >= min_len - EPS] if min_len else out


merge = norm


def merge_gaps(ranges, max_gap):
    """Merge ranges separated by gaps < max_gap (e.g. bridge short pauses inside speech)."""
    out = []
    for a, b in norm(ranges):
        if out and a - out[-1][1] < max_gap:
            out[-1][1] = b
        else:
            out.append([a, b])
    return out


def union(*lists):
    return norm([r for lst in lists for r in lst])


def intersect(a, b):
    a, b = norm(a), norm(b)
    out, i, j = [], 0, 0
    while i < len(a) and j < len(b):
        lo, hi = max(a[i][0], b[j][0]), min(a[i][1], b[j][1])
        if hi - lo > EPS:
            out.append([lo, hi])
        if a[i][1] < b[j][1]:
            i += 1
        else:
            j += 1
    return out


def subtract(a, b):
    """a minus b."""
    b = norm(b)
    ends = [r[1] for r in b]
    out = []
    for lo, hi in norm(a):
        cur = lo
        k = bisect.bisect_left(ends, cur + EPS)
        while k < len(b) and b[k][0] < hi:
            if b[k][0] > cur + EPS:
                out.append([cur, b[k][0]])
            cur = max(cur, b[k][1])
            k += 1
        if hi - cur > EPS:
            out.append([cur, hi])
    return out


def invert(ranges, lo, hi):
    """Complement inside [lo, hi)."""
    return subtract([[lo, hi]], ranges)


def clamp(ranges, lo, hi):
    return intersect(ranges, [[lo, hi]])


def pad(ranges, before=0.0, after=0.0, lo=None, hi=None):
    """Grow each range (negative values shrink), clamp to [lo, hi], merge."""
    out = [[a - before, b + after] for a, b in ranges]
    if lo is not None or hi is not None:
        out = [[a if lo is None else max(lo, a), b if hi is None else min(hi, b)] for a, b in out]
    return norm(out)


def drop_short(ranges, min_len):
    return [r for r in norm(ranges) if r[1] - r[0] >= min_len - EPS]


def total(ranges):
    return sum(b - a for a, b in norm(ranges))


def contains(ranges, t):
    """True when t lies inside one of the (normalised) ranges."""
    rs = norm(ranges)
    k = bisect.bisect_right([r[0] for r in rs], t + EPS) - 1
    return k >= 0 and rs[k][0] - EPS <= t < rs[k][1]


def overlap(a0, a1, b0, b1):
    """Length of the overlap of two single ranges."""
    return max(0.0, min(a1, b1) - max(a0, b0))


def to_frames(ranges, fps, mode="inner"):
    """Snap to the frame grid (still seconds). mode: 'inner' shrinks (ceil start, floor end) - use it for
    REMOVED ranges so no kept audio is clipped; 'outer' grows (floor start, ceil end) - use it for KEPT
    ranges; 'nearest' rounds both. Ranges that vanish are dropped."""
    out = []
    for a, b in ranges:
        fa, fb = a * fps, b * fps
        if mode == "inner":
            ia, ib = math.ceil(fa - 1e-6), math.floor(fb + 1e-6)
        elif mode == "outer":
            ia, ib = math.floor(fa + 1e-6), math.ceil(fb - 1e-6)
        else:
            ia, ib = round(fa), round(fb)
        if ib > ia:
            out.append([ia / fps, ib / fps])
    return norm(out)


def snap_edges(ranges, snap):
    """Apply snap(t) -> t' to every edge (e.g. lambda t: media.snap_quiet(db, t, 0.04)). Re-normalised."""
    return norm([[snap(a), snap(b)] for a, b in ranges])


def ripple_mapper(removed):
    """t -> t' after ripple-deleting `removed` (times inside a removed range map to its start).
    Used to move markers / words / review items onto the cut sequence."""
    rem = norm(removed)
    starts = [a for a, _ in rem]
    cum = [0.0]
    for a, b in rem:
        cum.append(cum[-1] + (b - a))

    def m(t):
        i = bisect.bisect_right(starts, t) - 1
        if i < 0:
            return t
        a, b = rem[i]
        if t < b:
            return a - cum[i]
        return t - cum[i + 1]
    return m


def keep_to_remove(keep, lo, hi):
    return invert(keep, lo, hi)


def remove_to_keep(remove, lo, hi):
    return invert(remove, lo, hi)


def round_list(ranges, nd=3):
    return [[round(a, nd), round(b, nd)] for a, b in ranges]
