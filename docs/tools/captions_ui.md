# Auto Caption (`captions`): editor UI + Premiere placement

The panel half of Auto Caption. Engine half: `docs/tools/captions.md` and the full engine API in
`docs/CAPTIONS_API.md` (`engine/ac/captions/`). Architecture rules: `docs/SPEC.md`; panel framework:
`docs/PANEL_API.md`. Screenshots (gitignored `--shots` folder): `build_captions_*.png`.

## Files

| File | What |
|---|---|
| `panel/js/tools/captions.js` | Core (`AC.cap`): registration, state per sequence, document load/restore, edit queue + undo/redo, busy gate, layout (sticky preview + 5 tabs), setup screen, build / rebuild, dock, keys |
| `panel/js/tools/captions_preview.js` | Sticky preview: engine `preview` through the worker (latest wins), playhead follow, zoom to caption, safe-zone overlay, drag-to-position handle, 3 dtk loop (`burn`), prev/next caption, undo/redo |
| `panel/js/tools/captions_text.js` | Teks tab: page cards + word chips, word popover, multi-select bar, inline edit, find & replace, Kamus, text rules + emoji, AI cleanup diff, orphan / low-confidence suggestions |
| `panel/js/tools/captions_style.js` | Template (Gaya Saya library, Terakhir dipakai, groups, search, sprite thumbnails, Acak / Gabung gaya), Gaya ("Gaya Pro" generated from the engine schema), Animasi classic pickers; "Simpan gaya" sheet; last used look writer (`AC.cap.lib`) |
| `panel/js/tools/captions_export.js` | Ekspor tab, "Terapkan ke timeline" (overlay / SRT / MOGRT), result card, linear-colour toggle |
| `panel/css/tools/captions.css` | All editor styles (mockup section 8 ported, every class prefixed `cap-`) |
| `panel/host/34_captions.jsx` | ES3 host functions `bac_captions_*` (below) |
| `tools/ui_tests/captions.mjs` | Headless test with the REAL engine (cli.py + worker.py) |

Core change (minimal, listed in the report): `panel/index.html` loads `captions_preview.js`, `captions_text.js`,
`captions_style.js`, `captions_export.js` right after `captions.js`.

## Flow

```
open tool -> AC.seq.peek() -> workdir = settings.workdir(seq name) -> <workdir>\captions.json ?
   no  -> Setup: template awal, text rules (filler, angka, sensor), Kamus  -> [Buat caption]
          = ctx.run engine `doc` (own process; Transkripsi from cache or GPU with the engine's lock + progress)
   yes -> Editor (restored), background: `doc {check_only}` (stale?) + bac_captions_status (what is on the timeline)
Editor:
   every edit -> AC.cap.op(ops) -> 120 ms batch -> worker `doc {ops, seq:null}` -> re-read captions.json
              -> UI re-render (keyed) -> worker `preview {t}` -> PNG on the stage
   Terapkan  -> worker `render` (versioned .mov, band, chunk cache) -> bac_captions_applyOverlay
                (relink in place when plan.relink, else place/replace on "Klipora Captions") -> result card
   edit after apply -> dock "Perbarui di timeline" -> render v(n+1) -> relink in place
   timeline cut later -> banner "Timeline berubah" -> [Perbarui caption] = engine `doc` with seq (keeps all edits)
```

- **One document per sequence.** The engine writes `<workdir>\captions.json`; reopening the tool (or the panel)
  restores it, including the last tab (`ctx.state.tab`) and what is on the timeline (read from Premiere).
- **Serial edit queue.** All document changes go through `AC.cap.op` (batched 120 ms, consecutive doc-level
  style edits coalesced) and the persistent worker, which is FIFO with previews and renders, so nothing races.
  Long jobs (render, autoposition, AI, rebuild) take a busy gate; edits made meanwhile wait and land afterwards.
  `AC.cap.idle()` flushes and waits (used before every long job, so the gate never deadlocks).
- **Doc-level style** is sent as the full new `doc.style` (`doc_style`, `replace: true`) so explicit nulls survive
  (e.g. the "Garis bawah" preset sets `highlight.color: null`); the engine's merge patch would delete them.
- **Undo / redo** (Ctrl Z / Ctrl Y, also in the preview bar): a snapshot of the user layer (template, style, params,
  page_over, ranges, word edit/style) before each batch; restore = write those fields into `captions.json` and
  run `doc {ops: []}` (CAPTIONS_API section 5 allows editing the user fields directly). 60 steps per sequence.

## Screens

**Setup** (no document): hero, Template awal, Sembunyikan eh/em/hmm, Rapikan angka dan Rupiah, Kata kasar (censor
mode), Kamus (terms for this video). The source card (scope Seluruh sequence / In/Out / Clip terpilih) is visible here
and passed as `params.scope`. Start look, in this order: (1) the **last used look** for the sequence's aspect class
(engine `last_style get`, CAPTIONS_API 13.2: vertical / horizontal / square kept apart): a card with its thumbnail,
badge "Gaya terakhir dipakai", name, source video and WIB time, plus "Pakai template asli" (back to the template
select; "Pakai gaya terakhir dipakai" returns); its text rules (filler, angka, sensor, Kamus) prefill the switches once
per sequence; Buat caption = engine `doc {template: ops[0].id, ops: [ops[1]]}`; (2) settings `captionTemplate`
("Jadikan default"); (3) the aspect default (Tutorial Bersih 16:9, Hormozi Kuning 9:16). A sequence that already has
`captions.json` always keeps its own look.

**Preview** (sticky on top; at >= 600 px the left 44 % column): engine PNG over the real frame at the playhead
(Premiere has no playhead event: the host is polled every 500 ms while the editor is on screen; a preview is made
when the playhead stops) or at the page / word picked in the editor (also seeks Premiere). Wide frames
(2292x960 in a 360 px panel) **zoom to the caption** automatically (zoom from the template's line length so the
longest line still fits; toggle in the bar). Overlays: platform safe zone (dashed) and the page position handle
(drag or Up/Down, Shift = 5 %; double-click = back to the template height) -> op `page_pos`. Bar: Putar 3 dtk
(engine `burn` MP4 in a `<video>`), previous / next caption, "3 / 18 0:04,02", undo, redo, zoom, safe zone,
follow playhead.

**Template** (top to bottom): the **Tiru gaya dari gambar** row (captions_brand.js; collapsed to one line with Tempel by
default, open state in `AC.store` key `cap.sty.open`, opens by itself on paste / run / result), search (name, description, group, font, effects) + **Acak gaya** (`template_random`,
random seed, applied as one undoable step; toast "Acak #0042" with Simpan) + **Gabung gaya** (panel: template A and B
selects, A/B per part Huruf / Warna dan efek / Latar / Kata aktif / Animasi / Tata letak -> `template_mix`, ops through
the edit queue, readability fixes counted in the toast). Badge bar "Gaya terakhir dipakai" while the doc still has the
look it was built with (stored per workdir in `AC.store` key `cap.fromLast`) with **Pakai template asli** (= op
`template` with the same id: own style cleared; Ctrl Z brings it back). **Gaya Saya** (engine `library`,
CAPTIONS_API 13.1): user templates, favourites first then newest, card = engine thumbnail + colour dots + star +
"Hari ini, 5 Okt"; menu (dots button, keyboard: arrows, Esc): Ganti nama (inline, Enter / Esc), Duplikat, Jadikan
favorit / Hapus dari favorit, Ekspor (`window.cep.fs.showSaveDialogEx` -> `library export`; outside CEP:
`<workRoot>\Gaya caption\<nama>.json`; font warnings shown under the grid), Hapus (second click "Yakin? Klik lagi";
a style the doc still uses is first copied into the doc as `doc_style` on the default template so the look does not
change). Section action **Impor gaya** (`showOpenDialogEx`, else a file input whose text is written to `%TEMP%` and
imported; names de-duplicated). **Terakhir dipakai**: the last used look of this aspect class as a card (click = its
`ops`). **Template**: filter chips Semua, Cocok (aspect of this sequence), then the engine groups with counts (Shorts
Viral, Tutorial, Podcast, Jualan/Promo, Sinematik, Edukasi, Minimal, Fun/Komik, Gaming, Berita, Aesthetic); 2/3
column grid of static engine thumbnails (`gallery`; "Pakai frame video ini" re-renders them over the frame at the
playhead); on hover / focus the card plays its animated sprite strip (`gallery {anim}`: cached strips through the
worker at once, missing ones rendered once in a background engine process so previews never queue behind them; CSS
`steps(frames, jump-none)` over `background-position`, image set on first hover; none with reduced motion; requests
are incremental: ids without a strip that were never asked for, on every `templates` / `library` event, so cards saved,
imported or duplicated later animate too). The Gaya Saya grid is not repainted while a card menu is open (the
repaint runs when the menu closes), and its paint key includes whether the list has loaded, so an empty library shows
"Belum ada" instead of staying on "Memuat Gaya Saya...". Keyboard: each grid (Template, Gaya Saya) is one Tab stop (roving tabindex:
focused, else checked, else first card); arrows move by the laid-out column count, Home / End jump; Enter / Space pick. Ember
ring on the current one, Default badge. Picking = op `template` (manual style is reset, toast with "Batalkan").
"Jadikan default" sets settings `captionTemplate`.

**Teks**: one card per caption page (timecode -> seek, duration, tags Cepat / Posisi / n ragu, join-to-previous
button). Hidden words (fillers, user-hidden) are slotted back by time as dashed strikethrough chips so they can
be shown again. Chip states: selected, coloured (swatch), pill, emoji, bold, italic, bigger, hidden,
low confidence (wavy warn underline, p < 0,5), edited (green bar), AI-edited (ember bar), AI suggestion (dashed
ember bar), find hit.
- Click = select + **word popover**: text, time + confidence, AI suggestion (Terima / Tolak), Warna, Sorot (pill),
  Ukuran 100/125/150 %, Tebal / Miring, Emoji, Sembunyikan / Tampilkan, Sensor, Pisah di sini (page break), Baris
  baru (line break) or Hapus pemisah, Reset, "Terapkan ke semua "kata" (n)". Below 360 px it is a bottom sheet.
- Double-click / Enter / F2 = inline edit (Enter saves, Esc cancels; empty = hide). Shift+click = range,
  Ctrl+click = add/remove -> selection bar (Atur gaya, Tebal, Sorot, Sembunyikan, Batal pilih).
- Keys (Teks tab, not while typing): Left/Right or Tab move the selection, Del hides, H toggles emphasis,
  Ctrl F find & replace (whole words, optional case match, Enter jumps to the next hit, "Ganti semua" = op replace).
- Suggestion cards: AI diff (n saran, Terima semua / Tinjau satu per satu = the shared Tinjau list ->
  engine `cleanup {mode: apply}` / Tolak semua), one-word captions (Gabung semua = op break join), low-confidence
  words (Lihat berikutnya), Kamus corrections.
- Kamus: terms for this video (`doc.params.glossary`) and for all videos (settings `glossary`); a change re-runs
  the deterministic rules on the cached transcript (engine `doc` with seq, `transcribe: false`), edits kept.
- Aturan teks dan emoji: hide fillers, numbers, censor mode (op `params`), Emoji otomatis sedikit / normal /
  banyak, Hapus emoji.

**Gaya ("Gaya Pro")**: generated from the engine metadata (`options.schema` + `options.groups`, CAPTIONS_API 6.5),
so every Pro key is editable without UI code per key. Top: **Efek cepat** chips (`options.effect_presets`). Then one
collapsible group per engine group except Animasi (Huruf, Warna teks, Garis tepi, Bayangan dan 3D, Cahaya, Latar,
Kata aktif, Posisi dan kemiringan; open state per viewer, Huruf / Warna teks / Latar / Posisi open by default), tag
"Diubah" when doc.style touches the group, **Reset <grup>** at its end (deletes only that group's keys). Controls by
field type: number/int = slider with unit / percent ("Tanpa" at 0 for strokes and shadows; null shows the effective
fallback), bool = switch, color = swatches + custom picker (alpha suffix kept, "none" when nullable), colors = stop
editor (add / remove within min/max items, gradient bar), enum = segmented (<= 4 short options) or select (nullable
adds "Ikuti template"), object = switch that writes `default_on` / explicit null, sub-fields indented and shown by
`show_if`. `font.case` also writes `uppercase`. Special pickers: **font picker** (button in the current font + Bawaan
/ Windows badge; panel with search, Semua / Bawaan / Windows, 7 category chips, rows "Halo Teman 123" rendered in the
real face: bundled files are loaded with `@font-face` from `engine/ac/captions/fonts`, installed fonts by name; rows use
`content-visibility` so only visible faces load; picking keeps the visual size with `size * cap_old / cap_new`; Esc closes it and ArrowDown from the search walks
the rows (Up / Down / Home / End), focus returns to the font button after a pick or Esc),
**live sample** (current caption text in the font, fill, outline, case, italic, skew), **Ketebalan** for multi-weight
designs; **shape tiles** for Latar (`options.box_shapes`: Tanpa, Per baris, Bulat, Panel, Per kata, Stabilo, Spanduk,
Pita, Bingkai, Stiker, drawn in CSS); **active-word looks** (`options.highlight_presets_all`) on top of Kata aktif;
**3x3 position grid** + **Posisi otomatis** (engine `autoposition`, off = `{clear: true}`) in Posisi. Every change =
`doc_style` with the full doc.style (explicit nulls kept), batched 120 ms, undoable, then a new preview.
"Kembalikan ke template" clears all.

**Animasi**: tile pickers with looping CSS demos (hover / focus / selected): Masuk (15), Kata aktif (9 engine
highlight presets), Kata muncul (reveal mode only, 11), Keluar (8). Durasi masuk / keluar / tiap kata, Membesar
kata aktif. The real result: Putar 3 dtk in the preview bar.

**Ekspor**: Overlay video transparan (Disarankan) / Caption Premiere (SRT, basic style; old caption tracks cannot
be removed by script, the UI says so) / MOGRT bisa diedit (short videos: disabled above 300 captions, import
estimate 0,26 dtk per clip). Overlay options: Simpan juga file .srt, **Samakan warna dengan pratinjau** (turns
`compositeLinearColor` off for this sequence only when the user flips it, with the explanation that it affects all
blending in the sequence), Pengaturan render (QuickTime Animation / PNG-MOV / ProRes 4444; Pita vs Satu frame
penuh = `band: off` fallback). Simpan gaya (opens the same sheet as the dock). Di timeline: what our tracks hold (version, up to date or not),
Buka folder render, Hapus dari timeline (two-step).

**Dock**: left **Simpan gaya** (star; icon only below 360 px; the primary hides its Ctrl Enter hint below 460 px)
opens a sheet above the dock (name prefilled "<template> saya", Enter saves, Esc closes): the card appears in Gaya
Saya at once as a placeholder and is replaced by the engine row (`library save {from_doc}`), toast with "Lihat".
Primary: "Terapkan N caption" / "Sudah di timeline (vN)" / "Perbarui di timeline" / "Buat track caption (N)" /
"Taruh N MOGRT". Progress pane = one task (Render overlay 82 %, Taruh di timeline 13 %, Tulis SRT 5 %) with cancel.
**Result card**: title (ditaruh / diperbarui), render time and re-rendered chunks, Caption / Versi / Ukuran, file
paths, linear-colour hint with "Samakan warna", Buka folder, Hapus hasil (clears our track), "Kembali ke editor".

Widths: 380 default; >= 600 split (preview left, sticky top 0); < 380 tab icons hidden; < 360 popover = bottom
sheet with scrim; < 340 timecode in the bar hidden; no horizontal overflow at 300 / 340 / 380 / 640 / 660 (tested).

**Last used look (writer)**: `captions_style.js` watches `cap.bus 'doc'`: when template, doc.style or the text rules
change in the open document (not when a document is merely opened), or a new overlay render version appears, it sends
`last_style set {template, style, params, w, h, seq_name, reason}` through the worker, debounced 1,5 s.

## Host functions (`panel/host/34_captions.jsx`, ES3)

| Function | Returns | Notes |
|---|---|---|
| `bac_captions_player(seqId)` | `{id, t}` | playhead seconds (polled, 500 ms) |
| `bac_captions_status(seqId)` | `{hasTrack, track, clips, path, start, end, ours, locked, linear, editable: {hasTrack, track, clips}}` | `path` = media of the first clip on "Klipora Captions" (old "AutoCut Captions" tracks are still found); `ours` = matches `_captions_v<n>.mov` |
| `bac_captions_applyOverlay(path, xNorm, yNorm, relink, seqId)` | `{mode: 'relink'|'place', track, start, end, path}` | `.mov` + exists check; locked track -> ERR; relink via `gxRelinkOverlay` (+ item/clip names), else `gxPlaceOverlay` into bin "Klipora Captions" (clears only our track). Opens the target sequence first when it is not the active one (QE track creation needs it) |
| `bac_captions_applySrt(path, seqId)` | `{ok, path}` | `.srt` + exists check, `gxCaptionTrackFromSrt` |
| `bac_captions_mogrtBegin(trackName, seqId)` | `{track, cleared}` | ensure + clear "Klipora Captions (Editable)" (old "AutoCut Captions (Editable)" recognised) |
| `bac_captions_mogrtItems(items, trackIdx, seqId)` | `{n, failed}` | per item `gxInsertMogrt`, `gxPopIn({pivot: anchor})`, Motion Position = `position`; chunks of 20 |
| `bac_captions_clear(seqId, which)` | `{ok, n}` | `'overlay' | 'editable' | 'all'`; skips locked tracks |
| `bac_captions_setLinear(on, seqId)` | `{ok, linear}` | only from the user's switch / button |

## Language (Indonesian / English)

Klipora's UI follows Premiere's language (Settings > Bahasa tampilan can force Bahasa Indonesia or English) and
switches live: the editor is rendered again and the tabs build themselves from scratch (`cap.text.create`,
`cap.styleUI.template / gaya / animasiClassic` reset their painted keys, the "Simpan gaya" sheet is rebuilt in the
new language the next time it opens). Locale keys: `cap.text.*` (Teks tab: word editor, popover, selection bar, AI
cleanup diff and Tinjau list, find & replace, Kamus, text rules / sensor / emoji), `cap.style.*` (Gaya tab, font
picker, position grid, auto position, save sheet, classic Animasi pickers), `cap.gallery.*` (Template tab: filter
chips Semua / Cocok = All / Fits, Gaya Saya = My Styles, card menu, import / export, Acak / Gabung gaya). Engine
texts come in the job language (`job.lang`): Gaya Pro schema labels, template names and groups
(`engine/ac/captions/layout.py`, `templates.py`), and "Tiru gaya dari gambar" (`stylist.py`, keys `cap.stylist.*`:
attribute chips, notes, warnings, errors, stage labels, summary). Ids stay Indonesian on purpose (tab ids `teks` /
`gaya`, censor modes, emoji densities `sedikit / normal / banyak`, stylist `src` "lokal" and `fidelity` values).
User content is never translated and is marked `data-i18n-skip` (transcript word chips, Kamus terms, Gaya Saya
names, font names and the font sample). Caption content does not follow the UI language either: the bleep mask
stays "[sensor]".

## Tests

```
node tools/ui_test.mjs tools/ui_tests/captions.mjs --engine live              # 94 checks, 380 px, screenshots
node tools/ui_test.mjs tools/ui_tests/captions.mjs --engine live --width 660   # split layout
node tools/ui_test.mjs tools/ui_tests/captions.mjs --engine live --width 340   # bottom sheet
node tools/ui_test.mjs tools/ui_tests/captions.mjs --engine live --seq long35  # 34,6 min: 761 cards, edit speed
node tools/ui_test.mjs tools/ui_tests/captions_v3.mjs --engine live --height 900               # 56 checks, v3 library / Gaya Pro
node tools/ui_test.mjs tools/ui_tests/captions_v3.mjs --engine live --height 900 --width 300   # narrow
node tools/ui_test.mjs tools/ui_tests/captions_v3.mjs --engine live --height 900 --width 640   # split layout
```
`captions_v3.mjs` (screenshots `v3_library_*.png` in the gitignored `--shots` folder): seeded last used look -> setup card, prefilled text
rules, escape and back; build from it; badge + Pakai template asli + undo; group chips, search, sprite strip on hover,
reduced motion; dock Simpan gaya -> card at once (placeholder < 10 ms); menu Favorit / Duplikat / Ganti nama / Ekspor
(stubbed `cep.fs.showSaveDialogEx`) / Hapus two-step; Impor gaya (stubbed `showOpenDialogEx`, name "(2)"); pick a Gaya
Saya card; Acak gaya; Gabung gaya (Kuning 3D effects on Tutorial Bersih); Gaya Pro groups, font picker (badges,
categories, keep visual size, real-font sample), 3D on/sub-field/off (explicit null), gradient stop editor, shape tile,
active-word look, Diubah tags, Reset huruf + undo; the last used look written per class; a synthesized 1080x1920
sequence (vertical class separate, portrait default, then remembered and offered to the next vertical sequence);
no horizontal overflow; an English pass (`AC.i18n.set('en')` with the editor open: Teks, word popover, Gaya with the
font picker, Template tab and the save sheet have no Indonesian leftovers, exact English labels, then back to
Indonesian). `captions_gallery.mjs` also checks the engine contract in English (same templates / groups, English
group and animation labels). Engine: `python engine/tests/test_captions_style.py` (+ English option labels, same
schema) and `python engine/tests/test_captions_stylist.py` (+ English chips, summary, template name, errors).
The test needs `--engine live` (every preview is a real engine call). Host functions are stubbed in the test with
a fake track state; the harness sandboxes `APPDATA`, which hides Python's user site-packages where fontTools is
installed on this PC, so the test points `PYTHONUSERBASE` back at the real one (the harness forwards `PYTHON*`).
Covered: setup, build with Kamus, preview (first frame, playhead follow, cached frame, zoom, next/prev), template
gallery + categories + pick + undo/redo + user template save/delete, Teks (popover, colour, inline edit, range
select, hide/show, split/join, find & replace, AI cleanup through the real proxy: accept one in the popover, the
rest through the Tinjau list, orphan merge), Gaya (size, background, grid, safe zone, autoposition, reset),
Animasi (in, highlight with explicit nulls, out, 3 dtk loop, position handle), Ekspor (overlay place v1 with a
pending edit, linear toggle, "Sudah di timeline", edit -> relink v2, SRT, MOGRT), restore after a panel reload,
stale banner -> Perbarui caption keeps edits, no horizontal overflow on every tab.

Measured headless (raw49, this PC): preview at a new playhead 225-229 ms (engine 182-190 ms), cached frame
129-137 ms, word colour click -> new preview on screen 246 ms, template pick -> preview 129 ms, build 18 pages
from cache ~1 s, overlay render + place 1,3 dtk. long35: 761 cards / 2802 chips, Teks tab 89 ms, style op
round trip 182 ms.

## Premiere verification (live, verify agent)

See the report's `premiere_test_plan`. Short version: load the panel, open a test sequence from the 49 s media in
bin "Klipora Test", Buat caption, edit, Terapkan (overlay v1 on a top track "Klipora Captions", nothing else
touched), edit + Perbarui (relink v2 in place), SRT and MOGRT once, linear-colour switch on the TEST sequence only,
timings of the preview, restore after reload, then delete what was created.

## Known limits / UNVERIFIED

- Not run inside Premiere yet: every `bac_captions_*` function, CEP image loading of `file:///` previews, `<video>`
  playback of the burn loop in CEP, `<input type=color>` popup in CEP, Ctrl Z / Ctrl Y reaching the panel
  (registered with `registerKeyEventsInterest`).
- Word timing nudge (ux 4.7) is not offered: the engine has no op for word timing in the user layer (t0/t1 are
  rebuilt from Whisper on every build). Needs an engine op (e.g. `timing {ids, d0, d1}` stored in `word.edit`).
- Old native caption tracks cannot be removed by script (no API); re-applying SRT adds another track.
- Old `_captions_v<n>.mov` project items from earlier "place" runs stay in the bin "Klipora Captions" (or "AutoCut Captions" in older projects; relink reuses
  the item; the engine deletes old files, so such items may go offline). No reliable delete API was verified.
- Undo covers editor ops; autoposition / AI apply / rebuild are not separate undo steps (an undo past them
  restores the earlier user layer, including positions).
- Two sequences with the same name share a workdir and so one `captions.json` (engine gotcha 9); the editor shows
  "Caption ini dibuat untuk sequence lain bernama sama" and offers Perbarui caption.
- v3 library / Gaya Pro, not run inside Premiere yet: `window.cep.fs.showSaveDialogEx` / `showOpenDialogEx` for
  Ekspor / Impor gaya (stubbed headless; signatures as in CEP 12), `@font-face` from `file:///` engine fonts for the
  font samples, sprite strips on hover. Undo restores the doc look but not the Gaya Saya files (rename / delete /
  import are not undo steps). The last used look stores what the panel sees; a look changed only outside the panel
  (e.g. by editing captions.json by hand) is not remembered until the next change or render.

**Animasi (v3, captions_anim.js)**: segmented Pustaka / Studio / Efek suara. Pustaka = preset grids per role (Masuk, Kata aktif, Keluar, Berulang) with engine sprite-strip previews (arrow keys move by the laid-out column count and select, radio pattern), durasi + jeda antar kata, "Ubah di Studio"; the classic pickers live in "Animasi dasar" (a classic entrance / exit pick switches the studio one off). Studio = keyframe editor (8 tracks, dots on a mini timeline: drag / arrows / Delete / double click), value + easing, live sprite, Pakai, Putar di pratinjau, Simpan sebagai animasi saya. Efek suara = 4 rules with sound + keras suara (gain) + play, volume / maks. per menit / jeda minimal / AI, suara sendiri, rencana, Terapkan ke timeline (track "Klipora SFX"). Contract: CAPTIONS_API.md section 11. Test: tools/ui_tests/captions_anim.mjs.
