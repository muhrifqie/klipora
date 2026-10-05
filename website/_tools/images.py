"""Builds website/assets/img/*.webp from harness screenshots (panel shots + template tiles only).

    python website/_tools/images.py [EN_SHOTS] [ID_SHOTS]     (default: docs/shots for both)

Panel shots come from tools/ui_test.mjs --shots <dir> with the neutral fixtures ("Tutorial - Episode 12"):
EN_SHOTS = a run with --locale en_US (panel-<x>.webp, used by the English pages and the README),
ID_SHOTS = a run with --locale id_ID (panel-<x>-id.webp, used by the Indonesian pages)."""
import sys
from pathlib import Path
from PIL import Image
SH = Path(__file__).resolve().parents[2] / 'docs' / 'shots'
EN_SH = Path(sys.argv[1]) if len(sys.argv) > 1 else SH
ID_SH = Path(sys.argv[2]) if len(sys.argv) > 2 else SH
OUT = Path(__file__).resolve().parents[1] / 'assets' / 'img'
OUT.mkdir(parents=True, exist_ok=True)

PANEL = {  # reviewed: neutral fixture names only; no personal frames, transcripts, paths or desktop content
    'panel-home': 'smoke_home.png', 'panel-silence': 'build_silence.png', 'panel-fillers': 'build_fillers.png',
    'panel-profanity': 'build_profanity.png', 'panel-zoom': 'build_zoom.png',
    'panel-voice': 'voice_main.png', 'panel-podcast': 'build_podcast.png',
}
for suffix, folder in (('', EN_SH), ('-id', ID_SH)):
    for name, src in PANEL.items():
        f = folder / src
        if suffix == '' and name == 'panel-home' and not f.is_file():
            f = folder / 'en_home.png'          # tools/ui_tests/smoke_en.mjs
        im = Image.open(f).convert('RGB')
        im.save(OUT / f'{name}{suffix}.webp', 'WEBP', quality=90, method=6)
if '--panels-only' in sys.argv:
    raise SystemExit(0)

# 9:16 template contact sheet grid (detected): 9 cols x 6 rows, tile 200x356. Crop to 322 px tall to drop the
# Windows taskbar strip and cursor at the bottom of the source frame.
COLS = [8, 216, 424, 632, 840, 1048, 1256, 1464, 1672]
ROWS = [8, 398, 788, 1178, 1568, 1958]
NAMES = ['Ali Abdaal', 'Bayangan Dramatis', 'Berita Terkini', 'Bingkai Tipis', 'Bold Pop', 'Boxed', 'Catatan Stabilo',
         'Esports', 'Flash Sale', 'Fokus Belajar', 'Garis Bawah', 'Goyang Hijau', 'Gradasi Api', 'Hijau Viral',
         'Hook Tengah', 'Hormozi Kuning', 'Huruf Kecil', 'Jualan', 'Judul Emas', 'Kabar Kilat', 'Karaoke',
         'Kartu Podcast', 'Komik', 'Komik Ledak', 'Kotak Muncul', 'Kuning 3D', 'Langkah Klik', 'Layar Lebar',
         'Minimal Putih', 'Mr Beast', 'Neon', 'Neon Gamer', 'Obrolan Santai', 'Panel Terang', 'Papan Kapur',
         'Pastel Lembut', 'Pita Diskon', 'Podcast', 'Promo Spanduk', 'Satu Kata', 'Senja', 'Sinematik',
         'Stabilo Tutorial', 'Stiker Lucu', 'Stiker Viral', 'Subtitle Klasik', 'Teks Gelap', 'Tulisan Tangan',
         'Tutorial Bersih', 'Tutorial Sorot', 'Typewriter']
PICK = ['Kuning 3D', 'Neon', 'Flash Sale', 'Esports', 'Pita Diskon', 'Stiker Viral', 'Goyang Hijau', 'Bold Pop',
        'Judul Emas', 'Komik Ledak', 'Promo Spanduk', 'Neon Gamer', 'Papan Kapur', 'Hijau Viral', 'Gradasi Api',
        'Catatan Stabilo']
sheet = Image.open(SH / 'v3_templates_9x16.png').convert('RGB')
for n in PICK:
    i = NAMES.index(n)
    x, y = COLS[i % 9], ROWS[i // 9]
    tile = sheet.crop((x, y + 40, x + 200, y + 322))   # also trims the empty top
    slug = n.lower().replace(' ', '-')
    tile.save(OUT / f'tpl-{slug}.webp', 'WEBP', quality=88, method=6)

for p in sorted(OUT.glob('*.webp')):
    print(p.name, Image.open(p).size, p.stat().st_size // 1024, 'KB')
