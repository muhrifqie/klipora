"""ac.tools.angles: Motion math, sentence splits, angle planner rules, analyze/apply on real media (cached
transcripts; the activity cache <stem>_angles.npz is built once next to the 49 s file, ~1 s), carry-over of the
user's edits, scope, the preview frame action and one cli.py run. Offline, no GPU job, no Grok."""
import json
import subprocess
import sys

import _common
from _common import ENGINE, MEDIA_49

from ac import review as RV
from ac.progress import ListEmitter
from ac.timeline import Timeline
from ac.tools import angles as A
from ac.util import EngineError

near = _common.approx

# ---------------------------------------------------------------- Motion math (vision.md 4.4)
o = A.place(0.7, 0.6, 140, 100, (2292, 960), (2292, 960))
assert o["scale"] == 140 and near(o["pos"][0], 0.3) and near(o["pos"][1], 0.36, 1e-4)   # x clamped at 1 - 1.4/2
assert near(o["view"][2], 1 / 1.4, 1e-3) and near(o["view"][0] + o["view"][2], 1.0, 1e-3)  # window at the right edge
w = A.place(0.9, 0.9, 100, 100, (2292, 960), (2292, 960))
assert w["pos"] == [0.5, 0.5] and w["view"] == [0.0, 0.0, 1.0, 1.0]                      # wide 100 % never moves
assert A.base_scale((1920, 1080), (2292, 960)) == round(100 * 2292 / 1920, 4)            # fill, not fit
m = A.place(0.5, 0.2, 118, A.base_scale((1920, 1080), (2292, 960)), (1920, 1080), (2292, 960))
assert near(m["scale"], 118 * 2292 / 1920, 1e-2) and m["pos"][1] > 0.5                  # 16:9 in 2.39: can tilt up
assert A.fits([0.4, 0.4, 0.6, 0.6], o["view"]) and not A.fits([0.0, 0.1, 0.9, 0.9], o["view"])
for bad in ({"use": ["wide"]}, {"scales": {"wide": 100, "medium": 101, "close": 140}}):
    try:
        A._params(bad)
        raise AssertionError("BAD_PARAMS expected")
    except EngineError as e:
        assert e.code == "BAD_PARAMS"
assert A._params({"weights": "dekat"})["weights"]["close"] == 3.0

# ---------------------------------------------------------------- sentence splits
words = [{"text": t, "t0": a, "t1": b, "seg": s} for t, a, b, s in [
    (" Halo", 0.2, 0.6, 0), (" semua.", 0.6, 1.0, 1), (" Hari", 1.5, 1.8, 0), (" ini", 1.8, 2.0, 0),
    (" kita", 2.0, 2.3, 0), (" belajar,", 2.3, 2.8, 0), (" terus", 3.0, 3.3, 0), (" kita", 3.3, 3.5, 0),
    (" coba", 3.5, 4.0, 1), (" Oke", 6.2, 6.5, 0), (" lanjut", 6.5, 7.0, 0), (" ya.", 7.0, 7.4, 1),
    (" Nah", 9.0, 9.3, 0), (" sekarang", 9.3, 9.8, 0)]]
c = A.sentence_starts(words, 0.0, 10.0, 120)
ts = [t for t, _ in c]
assert any(abs(t - 1.38) < 0.02 for t in ts) and any(abs(t - 6.08) < 0.02 for t in ts)  # just before "Hari" / "Oke"
assert all(abs(t * 120 - round(t * 120)) < 0.01 for t in ts)                               # frame-snapped
pts = A.split_points(0.0, 10.0, c, 2.0, 4.0)
assert pts and all(b - a >= 2.0 - 1e-9 for a, b in zip([0.0] + pts, pts + [10.0])), pts
assert A.split_points(0.0, 3.0, c, 2.0, 4.0) == []                                        # too short to split

# ---------------------------------------------------------------- planner rules
p = A._params({})
shots = [{"t0": i * 1.0, "t1": i * 1.0 + 1.0, "in_scope": True} for i in range(30)]
A.choose_angles(shots, p, [{} for _ in shots])
seq_a = [s["angle"] for s in shots]
assert all(x != y for x, y in zip(seq_a, seq_a[1:])), seq_a                               # never twice in a row
cnt = {a: seq_a.count(a) for a in A.ANGLES}
assert cnt["wide"] > cnt["medium"] > cnt["close"] > 0 and seq_a[0] == "wide", cnt          # weights 3/2/1
rh = A._params({"mode": "rhythm", "min_shot": 2.5})
shots_r = [{"t0": i * 1.0, "t1": i * 1.0 + 1.0, "in_scope": True} for i in range(12)]
A.choose_angles(shots_r, rh, [{} for _ in shots_r])
changes = [i for i in range(1, 12) if shots_r[i]["angle"] != shots_r[i - 1]["angle"]]
assert changes and all(b - a >= 3 for a, b in zip([0] + changes, changes)), changes          # >= 2,5 dtk per angle
fit = [{"wide": True, "medium": True, "close": False} for _ in range(10)]
shots_f = [{"t0": i * 2.0, "t1": i * 2.0 + 2.0, "in_scope": True} for i in range(10)]
A.choose_angles(shots_f, p, fit)
assert "close" not in [s["angle"] for s in shots_f]                                       # action would not fit
only_wide = [{"wide": True, "medium": False, "close": False} for _ in range(4)]
shots_t = [{"t0": i * 2.0, "t1": i * 2.0 + 2.0, "in_scope": True} for i in range(4)]
A.choose_angles(shots_t, p, only_wide)
assert [s["angle"] for s in shots_t] == ["wide", "medium", "wide", "medium"] and shots_t[1]["tight"]

if not _common.have(MEDIA_49):
    print("ok (no media)")
    sys.exit(0)

# ---------------------------------------------------------------- analyze / apply on the cut15 sequence
KEPT = [[1.62, 2.56], [2.96, 7.47], [8.46, 10.36], [10.59, 11.5], [15.17, 17.92], [19.49, 22.51], [23.48, 24.98],
        [25.65, 26.85], [27.2, 29.96], [31.06, 31.79], [32.02, 36.61], [38.14, 40.84], [41.65, 42.46], [42.74, 44.3],
        [44.49, 45.87]]


def cut15(extra_v2=False):
    t, v, a = 0.0, [], []
    for i, (s, e) in enumerate(KEPT):
        d = e - s
        cl = {"name": "x.mp4", "path": str(MEDIA_49), "start": round(t, 4), "end": round(t + d, 4), "in": s, "out": e}
        v.append(dict(cl, nodeId=f"v{i}"))
        a.append(dict(cl, nodeId=f"a{i}"))
        t += d
    v2 = [{"name": "logo", "path": str(MEDIA_49), "start": 2.0, "end": 4.0, "in": 0, "out": 2}] if extra_v2 else []
    return {"id": "seq-cut15", "name": "cut15", "fps": 120, "width": 2292, "height": 960, "duration": round(t, 4),
            "video": [{"index": 0, "clips": v}, {"index": 1, "clips": v2}], "audio": [{"index": 0, "clips": a}]}


tmp = _common.scratch("angles")
job = {"tool": "angles", "action": "analyze", "seq": cut15(extra_v2=True), "params": {}, "workdir": str(tmp)}
em = ListEmitter()
res = A.analyze(job, em)
assert "error" not in em.kinds()
assert [e["id"] for e in em.items if e["ev"] == "stage"] == ["read", "words", "vision", "plan"]
doc = RV.load(res["review"])
items = doc["items"]
st = res["stats"]
assert len(items) == 15 and st["clips"] == 15 and st["repeats"] == 0 and st["track"] == 0, st
assert all(it["kind"] == "shot" and it["on"] and set(it["opts"]) == set(A.ANGLES) for it in items)
assert [it["angle"] for it in items].count("wide") >= 5 and st["by_angle"]["close"] >= 2, st
assert all(x["angle"] != y["angle"] for x, y in zip(items, items[1:]))
for it in items:
    o = it["opts"][it["angle"]]
    k = o["scale"] / 100
    assert it["scale"] == o["scale"] and it["pos"] == o["pos"]
    assert 1 - k / 2 - 1e-6 <= it["pos"][0] <= k / 2 + 1e-6 and 1 - k / 2 - 1e-6 <= it["pos"][1] <= k / 2 + 1e-6
    assert it["label"].startswith(A.NAMES[it["angle"]]) and it["media"] == str(MEDIA_49)
assert st["anchors"].get("activity", 0) >= 8, st["anchors"]                               # screen activity found
assert any(it["ctx"] for it in items)                                                    # transcript text (cache)
assert isinstance(res["summary"], str) and res["summary"].startswith("15 shot")

ap = A.apply({"review": res["review"], "params": {}}, ListEmitter())
plan = ap["plan"]
assert plan["kind"] == "keyframes" and plan["mode"] == "static" and plan["tool"] == "angles" and plan["track"] == 0
assert plan["splits"] == [] and len(plan["items"]) == 15
assert all(set(x) == {"id", "t0", "t1", "angle", "scale", "pos"} for x in plan["items"])

# user edits: one row off, one angle picked -> apply follows, re-run keeps both (carry-over)
d2 = json.load(open(res["review"], encoding="utf-8"))
d2["items"][3]["on"], d2["items"][3]["touched"] = False, True
other = next(a for a in A.ANGLES if a != d2["items"][5]["angle"])
d2["items"][5]["angle"], d2["items"][5]["picked"] = other, True
json.dump(d2, open(res["review"], "w", encoding="utf-8"))
ap2 = A.apply({"review": res["review"]}, ListEmitter())["plan"]
assert len(ap2["items"]) == 14
x5 = next(x for x in ap2["items"] if x["id"] == d2["items"][5]["id"])
assert x5["angle"] == other and x5["scale"] == d2["items"][5]["opts"][other]["scale"]
em2 = ListEmitter()
res2 = A.analyze(job, em2)
assert res2["carried"] == 2
d3 = RV.load(res2["review"])
assert not d3["items"][3]["on"] and d3["items"][5]["angle"] == other and d3["items"][5]["picked"]
assert any(e.get("note") == "dari cache" for e in em2.items if e["ev"] == "stage_done" and e["id"] == "vision")

# ---------------------------------------------------------------- raw 49 s + sentence splits + scope
raw = Timeline.from_media(MEDIA_49).to_json()
em = ListEmitter()
r = A.analyze({"tool": "angles", "seq": raw, "params": {"split": True, "every": 6}, "workdir": str(tmp)}, em)
its = RV.load(r["review"])["items"]
assert r["stats"]["splits"] >= 4 and len(its) == r["stats"]["splits"] + 1, r["stats"]
assert all(it["t1"] - it["t0"] >= 2.5 - 1e-6 for it in its)                               # min shot respected
assert all(x["t1"] == y["t0"] for x, y in zip(its, its[1:])) and its[0]["t0"] == 0
plan = A.apply({"review": r["review"]}, ListEmitter())["plan"]
assert plan["splits"] == sorted(it["t0"] for it in its[1:])
assert all(abs(t * 120 - round(t * 120)) < 0.1 for t in plan["splits"])          # host rounds to frames

scope = {"kind": "inout", "t0": 10.0, "t1": 30.0}
r = A.analyze({"tool": "angles", "seq": raw, "params": {"split": True, "scope": scope}, "workdir": str(tmp)}, ListEmitter())
its = RV.load(r["review"])["items"]
assert its[0]["t0"] == 10.0 and its[-1]["t1"] == 30.0 and its[0]["cut_in"] and its[-1]["cut_out"]
sp = A.apply({"review": r["review"]}, ListEmitter())["plan"]["splits"]
assert sp[0] == 10.0 and sp[-1] == 30.0

em = ListEmitter()
r = A.analyze({"tool": "angles", "seq": raw, "params": {}, "workdir": str(tmp)}, em)       # 1 clip, no split
assert r["stats"]["n"] == 1 and any("cuma punya 1 clip" in e["msg"] for e in em.items if e["ev"] == "warn")

r = A.analyze({"tool": "angles", "seq": cut15(), "params": {"anchor": "center", "use": ["wide", "close"]},
               "workdir": str(tmp)}, ListEmitter())
its = RV.load(r["review"])["items"]
assert all(it["anchor"]["src"] == "center" and set(it["opts"]) == {"wide", "close"} for it in its)
assert [it["angle"] for it in its][:4] == ["wide", "close", "wide", "close"]

hd = dict(cut15(), width=1920, height=1080)                                                # media 2292x960 in 1080p
em = ListEmitter()
r = A.analyze({"tool": "angles", "seq": hd, "params": {"anchor": "center"}, "workdir": str(tmp)}, em)
its = RV.load(r["review"])["items"]
assert any("Ukuran video beda" in e["msg"] for e in em.items if e["ev"] == "warn")
# English job (job["lang"] from the panel): stage labels, warnings, row labels and summary in English
import cli  # noqa: E402
em = ListEmitter()
assert cli.execute({"tool": "angles", "action": "analyze", "seq": hd, "params": {"anchor": "center"}, "lang": "en",
                    "workdir": str(tmp / "en")}, em) == 0, em.items[-3:]
assert [i.get("label") for i in em.items if i["ev"] == "stage"][0] == "Read sequence"
assert any(e["msg"].startswith("The video size differs") for e in em.items if e["ev"] == "warn")
r_en = next(i["data"] for i in em.items if i["ev"] == "result")
assert r_en["summary"].startswith("15 shots: "), r_en["summary"]
assert RV.load(r_en["review"])["items"][0]["label"].split(" ")[0] in ("Wide", "Medium", "Close")
assert all(near(it["base"], 112.5, 1e-3) and near(it["opts"]["wide"]["scale"], 112.5, 1e-3) for it in its)   # fill

for params, code in (({"scope": {"kind": "inout", "t0": 100, "t1": 200}}, "NO_SHOTS"), ({"track": 4}, "NO_TRACK")):
    try:
        A.analyze({"tool": "angles", "seq": cut15(), "params": params, "workdir": str(tmp)}, ListEmitter())
        raise AssertionError(code + " expected")
    except EngineError as e:
        assert e.code == code, e.code

# ---------------------------------------------------------------- preview frame (worker action)
fr = A.frame({"tool": "angles", "seq": None, "params": {"media": str(MEDIA_49), "t": 12.0, "width": 320},
              "workdir": str(tmp)}, ListEmitter())
from pathlib import Path  # noqa: E402
assert Path(fr["path"]).is_file() and fr["w"] == 320 and fr["h"] == 134 and Path(fr["path"]).stat().st_size < 60_000

# ---------------------------------------------------------------- through the CLI (registry + JSON lines)
jp = tmp / "job.json"
jp.write_text(json.dumps({"id": "t1", "tool": "angles", "action": "analyze", "seq": cut15(), "params": {},
                          "workdir": str(tmp)}), encoding="utf-8")
out = subprocess.run([sys.executable, "-X", "utf8", str(ENGINE / "cli.py"), "run", str(jp)], capture_output=True,
                     text=True, encoding="utf-8", timeout=120)
evs = [json.loads(ln) for ln in out.stdout.splitlines() if ln.startswith("{")]
assert out.returncode == 0 and evs[-1]["ev"] == "result" and evs[-1]["data"]["stats"]["n"] == 15, out.stdout[-500:]
assert all(json.loads(ln) for ln in out.stdout.splitlines())                               # stdout = JSON lines only

_common.cleanup("angles")
print("ok")
