"""ac.captions: text rules, templates, captions doc + edit ops + carry-over, compile, preview, autoposition,
chunked straight-alpha render (pixel check), SRT, MOGRT, cleanup review, user templates, burn loop.
Offline: cached transcripts of the 49 s test file, AI disabled. ~20 s."""
import json
import os
import subprocess
import time
import zipfile

os.environ["AI_DISABLED"] = "1"          # cleanup must use its rule fallback here (never spend Grok quota)

import _common
from _common import MEDIA_49, MEDIA_GLOSSARY

import numpy as np

from ac.captions import ACTIONS
from ac.captions import compile as C
from ac.captions import glossary as G
from ac.captions import layout as L
from ac.captions import model as M
from ac.captions import mogrt as MG
from ac.captions import render as R
from ac.captions import srt as S
from ac.captions import templates as TP
from ac.progress import ListEmitter
from ac.util import EngineError, ffmpeg_exe, ffprobe

T0 = time.perf_counter()
timing = {}


def lap(name, t):
    timing[name] = round(time.perf_counter() - t, 2)


# ---------------------------------------------------------------- text rules
rows = [{"text": t} for t in "ini dia kita buka dulu toko kitah lalu ke dashboard dan cloudflare, seller".split()]
fx = {f["old"]: f["text"] for f in G.glossary_fixes(rows, ["TokoKita", "Cloudflare", "dashboard", "reseller"])}
assert fx == {"toko kitah": "TokoKita", "cloudflare,": "Cloudflare,"}, fx
assert G.phon("Djoeragan") == G.phon("juragan")                                    # old spelling sound-alike     # "ke dashboard" / "seller" untouched
nums = {f["old"]: f["text"] for f in G.number_fixes([{"text": t} for t in "harga 50 ribu, ongkir Rp15.000 modal 1,5 juta".split()])}
assert nums == {"50 ribu,": "50.000,", "Rp15.000": "Rp 15.000", "1,5 juta": "1.500.000"}, nums
assert not G.break_ok("di") and not G.break_ok("yang,") and G.break_ok("jualan")
assert G.mask("anjing,") == "a****g," and G.mask("anjing", "first") == "a*****" and G.is_filler("Eee,")

# ---------------------------------------------------------------- templates
tpls = {t["id"]: t for t in TP.list_all()}
need = ["tutorial_bersih", "hormozi_kuning", "karaoke", "mr_beast", "minimal_putih", "boxed", "neon", "podcast",
        "typewriter", "subtitle_klasik", "bold_pop", "hijau_viral", "komik", "sinematik", "garis_bawah", "ali_abdaal"]
assert all(k in tpls for k in need) and len(tpls) >= 16, sorted(tpls)
for t in tpls.values():
    assert L.get_font(t["font"]["family"]).family == t["font"]["family"], t["font"]["family"]   # bundled font
    assert C.anim_name(t["anim"]["in"], C.IN, C.ALIASES_IN) == t["anim"]["in"], t["id"]
assert TP.default_id(2292, 960) == "tutorial_bersih" and TP.default_id(1080, 1920) == "hormozi_kuning"
em = ListEmitter()
res = ACTIONS["templates"]({"params": {}, "seq": None}, em)
assert len(res["templates"]) >= 16 and res["options"]["highlight_presets"] and res["options"]["fonts"]
assert {"fade", "pop", "slide_up", "slide_down", "typewriter", "bounce", "blur", "zoom"} <= {a["id"] for a in res["options"]["anim_in"]}

if not _common.have(MEDIA_49):
    raise SystemExit(0)

# ---------------------------------------------------------------- doc on a cut sequence (3 clips, like an AutoCut clone)
tmp = _common.scratch("captions")
keeps = [(0.0, 7.0), (8.4, 13.0), (15.0, 48.9)]
clips, t = [], 0.0
for a, b in keeps:
    clips.append({"name": MEDIA_49.name, "path": str(MEDIA_49), "start": round(t, 3), "end": round(t + b - a, 3),
                  "in": a, "out": b})
    t += b - a
seq = {"id": "cut1", "name": "AC Caption Test (AutoCut)", "fps": 120, "width": 2292, "height": 960,
       "duration": round(t, 3), "player": 10.0, "video": [{"index": 0, "name": "V1", "clips": clips}],
       "audio": [{"index": 0, "name": "A1", "clips": [dict(c) for c in clips]}]}
job = {"tool": "captions", "seq": seq, "workdir": str(tmp)}
GLOSS = MEDIA_GLOSSARY[:1] + ["Cloudflare"]     # brand spoken in the clip (media.json) + a common term
t1 = time.perf_counter()
res = ACTIONS["doc"](dict(job, action="doc", params={"glossary": GLOSS, "inline": True}), em)
lap("doc", t1)
assert "error" not in em.kinds() and res["pages"] > 5 and res["template"] == "tutorial_bersih", res
assert res["summary"].endswith("koreksi glosarium") and res["stats"]["pages"] == res["pages"], res["summary"]
doc = M.load(res["doc"])
ids = [w["id"] for w in doc["words"]]
assert len(ids) == len(set(ids)) and all(":" in i for i in ids)
assert doc["seq"]["w"] == 2292 and doc["seq"]["clipsHash"] and doc["info"]["fixes"] >= len(GLOSS)
if MEDIA_GLOSSARY:   # the misheard brand is merged into one glossary word (span merge)
    wk = [w for w in doc["words"] if w.get("auto", {}).get("kind") == "glossary" and w["text"].startswith(GLOSS[0])]
    assert wk and any(w.get("merged") == wk[0]["id"] for w in doc["words"]), "misheard brand -> glossary term (span merge)"
else:
    print("skip: real-transcript brand merge (no glossary in KLIPORA_TEST_MEDIA/media.json)")
assert all(0 <= w["t0"] < w["t1"] <= seq["duration"] + 0.01 for w in doc["words"])
seen = [x for p in doc["pages"] for ln in p["lines"] for x in ln]
vis = [w["id"] for w in doc["words"] if not w.get("hide") and not w.get("merged")]
assert seen == vis, "every visible word is on exactly one page, in order"
assert all(a["t1"] <= b["t0"] + 1e-6 for a, b in zip(doc["pages"], doc["pages"][1:])), "pages never overlap"
assert res["data"]["words"][0]["id"] == ids[0] and "page_over" in res["data"]

# ---------------------------------------------------------------- edit ops (through the doc action without seq)
p0, p1 = doc["pages"][0], doc["pages"][1]
w_a, w_b = p1["lines"][0][0], p1["lines"][0][1]
nojob = dict(job, seq=None, action="doc")
ops = [{"op": "text", "ids": [w_a], "text": "UNTUK"},                                   # same count: own timing
       {"op": "style", "ids": [w_b], "style": {"color": "#FF4040", "scale": 1.2, "emoji": "\U0001F525"}},
       {"op": "page_pos", "page": p0["id"], "y": 30},
       {"op": "break", "ids": [w_b], "kind": "page"},
       {"op": "censor", "ids": [p0["lines"][0][0]], "on": True},
       {"op": "replace", "find": "teman-teman", "replace": "kawan-kawan"}]
res2 = ACTIONS["doc"](dict(nojob, params={"ops": ops}), em)
doc = M.load(res2["doc"])
by = {w["id"]: w for w in doc["words"]}
assert by[w_a]["text"] == "UNTUK" and by[w_a]["edit"]["src"] == "user"
assert by[w_b]["style"]["color"] == "#FF4040"
assert any(p["lines"][0][0] == w_b for p in doc["pages"]), "manual page break before w_b"
assert doc["pages"][0]["y"] == 30 and doc["pages"][0]["pos"] == "manual"
assert by[p0["lines"][0][0]]["censor"] and res2["notes"][0]["n"] >= 1
assert any(w["text"].startswith("kawan-kawan") for w in doc["words"])
assert M.apply_ops(json.loads(json.dumps(doc)), [{"op": "auto_emoji", "density": "banyak"}])[0]["n"] >= 1
pairs, W, H = M.page_pairs(doc)
first = pairs[0][0].words[0]
assert "*" in first.text, first.text                                                    # censored on screen
assert any(rw.style.get("emoji") for c, _ in pairs for rw in c.words)
# span edit with a different word count -> merged into the first word, timing = whole span
span = doc["pages"][3]["lines"][0][:2]
ACTIONS["doc"](dict(nojob, params={"ops": [{"op": "text", "ids": span, "text": "satu"}]}), em)
doc = M.load(res2["doc"])
by = {w["id"]: w for w in doc["words"]}
assert by[span[0]]["text"] == "satu" and by[span[1]].get("merged") == span[0]
# line break + join + template switch + reset
lb = doc["pages"][4]["lines"][0][2] if len(doc["pages"][4]["lines"][0]) > 2 else doc["pages"][4]["lines"][0][-1]
ACTIONS["doc"](dict(nojob, params={"ops": [{"op": "break", "ids": [lb], "kind": "line"}]}), em)
doc = M.load(res2["doc"])
pg = next(p for p in doc["pages"] if lb in [x for ln in p["lines"] for x in ln])
assert any(ln[0] == lb for ln in pg["lines"]), "forced line break"
try:
    M.apply_ops(doc, [{"op": "text", "ids": ["nope:1"], "text": "x"}])
    raise AssertionError("BAD_OP expected")
except EngineError as e:
    assert e.code == "BAD_OP"

# ---------------------------------------------------------------- re-run keeps the user layer (carry-over by id)
res3 = ACTIONS["doc"](dict(job, action="doc", params={}), em)
doc = M.load(res3["doc"])
by = {w["id"]: w for w in doc["words"]}
assert by[w_a]["text"] == "UNTUK" and by[w_b]["style"]["color"] == "#FF4040" and doc["pages"][0]["y"] == 30
assert doc["params"]["glossary"] == GLOSS
chk = ACTIONS["doc"](dict(job, action="doc", params={"check_only": True}), em)
assert chk["exists"] and chk["stale"] is False
seq2 = json.loads(json.dumps(seq))
seq2["audio"][0]["clips"][2]["out"] = 40.0
seq2["audio"][0]["clips"][2]["end"] = round(seq2["audio"][0]["clips"][2]["start"] + 25.0, 3)
assert ACTIONS["doc"](dict(job, seq=seq2, action="doc", params={"check_only": True}), em)["stale"] is True

# ---------------------------------------------------------------- compile every template on this doc
t1 = time.perf_counter()
for tid in tpls:
    d2 = json.loads(json.dumps(doc))
    M.apply_ops(d2, [{"op": "template", "id": tid}])
    M.repaginate(d2)
    pr, W, H = M.page_pairs(d2)
    text, n = C.ass_text(pr, W, H)
    assert n > 0 and "PlayResX: 2292" in text and "Kerning: yes" in text, tid
    bx, by_, bw, bh = C.caption_band(pr, W, H)
    assert 0 <= bx and 0 <= by_ and bx + bw <= W and by_ + bh <= H and bw > 0 and bh > 0, (tid, (bx, by_, bw, bh))
lap("compile_all_templates", t1)

# ---------------------------------------------------------------- preview (frame + transparent overlay)
from PIL import Image  # noqa: E402

t1 = time.perf_counter()
tmid = (doc["pages"][2]["t0"] + doc["pages"][2]["t1"]) / 2                       # page fully faded in
pv = ACTIONS["preview"](dict(job, action="preview", params={"t": tmid, "mode": "both"}), em)
lap("preview_cold", t1)
im = Image.open(pv["png"])
assert im.size == (1146, 480), im.size
ov = np.asarray(Image.open(pv["overlay"]))
assert ov.shape[2] == 4 and ov[0, 0, 3] == 0 and (ov[..., 3] > 200).sum() > 500, "overlay PNG has straight alpha"
t1 = time.perf_counter()
pv2 = ACTIONS["preview"](dict(job, action="preview", params={"t": tmid}), em)
lap("preview_cached", t1)
assert pv2["frame_cached"] is True and pv2["png"] != pv["png"]

# ---------------------------------------------------------------- autoposition (faces / UI / action, safe zone)
t1 = time.perf_counter()
ap = ACTIONS["autoposition"](dict(job, action="autoposition", params={}), em)
lap("autoposition", t1)
doc = M.load(res3["doc"])
assert ap["pages"] == len(doc["pages"]) and ap["safe"] == "youtube"
sx0, sy0, sx1, sy1 = L.safe_rect("youtube", 2292, 960)
assert all(sy0 / 960 * 100 - 1e-6 <= p["y"] <= sy1 / 960 * 100 for p in doc["pages"])
assert doc["pages"][0]["y"] == 30 and doc["pages"][0]["pos"] == "manual", "manual position kept"

# ---------------------------------------------------------------- render (chunked, straight alpha, versioned)
t1 = time.perf_counter()
rr = ACTIONS["render"](dict(job, action="render", params={"until": 6.0, "chunk": 2.0}), em)
lap("render_6s", t1)
pl = rr["plan"]
assert pl["kind"] == "overlay" and pl["track"] == "Klipora Captions" and pl["alpha"] == "straight" and rr["version"] == 1
assert rr["chunks"] >= 2 and rr["path"].endswith("_captions_v1.mov"), rr
st = ffprobe(rr["path"])["streams"][0]
bx, by_, bw, bh = pl["band"]
assert st["codec_name"] == "qtrle" and st["width"] == bw and st["height"] == bh, st
assert abs(int(st["nb_frames"]) - 180) <= 1, st["nb_frames"]
assert abs(pl["xNorm"] - (bx + bw / 2) / 2292) < 1e-4 and abs(pl["yNorm"] - (by_ + bh / 2) / 960) < 1e-4


def grab(args, w, h, cwd=None):
    r = subprocess.run([ffmpeg_exe(), "-v", "error", *args, "-f", "rawvideo", "-pix_fmt", "rgba", "-"],
                       capture_output=True, cwd=cwd)
    return np.frombuffer(r.stdout, np.uint8).reshape(h, w, 4).astype(float)


doc = M.load(res3["doc"])
pr, W, H = M.page_pairs(doc)
tt = next(p["t0"] + 0.4 for p in doc["pages"] if p["t0"] + 0.4 < 5.5)
ovf = grab(["-ss", f"{tt:.3f}", "-i", rr["path"], "-frames:v", "1"], bw, bh)
truth_ass = tmp / "truth.ass"
C.write_ass(truth_ass, pr, W, H)
assert tt < 7.0
src_t = tt                                                           # first clip = source [0, 7] at sequence 0
frame = grab(["-ss", f"{src_t:.3f}", "-i", str(MEDIA_49), "-frames:v", "1"], W, H)
truth = grab(["-ss", f"{src_t:.3f}", "-i", str(MEDIA_49), "-frames:v", "1", "-vf",
              f"format=rgb24,setpts={tt:.3f}/TB,ass=truth.ass:fontsdir={R.fonts_rel(tmp)}"], W, H, cwd=tmp)
comp = frame[..., :3].copy()
a = ovf[..., 3:4] / 255
region = comp[by_:by_ + bh, bx:bx + bw]
comp[by_:by_ + bh, bx:bx + bw] = ovf[..., :3] * a + region * (1 - a)
err = np.abs(comp - truth[..., :3])
assert (ovf[..., 3] > 0).sum() > 300 and err.max() <= 4.0, ("overlay composite == libass burn", err.max())
rr2 = ACTIONS["render"](dict(job, action="render", params={"until": 6.0, "chunk": 2.0}), em)
assert rr2.get("skipped"), "unchanged doc -> no re-render"
ACTIONS["doc"](dict(nojob, params={"ops": [{"op": "style", "ids": [w_a], "style": {"color": "#00FF00"}}]}), em)
rr3 = ACTIONS["render"](dict(job, action="render", params={"until": 6.0, "chunk": 2.0}), em)
assert rr3["version"] == 2 and rr3["plan"]["relink"] and rr3["reused_chunks"] >= 1, rr3

# ---------------------------------------------------------------- srt / vtt
sr = ACTIONS["srt"](dict(job, action="srt", params={}), em)
doc = M.load(res3["doc"])
txt = open(sr["path"], encoding="utf-8").read()
assert sr["cues"] == len(doc["pages"]) and txt.startswith("1\n00:00:") and " --> " in txt
assert sr["plan"]["kind"] == "caption_track"
vtt, n = S.vtt_text(doc, "line")
assert vtt.startswith("WEBVTT") and n >= len(doc["pages"])

# ---------------------------------------------------------------- mogrt (optional native, behind a flag)
try:
    ACTIONS["mogrt"](dict(job, action="mogrt", params={}), em)
    raise AssertionError("MOGRT_OFF expected")
except EngineError as e:
    assert e.code == "MOGRT_OFF" and e.msg == "Output MOGRT (editable di Premiere) belum diaktifkan.", e.msg
assert MG.TRACK == "Klipora Captions (Editable)"

# ---------------------------------------------------------------- English job language (UI texts per job)
from ac import i18n  # noqa: E402
from ac.captions import preview as PV  # noqa: E402

with i18n.using("en"):
    try:
        ACTIONS["mogrt"](dict(job, action="mogrt", params={}), em)
        raise AssertionError("MOGRT_OFF expected")
    except EngineError as e:
        assert e.msg == "MOGRT output (editable in Premiere) is not turned on." and "0.26 s" in e.hint, (e.msg, e.hint)
    em_en = ListEmitter()
    r_en = ACTIONS["doc"](dict(nojob, params={"ops": []}), em_en)
    assert any(x["ev"] == "stage" and x["label"] == "Update captions" for x in em_en.items), em_en.items
    assert " captions, " in r_en["summary"] and " words" in r_en["summary"] and "glossary fix" in r_en["summary"], r_en["summary"]
    assert L.SAFE_LABELS["vertical_universal"] == "All vertical" and C.ANIM_LABELS.get("fade") == "Fade"
    assert dict(L.FONT_CATEGORIES.items())["tebal"] == "Bold"
assert L.SAFE_LABELS["vertical_universal"] == "Semua vertikal" and C.ANIM_LABELS.get("fade") == "Pudar"
# caption tracks are skipped as picture sources: new name and the old AutoCut name
assert all(f"{n} (Editable)".startswith(PV.CAPTION_TRACK_PREFIXES) for n in ("Klipora Captions", "AutoCut Captions"))
if MG.base_mogrt():
    mg = ACTIONS["mogrt"](dict(job, action="mogrt", params={"enable": True}), em)
    it = mg["plan"]["items"]
    assert mg["plan"]["kind"] == "mogrt" and len(it) == len(doc["pages"])
    z = zipfile.ZipFile(it[0]["path"])
    assert json.loads(z.read("definition.json"))["clientControls"] == []
    try:
        ACTIONS["mogrt"](dict(job, action="mogrt", params={"enable": True, "states": True, "max_clips": 5}), em)
        raise AssertionError("TOO_MANY_CLIPS expected")
    except EngineError as e:
        assert e.code == "TOO_MANY_CLIPS"

# ---------------------------------------------------------------- cleanup: suggest (rule fallback) + apply review
cl = ACTIONS["cleanup"](dict(job, action="cleanup", params={}), em)
assert cl["source"] in ("fallback", "none") and os.path.isfile(cl["review"])
from ac import review as RV  # noqa: E402

doc = M.load(res3["doc"])
tw = doc["pages"][5]["lines"][0][0]
rv = RV.new("captions_cleanup", [RV.item(1, 2, "word_fix", on=True, ids=[tw], old="x", new="Koreksi"),
                                 RV.item(2, 3, "word_fix", on=False, ids=[tw], old="x", new="Tidak")], 48.9)
rvp = RV.save(rv, tmp / "cl_review.json")
ap2 = ACTIONS["cleanup"](dict(job, action="cleanup", params={"mode": "apply"}, review=str(rvp)), em)
assert ap2["applied"] == 1 and {w["id"]: w for w in M.load(res3["doc"])["words"]}[tw]["text"] == "Koreksi"

# ---------------------------------------------------------------- profanity: tool API, review-file fallback
doc = M.load(res3["doc"])
src = doc["info"]["profanity_source"]
assert src in ("profanity", "builtin") and M.censor_mode(doc) in ("stars", "full", "bleep", "off")
pw = next(w for w in doc["words"] if not w.get("hide") and not w.get("merged") and w["t0"] > 20)
RV.save(RV.new("profanity", [RV.item(pw["t0"], pw["t1"], "kasar", on=True)], 48.9), tmp / "profanity_review.json")
idx, why, _ = M.profanity_flags(doc["words"], job)              # no Timeline -> the tool's review file
assert why == "review" and [doc["words"][i]["id"] for i in idx] == [pw["id"]]
d4 = json.loads(json.dumps(doc))
M.apply_ops(d4, [{"op": "params", "censor": "full"}, {"op": "censor", "ids": [pw["id"]], "on": True}])
M.repaginate(d4)
shown = [rw.text for c, _ in M.page_pairs(d4)[0] for rw in c.words if rw.id == pw["id"]]
assert shown and set(shown[0].strip(",.?!")) == {"*"}, shown

# ---------------------------------------------------------------- user template save / list / delete
sv = ACTIONS["save_template"](dict(job, action="save_template", params={"from_doc": True, "name": "Tes Caption Agen"}), em)
assert sv["template"]["source"] == "user" and sv["template"]["thumb"]
lst = ACTIONS["templates"]({"params": {"options": False}, "seq": seq}, em)
assert any(r["id"] == sv["id"] for r in lst["templates"])
assert ACTIONS["save_template"](dict(job, params={"delete": sv["id"]}), em)["deleted"]

# ---------------------------------------------------------------- gallery + burn loop
gl = ACTIONS["gallery"](dict(job, action="gallery", params={"ids": ["hormozi_kuning", "boxed"]}), em)
assert all(Image.open(p).size == (360, 180) for p in gl["thumbs"].values()) and not gl["errors"]
t1 = time.perf_counter()
bn = ACTIONS["burn"](dict(job, action="burn", params={"t0": 5.0, "t1": 8.0}), em)
lap("burn_3s", t1)
bs = ffprobe(bn["path"])
assert bs["streams"][0]["width"] == 1146 and abs(float(bs["format"]["duration"]) - 3.0) < 0.2 and bn["segments"] == 2

kinds = em.kinds()
assert "error" not in kinds and "stage" in kinds and "progress" in kinds
_common.cleanup("captions")
print("timing", timing, "total", round(time.perf_counter() - T0, 1), "s")
print("ok")
