"""User caption style library ("Gaya Saya") and the last used style per aspect class.

Files (all in %APPDATA%\\Klipora, next to settings.json):
  caption_templates\\<id>.json  user templates (same format as templates.save_user / the `save_template` action)
  caption_library.json         metadata per user template: {"v": 1, "items": {id: {created, updated, favorite, aspect}}}
  caption_last.json            last used look per aspect class: {"v": 1, "vertical" | "horizontal" | "square": entry}

Actions (docs/CAPTIONS_API.md section 13):
  library      list | save | rename | duplicate | favorite | delete | export | import
  last_style   get | set | clear
Times are ISO strings in WIB (+07:00) plus epoch milliseconds for the panel's formatters.
Imported files are untrusted: every value is type-checked against templates.DEFAULTS, strings are bounded, colours
must be hex, font names are restricted to safe characters and replaced by the default font when not available.
"""
from __future__ import annotations

import contextlib
import copy
import json
import math
import re
from datetime import datetime
from pathlib import Path

from ..i18n import tr
from ..util import EngineError, appdata_dir, now_iso, read_json, write_json
from . import templates as TP

LIB_FILE = "caption_library.json"
LAST_FILE = "caption_last.json"
EXPORT_KIND = "autocut.caption_styles"                     # written (internal file format id, kept since v1)
EXPORT_KINDS = (EXPORT_KIND, "klipora.caption_styles")      # accepted on import
EXPORT_V = 1
CLASSES = ("vertical", "horizontal", "square")
CLASS_LABELS = {c: "cap.lib.class." + c for c in CLASSES}     # locale keys, see class_label()
PREF_KEYS = ("hide_fillers", "numbers", "censor", "glossary")
MAX_IMPORT_BYTES = 2 * 1024 * 1024
MAX_IMPORT_STYLES = 200
NAME_MAX = 60
_HEX = re.compile(r"^#[0-9A-Fa-f]{6}([0-9A-Fa-f]{2})?$")
_FONT_OK = re.compile(r"^[0-9A-Za-z .\-'&+]{1,64}$")
_META_KEYS = ("name", "description", "aspect", "default_for", "tags", "group")
_COLOR_KEYS = {"fill", "color", "outline_color", "shadow_color", "outline2_color", "border_color", "color_end",
               "pill", "underline", "past_color", "future_color"}


# ---------------------------------------------------------------- small helpers

def aspect_class(W, H):
    """'vertical' | 'horizontal' | 'square' (layout.aspect_kind thresholds)."""
    from .layout import aspect_kind
    return {"portrait": "vertical", "landscape": "horizontal"}.get(aspect_kind(int(W or 1920), int(H or 1080)), "square")


def _class_param(p, seq, W=None, H=None):
    a = (p or {}).get("aspect")
    if a in CLASSES:
        return a
    if a in ("portrait", "9:16"):
        return "vertical"
    if a in ("landscape", "16:9"):
        return "horizontal"
    seq = seq or {}
    W = W or seq.get("width") or (p or {}).get("w") or 1920
    H = H or seq.get("height") or (p or {}).get("h") or 1080
    return aspect_class(W, H)


def _ms(iso):
    try:
        return int(datetime.fromisoformat(iso).timestamp() * 1000)
    except (TypeError, ValueError):
        return None


def _iso_mtime(p):
    from ..util import WIB
    try:
        return datetime.fromtimestamp(Path(p).stat().st_mtime, WIB).isoformat(timespec="seconds")
    except OSError:
        return now_iso()


def class_label(cls):
    return tr(CLASS_LABELS[cls]) if cls in CLASS_LABELS else tr("cap.lib.class.all")


def clean_name(name, fallback=None):
    if fallback is None:
        fallback = tr("cap.lib.mine")
    s = re.sub(r"[\x00-\x1f\x7f]+", " ", str(name or "")).replace("—", "-").replace("–", "-")
    s = re.sub(r"\s+", " ", s).strip()[:NAME_MAX].strip()
    return s or fallback


def _lib_path():
    return appdata_dir() / LIB_FILE


def _read_store(p):
    """read_json for the library / last-look files, safe against Windows sharing races: a read that overlaps another
    process's os.replace can fail with PermissionError, and starting from an empty dict would then overwrite (lose)
    the user's saved styles. Retries ~1 s; a file that still cannot be opened raises instead of being treated as empty.
    Missing -> None; unparsable after the retries (really corrupt) -> None (old behaviour)."""
    import time
    p = Path(p)
    for _ in range(20):
        try:
            return json.loads(p.read_text(encoding="utf-8-sig"))
        except FileNotFoundError:
            return None
        except (OSError, ValueError):
            time.sleep(0.05)
    try:
        return json.loads(p.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        return None
    except ValueError:
        return None
    except OSError as e:
        raise EngineError("FILE_BUSY", tr("cap.lib.errBusy", name=p.name), tr("cap.lib.errBusyHint")) from e


def _write_store(p, obj):
    """write_json with a short retry: os.replace fails on Windows while another process has the target open."""
    import time
    for i in range(20):
        try:
            return write_json(p, obj, indent=1)
        except PermissionError:
            if i == 19:
                raise
            time.sleep(0.05)


def _lib():
    d = _read_store(_lib_path())
    if not isinstance(d, dict) or not isinstance(d.get("items"), dict):
        d = {"v": 1, "items": {}}
    return d


def _lib_save(d):
    d["v"] = 1
    _write_store(_lib_path(), d)


def _user_names(exclude=None):
    out = {}
    for tid in TP.user_ids():
        if tid == exclude:
            continue
        raw, _ = TP.raw(tid)
        if raw is not None:
            out[clean_name(raw.get("name"), tid).lower()] = tid
    return out


def unique_name(name, exclude=None, taken=None):
    """'Gaya Toko' -> 'Gaya Toko (2)' when a user template already has that name."""
    names = set(_user_names(exclude)) | {n.lower() for n in (taken or ())}
    base = clean_name(name)
    if base.lower() not in names:
        return base
    m = re.match(r"^(.*) \((\d+)\)$", base)
    stem, k = (m.group(1), int(m.group(2)) + 1) if m else (base, 2)
    while True:
        cand = f"{stem[:NAME_MAX - 6]} ({k})"
        if cand.lower() not in names:
            return cand
        k += 1


def _unique_id(name):
    tid = TP.slug(name)
    if tid in TP.builtin_ids():
        tid = "user_" + tid
    used = set(TP.user_ids()) | set(TP.builtin_ids())
    if tid not in used:
        return tid
    k = 2
    while f"{tid}_{k}" in used:
        k += 1
    return f"{tid}_{k}"


def _user_file(tid):
    return TP.user_dir() / f"{TP.slug(tid)}.json"


def _need_user(tid):
    tid = TP.slug(tid or "")
    if not tid or not _user_file(tid).is_file():
        raise EngineError("BAD_TEMPLATE", tr("cap.lib.errNotFound", id=tid), tr("cap.lib.errNotFoundHint"))
    return tid


def _write_user(tid, data):
    """Write a (partial or full) template dict as user template `tid` (stored = differences from DEFAULTS)."""
    data = {k: v for k, v in dict(data).items() if k not in ("id", "source", "base") and not str(k).startswith("_")}
    full = TP.deep_merge(TP.DEFAULTS, data)
    full.pop("default_for", None)
    write_json(_user_file(tid), TP.strip_defaults(full), indent=2)


def _touch(tid, created=False, **kv):
    lib = _lib()
    it = lib["items"].setdefault(tid, {})
    now = now_iso()
    if created or not it.get("created"):
        it["created"] = now
    it["updated"] = now
    it.update({k: v for k, v in kv.items() if v is not None})
    _lib_save(lib)
    return it


# ---------------------------------------------------------------- list

def row(tid, meta=None, thumb=None):
    """Picker row for one user template: templates.summary + gallery metadata + library metadata."""
    from . import gallery as G
    full = TP.load(tid)
    r = G.enrich_rows([TP.summary(full, thumb)], [full])[0]
    meta = meta or {}
    created = meta.get("created") or _iso_mtime(_user_file(tid))
    updated = meta.get("updated") or created
    asp = meta.get("aspect")
    if asp not in CLASSES:
        a = (full.get("aspect") or [""])[0]
        asp = "vertical" if a == "9:16" else "square" if a == "1:1" else "horizontal" if a else None
    r.update({"favorite": bool(meta.get("favorite")), "aspect_class": asp,
              "aspect_label": class_label(asp),
              "created": created, "updated": updated, "created_ms": _ms(created), "updated_ms": _ms(updated),
              "font_source": _font_source(full["font"]["family"])})
    return r


def list_rows(thumbs=True):
    """User templates, favourites first, then newest change first."""
    lib = _lib()["items"]
    ids = TP.user_ids()
    paths = {}
    if thumbs and ids:
        from . import preview as P
        tpls = []
        for tid in ids:
            try:
                tpls.append(TP.load(tid))
            except Exception:  # noqa: BLE001 - one broken file must not hide the others
                continue
        paths, _ = P.gallery(tpls, threads=4)
    rows = []
    for tid in ids:
        try:
            rows.append(row(tid, lib.get(tid), paths.get(tid)))
        except Exception:  # noqa: BLE001
            continue
    rows.sort(key=lambda r: (not r["favorite"], -(r["updated_ms"] or 0), r["name"].lower()))
    return rows


# ---------------------------------------------------------------- save / rename / duplicate / favorite / delete

def doc_look(doc, patch=None):
    """Effective look of a captions doc (template + doc.style [+ patch]) as a full template dict."""
    base = TP.load(doc.get("template"))
    data = TP.deep_merge({k: v for k, v in base.items() if k not in ("id", "source") and not k.startswith("_")},
                         doc.get("style") or {})           # engine semantics: an explicit null overrides the template
    return TP.deep_merge(data, patch or {}), base


def save(data, name, aspect=None, tid=None, description=None):
    """New user template (or overwrite `tid`). Returns its row."""
    new = not tid
    if tid:
        tid = _need_user(tid)
        name = unique_name(name, exclude=tid)
    else:
        name = unique_name(name)
        tid = _unique_id(name)
    data = dict(data)
    data["name"] = name
    if description is not None:
        data["description"] = clean_name(description, "")[:200] if description else ""
    data.setdefault("description", tr("cap.lib.descMine"))
    _write_user(tid, data)
    meta = _touch(tid, created=new, aspect=aspect if aspect in CLASSES else None)
    return row(tid, meta, _thumb(tid))


def _thumb(tid):
    from . import preview as P
    try:
        full = TP.load(tid)
        return str(P.thumb(full, *P.preferred_size(full)))
    except Exception:  # noqa: BLE001 - a thumbnail failure must not lose the saved template
        return None


def rename(tid, name):
    tid = _need_user(tid)
    raw, _ = TP.raw(tid)
    raw = dict(raw or {})
    raw["name"] = unique_name(name, exclude=tid)
    write_json(_user_file(tid), raw, indent=2)
    return row(tid, _touch(tid))


def duplicate(tid, name=None):
    """Copy of a user OR built-in template into Gaya Saya ("Nama (salinan)")."""
    src = TP.slug(tid or "")
    raw, kind = TP.raw(src)
    if raw is None:
        raise EngineError("BAD_TEMPLATE", tr("cap.lib.errNotFound", id=src))
    full = TP.load(src)
    data = TP.strip_defaults(full)
    nm = unique_name(name or tr("cap.lib.copyName", name=clean_name(TP.display_name(full), src)[:NAME_MAX - 10]))
    new = _unique_id(nm)
    data["name"] = nm
    _write_user(new, data)
    asp = (_lib()["items"].get(src) or {}).get("aspect")
    meta = _touch(new, created=True, aspect=asp)
    return row(new, meta, _thumb(new))


def favorite(tid, on=True):
    tid = _need_user(tid)
    lib = _lib()
    lib["items"].setdefault(tid, {"created": _iso_mtime(_user_file(tid))})["favorite"] = bool(on)
    _lib_save(lib)
    return row(tid, lib["items"][tid])


def delete(tid):
    tid = TP.slug(tid or "")
    ok = TP.delete_user(tid)
    lib = _lib()
    if lib["items"].pop(tid, None) is not None:
        _lib_save(lib)
    return ok


# ---------------------------------------------------------------- fonts

def _bundled_fonts():
    from .layout import bundled_index
    return set(bundled_index())


def _system_fonts():
    try:
        from .layout import system_fonts
        return set(system_fonts())
    except Exception:  # noqa: BLE001 - no font scan = treat as not installed
        return set()


def _font_source(family):
    if family in _bundled_fonts():
        return "bundled"
    return "system" if family in _system_fonts() else "missing"


def _fonts_of(full):
    fams = [full["font"]["family"]]
    emph = (full.get("emphasis") or {}).get("font")
    if emph:
        fams.append(emph)
    return [f for f in dict.fromkeys(fams) if isinstance(f, str) and f]


# ---------------------------------------------------------------- export / import

def export(ids, path):
    """Write user (or built-in) templates to a .json file. -> {path, n, fonts, warnings}"""
    ids = [TP.slug(i) for i in (ids if isinstance(ids, list) else [ids]) if i]
    if not ids:
        raise EngineError("BAD_PARAMS", tr("cap.lib.errExportNone"))
    if not path:
        raise EngineError("BAD_PARAMS", tr("cap.lib.errExportPath"))
    out = Path(path)
    if out.suffix.lower() != ".json":
        out = out.with_suffix(".json")
    lib = _lib()["items"]
    styles, fonts = [], {}
    for tid in ids:
        if TP.raw(tid)[0] is None:
            raise EngineError("BAD_TEMPLATE", tr("cap.lib.errNotFound", id=tid))
        full = TP.load(tid)
        meta = lib.get(tid) or {}
        styles.append({"id": tid, "name": full.get("name") or tid, "favorite": bool(meta.get("favorite")),
                       "aspect": meta.get("aspect"), "template": TP.strip_defaults(full)})
        for f in _fonts_of(full):
            fonts[f] = _font_source(f)
    warnings = []
    for f, src in fonts.items():
        if src == "system":
            warnings.append(tr("cap.lib.warnSysFont", font=f))
        elif src == "missing":
            warnings.append(tr("cap.lib.warnNoFont", font=f))
    data = {"kind": EXPORT_KIND, "v": EXPORT_V, "app": "Klipora", "exported": now_iso(),
            "styles": styles,
            "fonts": [{"family": f, "source": s, "bundled": s == "bundled"} for f, s in fonts.items()]}
    out.parent.mkdir(parents=True, exist_ok=True)
    write_json(out, data, indent=2)
    return {"path": str(out), "n": len(styles), "fonts": data["fonts"], "warnings": warnings}


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _clean_value(v, depth=0):
    """Generic JSON-safe value (unknown keys inside known sections, e.g. anim.fx): bounded size and depth."""
    if depth > 6:
        return None
    if v is None or isinstance(v, bool):
        return v
    if _num(v):
        return max(-1e6, min(1e6, v))
    if isinstance(v, str):
        return re.sub(r"[\x00-\x1f\x7f]", "", v)[:500]
    if isinstance(v, list):
        return [x for x in (_clean_value(x, depth + 1) for x in v[:200])]
    if isinstance(v, dict):
        out = {}
        for k, x in list(v.items())[:200]:
            if isinstance(k, str) and len(k) <= 64:
                out[k] = _clean_value(x, depth + 1)
        return out
    return None


def _clean_color(v):
    return v.upper() if isinstance(v, str) and _HEX.match(v) else None


def _schema_index():
    return {f["path"]: f for f in TP.STYLE_SCHEMA}


def _clamp(v, f):
    if f is None:
        return v
    if "min" in f and v < f["min"]:
        v = f["min"]
    if "max" in f and v > f["max"]:
        v = f["max"]
    if f.get("type") == "int":
        v = int(round(v))
    return v


_MISSING = object()


def _clean_against(v, ref, path, schema):
    """Value `v` checked against the DEFAULTS value `ref` at `path`. _MISSING = drop it."""
    f = schema.get(path)
    key = path.rsplit(".", 1)[-1]
    if v is None and f and f.get("nullable"):            # e.g. highlight.color = null ("Tanpa") over a colour default
        return None
    if isinstance(ref, dict):
        if not isinstance(v, dict):
            return _MISSING
        out = {}
        for k, x in v.items():
            if not isinstance(k, str) or k.startswith("_") or len(k) > 64:
                continue
            if k in ref:
                c = _clean_against(x, ref[k], f"{path}.{k}", schema)
                if c is not _MISSING:
                    out[k] = c
            else:
                out[k] = _clean_value(x, 2)               # newer keys (e.g. anim.fx) pass through, bounded
        return out
    if isinstance(ref, bool):
        return v if isinstance(v, bool) else _MISSING
    if _num(ref):
        return _clamp(v, f) if _num(v) else _MISSING
    if isinstance(ref, str):
        if not isinstance(v, str):
            return _MISSING
        if ref.startswith("#") or key in _COLOR_KEYS:
            return _clean_color(v) or _MISSING
        if f and f.get("type") == "enum" and f.get("options"):
            return v if v in {o["id"] for o in f["options"]} else _MISSING
        return re.sub(r"[\x00-\x1f\x7f]", "", v)[:120]
    if isinstance(ref, list):
        if not isinstance(v, list):
            return _MISSING
        return [_clean_value(x, 3) for x in v[:50]]
    # ref None: nullable value or an effect object
    if v is None:
        return None
    if key in _COLOR_KEYS or (f and f.get("type") == "color"):
        return _clean_color(v) or _MISSING
    if f and f.get("type") == "colors":
        if not isinstance(v, list):
            return _MISSING
        cols = [c for c in (_clean_color(x) for x in v[:8]) if c]
        return cols or None
    if f and f.get("type") in ("number", "int"):
        return _clamp(v, f) if _num(v) else _MISSING
    if f and f.get("type") == "enum":
        return v if isinstance(v, str) and v in {o["id"] for o in f.get("options") or []} else _MISSING
    if isinstance(v, dict):
        out = {}
        for k, x in v.items():
            if not isinstance(k, str) or len(k) > 64:
                continue
            sub = schema.get(f"{path}.{k}")
            if k in _COLOR_KEYS or (sub and sub.get("type") == "color"):
                c = _clean_color(x) if x is not None else None
                if c is not None or x is None:
                    out[k] = c
            elif k == "colors" and isinstance(x, list):
                cols = [c for c in (_clean_color(y) for y in x[:8]) if c]
                if cols:
                    out[k] = cols
            elif _num(x):
                out[k] = _clamp(x, sub)
            else:
                out[k] = _clean_value(x, 3)
        return out
    return _clean_value(v, 3)


def sanitize(tpl):
    """Untrusted template dict -> (clean partial template, warnings). Keeps only known sections."""
    if not isinstance(tpl, dict):
        raise EngineError("BAD_FILE", tr("cap.lib.errUnknown"))
    schema = _schema_index()
    out, warns = {}, []
    for k, v in tpl.items():
        if k in ("name", "description"):
            if isinstance(v, str):
                out[k] = clean_name(v, "")[: 200 if k == "description" else NAME_MAX]
        elif k in ("aspect", "tags"):
            if isinstance(v, list):
                out[k] = [re.sub(r"[^0-9A-Za-z:_\-]", "", str(x))[:24] for x in v[:12] if isinstance(x, str)]
        elif k == "group":
            if isinstance(v, str):
                out[k] = re.sub(r"[^0-9a-z_]", "", v)[:32]
        elif isinstance(TP.DEFAULTS.get(k), dict):
            c = _clean_against(v, TP.DEFAULTS[k], k, schema)
            if c is not _MISSING and c:
                out[k] = c
    fonts = _bundled_fonts() | _system_fonts()
    fam = (out.get("font") or {}).get("family")
    if fam is not None:
        if not isinstance(fam, str) or not _FONT_OK.match(fam) or fam not in fonts:
            warns.append(tr("cap.lib.warnFontReplaced", font=str(fam)[:64], default=TP.DEFAULTS["font"]["family"]))
            out["font"]["family"] = TP.DEFAULTS["font"]["family"]
    efam = (out.get("emphasis") or {}).get("font")
    if efam is not None and (not isinstance(efam, str) or not _FONT_OK.match(efam) or efam not in fonts):
        out["emphasis"]["font"] = None
    return out, warns


def _styles_in(data):
    """Template dicts in an imported JSON: our export file, a list of them, or one bare template."""
    if isinstance(data, dict) and data.get("kind") in EXPORT_KINDS:
        v = data.get("v")
        if not isinstance(v, int) or v > EXPORT_V:
            raise EngineError("BAD_FILE", tr("cap.lib.errNewer"), tr("cap.lib.errNewerHint"))
        items = data.get("styles")
        if not isinstance(items, list):
            raise EngineError("BAD_FILE", tr("cap.lib.errBroken"))
        out = []
        for it in items[:MAX_IMPORT_STYLES]:
            if isinstance(it, dict) and isinstance(it.get("template"), dict):
                t = dict(it["template"])
                t.setdefault("name", it.get("name"))
                out.append((t, bool(it.get("favorite")), it.get("aspect")))
        return out
    if isinstance(data, dict) and any(k in data for k in ("font", "style", "box", "highlight", "layout")):
        return [(data, False, None)]
    if isinstance(data, list):
        return [(t, False, None) for t in data[:MAX_IMPORT_STYLES]
                if isinstance(t, dict) and any(k in t for k in ("font", "style", "box", "highlight"))]
    raise EngineError("BAD_FILE", tr("cap.lib.errNotStyles"), tr("cap.lib.errNotStylesHint"))


def import_file(path):
    """Validate + sanitize + save every style in the file as new user templates (names de-duplicated)."""
    p = Path(path or "")
    if not p.is_file():
        raise EngineError("BAD_FILE", tr("cap.lib.errNoFile"), str(p))
    if p.stat().st_size > MAX_IMPORT_BYTES:
        raise EngineError("BAD_FILE", tr("cap.lib.errTooBig"))
    try:
        data = json.loads(p.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as e:
        raise EngineError("BAD_FILE", tr("cap.lib.errJson"), str(e)[:200]) from e
    items = _styles_in(data)
    if not items:
        raise EngineError("BAD_FILE", tr("cap.lib.errEmpty"))
    rows, skipped, warns, taken = [], [], [], []
    for tpl, fav, asp in items:
        try:
            clean, w = sanitize(tpl)
        except EngineError as e:
            skipped.append({"name": str((tpl or {}).get("name") or "?")[:NAME_MAX], "why": e.msg})
            continue
        nm = unique_name(clean.get("name") or tr("cap.lib.importName"), taken=taken)
        taken.append(nm)
        clean["name"] = nm
        clean.setdefault("description", tr("cap.lib.importDesc"))
        tid = _unique_id(nm)
        _write_user(tid, clean)
        meta = _touch(tid, created=True, favorite=True if fav else None, aspect=asp if asp in CLASSES else None)
        rows.append(row(tid, meta, _thumb(tid)))
        warns.extend(x for x in w if x not in warns)
    return {"imported": rows, "skipped": skipped, "warnings": warns}


# ---------------------------------------------------------------- last used style (per aspect class)

def _last_path():
    return appdata_dir() / LAST_FILE


def _last_all():
    d = _read_store(_last_path())
    return d if isinstance(d, dict) else {"v": 1}


def _prefs(params):
    out = {}
    for k in PREF_KEYS:
        if k in (params or {}):
            v = params[k]
            if k == "glossary":
                v = [str(x)[:60] for x in (v or []) if isinstance(x, str)][:200]
            elif k == "censor":
                v = str(v)[:16]
            else:
                v = bool(v)
            out[k] = v
    return out


def remember(template, style, params, W, H, reason="style", seq_name=None, cls=None):
    """Snapshot the effective look + caption prefs for the aspect class. Writes only when something changed."""
    cls = cls if cls in CLASSES else aspect_class(W, H)
    tid = TP.slug(template or "") or TP.default_id(W, H)
    base = TP.load(tid, W, H)
    st = style if isinstance(style, dict) else {}
    full = TP.deep_merge({k: v for k, v in base.items() if k not in ("id", "source") and not k.startswith("_")}, st)
    entry = {"template": base["id"], "template_name": base.get("name") or base["id"], "style": copy.deepcopy(st),
             "full": TP.strip_defaults(full), "params": _prefs(params),
             "max_words": (full.get("layout") or {}).get("max_words"),
             "seq": str(seq_name or "")[:120], "reason": reason}
    allx = _last_all()
    old = allx.get(cls) if isinstance(allx.get(cls), dict) else {}
    same = all(old.get(k) == entry[k] for k in ("template", "style", "params"))
    if same and old.get("updated"):
        return dict(old, changed=False, aspect=cls)
    entry["updated"] = now_iso()
    allx[cls] = entry
    allx["v"] = 1
    _write_store(_last_path(), allx)
    return dict(entry, changed=True, aspect=cls)


def last_get(cls, W=None, H=None, thumb=False):
    """Last entry for the class with ready-to-send `ops` ({op: template} + {op: doc_style, replace}), or None."""
    from . import gallery as G
    e = _last_all().get(cls)
    if not isinstance(e, dict) or not e.get("updated"):
        return None
    e = copy.deepcopy(e)
    if not W or not H:
        W, H = (1080, 1920) if cls == "vertical" else (1080, 1080) if cls == "square" else (1920, 1080)
    tid = TP.slug(e.get("template") or "")
    if tid and TP.raw(tid)[0] is not None:
        style = e.get("style") or {}
        e["template_missing"] = False
    else:                                   # template deleted: rebuild the look on the default from the snapshot
        tid = TP.default_id(W, H)
        base = TP.load(tid, W, H)
        full = TP.deep_merge(TP.DEFAULTS, e.get("full") or {})
        style = G.style_diff(full, base)
        e["template_missing"] = True
    e["base"] = tid
    e["ops"] = [{"op": "template", "id": tid}, {"op": "doc_style", "style": style, "replace": True}]
    e["aspect"] = cls
    e["aspect_label"] = class_label(cls)
    e["updated_ms"] = _ms(e.get("updated"))
    tname = e.get("template_name") or tid
    if not e["template_missing"]:
        with contextlib.suppress(Exception):
            tname = TP.display_name(TP.load(tid, W, H)) or tname        # built-ins: name in the job language
    e["name"] = tr("cap.lib.lastYours", name=tname) if style else tname
    if thumb:
        try:
            from . import preview as P
            full = TP.deep_merge(TP.load(tid, W, H), style)
            full["id"] = "last_" + cls
            e["thumb"] = str(P.thumb(full, W, H))
        except Exception:  # noqa: BLE001
            e["thumb"] = None
    return e


def last_clear(cls=None):
    allx = _last_all()
    n = 0
    for c in ([cls] if cls else list(CLASSES)):
        if allx.pop(c, None) is not None:
            n += 1
    _write_store(_last_path(), allx)
    return n


# ---------------------------------------------------------------- actions

def action_library(job, emit):
    p = job.get("params") or {}
    op = p.get("op") or "list"
    seq = job.get("seq") or {}
    if op == "list":
        rows = list_rows(thumbs=p.get("thumbs", True) is not False)
        return {"templates": rows, "n": len(rows), "favorites": sum(1 for r in rows if r["favorite"])}
    if op == "save":
        if p.get("template") is not None and not isinstance(p.get("template"), dict):
            raise EngineError("BAD_PARAMS", tr("cap.lib.errTemplateObj"))
        if p.get("from_doc", True) and not isinstance(p.get("template"), dict):
            from . import model as M
            d, _ = M.from_job(job)
            data, base = doc_look(d, p.get("patch"))
            W, H = (d.get("seq") or {}).get("w"), (d.get("seq") or {}).get("h")
            desc = p.get("description") or tr("cap.lib.descFrom", name=TP.display_name(base) if base else "template")
        else:
            data = TP.deep_merge(TP.DEFAULTS, sanitize(p["template"])[0])
            W, H = seq.get("width"), seq.get("height")
            desc = p.get("description")
        data.pop("default_for", None)
        cls = _class_param(p, seq, W, H) if (W and H) or p.get("aspect") else None
        r = save(data, p.get("name") or tr("cap.lib.mine"), cls, p.get("id"), desc)
        return {"template": r, "id": r["id"], "summary": tr("cap.lib.sumSaved", name=r["name"])}
    if op == "rename":
        r = rename(p.get("id"), p.get("name"))
        return {"template": r, "id": r["id"], "summary": tr("cap.lib.sumRenamed", name=r["name"])}
    if op == "duplicate":
        r = duplicate(p.get("id"), p.get("name"))
        return {"template": r, "id": r["id"], "summary": tr("cap.lib.sumDuplicated", name=r["name"])}
    if op == "favorite":
        r = favorite(p.get("id"), p.get("on", True) is not False)
        return {"template": r, "id": r["id"],
                "summary": tr("cap.lib.sumFav" if r["favorite"] else "cap.lib.sumUnfav", name=r["name"])}
    if op == "delete":
        ok = delete(p.get("id"))
        return {"deleted": ok, "id": TP.slug(p.get("id") or ""), "summary": tr("cap.lib.sumDeleted") if ok else tr("cap.lib.sumMissing")}
    if op == "export":
        r = export(p.get("ids") or p.get("id"), p.get("path"))
        r["summary"] = tr("cap.lib.sumExported", n=r["n"])
        return r
    if op == "import":
        r = import_file(p.get("path"))
        r["summary"] = (tr("cap.lib.sumImportedSkipped", n=len(r["imported"]), skipped=len(r["skipped"])) if r["skipped"]
                        else tr("cap.lib.sumImported", n=len(r["imported"])))
        return r
    raise EngineError("BAD_PARAMS", tr("cap.lib.errOp", op=op), tr("cap.lib.errOpHint"))


def action_last_style(job, emit):
    p = job.get("params") or {}
    op = p.get("op") or "get"
    seq = job.get("seq") or {}
    if op == "get":
        if p.get("all"):
            return {"last": {c: last_get(c, thumb=bool(p.get("thumb"))) for c in CLASSES}}
        cls = _class_param(p, seq)
        W, H = seq.get("width") or p.get("w"), seq.get("height") or p.get("h")
        return {"aspect": cls, "last": last_get(cls, W, H, bool(p.get("thumb")))}
    if op == "set":
        if p.get("from_doc"):
            from . import model as M
            d, _ = M.from_job(job)
            ds = d.get("seq") or {}
            e = remember(d.get("template"), d.get("style"), d.get("params"), ds.get("w"), ds.get("h"),
                         p.get("reason") or "doc", ds.get("name"))
        else:
            W, H = seq.get("width") or p.get("w") or 1920, seq.get("height") or p.get("h") or 1080
            e = remember(p.get("template"), p.get("style"), p.get("params"), W, H, p.get("reason") or "style",
                         p.get("seq_name") or seq.get("name"), p.get("aspect"))
        return {"aspect": e["aspect"], "changed": e["changed"], "updated": e.get("updated"),
                "template": e["template"], "summary": tr("cap.lib.sumLastSaved") if e["changed"] else tr("cap.lib.sumUnchanged")}
    if op == "clear":
        n = last_clear(p.get("aspect") if p.get("aspect") in CLASSES else None)
        return {"cleared": n, "summary": tr("cap.lib.sumLastCleared", n=n)}
    raise EngineError("BAD_PARAMS", tr("cap.lib.errLastOp", op=op), tr("cap.lib.errLastOpHint"))
