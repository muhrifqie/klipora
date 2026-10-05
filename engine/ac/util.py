"""Shared plumbing: errors, paths, settings, workdir, JSON io, ffmpeg/ffprobe, disk guard, file hash,
GPU lock, CUDA DLL setup and a log file that redacts secrets.

Stdlib only (numpy is never imported here) so every entry point can import it cheaply.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ENGINE_DIR = Path(__file__).resolve().parents[1]          # <repo>\engine
PROJECT_DIR = ENGINE_DIR.parent                             # <repo> (e.g. C:\klipora)
WIB = timezone(timedelta(hours=7))


# ---------------------------------------------------------------- errors

class EngineError(Exception):
    """An error the panel can show: `code` is machine readable, `msg`/`hint` are UI text in the job's language
    (build them with ac.i18n.tr at raise time).
    Raise it from tool actions; cli/worker turn it into {"ev":"error","code","msg","hint"} + exit 1."""

    def __init__(self, code, msg, hint=""):
        super().__init__(msg)
        self.code, self.msg, self.hint = code, msg, hint

    def to_event(self):
        return {"ev": "error", "code": self.code, "msg": self.msg, "hint": self.hint}


class Cancelled(EngineError):
    def __init__(self, msg=None):
        from .i18n import tr
        super().__init__("CANCELLED", msg or tr("common.cancelled"))


# ---------------------------------------------------------------- app folders

def _env_dir(var, fallback):
    return Path(os.environ.get(var) or Path.home() / fallback)


# Klipora data folders. Installs from before the rename used "AutoCutBOT": the first call copies the old folder
# (never moves it; lock/tmp files skipped) into "<new>.migrating-<pid>" and renames that to the new name, so a
# second process never sees half a folder. The panel (panel/js/core/sys.js) does the same on its first start.
# If the copy fails, the old folder keeps being used so nothing breaks.
APP_DIR = "Klipora"
LEGACY_APP_DIR = "AutoCutBOT"
_MIGRATED: dict[str, Path] = {}


def _migrate(base: Path) -> Path:
    new, old = base / APP_DIR, base / LEGACY_APP_DIR
    key = str(new)
    if key in _MIGRATED:
        return _MIGRATED[key]
    use = new
    if not new.exists() and old.is_dir():
        tmp = base / f"{APP_DIR}.migrating-{os.getpid()}"
        try:
            shutil.copytree(old, tmp, ignore=shutil.ignore_patterns("*.lock", "*.tmp"), dirs_exist_ok=True)
            (tmp / "MIGRATED_FROM.txt").write_text(
                f"Copied from {old} on first start of Klipora. The old folder is no longer used and can be deleted.\n",
                encoding="utf-8")
            try:
                tmp.rename(new)
            except OSError:
                if not new.exists():
                    raise
                shutil.rmtree(tmp, ignore_errors=True)   # another process won the race: use its copy
        except OSError:
            shutil.rmtree(tmp, ignore_errors=True)
            use = new if new.exists() else old
    _MIGRATED[key] = use
    return use


def appdata_dir():
    """%APPDATA%\\Klipora (settings, shared with the panel). Migrated once from %APPDATA%\\AutoCutBOT."""
    d = _migrate(_env_dir("APPDATA", "AppData/Roaming"))
    d.mkdir(parents=True, exist_ok=True)
    return d


def local_dir(*sub):
    """%LOCALAPPDATA%\\Klipora[\\sub...] (locks, logs, ai_cache, fallback media caches). Migrated once from AutoCutBOT."""
    d = _migrate(_env_dir("LOCALAPPDATA", "AppData/Local"))
    d = d.joinpath(*sub)
    d.mkdir(parents=True, exist_ok=True)
    return d


# ---------------------------------------------------------------- settings

DEFAULT_SETTINGS = {
    "python": "python",
    "workRoot": str(Path.home() / "Videos" / "Klipora"),
    "gpu": True,
    "whisperModel": "large-v3-turbo",
    "lang": "id",                   # speech (transcription) language; the UI language comes with the job (ac.i18n)
    "uiLang": "auto",
    "ai": True,
    "aiModel": "",                  # "" = per-task default (grok-fast / grok-auto)
    "captionTemplate": "",
    "outputDir": "",
    "glossary": [],
}
_SETTINGS = {"mtime": None, "data": None}


def settings_path():
    return appdata_dir() / "settings.json"


def settings():
    """Panel settings merged over DEFAULT_SETTINGS. Re-read when the file changes (worker stays current).
    Unknown keys written by the panel are kept. A broken file falls back to defaults (never raises)."""
    p = settings_path()
    try:
        mt = p.stat().st_mtime_ns
    except OSError:
        mt = None
    if _SETTINGS["data"] is None or mt != _SETTINGS["mtime"]:
        data = dict(DEFAULT_SETTINGS)
        if mt is not None:
            try:
                user = json.loads(p.read_text(encoding="utf-8-sig"))
                if isinstance(user, dict):
                    data.update({k: v for k, v in user.items() if v is not None})
            except (OSError, ValueError):
                pass
        _SETTINGS.update(mtime=mt, data=data)
    return dict(_SETTINGS["data"])


def setting(key, default=None):
    return settings().get(key, DEFAULT_SETTINGS.get(key, default))


# ---------------------------------------------------------------- names, workdir, json

_BAD = re.compile(r'[\\/:*?"<>|\x00-\x1f]+')


def safe_name(name, max_len=80):
    """File-system safe name (Windows): forbidden chars -> '_', trimmed, never empty."""
    s = _BAD.sub("_", str(name or "")).strip().strip(".")
    return (s[:max_len].rstrip(" .") or "untitled")


def workdir(job=None, seq_name=None):
    """Job work folder (created). Priority: job["workdir"], else <workRoot>\\<safe seq name>.
    workRoot defaults to %USERPROFILE%\\Videos\\Klipora. Big outputs (renders, previews) go here only."""
    if job and job.get("workdir"):
        d = Path(job["workdir"])
    else:
        if seq_name is None and job:
            seq_name = (job.get("seq") or {}).get("name")
        root = setting("workRoot") or DEFAULT_SETTINGS["workRoot"]   # "" from the panel = default
        d = Path(root) / safe_name(seq_name or "Tanpa nama")   # i18n-ignore (folder name, kept stable)
    d.mkdir(parents=True, exist_ok=True)
    return d


def read_json(path, default=None):
    """Parse a UTF-8 (BOM tolerated) JSON file; `default` when missing or broken."""
    try:
        return json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return default


def _plain(o):
    """json default: numpy scalars/arrays -> Python, Path -> str."""
    if hasattr(o, "item") and callable(o.item) and getattr(o, "ndim", 1) == 0:
        return o.item()
    if hasattr(o, "tolist"):
        return o.tolist()
    if hasattr(o, "__fspath__"):
        return str(o)
    raise TypeError(f"{type(o).__name__} is not JSON serializable")


def _finite(o):
    if isinstance(o, float):
        return o if o == o and abs(o) != float("inf") else None
    if isinstance(o, dict):
        return {k: _finite(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_finite(v) for v in o]
    return o


def dumps(obj, indent=None):
    """JSON text the panel can always JSON.parse: numpy values converted, NaN/inf -> null."""
    sep = None if indent else (",", ":")
    try:
        return json.dumps(obj, ensure_ascii=False, indent=indent, separators=sep, default=_plain, allow_nan=False)
    except ValueError:
        clean = _finite(json.loads(json.dumps(obj, default=_plain)))
        return json.dumps(clean, ensure_ascii=False, indent=indent, separators=sep)


def write_json(path, obj, indent=None):
    """Atomic write (tmp + os.replace) so a killed job never leaves half a file. Returns the Path."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    text = dumps(obj, indent)
    fd, tmp = tempfile.mkstemp(prefix=p.name + ".", suffix=".tmp", dir=str(p.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, p)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise
    return p


def now_iso():
    return datetime.now(WIB).isoformat(timespec="seconds")


def job_id(tool):
    return f"{tool}-{datetime.now(WIB).strftime('%Y%m%d-%H%M%S')}"


def fmt_sec(sec, digits=1):
    """Seconds with fixed decimals in the UI language: 12.5 -> '12,5 dtk' (id) / '12.5 s' (en).
    For the adaptive form ('3,7 dtk', '1 mnt 30 dtk') use ac.i18n.secs."""
    from .i18n import dec, unit_sec
    return f"{dec(sec, digits)} {unit_sec()}"


def writable_dir(d):
    """True when we can create files in d (media folders on read-only/network drives are not)."""
    try:
        fd, tmp = tempfile.mkstemp(prefix=".ac_probe_", dir=str(d))
        os.close(fd)
        os.unlink(tmp)
        return True
    except OSError:
        return False


_WRITABLE = {}


def cache_path(media, suffix):
    """Cache file for a media file: next to it as '<stem><suffix>' (established convention), or under
    %LOCALAPPDATA%\\Klipora\\cache\\<hash><suffix> when the media folder is not writable.
    An existing file wins; the folder write probe runs once per folder per process."""
    media = Path(media)
    near = media.with_name(media.stem + suffix)
    if near.is_file():
        return near
    d = str(media.parent).lower()
    if d not in _WRITABLE:
        _WRITABLE[d] = writable_dir(media.parent)
    if _WRITABLE[d]:
        return near
    try:
        return local_dir("cache") / (file_hash(media) + suffix)
    except OSError:
        return near


# ---------------------------------------------------------------- hashing

def file_hash(path):
    """Cheap identity of a media file: sha1(abs path lowercase | size | mtime_ns)[:16].
    Changes when the file is replaced/edited or moved; no content read (34 min mp4 = 240 MB)."""
    p = Path(path).resolve()
    st = p.stat()
    key = f"{str(p).lower()}|{st.st_size}|{st.st_mtime_ns}"
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]


def data_hash(obj):
    """Stable sha256[:24] of any JSON-able value (cache keys)."""
    raw = json.dumps(obj, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:24]


# ---------------------------------------------------------------- ffmpeg

def ffmpeg_exe():
    return os.environ.get("AC_FFMPEG") or shutil.which("ffmpeg") or "ffmpeg"


def ffprobe_exe():
    return os.environ.get("AC_FFPROBE") or shutil.which("ffprobe") or "ffprobe"


_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def run(cmd, *, check=True, capture=True, timeout=None, text=False, input=None):
    """subprocess.run without a console window. stdout/stderr captured (bytes unless text=True)."""
    try:
        return subprocess.run([str(c) for c in cmd], capture_output=capture, check=False, timeout=timeout,
                              text=text, input=input, creationflags=_NO_WINDOW,
                              stdin=subprocess.DEVNULL if input is None else None,
                              **({"encoding": "utf-8", "errors": "replace"} if text else {}))
    except FileNotFoundError as e:
        from .i18n import tr
        raise EngineError("NO_FFMPEG" if "ff" in str(cmd[0]).lower() else "NO_EXE",
                          tr("err.noFfmpeg.msg", name=Path(str(cmd[0])).name), tr("err.noFfmpeg.hint")) from e


def run_ffmpeg(args, *, check=True, timeout=None, capture=True):
    """ffmpeg -hide_banner -nostdin -loglevel error <args>. Returns CompletedProcess (bytes stdout, so raw
    PCM/frames can be piped). On failure raises EngineError('FFMPEG') with the last stderr lines."""
    cmd = [ffmpeg_exe(), "-hide_banner", "-nostdin", "-loglevel", "error", *args]
    r = run(cmd, timeout=timeout, capture=capture)
    if check and r.returncode != 0:
        err = (r.stderr or b"").decode("utf-8", "replace").strip().splitlines()
        from .i18n import tr
        raise EngineError("FFMPEG", tr("err.ffmpeg.failed", detail=err[-1][:300] if err else f"exit {r.returncode}"),
                          "\n".join(err[-5:])[:800])
    return r


def ffprobe(path):
    """ffprobe -show_streams -show_format as a dict. EngineError('NO_MEDIA') if missing/unreadable."""
    from .i18n import tr
    p = Path(path)
    if not p.is_file():
        raise EngineError("NO_MEDIA", tr("err.noMedia.msg", path=str(p)), tr("err.noMedia.hint"))
    r = run([ffprobe_exe(), "-v", "error", "-show_streams", "-show_format", "-of", "json", str(p)], text=True)
    if r.returncode != 0:
        raise EngineError("BAD_MEDIA", tr("err.badMedia.msg", name=p.name), (r.stderr or "")[-400:])
    return json.loads(r.stdout or "{}")


# ---------------------------------------------------------------- disk

def free_bytes(path=None):
    p = Path(path or Path.home())
    while not p.exists() and p.parent != p:
        p = p.parent
    return shutil.disk_usage(str(p)).free


def ensure_free(need_bytes, path=None, margin=500 * 1024 ** 2):
    """Raise EngineError('DISK_FULL') unless the drive of `path` has need_bytes + margin (default 500 MB)
    free. Call before every render/extract that writes more than a few MB."""
    free = free_bytes(path)
    if free < need_bytes + margin:
        drive = (Path(path or Path.home()).drive or "C:")
        from .i18n import dec, tr
        gb = lambda b: dec(b / 1024 ** 3, 1)  # noqa: E731
        raise EngineError("DISK_FULL", tr("err.diskFull.msg", drive=drive, free=gb(free), need=gb(need_bytes + margin)),
                          tr("err.diskFull.hint"))
    return free


# ---------------------------------------------------------------- GPU lock

class GpuLock:
    """Cross-process exclusive lock for Whisper/GPU work: %LOCALAPPDATA%\\Klipora\\gpu.lock.

    Uses an OS byte-range lock (msvcrt.locking on Windows, fcntl elsewhere), so a killed process
    (taskkill /T /F) releases it automatically: no stale lock files. While waiting it reports
    "Antre GPU ..." through emit.wait() every ~2 s. Re-entrant inside one process (thread-safe).

        with gpu_lock(emit, label="transcribe"):
            model.transcribe(...)
    """

    _local = threading.RLock()
    _depth = 0
    _fh = None

    def __init__(self, emit=None, timeout=1800.0, label="", poll=0.5):
        self.emit, self.timeout, self.label, self.poll = emit, timeout, label, poll

    @staticmethod
    def path():
        return local_dir() / "gpu.lock"

    @staticmethod
    def owner():
        return read_json(local_dir() / "gpu.owner.json", {}) or {}

    @staticmethod
    def _try(fh):
        try:
            if os.name == "nt":
                import msvcrt
                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            return False

    def __enter__(self):
        GpuLock._local.acquire()
        if GpuLock._depth:
            GpuLock._depth += 1
            return self
        fh = open(self.path(), "a+b")
        t0, last = time.time(), 0.0
        try:
            while not self._try(fh):
                waited = time.time() - t0
                if waited > self.timeout:
                    from .i18n import tr
                    raise EngineError("GPU_BUSY", tr("err.gpuBusy.msg"), tr("err.gpuBusy.hint"))
                if self.emit is not None and time.time() - last >= 2.0:
                    from .i18n import tr
                    who = self.owner()
                    note = tr("gpu.queue", who=who.get("label") or tr("gpu.otherProcess"), secs=int(waited))
                    self.emit.wait(note)
                    last = time.time()
                if self.emit is not None:
                    self.emit.check_cancel()
                time.sleep(self.poll)
        except BaseException:
            fh.close()
            GpuLock._local.release()
            raise
        GpuLock._fh = fh
        GpuLock._depth = 1
        with contextlib.suppress(OSError):
            write_json(local_dir() / "gpu.owner.json", {"pid": os.getpid(), "label": self.label, "since": now_iso()})
        return self

    def __exit__(self, *exc):
        try:
            GpuLock._depth -= 1
            if GpuLock._depth == 0 and GpuLock._fh is not None:
                fh, GpuLock._fh = GpuLock._fh, None
                with contextlib.suppress(OSError):
                    (local_dir() / "gpu.owner.json").unlink()
                with contextlib.suppress(OSError):
                    if os.name == "nt":
                        import msvcrt
                        fh.seek(0)
                        msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
                fh.close()
        finally:
            GpuLock._local.release()
        return False


def gpu_lock(emit=None, timeout=1800.0, label=""):
    return GpuLock(emit, timeout, label)


# ---------------------------------------------------------------- CUDA DLLs

_CUDA = {"done": False, "dirs": []}


def cuda_setup():
    """Make cuBLAS/cuDNN from the pip wheels (nvidia-cublas-cu12, nvidia-cudnn-cu12) loadable by
    CTranslate2 on Windows. Idempotent; returns the DLL dirs added (empty when the wheels are absent)."""
    if _CUDA["done"]:
        return list(_CUDA["dirs"])
    _CUDA["done"] = True
    for name in ("nvidia.cublas", "nvidia.cudnn", "nvidia.cuda_runtime", "nvidia.cuda_nvrtc"):
        try:
            mod = __import__(name, fromlist=["__path__"])
        except ImportError:
            continue
        for base in list(getattr(mod, "__path__", [])):
            d = os.path.join(base, "bin")
            if os.path.isdir(d) and d not in _CUDA["dirs"]:
                with contextlib.suppress(OSError, AttributeError):
                    os.add_dll_directory(d)
                os.environ["PATH"] = d + os.pathsep + os.environ.get("PATH", "")
                _CUDA["dirs"].append(d)
    return list(_CUDA["dirs"])


# ---------------------------------------------------------------- secrets + logging

def _secret_values():
    vals = []
    for k in ("AI_API_KEY", "PEXELS_API_KEY"):
        if os.environ.get(k):
            vals.append(os.environ[k])
    env = PROJECT_DIR / ".env"
    try:
        for line in env.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                if "KEY" in k.upper() or "TOKEN" in k.upper() or "SECRET" in k.upper():
                    v = v.strip().strip('"').strip("'")
                    if len(v) >= 6:
                        vals.append(v)
    except OSError:
        pass
    with contextlib.suppress(Exception):
        k = setting("pexelsKey")
        if isinstance(k, str) and len(k) >= 6:
            vals.append(k)
    with contextlib.suppress(Exception):   # AI provider keys decrypted/stored by this process (ac.ai.keystore)
        from .ai.keystore import revealed
        vals += revealed()
    return sorted(set(vals), key=len, reverse=True)


_SECRET_RE = re.compile(r"(?i)((?:authorization[\"'=:\s]+)?bearer\s+|authorization[\"'=:\s]+|"
                        r"api[_-]?key[\"'=:\s]+|token[\"'=:\s]+)([A-Za-z0-9._\-]{6,})")


def redact(text):
    """Remove secrets from any text before it is logged or sent to the panel: known key values from
    .env/settings plus 'Bearer xxx', 'api_key=xxx', 'token: xxx' patterns."""
    s = str(text)
    for v in _secret_values():
        s = s.replace(v, "***")
    return _SECRET_RE.sub(lambda m: m.group(1) + "***", s)


class _RedactFilter(logging.Filter):
    def filter(self, record):
        record.msg = redact(record.getMessage())
        record.args = ()
        return True


_LOGGER = {}


def log():
    """Engine file logger: %LOCALAPPDATA%\\Klipora\\logs\\engine.log (1 MB rotating x2), secrets redacted.
    Never logs to stdout (stdout is the JSON-lines protocol)."""
    if "lg" in _LOGGER:
        return _LOGGER["lg"]
    from logging.handlers import RotatingFileHandler
    lg = logging.getLogger("klipora")
    lg.setLevel(logging.INFO)
    lg.propagate = False
    try:
        h = RotatingFileHandler(local_dir("logs") / "engine.log", maxBytes=1024 ** 2, backupCount=2,
                                encoding="utf-8")
    except OSError:
        h = logging.StreamHandler(sys.stderr)
    h.setFormatter(logging.Formatter("%(asctime)s %(process)d %(levelname)s %(message)s"))
    h.addFilter(_RedactFilter())
    lg.addHandler(h)
    _LOGGER["lg"] = lg
    return lg
