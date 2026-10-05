"""ac.tools.viral: helpers, rule fallback on the real 34.6-min transcript, AI path with a fake Grok (window
anchoring, grounding, global rescore, per-window cache, partial failure), scope, edited sequence, apply plan,
and the CLI protocol. Offline by default (no Grok quota, no GPU: cached transcripts + envelopes).

  python engine/tests/test_viral.py            offline
  python engine/tests/test_viral.py --live     + one real Grok run on the 2-min test file (about 3 requests)
"""
import json
import re
import subprocess
import sys
import time

import _common
from _common import MEDIA_2M, MEDIA_34M, MEDIA_49

from ac import review as RV
from ac.ai import client as ai
from ac.progress import ListEmitter
from ac.timeline import Timeline
from ac.tools import viral as V
from ac.util import EngineError

tmp = _common.scratch("viral")

# ---------------------------------------------------------------- pure helpers
assert V.clip_name(1, "Cara Pasang\nName Server") == "Viral 01 - Cara Pasang Name Server"
assert V.clip_name(12, "") == "Viral 12 - Klip"
assert [V.score_color(s) for s in (91, 80, 79, 60, 59, 5)] == [0, 0, 4, 4, 3, 3]
prm = V.norm_params({"min": 50, "max": 40, "preset": "custom", "count": "5"}, 600)
assert prm["min_s"] == 50 and prm["max_s"] == 55 and prm["preset"] == "default" and prm["count"] == 5, prm
prm = V.norm_params({"preset": "custom", "prompt": "  momen   marah  ", "ai": False}, 600)
assert prm["preset"] == "custom" and prm["prompt"] == "momen marah" and not prm["use_ai"]
assert V.style_line(prm) == "User instructions: momen marah"
assert V.auto_count(49, 36) == 1 and V.auto_count(120, 36) == 2 and V.auto_count(2076, 36) == 10
assert V.auto_count(5 * 3600, 36) == 20
assert V.ungrounded("Untung 3.500 per hari", "Modal 0", "modal nol untung 3500 rupiah") == ["0"]
assert V.ungrounded("Cara daftar", "", "apa saja") == []
terms = V.topic_terms("domain cloudflare yang murah")
assert terms == ["domain", "cloudflare", "murah"], terms
assert V.topic_match("pasang name server di cloudflair lalu domain aktif", terms) == 0.67
assert V.topic_match("apa saja", []) is None

# synthetic units: 1 s words, 3-word units separated by 0.2 s (one 2 s pause before unit 6)
dw, units = [], []
t = 0.0
texts = ["nah jadi gini", "cara pasang domain", "pertama buka cloudflare", "lalu klik add", "isi nama domain",
         "simpan dulu ya", "terus cek status", "kalau aktif berarti", "domain sudah jalan", "oke sampai sini"]
for i, tx in enumerate(texts):
    if i == 6:
        t += 2.0
    w0 = len(dw)
    for w in tx.split():
        dw.append({"i": len(dw), "text": w, "start": t, "end": t + 0.9, "seg_end": 0, "toks": [len(dw)]})
        t += 1.0
    t += 0.2
    units.append({"i": i, "start": dw[w0]["start"], "end": dw[-1]["end"], "text": tx, "w0": w0, "w1": len(dw) - 1,
                  "grp": 0, "filler": i == 0, "gap_before": 0.3})
grp = (0, len(units) - 1, 0.0, t)
a, k = V.trim_opener(units, dw, 0, 3)
assert (a, k) == (1, 3), (a, k)                          # filler-only unit skipped
a, k = V.trim_opener(units, dw, 9, 9)
assert (a, k) == (9, 28), (a, k)                         # "oke" skipped, "sampai sini" kept
ab = V.fit(units, 1, 1, 0, 9, 10, 15, 12)
assert ab == (1, 4), ab                                  # grown by whole units to >= 10 s
d = units[ab[1]]["end"] - units[ab[0]]["start"]
assert 10 <= d <= 15, d
assert V.fit(units, 1, 9, 0, 9, 10, 15, 12)[1] <= 4      # shrunk below max, never crosses into a too-long run
assert V.fit(units, 8, 8, 8, 9, 30, 40, 35) is None      # cannot reach 0.8 x min inside its group
t0, t1, text = V.bounds(units, dw, 1, 3, 4, grp)
assert text == "cara pasang domain pertama buka cloudflare lalu klik add isi nama domain"
assert t0 < dw[3]["start"] and t1 > units[4]["end"] and t1 < units[5]["start"], (t0, t1)
c = [{"t0": 0, "t1": 30, "score": 50}, {"t0": 5, "t1": 35, "score": 70}, {"t0": 40, "t1": 60, "score": 40}]
assert [x["score"] for x in V.nms(c)] == [70, 40]


def run(media, params, seq=None, name="viral"):
    tl_json = seq or Timeline.from_media(media).to_json()
    job = {"tool": "viral", "action": "analyze", "seq": tl_json, "params": params, "workdir": str(tmp / name)}
    em = ListEmitter()
    t = time.time()
    res = V.analyze(job, em)
    return res, RV.load(res["review"]), em, time.time() - t


def check_doc(doc, min_s, max_s, dur):
    items = doc["items"]
    assert RV.validate(doc) == [] and doc["tool"] == "viral" and doc["timebase"] == "sequence"
    assert [it["score"] for it in items] == sorted((it["score"] for it in items), reverse=True)
    for it in items:
        assert it["kind"] == "clip" and 0 <= it["t0"] < it["t1"] <= dur + 1e-6, it
        assert min_s * 0.8 - 0.2 <= it["t1"] - it["t0"] <= max_s + 0.8, (it["t0"], it["t1"])
        assert 1 <= it["score"] <= 99 and it["title"] and it["text"] and isinstance(it["scores"], dict)
    for i, x in enumerate(items):          # no overlapping suggestions
        for y in items[i + 1:]:
            assert not V.overlaps(x, y), (x["t0"], x["t1"], y["t0"], y["t1"])


have = _common.have(MEDIA_34M, MEDIA_49, MEDIA_2M)
if have:
    # ---------------------------------------------------------------- rule fallback (AI off), 34.6 min
    res, doc, em, sec = run(MEDIA_34M, {"ai": False})
    check_doc(doc, 20, 60, 2076.8)
    assert doc["source"] == "fallback" and not doc["ai"] and all(it["src"] == "rule" for it in doc["items"])
    assert sum(it["on"] for it in doc["items"]) == res["count"] == 10, res
    assert "tanpa AI" in res["summary"] and res["warnings"], res
    kinds = em.kinds()
    assert "error" not in kinds and "warn" in kinds
    stages = [e["stage"] for e in em.items if e["ev"] == "stage"]
    assert stages == ["words", "audio", "find", "rank", "review"], stages
    assert sec < 6, sec
    print(f"fallback 34.6 min: {len(doc['items'])} klip in {sec:.2f} s, best {doc['items'][0]['score']}")

    # topic search moves topic clips up (fallback scoring)
    res_t, doc_t, _, _ = run(MEDIA_34M, {"ai": False, "topic": "domain cloudflare"}, name="topic")
    top = doc_t["items"][:5]
    assert all(it["topic"] is not None for it in doc_t["items"])
    assert sum(1 for it in top if it["topic"]) >= 4, [(it["topic"], it["title"]) for it in top]

    # In/Out scope: every clip inside [600, 900]
    _, doc_s, _, _ = run(MEDIA_34M, {"ai": False, "scope": {"kind": "inout", "t0": 600, "t1": 900}, "count": 3},
                         name="scope")
    assert doc_s["items"] and all(600 <= it["t0"] and it["t1"] <= 900 for it in doc_s["items"])
    assert sum(it["on"] for it in doc_s["items"]) == 3

    # 49 s raw clip -> one clip; 15-30 s preset still finds clips
    _, doc_49, _, _ = run(MEDIA_49, {"ai": False}, name="m49")
    check_doc(doc_49, 20, 60, 49.0)
    assert len(doc_49["items"]) >= 1 and doc_49["items"][0]["on"]
    _, doc_49s, _, _ = run(MEDIA_49, {"ai": False, "min": 15, "max": 30}, name="m49s")
    check_doc(doc_49s, 15, 30, 49.0)

    # edited ("(AutoCut)") sequence: 15 kept pieces of the 49 s file, words mapped through the cuts
    kept = [[1.62, 2.56], [2.96, 7.47], [8.46, 10.36], [10.59, 11.5], [15.17, 17.92], [19.49, 22.51], [23.48, 24.98],
            [25.65, 26.85], [27.2, 29.96], [31.06, 31.79], [32.02, 36.61], [38.14, 40.84], [41.65, 42.46],
            [42.74, 44.3], [44.49, 45.87]]
    clips, tpos = [], 0.0
    for a_, b_ in kept:
        clips.append({"name": MEDIA_49.name, "path": str(MEDIA_49), "start": tpos, "end": tpos + b_ - a_, "in": a_,
                      "out": b_})
        tpos += b_ - a_
    cut = {"id": "seq-cut15", "name": "cut15 (AutoCut)", "fps": 120, "width": 2292, "height": 960, "duration": tpos,
           "video": [{"index": 0, "name": "V1", "clips": clips}], "audio": [{"index": 0, "name": "A1", "clips": clips}]}
    _, doc_c, _, _ = run(None, {"ai": False, "min": 10, "max": 25}, seq=cut, name="cut15")
    check_doc(doc_c, 10, 25, tpos)

    # empty scope -> clear error
    try:
        run(MEDIA_49, {"ai": False, "min": 60, "max": 90}, name="short")
        raise AssertionError("expected NO_CLIPS")
    except EngineError as e:
        assert e.code == "NO_CLIPS" and "60-90" in e.msg, e.msg

    # ---------------------------------------------------------------- AI path with a fake Grok
    calls = {"win": 0, "rescore": 0}
    fail_first = {"on": False}
    zero_rescore = {"on": False}
    OrigCache = ai.Cache
    real_available, real_chat = ai.available, ai.chat_json

    def fake_chat(messages, schema=None, check=None, model=None, tag="", **kw):
        user = messages[-1]["content"]
        if tag == "viral_rescore":
            calls["rescore"] += 1
            letters = re.findall(r"^([A-T]) \(", user, re.M)
            if zero_rescore["on"]:   # degenerate answer seen live: every clip "score": 0
                return {"clips": [{"c": L, "score": 0, "title": f"Nol {L}", "hook": "x", "why": "viewer skip"} for L in letters]}
            return {"clips": [{"c": L, "score": 90 - 7 * i, "title": f"Judul ulang {L}", "hook": f"Hook {L} 777"
                               if i == 0 else f"Hook {L}", "why": "alasan singkat"} for i, L in enumerate(letters)]}
        calls["win"] += 1
        if fail_first["on"] and calls["win"] == 1:
            raise ai.AIBadOutput("fake bad json")
        rows = [r for r in user.splitlines() if re.match(r"^\d+:\d\d-\d+:\d\d ", r)]
        out = []
        for j in (2, len(rows) // 2):
            if j >= len(rows):
                continue
            head, txt = rows[j].split(" ", 1)
            st, en = head.split("-")
            end_row = rows[min(len(rows) - 1, j + 3)]
            eh, etxt = end_row.split(" ", 1)
            out.append({"start": st, "end": eh.split("-")[1], "start_quote": " ".join(txt.split()[:5]),
                        "end_quote": " ".join(etxt.split()[-4:]), "title": "Untung 9999 juta" if j == 2 else "Judul",
                        "hook": "Hook", "scores": {"hook": 90, "flow": 85, "value": 88, "trend": 80},
                        "reason": "kuat"})
        return {"clips": out}

    try:
        ai.Cache = lambda folder=None: OrigCache(tmp / "ai_cache")   # never pollute the real AI cache
        ai.available = lambda *a, **k: True
        ai.chat_json = fake_chat
        res, doc, em, sec = run(MEDIA_34M, {"preset": "jualan"}, name="ai")
        check_doc(doc, 20, 60, 2076.8)
        st = doc["stats"]
        assert doc["source"] == "ai" and doc["ai"] and st["windows"] == 6 and calls == {"win": 6, "rescore": 1}, (st, calls)
        assert st["ai_calls"] == 7 and st["cached"] == 0 and st["rescored"] >= 10, st
        assert all(it["src"] == "ai" and it["rescored"] for it in doc["items"])
        assert sum(st["resolve"].values()) >= 12 and st["resolve"].get("ok", 0) >= 10, st["resolve"]
        first = next(it for it in doc["items"] if it["title"] == "Judul ulang A")
        assert first["scores"]["ai"] == 90 and doc["items"][0]["scores"]["ai"] >= 83, doc["items"][0]["scores"]
        assert "777" in first["ungrounded"] and first["note"]["type"] == "warn", first["note"]   # invented number
        assert any(e["ev"] == "progress" and "Bagian" in e.get("note", "") for e in em.items)
        # second click: everything from the per-window + rescore cache, no request
        res2, doc2, em2, sec2 = run(MEDIA_34M, {"preset": "jualan"}, name="ai")
        assert calls == {"win": 6, "rescore": 1} and doc2["stats"]["cached"] == 7 and doc2["stats"]["ai_calls"] == 0
        assert [it["id"] for it in doc2["items"]] == [it["id"] for it in doc["items"]]
        print(f"fake AI 34.6 min: {len(doc['items'])} klip, cached re-run {sec2:.2f} s")

        # one window fails -> mixed, that part filled by rules, warning shown
        fail_first["on"] = True
        calls.update(win=0, rescore=0)
        ai.Cache = lambda folder=None: OrigCache(tmp / "ai_cache2")
        res3, doc3, em3, _ = run(MEDIA_34M, {"preset": "default"}, name="ai_mixed")
        assert doc3["source"] == "mixed" and doc3["stats"]["failed"] == 1 and res3["warnings"], doc3["stats"]
        assert any(e["ev"] == "warn" and "gagal dinilai AI" in e["msg"] for e in em3.items)
        check_doc(doc3, 20, 60, 2076.8)

        # degenerate rescore (all 0): window scores kept, warning, nothing cached -> the next click asks again
        fail_first["on"] = False
        zero_rescore["on"] = True
        calls.update(win=0, rescore=0)
        ai.Cache = lambda folder=None: OrigCache(tmp / "ai_cache3")
        _, doc6, em6, _ = run(MEDIA_34M, {"preset": "edukasi"}, name="ai_zero")
        assert calls["rescore"] == 1 and doc6["stats"]["rescored"] == 0, doc6["stats"]
        assert any(e["ev"] == "warn" and "tidak wajar" in e["msg"] for e in em6.items)
        assert max(it["score"] for it in doc6["items"]) >= 60 and not any(it["title"].startswith("Nol ") for it in doc6["items"])
        run(MEDIA_34M, {"preset": "edukasi"}, name="ai_zero")
        assert calls["rescore"] == 2, calls        # not cached
        zero_rescore["on"] = False
        _, doc7, _, _ = run(MEDIA_34M, {"preset": "edukasi"}, name="ai_zero")
        assert calls["rescore"] == 3 and doc7["stats"]["rescored"] >= 10, (calls, doc7["stats"])
        assert not V.rescore_sane({"clips": [{"c": "A", "score": 0}, {"c": "B", "score": 0}]}, 2)
        assert not V.rescore_sane({"clips": [{"c": L, "score": 50} for L in "ABCD"]}, 4)
        assert V.rescore_sane({"clips": [{"c": "A", "score": 45}]}, 1)

        # strict model on the 2-min file: pool filled with rule picks, all judged by the rescore
        _, doc4, _, _ = run(MEDIA_2M, {}, name="ai_2m")
        assert doc4["source"] == "ai" and all(it["rescored"] for it in doc4["items"]), doc4["stats"]
    finally:
        ai.Cache, ai.available, ai.chat_json = OrigCache, real_available, real_chat

    # proxy down -> rule fallback with a warning that says why
    try:
        ai.available = lambda *a, **k: False
        _, doc5, em5, _ = run(MEDIA_49, {}, name="m49b")
    finally:
        ai.available = real_available
    assert doc5["source"] == "fallback" and not doc5["ai"]
    assert any(e["ev"] == "warn" and "pakai aturan" in e["msg"] for e in em5.items), em5.items

    # ---------------------------------------------------------------- apply: edited review -> plan + clips
    path = tmp / "ai" / "viral_review.json"
    ed = json.loads(path.read_text(encoding="utf-8"))
    for it in ed["items"]:
        it["on"] = False
    ed["items"][1]["on"] = ed["items"][4]["on"] = True
    ed["items"][1]["touched"] = ed["items"][4]["touched"] = True
    path.write_text(json.dumps(ed, ensure_ascii=False), encoding="utf-8")
    seq = Timeline.from_media(MEDIA_34M).to_json()
    out = V.apply({"tool": "viral", "action": "apply", "seq": seq, "review": str(path), "params": {}}, ListEmitter())
    plan, clips = out["plan"], out["clips"]
    assert plan["kind"] == "markers" and plan["tag"] == "[Klipora-VR]" and out["bin"] == "Klipora Viral"
    assert [c["id"] for c in clips] == [ed["items"][1]["id"], ed["items"][4]["id"]]
    assert clips[0]["name"].startswith("Viral 01 - ") and clips[1]["name"].startswith("Viral 02 - ")
    assert clips[0]["score"] >= clips[1]["score"]
    for c_, m_ in zip(clips, plan["markers"]):
        assert abs(c_["t0"] * 120 - round(c_["t0"] * 120)) < 1e-6 and abs(c_["t1"] * 120 - round(c_["t1"] * 120)) < 1e-6
        assert c_["t0"] <= ed["items"][[i["id"] for i in ed["items"]].index(c_["id"])]["t0"] + 1e-9
        assert m_["t"] == c_["t0"] and m_["end"] == c_["t1"] and m_["color"] == V.score_color(c_["score"])
        assert m_["name"].startswith(f"Viral {c_['rank']:02d} ({c_['score']})") and "Skor" in m_["comment"]
    assert out["summary"].startswith("2 klip viral")
    # English job language: marker comments, summary and fallback names follow the job (sequence names stay "Viral NN")
    from ac import i18n
    with i18n.using("en"):
        out_en = V.apply({"tool": "viral", "action": "apply", "seq": seq, "review": str(path), "params": {}}, ListEmitter())
        assert out_en["summary"].startswith("2 viral clips, ") and out_en["summary"].endswith(" s in total"), out_en["summary"]
        assert all("Score " in m_["comment"] and "Skor" not in m_["comment"] for m_ in out_en["plan"]["markers"])
        assert out_en["clips"][0]["name"] == clips[0]["name"] and V.clip_name(3, "") == "Viral 03 - Clip"
    # nothing selected -> NO_CLIPS
    for it in ed["items"]:
        it["on"] = False
    path.write_text(json.dumps(ed), encoding="utf-8")
    try:
        V.apply({"review": str(path)}, ListEmitter())
        raise AssertionError("expected NO_CLIPS")
    except EngineError as e:
        assert e.code == "NO_CLIPS"

    # ---------------------------------------------------------------- CLI protocol (JSON lines, exit 0)
    job = {"id": "viral-test", "tool": "viral", "action": "analyze", "seq": Timeline.from_media(MEDIA_49).to_json(),
           "params": {"ai": False}, "workdir": str(tmp / "cli")}
    jp = tmp / "job.json"
    jp.write_text(json.dumps(job), encoding="utf-8")
    r = subprocess.run([sys.executable, "-X", "utf8", str(_common.ENGINE / "cli.py"), "run", str(jp)],
                       capture_output=True, text=True, encoding="utf-8", timeout=120)
    evs = [json.loads(x) for x in r.stdout.splitlines() if x.strip()]
    assert r.returncode == 0 and evs[-1]["ev"] == "result", (r.returncode, r.stdout[-500:], r.stderr[-500:])
    assert [e["stage"] for e in evs if e["ev"] == "stage"] == ["words", "audio", "find", "rank", "review"]
    assert evs[-1]["data"]["review"].endswith("viral_review.json")

    # ---------------------------------------------------------------- optional: real Grok (spends ~3 requests)
    if _common.flag("--live"):
        res, doc, em, sec = run(MEDIA_2M, {"preset": "edukasi"}, name="live")
        check_doc(doc, 20, 60, 120.5)
        print(f"live: {doc['source']} {len(doc['items'])} klip in {sec:.1f} s, stats {doc['stats']}")
        for it in doc["items"]:
            print(f"  {it['score']:>3} {it['t0']:7.2f}-{it['t1']:7.2f} {it['title']} | {it['hook']} | {it['why']}")

_common.cleanup("viral")
print("ok")
