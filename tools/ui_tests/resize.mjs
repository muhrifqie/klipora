// resize.mjs: Auto Resize panel flow in headless Chrome (run: node tools/ui_test.mjs tools/ui_tests/resize.mjs
// [--engine live|mock]). Live engine = the real engine/ac/tools/resize.py on the 49 s test clip (fixture raw49);
// mock = an in-page fake that returns the same result shapes. Host functions bac_resize_* are stubbed here (the
// fake CEP never runs ExtendScript); the live Premiere checks are in docs/tools/resize.md.
import fs from 'fs';
import path from 'path';

export default async function (t) {
  const ev = (s) => t.ev(s);
  const shots = path.join(t.root, 'docs', 'shots');
  const live = t.engine === 'live';

  // ---------------- host stubs (record what the panel sends)
  await t.host('bac_resize_begin', `function (name, w, h, seqId, inS, outS) {
    var s = __acStub.seq, c = JSON.parse(JSON.stringify(s)); c.id = 'made-resize'; c.name = name; c.width = w; c.height = h; __acStub.made[c.id] = c;
    return JSON.stringify({ok: true, id: c.id, name: c.name, origId: s.id, origName: s.name, w: w, h: h, fps: s.fps, frameOk: true}); }`);
  await t.host('bac_resize_keys', `function (id, track, items, last) {
    var keys = 0; items.forEach(function (it) { keys += it.keys ? it.keys.length : 0; });
    return JSON.stringify({ok: true, done: items.length, keys: keys, missing: 0, replaced: 0}); }`);
  await t.host('bac_resize_finish', `function (id) { var c = __acStub.made[id]; return JSON.stringify({ok: true, id: id, name: c.name, w: c.width, h: c.height, duration: c.duration}); }`);
  await t.host('bac_resize_importRender', `function (p, name) { __acStub.made['made-render'] = {id: 'made-render', name: name};
    return JSON.stringify({ok: true, id: 'made-render', name: name, w: 1080, h: 1920, fps: 30, duration: 6, bin: 'Klipora'}); }`);
  await t.host('bac_resize_native', `function (num, den, preset, name) { __acStub.made['made-native'] = {id: 'made-native', name: name};
    window.__nativePolls = 0; return JSON.stringify({ok: true, id: 'made-native', name: name, w: 540, h: 960, origId: __acStub.seq.id, origName: __acStub.seq.name, preset: preset}); }`);
  await t.host('bac_resize_nativeStatus', `function () { window.__nativePolls++; return JSON.stringify({ok: true, done: window.__nativePolls >= 2}); }`);

  if (!live) {
    // In-page fake engine with the real result shapes (preview = an existing PNG of the repo).
    const png = path.join(t.root, 'docs', 'shots', 'smoke_home.png').replace(/\\/g, '\\\\');
    await ev(`(__acStub.engineMock.resize = function (job, emit, done) {
      var fs = require('fs'), p = job.params || {}, wd = job.workdir, rv = wd + '\\\\resize_review.json';
      emit({ev: 'stage', id: 'read', label: 'Baca sequence', i: 0, n: 1, w: 1});
      if (job.action === 'analyze') {
        var items = [{id: 'j1000', t0: 10, t1: 15, kind: 'jump', on: true, conf: 0.8, label: 'Pindah ke kiri', ctx: 'Halaman berganti', shot: 1, src: 'Layar'},
                     {id: 'm2000', t0: 20, t1: 26, kind: 'move', on: true, conf: 0.9, label: 'Ikuti aksi layar ke kanan', ctx: 'Kamera bergerak halus', shot: 2, src: 'Layar'}];
        fs.mkdirSync(wd, {recursive: true});
        fs.writeFileSync(rv, JSON.stringify({v: 1, tool: 'resize', timebase: 'sequence', duration: 48.9, params: p, items: items}));
        emit({ev: 'result', data: {review: rv, preview: '${png}', summary: '9:16: 1 gerakan', stats: {shots: 3, moves: 1, jumps: 1, keys: 6},
          content: [{kind: 'screen'}], target: p.target, label: '9:16', size: [1080, 1920], layout: p.layout, duration: 48.9,
          advice: p.layout === 'crop' ? {layout: 'focus', text: 'Crop 9:16 hanya menampilkan 24% lebar layar.'} : null, warnings: []}});
      } else if (job.action === 'apply') {
        emit({ev: 'result', data: {plan: {kind: 'reframe', label: '9:16', w: 1080, h: 1920, name: 'X (9x16)', track: 0, inout: null,
          clips: [{track: 0, index: 0, start: 0, end: 48.9, scale: 200, keys: [[0, 1.2, 0.5], [12, 0.9, 0.5]]}]}}});
      } else {
        emit({ev: 'result', data: {path: wd + '\\\\resize\\\\x_9x16.mp4', size_mb: 3.2, duration: 6, render: {w: 1080, h: 1920, fps: 30, secs: 4},
          plan: {kind: 'import_render', path: wd + '\\\\resize\\\\x_9x16.mp4', name: 'X (9x16 fokus)'}}});
      }
      done(0);
    }, true)`);
  }

  // ---------------- main pane
  await t.go('tool/resize');
  await t.waitFor(`document.querySelector('[data-screen="tool-resize"] .tool-body .seg')`, 5000, 'resize page');
  const body = '[data-screen="tool-resize"] [data-pane="main"]';
  t.check('resize: 4 formats, 3 engines, 4 speeds', (await t.count(`${body} .resize-fmt-help`)) === 1 &&
    (await ev(`document.querySelectorAll('${body} .seg')[1].querySelectorAll('button').length`)) === 4 &&
    (await t.count(`${body} .opts .opt`)) === 3 &&
    (await ev(`document.querySelectorAll('${body} .tool-body > .sec')[2].querySelectorAll('.seg button').length`)) === 4);
  t.check('resize: default 9:16 Pintar Normal', (await ev(`document.querySelector('${body} .tool-body .seg button[aria-checked="true"]').textContent`)) === '9:16' &&
    (await ev(`document.querySelector('${body} .opts .opt.is-on b').textContent`)) === 'Pintar (Klipora)');
  t.check('resize: primary = Pratinjau kamera', (await t.text('#primary .lbl')) === 'Pratinjau kamera');
  t.check('resize: scope In/Out offered, Clip terpilih not', (await ev(`Array.prototype.map.call(document.querySelectorAll('${body} .src .seg button'), function (b) { return b.textContent; }).join('|')`)) === 'Seluruh sequence|In/Out');
  await t.shot('build_resize');

  // ---------------- Pintar: analyze -> preview
  await t.click('#primary');
  await t.waitFor(`location.hash === '#tool/resize/result'`, live ? 180000 : 15000, 'preview pane');
  await t.waitFor(`(function(){ var i = document.querySelector('.resize-sheet'); return i && i.complete && i.naturalWidth > 0; })()`, 10000, 'preview image loaded');
  t.check('preview: title + stats', /Pratinjau kamera 9:16/.test(await t.text('[data-pane="result"] .result-h h2')) && (await t.count('[data-pane="result"] .sum-strip > div')) === 3);
  t.check('preview: crop advice offers Fokus + blur', /terpotong/.test(await t.text('[data-pane="result"] .alert-warn b')) &&
    (await ev(`Array.prototype.some.call(document.querySelectorAll('[data-pane="result"] .btn'), function (b) { return /Pakai Fokus/.test(b.textContent); })`)));
  t.check('preview: primary = Buat sequence 9:16', (await t.text('#primary .lbl')) === 'Buat sequence 9:16');
  await t.shot('build_resize_preview');

  // ---------------- Tinjau gerakan
  const hasRows = await ev(`Array.prototype.some.call(document.querySelectorAll('[data-pane="result"] .btn'), function (b) { return b.textContent === 'Ubah pilihan'; })`);
  t.check('preview: Tinjau available (camera moves found)', hasRows);
  if (hasRows) {
    await ev(`Array.prototype.filter.call(document.querySelectorAll('[data-pane="result"] .btn'), function (b) { return b.textContent === 'Ubah pilihan'; })[0].click()`);
    await t.waitFor(`location.hash === '#tool/resize/review' && document.querySelectorAll('[data-screen="tool-resize"] .rv').length > 0`, 5000, 'review rows');
    const legend = await t.text('[data-screen="tool-resize"] .rv-head .legend');
    t.check('review: resize legend (no "Hemat")', !/Hemat/.test(legend) && /ditahan/.test(legend), legend);
    t.check('review: title counts camera moves', /gerakan kamera dipakai/.test(await t.text('[data-screen="tool-resize"] .rv-title')));
    await ev(`document.querySelector('[data-screen="tool-resize"] .rv').click()`);
    await t.wait(150);
    t.check('review: row action wording', /Tahan kamera di sini/.test(await t.text('[data-screen="tool-resize"] .rv.is-active .rv-actions')));
    await t.shot('build_resize_review');
    await t.click('#primary');                                    // engine apply -> host keys
  } else {
    await t.click('#primary');
  }
  await t.waitFor(`location.hash === '#tool/resize/result' && /Sequence 9:16 siap/.test((document.querySelector('[data-pane="result"] .result-h h2')||{}).textContent||'')`, live ? 60000 : 15000, 'reframe result');
  const begin = await t.calls('bac_resize_begin');
  const keys = await t.calls('bac_resize_keys');
  t.check('apply: clone named + frame 1080x1920', begin.length === 1 && begin[0][1] === 1080 && begin[0][2] === 1920 && /\(9x16\)$/.test(begin[0][0]), JSON.stringify(begin[0] || null).slice(0, 200));
  const items = keys.reduce((a, c) => a.concat(c[2]), []);
  t.check('apply: Motion items sent (scale 200% for 2292x960 -> 1080x1920)', items.length >= 1 && Math.abs(items[0].scale - 200) < 0.01, JSON.stringify(items[0] || null).slice(0, 200));
  t.check('apply: positions normalised inside the frame cover range', items.every((it) => (it.keys || [[0].concat(it.pos)]).every((k) => k[1] >= -1.5 && k[1] <= 2.5 && Math.abs(k[2] - 0.5) < 1e-6)));
  t.check('apply: last chunk flagged', keys.length >= 1 && keys[keys.length - 1][3] === true);
  t.check('apply: finish opened the new sequence', (await t.calls('bac_resize_finish')).length === 1);
  t.check('result: Buka yang asli + Hapus hasil', /Buka yang asli/.test(await t.text('[data-pane="result"] .btn-row')) && /Hapus hasil/.test(await t.text('[data-pane="result"] .btn-row')));
  await t.shot('build_resize_result');

  // ---------------- Fokus + blur: In/Out scope (6 s) -> render -> import
  await t.setSeq(Object.assign(await ev('JSON.parse(JSON.stringify(__acStub.seq))'), { inPoint: 10, outPoint: 16 }));
  await t.wait(400);
  await t.go('tool/resize');
  await t.wait(100);
  await ev(`document.querySelectorAll('[data-screen="tool-resize"] .opts .opt input')[1].click()`);
  await ev(`(function(){ var b = Array.prototype.filter.call(document.querySelectorAll('[data-screen="tool-resize"] .src .seg button'), function (x) { return x.textContent === 'In/Out'; })[0]; b.click(); return true; })()`);
  t.check('focus: render estimate note', /Render sekitar/.test(await t.text('[data-screen="tool-resize"] .resize-note')));
  await t.click('#primary');
  await t.waitFor(`location.hash === '#tool/resize/result' && /Pratinjau/.test(document.querySelector('[data-pane="result"] .result-h h2').textContent)`, live ? 60000 : 15000, 'focus preview');
  t.check('focus: primary = Render video 9:16', (await t.text('#primary .lbl')) === 'Render video 9:16');
  await t.click('#primary');
  await t.waitFor(`location.hash === '#tool/resize/result' && /Video 9:16 siap/.test(document.querySelector('[data-pane="result"] .result-h h2').textContent)`, live ? 240000 : 15000, 'render result');
  const imp = await t.calls('bac_resize_importRender');
  t.check('render: mp4 imported', imp.length === 1 && /\.mp4$/i.test(imp[0][0]), JSON.stringify(imp));
  if (live && imp.length) {
    const mp4 = imp[0][0];
    const ok = fs.existsSync(mp4) && fs.statSync(mp4).size > 50000;
    t.check('render: real MP4 written in the sandbox workdir', ok, mp4);
  }
  await t.shot('build_resize_render');

  // ---------------- Premiere Auto Reframe (host only)
  await t.go('tool/resize');
  await t.wait(100);
  await ev(`document.querySelectorAll('[data-screen="tool-resize"] .opts .opt input')[2].click()`);
  t.check('native: primary label + Diam disabled', (await t.text('#primary .lbl')) === 'Buat dengan Auto Reframe' &&
    (await ev(`Array.prototype.filter.call(document.querySelectorAll('[data-screen="tool-resize"] .tool-body > .sec')[2].querySelectorAll('.seg button'), function (b) { return b.textContent === 'Diam'; })[0].disabled`)));
  await t.click('#primary');
  await t.waitFor(`location.hash === '#tool/resize/result' && /Auto Reframe Premiere siap/.test(document.querySelector('[data-pane="result"] .result-h h2').textContent)`, 15000, 'native result');
  const nat = await t.calls('bac_resize_native');
  t.check('native: 9:16 default preset', nat.length === 1 && nat[0][0] === 9 && nat[0][1] === 16 && nat[0][2] === 'default', JSON.stringify(nat));
  await t.shot('build_resize_native');

  // back to Pintar for the next run of the panel
  await t.go('tool/resize');
  await ev(`document.querySelectorAll('[data-screen="tool-resize"] .opts .opt input')[0].click()`);

  // ---------------- English UI: main pane, camera preview, review (fresh analysis, the job runs in English too)
  await t.setSeq(Object.assign(await ev('JSON.parse(JSON.stringify(__acStub.seq))'), { inPoint: undefined, outPoint: undefined }));
  await ev(`(AC.i18n.set('en'), true)`);
  await t.go('tool/resize');
  await t.waitFor(`document.querySelector('${body} .opts .opt.is-on')`, 5000, 'resize main pane (en)');
  await t.wait(400);
  let left = await t.idLeftovers('#app');
  t.check('resize en: main pane has no Indonesian left', left.length === 0, left.join(' | '));
  t.check('resize en: exact English labels (primary, method card, speed)', (await t.text('#primary .lbl')) === 'Preview camera' &&
    (await ev(`document.querySelector('${body} .opts .opt.is-on b').textContent`)) === 'Smart (Klipora)' &&
    /Camera motion/.test(await t.text(body)), await t.text('#primary .lbl'));
  await t.click('#primary');
  await t.waitFor(`location.hash === '#tool/resize/result' && /Camera preview/.test((document.querySelector('[data-pane="result"] .result-h h2')||{}).textContent||'')`, live ? 180000 : 15000, 'preview pane (en)');
  await t.wait(300);
  left = await t.idLeftovers('#app');
  t.check('resize en: camera preview has no Indonesian left', left.length === 0, left.join(' | '));
  t.check('resize en: preview primary in English', /^Create 9:16 sequence$|^Render 9:16 video$/.test(await t.text('#primary .lbl')), await t.text('#primary .lbl'));
  const editLbl = await ev(`AC.t('job.editChoice')`);
  if (await ev(`Array.prototype.some.call(document.querySelectorAll('[data-pane="result"] .btn'), function (b) { return b.textContent === ${JSON.stringify(editLbl)}; })`)) {
    await ev(`Array.prototype.filter.call(document.querySelectorAll('[data-pane="result"] .btn'), function (b) { return b.textContent === ${JSON.stringify(editLbl)}; })[0].click()`);
    await t.waitFor(`location.hash === '#tool/resize/review' && document.querySelectorAll('[data-screen="tool-resize"] .rv').length > 0`, 5000, 'review rows (en)');
    await t.wait(300);
    left = await t.idLeftovers('#app');
    t.check('resize en: review has no Indonesian left', left.length === 0, left.join(' | '));
    t.check('resize en: review title in English', /camera moves? used/.test(await t.text('[data-screen="tool-resize"] .rv-title')), await t.text('[data-screen="tool-resize"] .rv-title'));
    await t.shot('build_resize_review_en');
  } else t.check('resize en: review reachable', false, 'no "' + editLbl + '" button');
  await ev(`(AC.i18n.set('id'), true)`);
  await t.go('tool/resize');
  t.check('shots written', fs.existsSync(path.join(shots, 'build_resize.png')));
}
