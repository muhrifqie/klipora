"""Persistent engine worker: JSON-lines RPC over stdin/stdout (docs/SPEC.md section 2, docs/ENGINE_API.md).

  python -X utf8 engine/worker.py [--idle-unload 300]

Requests (one JSON object per stdin line):
  {"id": 7, "job": {...same as a job file...}}     -> events of that job, each wrapped {"id": 7, "ev": ...}
  {"id": 8, "cmd": "ping"}                         -> {"id": 8, "ev": "result", "data": {"pong": true, "busy": 7, "queue": 0}}
  {"id": 9, "cmd": "cancel", "target": 7}          -> cancels the running job (it ends with error CANCELLED) or
                                                      drops it from the queue; replies {"cancelled": bool}
  {"id": 10, "cmd": "health" | "tools" | "unload" | "shutdown"}   health/tools take an optional "lang" ("id"|"en")
On start the worker prints {"ev": "ready", "pid": ..., "version": ...}.

Jobs run one at a time in arrival order (models stay warm between jobs); control commands (ping/cancel)
are answered immediately by the reader thread, even while a job runs. Whisper models are unloaded after
--idle-unload seconds without jobs to give the VRAM back. stdin EOF = shut down after the current job.
"""
from __future__ import annotations

import argparse
import json
import os
import queue
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import cli  # noqa: E402
from ac import __version__  # noqa: E402
from ac.progress import Emitter, claim_stdout, write_line  # noqa: E402
from ac import i18n  # noqa: E402
from ac.i18n import tr  # noqa: E402
from ac.util import log  # noqa: E402

send = write_line   # thread-safe JSON line on the protocol stdout


class Worker:
    def __init__(self, idle_unload=300.0):
        self.q = queue.Queue()
        self.current = None          # (id, Emitter)
        self.pending = {}            # id -> job (queued, not started)
        self.lock = threading.Lock()
        self.idle_unload = idle_unload
        self.stop = threading.Event()
        self.last_job = time.time()

    # ------------------------------------------------------------ reader thread
    def read_loop(self, stream):
        for raw in stream:
            line = raw.strip()
            if not line:
                continue
            try:
                req = json.loads(line)
            except ValueError:
                send({"ev": "error", "code": "BAD_REQUEST", "msg": tr("cli.badRequest.json")})
                continue
            if not isinstance(req, dict):
                send({"ev": "error", "code": "BAD_REQUEST", "msg": tr("cli.badRequest.object")})
                continue
            rid = req.get("id")
            cmd = req.get("cmd")
            if "job" in req:
                with self.lock:
                    self.pending[rid] = req["job"]
                self.q.put(rid)
            elif cmd == "ping":
                with self.lock:
                    busy = self.current[0] if self.current else None
                    n = len(self.pending)
                send({"id": rid, "ev": "result", "data": {"pong": True, "busy": busy, "queue": n,
                                                          "pid": os.getpid(), "version": __version__}})
            elif cmd == "cancel":
                send({"id": rid, "ev": "result", "data": {"cancelled": self.cancel(req.get("target"))}})
            elif cmd == "shutdown":
                send({"id": rid, "ev": "result", "data": {"bye": True}})
                self.shutdown()
                return
            elif cmd in ("health", "tools", "unload"):
                with self.lock:
                    self.pending[rid] = {"_cmd": cmd, "lang": req.get("lang")}
                self.q.put(rid)
            else:
                send({"id": rid, "ev": "error", "code": "BAD_REQUEST", "msg": tr("cli.badRequest.cmd", cmd=cmd)})
        self.shutdown()  # stdin closed (panel gone)

    def cancel(self, target):
        with self.lock:
            if self.current and self.current[0] == target:
                self.current[1].cancel()
                return True
            if target in self.pending:
                job = self.pending.pop(target)
                lang = job.get("lang") if isinstance(job, dict) else None
                send({"id": target, "ev": "error", "code": "CANCELLED", "msg": tr("common.cancelled", lang)})
                return True
        return False

    def shutdown(self):
        self.stop.set()
        self.q.put(None)

    # ------------------------------------------------------------ job loop
    def run(self):
        while True:
            try:
                rid = self.q.get(timeout=5.0)
            except queue.Empty:
                self._maybe_unload()
                if self.stop.is_set():
                    return
                continue
            if rid is None:
                if self.stop.is_set() and self.q.empty():
                    return
                continue
            with self.lock:
                if rid not in self.pending:
                    continue          # cancelled while queued
                job = self.pending.pop(rid)
                emit = Emitter(sink=send, wrap={"id": rid})
                self.current = (rid, emit)
            try:
                if isinstance(job, dict) and job.get("_cmd"):
                    with i18n.using(job.get("lang")):
                        self._command(job["_cmd"], emit)
                else:
                    cli.execute(job, emit)
            except BaseException as e:  # noqa: BLE001  - the worker must survive anything
                log().error("worker job %s crashed outside execute: %r", rid, e)
                emit.error("INTERNAL", tr("cli.workerError", detail=f"{type(e).__name__}: {e}"))
            finally:
                with self.lock:
                    self.current = None
                self.last_job = time.time()

    def _command(self, cmd, emit):
        if cmd == "health":
            emit.result(cli.health())
        elif cmd == "tools":
            emit.result({"tools": cli.TOOLS.describe()})
        elif cmd == "unload":
            emit.result({"unloaded": self._unload()})

    def _unload(self):
        if "ac.transcript" in sys.modules:
            return sys.modules["ac.transcript"].unload_models()
        return 0

    def _maybe_unload(self):
        if self.idle_unload and time.time() - self.last_job > self.idle_unload:
            if self._unload():
                log().info("worker: whisper models unloaded after idle")
            self.last_job = time.time()


def main(argv=None):
    cli._stdout_utf8()
    requests = claim_stdout()   # protocol owns stdout; children get NUL stdin (no Windows pipe deadlock)
    ap = argparse.ArgumentParser(prog="worker.py")
    ap.add_argument("--idle-unload", type=float, default=300.0, help="unload Whisper after N idle seconds (0=never)")
    a = ap.parse_args(argv)
    w = Worker(a.idle_unload)
    sys.stdout = sys.stderr  # stray prints outside jobs must never corrupt the protocol
    send({"ev": "ready", "pid": os.getpid(), "version": __version__})
    t = threading.Thread(target=w.read_loop, args=(requests,), daemon=True)
    t.start()
    w.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
