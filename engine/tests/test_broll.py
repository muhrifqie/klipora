"""ac.tools.broll: matching, activity, placement, AI plan (mocked, one call + cache), Pexels (mocked, key never
leaks), Grok Imagine (mocked), analyze -> review, fetch, apply -> pre-rendered clips + plan, CLI round trip.

Offline by default (no Grok quota, no network). `--live` adds ONE real planning call on the 2 min transcript.
"""
import base64
import io
import json
import subprocess
import sys
from pathlib import Path

import _common
from _common import MEDIA_2M, MEDIA_49

from ac import media as M, review as RV
from ac.ai import client as ai
from ac.progress import ListEmitter
from ac.timeline import Timeline
from ac.tools import broll as B
from ac.util import EngineError, run_ffmpeg

if not _common.have(MEDIA_49):
    print("ok (skipped)")
    sys.exit(0)

tmp = _common.scratch("broll")
CACHE = tmp / "cache"
B.cache_dir = lambda *sub: (CACHE.joinpath(*sub), CACHE.joinpath(*sub).mkdir(parents=True, exist_ok=True))[0]
B._ai_cache = lambda: ai.Cache(tmp / "ai_cache")
FAKE_KEY = "FAKEpexelsKEY0123456789"


def lavfi(out, src, dur, extra=()):
    run_ffmpeg(["-f", "lavfi", "-i", f"{src}:d={dur}", *extra, "-c:v", "libx264", "-pix_fmt", "yuv420p", "-y", str(out)])
    return out


def jpeg_bytes(color=(200, 120, 40), size=(1280, 720)):
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "JPEG", quality=85)
    return buf.getvalue()


# ---------------------------------------------------------------- pure helpers
assert B.file_tokens("Bisnis\\ResellerPacking paket_02.mp4") == ["bisnis", "reseller", "packing", "paket"]
assert B._tok_match("jualan", "jual") >= 0.75 and B._tok_match("keuangan", "uang") > 0 and B._tok_match("kopi", "laptop") == 0
assert B.render_fps(120) == 30 and B.render_fps(59.94) == 29.97 and B.render_fps(25) == 25 and B.render_fps(50) == 25
assert B.aspect_name(2292, 960) == "16:9" and B.aspect_name(1080, 1920) == "9:16" and B.aspect_name(1000, 1000) == "1:1"
x, y = B.pip_geometry("tr", 2292, 960)
assert abs(x - (1 - 0.025 - 0.19)) < 1e-3 and abs(y - (0.025 * 2292 / 960 + 0.19)) < 1e-3
assert B.pip_geometry("bl", 1920, 1080)[0] < 0.5 < B.pip_geometry("bl", 1920, 1080)[1]
busy_tr = [[0.0] * 12 for _ in range(6)]
busy_tr[0][11] = busy_tr[1][10] = 0.5                       # action top right -> PiP goes top left
assert B.choose_corner(busy_tr) == "tl" and B.choose_corner(None) == "tr"
assert B.placement_for("auto", {"busy": 0.05})[:2] == ("full", "full")
assert B.placement_for("auto", {"busy": 0.3})[:2] == ("pip", "pip")
assert B.placement_for("auto", {"busy": 0.7})[1] == "skip" and B.placement_for("full", {"busy": 0.7})[0] == "full"
assert B.placement_for("auto", None)[0] == "pip" and B.placement_for("auto", {"busy": 0, "none": True})[0] == "full"
assert B.looks_indonesian("uang tunai") and not B.looks_indonesian("people selling coffee")
p = B.params_of({"density": 99, "max_dur": 0.2, "placement": "x", "transition": "crossfade", "folder": "", "ai_max": 20})
assert p["density"] == 8 and p["max_dur"] == B.MIN_DUR and p["placement"] == "auto" and p["transition"] == "fade"
assert p["src_local"] is False and p["ai_max"] == 8
c = [{"t0": 0.0, "t1": 3.0, "score": 90, "auto": "full", "act": {"busy": 0}},
     {"t0": 2.0, "t1": 5.0, "score": 80, "auto": "full", "act": {"busy": 0}},      # overlaps the first
     {"t0": 20.0, "t1": 23.0, "score": 70, "auto": "skip", "act": {"busy": 0.8}},  # busy: shown unchecked
     {"t0": 40.0, "t1": 43.0, "score": 30, "auto": "pip", "act": {"busy": 0.3}}]
chosen, skipped = B.select_slots(c, 3, 10, "auto")
assert [s["t0"] for s in chosen] == [0.0, 40.0] and [s["t0"] for s in skipped] == [20.0]
chosen, skipped = B.select_slots(c, 3, 10, "full")
assert [s["t0"] for s in chosen] == [0.0, 20.0, 40.0] and not skipped

# ---------------------------------------------------------------- local library
lib_dir = tmp / "lib"
(lib_dir / "Bisnis").mkdir(parents=True)
lavfi(lib_dir / "uang tunai.mp4", "testsrc2=s=640x360:r=30", 3)
lavfi(lib_dir / "Bisnis" / "ResellerPacking paket.mp4", "testsrc=s=1280x720:r=25", 6)
lavfi(lib_dir / "random_123.mp4", "testsrc2=s=320x240:r=30", 2)
(lib_dir / "email notifikasi.png").write_bytes(jpeg_bytes((30, 60, 220), (800, 600)))   # JPEG bytes, .png name is fine
(lib_dir / "notes.txt").write_text("not media")
for own in ("Klipora", "AutoCut BOT"):                      # our own work folders (new + old name) are skipped
    (lib_dir / own).mkdir()
    (lib_dir / own / "skip_me.png").write_bytes(jpeg_bytes())
lib = B.scan_library(lib_dir)
assert sorted(f["name"] for f in lib) == ["ResellerPacking paket.mp4", "email notifikasi.png", "random_123.mp4",
                                          "uang tunai.mp4"], [f["name"] for f in lib]
m = B.match_library([("reseller", 3.0), ("daftar", 1.0)], lib)
assert m and m[0][1]["name"] == "ResellerPacking paket.mp4" and m[0][0] >= B.LOCAL_MIN
assert B.match_library([("keuangan", 3.0)], lib)[0][1]["name"] == "uang tunai.mp4"
assert not B.match_library([("kopi", 3.0)], lib)
try:
    B.scan_library(tmp / "nope")
    raise AssertionError("missing folder must raise")
except EngineError as e:
    assert e.code == "NO_FOLDER"

# English job language: user-facing engine text follows the job's "lang" (panel UI language)
from ac import i18n  # noqa: E402
with i18n.using("en"):
    assert B.placement_for("auto", None)[2]["text"] == "Couldn't read the screen activity, placed as PiP."
    try:
        B.scan_library(tmp / "nope")
    except EngineError as e:
        assert e.msg.startswith("B-roll folder not found: ") and "Local folder" in e.hint, (e.msg, e.hint)
assert B.placement_for("auto", None)[2]["text"].startswith("Aktivitas layar")   # back to Indonesian
# our own b-roll track (new and old name) is not part of the program when measuring screen activity
from types import SimpleNamespace as _NS  # noqa: E402
_own = _NS(video=[_NS(index=0, name="Video 1"), _NS(index=1, name="Klipora B-Roll"), _NS(index=2, name="AutoCut B-Roll")])
assert B._main_tracks(_own) == [0]

# ---------------------------------------------------------------- screen activity
still = lavfi(tmp / "still.mp4", "color=c=gray:s=640x360:r=30", 3)
moving = lavfi(tmp / "moving.mp4", "testsrc2=s=640x360:r=30", 3)
a_still, a_move = B.screen_activity(still, 0, 3), B.screen_activity(moving, 0, 3)
assert a_still["busy"] == 0.0 and a_move["busy"] > 0.5 and a_move["n"] >= 20, (a_still, a_move)
assert len(a_move["grid"]) == 6 and len(a_move["grid"][0]) == 12
quiet, busy = B.screen_activity(MEDIA_49, 0, 4), B.screen_activity(MEDIA_49, 24, 28)
assert quiet["busy"] < B.FULL_MAX and busy["busy"] > B.FULL_MAX, (quiet["busy"], busy["busy"])
tl = Timeline.from_media(MEDIA_49)
act = B.slot_activity(tl, 24.0, 28.0)
assert abs(act["busy"] - busy["busy"]) < 0.1 and act["grid"]
assert B.slot_activity(tl, 60.0, 62.0).get("none")          # nothing plays there: fullscreen is safe

# ---------------------------------------------------------------- render (exact size/length, no audio)
img = lib_dir / "email notifikasi.png"
r1 = B.render_clip(img, "image", tmp / "r_img.mp4", 2.5, 2292, 960, 30.0, pip=True, motion=1)
r2 = B.render_clip(lib_dir / "uang tunai.mp4", "video", tmp / "r_vid.mp4", 4.0, 2292, 960, 30.0,
                   src_dur=M.probe(lib_dir / "uang tunai.mp4")["duration"])          # 3 s source, loops to 4 s
for r, dur in ((r1, 2.5), (r2, 4.0)):
    info = M.probe(r)
    assert (info["width"], info["height"]) == (2292, 960) and not info["has_audio"], info
    assert dur <= info["duration"] <= dur + 0.12, (r, info["duration"])
frame = M.frame_grab(r1, 1.0)
assert frame[2, 2].min() > 240 and frame[480, 1146, 2] > 150      # white PiP border, blue content inside

# ---------------------------------------------------------------- AI plan (mocked): one call, cached, validated
tl2 = Timeline.from_media(MEDIA_2M) if _common.have(MEDIA_2M) else tl
from ac.ai.text import display_words, sentences  # noqa: E402
sents = sentences(display_words(tl2.words_on_timeline(transcribe=False)))
calls = []


def fake_chat_json(msgs, schema=None, check=None, **kw):
    calls.append(kw)
    assert "TRANSCRIPT:" in msgs[1]["content"] and "LIBRARY:" in msgs[1]["content"]
    s3, s5 = sents[3], sents[5]
    from ac.ai.text import ts
    return {"picks": [
        {"t": ts(s3["start"]), "w": " ".join(s3["text"].split()[:3]), "kw": s3["text"].split()[-1], "q": "Shopee seller packing box",
         "alt": "online shop owner", "img": "a seller packing boxes", "lib": 2, "score": 88},
        {"t": ts(s5["start"]), "w": " ".join(s5["text"].split()[:2]), "kw": "", "q": "laptop typing", "score": "70"},
        {"t": "99:59", "w": "tidak ada", "q": "x", "score": 50},                     # unresolvable -> dropped
        {"t": ts(s5["start"]), "w": " ".join(s5["text"].split()[:2]), "q": "dup", "score": 10}]}   # same line -> dropped


real_chat, real_avail = ai.chat_json, ai.available
ai.chat_json, ai.available = fake_chat_json, (lambda *a, **k: True)
try:
    r = B.plan_ai(sents, 4, lib, emit=ListEmitter())
    assert r["source"] == "ai" and len(r["picks"]) == 2 and len(calls) == 1, (r, calls)
    assert calls[0].get("repair") == 0 and calls[0].get("retries") == 0          # exactly one request per run
    p0 = r["picks"][0]
    assert p0["sid"] == 3 and p0["q"] == "seller packing box" and p0["lib"] == 1 and p0["score"] == 88  # brand stripped
    assert r["picks"][1]["score"] == 70 and r["picks"][1]["kw"]
    r2 = B.plan_ai(sents, 4, lib, emit=ListEmitter())
    assert r2["source"] == "cache" and len(calls) == 1 and r2["picks"] == r["picks"]
    # a bare array (model dropped the wrapper) is accepted; malformed picks are dropped one by one
    ai.chat_json = lambda msgs, **kw: fake_chat_json(msgs, **kw)["picks"] + [{"t": 5}, "x"]
    r3 = B.plan_ai(sents, 3, lib, emit=ListEmitter())
    assert r3["source"] == "ai" and [x["sid"] for x in r3["picks"]] == [3, 5], r3
    assert B._plan_check("prose only") and B._plan_check({"picks": "no"}) and not B._plan_check([{"t": "00:01"}])
finally:
    ai.chat_json, ai.available = real_chat, real_avail

# ---------------------------------------------------------------- analyze with mocked Pexels + local folder (rules)
px_calls = []


def fake_pexels_get(params, key):
    px_calls.append(params)
    assert key == FAKE_KEY
    return {"videos": [{"id": 1000 + i, "url": f"https://www.pexels.com/video/x-{1000 + i}/", "duration": 8 + i,
                        "width": 1920, "height": 1080, "image": f"https://images.pexels.com/videos/{1000 + i}/p.jpg",
                        "user": {"name": f"Kreator {i}", "url": "https://www.pexels.com/@k"},
                        "video_files": [{"file_type": "video/mp4", "width": 640, "height": 360, "quality": "sd",
                                         "link": f"https://videos.pexels.com/{1000 + i}_sd.mp4"},
                                        {"file_type": "video/mp4", "width": 1920, "height": 1080, "quality": "hd",
                                         "link": f"https://videos.pexels.com/{1000 + i}_hd.mp4"}]} for i in range(3)]}


def fake_download(url, dest, emit=None, **kw):
    assert url.endswith("_hd.mp4"), url                                      # nearest file >= 2/3 of the frame
    Path(dest).parent.mkdir(parents=True, exist_ok=True)
    Path(dest).write_bytes((lib_dir / "Bisnis" / "ResellerPacking paket.mp4").read_bytes())
    return Path(dest)


B._pexels_get = fake_pexels_get
B._http_get = lambda url, headers=None, timeout=20, max_bytes=0: jpeg_bytes((10, 200, 10), (320, 180))
B._download = fake_download
B.pexels_key = lambda: FAKE_KEY
imagine_calls = []
B._imagine_http = lambda prompt, aspect, timeout=240: (imagine_calls.append((prompt, aspect)), jpeg_bytes())[1]

wd = tmp / "wd"
job = {"tool": "broll", "action": "analyze", "seq": tl.to_json(), "workdir": str(wd),
       "params": {"use_ai": False, "density": 3, "folder": str(lib_dir), "src_pexels": True, "src_ai": True,
                  "ai_max": 1, "transition": "fade", "placement": "auto", "scope": {"kind": "all"}}}
em = ListEmitter()
res = B.analyze(job, em)
assert "error" not in em.kinds() and res["planner"] == "rules", res
doc = RV.load(res["review"])
items = doc["items"]
assert doc["tool"] == "broll" and doc["canvas"] == {"w": 2292, "h": 960, "fps": tl.fps} and items
assert res["summary"]["n"] == len(items) and res["stats"]["target"] == 2
for it in items:
    assert 0 <= it["t0"] < it["t1"] <= tl.duration + 1e-6 and it["kind"] == "broll" and it["id"].startswith("b")
    assert abs(it["t0"] * tl.fps - round(it["t0"] * tl.fps)) < 0.07         # frame aligned (review keeps ms)
    assert B.MIN_DUR - 1e-6 <= it["t1"] - it["t0"] <= 4.0 + 1e-6
    assert it["place"] in ("full", "pip") and len(it["pos"]) == 2 and it["cands"] and it["pick"] >= 0
    srcs = [c["src"] for c in it["cands"]]
    assert srcs.count("pexels") == 3 and srcs[-1] == "ai", srcs
    for cnd in it["cands"]:
        if cnd["src"] != "ai":
            assert cnd["thumb"] and Path(cnd["thumb"]).is_file(), cnd
assert any(it["cands"][it["pick"]]["src"] == "local" for it in items)         # rules matched the folder
assert all(p_.get("locale") == "id-ID" for p_ in px_calls) and px_calls        # rules query = Indonesian keyword
dump = Path(res["review"]).read_text(encoding="utf-8") + json.dumps(em.items)
assert FAKE_KEY not in dump                                                    # the Pexels key never leaks
first_ids = [it["id"] for it in items]

# re-run keeps manual choices (touched rows: on flag + picked candidate)
items[0]["on"], items[0]["touched"], items[0]["pick"] = False, True, 1
RV.save(doc, res["review"])
res_b = B.analyze(job, ListEmitter())
doc_b = RV.load(res_b["review"])
assert [it["id"] for it in doc_b["items"]] == first_ids
assert doc_b["items"][0]["on"] is False and doc_b["items"][0]["pick"] == 1

# ---------------------------------------------------------------- fetch: AI preview + new keyword
row = doc_b["items"][-1]
row["pick"] = len(row["cands"]) - 1                                              # the AI candidate
RV.save(doc_b, res_b["review"])
fj = {"tool": "broll", "action": "fetch", "workdir": str(wd), "review": res_b["review"], "params": {"ids": [row["id"]]}}
fr = B.fetch(fj, ListEmitter())
assert fr["done"] == [row["id"]] and len(imagine_calls) == 1
doc_c = RV.load(res_b["review"])
ai_c = doc_c["items"][-1]["cands"][doc_c["items"][-1]["pick"]]
assert ai_c["src"] == "ai" and Path(ai_c["thumb"]).is_file() and ai_c["aspect"] == "16:9"
fr = B.fetch({**fj, "params": {"ids": [first_ids[0]], "query": "uang tunai"}}, ListEmitter())
doc_c = RV.load(res_b["review"])
r0 = doc_c["items"][0]
assert r0["kw"] == "uang tunai" and r0["cands"][0]["src"] == "local" and r0["cands"][0]["name"] == "uang tunai.mp4"
assert r0["pick"] == 0 and r0["touched"]

# ---------------------------------------------------------------- apply: pre-render + plan
for it in doc_c["items"]:
    it["on"] = True
doc_c["items"][0]["place"] = "pip"
src_kinds = [it["cands"][it["pick"]]["src"] for it in doc_c["items"]]
RV.save(doc_c, res_b["review"])
em = ListEmitter()
out = B.apply({"tool": "broll", "action": "apply", "workdir": str(wd), "review": res_b["review"], "seq": tl.to_json(),
               "params": {}}, em)
plan = out["plan"]
assert plan["kind"] == "broll" and plan["track"] == B.TRACK == "Klipora B-Roll" and plan["name"].endswith("(Klipora)")
assert plan["width"] == 2292 and plan["height"] == 960 and plan["render_fps"] == 30.0 and not out["failed"], out
assert len(plan["items"]) == len(doc_c["items"]) and len(imagine_calls) == 1    # cached AI image reused
for it, src in zip(plan["items"], src_kinds):
    info = M.probe(it["path"])
    assert (info["width"], info["height"]) == (2292, 960) and not info["has_audio"]
    assert info["duration"] >= it["t1"] - it["t0"] - 1e-3 and it["fade"] == B.FADE and it["src"] == src
    assert 0 < it["pos"][0] < 1 and 0 < it["pos"][1] < 1 and it["place"] in ("full", "pip")
assert plan["items"][0]["place"] == "pip"
if "pexels" in src_kinds:
    assert out["credits"] and "Pexels" in Path(out["credits"]).read_text(encoding="utf-8")
assert FAKE_KEY not in json.dumps(out) + json.dumps(em.items)
again = B.apply({"tool": "broll", "action": "apply", "workdir": str(wd), "review": res_b["review"], "params": {}},
                ListEmitter())
assert [x["path"] for x in again["plan"]["items"]] == [x["path"] for x in plan["items"]]   # renders reused, not rewritten
try:
    doc_c["items"] = [dict(it, on=False) for it in doc_c["items"]]
    RV.save(doc_c, res_b["review"])
    B.apply({"tool": "broll", "action": "apply", "workdir": str(wd), "review": res_b["review"], "params": {}}, ListEmitter())
    raise AssertionError("apply with nothing checked must fail")
except EngineError as e:
    assert e.code == "NO_ITEMS"

# ---------------------------------------------------------------- CLI round trip (real process, AI off)
job_cli = {"id": "broll-test", "tool": "broll", "action": "analyze", "seq": tl.to_json(), "workdir": str(tmp / "wd_cli"),
           "params": {"use_ai": False, "src_pexels": False, "src_ai": True, "density": 2}}
jp = tmp / "job.json"
jp.write_text(json.dumps(job_cli), encoding="utf-8")
r = subprocess.run([sys.executable, "-X", "utf8", str(_common.ENGINE / "cli.py"), "run", str(jp)], capture_output=True,
                   text=True, encoding="utf-8", timeout=180)
evs = [json.loads(ln) for ln in r.stdout.splitlines() if ln.startswith("{")]
assert r.returncode == 0 and evs[-1]["ev"] == "result" and Path(evs[-1]["data"]["review"]).is_file(), r.stdout[-800:]
assert [e["id"] for e in evs if e["ev"] == "stage"] == ["words", "plan", "screen", "source"]

# ---------------------------------------------------------------- optional: one real Grok planning call
if _common.flag("--live") and _common.have(MEDIA_2M):
    B._ai_cache = lambda: ai.Cache(tmp / "ai_live")
    live = B.plan_ai(sents, 4, None, emit=ListEmitter())
    print("live picks:", [(round(sents[x["sid"]]["start"], 1), x["kw"], x["q"], x["score"]) for x in live["picks"]])
    assert live["source"] == "ai" and live["picks"]

_common.cleanup("broll")
print("ok")
