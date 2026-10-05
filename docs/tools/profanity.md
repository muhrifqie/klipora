# Sensor Kata Kasar / Bleep Profanity (tool id `profanity`)

Owner: profanity tool agent. Files: `engine/ac/tools/profanity.py`, `engine/ac/tools/_profanity_lexicon.py`,
`engine/ac/tools/_profanity_sfx.py`, `engine/ac/assets/sfx/` (generated tones), `engine/tests/test_profanity.py`,
`panel/js/tools/profanity.js`, `panel/css/tools/profanity.css`, `panel/host/33_profanity.jsx`,
`tools/ui_tests/profanity.mjs`. Background: internal research notes and prototypes (not published): AutoProfanity; Premiere audio keys, tracks, overwriteClip.

## Klipora names and languages

- The product is called **Klipora**. Results: clone `<seq> (Klipora)` (`AC.brand.suffix` / `AC_SEQ_SUFFIX` / engine
  `SEQ_SUFFIX`), sensor track `Klipora Sensor`, bin `Klipora`, markers `[Klipora-PF]` (review markers
  `[Klipora-PF-RV]`). These Premiere names are the same in both UI languages.
- Projects made before the rename keep working: the host re-run (`pfUndoOld`) finds an `AutoCut Sensor` track
  (`acNameIs`) and `[AC-PF]` markers (`acHasTag`); the engine treats both `Klipora Sensor` and `AutoCut Sensor` as our
  own track (`is_sensor_track`, never scanned as dialog); `tlFindBin("Klipora")` reuses an old `AutoCut BOT` bin.
- Word lists stay in `profanity.json`, now under `%APPDATA%\Klipora` (migrated from `%APPDATA%\AutoCutBOT` by the
  foundation on first start).
- UI language: Indonesian or English (Settings > Display language, default follows Premiere). Panel texts are locale
  keys `profanity.*` / `host.profanity*` (`panel/locales`), engine texts (stages, notes, warnings, errors, marker
  name and comment, `mode_label`, `tier_label`, rule reasons) are `profanity.*` in `engine/ac/locales`, picked per job
  (`job.lang`). English names: tool "Bleep Profanity" (tab "Profanity"), levels Relaxed / Normal / Strict, modes Beep /
  Mute / Duck / Custom sound, lists "Always bleep" / "Always allow". Swear words, masks (`k****l`, `[bip]`) and the AI
  prompt are content and are not translated. The word lists below quote the Indonesian UI.

## What it does

Finds swear words in the transcript of the active sequence (or In/Out / selected clips), lets the user review them,
and censors the checked ones on a CLONE `<seq> (Klipora)`: volume keyframes with 10 ms ramps on the dialog clips plus
a beep (or the user's own sound) on a new audio track `Klipora Sensor`, and red `[Klipora-PF]` markers. The original
sequence is never changed. The same choices are exported for captions (`hits_for_timeline`).

Pipeline (engine `analyze`, sequence time):

1. Words: `Timeline.words_on_timeline` (v3 cache, Whisper with the GPU lock when missing). Tracks named
   `Klipora Sensor` (or the old `AutoCut Sensor`) are excluded (never transcribe our own beeps).
2. Dengar ulang (on by default): the listener pass (`transcript.listener_words`, small model, verbatim prompt,
   cached `<stem>_listen.json`, shared with Hapus Filler). Whisper "cleans up" swears: on the 34,6 mnt test file the
   only swear ("anjir" at 22:41) is missing from the main transcript and present only in the listener pass. Listener
   hits that overlap a main-pass hit are dropped; the rest are listed with the note "Hanya terdengar saat dengar
   ulang" and conf <= 0,75.
3. Lexicon (`_profanity_lexicon.py`, ID + regional + EN), tiers: 3 kasar (always), 2 sedang (Normal, Ketat),
   1 ringan (Ketat only), 0 arti ganda (anjing, babi, monyet, setan, tai, tahi, asu, iblis, bangke, dick, ...: all
   levels, decided by context). Normalization: lowercase; leet only for mixed letter+digit tokens (`b4ngs4t`, never
   `741` -> `tai`); letter runs collapsed (`anjiiing`); one suffix (nya lah kah an in mu ku lo lu) or prefix
   (ng di ke) stripped; `fuck*`. Whisper self-masking (`f***`, `anj*ng`) is a hit (tier >= 2), matched to the lexicon
   word of the same shape. Tiers below the level are not listed but counted (`stats.below`, the "Cari lagi di Ketat"
   hint).
4. Priority: user allow list > user block list (multi-word groups like "dasar kampret") > built-in literal phrases
   (tahi lalat, anak anjing, babi guling, sate babi, setan merah, Dick Grayson, ...) > lexicon.
5. Edges: each Whisper edge moves to the quiet 10 ms frame NEAREST the word inside +-120 ms (listener words +-200 ms);
   quiet = within 3 dB of the window minimum or 24 dB under the word peak; + 20 ms pad; min length 0,25 dtk. Never
   "extend while loud". "anjir": listener 1360,95-1361,31 -> censor 1361,00-1361,38 (energy 1361,03-1361,30).
6. Arti ganda: AI 3 parallel votes (prompt with 6 examples, `arti` before `sensor`, a vote counts only when its
   echoed `kata` matches), censor at >= 2 of 3; a 1-of-3 vote is also censored when the context rules say swear
   (fixes the one measured AI miss, "Anjing emang, file-nya kehapus"). Context = the Whisper segment. Results are
   cached (`%LOCALAPPDATA%\Klipora\ai_cache`), so a re-scan costs no quota. AI off / proxy down / settings `ai:false`
   -> rules (warning "AI tidak dipakai, pakai aturan"): insult cues in the same clause ("dasar" before; lu/lo/kau/
   banget/emang/tenan/lah/sih after; elongated "Anjiiing"; "!"; exclamation at segment start "Asu, ..."; a lone word)
   against literal cues (si, seekor, peliharaan, kebun binatang, masak, kucing, ada, saya ... within 3 tokens,
   capitalized name after, possessive -nya). Rules are conservative: no cue = not censored, user checks.
7. Review file `<workdir>\profanity_review.json`; manual toggles survive a re-run (`review.carry_over`).

## Settings (panel -> engine params)

| UI | Param | Default | Notes |
|---|---|---|---|
| Tingkat sensor Longgar / **Normal** / Ketat | `level` | normal | min tier 3 / 2 / 1; arti ganda at every level |
| Cara sensor **Bip** / Senyap / Kecilkan / Suara kustom | `mode` (apply) | beep | `beep` `mute` `duck` `custom` |
| File suara kustom (wav mp3 m4a ogg flac aif) | `custom` | | converted by the engine to a 48 kHz stereo WAV we own (`%APPDATA%\Klipora\sfx\custom_*.wav`), max 3 dtk, 10 ms fades, placed centred on the word |
| Cek arti ganda dengan AI | `ai` | on | disabled (and off) when Pengaturan > AI is off |
| Dengar ulang audio | `deep` | on | listener pass, cached; GPU when not cached |
| Sensor juga caption | library `captionStyle` | stars | off -> `hits_for_timeline` returns [] |
| Daftar kata: Selalu sensor / Selalu izinkan | library `block` / `allow` | [] | `%APPDATA%\Klipora\profanity.json`, words or phrases, an entry lives in one list only |
| Lanjutan: Nada bip | `freq` | 1000 Hz | 400-2000 |
| Lanjutan: Volume bip ikut suaramu / Volume bip | `tone_db` | `auto` | auto = speech p75 - 4 dB, clamped -32..-12 dBFS (test media: -26 dB; a fixed -14 dB beep was 8-10 dB louder than these voices) |
| Lanjutan: Turunkan volume (Kecilkan) | `duck_db` | -20 dB | relative to the clip's own level |
| Lanjutan: Tambahan tepi | `pad` | 20 ms | |
| Lanjutan: Sensor minimal | `min_len` | 0,25 dtk | |
| Lanjutan: Tandai dengan marker | `markers` | on | `[Klipora-PF]`, red, on the clone |
| Lanjutan: Samaran di caption k****l / ****** / [bip] | library `captionStyle` | stars | |
| Cakupan (source card) | `scope` | Seluruh sequence | In/Out, Clip terpilih |

Review rows: time, word in context, tier tag (Kasar / Sedang / Ringan / Arti ganda), `AI n/3`, `Daftarmu` /
`Dengar ulang`, confidence, note (AI reason, rule reason, listener warning). Filters: Arti ganda, Dengar ulang.
Row actions: Lompat, Jangan disensor / Sensor kata ini, **Dengar** (engine `preview` through the worker, plays a
2-3 s WAV with the chosen mode), **Selalu izinkan** (lemma -> allow, row off), **Selalu sensor** (lemma -> block,
row on). Click = seek 1 dtk before the word. Primary: "Bip 3 kata" / "Bisukan" / "Kecilkan" / "Sensor".
No hits: result card "Tidak ada kata kasar" with words checked and "Cari lagi di Ketat" when ringan words exist.

## Engine actions (`engine/ac/tools/profanity.py`)

| Action | Params | Result |
|---|---|---|
| `analyze` | `level, ai, deep, pad, min_len, scope` | `{review, summary: "3 kata ditemukan, 2 disensor", stats: {words, below, ambiguous, ai: ai\|cache\|rules\|none\|off, listen, first7, n, on, sec_on}, n, on, level}`; stages `words, listen?, scan, edges, ai?, review` |
| `apply` | job `review` + `mode, freq, tone_db, duck_db, custom, custom_max, custom_gain, markers` | `{plan, summary, stats: {n, ranges, first7, tones, sec, captions, mode, tone_db}}`; errors `NO_HITS`, `NO_SFX`, `BAD_REVIEW` |
| `preview` | `media, s0, s1, lo, hi, id, speech_db` + sound params | `{path (WAV in <workdir>\preview, last 30 kept), dur, a, b, mode}` (~0,1 dtk) |
| `hits` | `style, level, include_off` (+ job `seq`) | `{hits, n, style}` = `hits_for_timeline` |
| `library` | `op: get\|set, block, allow, captionStyle` | `{library, builtin: {"3": [...], "2", "1", "0", literal}, path}` |

Review item extras: `word, lemma, tier, tier_label, mask, w0, w1 (Whisper edges), words (v3 word ids), texts,
media, track, ms0, ms1, lo, hi (source seconds), ai {votes, of, reason}`, `src` (`rule|ai|user|listen`). Doc meta:
`level, speech_db, sensor_track, dialog_tracks, caption_style`.

Plan (`kind: "censor"`, sequence seconds):

```json
{"kind": "censor", "mode": "beep", "mode_label": "Bip", "seq": {"id", "name"}, "name": "<seq> (Klipora)",
 "track_name": "Klipora Sensor", "tag": "[Klipora-PF]", "ramp": 0.01, "duck_db": -20, "low_db": null,
 "tone_db": -26.2, "tone_auto": true, "freq": 1000,
 "ranges": [{"t0": 1361.0, "t1": 1361.38, "paths": ["...mp4"], "ids": ["w136100"], "masks": ["a***r."]}],
 "tones": [{"t": 1361.0, "dur": 0.38, "path": "...\\engine\\ac\\assets\\sfx\\beep_1000hz_26_2db_380ms.wav"}],
 "markers": [{"t", "end", "name": "Sensor", "comment": "a***r. (Bip)", "tag": "[Klipora-PF]", "color": 1}]}
```

Ranges closer than 0,12 dtk are merged (one tone, no key flicker). Tones: sine at an exact RMS level, 5 ms fades
baked in, 48 kHz 16-bit stereo, one file per 20 ms length bucket rounded up, centred on the range, reused by name
from `engine/ac/assets/sfx` (a re-run imports nothing new). Every tone and custom sound ends with 70 ms of silence
(`_profanity_sfx.TAIL`, file names `..._t70.wav`): Premiere floors a placed audio clip to whole sequence frames
(verified live: a 380 ms beep became 375 ms at 120 fps and 366,7 ms at 30 fps), which cut off the baked fade-out
and clicked at the end of every beep. `dur` in the plan is the audible length (centring uses it, not the tail).

## Captions (for the captions agent)

```python
from ac.tools import profanity
masks = {h["id"]: h["mask"] for h in profanity.hits_for_timeline(tl)}       # tl = Timeline or Timeline JSON
# h = {"id": "<hash10>:<word idx>", "t0", "t1", "text", "mask": " k****l,", "lemma", "tier", "on", "source": "review"|"rules"}
profanity.mask_text(" kontol,", "stars")   # -> " k****l,"   ("full" -> " ******,", "bip" -> " [bip],")
```

- Keys are the v3 word ids of `Timeline.words_on_timeline`, so a caption made on ANY sequence cut from the same
  media masks the same words. Source of truth per word: the user's reviewed choice from the last "Terapkan"
  (`<media stem>_profanity.json` next to the media, keyed by word index, stale entries ignored when the word text
  no longer matches), else the rules (lexicon + the user's lists, arti ganda by context cues, no AI, caches only,
  no GPU).
- `style=None` uses the user's setting (`profanity.json` `captionStyle`); `"off"` returns `[]`. `include_off=True`
  also returns words the user chose to keep (`on: false`).
- Words only the listener heard are not in the caption text, so they have no caption entry (audio is still censored).

## Host (`panel/host/33_profanity.jsx`, ES3)

| Function | Does |
|---|---|
| `bac_profanity_begin(srcId, name, trackName, opts)` | clone (`tlCloneSequence`, unique "(Klipora)", `acMarkMade`), open it; undo an earlier sensor that the source carries (`pfUndoOld`: its `[Klipora-PF]` / old `[AC-PF]` markers (`acHasTag`) give the old ranges, our Level keys around them are removed (static level again when none are left), clips on tracks named `trackName` or its old name (`acNameIs`: `AutoCut Sensor`) cleared, old markers deleted, all on the clone only); reuse that sensor track or, when `opts.track` (a sound is placed), `tlAddAudioTrack` + name it (`Track.name =` works on 26.2.2, QE `setName` fallback). Deletes its own clone again when a later step of this call fails. `opts {track, tag}`. `{id, name, origId, origName, track, named, trackName, undo: {ranges, keys, clips}}` |
| `bac_profanity_keys(cloneId, ranges, opts)` | per range: only clips whose media path is in `paths` (music keeps playing), skips disabled clips and locked tracks (reported), keys `[a-ramp: base, a: low, b: low, b+ramp: base]` in MEDIA time, clamped to the clip (a range across a cut keeps the level low to the clip edge); old keys strictly inside the window removed; ramp = max(10 ms, 1 frame); duck = base level x 10^(dB/20). `{keys, clips, missed, locked, ramp}` |
| `bac_profanity_tones(cloneId, trackIdx, tones)` | imports each engine WAV once into bin "Klipora" (old "AutoCut BOT" reused) (`tlImportFile`: File.exists first, no modal), `overwriteClip(item, seconds)`; non-wav / missing refused. `{placed, missing}` |
| `bac_profanity_probe(seqId, times, trackIdx)` | read-back for verification: Level dB of every audio clip under each time (`-999` = muted), keys count, clips on the sensor track |

Panel flow (one `AC.Task` shown with `ctx.track`): engine `apply` -> begin -> keys (chunks of 40 ranges) -> tones
(chunks of 25) -> `bac_addMarkers(list, cloneId)`. Cancel (Esc) or an error after the clone exists deletes that clone
(`bac_deleteSequence`) and reopens the original: a half-censored copy would look finished. The same happens when NO
range found a dialog clip (error `LOCKED` "Track terkunci" with the track names, or `NO_AUDIO`), instead of a
"Sequence baru siap" card for a clone where nothing was censored. Result card: Buka yang asli, Ubah pilihan, Salin
daftar, Hapus hasil (deletes the clone), warnings (some ranges missed, locked tracks, track not renamed), a note when
an older sensor on the source was replaced, next: Auto Caption.

## Tests and measurements (this PC, 2026-10-05)

- `python engine/tests/test_profanity.py`: offline (AI disabled, sandboxed APPDATA, own scratch folder per process),
  ~1-2 s. `--live` adds the AI check (uses the AI cache after the first run).
- `node tools/ui_test.mjs tools/ui_tests/profanity.mjs --engine live`: 51/51 (real engine + worker on the 34,6 mnt
  clip, host bac_profanity_* stubbed in the page and the real jsx run against a fake Premiere DOM in node `vm`,
  including the re-run undo checks); `--engine mock`: 49/49, also at `--width 280`. Screenshots (gitignored `--shots` folder): `build_profanity*.png`.
- Rules (no AI) on the research's 23 sentences: 23/23. AI 3-vote on the 9 held-out ambiguous decisions: 8/9 raw
  (same miss as the research), 9/9 with the 1-vote + rules rescue.
- 34,6 mnt file: Normal 0 hits (1 ringan counted), Ketat 1 hit "anjir" 22:41,00-22:41,38 (listener), analyze 0,07 dtk
  from caches; preview 0,09-0,11 dtk; apply 0,00 dtk; `hits_for_timeline` 0,03 dtk; speech p75 -22,2 dB -> auto beep
  -26,2 dBFS. Tone RMS within 0,3 dB of the request; preview inside the word: mute < -80 dB, beep +-2 dB of the tone
  level, outside the word untouched.

## Premiere verifier: test step by step (live, one at a time, never save)

Names below are the current Klipora ones; the live records further down were taken before the rename (they show
"AutoCut" names and `[AC-PF]`, which the tool still recognises).

1. `node tools/reload.mjs`: no EXC / console lines; `AC.host.json('bac_ping')` OK; `typeof bac_profanity_keys` is
   loaded (`node tools/ev.mjs "typeof bac_profanity_begin"` -> function).
2. In bin `Klipora Test` make a sequence from `talk_35m.mp4` (34,6 mnt). Set In/Out to 22:30-22:50 so the
   test is short (`seq.setInPoint/OutPoint`), open it, open the Sensor tab (`#tool/profanity`).
3. Tingkat **Normal**, Cakupan In/Out, Cek arti ganda off -> Cari kata kasar -> card "Tidak ada kata kasar" with
   "Cari lagi di Ketat". Click it -> Tinjau shows 1 row "anjir." at 22:41,00, tags Ringan + Dengar ulang.
4. Click the row: Premiere CTI = 22:40,00 (1 dtk lead). Click "Dengar": a ~2,8 dtk sample plays with a beep over
   "anjir" (if CEP blocks audio a toast says so; report it).
5. Click "Bip 1 kata". Expect stages Siapkan suara sensor, Gandakan sequence, Atur volume 1 kata, Pasang suara sensor,
   Tandai marker; result "Sequence baru siap"; a new sequence "<name> (Klipora)" is ACTIVE; original unchanged.
6. In the clone check with `node tools/cdp.mjs "AC.host.json('bac_profanity_probe', '<cloneId>', [1360.9, 1361.2, 1361.5], <track>)"`:
   at 1361,2 dialog Level = -999 (muted) on A1 (and A2 if the media has a stereo pair track), at 1360,9 and 1361,5 =
   the original level (0 dB); sensor track has 1 clip starting ~1361,00 lasting 0,45 dtk (0,38 dtk tone + 70 ms silent tail). Visually: Effect Controls
   of the A1 clip shows 4 Level keys around 22:41; timeline shows the track named "Klipora Sensor" (if the name did
   not stick, the result card warns; report which API worked: `made.named`).
7. Play 22:39-22:43 in the clone: beep replaces "anjir", no click at the edges, beep loudness close to the voice.
   Original sequence: "anjir" still audible.
8. Marker: one red range marker "Sensor" at 22:41,00 with comment "a***r. (Bip)" and tag `[Klipora-PF]` on the clone only.
9. Repeat 5-6 with Cara **Senyap** (no new track, Level -999 inside), **Kecilkan** (Level -20 dB inside), and
   **Suara kustom** with any short wav/mp3 (one clip of max 3 dtk centred on the word on "Klipora Sensor").
10. Key-time check below 120 fps (verified 2026-10-05): on a 30 fps sequence the ramp keys must not collapse onto one
    frame (`bac_profanity_probe` shows 4 keys, the level is back to 0 dB 2 frames after the word).
11. Cancel test: start Bip and press Esc during "Atur volume": toast "Dibatalkan ...", the half-made clone is deleted
    (project sequence count back to before), original active again.
12. "Hapus hasil" on a result card deletes that clone (two-step confirm) and reopens the original.
13. Word lists: add "dasar kampret" to Selalu sensor, check `%APPDATA%\Klipora\profanity.json`; remove it.
14. Cleanup: delete every "(Klipora*)" sequence you made (`bac_deleteSequence`), the test sequence, the imported
    beep items in bin "Klipora" and the bin if it did not exist before; restore the user's active sequence.

## Live verification (Premiere 26.2.2, 2026-10-05, verifier)

Test sequences in bin `Klipora Test` (34,6 mnt with In/Out 22:30-22:50, a 30 fps copy, the 49 s file, a 4-clip
cut of the 34,6 mnt file with a cut INSIDE "anjir" and the 49 s file on A2 as "music"), all driven through the panel
UI (clicks via CDP), read back with ExtendScript and with real audio exports of the In/Out
(`seq.exportAsMediaDirect` + the system "AIFF 48kHz" preset, 0,2 dtk each, analysed with numpy). Project never saved;
everything made was deleted afterwards. Screens (gitignored `--shots` folder): `profanity_*.png`.

| Check | Result |
|---|---|
| Load | reload without EXC/console lines, all 4 `bac_profanity_*` functions present |
| Normal, In/Out, AI off | 0,3 dtk, "Tidak ada kata kasar", 10 kata, 1 ringan, "Cari lagi di Ketat" -> 1 row "anjir." 22:41,00 Ringan, Dengar ulang, 70% |
| Row click / Dengar | CTI 22:40,00; preview WAV 2,78 dtk plays in CEP (`Audio` not paused, no error); beep 1 kHz -25,5 dB exactly over the word |
| Bip | stages as planned, 0,8 dtk; clone active; 4 keys [22:40,99 0 dB, 22:41,00 -inf, 22:41,38 -inf, 22:41,39 0 dB]; track renamed by `Track.name =`; 1 beep clip; 1 red marker "Sensor", comment "a***r. (Bip)\n[AC-PF]"; original: no keys, 3 tracks, no markers |
| Real mix (export) | clone: speech at -13..-30 dB around, the word replaced by a clean 1 kHz -25,5 dB tone, outside the word identical to the original (max diff 0,003); original: "anjir" audible (-20..-27 dB) |
| Senyap / Kecilkan / Suara kustom | no new track, -inf inside / -20 dB inside / mp3 converted, 0,6 dtk clip centred on the word on "AutoCut Sensor"; quoted path pasted from Explorer accepted |
| 30 fps | keys 22:40,9667 / 22:41,00 / 22:41,38 / 22:41,4133: not snapped or collapsed, back to 0 dB 1 frame after the word |
| Cut sequence | 6 keys over the two clips around the cut (low up to the edge, no restore key at the cut), "music" on A2 untouched |
| Cancel (Esc in "Atur volume") | toast "Dibatalkan. Sequence sementara ... sudah dihapus", sequence count unchanged, original active |
| Hapus hasil | "Yakin hapus? Klik lagi", then deleted, original reopened |
| Word lists | "Dasar  Kampret" -> "dasar kampret" in `%APPDATA%\AutoCutBOT\profanity.json`, removed again; built-in list loads in 0,2 dtk |
| Whole 34,6 mnt, AI on | 0,8 dtk, 1 hit |
| 49 s, Normal | 81 kata, 0 hits, no Ketat hint |

Fixed during verification:

1. Click at the end of every beep / custom sound: Premiere floors the clip length to frames and cut off the fade-out
   (measured 0,075 -> 0 in one sample at 22:41,375). Sounds now end with 70 ms of silence; after the fix the export
   shows the 5 ms fade (0,075 -> 0,011 -> 0). Also a custom sound longer than 3 dtk was cut with no fade at all
   (FFmpeg `-t` was an output option after `areverse`); `-t` is now an input option.
2. Re-run on an already censored sequence (the clone becomes active after Terapkan, so this is the natural next click):
   "Kecilkan" gave a MUTE (the base level was read while the old mute key was still there), the old beep stayed, a
   second marker was added, and Bip would add a second sensor track. Now the old sensor is replaced in the new clone
   (`pfUndoOld`) and `pfKeyClip` removes old keys before reading the base level. Live: Bip clone -> Kecilkan re-run
   = -20 dB inside, empty sensor track, 1 marker; -> Bip again = sensor track reused, 1 beep, -inf inside, 1 marker.
3. Dialog on a locked track: the result said "Sequence baru siap, 1 kata disensor" with 0 keys. Now error "Track
   terkunci" (clone deleted, "Coba lagi" works after unlocking; verified).
4. Review row at the docked 330 px width hid the "Dengar ulang" tag (it was hidden up to 330 px); now the % goes
   first (301-340 px), the source tag only below 300 px.

## Known gaps / UNVERIFIED

- Mono-clip Level component name "Internal Volume Mono" assumed by the prefix match (test media is stereo).
- Clips with speed != 1: keys use `tlSeqToMedia` (verified only for speed 1); preview plays the source at 1x.
- A re-run on a censored sequence relies on its `[Klipora-PF]` (or old `[AC-PF]`) markers to find the old keys; with "Tandai dengan marker" off
  only keys of a range censored again are replaced (the old beep track is still cleared).
- Clone names stack on a re-run from a clone ("X (Klipora) (Klipora)"), like the other tools.
- A word cut in two by an earlier cut keeps its uncut length in sequence time (measured: 0,13 dtk of following
  silence also muted); harmless for silence cuts, which keep whole words.
- AI accuracy is measured on synthetic sentences; the test media contains only "anjir" (no live AI decision on media).
