"""Native caption export: SRT / WebVTT from the captions document (sequence time).

Premiere: `gxCaptionTrackFromSrt(seq, srtPath)` = createCaptionTrack (no styling API; SRT <font color>/<b>/<i>
tags are parsed into styled runs, on-screen rendering of those runs UNVERIFIED). Text is the document's display
text (user edits, glossary fixes, censor) without template case transforms unless upper=True.
"""
from __future__ import annotations

from pathlib import Path

from . import glossary as G
from . import layout as L
from . import model as M


def ts(sec, sep=","):
    ms = int(round(max(0.0, sec) * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{ms:03d}"


def _word_text(w, doc, tags, tpl):
    mode = M.censor_mode(doc)
    t = (w.get("text") or "").strip()
    if w.get("censor"):
        t = G.mask(t, mode if mode != "off" else "stars")
    t, _ = L.split_emoji(t)
    if not tags:
        return t
    st = w.get("style") or {}
    rw = L.RWord(text=t, start=0, end=0, style=st, raw=t)
    ws = L.wstyle(rw, tpl)
    if ws.get("color"):
        t = f'<font color="{ws["color"][:7]}">{t}</font>'
    if st.get("bold"):
        t = f"<b>{t}</b>"
    if st.get("italic"):
        t = f"<i>{t}</i>"
    return t


def cues(doc, mode="page", tags=False, upper=False):
    """[(t0, t1, text)] with mode page (one cue per page, lines kept) | line (one cue per line) | word."""
    M.resolve_words(doc)
    tpl = M.base_template(doc)
    by = {w["id"]: w for w in doc["words"]}
    out = []
    for p in doc.get("pages") or []:
        lines = [[by[i] for i in ln if i in by] for ln in p["lines"]]
        lines = [ln for ln in lines if ln]
        if not lines:
            continue
        fmt = lambda ws: " ".join(_word_text(w, doc, tags, tpl) for w in ws)  # noqa: E731
        if mode == "word":
            flat = [w for ln in lines for w in ln]
            for k, w in enumerate(flat):
                t1 = flat[k + 1]["t0"] if k + 1 < len(flat) else p["t1"]
                out.append((w["t0"], max(t1, w["t0"] + 0.05), _word_text(w, doc, tags, tpl)))
        elif mode == "line":
            for k, ln in enumerate(lines):
                t0 = p["t0"] if k == 0 else ln[0]["t0"]
                t1 = lines[k + 1][0]["t0"] if k + 1 < len(lines) else p["t1"]
                out.append((t0, max(t1, t0 + 0.05), fmt(ln)))
        else:
            out.append((p["t0"], p["t1"], "\n".join(fmt(ln) for ln in lines)))
    if upper:
        out = [(a, b, t.upper()) for a, b, t in out]
    # never overlap (players show both)
    fixed = []
    for k, (a, b, t) in enumerate(out):
        nxt = out[k + 1][0] if k + 1 < len(out) else None
        if nxt is not None and b > nxt:
            b = max(a + 0.02, nxt)
        fixed.append((round(a, 3), round(b, 3), t))
    return fixed


def srt_text(doc, mode="page", tags=False, upper=False):
    rows = cues(doc, mode, tags, upper)
    return "".join(f"{i}\n{ts(a)} --> {ts(b)}\n{t}\n\n" for i, (a, b, t) in enumerate(rows, 1)), len(rows)


def vtt_text(doc, mode="page", upper=False):
    rows = cues(doc, mode, False, upper)
    return "WEBVTT\n\n" + "".join(f"{ts(a, '.')} --> {ts(b, '.')}\n{t}\n\n" for a, b, t in rows), len(rows)


def write(doc, path, fmt="srt", mode="page", tags=False, upper=False):
    text, n = vtt_text(doc, mode, upper) if fmt == "vtt" else srt_text(doc, mode, tags, upper)
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")       # Premiere reads UTF-8 SRT (no BOM needed)
    return p, n
