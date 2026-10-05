# Podcast Multicam (`podcast`)

The camera follows whoever is talking. Input: one mic track per speaker and one camera track per speaker
(stacked video tracks, already in sync), plus an optional wide camera. Output: a clone `<sequence> (Klipora)` whose
camera tracks are cut at every switch point, with the inactive camera clips **disabled** (`TrackItem.disabled`).
Audio is never cut or muted. The user's original sequence is never touched.

Why stacked tracks and not a multicam sequence: no ExtendScript/C++ API can create a multicam source sequence and
FCP7 XML multiclips import flattened (internal research, not published). Stacked tracks + Disable
is also the recommended mode of the autocut.com plugin. Beyond that plugin: auto mapping, sync check, a review step per shot, listener
reactions, and a "Ganti kamera di playhead" quick fix.

Names (Klipora rename): clones are "<sequence> (Klipora)", markers use `[Klipora-POD]`. The host edits (razor,
disable, "Ganti kamera di playhead") accept sequences named "(Klipora[ n])" and the old "(AutoCut[ n])"
(`acIsResultName`), and old `[AC-POD]` markers are still replaced. UI language: Indonesian and English
(`podcast.*` / `host.podcast*` panel keys, `podcast.*` engine keys; the engine follows the job's `lang`). Speaker
names and track names are user content and are never translated.

Files: `engine/ac/tools/podcast.py`, `engine/tests/test_podcast.py`, `panel/js/tools/podcast.js`,
`panel/css/tools/podcast.css`, `panel/host/40_podcast.jsx`, `tools/ui_tests/podcast.mjs`.

## Flow (panel, Bahasa Indonesia)

1. **Pembicara**: one card per speaker (name, Mic track, Kamera track), "Tambah pembicara" (max 6), "Kamera wide"
   (Tidak ada | V1 ...). Auto-guessed when the sequence opens and re-guessed with "Tebak ulang":
   - named tracks win: "Mic Andi" pairs with "Cam Andi"; a video track named wide/lebar/master/2shot is the wide;
     the speaker name comes from the mic track name without "Mic";
   - unnamed tracks: k-th audio track with clips = speaker k, k-th free camera track = their camera; one camera
     more than speakers = wide on the LOWEST track (V1 = master shot). Speakers are "Pembicara 1, 2, ...".
   - the mapping is remembered per sequence id (manual edits win; layout changes re-guess unedited mappings).
   - problems show as a warning and disable the primary button: fewer than 2 mic tracks with clips, empty track,
     two speakers on one mic track, wide = a speaker camera, all speakers on one camera.
2. **Gaya pindah kamera** presets and settings, then "Analisis N pembicara".
3. **Tinjau**: speaker ribbon (one lane per mic owner, grey lane = crosstalk, bottom bar = camera per shot, click =
   jump), legend with screen share per camera, sync / shared-file warnings, one row per shot (camera colour dot +
   name, first words if a transcript cache exists, tags Awal/Bersahutan/Diam/Selingan/Reaksi/Manual, confidence %).
   Unchecking a row keeps the previous camera through that shot; the active row has a button per camera to pick
   another camera by hand ("Manual" tag, note "Otomatis: X"). Filters per camera and "Kurang yakin". "Kirim ke
   marker" writes one marker per switch (`Kamera: Andi`, tag `[Klipora-POD]`, replaced on re-run). Re-running the analysis
   keeps unchecked rows and hand-picked cameras (stable ids).
4. **Terapkan N pergantian**: engine `apply` -> plan -> host: clone, razor in chunks of 20 cut points (progress,
   Batalkan between chunks), enable/disable per camera track, open the clone. Refuses when the analysed sequence
   changed length since the analysis ("sudah berubah sejak dianalisis").
5. **Hasil**: "Multicam siap", Pergantian / Shot rata-rata / Wide %, camera bar + legend, "Ganti kamera di playhead"
   buttons, "Buka yang asli", "Ubah pilihan", "Hapus hasil", next: Potong Silence, Bab Otomatis.

**Ganti kamera di playhead** (main pane and result card; Alt+1..Alt+5 on the main pane): the chosen camera becomes
the only enabled camera for the shot under the playhead. The shot = intersection of the clips under the playhead on
all camera tracks (tracks only have edits where their own state changes). "Potong dulu di playhead" starts the shot
at the playhead instead (razor there). Every camera track is razored at the shot edges where needed. Only works on
Klipora results (made by the panel in this Premiere session or named `... (Klipora[ n])` or the old `... (AutoCut[ n])`); on the original it shows
"Ini sequence asli. Buka sequence hasil (Klipora) dulu, sequence asli tidak diubah." The status line shows which
camera is visible at the playhead.

## Settings and defaults

| UI | Param | Default | Notes |
|---|---|---|---|
| Gaya: Tenang / **Normal** / Dinamis | confirm, min_shot, max_shot | 1,0/4/30 · **0,8/2/15** · 0,6/1,8/8 | research table (Natural = 0,8 s confirm, 2 s min shot per SPEC) |
| Durasi shot minimal | min_shot | 2 dtk (1-6) | never switch earlier; last shot shorter than this is folded into the previous one |
| Tunggu sebelum pindah | confirm | 0,8 dtk (0,2-2) | a speaker must hold the floor this long ("hmm" never switches) |
| Selingan saat satu orang bicara lama | max_shot | 15 dtk (Mati/30/15/8) | cutaway to the wide (no wide: the listener) at the best pause, 2,5 dtk long |
| Sisipkan reaksi pendengar | react | on | another mic's short sound (< confirm) inside a long shot -> 2 dtk on the listener, at most every 6 dtk, speaker keeps >= min shot on both sides |
| Bersahutan pindah ke wide | cross_wide | on (needs wide) | crosstalk >= 1 dtk |
| Diam lama pindah ke wide (lanjutan) | silence_wide | on (needs wide) | all mics quiet >= 2,5 dtk, cut 0,5 dtk into the pause |
| Selisih volume mic (lanjutan) | dom_db | 6 dB (3-12) | a mic owns a frame only when this much louder than every other mic |
| Pindah sebelum bicara (lanjutan) | preroll | 0,25 dtk | cut lands this much before the speaker's first frame |
| Menang saat bersahutan (lanjutan) | speakers[k].prio | Tidak ada | that speaker owns crosstalk frames when their mic is on |
| Cek sinkron audio (lanjutan) | sync | on | PHAT offsets, about 1 s for 35 min (cached envelopes) |
| Cakupan (source card) | scope | Seluruh sequence | In/Out supported; outside the range nothing changes, every camera is cut at the edges |

## Engine (`engine/ac/tools/podcast.py`)

`analyze` stages: `audio` Baca track mic (per speaker: envelopes of the mic track's clips mapped to sequence time;
muted mic tracks are read, disabled clips are silent) -> `owner` Cari siapa yang bicara -> `sync` Cek sinkron audio
(optional) -> `shots` Susun shot kamera. Result `{review, summary: "20 pergantian kamera, 2 pembicara", stats, sync,
notes, kept}`. Errors: `NEED_SPEAKERS`, `NO_MIC`, `NO_CAM`, `SAME_MIC`, `NO_SWITCH`, `NO_RANGE`, `BAD_PARAM`.

1. **Mic sources**: a media file used by the mic tracks of several speakers (dual-mono recorder file split into two
   tracks, OBS file with one stream per mic) is split: by audio stream when it has enough streams, else by channel
   (first mic track = channel 0 / left). Channel envelopes are cached as `<stem>_env_ch<n>.npy/.json` next to the
   media (hash checked). Not separable -> note "kebanyakan wide".
2. **Frame owner** (port of `podcast_sim.activity`): 150 ms moving max, per-mic Otsu gate, owner = on and >= dom_db
   above every other mic; >= 2 mics on without owner = crosstalk; some mic on without owner = unknown; all quiet =
   silent. Priority speakers win crosstalk frames.
3. **Base shots** (port of `podcast_sim.shots`, evaluated per run, same decisions): switch at the first frame where
   the run has lasted `confirm` and the current shot `min_shot`; cut `preroll` before the run (never before shot
   start + min_shot). Crosstalk -> wide, silence -> wide. The opening shot is the first wanted camera from the scope
   start (wide when the recording starts with >= 2,5 s silence).
4. **Cutaways** inside long speaker shots: reactions, then overflow cutaways at the longest pause in the last 40% of
   each `max_shot` window. Shots are mapped to camera tracks (speakers sharing a camera merge), snapped to sequence
   frames, contiguous over the scope.
5. **Review file** `<workdir>\podcast_review.json`: items `{id, t0, t1, kind: spk|wide|react, on, conf, label, cam
   (video track), auto (engine's camera), spk, why: awal|bicara|bersahutan|diam|selingan|reaksi|kembali, note, text,
   pick?, touched?}` + doc extras `fps, scope, speakers [{name, mic, cam, prio, thr, share}], cams [{track, name, kind,
   spk, track_name}], wide, lanes {"0": [[t0,t1]], ..., "x": crosstalk}, sync, notes`.
6. **Sync**: PHAT cross-correlation (port of `sync_test.find_offset`, scipy.fft, complex64) over a window of up to
   120 s in the middle of the scope, max lag 8 s: every other mic vs the first speaker's mic, every camera track's own
   audio vs the mic mix. Row `{kind: mic|cam, track, name, offset (+ = late), psr, ok: psr >= 5, warn: ok and |offset|
   >= 1 frame}`; warnings like "Mic Budi telat 0,20 dtk dari mic Andi. Geser clip-nya di timeline supaya sinkron, lalu
   analisis lagi." Detection only: clips are not moved (TrackItem.move is unverified).

`apply` reads the edited review -> `{"plan": {"kind": "multicam", "timebase": "sequence", "fps", "cams": [tracks],
"wide", "names", "shots": [[t0, t1, track]], "cuts": [[t, [tracks to razor]]], "scope": [lo, hi], "switches"},
"summary", "stats": {switches, shots, razor, share}}`. Unchecked rows keep the previous camera; cuts contain only the
two tracks whose state changes, plus every camera at scope edges.

## Host (`panel/host/40_podcast.jsx`, ES3)

| Function | Returns | Notes |
|---|---|---|
| `bac_podcast_razor(seqId, cuts, from, to)` | `{ok, done, skipped, from, to, ms}` | QE `getVideoTrackAt(i).razor(tc)` (`tlTimecode`), skips gaps/existing edits, unlocks locked camera tracks and locks them again, restores the CTI |
| `bac_podcast_disable(seqId, track, shots, scope)` | `{ok, track, enabled, disabled, changed, unsplit, outside, ms}` | one camera track per call; clip middle decides the shot; `unsplit` = clips still spanning a switch of that track |
| `bac_podcast_switchAt(cam, cams, split, seqId)` | `{ok, t, cam, changed, found, cut, from, to}` | quick fix at the CTI (see above) |
| `bac_podcast_camAt(cams, seqId)` | `{ok, t, id, name, result, visible, under}` | read only, for the buttons |

All editing functions refuse sequences Klipora did not make. The panel clones with the foundation `bac_cloneSeq`
(analysed sequence id from the review, so the active sequence may differ) and tracks progress with `ctx.track`.

## Measurements (this PC, 2026-10-05, synthetic: no real multi-mic podcast exists)

| What | Result |
|---|---|
| 35 min synthetic (research benchmark: real speech split into 283 turns, -18 dB bleed, backchannels), confirm 0,8 / min 2 | 269 shots, 0 under 1 s, median 7,0 s, right speaker 93,2% (prototype: 270 / 0 / 7,0 / 93,4%) |
| same, preset Normal with wide | 336 shots, right 90,8%, wide 5,9% of talk |
| 35 min analyze end to end (2 WAV mics, cached envelopes) | 1,9 s first run (decode), 1,0 s with sync; review 103 KB; plan 335 cut points = 670 track razors |
| 2 min synthetic (engine test), Normal + wide | 21 shots, right 84,7%, wrong speaker 2,3%, rest wide (long tutorial pauses) |
| Sync | 0,200 s delay found as +0,200 s (PSR 32); aligned mics/cameras 0,000 s (PSR 25-109) |

Expected Premiere cost (UNVERIFIED): about 64 ms per razor (lab: qeSeq.razor), so 670 razors take about 45 s with
Premiere frozen per 20-cut chunk (about 2,6 s each). An XML route (export, split + `<enabled>FALSE`, import) would be
far faster for long podcasts but is not built (open issue).

## Tests

- `python engine/tests/test_podcast.py` (~6 s, offline): shot rules on hand-built labels, cutaways, PHAT offset,
  plan rules, then the synthetic 2-speaker timeline (accuracy, min shot, frame alignment, sync, review/plan, carry over,
  stereo L/R split, 0,2 s late mic, In/Out scope, error codes). `--bench` adds the 35 min benchmark. `--make <dir>`
  writes the verifier fixture (mic_a.wav, mic_b.wav, mics_lr.wav, mic_b_late.wav, truth.json, podcast_test.xml,
  podcast_test_distinct.xml).
- `node tools/ui_test.mjs tools/ui_tests/podcast.mjs --engine live` (36 checks): the ES3 host file in node vm against
  a fake Premiere model (razor/disable give exactly one enabled camera at every instant, In/Out edges, locked track,
  switch at playhead with and without split, refuses the original), then the panel with the real engine on the
  synthetic fixture (auto mapping, invalid mapping, analyze, review ribbon/filters/pick/uncheck, apply calls, result,
  switcher, Alt+2, changed-sequence refusal, Riwayat). Screenshots (gitignored `--shots` folder): `build_podcast.png`,
  `build_podcast_review.png`, `build_podcast_result.png`.

## Verified vs UNVERIFIED

- Verified offline: engine algorithm and plan, ES3 host logic against a simulated track model, panel flow in
  headless Chrome with the real engine.
- UNVERIFIED in Premiere 26.2.2: QE video-track razor timing and behaviour on linked A/V clips, `TrackItem.disabled`
  writes and Program Monitor refresh, unlocking/relocking camera tracks, the XML test-sequence import, channel order
  of dual-mono files split in Premiere, real podcast footage (real bleed, laughter, different mic gains), and camera
  scratch audio clips (left enabled; only video clips are disabled).

## What the Premiere verifier must test (step by step)

Work in bin `Klipora Test`, never save, delete only what you created (diff project items before/after), restore
the user's active sequence at the end.

1. Reload the panel (`node tools/reload.mjs`): no EXC/console lines, host `loadErrors` empty, `typeof
   bac_podcast_razor` is a function (`node tools/ev.mjs "typeof bac_podcast_razor"`).
2. Fixture: `python engine/tests/test_podcast.py --make "%USERPROFILE%\Videos\Klipora\podcast_fixture"`.
3. Import the distinct-camera sequence through the panel:
   `AC.host.json('bac_importXmlAndOpen', '<fixture folder>\\podcast_test_distinct.xml', 'AC Podcast Test Distinct')`
   (`<fixture folder>` = the folder from step 2, with escaped backslashes).
   Expect no dialog, V1/V2/V3 = `talk_35m.mp4` from 0 / 300 / 600 s, A1 = mic_a.wav, A2 = mic_b.wav,
   about 2:07. (If the import fails: `createNewSequenceFromClips` with the 34,6 min clip, then `tlPlaceClip` the video
   on V2/V3 with in 300/600 and the WAVs on A1/A2, deleting the camera audio that comes along.)
4. Open Podcast Multicam. Expect Pembicara 1 = A1 + V2, Pembicara 2 = A2 + V3, Kamera wide = V1, primary
   "Analisis 2 pembicara". Rename them Andi / Budi. Screenshot (gitignored `--shots` folder): `podcast.png`.
5. Run it. Expect the 4 stages, about 20 pergantian kamera, the ribbon with two lanes. The sync rows for cameras will
   say "not trusted" internally (distinct video audio), no warning lines. Click a row: the playhead moves.
   Uncheck one row, pick "Wide" on another. Screenshot (gitignored `--shots` folder): `podcast_review.png`.
6. "Terapkan N pergantian". Record the time per razor (apply duration / razors). Expect "Multicam siap", the clone
   `AC Podcast Test Distinct (Klipora)` active, original unchanged (3 video clips, all enabled).
7. On the clone, read every V1-V3 clip (start, end, disabled) and check against the plan (`AC.tools.runtime('podcast')`
   review doc or `<workdir>\podcast_review.json` + apply logic): every switch is a clip edge on the two tracks that
   change, and at every 0,5 s exactly one camera clip is enabled, the planned one; A1/A2 still one enabled clip each.
8. Visual check: `bac_exportFrame` at the middle of 3 shots (one per camera) and compare with the FFmpeg frame of the
   34,6 min file at `in + t` for the expected camera (300/600/0 s offsets); mean abs difference should be small (< 5)
   and large (> 20) for the wrong cameras. This proves `disabled` takes effect in the Program Monitor.
9. Ganti kamera di playhead on the clone: `bac_seek` into a shot, click another camera (and Alt+2 with the panel
   focused): that shot only changes, still exactly one enabled camera everywhere. Turn on "Potong dulu di playhead",
   repeat in the middle of a shot: a new edit at the playhead on all camera tracks, only the part after it changes.
   Lock V2 first once: the switch still works and V2 is locked again afterwards.
10. Open the original (Buka yang asli) and click a camera button: error toast "Ini sequence asli ...", nothing changes.
11. Re-run the analysis on the original after changing nothing: the unchecked row and the picked camera are kept.
12. Cleanup: `bac_deleteSequence` for the clone(s) and the imported test sequence, delete only the project items the
    XML import created (mic WAVs, duplicate master clips), delete the fixture folder, reopen the user's sequence.
