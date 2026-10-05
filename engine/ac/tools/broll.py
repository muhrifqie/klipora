"""B-Roll: supporting clips over the speech, placed where they do not hide what happens on screen.

Actions
  analyze  transcript (timeline words) -> ONE AI planning call (cached by transcript; rule fallback when AI is off
           or down) -> candidate moments -> screen activity per moment (fullscreen / PiP / skip) -> slots by
           density -> sources per slot (local folder match, Pexels search when a key is set, AI image prompt for the local proxy)
           with thumbnails -> review file (kind "broll", one row per slot, `cands` + `pick` per row).
  fetch    previews for some rows (params.ids): generates the AI image of the picked candidate (spends Imagine
           quota) or, with params.query, searches the local folder + Pexels again with a new keyword. Rewrites
           the review file.
  apply    every checked row: download / generate / take the picked source and pre-render it to the exact slot
           duration at the sequence frame size (cover crop, audio stripped, Ken Burns for stills, white border
           for PiP) -> plan {"kind": "broll", ...} that panel/host/41_broll.jsx places on a clone of the sequence,
           on a new top video track "Klipora B-Roll" (PiP via Motion scale/position, crossfade via opacity keys).

Params: density (per minute, 1), max_dur (s, 4), placement auto|full|pip, transition cut|fade, src_local + folder,
src_pexels, src_ai + ai_max (images per run, 3), use_ai (planning call, true), ai_rank (local file names go into
the same call, true), style foto|ilustrasi|sketsa, scope (panel). Research: docs/research/product_visual.md 4.3,
AI research notes 5(d). Never logs or returns the AI key or the Pexels key.
"""
from __future__ import annotations

import base64
import json
import math
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .. import media as M, ranges as R, review as RV
from ..i18n import tr
from ..timeline import Timeline
from ..util import (EngineError, data_hash, ensure_free, file_hash, local_dir, read_json, run_ffmpeg, setting,
                    workdir, write_json)

TITLE = "B-Roll"
DESCRIPTION = "Supporting clips that match the speech: local folder, Pexels videos, or AI images."

TRACK = "Klipora B-Roll"          # host track + bin name (replaced on every apply); a Premiere name, not translated
OLD_TRACKS = ("AutoCut B-Roll",)  # the same track in projects made before the rename
OWN_DIRS = ("klipora", "autocut bot", "broll_cache")   # our own work folders inside a library (old name too)
PLAN_VERSION = "broll-plan-1"     # bump when the planning prompt changes (AI cache key)
FULL_MAX, PIP_MAX = 0.15, 0.5     # screen busy fraction: < FULL_MAX fullscreen, < PIP_MAX PiP, else skip
PIP_SCALE = 38                    # Motion scale % of a PiP card
FADE = 0.25                       # crossfade seconds (opacity keys)
MIN_DUR = 1.5
VIDEO_EXT = {".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi", ".wmv", ".mts", ".m2ts"}
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
STYLE = {"foto": "photorealistic photo, natural light, no text, no logos",
         "ilustrasi": "clean flat vector illustration, soft colors, no text, no logos",
         "sketsa": "pencil sketch drawing, white paper, no text, no logos"}

# On-screen instruction words: the viewer must see the screen while these are said.
UI_WORDS = set((
    "klik klick ngeklik diklik isi isikan diisi mengisi pilih dipilih memilih ketik diketik mengetik "  # i18n-ignore
    "scroll scrol tekan ditekan tombol menu kolom centang dicentang login masuk copy paste salin tempel layar disini sini "  # i18n-ignore
    "situ bawah atas kanan kiri tab halaman tampilan buka dibuka membuka tutup submit enter form field password username "  # i18n-ignore
    "akun kode link tautan setting pengaturan refresh upload save simpan check cek aktifin aktifkan setup install "  # i18n-ignore
    "download unduh edit hapus ubah ganti tambahkan verifikasi "  # i18n-ignore
).split())
# Concrete, filmable topics that benefit from b-roll (business tutorials for Indonesian sellers).
GOOD_WORDS = set((
    "uang duit cuan untung keuntungan penghasilan bisnis usaha jualan jual menjual dagang toko produk "  # i18n-ignore
    "barang pesanan order pembeli pelanggan customer reseller dropship dropshipper kirim pengiriman paket kurir gudang stok "  # i18n-ignore
    "modal harga diskon promo sukses berhasil hasil target tim grup komunitas keluarga rumah kantor kota pasar belanja "  # i18n-ignore
    "online internet handphone smartphone laptop komputer email notifikasi pesan chat bank transfer bayar pembayaran "  # i18n-ignore
    "dompet kerja karyawan branding merek brand makanan minuman kopi baju pakaian sepatu kosmetik "  # i18n-ignore
).split())
STOP_EXTRA = set((
    "yang dan atau ini itu nah ya ok oke eh em hmm anu nih tuh sih deh dong kok lah kan guys gitu gini "  # i18n-ignore
    "kayak gimana bagaimana kalian kamu saya aku kita kami dia mereka teman teman-teman temen bisa akan sudah udah lagi "  # i18n-ignore
    "juga aja saja dulu nanti tadi terus jadi kalau kalo karena tapi untuk dengan dari pada dalam oleh sama punya ada "  # i18n-ignore
    "adalah mau harus silahkan silakan mungkin semua semuanya baik pertama kedua video tutorial cara the and for with "  # i18n-ignore
    "this that from your clip stock footage pexels pixabay final copy img dsc vid mov edit render new baru kemudian "  # i18n-ignore
    "sampai biasanya harusnya takutnya sebenarnya sebenernya nantinya misalkan contohnya intinya pokoknya makanya tolong "  # i18n-ignore
    "sebentar lanjutnya sesuai ketika seperti setelah sebelum sekarang langsung memang emang banget sekali tetap masih "  # i18n-ignore
    "anyway oke "  # i18n-ignore
).split())
BRANDS = {"whatsapp", "cloudflare", "telegram", "tiktok", "shopee", "google", "tokopedia", "instagram", "facebook",
          "youtube", "lazada", "gojek", "grab"}


# ====================================================================== small helpers

def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


def _num(v, default):
    try:
        f = float(v)
        return default if f != f else f
    except (TypeError, ValueError):
        return default


def params_of(p):
    """Normalised params (panel names). Unknown keys are ignored."""
    p = p or {}
    folder = str(p.get("folder") or "").strip().strip('"')
    return {
        "density": _clamp(_num(p.get("density"), 1.0), 0.25, 8.0),
        "max_dur": _clamp(_num(p.get("max_dur"), 4.0), MIN_DUR, 10.0),
        "placement": p.get("placement") if p.get("placement") in ("auto", "full", "pip") else "auto",
        "transition": "fade" if p.get("transition") in ("fade", "crossfade") else "cut",
        "src_local": bool(p.get("src_local", True)) and bool(folder),
        "folder": folder,
        "src_pexels": bool(p.get("src_pexels", True)),
        "src_ai": bool(p.get("src_ai", True)),
        "ai_max": int(_clamp(_num(p.get("ai_max"), 3), 0, 8)),
        "use_ai": bool(p.get("use_ai", True)),
        "ai_rank": bool(p.get("ai_rank", True)),
        "style": p.get("style") if p.get("style") in STYLE else "foto",
        "scope": p.get("scope"),
    }


def norm(t):
    return re.sub(r"[^\w]+", "", str(t).lower())


def words_of(text):
    """Lowercase word tokens of free text (keeps '-' inside words: 'teman-teman')."""
    return [w for w in re.split(r"[^0-9a-zA-Z\-]+", str(text).lower()) if w.strip("-")]


def looks_indonesian(text):
    """True when a query has Indonesian words (Pexels then searches with locale id-ID)."""
    from ..ai.prompts import STOP
    ws = words_of(text)
    return any(w in STOP or w in STOP_EXTRA or w in GOOD_WORDS or w in UI_WORDS or w.endswith(("nya", "kan"))
               for w in ws)


def stem_id(w):
    """Very small Indonesian stemmer for matching ('penjualan' ~ 'jual', 'keuangan' ~ 'uang')."""
    w = w.lower()
    if len(w) < 6:
        return w
    for suf in ("nya", "kan", "lah", "kah", "an", "i"):
        if w.endswith(suf) and len(w) - len(suf) >= 4:
            w = w[: -len(suf)]
            break
    for pre in ("meng", "meny", "mem", "men", "me", "peng", "peny", "pem", "pen", "pe", "ber", "ter", "di", "ke", "se"):
        if w.startswith(pre) and len(w) - len(pre) >= 4:
            w = w[len(pre):]
            break
    return w


def content_words(text):
    """Words worth a b-roll: >= 4 letters, not a stop word, not an on-screen instruction."""
    from ..ai.prompts import STOP
    out = []
    for w in words_of(text):
        w = w.strip("-")
        if len(w) < 4 or w.isdigit() or w in STOP or w in STOP_EXTRA or w in UI_WORDS:
            continue
        out.append(w)
    return out


def file_tokens(rel):
    """Tokens of a library file: stem + parent folders, camelCase split, without numbers and stop words."""
    s = re.sub(r"([a-z])([A-Z])", r"\1 \2", str(rel))
    s = re.sub(r"\.[A-Za-z0-9]{2,4}$", "", s)
    out = []
    for w in re.split(r"[^0-9a-zA-Z]+", s.lower()):
        if len(w) >= 3 and not w.isdigit() and w not in STOP_EXTRA:
            out.append(w)
    return list(dict.fromkeys(out))


def _tok_match(a, b):
    """Similarity of two lowercase tokens, 0..1."""
    if a == b:
        return 1.0
    if len(a) >= 4 and len(b) >= 4 and (a.startswith(b) or b.startswith(a)):
        return 0.75
    sa, sb = stem_id(a), stem_id(b)
    if len(sa) >= 3 and sa == sb:
        return 0.8
    if len(sa) >= 4 and len(sb) >= 4 and (sa in b or sb in a):
        return 0.6
    return 0.0


def even(n):
    n = int(round(n))
    return n + (n % 2)


def render_fps(fps):
    """Output rate of the pre-rendered b-roll: the sequence rate halved until <= 30 (120 -> 30, 59.94 -> 29.97)."""
    f = float(fps or 30.0)
    while f > 31.0:
        f /= 2.0
    return round(max(f, 10.0), 3)


def aspect_name(w, h):
    """Nearest image-model aspect ratio for a frame."""
    r = w / max(1, h)
    best = min((("16:9", 16 / 9), ("3:2", 1.5), ("1:1", 1.0), ("2:3", 2 / 3), ("9:16", 9 / 16)),
               key=lambda x: abs(math.log(r / x[1])))
    return best[0]


def _dirs(job):
    root = workdir(job) / "broll"
    d = {"root": root, "thumbs": root / "thumbs", "render": root / "render"}
    for v in d.values():
        v.mkdir(parents=True, exist_ok=True)
    return d


def cache_dir(*sub):
    return local_dir("broll_cache", *sub)


# ====================================================================== local library

def scan_library(folder, max_files=3000, max_depth=4, exclude=()):
    """Video + image files under `folder` -> [{path, rel, name, kind, tokens}]. Skips our own work folders,
    the sequence's own media (`exclude`) and hidden folders. Names only: nothing is opened or probed."""
    root = Path(folder)
    if not root.is_dir():
        raise EngineError("NO_FOLDER", tr("broll.err.noFolder", folder=str(folder)), tr("broll.err.noFolderHint"))
    skip = {str(Path(p)).lower() for p in exclude if p}
    work = str(Path(setting("workRoot") or "")).lower() if setting("workRoot") else ""
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = Path(dirpath).relative_to(root)
        depth = 0 if str(rel_dir) == "." else len(rel_dir.parts)
        dirnames[:] = [d for d in dirnames if not d.startswith(".") and d.lower() not in OWN_DIRS
                       and depth < max_depth]
        if work and str(Path(dirpath)).lower().startswith(work):
            continue
        for f in sorted(filenames):
            ext = Path(f).suffix.lower()
            kind = "video" if ext in VIDEO_EXT else "image" if ext in IMAGE_EXT else None
            p = Path(dirpath) / f
            if not kind or str(p).lower() in skip or f.startswith("."):
                continue
            rel = str(p.relative_to(root))
            out.append({"path": str(p), "rel": rel, "name": f, "kind": kind, "tokens": file_tokens(rel)})
            if len(out) >= max_files:
                return out
    return out


def _idf(lib):
    df = {}
    for f in lib:
        for t in set(f["tokens"]):
            df[t] = df.get(t, 0) + 1
    n = max(1, len(lib))
    return {t: math.log(1.0 + n / c) for t, c in df.items()}


def match_library(terms, lib, idf=None, top=3):
    """Rank library files for weighted slot terms [(token, weight)] -> [(score, file)] best first (score > 0)."""
    if not lib or not terms:
        return []
    idf = idf or _idf(lib)
    norm_idf = max(idf.values()) if idf else 1.0
    out = []
    for f in lib:
        sc = 0.0
        for tok, w in terms:
            best, best_t = 0.0, None
            for ft in f["tokens"]:
                m = _tok_match(tok, ft)
                if m > best:
                    best, best_t = m, ft
            if best_t is not None:
                sc += w * best * (0.5 + 0.5 * idf.get(best_t, norm_idf) / norm_idf)
        if sc > 0:
            out.append((round(sc, 3), f))
    out.sort(key=lambda x: (-x[0], x[1]["rel"]))
    return out[:top]


def slot_terms(slot):
    """Weighted matching terms of a slot: keyword 3, English queries 2, sentence content words 1."""
    terms = {}
    for t in words_of(slot.get("kw", "")):
        if len(t) >= 3:
            terms[t] = max(terms.get(t, 0), 3.0)
    for t in words_of(slot.get("q", "")) + words_of(slot.get("alt", "")):
        if len(t) >= 3 and t not in STOP_EXTRA:
            terms[t] = max(terms.get(t, 0), 2.0)
    for t in content_words(slot.get("text", "")):
        terms[t] = max(terms.get(t, 0), 1.0)
    return list(terms.items())


LOCAL_MIN = 1.4   # library match score that counts as a real match (one keyword hit ~ 1.5-3)


# ====================================================================== screen activity

def screen_activity(path, s0, s1, fps=8, width=480, diff_thr=18, still_frac=0.0004, global_frac=0.12, gw=12, gh=6):
    """How much changes on screen in source [s0, s1) of a video (port of proto_visual/activity.py, one window).
    -> {"busy": non-still sample fraction, "global": scroll/page-switch fraction, "grid": gh x gw activity
    (normalised to 1), "n": samples} or None when the file has no video. CPU decode (NVDEC rejects these files)."""
    import numpy as np
    info = M.probe(path)
    if not info["has_video"]:
        return None
    if Path(path).suffix.lower() in IMAGE_EXT:
        return {"busy": 0.0, "global": 0.0, "grid": [[0.0] * gw for _ in range(gh)], "n": 0}
    w = int(width)
    h = even(info["height"] * w / max(1, info["width"]))
    dur = max(0.3, float(s1) - float(s0))
    raw = run_ffmpeg(["-ss", f"{max(0.0, float(s0)):.3f}", "-t", f"{dur:.3f}", "-i", str(path), "-an", "-sn",
                      "-vf", f"fps={fps},scale={w}:{h}:flags=area,format=gray", "-f", "rawvideo", "-"]).stdout
    n = len(raw) // (w * h)
    if n < 2:
        return {"busy": 0.0, "global": 0.0, "grid": [[0.0] * gw for _ in range(gh)], "n": 0}
    fr = np.frombuffer(raw[: n * w * h], np.uint8).reshape(n, h, w)
    try:
        import cv2
        kernel = np.ones((3, 3), np.uint8)
        opener = lambda m: cv2.morphologyEx(m, cv2.MORPH_OPEN, kernel)  # noqa: E731
    except ImportError:          # pragma: no cover - opencv is installed on the target PC
        opener = lambda m: m  # noqa: E731
    acc = np.zeros((gh, gw), np.float64)
    busy = glob = 0
    ch, cw = h // gh, w // gw
    for k in range(1, n):
        diff = np.abs(fr[k].astype(np.int16) - fr[k - 1].astype(np.int16)) > diff_thr
        m = opener(diff.astype(np.uint8))
        frac = float(m.mean())
        if frac < still_frac:
            continue
        busy += 1
        if frac > global_frac:
            glob += 1
            continue
        acc += m[: ch * gh, : cw * gw].reshape(gh, ch, gw, cw).sum(axis=(1, 3))
    tot = acc.sum()
    grid = (acc / tot).round(4).tolist() if tot > 0 else acc.tolist()
    return {"busy": round(busy / (n - 1), 3), "global": round(glob / (n - 1), 3), "grid": grid, "n": n - 1}


def _main_tracks(tl):
    """Video tracks that hold the program (our own b-roll track is ignored)."""
    return [t.index for t in tl.video if t.name != TRACK and t.name not in OLD_TRACKS]


def slot_activity(tl, t0, t1, tracks=None):
    """Screen activity of sequence [t0, t1): topmost visible clip per piece, weighted by piece length.
    -> activity dict, {"busy": 0, ...} when no video plays there, or None when it cannot be measured."""
    tracks = _main_tracks(tl) if tracks is None else tracks
    edges = {t0, t1}
    for c in tl.video_clips(tracks=tracks):
        if c.end > t0 and c.start < t1:
            edges.update(x for x in (c.start, c.end) if t0 < x < t1)
    edges = sorted(edges)
    pieces = []
    for a, b in zip(edges, edges[1:]):
        if b - a < 0.05:
            continue
        c = tl.clip_at((a + b) / 2, "video", tracks=tracks)
        if c is not None:
            pieces.append((c, a, b))
    if not pieces:
        return {"busy": 0.0, "global": 0.0, "grid": None, "n": 0, "none": True}
    tot = busy = glob = 0.0
    grid = None
    for c, a, b in pieces:
        if not c.has_media or not Path(c.path).is_file():
            return None
        s0, s1 = sorted((c.seq_to_src(a), c.seq_to_src(b)))
        try:
            act = screen_activity(c.path, s0, s1)
        except EngineError:
            return None
        if act is None:
            continue
        d = b - a
        tot += d
        busy += act["busy"] * d
        glob += act["global"] * d
        if act["grid"]:
            import numpy as np
            g = np.array(act["grid"]) * d
            grid = g if grid is None else grid + g
    if tot <= 0:
        return {"busy": 0.0, "global": 0.0, "grid": None, "n": 0, "none": True}
    if grid is not None:
        grid = (grid / grid.sum()).round(4).tolist() if grid.sum() > 0 else grid.tolist()
    return {"busy": round(busy / tot, 3), "global": round(glob / tot, 3), "grid": grid}


def pip_geometry(corner, w, h, scale=PIP_SCALE, margin=0.025):
    """Motion Position (normalised to the frame) of a PiP card that is a scaled copy of the full frame."""
    s = scale / 100.0
    mx = margin
    my = margin * w / max(1, h)
    x = 1 - mx - s / 2 if corner in ("tr", "br") else mx + s / 2
    y = my + s / 2 if corner in ("tr", "tl") else 1 - my - s / 2
    return [round(x, 4), round(y, 4)]


def choose_corner(grid, scale=PIP_SCALE, w=16, h=9):
    """Corner where the PiP hides the least on-screen activity (ties -> top right)."""
    if not grid:
        return "tr"
    gh, gw = len(grid), len(grid[0])
    s = scale / 100.0 + 0.04
    nx, ny = max(1, math.ceil(s * gw)), max(1, math.ceil(s * gh))

    def mass(xs, ys):
        return sum(grid[y][x] for y in ys for x in xs)
    boxes = {"tr": (range(gw - nx, gw), range(0, ny)), "tl": (range(0, nx), range(0, ny)),
             "br": (range(gw - nx, gw), range(gh - ny, gh)), "bl": (range(0, nx), range(gh - ny, gh))}
    scores = {k: mass(*v) for k, v in boxes.items()}
    best = min(scores.values())
    for k in ("tr", "tl", "br", "bl"):          # preference order on (near) ties
        if scores[k] <= best + 0.02:
            return k
    return "tr"


def placement_for(mode, act):
    """-> (place 'full'|'pip', auto verdict 'full'|'pip'|'skip', note or None)."""
    if act is None:
        auto, note = "pip", {"type": "warn", "text": tr("broll.note.unread")}
    elif act.get("none"):
        auto, note = "full", None
    else:
        b = act["busy"]
        auto = "full" if b < FULL_MAX else "pip" if b < PIP_MAX else "skip"
        pct = int(round(b * 100))
        note = None if auto == "full" else {
            "type": "info" if auto == "pip" else "warn",
            "text": tr("broll.note.pip", pct=pct) if auto == "pip" else tr("broll.note.busy", pct=pct)}
    if mode == "full":
        return "full", auto, (note if auto == "skip" else None)
    if mode == "pip":
        return "pip", auto, None
    return ("pip" if auto == "skip" else auto), auto, note


# ====================================================================== planning (AI + rules)

PICK_SCHEMA = {"type": "object", "required": ["t", "q"], "properties": {
    "t": {"type": "string"}, "w": {"type": "string"}, "kw": {"type": "string"}, "q": {"type": "string"},
    "alt": {"type": "string"}, "img": {"type": "string"}}}


def _picks_of(obj):
    """{"picks": [...]} or a bare [...] (models sometimes drop the wrapper) -> list or None."""
    picks = obj.get("picks") if isinstance(obj, dict) else obj
    return picks if isinstance(picks, list) else None


def _plan_check(obj):
    """chat() check: only the shape of the answer; single bad picks are dropped later, not the whole answer."""
    picks = _picks_of(obj)
    if picks is None:
        return ['expected {"picks": [...]}']
    return [] if any(isinstance(x, dict) for x in picks) or not picks else ["picks must be objects"]


def ai_reason(e):
    """Reason (job language) for an AI fallback warning (no key, no URL)."""
    from ..ai import client as ai
    if isinstance(e, ai.AIUnavailable):
        return tr("broll.ai.offline")
    if isinstance(e, ai.AIBadOutput):
        return tr("broll.ai.badAnswer")
    return tr("broll.ai.failed")


def plan_messages(sents, k, lib_names=None):
    from ..ai.prompts import BASE, _lines
    lib_rule = ('- "lib": the number of the best matching LIBRARY file (the user\'s own clips) for this moment, '
                'or -1 when no file fits well\n') if lib_names else ""
    lib_ex = '"lib":-1,' if lib_names else ""
    sys_ = f"""{BASE  # i18n-ignore: AI prompt (content, not UI text)
}
Task: plan B-roll for a video. B-roll = short supporting footage or an image shown over the video while the
speaker talks. Input: TRANSCRIPT, one line per row as "mm:ss text" (Indonesian speech).
Choose the {k} best lines for B-roll (fewer when the video has fewer good moments).
Good moments: concrete things, places, people, results, money, business, feelings, examples.
Bad moments: greetings, filler, and on-screen instructions ("klik", "isi", "pilih", "ketik", "scroll", "di sini"),
because the viewer must see the screen then.
For every pick return:
- "t": the mm:ss of the line, copied exactly
- "w": the first 2-3 words of the line, copied exactly
- "kw": 1-3 Indonesian words copied from the line that name the B-roll subject
- "q": English stock-video search query, 2-5 words, concrete and filmable (people, objects, places, actions); no brand names, no UI words
- "alt": a different English query, 2-5 words
- "img": English prompt for an AI image generator, one sentence describing a concrete scene; no text, no logos, no brand names
{lib_rule}- "score": 0-100, how much the moment benefits from B-roll
Output: {{"picks":[{{"t":"00:00","w":"...","kw":"...","q":"...","alt":"...","img":"...",{lib_ex}"score":0}}]}}"""
    user = "TRANSCRIPT:\n" + "\n".join(_lines(sents))
    if lib_names:
        user += "\n\nLIBRARY:\n" + "\n".join(f"{i + 1} {n}" for i, n in enumerate(lib_names))
    return [{"role": "system", "content": sys_}, {"role": "user", "content": user}]


def _clean_query(q, brands):
    q = re.sub(r"\s+", " ", str(q or "")).strip().strip(".\"'").lower()
    q = " ".join(w for w in q.split() if norm(w) not in brands)
    return q if q and q.isascii() and 1 <= len(q.split()) <= 7 else ""


def _ai_cache():
    from ..ai import client as ai
    return ai.Cache()


def plan_ai(sents, k, lib=None, emit=None, model=None, cache=True):
    """One AI call -> {"source": "ai"|"cache", "picks": [{sid, kw, q, alt, img, lib, score}]}.
    Raises ai.AIError when the AI cannot be used (caller falls back to rules)."""
    from ..ai import client as ai
    from ..ai.prompts import resolve
    lib_names = [Path(f["rel"]).with_suffix("").as_posix() for f in (lib or [])][:200] or None
    model = model or setting("aiModel") or "grok-fast"
    msgs = plan_messages(sents, k, lib_names)
    store = _ai_cache()
    key = store.key(PLAN_VERSION, model, k, lib_names, [m["content"] for m in msgs])
    raw = store.get(key) if cache else None
    source = "cache" if raw else "ai"
    if raw is None:
        if not ai.available():
            raise ai.AIUnavailable(tr("broll.ai.noProxy"))
        if emit:
            emit.log(f"AI broll plan ({model}), {len(sents)} lines")
        # exactly one request: no client retry, no repair round (a bad answer falls back to the rules)
        raw = ai.chat_json(msgs, schema=None, check=_plan_check, model=model, tag="broll_plan", retries=0, repair=0,
                           timeout=150)
        if cache:
            store.put(key, raw)
    elif emit:
        emit.done_note(tr("broll.done.aiCache"))
    brands = BRANDS | {norm(g) for g in (setting("glossary") or []) if isinstance(g, str)}
    picks, seen = [], set()
    for it in _picks_of(raw) or []:
        if not isinstance(it, dict) or ai.validate_schema(it, PICK_SCHEMA):
            continue
        idx, _how = resolve(sents, it.get("t"), it.get("w", ""), True, after=-1, tol=1.0)
        if idx is None:
            continue
        kw = re.sub(r"\s+", " ", str(it.get("kw") or "")).strip()[:40]
        k0 = norm(kw.split()[0]) if kw else ""
        if k0 and k0 not in norm(sents[idx]["text"]):   # keyword quoted from a neighbour line: move there
            idx = next((j for j in (idx + 1, idx - 1) if 0 <= j < len(sents) and k0 in norm(sents[j]["text"])), idx)
        if idx in seen:
            continue
        seen.add(idx)
        q = _clean_query(it.get("q"), brands)
        alt = _clean_query(it.get("alt"), brands)
        if not q and not alt:
            continue
        if not kw or norm(kw.split()[0]) not in norm(sents[idx]["text"]):
            cw = content_words(sents[idx]["text"])
            kw = max(cw, key=len) if cw else (q or alt).split()[0]
        try:
            li = int(str(it.get("lib", -1)).strip().lstrip("Ll#"))
        except ValueError:
            li = -1
        img = re.sub(r"\s+", " ", str(it.get("img") or "")).strip()[:400] or f"a photo of {q or alt}"
        picks.append({"sid": idx, "kw": kw, "q": q or alt, "alt": alt if q else "", "img": img,
                      "lib": li - 1 if lib_names and 1 <= li <= len(lib_names) else -1,
                      "score": int(_clamp(_num(it.get("score"), 0), 0, 100))})
    return {"source": source, "picks": picks}


def plan_rules(sents, lib=None, idf=None):
    """Rule fallback: content words, business topics up, on-screen instructions down, local matches up."""
    picks = []
    for k, s in enumerate(sents):
        dur = s["end"] - s["start"]
        ws = words_of(s["text"])
        cw = content_words(s["text"])
        if dur < 0.8 or not cw:
            continue
        good = [w for w in cw if w in GOOD_WORDS or stem_id(w) in GOOD_WORDS]
        ui = sum(1 for w in ws if w in UI_WORDS)
        score = 12 * min(len(set(cw)), 4) + 18 * min(len(good), 2) - 22 * ui + (8 if 1.5 <= dur <= 10 else 0)
        kw = good[0] if good else max(cw, key=len)
        li = -1
        if lib:
            m = match_library([(w, 1.0) for w in cw], lib, idf, top=1)
            if m and m[0][0] >= LOCAL_MIN * 0.7:
                score += 25
                li = lib.index(m[0][1])
                kw = next((w for w in cw if any(_tok_match(w, t) >= 0.75 for t in m[0][1]["tokens"])), kw)
        picks.append({"sid": k, "kw": kw, "q": "", "alt": "", "img": f"a scene about {kw}: {s['text'][:120]}", "lib": li,
                      "score": int(_clamp(score, 0, 100)), "rule": True})
    return picks


def slot_window(sent, dwords, kw, max_dur, lo, hi, fps):
    """[t0, t1] of a b-roll for a sentence: starts at the keyword (or the sentence), lasts to the sentence end
    (+0.4 s), at least min(2.5, max_dur), at most max_dur, inside [lo, hi], frame aligned."""
    t0 = sent["start"]
    key = norm(str(kw).split()[0]) if kw else ""
    if key:
        for i in range(sent["w0"], sent["w1"] + 1):
            if norm(dwords[i]["text"]).startswith(key[: max(3, len(key) - 2)]):
                t0 = max(sent["start"], dwords[i]["start"] - 0.08)
                break
    want = _clamp(sent["end"] - t0 + 0.4, min(2.5, max_dur), max_dur)
    t0 = max(lo, t0)
    t1 = min(hi, t0 + want)
    if t1 - t0 < MIN_DUR:
        t0 = max(lo, t1 - MIN_DUR)
    f = float(fps or 30)
    return round(round(t0 * f) / f, 4), round(round(t1 * f) / f, 4)


def select_slots(cands, n, spacing, mode):
    """Greedy by (activity adjusted) score: >= spacing s between starts, no overlaps, n slots. In auto mode slots
    over a busy screen are not counted; up to 3 of them are returned as skipped (shown unchecked)."""
    def adj(c):
        b = c["act"]["busy"] if c.get("act") and not c["act"].get("none") else (0.3 if c.get("act") is None else 0.0)
        return c["score"] * (1 - 0.5 * b if mode == "auto" else 1)
    chosen, skipped = [], []
    for c in sorted(cands, key=lambda c: (-adj(c), c["t0"])):
        if len(chosen) >= n:
            break
        if any(abs(c["t0"] - o["t0"]) < spacing or (c["t0"] < o["t1"] + 0.5 and o["t0"] < c["t1"] + 0.5)
               for o in chosen):
            continue
        if mode == "auto" and c["auto"] == "skip":
            if len(skipped) < 3 and not any(abs(c["t0"] - o["t0"]) < spacing for o in skipped):
                skipped.append(c)
            continue
        chosen.append(c)
    skipped = [s for s in skipped if not any(s["t0"] < o["t1"] + 0.5 and o["t0"] < s["t1"] + 0.5 for o in chosen)]
    return chosen, skipped


# ====================================================================== thumbnails

def _thumb_name(*parts):
    return data_hash(parts)[:16] + ".jpg"


def thumb_from_image(src, out, width=240):
    from PIL import Image
    out = Path(out)
    if out.is_file():
        return out
    with Image.open(src) as im:
        im = im.convert("RGB")
        im.thumbnail((width, width * 2))
        out.parent.mkdir(parents=True, exist_ok=True)
        im.save(out, "JPEG", quality=82)
    return out


def thumb_for_file(path, kind, thumbs_dir, width=240):
    """Small JPEG preview of a local file (cached by path + size + mtime). None when it cannot be read."""
    p = Path(path)
    try:
        st = p.stat()
    except OSError:
        return None
    out = Path(thumbs_dir) / _thumb_name(str(p).lower(), st.st_size, st.st_mtime_ns, width)
    if out.is_file():
        return str(out)
    try:
        if kind == "image":
            return str(thumb_from_image(p, out, width))
        info = M.probe(p)
        M.frame_grab(p, min(max(0.5, info["duration"] * 0.3), max(0.0, info["duration"] - 0.2)), out=out, width=width)
        return str(out) if out.is_file() else None
    except Exception:  # noqa: BLE001 - a broken file just has no thumbnail
        return None


# ====================================================================== Pexels

PEXELS_API = "https://api.pexels.com/videos/search"


def pexels_key():
    """Pexels key from the panel Settings (write-only field), else PEXELS_API_KEY in .env. Never logged."""
    k = setting("pexelsKey")
    if not (isinstance(k, str) and k.strip()):
        from ..ai import client as ai
        k = os.environ.get("PEXELS_API_KEY") or ai.load_env().get("PEXELS_API_KEY") or ""
    k = str(k).strip()
    return k if len(k) >= 10 else ""


def _http_get(url, headers=None, timeout=20, max_bytes=8 * 1024 ** 2):
    req = urllib.request.Request(url, headers={"User-Agent": "Klipora/2.0", **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read(max_bytes + 1)[:max_bytes]


def _pexels_get(params, key):
    """GET the Pexels video search (tests replace this). -> parsed JSON."""
    url = PEXELS_API + "?" + urllib.parse.urlencode(params)
    return json.loads(_http_get(url, {"Authorization": key}, timeout=20).decode("utf-8"))


class PexelsError(Exception):
    """Pexels failure with a user message (job language). fatal=True: stop asking Pexels in this run (key, rate limit)."""

    def __init__(self, msg, fatal=False):
        super().__init__(msg)
        self.fatal = fatal


def pexels_search(query, w, h, key, locale=None, per_page=6):
    """Videos for a query (cached 24 h per query/orientation/locale) -> [{id, page, user, user_url, image, duration,
    width, height, files: [{link, width, height, quality}]}]. PexelsError with a user message on failure."""
    orient = "landscape" if w >= h * 1.1 else "portrait" if h >= w * 1.1 else "square"
    prm = {"query": query, "orientation": orient, "size": "medium", "per_page": per_page}  # i18n-ignore
    if locale:
        prm["locale"] = locale
    cp = cache_dir("pexels_search") / (data_hash(prm) + ".json")
    hit = read_json(cp)
    if hit and time.time() - hit.get("at", 0) < 24 * 3600:
        return hit["videos"]
    try:
        data = _pexels_get(prm, key)
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise PexelsError(tr("broll.pexels.keyRejected"), True) from None
        if e.code == 429:
            raise PexelsError(tr("broll.pexels.rateLimit"), True) from None
        raise PexelsError(tr("broll.pexels.http", code=str(e.code))) from None
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise PexelsError(tr("broll.pexels.unreachable", reason=str(getattr(e, "reason", e))), True) from None
    vids = []
    for v in data.get("videos") or []:
        files = [{"link": f.get("link"), "width": int(f.get("width") or 0), "height": int(f.get("height") or 0),
                  "quality": f.get("quality")}
                 for f in v.get("video_files") or [] if f.get("file_type") == "video/mp4" and f.get("link")]
        if not files:
            continue
        user = v.get("user") or {}
        vids.append({"id": v.get("id"), "page": v.get("url"), "user": user.get("name") or "", "user_url": user.get("url"),
                     "image": v.get("image") or ((v.get("video_pictures") or [{}])[0].get("picture")),
                     "duration": float(v.get("duration") or 0), "width": int(v.get("width") or 0),
                     "height": int(v.get("height") or 0), "files": files})
    write_json(cp, {"at": time.time(), "videos": vids})
    return vids


def pick_pexels_file(files, w, h):
    """The smallest mp4 that still covers about 2/3 of the frame (nearest above), else the largest."""
    need = min(max(w, h), 1920) * 0.66
    big = lambda f: max(f["width"], f["height"])  # noqa: E731
    ok = sorted((f for f in files if big(f) >= need), key=big)
    return ok[0] if ok else max(files, key=big)


def _download(url, dest, emit=None, max_bytes=300 * 1024 ** 2, timeout=30):
    """Stream a URL to dest (atomic). Checks free disk space first. Tests replace this."""
    dest = Path(dest)
    if dest.is_file() and dest.stat().st_size > 0:
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    req = urllib.request.Request(url, headers={"User-Agent": "Klipora/2.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        total = int(r.headers.get("Content-Length") or 0)
        if total > max_bytes:
            raise EngineError("BROLL_BIG", tr("broll.err.tooBig"), url)
        ensure_free(total or 80 * 1024 ** 2, dest.parent)
        got = 0
        with open(tmp, "wb") as f:
            while True:
                chunk = r.read(256 * 1024)
                if not chunk:
                    break
                f.write(chunk)
                got += len(chunk)
                if got > max_bytes:
                    raise EngineError("BROLL_BIG", tr("broll.err.tooBig"), url)
                if emit is not None:
                    emit.check_cancel()
    os.replace(tmp, dest)
    return dest


def prune_cache(sub="pexels", keep_bytes=600 * 1024 ** 2):
    """Delete the oldest downloads when the cache folder grows past keep_bytes."""
    d = cache_dir(sub)
    files = sorted((p for p in d.glob("*") if p.is_file()), key=lambda p: p.stat().st_mtime, reverse=True)
    tot = 0
    for p in files:
        tot += p.stat().st_size
        if tot > keep_bytes:
            try:
                p.unlink()
            except OSError:
                pass


def pexels_cands(query, w, h, key, thumbs_dir, locale=None, top=3, dur=4.0):
    vids = pexels_search(query, w, h, key, locale=locale)
    vids.sort(key=lambda v: (v["duration"] < dur, 0))     # long enough first, API order otherwise
    out = []
    for v in vids[:top]:
        f = pick_pexels_file(v["files"], w, h)
        thumb = None
        if v.get("image"):
            tp = Path(thumbs_dir) / f"px_{v['id']}.jpg"
            try:
                if not tp.is_file():
                    raw = _http_get(v["image"], timeout=15, max_bytes=4 * 1024 ** 2)
                    tmp = tp.with_suffix(".src")
                    tmp.write_bytes(raw)
                    try:
                        thumb_from_image(tmp, tp)
                    finally:
                        tmp.unlink(missing_ok=True)
                thumb = str(tp)
            except Exception:  # noqa: BLE001 - no thumbnail is not an error
                thumb = None
        out.append({"src": "pexels", "id": v["id"], "url": f["link"], "fw": f["width"], "fh": f["height"],
                    "dur": v["duration"], "page": v.get("page"), "user": v.get("user"), "user_url": v.get("user_url"),
                    "thumb": thumb, "name": f"{query} #{v['id']}", "query": query})
    return out


# ====================================================================== AI image (local proxy, optional)

def _imagine_http(prompt, aspect, timeout=240):
    """POST /images/generations on the local OpenAI-compatible proxy -> JPEG bytes (tests replace this)."""
    from ..ai import client as ai
    cfg = ai.config()
    if cfg["disabled"]:
        raise ai.AIUnavailable(tr("broll.img.disabled"))
    if not cfg["has_key"]:
        raise ai.AIUnavailable(tr("broll.img.noKey"))
    body = json.dumps({"model": "grok-imagine", "prompt": prompt, "n": 1, "aspect_ratio": aspect,
                       "response_format": "b64_json"}).encode("utf-8")
    req = urllib.request.Request(cfg["base_url"].rstrip("/") + "/images/generations", data=body, method="POST",
                                 headers={"Content-Type": "application/json",
                                          "Authorization": "Bearer " + ai.load_env().get("AI_API_KEY", "")})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 503:
            raise ai.AIUnavailable(tr("broll.img.quota")) from None
        if e.code in (401, 403):
            raise ai.AIUnavailable(tr("broll.img.keyRejected")) from None
        raise ai.AIError(tr("broll.img.http", code=str(e.code))) from None
    except (urllib.error.URLError, OSError) as e:
        raise ai.AIUnavailable(tr("broll.img.unreachable", reason=str(getattr(e, "reason", e)))) from None
    return base64.b64decode(data["data"][0]["b64_json"])


def imagine_path(prompt, aspect):
    return cache_dir("ai") / (data_hash([prompt, aspect]) + ".jpg")


def imagine(prompt, aspect, emit=None):
    """AI image still for a prompt, cached by prompt + aspect (a re-run never spends quota again).
    -> (Path, generated: bool). Raises ai.AIError."""
    out = imagine_path(prompt, aspect)
    if out.is_file() and out.stat().st_size > 0:
        return out, False
    ensure_free(20 * 1024 ** 2, out.parent)
    if emit is not None:
        emit.log("AI image: 1 image")
    raw = _imagine_http(prompt, aspect)
    if len(raw) < 1000:
        from ..ai import client as ai
        raise ai.AIError(tr("broll.img.empty"))
    tmp = out.with_suffix(".part")
    tmp.write_bytes(raw)
    os.replace(tmp, out)
    return out, True


# ====================================================================== rendering

_ENC = {"nvenc": None}


def _encoders(w):
    nv = ["-c:v", "h264_nvenc", "-preset", "p4", "-rc", "vbr", "-cq", "21", "-b:v", "0"]
    x264 = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20"]
    if w > 4096 or _ENC["nvenc"] is False:
        return [x264]
    return [nv, x264]


def render_clip(src, kind, out, dur, w, h, fps, pip=False, motion=0, start=None, src_dur=None):
    """Pre-render one b-roll to exactly ceil(dur*fps)+2 frames at w x h (cover crop), h264 yuv420p, no audio.
    kind 'image' = Ken Burns (upscaled 2x before zoompan so it does not jitter; motion 0 zoom in, 1 zoom out,
    2 zoom in + pan). pip=True bakes a thin white border so the scaled card stands out. Writes atomically."""
    out = Path(out)
    if out.is_file() and out.stat().st_size > 0:
        return out
    w, h = even(w), even(h)
    n = int(math.ceil(dur * fps)) + 2
    border = max(2, even(w * 0.012)) if pip else 0
    iw, ih = w - 2 * border, h - 2 * border
    if kind == "image":
        z = {0: f"1+0.12*on/{n}", 1: f"1.12-0.12*on/{n}", 2: f"1.04+0.08*on/{n}"}[motion % 3]
        x = "(iw-iw/zoom)*on/" + str(n) if motion % 3 == 2 else "iw/2-(iw/zoom/2)"
        chain = (f"scale={2 * iw}:{2 * ih}:force_original_aspect_ratio=increase,crop={2 * iw}:{2 * ih},setsar=1,"
                 f"zoompan=z='{z}':x='{x}':y='ih/2-(ih/zoom/2)':d={n}:s={iw}x{ih}:fps={fps}")
        inputs = ["-i", str(src)]
    else:
        sd = float(src_dur or 0.0)
        s0 = float(start) if start is not None else (min(max(0.0, sd * 0.15), max(0.0, sd - dur)) if sd else 0.0)
        loop = ["-stream_loop", "-1"] if sd and sd - s0 < dur + 0.1 else []
        chain = f"scale={iw}:{ih}:force_original_aspect_ratio=increase,crop={iw}:{ih},setsar=1,fps={fps}"
        inputs = [*loop, "-ss", f"{s0:.3f}", "-i", str(src)]
    if border:
        chain += f",pad={w}:{h}:{border}:{border}:color=white"
    chain += ",format=yuv420p"
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.stem + ".part.mp4")
    last = None
    for enc in _encoders(w):
        try:
            run_ffmpeg([*inputs, "-vf", chain, "-frames:v", str(n), "-r", f"{fps:g}", "-an", "-sn", "-dn", *enc,
                        "-movflags", "+faststart", "-y", str(tmp)], timeout=600)
            os.replace(tmp, out)
            return out
        except EngineError as e:
            last = e
            if "nvenc" in enc[1]:
                _ENC["nvenc"] = False
            tmp.unlink(missing_ok=True)
    raise last


# ====================================================================== review helpers

def _ctx_parts(text, kw):
    k = (kw or "").split()[0] if kw else ""
    i = text.lower().find(k.lower()) if k else -1
    if i < 0:
        return {"pre": "", "post": text}
    return {"pre": text[:i].strip()[-60:], "post": text[i + len(k):].strip()[:80]}


def _choose_pick(cands, ai_left):
    """Default candidate: local match, else Pexels, else an AI image while the per-run budget lasts."""
    for want in ("local", "pexels"):
        for k, c in enumerate(cands):
            if c["src"] == want:
                return k
    for k, c in enumerate(cands):
        if c["src"] == "ai" and (c.get("thumb") or ai_left > 0):
            return k
    return -1


def _local_cands(slot, lib, idf, thumbs, used, ai_lib=-1, top=3):
    ranked = match_library(slot_terms(slot), lib, idf, top=top + 2)
    ai_f = lib[ai_lib] if 0 <= ai_lib < len(lib) else None
    if ai_f is not None:
        ranked = [(max(LOCAL_MIN, ranked[0][0] if ranked else 0) + 0.5, ai_f)] + [r for r in ranked if r[1] is not ai_f]
    out = []
    for sc, f in ranked:
        if sc < LOCAL_MIN * (0.6 if out else 1.0):
            continue
        out.append({"src": "local", "path": f["path"], "name": f["rel"], "kind": f["kind"], "score": sc,
                    "thumb": thumb_for_file(f["path"], f["kind"], thumbs), "ai": f is ai_f})
        if len(out) >= top:
            break
    out.sort(key=lambda c: (c["path"] in used, -c["score"]))   # unused files first (variety)
    return out


def ai_thumb(img, thumbs_dir):
    """Small preview of a generated AI image (the review list shows 72 px wide thumbnails)."""
    try:
        return str(thumb_from_image(img, Path(thumbs_dir) / ("ai_" + Path(img).stem[:16] + ".jpg")))
    except Exception:  # noqa: BLE001
        return str(img)


def _ai_cand(prompt, style, w, h, thumbs_dir=None):
    full = f"{prompt}. {STYLE[style]}"
    aspect = aspect_name(w, h)
    p = imagine_path(full, aspect)
    thumb = None
    if p.is_file():                       # generated in an earlier run: free preview
        thumb = ai_thumb(p, thumbs_dir) if thumbs_dir else str(p)
    return {"src": "ai", "prompt": full, "aspect": aspect, "thumb": thumb, "name": prompt[:80]}


# ====================================================================== actions

def analyze(job, emit):
    p = params_of(job.get("params"))
    tl = Timeline.from_json(job.get("seq"))
    d = _dirs(job)
    emit.plan([("words", tr("broll.stage.words"), 0.3), ("plan", tr("broll.stage.plan"), 0.25),
               ("screen", tr("broll.stage.screen"), 0.25), ("source", tr("broll.stage.source"), 0.2)])
    warnings = []

    def warn(msg):
        if msg not in warnings:
            warnings.append(msg)
            emit.warn(msg)

    with emit.step("words"):
        words = tl.words_on_timeline(emit=emit)
        scope = tl.scope_ranges(p["scope"])
        words = [w for w in words if R.contains(scope, (w["t0"] + w["t1"]) / 2)]
        if not words:
            raise EngineError("NO_WORDS", tr("broll.err.noWords"), tr("broll.err.noWordsHint"))
    from ..ai.text import display_words, sentences
    dwords = display_words(words)
    sents = sentences(dwords)
    span = R.total(scope)
    n = max(1, int(round(span / 60.0 * p["density"])))
    spacing = _clamp(30.0 / p["density"], 3.0, 30.0)

    with emit.step("plan"):
        lib, idf = [], None
        if p["src_local"]:
            lib = scan_library(p["folder"], exclude=tl.media_paths("audio", True) + tl.media_paths("video"))
            idf = _idf(lib) if lib else None
            emit.log(f"B-roll folder: {len(lib)} files")
            if not lib:
                warn(tr("broll.warn.folderEmpty"))
        emit.progress(20)
        k = min(n * 2 + 2, n + 10, 50)
        picks, ai_src = None, "off"
        if p["use_ai"]:
            from ..ai import client as ai
            try:
                r = plan_ai(sents, k, lib if p["ai_rank"] else None, emit=emit)
                picks, ai_src = r["picks"], r["source"]
                if not picks:
                    warn(tr("broll.warn.aiNoMoments"))
            except ai.AIError as e:
                ai_src = "fallback"
                emit.log(f"AI broll plan: {type(e).__name__}: {e}")
                warn(tr("broll.warn.aiNotUsed", reason=ai_reason(e)))
        if not picks:
            picks = plan_rules(sents, lib, idf)
            ai_src = "rules" if ai_src == "off" else ai_src
        emit.progress(100)

    # candidate windows (top by score) -> screen activity
    lo_hi = lambda t: next(((a, b) for a, b in scope if a - 0.01 <= t <= b + 0.01), (0.0, tl.duration))  # noqa: E731
    cands = []
    for pk in sorted(picks, key=lambda x: -x["score"])[: max(3 * n + 3, 8)]:
        s = sents[pk["sid"]]
        lo, hi = lo_hi(s["start"])
        t0, t1 = slot_window(s, dwords, pk["kw"], p["max_dur"], lo, min(hi, tl.duration), tl.fps)
        if t1 - t0 < MIN_DUR - 1e-6:
            continue
        cands.append({**pk, "t0": t0, "t1": t1, "text": s["text"]})
    with emit.step("screen"):
        tracks = _main_tracks(tl)
        with ThreadPoolExecutor(max_workers=3) as ex:
            futs = [ex.submit(slot_activity, tl, c["t0"], c["t1"], tracks) for c in cands]
            for i, (c, f) in enumerate(zip(cands, futs)):
                c["act"] = f.result()
                c["place"], c["auto"], c["pnote"] = placement_for(p["placement"], c["act"])
                emit.progress(100.0 * (i + 1) / max(1, len(cands)))
        min_score = 15
        chosen, skipped = select_slots([c for c in cands if c["score"] >= min_score] or cands, n, spacing,
                                       p["placement"])
        emit.done_note(tr("broll.done.moments", n=len(chosen)))
        if not chosen and not skipped:
            raise EngineError("NO_SLOTS", tr("broll.err.noSlots"), tr("broll.err.noSlotsHint"))
        if not chosen:
            warn(tr("broll.warn.allBusy"))

    with emit.step("source"):
        key = pexels_key() if p["src_pexels"] else ""
        if p["src_pexels"] and not key:
            warn(tr("broll.warn.pexelsNoKey"))
        pexels_ok = bool(key)
        used, ai_left, items = set(), (p["ai_max"] if p["src_ai"] else 0), []
        allslots = [(c, True) for c in chosen] + [(c, False) for c in skipped]
        # best slots first, so the scarce sources (AI image budget, unused local files) go where they matter most
        for i, (c, on) in enumerate(sorted(allslots, key=lambda x: (not x[1], -x[0]["score"], x[0]["t0"]))):
            cl = []
            if lib:
                cl += _local_cands(c, lib, idf, d["thumbs"], used, c.get("lib", -1))
            if pexels_ok:
                query, locale = (c["q"], None) if c.get("q") else (c["kw"], "id-ID")
                try:
                    cl += pexels_cands(query, tl.width, tl.height, key, d["thumbs"], locale=locale,
                                       dur=c["t1"] - c["t0"])
                except PexelsError as e:
                    warn(str(e))
                    pexels_ok = pexels_ok and not e.fatal
            if p["src_ai"] and p["ai_max"] > 0:
                cl.append(_ai_cand(c["img"], p["style"], tl.width, tl.height, d["thumbs"]))
            pick = _choose_pick(cl, ai_left)
            note = c["pnote"]
            if pick < 0:
                on = False
                note = {"type": "warn", "text": tr("broll.note.noSource") if not cl else
                        tr("broll.note.aiLimit", max=p["ai_max"])}
            else:
                cp = cl[pick]
                if cp["src"] == "local":
                    used.add(cp["path"])
                if cp["src"] == "ai" and not cp.get("thumb") and on:
                    ai_left -= 1
            corner = choose_corner((c.get("act") or {}).get("grid"))
            ctx = _ctx_parts(c["text"], c["kw"])
            it = RV.item(c["t0"], c["t1"], "broll", on=on, conf=c["score"] / 100.0, label=c["kw"], ctx=ctx, note=note,
                         text=c["text"], kw=c["kw"], q=c.get("q", ""), alt=c.get("alt", ""), img=c.get("img", ""),
                         busy=(c.get("act") or {}).get("busy"), place=c["place"], auto=c["auto"], corner=corner,
                         pos=pip_geometry(corner, tl.width, tl.height), scale=PIP_SCALE, cands=cl, pick=pick,
                         src=cl[pick]["src"] if pick >= 0 else None, plan="rules" if c.get("rule") else "ai")
            items.append(it)
            emit.progress(100.0 * (i + 1) / max(1, len(allslots)))

    stats = {"n": len(items), "on": sum(1 for it in items if it["on"]),
             "full": sum(1 for it in items if it["on"] and it["place"] == "full"),
             "pip": sum(1 for it in items if it["on"] and it["place"] == "pip"),
             "skip": sum(1 for it in items if not it["on"]),
             "local": sum(1 for it in items if it.get("src") == "local"),
             "pexels": sum(1 for it in items if it.get("src") == "pexels"),
             "ai": sum(1 for it in items if it.get("src") == "ai"), "target": n, "candidates": len(cands),
             "library": len(lib), "planner": ai_src}
    doc = RV.new("broll", items, tl.duration, seq=tl, params={k2: v for k2, v in p.items() if k2 != "scope"},
                 stats=stats, canvas={"w": tl.width, "h": tl.height, "fps": tl.fps}, track=TRACK, planner=ai_src,
                 warnings=warnings)
    path = RV.path_for(d["root"].parent, "broll")
    old = read_json(path)
    if old and old.get("tool") == "broll":
        RV.carry_over(doc, old)
        _carry_picks(doc, old)
    RV.save(doc, path)
    return {"review": str(path), "summary": RV.summary(doc), "stats": stats, "planner": ai_src, "warnings": warnings}


def _cand_key(c):
    return c.get("path") or c.get("url") or c.get("prompt")


def _carry_picks(doc, old):
    """Keep the candidate the user picked in a previous run of the same slot."""
    prev = {it.get("id"): it for it in old.get("items", []) if it.get("touched")}
    for it in doc["items"]:
        o = prev.get(it["id"])
        if not o or not o.get("cands") or o.get("pick", -1) < 0:
            continue
        want = _cand_key(o["cands"][o["pick"]])
        for k, c in enumerate(it.get("cands") or []):
            if _cand_key(c) == want:
                it["pick"], it["src"] = k, c["src"]
        if o.get("place") in ("full", "pip"):
            it["place"] = o["place"]


def _doc_canvas(doc, job):
    cv = doc.get("canvas") or {}
    seq = job.get("seq") if isinstance(job.get("seq"), dict) else {}
    w = int(cv.get("w") or seq.get("width") or 1920)
    h = int(cv.get("h") or seq.get("height") or 1080)
    fps = float(cv.get("fps") or seq.get("fps") or 30.0)
    return w, h, fps


def materialize(cand, emit=None, ai_budget=None):
    """Local file path of a candidate's media: local path, downloaded Pexels mp4, or generated AI image.
    -> (path, kind 'video'|'image', generated_ai: bool)."""
    if cand["src"] == "local":
        p = Path(cand["path"])
        if not p.is_file():
            raise EngineError("NO_MEDIA", tr("broll.err.mediaMissing", name=p.name), str(p))
        return p, cand.get("kind") or ("image" if p.suffix.lower() in IMAGE_EXT else "video"), False
    if cand["src"] == "pexels":
        dest = cache_dir("pexels") / f"{cand['id']}_{cand.get('fw', 0)}.mp4"
        try:
            _download(cand["url"], dest, emit)
        except (urllib.error.URLError, OSError) as e:
            raise EngineError("PEXELS", tr("broll.err.pexelsDownload", reason=str(getattr(e, "reason", e))), "") from None
        return dest, "video", False
    if cand["src"] == "ai":
        from ..ai import client as ai
        if ai_budget is not None and ai_budget[0] <= 0 and not imagine_path(cand["prompt"], cand["aspect"]).is_file():
            raise EngineError("AI_LIMIT", tr("broll.err.aiLimit"), "")
        try:
            p, gen = imagine(cand["prompt"], cand["aspect"], emit)
        except ai.AIError as e:
            raise EngineError("AI_IMAGE", tr("broll.err.aiImage", err=str(e)), "") from None
        if gen and ai_budget is not None:
            ai_budget[0] -= 1
        return p, "image", gen
    raise EngineError("BAD_REVIEW", tr("broll.err.badSource", src=str(cand.get("src"))))


def fetch(job, emit):
    """Previews / new search for some rows. params: ids [..], query (optional new keyword)."""
    doc = RV.from_job(job)
    prm = job.get("params") or {}
    p = params_of(doc.get("params") or {})
    ids = set(prm.get("ids") or [])
    query = str(prm.get("query") or "").strip()
    rows = [it for it in doc["items"] if (it["id"] in ids if ids else it.get("on"))]
    if not rows:
        raise EngineError("NO_ITEMS", tr("broll.err.noRows"), "")
    w, h, _fps = _doc_canvas(doc, job)
    d = _dirs(job)
    emit.plan([("fetch", tr("broll.stage.fetchAgain") if query else tr("broll.stage.fetchPreview"), 1.0)])
    warnings, done = [], []
    with emit.step("fetch"):
        lib = scan_library(p["folder"]) if (query and p["src_local"]) else []
        idf = _idf(lib) if lib else None
        key = pexels_key() if (query and p["src_pexels"]) else ""
        for i, it in enumerate(rows):
            try:
                if query:
                    slot = {"kw": query, "q": query if query.isascii() else "", "alt": "", "text": query}
                    cl = _local_cands(slot, lib, idf, d["thumbs"], set()) if lib else []
                    if key:
                        try:
                            cl += pexels_cands(query, w, h, key, d["thumbs"], locale="id-ID" if looks_indonesian(query)
                                               else None, dur=it["t1"] - it["t0"])
                        except PexelsError as e:
                            warnings.append(str(e))
                    if p["src_ai"]:
                        cl.append(_ai_cand(f"a scene showing {query}", p["style"], w, h, d["thumbs"]))
                    old_ai = [c for c in it.get("cands") or [] if c["src"] == "ai"]
                    if not any(c["src"] == "ai" for c in cl):
                        cl += old_ai
                    if not cl:
                        warnings.append(tr("broll.warn.noResults", q=query))
                        continue
                    it.update(cands=cl, pick=0, src=cl[0]["src"], kw=query, label=query, touched=True)
                    it["ctx"] = _ctx_parts(it.get("text", ""), query)
                else:
                    c = (it.get("cands") or [])[it.get("pick", -1)] if it.get("pick", -1) >= 0 else None
                    if c is None:
                        continue
                    if c["src"] == "ai" and not c.get("thumb"):
                        path, _kind, _gen = materialize(c, emit)
                        c["thumb"] = ai_thumb(path, d["thumbs"])
                    elif c["src"] == "local" and not c.get("thumb"):
                        c["thumb"] = thumb_for_file(c["path"], c.get("kind", "video"), d["thumbs"])
                done.append(it["id"])
            except EngineError as e:
                warnings.append(e.msg)
            emit.progress(100.0 * (i + 1) / len(rows))
    for msg in warnings:
        emit.warn(msg)
    RV.save(doc, job["review"])
    return {"review": job["review"], "done": done, "warnings": warnings, "summary": RV.summary(doc)}


def apply(job, emit):
    """Pre-render every checked row -> plan {"kind": "broll"} (host: bac_broll_prepare + bac_broll_place)."""
    doc = RV.from_job(job)
    p = params_of(doc.get("params") or {})
    rows = [it for it in RV.selected(doc) if it.get("pick", -1) >= 0 and it.get("cands")]
    if not rows:
        raise EngineError("NO_ITEMS", tr("broll.err.noneSelected"), tr("broll.err.noneSelectedHint"))
    w, h, fps = _doc_canvas(doc, job)
    w, h = even(w), even(h)
    ofps = render_fps(fps)
    d = _dirs(job)
    ensure_free(len(rows) * 15 * 1024 ** 2, d["render"])
    emit.plan([("source", tr("broll.stage.prepare"), 0.45), ("render", tr("broll.stage.render"), 0.55)])
    failed, ready = [], []
    budget = [max(p["ai_max"], 1) if p["src_ai"] else 0]
    with emit.step("source"):
        for i, it in enumerate(rows):
            c = it["cands"][it["pick"]]
            try:
                path, kind, _gen = materialize(c, emit, budget)
                ready.append((it, c, path, kind))
            except EngineError as e:
                failed.append({"id": it["id"], "t0": it["t0"], "msg": e.msg})
                emit.warn(tr("broll.warn.skipped", tc=_tc(it["t0"]), msg=e.msg))
            emit.progress(100.0 * (i + 1) / len(rows))
        prune_cache("pexels")
    items, credits = [], []
    with emit.step("render"):
        for i, (it, c, path, kind) in enumerate(ready):
            dur = max(MIN_DUR * 0.5, float(it["t1"]) - float(it["t0"]))
            pip = it.get("place") == "pip"
            motion = int(round(float(it["t0"]) * 10)) % 3
            src_dur = None
            try:
                if kind == "video":
                    src_dur = M.probe(path)["duration"]
                name = f"{it['id']}_{data_hash([str(path), file_hash(path), dur, w, h, ofps, pip, motion])[:10]}.mp4"
                out = render_clip(path, kind, d["render"] / name, dur, w, h, ofps, pip=pip, motion=motion,
                                  src_dur=src_dur)
            except EngineError as e:
                failed.append({"id": it["id"], "t0": it["t0"], "msg": e.msg})
                emit.warn(tr("broll.warn.renderFail", tc=_tc(it["t0"]), msg=e.msg))
                continue
            it["rendered"] = str(out)
            items.append({"id": it["id"], "path": str(out), "t0": float(it["t0"]), "t1": float(it["t1"]),
                          "place": "pip" if pip else "full", "scale": it.get("scale", PIP_SCALE),
                          "pos": it.get("pos") or pip_geometry("tr", w, h),
                          "fade": FADE if p["transition"] == "fade" else 0, "label": it.get("kw", ""),
                          "src": c["src"]})
            if c["src"] == "pexels":
                credits.append(tr("broll.credit.pexels", tc=_tc(it["t0"]), user=c.get("user") or tr("broll.credit.contributor"),
                                  page=c.get("page") or ""))
            elif c["src"] == "ai":
                credits.append(tr("broll.credit.ai", tc=_tc(it["t0"]), name=c.get("name", "")))
            emit.progress(100.0 * (i + 1) / max(1, len(ready)))
    if not items:
        raise EngineError("BROLL_NONE", tr("broll.err.noneReady"), failed[0]["msg"] if failed else tr("broll.err.noneReadyHint"))
    cred_path = None
    if credits:
        cred_path = d["root"] / "Kredit_Broll.txt"
        cred_path.write_text(tr("broll.credit.head") + "\n\n" + "\n".join(credits) + "\n", encoding="utf-8")
    RV.save(doc, job["review"])
    seq_name = (doc.get("seq") or {}).get("name") or (job.get("seq") or {}).get("name") or "Sequence"
    nf = sum(1 for x in items if x["place"] == "full")
    plan = {"kind": "broll", "track": TRACK, "bin": TRACK, "name": f"{seq_name} (Klipora)", "width": w, "height": h,
            "fps": fps, "render_fps": ofps, "transition": p["transition"], "duration": doc.get("duration"),
            "items": items}
    return {"plan": plan, "failed": failed, "credits": str(cred_path) if cred_path else None,
            "credits_text": "\n".join(credits), "folder": str(d["render"]),
            "summary": tr("broll.summary", n=len(items), full=nf, pip=len(items) - nf)}


def _tc(t):
    t = float(t)
    return f"{int(t // 60)}:{int(t % 60):02d}"


def library(job, emit):
    """Count the files of a local B-roll folder (params.folder) -> {n, videos, images, sample}."""
    folder = str((job.get("params") or {}).get("folder") or "")
    lib = scan_library(folder)
    vids = sum(1 for f in lib if f["kind"] == "video")
    return {"n": len(lib), "videos": vids, "images": len(lib) - vids, "sample": [f["rel"] for f in lib[:8]]}


ACTIONS = {"analyze": analyze, "fetch": fetch, "apply": apply, "library": library}
