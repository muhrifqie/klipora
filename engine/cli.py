"""Klipora engine CLI (docs/SPEC.md section 2, docs/ENGINE_API.md).

  python -X utf8 engine/cli.py run <job.json>      run one tool action; JSON-lines events on stdout
  python -X utf8 engine/cli.py health              environment check -> one {"ev":"result","data":{...}}
  python -X utf8 engine/cli.py tools               registered tools and their actions
  python -X utf8 engine/cli.py ai <sub> ...        AI provider profiles (docs/AI_PROVIDERS.md); keys via stdin only
  python -X utf8 engine/cli.py transcribe <media> [--listen] [--refresh]   build transcript caches (dev)

Exit codes: 0 = success (exactly one "result" event), 1 = error (one "error" event), 2 = bad command line.
Stray print() output inside a tool is turned into {"ev":"log"} lines so stdout stays parseable.
User-facing texts (errors, health issues, tool titles) follow the job's "lang" or AC_LANG (ac.i18n).
"""
from __future__ import annotations

import argparse
import io
import json
import os
import platform
import shutil
import subprocess
import sys
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from ac import __version__  # noqa: E402
from ac import tools as TOOLS  # noqa: E402
from ac.progress import Emitter, claim_stdout  # noqa: E402
from ac import i18n  # noqa: E402
from ac.i18n import tr  # noqa: E402
from ac.util import (EngineError, GpuLock, appdata_dir, ffmpeg_exe, free_bytes, local_dir, log, read_json,  # noqa: E402
                     redact, setting, settings_path)


class _LogWriter(io.TextIOBase):
    """Replaces sys.stdout while a tool runs: complete lines become {"ev":"log"} events."""

    def __init__(self, emit):
        self._emit, self._buf = emit, ""

    def writable(self):
        return True

    def write(self, s):
        self._buf += str(s)
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            if line.strip():
                self._emit.log(line.rstrip())
        return len(s)

    def flush(self):
        if self._buf.strip():
            self._emit.log(self._buf.rstrip())
        self._buf = ""


def load_job(job):
    """Normalise a job: dict or path to a JSON file; `seq` may be inline or a path to a Timeline JSON file."""
    if isinstance(job, (str, Path)):
        data = read_json(job)
        if not isinstance(data, dict):
            raise EngineError("BAD_JOB", tr("err.badJob.read", path=str(job)))
        job = data
    if not isinstance(job, dict):
        raise EngineError("BAD_JOB", tr("err.badJob.notObject"))
    if not job.get("tool") or not job.get("action"):
        raise EngineError("BAD_JOB", tr("err.badJob.fields"))
    if isinstance(job.get("seq"), str):
        seq = read_json(job["seq"])
        if not isinstance(seq, dict):
            raise EngineError("BAD_JOB", tr("err.badJob.seqFile"))
        job["seq"] = seq
    job.setdefault("params", {})
    if job["params"] is None:
        job["params"] = {}
    return job


def execute(job, emit):
    """Run one job with `emit`; emits exactly one result or error event. Returns the exit code (0/1).
    Shared by cli.py and worker.py."""
    t0 = time.time()
    tool = action = "?"
    lang = job.get("lang") if isinstance(job, dict) else None
    if lang is None and isinstance(job, (str, Path)):
        try:
            lang = (read_json(job) or {}).get("lang")
        except Exception:  # noqa: BLE001 - load_job reports a broken file properly below
            lang = None
    with i18n.using(lang):
        return _execute(job, emit, t0)


def _execute(job, emit, t0):
    tool = action = "?"
    old_stdout = sys.stdout
    sys.stdout = _LogWriter(emit)
    try:
        job = load_job(job)
        tool, action = str(job["tool"]), str(job["action"])
        fn = TOOLS.action(tool, action)
        log().info("job %s %s/%s start", job.get("id"), tool, action)
        data = fn(job, emit)
        sys.stdout.flush()
        if data is None:
            data = {}
        json.dumps(data, default=_jsonable)  # fail here (INTERNAL) rather than half-writing a result line
        emit.result(data)
        log().info("job %s %s/%s ok %.2fs", job.get("id"), tool, action, time.time() - t0)
        return 0
    except EngineError as e:
        sys.stdout.flush()
        emit.error(e.code, e.msg, e.hint)
        log().info("job %s/%s error %s: %s", tool, action, e.code, e.msg)
        return 1
    except KeyboardInterrupt:
        emit.error("CANCELLED", tr("common.cancelled"))
        return 1
    except OSError as e:  # permission denied, disk full, file vanished: user-fixable, not a crash
        log().error("job %s/%s io error: %s", tool, action, traceback.format_exc())
        full = getattr(e, "errno", None) == 28
        what = {"path": e.filename or "", "detail": e.strerror or str(e)}
        emit.error("DISK_FULL" if full else "IO", tr("err.diskFull.write" if full else "err.io.msg", **what),
                   tr("err.diskFull.writeHint" if full else "err.io.hint"))
        return 1
    except Exception as e:  # noqa: BLE001
        tb = traceback.format_exc()
        log().error("job %s/%s crashed: %s", tool, action, tb)
        emit.error("INTERNAL", tr("err.internal.msg", where=f"{tool}/{action}", detail=f"{type(e).__name__}: {e}"),
                   tb[-1500:])
        return 1
    finally:
        sys.stdout = old_stdout


def _jsonable(o):
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
    raise TypeError(f"{type(o).__name__} is not JSON serializable")


# ====================================================================== health

def _run(cmd, timeout=5):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=timeout, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return r.returncode, r.stdout, r.stderr
    except (OSError, subprocess.TimeoutExpired) as e:
        return -1, "", str(e)


def _check_ffmpeg():
    exe = ffmpeg_exe()
    code, out, _ = _run([exe, "-hide_banner", "-version"])
    if code != 0:
        return {"ok": False, "path": exe, "why": tr("health.ffmpegNotOnPath")}
    _, filters, _ = _run([exe, "-hide_banner", "-filters"])
    _, enc, _ = _run([exe, "-hide_banner", "-encoders"])
    ver = (out.splitlines() or [""])[0]
    return {"ok": True, "path": shutil.which(exe) or exe, "version": ver[:120],
            "libass": any(ln.split()[1:2] in (["ass"], ["subtitles"]) for ln in filters.splitlines() if ln.strip()),
            "nvenc": " h264_nvenc " in enc, "qtrle": " qtrle " in enc,
            "ffprobe": bool(shutil.which("ffprobe"))}


def _check_gpu():
    out = {"ok": False}
    code, smi, _ = _run(["nvidia-smi", "--query-gpu=name,memory.used,memory.total,driver_version",
                         "--format=csv,noheader,nounits"], timeout=5)
    if code == 0 and smi.strip():
        parts = [p.strip() for p in smi.strip().splitlines()[0].split(",")]
        if len(parts) >= 4:
            out.update(name=parts[0], mem_used_mb=int(float(parts[1])), mem_total_mb=int(float(parts[2])),
                       driver=parts[3])
    from ac.util import cuda_setup
    dirs = cuda_setup()
    dlls = {}
    for pattern in ("cublas64_12.dll", "cublasLt64_12.dll", "cudnn64_9.dll"):
        dlls[pattern] = any((Path(d) / pattern).is_file() for d in dirs)
    out["cuda_dlls"] = dlls
    try:
        import ctranslate2
        out["ctranslate2"] = ctranslate2.__version__
        out["cuda_devices"] = ctranslate2.get_cuda_device_count()
    except Exception as e:  # noqa: BLE001
        out["ctranslate2"] = None
        out["why"] = f"ctranslate2: {e}"
        out["cuda_devices"] = 0
    out["ok"] = bool(out.get("cuda_devices")) and all(dlls.values())
    if not out["ok"] and "why" not in out:
        out["why"] = tr("health.gpuNotReady")
    owner = GpuLock.owner()
    if owner:
        out["lock"] = owner
    return out


_HF_REPOS = {"large-v3-turbo": "mobiuslabsgmbh/faster-whisper-large-v3-turbo", "turbo": "mobiuslabsgmbh/faster-whisper-large-v3-turbo",
             "large-v3": "Systran/faster-whisper-large-v3", "large-v2": "Systran/faster-whisper-large-v2",
             "medium": "Systran/faster-whisper-medium", "small": "Systran/faster-whisper-small",
             "base": "Systran/faster-whisper-base", "tiny": "Systran/faster-whisper-tiny"}


def _check_models():
    hub = Path(os.environ.get("HF_HUB_CACHE") or os.environ.get("HUGGINGFACE_HUB_CACHE")
               or Path(os.environ.get("HF_HOME") or Path.home() / ".cache" / "huggingface") / "hub")
    out = {}
    from ac.transcript import LISTEN_MODEL
    for name in dict.fromkeys([setting("whisperModel") or "large-v3-turbo", LISTEN_MODEL]):
        repo = _HF_REPOS.get(name, name)
        d = hub / ("models--" + repo.replace("/", "--"))
        out[name] = {"cached": any(d.glob("snapshots/*/model.bin")) if d.is_dir() else False, "repo": repo}
    return out


def _check_ai():
    try:
        from ac.ai import client
        return client.health()
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "why": redact(str(e))}


def _check_disk():
    root = Path(setting("workRoot") or Path.home())
    rows = {}
    for label, p in (("C:", Path(os.environ.get("SystemDrive", "C:") + "\\")), ("workRoot", root)):
        try:
            rows[label] = round(free_bytes(p) / 1024 ** 3, 2)
        except OSError:
            rows[label] = None
    low = min(v for v in rows.values() if v is not None) if any(v is not None for v in rows.values()) else 0
    return {"free_gb": rows, "ok": low >= 2.0,
            **({} if low >= 2.0 else {"why": tr("health.diskLow", gb=i18n.dec(low, 1))})}


def health():
    t0 = time.time()
    checks = {"ffmpeg": _check_ffmpeg, "gpu": _check_gpu, "models": _check_models, "ai": _check_ai,
              "disk": _check_disk}
    with ThreadPoolExecutor(max_workers=len(checks)) as ex:
        futs = {k: ex.submit(fn) for k, fn in checks.items()}
        res = {}
        for k, f in futs.items():
            try:
                res[k] = f.result(timeout=20)
            except Exception as e:  # noqa: BLE001
                res[k] = {"ok": False, "why": redact(f"{type(e).__name__}: {e}")}
    res["python"] = {"ok": sys.version_info >= (3, 11), "version": platform.python_version(),
                     "exe": sys.executable}
    res["engine"] = {"name": "Klipora", "version": __version__, "dir": str(Path(__file__).resolve().parent),
                     "settings": str(settings_path()), "appdata": str(appdata_dir()), "local": str(local_dir()),
                     "lang": i18n.get_lang()}
    res["ok"] = bool(res["python"]["ok"] and res["ffmpeg"].get("ok"))
    res["issues"] = health_issues(res)
    res["ms"] = int((time.time() - t0) * 1000)
    return res


def health_issues(res):
    """User-facing problems (UI language) from the health check results (ffmpeg/gpu/models/ai/disk)."""
    issues = []
    ff, gpu, ai, disk = (res.get(k) or {} for k in ("ffmpeg", "gpu", "ai", "disk"))
    if not ff.get("ok"):
        issues.append(tr("health.noFfmpeg"))
    elif not ff.get("libass"):
        issues.append(tr("health.noLibass"))
    if not gpu.get("ok"):
        issues.append(gpu.get("why") or tr("health.gpuNotReadyShort"))
    for name, m in (res.get("models") or {}).items():
        if not m.get("cached"):
            issues.append(tr("health.modelMissing", name=name))
    if not ai.get("ok"):
        issues.append(tr("health.aiOffline", why=str(ai.get("why") or tr("health.aiNoReply"))))
    if not disk.get("ok"):
        issues.append(disk.get("why") or tr("health.diskAlmostFull"))
    return issues


# ====================================================================== main

def _stdout_utf8():
    for s in (sys.__stdout__, sys.__stderr__):
        try:
            s.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
        except (AttributeError, ValueError):
            pass


def main(argv=None):
    _stdout_utf8()
    claim_stdout()   # stdout = JSON lines only (children and C code write to stderr)
    ap = argparse.ArgumentParser(prog="cli.py", description="Klipora engine")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="run a job file")
    r.add_argument("job")
    sub.add_parser("health")
    sub.add_parser("tools")
    ai = sub.add_parser("ai", help="AI provider profiles: profiles|save|remove|set-key|delete-key|models|test|activate")
    ai.add_argument("rest", nargs=argparse.REMAINDER)
    t = sub.add_parser("transcribe", help="build transcript caches for a media file")
    t.add_argument("media")
    t.add_argument("--listen", action="store_true", help="also the filler listener pass")
    t.add_argument("--refresh", action="store_true")
    a = ap.parse_args(argv)
    emit = Emitter()
    if a.cmd == "run":
        if not Path(a.job).is_file():
            emit.error("BAD_JOB", tr("err.badJob.notFound", path=a.job))
            return 1
        return execute(a.job, emit)
    if a.cmd == "health":
        emit.result(health())
        return 0
    if a.cmd == "ai":
        from ac.ai import providers
        return providers.cli_main(a.rest, emit, claim_stdout())   # claim_stdout() -> original stdin (keys)
    if a.cmd == "tools":
        emit.result({"tools": TOOLS.describe()})
        return 0
    if a.cmd == "transcribe":
        job = {"tool": "_transcribe", "action": "run"}

        def run(_job, em):
            from ac import transcript as T
            em.plan([("words", tr("stage.transcribe"), 0.8), ("listen", tr("stage.listen"), 0.2)] if a.listen
                    else [("words", tr("stage.transcribe"), 1.0)])
            with em.step("words"):
                w = T.words(a.media, emit=em, refresh=a.refresh)
            out = {"words": len(w), "cache": str(T.words_path(a.media))}
            if a.listen:
                with em.step("listen"):
                    out["listen"] = len(T.listener_words(a.media, emit=em, refresh=a.refresh))
            return out
        try:
            data = run(job, emit)
            emit.result(data)
            return 0
        except EngineError as e:
            emit.error(e.code, e.msg, e.hint)
            return 1
    return 2


if __name__ == "__main__":
    sys.exit(main())
