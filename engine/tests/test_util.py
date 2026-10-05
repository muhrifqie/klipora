"""ac.util: settings, names, json io, hashing, disk guard, redaction, GPU lock (cross-process)."""
import json
import os
import subprocess
import sys
import time

import _common
from _common import ENGINE

tmp = _common.scratch("util")
os.environ["APPDATA"] = str(tmp / "roaming")          # isolate settings / locks from the real profile
os.environ["LOCALAPPDATA"] = str(tmp / "local")

from ac import util as U  # noqa: E402
from ac.progress import ListEmitter  # noqa: E402

# ---- settings: defaults, then panel-written overrides (unknown keys kept, broken file ignored)
s = U.settings()
assert s["whisperModel"] == "large-v3-turbo" and s["gpu"] is True and s["lang"] == "id"
assert U.settings_path() == tmp / "roaming" / "Klipora" / "settings.json"
U.settings_path().write_text(json.dumps({"gpu": False, "extra": 1, "lang": None}), encoding="utf-8")
time.sleep(0.02)
s = U.settings()
assert s["gpu"] is False and s["extra"] == 1 and s["lang"] == "id", s
assert U.setting("whisperModel") == "large-v3-turbo"
U.settings_path().write_text("{broken", encoding="utf-8")
os.utime(U.settings_path(), None)
assert U.settings()["gpu"] is True

# ---- names, workdir, json io
assert U.safe_name('a/b:c*?"<>|.') == "a_b_c_"
assert U.safe_name("") == "untitled" and len(U.safe_name("x" * 300)) == 80
U.settings_path().write_text(json.dumps({"workRoot": str(tmp / "work")}), encoding="utf-8")
os.utime(U.settings_path(), None)
wd = U.workdir({"seq": {"name": "Seq: 1/2"}})
assert wd == tmp / "work" / "Seq_ 1_2" and wd.is_dir(), wd
assert U.workdir({"workdir": str(tmp / "explicit")}) == tmp / "explicit"
p = U.write_json(tmp / "x" / "a.json", {"a": [1, "é"]}, indent=1)
assert U.read_json(p) == {"a": [1, "é"]} and U.read_json(tmp / "nope.json", 7) == 7
assert not list((tmp / "x").glob("*.tmp")), "temp file left behind"

# ---- hashing
f = tmp / "h.bin"
f.write_bytes(b"abc")
h1 = U.file_hash(f)
assert h1 == U.file_hash(f) and len(h1) == 16
time.sleep(0.02)
f.write_bytes(b"abcd")
assert U.file_hash(f) != h1
assert U.data_hash({"b": 1, "a": 2}) == U.data_hash({"a": 2, "b": 1})

# ---- disk guard
assert U.ensure_free(1, tmp) > 0
try:
    U.ensure_free(10 ** 15, tmp)
    raise AssertionError("ensure_free should raise")
except U.EngineError as e:
    assert e.code == "DISK_FULL" and "GB" in e.msg and "," in e.msg, e.msg

# ---- ffprobe / ffmpeg errors are EngineErrors with Indonesian text
try:
    U.ffprobe(tmp / "missing.mp4")
    raise AssertionError("ffprobe should raise")
except U.EngineError as e:
    assert e.code == "NO_MEDIA"
try:
    U.run_ffmpeg(["-i", str(tmp / "missing.mp4"), "-f", "null", "-"])
    raise AssertionError("run_ffmpeg should raise")
except U.EngineError as e:
    assert e.code == "FFMPEG" and e.msg.startswith("FFmpeg gagal")

# ---- redaction (never leak keys into logs / panel)
os.environ["AI_API_KEY"] = "sk-test-SECRET-123456"
txt = U.redact("key sk-test-SECRET-123456 and Authorization: Bearer abcdefghijkl and api_key=zzzzzzzz9")
assert "SECRET" not in txt and "abcdefghijkl" not in txt and "zzzzzzzz9" not in txt, txt
U.log().info("leak? sk-test-SECRET-123456")
for h in U.log().handlers:
    h.flush()
logtxt = (tmp / "local" / "Klipora" / "logs" / "engine.log").read_text(encoding="utf-8")
assert "leak?" in logtxt and "SECRET" not in logtxt
real_env = (_common.ROOT / ".env")
if real_env.is_file():  # values from the real .env are redacted too (we never print them here)
    key = next((ln.split("=", 1)[1].strip().strip('"\'') for ln in real_env.read_text(encoding="utf-8").splitlines()
                if ln.startswith("AI_API_KEY=")), "")
    if len(key) >= 6:
        assert key not in U.redact(f"x {key} y")

# ---- GPU lock across processes: held by a child -> we queue ("Antre") then time out; killed child frees it
child_src = (f"import sys,time; sys.path.insert(0, r'{ENGINE}'); from ac.util import gpu_lock\n"
             "with gpu_lock(label='child'):\n    print('held', flush=True); time.sleep(30)\n")
env = dict(os.environ)
child = subprocess.Popen([sys.executable, "-c", child_src], stdout=subprocess.PIPE, text=True, env=env)
assert child.stdout.readline().strip() == "held"
em = ListEmitter()
t0 = time.time()
try:
    with U.GpuLock(em, timeout=2.5, label="test", poll=0.1):
        raise AssertionError("lock should be busy")
except U.EngineError as e:
    assert e.code == "GPU_BUSY"
assert 2.4 < time.time() - t0 < 6, time.time() - t0
notes = [e.get("note", "") for e in em.items if e["ev"] == "progress"]
assert any(n.startswith("Antre GPU (child)") for n in notes), notes
child.kill()                    # taskkill-style death: the OS releases the byte lock
child.wait()
t0 = time.time()
with U.gpu_lock(None, timeout=5, label="test"):
    with U.gpu_lock(None, timeout=1, label="nested"):   # re-entrant in one process
        assert U.GpuLock.owner().get("label") == "test"
assert time.time() - t0 < 2
assert U.GpuLock.owner() == {}

# ---- cuda dll setup is idempotent
assert U.cuda_setup() == U.cuda_setup()
for h in list(U.log().handlers):      # release engine.log so the scratch folder can be deleted
    h.close()
    U.log().removeHandler(h)
_common.cleanup("util")
print("ok")
