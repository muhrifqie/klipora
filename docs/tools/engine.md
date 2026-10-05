# Klipora engine foundation (cli.py, worker.py, engine/ac/*)

Not a user-facing tool: the shared Python engine every tool runs on. Full API: `docs/ENGINE_API.md`.

## What it does

- `engine/cli.py run <job.json>` runs one tool action and streams JSON-lines progress; `health` checks Python,
  FFmpeg (+libass/nvenc), GPU + CUDA DLLs, cached Whisper models, the AI route (default: local OpenAI-compatible proxy at 127.0.0.1:8168) and free disk;
  `tools` lists registered tools.
- `engine/worker.py` is the persistent version (one job at a time, Whisper kept warm, ping/cancel answered
  immediately).
- Shared building blocks: settings, workdir, disk guard, GPU lock, 10 ms envelope + Otsu threshold, Whisper
  words cache v3 (continuations merged, hallucinations dropped, word starts de-stretched, skipped speech
  re-transcribed), filler listener pass, Timeline model (source <-> sequence), interval algebra, review files,
  FCP7 XML writer and `xml_cut`, optional AI tasks with rule fallbacks.
- Reference/integration tools: `echo` (fake review for panel tests) and `xmeml` (`cut`: remove ranges from an
  exported sequence XML for the panel's > 150-range path; `build`: new sequence XML from a clip list).
- Legacy `engine/autocut.py` and `engine/caption.py` keep their CLI contract for the current panel (output file
  names like `<stem>_autocut.xml` unchanged).

## UI language (Indonesian / English)

Every user-facing engine text (error msg + hint, stage labels, stage notes, GPU queue note, warnings, health
issues, `cli.py tools` titles/descriptions) comes from `engine/ac/locales/{id,en}.json` through `ac.i18n.tr`.
The language is the job's `lang` field (the panel adds it), else `AC_LANG`, else `id`. The worker's `health` and
`tools` commands accept an optional `"lang"` too. Generic keys any tool can reuse: `stage.*` (audio, audioRead,
voiceAudio, transcribe, readTranscript, checkTranscript, cutPlan, review, reviewPrep, readSeq, buildXml,
renderPreview, renderVideo, processVoice, checkResult, preview, listen), `note.*` (cached, aiCached,
cacheRefreshed, noTranscript, noTranscriptYet, noAudio, off, notNeeded, words, found, parts, threshold) and
`err.*` (badJob, noSeq, noMedia, badMedia, noAudio, noVideo, noFfmpeg, ffmpeg, diskFull, io, gpuBusy, whisper,
internal, badReview). Tool titles: `tool.<id>.title` / `tool.<id>.desc` (fallback: the module's TITLE/DESCRIPTION).
`ac.util.fmt_sec` is locale-aware (`12,5 dtk` / `12.5 s`); `ac.i18n.secs` gives the adaptive form.

## Settings read from `%APPDATA%\Klipora\settings.json` (written by the panel)

Migrated once from `%APPDATA%\AutoCutBOT` (the old folder is copied, never moved).

`workRoot` (default `%USERPROFILE%\Videos\Klipora`), `gpu` (true), `whisperModel` (`large-v3-turbo`),
`lang` (`id`, speech language), `uiLang` (`auto`), `ai` (true), `aiModel` (""). Secrets stay in `<repo>\.env` and are never returned.

## Caches written next to the media (established convention)

`<stem>_env.npy/.json` (envelope), `<stem>_words.json` (v3), `<stem>_listen.json` (listener). The three test
media (`KLIPORA_TEST_MEDIA`) had fresh caches on the dev machine (2026-10-05). The media files themselves are never modified.

## What the Premiere verifier must test (step by step)

1. Panel Settings: press the health check. Expect green Python/FFmpeg/GPU, both Whisper models "cached", AI dot
   green when an AI provider answers (grey with "AI offline ... pakai aturan" when it is stopped; in English "... using rules"). The key must not
   appear anywhere in the panel or its log.
2. Legacy Potong Silence tab (until the new silence tool replaces it): on a sequence of
   `talk_49s.mp4`, run "Potong Silence". Expect a new sequence `<name> (Klipora)` that imports
   without dialogs and plays with cuts (same structure as before the refactor).
3. Legacy caption tab: "Buat Caption" on the same sequence. Expect captions to appear, served from the v3
   cache (no long "Transkripsi" wait), with no "Terima kasih telah menonton" lines.
4. Engine job path from the panel (developer page / echo tool if exposed by the panel foundation): run echo
   analyze, toggle rows, apply. Expect stage labels, a progress bar, a review list and a `remove_ranges` plan.
5. More than 150 ranges (any tool that produces them, or echo with `every: 0.3`): the panel exports the
   sequence XML, runs `xmeml/cut` and imports the result. Expect a new sequence, no modal dialog, cuts at the
   same places as QE extract would make (the engine output is byte-identical to the lab-verified
   outputs, internal fixtures not published).
6. Worker: start a long job and press Batal. Expect the job to end with "Dibatalkan." (English: "Cancelled.") within ~1 s and the next
   job to run normally (no orphan python/ffmpeg in Task Manager).
7. GPU queue: start two transcriptions on different media at once (two panels or panel + `cli.py transcribe`).
   The second shows "Antre GPU (...)" (English: "Waiting for GPU (...)") until the first finishes, then runs.
