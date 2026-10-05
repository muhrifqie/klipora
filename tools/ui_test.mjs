// ui_test.mjs: headless Chrome harness for panel/index.html with the fake CEP from tools/cep_stub.js.
// Usage:
//   node tools/ui_test.mjs [tests/x.mjs ...] [--engine mock|live|auto] [--seq raw49|cut15|long35|none]
//                          [--width 380] [--height 720] [--shots docs/shots] [--keep] [--quiet] [--no-compat]
//                          [--locale id_ID|en_US]   (Premiere UI locale; the panel follows it while uiLang = auto)
// Default test: tools/ui_tests/foundation.mjs. Every test file exports `default async function (t) {...}`.
// Exit code 1 when a check fails or the page logs an error (exceptions, console.error, failed resource loads).
// Sandbox: %TEMP%\ac_ui_<random> holds the fake USERPROFILE/APPDATA/LOCALAPPDATA/TEMP; page writes are only
// allowed inside it (reads anywhere). --engine live runs the real engine/cli.py (python) through the harness.
// See docs/PANEL_API.md "Testing" for the `t` API.
import { spawn } from 'child_process';
import fs from 'fs';
import http from 'http';
import os from 'os';
import path from 'path';
import { fileURLToPath, pathToFileURL } from 'url';

const here = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(here, '..');
const PANEL = path.join(ROOT, 'panel');
const CHROME = process.env.AC_CHROME || 'C:/Program Files/Google/Chrome/Application/chrome.exe';

// ---------------------------------------------------------------- args
const argv = process.argv.slice(2);
const opt = { engine: 'auto', seq: 'raw49', width: 380, height: 720, shots: path.join(ROOT, 'docs', 'shots'), keep: false, quiet: false, tests: [] };
for (let i = 0; i < argv.length; i++) {
  const a = argv[i];
  if (a === '--engine') opt.engine = argv[++i];
  else if (a === '--seq') opt.seq = argv[++i];
  else if (a === '--width') opt.width = +argv[++i];
  else if (a === '--height') opt.height = +argv[++i];
  else if (a === '--shots') opt.shots = path.resolve(argv[++i]);
  else if (a === '--keep') opt.keep = true;
  else if (a === '--quiet') opt.quiet = true;
  else if (a === '--no-compat') opt.noCompat = true;
  else if (a === '--locale') opt.locale = argv[++i];
  else opt.tests.push(path.resolve(a));
}
if (!opt.tests.length) opt.tests.push(path.join(here, 'ui_tests', 'foundation.mjs'));
// ---------------------------------------------------------------- test media (same layout as engine/tests/_common.py)
// KLIPORA_TEST_MEDIA = folder with talk_49s.mp4 / talk_35m.mp4 / seq_2m.mp4, or a media.json there that maps those
// keys to files elsewhere (+ "glossary": brand terms spoken in the clip). Without it the fixtures point at
// placeholder paths: --engine auto falls back to mock, and tests that need real media skip.
const MEDIA = (() => {
  const dir = process.env.KLIPORA_TEST_MEDIA || '';
  const names = { talk_49s: 'talk_49s.mp4', talk_35m: 'talk_35m.mp4', seq_2m: 'seq_2m.mp4' };
  let conf = {};
  if (dir) { try { conf = JSON.parse(fs.readFileSync(path.join(dir, 'media.json'), 'utf8')); } catch { conf = {}; } }
  const m = { dir, glossary: Array.isArray(conf.glossary) ? conf.glossary.filter((g) => typeof g === 'string' && g) : [], have: {} };
  for (const [k, n] of Object.entries(names)) {
    const v = conf[k] || n;
    m[k] = path.normalize(dir ? (path.isAbsolute(v) ? v : path.join(dir, v)) : path.join('C:/klipora_test_media', n));
    m.have[k] = !!dir && fs.existsSync(m[k]);
  }
  return m;
})();
globalThis.KLIPORA_MEDIA = MEDIA;   // test modules read media paths at import time
const SEQ_MEDIA = { raw49: 'talk_49s', cut15: 'talk_49s', long35: 'talk_35m' };
const needMedia = SEQ_MEDIA[opt.seq];
if (opt.engine === 'auto') {
  opt.engine = fs.existsSync(path.join(ROOT, 'engine', 'cli.py')) && (!needMedia || MEDIA.have[needMedia]) ? 'live' : 'mock';
  if (opt.engine === 'mock' && needMedia && !MEDIA.have[needMedia]) console.log('note: no test media for --seq ' + opt.seq + ' (set KLIPORA_TEST_MEDIA), using --engine mock');
} else if (opt.engine === 'live' && needMedia && !MEDIA.have[needMedia]) {
  console.log('skip: --engine live with --seq ' + opt.seq + ' needs real test media (' + needMedia + '.mp4), set KLIPORA_TEST_MEDIA');
  process.exit(0);
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
// Remove stale sandboxes of killed runs (older than 1 hour) so %TEMP% never fills up.
for (const d of fs.readdirSync(os.tmpdir())) {
  if (!d.startsWith('ac_ui_')) continue;
  const p = path.join(os.tmpdir(), d);
  try { if (Date.now() - fs.statSync(p).mtimeMs > 3600e3) fs.rmSync(p, { recursive: true, force: true }); } catch { /* in use */ }
}
const sandbox = fs.mkdtempSync(path.join(os.tmpdir(), 'ac_ui_'));
for (const d of ['home/AppData/Roaming', 'home/AppData/Local', 'home/Videos', 'temp', 'chrome']) fs.mkdirSync(path.join(sandbox, d), { recursive: true });
const ENV = {
  USERPROFILE: path.join(sandbox, 'home'), APPDATA: path.join(sandbox, 'home', 'AppData', 'Roaming'),
  LOCALAPPDATA: path.join(sandbox, 'home', 'AppData', 'Local'), TEMP: path.join(sandbox, 'temp'), TMP: path.join(sandbox, 'temp'), SystemDrive: 'C:'
};

// ---------------------------------------------------------------- harness HTTP server (fs + processes for the stub)
const inside = (p) => { const r = path.resolve(p).toLowerCase(); return r.startsWith(sandbox.toLowerCase() + path.sep) || r === sandbox.toLowerCase(); };
const procs = new Map(); let nextId = 1; const ourPids = new Set();
function fsOp(b) {
  const p = b.p;
  switch (b.op) {
    case 'exists': return fs.existsSync(p);
    case 'read': return fs.readFileSync(p, 'utf8');
    case 'readdir': return fs.readdirSync(p);
    case 'stat': { const s = fs.statSync(p); return { size: s.size, mtime: s.mtimeMs, dir: s.isDirectory() }; }
    case 'realpath': return fs.realpathSync(p);
  }
  if (!inside(p) || (b.to && !inside(b.to))) { const e = new Error('write outside sandbox refused: ' + p); e.code = 'EACCES'; throw e; }
  switch (b.op) {
    case 'write': fs.mkdirSync(path.dirname(p), { recursive: true }); fs.writeFileSync(p, b.data, 'utf8'); return true;
    case 'append': fs.appendFileSync(p, b.data, 'utf8'); return true;
    case 'mkdir': fs.mkdirSync(p, { recursive: true }); return true;
    case 'unlink': fs.unlinkSync(p); return true;
    case 'rename': fs.renameSync(p, b.to); return true;
  }
  throw new Error('unknown op ' + b.op);
}
function startProc(b) {
  const base = path.basename(String(b.cmd)).toLowerCase().replace(/\.exe$/, '');
  if (base === 'explorer') return { id: 0, pid: 0, fake: true };
  if (base === 'taskkill') {
    const pid = Number(b.args[b.args.indexOf('/pid') + 1]);
    if (!ourPids.has(pid)) { const e = new Error('taskkill refused: pid ' + pid + ' was not started by the harness'); e.code = 'EPERM'; throw e; }
  } else if (!/python/i.test(base) && !/python/i.test(String(b.cmd))) { const e = new Error('spawn refused: ' + b.cmd); e.code = 'EPERM'; throw e; }
  // Real env for spawned processes (HF model cache, %LOCALAPPDATA% GPU lock stay shared with other jobs); only
  // APPDATA is sandboxed so the engine reads the settings.json the page wrote. PYTHON*/AC_* vars pass through.
  const pass = Object.fromEntries(Object.entries(b.env || {}).filter(([k]) => /^(PYTHON|AC_)/.test(k)));
  const child = spawn(b.cmd, b.args || [], { cwd: b.cwd || ROOT, env: { ...process.env, ...pass, APPDATA: ENV.APPDATA }, windowsHide: true });
  const id = nextId++, rec = { child, events: [], done: false, waiters: [] };
  const push = (t, d) => { rec.events.push({ t, d }); rec.waiters.splice(0).forEach((f) => f()); };
  child.stdout.on('data', (d) => push('out', d.toString('utf8')));
  child.stderr.on('data', (d) => push('err', d.toString('utf8')));
  child.on('error', (e) => { push('error', { message: e.message, code: e.code }); });
  child.on('close', (code) => { rec.done = true; push('exit', code); });
  procs.set(id, rec); if (child.pid) ourPids.add(child.pid);
  return { id, pid: child.pid || 0 };
}
const server = http.createServer((req, res) => {
  let body = '';
  req.on('data', (c) => { body += c; });
  req.on('end', () => {
    const send = (obj) => { res.writeHead(200, { 'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*' }); res.end(JSON.stringify(obj)); };
    const url = new URL(req.url, 'http://x');
    let b = {}; try { b = body ? JSON.parse(body) : {}; } catch { b = {}; }
    try {
      if (url.pathname === '/fs') return send({ result: fsOp(b) });
      if (url.pathname === '/spawn') return send(startProc(b));
      if (url.pathname === '/stdin') { const r = procs.get(b.id); if (r) { if (b.end) r.child.stdin.end(); else r.child.stdin.write(b.data); } return send({ ok: true }); }
      if (url.pathname === '/kill') { const r = procs.get(b.id); if (r) r.child.kill(); return send({ ok: true }); }
      if (url.pathname === '/proc') {
        const r = procs.get(+url.searchParams.get('id')); const since = +url.searchParams.get('since') || 0;
        if (!r) return send({ events: [{ t: 'exit', d: 0 }], next: 1, done: true });
        let sent = false;
        const reply = () => { if (sent) return; sent = true; send({ events: r.events.slice(since), next: r.events.length, done: r.done }); };
        if (r.events.length > since || r.done) return reply();
        const t = setTimeout(reply, 250); r.waiters.push(() => { clearTimeout(t); reply(); });
        return;
      }
      send({ error: { code: 'ENOTFOUND', message: 'no route' } });
    } catch (e) { send({ error: { code: e.code || 'EIO', message: e.message } }); }
  });
});
await new Promise((r) => server.listen(0, '127.0.0.1', r));
const SERVER = `http://127.0.0.1:${server.address().port}`;

// ---------------------------------------------------------------- Chrome + CDP
const port = 9600 + Math.floor(Math.random() * 300);
const chrome = spawn(CHROME, ['--headless=new', `--remote-debugging-port=${port}`, `--user-data-dir=${path.join(sandbox, 'chrome')}`, '--hide-scrollbars',
  '--force-device-scale-factor=1', '--allow-file-access-from-files', '--disable-gpu', '--no-first-run', '--disable-extensions',
  '--disable-component-update', '--disable-background-networking', '--disk-cache-size=1', '--media-cache-size=1',
  `--window-size=${opt.width},${opt.height}`, 'about:blank'], { stdio: 'ignore' });
let target;
for (let i = 0; i < 60 && !target; i++) { await sleep(200); try { target = (await (await fetch(`http://127.0.0.1:${port}/json/list`)).json()).find((t) => t.type === 'page'); } catch { /* starting */ } }
if (!target) { console.error('Chrome did not start'); process.exit(2); }
const ws = new WebSocket(target.webSocketDebuggerUrl);
await new Promise((r) => ws.addEventListener('open', r));
let mid = 0; const pending = new Map(); const errors = []; const logs = [];
ws.addEventListener('message', (ev) => {
  const m = JSON.parse(ev.data);
  if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id); return; }
  if (m.method === 'Runtime.exceptionThrown') { const d = m.params.exceptionDetails; errors.push('EXC ' + (d.exception?.description || d.text) + ' @' + (d.url || '') + ':' + d.lineNumber); }
  if (m.method === 'Runtime.consoleAPICalled') {
    const txt = m.params.args.map((a) => a.value ?? a.description).join(' ');
    if (m.params.type === 'error' || m.params.type === 'assert') errors.push('CONSOLE ' + txt); else logs.push(m.params.type + ' ' + txt);
  }
  if (m.method === 'Log.entryAdded' && m.params.entry.level === 'error') errors.push('LOG ' + m.params.entry.text + ' ' + (m.params.entry.url || ''));
});
const send = (method, params = {}) => new Promise((r) => { const i = ++mid; pending.set(i, r); ws.send(JSON.stringify({ id: i, method, params })); });
await send('Runtime.enable'); await send('Log.enable'); await send('Page.enable');
await send('Emulation.setDeviceMetricsOverride', { width: opt.width, height: opt.height, deviceScaleFactor: 1, mobile: false });

const stubSrc = fs.readFileSync(path.join(here, 'cep_stub.js'), 'utf8');
async function load(conf = {}) {
  // Premiere-exported xmeml of the 49 s clip (<clip>_autocut.xml next to it), copied by the fake bac_exportXml
  const sampleXml = MEDIA.talk_49s.replace(/\.[^.\/]+$/, '') + '_autocut.xml';
  const c = { server: SERVER, engine: opt.engine, extPath: PANEL, sandbox, env: ENV, seq: opt.seq === 'none' ? null : opt.seq,
              sampleXml: fs.existsSync(sampleXml) ? sampleXml : null, locale: opt.locale || 'id_ID',
              media: { talk_49s: MEDIA.talk_49s, talk_35m: MEDIA.talk_35m, seq_2m: MEDIA.seq_2m }, ...conf };
  const { identifier } = (await send('Page.addScriptToEvaluateOnNewDocument', { source: `window.__AC_STUB_CONFIG = ${JSON.stringify(c)};\n${stubSrc}` })).result;
  await send('Page.navigate', { url: 'about:blank' }); await sleep(50);
  await send('Page.navigate', { url: pathToFileURL(path.join(PANEL, 'index.html')).href });
  await t.waitFor('window.AC && AC.readyState && AC.readyState !== "booting"', 10000, 'panel boot (AC.readyState)');
  await send('Page.removeScriptToEvaluateOnNewDocument', { identifier });
}

// ---------------------------------------------------------------- test API
const results = [];
const t = {
  engine: opt.engine, locale: opt.locale || 'id_ID', media: MEDIA, sandbox, root: ROOT, panel: PANEL, env: ENV, width: opt.width, height: opt.height,
  async ev(expr) {
    const r = await send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true });
    if (r.result?.exceptionDetails) throw new Error('ev failed: ' + (r.result.exceptionDetails.exception?.description || r.result.exceptionDetails.text) + '\n  in: ' + expr.slice(0, 200));
    return r.result?.result?.value;
  },
  wait: sleep,
  async waitFor(expr, timeout = 5000, label = expr) {
    const t0 = Date.now();
    while (Date.now() - t0 < timeout) { try { if (await t.ev(`!!(${expr})`)) return true; } catch { /* not yet */ } await sleep(50); }
    throw new Error('timeout waiting for ' + label);
  },
  click: (sel) => t.ev(`(function(){var e=document.querySelector(${JSON.stringify(sel)}); if(!e) throw new Error('missing ' + ${JSON.stringify(sel)}); e.click(); return true;})()`),
  // key('Enter', {ctrl:true}, '#target') dispatches keydown on the target (default: the focused element).
  key: (key, mods = {}, sel) => t.ev(`(function(){var el=${sel ? `document.querySelector(${JSON.stringify(sel)})` : 'document.activeElement||document.body'};
    el.dispatchEvent(new KeyboardEvent('keydown',{key:${JSON.stringify(key)},ctrlKey:${!!mods.ctrl},altKey:${!!mods.alt},shiftKey:${!!mods.shift},bubbles:true,cancelable:true})); return true;})()`),
  type: (sel, text) => t.ev(`(function(){var e=document.querySelector(${JSON.stringify(sel)}); e.focus(); e.value=${JSON.stringify(text)}; e.dispatchEvent(new Event('input',{bubbles:true})); e.dispatchEvent(new Event('change',{bubbles:true})); return true;})()`),
  go: async (route) => { await t.ev(`AC.router.go(${JSON.stringify(route)})`); await sleep(60); },
  text: (sel) => t.ev(`(function(){var e=document.querySelector(${JSON.stringify(sel)}); return e ? e.textContent.trim() : null;})()`),
  count: (sel) => t.ev(`document.querySelectorAll(${JSON.stringify(sel)}).length`),
  hash: () => t.ev('location.hash'),
  async shot(name) {
    fs.mkdirSync(opt.shots, { recursive: true });
    const s = await send('Page.captureScreenshot', { format: 'png' });
    const f = path.join(opt.shots, name.endsWith('.png') ? name : name + '.png');
    fs.writeFileSync(f, Buffer.from(s.result.data, 'base64'));
    return f;
  },
  check(name, ok, detail = '') { results.push({ name, ok: !!ok, detail }); if (!opt.quiet || !ok) console.log(`${ok ? 'PASS' : 'FAIL'} ${name}${detail && !ok ? '  -> ' + detail : ''}`); return !!ok; },
  // Replace a host function: t.host('bac_seek', 'function (t) { return JSON.stringify({ok:true}); }')
  host: (fn, src) => t.ev(`(__acStub.host[${JSON.stringify(fn)}] = (${src}), true)`),
  calls: (fn) => t.ev(`__acStub.calls.filter(function(c){return c.fn===${JSON.stringify(fn)}}).map(function(c){return c.args})`),
  fire: (name = 'onActiveSequenceChanged') => t.ev(`__acStub.fire('com.klipora.ev', ${JSON.stringify(name)} + '|' + (__acStub.seq ? __acStub.seq.id : ''))`),
  setSeq: (v) => t.ev(`(__acStub.setSeq(${typeof v === 'string' || v === null ? JSON.stringify(v) : JSON.stringify(v)}), true)`),
  // English runs: Indonesian words left in the VISIBLE text under `sel` (same word list as tools/i18n_check.mjs).
  // Returns [] when clean, else up to 20 offending text snippets. Elements with [data-i18n-skip] (user content) are ignored.
  async idLeftovers(sel = '#app') {
    const { ID_WORDS } = await import(pathToFileURL(path.join(here, 'i18n_check.mjs')).href);
    return t.ev(`(function(){var words=${JSON.stringify(ID_WORDS)}, set={}; words.forEach(function(w){set[w]=1;});
      var root=document.querySelector(${JSON.stringify(sel)}); if(!root) return ['missing ' + ${JSON.stringify(sel)}];
      var out=[], walker=document.createTreeWalker(root, NodeFilter.SHOW_TEXT|NodeFilter.SHOW_ELEMENT);
      var check=function(txt){ txt=String(txt||'').trim(); if(!txt) return; if(/(^|[^A-Za-z])(dtk|mnt)([^A-Za-z]|$)/.test(txt) || txt.split(/[^A-Za-zÀ-ÿ]+/).some(function(w){return set[w.toLowerCase()];})) { if (out.indexOf(txt) < 0 && out.length < 20) out.push(txt.slice(0, 90)); } };
      var n; while((n=walker.nextNode())){
        if (n.nodeType===1) { if (n.closest('[data-i18n-skip],script,style,svg,textarea,input')) continue; if (n.offsetParent===null && n.tagName!=='BODY') continue;
          ['aria-label','title','placeholder'].forEach(function(a){ if(n.hasAttribute(a)) check(n.getAttribute(a)); }); continue; }
        var pe=n.parentElement; if(!pe || pe.closest('[data-i18n-skip],script,style,svg,textarea') || pe.offsetParent===null) continue;
        check(n.nodeValue);
      }
      return out;})()`);
  },
  load, errors, logs,
  readSandbox: (rel) => fs.readFileSync(path.join(sandbox, rel), 'utf8'),
  existsSandbox: (rel) => fs.existsSync(path.join(sandbox, rel))
};

// ---------------------------------------------------------------- run
let crashed = null;
try {
  console.log(`ui_test: engine=${opt.engine} seq=${opt.seq} locale=${opt.locale || 'id_ID'} ${opt.width}x${opt.height} sandbox=${sandbox}`);
  if (!opt.noCompat) {
    const { check } = await import(pathToFileURL(path.join(here, 'compat_check.mjs')).href);
    const found = check();
    t.check('compat: Chrome 99 (js/css) + ES3 (host jsx) static check', !found.length, found.join(' | '));
  }
  await load();
  for (const f of opt.tests) {
    const mod = await import(pathToFileURL(f).href);
    if (!opt.quiet) console.log(`--- ${path.relative(ROOT, f)}`);
    await mod.default(t);
  }
} catch (e) { crashed = e; console.log('CRASH ' + (e.stack || e)); }
const failed = results.filter((r) => !r.ok);
const uniqErr = [...new Set(errors)];
console.log(`\n${results.length - failed.length}/${results.length} checks passed` + (uniqErr.length ? `, ${uniqErr.length} page error(s):\n  ` + uniqErr.join('\n  ') : ', no page errors'));
ws.close(); chrome.kill(); server.close();
for (const r of procs.values()) { try { r.child.kill(); } catch { /* gone */ } }
await sleep(300);
if (!opt.keep) {
  for (let i = 0; i < 10; i++) { try { fs.rmSync(sandbox, { recursive: true, force: true }); break; } catch { await sleep(300); } }   // Chrome may still hold files
} else console.log('sandbox kept: ' + sandbox);
process.exit(failed.length || uniqErr.length || crashed ? 1 : 0);
