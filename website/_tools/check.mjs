// Verifies every page in headless Chrome: screenshots at 1440 and 390, console errors, broken links and anchors.
// Run: node website/_tools/check.mjs   (screenshots go to website/_shots/, which is gitignored)
import { spawn } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import { ROOT } from './lib.mjs';
import { start, resolve } from './serve.mjs';

const CHROME = 'C:/Program Files/Google/Chrome/Application/chrome.exe';
const PORT = 4321, DBG = 9333;
const BASE = `http://127.0.0.1:${PORT}`;
const PAGES = ['/', '/docs', '/changelog', '/id', '/id/docs', '/id/changelog'];
const WIDTHS = [1440, 390];
const OUT = path.join(ROOT, '_shots');
fs.mkdirSync(OUT, { recursive: true });

const srv = await start(PORT);
const prof = fs.mkdtempSync(path.join(os.tmpdir(), 'klipora-chk-'));
const chrome = spawn(CHROME, ['--headless=new', `--remote-debugging-port=${DBG}`, `--user-data-dir=${prof}`, '--hide-scrollbars', '--no-first-run', 'about:blank'], { stdio: 'ignore' });

let ver;
for (let i = 0; i < 50 && !ver; i++) {
  try { ver = await (await fetch(`http://127.0.0.1:${DBG}/json/version`)).json(); } catch { await new Promise((r) => setTimeout(r, 200)); }
}
const targets = await (await fetch(`http://127.0.0.1:${DBG}/json/list`)).json();
const ws = new WebSocket(targets.find((t) => t.type === 'page').webSocketDebuggerUrl);
await new Promise((r) => ws.addEventListener('open', r, { once: true }));
let id = 0; const pending = new Map(); const events = [];
ws.addEventListener('message', (m) => {
  const d = JSON.parse(m.data);
  if (d.id && pending.has(d.id)) { pending.get(d.id)(d); pending.delete(d.id); } else if (d.method) events.push(d);
});
const send = (method, params = {}) => new Promise((ok, bad) => {
  const n = ++id; pending.set(n, (d) => (d.error ? bad(new Error(method + ': ' + d.error.message)) : ok(d.result)));
  ws.send(JSON.stringify({ id: n, method, params }));
});
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const evalJs = async (expr) => (await send('Runtime.evaluate', { expression: expr, returnByValue: true, awaitPromise: true })).result.value;

await send('Page.enable'); await send('Runtime.enable'); await send('Log.enable'); await send('Network.enable');
const problems = [];
const failedReq = [];
for (const w of WIDTHS) {
  const mobile = w < 768;
  await send('Emulation.setDeviceMetricsOverride', { width: w, height: mobile ? 844 : 900, deviceScaleFactor: 1, mobile });
  for (const p of PAGES) {
    events.length = 0;
    await send('Page.navigate', { url: BASE + p });
    await sleep(2500);
    // Scroll through so lazy images load and reveal animations fire, then return to top.
    const h = await evalJs('document.documentElement.scrollHeight');
    for (let y = 0; y < h; y += 700) { await evalJs(`window.scrollTo(0, ${y})`); await sleep(60); }
    await evalJs('window.scrollTo(0, 0)'); await sleep(900);
    for (const e of events) {
      if (e.method === 'Runtime.exceptionThrown') problems.push(`${p} @${w}: exception ${e.params.exceptionDetails.text} ${e.params.exceptionDetails.exception?.description || ''}`);
      if (e.method === 'Runtime.consoleAPICalled' && ['error', 'warning'].includes(e.params.type)) problems.push(`${p} @${w}: console.${e.params.type} ${e.params.args.map((a) => a.value || a.description).join(' ')}`);
      if (e.method === 'Log.entryAdded' && e.params.entry.level === 'error') problems.push(`${p} @${w}: log ${e.params.entry.text} ${e.params.entry.url || ''}`);
      if (e.method === 'Network.loadingFailed') failedReq.push(`${p}: ${e.params.errorText}`);
      if (e.method === 'Network.responseReceived' && e.params.response.status >= 400) problems.push(`${p} @${w}: HTTP ${e.params.response.status} ${e.params.response.url}`);
    }
    const ovf = await evalJs(`(() => { const W = document.documentElement.clientWidth; const bad = []; document.querySelectorAll('body *').forEach(el => { const r = el.getBoundingClientRect(); if (r.width && r.right > W + 1 && getComputedStyle(el).position !== 'fixed' && !el.closest('.marquee') && !el.closest('pre') && !el.closest('.table-wrap') && !el.closest('.sr-only') && !el.closest('svg[style*="display:none"]')) bad.push(el.tagName + '.' + el.className + ' ' + Math.round(r.right)); }); return { sw: document.documentElement.scrollWidth, W, bad: bad.slice(0, 8) }; })()`);
    if (ovf.sw > ovf.W || ovf.bad.length) problems.push(`${p} @${w}: horizontal overflow scrollWidth=${ovf.sw} vs ${ovf.W} ${ovf.bad.join(' | ')}`);
    if (w === 1440) {
      const links = await evalJs(`[...document.querySelectorAll('a[href]')].map(a => a.getAttribute('href'))`);
      const ids = new Set(await evalJs(`[...document.querySelectorAll('[id]')].map(e => e.id)`));
      for (const l of links) {
        if (l.startsWith('#')) { if (l.length > 1 && !ids.has(l.slice(1))) problems.push(`${p}: missing anchor ${l}`); continue; }
        if (l.startsWith('/')) {
          const [u, hash] = l.split('#');
          if (!resolve(u)) { problems.push(`${p}: broken link ${l}`); continue; }
          if (hash) {
            const html = fs.readFileSync(resolve(u), 'utf8');
            if (!html.includes(`id="${hash}"`)) problems.push(`${p}: missing anchor ${l}`);
          }
        }
      }
      const imgs = await evalJs(`[...document.images].filter(i => !i.complete || !i.naturalWidth).map(i => i.src)`);
      imgs.forEach((s) => problems.push(`${p}: image not loaded ${s}`));
      const noAlt = await evalJs(`[...document.images].filter(i => !i.hasAttribute('alt')).map(i => i.src)`);
      noAlt.forEach((s) => problems.push(`${p}: image without alt ${s}`));
    }
    const name = (p === '/' ? 'home' : p.slice(1).replace(/\//g, '-')) + `-${w}`;
    await sleep(1500);
    const fold = await send('Page.captureScreenshot', { format: 'png' });
    fs.writeFileSync(path.join(OUT, name + '-fold.png'), Buffer.from(fold.data, 'base64'));
    const full = await evalJs('document.documentElement.scrollHeight');
    const shot = await send('Page.captureScreenshot', { format: 'png', captureBeyondViewport: true, clip: { x: 0, y: 0, width: w, height: Math.min(full, 16000), scale: 1 } });
    fs.writeFileSync(path.join(OUT, name + '.png'), Buffer.from(shot.data, 'base64'));
    console.log('shot', name, full + 'px');
  }
}
ws.close(); chrome.kill(); srv.close();
const ext = failedReq.filter((x) => !x.includes('ERR_ABORTED'));
console.log(problems.length || ext.length ? 'PROBLEMS:\n' + [...problems, ...ext].join('\n') : 'OK: no console errors, broken links, missing anchors or overflow');
process.exit(0);
