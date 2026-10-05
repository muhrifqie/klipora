# Contributing to Klipora

Thanks for helping. Bug reports, fixes, new caption templates, translations and docs are all welcome.
Open an [issue](https://github.com/muhrifqie/klipora/issues) first for larger changes so we can agree on the
approach.

## Dev setup

1. Follow the install steps in the [README](README.md) (Python 3.12+, FFmpeg full build, the CEP junction and
   `PlayerDebugMode`). Developing straight from your clone through the junction means a panel reload picks up
   your changes.
2. Optional: an NVIDIA GPU + `requirements-gpu.txt` for fast transcription. Everything also runs on the CPU.
3. Panel DevTools: `panel/.debug` exposes the panel on `http://localhost:8088` while Premiere runs (open it in
   Chrome). Reload the panel from DevTools after editing `panel/js` or `panel/css`; changes to `panel/host/*.jsx`
   need the panel to be closed and reopened.

Architecture, contracts and per-tool notes live in [docs/](docs/README.md). Start with `docs/SPEC.md`,
`docs/PANEL_API.md` and `docs/ENGINE_API.md`.

## Tests

Run these before opening a pull request:

```powershell
python engine/tests/run_all.py                    # engine, offline: no Premiere, no GPU job, no AI quota
node tools/ui_test.mjs tools/ui_tests/smoke.mjs   # panel in headless Chrome with a fake Premiere (tools/cep_stub.js)
node tools/i18n_check.mjs                         # locales in sync, no hardcoded UI text
node tools/compat_check.mjs                       # CEP 12 runtime limits
```

- `python engine/tests/run_all.py captions ai` runs only tests whose name contains one of the words.
- UI tests: `node tools/ui_test.mjs tools/ui_tests/<tool>.mjs [--engine live|mock] [--seq raw49|cut15|long35]
  [--width 280] [--locale en_US] [--shots <folder>]`. `--engine live` runs the real engine through the harness.
  The harness sandboxes `USERPROFILE`/`APPDATA`, so tests never touch your real settings.
- Tests never touch a running Premiere. The tools that do (`tools/cdp.mjs`, `ev.mjs`, `reload.mjs`, `shot.mjs`)
  talk to the live panel over the debug port; use them only with a scratch project.

### Test media

Many checks need real footage with speech. Point `KLIPORA_TEST_MEDIA` at a folder with:

| File | What |
|---|---|
| `talk_49s.mp4` | ~49 s screen-recorded tutorial with speech (most media tests and the default UI fixture) |
| `talk_35m.mp4` | ~35 min tutorial (long-file paths, `--seq long35`) |
| `seq_2m.mp4` | ~2 min talking clip (fillers, podcast fixture, chapter metadata) |
| `media.json` | optional: `{"talk_49s": "D:/clips/any.mp4", "talk_35m": "...", "seq_2m": "...", "glossary": ["BrandSaidInTheClip"]}` maps the names to files elsewhere (relative paths resolve against the folder) and lists brand words actually spoken, for the glossary checks |

```powershell
$env:KLIPORA_TEST_MEDIA = "D:\klipora-test-media"
python engine/tests/run_all.py
```

Without it those tests print `skip: ... set KLIPORA_TEST_MEDIA` and pass, some core tests run on a small
synthetic clip generated with FFmpeg (`engine/tests/_common.py: synth_clip`), and `ui_test.mjs --engine auto`
falls back to the mock engine. Transcripts and analysis caches are written next to the media
(`*_words.json`, `*_env.npy`, ...); the first run transcribes once. Never commit footage, transcripts or caches.

## i18n rules

The UI ships in Indonesian (`id`, the source language) and English (`en`).

- Every user-visible string goes through a locale key: panel strings in `panel/locales/{id,en}.json`
  (`AC.t('key', {vars})`, `data-i18n` attributes), engine messages in `engine/ac/locales/{id,en}.json`
  (`tr("key", **vars)`). ExtendScript host code may pass a fallback: `acT("key", "fallback")`.
- Add the key to **both** files with the same `{placeholders}`; plurals use `{"one": ..., "other": ...}`.
- Run `node tools/i18n_check.mjs`. It fails on missing keys, mismatched placeholders, empty strings and
  hardcoded Indonesian text in code. Mark intentional data (lexicons, speech regexes, fixtures) with a
  `// i18n-ignore` or `# i18n-ignore` line comment.
- Plain, friendly wording. Keep terms consistent with existing strings (e.g. "Review", "Apply", "sequence").

## Runtime constraints

- **Panel JS** (`panel/js`) runs in CEP 12 = **Chromium 99**: no ES modules, no `structuredClone`, `.at()`,
  `replaceAll`, `Array.prototype.findLast`, top-level `await`, etc. Every file is an IIFE that only adds to
  `window.AC`. No build step and no npm dependencies in the panel.
- **Panel CSS**: no `:has()`, nesting, container queries, `color-mix()`, `dvh/svh/lvh`, `@layer`, `oklch()`.
- **Host code** (`panel/host/*.jsx`) is ExtendScript = **ES3**: no `let`/`const`, arrow functions, template
  strings, `JSON`, `Array.prototype.forEach/map/indexOf`, or trailing commas. Use the helpers in `00_util.jsx`.
- `node tools/compat_check.mjs` enforces these (it also runs before every UI test).
- **Engine**: Python 3.12+, standard library first; new dependencies need a good reason and go in
  `requirements.txt`. The AI layer must stay stdlib-only and every AI feature needs a rule-based fallback.
- Never block a core feature on AI, the network or the GPU.

## Secrets and privacy

- Never commit `.env`, API keys, tokens, cookies, HAR files, personal footage, transcripts or screenshots of your
  desktop. `.gitignore` covers the usual paths; check `git status` before committing.
- Never print or log a key, and never send one anywhere except the provider it belongs to. See
  [SECURITY.md](SECURITY.md).
- Screenshots for docs or the website: use the headless harness (`--shots`) with the neutral fixtures, not
  captures of your own projects.

## Pull requests

- One topic per PR, with a short description of what changed and how you tested it (commands + result).
- Code, comments and commit messages in English; UI text in both locales.
- Keep the existing style: small functions, comments that explain *why*, no reformatting of untouched code.
