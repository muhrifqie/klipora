"""Potong Pengulangan (ac.tools.repeats): detectors on synthetic transcripts, analyze/apply/preview on the real
test media (cached transcripts only, no GPU job), sequence mapping, scope, carry-over, AI offline fallback.

  python engine/tests/test_repeats.py          offline (spends NO Grok quota)
  python engine/tests/test_repeats.py --live   + one real AI labelling call on the 34,6 min transcript"""
import json
import os
import time
import wave

import _common
from _common import MEDIA_2M, MEDIA_34M, MEDIA_49

LIVE = _common.flag("--live")
tmp = _common.scratch("repeats")
os.environ["LOCALAPPDATA"] = str(tmp / "local")          # isolated AI cache / logs
os.environ["APPDATA"] = str(tmp / "roaming")             # default settings (ai on)
if not LIVE:
    os.environ["AI_BASE_URL"] = "http://127.0.0.1:9/v1"   # discard port: AI offline -> rule fallback

import numpy as np  # noqa: E402

from ac import i18n  # noqa: E402
from ac import review as RV  # noqa: E402
from ac.progress import ListEmitter, NullEmitter  # noqa: E402
from ac.timeline import Timeline  # noqa: E402
from ac.tools import repeats as RP  # noqa: E402
from ac.util import EngineError  # noqa: E402


# ------------------------------------------------------------------ synthetic transcript + envelope
def synth(text, gap=0.08, word=0.3, pauses=None, quiet=()):
    """Words of `text` (a '|' adds a 1,5 s pause, '.' ends a sentence) + an envelope: -20 dB under words,
    -80 dB between them; words whose index is in `quiet` get -85 dB (hallucinated over silence)."""
    rows, t = [], 0.5
    for tok in text.split():
        if tok == "|":
            t += 1.5
            continue
        rows.append([" " + tok, round(t, 3), round(t + word, 3), 1 if tok.endswith(".") else 0])
        t += word + gap
    n = int((t + 1.0) / 0.01)
    db = np.full(n, -80.0, np.float32)
    for k, r in enumerate(rows):
        db[int(r[1] / 0.01) + 3:int(r[2] / 0.01) - 1] = -85.0 if k in quiet else -20.0
    return rows, db


def run_synth(text, **kw):
    rows, db = synth(text, **{k: v for k, v in kw.items() if k in ("gap", "word", "quiet")})
    W = RP.make_words(rows)
    sn = RP.Snapper(db)
    loops = RP.find_loops(W, sn.sp)
    prm = RP._params(kw.get("params", {}))
    items = RP.build_items(W, RP.detect(W, kinds=prm["kinds"], window=prm["window"]), sn, prm)
    return W, items, loops


def drops(items):
    return [(it["kind"], it["drop"], it["on"]) for it in items]


W, items, _ = run_synth("nah kita kita bisa buka menu produk di sebelah kiri")
assert drops(items) == [("stutter", "kita", True)], drops(items)
it = items[0]
assert it["keep"].startswith("kita bisa") and it["ctx"]["pre"] == "nah" and it["auto"] and not it["tight"]
assert W[1].t0 - 0.1 <= it["t0"] <= W[1].t0 + 0.06 and abs(it["t1"] - W[2].t0) <= 0.06, (it["t0"], it["t1"])
assert it["takes"][0]["role"] == "drop" and it["takes"][-1]["role"] == "keep"

W, items, _ = run_synth("terus untuk hosting gak usah, gak usah pakai hosting yang mahal")
assert drops(items) == [("stutter", "gak usah,", True)] and items[0]["conf"] >= 0.85, drops(items)

W, items, _ = run_synth("eh sorry sorry sorry disini kayaknya salah deh")
assert len(items) == 1 and items[0]["drop"] == "sorry sorry" and len(items[0]["takes"]) == 3, drops(items)

W, items, _ = run_synth("biasanya kalau di jam kerja ya jam kerja itu palingan satu sampai dua jam")
assert [(k, d) for k, d, _ in drops(items)] == [("restart", "jam kerja ya")] and items[0]["on"], drops(items)

W, items, _ = run_synth("untuk produknya kita ke beranda lagi sorry kita ke menu produk nah cek dulu")
assert items and items[0]["kind"] == "correction" and "sorry" in items[0]["drop"], drops(items)

W, items, _ = run_synth("jadi kita buat dulu akun barunya. | | jadi kita buat dulu akun barunya lewat menu daftar ya.")
assert [(k, on) for k, _, on in drops(items)] == [("retake", True)] and items[0]["sim"] >= 0.8, drops(items)
assert items[0]["drop"] == "jadi kita buat dulu akun barunya."
# a short re-think pause (<= 2 s) goes with the attempt: the cut ends at the onset of the new take
W2, it2, _ = run_synth("jadi kita buat dulu akun barunya. | jadi kita buat dulu akun barunya lewat menu daftar ya.")
assert it2 and not it2[0].get("long_gap") and abs(it2[0]["t1"] - W2[6].t0) <= 0.06, (it2[0]["t1"], W2[6].t0)
# "di sini | di bagian bawah": one word swapped = maybe a list / correction of content -> listed, unchecked
W2, it2, _ = run_synth("kamu bisa klik tombol simpan di sini. | kamu bisa klik tombol simpan di bagian bawah ini ya.")
assert [(k, on) for k, _, on in drops(it2)] == [("retake", False)] and "daftar" in it2[0]["why"], drops(it2)
with i18n.using("en"):                              # English job: reasons, kind labels and notes follow it
    _, it_en, _ = run_synth("kamu bisa klik tombol simpan di sini. | kamu bisa klik tombol simpan di bagian bawah ini ya.")
    assert it_en[0]["kind_label"] == "Retake" and "looks like a list" in it_en[0]["why"] and " s pause" in it_en[0]["why"], it_en[0]
    assert RP.flag_label("loop") == "Repeated words without sound"
# a long pause (> max_join 2 s) before the new take is NOT removed (Potong Silence decides about pauses)
assert items[0]["t1"] < W[6].t0 - 1.0 and items[0].get("long_gap"), (items[0]["t1"], W[6].t0)

# not repeats: sentence boundary echo, reduplication, tics, list items
for text in ("lihat di bawah ini. Ini adalah harga paket kita", "jalan pelan pelan saja ya",
             "oke ya ya ya kita mulai", "tanpa modal, tanpa deposit, tanpa ribet"):
    W, items, _ = run_synth(text)
    assert not items, (text, drops(items))
# parallel list is listed at most as an unchecked suggestion
W, items, _ = run_synth("kamu gak usah mikirin stok gak usah mikirin akun yang penting jualan")
assert all(not on for _, _, on in drops(items)), drops(items)

# Whisper loop over silence -> flag, never a stutter; the same over clear sound -> a real stammer
W, items, loops = run_synth("nah ini ini ini ini kita mulai", quiet={1, 2, 3, 4})
assert loops == [(1, 5)], loops
W, items, loops = run_synth("nah saya saya saya mau jelasin dulu")
assert not loops and items and items[0]["kind"] == "stutter" and items[0]["drop"] == "saya saya", drops(items)

# glued cut (no dip at the edge) -> tight, penalised, not pre-checked
rows, db = synth("biar nanti customer gak gak nanyain stock")
db[:] = -20.0
W2 = RP.make_words(rows)
it2 = RP.build_items(W2, RP.detect(W2), RP.Snapper(db, -45.0), RP._params({}))
assert it2 and it2[0]["tight"] and not it2[0]["on"] and it2[0]["note"]["type"] == "warn", it2

# params: kinds filter and thresholds
W, items, _ = run_synth("nah kita kita bisa buka menu produk di sebelah kiri", params={"kinds": ["restart"]})
assert not items
W, items, _ = run_synth("nah kita kita bisa buka menu produk di sebelah kiri", params={"min_on": 95})
assert items and not items[0]["on"]
print("synthetic ok")

if not _common.have(MEDIA_34M, MEDIA_49):
    raise SystemExit(0)


# ------------------------------------------------------------------ real media (cached transcripts)
def analyze(seq, params=None, wd="w34"):
    job = {"tool": "repeats", "action": "analyze", "seq": seq, "params": params or {}, "workdir": str(tmp / wd)}
    em = ListEmitter()
    res = RP.analyze(job, em)
    assert "error" not in em.kinds()
    return res, RV.load(res["review"]), em, job


tl34 = Timeline.from_media(MEDIA_34M)
t0 = time.time()
res, doc, em, job = analyze(tl34.to_json())
secs = time.time() - t0
assert secs < 5, secs
stages = [e["stage"] for e in em.items if e.get("ev") == "stage"]
assert stages == ["words", "audio", "find", "review"], stages
assert any(e.get("ev") == "stage_done" and e.get("note") == "dari cache" for e in em.items)
items = doc["items"]
summ = res["summary"]
assert doc["tool"] == "repeats" and doc["timebase"] == "sequence" and doc["fps"] == tl34.fps
assert 12 <= summ["n"] <= 60 and 5 <= summ["on"] <= 25, summ
assert all(it["kind"] in RP.KINDS and 0 <= it["conf"] <= 1 and it["t1"] > it["t0"] for it in items)
assert all(0 <= it["t0"] and it["t1"] <= tl34.duration for it in items)
assert len({it["id"] for it in items}) == len(items)
assert all(it["on"] == (it["auto"] and it["conf"] >= 0.65) for it in items)


def at(t, tol=0.4, src=items):
    return next((it for it in src if abs(it["t0"] - t) <= tol), None)


# known findings on this tutorial (docs/research/fillers_repeat.md, product_cutting.md 3.2)
for t, kind in ((573.6, "stutter"), (1995.6, "restart"), (404.9, "retake"), (1808.6, "stutter"), (505.0, "stutter")):
    it = at(t)
    assert it and it["kind"] == kind and it["on"], (t, it)
assert at(1808.6)["drop"].lower().startswith("sorry sorry")
for t in (250.1, 471.6, 1023.8, 1561.3):           # list items / restatements: never pre-checked
    it = at(t)
    assert it is None or not it["on"], (t, it)
flags = doc["flags"]
assert any(f["kind"] == "loop" and 1859 < f["t0"] < 1861 for f in flags), flags
assert any(f["kind"] == "phrase" and "menonton" in f["text"].lower() for f in flags), flags
assert all(not (f["t0"] - 0.3 < it["t0"] < f["t1"] + 0.3) for f in flags for it in items)
assert all(f["label"] for f in flags)
print(f"34 min: {summ['n']} found, {summ['on']} pre-checked, {len(flags)} flags, {secs:.2f} s")

# stable ids + the user's manual choice survives a re-run (carry_over)
first = at(573.6)
doc["items"] = [dict(x, on=False, touched=True) if x["id"] == first["id"] else x for x in doc["items"]]
RV.save(doc, res["review"])
res2, doc2, _, _ = analyze(tl34.to_json())
assert [x["id"] for x in doc2["items"]] == [x["id"] for x in items]
again = next(x for x in doc2["items"] if x["id"] == first["id"])
assert again["on"] is False and again["touched"] and res2["summary"]["on"] == summ["on"] - 1

# apply: frame-aligned plan of the checked items
ap = RP.apply({"tool": "repeats", "action": "apply", "seq": tl34.to_json(), "review": res2["review"], "params": {}},
              NullEmitter())
plan = ap["plan"]
assert plan["kind"] == "remove_ranges" and plan["timebase"] == "sequence"
rng = plan["ranges"]
assert len(rng) == ap["summary"]["count"] == res2["summary"]["on"], (len(rng), ap["summary"])
assert all(a < b for a, b in rng) and all(rng[k][1] <= rng[k + 1][0] for k in range(len(rng) - 1))
assert all(abs(x * tl34.fps - round(x * tl34.fps)) < 1e-3 for r in rng for x in r), rng[:3]
assert sum(b - a for a, b in rng) <= res2["summary"]["sec_on"] + 1e-6
# the analysed sequence changed since: refuse
bad = dict(tl34.to_json(), duration=tl34.duration - 5)
try:
    RP.apply({"tool": "repeats", "action": "apply", "seq": bad, "review": res2["review"], "params": {}}, NullEmitter())
    raise AssertionError("SEQ_CHANGED expected")
except EngineError as e:
    assert e.code == "SEQ_CHANGED", e.code

# preview: the audio around one cut with the cut applied
it = at(573.6, src=doc2["items"])
pv = RP.preview({"tool": "repeats", "action": "preview", "seq": tl34.to_json(), "workdir": str(tmp / "w34"),
                 "params": {"cut": [it["t0"], it["t1"]], "pre": 2.0, "post": 1.5, "id": it["id"]}}, NullEmitter())
with wave.open(pv["path"]) as wf:
    assert wf.getnchannels() == 1 and wf.getframerate() == 48000
    dur = wf.getnframes() / 48000
assert abs(dur - 3.49) < 0.02 and abs(pv["dur"] - dur) < 1e-3, dur
x = np.frombuffer(open(pv["path"], "rb").read()[44:], "<i2")
assert np.abs(x).max() > 1000, "preview is silent"
os.remove(pv["path"])

# a cut sequence: clips map to sequence time; scope and kind filter
seq = {"id": "seq-two", "name": "Dua clip", "fps": 120, "width": 2292, "height": 960, "duration": 800.0,
       "video": [], "audio": [{"index": 0, "name": "A1", "clips": [
           {"name": "x.mp4", "path": str(MEDIA_34M), "start": 0.0, "end": 400.0, "in": 300.0, "out": 700.0},
           {"name": "x.mp4", "path": str(MEDIA_34M), "start": 400.0, "end": 800.0, "in": 1100.0, "out": 1500.0}]}]}
res3, doc3, _, _ = analyze(seq, wd="w2c")
for src_t, seq_t in ((404.9, 104.9), (505.0, 205.0), (1128.3, 428.3), (1406.0, 706.0)):
    it = at(seq_t, src=doc3["items"])
    ref = at(src_t)
    assert it and ref and it["kind"] == ref["kind"] and abs((it["t0"] - seq_t) - (ref["t0"] - src_t)) < 0.02, (seq_t, it)
assert all(0 <= x["t0"] and x["t1"] <= 800 for x in doc3["items"])
res4, doc4, _, _ = analyze(seq, {"scope": {"kind": "inout", "t0": 100.0, "t1": 300.0}}, wd="w2c")
assert doc4["items"] and all(100 <= x["t0"] and x["t1"] <= 300 for x in doc4["items"]), [x["t0"] for x in doc4["items"]]
res5, doc5, _, _ = analyze(tl34.to_json(), {"kinds": ["stutter"]}, wd="wk")
assert doc5["items"] and {x["kind"] for x in doc5["items"]} == {"stutter"}

# short media: nothing (or little) to find, no errors
for media in (MEDIA_49, MEDIA_2M):
    if media.is_file():
        r, d, _, _ = analyze(Timeline.from_media(media).to_json(), wd="ws")
        assert r["summary"]["n"] <= 3 and isinstance(d["flags"], list), r["summary"]

# no audio -> NO_AUDIO
try:
    RP.analyze({"tool": "repeats", "action": "analyze", "params": {}, "workdir": str(tmp / "wn"),
                "seq": {"name": "v", "duration": 5, "video": [{"index": 0, "clips": [
                    {"name": "a", "path": str(MEDIA_49), "start": 0, "end": 5, "in": 0, "out": 5}]}], "audio": []}},
               NullEmitter())
    raise AssertionError("NO_AUDIO expected")
except EngineError as e:
    assert e.code == "NO_AUDIO", e.code

# AI: offline -> warning + rule result unchanged; --live -> labels only (never changes on/cut)
t0 = time.time()
res6, doc6, em6, _ = analyze(tl34.to_json(), {"ai": True}, wd="wai")
assert [e["stage"] for e in em6.items if e.get("ev") == "stage"] == ["words", "audio", "find", "ai", "review"]
assert [(x["id"], x["on"], x["t0"], x["t1"]) for x in doc6["items"]] == [(x["id"], x["on"], x["t0"], x["t1"]) for x in items]
if LIVE:
    assert doc6["ai"]["source"] in ("ai", "cache"), doc6["ai"]
    got = [x for x in doc6["items"] if x.get("ai")]
    assert len(got) >= 0.8 * len(items) and all(x["ai"]["verdict"] in ("cut", "keep", "other") for x in got)
    print(f"AI live: {len(got)}/{len(items)} labelled ({doc6['ai']['source']}, {doc6['ai']['model']}) in {time.time() - t0:.1f} s")
else:
    assert doc6["ai"]["source"] == "fallback" and not any(x.get("ai") for x in doc6["items"]), doc6["ai"]
    assert any(e.get("ev") == "warn" and "AI tidak dipakai" in e["msg"] for e in em6.items)
    assert time.time() - t0 < 8, time.time() - t0
assert "AI_API_KEY" not in json.dumps(doc6) and "Bearer" not in json.dumps(doc6)

_common.cleanup("repeats")
print("ok")
