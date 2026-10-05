"""Sound effects per caption word ("Efek suara" in the Animasi tab).

Library: 12 sounds synthesized with ffmpeg lavfi `aevalsrc` (+ filters), normalized with numpy to about -14 dBFS
RMS over the audible part (~ -14 LUFS for these short hits) with peaks <= -1 dBFS, stored as 48 kHz mono 16-bit WAV
in engine/ac/assets/sfx/caption/ (generated once, `build_library`). User sounds (wav/mp3/m4a/ogg/flac, <= 8 s) are
converted + normalized the same way into %APPDATA%\\Klipora\\caption_sfx\\u_<name>.wav.

Plan (`sfx_plan`): rules over the captions document
  penting  emphasised words: template emphasis (numbers / keywords), per-word styles that stand out (colour,
           scale, bold, pill, underline, glow, font) and, with params.ai, the AI emphasis task (cached; rule
           fallback when the proxy is down)
  angka    words with a digit
  emoji    words that show an emoji
  halaman  every caption page start
each {on, sound, gain dB}; a hit is moved earlier by the sound's lead (whoosh / riser peak on the word).
Conflicts: priority angka > penting > emoji > halaman, `min_gap` seconds between hits, at most `max_per_min` hits in
any 60 s window. Render (`sfx_render`): one mixed mono WAV from the first hit to the last hit end, written in
10 s blocks (memory stays small on 35 min timelines), versioned file names (Premiere locks linked files).
"""
from __future__ import annotations

import math
import re
import wave
from pathlib import Path

import numpy as np

from ..i18n import tr

HERE = Path(__file__).resolve().parent
LIB_DIR = HERE.parent / "assets" / "sfx" / "caption"
SR = 48000
LIB_V = 1
TARGET_RMS_DB = -14.0
PEAK_DB = -1.0
TRACK = "Klipora SFX"          # Premiere track name (same in every UI language)
OLD_TRACKS = ("AutoCut SFX",)  # projects made before the rename

# id: (label locale key, duration s, aevalsrc expression, extra filters, lead s)
SOUNDS = {
    "pop": ("cap.sfx.sound.pop", 0.16, "sin(2*PI*(260*t+700*(1-exp(-40*t))/40))*exp(-30*t)*min(1,t*800)", "", 0.0),
    "whoosh": ("cap.sfx.sound.whoosh", 0.5, "(2*random(0)-1)*pow(sin(PI*t/0.5),3)", "highpass=f=350,lowpass=f=2400", 0.25),
    "swoosh": ("cap.sfx.sound.swoosh", 0.3, "(2*random(0)-1)*pow(sin(PI*pow(t/0.3,0.55)),2)", "highpass=f=1400,lowpass=f=7000",
               0.12),
    "ding": ("cap.sfx.sound.ding", 1.1, "(sin(2*PI*1318*t)+0.35*sin(2*PI*2636*t)+0.12*sin(2*PI*3954*t))*exp(-4.5*t)*min(1,t*400)",
             "", 0.0),
    "click": ("cap.sfx.sound.click", 0.04, "((2*random(0)-1)*0.6+sin(2*PI*2200*t))*exp(-250*t)", "", 0.0),
    "boom": ("cap.sfx.sound.boom", 1.3, "sin(2*PI*(38*t+50*(1-exp(-6*t))/6))*exp(-2.6*t)*min(1,t*300)"
                          "+0.4*(2*random(0)-1)*exp(-14*t)", "lowpass=f=260", 0.0),
    "riser": ("cap.sfx.sound.riser", 1.4, "(0.5*(2*random(0)-1)+0.6*sin(2*PI*(180*t+300*t*t)))*pow(t/1.4,2)",
              "highpass=f=150,afade=t=out:st=1.36:d=0.04", 1.3),
    "glitch": ("cap.sfx.sound.glitch", 0.32, "0.5*sgn(sin(2*PI*(300+900*mod(floor(t*28),3))*t))*lt(mod(t*40,1),0.6)"
                               "+0.3*(2*random(0)-1)*lt(mod(t*23,1),0.3)", "lowpass=f=6000", 0.0),
    "cash": ("cap.sfx.sound.cash", 0.9, "0.5*(2*random(0)-1)*exp(-120*t)+0.5*(sin(2*PI*2093*t)+0.5*sin(2*PI*4186*t))*exp(-7*t)"
                             "+0.5*gte(t,0.09)*(sin(2*PI*2637*t)+0.4*sin(2*PI*5274*t))*exp(-6*(t-0.09))", "", 0.0),
    "bubble": ("cap.sfx.sound.bubble", 0.16, "sin(2*PI*(250*t+2600*t*t))*exp(-22*t)*min(1,t*500)", "", 0.0),
    "typewriter": ("cap.sfx.sound.typewriter", 0.07, "(2*random(0)-1)*exp(-180*t)+0.5*sin(2*PI*3100*t)*exp(-90*t)"
                                        "+0.3*sin(2*PI*180*t)*exp(-60*t)", "", 0.0),
    "camera": ("cap.sfx.sound.camera", 0.3, "(2*random(0)-1)*(exp(-90*t)+gte(t,0.1)*exp(-70*(t-0.1)))*0.8", "highpass=f=800", 0.0),
}
RULES = {  # id: (label locale key, priority, default)
    "angka": ("cap.sfx.rule.angka", 4, {"on": True, "sound": "cash", "gain": -3.0}),
    "penting": ("cap.sfx.rule.penting", 3, {"on": True, "sound": "pop", "gain": 0.0}),
    "emoji": ("cap.sfx.rule.emoji", 2, {"on": False, "sound": "bubble", "gain": -2.0}),
    "halaman": ("cap.sfx.rule.halaman", 1, {"on": False, "sound": "swoosh", "gain": -8.0}),
}
DEFAULT_PARAMS = {"volume": -6.0, "max_per_min": 12, "min_gap": 0.6, "offset": -0.03, "ai": False}  # i18n-ignore
EMPH_KEYS = ("color", "active_color", "bold", "pill", "underline", "glow", "font")
AUDIO_EXT = (".wav", ".mp3", ".m4a", ".aac", ".ogg", ".flac", ".aif", ".aiff")


# ---------------------------------------------------------------- library

def _db(x):
    return 20 * math.log10(max(1e-9, x))


def normalize(x):
    """Gated RMS (10 ms blocks within 40 dB of the loudest) -> TARGET_RMS_DB, then peak <= PEAK_DB."""
    x = np.asarray(x, dtype=np.float32)
    if not x.size or not np.any(x):
        return x
    n = max(1, x.size // 480)
    blocks = x[: n * 480].reshape(n, -1) if x.size >= 480 else x.reshape(1, -1)
    rms = np.sqrt(np.mean(blocks.astype(np.float64) ** 2, axis=1))
    gate = rms >= rms.max() * 0.01
    level = float(np.sqrt(np.mean(rms[gate] ** 2)))
    y = x * (10 ** (TARGET_RMS_DB / 20) / max(level, 1e-9))
    peak = float(np.max(np.abs(y)))
    lim = 10 ** (PEAK_DB / 20)
    if peak > lim:
        y = y * (lim / peak)
    return y.astype(np.float32)


def write_wav(path, x, sr=SR):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    pcm = (np.clip(np.asarray(x, dtype=np.float32), -1, 1) * 32767).astype("<i2")
    tmp = path.with_suffix(".tmp")
    with wave.open(str(tmp), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())
    tmp.replace(path)
    return path


def read_wav(path):
    """Mono float32 at SR (any WAV we wrote; other files go through ffmpeg)."""
    path = Path(path)
    try:
        with wave.open(str(path), "rb") as w:
            if w.getframerate() == SR and w.getsampwidth() == 2:
                raw = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(np.float32) / 32768
                ch = w.getnchannels()
                return raw.reshape(-1, ch).mean(axis=1) if ch > 1 else raw
    except (wave.Error, OSError, EOFError):
        pass
    return decode(path)


def decode(path, max_sec=None):
    from ..util import run_ffmpeg
    args = ["-i", str(path)] + (["-t", f"{max_sec:.3f}"] if max_sec else []) + \
           ["-vn", "-ac", "1", "-ar", str(SR), "-f", "f32le", "-"]
    r = run_ffmpeg(args)
    return np.frombuffer(r.stdout, dtype="<f4").copy()


def synth(sid):
    """Raw float32 of a library sound from its lavfi recipe."""
    from ..util import run_ffmpeg
    label, dur, expr, filt, lead = SOUNDS[sid]
    src = f"aevalsrc='{expr}':s={SR}:d={dur}"
    args = ["-f", "lavfi", "-i", src] + (["-af", filt] if filt else []) + ["-ac", "1", "-f", "f32le", "-"]
    r = run_ffmpeg(args)
    return np.frombuffer(r.stdout, dtype="<f4").copy()


def build_library(force=False):
    """Generate (missing) library WAVs. -> {id: path}"""
    out = {}
    LIB_DIR.mkdir(parents=True, exist_ok=True)
    for sid in SOUNDS:
        p = LIB_DIR / f"{sid}.wav"
        if force or not p.is_file():
            write_wav(p, normalize(synth(sid)))
        out[sid] = p
    return out


def user_dir():
    from ..util import appdata_dir
    d = appdata_dir() / "caption_sfx"
    d.mkdir(parents=True, exist_ok=True)
    return d


def slug(name):
    s = re.sub(r"[^a-z0-9]+", "_", str(name or "").lower()).strip("_")
    return s[:40] or "suara"


def add_user(src, name=None):
    """Convert + normalize a user audio file (first 8 s) into the user library. -> row"""
    from ..util import EngineError
    src = Path(str(src or ""))
    if not src.is_file():
        raise EngineError("NO_MEDIA", tr("cap.sfx.errNoFile", path=str(src)), tr("cap.sfx.errNoFileHint"))
    if src.suffix.lower() not in AUDIO_EXT:
        raise EngineError("BAD_PARAMS", tr("cap.sfx.errFormat", ext=src.suffix), tr("cap.sfx.errFormatHint"))
    x = decode(src, 8.0)
    if not x.size or float(np.max(np.abs(x))) < 1e-4:
        raise EngineError("BAD_PARAMS", tr("cap.sfx.errSilent"), tr("cap.sfx.errSilentHint"))
    sid = "u_" + slug(name or src.stem)
    write_wav(user_dir() / f"{sid}.wav", normalize(x))
    return next(r for r in library() if r["id"] == sid)


def delete_user(sid):
    if not str(sid).startswith("u_"):
        return False
    p = user_dir() / f"u_{slug(sid[2:])}.wav"
    if p.is_file():
        p.unlink()
        return True
    return False


def library():
    """[{id, label, path, dur, lead, source builtin|user}]"""
    build_library()
    rows = [{"id": sid, "label": tr(v[0]), "path": str(LIB_DIR / f"{sid}.wav"), "dur": v[1], "lead": v[4],
             "source": "builtin"} for sid, v in SOUNDS.items()]
    try:
        for p in sorted(user_dir().glob("u_*.wav")):
            try:
                with wave.open(str(p), "rb") as w:
                    dur = w.getnframes() / float(w.getframerate())
            except (wave.Error, OSError, EOFError):
                continue
            rows.append({"id": p.stem, "label": p.stem[2:].replace("_", " ").capitalize(), "path": str(p),
                         "dur": round(dur, 3), "lead": 0.0, "source": "user"})
    except OSError:
        pass
    return rows


def rule_label(rid):
    """Label of a rule ("Kata penting" / "Important words") in the job language."""
    r = RULES.get(rid)
    return tr(r[0]) if r else str(rid)


def rules_catalog():
    """[{id, label, default}] for the panel (labels in the job language)."""
    return [{"id": k, "label": tr(v[0]), "default": v[2]} for k, v in RULES.items()]


def is_track(name):
    """Our SFX track, also under its pre-rename name."""
    return str(name) == TRACK or str(name) in OLD_TRACKS


def sound_map():
    return {r["id"]: r for r in library()}


# ---------------------------------------------------------------- plan

def rules_from(params):
    """Merged rule settings {id: {on, sound, gain}} + general params."""
    params = params or {}
    rules = {}
    given = params.get("rules") or {}
    for rid, (label, prio, dflt) in RULES.items():
        r = dict(dflt)
        if isinstance(given.get(rid), dict):
            r.update({k: v for k, v in given[rid].items() if k in ("on", "sound", "gain")})
        r["on"] = bool(r.get("on"))
        try:
            r["gain"] = max(-30.0, min(12.0, float(r.get("gain") or 0)))
        except (TypeError, ValueError):
            r["gain"] = 0.0
        rules[rid] = r
    gen = dict(DEFAULT_PARAMS)
    for k in DEFAULT_PARAMS:
        if params.get(k) is not None:
            gen[k] = params[k]
    gen["volume"] = max(-40.0, min(12.0, float(gen["volume"])))
    gen["max_per_min"] = max(1, min(120, int(gen["max_per_min"])))  # i18n-ignore
    gen["min_gap"] = max(0.0, min(10.0, float(gen["min_gap"])))
    gen["offset"] = max(-1.0, min(1.0, float(gen["offset"])))
    return rules, gen


def _ai_emphasis(doc, emit=None):
    """Word ids the AI emphasis task highlights (cached task). AI off / proxy down -> the task's rule fallback
    (longest content word per caption line, numbers and names first)."""
    from ..ai import tasks
    ws = [w for w in doc.get("words") or [] if not w.get("hide") and not w.get("merged")]
    if not ws:
        return set(), "none"
    rows = [{"text": " " + (w.get("text") or ""), "t0": w["t0"], "t1": w["t1"], "seg": w.get("seg", 0)} for w in ws]
    try:
        r = tasks.run("emphasis", rows, emit=emit)
    except Exception:  # noqa: BLE001 - AI is optional
        return set(), "fallback"
    if r.get("source") == "none":
        return set(), "none"
    starts = []
    from ..ai.text import display_words
    dw = display_words(rows)
    for ln in (r.get("result") or {}).get("lines") or []:
        for i in ln.get("hl") or []:
            if 0 <= i < len(dw):
                starts.append(dw[i]["start"])
    ids = set()
    for s in starts:
        best = min(ws, key=lambda w: abs(w["t0"] - s))
        if abs(best["t0"] - s) < 0.05:
            ids.add(best["id"])
    return ids, r.get("source") or "ai"


def candidates(doc, rules, ai_ids=()):
    """All rule hits before conflict resolution: [{t, rule, prio, word, text}]."""
    from . import layout as L
    from . import model as M
    pairs, W, H = M.page_pairs(doc)
    out = []
    for cap, tpl in pairs:
        ws = cap.words
        if rules["halaman"]["on"] and ws:
            out.append({"t": cap.start, "rule": "halaman", "word": ws[0].id, "text": ws[0].text})
        for w in ws:
            s = L.wstyle(w, tpl)
            text = w.text or ""
            if rules["angka"]["on"] and any(ch.isdigit() for ch in text):
                out.append({"t": w.start, "rule": "angka", "word": w.id, "text": text})
            if rules["penting"]["on"]:
                strong = any(s.get(k) for k in EMPH_KEYS if k != "color") or (s.get("color") and s.get("color") !=
                                                                              tpl["style"].get("fill"))
                strong = strong or (s.get("scale") or 1.0) > 1.05 or w.id in ai_ids
                if strong:
                    out.append({"t": w.start, "rule": "penting", "word": w.id, "text": text})
            if rules["emoji"]["on"] and s.get("emoji"):
                out.append({"t": w.start, "rule": "emoji", "word": w.id, "text": text})
    for c in out:
        c["prio"] = RULES[c["rule"]][1]
    return out


def plan(doc, params=None, emit=None):
    """-> {hits: [{t, sound, gain, rule, word, text}], stats, rules, params, ai}"""
    rules, gen = rules_from(params)
    sounds = sound_map()
    for r in rules.values():
        if r["sound"] not in sounds:
            r["sound"] = "pop"
    ai_ids, ai_src = (set(), None)
    if gen.get("ai") and rules["penting"]["on"]:
        ai_ids, ai_src = _ai_emphasis(doc, emit)
    cands = candidates(doc, rules, ai_ids)
    # one candidate per word (highest priority), then greedy by priority
    best = {}
    for c in cands:
        k = (c["word"], round(c["t"], 3))
        if k not in best or c["prio"] > best[k]["prio"]:
            best[k] = c
    order = sorted(best.values(), key=lambda c: (-c["prio"], c["t"]))
    taken, dropped = [], {"gap": 0, "rate": 0}
    for c in order:
        r = rules[c["rule"]]
        snd = sounds[r["sound"]]
        t = max(0.0, c["t"] + gen["offset"] - snd["lead"])
        if any(abs(t - h["t"]) < gen["min_gap"] for h in taken):
            dropped["gap"] += 1
            continue
        near = [h["t"] for h in taken if abs(h["t"] - t) < 60.0] + [t]
        if any(sum(1 for x in near if s0 <= x < s0 + 60.0) > gen["max_per_min"] for s0 in near if s0 <= t):  # i18n-ignore
            dropped["rate"] += 1
            continue
        taken.append({"t": round(t, 3), "sound": r["sound"], "gain": round(gen["volume"] + r["gain"], 2),
                      "rule": c["rule"], "word": c["word"], "text": c["text"], "at": round(c["t"], 3)})
    taken.sort(key=lambda h: h["t"])
    per = {rid: sum(1 for h in taken if h["rule"] == rid) for rid in RULES}
    return {"hits": taken, "rules": rules, "params": gen, "ai": ai_src,
            "stats": {"candidates": len(cands), "hits": len(taken), "dropped_gap": dropped["gap"],
                      "dropped_rate": dropped["rate"], "per_rule": per}}  # i18n-ignore


# ---------------------------------------------------------------- render

def mix(hits, out, block=10.0):
    """Mixed mono WAV from the first hit to the last hit end. -> {path, start, duration, hits}"""
    sounds = sound_map()
    clips = {}
    items = []
    for h in hits:
        sid = h.get("sound")
        if sid not in sounds:
            continue
        if sid not in clips:
            clips[sid] = read_wav(sounds[sid]["path"])
        x = clips[sid]
        if not x.size:
            continue
        items.append((float(h["t"]), x, 10 ** (float(h.get("gain", 0)) / 20)))
    if not items:
        return None
    items.sort(key=lambda it: it[0])
    start = max(0.0, items[0][0])
    end = max(t + x.size / SR for t, x, _ in items)
    n_total = int(math.ceil((end - start) * SR)) + 1
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp")
    bs = int(block * SR)
    peak = 0.0
    with wave.open(str(tmp), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        for b0 in range(0, n_total, bs):
            b1 = min(n_total, b0 + bs)
            buf = np.zeros(b1 - b0, dtype=np.float32)
            for t, x, g in items:
                s0 = int(round((t - start) * SR))
                s1 = s0 + x.size
                if s1 <= b0 or s0 >= b1:
                    continue
                a, b = max(s0, b0), min(s1, b1)
                buf[a - b0:b - b0] += x[a - s0:b - s0] * g
            peak = max(peak, float(np.max(np.abs(buf))) if buf.size else 0.0)
            np.clip(buf, -0.98, 0.98, out=buf)    # overlapping loud hits: soft safety (rare with min_gap)
            w.writeframes((buf * 32767).astype("<i2").tobytes())
    tmp.replace(out)
    return {"path": str(out), "start": round(start, 4), "duration": round(n_total / SR, 4), "hits": len(items),
            "peak_db": round(_db(peak), 2)}


def hit_clips(hits, folder):
    """Per-hit mode: one WAV per sound + gain (placed by the host at every hit time). -> [{t, path}]"""
    sounds = sound_map()
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    out, made = [], {}
    for h in hits:
        sid = h.get("sound")
        if sid not in sounds:
            continue
        g = round(float(h.get("gain", 0)), 1)
        key = f"{sid}_{('m' if g < 0 else 'p')}{abs(g):g}".replace(".", "_")
        if key not in made:
            p = folder / f"sfx_{key}.wav"
            write_wav(p, read_wav(sounds[sid]["path"]) * (10 ** (g / 20)))
            made[key] = str(p)
        out.append({"t": h["t"], "path": made[key], "sound": sid})
    return out


def versioned(folder, stem):
    from .render import next_version
    return next_version(Path(folder), stem, ".wav")


def cleanup(folder, stem, keep=2, protect=()):
    from .render import cleanup_versions
    return cleanup_versions(Path(folder), stem, keep=keep, protect=protect)
