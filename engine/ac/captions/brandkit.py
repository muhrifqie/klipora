"""Brand Kit: named brand presets (colours, fonts, brand words, emoji policy, tone) stored in
%APPDATA%\\Klipora\\brandkit.json (several kits, one active), and the two caption actions built on it
(docs/CAPTIONS_API.md 12.2 / 12.3):

  brandkit        op get | set | delete | activate | apply
                  apply = ops that restyle the current captions doc with the kit (palette, fonts, highlight colours,
                  brand words as emphasis keywords + glossary, emoji policy). The panel sends them through its edit
                  queue (one undo step); `commit: true` applies them here instead.
  brand_emphasis  per-word emphasis suggestions (colour / scale / emoji) from the AI "emphasis" task + brand words,
                  rule fallback (numbers, prices, brand words, long content words). Reviewable: items + ops.

The logo / watermark path is stored only (not rendered in captions).
"""
from __future__ import annotations

import re
import time

from ..i18n import tr

KIT_V = 1
FILE = "brandkit.json"
EMOJI_LEVELS = ("none", "sedikit", "banyak")
EMOJI_LABELS = {k: "cap.brand.emoji." + k for k in EMOJI_LEVELS}           # locale keys
TONES = {k: "cap.brand.tone." + k for k in ("santai", "profesional", "semangat", "lucu", "edukatif")}
TONE_SCALE = {"profesional": 1.0, "edukatif": 1.06, "santai": 1.08, "lucu": 1.12, "semangat": 1.15}
COLOR_KEYS = ("primary", "accent", "text", "background")
DEFAULT_COLORS = {"primary": "#FF6A00", "accent": "#FFD400", "text": "#FFFFFF", "background": "#111111"}
DEFAULT_FONTS = {"heading": "Montserrat Black", "body": "Plus Jakarta Sans Bold"}
MAX_KITS = 40
MAX_WORDS = 200

# Indonesian words that are never worth a highlight (fallback rules) (+ the "i18n-ignore" marker token)
STOP = set("""yang di ke dari dan atau untuk dengan pada dalam oleh ini itu ada akan juga sudah belum bisa i18n-ignore
harus kalau kalo karena agar supaya jadi jika maka saat ketika lalu terus kemudian nah ya yah sih dong deh kok kan
aja saja lagi masih sangat banget sekali lebih paling cuma hanya semua setiap para sang si nya kita kami kamu saya
aku anda dia mereka gue gua lu lo guys teman temen oke ok baik nanti tadi sini situ sana begitu gitu begini gini
apa siapa mana kapan bagaimana gimana kenapa mengapa berapa tidak nggak gak enggak bukan jangan mau ingin punya
tapi tetapi namun serta bahwa sebagai seperti misalnya contoh udah pas tuh nih biar sampai hingga""".split())
_NUM = re.compile(r"\d")
_RP = re.compile(r"^(rp|idr)\b", re.I)


# ====================================================================== storage

def path():
    from ..util import appdata_dir
    return appdata_dir() / FILE


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def load():
    """{v, active, kits: [kit]} (missing / broken file -> empty store)."""
    from ..util import read_json
    data = read_json(path(), None)
    if not isinstance(data, dict) or not isinstance(data.get("kits"), list):
        return {"v": KIT_V, "active": None, "kits": []}
    kits = [k for k in data["kits"] if isinstance(k, dict) and k.get("id")]
    act = data.get("active") if any(k["id"] == data.get("active") for k in kits) else (kits[0]["id"] if kits else None)
    return {"v": KIT_V, "active": act, "kits": kits}


def save(data):
    from ..util import write_json
    write_json(path(), {"v": KIT_V, "active": data.get("active"), "kits": data.get("kits") or []}, indent=2)


def slug(name):
    s = re.sub(r"[^a-z0-9]+", "_", str(name or "").lower()).strip("_")
    return s[:40] or "brand"


def _hex(v, default):
    from .stylist import norm_hex
    return norm_hex(v) or default


def _words(src):
    if isinstance(src, str):
        src = re.split(r"[,;\n]", src)
    out = []
    for x in src or []:
        if isinstance(x, str):
            x = re.sub(r"\s+", " ", x).strip()[:60]
            if x and x.lower() not in (o.lower() for o in out):
                out.append(x)
    return out[:MAX_WORDS]


def clean(kit, old=None):
    """Validated kit dict (unknown keys dropped, colours #RRGGBB, emoji / tone checked) -> (kit, warnings)."""
    from ..util import EngineError
    old = old or {}
    k = dict(old)
    k.update({kk: v for kk, v in (kit or {}).items() if v is not None})
    warns = []
    name = re.sub(r"\s+", " ", str(k.get("name") or "").strip())[:60]
    if not name:
        raise EngineError("BAD_KIT", tr("cap.brand.errNoName"), tr("cap.brand.errNoNameHint"))
    cols = dict(DEFAULT_COLORS)
    cols.update(old.get("colors") or {})
    for ck, cv in ((kit or {}).get("colors") or {}).items():
        if ck in COLOR_KEYS:
            nv = _hex(cv, None)
            if nv is None:
                warns.append(tr("cap.brand.warnColor", which=ck, value=cv))
            else:
                cols[ck] = nv
    fonts = dict(DEFAULT_FONTS)
    fonts.update(old.get("fonts") or {})
    for fk, fv in ((kit or {}).get("fonts") or {}).items():
        if fk in ("heading", "body") and isinstance(fv, str) and fv.strip():
            fonts[fk] = fv.strip()[:80]
    try:
        from .layout import font_catalog
        fams = {r["family"] for r in font_catalog(system=True)["fonts"]}
        for fk, fv in fonts.items():
            if fv not in fams:
                warns.append(tr("cap.brand.warnFont", font=fv))
    except Exception:  # noqa: BLE001 - a font scan problem must not block saving
        pass
    emoji = str(k.get("emoji") or "sedikit").lower()
    if emoji not in EMOJI_LEVELS:
        emoji = "sedikit"
    tone = str(k.get("tone") or "santai").strip().lower()[:40] or "santai"
    logo = str(k.get("logo") or "").strip()[:400]
    out = {"id": k.get("id") or slug(name), "name": name, "colors": {c: cols[c] for c in COLOR_KEYS},
           "fonts": {"heading": fonts["heading"], "body": fonts["body"]}, "logo": logo,
           "words": _words(k.get("words")), "emoji": emoji, "tone": tone,
           "created": old.get("created") or _now(), "updated": _now()}
    return out, warns


def get(kit_id=None, data=None):
    """Kit by id, or the active one; None when there is none."""
    data = data or load()
    want = kit_id or data.get("active")
    return next((k for k in data["kits"] if k["id"] == want), None)


# ====================================================================== style from a kit

def _lum(hx):
    from .stylist import luminance, rgb_of
    return luminance(rgb_of(hx))


def _contrast_text(bg):
    """Readable text colour on a fill (white or near black)."""
    return "#111111" if _lum(bg) > 0.45 else "#FFFFFF"


def brand_tokens(kit):
    """Single words of the brand words (emphasis keywords match one word at a time)."""
    out = []
    for w in kit.get("words") or []:
        for t in re.findall(r"[0-9A-Za-zÀ-ɏ][0-9A-Za-zÀ-ɏ'-]*", w):
            t = t.lower()
            if len(t) >= 3 and t not in STOP and t not in out:
                out.append(t)
    return out


def style_patch(kit, eff):
    """doc.style patch that recolours the effective template `eff` with the kit (structure kept)."""
    from .layout import font_catalog
    c = kit["colors"]
    st = {"style": {"fill": c["text"]}, "highlight": {}, "emphasis": {}}
    try:
        fams = {r["family"] for r in font_catalog(system=True)["fonts"]}
    except Exception:  # noqa: BLE001
        fams = set()
    head = (kit.get("fonts") or {}).get("heading")
    if head and (not fams or head in fams):
        st["font"] = {"family": head}
    hl = eff.get("highlight") or {}
    if hl.get("pill"):
        st["highlight"]["pill"] = c["primary"]
        st["highlight"]["color"] = _contrast_text(c["primary"])
    elif hl.get("underline"):
        st["highlight"]["underline"] = c["accent"]
        if hl.get("color"):
            st["highlight"]["color"] = c["accent"]
    elif hl.get("color") is not None or (eff.get("timing") or {}).get("mode") == "karaoke":
        st["highlight"]["color"] = c["accent"]
    if hl.get("glow"):
        st["highlight"]["glow"] = dict(hl["glow"], color=c["accent"] + "CC")
    box = eff.get("box") or {}
    if box.get("enabled"):
        st["box"] = {"color": c["background"]}
        if box.get("gradient"):
            st["box"]["gradient"] = None
        if _lum(c["background"]) > 0.5 and _lum(c["text"]) > 0.5:
            st["style"]["fill"] = _contrast_text(c["background"])      # light box: keep the text readable
    sty = eff.get("style") or {}
    if sty.get("glow"):
        st["style"]["glow"] = dict(sty["glow"], color=c["primary"] + "CC")
    if sty.get("gradient"):
        st["style"]["gradient"] = dict(sty["gradient"], colors=[c["primary"], c["accent"]])
    if sty.get("color_cycle"):
        st["style"]["color_cycle"] = [c["text"], c["accent"]]
    if sty.get("extrude"):
        st["style"]["extrude"] = dict(sty["extrude"], color=c["primary"])
    toks = brand_tokens(kit)
    em = eff.get("emphasis") or {}
    if toks:
        st["emphasis"] = {"keywords": list(dict.fromkeys([*(em.get("keywords") or []), *toks]))[:120],
                          "color": c["primary"] if c["primary"].upper() != st["style"]["fill"].upper() else c["accent"],
                          "scale": max(float(em.get("scale") or 1.0), TONE_SCALE.get(kit.get("tone"), 1.08))}
    elif em.get("color"):
        st["emphasis"] = {"color": c["accent"]}
    return {k: v for k, v in st.items() if v}


def effective(doc):
    """Template + doc.style of a captions doc (the look the user sees)."""
    from . import templates as TP
    from .layout import deep_merge
    seq = doc.get("seq") or {}
    W, H = int(seq.get("w") or 1920), int(seq.get("h") or 1080)
    return deep_merge(TP.load(doc.get("template"), W, H), doc.get("style") or {})


def apply_ops(kit, doc):
    """Edit ops (CAPTIONS_API section 5) that put the kit on the doc -> (ops, info)."""
    from . import model as M
    eff = effective(doc)
    patch = style_patch(kit, eff)
    new_style = M.merge_patch(doc.get("style") or {}, patch)
    # explicit nulls of the patch must survive (doc_style replace keeps them)
    for sec, vals in patch.items():
        for k, v in vals.items():
            if v is None:
                new_style.setdefault(sec, {})[k] = None
    ops = [{"op": "doc_style", "style": new_style, "replace": True}]
    gl_old = list((doc.get("params") or {}).get("glossary") or [])
    low = {g.lower() for g in gl_old}
    added = [w for w in kit.get("words") or [] if w.lower() not in low]
    if added:
        ops.append({"op": "params", "glossary": gl_old + added})
    emo = kit.get("emoji")
    if emo == "none":
        ops.append({"op": "clear_emoji"})
    elif emo in ("sedikit", "banyak"):
        ops.append({"op": "auto_emoji", "density": emo})
    return ops, {"glossary_added": added, "patch": patch, "rebuild": bool(added)}


# ====================================================================== emphasis suggestions

def _core(t):
    return re.sub(r"[^\w]", "", str(t or "").lower())


def _is_number(t):
    return bool(_NUM.search(t or "")) or bool(_RP.match(t or ""))


def emphasis_items(doc, kit=None, use_ai=True, max_per_page=1, emit=None):
    """Reviewable per-word emphasis suggestions -> (items, source, warnings, stats)."""
    from . import model as M
    kit = kit or {}
    M.resolve_words(doc)
    vis = [w for w in doc["words"] if not w.get("hide") and not w.get("merged") and (w.get("text") or "").strip()]
    if not vis:
        return [], "none", [tr("cap.brand.noWords")], {}
    idx = {w["id"]: i for i, w in enumerate(vis)}
    toks = set(brand_tokens(kit))
    brand_full = [w.lower() for w in kit.get("words") or []]
    picks = {}          # word index -> {why, emoji}
    warnings, source = [], "fallback"
    ai_lines = []
    if use_ai:
        from ..ai import tasks
        rows = [{"text": " " + (w.get("text") or "").strip(), "t0": w["t0"], "t1": w["t1"], "seg": w.get("seg", 0)}
                for w in vis]
        r = tasks.run("emphasis", rows, glossary=kit.get("words") or [], emit=emit)
        source = r.get("source") or "fallback"
        warnings += [w for w in r.get("warnings") or [] if w][:3]
        if source in ("ai", "cache", "mixed"):
            ai_lines = (r.get("result") or {}).get("lines") or []
            for ln in ai_lines:
                for i in ln.get("hl") or []:
                    if 0 <= i < len(vis) and not ln.get("filled_by_fallback"):
                        picks.setdefault(i, {"why": "ai"})
                if ln.get("emoji") and ln.get("hl"):
                    i0 = ln["hl"][0]
                    if 0 <= i0 < len(vis):
                        picks.setdefault(i0, {"why": "ai"})["emoji"] = ln["emoji"]
    # brand words and numbers always qualify; per page keep the strongest ones
    for i, w in enumerate(vis):
        c = _core(w.get("text"))
        if c and (c in toks or any(c == _core(b) for b in brand_full)):
            picks[i] = dict(picks.get(i, {}), why="brand")
        elif _is_number(w.get("text")) and i not in picks:
            picks[i] = {"why": "angka"}
    rank = {"brand": 0, "angka": 1, "ai": 2, "kata": 3}
    items, n = [], 0
    for page in doc.get("pages") or []:
        ids = [wid for ln in page["lines"] for wid in ln if wid in idx]
        cand = [idx[wid] for wid in ids if idx[wid] in picks]
        if not cand and source not in ("ai", "cache", "mixed"):
            # rule fallback: the longest content word of the page
            best = None
            for wid in ids:
                t = vis[idx[wid]].get("text") or ""
                c = _core(t)
                parts = [x for x in re.split(r"[^\w]+", t.lower()) if x]
                if len(c) < 5 or c in STOP or any(x in STOP for x in parts):
                    continue
                if best is None or len(c) > best[0]:
                    best = (len(c), idx[wid])
            if best:
                picks[best[1]] = {"why": "kata"}
                cand = [best[1]]
        cand.sort(key=lambda i: (rank.get(picks[i]["why"], 9), -len(_core(vis[i].get("text")))))
        lim = max(1, int(max_per_page)) + sum(1 for i in cand if picks[i]["why"] == "brand")
        for i in cand[:lim]:
            w = vis[i]
            if (w.get("style") or {}).get("color") or (w.get("style") or {}).get("pill"):
                continue            # the user coloured it already
            n += 1
            items.append({"id": f"e{n}", "ids": [w["id"]], "text": (w.get("text") or "").strip(), "t0": w["t0"],
                          "t1": w["t1"], "why": picks[i]["why"], "emoji": picks[i].get("emoji"), "on": True,
                          "page": page["id"]})
    st = {"words": len(vis), "items": len(items), "brand": sum(1 for x in items if x["why"] == "brand"),
          "ai": sum(1 for x in items if x["why"] == "ai"), "angka": sum(1 for x in items if x["why"] == "angka"),
          "kata": sum(1 for x in items if x["why"] == "kata")}
    return items, source, warnings, st


WHY_LABELS = {k: "cap.brand.why." + k for k in ("brand", "ai", "angka", "kata")}     # locale keys


def emphasis_style(kit, eff, why, emoji=None, emoji_level="sedikit"):
    """Word style for one suggestion: brand words in the primary colour, the rest in the accent."""
    c = (kit or {}).get("colors") or {}
    fill = ((eff.get("style") or {}).get("fill") or "#FFFFFF")[:7].upper()
    act = ((eff.get("highlight") or {}).get("color") or "")[:7].upper()
    col = c.get("primary") if why == "brand" else c.get("accent")
    if not col:
        col = "#FFD400" if why != "brand" else "#FF6A00"
    if col.upper() in (fill, act):
        col = c.get("accent") if col == c.get("primary") else c.get("primary") or "#FF6A00"
    st = {"color": col, "scale": TONE_SCALE.get((kit or {}).get("tone"), 1.1) if why != "angka" else 1.12}
    if st["scale"] == 1.0:
        st.pop("scale")
    if emoji and emoji_level != "none":
        st["emoji"] = emoji
    return st


def emphasis_ops(items, kit, eff, emoji_level="sedikit"):
    """op `style` list for the `on` items (emoji thinned by the kit's emoji policy)."""
    ops, last_emoji = [], -1e9
    gap = {"sedikit": 15.0, "banyak": 0.0}.get(emoji_level)
    for it in items:
        if not it.get("on"):
            continue
        emo = it.get("emoji") if gap is not None and it["t0"] - last_emoji >= gap else None
        if emo:
            last_emoji = it["t0"]
        ops.append({"op": "style", "ids": list(it["ids"]),
                    "style": emphasis_style(kit, eff, it["why"], emo, emoji_level)})
    return ops


# ====================================================================== actions

def action(job, emit):
    """`brandkit` action: params.op get | set | delete | activate | apply."""
    from ..util import EngineError
    p = job["params"]
    op = p.get("op") or "get"
    data = load()
    if op == "get":
        return {"kits": data["kits"], "active": data["active"], "file": str(path()),
                "tones": [{"id": k, "label": tr(v)} for k, v in TONES.items()],
                "emoji_levels": [{"id": k, "label": tr(v)} for k, v in EMOJI_LABELS.items()],
                "defaults": {"colors": DEFAULT_COLORS, "fonts": DEFAULT_FONTS},
                "summary": tr("cap.brand.sumKits", n=len(data["kits"]))}
    if op == "set":
        src = p.get("kit") or {}
        old = next((k for k in data["kits"] if k["id"] == src.get("id")), None) if src.get("id") else None
        kit, warns = clean(src, old)
        if old is None:
            base, n = kit["id"], 2
            while any(k["id"] == kit["id"] for k in data["kits"]):
                kit["id"] = f"{base}_{n}"
                n += 1
            if len(data["kits"]) >= MAX_KITS:
                raise EngineError("BAD_KIT", tr("cap.brand.errMax", n=MAX_KITS), tr("cap.brand.errMaxHint"))
            data["kits"].append(kit)
        else:
            data["kits"] = [kit if k["id"] == old["id"] else k for k in data["kits"]]
        if p.get("activate") or not data.get("active"):
            data["active"] = kit["id"]
        save(data)
        return {"kit": kit, "kits": data["kits"], "active": data["active"], "warnings": warns,
                "summary": tr("cap.brand.sumSaved", name=kit["name"])}
    if op in ("delete", "activate"):
        kid = p.get("id")
        if not any(k["id"] == kid for k in data["kits"]):
            raise EngineError("BAD_KIT", tr("cap.brand.errNotFound", id=kid))
        if op == "delete":
            data["kits"] = [k for k in data["kits"] if k["id"] != kid]
            if data.get("active") == kid:
                data["active"] = data["kits"][0]["id"] if data["kits"] else None
        else:
            data["active"] = kid
        save(data)
        return {"kits": data["kits"], "active": data["active"],
                "summary": tr("cap.brand.sumDeleted") if op == "delete" else tr("cap.brand.sumActivated")}
    if op == "apply":
        from . import model as M
        kit = p.get("kit") if isinstance(p.get("kit"), dict) else get(p.get("id"), data)
        if not kit:
            raise EngineError("NO_KIT", tr("cap.brand.errNoKit"), tr("cap.brand.errNoKitHint"))
        if p.get("kit"):
            kit, _ = clean(kit)
        d, dpath = M.from_job(job)
        ops, info = apply_ops(kit, d)
        out = {"kit": {"id": kit["id"], "name": kit["name"]}, "ops": ops, "glossary_added": info["glossary_added"],
               "rebuild": info["rebuild"], "patch": info["patch"],
               "summary": (tr("cap.brand.sumAppliedGloss", name=kit["name"], n=len(info["glossary_added"]))
                           if info["glossary_added"] else tr("cap.brand.sumApplied", name=kit["name"]))}
        if p.get("commit"):
            out["notes"] = M.apply_ops(d, ops)
            M.repaginate(d)
            M.save(d, dpath)
            out["doc"] = str(dpath)
        return out
    raise EngineError("BAD_PARAMS", tr("cap.brand.errOp", op=op))


def emphasis_action(job, emit):
    """`brand_emphasis` action: suggestions (items + ops); `commit: true` applies every suggestion."""
    from . import model as M
    p = job["params"]
    d, dpath = M.from_job(job)
    kit = get(p.get("id")) if p.get("kit") is None else clean(p["kit"])[0]
    if p.get("brand", True) is False:
        kit = None
    emit.plan([("ai", tr("cap.brand.stageAi"), 0.9), ("ops", tr("cap.brand.stageOps"), 0.1)])
    with emit.step("ai"):
        items, source, warns, st = emphasis_items(d, kit, use_ai=p.get("ai", True) is not False,
                                                  max_per_page=int(p.get("max_per_page") or 1), emit=emit)  # i18n-ignore
    with emit.step("ops"):
        eff = effective(d)
        level = (kit or {}).get("emoji") or p.get("emoji") or "sedikit"
        for it in items:
            it["style"] = emphasis_style(kit or {}, eff, it["why"], it.get("emoji"), level)
            it["label"] = tr(WHY_LABELS[it["why"]]) if it["why"] in WHY_LABELS else it["why"]
        ops = emphasis_ops(items, kit or {}, eff, level)
    out = {"items": items, "ops": ops, "source": source, "warnings": warns, "stats": st,
           "kit": {"id": kit["id"], "name": kit["name"]} if kit else None,
           "summary": tr("cap.brand.sumEmph" if source in ("ai", "cache", "mixed") else "cap.brand.sumEmphRules",
                         n=len(items))}
    if p.get("commit"):
        out["notes"] = M.apply_ops(d, ops)
        M.repaginate(d)
        M.save(d, dpath)
        out["doc"] = str(dpath)
    return out
