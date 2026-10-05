# Hapus Filler (tool id `fillers`)

Finds hesitations ("eh", "emm", "eee", "hmm", ...) and, only when the user enables them, habit words ("ya",
"oke", "gitu", "apa namanya", ...) on the active sequence, shows them in Tinjau, and cuts the checked ones into a
new "(Klipora)" sequence. The original sequence is never changed. No AI per word: internal research (not published)
measured an LLM (via the local proxy) at 17-86 % accuracy on identical calls, so every decision
is rule based and reproducible.

| Part | File |
|---|---|
| Engine tool (actions, timeline mapping, review, silence combo) | `engine/ac/tools/fillers.py` |
| Fused detector (features, islands, claim, held sounds, cut rule) | `engine/ac/tools/_filler_audio.py` (port of the internal acoustics prototype) |
| Lexicon tiers + habit phrase-boundary rules | `engine/ac/tools/_filler_lexicon.py` (port of the internal lexicon prototype) |
| Engine test | `engine/tests/test_fillers.py` |
| Panel page | `panel/js/tools/fillers.js`, `panel/css/tools/fillers.css` |
| Host (markers) | `panel/host/31_fillers.jsx` |
| Headless test | `tools/ui_tests/fillers.mjs` |

The helper modules start with `_` so the engine tool registry (`ac.tools.names()`) does not list them as tools.

## What the user sees (panel, Bahasa Indonesia)

| Control | Default | Engine param |
|---|---|---|
| Kata yang dicari > **Suara ragu** (eh, ehm, em, emm, eee, hmm, uh, um + spelling variants: ee, mm, ah, eng, euh, hum) | on | `always: true` |
| **Kata "anu"** | on | `anu: true` |
| **Kata kebiasaan** chips: ya, oke, guys, gitu, kan, sih, deh, nah, nih, tuh, dong, lho, baik, apa namanya, apa ya, apa sih, gimana ya, istilahnya, pokoknya. After a run each chip shows how often the word sits at a phrase boundary in that sequence | all off | `habits: []` |
| Cara deteksi > **Tangkap suara ragu tanpa kata** (acoustic) | on | `acoustic: true` |
| **Kepekaan**: Hati-hati 80 % / Seimbang 65 % / Bersih total 50 % | Seimbang | `threshold` |
| Pengaturan lanjutan > **Batas keyakinan** slider 30-95 % ("Custom" when it matches no preset) | 65 % | `threshold: 0.65` |
| Hasil > **Sekalian potong jeda** (runs the Potong Silence engine with the last Potong Silence settings from the panel state) | off | `with_silence: false`, `silence: {preset, offset, min_silence, pad_before, pad_after, min_talk, guard}` |
| Source card scope: Seluruh sequence / In/Out / Clip terpilih | all | `scope` (added by `ctx.run`) |

Primary button: "Deteksi filler" (disabled with a tooltip when every detector is off). Items at or above the
threshold are pre-checked; everything else is still listed, unchecked.

**Tinjau**: title "N filler dibuang dari M ditemukan" (+ "+ K jeda" with the silence combo). Each row: timecode,
source tag (Transkrip / Pendengar / Akustik / Jeda, "+1" when two detectors agree, tooltip lists them), "Mepet"
badge when the filler is glued to a word, confidence %, context `... daftar dulu [eee] Gunakan email ...`, a note
(reason, or "Mepet ke kata. Dengar dulu sebelum dibuang."), duration + dibuang/disimpan. Filter chips: Semua,
Dibuang, Disimpan, Mepet kata, plus Akustik / Kebiasaan / Jeda when present. Click a row = select + move the Premiere
playhead to **0,5 dtk before the cut** (press Space to hear the filler in context). "Kirim ke marker" replaces the
`[Klipora-FILLER]` markers with one marker per checked row (orange filler, purple akustik, yellow kebiasaan, blue jeda).
Primary: "Hapus N filler" / "Hapus N filler + K jeda" / "Potong K jeda". Every toggle is written to the review file at
once (debounced), so "uncheck, Batal, Deteksi lagi" keeps the choice (engine `carry_over`). Seeks, "Kirim ke marker"
and the cut always target the REVIEWED sequence (`doc.seq.id`), not the active one; "Ubah pilihan" on the result card
re-opens it first (after a cut the clone is active). Nothing found -> result card "Tidak ada filler ditemukan" with a
hint (habit words / acoustic) instead of an empty Tinjau.

## Klipora names and languages

- Product name Klipora: the result is a new "<seq> (Klipora)" sequence (`AC.brand.suffix`); review markers are tagged
  `[Klipora-FILLER]` (old `[AC-FILLER]` markers are still cleared: `acHasTag` in `31_fillers.jsx`).
- The UI follows the panel language (Bahasa Indonesia or English): panel texts `fillers.*`, host messages
  `host.fillers*`, engine texts (stage labels, wordless detection labels, reasons, notes, warnings, errors, summary)
  `fillers.*` in `engine/ac/locales`, in the job language. The filler WORDS themselves (eh, anu, ya, oke, ...) are
  transcript content and are never translated. English name: "Remove Fillers" (tab "Fillers"). `fillers.mjs`
  ends with an English pass on the main pane.

## Engine

### `analyze` (stages: Transkripsi, Dengar ulang (pass kedua), Analisis suara, [Cari jeda], Siapkan tinjauan)

1. Heard media = unmuted audio tracks, enabled clips with an existing file (`Timeline.audio_clips()`).
   Errors: `NO_SEQ`, `NO_AUDIO` (no heard audio), `NO_MEDIA` (all files offline).
2. Per media: production words `ac.transcript.words` (cache v3) and the listener pass
   `ac.transcript.listener_words` (`small` + batched + filler prompt, cache `<stem>_listen.json`). When either cache
   is missing the tool holds **one GPU lock** (`ac.util.gpu_lock`) for both passes; with both caches present the GPU
   lock is never touched (tested). A failing listener (model missing etc.) degrades with a warning to transcript +
   acoustics; `CANCELLED`/`GPU_BUSY`/`NO_MEDIA`/`NO_AUDIO` still abort.
3. Frame features (25 ms RMS dB, autocorrelation pitch, flatness, held-sound "stability"), chunked 2 min at a time,
   cached as `<stem>_fillers.npz` next to the media (hash + version checked; 2 MB for 34,6 mnt). Detector = the
   verified prototype: lexicon tokens, listener tokens, unclaimed voiced islands (orphans), held prefix/mid sounds;
   tokens snapped onto the real sound; cut = filler + the SHORTER neighbouring pause minus 40 ms, edges on the
   quietest 10 ms frame; `tight` when glued (< 40 ms) to a word. A cut END glued to the next word that is still
   loud (within 6 dB of the filler) follows the filler's decay until it is 6 dB down, then snaps +-10 ms
   (`Analysis.tail`, max 100 ms, stops if the level rises: vowel-initial word). Reason (live check): the held-run end
   stops 30-60 ms before the vowel fades and QE extract is a hard cut, so the old edge kept an "e" blip + click. Confidence: snapped token 0,92 (glued 0,80), timestamp
   only 0,70 (glued 0,55), +0,05 when a second detector agrees (max 0,97); orphans 0,15-0,9 from acoustics, capped at
   0,35 when the listener heard a real word there and 0,6 when quiet. Disabled tiers are dropped BEFORE the overlap
   merge. Drawl ("dulu~~") is off (`drawl: false`) and never pre-checked.
4. Habit words (only the enabled keys): phrase-boundary rules (opening: nah/oke/baik/guys; closing: ya/gitu/kan/sih/
   deh/nih/tuh/dong/lho/guys, "gitu ya"; searching: apa namanya/apa ya/apa sih/gimana ya/istilahnya/pokoknya; never
   after "apa/kayak"). Confidence search 0,8, transition 0,75, tag 0,7, -0,25 when glued (so glued tics are listed but
   not pre-checked at 65 %). Stutters are left to the repeat tool. A habit hit overlapping a filler is dropped.
5. Source -> sequence: a detection belongs to a clip when its sound midpoint is in the clip's [in, out); the cut is
   clamped to the clip and mapped with `clip.src_to_seq` (speed aware); stereo twins (A1+A2) count once; scope ranges
   clip the result. Context = 6 timeline words before/after the cut (by word END time; Whisper starts are stretched).
6. Optional silence combo: the installed `silence` tool's own `analyze` runs inside the "Cari jeda" stage (its stages
   are folded into ours by a proxy emitter; its review goes to `<workdir>\_fillers_silence\` so the user's own silence
   review is untouched). Its items join the review as `kind: "gap"` with their own on/off. Tool missing or failing ->
   warning "Jeda tidak ikut dipotong: ...", the filler result still comes back.
7. Review `<workdir>\fillers_review.json` (`ac.review` shape, timebase sequence). Manual toggles of a previous review
   of the SAME sequence are carried over (`touched`).

Result: `{review, summary {n, on, sec_on}, counts {always, anu, habit, acoustic, gap}, habit_counts {ya: 53, ...},
silence: null | {ok, n, on, sec_on, review} | {ok: false, msg}, threshold}`.

Review item extras: `t0/t1` = the cut (sequence s), `sound` [s0, s1] (the filler itself), `kind` filler|habit|sound|gap,
`tier` always|anu|habit|acoustic|gap, `src` + `srcs` (transcript|listener|acoustic|silence), `tight`, `sub`
(lexicon|listener|orphan|prefix|mid|drawl|habit|silence), `src_t` (cut in source seconds), habit `key` + `rule`.
Doc extras: `fps`, `threshold`, `counts`, `habit_counts`, `silence`, `media`.

### `apply`

Reads the edited review (`job.review`, must be a `fillers` review, else `BAD_REVIEW`), merges the checked cuts,
snaps them **inward** to the sequence frame grid (`ranges.to_frames(..., "inner")`: never eats a kept frame) and
returns `{plan: {kind: "remove_ranges", ranges, timebase: "sequence"}, summary: "1 filler + 19 jeda dibuang, 41,9 dtk",
counts: {filler, gap, ranges}}`. The panel applies it with `ctx.applyPlan` -> `AC.apply.removeRanges` (clone +
QE extract, or the XML route above 150 ranges).

## Measurements (this PC, cached transcripts)

| Media | Result (defaults) | Time |
|---|---|---|
| 49 s | 0 pre-checked (the clip has no hesitations) | 0,1 s |
| 2 mnt `seq_2m.mp4` | 3 found, 1 checked: "eee" 38,32-38,71 (Pendengar + Akustik, mepet, 85 %), the true "eee" from the research; plan `[[38.325, 38.7083]]` (46 frames at 120 fps; was 38.675 before the decay rule). With "ya": +5 rows, 1 checked (105,67, not glued), 4 glued unchecked. With silence (natural): 1 filler + 19 jeda, 42,0 dtk | 0,04-0,4 s |
| 34,6 mnt | 162 found (45 hesitation, 1 anu, 116 acoustic), 32 checked = 23,3 dtk (counts identical to the prototype baseline 162 / 32; 8 glued cut ends moved 30-100 ms by the decay rule). Habit hits at boundaries: ya 53, guys 52, oke 37, nah 27, nih 15, kan 12, gitu 8, baik 6. With silence (live 2026-10-05, current Potong Silence defaults): 569 rows, 32 filler + 407 jeda, 10 mnt 6 dtk | cold 5,6 s (decode 1,2 + features 4,0), cached 0,26 s |

Port check: on identical audio the ported features equal the prototype's bit for bit (chunk sizes 777 / 3000 /
12000); with the engine's float32 decoder instead of the prototype's int16 one, 1-3 orphan edges move by 10-20 ms.

## Tests

- `python engine/tests/test_fillers.py` (~2 s): lexicon tiers + listener split-word guard, habit rules ("kayak gitu"
  never, "gitu ya", "apa namanya", per-key enabling), params validation, feature chunk invariance + cache, 49 s (0
  checked), 2 mnt (the "eee": label, sources, tight, sound inside cut, context), threshold / tier / habit toggles, cut
  sequence mapping (2 clips, stereo twin, muted track), scope In/Out, apply (frame grid, summary, foreign review
  rejected), carry-over of manual toggles, silence combo with a stand-in tool + missing tool warning, GPU lock (not
  taken with caches; held across both passes without), listener failure degrade, cancel, empty sequence, CLI protocol.
- `node tools/ui_test.mjs tools/ui_tests/fillers.mjs --engine live|mock` (32 live / 31 mock checks: + toggle saved at
  once, markers and apply use the reviewed sequence id (live), empty result card (mock); 280 and 380 px, no page errors). Screens (gitignored `--shots` folder): `build_fillers.png`, `build_fillers_review.png`,
  `build_fillers_result.png`.

## Live Premiere verification (2026-10-05, Premiere 26.2.2, verifier agent)

Test sequences from the 2 mnt and 34,6 mnt media in bin "Klipora Test", driven through the panel UI (cdp clicks),
checked with ExtendScript; all removed afterwards, project never saved. Screens (gitignored `--shots` folder): `fillers.png` (defaults),
`fillers_habits.png`, `fillers_review.png` (before the tag-width fix: "85%" clipped at 330 px), `fillers_review_habit.png` (after), `fillers_review_gaps.png`, `fillers_result.png`,
`fillers_error_noaudio.png`, `fillers_none.png` (before: `fillers_none_before.png`).

| Check | Result |
|---|---|
| Panel reload, host `31_fillers.jsx` loaded, console | OK, no exceptions, `AC.log.errors` empty |
| Defaults (Suara ragu, anu, 19 chips off, Tangkap suara ragu, Seimbang 65 %, jeda off) | OK |
| Deteksi on 2 mnt (stages from cache, no GPU wait) | 1,1 s, "1 filler dibuang dari 3 ditemukan", row 0:38,32 eee Pendengar +1 / Mepet / 85 % |
| Row click | toast "Playhead Premiere ke 0:37,82", CTI 37,8167 s (frame 4538) |
| Kirim ke marker (x2), clearMarkers | 1 orange "Filler: eee" 38,32-38,68 tagged; re-send still 1; clear `{ok, n: 1}`; 32 markers on 34,6 mnt in 0,9 s; akustik = purple; nothing checked = markers removed |
| Hapus 1 filler | clone 126,892 -> 126,508 s (46 frames), 2 clips split at source 38,325 / 38,7083, original untouched; "Buka yang asli" OK |
| "ya" chip | count 5 shown only on the analysed sequence; 8 rows, 4 glued 45 % unchecked, 1:45,67 70 % checked |
| Carry-over | FIXED: was lost after "uncheck, Batal, Deteksi lagi" (file only written on apply); now kept |
| Sekalian potong jeda | stage "Cari jeda", chip "Jeda 19", "Hapus 1 filler + 19 jeda"; clone 84,917 s = exact sum of the 20 merged ranges, 20 clips |
| Scope In/Out 0-20 | only rows inside (3 jeda + 1 akustik); note: an In/Out set by script reaches the panel only after a refresh/focus |
| Muted A1 | error card "Tidak ada audio" (NO_AUDIO), Indonesian, "Coba lagi" |
| Cancel | "Batalkan" during start-up: state cancelled, toast "Dibatalkan. Tidak ada yang berubah.", no python left |
| 34,6 mnt | 162 / 32 in 0,8 s (cached); apply 32 cuts by extract 2,8 s, exact 2053,642 s, 33 clips; + 407 jeda = 438 ranges via the XML route 1,2-1,4 s, exact 1471,117 s |
| Multi-clip (19-clip jeda-only clone, eee kept) | eee mapped to 27,587 s, orphan clamped to its clip; cut = same source frames 38,325 / 38,7083; exported frame continuous |
| User's 15-clip "(AutoCut)" of the 49 s file (read-only) | 0 found, no error (now the "Tidak ada filler ditemukan" card) |

Mepet cut sound (listen proxy: the source spliced exactly like QE extract, levels + Whisper small on CPU): the old end
38,675 resumed INSIDE the eee at -22 dB (35 ms "e" blip with a hard onset, sample jump 0,08); 9 of 16 pre-checked glued
cuts on the 34,6 mnt video had the same loud edge. With the decay rule it resumes at -36 dB before the "g"; 14 of 16
are quiet now (2 have no dip: 886,96 and 1070,54, they keep the Mepet warning). Whisper: "besari" -> "sorry" (1681,6),
"Mereka" -> "Minta" (1570,1), the eee itself unchanged ("Gunakan"); on the 34,6 mnt copy of the same eee Whisper hears
"Bunakan" (0,35-0,43) for any end inside the dip, i.e. the remaining hard-cut onset (QE extract has no crossfade).
Seek lead 0,5 dtk: here it lands in the 2 s pause before the eee, so Space plays 0,5 dtk of silence then "eee gunakan".

## Premiere verifier: step by step

Work only in bin `Klipora Test`, never save, delete what you create, restore the user's active sequence.

1. `node tools/reload.mjs`: no EXC/console lines; `AC.host.json('bac_ping')` ok; host load list contains
   `31_fillers.jsx` without error (`typeof bac_fillers_markers` via `node tools/ev.mjs "typeof bac_fillers_markers"` ->
   `function`).
2. Create a sequence from `%KLIPORA_TEST_MEDIA%\seq_2m.mp4` (2 mnt) in the
   test bin; make it active. Open Hapus Filler (`#tool/fillers`). Check defaults: Suara ragu on, Kata "anu" on, all
   kebiasaan chips off, Tangkap suara ragu on, Kepekaan Seimbang, Sekalian potong jeda off. Screenshot.
3. Click "Deteksi filler": stages Transkripsi (dari cache), Dengar ulang (dari cache), Analisis suara, Siapkan
   tinjauan; no GPU wait. Tinjau shows "1 filler dibuang dari 3 ditemukan".
4. Row 0:38,32: label "eee", tags "Pendengar +1", "Mepet", "85%", context "... Saya bakal daftar dulu [eee] Gunakan
   email ...". Click it: toast "Playhead Premiere ke 0:37,82", Premiere CTI at 37,82 s (frame aligned). Press Space in
   Premiere and listen: "daftar dulu, eee, gunakan email".
5. "Kirim ke marker": exactly 1 orange marker "Filler: eee" at 38,32 s, duration 0,39 s, comment ends with
   `[Klipora-FILLER]`. Click again: still 1 marker (replaced). Remove it: `AC.host.json('bac_fillers_clearMarkers','[Klipora-FILLER]')`
   -> `{ok:true,n:1}`.
6. Click "Hapus 1 filler": new sequence "<name> (Klipora)" opens; duration = original - 0,383 s (46 frames at 120 fps);
   2 clips, cut at 38,325-38,7083 s source; original unchanged (1 clip). Listen across the cut ("dulu gunakan"): no click,
   no clipped consonant. Result card "Buka yang asli" re-activates the original.
7. Back on the settings page enable chip "ya" (it now shows a count, 5 on this media), run again: 8 rows; "ya" rows at
   14,40 / 32,10 / 39,84 / 64,90 carry "Mepet" and are unchecked, 1:45,67 is checked (70 %). Uncheck one row, run
   "Deteksi filler" again: the row stays unchecked (carry-over).
8. Turn on "Sekalian potong jeda": stage "Cari jeda" appears; filter chip "Jeda" counts the gaps; primary reads
   "Hapus 1 filler + N jeda" (N = what Potong Silence finds with its current settings; 19 with the natural preset).
   Apply: the clone is shorter by the summed ranges shown in the result legend.
9. Scope: set In/Out around 0:00-0:20 in the original, choose In/Out on the source card, run: only rows inside
   0:00-0:20 (no 0:38 "eee").
10. Optional: run on the 15-clip "(Klipora)" sequence of the 49 s file (read-only analyze is enough): no errors,
    rows (if any) fall inside clip spans.
11. Cleanup: delete the created "(Klipora)" sequences (`bac_deleteSequence`), the test sequence and bin; make sure no
    `[Klipora-FILLER]` markers remain; do not save the project.
