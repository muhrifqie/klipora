"""ac.captions template library: 45+ built-ins with valid metadata/groups, every template compiles (16:9 + 9:16),
templates action metadata (groups, colors, labels, is_new), animated sprite sheets (frames, size, cache hit, lazy
batches, user frame, LRU prune), template_mix and template_random (readable, applied through doc ops). Offline,
~10-20 s (fonts warm)."""
import copy
import json
import time

import _common
from _common import MEDIA_49

from ac.captions import ACTIONS
from ac.captions import compile as C
from ac.captions import gallery as G
from ac.captions import layout as L
from ac.captions import model as M
from ac.captions import templates as TP
from ac.progress import ListEmitter

T0 = time.perf_counter()
em = ListEmitter()
tmp = _common.scratch("captions_gallery")
G.anim_dir = lambda: tmp / "anim"            # isolate the sprite cache (LRU test deletes files)
(tmp / "anim").mkdir()

# ---------------------------------------------------------------- library content + metadata
ids = TP.builtin_ids()
assert len(ids) >= 45, len(ids)
for tid in G.CLASSIC_IDS:                    # existing ids keep working
    assert tid in ids, tid
fams = {f["family"] for f in L.font_families()}
group_ids = {g for g, _ in G.GROUPS}
seen_groups = set()
for tid in ids:
    raw, src = TP.raw(tid)
    t = TP.load(tid)
    assert src == "builtin" and t["name"] and t["description"], tid
    assert "—" not in t["description"] and "—" not in t["name"], tid          # no em dashes in UI copy
    assert t["font"]["family"] in fams, (tid, t["font"]["family"])
    assert set(t["aspect"]) <= {"9:16", "16:9", "21:9", "1:1"} and t["aspect"], tid
    if tid not in G.CLASSIC_IDS:
        assert raw.get("group") in group_ids, tid
    gs = G.groups_of(t)
    assert gs and set(gs) <= group_ids, (tid, gs)
    seen_groups |= set(gs)
    assert not G.ensure_readable(copy.deepcopy(t)), (tid, G.ensure_readable(copy.deepcopy(t)))
assert seen_groups == group_ids, group_ids - seen_groups
pro_used = set()
for tid in ids:
    st, bx, hl = TP.load(tid)["style"], TP.load(tid)["box"], TP.load(tid)["highlight"]
    pro_used |= {k for k in ("gradient", "extrude", "long_shadow", "outline2") if st.get(k)}
    pro_used |= {"shape:" + bx["shape"]} if bx.get("enabled") and bx.get("shape") != "rect" else set()
    pro_used |= {"glow"} if hl.get("glow") or st.get("glow") else set()
assert {"gradient", "extrude", "long_shadow", "outline2", "glow", "shape:sticker", "shape:banner",
        "shape:marker", "shape:ribbon"} <= pro_used, pro_used

r = ACTIONS["templates"]({"params": {"options": False}, "seq": None}, em)
assert [g["id"] for g in r["groups"]] == [g for g, _ in G.GROUPS] and all(g["count"] > 0 for g in r["groups"])
row = next(x for x in r["templates"] if x["id"] == "kuning_3d")
for k in ("group", "groups", "colors", "effects", "anim_in_label", "anim_out_label", "mode_label", "is_new",
          "aspect_fit", "font"):
    assert k in row, k
assert row["is_new"] and row["group"] == "shorts_viral" and row["aspect_fit"] == "9:16"
assert row["colors"] and all(c.startswith("#") and len(c) == 7 for c in row["colors"])
assert any(e["id"] == "extrude" for e in row["effects"]) and row["pro"]
old = next(x for x in r["templates"] if x["id"] == "hormozi_kuning")
assert not old["is_new"] and old["group"] == "shorts_viral"

# every built-in compiles in both aspects (demo words)
t1 = time.perf_counter()
for tid in ids:
    for W, H in ((1920, 1080), (1080, 1920)):
        tp = L.prep_template(TP.load(tid, W, H))
        caps = L.paginate(G._demo_words(tp), tp, W, H)
        assert caps, tid
        text, n = C.ass_text([(c, tp) for c in caps], W, H)
        assert n > 0, (tid, W, H)
compile_s = time.perf_counter() - t1

# ---------------------------------------------------------------- animated sprite sheets
from PIL import Image  # noqa: E402

job = {"tool": "captions", "params": {"anim": True, "ids": ["kuning_3d", "berita_terkini", "komik"], "limit": 2},
       "seq": None, "workdir": str(tmp)}
t1 = time.perf_counter()
a1 = ACTIONS["gallery"](job, em)
cold = time.perf_counter() - t1
assert len(a1["anims"]) == 2 and a1["pending"] == ["komik"] and not a1["errors"], a1
info = a1["anims"]["kuning_3d"]
assert info["frames"] == 16 and info["w"] == 320 and info["h"] == 160 and info["cols"] == 16 and not info["cached"]
im = Image.open(info["png"])
assert im.size == (320 * 16, 160)
frames = [im.crop((k * 320, 0, k * 320 + 320, 160)).convert("L") for k in range(16)]
import numpy as np  # noqa: E402

arr = [np.asarray(f, dtype=np.int16) for f in frames]
diffs = [float(np.abs(arr[k] - arr[k + 1]).mean()) for k in range(15)]
assert sum(d > 0.5 for d in diffs) >= 5, diffs              # it animates (pop, karaoke, page change)
assert float(np.abs(arr[0] - arr[8]).max()) > 100            # frame 0 = before the first word, frame 8 = text
# second call: cached (fast), the pending one gets rendered
t1 = time.perf_counter()
a2 = ACTIONS["gallery"](dict(job, params=dict(job["params"], limit=None)), em)
assert a2["anims"]["kuning_3d"]["cached"] and not a2["anims"]["komik"]["cached"] and not a2["pending"]
a3 = ACTIONS["gallery"](dict(job, params={"anim": True, "ids": ["kuning_3d", "senja"], "cached_only": True}), em)
assert a3["anims"]["kuning_3d"]["cached"] and a3["pending"] == ["senja"]
warm = time.perf_counter() - t1
# user frame: different cache entry, same geometry
if _common.have(MEDIA_49):
    from ac.media import probe  # noqa: E402
    d = probe(str(MEDIA_49))["duration"]
    seq = {"name": "AC Gallery", "fps": 30, "width": 2292, "height": 960, "duration": d, "player": 0,
           "video": [{"index": 0, "name": "V1", "clips": [{"name": "x.mp4", "path": str(MEDIA_49), "start": 0, "end": d,
                                                              "in": 0, "out": d}]}], "audio": []}
    a4 = ACTIONS["gallery"](dict(job, seq=seq, params={"anim": True, "ids": ["kuning_3d"], "t": 20.0}), em)
    assert a4["frame"] == "user" and a4["anims"]["kuning_3d"]["png"] != info["png"]
    assert Image.open(a4["anims"]["kuning_3d"]["png"]).size == (320 * 16, 160)
# LRU prune keeps the newest, protects the given files
files = sorted((tmp / "anim").glob("*.png"), key=lambda p: p.stat().st_mtime)
assert len(files) >= 3
removed = G.prune(limit_mb=0, protect=[str(files[-1])])
left = list((tmp / "anim").glob("*.png"))
assert removed == len(files) - 1 and [str(p) for p in left] == [str(files[-1])]

# ---------------------------------------------------------------- template_mix / template_random
mix = ACTIONS["template_mix"]({"params": {"parts": {"font": "kuning_3d", "box": "podcast", "highlight": "stiker_viral",
                                                    "anim": "komik"}, "base": "boxed"}, "seq": None}, em)
assert mix["template"] == "boxed" and mix["full"]["font"]["family"] == "Montserrat Black"
assert mix["full"]["box"]["per"] == "page" and mix["full"]["anim"]["in"] == "none"
assert mix["ops"][1] == {"op": "doc_style", "style": mix["style"], "replace": True}
assert G.contrast(mix["full"]["style"]["fill"], mix["full"]["box"]["color"]) >= 4.5     # readability fix applied
try:
    ACTIONS["template_mix"]({"params": {"parts": {"warna": "boxed"}}, "seq": None}, em)
    raise AssertionError("bad part accepted")
except Exception as e:  # noqa: BLE001
    assert getattr(e, "code", "") == "BAD_PARAMS", e

seeds = {}
for s in range(1, 41):
    for W, H in ((1080, 1920), (1920, 1080)):
        rr = G.template_random(s, W, H)
        f = rr["full"]
        assert not G.ensure_readable(copy.deepcopy(f)), (s, G.ensure_readable(copy.deepcopy(f)))    # already fixed
        cap = G._cap_ratio(f["font"]["family"]) * f["font"]["size"]
        assert G.SIZE_CAP["min"] <= cap <= G.SIZE_CAP["word_max"] + 0.5, (s, cap)
        bg = G._box_bg(f)
        if bg is not None:
            assert G.contrast(f["style"]["fill"], bg) >= 4.5, s
        seeds[(s, W)] = json.dumps(rr["style"], sort_keys=True)
assert G.template_random(7, 1080, 1920)["style"] == G.template_random(7, 1080, 1920)["style"]   # seeded = stable
assert len(set(seeds.values())) > 60
rnd = ACTIONS["template_random"]({"params": {"seed": 3}, "seq": {"width": 1080, "height": 1920}}, em)
assert rnd["seed"] == 3 and rnd["name"].startswith("Acak")

# the returned ops apply to a real doc and paginate
if _common.have(MEDIA_49):
    from ac.timeline import Timeline  # noqa: E402
    seqv = json.loads(json.dumps(seq))
    seqv["audio"] = [{"index": 0, "name": "A1", "clips": [dict(seqv["video"][0]["clips"][0])]}]
    seqv["width"], seqv["height"] = 1080, 1920
    djob = {"tool": "captions", "seq": seqv, "workdir": str(tmp), "params": {"transcribe": False}}
    try:
        ACTIONS["doc"](djob, em)
        for res in (mix, rnd):
            ACTIONS["doc"](dict(djob, seq=None, params={"ops": res["ops"]}), em)
            d = M.load(tmp / "captions.json")
            assert d["template"] == res["template"] and d["style"] == res["style"] and d["pages"]
            pr, W, H = M.page_pairs(d)
            assert C.ass_text(pr, W, H)[1] > 0
    except Exception as e:  # noqa: BLE001
        if getattr(e, "code", "") != "NO_WORDS":     # no cached transcript on this PC: skip that part
            raise
        print("SKIP doc apply (no cached transcript)")

# ---------------------------------------------------------------- English (job language "en")
from ac import i18n  # noqa: E402
with i18n.using("en"):
    for tid in ids:                                       # every built-in has an English name + description
        assert i18n.raw(f"cap.tpl.{tid}.name", "en") and i18n.raw(f"cap.tpl.{tid}.desc", "en"), tid
    gold = TP.summary(TP.load("judul_emas"))
    assert gold["name"] == "Gold Title" and gold["description"].startswith("DM Serif with a gold gradient"), gold
    ren = ACTIONS["templates"]({"params": {"options": True}, "seq": None}, em)
    hv = next(x for x in ren["templates"] if x["id"] == "hijau_viral")
    assert hv["name"] == "Viral Green" and hv["group_label"] == "Viral Shorts", (hv["name"], hv["group_label"])
    assert next(x for x in ren["templates"] if x["id"] == "berita_terkini")["name"] == "Breaking News"
    assert next(g for g in ren["groups"] if g["id"] == "jualan")["label"] == "Sales/Promo"
    k3 = next(x for x in ren["templates"] if x["id"] == "kuning_3d")
    assert k3["name"] == "Yellow 3D" and k3["effects"][0]["label"] == "3D"
    op = ren["options"]
    assert op["highlight_presets"][0]["label"] == "No highlight" and op["groups"][0]["label"] == "Font"
    assert next(f for f in op["schema"] if f["path"] == "anim.in_dur")["unit"] == "s"
    assert next(f for f in op["schema"] if f["path"] == "font.family")["help"].startswith("Bundled fonts")
    assert next(o for o in op["punct"] if o["id"] == "soft")["label"] == "Remove commas & periods"
    assert ACTIONS["template_random"]({"params": {"seed": 3}, "seq": {"width": 1080, "height": 1920}}, em)["name"] == "Random #0003"
    mx = ACTIONS["template_mix"]({"params": {"parts": {"font": "kuning_3d"}, "base": "boxed"}, "seq": None}, em)
    assert mx["name"] == "Mix of Yellow 3D", mx["name"]
    user = TP.save_user({"name": "Gaya Toko Saya", "style": {"fill": "#FFE600"}}, "capc_en_user")
    try:
        assert TP.summary(user)["name"] == "Gaya Toko Saya"          # user templates keep the user's own name
    finally:
        TP.delete_user(user["id"])
assert TP.summary(TP.load("judul_emas"))["name"] == "Judul Emas"        # back to Indonesian
assert TP.options()["highlight_presets"][0]["label"] == "Tanpa sorot"

_common.cleanup("captions_gallery")
print(f"templates {len(ids)}, compile both aspects {compile_s:.1f} s, anim cold(2) {cold:.2f} s, warm {warm:.2f} s, "
      f"total {time.perf_counter() - T0:.1f} s")
print("ok")
