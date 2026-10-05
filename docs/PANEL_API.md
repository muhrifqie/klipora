# Klipora panel: API for tool agents

Owner: panel foundation. Source of truth for everything under `panel/` except `panel/js/tools/<tool>.js` bodies and
`panel/host/<tool>.jsx`. Architecture and contracts: `docs/SPEC.md`. Design: an internal UX study and approved mockup (not
published); the shipped `panel/css/base.css` tokens are the reference. Engine side: `docs/ENGINE_API.md` (engine foundation).

Status: everything below is implemented and covered by headless tests (`tools/ui_tests/foundation.mjs`, 66 checks;
`tools/ui_tests/components.mjs`, 35 checks; both pass with the real engine and with the mock) plus one live read-only
check in Premiere 26.2.2 (section 16). Items marked UNVERIFIED have not been run inside Premiere.

---

## 1. Ground rules

| Rule | Why |
|---|---|
| Classic scripts only, each file one IIFE, only `window.AC` is shared | CEP 12 has globals `cep`, `require`, `process`, `module`; ES modules are not used |
| Chrome 99 (CEP 12): no `:has()`, CSS nesting, container queries, `color-mix()`, `dvh`, `text-wrap`, `structuredClone`, `.at()`, `replaceAll`, `navigator.clipboard`, `popover` | `node tools/compat_check.mjs` enforces it (also runs first in every `ui_test`) |
| ExtendScript (`panel/host/*.jsx`) is ES3: no `let/const`, arrows, template strings, Array extras (`forEach/map/filter/indexOf` on arrays), `JSON`, trailing commas | Same checker, plus a parse check |
| Never print/store the AI key. It lives only in `<repo>\.env` (or the encrypted key store, see `docs/AI_PROVIDERS.md`) and only the engine reads it | SPEC rule 4 |
| Never save the project; cutting tools work on a clone; check `new File(p).exists` before any import | SPEC rules 1-3 |
| UI copy in Bahasa Indonesia AND English through `AC.t` (sections 12, 17), code/comments/identifiers in English | SPEC rule 8; `node tools/i18n_check.mjs` |
| Write panel code in ES5 style (`var`, `function`), like the foundation | Consistency, Chrome 99 safety |
| Do not touch files you do not own. Need a foundation change? Describe it in your report (`core_changes`) | Many agents work in parallel |

Files you own as the `<tool>` agent: `panel/js/tools/<tool>.js`, `panel/css/tools/<tool>.css`, optional
`panel/host/<tool>.jsx`, `tools/ui_tests/<tool>.mjs`. Tool ids (= engine tool ids): `silence fillers repeat profanity
captions chapters viral zoom resize angles podcast broll` (dev: `echo`, engine-only: `xmeml`).

---

## 2. Load order and file map

`panel/index.html` loads, in this order: `css/base.css`, `css/tools/*.css`, then

```
js/core/00_ac.js     AC namespace, AC.util, AC.Emitter, AC.bus, AC.Task, AC.store, AC.log
js/core/sys.js       AC.sys: the only Node user (fs, child_process, paths)
js/core/bridge.js    AC.host: evalScript bridge + host/*.jsx loader
js/core/settings.js  AC.settings (%APPDATA%\Klipora\settings.json)
js/core/i18n.js      AC.i18n + AC.t: UI language (panel/locales/{id,en}.json), section 17
js/core/history.js   AC.history (Riwayat, %APPDATA%\Klipora\history.json)
js/core/engine.js    AC.engine: job runner, worker, health
js/core/seq.js       AC.seq: active sequence (events / polling)
js/core/theme.js     AC.theme: Premiere brightness, density, reduced motion
js/core/keys.js      AC.keys: CEP key interest + global shortcuts
js/core/ui.js        AC.ui: DOM helpers + components + dock + toasts + source card
js/core/canvas.js    AC.ui.ribbon (cut map), AC.ui.waveform
js/core/review.js    AC.ui.reviewList (virtualized Tinjau list), AC.ui.loadReview
js/core/jobview.js   AC.ui.progressView, resultCard, errorView
js/core/apply.js     AC.apply: removeRanges / markers / plan
js/core/tools.js     AC.tools: registry, tool page runtime, ctx + job flow
js/core/router.js    AC.router: routes, app bar, sibling tabs, dock switching
js/core/palette.js   AC.palette (Ctrl+K), shortcut sheet, AC.recipes (placeholders)
js/pages/home.js, settings_page.js, history_page.js
js/tools/<12 tools>.js, js/tools/echo.js
js/main.js           boot (sets AC.ready / AC.readyState)
```

Host: `panel/host/*.jsx` are `$.evalFile`d in name order on every panel load (`00_util`, `05_bridge`, `10_timeline`,
`20_graphics`, then `<tool>.jsx` files, which sort after the digits). The manifest `ScriptPath` only runs once per
Premiere session, so never rely on it.

---

## 3. Writing a tool (quick start)

`panel/js/tools/echo.js` is the complete reference (presets, sliders, switches, radio cards, run, review, apply,
result, dry run). Minimal shape:

```js
(function () {
  'use strict';
  var AC = window.AC, U = AC.util, ui = AC.ui;
  AC.tools.register({
    id: 'silence', group: 'potong', order: 1,                 // keep the stub's id/group/order/title/tab/icon/desc
    title: 'Potong Silence', tab: 'Silence', icon: 'wave',
    desc: 'Buang jeda diam, sisakan napas secukupnya.', badges: [],
    lead: '...', cta: 'Deteksi jeda',
    defaults: { preset: 'normal', th: -35 },                  // initial ctx.state (persisted per viewer)
    review: { unit: 'jeda' },                                 // defaults for the automatic review pane
    next: [{ tool: 'fillers', reason: 'Transkrip sudah ada, tinggal cek AI' }],   // "Lanjut ke" on the result card
    render: function (el, ctx) {                              // called ONCE, first time the page opens
      var sec = ui.section({ title: 'Deteksi' });
      var th = ui.slider({ label: 'Batas diam', min: -60, max: -15, value: ctx.state.th, unit: ' dB', decimals: 0,
        help: 'Lebih kecil = suara pelan tetap dianggap bicara',
        onInput: function (v) { ctx.state.th = v; ctx.save(); } });
      sec.add(th); el.appendChild(sec.el);
      ctx.dock({ primary: { label: 'Deteksi jeda', onClick: function () {
        ctx.run({ action: 'analyze', title: 'Mendeteksi jeda', params: { threshold: ctx.state.th } });
        // default: result.review -> Tinjau pane; Terapkan -> engine "apply" -> plan -> applied -> result card
      } } });
    },
    onShow: function (ctx) { /* every time the main pane becomes visible */ },
    onSeq: function (lite, ctx) { /* active sequence changed */ }
  });
})();
```

The framework mounts, above your `el`, a source card with the scope selector (Seluruh sequence | In/Out | Clip terpilih).
`ctx.run()` adds `params.scope` automatically; engine tools pass it straight to `Timeline.scope_ranges(params.get("scope"))`.

---

## 4. `AC.tools.register(def)`

| Field | Type | Meaning |
|---|---|---|
| `id` | string, required | Route `#tool/<id>`, engine tool id (unless `engineTool`), CSS file `css/tools/<id>.css` |
| `group`, `order` | `'potong'|'teks'|'kamera'|'publikasi'|'dev'`, number | Home group + sibling tab order |
| `title`, `tab`, `icon`, `desc` | strings | App bar title, sibling tab label, sprite icon name (`i-<icon>`), Home one-liner |
| `badges` | `['ai','baru','gpu','beta','dev']` | Shown on Home and in the placeholder hero |
| `lead`, `cta` | strings | Hero sentence and primary label (used by `AC.tools.placeholder`) |
| `hidden` | bool | Only visible with Settings > Developer > Mode developer (echo) |
| `source` | `true` (default) / `false` / `{scope, scopes, ribbon, tags}` | Source card above the body. `scopes`: subset of `['all','inout','selected']` |
| `engineTool` | string | Engine tool id when it differs from `id` |
| `defaults` | object | First value of `ctx.state` |
| `review` | object | Default `ctx.review()` options when `ctx.run` uses the default result handler |
| `next` | `[{tool, reason}]` | Next steps on the default remove result card |
| `render(el, ctx)` | fn | Build the main pane once. Throwing shows an error alert instead of the page |
| `onShow(ctx)` / `onHide(ctx)` | fn | Main pane shown / left |
| `onSeq(lite, ctx)` | fn | `AC.seq` 'change' while mounted (lite info, section 7) |
| `onScope(scope, ctx)` | fn | Source card scope changed |
| `onKey(e, ctx)` | fn -> bool | Keys on the main pane after the global ones (Ctrl+K, Ctrl+Enter, Esc). Return true when handled. Check `AC.keys.typing()` before handling letters |

Other registry calls: `AC.tools.get(id)`, `AC.tools.list()`, `AC.tools.siblings(id)`, `AC.tools.runtime(id)` (tests:
`{ctx, pane, task, review, panes, ...}`), `AC.tools.placeholder` (the "Sedang dibangun" render).

---

## 5. `ctx` (second argument of `render`)

| Member | Meaning |
|---|---|
| `ctx.el` | Your body container (main pane, below the source card) |
| `ctx.source` | Source card controller: `scope()` -> `{kind:'all'|'inout'|'selected', t0, t1}`, `setTags(html)`, `refresh()` |
| `ctx.scope()` | Same as `ctx.source.scope()` (`{kind:'all'}` without a source card) |
| `ctx.state`, `ctx.save()` | Per-viewer persisted state (localStorage `ac.tool.<id>`). Use for last params only |
| `ctx.sub` | Sub route (`#tool/captions/teks` -> `'teks'`) for tools with internal tabs; `ctx.go(sub)` navigates |
| `ctx.dock(spec)` | Main-pane dock (section 9). Returns `{update(patch)}`; also `ctx.setPrimary({label, disabled})` |
| `ctx.run(opts)` | Engine job with progress pane (below). Returns the Job |
| `ctx.review(opts)` | Show the Tinjau pane (section 9.6). Returns the list controller (`ctx.reviewList`) |
| `ctx.applyRemove(ranges, opts)` | Cut sequence seconds into a new "(Klipora)" sequence with progress + default result card |
| `ctx.applyPlan(plan, opts)` | Apply an engine plan (`remove_ranges` -> applyRemove, `markers`, `xml_import`) |
| `ctx.removeResult(res, ranges, opts)` | The default result-card options for a remove result (customize, then `ctx.result`) |
| `ctx.track(task, opts)` | Progress pane for an `AC.Task` the tool drives itself (host work, e.g. keyframes on a clone); `opts {title, note, onDone(res, task), onRetry}`; errors/cancel go to the error pane / toast like `ctx.run`. Returns the task |
| `ctx.result(opts)` | Result pane (section 9.7); dock = "Lanjut ke <next>" or "Selesai" unless `opts.primary` |
| `ctx.error(err, {onRetry, actions})` | Error pane (real message + log + "Salin detail") |
| `ctx.back()` | Back to the main pane. `ctx.job()` current Job, `ctx.pane()`, `ctx.isActive()` (tool on screen) |
| `ctx.seq(opts)` | `AC.seq.current(opts)`: full Timeline JSON |
| `ctx.toast(msg, opts)` | `AC.ui.toast` |

### 5.1 `ctx.run(opts)`

```js
ctx.run({
  action: 'analyze',               // engine action
  params: { ... },                 // tool params; params.scope is added from the source card
  title: 'Mendeteksi jeda',        // progress title + history sub line
  stages: [{id:'audio', label:'Ambil audio', w:0.2}, ...],   // optional: show the plan before the engine announces it
  seq: undefined,                  // undefined = current full Timeline JSON (job fails with NO_SEQ if none); null = no seq
  review: 'C:\\...\\x_review.json',// for apply actions (job.review)
  workdir: undefined,              // default <workRoot>\<safe seq name>
  worker: false,                   // true = engine/worker.py (models stay warm; previews)
  onResult: function (data, job) {},  // default: data.review -> ctx.review(def.review), data.plan -> ctx.applyPlan, else result card
  onError: function (err, job) {}     // return false to fall through to the error pane
});
```

Lifecycle and routes (one job per tool; a second click shows "Masih berjalan"):

```
#tool/<id>            main pane (your UI)
#tool/<id>/progress   stages, %, ETA; dock: "Jalankan di latar" | "Batalkan Esc"; Esc cancels (taskkill /T /F)
#tool/<id>/review     Tinjau list; dock: "Batal" | primary "<label(n)>"
#tool/<id>/result     result card; dock: "Lanjut ke ..." or "Selesai"
#tool/<id>/error      error alert; dock: "Kembali" | "Coba lagi"
```

When the user leaves the tool while it runs ("Jalankan di latar", or navigates away), the clock icon shows a progress
ring, and when the job ends a toast "<Tool> selesai" with "Lihat" opens the pending pane. Cancelled jobs toast
"Dibatalkan. Tidak ada yang berubah." and return to the main pane.

---

## 6. `AC.engine` (job runner, SPEC section 2)

`AC.engine.run(opts) -> Job`. Same options as `ctx.run` plus `tool` (required), `record` (default true = Riwayat),
`id`. What it does:

1. Builds `{id, tool, action, seq, params, workdir, review?}` (id `<tool>-YYYYMMDD-HHMMSS-<n>`, WIB) and writes it to
   `%TEMP%\Klipora\jobs\<id>.json` (files older than 2 days are deleted at boot).
2. Spawns `<settings.python> -X utf8 engine/cli.py run <job.json>` (cwd `engine/`, `PYTHONIOENCODING=utf-8`,
   `PYTHONUNBUFFERED=1`, no console window).
3. Parses stdout JSON lines (`stage`, `progress` (`pct`, `all`, `note`), `stage_done`, `log`, `warn`, `result`,
   `error`); legacy `PROGRESS n` lines count as progress; anything else and stderr go to the job log.
4. Resolves with `result.data` on exit 0; rejects with an Error `{code, msg, hint, log}` (`code` from the engine
   error event, or `PYTHON` (spawn ENOENT), `EXIT`, `NO_RESULT`, `NO_ENGINE`, `NO_SEQ`, `CANCELLED`).
5. `job.cancel()` runs `taskkill /pid <pid> /T /F` (kills ffmpeg children too). Panel reload/close kills running jobs.

Job (= `AC.Task`) fields: `id, tool, action, title, label, state ('queued'|'running'|'done'|'error'|'cancelled'),
stages [{id,label,w,sub,state:'wait'|'run'|'done'|'err',pct,sec,note}], cur, pct (0..100 overall), note, logLines,
warnings, result, error, started, ended, pid, job (the JSON), jobPath, summary`. Events: `stage, progress, stage_done,
log, warn, state, result, error, end`. Promise: `job.promise`, also `job.then(...)`.
Setting `job.summary` (string) before it ends changes the Riwayat line; otherwise `data.summary` from the engine is
used automatically (a string as is, or the `review.summary()` dict `{n, on, sec_on}` -> "12 ditemukan, 9 dipilih (31,4 dtk)",
`AC.engine.summaryText`).

`AC.engine.worker(req) -> Job`: same request shape; sent as `{"id": n, "job": {...}}` to one persistent
`engine/worker.py` (spawned on first use, auto-restarted up to 3 times per minute after a crash, killed on panel
unload). Cancel sends `{"id": m, "cmd": "cancel", "target": n}`. Falls back to `run()` when `worker.py` is missing.
Use it for fast repeated calls (previews); long user jobs should use `run()` (own process = clean cancel).

Others: `AC.engine.health()` (runs `cli.py health`, caches `AC.engine.lastHealth = {ok, ms, data, issues, ai: {ok,
ms, model, hasKey, base, disabled, msg}}`, emits `AC.bus 'health'`), `AC.engine.cli(args)`, `AC.engine.pythonVersion()`,
`AC.engine.available()`, `AC.engine.jobs` (running).

---

## 7. `AC.seq` (active sequence)

| Call | Returns |
|---|---|
| `AC.seq.peek()` | Last lite info (sync, may be `null`) |
| `AC.seq.current({force})` | Promise of the full Timeline JSON (SPEC section 3), cached until Premiere reports a change |
| `AC.seq.refresh()` | Re-read lite info now |
| `AC.seq.on('change', fn(lite, prev))` | Target sequence or its header/selection/track counts/track mute or lock flags changed. Also `'lock'`, `'player'` |
| `AC.seq.lock(bool)` | Keep the current sequence as target ("Kunci sumber") |
| `AC.seq.hold(promise)` | Pause refreshes while host-heavy work runs (AC.apply does this) |
| `AC.seq.mode` | `'events'` (host `tlBindEvents` -> CSXS `com.klipora.ev`, debounced 250 ms) or `'poll'` (2 s, only when events are unavailable) |
| `AC.seq.mainTrack(s)`, `mediaPaths(s)`, `hasTranscript(s)`, `scopeRange(scope, s)` | Helpers |

Full Timeline JSON (`bac_seqInfo('full')`): `{id, name, fps, timebase (ticks/frame string), width, height, displayFormat,
duration, inPoint|null, outPoint|null, player, active, level:'full', video:[{index, name, muted, locked, targeted, count,
clips:[{name, path|null, start, end, in, out, speed, disabled, selected, mgt, nested, nodeId}]}], audio:[...],
markers:[{name, comments, start, end, type, color, guid}], selection:[{name, kind, track, start, end, nodeId}],
selectedCount, ms}`. All times in sequence seconds. `path` is null for graphics and nested sequences.
Lite (`'lite'`): same header, tracks without `clips` (only `count`), `spans` ([start,end] of the main track clips),
`nMarkers`, `selectedCount`.

---

## 8. `AC.host` and host functions

```js
AC.host.call('bac_seek', 12.5)            // Promise<string>
AC.host.json('bac_seqInfo', 'full')       // Promise<object|null>
AC.host.exec('bac_removeRanges', [id, ranges, 0, 25], { json: true, timeout: 180000 })
AC.host.eval('app.version', 5000)         // raw evalScript, no ERR handling (dev only)
```

Arguments are encoded as JS literals with `JSON.stringify` (objects and arrays arrive as ES3 literals, U+2028/2029
escaped). Results: `"ERR:<msg>"` rejects with `code 'HOST'` and `msg`; `"EvalScript error."` (function threw or does not
exist) rejects `HOST_EVAL`; timeout (default 20 s) rejects `HOST_TIMEOUT` ("Mungkin ada dialog yang terbuka di
Premiere"). Calls wait for the host loader. ExtendScript is single threaded: long calls block Premiere's UI, so chunk them.

### 8.1 Foundation host API (`panel/host/05_bridge.jsx`)

| Function | Returns (JSON) | Notes |
|---|---|---|
| `bac_ping()` | `{ok, version, project, helpers}` | |
| `bac_seqInfo(level, seqId?)` | Timeline JSON or `null` | `level` `'lite'` or `'full'` (default). `seqId` = locked target |
| `bac_activeSeqId()` | `{id, name}` or `null` | |
| `bac_openSequence(id)` | `{ok, id, name}` | "Buka yang asli" |
| `bac_seek(sec, seqId?)` | `{ok, t}` | Frame-aligned `setPlayerPosition` (verified live 26.2.2) |
| `bac_addMarkers(list, seqId?)` | `{ok, n}` | `list`: `[{t, end?, name, comment, tag, color 0..7, type 'Comment'|'Chapter'|'Segmentation'}]` or tab lines `start\tend\tname\tcomment`. The tag is appended to the comment |
| `bac_clearMarkersByTag(tag, seqId?)` | `{ok, n}` | Deletes markers whose name or comment contains the tag |
| `bac_cloneSeq(name, seqId?)` | `{ok, id, name, origId, origName}` | Unique "<name> (Klipora)" / "(Klipora 2)" |
| `bac_removeRanges(seqId, ranges, from, to)` | `{ok, ranges, done, ms, endSec}` | QE extract of merged ranges `[from,to)`; call LAST chunk first |
| `bac_applyRemove(ranges, name, opts)` | extract: `{mode:'extract', id, name, origId, ...}`; xml: `{mode:'xml', xmlPath, lost}` | All-in-one (SPEC 4). `opts {seqId, mode 'auto'|'extract'|'xml', maxExtract 150, xmlPath}` |
| `bac_exportXml(path, seqId?)` | `{ok, path, lost, ms}` | FCP7 XML + loss detection |
| `bac_importXmlAndOpen(path, name?)` | `{ok, id, name, created}` | Only existing `.xml` files. The new sequence gets a unique name like `bac_cloneSeq` ("(Klipora 2)"; verified live) |
| `bac_deleteSequence(id)` | `{ok}` | Only sequences created by bac_* in this Premiere session (verified live: deletes, closes its tab; refuses others) |
| `bac_exportFrame(sec, path, seqId?)` | `{ok, tc, path, exists}` | QE exportFramePNG; caption tracks not drawn |
| `bac_bindEvents(type?)` / `bac_unbindEvents()` | `{ok, n}` | Used by AC.seq |

Also available to every host file (ported unchanged from the lab, verified live): all `tl*` helpers in
`10_timeline.jsx` (`tlRemoveRanges`, `tlCloneSequence`, `tlTimecode`, `tlExportXml`, `tlImportXml`, `tlAddMarker`,
`tlReadMarkers`, `tlClearMarkers`, `tlZoomKeys`, `tlZoomPos`, `tlSeqToMedia`, `tlDuck`, `tlDbToLevel`, `tlAddAudioTrack`,
`tlAddVideoTrack`, `tlPlaceClip`, `tlExportFrame`, `tlSetFrame`, `tlAutoReframe`, `tlReframeDone`, `tlSeqInfo`,
`tlBindEvents`, ...), all `gx*` helpers in `20_graphics.jsx` (`gxPlaceOverlay`, `gxRelinkOverlay`, `gxEnsureVideoTrack`,
`gxInsertMogrt`, `gxPopIn`, `gxCaptionTrackFromSrt`, ...) and `00_util.jsx` (`tlJSON`, `tlImportFile`, `tlFindBin`,
`tlSeqById`, `TL_TICKS`, `TL_BS`, `acOk`, `acErr`, `acCatch`, `acSeq`, `acRanges`, `acUniqueSeqName`, `acMarkMade`).
Their behaviour was verified live in Premiere (the research notes behind that are internal and not published).

### 8.2 Your own host file `panel/host/<tool>.jsx`

```js
// <tool>.jsx: ES3. Prefix every function with bac_<tool>_ (e.g. bac_zoom_applyKeys). Return acOk(obj) / acErr(msg).
function bac_zoom_applyKeys(seqId, items) {
    try {
        var seq = acSeq(seqId);
        if (!seq) return acErr("Buka sequence dulu.");
        // ... use tl* / gx* helpers; never app.project.save(); File.exists before importFiles ...
        return acOk({ ok: true, n: items.length });
    } catch (e) { return acCatch(e, "bac_zoom_applyKeys"); }
}
```

It is loaded on the next panel load (`node tools/reload.mjs` live, or Settings > Developer > "Muat ulang host").

---

## 9. Components (`AC.ui`)

Every component returns a controller `{el, get(), set(v, silent), ...}`; append `ctrl.el` (or pass the controller to
`section.add()` / `ui.append()`, which accept controllers). CSS lives in `css/base.css`.

### 9.1 Layout and helpers
- `ui.h(tag, attrs, children)` DOM builder (`class`, `text`, `html`, `on: {click}`, `style: {}`, `dataset`, aria-*).
  `ui.append(el, children)`, `ui.html(str)`, `ui.icon(name, cls)` (sprite `#i-<name>`), `ui.addIcons(symbolsHtml)`.
- `ui.section({title, aside, action: {label, onClick}, help})` -> `{el, add(c), setAside(t)}`: hairline heading section.
- `ui.advanced({title='Pengaturan lanjutan', key, open, aside})` -> `{el, body, add, setAside}`: collapsible `<details>`;
  `key` remembers the open state. Show "Custom" with `setAside('Custom')` after manual edits.
- `ui.inlineField({label, help, control})`, `ui.field({label, help, control})`, `ui.tag(text, kind, icon)`
  (kinds `ok warn err ai line`), `ui.badge('ai'|'baru'|'gpu'|'beta'|'dev')`, `ui.fps(n)`.
- `ui.button({label, icon, kind: ''|'primary'|'ghost'|'danger'|'ghost-danger', small, kbd, onClick, id, title, disabled})`.
- `ui.confirmClick(button, fn, ask)`: destructive actions need a second click ("Yakin? Klik lagi").

### 9.2 Inputs
- `ui.segmented({options, value, onChange, small, ariaLabel, width})` -> `{get, set, disable(v, bool, title)}`.
  `options`: `[{value, label, icon, disabled, title}]`, `[[value, label]]` or plain values. Arrow keys move.
- `ui.presets({options: [{value, label, hint, values}], value, onChange(v, preset), customHint})` ->
  `{get, set, match(vals), sync(vals)}`. After a manual change call `sync(currentValues)`: it selects the matching
  preset or shows the "Custom" hint and clears the selection.
- `ui.slider({label, min, max, step, value, unit, decimals, format(v), help, link: {label, onClick}, onInput, onChange})`
  -> `{input, get, set}`. Value shown in accent with decimal comma ("0,50 dtk").
- `ui.stepper({label, min, max, step, value, unit, decimals, onChange})`.
- `ui.switch({label, help, checked, badge (html), disabled, onChange})` -> `{input, get, set}`: real checkbox with
  `role="switch"`, keyboard focusable.
- `ui.radioCards({options: [{value, title, desc, tag, tagKind}], value, onChange})`: first option = the safe one.
- `ui.chips({options, value, multi, onChange, add: {label, onAdd}})`, `ui.select({options, value, onChange, width})`
  (+ `setOptions`), `ui.input({value, placeholder, type, mono, onInput, onChange})`.

### 9.3 Source card
`ui.sourceCard({scope, scopes, ribbon, tags, onScope})` (tools get one automatically, see `def.source`). Shows the
target sequence (name, duration, clip count, size, fps), "Kunci sumber" lock, optional cut ribbon of the main track,
tags, and the scope selector. In/Out and Clip terpilih are disabled with a reason when not available.
`scope()` -> `{kind:'all', t0:0, t1:duration}` | `{kind:'inout', t0, t1}` | `{kind:'selected'}` (the engine reads
`job.seq.selection`).

### 9.4 Dock (sticky bottom bar)
`ctx.dock({primary: {label, icon, onClick, disabled, kbd: true, title}, left: [buttonOpts], right: [buttonOpts |
{iconOnly: true, icon, label, onClick}], grow: bool})`. Exactly one primary per screen, label = verb + count + noun
("Potong 16 jeda"); it is `#primary`, so Ctrl+Enter clicks it. Live updates: `ctx.setPrimary({label, disabled})`.

### 9.5 Canvas
- `ui.ribbon({size: 'sm'|'lg', label, onClick(t)})` -> `{el, set({total, spans, edges, playhead}), redraw}`.
  `spans`: `[[t0,t1]]` or `[{t0, t1, off, sel}]` (off = outlined = kept, sel = ringed). Large = hatched. Canvas-based,
  fine for thousands of spans; `aria-label` summarises the cut.
- `ui.waveform({env: [dB per step], dur, threshold, min=-70, max=-12, thMin=-60, thMax=-15, cuts: fn(th) -> spans,
  onThreshold(db), legend, tip})` -> `{el, canvas, set({env, dur, legend}), setThreshold(db), setCuts(spans), threshold()}`.
  Drag vertically (pointer capture) or ArrowUp/ArrowDown (Shift = 5 dB) on the focused canvas. Downsample long
  envelopes for drawing only if you need to; the draw loop bins per 2 px.

### 9.6 Review list (Tinjau)
`ctx.review(opts)` (pane + dock) or `ui.reviewList(opts)` (component). Options:

| Option | Default | Meaning |
|---|---|---|
| `file` / `doc` | | Review file path (engine output) or the doc object. Shape: `{tool, timebase, duration, items:[{id, t0, t1, kind, on, conf, label, ctx:{pre,post}|string, note:string|{type:'guard'|'ai'|'warn'|'info', text, words}, src, ...}]}` |
| `unit`, `verbOn`, `verbOff` | `'bagian'`, `'dibuang'`, `'disimpan'` | Copy: "16 jeda dibuang dari 16 ditemukan" |
| `filters` | Semua / Dibuang / Disimpan | Extra counted chips `[{id, label, icon, test(item)}]` |
| `ctx(item)`, `note(item)`, `tags(item)` | built in | HTML for context (`pre <mark>label</mark> post`), `{text, kind:'ok'|'warn', icon}` note, tags (src + conf badge) |
| `seekTime(item)` | `item.t0` | Sequence seconds for `bac_seek` (review timebase must be `sequence`) |
| `seek` | true | false = no Premiere seek (tests) |
| `rowActions` | | Extra buttons on the active row `[{label, icon, run(item, list)}]` |
| `bulkAction` | "Kirim ke marker" | `{label, run(list)}` or `null`. Default adds red markers tagged `markerTag` (default `[Klipora-RV]`, replaced on re-run) |
| `primary` | `{label: fn(nOn, nAll, sec) | string, onApply(items, doc, file, list)}` | Dock primary. Default onApply runs the engine `apply` action with `review: file` |
| `title(nOn, nAll, sec)` | | Custom heading HTML |

Behaviour: virtualized rows (only visible rows in the DOM; verified with 3000), click row = select + seek, checkbox =
toggle only, Up/Down or J/K move + seek, Space toggles, Enter seeks, A toggles all visible, Home/End/PageUp/PageDown,
"Pilih semua (n/m)" works on the filtered rows, cut ribbon on top (click = nearest item). Before `onApply` the edited doc
is written back to `file` (only `on` flags change; changed rows get `touched: true` for the engine's `carry_over`). Controller: `items(), selected(), ranges(), count(), toggle(i, v),
setAll(v), setActive(i, seek), setFilter(id), save(path), refresh(), focus()`.

### 9.7 Result, progress, errors, empty states, toasts
- `ctx.result({title, sub, stats: {before, after} | statsList: [{label, value, accent}], ribbon: {total, spans},
  legend: ['15 bagian disimpan', '16 jeda dibuang, 17,7 dtk'], warn: {title, text}, original: {id, name},
  onEdit, onDelete, actions: [{label, icon, onClick, kind}], next: [{tool, reason}], extra: element, primary})`.
  `original` adds "Buka yang asli" (`bac_openSequence`), `onEdit` adds "Ubah pilihan", `onDelete` adds "Hapus hasil"
  (two-step confirm).
- `ui.progressView(task, {title, note})` (used by ctx.run/applyRemove), `ui.errorView(err, {onRetry, actions, title})`,
  `ui.errorTitle(err)`, `ui.errorDetail(err)`.
- `ui.alert({kind: 'err'|'warn'|'info', icon, title, text, log, actions})`: inline alerts (errors are never toasts).
- `ui.empty({icon, title, text, steps: [], action: {label, icon, onClick}})`.
- `ui.toast(msg, {icon, kind: ''|'ok'|'err', action: {label, run}, ms})`: one at a time, 2,6 s (6 s with an action).
  `ui.copy(text, what)` copies (CEP-safe) and toasts.

---

## 10. Applying results (`AC.apply`)

`AC.apply.removeRanges(ranges, opts) -> Task` (sequence seconds; resolves
`{mode:'extract'|'xml', id, name, origId, origName, count, removed, before, after, lost}`). Options: `name`
(default "<seq> (Klipora)"), `seqId`, `mode` (`'auto'`, `'extract'`, `'xml'`), `maxExtract` (150), `chunk` (25),
`tool`, `title`, `record`. Ranges are merged in frame space exactly like the host's `tlMergeRanges`.

- <= 150 merged ranges: `bac_cloneSeq` then `bac_removeRanges` in chunks of 25 from the end (progress per chunk). The
  original sequence is never touched. Cancel stops between chunks (the clone stays partly cut; the message says so).
- \> 150: `bac_exportXml` to `<workdir>\xml\cut_<stamp>_in.xml`; if Premiere reports lost effects it falls back to
  extract; else engine job `{tool: "xmeml", action: "cut", seq: null, params: {xml, out, ranges, name}}` ->
  result `{path, ...}` -> `bac_importXmlAndOpen(path, name)`. Any XML-route failure falls back to extract (logged as a
  warning). Verified end to end against the real engine `ac/tools/xmeml.py` in the harness.

`AC.apply.markers(list, {tag, replace: true, seqId})` (chunks of 200), `AC.apply.plan(plan, opts)` for `remove_ranges`,
`markers`, `xml_import`. Other plan kinds (`keyframes`, `overlay`, `tracks`) are applied by the tool through its own
`host/<tool>.jsx` (reject code `UNSUPPORTED_PLAN`).

---

## 11. Other modules

- `AC.settings`: `get(k)`, `set(k, v)` (writes `%APPDATA%\Klipora\settings.json` at once, emits `'change'`),
  `all()`, `reset()`, `workRoot()`, `workdir(seqName)`, `path()`, `on('change', fn(k, v))`. Keys (shared with
  `engine/ac/util.py`): `python, workRoot, gpu, whisperModel, lang, ai, aiModel, captionTemplate, outputDir, density,
  followTheme, reduceMotion, developer, uiLang` (`auto` | `id` | `en`; `lang` is the SPEECH language). Secret `pexelsKey` is write-only: `get()` returns undefined; use
  `hasSecret('pexelsKey')`, `setSecret('pexelsKey', v)`; the engine reads it from the same file. Never put it in the DOM.
- `AC.aiSettings` (`js/core/ai_settings.js` + `css/ai_settings.css`, loaded after `palette.js`): Pengaturan > AI >
  Penyedia AI (profiles, write-only keys, order, model picker, tests). `mount()`, `refresh(probe)`, `cli(args, stdin,
  ms)` (`cli.py ai ...`), `idle()`, `state()`. The settings page mounts it in the AI section and calls `refresh()` on
  show. Contract: docs/AI_PROVIDERS.md section 2.6. Test: `tools/ui_tests/ai_settings.mjs`.
- `AC.brandKit` (`js/tools/captions_brand.js`, after `captions_export.js`): Brand Kit store + editor sheet + "Tiru gaya
  dari gambar". `load(force)`, `save(kit, activate)`, `remove(id)`, `activate(id)`, `active()`, `apply(id)` (restyles
  the open captions doc through `AC.cap.op`), `open({id, fromGaya, fromSettings})` / `close()` / `isOpen()`,
  `settingsRow()` (mounted by the settings page in the Caption section), `fonts()`, `state()` for tests. It mounts
  itself into the caption editor's Template and Gaya tabs. Engine contract: docs/CAPTIONS_API.md section 12.
- `AC.store.get/set(key, value)`: localStorage (per-viewer conveniences only; can be empty).
- `AC.history`: `track(task)`, `list()`, `recent(n)`, `running()`, `clear()`, `on('change')`. Every `ctx.run` and
  `applyRemove` is tracked; Riwayat shows live jobs on top, then the last 50 with WIB time, duration, warnings, log.
- `AC.palette.add({g: 'Aksi', ic, l, d, run, dev})`: extra Ctrl+K entries. `AC.recipes` + `AC.runRecipe(r)`: Home recipe
  carousel (placeholder until the recipe runner exists).
- `AC.keys.register([{keyCode, ctrlKey, altKey, shiftKey}])` (extra CEP key interest), `AC.keys.onEscape(fn)` (popovers:
  return true when closed; returns an unregister fn), `AC.keys.typing()`.
- `AC.router.go(route, {replace})`, `AC.router.current()`, `AC.router.page(name, def)`.
- `AC.theme.sync()` (Premiere `appSkinInfo` + `ThemeColorChanged`; `data-theme="light"` above 45% luminance, surfaces
  derived from the host colour in dark), density `data-density="compact"`, `data-motion="reduce"`.
- `AC.util`: `$`, `$$`, `esc`, `dec(n, d)` (decimal comma), `int(n)` ("1.500.000"), `rp(n)` ("Rp 1.500.000"), `mmss(s)`
  ("0:49"), `tcode(s)` ("0:11,50"), `dtk(s)` ("3,7 dtk", "1 mnt 30 dtk"), `wibTime(ms)` ("01:13 WIB"), `dayLabel(ms)`
  ("Hari ini, 5 Okt"), `debounce`, `clamp`, `copy`, `assign`, `copyText`, `safeName`, `err(code, msg, hint)`, `errMsg(e)`.
- `AC.bus` events: `'seq', 'route', 'settings', 'health', 'task', 'theme', 'tools'`. `AC.log.info/warn/error/text()`.
- `AC.sys`: `paths {ext, root, engine, cli, worker, host, appData, local, temp, jobs, home, videos}`, `exists,
  readText, writeText (atomic), readJSON, writeJSON, list, mkdirp, remove, mtime, spawn, killTree, openFolder, join,
  dirname, basename, stem, extname`. Use it instead of `require` so tests work.

---

## 12. Copy rules (ux_design.md section 8)

- "kamu", short sentences, verb first on buttons; say what will happen ("Potong 16 jeda") and afterwards what changed
  and how to undo ("Sequence asli tidak diubah", "Buka yang asli").
- Glossary: jeda (not silence, except the tool name "Potong Silence"), dibuang / disimpan (dihapus only for deleting
  files/results), Tinjau, Gaya potong / preset, Pengaturan lanjutan, Batalkan (job) / Batal (dialog), sequence, clip,
  track, marker, render, playhead (never sekuens/klip), AI (provider names such as Grok only in Settings).
- Units: "dtk", "mnt", decimal comma ("0,5 dtk"), "0:31", timecode "0:11,50", "01:13 WIB", "Rp 1.500.000".
  Use `AC.util` formatters. No em dashes. Sentence case, no uppercase eyebrows.
- Errors: title = what failed in user words; text = plain reason + what to do; the real error text goes in the log block.
- English (same keys in `panel/locales/en.json`): concise, sentence case, verbs on buttons ("Cut 16 pauses", "Apply to
  timeline"), friendly "you", no em dashes, decimal dot, "s"/"min". Natural, not literal: jeda = pause, Tinjau = Review,
  dibuang / disimpan = removed / kept, "Sequence asli tidak diubah" = "Your original sequence is untouched", "Buka yang
  asli" = "Open original". Premiere terms (sequence, clip, track, marker, playhead, In/Out) stay.

---

## 13. CSS conventions

- Use the tokens from `css/base.css` (`--bg`, `--surface-0..4`, `--surface-pop`, `--border*`, `--text-1..3`, `--text-off`,
  `--accent*` (ember, dark text on fills: `--on-accent`), `--success/warn/danger(-soft)`, `--kept`, `--cut`, `--ring`,
  type `--fs-*`, `--font-ui/display/mono`, shape `--r-ctl 6 / --r-card 10 / --r-chip 5 / --r-pill`, density `--pad --gap
  --gap-sm --ctl-h --row-h --bar-h --icon --dock-pad`, motion `--ease --t-fast --t --t-slow`). Never hard-code colours
  (light theme and Premiere brightness must keep working).
- Reuse the component classes (`.sec`, `.sec-h`, `.card`, `.src`, `.seg`, `.field`, `.range`, `.sw`, `.opts/.opt`,
  `.chip`, `.btn*`, `.alert*`, `.empty`, `.sum-strip`, `.wave-wrap`, `.legend`, `.tag*`, `.stats`, `.next-i`).
- Prefix tool-only classes with the tool id (`.silence-xxx`) and keep them in `css/tools/<tool>.css`. The caption editor
  CSS (stage, template grid, word chips, style controls, animation tiles) is in the mockup section 8: port it to
  `captions.css`.
- Must work at 280-1200 px wide (media queries on the viewport; no container queries), in both densities, and with
  `prefers-reduced-motion` / `data-motion="reduce"`.
- State is never colour only (text or hatch too); focus ring `box-shadow: var(--ring)`; targets >= 24 px.

---

## 14. Testing

### 14.1 Headless harness (`tools/ui_test.mjs`)

```
node tools/ui_test.mjs [tools/ui_tests/<tool>.mjs ...] [--engine live|mock|auto] [--seq raw49|cut15|long35|none]
                       [--width 380] [--height 720] [--shots docs/shots] [--keep] [--quiet] [--no-compat]
```

- Launches headless Chrome on `panel/index.html` with `tools/cep_stub.js` injected before the page scripts, waits for
  `AC.readyState`, runs `tools/compat_check.mjs` as the first check, then each test file's
  `export default async function (t)`. Exit code 1 on any failed check, page exception, `console.error` or failed
  resource load. Default test: `tools/ui_tests/foundation.mjs`.
- Sandbox: `%TEMP%\ac_ui_<random>` is the page's USERPROFILE/APPDATA/LOCALAPPDATA/TEMP, so settings, history, job files
  and workdirs land there and are deleted afterwards (`--keep` keeps them). Page writes outside the sandbox are refused;
  reads go anywhere (real media and caches next to them).
- `--engine live` (default when `engine/cli.py` exists): `child_process.spawn` runs real processes through the harness
  (python only, plus `taskkill` for pids the harness started; `explorer` is ignored). Spawned processes get the real
  environment except `APPDATA` (sandbox, so the engine reads the page's settings.json); the GPU lock and model caches
  stay shared. `--engine mock`: in-page fake engine (`cli.py health/tools/run`, `worker.py`, `--version`, `taskkill`)
  with a built-in `echo` tool; add mocks with `__acStub.engineMock[tool] = function (job, emit, done) {...}` (emit
  JSON-line objects, call `done(exitCode)`, return a stop function).
- `--shots` defaults to `docs/shots` (gitignored, not part of the public repo).
- Fixtures (`--seq`, `t.setSeq(name|object|null)`): `raw49` (49 s test clip, 1 clip), `cut15` ("(Klipora)" sequence,
  15 clips, real cut list), `long35` (34,6 min clip), `none`.

`t` API: `ev(expr)` (awaits promises; wrap expressions that return a Job as `(expr, true)`), `waitFor(expr, ms, label)`,
`wait(ms)`, `click(sel)`, `key(key, {ctrl, alt, shift}, sel)`, `type(sel, text)`, `go(route)`, `text(sel)`, `count(sel)`,
`hash()`, `shot(name)` (PNG in `--shots`), `check(name, ok, detail)`, `host(fn, 'function (...) { return JSON...; }')`
(replace a stub host function), `calls(fn)` (args of every evalScript call to `fn`), `fire(eventName)` (Premiere event),
`setSeq(v)`, `load(stubConfig)` (reload the page with another stub config, e.g. `{events: false}` for polling,
`{node: false}`, `{python: false}`), `readSandbox(rel)`, `existsSandbox(rel)`, `engine`, `sandbox`, `root`, `panel`.

In-page stub API (`window.__acStub`): `calls`, `host` (handlers, override any `bac_*` or add `bac_<tool>_*`), `seq`,
`fixtures`, `markers`, `made` (sequences created by clone/import), `fire(type, data)`, `setSeq(v, fire)`, `setSkin({red,
green, blue})` (fires ThemeColorChanged), `keys` (registered key interest), `engineMock`, `health` (mock health data),
`fs`, `mode`.

Example test (`tools/ui_tests/<tool>.mjs`):

```js
export default async function (t) {
  await t.go('tool/silence');
  await t.host('bac_silence_x', 'function (a) { return JSON.stringify({ok: true}); }');
  await t.click('#primary');
  await t.waitFor(`location.hash === '#tool/silence/review'`, 30000, 'review');
  t.check('review rows', (await t.count('.rv')) > 0);
  await t.shot('silence_review');
}
```

### 14.2 Static checks
`node tools/compat_check.mjs` (Chrome 99 JS/CSS, IIFE wrapping, ES3 host). The test suites:
`node tools/ui_test.mjs` (foundation), `node tools/ui_test.mjs tools/ui_tests/components.mjs`,
`node tools/ui_test.mjs tools/ui_tests/smoke.mjs` (fast boot check).

### 14.3 Live Premiere (verify agents only, one at a time)
`node tools/reload.mjs` (reload panel, prints exceptions/console), `node tools/cdp.mjs "<js>"` (evaluate in the panel,
e.g. `AC.host.json('bac_seqInfo','lite')`, `AC.log.errors`), `node tools/ev.mjs "<extendscript>"`,
`node tools/shot.mjs <png>`. Devtools port 8088 (`panel/.debug`).

---

## 15. Contracts with the engine (what the panel sends and expects)

| Item | Contract |
|---|---|
| Job file | `{id, tool, action, seq, params (+ params.scope), workdir, review?}`; `%TEMP%\Klipora\jobs\<id>.json` |
| Events | SPEC section 2 plus `progress.all` (overall %), `progress.note` (e.g. "Antre GPU ..."), `stage.sub`, `stage.w`; worker events carry the stage id in `stage` |
| Result | `data.review` (path) -> review pane; `data.plan` -> apply; `data.summary` (string or `{n, on, sec_on}`) -> Riwayat line |
| Review file | `engine/ac/review.py` shape; panel only flips `on` and writes the file back before `apply` |
| Health | `cli.py health` result `{ok, python, ffmpeg, gpu, models, ai: {ok, ms, base_url, model, has_key, disabled, why}, disk, issues, ms}` |
| XML cut | tool `xmeml`, action `cut`, params `{xml, out, ranges, name}` -> `{path, ...}` |
| Settings | `%APPDATA%\Klipora\settings.json` keys in section 11 (`pexelsKey` included, never shown) |

## 16. Verification status and known gaps

- Headless (Chrome 154 + stub): `foundation.mjs` 66/66, `components.mjs` 35/35, `smoke.mjs` 5/5, with the real engine
  (`--engine live`: cli.py run/health, worker.py, xmeml/cut, taskkill) and with the mock; widths 280/380/660 px have no
  horizontal overflow. Screenshots (`foundation_headless_*.png`) are written to the `--shots` folder (default `docs/shots`, gitignored).
- Live Premiere 26.2.2 (one read-only check, 2026-10-05): panel boots without console errors, 4 host files load in
  20 ms, `AC.seq.mode = 'events'` (tlBindEvents works), `bac_seqInfo('full')` returns the active sequence in 14 ms
  (15 clips, In/Out unset -> null), `cli.py health` OK (AI 21 ms).
- Seam check live (2026-10-05, Premiere 26.2.2, test sequence from the 49 s test clip `talk_49s.mp4` in bin `Klipora Test`, all
  removed afterwards, project never saved): panel reload without console errors; `bac_seqInfo('full')` of the user's
  sequence and of the test sequence parse in `ac.timeline.Timeline` (15+15 clips, 74 / 81 words from the v3 cache,
  envelope, scope); Echo end to end (stages "Baca sequence, Cari bagian, Siapkan review", review list, row click ->
  `bac_seek` moved the playhead to 22,5 dtk, engine `apply` -> `AC.apply.removeRanges` clone + extract: 48,917 ->
  44,717 dtk, 7 cuts exact, original unchanged, "Buka yang asli"); `bac_applyRemove` 3 ranges (48,917 -> 44,417 dtk,
  4 clips, exact in/out); XML route (`mode: 'xml'`: `bac_exportXml` -> engine `xmeml/cut` -> `bac_importXmlAndOpen`)
  gave the identical 4-clip sequence and no duplicate master clips; `AC.engine.worker` echo / cancel (`CANCELLED`) /
  next request; `bac_deleteSequence`. Clones and XML imports land in the project ROOT bin (Premiere `clone()` behaviour).
- Still UNVERIFIED live: markers through the panel, `registerKeyEventsInterest` effects, `ThemeColorChanged`,
  MinSize/MaxSize for a docked panel, `bac_exportFrame` through the bridge.
- Recipes are placeholders (toast). Bricolage Grotesque is not bundled (falls back to Segoe UI Variable Display).
- Every Premiere edit triggers one lite `bac_seqInfo` (3 ms on 15 clips); full info is only fetched when a tool runs or
  Home checks the transcript tag. Watch the cost on 400+ clip sequences.
- "Pilih semua" in the review list acts on the filtered rows (the mockup acted on all rows).

---

## 17. Languages (`AC.i18n`, Indonesian + English)

- Locale files `panel/locales/id.json`, `panel/locales/en.json`: flat keys (`<ns>.<camelCase>`), sorted. A value is a
  string or a plural object `{"one": "...", "other": "..."}` (form chosen by `vars.n` / `vars.count` with
  `Intl.PluralRules`). `node tools/i18n_check.mjs` fails on missing keys between id/en, different `{placeholders}`, em
  dashes in English, and Indonesian literals left in `panel/js/**`, `panel/host/*.jsx` and `engine/ac/**/*.py`
  (heuristic; mark intentional data with `// i18n-ignore`).
- `AC.t(key, vars)`: text in the current language; `{name}` placeholders; integers are formatted per locale. Escape
  before innerHTML (`U.esc(AC.t(..))`) or use `AC.i18n.html(key, vars)` (escapes the values, keeps markup in the
  locale string). Missing key -> Indonesian -> the key (logged once).
- `AC.i18n.lang()` (`'id'|'en'`), `setting()` (`'auto'|'id'|'en'`), `set(v)` (saves `uiLang`, switches), `has(key)`,
  `raw(key)`, `hostLocale()`, `apply(root)` (fills `data-i18n`, `data-i18n-aria|title|ph` attributes in static markup).
  `auto` follows Premiere's UI locale (`getHostEnvironment().appUILocale`, `id*` -> Indonesian, else English).
  Installs from before this release keep Indonesian (settings.json without `uiLang` -> `id`).
- Formatting follows the language: `U.dec`, `U.int`, `U.dtk` ("3,7 dtk" / "3.7 s"), `U.tcode`, `U.wibTime`
  (id: "01:13 WIB"; en: PC local time), `U.dayLabel`; `AC.t('unit.sec')`, `AC.t('unit.min')`.
- Tools: `AC.tools.register({ ..., text: function (t) { return {title, tab, desc, lead, cta, next, review}; } })` is
  merged into the def at register time and after every switch. Pages may use `title: function () {...}`.
- Live switch: `AC.i18n.set('en')` -> `AC.tools.relabel()` + `AC.router.remount()`: every page and every idle tool
  page is dropped and rendered again (`render(el, ctx)` runs again, `ctx.state` survives; Emitter listeners added
  during render are removed first, see `AC.collectListeners`; optional `def.onUnmount(ctx)` / page `onUnmount()`).
  Tools with a running job keep their page until the next switch. `AC.bus` emits `'locale'`. Never compute
  translated text at script load time.
- Host (ExtendScript): `acT("key", "Indonesian fallback", a, b)` returns the panel's `host.<key>` string with `{0}`,
  `{1}` filled. The panel sends all `host.*` keys with `bac_setStrings` after loading the host files and on every switch.
- Engine: every job carries `lang`, every spawned engine process gets env `AC_LANG` (docs/ENGINE_API.md 3.4).
- Brand helpers: `AC.brand` (`name`, `suffix` " (Klipora)", `resultRe`, `suffixRe`, `track(x)`, `nameAlts`,
  `nameIs`), `U.tagAlts(tag)` / `U.hasTag(text, tag)` ("[Klipora-X]" also matches the old "[AC-X]"). Host:
  `AC_SEQ_SUFFIX`, `acIsResultName`, `acNameAlts`, `acNameIs`, `acTagAlts`, `acHasTag`; `tlFindBin` and
  `gxFindVideoTrack` also find the old "AutoCut ..." names, `bac_clearMarkersByTag` also clears old tags.
- Tests: the stub's Premiere locale is `id_ID` by default (`node tools/ui_test.mjs ... --locale en_US` for English,
  or `t.load({locale: 'en_US'})`); `await t.idLeftovers(sel)` lists visible Indonesian words left under `sel`
  (aria-label/title/placeholder too; `[data-i18n-skip]` excludes user content). English suites: `smoke_en.mjs`,
  `silence_en.mjs`, `captions_en.mjs`, plus English passes inside the tool suites.
