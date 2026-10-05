"""engine/cli.py + engine/worker.py: job protocol end to end with the reference `echo` tool (no Premiere, no GPU)."""
import json
import subprocess
import sys
import time

import _common
from _common import ENGINE, MEDIA_49

PY = [sys.executable, "-X", "utf8"]
tmp = _common.scratch("cli")


def run_cli(*args, timeout=60):
    r = subprocess.run(PY + [str(ENGINE / "cli.py"), *map(str, args)], capture_output=True, text=True,
                       encoding="utf-8", timeout=timeout)
    events = [json.loads(ln) for ln in r.stdout.splitlines() if ln.startswith("{")]
    other = [ln for ln in r.stdout.splitlines() if ln.strip() and not ln.startswith("{")]
    assert not other, f"non-JSON stdout: {other[:3]}"
    return r.returncode, events


def job(path, **kw):
    p = tmp / path
    p.write_text(json.dumps(kw), encoding="utf-8")
    return p


# ---- tools
code, ev = run_cli("tools")
assert code == 0 and ev[-1]["ev"] == "result"
tools = {t["id"]: t for t in ev[-1]["data"]["tools"]}
assert tools["echo"]["ok"] and {"analyze", "apply"} <= set(tools["echo"]["actions"])

# ---- analyze -> review file -> panel edit -> apply -> plan
seq = {"id": "s", "name": "Tes CLI", "fps": 120, "duration": 20.0,
       "video": [{"index": 0, "name": "V1", "clips": [{"name": "x", "path": "", "start": 0, "end": 20, "in": 0, "out": 20}]}],
       "audio": []}
(tmp / "seq.json").write_text(json.dumps(seq), encoding="utf-8")
code, ev = run_cli("run", job("a.json", id="echo-1", tool="echo", action="analyze", seq=str(tmp / "seq.json"),
                               params={"every": 4, "len": 0.5, "seconds": 0.1}, workdir=str(tmp / "wd")))
assert code == 0, ev
kinds = [e["ev"] for e in ev]
assert kinds[0] == "stage" and kinds[-1] == "result" and kinds.count("result") == 1 and "error" not in kinds
assert [e["stage"] for e in ev if e["ev"] == "stage"] == ["read", "scan", "review"]
assert all(e["n"] == 3 for e in ev if e["ev"] == "stage")
res = ev[-1]["data"]
rv = json.loads(open(res["review"], encoding="utf-8").read())
assert rv["tool"] == "echo" and rv["timebase"] == "sequence" and len(rv["items"]) == 5 and res["summary"]["n"] == 5
for it in rv["items"]:          # the panel toggles everything on
    it["on"] = True
(tmp / "edited.json").write_text(json.dumps(rv), encoding="utf-8")
code, ev = run_cli("run", job("b.json", tool="echo", action="apply", review=str(tmp / "edited.json")))
assert code == 0 and ev[-1]["data"]["plan"]["kind"] == "remove_ranges"
assert ev[-1]["data"]["plan"]["ranges"] == [[it["t0"], it["t1"]] for it in rv["items"]]

# ---- errors: tool error, crash, unknown tool/action, bad job file, missing job
for j, want in ((job("c.json", tool="echo", action="fail", params={"code": "X1", "msg": "gagal"}), "X1"),
                (job("d.json", tool="echo", action="crash"), "INTERNAL"),
                (job("e.json", tool="nope", action="analyze"), "NO_TOOL"),
                (job("f.json", tool="echo", action="nope"), "NO_ACTION"),
                (job("g.json", action="analyze"), "BAD_JOB"),
                (tmp / "missing.json", "BAD_JOB")):
    code, ev = run_cli("run", j)
    assert code == 1 and ev[-1]["ev"] == "error" and ev[-1]["code"] == want, (j, ev[-1])
    assert ev[-1]["msg"] and "result" not in [e["ev"] for e in ev]
code, _ = run_cli("bogus") if False else (2, None)
r = subprocess.run(PY + [str(ENGINE / "cli.py")], capture_output=True, text=True)
assert r.returncode == 2

# ---- stray print() inside a tool becomes a log event (in-process)
sys.path.insert(0, str(ENGINE))
import cli  # noqa: E402
from ac.progress import ListEmitter  # noqa: E402
orig = cli.TOOLS.action


def noisy(job, emit):
    print("hello from tool")
    print('{"looks": "like json"}')
    return {"x": 1}


cli.TOOLS.action = lambda t, a: noisy
em = ListEmitter()
assert cli.execute({"tool": "echo", "action": "noisy"}, em) == 0
cli.TOOLS.action = orig
assert [e["msg"] for e in em.items if e["ev"] == "log"] == ["hello from tool", '{"looks": "like json"}']
assert em.items[-1] == {"ev": "result", "data": {"x": 1}}

# ---- health (one result, no secrets)
t0 = time.time()
code, ev = run_cli("health")
hsecs = time.time() - t0
assert code == 0 and len(ev) == 1 and ev[0]["ev"] == "result"
h = ev[0]["data"]
assert {"ffmpeg", "gpu", "models", "ai", "disk", "python", "engine", "ok", "issues"} <= set(h)
assert h["ffmpeg"]["ok"] and isinstance(h["issues"], list)
env = _common.ROOT / ".env"
if env.is_file():
    for ln in env.read_text(encoding="utf-8").splitlines():
        if ln.startswith("AI_API_KEY=") and len(ln) > 17:
            assert ln.split("=", 1)[1].strip().strip('"\'') not in json.dumps(h), "API key leaked in health"

# ---- worker: ready, ping while busy, queue, cancel, errors, shutdown
w = subprocess.Popen(PY + [str(ENGINE / "worker.py"), "--idle-unload", "0"], stdin=subprocess.PIPE,
                     stdout=subprocess.PIPE, text=True, encoding="utf-8", bufsize=1)


def send(obj):
    w.stdin.write(json.dumps(obj) + "\n")
    w.stdin.flush()


def read_until(pred, timeout=20):
    t0, got = time.time(), []
    while time.time() - t0 < timeout:
        ln = w.stdout.readline()
        if not ln:
            break
        e = json.loads(ln)
        got.append(e)
        if pred(e):
            return got
    raise AssertionError(f"timeout; got {got[-5:]}")


ready = json.loads(w.stdout.readline())
assert ready["ev"] == "ready" and ready["pid"] > 0
send({"id": 1, "job": {"tool": "echo", "action": "slow", "params": {"seconds": 5}}})
send({"id": 2, "job": {"tool": "echo", "action": "echo", "params": {"k": "v"}}})
got = read_until(lambda e: e.get("id") == 1 and e["ev"] == "stage")
assert got[-1]["stage"] == "wait"
send({"id": 3, "cmd": "ping"})
got = read_until(lambda e: e.get("id") == 3)
assert got[-1]["data"]["busy"] == 1 and got[-1]["data"]["queue"] == 1
send({"id": 4, "cmd": "cancel", "target": 1})
got = read_until(lambda e: e.get("id") == 2 and e["ev"] == "result")
by_id = {}
for e in got:
    by_id.setdefault(e.get("id"), []).append(e)
assert by_id[4][-1]["data"]["cancelled"] is True
assert by_id[1][-1]["ev"] == "error" and by_id[1][-1]["code"] == "CANCELLED"
assert by_id[2][-1]["data"] == {"params": {"k": "v"}}
send({"id": 5, "job": {"tool": "echo", "action": "fail"}})
send({"id": 6, "cmd": "nope"})
send({"id": 7, "cmd": "tools"})
got = read_until(lambda e: e.get("id") == 7 and e["ev"] == "result")
codes = {e.get("id"): e.get("code") for e in got if e["ev"] == "error"}
assert codes.get(5) == "ECHO_FAIL" and codes.get(6) == "BAD_REQUEST", codes
if MEDIA_49.is_file():  # a real analysis through the worker (cached envelope, no GPU)
    send({"id": 8, "job": {"tool": "echo", "action": "analyze", "workdir": str(tmp / "wd2"),
                           "params": {"media": str(MEDIA_49), "use_audio": True, "seconds": 0}}})
    got = read_until(lambda e: e.get("id") == 8 and e["ev"] in ("result", "error"))
    assert got[-1]["ev"] == "result" and got[-1]["data"]["summary"]["n"] > 0, got[-1]
send({"id": 9, "cmd": "shutdown"})
read_until(lambda e: e.get("id") == 9)
assert w.wait(timeout=10) == 0

# ---- English UI: job "lang" picks the language of generic errors/stages; AC_LANG for health and tools
code, ev = run_cli("run", job("en1.json", tool="echo", action="apply", lang="en"))
assert code == 1 and ev[-1]["code"] == "BAD_JOB", ev[-1]
assert ev[-1]["msg"] == "The apply job needs a review file." and ev[-1]["hint"] == "Run the analysis first.", ev[-1]
code, ev = run_cli("run", job("en2.json", action="analyze", lang="en"))
assert code == 1 and ev[-1]["code"] == "BAD_JOB" and ev[-1]["msg"] == "The job needs 'tool' and 'action'.", ev[-1]
code, ev = run_cli("run", job("en3.json", tool="echo", action="analyze", lang="en", params={"seconds": 0},
                               workdir=str(tmp / "wd3")))
assert code == 0 and [e["label"] for e in ev if e["ev"] == "stage"] == ["Read sequence", "Find sections", "Prepare review"]
code, ev = run_cli("run", job("id1.json", action="analyze"))          # no lang, no AC_LANG -> Indonesian
assert ev[-1]["msg"] == "Job butuh 'tool' dan 'action'.", ev[-1]
from ac import i18n  # noqa: E402
with i18n.using("en"):
    issues = cli.health_issues({"ffmpeg": {"ok": True, "libass": False}, "gpu": {"ok": True},
                                "models": {"tiny": {"cached": False}}, "ai": {"ok": False}, "disk": {"ok": True}})
    assert issues == ["FFmpeg has no libass, so captions cannot be rendered.",
                      "Whisper model 'tiny' is not downloaded yet (it downloads automatically on first use).",
                      "AI offline: the proxy is not responding. Features still work using rules."], issues
    rows = {t["id"]: t for t in cli.TOOLS.describe()}
    assert rows["silence"]["title"] == "Cut Silences" and rows["echo"]["description"].startswith("Sample tool"), rows["echo"]
assert cli.health_issues({"ffmpeg": {"ok": False}, "gpu": {"ok": True}, "models": {}, "ai": {"ok": True},
                          "disk": {"ok": True}}) == ["FFmpeg tidak ditemukan: semua fitur audio/video butuh FFmpeg."]
import os  # noqa: E402
r = subprocess.run(PY + [str(ENGINE / "cli.py"), "health"], capture_output=True, text=True, encoding="utf-8",
                   timeout=120, env={**os.environ, "AC_LANG": "en"})
hd = json.loads([ln for ln in r.stdout.splitlines() if ln.startswith("{")][-1])["data"]
assert hd["engine"]["name"] == "Klipora" and hd["engine"]["lang"] == "en", hd["engine"]
assert not any(w in " ".join(hd["issues"]) for w in ("tidak", "belum", "pakai", "diunduh")), hd["issues"]
_common.cleanup("cli")
print(f"ok (health {hsecs:.1f} s)")
