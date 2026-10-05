# Klipora v2: Engine API (foundation)

Source of truth for tool agents writing `engine/ac/tools/<tool>.py` (and `engine/ac/captions/`).
Everything below exists and is tested (`python engine/tests/run_all.py`, 12/12 pass, ~15 s, offline).
Read `docs/SPEC.md` first; this file documents the real Python APIs behind it.

Contents: 1 Quick start · 2 Job protocol (CLI + worker) · 3 Writing a tool (3.4 user-facing text) · 4 Module reference ·
5 Caches and files · 6 GPU lock · 7 AI · 8 Tests · 9 Legacy CLIs · 10 Measurements · 11 Gotchas

---

## 1. Quick start (a tool in 40 lines)

```python
# engine/ac/tools/mytool.py
"""What the tool does (one paragraph)."""
from __future__ import annotations

from .. import media as M, ranges as R, review as RV
from ..timeline import Timeline
from ..util import EngineError, workdir

TITLE = "Nama Tool"                       # fallback; `cli.py tools` shows tr("tool.<id>.title") / tr("tool.<id>.desc")
DESCRIPTION = "Satu kalimat."


def analyze(job, emit):
    p = job["params"]                     # always a dict (cli fills {})
    tl = Timeline.from_json(job["seq"])   # EngineError NO_SEQ when empty
    emit.plan([("audio", "Baca audio", 0.3), ("words", "Transkripsi", 0.5), ("plan", "Hitung", 0.2)])
    with emit.step("audio"):
        db = tl.envelope_on_timeline()    # sequence time, 10 ms frames, cached per media
    with emit.step("words"):
        words = tl.words_on_timeline(emit=emit)          # transcribes once per media (GPU lock), cached v3
    with emit.step("plan"):
        thr = M.otsu_db(db)
        gaps = R.invert(M.voiced_spans(db, thr), 0.0, tl.duration)
        items = [RV.item(a, b, "gap", conf=0.9, label=f"Jeda {b - a:.1f} dtk".replace(".", ","))
                 for a, b in gaps if b - a >= float(p.get("min", 0.5))]
        emit.progress(100)
    doc = RV.new("mytool", items, tl.duration, seq=tl, params=p, thr=thr)
    path = RV.save(doc, RV.path_for(workdir(job), "mytool"))
    return {"review": str(path), "summary": RV.summary(doc)}


def apply(job, emit):
    doc = RV.from_job(job)                # job["review"] = the file the panel edited
    return {"plan": {"kind": "remove_ranges", "ranges": R.round_list(RV.selected_ranges(doc)), "timebase": "sequence"}}


ACTIONS = {"analyze": analyze, "apply": apply}
```

Run it exactly like the panel does:

```
python -X utf8 engine/cli.py run job.json      # job.json = {"tool":"mytool","action":"analyze","seq":{...},"params":{...},"workdir":"..."}
```

Reference implementation used by the panel foundation for integration tests: `engine/ac/tools/echo.py`
(analyze/apply/slow/fail/crash/echo). Foundation XML jobs: `engine/ac/tools/xmeml.py` (cut/build).

---

## 2. Job protocol

### 2.1 Job file (panel -> engine)

```json
{ "id": "silence-20261005-031500-1234",
  "tool": "silence", "action": "analyze",
  "seq": { ...Timeline JSON (SPEC section 3)... }  |  "C:\\...\\seq.json"  |  null,
  "params": { ...tool specific... },
  "workdir": "%USERPROFILE%\\Videos\\Klipora\\<safe seq name>",
  "review": "C:\\...\\silence_review.json",
  "lang": "en" }
```

- `lang` (`"id"` | `"en"`, optional, default `"id"`): UI language for every user-facing text the job produces
  (stage labels, notes, warnings, error msg/hint, review labels, summaries, option/template labels). The panel adds it
  to every job (`AC.engine.run` / `worker`); `cli.execute` runs the job inside `ac.i18n.using(lang)`. Engine processes
  started by the panel also get env `AC_LANG` (used by `health`, `tools`, `cli.py ai ...`). See section 3.4.
- `seq` may be inline, a path to a JSON file, or null (tools that do not need a sequence). `cli.load_job()`
  normalises it; `params` is always a dict.
- The panel writes jobs to `%TEMP%\Klipora\jobs\<id>.json` and sets `workdir` from its settings.

### 2.2 `engine/cli.py`

| Command | Output (stdout, JSON lines) | Exit |
|---|---|---|
| `cli.py run <job.json>` | stage/progress/stage_done/log/warn events, then exactly one `result` or one `error` | 0 ok / 1 error |
| `cli.py health` | one `{"ev":"result","data":{...}}` (section 2.5), ~2 s | 0 |
| `cli.py tools` | one result `{"tools":[{id,title,description,actions,ok,error?}]}`, ~0.15 s | 0 |
| `cli.py transcribe <media> [--listen] [--refresh]` | build caches (dev helper) | 0 / 1 |
| `cli.py ai <profiles\|save\|order\|activate\|remove\|set-key\|delete-key\|models\|test\|health> ...` | AI provider profiles, one result (docs/AI_PROVIDERS.md 2.5); keys only via stdin | 0 / 1 |
| bad command line | argparse usage on stderr | 2 |

Always spawn with `python -X utf8`. Cancel = `taskkill /pid <pid> /T /F` (kills ffmpeg children too).

### 2.3 Events

```
{"ev":"stage","id":"transcribe","stage":"transcribe","label":"Transkripsi","i":1,"n":4,"w":0.5}
{"ev":"progress","pct":42.5,"all":61.2}                     pct = inside the current stage, all = whole job
{"ev":"progress","pct":0,"note":"Antre GPU (transkripsi x.mp4) 12 dtk"}     status text while waiting
{"ev":"stage_done","id":"transcribe","stage":"transcribe","sec":2.1,"note":"dari cache"}
{"ev":"log","msg":"..."}         {"ev":"warn","msg":"AI tidak dipakai, pakai aturan: ..."}
{"ev":"result","data":{...}}     exactly one on success (the action's return value)
{"ev":"error","code":"NO_AUDIO","msg":"Pesan untuk user (ID)","hint":"..."}
```

- `i` is **0-based**, `n` = number of planned stages, `w` = stage weight (weights sum to 1). `i/n/w` exist only
  when the tool called `emit.plan(...)`; `all` likewise.
- Stage events carry the stage id in **both** `id` and `stage`; in worker mode `id` is the request id, so the
  panel must read `stage` (panel `engine.js` does).
- Progress is throttled (max ~10/s and 0.5 % steps); `note` and 100 % always go out.
- Anything a tool `print()`s becomes `{"ev":"log"}`; child processes and C extensions write to **stderr**
  (cli/worker call `progress.claim_stdout()`), so stdout is always pure JSON lines.
- All `msg`/`hint` text passes `util.redact()` (API keys never reach the panel).

Error codes used by the foundation: `BAD_JOB`, `NO_TOOL`, `NO_ACTION`, `TOOL_IMPORT`, `NO_SEQ`, `NO_MEDIA`,
`BAD_MEDIA`, `NO_AUDIO`, `NO_VIDEO`, `NO_FFMPEG`, `FFMPEG`, `DISK_FULL`, `IO`, `GPU_BUSY`, `WHISPER`,
`CANCELLED`, `BAD_REVIEW`, `NO_XML`, `BAD_XML`, `INTERNAL` (unexpected exception; hint = traceback tail).
Tools add their own (UPPER_SNAKE, Indonesian `msg`, actionable `hint`).

### 2.4 `engine/worker.py` (persistent, keeps Whisper warm)

```
python -X utf8 engine/worker.py [--idle-unload 300]
<- {"ev":"ready","pid":1234,"version":"2.0.0"}
-> {"id":7,"job":{...job object...}}            <- {"id":7,"ev":"stage","stage":"audio",...} ... {"id":7,"ev":"result",...}
-> {"id":8,"cmd":"ping"}                        <- {"id":8,"ev":"result","data":{"pong":true,"busy":7,"queue":0,"pid":..,"version":..}}
-> {"id":9,"cmd":"cancel","target":7}           <- {"id":9,"ev":"result","data":{"cancelled":true}}  then job 7 ends with error CANCELLED
-> {"id":10,"cmd":"health"|"tools"|"unload"}    <- same data as the CLI commands / {"unloaded": n}
-> {"id":11,"cmd":"shutdown"}                   <- {"id":11,"ev":"result","data":{"bye":true}}; stdin EOF also stops it
```

- Jobs run **one at a time** in arrival order; `ping`/`cancel` are answered immediately even while a job runs.
- Cancel is cooperative: `emit.progress()` and `emit.check_cancel()` raise `Cancelled` once requested. A tool
  must call `emit.progress()` regularly inside long loops (or `emit.check_cancel()`); the CLI path is killed
  with taskkill instead.
- Whisper models stay loaded between jobs and are freed after `--idle-unload` seconds without jobs.

### 2.5 `health` result

```json
{"ok": true, "issues": ["AI offline: ... Fitur tetap jalan pakai aturan."], "ms": 1745,
 "python": {"ok": true, "version": "3.14.3", "exe": "..."},
 "ffmpeg": {"ok": true, "path": "...", "version": "ffmpeg version 8.1.1 ...", "libass": true, "nvenc": true, "qtrle": true, "ffprobe": true},
 "gpu": {"ok": true, "name": "NVIDIA GeForce RTX ... Laptop GPU", "mem_used_mb": 3174, "mem_total_mb": 8151, "driver": "616.92",
         "cuda_dlls": {"cublas64_12.dll": true, "cublasLt64_12.dll": true, "cudnn64_9.dll": true},
         "ctranslate2": "4.8.1", "cuda_devices": 1, "lock": {"pid": 1, "label": "...", "since": "..."}},
 "models": {"large-v3-turbo": {"cached": true, "repo": "mobiuslabsgmbh/faster-whisper-large-v3-turbo"}, "small": {...}},
 "ai": {"ok": true, "ms": 22, "base_url": "http://127.0.0.1:8168/v1", "model": "grok-fast", "has_key": true, "disabled": false, "why": "..."},
 "disk": {"ok": true, "free_gb": {"C:": 5.6, "workRoot": 5.6}},
 "engine": {"version": "2.0.0", "dir": "...", "settings": "...\\settings.json", "appdata": "...", "local": "..."}}
```
`ok` = Python + FFmpeg usable (hard requirements); everything else is informative and listed in `issues`
(Indonesian). The AI key is never part of it.

---

## 3. Writing a tool

### 3.1 Contract

- File `engine/ac/tools/<id>.py` (or the `engine/ac/captions/` package for tool id `captions`) defines
  `ACTIONS = {"analyze": fn, "apply": fn, ...}`; optional `TITLE`, `DESCRIPTION`. Discovery is lazy
  (`ac.tools.names()` lists files without importing; `get()` imports on first use), so keep heavy imports
  (faster_whisper, cv2, skia) inside functions.
- `fn(job, emit) -> dict` (JSON-serialisable; numpy scalars/arrays and Paths are converted). Return value =
  `result.data`. Raise `ac.util.EngineError(code, msg_id, hint_id)` for user-facing failures; any other
  exception becomes `INTERNAL` (traceback in hint + `%LOCALAPPDATA%\Klipora\logs\engine.log`); `OSError`
  becomes `IO` / `DISK_FULL`.
- Never `print()` protocol lines yourself; never write outside `workdir(job)` except the media caches in
  section 5; call `util.ensure_free(bytes, dir)` before writing anything big; delete your temporary files.
- Times in results/review/plans are **sequence seconds** (Timeline maps source <-> sequence).

### 3.2 Recommended result shapes

```json
analyze -> {"review": "<path>", "summary": {"n": 12, "on": 9, "sec_on": 31.4}, ...extra stats/previews}
apply   -> {"plan": {...}, "summary": {...}}
```

Plans the panel applies (SPEC section 2): `remove_ranges` (`{"kind":"remove_ranges","ranges":[[t0,t1]],
"timebase":"sequence"}`; the panel merges in frame space, clones the sequence, extracts <= 150 ranges or goes
through `xmeml/cut` above that), `markers` (`{"kind":"markers","markers":[{"t","end","name","comment","tag","color"}]}`),
`keyframes`, `overlay`, `xml_import` (`{"kind":"xml_import","path":...}`), `tracks`. Round ranges with
`ranges.round_list()`; for removals prefer `ranges.to_frames(r, tl.fps, "inner")` so no kept frame is eaten.

### 3.3 The `emit` object (`ac.progress.Emitter`)

| Call | Effect |
|---|---|
| `emit.plan([(id, label, weight), ...])` | declare stages up front (enables `i/n/w` and `all`) |
| `with emit.step(id[, label]):` | `stage` then `stage_done` with measured `sec` (no stage_done on exception) |
| `emit.stage(id, label=None, i=None, n=None, w=None, sub=None)` / `emit.stage_done(id=None, sec=None, note=None)` | manual form |
| `emit.progress(pct, note=None, force=False)` | 0..100 inside the stage; throttled; **raises Cancelled when cancelled** |
| `emit.sub(lo, hi)` | callable mapping a helper's 0..100 onto lo..hi of the current stage |
| `emit.done_note("dari cache")` | note shown on the current stage's `stage_done` |
| `emit.wait(note)` | status line without moving the bar (used by the GPU queue) |
| `emit.log(msg)`, `emit.warn(msg)` | redacted text events (warn = shown to the user, e.g. AI fallback) |
| `emit.check_cancel()`, `emit.cancelled` | cooperative cancel |
| `emit(ev, **fields)` | raw event |

Foundation helpers (`transcript.words`, `Timeline.words_on_timeline`, ...) take `emit=` and report progress
0..100 **inside your current stage** and set `done_note("dari cache")` when cached. Tests use
`ListEmitter()` (collects `.items`) or `NullEmitter()`.

### 3.4 Review files (`ac.review`)

```json
{"v": 1, "tool": "silence", "timebase": "sequence", "duration": 48.925, "created": "2026-10-05T03:15:00+07:00",
 "seq": {"id": "...", "name": "..."}, "params": {...}, "stats": {"n": 12, "on": 9, "sec_on": 31.4},
 "items": [{"id": "g1150", "t0": 11.5, "t1": 15.17, "kind": "gap", "on": true, "conf": 0.9,
            "label": "Jeda 3,7 dtk", "ctx": {"pre": "dari membuka dulu", "post": "ini dia,"},
            "note": {"type": "guard", "words": ["Nah"], "text": "..."}, "cut": [11.56, 15.02], "...": "tool extras"}]}
```

- `id` = kind initial + `round(t0*100)` (`g1150`), `-2`, `-3` on collisions (shorter item first): the same
  finding keeps its id across re-runs, so `carry_over(new, old)` can restore the user's manual toggles
  (`touched: true` items).
- Panel contract (`panel/js/core/review.js`): it reads `id,t0,t1,kind,on,conf,label,ctx` (`{pre,post}` or string,
  `label` is the highlighted middle part), `note` (`string` or `{type: guard|ai|warn|info, text, words}`),
  `ai.reason`; edits only `on` (+ `touched`) and passes the edited file as `job.review` to `apply`.
- Optional `cut: [a, b]` = exact range to remove when it differs from the displayed `t0..t1`.

---

### 3.4 User-facing text (`ac.i18n`, Indonesian + English)

Never put user-visible literals in a tool. Strings live in `engine/ac/locales/{id,en}.json` (flat keys; plural
values `{"one": "...", "other": "..."}`), and `node tools/i18n_check.mjs` checks key/placeholder parity and flags
Indonesian literals left in `engine/ac/**/*.py` (mark data such as lexicons with `# i18n-ignore`).

| Call | Effect |
|---|---|
| `tr(key, **vars)` (alias `t`) | text in the current language; `{name}` placeholders; plural form by `n`; integers formatted per language (1.500 / 1,500); missing key -> Indonesian -> the key |
| `get_lang()`, `set_lang(l)`, `using(l)` | current language: job `lang` > env `AC_LANG` > `"id"` (process-global; the worker runs one job at a time) |
| `dec(x, d)`, `int_(n)` | `0,50` / `0.50`, `1.500.000` / `1,500,000` |
| `secs(s)`, `mmss(s)` | `3,7 dtk` / `3.7 s`, `1 mnt 30 dtk` / `1 min 30 s`; `0:49` |
| `has(key)`, `raw(key)`, `norm(l)` | helpers (`norm("en_US") == "en"`) |

Call `tr()` when the job runs, never at import time (module constants with labels are keys, translated inside the
action). `emit.log` text is for developers (English, not translated). AI prompts are content and are not translated.
`cli.py tools` shows `tr("tool.<id>.title")` / `tr("tool.<id>.desc")`, falling back to the module's TITLE/DESCRIPTION.

## 4. Module reference

All modules live in `engine/ac/`. Import as `from ac import media as M` (cli/worker/tests put `engine/` on
`sys.path`) or relatively inside tools (`from .. import media as M`).

### 4.1 `ac.util`

| API | Notes |
|---|---|
| `EngineError(code, msg, hint="")`, `Cancelled()` | `.to_event()`; Cancelled.code = `CANCELLED` |
| `ENGINE_DIR`, `PROJECT_DIR`, `WIB` | paths / UTC+7 tz |
| `appdata_dir()` -> `%APPDATA%\Klipora` | settings (shared with the panel) |
| `local_dir(*sub)` -> `%LOCALAPPDATA%\Klipora\...` | locks, logs, ai_cache, fallback media caches |
| `settings()` -> dict, `setting(key, default=None)` | `settings.json` merged over `DEFAULT_SETTINGS`; re-read when the file changes |
| `DEFAULT_SETTINGS` | `python, workRoot (%USERPROFILE%\Videos\Klipora), gpu (True), whisperModel ("large-v3-turbo"), lang ("id"), ai (True), aiModel (""), captionTemplate, outputDir, glossary ([])` |
| `safe_name(name, max_len=80)` | Windows-safe file name |
| `workdir(job=None, seq_name=None)` -> Path | `job["workdir"]` else `<workRoot>\<safe seq name>`; created |
| `read_json(path, default=None)`, `write_json(path, obj, indent=None)`, `dumps(obj, indent=None)` | BOM tolerant; atomic write (tmp + replace); numpy values converted and NaN/inf written as `null` (the panel's JSON.parse rejects NaN; protocol lines do the same) |
| `now_iso()`, `job_id(tool)`, `fmt_sec(12.5)` -> `"12,5 dtk"` | WIB times, Indonesian numbers |
| `cache_path(media, suffix)` | `<stem><suffix>` next to the media, else `%LOCALAPPDATA%\Klipora\cache\<hash><suffix>` |
| `file_hash(path)` | sha1(abs path lower, size, mtime_ns)[:16]; no content read |
| `data_hash(obj)` | sha256[:24] of JSON (cache keys) |
| `run(cmd, check, capture, timeout, text, input)` | subprocess without console window, stdin=DEVNULL |
| `run_ffmpeg(args, check=True, timeout=None)` | `ffmpeg -hide_banner -nostdin -loglevel error ...`; bytes stdout (pipe PCM/frames); `EngineError("FFMPEG")` with stderr tail |
| `ffprobe(path)` -> dict | `EngineError("NO_MEDIA"/"BAD_MEDIA")` |
| `free_bytes(path)`, `ensure_free(need_bytes, path=None, margin=500 MB)` | raises `DISK_FULL` "Ruang disk C: tinggal 4,8 GB, butuh sekitar 1,2 GB." |
| `GpuLock(emit=None, timeout=1800, label="", poll=0.5)`, `gpu_lock(emit, timeout, label)` | section 6 |
| `cuda_setup()` | adds pip cuBLAS/cuDNN `bin` dirs (idempotent) |
| `redact(text)` | removes .env/settings secrets and `Bearer/api_key/token` values |
| `log()` | file logger `%LOCALAPPDATA%\Klipora\logs\engine.log` (1 MB x 2, redacted); never stdout |

### 4.2 `ac.progress`

`Emitter(sink=None, wrap=None, min_interval=0.1)` (section 3.3), `NullEmitter()`, `ListEmitter()` (`.items`,
`.kinds()`), `claim_stdout()` (cli/worker only), `write_line(obj)`, `to_json_line(obj)`.

### 4.3 `ac.media` (numpy)

Constants: `SR = 16000`, `HOP = 0.01` (frame i <-> time `i*HOP`), `WIN = 0.03`, `FLOOR_DB = -90`.

| API | Returns / notes |
|---|---|
| `require(path)` | Path or `EngineError("NO_MEDIA")` |
| `probe(path)` | `{path, duration, fps, fps_frac "120/1", width, height, has_video, has_audio, audio_streams, sample_rate, channels, vcodec, acodec}` (cached per hash) |
| `load_audio(path, sr=16000, stream=None, start=None, dur=None)` | mono float32 via ffmpeg pipe; `stream="0:a:1"` picks an OBS track; `NO_AUDIO` if none. 34.6 min = 33 M samples (133 MB) in 1.2 s |
| `rms_db(x, sr, hop, win, floor)` | float32 dB per 10 ms (30 ms window), clamped -90 |
| `envelope(path, stream=None, emit=None, refresh=False, audio=None)` | cached `<stem>_env.npy` + `<stem>_env.json` (`{v, hash, hop, win, sr, n, stream, duration, floor}`); stream caches `<stem>_env_0a1.npy` |
| `idx(t)`, `env_slice(db, t0, t1)` | frame index / slice |
| `downsample(db, bars=2000, mode="max")` | list for UI waveforms (bar k covers `len(db)*HOP/bars` s) |
| `otsu_db(db)` | Otsu valley over the dB histogram (plateau centre): -53.5 (34.6 min), -52.5 (49 s), -53.5 (2 min) |
| `gate(db, thr, hyst=4.0)` | bool mask with hysteresis (run counts when it holds a frame >= thr) |
| `runs(mask, hop, min_gap=0, min_len=0)` | `[[t0, t1]]` seconds (rounded 1e-6) |
| `voiced_spans(db, thr=None, min_gap=0.3, min_len=0.06, hyst=4.0)` | sound regions |
| `snap_quiet(db, t, radius=0.03)` | quietest frame within +-radius (click-free cut points) |
| `first_audible(db, t0, t1, thr)`, `last_audible(db, t0, t1, thr)` | onset / offset inside a window or None |
| `frame_grab(path, t, out=None, width=None)` | RGB uint8 ndarray (H, W, 3), or writes PNG `out` and returns its Path |

### 4.4 `ac.transcript`

Word rows (cache v3): `[text, start, end, seg_end, prob]`, source seconds. `text` keeps Whisper's leading
space (`"".join(texts)` = transcript; use `.strip()` to display); continuation tokens are already merged, so
**one row = one word and the row index is a stable word id** (`<file_hash[:10]>:<index>` on the timeline).
Spans are sorted and non-overlapping; starts are de-stretched to the audible onset (ends are Whisper's).

| API | Notes |
|---|---|
| `words(path, model=None, lang=None, emit=None, refresh=False, device=None, audio=None, repair=True)` | cached v3 or transcribe (GPU lock, CPU fallback with a warning). Upgrades a v2 cache in place (post-process + hole repair, no full pass). Errors: `NO_MEDIA, NO_AUDIO, GPU_BUSY, CANCELLED, WHISPER` |
| `cached_words(path, model=None, lang=None)` / `has_words(path)` | never transcribes (use for optional features, e.g. a word guard) |
| `load_doc(path, model=None, lang=None)` | full cache document (below) or None |
| `listener_words(path, emit=None, refresh=False, device=None, audio=None, batch=8)` | filler listener pass `[[text, start, end, prob]]` (small + BatchedInferencePipeline + `LISTEN_PROMPT`), cached `<stem>_listen.json`; timestamps +-0.2 s, snap them with the envelope |
| `words_path(path)`, `listen_path(path)` | cache file locations |
| `merge_continuations(rows)` | `" teman"+"-teman,"`, `".com"`, `".000"`, `"%"` glued; `" .com"` WITH a space stays a word (spoken "titik com") |
| `drop_hallucinations(rows, on=None)` -> `(rows, issues)` | stock phrases (`HALLU_RE`: "terima kasih telah menonton", "sampai jumpa di video ...", "selamat menikmati", ...; a single phrase over clear sound is kept, repeats always dropped), n-gram loops (1 word x4, 2-4 words x3), dense repeats, segment loops; numbers and real stutters ("eh sorry sorry sorry", "ya ya ya") survive |
| `destretch(rows, on)` -> `(rows, moved)` | start -> first sounding frame - 30 ms |
| `find_holes(rows, on)` | `[[t0, t1, voiced_s]]` uncovered sound spans >= 2 s with >= 1 s sound |
| `repair_holes(rows, audio, on, model, lang, emit, p0, p1)` | re-transcribe each hole alone, strict segment filter (`_real_seg`), duplicate guard |
| `postprocess(rows, db, thr=None)`, `fix_overlaps(rows)`, `coverage_mask(rows, n)`, `text_of(rows)` | building blocks |
| `get_model(name, device)`, `unload_models()` | model cache (worker keeps it warm) |

Cache document `<stem>_words.json`:

```json
{"v": 3, "model": "large-v3-turbo", "lang": "id", "hash": "<file_hash>", "media": "C:\\...mp4", "duration": 2076.742,
 "created": "...+07:00", "device": "cuda", "secs": 70.7, "thr": -53.5, "upgraded_from": null,
 "settings": {"vad": true, "condition_on_previous_text": false, "word_timestamps": true},
 "words": [[" Baik,", 2.22, 2.86, 0, 0.91], ...],
 "issues": [{"t0": 1954.56, "t1": 1971.55, "text": "Terima kasih telah menonton.", "kind": "phrase"}],
 "destretch": {"moved": 176}, "repair": {"checked": 24, "added": 0, "spans": [[t0, t1, n_added, "text"]]},
 "segs": [[t0, t1, avg_logprob, no_speech_prob, compression_ratio], ...]}
```

`issues` tells captions/repeat tools where Whisper invented text (useful for "cek di sini" markers). `segs`
gives per-segment confidence. `seg_end=1` is Whisper's phrase end (caption chunking cue).

### 4.5 `ac.timeline`

`Timeline.from_json(seq)` (SPEC section 3; also accepts tlSeqInfo keys `idx/media/inPoint/outPoint/endSec/inSec/
outSec/playerSec`, `-400000` = unset; extra panel keys such as `selection`, `active`, `displayFormat`, clip
`mgt/nested` stay in `tl.raw` / `clip.raw`), `Timeline.from_file(path)`, `Timeline.from_media(path)` (one clip on V1+A1;
use it for offline tests). Attributes: `id, name, fps, width, height, duration, in_point, out_point, player,
markers, video, audio` (lists of `Track{kind, index, name, muted, locked, clips}`). `markers` is the host list as is:
`[{name, comments, start, end, type, color, guid}]` (sequence seconds; `bac_seqInfo('full')`).

`Clip{kind, track, index, name, path, start, end, src_in, src_out, speed, disabled, selected, node_id, raw}`
with `.dur`, `.has_media`, `.seq_to_src(t)`, `.src_to_seq(s)`, `.contains(t)` (speed-aware).

| Method | Notes |
|---|---|
| `clips(kind=None, tracks=None, include_muted=True, include_disabled=False)` | track order, then time |
| `audio_clips(include_muted=False, tracks=None, include_disabled=False)` | what is heard (media clips only) |
| `video_clips(tracks=None, include_disabled=False)` | |
| `clip_at(t, kind="video", tracks=None)` | topmost video clip / lowest audio clip under t |
| `seq_to_src(clip, t)`, `src_to_seq(clip, s)` | also module functions |
| `media_paths(kind="audio")` | unique paths in order |
| `scope_ranges(scope)` | sequence ranges to work on. `scope` = `"all"`, `"inout"`, `"selected"`, None, or the panel's `params.scope` dict `{kind, t0, t1}` (pass it straight through: `tl.scope_ranges(p.get("scope"))`; t0/t1 win over the sequence In/Out; `selected` = selected clips, else the host `selection` list); falls back to all |
| `words_on_timeline(tracks=None, include_muted=False, transcribe=True, emit=None)` | `[{"text","t0","t1","seg","p","id","src","s0","s1","track","clip","clip_end"}]` sorted; word belongs to a clip when its source midpoint is in `[src_in, src_out)`; stereo twins de-duplicated; `transcribe=False` = caches only |
| `envelope_on_timeline(tracks=None, include_muted=False)` | float32 dB in sequence time, `ceil(duration/HOP)` frames, max over included audio clips, -90 where nothing plays |
| `frame(t)`, `to_json()` | |

### 4.6 `ac.ranges` (pure Python, `[[t0, t1]]`, half-open)

`norm/merge(ranges, min_len=0)`, `merge_gaps(ranges, max_gap)`, `union(*lists)`, `intersect(a, b)`,
`subtract(a, b)`, `invert(ranges, lo, hi)`, `clamp(ranges, lo, hi)`, `pad(ranges, before, after, lo, hi)`,
`drop_short(ranges, min_len)`, `total(ranges)`, `contains(ranges, t)`, `overlap(a0, a1, b0, b1)`,
`to_frames(ranges, fps, mode="inner"|"outer"|"nearest")` (inner = shrink removals, outer = grow keeps),
`snap_edges(ranges, fn)` (e.g. `lambda t: M.snap_quiet(db, t, 0.04)`), `ripple_mapper(removed)` (t -> t after
ripple delete: move markers/words), `keep_to_remove`, `remove_to_keep`, `round_list(ranges, nd=3)`.

### 4.7 `ac.review`

`item(t0, t1, kind, on=True, conf=None, label="", ctx=None, note=None, **extra)`, `assign_ids(items, prefix=None)`,
`new(tool, items, duration, seq=None, params=None, stats=None, timebase="sequence", **meta)`, `validate(doc)` ->
errors, `save(doc, path)`, `load(path)` (`BAD_REVIEW`), `from_job(job)`, `path_for(workdir, tool)` ->
`<workdir>\<tool>_review.json`, `selected(doc, kinds=None)`, `selected_ranges(doc, kinds=None)`,
`apply_edits(doc, edits)` (edited doc / items / `{id: bool}`), `carry_over(new_doc, old_doc)`, `summary(doc)`.

### 4.8 `ac.xmeml`

| API | Notes |
|---|---|
| `build_sequence(name, clips, fps, width, height, sample_rate=48000, channels=2, probe=None, markers=None)` | multi-source xmeml string. clip = `{path, start, src_in, src_out}` seconds + optional `video/audio` (bool), `vtrack/atrack` (0-based), `enabled`, `name`, `filters` (ET elements or XML strings on the video clipitem). Frames in the **sequence** rate; each `<file>` defined once; V/A of one clip linked |
| `keep_clips(path, keeps)` | kept source ranges of one file -> butted clips |
| `to_xmeml(path, info, segments, name=None)` | legacy single-source writer (same clip structure as the old autocut.py; verified import layout) |
| `cut(tree, ranges_sec, new_name=None)` / `cut_file(inp, out, ranges_sec, new_name=None)` | remove sequence ranges from a Premiere export (port of an internal lab prototype). Output is **byte-identical** to the Premiere-verified lab outputs (internal fixtures, not published); 300 cuts in ~50 ms. Returns `{fps, removedFrames, newDurationFrames, keeps, warnings, ms}` |
| `write(path, xml)`, `summary(xml)`, `rate_of(el)` | helpers |

Job form (used by `panel/js/core/apply.js` for > 150 ranges):
`{"tool":"xmeml","action":"cut","params":{"xml","out","ranges","name"}}` -> `{"path", ...cut info}`;
`{"tool":"xmeml","action":"build","params":{"out","name","fps","width","height","clips","markers"}}` ->
`{"path","plan":{"kind":"xml_import","path"}}`.

### 4.9 `ac.tools`

`names()`, `get(tool)` (module; `NO_TOOL` / `TOOL_IMPORT`), `action(tool, name)` (`NO_ACTION`), `describe()`.

---

## 5. Caches and files

| What | Where | Key / invalidation |
|---|---|---|
| Envelope | `<media dir>\<stem>_env.npy` + `_env.json` | `file_hash` (path+size+mtime) |
| Words v3 | `<media dir>\<stem>_words.json` | hash + model + lang; v2 files upgraded in place |
| Listener | `<media dir>\<stem>_listen.json` | hash + model + prompt |
| Not writable media folder | `%LOCALAPPDATA%\Klipora\cache\<hash><suffix>` | same |
| AI results | `%LOCALAPPDATA%\Klipora\ai_cache\<key>.json` | task + PROMPT_VERSION + model + params + words; only `source=="ai"` |
| Review / renders / temp | `workdir(job)` (`%USERPROFILE%\Videos\Klipora\<seq>` by default) | tool-defined |
| Logs | `%LOCALAPPDATA%\Klipora\logs\engine.log` | rotating, redacted |
| GPU lock | `%LOCALAPPDATA%\Klipora\gpu.lock` (+ `gpu.owner.json`) | OS byte lock |
| Settings | `%APPDATA%\Klipora\settings.json` (written by the panel) | mtime |

Media files are never modified. Caches next to the media are the established convention (test media
included). Tools needing their own per-media cache use `util.cache_path(media, "_<tool>.json")` and store
`file_hash(media)` inside to detect staleness.

---

## 6. GPU lock

```python
from ..util import gpu_lock
with gpu_lock(emit, label="zoom faces x.mp4"):     # waits, emitting progress notes "Antre GPU (<label of holder>) 12 dtk"
    run_gpu_work()
```

- One Whisper/GPU job at a time across all processes (panel jobs, worker, tests, other agents).
  `transcript.words/listener_words` take it themselves; take it yourself only for other GPU models (ONNX CUDA, NVENC
  bursts are fine without it).
- OS byte-range lock (`msvcrt.locking`): a killed process frees it instantly, no stale lock files.
  Re-entrant within a process. Timeout (default 30 min) -> `GPU_BUSY`. While waiting, cancel works.
- Holder info: `GpuLock.owner()` -> `{"pid","label","since"}` (also in `health.gpu.lock`).

---

## 7. AI (`ac.ai`, optional; local OpenAI-compatible proxy by default, other providers via profiles)

```python
from ..ai import tasks
r = tasks.run("chapters", words, glossary=["TokoKita"], emit=emit)   # words = v3 rows or timeline word dicts
# r = {"source": "ai"|"fallback"|"cache"|"mixed"|"none", "result": {...}, "warnings": [...], "stats": {...}, "model": "grok-fast"}
```

- Tasks (`tasks.TASKS`) and default models: chapters/emphasis/moments/broll/cleanup/meta -> `grok-fast`;
  viral/repeats/filler -> `grok-auto`. Never raises for AI problems: proxy down / quota / bad JSON -> rule
  fallback in < 0.05 s (`emit.warn` explains). With timeline word dicts every output time is sequence time.
- Result shapes are those of `ac.ai.prompts` (see the schemas in `engine/ac/ai/prompts.py`): e.g. chapters -> `{"chapters":[{"start","title","unit"}],"youtube":"00:00 ..."}`.
- Lower level: `from ..ai import client as ai` -> `ai.health()`, `ai.available()`, `ai.chat(messages, ...)`,
  `ai.chat_json(messages, schema=..., check=..., model=..., tag=...)` (raises `ai.AIError` / `AIUnavailable` /
  `AIBadOutput`), `ai.extract_json`, `ai.validate_schema`, `ai.chunk_by_chars`, `ai.map_parallel`, `ai.Cache()`.
  `ac.ai.text`: `display_words`, `sentences`, `caption_lines`, `ts`, `parse_ts`, `norm`.
- Rules: base URL is forced to `127.0.0.1` (localhost costs +2 s); never ask the model for integer
  ids, use `mm:ss` + quotes and `prompts.resolve()`; timestamps always come from Whisper words; AI output is a
  reviewable suggestion; AI runs only on an explicit user action; the key in `<repo>\.env` is never
  printed, logged or returned (`health` only says `has_key`). Settings `ai: false` disables it.
- **Provider profiles (2026-10-05, docs/AI_PROVIDERS.md):** the local proxy (`grok_local`) is now one profile among OpenRouter, Gemini,
  OpenAI, xAI, Anthropic (native Messages API), DeepSeek, Groq, Ollama and custom endpoints, tried in the user's
  order with automatic fallback (then rules). Pass model SLOTS: `model="fast"|"smart"|"vision"` (the local proxy preset's model aliases such as
  `grok-auto` still work and map to the slot on other providers); `tasks.DEFAULT_MODEL` is slot based and
  `tasks.run()` also returns `profile`. Images: `ai.chat_vision(prompt, [path|bytes|data URL], schema=...)` uses the
  route's vision models. Keys: `cli.py ai set-key <id>` (stdin only, DPAPI). `health()` adds `profile` and `route`.

---

## 8. Tests

```
python engine/tests/run_all.py                 # every engine/tests/test_*.py + legacy engine/test_*.py, offline
python engine/tests/run_all.py media timeline  # subset by name
python engine/tests/test_transcript.py --gpu   # + live transcription of a COPY of talk_49s.mp4 (GPU lock)
python engine/tests/test_ai.py --live          # + one real chapters call (spends 2 AI requests)
```

Write `engine/tests/test_<tool>.py` as a plain script with asserts ending in `print("ok")`:

```python
import _common                                   # puts engine/ on sys.path
from _common import MEDIA_49, MEDIA_2M, MEDIA_34M  # test media from %KLIPORA_TEST_MEDIA% (skip with _common.have(...) if missing)
from ac.progress import ListEmitter
from ac.timeline import Timeline
from ac.tools import mytool

tmp = _common.scratch("mytool")                  # %TEMP%\autocut_tests\mytool (fresh)
job = {"tool": "mytool", "action": "analyze", "seq": Timeline.from_media(MEDIA_49).to_json(),
       "params": {}, "workdir": str(tmp)}
em = ListEmitter()
res = mytool.analyze(job, em)
assert res["summary"]["n"] > 0 and "error" not in em.kinds()
_common.cleanup("mytool")
print("ok")
```

Use the cached transcripts (all 3 test media have v3 + listener caches); never re-transcribe in a default test.

---

## 9. Legacy CLIs (kept for the current panel until replaced)

- `engine/autocut.py <media> [--threshold -35] [--min-silence 0.5] [--margin 0.2] [--render]` -> `<stem>_autocut.xml/.json`
  next to the media (unchanged contract; XML via `ac.xmeml.to_xmeml`, same clip structure as before).
- `engine/caption.py clips.json out.srt [--max-chars 32] [--model] [--lang id] [--fix "a = b; c = d"]` ->
  `PROGRESS n` lines + `"<n> caption"`; transcription now uses the v3 cache (`ac.transcript.words`).
  `caption.transcribe()` still returns 4-field rows for old importers.

---

## 10. Measurements (dev machine with an 8 GB RTX-class laptop GPU, 2026-10-05)

| What | Result |
|---|---|
| Envelope, 34.6 min file | decode 1.23 s + RMS 0.20 s (first time, incl. save 1.41 s); cache load 2 ms; 207 671 frames |
| Envelope, 49 s file | 0.09 s; cache 5 ms |
| Words v3, 34.6 min (cold GPU, full pipeline incl. decode, VAD, turbo, post-process, hole repair) | 70.7-75.2 s, 2803 words |
| Words v3, 2 min / 49 s | 3.9 s / 1.8 s warm (6.1-6.5 s cold incl. model load) |
| Listener pass, 34.6 min / 2 min / 49 s | 18.1 s / 1.8 s / 1.8 s (2991 / 199 / 82 words) |
| Old v2 cache, 34.6 min | 2839 words, 57 s hole (94.19 -> 151.66 s), 7x "Terima kasih telah menonton", "Tenggak" loops, 11 zero-length words |
| New main pass (`condition_on_previous_text=False`) | hole gone (>= 30 words in 94-113 s), no outro loop; 176 word starts de-stretched; dropped as hallucination: "ini" x12 loop 1860-1919 s, one "Terima kasih telah menonton." |
| Hole repair on the old v2 transcript | 31 spans checked in 14.2 s, +65 real words: 94-113 s ("Saya akan benerin dulu ... praktekin di sini") and 1995-2012 s; junk spans ("Terima kasih.", "selamat menikmati", garbled background audio) rejected |
| Hole repair on the new pass | 24 uncovered sound spans (background audio after 29 min) checked, 0 junk words added |
| `cli.py tools` / `health` | 0.15 s / ~2 s |
| xml cut, 300 ranges | ~50 ms |

---

## 11. Gotchas

- Use `ac.util.run` / `run_ffmpeg` for subprocesses (no console window, stdin=DEVNULL). The worker also points
  fd 0 at NUL: on Windows a child inheriting a stdin pipe that another thread reads stalls until the next request.
- `emit.progress()` is the cancel point; long loops without it cannot be cancelled in the worker.
- Keep imports of faster_whisper / cv2 / skia inside functions: `cli.py tools` imports every tool module.
- Whisper word spans are gapless and stretched at the start; v3 fixes starts, but never treat raw spans as
  keep ranges (use the envelope + word guard, see product_cutting.md 2.4).
- `" .com"` with a leading space is a spoken word ("titik com"); `".com"` without it was merged.
- Whisper on GPU is not bit-deterministic between runs (different hallucination spots), so tests assert
  properties, not exact word lists.
- Times: never format with a dot in UI text (`fmt_sec`, `.replace(".", ",")`); WIB timestamps (`now_iso`).
