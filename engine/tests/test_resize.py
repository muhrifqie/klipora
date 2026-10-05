"""Auto Resize engine checks (offline, real media, no Premiere, no GPU job). ~30-60 s the first time (activity
analysis of the 49 s clip is cached next to it afterwards).

  python engine/tests/test_resize.py           geometry, camera math, analyze/apply on the 49 s clip and a 15-clip
                                               cut of it, review toggles, a 3 s focus render (deleted), cancel,
                                               face path on proto/vision/samples/synth_talk.mp4 (ground truth)
  python engine/tests/test_resize.py --long    + the 34.6 min recording as a 727-clip AutoCut timeline
"""
import json
import os
import subprocess
import time

import _common
from _common import MEDIA_34M, MEDIA_49, ROOT, approx
from ac import media as M
from ac.progress import ListEmitter
from ac.timeline import Timeline
from ac.tools import _resize_camera as CAM
from ac.tools import _resize_render as R
from ac.tools import resize
from ac.util import Cancelled, EngineError, ffprobe_exe

SYN = ROOT / "proto" / "vision" / "samples" / "synth_talk.mp4"
SYN_GT = ROOT / "proto" / "vision" / "samples" / "synth_talk_gt.json"
KEPT = [[1.62, 2.56], [2.96, 7.47], [8.46, 10.36], [10.59, 11.5], [15.17, 17.92], [19.49, 22.51], [23.48, 24.98],
        [25.65, 26.85], [27.2, 29.96], [31.06, 31.79], [32.02, 36.61], [38.14, 40.84], [41.65, 42.46], [42.74, 44.3],
        [44.49, 45.87]]                                          # the (AutoCut) cut of the 49 s clip (tools/cep_stub.js)


def cut_seq(media, kept, name):
    info = M.probe(media)
    t, clips = 0.0, []
    for a, b in kept:
        clips.append({"name": "x.mp4", "path": str(media), "start": round(t, 6), "end": round(t + b - a, 6), "in": a, "out": b})
        t += b - a
    return {"id": "seq-" + name, "name": name, "fps": 120, "width": info["width"], "height": info["height"],
            "duration": round(t, 6), "video": [{"index": 0, "name": "V1", "clips": clips}],
            "audio": [{"index": 0, "name": "A1", "clips": [dict(c) for c in clips]}]}


def test_geometry():
    g = R.geometry("9x16", "crop", 2292, 960)
    assert approx(g["scale"], 2.0) and approx(g["cw"], 540 / 2292, 1e-9) and g["ch"] == 1.0, g
    f = R.geometry("9x16", "focus", 2292, 960)
    assert f["win"] == [0, 378, 1080, 1080] and approx(f["cw"], 960 / 2292, 1e-9), f      # 1:1 window, 20 % title zone
    assert R.geometry("16x9", "focus", 2292, 960)["cw"] == 1.0                             # 16:9 focus = fit + blur
    c16 = R.geometry("16x9", "crop", 2292, 960)
    assert approx(c16["scale"], 1.125) and approx(c16["cw"], 1920 / (2292 * 1.125), 1e-9)
    z = R.geometry("9x16", "crop", 2292, 960, zoom=1.4)
    assert approx(z["ch"], 1 / 1.4, 1e-9) and approx(z["cw"], 540 / 2292 / 1.4, 1e-9)
    print("geometry ok")


def test_camera_math():
    pts = [(i * 0.1, (i % 7) * 0.01 + (0.3 if i > 40 else 0)) for i in range(120)]
    kept = CAM.rdp(pts, 0.005)
    assert kept[0] == pts[0] and kept[-1] == pts[-1] and len(kept) < len(pts)
    for t, x in pts:   # every original point within eps of the reduced polyline
        assert abs(x - CAM.value_at([list(k) for k in kept], t)) <= 0.005 + 1e-9
    sp = CAM.speed_params("normal", "screen")
    ts = [i / 10 for i in range(60)]
    k, mode = CAM.plan_axis(ts, [0.5] * 60, 0.0, 6.0, 0.25, sp, "screen", 0.5, 1e-4)
    assert mode == "static" and k == [[0.0, 0.5]], (k, mode)
    xs = [0.3] * 25 + [0.75] * 35                       # subject jumps across the frame and stays
    k, mode = CAM.plan_axis(ts, xs, 0.0, 6.0, 0.25, sp, "screen", 0.3, 1e-4)
    assert mode == "track" and abs(k[0][1] - 0.3) < 0.02 and abs(k[-1][1] - 0.75) < 0.02, (k, mode)
    assert all(b[0] >= a[0] for a, b in zip(k, k[1:])), k
    k, mode = CAM.plan_axis(ts, xs, 0.0, 6.0, 0.25, CAM.speed_params("none", "screen"), "screen", 0.3, 1e-4)
    assert mode == "static" and len(k) == 1
    k, mode = CAM.plan_axis(ts, [float("nan")] * 60, 0.0, 6.0, 0.25, sp, "screen", 0.42, 1e-4)
    assert mode == "hold" and k == [[0.0, 0.42]]
    print("camera math ok")


def check_plan(plan, seq, target="9x16"):
    wf, hf = R.TARGETS[target]["size"]
    assert plan["kind"] == "reframe" and (plan["w"], plan["h"]) == (wf, hf)
    fr = 1.0 / seq["fps"]
    clips = seq["video"][0]["clips"]
    assert len(plan["clips"]) == len(clips)
    n_keys = 0
    for it, c in zip(plan["clips"], clips):
        assert approx(it["start"], c["start"], 1e-3) and approx(it["scale"], 200.0, 1e-3), it
        s = it["scale"] / 100
        lo, hi = 1 - 2292 * s / wf / 2, 2292 * s / wf / 2           # Position range that keeps the frame covered
        vals = [it["pos"]] if "pos" in it else [k[1:] for k in it["keys"]]
        assert all(lo - 1e-4 <= x <= hi + 1e-4 and approx(y, 0.5, 1e-6) for x, y in vals), (it, lo, hi)
        if "keys" in it:
            ts = [k[0] for k in it["keys"]]
            assert ts == sorted(ts) and ts[0] >= c["start"] - 1e-4 and ts[-1] <= c["end"] - fr + 1e-3, (it, c)
            for a, b in zip(it["keys"], it["keys"][1:]):         # a camera cut = two keys exactly one frame apart,
                if b[0] - a[0] < fr * 1.5:                          # both on the frame grid (no half-way frame)
                    assert all(abs(t / fr - round(t / fr)) < 1e-3 for t in (a[0], b[0])), (a, b, fr)
            n_keys += len(ts)
    return n_keys


def test_analyze_apply(tmp):
    tl = Timeline.from_media(MEDIA_49)
    job = {"tool": "resize", "action": "analyze", "seq": tl.to_json(), "params": {}, "workdir": str(tmp / "raw")}
    em = ListEmitter()
    t0 = time.time()
    res = resize.analyze(job, em)
    kinds = em.kinds()
    assert "error" not in kinds and kinds.count("stage") == 4, kinds
    assert res["content"][0]["kind"] == "screen", res["content"]                   # web-page faces are not a talking head
    assert res["advice"] and res["advice"]["layout"] == "focus"                    # 9:16 crop shows 24 % of the width
    assert os.path.getsize(res["preview"]) > 20000
    doc = json.load(open(res["review"], encoding="utf-8"))
    assert doc["tool"] == "resize" and doc["items"], doc.get("stats")
    assert all(it["kind"] in ("move", "jump") and it["t1"] > it["t0"] for it in doc["items"])
    path = json.load(open(tmp / "raw" / "resize" / "path.json", encoding="utf-8"))
    shots = path["shots"]
    assert approx(shots[0]["t0"], 0.0, 1e-3) and approx(shots[-1]["t1"], tl.duration, 1e-3)
    assert all(approx(a["t1"], b["t0"], 1e-3) for a, b in zip(shots, shots[1:])), "shots must tile the scope"
    cw = 540 / 2292
    for sh in shots:
        assert all(cw / 2 - 1e-6 <= v <= 1 - cw / 2 + 1e-6 for _, v in sh["kx"]), sh
    print(f"analyze ok: {res['summary']} ({time.time() - t0:.1f} s)")
    # English job (job["lang"] from the panel): stage labels, advice, review rows and summary in English
    import cli
    em_en = ListEmitter()
    assert cli.execute(dict(job, lang="en", workdir=str(tmp / "raw_en")), em_en) == 0, em_en.items[-3:]
    res_en = next(i["data"] for i in em_en.items if i["ev"] == "result")
    assert [i.get("label") for i in em_en.items if i["ev"] == "stage"][1] == "Analyze video"
    assert res_en["summary"].startswith("9:16: ") and "position changes in" in res_en["summary"], res_en["summary"]
    assert "Focus + blur shows more" in res_en["advice"]["text"], res_en["advice"]
    rows_en = json.load(open(res_en["review"], encoding="utf-8"))["items"]
    assert all(r["label"].startswith(("Follow ", "Jump to ")) for r in rows_en), [r["label"] for r in rows_en]

    job_a = dict(job, action="apply", review=res["review"])
    out = resize.apply(job_a, ListEmitter())
    n_keys = check_plan(out["plan"], tl.to_json())
    assert out["plan"]["inout"] is None and out["plan"]["name"].endswith("(9x16)")
    # every row off -> the camera holds the first framing: no keyframes left
    for it in doc["items"]:
        it["on"], it["touched"] = False, True
    json.dump(doc, open(res["review"], "w", encoding="utf-8"))
    held = resize.apply(job_a, ListEmitter())["plan"]["clips"]
    assert all("pos" in c for c in held), held
    print(f"apply ok: {n_keys} keys; all rows off -> static")

    # carry over: a re-run keeps the user's unchecked rows
    res2 = resize.analyze(job, ListEmitter())
    doc2 = json.load(open(res2["review"], encoding="utf-8"))
    assert all(not it["on"] for it in doc2["items"]), "carry_over lost the manual choices"
    assert res2["stats"]["held"] == len(doc2["items"]) and res["stats"]["held"] == 0, (res["stats"], res2["stats"])

    # 15-clip AutoCut sequence: one Motion item per clip, keys inside each clip, In/Out scope keeps the frame size
    seq = cut_seq(MEDIA_49, KEPT, "Cut 15 (AutoCut)")
    jc = {"tool": "resize", "action": "analyze", "seq": seq, "params": {"speed": "fast"}, "workdir": str(tmp / "cut")}
    rc = resize.analyze(jc, ListEmitter())
    oc = resize.apply(dict(jc, action="apply", review=rc["review"]), ListEmitter())
    check_plan(oc["plan"], seq)
    js = dict(jc, params={"speed": "fast", "scope": {"kind": "inout", "t0": 5.0, "t1": 20.0}})
    rs = resize.analyze(js, ListEmitter())
    assert rs["scope"] == [5.0, 20.0]
    os_ = resize.apply(dict(js, action="apply", review=rs["review"]), ListEmitter())
    assert os_["plan"]["inout"] == [5.0, 20.0] and len(os_["plan"]["clips"]) == 15
    # same aspect -> clear error
    try:
        resize.analyze(dict(job, params={"target": "16x9"}, seq=Timeline.from_media(SYN).to_json()) if SYN.is_file()
                       else dict(job, params={"target": "bogus"}), ListEmitter())
        raise AssertionError("expected an EngineError")
    except EngineError as e:
        assert e.code in ("SAME_ASPECT", "BAD_PARAM"), e.code
    print(f"cut timeline ok: {oc['summary']}")


class CancelAfter(ListEmitter):
    """Cancels the job at the n-th progress event of stage `stage`."""

    def __init__(self, stage, n):
        super().__init__()
        self.want, self.n, self.cur = stage, n, None

    def stage(self, id, *a, **k):
        self.cur = id
        return super().stage(id, *a, **k)

    def progress(self, pct, note=None, force=False):
        if self.cur == self.want:
            self.n -= 1
            if self.n <= 0:
                self.cancel()
        return super().progress(pct, note, force)


def test_render(tmp):
    tl = Timeline.from_media(MEDIA_49)
    job = {"tool": "resize", "action": "render", "seq": tl.to_json(), "workdir": str(tmp / "render"),
           "params": {"layout": "focus", "scope": {"kind": "inout", "t0": 10.0, "t1": 13.0}}}
    rdir = tmp / "render" / "resize"                      # leftovers of a run the panel killed (taskkill /F)
    rdir.mkdir(parents=True, exist_ok=True)
    for name in ("old_9x16_010101.part.mp4", "audio_010101.m4a", "done_9x16_010101.mp4"):
        (rdir / name).write_bytes(b"x")
    res = resize.render(job, ListEmitter())
    assert not res["path"].endswith(".part.mp4") and os.path.isfile(res["path"]), res["path"]
    assert sorted(p.name for p in rdir.glob("*.m*") if p.name != os.path.basename(res["path"])) == ["done_9x16_010101.mp4"]
    os.remove(rdir / "done_9x16_010101.mp4")
    info = M.probe(res["path"])
    assert (info["width"], info["height"]) == (1080, 1920) and approx(info["duration"], 3.0, 0.08), info
    assert info["has_audio"] and info["fps"] == 30.0 and res["plan"]["kind"] == "import_render"
    tags = subprocess.run([ffprobe_exe(), "-v", "error", "-select_streams", "v", "-show_entries",
                           "stream=color_space,color_transfer,color_primaries", "-of", "csv=p=0", res["path"]],
                          capture_output=True, text=True).stdout.strip()
    assert tags == "bt709,bt709,bt709", tags                      # Premiere must not guess the colour space
    fr = M.frame_grab(res["path"], 1.5)
    top, mid = fr[100:300].mean(), fr[400:1400].mean()                   # blurred dark band vs sharp window
    assert fr.shape == (1920, 1080, 3) and mid > 0, (top, mid)
    print(f"render ok: {res['summary']}, {res['render']['codec']} {res['render']['secs']} s")
    os.remove(res["path"])
    em = CancelAfter("render", 3)
    try:
        resize.render(dict(job, params=dict(job["params"], layout="crop")), em)
        raise AssertionError("expected Cancelled")
    except Cancelled:
        pass
    left = [p for p in (tmp / "render" / "resize").glob("*.m*")]
    assert not left, f"partial files left after cancel: {left}"
    print("render cancel ok (no partial files)")


def test_faces(tmp):
    if not (SYN.is_file() and SYN_GT.is_file()):
        print("SKIP faces (proto/vision/samples/synth_talk.mp4 missing)")
        return
    gt = json.load(open(SYN_GT))
    tl = Timeline.from_media(SYN)
    job = {"tool": "resize", "action": "analyze", "seq": tl.to_json(), "params": {}, "workdir": str(tmp / "syn"),
           "cache_dir": str(tmp / "syn")}
    res = resize.analyze(job, ListEmitter())
    assert res["content"][0]["kind"] == "face", res["content"]
    shots = json.load(open(tmp / "syn" / "resize" / "path.json", encoding="utf-8"))["shots"]
    starts = [s["t0"] for s in shots]
    for cut in (6.0, 10.0, 17.0):                                       # hard cuts found to the frame
        assert any(abs(s - cut) < 0.05 for s in starts), (cut, starts)
    sw = [s["t0"] for s in shots if s["reason"] == "speaker"]
    assert any(abs(s - 13.5) <= 0.35 for s in sw), sw                    # speaker switch = camera cut
    cam = R.CamPath(shots)
    cw = 1080 * 1080 / 1920 / 1920
    cov = n = 0
    for f in gt["frames"]:
        if not f["faces"]:
            continue
        b = f["faces"][f["speaker"]] if f["speaker"] is not None and f["speaker"] < len(f["faces"]) else f["faces"][0]
        c, _ = cam.at(f["t"])
        n += 1
        cov += (b[0] >= c - cw / 2 - 1e-9) and (b[0] + b[2] <= c + cw / 2 + 1e-9)
    assert cov / n >= 0.95, cov / n
    print(f"faces ok: subject fully in the 9:16 crop {100 * cov / n:.1f} % (proto hybrid: 98.8 %), switches {sw}")


def test_long(tmp):
    segs = json.load(open(MEDIA_34M.with_name(MEDIA_34M.stem + "_autocut.json"), encoding="utf-8"))["segments"]
    seq = cut_seq(MEDIA_34M, segs, "Long (AutoCut)")
    for sp in ("slow", "normal", "fast"):
        job = {"tool": "resize", "action": "analyze", "seq": seq, "params": {"speed": sp}, "workdir": str(tmp / "long")}
        t0 = time.time()
        res = resize.analyze(job, ListEmitter())
        out = resize.apply(dict(job, action="apply", review=res["review"]), ListEmitter())
        n = check_plan(out["plan"], seq)
        print(f"long {sp}: {res['summary']}, {res['stats']['per_min']}/min, {n} keys, {time.time() - t0:.1f} s")


if __name__ == "__main__":
    tmp = _common.scratch("resize")
    test_geometry()
    test_camera_math()
    if _common.have(MEDIA_49):
        test_analyze_apply(tmp)
        test_render(tmp)
    test_faces(tmp)
    if _common.flag("--long") and _common.have(MEDIA_34M):
        test_long(tmp)
    _common.cleanup("resize")
    print("ok")
