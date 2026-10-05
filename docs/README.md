# Klipora docs

Engineering documentation for Klipora: a Premiere Pro CEP panel (`panel/`) plus a Python engine (`engine/`).
Start with the project [README](../README.md) for installation and the [contributing guide](../CONTRIBUTING.md)
for the dev workflow. Some pages (user guides, the v3 checklist) are in Bahasa Indonesia; the technical
references are in English.

## Architecture and APIs

| File | What it covers |
|---|---|
| [SPEC.md](SPEC.md) | Architecture and contracts: non-negotiable rules, repo layout, job protocol (panel <-> engine), Timeline JSON, how cuts are applied, tool list, testing protocol, names, data folders and languages |
| [ENGINE_API.md](ENGINE_API.md) | Python engine reference: CLI and worker protocol, writing a tool, module reference (`engine/ac/*`), caches, GPU lock, AI layer, tests, measurements |
| [PANEL_API.md](PANEL_API.md) | Panel framework for tool authors: `window.AC` API, host (ExtendScript) bridge, components, review list, headless test harness (`tools/ui_test.mjs`), i18n |
| [CAPTIONS_API.md](CAPTIONS_API.md) | Auto Caption engine API: caption document model, templates, styles, rendering, preview, SRT/MOGRT export, animation + SFX, style from image, Brand Kit |
| [AI_PROVIDERS.md](AI_PROVIDERS.md) | AI providers: user guide (Indonesian) and technical reference for profiles, encrypted keys, presets (local OpenAI-compatible proxy, OpenRouter, Gemini, OpenAI, xAI, Anthropic, ...), routing and fallbacks |
| [CHANGELOG_v3.md](CHANGELOG_v3.md) | v3 release notes (Auto Caption + AI providers) and the live Premiere test checklist (Indonesian; uses the old name "AutoCut") |

## Tools (`tools/`)

| File | What it covers |
|---|---|
| [tools/engine.md](tools/engine.md) | Engine foundation (`cli.py`, `worker.py`, shared modules), settings, caches, live checks |
| [tools/silence.md](tools/silence.md) | Potong Silence (Cut Silences): envelope + Otsu threshold, pads, modes, review, live tests |
| [tools/fillers.md](tools/fillers.md) | Hapus Filler (Remove Fillers): listener pass + acoustic fusion, lexicon tiers, habit words |
| [tools/repeat.md](tools/repeat.md) | Potong Pengulangan (Cut Repeats): stutters, false starts, retakes, optional AI labels |
| [tools/profanity.md](tools/profanity.md) | Sensor Kata Kasar (Bleep Profanity): ID + EN lexicon, AI vote for ambiguous words, beep/mute/duck |
| [tools/captions.md](tools/captions.md) | Auto Captions, engine half: transcript -> caption doc, render, placement, live checks |
| [tools/captions_ui.md](tools/captions_ui.md) | Auto Captions, editor UI and Premiere placement |
| [tools/captions_brand.md](tools/captions_brand.md) | Style from image + Brand Kit (user guide, Indonesian) |
| [tools/chapters.md](tools/chapters.md) | Auto Chapters: chapter picks and titles, markers, YouTube text, title/description/hashtags |
| [tools/viral.md](tools/viral.md) | Viral Clips: windowing, scoring, sub-sequences per clip, optional 9:16 |
| [tools/zoom.md](tools/zoom.md) | Auto Zoom: screen activity, speech emphasis, rhythm; Motion keyframes |
| [tools/resize.md](tools/resize.md) | Auto Resize: smart reframe (screen / faces), focus + blur, Premiere Auto Reframe |
| [tools/angles.md](tools/angles.md) | Auto Angles: punch-in framing per cut, optional sentence splits |
| [tools/podcast.md](tools/podcast.md) | Podcast Multicam: per-mic loudness, camera switching on stacked tracks |
| [tools/broll.md](tools/broll.md) | B-Roll: moment picking, local library / Pexels / optional AI still, placement on a new track |
| [tools/voice.md](tools/voice.md) | Suara Jernih (Clear Voice): denoise, EQ, loudness, preview |

## Notes

- **Not in the public repo:** `docs/research/` (research notes), `docs/shots/` (screenshots written by the headless
  tests, the default `--shots` folder) and `proto/` (prototypes) are internal and gitignored. Docs mention them only
  as background; nothing here requires them.
- **Test media:** tests and live checks that need real footage read the folder in env `KLIPORA_TEST_MEDIA`
  (`talk_49s.mp4`, `talk_35m.mp4`, `seq_2m.mp4`, optional `media.json`; see SPEC section 7). Without it those tests
  print "skip: ... set KLIPORA_TEST_MEDIA".
- **Measurements** marked "this PC" were taken on the dev machine (Windows 11, 8 GB RTX-class laptop GPU); expect
  different numbers on other hardware.
- `<repo>` means your checkout folder (for example `C:\klipora`).
