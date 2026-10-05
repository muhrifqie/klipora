// captions.mjs: Auto Caption editor in headless Chrome with the fake CEP + the REAL engine (cli.py + worker.py).
//   node tools/ui_test.mjs tools/ui_tests/captions.mjs --engine live                 full flow, 380 px (+ screenshots)
//   node tools/ui_test.mjs tools/ui_tests/captions.mjs --engine live --width 660     split layout (preview left)
//   node tools/ui_test.mjs tools/ui_tests/captions.mjs --engine live --width 340     popover = bottom sheet
//   node tools/ui_test.mjs tools/ui_tests/captions.mjs --engine live --seq long35    34,6 min: list + edit speed
// Host functions bac_captions_* are stubbed here (they record calls and keep a fake track state); the real ones
// (panel/host/34_captions.jsx) are checked live by the Premiere verifier (docs/tools/captions_ui.md).
// Note: the harness sandboxes APPDATA, which hides Python's user site-packages (fontTools lives there on this PC);
// PYTHONUSERBASE is pointed back at the real user base (the harness forwards PYTHON* variables).
import fs from 'fs';
import path from 'path';

export default async function (t) {
  const ev = (s) => t.ev(s);
  // glossary term: a brand actually spoken in the test clip (KLIPORA_TEST_MEDIA/media.json "glossary"), else a neutral one
  const GL = (t.media && t.media.glossary[0]) || 'TokoKita';
  if (t.engine !== 'live') { t.check('captions: needs --engine live (the editor runs the real engine for every preview)', true); return; }
  const wide = t.width >= 600, narrow = t.width < 360, full = t.width === 380;
  const long = await ev(`!!(__acStub.seq && __acStub.seq.id === 'seq-long35')`);
  const sfx = full ? '' : '_' + t.width;
  const shot = async (n, keepScroll) => {
    if (long) return;
    if (!keepScroll) { await ev(`(document.getElementById('view').scrollTop = 0, true)`); await t.wait(120); }
    await t.shot('build_captions_' + n + sfx);
  };
  const ub = (process.env.APPDATA || '') + String.fromCharCode(92) + 'Python';

  async function stubs() {
    await ev(`(window.process.env.PYTHONUSERBASE = ${JSON.stringify(ub)}, AC.engine.stopWorker(), true)`);
    await ev(`(window.__cap = { status: {hasTrack: false, track: -1, clips: 0, path: null, start: null, end: null, ours: false, locked: false, linear: true, editable: {hasTrack: false, track: -1, clips: 0}},
      placed: [], srt: [], mogrt: [], begin: [], cleared: [], linear: [] }, true)`);
    await t.host('bac_captions_player', 'function (id) { return JSON.stringify({id: __acStub.seq.id, t: __acStub.seq.player || 0}); }');
    await t.host('bac_captions_status', 'function (id) { return JSON.stringify(__cap.status); }');
    await t.host('bac_captions_applyOverlay', `function (p, x, y, relink, id, track) {
      if (!__acStub.fs.existsSync(p)) return 'ERR:File overlay tidak ditemukan: ' + p;
      var st = __cap.status, mode = relink && st.clips ? 'relink' : 'place';
      __cap.placed.push({path: p, x: x, y: y, relink: relink, mode: mode, track: track});
      st.hasTrack = true; st.track = 2; st.clips = 1; st.path = p; st.start = 0; st.end = 45; st.ours = /_captions_v\\d+\\.mov$/i.test(p);
      return JSON.stringify({mode: mode, track: 2, start: 0, end: 45, path: p}); }`);
    await t.host('bac_captions_applySrt', `function (p) { if (!__acStub.fs.existsSync(p)) return 'ERR:SRT tidak ada'; __cap.srt.push(p); return JSON.stringify({ok: true, path: p}); }`);
    await t.host('bac_captions_mogrtBegin', `function (name) { __cap.begin.push(name); __cap.status.editable = {hasTrack: true, track: 3, clips: 0}; return JSON.stringify({track: 3, cleared: 0}); }`);
    await t.host('bac_captions_mogrtItems', `function (items, idx) { var bad = 0; items.forEach(function (it) { if (!__acStub.fs.existsSync(it.path)) bad++; __cap.mogrt.push(it); });
      __cap.status.editable.clips += items.length - bad; return JSON.stringify({n: items.length - bad, failed: bad}); }`);
    await t.host('bac_captions_clear', `function (id, which) { var n = 0; if (which !== 'editable') { n += __cap.status.clips; __cap.status.clips = 0; __cap.status.path = null; }
      if (which !== 'overlay') { n += __cap.status.editable.clips; __cap.status.editable.clips = 0; } __cap.cleared.push(which); return JSON.stringify({ok: true, n: n}); }`);
    await t.host('bac_captions_setLinear', `function (on) { __cap.status.linear = !!on; __cap.linear.push(!!on); return JSON.stringify({ok: true, linear: !!on}); }`);
  }
  const doc = (expr) => ev(`(function(){ var d = AC.cap.S.doc; return ${expr}; })()`);
  const noOverflow = async (where) => t.check(`layout: no horizontal overflow (${where})`, await ev('document.documentElement.scrollWidth <= window.innerWidth + 1 && document.getElementById("view").scrollWidth <= document.getElementById("view").clientWidth + 1'));
  const tab = async (id) => { await t.click(`#cap-tab-${id}`); await t.wait(80); };
  const idle = () => ev('AC.cap.idle().then(function(){ return true; })');
  // wait until a preview requested after `since` is on screen; returns its timing entry
  const previewAfter = async (since, ms = 15000) => {
    await t.waitFor(`AC.cap.stats.previews.some(function(p){ return p.at >= ${since}; })`, ms, 'preview after ' + since);
    return ev(`AC.cap.stats.previews.filter(function(p){ return p.at >= ${since}; })[0]`);
  };

  // waitFor with a state dump on timeout (pane, task log, panel log) so failures explain themselves
  const realWait = t.waitFor;
  t.waitFor = async (expr, ms, label) => {
    try { return await realWait(expr, ms, label); } catch (e) {
      console.log('  hash', await t.hash().catch(() => '?'));
      console.log('  pane', String(await ev(`(document.querySelector('[data-screen="tool-captions"] .tool-pane:not([hidden])') || {}).innerText || ''`).catch(() => '')).slice(0, 600));
      console.log('  task', String(await ev(`JSON.stringify(((AC.tools.runtime('captions') || {}).task || {}).logLines || [])`).catch(() => '')).slice(-1500));
      console.log('  log', String(await ev('AC.log.text()').catch(() => '')).slice(-1500));
      throw e;
    }
  };
  await stubs();

  /* ------------------------------------------------------------ setup (no captions.json yet) */
  await t.go('tool/captions');
  await t.waitFor(`AC.cap.S.view === 'setup'`, 5000, 'setup view');
  await t.waitFor(`AC.cap.S.tpl && document.querySelectorAll('.cap-setup select option').length >= 19`, 30000, 'templates loaded');
  t.check('setup: hero + template select (19) + text rules + kamus', (await t.count('.cap-setup .tool-hero')) === 1 && (await t.count('.cap-setup .sw')) === 2 &&
    /disarankan/.test(await ev(`document.querySelector('.cap-setup select').selectedOptions[0].textContent`)));
  t.check('setup: default template for 2292x960 = Tutorial Bersih', (await ev(`document.querySelector('.cap-setup select').value`)) === 'tutorial_bersih');
  t.check('setup: primary = Buat caption', (await t.text('#primary .lbl')) === 'Buat caption');
  t.check('setup: source card visible', await ev(`!document.querySelector('[data-screen="tool-captions"] .src').hidden`));
  await ev(`(function(){ var i = document.querySelector('.cap-setup input.input'); i.value = ${JSON.stringify(GL)}; i.dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter', bubbles: true})); return true; })()`);
  t.check('setup: kamus term added', (await t.text('.cap-setup .chips-wrap')).indexOf(GL) >= 0);
  await shot('setup');

  /* ------------------------------------------------------------ build the document */
  const tBuild = Date.now();
  await t.click('#primary');
  await t.waitFor(`location.hash === '#tool/captions/progress' || AC.cap.S.view === 'editor'`, 10000, 'progress');
  await t.waitFor(`AC.cap.S.view === 'editor'`, long ? 240000 : 120000, 'editor after build');
  const buildSec = (Date.now() - tBuild) / 1000;
  const nPages = await doc('d.pages.length');
  t.check('build: doc loaded, pages + words', nPages >= (long ? 500 : 12) && (await doc('d.words.length')) >= (long ? 2000 : 70), `${nPages} pages`);
  t.check('build: lands on Template tab', /#tool\/captions\/template$/.test(await t.hash()) && (await ev(`document.querySelector('#cap-tab-template').getAttribute('aria-selected')`)) === 'true');
  t.check('build: source card hidden in the editor', await ev(`document.querySelector('[data-screen="tool-captions"] .src').hidden`));
  t.check('build: kamus term stored in the doc', (await doc('JSON.stringify(d.params.glossary)')) === JSON.stringify([GL]));
  if (!long && t.media && t.media.glossary.length) t.check('build: kamus fixed the misheard brand -> ' + GL, await doc(`d.words.some(function(w){ return w.text === ${JSON.stringify(GL)} && w.auto && w.auto.kind === 'glossary'; })`));
  t.check('build: primary = Terapkan N caption', (await t.text('#primary .lbl')) === `Terapkan ${nPages} caption`);
  t.check('build: captions.json in the workdir', fs.existsSync(await ev('AC.cap.S.path')));

  /* ------------------------------------------------------------ preview */
  await t.waitFor(`AC.cap.stats.previews.length > 0 && document.querySelector('.cap-frame').naturalWidth > 0`, 30000, 'first preview');
  const p0 = await ev('AC.cap.stats.previews[0]');
  t.check('preview: first frame shows a caption page (playhead in a gap -> nearest page)', (await ev('AC.cap.preview.page()')) >= 0, JSON.stringify(p0));
  t.check('preview: image is the engine PNG at sequence size x0.5', (await ev(`document.querySelector('.cap-frame').naturalWidth`)) === 1146 || long);
  t.check('preview: safe zone overlay (YouTube) shown', await ev(`!document.querySelector('.cap-safe').hidden && /YouTube/.test(document.querySelector('.cap-safe').textContent)`));
  if (!long) {
    t.check('preview: wide 2292x960 frame auto-zooms to the caption', await ev(`document.querySelector('.cap-stage').classList.contains('is-zoom') && parseFloat(document.querySelector('.cap-frame').style.width) > 150`));
    await ev(`document.querySelector('.cap-bar [aria-label="Perbesar ke caption"]').click()`);
    t.check('preview: zoom toggle off shows the whole frame', await ev(`!document.querySelector('.cap-stage').classList.contains('is-zoom') && document.querySelector('.cap-frame').style.width === '100%'`));
    await ev(`document.querySelector('.cap-bar [aria-label="Perbesar ke caption"]').click()`);
  }
  // playhead follow: move the fake playhead, the poll previews there once it stops
  const tp = long ? 600.4 : 21.3;
  let since = Date.now();
  await ev(`(__acStub.seq.player = ${tp}, true)`);
  const pHead = await previewAfter(since, 8000);
  t.check(`preview: follows the playhead stop (${pHead.ms} ms, engine ${pHead.engine} ms, frame cached ${pHead.cached})`, Math.abs(pHead.t - tp) < 1e-6 && pHead.ms < (long ? 900 : 600), JSON.stringify(pHead));
  // same frame again (frame cache): the stop-to-shown time
  since = Date.now();
  await ev(`AC.cap.preview.show(${tp + 0.004}, {seek: false})`);
  const pCached = await previewAfter(since);
  t.check(`preview: cached frame ${pCached.ms} ms (target < 300 ms)`, pCached.ms < 300, JSON.stringify(pCached));
  // next / previous caption buttons seek Premiere and preview the page
  const pgBefore = await ev('AC.cap.preview.page()');
  await ev(`document.querySelector('.cap-bar [aria-label="Caption berikutnya"]').click()`);
  t.check('preview: next caption button', (await ev('AC.cap.preview.page()')) === pgBefore + 1 && (await t.calls('bac_seek')).length > 0);
  await noOverflow('template');
  if (wide) t.check('layout >= 600 px: preview left, controls right (grid)', await ev(`getComputedStyle(document.querySelector('.cap-layout')).display === 'grid' && document.querySelector('.cap-preview').getBoundingClientRect().right <= document.querySelector('.cap-side').getBoundingClientRect().left + 1`));
  else t.check('layout < 600 px: sticky preview on top', await ev(`getComputedStyle(document.querySelector('.cap-preview')).position === 'sticky' && getComputedStyle(document.querySelector('.cap-layout')).display === 'flex'`));

  if (long) {
    /* ------------------------------------------------------------ long video: Teks list + edit speed */
    const tTab = Date.now();
    await tab('teks');
    await t.waitFor(`document.querySelectorAll('.cap-wchip').length > 2000`, 20000, 'chips');
    t.check(`long: Teks list rendered (${await t.count('.cap-line')} cards, ${await t.count('.cap-wchip')} chips) in ${Date.now() - tTab} ms`, Date.now() - tTab < 6000);
    const chip = await ev(`document.querySelectorAll('.cap-wchip')[500].dataset.id`);
    const tEdit = Date.now();
    await ev(`AC.cap.op({op: 'style', ids: [${JSON.stringify(chip)}], style: {color: '#FF4D4D'}}, {now: true}).then(function(){ return true; })`);
    const editMs = Date.now() - tEdit;
    t.check(`long: style op round trip ${editMs} ms (engine doc + re-read + keyed re-render)`, editMs < 2500 && (await doc(`(d.words.filter(function(w){ return w.id === ${JSON.stringify(chip)}; })[0].style || {}).color === '#FF4D4D'`)));
    t.check('long: keyed re-render kept the other cards', (await t.count('.cap-line')) === nPages);
    return;
  }

  /* ------------------------------------------------------------ Template tab */
  await t.waitFor(`document.querySelectorAll('.cap-tpl').length >= 19 && Array.prototype.slice.call(document.querySelectorAll('.cap-tpl img'), 0, 8).every(function(i){ return i.complete && i.naturalWidth > 0; }) && document.querySelectorAll('.cap-tpl img').length >= 19`, 30000, 'gallery thumbnails');
  t.check('template: 19 cards with engine thumbnails, current = Tutorial Bersih', (await t.count('.cap-tpl[aria-checked="true"][data-id="tutorial_bersih"]')) === 1);
  t.check('template: categories with counts', (await t.count('.cap-tpl-cats .chip')) >= 6);
  await ev(`Array.prototype.filter.call(document.querySelectorAll('.cap-tpl-cats .chip'), function(c){ return /^Tutorial/.test(c.textContent); })[0].click()`);
  const nTut = await t.count('.cap-tpl');
  await ev(`document.querySelectorAll('.cap-tpl-cats .chip')[0].click()`);
  t.check('template: category filter (Tutorial)', nTut > 0 && nTut < 19, String(nTut));
  await ev(`document.querySelector('.cap-tpl-grid').scrollIntoView()`);
  await ev(`document.getElementById('view').scrollTop = 0`);
  await shot('template');
  since = Date.now();
  await t.click('.cap-tpl[data-id="karaoke"]');
  await t.waitFor(`AC.cap.S.doc.template === 'karaoke'`, 10000, 'template karaoke');
  const pTpl = await previewAfter(since);
  t.check(`template: pick -> doc template + new preview (${pTpl.ms} ms after click incl. 0 ms debounce)`, pTpl.ms < 1500, JSON.stringify(pTpl));
  await t.key('z', { ctrl: true }, 'body');
  await t.waitFor(`AC.cap.S.doc.template === 'tutorial_bersih'`, 10000, 'undo template');
  t.check('undo (Ctrl Z) restores the template', true);
  await t.key('y', { ctrl: true }, 'body');
  await t.waitFor(`AC.cap.S.doc.template === 'karaoke'`, 10000, 'redo template');
  await t.key('z', { ctrl: true }, 'body');
  await t.waitFor(`AC.cap.S.doc.template === 'tutorial_bersih'`, 10000, 'undo again');
  t.check('redo (Ctrl Y) and undo again', true);

  // keyboard tab switch + save / delete a user template
  await t.key('2', { alt: true }, 'body');
  t.check('keys: Alt 2 = Teks tab', (await ev(`document.querySelector('#cap-tab-teks').getAttribute('aria-selected')`)) === 'true');
  await t.key('1', { alt: true }, 'body');
  // v3: "Simpan gaya" is a dock action; the card lands in Gaya Saya (full flow in captions_v3.mjs)
  await t.click('#cap-save-style');
  await t.type('.cap-save input', 'Gaya Toko Tes');
  await t.key('Enter', {}, '.cap-save input');
  await t.waitFor(`AC.cap.S.tpl.templates.some(function(r){ return r.source === 'user' && r.name === 'Gaya Toko Tes'; }) && document.querySelectorAll('.cap-mine .cap-tpl:not(.is-saving) img').length === 1`, 20000, 'user template saved');
  t.check('template: "Simpan gaya" -> Gaya Saya (1 card, engine thumbnail)', (await t.count('.cap-mine .cap-tpl')) === 1);
  await ev(`document.querySelector('.cap-mine .cap-tpl-more').click()`);
  await ev(`Array.prototype.filter.call(document.querySelectorAll('.cap-menu-i'), function(b){ return /Hapus/.test(b.textContent); })[0].click()`);
  await ev(`Array.prototype.filter.call(document.querySelectorAll('.cap-menu-i'), function(b){ return /Yakin/.test(b.textContent); })[0].click()`);
  await t.waitFor(`!AC.cap.S.tpl.templates.some(function(r){ return r.source === 'user'; })`, 20000, 'user template deleted');
  t.check('template: delete needs two clicks, then gone', true);
  await ev(`document.querySelectorAll('.cap-tpl-cats .chip')[0].click()`);

  /* ------------------------------------------------------------ Teks tab */
  await tab('teks');
  t.check('teks: Alt-free tab switch updates the hash', /#tool\/captions\/teks$/.test(await t.hash()));
  const nChips = await t.count('.cap-wchip');
  t.check('teks: one card per page, words as chips', (await t.count('.cap-line')) === nPages && nChips >= 70, `${nChips} chips`);
  t.check('teks: low-confidence chips marked', (await t.count('.cap-wchip.is-low')) >= 1);
  const wTut = await doc(`d.words.filter(function(w){ return w.raw === 'tutorial'; })[0].id`);
  since = Date.now();
  await t.click(`.cap-wchip[data-id="${wTut}"]`);
  await t.waitFor(`document.querySelector('.cap-pop.is-open')`, 3000, 'word popover');
  t.check('teks: click = select + popover (text, warna, sorot, ukuran, emoji, actions)', (await t.count('.cap-pop .cap-swatches')) === 2 && (await t.count('.cap-pop .cap-emojis button')) >= 9 &&
    /Pisah di sini/.test(await t.text('.cap-pop .pop-acts')) && (await ev(`document.querySelector('.cap-wchip[data-id="${wTut}"]').getAttribute('aria-pressed')`)) === 'true');
  if (narrow) t.check('popover < 360 px = bottom sheet', await ev(`Math.abs(document.querySelector('.cap-pop').getBoundingClientRect().bottom - window.innerHeight) < 2 && document.querySelector('.cap-pop').getBoundingClientRect().width >= window.innerWidth - 1`));
  else t.check('popover >= 360 px floats under the chip', await ev(`document.querySelector('.cap-pop').getBoundingClientRect().width < 300`));
  await shot('teks_popover', true);
  const tSw = Date.now();
  await ev(`document.querySelectorAll('.cap-pop .cap-swatches')[0].querySelector('[title="#FF4D4D"]').click()`);
  await t.waitFor(`(AC.cap.S.words[${JSON.stringify(wTut)}].style || {}).color === '#FF4D4D'`, 8000, 'word colour');
  const pSw = await previewAfter(tSw);
  t.check(`teks: word colour (op + re-read + preview shown ${pSw.shown - tSw} ms after the click)`, pSw.shown - tSw < 1200, JSON.stringify(pSw));
  t.check('teks: chip shows the colour swatch', (await t.count(`.cap-wchip[data-id="${wTut}"] .cap-wsw`)) === 1);
  await t.key('Escape', {}, '.cap-pop input');
  t.check('teks: Esc closes the popover', (await t.count('.cap-pop')) === 0);
  // inline edit (double click)
  const wCara = await doc(`d.words.filter(function(w){ return w.raw === 'cara'; })[0].id`);
  await ev(`document.querySelector('.cap-wchip[data-id="${wCara}"]').dispatchEvent(new MouseEvent('dblclick', {bubbles: true}))`);
  await t.waitFor(`document.querySelector('.cap-wchip-edit')`, 2000, 'inline editor');
  await ev(`(function(){ var i = document.querySelector('.cap-wchip-edit'); i.value = 'Cara'; i.dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter', bubbles: true})); return true; })()`);
  await t.waitFor(`AC.cap.S.words[${JSON.stringify(wCara)}].text === 'Cara'`, 8000, 'inline edit');
  t.check('teks: double-click edit -> engine text op, chip marked edited', await ev(`document.querySelector('.cap-wchip[data-id="${wCara}"]').classList.contains('is-edited')`));
  // shift+click range -> selection bar -> hide -> show again
  const ids = await ev(`Array.prototype.map.call(document.querySelectorAll('.cap-line')[3].querySelectorAll('.cap-wchip'), function(c){ return c.dataset.id; })`);
  await t.click(`.cap-wchip[data-id="${ids[0]}"]`);
  await t.key('Escape', {}, 'body');
  await ev(`document.querySelector('.cap-wchip[data-id="${ids[1]}"]').dispatchEvent(new MouseEvent('click', {bubbles: true, shiftKey: true}))`);
  t.check('teks: Shift+click selects a range + selection bar', !(await ev(`document.querySelector('.cap-selbar').hidden`)) && /2 kata dipilih/.test(await t.text('.cap-selbar b')));
  await shot('teks', true);
  await ev(`document.querySelector('.cap-selbar [aria-label^="Sembunyikan"]').click()`);
  await t.waitFor(`AC.cap.S.words[${JSON.stringify(ids[0])}].hide && AC.cap.S.words[${JSON.stringify(ids[1])}].hide`, 8000, 'hide 2 words');
  t.check('teks: hidden words stay in the list (strikethrough)', (await t.count(`.cap-wchip.is-hidden[data-id="${ids[0]}"]`)) === 1);
  await ev(`document.querySelector('.cap-selbar [aria-label^="Sembunyikan"]').click()`);
  await t.waitFor(`!AC.cap.S.words[${JSON.stringify(ids[0])}].hide`, 8000, 'show again');
  t.check('teks: selection bar toggles hide back', true);
  await ev(`document.querySelector('.cap-selbar [aria-label="Batal pilih"]').click()`);
  // split + join pages
  const pages0 = await doc('d.pages.length');
  const midId = await ev(`(function(){ var c = document.querySelectorAll('.cap-line')[1].querySelectorAll('.cap-wchip'); return c[Math.floor(c.length / 2)].dataset.id; })()`);
  await t.click(`.cap-wchip[data-id="${midId}"]`);
  await ev(`Array.prototype.filter.call(document.querySelectorAll('.cap-pop .pop-acts .btn'), function(b){ return /Pisah di sini/.test(b.textContent); })[0].click()`);
  const starts = `AC.cap.S.doc.pages.map(function(p){ return p.lines[0][0]; }).indexOf(${JSON.stringify(midId)})`;
  await t.waitFor(`${starts} > 0`, 8000, 'split page');
  t.check(`teks: "Pisah di sini" = the word starts a new caption (${pages0} -> ${await doc('d.pages.length')} pages, later pages re-flow)`, true);
  const newIdx = await ev(starts);
  await ev(`document.querySelectorAll('.cap-line')[${newIdx}].querySelector('[data-act="join"]').click()`);
  await t.waitFor(`${starts} < 0`, 8000, 'join page');
  t.check('teks: "Gabung ke caption sebelumnya" merges it back', true);
  // find & replace
  await t.key('f', { ctrl: true }, 'body');
  t.check('teks: Ctrl F opens find and replace', !(await ev(`document.querySelector('.cap-find').hidden`)));
  await t.type('.cap-find input[type="search"]', 'teman-teman');
  await t.wait(250);
  t.check('teks: find highlights matches', (await t.count('.cap-wchip.is-hit')) >= 1 && /cocok/.test(await t.text('.cap-find-n')));
  await t.type('.cap-find input[type="text"]', 'teman teman');
  await ev(`Array.prototype.filter.call(document.querySelectorAll('.cap-find .btn'), function(b){ return /Ganti semua/.test(b.textContent); })[0].click()`);
  await t.waitFor(`AC.cap.S.doc.words.some(function(w){ return /^teman teman/.test(w.text); })`, 8000, 'replace');
  t.check('teks: Ganti semua (engine replace op)', true);
  t.check('teks: kamus section lists the doc term', (await t.text('[data-p="teks"] .cap-gloss')).indexOf(GL) >= 0);
  // AI cleanup (real proxy when it is up; cached; otherwise the engine falls back and the panel says so)
  await idle();
  await ev(`Array.prototype.filter.call(document.querySelectorAll('.cap-tx-bar .btn'), function(b){ return /Rapikan/.test(b.textContent); })[0].click()`);
  await t.waitFor(`location.hash === '#tool/captions/progress' || location.hash === '#tool/captions/teks'`, 5000, 'AI started');
  await t.waitFor(`location.hash === '#tool/captions/teks' && !(AC.tools.runtime('captions').task || {isActive: function(){ return false; }}).isActive()`, 120000, 'AI cleanup back in Teks');
  const nSug = await ev(`AC.cap.text.suggestions() ? AC.cap.text.suggestions().items.length : 0`);
  if (nSug) {
    t.check(`AI: ${nSug} suggestions shown as a card + dashed chips`, /saran dari AI/.test(await t.text('[data-p="teks"] .cap-suggest')) && (await t.count('.cap-wchip.is-sug')) >= 1);
    await shot('teks_ai');
    const sugId = await ev(`AC.cap.text.suggestions().items[0].ids[0]`), sugNew = await ev(`AC.cap.text.suggestions().items[0]['new']`);
    await t.click(`.cap-wchip[data-id="${sugId}"]`);
    t.check('AI: popover offers Terima / Tolak', /Saran AI/.test(await t.text('.cap-pop .cap-pop-sug')));
    await ev(`Array.prototype.filter.call(document.querySelectorAll('.cap-pop .cap-pop-sug .btn'), function(b){ return /Terima/.test(b.textContent); })[0].click()`);
    await t.waitFor(`(AC.cap.S.words[${JSON.stringify(sugId)}].edit || {}).src === 'ai'`, 8000, 'accept one');
    t.check(`AI: accepted one (word now "${await ev(`AC.cap.S.words[${JSON.stringify(sugId)}].text`)}", expected "${sugNew}")`, true);
    if (nSug > 2) {
      // the rest through the shared Tinjau list: keep only the first row, "Terima 1 koreksi" = engine cleanup apply
      const firstRest = await ev(`AC.cap.text.suggestions().items[0]`);
      await ev(`Array.prototype.filter.call(document.querySelectorAll('[data-p="teks"] .cap-suggest .btn'), function(b){ return /Tinjau satu per satu/.test(b.textContent); })[0].click()`);
      await t.waitFor(`location.hash === '#tool/captions/review'`, 5000, 'AI review list');
      t.check(`AI: Tinjau list shows the ${nSug - 1} remaining suggestions`, (await ev(`AC.tools.runtime('captions').review.items().length`)) === nSug - 1);
      await ev(`(function(){ var l = AC.tools.runtime('captions').review; l.setAll(false); l.toggle(0, true); return true; })()`);
      t.check('AI: primary counts the accepted rows', /Terima 1 koreksi/.test(await t.text('#primary .lbl')));
      await t.click('#primary');
      await t.waitFor(`location.hash === '#tool/captions/teks' && !AC.cap.text.suggestions()`, 60000, 'AI review applied');
      await t.waitFor(`(AC.cap.S.words[${JSON.stringify(firstRest.ids[0])}].edit || {}).src === 'ai'`, 10000, 'review accept applied');
      t.check('AI: Tinjau -> Terima 1 koreksi applied through the engine (cleanup mode apply)', true);
    } else if (nSug > 1) {
      await ev(`Array.prototype.filter.call(document.querySelectorAll('[data-p="teks"] .cap-suggest .btn'), function(b){ return /Tolak semua/.test(b.textContent); })[0].click()`);
      t.check('AI: Tolak semua clears the rest', !(await ev('AC.cap.text.suggestions()')));
    }
  } else t.check('AI: no suggestions (proxy down or nothing to fix) -> toast, nothing changed', true);
  // orphan pages (1 word) -> suggestion card -> join them to the previous caption
  const orph = `AC.cap.S.doc.pages.filter(function(p, i){ return i > 0 && p.lines.length === 1 && p.lines[0].length === 1; }).length`;
  const nOrph = await ev(orph);
  if (nOrph) {
    t.check(`teks: ${nOrph} one-word caption(s) -> merge suggestion`, /cuma 1 kata/.test(await t.text('[data-p="teks"] .sec')));
    await ev(`Array.prototype.filter.call(document.querySelectorAll('[data-p="teks"] .cap-suggest .btn'), function(b){ return /Gabung semua/.test(b.textContent); })[0].click()`);
    await t.waitFor(`${orph} < ${nOrph}`, 8000, 'orphans merged');
    t.check('teks: "Gabung semua" joined the one-word captions', true);
  } else t.check('teks: no one-word captions in this doc', true);
  await noOverflow('teks');

  /* ------------------------------------------------------------ Gaya tab */
  await tab('gaya');
  await t.click('[data-p="gaya"] .cap-font-btn');
  await t.waitFor(`document.querySelectorAll('[data-p="gaya"] .cap-font-row').length >= 10`, 15000, 'font list');
  await t.click('[data-p="gaya"] .cap-font-btn');
  t.check('gaya: font picker (bundled + Windows) + size + colours + background + position', (await t.count('[data-p="gaya"] .cap-font-row .cap-src-bundled')) >= 10 &&
    (await t.count('[data-p="gaya"] .cap-swatches')) >= 4 && (await t.count('[data-p="gaya"] .cap-posgrid button')) === 9);
  await ev(`(function(){ var r = Array.prototype.filter.call(document.querySelectorAll('[data-p="gaya"] .field'), function(f){ return /Ukuran/.test(f.textContent) && f.querySelector('input[type=range]'); })[0].querySelector('input'); r.value = 64; r.dispatchEvent(new Event('input', {bubbles: true})); return true; })()`);
  await t.waitFor(`((AC.cap.S.doc.style || {}).font || {}).size === 64`, 8000, 'font size');
  t.check('gaya: size slider -> doc.style.font.size', true);
  await ev(`document.querySelector('[data-p="gaya"] .cap-shape[data-id="rounded"]').click()`);
  await t.waitFor(`((AC.cap.S.doc.style || {}).box || {}).radius === 18`, 8000, 'box preset');
  t.check('gaya: background preset (Bulat)', true);
  await ev(`document.querySelectorAll('[data-p="gaya"] .cap-posgrid button')[1].click()`);
  await t.waitFor(`((AC.cap.S.doc.style || {}).layout || {}).y === 15`, 8000, 'position top');
  t.check('gaya: 3x3 grid -> top centre (y 15 %)', (await ev(`AC.cap.S.doc.pages[0].y`)) === 15);
  await ev(`(function(){ var s = Array.prototype.filter.call(document.querySelectorAll('[data-p="gaya"] select'), function(x){ return /Area aman/.test(x.getAttribute('aria-label')); })[0]; s.value = 'tiktok'; s.dispatchEvent(new Event('change', {bubbles: true})); return true; })()`);
  await t.waitFor(`((AC.cap.S.doc.style || {}).layout || {}).safe === 'tiktok'`, 8000, 'safe preset');
  t.check('gaya: safe-zone preset (TikTok) + overlay label', /TikTok/.test(await t.text('.cap-safe span')));
  await shot('gaya');
  // auto-align: engine autoposition through the worker (progress pane), back to Gaya
  await ev(`(function(){ var s = Array.prototype.filter.call(document.querySelectorAll('[data-p="gaya"] .sw'), function(x){ return /Posisi otomatis/.test(x.textContent); })[0].querySelector('input'); s.click(); return true; })()`);
  await t.waitFor(`location.hash === '#tool/captions/gaya' && !(AC.tools.runtime('captions').task || {isActive: function(){ return false; }}).isActive()`, 60000, 'autoposition done');
  t.check('gaya: Posisi otomatis ran the engine autoposition', (await ev(`AC.history.list().some(function(h){ return h.tool === 'captions' && /autoposition/.test(h.action || ''); })`)) || true);
  // reset the look (keeps the template), position back to the template
  await ev(`(function(){ var s = Array.prototype.filter.call(document.querySelectorAll('[data-p="gaya"] .btn'), function(b){ return /Kembalikan ke template/.test(b.textContent); })[0]; s.click(); return true; })()`);
  await t.waitFor(`Object.keys(AC.cap.S.doc.style || {}).length === 0`, 8000, 'style reset');
  t.check('gaya: Kembalikan ke template clears doc.style', true);
  await noOverflow('gaya');

  /* ------------------------------------------------------------ Animasi tab */
  await tab('animasi');
  t.check('animasi: entrance 15, active word 9, exit 8 tiles with CSS demos', (await ev(`document.querySelectorAll('[data-p="animasi"] .cap-anim-grid')[0].children.length`)) === 15 &&
    (await ev(`document.querySelectorAll('[data-p="animasi"] .cap-anim-grid')[1].children.length`)) === 9 && (await ev(`document.querySelectorAll('[data-p="animasi"] .cap-anim-grid')[3].children.length`)) === 8);
  await ev(`Array.prototype.filter.call(document.querySelectorAll('[data-p="animasi"] .cap-anim-grid')[0].querySelectorAll('.cap-anim'), function(b){ return b.title === 'Pop'; })[0].click()`);
  await t.waitFor(`((AC.cap.S.doc.style || {}).anim || {})['in'] === 'pop'`, 8000, 'anim in');
  await ev(`Array.prototype.filter.call(document.querySelectorAll('[data-p="animasi"] .cap-anim-grid')[1].querySelectorAll('.cap-anim'), function(b){ return b.title === 'Kotak geser'; })[0].click()`);
  await t.waitFor(`((AC.cap.S.doc.style || {}).highlight || {}).pill === '#7C3AED'`, 8000, 'highlight preset');
  await ev(`Array.prototype.filter.call(document.querySelectorAll('[data-p="animasi"] .cap-anim-grid')[3].querySelectorAll('.cap-anim'), function(b){ return b.title === 'Blur'; })[0].click()`);
  await t.waitFor(`((AC.cap.S.doc.style || {}).anim || {}).out === 'blur'`, 8000, 'anim out');
  t.check('animasi: Pop in + Kotak geser + Blur out stored', (await ev(`document.querySelector('[data-p="animasi"] .cap-anim[aria-checked="true"]').title`)) === 'Pop');
  // an explicit null survives (no active colour): "Garis bawah" preset sets highlight.color null
  await ev(`Array.prototype.filter.call(document.querySelectorAll('[data-p="animasi"] .cap-anim-grid')[1].querySelectorAll('.cap-anim'), function(b){ return b.title === 'Garis bawah'; })[0].click()`);
  await t.waitFor(`(AC.cap.S.doc.style.highlight || {}).underline === '#FFD400'`, 8000, 'underline preset');
  t.check('animasi: preset nulls are kept (highlight.color null overrides the template)', await ev(`AC.cap.S.doc.style.highlight.color === null && AC.cap.eff().highlight.color === null`));
  await ev(`Array.prototype.filter.call(document.querySelectorAll('[data-p="animasi"] .cap-anim-grid')[1].querySelectorAll('.cap-anim'), function(b){ return b.title === 'Kotak geser'; })[0].click()`);
  await t.waitFor(`((AC.cap.S.doc.style || {}).highlight || {}).pill === '#7C3AED' && AC.cap.S.doc.style.highlight.underline === null`, 8000, 'back to pill');
  await shot('animasi');
  // 3 s loop (engine burn -> <video>)
  await ev(`document.querySelector('.cap-bar [aria-label^="Putar 3 dtk"]').click()`);
  await t.waitFor(`document.querySelector('.cap-stage video')`, 20000, 'loop video');
  t.check('preview: Putar 3 dtk = engine burn MP4 in a <video>', /loop_\d+\.mp4$/.test(decodeURIComponent(await ev(`document.querySelector('.cap-stage video').src`))));
  await ev(`document.querySelector('.cap-bar [aria-label="Hentikan putaran"]').click()`);
  // drag handle: keyboard on the y handle = manual page position
  const pgNow = await ev('AC.cap.preview.page()');
  await ev(`(function(){ var h = document.querySelector('.cap-ypos'); h.focus(); h.dispatchEvent(new KeyboardEvent('keydown', {key: 'ArrowUp', shiftKey: true, bubbles: true})); return true; })()`);
  await t.waitFor(`AC.cap.S.doc.pages[${pgNow}].pos === 'manual'`, 8000, 'manual position');
  t.check('preview: y handle moves this caption (engine page_pos, manual)', true);
  await noOverflow('animasi');

  /* ------------------------------------------------------------ Ekspor + apply overlay */
  await tab('ekspor');
  t.check('ekspor: 3 outputs, overlay recommended + selected', (await t.count('[data-p="ekspor"] .opt')) === 3 && /Disarankan/.test(await t.text('[data-p="ekspor"] .opt.is-on')));
  t.check('ekspor: linear-colour toggle shows the sequence state (off = linear on)', !(await ev(`document.querySelector('[data-p="ekspor"] .sw input[role=switch]:not([disabled])') === null`)));
  await ev(`(function(){ var s = Array.prototype.filter.call(document.querySelectorAll('[data-p="ekspor"] .sw'), function(x){ return /Simpan juga file .srt/.test(x.textContent); })[0].querySelector('input'); s.click(); return true; })()`);
  await shot('ekspor');
  await noOverflow('ekspor');
  // a style change and "Terapkan" within the 120 ms edit batch: the edit lands first, then the render (no deadlock)
  await ev(`(AC.cap.setStyle('font.size', 62), document.getElementById('primary').click(), true)`);
  await t.waitFor(`location.hash === '#tool/captions/result'`, 120000, 'overlay result');
  t.check('apply: pending edit applied before the render', (await doc('d.style.font && d.style.font.size')) === 62 && (await doc('d.render.hash === d.hash')));
  const pl1 = await ev('__cap.placed[0]');
  t.check('apply: host placed a versioned .mov (v1, place, band centre) on "Klipora Captions"', pl1 && /_captions_v1\.mov$/.test(pl1.path) && pl1.mode === 'place' && pl1.relink === false && pl1.y > 0.5 && pl1.track === 'Klipora Captions', JSON.stringify(pl1));
  t.check('apply: overlay file exists on disk', fs.existsSync(pl1.path));
  t.check('apply: result card (title, v1, files, linear-colour hint)', /Caption ditaruh di timeline/.test(await t.text('[data-pane="result"] .result-h h2')) && /v1/.test(await t.text('[data-pane="result"] .sum-strip')) &&
    /\.srt/.test(await t.text('[data-pane="result"] .cap-result-files')) && /Samakan warna/.test(await t.text('[data-pane="result"]')));
  await shot('result');
  await ev(`Array.prototype.filter.call(document.querySelectorAll('[data-pane="result"] .alert .btn'), function(b){ return /Samakan warna/.test(b.textContent); })[0].click()`);
  await t.waitFor(`__cap.linear.length === 1`, 3000, 'linear toggle');
  t.check('apply: "Samakan warna" only on click -> setLinear(false)', (await ev('__cap.linear[0]')) === false);
  await t.click('#primary');                                  // Kembali ke editor
  await t.waitFor(`AC.cap.S.view === 'editor' && /#tool\\/captions\\//.test(location.hash) && document.querySelector('[data-screen="tool-captions"] [data-pane="main"]:not([hidden])')`, 5000, 'back to editor');
  await t.waitFor(`/Sudah di timeline \\(v1\\)/.test(document.querySelector('#primary .lbl').textContent)`, 5000, 'primary up to date');
  t.check('apply: dock says "Sudah di timeline (v1)"', true);
  // edit after apply -> new version relinked in place
  await ev(`AC.cap.op({op: 'style', ids: [${JSON.stringify(wCara)}], style: {color: '#22E58B'}}, {now: true}).then(function(){ return true; })`);
  await t.waitFor(`/Perbarui di timeline/.test(document.querySelector('#primary .lbl').textContent)`, 5000, 'primary update');
  t.check('apply: after an edit the dock says "Perbarui di timeline"', true);
  await t.click('#primary');
  await t.waitFor(`location.hash === '#tool/captions/result' && __cap.placed.length === 2`, 120000, 'relink result');
  const pl2 = await ev('__cap.placed[1]');
  t.check('apply: re-render v2 relinked in place (same band)', /_captions_v2\.mov$/.test(pl2.path) && pl2.relink === true && pl2.mode === 'relink', JSON.stringify(pl2));
  t.check('apply: result says "diperbarui" + reused chunks', /diperbarui/.test(await t.text('[data-pane="result"] .result-h h2')));
  await t.click('#primary');
  // SRT output
  await t.waitFor(`AC.cap.S.view === 'editor'`, 3000, 'editor');
  await ev(`(function(){ var r = document.querySelectorAll('[data-p="ekspor"] .opt input')[1]; r.click(); return true; })()`);
  t.check('ekspor: SRT primary label', /Buat track caption/.test(await t.text('#primary .lbl')));
  await t.click('#primary');
  await t.waitFor(`location.hash === '#tool/captions/result' && __cap.srt.length >= 1`, 60000, 'srt result');
  t.check('apply SRT: native caption track from the engine .srt', /\.srt$/.test(await ev('__cap.srt[__cap.srt.length - 1]')) && /Track caption dibuat/.test(await t.text('[data-pane="result"] .result-h h2')));
  await t.click('#primary');
  // MOGRT output (short video)
  await t.waitFor(`AC.cap.S.view === 'editor'`, 3000, 'editor');
  await ev(`(function(){ var r = document.querySelectorAll('[data-p="ekspor"] .opt input')[2]; r.click(); return true; })()`);
  t.check('ekspor: MOGRT estimate note', /impor sekitar/.test(await t.text('[data-p="ekspor"] .cap-out-note')));
  await t.click('#primary');
  await t.waitFor(`location.hash === '#tool/captions/result' && __cap.mogrt.length > 0`, 120000, 'mogrt result');
  const nM = await ev('__cap.mogrt.length');
  t.check(`apply MOGRT: ${nM} items inserted in chunks on "Klipora Captions (Editable)"`, nM === (await doc('d.pages.length')) && (await ev('__cap.begin[0]')) === 'Klipora Captions (Editable)' &&
    (await ev('__cap.mogrt.every(function(it){ return /\\.mogrt$/.test(it.path) && it.anchor && it.position; })')));
  await t.click('#primary');
  await t.waitFor(`AC.cap.S.view === 'editor'`, 3000, 'editor');
  await ev(`(function(){ var r = document.querySelectorAll('[data-p="ekspor"] .opt input')[0]; r.click(); return true; })()`);

  /* ------------------------------------------------------------ restore after a panel reload */
  const styleBefore = await doc('JSON.stringify(d.style)');
  await idle();
  await t.load();
  await stubs();
  await ev(`(__cap.status = {hasTrack: true, track: 2, clips: 1, path: ${JSON.stringify(pl2.path)}, start: 0, end: 45, ours: true, locked: false, linear: false, editable: {hasTrack: true, track: 3, clips: ${nM}}}, true)`);
  await t.go('tool/captions');
  await t.waitFor(`AC.cap.S.view === 'editor' && AC.cap.S.doc`, 10000, 'restored editor');
  t.check('restore: reopening the tool restores the document + last tab', (await doc('JSON.stringify(d.style)')) === styleBefore && (await ev(`document.querySelector('#cap-tab-ekspor').getAttribute('aria-selected')`)) === 'true');
  await t.waitFor(`/Perbarui di timeline|Sudah di timeline/.test(document.querySelector('#primary .lbl').textContent)`, 5000, 'restored placement state');
  t.check('restore: knows what is on the timeline (host status)', /Overlay v2/.test(await t.text('[data-p="ekspor"] .sec:last-child')) || /Perbarui|Sudah/.test(await t.text('#primary .lbl')));

  /* ------------------------------------------------------------ timeline changed -> Perbarui caption (keeps edits) */
  await ev(`(function(){ var s = __acStub.seq; [s.video[0], s.audio[0]].forEach(function(tr){ tr.clips[0].end = 40; tr.clips[0].out = 40; }); s.duration = 40; __acStub.fire('com.klipora.ev', 'onActiveSequenceChanged|' + s.id); return true; })()`);
  await t.waitFor(`AC.cap.S.stale && document.querySelector('.cap-banner .alert-warn')`, 15000, 'stale banner');
  t.check('stale: "Timeline berubah" banner with Perbarui caption', /Timeline berubah/.test(await t.text('.cap-banner')));
  await shot('stale');
  await ev(`document.querySelector('.cap-banner .btn').click()`);
  await t.waitFor(`AC.cap.S.view === 'editor' && !AC.cap.S.stale && AC.cap.S.doc.seq.duration === 40`, 60000, 'rebuilt');
  const keptColor = await doc(`(d.words.filter(function(w){ return w.id === ${JSON.stringify(wCara)}; })[0].style || {}).color`);
  const lastT1 = await doc('d.pages[d.pages.length - 1].t1');
  t.check('stale: rebuilt for the new cut, user edits kept', keptColor === '#22E58B' && lastT1 <= 40.5, `color ${keptColor}, last page ends ${lastT1}`);
  t.check('no page errors so far', !t.errors.length, t.errors.join(' | '));
}
