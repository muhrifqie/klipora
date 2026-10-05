"""Sensor Kata Kasar (ac.tools.profanity): lexicon + normalization, context rules on the research's 23
sentences, user lists, edge refine, real-media analyze (cached transcripts, no GPU, no AI quota), apply plans for
every mode, tone files, preview render, captions hits, job protocol through cli.py, Klipora names (old "AutoCut
Sensor" / [AC-PF] still recognised) and English job texts.

  python engine/tests/test_profanity.py           offline (AI disabled, never spends Grok quota)
  python engine/tests/test_profanity.py --live    + the AI 3-vote check on the 12 held-out sentences (3 requests)
"""
import atexit
import os
import shutil
import subprocess
import sys
import tempfile
import wave
from pathlib import Path

LIVE = "--live" in sys.argv[1:]
if not LIVE:
    os.environ["AI_DISABLED"] = "1"          # before any ac.ai import: rules only, zero quota
RUN = f"profanity_{os.getpid()}"              # own folders: run_all.py from another agent may run this test too
SANDBOX = Path(tempfile.gettempdir()) / "autocut_tests" / (RUN + "_appdata")
shutil.rmtree(SANDBOX, ignore_errors=True)
SANDBOX.mkdir(parents=True, exist_ok=True)
os.environ["APPDATA"] = str(SANDBOX)         # word library + custom sounds land in the sandbox

import numpy as np  # noqa: E402

import _common  # noqa: E402
from _common import MEDIA_34M, MEDIA_49, approx  # noqa: E402
from ac import i18n  # noqa: E402
from ac import review as RV  # noqa: E402
from ac.progress import ListEmitter, NullEmitter  # noqa: E402
from ac.timeline import Timeline  # noqa: E402
from ac.tools import _profanity_lexicon as LX  # noqa: E402
from ac.tools import _profanity_sfx as SFX  # noqa: E402
from ac.tools import profanity as P  # noqa: E402
from ac.util import EngineError, file_hash, read_json, write_json  # noqa: E402

tmp = _common.scratch(RUN)
atexit.register(lambda: (_common.cleanup(RUN), shutil.rmtree(SANDBOX, ignore_errors=True)))   # also after a failure

# ---------------------------------------------------------------- 1. lexicon + normalization
assert LX.norm("Anjiiing,") == "anjing" and LX.norm("cokkk") == "cok" and LX.norm(" BANGSAAAT!") == "bangsat"
assert LX.lookup("b4ngs4t")[:2] == ("bangsat", 3)
assert LX.lookup("741") is None and LX.lookup("2024") is None, "pure numbers must never become words (741 -> tai)"
assert LX.lookup(" f***")[:3] == ("fuck", 3, True), LX.lookup(" f***")
assert LX.lookup(" anj*ng")[0] == "anjing" and LX.lookup(" anj*ng")[1] >= 2
assert LX.lookup(" anjingnya")[:2] == ("anjing", 0) and LX.lookup("tai-nya")[:2] == ("tai", 0)
assert LX.lookup(" Fucking") [:2] == ("fucking", 3) and LX.lookup("motherfuckers")[0] == "fuck"
for clean_word in ("santai", "pantai", "ketahuan", "cokelat", "asumsi", "tahun", "setahun", "babinsa", "cukup",
                   "taiwan", "monitor", "bangkit", "Dickson", "colok", "sialnya"):
    assert LX.lookup(clean_word) is None, clean_word
assert LX.mask_text(" kontol,") == " k****l,", LX.mask_text(" kontol,")
assert LX.mask_text(" anjing", "bip") == " [bip]" and LX.mask_text("Tai!", "stars") == "T**!"
assert LX.mask_text(" anjing.", "full") == " ******." and LX.mask_text(" f***") == " f***"
assert LX.mask_text(" babi", "off") == " babi"
assert LX.phrase_tokens("  Dasar   KAMPRET! ") == ["dasar", "kampret"]


# ---------------------------------------------------------------- 2. context rules (no AI) on 23 sentences
def mk(text, t0=0.0):
    out, t = [], t0
    for k, w in enumerate(text.split()):
        out.append({"text": " " + w, "t0": round(t, 2), "t1": round(t + 0.3, 2), "seg": 0, "p": 0.9,
                    "id": f"test:{k}"})
        t += 0.35
    out[-1]["seg"] = 1
    return out


CASES = [  # (sentence, words that SHOULD be censored at Normal)  -- product_ai.md 4.3 tuned + held-out sets
    ("Ini anjing peliharaan saya namanya Bruno lucu banget kan", []),
    ("Anjing lu, udah dibilangin jangan diklik malah diklik", ["Anjing"]),
    ("Ada tahi lalat di pipi kirinya jadi gampang dikenali", []),
    ("Dasar babi, kerjaan gak beres-beres dari kemarin", ["babi,"]),
    ("Hari ini kita masak babi panggang khas Batak ya teman-teman", []),
    ("Anjiiing servernya down lagi, kontol emang", ["Anjiiing", "kontol"]),
    ("Gila sih ini, f*** banget harganya naik terus, bangsat", ["f***", "bangsat"]),
    ("Waduh asu tenan, jancuk lah koneksine", ["asu", "jancuk"]),
    ("Ini monyet di kebun binatang Ragunan lagi makan pisang", []),
    ("Setan banget sih nih bug, udah tiga jam belum kelar", ["Setan"]),
    ("Film horor ini setannya serem banget", []),
    ("Kucing sama anjing di rumah saya akur banget", []),
    ("Anjing emang, file-nya kehapus semua padahal belum disave", ["Anjing"]),
    ("Si Budi punya tahi lalat besar di dagu", []),
    ("Monyet lu, ngapain ngerjain gue kayak gitu", ["Monyet"]),
    ("Sate babi di Bali enak banget harganya murah", []),
    ("Babi lah, transfer gue nyangkut dari kemarin", ["Babi"]),
    ("Ada monyet lepas di kompleks tadi pagi", []),
    ("Tai banget nih aplikasi, crash terus", ["Tai"]),
    ("Kucingnya pup, tai-nya bau banget harus dibersihin", []),
    ("Ritual ngusir setan di desa itu unik", []),
    ("Asu, aku kaget tenan", ["Asu,"]),
    ("Dick Grayson adalah Robin pertama di komik Batman", []),
]
wrong = []
for sent, want in CASES:
    ws = mk(sent)
    hits, _ = P.detect(ws, "normal")
    got = []
    for h in hits:
        on = True if h["tier"] else P.rule_decide(ws, h["i0"], h["i1"])[0]
        if on:
            got.append(ws[h["i0"]]["text"].strip())
    if sorted(got) != sorted(want):
        wrong.append((sent, got, want))
assert not wrong, "rule decisions differ:\n" + "\n".join(map(str, wrong))

# levels: anjir is "ringan" (tier 1): hidden at Normal (counted in `below`), found at Ketat
ws = mk("Oh mbak anjir keren banget hasilnya")
assert P.detect(ws, "normal") == ([], 1) and len(P.detect(ws, "ketat")[0]) == 1
assert P.detect(mk("goblok banget sih"), "longgar")[1] == 1 and len(P.detect(mk("goblok banget"), "normal")[0]) == 1

# ---------------------------------------------------------------- 3. user lists (library in the sandbox)
lib = P.save_library({"block": ["Dasar  Kampret", "pinjol", "pinjol", ""], "allow": ["anjing", "pinjol"],
                      "captionStyle": "nope"})
assert lib["block"] == ["dasar kampret", "pinjol"] and lib["allow"] == ["anjing"] and lib["captionStyle"] == "stars"
assert read_json(P.library_path())["block"] == ["dasar kampret", "pinjol"]
assert P.load_library()["allow"] == ["anjing"]
ws = mk("dasar kampret lu, pinjolnya bikin pusing anjing lu")
hits, _ = P.detect(ws, "normal", lib["block"], lib["allow"])
assert [(h["i0"], h["i1"], h["src"], h["tier"]) for h in hits] == [(0, 1, "user", 3), (3, 3, "user", 3)], hits
ws = mk("ada tahi lalat dan anak anjing")
assert P.detect(ws, "ketat")[0] == [], "built-in literal phrases are never hits"
assert len(P.detect(mk("tahi lalat"), "normal", block=["tahi lalat"])[0]) == 1, "user block beats built-in phrases"
r = P.library({"params": {"op": "set", "block": ["bodoh"], "allow": [], "captionStyle": "bip"}}, NullEmitter())
assert r["library"]["block"] == ["bodoh"] and r["library"]["captionStyle"] == "bip" and "anjing" in r["builtin"]["0"]
P.save_library({"block": [], "allow": [], "captionStyle": "stars"})

# ---------------------------------------------------------------- 4. edge refine (synthetic envelope)
db = np.full(300, -70.0, np.float32)          # 3 s, 10 ms frames
db[100:140] = -18.0                           # word energy 1.00 .. 1.40 s
db[95:100] = -45.0                            # quiet-ish lead-in
s, e = P.refine(1.05, 1.32, db)               # Whisper edges a bit off
assert 0.95 <= s <= 1.0 and 1.40 <= e <= 1.45, (s, e)
s, e = P.refine(2.0, 2.05, np.full(300, -90.0, np.float32))     # no audio: pads + min length around the word
assert approx(e - s, 0.25, 1e-3), (s, e)

# ---------------------------------------------------------------- 5. real media (cached transcripts)
if _common.have(MEDIA_34M, MEDIA_49):
    tl = Timeline.from_media(MEDIA_34M)
    job = {"tool": "profanity", "action": "analyze", "seq": tl.to_json(), "params": {"level": "normal", "ai": False},
           "workdir": str(tmp / "wd")}
    em = ListEmitter()
    res = P.analyze(job, em)
    assert res["n"] == 0 and res["stats"]["below"] >= 1 and res["stats"]["words"] > 2500, res
    assert "error" not in em.kinds() and [e["id"] for e in em.items if e["ev"] == "stage"] == \
        ["words", "listen", "scan", "edges", "review"]
    job["params"] = {"level": "ketat", "ai": True}          # AI on, but disabled -> no ambiguous -> no call
    res = P.analyze(job, ListEmitter())
    doc = RV.load(res["review"])
    assert res["n"] == 1 and res["on"] == 1, res
    it = doc["items"][0]
    assert it["lemma"] == "anjir" and it["tier"] == 1 and it["src"] == "listen" and it["on"], it
    assert 1360.9 <= it["t0"] <= 1361.05 and 1361.3 <= it["t1"] <= 1361.5, (it["t0"], it["t1"])
    assert it["media"] == str(MEDIA_34M) and approx(it["ms0"], it["t0"], 1e-3) and it["hi"] > 2000
    assert doc["speech_db"] and -30 < doc["speech_db"] < -15 and doc["level"] == "ketat", doc["speech_db"]

    # the same moment inside a cut timeline: clip src 1300..1400 placed at sequence 5 s (A1 + a stereo twin on A2)
    clip = {"name": "x", "path": str(MEDIA_34M), "start": 5.0, "end": 105.0, "in": 1300.0, "out": 1400.0}
    cut = {"name": "Potongan", "fps": 120, "duration": 105.0, "video": [{"index": 0, "clips": [dict(clip)]}],
           "audio": [{"index": 0, "name": "A1", "clips": [dict(clip)]}, {"index": 1, "name": "A2", "clips": [dict(clip)]},
                     {"index": 2, "name": "AutoCut Sensor", "clips": [{"name": "beep", "path": str(MEDIA_49), "start": 0,
                                                                   "end": 1, "in": 0, "out": 1}]}]}
    em = ListEmitter()
    res2 = P.analyze({"tool": "profanity", "seq": cut, "params": {"level": "ketat", "ai": False},
                      "workdir": str(tmp / "wd2")}, em)
    d2 = RV.load(res2["review"])
    assert res2["n"] == 1 and approx(d2["items"][0]["t0"], it["t0"] - 1295.0, 0.02), d2["items"]
    assert d2["sensor_track"] and d2["dialog_tracks"] == [0, 1]
    assert any(e["ev"] == "warn" and "AutoCut Sensor" in e["msg"] for e in em.items), "warns about an old sensor track"
    # both the new and the pre-rename sensor track names are "ours" (not dialog)
    assert P.SENSOR_TRACK == "Klipora Sensor" and P.is_sensor_track("Klipora Sensor") and P.is_sensor_track(" AutoCut Sensor ")
    assert not P.is_sensor_track("Audio 1") and not P.is_sensor_track("Klipora Suara")
    both = Timeline.from_json({**cut, "audio": cut["audio"] + [{"index": 3, "name": "Klipora Sensor", "clips": []}]})
    assert P.dialog_tracks(both) == [0, 1], P.dialog_tracks(both)

    # user's manual choice survives a re-run (carry_over)
    d2["items"][0]["on"], d2["items"][0]["touched"] = False, True
    write_json(res2["review"], d2)
    res3 = P.analyze({"tool": "profanity", "seq": cut, "params": {"level": "ketat", "ai": False},
                      "workdir": str(tmp / "wd2")}, ListEmitter())
    assert res3["on"] == 0 and RV.load(res3["review"])["items"][0]["touched"]

    res49 = P.analyze({"tool": "profanity", "seq": Timeline.from_media(MEDIA_49).to_json(),
                       "params": {"level": "ketat", "ai": False}, "workdir": str(tmp / "wd49")}, ListEmitter())
    assert res49["n"] == 0 and res49["stats"]["words"] > 60, res49

    # ------------------------------------------------------------ 6. apply: plans for every mode
    rv_job = {"tool": "profanity", "action": "apply", "review": res["review"], "seq": None, "workdir": str(tmp / "wd")}
    out = P.apply({**rv_job, "params": {"mode": "beep"}}, ListEmitter())
    plan = out["plan"]
    assert plan["kind"] == "censor" and plan["mode"] == "beep" and plan["track_name"] == "Klipora Sensor"
    assert plan["name"].endswith(" (Klipora)") and plan["mode_label"] == "Bip" and plan["markers"][0]["name"] == "Sensor"
    assert len(plan["ranges"]) == 1 and plan["ranges"][0]["paths"] == [str(MEDIA_34M)]
    tone = plan["tones"][0]
    assert Path(tone["path"]).parent == SFX.SFX_DIR and tone["dur"] >= plan["ranges"][0]["t1"] - plan["ranges"][0]["t0"] - 1e-6
    assert approx(tone["t"] + tone["dur"] / 2, (plan["ranges"][0]["t0"] + plan["ranges"][0]["t1"]) / 2, 0.002)
    assert plan["tone_auto"] and -32 <= plan["tone_db"] <= -12 and plan["markers"][0]["tag"] == "[Klipora-PF]"
    assert plan["markers"][0]["comment"] == "a***r. (Bip)" and out["stats"]["first7"] == 0, plan["markers"][0]
    assert out["summary"] == "1 kata disensor (Bip)", out["summary"]
    # English job (the panel sends job["lang"]; cli.execute wraps it in i18n.using): labels, markers, errors
    with i18n.using("en"):
        out_en = P.apply({**rv_job, "params": {"mode": "beep"}}, ListEmitter())
        assert out_en["summary"] == "1 word censored (Beep)" and out_en["plan"]["mode_label"] == "Beep", out_en["summary"]
        assert out_en["plan"]["markers"][0]["name"] == "Censor" and out_en["plan"]["markers"][0]["comment"].endswith("(Beep)")
        assert out_en["plan"]["name"].endswith(" (Klipora)") and out_en["plan"]["track_name"] == "Klipora Sensor"
        assert P.tier_label(0) == "Double meaning" and P.rule_decide(mk("dasar babi"), 1, 1)[1].startswith('"dasar" before it')
    assert P.tier_label(0) == "Arti ganda", "back to Indonesian after the English block"
    with wave.open(tone["path"], "rb") as w:
        assert w.getnchannels() == 2 and w.getframerate() == 48000
        x = np.frombuffer(w.readframes(w.getnframes()), "<i2").reshape(-1, 2)[:, 0] / 32768.0
    mid = x[len(x) // 4: 3 * len(x) // 4]
    assert abs(20 * np.log10(np.sqrt(np.mean(mid ** 2))) - plan["tone_db"]) < 0.3, "tone RMS = requested dBFS"
    assert abs(x[0]) < 0.01 and abs(x[-1]) < 0.01, "fades baked in (no click)"
    n_aud = int(round(tone["dur"] * 48000))
    assert len(x) == n_aud + int(round(SFX.TAIL * 48000)) and not np.any(x[n_aud:]), "silent tail after the fade"
    assert np.abs(x[n_aud - 48:n_aud]).max() < 0.25 * np.abs(mid).max(), "fade-out ends before the tail"

    out = P.apply({**rv_job, "params": {"mode": "mute", "markers": False}}, ListEmitter())
    assert out["plan"]["tones"] == [] and out["plan"]["markers"] == [] and out["plan"]["mode"] == "mute"
    out = P.apply({**rv_job, "params": {"mode": "duck", "duck_db": -20}}, ListEmitter())
    assert out["plan"]["low_db"] == -20 and out["plan"]["tones"] == []
    snd = tmp / "quack.mp3"
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    "sine=frequency=440:duration=5", str(snd)], check=True)
    out = P.apply({**rv_job, "params": {"mode": "custom", "custom": str(snd), "custom_max": 0.8}}, ListEmitter())
    ct = out["plan"]["tones"][0]
    assert Path(ct["path"]).is_relative_to(SANDBOX) and approx(ct["dur"], 0.8, 0.02), ct
    with wave.open(ct["path"], "rb") as w:     # 5 s source cut to 0.8 s: faded at the cut, then a silent tail
        y = np.frombuffer(w.readframes(w.getnframes()), "<i2").reshape(-1, 2)[:, 0] / 32768.0
    n_aud = int(round(ct["dur"] * 48000))
    assert approx(len(y) / 48000, ct["dur"] + SFX.TAIL, 0.002) and np.abs(y[n_aud + 48:]).max() < 1e-3, "custom tail"
    assert np.abs(y[n_aud - 48:n_aud]).max() < 0.25 * np.abs(y[len(y) // 3:len(y) // 2]).max(), "custom fade-out"
    try:
        P.apply({**rv_job, "params": {"mode": "custom", "custom": str(tmp / "missing.wav")}}, ListEmitter())
        raise AssertionError("missing custom sound must fail")
    except EngineError as e:
        assert e.code == "NO_SFX"
    d = RV.load(res["review"])
    d["items"][0]["on"] = False
    write_json(tmp / "none.json", d)
    try:
        P.apply({**rv_job, "review": str(tmp / "none.json"), "params": {}}, ListEmitter())
        raise AssertionError("nothing checked must fail")
    except EngineError as e:
        assert e.code == "NO_HITS" and e.msg == "Tidak ada kata yang dicentang.", e.msg
    with i18n.using("en"):
        try:
            P.apply({**rv_job, "review": str(tmp / "none.json"), "params": {}}, ListEmitter())
            raise AssertionError("nothing checked must fail")
        except EngineError as e:
            assert e.msg == "No words are checked." and e.hint == "Check at least one word in the review.", (e.msg, e.hint)

    # ------------------------------------------------------------ 7. preview render ("Dengar")
    def rms_db(path, a, b):
        with wave.open(str(path), "rb") as w:
            y = np.frombuffer(w.readframes(w.getnframes()), "<i2").reshape(-1, 2)[:, 0] / 32768.0
        seg = y[int(a * 48000):int(b * 48000)]
        return 20 * np.log10(np.sqrt(np.mean(seg ** 2)) + 1e-9)

    pv = {"media": it["media"], "s0": it["ms0"], "s1": it["ms1"], "lo": it["lo"], "hi": it["hi"], "id": it["id"],
          "speech_db": doc["speech_db"]}
    rm = P.preview({"params": {**pv, "mode": "mute"}, "workdir": str(tmp / "wd")}, ListEmitter())
    ro = P.preview({"params": {**pv, "mode": "duck", "duck_db": 0}, "workdir": str(tmp / "wd")}, ListEmitter())
    rb = P.preview({"params": {**pv, "mode": "beep"}, "workdir": str(tmp / "wd")}, ListEmitter())
    a, b = rm["a"] + 0.02, rm["b"] - 0.02
    assert Path(rm["path"]).is_file() and 2.5 < rm["dur"] < 3.0, rm
    orig, muted, beep = rms_db(ro["path"], a, b), rms_db(rm["path"], a, b), rms_db(rb["path"], a, b)
    assert orig > -30 and muted < -80 and abs(beep - plan["tone_db"]) < 2.0, (orig, muted, beep)
    assert rms_db(rm["path"], 0.1, 0.9) > -60, "audio outside the word is untouched"

    # ------------------------------------------------------------ 8. captions: decisions + hits_for_timeline
    tl49 = Timeline.from_media(MEDIA_49)
    words = tl49.words_on_timeline(transcribe=False)
    dec_path = P._decisions_path(MEDIA_49)
    backup = dec_path.read_bytes() if dec_path.is_file() else None
    try:
        fake = [dict(w) for w in words[:6]]
        fake[2]["text"], fake[4]["text"] = " bangsat", " anjing,"      # pretend Whisper heard these
        h = P.hits_for_timeline(tl49, words=fake)
        assert [x["id"] for x in h] == [fake[2]["id"]] and h[0]["mask"] == " b*****t" and h[0]["source"] == "rules"
        idx2, idx4 = fake[2]["id"].split(":")[1], fake[4]["id"].split(":")[1]
        rdoc = {"level": "normal", "items": [
            {"on": False, "media": str(MEDIA_49), "words": [fake[2]["id"]], "texts": [" bangsat"], "lemma": "bangsat", "tier": 3},
            {"on": True, "media": str(MEDIA_49), "words": [fake[4]["id"]], "texts": [" anjing,"], "lemma": "anjing", "tier": 0}]}
        assert P.save_decisions(rdoc) == 2
        dd = read_json(dec_path)
        assert dd["hash"] == file_hash(MEDIA_49) and dd["words"][idx2]["on"] is False and dd["words"][idx4]["on"]
        h = P.hits_for_timeline(tl49.to_json(), words=fake, style="bip")
        assert [(x["id"], x["mask"], x["source"]) for x in h] == [(fake[4]["id"], " [bip],", "review")], h
        assert len(P.hits_for_timeline(tl49, words=fake, include_off=True)) == 2
        assert P.hits_for_timeline(tl49, words=fake, style="off") == []
        assert P.hits_for_timeline(tl49) == [], "real 49 s transcript has no swear words"
        r = P.hits({"seq": tl49.to_json(), "params": {}}, NullEmitter())
        assert r["n"] == 0 and r["hits"] == []
    finally:
        if backup is None:
            dec_path.unlink(missing_ok=True)
        else:
            dec_path.write_bytes(backup)

    # ------------------------------------------------------------ 9. job protocol through cli.py
    jf = tmp / "job.json"
    write_json(jf, {"id": "pf-test", "tool": "profanity", "action": "analyze", "seq": cut,
                    "params": {"level": "ketat", "ai": True, "deep": True}, "workdir": str(tmp / "wd3")})
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    pr = subprocess.run([sys.executable, "-X", "utf8", str(_common.ENGINE / "cli.py"), "run", str(jf)],
                        capture_output=True, text=True, encoding="utf-8", env=env, timeout=120)
    import json
    evs = [json.loads(ln) for ln in pr.stdout.splitlines() if ln.startswith("{")]
    assert pr.returncode == 0, pr.stdout[-800:] + pr.stderr[-800:]
    assert [e["stage"] for e in evs if e["ev"] == "stage"] == ["words", "listen", "scan", "edges", "ai", "review"]
    assert evs[-1]["ev"] == "result" and evs[-1]["data"]["n"] == 1 and "kata" in evs[-1]["data"]["summary"]

# ---------------------------------------------------------------- 10. AI off -> rules + warning
found = [{"tier": 0, "word": "Anjing", "sentence": "Anjing lu, udah dibilangin", "rule": (True, "x")}]
em = ListEmitter()
src = P._decide_ai(found, em) if not LIVE else "skip"
if not LIVE:
    assert src == "rules" and "ai" not in found[0] and any(e["ev"] == "warn" and "aturan" in e["msg"] for e in em.items)

# full analyze with ambiguous words: synthetic speech laid over the real 49 s clip (words patched in, so the
# envelope / clip mapping / review building run for real). Offline: rule decisions; --live: AI votes (3 requests).
if _common.have(MEDIA_49):
    tl = Timeline.from_media(MEDIA_49)
    real = tl.words_on_timeline(transcribe=False)
    sents = ["Anjing lu, udah dibilangin jangan diklik", "Ini anjing saya namanya Bruno lucu",
             "Dasar babi, kerjaan gak beres", "Ada monyet lepas di kompleks tadi", "Hari ini masak babi panggang"]
    fake, k = [], 0
    for n, sent in enumerate(sents):
        for w in mk(sent, 2.0 + 10.0 * n):
            w.update(id=real[k % len(real)]["id"].split(":")[0] + f":{9000 + k}", src=str(MEDIA_49), track=0, clip=0,
                     s0=w["t0"], s1=w["t1"], clip_end=False)
            fake.append(w)
            k += 1
    orig = Timeline.words_on_timeline
    Timeline.words_on_timeline = lambda self, **kw: [dict(w) for w in fake]
    try:
        em = ListEmitter()
        res = P.analyze({"tool": "profanity", "seq": tl.to_json(), "params": {"level": "normal", "ai": True, "deep": False},
                         "workdir": str(tmp / "wd_amb")}, em)
    finally:
        Timeline.words_on_timeline = orig
    d = RV.load(res["review"])
    got = {(it["label"], it["on"]) for it in d["items"]}
    assert got == {("Anjing", True), ("anjing", False), ("babi,", True), ("monyet", False)}, got   # babi panggang: built-in
    assert all(it["tier"] == 0 and it["note"]["type"] in ("warn", "ai") for it in d["items"])
    assert all(it["words"] and it["media"] == str(MEDIA_49) and "ms0" in it for it in d["items"])
    assert d["stats"]["ambiguous"] == 4 and d["stats"]["ai"] in (("ai", "cache") if LIVE else ("rules",)), d["stats"]
    assert [e["id"] for e in em.items if e["ev"] == "stage"] == ["words", "scan", "edges", "ai", "review"]

# ---------------------------------------------------------------- 11. live AI 3-vote (only with --live)
if LIVE:
    held = CASES[11:]
    items, want = [], []
    for sent, bad in held:
        ws = mk(sent)
        for h in P.detect(ws, "normal")[0]:
            if h["tier"] == 0:
                items.append({"kata": ws[h["i0"]]["text"].strip(" ,.!?"), "kalimat": sent})
                want.append(ws[h["i0"]]["text"].strip() in bad)
    votes, src = P.ai_votes(items, emit=ListEmitter())
    rules = []
    for sent, _bad in held:
        ws = mk(sent)
        rules += [P.rule_decide(ws, h["i0"], h["i1"])[0] for h in P.detect(ws, "normal")[0] if h["tier"] == 0]
    got = [v["votes"] >= (2 if v["of"] >= 3 else v["of"]) or (v["votes"] == 1 and r) for v, r in zip(votes, rules)]
    ok = sum(1 for g, w in zip(got, want) if g == w)
    print(f"AI {src}: {ok}/{len(want)} held-out decisions correct, votes {[v['votes'] for v in votes]}")
    assert ok >= len(want) - 2

print("ok")
