// repeat.mjs: Potong Pengulangan page in headless Chrome with the fake CEP (run: node tools/ui_test.mjs
// tools/ui_tests/repeat.mjs [--engine live|mock]). With --engine live the real engine/ac/tools/repeats.py runs on the
// 34,6 min test media (cached transcript, no GPU job, no AI quota): main pane -> analyze -> Tinjau (strikethrough rows,
// "Transkrip ngaco" flags, seek, Dengar preview through the worker, markers, filters) -> apply (clone + extract in the
// stub) -> result; "Marker saja" mode; AI switch follows Settings; empty result on the 49 s clip.
export default async function (t) {
  const ev = (s) => t.ev(s);
  const live = t.engine === 'live';
  const label = () => t.text('#primary .lbl') .then((x) => x || t.text('#primary'));
  if (!live) {
    // mock engine: a tiny analyze/apply/preview with the real result shapes
    await ev(`(__acStub.engineMock.repeats = function (job, emit, done) {
      var fs = require('fs'), path = require('path'), dir = job.workdir;
      try { fs.mkdirSync(dir, { recursive: true }); } catch (e) {}
      if (job.action === 'analyze') {
        var items = [
          { id: 'm34827', t0: 348.27, t1: 349.31, kind: 'restart', on: false, conf: 0.6, label: 'jam kerja ya', drop: 'jam kerja ya', keep: 'Jam kerja itu palingan', ctx: { pre: 'Kalau misalkan di', post: 'Jam kerja itu palingan' }, why: 'Awal kalimat diulang (2 kata)', auto: true, tight: true },
          { id: 's57355', t0: 573.55, t1: 574.15, kind: 'stutter', on: true, conf: 0.85, label: 'Gak usah,', drop: 'Gak usah,', keep: 'gak usah pakai hosting', after: 'Ini gak usah', ctx: { pre: 'Terus untuk hosting', post: 'gak usah pakai hosting' }, why: 'Kata diulang langsung (2 kata)', auto: true, tight: false }];
        var doc = { v: 1, tool: 'repeats', timebase: 'sequence', duration: job.seq.duration, fps: 120, seq: { id: job.seq.id, name: job.seq.name }, items: items,
                    flags: [{ t0: 1860.26, t1: 1919.19, text: 'ini ini ini', kind: 'loop', label: 'Kata berulang tanpa suara' },
                            { t0: 1954.56, t1: 1971.55, text: 'Terima kasih telah menonton.', kind: 'phrase', label: 'Kalimat penutup palsu' }], ai: { used: false }, params: job.params, stats: {} };
        var p = path.join(dir, 'repeats_review.json'); fs.writeFileSync(p, JSON.stringify(doc));
        emit({ ev: 'result', data: { review: p, summary: { n: 2, on: 1, sec_on: 0.6 }, flags: doc.flags, stats: { words: 2803 } } }); done(0);
      } else if (job.action === 'apply') {
        emit({ ev: 'result', data: { plan: { kind: 'remove_ranges', ranges: [[573.55, 574.15]], timebase: 'sequence' }, summary: { n: 2, on: 1, sec_on: 0.6, count: 1 } } }); done(0);
      } else {   // preview: a tiny 8-bit WAV whose bytes are all ASCII (the harness fs bridge writes text)
        var wav = path.join(dir, 'repeat_preview', job.params.id + '.wav'), data = new Array(65).join(String.fromCharCode(127));
        try { fs.mkdirSync(path.dirname(wav), { recursive: true }); } catch (e) {}
        fs.writeFileSync(wav, 'RIFF' + String.fromCharCode(100, 0, 0, 0) + 'WAVEfmt ' + String.fromCharCode(16, 0, 0, 0, 1, 0, 1, 0, 64, 31, 0, 0, 64, 31, 0, 0, 1, 0, 8, 0) + 'data' + String.fromCharCode(64, 0, 0, 0) + data);
        emit({ ev: 'result', data: { path: wav, dur: 0.008, pre: 2 } }); done(0);
      }
    }, true)`);
  }

  await t.setSeq('long35'); await t.wait(400);
  await t.go('tool/repeat');
  await t.waitFor(`document.querySelector('.screen[data-screen="tool-repeat"] .tool-hero')`, 3000, 'repeat page');
  const scr = '.screen[data-screen="tool-repeat"]';
  t.check('main: title + tab', (await t.text('#crumbTitle')) === 'Potong Pengulangan' && (await t.text('#subnav [aria-selected="true"]')) === 'Ulang');
  t.check('main: 3 presets (Ketat/Normal/Longgar)', (await t.count(`${scr} .seg[aria-label="Gaya potong pengulangan"] button`)) === 3);
  t.check('main: 4 kind chips on', (await t.count(`${scr} .chips-wrap .chip[aria-pressed="true"]`)) === 4);
  t.check('main: AI switch off by default', (await ev(`document.querySelector('${scr} input[role="switch"]').checked`)) === false);
  t.check('main: result modes', (await t.count(`${scr} .opts .opt`)) === 2);
  t.check('main: dock = Cari pengulangan', /Cari pengulangan/.test(await t.text('#primary')));
  await t.waitFor(`/Transkrip tersimpan/.test(document.querySelector('${scr} .src .tags').textContent)`, 4000, 'transcript tag');
  t.check('main: transcript tag', true);
  // chips: no kind -> primary disabled
  for (let i = 0; i < 4; i++) await ev(`(document.querySelectorAll('${scr} .chips-wrap .chip')[${i}].click(), true)`);
  t.check('main: no kind -> primary disabled', await ev(`document.querySelector('#primary').disabled === true`));
  for (let i = 0; i < 4; i++) await ev(`(document.querySelectorAll('${scr} .chips-wrap .chip')[${i}].click(), true)`);
  t.check('main: kinds back -> primary enabled', await ev(`document.querySelector('#primary').disabled === false`));
  // preset -> sliders; manual change -> Custom
  await ev(`(document.querySelectorAll('${scr} .seg[aria-label="Gaya potong pengulangan"] button')[0].click(), true)`);
  t.check('preset Ketat sets sliders', (await ev(`AC.tools.runtime('repeat').ctx.state.minOn`)) === 80 && (await ev(`document.querySelector('${scr} details output').textContent`)) === '80%');
  await ev(`(function(){ var i=document.querySelector('${scr} details input[type=range]'); i.value='70'; i.dispatchEvent(new Event('input')); return true; })()`);
  t.check('manual slider -> Custom', (await ev(`AC.tools.runtime('repeat').ctx.state.preset`)) === null);
  await ev(`(document.querySelectorAll('${scr} .seg[aria-label="Gaya potong pengulangan"] button')[1].click(), true)`);
  t.check('preset Normal restores', (await ev(`AC.tools.runtime('repeat').ctx.state.minOn`)) === 65);
  // AI switch follows Settings
  await ev(`(AC.settings.set('ai', false), true)`); await t.wait(50);
  t.check('AI off in Settings -> switch disabled + note', (await ev(`document.querySelector('${scr} input[role="switch"]').disabled`)) && /Pengaturan/.test(await t.text(`${scr} .repeat-ai-note`)));
  await ev(`(AC.settings.set('ai', true), true)`); await t.wait(50);
  t.check('AI on -> switch enabled', (await ev(`document.querySelector('${scr} input[role="switch"]').disabled`)) === false);
  t.check('main: no horizontal overflow at ' + t.width + ' px', await ev(`(function(){ var v=document.getElementById('view')||document.body; return document.documentElement.scrollWidth <= window.innerWidth + 1 && Array.prototype.every.call(document.querySelectorAll('.screen[data-screen="tool-repeat"] *'), function(e){ var r=e.getBoundingClientRect(); return !r.width || r.right <= window.innerWidth + 1; }); })()`));
  await t.shot(t.width === 380 ? 'build_repeat_main' : 'build_repeat_main_' + t.width);

  // ---------------------------------------------------------------- analyze -> Tinjau
  await t.click('#primary');
  await t.waitFor(`location.hash === '#tool/repeat/review'`, live ? 60000 : 8000, 'review pane');
  await t.wait(250);
  const job = await ev(`AC.tools.runtime('repeat').task.job`);
  t.check('job: engine tool repeats + params', job && job.tool === 'repeats' && job.params.min_on === 0.65 && job.params.sim === 0.8 && job.params.kinds.length === 4 && job.params.ai === false && job.params.scope && job.params.scope.kind === 'all', JSON.stringify(job && job.params));
  const n = await ev(`AC.tools.runtime('repeat').review.items().length`), on = await ev(`AC.tools.runtime('repeat').review.count()`);
  t.check(`review: ${n} found, ${on} checked`, n >= (live ? 12 : 2) && on >= 1 && on < n);
  t.check('review: rows with strikethrough + kept take', (await t.count('.rv .repeat-ctx del')) > 0 && (await t.count('.rv .repeat-ctx ins')) > 0);
  t.check('review: kind tag + confidence', (await t.count('.rv .rv-l1 .repeat-kind')) > 0 && (await t.count('.rv .rv-l1 .conf')) > 0);
  t.check('review: notes on rows', (await t.count('.rv .rv-note')) > 0);
  t.check('review: legend line', (await t.count('.rv-head .repeat-legend')) === 1);
  t.check('review: Transkrip ngaco flags block', /Transkrip ngaco di 2 tempat/.test(await t.text('.repeat-flags')) && (await t.count('.repeat-flag-list .chip')) === 2);
  t.check('review: kind filter chips', /Gagap/.test(await t.text('.rv-tools')) && /Perlu dicek/.test(await t.text('.rv-tools')));
  t.check('review: dock label', (await label()) === `Potong ${on} pengulangan`, await label());
  // row height consistent (two-line snippet fits the virtual row)
  t.check('review: snippet fits row', await ev(`(function(){ var r=document.querySelector('.rv'); var c=r.querySelector('.repeat-ctx'); return c.getBoundingClientRect().bottom <= r.getBoundingClientRect().bottom + 0.5 && c.getBoundingClientRect().height >= 12; })()`));
  t.check('review: no horizontal overflow at ' + t.width + ' px', await ev(`(function(){ var v=document.getElementById('view')||document.body; return document.documentElement.scrollWidth <= window.innerWidth + 1 && Array.prototype.every.call(document.querySelectorAll('.repeat-flags *, .rv *, .rv-bulk *, .repeat-legend'), function(e){ var r=e.getBoundingClientRect(); return !r.width || r.right <= window.innerWidth + 1; }); })()`));
  await t.shot(t.width === 380 ? 'build_repeat' : 'build_repeat_' + t.width);

  // filter by kind
  await ev(`(document.querySelector('.rv-tools [data-f="stutter"]').click(), true)`); await t.wait(60);
  const stut = await ev(`AC.tools.runtime('repeat').review.items().filter(function(i){return i.kind==='stutter'}).length`);
  const kindsShown = await ev(`Array.prototype.map.call(document.querySelectorAll('.rv .repeat-kind'), function(e){return e.textContent})`);
  t.check('filter Gagap', kindsShown.length === stut && kindsShown.every((k) => k === 'Gagap'), JSON.stringify(kindsShown));
  await ev(`(document.querySelector('.rv-tools [data-f="all"]').click(), true)`); await t.wait(60);

  // row click -> seek; Dengar -> preview wav through the worker
  const calls0 = (await t.calls('bac_seek')).length;
  await ev(`(document.querySelector('.rv[data-i="0"] .rv-main').click(), true)`); await t.wait(250);
  const seeks = await t.calls('bac_seek');
  const it0 = await ev(`AC.tools.runtime('repeat').review.items()[0]`);
  t.check('row click seeks Premiere to t0', seeks.length > calls0 && Math.abs(seeks[seeks.length - 1][0] - it0.t0) < 1e-6);
  t.check('active row shows Dengar', /Dengar/.test(await t.text('.rv.is-active .rv-actions')));
  await ev(`(Array.prototype.filter.call(document.querySelectorAll('.rv.is-active .rv-actions button'), function(b){return /Dengar/.test(b.textContent)})[0].click(), true)`);
  await t.waitFor(`document.querySelector('audio.repeat-audio') && document.querySelector('audio.repeat-audio').getAttribute('data-id') === ${JSON.stringify(it0.id)}`, live ? 30000 : 5000, 'preview audio');
  const src = await ev(`document.querySelector('audio.repeat-audio').src`);
  t.check('Dengar: wav from the engine', /^file:\/\/\/.*repeat_preview\/.*\.wav$/.test(decodeURI(src)), src);
  const wav = decodeURI(src).replace('file:///', '').replace(/\//g, '\\');
  t.check('Dengar: wav exists', await ev(`AC.sys.exists(${JSON.stringify(wav)})`), wav);

  // toggle one row -> live dock label; touched written back on apply
  await ev(`(document.querySelector('.rv[data-i="0"] .ck').click(), true)`); await t.wait(80);
  const on2 = await ev(`AC.tools.runtime('repeat').review.count()`);
  t.check('toggle updates dock label', on2 !== on && (await label()) === `Potong ${on2} pengulangan`);

  // markers: bulk "Kirim ke marker" (host file missing in the stub -> foundation fallback), then our host fn
  await ev(`(__acStub.markers = [], true)`);
  await ev(`(Array.prototype.filter.call(document.querySelectorAll('.rv-bulk .linkbtn'), function(b){return /marker/.test(b.textContent)})[0].click(), true)`);
  await t.waitFor(`__acStub.markers.length === ${on2}`, 5000, 'fallback markers');
  t.check('Kirim ke marker: fallback when host fn missing', /Ulang \(/.test(await ev(`__acStub.markers[0].name`)) && (await ev(`__acStub.markers[0].tag`)) === '[Klipora-ULANG]');
  await t.host('bac_repeat_markers', `function (list, tag) { __acStub.repeatMarkers = { list: list, tag: tag }; return JSON.stringify({ ok: true, n: list.length, removed: 0 }); }`);
  await ev(`(Array.prototype.filter.call(document.querySelectorAll('.rv-bulk .linkbtn'), function(b){return /marker/.test(b.textContent)})[0].click(), true)`);
  await t.waitFor(`__acStub.repeatMarkers`, 5000, 'host markers');
  t.check('Kirim ke marker: bac_repeat_markers', (await ev(`__acStub.repeatMarkers.list.length`)) === on2 && (await ev(`__acStub.repeatMarkers.tag`)) === '[Klipora-ULANG]' && /Simpan:/.test(await ev(`__acStub.repeatMarkers.list[0].comment`)));
  await ev(`(Array.prototype.filter.call(document.querySelectorAll('.repeat-flags button'), function(b){return /Tandai/.test(b.textContent)})[0].click(), true)`);
  await t.waitFor(`__acStub.repeatMarkers.tag === '[Klipora-NGACO]'`, 5000, 'flag markers');
  t.check('Transkrip ngaco -> yellow markers', (await ev(`__acStub.repeatMarkers.list[0].color`)) === 4 && (await ev(`__acStub.repeatMarkers.list.length`)) === 2);
  const fseek = (await t.calls('bac_seek')).length;
  await ev(`(document.querySelector('.repeat-flag-list .chip').click(), true)`); await t.wait(150);
  t.check('flag chip seeks', (await t.calls('bac_seek')).length === fseek + 1);

  // ---------------------------------------------------------------- apply -> new sequence
  await ev(`(__acStub.calls = [], true)`);
  await t.click('#primary');
  await t.waitFor(`location.hash === '#tool/repeat/result'`, live ? 60000 : 8000, 'result pane');
  const doc = JSON.parse(await ev(`AC.sys.readText(AC.tools.runtime('repeat').reviewOpts.file)`));
  t.check('apply: edited review written back (touched)', doc.items.filter((x) => x.touched).length === 1 && doc.items.filter((x) => x.on).length === on2);
  const clones = await t.calls('bac_cloneSeq'), rr = await t.calls('bac_removeRanges');
  t.check('apply: clone + extract', clones.length === 1 && rr.length >= 1 && rr[0][1].length >= 1, JSON.stringify(rr[0] && rr[0][1].slice(0, 2)));
  t.check('apply: result card', /Sequence baru siap/.test(await t.text(`${scr} [data-pane="result"] h2`)) && /pengulangan dibuang/.test(await t.text(`${scr} [data-pane="result"] .legend`)));
  t.check('apply: next = Potong Silence', /Lanjut ke Potong Silence/.test(await t.text('#primary')));
  if (t.width === 380) await t.shot('build_repeat_result');

  // ---------------------------------------------------------------- "Marker saja" mode
  await t.go('tool/repeat');
  await ev(`(document.querySelectorAll('${scr} .opts input')[1].click(), true)`);
  await ev(`(__acStub.repeatMarkers = null, true)`);
  await t.click('#primary');
  await t.waitFor(`location.hash === '#tool/repeat/review'`, live ? 60000 : 8000, 'review pane 2');
  await t.wait(200);
  const on3 = await ev(`AC.tools.runtime('repeat').review.count()`);
  t.check('markers mode: dock label', (await label()) === `Tandai ${on3} pengulangan`, await label());
  await t.click('#primary');
  await t.waitFor(`location.hash === '#tool/repeat/result'`, 8000, 'markers result');
  t.check('markers mode: result', /marker dibuat/.test(await t.text(`${scr} [data-pane="result"] h2`)) && (await ev(`__acStub.repeatMarkers.list.length`)) === on3);
  await t.go('tool/repeat');
  await ev(`(document.querySelectorAll('${scr} .opts input')[0].click(), true)`);

  // ---------------------------------------------------------------- empty result on the 49 s clip (live engine only)
  if (live) {
    await t.setSeq('raw49'); await t.wait(400);
    await t.click('#primary');
    await t.waitFor(`location.hash === '#tool/repeat/result'`, 60000, 'empty result');
    t.check('49 s: Tidak ada pengulangan', /Tidak ada pengulangan/.test(await t.text(`${scr} [data-pane="result"] h2`)));
    if (t.width === 380) await t.shot('build_repeat_empty');
    await t.setSeq('long35'); await t.wait(300);
  }

  // ---------------------------------------------------------------- English pass (live switch re-renders the idle page)
  await t.go('tool/repeat');
  await ev("AC.i18n.set('en')");
  await t.waitFor(`AC.i18n.lang() === 'en' && document.querySelector('${scr} .tool-hero')`, 5000, 'English repeat page');
  await t.wait(150);
  t.check('repeat (en): title, tab, CTA', (await t.text('#crumbTitle')) === 'Cut Repeats' && (await t.text('#subnav [aria-selected="true"]')) === 'Repeats' &&
    /^Find repeats/.test(await t.text('#primary')), (await t.text('#crumbTitle')) + ' | ' + (await t.text('#primary')));
  t.check('repeat (en): presets + kinds', (await t.count(`${scr} .seg[aria-label="Repeat cutting style"] button`)) === 3 && /Stutter/.test(await t.text(`${scr} .chips-wrap`)), await t.text(`${scr} .chips-wrap`));
  await t.waitFor(`/Transcript saved/.test(document.querySelector('${scr} .src .tags').textContent)`, 4000, 'English transcript tag');
  const leftEn = await t.idLeftovers('#app');
  t.check('repeat (en): main pane has no Indonesian leftovers', leftEn.length === 0, JSON.stringify(leftEn));
  await ev("AC.i18n.set('id')");
  await t.waitFor(`AC.i18n.lang() === 'id' && /Cari pengulangan/.test(document.getElementById('primary').textContent)`, 5000, 'back to Indonesian');
  await t.go('home');
}
