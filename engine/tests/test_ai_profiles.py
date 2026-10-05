"""AI provider profiles: keystore (DPAPI), profile validation, `cli.py ai ...`, routing + fallback, circuit
breaker, response_format fallback, vision slot, Anthropic adapter, model lists, cache keys per profile+model.
Offline: a fake OpenAI-compatible + Anthropic server runs inside this test (no quota, no real keys).
  python engine/tests/test_ai_profiles.py
  python engine/tests/test_ai_profiles.py --live    + ONE real request to the local Grok proxy (`ai test grok_local`)"""
import json
import os
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import _common
from _common import ENGINE

LIVE = _common.flag("--live")
tmp = _common.scratch("ai_profiles")
REAL_APPDATA = os.environ.get("APPDATA")
os.environ["APPDATA"] = str(tmp / "roaming")          # profiles, keys, health cache, settings: sandboxed
os.environ["LOCALAPPDATA"] = str(tmp / "local")       # ai_cache, logs
os.environ["AI_BASE_URL"] = "http://127.0.0.1:9/v1"   # local Grok profile -> discard port (refused)
os.environ.pop("AI_DISABLED", None)

from ac.ai import client as ai  # noqa: E402
from ac.ai import keystore as KS  # noqa: E402
from ac.ai import prompts as P  # noqa: E402
from ac.ai import providers as PV  # noqa: E402
from ac.ai import tasks  # noqa: E402
from ac import util  # noqa: E402

KEY_A = "sk-test-AAAA-1234567890-abcdef"
KEY_E = "sk-ant-test-EEEE-0987654321"

# ====================================================================== fake provider server
LOG = []          # (provider, method, path, headers, body)
MODE = {}         # provider -> "ok" | "503" | "fmt400" | "garbage" | "echo401" | "nomodels"
EXPECT = {"a": KEY_A, "e": KEY_E}


class Fake(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, obj):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _route(self, method):
        parts = self.path.split("?")[0].strip("/").split("/")
        prov = parts[0]
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n) or b"null") if n else None
        LOG.append((prov, method, self.path, {k.lower(): v for k, v in self.headers.items()}, body))
        mode = MODE.get(prov, "ok")
        want = EXPECT.get(prov)
        if want:
            got = self.headers.get("x-api-key") if prov == "e" else (self.headers.get("Authorization") or "")[7:]
            if got != want:
                if mode == "echo401":
                    return self._send(401, {"error": {"message": f"Incorrect API key provided: {got}"}})
                return self._send(401, {"error": {"message": "bad key"}})
        if method == "GET" and parts[-1] == "models":
            if mode == "nomodels":
                return self._send(404, {"detail": "Not Found"})
            if prov == "e":
                return self._send(200, {"data": [{"type": "model", "id": "claude-test-fast", "display_name": "Claude Test",
                                                  "max_input_tokens": 200000}], "has_more": False})
            if prov == "g":
                return self._send(200, {"object": "list", "data": [{"id": "models/gemini-test-flash", "object": "model"}]})
            return self._send(200, {"data": [
                {"id": "vendor/fast-1", "name": "Fast One", "context_length": 128000,
                 "architecture": {"input_modalities": ["text", "image"]}, "pricing": {"prompt": "0", "completion": "0"}},
                {"id": "vendor/smart-1", "context_length": 1000000, "architecture": {"modality": "text->text"}},
                {"id": "vendor/fast-1", "name": "duplicate"}]})
        if method == "POST" and parts[-1] in ("completions", "messages"):
            if mode == "503":
                return self._send(503, {"error": {"message": "overloaded"}})
            if mode == "fmt400" and body.get("response_format"):
                return self._send(400, {"error": {"message": "response_format is not supported by this model"}})
            text = json.dumps(body)
            if "17 + 25" in text:
                reply = '{"sum": 42}'
            elif "image" in text:
                reply = '{"color": "red"}'
            else:
                reply = '{"chapters": [], "echo": "%s"}' % prov
            if mode == "garbage":
                reply = "maaf, saya tidak bisa"
            if parts[-1] == "messages":
                return self._send(200, {"content": [{"type": "thinking", "thinking": ""}, {"type": "text", "text": reply}],
                                        "stop_reason": "end_turn"})
            return self._send(200, {"choices": [{"message": {"role": "assistant", "content": reply}}]})
        return self._send(404, {"detail": "Not Found"})

    def do_GET(self):
        self._route("GET")

    def do_POST(self):
        self._route("POST")


srv = ThreadingHTTPServer(("127.0.0.1", 0), Fake)
threading.Thread(target=srv.serve_forever, daemon=True).start()
BASE = f"http://127.0.0.1:{srv.server_address[1]}"


def requests_of(prov, method="POST"):
    return [r for r in LOG if r[0] == prov and r[1] == method]


# ====================================================================== 1. backward compat: no profiles file
doc = PV.load()
assert doc["synthesized"] and [p["id"] for p in doc["profiles"]] == ["grok_local"], doc
assert PV.route()[0]["kind"] == "grok_local" and doc["profiles"][0]["model_fast"] == "grok-fast"
cfg = ai.config()
assert "key" not in json.dumps(cfg).lower().replace("has_key", "") and cfg["route"] == ["grok_local"]
assert cfg["base_url"] == "http://127.0.0.1:9/v1"
assert ai.resolve_model(PV.get("grok_local"), "smart") == "grok-auto"
assert ai.resolve_model(PV.get("grok_local"), "grok-3") == "grok-3"            # proxy aliases pass through
assert not PV.profiles_path().exists(), "reading must not create the profiles file"

# ====================================================================== 2. keystore (DPAPI) + redaction
KS.set_key("a", KEY_A)
raw = KS.path().read_text(encoding="utf-8")
assert KEY_A not in raw and "1234567890" not in raw and json.loads(raw)["keys"]["a"]
assert KS.has_key("a") and KS.get_key("a") == KEY_A and not KS.has_key("zz") and KS.get_key("zz") == ""
assert KEY_A in KS.revealed()
assert KEY_A not in util.redact(f"HTTP 401: Incorrect API key provided: {KEY_A} (x)")
assert KS.delete_key("a") and not KS.has_key("a") and not KS.delete_key("a")
for bad in ("short", "has space inside key", ""):
    try:
        KS.set_key("a", bad)
        raise AssertionError("weak key accepted")
    except KS.KeystoreError:
        pass

# ====================================================================== 3. validation
def refused(obj, code):
    try:
        PV.validate(obj)
    except PV.ProfileError as e:
        assert e.code == code, (e.code, code)
        return
    raise AssertionError(f"accepted: {obj}")


refused({"kind": "custom", "base_url": BASE + "/a/v1", "api_key": "x" * 20}, "AI_KEY_IN_PROFILE")
refused({"kind": "custom", "base_url": BASE + "/a/v1", "extra_headers": {"Authorization": "Bearer x"}}, "AI_KEY_IN_PROFILE")
refused({"kind": "custom", "base_url": "ftp://x"}, "AI_BAD_URL")
refused({"kind": "custom", "base_url": "https://user:pw@x.com/v1"}, "AI_BAD_URL")
refused({"kind": "custom", "base_url": "https://x.com/v1?key=abc"}, "AI_BAD_URL")
refused({"kind": "custom"}, "AI_BAD_URL")
refused({"kind": "nope", "base_url": BASE}, "AI_BAD_PROFILE")
refused({"kind": "custom", "base_url": BASE, "id": "Bad Id"}, "AI_BAD_PROFILE")
refused({"kind": "custom", "base_url": BASE, "json_mode": "maybe"}, "AI_BAD_PROFILE")
p, w = PV.validate({"kind": "ollama"})
assert p["base_url"] == "http://127.0.0.1:11434/v1" and p["json_mode"] == "auto" and p["id"] == "ollama"
p, w = PV.validate({"kind": "custom", "base_url": "http://localhost:1234/v1", "model_fast": "m"})
assert p["base_url"] == "http://127.0.0.1:1234/v1" and not w
p, w = PV.validate({"kind": "custom", "base_url": "http://203.0.113.5/v1", "model_fast": "m"})
assert any("http" in x for x in w), w
p, w = PV.validate({"kind": "openrouter"})
assert p["base_url"] == "https://openrouter.ai/api/v1" and p["model_fast"]
assert PV.PRESETS["openrouter"]["headers"]["X-Title"] == "Klipora"

# ====================================================================== 4. `cli.py ai ...` (subprocess, JSON lines)
PY = [sys.executable, "-X", "utf8", str(ENGINE / "cli.py"), "ai"]


def cli(*args, stdin="", ok=True):
    r = subprocess.run(PY + list(args), input=stdin, capture_output=True, text=True, encoding="utf-8",
                       timeout=60, env=dict(os.environ))
    lines = [json.loads(x) for x in r.stdout.splitlines() if x.strip()]
    assert len(lines) == 1, (args, r.stdout[-500:], r.stderr[-800:])
    assert KEY_A not in r.stdout and KEY_A not in r.stderr and KEY_E not in r.stdout, "key leaked by cli"
    ev = lines[0]
    if ok:
        assert r.returncode == 0 and ev["ev"] == "result", (args, ev, r.stderr[-800:])
        return ev["data"]
    assert r.returncode == 1 and ev["ev"] == "error", (args, ev)
    return ev


d = cli("profiles")
assert d["synthesized"] and d["active"] == "grok_local" and len(d["presets"]) == len(PV.PRESETS)
assert {"has_key", "needs_key", "health"} <= set(d["profiles"][0])
assert d["profiles"][0]["name"] == "Proxy lokal (kompatibel OpenAI)" and next(x for x in d["presets"] if x["kind"] == "ollama")["name"] == "Ollama (lokal)"
# English UI (the panel passes AC_LANG to every `cli.py ai` run): preset labels, notes and errors in English
r_en = subprocess.run(PY + ["profiles"], capture_output=True, text=True, encoding="utf-8", timeout=60,
                      env=dict(os.environ, AC_LANG="en"))
d_en = json.loads(r_en.stdout.strip().splitlines()[-1])["data"]
assert d_en["profiles"][0]["name"] == "Local proxy (OpenAI-compatible)", d_en["profiles"][0]
assert next(x for x in d_en["presets"] if x["kind"] == "ollama")["note"].startswith("Models run on this computer")
r_en = subprocess.run(PY + ["remove", "nope"], capture_output=True, text=True, encoding="utf-8", timeout=60,
                      env=dict(os.environ, AC_LANG="en"))
assert json.loads(r_en.stdout.strip().splitlines()[-1])["msg"] == "Profile nope does not exist.", r_en.stdout
p_en, _ = PV.validate({"kind": "grok_local", "name": "Local proxy (OpenAI-compatible)"})
assert p_en["name"] == "Proxy lokal (kompatibel OpenAI)", "a translated preset default is stored as the default"
p_old, _ = PV.validate({"kind": "grok_local", "name": "Grok lokal"})
assert p_old["name"] == "Proxy lokal (kompatibel OpenAI)", "a legacy preset name is stored as the current default"
assert PV.display_name({"kind": "grok_local", "name": "Grok lokal"}) == PV.preset_text("grok_local", "name"), "legacy name follows the UI"
d = cli("save", stdin=json.dumps({"id": "a", "name": "Fake A", "kind": "custom", "base_url": BASE.replace("127.0.0.1", "localhost") + "/a/v1",
                                  "model_fast": "vendor/fast-1", "model_smart": "vendor/smart-1",
                                  "vision_model": "vendor/fast-1", "json_mode": "on"}))
assert d["saved"] == "a" and d["order"] == ["grok_local", "a"] and d["profile"]["has_key"] is False
assert d["profile"]["base_url"].startswith("http://127.0.0.1:")
e = cli("save", stdin=json.dumps({"id": "a", "kind": "custom", "base_url": BASE, "key": KEY_A}), ok=False)
assert e["code"] == "AI_KEY_IN_PROFILE" and KEY_A not in json.dumps(e)
e = cli("models", "a", ok=False)                 # custom: key optional, so the server's 401 is reported
assert e["code"] == "AI_AUTH", e
e = cli("save", stdin=json.dumps({"id": "k", "kind": "groq"}))
e = cli("models", "k", ok=False)                 # preset that needs a key: refused before any request
assert e["code"] == "AI_NO_KEY", e
cli("remove", "k")
d = cli("set-key", "a", stdin=KEY_A + "\n")
assert d == {"id": "a", "has_key": True}
assert KEY_A not in PV.profiles_path().read_text(encoding="utf-8") and KEY_A not in KS.path().read_text(encoding="utf-8")
d = cli("models", "a")
assert d["n"] == 2 and d["models"][0] == {"id": "vendor/fast-1", "name": "Fast One", "ctx": 128000, "vision": True, "free": True}
assert d["models"][1] == {"id": "vendor/smart-1", "ctx": 1000000, "vision": False}
assert requests_of("a", "GET")[-1][3]["authorization"] == "Bearer " + KEY_A
d = cli("test", "a", "--vision")
assert d["ok"] and d["reach"]["ok"] and d["chat"]["right"] and d["chat"]["format"] == "response_format", d
assert d["vision"]["ok"] and d["vision"]["model"] == "vendor/fast-1", d
body = requests_of("a")[-1][4]
assert body["messages"][-1]["content"][1]["image_url"]["url"].startswith("data:image/png;base64,")
d = cli("profiles")
pa = next(p for p in d["profiles"] if p["id"] == "a")
assert pa["has_key"] and pa["health"]["ok"] and pa["health"]["tested"] and pa["health"]["vision"] is True
assert KEY_A not in json.dumps(d)
d = cli("activate", "a")
assert d["order"] == ["a", "grok_local"]
d = cli("order", stdin=json.dumps(["grok_local", "a", "missing"]))
assert d["order"] == ["grok_local", "a"]
e = cli("set-key", "nope", stdin=KEY_A, ok=False)
assert e["code"] == "AI_NO_PROFILE"
e = cli("bogus", ok=False)
assert e["code"] == "AI_BAD_ARGS"
# draft profile (wizard) is not routed, and a stale draft is pruned with its key
d = cli("save", stdin=json.dumps({"id": "draft1", "kind": "groq", "draft": True, "enabled": False}))
assert "draft1" not in d["order"]
cli("set-key", "draft1", stdin="gsk_test_draft_key_123\n")
doc = json.loads(PV.profiles_path().read_text(encoding="utf-8"))
next(p for p in doc["profiles"] if p["id"] == "draft1").update(created=time.time() - 7200, touched=time.time() - 7200)
PV.profiles_path().write_text(json.dumps(doc), encoding="utf-8")
d = cli("profiles")
assert "draft1" not in [p["id"] for p in d["profiles"]] and not KS.has_key("draft1")

# ====================================================================== 5. routing + fallback (in-process)
PV.reset_cache()
ai.forget()
for pid, obj in {
    "b": {"name": "Fake B", "kind": "custom", "base_url": BASE + "/b/v1", "model_fast": "b-fast", "model_smart": "b-smart"},
    "c": {"name": "Fake C", "kind": "custom", "base_url": BASE + "/c/v1", "model_fast": "c-fast", "json_mode": "on"},
    "e": {"name": "Fake Claude", "kind": "anthropic", "base_url": BASE + "/e/v1", "model_fast": "claude-test-fast",
          "model_smart": "claude-test-smart", "vision_model": "claude-test-vision"},
    "g": {"name": "Fake Gemini", "kind": "gemini", "base_url": BASE + "/g/v1", "model_fast": "gemini-test-flash"},
}.items():
    PV.save_profile(dict(obj, id=pid))
KS.set_key("e", KEY_E)
KS.set_key("g", "AIza-test-gggggggggggg")
SCHEMA = {"type": "object", "required": ["sum"]}
MSG = [{"role": "system", "content": "test"}, {"role": "user", "content": "Compute 17 + 25"}]

# Grok (dead port) first -> A answers; the served profile/model is visible in METRICS
PV.set_order(["grok_local", "a", "b", "c", "e", "g"])
assert ai.available()
mark = ai.metrics_mark()
t0 = time.time()
tr = {}
obj = ai.chat_json(MSG, schema=SCHEMA, model="smart", retries=0, trace=tr)
assert obj == {"sum": 42} and tr["profile"] == "a" and tr["model"] == "vendor/smart-1", tr
assert time.time() - t0 < 8, time.time() - t0
srv_by = ai.served_since(mark)
assert srv_by[0] == "a" and srv_by[1] == "vendor/smart-1" and srv_by[2] == ["grok_local"], srv_by
assert requests_of("a")[-1][4]["response_format"] == {"type": "json_object"}          # json_mode "on"
# grok aliases from old tool code map to the slot on other providers
obj = ai.chat_json(MSG, schema=SCHEMA, model="grok-auto", retries=0, trace=tr)
assert tr["model"] == "vendor/smart-1"
obj = ai.chat_json(MSG, schema=SCHEMA, model="grok-fast", retries=0, trace=tr)
assert tr["model"] == "vendor/fast-1"
h = ai.health()
assert h["ok"] and h["profile"]["id"] == "a" and "Proxy lokal (kompatibel OpenAI)" in h.get("note", "") and len(h["route"]) == 6, h
assert [r["ok"] for r in h["route"]][:2] == [False, True]

# B overloaded (503) -> next provider; 3 failures open B's circuit and it is skipped without a request
PV.set_order(["b", "a"])
ai.forget()
MODE["b"] = "503"
for _ in range(3):
    obj = ai.chat_json(MSG, schema=SCHEMA, retries=0, trace=tr)
    assert tr["profile"] == "a" and tr["tried"][0]["profile"] == "b"
n_b = len(requests_of("b"))
obj = ai.chat_json(MSG, schema=SCHEMA, retries=0, trace=tr)
assert tr["profile"] == "a" and len(requests_of("b")) == n_b and not tr["tried"], "open circuit must skip b"
MODE["b"] = "ok"
PV.save_profile({"id": "b", "model_smart": "b-smart-2"})   # editing the profile (or its key) closes the circuit
obj = ai.chat_json(MSG, schema=SCHEMA, retries=0, trace=tr)
assert tr["profile"] == "b" and len(requests_of("b")) == n_b + 1, tr
# garbage from the primary (after one repair) -> fallback
MODE["b"] = "garbage"
obj = ai.chat_json(MSG, schema=SCHEMA, retries=0, trace=tr)
assert tr["profile"] == "a" and len(tr["raw"]) >= 3, tr       # b: reply + repair reply, then a
MODE["b"] = "ok"
ai.forget()

# server without JSON mode: response_format refused once -> prompt-only from then on
PV.set_order(["c"])
MODE["c"] = "fmt400"
obj = ai.chat_json(MSG, schema=SCHEMA, retries=0, trace=tr)
reqs = requests_of("c")
assert obj == {"sum": 42} and "response_format" in reqs[-2][4] and "response_format" not in reqs[-1][4]
assert tr["json_format"] == "prompt" and "c" in ai._NO_FMT
KS.set_key("c", "sk-new-key-for-c-123456")               # a new key retries JSON mode once more
ai.chat_json(MSG, schema=SCHEMA, retries=0)
assert "response_format" in requests_of("c")[-2][4] and "response_format" not in requests_of("c")[-1][4]
ai.chat_json(MSG, schema=SCHEMA, retries=0)
assert "response_format" not in requests_of("c")[-1][4]
MODE["c"] = "ok"

# vision: profiles without a vision model are skipped, the image goes to the vision model
PV.set_order(["b", "a"])
obj = ai.chat_vision('What colour? Reply {"color": "..."}', [ai._png()], schema={"type": "object"}, retries=0, trace=tr)
assert obj == {"color": "red"} and tr["profile"] == "a" and tr["model"] == "vendor/fast-1", tr
part = ai.image_part(ai._png())
assert part["image_url"]["url"].startswith("data:image/png;base64,")
try:
    ai.image_part(b"not an image at all")
    raise AssertionError("garbage accepted as image")
except ai.AIError:
    pass

# Anthropic native adapter: x-api-key + anthropic-version, system field, image blocks, text block extraction
PV.set_order(["e"])
obj = ai.chat_json(MSG, schema=SCHEMA, model="fast", retries=0, trace=tr)
prov, method, path, hdr, body = requests_of("e")[-1]
assert obj == {"sum": 42} and path.endswith("/e/v1/messages") and tr["model"] == "claude-test-fast"
assert hdr.get("x-api-key") == KEY_E and hdr.get("anthropic-version") == "2023-06-01" and "authorization" not in hdr
assert "OUTPUT FORMAT" in body["system"] and body["messages"][0]["role"] == "user" and body["max_tokens"] > 0
obj = ai.chat_vision("What colour?", [ai._png()], schema={"type": "object"}, retries=0, trace=tr)
img = requests_of("e")[-1][4]["messages"][0]["content"][1]
assert img["type"] == "image" and img["source"]["type"] == "base64" and img["source"]["media_type"] == "image/png"
assert tr["model"] == "claude-test-vision"
m = ai.list_models("e")
assert m["models"] == [{"id": "claude-test-fast", "name": "Claude Test", "ctx": 200000}]
assert "limit=" in requests_of("e", "GET")[-1][2]
m = ai.list_models("g")
assert [x["id"] for x in m["models"]] == ["gemini-test-flash"]          # "models/" prefix stripped
MODE["b"] = "nomodels"
m = ai.list_models("b")
assert m["unsupported"] and m["models"] == []
assert ai.test_profile("b", quick=True)["ok"]                           # 404 /models still means "reachable"
MODE["b"] = "ok"

# a key echoed back by a provider never reaches errors/logs
PV.save_profile({"id": "x", "kind": "custom", "base_url": BASE + "/a/v1", "model_fast": "m"})
KS.set_key("x", "sk-wrong-key-zzzzzzzzzz")
MODE["a"] = "echo401"
try:
    ai.chat_json(MSG, profile="x", retries=0)
    raise AssertionError("401 must raise")
except ai.AIUnavailable as e:
    assert "sk-wrong-key-zzzzzzzzzz" not in str(e)
assert all("sk-wrong-key" not in json.dumps(r) for r in ai.METRICS)
MODE["a"] = "ok"
PV.remove("x")
assert not KS.has_key("x")

# ====================================================================== 6. tasks: cache key = profile + model
real_chapters = P.chapters


def fake_chapters(sents, dur, model=None, trace=None, glossary=()):
    ai.chat_json(MSG, schema=SCHEMA, model=model, retries=0, tag="chapters")
    return {"source": "ai", "result": {"chapters": [{"start": 0.0, "title": "Intro"}], "youtube": "00:00 Intro"},
            "warnings": [], "stats": {}}


words = [["Halo", 0.0, 0.4, False], ["semua.", 0.4, 0.9, True], ["Ini", 1.2, 1.4, False], ["tes.", 1.4, 1.9, True]]
P.chapters = fake_chapters
try:
    PV.set_order(["grok_local", "a"])
    ai.forget()
    r1 = tasks.run("chapters", words)
    assert r1["source"] == "ai" and r1["profile"] == "a" and r1["model"] == "vendor/fast-1", r1
    assert any("Proxy lokal (kompatibel OpenAI) gagal" in w for w in r1["warnings"]), r1["warnings"]
    n_a = len(requests_of("a"))
    r2 = tasks.run("chapters", words)
    assert r2["source"] == "cache" and len(requests_of("a")) == n_a       # served from the a:model key
    assert not any("gagal" in w for w in r2["warnings"]), "one-off provider note must not be cached"
    PV.set_order(["b"])
    PV.save_profile({"id": "a", "enabled": False})       # a's cached answer is only reused while a is in the route
    r3 = tasks.run("chapters", words)
    assert r3["source"] == "ai" and r3["profile"] == "b" and r3["model"] == "b-fast", r3
    PV.save_profile({"id": "a", "enabled": True})
    store = ai.Cache()
    k_a = store.key("chapters", P.PROMPT_VERSION, "a:vendor/fast-1", [], "natural", 5, "youtube", "Video", None,
                    tasks._compact(words))
    assert store.get(k_a)["profile"] == "a"
    assert tasks._model_part(("grok_local", "grok-fast"), "fast") == "grok-fast"    # old Grok cache keys still hit
    PV.set_order(["a", "b"])                     # a answered before: its cached answer is reused, b is not asked
    n_b = len(requests_of("b"))
    ai.forget()
    r4 = tasks.run("chapters", words)
    assert r4["source"] == "cache" and r4["profile"] in ("a", "b") and len(requests_of("b")) == n_b, r4
    PV.set_order(["b", "a"])
    r5 = tasks.run("chapters", words)
    assert r5["source"] == "cache" and r5["profile"] == "b", r5            # the first provider's own answer wins
    assert tasks.DEFAULT_MODEL["viral"] == "smart" and tasks.DEFAULT_MODEL["chapters"] == "fast"
finally:
    P.chapters = real_chapters
# everything down -> rule fallback, never cached (route = enabled profiles only)
before = len(list(ai.Cache().folder.glob("*.json")))
for p in PV.profiles():
    PV.save_profile({"id": p["id"], "enabled": p["id"] == "grok_local"})
assert [p["id"] for p in PV.route()] == ["grok_local"]
ai.forget()
r = tasks.run("chapters", words, cache=True)
assert r["source"] == "fallback" and r["model"] == "grok-fast" and r["profile"] == "grok_local", r
assert len(list(ai.Cache().folder.glob("*.json"))) == before, "fallbacks must not be cached"

# ====================================================================== 7. AI switched off in Pengaturan
(tmp / "roaming" / "Klipora" / "settings.json").write_text(json.dumps({"ai": False}), encoding="utf-8")
PV.activate("a")
assert [p["id"] for p in PV.route()] == ["a", "grok_local"]
assert not ai.available() and ai.config()["disabled"] and ai.health()["why"] == "AI dimatikan di pengaturan"
try:
    ai.chat_json(MSG, retries=0)
    raise AssertionError("disabled AI must raise")
except ai.AIUnavailable:
    pass
assert ai.test_profile("a")["ok"], "an explicit connection test still works while AI is off"
(tmp / "roaming" / "Klipora" / "settings.json").unlink()

# ====================================================================== 8. live: ONE request to the real Grok proxy
if LIVE:
    env = dict(os.environ, APPDATA=REAL_APPDATA or "")
    env.pop("AI_BASE_URL", None)
    r = subprocess.run(PY + ["test", "grok_local"], capture_output=True, text=True, encoding="utf-8", timeout=200, env=env)
    ev = json.loads(r.stdout.splitlines()[-1])
    print("live grok:", json.dumps(ev["data"], ensure_ascii=False)[:400])
    assert ev["ev"] == "result" and ev["data"]["ok"] and ev["data"]["chat"]["right"], ev

srv.shutdown()
_common.cleanup("ai_profiles")
print("ok")
