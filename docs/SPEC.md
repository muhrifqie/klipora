# Klipora (formerly AutoCut BOT) v2: Architecture & Contracts (source of truth for all build agents)

Owner: orchestrator. The research notes behind the decisions (named in the tool table below) are internal and not published.
Goal: a Premiere Pro 26.2 CEP panel + Python engine that matches and beats autocut.com for an Indonesian creator
(screen-recording tutorials today; talking-head / podcast / shorts tomorrow). Windows 11.

## 0. Non-negotiable rules

1. **Non-destructive.** Never modify the user's original sequence or media. Cutting tools work on a clone
   (`<name> (Klipora)`), overlays live on a named track that is replaced on re-run, markers are tagged so re-runs replace them.
2. **Never save the Premiere project** from code. Never delete project items you did not create.
3. **Modal dialogs hang ExtendScript.** Always `new File(p).exists` before `importFiles`; never import `.webm`;
   only import `mov/mp4/png/wav/srt/xml/mogrt` we generated. Fonts used by MOGRTs must be installed.
4. **Secrets:** `<repo>\.env` holds `AI_BASE_URL/AI_API_KEY/AI_MODEL`. Never print, log, return to the panel,
   store in DOM/localStorage, or write the key anywhere else. AI base URL must use `127.0.0.1` (not `localhost`, +2 s on Windows).
5. **Disk may be tight** (dev machines can have only a few GB free). Check free space before any render (`ac.util.ensure_free(bytes)`), write big
   outputs only under the job workdir, delete your own test renders, `pip install --no-cache-dir`. Never fill the disk.
6. **GPU (an 8 GB NVIDIA laptop GPU on the dev machine):** at most one Whisper model process at a time (engine uses a lock file
   `%LOCALAPPDATA%\Klipora\gpu.lock`). Tests must use cached transcripts whenever possible.
7. **Compat:** panel JS = Chrome 99 / CEP 12 (no `:has()`, CSS nesting, container queries, `color-mix`, `dvh`,
   `structuredClone`, `Array.prototype.at`, `replaceAll` is OK in 99 but avoid, no ES modules). ExtendScript = **ES3**
   (no `let/const`, arrow fns, `JSON` object unless our `tlJSON`, `Array.prototype.indexOf/forEach/map`, trailing commas).
   Python 3.14, stdlib + numpy/scipy/uharfbuzz/fontTools/Pillow/skia/opencv/faster-whisper/onnxruntime (already installed).
8. **UI languages = Bahasa Indonesia + English** (section 8). Indonesian copy: kamu, verbs on buttons, "dtk", WIB,
   Rp 1.500.000, decimal comma, no em-dashes. English copy: concise, sentence case, verbs on buttons ("Cut 16 pauses"),
   friendly, no em-dashes, decimal dot, "s". No user-visible literal in code: panel `AC.t(key)`, host `acT(key, fallback)`,
   engine `ac.i18n.tr(key)`; `node tools/i18n_check.mjs` enforces it. Code, comments, identifiers, commit messages = English.
9. Match surrounding code style; small focused modules; one runnable check per non-trivial module (`engine/tests/test_*.py`
   plain asserts, runnable with `python engine/tests/test_x.py`; `python engine/tests/run_all.py` runs all).
10. AI is **optional**: every feature must work (rule-based fallback) when no AI provider answers. AI output is always
    validated locally and shown as reviewable suggestions; timestamps never come from the model (resolve to Whisper words).

## 1. Layout

```
engine/
  cli.py                 # python engine/cli.py run <job.json> | health | tools   (JSON-lines on stdout)
  worker.py              # persistent JSON-lines RPC over stdin/stdout (same handlers; keeps models warm; previews)
  autocut.py, caption.py # legacy CLIs, kept working as thin wrappers over ac.*
  ac/
    __init__.py
    util.py              # paths, workdir, json io, run_ffmpeg/ffprobe, ensure_free, file hash, gpu lock, settings
    progress.py          # emit(stage/progress/stage_done/log/warn/result/error) JSON lines
    media.py             # probe, audio extract (16k mono f32), RMS envelope (10 ms) + cache, otsu threshold, frame grab
    transcript.py        # words(media) -> cache v3; hallucination filter; continuation merge; hole repair; listener pass
    timeline.py          # Timeline model (from host JSON), clip/track queries, source<->sequence mapping, words_on_timeline
    ranges.py            # interval algebra (merge, subtract, invert, pad, snap to frames/quiet points)
    review.py            # review file schema (load/save/validate/apply selections)
    xmeml.py             # FCP7 XML writer (multi-source) + xml_cut (remove ranges from exported XML)
    ai/                  # client.py, providers.py, keystore.py, prompts.py, tasks.py (cached; circuit breaker; fallback)
    tools/               # one module per tool; each exposes ACTIONS = {"analyze": fn, "apply": fn, ...}
      silence.py fillers.py repeats.py profanity.py chapters.py viral.py zoom.py resize.py angles.py podcast.py broll.py
    captions/            # model.py layout.py compile.py render.py preview.py templates/ fonts/ emoji.py glossary.py smart.py srt.py mogrt.py
  tests/
panel/
  CSXS/manifest.xml      # size 380x720, min 280x360
  index.html             # shell; loads css + js in fixed order (all tool files pre-listed)
  css/base.css           # tokens + components
  css/tools/<tool>.css   # optional per-tool styles
  js/core/*.js           # AC namespace: bridge(host/engine), router, ui components, job runner, store, theme, keys
  js/tools/<tool>.js     # one per tool, registers via AC.tools.register({...})
  host/*.jsx             # ExtendScript, loaded with $.evalFile by main loader (all files in folder, sorted)
    00_util.jsx 10_timeline.jsx 20_graphics.jsx  (shared helpers)  + <tool>.jsx per tool
tools/                   # dev helpers: ev.mjs cdp.mjs shot.mjs reload.mjs (+ fake CEP stub for headless tests)
docs/                    # SPEC.md, ENGINE_API.md, PANEL_API.md (written by foundation agents), CAPTIONS_API.md, AI_PROVIDERS.md, tools/*.md
```

## 2. Job protocol (panel ⇄ engine)

Panel writes a job file and spawns `python -X utf8 engine/cli.py run <job.json>` (or sends it to `worker.py`).

```json
{ "id": "silence-20261005-031500", "tool": "silence", "action": "analyze",
  "seq": { ...Timeline JSON from host bac_seqInfo()... },
  "params": { ... tool specific ... },
  "workdir": "%USERPROFILE%\\Videos\\Klipora\\<seq-safe-name>",
  "review": "<path to edited review json>  (apply actions)" }
```

stdout JSON lines (anything else = log):
```
{"ev":"stage","id":"transcribe","label":"Transkripsi","i":1,"n":4}
{"ev":"progress","pct":42.5}
{"ev":"stage_done","id":"transcribe","sec":2.1,"note":"dari cache"}
{"ev":"log","msg":"..."}      {"ev":"warn","msg":"AI offline, pakai aturan"}
{"ev":"result","data":{ ... }}          # exactly one on success; exit code 0
{"ev":"error","code":"NO_AUDIO","msg":"Pesan untuk user (ID)","hint":"..."}   # exit code 1
```
Cancel = `taskkill /pid <pid> /T /F`. `worker.py`: request `{"id":n,"job":{...}}` → same events wrapped `{"id":n,...}`.

Results that change the timeline are **plans** the panel applies through host functions:
```json
{"plan": {"kind": "remove_ranges", "ranges": [[t0,t1],...], "timebase": "sequence"} }
{"plan": {"kind": "markers", "markers": [{"t":0,"end":0,"name":"..","comment":"..","tag":"[Klipora-CH]","color":4}]}}
{"plan": {"kind": "keyframes", "items": [...]} }      {"plan": {"kind": "overlay", "path": "..", ...}}
{"plan": {"kind": "xml_import", "path": ".."}}         {"plan": {"kind": "tracks", ...}}
```
Every analyze action that the user should check writes a **review file** (shape:
`{tool, timebase:"sequence", duration, items:[{id,t0,t1,kind,on,conf,label,ctx,note,...}]}`) and returns its path.
The panel edits `on` flags, then calls the tool's `apply` action (or applies the plan directly).

## 3. Timeline JSON (host → engine), produced by `bac_seqInfo()` (port of lab `tlSeqInfo(seq,true)`)

```json
{ "id":"..","name":"..","fps":120,"timebase":"2116800000","width":2292,"height":960,"duration":31.2,
  "inPoint":null,"outPoint":null,"player":16.16,
  "video":[{"index":0,"name":"V1","muted":false,"locked":false,"clips":[
      {"name":"x.mp4","path":"C:\\..\\x.mp4","start":0,"end":0.94,"in":1.61,"out":2.55,"disabled":false,"selected":false,"nodeId":".."}]}],
  "audio":[{"index":0,"name":"A1","muted":false,"locked":false,"clips":[...]}],
  "markers":[...] }
```
All times in **seconds**. `ac.timeline.Timeline` wraps it: `audio_clips(include_muted=False)`, `seq_to_src(clip,t)`,
`src_to_seq(clip,t)`, `words_on_timeline()` (uses transcript cache per media; word midpoint inside clip [in,out)).

## 4. Applying cuts (verified in lab)

`remove_ranges` plan (sequence seconds) → host `bac_applyRemove(rangesTab, name)`:
1. `tlCloneSequence(active, "<name> (Klipora)")` → open clone.
2. ≤150 ranges: QE `extract` per range from the end backwards (`tlRemoveRanges`). >150 ranges: `tlExportXml` (abort to
   path 2 if `.lost` not empty) → engine `xmeml.xml_cut` → `tlImportXml`.
3. Return new sequence id/name; panel shows result + "Buka yang asli".
Keep the legacy "single raw clip → xmeml" path only as an engine fallback.

## 5. Tools (owner = one build agent each; defaults tuned for Indonesian tutorials)

| Tool id | Title (ID) | Engine actions | Timeline effect | Research (internal notes, not published) |
|---|---|---|---|---|
| silence | Potong Silence | analyze (envelope+Otsu auto threshold, hysteresis, pads 0.06/0.15, min talk 0.15, word guard), apply | remove_ranges (also: keep-spaces/mute/markers-only modes) | product_cutting §Silences v2, ux §4.1 |
| fillers | Hapus Filler | analyze (listener pass small+batched, acoustic fusion, lexicon tiers, contextual rules off), apply | remove_ranges | fillers_repeat, ai §filler |
| repeat | Potong Pengulangan | analyze (rules: stutter/false start/retake/hallucination; AI only labels), apply | remove_ranges | fillers_repeat, product_cutting |
| profanity | Sensor Kata Kasar | analyze (tiered ID+EN lexicon, AI 3-vote for ambiguous), apply (beep/mute/duck) | volume keys + tone clips on new audio track; captions masking list | product_ai |
| captions | Auto Caption | transcribe, doc (build captions doc), preview, gallery, render (overlay), srt, mogrt, cleanup(AI), glossary | overlay track "Klipora Captions" (relink versioned files) / native SRT / MOGRT (short) | caption_render_tech, premiere_graphics_api, product_captions, vision §captions |
| chapters | Bab Otomatis | analyze (parts in parallel, merge to count) | markers tagged [Klipora-CH] + YouTube text + copy | product_ai, ai |
| viral | Klip Viral | analyze (windows, quote-anchored, expand, rescore) , apply | markers + createSubsequence per clip (+ optional 9:16) | product_ai, ai |
| zoom | Auto Zoom | analyze (screen activity map / face anchor / emphasis words; rhythm budget), apply | Motion Scale/Position keyframes per clip (media time) | vision §4, product_visual |
| resize | Auto Resize | analyze (reframe path: hybrid camera; screen = focus+blur layout), apply | new sequence WxH + Position keys; or native autoReframeSequence | vision, product_visual |
| angles | Angle Otomatis | analyze (punch-in 100/118/140 per cut, no repeats), apply | Motion Scale keys per clip | product_cutting §Angles |
| podcast | Podcast Multicam | analyze (per-mic loudness, 6 dB rule, 0.8 s confirm, 2 s min shot, auto sync), apply | stacked cam tracks, inactive clips disabled | product_cutting §Podcast |
| broll | B-Roll | analyze (one AI plan; activity-aware placement), fetch (local folder / Pexels key / optional AI still from the local proxy's image endpoint + Ken Burns), apply | clips on new top track | product_visual §B-roll |

Captions output default = **straight-alpha qtrle overlay** rendered with the black/white difference matte (NOT `ass alpha=1`),
30 fps, on track named `Klipora Captions`, relinked with `changeMediaPath` to a new versioned file on each re-render.
Sequence `compositeLinearColor` is never changed silently (offer a toggle). Per-word overrides keyed by stable word id.

## 6. Panel framework (implemented by foundation; tools use it)

`window.AC` namespace (classic scripts). Foundation must document the final API in `docs/PANEL_API.md`. Minimum:
- `AC.host.call(fn, ...args) → Promise<string>`; `AC.host.json(fn, ...args) → Promise<object>` (ERR: → reject).
- `AC.engine.run({tool, action, params, seq?}) → Job` with `.on('stage'|'progress'|'log'|'warn'|'result'|'error')`, `.cancel()`;
  `AC.engine.worker(req)` for fast calls (preview). Python path from settings (default `python`).
- `AC.seq.current()` (cached Timeline JSON, refreshed on Premiere events via `tlBindEvents`, fallback polling 2 s).
- `AC.tools.register({id, group, title, tab, icon, badges, render(el, ctx), onShow, onHide})`.
- Components: source card, preset segmented control, slider with unit + helper, collapsible advanced section, radio cards,
  switch (keyboard-focusable), dock with primary action + Ctrl+Enter, progress stages + cancel, **review list**
  (virtualized, checkbox, click-to-seek via `bac_seek`, filters, bulk toggles), result card (before/after, cut ribbon,
  "Buka yang asli"), toasts, empty/error states, cut-map ribbon, waveform canvas.
- Router: Home (groups Potong/Teks/Kamera/Publikasi + Resep), tool pages with sibling tabs, Settings, Riwayat, Ctrl+K palette.
- Theme: design tokens (ember accent #f7843e, dark text on accent), follow Premiere brightness.
- Settings (persisted to `%APPDATA%\Klipora\settings.json` via Node fs): python path, work root, GPU on/off, Whisper model,
  AI on/off + health dot (`cli.py health`), Pexels key (write-only field), default caption template, output folder.

## 7. Testing protocol

- Build agents: engine tested offline on real media + cached transcripts; panel tested in **headless Chrome**
  (`C:/Program Files/Google/Chrome/Application/chrome.exe --headless=new`, runner `tools/ui_test.mjs`) with the fake CEP stub
  `tools/cep_stub.js`. **Build agents must not touch live Premiere.**
- Verify agents (serialized, one at a time): live Premiere via `tools/ev.mjs` / `tools/cdp.mjs` / `tools/reload.mjs` /
  `tools/shot.mjs`; work in bin `Klipora Test`, on sequences built from test media; restore the user's active sequence;
  delete only what you created; never save. Visual checks via QE `exportFramePNG` (`tlExportFrame`). Panel screenshots go to
  the `--shots` folder (default `docs/shots/<tool>.png`, gitignored).
- Test media: a folder named by env `KLIPORA_TEST_MEDIA` with `talk_49s.mp4` (~49 s), `talk_35m.mp4` (~34.6 min) and
  `seq_2m.mp4` (~2 min). An optional `media.json` in that folder maps `talk_49s` / `talk_35m` / `seq_2m` to absolute
  paths elsewhere (plus optional `glossary`: brand terms actually spoken in your clip). The reference clips are
  2292x960 120 fps screen-recorded Indonesian tutorials. Tests that need media skip with "skip: ... set
  KLIPORA_TEST_MEDIA" when it is missing.

## 8. Name, data folders, languages (Klipora release)

- Product name **Klipora** (open source, global). Internal names kept to avoid churn: Python package `ac`, `window.AC`,
  file names, host prefixes `bac_*`, the repo folder name `AutoCut` in older checkouts.
- CEP: bundle `com.klipora.panel`, extension `com.klipora.panel.main` (menu Window > Extensions > Klipora; debug port 8088),
  installed as the junction `%APPDATA%\Adobe\CEP\extensions\com.klipora.panel` -> `<repo>\panel`.
  Host event type `com.klipora.ev`.
- Data: `%APPDATA%\Klipora` (settings, history, profiles, user libraries), `%LOCALAPPDATA%\Klipora` (locks, logs,
  caches), `%TEMP%\Klipora\jobs`. One-time migration from the old `AutoCutBOT` folders: the first process that needs
  the folder (panel `sys.js` or engine `util.py`) COPIES the old folder (lock/tmp files skipped) into
  `<new>.migrating-<pid>`, renames it to the new name and writes `MIGRATED_FROM.txt`; the old folder is left untouched.
  If the copy fails, the old folder keeps being used. Existing settings keep their saved `workRoot` (Premiere projects link
  to media rendered there); new installs default to `%USERPROFILE%\Videos\Klipora`.
- Premiere names: result sequences `<name> (Klipora)`, tracks `Klipora Captions`, `Klipora Captions (Editable)`,
  `Klipora Suara`, `Klipora Sensor`, `Klipora B-Roll`, `Klipora SFX`, bins `Klipora`, `Klipora Captions`,
  `Klipora Viral`, `Klipora B-Roll`, marker tags `[Klipora-XX]`. The old names (`(AutoCut[ n])`, `AutoCut <X>` tracks
  and bins, bin `AutoCut BOT`, tags `[AC-XX]`) are still recognised for replace / relink / cleanup
  (host `acNameAlts / acTagAlts`, panel `AC.brand / U.hasTag`, engine checks).
- Languages: setting `uiLang` = `auto` (Premiere UI locale from `getHostEnvironment().appUILocale`: `id*` -> Indonesian,
  else English) | `id` | `en`. Installs from before this release (settings.json without `uiLang`) are set to `id`.
  Panel strings: `panel/locales/{id,en}.json`; engine strings: `engine/ac/locales/{id,en}.json`; host strings are the
  panel's `host.*` keys, sent with `bac_setStrings`. Every engine job carries `"lang"` and every engine process gets
  env `AC_LANG`. Switching the language re-renders the panel without a reload (idle tool pages are re-rendered; a tool
  with a running job keeps its page). Formatting: id = decimal comma, "dtk", WIB times; en = decimal dot, "s", PC local
  time. The speech language (`lang`, Whisper) is a separate setting.
