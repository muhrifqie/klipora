# Auto Captions (`captions`): engine half

Animated word-by-word captions for the active sequence, beating autocut.com AutoCaptions on: local transcription,
works on already-cut Klipora sequences, Indonesian text rules, real-footage preview in ~0.1 s, per-word editing
that survives re-runs, auto-position that avoids UI/faces/the click spot, and an overlay that re-renders only the
changed part after an edit. Full API for the UI agent: `docs/CAPTIONS_API.md`. Code: `engine/ac/captions/`.
Test: `python engine/tests/test_captions.py` (offline, ~6 s). Build screenshot (gitignored `--shots` folder): `build_captions.png`
(engine previews over the real frame: 10 looks incl. per-word overrides, word pills, portrait crops, plus the
19-template gallery).

## What it does

1. **doc**: maps the transcript (cache v3, one GPU transcription per media) onto the timeline, applies
   deterministic Indonesian rules (glossary sound-alikes like "toko kitah" -> "TokoKita", "50 ribu" -> "50.000",
   "Rp15.000" -> "Rp 15.000", filler words hidden on screen only, profanity masked with the Sensor Kata Kasar
   tool's decisions via `hits_for_timeline`), paginates per template (pixel-exact widths, no line ending on "di/ke/yang/untuk...",
   comma-aware balanced lines) and writes `<workdir>\captions.json`. Every user edit is keyed by a stable word id.
2. **templates / gallery / save_template**: 19 built-in looks + user templates; cached picker thumbnails.
3. **preview**: PNG of the captions at the playhead over the real frame (or a transparent PNG).
4. **autoposition**: per-page height that keeps faces, on-screen UI text and the action area visible inside the
   platform safe zone, with stability (captions only move when something important is underneath).
5. **render**: straight-alpha qtrle `.mov` overlay (exact colours, verified method), band-only when possible,
   30 fps, versioned `_v<n>` names, rendered as cached chunks in parallel.
6. **srt** (native caption track), **burn** (3 s MP4 loop for checking), **mogrt** (optional native editable
   MOGRTs for short videos), **cleanup** (AI text fixes as a review list, applied only on accept).

## Names and language

- UI: "Auto Caption" / tab "Caption" in Indonesian, "Auto Captions" / tab "Captions" in English. The panel follows
  the Klipora display language (Settings, or Premiere's UI language when set to automatic) and can switch live
  while the editor is open (the editor is rebuilt, the document and the current tab stay). Engine texts (stages,
  summaries, errors, warnings, option labels) come back in the job language (`job.lang`), see
  `docs/CAPTIONS_API.md` section 2. Caption text itself is never translated.
- Premiere names (same in both languages): overlay track **"Klipora Captions"**, MOGRT track
  **"Klipora Captions (Editable)"**, bin **"Klipora Captions"**. Projects captioned before the rename keep their
  "AutoCut Captions" tracks/bin: apply, relink, status and "Hapus dari timeline" find and reuse them; they are not
  renamed. The document kind `autocut.captions` stays (internal format id).
- English test: `node tools/ui_test.mjs tools/ui_tests/captions_en.mjs` (boots with Premiere locale en_US, walks
  setup, every sub tab, the live en -> id -> en switch and the overlay apply).

## Settings and defaults

| Setting | Default | Notes |
|---|---|---|
| Template | Tutorial Bersih (16:9 / 21:9), Hormozi Kuning (9:16) | settings `captionTemplate` overrides |
| Glossary | settings `glossary` + per-document list | deterministic, ~10 ms, before any AI |
| Sembunyikan filler | on | eh/em/hmm hidden in captions only |
| Sensor kata kasar | `auto` = ikut switch "Sensor juga caption" di tool Sensor Kata Kasar (words from its `hits_for_timeline`) | off / stars / first / full / bleep |
| Format angka & Rupiah | on | |
| Overlay | qtrle, 30 fps, band auto | ProRes 4444 / PNG-MOV optional |
| Auto-posisi | off until clicked | platform from the template (`auto` = YouTube / TikTok) |
| MOGRT | off (`enable`) | max 300 clips, fonts must be installed |
| AI cleanup | only on click | grok-fast, cached; falls back to rules when no AI provider answers |

## How it lands in Premiere

- Overlay: one clip from 0 on the top video track **"Klipora Captions"** (created once, replaced on re-run; an old
  "AutoCut Captions" track is reused),
  positioned at the band centre (`xNorm/yNorm`), alpha straight. Re-render -> new file `_v<n+1>` -> relinked in
  place when the band is unchanged (`plan.relink`), else re-placed. Never touches other tracks, never saves.
- SRT -> native caption track (`gxCaptionTrackFromSrt`).
- MOGRT -> track "Klipora Captions (Editable)", one graphic per page (or per word state), pop-in via `gxPopIn`.
- Optional toggle "Samakan warna dengan preview" = `compositeLinearColor=false` on that sequence (never silently).

## What the Premiere verifier must test (step by step)

Work in bin `Klipora Test` on a sequence made from `%KLIPORA_TEST_MEDIA%\talk_49s.mp4`
(`createNewSequenceFromClips`), restore the user's active sequence at the end, delete only what you created,
never save. Engine jobs can run without the caption UI (built later): write the job JSON yourself.

1. **Doc on the real sequence.** `node tools/ev.mjs "bac_seqInfo('full')"` > seq.json. Job
   `{"tool":"captions","action":"doc","seq":<seq.json>,"params":{"glossary":["TokoKita"]},"workdir":"<scratch>"}`
   (use the brand terms actually spoken in your clip, e.g. the `glossary` list of `media.json`)
   -> `python -X utf8 engine/cli.py run job.json`. Expect `result` with ~17-18 pages (reference clip), `stats.fixes >= 1`, words
   ids `<hash>:<n>`, and the page that contains the brand term spells it exactly as in the glossary. Repeat on a clone cut by Potong Silence:
   words follow the cuts (fewer words, times inside the new duration).
2. **Render + place.** Job `render` (same workdir) -> `plan` with `band` and `xNorm/yNorm`. Place it:
   `node tools/ev.mjs "tlJSON(gxPlaceOverlay(app.project.activeSequence, '<plan.path with />', {startSec:0, xNorm:<x>, yNorm:<y>, scale:100, bin: tlFindBin('Klipora Test', true)}))"`.
   Expect `{ok:true, track:<n>, start:0, end:~43-46}` and a track named "Klipora Captions". No dialog.
3. **Pixel check.** On the TEST sequence only: `gxSetLinearCompositing(seq,false)`. For t = 4,4 s and 17,6 s:
   `bac_exportFrame(t, path)` and the engine `preview {t, scale:1}` (job with the same seq). Compare the caption
   region (PIL): mean diff < 2, no halo, no black stripe on the right edge, caption at the same position (band
   placement correct). Repeat once with linear compositing ON and only note the difference (expected lighter box).
4. **Band placement with Premiere's scaling preference**: confirm the band clip is NOT scaled (Motion Scale 100,
   width = band width). If it is scaled, re-run render with `{"band":"off"}` and report.
5. **Edit + re-render + relink.** Job `doc` with `seq:null`, `params.ops:[{"op":"style","ids":["<a word id>"],
   "style":{"color":"#FF4040"}}]`, then `render` again: expect `version` +1, `plan.relink:true`,
   `reused_chunks >= 0`. `gxRelinkOverlay(seq,'Klipora Captions','<new path>')` -> ok, same start, end = new
   duration; the exported frame at the edited word shows red. Then `doc` op `page_pos` y=30 for page 0 + render:
   band changes -> `relink:false` -> place again (replace) and check the position.
6. **Timing on 120 fps.** Export frames at a word start (e.g. the active-word colour change of "tutorial" ~3,33 s)
   and 1 frame before/after: the highlight changes on the overlay frame floor((t)*30), held 4 sequence frames.
7. **SRT.** Job `srt` -> `gxCaptionTrackFromSrt(seq, path, bin)` returns true; the caption track shows the page
   texts at the right times (visual check in the Program monitor; exportFrame does not draw captions). Also try
   `{"tags":true}` once and note whether coloured words render (UNVERIFIED so far).
8. **MOGRT (optional).** Job `mogrt {"enable":true}` on a short range (or the 49 s sequence: ~17 clips). Insert the
   first 3 items: `gxInsertMogrt(seq, path, start, end, trackIdx)`, `gxPopIn(clip,{pivot:anchor})`, set Motion
   Position = `position`. Expect text with per-word colours, at the page height, no "Resolve Fonts" dialog (fonts
   fall back to Arial when not installed; check the warning). Check a 2-line page (`\r`), report if it shows a
   line break or a symbol.
9. **Preview speed in the panel worker** (once the UI exists): playhead stop -> preview < 300 ms, style edit ->
   preview < 200 ms (engine measured 70-90 ms cached frame, 130-165 ms new frame).
10. **Cleanup.** Delete the overlay/caption/MOGRT tracks' clips you created (`gxClearTrack`), the imported
    `_captions_v*.mov` / `.srt` / `.mogrt` project items and the test sequence/bin; reset linear compositing if you
    changed it on anything but the test sequence (you should not have); restore the user's active sequence and CTI.
    Delete the scratch workdir (renders).

## Known limits

- Full-length burn-in MP4 is not offered (export from Premiere with the overlay track).
- Bilingual captions, SFX, per-speaker styles: not built (roadmap P2/P3).
- Autoposition on long videos is decode-bound (~1 s per minute of video).
