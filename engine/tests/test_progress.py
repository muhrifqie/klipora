"""ac.progress: event shapes, plan weights, throttling, cancel, worker wrapping."""
import json
import subprocess
import sys

import _common
from _common import ENGINE

from ac.progress import ListEmitter, NullEmitter
from ac.util import Cancelled

em = ListEmitter()
em.plan([("audio", "Ambil audio", 1), ("transcribe", "Transkripsi", 3)])
with em.step("audio"):
    em.progress(50)
    em.done_note("dari cache")
em.stage("transcribe")
for p in range(0, 101, 1):
    em.progress(p)
em.stage_done()
em.warn("Grok offline, pakai aturan")
em.result({"ok": True})
kinds = em.kinds()
assert kinds[:3] == ["stage", "progress", "stage_done"], kinds
st = em.items[0]
assert st == {"ev": "stage", "id": "audio", "label": "Ambil audio", "i": 0, "n": 2, "w": 0.25, "stage": "audio"}, st
done = em.items[2]
assert done["id"] == "audio" and done["note"] == "dari cache" and "sec" in done
assert em.items[1]["pct"] == 50 and em.items[1]["all"] == 12.5
prog = [e for e in em.items if e["ev"] == "progress" and e.get("all", 0) > 25]
assert prog[-1]["pct"] == 100 and prog[-1]["all"] == 100.0
assert len(prog) < 101, "progress must be throttled (0.5 % steps)"
assert em.items[-2] == {"ev": "warn", "msg": "Grok offline, pakai aturan"} and em.items[-1]["ev"] == "result"

# sub-range mapping and wait notes
em2 = ListEmitter()
em2.stage("x", "X")
f = em2.sub(50, 100)
f(50)
assert em2.items[-1]["pct"] == 75
em2.wait("Antre GPU (transkripsi) 4 dtk")
assert em2.items[-1]["note"].startswith("Antre GPU") and em2.items[-1]["pct"] == 75

# cancel: progress is the cooperative cancel point
em3 = ListEmitter()
em3.cancel()
try:
    em3.progress(10)
    raise AssertionError("expected Cancelled")
except Cancelled as e:
    assert e.code == "CANCELLED"

# worker wrapping: request id wins, stage id stays readable as "stage"
em4 = ListEmitter(wrap={"id": 7})
em4.stage("transcribe", "Transkripsi")
em4.stage_done()
assert em4.items[0]["id"] == 7 and em4.items[0]["stage"] == "transcribe" and list(em4.items[0])[0] == "id"
assert em4.items[1]["stage"] == "transcribe"

# redaction on log/warn/error
import os  # noqa: E402
os.environ["AI_API_KEY"] = "sk-progress-SECRET-99"
em5 = ListEmitter()
em5.log("calling with sk-progress-SECRET-99")
em5.error("X", "bad sk-progress-SECRET-99", "hint sk-progress-SECRET-99")
assert "SECRET" not in json.dumps(em5.items)

NullEmitter().progress(5)

# default sink: one JSON line per event on the real stdout (numpy values serialised)
code = ("import sys; sys.path.insert(0, r'%s'); import numpy as np; from ac.progress import Emitter;"
        "e=Emitter(); e.stage('a','A'); e.result({'v': np.float32(1.5), 'arr': np.arange(2)})" % ENGINE)
out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, encoding="utf-8").stdout
lines = [json.loads(ln) for ln in out.splitlines()]
assert lines[0]["ev"] == "stage" and lines[1] == {"ev": "result", "data": {"v": 1.5, "arr": [0, 1]}}, lines
print("ok")
