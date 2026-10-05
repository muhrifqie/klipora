"""ac.media: probe, audio, envelope (+cache), Otsu, gate/runs, snapping, downsample, frame grab.
Uses the 49 s test file (and the 34.6 min file for the timing line when present)."""
import time

import numpy as np

import _common
from _common import MEDIA_34M, MEDIA_49

from ac import media as M
from ac.util import EngineError

# ---- pure helpers on synthetic data
sr = M.SR
t = np.arange(sr * 3) / sr
x = np.zeros_like(t, dtype=np.float32)
x[sr:2 * sr] = 0.1 * np.sin(2 * np.pi * 220 * t[sr:2 * sr])          # 1 s tone between 1 and 2 s
db = M.rms_db(x)
assert len(db) == 300 and db.dtype == np.float32
assert db[50] == M.FLOOR_DB and -24 < db[150] < -22, (db[50], db[150])          # 0.1 amp sine ~ -23 dBFS
sp = M.voiced_spans(db, thr=-50)
assert len(sp) == 1 and abs(sp[0][0] - 0.99) < 0.03 and abs(sp[0][1] - 2.01) < 0.03, sp
assert M.first_audible(db, 0.5, 1.5, -50) == 0.99 and M.last_audible(db, 1.5, 2.5, -50) == 2.01
assert M.snap_quiet(db, 1.0, radius=0.05) < 0.99
assert M.runs(np.array([1, 1, 0, 1, 0, 0, 0, 1], bool), hop=1.0, min_gap=2) == [[0.0, 4.0], [7.0, 8.0]]
bim = np.r_[np.full(600, -88.0), np.full(400, -27.0)]
assert -70 < M.otsu_db(bim) < -40
assert M.otsu_db(np.full(100, -90.0)) <= -20                 # unimodal fallback stays sane
g = M.gate(np.array([-60, -52, -45, -52, -60, -52, -60.0]), thr=-48, hyst=4)
assert g.tolist() == [False, True, True, True, False, False, False], g
ds = M.downsample(np.arange(10000, dtype=np.float32), bars=100)
assert len(ds) == 100 and ds[0] == 99.0 and ds[-1] == 9999.0

# ---- synthetic clip (FFmpeg lavfi, no personal media needed): 12 s, 640x360 @ 30 fps, tone 1.5 s on / 0.8 s off
syn = _common.synth_clip()
if syn is not None:
    si = M.probe(syn)
    assert si["has_video"] and si["has_audio"] and (si["width"], si["height"]) == (640, 360), si
    assert abs(si["fps"] - 30) < 0.01 and 11.9 < si["duration"] < 12.1, si
    sa = M.load_audio(syn)
    assert sa.dtype == np.float32 and abs(len(sa) / M.SR - 12.0) < 0.1
    se = M.envelope(syn, audio=sa, refresh=True)
    assert np.array_equal(se, M.envelope(syn)) and len(se) == len(sa) // 160
    sthr = M.otsu_db(se)
    ss = M.voiced_spans(se, sthr)
    assert len(ss) == 6 and all(abs((b - a) - 1.5) < 0.1 for a, b in ss[:5]), ss       # bursts at 0, 2.3, 4.6 ...
    assert abs(ss[1][0] - 2.3) < 0.05, ss
    sf = M.frame_grab(syn, 3.0, width=320)
    assert sf.shape == (180, 320, 3) and sf.std() > 5
    print("synthetic clip ok")
else:
    print("skip: synthetic clip (FFmpeg not found)")

if not _common.have(MEDIA_49):
    raise SystemExit(0)

# ---- probe
info = M.probe(MEDIA_49)
assert info["has_video"] and info["has_audio"] and info["width"] == 2292 and info["height"] == 960, info
assert abs(info["fps"] - 120) < 0.01 and 48 < info["duration"] < 50 and info["channels"] == 2

# ---- audio + envelope with cache
t0 = time.time()
audio = M.load_audio(MEDIA_49)
assert audio.dtype == np.float32 and abs(len(audio) / M.SR - info["duration"]) < 0.1
env = M.envelope(MEDIA_49, audio=audio, refresh=True)
t_build = time.time() - t0
npy, meta = M._env_paths(str(MEDIA_49), None)
assert npy.is_file() and meta.is_file()
t0 = time.time()
env2 = M.envelope(MEDIA_49)
t_cache = time.time() - t0
assert np.array_equal(env, env2) and t_cache < 0.2, t_cache
assert len(env) == len(audio) // 160 and env.min() >= M.FLOOR_DB
thr = M.otsu_db(env)
assert -56 <= thr <= -49, thr                                 # measured -52.5 dB on this file
win = M.load_audio(MEDIA_49, start=10.0, dur=2.0)
assert abs(len(win) - 2 * M.SR) < 400
spans = M.voiced_spans(env, thr)
assert 5 < len(spans) < 80

# ---- frame grab: ndarray (scaled) and PNG
fr = M.frame_grab(MEDIA_49, 12.0, width=480)
assert fr.shape == (202, 480, 3) and fr.dtype == np.uint8 and fr.std() > 5, fr.shape
tmp = _common.scratch("media")
png = M.frame_grab(MEDIA_49, 48.9, out=tmp / "f.png")       # near the end: clamped, still a frame
assert png.is_file() and png.stat().st_size > 1000
full = M.frame_grab(MEDIA_49, 0.0)
assert full.shape == (960, 2292, 3)
try:
    M.probe(tmp / "missing.mp4")
    raise AssertionError("probe should raise")
except EngineError as e:
    assert e.code == "NO_MEDIA"
_common.cleanup("media")

msg = f"49 s: envelope built {t_build:.2f} s, cached {t_cache * 1000:.0f} ms, otsu {thr:.1f} dB"
if MEDIA_34M.is_file():
    t0 = time.time()
    a34 = M.load_audio(MEDIA_34M)
    t_dec = time.time() - t0
    t0 = time.time()
    e34 = M.rms_db(a34)
    t_env = time.time() - t0
    del a34
    t0 = time.time()
    c34 = M.envelope(MEDIA_34M)
    t_c = time.time() - t0
    assert len(c34) == len(e34) and abs(M.otsu_db(c34) + 53.5) <= 1.0
    msg += f"; 34.6 min: decode {t_dec:.2f} s + envelope {t_env:.2f} s, cache load {t_c * 1000:.0f} ms"
print(msg)
print("ok")
