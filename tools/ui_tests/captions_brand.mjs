// captions_brand.mjs: "Tiru gaya dari gambar" (Template tab) + Brand Kit (Gaya tab, Settings) in headless Chrome
// with the fake CEP + the REAL engine (style_from_image / brandkit / brand_emphasis run in python).
//   node tools/ui_test.mjs tools/ui_tests/captions_brand.mjs --engine live              380 px (+ screenshots v3_brand_*)
//   node tools/ui_test.mjs tools/ui_tests/captions_brand.mjs --engine live --width 340  narrow: sheet at the bottom
// AI is switched off in the page settings (the engine reads the sandboxed settings.json), so no AI quota is used:
// the stylist runs its local measurement and "Sorot kata penting" its rule fallback.
import { execFileSync } from 'child_process';
import fs from 'fs';
import path from 'path';

export default async function (t) {
  const ev = (s) => t.ev(s);
  if (t.engine !== 'live') { t.check('captions_brand: needs --engine live', true); return; }
  const full = t.width === 380, sfx = full ? '' : '_' + t.width;
  const shot = async (n) => { await t.wait(150); await t.shot('v3_brand_' + n + sfx); };
  const ub = (process.env.APPDATA || '') + String.fromCharCode(92) + 'Python';
  const doc = (expr) => ev(`(function(){ var d = AC.cap.S.doc; return ${expr}; })()`);
  const idle = () => ev('AC.cap.idle().then(function(){ return true; })');
  const noOverflow = async (where) => t.check(`layout: no horizontal overflow (${where})`,
    await ev('document.documentElement.scrollWidth <= window.innerWidth + 1 && document.getElementById("view").scrollWidth <= document.getElementById("view").clientWidth + 1'));
  const realWait = t.waitFor;
  t.waitFor = async (expr, ms, label) => {
    try { return await realWait(expr, ms, label); } catch (e) {
      console.log('  log', String(await ev('AC.log.text()').catch(() => '')).slice(-1500));
      console.log('  state', JSON.stringify(await ev('AC.brandKit.state()').catch(() => null)));
      throw e;
    }
  };

  // test images: two of OUR templates rendered by the engine (portrait boxed, landscape karaoke)
  const imgDir = path.join(t.sandbox, 'imgs');
  fs.mkdirSync(imgDir, { recursive: true });
  const py = `import sys; sys.path.insert(0, r"${path.join(t.root, 'engine')}")
from ac.captions import stylist as S, templates as TP
S.render_style(TP.load("boxed", 1080, 1920), 1080, 1920, r"${path.join(imgDir, 'boxed.png')}", text="Rahasia cuan jualan online", out_w=540)
S.render_style(TP.load("karaoke", 1920, 1080), 1920, 1080, r"${path.join(imgDir, 'karaoke.png')}", text="Rahasia cuan jualan online", out_w=960)`;
  execFileSync('python', ['-X', 'utf8', '-c', py], { env: { ...process.env, PYTHONUSERBASE: ub }, stdio: 'pipe' });
  const b64 = (n) => fs.readFileSync(path.join(imgDir, n)).toString('base64');

  // host stubs the editor needs (same as captions.mjs, minimal)
  await ev(`(window.process.env.PYTHONUSERBASE = ${JSON.stringify(ub)}, AC.engine.stopWorker(), true)`);
  await t.host('bac_captions_player', 'function (id) { return JSON.stringify({id: __acStub.seq.id, t: __acStub.seq.player || 0}); }');
  await t.host('bac_captions_status', 'function (id) { return JSON.stringify({hasTrack: false, track: -1, clips: 0, path: null, ours: false, locked: false, linear: true, editable: {hasTrack: false, track: -1, clips: 0}}); }');
  await ev(`(AC.settings.set('ai', false), true)`);

  /* ------------------------------------------------------------ build a caption document */
  await t.go('tool/captions');
  await t.waitFor(`AC.cap.S.view === 'setup' && AC.cap.S.tpl`, 30000, 'setup');
  await t.click('#primary');
  await t.waitFor(`AC.cap.S.view === 'editor' && AC.cap.S.doc`, 120000, 'editor');
  await t.waitFor(`document.querySelector('#cap-panel-template .cap-sty')`, 5000, 'stylist card');
  t.check('template tab: "Tiru gaya dari gambar" card is the first thing', await ev(`document.querySelector('#cap-panel-template').firstElementChild.classList.contains('cap-sty')`));
  // collapsed to one row by default: the template gallery stays above the fold
  await ev(`(document.getElementById('view').scrollTop = 0, true)`);
  await t.waitFor(`document.querySelector('#cap-panel-template .cap-tpl-grid:not(.cap-mine):not(.cap-lastgrid) .cap-tpl')`, 15000, 'template cards');
  const fold = await ev(`(function(){ var c = document.querySelector('.cap-sty'), s = document.querySelector('.cap-tpl-search'), k = document.querySelector('#cap-panel-template .cap-tpl[data-id]');
    return { collapsed: c.classList.contains('is-collapsed') && document.querySelector('.cap-sty-body').hidden, h: Math.round(c.getBoundingClientRect().height),
      search: Math.round(s.getBoundingClientRect().top), card: Math.round(k.getBoundingClientRect().top), vh: window.innerHeight }; })()`);
  t.check(`template tab: Tiru gaya collapsed to one row (${fold.h} px), search + first template card above the fold (${fold.card} < ${fold.vh})`,
    fold.collapsed && fold.h <= 60 && fold.card < fold.vh - 60 && /Tempel/.test(await t.text('.cap-sty-h')), JSON.stringify(fold));
  await shot('template');
  await t.click('.cap-sty-tgl');
  t.check('template tab: toggle opens the card (aria-expanded), state remembered', await ev(`!document.querySelector('.cap-sty-body').hidden && document.querySelector('.cap-sty-tgl').getAttribute('aria-expanded') === 'true' && localStorage.getItem('ac.cap.sty.open') === 'true'`));
  t.check('template tab: drop zone + Pilih gambar / Tempel / Pakai frame pratinjau + AI switch',
    (await t.count('.cap-sty-drop')) === 1 && /Pilih gambar/.test(await t.text('.cap-sty-src')) && /Tempel/.test(await t.text('.cap-sty-src')) &&
    /frame pratinjau/.test(await t.text('.cap-sty-src')) && (await t.count('.cap-sty .sw')) === 1);
  t.check('AI switch copy: "Pakai AI pembaca gambar" (no "AI vision")', /AI pembaca gambar/.test(await t.text('.cap-sty .sw')) && !/vision/i.test(await t.text('.cap-sty')));
  await ev(`(document.getElementById('view').scrollTop = 0, true)`);
  await shot('template_open');
  await noOverflow('template + stylist card');

  /* ------------------------------------------------------------ paste a screenshot (Ctrl+V), AI switch off */
  await ev(`(document.querySelector('.cap-sty .sw input').click(), true)`);
  t.check('AI switch off', !(await ev(`document.querySelector('.cap-sty .sw input').checked`)));
  await t.click('.cap-sty-tgl');            // collapse again: a paste must still work and open the card
  t.check('collapsed again', await ev(`document.querySelector('.cap-sty-body').hidden`));
  const tPaste = Date.now();
  await ev(`(function(){
    var bin = atob(${JSON.stringify(b64('boxed.png'))}), u = new Uint8Array(bin.length);
    for (var i = 0; i < bin.length; i++) u[i] = bin.charCodeAt(i);
    var dt = new DataTransfer(); dt.items.add(new File([u], 'boxed.png', {type: 'image/png'}));
    document.dispatchEvent(new ClipboardEvent('paste', {clipboardData: dt, bubbles: true, cancelable: true}));
    return true; })()`);
  await t.waitFor(`AC.brandKit.state().styBusy || AC.brandKit.state().sty`, 5000, 'stylist started');
  t.check('paste while collapsed: card opens for the progress / result', await ev(`!document.querySelector('.cap-sty-body').hidden`));
  await t.waitFor(`AC.brandKit.state().sty && !AC.brandKit.state().styBusy`, 90000, 'stylist result');
  const st1 = await ev('AC.brandKit.state().sty');
  t.check(`paste: style read from the image in ${((Date.now() - tPaste) / 1000).toFixed(1)} dtk (local, AI off)`, st1.source === 'local' && st1.chips >= 8, JSON.stringify(st1));
  await t.waitFor(`Array.prototype.every.call(document.querySelectorAll('.cap-sty-cmp img'), function(i){ return i.complete && i.naturalWidth > 0; }) && document.querySelectorAll('.cap-sty-cmp img').length === 2`, 10000, 'compare images');
  t.check('result: side by side (Gambar kamu | Hasil Klipora), zoomed on the caption', /Gambar kamu/.test(await t.text('.cap-sty-cmp')) && /Hasil Klipora/.test(await t.text('.cap-sty-cmp')) &&
    await ev(`document.querySelector('.cap-sty-cmp').classList.contains('is-zoom') && /zoom_/.test(document.querySelector('.cap-sty-cmp img').src)`));
  await ev(`(document.querySelector('.cap-sty-cmp').click(), true)`);
  t.check('result: click shows the whole frames', await ev(`!document.querySelector('.cap-sty-cmp').classList.contains('is-zoom') && /src_/.test(document.querySelector('.cap-sty-cmp img').src)`));
  await ev(`(document.querySelector('.cap-sty-cmp').click(), true)`);
  t.check('result: confidence chips with % and colour swatches', (await t.count('.cap-sty-chip')) === st1.chips && (await t.count('.cap-sty-chip .cap-sty-sw')) >= 2 &&
    /%/.test(await t.text('.cap-sty-chip .cap-sty-c')));
  t.check('result: flagged "Tanpa AI, akurasi lebih rendah"', /Tanpa AI/.test(await t.text('.cap-sty-meta')));
  t.check('result: measured text colour #111111 + box per line (boxed template)',
    /#111111/.test(await t.text('.cap-sty-chip[data-id="fill"]')) && /Per baris/.test(await t.text('.cap-sty-chip[data-id="box"]')));
  await ev(`(function(){ var c = document.querySelector('.cap-sty'); document.getElementById('view').scrollTop = c.offsetTop - 8; return true; })()`);
  await shot('stylist_result');

  /* ------------------------------------------------------------ Pakai gaya ini (one undo step) */
  const undoN = await ev('AC.cap.S.undo.length');
  await ev(`(function(){ var b = Array.prototype.filter.call(document.querySelectorAll('.cap-sty-res .btn'), function(x){ return /Pakai gaya ini/.test(x.textContent); })[0]; b.click(); return true; })()`);
  await t.waitFor(`AC.cap.S.doc && AC.cap.S.doc.style && AC.cap.S.doc.style.box && AC.cap.S.doc.style.box.enabled === true`, 15000, 'style applied');
  await idle();
  t.check('Pakai gaya ini: template + doc style applied (box, dark text) as one undo step',
    (await doc('d.style.style.fill')) === '#111111' && (await ev('AC.cap.S.undo.length')) === undoN + 1 && (await doc('d.template')) === (await ev('AC.brandKit.state().sty.base')));
  await ev('AC.cap.undo()');
  await t.waitFor(`AC.cap.S.undo.length === ${undoN}`, 10000, 'undo');
  t.check('Pakai gaya ini: Ctrl Z brings the old look back', (await doc('JSON.stringify(d.style)')) === '{}');

  /* ------------------------------------------------------------ Simpan ke Gaya Saya */
  await ev(`(function(){ var i = document.querySelector('.cap-sty-res input.input'); i.value = 'Gaya Contoh Boxed'; i.dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter', bubbles: true})); return true; })()`);
  await t.waitFor(`AC.cap.S.tpl.templates.some(function(r){ return r.name === 'Gaya Contoh Boxed' && r.source === 'user'; })`, 20000, 'user template saved');
  await t.waitFor(`/Gaya Contoh Boxed/.test((document.querySelector('.cap-mine') || {}).textContent || '')`, 20000, 'Gaya Saya card');
  t.check('Simpan ke Gaya Saya: library save (card in Gaya Saya, toast "Gaya Saya" with Lihat, no old "Template milik saya")',
    /Gaya Saya/.test(await ev(`Array.prototype.map.call(document.querySelectorAll('.toast'), function(x){ return x.textContent; }).join(' | ')`)) &&
    !/Template milik saya/.test(await ev(`document.body.textContent`)) && /Lihat/.test(await ev(`Array.prototype.map.call(document.querySelectorAll('.toast .linkbtn'), function(x){ return x.textContent; }).join()`)));

  /* ------------------------------------------------------------ drop a second image (landscape) */
  await ev(`(function(){
    var bin = atob(${JSON.stringify(b64('karaoke.png'))}), u = new Uint8Array(bin.length);
    for (var i = 0; i < bin.length; i++) u[i] = bin.charCodeAt(i);
    var dt = new DataTransfer(); dt.items.add(new File([u], 'karaoke.png', {type: 'image/png'}));
    document.querySelector('.cap-sty-drop').dispatchEvent(new DragEvent('drop', {dataTransfer: dt, bubbles: true, cancelable: true}));
    return true; })()`);
  await t.waitFor(`AC.brandKit.state().styBusy`, 5000, 'drop started');
  await t.waitFor(`!AC.brandKit.state().styBusy`, 90000, 'drop result');
  t.check('drop: a dropped file is analysed too (karaoke: white text, yellow active word)',
    /#FFFFFF/.test(await t.text('.cap-sty-chip[data-id="fill"]')) && /#FFD/.test(await t.text('.cap-sty-chip[data-id="active"]')));

  /* ------------------------------------------------------------ Gaya tab: Brand Kit bar -> editor sheet */
  await t.click('#cap-tab-gaya');
  await t.waitFor(`document.querySelector('#cap-panel-gaya .cap-bk-bar') && AC.brandKit.state().kits`, 15000, 'brand bar');
  t.check('gaya tab: Brand Kit bar on top, no kit yet', await ev(`document.querySelector('#cap-panel-gaya').firstElementChild.classList.contains('cap-bk-bar')`) &&
    /Belum ada/.test(await t.text('.cap-bk-bar .cap-bk-sub')));
  await ev(`(function(){ var b = Array.prototype.filter.call(document.querySelectorAll('.cap-bk-bar .btn'), function(x){ return /Atur/.test(x.textContent); })[0]; b.click(); return true; })()`);
  await t.waitFor(`document.querySelector('.cap-bk-sheet .cap-bk-crow') && document.querySelectorAll('.cap-bk-sheet select option').length > 20`, 20000, 'editor sheet');
  t.check('editor: 4 colour rows with swatches + hex, fonts from the catalog (Windows fonts too)',
    (await t.count('.cap-bk-crow')) === 4 && (await t.count('.cap-bk-crow .cap-swatch')) >= 40 &&
    (await ev(`Array.prototype.some.call(document.querySelectorAll('.cap-bk-sheet select option'), function(o){ return /\\(Windows\\)/.test(o.textContent); })`)));
  // fill the form: name, colours (hex and swatch), heading font, brand words, emoji, tone
  await ev(`(function(){ var s = document.querySelector('.cap-bk-sheet');
    var name = s.querySelector('input[aria-label="Nama Brand Kit"]'); name.value = 'TokoKita'; name.dispatchEvent(new Event('input', {bubbles: true}));
    var hx = s.querySelector('.cap-bk-crow[data-key="primary"] .cap-bk-hex'); hx.value = '00a86b'; hx.dispatchEvent(new Event('input', {bubbles: true}));
    var bad = s.querySelector('.cap-bk-crow[data-key="background"] .cap-bk-hex'); bad.value = '#12'; bad.dispatchEvent(new Event('input', {bubbles: true}));
    s.querySelector('.cap-bk-crow[data-key="accent"] .cap-swatch[title="#FFD400"]').click();
    var sel = s.querySelector('select[aria-label="Font judul"]'); sel.value = 'Rubik Black'; sel.dispatchEvent(new Event('change', {bubbles: true}));
    var w = s.querySelector('input[aria-label="Tambah kata brand"]'); w.value = 'TokoKita, reseller'; w.dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter', bubbles: true}));
    Array.prototype.filter.call(s.querySelectorAll('.seg button, [role="radio"]'), function(b){ return b.textContent === 'Tanpa' && b.closest('.field'); })[0].click();
    return true; })()`);
  t.check('editor: bad hex marked, good hex accepted, words as chips, live sample uses the colours',
    await ev(`document.querySelector('.cap-bk-crow[data-key="background"] .cap-bk-hex').classList.contains('is-bad')`) &&
    (await t.count('.cap-bk-words .chip')) === 2 && /TokoKita/.test(await t.text('.cap-bk-sample')) &&
    /0, 168, 107/.test(await ev(`document.querySelector('.cap-bk-sample span:nth-child(2)').style.color`)));
  t.check('editor: the sheet never shows a key or secret field', !(await ev(`!!document.querySelector('.cap-bk-sheet input[type="password"]')`)));
  // aria-modal: Tab from the last control wraps to the first, Shift+Tab from the first to the last
  const trap = await ev(`(function(){ var s = document.querySelector('.cap-bk-sheet');
    var f = Array.prototype.filter.call(s.querySelectorAll('button, [href], input, select, textarea, [tabindex]'), function(x){ return !x.disabled && x.tabIndex >= 0 && x.offsetParent !== null; });
    f[f.length - 1].focus(); f[f.length - 1].dispatchEvent(new KeyboardEvent('keydown', {key: 'Tab', bubbles: true, cancelable: true}));
    var a = document.activeElement === f[0];
    f[0].dispatchEvent(new KeyboardEvent('keydown', {key: 'Tab', shiftKey: true, bubbles: true, cancelable: true}));
    return a && document.activeElement === f[f.length - 1]; })()`);
  t.check('editor: Tab / Shift+Tab stay inside the sheet', trap);
  t.check('editor: close button sits at the right edge of the header', await ev(`(function(){ var h = document.querySelector('.cap-bk-sh').getBoundingClientRect(), b = document.querySelector('.cap-bk-sh .ibtn').getBoundingClientRect(); return h.right - b.right < 4; })()`));
  await ev(`(document.querySelector('.cap-bk-sheet').scrollTop = 0, true)`);
  await shot('kit_editor');
  await noOverflow('brand kit sheet');
  // Simpan dan terapkan
  await ev(`(function(){ var b = Array.prototype.filter.call(document.querySelectorAll('.cap-bk-sheet .btn'), function(x){ return /Simpan dan terapkan/.test(x.textContent); })[0]; b.click(); return true; })()`);
  await t.waitFor(`!AC.brandKit.state().open && AC.cap.S.doc.style.emphasis && (AC.cap.S.doc.style.emphasis.keywords || []).indexOf('tokokita') >= 0`, 20000, 'kit applied');
  await t.waitFor(`(AC.cap.S.doc.params.glossary || []).indexOf('TokoKita') >= 0`, 20000, 'glossary');
  await idle();
  const kit = await ev(`AC.brandKit.state().kits[0]`);
  t.check('kit saved in %APPDATA%\\Klipora\\brandkit.json (active, colours, words, emoji none)',
    t.existsSandbox('home/AppData/Roaming/Klipora/brandkit.json') && kit.colors.primary === '#00A86B' && kit.colors.accent === '#FFD400' &&
    kit.fonts.heading === 'Rubik Black' && kit.words.join() === 'TokoKita,reseller' && kit.emoji === 'none' && (await ev('AC.brandKit.state().active')) === kit.id);
  t.check('Terapkan: doc restyled (font, active colour = accent, brand words highlighted in primary) + glossary',
    (await doc('d.style.font.family')) === 'Rubik Black' && (await doc('d.style.highlight.color')) === '#FFD400' && (await doc('d.style.emphasis.color')) === '#00A86B' &&
    (await doc('d.params.glossary.join()')) === 'TokoKita,reseller');
  t.check('gaya tab: bar shows the active kit + colour dots', /TokoKita/.test(await t.text('.cap-bk-bar b')) && (await t.count('.cap-bk-bar .cap-bk-dots i')) === 4);

  /* ------------------------------------------------------------ Sorot kata penting (rules, AI off) */
  await ev(`(function(){ var b = Array.prototype.filter.call(document.querySelectorAll('.cap-bk-bar .btn'), function(x){ return /Sorot kata penting/.test(x.textContent); })[0]; b.click(); return true; })()`);
  await t.waitFor(`AC.brandKit.state().emph !== null && !AC.brandKit.state().emphBusy && document.querySelector('.cap-bk-it')`, 60000, 'emphasis list');
  const nE = await t.count('.cap-bk-it');
  t.check(`Sorot: ${nE} suggestions to review (brand words + key words), rule fallback labelled`, nE >= 5 && /Aturan, tanpa AI/.test(await t.text('.cap-bk-rh')) &&
    /Kata brand/.test(await t.text('.cap-bk-list')));
  await ev(`(document.getElementById('view').scrollTop = 0, true)`);
  await shot('emphasis');
  await ev(`(function(){ var c = document.querySelector('.cap-bk-it input'); c.click(); return true; })()`);
  t.check('Sorot: unchecking one changes the button to "Terima n kata"', new RegExp('Terima ' + (nE - 1) + ' kata').test(await t.text('.cap-bk-review .btn-primary, .cap-bk-review .btn.primary, .cap-bk-review .btn')));
  const colored0 = await doc(`d.words.filter(function(w){ return (w.style || {}).color; }).length`);
  const undo1 = await ev('AC.cap.S.undo.length');
  await ev(`(function(){ var b = Array.prototype.filter.call(document.querySelectorAll('.cap-bk-review .btn'), function(x){ return /Terima/.test(x.textContent); })[0]; b.click(); return true; })()`);
  await t.waitFor(`AC.cap.S.undo.length === ${undo1 + 1}`, 15000, 'emphasis applied');
  await idle();
  const colored1 = await doc(`d.words.filter(function(w){ return (w.style || {}).color; }).length`);
  t.check(`Sorot: ${nE - 1} words coloured as one undo step`, colored1 - colored0 === nE - 1, `${colored0} -> ${colored1}`);
  await ev('AC.cap.undo()');
  await t.waitFor(`AC.cap.S.undo.length === ${undo1}`, 10000, 'undo emphasis');
  t.check('Sorot: undo removes them again', (await doc(`d.words.filter(function(w){ return (w.style || {}).color; }).length`)) === colored0);

  /* ------------------------------------------------------------ English UI (live switch): brand bar, kit sheet, stylist card */
  await idle();
  await ev(`(AC.i18n.set('en'), true)`);
  await t.waitFor(`AC.cap.S.view === 'editor' && document.querySelector('#cap-tab-gaya')`, 30000, 'editor after switch');
  await ev(`(document.querySelector('#cap-tab-gaya').click(), true)`);
  await t.waitFor(`document.querySelector('#cap-panel-gaya .cap-bk-bar') && /Highlight key words/.test(document.querySelector('#cap-panel-gaya .cap-bk-bar').textContent)`, 20000, 'english brand bar');
  t.check('en: brand bar labels (Brand Kit: TokoKita, Manage, Apply, Highlight key words (AI))',
    /Brand Kit: TokoKita/.test(await t.text('.cap-bk-bar b')) && /Manage/.test(await t.text('.cap-bk-bar')) && /Apply/.test(await t.text('.cap-bk-bar')) &&
    /Highlight key words \(AI\)/.test(await t.text('.cap-bk-bar')));
  let leftB = await t.idLeftovers('#cap-panel-gaya .cap-bk-bar');
  t.check('en: no Indonesian left in the Brand Kit bar', leftB.length === 0, leftB.join(' | '));
  await ev(`(function(){ var b = Array.prototype.filter.call(document.querySelectorAll('.cap-bk-bar .btn'), function(x){ return /Manage/.test(x.textContent); })[0]; b.click(); return true; })()`);
  await t.waitFor(`document.querySelector('.cap-bk-sheet .cap-bk-crow') && document.querySelectorAll('.cap-bk-sheet select option').length > 20`, 20000, 'english sheet');
  const sheetTxt = await t.text('.cap-bk-sheet');
  t.check('en: kit sheet in English (Colors, Primary, Fonts, Brand words, Tone, Save and apply)', /Colors/.test(sheetTxt) && /Primary/.test(sheetTxt) &&
    /Fonts/.test(sheetTxt) && /Brand words/.test(sheetTxt) && /Tone/.test(sheetTxt) && /Save and apply/.test(sheetTxt) && /Shop at/.test(sheetTxt));
  t.check('en: tone + emoji options from the engine in English (Casual, A few)', await ev(`(function(){ var s = document.querySelector('.cap-bk-sheet select[aria-label="Tone"]');
    return !!s && Array.prototype.some.call(s.options, function(o){ return o.textContent === 'Casual'; }) && /A few/.test(document.querySelector('.cap-bk-sheet').textContent); })()`));
  leftB = await t.idLeftovers('.cap-bk-sheet');
  t.check('en: no Indonesian left in the Brand Kit sheet', leftB.length === 0, leftB.join(' | '));
  await shot('en_kit_editor');
  await t.key('Escape');
  await t.waitFor(`!AC.brandKit.state().open`, 3000, 'Esc closes (en)');
  await ev(`(document.querySelector('#cap-tab-template').click(), true)`);
  await t.waitFor(`document.querySelector('#cap-panel-template .cap-sty') && /Copy a style from an image/.test(document.querySelector('#cap-panel-template .cap-sty').textContent)`, 15000, 'english stylist card');
  await ev(`(function(){ var r = document.querySelector('.cap-sty-res'); if (r) r.setAttribute('data-i18n-skip', ''); return true; })()`);   // chips/notes: stylist.py texts
  if (await ev(`document.querySelector('.cap-sty-body').hidden`)) await t.click('.cap-sty-tgl');
  t.check('en: stylist card (Paste, Choose image, Use preview frame, Use AI image reader)', /Paste/.test(await t.text('.cap-sty')) && /Choose image/.test(await t.text('.cap-sty')) &&
    /Use preview frame/.test(await t.text('.cap-sty')) && /Use AI image reader/.test(await t.text('.cap-sty')));
  leftB = await t.idLeftovers('#cap-panel-template .cap-sty');
  t.check('en: no Indonesian left in the stylist card', leftB.length === 0, leftB.join(' | '));
  const tplEn = await ev(`AC.cap.worker('templates', {options: false}).promise.then(function(r){ return r.templates.filter(function(t){ return t.id === 'hijau_viral'; })[0].name; })`);
  t.check(`en: template names from the engine in English (hijau_viral = ${tplEn})`, tplEn === 'Viral Green');
  await ev(`(AC.i18n.set('id'), true)`);
  await t.waitFor(`AC.cap.S.view === 'editor' && document.querySelector('#cap-tab-gaya')`, 30000, 'editor back in id');
  await ev(`(document.querySelector('#cap-tab-gaya').click(), true)`);
  await t.waitFor(`document.querySelector('#cap-panel-gaya .cap-bk-bar') && /Sorot kata penting/.test(document.querySelector('#cap-panel-gaya .cap-bk-bar').textContent)`, 20000, 'back to Indonesian');
  t.check('id: switched back to Indonesian', true);

  /* ------------------------------------------------------------ Settings > Caption > Brand Kit */
  await t.go('settings');
  await t.waitFor(`document.querySelector('.cap-bk-set') && /TokoKita/.test(document.querySelector('.cap-bk-set').textContent)`, 15000, 'settings row');
  t.check('settings: Brand Kit row shows the active kit', true);
  await ev(`(function(){ var b = document.querySelector('.cap-bk-set .btn'); b.focus(); b.click(); return true; })()`);   // a real click focuses it
  await t.waitFor(`AC.brandKit.state().open && document.querySelector('.cap-bk-sheet .cap-bk-kits .chip[aria-checked="true"]')`, 15000, 'sheet from settings');
  t.check('settings: Atur Brand Kit opens the same editor on the active kit (button = Simpan)', /TokoKita/.test(await t.text('.cap-bk-kits')) &&
    !/terapkan/i.test(await t.text('.cap-bk-actions')));
  await shot('settings');
  // new kit from Settings, then delete it with the two-step button
  await ev(`(function(){ document.querySelector('.cap-bk-kits .chip-add').click(); var s = document.querySelector('.cap-bk-sheet');
    var name = s.querySelector('input[aria-label="Nama Brand Kit"]'); name.value = 'Kanal Kedua'; name.dispatchEvent(new Event('input', {bubbles: true}));
    Array.prototype.filter.call(s.querySelectorAll('.btn'), function(x){ return /^Simpan$/.test(x.textContent.trim()); })[0].click(); return true; })()`);
  await t.waitFor(`(AC.brandKit.state().kits || []).length === 2`, 15000, 'second kit');
  t.check('settings: second kit saved, the first stays active', (await ev('AC.brandKit.state().active')) === kit.id);
  await ev(`(function(){ var d = Array.prototype.filter.call(document.querySelectorAll('.cap-bk-sheet .btn'), function(x){ return /Hapus/.test(x.textContent); })[0]; d.click(); d.click(); return true; })()`);
  await t.waitFor(`(AC.brandKit.state().kits || []).length === 1`, 15000, 'kit deleted');
  t.check('settings: Hapus needs two clicks and removes the kit', true);
  await t.key('Escape');
  await t.waitFor(`!AC.brandKit.state().open`, 3000, 'Esc closes');
  t.check('Esc closes the sheet, focus back on the button that opened it', await ev(`document.activeElement === document.querySelector('.cap-bk-set .btn')`));
  t.check('no page errors', !t.errors.length, t.errors.join(' | '));
}
