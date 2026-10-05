"""Auto Chapters (engine/ac/tools/chapters.py): rules, rule fallback on real media, mocked parallel AI, apply, CLI.
  python engine/tests/test_chapters.py          offline (cached transcripts, NO Grok quota, no GPU)
  python engine/tests/test_chapters.py --live   + one real analyze (2 min file) and one meta call (~2-3 requests)"""
import json
import os
import re
import subprocess
import sys
import threading

import _common
from _common import ENGINE, MEDIA_2M, MEDIA_34M, MEDIA_49

LIVE = _common.flag("--live")
tmp = _common.scratch("chapters")
os.environ["LOCALAPPDATA"] = str(tmp / "local")      # isolated ai_cache / logs
os.environ["APPDATA"] = str(tmp / "roaming")          # default settings (ai on, no glossary)
if not LIVE:
    os.environ["AI_BASE_URL"] = "http://127.0.0.1:9/v1"   # discard port: the proxy is "down"

from ac import i18n  # noqa: E402
from ac import review as RV  # noqa: E402
from ac import transcript as T  # noqa: E402
from ac.ai import client as ai  # noqa: E402
from ac.progress import ListEmitter  # noqa: E402
from ac.timeline import Timeline  # noqa: E402
from ac.tools import chapters as C  # noqa: E402
from ac.util import EngineError  # noqa: E402

# ---------------------------------------------------------------- pure rules
assert C.target_count(120, "normal") == 3 and C.target_count(2075, "normal") == 9
assert C.target_count(2075, "less") == 5 and C.target_count(2075, "more") == 14 and C.target_count(9000, "more") == 24
assert C.target_count(600, 7) == 7 and C.target_count(600, "4") == 4
assert C.clean_title(' 1. "Daftar Akun Reseller". ') == "Daftar Akun Reseller"
assert C.clean_title("Bab 3: Atur Harga — Produk") == "Atur Harga - Produk"
assert C.yt_time(65.9) == "01:05" and C.yt_time(3725, True) == "1:02:05"
chs = [{"t": 0.0, "title": "A"}, {"t": 30.0, "title": "B"}, {"t": 3700.0, "title": "C"}]
assert C.youtube_text(chs) == "0:00:00 A\n0:00:30 B\n1:01:40 C"          # >= 1 h: every line h:mm:ss
assert C.youtube_text([dict(c, t=c["t"] + 100) for c in chs[:2]], offset=100) == "00:00 A\n00:30 B"
codes = [i["code"] for i in C.youtube_issues([{"t": 5.0, "title": "A"}, {"t": 12.0, "title": ""}], 0.0, 60.0)]
assert codes == ["FEW", "FIRST", "SHORT", "EMPTY"], codes
assert C.youtube_issues([{"t": 0.0, "title": "A"}, {"t": 10.0, "title": "B"}, {"t": 20.0, "title": "C"}], 0, 30) == []
# merge_to_count: deterministic, <= n + 1, keeps the longer chapter's title, never below 3
many = [{"t": float(t), "title": f"T{t}"} for t in (0, 40, 45, 100, 160, 170, 230, 290)]
m = C.merge_to_count(many, 300.0, 4, 20.0)
assert len(m) <= 5 and m[0]["t"] == 0.0 and all(b["t"] - a["t"] >= 20 for a, b in zip(m, m[1:])), m
assert C.merge_to_count(many, 300.0, 4, 20.0) == m
assert len(C.merge_to_count(many[:3], 300.0, 1, 200.0)) == 3
# hallucination filter
assert not C._is_real("Terima kasih telah menonton.") and not C._is_real("menonton.")
assert not C._is_real("ini ini ini ini ini") and C._is_real("Terus kita klik daftar di sini")

if not all(T.cached_words(p) for p in (MEDIA_49, MEDIA_2M, MEDIA_34M) if p.is_file()) or not _common.have(MEDIA_49):
    print("SKIP media checks (no cached transcripts)")
    _common.cleanup("chapters")
    print("ok")
    raise SystemExit(0)


def job(media, action="analyze", seq=None, **params):
    return {"tool": "chapters", "action": action, "seq": seq or Timeline.from_media(media).to_json(),
            "params": params, "workdir": str(tmp / "wd")}


def check_valid(res, lo=0.0):
    chs = res["chapters"]
    assert chs[0]["t"] == lo, chs[0]
    assert all(b["t"] - a["t"] >= 10 for a, b in zip(chs, chs[1:])), chs
    assert all(c["title"].strip() for c in chs)
    doc = RV.load(res["review"])
    assert doc["tool"] == "chapters" and doc["timebase"] == "sequence" and len(doc["items"]) == len(chs)
    assert all(it["kind"] == "chapter" and it["on"] and it["t1"] > it["t0"] for it in doc["items"])
    assert doc["youtube"] == res["youtube"] and res["youtube"].startswith("00:00 ")
    return doc


# ---------------------------------------------------------------- rule fallback (proxy down)
real_available, real_chat = ai.available, ai.chat_json
if not LIVE:
    em = ListEmitter()
    res = C.analyze(job(MEDIA_34M, count="normal"), em)
    doc = check_valid(res)
    assert res["source"] == "rule" and res["target"] == 9 and 5 <= len(res["chapters"]) <= 10, res["summary"]
    assert all(it["generic"] for it in doc["items"]) and res["issues"] == []
    assert any("AI" in w for w in res["warnings"]) and "warn" in em.kinds() and "error" not in em.kinds()
    assert [e["stage"] for e in em.items if e["ev"] == "stage"] == ["words", "units", "find", "check"]
    assert any(e["ev"] == "stage" and e["label"] == "Cari pergantian topik" for e in em.items)
    # keyword titles: words from the chapter, no filler/function words
    titles = " ".join(c["title"] for c in res["chapters"]).lower()
    assert not re.search(r"\b(yang|nanti|klik|sorry|udah|kalian)\b", titles), titles
    n_less = len(C.analyze(job(MEDIA_34M, count="less"), ListEmitter())["chapters"])
    n_more = len(C.analyze(job(MEDIA_34M, count="more"), ListEmitter())["chapters"])
    assert n_less < len(res["chapters"]) < n_more, (n_less, len(res["chapters"]), n_more)
    # short media still valid (the 49 s file reaches exactly 3 x >= 10 s)
    r49 = C.analyze(job(MEDIA_49), ListEmitter())
    check_valid(r49)
    # params.ai = false -> rule path even when the proxy would answer
    r2m = C.analyze(job(MEDIA_2M, ai=False), ListEmitter())
    check_valid(r2m)
    assert r2m["source"] == "rule" and "dimatikan" in r2m["warnings"][0]

# ---------------------------------------------------------------- In/Out scope + cut sequence (sequence time)
if not LIVE:
    rs = C.analyze(job(MEDIA_34M, scope={"kind": "inout", "t0": 600.0, "t1": 1500.0}), ListEmitter())
    check_valid(rs, lo=600.0)
    assert all(600 <= c["t"] < 1500 for c in rs["chapters"]) and rs["offset"] == 600.0 and rs["end"] == 1500.0
    kept = [[1.62, 2.56], [2.96, 7.47], [8.46, 10.36], [10.59, 11.5], [15.17, 17.92], [19.49, 22.51], [23.48, 24.98],
            [25.65, 26.85], [27.2, 29.96], [31.06, 31.79], [32.02, 36.61], [38.14, 40.84], [41.65, 42.46]]
    clips, t = [], 0.0
    for a, b in kept:
        clips.append({"name": "x", "path": str(MEDIA_49), "start": t, "end": t + b - a, "in": a, "out": b})
        t += b - a
    cut = {"id": "cut", "name": "Cut", "fps": 120, "duration": t, "video": [],
           "audio": [{"index": 0, "name": "A1", "clips": clips}]}
    rc = C.analyze(job(MEDIA_49, seq=cut), ListEmitter())
    assert rc["chapters"][0]["t"] == 0.0 and abs(rc["end"] - t) < 1e-3, (rc["end"], t)
    assert all(c["t"] < t for c in rc["chapters"])
    try:
        vid = [{"index": 0, "name": "V1", "clips": [{"name": "g", "path": "", "start": 0, "end": 10, "in": 0, "out": 10}]}]
        C.analyze(job(MEDIA_49, seq={"id": "e", "name": "E", "fps": 30, "duration": 10, "video": vid, "audio": []}),
                  ListEmitter())
        raise AssertionError("no audio must fail")
    except EngineError as e:
        assert e.code == "NO_AUDIO"

# ---------------------------------------------------------------- mocked AI: parallel parts, resolve, merge, polish
if not LIVE:
    calls, lock = [], threading.Lock()
    fail_part = {"k": None}

    def fake_chat(messages, schema=None, model=None, tag="", timeout=None, **kw):
        user = messages[-1]["content"]
        with lock:
            calls.append(tag)
        if tag == "chapters_titles":
            rows = [ln for ln in user.splitlines() if " | " in ln]
            return {"chapters": [{"start": ln.split(" | ")[0], "title": f"Judul Rapi {k + 1}"} for k, ln in enumerate(rows)]}
        m = re.search(r"bagian (\d+) dari (\d+)", user)
        if fail_part["k"] is not None and m and int(m.group(1)) == fail_part["k"]:
            raise ai.AIError("HTTP 502 (mock)")
        lines = [ln for ln in user.splitlines() if re.match(r"^\d{1,2}:\d{2}(:\d{2})? ", ln)]
        out = []
        for k, ln in enumerate(lines[::4]):     # a "topic" every 4th line (~2 min), quote = first words
            stamp, text = ln.split(" ", 1)
            out.append({"start": stamp, "quote": " ".join(text.split()[:4]), "title": f"{k + 1}. Langkah {stamp}."})
        return {"chapters": out}

    ai.available, ai.chat_json = (lambda *a, **k: True), fake_chat
    try:
        em = ListEmitter()
        res = C.analyze(job(MEDIA_34M, count="normal", hint="per langkah setup"), em)
        doc = check_valid(res)
        st = res["stats"]["ai"]
        assert res["source"] == "ai" and st["parts"] == 3 and st["ok"] == 3 and st["failed"] == 0, st
        assert calls.count("chapters_part") == 3 and calls.count("chapters_titles") == 1, calls
        assert st["resolved"].get("ok", 0) >= st["raw"] * 0.8, st
        assert len(res["chapters"]) <= res["target"] + 1, (len(res["chapters"]), res["target"])
        assert all(c["title"].startswith("Judul Rapi") for c in res["chapters"]), res["chapters"]   # polished
        assert not any(it["generic"] for it in doc["items"])
        assert res["chapters"][-1]["t"] > 1500, "parts must cover the whole video (no front-loading)"
        # second run: every call answered from the AI cache, zero requests
        calls.clear()
        em2 = ListEmitter()
        res2 = C.analyze(job(MEDIA_34M, count="normal", hint="per langkah setup"), em2)
        assert calls == [] and res2["chapters"] == res["chapters"], calls
        assert any(e["ev"] == "stage_done" and e.get("note") == "dari cache AI" for e in em2.items)
        # one part fails -> mixed: that part falls back to rules, the rest stays AI
        calls.clear()
        fail_part["k"] = 2
        r3 = C.analyze(job(MEDIA_34M, count="more", hint="lain"), ListEmitter())
        check_valid(r3)
        assert r3["source"] == "mixed" and r3["stats"]["ai"]["failed"] == 1 and r3["warnings"], r3["stats"]["ai"]
        fail_part["k"] = None
        # the model under-segments (1 chapter for a 3-step talk) -> topped up to YouTube's 3 with flagged rule picks
        few = {"on": True}
        real_fake = fake_chat

        def stingy(messages, **kw):
            if few["on"] and kw.get("tag") == "chapters_part":
                return {"chapters": [{"start": "00:00", "quote": "", "title": "Pengenalan dan Daftar Reseller"}]}
            return real_fake(messages, **kw)
        ai.chat_json = stingy
        r4 = C.analyze(job(MEDIA_2M), ListEmitter())
        check_valid(r4)
        assert len(r4["chapters"]) >= 3 and r4["stats"]["ai"].get("topped_up", 0) >= 2, r4["stats"]["ai"]
        assert r4["chapters"][0]["title"] == "Pengenalan dan Daftar Reseller" and not r4["chapters"][0]["generic"]
        assert all(c["generic"] for c in r4["chapters"][1:]) and r4["source"] == "ai" and r4["warnings"]
        ai.chat_json = fake_chat
        # retitle with the live list (panel sends params.chapters)
        rt = C.retitle(job(MEDIA_34M, "retitle", chapters=res["chapters"][:4], style="netral"), ListEmitter())
        assert len(rt["titles"]) == 4 and rt["titles"][0].startswith("Judul Rapi")
    finally:
        ai.available, ai.chat_json = real_available, real_chat

# ---------------------------------------------------------------- apply (edited review -> markers plan + txt)
res = C.analyze(job(MEDIA_34M, ai=False), ListEmitter())
doc = RV.load(res["review"])
items = doc["items"]
items[0]["t0"] = 4.0                                     # user moved the first chapter (YouTube needs 00:00)
items[1]["title"] = items[1]["label"] = " Daftar Domain dan Bayar. "
items[2]["t0"] = items[2]["t0"] + 3.5                   # time adjusted
del items[3]                                             # deleted (merged into the previous one)
items[-1]["on"] = False                                  # or switched off
edited = tmp / "edited.json"
edited.write_text(json.dumps(doc), encoding="utf-8")
ja = job(MEDIA_34M, "apply")
ja["review"] = str(edited)
em = ListEmitter()
ap = C.apply(ja, em)
plan = ap["plan"]
assert plan["kind"] == "markers" and plan["tag"] == "[Klipora-CH]" and plan["timebase"] == "sequence"
mk = plan["markers"]
assert len(mk) == len(items) - 1 == ap["n"]
assert mk[0]["t"] == 0.0 and ap["notes"] and "00:00" in ap["notes"][0]
assert all(m["color"] == 7 and m["type"] == "Chapter" and m["tag"] == "[Klipora-CH]" for m in mk)
assert all(a["end"] == b["t"] for a, b in zip(mk, mk[1:])) and abs(mk[-1]["end"] - doc["end"]) < 1e-3
assert mk[1]["name"] == "Daftar Domain dan Bayar" and all(m["comment"] for m in mk)
assert abs(mk[2]["t"] - (doc["items"][2]["t0"])) < 1e-6
txt = open(ap["txt"], encoding="utf-8").read()
assert txt.startswith("00:00 ") and "Daftar Domain dan Bayar" in txt and txt.count("\n") == len(mk)
assert ap["youtube_ok"] and ap["youtube"] == txt.strip()
# English job language: user-facing text (notes, summary, default titles) follows the job, prompts do not
doc["items"] = [dict(it, title="", label="") if k == 1 else it for k, it in enumerate(items)]
edited.write_text(json.dumps(doc), encoding="utf-8")
with i18n.using("en"):
    ap_en = C.apply(ja, ListEmitter())
    assert ap_en["summary"] == f"{ap_en['n']} chapters ready", ap_en["summary"]
    assert ap_en["notes"][0].startswith("The first chapter was moved from "), ap_en["notes"]
    assert ap_en["plan"]["markers"][1]["name"] == "Chapter 2", ap_en["plan"]["markers"][1]
    assert C.youtube_issues([{"t": 0.0, "title": "A"}], 0.0, 60.0)[0]["msg"].startswith("YouTube needs at least 3 chapters")
    assert C.part_messages([{"start": 0.0, "end": 30.0, "text": "x"}], 0, 1, 30.0, 1)[0]["content"].startswith("Kamu editor")
assert C.apply(ja, ListEmitter())["summary"].endswith("bab siap")   # back to the default (id)
assert C.has_tag("Pembuka\n[Klipora-CH]") and C.has_tag("lama [AC-CH]") and not C.has_tag("[AC-VR]")
doc["items"] = []
edited.write_text(json.dumps(doc), encoding="utf-8")
try:
    C.apply(ja, ListEmitter())
    raise AssertionError("empty list must fail")
except EngineError as e:
    assert e.code == "NO_CHAPTERS"

# ---------------------------------------------------------------- meta / retitle without AI
if not LIVE:
    mt = C.meta(job(MEDIA_2M, "meta", chapters=[{"t": 0, "title": "A"}, {"t": 40, "title": "B"}]), ListEmitter())
    assert mt["ai"] is False and mt["titles"] == [] and mt["description"] == "00:00 A\n00:40 B"
    mt2 = C.meta(job(MEDIA_2M, "meta", ai=False, chapters=[{"t": 0, "title": "A"}]), ListEmitter())
    assert mt2["ai"] is False and mt2["warnings"] == ["AI dimatikan untuk alat ini"]
    # Live regression: grok-fast answered hashtags without "#" ('shopee', 'gpt') and the whole meta call failed the
    # schema even after the repair request. The schema now accepts them; meta_post normalises to "#word".
    from ac.ai import prompts as AP
    bare = {"titles": ["Cara Daftar Reseller WarungPay", "Jualan Akun Premium dari Nol", "Bikin Toko Online Sendiri"],
            "description": "x" * 120, "hashtags": ["#reseller", "shopee", "gpt", "Chat GPT"],
            "tags": ["reseller", "warungpay", "toko online", "jualan", "akun premium"]}
    assert ai.validate_schema(bare, AP.META_SCHEMA) == [], ai.validate_schema(bare, AP.META_SCHEMA)
    assert AP.meta_post(bare)[0]["hashtags"] == ["#reseller", "#shopee", "#gpt", "#chatgpt"]
    try:
        C.retitle(job(MEDIA_2M, "retitle", chapters=[{"t": 0, "title": "A"}]), ListEmitter())
        raise AssertionError("retitle needs AI")
    except EngineError as e:
        assert e.code == "NO_AI"

# ---------------------------------------------------------------- CLI end to end (JSON lines, tools listing)
jp = tmp / "job.json"
jp.write_text(json.dumps(dict(job(MEDIA_2M), id="chapters-test", params={"ai": False})), encoding="utf-8")
env = dict(os.environ)
r = subprocess.run([sys.executable, "-X", "utf8", str(ENGINE / "cli.py"), "run", str(jp)], capture_output=True,
                   text=True, encoding="utf-8", timeout=120, env=env)
lines = [json.loads(ln) for ln in r.stdout.splitlines() if ln.strip()]
assert r.returncode == 0 and lines[-1]["ev"] == "result", r.stdout[-500:] + r.stderr[-500:]
assert [e["stage"] for e in lines if e["ev"] == "stage"] == ["words", "units", "find", "check"]
assert lines[-1]["data"]["summary"].endswith("(tanpa AI)")
r = subprocess.run([sys.executable, "-X", "utf8", str(ENGINE / "cli.py"), "tools"], capture_output=True, text=True,
                   encoding="utf-8", timeout=60, env=env)
tools = {t["id"]: t for t in json.loads(r.stdout.splitlines()[-1])["data"]["tools"]}
assert tools["chapters"]["ok"] and {"analyze", "apply", "meta", "retitle"} <= set(tools["chapters"]["actions"])

# ---------------------------------------------------------------- live (spends Grok quota)
if LIVE:
    assert ai.available(), "proxy not reachable"
    res = C.analyze(job(MEDIA_2M), ListEmitter())
    check_valid(res)
    print(f"live analyze: {res['summary']} source={res['source']}\n{res['youtube']}")
    mt = C.meta(job(MEDIA_2M, "meta", chapters=res["chapters"]), ListEmitter())
    print(f"live meta: ai={mt['ai']} titles={mt['titles'][:2]} hashtags={mt['hashtags'][:5]}")
    assert mt["ai"] and len(mt["titles"]) >= 1 and mt["description"]

_common.cleanup("chapters")
print("ok")
