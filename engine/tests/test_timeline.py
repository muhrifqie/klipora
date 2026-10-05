"""ac.timeline: parsing (SPEC section 3 + tlSeqInfo-style keys), queries, mapping, words/envelope on timeline."""
import numpy as np

import _common
from _common import MEDIA_2M, MEDIA_49

from ac import media as M
from ac import transcript as T
from ac.timeline import Timeline
from ac.util import EngineError

A, B = str(MEDIA_49), str(MEDIA_2M)
seq = {
    "id": "s1", "name": "Tes", "fps": 120, "timebase": "2116800000", "width": 2292, "height": 960, "duration": 30.0,
    "inPoint": None, "outPoint": "-400000", "player": 3.5,
    "video": [
        {"index": 0, "name": "V1", "muted": False, "locked": False, "clips": [
            {"name": "a", "path": A, "start": 0, "end": 10, "in": 5, "out": 15, "selected": True, "nodeId": "n1"},
            {"name": "a", "path": A, "start": 10, "end": 20, "in": 30, "out": 40}]},
        {"index": 1, "name": "V2", "clips": [{"name": "g", "path": "", "start": 2, "end": 4, "in": 0, "out": 2}]},
    ],
    "audio": [
        {"index": 0, "name": "A1", "clips": [
            {"name": "a", "path": A, "start": 0, "end": 10, "in": 5, "out": 15},
            {"name": "a", "path": A, "start": 10, "end": 20, "in": 30, "out": 40}]},
        {"idx": 1, "name": "A2", "muted": False, "clips": [   # stereo twin of A1 (duplicate words must collapse)
            {"name": "a", "media": A, "start": 0, "end": 10, "inPoint": 5, "outPoint": 15}]},
        {"index": 2, "name": "Musik", "muted": True, "clips": [
            {"name": "b", "path": B, "start": 0, "end": 30, "in": 0, "out": 30}]},
        {"index": 3, "name": "A4", "clips": [
            {"name": "b2", "path": B, "start": 20, "end": 30, "in": 60, "out": 80, "speed": 2.0, "disabled": False}]},
    ],
    "markers": [{"t": 1.0, "name": "m"}],
}
tl = Timeline.from_json(seq)
assert tl.fps == 120 and tl.duration == 30.0 and tl.in_point is None and tl.out_point is None and tl.player == 3.5
assert [t.name for t in tl.audio] == ["A1", "A2", "Musik", "A4"] and tl.audio[1].index == 1
assert len(tl.audio_clips()) == 4                       # muted "Musik" excluded
assert len(tl.audio_clips(include_muted=True)) == 5
assert tl.audio[1].clips[0].src_in == 5 and tl.audio[1].clips[0].path == A
c = tl.clip_at(12.0)
assert c.track == 0 and c.src_in == 30 and abs(c.seq_to_src(12.0) - 32.0) < 1e-9 and abs(c.src_to_seq(35) - 15) < 1e-9
assert tl.clip_at(3.0).track == 1                        # topmost video wins
assert tl.clip_at(25.0) is None and tl.clip_at(25.0, "audio").track == 2   # muted tracks still hold clips
assert tl.clip_at(25.0, "audio", tracks=[3]).track == 3
fast = tl.audio[3].clips[0]
assert fast.seq_to_src(21) == 62 and fast.src_to_seq(70) == 25   # speed 2
assert tl.scope_ranges("all") == [[0.0, 30.0]]
assert tl.scope_ranges("inout") == [[0.0, 30.0]]         # unset in/out -> whole sequence
assert tl.scope_ranges("selected") == [[0.0, 10.0]]
tl2 = Timeline.from_json({**seq, "inPoint": 4.0, "outPoint": 9.0})
assert tl2.scope_ranges("inout") == [[4.0, 9.0]]
# panel params.scope dicts (source card): {kind, t0, t1}; t0/t1 win, None/unknown = all
assert tl.scope_ranges(None) == [[0.0, 30.0]] and tl.scope_ranges({"kind": "all", "t0": 0, "t1": 30}) == [[0.0, 30.0]]
assert tl.scope_ranges({"kind": "inout", "t0": 2.5, "t1": 7.0}) == [[2.5, 7.0]]
assert tl2.scope_ranges({"kind": "inout"}) == [[4.0, 9.0]]
assert tl.scope_ranges({"kind": "selected"}) == [[0.0, 10.0]]
no_flags = Timeline.from_json({**seq, "video": [{"index": 0, "clips": [{"path": A, "start": 0, "end": 10, "in": 0, "out": 10}]}],
                               "selection": [{"name": "a", "kind": "video", "track": 0, "start": 12.0, "end": 14.5}]})
assert no_flags.scope_ranges({"kind": "selected"}) == [[12.0, 14.5]]
assert tl.media_paths() == [A, B]
assert Timeline.from_json(tl.to_json()).to_json() == tl.to_json()
try:
    Timeline.from_json({"name": "kosong"})
    raise AssertionError("empty seq should raise")
except EngineError as e:
    assert e.code == "NO_SEQ"

# tlSeqInfo-style input (lab helper keys) also works
lab = {"name": "L", "id": "x", "fps": 120, "endSec": 12.0, "inSec": -400000, "outSec": -400000, "playerSec": 1.0,
       "video": [{"idx": 0, "name": "V1", "clips": [{"name": "a", "start": 0, "end": 12, "inPoint": 1, "outPoint": 13,
                                                      "media": A, "speed": 1, "disabled": False}]}], "audio": []}
lt = Timeline.from_json(lab)
assert lt.duration == 12.0 and lt.in_point is None and lt.video[0].clips[0].src_in == 1

# ---- from_media + envelope on timeline with the synthetic clip (no personal media needed)
syn = _common.synth_clip()
if syn is not None:
    sm = Timeline.from_media(syn)
    assert len(sm.video) == 1 and len(sm.audio) == 1 and abs(sm.duration - M.probe(syn)["duration"]) < 1e-6
    assert sm.video[0].clips[0].src_in == 0 and sm.fps > 0
    print("synthetic from_media ok")

if not _common.have(MEDIA_49, MEDIA_2M) or T.cached_words(MEDIA_49) is None:
    raise SystemExit(0)

# ---- words on timeline (cache only, no GPU)
words = tl.words_on_timeline(transcribe=False)
src = T.cached_words(MEDIA_49)
assert words and all(0 <= w["t0"] < w["t1"] <= 30.0 + 1e-6 for w in words)
ids = [w["id"] for w in words]
assert len(ids) == len(set(ids)), "stereo twin A2 must not duplicate words"
for w in words:            # every word maps back to its source row
    row = src[int(w["id"].split(":")[1])] if w["src"] == A else T.cached_words(MEDIA_2M)[int(w["id"].split(":")[1])]
    assert row[0] == w["text"] and abs(row[1] - w["s0"]) < 1e-9
first = [w for w in words if w["src"] == A and w["t0"] < 10]
assert all(5 <= (w["s0"] + w["s1"]) / 2 < 15 for w in first)
assert any(w["clip_end"] for w in words)
assert all(a["t0"] <= b["t0"] for a, b in zip(words, words[1:]))
b_words = [w for w in words if w["src"] == B]
assert b_words and all(20 <= w["t0"] <= 30 for w in b_words) and all(60 <= (w["s0"] + w["s1"]) / 2 < 80 for w in b_words)

# ---- envelope on timeline equals the source envelope shifted (single clip), max over tracks
env_a = M.envelope(MEDIA_49)
single = Timeline.from_json({"name": "x", "fps": 120, "duration": 12.0, "video": [], "audio": [
    {"index": 0, "name": "A1", "clips": [{"name": "a", "path": A, "start": 2.0, "end": 12.0, "in": 5.0, "out": 15.0}]}]})
ev = single.envelope_on_timeline()
assert len(ev) == 1200 and np.all(ev[:200] == M.FLOOR_DB)
assert np.allclose(ev[200:1200], env_a[500:1500]), "shifted copy expected"
full = tl.envelope_on_timeline()
assert len(full) == 3000 and full.max() > -40 and np.all(full >= M.FLOOR_DB)
only_a4 = tl.envelope_on_timeline(tracks=[3])
assert np.all(only_a4[:2000] == M.FLOOR_DB) and only_a4[2000:].max() > -60

# ---- from_media: one clip on V1/A1 covering the file
fm = Timeline.from_media(MEDIA_49)
assert len(fm.video) == 1 and len(fm.audio) == 1 and abs(fm.duration - M.probe(MEDIA_49)["duration"]) < 1e-6
assert len(fm.words_on_timeline(transcribe=False)) == len(src)
print(f"ok ({len(words)} timeline words)")
