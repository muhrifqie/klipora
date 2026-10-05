# Auto Caption (tool id `captions`): engine API

Source of truth for the agent that builds the caption editor UI (`panel/js/tools/captions.js`,
`panel/css/tools/captions.css`, `panel/host/34_captions.jsx`). Engine code: `engine/ac/captions/`
(package = tool `captions`; actions in `__init__.py`). Everything below is implemented and covered by
`python engine/tests/test_captions.py` (offline, ~6 s) and, for the "Gaya Pro" style engine (section 6.4-6.6,
fonts, `fonts` action), `python engine/tests/test_captions_style.py` (offline, ~15 s), and for the template
library (3.2 metadata, 3.3.1 animated gallery, 3.13-3.14, 6.7) `python engine/tests/test_captions_gallery.py`
(offline, ~6 s) + `node tools/ui_test.mjs tools/ui_tests/captions_gallery.mjs --engine live`. Read `docs/SPEC.md`,
`docs/ENGINE_API.md` (job protocol, events) and `docs/PANEL_API.md` (`ctx.run`, `AC.engine.worker`) first.

Contents: 1 Flow · 2 Jobs, files, errors · 3 Actions · 4 Captions document · 5 Edit ops · 6 Templates
(6.4 Gaya Pro effects, 6.5 option metadata for generic controls, 6.6 fonts, 6.7 template library) · 7 Applying results in Premiere
(host) · 8 Preview loop · 9 Measured numbers · 10 Gotchas / UNVERIFIED · 12 Style from image and Brand Kit

---

## 1. Flow (what the editor does)

```
open tool ──► templates (once: list + options) ──► gallery (thumbs, cached)
          ──► doc {template?, glossary?}      → <workdir>\captions.json   (transcribes once per media, GPU lock)
editor loop (worker = fast):
   user edits ──► doc {ops:[...]}  (seq: null)   → doc file rewritten, pages re-paginated (~5-60 ms)
              ──► preview {t}                    → PNG over the real frame (~70-90 ms cached frame, ~130-165 ms new frame)
optional    ──► autoposition / cleanup (AI, review list) / burn (3 s MP4 loop)
apply       ──► render                           → plan {kind:"overlay"}       → host: place or relink on "Klipora Captions"
            ──► srt                              → plan {kind:"caption_track"} → host: gxCaptionTrackFromSrt
            ──► mogrt {enable:true}              → plan {kind:"mogrt"}         → host: gxInsertMogrt + gxPopIn per item
re-open     ──► doc {check_only:true}            → {stale}: show "Timeline berubah, Perbarui caption"
```

The document is the single source of truth. Pages are derived from words + template + user flags on every `doc`
call, and every renderer (preview, overlay, SRT, MOGRT) reads `doc.pages`, so the editor shows exactly what is
rendered. User work (text edits, per-word styles, page positions, breaks, ranges, template, doc style) is keyed
by stable word ids and survives re-runs, re-transcription and timeline edits.

---

## 2. Jobs, files, errors

Job envelope (ENGINE_API 2.1): `{tool: "captions", action, seq, params, workdir, review?}`.

- `seq`: Timeline JSON (`bac_seqInfo('full')`, the `ctx.run` default). Required for `doc` (first build),
  `autoposition`, `burn`; optional for `preview` (without it the caption is drawn over a neutral background) and
  `gallery`; ignored by the others. Pass `seq: null` for pure edits (`doc` with `ops`) to skip the timeline.
- `workdir`: default `<workRoot>\<safe seq name>` (panel `AC.settings.workdir`). Files the engine writes there:

| File | What |
|---|---|
| `captions.json` | the captions document (section 4); `params.doc` overrides the path for every action |
| `preview\prev_<n>.png`, `preview\ovl_<n>.png`, `preview\loop_<n>.mp4` | previews (unique names, last 6 / 2 kept) |
| `preview\frames\<hash>.png` | decoded frame cache (60 kept) |
| `captions_render\<seq>_captions_v<n>.mov` | overlay renders (last 3 kept, the linked one is never deleted) |
| `captions_render\chunks\<hash>.mov` | render chunk cache (reused after edits; pruned to the last 2 renders) |
| `captions_render\<seq>_subtitle_v<n>.srt/.vtt` | SRT / VTT exports |
| `mogrt\v<n>\page_0001.mogrt` | MOGRT exports (previous version kept, Premiere may hold it open) |
| `captions_cleanup_review.json` | AI cleanup suggestions (review file, ENGINE_API 3.4 shape) |
| `profanity_review.json` | fallback only (when `ac.tools.profanity.hits_for_timeline` is unavailable): its `on` items are censored |

Other locations: user templates `%APPDATA%\AutoCutBOT\caption_templates\<id>.json`, thumbnails
`%LOCALAPPDATA%\AutoCutBOT\captions\thumbs\`, font indexes `%LOCALAPPDATA%\AutoCutBOT\captions\fonts_bundled.json`
and `sysfonts_v2.json`, font picker samples `...\captions\fontprev\`, per-ASS font folders `...\captions\fontsets\`
(hard links, last 40 kept). Bundled fonts: `engine/ac/captions/fonts/*.ttf` (50 static OFL faces, 33 families,
licences + rebuild script in `fonts/LICENSES/`; section 6.6).

Events: standard (`stage` / `progress` / `stage_done` / `warn` / `result` / `error`). Long actions declare a plan
(`emit.plan`) so `progress.all` is meaningful: `doc` (Transkripsi 0.75, Susun caption 0.25), `render` (Siapkan 0.05,
Render overlay 0.95), `autoposition` (Analisis frame 0.95, Simpan 0.05), `cleanup` (Koreksi teks (AI), Siapkan
review), `gallery`, `burn`, `mogrt`. `preview` / `srt` emit one short stage. Every action except `templates` /
`gallery` / `preview` returns `summary` as a line for Riwayat / History (cleanup returns the review summary dict).

**Language (Klipora i18n).** Every user-facing engine text (stage labels, `summary`, `EngineError` msg + hint,
warnings, cleanup review notes, option labels such as `safe_zones`, `font_categories`, `anim_in/out/word_in`) is
translated at run time with `ac.i18n.tr` in the job language (`job["lang"]` set by the panel, else env `AC_LANG`,
else Indonesian). Keys live in `engine/ac/locales/{id,en}.json`: the shell part uses `cap.*`, `cap.render.*`,
`cap.preview.*`, `cap.model.*`, `cap.mogrt.*`, `cap.srt.*`, `cap.layout.safe.<id>`, `cap.layout.cat.<id>`,
`cap.animName.<id>`; styling / animation / brand / library parts use `cap.style.*`, `cap.anim.*`, `cap.brand.*`,
`cap.tpl.*`, `cap.lib.*`, `cap.sfx.*`. The label tables `layout.SAFE_LABELS`, `layout.FONT_CATEGORIES` and
`compile.ANIM_LABELS` are `layout.LabelMap` objects: same ids, values translated on every read (`[k]`, `.get`,
`.items()`, `.values()`), so module-level tables follow the job language. Caption TEXT is content and is never
translated; the Indonesian text rules (fillers, numbers, Rupiah, glossary sound-alikes) stay as they are.
Examples (en): stages "Transcribe" / "Build captions", render summary "Overlay v2 ready (1.2 MB, 3.4 s)",
`MOGRT_OFF` "MOGRT output (editable in Premiere) is not turned on.".

**Names in Premiere (same in every language).** Overlay track `Klipora Captions` (`OVERLAY_TRACK`), MOGRT track
`Klipora Captions (Editable)` (`mogrt.TRACK`), bin `Klipora Captions`, MOGRT capsules `Klipora Caption` /
`Klipora <n>`, ASS title `Klipora captions`. Projects from before the rename keep their `AutoCut Captions` /
`AutoCut Captions (Editable)` tracks and bin: the host finds them (`gxFindVideoTrack` / `tlFindBin` via
`acNameAlts`), places, relinks and clears on them and does NOT rename them (renaming needs QE on the active
sequence and would surprise users of existing projects). The document kind stays `autocut.captions` (internal
file format id, read and written as before).

Error codes (besides the foundation ones: `NO_SEQ`, `NO_MEDIA`, `FFMPEG`, `DISK_FULL`, `GPU_BUSY`, `WHISPER`,
`CANCELLED`, `BAD_REVIEW`, `INTERNAL`, ...):

| Code | When | msg (ID) |
|---|---|---|
| `NO_CAPTIONS` | action needs `captions.json` / pages but there is none | "Dokumen caption belum ada." |
| `NO_WORDS` | no heard words on the timeline (muted tracks, no speech) | "Tidak ada kata yang terdengar di sequence ini." |
| `BAD_OP` | edit op with an unknown op / word id / page | "Kata tidak ditemukan: ..." |
| `BAD_TEMPLATE` | unknown template id, empty save_template data | "Template tidak ditemukan: ..." |
| `BAD_PARAMS` | bad preview mode / codec | |
| `NO_FRAME` | `preview.frame` PNG missing | |
| `BAD_RANGE` | burn range < 0,2 dtk | |
| `MOGRT_OFF` | `mogrt` without `params.enable` (or setting `captionMogrt`) | |
| `TOO_MANY_CLIPS` | MOGRT clips > `max_clips` (default 300) | |
| `NO_MOGRT_BASE` / `BAD_MOGRT_BASE` | Premiere's bundled "Bold Web Caption.mogrt" not found / unknown format | |

---

## 3. Actions

All examples are real results (49 s test file, shortened). `params.scope` from the source card is honoured by `doc`.

### 3.1 `doc`: build / update the document (also the edit endpoint)

| Param | Default | Meaning |
|---|---|---|
| `template` | settings `captionTemplate`, else `tutorial_bersih` (landscape/square) / `hormozi_kuning` (portrait) | template id. Changing it clears `doc.style` unless `keep_style: true` |
| `style` | none | JSON merge patch onto `doc.style` (null deletes a key) |
| `glossary` | doc's own list | doc glossary terms (list or "a, b; c"). Settings `glossary` is always added |
| `hide_fillers` | true | hide eh/em/hmm... in captions (display only; audio untouched) |
| `censor` | "auto" | `auto` (= the Sensor Kata Kasar tool's caption switch: stars / full / bip / off; stars when that tool is absent), `off`, `stars` (a****g), `first` (a*****), `full` (******), `bleep` ([sensor]). Which words: the profanity tool's `hits_for_timeline` (its reviewed decisions per media word, else its lexicon rules) |
| `numbers` | true | "50 ribu" -> "50.000", "Rp15.000" -> "Rp 15.000" |
| `min_word` | 0.08 | min highlight duration per word (s) |
| `tracks` | all heard | audio track indexes to caption |
| `transcribe` | true | false = only cached transcripts (no GPU) |
| `scope` | all | `ctx.run` scope dict: caption only In/Out or the selection |
| `ops` | [] | edit ops (section 5), applied after the build, in order |
| `rebuild` | false | ignore the existing doc (drops every user edit) |
| `check_only` | false | with `seq`: only report `{exists, stale}` (audio edit or frame size changed since the doc was built) |
| `inline` | false | also return the slim doc in `data` (saves a file read in the panel) |
| `keep_style` | false | keep `doc.style` when `template` changes |

Without `seq` (or `seq: null`) only `template` / `style` / `ops` apply to the saved doc (fast, use the worker).

```json
-> {"doc": "C:\\...\\captions.json", "summary": "17 halaman, 80 kata, 2 koreksi glosarium",
    "stats": {"words": 80, "pages": 17, "hidden": 0, "censored": 0, "fixes": 2, "edited": 0, "low_conf": 3,
              "fast_pages": 3, "start": 1.66, "end": 42.54, "paginate_ms": 2},
    "template": "tutorial_bersih", "pages": 17, "notes": [{"op": "replace", "n": 3}],
    "info": {"fixes": 2, "profane": 0, "profanity_source": "profanity", "censor_style": "stars"},
    "hash": "7365d9c8707b0f5fd3bc32b1",
    "rendered": false, "data": {...slim doc, only with inline}}
check_only -> {"doc": "...", "exists": true, "stale": false, "stats": {...}, "template": "tutorial_bersih",
               "summary": "Caption sesuai timeline" | "Timeline berubah"}
```
`rendered` = the last overlay render matches this doc (`doc.render.hash == doc.hash`): show "Sudah terbaru" on the
render button.

### 3.2 `templates`

`params`: `options` (default true), `full` (false: also return every resolved template), `system_fonts` (false:
also return the installed Windows font families usable as `font.family`, Regular faces, cached index ~0,2 s).
Uses `seq` size for the default.
-> `{templates: [row], default: "tutorial_bersih", options: {...}, full?: {id: template}, system_fonts?: ["Arial", ...]}`.

```json
row = {"id": "tutorial_bersih", "name": "Tutorial Bersih", "description": "Default rekaman layar 16:9: ...",
       "source": "builtin" | "user", "aspect": ["16:9", "21:9"], "default_for": ["landscape"], "tags": ["tutorial"],
       "font": "Plus Jakarta Sans Bold", "uppercase": false, "mode": "karaoke", "anim_in": "fade", "anim_out": "fade",
       "box": "line" | "page" | "word" | null, "highlight": "#FFE066", "emoji": false, "thumb": null}
options = {"fonts": [{"family": "Anton", "weight": 400, "file": "Anton.ttf", "bundled": true, "base": "Anton",
                      "categories": ["kondensasi", "tebal"], "cap": 0.4958}, ...],          // bundled only
           "anim_in": [{"id": "fade", "label": "Pudar"}, ...], "anim_out": [...], "word_in": [...],
           "modes": [{"id": "karaoke", "label": "Karaoke (kata aktif)"}, ...],
           "highlight_presets": [{"id": "pill", "label": "Kotak geser", "patch": {...}}, ...],
           "box_presets": [...5 classic ids...], "box_shapes": [...10, section 6.4...],
           "effect_presets": [...11 one-click looks...], "cases": [{"id": "title", "label": "Awal Kata Besar"}, ...],
           "font_categories": [{"id": "tebal", "label": "Tebal"}, ...],
           "groups": [{"id": "huruf", "label": "Huruf"}, ...], "schema": [...95 fields, section 6.5...],
           "word_schema": [...per-word style controls...],
           "safe_zones": [...], "punct": [...], "align": [...], "emoji_position": [...],
           "censor_modes": [...], "word_style_keys": [...], "defaults": {...DEFAULTS, every template key}}
```
For the full font list (with installed Windows fonts, categories, samples) use the `fonts` action (3.12).
Built-in: 51 (the library, section 6.7). Defaults stay Tutorial Bersih (16:9) and Hormozi Kuning (9:16).

Library fields added to every row (`gallery.meta`, section 6.7) and a top-level `groups` list:
```jsonc
out.groups = [{"id": "shorts_viral", "label": "Shorts Viral", "count": 14}, ...11 in fixed order...]
row += {"group": "shorts_viral",            // primary group id (null for an unknown user template)
        "groups": ["shorts_viral", "fun"],  // every group the template belongs to (filter chips)
        "group_label": "Shorts Viral",
        "aspect_fit": "9:16" | "16:9" | "both",
        "colors": ["#FFFFFF", "#FFE600", "#000000"],   // up to 4 opaque swatch dots (fill/gradient, accent, box, ...)
        "effects": [{"id": "extrude", "label": "3D"}, ...],   // gradient extrude long_shadow outline2 glow box shape
                                                              // sticker rotate skew wiggle emoji
        "font_size": 90, "anim_in_label": "Pop", "anim_out_label": "Tanpa",
        "word_in_label": null | "Jatuh",     // reveal mode only
        "mode_label": "Karaoke (kata aktif)",
        "is_new": true,                      // built-in and not one of the 19 pre-library templates
        "pro": true}                         // uses a Gaya Pro text/box/position effect
```
Legacy tags (`shorts`, `tutorial`, `podcast`, `fun`, `gaming`, `cinematic`, `clean`, `education`, `jualan`, ...) are
kept on every template, so category filters built on `tags` keep working.

### 3.3 `gallery`

`params`: `ids` (default all), `t` (sequence time: draw the demo phrase over the user's frame at t; default a
neutral gradient), `size` ([360, 180]), `force`. Thumbnails are cached by template content, so a second call is
~3 ms; 19 templates cold ~0,8-2,8 s (8 threads).
-> `{thumbs: {id: "C:\\...\\thumbs\\hormozi_kuning_216c192b38a0.png" | null}, errors: {id: msg}, size: [360, 180]}`.
Without `t` each template is drawn in its preferred frame (first `aspect`), cropped to the caption band.

#### 3.3.1 `gallery` with `anim: true`: animated sprite sheets

Each template's demo loop ("Rahasia cuan jualan online 100% gratis!", ~2,3 s) rendered by libass (same compiler as
preview/render) into ONE horizontal PNG strip of `frames` frames, cropped to the caption area. Frame 0 is the
empty frame before the first word, so the loop restarts cleanly and shows the entrance animation.

| Param | Default | Meaning |
|---|---|---|
| `anim` | false | true = this mode |
| `ids` | all | templates, **in the order to render** (send the visible cards first) |
| `limit` | none | render at most this many NEW strips in this call; the others come back in `pending` (lazy batches: call again with `pending` or the next visible ids) |
| `cached_only` | false | return only what is cached (instant), everything else in `pending` |
| `t` + `seq` | none | draw over the user's frame at sequence time t (frame cache as `preview`); every template uses the sequence size |
| `frame` | none | PNG exported by Premiere (`bac_exportFrame`) instead of decoding (drawn at half the sequence size) |
| `size` | [320, 160] | one frame, px |
| `frames` / `fps` | 16 / 7 | 4..32 / 2..30 (16 at 7 fps = 2,29 s loop) |
| `force` | false | re-render |

```json
-> {"anims": {"kuning_3d": {"png": "C:\\...\\AutoCutBOT\\captions\\anim\\kuning_3d_23891859c31b7882.png",
                            "frames": 16, "fps": 7, "w": 320, "h": 160, "cols": 16, "rows": 1,
                            "sheet_w": 5120, "sheet_h": 160, "dur": 2.286, "cached": false}},
    "pending": ["komik"], "errors": {}, "size": [320, 160], "frames": 16, "fps": 7,
    "frame": "neutral" | "user", "cache_mb": 5.4}
```
Progress: one `progress` event per rendered strip (`note` "3/6"). Cost (this PC): all 51 strips cold 2,9 s
(4 threads, ~100 KB each, 5,4 MB total); cached call for all 51 ~50 ms; 2 new strips ~0,8 s.
Cache: `%LOCALAPPDATA%\AutoCutBOT\captions\anim\<id>_<hash>.png`, key = template content + frame content (sha1 of
the PNG bytes, "neutral" otherwise) + size/frames/fps. LRU: a hit touches the file; after each batch the least
recently used strips are deleted until the folder is under 140 MB (strips of the current call are kept).

Panel animation (Chrome 99, no video decoding; checked headless in `tools/ui_tests/captions_gallery.mjs`):
```css
.thumb { aspect-ratio: 2 / 1; background: url(<png>) no-repeat; background-size: 1600% 100%;   /* frames x 100% */
         animation: strip 2.286s steps(16, jump-none) infinite; }                              /* dur, frames */
@keyframes strip { from { background-position: 0% 0 } to { background-position: 100% 0 } }
```
Tips: show the static `thumbs` first and swap to the strip when it arrives; pause off-screen cards
(`animation-play-state: paused`) or animate only on hover/focus when many cards are visible; respect
`prefers-reduced-motion`. Wide 16:9 templates with long lines (tutorial, subtitle) look small in a 2:1 strip
because the whole line is kept in the crop.

### 3.4 `preview`

| Param | Default | Meaning |
|---|---|---|
| `t` | `seq.player` | sequence seconds |
| `mode` | `frame` | `frame` = captions burned over the real frame, `overlay` = transparent PNG (straight alpha) to stack over your own image, `both` |
| `scale` | 0.5 | output size factor (2292x960 -> 1146x480). libass scales exactly |
| `fit` | `cover` | how a source of another size fills the sequence frame: `cover` (scale + centre crop), `contain`, `native` |
| `frame` | none | PNG exported by Premiere (`bac_exportFrame`) to use instead of decoding the media (exact composite of all tracks; do not include our overlay track) |

```json
-> {"t": 10.0, "pages": ["p:314743ca9d:18"], "w": 1146, "h": 480, "png": "C:\\...\\preview\\prev_49028701.png",
    "overlay": "C:\\...\\preview\\ovl_49028979.png", "frame_cached": true, "ms": 91}
```
Run it through `AC.engine.worker` (see section 8). Each call writes a NEW file name (an `<img>` would cache a reused
path). The frame under t is the topmost enabled video clip with real media, skipping tracks named
`Klipora Captions*` (and the old `AutoCut Captions*`, `preview.CAPTION_TRACK_PREFIXES`) and our `_captions_v*` files.

### 3.5 `autoposition`

Per page: 3 frames, grids for faces (YuNet), on-screen text/UI (edge boxes) and action (frame difference, ignored
on page changes), candidate y every 5 % inside the platform safe zone, Viterbi over pages with jump costs and a 3 s
min-run cleanup, so captions only move when something important is underneath. Manual positions are fixed points.

| Param | Default | Meaning |
|---|---|---|
| `platform` | template `layout.safe` (`auto` = youtube landscape / tiktok portrait / square) | `youtube`, `tiktok`, `reels`, `shorts`, `vertical_universal`, `square`, `feed45`, `title`, `none` |
| `avoid` | `["faces","text","action"]` | what to keep clear |
| `contrast` | false | also give low-contrast pages (WCAG < 3:1, no box, outline < 4) an automatic box (`page_over[..].auto_style`) |
| `reset_manual` | false | also replace manual positions |
| `clear` | false | remove all automatic positions (back to the template y) |

```json
-> {"pages": 45, "moved": 11, "boxed": 0, "safe": "youtube", "anchor": 85.03, "faces_model": true,
    "summary": "11 dari 45 halaman dipindah",
    "items": [{"page": "p:..:12", "y": 75.0, "why": ["action"], "cost": {"face": 0, "text": 0.1, "action": 0}}, ...]}
```
`why`: `default`, `face`, `text`, `action`, `stability` (moved with its neighbours), `manual`. Results are stored in
`doc.page_over` (`auto: true`), the doc is re-paginated and saved; the next `doc` call keeps them.

### 3.6 `render`: the overlay video

Straight-alpha QuickTime Animation (qtrle, argb) rendered with the black/white difference matte (pixel-exact vs
libass, verified in Premiere by the lab), 30 fps (<= min(seq fps, 60)), **one file from sequence 0** to the last
caption + 0,5 s. Automatically a **band** (only the area captions ever touch, snapped to 8 px) when it covers
<= 60 % of the frame, else full frame. Rendered as cached frame-aligned ~60 s chunks in parallel, then stream-copied
into one versioned file: after an edit only the dirty chunks re-render. Every page compiles once per render job
(`compile.page_cache()`: the band pass and the chunk texts reuse the events, shifted to the band origin, byte-identical
output); the prep step reports progress per page and is a cancel point. Pro templates on the 34,6 min doc: flash_sale
533 k events, compile ~28 s once (was ~60 s: band pass + a second full pass for the chunks).

| Param | Default | Meaning |
|---|---|---|
| `codec` | `qtrle` | `qtrle` (~30 MB/min at 1080x1920 dense), `png` (PNG-in-MOV, 1,5x), `prores` (ProRes 4444 709, 4x) |
| `fps` | min(30, seq fps) | 10..60 |
| `band` | `auto` | `auto` / `on` / `off` (full frame) |
| `force` | false | render even when `doc.hash` equals the last render |
| `until` | none | only render [0, until] s (tests / quick checks) |
| `out_dir` | settings `outputDir`, else `<workdir>\captions_render` | |
| `chunk` | 60 | chunk length (s) |

```json
-> {"path": "C:\\...\\captions_render\\AC Test Cut (Klipora)_captions_v3.mov", "seconds": 3.47, "size_mb": 124.5,
    "summary": "Overlay v3 siap (124,5 MB, 3,5 dtk)", "chunks": 35, "rendered_chunks": 1, "reused_chunks": 34,
    "removed": ["..._captions_v1.mov"], "version": 3, "speed": 598.2,
    "plan": {"kind": "overlay", "path": "C:\\...\\_captions_v3.mov", "track": "Klipora Captions", "start": 0.0,
             "duration": 2075.77, "frame": [2292, 960], "band": [832, 752, 624, 120], "full": false,
             "xNorm": 0.49913, "yNorm": 0.84583, "scale": 100, "alpha": "straight", "codec": "qtrle", "fps": 30.0,
             "version": 3, "previous": "C:\\...\\_captions_v2.mov", "relink": true}}
unchanged doc -> {"path": "...v2.mov", "skipped": true, "plan": {...last plan}, "version": 2, "summary": "Overlay v2 sudah terbaru"}
```
`relink` is true only when a previous render exists AND the band geometry and codec are unchanged (then swap media
in place); otherwise replace the clip (section 7). `xNorm/yNorm` = band centre as a fraction of the frame (Motion
Position). Checks free disk space first (`DISK_FULL`), deletes old versions best-effort (Premiere locks linked files).

### 3.7 `srt`

`params`: `format` (`srt` | `vtt`), `mode` (`page` = one cue per page with its line breaks, `line`, `word`),
`tags` (SRT `<font color>` for coloured words, `<b>`, `<i>`; on-screen rendering in Premiere UNVERIFIED), `upper`,
`out` (path; default versioned `<workdir>\captions_render\<seq>_subtitle_v<n>.srt`).
-> `{path, cues, format, summary, plan: {kind: "caption_track", path, format}}`. Cues never overlap; text = doc
display text (edits, glossary, censor), without template case transforms unless `upper`.

### 3.8 `burn`: short MP4 loop

`params`: `t0` (default `seq.player`), `t1` or `dur` (3 s; max 30 s), `scale` (0.5), `audio` (true), `fit`.
Rebuilds the picture from the clips under the range (cuts, speed) and burns the captions in (h264_nvenc when it
works, else libx264). For checking animation and sync in the panel (`<video>`); full-length burn-in is deliberately
not offered (export from Premiere with the overlay track instead).
-> `{path: "...\\preview\\loop_<n>.mp4", t0, t1, w, h, audio, segments, summary}` (~0,8 s for 3 s).

### 3.9 `mogrt`: optional native editable captions

Only with `params.enable: true` (or setting `captionMogrt: true`). One native text MOGRT per page (per-word colour,
font, size, stroke; text box when the template has one), or with `states: true` one clip per active-word state
(karaoke colour). Fonts must be installed in Windows: template fonts are mapped to installed faces, else Arial Bold
(`warnings`). Limits: `max_clips` (300). `single_line: true` joins lines with spaces.
```json
-> {"plan": {"kind": "mogrt", "track": "Klipora Captions (Editable)", "seconds_estimate": 4.4,
             "items": [{"path": "...\\mogrt\\v1\\page_0001.mogrt", "start": 1.66, "end": 3.01, "page": 0, "word": null,
                        "anchor": [0.5, 0.871], "position": [0.5, 0.8628], "pop": true}, ...]},
    "dir": "...\\mogrt\\v1", "version": 1, "fonts": {"Plus Jakarta Sans Bold": "Arial-BoldMT"},
    "warnings": ["Font belum terpasang di Windows, diganti Arial: Plus Jakarta Sans Bold"], "summary": "17 MOGRT siap ..."}
```

### 3.10 `cleanup`: AI text cleanup (suggestions only)

Deterministic fixes (glossary, numbers) are already applied by `doc`. `cleanup` sends the current display text to
the AI route (`ac.ai.tasks.run("cleanup")`, fast slot, cached, sentence rows + local difflib alignment: the model never
changes timing, deletions/insertions are rejected) and writes a **review file**; nothing changes until accepted.

- `params.mode` omitted (suggest) ->
  ```json
  {"review": "C:\\...\\captions_cleanup_review.json", "summary": {"n": 6, "on": 6, "sec_on": 4.07},
   "source": "ai" | "cache" | "mixed" | "fallback" | "none", "warnings": [], "model": "grok-fast",
   "edits": [{"id": "c333", "ids": ["314743ca9d:12"], "old": "Kita", "new": "kita", "kind": "case_punct"}, ...]}
  ```
  Review items: `{id, t0, t1, kind: case_punct|word_fix|merge, on: true, conf, label: "old → new",
  ctx: {pre, post}, ids, old, new, note: {type: "ai", text: "Huruf besar / tanda baca" | "Salah dengar" | "Gabung kata"}}`
  (the panel's review list can show it as is). AI off / proxy down -> `source: "fallback"`, no items, a `warn` event.
- `params.mode: "apply"` + `job.review` (the edited file) -> applies the `on` items as `accept` ops (user text
  edits with `src: "ai"`), re-paginates, saves -> `{doc, applied, summary, stats}`.

### 3.11 `save_template`

| Params | Effect |
|---|---|
| `{from_doc: true, name, id?, description?, template?}` | saves the doc's effective look (template + `doc.style` [+ patch]) as a user template |
| `{template: {...full or partial...}, name, id?, description?}` | saves the given dict (missing keys = defaults) |
| `{delete: "<id>"}` | deletes a user template -> `{deleted, id}` |

-> `{template: row (source "user", thumb path), id}`. Ids are slugs; a name that would shadow a built-in id becomes
`user_<id>`. User templates may set `"base": "<template id>"` to extend another template.

### 3.12 `fonts`: font catalog for the picker

`params`: `system` (true: also installed Windows fonts), `refresh` (false: rescan the Windows font folders),
`preview` (list of families, `"all"` or true: sample PNGs), `text` (sample text, default "Halo Teman 123"),
`size` ([360, 72]). No `seq` needed; fast (bundled index cached, Windows scan ~0,6 s cold then ~20 ms).
```json
-> {"fonts": [{"family": "Rubik Black", "base": "Rubik", "weight": 900, "source": "bundled",
               "categories": ["tebal", "bulat"], "category": "tebal", "file": "RubikBlack.ttf", "cap": 0.4569,
               "bold_face": true, "italic_face": false, "preview": true},
              {"family": "Arial", "base": "Arial", "weight": 400, "source": "system", "categories": ["standar"],
               "category": "standar", "path": "C:\\WINDOWS\\Fonts\\arial.ttf", "cap": 0.6412,
               "bold_face": true, "italic_face": true, "preview": true}, ...],          // ~295 on this PC
    "families": [{"base": "Rubik", "source": "bundled", "category": "tebal",
                  "faces": [{"family": "Rubik Bold", "weight": 700}, {"family": "Rubik Black", "weight": 900}]}, ...],
    "categories": [{"id": "tebal", "label": "Tebal"}, {"id": "standar", "label": "Standar"},
                   {"id": "bulat", "label": "Bulat"}, {"id": "kondensasi", "label": "Kondensasi"},
                   {"id": "serif", "label": "Serif"}, {"id": "tulisan_tangan", "label": "Tulisan tangan"},
                   {"id": "dekoratif", "label": "Dekoratif"}],
    "previews": {"Rubik Black": "C:\\...\\AutoCutBOT\\captions\\fontprev\\<hash>.png", "Bad Font": null}}
```
`family` is the value for `font.family` / word style `font` / `emphasis.font`. Weights of one design are grouped
by `base` (bundled faces are separate families such as "Rubik Bold" / "Rubik Black", libass picks them by name;
installed families use their nameID 1 family and libass/DirectWrite picks their Bold / Italic face for word
`bold` / `italic`). `cap` = cap height / font size cell: the same `font.size` looks bigger in fonts with a higher
`cap` (Arial 0,64 vs Montserrat 0,45). To keep the visual size when switching font:
`size_new = size_old * cap_old / cap_new`. Preview PNGs: white text, dark outline, transparent background,
drawn with Pillow from the same file (cached; ~5 ms each).

### 3.13 `template_mix`: combine parts of different templates

`params`: `parts` = `{font?, style?, box?, highlight?, anim?, layout?: template id}`, `base` (template id the doc
keeps; default the first part's template). Part groups: `font` = all of `font` (family, size, case, spacing, skew);
`style` = all of `style` (fill, strokes, shadow, glow, gradient, 3D, long shadow); `box` = all of `box`;
`highlight` = `highlight` + `timing.mode` + `anim.word_in(_dur)`; `anim` = `anim.in/in_dur/out/out_dur`;
`layout` = `layout` (except `y`, kept from the base) + pacing (`timing.max_gap/max_dur/min_dur/hold/fill_gaps/lead`).
The result goes through the readability check (6.7), so for example dark text on a dark box gets fixed.
```json
-> {"template": "boxed", "style": {...doc.style that turns `boxed` into the mix, explicit nulls kept...},
    "full": {...the resolved draft template...}, "name": "Campuran Kuning 3D + Podcast + Stiker Viral",
    "parts": {"font": "kuning_3d", ...}, "fixes": ["Warna teks disesuaikan dengan latar"],
    "ops": [{"op": "template", "id": "boxed"}, {"op": "doc_style", "style": {...}, "replace": true}],
    "summary": "..."}
```
Apply: send `ops` with `doc` (no seq). To keep it: `save_template {from_doc: true, name}`. Unknown part ->
`BAD_PARAMS`, unknown id -> `BAD_TEMPLATE`.

### 3.14 `template_random`: curated random look

`params`: `seed` (int; same seed + frame size = same look; default random), `base` (template id to start from;
default the aspect default), `keep` (part names from 3.13 to leave as the base has them). Uses `seq` size (or
`params.w/h`, default 1080x1920). Picks from curated lists: 20 display fonts sized to a sane cap height (40-48 px@1080
portrait, 24-29 landscape), 8 palettes (fill / accent / stroke), one text effect (none, 3D, double stroke, gradient,
long shadow, glow), a background (none, line, marker, banner, sticker, panel; the text colour follows the box), an
active-word look from `highlight_presets_all`, an entrance/exit pair and a layout per aspect; then the readability
check (6.7). -> same shape as 3.13 plus `seed`; `name` = "Acak #0042".

---

## 4. The captions document (`captions.json`)

```jsonc
{
 "v": 1, "kind": "autocut.captions",
 "seq": {"id": "...", "name": "...", "w": 2292, "h": 960, "fps": 120.0, "duration": 48.925,
         "clipsHash": "b54ff227f080aa173273b7d4"},        // audio edit fingerprint (staleness)
 "created": "2026-10-05T04:38:51+07:00", "updated": "...", "lang": "id",
 "template": "tutorial_bersih",                           // USER layer from here ...
 "style": {},                                             // doc-level template overrides (deep merge)
 "params": {"hide_fillers": true, "censor": "auto", "numbers": true, "glossary": ["TokoKita"],
            "min_word": 0.08, "tracks": null, "scope": "all"},
 "page_over": {"<word id>": {"y": 70.0, "auto": false, "why": ["text"], "style": {...}, "auto_style": {...}}},
 "ranges": [{"t0": 30.0, "t1": 42.0, "style": {"template": "neon", "highlight": {"color": "#00FFAA"}}}],
 "render": {"version": 3, "path": "...", "codec": "qtrle", "fps": 30.0, "band": [x, y, w, h], "full": false,
            "duration": 43.04, "hash": "...", "rendered": "...", "chunks": ["..."], "plan": {...}, "previous": "..."},
 "words": [ ... ],                                        // see below
 "pages": [ ... ],                                        // DERIVED (read-only for the UI)
 "info": {"fixes": 2, "profane": 0, "profanity_source": "profanity" | "review" | "builtin",
          "censor_style": "stars"},                      // the profanity tool's caption style (censor "auto")
 "stats": {"words": 81, "pages": 18, "hidden": 0, "censored": 0, "fixes": 1, "edited": 2, "low_conf": 3,
           "fast_pages": 3, "start": 1.66, "end": 45.94, "paginate_ms": 2},
 "hash": "e35a35f7daf3ba7406c385cc"                       // content hash of what would be rendered
}
```

### 4.1 `words[]` (one per Whisper word on the timeline, sequence time, sorted)

```json
{"id": "314743ca9d:4", "t0": 3.93, "t1": 4.31, "raw": "cara", "text": "Cara", "p": 0.936, "seg": 0,
 "hide": false, "censor": false, "edit": {"text": "Cara", "merged": "", "src": "user"}}
{"id": "314743ca9d:3", "t0": 3.33, "t1": 3.93, "raw": "tutorial", "text": "tutorial", "p": 0.998, "seg": 0,
 "hide": false, "censor": false, "style": {"color": "#FF4D4D"}}
{"id": "314743ca9d:25", "t0": 17.22, "t1": 17.5, "raw": "toko", "text": "TokoKita", "p": 0.95, "seg": 0,
 "auto": {"text": "TokoKita", "kind": "glossary", "n": 2, "old": "toko kitah"}, "hide": false, "censor": false}
{"id": "314743ca9d:26", "t0": 17.5, "t1": 17.88, "raw": "kitah", "text": "kitah", "p": 0.684, "seg": 1,
 "merged": "314743ca9d:25", "hide": false, "censor": false}
```

| Field | Layer | Meaning |
|---|---|---|
| `id` | stable | `<media file hash 10>:<transcript row>` (`~2` suffix when the same source word appears twice on the timeline) |
| `t0`, `t1` | engine | sequence seconds (Whisper timing mapped through the clips) |
| `raw` | engine | Whisper text |
| `p`, `seg` | engine | Whisper probability (show `p < 0.5` as red "cek" words), 1 = Whisper phrase end |
| `auto` | engine | deterministic fix on the FIRST word of a span: `{text, kind: glossary|number, n, old}`; following words get `merged` |
| `filler`, `profane` | engine | detected filler / profanity (flags, recomputed each build) |
| `edit` | **USER** | `{text?, hide?, censor?, merged?, brk?, src: user|ai}`; `merged: ""` = explicitly not merged; `brk`: `page` (new page before this word), `line` (new line), `join` (never break before) |
| `style` | **USER** | per-word style overrides (section 6.3) |
| `text`, `hide`, `censor`, `merged` | resolved | effective values (edit > auto > raw); what the UI shows |

A word whose `merged` points to another id is displayed as part of that word (span edits, glossary spans): the head
word carries the text and is highlighted for the whole span. Hidden words and merged followers are not on pages.

### 4.2 `pages[]` (derived)

```json
{"id": "p:314743ca9d:2", "i": 1, "t0": 3.01, "t1": 6.49,
 "lines": [["314743ca9d:2", "314743ca9d:3", "314743ca9d:4", "314743ca9d:5"], ["314743ca9d:6", "...:7", "...:8", "...:9"]],
 "text": "untuk tutorial Cara pengajuan\nreseller sampai ke setting", "over": "314743ca9d:2",
 "y": 70.0, "pos": "manual", "cps": 14.1, "fast": true, "low": ["314743ca9d:8"]}
```
`id` = `p:` + first word id (changes when the page starts at another word). `t0/t1` = visibility (lead/hold/fill
gaps applied). `text` = display text after template transforms (uppercase/punctuation). `y` = effective centre y
(% of H). `pos` = `manual` | `auto` | null. `cps` = reading speed (chars/s); `fast` when > 17. `low` = low-confidence
word ids. `over` = the `page_over` key in effect.

Pagination rules (Indonesian): limits per template (`max_words`, `max_chars`, `max_lines`, real pixel width), new
page on pause > `max_gap`, Whisper phrase end or `.?!`, `max_dur`; a line/page never ends on a function word
(`di ke dari yang dan untuk dengan ... nih sama buat`) when avoidable; comma-aware balanced line breaks; 1-word
orphans borrow the previous word; user `brk` flags win.

### 4.3 Style resolution

Per page: template (`doc.template`) -> `doc.style` -> `ranges` covering the page start (a range style may switch
`template`) -> `page_over[key].auto_style` -> `page_over[key].style` -> `page_over[key].y`.
Per word: template emphasis rules (numbers / keywords / emoji map) -> `word.style`.
Pagination uses the doc-level template (template + doc.style); page/range overrides are visual (auto-fit shrinks a
page whose override makes it too wide).

---

## 5. Edit ops (`doc` with `params.ops`, applied in order, then re-paginated and saved)

| Op | Fields | Effect |
|---|---|---|
| `text` | `ids` (consecutive word ids), `text` | same word count -> per-word texts (own timing); other count -> first word gets the whole text, the rest merge into it (timing = whole span); empty text -> hide |
| `accept` | `edits: [{ids, new}]` | like `text` with `src: "ai"` (cleanup) |
| `reset` | `ids`, `fields?` (`["text","hide","censor","merged","brk","style"]`) | remove user edits (all when omitted) |
| `style` | `ids`, `style` | merge patch into `word.style` (null deletes a key) |
| `hide` / `censor` | `ids`, `on: true|false|null` | null = back to automatic |
| `break` | `ids`, `kind: "page"|"line"|"join"|null` | page / line break before the word, or forbid a break |
| `page_style` | `page` (page id / index) or `id` (word id), `style` (null clears) | page-only template overrides |
| `page_pos` | `page` or `id`, `y` (0..100 % of H, null = template y) | manual position (drag in the preview) |
| `template` | `id`, `keep_style?` | switch template (clears `doc.style` unless keep_style) |
| `doc_style` | `style`, `replace?` | merge patch into `doc.style` (e.g. a highlight / box preset `patch`) |
| `range_style` | `t0`, `t1`, `style` (null removes that range) | section style ("customize specific sections") |
| `range_clear` | | remove all ranges |
| `replace` | `find`, `replace`, `case?`, `whole?` (true) | find & replace over display words (multi-word find ok) -> note `{op, n}` |
| `params` | any of `hide_fillers, censor, numbers, glossary, min_word, tracks, scope` | doc params (glossary changes apply on the next build with `seq`) |
| `auto_emoji` | `density: sedikit|normal|banyak`, `map?` | emoji on Indonesian keywords at most every 15 / 8 / 4 s (as word styles) -> note `{op, n}` |
| `clear_emoji` | | remove every word emoji |
| `clear_auto_pos` | | drop automatic positions |

```js
// examples (ctx.run / AC.engine.worker, seq: null)
{action: 'doc', seq: null, params: {ops: [
  {op: 'text', ids: ['314743ca9d:25', '314743ca9d:26'], text: 'TokoKita'},
  {op: 'style', ids: ['314743ca9d:3'], style: {color: '#FF4D4D', scale: 1.2, emoji: '🔥', pill: '#16A34A'}},
  {op: 'break', ids: ['314743ca9d:9'], kind: 'page'},
  {op: 'page_pos', page: 'p:314743ca9d:2', y: 30},
  {op: 'doc_style', style: {box: {enabled: true, per: 'word', opacity: 0.5}}},
  {op: 'range_style', t0: 30, t1: 42, style: {template: 'neon'}}]}}
```
The UI may also edit `captions.json` directly (only the USER fields: `template`, `style`, `params`, `page_over`,
`ranges`, `words[].edit`, `words[].style`) and then call `doc` with `seq: null` to re-resolve and re-paginate.
Always re-read the file (or use `inline`) after a `doc` call: the engine rewrites it atomically.

---

## 6. Templates

### 6.1 Schema (every key; files store only differences from `options.defaults`)

| Key | Values / units |
|---|---|
| `name`, `description`, `aspect` (["16:9"]), `default_for` (["landscape"/"portrait"]), `tags` | metadata |
| `font.family` | bundled family name or an installed Windows family (`fonts` action, 6.6) |
| `font.size` | px at a 1080-px short side (scaled by min(W,H)/1080). ASS size = winAscent+winDescent cell, not em |
| `font.uppercase`, `font.italic`, `font.letter_spacing` (px@1080), `font.word_spacing` (x space), `font.line_spacing` (x size), `font.punct` (`keep` / `soft` = drop , . / `strip`) | |
| `font.case` **(Pro)** | `null` (= follow `uppercase`) / `as_is` / `upper` / `lower` / `title` (each word capitalised, rest kept) / `sentence` (sentence starts capitalised, other words lower-cased except acronyms/camel case like "AI", "WhatsApp", glossary fixes and user-typed text). Wins over `uppercase` when set; when the UI writes `case` it should also write `uppercase: (case == "upper")` for older readers |
| `font.skew` **(Pro)** | forward slant, -0.5..0.5 (`\fax`, 0,2 = like italic); neighbours get room |
| `style.fill`, `style.outline` (px@1080), `style.outline_color`, `style.shadow`, `style.shadow_x/y`, `style.shadow_color`, `style.shadow_blur` (>0 = soft shadow layer), `style.glow` (`{color, width, blur, active_only, strength}` or null), `style.color_cycle` (list) | colours `#RRGGBB` or `#RRGGBBAA` (AA = opacity) |
| `style.outline_opacity` **(Pro)** | 0..1 or null (= the outline colour's alpha) |
| `style.outline2`, `style.outline2_color` **(Pro)** | second outer stroke (double outline), px@1080, 0 = off |
| `style.gradient` **(Pro)** | null or `{colors: [2-3 colours], dir: "vertical"\|"horizontal", span: "word"\|"line" (horizontal only), bands: 2..16 (8)}` |
| `style.extrude` **(Pro)** | 3D: null or `{depth: px@1080, angle: deg (0 = right, 90 = down; 60), color, color_end (null = same), steps: 0 = auto, max 32}` |
| `style.long_shadow` **(Pro)** | null or `{length: px@1080, angle: deg (45), color, fade: true, steps: 0 = auto, max 24}` |
| `layout.max_words`, `max_chars`, `max_lines` (1-5), `x`, `y` (% of frame, block centre), `width` (% max line width), `align` (center/left/right), `safe` (zone id or `auto`), `auto_fit` | auto_fit shrinks a page so the drawn block (text + outline / box padding + the active word's pop and highlight pill) fits the safe width, so it is not clamped against an edge |
| `layout.rotate`, `layout.rotate_jitter` **(Pro)** | page tilt in degrees (positive = counter-clockwise, like ASS `\frz`); jitter = every other page +j / -j (by page index, so preview = render) |
| `timing.mode` | `line` (no word highlight), `karaoke` (active word), `reveal` (words appear when spoken), `word` (one word per page) |
| `timing.max_gap`, `max_dur`, `min_dur`, `hold`, `fill_gaps`, `lead` | seconds |
| `box.enabled`, `box.per` (`line` / `page` / `word`), `box.color`, `box.opacity` (0..1 or null = colour alpha), `box.radius`, `box.pad_x`, `box.pad_y` (px@1080) | background |
| `box.shape` **(Pro)** | `rect` (rounded by `radius`) / `marker` (rough highlighter brush) / `banner` (slanted ends) / `ribbon` (notched ends) / `sticker` (= one rounded box per word, alternating `tilt`, the word turns with it) |
| `box.filled`, `box.border`, `box.border_color` **(Pro)** | `filled: false` + `border` (px@1080) = outline-only frame; border also works on filled boxes |
| `box.gradient`, `box.shadow`, `box.tilt` **(Pro)** | null or `{colors: [2-3], bands: 8}` (vertical); null or `{x, y, blur, color}` (drop shadow under the box); degrees |
| `highlight.color` (active word, null = none), `scale` (active pop, ~1.12), `pill` (sliding pill colour), `pill_radius/pad_x/pad_y`, `pill_slide`, `underline` (sliding underline colour), `past_color`, `future_alpha` (dim unspoken words), `outline_color`, `pop_dur`, `sweep` (karaoke fill) | word highlight |
| `highlight.future_color`, `highlight.pill_shape`, `highlight.glow`, `highlight.wiggle` **(Pro)** | colour of words not spoken yet (karaoke); `rect`/`marker`/`banner`/`ribbon`; active-word glow `{color, width, blur, strength}` (only while the word is active); active word tilts +-deg (alternating). `past_color` now also works without an active colour |
| `anim.in` | `none fade pop scale zoom slide_up slide_down slide_left slide_right bounce blur zoom_blur rotate drop typewriter` (aliases: `zoom_out`=zoom, `zoom_in`=scale, `slide`=slide_up) |
| `anim.out` | `none fade pop scale zoom slide_up slide_down blur` (alias `slide`=slide_down) |
| `anim.in_dur`, `out_dur`, `word_in` (reveal mode: `none fade pop scale zoom slide_up slide_down bounce blur drop typewriter`), `word_in_dur` | |
| `emphasis.numbers`, `keywords` (list), `color`, `scale`, `font`, `pill` | automatic emphasis |
| `emoji.enabled`, `map` ({keyword: emoji}), `position` (`above` / `after`), `size` | keyword emoji (colour vector, animated) |

Highlight presets (`options.highlight_presets`, apply `patch` with op `doc_style`): `none`, `color`, `scale`,
`pill` (Kotak geser), `sweep` (Karaoke isi), `underline`, `dim`, `reveal`, `one_word` (these 9 ids are kept as
they are for the current Animasi tab). `options.highlight_presets_all` = those 9 + (Pro) `box_pop` (Kotak muncul:
own pill per word that pops in), `marker` (Stabilo behind the active word), `color_underline` (Warna + garis),
`glow` (Kata menyala), `wiggle` (Goyang). Every preset that touches `highlight` also resets the Pro extras
(`glow: null, wiggle: 0, pill_shape: "rect", future_color: null`) unless it sets them.
Background presets (`options.box_presets`, unchanged ids for the current Gaya tab): `none`, `line`, `rounded`,
`page`, `word` (their patches now also set `shape: "rect", filled: true, border: 0, tilt: 0`).
`options.box_shapes` = those 5 + `marker` (Stabilo), `banner` (Spanduk), `ribbon` (Pita), `outline` (Bingkai),
`sticker` (Stiker); a shape preset sets the shape and its geometry only (colour, opacity, gradient, shadow stay).
`options.effect_presets` = one-click looks: `polos` (Tanpa efek: switches every Pro text effect off), `tebal_3d`,
`bayangan_panjang`, `garis_ganda`, `gradasi_api`, `gradasi_pelangi`, `neon`, `stabilo` (yellow marker box + dark
text), `spanduk_merah`, `stiker`, `miring`. All are deep-merged into `doc.style` like the other presets (the UI's
`doc_style replace` keeps explicit nulls).

### 6.2 Safe zones (fractions kept clear; third-party measurements, editable later)

`youtube` 5/10/5/5 % (top/bottom/left/right), `tiktok` 130/484/44/140 px @1080x1920, `reels` 14/35/6/6 %, `shorts`
180/390/60/120 px, `vertical_universal` (max of the three), `square`/`feed45` 5 %, `title` 5 %, `none`. The page
block is clamped inside; `auto` = tiktok (portrait), square (~1:1), youtube (landscape).

### 6.3 Per-word style keys (`word.style`, op `style`)

`color`, `active_color`, `outline_color`, `font` (family), `scale` (size factor; neighbours make room), `bold`
(-> heaviest bundled weight of the family; `\b1` for system fonts), `italic`, `pill` (always-on rounded highlight
behind the word, colour), `underline` (colour), `emoji` (shown above/after the word, pops in when spoken),
`uppercase`, `glow` (`{color, width, blur}`), (Pro) `case` (as `font.case`, per word) and `rotate` (degrees, the
word turns around its centre). Word text, hide and censor are edits (ops `text`, `hide`, `censor`), not styles.
Emoji typed into a word's text are moved to its `emoji` automatically (drawn in colour). `options.word_schema`
describes these keys for generic controls (same field format as 6.5, `key` instead of `path`).
A per-word `color` (or an emphasis colour) turns the gradient off for that word.

### 6.4 Gaya Pro effects: what each option draws (and costs)

All effects are plain libass (one ASS, same preview / overlay / band pipeline, straight alpha via the black/white
matte; composite vs libass burn max error 2,2/255 with every heavy effect on, `test_captions_style.py`). They work
in every timing mode and with every animation; every value is in the template/doc/range/page style JSON, so
templates, user templates, `doc_style`, `range_style` and `page_style` can use them. Defaults are all "off", so
existing templates and documents render byte-identical to before (checked on all 19 templates, 16:9 and 9:16).

| Option | How it is drawn | Cost (events on the 34,6 min tutorial, classic = 49 480) |
|---|---|---|
| `style.gradient` | the word in the first stop + `bands-1` copies clipped to bands (horizontal stripes for `vertical`, vertical slices of the word or of the whole line for `horizontal`) in interpolated colours. Copies hide while the word shows another colour (active, past, future, per-word), so the karaoke colour still works | 6 bands: 92 k events, compile ~4-6 s CPU |
| `style.outline2` | a copy under the text with `\bord` = outline + outline2 in `outline2_color` (fill too); the hard shadow moves to this copy | 58 k |
| `style.extrude` | `steps` (auto = depth/2 px, 2..24; a user value is clamped to 1..32 in the compiler and by the schema, so an imported style cannot explode the event count) copies offset along `angle`, far to near, colour `color` -> `color_end`, `\bord` >= half a step so the stack has no gaps | depth 10: 91 k, ~4-5 s CPU |
| `style.long_shadow` | like extrude but long and fading (`fade`: alpha 100 % -> 15 % at the end; alphas stack where copies overlap, so use a strong colour) | 42 px: 133 k, ~6 s CPU |
| `style.glow.strength` | alpha x min(1, strength); > 1 adds a tighter second glow | +1 layer |
| `highlight.glow` | glow copy that exists only while the word is active | 77 k |
| `highlight.wiggle`, word `rotate`, `box.shape: sticker` | `\frz` around the word centre (wiggle eases in/out with the pop timing, sign alternates per word) | 0 extra |
| `layout.rotate`, `rotate_jitter` | word/box/pill/emoji centres turned about the page centre + `\frz` on everything (rotate-in pages turn about the centre with `\org`) | 0 extra |
| `font.skew` | `\fax` on every text layer (libass shears around the line middle) | 0 extra |
| `box.shape` `marker` / `banner` / `ribbon` | `\p1` drawings regenerated per piece (growing boxes in reveal mode keep their rough edge: marker points sit at fixed px steps) | 0 extra |
| `box.border`, `filled: false` | `\bord` on the box drawing (transparent fill = outline-only frame) | 0 extra |
| `box.shadow` | offset + blurred copy of the box drawing under it | +1 per box |
| `box.gradient` | box copies clipped to horizontal bands | +bands-1 per box |

Layer order with Pro effects (pages that use outline2 / extrude / long shadow / box shadow): box shadow < box (+
gradient bands) < pills/underlines < glow < soft shadow < 3D / long shadow < outer stroke < text (+ gradient
bands, sweep) < emoji. Pages without them keep the classic numbers (pages never overlap in time).
Copies of the text (gradient bands, outline2, extrude, long shadow) merge consecutive pieces that are exactly
linear (a fade-in becomes one event), which halves their event count. Render time grows with events and stroke
size: 49 s portrait with gradient + outline2 + 3D + marker box + tilt/wiggle rendered in 15,7 s (one 60 s chunk;
classic 2,1 s); 34,6 min tutorial with gradient + outline2 20,5 s (classic 8,2 s), overlay 211 MB (139 MB).
Recommend the heavy text effects (3D, long shadow, gradient) for shorts; boxes, tilt, skew, case, fonts and
highlight looks are free.

Layout rules: outline2, skew, banner/ribbon ends, sticker tilt and per-word box borders get room between words
(page widths and line breaks account for them); the active-word underline sits below a thick outline / hard shadow
so it stays visible; `cap` (6.6) keeps the visual size when switching fonts.

### 6.5 Option metadata for generic controls (`options.schema`, `options.groups`)

`options.schema` lists every style key the editor may show (95 fields; Indonesian labels, no em dashes):
```jsonc
{"path": "style.extrude.depth",       // dot path into the template / doc.style
 "type": "number",                     // number | int | bool | color | colors | enum | font | object
 "label": "Kedalaman 3D", "group": "bayangan",
 "min": 2, "max": 40, "step": 1, "unit": "px", "decimals": 0,      // number / int
 "default": 10,                        // DEFAULTS value, or for a sub-field of an object: its default_on value
 "parent": "style.extrude",            // only for sub-fields of an effect object
 "show_if": {"style.extrude": "$on"},  // show when every condition holds
 "help": "..."}                        // optional helper line
```
- `number`/`int`: `min`, `max`, `step`, `unit` (`px` = px at 1080, `°`, `%`, `x`, `dtk`), `decimals`;
  `format: "percent"` = show value x 100 % (0..1 or 1..1,4 values).
- `color`: `#RRGGBB` or `#RRGGBBAA`; `alpha: true` = offer opacity. `colors`: list, `min_items`, `max_items`.
- `enum`: `options: [{id, label}]` or `options_ref: "anim_in"` (= `options.anim_in`, also `anim_out`, `word_in`,
  `safe_zones`). `font`: a family from the `fonts` action.
- `nullable: true`: null is a valid value (`null_label` = what to call it, e.g. "Ikuti template", "Tanpa").
- `object`: an effect that is null when off; switching it on writes `default_on` (a full object), its sub-fields
  (`parent`) edit keys inside it; switching off writes null (send the whole doc.style with `replace: true`, as the
  UI already does, so the null survives the merge).
- `show_if`: `{path: value}` (equal), `{path: [values]}` (one of), `{path: "$on"}` (not null / false / 0 / "").
- Groups (`options.groups`, in order): `huruf` Huruf, `isi` Warna teks, `garis` Garis tepi, `bayangan` Bayangan dan
  3D, `cahaya` Cahaya, `latar` Latar, `sorot` Kata aktif, `posisi` Posisi dan kemiringan, `animasi` Animasi.
- `options.word_schema`: the same format for per-word keys (`key` instead of `path`).
- `options.cases`, `options.font_categories`, `options.box_shapes`, `options.effect_presets`,
  `options.highlight_presets_all`: see 6.1 and 6.6.

### 6.6 Fonts

Bundled (`engine/ac/captions/fonts/`, 50 static OFL faces; libass loads them from `fontsdir`):
Anton, Archivo Black / ExtraBold, Bangers, Bebas Neue, Inter SemiBold / ExtraBold / Black, Lilita One, Montserrat
Bold / ExtraBold / Black, Plus Jakarta Sans Bold / ExtraBold, Poppins Bold / ExtraBold / Black, Titan One, and new:
Rubik Bold / Black, Nunito ExtraBold / Black, Fredoka SemiBold / Bold, Baloo 2 ExtraBold, Barlow Condensed Bold /
ExtraBold / Black, Oswald SemiBold / Bold, Sora Bold / ExtraBold, Outfit Bold / Black, Manrope Bold / ExtraBold,
Kanit Bold / Black, Righteous, Paytone One, Passion One, Russo One, Black Ops One, Bungee, Pacifico, Caveat Brush,
Knewave, Gochi Hand, DM Serif Display, Abril Fatface. Variable sources were instanced to static weights with
unique family names (`fonts/LICENSES/build_fonts.py`); Luckiest Guy and Permanent Marker are Apache-licensed in
google/fonts and were not added (OFL only). Every `font.family` value is one face; weights of a design share
`base` in the catalog.
Installed Windows fonts (`C:\Windows\Fonts` + per-user fonts, .ttf/.otf/.ttc, Latin, static) work by family name:
layout metrics come from the same file (fontTools + HarfBuzz), libass finds them through DirectWrite, word `bold` /
`italic` use the family's real Bold / Italic face for metrics (`\b1` / `\i1`). Variable system fonts, emoji and
symbol fonts are not listed.
Each ffmpeg/libass start loads only the bundled faces its ASS uses (`render.fonts_rel(dir, ass)` ->
`layout.fontset_dir`: hard-linked subset folder): with 50 faces in `fonts/` this keeps preview start-up at the
old cost (measured: subset 83 ms CPU vs all 50 faces 102 ms vs the old 17 faces 87 ms).

### 6.7 Template library (`templates/*.json`, `gallery.py`)

51 built-ins = the 19 classic ones (files unchanged, so their ASS output is byte-identical; groups come from
`gallery.CLASSIC_GROUPS`) + 32 library templates that carry `"group": "<id>"` in their JSON and use the Gaya Pro
keys. Names and styles are original (inspired by popular short-form looks, no creator names). Primary group
(`*` = classic):

| Group (`id`) | Templates |
|---|---|
| Shorts Viral (`shorts_viral`) | Kuning 3D, Gradasi Api, Stiker Viral, Kotak Muncul, Goyang Hijau, Hook Tengah, Hormozi Kuning*, Bold Pop*, Boxed*, Garis Bawah*, Hijau Viral*, Satu Kata* |
| Tutorial (`tutorial`) | Stabilo Tutorial, Panel Terang, Langkah Klik, Tutorial Bersih*, Tutorial Sorot*, Typewriter* |
| Podcast (`podcast`) | Kartu Podcast, Obrolan Santai, Podcast* |
| Jualan/Promo (`jualan`) | Promo Spanduk, Flash Sale, Pita Diskon, Jualan* |
| Sinematik (`sinematik`) | Layar Lebar, Judul Emas, Bayangan Dramatis, Sinematik* |
| Edukasi (`edukasi`) | Fokus Belajar, Catatan Stabilo, Papan Kapur, Ali Abdaal* |
| Minimal (`minimal`) | Huruf Kecil, Bingkai Tipis, Teks Gelap, Minimal Putih*, Subtitle Klasik* |
| Fun/Komik (`fun`) | Komik Ledak, Stiker Lucu, Komik*, Mr Beast*, Karaoke* |
| Gaming (`gaming`) | Neon Gamer, Esports, Neon* |
| Berita (`berita`) | Berita Terkini, Kabar Kilat |
| Aesthetic (`aesthetic`) | Pastel Lembut, Tulisan Tangan, Senja |

A template can sit in more groups (`groups`: its `group` + group ids in `tags`, e.g. Boxed is also Berita).
Every template was rendered over a light screen-recording frame in 9:16 and 16:9 and checked by eye
(screenshots `v3_gallery_library_*.png` in the gitignored `--shots` folder); light-coloured looks without a box carry a stroke or a dark soft shadow.

Readability check (`gallery.ensure_readable`, used by `template_mix` / `template_random`; every built-in passes it
unchanged):
- opaque-ish box (filled, opacity >= 0,55): text vs box contrast >= 4,5:1 (WCAG ratio) else the text turns black or
  white and the gradient is dropped; gradient stops < 3:1 vs the box drop the gradient;
- no box: the text needs a halo (stroke >= 2, glow, 3D, long shadow or soft shadow) else a 4 px contrasting stroke
  is added; a stroke < 3:1 against the fill flips to black/white;
- active word: >= 3:1 against what is behind it (pill, box, a stroke >= 4 px, else the video) and visibly different
  from the text colour (RGB distance, so yellow on white counts as different) unless a pill/glow/underline/dim/scale
  marks it; `future_alpha` >= 0,45;
- size: cap height (`font.size` x the font's cap ratio) between 18 and 120 px@1080 (170 in one-word mode).
Fixes are returned as Indonesian lines in `fixes`.

---

## 7. Applying results in Premiere (host side, `panel/host/34_captions.jsx`, ES3)

Verified helpers in `panel/host/20_graphics.jsx` (premiere_graphics_api.md): `gxPlaceOverlay`, `gxRelinkOverlay`,
`gxFindVideoTrack`, `gxClearTrack`, `gxGet/SetLinearCompositing`, `gxInsertMogrt`, `gxPopIn`, `gxCaptionTrackFromSrt`.

### 7.1 Overlay plan (`kind: "overlay"`)

```js
// bac_captions_applyOverlay(path, xNorm, yNorm, relink, seqId, trackName) -> {mode, track, start, end, nodeId?}
// (sketch; the real one in panel/host/34_captions.jsx also refuses a trackName that is not ours = plan.track,
//  and its messages go through acT("captions...") so they follow the panel language)
function bac_captions_applyOverlay(path, xNorm, yNorm, relink, seqId) {
    try {
        var seq = acSeq(seqId);
        if (!seq) return acErr("Buka sequence dulu.");
        if (!new File(path).exists) return acErr("File overlay tidak ditemukan.");
        if (relink === true || relink === "true") {
            var hit = gxFindVideoTrack(seq, GX_CAPTION_TRACK);
            if (hit && hit.track.clips.numItems > 0) {
                var r = gxRelinkOverlay(seq, GX_CAPTION_TRACK, path);        // ~15 ms, keeps the clip
                if (r.ok) return acOk({ mode: "relink", track: hit.index, start: r.start, end: r.end });
            }
        }
        var bin = tlFindBin("Klipora Captions", true);
        var p = gxPlaceOverlay(seq, path, { startSec: 0, xNorm: Number(xNorm), yNorm: Number(yNorm), scale: 100, bin: bin });
        if (!p.ok) return acErr("Overlay gagal ditaruh: " + p.err);
        return acOk({ mode: "place", track: p.track, nodeId: p.nodeId, start: p.start, end: p.end });
    } catch (e) { return acCatch(e, "bac_captions_applyOverlay"); }
}
```
- Track `Klipora Captions` (top, created once by `gxEnsureVideoTrack`); `gxPlaceOverlay` clears only that track.
- Use `plan.relink` as given (geometry unchanged); when it is false, place again (replace).
- Never import `.webm`; only our `.mov` / `.png` / `.srt` / `.mogrt` (exists check first).
- Old project items `<seq>_captions_v<n>.mov` in the bin "Klipora Captions" were created by us; deleting the
  unused ones is allowed (never others). The files are kept/deleted by the engine (last 3).
- Colours match the engine preview exactly only with `compositeLinearColor = false` (lab: default true, shadows /
  semi-transparent boxes differ up to 63 levels). Offer a toggle "Samakan warna dengan preview" that calls
  `gxSetLinearCompositing(seq, false)` and explains it affects the whole sequence. Never flip it silently.
- 30 fps overlay on a 120 fps sequence: each overlay frame is held 4 frames (lab: exact).

### 7.2 Caption track plan (`kind: "caption_track"`)
`gxCaptionTrackFromSrt(seq, plan.path, tlFindBin("Klipora Captions", true))` -> Boolean. No styling API exists.

### 7.3 MOGRT plan (`kind: "mogrt"`)
Create/find the video track `Klipora Captions (Editable)` (`gxEnsureVideoTrack(seq, plan.track)`), clear it
(`gxClearTrack`), then per item (chunk ~25 per evalScript; ~260 ms each):
`var c = gxInsertMogrt(seq, it.path, it.start, it.end, idx); if (it.pop) gxPopIn(c, {pivot: it.anchor});`
then set Motion Position to `it.position` (`tlComp(c, "AE.ADBE Motion").properties[0].setValue(it.position, true)`).
`anchor` = the template's native text centre (0.5, 0.871, measured at 1080x1920), `position` = our page centre.

---

## 8. Preview loop (editor)

- Use `AC.engine.worker({tool: 'captions', action: 'preview', params: {t, mode: 'frame'}})`: the worker keeps the
  fonts, emoji glyphs, templates and the frame cache warm. Measured through the worker on the 49 s file: first
  call of a fresh worker ~300-500 ms (imports, fonts), new playhead position 130-165 ms (decode + burn in one
  ffmpeg pass; the frame is cached at the same time), cached frame 70-90 ms, an edit op + preview ~6 + ~90 ms.
- Debounce edits ~120 ms; send `doc {ops}` then `preview` (both worker, in order). Show the previous PNG until the new
  one arrives; ignore results whose `t` is no longer the playhead.
- For scrubbing / an exact Premiere composite: `bac_exportFrame(t, path)` once per playhead stop, then `preview
  {t, frame: path}`; or `mode: 'overlay'` and stack the transparent PNG over your own image.
- `preview.pages` = page ids visible at t (highlight them in the page list). Page geometry for drag-to-position:
  `page.y` (% of H); send `page_pos` with the new y on drop.
- Thumbnails: `gallery` once (cached), `gallery {t}` to render the picker over the user's footage.

---

## 9. Measured numbers (this PC, 2026-10-05)

| What | Result |
|---|---|
| `doc` 49 s / 2 min / 34.6 min (cached transcript) | 0,03-0,3 s; 34.6 min: 2803 words -> 761 pages (Tutorial Bersih), 1358 (Hormozi) |
| edit op + re-paginate, 34.6 min | ~40 ms |
| compile ASS, 34.6 min | 0,9 s (49k events, Tutorial Bersih) - 2,7 s (90k, Karaoke) |
| render, 49 s timeline | 0,4-1,9 s |
| render, 34.6 min, band 624x120, qtrle | 10,5 s cold (35 chunks, 6-8 parallel), 124 MB; after one edit 3,5 s (1 chunk re-rendered); single process was 37 s |
| render accuracy | composite of the overlay over the frame vs libass burn (RGB): max error 2,1/255 |
| chunked vs single-pass render | bit-identical frames, same frame count (62 273) |
| preview (worker) | 70-90 ms cached frame, 130-165 ms new frame, overlay PNG ~170 ms |
| autoposition | 49 s 1,0-1,4 s, 2 min 4,3 s, 34.6 min 34 s (decode-bound) |
| gallery, 19 templates | 0,8-2,8 s cold, ~3 ms cached |
| burn 3 s loop | 0,8 s |
| AI cleanup (local proxy, `grok-fast`, 80 words, live once) | 6 suggestions (case/punct), source ai |
| Gaya Pro, classic templates (A/B old vs new code, same machine load) | ASS byte-identical for all 19 templates (16:9 + 9:16); compile CPU equal within noise (34,6 min Tutorial 1,0-1,7 s, Karaoke ~4 s); render 49 s 2,1 vs 2,2 s, 34,6 min 8,2-8,7 vs 8,8-9,0 s |
| Gaya Pro, preview (cached frame, 50 bundled fonts) | 89-105 ms vs 90-97 ms before (per-ASS font subset); Pro looks 88-127 ms |
| Gaya Pro effects, compile 34,6 min Tutorial | gradient 4-6 s CPU (92 k events), 3D 3,7-5,4 s (91 k), long shadow ~6 s (133 k), outline2 1,5-2,2 s (58 k); box shapes / tilt / skew / case ≈ classic |
| font catalog | Windows scan 0,6 s cold (429 faces), ~20 ms warm; ~295 usable families on this PC |

---

## 10. Gotchas / UNVERIFIED (for the Premiere verifier)

1. **Band clip placement** relies on the clip being imported at native size (lab default). If the user's
   preference "Default Media Scaling" is "Scale to frame size" / "Set to frame size", a band clip would be scaled up.
   Check; the fallback is `render {band: "off"}` (full-frame overlay, no positioning).
2. `changeMediaPath` relink to a file with a different frame size is UNVERIFIED -> the engine sets
   `relink: false` whenever the band changed.
3. MOGRT: multi-line text uses `\r` between lines (UNVERIFIED; `single_line: true` avoids it), the Motion mapping
   `anchor/position` for sequences other than 1080x1920 is UNVERIFIED, fonts must be installed.
4. SRT `<font color>` rendering on Premiere caption tracks is UNVERIFIED.
5. Safe-zone percentages are third-party measurements.
6. Face avoidance was verified on synthetic frames only (research); the screen recordings contain faces only inside
   web pages.
7. Premiere keeps media files open: the engine never overwrites a version; renders are versioned `_v<n>`.
8. The preview uses sRGB blending; Premiere's default linear compositing makes soft shadows lighter (7.1).
9. Workdir collisions: two sequences with the same name share a workdir (and so `captions.json`); the doc stores
   `seq.id` but does not refuse a different id.
10. Gaya Pro effects exist only in the overlay render, preview, gallery and burn loop. SRT ignores them; MOGRT keeps
    its own subset (font, colours, stroke, box). Not yet checked inside Premiere (UNVERIFIED): heavy semi-transparent
    stacks (long shadow, glow, marker at 60 %) under Premiere's default linear compositing (expected lighter, as 8).
11. Installed Windows fonts are found by libass through DirectWrite on this PC only; a document that uses one renders
    with the default font on a PC without it (the layout falls back too, so nothing overlaps).

---

## 11. Animation studio and sound effects per word (`anim.py`, `sfx.py`)

Panel: `panel/js/tools/captions_anim.js` (`AC.cap.animUI`, the Animasi tab: Pustaka / Studio / Efek suara; the classic
pickers stay inside "Animasi dasar"), host `panel/host/42_caption_sfx.jsx`. Tests: `engine/tests/test_captions_anim.py`
(offline, ~13 s), `tools/ui_tests/captions_anim.mjs` (`--engine live`, 9:16 sequence). Shots: `v3_anim_*.png` in the
`--shots` folder (default `docs/shots`, gitignored) (`v3_anim_strips_in.png` / `v3_anim_strips_other.png` = frame strips of 18 presets rendered through the real compiler).

### 11.1 Style key `anim.fx` (template / doc / range / page style)

```jsonc
"anim": {"fx": {"in": "pop_kata",                                   // preset id
                "active": {"preset": "aktif_pop", "dur": 0.3},      // preset + overrides (dur, stagger, order, unit, release, tracks)
                "out": {...full inline spec...},                     // Studio / custom animation (inline: survives a lost file)
                "loop": null}}                                       // null / missing = off
```
- `fx.in` / `fx.out` REPLACE the classic `anim.in` / `anim.out` on that page; `fx.active` runs while a word is spoken
  (karaoke / word / reveal modes; adds to the classic highlight colour / pill / `highlight.scale`; neighbours make
  room when it scales up); `fx.loop` runs while the page is on screen. Classic `word_in` (reveal) still applies.
- No `fx` (or `{}` / null) = the classic path, byte-identical ASS (checked on every built-in template, 16:9 + 9:16).
- In `reveal` mode a per-word entrance starts when the word is spoken (stagger ignored).
- Cost: animated windows are sampled at <= 35 ms per piece (like the classic compiler). In / out / active add
  ~10-30 events per word; loop adds ~28 events per word per second for the whole page time (recommend it for shorts).

### 11.2 Animation spec

```jsonc
{"id": "pop_kata", "label": "Pop per kata", "group": "in" | "active" | "out" | "loop",
 "dur": 0.32,            // s (loop: one period). in/out: per word; dur + stagger x (n-1) is squeezed into 60 % / 40 % of the page
 "stagger": 0.06,        // s between words (loop: phase shift per word)
 "order": "forward" | "reverse" | "center" | "edges" | "random",
 "unit": "word" | "page",// page = the whole block (+ box, pills, emoji) moves as one around the page centre
 "release": 0.15,        // active: seconds to ease back to rest after the word
 "tracks": {"scale": [{"t": 0, "v": 0.2, "e": "back"}, {"t": 1, "v": 1, "e": "linear"}], ...}}
```
Tracks (`tracks` in the `anim` action): `scale` (x, 0..5), `opacity` (0..1), `x` / `y` (% of the word's font size,
+ = right / down), `rot` (degrees, + = clockwise, unlike `layout.rotate`), `blur` (px @1080), `spacing` (letter
spacing px @1080, a per-piece `\fsp` tag; neighbours do not move), `color` (`#RRGGBB` or null = the word's own colour,
RGB interpolation; turns the gradient bands off while it differs). Key `t` = 0..1 of `dur`; `e` = easing from this key
to the next: `linear in out in_out back back_in elastic spring bounce steps:N`. Before the first key the first value
holds, after the last the last. Values are clamped and bad easings become linear (`anim.norm_spec`). Roles combine
multiplicatively (scale, opacity) / additively (x, y, rot, blur, spacing) with the classic page animation and pop.

Library (40, `anim.PRESETS_LIST`): Masuk 17 (pudar_halus, pop_kata, pantul_masuk, naik, turun, dari_kanan, dari_kiri,
zoom_masuk, blur_fokus, putar_masuk, pegas_masuk, ketik_kata, renggang_masuk, kilat_warna, jatuh_putar, glitch_masuk,
blok_naik [page unit]), Kata aktif 9 (aktif_pop, aktif_lompat, aktif_goyang, aktif_denyut, aktif_miring, aktif_pegas,
aktif_kilat, aktif_renggang, aktif_naik), Keluar 8 (keluar_pudar, keluar_turun, keluar_naik, keluar_kecil,
keluar_zoom, keluar_blur, keluar_putar, keluar_kiri), Loop 6 (loop_melayang, loop_gelombang, loop_goyang,
loop_denyut, loop_getar, loop_kedip).

### 11.3 Actions

| Action | Params | Result |
|---|---|---|
| `anim` | | `{groups [{id,label}], presets [spec], user [spec + user: true], tracks [{id,label,unit,min,max,step,decimals,format?,default}], easings, orders, units, defaults, path}` |
| `anim_save` | `{anim: spec, name, id?}` / `{delete: "u_x"}` | `{anim, id: "u_<slug>", user, summary}` (`BAD_ANIM` without keyframes) / `{deleted, user}`. File `%APPDATA%\AutoCutBOT\caption_anims.json` `{v, kind, anims}`; `u_*` ids resolve like presets |
| `anim_sprites` | `ids` (list or `"all"`), `specs {key: spec}`, `frames` (16), `size` ([176, 64]), `force` | `{sprites: {key: {png, frames, dur, w, h, group}}, errors}`: one PNG strip (`frames` cells side by side) of the demo phrase "Halo teman semua!" (white, black outline, yellow active word) rendered by libass through the real compiler. Cached by content in `%LOCALAPPDATA%\AutoCutBOT\captions\anim_sprites\` (40 presets cold ~2,5 s with 6 threads, then a few ms). Panel CSS: `background-size: frames*100%`, `steps(frames, jump-none)` |
| `sfx_library` | | `{sounds: [{id, label, path, dur, lead, source builtin|user}], rules [{id,label,default}], defaults, track: "Klipora SFX"}` |
| `sfx_add` | `{path, name?}` / `{delete: "u_x"}` | user sound (first 8 s, normalized) into `%APPDATA%\AutoCutBOT\caption_sfx\u_<slug>.wav` -> `{sound, sounds}` |
| `sfx_plan` | `rules`, `volume`, `max_per_min`, `min_gap`, `offset`, `ai` | `{hits: [{t, sound, gain, rule, word, text, at}], rules, params, ai, stats {candidates, hits, dropped_gap, dropped_rate, per_rule}, summary}` (`t` = where the sound starts, `at` = the word time) |
| `sfx_render` | plan params, or `hits` (edited list); `mode` `mixed` (default) / `clips`; `out_dir` | mixed: `{plan: {kind: "audio_track", track: "Klipora SFX", path: "<seq>_sfx_v<n>.wav", start, duration, version, previous}, hits, stats, peak_db, summary}`; clips: `{plan: {kind: "audio_clips", track, items: [{t, path, sound}], version}}`. `NO_SFX` when nothing matches or every hit names a sound that no longer exists ("Suara efek yang dipilih sudah tidak ada."); hits with a deleted sound are skipped and counted in `dropped` (summary: ", k dilewati karena suaranya sudah dihapus"). Writes `doc.sfx = {version, path, hits, start}` |

### 11.4 Sound effects

- Library (12 WAVs in `engine/ac/assets/sfx/caption/`, 48 kHz mono 16-bit, made by `sfx.build_library()` from ffmpeg
  lavfi `aevalsrc` recipes, no downloads): `pop whoosh swoosh ding click boom riser glitch cash bubble typewriter
  camera` (Pop, Whoosh, Swoosh, Ding, Klik, Boom, Naik (riser), Glitch, Kaching, Gelembung, Mesin ketik, Kamera).
  Normalized to -14 dBFS gated RMS (about -14 LUFS for short hits), peaks <= -1 dBFS. `lead`: whoosh 0,25 s, swoosh
  0,12 s, riser 1,3 s start earlier so the peak lands on the word.
- Rules (priority when one word matches several): `angka` (digit in the word; default on, Kaching -3 dB) > `penting`
  (template emphasis / per-word colour, scale > 1,05, bold, pill, underline, glow, font; with `ai: true` also the AI
  emphasis task, cached, 1 request per doc; AI off or proxy down = the task's rule fallback, longest content word per
  line; default on, Pop 0 dB) > `emoji` (word shows an emoji; off, Gelembung -2 dB) > `halaman` (every page start;
  off, Swoosh -8 dB).
- Defaults: `volume` -6 dB (added to each rule gain), `max_per_min` 12 (any 60 s window), `min_gap` 0,6 s,
  `offset` -0,03 s.
- Mixed WAV: from the first hit to the last hit end, written in 10 s blocks (a 35 min timeline is ~200 MB at worst),
  `ensure_free` first, last 2 versions kept (the linked one never deleted).

### 11.5 Host (`42_caption_sfx.jsx`, ES3)

`bac_csfx_apply(path, start, seqId)`: finds or appends the audio track named "Klipora SFX" (QE `setName` fallback, as
in the profanity tool; when no track has that name, the last audio track holding ONLY our clips is reused, so a failed
rename never stacks a second copy of the mix), imports the WAV into bin "Klipora Captions" after `File.exists`, then
checks overlap of [start, start + item duration] against the other clips on that track and refuses BEFORE removing
anything (a refused or failed apply keeps the placed SFX), then removes only OUR clips (`*_sfx_v<n>.wav` /
`sfx_*.wav`) and `overwriteClip(item, start)` -> `{ok, mode place|replace, track, removed, start, end, path, named}`.
`ok: false` = the placed clip was not found afterwards; the panel then says "Efek suara belum terpasang di timeline"
instead of the success text. The steps are separate undo entries in Premiere (no single Ctrl Z claim in the UI:
"Terapkan lagi untuk mengganti, atau hapus track"). Also `bac_csfx_clips(items, first, seqId)` (per-hit mode; chunk
the items, `first: true` clears; other clips' ranges are read on every chunk before anything is removed and checked
against each hit's real duration), `bac_csfx_status(seqId)`, `bac_csfx_clear(seqId)`. Other tracks are never touched;
nothing is saved. UNVERIFIED in live Premiere 26.2.2: naming of a new audio track, and `overwriteClip` of the mono WAV
on a stereo track.

---

## 12. Style from image and Brand Kit (`stylist.py`, `brandkit.py`)

"Tiru gaya dari gambar" turns a screenshot of a caption the user likes into a draft style; the Brand Kit stores brand
colours / fonts / words once and restyles any captions doc with them. Panel: `panel/js/tools/captions_brand.js`
(`AC.brandKit`). User guide (ID): `docs/tools/captions_brand.md`. Tests: `python engine/tests/test_captions_stylist.py`
(offline, ~15 s; `--live` = AI round trip, <= 4 requests, cached) and
`node tools/ui_test.mjs tools/ui_tests/captions_brand.mjs --engine live` (33 checks, AI off).

### 12.1 `style_from_image`

| Param | Default | Meaning |
|---|---|---|
| `image` | | `data:image/...;base64,...` (the panel sends a PNG downscaled to <= 1600 px) |
| `path` | | image file instead of `image` |
| `clipboard` | false | read the Windows clipboard (`PIL.ImageGrab`: a copied picture or a copied image file) |
| `frame`, `t` | false, `seq.player` | use the decoded video frame at sequence time `t` (needs `seq`; e.g. a reference clip with burned-in captions) |
| `ai` | true | ask the vision slot of `ac.ai.client` (one request; skipped when AI is off or no provider has a vision model) |
| `cache` | true | AI answers are cached by image (`%LOCALAPPDATA%\AutoCutBOT\ai_cache`), also while AI is switched off |
| `render` | true | render our version for the side-by-side compare |
| `size` | | `[W, H]` of the user's sequence: used for our render only when the image is a crop (not a full frame) |
| `portrait` | false | crop + no `size`: render in 1080x1920 instead of 1920x1080 |
| `name` | "Gaya dari gambar" | name put into the returned `template` |
| `system_fonts` | false | also pick installed Windows fonts (default: bundled OFL fonts only, so the result renders on any PC) |

Pipeline (no OCR anywhere): copy of the image -> `<workdir>\stylist\src_<hash>.png` -> **local measurement**
(always): word blobs of hard edges in two passes, caption block = biggest / hardest-edged / centred text (lines grouped),
background box (one flat colour with a sharp rim covering a big part of the block), text fill = the colour that is wrapped
by another colour (the outline) else the deepest big colour (distance transform), active word = another deep distinct
colour (a pill when it wraps part of the fill), outline colour + width (stroke area / glyph perimeter; anti-aliased edges
rejected), drop shadow (outline that is only below the glyphs), case (row density in the top quarter of the cap height),
lines, words per line (column gaps), weight (stem thickness / cap height), width (glyph boxes), size (cap height / short
side) and position (block centre, only when the image has a frame aspect: 16:9, 9:16, 4:3, 1:1, 4:5, 21:9, 2292x960...)
-> **AI** (optional): the whole image (<= 1024 px) + a zoomed crop of the measured block, strict JSON prompt (text,
colours, outline, shadow, background, case, font category / weight / width, italic, position, align, size class, words
per line, lines, highlight style, animation guess, full frame) -> `norm_ai` (unknown values dropped) -> **merge** ->
**mapping**.

Merge rules: colours within deltaE 30 -> the measured one (exact); far apart -> the measurement when it was taken on a
clean background (conf >= 0,6; vision models are weak at exact colours), else the AI colour. Position / size / outline
width: measured when the image is a full frame. A measured box beats an AI "no box". Other attributes: the higher
confidence wins (same value: +0,1). Disagreements are listed in `notes`.

Mapping (`to_style`): `base` = closest built-in template by box kind, case, highlight kind (+ aspect, no heavy Pro
effects); `style` = explicit doc.style patch: `font.family` (catalog font by category / width / weight, `pick_font`),
`font.case` + `font.uppercase`, `font.size` (cap height x 1080 / the font's `cap`), `style.fill / outline /
outline_color / shadow*`, every Pro text effect off (`gradient`, `extrude`, `long_shadow`, `glow`, `outline2`,
`color_cycle`), `box` (enabled, per, shape, radius, colour, opacity), `highlight` (colour / pill / underline / glow /
scale, Pro extras reset), `timing.mode` (`line` when nothing is highlighted), `layout.y / align / max_lines /
max_words / rotate 0`, `anim.in` (AI guess).

```json
-> {"base": "boxed", "style": {...doc.style patch...}, "template": {...base + patch, name, description...},
    "attrs": {"fill": {"value": "#111111", "conf": 0.8, "src": "lokal"}, "box": {...}, ...},
    "chips": [{"id": "fill", "label": "Warna teks", "value": "#111111", "conf": 0.8, "src": "lokal", "color": "#111111"},
              {"id": "box", "label": "Latar", "value": "Per baris", ...}, ...],
    "source": "local" | "ai" | "mixed", "ai": {"used": false, "source": "off" | "ai" | "cache" | "fallback", "profile", "model"},
    "fidelity": "tinggi" | "sedang" | "rendah", "conf": 0.56, "notes": [], "warnings": [],
    "image": "<workdir>\\stylist\\src_<hash>.png", "text": "Rahasia cuan" | null, "full_frame": true, "frame": [1080, 1920],
    "compare": {"theirs": "...src_<hash>.png", "ours": "...ours_<hash>_<base>_<n>.png", "w": 1080, "h": 1920,
                "theirs_zoom": "...zoom_t_*.png", "ours_zoom": "...zoom_o_*.png"},
    "summary": "Gaya dari gambar: Montserrat Black, 10 atribut (tanpa AI, akurasi lebih rendah)"}
```
`fidelity`: `rendah` = no AI (local measurement only), `sedang` = AI with average confidence < 0,6, `tinggi` otherwise.
Chip ids: `fill active stroke box case font size position words highlight anim`. Our render is drawn over a blurred,
darkened copy of their image (same aspect); the zoom pair crops the same caption region from both (480 px wide).

Apply in the editor: ops `[{op: "template", id: base}, {op: "doc_style", style, replace: true}]` (one undo step).
Save: `save_template {template, name}`. Errors: `BAD_PARAMS` (no image), `NO_IMAGE` (path missing), `BAD_IMAGE`
(unreadable / < 48 px), `NO_CLIPBOARD`, `NO_CAPTION_FOUND` (no text block and no AI), `NO_SEQ` (`frame` without seq).
Files in `<workdir>\stylist\` (last 12 inputs, 24 renders / zooms / backgrounds kept).

### 12.2 `brandkit`: kits in `%APPDATA%\AutoCutBOT\brandkit.json`

```jsonc
{"v": 1, "active": "tokokita", "kits": [{"id": "tokokita", "name": "TokoKita",
  "colors": {"primary": "#00A86B", "accent": "#FFD400", "text": "#FFFFFF", "background": "#111111"},
  "fonts": {"heading": "Rubik Black", "body": "Plus Jakarta Sans Bold"},   // catalog families (heading is used)
  "logo": "C:\\...\\logo.png",            // stored only, never rendered in captions
  "words": ["TokoKita", "reseller"],      // brand words: glossary + emphasis keywords
  "emoji": "none" | "sedikit" | "banyak", "tone": "santai" | "profesional" | "semangat" | "lucu" | "edukatif",
  "created": "...", "updated": "..."}]}
```

| `params.op` | Params | Result |
|---|---|---|
| `get` (default) | | `{kits, active, file, tones: [{id, label}], emoji_levels, defaults: {colors, fonts}}` |
| `set` | `kit` (full or partial; with `id` = update, keeps the other fields), `activate` | `{kit, kits, active, warnings}`. New id = slug of the name (`_2`, `_3` on clashes). The first kit becomes active. Bad colours / unknown fonts -> `warnings` (colour kept / font kept but captions fall back). Error `BAD_KIT` (no name, > 40 kits) |
| `delete` / `activate` | `id` | `{kits, active}` (`BAD_KIT` for an unknown id) |
| `apply` | `id` (default active) or `kit` (inline), `commit` | `{kit: {id, name}, ops, glossary_added, rebuild, patch}`; `commit: true` applies the ops in the engine (`doc`, `notes`). `NO_KIT` when there is none |

`apply` ops: `doc_style` (`replace: true`, the current doc.style + the kit patch), `params` (glossary + the brand
words that were missing), `clear_emoji` (emoji `none`) or `auto_emoji {density}`. `rebuild: true` = brand words were
added to the glossary: run `doc` with `seq` and `transcribe: false` (the panel calls `cap.text.refreshWords()`), so
their spelling is fixed on the cached transcript. The patch recolours the effective look (template + doc.style)
without changing its structure: `style.fill` = text, `font.family` = heading (when installed), active colour = accent,
pill = primary (text on it = readable black/white), underline = accent, box colour = background (light box + light
text -> dark text), glow / gradient / colour cycle / 3D recoloured when the look has them, `emphasis.keywords` +=
brand tokens (single words >= 3 letters, no stop words), `emphasis.color` = primary, `emphasis.scale` by tone
(profesional 1,0, edukatif 1,06, santai 1,08, lucu 1,12, semangat 1,15).

### 12.3 `brand_emphasis`: reviewable per-word emphasis

Params: `id` (kit, default active) or `kit` (inline), `brand: false` (ignore the kit), `ai` (true: the existing
`emphasis` AI task, cached, `tasks.run`), `max_per_page` (1; brand words come on top), `commit`.
Picks: brand words (always), numbers / prices, the AI's key words (+ its emoji), else per page the longest content
word (>= 5 letters, no stop words, no reduplicated fillers such as "teman-teman"). Words the user already coloured are
skipped. Style per item: brand words in the kit primary, the rest in the accent (swapped when it equals the text or
active colour), `scale` by tone (1,12 for numbers), emoji only when the kit's emoji policy allows (`sedikit` = at most
one every 15 s, `banyak` = all, `none` = no emoji).
```json
-> {"items": [{"id": "e1", "ids": ["314743ca9d:6"], "text": "reseller", "t0": 4.91, "t1": 5.4, "why": "brand" | "ai" | "angka" | "kata",
               "label": "Kata brand", "emoji": null, "on": true, "page": "p:314743ca9d:6", "style": {"color": "#00A86B", "scale": 1.15}}],
    "ops": [{"op": "style", "ids": ["314743ca9d:6"], "style": {...}}], "source": "ai" | "cache" | "mixed" | "fallback",
    "stats": {"words": 80, "items": 28, "brand": 2, "ai": 0, "angka": 0, "kata": 26}, "kit": {"id", "name"} | null,
    "warnings": [], "summary": "28 kata penting disarankan (aturan, tanpa AI)"}
```
The panel shows the items with checkboxes and sends the `ops` of the checked ones through `cap.op` (one undo step).

### 12.4 Measured (this PC, 2026-10-05)

| What | Result |
|---|---|
| Round trip, local only: 12 of our templates x 16:9 + 9:16 rendered on a neutral background (24 images, 94 checks) | text colour (deltaE <= 15) 91 % (misses: Minimal Putih 9:16 soft shadow, Neon 9:16 glow), box yes/no 100 %, case 100 %, position (<= 8 %) 100 % |
| Round trip with AI (local proxy profile `grok_local`, vision slot `grok-auto`, whole image + zoom crop), 4 templates | after the merge: text colour, box, case, position 4/4 each; raw AI text colour 3/4 (Karaoke white read as yellow, the measurement won) |
| AI without the zoom crop (first try, 1 request) | text colour and position wrong (read the outline as the fill). The crop fixed it |
| AI requests used while building + testing | 5 (limit 10); every answer cached |
| Real busy frame (screen recording with a big web headline under the caption) | local block detection picks the headline in most 16:9 cases: crop around the caption or use AI |
| Time | local analysis 0,5 s at 1080x1920 (+ ~1 s imports on the first call); whole action without AI ~2,4 dtk in the panel (own process, compare render included); AI 6 s per image (local proxy), first call 31 s |
| `brand_emphasis` rules, 80 words | 0,08 s |

### 12.5 Gotchas / UNVERIFIED

1. UNVERIFIED in CEP: the `paste` event for Ctrl+V (Ctrl+V key interest is registered), drag and drop from Explorer,
   and the file picker. The "Tempel" button reads the Windows clipboard in the engine (`PIL.ImageGrab`) and does not
   depend on CEP clipboard support (not run live either).
2. Only the local proxy (`grok_local`) vision route was tested; other providers' vision models are untested (see AI_PROVIDERS.md).
3. Local measurement is reliable on flat / dark backgrounds and for our kind of captions; busy frames, colour-cycling
   words, gradients (one band is measured), emoji-heavy captions and glow lower the accuracy. The result is always a
   draft: the chips show the confidence and the user can tweak in Gaya.
4. The font is matched by category / weight / width, never recognised by name (no OCR / font ID): expect "a similar
   font" from the bundled catalog.
5. Brand words match one word at a time in the template emphasis rule (a multi-word brand highlights each word).
6. The logo / watermark path is stored only.

---

## 13. "Gaya Saya" library and the last used look (`library.py`)

Tests: `python engine/tests/test_captions_library.py` (offline, `%APPDATA%` sandboxed, ~2-7 s) and
`node tools/ui_test.mjs tools/ui_tests/captions_v3.mjs --engine live` (panel, section "Tests" of `docs/tools/captions_ui.md`).
Files (all in `%APPDATA%\AutoCutBOT`): `caption_templates\<id>.json` (user templates, same format as `save_template`),
`caption_library.json` (`{"v": 1, "items": {id: {created, updated, favorite, aspect}}}`, times ISO WIB),
`caption_last.json` (`{"v": 1, "vertical" | "horizontal" | "square": entry}`). Aspect class = `layout.aspect_kind`
(ratio < 0,85 vertical, < 1,2 square, else horizontal). No `seq` needed for any of these actions.
`caption_library.json` / `caption_last.json` are read with `_read_store` (retries ~1 s when a read overlaps another
process's `os.replace` on Windows; a file that stays locked raises `FILE_BUSY` instead of being treated as empty, so a
sharing race can never wipe the library) and written with `_write_store` (retries `PermissionError` from `os.replace`
while another process has the file open).

### 13.1 `library`

| `params.op` | Params | Result |
|---|---|---|
| `list` (default) | `thumbs` (true) | `{templates: [row], n, favorites}`: favourites first, then newest change first |
| `save` | `name`, `from_doc` (default true: the doc's template + `doc.style` [+ `patch`], explicit nulls kept) or `template` (dict, sanitised like an import), `id` (overwrite that user template), `description`, `aspect` | `{template: row, id}` |
| `rename` | `id`, `name` | `{template: row}` (name de-duplicated against the other user templates) |
| `duplicate` | `id` (user OR built-in), `name?` | `{template: row}` named "<name> (salinan)" |
| `favorite` | `id`, `on` (true) | `{template: row}` |
| `delete` | `id` | `{deleted}` (file + metadata) |
| `export` | `ids` (or `id`), `path` (`.json` appended when missing) | `{path, n, fonts: [{family, source: bundled\|system\|missing, bundled}], warnings}` |
| `import` | `path` | `{imported: [row], skipped: [{name, why}], warnings}` |

`row` = the `templates` row (3.2, incl. library fields `group`, `colors`, `effects`, ...) + `favorite`, `aspect_class`
(`vertical|horizontal|square|null`), `aspect_label`, `created`, `updated` (ISO +07:00), `created_ms`, `updated_ms`,
`font_source`, `thumb` (engine thumbnail PNG). Names: control characters stripped, em/en dashes become "-", max 60;
a taken name becomes "Nama (2)", "Nama (3)", ...; ids are slugs and never overwrite a built-in or another file.

Export file (schema-versioned; import accepts it, a list of templates, or one bare template dict):
```jsonc
{"kind": "autocut.caption_styles", "v": 1, "app": "Klipora", "exported": "2026-10-05T10:05:18+07:00",
 "styles": [{"id": "gaya_toko", "name": "Gaya Toko", "favorite": true, "aspect": "vertical",
             "template": {...only the keys that differ from options.defaults (base templates resolved)...}}],
 "fonts": [{"family": "Anton", "source": "bundled", "bundled": true},
           {"family": "Segoe UI", "source": "system", "bundled": false}]}
```
A Windows (system) font is not embedded: export returns a warning ("Pasang font ini di PC lain sebelum impor").
Import is untrusted input: files > 2 MB or with `v` > 1 are refused (`BAD_FILE`, also for broken JSON / no styles);
only known sections are kept (`font style layout timing box highlight anim emphasis emoji` + `name description aspect
tags group`; `base`, `id`, unknown sections dropped); every known key is type-checked against `options.defaults` and
clamped to the schema range (6.5), enums must be valid ids, colours must be `#RRGGBB(AA)`; newer keys inside a known
section (e.g. `anim.fx`) pass through bounded (depth, list and string length); a font that is neither bundled nor
installed (or has unsafe characters) becomes the default font with a warning.

### 13.2 `last_style`

| `params.op` | Params | Result |
|---|---|---|
| `get` (default) | aspect from `seq` size, `w`/`h`, or `aspect`; `thumb` (false); `all` (every class) | `{aspect, last: entry \| null}` (`all`: `{last: {class: entry}}`) |
| `set` | `from_doc: true` (doc in the workdir) or `template`, `style` (doc.style), `params` (doc params), `w`/`h` or `aspect`, `seq_name`, `reason` | `{aspect, changed, updated, template}`; the file is only rewritten when template / style / text rules changed |
| `clear` | `aspect?` (all when omitted) | `{cleared}` |

```jsonc
entry = {"template": "stabilo_tutorial", "template_name": "Stabilo Tutorial", "style": {...doc.style, nulls kept...},
         "full": {...effective look, differences from defaults...},
         "params": {"hide_fillers": false, "numbers": true, "censor": "off", "glossary": ["TokoKita"]},
         "max_words": 5, "seq": "Video kemarin", "reason": "style" | "render" | "doc", "updated": "...+07:00",
         // get adds:
         "ops": [{"op": "template", "id": "stabilo_tutorial"}, {"op": "doc_style", "style": {...}, "replace": true}],
         "base": "stabilo_tutorial", "template_missing": false, "aspect": "horizontal",
         "aspect_label": "Horizontal 16:9", "updated_ms": 1791169518000, "name": "Stabilo Tutorial (gaya kamu)",
         "thumb": "C:\...\thumbs\last_horizontal_<hash>.png"}
```
When the stored template no longer exists (user template deleted), `ops` rebuild the snapshot (`full`) on the aspect
default template (`template_missing: true`). Use: build a new doc with `{template: ops[0].id, ops: [ops[1]]}` (the
panel does this for a sequence without `captions.json`), or send `ops` to an existing doc. The panel writes the
entry (`set`, 1,5 s debounce) after an explicit style / template / text-rule change and after a new overlay render.
