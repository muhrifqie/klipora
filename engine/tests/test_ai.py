"""ac.ai: JSON extraction/validation, offline degradation (dead port, no quota), cache behaviour.
  python engine/tests/test_ai.py          offline only (spends NO Grok quota)
  python engine/tests/test_ai.py --live   + health and one real chapters call on the 49 s transcript"""
import json
import os
import time

import _common
from _common import MEDIA_49, MEDIA_GLOSSARY

LIVE = _common.flag("--live")
tmp = _common.scratch("ai")
os.environ["LOCALAPPDATA"] = str(tmp / "local")      # isolated ai_cache
os.environ["APPDATA"] = str(tmp / "roaming")
if not LIVE:
    os.environ["AI_BASE_URL"] = "http://127.0.0.1:9/v1"   # discard port: connection refused

from ac.ai import client as ai  # noqa: E402
from ac.ai import prompts as P  # noqa: E402
from ac.ai import tasks  # noqa: E402
from ac.ai.text import display_words, parse_ts, sentences, ts  # noqa: E402
from ac.progress import ListEmitter  # noqa: E402
from ac import transcript as T  # noqa: E402

# ---- JSON extraction (cases seen live from the proxy)
cases = {
    '{"a":1}': {"a": 1},
    'Sure!\n```json\n{"a": [1,2]}\n```\nDone': {"a": [1, 2]},
    '<think>{"x":0}</think>{"a":2}': {"a": 2},
    'See [1]. {"a":4} more text': {"a": 4},
    '{"a":[1,2,],}': {"a": [1, 2]},
    '{“a”: “b”}': {"a": "b"},
    '{"a": True, "b": None}': {"a": True, "b": None},
    '{"id": 3.0}': {"id": 3},
    '{"titles":["a","b"]': {"titles": ["a", "b"]},
    '```json\n{"x":{"y":[1,2': {"x": {"y": [1, 2]}},
}
for raw, want in cases.items():
    assert ai.extract_json(raw, expect=dict) == want, raw
try:
    ai.extract_json("no json here", expect=dict)
    raise AssertionError("should fail")
except ValueError:
    pass
errs = ai.validate_schema({"chapters": [{"start": "1:00x", "title": "x"}]}, P.CHAPTERS_SCHEMA)
assert any("does not match" in e for e in errs) and any("missing 'quote'" in e for e in errs), errs
assert ts(3725) == "1:02:05" and ts(65.5, frac=True) == "01:05.5" and parse_ts("1:02:03") == 3723

# ---- config never exposes the key; base URL forced to 127.0.0.1
cfg = ai.config()
assert "key" not in json.dumps(cfg).lower().replace("has_key", "")
assert "localhost" not in cfg["base_url"]
h = ai.health()
assert set(h) >= {"ok", "ms", "base_url", "model", "has_key", "disabled"}
if ai._ENV.get("AI_API_KEY"):
    assert ai._ENV["AI_API_KEY"] not in json.dumps(h)

if not MEDIA_49.is_file() or T.cached_words(MEDIA_49) is None:
    print("SKIP transcript-based checks")
    raise SystemExit(0)
words = T.cached_words(MEDIA_49)
d = display_words(words)
s = sentences(d)
assert len(d) == len(words) and d[5]["i"] == 5          # v3: display index == cache row index
assert 3 <= len(s) <= 30

if not LIVE:
    # ---- proxy down: every task falls back fast and never raises
    assert h["ok"] is False
    t0 = time.time()
    for name in tasks.TASKS:
        em = ListEmitter()
        r = tasks.run(name, words, glossary=MEDIA_GLOSSARY, emit=em)
        assert r["source"] in ("fallback", "none", "mixed"), (name, r["source"])
        assert "result" in r and r["model"]
        if name == "chapters":
            assert r["result"]["chapters"][0]["start"] == 0.0
    assert time.time() - t0 < 15, time.time() - t0
    assert not list((tmp / "local" / "Klipora" / "ai_cache").glob("*.json")), "fallbacks must not be cached"
    try:
        ai.chat([{"role": "user", "content": "hi"}])
        raise AssertionError("chat should raise when the proxy is down")
    except ai.AIUnavailable:
        pass
    # timeline-word dicts are accepted too
    tw = [{"text": w[0], "t0": w[1] + 100, "t1": w[2] + 100, "seg": w[3]} for w in words]
    r = tasks.run("chapters", tw)
    assert r["result"]["chapters"][0]["start"] == 0.0 or r["result"]["chapters"][0]["start"] >= 100
    # cache hit path (simulate an AI result already stored)
    store = ai.Cache()
    key = store.key("x", 1)
    store.put(key, {"source": "ai", "result": {"v": 1}})
    assert store.get(key)["result"] == {"v": 1}
    print("offline fallbacks ok")
else:
    assert h["ok"], f"proxy not reachable: {h}"
    t0 = time.time()
    r = tasks.run("chapters", words, glossary=MEDIA_GLOSSARY, cache=False)
    secs = time.time() - t0
    assert r["source"] in ("ai", "fallback"), r["source"]
    print(f"live chapters: source={r['source']} in {secs:.1f} s -> {r['result'].get('youtube', '')[:200]}")
    r2 = tasks.run("chapters", words, glossary=MEDIA_GLOSSARY)
    r3 = tasks.run("chapters", words, glossary=MEDIA_GLOSSARY)
    if r2["source"] == "ai":
        assert r3["source"] == "cache"
_common.cleanup("ai")
print("ok")
