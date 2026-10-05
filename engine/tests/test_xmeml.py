"""ac.xmeml: multi-source writer, legacy single-source equivalence, cut() vs the Premiere-verified lab outputs."""
import json
import xml.etree.ElementTree as ET

import _common
from _common import LAB, MEDIA_2M, MEDIA_49

from ac import xmeml as X

# ---- writer with an injected probe (no media needed)
INFO = {"a.mp4": {"duration": 60.0, "fps": 120.0, "width": 2292, "height": 960, "has_video": True, "has_audio": True,
                  "sample_rate": 48000, "channels": 2},
        "b.wav": {"duration": 30.0, "fps": 0, "width": 0, "height": 0, "has_video": False, "has_audio": True,
                  "sample_rate": 44100, "channels": 1}}
xml = X.build_sequence("Tes (AutoCut)", [
    {"path": "a.mp4", "start": 0.0, "src_in": 1.0, "src_out": 2.5},
    {"path": "a.mp4", "start": 1.5, "src_in": 10.0, "src_out": 12.0, "enabled": False},
    {"path": "b.wav", "start": 0.5, "src_in": 0.0, "src_out": 3.0, "atrack": 1},
    {"path": "a.mp4", "start": 3.5, "src_in": 20, "src_out": 21, "vtrack": 1, "audio": False,
     "filters": ["<filter><effect><name>Basic Motion</name><effectid>basic</effectid></effect></filter>"]},
    {"path": "a.mp4", "start": 9, "src_in": 5, "src_out": 5},            # empty: skipped
], fps=120, width=1920, height=1080, probe=lambda p: INFO[p],
    markers=[{"t": 1.0, "name": "Bab 1"}, {"t": 2.0, "end": 3.0, "name": "Bab 2", "comment": "x"}])
s = X.summary(xml)
assert s["name"] == "Tes (AutoCut)" and s["fps"] == 120.0 and s["duration"] == 540, s["duration"]
assert s["video"][0] == [(0, 180, 120, 300, "TRUE"), (180, 420, 1200, 1440, "FALSE")], s["video"]
assert s["video"][1] == [(420, 540, 2400, 2520, "TRUE")]
assert s["audio"][0] == [(0, 180, 120, 300, "TRUE"), (180, 420, 1200, 1440, "FALSE")]
assert s["audio"][1] == [(60, 420, 0, 360, "TRUE")]
root = ET.fromstring(xml.split("\n", 2)[2])
files = [f for f in root.iter("file") if len(f)]
assert sorted(f.findtext("name") for f in files) == ["a.mp4", "b.wav"], "each file fully defined once"
assert root.find(".//file[@id='file-2']/rate/timebase").text in ("0", "120")  # wav: no video rate needed
links = root.findall(".//clipitem[@id='clipitem-v1-1']/link")
assert [ln.findtext("linkclipref") for ln in links] == ["clipitem-v1-1", "clipitem-a1-1"]
assert root.find(".//clipitem[@id='clipitem-a2-1']/link") is None        # audio-only clip: no links
assert root.find(".//clipitem[@id='clipitem-v2-1']/filter/effect/name").text == "Basic Motion"
mk = root.findall("sequence/marker")
assert [(m.findtext("name"), m.findtext("in"), m.findtext("out")) for m in mk] == [("Bab 1", "120", "-1"), ("Bab 2", "240", "360")]

# ntsc rates
for fps, tb, ntsc in ((29.97, "30", "TRUE"), (30000 / 1001, "30", "TRUE"), (23.976, "24", "TRUE"), (59.94, "60", "TRUE"),
                      (25, "25", "FALSE"), (120, "120", "FALSE")):
    r = X._rate(ET.Element("x"), fps)
    assert (r.findtext("timebase"), r.findtext("ntsc")) == (tb, ntsc), (fps, r.findtext("timebase"), r.findtext("ntsc"))

# ---- legacy to_xmeml on real media (frames as the old writer) + keep_clips helper
if _common.have(MEDIA_49):
    from fractions import Fraction
    from ac.media import probe
    info = probe(MEDIA_49)
    legacy = {"duration": info["duration"], "fps": Fraction(info["fps_frac"]), "width": 2292, "height": 960,
              "has_video": True, "sample_rate": info["sample_rate"], "channels": 2}
    lx = X.summary(X.to_xmeml(MEDIA_49, legacy, [(0.0, 1.5), (3.0, 4.25)]))
    assert lx["video"][0] == [(0, 180, 0, 180, "TRUE"), (180, 330, 360, 510, "TRUE")] and lx["duration"] == 330
    ks = X.keep_clips(MEDIA_49, [[1, 2], [5, 5], [7, 9]])
    assert [(c["start"], c["src_in"], c["src_out"]) for c in ks] == [(0.0, 1, 2), (1.0, 7, 9)]
    if _common.have(MEDIA_2M):
        multi = X.build_sequence("Dua sumber", [{"path": str(MEDIA_49), "start": 0, "src_in": 0, "src_out": 2},
                                                {"path": str(MEDIA_2M), "start": 2, "src_in": 10, "src_out": 13}],
                                 120, 2292, 960)
        ms = X.summary(multi)
        assert ms["video"][0] == [(0, 240, 0, 240, "TRUE"), (240, 600, 1200, 1560, "TRUE")]
        assert multi.count("<pathurl>") == 2

# ---- cut(): identical to the outputs that were verified in Premiere 26.2.2 (proto/lab)
for src, rng, exp, name in (("rich3.xml", "r3_ranges.json", "rich3_cut.xml", "LAB rich3 xmlcut"),
                            ("kfxml.xml", "kf_ranges.json", "kfxml_cut.xml", "LAB kfxml cut")):
    if not _common.have(LAB / src, LAB / exp):
        continue
    tmp = _common.scratch("xmeml")
    info = X.cut_file(LAB / src, tmp / exp, json.loads((LAB / rng).read_text()), name)
    got = (tmp / exp).read_text(encoding="utf-8")
    want = (LAB / exp).read_text(encoding="utf-8")
    assert got == want, f"{src}: differs from verified lab output"
    assert info["warnings"] == [] and info["removedFrames"] > 0
    # same through the panel's job contract: {tool: "xmeml", action: "cut", params: {xml, out, ranges, name}}
    from ac.progress import ListEmitter
    from ac.tools import xmeml as XT
    res = XT.cut({"params": {"xml": str(LAB / src), "out": str(tmp / ("job_" + exp)),
                             "ranges": json.loads((LAB / rng).read_text()), "name": name}}, ListEmitter())
    assert (tmp / ("job_" + exp)).read_text(encoding="utf-8") == want and res["path"].endswith("job_" + exp)
    if MEDIA_49.is_file():
        built = XT.build({"workdir": str(tmp), "params": {
            "name": "Vertikal", "fps": 30, "width": 1080, "height": 1920,
            "clips": [{"path": str(MEDIA_49), "start": 0, "src_in": 2, "src_out": 4}]}}, ListEmitter())
        bs = X.summary(open(built["path"], encoding="utf-8").read())
        assert built["plan"]["kind"] == "xml_import" and bs["video"][0] == [(0, 60, 60, 120, "TRUE")], bs
_common.cleanup("xmeml")

# big ranges list (300 cuts) on bigXML: fast, durations add up
if _common.have(LAB / "bigXML.xml", LAB / "big_ranges.json"):
    import time
    tree = ET.parse(LAB / "bigXML.xml")
    seq = tree.getroot().find("sequence")
    total = int(seq.findtext("duration"))
    rng = json.loads((LAB / "big_ranges.json").read_text())
    t0 = time.perf_counter()
    info = X.cut(tree, rng)
    ms = (time.perf_counter() - t0) * 1000
    assert info["newDurationFrames"] + info["removedFrames"] == total and ms < 2000, (info, ms)
    print(f"cut {len(rng)} ranges in {ms:.0f} ms")
print("ok")
