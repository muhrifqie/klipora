"""AI provider profiles (no secrets) and the `cli.py ai ...` commands. docs/AI_PROVIDERS.md.

Profiles file %APPDATA%\\Klipora\\ai_profiles.json (re-read when it changes, so the worker stays current):
    {"v": 1, "order": ["grok_local", "gemini"],                 # active first, then the fallbacks, then rules
     "profiles": [{"id", "name", "kind", "base_url", "model_fast", "model_smart", "vision_model",
                   "enabled", "json_mode": "auto"|"on"|"off", "extra_headers": {}, "draft"?, "created"}]}
No file yet -> one synthesized "grok_local" (local proxy) profile from <repo>\\.env (backward compatible).
Keys live in ac.ai.keystore (DPAPI); a profile only ever says has_key true/false.
Health/test results are cached in %APPDATA%\\Klipora\\ai_health.json (no secrets).
"""
from __future__ import annotations

import json
import os
import re
import sys
import threading
import time
from urllib.parse import urlparse

from ..i18n import has as tr_has
from ..i18n import tr
from . import keystore as KS

__all__ = ["PRESETS", "SLOTS", "load", "profiles", "route", "get", "save_profile", "set_order", "activate",
           "remove", "public", "health_get", "health_put", "cli_main", "ProfileError"]

SLOTS = ("fast", "smart", "vision")

# kind -> preset. Model names are suggestions (UNVERIFIED that every provider still serves them on a given day);
# the panel wizard fetches the real list with "Ambil daftar model". adapter "openai" = /chat/completions,
# "anthropic" = native /v1/messages (x-api-key + anthropic-version). json_fmt = response_format json_object
# is known to work there (json_mode "auto" uses it; a 400 about it turns it off for that profile).
PRESETS = {   # names/notes = Indonesian defaults; the panel gets them translated (preset_text, ai.preset.*)
    # any OpenAI-compatible endpoint on this computer (e.g. a local proxy); kind id kept for stored profiles
    "grok_local": {"name": "Proxy lokal (kompatibel OpenAI)", "base_url": "http://127.0.0.1:8168/v1",  # i18n-ignore
                   "model_fast": "grok-fast", "model_smart": "grok-auto", "vision_model": "grok-auto",
                   "needs_key": True, "adapter": "openai", "json_fmt": False, "key_url": "", "paid": False,
                   "note": "Endpoint apa pun yang kompatibel OpenAI, misalnya proxy lokal di komputer ini. "  # i18n-ignore
                           "Kunci dibaca dari .env kalau belum diisi di sini."},  # i18n-ignore
    "openrouter": {"name": "OpenRouter", "base_url": "https://openrouter.ai/api/v1",
                   "model_fast": "google/gemini-2.5-flash", "model_smart": "google/gemini-2.5-pro",
                   "vision_model": "google/gemini-2.5-flash", "needs_key": True, "adapter": "openai", "json_fmt": True,
                   "key_url": "openrouter.ai/keys", "paid": True,
                   "headers": {"HTTP-Referer": "https://github.com/muhrifqie/klipora", "X-Title": "Klipora"},
                   "note": "Satu kunci untuk banyak model (ada yang gratis)."},  # i18n-ignore
    "gemini": {"name": "Gemini", "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
               "model_fast": "gemini-2.5-flash", "model_smart": "gemini-2.5-pro", "vision_model": "gemini-2.5-flash",
               "needs_key": True, "adapter": "openai", "json_fmt": True, "key_url": "aistudio.google.com/apikey",
               "paid": True, "note": "Google AI Studio. Ada kuota gratis harian."},  # i18n-ignore
    "openai": {"name": "OpenAI", "base_url": "https://api.openai.com/v1", "model_fast": "gpt-5-mini",
               "model_smart": "gpt-5", "vision_model": "gpt-5-mini", "needs_key": True, "adapter": "openai",
               "json_fmt": True, "key_url": "platform.openai.com/api-keys", "paid": True, "note": ""},
    "xai": {"name": "xAI (Grok API)", "base_url": "https://api.x.ai/v1", "model_fast": "grok-3-mini",
            "model_smart": "grok-4", "vision_model": "grok-4", "needs_key": True, "adapter": "openai",
            "json_fmt": True, "key_url": "console.x.ai", "paid": True, "note": "API resmi xAI (berbayar)."},  # i18n-ignore
    "anthropic": {"name": "Anthropic (Claude)", "base_url": "https://api.anthropic.com/v1",
                  "model_fast": "claude-haiku-4-5", "model_smart": "claude-opus-5-5", "vision_model": "claude-opus-5-5",
                  "needs_key": True, "adapter": "anthropic", "json_fmt": False,
                  "key_url": "console.anthropic.com/settings/keys", "paid": True,
                  "note": "Lewat Messages API asli (bukan lapisan OpenAI)."},  # i18n-ignore
    "deepseek": {"name": "DeepSeek", "base_url": "https://api.deepseek.com/v1", "model_fast": "deepseek-chat",
                 "model_smart": "deepseek-reasoner", "vision_model": "", "needs_key": True, "adapter": "openai",
                 "json_fmt": True, "key_url": "platform.deepseek.com/api_keys", "paid": True,
                 "note": "Murah. Tidak bisa membaca gambar."},  # i18n-ignore
    "groq": {"name": "Groq", "base_url": "https://api.groq.com/openai/v1", "model_fast": "llama-3.1-8b-instant",
             "model_smart": "llama-3.3-70b-versatile", "vision_model": "", "needs_key": True, "adapter": "openai",
             "json_fmt": True, "key_url": "console.groq.com/keys", "paid": False, "note": "Sangat cepat, ada kuota gratis."},  # i18n-ignore
    "ollama": {"name": "Ollama (lokal)", "base_url": "http://127.0.0.1:11434/v1", "model_fast": "", "model_smart": "",  # i18n-ignore
               "vision_model": "", "needs_key": False, "adapter": "openai", "json_fmt": True, "key_url": "",
               "paid": False, "note": "Model jalan di komputer ini. Transkrip tidak keluar dari PC."},  # i18n-ignore
    "custom": {"name": "Custom", "base_url": "", "model_fast": "", "model_smart": "", "vision_model": "",
               "needs_key": False, "adapter": "openai", "json_fmt": False, "key_url": "", "paid": False,
               "note": "Endpoint lain yang kompatibel OpenAI (/chat/completions)."},  # i18n-ignore
}
# preset names stored by older versions: still treated as the untouched default (shown translated)
LEGACY_NAMES = {"grok_local": {"Grok lokal", "Local Grok"}}  # i18n-ignore

_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")
_SECRETISH = re.compile(r"(?i)(api[_-]?key|apikey|secret|token|password|passwd|authorization|^auth|cookie|^key$|x-api-key)")
_HDR_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{0,63}$")
_LOCK = threading.Lock()
_STATE = {"mtime": None, "doc": None, "path": None}
DRAFT_TTL = 3600.0


class ProfileError(Exception):
    def __init__(self, code, msg, hint=""):
        super().__init__(msg)
        self.code, self.msg, self.hint = code, msg, hint


# ---------------------------------------------------------------- files

def _appdata():
    from ..util import appdata_dir
    return appdata_dir()


def profiles_path():
    return _appdata() / "ai_profiles.json"


def health_path():
    return _appdata() / "ai_health.json"


def _write(p, obj):
    tmp = p.with_name(p.name + f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, p)


def _env_models():
    """grok_local defaults from .env (AI_MODEL / AI_MODEL_SMART), like the old single-profile client."""
    from .client import load_env
    env = load_env()
    return env.get("AI_MODEL") or "grok-fast", env.get("AI_MODEL_SMART") or "grok-auto"


def synth_grok():
    fast, smart = _env_models()
    return {"id": "grok_local", "name": PRESETS["grok_local"]["name"], "kind": "grok_local", "base_url": "",
            "model_fast": fast, "model_smart": smart, "vision_model": "grok-auto", "enabled": True,
            "json_mode": "off", "extra_headers": {}}


def load():
    """-> {"v", "order", "profiles", "synthesized"} (a fresh copy). Never raises: a broken file = no profiles
    except the synthesized local proxy one (logged), so AI features degrade instead of crashing."""
    p = profiles_path()
    try:
        mt = p.stat().st_mtime_ns
    except OSError:
        mt = None
    if _STATE["doc"] is None or mt != _STATE["mtime"] or _STATE["path"] != str(p):
        doc = None
        if mt is not None:
            try:
                raw = json.loads(p.read_text(encoding="utf-8-sig"))
                if isinstance(raw, dict) and isinstance(raw.get("profiles"), list):
                    doc = {"v": 1, "profiles": [x for x in raw["profiles"] if isinstance(x, dict) and x.get("id")],
                           "order": [str(x) for x in raw.get("order") or [] if isinstance(x, str)],
                           "synthesized": False}
            except (OSError, ValueError) as e:
                from ..util import log
                log().warning("ai_profiles.json unreadable, using the .env local proxy only: %s", e)
        if doc is None:
            doc = {"v": 1, "profiles": [synth_grok()], "order": ["grok_local"], "synthesized": True}
        ids = [x["id"] for x in doc["profiles"]]
        order = [i for i in doc["order"] if i in ids]
        order += [x["id"] for x in doc["profiles"] if x["id"] not in order and not x.get("draft")]
        doc["order"] = list(dict.fromkeys(order))
        _STATE.update(mtime=mt, doc=doc, path=str(p))
    return json.loads(json.dumps(_STATE["doc"]))


def _store(doc):
    out = {"v": 1, "order": doc["order"], "profiles": doc["profiles"]}
    _write(profiles_path(), out)
    _STATE.update(mtime=None, doc=None)


def reset_cache():
    _STATE.update(mtime=None, doc=None)


def profiles():
    return load()["profiles"]


def get(pid):
    for p in profiles():
        if p["id"] == pid:
            return p
    return None


def route():
    """Enabled, finished profiles in fallback order (first = active)."""
    doc = load()
    by = {p["id"]: p for p in doc["profiles"]}
    return [by[i] for i in doc["order"] if i in by and by[i].get("enabled", True) and not by[i].get("draft")]


def preset(kind):
    return PRESETS.get(kind) or PRESETS["custom"]


def needs_key(p):
    return bool(preset(p.get("kind")).get("needs_key"))


def has_key(p):
    """Usable credentials: a stored key this Windows user can decrypt, or (built-in local proxy profile only)
    AI_API_KEY from .env."""
    if KS.has_key(p["id"]) and not KS.key_error(p["id"]):
        return True
    return _env_key_ok(p)


def _env_key_ok(p):
    if p.get("kind") != "grok_local":
        return False
    from .client import env_key_allowed, load_env
    return env_key_allowed(p) and bool(load_env().get("AI_API_KEY"))


# ---------------------------------------------------------------- validation

def _clean_url(u):
    u = str(u or "").strip()
    if not u:
        return ""
    u = u.replace("://localhost", "://127.0.0.1")  # Windows: localhost tries ::1 first (+2 s per request)
    pr = urlparse(u)
    if pr.scheme not in ("http", "https") or not pr.netloc or any(c.isspace() for c in u):
        raise ProfileError("AI_BAD_URL", tr("ai.badUrl"), tr("ai.urlExample"))
    if pr.username or pr.password:
        raise ProfileError("AI_BAD_URL", tr("ai.userInUrl"), tr("ai.keyFieldHint"))
    if pr.query and re.search(r"(?i)key|token|secret", pr.query):
        raise ProfileError("AI_BAD_URL", tr("ai.keyInUrl"), tr("ai.keyFieldHint"))
    return u


def _is_local(u):
    host = (urlparse(u).hostname or "").lower()
    return host in ("127.0.0.1", "::1", "localhost") or host.startswith("192.168.") or host.startswith("10.")


def _model_name(v, field):
    v = str(v or "").strip()
    if len(v) > 200 or any(c in v for c in "\r\n\t"):
        raise ProfileError("AI_BAD_PROFILE", tr("ai.badModelName", field=field))
    return v


def _new_id(kind, existing):
    base = re.sub(r"[^a-z0-9_-]", "", kind.lower())[:30] or "ai"
    if base not in existing:
        return base
    n = 2
    while f"{base}_{n}" in existing:
        n += 1
    return f"{base}_{n}"


def validate(obj, existing_ids=(), prev=None):
    """Profile dict from the panel -> clean profile (raises ProfileError). Secrets are refused."""
    if not isinstance(obj, dict):
        raise ProfileError("AI_BAD_PROFILE", tr("ai.profileNotObject"))
    for k, v in obj.items():
        if _SECRETISH.search(str(k)):
            raise ProfileError("AI_KEY_IN_PROFILE", tr("ai.keyInProfile"), tr("ai.keyInProfileHint"))
    kind = str(obj.get("kind") or (prev or {}).get("kind") or "custom")
    if kind not in PRESETS:
        raise ProfileError("AI_BAD_PROFILE", tr("ai.unknownKind", kind=kind))
    pre = PRESETS[kind]
    base = dict(prev or {})
    pid = str(obj.get("id") or base.get("id") or "") or _new_id(kind, set(existing_ids))
    if not _ID.match(pid):
        raise ProfileError("AI_BAD_PROFILE", tr("ai.badIdChars"))
    out = {"id": pid, "kind": kind}
    name = str(obj.get("name", base.get("name")) or pre["name"]).strip()
    if name in {tr(_pkey(kind, "name"), lang=lg) for lg in ("id", "en") if tr_has(_pkey(kind, "name"))} | LEGACY_NAMES.get(kind, set()):
        name = pre["name"]                          # translated preset default -> stored default (follows the UI)
    if len(name) > 40 or any(c in name for c in "\r\n"):
        raise ProfileError("AI_BAD_PROFILE", tr("ai.nameTooLong"))
    out["name"] = name
    url = obj.get("base_url", base.get("base_url", "" if kind == "grok_local" else pre["base_url"]))
    out["base_url"] = _clean_url(url)
    if not out["base_url"] and kind != "grok_local":
        raise ProfileError("AI_BAD_URL", tr("ai.urlMissing"), tr("ai.urlExample"))
    for slot in ("model_fast", "model_smart", "vision_model"):
        out[slot] = _model_name(obj.get(slot, base.get(slot, pre[slot])), slot)
    out["enabled"] = bool(obj.get("enabled", base.get("enabled", True)))
    jm = str(obj.get("json_mode", base.get("json_mode", "off" if kind == "grok_local" else "auto")))
    if jm not in ("auto", "on", "off"):
        raise ProfileError("AI_BAD_PROFILE", tr("ai.badJsonMode"))
    out["json_mode"] = jm
    hdr = obj.get("extra_headers", base.get("extra_headers")) or {}
    if not isinstance(hdr, dict) or len(hdr) > 10:
        raise ProfileError("AI_BAD_PROFILE", tr("ai.badHeaders"))
    clean = {}
    for k, v in hdr.items():
        k, v = str(k).strip(), str(v)
        if not _HDR_NAME.match(k) or len(v) > 300 or any(c in v for c in "\r\n"):
            raise ProfileError("AI_BAD_PROFILE", tr("ai.badHeader", name=k))
        if _SECRETISH.search(k):
            raise ProfileError("AI_KEY_IN_PROFILE", tr("ai.headerLooksLikeKey", name=k))
        clean[k] = v
    out["extra_headers"] = clean
    if obj.get("draft", base.get("draft")):
        out["draft"] = True
    out["created"] = float(base.get("created") or time.time())
    if out.get("draft"):
        out["touched"] = time.time()                # every wizard save keeps the draft alive (prune_drafts)
    warnings = []
    if out["base_url"].startswith("http://") and not _is_local(out["base_url"]):
        warnings.append(tr("ai.warnHttp"))
    if not out["model_fast"] and not out["model_smart"]:
        warnings.append(tr("ai.warnNoModel"))
    return out, warnings


# ---------------------------------------------------------------- mutations

def save_profile(obj, order=None):
    """Upsert one profile (and optionally the order). -> (profile, warnings)."""
    with _LOCK:
        doc = load()
        by = {p["id"]: p for p in doc["profiles"]}
        prev = by.get(str(obj.get("id") or "")) if isinstance(obj, dict) else None
        prof, warnings = validate(obj, by.keys(), prev)
        by[prof["id"]] = prof
        doc["profiles"] = [by[p["id"]] if p["id"] == prof["id"] else p for p in doc["profiles"]]
        if prof["id"] not in [p["id"] for p in doc["profiles"]]:
            doc["profiles"].append(prof)
        if prof.get("draft"):
            doc["order"] = [i for i in doc["order"] if i != prof["id"]]
        elif prof["id"] not in doc["order"]:
            doc["order"].append(prof["id"])
        if order is not None:
            doc["order"] = _clean_order(order, doc)
        _store(doc)
    return prof, warnings


def _clean_order(order, doc):
    if not isinstance(order, list):
        raise ProfileError("AI_BAD_PROFILE", tr("ai.orderNotList"))
    ids = [p["id"] for p in doc["profiles"] if not p.get("draft")]
    out = [str(i) for i in order if str(i) in ids]
    return list(dict.fromkeys(out + [i for i in ids if i not in out]))


def set_order(order):
    with _LOCK:
        doc = load()
        doc["order"] = _clean_order(order, doc)
        _store(doc)
        return doc["order"]


def activate(pid):
    with _LOCK:
        doc = load()
        p = next((x for x in doc["profiles"] if x["id"] == pid), None)
        if not p:
            raise ProfileError("AI_NO_PROFILE", tr("ai.noProfile", pid=pid))
        p["enabled"] = True
        p.pop("draft", None)
        doc["order"] = [pid] + [i for i in doc["order"] if i != pid]
        _store(doc)
        return doc["order"]


def remove(pid):
    with _LOCK:
        doc = load()
        n = len(doc["profiles"])
        doc["profiles"] = [p for p in doc["profiles"] if p["id"] != pid]
        doc["order"] = [i for i in doc["order"] if i != pid]
        if len(doc["profiles"]) == n:
            raise ProfileError("AI_NO_PROFILE", tr("ai.noProfile", pid=pid))
        _store(doc)
    KS.delete_key(pid)
    hc = _health_all()
    if hc.pop(pid, None) is not None:
        _write(health_path(), hc)
    return True


def _last_touch(p):
    return max(float(p.get("created") or 0), float(p.get("touched") or 0))


def touch_draft(pid):
    """Mark a draft as still in use (set-key from the wizard), so prune_drafts keeps it and its key."""
    with _LOCK:
        doc = load()
        p = next((x for x in doc["profiles"] if x["id"] == pid and x.get("draft")), None)
        if p:
            p["touched"] = time.time()
            _store(doc)


def prune_drafts(max_age=DRAFT_TTL):
    """Drafts left behind by a closed wizard (untouched for an hour) are removed with their key."""
    doc = load()
    old = [p["id"] for p in doc["profiles"] if p.get("draft") and time.time() - _last_touch(p) > max_age]
    for pid in old:
        try:
            remove(pid)
        except ProfileError:
            pass
    return old


# ---------------------------------------------------------------- health cache (no secrets)

def _health_all():
    try:
        d = json.loads(health_path().read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def health_get(pid=None):
    d = _health_all()
    return d.get(pid) if pid else d


def health_put(pid, row):
    from ..util import redact
    d = _health_all()
    cur = dict(d.get(pid) or {})
    cur.update({k: (redact(v) if isinstance(v, str) else v) for k, v in row.items()})
    cur["at"] = round(time.time(), 1)
    d[pid] = cur
    try:
        _write(health_path(), d)
    except OSError:
        pass
    return cur


# ---------------------------------------------------------------- public view (for the panel)

def public(p, active_id=None, health=None):
    pre = preset(p.get("kind"))
    out = {k: p.get(k) for k in ("id", "name", "kind", "base_url", "model_fast", "model_smart", "vision_model",
                                 "enabled", "json_mode", "extra_headers")}
    out["name"] = display_name(p)
    if p.get("kind") == "grok_local" and not out["base_url"]:
        from .client import grok_env_base
        out["base_url_effective"] = grok_env_base()
    out["draft"] = bool(p.get("draft"))
    out["has_key"] = has_key(p)
    out["key_error"] = KS.key_error(p["id"]) if KS.has_key(p["id"]) else ""
    out["key_from_env"] = bool(out["has_key"] and (not KS.has_key(p["id"]) or out["key_error"]))
    out["needs_key"] = needs_key(p)
    out["active"] = p["id"] == active_id
    out["health"] = health or None
    out["paid"] = bool(pre.get("paid"))
    return out


def _pkey(kind, field):
    """Locale key of a preset text: ai.preset.grokLocal.name, ai.preset.gemini.note, ..."""
    camel = re.sub(r"_([a-z])", lambda m: m.group(1).upper(), kind)
    return f"ai.preset.{camel}.{field}"


def preset_text(kind, field):
    """Preset name / note in the UI language (PRESETS keeps the Indonesian default, also stored in profiles)."""
    pre = preset(kind)
    k = _pkey(kind, field)
    return tr(k) if tr_has(k) else pre.get(field, "")


def display_name(p):
    """Profile name for the UI: an untouched preset default ("Proxy lokal (kompatibel OpenAI)") follows the UI language."""
    name = (p or {}).get("name") or (p or {}).get("id") or ""
    kind = (p or {}).get("kind")
    if kind in PRESETS and (name == PRESETS[kind]["name"] or name in LEGACY_NAMES.get(kind, ())):
        return preset_text(kind, "name")
    return name


def presets_public():
    return [{"kind": k, "name": preset_text(k, "name"), "base_url": v["base_url"], "model_fast": v["model_fast"],
             "model_smart": v["model_smart"], "vision_model": v["vision_model"], "needs_key": v["needs_key"],
             "key_url": v["key_url"], "note": preset_text(k, "note") if v["note"] else "", "paid": v["paid"],
             "adapter": v["adapter"]}
            for k, v in PRESETS.items()]


def snapshot(probe=False):
    """`cli.py ai profiles` result."""
    from ..util import setting
    prune_drafts()
    doc = load()
    if probe:
        from . import client
        client.health(write_cache=True)
    hc = _health_all()
    rt = [p["id"] for p in route()]
    active = rt[0] if rt else None
    return {"profiles": [public(p, active, hc.get(p["id"])) for p in doc["profiles"]],
            "order": doc["order"], "route": rt, "active": active, "synthesized": doc["synthesized"],
            "disabled": not bool(setting("ai", True)), "presets": presets_public()}


# ---------------------------------------------------------------- `cli.py ai ...`

USAGE = ("ai profiles [--probe] | ai save [file] (profile JSON on stdin) | ai remove <id> | "
         "ai set-key <id> (key on stdin) | ai delete-key <id> | ai models <id> | ai test <id> [--vision] [--quick] | "
         "ai activate <id> | ai order (JSON list on stdin) | ai health")


def _read_stdin(stdin, limit=200_000):
    if stdin is None:
        return ""
    data = stdin.read(limit + 1)
    if len(data) > limit:
        raise ProfileError("AI_STDIN", tr("ai.inputTooBig"))
    return data


def _need_id(args):
    if not args or not _ID.match(args[0]):
        raise ProfileError("AI_BAD_ARGS", tr("ai.needId"), USAGE)
    return args[0]


def _need_profile(pid):
    p = get(pid)
    if not p:
        raise ProfileError("AI_NO_PROFILE", tr("ai.noProfile", pid=pid), tr("ai.reloadHint"))
    return p


def cli_main(argv, emit, stdin=None):
    """Dispatch `cli.py ai <sub> ...`; exactly one result or error event. -> exit code."""
    from ..util import redact
    try:
        data = _dispatch(list(argv or []), stdin)
        emit.result(data)
        return 0
    except ProfileError as e:
        emit.error(e.code, e.msg, e.hint)
        return 1
    except KS.KeystoreError as e:
        emit.error("AI_KEYSTORE", str(e), tr("ai.resaveKeyHint"))
        return 1
    except Exception as e:  # noqa: BLE001
        import traceback
        emit.error("INTERNAL", redact(tr("ai.cmdFailed", type=type(e).__name__, err=str(e))), redact(traceback.format_exc()[-1200:]))
        return 1


def _dispatch(argv, stdin):
    if not argv:
        raise ProfileError("AI_BAD_ARGS", tr("ai.noSub"), USAGE)
    sub, args = argv[0], argv[1:]
    flags = {a for a in args if a.startswith("--")}
    args = [a for a in args if not a.startswith("--")]
    if sub == "profiles":
        return snapshot(probe="--probe" in flags)
    if sub == "save":
        if args:
            text = open(args[0], encoding="utf-8-sig").read()
        else:
            text = _read_stdin(stdin)
        try:
            obj = json.loads(text or "null")
        except ValueError:
            raise ProfileError("AI_BAD_PROFILE", tr("ai.profileNotJson")) from None
        if not isinstance(obj, dict):
            raise ProfileError("AI_BAD_PROFILE", tr("ai.profileNotObject"))
        if "profile" in obj or "order" in obj:
            prof, warnings = (save_profile(obj["profile"], obj.get("order")) if obj.get("profile") is not None
                              else (None, []))
            if prof is None:
                set_order(obj["order"])
        else:
            prof, warnings = save_profile(obj)
        doc = load()
        return {"saved": prof["id"] if prof else None, "warnings": warnings, "order": doc["order"],
                "profile": public(prof, (route() or [{}])[0].get("id")) if prof else None}
    if sub == "order":
        try:
            order = json.loads(_read_stdin(stdin) or "null")
        except ValueError:
            raise ProfileError("AI_BAD_ARGS", tr("ai.orderNotJson")) from None
        return {"order": set_order(order)}
    if sub == "remove":
        pid = _need_id(args)
        remove(pid)
        return {"removed": pid, "order": load()["order"]}
    if sub == "set-key":
        pid = _need_id(args)
        p = _need_profile(pid)
        key = _read_stdin(stdin, 8192).strip().splitlines()
        key = key[0].strip() if key else ""
        if not key:
            raise ProfileError("AI_NO_KEY", tr("ai.keyEmpty"), tr("ai.keyEmptyHint"))
        KS.set_key(pid, key)
        key = None  # noqa: F841 - drop the reference early
        touch_draft(pid)
        from . import client
        client.forget(pid)
        return {"id": pid, "has_key": has_key(p)}
    if sub == "delete-key":
        pid = _need_id(args)
        p = _need_profile(pid)
        existed = KS.delete_key(pid)
        from . import client
        client.forget(pid)
        return {"id": pid, "deleted": existed, "has_key": has_key(p)}
    if sub == "activate":
        pid = _need_id(args)
        return {"active": pid, "order": activate(pid)}
    if sub == "models":
        pid = _need_id(args)
        _need_profile(pid)
        from . import client
        return client.list_models(pid)
    if sub == "test":
        pid = _need_id(args)
        _need_profile(pid)
        from . import client
        return client.test_profile(pid, vision="--vision" in flags, quick="--quick" in flags)
    if sub == "health":
        from . import client
        return client.health(write_cache=True)
    raise ProfileError("AI_BAD_ARGS", tr("ai.unknownSub", sub=sub), USAGE)


if __name__ == "__main__":  # pragma: no cover - use engine/cli.py ai ...
    print(USAGE, file=sys.stderr)
