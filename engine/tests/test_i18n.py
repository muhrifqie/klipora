"""ac.i18n: locale files (id/en same keys + placeholders), tr() interpolation/plurals/fallback, number formatting,
per-job language in cli.execute, AC_LANG env, and the AutoCutBOT -> Klipora data folder migration.
Run: python engine/tests/test_i18n.py"""
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from _common import scratch  # noqa: E402

tmp = scratch("i18n")
os.environ["APPDATA"] = str(tmp / "roaming")
os.environ["LOCALAPPDATA"] = str(tmp / "local")
os.environ.pop("AC_LANG", None)

from ac import i18n as I  # noqa: E402
from ac import util as U  # noqa: E402

# ---------------------------------------------------------------- locale files
D = Path(I.__file__).parent / "locales"
ids = json.loads((D / "id.json").read_text(encoding="utf-8"))
ens = json.loads((D / "en.json").read_text(encoding="utf-8"))
assert set(ids) == set(ens), sorted(set(ids) ^ set(ens))[:20]


def ph(v):
    s = v if isinstance(v, str) else " ".join(v.values())
    return sorted(set(re.findall(r"\{\w+\}", s)))


for k in ids:
    assert ph(ids[k]) == ph(ens[k]), (k, ids[k], ens[k])
    for v in (ids[k], ens[k]):
        assert isinstance(v, str) or (isinstance(v, dict) and "other" in v), k
    assert "—" not in json.dumps(ens[k], ensure_ascii=False), ("em dash", k)

# ---------------------------------------------------------------- tr / formatting
assert I.get_lang() == "id"
assert I.tr("unit.sec") == "dtk" and I.tr("unit.sec", "en") == "s"
assert I.tr("no.such.key") == "no.such.key"
with I.using("en"):
    assert I.get_lang() == "en" and I.tr("common.cancelled") == "Cancelled."
    assert I.secs(3.7) == "3.7 s" and I.secs(95) == "1 min 35 s" and I.dec(0.5, 2) == "0.50" and I.int_(1500000) == "1,500,000"
assert I.get_lang() == "id"
assert I.secs(3.7) == "3,7 dtk" and I.secs(95) == "1 mnt 35 dtk" and I.dec(0.5, 2) == "0,50" and I.int_(1500000) == "1.500.000"
assert I.norm("en_US") == "en" and I.norm("in_ID") == "id" and I.norm("fr") == "id" and I.norm(None) == "id"
I._dicts["en"] = dict(I._dict("en"), **{"t.n": {"one": "{n} pause", "other": "{n} pauses"}})
I._dicts["id"] = dict(I._dict("id"), **{"t.n": "{n} jeda"})
assert I.tr("t.n", "en", n=1) == "1 pause" and I.tr("t.n", "en", n=1500) == "1,500 pauses" and I.tr("t.n", n=2) == "2 jeda"
I.reload()
os.environ["AC_LANG"] = "en"
assert I.get_lang() == "en" and U.Cancelled().msg == "Cancelled."
os.environ.pop("AC_LANG")
assert U.Cancelled().msg == "Dibatalkan."

# ---------------------------------------------------------------- per-job language through cli.execute
import cli  # noqa: E402
from ac.progress import Emitter  # noqa: E402
ev = []
rc = cli.execute({"tool": "nope_tool", "action": "x", "lang": "en", "params": {}}, Emitter(sink=ev.append))
assert rc == 1 and ev[-1]["ev"] == "error" and I.get_lang() == "id", ev[-1]

# ---------------------------------------------------------------- migration AutoCutBOT -> Klipora
os.environ["APPDATA"] = str(tmp / "roaming2")   # fresh base: the cli run above already created roaming/Klipora
old = tmp / "roaming2" / "AutoCutBOT"
(old / "sub").mkdir(parents=True)
(old / "settings.json").write_text('{"ai": false}', encoding="utf-8")
(old / "sub" / "a.bin").write_bytes(b"\x00\xff")
(old / "x.lock").write_text("1", encoding="utf-8")
U._MIGRATED.clear()
new = U.appdata_dir()
assert new == tmp / "roaming2" / "Klipora", new
assert (new / "settings.json").read_text(encoding="utf-8") == '{"ai": false}' and (new / "sub" / "a.bin").read_bytes() == b"\x00\xff"
assert not (new / "x.lock").exists() and (new / "MIGRATED_FROM.txt").is_file() and old.is_dir()   # old kept
assert U.settings_path() == new / "settings.json" and U.setting("ai") is False
assert U.local_dir() == tmp / "local" / "Klipora"        # nothing to migrate: plain new folder
print("test_i18n OK")
