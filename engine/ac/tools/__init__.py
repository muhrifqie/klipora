"""Tool registry with lazy discovery: every module `ac/tools/<id>.py` (and the `ac.captions` package as tool
"captions") is a tool when it defines

    ACTIONS = {"analyze": fn(job, emit) -> dict, "apply": fn(job, emit) -> dict, ...}
    TITLE = "..."                     # optional, Indonesian fallback title
    DESCRIPTION = "..."               # optional, Indonesian fallback description

`describe()` shows the title/description in the current UI language from the locale keys tool.<id>.title /
tool.<id>.desc (engine/ac/locales), falling back to TITLE/DESCRIPTION when a key is missing.
Modules are imported only when used (`get`) or listed (`describe`), so one broken tool never breaks the others.
"""
from __future__ import annotations

import importlib
import pkgutil
import traceback
from pathlib import Path

from ..i18n import has, tr
from ..util import EngineError

_PKG_DIR = Path(__file__).resolve().parent
_CAPTIONS_DIR = _PKG_DIR.parent / "captions"


def names():
    """Tool ids available on disk (no import): ac/tools/*.py without a leading underscore, plus 'captions'
    when the ac/captions package exists."""
    out = sorted(m.name for m in pkgutil.iter_modules([str(_PKG_DIR)]) if not m.name.startswith("_"))
    if (_CAPTIONS_DIR / "__init__.py").is_file():
        out.append("captions")
    return out


def _module_name(tool):
    if tool == "captions":
        return "ac.captions"
    return f"ac.tools.{tool}"


def get(tool):
    """Import and return the tool module. EngineError('NO_TOOL') when unknown or broken."""
    tool = str(tool or "")
    if not tool.replace("_", "").isalnum() or tool not in names():
        raise EngineError("NO_TOOL", tr("tool.err.unknown", tool=tool or tr("tool.err.empty")), tr("tool.err.updateHint"))
    try:
        mod = importlib.import_module(_module_name(tool))
    except Exception as e:  # noqa: BLE001  - a tool with a bug must not take the engine down
        raise EngineError("TOOL_IMPORT", tr("tool.err.import", tool=tool, detail=f"{type(e).__name__}: {e}"),
                          traceback.format_exc(limit=3)[-800:]) from e
    if not isinstance(getattr(mod, "ACTIONS", None), dict):
        raise EngineError("NO_TOOL", tr("tool.err.noActions", tool=tool))
    return mod


def action(tool, name):
    """The callable for tool/action. EngineError('NO_ACTION') when the tool lacks it."""
    mod = get(tool)
    fn = mod.ACTIONS.get(name)
    if fn is None:
        raise EngineError("NO_ACTION", tr("tool.err.noAction", action=name, tool=tool),
                          tr("tool.err.actions", list=", ".join(sorted(mod.ACTIONS))))
    return fn


def text(tool, kind, fallback=""):
    """Tool title ("title") or description ("desc") in the UI language: locale key tool.<id>.<kind>, else fallback."""
    key = f"tool.{tool}.{kind}"
    return tr(key) if has(key) else fallback


def describe():
    """[{id, title, description, actions, ok, error?}] for `cli.py tools` (imports each tool)."""
    out = []
    for t in names():
        row = {"id": t, "ok": True}
        try:
            mod = get(t)
            row.update(title=text(t, "title", getattr(mod, "TITLE", t)),
                       description=text(t, "desc", getattr(mod, "DESCRIPTION", "")), actions=sorted(mod.ACTIONS))
        except EngineError as e:
            row.update(ok=False, error=e.msg)
        out.append(row)
    return out
