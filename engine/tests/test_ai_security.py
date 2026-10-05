"""AI client hardening (review findings AISEC-1..11), offline with fake providers on 127.0.0.1 / 127.0.0.2:
redirects never followed (key never reaches a second host), timeouts not retried + breaker, non-JSON storms
bounded + breaker, .env Grok key only for the built-in local profile, wizard drafts kept alive while touched,
probe 5xx = not ok, quota errors inside a 200 body / HTML 200 fail over at once, a successful `ai test` closes
the breaker in another process, an undecryptable key counts as missing.
  python engine/tests/test_ai_security.py"""
import base64
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import _common

tmp = _common.scratch("ai_security")
os.environ["APPDATA"] = str(tmp / "roaming")
os.environ["LOCALAPPDATA"] = str(tmp / "local")
os.environ["AI_BASE_URL"] = "http://127.0.0.1:9/v1"   # built-in Grok profile -> refused port
os.environ["AI_API_KEY"] = "envkey-FAKE-grok-0123456789"
os.environ.pop("AI_DISABLED", None)

from ac.ai import client as ai  # noqa: E402
from ac.ai import keystore as KS  # noqa: E402
from ac.ai import providers as PV  # noqa: E402

KEY = "sk-SECRET-a-0123456789abcdef"
LOG = []        # (server, prov, method, path, auth)
MODE = {}       # prov -> behaviour
STEAL = {}


class Fake(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _reply(self, code, obj=None, raw=None, headers=()):
        body = raw if raw is not None else json.dumps(obj).encode("utf-8")
        self.send_response(code)
        for k, v in headers:
            self.send_header(k, v)
        self.send_header("Content-Type", "application/json" if raw is None else "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _route(self, method):
        n = int(self.headers.get("Content-Length") or 0)
        if n:
            self.rfile.read(n)
        parts = self.path.split("?")[0].strip("/").split("/")
        prov = parts[0]
        auth = self.headers.get("Authorization") or self.headers.get("x-api-key") or ""
        LOG.append((self.server.server_address[0], prov, method, self.path, auth))
        mode = MODE.get(prov, "ok")
        if prov == "steal":
            return self._reply(200, {"data": [{"id": "x"}], "choices": [{"message": {"content": '{"sum": 1}'}}]})
        if mode == "redirect":
            return self._reply(302, {}, headers=[("Location", STEAL["url"] + self.path)])
        if mode == "slow":
            time.sleep(2.5)
        if mode == "503":
            return self._reply(503, {"error": {"message": "down"}})
        if method == "GET":
            return self._reply(200, {"data": [{"id": "m"}]})
        if mode == "garbage":
            return self._reply(200, {"choices": [{"message": {"content": "maaf saya tidak bisa"}}]})
        if mode == "or429":
            return self._reply(200, {"error": {"code": 429, "message": "Rate limit exceeded upstream"}})
        if mode == "html":
            return self._reply(200, raw=b"<html><body>Login dulu</body></html>")
        return self._reply(200, {"choices": [{"message": {"content": '{"sum": 42}'}}]})

    def do_GET(self):
        self._route("GET")

    def do_POST(self):
        self._route("POST")


def serve(host):
    s = ThreadingHTTPServer((host, 0), Fake)
    s.handle_error = lambda *a: None          # the slow handler's client is gone (timeout): expected
    threading.Thread(target=s.serve_forever, daemon=True).start()
    return s, f"http://{host}:{s.server_address[1]}"


srv1, BASE = serve("127.0.0.1")
srv2, OTHER = serve("127.0.0.2")
STEAL["url"] = OTHER + "/steal"
MSG = [{"role": "user", "content": "Compute 17 + 25"}]
SCHEMA = {"type": "object", "required": ["sum"]}


def posts(prov):
    return [r for r in LOG if r[1] == prov and r[2] == "POST"]


def add(pid, **kw):
    PV.save_profile(dict({"id": pid, "kind": "custom", "base_url": f"{BASE}/{pid}/v1", "model_fast": "m"}, **kw))


for pid in ("a", "b", "c"):
    add(pid)
    KS.set_key(pid, KEY.replace("-a-", f"-{pid}-"))
PV.save_profile({"id": "grok_local", "enabled": False})

# ---------------------------------------------------------------- AISEC-1: redirects are never followed
MODE["a"] = "redirect"
PV.set_order(["a", "b"])
ai.forget()
h = ai.health()
row_a = next(r for r in h["route"] if r["id"] == "a")
assert not row_a["ok"] and "dialihkan" in row_a["why"], row_a
try:
    ai.list_models("a")
    raise AssertionError("redirected model list accepted")
except PV.ProfileError as e:
    assert e.code == "AI_REDIRECT", e.code
ai.forget()
tr = {}
obj = ai.chat_json(MSG, schema=SCHEMA, retries=1, trace=tr)
assert obj == {"sum": 42} and tr["profile"] == "b" and len(posts("a")) == 1, (tr, posts("a"))
assert not [r for r in LOG if r[0] == "127.0.0.2"], "a redirect target was contacted (key would leak)"
assert ai._is_open(PV.get("a")), "a redirecting provider must rest"
MODE["a"] = "ok"

# ---------------------------------------------------------------- AISEC-2: a timeout is not retried
MODE["a"] = "slow"
ai.forget()
n_a = len(posts("a"))
t0 = time.time()
obj = ai.chat_json(MSG, schema=SCHEMA, retries=1, timeout=1, trace=tr)
dt = time.time() - t0
assert tr["profile"] == "b" and len(posts("a")) == n_a + 1 and dt < 2.0, (tr, dt)
t0 = time.time()
obj = ai.chat_json(MSG, schema=SCHEMA, retries=1, timeout=1, trace=tr)
assert tr["profile"] == "b" and len(posts("a")) == n_a + 1 and time.time() - t0 < 0.5, "slow provider must rest"
MODE["a"] = "ok"
time.sleep(1.6)   # let the slow handler finish before the next section reuses a

# ---------------------------------------------------------------- AISEC-3: non-JSON storm is bounded
for pid in ("a", "b", "c"):
    MODE[pid] = "garbage"
PV.set_order(["a", "b", "c"])
ai.forget()
counts = []
for i in range(4):
    before = sum(len(posts(x)) for x in "abc")
    try:
        ai.chat_json(MSG, schema=SCHEMA, retries=0)
        raise AssertionError("garbage accepted")
    except ai.AIError:
        pass
    counts.append(sum(len(posts(x)) for x in "abc") - before)
assert counts[0] == 4 and counts[1] == 4 and counts[2] == 0 and counts[3] == 0, counts
for pid in ("a", "b", "c"):
    MODE[pid] = "ok"
    assert ai._is_open(PV.get(pid))
ai.forget()

# ---------------------------------------------------------------- AISEC-4: .env key only for built-in local Grok
PV.save_profile({"id": "grok_local_2", "kind": "grok_local", "base_url": f"{BASE}/g2/v1", "model_fast": "m"})
g2 = PV.get("grok_local_2")
assert not ai._has_creds(g2) and not PV.has_key(g2) and ai._key_for(g2) == ""
g1 = PV.get("grok_local")
assert ai._has_creds(g1) and ai._key_for(g1) == os.environ["AI_API_KEY"]
PV.save_profile({"id": "grok_local", "base_url": "https://grok.example.com/v1"})
os.environ.pop("AI_BASE_URL")
assert not ai._has_creds(PV.get("grok_local")) and ai._key_for(PV.get("grok_local")) == "", "remote URL got env key"
os.environ["AI_BASE_URL"] = "http://127.0.0.1:9/v1"
PV.save_profile({"id": "grok_local", "base_url": ""})
PV.remove("grok_local_2")
assert not [r for r in LOG if r[1] == "g2"]

# ---------------------------------------------------------------- AISEC-5: a draft in use is not pruned
PV.save_profile({"id": "draft1", "kind": "groq", "draft": True, "enabled": False})
KS.set_key("draft1", "gsk_test_draft_key_123")
doc = json.loads(PV.profiles_path().read_text(encoding="utf-8"))
next(p for p in doc["profiles"] if p["id"] == "draft1")["created"] = time.time() - 7200   # wizard open 2 h
PV.profiles_path().write_text(json.dumps(doc), encoding="utf-8")
PV.touch_draft("draft1")
PV.snapshot()
assert PV.get("draft1") and KS.has_key("draft1"), "a touched draft lost its key"
doc = json.loads(PV.profiles_path().read_text(encoding="utf-8"))
next(p for p in doc["profiles"] if p["id"] == "draft1").update(touched=time.time() - 7200)
PV.profiles_path().write_text(json.dumps(doc), encoding="utf-8")
PV.snapshot()
assert not PV.get("draft1") and not KS.has_key("draft1"), "an abandoned draft must be pruned"

# ---------------------------------------------------------------- AISEC-6: probe 5xx is not "Terhubung"
MODE["a"] = "503"
PV.set_order(["a"])
ai.forget()
h = ai.health()
row_a = next(r for r in h["route"] if r["id"] == "a")
assert not row_a["ok"] and "HTTP 503" in row_a["why"] and h["profile"]["id"] != "a", h
assert not ai._probe(PV.get("a"))["ok"]
MODE["a"] = "ok"
ai.forget()

# ---------------------------------------------------------------- AISEC-7: 429 in a 200 body / HTML 200
PV.set_order(["a", "b"])
for mode in ("or429", "html"):
    MODE["a"] = mode
    ai.forget()
    n_a = len(posts("a"))
    t0 = time.time()
    obj = ai.chat_json(MSG, schema=SCHEMA, retries=1, trace=tr)
    assert tr["profile"] == "b" and len(posts("a")) == n_a + 1 and time.time() - t0 < 1.5, (mode, tr)
    assert ai._is_open(PV.get("a")), mode
MODE["a"] = "ok"

# ---------------------------------------------------------------- AISEC-8: a successful test closes the breaker
assert ai._is_open(PV.get("a"))
PV.health_put("a", {"ok": True, "tested": True, "test_ok_at": round(time.time() + 0.01, 3)})  # cli.py ai test
time.sleep(0.02)
assert not ai._is_open(PV.get("a")), "worker must honour a newer successful test"
obj = ai.chat_json(MSG, schema=SCHEMA, retries=0, trace=tr)
assert tr["profile"] == "a"

# ---------------------------------------------------------------- AISEC-11: undecryptable key = missing
raw = json.loads(KS.path().read_text(encoding="utf-8"))
raw["keys"]["c"] = base64.b64encode(b"\x01\x00\x00\x00not-a-dpapi-blob-from-this-user").decode("ascii")
KS.path().write_text(json.dumps(raw), encoding="utf-8")
time.sleep(0.05)
pc = PV.get("c")
assert KS.has_key("c") and KS.key_error("c") and not ai._has_creds(pc) and not PV.has_key(pc)
pub = PV.public(pc)
assert pub["has_key"] is False and "simpan ulang" in pub["key_error"], pub
from ac import i18n  # noqa: E402
with i18n.using("en"):   # English UI: the same state comes back in English (AC_LANG=en / job lang)
    pub_en = PV.public(pc)
    assert "save the key again" in pub_en["key_error"], pub_en
    assert "Windows" in ai.test_profile("c")["why"] and "cannot be opened" in ai.test_profile("c")["why"]
PV.set_order(["c", "b"])
ai.forget()
n_c = len(posts("c"))
obj = ai.chat_json(MSG, schema=SCHEMA, retries=0, trace=tr)
assert tr["profile"] == "b" and len(posts("c")) == n_c, "no unauthenticated request with a broken key"
t = ai.test_profile("c")
assert not t["ok"] and "Windows" in t["why"], t
KS.set_key("c", KEY.replace("-a-", "-c-"))
assert not KS.key_error("c") and ai._has_creds(PV.get("c"))

# no key ever reached the second host, and none is in METRICS
assert not [r for r in LOG if r[0] == "127.0.0.2"]
assert all("SECRET" not in json.dumps(r) and "envkey" not in json.dumps(r) for r in ai.METRICS)

srv1.shutdown()
srv2.shutdown()
_common.cleanup("ai_security")
print("ok")
