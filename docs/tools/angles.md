# Auto Angles / Angle Otomatis (tool id `angles`)

**Klipora** (formerly AutoCut BOT). UI in Bahasa Indonesia or English (Settings > Display language): English name
"Auto Angles" (tab "Angles"), angles Wide / Medium / Close. Panel texts: keys `angles.*` / `host.angles*`; engine texts
(stages, row labels and notes, warnings, errors, summary) follow the job's `lang` (keys `angles.*` in
engine/ac/locales). The clone is named `<name> (Klipora)` (`AC.brand.suffix`; an old "(AutoCut n)" suffix is stripped
first via `AC.brand.suffixRe`), markers are tagged `[Klipora-ANG]` (old `[AC-ANG]` still recognised).

Single-camera "virtual multicam" for screen-recording tutorials (and later talking heads). Every clip of the camera
track gets a **static punch-in angle**: Lebar 100%, Sedang 118%, Dekat 140% (configurable). The angle changes on every
cut and **never repeats twice in a row**, screen time follows the weights 3/2/1, and the crop is **anchored on the
action** (frame-difference activity of the screen) or on a face. Optionally long clips are split at sentence starts
("Tambah potongan di awal kalimat") to get more angle changes. Everything is applied to a **clone**; the user's
sequence is never touched. No animation, no keyframes: one Scale + Position value per clip.

Files: `engine/ac/tools/angles.py`, `engine/tests/test_angles.py`, `panel/js/tools/angles.js`,
`panel/css/tools/angles.css`, `panel/host/39_angles.jsx`, `tools/ui_tests/angles.mjs`.
Background: internal research notes and prototypes (not published) on cutting, vision and the Premiere timeline API; the angle and activity
prototypes were ported and hardened (see "How it decides").

## What the user sees

1. **Main pane** (`#tool/angles`): source card + scope (Seluruh sequence / In/Out / Clip terpilih), then
   - **Gaya angle** presets: Halus (100/112/125%, min 3 dtk, shot ~8 dtk), **Normal** (100/118/140%, 2,5 dtk, ~6 dtk),
     Sering (100/120/150%, 2 dtk, ~4 dtk). Manual edits show "Custom".
   - **Variasi crop**: a diagram of the nested crop boxes + chips Lebar / Sedang / Dekat (minimum 2).
   - **Kapan ganti angle**: "Di setiap potongan" (default, every jump cut changes angle) or "Jaga durasi minimum"
     (rhythm: change only after the shot lasted >= Durasi shot minimal); slider Durasi shot minimal 1-6 dtk (2,5);
     switch **Tambah potongan di awal kalimat** (off by default) + "Panjang shot sekitar" 3-12 dtk (6).
   - **Titik fokus**: Otomatis (default) | Aksi layar | Wajah | Tengah.
   - **Pengaturan lanjutan**: Skala Lebar 100-130%, Sedang 104-170%, Dekat 110-220% (must increase by >= 2%), Porsi
     angle (Banyak Lebar 3/2/1 = default, Rata 1/1/1, Banyak Dekat 1/2/3), Track kamera (Otomatis = video track with
     the most media time, or V1..Vn).
   - A notice when the camera track has fewer than 3 clips and splits are off ("Sequence ini cuma 1 clip") with a
     button that turns the sentence splits on.
   - Dock: **Buat angle** (Ctrl+Enter).
2. **Proses**: Baca sequence, Baca transkrip / Cari awal kalimat, Cari titik aksi, Atur angle.
3. **Tinjau**: one row per shot (clip or split piece): timecode, angle mark ("Sedang 118%") + the words spoken in
   the shot, tags (focus source: Aksi layar / Wajah / Tengah / Aksi sebelumnya, "Potongan baru", "Dipilih"), notes
   (split, held angle in rhythm mode, "Aksi lebar, tepi layar bisa sedikit terpotong"). Above the list:
   - an **angle lane** (block height = how close; click = jump to the shot) replacing the cut ribbon,
   - **Pratinjau crop**: the real source frame of the active shot with the crop box of every angle (the chosen one
     dims the rest of the frame), the focus point dot and a Lebar/Sedang/Dekat switch. Click a box, press **1/2/3**
     or use the row buttons to change the angle of the active shot. A warning appears when the pick equals a
     neighbour (the cut would feel like a jump again). "Sembunyikan" collapses it (remembered).
   - Checkbox = "diatur" (apply) / "dibiarkan" (leave the clip untouched). Filters: Semua, Diatur, Dibiarkan, Lebar,
     Sedang, Dekat, Potongan baru, Perlu dicek. "Kirim ke marker" adds markers "Angle Sedang 118%" tagged `[Klipora-ANG]`.
   - Dock: Batal | **Terapkan N angle**.
4. **Hasil**: "Sequence baru siap", counts per angle (+ "Potongan baru"), the final angle lane, Buka yang asli,
   Ubah pilihan (back to Tinjau, apply again = another clone), Hapus hasil (deletes the clone), warning when clips
   were skipped (keyframed Motion) or a shot did not match a clip. Lanjut ke Auto Zoom / Auto Caption.

## Engine (`engine/ac/tools/angles.py`)

| Action | In | Out |
|---|---|---|
| `analyze` | `seq` (Timeline JSON), `params` below | `{review, summary: "15 shot: 8 Lebar, 4 Sedang, 3 Dekat", stats, carried, track, duration}` |
| `apply` | `review` (edited file) | `{plan: {kind: "keyframes", mode: "static", tool: "angles", track, fps, seq: {id, name}, splits: [sec...], items: [{id, t0, t1, angle, scale, pos: [x, y]}]}, summary, by_angle, splits}` |
| `frame` | `params {media, t (source s), width (480), dir}` | `{path: jpg, w, h, t}` (preview; worker; cached in `<workdir>\angles_frames`, newest 80 kept) |

Params (`analyze`): `scales {wide, medium, close}` (%, default 100/118/140), `use` (enabled angles, >= 2),
`weights` (`"lebar"` 3/2/1 default, `"rata"`, `"dekat"`, or a list/dict), `mode` (`"cut"` default | `"rhythm"`),
`min_shot` (2,5 s), `split` (false), `every` (6 s, target length of split pieces), `anchor` (`auto` | `activity` |
`face` | `center`), `track` (`"auto"` | 0-based index), `scope` (panel scope dict). Errors: `BAD_PARAMS` (fewer than 2
angles, scales not increasing), `NO_VIDEO`, `NO_TRACK`, `NO_SHOTS` (no clip in the scope), plus the foundation codes.

Review rows (kind `shot`, id `s<t0 cs>`): `angle, scale, pos, base, anchor {x, y, src, box}`,
`opts {angle: {scale, pos, view [x0, y0, w, h], fits}}` for every enabled angle (so the panel switches angles without
the engine), `cut_in / cut_out` (edge is a new razor point), `why` (`cut` | `sentence` | `scope`), `track, clip,
media, sm` (source time of the shot middle, for the preview), `src_wh`. The panel sets `picked: true` when the user
chose an angle and `touched: true` on any edit; a re-run keeps both (`carry_over`).

### How it decides

- **Shots**: usable clips of the camera track (enabled, not graphics, media or nested). With splits on, scope edges
  inside a clip become razor points too, and each in-scope piece is split just before a sentence/phrase start
  (Whisper punctuation, segment end, comma, or a pause >= 0,35 s) so pieces are >= `min_shot` and close to `every`.
  Words come from `Timeline.words_on_timeline` (transcribes once if no cache; the GPU lock is taken by the transcript
  module). Without splits only cached words are read (row text).
- **Angle choice**: largest screen-time deficit vs the weights wins, never the previous angle, ties go wider; the
  first shot is Lebar. An angle whose window cannot hold the action box (10-90 % of the activity) is avoided when
  another allowed angle fits; if none fits, the widest allowed angle is used and the row gets a warning. In rhythm
  mode the angle is held until the shot lasted `min_shot` (note "Angle ditahan"). An untouched (out-of-scope) clip
  counts as Lebar for the never-repeat rule.
- **Anchor** (static per shot): `auto` = a face only when it is big (>= 12 % of the height) and present in >= 60 % of
  the 1 fps samples (screen recordings contain small faces in web ads: ignored), else the **activity** centre: frame
  differences at 4 fps / 640 px (CPU, `-skip_frame noref`), 48x20 grid in 0,5 s bins, the shot itself plus 4 s before /
  2 s after at half weight, edge cells damped (taskbar, toasts, tab strip), repeated tiny changes muted (tab spinner,
  clock), and "only edge activity" rejected. No activity: the last action anchor of the same media within 15 s, else
  the centre. `face`: face, else activity, else centre. `center`: no image analysis.
- **Motion values**: base scale = fill (`100 * max(seq_w / W, seq_h / H)`; 100 for the user's same-size media), Scale =
  base x angle%, Position (normalized 0..1, where the clip centre lands) = `0.5 + (0.5 - anchor) * k`, clamped to
  `[1 - k/2, k/2]` so the frame is always covered (k = clip size in frame widths). Lebar 100% is always centred. A
  re-run on an angled clone resets every clip to the same values (no compounding).

### Caches and files

- `<media stem>_angles.npz` next to the media (activity grid, face samples, which bins are done; keyed by the file hash
  and analysis settings; only the source ranges the shots use are analysed, later runs add missing ranges).
  Sizes measured: 49 s file 8 KB, 34,6 min file 264 KB, 2 min file 14 KB.
- `<workdir>\angles_review.json`, `<workdir>\angles_frames\*.jpg` (preview frames, ~10-40 KB each, max 80).

### Measured (this PC, 2026-10-05)

| Case | Result |
|---|---|
| cut15 (15 clips of the 49 s file) | first run 0,95 s (activity 47 s of source), cached 0,01 s; 15 shots, 8 Lebar / 4 Sedang / 3 Dekat, 0 repeats, 12 activity anchors |
| 49 s raw, splits on (every 6 s) | 8 shots, 7 splits at sentence starts, all pieces >= 2,5 s |
| 34,6 min file, legacy silence cut (727 clips) | first run 44,5 s (2048 s of source analysed = 0,021x realtime), planning 0,18 s; 353 / 240 / 134 shots, 0 repeats |
| 34,6 min raw, splits on | 1,1 s with the cache (290 shots, 289 splits) |
| 2 min file, splits on | 4,8 s first run, 18 shots |

## Host (`panel/host/39_angles.jsx`, ES3)

| Function | Returns | Notes |
|---|---|---|
| `bac_angles_split(seqId, times, track)` | `{ok, done, ms, clips}` | QE `razor(tlTimecode(frame))` on the clone (made active) = add edit on every track; all tracks are unlocked, sync-locked and targeted first and restored after (`tlTrackStates`/`tlApplyTrackStates`); In/Out and playhead restored. Times outside the sequence are skipped |
| `bac_angles_apply(seqId, track, items)` | `{ok, set, keyed, nomotion, items, matched, bad, ms}` | Every clip on V(track+1) whose middle lies in an item gets Motion `properties[1]` (Scale) and `[0]` (Position) via `setValue` (static). Clips with keyframed Scale/Position are skipped (`keyed`). A locked track is unlocked for the call and locked again. `bad` = Scale read back differently |
| `bac_angles_read(seqId, track)` | `{ok, name, clips: [{start, end, scale, pos, keyed}]}` | For checks |

Panel order (`angles.js` `applyPlan`): `bac_cloneSeq("<name without (Klipora|AutoCut n)> (Klipora)", analysedSeqId)` ->
`bac_angles_split` in adaptive chunks (only when the plan has splits; starts at 20 razors, then sized so one call
takes ~1,2 s, 8..60) -> `bac_angles_apply` in chunks of 60 -> `bac_openSequence(clone)`. Progress through `ctx.track`
(stages Gandakan sequence / Tambah N potongan / Atur N angle), Riwayat entry "Terapkan angle" with the summary.
Cancel (Esc / Batalkan) stops between chunks, deletes the half-made clone (`bac_deleteSequence`), reopens the source
and toasts "Dibatalkan. Tidak ada yang berubah." (if the delete fails, the message names the partial clone).

## Tests (all offline, no Premiere)

- `python engine/tests/test_angles.py` (~1 s with caches): Motion math (clamp, fill for other sizes), params errors,
  sentence splits, planner rules (no repeats, weights, rhythm hold, fit avoidance), analyze/apply on the cut15 sequence
  with real media (activity anchors, transcript text), user edits + carry-over, raw 49 s with splits, In/Out scope
  edges as razor points, 1-clip warning, `anchor: center` with 2 angles, other-size warning, `NO_SHOTS`/`NO_TRACK`,
  the `frame` action, one real `cli.py run` (JSON lines only on stdout).
- `node tools/ui_test.mjs tools/ui_tests/angles.mjs --engine live --seq cut15` (46/46, also `--engine mock`
  38/38 and `--width 280 --height 560` 46/46; the 46th check is a real `elementFromPoint` hit test of every crop box
  while Lebar is active, added by the live verifier): runs the REAL `39_angles.jsx` in a Node vm against a mocked Premiere DOM
  (razor on unlocked tracks, Motion params: split count, lock/target restore, values per clip, keyed clip skipped,
  locked camera track), then the page flow with the real engine: main pane, analyze, review (15 rows, no repeats,
  preview frame from the worker, 3 crop boxes, key 3, crop-box click, uncheck), apply (clone from the analysed id,
  values per checked shot, edited angle reaches the host, no razor without splits, clone opened, result card, review
  written back, Riwayat), then raw49: notice -> splits -> razor times sorted inside the sequence.
- Screens (gitignored `--shots` folder): `build_angles.png` (main), `build_angles_settings.png`, `build_angles_review.png`,
  `build_angles_result.png`, and the 280 px variants `build_angles_280.png`, `build_angles_review_280.png`,
  `build_angles_result_280.png`.

## Live verification (Premiere 26.2.2, 2026-10-05)

Run through the panel UI with real mouse/key events (CDP `Input.*`) in bin `Klipora Test`; everything created
was deleted afterwards, the project was never saved.

| Check | Result |
|---|---|
| Load | 3 host functions present, no console errors (panel monitored the whole session) |
| Static `setValue` (Scale 140, Position [0.3, 0.36]) | `{set:1, bad:0}`, read back 140 / [0.3, 0.36], `keyed:false` (no stopwatch). Exported frame at 10 s vs ffmpeg `crop=1637:686:655:233,scale=2292:960`: mean abs diff **1,40** (uncropped frame: 17,2) |
| cut15 (15 clips, Normal) | analyze 0,4 s (cache), 15 rows, 0 neighbours with the same angle, lane + real preview frame with 3 boxes; row click -> playhead 7,35 dtk + toast; key 3 -> "Dekat 140%"; crop-box click; row button; uncheck |
| Terapkan 14 angle | clone "AngTest cut15 (AutoCut)" opened in 0,4 s; all 15 clips read back exactly as the review (unchecked clip 100 / centre); source all 100 / centre; 15/15 clips, 31,25 dtk. Frame checks at 5 clips (Dekat, Sedang, Lebar, unchecked): mean abs diff 1,2-4,8 vs crop reference (16-34 vs uncropped) |
| Result card | Buka yang asli, Ubah pilihan (back to Tinjau, apply again = "(AutoCut 2)"), Hapus hasil (two clicks, clone gone), Riwayat entries |
| Re-run | carry-over keeps picked angles and unchecked rows; re-run on an angled clone resets values (no compounding) |
| Sentence splits (raw 49 s, In/Out 5-40, playhead 3 s, A3 locked) | notice + "Nyalakan potongan kalimat"; 8 rows / 7 "Potongan baru"; clone V1 **and** A1 = 8 clips at the review times (exact), no gaps, same duration; In/Out, playhead and lock/target states unchanged; original still 1 clip. Razor on an existing edit = no-op |
| Keyframed clip + locked V1 | "1 clip dilewati" warning, keys (100 -> 150) intact, other 14 set, V1 locked again on the new clone |
| In/Out scope + splits | scope start becomes a razor point (cut_in); an edge < 0,25 s from a clip end is not cut (by design, no slivers) |
| Errors | audio-only sequence: notice "Belum ada clip video", engine NO_VIDEO -> "Tidak ada clip video di sequence ini. Taruh rekaman di track V1 lalu coba lagi." |
| Kirim ke marker | 15 markers "Angle Sedang 118%" tagged `[AC-ANG]`; second click replaces (still 15) |
| 34,6 min after Potong Silence (407 clips) | analyze 1,8 s (activity cache), 0 repeats (197/137/73); apply 1,1 s, 407/407 values exact, source unchanged |
| 34,6 min raw + splits (290 shots, 289 splits) | razor 15 s with adaptive chunks (Premiere evalScript latency <= 1,5 s between calls; fixed chunks of 40 took 19 s with up to 3,5 s blocks); V1 = A1 = 290 clips, all boundaries exact, values exact; Esc during razor -> partial clone deleted, source reopened, "Dibatalkan. Tidak ada yang berubah." |

Fixed during verification: active crop box was raised with `z-index` (the full-frame Lebar box swallowed every click on
Sedang/Dekat; `angles.css`), cancel left a half-made clone (`angles.js`), razor chunks blocked Premiere up to 3,5 s
(adaptive chunks, `angles.js`). Screens (gitignored `--shots` folder): `angles_*.png`.

## Still unverified / limits

1. Clips with "Scale to Frame Size", a moved Anchor Point or non-uniform scale: the fill math assumes native pixels
   and a centred anchor. Same-size media (all test media) are exact. Analyze warns when media size differs.
2. Speed-changed clips: the anchor uses the speed-aware source mapping; Motion is time independent, so this is low risk.
3. The result card's per-angle counts come from the plan, so a clip skipped for keyframes is still counted (the
   sub line "14 clip diberi angle" and the "1 clip dilewati" warning are exact).

## What the Premiere verifier must test (step by step)

Work in bin `Klipora Test`, on sequences built from the test media; never save; delete only what you created;
restore the user's active sequence at the end.

1. **Load**: `node tools/reload.mjs` -> no exceptions; `node tools/cdp.mjs "AC.host.eval('typeof bac_angles_apply + typeof bac_angles_split + typeof bac_angles_read')"`
   -> `functionfunctionfunction`; `AC.log.errors` empty.
2. **Static Motion setter (UNVERIFIED 1)**: make a test sequence from `talk_49s.mp4`
   (`createNewSequenceFromClips`), clone it (`bac_cloneSeq`), then on the clone:
   `bac_angles_apply(cloneId, 0, [{id:'x', t0:0, t1:60, scale:140, pos:[0.3, 0.36]}])` -> `{set:1, bad:0}`;
   `bac_angles_read(cloneId, 0)` -> scale 140, pos [0.3, 0.36]; Effect Controls shows Scale 140 and Position
   (0.3 x 2292, 0.36 x 960) = (687.6, 345.6) px with no keyframe stopwatch on. Export frame 10 s
   (`bac_exportFrame(10, '<scratch>\\ang_10', cloneId)`) and compare with
   `ffmpeg -ss 10 -i "<49 s mp4>" -frames:v 1 -vf "crop=1637:686:655:233,scale=2292:960" ref.png`: mean abs
   difference should be small (lab zoom check: 2,2-2,5; a wrong mapping gives > 30).
3. **Panel flow on a cut sequence**: run Potong Silence on the test sequence (or use any multi-clip sequence),
   open it, Angle tab, preset Normal, press **Buat angle**. Expect the 4 stages, then Tinjau with one row per V1 clip,
   no two neighbours with the same angle, the angle lane, and the Pratinjau crop showing the real frame with 3 boxes.
   Click a row: the playhead moves (toast). Press 3 on a row: its mark becomes "Dekat 140%". Uncheck one row.
4. **Terapkan**: new sequence "<name> (Klipora 2)" (or similar) opens. For 3-4 clips compare Effect Controls
   Scale/Position with the review rows (Lebar = 100 and centred position; Sedang 118; Dekat 140); the unchecked clip
   keeps 100 / centre; clip count and duration equal the source sequence; the source sequence is unchanged (all clips
   Scale 100). `bac_angles_read` on the clone lists the same values. Scrub over a few cuts: every cut changes framing.
5. **Sentence splits**: on the raw 1-clip test sequence the notice "Sequence ini cuma 1 clip" shows; press "Nyalakan
   potongan kalimat", Buat angle. Tinjau shows ~8 rows with "Potongan baru". Terapkan: the clone has the same number of
   clips on V1 **and** A1 (razor on all tracks), boundaries at the review times (+-1 frame), no gaps, same duration;
   the original still has 1 clip; track lock/target states, In/Out and playhead of the clone are as before.
6. **Keyframed clip**: on a clone, add a Scale keyframe to one clip (or run Auto Zoom first), apply angles to that
   sequence: the result warns "1 clip dilewati" and the keyframes are intact.
7. **Locked camera track**: lock V1 on a test sequence, apply: angles are set and V1 is locked again on the clone.
8. **Hapus hasil** deletes the clone; "Buka yang asli" re-opens the source.
9. **Long sequence** (optional): 34,6 min file after Potong Silence (~700 clips): analyze ~45 s the first time
   (cached afterwards), apply in chunks of 60 with progress; Premiere stays responsive between chunks.
10. Cleanup: delete test sequences/bin items you created, restore the user's active sequence and playhead.
