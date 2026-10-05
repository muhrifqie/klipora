"""Auto Zoom (engine/ac/tools/zoom.py): camera math, screen/speech/rhythm planning on the real 49 s tutorial,
per-clip keyframes on a 15-clip AutoCut timeline, re-run reset entries, error paths and a short preview render.
Offline: cached transcripts only (no GPU, no Grok, no Premiere). ~10 s (first run +2 s for the activity cache).
    python engine/tests/test_zoom.py
"""
import json
import sys
import time

import numpy as np

import _common
from _common import MEDIA_49

from ac import review as RV
from ac.progress import ListEmitter
from ac.timeline import Timeline
from ac.tools import zoom as Z
from ac.util import EngineError

FPS = 120.0
FR = 1 / FPS


def frames_ok(K, dur, cut_times=()):
    """Viewport inside the source at every frame; no jumps except at the given instant cuts."""
    kt = [k[0] for k in K]
    prev = None
    for t in np.arange(0, dur, FR):
        s, cx, cy = Z.state_at(K, float(t), kt)
        assert s >= 1 - 1e-9, (t, s)
        assert 0.5 / s - 1e-6 <= cx <= 1 - 0.5 / s + 1e-6 and 0.5 / s - 1e-6 <= cy <= 1 - 0.5 / s + 1e-6, (t, s, cx, cy)
        if prev is not None and abs(s - prev) > 0.06:
            assert any(abs(t - c) < 1.5 * FR for c in cut_times), f"jump of {s - prev:.3f} at {t:.4f}"
        prev = s


def ev(t_full, t_hold, z=2.0, cx=0.3, cy=0.4, rin=0.6, rout=0.7, end="ease"):
    return {"t_in": t_full - rin, "t_full": t_full, "t_hold": t_hold, "t_out": t_hold + (0 if end == "cut" else rout),
            "z": z, "cx": cx, "cy": cy, "ease_in": "smooth" if rin else "lin", "ease_out": "smooth" if rout else "lin",
            "end": end}


def main():
    t_start = time.time()
    # ---------------------------------------------------------------- 1. camera curve (pure math)
    K = Z.camera_keys([ev(2.0, 4.0), ev(8.0, 9.0, z=1.5, cx=0.7, end="cut")], FPS, 12.0)
    frames_ok(K, 12.0, cut_times=[9.0])
    assert Z.state_at(K, 1.3)[0] == 1.0 and abs(Z.state_at(K, 3.0)[0] - 2.0) < 1e-9, "zoom complete at t_full"
    assert Z.state_at(K, 9.0 - FR)[0] == 1.5 and Z.state_at(K, 9.0)[0] == 1.0, "instant exit is one frame"
    assert all(abs(k[0] * FPS - round(k[0] * FPS)) < 1e-4 for k in K), "keys on the frame grid"
    # zoom about a fixed point: a source point keeps its screen position while the zoom ramps
    a, b = Z.state_at(K, 1.6), Z.state_at(K, 1.9)
    fixed = 0.5 + (0.3 - 0.5) * 2.0 / (2.0 - 1.0)   # source x whose screen position 0.5 + (x - cx) * s stays put
    xa = 0.5 + (fixed - a[1]) * a[0]
    xb = 0.5 + (fixed - b[1]) * b[0]
    assert abs(xa - xb) < 1e-6, (xa, xb)
    # calm pacing: two zooms 1 s apart glide viewport -> viewport without going back to 1x
    K2 = Z.camera_keys([ev(2.0, 4.0), ev(5.6, 7.0, cx=0.6)], FPS, 10.0)
    assert min(Z.state_at(K2, t)[0] for t in np.arange(4.0, 5.6, 0.02)) > 1.9, "direct move keeps the zoom"
    frames_ok(K2, 10.0)
    # ... but never across a hard cut
    K3 = Z.camera_keys([ev(2.0, 4.0), ev(5.6, 7.0, cx=0.6)], FPS, 10.0, hard=[4.5])
    assert min(Z.state_at(K3, t)[0] for t in np.arange(4.0, 5.6, 0.02)) == 1.0

    # per clip keys: guards pin holds, media time, nothing for clips that stay at 1x
    tl = Timeline({"name": "x", "fps": FPS, "duration": 12.0, "video": [{"index": 0, "clips": [
        {"path": str(MEDIA_49), "start": 0.0, "end": 3.0, "in": 10.0, "out": 13.0},
        {"path": str(MEDIA_49), "start": 3.0, "end": 5.0, "in": 20.0, "out": 22.0},
        {"path": str(MEDIA_49), "start": 5.0, "end": 7.0, "in": 30.0, "out": 32.0}]}]})
    ks = [Z.clip_keys(K, c, FPS) for c in tl.video[0].clips]
    assert ks[0] and ks[1] and not ks[2], [len(k) for k in ks]
    assert ks[0][0][0] == 10.0 and abs(ks[1][0][0] - 20.0) < 1e-6, "keys in clip media time"
    assert abs(ks[0][-1][1] - ks[1][0][1]) < 0.01, "continuous across the cut"
    hold = [k for k in ks[0] if abs(k[1] - 2.0) < 1e-9]
    assert len(hold) >= 2 and len(ks[0]) < 40, len(ks[0])
    ease = Z.clip_keys(K, tl.video[0].clips[0], FPS, interp="ease")
    assert len(ease) < len(ks[0]), "ease mode = no dense keys"

    if not _common.have(MEDIA_49):
        print("ok (math only, media missing)")
        return
    tmp = _common.scratch("zoom")
    raw = Timeline.from_media(MEDIA_49).to_json()
    raw["id"] = "seq-raw49"

    # ---------------------------------------------------------------- 2. screen mode, raw 49 s
    job = {"tool": "zoom", "action": "analyze", "seq": raw, "params": {"mode": "screen"}, "workdir": str(tmp)}
    em = ListEmitter()
    res = Z.analyze(job, em)
    assert "error" not in em.kinds() and res["mode"] == "screen", em.items[-3:]
    stages = [i["id"] for i in em.items if i["ev"] == "stage_done"]
    assert stages == ["read", "activity", "words", "plan", "thumbs"], stages
    doc = RV.load(res["review"])
    its = doc["items"]
    # same 5 sessions as the verified prototype (docs/research/vision.md 6.2): jump list, address bar, header
    # buttons (all end on a page change), product card, form field
    assert len(its) == 5 and all(it["on"] for it in its), [(it["t_full"], it["on"]) for it in its]
    want = [(9.67, "cut", 15.0), (16.76, "cut", 18.5), (21.8, "cut", 25.1), (26.5, "ease", None), (39.5, "ease", None)]
    for it, (tf, end, cut) in zip(its, want):
        assert abs(it["t_full"] - tf) < 0.8, (it["t_full"], tf)
        assert it["end"] == end, (it["id"], it["end"])
        if cut:
            assert abs(it["t_hold"] - cut) < 0.05 and it["t1"] == it["t_hold"], it
        assert 1.25 <= it["z"] <= 2.2 and it["kind"] == "zoom" and it["label"].startswith("Zoom ")
        z = it["z"]
        assert 0.5 / z - 1e-6 <= it["cx"] <= 1 - 0.5 / z + 1e-6 and 0.5 / z - 1e-6 <= it["cy"] <= 1 - 0.5 / z + 1e-6
        th = it.get("thumb")
        assert th and open(th, "rb").read(8) == b"\x89PNG\r\n\x1a\n", th
    assert its[1]["z"] == 2.2 and its[1]["cx"] < 0.3, "address bar zoom is top-left and tight"
    assert res["stats"]["thumbs"] == 5 and isinstance(res["summary"], str)
    # second run: activity from the cache next to the media
    em2 = ListEmitter()
    Z.analyze(job, em2)
    act_done = next(i for i in em2.items if i["ev"] == "stage_done" and i["id"] == "activity")
    assert act_done.get("note") == "dari cache", act_done
    # English job (the panel adds job["lang"]; cli.execute switches the language): stages, notes, rows, summary
    import cli
    em_en = ListEmitter()
    assert cli.execute(dict(job, lang="en", workdir=str(tmp / "en")), em_en) == 0, em_en.items[-3:]
    lab = {i["id"]: i.get("label") for i in em_en.items if i["ev"] == "stage"}
    note = next(i for i in em_en.items if i["ev"] == "stage_done" and i["id"] == "activity").get("note")
    res_en = next(i["data"] for i in em_en.items if i["ev"] == "result")
    assert lab["activity"] == "Analyze screen action" and note == "from cache", (lab, note)
    assert res_en["summary"] == "5 zooms used out of 5 moments", res_en["summary"]
    it_en = RV.load(res_en["review"])["items"]
    assert it_en[0]["src"] == "Screen action" and "." in it_en[1]["label"], (it_en[0]["src"], it_en[1]["label"])

    # apply -> host plan
    em3 = ListEmitter()
    ap = Z.apply(dict(job, action="apply", review=res["review"]), em3)
    pl = ap["plan"]
    assert pl["kind"] == "keyframes" and pl["events"] == 5 and pl["clips"] == 1 and 40 < pl["keys"] < 200, pl
    plan = json.load(open(pl["path"], encoding="utf-8"))
    c = plan["clips"][0]
    assert c["w"] == 2292 and c["h"] == 960 and c["track"] == 0 and plan["seq"]["id"] == "seq-raw49"
    ts = [k[0] for k in c["keys"]]
    assert ts == sorted(ts) and len(set(ts)) == len(ts) and ts[0] == 0.0 and ts[-1] <= 48.925
    assert all(k[1] >= 1.0 - 1e-9 for k in c["keys"])
    # one key per frame at most, every page-change exit is exactly one frame
    cut_keys = [i for i in range(1, len(ts)) if c["keys"][i][1] == 1.0 and c["keys"][i - 1][1] > 1.2]
    assert len(cut_keys) == 3 and all(abs(ts[i] - ts[i - 1] - FR) < 1e-5 for i in cut_keys), cut_keys

    # user turns 2 zooms off -> fewer keys, nothing in the second zoom any more
    doc["items"][1]["on"] = False
    doc["items"][1]["touched"] = True
    RV.save(doc, res["review"])
    ap2 = Z.apply(dict(job, action="apply", review=res["review"]), ListEmitter())
    plan2 = json.load(open(ap2["plan"]["path"], encoding="utf-8"))
    k2 = plan2["clips"][0]["keys"]
    assert ap2["plan"]["events"] == 4 and all(k[1] == 1.0 for k in k2 if 16.0 < k[0] < 18.6), "zoom 2 removed"
    # carry_over keeps the manual choice on re-analysis
    res_b = Z.analyze(job, ListEmitter())
    assert not RV.load(res_b["review"])["items"][1]["on"], "touched item stays off"

    # re-run reset: clips that carry keys from an earlier run are cleared first
    ap3 = Z.apply(dict(job, action="apply", review=res["review"], params={"reset": [
        {"track": 0, "start": 0.0, "end": 48.925, "in": 0.0, "scale": 100, "pos": [0.5, 0.5]}]}), ListEmitter())
    plan3 = json.load(open(ap3["plan"]["path"], encoding="utf-8"))
    assert plan3["clips"][0]["reset"] == {"scale": 100.0, "pos": [0.5, 0.5]} and ap3["plan"]["reset"] == 1

    # wrong sequence -> SEQ_CHANGED; nothing selected -> NO_ZOOM
    other = dict(raw, id="seq-other")
    try:
        Z.apply(dict(job, seq=other, action="apply", review=res["review"]), ListEmitter())
        raise AssertionError("SEQ_CHANGED expected")
    except EngineError as e:
        assert e.code == "SEQ_CHANGED", e.code
    for it in doc["items"]:
        it["on"] = False
    RV.save(doc, res["review"])
    try:
        Z.apply(dict(job, action="apply", review=res["review"]), ListEmitter())
        raise AssertionError("NO_ZOOM expected")
    except EngineError as e:
        assert e.code == "NO_ZOOM", e.code

    # ---------------------------------------------------------------- 3. AutoCut timeline (15 clips)
    kept = [[1.62, 2.56], [2.96, 7.47], [8.46, 10.36], [10.59, 11.5], [15.17, 17.92], [19.49, 22.51], [23.48, 24.98],
            [25.65, 26.85], [27.2, 29.96], [31.06, 31.79], [32.02, 36.61], [38.14, 40.84], [41.65, 42.46],
            [42.74, 44.3], [44.49, 45.87]]
    t, v = 0.0, []
    for a, b in kept:   # frame-aligned like Premiere
        d = round((b - a) * FPS) / FPS
        v.append({"name": "x.mp4", "path": str(MEDIA_49), "start": round(t, 6), "end": round(t + d, 6), "in": a, "out": a + d})
        t += d
    cut = {"id": "seq-cut15", "name": "cut15", "fps": FPS, "width": 2292, "height": 960, "duration": round(t, 6),
           "video": [{"index": 0, "name": "V1", "clips": v}, {"index": 1, "name": "V2", "clips": []}],
           "audio": [{"index": 0, "name": "A1", "clips": [dict(x) for x in v]}]}
    jc = {"tool": "zoom", "action": "analyze", "seq": cut, "params": {"mode": "screen"}, "workdir": str(tmp / "cut")}
    rc = Z.analyze(jc, ListEmitter())
    dc = RV.load(rc["review"])
    assert 3 <= len(dc["items"]) <= 6 and all(0 <= it["t0"] < it["t1"] <= t + 1e-6 for it in dc["items"])
    tlc = Timeline.from_json(cut)
    _tr, clips, _m = Z._target(tlc, {})
    hard = Z._boundaries(clips, FPS, {str(MEDIA_49): Z.activity(MEDIA_49)})
    assert 3 <= len(hard) <= 8, hard   # page changes inside removed parts are hard cuts, silences are not
    apc = Z.apply(dict(jc, action="apply", review=rc["review"]), ListEmitter())
    pc = json.load(open(apc["plan"]["path"], encoding="utf-8"))
    by_start = {round(x["start"], 3): x for x in v}
    for cl in pc["clips"]:
        src = by_start[round(cl["start"], 3)]
        assert all(src["in"] - 1e-6 <= k[0] <= src["out"] + 1e-6 for k in cl["keys"]), "keys inside the clip"
    assert pc["stats"]["clips"] >= 3

    # ---------------------------------------------------------------- 4. speech and rhythm (cached words)
    js = {"tool": "zoom", "action": "analyze", "seq": raw, "params": {"mode": "speech", "per_min": 4}, "workdir": str(tmp / "sp")}
    es = ListEmitter()
    rs = Z.analyze(js, es)
    ds = RV.load(rs["review"])
    on = [it for it in ds["items"] if it["on"]]
    assert 1 <= len(on) <= 4 and len(ds["items"]) >= len(on), [(it["t_full"], it["on"]) for it in ds["items"]]
    assert all(1.12 <= it["z"] <= 1.3 for it in ds["items"]) and all(it.get("note") for it in ds["items"])
    assert any("faces" == i.get("id") for i in es.items if i["ev"] == "stage_done")
    jr = {"tool": "zoom", "action": "analyze", "seq": raw, "params": {"mode": "rhythm"}, "workdir": str(tmp / "rh")}
    dr = RV.load(Z.analyze(jr, ListEmitter())["review"])
    assert len(dr["items"]) >= 3 and all(it["z"] == 1.15 and it["end"] == "cut" and it["t0"] == it["t_full"] for it in dr["items"])
    starts = [it["t_full"] for it in dr["items"]]
    assert all(b - a > 2.0 for a, b in zip(starts, starts[1:])), "every other sentence"

    # face anchor (talking head): YuNet on a COPY of the OpenCV sample cartoon (cache lands next to the copy)
    sample = _common.ROOT / "proto" / "vision" / "samples" / "Megamind.avi"
    if sample.is_file() and Z._yunet_model() is not None:
        import shutil
        mm = tmp / "Megamind.avi"
        shutil.copy(sample, mm)
        tlm = Timeline.from_media(mm)
        cfg = Z._params({"mode": "speech"})
        evs = [Z._event(1.0, 3.0, 1.25, 0.5, 0.5, cfg), Z._event(7.0, 9.0, 1.25, 0.5, 0.5, cfg)]
        warns = []
        n_f = Z._anchor_faces(evs, tlm.video[0].clips, ListEmitter(), warns)
        assert n_f == 2 and all(e["anchor"] == "wajah" for e in evs) and not warns, (n_f, evs, warns)
        assert abs(evs[1]["cx"] - 0.67) < 0.1 and evs[1]["cy"] < 0.5, evs[1]   # face top right at ~8 s
        assert (tmp / "Megamind_zoomfaces.json").is_file()

    # ---------------------------------------------------------------- 5. preview render (NVENC or libx264)
    doc = RV.load(res["review"])
    pr = Z.preview(dict(job, action="preview", review=res["review"], params={"t0": 15.5, "t1": 18.5, "ids": [doc["items"][1]["id"]]}), ListEmitter())
    import subprocess
    info = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-count_packets", "-show_entries",
                           "stream=width,height,nb_read_packets", "-of", "json", pr["path"]], capture_output=True, text=True)
    st = json.loads(info.stdout)["streams"][0]
    assert st["width"] == 1146 and st["height"] == 480 and abs(int(st["nb_read_packets"]) - 90) <= 2, st

    _common.cleanup("zoom")
    print(f"ok ({time.time() - t_start:.1f} s)")


if __name__ == "__main__":
    sys.exit(main())
