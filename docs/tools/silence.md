# Potong Silence (tool id `silence`)

Owner: silence tool agent. Files: `engine/ac/tools/silence.py`, `engine/tests/test_silence.py`,
`panel/js/tools/silence.js`, `panel/css/tools/silence.css`, `panel/host/30_silence.jsx`, `tools/ui_tests/silence.mjs`.
Background: internal research notes and prototypes (not published): Silences v2, UX design.

## What it does

Finds the pauses in the WHOLE active sequence (or In/Out, or the selected clips) and either cuts them out on a
clone ("<seq> (Klipora)", ripple), marks them with red `[Klipora-SIL]` markers, or mutes them on a clone. The
original sequence is never changed. Works on raw recordings, on sequences that were already cut (hundreds of
butted clips) and on multi-track sequences: a frame counts as silent only when EVERY included audio track is
silent.

Pipeline (engine and panel run the same detector, 10 ms frames, sequence time):

1. Envelope = max dB over the included audio tracks (`Timeline.envelope_on_timeline`, cached per media).
2. Threshold = `floor(Otsu valley of the SOURCE media envelopes)` + offset (integer dB). The valley is taken
   from the whole source files, not from the timeline: an already cut sequence has little silence left and its
   own valley drifts into speech (49 s file: -53 dB raw, -47 dB on its Natural cut, -42 dB on its Kilat cut),
   which is the old "Agresif eats words" bug. Result: a re-run on an AutoCut sequence finds 0 new gaps.
3. Hysteresis gate: a run of frames >= thr-4 dB is sound when it holds a frame >= thr.
4. Word guard (on by default): frames of transcript words that are >= thr-6 dB are forced to sound (+-30 ms).
   Words are mapped to the timeline by OVERLAP (not midpoint): Whisper spans stretch into pauses, so after a cut
   211 of 2803 words have their midpoint in removed time; dropping them made a Kilat re-run on the 35 min file
   find 37 new gaps (now 0). Uses the transcript cache only; "Buat transkrip dulu" transcribes first (GPU lock).
5. Protected spans: included audio clips without a readable file (offline, nested) and video-only spans
   (titles, images without audio under them) are never cut.
6. Pauses shorter than "Diam minimal" are bridged; sounds shorter than "Bicara minimal" (clicks, breaths) are
   dropped unless they hold a transcript word; each talk is padded by "Sisa sebelum/sesudah"; the removals are
   snapped inward to the sequence frame grid; removals under 0,05 dtk are dropped.

The panel gets the floored envelope (`floor(dB)`, base64 uint8) from the `preview` action. Because the threshold
is an integer, `dB >= thr` equals `floor(dB) >= thr`, so the JS port in `silence.js` (`AC.silence.detect`)
gives exactly the engine's count while sliders move. Every preview carries the engine's own count for the sent
settings and the panel logs a warning if its count differs (never happened in tests).

## Klipora names and languages

- Product name Klipora: new clones are named "<seq> (Klipora)" (`AC.brand.suffix`), markers are tagged `[Klipora-SIL]`.
  Old `[AC-SIL]` markers and "(AutoCut)" sequences from earlier versions are still recognised and cleared.
- The UI follows the panel language (Bahasa Indonesia or English, see Settings > Display language): panel texts are
  locale keys `silence.*` (`panel/locales`), host messages `host.silence*`, engine texts (stage labels, review
  labels such as "1,2 dtk diam" / "Pause 1.2 s", marker names "Jeda 1,2 dtk" / "Pause 1.2 s", notes, warnings,
  errors, summary) are `silence.*` in `engine/ac/locales` and follow the job language. English name: "Cut Silences"
  (tab "Silences"). English UI test: `node tools/ui_test.mjs tools/ui_tests/silence_en.mjs`.

## Settings (panel -> engine params)

| UI | Param | Default | Notes |
|---|---|---|---|
| Gaya potong Santai / **Natural** / Cepat / Kilat | `preset` | natural | only changes the four numbers below; "Custom" after a manual change |
| Ambang suara (offset, also the draggable line on the waveform, ArrowUp/Down, Shift = 5 dB) | `offset` | 0 = otomatis | -20..+20 dB around the auto value; "Hitung otomatis" resets |
| Diam minimal | `min_silence` | 0,50 dtk | Santai 0,80, Cepat 0,30, Kilat 0,20 |
| Sisa sebelum / Sisa sesudah | `pad_before` / `pad_after` | 0,06 / 0,15 dtk | Santai 0,10/0,25, Cepat 0,04/0,10, Kilat 0,02/0,06 |
| Bicara minimal | `min_talk` | 0,15 dtk | Santai 0,20, Cepat 0,12, Kilat 0,10 |
| Lindungi kata | `guard` | on | needs a transcript (Auto Caption / Hapus Filler / "Buat transkrip dulu") |
| Buat transkrip dulu (only shown without a transcript) | `transcribe` | off | GPU, about 1 mnt per 30 mnt of video |
| Track yang didengar (chips A1, A2 ...) | `tracks` | all unmuted tracks | an explicit list may include muted tracks |
| Cakupan (source card) | `scope` | Seluruh sequence | In/Out, Clip terpilih |
| Hasil: **Buang jeda (ripple)** / Tandai saja (marker) / Senyapkan jeda | `mode` | remove | |
| Tinjau dulu sebelum diterapkan | `carry` | on | off = apply everything found (no Tinjau, manual choices of earlier runs are not reused) |

Measured (34,6 mnt tutorial, 2803 words, threshold -54 dB): Santai 268 jeda, 78 % kept; Natural 407, 72 %;
Cepat 590, 68 %; Kilat 717, 65 %; 0 words cut or clipped in every preset with the guard (without the guard:
Natural 20 clipped, Kilat 2 cut + 84 clipped). Engine analyze 0,05 dtk (cached envelope), 0,3 dtk on a
717-clip AutoCut sequence; panel live recompute on 207 000 frames well under 150 ms.

## Engine actions (`engine/ac/tools/silence.py`)

- `preview` -> `{dur, fps, n, env (base64 uint8, dB+90), floor, auto, base, thr, algo, presets, words [[t0,t1]],
  words_n, has_words, protect, scope, tracks, used, check {n, sec, thr}}` (`words` = guard spans, overlap-mapped).
  Used by the panel through the worker.
- `analyze` -> stages `audio, words, detect, review`; writes `<workdir>\silence_review.json` (items `gap`,
  `label` "1,3 dtk diam", `ctx {pre, post}` = 3 words each side, `note` guard `{type:'guard', words}` or
  `{type:'warn'}` when a word is lost with the guard off, `cut` = exact removal); returns `{review, summary,
  stats}` (stats: thr, auto, protected + protected_words, lost, after, save_pct, tracks, scope ...). Manual
  toggles of the previous review of the same sequence are carried over (`carry`).
- `apply` (params.mode) -> `remove_ranges` / `markers` (tag `[Klipora-SIL]`, red, one per checked gap, comment =
  context words) / `mute_ranges` (`ranges`, `tracks`); every plan carries `seq {id, name}` of the analyzed
  sequence so the panel cuts that one even if another sequence became active.
- Errors: `NO_SEQ`, `NO_AUDIO` (no audible track / chosen tracks empty; names the muted tracks when muting is the reason), `NO_MEDIA` (all included audio
  offline), `BAD_REVIEW`.

## How it applies (panel)

- Buang jeda: `ctx.applyRemove(ranges, {seqId})` -> `AC.apply.removeRanges` (clone + QE extract <= 150 ranges,
  XML route above). Result card: Sebelum -> Sesudah, Hemat %, cut ribbon, Buka yang asli, Ubah pilihan,
  Hapus hasil, a warning listing the words the guard protected.
- Tandai saja: `AC.apply.markers` on the analyzed sequence (old `[Klipora-SIL]` markers are replaced, other markers
  stay). Result card: count, "Kalau dipotong" duration, Hemat %, "Hapus marker".
- Senyapkan: `bac_cloneSeq` -> `bac_silence_mute(cloneId, ranges, tracks, from, to)` in chunks of 40 ->
  `bac_openSequence(clone)`. Hold keys on the Volume Level (verified pattern of `tlDuck`): level kept one frame
  before each gap, 0 (-inf) inside, restored at the end. Locked tracks are skipped and named in the result;
  when EVERY chosen track with clips is locked the panel stops before cloning (error "Track terkunci"), and a
  run that wrote 0 keys deletes its unchanged clone and shows "Tidak ada audio".
  Uses the new foundation helper `ctx.track(task, opts)` (progress pane for a tool-driven host task).

## Tests

- `python engine/tests/test_silence.py` (~1 s, cached media only): settings, synthetic detector cases
  (hysteresis, min talk, guard, protect, scope, frame snap, multi-track max), integer-threshold equivalence on
  all 3 media x 4 presets x 3 offsets, preview/analyze/apply, carry-over, In/Out, re-run idempotence on AutoCut
  sequences (49 s Natural/Kilat, 35 min Kilat 717 clips + stereo twin), multi-track (only where all included
  tracks are silent), offline clip + video-only title protection, error codes.
- `node tools/ui_test.mjs tools/ui_tests/silence.mjs --engine live` (35 checks, also `--width 280`/`660`):
  JS detector == engine for 24 preset/offset/guard combos on raw49, cut15 and in the self-check on long35,
  sliders/presets/threshold keys, analyze -> Tinjau (rows, filters, click-to-seek) -> apply remove / markers /
  mute (stubbed host), error path, layout. Screens (gitignored `--shots` folder): `build_silence*.png`.

## What the Premiere verifier must test (step by step)

Work in bin `Klipora Test` with a sequence made from `talk_49s.mp4` (49 s); delete what you
create, never save. Reload the panel (`node tools/reload.mjs`): no `EXC`, `AC.host.loadErrors` empty
(`30_silence.jsx` must load).

1. Open Potong Silence on the test sequence. Within ~2 s the waveform appears, legend "Seluruh sequence,
   perkiraan langsung", tags "Ambang -53 dB, otomatis" and "81 kata dilindungi"; strip shows Jeda 8 (Natural).
   `AC.tools.runtime('silence').ctx.silence.check.ok === true`.
2. Click Santai / Cepat / Kilat: counts change instantly (Santai 7, Cepat 13, Kilat 16 on this file). Drag the
   dashed line up/down: the dB label, the Ambang slider and the counts follow. "Hitung otomatis" resets to -53.
3. Natural, Tinjau on, Buang jeda: press "Tinjau 8 jeda". Expect stages Baca audio / Cek transkrip / Cari jeda /
   Siapkan tinjauan, then 8 rows with context words. Click a row: Premiere's playhead jumps to that gap
   (toast "Playhead Premiere ke ..."). Uncheck one row, press "Potong 7 jeda".
4. Expect a new active sequence "<name> (Klipora)" about 11 s shorter, clips butted, no word clipped when you
   play across the cuts (listen at every cut); the original sequence still 0:49 with 1 clip. Result card:
   Sebelum 0:49 -> Sesudah ~0:38, Hemat ~22 %. "Buka yang asli" re-activates the original; "Hapus hasil"
   (two clicks) deletes the clone.
5. Open Potong Silence ON THE CLONE (Natural): the strip shows Jeda 0 and the dock "Tidak ada jeda" (disabled),
   the tag still says "Ambang -53 dB": the threshold comes from the source media, so a re-run never eats into
   the already tight cut. Kilat on the clone still finds a few short pauses.
6. Kilat on the original, Tinjau on: rows with a shield note "Batas digeser, kata ... tetap utuh" (if any) are
   in the "Mepet kata" filter; apply and listen to those spots: the words must be whole.
7. Tandai saja, Tinjau off: press "Tandai 8 jeda". Expect 8 red markers named "Jeda x dtk" spanning each gap,
   comment ending in `[Klipora-SIL]`; run again: still 8 (replaced, not doubled); user markers untouched.
   "Hapus marker" removes only the `[Klipora-SIL]` ones.
8. Senyapkan jeda: expect a new "(Klipora)" clone with the SAME duration (0:49), Volume Level hold keyframes on
   A1 (0 inside each gap, original level elsewhere), audible silence in the gaps, speech unchanged. Check one
   clip's keyframes in Effect Controls. Lock A1 on a copy and run again: result warns "Track terkunci dilewati".
   UNVERIFIED live: `getValueAtTime` on Level, key placement one frame before each gap at 120 fps.
9. Multi-track: add the 2 min media on A2 under the same span. Expect fewer gaps (only where A1 and A2 are both
   silent). Deselect A2 in "Track yang didengar": counts return to the A1-only values. Mute A2 in Premiere:
   it is excluded by default.
10. In/Out: set In 0:10, Out 0:30, choose In/Out in the source card: all gaps between 0:10 and 0:30.
11. Long file: a sequence of `talk_35m.mp4` (34,6 mnt): preview within ~3 s, Natural 407 jeda; apply
    goes through the XML route (> 150 ranges) and the clone is ~24 mnt 50 dtk.
12. After every step: `AC.log.errors` empty, no orphan python.exe (worker is killed on panel unload).

## Live verification (Premiere 26.2.2, 2026-10-05, verifier)

Run through the panel UI (cdp clicks) on test sequences in bin `Klipora Test`; everything created was deleted,
the user's active sequence and playhead were restored, the project was never saved. Screens (gitignored `--shots` folder): `silence_*.png`.

| Step | Result |
|---|---|
| Reload | no EXC, `loadErrors` [], `30_silence.jsx` loaded, `AC.log.errors` [] for the whole session |
| 1 Preview 49 s | 0,2 dtk; legend, "Ambang -53 dB, otomatis", "81 kata dilindungi", Jeda 8, self-check ok |
| 2 Presets / threshold | Santai 7, Cepat 13, Kilat 16, Natural 8 (< 50 ms each); drag -> -44 dB, ArrowUp -43, Shift+ArrowUp -38, Hitung otomatis -53 |
| 3 Tinjau | stages Baca audio / Cek transkrip / Cari jeda / Siapkan tinjauan, 8 rows with context; row click -> Premiere playhead 12,5667 (toast); uncheck -> "Potong 7 jeda" |
| 4 Remove | clone 38,108 dtk, 6 butted clips on the frame grid, in points = kept ranges; original 48,917 dtk 1 clip; card 0:49 -> 0:38, 22%; exported frames on both sides of a cut match the source frames; 0 of 81 words lose an audible frame (envelope check); Buka yang asli, Hapus hasil (2 clicks) ok |
| 5 Re-run on clone | Natural finds exactly the 1 gap left unchecked (0,317 dtk), threshold still -53; Santai 0 -> dock "Tidak ada jeda" disabled |
| 6 Kilat + guard | 16 rows, "Mepet kata 1" ("Nah"), clone 34,475 dtk, guard warning on the result card |
| 7 Markers | 8 red (index 1) markers "Jeda x dtk", comment + `[AC-SIL]`; re-run still 8 + user marker; Hapus marker removes 8, user marker kept |
| 8 Mute | clone same duration, 22 HOLD keys on A1 (0 inside gaps, key one frame before each gap, restore at gap end); `getValueAtTime` (number seconds) works; a -6 dB clip is restored to 0,0891 |
| 8b Locked A1 | FIXED: was "8 jeda dibuat hening" with 0 keys; now error "Track terkunci" before cloning, Coba lagi after unlocking works |
| 9 Multi-track | A1 + seq_2m.mp4 on A2: 3 jeda (-54 dB), deselect A2 -> 8 (-53 dB); mute A2 in Premiere -> 8 live, unmute -> 3 live (FIXED, see below); removed spans quiet on both media |
| 10 In/Out 0:10-0:30 | 2 gaps (12,567 and 18,075), legend "In/Out 0:10,00 sampai 0:30,00" |
| 11 35 mnt | preview < 0,3 dtk, Natural 407 / Kilat 717, Kilat recompute 5 ms; analyze -> 407 rows; apply via XML route 1,2 dtk, clone 24:50,8 (407 clips, no gaps, frame grid), 0 of 2803 words lose an audible frame; mute 407 gaps = 1220 keys in 0,8 dtk, 22 probes exact |
| Cancel | Batalkan during analyze -> toast "Dibatalkan. Tidak ada yang berubah.", no orphan python (worker replaced on reload) |
| Muted-only seq | error pane "Tidak ada audio: Semua track yang berisi audio sedang di-mute (Audio 1)" (was "Track audio yang dipilih kosong") |
| Carry-over | Ubah pilihan reopens the review with the same choices; a new Tinjau on the same sequence keeps the manual uncheck |

Fixes made during verification: threshold tag now follows drags/keys (was stuck on "-53 dB, otomatis"); mute
mode refuses all-locked tracks and never reports a fake success; track chips that equal the default set store no
explicit list (muting a track later excludes it); the preview refreshes when a track is muted/unmuted (core
`AC.seq` signature now includes mute/lock flags); NO_AUDIO names the muted tracks; dock `title` updates
(core `ui.dock.update`); error title for `LOCKED` (core `jobview`).
Known, not changed: Kilat keeps short word-less sounds (mouse double clicks) as 0,13-0,24 dtk fragments (82 on the
35 mnt file, 2 on 49 s), because min_talk measures the merged span; the XML route imports a second master clip of
the media into the project root when the source clip lives in a sub-bin.
