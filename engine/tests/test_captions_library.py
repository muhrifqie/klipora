"""ac.captions.library: "Gaya Saya" user template library (save from a doc, list with favourites first + WIB times,
rename, duplicate, favourite, delete, export with font notes, import with validation / sanitising / name de-dupe)
and the last used style per aspect class (set from a doc, get with ready ops, template-deleted fallback, clear).
Offline, %APPDATA% sandboxed (never touches the real user library), cached transcript of the 49 s test file. ~5 s."""
import json
import os
import time

import _common
from _common import MEDIA_49

tmp = _common.scratch("captions_library")
os.environ["APPDATA"] = str(tmp / "appdata")          # isolate caption_templates / caption_library / caption_last
os.environ["AI_DISABLED"] = "1"

from ac.captions import ACTIONS
from ac.captions import library as LB
from ac.captions import model as M
from ac.captions import templates as TP
from ac.progress import ListEmitter
from ac.util import EngineError, appdata_dir

T0 = time.perf_counter()
em = ListEmitter()
assert str(appdata_dir()).startswith(str(tmp)), appdata_dir()
lib = lambda **p: ACTIONS["library"]({"tool": "captions", "seq": None, "workdir": str(tmp / "wd"), "params": p}, em)
last = lambda seq=None, **p: ACTIONS["last_style"]({"tool": "captions", "seq": seq, "workdir": str(tmp / "wd"), "params": p}, em)

# ---------------------------------------------------------------- empty library, helpers
assert lib(op="list")["templates"] == []
assert LB.aspect_class(1080, 1920) == "vertical" and LB.aspect_class(2292, 960) == "horizontal" and LB.aspect_class(1080, 1080) == "square"
assert LB.clean_name("  Gaya—Toko \n baru ") == "Gaya-Toko baru" and LB.clean_name("") == "Gaya saya"
try:
    lib(op="nope")
    raise AssertionError("unknown op must fail")
except EngineError as e:
    assert e.code == "BAD_PARAMS"

# ---------------------------------------------------------------- save from a template dict (no doc needed)
r1 = lib(op="save", name="Gaya Toko", template={"font": {"family": "Anton", "size": 96}, "style": {"fill": "#FFE600"}},
         aspect="vertical")
t1 = r1["template"]
assert t1["source"] == "user" and t1["name"] == "Gaya Toko" and t1["aspect_class"] == "vertical" and t1["thumb"], t1
assert t1["created"].endswith("+07:00") and t1["created_ms"] and t1["font"] == "Anton" and t1["font_source"] == "bundled"
full1 = TP.load(t1["id"])
assert full1["font"]["size"] == 96 and full1["style"]["fill"] == "#FFE600"
r2 = lib(op="save", name="Gaya Toko", template={"style": {"fill": "#22E58B"}})       # same name -> "(2)"
assert r2["template"]["name"] == "Gaya Toko (2)" and r2["id"] != t1["id"], r2["template"]["name"]

# ---------------------------------------------------------------- save from a real captions doc (+ last used)
if _common.have(MEDIA_49):
    clip = {"name": MEDIA_49.name, "path": str(MEDIA_49), "start": 0.0, "end": 48.9, "in": 0.0, "out": 48.9}
    seq = {"id": "v1", "name": "AC Library Test 9x16", "fps": 30, "width": 1080, "height": 1920, "duration": 48.9,
           "player": 5.0, "video": [{"index": 0, "name": "V1", "clips": [clip]}],
           "audio": [{"index": 0, "name": "A1", "clips": [dict(clip)]}]}
    job = {"tool": "captions", "seq": seq, "workdir": str(tmp / "wd")}
    res = ACTIONS["doc"](dict(job, action="doc", params={"transcribe": False, "template": "kuning_3d",
                                                         "ops": [{"op": "doc_style", "style": {"font": {"size": 88}, "highlight": {"color": None}}, "replace": True}]}), em)
    d = M.load(res["doc"])
    assert d["template"] == "kuning_3d" and d["style"]["highlight"]["color"] is None
    sv = ACTIONS["library"](dict(job, seq=None, params={"op": "save", "name": "Kuning Saya"}), em)["template"]
    fs = TP.load(sv["id"])
    assert sv["aspect_class"] == "vertical" and fs["font"]["size"] == 88 and fs["highlight"]["color"] is None, fs["highlight"]
    assert "Kuning 3D" in fs["description"]
    old_sv = ACTIONS["save_template"](dict(job, seq=None, params={"from_doc": True, "name": "Lewat Save Template"}), em)
    assert TP.load(old_sv["id"])["highlight"]["color"] is None, "save_template keeps explicit nulls of doc.style too"
    assert LB.delete(old_sv["id"])
    # last used: set from the doc, get with ops that reproduce the look
    s1b = ACTIONS["last_style"](dict(job, seq=None, params={"op": "set", "from_doc": True}), em)
    assert s1b["aspect"] == "vertical" and s1b["changed"] is True and s1b["template"] == "kuning_3d", s1b
    s2 = ACTIONS["last_style"](dict(job, seq=None, params={"op": "set", "from_doc": True}), em)
    assert s2["changed"] is False, "no rewrite when nothing changed"
    g = last({"width": 1080, "height": 1920}, op="get", thumb=True)
    lv = g["last"]
    assert g["aspect"] == "vertical" and lv["template"] == "kuning_3d" and lv["ops"][0] == {"op": "template", "id": "kuning_3d"}
    assert lv["ops"][1]["replace"] is True and lv["ops"][1]["style"]["highlight"]["color"] is None and lv["thumb"]
    assert lv["params"]["hide_fillers"] is True and lv["max_words"] and lv["updated"].endswith("+07:00")
    # the ops rebuild the same look on a fresh doc (new sequence, same aspect)
    job2 = dict(job, workdir=str(tmp / "wd2"))
    res2 = ACTIONS["doc"](dict(job2, action="doc", params={"transcribe": False, "template": lv["template"], "ops": lv["ops"][1:]}), em)
    d2 = M.load(res2["doc"])
    assert d2["template"] == "kuning_3d" and d2["style"] == d["style"] and d2["pages"], d2["style"]
    assert last({"width": 1920, "height": 1080}, op="get")["last"] is None, "horizontal class is still empty"

# ---------------------------------------------------------------- last used without a doc (panel sends the fields)
st = {"font": {"family": "Rubik Black"}, "box": {"enabled": True, "shape": "marker"}}
s3 = last({"width": 2292, "height": 960, "name": "Tutorial"}, op="set", template="tutorial_bersih", style=st,
          params={"hide_fillers": False, "numbers": True, "censor": "off", "glossary": ["TokoKita"], "junk": 1})
assert s3["aspect"] == "horizontal" and s3["changed"]
lh = last({"width": 2292, "height": 960}, op="get")["last"]
assert lh["style"] == st and lh["params"] == {"hide_fillers": False, "numbers": True, "censor": "off", "glossary": ["TokoKita"]}
# template deleted -> ops rebuild the snapshot on the aspect default
tmp_t = lib(op="save", name="Sementara", template={"font": {"family": "Bungee", "size": 70}, "style": {"fill": "#38BDF8"}})["id"]
last({"width": 1080, "height": 1080}, op="set", template=tmp_t, style={"layout": {"y": 60}})
lib(op="delete", id=tmp_t)
lsq = last(None, op="get", aspect="square")["last"]
assert lsq["template_missing"] and lsq["ops"][0]["id"] == TP.default_id(1080, 1080)
assert lsq["ops"][1]["style"]["font"]["family"] == "Bungee" and lsq["ops"][1]["style"]["style"]["fill"] == "#38BDF8"
assert lsq["ops"][1]["style"]["layout"]["y"] == 60
allx = last(None, op="get", all=True)["last"]
assert set(allx) == {"vertical", "horizontal", "square"}
assert last(None, op="clear", aspect="square")["cleared"] == 1 and last(None, op="get", aspect="square")["last"] is None

# ---------------------------------------------------------------- rename / duplicate / favourite / list order
rn = lib(op="rename", id=t1["id"], name="Gaya Toko Baru")["template"]
assert rn["id"] == t1["id"] and rn["name"] == "Gaya Toko Baru" and TP.load(t1["id"])["font"]["size"] == 96
assert lib(op="rename", id=r2["id"], name="Gaya Toko Baru")["template"]["name"] == "Gaya Toko Baru (2)"
dp = lib(op="duplicate", id=t1["id"])["template"]
assert dp["name"] == "Gaya Toko Baru (salinan)" and dp["id"] != t1["id"] and TP.load(dp["id"])["font"]["family"] == "Anton"
db = lib(op="duplicate", id="neon_gamer")["template"]                  # a built-in can be copied into Gaya Saya
assert db["source"] == "user" and db["name"].endswith("(salinan)")
time.sleep(1.1)                                                        # updated stamps have 1 s resolution
fv = lib(op="favorite", id=r2["id"], on=True)["template"]
assert fv["favorite"] is True
rows = lib(op="list")["templates"]
assert rows[0]["id"] == r2["id"] and rows[0]["favorite"], [r["name"] for r in rows]          # favourites first
assert all(r["thumb"] for r in rows) and all("updated_ms" in r for r in rows)
assert not lib(op="favorite", id=r2["id"], on=False)["template"]["favorite"]
try:
    lib(op="rename", id="tidak_ada", name="x")
    raise AssertionError("rename of a missing id must fail")
except EngineError as e:
    assert e.code == "BAD_TEMPLATE"

# ---------------------------------------------------------------- export (font notes) / import (validate, sanitise, de-dupe)
sysfam = next(iter(sorted(LB._system_fonts() - LB._bundled_fonts())), None)
ex_ids = [t1["id"], dp["id"]]
if sysfam:
    sy = lib(op="save", name="Pakai Font Windows", template={"font": {"family": sysfam}})["id"]
    ex_ids.append(sy)
ex = lib(op="export", ids=ex_ids, path=str(tmp / "out" / "gaya_saya"))
assert ex["path"].endswith("gaya_saya.json") and ex["n"] == len(ex_ids)
data = json.loads(open(ex["path"], encoding="utf-8").read())
assert data["kind"] == "autocut.caption_styles" and data["v"] == 1 and len(data["styles"]) == len(ex_ids)
assert data["app"] == "Klipora", data["app"]
assert {"family": "Anton", "source": "bundled", "bundled": True} in data["fonts"]
if sysfam:
    assert any(sysfam in w for w in ex["warnings"]), ex["warnings"]
n_before = len(lib(op="list")["templates"])
im = lib(op="import", path=ex["path"])
assert len(im["imported"]) == len(ex_ids) and not im["skipped"]
names = [r["name"] for r in im["imported"]]
assert names[0] == "Gaya Toko Baru (3)" and names[1] == "Gaya Toko Baru (salinan) (2)", names       # never overwrite
assert len(lib(op="list")["templates"]) == n_before + len(ex_ids)
assert TP.load(im["imported"][0]["id"])["font"]["size"] == 96
# hostile / broken files
bad = tmp / "bad.json"
bad.write_text(json.dumps({"kind": "autocut.caption_styles", "v": 1, "styles": [{"name": "Jahat\x07 {\\b1}", "template": {
    "font": {"family": "Evil,Font{\\fnX}", "size": "besar", "skew": 9, "uppercase": "ya"},
    "style": {"fill": "red", "outline": 1e9, "gradient": {"colors": ["#FF0000", "bukan", "#00FF00"], "dir": "vertical", "bands": 99},
              "extrude": {"depth": 10, "color": "#000000", "evil": {"a": {"b": {"c": {"d": {"e": {"f": {"g": 1}}}}}}}}},
    "box": {"enabled": True, "shape": "kotak_aneh", "color": "#000000B3", "radius": -50},
    "layout": {"max_lines": 99, "safe": "tiktok"}, "anim": {"in": "pop", "fx": {"in": "pop_kata"}},
    "evil_section": {"x": 1}, "base": "../../etc"}}]}), encoding="utf-8")
ib = lib(op="import", path=str(bad))
assert len(ib["imported"]) == 1 and any("tidak ada di PC ini" in w for w in ib["warnings"]), ib
tb_id = ib["imported"][0]["id"]
raw, _ = TP.raw(tb_id)
tb = TP.load(tb_id)
assert "evil_section" not in raw and "base" not in raw and "\x07" not in tb["name"]
assert tb["font"]["family"] == TP.DEFAULTS["font"]["family"] and tb["font"]["size"] == TP.DEFAULTS["font"]["size"]
assert tb["font"]["skew"] == 0.4 and tb["font"]["uppercase"] is False                  # clamped to the schema / dropped
assert tb["style"]["fill"] == "#FFFFFF" and tb["style"]["outline"] == 24
assert tb["style"]["gradient"]["colors"] == ["#FF0000", "#00FF00"] and tb["style"]["gradient"]["bands"] == 16
assert tb["box"]["shape"] == "rect" and tb["box"]["radius"] == 0 and tb["layout"]["max_lines"] == 5
assert tb["anim"]["fx"] == {"in": "pop_kata"}, "newer keys inside known sections pass through"
for bad_text, why in (("{nope", "bukan JSON"), (json.dumps({"kind": "autocut.caption_styles", "v": 99, "styles": []}), "versi"),
                      (json.dumps({"hello": 1}), "bukan file gaya")):
    bad.write_text(bad_text, encoding="utf-8")
    try:
        lib(op="import", path=str(bad))
        raise AssertionError("must reject: " + why)
    except EngineError as e:
        assert e.code == "BAD_FILE", (why, e.code)
bare = tmp / "bare.json"                                              # a plain template dict also imports
bare.write_text(json.dumps({"name": "Template Mentah", "font": {"family": "Bebas Neue"}, "box": {"enabled": True}}), encoding="utf-8")
assert lib(op="import", path=str(bare))["imported"][0]["name"] == "Template Mentah"

# the library rows are what the `templates` action lists too (same user dir)
tp = ACTIONS["templates"]({"params": {"options": False}, "seq": None}, em)
assert {r["id"] for r in tp["templates"] if r["source"] == "user"} == {r["id"] for r in lib(op="list", thumbs=False)["templates"]}

# ---------------------------------------------------------------- every built-in survives sanitize (export/import)
from ac.captions.layout import deep_merge
SCH = {f["path"]: f for f in TP.STYLE_SCHEMA}


def _walk(o, pre=""):
    for k, v in o.items():
        p = f"{pre}.{k}" if pre else k
        yield p, v
        if isinstance(v, dict):
            yield from _walk(v, p)


lost, out_of_range = [], []
for bid in TP.builtin_ids():
    eff = TP.load(bid)
    body = {k: v for k, v in eff.items() if isinstance(TP.DEFAULTS.get(k), dict)}
    clean, _ = LB.sanitize(body)
    back = deep_merge(TP.DEFAULTS, clean)
    for p, v in _walk(body):
        f = SCH.get(p)
        if f and f.get("type") == "number" and isinstance(v, (int, float)) and not isinstance(v, bool):
            if v < f.get("min", -1e9) or v > f.get("max", 1e9):
                out_of_range.append(f"{bid} {p}={v} [{f.get('min')}, {f.get('max')}]")
        if isinstance(v, dict):
            continue
        cur = back
        for part in p.split("."):
            cur = cur.get(part, "<missing>") if isinstance(cur, dict) else "<missing>"
        if cur != v and not (p == "font.family" and cur == TP.DEFAULTS["font"]["family"]):
            lost.append(f"{bid} {p}: {v!r} -> {cur!r}")
assert not out_of_range, out_of_range
assert not lost, lost[:10]
assert LB.sanitize({"highlight": {"color": None, "style": "none"}})[0]["highlight"]["color"] is None
# effect copy counts are clamped (each copy = one ASS event per word piece)
cs, _ = LB.sanitize({"style": {"extrude": {"depth": 40, "steps": 3000}, "long_shadow": {"length": 120, "steps": 1e12}}})
assert cs["style"]["extrude"]["steps"] == 32 and cs["style"]["long_shadow"]["steps"] == 24, cs["style"]
from ac.captions import compile as CC
fxs = {"long": {"length": 120, "steps": 1e12}, "extrude": {"depth": 40, "steps": float("nan")}}
assert len(CC.stack_copies(fxs, 1, 1, 2)) == 24 + 20, len(CC.stack_copies(fxs, 1, 1, 2))
assert len(CC.stack_copies({"long": None, "extrude": {"depth": 40, "steps": 3000}}, 1, 1, 2)) == 32

# page cache (act_render): band pass + chunk texts reuse each compiled page, output byte-identical
from ac.captions import layout as LL
from ac.captions import preview as PV
for tid in ("flash_sale", "kuning_3d", "bold_pop"):
    tp_ = LL.prep_template(dict(TP.load(tid, 1080, 1920)))
    prs = [(c, tp_) for c in LL.paginate(PV._demo_words(tp_), tp_, 1080, 1920)]
    band0 = CC.caption_band(prs, 1080, 1920)
    ref = CC.ass_text(prs, 1080, 1920, band0)
    with CC.page_cache():
        seen = []
        band1 = CC.caption_band(prs, 1080, 1920, on_page=lambda i, n: seen.append((i, n)))
        assert band1 == band0 and seen[-1] == (len(prs), len(prs)), (tid, band0, band1)
        assert CC.ass_text(prs, 1080, 1920, band1) == ref, tid
        assert CC.ass_text(prs[1:], 1080, 1920, None) == CC.ass_text(prs[1:], 1080, 1920, None), tid
    assert CC.ass_text(prs[1:], 1080, 1920) == CC.ass_text(prs[1:], 1080, 1920), tid

# auto-fit: one very long word stays inside the 9:16 safe zone with its active pill + pop (bold_pop)
tp_ = LL.prep_template(dict(TP.load("bold_pop", 1080, 1920)))
lw_ = LL.RWord(text=LL.transform_text("Mempertanggungjawabkannya", tp_), start=0.0, end=2.0, id="x:0",
               raw="Mempertanggungjawabkannya", sentence_end=True, seg_end=1)
cap_ = LL.Caption([LL.Line([lw_])], 0.0, 2.0, id="p", index=0)
_, bb_, fit_ = LL.layout(cap_, tp_, 1080, 1920)
bx_, _, bw_, _ = CC.caption_band([(cap_, tp_)], 1080, 1920)
assert fit_ < 1 and abs((bb_[0] + bb_[2]) / 2 - 540) < 2 and bx_ > 0 and bx_ + bw_ < 1080, (fit_, bb_, bx_, bw_)

# ---------------------------------------------------------------- Klipora file kind + English (job language "en")
from ac import i18n  # noqa: E402
kl = tmp / "klipora_kind.json"
kl.write_text(json.dumps({"kind": "klipora.caption_styles", "v": 1, "app": "Klipora",
                          "styles": [{"name": "Gaya Klipora", "template": {"font": {"family": "Anton"}}}]}), encoding="utf-8")
assert lib(op="import", path=str(kl))["imported"][0]["name"] == "Gaya Klipora"      # new kind id accepted too
with i18n.using("en"):
    old = tmp / "old_kind.json"                       # a file exported before the rename still imports
    old.write_text(json.dumps({"kind": "autocut.caption_styles", "v": 1, "app": "AutoCut BOT v2",
                               "styles": [{"name": "Gaya Lama", "template": {"style": {"fill": "#22E58B"}}}]}), encoding="utf-8")
    io_ = lib(op="import", path=str(old))
    assert io_["imported"][0]["name"] == "Gaya Lama" and io_["summary"] == "1 style imported", io_["summary"]
    bad.write_text(json.dumps({"kind": "autocut.caption_styles", "v": 99, "styles": []}), encoding="utf-8")
    try:
        lib(op="import", path=str(bad))
        raise AssertionError("newer file must fail")
    except EngineError as e:
        assert "newer version of Klipora" in e.msg and "Klipora" in (e.hint or ""), (e.msg, e.hint)
    en_rows = lib(op="list", thumbs=False)["templates"]
    assert any(r["name"] == "Gaya Toko Baru" for r in en_rows)            # user names stay as typed
    assert {r["aspect_label"] for r in en_rows} <= {"Vertical 9:16", "Horizontal 16:9", "Square 1:1", "All ratios"}
    dup = lib(op="duplicate", id="judul_emas")["template"]
    assert dup["name"] == "Gold Title (copy)", dup["name"]
    assert lib(op="delete", id=dup["id"])["summary"] == "Style deleted"

# ---------------------------------------------------------------- delete
assert lib(op="delete", id=dp["id"])["deleted"] and not lib(op="delete", id=dp["id"])["deleted"]
assert dp["id"] not in json.loads((appdata_dir() / "caption_library.json").read_text(encoding="utf-8"))["items"]

_common.cleanup("captions_library")
print(f"OK test_captions_library ({time.perf_counter() - T0:.1f} s)")
