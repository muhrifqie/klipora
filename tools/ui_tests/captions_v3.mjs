// captions_v3.mjs: "Gaya Saya" library, last used look, Template gallery v3 and the "Gaya Pro" tab in headless Chrome
// with the fake CEP + the REAL engine (worker.py / cli.py).
//   node tools/ui_test.mjs tools/ui_tests/captions_v3.mjs --engine live                 380 px, full flow + screenshots
//   node tools/ui_test.mjs tools/ui_tests/captions_v3.mjs --engine live --width 300     narrow layout
//   node tools/ui_test.mjs tools/ui_tests/captions_v3.mjs --engine live --width 640     split layout (preview left)
// Covers: start from the last used look (setup card, text rules, build, badge, "Pakai template asli", undo), the same
// for a synthesized 1080x1920 sequence (vertical class is separate), Template tab (group chips, search, sprite strip on
// hover, static with reduced motion, Acak gaya, Gabung gaya), dock "Simpan gaya" -> card in Gaya Saya at once, card
// menu (Favorit, Duplikat, Ganti nama, Ekspor through cep.fs.showSaveDialogEx, Hapus two-step), Impor gaya
// (cep.fs.showOpenDialogEx), Gaya Pro (groups from the schema, font picker with categories + badges + sample, keep
// visual size, object effects, colour list editor, shape tiles, active-word looks, reset per group, undo), and that
// the last used look is written for the aspect class. Screenshots: docs/shots/v3_library_*.png.
import fs from 'fs';
import path from 'path';
import { ID_WORDS } from '../i18n_check.mjs';

const ID = new Set(ID_WORDS);

export default async function (t) {
  const ev = (s) => t.ev(s);
  if (t.engine !== 'live') { t.check('captions_v3: needs --engine live', true); return; }
  const W = t.width, full = W === 380, sfx = full ? '' : '_' + W;
  const shot = async (n, top) => { if (top !== false) { await ev(`(document.getElementById('view').scrollTop = 0, true)`); await t.wait(100); } await t.shot('v3_library_' + n + sfx); };
  const ub = (process.env.APPDATA || '') + String.fromCharCode(92) + 'Python';
  const noOverflow = async (where) => t.check(`layout ${W}px: no horizontal overflow (${where})`, await ev('document.documentElement.scrollWidth <= window.innerWidth + 1 && document.getElementById("view").scrollWidth <= document.getElementById("view").clientWidth + 1'));
  const tab = async (id) => { await t.click(`#cap-tab-${id}`); await t.wait(120); };
  const idle = () => ev('AC.cap.idle().then(function(){ return true; })');
  const doc = (expr) => ev(`(function(){ var d = AC.cap.S.doc; return ${expr}; })()`);
  // scroll the editor so the element sits below the sticky preview (narrow layout) / at the top (split layout)
  const scrollTo = (sel) => ev(`(function(){ var e = document.querySelector(${JSON.stringify(sel)}), v = document.getElementById('view'); if (!e) return false;
    var st = document.querySelector('.cap-layout > :first-child'), off = st && getComputedStyle(st).position === 'sticky' && st.getBoundingClientRect().width < v.clientWidth * 0.9 ? 8 : (st ? st.getBoundingClientRect().height + 8 : 8);
    v.scrollTop += e.getBoundingClientRect().top - v.getBoundingClientRect().top - off; return true; })()`);
  const realWait = t.waitFor;
  t.waitFor = async (expr, ms, label) => {
    try { return await realWait(expr, ms, label); } catch (e) {
      console.log('  hash', await t.hash().catch(() => '?'));
      console.log('  log', String(await ev('AC.log.text()').catch(() => '')).slice(-1500));
      throw e;
    }
  };
  async function stubs() {
    await ev(`(window.process.env.PYTHONUSERBASE = ${JSON.stringify(ub)}, AC.engine.stopWorker(), true)`);
    await t.host('bac_captions_player', 'function (id) { return JSON.stringify({id: __acStub.seq.id, t: 6}); }');
    await t.host('bac_captions_status', 'function (id) { return JSON.stringify({hasTrack: false, track: -1, clips: 0, path: null, ours: false, locked: false, linear: true, editable: {hasTrack: false, track: -1, clips: 0}}); }');
  }
  const worker = (action, params, seq) => ev(`AC.engine.worker({tool: 'captions', action: ${JSON.stringify(action)}, params: ${JSON.stringify(params)}, seq: ${JSON.stringify(seq || null)}, workdir: AC.settings.workRoot(), record: false}).promise`);
  const lastFile = () => ev(`AC.sys.readJSON(AC.sys.join(AC.sys.paths.appData, 'caption_last.json'), null)`);
  await stubs();

  /* ------------------------------------------------------------ last used look (horizontal) -> setup */
  const seeded = await worker('last_style', { op: 'set', template: 'stabilo_tutorial', style: { font: { size: 58 }, layout: { max_words: 5 } },
    params: { hide_fillers: false, numbers: true, censor: 'off', glossary: ['TokoKita'] }, w: 2292, h: 960, seq_name: 'Video kemarin' });
  t.check('last_style: seeded a horizontal look in the sandbox', seeded.aspect === 'horizontal' && seeded.changed, JSON.stringify(seeded));
  await t.go('tool/captions');
  await t.waitFor(`AC.cap.S.view === 'setup' && AC.cap.S.last && document.querySelector('.cap-last-card')`, 30000, 'setup with the last used look');
  t.check('setup: card "Gaya terakhir dipakai" replaces the template select', /Gaya terakhir dipakai/.test(await t.text('.cap-last-card')) &&
    /Stabilo Tutorial/.test(await t.text('.cap-last-card')) && await ev(`document.querySelector('.cap-setup select').hidden`));
  t.check('setup: text rules come from the last used look', await ev(`AC.tools.runtime('captions').ctx.state.hideFillers === false && AC.tools.runtime('captions').ctx.state.censor === 'off'`) &&
    !(await ev(`document.querySelector('.cap-setup .sw input').checked`)));
  await t.waitFor(`document.querySelector('.cap-last-card img') && document.querySelector('.cap-last-card img').naturalWidth > 0`, 15000, 'last look thumbnail');
  await shot('setup_last');
  // escape hatch and back
  await ev(`Array.prototype.filter.call(document.querySelectorAll('.cap-setup .btn'), function(b){ return /Pakai template asli/.test(b.textContent); })[0].click()`);
  t.check('setup: "Pakai template asli" shows the template select again', !(await ev(`document.querySelector('.cap-setup select').hidden`)) && !(await t.count('.cap-last-card')));
  await ev(`Array.prototype.filter.call(document.querySelectorAll('.cap-setup .btn'), function(b){ return /Pakai gaya terakhir/.test(b.textContent); })[0].click()`);
  t.check('setup: back to the last used look', (await t.count('.cap-last-card')) === 1);

  /* ------------------------------------------------------------ build from the last used look */
  await t.click('#primary');
  await t.waitFor(`AC.cap.S.view === 'editor' && AC.cap.S.doc`, 120000, 'editor after build');
  t.check('build: doc starts from the last used look (template + style)', (await doc('d.template')) === 'stabilo_tutorial' && (await doc('d.style.font.size')) === 58 &&
    (await doc('d.style.layout.max_words')) === 5 && (await doc('d.params.hide_fillers')) === false);
  await t.waitFor(`!document.querySelector('.cap-lastbar').hidden`, 5000, 'last bar');
  t.check('template: badge "Gaya terakhir dipakai" with "Pakai template asli"', /Gaya terakhir dipakai/.test(await t.text('.cap-lastbar')) && /Pakai template asli/.test(await t.text('.cap-lastbar')));
  await t.waitFor(`document.querySelectorAll('.cap-tpl[data-id] img').length >= 40`, 40000, 'gallery thumbnails');
  await t.wait(400);
  await shot('template');
  await ev(`document.querySelector('.cap-lastbar .btn').click()`);
  await t.waitFor(`AC.cap.S.doc.template === 'stabilo_tutorial' && Object.keys(AC.cap.S.doc.style || {}).length === 0`, 8000, 'template asli');
  t.check('template: "Pakai template asli" = same template, own style cleared, badge gone', await ev(`document.querySelector('.cap-lastbar').hidden`));
  await ev('AC.cap.undo()');
  await t.waitFor(`(AC.cap.S.doc.style.font || {}).size === 58`, 8000, 'undo');
  t.check('template: Ctrl Z brings the last used look back', !(await ev(`document.querySelector('.cap-lastbar').hidden`)));
  t.check('template: "Terakhir dipakai" card for this aspect', (await t.count('.cap-tpl-last')) === 1 && /Horizontal/.test(await t.text('.cap-tpl-last')));

  /* ------------------------------------------------------------ Template tab: groups, search, sprites */
  const nChips = await t.count('.cap-tpl-cats .chip');
  t.check(`template: filter chips Semua + Cocok + engine groups (${nChips})`, nChips >= 10 && /Shorts Viral/.test(await t.text('.cap-tpl-cats')));
  const nAll = await ev(`document.querySelectorAll('.cap-tpl-grid:not(.cap-mine):not(.cap-lastgrid) .cap-tpl').length`);
  await ev(`document.querySelector('.cap-tpl-cats .chip[data-g="podcast"]').click()`);
  const nPod = await ev(`document.querySelectorAll('.cap-tpl-grid:not(.cap-mine):not(.cap-lastgrid) .cap-tpl').length`);
  t.check(`template: group chip Podcast filters (${nPod} of ${nAll})`, nAll >= 45 && nPod >= 3 && nPod < 10);
  await ev(`document.querySelector('.cap-tpl-cats .chip[data-g="all"]').click()`);
  await t.type('.cap-tpl-search', 'neon');
  await t.wait(250);
  const found = await ev(`Array.prototype.map.call(document.querySelectorAll('.cap-tpl-grid:not(.cap-mine) .cap-tpl[data-id]'), function(c){ return c.dataset.id; })`);
  t.check('template: search "neon" finds the neon looks only', found.length >= 2 && found.every((id) => /neon/.test(id)), JSON.stringify(found));
  await t.type('.cap-tpl-search', '');
  await t.wait(200);
  // animated sprite strip on hover (cached strips via the worker, the rest rendered in a background process)
  await t.waitFor(`(function(){ var c = document.querySelector('.cap-tpl[data-id="kuning_3d"]'); c.dispatchEvent(new MouseEvent('mouseenter')); return c.classList.contains('is-play'); })()`, 60000, 'sprite ready');
  const spr = await ev(`(function(){ var s = document.querySelector('.cap-tpl[data-id="kuning_3d"] .cap-tpl-spr'), cs = getComputedStyle(s);
    return {bg: cs.backgroundImage.slice(0, 12), size: s.style.backgroundSize, play: cs.animationPlayState, op: cs.opacity, tf: s.style.animationTimingFunction}; })()`);
  t.check('template: hover plays the sprite strip (steps, running)', spr.bg.indexOf('url(') === 0 && spr.size === '1600% 100%' && spr.play === 'running' && spr.op === '1' && /steps\(16/.test(spr.tf), JSON.stringify(spr));
  await ev(`(document.querySelector('.cap-tpl[data-id="kuning_3d"]').scrollIntoView({block: 'center'}), true)`);
  await t.wait(500);
  await t.shot('v3_library_sprite_hover' + sfx);
  await ev(`document.querySelector('.cap-tpl[data-id="kuning_3d"]').dispatchEvent(new MouseEvent('mouseleave'))`);
  await ev(`(document.documentElement.setAttribute('data-motion', 'reduce'), document.querySelector('.cap-tpl[data-id="neon_gamer"]').dispatchEvent(new MouseEvent('mouseenter')), true)`);
  t.check('template: reduced motion keeps the static thumbnail', !(await ev(`document.querySelector('.cap-tpl[data-id="neon_gamer"]').classList.contains('is-play')`)) &&
    (await ev(`getComputedStyle(document.querySelector('.cap-tpl[data-id="neon_gamer"] .cap-tpl-spr')).display`)) === 'none');
  await ev(`(document.documentElement.removeAttribute('data-motion'), true)`);
  await noOverflow('template');
  // keyboard: one Tab stop per grid (roving tabindex), arrows move by the laid-out column count
  const G = `document.querySelector('.cap-tpl-grid:not(.cap-mine):not(.cap-lastgrid)')`;
  t.check('template: roving tabindex (1 Tab stop in the built-in grid)', (await ev(`Array.prototype.filter.call(${G}.querySelectorAll('.cap-tpl'), function(c){ return c.tabIndex === 0; }).length`)) === 1);
  const nav = await ev(`(function(){ var g = ${G}, c = g.querySelectorAll('.cap-tpl'), cols = 0; while (cols < c.length && c[cols].offsetTop === c[0].offsetTop) cols++;
    c[0].focus(); c[0].dispatchEvent(new KeyboardEvent('keydown', {key: 'ArrowRight', bubbles: true, cancelable: true}));
    var r = document.activeElement === c[1];
    c[1].dispatchEvent(new KeyboardEvent('keydown', {key: 'ArrowDown', bubbles: true, cancelable: true}));
    var d = document.activeElement === c[1 + cols];
    document.activeElement.dispatchEvent(new KeyboardEvent('keydown', {key: 'End', bubbles: true, cancelable: true}));
    var e = document.activeElement === c[c.length - 1] && c[c.length - 1].tabIndex === 0;
    document.activeElement.dispatchEvent(new MouseEvent('mouseleave')); document.activeElement.blur();
    return {r: r, d: d, e: e, cols: cols}; })()`);
  t.check(`template: ArrowRight / ArrowDown (${nav.cols} cols) / End move the focus`, nav.r && nav.d && nav.e, JSON.stringify(nav));
  t.check('gaya: quick-look chips are role=radio without aria-pressed', (await ev(`document.querySelectorAll('.cap-looks .chip[aria-pressed]').length`)) === 0);

  /* ------------------------------------------------------------ dock "Simpan gaya" -> Gaya Saya at once */
  t.check('dock: "Simpan gaya" next to the primary action', /Simpan gaya/.test(await ev(`document.getElementById('cap-save-style').getAttribute('title') + document.getElementById('cap-save-style').textContent`)));
  await t.click('#cap-save-style');
  t.check('save: sheet with a suggested name', !(await ev(`document.querySelector('.cap-save').hidden`)) && /saya$/.test(await ev(`document.querySelector('.cap-save input').value`)));
  await shot('save_sheet', false);
  await t.type('.cap-save input', 'Gaya Toko Tes');
  const tSave = Date.now();
  await t.key('Enter', {}, '.cap-save input');
  await t.waitFor(`document.querySelectorAll('.cap-mine .cap-tpl').length === 1`, 2000, 'card at once');
  const msCard = Date.now() - tSave;
  t.check(`save: card in Gaya Saya at once (${msCard} ms, placeholder)`, msCard < 1000 && (await ev(`document.querySelector('.cap-mine .cap-tpl').classList.contains('is-saving') || !!document.querySelector('.cap-mine .cap-tpl img')`)));
  await t.waitFor(`document.querySelector('.cap-mine .cap-tpl:not(.is-saving) img')`, 20000, 'saved card');
  await scrollTo('.cap-mine');
  await t.waitFor(`document.querySelector('.cap-mine .cap-tpl img').naturalWidth > 0`, 10000, 'saved card thumbnail');
  const savedId = await ev(`document.querySelector('.cap-mine .cap-tpl').dataset.id`);
  await t.waitFor(`AC.cap.S.tpl.templates.some(function(r){ return r.id === ${JSON.stringify(savedId)} && r.source === 'user'; })`, 10000, 'picker list refreshed');
  t.check('save: engine row (user template, own thumbnail), picker list refreshed', /gaya_toko_tes/.test(savedId));
  // a card saved after the first Template visit gets its own animated strip too (incremental gallery anim)
  await t.waitFor(`(function(){ var c = document.querySelector('.cap-mine [data-id="${savedId}"]'); if (!c) return false; c.dispatchEvent(new MouseEvent('mouseenter')); var ok = c.classList.contains('is-play') && !!c.querySelector('.cap-tpl-spr').dataset.png; c.dispatchEvent(new MouseEvent('mouseleave')); return ok; })()`, 90000, 'saved card sprite');
  t.check('save: new Gaya Saya card plays its sprite strip on hover', true);
  // menu: favourite, duplicate, rename, export, delete
  const menu = async (id, label) => {
    await ev(`document.querySelector('.cap-mine [data-id="${id}"] .cap-tpl-more').click()`);
    await t.waitFor(`document.querySelector('.cap-menu')`, 2000, 'menu');
    return ev(`(function(){ var b = Array.prototype.filter.call(document.querySelectorAll('.cap-menu .cap-menu-i'), function(x){ return x.textContent.indexOf(${JSON.stringify(label)}) >= 0; })[0]; b.click(); return true; })()`);
  };
  await ev(`document.querySelector('.cap-mine [data-id="${savedId}"] .cap-tpl-more').click()`);
  t.check('menu: Ganti nama, Duplikat, Favorit, Ekspor, Hapus', (await ev(`Array.prototype.map.call(document.querySelectorAll('.cap-menu .cap-menu-i'), function(x){ return x.textContent; }).join('|')`)) === 'Ganti nama|Duplikat|Jadikan favorit|Ekspor|Hapus');
  await scrollTo('.cap-mine');
  await t.shot('v3_library_menu' + sfx);
  await t.key('Escape');
  await t.waitFor(`!document.querySelector('.cap-menu')`, 2000, 'menu closed by Esc');
  await menu(savedId, 'Duplikat');
  await t.waitFor(`document.querySelectorAll('.cap-mine .cap-tpl:not(.is-saving)').length === 2`, 15000, 'duplicate');
  const dupId = await ev(`Array.prototype.filter.call(document.querySelectorAll('.cap-mine .cap-tpl'), function(c){ return c.dataset.id !== ${JSON.stringify(savedId)}; })[0].dataset.id`);
  t.check('menu: Duplikat -> "(salinan)"', /salinan/.test(await t.text(`.cap-mine [data-id="${dupId}"] .cap-tpl-meta b`)));
  await menu(savedId, 'favorit');
  await t.waitFor(`document.querySelector('.cap-mine .cap-tpl').dataset.id === ${JSON.stringify(savedId)} && document.querySelector('.cap-mine [data-id="${savedId}"] .cap-fav')`, 10000, 'favourite first');
  t.check('menu: Favorit -> star + favourites first', true);
  await menu(dupId, 'Ganti nama');
  await t.waitFor(`document.querySelector('.cap-tpl-rename')`, 2000, 'rename input');
  await ev(`(function(){ var i = document.querySelector('.cap-tpl-rename'); i.value = 'Gaya Promo Merah'; i.dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter', bubbles: true})); return true; })()`);
  await t.waitFor(`/Gaya Promo Merah/.test(document.querySelector('.cap-mine [data-id="${dupId}"] .cap-tpl-meta b').textContent)`, 10000, 'renamed');
  t.check('menu: Ganti nama inline (Enter saves)', true);
  // export through the CEP save dialog (stubbed)
  const outPath = path.join(t.sandbox, 'ekspor', 'gaya toko.json');
  await ev(`(window.cep = window.cep || {}, window.cep.fs = window.cep.fs || {}, window.cep.fs.showSaveDialogEx = function (title, dir, types, name) { window.__saveAsk = [title, types, name]; return {err: 0, data: ${JSON.stringify(outPath)}}; }, true)`);
  await menu(savedId, 'Ekspor');
  await t.waitFor(`AC.sys.exists(${JSON.stringify(outPath)})`, 10000, 'export file');
  const ex = JSON.parse(fs.readFileSync(outPath, 'utf8'));
  t.check('menu: Ekspor -> schema-versioned JSON with fonts (save dialog asked for .json)', ex.kind === 'autocut.caption_styles' && ex.v === 1 && ex.styles.length === 1 && ex.fonts.length >= 1 &&
    (await ev('JSON.stringify(window.__saveAsk[1])')) === '["json"]' && /Gaya Toko Tes\.json$/.test(await ev('window.__saveAsk[2]')));
  // delete (two clicks)
  await menu(dupId, 'Hapus');
  t.check('menu: Hapus asks first', /Yakin/.test(await t.text('.cap-menu')));
  await ev(`Array.prototype.filter.call(document.querySelectorAll('.cap-menu .cap-menu-i'), function(x){ return /Yakin/.test(x.textContent); })[0].click()`);
  await t.waitFor(`!document.querySelector('.cap-mine [data-id="${dupId}"]')`, 10000, 'deleted');
  t.check('menu: Hapus on the second click', (await t.count('.cap-mine .cap-tpl')) === 1);
  // import through the CEP open dialog (stubbed): same file -> new card with a de-duplicated name
  await ev(`(window.cep.fs.showOpenDialogEx = function () { return {err: 0, data: [${JSON.stringify(outPath)}]}; }, true)`);
  await ev(`Array.prototype.filter.call(document.querySelectorAll('[data-p="template"] .sec-h .linkbtn'), function(b){ return /Impor gaya/.test(b.textContent); })[0].click()`);
  await t.waitFor(`document.querySelectorAll('.cap-mine .cap-tpl:not(.is-saving)').length === 2`, 15000, 'imported');
  t.check('impor: new card, name de-duplicated ("(2)")', /Gaya Toko Tes \(2\)/.test(await t.text('.cap-mine')));
  // pick a Gaya Saya card
  await ev(`document.querySelector('.cap-mine [data-id="${savedId}"]').click()`);
  await t.waitFor(`AC.cap.S.doc.template === ${JSON.stringify(savedId)}`, 8000, 'pick user style');
  t.check('pick: Gaya Saya card = op template (ring on the card)', (await ev(`document.querySelector('.cap-mine [data-id="${savedId}"]').getAttribute('aria-checked')`)) === 'true');
  await scrollTo('.cap-mine');
  await t.wait(200);
  await t.shot('v3_library_gaya_saya' + sfx);

  /* ------------------------------------------------------------ Acak gaya / Gabung gaya */
  const before = await doc('JSON.stringify([d.template, d.style])');
  await ev(`Array.prototype.filter.call(document.querySelectorAll('.cap-tpl-acts .btn'), function(b){ return /Acak gaya/.test(b.textContent); })[0].click()`);
  await t.waitFor(`JSON.stringify([AC.cap.S.doc.template, AC.cap.S.doc.style]) !== ${JSON.stringify(before)}`, 15000, 'random look');
  t.check('acak: random readable look applied as one undoable step', Object.keys(await doc('d.style')).length > 2 && (await ev('AC.cap.canUndo()')));
  await ev(`Array.prototype.filter.call(document.querySelectorAll('.cap-tpl-acts .btn'), function(b){ return /Gabung gaya/.test(b.textContent); })[0].click()`);
  t.check('gabung: panel with A/B selects + 6 parts', !(await ev(`document.querySelector('.cap-mix').hidden`)) && (await t.count('.cap-mix select')) === 2 && (await t.count('.cap-mix-row')) === 6);
  await ev(`(function(){ var s = document.querySelectorAll('.cap-mix select'); s[0].value = 'tutorial_bersih'; s[0].dispatchEvent(new Event('change')); s[1].value = 'kuning_3d'; s[1].dispatchEvent(new Event('change')); return true; })()`);
  await scrollTo('.cap-mix');
  await t.shot('v3_library_mix' + sfx);
  await ev(`Array.prototype.filter.call(document.querySelectorAll('.cap-mix .btn'), function(b){ return /Gabungkan/.test(b.textContent); })[0].click()`);
  await t.waitFor(`AC.cap.S.doc.template === 'tutorial_bersih' && ((AC.cap.S.doc.style || {}).style || {}).extrude`, 15000, 'mix applied');
  t.check('gabung: base A + Warna dan efek from B (3D of Kuning 3D)', true);
  await noOverflow('template + mix');

  /* ------------------------------------------------------------ Gaya Pro */
  await tab('gaya');
  await t.wait(300);
  await shot('gaya_top');
  const nGroups = await t.count('[data-p="gaya"] .cap-grp');
  t.check(`gaya: ${nGroups} collapsible groups from the engine schema`, nGroups === 8 && /Bayangan dan 3D/.test(await t.text('[data-p="gaya"] .cap-pro')));
  t.check('gaya: one-click effects row', (await t.count('[data-p="gaya"] .cap-fx .chip')) >= 10);
  await t.waitFor(`document.querySelector('.cap-font-btn .cap-src')`, 15000, 'font catalog');
  await t.click('.cap-font-btn');
  await t.waitFor(`document.querySelectorAll('.cap-font-row').length >= 30`, 15000, 'font list');
  const fr = await ev(`(function(){ var r = document.querySelectorAll('.cap-font-row'); return {n: r.length, bundled: document.querySelectorAll('.cap-font-row .cap-src-bundled').length, sys: document.querySelectorAll('.cap-font-row .cap-src-system').length, chips: document.querySelectorAll('.cap-font-cats .chip').length}; })()`);
  t.check(`font picker: ${fr.n} families, Bawaan + Windows badges, 8 category chips`, fr.bundled >= 30 && fr.sys >= 1 && fr.chips === 8, JSON.stringify(fr));
  await scrollTo('[data-p="gaya"] .cap-fontpick');
  await t.wait(500);
  await t.shot('v3_library_fontpicker' + sfx, false);
  await ev(`Array.prototype.filter.call(document.querySelectorAll('.cap-font-cats .chip'), function(c){ return c.textContent === 'Tulisan tangan'; })[0].click()`);
  const hand = await ev(`Array.prototype.map.call(document.querySelectorAll('.cap-font-row'), function(r){ return r.dataset.base; })`);
  t.check('font picker: category filter (Tulisan tangan)', hand.length >= 2 && hand.indexOf('Pacifico') >= 0 && hand.indexOf('Anton') < 0, JSON.stringify(hand.slice(0, 8)));
  await ev(`document.querySelectorAll('.cap-font-cats .chip')[0].click()`);
  await t.type('.cap-font-panel input[type=search]', 'anton');
  await t.wait(200);
  await t.key('ArrowDown', {}, '.cap-font-panel input[type=search]');
  t.check('font picker: ArrowDown from the search goes into the list', await ev(`document.activeElement.classList.contains('cap-font-row')`));
  await t.key('Escape');
  t.check('font picker: Esc closes it, focus back on the font button', await ev(`document.querySelector('.cap-font-panel').hidden && document.activeElement === document.querySelector('.cap-font-btn')`));
  await t.click('.cap-font-btn');
  await t.waitFor(`document.querySelector('.cap-font-row[data-base="Anton"]')`, 10000, 'font list again');
  const capSize0 = await ev(`AC.cap.eff().font.size`), fam0 = await ev('AC.cap.eff().font.family');
  await ev(`document.querySelector('.cap-font-row[data-base="Anton"]').click()`);
  await t.waitFor(`((AC.cap.S.doc.style || {}).font || {}).family === 'Anton'`, 8000, 'font Anton');
  t.check('font picker: after picking, focus is on the font button (not lost)', await ev(`document.activeElement === document.querySelector('.cap-font-btn')`));
  const size1 = await doc('d.style.font.size');
  t.check(`font picker: Anton keeps the visual size (${fam0} ${capSize0} px -> ${size1} px)`, size1 && size1 !== capSize0, String(size1));
  t.check('font picker: live sample uses the real font file', /acf Anton/.test(await ev(`document.querySelector('.cap-font-sample').style.fontFamily`)) &&
    await ev(`document.fonts.check('20px "acf Anton"')`));
  // object effect: 3D on/off + sub-field
  const ex3d = '[data-p="gaya"] [data-path="style.extrude"] input';
  if (await ev(`!AC.cap.eff().style.extrude`)) { await t.click(ex3d); await t.waitFor(`(AC.cap.S.doc.style.style || {}).extrude`, 8000, '3D on'); }
  t.check('gaya: 3D sub-fields visible when on', !(await ev(`document.querySelector('[data-p="gaya"] [data-path="style.extrude.depth"]').hidden`)));
  await ev(`(function(){ var r = document.querySelector('[data-p="gaya"] [data-path="style.extrude.depth"] input'); r.value = 18; r.dispatchEvent(new Event('input', {bubbles: true})); return true; })()`);
  await t.waitFor(`AC.cap.S.doc.style.style.extrude.depth === 18`, 8000, '3D depth');
  t.check('gaya: slider writes the sub-field (3D depth 18 px)', true);
  // copy count (engine schema, clamped 0..32: every copy is one ASS event per word piece)
  const stepsSel = '[data-p="gaya"] [data-path="style.extrude.steps"]';
  t.check('gaya: 3D copy count slider (0 = otomatis, max 32)', await ev(`(function(){ var r = document.querySelector('${stepsSel} input[type=range]'); return !!r && !document.querySelector('${stepsSel}').hidden && +r.max === 32 && +r.min === 0; })()`));
  if (full) { await ev(`(function(){ var e = document.querySelector('${stepsSel}'), v = document.getElementById('view'), g = e.closest('details'); if (g) g.open = true; v.scrollTop += e.getBoundingClientRect().top - v.getBoundingClientRect().top - 330; return [v.scrollTop, e.getBoundingClientRect().top]; })()`); await t.wait(250); await t.shot('v3_engine_gaya_steps'); }
  await t.click(ex3d);
  await t.waitFor(`AC.cap.S.doc.style.style.extrude === null`, 8000, '3D off');
  t.check('gaya: switching an effect off writes an explicit null', await ev(`document.querySelector('[data-p="gaya"] [data-path="style.extrude.depth"]').hidden`));
  // gradient + colour list editor
  await t.click('[data-p="gaya"] [data-path="style.gradient"] input');
  await t.waitFor(`(AC.cap.S.doc.style.style || {}).gradient && !document.querySelector('[data-path="style.gradient.colors"]').hidden`, 8000, 'gradient on');
  await t.click('[data-p="gaya"] [data-path="style.gradient.colors"] .cap-cc-add');
  await t.waitFor(`AC.cap.S.doc.style.style.gradient.colors.length === 3`, 8000, 'third stop');
  t.check('gaya: gradient editor adds a colour stop (3)', (await t.count('[data-path="style.gradient.colors"] .cap-cc')) === 3);
  // shape tiles + active-word looks
  await ev(`document.querySelector('[data-p="gaya"] .cap-shape[data-id="marker"]').click()`);
  await t.waitFor(`(AC.cap.S.doc.style.box || {}).shape === 'marker'`, 8000, 'marker shape');
  t.check('gaya: shape tile Stabilo -> box.shape marker', (await ev(`document.querySelector('.cap-shape[data-id="marker"]').getAttribute('aria-checked')`)) === 'true');
  await ev(`document.querySelector('[data-p="gaya"] .cap-looks:not(.cap-fx) .chip[data-id="glow"]').click()`);
  await t.waitFor(`((AC.cap.S.doc.style.highlight || {}).glow)`, 8000, 'glow look');
  t.check('gaya: active-word look "Kata menyala"', true);
  await scrollTo('[data-p="gaya"] .cap-grp[data-g="latar"]');
  await t.wait(300);
  await t.shot('v3_library_gaya_pro' + sfx, false);
  await scrollTo('[data-p="gaya"] .cap-grp[data-g="isi"]');
  await t.wait(200);
  await t.shot('v3_library_gaya_isi' + sfx, false);
  t.check('gaya: changed groups are marked "Diubah"', (await t.count('[data-p="gaya"] .cap-grp-mod:not([hidden])')) >= 3);
  // reset one group (Huruf), then undo it
  await ev(`document.querySelector('[data-p="gaya"] .cap-grp[data-g="huruf"] .cap-grp-foot .btn').click()`);
  await t.waitFor(`!(AC.cap.S.doc.style || {}).font`, 8000, 'reset huruf');
  t.check('gaya: Reset huruf removes only the font keys', !!(await doc('d.style.box')));
  await ev('AC.cap.undo()');
  await t.waitFor(`((AC.cap.S.doc.style || {}).font || {}).family === 'Anton'`, 8000, 'undo reset');
  t.check('gaya: Ctrl Z restores the group', true);
  await noOverflow('gaya');

  /* ------------------------------------------------------------ last used look written for this aspect */
  await idle();
  await t.waitFor(`(function(){ var f = AC.sys.readJSON(AC.sys.join(AC.sys.paths.appData, 'caption_last.json'), null); return f && f.horizontal && f.horizontal.style && f.horizontal.style.font && f.horizontal.style.font.family === 'Anton'; })()`, 8000, 'last style written');
  const lf = await lastFile();
  t.check('last_style: explicit changes are remembered for "horizontal" (template, style, text rules)', lf.horizontal.template === (await doc('d.template')) && lf.horizontal.params.hide_fillers === false && !lf.vertical);

  /* ------------------------------------------------------------ English UI: Teks + Gaya (+ Template) tabs, popover, save sheet */
  // "per" is in the Indonesian word list but is English too ("Words per line"), "Bebas" is a font (Bebas Neue):
  // leftovers that only hit those are fine.
  const OK = { per: 1, bebas: 1 };
  const left = async (sel) => (await t.idLeftovers(sel)).filter((s) => /(^|[^A-Za-z])(dtk|mnt)([^A-Za-z]|$)/.test(s) ||
    s.split(/[^A-Za-zÀ-ÿ]+/).some((w) => w && !OK[w.toLowerCase()] && ID.has(w.toLowerCase())));
  const openAll = (sel) => ev(`(document.querySelectorAll(${JSON.stringify(sel + ' details')}).forEach(function(d){ d.open = true; }), true)`);
  await ev(`AC.i18n.set('en')`);
  await t.waitFor(`AC.i18n.lang() === 'en' && AC.cap.S.view === 'editor' && document.querySelector('[data-p="teks"] .cap-wchip') && document.querySelector('[data-p="gaya"] .cap-grp')`, 30000, 'editor rendered again in English');
  await tab('teks');
  await openAll('[data-p="teks"]');
  await ev('(AC.cap.text.find(), true)');
  await t.wait(250);
  const teksLeft = await left('[data-p="teks"]');
  t.check('en: Teks tab has no Indonesian leftovers', !teksLeft.length, JSON.stringify(teksLeft));
  const teksTxt = await t.text('[data-p="teks"]');
  t.check('en: Teks labels (Clean up with AI, Find and replace, Glossary, Text rules and emoji, Hide uh, um, hmm, Match case)',
    ['Clean up with AI', 'Find and replace', 'Glossary', 'Text rules and emoji', 'Hide uh, um, hmm', 'Match case', 'Replace all'].every((x) => teksTxt.indexOf(x) >= 0), teksTxt.slice(0, 300));
  if (full) { await shot('en_teks'); }
  // word popover
  await ev(`document.querySelector('[data-p="teks"] .cap-wchip:not(.is-hidden)').click()`);
  await t.waitFor(`document.querySelector('.cap-pop')`, 3000, 'popover');
  const popLeft = await left('.cap-pop');
  const popTxt = await t.text('.cap-pop');
  t.check('en: word popover in English (Time, Color, Highlight, Size, Font, Hide, Censor)', !popLeft.length &&
    ['Time', 'Color', 'Highlight', 'Size', 'Font', 'Hide', 'Censor', 'Bold', 'Italic'].every((x) => popTxt.indexOf(x) >= 0), JSON.stringify(popLeft) + ' ' + popTxt.slice(0, 200));
  await t.key('Escape');
  await t.waitFor(`!document.querySelector('.cap-pop')`, 2000, 'popover closed');
  // Gaya tab: every group open + the font picker
  await tab('gaya');
  // the engine options (group / field labels) come again in the job language: wait for the rebuilt groups
  await t.waitFor(`(function(){ var s = document.querySelector('[data-p="gaya"] .cap-grp[data-g="huruf"] summary'); return !!s && !/Huruf/.test(s.textContent); })()`, 30000, 'Gaya groups in English');
  await openAll('[data-p="gaya"]');
  await t.click('.cap-font-btn');
  await t.waitFor(`document.querySelectorAll('.cap-font-row').length >= 30`, 15000, 'font list (en)');
  await t.wait(200);
  const gayaLeft = await left('[data-p="gaya"]');
  t.check('en: Gaya tab has no Indonesian leftovers', !gayaLeft.length, JSON.stringify(gayaLeft));
  const gayaTxt = await t.text('[data-p="gaya"]');
  t.check('en: Gaya labels (Quick effects, Font, Back to template, Search fonts, Top left, Built-in badge)',
    ['Quick effects', 'Font', 'Back to template'].every((x) => gayaTxt.indexOf(x) >= 0) &&
    (await ev(`document.querySelector('.cap-font-panel input[type=search]').placeholder`)) === 'Search fonts' &&
    (await ev(`document.querySelector('.cap-posgrid button').getAttribute('aria-label')`)) === 'Top left' &&
    (await ev(`document.querySelector('.cap-font-row .cap-src-bundled').textContent`)) === 'Built-in', gayaTxt.slice(0, 300));
  if (full) { await scrollTo('[data-p="gaya"] .cap-fontpick'); await t.wait(200); await t.shot('v3_library_en_gaya', false); }
  await t.key('Escape');
  // Template tab: filter chips + library section
  await tab('template');
  await t.wait(200);
  const tplLeft = await left('[data-p="template"]');
  t.check('en: Template tab has no Indonesian leftovers', !tplLeft.length, JSON.stringify(tplLeft));
  t.check('en: filter chips "All" + "Fits", My Styles, Import style', /^All\b/.test(await ev(`document.querySelector('.cap-tpl-cats .chip[data-g="all"]').textContent`)) &&
    /^Fits\b/.test(await ev(`(document.querySelector('.cap-tpl-cats .chip[data-g="fit"]') || {textContent: 'Fits'}).textContent`)) &&
    /My Styles/.test(await t.text('[data-p="template"]')) && /Import style/.test(await t.text('[data-p="template"]')));
  // save sheet (built in Indonesian earlier: rebuilt in English)
  await t.click('#cap-save-style');
  await t.waitFor(`!document.querySelector('.cap-save').hidden`, 3000, 'save sheet (en)');
  const saveLeft = await left('.cap-save');
  t.check('en: save sheet in English (Save this style, My <template>)', !saveLeft.length && /Save this style/.test(await t.text('.cap-save')) &&
    /^My /.test(await ev(`document.querySelector('.cap-save input').value`)), JSON.stringify(saveLeft));
  await t.key('Escape');
  await t.waitFor(`document.querySelector('.cap-save').hidden`, 2000, 'save sheet closed');
  // back to Indonesian (the rest of the test reads Indonesian labels)
  await ev(`AC.i18n.set('id')`);
  await t.waitFor(`AC.i18n.lang() === 'id' && AC.cap.S.view === 'editor' && document.querySelector('[data-p="teks"] .cap-tx-bar')`, 30000, 'editor rendered again in Indonesian');
  t.check('id again: Teks toolbar "Rapikan dengan AI"', /Rapikan dengan AI/.test(await t.text('[data-p="teks"] .cap-tx-bar')));

  /* ------------------------------------------------------------ 9:16 sequence: separate class, aspect default, then remembered */
  await ev(`(function(){ var s = JSON.parse(JSON.stringify(__acStub.fixtures.raw49())); s.id = 'seq-v916'; s.name = 'Tes Vertikal 9x16'; s.width = 1080; s.height = 1920; __acStub.setSeq(s, true); return true; })()`);
  await t.waitFor(`AC.cap.S.seq && AC.cap.S.seq.id === 'seq-v916' && AC.cap.S.view === 'setup' && AC.cap.S.tpl`, 15000, 'vertical setup');
  await t.wait(600);
  t.check('9:16: no vertical look yet -> template select with the portrait default', !(await t.count('.cap-last-card')) && (await ev(`document.querySelector('.cap-setup select').value`)) === 'hormozi_kuning');
  await t.click('#primary');
  await t.waitFor(`AC.cap.S.view === 'editor' && AC.cap.S.doc && AC.cap.S.doc.seq.w === 1080`, 120000, 'vertical editor');
  t.check('9:16: doc built with Hormozi Kuning', (await doc('d.template')) === 'hormozi_kuning');
  await ev(`AC.cap.setStyle('style.fill', '#22E58B')`);
  await idle();
  await t.waitFor(`(function(){ var f = AC.sys.readJSON(AC.sys.join(AC.sys.paths.appData, 'caption_last.json'), null); return f && f.vertical && f.vertical.style.style && f.vertical.style.style.fill === '#22E58B'; })()`, 8000, 'vertical remembered');
  const lf2 = await lastFile();
  t.check('9:16: remembered as "vertical", horizontal untouched', lf2.vertical.template === 'hormozi_kuning' && lf2.horizontal.style.font.family === 'Anton');
  await ev(`(function(){ var s = JSON.parse(JSON.stringify(__acStub.fixtures.raw49())); s.id = 'seq-v916b'; s.name = 'Tes Vertikal Kedua'; s.width = 1080; s.height = 1920; __acStub.setSeq(s, true); return true; })()`);
  await t.waitFor(`AC.cap.S.seq && AC.cap.S.seq.id === 'seq-v916b' && document.querySelector('.cap-last-card')`, 15000, 'second vertical setup');
  t.check('9:16: next vertical sequence starts from the vertical look', /Hormozi Kuning/.test(await t.text('.cap-last-card')));
  await shot('setup_vertical');
  t.check('no page errors', !t.errors.length, t.errors.join(' | '));
}
