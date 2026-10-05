# Auto Resize (tool id `resize`)

**Klipora** (formerly AutoCut BOT). UI in Bahasa Indonesia or English (Settings > Display language): English names are
"Auto Resize", methods "Smart (Klipora)" / "Focus + blur" / "Premiere Auto Reframe", camera motion Slow / Normal /
Fast / Still. Panel texts: keys `resize.*` / `host.resize*`; engine texts (stages, review rows, advice, warnings,
errors, summary) follow the job's `lang` (keys `resize.*` in engine/ac/locales). Rendered results go into bin
"Klipora" (the old bin "AutoCut BOT" is still found and reused). New sequence names keep the neutral suffixes
"(9x16)", "(resize)", "(9x16 Premiere)", "(Auto Reframe)"; the focus render is "(9x16 fokus)" / "(9x16 focus)".

Makes a 9:16 / 1:1 / 4:5 / 16:9 version of the active sequence whose framing follows the action: clicks, typing,
menus and page changes in screen recordings, or the faces / active speaker in camera footage. The original sequence
is never changed: every result is a new sequence.

| Part | File |
|---|---|
| Engine actions (`analyze`, `apply`, `render`) | `engine/ac/tools/resize.py` |
| Video analysis (frame sampler, activity map, YuNet faces, face probe) | `engine/ac/tools/_resize_vision.py` |
| Camera math (hybrid operator, speed presets, speaker Viterbi, RDP) | `engine/ac/tools/_resize_camera.py` |
| Pixels (layout geometry, compose, contact sheet, MP4 + audio render) | `engine/ac/tools/_resize_render.py` |
| Face model (YuNet 2026may, 230 KB) | `engine/ac/assets/models/face_detection_yunet_2026may.onnx` |
| Panel page | `panel/js/tools/resize.js`, `panel/css/tools/resize.css` |
| Host (ES3) | `panel/host/38_resize.jsx` |
| Tests | `engine/tests/test_resize.py`, `tools/ui_tests/resize.mjs` |

The `_resize_*.py` helpers start with an underscore so the tool registry does not list them as tools.

## Engines ("Cara membuat")

| Engine | What happens | Editable | Status |
|---|---|---|---|
| **Pintar (Klipora)** / Smart (default) | engine `analyze` -> preview -> engine `apply` -> plan `{"kind": "reframe"}` -> host clones the sequence, sets the frame size (`tlSetFrame`), then sets Motion **Scale** and **Position** per clip on the main video track (a static value, or Position keyframes in clip media time). Crop layout. | yes, per clip | engine + panel tested; host UNVERIFIED live |
| **Fokus + blur** | engine `analyze` -> preview -> engine `render`: MP4 of the scope (blurred darkened full frame + sharp window following the action, NVENC, audio mixed from the heard clips) -> host imports it into bin "Klipora" (`AC_RESIZE_BIN`; old "AutoCut BOT" reused) and makes a sequence from it. | no (one video clip) | engine + panel tested; host UNVERIFIED live |
| **Premiere Auto Reframe** | host `seq.autoReframeSequence(num, den, preset, name, false)`, then polls `isDoneAnalyzingForVideoEffects()` every 1 s. No engine job. | yes | API verified in the lab (premiere_timeline_api.md 6); flow UNVERIFIED live |

Why focus + blur exists: a full-height 9:16 crop of the 2292x960 tutorials is only 540 px = **23,6 %** of the width,
so forms and paragraphs get cut. The focus window for 9:16 is 1:1 (960x960 of the source = 42 %), shown at
1080x1080 with a 20 % title zone above and room for captions below. After a Pintar analysis of screen content the
preview pane says so and offers "Pakai Fokus + blur" (one click re-runs the analysis with the focus layout).

## Settings and defaults

| Setting (UI) | Param | Values | Default |
|---|---|---|---|
| Format | `target` | `9x16` 1080x1920, `1x1` 1080x1080, `4x5` 1080x1350, `16x9` 1920x1080 (the format equal to the source aspect is disabled) | `9x16` |
| Cara membuat | (panel) `layout` | Pintar -> `crop`, Fokus + blur -> `focus`, Premiere Auto Reframe -> host only | Pintar |
| Gerak kamera | `speed` | `slow` Lambat, `normal`, `fast` Cepat, `none` Diam (no moves inside a shot; native: slow->`slower`, normal->`default`, fast->`faster`) | `normal` |
| Subjek (lanjutan) | `subject` | `auto` (face probe decides), `screen`, `face` | `auto` |
| Perbesar (lanjutan) | `zoom` | 100-160 % (window tighter than full height; then the camera also moves vertically) | 100 % |
| Abaikan taskbar dan notifikasi | `bands` | taskbar 5 %, toast corner 2 %, screen edges 10 %, browser bar 35 % weight | on |
| Latar (Fokus) | `bg` | `blur`, `dark` | `blur` |
| Frame rate render (Fokus) | `fps` | 30, 60 | 30 |
| Cakupan (source card) | `scope` | Seluruh sequence, In/Out (Pintar: clone gets the same In/Out; Fokus: renders only that part) | all |

Speed presets (`_resize_camera.SPEEDS`; screen content adds 25 % dead zone and 0,3 s hold):

| | dead zone (x crop width) | hold | glide speed | glide length | static if spread <= | keep framing at a cut if shift < |
|---|---|---|---|---|---|---|
| Lambat | 0,30 | 0,8 s | 0,25 src/s | 0,8-1,8 s | 0,60 crop | 0,40 crop |
| Normal | 0,20 | 0,5 s | 0,45 src/s | 0,5-1,2 s | 0,50 crop | 0,35 crop |
| Cepat | 0,10 | 0,25 s | 0,9 src/s | 0,3-0,8 s | 0,35 crop | 0,25 crop |

## How it works

1. **Main track** = the video track with the most media time (V1 for tutorials). Other video tracks (captions,
   overlays, titles) are not moved; the preview and result warn about them ("Buat ulang caption").
2. **Per media, once, cached next to it** (`util.cache_path`, same convention as `_words.json`):
   - face probe: YuNet on 24 keyframes; `ratio` = share with a face >= 8 % of the frame height. >= 0,35 -> face
     content, else screen. Stored in `<stem>_resize_faces.json` (`probe`).
   - screen: `<stem>_resize_act.npz` = per 0,1 s sample: still / local / global (page change, scroll), changed
     pixels per 48x20 cell (tiny cursor-like blobs count half), distractor cells (spinners, clock, badges) muted.
     Port of the internal activity prototype. 12 KB for 49 s, 363 KB for 34,6 min.
   - face: `<stem>_resize_faces.json` (`full`) = YuNet + landmarks at 5 fps, mouth-motion energy per face, hard cuts
     (coarse thumbnail difference refined to the frame with ffmpeg's scene score on a 0,5 s window). Port of
     the internal reframe prototype.
3. **Shots** in sequence time: clip boundaries + page changes (screen) or hard cuts + speaker switches (faces).
   The camera may cut between shots but never pans across one. Transitions are ignored: 0,8 s before a page change
   and 0,3 s after it.
4. **Per shot and axis**: target = windowed (+-0,6 s) centre of the weighted activity, or the face / group / active
   speaker (Viterbi, switch = cut). Static when the spread is small, the shot is short or speed is Diam (with
   cross-cut hysteresis); otherwise the **hybrid virtual camera operator** (dead-zone hold, eased glides that start
   early, Kalman/RTS follow only for sustained face motion), then Ramer-Douglas-Peucker. A shot with no target holds
   the previous framing.
5. **Review file** `<workdir>\resize_review.json`: one row per camera change, `kind` `move` (the camera pans inside
   the shot) or `jump` (the camera cuts to a new framing). Unchecking a row holds the camera where it was; later
   "hold" shots follow the real position. Re-runs keep manual choices (`carry_over`).
6. **Preview** `<workdir>\resize\preview_<time>_<target>_<layout>.png` (960 px wide; the 2 previous ones are kept):
   camera path strip (line = window centre over the source width, band = visible width, red ticks = camera cuts)
   + 8 tiles (6 for 1:1 / 4:5, 4 for 16:9) of the real output layout with a mini source map and the crop box.
7. **Pintar plan** (`apply`): `{"kind": "reframe", "w", "h", "name": "<seq> (9x16)", "track", "inout", "clips":
   [{"track", "index", "start", "end", "scale", "pos": [x, y]} | {..., "keys": [[seqSec, x, y]]}]}` with
   `s = max(Wf/Wm, Hf/Hm) * zoom`, `Scale = 100 s`, `Position = 0.5 + (0.5 - c) * Wm * s / Wf` (normalised to the
   frame, Premiere 26.x). Camera cuts inside a clip = two keys one frame apart (linear interpolation). Keys stay inside
   `[start, end - 1 frame]`. The host converts sequence seconds to clip media time (`tlSeqToMedia`), clears existing
   Scale/Position keys first (reported as `replaced`) and sets interpolation 0 (linear).
8. **Render** (`render`): ensure_free(estimate) first; audio = every heard audio clip of the scope mixed in 30 s
   windows (stereo twins removed, same-file neighbours decoded in one call; clip volume keys/effects are not
   applied) -> AAC; video frames decoded per run of monotonic source time (background thread), composed with
   `warpAffine` (sub-pixel, no stepping), encoded with `h264_nvenc` (`libx264` fallback), BT.709 tags, faststart.
   Output `<workdir>\resize\<seq>_<target>_<time>.mp4` (new name every run: Premiere locks imported files).
   Cancel kills ffmpeg and deletes the partial file.

## Results (this PC, 2026-10-05)

| Case | Result |
|---|---|
| 49 s tutorial, first analysis (probe + activity) | 6,4 s; cached re-run 1,2 s; preview 1,1 s |
| 49 s, 9:16 crop Normal | 6 shots, 1 move + 2 jumps; apply 1 clip, 20 keys; advice "Fokus + blur" (24 % width) |
| 34,6 min recording as its 727-clip AutoCut timeline (19:43) | analysis 46 s first time (0,02x realtime), plan 0,5 s, preview 1,5 s, apply 0,2 s (plan JSON 83 KB) |
| same, 9:16 crop Lambat / Normal / Cepat | 141 / 182 / 234 camera events (7,1 / 9,2 / 11,9 per min), keys on 31 / 41 / 59 clips (262 / 334 / 451 keys) |
| same, 9:16 Fokus Normal | 75 events (3,8 per min) |
| synthetic talking video (internal fixture, ground truth) | cuts 6,0 / 10,0 / 17,0 s exact, speaker switch at 13,3 s (truth 13,5), subject fully in the 9:16 crop 98,8 % of frames (= proto hybrid) |
| Focus render 8 s 1080x1920 30 fps | 240 frames in 4,3 s (56 fps), h264_nvenc, 1,3 MB, duration exactly 8,0 s with AAC audio |

## Tests

```
python engine/tests/test_resize.py          # ~17 s: geometry, camera math, analyze/apply 49 s + 15-clip cut, toggles,
                                            # carry-over, In/Out, 3 s render (deleted) + cancel, synthetic faces
python engine/tests/test_resize.py --long   # + 727-clip timeline of the 34,6 min recording (cached activity)
node tools/ui_test.mjs tools/ui_tests/resize.mjs --engine live   # 25 checks, real engine on the 49 s clip
node tools/ui_test.mjs tools/ui_tests/resize.mjs --engine mock   # 24 checks
```
Screens (gitignored `--shots` folder): `build_resize.png` (main), `build_resize_preview.png`, `build_resize_review.png`,
`build_resize_result.png`, `build_resize_render.png`, `build_resize_native.png`.

## Premiere verifier: step by step (live, Premiere 26.2.2)

Rules: work in bin `Klipora Test`, never save, delete only what you made, restore the user's active sequence.

1. `node tools/reload.mjs` -> no EXC/console lines; `AC.host.loadErrors` is `[]` and `AC.host.files` contains
   `38_resize.jsx`; `node tools/ev.mjs "typeof bac_resize_begin + typeof bac_resize_native"` -> `functionfunction`.
2. Make a test sequence from `talk_49s.mp4` (createNewSequenceFromClips) in `Klipora Test`, activate it.
3. **Pintar 9:16**: open Auto Resize, keep defaults, "Pratinjau kamera". Expect the preview pane with the sheet image,
   "Tulisan bisa terpotong" advice. Click "Buat sequence 9:16". Expect result "Sequence 9:16 siap".
   - `AC.host.json('bac_seqInfo','full')` on the new active sequence `<name> (9x16)`: width 1080, height 1920, same
     clip count as the source.
   - UNVERIFIED items to confirm: (a) `tlSetFrame` on the clone sticks (result has no "Ukuran frame belum" warning);
     (b) Motion Position accepts normalised values outside 0..1 for a clip larger than the frame (e.g. 1.27 / -0.33)
     and Scale 200 shows the full 960 px height; (c) keys land in clip media time.
     Read back: `clip.components` Motion `properties[1].getValue()` = 200, `properties[0].getKeys()` count = plan.
   - Visual: `bac_exportFrame` of the new sequence at 12,5 s (Chrome jump list should be inside the frame), 16,4 s
     (incognito page) and 36,4 s (Daftar akun form, left part); compare with tiles 3 / 4 / 8 of the preview PNG.
   - The source sequence is unchanged: 2292x960, clip Scale 100, no Position keys.
   - "Hapus hasil" deletes the new sequence (bac_deleteSequence).
4. **15-clip cut**: run Potong Silence (or `bac_applyRemove`) on the test sequence, then Pintar 9:16 Cepat on the
   "(Klipora)" clone. Every clip gets Scale 200; only some get keys; scrub across a camera cut inside a clip (two keys
   one frame apart) and across clip boundaries: no slide, a clean cut.
5. **In/Out**: set In 5 s / Out 20 s on the source, choose scope "In/Out", Pintar. The new sequence has the same In/Out.
6. **Existing Motion keys**: on a sequence that already has Auto Zoom keys, Pintar -> the result card warns
   "Zoom atau posisi lama di N properti diganti" and the new sequence shows only the reframe keys.
7. **Fokus + blur 9:16**: choose Fokus + blur, scope In/Out (10-16 s), "Pratinjau kamera", "Render video 9:16".
   Expect progress stages Baca/Analisis/Hitung/Siapkan audio/Render video, then "Video 9:16 siap"; a new sequence from
   the MP4 (1080x1920, 30 fps, 6 s) in bin "Klipora"; no import dialog. Play it: audio in sync, blurred top/bottom,
   sharp square window. Cleanup: delete the sequence, the imported MP4 project item and the bin "Klipora" if you
   created it, and the MP4 under the workdir `resize\` folder.
8. **Premiere Auto Reframe 9:16 Normal**: "Buat dengan Auto Reframe" -> stages "Buat sequence Auto Reframe",
   "Premiere menganalisis gerak" -> "Auto Reframe Premiere siap" with size 540x960 and preset default. The new sequence
   is in the root bin "Auto Reframed Sequences"; source unchanged. Delete it and that bin afterwards.
9. Cancel paths: Esc during the Pintar key step -> toast "Dibatalkan..." (the partial new sequence is named in the
   message); Esc during render -> no MP4 left in `resize\`.
10. Screenshots `resize.png` (preview pane) and `resize_result.png` (gitignored `--shots` folder); restore the user's active sequence.

## Known gaps / UNVERIFIED

- All host functions are UNVERIFIED live (written from lab-verified helpers: clone, setSettings, Motion keys,
  autoReframeSequence, createNewSequenceFromClips). Position outside 0..1 on a clip larger than the frame is the main
  open question.
- Pintar ignores Default Media Scaling / "Set to frame size": Scale assumes 100 % = native pixels (true for the test
  media). Clips that already carry Motion keys get them replaced (warned).
- The focus layout is only available as a render; a two-layer ExtendScript version (V1 blurred + V2 cropped) needs
  effect parameter scripting that is not verified.
- Render audio ignores clip volume keyframes and audio effects; nested sequences / graphics on the main track are not
  reframed (warned).
- Face content numbers come from the synthetic sample only (the user's media has no camera footage).
