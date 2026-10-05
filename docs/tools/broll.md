# B-Roll (tool id `broll`)

Owner: broll tool agent. Files: `engine/ac/tools/broll.py`, `engine/tests/test_broll.py`, `panel/js/tools/broll.js`,
`panel/css/tools/broll.css`, `panel/host/41_broll.jsx`, `tools/ui_tests/broll.mjs`.
Background: internal research notes and prototypes (not published): b-roll planning and activity prototypes, ported.

## What it does

Finds the moments in the speech that benefit from supporting footage, fills each with a clip from the user's own
folder, a Pexels stock video (only with a key in Settings) or an optional AI still (from the local proxy's image endpoint, if it offers one) animated with Ken Burns, and
places them on a CLONE of the sequence ("<seq> (Klipora)") on a new top video track **"Klipora B-Roll"**. The
original sequence is never changed.

Names (Klipora rename): the track and bin are "Klipora B-Roll" in every UI language (Premiere identifiers), new
clones are "<seq> (Klipora)", markers use `[Klipora-BROLL]`. Projects made before the rename keep working: a clone
of an earlier result reuses and clears an old "AutoCut B-Roll" track (`gxFindVideoTrack` / `tlFindBin` accept the
old names), the engine's screen-activity pass ignores both track names, old `[AC-BROLL]` markers are still cleared,
and the local folder walk (panel count + engine `scan_library`) skips our own work folders "Klipora" and the old
"AutoCut BOT". Engine HTTP requests send `User-Agent: Klipora/2.0`.

UI language: every label, toast, stage and engine message exists in Indonesian and English (`broll.*` /
`host.broll*` keys in `panel/locales`, `broll.*` in `engine/ac/locales`); the engine follows the job's `lang`.
Keywords, transcript lines and file names are user content and stay as they are. The AI planning prompt is not
translated.

What beats autocut.com: placement is **activity aware**. For every candidate moment the engine measures how much
changes on screen (frame diff, port of the verified `activity.py`): a quiet screen gets a fullscreen b-roll, a
screen with some action gets a **PiP card in the corner with the least action** (so the click/typing stays
visible), and a busy screen is skipped (shown unchecked in Tinjau with the reason). Tutorials are never covered
blindly.

Pipeline (`analyze`, all times in sequence seconds):

1. Words on the timeline (`Timeline.words_on_timeline`, transcript cache, GPU only when no cache) inside the scope
   (Seluruh sequence / In/Out / Clip terpilih) -> utterance units (`ac.ai.text.sentences`).
2. Target count `n = round(scope minutes x density)` (at least 1), spacing `clamp(30 / density, 3, 30)` s.
3. Plan: **one** AI call (`grok-fast`, or Settings aiModel) over all lines as `mm:ss text`, asking for
   `k = min(2n+2, n+10, 50)` picks `{t, w, kw, q, alt, img, lib, score}`; with "AI ikut mencocokkan file lokal" the
   local file names (max 200) go into the SAME call and the model may name the best file (`lib`). No retry, no
   repair round: exactly one request per run; the answer is cached by prompt + transcript
   (`%LOCALAPPDATA%\Klipora\ai_cache`), so a second run costs no quota. Picks are resolved to lines by time +
   quoted words (`prompts.resolve`, never by index), moved to a neighbour line when the keyword is there, brand
   words are stripped from the English queries, malformed picks are dropped one by one.
   Rules fallback (AI off, offline, quota, unreadable answer): content words up, business/topic words up, on-screen
   instruction words ("klik", "isi", "pilih", "refresh" ...) down, a local-folder match +25.
4. Window per pick: starts at the keyword word (-80 ms) or the line start, lasts to the line end +0,4 s,
   min(2,5 s, max) .. max duration, inside the scope, frame aligned.
5. Screen activity of the top `max(3n+3, 8)` candidates (3 threads, ~0,4 s per 4 s window, CPU decode 8 fps at
   480 px; the topmost video clip per piece, our own b-roll track ignored): `busy` = share of samples that change.
   `busy < 0,15` fullscreen, `< 0,5` PiP, else skip. Corner = the one of tr/tl/br/bl with the least activity
   (12x6 grid), ties prefer top right.
6. Greedy selection by score (x (1 - 0,5 busy) in Otomatis) with spacing and no overlap; up to 3 skipped
   (busy) moments are kept unchecked so the user sees why.
7. Sources per slot, best slots first (so the scarce AI budget and unused local files go where they matter):
   local matches (token match incl. a tiny Indonesian stemmer, IDF weighted, unused files first, AI `lib` pick
   first), Pexels search (3 results, 24 h cache, thumbnails), AI candidate (prompt + style). Default pick: local,
   else Pexels, else AI while `ai_max` lasts; otherwise the row is unchecked with a note.
8. Review file `<workdir>\broll_review.json`; re-runs keep the user's checked/unchecked rows AND the picked source
   (`carry_over` + picked candidate by path/url/prompt).

`apply` downloads the picked Pexels file (cache `%LOCALAPPDATA%\Klipora\broll_cache\pexels`, pruned to 600 MB),
generates the AI still (cache `broll_cache\ai`, keyed by prompt + aspect, a re-run never spends quota again; at
most `ai_max` new images per run) and pre-renders every checked row with FFmpeg to `<workdir>\broll\render\
<id>_<hash>.mp4`: exactly `ceil(dur x fps) + 2` frames at the sequence frame size (cover crop), 30 fps for a
120 fps sequence (`render_fps`: halved until <= 30), h264 NVENC (libx264 fallback), **audio stripped** (`-an`).
Stills get Ken Burns (upscaled 2x before zoompan, so no jitter; zoom in / zoom out / zoom + pan alternate).
PiP rows get a thin white border baked in. Renders are never overwritten (same inputs = same file name, reused);
Premiere keeps imported files open. `Kredit_Broll.txt` (Pexels authors + URLs, AI prompts) is written next to
the renders for the video description.

## Settings (panel -> engine params)

| UI | Param | Default | Notes |
|---|---|---|---|
| Gaya Jarang / Sedang / Sering | (density, max_dur) | Jarang = 1 per mnt, 4 dtk | Sedang 2 / 3,5; Sering 4 / 3; Custom after a manual change |
| Folder lokal + path + Pilih | `src_local`, `folder` | off, empty | typing/picking a folder turns it on; the info line counts video + images (3 levels) |
| Video stok Pexels | `src_pexels` | on, but disabled until a key is saved in Pengaturan | key is write-only (`AC.settings.hasSecret('pexelsKey')`), never in the DOM, never in events/review/logs |
| Gambar AI + Maksimal gambar AI per proses | `src_ai`, `ai_max` | on, 3 (1-8) | quota warning in the UI; images cached |
| Penempatan Otomatis / Layar penuh / PiP | `placement` | Otomatis | help text explains each |
| Kepadatan | `density` | 1 per mnt | 0,5-6 |
| Durasi maksimal | `max_dur` | 4 dtk | 1,5-8 |
| Lanjutan: Transisi Potong / Crossfade | `transition` | Crossfade | 0,25 s opacity keys |
| Lanjutan: Pilih momen pakai AI | `use_ai` | on | off = rules only (no AI request) |
| Lanjutan: AI ikut mencocokkan file lokal | `ai_rank` | on | file names in the same planning call |
| Lanjutan: Gaya gambar AI | `style` | foto | foto / ilustrasi / sketsa (prompt suffix, always "no text, no logos") |
| Cakupan (source card) | `scope` | Seluruh sequence | In/Out, Clip terpilih |

## Engine actions (`engine/ac/tools/broll.py`)

| Action | Input | Result |
|---|---|---|
| `analyze` | `seq`, params above | `{review, summary: {n, on, sec_on}, stats: {n, on, full, pip, skip, local, pexels, ai, target, candidates, library, planner}, planner: ai/cache/rules/fallback, warnings}`. Stages `words, plan, screen, source`. Errors `NO_WORDS`, `NO_FOLDER`, `NO_SLOTS` |
| `fetch` | `review`, `params.ids`, optional `params.query` | Builds previews (AI image for the picked AI candidate) or searches local + Pexels again with a new keyword (row gets new `cands`, `pick` 0, `kw`). Rewrites the review. `{review, done, warnings}` |
| `apply` | `review` (edited) | `{plan: {kind: "broll", track, bin, name: "<seq> (Klipora)", width, height, fps, render_fps, transition, duration, items: [{id, path, t0, t1, place: full/pip, scale: 38, pos: [x, y], fade, label, src}]}, failed, credits, credits_text, folder, summary}`. Stages `source, render`. Error `NO_ITEMS`, `BROLL_NONE` |
| `library` | `params.folder` | `{n, videos, images, sample}` (dev helper) |

Review row extras: `text, kw, q, alt, img, busy, place (full/pip, user may flip), auto (full/pip/skip),
corner, pos, scale, cands: [{src: local|pexels|ai, name, thumb, path | url+id+user+page | prompt+aspect}],
pick, src, plan (ai/rules)`. PiP geometry: Motion Position (normalised) of a full-frame clip scaled 38 %,
margin 2,5 % of the width (same pixels vertically): top right on 2292x960 = `[0.785, 0.2497]`.

## How it applies (panel + host)

Tinjau list (shared component): thumbnail + keyword + spoken line + source per row, tags Penuh/PiP, source,
`k/n` candidates; filters Lokal / Pexels / AI / Layar sibuk; row actions **Ganti sumber** (cycle candidates),
**Penuh/PiP**, **Kata lain** (inline search box -> engine `fetch` with the keyword), **Buat gambar AI** (engine
`fetch`, ~15-20 s, 1 image of quota); "Kirim ke marker" uses tag `[Klipora-BROLL]`. Dock "Pasang N B-roll" saves the
edited review -> engine `apply` (progress pane) -> host task (`ctx.track`): `bac_broll_prepare(name, seqId,
track)` (clone of the ANALYZED sequence by id, open it, `gxEnsureVideoTrack` "Klipora B-Roll", clear it) ->
`bac_broll_place(cloneId, items, {track, bin})` in chunks of 4 (import into root bin "Klipora B-Roll" with the
File.exists + extension guard, `overwriteClip(item, ticks)` at the frame of t0, `clip.end = t1`, PiP: Motion
Scale 38 + Position, crossfade: Opacity keys 0 -> 100 -> 100 -> 0 at clip media time, clip renamed
"B-roll <kata>") -> `bac_openSequence(clone)` -> result card (B-roll / Layar penuh / PiP counts, ribbon, Buka yang
asli, Ubah pilihan, Hapus hasil (bac_deleteSequence), Buka folder render, Salin kredit). Cancel stops between
chunks. `bac_broll_pickFolder` is the folder picker fallback when `cep.fs.showOpenDialogEx` is missing.

## Measurements (this PC, 2026-10-05)

| What | Result |
|---|---|
| analyze 49 s, rules, local folder (4 files) | 1,4-1,8 s (activity 9 windows) |
| analyze 49 s, AI (1 AI request) / same again | 6,0 s / 1,0 s ("dari cache AI") |
| analyze 34,6 mnt, rules | 16,3 s (108 activity windows, 35 slots) |
| AI planning call, 2 min transcript, k = 6 | 5,0 s, 6 picks, concrete queries ("person filling online form") |
| AI image 16:9 still via the local proxy's image endpoint (live) | 14,9 s, 1280x720 JPEG 260 KB |
| Ken Burns render 3,5-4 s at 2292x960 (NVENC) | 0,8-1,0 s |
| Video pre-render 4 s (3 s source looped) at 2292x960 | ~0,7 s |
| Activity, 4 s window on the 2292x960 120 fps recording | ~0,4 s (busy 0 .. 0,58 across the 49 s clip) |

## Tests

- `python engine/tests/test_broll.py` (offline, ~11 s, part of `run_all.py`): helpers, library scan/match,
  activity on synthetic still/moving clips and on the 49 s recording, renders (2292x960, no audio, exact length,
  PiP border pixels), AI plan with a mocked `chat_json` (one call with `retries=0, repair=0`, cache hit on the
  second run, bare-array answers, brand stripping), Pexels with a mocked API/download (file choice, locale id-ID
  for Indonesian keywords, **the key never appears in the review or events**), mocked image generation (cache, budget),
  analyze -> review, carry-over of touched rows, fetch (AI preview, new keyword), apply -> plan, CLI round trip.
  `--live` adds one real planning call.
- `node tools/ui_test.mjs tools/ui_tests/broll.mjs --engine live` (headless Chrome + stub + REAL engine, no AI
  quota, no network): 33/33. Screens (gitignored `--shots` folder): `build_broll_main.png`, `build_broll.png` (Tinjau),
  `build_broll_result.png`.
- Live once during the build (not Premiere): 1 planning call on the 2 min transcript, 2 analyze calls in AI mode on
  the 49 s clip (one fell back to rules on an unreadable answer, then fixed parsing; the next call worked and was
  cached), 1 AI image (deleted afterwards).

## What the Premiere verifier must test (step by step)

Work in bin `Klipora Test` with a sequence made from `talk_49s.mp4` (49 s, 2292x960 120 fps);
delete what you create (clone sequences, bin "Klipora B-Roll"), never save. Reload the panel
(`node tools/reload.mjs`): no `EXC`, `AC.host.loadErrors` empty (`41_broll.jsx` must load).

0. Sample library (no personal media): in Git Bash
   `mkdir -p "$TEMP/broll_lib/Bisnis" && ffmpeg -y -f lavfi -i testsrc2=s=640x360:r=30:d=4 -c:v libx264 -pix_fmt yuv420p "$TEMP/broll_lib/Bisnis/Reseller packing paket.mp4" && ffmpeg -y -f lavfi -i testsrc=s=1280x720:r=25:d=6 -c:v libx264 -pix_fmt yuv420p "$TEMP/broll_lib/uang tunai.mp4" && ffmpeg -y -f lavfi -i color=c=0x3060d0:s=800x600:d=1 -frames:v 1 "$TEMP/broll_lib/email notifikasi.png"`.
1. Open B-Roll on the test sequence. Strip: Perkiraan 1 B-roll, Bagian 0:49. "Video stok Pexels" is disabled with
   "Buka Pengaturan" (unless a key is saved). Click **Pilih** next to the folder field: the CEP folder dialog opens
   (`cep.fs.showOpenDialogEx`); choose `%TEMP%\broll_lib`: Folder lokal turns on, info "2 video, 1 gambar".
   UNVERIFIED live: the CEP dialog (fallback = ExtendScript `Folder.selectDlg` via `bac_broll_pickFolder`).
2. Lanjutan: switch "Pilih momen pakai AI" OFF (no quota). Sedang preset (2 per mnt). Press **Cari B-roll**.
   Expect stages Transkripsi (dari cache) / Rencana B-roll / Cek aktivitas layar (2 momen) / Cari sumber, then 2
   rows: 0:04,83 "reseller" (Penuh, Lokal: Bisnis\Reseller packing paket.mp4) and 0:40,72 "email" (Penuh, Lokal:
   email notifikasi.png), thumbnails visible. Click a row: playhead jumps there.
3. On row 1: **Penuh/PiP** -> tag PiP; **Ganti sumber** -> Gambar AI ("dibuat saat dipasang"), click again ->
   back to Lokal. Uncheck nothing. Press **Pasang 2 B-roll**: engine stages Siapkan sumber / Render B-roll, then
   host stages Gandakan sequence / Pasang 2 B-roll / Buka sequence baru.
4. Expect a new active sequence "<name> (Klipora)" with the SAME duration (0:49) and a new TOP video track named
   **Klipora B-Roll** holding 2 clips named "B-roll reseller" / "B-roll email" at exactly 4,833-7,767 s and
   40,717-44,217 s (Sedang = max 3,5 dtk; frame boundaries at 120 fps); NO new audio clips (renders have no audio); bin "Klipora B-Roll"
   with 2 mp4 items from `...\Videos\Klipora\<seq>\broll\render\`. Original sequence unchanged (1 clip, no
   new track). Export frames (`bac_exportFrame`) at 6,0 s and 42,0 s:
   - 6,0 s (PiP): the test pattern card scaled 38 % with a white border in the top-right corner (centre
     ~[0.785, 0.25] of the frame), the screen recording visible around it. Check Effect Controls: Motion Scale 38,
     Position 0.785/0.2497 (normalised) = about 1799/240 px.
   - 42,0 s (fullscreen): the blue Ken Burns still covers the whole frame.
   - Crossfade: Opacity keys at clip start (0 %), +0,25 s (100 %), end-0,25 s (100 %), last frame (0 %); a frame at
     4,9 s is a mix of b-roll and screen. UNVERIFIED live: opacity keys on an mp4 clip (gxPopIn used them on
     MOGRTs), `clip.end = Number` on a 30 fps clip in a 120 fps sequence, `TrackItem.name` set on video clips.
5. Play across both b-rolls: no audio change (voice continues), no black frames at the edges.
6. Result card: B-roll 2, Layar penuh 1, PiP 1; "Buka yang asli" re-activates the original; "Ubah pilihan" returns
   to Tinjau; "Hapus hasil" (two clicks) deletes the clone; "Buka folder render" opens Explorer.
7. Run B-Roll again ON THE CLONE: activity ignores the "Klipora B-Roll" track; Pasang makes "(Klipora 2)" whose
   b-roll track is cleared and re-filled (still ONE b-roll track, no duplicates); the first clone is untouched.
8. AI mode (spends 1 AI request unless cached): switch "Pilih momen pakai AI" on, Jarang, run. Expect planner
   "ai" (no warning), 1-2 rows whose keywords are spoken words; a row over screen action shows the note "Ada aksi
   di layar (..%), dipasang kecil di pojok" and gets PiP in the emptiest corner. Run again: Rencana B-roll stage
   says "dari cache AI" (no request). With the proxy stopped: warning "AI tidak dipakai (AI offline ...)", rules.
9. Gambar AI (spends 1 AI image request, ~15-20 s; needs a local proxy with an image endpoint): on a row press **Buat gambar AI**: progress, then the thumbnail
   shows the generated picture. Pasang: the still becomes a Ken Burns clip (slow zoom). "Salin kredit" copies the
   AI prompt line. Re-running never regenerates the same image.
10. Pexels (only if the user has a key; never type a key yourself): with a key saved in Pengaturan the switch is
    enabled; a run adds Pexels candidates with thumbnails, apply downloads the chosen mp4 to
    `%LOCALAPPDATA%\Klipora\broll_cache\pexels`, `Kredit_Broll.txt` lists the author + URL. Check
    `AC.log.text()`, the review JSON and Riwayat: the key never appears.
11. Busy screen: placement Otomatis on the 34,6 mnt recording (rules, AI images max 3): analyze ~16 s, 35 moments,
    rows over busy screens unchecked with "Layar sedang sibuk (..%)"; Layar penuh forces fullscreen everywhere.
12. After every step: `AC.log.errors` empty, no orphan python.exe/ffmpeg.exe; delete the clones, the bin
    "Klipora B-Roll" and `%TEMP%\broll_lib`; the render folder under `Videos\Klipora\<seq>\broll` can be
    deleted once no clone uses it.

## Known gaps / UNVERIFIED

- Everything Premiere-side is UNVERIFIED live (build agents may not touch Premiere): placement on the new track,
  PiP Motion values, opacity crossfade keys, clip naming, the CEP folder dialog.
- Pexels with a real key is UNVERIFIED (no key on this PC); the code follows the documented API
  (`/videos/search`, `Authorization` header, 200 req/h) and is covered by mocked tests. Pixabay is not implemented.
- Activity thresholds were tuned on screen recordings. Talking-head footage moves all the time, so Otomatis may pick
  PiP/skip there: use "Layar penuh".
- Renders stay in the workdir as long as a clone uses them (Premiere keeps them open); they are small (1-6 MB per
  b-roll) but are not pruned automatically.
