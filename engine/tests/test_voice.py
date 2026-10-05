"""Suara Jernih (tool id "voice"): settings, DeepFilterNet port, loudness, detectors, track choice, and the full
analyze -> apply -> preview/snippet/match_eq flow on the 49 s test clip (cached transcript, CPU only, ~30 s),
Klipora track names (the old "AutoCut Suara" is still recognised) and English job texts.
python engine/tests/test_voice.py"""
import json
import math
import re
import wave
from pathlib import Path

import numpy as np

import _common
from _common import MEDIA_2M, MEDIA_49

from ac import i18n
from ac import media as M
from ac import transcript as T
from ac.progress import ListEmitter
from ac.timeline import Timeline
from ac.tools import _voice_detect as VD
from ac.tools import _voice_df as DF
from ac.tools import _voice_dsp as V
from ac.tools import voice

tmp = _common.scratch("voice")
SR = 48000


def sisdr(y, c):
    y, c = np.asarray(y, np.float64), np.asarray(c, np.float64)
    a = np.dot(y, c) / np.dot(c, c)
    e = y - a * c
    return 10 * math.log10(np.dot(a * c, a * c) / np.dot(e, e))


# ---------------------------------------------------------------- settings + preset parity with the panel
s = voice.settings({})
assert s["preset"] == "natural" and s["lufs"] == -14.0 and s["tp"] == -1.0 and s["denoise"] == "ai", s
assert voice.settings({"platform": "podcast"})["lufs"] == -16.0
assert voice.settings({"platform": "custom", "lufs": -30})["lufs"] == -24.0          # clamped
assert voice.settings({"preset": "kuat", "presence": 1})["preset"] == "custom"
assert voice.settings({"preset": "kuat", "presence": 4.0})["preset"] == "kuat"
assert voice.settings({"clicks": False})["clicks"] is False
assert voice.settings({"eq": [[1000, 9], [5, 1], ["x", 2]]})["chain"]["eq"] == [[1000, 6.0]]
js = (_common.ROOT / "panel" / "js" / "tools" / "voice.js").read_text(encoding="utf-8")
m = re.search(r"var PRESETS = (\{.*?\});\n", js)
assert m and json.loads(m.group(1)) == V.PRESETS, "panel/js/tools/voice.js PRESETS differ from _voice_dsp.PRESETS"
f = V.chain_filters(V.PRESETS["podcast"], pre_gain=3.0)
assert f[0].startswith("highpass=f=90") and any(x.startswith("acompressor") for x in f) and any("deesser" in x for x in f)

# ---------------------------------------------------------------- DeepFilterNet port
assert sum(DF.WIDTHS) == DF.NFREQ and len(DF.WIDTHS) == 32 and min(DF.WIDTHS) >= 2
x = np.random.default_rng(0).standard_normal(SR * 2).astype(np.float32) * 0.1
xp = np.concatenate([np.zeros(DF.HOP, np.float32), x, np.zeros(SR, np.float32)])
assert np.abs(DF.istft_ola(DF.stft(xp, 0))[: len(x)] - xp[DF.HOP: DF.HOP + len(x)]).max() < 1e-5    # exact OLA
assert DF.available(), "DeepFilterNet3 ONNX files missing in engine/ac/assets/models/deepfilternet3"
if _common.have(MEDIA_49):
    clean = M.load_audio(MEDIA_49, sr=SR)[SR * 2: SR * 14]
    rng = np.random.default_rng(1)
    W = np.fft.rfft(rng.standard_normal(len(clean)))
    fr = np.fft.rfftfreq(len(clean), 1 / SR)
    pink = np.fft.irfft(W / np.sqrt(np.maximum(fr, 20)), len(clean))
    t = np.arange(len(clean)) / SR
    noise = pink / np.std(pink) + 0.3 * np.sin(2 * np.pi * 50 * t)
    k = np.sqrt(np.mean(clean.astype(np.float64) ** 2)) / np.sqrt(np.mean(noise ** 2)) / 10 ** (5 / 20)
    noisy = (clean + k * noise).astype(np.float32)
    y = DF.enhance(noisy, chunk_s=4.0, ctx_s=1.0)              # small chunks: exercises the chunk seams
    assert len(y) == len(noisy) and np.isfinite(y).all()
    g0, g1 = sisdr(noisy, clean), sisdr(y, clean)
    assert g1 - g0 > 4.0, f"DeepFilterNet improvement {g1 - g0:.1f} dB"
    assert V.lag(clean, y) == 0
    y2, used = V.denoise(noisy, "fft", 0.8)
    assert used == "fft" and len(y2) == len(noisy) and abs(V.lag(clean, y2)) <= 1
    if V.rnnoise_ok():
        y3, used = V.denoise(noisy, "rnnoise", 1.0)
        assert used == "rnnoise" and abs(V.lag(clean, y3)) <= 1 and sisdr(y3, clean) > g0, sisdr(y3, clean)
    print(f"denoise SI-SDR at 5 dB SNR: noisy {g0:.1f}, DeepFilterNet {g1:.1f} dB")

# ---------------------------------------------------------------- loudness (BS.1770) vs FFmpeg ebur128
sine = (0.1 * np.sin(2 * np.pi * 997 * np.arange(SR * 5) / SR)).astype(np.float32)   # -20 dBFS peak, -23 dBFS RMS
I, _ = V.loudness(sine, stereo=False)
assert abs(I - (-23.0 - 0.691 + 0.691 + 0.0)) < 0.7, I                          # K-weighting ~ +0.66 dB at 1 kHz
assert abs(V.true_peak(sine) - (-20.0)) < 0.1
if _common.have(MEDIA_49):
    a49 = M.load_audio(MEDIA_49, sr=SR)
    I49, _ = V.loudness(a49, stereo=False)
    r = __import__("subprocess").run([V.ffmpeg_exe(), "-hide_banner", "-nostdin", "-f", "f32le", "-ar", "48000", "-ac", "1",
                                      "-i", "-", "-af", "ebur128=peak=true", "-f", "null", "-"], input=a49.tobytes(),
                                     capture_output=True)
    ff = float(re.findall(r"I:\s+(-?[\d.]+) LUFS", r.stderr.decode())[-1])
    assert abs(I49 - ff) < 0.15, (I49, ff)
    assert abs(V.loudness(a49)[0] - I49 - V.STEREO_DB) < 1e-6

# ---------------------------------------------------------------- detectors
g = VD.gain_curve(SR, [{"t0": 0.4, "t1": 0.5, "db": -20}], ramp=0.005)
assert abs(g[int(0.45 * SR)] - 0.1) < 1e-6 and g[int(0.3 * SR)] == 1.0 and g[int(0.6 * SR)] == 1.0
assert 0.1 < g[int(0.398 * SR)] < 1.0
db_speech = M.envelope(MEDIA_49) if _common.have(MEDIA_49) else None
if db_speech is not None:
    assert VD.music_score(db_speech)[0] < 0.3
tt = np.arange(16000 * 30) / 16000
mus = (sum(np.sin(2 * np.pi * f0 * tt) for f0 in (220, 277, 330, 440)) * 0.1 * (1 + 0.5 * np.sin(2 * np.pi * 2 * tt))).astype(np.float32)
assert VD.music_score(M.rms_db(mus))[0] > 0.8

if _common.have(MEDIA_49):
    rows = T.cached_words(MEDIA_49)
    assert rows, "49 s test clip needs its cached transcript"
    words = [[float(r_[1]), float(r_[2])] for r_ in rows]
    # Inject synthetic clicks (2 ms broadband bursts, fast decay) into pauses >= 0.6 s, away from real events.
    base = a49.copy()
    found0 = VD.clicks(base, words)
    gaps = [(words[i][1], words[i + 1][0]) for i in range(len(words) - 1) if words[i + 1][0] - words[i][1] >= 0.6]
    rng = np.random.default_rng(3)
    shots = []
    gaps = [(words[i][1], words[i + 1][0]) for i in range(len(words) - 1) if words[i + 1][0] - words[i][1] >= 0.45]
    for g0_, g1_ in gaps:
        for tc in np.arange(g0_ + 0.2, g1_ - 0.2, 0.45):
            if any(abs(tc - (c["t0"] + c["t1"]) / 2) < 0.5 for c in found0):
                continue
            n = int(0.004 * SR)
            amp = 10 ** (rng.uniform(-50, -26) / 20)                    # quiet trackpad tick .. loud keyboard
            burst = rng.standard_normal(n) * np.exp(-np.arange(n) / (0.0008 * SR)) * amp
            i0 = int(tc * SR)
            base[i0:i0 + n] += burst.astype(np.float32)
            shots.append(float(tc))
    assert len(shots) >= 5, shots
    found = VD.clicks(base, words)
    hit = sum(1 for tc in shots if any(c["t0"] - 0.01 <= tc <= c["t1"] + 0.01 for c in found))
    assert hit >= 0.8 * len(shots), (hit, shots)
    for c in found:                                            # never inside a word (+ guard)
        assert not any(c["t0"] < w1 + VD.WORD_GUARD - 1e-6 and c["t1"] > w0 - VD.WORD_GUARD + 1e-6 for w0, w1 in words), c
    print(f"clicks: {hit}/{len(shots)} injected found, {len(found0)} natural groups in 49 s")
    # A speech-like onset (voiced, keeps going) right after a gap is NOT a click.
    tone = np.zeros(SR, np.float32)
    tone[SR // 2:] = (0.2 * np.sin(2 * np.pi * 180 * np.arange(SR // 2) / SR)).astype(np.float32)
    assert not VD.clicks(tone, [], words_known=False)
    # Breath: 0.4 s band-passed noise (500 Hz..5 kHz) 15 dB under speech in a long pause.
    from scipy.signal import butter, sosfilt
    long_gap = max(gaps, key=lambda gp: gp[1] - gp[0])
    nb = int(0.4 * SR)
    br = sosfilt(butter(4, [500, 5000], "bandpass", fs=SR, output="sos"), rng.standard_normal(nb)).astype(np.float32)
    br *= 10 ** (-42 / 20) / np.sqrt(np.mean(br ** 2))
    b2 = a49.copy()
    tb = (long_gap[0] + long_gap[1]) / 2 - 0.2
    b2[int(tb * SR): int(tb * SR) + nb] += br
    bfound = VD.breaths(b2, words)
    assert any(x["t0"] - 0.05 <= tb + 0.2 <= x["t1"] + 0.05 for x in bfound), (tb, bfound)

# ---------------------------------------------------------------- timeline: voice twins, music, re-run
music_wav = tmp / "music.wav"
mm = np.tile(mus, 2)[: 16000 * 49]
with wave.open(str(music_wav), "wb") as w:
    w.setnchannels(1)
    w.setsampwidth(2)
    w.setframerate(16000)
    w.writeframes((np.clip(mm, -1, 1) * 30000).astype("<i2").tobytes())


def seq_json(extra_tracks=(), name="Voice test"):
    d = 48.925
    clip = lambda p, n: {"name": n, "path": str(p), "start": 0.0, "end": d, "in": 0.0, "out": d}  # noqa: E731
    audio = [{"index": 0, "name": "A1", "muted": False, "locked": False, "clips": [clip(MEDIA_49, "x.mp4")]},
             {"index": 1, "name": "A2", "muted": False, "locked": False, "clips": [clip(MEDIA_49, "x.mp4")]},
             {"index": 2, "name": "Musik", "muted": False, "locked": False, "clips": [clip(music_wav, "music.wav")]}]
    for t_ in extra_tracks:
        audio.append(t_)
    return {"id": "seq-voice", "name": name, "fps": 120, "width": 2292, "height": 960, "duration": d,
            "video": [{"index": 0, "name": "V1", "clips": [clip(MEDIA_49, "x.mp4")]}], "audio": audio}


if _common.have(MEDIA_49, MEDIA_2M):
    tl = Timeline.from_json(seq_json())
    plan = voice.track_plan(tl, voice.settings({}))
    assert plan["render"] == [0] and plan["mute"] == [0, 1] and not plan["rerun"], plan
    mus_row = [x for x in plan["music"] if x["track"] == 2][0]
    assert mus_row["auto"] and mus_row["score"] >= 0.5, plan["music"]
    plan2 = voice.track_plan(tl, voice.settings({"voice_tracks": [1]}))
    assert plan2["render"] == [1] and plan2["mute"] == [1]

    # analyze
    job = {"tool": "voice", "action": "analyze", "seq": seq_json(), "params": {}, "workdir": str(tmp)}
    em = ListEmitter()
    res = voice.analyze(job, em)
    assert "error" not in em.kinds() and Path(res["review"]).is_file()
    st = res["stats"]
    assert st["has_words"] and st["words"] > 50 and st["clicks"] >= 3 and abs(st["before"]["lufs"] - (-20.4)) < 0.3, st
    doc = json.loads(Path(res["review"]).read_text(encoding="utf-8"))
    kinds = {it["kind"] for it in doc["items"]}
    assert kinds <= {"click", "breath"} and all("ctx" in it for it in doc["items"])
    for it in doc["items"]:
        assert not any(it["t0"] < w1 and it["t1"] > w0 for w0, w1 in words), it

    # apply (+ ducking plan for the music track)
    job_a = dict(job, action="apply", review=res["review"])
    em = ListEmitter()
    out = voice.apply(job_a, em)
    p = out["plan"]
    assert p["kind"] == "voice" and Path(p["wav"]).is_file() and p["track_name"] == "Klipora Suara"
    assert out["summary"].startswith("Suara diproses ") and out["summary"].endswith(" LUFS"), out["summary"]
    assert p["mute_tracks"] == [0, 1] and len(p["mute_clips"]) == 2 and p["start"] == 0.0
    assert p["duck"] and p["duck"]["tracks"] == [2] and len(p["duck"]["ranges"]) >= 3 and p["duck"]["db"] == -12.0
    with wave.open(p["wav"], "rb") as w:
        assert w.getframerate() == SR and w.getnchannels() == 2 and w.getsampwidth() == 2
        assert abs(w.getnframes() - round(48.925 * SR)) <= 2
    after = out["stats"]["after"]
    assert abs(after["lufs"] - (-14.0)) <= 0.5 and after["tp"] <= -0.9, after
    assert out["stats"]["normalization"] == "linear", out["stats"]
    y = voice._read_wav_mono(p["wav"])
    assert abs(V.lag(a49, y[: len(a49)])) <= 2                               # stays in sync with the video
    assert Path(out["stats"]["preview"]["before"]).is_file() and Path(out["stats"]["preview"]["after"]).is_file()
    assert not list((Path(p["wav"]).parent).glob("_voice_*"))                # temp files removed

    # a selected click is really attenuated in the output (relative to the speech level around it)
    clk = [it for it in doc["items"] if it["kind"] == "click" and it["t1"] - it["t0"] > 0.02]
    if clk:
        it = max(clk, key=lambda z: z.get("peak", -99))
        seg_raw = a49[int(it["t0"] * SR): int(it["t1"] * SR)]
        seg_out = y[int(it["t0"] * SR): int(it["t1"] * SR)]
        lv = lambda z: 20 * math.log10(np.sqrt(np.mean(np.asarray(z, np.float64) ** 2)) + 1e-9)  # noqa: E731
        gain = after["speech_db"] - st["before"]["speech_db"]
        assert lv(seg_out) - lv(seg_raw) < gain - 12, (lv(seg_out), lv(seg_raw), gain)

    # preview + snippet + match_eq
    pv = voice.preview(dict(job, action="preview", params={"t": 20.0}), ListEmitter())
    assert Path(pv["before"]).is_file() and Path(pv["after"]).is_file() and abs(pv["t0"] - 20.0) < 1e-6
    with wave.open(pv["after"], "rb") as w:
        assert abs(w.getnframes() / SR - 10.0) < 0.02
    sn = voice.snippet(dict(job, action="snippet", params={"t0": 13.21, "t1": 13.29, "kind": "click"}), ListEmitter())
    assert Path(sn["path"]).is_file() and sn["dur"] > 1.5
    eq = voice.match_eq(dict(job, action="match_eq", params={"ref": str(MEDIA_2M)}), ListEmitter())
    assert len(eq["eq"]) == 8 and all(abs(gg) <= 6.0 for _, gg in eq["eq"]), eq
    eq_self = voice.match_eq(dict(job, action="match_eq", params={"ref": str(MEDIA_49)}), ListEmitter())
    assert all(abs(gg) <= 1.5 for _, gg in eq_self["eq"]), eq_self
    print("match_eq 2 min clip ->", eq["eq"])

    # re-run on a result: our output track exists, original voice clips disabled -> still found, ours ignored
    for out_name in ("Klipora Suara", "AutoCut Suara"):
        rr = seq_json(extra_tracks=[{"index": 3, "name": out_name, "clips": [
            {"name": "x_suara.wav", "path": p["wav"], "start": 0.0, "end": 48.925, "in": 0.0, "out": 48.925}]}])
        for tr in rr["audio"][:2]:
            tr["clips"][0]["disabled"] = True
        plan3 = voice.track_plan(Timeline.from_json(rr), voice.settings({}))
        assert plan3["rerun"] and plan3["render"] == [0] and plan3["ours"] == [3] and \
            all(x["track"] != 3 for x in plan3["music"]), (out_name, plan3)
    # our other helper tracks (new and old names) are never voice or music
    hp = seq_json(extra_tracks=[{"index": 3, "name": "Klipora Sensor", "clips": [dict(rr["audio"][3]["clips"][0])]},
                                {"index": 4, "name": "AutoCut B-Roll", "clips": [dict(rr["audio"][3]["clips"][0])]}])
    plan4 = voice.track_plan(Timeline.from_json(hp), voice.settings({}))
    assert all(r["index"] not in (3, 4) for r in plan4["rows"]) and not plan4["ours"], plan4["rows"]

    # tracks action (page overview)
    tr_res = voice.tracks({"seq": seq_json(), "params": {}}, ListEmitter())
    assert tr_res["voice"] == [0] and any(x["auto"] for x in tr_res["music"])

    # errors: empty sequence, missing reference
    try:
        voice.match_eq(dict(job, params={"ref": "C:/nope.wav"}), ListEmitter())
        raise AssertionError("missing ref accepted")
    except voice.EngineError as e:
        assert e.code == "NO_MEDIA" and e.msg == "File contoh tidak ditemukan.", e.msg
    # English job language (the panel sends job["lang"]): error text, review item labels, advice
    with i18n.using("en"):
        try:
            voice.match_eq(dict(job, params={"ref": "C:/nope.wav"}), ListEmitter())
            raise AssertionError("missing ref accepted")
        except voice.EngineError as e:
            assert e.msg == "Sample file not found." and e.hint == "Choose the sample audio or video file again.", e.msg
        assert voice._denoise_label("off") == "noise: off" and voice._advice({"noise_db": -40})[0]["text"].startswith("Background noise")
        em = ListEmitter()
        res_en = voice.analyze(dict(job, workdir=str(tmp / "en")), em)
        doc_en = json.loads(Path(res_en["review"]).read_text(encoding="utf-8"))
        labels = {it["label"] for it in doc_en["items"]}
        assert labels and all(re.match(r"^(Click|Typing, \d+ clicks|\d+ clicks|Breath \d+\.\d s)$", x) for x in labels), labels
        assert next(e.get("label") for e in em.items if e["ev"] == "stage") == "Reading voice audio", em.items[:3]
    assert voice._denoise_label("off") == "noise: mati"

_common.cleanup("voice")
print("ok")
