"""Timeline model built from the host JSON (docs/SPEC.md section 3, `bac_seqInfo()`), all times in seconds.

    tl = Timeline.from_json(job["seq"])          # or Timeline.from_media(path) for offline tests
    for c in tl.audio_clips():                   # unmuted tracks, enabled clips
        src = tl.seq_to_src(c, 12.0)
    words = tl.words_on_timeline()               # transcript (cache v3) mapped to sequence time
    db = tl.envelope_on_timeline()               # max dB over the audio tracks, 10 ms frames, sequence time

Tolerant input: clips may use path|media, in|inPoint, out|outPoint, nodeId; tracks index|idx; sequence
duration|endSec, inPoint|inSec, outPoint|outSec (-400000 or null = unset), player|playerSec, speed (default 1).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path

from .i18n import tr
from .util import EngineError, file_hash, read_json

UNSET = -400000


@dataclass
class Clip:
    kind: str                    # "video" | "audio"
    track: int                   # track index (0 = V1/A1)
    index: int                   # clip index inside the track
    name: str
    path: str | None             # media path ("" / None for graphics, nests, generators)
    start: float                 # sequence seconds
    end: float
    src_in: float                # source (media) seconds
    src_out: float
    speed: float = 1.0
    disabled: bool = False
    selected: bool = False
    node_id: str | None = None
    raw: dict = field(default_factory=dict, repr=False)

    @property
    def dur(self):
        return self.end - self.start

    @property
    def has_media(self):
        return bool(self.path) and Path(self.path).suffix.lower() not in ("", ".prproj", ".mogrt", ".aegraphic")

    def seq_to_src(self, t):
        return self.src_in + (t - self.start) * self.speed

    def src_to_seq(self, s):
        return self.start + (s - self.src_in) / (self.speed or 1.0)

    def contains(self, t):
        return self.start - 1e-9 <= t < self.end - 1e-9

    def to_json(self):
        return {"name": self.name, "path": self.path, "start": self.start, "end": self.end, "in": self.src_in,
                "out": self.src_out, "speed": self.speed, "disabled": self.disabled, "selected": self.selected,
                "nodeId": self.node_id}


@dataclass
class Track:
    kind: str
    index: int
    name: str
    muted: bool = False
    locked: bool = False
    clips: list = field(default_factory=list)

    def to_json(self):
        return {"index": self.index, "name": self.name, "muted": self.muted, "locked": self.locked,
                "clips": [c.to_json() for c in self.clips]}


def seq_to_src(clip, t):
    return clip.seq_to_src(t)


def src_to_seq(clip, s):
    return clip.src_to_seq(s)


def _num(v, default=None):
    try:
        f = float(v)
        return default if math.isnan(f) else f
    except (TypeError, ValueError):
        return default


def _point(v):
    f = _num(v)
    return None if f is None or f <= UNSET + 1 or f < 0 else f


class Timeline:
    def __init__(self, data):
        d = data or {}
        self.raw = d
        self.id = d.get("id")
        self.name = d.get("name") or "Sequence"
        self.fps = _num(d.get("fps"), 0.0) or (254016000000 / _num(d.get("timebase"), 2116800000.0))
        self.width = int(_num(d.get("width"), 1920))
        self.height = int(_num(d.get("height"), 1080))
        self.in_point = _point(d.get("inPoint", d.get("inSec")))
        self.out_point = _point(d.get("outPoint", d.get("outSec")))
        self.player = _num(d.get("player", d.get("playerSec")), 0.0)
        self.markers = d.get("markers") if isinstance(d.get("markers"), list) else []
        self.video = [self._track("video", t, k) for k, t in enumerate(d.get("video") or [])]
        self.audio = [self._track("audio", t, k) for k, t in enumerate(d.get("audio") or [])]
        ends = [c.end for t in self.video + self.audio for c in t.clips]
        self.duration = _num(d.get("duration", d.get("endSec")), 0.0) or (max(ends) if ends else 0.0)

    # ------------------------------------------------------------ construction
    @staticmethod
    def _track(kind, t, k):
        idx = int(_num(t.get("index", t.get("idx")), k))
        tr = Track(kind, idx, t.get("name") or f"{kind[0].upper()}{idx + 1}", bool(t.get("muted")),
                   bool(t.get("locked")))
        for i, c in enumerate(t.get("clips") or []):
            path = c.get("path", c.get("media")) or None
            tr.clips.append(Clip(kind, idx, i, c.get("name") or (Path(path).name if path else ""), path,
                                 _num(c.get("start"), 0.0), _num(c.get("end"), 0.0),
                                 _num(c.get("in", c.get("inPoint")), 0.0), _num(c.get("out", c.get("outPoint")), 0.0),
                                 _num(c.get("speed"), 1.0) or 1.0, bool(c.get("disabled")), bool(c.get("selected")),
                                 c.get("nodeId"), c))
        tr.clips.sort(key=lambda c: c.start)
        return tr

    @classmethod
    def from_json(cls, data):
        if not isinstance(data, dict) or not (data.get("video") or data.get("audio")):
            raise EngineError("NO_SEQ", tr("err.noSeq.msg"), tr("err.noSeq.hint"))
        return cls(data)

    @classmethod
    def from_file(cls, path):
        return cls.from_json(read_json(path))

    @classmethod
    def from_media(cls, path, name=None):
        """One clip of the whole file on V1 + A1 (offline tests, legacy single-file flows)."""
        from .media import probe
        info = probe(path)
        dur = info["duration"]
        clip = {"name": Path(path).name, "path": str(path), "start": 0.0, "end": dur, "in": 0.0, "out": dur}
        return cls({"id": "media:" + file_hash(path), "name": name or Path(path).stem, "fps": info["fps"],
                    "width": info["width"] or 1920, "height": info["height"] or 1080, "duration": dur,
                    "video": [{"index": 0, "name": "V1", "clips": [dict(clip)]}] if info["has_video"] else [],
                    "audio": [{"index": 0, "name": "A1", "clips": [dict(clip)]}] if info["has_audio"] else []})

    def to_json(self):
        return {"id": self.id, "name": self.name, "fps": self.fps, "width": self.width, "height": self.height,
                "duration": self.duration, "inPoint": self.in_point, "outPoint": self.out_point,
                "player": self.player, "video": [t.to_json() for t in self.video],
                "audio": [t.to_json() for t in self.audio], "markers": self.markers}

    # ------------------------------------------------------------ queries
    def tracks(self, kind):
        return self.video if kind == "video" else self.audio

    def clips(self, kind=None, tracks=None, include_muted=True, include_disabled=False):
        """Clips in track order then time. tracks = iterable of track indexes (None = all)."""
        out = []
        for k in (("video", "audio") if kind is None else (kind,)):
            for tr in self.tracks(k):
                if tracks is not None and tr.index not in tracks:
                    continue
                if tr.muted and not include_muted:
                    continue
                out += [c for c in tr.clips if include_disabled or not c.disabled]
        return out

    def audio_clips(self, include_muted=False, tracks=None, include_disabled=False):
        """Audio clips that are heard: unmuted tracks, enabled clips (by default) with a media path."""
        return [c for c in self.clips("audio", tracks, include_muted, include_disabled) if c.has_media]

    def video_clips(self, tracks=None, include_disabled=False):
        return self.clips("video", tracks, True, include_disabled)

    def clip_at(self, t, kind="video", tracks=None, include_disabled=False):
        """Clip under sequence time t: topmost video track wins (V3 over V1); audio = lowest track."""
        trs = self.tracks(kind)
        order = sorted(trs, key=lambda tr: tr.index, reverse=(kind == "video"))
        for tr in order:
            if tracks is not None and tr.index not in tracks:
                continue
            for c in tr.clips:
                if c.contains(t) and (include_disabled or not c.disabled):
                    return c
        return None

    def seq_to_src(self, clip, t):
        return clip.seq_to_src(t)

    def src_to_seq(self, clip, s):
        return clip.src_to_seq(s)

    def media_paths(self, kind="audio", include_muted=False):
        seen = []
        cl = self.audio_clips(include_muted) if kind == "audio" else [c for c in self.video_clips() if c.has_media]
        for c in cl:
            if c.path not in seen:
                seen.append(c.path)
        return seen

    def frame(self, t):
        return int(round(t * self.fps))

    def scope_ranges(self, scope="all"):
        """Sequence ranges a tool should work on: 'all' -> [[0, duration]]; 'inout' -> [[in, out]] (falls
        back to all when unset); 'selected' -> union of selected clips (falls back to all when none).
        `scope` may also be the panel's params.scope dict {"kind": "all"|"inout"|"selected", "t0", "t1"}
        (t0/t1 win over the sequence In/Out) or None (= all)."""
        from .ranges import norm
        t0 = t1 = None
        if isinstance(scope, dict):
            t0, t1 = _num(scope.get("t0")), _num(scope.get("t1"))
            scope = scope.get("kind") or "all"
        if scope == "inout":
            a = t0 if t0 is not None else self.in_point
            b = t1 if t1 is not None else self.out_point
            if a is not None or b is not None:
                a = max(0.0, a or 0.0)
                b = min(b, self.duration) if b is not None and b > a else self.duration
                return [[a, b]]
        if scope == "selected":
            sel = [[c.start, c.end] for c in self.clips(include_disabled=True) if c.selected]
            if not sel:   # host `selection` list (bac_seqInfo full) when clip flags are missing
                sel = [[_num(s.get("start"), 0.0), _num(s.get("end"), 0.0)] for s in self.raw.get("selection") or []
                       if isinstance(s, dict)]
            sel = [r for r in sel if r[1] > r[0]]
            if sel:
                return norm(sel)
        return [[0.0, self.duration]]

    # ------------------------------------------------------------ words / envelope
    def words_on_timeline(self, tracks=None, include_muted=False, transcribe=True, emit=None):
        """Transcript words of every heard audio clip, in SEQUENCE time, sorted by t0.

        A word belongs to a clip when its source midpoint is inside [src_in, src_out); its edges are clamped
        to the clip. transcribe=False uses only existing caches (no GPU job; media without a cache are skipped).
        Duplicates (same word id at the same time from stereo pairs on A1+A2) are dropped.
        -> [{"text", "t0", "t1", "seg", "p", "id": "<hash10>:<word idx>", "src", "s0", "s1", "track", "clip",
             "clip_end": bool (last word inside its clip)}]"""
        from . import transcript as T
        clips = self.audio_clips(include_muted, tracks)
        paths = list(dict.fromkeys(c.path for c in clips if Path(c.path).is_file()))
        by_path = {}
        for k, p in enumerate(paths):
            if transcribe:
                sub = None
                if emit is not None:
                    base, share = 100.0 * k / len(paths), 100.0 / len(paths)
                    sub = _SubEmit(emit, base, share)
                by_path[p] = (T.words(p, emit=sub), file_hash(p)[:10])
            else:
                w = T.cached_words(p)
                if w is not None:
                    by_path[p] = (w, file_hash(p)[:10])
        out, seen = [], set()
        for c in clips:
            if c.path not in by_path:
                continue
            rows, h = by_path[c.path]
            lo, hi = min(c.src_in, c.src_out), max(c.src_in, c.src_out)
            mine = []
            for i, r in enumerate(rows):
                s0, s1 = float(r[1]), float(r[2])
                if s1 < lo - 5:
                    continue
                if s0 > hi + 5:
                    break
                if lo <= (s0 + s1) / 2 < hi:
                    t0 = c.src_to_seq(max(s0, lo))
                    t1 = max(c.src_to_seq(min(s1, hi)), t0 + 0.01)
                    key = (h, i, round(t0, 3))
                    if key in seen:
                        continue
                    seen.add(key)
                    mine.append({"text": r[0], "t0": round(t0, 3), "t1": round(t1, 3), "seg": int(r[3]),
                                 "p": float(r[4]) if len(r) > 4 else 1.0, "id": f"{h}:{i}", "src": c.path,
                                 "s0": s0, "s1": s1, "track": c.track, "clip": c.index, "clip_end": False})
            if mine:
                mine[-1]["clip_end"] = True
            out += mine
        out.sort(key=lambda w: (w["t0"], w["t1"]))
        return out

    def envelope_on_timeline(self, tracks=None, include_muted=False, emit=None):
        """Loudness in SEQUENCE time: 10 ms frames, max dB over every included audio clip (source envelopes
        are cached per media). Frames with no audio = media.FLOOR_DB. -> float32 array, len ceil(dur/HOP)."""
        import numpy as np
        from . import media as M
        n = int(math.ceil(self.duration / M.HOP))
        out = np.full(n, M.FLOOR_DB, np.float32)
        clips = [c for c in self.audio_clips(include_muted, tracks) if Path(c.path).is_file()]
        envs = {}
        for k, c in enumerate(clips):
            if c.path not in envs:
                envs[c.path] = M.envelope(c.path)
            env = envs[c.path]
            a, b = max(0, int(round(c.start / M.HOP))), min(n, int(round(c.end / M.HOP)))
            if b <= a or len(env) == 0:
                continue
            ks = np.arange(a, b)
            si = np.floor((c.src_in + (ks * M.HOP - c.start) * c.speed) / M.HOP + 1e-6).astype(np.int64)
            ok = (si >= 0) & (si < len(env))
            out[ks[ok]] = np.maximum(out[ks[ok]], env[si[ok]])
            if emit is not None:
                emit.progress(100.0 * (k + 1) / len(clips))
        return out


class _SubEmit:
    """Maps a nested helper's 0..100 progress onto base..base+share of the caller's stage."""

    def __init__(self, emit, base, share):
        self._e, self._b, self._s = emit, base, share

    def progress(self, pct, note=None, force=False):
        self._e.progress(self._b + self._s * max(0.0, min(100.0, pct)) / 100, note=note, force=force)

    def __getattr__(self, name):
        return getattr(self._e, name)
