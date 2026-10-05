"""Hapus Filler (ac/tools/fillers.py + _filler_audio + _filler_lexicon), offline on the cached transcripts.

python engine/tests/test_fillers.py         (~5 s; first run on a machine also builds <stem>_fillers.npz caches)
"""
import json
import subprocess
import sys
import threading
import time
import types

import _common
from _common import ENGINE, MEDIA_2M, MEDIA_49

import numpy as np

from ac import i18n
from ac import media as M, review as RV, transcript as T, util as U
from ac.progress import ListEmitter
from ac.timeline import Timeline
from ac.tools import _filler_audio as FA, _filler_lexicon as LX, fillers

# ------------------------------------------------------------------ lexicon + rules (pure)
for t, want in [("Eee,", "always"), (" emm", "always"), ("Hmm...", "always"), ("eh", "always"), ("Em.", "always"),
                ("uh", "always"), ("um", "always"), ("ehm", "always"), (" anu", "anu"), ("Anuu", "anu"),
                ("ya", None), ("makan", None), (" E-mail", None), ("", None)]:
    assert LX.tier(t) == want, (t, LX.tier(t), want)
assert LX.listener_hits([[" E", 1, 1.1], ["-mail", 1.1, 1.4], [" eee", 2, 2.4], [" kita", 2.5, 2.8]]) == [2]


def rows(text, gap_after=()):
    """Synthetic v3 rows: 0.3 s per word, 0.05 s apart, 0.6 s pause after the indexes in gap_after."""
    out, t = [], 0.0
    for i, w in enumerate(text.split()):
        out.append([" " + w, round(t, 3), round(t + 0.3, 3), 0, 0.9])
        t += 0.35 + (0.6 if i in gap_after else 0.0)
    return out


ws = rows("oke kita buka menu ini ya terus kayak gitu ya apa namanya tombolnya gitu ya", gap_after=(5, 9))
hits = LX.habit_find(ws, LX.HABITS)
got = [(LX.norm(ws[i][0]) if j == i + 1 else " ".join(LX.norm(w[0]) for w in ws[i:j]), kind) for i, j, kind, _ in hits]
assert ("oke", "transition") in got, got                          # opens the phrase (start of the list)
assert ("ya", "tag") in got, got                                  # "ini ya" + pause
assert ("apa namanya", "search") in got, got                      # after the pause
assert ("gitu ya", "tag") in got, got                             # closes the last phrase
assert not any(g == ("gitu", "tag") for g in got), got            # "kayak gitu" is a phrase, never a tic
assert LX.habit_find(ws, []) == []
only_ya = LX.habit_find(ws, ["ya"])
assert only_ya and all(k == "ya" for _, _, _, k in only_ya), only_ya

# ------------------------------------------------------------------ glued cut end follows the filler's decay
# (live Premiere check: QE extract is a hard cut, so an end inside the "eee" tail kept an audible blip + click)
def tail_an(db):
    an = FA.Analysis.__new__(FA.Analysis)
    an.db = np.asarray(db, np.float32)
    return an


plateau, decay, word = [-20.0] * 30, [-21, -23, -26, -29, -33, -36], [-30, -24, -18, -17]
an = tail_an(plateau + decay + word)
assert an.tail(0.29, -20.0) == 0.33, an.tail(0.29, -20.0)            # -6 dB point (-26 at 0.32) +-1 frame -> 0.33 (-29)
assert tail_an(plateau + [-20.5] * 12 + word).tail(0.29, -20.0) == 0.29   # no decay (vowel-initial word): keep
assert tail_an(plateau + [-19, -17, -15] + word).tail(0.29, -20.0) == 0.29  # rising at once (next word): keep
assert tail_an(plateau[:29] + [-40.0] + word).tail(0.29, -20.0) == 0.29   # edge already quiet: keep
assert tail_an(plateau + [-21.0] * 20 + decay).tail(0.29, -20.0) == 0.29  # decay beyond reach (0.1 s): keep

# ------------------------------------------------------------------ params
P = fillers.params({"habits": ["YA", "bogus", "apa namanya"], "threshold": 5, "anu": 0})
assert P["habits"] == ["ya", "apa namanya"] and P["threshold"] == 0.95 and P["anu"] is False and P["always"] is True
assert fillers.params(None)["threshold"] == 0.65 and fillers.params({"threshold": "x"})["threshold"] == 0.65

if not _common.have(MEDIA_2M, MEDIA_49) or not (T.has_words(MEDIA_2M) and T.has_words(MEDIA_49)):
    print("ok (media/caches missing: offline parts only)")
    sys.exit(0)

# ------------------------------------------------------------------ features: chunk invariant + cache
x = M.load_audio(MEDIA_49)
F1, F2 = FA.features(x), FA.features(x, chunk=777)
for k in ("db", "f0", "flat", "stab"):
    assert len(F1[k]) == len(x) // FA.HOP and np.array_equal(F1[k], F2[k]), k
FA.save_features(MEDIA_49, F1)
C = FA.cached_features(MEDIA_49)
assert C is not None and all(np.array_equal(C[k], F1[k]) for k in F1)
assert FA.feature_path(MEDIA_49).name.endswith("_fillers.npz")

tmp = _common.scratch("fillers")


def job(seq, action="analyze", **params):
    return {"tool": "fillers", "action": action, "seq": seq, "params": params, "workdir": str(tmp)}


def run(seq, **params):
    em = ListEmitter()
    res = fillers.analyze(job(seq, **params), em)
    assert "error" not in em.kinds()
    return res, RV.load(res["review"]), em


# ------------------------------------------------------------------ 49 s file: no fillers, nothing pre-checked
res, doc, em = run(Timeline.from_media(MEDIA_49).to_json())
assert res["summary"]["on"] == 0, res["summary"]
stages = [e["id"] for e in em.items if e["ev"] == "stage"]
assert stages == ["words", "listen", "sound", "review"], stages
assert all(e.get("note") == "dari cache" for e in em.items if e["ev"] == "stage_done" and e["id"] in ("words", "listen"))

# ------------------------------------------------------------------ 2 min file: the true "eee" at 38.3 s
SEQ2 = Timeline.from_media(MEDIA_2M).to_json()
t0 = time.time()
res, doc, em = run(SEQ2)
assert time.time() - t0 < 30
assert not RV.validate(doc) and doc["tool"] == "fillers" and doc["timebase"] == "sequence" and doc["fps"] > 0
eee = [it for it in doc["items"] if it["kind"] == "filler" and 38.0 < it["t0"] < 38.8]
assert len(eee) == 1, [(it["t0"], it["label"]) for it in doc["items"]]
e = eee[0]
assert e["on"] and e["conf"] >= 0.65 and e["label"] == "eee" and e["tier"] == "always", e
assert e["src"] == "listener" and "acoustic" in e["srcs"] and isinstance(e["tight"], bool), e
assert e["sound"][0] >= e["t0"] - 1e-6 and e["sound"][1] <= e["t1"] + 1e-6, e
assert e["tight"] and e["t1"] >= 38.70, e           # cut end past the eee's decay (was 38.68: -22 dB blip)
assert isinstance(e["ctx"], dict) and "daftar dulu" in e["ctx"]["pre"] and e["ctx"]["post"].lower().startswith("gunakan"), e["ctx"]
assert e["note"]["type"] in ("warn", "info") and e["note"]["text"]
assert not any(it["kind"] == "habit" for it in doc["items"])            # habit words are opt-in
assert all(it["on"] == (it["conf"] >= 0.65 and it["sub"] != "drawl") for it in doc["items"])
assert res["habit_counts"].get("ya", 0) >= 1 and res["counts"]["always"] >= 1
ids = [it["id"] for it in doc["items"]]
assert len(ids) == len(set(ids))

# threshold / tier toggles / habits
_, d_hi, _ = run(SEQ2, threshold=0.9)
assert not [it for it in d_hi["items"] if it["on"] and it["conf"] < 0.9]
assert any(not it["on"] for it in d_hi["items"] if 38.0 < it["t0"] < 38.8)
_, d_noal, _ = run(SEQ2, always=False, acoustic=False)
assert not d_noal["items"], [(it["t0"], it["kind"]) for it in d_noal["items"]]
_, d_ac, _ = run(SEQ2, always=False)
assert all(it["tier"] == "acoustic" for it in d_ac["items"])
assert any(38.0 < it["t0"] < 38.8 for it in d_ac["items"])           # the "eee" is also heard acoustically
_, d_h, _ = run(SEQ2, habits=["ya", "nah"])
hab = [it for it in d_h["items"] if it["kind"] == "habit"]
assert hab and all(it["key"] in ("ya", "nah") and it["rule"] in ("tag", "transition") for it in hab)
assert all(not it["on"] for it in hab if it["tight"])                 # glued tics are listed, never pre-checked
assert any(it["on"] for it in hab)

# ------------------------------------------------------------------ timeline mapping: cut sequence, twins, mute
src = SEQ2["audio"][0]["clips"][0]


def clip(start, end, src_in):
    return {**src, "start": start, "end": end, "in": src_in, "out": src_in + (end - start)}


cut = json.loads(json.dumps(SEQ2))
cut["name"] = "Sequence 01 (AutoCut)"
cut["video"][0]["clips"] = [clip(0, 30, 0), clip(30, 55, 35)]
cut["audio"] = [{"index": 0, "name": "A1", "clips": [clip(0, 30, 0), clip(30, 55, 35)]},
                {"index": 1, "name": "A2", "clips": [clip(0, 30, 0), clip(30, 55, 35)]},          # stereo twin
                {"index": 2, "name": "A3", "muted": True, "clips": [clip(60, 100, 0)]}]           # muted: ignored
cut["duration"] = 55.0
_, dc, _ = run(cut)
m = [it for it in dc["items"] if it["kind"] == "filler" and 33.0 < it["t0"] < 33.8]
assert len(m) == 1, [(it["t0"], it["kind"]) for it in dc["items"]]
assert abs(m[0]["t0"] - (e["t0"] - 5.0)) < 0.002 and abs(m[0]["src_t"][0] - e["t0"]) < 0.002, (m[0], e)
assert all(it["t1"] <= 55.0 + 1e-6 for it in dc["items"])
assert not [it for it in dc["items"] if 30.0 - 1e-6 <= it["sound"][0] and it["src_t"][0] < 35.0 - 0.05 and it["t0"] >= 30.0]

# scope: In/Out 0..20 s -> nothing at 38 s
_, ds, _ = run(SEQ2, scope={"kind": "inout", "t0": 0, "t1": 20})
assert ds["items"] and all(it["t0"] >= -1e-6 and it["t1"] <= 20.0 + 1e-6 for it in ds["items"])

# ------------------------------------------------------------------ apply (+ carry over of manual choices)
res, doc, _ = run(SEQ2, habits=["ya"])
for it in doc["items"]:
    if it["kind"] == "habit" and it["on"]:
        it["on"], it["touched"] = False, True
        flipped = it["id"]
        break
else:
    raise AssertionError("no checked habit item to flip")
U.write_json(res["review"], doc)
jb = job(SEQ2, "apply")
jb["review"] = res["review"]
out = fillers.apply(jb, ListEmitter())
plan = out["plan"]
assert plan["kind"] == "remove_ranges" and plan["timebase"] == "sequence" and plan["ranges"]
fps = doc["fps"]
for a, b in plan["ranges"]:   # on the frame grid; plan values are rounded to 0,1 ms (<= 0,006 frame at 120 fps)
    assert b > a and abs(a * fps - round(a * fps)) < 0.01 and abs(b * fps - round(b * fps)) < 0.01, (a, b)
sel = RV.selected_ranges(doc)
assert len(plan["ranges"]) == len(sel) and all(a >= s[0] - 1e-6 and b <= s[1] + 1e-6 for (a, b), s in zip(plan["ranges"], sel))
assert out["summary"].endswith("dtk") and "filler dibuang" in out["summary"] and out["counts"]["filler"] == len(RV.selected(doc))
with i18n.using("en"):                                                    # English job: summary + notes follow it
    out_en = fillers.apply(jb, ListEmitter())
    assert out_en["summary"].endswith(" s") and " removed, " in out_en["summary"] and "dibuang" not in out_en["summary"], out_en["summary"]
    assert fillers._note({"tight": True, "sub": "orphan"})["text"].startswith("Touches a word")
    assert fillers._note({"sub": "lexicon", "sources": ["lexicon", "orphan"]})["text"] ==         "Written in the transcript, matches a hesitation sound in the audio."
res2, doc2, _ = run(SEQ2, habits=["ya"])                                   # re-run keeps the manual choice
assert [it for it in doc2["items"] if it["id"] == flipped][0]["on"] is False
bad = dict(doc, tool="silence")
U.write_json(tmp / "bad.json", bad)
jb["review"] = str(tmp / "bad.json")
try:
    fillers.apply(jb, ListEmitter())
    raise AssertionError("apply accepted a foreign review")
except U.EngineError as ex:
    assert ex.code == "BAD_REVIEW"

# ------------------------------------------------------------------ "Sekalian potong jeda" with a stand-in silence tool
import ac.tools as TOOLS  # noqa: E402

real_names, real_get = TOOLS.names, TOOLS.get


def fake_analyze(j, em):
    em.plan([("a", "A", 1), ("b", "B", 1)])
    with em.step("a"):
        em.progress(50)
    d = RV.new("silence", [RV.item(10, 11.5, "gap", label="Jeda 1,5 dtk"), RV.item(20, 22, "gap", on=False)], 126.9)
    return {"review": str(RV.save(d, RV.path_for(j["workdir"], "silence")))}


TOOLS.names = lambda: real_names() + ["silence"]
TOOLS.get = lambda t: types.SimpleNamespace(ACTIONS={"analyze": fake_analyze}) if t == "silence" else real_get(t)
try:
    res, dg, em = run(SEQ2, with_silence=True)
finally:
    TOOLS.names, TOOLS.get = real_names, real_get
gaps = [it for it in dg["items"] if it["kind"] == "gap"]
assert len(gaps) == 2 and res["silence"]["ok"] and res["silence"]["on"] == 1 and res["counts"]["gap"] == 2, res["silence"]
assert [e["id"] for e in em.items if e["ev"] == "stage"] == ["words", "listen", "sound", "gaps", "review"]
assert (tmp / "_fillers_silence" / "silence_review.json").is_file()          # never overwrites the user's own
jb = job(SEQ2, "apply")
jb["review"] = res["review"]
out = fillers.apply(jb, ListEmitter())
assert any(abs(a - 10.0) < 0.01 for a, _ in out["plan"]["ranges"]) and "1 jeda" in out["summary"], out["summary"]
TOOLS.names = lambda: [n for n in real_names() if n != "silence"]
try:
    res, dg, em = run(SEQ2, with_silence=True)                                # missing tool -> warning, no gaps
finally:
    TOOLS.names = real_names
assert not res["silence"]["ok"] and "warn" in em.kinds() and not [it for it in dg["items"] if it["kind"] == "gap"]

# ------------------------------------------------------------------ GPU lock: never taken with caches; held for both passes without
hold, release = threading.Event(), threading.Event()


def holder():
    with U.gpu_lock(label="test holder"):
        hold.set()
        release.wait(30)


th = threading.Thread(target=holder, daemon=True)
th.start()
hold.wait(5)
done = {}
worker = threading.Thread(target=lambda: done.setdefault("res", fillers.analyze(job(SEQ2), ListEmitter())), daemon=True)
worker.start()
worker.join(20)
release.set()
th.join(5)
assert "res" in done, "analyze waited for the GPU although every cache exists"

seen = []
real_words, real_listen, real_wc = fillers.T.words, fillers.T.listener_words, fillers._words_cached
fillers._words_cached = lambda p: False
fillers.T.words = lambda p, **kw: (seen.append(("words", U.GpuLock._depth)), T.cached_words(p))[1]
fillers.T.listener_words = lambda p, **kw: (seen.append(("listen", U.GpuLock._depth)), real_listen(p))[1]
try:
    run(SEQ2)
finally:
    fillers.T.words, fillers.T.listener_words, fillers._words_cached = real_words, real_listen, real_wc
assert seen == [("words", 1), ("listen", 1)], seen
assert U.GpuLock._depth == 0


def broken_listener(p, **kw):
    raise U.EngineError("WHISPER", "model small tidak ada")


fillers.T.listener_words = broken_listener
try:
    res, dl, em = run(SEQ2)                                   # listener failure degrades to transcript + acoustic
finally:
    fillers.T.listener_words = real_listen
assert any("Dengar ulang gagal" in e.get("msg", "") for e in em.items if e["ev"] == "warn")
assert dl["items"] and not any("listener" in it["srcs"] for it in dl["items"])

# ------------------------------------------------------------------ cancel + errors
em = ListEmitter()
em.cancel()
try:
    fillers.analyze(job(SEQ2), em)
    raise AssertionError("not cancelled")
except U.Cancelled:
    pass
try:
    fillers.analyze(job({"name": "x", "video": [{"index": 0, "clips": []}], "audio": []}), ListEmitter())
    raise AssertionError("empty sequence accepted")
except U.EngineError as ex:
    assert ex.code in ("NO_SEQ", "NO_AUDIO"), ex.code

# ------------------------------------------------------------------ CLI protocol (what the panel runs)
jp = tmp / "job.json"
U.write_json(jp, job(SEQ2, habits=["ya"]))
r = subprocess.run([sys.executable, "-X", "utf8", str(ENGINE / "cli.py"), "run", str(jp)], capture_output=True,
                   text=True, encoding="utf-8", timeout=120)
assert r.returncode == 0, r.stdout[-500:] + r.stderr[-500:]
evs = [json.loads(ln) for ln in r.stdout.splitlines() if ln.startswith("{")]
assert [e["stage"] for e in evs if e["ev"] == "stage"] == ["words", "listen", "sound", "review"]
result = [e for e in evs if e["ev"] == "result"]
assert len(result) == 1 and result[0]["data"]["review"] and result[0]["data"]["summary"]["n"] > 0

_common.cleanup("fillers")
print("ok")
