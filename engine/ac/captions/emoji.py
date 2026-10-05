"""Colour emoji for libass captions (port of proto/caption_render/emoji.py, verified).

libass draws emoji fonts as monochrome outlines (no COLR/CBDT). colr_ass_layers() converts a Segoe UI Emoji
COLR v0 glyph (flat-colour layers) into stacked ASS vector drawings (\\p1): pure libass, so emoji animate and
scale with the caption. Every layer starts with 'm 0 0 m upem upem' so all layers share one bbox and stay
aligned under \\an5. Emoji sequences (skin tones, ZWJ, VS16) resolve through HarfBuzz GSUB. Country flags are
not in Segoe UI Emoji (skipped). Cost: first font load ~250 ms, then sub-ms per emoji (cached).
"""
from __future__ import annotations

import os
from functools import lru_cache

SEGOE = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts", "seguiemj.ttf")

# Indonesian keyword -> emoji (templates with emoji.enabled; also the "auto emoji" suggestion list)
EMOJI_ID = {
    "uang": "\U0001F4B0", "duit": "\U0001F4B0", "cuan": "\U0001F4B0", "untung": "\U0001F4B0", "gaji": "\U0001F4B0",
    "mantap": "\U0001F525", "viral": "\U0001F525", "keren": "\U0001F60E", "gratis": "\U0001F381",
    "daftar": "\U0001F4DD", "email": "\U0001F4E7", "password": "\U0001F511", "reseller": "\U0001F91D",
    "jualan": "\U0001F6D2", "jual": "\U0001F6D2", "belanja": "\U0001F6D2", "setting": "\u2699\ufe0f",
    "mulai": "\U0001F680", "tutorial": "\U0001F4DA", "cloudflare": "\u2601\ufe0f", "semoga": "\U0001F64F",
    "kelihatan": "\U0001F440", "lihat": "\U0001F440", "layar": "\U0001F5A5\ufe0f", "akun": "\U0001F464",
    "berhasil": "\u2705", "sukses": "\u2705", "salah": "\u274C", "gagal": "\u274C", "penting": "\u2757",
    "cepat": "\u26A1", "rahasia": "\U0001F92B", "toko": "\U0001F3EA",
    "hp": "\U0001F4F1", "handphone": "\U0001F4F1", "whatsapp": "\U0001F4AC", "pesan": "\U0001F4AC",
    "kirim": "\U0001F4E4", "paket": "\U0001F4E6", "ongkir": "\U0001F69A", "promo": "\U0001F3F7\ufe0f",
    "diskon": "\U0001F3F7\ufe0f", "waktu": "\u23F0", "ide": "\U0001F4A1", "tips": "\U0001F4A1",
    "bahaya": "\u26A0\ufe0f", "hati-hati": "\u26A0\ufe0f", "love": "\u2764\ufe0f", "suka": "\u2764\ufe0f",
    "senang": "\U0001F604", "sedih": "\U0001F622", "kaget": "\U0001F631", "lucu": "\U0001F602",
    "target": "\U0001F3AF", "naik": "\U0001F4C8", "turun": "\U0001F4C9", "data": "\U0001F4CA",
    "internet": "\U0001F310", "website": "\U0001F310", "kode": "\U0001F4BB", "laptop": "\U0001F4BB",
    "video": "\U0001F3AC", "foto": "\U0001F4F8", "musik": "\U0001F3B5", "makan": "\U0001F37D\ufe0f",
    "kopi": "\u2615", "rumah": "\U0001F3E0", "mobil": "\U0001F697", "selesai": "\U0001F3C1",
}


@lru_cache(maxsize=1)
def _segoe():
    from fontTools.ttLib import TTFont
    f = TTFont(SEGOE, lazy=True)
    t = f["COLR"].table
    v0 = {r.BaseGlyph: r for r in t.BaseGlyphRecordArray.BaseGlyphRecord}
    layers = t.LayerRecordArray.LayerRecord
    return f, f.getGlyphSet(), f.getBestCmap(), v0, layers, f["CPAL"].palettes[0], f["head"].unitsPerEm, \
        f["OS/2"].usWinAscent


@lru_cache(maxsize=1)
def _hb():
    import uharfbuzz as hb
    return hb, hb.Font(hb.Face(hb.Blob.from_file_path(SEGOE)))


def available():
    return os.path.isfile(SEGOE)


def emoji_glyph(seq):
    """Glyph name for an emoji sequence (skin tones, ZWJ) via HarfBuzz GSUB ligatures."""
    f, gs, cmap, v0, layers, pal, upem, asc = _segoe()
    hb, font = _hb()
    buf = hb.Buffer()
    buf.add_str(seq)
    buf.guess_segment_properties()
    hb.shape(font, buf, {})
    order = f.getGlyphOrder()
    names = [order[i.codepoint] for i in buf.glyph_infos if order[i.codepoint] in v0]
    return names[0] if names else cmap.get(ord(seq[0]))


def _pen_class():
    from fontTools.pens.basePen import BasePen

    class AssPen(BasePen):
        """Glyph outline -> ASS drawing commands (y flipped, scaled)."""

        def __init__(self, glyphset, scale, ymax):
            super().__init__(glyphset)
            self.s, self.ymax, self.cmds = scale, ymax, []

        def _p(self, pt):
            return f"{pt[0] * self.s:.1f} {(self.ymax - pt[1]) * self.s:.1f}"

        def _moveTo(self, pt):
            self.cmds.append("m " + self._p(pt))

        def _lineTo(self, pt):
            self.cmds.append("l " + self._p(pt))

        def _curveToOne(self, p1, p2, p3):
            self.cmds.append(f"b {self._p(p1)} {self._p(p2)} {self._p(p3)}")

        def _qCurveToOne(self, p1, p2):  # quadratic -> cubic
            p0 = self._getCurrentPoint()
            c1 = (p0[0] + 2 / 3 * (p1[0] - p0[0]), p0[1] + 2 / 3 * (p1[1] - p0[1]))
            c2 = (p2[0] + 2 / 3 * (p1[0] - p2[0]), p2[1] + 2 / 3 * (p1[1] - p2[1]))
            self._curveToOne(c1, c2, p2)
    return AssPen


@lru_cache(maxsize=512)
def colr_ass_layers(ch, size):
    """[(ass colour '&HBBGGRR&', alpha '&HAA&', drawing)] for one emoji (sequence), em box = size px; None when
    the emoji is not in Segoe UI Emoji (flags) or the font is missing."""
    if not ch or not available():
        return None
    f, gs, cmap, v0, layers, pal, upem, asc = _segoe()
    g = emoji_glyph(ch)
    if g not in v0:
        return None
    rec = v0[g]
    s = size / upem
    anchor = f"m 0 0 m {upem * s:.1f} {upem * s:.1f} "
    pen_cls = _pen_class()
    out = []
    for lr in layers[rec.FirstLayerIndex:rec.FirstLayerIndex + rec.NumLayers]:
        pen = pen_cls(gs, s, asc - (asc - upem) / 2)
        gs[lr.LayerGlyph].draw(pen)
        if lr.PaletteIndex == 0xFFFF:
            col, a = "&HFFFFFF&", "&H00&"
        else:
            c = pal[lr.PaletteIndex]
            col, a = f"&H{c.blue:02X}{c.green:02X}{c.red:02X}&", f"&H{255 - c.alpha:02X}&"
        out.append((col, a, anchor + " ".join(pen.cmds)))
    return tuple(out)


def is_emoji(ch):
    o = ord(ch[0])
    return o >= 0x1F000 or 0x2600 <= o <= 0x27BF or o in (0x2B50, 0x2B55, 0x203C, 0x2049)


def suggest(word_text, extra=None):
    """Emoji for an Indonesian keyword (extra map first), else None."""
    from .glossary import norm
    k = norm(word_text)
    if extra:
        hit = {norm(a): b for a, b in extra.items()}.get(k)
        if hit:
            return hit
    return EMOJI_ID.get(k)
