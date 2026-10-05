"""Transcript text helpers for prompts and rule-based features (port of proto/ai/transcript.py).

Input words are either cache v3 rows [text, start, end, seg_end, prob] (ac.transcript.words) or timeline word
dicts {"text","t0","t1","seg",...} (Timeline.words_on_timeline). v3 rows are already one word per row, so the
display-word index `i` equals the row index; legacy v2 continuation tokens (" teman" + "-teman,") are still
merged when present.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

END_PUNCT = ".?!"


def load_words(path):
    """Words list from any *_words.json (v2 or v3)."""
    return json.loads(Path(path).read_text(encoding="utf-8"))["words"]


def _row(w):
    if isinstance(w, dict):
        return w.get("text", ""), float(w.get("t0", w.get("start", 0.0))), float(w.get("t1", w.get("end", 0.0))), \
            int(w.get("seg", w.get("seg_end", 0)) or 0)
    return str(w[0]), float(w[1]), float(w[2]), int(w[3]) if len(w) > 3 else 0


def display_words(words):
    """-> [{i, text, start, end, seg_end, toks:[source idx]}]; i is the index every prompt/result uses."""
    out = []
    for k, w in enumerate(words):
        text, s, e, seg = _row(w)
        if out and not text.startswith(" ") and text.strip() and not out[-1]["seg_end"]:
            d = out[-1]
            d["text"] += text
            d["end"] = e
            d["seg_end"] = seg
            d["toks"].append(k)
        else:
            out.append({"i": len(out), "text": text.strip(), "start": s, "end": e, "seg_end": seg, "toks": [k]})
    return out


def sentences(dwords, max_gap=1.0, max_dur=20.0, seg_words=6):
    """Utterance units: break on terminal punctuation, a pause >= max_gap, a Whisper segment end once the unit
    has >= seg_words words (or past max_dur), hard cap 2*max_dur. Screen tutorials have little punctuation,
    so pauses + Whisper segments do the work. -> [{id, start, end, text, w0, w1}] (w1 inclusive)."""
    sents, cur = [], []

    def flush():
        if cur:
            sents.append({"id": len(sents), "start": cur[0]["start"], "end": cur[-1]["end"],
                          "text": " ".join(w["text"] for w in cur), "w0": cur[0]["i"], "w1": cur[-1]["i"]})
            cur.clear()

    for w in dwords:
        if cur:
            prev = cur[-1]
            if (prev["text"][-1:] in END_PUNCT or w["start"] - prev["end"] >= max_gap
                    or (prev["seg_end"] and (len(cur) >= seg_words or w["end"] - cur[0]["start"] > max_dur))
                    or w["end"] - cur[0]["start"] > max_dur * 2):
                flush()
        cur.append(w)
    flush()
    return sents


def caption_lines(dwords, max_chars=32, max_gap=0.6, max_dur=3.5):
    """Mirror of the legacy caption chunker on display words, keeping indices. -> [[display idx, ...]]"""
    lines, cur = [], []
    for w in dwords:
        if cur:
            text = " ".join(dwords[i]["text"] for i in cur + [w["i"]])
            last = dwords[cur[-1]]
            if (len(text) > max_chars or w["start"] - last["end"] > max_gap
                    or w["end"] - dwords[cur[0]]["start"] > max_dur or last["seg_end"]
                    or last["text"][-1:] in END_PUNCT):
                lines.append(cur)
                cur = []
        cur.append(w["i"])
    if cur:
        lines.append(cur)
    return lines


def ts(sec, frac=False):
    """Seconds -> 'mm:ss' ('h:mm:ss' past an hour); frac=True -> 'mm:ss.s'."""
    sec = max(0.0, sec)
    h, m, s = int(sec // 3600), int(sec // 60 % 60), sec % 60
    s_txt = f"{s:04.1f}" if frac else f"{int(s):02d}"
    return f"{h}:{m:02d}:{s_txt}" if h else f"{m:02d}:{s_txt}"


def parse_ts(text):
    """'1:02:03', '02:03', '123', '02:03.5' -> seconds (float). ValueError on junk."""
    parts = str(text).strip().split(":")
    if not 1 <= len(parts) <= 3:
        raise ValueError(text)
    sec = 0.0
    for p in parts:
        sec = sec * 60 + float(p)
    return sec


def render_sentences(sents, with_end=False, with_dur=False):
    out = []
    for s in sents:
        head = f"[{s['id']}] {ts(s['start'])}"
        if with_end:
            head += f"-{ts(s['end'])}"
        if with_dur:
            head += f" ({s['end'] - s['start']:.0f}s)"
        out.append(f"{head} {s['text']}")
    return out


def norm(t):
    return re.sub(r"[^\w]+", "", t.lower())
