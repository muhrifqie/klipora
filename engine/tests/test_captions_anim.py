"""ac.captions animation studio (anim.py) + sound effects per word (sfx.py): preset library and spec validation,
easing, keyframe evaluation (stagger, order, active release, loop), compile hooks (classic templates unchanged,
<= 35 ms pieces, letter spacing / colour tracks, neighbour push), user animations, sprite sheets, SFX library levels,
rule plan (priorities, min gap, rate limit), mixed WAV placement and the engine actions. Offline (cached transcript
of the 49 s test file, no AI). ~10-20 s."""
import json
import os
import re
import time
import wave

import _common
from _common import MEDIA_49, scratch, cleanup

import numpy as np

SB = scratch("captions_anim")
os.environ["APPDATA"] = str(SB / "appdata")          # user animations / sounds land in the sandbox

from ac.captions import ACTIONS                       # noqa: E402
from ac.captions import anim as A                     # noqa: E402
from ac.captions import compile as C                  # noqa: E402
from ac.captions import layout as L                   # noqa: E402
from ac.captions import model as M                    # noqa: E402
from ac.captions import sfx as X                      # noqa: E402
from ac.progress import ListEmitter                   # noqa: E402
from ac.timeline import Timeline                      # noqa: E402

T0 = time.perf_counter()
em = ListEmitter()

# ---------------------------------------------------------------- catalog + specs
cat = ACTIONS["anim"]({"params": {}, "seq": None}, em)
groups = {g["id"] for g in cat["groups"]}
assert groups == {"in", "active", "out", "loop"}, groups
assert len(cat["presets"]) >= 30, len(cat["presets"])
for g in groups:
    assert sum(1 for p in cat["presets"] if p["group"] == g) >= 5, g
ids = [p["id"] for p in cat["presets"]]
assert len(ids) == len(set(ids))
for p in cat["presets"]:
    sp = A.norm_spec(p["id"])
    assert sp and sp["group"] == p["group"] and sp["tracks"], p["id"]
    assert p["label"] and "—" not in p["label"], p["id"]
    for name, keys in sp["tracks"].items():
        assert name in A.TRACKS and keys == sorted(keys, key=lambda k: k["t"]), (p["id"], name)
assert {t["id"] for t in cat["tracks"]} == set(A.TRACKS)
assert A.norm_spec(None) is None and A.norm_spec("nope") is None and A.norm_spec({"tracks": {}}) is None
o = A.norm_spec({"preset": "pop_kata", "dur": 0.5, "stagger": 0.1})
assert o["dur"] == 0.5 and o["stagger"] == 0.1 and o["tracks"] == A.PRESETS["pop_kata"]["tracks"]
inl = A.norm_spec({"group": "in", "tracks": {"scale": [[0, 9], [1, 1, "bad_ease"]], "color": [[0, "#ff0000"], [1, "x"]],
                                             "nope": [[0, 1]]}})
assert inl["tracks"]["scale"][0]["v"] == 5.0 and inl["tracks"]["scale"][1]["e"] == "linear"   # clamped / cleaned
assert inl["tracks"]["color"][0]["v"] == "#FF0000" and inl["tracks"]["color"][1]["v"] is None and "nope" not in inl["tracks"]
assert A.norm_spec("aktif_pop", "loop")["group"] == "loop"          # a spec dropped on another role plays as it

# easing endpoints + shapes
for e, _ in A.EASINGS:
    assert abs(A.ease(e, 0)) < 1e-9 and abs(A.ease(e, 1) - 1) < 1e-6, e
assert A.ease("back", 0.7) > 1.0 and A.ease("steps:3", 0.5) == 1 / 3 and A.ease("in", 0.5) < 0.5 < A.ease("out", 0.5)
assert A.track_at(A.norm_keys("color", [[0, "#000000"], [1, None]]), 0.5, "#FFFFFF") == "#808080"
assert A._ranks(5, "center") == [3, 1, 0, 2, 4] and A._ranks(4, "reverse") == [3, 2, 1, 0]
assert sorted(A._ranks(6, "random", 3)) == list(range(6))


# ---------------------------------------------------------------- compile hooks
def seq(W, H):
    c = {"name": MEDIA_49.name, "path": str(MEDIA_49), "start": 0.0, "end": 48.9, "in": 0.0, "out": 48.9}
    return {"id": f"an{W}", "name": "AC Anim Test", "fps": 120, "width": W, "height": H, "duration": 48.9,
            "player": 12.0, "video": [{"index": 0, "name": "V1", "clips": [c]}],
            "audio": [{"index": 0, "name": "A1", "clips": [dict(c)]}]}


if not _common.have(MEDIA_49):   # compile hooks need the cached transcript of the real clip
    raise SystemExit(0)
tl = Timeline.from_json(seq(1080, 1920))
base_doc = M.build(tl, tl.words_on_timeline(transcribe=False), None, {}, None, template="hormozi_kuning")
assert base_doc["pages"], "cached transcript"


def events(style, template="hormozi_kuning"):
    d = json.loads(json.dumps(base_doc))
    M.apply_ops(d, [{"op": "template", "id": template}, {"op": "doc_style", "style": style}])
    M.repaginate(d)
    pairs, W, H = M.page_pairs(d)
    text, n = C.ass_text(pairs, W, H)
    lines = text.splitlines()
    return d, pairs, lines[lines.index("[Events]") + 2:]


def secs(ts):
    h, m, s = ts.split(":")
    return int(h) * 3600 + int(m) * 60 + float(s)


def span(ev):
    f = ev.split(",", 4)
    return secs(f[1]), secs(f[2])


for tid in ("hormozi_kuning", "tutorial_bersih", "karaoke"):
    assert events({}, tid)[2] == events({"anim": {"fx": None}}, tid)[2] == events({"anim": {"fx": {}}}, tid)[2], tid

# in: per-word stagger, words start invisible, pieces <= 35 ms inside the animation
d, pairs, ev = events({"anim": {"fx": {"in": "pop_kata"}, "in": "fade"}})
cap0, tpl0 = pairs[0]
P0 = cap0.start
boxes, bbox, fit = L.layout(cap0, tpl0, 1080, 1920)
fx = A.page_fx(tpl0, boxes, cap0.start, cap0.end, 1080, 1920)
for i, b in enumerate(boxes):                     # every piece of a word inside its own entrance is <= 35 ms
    s0, du = fx._start("in", i), fx.t["in"][0]
    mine = [e for e in ev if e.endswith("}" + C.ass_escape(b.w.text)) and s0 - 0.005 <= span(e)[0] < s0 + du - 0.04]
    assert mine and all(span(e)[1] - span(e)[0] <= 0.036 for e in mine), (i, b.w.text, mine[:2])
assert fx and fx.override("fade", "fade") == ("none", "fade")
w0, w1 = fx.word(0, P0), fx.word(1, P0 + 0.03)
assert w0["s"] < 0.3 and w0["a"] == 0.0, w0
assert fx.word(0, P0 + 0.5)["s"] == 1.0 and fx.word(0, P0 + 0.5)["a"] == 1.0
st = fx.t["in"][1]
assert st > 0 and abs(fx._start("in", 1) - fx._start("in", 0) - st) < 1e-9
# a short page compresses dur + stagger into 60 % of its time
short = A.PageFx({"in": A.norm_spec({"preset": "pop_kata", "stagger": 0.3})}, boxes, 0, 0.5, "karaoke", 1920, 1)
assert short.t["in"][0] + short.t["in"][1] * (len(boxes) - 1) <= 0.3 + 1e-6

# out: page fade replaced, words leave in order
_, pairs_o, ev_o = events({"anim": {"fx": {"out": "keluar_turun"}}})
c1, t1 = pairs_o[1]
b1, _, _ = L.layout(c1, t1, 1080, 1920)
fo = A.page_fx(t1, b1, c1.start, c1.end, 1080, 1920)
assert fo.word(0, c1.end - 0.001)["a"] < 0.1 and fo.word(0, c1.start + 0.05)["a"] == 1.0
assert fo.word(len(b1) - 1, c1.end - 0.001)["dy"] > 0

# active: pop while spoken, back to rest after release; neighbours make room (push)
_, pairs_a, ev_a = events({"anim": {"fx": {"active": "aktif_pop"}}, "highlight": {"scale": 1.0}})
ca, ta = pairs_a[2]
ba, _, _ = L.layout(ca, ta, 1080, 1920)
fa = A.page_fx(ta, ba, ca.start, ca.end, 1080, 1920)
act = [(w.start, max(w.end, w.start + 0.05)) for w in ca.words]
fa.bind(act)
a0, a1 = act[1]
assert abs(fa.word(1, a0 + 0.2)["s"] - 1.18) < 0.02 and fa.word(1, a0 - 0.01)["s"] == 1.0 and fa.pushes
assert fa.word(1, a1 + 0.5)["s"] == 1.0
assert any("\\fscx118" in e or "\\fscx117" in e for e in ev_a), "active scale in the ASS"

# loop: periodic, every word sampled across the page
fl = A.PageFx({"loop": A.norm_spec("loop_gelombang")}, ba, 0.0, 5.0, "karaoke", 1920, 1)
assert abs(fl.word(0, 0.3)["dy"] - fl.word(0, 1.5)["dy"]) < 1e-9 and fl.word(0, 0.3)["dy"] < 0
assert len(fl.windows(0)) >= 10

# letter spacing + colour tracks reach the ASS; spacing piece tags override the static \fsp
_, _, ev_s = events({"anim": {"fx": {"in": "renggang_masuk"}}})
assert any(re.search(r"\\3a&H[0-9A-F]{2}&\\fsp[1-9]", e) for e in ev_s), "animated \\fsp after the colour tags"
_, _, ev_c = events({"anim": {"fx": {"in": "kilat_warna"}}})
assert any("\\1c&H00E6FF&" in e for e in ev_c)                   # #FFE600 flash
# page unit: the whole block (and its box) moves together
_, _, ev_p = events({"anim": {"fx": {"in": "blok_naik"}}, "box": {"enabled": True, "per": "page"}})
assert any("\\move(" in e and "\\p1" in e for e in ev_p), "box follows a page-unit entrance"
# every preset compiles in karaoke and reveal mode
for pid in ids:
    g = A.PRESETS[pid]["group"]
    for mode in ("karaoke", "reveal"):
        _, _, evp = events({"anim": {"fx": {g: pid}}, "timing": {"mode": mode}})
        assert evp, (pid, mode)

# ---------------------------------------------------------------- user animations
saved = ACTIONS["anim_save"]({"params": {"anim": {"group": "in", "dur": 0.4, "tracks": {"y": [[0, 50, "out"], [1, 0]]}},
                                         "name": "Naik saya"}, "seq": None}, em)
assert saved["id"] == "u_naik_saya" and (SB / "appdata" / "Klipora" / "caption_anims.json").is_file()
assert A.norm_spec("u_naik_saya")["tracks"]["y"][0]["v"] == 50
assert any(u["id"] == "u_naik_saya" for u in ACTIONS["anim"]({"params": {}}, em)["user"])
_, _, ev_u = events({"anim": {"fx": {"in": "u_naik_saya"}}})
assert ev_u
try:
    ACTIONS["anim_save"]({"params": {"anim": {"tracks": {}}}}, em)
    raise AssertionError("empty animation must fail")
except Exception as e:  # noqa: BLE001
    assert getattr(e, "code", "") == "BAD_ANIM", e
assert ACTIONS["anim_save"]({"params": {"delete": "u_naik_saya"}}, em)["deleted"]
assert A.norm_spec("u_naik_saya") is None

# ---------------------------------------------------------------- sprites (frame strips)
sp = ACTIONS["anim_sprites"]({"params": {"ids": ["pop_kata", "aktif_goyang"], "frames": 8, "size": [120, 44],
                                          "specs": {"mine": {"group": "loop", "dur": 1, "tracks": {"rot": [[0, 0], [0.5, 10], [1, 0]]}}},
                                          "force": True}}, em)
assert not sp["errors"] and set(sp["sprites"]) == {"pop_kata", "aktif_goyang", "mine"}, sp
from PIL import Image  # noqa: E402
im = np.asarray(Image.open(sp["sprites"]["pop_kata"]["png"]).convert("L")).astype(int)
assert im.shape == (44, 960), im.shape
cells = [im[:, i * 120:(i + 1) * 120] for i in range(8)]
lit = [int((c > 200).sum()) for c in cells]
assert lit[0] < 5 and lit[-1] > 60 and lit[-1] >= lit[2], lit         # nothing at P0, full phrase at the end
again = ACTIONS["anim_sprites"]({"params": {"ids": ["pop_kata"], "frames": 8, "size": [120, 44]}}, em)
assert again["sprites"]["pop_kata"]["png"] == sp["sprites"]["pop_kata"]["png"]

# ---------------------------------------------------------------- SFX library
lib = ACTIONS["sfx_library"]({"params": {}}, em)
assert len([s for s in lib["sounds"] if s["source"] == "builtin"]) >= 12
need = {"pop", "whoosh", "swoosh", "ding", "click", "boom", "riser", "glitch", "cash", "bubble", "typewriter", "camera"}
assert need <= {s["id"] for s in lib["sounds"]}
for s in lib["sounds"]:
    x = X.read_wav(s["path"])
    pk = 20 * np.log10(np.abs(x).max())
    assert x.size > 100 and -8.5 <= pk <= -0.9, (s["id"], pk)
    with wave.open(s["path"], "rb") as w:
        assert w.getframerate() == 48000 and w.getnchannels() == 1 and w.getsampwidth() == 2

# user sound: convert + normalize, then delete
src = SB / "my.wav"
X.write_wav(src, 0.05 * np.sin(np.linspace(0, 2 * np.pi * 440, 48000)).astype(np.float32))
add = ACTIONS["sfx_add"]({"params": {"path": str(src), "name": "Tepuk Saya"}}, em)
assert add["sound"]["id"] == "u_tepuk_saya" and add["sound"]["source"] == "user"
xu = X.read_wav(add["sound"]["path"])
assert abs(20 * np.log10(np.sqrt((xu ** 2).mean())) - X.TARGET_RMS_DB) < 0.5
assert ACTIONS["sfx_add"]({"params": {"delete": "u_tepuk_saya"}}, em)["deleted"]

# ---------------------------------------------------------------- SFX plan
wd = SB / "wd"
wd.mkdir()
doc_path = wd / "captions.json"
d = json.loads(json.dumps(base_doc))
M.apply_ops(d, [{"op": "style", "ids": [d["pages"][3]["lines"][0][0]], "style": {"color": "#FF4D4D", "emoji": "🔥"}}])
M.repaginate(d)
M.save(d, doc_path)
job = {"tool": "captions", "seq": None, "workdir": str(wd), "params": {}}
r = ACTIONS["sfx_plan"](dict(job, params={"rules": {"halaman": {"on": True}, "emoji": {"on": True}},
                                          "max_per_min": 200, "min_gap": 0.0}), em)
hits = r["hits"]
assert r["stats"]["per_rule"]["halaman"] >= len(d["pages"]) - 3, r["stats"]
emph = [h for h in hits if h["word"] == d["pages"][3]["lines"][0][0]]
assert emph and emph[0]["rule"] == "penting", emph                   # priority: penting > emoji > halaman
nums = [w for w in d["words"] if any(ch.isdigit() for ch in w["text"]) and not w.get("hide")]
if nums:
    assert any(h["rule"] == "angka" for h in hits)
r2 = ACTIONS["sfx_plan"](dict(job, params={"rules": {"halaman": {"on": True, "sound": "whoosh"}},
                                           "max_per_min": 6, "min_gap": 1.5}), em)
h2 = r2["hits"]
assert all(b["t"] - a["t"] >= 1.5 - 1e-6 for a, b in zip(h2, h2[1:])), "min gap"
assert all(sum(1 for o in h2 if h["t"] <= o["t"] < h["t"] + 60) <= 6 for h in h2), "rate limit (any 60 s window)"
wh = [h for h in h2 if h["sound"] == "whoosh"]
assert wh and all(abs(h["t"] - (h["at"] - 0.03 - 0.25)) < 1e-3 or h["t"] == 0 for h in wh), "whoosh lead"
assert r2["stats"]["dropped_gap"] + r2["stats"]["dropped_rate"] > 0
# AI emphasis with AI switched off in settings -> cached AI result or the rule fallback (never a request)
(SB / "appdata" / "Klipora").mkdir(parents=True, exist_ok=True)
(SB / "appdata" / "Klipora" / "settings.json").write_text('{"ai": false}', encoding="utf-8")
ra = ACTIONS["sfx_plan"](dict(job, params={"ai": True, "max_per_min": 60, "min_gap": 0.3}), em)
assert ra["ai"] in ("fallback", "cache") and ra["stats"]["per_rule"]["penting"] >= 5, (ra["ai"], ra["stats"])
off = ACTIONS["sfx_plan"](dict(job, params={"rules": {k: {"on": False} for k in X.RULES}}), em)
assert off["hits"] == []

# ---------------------------------------------------------------- SFX render (mixed WAV, versioned)
rr = ACTIONS["sfx_render"](dict(job, params={"rules": {"halaman": {"on": True}}, "volume": 0}), em)
pl = rr["plan"]
assert pl["kind"] == "audio_track" and pl["track"] == "Klipora SFX" and pl["path"].endswith("_sfx_v1.wav"), pl
y = X.read_wav(pl["path"])
assert abs(y.size / 48000 - pl["duration"]) < 0.01
first_hit = rr["hits"][0]
assert abs(pl["start"] - first_hit["t"]) < 1e-3
for h in rr["hits"][:5]:                                    # sound energy right at every hit
    i = int(round((h["t"] - pl["start"]) * 48000))
    assert np.abs(y[i:i + 2400]).max() > 0.05, h
rr2 = ACTIONS["sfx_render"](dict(job, params={"hits": rr["hits"][:3]}), em)
assert rr2["plan"]["path"].endswith("_sfx_v2.wav") and rr2["plan"]["previous"] == pl["path"]
assert M.load(doc_path)["sfx"]["version"] == 2
rc = ACTIONS["sfx_render"](dict(job, params={"hits": rr["hits"][:4], "mode": "clips"}), em)
assert rc["plan"]["kind"] == "audio_clips" and len(rc["plan"]["items"]) == 4
assert all(os.path.isfile(it["path"]) for it in rc["plan"]["items"])
try:
    ACTIONS["sfx_render"](dict(job, params={"rules": {k: {"on": False} for k in X.RULES}}), em)
    raise AssertionError("no hits must fail")
except Exception as e:  # noqa: BLE001
    assert getattr(e, "code", "") == "NO_SFX", e
stale = [dict(h, sound="suara_dihapus") for h in rr["hits"][:3]]            # sound deleted after planning
for mode in ("mixed", "clips"):
    try:
        ACTIONS["sfx_render"](dict(job, params={"hits": stale, "mode": mode}), em)
        raise AssertionError("stale sounds must fail")
    except Exception as e:  # noqa: BLE001
        assert getattr(e, "code", "") == "NO_SFX" and "sudah tidak ada" in str(e), (mode, e)
rp = ACTIONS["sfx_render"](dict(job, params={"hits": rr["hits"][:2] + stale}), em)
assert rp["dropped"] == 3 and "2 bunyi" in rp["summary"] and "3 dilewati" in rp["summary"], rp["summary"]

# ---------------------------------------------------------------- English (job language "en")
from ac import i18n  # noqa: E402
with i18n.using("en"):
    ce = ACTIONS["anim"]({"params": {}, "seq": None}, em)
    assert [g["label"] for g in ce["groups"]] == ["In", "Active word", "Out", "Loop"], ce["groups"]
    lab = {p["id"]: p["label"] for p in ce["presets"]}
    assert lab["pudar_halus"] == "Soft fade" and lab["pop_kata"] == "Word pop" and lab["loop_getar"] == "Shake"
    assert all(not v.startswith("cap.") for v in lab.values())
    assert next(t for t in ce["tracks"] if t["id"] == "spacing")["label"] == "Letter spacing"
    assert next(e for e in ce["easings"] if e["id"] == "in_out")["label"] == "Smooth"
    assert [u["label"] for u in ce["units"]] == ["Each word", "One block"]
    assert [r["label"] for r in X.rules_catalog()] == ["Numbers", "Important words", "Emoji", "Every page"]
    assert next(x for x in X.library() if x["id"] == "cash")["label"] == "Cha-ching"
    try:
        ACTIONS["anim_save"]({"params": {"anim": {"tracks": {}}}}, em)
        raise AssertionError("empty animation must fail")
    except Exception as e:  # noqa: BLE001
        assert getattr(e, "code", "") == "BAD_ANIM" and "Empty animation" in str(e), e
assert ACTIONS["anim"]({"params": {}, "seq": None}, em)["groups"][0]["label"] == "Masuk"
assert X.rules_catalog()[1]["label"] == "Kata penting" and X.is_track("AutoCut SFX") and X.is_track(X.TRACK)

cleanup("captions_anim")
print(f"OK test_captions_anim ({len(ids)} presets, {len(lib['sounds'])} sounds) {time.perf_counter() - T0:.1f}s")
