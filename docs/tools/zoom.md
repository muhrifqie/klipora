# Auto Zoom (tool id `zoom`)

**Klipora** (formerly AutoCut BOT). UI in Bahasa Indonesia or English (Settings > Display language, default follows
Premiere): English names are "Auto Zoom", modes "Follow screen action" / "Speech emphasis" / "Rhythmic". Panel texts:
locale keys `zoom.*` / `host.zoom*` (panel/locales); engine texts (stage labels, review notes, warnings, errors,
summary) follow the job's `lang`: keys `zoom.*` in engine/ac/locales.

Punch-in zooms that follow what matters, applied as **Motion Scale/Position keyframes per clip** (clip MEDIA time) on a
**clone** `<sequence> (Zoom)`. The user's sequence is never touched; the project is never saved.

| Mode (UI) | `params.mode` | What triggers a zoom | Where it zooms | Default zoom range |
|---|---|---|---|---|
| **Ikuti aksi layar** (default) | `screen` | Frame-difference activity of the screen recording: clicks, typing, menus, hover changes, cursor dwell on UI | The action box (zoom makes it ~60 % of the frame) | 1,25x to 2,2x |
| Penekanan bicara | `speech` | Action words (klik, isi, buka...), emphasis words (penting, jangan, gratis...), numbers, sentence starts, louder-than-local words, timeline cuts, optional AI "momen penting" | Biggest face (YuNet), else the centre | 1,12x to 1,3x |
| Ritmis | `rhythm` | Every other sentence (two-camera feel) | Face, else centre | 1,15x |

Files: `engine/ac/tools/zoom.py`, `engine/tests/test_zoom.py`, `panel/js/tools/zoom.js`, `panel/css/tools/zoom.css`,
`panel/host/37_zoom.jsx`, `tools/ui_tests/zoom.mjs`, this file.
Background: verified internal prototypes and research notes (not published) were ported: the autozoom prototype
(activity map, clusters, sessions, pacing, page-change exits; the 49 s result is reproduced exactly), the zoom plan
prototype (speech triggers, rhythm budget, ramps), and the Premiere notes on media-time keys, normalised Position and
interpolation codes.

## What the user sees

1. **Main pane** (`#tool/zoom`): source card + scope (Seluruh sequence / In/Out / Clip terpilih). In screen mode the
   card shows "Analisis layar tersimpan" (cache exists, re-analysis takes ~1 s) or "Analisis pertama sekitar N dtk".
   - **Mode**: radio cards Ikuti aksi layar | Penekanan bicara | Ritmis.
   - **Gaya zoom**: Kekuatan Ringan / **Sedang** / Kuat (hint shows the range, e.g. "Zoom 1,25x sampai 2,2x"),
     **Maks zoom per menit** 1-12 (screen 6, speech 4; hidden for Ritmis), **Animasi** Halus / Cepat / Lompat.
   - **Zona abaikan** (screen only): chips Taskbar + Notifikasi (default on), Bar browser, Tepi layar.
   - **Pengaturan lanjutan**: Zoom minimal / maksimal (1,05x-3x, manual = "Custom"), Durasi animasi (0,15-1,2 dtk),
     Momen penting dari AI (speech only, off by default, 1 AI request, rule fallback), Track yang di-zoom
     (Otomatis = lowest video track with media; V1..Vn).
   - Dock: **Cari momen zoom** (Ctrl+Enter).
2. **Proses**: screen = Baca sequence, Analisis aksi layar, Baca transkrip, Susun zoom, Buat gambar pratinjau;
   speech/rhythm = Baca sequence, Transkripsi, Ukur penekanan suara, (Momen penting (AI)), Susun zoom, Cari wajah,
   Buat gambar pratinjau.
3. **Tinjau**: "N zoom dipakai dari M momen", ribbon of the zoom moments, legend "X dtk ter-zoom", filters Semua /
   Dipakai / Dilewati / Zoom kuat (>= 1,8x) / Keluar instan (screen) / AI (speech).
   - Each row: thumbnail (real frame at the middle of the zoom, outside darkened, accent rectangle = the zoomed
     viewport), timecode, zoom level mark ("2,2x") + words spoken during the zoom, note (e.g. "Keluar instan saat layar
     berganti", "Kata aksi «buka»", "AI: ..."), duration "dipakai/dilewati".
   - Click a row = Premiere playhead to the zoom start (`bac_seek`). Active row buttons: Lompat, Lewati zoom ini /
     Pakai zoom ini, **Pratinjau video**.
   - **Pratinjau zoom** box above the list: big thumbnail of the active moment, its zoom and time range, button
     **Video pratinjau** = engine worker renders that moment +-1 s with the zoom applied (NVENC, 1146x480, 30 fps,
     with audio; ~1,5 s for 12 s) and plays it inline; "Buka di pemutar" opens the mp4. "Sembunyikan" collapses it.
   - Bulk "Kirim ke marker" adds markers tagged `[Klipora-ZOOM]` (replaced on re-run; the old `[AC-ZOOM]` tags are recognised and cleared too).
   - Dock: Batal | **Terapkan N zoom**.
4. **Terapkan**: engine `apply` writes the keyframe plan, then the host clones the sequence and sets the keys in
   chunks (progress: Gandakan sequence, Pasang N keyframe di M clip, Buka hasil), then opens the clone.
5. **Hasil**: "Zoom terpasang", stats Zoom / Clip / Keyframe, ribbon, "Buka yang asli", "Ubah pilihan" (back to
   Tinjau, apply again = another clone), "Hapus hasil" (two-step; deletes the clone made in this session), warnings
   for skipped clips, next steps Auto Caption / Auto Resize.

## Engine (`engine/ac/tools/zoom.py`)

Actions (`ACTIONS = analyze, apply, preview`), job protocol per SPEC section 2.

### analyze
`params`: `mode` (`screen`|`speech`|`rhythm`), `intensity` (`ringan`|`sedang`|`kuat`), `zmin`/`zmax` (override),
`per_min`, `ramp` (`smooth`|`fast`|`jump`; rhythm default `jump`), `ramp_s` (zoom-in seconds), `ignore` (zone ids
`taskbar notif browser edges` or `[x0,y0,x1,y1]` rects; screen default taskbar+notif), `ai` (speech), `track`,
`scope` (panel dict). Result: `{review, mode, summary: "5 zoom dipakai dari 5 momen", stats {n, on, sec_on, per_min,
coverage, thumbs}, warnings, track, duration}`.

Review items (`kind: "zoom"`, `id` = `z<t0 cs>`): `t0` = ramp start, `t1` = back at 1x, plus `t_full` (zoom complete),
`t_hold` (zoom-out starts / instant exit), `z`, `cx`, `cy` (viewport centre, normalised source), `ease_in`,
`ease_out`, `end` (`ease`|`cut`), `anchor` (`aksi`|`wajah`|`tengah`), `src`, `why`, `bbox` (screen), `thumb` (PNG
path), `label` "Zoom 2,2x", `ctx` (words), `note`. Doc extras: `mode`, `track`, `sig` (target clip signature), `fps`,
`width`, `height`, `cfg`. Re-analysis keeps the user's manual on/off (`carry_over`, same mode + sequence).

How screen mode decides (port of `autozoom.py`, unchanged constants):
- **Activity** per source file, 10 fps, gray, half width (1146 px), CPU decode with `-skip_frame noref`: `absdiff > 24`;
  changed fraction > 0.18 = **global** (page change, scroll, app switch); animated cells masked by a ~10 s EMA; local
  components -> boxes (cursor = small compact blob, weight x0.3). Cached as `<media stem>_zoomact.npz` next to the
  media (key: version + file hash + settings).
- **Timeline mapping**: samples of every clip of the target track mapped to sequence time; **hard cuts** = gaps,
  other file, backwards jumps, or a page change inside the removed part of the same file (silence-cut boundaries
  are soft, a zoom may continue across them). Hard cuts and scope ends act as global events.
- Cursor dwell (>= 0.6 s on UI, edge density >= 4 %) adds attention; clusters (fit at zoom >= 1.5, closed by 1.2 s idle
  / global / spread) -> sessions (gap <= 2.7 s, no global between, union fits at the minimum zoom).
- **Rhythm budget**: densest sessions first, total zoom time <= 60 % of the scope and at most `per_min` zoom starts in
  any 60 s window. Sessions outside the budget are still listed, unchecked.
- Zoom completes 0.15 s before the action (snapped to a word start when a transcript cache exists), holds until the
  last activity + 0.9 s (snapped to a word end), exits **instantly at a following page change** (1 frame) or eases out.
  "Lompat" lands the instant zoom-in on a nearby timeline cut when one is within 0.3 s.
- Ignore zones: boxes whose centre lies in a zone take part in the relative weight filter, then never steer the zoom.

Speech: candidates scored (sentence start +1.5, action word +2.5, emphasis +2.0, number +1.5, loudness z > 1.2 +min(2.5, z),
hard cut +1.0, AI moment +5.0); greedy best-first with budget `round(minutes x per_min)` and min gap `max(1.2, 20 /
per_min)` s; extra non-overlapping candidates are listed unchecked. The zoom lands on the trigger word (ramp before
it), holds to the sentence end (1,8-4,5 s), exits at a hard cut when one comes later than 1,5 s. Zoom = min + (max -
min) x score / 6. Faces: YuNet (`face_detection_yunet_2026may.onnx`, searched in `engine/ac/assets/models`, `engine/models`, `engine/ac/models`,
then dev-only fallback folders), one frame per zoom at 640 px, faces < 6 % of the frame
height ignored (web-ad faces in screen recordings), anchor = face centre with the eyes in the upper part; cached as
`<stem>_zoomfaces.json`.

Rhythm: sentences (punctuation / Whisper segment end / gap > 0.6 s; < 1,2 s merged), every second one zoomed from its
start (snapped to a cut, or the middle of the pause before it) to the next sentence start.

### The camera curve and keys
`camera_keys(events)` builds ONE curve over the sequence: keys `[t, s, cx, cy, ease]` on the sequence frame grid.
Between keys Scale and the Position offset `(0.5 - c) x s` move with the same eased fraction (smoothstep for Halus ~
Premiere ease code 3; ease-out cubic for Cepat; ease-out-back +5 % for Cepat in talk modes; 1-frame steps for Lompat
and page-change exits) = a zoom about a fixed point, the viewport always inside the frame. An eased exit followed by
another zoom within 1,5 s becomes a direct glide (0,5-1,2 s) from viewport to viewport (never across a hard cut).

`clip_keys` samples the curve per clip: clip start, clip end - 1 frame, every breakpoint, dense steps on ramps
(<= 0,1 s, at least 6 per ramp), **1-frame guard keys** on both sides of every move (so the keys next to a hold carry the
hold value and Premiere's spatial Bezier on Position cannot drift during holds or overshoot at instant exits), redundant
keys inside constant runs dropped. A zoom spanning a cut is sampled at both clip boundaries (continuous). Clips that
stay at 1x get no keys.

### apply
`job.review` = the edited review; `job.seq` must be the analyzed sequence (else `SEQ_CHANGED`). `params.reset` =
`[{track, start, end, in, scale, pos}]` clips carrying Klipora zoom keys from an earlier run (the panel's record),
`params.interp` = `linear` (default, dense keys) or `ease` (keys only at breakpoints + guards, Premiere code 3:
**experimental**, see verifier step 9), `params.step`. Writes `<workdir>\zoom_keys.json` (ASCII JSON, ExtendScript
evals it):
```
{"v":1,"tool":"zoom","interp":"linear","track":0,"seq":{"id","name","width","height","fps"},"events":5,
 "clips":[{"track":0,"start":0.0,"end":48.925,"in":0.0,"speed":1,"name":"..","w":2292,"h":960,
           "keys":[[mediaSec, s, cx, cy], ...], "reset":{"scale":100,"pos":[0.5,0.5]}?}], "stats":{"clips","keys","reset"}}
```
Result: `{plan: {kind: "keyframes", path, seq, events, clips, keys, reset, interp, ops}, summary}`. Errors: `NO_ZOOM`
(nothing checked), `SEQ_CHANGED`, `BAD_REVIEW`, `NO_VIDEO`, `NO_MEDIA`. A changed clip layout since analysis only warns.

### preview
`params {t0, t1 (sequence s, max 60 s), ids (optional review ids, on or off)}` -> `{path, t0, t1, frames, encoder,
size, secs}`: decodes the target-track pieces at 30 fps, applies the curve per frame (`warpAffine`), encodes
`h264_nvenc` (falls back to `libx264`), concatenates the pieces' audio. Output `<workdir>\zoom_preview\` (last 2 kept,
`ensure_free(150 MB)`). Runs in the worker (`AC.engine.worker`).

### Measured (this PC, 2026-10-05)
| What | Result |
|---|---|
| 49 s tutorial, screen, first run | activity 1,7 s, thumbnails 0,3 s; 5 zooms (= prototype: jump list 1,48x, address bar 2,2x, header buttons 1,34x all exit on the page change at 15,0 / 18,5 / 25,1 s; product card 2,2x; form field 2,2x), 93 keyframes |
| 49 s, cached | analyze 0,6 s (cli round trip ~1,5 s) |
| 34,6 min raw | activity 78,8 s (0,038x realtime, cache 584 KB), plan 0,26 s, 142 thumbnails 8,3 s; 129 zooms on (3,7/min), 26 page-change exits, median zoom 1,82x, 2694 keys, apply 0,01 s |
| 34,6 min as AutoCut timeline (727 clips) | analyze 5,7 s from cache, 82 zooms, 295 clips keyed, 2246 keys, apply 0,22 s |
| Preview render 12 s | 1,45 s (NVENC, 1146x480) |

## Host (`panel/host/37_zoom.jsx`, ES3)

| Function | Returns | Notes |
|---|---|---|
| `bac_zoom_clone(seqId)` | `{ok, id, name, origId, origName}` | `tlCloneSequence`, name `<base> (Zoom)` / `(Zoom 2)`; a zoom result zoomed again keeps the short base name; `acMarkMade` (so "Hapus hasil" may delete it); original re-activated |
| `bac_zoom_keys(seqId, planPath, from, to)` | `{ok, done, keys, reset, skipped: [{start, why}], base: [{track,start,end,in,scale,pos}], ms}` | plan clips `[from, to)`; clip found by start frame (+-1), reset entries also by in-point; Motion `[0]` Position, `[1]` Scale, `[3]` Uniform Scale. `reset` -> `setTimeVarying(false)` + `setValue(base)`. Clips with other Motion keys are skipped (`why: "keys"`), Uniform Scale off -> `uniform`. Scale = base x s, Position = basePos + (0.5 - c) x s x (media size x base / 100) / frame size. `updateUI=false` except the last key of the chunk; interpolation 0 (linear) or 3 (ease) set on every key; keys not in the plan (a key `setTimeVarying(true)` may drop at the playhead) are removed |
| `bac_zoom_scan(seqId, track)` | `{clips: [{name, start, end, in, scaleTV, posTV, nScale, nPos, scale, pos}]}` | verification / "does it carry keys" |
| `bac_zoom_values(seqId, track, times)` | `{values: [{t, media, scale, pos}]}` | Scale/Position at sequence times (media-time conversion) |

The panel calls `bac_zoom_clone`, then `bac_zoom_keys` in chunks of <= 60 clips / 1500 keys (timeout 120 s each),
then `bac_openSequence`. Cancel stops between chunks (the clone stays partly keyed; the message says so).

**Re-run record**: after a successful apply the panel writes `%APPDATA%\Klipora\zoom_applied.json` (migrated from the old `%APPDATA%\AutoCutBOT`)
`{<clone id>: {name, from, created, clips: base list}}` (last 40). When the user later zooms that result sequence again,
its entry becomes `params.reset`, so the new clone first drops our old keys and restores the base values.
"Hapus hasil" removes the entry.

## Tests

```
python engine/tests/test_zoom.py                                    # ~4 s, offline: camera math, 49 s screen (=prototype), apply/plan, reset, errors, 15-clip timeline, speech, rhythm, YuNet on a copy of the OpenCV cartoon, 3 s NVENC preview
node tools/ui_test.mjs tools/ui_tests/zoom.mjs --engine live        # 50 checks: host sim (real 37_zoom.jsx in a vm, 16 checks), page flow with the real engine + worker preview
node tools/ui_test.mjs tools/ui_tests/zoom.mjs --engine mock        # 46 checks without python
node tools/ui_test.mjs tools/ui_tests/zoom.mjs --engine live --width 280
```
Screenshots (gitignored `--shots` folder): `build_zoom.png` (main), `build_zoom_review.png`, `build_zoom_result.png` (+ `_280` variants).

## Known limits / UNVERIFIED (Premiere)

- Position spatial interpolation (Auto Bezier?) between dense linear keys: mitigated by guard keys, verify step 8.
- Whether `setTimeVarying(true)` adds a key at the playhead (we remove any key not in the plan).
- Key times for clips with speed != 1 (media = in + (t - start) x speed, lab-verified for speed 1 only).
- `interp: "ease"` (code 3) on Position: keys only at breakpoints; not the default.
- CEP H.264 playback of the preview mp4 (fallback: "Buka di pemutar").
- "Scale to frame size" clips: the 100 % = native-size assumption breaks the Position offset (Scale stays right).
- A clip with a static Angle Otomatis punch-in (Scale/Position set, no keys) is zoomed on top of it: the zoom target
  lands where the angle's centre is, not the frame centre.

## Premiere verifier: step by step

Work in bin `Klipora Test`, never save, delete only what you made, restore the user's active sequence.

1. **Setup**: `node tools/reload.mjs` (no EXC/console lines). Make a test sequence from `talk_49s.mp4`
   (49 s) in bin `Klipora Test` (as in the seam check). Note its id (`AC.host.json('bac_activeSeqId')`).
2. **Analyze**: open Auto Zoom (Kamera > Zoom). Expect "Analisis layar tersimpan" on the source card. Mode Ikuti aksi
   layar, Sedang, 6 per mnt, Halus, Taskbar + Notifikasi. Click **Cari momen zoom**. Expect 5 rows in ~2 s with
   thumbnails; first rows at 0:09,07 (1,48x), 0:16,16 (2,2x), 0:21,20 (1,34x), 0:25,54 (2,2x), 0:38,80 (2,2x).
3. **Seek**: click row 2 -> Premiere playhead at 16,16 s (toast "Playhead Premiere ke 0:16,16").
4. **Preview**: click **Video pratinjau** in the preview box. Expect "Merender pratinjau... %" then a playing video
   in the panel; if CEP cannot play H.264 the message says "Klik Buka di pemutar" and that button opens the mp4.
   Screenshot (gitignored `--shots` folder): `zoom_live_review.png`.
5. **Apply**: keep all 5 checked, click **Terapkan 5 zoom**. Expect stages Gandakan sequence / Pasang 93 keyframe di
   1 clip / Buka hasil, a new active sequence `<name> (Zoom)` and the result card "Zoom terpasang" (5 zoom, 1 clip,
   93 keyframe). Screenshot (gitignored `--shots` folder): `zoom_live_result.png`.
6. **Original untouched**: `AC.host.json('bac_zoom_scan', '<original id>', 0)` -> clip `scaleTV:false, nScale:0`,
   scale 100, pos [0.5,0.5].
7. **Keys on the clone**: `AC.host.json('bac_zoom_scan', '<clone id>', 0)` -> `nScale` = `nPos` = the key count of
   `clips[0].keys` in `<workdir>\zoom_keys.json` (93). If there is one more, `setTimeVarying` added a stray key that
   was not removed: report it.
8. **Values and drift** (`bac_zoom_values('<clone id>', 0, [...])`):
   - `[8.0, 12.0, 14.98, 15.0, 17.5, 23.0, 27.5, 40.5, 45.0]` -> Scale ~ `100, 147.8, 147.8, 100, 220, 134.4, 220, 220, 100`
     (15,0 s = the instant exit on the page change: 14,983 s still zoomed, 15,0 s back to 100). Position (normalised)
     at 12,0 s ~ `[0.4484, 0.2610]`, at 17,5 s ~ `[1.0999, 1.0999]`, at 23,0 s ~ `[0.3281, 0.6719]`.
   - Hold drift: `[10.0, 11.0, 12.0, 13.0, 14.0, 14.9]` -> Position identical within 0.0005 (spatial Bezier would show
     a slow drift here; report the max difference).
   - Mid-ramp: `[9.3667]` -> Scale ~ 123,9, Position ~ `[0.4742, 0.3805]` (engine curve; +-1 % is fine).
9. **Visual**: `AC.host.json('bac_exportFrame', 12.0, '<scratch>/zoom_live_12s')` on the clone ->
   the Chrome jump list fills the frame (compare with the row-1 thumbnail rectangle). Same at 17.5 s: the address bar
   dropdown at 2,2x, text readable.
10. **Re-run on the result**: with the clone active, run Auto Zoom again with Animasi **Cepat** -> Terapkan. Expect
    `<name> (Zoom 2)`, result legend "zoom lama di 1 clip diganti", and `bac_zoom_scan` on the new clone shows only the
    new key count (no doubled keys; values at 12,0 s still 147,8 after the faster ramp).
11. **Cut sequence**: run Potong Silence (or use a 15-clip "(Klipora)" sequence of the same media), then Auto Zoom.
    Expect keys on several clips; at a cut inside a zoom, `bac_zoom_values` at (cut - 1 frame) and (cut) differ by less
    than 3 % in Scale (continuous across the cut).
12. **User keys are safe**: on a test sequence add one manual Scale keyframe to the clip, run Auto Zoom, Terapkan ->
    warning "1 clip dilewati ... keyframe Motion sendiri", that clip unchanged on the clone.
13. **Speech / Ritmis smoke**: mode Penekanan bicara -> 3 of 6 moments checked, zoom 1,2x centre (no faces in these
    recordings); Ritmis -> ~6 moments, instant 1,15x jumps at sentence starts. Apply one of them and check values.
14. **Result actions**: "Buka yang asli" re-activates the original; "Hapus hasil" (click twice) deletes the clone.
15. **Cleanup**: delete every `(Zoom)` sequence you made (`bac_deleteSequence`), the test sequence and bin; restore the
    user's active sequence and playhead. Never save.
