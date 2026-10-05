"""JSON-lines progress events (docs/SPEC.md section 2), one object per stdout line:

  {"ev":"stage","id":"transcribe","stage":"transcribe","label":"Transcribe","i":1,"n":4,"w":0.5}
                                                     i is 0-based, n = stage count, w = weight (sums to 1)
  {"ev":"progress","pct":42.5,"all":61.2}          pct within the current stage; all = whole job (when known)
  {"ev":"progress","pct":0,"note":"Waiting for GPU (transcribe) 12 s"}          status text while waiting
  {"ev":"stage_done","id":"transcribe","stage":"transcribe","sec":2.1,"note":"from cache"}
  {"ev":"log","msg":"..."}   {"ev":"warn","msg":"AI offline, using rules"}
Labels, notes, warnings and error texts are in the job's UI language: build them with ac.i18n.tr at run time.
  {"ev":"result","data":{...}}                       exactly one, written by cli/worker from the action's return value
  {"ev":"error","code":"NO_AUDIO","msg":"...","hint":"..."}
Worker mode wraps every event with the request id: {"id": 7, "ev": "stage", "stage": "transcribe", ...}
(the request id wins over the stage id, so read stage ids from `stage`).

Tool actions receive an Emitter as `emit`:

    emit.plan([("audio", tr("stage.audio"), 0.1), ("transcribe", tr("stage.transcribe"), 0.6),
               ("plan", tr("stage.cutPlan"), 0.3)])
    with emit.step("audio"):          # stage event, then stage_done with measured sec
        ...
        emit.progress(50)             # 0..100 inside the step (throttled; raises Cancelled when cancelled)
    emit.done_note(tr("note.cached")) # note for the stage_done of the current step
    emit.warn(tr("..."))
    return {"review": path, ...}      # the action's return value becomes {"ev":"result","data":...}
"""
from __future__ import annotations

import contextlib
import json
import sys
import threading
import time

from .util import Cancelled, redact


class Emitter:
    """Writes events through `sink(dict)` (default: one JSON line on stdout, flushed).
    `wrap` adds fields to every event (worker: {"id": request id}). Thread-safe."""

    def __init__(self, sink=None, wrap=None, min_interval=0.1):
        self._sink = sink or _stdout_sink
        self._wrap = dict(wrap or {})
        self._lock = threading.Lock()
        self._plan = []                # [(id, label, weight)]
        self._cur = None               # current stage dict
        self._done_w = 0.0             # weight of finished stages
        self._last_pct = (-1.0, 0.0)   # (pct, time) for throttling
        self._min_interval = min_interval
        self.cancel_event = threading.Event()
        self.events = 0

    # ------------------------------------------------------------ raw
    def __call__(self, ev, **fields):
        """Raw event: emit("log", msg="...")."""
        obj = {"ev": ev, **fields}
        if ev in ("stage", "stage_done") and "id" in fields:
            obj["stage"] = fields["id"]          # always readable as ev.stage (worker: "id" = request id)
        if self._wrap:  # wrap fields first and winning: {"id": <request id>, "ev": ...}
            obj = {**self._wrap, **{k: v for k, v in obj.items() if k not in self._wrap}}
        with self._lock:
            self.events += 1
            self._sink(obj)

    # ------------------------------------------------------------ cancel
    @property
    def cancelled(self):
        return self.cancel_event.is_set()

    def cancel(self):
        self.cancel_event.set()

    def check_cancel(self):
        if self.cancel_event.is_set():
            raise Cancelled()

    # ------------------------------------------------------------ stages
    def plan(self, stages):
        """Declare the stages up front: [(id, label) | (id, label, weight)]. Weights are normalised;
        they let the panel show one overall bar (`all` in progress events)."""
        rows = [(s[0], s[1], float(s[2]) if len(s) > 2 else 1.0) for s in stages]
        tot = sum(w for _, _, w in rows) or 1.0
        self._plan = [(i, lab, w / tot) for i, lab, w in rows]
        self._done_w = 0.0

    def stage(self, id, label=None, i=None, n=None, w=None, sub=None):
        """Start a stage. With a plan, only `id` is needed (label/i/n/w filled in)."""
        self.check_cancel()
        idx = next((k for k, s in enumerate(self._plan) if s[0] == id), None)
        if idx is not None:
            label = label or self._plan[idx][1]
            i = idx if i is None else i
            n = len(self._plan) if n is None else n
            w = round(self._plan[idx][2], 4) if w is None else w
        if self._cur is not None and not self._cur.get("closed"):
            self._close(None)
        self._cur = {"id": id, "t0": time.time(), "w": w or 0.0, "note": None}
        ev = {"id": id, "label": label or id}
        for k, v in (("i", i), ("n", n), ("w", w), ("sub", sub)):
            if v is not None:
                ev[k] = v
        self._last_pct = (-1.0, 0.0)
        self("stage", **ev)

    def done_note(self, note):
        """Set the note shown on the current stage's stage_done (e.g. tr("note.cached"))."""
        if self._cur is not None:
            self._cur["note"] = note

    def stage_done(self, id=None, sec=None, note=None):
        if self._cur is None:
            self("stage_done", id=id, sec=round(sec or 0.0, 2), **({"note": note} if note else {}))
            return
        if note:
            self._cur["note"] = note
        self._close(sec)

    def _close(self, sec):
        cur = self._cur
        cur["closed"] = True
        self._done_w += cur["w"] or 0.0
        ev = {"id": cur["id"], "sec": round(time.time() - cur["t0"] if sec is None else sec, 2)}
        if cur.get("note"):
            ev["note"] = cur["note"]
        self("stage_done", **ev)

    @contextlib.contextmanager
    def step(self, id, label=None, note=None, **kw):
        """`with emit.step("audio"): ...` = stage + stage_done(sec). Errors propagate (no stage_done)."""
        self.stage(id, label, **kw)
        if note:
            self.done_note(note)
        yield self
        if self._cur is not None and self._cur["id"] == id and not self._cur.get("closed"):
            self._close(None)

    # ------------------------------------------------------------ progress
    def progress(self, pct, note=None, force=False):
        """Progress inside the current stage, 0..100. Throttled to ~10 events/s and 0.5 % steps.
        Also the cooperative cancel point: raises Cancelled when the job was cancelled."""
        self.check_cancel()
        pct = max(0.0, min(100.0, float(pct)))
        last, t = self._last_pct
        now = time.time()
        if not force and note is None and pct < 100 and (abs(pct - last) < 0.5 or now - t < self._min_interval):
            return
        self._last_pct = (pct, now)
        ev = {"pct": round(pct, 1)}
        if self._plan and self._cur is not None:
            ev["all"] = round(100 * (self._done_w + (self._cur["w"] or 0.0) * pct / 100), 1)
        if note:
            ev["note"] = note
        self("progress", **ev)

    def sub(self, lo, hi):
        """Callable mapping 0..100 onto lo..hi of the current stage (for nested helpers)."""
        return lambda p: self.progress(lo + (hi - lo) * max(0.0, min(100.0, p)) / 100)

    def wait(self, note):
        """Waiting status (GPU queue, etc.) without moving the bar."""
        self.progress(max(0.0, self._last_pct[0]), note=note)

    # ------------------------------------------------------------ messages
    def log(self, msg):
        self("log", msg=redact(msg))

    def warn(self, msg):
        self("warn", msg=redact(msg))

    def result(self, data):
        self("result", data=data)

    def error(self, code, msg, hint=""):
        self("error", code=code, msg=redact(msg), hint=redact(hint))


_PROTO = {"stream": None, "stdin": None, "lock": threading.Lock()}


def claim_stdout():
    """Make the JSON-lines protocol the ONLY thing on stdout (cli.py / worker.py call this first).
    The real stdout handle is duplicated for protocol writes, then fd 1 is pointed at stderr, so child
    processes (ffmpeg without capture) and C extensions printing to stdout can never corrupt the stream.
    fd 0 is pointed at NUL as well: on Windows a child inheriting a stdin pipe that another thread is
    reading blocks in DuplicateHandle until the next line arrives (measured: worker deadlock).
    Returns a text stream on the ORIGINAL stdin (the worker reads requests from it)."""
    if _PROTO["stream"] is not None:
        return _PROTO["stdin"]
    import os
    try:
        sys.__stdout__.flush()
    except (AttributeError, ValueError, OSError):
        pass
    proto = os.fdopen(os.dup(1), "w", encoding="utf-8", errors="replace", buffering=1, newline="\n")
    os.dup2(2, 1)
    try:
        stdin = os.fdopen(os.dup(0), "r", encoding="utf-8", errors="replace")
        nul = os.open(os.devnull, os.O_RDONLY)
        os.dup2(nul, 0)
        os.close(nul)
    except OSError:
        stdin = sys.stdin
    if os.name == "nt":  # dup2 does not update the Win32 std handles that subprocess inherits: do it here
        try:
            import ctypes
            import msvcrt
            k32 = ctypes.windll.kernel32
            k32.SetStdHandle(-10, msvcrt.get_osfhandle(0))   # STD_INPUT_HANDLE  -> NUL
            k32.SetStdHandle(-11, msvcrt.get_osfhandle(1))   # STD_OUTPUT_HANDLE -> stderr
        except (OSError, AttributeError, ValueError):
            pass
    _PROTO.update(stream=proto, stdin=stdin)
    return stdin


def _finite(o):
    """NaN/inf -> None (JSON.parse in the panel rejects NaN)."""
    if isinstance(o, float):
        return o if o == o and o not in (float("inf"), float("-inf")) else None
    if isinstance(o, dict):
        return {k: _finite(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_finite(v) for v in o]
    return o


def write_line(obj):
    """Write one protocol line (thread-safe) to the claimed stdout (else sys.__stdout__)."""
    try:
        line = json.dumps(obj, ensure_ascii=False, default=_default, allow_nan=False)
    except ValueError:
        line = json.dumps(_finite(json.loads(json.dumps(obj, default=_default))), ensure_ascii=False)
    with _PROTO["lock"]:
        out = _PROTO["stream"] or (sys.__stdout__ if sys.__stdout__ is not None else sys.stdout)
        out.write(line + "\n")
        out.flush()


_stdout_sink = write_line


def _default(o):
    try:
        import numpy as np
        if isinstance(o, np.generic):
            return o.item()
        if isinstance(o, np.ndarray):
            return o.tolist()
    except ImportError:
        pass
    if hasattr(o, "__fspath__"):
        return str(o)
    raise TypeError(f"not JSON serializable: {type(o).__name__}")


class NullEmitter(Emitter):
    """Discards everything (library use and tests). Still honours cancel()."""

    def __init__(self):
        super().__init__(sink=lambda obj: None)


class ListEmitter(Emitter):
    """Collects events in .items (tests)."""

    def __init__(self, **kw):
        self.items = []
        super().__init__(sink=self.items.append, min_interval=0.0, **kw)

    def kinds(self):
        return [e["ev"] for e in self.items]


def to_json_line(obj):
    return json.dumps(obj, ensure_ascii=False, default=_default)
