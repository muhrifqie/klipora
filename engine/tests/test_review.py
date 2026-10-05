"""ac.review: build, stable ids, save/load/validate, edits, carry-over, selected ranges."""
import json

import _common

from ac import review as RV
from ac.util import EngineError

tmp = _common.scratch("review")
items = [RV.item(11.5, 15.17, "gap", conf=0.9, label="Jeda 3,7 dtk", ctx={"pre": "dari membuka dulu", "post": "ini dia,"}),
         RV.item(2.0, 2.4, "filler", on=False, conf=0.55, word="eee"),
         RV.item(11.5, 11.9, "gap", note={"type": "guard"}),   # same kind + t0: shorter one sorts first -> g1150
         RV.item(20.0, 21.0, "repeat", cut=[20.05, 20.95])]
doc = RV.new("silence", items, 48.925, seq={"id": "s1", "name": "Seq"}, params={"preset": "natural"})
ids = [it["id"] for it in doc["items"]]
assert ids == ["f200", "g1150", "g1150-2", "r2000"], ids
assert doc["timebase"] == "sequence" and doc["seq"] == {"id": "s1", "name": "Seq"} and doc["v"] == 1
assert doc["stats"]["n"] == 4 and doc["stats"]["on"] == 3
assert RV.validate(doc) == []
p = RV.save(doc, RV.path_for(tmp, "silence"))
assert p.name == "silence_review.json"
loaded = RV.load(p)
assert loaded == json.loads(json.dumps(doc))
# re-run gives the same ids for the same findings
again = RV.new("silence", [RV.item(11.5, 15.17, "gap"), RV.item(2.0, 2.4, "filler")], 48.925)
assert [it["id"] for it in again["items"]] == ["f200", "g1150"]

# selected ranges: cut overrides t0..t1, overlapping merged
assert RV.selected_ranges(doc) == [[11.5, 15.17], [20.05, 20.95]]
assert RV.selected_ranges(doc, kinds={"repeat"}) == [[20.05, 20.95]]

# panel edits flip `on` (touched) and are applied by id
edited = json.loads(json.dumps(doc))
edited["items"][0]["on"] = True                     # filler on
edited["items"][1]["on"] = False                    # gap off
n = RV.apply_edits(doc, edited)
assert n == 2 and doc["items"][0]["on"] and doc["items"][0]["touched"] and not doc["items"][1]["on"]
assert RV.apply_edits(doc, {"r2000": False, "nope": True}) == 1
assert RV.summary(doc) == {"n": 4, "on": 2, "sec_on": 4.07}, RV.summary(doc)   # f200 + g1150-2 (11.5-15.17)

# carry-over keeps manual choices after a re-run
fresh = RV.new("silence", [RV.item(11.5, 15.17, "gap"), RV.item(2.0, 2.4, "filler", on=False),
                           RV.item(30, 31, "gap")], 48.925)
assert RV.carry_over(fresh, doc) == 2
by = {it["id"]: it["on"] for it in fresh["items"]}
assert by == {"f200": True, "g1150": False, "g3000": True}, by

# validation / load errors
bad = {"tool": "x", "timebase": "frames", "duration": 1, "items": [{"id": "a", "t0": 2, "t1": 1, "kind": "k", "on": 1},
                                                                    {"id": "a", "t0": 0, "t1": 1, "kind": "k"}]}
errs = RV.validate(bad)
assert any("timebase" in e for e in errs) and any("t1 < t0" in e for e in errs) and any("duplicate" in e for e in errs)
assert any("missing 'on'" in e for e in errs)
for target in (tmp / "missing.json",):
    try:
        RV.load(target)
        raise AssertionError("should raise")
    except EngineError as e:
        assert e.code == "BAD_REVIEW"
try:
    RV.from_job({"tool": "x"})
    raise AssertionError("should raise")
except EngineError as e:
    assert e.code == "BAD_JOB"
assert RV.from_job({"review": str(p)})["tool"] == "silence"
_common.cleanup("review")
print("ok")
