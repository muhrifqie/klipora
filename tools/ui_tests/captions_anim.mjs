// captions_anim.mjs: Auto Caption "Animasi" tab (animation studio + sound effects per word) in headless Chrome with
// the fake CEP + the REAL engine (anim / anim_sprites / anim_save / sfx_* actions through the worker).
//   node tools/ui_test.mjs tools/ui_tests/captions_anim.mjs --engine live --shots docs/shots            380 px
//   node tools/ui_test.mjs tools/ui_tests/captions_anim.mjs --engine live --shots docs/shots --width 900 split layout
// The sequence is a synthesized 1080x1920 timeline over the 49 s test clip (9:16 shorts look). Host functions
// bac_captions_* / bac_csfx_* are stubbed (the real ones: panel/host/34_captions.jsx, 42_caption_sfx.jsx, live only).
import fs from 'fs';

export default async function (t) {
  const ev = (s) => t.ev(s);
  if (t.engine !== 'live') { t.check('captions_anim: needs --engine live', true); return; }
  const sfx = t.width === 380 ? '' : '_' + t.width;
  const ub = (process.env.APPDATA || '') + String.fromCharCode(92) + 'Python';
  const shot = async (name, sel) => {
    // scroll the side panel so `sel` sits just under the sticky preview (380 px) or at the top (split layout)
    if (sel) await ev(`(function(){ var e = document.querySelector(${JSON.stringify(sel)}), v = document.getElementById('view'), bar = document.querySelector('.cap-bar');
      if (!e) return false; var top = window.innerWidth < 600 && bar ? bar.getBoundingClientRect().bottom + 8 : 8; v.scrollTop += e.getBoundingClientRect().top - top; return true; })()`);
    await t.wait(450);
    await t.shot('v3_anim_' + name + sfx);
  };
  const doc = (expr) => ev(`(function(){ var d = AC.cap.S.doc; return ${expr}; })()`);
  const idle = () => ev('AC.cap.idle().then(function(){ return true; })');
  const fxv = (role) => doc(`JSON.stringify(((d.style || {}).anim || {}).fx ? d.style.anim.fx[${JSON.stringify(role)}] : undefined)`);

  await ev(`(window.process.env.PYTHONUSERBASE = ${JSON.stringify(ub)}, AC.engine.stopWorker(), true)`);
  // 9:16 sequence from the 49 s clip
  await ev(`(function(){ var s = JSON.parse(JSON.stringify(__acStub.fixtures.raw49())); s.id = 'seq-anim916'; s.name = 'AC Anim 916'; s.width = 1080; s.height = 1920; s.player = 6.2;
    __acStub.setSeq(s, true); return true; })()`);
  await ev(`(window.__cap = { status: {hasTrack: false, track: -1, clips: 0, path: null, ours: false, locked: false, linear: true, editable: {hasTrack: false, track: -1, clips: 0}}, sfx: [] }, true)`);
  await t.host('bac_captions_player', 'function (id) { return JSON.stringify({id: __acStub.seq.id, t: __acStub.seq.player || 0}); }');
  await t.host('bac_captions_status', 'function (id) { return JSON.stringify(__cap.status); }');
  await t.host('bac_csfx_apply', `function (p, start, id) {
    if (!__acStub.fs.existsSync(p)) return 'ERR:File efek suara tidak ditemukan: ' + p;
    var removed = __cap.sfx.length; __cap.sfx.push({path: p, start: start, seq: id});
    return JSON.stringify({ok: true, mode: removed ? 'replace' : 'place', track: 2, removed: removed, start: start, end: start + 10, path: p, named: true}); }`);

  /* ------------------------------------------------------------ build captions */
  await t.go('tool/captions');
  await t.waitFor(`AC.cap.S.view === 'setup' && AC.cap.S.tpl`, 30000, 'setup');
  await t.click('#primary');
  await t.waitFor(`AC.cap.S.view === 'editor' && AC.cap.S.doc`, 120000, 'editor');
  t.check('setup: 9:16 doc built', (await doc('d.seq.w + "x" + d.seq.h')) === '1080x1920');

  /* ------------------------------------------------------------ Pustaka */
  await t.click('#cap-tab-animasi');
  await t.waitFor(`document.querySelectorAll('[data-p="animasi"] .cap-afx-grid .cap-afx').length > 30`, 30000, 'preset grid');
  const tStart = Date.now();
  await t.waitFor(`Array.prototype.every.call(document.querySelectorAll('[data-p="animasi"] .cap-afx-grid .cap-afx .cap-afx-spr:not(.is-none)'), function(s){ return /url\\(/.test(s.style.backgroundImage); })`, 60000, 'sprites');
  const counts = await ev(`Array.prototype.map.call(document.querySelectorAll('[data-p="animasi"] .cap-afx-grid'), function(g){ return g.dataset.role + ':' + g.children.length; }).join(',')`);
  t.check(`pustaka: 4 groups (Masuk / Kata aktif / Keluar / Loop) with >= 5 presets + Tanpa each (${counts})`,
    counts.split(',').length === 4 && counts.split(',').every((c) => Number(c.split(':')[1]) >= 6));
  t.check(`pustaka: every preset tile has an animated sprite strip (${Date.now() - tStart} ms)`, await ev(`(function(){ var s = document.querySelector('[data-p="animasi"] .cap-afx-grid .cap-afx-spr:not(.is-none)'); var cs = getComputedStyle(s);
    return cs.animationName === 'cap-afx-play' && /steps/.test(cs.animationTimingFunction) && /%/.test(s.style.backgroundSize); })()`));
  const sprOk = await ev(`(function(){ var s = document.querySelector('[data-p="animasi"] .cap-afx[data-id="pop_kata"] .cap-afx-spr'); return new Promise(function(r){ var i = new Image(); i.onload = function(){ r(i.naturalWidth + 'x' + i.naturalHeight); }; i.onerror = function(){ r('err'); };
    i.src = s.style.backgroundImage.replace(/^url\\("?|"?\\)$/g, ''); }); })()`);
  t.check(`pustaka: sprite PNG loads (16 frames x 176x64 = ${sprOk})`, sprOk === '2816x64');
  t.check('pustaka: classic pickers kept inside "Animasi dasar" (4 grids)', (await t.count('[data-p="animasi"] details .cap-anim-grid')) === 4);

  const tileClick = async (role, id) => ev(`(document.querySelector('[data-p="animasi"] .cap-afx-grid[data-role="${role}"] .cap-afx[data-id="${id}"]').click(), true)`);
  await tileClick('in', 'pop_kata');
  await tileClick('active', 'aktif_goyang');
  await tileClick('loop', 'loop_melayang');
  await idle();
  await t.waitFor(`AC.cap.S.doc.style.anim && AC.cap.S.doc.style.anim.fx && AC.cap.S.doc.style.anim.fx.loop === 'loop_melayang'`, 15000, 'fx stored');
  t.check('pustaka: picks stored as anim.fx ids', (await fxv('in')) === '"pop_kata"' && (await fxv('active')) === '"aktif_goyang"');
  t.check('pustaka: picked tile is aria-checked + roving tabindex', await ev(`(function(){ var b = document.querySelector('.cap-afx-grid[data-role="in"] .cap-afx[aria-checked="true"]'); return b && b.dataset.id === 'pop_kata' && b.tabIndex === 0; })()`));
  // keyboard: arrow keys move focus inside a grid
  await ev(`(document.querySelector('.cap-afx-grid[data-role="in"] .cap-afx[data-id="pop_kata"]').focus(), true)`);
  await t.key('ArrowRight', {}, '.cap-afx-grid[data-role="in"] .cap-afx[data-id="pop_kata"]');
  t.check('pustaka: ArrowRight moves focus to the next tile', await ev(`document.activeElement.dataset.id === 'pantul_masuk'`));
  await idle();
  t.check('pustaka: arrow keys also select (radio pattern)', (await fxv('in')) === '"pantul_masuk"');
  // ArrowDown steps by the real column count (2 / 3 / 4 by width), not a fixed 4
  const cols = await ev(`(function(){ var t = document.querySelectorAll('.cap-afx-grid[data-role="in"] .cap-afx'), n = 0; while (n < t.length && t[n].offsetTop === t[0].offsetTop) n++; return n; })()`);
  await ev(`(document.querySelector('.cap-afx-grid[data-role="in"] .cap-afx[data-id="none"]').focus(), true)`);
  await t.key('ArrowDown', {}, '.cap-afx-grid[data-role="in"] .cap-afx[data-id="none"]');
  const downIdx = await ev(`Array.prototype.indexOf.call(document.querySelectorAll('.cap-afx-grid[data-role="in"] .cap-afx'), document.activeElement)`);
  const downTop = await ev(`document.activeElement.offsetLeft === document.querySelector('.cap-afx-grid[data-role="in"] .cap-afx').offsetLeft`);
  t.check(`pustaka: ArrowDown lands on the same column one row down (${cols} cols, index ${downIdx})`, downIdx === cols && downTop);
  // back to pop_kata for the next checks
  await tileClick('in', 'pop_kata');
  await idle();
  // duration + stagger tweak -> {preset, dur, stagger}
  t.check('pustaka: duration / stagger controls shown for an active role', await ev(`!document.querySelector('.cap-afx-grid[data-role="in"]').parentNode.querySelector('.cap-afx-more').hidden`));
  await ev(`(function(){ var i = document.querySelector('.cap-afx-grid[data-role="in"]').parentNode.querySelectorAll('.cap-afx-more input[type=range]'); i[0].value = 500; i[0].dispatchEvent(new Event('change')); return true; })()`);
  await idle();
  await t.waitFor(`AC.cap.S.doc.style.anim.fx.in && AC.cap.S.doc.style.anim.fx.in.dur === 0.5`, 10000, 'dur stored');
  t.check('pustaka: duration slider stores {preset, dur}', (await fxv('in')) === '{"preset":"pop_kata","dur":0.5}');
  await t.waitFor(`AC.cap.stats.previews.length > 0 && document.querySelector('.cap-frame').naturalWidth > 0`, 30000, 'preview');
  await shot('library', '[data-p="animasi"] .cap-afx-top');
  await shot('library_more', '.cap-afx-grid[data-role="out"]');

  // classic entrance pick switches the studio entrance off in the same edit
  await ev(`(function(){ var g = document.querySelectorAll('[data-p="animasi"] details .cap-anim-grid')[0]; Array.prototype.filter.call(g.querySelectorAll('.cap-anim'), function(b){ return b.title === 'Pudar'; })[0].click(); return true; })()`);
  await idle();
  t.check('classic: Pudar entrance clears anim.fx.in, keeps the others', (await fxv('in')) === 'null' && (await doc('d.style.anim.in')) === 'fade' && (await fxv('active')) === '"aktif_goyang"');

  /* ------------------------------------------------------------ Studio */
  await ev(`(Array.prototype.filter.call(document.querySelectorAll('[data-p="animasi"] .cap-afx-top button'), function(b){ return b.textContent === 'Studio'; })[0].click(), true)`);
  await t.waitFor(`document.querySelectorAll('.cap-afx-trk').length === 8`, 10000, 'studio tracks');
  t.check('studio: 8 tracks (skala, kepekatan, geser X/Y, putar, blur, jarak huruf, warna)', (await t.count('.cap-afx-trk')) === 8);
  await ev(`(function(){ var s = document.querySelector('.cap-afx-view:not([hidden]) select.select'); var o = Array.prototype.filter.call(s.options, function(x){ return x.value === 'zoom_masuk'; })[0]; s.value = o.value; s.dispatchEvent(new Event('change')); return true; })()`);
  await t.waitFor(`AC.cap.animUI.F.d && AC.cap.animUI.F.d.spec.tracks.blur`, 5000, 'loaded zoom_masuk');
  t.check('studio: "Mulai dari" loads a preset (3 tracks with keys)', (await ev(`document.querySelectorAll('.cap-afx-trk:not(.is-off)').length`)) === 3);
  await t.waitFor(`/url\\(/.test((document.querySelector('.cap-afx-prev .cap-afx-spr') || {style: {}}).style.backgroundImage || '')`, 20000, 'draft sprite');
  t.check('studio: live sprite of the draft', true);
  // add the rotation track, retime its last key with the keyboard, add a key by double click, set value + easing
  await ev(`(document.querySelector('.cap-afx-trk[data-track="rot"] .cap-afx-tn').click(), true)`);
  t.check('studio: clicking an empty track adds 2 keys', (await ev(`AC.cap.animUI.F.d.spec.tracks.rot.length`)) === 2);
  await ev(`(document.querySelectorAll('.cap-afx-trk[data-track="rot"] .cap-afx-dot')[1].focus(), true)`);
  await ev(`(function(){ var d = document.querySelectorAll('.cap-afx-trk[data-track="rot"] .cap-afx-dot')[1]; d.dispatchEvent(new KeyboardEvent('keydown', {key: 'ArrowLeft', bubbles: true})); return true; })()`);
  t.check('studio: ArrowLeft retimes the focused key (1 -> 0,95)', (await ev(`AC.cap.animUI.F.d.spec.tracks.rot[1].t`)) === 0.95);
  await ev(`(function(){ var l = document.querySelector('.cap-afx-trk[data-track="rot"] .cap-afx-tl'), r = l.getBoundingClientRect();
    l.dispatchEvent(new MouseEvent('dblclick', {bubbles: true, clientX: r.left + r.width * 0.4, clientY: r.top + 5})); return true; })()`);
  t.check('studio: double click on the timeline adds a key at 40 %', (await ev(`AC.cap.animUI.F.d.spec.tracks.rot.map(function(k){ return k.t; }).join(',')`)) === '0,0.4,0.95');
  // pointer drag of the middle key to ~70 %
  await ev(`(function(){ var d = document.querySelectorAll('.cap-afx-trk[data-track="rot"] .cap-afx-dot')[1], l = d.parentNode.getBoundingClientRect();
    d.dispatchEvent(new PointerEvent('pointerdown', {bubbles: true, button: 0, pointerId: 1, clientX: l.left + l.width * 0.4}));
    d.dispatchEvent(new PointerEvent('pointermove', {bubbles: true, pointerId: 1, clientX: l.left + l.width * 0.7}));
    d.dispatchEvent(new PointerEvent('pointerup', {bubbles: true, pointerId: 1, clientX: l.left + l.width * 0.7})); return true; })()`);
  const dragT = await ev(`AC.cap.animUI.F.d.spec.tracks.rot.map(function(k){ return k.t; }).join(',')`);
  t.check(`studio: dragging a key retimes it (${dragT})`, /^0,0\.(69|7|71),0\.95$/.test(dragT));
  await ev(`(function(){ var i = document.querySelector('.cap-afx-key input[type=number]'); i.value = '-30'; i.dispatchEvent(new Event('change')); return true; })()`);
  await ev(`(function(){ var s = document.querySelector('.cap-afx-key select'); s.value = 'elastic'; s.dispatchEvent(new Event('change')); return true; })()`);
  t.check('studio: value + easing edit the selected key', (await ev(`JSON.stringify(AC.cap.animUI.F.d.spec.tracks.rot[AC.cap.animUI.F.d.key])`)) === '{"t":0.7,"v":-30,"e":"elastic"}' ||
    (await ev(`AC.cap.animUI.F.d.spec.tracks.rot.some(function(k){ return k.v === -30 && k.e === 'elastic'; })`)));
  await ev(`(document.querySelectorAll('.cap-afx-trk[data-track="rot"] .cap-afx-dot')[1].focus(), true)`);
  await ev(`(function(){ var d = document.activeElement; d.dispatchEvent(new KeyboardEvent('keydown', {key: 'Delete', bubbles: true})); return true; })()`);
  t.check('studio: Delete removes the focused key', (await ev(`AC.cap.animUI.F.d.spec.tracks.rot.length`)) === 2);
  await ev(`(function(){ var i = document.querySelector('.cap-afx-view:not([hidden]) input.input:not(.cap-afx-num)'); i.value = 'Zoom putar saya'; return true; })()`);
  await shot('studio', '[data-p="animasi"] .cap-afx-top');
  await shot('studio_tracks', '.cap-afx-tracks');
  // use it + save as a custom animation
  await ev(`(Array.prototype.filter.call(document.querySelectorAll('.cap-afx-view:not([hidden]) .btn'), function(b){ return b.textContent.trim() === 'Pakai'; })[0].click(), true)`);
  await idle();
  t.check('studio: Pakai stores the inline spec as anim.fx.in', await doc(`!!(d.style.anim.fx.in && d.style.anim.fx.in.tracks && d.style.anim.fx.in.tracks.rot && d.style.anim.fx.in.group === 'in')`));
  await ev(`(Array.prototype.filter.call(document.querySelectorAll('.cap-afx-view:not([hidden]) .btn'), function(b){ return b.textContent.trim() === 'Simpan sebagai animasi saya'; })[0].click(), true)`);
  await t.waitFor(`document.querySelector('.cap-afx-grid[data-role="in"] .cap-afx[data-id="u_zoom_putar_saya"] .cap-afx-mine')`, 30000, 'saved custom tile');
  const userFile = await ev(`AC.sys.join(AC.sys.paths.appData, 'caption_anims.json')`);
  t.check('studio: saved to %APPDATA%\\Klipora\\caption_anims.json + shown in Pustaka with "Saya"', fs.existsSync(userFile) && /u_zoom_putar_saya/.test(fs.readFileSync(userFile, 'utf8')));
  // play in the main preview: applies the draft, then the preview's 3 s loop (engine burn -> <video>)
  await ev(`(Array.prototype.filter.call(document.querySelectorAll('.cap-afx-view:not([hidden]) .btn'), function(b){ return /Putar di pratinjau/.test(b.textContent); })[0].click(), true)`);
  await t.waitFor(`document.querySelector('.cap-stage video')`, 60000, 'loop video');
  t.check('studio: "Putar di pratinjau" plays the 3 s loop in the main preview', /loop_.*\.mp4/.test(await ev(`decodeURIComponent(document.querySelector('.cap-stage video').src)`)));
  await ev(`(AC.cap.preview.stopLoop(), true)`);

  /* ------------------------------------------------------------ Efek suara */
  await ev(`(Array.prototype.filter.call(document.querySelectorAll('[data-p="animasi"] .cap-afx-top button'), function(b){ return b.textContent === 'Efek suara'; })[0].click(), true)`);
  await t.waitFor(`document.querySelectorAll('.cap-sfx-rule').length === 4`, 20000, 'sfx rules');
  t.check('sfx: 4 rules, >= 12 sounds per picker', (await ev(`document.querySelector('.cap-sfx-rule select').options.length`)) >= 12);
  t.check('sfx: defaults Angka + Kata penting on, Emoji + Setiap halaman off', (await ev(`Array.prototype.map.call(document.querySelectorAll('.cap-sfx-rule'), function(r){ return r.dataset.rule + (r.classList.contains('is-on') ? '1' : '0'); }).join(',')`)) === 'angka1,penting1,emoji0,halaman0');
  await ev(`(document.querySelector('.cap-sfx-rule[data-rule="halaman"] input[type=checkbox]').click(), true)`);
  await ev(`(function(){ var s = document.querySelector('.cap-sfx-rule[data-rule="halaman"] select'); s.value = 'whoosh'; s.dispatchEvent(new Event('change')); return true; })()`);
  t.check('sfx: rule settings kept in the tool state', await ev(`AC.cap.S.ctx.state.sfx.rules.halaman.on === true && AC.cap.S.ctx.state.sfx.rules.halaman.sound === 'whoosh'`));
  await ev(`(AC.cap.animUI.sfxPlan(), true)`);
  await t.waitFor(`document.querySelectorAll('.cap-sfx-hit').length > 3`, 30000, 'sfx plan list');
  const nh = await t.count('.cap-sfx-hit');
  t.check(`sfx: plan list (${nh} bunyi) with time, word, sound, rule`, /Whoosh · Halaman/.test(await t.text('.cap-sfx-list')) && /bunyi/.test(await t.text('.cap-sfx-sum')));
  await ev(`(document.querySelector('.cap-sfx-hit .cap-sfx-t').click(), true)`);
  await ev(`(AC.cap.animUI.sfxApply(), true)`);
  await t.waitFor(`__cap.sfx.length === 1`, 60000, 'sfx applied');
  const ap = await ev('__cap.sfx[0]');
  t.check('sfx: Terapkan renders a mixed WAV and calls bac_csfx_apply(path, start, seqId)', /_sfx_v1\.wav$/.test(ap.path) && fs.existsSync(ap.path) && ap.seq === 'seq-anim916' && ap.start >= 0, JSON.stringify(ap));
  await t.waitFor(`/Klipora SFX/.test(document.querySelector('.cap-sfx-sum').textContent)`, 5000, 'sfx summary');
  await shot('sfx', '[data-p="animasi"] .cap-afx-top');
  await shot('sfx_plan', '.cap-sfx-sum');
  await ev(`(AC.cap.animUI.sfxApply(), true)`);
  await t.waitFor(`__cap.sfx.length === 2`, 60000, 'sfx re-applied');
  t.check('sfx: re-apply = new version (v2), host replaces', /_sfx_v2\.wav$/.test(await ev('__cap.sfx[1].path')));
  t.check('sfx: summary has no single-Ctrl-Z claim', !/Ctrl Z/.test(await t.text('.cap-sfx-sum')));
  // host could not find the placed clip (ok:false): no success text
  await t.host('bac_csfx_apply', `function (p, start, id) { __cap.sfx.push({path: p, start: start, seq: id});
    return JSON.stringify({ok: false, mode: 'replace', track: 2, removed: 1, start: start, end: null, path: p, named: true}); }`);
  await ev(`(AC.cap.animUI.sfxApply(), true)`);
  await t.waitFor(`__cap.sfx.length === 3`, 60000, 'sfx apply ok:false');
  await t.waitFor(`/belum terpasang/.test(document.querySelector('.cap-sfx-sum').textContent)`, 5000, 'sfx ok:false message');
  t.check('sfx: host ok:false -> "belum terpasang", no "bunyi ada di track"', !/bunyi ada di track/.test(await t.text('.cap-sfx-sum')));
  t.check('layout: no horizontal overflow', await ev('document.documentElement.scrollWidth <= window.innerWidth + 1 && document.getElementById("view").scrollWidth <= document.getElementById("view").clientWidth + 1'));

  /* ------------------------------------------------------------ English UI (live switch, editor stays open) */
  await idle();
  await ev(`(AC.i18n.set('en'), true)`);
  await t.waitFor(`AC.cap.S.view === 'editor' && document.querySelector('#cap-tab-animasi')`, 30000, 'editor after switch');
  await ev(`(document.querySelector('#cap-tab-animasi').click(), true)`);
  const segBtn = (label) => ev(`(Array.prototype.filter.call(document.querySelectorAll('[data-p="animasi"] .cap-afx-top button'), function(b){ return b.textContent === ${JSON.stringify(label)}; })[0].click(), true)`);
  await t.waitFor(`/Library/.test((document.querySelector('[data-p="animasi"] .cap-afx-top') || {}).textContent || '')`, 15000, 'english segmented');
  await segBtn('Library');
  await t.waitFor(`Array.prototype.some.call(document.querySelectorAll('[data-p="animasi"] .cap-afx-l'), function(l){ return l.textContent === 'Soft fade'; })`, 30000, 'english preset labels from the engine');
  const segTxt = await ev(`Array.prototype.map.call(document.querySelectorAll('[data-p="animasi"] .cap-afx-top button'), function(b){ return b.textContent; }).join('|')`);
  t.check(`en: animation views "Library | Studio | Sound effects" (${segTxt})`, segTxt === 'Library|Studio|Sound effects');
  const secT = await ev(`Array.prototype.map.call(document.querySelectorAll('[data-p="animasi"] .cap-afx-view:not([hidden]) .cap-afx-grid'), function(g){ return g.getAttribute('aria-label'); }).join('|')`);
  t.check(`en: preset grids per role, engine preset labels in English (Soft fade, Word pop) (${secT})`, /in/.test(secT) &&
    await ev(`(function(){ var l = Array.prototype.map.call(document.querySelectorAll('[data-p="animasi"] .cap-afx-l'), function(x){ return x.textContent; }); return l.indexOf('Word pop') >= 0 && l.indexOf('None') >= 0 && l.indexOf('Pop per kata') < 0; })()`));
  // "Animasi dasar" holds the classic pickers of captions_style.js (labels from the template options cache, not this
  // package): checked by the style tests, skipped here
  await ev(`(Array.prototype.forEach.call(document.querySelectorAll('[data-p="animasi"] details'), function(d){ d.setAttribute('data-i18n-skip', ''); }), true)`);
  let left = await t.idLeftovers('[data-p="animasi"]');
  t.check('en: no Indonesian left in the Library view', left.length === 0, left.join(' | '));
  t.check('en: "Basic animation" section title', /Basic animation/.test(await t.text('[data-p="animasi"] details summary')));
  await segBtn('Studio');
  await t.waitFor(`document.querySelectorAll('.cap-afx-trk').length === 8 && /Letter spacing/.test(document.querySelector('.cap-afx-tracks').textContent)`, 15000, 'english studio');
  t.check('en: Studio tracks + buttons in English', /Animation studio/.test(await t.text('[data-p="animasi"] .cap-afx-view:not([hidden])')) &&
    /Save as my animation/.test(await t.text('[data-p="animasi"] .cap-afx-view:not([hidden])')) && /Play in preview/.test(await t.text('[data-p="animasi"] .cap-afx-view:not([hidden])')));
  left = await t.idLeftovers('[data-p="animasi"]');
  t.check('en: no Indonesian left in the Studio view', left.length === 0, left.join(' | '));
  await segBtn('Sound effects');
  await t.waitFor(`document.querySelectorAll('.cap-sfx-rule').length === 4 && /Important words/.test(document.querySelector('.cap-sfx-rules').textContent)`, 20000, 'english sfx');
  await ev(`(AC.cap.animUI.sfxPlan(), true)`);
  await t.waitFor(`document.querySelectorAll('.cap-sfx-hit').length > 3 && / sounds?/.test(document.querySelector('.cap-sfx-sum').textContent)`, 30000, 'english sfx plan');
  t.check('en: sound rules + plan in English (Numbers, Important words, "N sounds", Klipora SFX track)',
    /Numbers/.test(await t.text('.cap-sfx-rules')) && /^\d+ sounds?/.test(await t.text('.cap-sfx-sum')) && /"Klipora SFX" audio track/.test(await t.text('[data-p="animasi"] .cap-afx-view:not([hidden])')) &&
    /Apply to timeline/.test(await t.text('[data-p="animasi"] .cap-afx-view:not([hidden])')));
  left = await t.idLeftovers('[data-p="animasi"]');
  t.check('en: no Indonesian left in the Sound effects view', left.length === 0, left.join(' | '));
  const tplEn = await ev(`AC.cap.worker('templates', {options: false}).promise.then(function(r){ var x = r.templates.filter(function(t){ return t.id === 'judul_emas'; })[0]; return x.name + '|' + x.group_label; })`);
  t.check(`en: template names come from the engine in English (judul_emas = ${tplEn})`, tplEn === 'Gold Title|Cinematic');
  await shot('en_sfx', '[data-p="animasi"] .cap-afx-top');
  await ev(`(AC.i18n.set('id'), true)`);
  await t.waitFor(`AC.cap.S.view === 'editor' && document.querySelector('#cap-tab-animasi')`, 30000, 'editor back in id');
  await ev(`(document.querySelector('#cap-tab-animasi').click(), true)`);
  await t.waitFor(`/Pustaka/.test((document.querySelector('[data-p="animasi"] .cap-afx-top') || {}).textContent || '')`, 15000, 'back to Indonesian');
  t.check('id: switched back to Indonesian', true);
  t.check('no page errors', !t.errors.length, t.errors.join(' | '));
}
