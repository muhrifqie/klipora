"""Podcast Multicam (engine/ac/tools/podcast.py) on a SYNTHETIC two-speaker setup (no real podcast footage exists):
real Indonesian speech of the 2 min test file is split into alternating turns on two 'mics' (each hears the other
at -18 dB + noise, 0.3 s 'mhm' backchannels), cameras = the same video on three tracks (wide, Andi, Budi).

    python engine/tests/test_podcast.py                 # offline checks (~10 s)
    python engine/tests/test_podcast.py --bench         # + 35 min benchmark vs the research prototype numbers
    python engine/tests/test_podcast.py --make <dir>    # write the synthetic mics for the Premiere verifier, no test
"""
import json
import sys
import time
import wave
from pathlib import Path

import numpy as np

import _common
from _common import MEDIA_2M, MEDIA_34M
from ac import i18n, media as M
from ac import review as RV
from ac.progress import ListEmitter
from ac.tools import podcast as P
from ac.util import EngineError, write_json

SR = M.SR
DISTINCT_IN = (0.0, 300.0, 600.0)     # source in-points of V1/V2/V3 in podcast_test_distinct.xml (34,6 min file)


# ---------------------------------------------------------------- synthetic mics (port of podcast_sim.synth)

def synth(x, seed=7, bleed_db=-18.0, noise_db=-62.0):
    """Talk spurts grouped into 3-12 s turns alternate between mic A and B; each mic hears the other at bleed_db
    plus noise; the listener says a 0.3 s 'mhm' (speech snippet at 0.7x) every 6-10 s.
    -> (mic_a, mic_b, truth per 10 ms frame: 0 = A, 1 = B, -1 = nobody)"""
    rng = np.random.default_rng(seed)
    db = M.rms_db(x)
    segs = M.runs(M.gate(db, M.otsu_db(db)))
    merged = []
    for a, b in segs:
        if merged and (b - merged[-1][0] < 6 or a - merged[-1][1] < 0.3):
            merged[-1][1] = b
        else:
            merged.append([a, b])
    a_sig, b_sig = np.zeros_like(x), np.zeros_like(x)
    truth = np.full(len(db), -1, np.int8)
    for k, (s, e) in enumerate(merged):
        i, j = int(s * SR), int(e * SR)
        (a_sig if k % 2 == 0 else b_sig)[i:j] = x[i:j]
        truth[int(s / M.HOP):int(e / M.HOP)] = k % 2
    for k, (s, e) in enumerate(merged):
        t = s + 2.0
        while t + 0.3 < e:
            src = int(rng.uniform(0, len(x) / SR - 0.3) * SR)
            snip = x[src:src + int(0.3 * SR)] * 0.7
            tgt = b_sig if k % 2 == 0 else a_sig
            tgt[int(t * SR):int(t * SR) + len(snip)] += snip
            t += rng.uniform(6, 10)
    bleed = 10 ** (bleed_db / 20)

    def noise():
        return rng.normal(0, 10 ** (noise_db / 20), len(x)).astype(np.float32)
    return (a_sig + bleed * b_sig + noise()).astype(np.float32), (b_sig + bleed * a_sig + noise()).astype(np.float32), truth


def write_wav(path, *chans):
    """16-bit PCM WAV at 16 kHz, one channel per array."""
    data = np.stack([np.clip(c, -1, 1) for c in chans], axis=1)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(len(chans))
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes((data * 32767).astype("<i2").tobytes())
    return Path(path)


def make_fixture(out_dir, media=MEDIA_2M, delay_b=0.2):
    """Write mic_a.wav, mic_b.wav, mics_lr.wav (A left, B right), mic_b_late.wav (B delay_b s late) + truth.json."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    x = M.load_audio(media)
    a, b, truth = synth(x)
    files = {"a": write_wav(out / "mic_a.wav", a), "b": write_wav(out / "mic_b.wav", b),
             "lr": write_wav(out / "mics_lr.wav", a, b),
             "late": write_wav(out / "mic_b_late.wav", np.concatenate([np.zeros(int(delay_b * SR), np.float32), b])[:len(b)])}
    write_json(out / "truth.json", {"media": str(media), "hop": M.HOP, "delay_b": delay_b,
                                    "turns": [[round(t * M.HOP, 2)] for t in np.flatnonzero(np.diff(truth) != 0)],
                                    "truth": truth.tolist()})
    # Premiere test sequence (verifier): V1 wide, V2 cam Andi, V3 cam Budi (= the same video), A1 mic A, A2 mic B
    from ac import xmeml as X
    vdur = min(M.probe(media)["duration"], len(x) / SR)
    clips = [{"path": str(media), "start": 0.0, "src_in": 0.0, "src_out": vdur, "video": True, "audio": False, "vtrack": v}
             for v in range(3)]
    clips += [{"path": str(files[k]), "start": 0.0, "src_in": 0.0, "src_out": vdur, "video": False, "audio": True,
               "atrack": i} for i, k in enumerate(("a", "b"))]
    files["xml"] = X.write(out / "podcast_test.xml", X.build_sequence("AC Podcast Test", clips, 120, 2292, 960))
    if MEDIA_34M.is_file():   # same layout, but every camera shows a different part of the 34,6 min file (frame checks)
        for k, off in enumerate(DISTINCT_IN):
            clips[k] = dict(clips[k], path=str(MEDIA_34M), src_in=off, src_out=off + vdur)
        files["xml_distinct"] = X.write(out / "podcast_test_distinct.xml",
                                        X.build_sequence("AC Podcast Test Distinct", clips, 120, 2292, 960))
    return files, truth, len(x) / SR


def seq_json(dur, mic_a, mic_b, media=MEDIA_2M, names=("Mic Andi", "Mic Budi")):
    def clip(path, end):
        return {"name": Path(path).name, "path": str(path), "start": 0.0, "end": end, "in": 0.0, "out": end}
    return {"id": "seq-podcast-test", "name": "Podcast Test", "fps": 120, "width": 2292, "height": 960,
            "duration": dur,
            "video": [{"index": 0, "name": "Wide", "clips": [clip(media, dur)]},
                      {"index": 1, "name": "Cam Andi", "clips": [clip(media, dur)]},
                      {"index": 2, "name": "Cam Budi", "clips": [clip(media, dur)]}],
            "audio": [{"index": 0, "name": names[0], "clips": [clip(mic_a, dur)]},
                      {"index": 1, "name": names[1], "clips": [clip(mic_b, dur)]}]}


SPEAKERS = [{"name": "Andi", "mic": 0, "cam": 1}, {"name": "Budi", "mic": 1, "cam": 2}]


def accuracy(items, truth, cam_of_spk=(1, 2)):
    """(right, wrong): share of truth talk frames showing the talking speaker's camera / the OTHER speaker's
    camera (wide shots and cutaways count as neither right nor wrong speaker)."""
    cam = np.full(len(truth), -9)
    for it in items:
        cam[int(round(it["t0"] / M.HOP)):int(round(it["t1"] / M.HOP))] = it["cam"]
    talk = truth >= 0
    want = np.where(truth == 0, cam_of_spk[0], cam_of_spk[1])
    other = np.where(truth == 0, cam_of_spk[1], cam_of_spk[0])
    return float(np.mean(cam[talk] == want[talk])), float(np.mean(cam[talk] == other[talk]))


def run(job):
    em = ListEmitter()
    res = P.analyze(job, em)
    assert "error" not in em.kinds(), em.items
    return res, em


# ---------------------------------------------------------------- pure checks (no media)

def check_shot_rules():
    o = P.options({"preset": "normal", "react": False, "max_shot": 0})
    n = 3000                                     # 30 s
    lab = np.full(n, P.SILENT, np.int8)
    lab[0:1000] = 0                              # A talks 0-10 s
    lab[400:430] = 1                             # B 'mhm' 0.3 s at 4 s: no switch
    lab[1000:2000] = 1                           # B talks 10-20 s
    lab[2000:2200] = P.CROSS                     # crosstalk 20-22 s
    lab[2200:3000] = 0                           # A again
    sh = P.plan_shots(lab, 2, True, o, 0.0, 30.0)
    cams = [(round(a, 2), round(b, 2), c) for a, b, c, _ in sh]
    assert cams == [(0.0, 9.75, 0), (9.75, 20.0, 1), (20.0, 22.0, 2), (22.0, 30.0, 0)], cams
    # no wide camera: crosstalk keeps the current speaker
    sh = P.plan_shots(lab, 2, False, o, 0.0, 30.0)
    assert [c for _, _, c, _ in sh] == [0, 1, 0], sh
    # min shot: B holds the floor for 1 s (> confirm), A takes it back at once -> the return waits for min_shot
    lab2 = np.full(2000, P.SILENT, np.int8)
    lab2[0:500] = 0
    lab2[500:600] = 1
    lab2[600:2000] = 0
    sh = P.plan_shots(lab2, 2, False, o, 0.0, 20.0)
    assert [(round(a, 2), round(b, 2), c) for a, b, c, _ in sh] == [(0.0, 4.75, 0), (4.75, 6.75, 1), (6.75, 20.0, 0)], sh
    # silence >= 2,5 s goes wide (cut 0,5 s into the pause), then back to the next speaker
    lab3 = np.full(2000, P.SILENT, np.int8)
    lab3[0:800] = 0
    lab3[1200:2000] = 1
    sh = P.plan_shots(lab3, 2, True, o, 0.0, 20.0)
    assert [c for _, _, c, _ in sh] == [0, 2, 1] and abs(sh[1][0] - 8.5) < 1e-6 and abs(sh[2][0] - 11.75) < 1e-6, sh
    print("  shot rules ok")


def check_cutaways():
    o = P.options({"preset": "normal"})          # max_shot 15, react on
    lab = np.full(6000, P.SILENT, np.int8)       # 60 s monologue of A with sentence pauses
    lab[0:6000] = 0
    for p in range(500, 6000, 700):
        lab[p:p + 40] = P.SILENT                 # 0,4 s pause every 7 s
    lab[2600:2630] = 1                           # B 'mhm' at 26 s
    sh = P.plan_shots(lab, 2, True, o, 0.0, 60.0)
    kinds = [w for *_, w in sh]
    assert "reaksi" in kinds and "selingan" in kinds, sh
    r = next(s for s in sh if s[3] == "reaksi")
    assert r[2] == 1 and abs(r[0] - 25.7) < 1e-6 and abs(r[1] - r[0] - 2.0) < 1e-6, r
    for a, b, c, w in sh:
        assert b - a >= o["min_shot"] - 1e-6, (a, b, c, w)
    gaps = [b - a for a, b, c, w in sh if c == 0]
    assert max(gaps) <= o["max_shot"] + 1e-6, gaps
    # selingan lands in a pause when there is one
    sel = [s for s in sh if s[3] == "selingan"]
    assert all(lab[int(round((s[0] - 0.1) / M.HOP))] == P.SILENT for s in sel), sel
    # without a wide camera the cutaway shows the listener
    sh2 = P.plan_shots(lab, 2, False, o, 0.0, 60.0)
    assert all(c == 1 for a, b, c, w in sh2 if w == "selingan"), sh2
    print("  cutaways ok")


def check_offset():
    rng = np.random.default_rng(3)
    x = rng.normal(0, 0.1, SR * 30).astype(np.float32)
    late = np.concatenate([np.zeros(int(0.137 * SR), np.float32), x])[:len(x)]
    other = np.convolve(late, [0.6, 0.3, 0.1], "same") * 0.4 + rng.normal(0, 0.01, len(x))
    off, psr = P.find_offset(x, other.astype(np.float32), max_lag=2.0)
    assert abs(off - 0.137) < 0.001 and psr > 5, (off, psr)
    off, psr = P.find_offset(other.astype(np.float32), x, max_lag=2.0)
    assert abs(off + 0.137) < 0.001, off
    print(f"  find_offset ok ({off:+.4f} s, PSR {psr:.0f})")


def check_texts():
    """Sync warning in both languages (the Indonesian one is what the panel test and users already know)."""
    r = {"warn": True, "offset": 0.2, "kind": "mic", "name": "Budi"}
    assert P.sync_text(r, "Andi").startswith("Mic Budi telat 0,20 dtk dari mic Andi."), P.sync_text(r, "Andi")
    with i18n.using("en"):
        assert P.sync_text(r, "Andi").startswith("Mic Budi is 0.20 s late against mic Andi."), P.sync_text(r, "Andi")
        assert P.summary_text({"switches": 1, "speakers": 2}) == "1 camera switch, 2 speakers"
    assert P.summary_text({"switches": 12, "speakers": 2}) == "12 pergantian kamera, 2 pembicara"
    print("  texts ok (id + en)")


def check_plan():
    doc = {"tool": "podcast", "duration": 30.0, "fps": 30,
           "cams": [{"track": 1, "name": "Andi"}, {"track": 2, "name": "Budi"}, {"track": 0, "name": "Wide"}],
           "items": [RV.item(0, 10, "spk", cam=1), RV.item(10, 15, "spk", cam=2),
                     RV.item(15, 20, "wide", on=False, cam=0), RV.item(20, 30, "spk", cam=1)]}
    RV.assign_ids(doc["items"])
    plan = P.build_plan(doc)
    assert plan["shots"] == [[0.0, 10.0, 1], [10.0, 20.0, 2], [20.0, 30.0, 1]], plan["shots"]
    assert plan["cuts"] == [[10.0, [1, 2]], [20.0, [1, 2]]], plan["cuts"]
    doc["items"][2]["on"] = True
    doc["items"][0]["t0"] = 5.0                     # scope starts at 5 s: every camera is razored there
    plan = P.build_plan(doc)
    assert plan["cuts"][0] == [5.0, [0, 1, 2]] and plan["cuts"][2] == [15.0, [0, 2]], plan["cuts"]
    print("  plan ok")


def check_errors(seq):
    def err(params):
        try:
            P.analyze({"tool": "podcast", "action": "analyze", "seq": seq, "params": params, "workdir": str(tmp)},
                      ListEmitter())
        except EngineError as e:
            return e.code
        return None
    assert err({"speakers": SPEAKERS[:1]}) == "NEED_SPEAKERS"
    assert err({"speakers": [SPEAKERS[0], dict(SPEAKERS[1], mic=0)]}) == "SAME_MIC"
    assert err({"speakers": [SPEAKERS[0], dict(SPEAKERS[1], cam=7)]}) == "NO_CAM"
    assert err({"speakers": [SPEAKERS[0], dict(SPEAKERS[1], cam=1)]}) == "NO_SWITCH"
    assert err({"speakers": SPEAKERS, "wide": 1}) == "NO_CAM"
    # English job language (panel UI language): messages follow the job
    try:
        cli_job = {"tool": "podcast", "action": "analyze", "seq": seq, "params": {"speakers": SPEAKERS[:1]},
                   "workdir": str(tmp), "lang": "en"}
        with i18n.using(cli_job["lang"]):
            P.analyze(cli_job, ListEmitter())
        raise AssertionError("one speaker must fail")
    except EngineError as e:
        assert e.msg == "You need at least 2 speakers, each with their own mic track.", e.msg
    print("  errors ok")


# ---------------------------------------------------------------- benchmark (research numbers)

def bench():
    """35 min synthetic: frame labels + shots vs the prototype (research table: confirm 0.8 / min 2 s gives
    93.4% right speaker, 0 shots < 1 s)."""
    if not _common.have(MEDIA_34M):
        return
    x = M.load_audio(MEDIA_34M)
    a, b, truth = synth(x)
    t0 = time.perf_counter()
    lab, thr = P.frame_owner([M.rms_db(a), M.rms_db(b)])
    t1 = time.perf_counter()
    talk = truth >= 0
    n = min(len(lab), len(truth))
    lab, truth, talk = lab[:n], truth[:n], talk[:n]
    print(f"  {n * M.HOP:.0f} s; labels in {t1 - t0:.2f} s; labelled acc {np.mean(lab[talk & (lab >= 0)] == truth[talk & (lab >= 0)]) * 100:.1f}%"
          f", unlabelled {np.mean(lab[talk] < 0) * 100:.1f}%  thr {thr}")
    for cfg in [dict(confirm=0.0, min_shot=0.5, max_shot=0, react=False, cross_wide=False, silence_wide=False),
                dict(confirm=0.8, min_shot=2.0, max_shot=0, react=False, cross_wide=False, silence_wide=False),
                dict(preset="normal"), dict(preset="normal", react=False), dict(preset="tenang"), dict(preset="dinamis")]:
        o = P.options(cfg)
        for wide in (False, True):
            sh = P.plan_shots(lab, 2, wide, o, 0.0, n * M.HOP)
            cam = np.full(n, -9)
            for s, e, c, _ in sh:
                cam[int(round(s / M.HOP)):int(round(e / M.HOP))] = c
            lens = np.array([e - s for s, e, *_ in sh])
            right = np.mean(cam[talk] == truth[talk]) * 100
            print(f"  {cfg} wide={wide}: {len(sh)} shots, <1s {int((lens < 1).sum())}, median {np.median(lens):.1f}s,"
                  f" right {right:.1f}%, wide {np.mean(cam[talk] == 2) * 100:.1f}%")


# ---------------------------------------------------------------- main

if "--make" in sys.argv:
    out = Path(sys.argv[sys.argv.index("--make") + 1])
    files, truth, dur = make_fixture(out)
    print(json.dumps({k: str(v) for k, v in files.items()} | {"duration": round(dur, 3)}, indent=1))
    sys.exit(0)

check_shot_rules()
check_cutaways()
check_offset()
check_plan()
check_texts()

if _common.have(MEDIA_2M):
    tmp = _common.scratch("podcast")
    t_start = time.perf_counter()
    files, truth, dur = make_fixture(tmp / "mics")
    seq = seq_json(dur, files["a"], files["b"])
    job = {"tool": "podcast", "action": "analyze", "seq": seq, "workdir": str(tmp / "work"),
           "params": {"speakers": SPEAKERS, "wide": 0, "preset": "normal", "scope": {"kind": "all"}}}
    res, em = run(job)
    doc = RV.load(res["review"])
    items = doc["items"]
    assert doc["tool"] == "podcast" and doc["timebase"] == "sequence" and not RV.validate(doc)
    assert abs(items[0]["t0"]) < 1e-6 and abs(items[-1]["t1"] - doc["scope"][1]) < 1e-6
    for x, y in zip(items, items[1:]):
        assert abs(x["t1"] - y["t0"]) < 1e-6 and x["cam"] != y["cam"], (x, y)
        assert x["t1"] - x["t0"] >= 2.0 - 1e-6, x                    # min shot (last shot folded too)
        assert abs(x["t1"] * 120 - round(x["t1"] * 120)) < 1e-3      # frame aligned at 120 fps
    acc, wrong = accuracy(items, truth)
    st = res["stats"]
    assert acc >= 0.80 and wrong <= 0.05, (acc, wrong)            # measured 84,7% / 2,3% (rest = wide)
    assert st["switches"] >= 5 and st["wide_pct"] < 0.3, st
    assert {r["kind"] for r in res["sync"]} == {"mic", "cam"}
    for r in res["sync"]:
        assert r["ok"] and not r["warn"] and abs(r["offset"]) < 0.005, r
    assert set(doc["lanes"]) == {"0", "1", "x"} and len(doc["lanes"]["0"]) > 3
    assert [s["name"] for s in doc["speakers"]] == ["Andi", "Budi"] and doc["wide"] == 0
    assert [c["track"] for c in doc["cams"]] == [1, 2, 0]
    kinds = [e.get("stage") for e in em.items if e.get("ev") == "stage"]
    assert kinds == ["audio", "owner", "sync", "shots"], kinds
    print(f"  analyze ok: {len(items)} shots, {st['switches']} switches, right speaker {acc * 100:.1f}%, wrong {wrong * 100:.1f}%, "
          f"wide {st['wide_pct'] * 100:.0f}%, owned {st['owned'] * 100:.0f}%, {time.perf_counter() - t_start:.1f} s")

    # apply: one row off (keeps the previous camera), one camera picked by hand (panel rowAction)
    k_off = next(i for i, it in enumerate(items) if i > 0 and it["kind"] == "spk")
    items[k_off]["on"], items[k_off]["touched"] = False, True
    k_pick = next(i for i, it in enumerate(items) if i > k_off + 1)
    items[k_pick]["cam"], items[k_pick]["pick"], items[k_pick]["label"] = 0, True, "Wide"
    RV.save(doc, res["review"])
    em = ListEmitter()
    out = P.apply({"tool": "podcast", "action": "apply", "review": res["review"]}, em)
    plan = out["plan"]
    assert plan["kind"] == "multicam" and plan["cams"] == [1, 2, 0]
    for x, y in zip(plan["shots"], plan["shots"][1:]):
        assert abs(x[1] - y[0]) < 1e-9 and x[2] != y[2]
    at = lambda t: next(s[2] for s in plan["shots"] if s[0] <= t < s[1])   # noqa: E731
    mid_off = (items[k_off]["t0"] + items[k_off]["t1"]) / 2
    assert at(mid_off) == at(items[k_off]["t0"] - 0.01), "row switched off must keep the previous camera"
    assert at((items[k_pick]["t0"] + items[k_pick]["t1"]) / 2) == 0
    assert all(set(c[1]) <= set(plan["cams"]) and len(c[1]) == 2 for c in plan["cuts"])
    assert len(plan["cuts"]) == len(plan["shots"]) - 1
    print(f"  apply ok: {len(plan['shots'])} shots, {sum(len(c[1]) for c in plan['cuts'])} razor cuts")

    # re-run keeps the manual choices (carry over by stable id)
    res2, _ = run(job)
    doc2 = RV.load(res2["review"])
    by = {it["id"]: it for it in doc2["items"]}
    assert by[items[k_off]["id"]]["on"] is False and by[items[k_pick]["id"]]["cam"] == 0, "carry over"
    assert res2["kept"] == 2, res2["kept"]

    # one stereo file on both mic tracks (A = left on A1, B = right on A2): split per channel
    job_lr = dict(job, seq=seq_json(dur, files["lr"], files["lr"]), workdir=str(tmp / "work_lr"))
    res_lr, em_lr = run(job_lr)
    acc_lr, wrong_lr = accuracy(RV.load(res_lr["review"])["items"], truth)
    assert acc_lr >= 0.80 and wrong_lr <= 0.05 and any("kanal" in n for n in res_lr["notes"]), (acc_lr, res_lr["notes"])
    assert any(r.get("note") for r in res_lr["sync"] if r["kind"] == "mic")
    print(f"  stereo split ok: right speaker {acc_lr * 100:.1f}%")

    # mic B 0,2 s late: the sync check flags it (and the switching still works)
    job_late = dict(job, seq=seq_json(dur, files["a"], files["late"]), workdir=str(tmp / "work_late"))
    res_late, em_late = run(job_late)
    r = next(r for r in res_late["sync"] if r["kind"] == "mic")
    assert r["warn"] and abs(r["offset"] - 0.2) < 0.005 and r["psr"] >= 5, r
    warns = [e["msg"] for e in em_late.items if e.get("ev") == "warn"]
    assert any("Mic Budi telat 0,20 dtk" in w for w in warns), warns
    print(f"  sync ok: mic Budi {r['offset']:+.3f} s (PSR {r['psr']})")

    # In/Out scope: shots only inside, every camera razored at the edges
    job_io = dict(job, workdir=str(tmp / "work_io"), params=dict(job["params"], scope={"kind": "inout", "t0": 20.0, "t1": 80.0}))
    res_io, _ = run(job_io)
    d_io = RV.load(res_io["review"])
    assert abs(d_io["items"][0]["t0"] - 20.0) < 1e-6 and abs(d_io["items"][-1]["t1"] - 80.0) < 1e-6
    plan_io = P.apply({"review": res_io["review"]}, ListEmitter())["plan"]
    assert plan_io["cuts"][0] == [20.0, [0, 1, 2]] and plan_io["cuts"][-1] == [80.0, [0, 1, 2]], plan_io["cuts"]
    print("  in/out scope ok")

    check_errors(seq)
    _common.cleanup("podcast")

if "--bench" in sys.argv:
    bench()
print("ok")
