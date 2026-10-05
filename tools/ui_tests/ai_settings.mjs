// ai_settings.mjs: Pengaturan > AI > Penyedia AI in headless Chrome with the fake CEP and the REAL engine
// (node tools/ui_test.mjs tools/ui_tests/ai_settings.mjs). A fake OpenAI-compatible provider runs inside this test
// (127.0.0.1, random port), so no quota is spent and no real key is used. The engine's APPDATA is the sandbox, so
// ai_profiles.json / ai_keys.bin land there. Checks: list, wizard (preset, URL, write-only key, model list, search,
// slot fill, test, save), order up/down, detail test, enable/disable, edit, cancel removes the draft, delete, and
// that the key never reaches the DOM, localStorage or any file in plain text.
import fs from 'fs';
import http from 'http';
import path from 'path';

const FAKE_KEY = 'sk-fake-UI-0123456789-abcdefXYZ';
const MODELS = [
  { id: 'fake-fast', name: 'Fake Fast', context_length: 128000, architecture: { input_modalities: ['text'] } },
  { id: 'fake-smart', context_length: 1000000 },
  { id: 'fake-vision', architecture: { input_modalities: ['text', 'image'] }, pricing: { prompt: '0', completion: '0' } }
];

function startFake() {
  const log = [];
  const srv = http.createServer((req, res) => {
    let body = '';
    req.on('data', (c) => { body += c; });
    req.on('end', () => {
      const auth = req.headers.authorization || '';
      log.push({ method: req.method, url: req.url, auth, body });
      const send = (code, obj) => { res.writeHead(code, { 'Content-Type': 'application/json' }); res.end(JSON.stringify(obj)); };
      if (auth !== 'Bearer ' + FAKE_KEY) return send(401, { error: { message: 'bad key' } });
      if (req.method === 'GET' && req.url.endsWith('/v1/models')) return send(200, { object: 'list', data: MODELS });
      if (req.method === 'POST' && req.url.endsWith('/v1/chat/completions')) {
        const reply = body.includes('17 + 25') ? '{"sum": 42}' : body.includes('image_url') ? '{"color": "red"}' : '{"ok": true}';
        return send(200, { choices: [{ message: { role: 'assistant', content: reply } }] });
      }
      send(404, { detail: 'Not Found' });
    });
  });
  return new Promise((r) => srv.listen(0, '127.0.0.1', () => r({ srv, log, base: `http://127.0.0.1:${srv.address().port}` })));
}

export default async function (t) {
  if (t.engine === 'mock') {   // the in-page mock engine has no `cli.py ai`: the section must still render, without errors
    await t.go('settings');
    await t.waitFor(`document.querySelector('#aiProv .alert, #aiProv .aip-row')`, 10000, 'provider section (mock)');
    t.check('ai (mock engine): section renders a readable state', (await t.count('#aiProv')) === 1);
    return;
  }
  const fake = await startFake();
  const rows = () => t.ev(`Array.prototype.map.call(document.querySelectorAll('#aiProv .aip-row'), function (r) { return r.dataset.id; })`);
  const chain = () => t.text('#aiProv .aip-chain');
  const noKeyInPage = () => t.ev(`(function () { var k = ${JSON.stringify(FAKE_KEY)}; var ls = ''; try { for (var i = 0; i < localStorage.length; i++) ls += localStorage.key(i) + localStorage.getItem(localStorage.key(i)); } catch (e) {}
    var vals = Array.prototype.map.call(document.querySelectorAll('input'), function (i) { return i.value; }).join('|');
    return document.documentElement.outerHTML.indexOf(k) < 0 && ls.indexOf(k) < 0 && vals.indexOf(k) < 0 && AC.log.text().indexOf(k) < 0; })()`);
  const scrollTo = (sel) => t.ev(`(function(){ var e = document.querySelector(${JSON.stringify(sel)}); if (e) e.scrollIntoView({block: 'start'}); return !!e; })()`);
  const noOverflow = () => t.ev(`document.documentElement.scrollWidth <= window.innerWidth + 1`);

  try {
    // ---------------------------------------------------------------- list (no profiles file -> synthesized Grok)
    await t.go('settings');
    await t.waitFor(`document.querySelector('#aiProv .aip-row[data-id="grok_local"]')`, 30000, 'provider list');
    t.check('list: synthesized local proxy profile', JSON.stringify(await rows()) === '["grok_local"]');
    t.check('list: chain text ends with aturan bawaan', /Proxy lokal \(kompatibel OpenAI\), lalu aturan bawaan/.test(await chain()), await chain());
    t.check('list: Grok risk note visible', await t.ev(`!document.querySelector('#aiProv .alert-warn').hidden`));
    t.check('list: privacy note', /Transkrip dikirim/.test(await t.text('#aiProv .alert-info')));
    t.check('list: section title "AI"', await t.ev(`Array.prototype.some.call(document.querySelectorAll('.sec-h span'), function (s) { return s.textContent === 'AI'; })`));
    t.check('list: old "Dibaca dari .env" row removed', !(await t.ev(`document.body.textContent.indexOf('Dibaca dari .env') >= 0`)));
    await scrollTo('#aiProv');
    await t.shot('v3_ai_providers_list');

    // ---------------------------------------------------------------- wizard: preset + URL + key + models
    await t.click('#aiAdd');
    await t.waitFor(`document.querySelector('#aiWiz .aip-preset[data-kind="custom"]')`, 5000, 'wizard');
    t.check('wizard: 9 presets (local proxy hidden: it already exists and only it may use the .env key)',
      (await t.count('#aiWiz .aip-preset')) === 9 && (await t.count('#aiWiz .aip-preset[data-kind="grok_local"]')) === 0);
    t.check('wizard: add button hidden while open', await t.ev(`document.getElementById('aiAdd').hidden`));
    await t.click('#aiWiz .aip-preset[data-kind="gemini"]');
    await t.waitFor(`document.getElementById('aiwUrl')`, 3000, 'gemini form');
    t.check('wizard: Gemini base URL prefilled', (await t.ev(`document.getElementById('aiwUrl').value`)) === 'https://generativelanguage.googleapis.com/v1beta/openai/');
    t.check('wizard: key field is a password field', (await t.ev(`document.getElementById('aiwKey').type`)) === 'password');
    t.check('wizard: key hint names the key page', /aistudio\.google\.com/.test(await t.text('.aip-keystate')));
    await t.click('#aiWiz .aip-preset[data-kind="custom"]');
    await t.waitFor(`document.getElementById('aiwUrl') && document.getElementById('aiwUrl').value === ''`, 3000, 'custom form');
    await t.type('#aiwName', 'Fake Lokal');
    await t.type('#aiwUrl', fake.base.replace('127.0.0.1', 'localhost') + '/v1');
    await t.type('#aiwKey', FAKE_KEY);
    await t.click('#aiwModels');
    t.check('key: input cleared right after it is read', (await t.ev(`document.getElementById('aiwKey').value`)) === '');
    await t.waitFor(`document.querySelectorAll('#aiWiz .aip-model').length === 3`, 30000, 'model list');
    t.check('key: stored state shown, never the key', /tersimpan terenkripsi/i.test(await t.text('.aip-keystate')));
    t.check('models: context + image + free labels', /128K/.test(await t.text('.aip-model[data-id="fake-fast"] small')) &&
      /gambar, gratis/.test(await t.text('.aip-model[data-id="fake-vision"] small')), await t.text('.aip-model[data-id="fake-vision"] small'));
    t.check('models: count line', /3 dari 3 model/.test(await t.text('.aip-mcount')));
    await t.click('.aip-model[data-id="fake-fast"]');
    await t.click('.aip-model[data-id="fake-smart"]');
    await t.click('.aip-model[data-id="fake-vision"]');
    const slots = await t.ev(`[document.getElementById('aiwCepat').value, document.getElementById('aiwPintar').value, document.getElementById('aiwVision').value]`);
    t.check('models: clicks fill Cepat, Pintar, Vision in turn', JSON.stringify(slots) === '["fake-fast","fake-smart","fake-vision"]', JSON.stringify(slots));
    await t.type('#aiwSearch', 'smart');
    await t.waitFor(`document.querySelectorAll('#aiWiz .aip-model').length === 1`, 3000, 'search filter');
    t.check('models: search filters the list', (await t.text('#aiWiz .aip-model span')) === 'fake-smart');
    await t.type('#aiwSearch', '');
    t.check('no horizontal overflow (wizard)', await noOverflow());
    await scrollTo('#aiWiz');
    await t.shot('v3_ai_providers_wizard');

    // ---------------------------------------------------------------- test + save
    await t.click('#aiwTest');
    await t.waitFor(`/Koneksi (berhasil|gagal)/.test((document.querySelector('#aiWiz .aip-test') || {}).textContent || '')`, 60000, 'wizard test');
    const tr = await t.text('#aiWiz .aip-test');
    t.check('test: connection ok, JSON right, image read', /Koneksi berhasil/.test(tr) && /JSON benar/.test(tr) && /membaca gambar/.test(tr), tr);
    await t.ev(`document.querySelector('#aiWiz .aip-test').scrollIntoView({block: 'center'})`);
    await t.shot('v3_ai_providers_test');
    t.check('fake provider got the key as Bearer (engine only)', fake.log.some((r) => r.url.endsWith('/chat/completions') && r.auth === 'Bearer ' + FAKE_KEY));
    await t.click('#aiwSave');
    await t.waitFor(`!document.getElementById('aiWiz') && document.querySelector('#aiProv .aip-row[data-id="custom"]')`, 20000, 'saved profile row');
    t.check('save: appended as fallback after Grok', JSON.stringify(await rows()) === '["grok_local","custom"]');
    t.check('save: chain local proxy then Fake', /Proxy lokal \(kompatibel OpenAI\), lalu Fake Lokal, lalu aturan bawaan/.test(await chain()), await chain());
    t.check('save: localhost rewritten to 127.0.0.1', /127\.0\.0\.1/.test(await t.text('#aiProv .aip-row[data-id="custom"] .aip-detail')));
    t.check('save: key state in detail', /Tersimpan, terenkripsi/.test(await t.text('#aiProv .aip-row[data-id="custom"] .aip-detail')));

    // ---------------------------------------------------------------- order, detail test, enable/disable, edit
    await t.click('#aiProv .aip-row[data-id="custom"] .aip-mv[aria-label^="Naikkan"]');
    await t.waitFor(`/^Urutan: Fake Lokal, lalu Proxy lokal/.test(document.querySelector('#aiProv .aip-chain').textContent)`, 20000, 'move up');
    t.check('order: Fake Lokal is now Utama', /Utama/.test(await t.text('#aiProv .aip-row[data-id="custom"] .aip-main .tag')));
    await t.ev(`(function(){ var b = Array.prototype.filter.call(document.querySelectorAll('#aiProv .aip-row[data-id="custom"] .aip-detail .btn'), function (x) { return /Tes koneksi/.test(x.textContent); })[0]; b.click(); return true; })()`);
    await t.waitFor(`/Koneksi berhasil/.test(document.querySelector('#aiProv .aip-row[data-id="custom"] .aip-detail').textContent)`, 60000, 'row test');
    t.check('detail: status dot green after the test', await t.ev(`!!document.querySelector('#aiProv .aip-row[data-id="custom"] .aip-dot.is-ok')`));
    t.check('detail: last test line', /Berhasil/.test(await t.text('#aiProv .aip-row[data-id="custom"] .aip-kv')));
    await scrollTo('#aiProv');
    await t.shot('v3_ai_providers_detail');
    await t.click('#aiProv .aip-row[data-id="custom"] .aip-detail .sw input');
    await t.waitFor(`document.querySelector('#aiProv .aip-row[data-id="custom"].is-off')`, 20000, 'disabled');
    t.check('disable: dropped from the chain', /^Urutan: Proxy lokal \(kompatibel OpenAI\), lalu aturan bawaan/.test(await chain()), await chain());
    await t.click('#aiProv .aip-row[data-id="custom"] .aip-detail .sw input');
    await t.waitFor(`!document.querySelector('#aiProv .aip-row[data-id="custom"].is-off')`, 20000, 're-enabled');
    await t.ev(`(function(){ var b = Array.prototype.filter.call(document.querySelectorAll('#aiProv .aip-row[data-id="custom"] .aip-detail .btn'), function (x) { return /Ubah/.test(x.textContent); })[0]; b.click(); return true; })()`);
    await t.waitFor(`document.getElementById('aiwName')`, 3000, 'edit wizard');
    t.check('edit: no preset step, fields prefilled', (await t.count('#aiWiz .aip-preset')) === 0 && (await t.ev(`document.getElementById('aiwPintar').value`)) === 'fake-smart');
    t.check('edit: key field empty, saved state shown', (await t.ev(`document.getElementById('aiwKey').value`)) === '' && /tersimpan/i.test(await t.text('.aip-keystate')));
    await t.type('#aiwName', 'Fake Diubah');
    await t.click('#aiwSave');
    await t.waitFor(`!document.getElementById('aiWiz') && /Fake Diubah/.test(document.querySelector('#aiProv .aip-row[data-id="custom"] b').textContent)`, 20000, 'edited');
    t.check('edit: name saved, still Utama', /Utama/.test(await t.text('#aiProv .aip-row[data-id="custom"] .aip-main .tag')));

    // ---------------------------------------------------------------- cancel removes the draft and its key
    await t.click('#aiAdd');
    await t.click('#aiWiz .aip-preset[data-kind="custom"]');
    await t.type('#aiwUrl', fake.base + '/v1');
    await t.type('#aiwKey', FAKE_KEY);
    await t.click('#aiwKeySave');
    await t.waitFor(`AC.aiSettings.state().wiz && AC.aiSettings.state().wiz.hasKey`, 20000, 'draft key saved');
    const draftId = await t.ev(`AC.aiSettings.state().wiz.id`);
    t.check('draft: not listed while the wizard is open', !(await rows()).includes(draftId));
    // the draft's key vanished meanwhile (pruned after an hour): Simpan must not report success
    await t.ev(`AC.aiSettings.cli(['delete-key', ${JSON.stringify(draftId)}]).then(function () { return true; })`);
    await t.type('#aiwCepat', 'fake-fast');
    await t.click('#aiwSave');
    await t.waitFor(`/Kunci hilang/.test(Array.prototype.map.call(document.querySelectorAll('.toast'), function (x) { return x.textContent; }).join(' | '))`, 20000, 'lost key toast');
    t.check('lost key: wizard stays open, key state asks again', !!(await t.ev(`document.getElementById('aiWiz')`)) && !(await t.ev(`AC.aiSettings.state().wiz.hasKey`)));
    await t.click('#aiwCancel');
    await t.waitFor(`!document.getElementById('aiWiz')`, 5000, 'wizard closed');
    await t.ev(`AC.aiSettings.idle().then(function () { return true; })`);
    const snap = await t.ev(`AC.aiSettings.cli(['profiles']).then(function (d) { return d.profiles.map(function (p) { return p.id; }); })`);
    t.check('cancel: draft profile removed', !snap.includes(draftId), JSON.stringify(snap));

    // ---------------------------------------------------------------- health dot from `cli.py health` route rows
    await t.ev(`AC.engine.health()`);
    t.check('health: engine reports the route rows', await t.ev(`!!(AC.engine.lastHealth.data.ai && AC.engine.lastHealth.data.ai.route && AC.engine.lastHealth.data.ai.route.length === 2)`));
    await t.ev(`(function(){ var h = Array.prototype.filter.call(document.querySelectorAll('.sec-h'), function (x) { return x.textContent.indexOf('AI') === 0; })[0]; h.scrollIntoView({block: 'start'}); return true; })()`);
    await t.wait(150);
    await t.shot('v3_ai_providers_section');
    t.check('health: status host line names the provider', /Fake Diubah|Proxy lokal/.test(await t.text('#aiStatus') + ' ' + await t.ev(`document.querySelector('#aiStatus').parentNode.textContent`)));

    // ---------------------------------------------------------------- key this Windows user cannot decrypt
    const kpath = path.join(t.env.APPDATA, 'Klipora', 'ai_keys.bin');
    const kdoc = JSON.parse(fs.readFileSync(kpath, 'utf8'));
    kdoc.keys.custom = Buffer.from('not-a-dpapi-blob-of-this-user').toString('base64');
    fs.writeFileSync(kpath, JSON.stringify(kdoc));
    await t.ev(`AC.aiSettings.refresh().then(function () { return true; })`);
    await t.waitFor(`/Kunci tidak bisa dibuka/.test(document.querySelector('#aiProv .aip-row[data-id="custom"]').textContent)`, 20000, 'key error state');
    t.check('broken key: row warns instead of "Tersimpan, terenkripsi"', /Tidak bisa dibuka di user Windows ini/.test(await t.text('#aiProv .aip-row[data-id="custom"] .aip-detail')) &&
      !/Tersimpan, terenkripsi/.test(await t.text('#aiProv .aip-row[data-id="custom"] .aip-detail')));
    await scrollTo('#aiProv .aip-row[data-id="custom"]');
    await t.shot('v3_ai_providers_key_error');

    // ---------------------------------------------------------------- delete
    const delBtn = `Array.prototype.filter.call(document.querySelectorAll('#aiProv .aip-row[data-id="custom"] .aip-detail .btn'), function (x) { return x.textContent === 'Hapus' || /Yakin/.test(x.textContent); })[0]`;
    await t.ev(`(${delBtn}).click(), true`);
    await t.ev(`(${delBtn}).click(), true`);
    await t.waitFor(`!document.querySelector('#aiProv .aip-row[data-id="custom"]')`, 20000, 'deleted');
    t.check('delete: back to Grok only', JSON.stringify(await rows()) === '["grok_local"]');

    // ---------------------------------------------------------------- secrets
    t.check('key never in DOM, inputs, localStorage or panel log', await noKeyInPage());
    const roam = path.join(t.env.APPDATA, 'Klipora');
    const leaks = fs.existsSync(roam) ? fs.readdirSync(roam).filter((f) => fs.statSync(path.join(roam, f)).isFile() && fs.readFileSync(path.join(roam, f), 'utf8').includes(FAKE_KEY)) : ['(no folder)'];
    t.check('key never in plain text in %APPDATA%\\Klipora files', leaks.length === 0, leaks.join(', '));
    t.check('profiles file written in the sandbox', fs.existsSync(path.join(roam, 'ai_profiles.json')) && fs.existsSync(path.join(roam, 'ai_keys.bin')));
    const job = path.join(t.sandbox, 'temp');
    const scan = (d) => fs.existsSync(d) ? fs.readdirSync(d, { withFileTypes: true }).some((e) => e.isDirectory() ? scan(path.join(d, e.name)) : fs.readFileSync(path.join(d, e.name), 'utf8').includes(FAKE_KEY)) : false;
    t.check('key never in %TEMP% job files', !scan(job));
    t.check('no horizontal overflow (list)', await noOverflow());

    // ---------------------------------------------------------------- English UI (live switch, engine texts follow AC_LANG)
    await t.ev(`AC.i18n.set('en')`);
    await t.waitFor(`document.querySelector('#aiProv .aip-top b') && document.querySelector('#aiProv .aip-top b').textContent === 'AI providers'`, 10000, 'english section');
    await t.ev(`AC.aiSettings.refresh(true).then(function () { return true; })`);   // probe: health reasons in English too
    await t.waitFor(`/Local proxy/.test(document.querySelector('#aiProv .aip-chain').textContent)`, 30000, 'english chain');
    t.check('en: chain text', /^Order: Local proxy \(OpenAI-compatible\), then the built-in rules\.$/.test(await chain()), await chain());
    t.check('en: privacy note', /Transcripts are sent/.test(await t.text('#aiProv .alert-info')));
    await t.click('#aiProv .aip-row[data-id="grok_local"] .aip-main');
    await t.waitFor(`document.querySelector('#aiProv .aip-row[data-id="grok_local"] .aip-detail')`, 5000, 'english detail');
    t.check('en: detail labels', /Fast model/.test(await t.text('#aiProv .aip-row[data-id="grok_local"] .aip-kv')) &&
      /Test connection/.test(await t.text('#aiProv .aip-row[data-id="grok_local"] .aip-detail')));
    await t.click('#aiAdd');
    await t.waitFor(`document.querySelector('#aiWiz .aip-preset[data-kind="gemini"]')`, 5000, 'english wizard');
    await t.click('#aiWiz .aip-preset[data-kind="gemini"]');
    await t.waitFor(`document.getElementById('aiwUrl')`, 3000, 'english gemini form');
    t.check('en: wizard title + preset note', /Add AI provider/.test(await t.text('#aiWiz .aip-wiz-h')) && /free daily quota/.test(await t.text('#aiWiz')));
    t.check('en: Ollama preset label', (await t.text('#aiWiz .aip-preset[data-kind="ollama"]')) === 'Ollama (local)');
    const left = await t.idLeftovers('#aiProv');
    t.check('en: no Indonesian left in the AI providers section', left.length === 0, JSON.stringify(left).slice(0, 600));
    await scrollTo('#aiWiz');
    await t.shot('v3_ai_providers_wizard_en');
    await t.click('#aiwCancel');
    await t.ev(`AC.i18n.set('id')`);
    await t.waitFor(`document.querySelector('#aiProv .aip-top b') && document.querySelector('#aiProv .aip-top b').textContent === 'Penyedia AI'`, 10000, 'back to id');
  } finally {
    fake.srv.close();
  }
}
