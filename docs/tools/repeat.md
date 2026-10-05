# Potong Pengulangan (panel id `repeat`, engine tool `repeats`)

Owner: repeat tool agent. Files: `engine/ac/tools/repeats.py`, `engine/tests/test_repeats.py`,
`panel/js/tools/repeat.js` (registers `engineTool: 'repeats'`), `panel/css/tools/repeat.css`,
`panel/host/32_repeat.jsx`, `tools/ui_tests/repeat.mjs`.
Background: internal research notes and prototypes (not published): retake detector prototype ported; AI only labels + fragment guard.

## What it does

Finds bad takes in the transcript of the active sequence (or In/Out, or the selected clips) and removes the
EARLIER attempt, so the LAST take always stays:

| Kind (UI) | Example (34,6 min test video) | Pre-checked when |
|---|---|---|
| Gagap (`stutter`) | "Gak usah, gak usah pakai hosting", "sorry sorry sorry" | always, except demonstratives ("ini ini", "ada ada") and sentence-boundary echoes |
| Mulai ulang (`restart`) | "bakal masuk kesini, Sorry, ini, bakal masuk kesini gitu ya" | the attempt reappears >= Kemiripan (80 %) at the start of the new take, or contains "sorry/maaf"; never for list-like pairs ("stok / akun") |
| Ralat (`correction`) | "kita ke beranda lagi, sorry, kita ke Piner Seller" | marker is sorry/maaf and the next words echo the attempt (>= 30 %), or near-identical |
| Take ulang (`retake`) | "Butuh APK, ini ya. [1,3 dtk] Dia tuh butuh APK" | the earlier sentence is contained >= Kemiripan in the later one and is not list-like |

and only when the confidence is >= "Yakin minimal" (65 %). Everything else above 45 % is listed unchecked.

- Whisper hallucination runs are **flags, never cuts** ("Transkrip ngaco"): the transcript cache's dropped
  `issues` (e.g. "ini" x12 over silence at 31:00, "Terima kasih telah menonton." at 32:35) plus loops the tool
  finds itself (an n-gram said >= 3x over mostly quiet audio). Candidates touching a flag are suppressed. The
  Tinjau pane shows them as a compact block with seekable time chips and "Tandai di timeline" (yellow markers
  `[Klipora-NGACO]`).
- Cut points (sequence time, 10 ms envelope): from the acoustic onset of the removed attempt to the onset of the
  kept take, so the pause BEFORE the attempt survives. A pause longer than 2 dtk before the new take is left alone
  (only the attempt goes; long pauses can hold screen actions and are Potong Silence's job; the row says so).
  An edge that is neither in a pause nor in a >= 8 dB dip is "Nempel ke kata lain" (-0,15 confidence, warn note).
- "Minta pendapat AI" (off by default, grok-auto, one request per 40 groups, cached): adds a verdict tag
  ("AI setuju" / "AI ragu") and a reason per row. It never changes a checkbox or a cut. Offline -> warning
  "AI tidak dipakai" and the rule result is unchanged.
- "Dengar" (on the active row): the engine renders 2 dtk before the cut + 1,5 dtk after it with the cut applied
  (10 ms crossfade, 48 kHz mono WAV in `<workdir>\repeat_preview\`, last 5 kept) and the panel plays it, so the
  user hears the edit before applying.
- Re-runs keep manual choices: rows the user toggled (`touched`) keep their state for the same ids.

## Klipora names and languages

- Product name Klipora: cuts go to a new "<seq> (Klipora)" sequence; markers are tagged `[Klipora-ULANG]` (repeats) and
  `[Klipora-NGACO]` (garbled transcript flags). Old `[AC-ULANG]` / `[AC-NGACO]` markers are still replaced
  (`acHasTag` in `32_repeat.jsx`).
- The UI follows the panel language (Bahasa Indonesia or English): panel texts `repeat.*`, host messages
  `host.repeat*`, engine texts (kind labels, row reasons `why`, notes, flag labels, warnings, errors, stage labels)
  `repeat.*` in `engine/ac/locales`, in the job language. The AI prompt itself is not translated (the AI reason stays
  Indonesian). English name: "Cut Repeats" (tab "Repeats"). `repeat.mjs` ends with an English pass on the main pane.

## Settings (panel -> engine params)

| UI | Param | Default (Normal) | Ketat / Longgar |
|---|---|---|---|
| Gaya (preset) | (sets the four below) | Normal | |
| Yakin minimal untuk dicentang (50-95 %) | `min_on` (0..1) | 0,65 | 0,80 / 0,55 |
| (preset only) list threshold | `min_show` | 0,45 | 0,55 / 0,35 |
| Kemiripan kalimat (60-95 %) | `sim` | 0,80 | 0,90 / 0,65 |
| Jarak maksimal antar take (5-60 dtk) | `window` | 20 | 10 / 40 |
| Cari: Gagap, Mulai ulang, Ralat, Take ulang | `kinds` | all four | |
| Minta pendapat AI | `ai` | off (disabled with a note when AI is off in Pengaturan) | |
| Hasil: Sequence baru / Marker saja | panel only | Sequence baru | |
| source card scope | `scope` | Seluruh sequence | In/Out, Clip terpilih |

Engine-only: `max_join` (2,0 dtk).

## Engine actions (`python -X utf8 engine/cli.py run job.json`, tool `repeats`)

- `analyze`: stages Transkripsi (v3 cache, GPU only when missing) / Baca audio / Cari pengulangan /
  [Pendapat AI] / Siapkan tinjauan. Writes `<workdir>\repeats_review.json` (ENGINE_API 3.4 shape; items carry
  `drop`, `keep`, `after`, `why`, `takes[{t0,t1,text,role}]`, `sim`, `auto`, `tight`, `long_gap`, `ai`; doc carries
  `fps`, `flags[{t0,t1,text,kind,label}]`, `ai{used,source,model,n}`). Returns `{review, summary, flags, stats, ai}`.
  Errors: `NO_SEQ`, `NO_AUDIO` ("Sequence ini tidak punya audio yang terdengar."), transcript errors.
- `apply` (`job.review` = the edited file): `{plan: {kind: remove_ranges, ranges, timebase: sequence}, summary}`;
  checked cuts, merged, snapped inward to the sequence frame grid. Refuses with `SEQ_CHANGED` when the same
  sequence's duration changed since analyze.
- `preview` (`params.cut, pre, post, id`): `{path, dur, pre, post}` (panel calls it through the worker).

## How it applies (panel)

Tinjau -> primary "Potong N pengulangan" -> engine `apply` -> `ctx.applyPlan` with `seqId` = the analysed
sequence -> `AC.apply.removeRanges` (clone "<seq> (Klipora)", QE extract <= 150 ranges, XML route above) ->
result card (before/after, "Buka yang asli", next: Potong Silence, Auto Caption). "Marker saja" mode: primary
"Tandai N pengulangan" -> `bac_repeat_markers(list, '[Klipora-ULANG]', seqId)` (orange markers named
"Ulang (Gagap): ...", comment "Simpan: <kept take>", re-runs replace them; falls back to the foundation marker API if
the host file is not loaded). The review bulk action "Kirim ke marker" does the same without leaving Tinjau.

## Measurements (this PC, cached transcripts)

| Media | Found / pre-checked | Flags | Time |
|---|---|---|---|
| 34,6 min tutorial | 21 / 10 (15,0 dtk; plan 10 ranges, 2076,742 -> 2061,742 dtk) | 2 | 0,07 s analyze (cached) |
| same, In/Out 340-600 dtk | 8 / 4 (7,9 dtk) | 0 | |
| 49 s, 2 min | 0 / 0 ("Tidak ada pengulangan") | 0 | |

Pre-checked on the 34,6 min file (sequence = source time on a one-clip sequence): 404,89 retake, 435,80 restart
(sorry), 504,99 "Misalkan", 573,55 "Gak usah,", 1128,29 "untuk", 1405,97 "gak", 1556,29 retake, 1650,84 ralat,
1808,63 "sorry sorry", 1995,56 restart. Listen proxy (render each edit, 3,5 dtk each side, re-transcribe with
turbo): 8/10 read back as intended, 2 unclear (1128,29 and 1808,63, one-word stutters in fast speech). AI live
(grok-auto, 1 request, 7 s): 21/21 labelled; it agreed with nearly everything, including the two list-like false
positives the rules leave unchecked, which is why it only labels.

## Tests

- `python engine/tests/test_repeats.py` (3 s, offline, no GPU, no quota): synthetic detectors (stutter, 2-word
  stutter, x3 stutter, restart, correction, retake with short and long pause, boundary echo / reduplication / tics /
  lists not flagged, parallel list unchecked, Whisper loop over silence flagged vs real stammer, tight joint),
  34,6 min analyze (known findings + flags + no candidate in a flag), stable ids + carry_over, apply plan frame
  grid + `SEQ_CHANGED`, preview WAV, two-clip sequence mapping, In/Out scope, kind filter, short media, `NO_AUDIO`,
  AI offline fallback (`--live`: one real AI call).
- `node tools/ui_test.mjs tools/ui_tests/repeat.mjs --engine live` (44 checks; `--engine mock` 43) at 280/380/660 px:
  main pane, presets/Custom, kinds, AI switch vs Settings, analyze job params, Tinjau rows (strikethrough + kept
  take, kind tag, confidence, notes, legend), flags block, filters, row click -> `bac_seek`, Dengar -> real WAV,
  toggle -> dock label, markers (fallback + `bac_repeat_markers`), flag markers + seek, apply -> clone + extract ->
  result, "Marker saja", empty result on 49 s, no horizontal overflow, no console errors.
  Screens (gitignored `--shots` folder): `build_repeat.png` (Tinjau), `build_repeat_main.png`, `build_repeat_result.png`,
  `build_repeat_empty.png`.
- `panel/host/32_repeat.jsx`: compat check (ES3) + a Node run against a fake marker API (replace by tag, keeps
  other markers, colours, ERR paths). Not yet run in Premiere.

## UNVERIFIED (needs live Premiere)

- `bac_repeat_markers` in Premiere 26.2.2 (uses the lab-verified `tlAddMarker` + marker iteration from
  `bac_clearMarkersByTag`).
- "Dengar" playback of a `file:///` WAV inside CEP 12 (works in headless Chrome 154 from a file:// page).
- How the 10 default cuts actually sound (ASR proxy only, see Measurements).

## What the Premiere verifier must test (step by step)

Work in bin `Klipora Test`, never save the project, delete only what you made, restore the user's active
sequence at the end.

1. Reload the panel (`node tools/reload.mjs`): no EXC/console lines; `AC.host` load errors empty (32_repeat.jsx
   loads: `AC.host.json('bac_repeat_markers', [], '[AC-TEST]', '')` returns `{ok:true,n:0,removed:0}` on any open
   sequence).
2. Make a test sequence from `%KLIPORA_TEST_MEDIA%\talk_35m.mp4` (createNewSequenceFromClips, name
   "AC Repeat Test"), set In 5:40 (340 dtk) and Out 10:00 (600 dtk). Open Potong Pengulangan (Home > Potong > Ulang).
   Expect the source card tag "Transkrip tersimpan", scope In/Out enabled.
3. Choose scope "In/Out", keep Gaya Normal, click "Cari pengulangan". Expect stages Transkripsi (dari cache), Baca
   audio, Cari pengulangan, Siapkan tinjauan in ~2 dtk, then Tinjau "4 pengulangan dibuang dari 8 ditemukan",
   no "Transkrip ngaco" block (no flags in that range).
4. Rows: the removed attempt is red struck-through, the kept take bold. Click the 6:44,89 row (Take ulang 90 %):
   Premiere's playhead moves to 6:44,89 (toast "Playhead Premiere ke 6:44,89"). Press "Dengar": a WAV plays
   (about 3,5 dtk: "...di website kita | Dia tuh butuh APK"). If nothing plays, record the toast text.
5. "Kirim ke marker": 4 orange markers "Ulang (...)" with comment "Simpan: ..." + `[Klipora-ULANG]` on the test
   sequence; run it again: still 4 (replaced, not doubled). Remove them afterwards
   (`AC.host.json('bac_clearMarkersByTag','[Klipora-ULANG]')`).
6. Press "Potong 4 pengulangan". Expect a new sequence "AC Repeat Test (Klipora)": duration 2076,742 - 7,883 =
   2068,858 dtk (+-1 frame), 5 clips with cuts at 404,89-408,08, 435,80-438,73, 504,99-506,15, 573,55-574,15
   (sequence = source time on this one-clip sequence). Original sequence unchanged. Play 3 dtk around each cut and
   note any clipped word. "Buka yang asli" re-opens the original.
7. Back to the main pane, scope "Seluruh sequence", run again: Tinjau shows 21 found / 10 checked and the
   "Transkrip ngaco di 2 tempat" block (31:00, 32:35). Click a chip -> playhead moves; "Tandai di timeline" ->
   2 yellow markers `[Klipora-NGACO]`. Remove them afterwards.
8. Hasil "Marker saja" + run + "Tandai 10 pengulangan": result "10 marker dibuat", markers on the sequence. Remove.
9. Optional (spends 1 AI request): switch on "Minta pendapat AI", run: an extra stage "Pendapat AI", rows get
   "AI setuju"/"AI ragu" tags and "AI: ..." notes, the checked count stays 10. With the proxy stopped: a warning
   "AI tidak dipakai" and the same 10 rows.
10. Clean up: delete the "(Klipora)" sequences made in steps 6 (`bac_deleteSequence`), the test sequence and its
    bin; check the project item list equals the baseline; restore the user's active sequence. Screenshots (gitignored `--shots` folder): `repeat.png` (Tinjau) and `repeat_result.png`.
