"""ac.tools.silence: settings, the pure detector (synthetic envelopes), integer-threshold equivalence with the
floored envelope the panel uses, and preview/analyze/apply on the real test media (cached envelopes and
transcripts only: no GPU, no ffmpeg decode when the caches exist)."""
import base64
import math

import numpy as np

import _common
from _common import MEDIA_2M, MEDIA_34M, MEDIA_49

from ac import i18n
from ac import media as M
from ac import ranges as R
from ac import review as RV
from ac.progress import ListEmitter, NullEmitter
from ac.timeline import Timeline
from ac.tools import silence as S
from ac.util import EngineError, write_json

# ---------------------------------------------------------------- settings
s = S.settings({})
assert s["preset"] == "natural" and s["min_silence"] == 0.5 and s["pad_before"] == 0.06 and s["pad_after"] == 0.15
assert s["min_talk"] == 0.15 and s["offset"] == 0 and s["threshold"] is None and s["guard"] and s["mode"] == "remove"
k = S.settings({"preset": "kilat", "pad_after": 0.3, "offset": 4.4, "guard": False, "mode": "mute", "min_silence": 99})
assert k["preset"] == "custom" and k["min_talk"] == 0.10 and k["pad_after"] == 0.3 and k["offset"] == 4
assert k["guard"] is False and k["mode"] == "mute" and k["min_silence"] == 5.0
assert S.settings({"preset": "nope", "mode": "x", "threshold": -47.6})["threshold"] == -48
assert S.settings({"offset": -99})["offset"] == -30
assert set(S.PRESETS) == {"santai", "natural", "cepat", "kilat"} and S.settings({"preset": "cepat"})["preset"] == "cepat"

# ---------------------------------------------------------------- synthetic detector


def env(segs, n, floor=-88.0):
    """Envelope with dB levels per [t0, t1, db] (10 ms frames)."""
    e = np.full(n, floor, np.float32)
    for a, b, v in segs:
        e[S.fidx(a):S.fidx(b)] = v
    return e


nat = S.settings({})
# speech 1-3 s and 4-6 s, silence elsewhere (10 s): cuts [0, 0.94), [3.15, 3.94), [6.15, 10)
e = env([(1, 3, -25), (4, 6, -25)], 1000)
r = S.plan(e, -50, nat, dur=10.0, fps=100)
assert r["cuts"] == [[0.0, 0.94], [3.15, 3.94], [6.15, 10.0]], r["cuts"]
# pause shorter than min_silence (0.4 s < 0.5 s) is bridged
r = S.plan(env([(1, 3, -25), (3.4, 6, -25)], 1000), -50, nat, dur=10.0, fps=100)
assert r["cuts"] == [[0.0, 0.94], [6.15, 10.0]], r["cuts"]
# hysteresis: a -52 dB tail (thr-4 < -52 < thr) attached to speech is kept, a lone -52 dB murmur is not
r = S.plan(env([(1, 3, -25), (3, 3.5, -52), (7, 7.5, -52)], 1000), -50, nat, dur=10.0, fps=100)
assert r["cuts"] == [[0.0, 0.94], [3.65, 10.0]], r["cuts"]
# min_talk: a 0.1 s click is dropped (< 0.15 s) ...
click = env([(1, 3, -25), (6, 6.1, -30)], 1000)
assert S.plan(click, -50, nat, dur=10.0, fps=100)["cuts"] == [[0.0, 0.94], [3.15, 10.0]]
# ... unless a transcript word covers it (word guard keeps short talks that hold a word)
r = S.plan(click, -50, nat, words=[[6.0, 6.1]], dur=10.0, fps=100)
assert r["cuts"] == [[0.0, 0.94], [3.15, 5.91], [6.28, 10.0]], r["cuts"]   # word frames dilated by 3
assert S.plan(click, -50, nat, words=[[6.0, 6.1]], dur=10.0, fps=100, guard=False)["cuts"] == [[0.0, 0.94], [3.15, 10.0]]
# word guard: a quiet word at -55 dB (below thr, above thr-6) is protected; at -60 dB it is not audible enough
for level, kept in ((-55, True), (-60, False)):
    q = env([(1, 3, -25), (5, 5.4, level)], 1000)
    cuts = S.plan(q, -50, nat, words=[[5.0, 5.4]], dur=10.0, fps=100)["cuts"]
    assert (not R.contains(cuts, 5.2)) == kept, (level, cuts)
# protected spans (offline clips, video-only) are never cut
assert not R.contains(S.plan(e, -50, nat, protect=[[7, 8]], dur=10.0, fps=100)["cuts"], 7.5)
# scope: only inside [2, 8)
r = S.plan(e, -50, nat, scope=[[2.0, 8.0]], dur=10.0, fps=100)
assert r["cuts"] == [[3.15, 3.94], [6.15, 8.0]], r["cuts"]
# frame snapping is inward (no kept frame eaten) and removals < 0.05 s are dropped
r = S.plan(e, -50, nat, dur=10.0, fps=24)
assert all(abs(a * 24 - round(a * 24)) < 1e-6 and abs(b * 24 - round(b * 24)) < 1e-6 for a, b in r["cuts"])
assert r["cuts"][1][0] >= 3.15 - 1e-9 and r["cuts"][1][1] <= 3.94 + 1e-9
tiny = S.settings({"min_silence": 0.25, "pad_before": 0.1, "pad_after": 0.12})
assert S.plan(env([(1, 3, -25), (3.26, 6, -25)], 1000), -50, tiny, dur=10.0, fps=100)["cuts"][1] == [6.12, 10.0]
# silence only where ALL tracks are silent: the max envelope of two tracks
a1, a2 = env([(1, 3, -25)], 1000), env([(5, 7, -25)], 1000)
both = S.plan(np.maximum(a1, a2), -50, nat, dur=10.0, fps=100)["cuts"]
assert not R.contains(both, 2.0) and not R.contains(both, 6.0) and R.contains(both, 4.0)

# ---------------------------------------------------------------- real media
from ac import transcript as T  # noqa: E402

if not _common.have(MEDIA_49, MEDIA_2M):
    raise SystemExit(0)
if not (T.has_words(MEDIA_49) and T.has_words(MEDIA_2M)):
    print("SKIP real-media part: transcript caches missing (python engine/cli.py transcribe <media>)")
    raise SystemExit(0)

tmp = _common.scratch("silence")
A, B = str(MEDIA_49), str(MEDIA_2M)
tl49 = Timeline.from_media(A)
db49 = tl49.envelope_on_timeline()            # sequence time: ceil(duration / 10 ms) frames

# integer threshold => plan(db) == plan(floor(db)): what makes the panel's live detector exact
words49 = [[w["t0"], w["t1"]] for w in tl49.words_on_timeline(transcribe=False)]
for media in (A, B) + ((str(MEDIA_34M),) if MEDIA_34M.is_file() else ()):
    tl = Timeline.from_media(media)
    db = tl.envelope_on_timeline()
    ws = [[w["t0"], w["t1"]] for w in tl.words_on_timeline(transcribe=False)]
    for pr in S.PRESETS:
        for off in (-6, 0, 5):
            thr = math.floor(M.otsu_db(db)) + off
            cfg = S.settings({"preset": pr})
            p1 = S.plan(db, thr, cfg, words=ws, dur=tl.duration, fps=tl.fps)["cuts"]
            p2 = S.plan(np.floor(db), thr, cfg, words=ws, dur=tl.duration, fps=tl.fps)["cuts"]
            assert p1 == p2, (media, pr, off)

# preview: floored envelope round trip, auto threshold, check count
pv = S.preview({"seq": tl49.to_json(), "params": {}}, ListEmitter())
env_u8 = np.frombuffer(base64.b64decode(pv["env"]), np.uint8)
assert len(env_u8) == pv["n"] == len(db49)
assert np.array_equal(env_u8.astype(int) - 90, np.clip(np.floor(db49), -90, 0).astype(int))
assert pv["auto"] == -52.5 and pv["base"] == -53 and pv["thr"] == -53 and pv["fps"] == tl49.fps
assert pv["has_words"] and len(pv["words"]) == len(words49) and pv["scope"] == [[0.0, tl49.duration]]
assert pv["presets"] == S.PRESETS and pv["tracks"][0]["used"] and pv["check"]["n"] > 0

# analyze: review file, 0 audible words lost with the guard, apply plans
job = {"tool": "silence", "action": "analyze", "seq": tl49.to_json(), "params": {}, "workdir": str(tmp)}
em = ListEmitter()
res = S.analyze(job, em)
stages = [x["id"] for x in em.items if x["ev"] == "stage"]
assert "error" not in em.kinds() and stages == ["audio", "words", "detect", "review"], stages
doc = RV.load(res["review"])
assert doc["tool"] == "silence" and doc["timebase"] == "sequence" and not RV.validate(doc)
items = doc["items"]
assert len(items) == res["summary"]["n"] == pv["check"]["n"] and res["summary"]["n"] >= 5
assert abs(res["summary"]["sec_on"] - pv["check"]["sec"]) < 0.01
assert all(0 <= it["t0"] < it["t1"] <= tl49.duration + 1e-6 and it["kind"] == "gap" and it["label"].endswith("dtk diam")
           for it in items)
assert all(isinstance(it.get("ctx"), dict) for it in items) and any(it["ctx"]["pre"] for it in items)
cuts = [it["cut"] for it in items]
loss = S._audible_loss(words49, db49, res["stats"]["thr"], cuts)
assert sum(1 for x in loss if x is not None and x > 0.05) == 0, "word guard must keep every audible word whole"
assert res["stats"]["thr"] == -53 and res["stats"]["has_words"] and res["stats"]["lost"] == 0
for pr in ("cepat", "kilat"):            # faster presets: the guard actually moves boundaries
    r2 = S.analyze({**job, "params": {"preset": pr}}, NullEmitter())
    l2 = S._audible_loss(words49, db49, r2["stats"]["thr"], [it["cut"] for it in RV.load(r2["review"])["items"]])
    assert sum(1 for x in l2 if x is not None and x > 0.05) == 0, pr
off = S.analyze({**job, "params": {"preset": "kilat", "guard": False}}, NullEmitter())
assert off["stats"]["protected"] == 0 and off["summary"]["n"] >= RV.load(r2["review"])["stats"]["n"]

# re-run keeps the user's manual toggles (touched) for the same ids
res = S.analyze(job, NullEmitter())
doc = RV.load(res["review"])
doc["items"][0]["on"], doc["items"][0]["touched"] = False, True
write_json(res["review"], doc)
res2 = S.analyze(job, NullEmitter())
doc2 = RV.load(res2["review"])
assert doc2["items"][0]["on"] is False and doc2["stats"]["carried"] == 1 and res2["summary"]["on"] == len(items) - 1

aj = {"tool": "silence", "action": "apply", "seq": None, "params": {}, "review": res2["review"], "workdir": str(tmp)}
ap = S.apply(aj, ListEmitter())
assert ap["plan"]["kind"] == "remove_ranges" and ap["plan"]["timebase"] == "sequence" and ap["mode"] == "remove"
assert len(ap["plan"]["ranges"]) == len(items) - 1 and ap["plan"]["seq"]["id"] == tl49.id
assert abs(ap["stats"]["after"] - (tl49.duration - ap["stats"]["sec"])) < 1e-6
mk = S.apply({**aj, "params": {"mode": "markers"}}, ListEmitter())["plan"]
assert mk["kind"] == "markers" and mk["tag"] == "[Klipora-SIL]" and len(mk["markers"]) == len(items) - 1
assert all(m["tag"] == "[Klipora-SIL]" and m["end"] > m["t"] and m["name"].startswith("Jeda ") for m in mk["markers"])
assert ap["summary"].endswith("dtk") and " jeda dibuang, " in ap["summary"], ap["summary"]
# English job: labels, marker names, notes and the summary follow the job language
with i18n.using("en"):
    ren = S.analyze(job, NullEmitter())
    en_items = RV.load(ren["review"])["items"]
    assert en_items and all(it["label"].startswith("Pause ") and it["label"].endswith(" s") for it in en_items), en_items[0]
    assert all("dtk" not in it["label"] and "," not in it["label"] for it in en_items)
    mke = S.apply({**aj, "review": ren["review"], "params": {"mode": "markers"}}, ListEmitter())
    assert all(m["name"].startswith("Pause ") for m in mke["plan"]["markers"])
    assert " pauses marked, " in mke["summary"] and mke["summary"].endswith(" s"), mke["summary"]
res2 = S.analyze(job, NullEmitter())   # back to Indonesian for the checks below (same review file)
mu = S.apply({**aj, "params": {"mode": "mute"}}, ListEmitter())
assert mu["plan"]["kind"] == "mute_ranges" and mu["plan"]["tracks"] == [0] and mu["stats"]["after"] == mu["stats"]["before"]
try:
    S.apply({**aj, "review": str(tmp / "missing.json")}, NullEmitter())
    raise AssertionError("missing review must fail")
except EngineError as e:
    assert e.code == "BAD_REVIEW"

# In/Out scope: every cut inside [10, 30)
sc = S.analyze({**job, "params": {"scope": {"kind": "inout", "t0": 10.0, "t1": 30.0}}}, NullEmitter())
sci = RV.load(sc["review"])["items"]
assert sci and all(10.0 - 1e-6 <= it["t0"] and it["t1"] <= 30.0 + 1e-6 for it in sci)
assert sc["stats"]["scope"] == [[10.0, 30.0]]

# AutoCut sequences (the 49 s file cut into butted clips): a re-run with the same preset finds nothing new,
# because the auto threshold comes from the source media (Otsu over the cut timeline would drift to -42 dB)
def cut_sequence(cuts):
    t, vclips, aclips = 0.0, [], []
    for k0, k1 in R.invert(cuts, 0.0, tl49.duration):
        c = {"name": "x.mp4", "path": A, "start": t, "end": t + (k1 - k0), "in": k0, "out": k1}
        vclips.append(dict(c))
        aclips.append(dict(c))
        t += k1 - k0
    return {"id": "cut", "name": "Cut (AutoCut)", "fps": 120, "duration": t,
            "video": [{"index": 0, "name": "V1", "clips": vclips}], "audio": [{"index": 0, "name": "A1", "clips": aclips}]}


for pr in ("natural", "kilat"):
    first = S.analyze({**job, "params": {"preset": pr}}, NullEmitter())
    cut_seq = cut_sequence([it["cut"] for it in RV.load(first["review"])["items"]])
    rc = S.analyze({**job, "seq": cut_seq, "params": {"preset": pr}}, NullEmitter())
    assert len(cut_seq["audio"][0]["clips"]) >= 7 and rc["stats"]["thr"] == first["stats"]["thr"]
    assert rc["summary"]["n"] == 0 and rc["stats"]["lost"] == 0, (pr, rc["summary"])
    wc = Timeline.from_json(cut_seq).words_on_timeline(transcribe=False)
    # word spans stretch into pauses, so a few midpoints of fully audible words land in removed time (Kilat)
    assert len(wc) >= len(words49) - (1 if pr == "natural" else 5), (pr, len(wc))
    if pr == "natural":                   # a faster preset on the Natural cut still works clip by clip
        fast = S.analyze({**job, "seq": cut_seq, "params": {"preset": "kilat"}}, NullEmitter())
        fi = RV.load(fast["review"])["items"]
        assert fast["summary"]["n"] > 0 and fast["stats"]["lost"] == 0
        assert all(it["cut"][1] <= cut_seq["duration"] + 1e-9 for it in fi)

# 34,6 min, Kilat cut into 717 clips (+ a stereo twin on A2): the re-run is still idempotent. Needs guard words
# mapped by OVERLAP: with the midpoint rule 211 stretched words drop off the cut timeline and 37 gaps reappear.
if MEDIA_34M.is_file():
    L = str(MEDIA_34M)
    tll = Timeline.from_media(L)
    fl = S.analyze({**job, "seq": tll.to_json(), "params": {"preset": "kilat"}}, NullEmitter())
    t, lclips = 0.0, []
    for k0, k1 in R.invert([it["cut"] for it in RV.load(fl["review"])["items"]], 0.0, tll.duration):
        lclips.append({"name": "l.mp4", "path": L, "start": t, "end": t + (k1 - k0), "in": k0, "out": k1})
        t += k1 - k0
    long_cut = {"id": "lc", "name": "Long (AutoCut)", "fps": 120, "duration": t,
                "video": [{"index": 0, "clips": [dict(c) for c in lclips]}],
                "audio": [{"index": 0, "clips": [dict(c) for c in lclips]}, {"index": 1, "clips": [dict(c) for c in lclips]}]}
    rl = S.analyze({**job, "seq": long_cut, "params": {"preset": "kilat"}}, NullEmitter())
    assert len(lclips) > 600 and rl["summary"]["n"] == 0 and rl["stats"]["thr"] == fl["stats"]["thr"], rl["summary"]

# multi-track: A1 = 49 s file, A2 = 2 min file, A3 muted music. Cut only where A1 AND A2 are silent.
def cl(path, start, end, src_in, name="c"):
    return {"name": name, "path": path, "start": start, "end": end, "in": src_in, "out": src_in + end - start}


multi = {"id": "m", "name": "Multi", "fps": 120, "duration": 40.0,
         "video": [{"index": 0, "name": "V1", "clips": [cl(A, 0, 40, 0)]}],
         "audio": [{"index": 0, "name": "A1", "clips": [cl(A, 0, 40, 0)]},
                   {"index": 1, "name": "A2", "clips": [cl(B, 0, 40, 50)]},
                   {"index": 2, "name": "Musik", "muted": True, "clips": [cl(B, 0, 40, 0)]}]}
tlm = Timeline.from_json(multi)
rm = S.analyze({**job, "seq": multi}, NullEmitter())
assert rm["stats"]["tracks"] == [0, 1] and rm["summary"]["n"] > 0
# with min_talk 0 (no click dropping) no removed frame may be loud on ANY included track
rm0 = S.analyze({**job, "seq": multi, "params": {"min_talk": 0}}, NullEmitter())
mcuts = [it["cut"] for it in RV.load(rm0["review"])["items"]]
thr_m = rm0["stats"]["thr"]
for tr in (0, 1):
    e_tr = tlm.envelope_on_timeline(tracks=[tr])
    for a, b in mcuts:
        assert e_tr[S.fidx(a):S.fidx(b)].max() < thr_m, (tr, a, b)
only1 = S.analyze({**job, "seq": multi, "params": {"tracks": [0]}}, NullEmitter())
assert only1["stats"]["tracks"] == [0] and only1["summary"]["sec_on"] > rm["summary"]["sec_on"]
with_music = S.analyze({**job, "seq": multi, "params": {"tracks": [0, 2]}}, NullEmitter())
assert with_music["stats"]["tracks"] == [0, 2]   # explicit choice includes the muted track

# protection: an offline clip on A1 and a video-only title are never cut
prot = {"id": "p", "name": "Prot", "fps": 120, "duration": 48.0,
        "video": [{"index": 0, "name": "V1", "clips": [cl(A, 0, 40, 0), cl(None, 40, 48, 0, "Judul")]}],
        "audio": [{"index": 0, "name": "A1", "clips": [cl(A, 0, 30, 0), cl(str(tmp / "offline.mp4"), 30, 40, 0, "gone")]}]}
rp = S.analyze({**job, "seq": prot}, ListEmitter())
pcuts = [it["cut"] for it in RV.load(rp["review"])["items"]]
assert rp["stats"]["unreadable"] == 1 and rp["stats"]["video_only"] == 1
assert not any(R.overlap(a, b, 30.0, 48.0) > 0 for a, b in pcuts), pcuts

# errors
video_only = {"id": "x", "name": "x", "duration": 10, "video": [{"index": 0, "clips": [cl(A, 0, 10, 0)]}],
              "audio": [{"index": 0, "clips": []}]}
offline = {"id": "x", "name": "x", "duration": 10, "audio": [{"index": 0, "clips": [cl(str(tmp / "no.mp4"), 0, 10, 0)]}]}
all_muted = {"id": "x", "name": "x", "duration": 10, "audio": [{"index": 0, "muted": True, "clips": [cl(A, 0, 10, 0)]}]}
# Premiere's default 3 audio tracks: A1 (the audio) muted, A2/A3 empty and unmuted -> same mute-specific message
muted_a1 = {"id": "x", "name": "x", "duration": 10, "audio": [{"index": 0, "name": "Audio 1", "muted": True,
            "clips": [cl(A, 0, 10, 0)]}, {"index": 1, "clips": []}, {"index": 2, "clips": []}]}
for bad, code, says in ((video_only, "NO_AUDIO", "kosong"), (offline, "NO_MEDIA", "offline"),
                        (all_muted, "NO_AUDIO", "di-mute"), (muted_a1, "NO_AUDIO", "di-mute (Audio 1)")):
    try:
        S.analyze({**job, "seq": bad}, NullEmitter())
        raise AssertionError("expected " + code)
    except EngineError as e:
        assert e.code == code and says in e.msg, (e.code, code, e.msg)

_common.cleanup("silence")
print(f"ok ({len(items)} jeda on 49 s Natural, Kilat {r2['summary']['n']} jeda / {r2['stats']['protected']} kata dilindungi, "
      f"thr {res['stats']['thr']} dB)")
