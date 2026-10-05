"""Stdlib-only AI client with provider profiles (docs/AI_PROVIDERS.md, ENGINE_API.md section 7).
Default provider = a local OpenAI-compatible proxy (http://127.0.0.1:8168/v1, optional key in .env).
Other profiles: OpenRouter, Gemini, OpenAI, xAI, Anthropic (native Messages API), DeepSeek, Groq, Ollama, custom.
Never prints, logs or returns a key.

    from ac.ai import client as ai
    ai.health()                                   # {"ok", "ms", "base_url", "model", "has_key", "disabled", "route", ...}
    obj = ai.chat_json(messages, schema=SCHEMA, model="smart", tag="chapters")
    obj = ai.chat_vision("Warna apa?", ["shot.png"], schema=...)             # picks the profile's vision model

Models: pass a SLOT ("fast" = extraction, "smart" = judgment, "vision" = images) and every profile in the route
uses its own model for it. Concrete names still work: on the local proxy profile they go to the proxy as they are; the
Grok-style aliases (grok-fast / grok-auto) map to fast / smart on other providers.

Routing: profiles in providers.route() order (active first, then fallbacks). A provider that is down, rejects the
key, is out of quota or answers garbage is skipped and the next one is asked; when all fail the caller gets
AIError and runs its rule fallback (prompts.py). Circuit breaker, health cache and response_format support are
tracked per profile.

Contract for callers: every AI feature is OPTIONAL. Public calls either return a value or raise AIError
fast; callers catch AIError and fall back to a heuristic (see prompts.py). Nothing here may block a core
feature (cut, caption, export).

Local proxy preset (kind grok_local, http://127.0.0.1:8168/v1 by default, key optional in .env): any
OpenAI-compatible endpoint running on this computer. What the client assumes about it:
- model names are passed through unchanged; the preset defaults are the aliases grok-fast / grok-auto.
- response_format / temperature / max_tokens may be ignored -> JSON is enforced by prompt + parse + repair.
- 503 = the proxy has no capacity right now (rests 5 min); it may retry upstream on its own before answering.
- every request is stateless (no memory between calls).
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import random
import re
import socket
import struct
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from ..i18n import tr
from . import keystore as KS
from . import providers as PV

__all__ = ["AIError", "AIUnavailable", "AIBadOutput", "config", "available", "health", "chat", "chat_json",
           "chat_vision", "image_part", "list_models", "test_profile", "resolve_model", "cache_identity", "cache_identities",
           "metrics_mark", "served_since", "forget", "extract_json", "validate_schema", "chunk_by_chars",
           "map_parallel", "est_tokens", "METRICS", "Cache"]

SLOTS = PV.SLOTS
GROK_ALIASES = {"grok-fast": "fast", "grok-3-mini": "fast", "grok-2": "fast", "grok-3": "fast",
                "grok-auto": "smart", "grok-4": "smart", "gpt-4o": "smart", "gpt-4": "smart"}
ANTHROPIC_VERSION = "2023-06-01"
USER_AGENT = "Klipora/2.0 (+stdlib urllib)"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Never follow a redirect. urllib's default handler copies Authorization / x-api-key to the Location host
    (even another host or plain http) and turns a POST into a GET whose answer it accepts. A 3xx therefore
    surfaces as HTTPError and every caller reports it as a wrong URL."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D102
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


def _open(req, timeout):
    """Every AI HTTP request goes through here (no redirects, see _NoRedirect)."""
    return _OPENER.open(req, timeout=timeout)


def _redirect_why(code):
    return tr("ai.redirect", code=str(code))


# ---------------------------------------------------------------- config

def _find_env():
    from ..util import PROJECT_DIR
    p = PROJECT_DIR / ".env"  # <repo>\.env (AI_BASE_URL / AI_API_KEY / AI_MODEL)
    return p if p.is_file() else None


def load_env(path=None):
    """Parse KEY=VALUE lines. Process env vars win over the file. Never logs values."""
    out = {}
    path = Path(path) if path else _find_env()
    if path and path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip().strip('"').strip("'")
    for k in ("AI_BASE_URL", "AI_API_KEY", "AI_MODEL", "AI_MODEL_SMART", "AI_TIMEOUT", "AI_MAX_PARALLEL",
              "AI_DISABLED", "AI_LOG"):
        if os.environ.get(k):
            out[k] = os.environ[k]
    return out


_ENV = load_env()


def grok_env_base():
    """Base URL of the local proxy from .env / AI_BASE_URL (forced to 127.0.0.1 and /v1)."""
    # Windows gotcha: "localhost" resolves to ::1 first; the proxy listens on 0.0.0.0 (IPv4 only), so every
    # request waited ~2.05 s for the IPv6 attempt to be refused. Measured: localhost 2.05 s vs 127.0.0.1 0.004 s.
    base = _ENV.get("AI_BASE_URL", "http://127.0.0.1:8168/v1").replace("://localhost", "://127.0.0.1")
    if not base.rstrip("/").endswith("/v1"):
        base = base.rstrip("/") + "/v1"
    return base


def _disabled():
    return _ENV.get("AI_DISABLED", "") in ("1", "true", "yes") or not _settings_ai()


def config():
    """Effective settings without any key (safe to print). base_url/model describe the local proxy from
    .env (legacy callers such as the AI image call in tools/broll.py post there); has_key = at least one provider in
    the route has its credentials; active = the profile that would be asked first; route = fallback order."""
    rt = PV.route()
    ready = [p for p in rt if _has_creds(p)]
    return {"base_url": grok_env_base(),
            "model": _ENV.get("AI_MODEL", "grok-fast"),
            "timeout": float(_ENV.get("AI_TIMEOUT", 150)),
            "max_parallel": int(_ENV.get("AI_MAX_PARALLEL", 3)),
            "disabled": _disabled(),
            "has_key": bool(ready),
            "active": ready[0]["id"] if ready else None,
            "route": [p["id"] for p in rt]}


def _settings_ai():
    """Panel setting "ai" (default on). Read through util.setting so a toggle applies without restart."""
    try:
        from ..util import setting
        return bool(setting("ai", True))
    except Exception:  # noqa: BLE001
        return True


def reload_env():
    """Re-read .env and the profiles file (worker: after the user edited them)."""
    _ENV.clear()
    _ENV.update(load_env())
    PV.reset_cache()
    forget()


def _redact(text):
    try:
        from ..util import redact
        return redact(text)
    except Exception:  # noqa: BLE001
        return "(detail disembunyikan)"


# ---------------------------------------------------------------- errors

class AIError(Exception):
    """Base: any AI failure. Callers catch this and fall back."""


class AIUnavailable(AIError):
    """Provider down, auth failure, no capacity/quota, or circuit open. Do not retry now."""


class AIBadOutput(AIError):
    """Provider answered but the content is not usable JSON after the repair attempt(s)."""

    def __init__(self, msg, raw=""):
        super().__init__(msg)
        self.raw = raw


# ---------------------------------------------------------------- per-profile state

_SEM = threading.BoundedSemaphore(max(1, int(_ENV.get("AI_MAX_PARALLEL", 3) or 3)))
_LOCK = threading.RLock()
_BREAKERS: dict[str, dict] = {}   # profile id -> {"fails", "open_until", "why", "fp"}
_HEALTH: dict[str, dict] = {}     # profile id -> {"at", "ok", "ms", "why", "fp"} (cheap /models probe)
_NO_FMT: dict[str, str] = {}      # profile id -> fp, server rejected response_format (this process)
METRICS: list[dict] = []          # one row per HTTP attempt; no prompt text, no key


def _brk(pid):
    with _LOCK:
        return _BREAKERS.setdefault(pid, {"fails": 0, "open_until": 0.0, "why": ""})


def _fp(p):
    """Fingerprint of a profile + its stored key blob: per-profile state (circuit, probe result, JSON-mode memory)
    is dropped as soon as the user edits the profile or replaces the key, also in a long-running worker."""
    raw = json.dumps(p, sort_keys=True, default=str) + KS.blob_tag(p["id"])
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


def _trip(p, why, seconds):
    b = _brk(p["id"])
    with _LOCK:
        b.update(open_until=time.time() + seconds, why=why, fp=_fp(p), since=time.time(), fails=0, bad=0)


def _tested_ok_since(pid, t):
    """A successful `ai test` (separate cli.py process) after time t: written to ai_health.json as test_ok_at."""
    try:
        return float((PV.health_get(pid) or {}).get("test_ok_at") or 0) > t
    except Exception:  # noqa: BLE001
        return False


def _is_open(p):
    b = _brk(p["id"])
    if time.time() >= b["open_until"]:
        return False
    # key or profile changed since the trip, or the user just ran a successful "Tes koneksi": try again now
    if (b.get("fp") and b["fp"] != _fp(p)) or _tested_ok_since(p["id"], b.get("since", 0.0)):
        with _LOCK:
            b.update(open_until=0.0, fails=0, bad=0, why="")
        return False
    return True


def _health_of(p):
    h = _HEALTH.get(p["id"])
    return h if h and h.get("fp") == _fp(p) else None


def forget(pid=None):
    """Clear breaker, health and format memory (after a key or profile change)."""
    with _LOCK:
        for d in (_BREAKERS, _HEALTH):
            if pid is None:
                d.clear()
            else:
                d.pop(pid, None)
        if pid is None:
            _NO_FMT.clear()
        else:
            _NO_FMT.pop(pid, None)


def _record(row):
    with _LOCK:
        METRICS.append(row)
    log = _ENV.get("AI_LOG")
    if log:
        try:
            with open(log, "a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
        except OSError:
            pass


def metrics_mark():
    return len(METRICS)


def served_since(mark):
    """(profile id, model, [profile ids that failed before it]) of the first answered request since `mark`."""
    failed = []
    for row in METRICS[mark:]:
        if row.get("ok") and row.get("profile"):
            return row["profile"], row.get("model"), [p for p in dict.fromkeys(failed) if p != row["profile"]]
        if row.get("profile") and not row.get("ok"):
            failed.append(row["profile"])
    return None


# ---------------------------------------------------------------- profiles -> endpoints

_LOOPBACK = ("127.0.0.1", "::1", "localhost")


def env_key_allowed(p):
    """The .env AI_API_KEY (local proxy key) is only sent to the built-in local proxy profile when it points at this
    computer or at the .env URL itself. A second local proxy profile or an edited remote URL needs its own key."""
    if p.get("id") != "grok_local" or p.get("kind") != "grok_local":
        return False
    base = _base_url(p)
    host = (urllib.parse.urlparse(base).hostname or "").lower()
    return host in _LOOPBACK or base == grok_env_base().rstrip("/")


def _env_key(p):
    return _ENV.get("AI_API_KEY", "") if env_key_allowed(p) else ""


def _key_for(p):
    return KS.get_key(p["id"]) or _env_key(p)


def creds_why(p):
    """"" when profile p can authenticate, else why not (safe to show). A stored key that this Windows user
    cannot decrypt counts as missing, so no unauthenticated request is sent (it would trip "kunci ditolak")."""
    pid = p["id"]
    env_ok = bool(_env_key(p))
    if KS.has_key(pid):
        err = KS.key_error(pid)
        return "" if not err or env_ok else err
    if not PV.needs_key(p) or env_ok:
        return ""
    return tr("ai.envKeyMissing") if p.get("kind") == "grok_local" else tr("ai.keyMissing")


def _has_creds(p):
    return not creds_why(p)


def _sentence(t):
    return (t[:1].upper() + t[1:] + ("" if t.endswith(".") else ".")) if t else t


def _base_url(p):
    if p.get("kind") == "grok_local":
        b = p.get("base_url") or ""
        if not b or os.environ.get("AI_BASE_URL"):    # .env value, or the dev/test override
            return grok_env_base().rstrip("/")
        b = b.replace("://localhost", "://127.0.0.1").rstrip("/")
        return b if b.endswith("/v1") else b + "/v1"
    return str(p.get("base_url") or "").replace("://localhost", "://127.0.0.1").rstrip("/")


class Endpoint:
    """One profile + the concrete model to ask. Holds no key (read at request time)."""

    def __init__(self, p, model):
        self.p, self.pid, self.kind, self.model = p, p["id"], p.get("kind") or "custom", model
        self.name = PV.display_name(p) or self.pid
        self.adapter = PV.preset(self.kind).get("adapter", "openai")
        self.base = _base_url(p)

    def headers(self, body=True):
        h = {"Accept": "application/json", "User-Agent": USER_AGENT}
        if body:
            h["Content-Type"] = "application/json"
        h.update(PV.preset(self.kind).get("headers") or {})
        h.update(self.p.get("extra_headers") or {})
        key = _key_for(self.p)
        if self.adapter == "anthropic":
            h["anthropic-version"] = ANTHROPIC_VERSION
            if key:
                h["x-api-key"] = key
        elif key or self.kind == "grok_local":
            h["Authorization"] = f"Bearer {key}"
        return h

    def models_url(self, limit=None):
        u = self.base + "/models"
        if self.adapter == "anthropic":
            u += f"?limit={limit or 1000}"
        return u

    def chat_url(self):
        return self.base + ("/messages" if self.adapter == "anthropic" else "/chat/completions")


def resolve_model(p, model=None, first=True, vision=False):
    """Concrete model for profile `p`. model = slot ("fast"/"smart"/"vision"), Grok alias, concrete name or None.
    -> model name or None (profile cannot serve this request, e.g. no vision model)."""
    def slot(s):
        return str((p.get("vision_model") if s == "vision" else p.get(f"model_{s}")) or "").strip()
    m = str(model or "").strip()
    if vision or m == "vision":
        if m and m not in SLOTS and m not in GROK_ALIASES and (first or m == slot("vision")):
            return m
        return slot("vision") or None
    if not m:
        m = "fast"
    if m in SLOTS:
        return slot(m) or slot("smart" if m == "fast" else "fast") or None
    if p.get("kind") == "grok_local":
        return m                                  # the proxy maps its own aliases (unknown -> auto)
    if m in GROK_ALIASES:
        return slot(GROK_ALIASES[m]) or slot("fast") or slot("smart") or None
    if first or m in (slot("fast"), slot("smart"), slot("vision")):
        return m
    return slot("smart") or slot("fast") or None


def _has_images(messages):
    for m in messages or ():
        c = m.get("content") if isinstance(m, dict) else None
        if isinstance(c, list) and any(isinstance(x, dict) and x.get("type") in ("image_url", "image") for x in c):
            return True
    return False


def _endpoints(model=None, messages=None, only=None, strict=True):
    """Ordered endpoints for a request -> (endpoints, skipped [{id, why, down?}]). only = one profile id
    (`ai test`; ignores enabled/breaker/health). strict=False keeps profiles with an open breaker or a failed
    probe. down=True marks a provider that is known to be down right now (reported as "gagal" to the user)."""
    vision = model == "vision" or _has_images(messages)
    if only:
        p = PV.get(only)
        profs = [p] if p else []
    else:
        profs = PV.route()
    out, skipped, first = [], [], True
    for p in profs:
        name = PV.display_name(p)
        why = creds_why(p)
        if why:
            skipped.append({"id": p["id"], "why": f"{name}: {why}"})
            continue
        if strict and not only:
            if _is_open(p):
                skipped.append({"id": p["id"], "why": f"{name}: " + tr("ai.resting", why=_brk(p['id'])['why']), "down": True})
                continue
            h = _health_of(p)
            if h and not h["ok"] and time.time() - h["at"] < 30:
                skipped.append({"id": p["id"], "why": f"{name}: {h.get('why') or tr('ai.noResponse')}", "down": True})
                continue
        m = resolve_model(p, model, first, vision)
        if not m:
            skipped.append({"id": p["id"], "why": f"{name}: " + tr("ai.noVisionModel" if vision else "ai.noModel")})
            continue
        out.append(Endpoint(p, m))
        first = False
    return out, skipped


def cache_identities(model=None):
    """Every (profile id, model) in the route that has credentials, in order: tasks.run() looks the result cache up
    under each of them, so an answer paid for on a fallback provider is reused while the primary is still down."""
    out = []
    for p in PV.route():
        if _has_creds(p):
            m = resolve_model(p, model, not out)
            if m and (p["id"], m) not in out:
                out.append((p["id"], m))
    return out


def cache_identity(model=None):
    """(profile id, model) that would answer `model` first: part of every AI result-cache key, so answers of
    different providers/models never mix. When nothing is reachable the first configured profile is used, so
    an earlier answer can still be served from the cache offline."""
    eps, _ = _endpoints(model)
    if eps:
        return eps[0].pid, eps[0].model
    for p in PV.route():
        m = resolve_model(p, model, True)
        if m:
            return p["id"], m
    return None


# ---------------------------------------------------------------- health (free: GET /models, no tokens)

def _probe(p, timeout=1.5, max_age=30.0):
    pid = p["id"]
    h = _health_of(p)
    if h and time.time() - h["at"] < max_age:
        return h
    ep = Endpoint(p, None)
    t0, ok, why = time.time(), False, ""
    try:
        req = urllib.request.Request(ep.models_url(limit=1), headers=ep.headers(body=False))
        with _open(req, timeout) as r:
            ok = 200 <= r.status < 300
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            why = tr("ai.keyRejectedHttp", code=str(e.code))
        elif e.code == 402:
            why = tr("ai.quotaHttp")
        elif e.code == 429:
            why = tr("ai.rateLimitedHttp")
        elif 300 <= e.code < 400:
            why = _redirect_why(e.code)
        elif e.code >= 500 and e.code != 501:
            why = tr("ai.serverErrorHttp", code=str(e.code))
        else:
            ok = True                              # 404/405/501...: server answers, it just has no /models list
    except (urllib.error.URLError, OSError, ValueError) as e:
        reason = getattr(e, "reason", e)
        why = (tr("ai.proxyNoResponse", url=ep.base) if p.get("kind") == "grok_local"
               else tr("ai.noResponseWhy", reason=str(reason)))
    h = {"at": time.time(), "ok": ok, "ms": int((time.time() - t0) * 1000), "why": _redact(why)[:200], "fp": _fp(p)}
    _HEALTH[pid] = h
    return h


def available(timeout=1.5, max_age=30.0):
    """Cheap probe: True when at least one provider in the route answers GET /models (no quota spent).
    Cached max_age seconds per profile. Quota/auth problems on chat still show up as AIUnavailable later."""
    if _disabled():
        return False
    for p in PV.route():
        if not _has_creds(p) or _is_open(p):
            continue
        if _probe(p, timeout, max_age)["ok"]:
            return True
    return False


def health(timeout=1.5, write_cache=False):
    """Status for the panel's AI dot / `cli.py health` (no quota spent, keys never included). Probes every
    profile in the route in parallel. ok = at least one provider answers. base_url/model/profile = the one that
    would answer first. route = per-profile rows."""
    t0 = time.time()
    rt = PV.route()
    disabled = _disabled()

    def one(p):
        row = {"id": p["id"], "name": PV.display_name(p), "kind": p.get("kind"), "has_key": _has_creds(p)}
        if not row["has_key"]:
            row.update(ok=False, why=creds_why(p))
            return row
        h = _probe(p, timeout, 0.0)
        row.update(ok=h["ok"], ms=h["ms"], why=h["why"])
        if _is_open(p):
            row["circuit"] = _brk(p["id"])["why"]
        return row

    if disabled:
        rows = [{"id": p["id"], "name": PV.display_name(p), "kind": p.get("kind"), "has_key": _has_creds(p),
                 "ok": False, "why": tr("ai.disabledShort")} for p in rt]
    elif rt:
        with ThreadPoolExecutor(max_workers=min(6, len(rt))) as ex:
            rows = list(ex.map(one, rt))
    else:
        rows = []
    good = [r for r in rows if r.get("ok")]
    lead_row = good[0] if good else (rows[0] if rows else None)
    lead = PV.get(lead_row["id"]) if lead_row else None
    out = {"ok": bool(good) and not disabled, "ms": int((time.time() - t0) * 1000),
           "base_url": _base_url(lead) if lead else "", "model": (resolve_model(lead, "fast") or "") if lead else "",
           "has_key": any(r["has_key"] for r in rows), "disabled": disabled,
           "profile": {"id": lead["id"], "name": PV.display_name(lead), "kind": lead.get("kind")} if lead else None,
           "route": rows}
    if lead_row and lead_row.get("circuit"):
        out["circuit"] = lead_row["circuit"]
    if disabled:
        out["why"] = tr("ai.disabledSettings")
    elif not rows:
        out["why"] = tr("ai.noActiveProvider")
    elif not out["has_key"]:
        out["why"] = rows[0]["why"] if len(rows) == 1 else tr("ai.keyMissing")
    elif not good:
        out["why"] = rows[0]["why"] if len(rows) == 1 else "; ".join(f"{r['name']}: {r['why']}" for r in rows[:3])
    elif good[0] is not rows[0]:
        out["note"] = tr("ai.notReadyUsing", name=rows[0]["name"], other=good[0]["name"])
    if write_cache:
        for r in rows:
            PV.health_put(r["id"], {"ok": bool(r.get("ok")), "ms": r.get("ms"), "why": r.get("why", ""), "probe": True})
    return out


# ---------------------------------------------------------------- HTTP

def est_tokens(text):
    """Same chars/4 heuristic the proxy logs. Indonesian transcripts measure ~4.3 chars/token on GPT-style
    tokenizers, so this slightly over-estimates (safe side)."""
    return (len(text) + 3) // 4


def _text_of(content):
    if isinstance(content, list):
        return "\n".join(str(x.get("text", "")) for x in content if isinstance(x, dict) and x.get("type") == "text")
    return str(content or "")


def _chars(msgs):
    n = 0
    for m in msgs:
        c = m.get("content")
        n += len(_text_of(c)) + (1000 * sum(1 for x in c if isinstance(x, dict) and x.get("type") != "text")
                                  if isinstance(c, list) else 0)
    return n


def _to_anthropic(messages):
    """OpenAI-style messages -> (system text, Messages API messages). image_url data URLs -> base64 image blocks."""
    system, conv = [], []
    for m in messages:
        role, c = m.get("role"), m.get("content")
        if role == "system":
            system.append(_text_of(c))
            continue
        if isinstance(c, list):
            parts = []
            for x in c:
                if not isinstance(x, dict):
                    continue
                if x.get("type") == "text":
                    parts.append({"type": "text", "text": str(x.get("text", ""))})
                elif x.get("type") == "image_url":
                    u = x.get("image_url")
                    u = u.get("url", "") if isinstance(u, dict) else str(u or "")
                    mm = re.match(r"data:([\w/+.-]+);base64,(.*)$", u, re.S)
                    if mm:
                        parts.append({"type": "image", "source": {"type": "base64", "media_type": mm.group(1),
                                                                  "data": mm.group(2)}})
                    elif u:
                        parts.append({"type": "image", "source": {"type": "url", "url": u}})
            c = parts
        else:
            c = str(c or "")
        conv.append({"role": "assistant" if role == "assistant" else "user", "content": c})
    return "\n\n".join(s for s in system if s), conv


def _post(ep, messages, timeout, json_fmt=False):
    """One HTTP request -> (content, reasoning). Raises urllib errors / ValueError / AIError."""
    if ep.adapter == "anthropic":
        system, conv = _to_anthropic(messages)
        body = {"model": ep.model, "max_tokens": 16000, "messages": conv}
        if system:
            body["system"] = system
    else:
        body = {"model": ep.model, "messages": messages, "stream": False}
        if json_fmt:
            body["response_format"] = {"type": "json_object"}
    req = urllib.request.Request(ep.chat_url(), data=json.dumps(body).encode("utf-8"), method="POST",
                                 headers=ep.headers())
    with _open(req, timeout) as r:
        data = json.loads(r.read().decode("utf-8"))   # HTML login / captive portal -> ValueError (not retried)
    if not isinstance(data, dict):
        raise ValueError("reply is not a JSON object")
    if ep.adapter == "anthropic":
        if data.get("stop_reason") == "refusal":
            raise AIError(f"{ep.name}: " + tr("ai.refusal"))
        text = "".join(str(b.get("text", "")) for b in data.get("content") or [] if isinstance(b, dict)
                       and b.get("type") == "text")
        return text, ""
    if data.get("error") and not data.get("choices"):   # OpenRouter reports upstream errors inside a 200
        err = data["error"]
        msg = f"{ep.name}: {(err.get('message') if isinstance(err, dict) else err) or 'error'}"[:200]
        try:
            code = int(err.get("code")) if isinstance(err, dict) else 0
        except (TypeError, ValueError):
            code = 0
        if code in (401, 402, 403, 429):          # auth / credit / rate limit: same policy as the HTTP status
            raise _Status(code, msg)
        raise AIError(msg)
    msg = (data.get("choices") or [{}])[0].get("message") or {}
    content = msg.get("content") or ""
    if isinstance(content, list):
        content = "".join(str(x.get("text", "")) for x in content if isinstance(x, dict))
    return content, msg.get("reasoning_content") or msg.get("reasoning") or ""


class _Status(AIError):
    """Auth / quota / rate-limit error reported inside a 200 body (OpenRouter): handled like that HTTP status."""

    def __init__(self, code, msg):
        super().__init__(msg)
        self.code = code


def chat(messages, model=None, json_mode=False, timeout=None, retries=1, schema=None, check=None,
         repair=1, tag="", trace=None, profile=None):
    """Send a chat to the first provider that can answer. Returns the reply text, or (json_mode=True) the
    parsed + validated JSON value.

    model:   slot "fast" | "smart" | "vision", a Grok alias, or a concrete model name (see resolve_model).
             Messages with image parts always use the vision slot.
    retries: extra attempts per provider on transient errors (HTTP 5xx, empty reply). A timeout is never retried:
             the provider may still be working (and billing) on the first request, so the next profile is asked
             and the slow one rests 90 s. A local proxy may also retry upstream on its own.
    schema:  JSON-schema subset (see validate_schema); check: fn(obj) -> list[str] semantic errors.
    repair:  on bad JSON / schema / check errors, re-ask with the errors (costs 1 request each). The budget is
             shared by the whole route, so a chain of providers that all answer prose costs at most
             len(route) + repair requests. A profile that answers non-JSON twice in a row rests 5 min.
    trace:   optional dict filled with {"raw", "errors", "repairs", "tried", "profile", "model", "json_format"}.
    profile: ask only this profile id (connection tests); otherwise the route with fallbacks.
    Raises AIUnavailable (fallback now) or AIBadOutput / AIError."""
    if _disabled() and not profile:
        raise AIUnavailable(tr("ai.disabledSettings"))
    eps, skipped = _endpoints(model, messages, only=profile)
    for s in skipped:
        if s.get("down"):   # visible to tasks.run (served_since) as a provider that could not answer
            _record({"t": round(time.time(), 3), "tag": tag, "profile": s["id"], "ok": False, "skipped": s["why"][:120]})
    if not eps:
        raise AIUnavailable(tr("ai.noProviderReady")
                            + (": " + "; ".join(s["why"] for s in skipped[:3]) if skipped else ""))
    if trace is not None:
        trace.update(raw=[], errors=[], repairs=0, tried=[])
    last = None
    budget = {"repair": max(0, int(repair or 0))}
    for i, ep in enumerate(eps):
        try:
            out = _chat_on(ep, messages, json_mode, timeout, retries, schema, check, budget, tag, trace)
            if trace is not None:
                trace.update(profile=ep.pid, model=ep.model)
            return out
        except AIError as e:
            last = e
            if trace is not None:
                trace["tried"].append({"profile": ep.pid, "model": ep.model, "error": _redact(str(e))[:200]})
            if i + 1 < len(eps):
                try:
                    from ..util import log
                    log().info("AI %s: %s failed (%s), trying %s", tag, ep.pid, type(e).__name__, eps[i + 1].pid)
                except Exception:  # noqa: BLE001
                    pass
    raise last


def _chat_on(ep, messages, json_mode, timeout, retries, schema, check, budget, tag, trace):
    timeout = timeout or float(_ENV.get("AI_TIMEOUT", 150))
    msgs = list(messages)
    if json_mode:
        msgs = _with_json_rule(msgs)
    jm = ep.p.get("json_mode") or "auto"
    use_fmt = bool(json_mode and ep.adapter == "openai" and _NO_FMT.get(ep.pid) != _fp(ep.p) and (
        jm == "on" or (jm == "auto" and PV.preset(ep.kind).get("json_fmt") and (schema or {}).get("type") == "object")))
    while True:
        text, used_fmt = _send(ep, msgs, timeout, retries, tag, use_fmt)
        if trace is not None:
            trace["raw"].append(text)
            trace["json_format"] = "response_format" if used_fmt else "prompt"
        if not json_mode:
            return text
        try:
            obj = extract_json(text, expect=dict if (schema or {}).get("type") == "object" else None)
            errs = validate_schema(obj, schema) if schema else []
            if not errs and check:
                errs = list(check(obj) or [])
        except ValueError as e:
            obj, errs = None, [f"not valid JSON: {e}"]
        if trace is not None:
            trace["errors"].append(errs)
        b = _brk(ep.pid)
        if not errs:
            with _LOCK:
                b["bad"] = 0
            return obj
        if budget["repair"] <= 0:
            if obj is None:                        # prose / refusal, not just a schema slip
                with _LOCK:
                    b["bad"] = b.get("bad", 0) + 1
                    strikes = b["bad"]
                if strikes >= 2:
                    _trip(ep.p, tr("ai.notJsonTwice"), 300)
            raise AIBadOutput(f"{ep.name}: " + "; ".join(errs[:5]), raw=text)
        budget["repair"] -= 1
        if trace is not None:
            trace["repairs"] += 1
        msgs = msgs + [{"role": "assistant", "content": text[:6000]},
                       {"role": "user", "content": "Your previous reply is invalid:\n- " + "\n- ".join(errs[:8])
                        + "\nReturn ONLY the corrected JSON (same task, same schema). No prose, no markdown."}]


def chat_json(messages, schema=None, check=None, **kw):
    return chat(messages, json_mode=True, schema=schema, check=check, **kw)


_JSON_RULE = ("OUTPUT FORMAT: reply with exactly one JSON value and nothing else. No markdown, no code fence, "
              "no explanation before or after. Use double quotes. Do not search the web.")


def _with_json_rule(msgs):
    msgs = [dict(m) for m in msgs]
    for m in msgs:
        if m["role"] == "system":
            if isinstance(m["content"], list):
                m["content"] = list(m["content"]) + [{"type": "text", "text": _JSON_RULE}]
            else:
                m["content"] = m["content"].rstrip() + "\n\n" + _JSON_RULE
            return msgs
    return [{"role": "system", "content": _JSON_RULE}] + msgs


def _fmt_rejected(detail):
    return bool(re.search(r"(?i)response_format|json_object|json mode|json_mode", detail or ""))


def _status_error(ep, code, detail):
    """HTTP status (or the same code inside a 200 body) -> exception to raise now, or None = transient (retry)."""
    if code in (401, 403):
        _trip(ep.p, tr("ai.keyRejected"), 600)
        return AIUnavailable(f"{ep.name}: " + tr("ai.keyRejectedHttp", code=str(code)))
    if code == 402:
        _trip(ep.p, tr("ai.outOfCredit"), 600)
        return AIUnavailable(f"{ep.name}: " + tr("ai.quotaHttp"))
    if code == 503 and ep.kind == "grok_local":   # local proxy has no capacity right now
        _trip(ep.p, "local proxy unavailable (503)", 300)
        return AIUnavailable(f"local proxy unavailable (503): {detail[:160]}")
    if code == 429:
        _trip(ep.p, tr("ai.rateLimited"), 120)
        return AIUnavailable(f"{ep.name}: " + tr("ai.rateLimitedShort"))
    if 300 <= code < 400:                       # redirects are never followed (key + prompt stay here)
        _trip(ep.p, _redirect_why(code), 600)
        return AIUnavailable(f"{ep.name}: {_redirect_why(code)}")
    if code in (400, 404, 413, 422):            # bad model / too big / unsupported input: next provider
        return AIError(f"{ep.name}: HTTP {code}: {detail[:200]}")
    return None


def _timed_out(ep, timeout):
    """A read timeout is not retried (the provider may still be processing and billing the first request):
    the next profile is asked and this one rests 90 s, so parallel chunks do not each wait the full timeout."""
    _trip(ep.p, tr("ai.slow", n=str(int(timeout))), 90)
    return AIUnavailable(f"{ep.name}: timeout after {timeout}s")


def _send(ep, msgs, timeout, retries, tag, use_fmt=False):
    """-> (content, response_format used). Error policy per provider (breaker keyed by profile id).
    Retried (with backoff): HTTP 5xx and empty replies. Everything else fails over at once."""
    delay = 2.0
    last = None
    prompt_chars = _chars(msgs)
    attempt = 0
    while attempt <= retries:
        t0 = time.time()
        row = {"t": round(t0, 3), "tag": tag, "profile": ep.pid, "model": ep.model, "attempt": attempt,
               "in_chars": prompt_chars}
        try:
            with _SEM:
                t0 = time.time()
                content, _reasoning = _post(ep, msgs, timeout, use_fmt)
            row.update(ms=int((time.time() - t0) * 1000), out_chars=len(content))
            if not content.strip() or content.startswith("[Error:"):
                raise AIError(f"{ep.name}: " + tr("ai.emptyReply", text=content[:120]))
            row["ok"] = True
            _record(row)
            b = _brk(ep.pid)
            with _LOCK:
                b["fails"] = 0
            return content, use_fmt
        except urllib.error.HTTPError as e:
            detail = _redact(_http_detail(e))
            row.update(ms=int((time.time() - t0) * 1000), ok=False, err=f"HTTP {e.code} {detail[:120]}")
            _record(row)
            if e.code == 400 and use_fmt and _fmt_rejected(detail):
                _NO_FMT[ep.pid] = _fp(ep.p)                 # server has no JSON mode: prompt-only from now on
                use_fmt = False
                continue                            # same attempt, no backoff
            fatal = _status_error(ep, e.code, detail)
            if fatal:
                raise fatal from None
            last = AIError(f"{ep.name}: HTTP {e.code}: {detail[:200]}")
        except _Status as e:                        # 401/402/403/429 inside a 200 body (OpenRouter)
            detail = _redact(str(e))
            row.update(ms=int((time.time() - t0) * 1000), ok=False, err=f"HTTP {e.code} in body {detail[:100]}")
            _record(row)
            raise _status_error(ep, e.code, detail) from None
        except (urllib.error.URLError, ConnectionError) as e:
            reason = getattr(e, "reason", e)
            row.update(ms=int((time.time() - t0) * 1000), ok=False, err=f"conn {reason}")
            _record(row)
            if isinstance(reason, (socket.timeout, TimeoutError)):
                raise _timed_out(ep, timeout) from None
            # refused / DNS / reset -> provider is down: fail fast for 60 s
            _trip(ep.p, tr("ai.notConnected", reason=str(reason)), 60)
            _HEALTH[ep.pid] = {"at": time.time(), "ok": False, "ms": 0, "why": tr("ai.noResponse"), "fp": _fp(ep.p)}
            raise AIUnavailable(("proxy unreachable: " if ep.kind == "grok_local" else f"{ep.name} unreachable: ")
                                + str(reason)) from None
        except (socket.timeout, TimeoutError):
            row.update(ms=int((time.time() - t0) * 1000), ok=False, err="timeout")
            _record(row)
            raise _timed_out(ep, timeout) from None
        except AIError as e:
            row["ok"] = False
            row["err"] = _redact(str(e))[:120]
            _record(row)
            last = e
        except ValueError as e:                     # 200 with a non-JSON body: HTML login page, wrong URL
            row.update(ms=int((time.time() - t0) * 1000), ok=False, err=f"bad body {str(e)[:80]}")
            _record(row)
            _trip(ep.p, tr("ai.notJson"), 120)
            raise AIUnavailable(f"{ep.name}: " + tr("ai.notJsonUrl")) from None
        if attempt < retries:
            time.sleep(delay + random.uniform(0, 1.0))
            delay *= 2.5
        attempt += 1
    b = _brk(ep.pid)
    with _LOCK:
        b["fails"] += 1
        tired = b["fails"] >= 3
    if tired:  # 3 failed calls in a row: stop hammering this provider for 2 min
        _trip(ep.p, tr("ai.failedThrice", err=str(last)), 120)
    raise last


def _http_detail(e):
    try:
        body = e.read().decode("utf-8", "replace")
        if not body.startswith("{"):
            return body[:400]
        d = json.loads(body)
        err = d.get("error")
        if isinstance(err, dict):
            return str(err.get("message") or err.get("type") or err)
        return str(d.get("detail") or err or d.get("message") or body)[:400]
    except Exception:  # noqa: BLE001
        return ""


# ---------------------------------------------------------------- vision

def _png(rgb=(220, 30, 30), w=48, h=48):
    """Tiny solid-colour PNG (stdlib) for the vision check."""
    raw = b"".join(b"\x00" + bytes(rgb) * w for _ in range(h))

    def chunk(t, d):
        c = struct.pack(">I", len(d)) + t + d
        return c + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def image_part(src, max_side=1280, quality=85):
    """OpenAI-style image content part from a file path, bytes, or a data:/http(s) URL. Big images are
    downscaled (Pillow) to max_side px JPEG so requests stay small. Raises AIError when unreadable / > 8 MB."""
    if isinstance(src, str) and src.startswith(("data:", "http://", "https://")):
        return {"type": "image_url", "image_url": {"url": src}}
    try:
        data = bytes(src) if isinstance(src, (bytes, bytearray)) else Path(src).read_bytes()
    except OSError as e:
        raise AIError(tr("ai.imageUnreadable", err=str(e))) from None
    mime = ("image/png" if data[:8] == b"\x89PNG\r\n\x1a\n" else "image/jpeg" if data[:2] == b"\xff\xd8"
            else "image/webp" if data[8:12] == b"WEBP" else "image/gif" if data[:4] == b"GIF8" else "")
    try:
        from io import BytesIO

        from PIL import Image
        im = Image.open(BytesIO(data))
        if max(im.size) > max_side or len(data) > 1_500_000 or not mime:
            im.thumbnail((max_side, max_side))
            buf = BytesIO()
            alpha = im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info)
            if alpha:
                im.save(buf, "PNG", optimize=True)
                mime = "image/png"
            else:
                im.convert("RGB").save(buf, "JPEG", quality=quality)
                mime = "image/jpeg"
            data = buf.getvalue()
    except ImportError:
        pass
    except Exception as e:  # noqa: BLE001 - not an image Pillow understands
        if not mime:
            raise AIError(tr("ai.notImage", err=str(e))) from None
    if not mime:
        raise AIError(tr("ai.imageFormat"))
    if len(data) > 8 * 1024 * 1024:
        raise AIError(tr("ai.imageTooBig"))
    return {"type": "image_url", "image_url": {"url": f"data:{mime};base64," + base64.b64encode(data).decode("ascii")}}


def chat_vision(prompt, images, system=None, json_mode=True, schema=None, check=None, model="vision", **kw):
    """Ask about one or more images (paths, bytes or data URLs) with the route's vision models.
    Profiles without a vision model are skipped. Same errors/return as chat()."""
    content = [{"type": "text", "text": prompt}] + [image_part(i) for i in images]
    msgs = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": content}]
    return chat(msgs, model=model, json_mode=json_mode, schema=schema, check=check, **kw)


# ---------------------------------------------------------------- model lists + connection test (`cli.py ai`)

def _norm_models(data, kind):
    rows = data.get("data") if isinstance(data, dict) else data
    if isinstance(data, dict) and rows is None:
        rows = data.get("models")
    out = {}
    for r in rows or []:
        if isinstance(r, str):
            r = {"id": r}
        if not isinstance(r, dict):
            continue
        mid = str(r.get("id") or r.get("name") or r.get("model") or "").strip()
        if kind == "gemini" and mid.startswith("models/"):
            mid = mid[len("models/"):]
        if not mid or len(mid) > 200 or mid in out:
            continue
        row = {"id": mid}
        name = r.get("display_name") or r.get("displayName") or (r.get("name") if r.get("name") != mid else None)
        if name and str(name) != mid:
            row["name"] = str(name)[:120]
        ctx = (r.get("context_length") or r.get("context_window") or r.get("max_input_tokens")
               or r.get("inputTokenLimit") or (r.get("top_provider") or {}).get("context_length"))
        if isinstance(ctx, (int, float)) and ctx > 0:
            row["ctx"] = int(ctx)
        arch = r.get("architecture") or {}
        mods = arch.get("input_modalities") or r.get("input_modalities")
        if isinstance(mods, list):
            row["vision"] = "image" in mods
        elif isinstance(arch.get("modality"), str):
            row["vision"] = "image" in arch["modality"].split("->")[0]
        pr = r.get("pricing") or {}
        if isinstance(pr, dict) and str(pr.get("prompt")) == "0" and str(pr.get("completion")) == "0":
            row["free"] = True
        out[mid] = row
    return sorted(out.values(), key=lambda x: x["id"].lower())


def list_models(pid, timeout=20):
    """`cli.py ai models <id>` -> {"id", "models": [{id, name?, ctx?, vision?, free?}], "n", "ms", "unsupported"}."""
    p = PV.get(pid)
    if not p:
        raise PV.ProfileError("AI_NO_PROFILE", tr("ai.noProfile", pid=pid))
    why = creds_why(p)
    if why:
        raise PV.ProfileError("AI_NO_KEY", _sentence(why),
                              tr("ai.saveKeyFirst"))
    ep = Endpoint(p, None)
    t0 = time.time()
    try:
        req = urllib.request.Request(ep.models_url(), headers=ep.headers(body=False))
        with _open(req, timeout) as r:
            data = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        ms = int((time.time() - t0) * 1000)
        if 300 <= e.code < 400:
            raise PV.ProfileError("AI_REDIRECT", tr("ai.redirectMsg", name=ep.name, code=str(e.code)),
                                  tr("ai.redirectHint")) from None
        if e.code in (404, 405, 501):
            return {"id": pid, "models": [], "n": 0, "ms": ms, "unsupported": True,
                    "msg": tr("ai.noModelList")}
        detail = _redact(_http_detail(e))[:300]
        if e.code in (401, 403):
            PV.health_put(pid, {"ok": False, "ms": ms, "why": tr("ai.keyRejectedHttp", code=str(e.code))})
            raise PV.ProfileError("AI_AUTH", tr("ai.keyRejectedBy", name=ep.name, code=str(e.code)),
                                  tr("ai.checkKeyHint")) from None
        raise PV.ProfileError("AI_HTTP", tr("ai.httpAnswer", name=ep.name, code=str(e.code)), detail) from None
    except (urllib.error.URLError, OSError) as e:
        raise PV.ProfileError("AI_OFFLINE", tr("ai.unreachable", name=ep.name),
                              _redact(str(getattr(e, "reason", e)))[:300]) from None
    except ValueError:
        raise PV.ProfileError("AI_HTTP", tr("ai.replyNotJson", name=ep.name), tr("ai.checkUrlHint")) from None
    ms = int((time.time() - t0) * 1000)
    models = _norm_models(data, p.get("kind"))
    PV.health_put(pid, {"ok": True, "ms": ms, "why": "", "models": len(models)})
    return {"id": pid, "models": models[:3000], "n": len(models), "ms": ms, "unsupported": False}


TEST_SCHEMA = {"type": "object", "required": ["sum"], "properties": {"sum": {"type": ["integer", "number", "string"]}}}


def test_profile(pid, vision=False, quick=False):
    """`cli.py ai test <id>`: reachability (free) + one small JSON chat on the fast model (1 request) + optional
    vision check (1 request). Result is cached for the panel's status dots."""
    p = PV.get(pid)
    if not p:
        raise PV.ProfileError("AI_NO_PROFILE", tr("ai.noProfile", pid=pid))
    out = {"id": pid, "name": PV.display_name(p), "ok": False}
    why = creds_why(p)
    if why:
        out["why"] = _sentence(why)
        PV.health_put(pid, {"ok": False, "why": out["why"], "tested": True})
        return out
    forget(pid)
    h = _probe(p, timeout=8, max_age=0.0)
    out["reach"] = {"ok": h["ok"], "ms": h["ms"], "why": h["why"]}
    if not h["ok"] or quick:
        out.update(ok=h["ok"], why=h["why"])
        PV.health_put(pid, {"ok": h["ok"], "ms": h["ms"], "why": h["why"], "probe": True})
        return out
    m = resolve_model(p, "fast")
    msgs = [{"role": "system", "content": "You are a connection test for a video plugin. Answer in JSON only."},
            {"role": "user", "content": 'Compute 17 + 25. Reply exactly {"sum": <number>}.'}]
    trace, t0 = {}, time.time()
    try:
        obj = chat(msgs, model="fast", json_mode=True, schema=TEST_SCHEMA, profile=pid, retries=0, repair=1,
                   timeout=90, tag="ai_test", trace=trace)
        try:
            right = float(str(obj.get("sum")).strip()) == 42
        except ValueError:
            right = False
        out["chat"] = {"ok": True, "ms": int((time.time() - t0) * 1000), "model": m, "json": True, "right": right,
                       "format": trace.get("json_format", "prompt"), "repairs": trace.get("repairs", 0)}
    except AIError as e:
        out["chat"] = {"ok": False, "ms": int((time.time() - t0) * 1000), "model": m, "why": _redact(str(e))[:300]}
    if vision:
        vm = resolve_model(p, "vision", vision=True)
        if not vm:
            out["vision"] = {"ok": False, "why": tr("ai.noVisionSelected")}
        else:
            t1 = time.time()
            try:
                obj = chat_vision('What is the main colour of this image? Reply {"color": "<english word>"}',
                                  [_png()], schema={"type": "object", "required": ["color"]}, profile=pid,
                                  retries=0, repair=1, timeout=90, tag="ai_test_vision")
                col = str(obj.get("color", "")).lower()
                out["vision"] = {"ok": any(w in col for w in ("red", "merah", "crimson", "scarlet")),
                                 "ms": int((time.time() - t1) * 1000), "model": vm, "answer": col[:40]}
            except AIError as e:
                out["vision"] = {"ok": False, "ms": int((time.time() - t1) * 1000), "model": vm,
                                 "why": _redact(str(e))[:300]}
    out["ok"] = bool(out["chat"]["ok"])
    out["why"] = "" if out["ok"] else out["chat"].get("why", "")
    row = {"ok": out["ok"], "ms": out["chat"]["ms"], "why": out["why"], "tested": True, "model": m,
           "json": out["chat"].get("json", False), "format": out["chat"].get("format")}
    if out["ok"]:   # tells a long-running worker to close this profile's circuit breaker (see _is_open)
        row["test_ok_at"] = round(time.time(), 3)
    if "vision" in out:
        row["vision"] = out["vision"]["ok"]
    PV.health_put(pid, row)
    return out


# ---------------------------------------------------------------- JSON extraction

_THINK = re.compile(r"<think(?:ing)?>.*?</think(?:ing)?>", re.S | re.I)
_FENCE = re.compile(r"```[a-zA-Z]*\s*\n?(.*?)```", re.S)


def extract_json(text, expect=None):
    """Pull the first JSON object/array out of a model reply.
    Handles <think> blocks (closed or dangling), ``` fences, prose before/after, smart quotes, trailing commas,
    Python literals. expect=dict|list skips JSON values of the wrong type (e.g. a "[1]" citation in prose)."""
    if not isinstance(text, str):
        raise ValueError("reply is not text")
    t = _THINK.sub("", text)
    if re.search(r"</think(?:ing)?>", t, re.I):  # opening tag lost, keep what follows the close tag
        t = re.split(r"</think(?:ing)?>", t, flags=re.I)[-1]
    candidates = [m.group(1) for m in _FENCE.finditer(t)] + [t]
    if "```" in t:  # unterminated fence: keep what follows the opening fence
        candidates.append(re.split(r"```[a-zA-Z]*", t, maxsplit=1)[-1])
    for cand in candidates:
        if _unbalanced(cand):  # truncated outer value: inner objects would decode, but they are not the answer
            obj = _close_truncated(_repair(cand), expect)
            if obj is not None:
                return _intify(obj)
        for variant in (cand, _repair(cand)):
            obj = _scan(variant, expect)
            if obj is not None:
                return _intify(obj)
    # Unbalanced / truncated JSON. Seen live: grok-fast returned '{"titles":[...]' without the final '}'
    # (2 of 15 small calls). Close open brackets; if that fails, drop the trailing partial element and retry.
    for cand in candidates:
        obj = _close_truncated(_repair(cand), expect)
        if obj is not None:
            return _intify(obj)
    raise ValueError("no JSON value found" + (f" of type {expect.__name__}" if expect else ""))


def _unbalanced(t):
    """True when the first { or [ is never closed (reply cut off / missing final brace)."""
    start = min([i for i in (t.find("{"), t.find("[")) if i >= 0], default=-1)
    if start < 0:
        return False
    depth, in_str, esc = 0, False, False
    for ch in t[start:]:
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
        elif ch == '"':
            in_str = True
        elif ch in "{[":
            depth += 1
        elif ch in "}]":
            depth -= 1
            if depth == 0:
                return False
    return depth > 0


def _close_truncated(t, expect, max_backoff=50):
    start = min([i for i in (t.find("{"), t.find("[")) if i >= 0], default=-1)
    if start < 0:
        return None
    body = t[start:].rstrip().rstrip("`").rstrip()
    for _ in range(max_backoff):
        stack, in_str, esc = [], False, False
        for ch in body:
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
            elif ch == '"':
                in_str = True
            elif ch in "{[":
                stack.append("}" if ch == "{" else "]")
            elif ch in "}]" and stack:
                stack.pop()
        fixed = body + ('"' if in_str else "") + "".join(reversed(stack))
        try:
            obj = json.loads(re.sub(r",\s*([}\]])", r"\1", fixed))
            if expect is None or isinstance(obj, expect):
                return obj
        except json.JSONDecodeError:
            pass
        cut = max(body.rfind(","), body.rfind("{", 0, len(body) - 1), body.rfind("[", 0, len(body) - 1))
        if cut <= 0:
            return None
        body = body[:cut]  # drop the trailing partial element and try again
    return None


def _intify(o):
    """3.0 -> 3 so ids can index lists; leaves real floats alone."""
    if isinstance(o, float) and o.is_integer():
        return int(o)
    if isinstance(o, list):
        return [_intify(x) for x in o]
    if isinstance(o, dict):
        return {k: _intify(v) for k, v in o.items()}
    return o


def _scan(t, expect):
    dec = json.JSONDecoder()
    best = None
    for m in re.finditer(r"[{\[]", t):
        try:
            obj, end = dec.raw_decode(t, m.start())
        except json.JSONDecodeError:
            continue
        if expect is not None and not isinstance(obj, expect):
            continue
        if isinstance(obj, (dict, list)) and (best is None or end - m.start() > best[1]):
            best = (obj, end - m.start())
            if expect is not None or isinstance(obj, dict):
                break  # first object of the right type wins (outermost)
    return best[0] if best else None


def _repair(t):
    t = t.replace("\u201c", '"').replace("\u201d", '"').replace("\u2018", "'").replace("\u2019", "'")
    t = re.sub(r",\s*([}\]])", r"\1", t)  # trailing commas
    t = re.sub(r"(?<![\w\"])True(?![\w\"])", "true", t)
    t = re.sub(r"(?<![\w\"])False(?![\w\"])", "false", t)
    t = re.sub(r"(?<![\w\"])None(?![\w\"])", "null", t)
    t = re.sub(r"^\s*//.*$", "", t, flags=re.M)  # line comments
    return t


# ---------------------------------------------------------------- schema validation (JSON-schema subset)

_TYPES = {"object": dict, "array": list, "string": str, "boolean": bool, "null": type(None),
          "integer": int, "number": (int, float)}


def validate_schema(obj, schema, path="$"):
    """Subset of JSON Schema: type (str or list), properties, required, additionalProperties(bool), items,
    enum, minimum, maximum, minLength, maxLength, minItems, maxItems, pattern. Returns list of error strings."""
    errs = []
    types = schema.get("type")
    if types:
        tl = types if isinstance(types, list) else [types]
        ok = any(isinstance(obj, _TYPES[t]) and not (t in ("integer", "number") and isinstance(obj, bool))
                 for t in tl)
        if "integer" in tl and isinstance(obj, float) and obj.is_integer():
            ok = True
        if not ok:
            return [f"{path}: expected {'/'.join(tl)}, got {type(obj).__name__}"]
    if "enum" in schema and obj not in schema["enum"]:
        errs.append(f"{path}: {obj!r} not in {schema['enum']}")
    if isinstance(obj, (int, float)) and not isinstance(obj, bool):
        if "minimum" in schema and obj < schema["minimum"]:
            errs.append(f"{path}: {obj} < {schema['minimum']}")
        if "maximum" in schema and obj > schema["maximum"]:
            errs.append(f"{path}: {obj} > {schema['maximum']}")
    if isinstance(obj, str):
        if len(obj) < schema.get("minLength", 0):
            errs.append(f"{path}: string shorter than {schema['minLength']}")
        if "maxLength" in schema and len(obj) > schema["maxLength"]:
            errs.append(f"{path}: string longer than {schema['maxLength']}")
        if "pattern" in schema and not re.search(schema["pattern"], obj):
            errs.append(f"{path}: {obj!r} does not match {schema['pattern']}")
    if isinstance(obj, list):
        if len(obj) < schema.get("minItems", 0):
            errs.append(f"{path}: fewer than {schema['minItems']} items")
        if "maxItems" in schema and len(obj) > schema["maxItems"]:
            errs.append(f"{path}: more than {schema['maxItems']} items")
        if "items" in schema:
            for i, x in enumerate(obj):
                errs += validate_schema(x, schema["items"], f"{path}[{i}]")
                if len(errs) > 20:
                    break
    if isinstance(obj, dict):
        for k in schema.get("required", []):
            if k not in obj:
                errs.append(f"{path}: missing '{k}'")
        props = schema.get("properties", {})
        for k, v in obj.items():
            if k in props:
                errs += validate_schema(v, props[k], f"{path}.{k}")
            elif schema.get("additionalProperties") is False:
                errs.append(f"{path}: unexpected key '{k}'")
    return errs


# ---------------------------------------------------------------- chunking + parallel map

def chunk_by_chars(lines, max_chars=24000, overlap=0):
    """Split a list of text lines into consecutive chunks of <= max_chars (joined with newlines).
    overlap = number of lines repeated at the start of the next chunk (context for boundary topics)."""
    chunks, cur, size = [], [], 0
    for ln in lines:
        if cur and size + len(ln) + 1 > max_chars:
            chunks.append(cur)
            cur = cur[-overlap:] if overlap else []
            size = sum(len(x) + 1 for x in cur)
        cur.append(ln)
        size += len(ln) + 1
    if cur:
        chunks.append(cur)
    return chunks


def map_parallel(fn, items, workers=None):
    """Run fn over items with at most AI_MAX_PARALLEL requests in flight (the semaphore inside _send
    enforces it globally, this just provides the threads). Returns list of (result | Exception)."""
    workers = workers or config()["max_parallel"]

    def safe(x):
        try:
            return fn(x)
        except Exception as e:  # noqa: BLE001  - caller decides per item
            return e

    with ThreadPoolExecutor(max_workers=workers) as ex:
        return list(ex.map(safe, items))


# ---------------------------------------------------------------- result cache (protects quota)

class Cache:
    """JSON file cache keyed by sha256(task, prompt version, model, input). Re-clicking a button must not
    spend provider quota again. Default folder: %LOCALAPPDATA%/Klipora/ai_cache. Only cache source == "ai"."""

    def __init__(self, folder=None):
        if folder is None:
            from ..util import local_dir
            folder = local_dir("ai_cache")
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)

    def key(self, *parts):
        return hashlib.sha256(json.dumps(parts, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:24]

    def get(self, key):
        p = self.folder / f"{key}.json"
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def put(self, key, value):
        p = self.folder / f"{key}.json"
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, p)
