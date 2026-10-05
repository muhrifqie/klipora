"""ac.captions stylist ("Tiru gaya dari gambar") + Brand Kit:
  - colour / font helpers, AI JSON normalisation, merge rules, font picking by category / weight / width
  - ROUND TRIP (offline): render our own templates to PNG (16:9 and 9:16), analyse them locally (no AI) and measure
    the attribute match: text colour within deltaE 15, box yes/no, case, position within 8 %
  - style_from_image action: local only, with a canned AI answer (mixed), the frame of the timeline, bad inputs
  - brandkit get / set / activate / delete / apply (ops restyle the doc, brand words -> glossary + emphasis keywords)
  - brand_emphasis: rule fallback (no AI) and a canned AI answer; ops apply cleanly; emoji policy
Offline by default (AI disabled, %APPDATA% sandboxed). ~20-40 s.
  python engine/tests/test_captions_stylist.py
  python engine/tests/test_captions_stylist.py --live   + AI round trip on 4 templates through the real vision route
                                                          (<= 4 requests, results cached by image: a rerun is free)"""
import json
import os
import time

import _common
from _common import MEDIA_49

LIVE = _common.flag("--live")
tmp = _common.scratch("captions_stylist")
os.environ["APPDATA"] = str(tmp / "roaming")          # brandkit.json + settings: sandboxed
if not LIVE:
    os.environ["AI_DISABLED"] = "1"

from PIL import Image  # noqa: E402

from ac.captions import ACTIONS  # noqa: E402
from ac.captions import brandkit as BK  # noqa: E402
from ac.captions import layout as L  # noqa: E402
from ac.captions import model as M  # noqa: E402
from ac.captions import stylist as S  # noqa: E402
from ac.captions import templates as TP  # noqa: E402
from ac.progress import ListEmitter  # noqa: E402
from ac.timeline import Timeline  # noqa: E402
from ac.util import EngineError  # noqa: E402

T0 = time.perf_counter()
em = ListEmitter()

# ====================================================================== helpers
assert S.norm_hex("#fff") == "#FFFFFF" and S.norm_hex("00a86bcc") == "#00A86B" and S.norm_hex("red") is None
assert S.delta_e("#FFFFFF", "#FFFFFF") == 0 and S.delta_e("#FFFFFF", "#000000") > 99
assert 2 < S.delta_e("#FFE600", "#FFD400") < 15
assert S.frame_like(1920, 1080) and S.frame_like(1080, 1920) and S.frame_like(2292, 960) and not S.frame_like(800, 200)
assert S.frame_size(2292, 960) == (2578, 1080) and S.frame_size(540, 960) == (1080, 1920)
import numpy as np  # noqa: E402
pts = np.array([[0, 0, 0]] * 50 + [[100, 0, 0]] * 50, np.float32)
c1, l1 = S.kmeans(pts, 2)
c2, l2 = S.kmeans(pts, 2)
assert np.allclose(c1, c2) and sorted(round(float(c[0])) for c in c1) == [0, 100], "deterministic k-means"

cat = L.font_catalog(system=False)["fonts"]
assert S.pick_font("condensed", 900, catalog=cat)["base"] in ("Anton", "Barlow Condensed")
assert S.pick_font("sans", 700, "condensed", catalog=cat)["base"] in ("Anton", "Barlow Condensed", "Oswald")
assert S.pick_font("sans", 900, catalog=cat)["family"] == "Montserrat Black"
assert S.pick_font("sans", 700, catalog=cat)["family"] == "Montserrat Bold"
assert S.pick_font("rounded", 900, catalog=cat)["base"] == "Nunito"
assert S.pick_font("serif", 400, catalog=cat)["family"] == "DM Serif Display"
assert S.pick_font("handwritten", 400, catalog=cat)["family"] == "Caveat Brush"
assert S.pick_font("comic", 400, catalog=cat)["family"] == "Bangers"
assert S.pick_font("nonsense", 700, catalog=cat)["family"] == "Montserrat Bold", "unknown category -> sans"

ai_raw = {"text": "BELI SEKARANG", "text_color": "#ffffff", "active_word_color": "#FFE600", "active_word_text": "BELI",
          "stroke_color": "#000", "stroke_width": "thick", "shadow": "soft", "background": "per_line",
          "background_color": "#000000", "background_opacity": 0.6, "background_shape": "rounded", "case": "UPPER",
          "font_category": "condensed", "font_weight": "black", "font_width": "condensed", "italic": False,
          "position_y_pct": 72, "align": "center", "size": "large", "words_per_line": 2, "lines": 1,
          "highlight_style": "color", "animation_guess": "pop", "full_frame": True, "junk": "x"}
na = S.norm_ai(ai_raw)
assert na["fill"]["value"] == "#FFFFFF" and na["active"]["value"] == "#FFE600" and na["stroke"]["value"] == "#000000"
assert na["stroke_width"]["value"] == 10 and na["box"]["value"] == "line" and na["case"]["value"] == "upper"
assert na["font_cat"]["value"] == "condensed" and na["weight"]["value"] == 900 and na["anim"]["value"] == "pop"
assert na["_text"] == "BELI SEKARANG" and na["_frame"] is True and all(v["src"] == "ai" for k, v in na.items()
                                                                       if not k.startswith("_"))
bad = S.norm_ai({"text_color": "kuning", "case": "???", "background": "yes", "position_y_pct": 400,
                 "words_per_line": "dua", "stroke_width": "none"})
assert "fill" not in bad and "case" not in bad and "box" not in bad and "position" not in bad and "words" not in bad
assert bad["stroke"]["value"] is None and bad["stroke_width"]["value"] == 0
assert S.norm_ai("nope") == {} and S.norm_ai(None) == {}

# merge: close colours -> the measured one; far apart -> measurement wins on a clean background, AI otherwise
loc = {"fill": S._attr("#FDFDFD", 0.8), "box": S._attr("none", 0.65), "position": S._attr(71.0, 0.8)}
mg, notes = S.merge(loc, {"fill": S._attr("#FFFFFF", 0.8, "ai"), "box": S._attr("line", 0.75, "ai"),
                          "position": S._attr(60, 0.6, "ai")})
assert mg["fill"]["value"] == "#FDFDFD" and mg["fill"]["src"] == "ai+lokal" and mg["position"]["value"] == 71.0
assert mg["box"]["value"] == "line", "semantic attribute: higher confidence wins"
mg, notes = S.merge({"fill": S._attr("#FFFFFF", 0.8)}, {"fill": S._attr("#000000", 0.8, "ai")})
assert mg["fill"]["value"] == "#FFFFFF" and notes, "vision models are weak at exact colours"
mg, _ = S.merge({"fill": S._attr("#FFFFFF", 0.5)}, {"fill": S._attr("#000000", 0.8, "ai")})
assert mg["fill"]["value"] == "#000000", "busy picture: the AI colour wins"

# mapping: attributes -> base template + explicit style patch
base, st = S.to_style(na, 1080, 1920, cat)
assert base in TP.builtin_ids()
assert st["font"]["family"] in ("Anton", "Barlow Condensed Black") and st["font"]["case"] == "upper" \
    and st["font"]["uppercase"] is True
assert st["style"]["fill"] == "#FFFFFF" and st["style"]["outline"] == 10 and st["style"]["outline_color"] == "#000000"
assert st["style"]["gradient"] is None and st["style"]["extrude"] is None, "Pro effects switched off"
assert st["box"]["enabled"] is True and st["box"]["per"] == "line" and st["box"]["opacity"] == 0.6
assert st["highlight"]["color"] == "#FFE600" and st["layout"]["y"] == 72 and st["anim"]["in"] == "pop"
assert st["style"]["shadow_blur"] > 0
base2, st2 = S.to_style({"fill": S._attr("#FFFFFF", 0.8), "box": S._attr("none", 0.7)}, 1920, 1080, cat)
assert st2["box"]["enabled"] is False and st2["highlight"]["color"] is None and st2["timing"]["mode"] == "line"
_, st3 = S.to_style({"fill": S._attr("#111111", 0.8), "box": S._attr("active", 0.7),
                     "box_color": S._attr("#FE2C55", 0.7)}, 1080, 1920, cat)
assert st3["highlight"]["pill"] == "#FE2C55", "a box behind the active word only = sliding pill"
print(f"helpers ok ({time.perf_counter() - T0:.1f}s)")

# ====================================================================== round trip: our templates -> PNG -> local analysis
RT = ["hormozi_kuning", "tutorial_bersih", "boxed", "karaoke", "minimal_putih", "bold_pop", "garis_bawah",
      "sinematik", "subtitle_klasik", "neon", "mr_beast", "tutorial_sorot"]
TEXT = "Rahasia cuan jualan online"


def expect(tid, W, H):
    tpl = L.prep_template(TP.load(tid, W, H))
    words, ai = S._words_for(TEXT, tpl)
    caps = L.paginate(words, tpl, W, H)
    cap = next((c for c in caps if any(w.id == words[ai].id for w in c.words)), caps[0])
    boxes, _, _ = L.layout(cap, tpl, W, H)
    y = (min(b.base - b.cap for b in boxes) + max(b.base for b in boxes)) / 2 / H * 100
    f = tpl["font"]
    up = f.get("case") == "upper" or (f.get("case") is None and f.get("uppercase"))
    hl = tpl["highlight"]
    plain_fill = not (tpl["style"].get("color_cycle") or tpl["style"].get("gradient") or hl.get("future_alpha", 1) < 1
                      or hl.get("future_color") or hl.get("past_color"))
    return {"fill": tpl["style"]["fill"][:7], "plain": plain_fill, "box": bool(tpl["box"]["enabled"]),
            "upper": bool(up), "y": y}


score = {"fill": [0, 0], "box": [0, 0], "case": [0, 0], "position": [0, 0]}
rows = []
for tid in RT:
    for W, H in ((1080, 1920), (1920, 1080)):
        png = tmp / f"rt_{tid}_{W}.png"
        S.render_style(TP.load(tid, W, H), W, H, png, text=TEXT, out_w=960 if W > H else 540)
        a = S.analyze_local(Image.open(png).convert("RGB"))
        ex = expect(tid, W, H)

        def v(k):
            return (a.get(k) or {}).get("value")
        ok = {}
        if ex["plain"]:
            ok["fill"] = v("fill") is not None and S.delta_e(v("fill"), ex["fill"]) <= 15
        ok["box"] = (v("box") not in (None, "none")) == ex["box"]
        ok["case"] = (v("case") == "upper") == ex["upper"]
        ok["position"] = v("position") is not None and abs(v("position") - ex["y"]) <= 8
        for k, good in ok.items():
            score[k][0] += int(good)
            score[k][1] += 1
        rows.append((tid, W, {k: ("ok" if g else f"MISS {v('fill') if k == 'fill' else v(k)}") for k, g in ok.items()}))
miss = [r for r in rows if any(x != "ok" for x in r[2].values())]
for r in miss:
    print("  round trip miss:", r)
rate = {k: round(n / max(1, d), 3) for k, (n, d) in score.items()}
print(f"round trip local ({sum(d for _, d in score.values())} checks on {len(rows)} renders): {rate} "
      f"({time.perf_counter() - T0:.1f}s)")
assert rate["fill"] >= 0.85 and rate["box"] >= 0.9 and rate["case"] >= 0.85 and rate["position"] >= 0.9, rate

# ====================================================================== style_from_image action
wd = tmp / "wd"
png = tmp / "rt_boxed_1080.png"
r = ACTIONS["style_from_image"]({"tool": "captions", "seq": None, "workdir": str(wd),
                                 "params": {"path": str(png), "ai": False}}, em)
assert r["source"] == "local" and r["fidelity"] == "rendah" and not r["ai"]["used"]
assert r["style"]["box"]["enabled"] is True and r["style"]["style"]["fill"] == "#111111"
assert r["template"]["box"]["enabled"] is True and "id" not in r["template"] and r["template"]["name"]
assert os.path.isfile(r["compare"]["ours"]) and os.path.isfile(r["compare"]["theirs"]) and r["full_frame"]
zt, zo = Image.open(r["compare"]["theirs_zoom"]), Image.open(r["compare"]["ours_zoom"])
assert zt.size == zo.size and zt.size[0] == 480 and zt.size[0] > 1.6 * zt.size[1], ("zoom strips", zt.size, zo.size)

assert {c["id"] for c in r["chips"]} >= {"fill", "box", "case", "font", "position"}
assert all(0 <= c["conf"] <= 1 for c in r["chips"]) and r["base"] in TP.builtin_ids()
# applying the result as editor ops (what "Pakai gaya ini" sends) works on a real doc
tl = None
if _common.have(MEDIA_49):
    clip = {"name": MEDIA_49.name, "path": str(MEDIA_49), "start": 0.0, "end": 48.9, "in": 0.0, "out": 48.9}
    seq = {"id": "sty", "name": "AC Stylist Test", "fps": 120, "width": 1080, "height": 1920, "duration": 48.9,
           "player": 21.0, "video": [{"index": 0, "name": "V1", "clips": [clip]}],
           "audio": [{"index": 0, "name": "A1", "clips": [dict(clip)]}]}
    tl = Timeline.from_json(seq)
    rd = ACTIONS["doc"]({"tool": "captions", "seq": seq, "workdir": str(wd),
                         "params": {"template": "hormozi_kuning", "transcribe": False}}, em)
    d = M.load(rd["doc"])
    M.apply_ops(d, [{"op": "template", "id": r["base"]}, {"op": "doc_style", "style": r["style"], "replace": True}])
    M.repaginate(d)
    tpl0 = M.base_template(d)
    assert tpl0["box"]["enabled"] and tpl0["style"]["fill"] == "#111111" and d["pages"]
    # use the current frame of the timeline (screen recording: whatever text it finds, or a clean error)
    try:
        rf = ACTIONS["style_from_image"]({"tool": "captions", "seq": seq, "workdir": str(wd),
                                          "params": {"frame": True, "ai": False, "render": False}}, em)
        assert rf["image"].endswith(".png") and rf["compare"] is None
    except EngineError as e:
        assert e.code == "NO_CAPTION_FOUND", e.code

# canned AI answer: source mixed, AI semantics + measured colours
_orig = S.analyze_ai
S.analyze_ai = lambda im, use_cache=True, emit=None, bbox=None: (
    dict(ai_raw, text_color="#101010", text="Rahasia cuan", active_word_text="cuan", background="per_line",
         background_color="#FFFFFF", case="sentence"), None, {"source": "ai", "profile": "fake", "model": "fake-v"})
try:
    r2 = ACTIONS["style_from_image"]({"tool": "captions", "seq": None, "workdir": str(wd),
                                      "params": {"image": "data:image/png;base64,"
                                                 + __import__("base64").b64encode(png.read_bytes()).decode()}}, em)
finally:
    S.analyze_ai = _orig
assert r2["source"] == "mixed" and r2["ai"]["used"] and r2["fidelity"] in ("tinggi", "sedang") and r2["text"]
assert r2["style"]["style"]["fill"] == "#111111", "measured fill (deltaE < 30 from the AI's #101010)"
assert r2["style"]["font"]["case"] == "sentence" and r2["style"]["font"]["family"] in ("Anton", "Barlow Condensed Black")
assert any(c["src"] == "ai+lokal" for c in r2["chips"])
for bad_p, code in (({"path": str(tmp / "nope.png")}, "NO_IMAGE"), ({"image": "data:image/png;base64,QUJD"}, "BAD_IMAGE"),
                    ({}, "BAD_PARAMS")):
    try:
        ACTIONS["style_from_image"]({"tool": "captions", "seq": None, "workdir": str(wd), "params": dict(bad_p, ai=False)}, em)
        raise AssertionError(f"expected {code}")
    except EngineError as e:
        assert e.code == code, (e.code, code)
tiny = tmp / "tiny.png"
Image.new("RGB", (30, 20), (0, 0, 0)).save(tiny)
try:
    ACTIONS["style_from_image"]({"tool": "captions", "seq": None, "workdir": str(wd), "params": {"path": str(tiny)}}, em)
    raise AssertionError("tiny image accepted")
except EngineError as e:
    assert e.code == "BAD_IMAGE"

# job language: Indonesian by default, English when the job says lang "en" (cli.execute wraps jobs in i18n.using)
from ac import i18n  # noqa: E402
lab = {c["id"]: c["label"] for c in r["chips"]}
assert lab["fill"] == "Warna teks" and r["summary"].startswith("Gaya dari gambar") and r["template"]["name"] == "Gaya dari gambar", (lab, r["summary"])
with i18n.using("en"):
    re_ = ACTIONS["style_from_image"]({"tool": "captions", "seq": None, "workdir": str(wd), "lang": "en",
                                       "params": {"path": str(png), "ai": False, "render": False}}, em)
    lab = {c["id"]: c["label"] for c in re_["chips"]}
    assert lab["fill"] == "Text color" and lab["box"] == "Background" and lab["font"] == "Font", lab
    assert re_["summary"].startswith("Style from image: ") and "attributes (no AI, lower accuracy)" in re_["summary"], re_["summary"]
    assert re_["template"]["name"] == "Style from image" and re_["template"]["description"] == "Copied from an image (no AI)"
    try:
        ACTIONS["style_from_image"]({"tool": "captions", "seq": None, "workdir": str(wd), "params": {"ai": False}}, em)
        raise AssertionError("expected BAD_PARAMS")
    except EngineError as e:
        assert e.code == "BAD_PARAMS" and e.msg == "No image yet." and "Paste (Ctrl+V)" in (e.hint or ""), (e.msg, e.hint)
print("job language en: chips, summary, template name, errors in English")
print(f"style_from_image ok ({time.perf_counter() - T0:.1f}s)")

# ====================================================================== brand kit
assert BK.load() == {"v": 1, "active": None, "kits": []}
try:
    BK.clean({"name": "  "})
    raise AssertionError("empty name accepted")
except EngineError as e:
    assert e.code == "BAD_KIT"
k, w = BK.clean({"name": "Toko A", "colors": {"primary": "zz", "accent": "#0f0"}, "emoji": "meledak",
                 "words": "TokoKita, tokokita; Toko Cuan", "fonts": {"heading": "Font Hantu"}})
assert k["colors"]["primary"] == BK.DEFAULT_COLORS["primary"] and k["colors"]["accent"] == "#00FF00"
assert k["emoji"] == "sedikit" and k["words"] == ["TokoKita", "Toko Cuan"] and len(w) == 2
run = lambda params, seqj=None: ACTIONS["brandkit"]({"tool": "captions", "seq": seqj, "workdir": str(wd),  # noqa: E731
                                                       "params": params}, em)
g = run({"op": "get"})
assert g["kits"] == [] and g["active"] is None and len(g["tones"]) == 5 and len(g["emoji_levels"]) == 3
s1 = run({"op": "set", "kit": {"name": "TokoKita", "colors": {"primary": "#00A86B", "accent": "#FFCC00",
                                                               "text": "#FFFFFF", "background": "#0B3D2E"},
                               "fonts": {"heading": "Rubik Black", "body": "Nunito ExtraBold"},
                               "words": ["TokoKita", "reseller"], "emoji": "none", "tone": "semangat",
                               "logo": "C:\\logo.png"}})
assert s1["active"] == "tokokita" and s1["kit"]["fonts"]["heading"] == "Rubik Black" and not s1["warnings"]
s2 = run({"op": "set", "kit": {"name": "TokoKita", "colors": {"primary": "#123456"}}})
assert s2["kit"]["id"] == "tokokita_2" and s2["active"] == "tokokita", "same name -> new id, active unchanged"
s3 = run({"op": "set", "kit": {"id": "tokokita_2", "name": "Kanal Kedua", "tone": "profesional"}})
assert s3["kit"]["colors"]["primary"] == "#123456" and s3["kit"]["name"] == "Kanal Kedua", "partial update keeps fields"
assert run({"op": "activate", "id": "tokokita_2"})["active"] == "tokokita_2"
assert run({"op": "activate", "id": "tokokita"})["active"] == "tokokita"
assert json.loads((tmp / "roaming" / "Klipora" / "brandkit.json").read_text(encoding="utf-8"))["active"] == "tokokita"
dl = run({"op": "delete", "id": "tokokita_2"})
assert [x["id"] for x in dl["kits"]] == ["tokokita"]
try:
    run({"op": "delete", "id": "ghost"})
    raise AssertionError("ghost kit deleted")
except EngineError as e:
    assert e.code == "BAD_KIT"

if tl is not None:
    ap = run({"op": "apply"})
    ops = ap["ops"]
    assert ops[0]["op"] == "doc_style" and ops[0]["replace"] is True
    assert ap["glossary_added"] == ["TokoKita", "reseller"] and ap["rebuild"] is True
    assert {"op": "clear_emoji"} in ops, "emoji policy none"
    d = M.load(rd["doc"])
    M.apply_ops(d, ops)
    M.repaginate(d)
    t = M.base_template(d)
    assert t["font"]["family"] == "Rubik Black" and t["style"]["fill"] == "#FFFFFF"
    assert t["highlight"]["color"] == "#FFCC00", "active word colour = accent"
    assert set(t["emphasis"]["keywords"]) >= {"tokokita", "reseller"} and t["emphasis"]["color"] == "#00A86B"
    assert t["emphasis"]["scale"] == BK.TONE_SCALE["semangat"]
    assert d["params"]["glossary"] == ["TokoKita", "reseller"]
    # brand words are highlighted by the template emphasis rule
    rw = M.render_words(d, L.prep_template(dict(t)))
    hit = [x for x in rw if x.raw.strip(".,!?").lower() == "reseller"]
    assert hit and L.wstyle(hit[0], t).get("color") == "#00A86B", "brand word coloured"
    # a boxed template gets the kit background; light box + light text -> readable text
    d2 = M.load(rd["doc"])
    M.apply_ops(d2, [{"op": "template", "id": "boxed"}])
    ops2, info2 = BK.apply_ops(BK.clean({"name": "Terang", "colors": {"background": "#FFFFFF", "text": "#FFFFFF"}})[0], d2)
    st2 = ops2[0]["style"]
    assert st2["box"]["color"] == "#FFFFFF" and st2["style"]["fill"] == "#111111"
    # commit = applied in the engine
    cm = run({"op": "apply", "commit": True})
    assert cm["doc"] and M.load(rd["doc"])["params"]["glossary"] == ["TokoKita", "reseller"]

    # ------------------------------------------------------------ brand_emphasis (no AI: rules)
    ACTIONS["doc"]({"tool": "captions", "seq": seq, "workdir": str(wd), "params": {"rebuild": True,
                    "template": "hormozi_kuning", "transcribe": False, "glossary": ["TokoKita", "reseller"]}}, em)
    run({"op": "set", "kit": {"id": "tokokita", "name": "TokoKita", "emoji": "sedikit"}})
    e1 = ACTIONS["brand_emphasis"]({"tool": "captions", "seq": None, "workdir": str(wd), "params": {"ai": False}}, em)
    assert e1["source"] == "fallback" and e1["items"] and "tanpa AI" in e1["summary"]
    whys = {it["why"] for it in e1["items"]}
    assert "brand" in whys and whys <= {"brand", "angka", "kata"}, whys
    br = [it for it in e1["items"] if it["why"] == "brand"]
    assert all(it["style"]["color"] == "#00A86B" for it in br), "brand words in the primary colour"
    assert all(it["text"].lower().strip(".,") not in BK.STOP for it in e1["items"])
    assert len(e1["ops"]) == len(e1["items"]) and all(o["op"] == "style" for o in e1["ops"])
    d = M.load(rd["doc"])
    M.apply_ops(d, e1["ops"])
    M.repaginate(d)
    assert sum(1 for w in d["words"] if (w.get("style") or {}).get("color")) == len(e1["items"])
    pages_with = {it["page"] for it in e1["items"] if it["why"] != "brand"}
    assert len(pages_with) == sum(1 for it in e1["items"] if it["why"] != "brand"), "max 1 per page (+ brand words)"
    # canned AI answer: AI picks + emoji, thinned to one per 15 s by the 'sedikit' policy
    from ac.ai import tasks as AT  # noqa: E402
    _run = AT.run

    def fake_run(task, rows, **kw):
        assert task == "emphasis" and rows and rows[0]["text"].startswith(" ")
        lines = [{"l": i, "words": [i], "hl": [i], "emoji": "🔥" if i % 4 == 0 else "", "zoom": False}
                 for i in range(0, len(rows), 3)]
        return {"source": "ai", "result": {"lines": lines}, "warnings": [], "stats": {}, "model": "fake"}
    AT.run = fake_run
    try:
        e2 = ACTIONS["brand_emphasis"]({"tool": "captions", "seq": None, "workdir": str(wd),
                                        "params": {"max_per_page": 1}}, em)
    finally:
        AT.run = _run
    assert e2["source"] == "ai" and any(it["why"] == "ai" for it in e2["items"])
    emo = [o for o in e2["ops"] if o["style"].get("emoji")]
    ts = sorted(it["t0"] for it in e2["items"] if it.get("emoji") and it["on"])
    assert emo and len(emo) <= 1 + int((ts[-1] - ts[0]) // 15) if ts else True, "emoji policy sedikit = >= 15 s apart"
    # commit applies everything
    e3 = ACTIONS["brand_emphasis"]({"tool": "captions", "seq": None, "workdir": str(wd),
                                    "params": {"ai": False, "commit": True, "brand": False}}, em)
    assert e3["kit"] is None and e3["doc"] and all(it["why"] != "brand" for it in e3["items"])
    print(f"  emphasis: rules {e1['stats']}, canned AI {e2['stats']}, emoji ops {len(emo)}")
print(f"brand kit ok ({time.perf_counter() - T0:.1f}s)")

# ====================================================================== live AI round trip (<= 4 requests, cached)
if LIVE:
    calls = 0
    res = []
    for tid, W, H in (("hormozi_kuning", 1080, 1920), ("boxed", 1080, 1920), ("tutorial_bersih", 1920, 1080),
                      ("karaoke", 1920, 1080)):
        png = tmp / f"rt_{tid}_{W}.png"
        im = Image.open(png).convert("RGB")
        loc = S.analyze_local(im)
        t1 = time.perf_counter()
        obj, why, meta = S.analyze_ai(im, bbox=(loc.get("_block") or {}).get("bbox"))
        calls += meta.get("source") == "ai"
        ex = expect(tid, W, H)
        if obj is None:
            res.append((tid, W, "AI gagal", why))
            continue
        a = S.norm_ai(obj)
        merged, notes = S.merge(loc, a)
        base, st = S.to_style(merged, W, H, cat)
        ok = {"fill_ai": a.get("fill") and S.delta_e(a["fill"]["value"], ex["fill"]) <= 25,
              "fill_merged": S.delta_e(st["style"]["fill"], ex["fill"]) <= 15,
              "box": bool(st["box"].get("enabled")) == ex["box"],
              "case": (st["font"].get("case") == "upper") == ex["upper"],
              "position": abs(st["layout"].get("y", -99) - ex["y"]) <= 8}
        res.append((tid, W, meta.get("source"), f"{time.perf_counter() - t1:.1f}s", ok, obj.get("text")))
    for r_ in res:
        print("  live:", r_)
    print(f"live AI round trip: {calls} new request(s)")
    assert calls <= 4

_common.cleanup("captions_stylist")
print(f"OK test_captions_stylist ({time.perf_counter() - T0:.1f}s)")
