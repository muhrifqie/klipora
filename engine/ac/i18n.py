"""User-facing engine text in the panel's UI language (Bahasa Indonesia "id", default, or English "en").

Strings live in engine/ac/locales/{id,en}.json: flat keys -> text, or plural objects {"one": "...", "other": "..."}.

    from ..i18n import tr, dec, secs
    emit.plan([("audio", tr("stage.audio"), 0.2), ...])
    raise EngineError("NO_AUDIO", tr("err.no_audio.msg"), tr("err.no_audio.hint"))
    tr("silence.found", n=16)            # plural form picked by n; integers formatted per language (1.500 / 1,500)
    secs(3.7) -> "3,7 dtk" | "3.7 s"     dec(0.5, 2) -> "0,50" | "0.50"

Which language: the job's "lang" field (the panel adds it to every job; cli.execute wraps the job in using()),
else the AC_LANG environment variable (the panel sets it for every engine process: health, `cli.py ai`, worker),
else "id". The current language is process-global (the worker runs one job at a time), so helper threads that
build messages see the same language. Missing keys fall back to Indonesian, then to the key itself.
"""
from __future__ import annotations

import contextlib
import json
import os
import re
from pathlib import Path

LANGS = ("id", "en")
DEFAULT = "id"
_DIR = Path(__file__).resolve().parent / "locales"
_dicts: dict[str, dict] = {}
_lang: str | None = None
_missing: set[str] = set()


def norm(lang) -> str:
    """'en_US' / 'EN' / 'en-GB' -> 'en'; unknown or empty -> 'id'."""
    s = str(lang or "").strip().lower().replace("-", "_").split("_")[0]
    if s == "in":
        s = "id"
    return s if s in LANGS else DEFAULT


def get_lang() -> str:
    if _lang:
        return _lang
    env = os.environ.get("AC_LANG")
    return norm(env) if env else DEFAULT


def set_lang(lang) -> str:
    """Set the process language (None = back to AC_LANG / default). Returns the effective language."""
    global _lang
    _lang = norm(lang) if lang else None
    return get_lang()


@contextlib.contextmanager
def using(lang):
    """Run a block (one job) in `lang`; None keeps the current language."""
    global _lang
    prev = _lang
    if lang:
        _lang = norm(lang)
    try:
        yield get_lang()
    finally:
        _lang = prev


def _dict(lang: str) -> dict:
    d = _dicts.get(lang)
    if d is None:
        try:
            d = json.loads((_DIR / f"{lang}.json").read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            d = {}
        _dicts[lang] = d
    return d


def reload():
    _dicts.clear()
    _missing.clear()


def has(key: str) -> bool:
    return key in _dict(get_lang()) or key in _dict(DEFAULT)


def raw(key: str, lang: str | None = None):
    """The locale entry (str or plural dict) or None."""
    lang = norm(lang) if lang else get_lang()
    v = _dict(lang).get(key)
    if v is None and lang != DEFAULT:
        v = _dict(DEFAULT).get(key)
    return v


def _plural(lang: str, n) -> str:
    if lang == "en" and isinstance(n, (int, float)) and abs(n) == 1:
        return "one"
    return "other"   # Indonesian has no plural inflection


def fmt_num(v, lang: str | None = None) -> str:
    lang = norm(lang) if lang else get_lang()
    if isinstance(v, bool):
        return str(v)
    if isinstance(v, int) or (isinstance(v, float) and v.is_integer() and abs(v) < 1e15):
        return int_(int(v), lang)
    if isinstance(v, float):
        s = repr(v)
        return s if lang == "en" else s.replace(".", ",")
    return str(v)


_PH = re.compile(r"\{(\w+)\}")


def tr(key: str, lang: str | None = None, **vars) -> str:
    """Translated text for `key` with {name} placeholders filled from vars (numbers formatted per language)."""
    lang = norm(lang) if lang else get_lang()
    v = raw(key, lang)
    if v is None:
        if key not in _missing:
            _missing.add(key)
        return key
    if isinstance(v, dict):
        n = vars.get("n", vars.get("count"))
        v = v.get(_plural(lang, n), v.get("other", key))
    v = str(v)
    if not vars:
        return v

    def sub(m):
        k = m.group(1)
        if k not in vars:
            return m.group(0)
        x = vars[k]
        return fmt_num(x, lang) if isinstance(x, (int, float)) else ("" if x is None else str(x))

    return _PH.sub(sub, v)


t = tr


def missing() -> list[str]:
    return sorted(_missing)


# ---------------------------------------------------------------- number / time formatting

def dec(x, d: int = 1, lang: str | None = None) -> str:
    """Fixed decimals with the language's separator: dec(0.5, 2) -> '0,50' (id) / '0.50' (en)."""
    lang = norm(lang) if lang else get_lang()
    s = f"{float(x or 0):.{d}f}"
    return s if lang == "en" else s.replace(".", ",")


def int_(n, lang: str | None = None) -> str:
    """Thousands separator: 1500000 -> '1.500.000' (id) / '1,500,000' (en)."""
    lang = norm(lang) if lang else get_lang()
    s = f"{int(round(float(n or 0))):,}"
    return s if lang == "en" else s.replace(",", ".")


def unit_sec(lang: str | None = None) -> str:
    return tr("unit.sec", lang)


def secs(s, lang: str | None = None) -> str:
    """Like the panel's AC.util.dtk: '3,7 dtk' / '12 dtk' / '1 mnt 30 dtk' (en: '3.7 s' / '1 min 30 s')."""
    lang = norm(lang) if lang else get_lang()
    s = max(0.0, float(s or 0))
    us, um = tr("unit.sec", lang), tr("unit.min", lang)
    if s >= 90:
        tot = int(round(s))
        m, r = divmod(tot, 60)
        return f"{m} {um}" + (f" {r} {us}" if r else "")
    return f"{dec(s, 1 if s < 10 else 0, lang)} {us}"


def mmss(s) -> str:
    """0:49 / 1:02:03 (same in every language)."""
    s = max(0.0, float(s or 0))
    tot = int(round(s))
    h, rem = divmod(tot, 3600)
    m, r = divmod(rem, 60)
    return f"{h}:{m:02d}:{r:02d}" if h else f"{m}:{r:02d}"
