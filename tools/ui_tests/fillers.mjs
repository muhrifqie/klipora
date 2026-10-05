// fillers.mjs: Hapus Filler page in headless Chrome (run: node tools/ui_test.mjs tools/ui_tests/fillers.mjs [--engine live|mock]).
// Live engine: real engine/ac/tools/fillers.py on the 2-min test media (cached transcripts) -> Tinjau -> apply on the stub.
// Screenshots: docs/shots/build_fillers.png (settings), build_fillers_review.png, build_fillers_result.png.
const M2 = globalThis.KLIPORA_MEDIA.seq_2m;   // KLIPORA_TEST_MEDIA/seq_2m.mp4 (see tools/ui_test.mjs)
const D2 = 126.892;
const S = '[data-screen="tool-fillers"]';

function seq2() {
  const clip = { name: 'Interview - Part 2.mp4', path: M2, start: 0, end: D2, in: 0, out: D2, speed: 1, disabled: false, selected: false, mgt: false, nested: false, nodeId: 'f0' };
  const tr = (kind) => [{ index: 0, name: kind + ' 1', muted: false, locked: false, targeted: true, count: 1, clips: [Object.assign({}, clip)] }];
  return { id: 'seq-2m', name: 'Interview - Part 2', fps: 120, timebase: '2116800000', width: 2292, height: 960, displayFormat: 998, duration: D2,
           inPoint: null, outPoint: null, player: 0, active: true, level: 'full', video: tr('Video'), audio: tr('Audio'), markers: [], selection: [], selectedCount: 0, ms: 3 };
}

// In-page fake of engine/ac/tools/fillers.py for --engine mock (same review shape, 3 items).
const MOCK = `function (job, emit, done) {
  var fs = require('fs'), path = require('path');
  if (job.action === 'apply') {
    var d = JSON.parse(fs.readFileSync(job.review, 'utf8')), rs = d.items.filter(function (it) { return it.on; }).map(function (it) { return [it.t0, it.t1]; });
    emit({ev:'stage', id:'plan', label:'Hitung potongan', i:0, n:1, w:1}); emit({ev:'stage_done', id:'plan', sec:0.01});
    emit({ev:'result', data:{plan:{kind:'remove_ranges', ranges:rs, timebase:'sequence'}, summary: rs.length + ' filler dibuang', counts:{filler: rs.length, gap: 0}}});
    return done(0);
  }
  ['words','listen','sound','review'].forEach(function (s, i) { emit({ev:'stage', id:s, label:s, i:i, n:4, w:0.25}); emit({ev:'progress', pct:100, all:(i+1)*25}); emit({ev:'stage_done', id:s, sec:0.01}); });
  var items = [
    {id:'f3832', t0:38.32, t1:38.68, kind:'filler', on:true, conf:0.85, label:'eee', tier:'always', src:'listener', srcs:['listener','acoustic'], tight:true, sound:[38.34,38.66], ctx:{pre:'masuk ya Saya bakal daftar dulu', post:'Gunakan email yang aktif ya'}, note:{type:'warn', text:'Mepet ke kata. Dengar dulu sebelum dibuang.'}},
    {id:'h10567', t0:105.67, t1:105.86, kind:'habit', on:true, conf:0.7, label:'ya', tier:'habit', src:'transcript', srcs:['transcript'], tight:false, key:'ya', ctx:{pre:'gak duplikat', post:'Kebetulan itu'}, note:{type:'info', text:'Penegas di akhir kalimat.'}},
    {id:'s2541', t0:25.41, t1:26.05, kind:'sound', on:false, conf:0.15, label:'suara ragu', tier:'acoustic', src:'acoustic', srcs:['acoustic'], tight:false, ctx:{pre:'sampai akhir', post:'Baik, pertama'}, note:{type:'info', text:'Suara bersuara tanpa kata.'}}];
  var rp = job.workdir + '\\\\fillers_review.json';
  fs.mkdirSync(path.dirname(rp), {recursive:true});
  fs.writeFileSync(rp, JSON.stringify({v:1, tool:'fillers', timebase:'sequence', duration:${D2}, fps:120, items:items}));
  emit({ev:'result', data:{review: rp, summary:{n:3, on:2, sec_on:0.55}, counts:{always:1, habit:1, acoustic:1, gap:0}, habit_counts:{ya:5, guys:5}, silence:null}});
  done(0);
}`;

export default async function (t) {
  const live = t.engine === 'live';
  if (!live) await t.ev(`(__acStub.engineMock.fillers = ${MOCK}, true)`);
  await t.setSeq(seq2());
  await t.waitFor(`AC.seq.peek() && AC.seq.peek().id === 'seq-2m'`, 5000, 'test sequence active');
  await t.go('tool/fillers');
  await t.waitFor(`document.querySelector('${S} .tool-hero')`, 3000, 'fillers page');

  // ---- settings page
  t.check('fillers: page renders (hero, 4 switches, 19 habit chips, presets)',
    (await t.count(`${S} .tool-body .sw`)) === 4 && (await t.count(`${S} .fillers-habits .chip`)) === 19 && (await t.count(`${S} .tool-body .seg button`)) === 3);
  t.check('fillers: dock primary "Deteksi filler"', /Deteksi filler/.test(await t.text('#primary')));
  t.check('fillers: habit words off by default', (await t.count(`${S} .fillers-habits .chip[aria-pressed="true"]`)) === 0);
  t.check('fillers: hesitation + anu + acoustic on by default', await t.ev(`(function(){ var s = document.querySelectorAll('${S} .tool-body .sw input'); return s[0].checked && s[1].checked && s[2].checked && !s[3].checked; })()`));
  t.check('fillers: aside counts active words', /^9 kata aktif$/.test(await t.text(`${S} .tool-body .sec .aside`)));
  const out = () => t.text(`${S} .sec-d output`);
  await t.ev(`(Array.prototype.filter.call(document.querySelectorAll('${S} .tool-body .seg button'), function (b) { return b.textContent === 'Hati-hati'; })[0].click(), true)`);
  t.check('fillers: preset Hati-hati -> 80%', (await out()) === '80%' && (await t.ev(`AC.tools.runtime('fillers').ctx.state.threshold`)) === 0.8);
  await t.ev(`(function(){ var r = document.querySelector('${S} .sec-d input[type=range]'); r.value = '0.7'; r.dispatchEvent(new Event('input')); return true; })()`);
  t.check('fillers: manual threshold -> Custom', (await out()) === '70%' && /Custom/.test(await t.text(`${S} .sec-d .aside`)));
  await t.ev(`(Array.prototype.filter.call(document.querySelectorAll('${S} .tool-body .seg button'), function (b) { return b.textContent === 'Seimbang'; })[0].click(), true)`);
  t.check('fillers: preset Seimbang -> 65%', (await out()) === '65%' && !(await t.text(`${S} .sec-d .aside`)));
  // everything off -> primary disabled; back on
  await t.ev(`(function(){ var s = document.querySelectorAll('${S} .tool-body .sw input'); [0,1,2].forEach(function (i) { s[i].click(); }); return true; })()`);
  t.check('fillers: nothing enabled -> primary disabled', await t.ev(`document.getElementById('primary').disabled`));
  await t.ev(`(function(){ var s = document.querySelectorAll('${S} .tool-body .sw input'); [0,1,2].forEach(function (i) { s[i].click(); }); return true; })()`);
  t.check('fillers: re-enabled -> primary enabled', !(await t.ev(`document.getElementById('primary').disabled`)));
  await t.ev(`(Array.prototype.filter.call(document.querySelectorAll('${S} .fillers-habits .chip'), function (b) { return b.textContent === 'ya'; })[0].click(), true)`);
  t.check('fillers: habit chip toggles + persists', JSON.stringify(await t.ev(`AC.tools.runtime('fillers').ctx.state.habits`)) === '["ya"]' && /^10 kata aktif$/.test(await t.text(`${S} .tool-body .sec .aside`)));
  t.check('fillers: no horizontal overflow (settings)', await t.ev(`document.querySelector('${S} .tool-body').scrollWidth <= document.querySelector('${S} .tool-body').clientWidth + 1`));
  await t.shot('build_fillers');
  const scrollMain = (to) => t.ev(`(function(){ var e = document.querySelector('${S} .tool-body'); while (e && e !== document.body) { if (e.scrollHeight > e.clientHeight + 4) e.scrollTop = ${to}; e = e.parentElement; } return true; })()`);
  await scrollMain(99999); await t.wait(80);
  await t.shot('build_fillers_bottom');
  await scrollMain(0);

  // ---- analyze -> Tinjau
  await t.click('#primary');
  await t.waitFor(`location.hash === '#tool/fillers/review'`, 90000, 'review pane');
  await t.wait(200);
  const job = await t.ev(`(function(){ var j = AC.tools.runtime('fillers').task; return j && j.job ? j.job.params : null; })()`);
  t.check('fillers: analyze params sent', job && job.always === true && job.anu === true && job.acoustic === true && job.threshold === 0.65 && JSON.stringify(job.habits) === '["ya"]' && job.with_silence === false && job.scope && job.scope.kind === 'all', JSON.stringify(job));
  const info = await t.ev(`(function(){ var l = AC.tools.runtime('fillers').review, it = l.items(), k = -1;
    for (var i = 0; i < it.length; i++) if (it[i].label === 'eee' && it[i].t0 > 38 && it[i].t0 < 39) k = i;
    return { n: it.length, k: k, eee: k >= 0 ? it[k] : null, habits: it.filter(function (x) { return x.kind === 'habit'; }).length, on: l.count() }; })()`);
  t.check('fillers: review lists the true "eee" at 0:38 (checked, Pendengar + Akustik, mepet)', info.eee && info.eee.on && info.eee.tight && info.eee.srcs.indexOf('listener') >= 0 && info.eee.srcs.indexOf('acoustic') >= 0, JSON.stringify(info.eee));
  t.check('fillers: habit "ya" rows present', info.habits >= 1, String(info.habits));
  await t.ev(`(AC.tools.runtime('fillers').review.setActive(${info.k}, true), true)`);
  await t.wait(350);
  t.check('fillers: row tags (source +1, Mepet, confidence)',
    (await t.text(`${S} .rv.is-active .fillers-src`)) === 'Pendengar +1' && /Mepet/.test(await t.text(`${S} .rv.is-active .fillers-tight`)) && (await t.text(`${S} .rv.is-active .conf`)) === '85%');
  t.check('fillers: context with <mark>eee</mark>', /daftar dulu/.test(await t.text(`${S} .rv.is-active .rv-ctx`)) && (await t.text(`${S} .rv.is-active .rv-ctx mark`)) === 'eee');
  const seeks = await t.calls('bac_seek');
  t.check('fillers: click-to-seek half a second before the cut', seeks.length && Math.abs(seeks[seeks.length - 1][0] - (info.eee.t0 - 0.5)) < 1e-6, JSON.stringify(seeks.slice(-1)));
  t.check('fillers: title + dock label', /filler dibuang/.test(await t.text(`${S} .rv-title`)) && new RegExp('^Hapus ' + info.on + ' filler').test(await t.text('#primary')), await t.text('#primary'));
  t.check('fillers: "Mepet kata" filter chip', await t.ev(`Array.prototype.some.call(document.querySelectorAll('${S} .rv-tools .chip'), function (b) { return /Mepet kata/.test(b.textContent); })`));
  t.check('fillers: no horizontal overflow (review rows)', await t.ev(`Array.prototype.every.call(document.querySelectorAll('${S} .rv'), function (r) { return r.scrollWidth <= r.clientWidth + 1; })`));
  await t.shot('build_fillers_review');

  // markers: own host function, then the fallback when the host file is not loaded
  await t.host('bac_fillers_markers', 'function (list, tag, seqId) { __acStub.fm = {n: list.length, tag: tag, seq: seqId, first: list[0]}; return JSON.stringify({ok: true, n: list.length, cleared: 0}); }');
  await t.ev(`(Array.prototype.filter.call(document.querySelectorAll('${S} .rv-bulk .linkbtn'), function (b) { return /marker/.test(b.textContent); })[0].click(), true)`);
  await t.waitFor('window.__acStub.fm', 3000, 'bac_fillers_markers');
  const fm = await t.ev('__acStub.fm');
  t.check('fillers: "Kirim ke marker" -> bac_fillers_markers (checked rows, tag, colour)', fm.n === info.on && fm.tag === '[Klipora-FILLER]' && typeof fm.first.color === 'number' && /Filler: /.test(fm.first.name), JSON.stringify(fm));
  // markers go to the reviewed sequence (its id from the review), not whatever is active (live engine writes doc.seq)
  if (live) t.check('fillers: markers target the reviewed sequence id', fm.seq === 'seq-2m', JSON.stringify(fm.seq));
  await t.ev('(delete __acStub.host.bac_fillers_markers, true)');
  const before = (await t.calls('bac_addMarkers')).length;
  await t.ev(`(Array.prototype.filter.call(document.querySelectorAll('${S} .rv-bulk .linkbtn'), function (b) { return /marker/.test(b.textContent); })[0].click(), true)`);
  await t.waitFor(`__acStub.calls.filter(function (c) { return c.fn === 'bac_addMarkers'; }).length > ${before}`, 3000, 'marker fallback');
  t.check('fillers: marker fallback through AC.apply.markers', (await t.calls('bac_clearMarkersByTag')).some((a) => a[0] === '[Klipora-FILLER]'));

  // toggle one checked row off -> label follows
  const offIdx = await t.ev(`(function(){ var it = AC.tools.runtime('fillers').review.items(); for (var i = 0; i < it.length; i++) if (it[i].on && i !== ${info.k}) return i; return -1; })()`);
  if (offIdx >= 0) {
    await t.ev(`(AC.tools.runtime('fillers').review.toggle(${offIdx}, false), true)`);
    t.check('fillers: unchecking a row updates the dock', new RegExp('^Hapus ' + (info.on - 1) + ' filler').test(await t.text('#primary')), await t.text('#primary'));
    await t.wait(400);   // the list's onChange is debounced (120 ms)
    const early = await t.ev(`(function(){ var d = AC.sys.readJSON(AC.tools.runtime('fillers').reviewOpts.file); return d.items.filter(function (x) { return x.touched && !x.on; }).length; })()`);
    t.check('fillers: a toggle is saved at once (carry-over after Batal + Deteksi lagi)', early >= 1, String(early));
  }

  // ---- apply -> clone + extract on the stub -> result card
  await t.click('#primary');
  await t.waitFor(`location.hash === '#tool/fillers/result'`, 60000, 'result pane');
  const rem = await t.calls('bac_removeRanges');
  const ranges = rem.length ? rem.reduce((a, c) => a.concat(c[1] || []), []) : [];
  t.check('fillers: apply cut the "eee" range on a clone', (await t.calls('bac_cloneSeq')).length >= 1 && ranges.some((r) => Math.abs(r[0] - info.eee.t0) < 0.02 && Math.abs(r[1] - info.eee.t1) < 0.02), JSON.stringify(ranges.slice(0, 4)));
  const saved = await t.ev(`(function(){ var d = AC.sys.readJSON(AC.tools.runtime('fillers').reviewOpts.file); return d.items.filter(function (x) { return x.touched; }).length; })()`);
  t.check('fillers: edited review written back (touched rows)', offIdx < 0 || saved >= 1, String(saved));
  if (live) t.check('fillers: apply reads the reviewed sequence by id (not the active one)', (await t.calls('bac_seqInfo')).some((a) => a[0] === 'lite' && a[1] === 'seq-2m'));
  t.check('fillers: result card "Sequence baru siap" + Buka yang asli', /Sequence baru siap/.test(await t.text(`${S} [data-pane="result"]`)) && /Buka yang asli/.test(await t.text(`${S} [data-pane="result"]`)));
  await t.shot('build_fillers_result');

  // ---- back on the settings page: counts belong to the analysed sequence (hidden on the clone, shown on the original)
  await t.go('tool/fillers');
  const onClone = await t.ev(`(AC.seq.peek() || {}).id !== 'seq-2m'`);
  if (onClone) t.check('fillers: habit counts hidden on another sequence (the clone)', (await t.count(`${S} .fillers-habits .chip .n`)) === 0);
  await t.setSeq(seq2());
  await t.waitFor(`AC.seq.peek() && AC.seq.peek().id === 'seq-2m'`, 5000, 'original sequence active again');
  await t.wait(150);
  t.check('fillers: habit chips show counts from the last run', (await t.count(`${S} .fillers-habits .chip .n`)) === 19 && /Angka/.test(await t.text(`${S} .fillers-counts`)) &&
    /^ya\s+\d+$/.test(await t.ev(`Array.prototype.filter.call(document.querySelectorAll('${S} .fillers-habits .chip'), function (b) { return b.textContent.trim().split(' ')[0] === 'ya'; })[0].textContent.trim()`)));

  // ---- English pass (live language switch re-renders the idle page): main pane labels + no Indonesian leftovers
  await t.go('tool/fillers');
  await t.ev("AC.i18n.set('en')");
  await t.waitFor(`AC.i18n.lang() === 'en' && document.querySelector('${S} .tool-hero')`, 5000, 'English fillers page');
  await t.wait(150);
  t.check('fillers (en): title, tab, CTA', (await t.text('#crumbTitle')) === 'Remove Fillers' && /^Detect fillers/.test(await t.text('#primary')) &&
    (await t.ev(`AC.tools.runtime('fillers').ctx.def.tab`)) === 'Fillers', (await t.text('#crumbTitle')) + ' | ' + (await t.text('#primary')));
  t.check('fillers (en): sections + preset labels', /Words to find/.test(await t.text(`${S} .tool-body`)) && /Balanced/.test(await t.text(`${S} .tool-body .seg`)), await t.text(`${S} .tool-body .seg`));
  const leftEn = await t.idLeftovers('#app');
  t.check('fillers (en): main pane has no Indonesian leftovers', leftEn.length === 0, JSON.stringify(leftEn));
  await t.ev("AC.i18n.set('id')");
  await t.waitFor(`AC.i18n.lang() === 'id' && /Deteksi filler/.test(document.getElementById('primary').textContent)`, 5000, 'back to Indonesian');

  // ---- nothing found -> a result card, not an empty Tinjau ("Tidak ada yang cocok dengan saringan ini")
  if (!live) {
    await t.ev(`(__acStub.engineMock.fillers = function (job, emit, done) { var fs = require('fs'), path = require('path'); var rp = job.workdir + '\\\\fillers_review.json';
      fs.mkdirSync(path.dirname(rp), {recursive: true}); fs.writeFileSync(rp, JSON.stringify({v: 1, tool: 'fillers', timebase: 'sequence', duration: ${D2}, fps: 120, items: []}));
      emit({ev: 'result', data: {review: rp, summary: {n: 0, on: 0, sec_on: 0}, counts: {}, habit_counts: {}, silence: null}}); done(0); }, true)`);
    await t.go('tool/fillers');
    await t.click('#primary');
    await t.waitFor(`location.hash === '#tool/fillers/result'`, 30000, 'empty result');
    t.check('fillers: nothing found -> "Tidak ada filler ditemukan" card', /Tidak ada filler ditemukan/.test(await t.text(`${S} [data-pane="result"]`)) && /Atur ulang/.test(await t.text('#primary')));
  }
}
