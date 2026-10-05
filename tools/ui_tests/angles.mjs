// angles.mjs: Angle Otomatis in headless Chrome with the fake CEP stub and (by default) the REAL engine.
//   node tools/ui_test.mjs tools/ui_tests/angles.mjs [--engine live] [--width 280]
// Flow 1 (cut15 fixture, 15 clips): main pane -> analyze -> Tinjau (lane, frame preview, angle edit with the 3 key and a
// row button, one shot unchecked) -> engine apply -> host chain (bac_cloneSeq, bac_angles_apply, bac_openSequence) ->
// result card. Flow 2 (raw49, 1 clip): the "cuma 1 clip" notice turns on sentence splits -> bac_angles_split gets the
// split times. In the page the host bac_angles_* functions are stubbed; hostSim() below runs the REAL
// panel/host/39_angles.jsx in a Node vm against a mocked Premiere DOM (razor + Motion) first.
import fs from 'fs';
import path from 'path';
import vm from 'vm';

export default async function (t) {
  hostSim(t);
  const live = t.engine === 'live';
  const narrow = t.width < 340;
  await t.ev(`(localStorage.removeItem('ac.tool.angles'), true)`);
  await t.host('bac_angles_split', 'function (id, times, track) { return JSON.stringify({ ok: true, done: times.length, ms: 2, clips: times.length + 1 }); }');
  await t.host('bac_angles_apply', 'function (id, track, items) { return JSON.stringify({ ok: true, set: items.length, keyed: 0, nomotion: 0, items: items.length, matched: items.length, bad: 0, ms: 4 }); }');
  if (!live) {
    // mock engine: minimal analyze/apply/frame so the page flow runs without python
    await t.ev(`(__acStub.engineMock.angles = function (job, emit, done) {
      if (job.action === 'analyze') {
        var items = [], A = ['wide', 'medium', 'close'], t0 = 0;
        for (var i = 0; i < 6; i++) { var a = A[i % 3]; var o = {}; A.forEach(function (k, j) { o[k] = { scale: [100, 118, 140][j], pos: [0.5, 0.5], view: [0.5 - 50 / [100, 118, 140][j], 0.5 - 50 / [100, 118, 140][j], 100 / [100, 118, 140][j], 100 / [100, 118, 140][j]], fits: true }; });
          items.push({ id: 's' + i, t0: t0, t1: t0 + 4, kind: 'shot', on: true, label: a, angle: a, scale: o[a].scale, pos: o[a].pos, opts: o, anchor: { x: 0.5, y: 0.5, src: 'center' }, ctx: 'kata ' + i, why: 'cut', cut_in: false, cut_out: false, media: null, sm: 1 }); t0 += 4; }
        var p = job.workdir + '\\\\angles_review.json';
        require('fs').writeFileSync(p, JSON.stringify({ v: 1, tool: 'angles', timebase: 'sequence', duration: 24, seq: { id: job.seq.id, name: job.seq.name }, track: 0, size: [2292, 960], angles: { wide: { scale: 100 }, medium: { scale: 118 }, close: { scale: 140 } }, items: items }));
        emit({ ev: 'result', data: { review: p, summary: '6 shot' } }); done(0);
      } else if (job.action === 'apply') {
        var d = JSON.parse(require('fs').readFileSync(job.review, 'utf8'));
        emit({ ev: 'result', data: { plan: { kind: 'keyframes', mode: 'static', tool: 'angles', track: 0, seq: d.seq, splits: [], items: d.items.filter(function (x) { return x.on; }).map(function (x) { return { id: x.id, t0: x.t0, t1: x.t1, angle: x.angle, scale: x.opts[x.angle].scale, pos: x.opts[x.angle].pos }; }) }, summary: '6 shot', by_angle: {} } }); done(0);
      } else { emit({ ev: 'error', code: 'NO_MEDIA', msg: 'mock' }); done(1); }
      return function () {};
    }, true)`);
  }

  /* ---------------- main pane */
  await t.setSeq('cut15');
  await t.go('tool/angles');
  await t.waitFor(`document.querySelector('[data-screen="tool-angles"] .angles-demo')`, 5000, 'angles main pane');
  await t.wait(300);
  t.check('angles: main pane renders presets, crop diagram, focus control',
    (await t.count('[data-screen="tool-angles"] .angles-demo-box')) === 3 && (await t.count('[data-screen="tool-angles"] .angles-anchor button')) === 4);
  t.check('angles: dock primary "Buat angle"', (await t.text('#primary .lbl')) === 'Buat angle');
  t.check('angles: no 1-clip notice on a 15-clip sequence', (await t.count('[data-screen="tool-angles"] .angles-notice .alert')) === 0);
  t.check('angles: sentence-split slider hidden while the switch is off',
    await t.ev(`(function(){var s=[].slice.call(document.querySelectorAll('[data-screen="tool-angles"] .field')).filter(function(f){return /Panjang shot/.test(f.textContent);})[0]; return !!s && s.hidden;})()`));
  const overflow = await t.ev('document.documentElement.scrollWidth - document.documentElement.clientWidth');
  t.check(`angles: no horizontal overflow at ${t.width} px`, overflow <= 0, String(overflow));
  // chips refuse to go below 2 angles
  await t.click('[data-screen="tool-angles"] .chips-wrap .chip:nth-child(3)');
  await t.click('[data-screen="tool-angles"] .chips-wrap .chip:nth-child(2)');
  t.check('angles: at least 2 angles stay selected', (await t.ev(`AC.tools.runtime('angles').ctx.state.use.length`)) === 2);
  await t.click('[data-screen="tool-angles"] .chips-wrap .chip:nth-child(3)');
  t.check('angles: 3 angles again', (await t.ev(`AC.tools.runtime('angles').ctx.state.use.length`)) === 3);
  await t.shot(narrow ? 'build_angles_280' : 'build_angles');
  await t.ev(`(document.querySelector('[data-screen="tool-angles"] details.sec-d').open = true, document.querySelector('[data-screen="tool-angles"] .angles-anchor').scrollIntoView(), true)`);
  await t.wait(150);
  if (!narrow) await t.shot('build_angles_settings');
  await t.ev(`(document.querySelector('[data-screen="tool-angles"] details.sec-d').open = false, true)`);

  /* ---------------- analyze -> Tinjau */
  await t.click('#primary');
  await t.waitFor(`location.hash === '#tool/angles/review'`, 90000, 'angles review');
  await t.wait(400);
  const nRows = await t.ev(`AC.tools.runtime('angles').review.items().length`);
  t.check('angles: review lists one shot per clip', live ? nRows === 15 : nRows === 6, String(nRows));
  t.check('angles: title counts shots per angle', /shot diatur/.test(await t.text('[data-screen="tool-angles"] .rv-title')));
  t.check('angles: angle lane + legend replace the cut ribbon', (await t.count('[data-screen="tool-angles"] canvas.angles-lane')) === 1 &&
    await t.ev(`getComputedStyle(document.querySelector('[data-screen="tool-angles"] .rv-head > canvas.ribbon')).display === 'none'`));
  const noRepeat = await t.ev(`(function(){var it=AC.tools.runtime('angles').review.items(); for (var i=1;i<it.length;i++) if (it[i].angle===it[i-1].angle) return false; return true;})()`);
  t.check('angles: no angle twice in a row', noRepeat);
  if (live) {
    await t.waitFor(`(function(){var i=document.querySelector('.angles-pv-img'); return i && i.complete && i.naturalWidth > 0;})()`, 30000, 'preview frame');
    t.check('angles: preview frame loaded through the worker (frame action)', true);
    t.check('angles: crop boxes drawn for 3 angles', (await t.count('.angles-pv-box')) === 3 && (await t.count('.angles-pv-box.is-on')) === 1);
    // Real hit test (t.click dispatches on the element and cannot see stacking): the label of every box must hit
    // that box, or a later (smaller) one on top of it. Live bug: the active full-frame Lebar box was raised with
    // z-index and swallowed every click on Sedang/Dekat.
    const hits = await t.ev(`(function(){document.querySelector('.angles-pv-frame').scrollIntoView({block:'nearest'});
      var bs=[].slice.call(document.querySelectorAll('.angles-pv-box'));return bs.map(function(b,k){
      var r=b.querySelector('i').getBoundingClientRect(),x=r.left+r.width/2,y=r.top+r.height/2,want=b;
      bs.forEach(function(o,j){var q=o.getBoundingClientRect();if(j>k&&x>=q.left&&x<q.right&&y>=q.top&&y<q.bottom)want=o;});
      var h=document.elementFromPoint(x,y);return (h&&h.closest('[data-a]'))===want;});})()`);
    t.check('angles: every crop box is clickable while Lebar is active', hits.length === 3 && hits.every(Boolean), JSON.stringify(hits));
  }
  // pick an angle for row 2 with the "3" key, and for row 3 with a crop box click
  await t.click('[data-screen="tool-angles"] .rv[data-i="1"] .rv-main');
  await t.wait(150);
  const before = await t.ev(`AC.tools.runtime('angles').review.items()[1].angle`);
  await t.key('3', {}, '[data-screen="tool-angles"] .rv-scroll');
  await t.wait(150);
  const after = await t.ev(`AC.tools.runtime('angles').review.items()[1]`);
  t.check('angles: key 3 sets Dekat on the active shot', after.angle === 'close' && after.picked === true && after.scale === after.opts.close.scale, before + ' -> ' + after.angle);
  t.check('angles: preview follows the active shot', (await t.ev(`document.querySelector('.angles-pv-box.is-on').getAttribute('data-a')`)) === 'close');
  await t.click('[data-screen="tool-angles"] .rv[data-i="2"] .rv-main');
  await t.wait(150);
  const want2 = await t.ev(`(function(){var it=AC.tools.runtime('angles').review.items()[2]; return it.angle === 'wide' ? 'medium' : 'wide';})()`);
  await t.click(`.angles-pv-box[data-a="${want2}"]`);
  await t.wait(100);
  t.check('angles: clicking a crop box picks that angle', (await t.ev(`AC.tools.runtime('angles').review.items()[2].angle`)) === want2);
  t.check('angles: row actions are angle buttons (no "Buang bagian ini")',
    await t.ev(`(function(){var r=document.querySelector('[data-screen="tool-angles"] .rv.is-active .rv-actions'); var tog=r.querySelector('[data-act="toggle"]'); return r.querySelectorAll('[data-act^="x"]').length === 3 && getComputedStyle(tog).display === 'none';})()`));
  // uncheck shot 4
  await t.click('[data-screen="tool-angles"] .rv[data-i="3"] .ck');
  await t.wait(150);
  const nOn = await t.ev(`AC.tools.runtime('angles').review.count()`);
  t.check('angles: dock label follows the checked shots', (await t.text('#primary .lbl')) === 'Terapkan ' + nOn + ' angle', await t.text('#primary .lbl'));
  await t.shot(narrow ? 'build_angles_review_280' : 'build_angles_review');

  /* ---------------- apply -> host chain -> result */
  await t.click('#primary');
  await t.waitFor(`location.hash === '#tool/angles/result'`, 60000, 'angles result');
  await t.wait(300);
  const clones = await t.calls('bac_cloneSeq');
  const applies = await t.calls('bac_angles_apply');
  const sent = applies.reduce((a, c) => a.concat(c[2]), []);
  t.check('angles: clone made from the analysed sequence', clones.length === 1 && clones[0][1] === 'seq-cut15' && / \(Klipora\)$/.test(clones[0][0]), JSON.stringify(clones));
  t.check('angles: static Motion sent for every checked shot', sent.length === nOn && applies.every((c) => c[0] === 'made-0' && c[1] === 0), sent.length + ' / ' + nOn);
  const s1 = sent.find((x) => x.t0 === after.t0);
  t.check('angles: edited angle reaches the host', s1 && s1.angle === 'close' && s1.scale === after.opts.close.scale && s1.pos.length === 2);
  t.check('angles: no razor without sentence splits', (await t.calls('bac_angles_split')).length === 0);
  t.check('angles: result sequence opened', JSON.stringify((await t.calls('bac_openSequence')).slice(-1)) === JSON.stringify([['made-0']]));
  t.check('angles: result card', (await t.text('[data-screen="tool-angles"] [data-pane="result"] h2')) === 'Sequence baru siap' &&
    (await t.count('[data-screen="tool-angles"] [data-pane="result"] canvas.angles-lane')) === 1);
  t.check('angles: edited review written back (picked + touched)', await t.ev(`(function(){var f=AC.tools.runtime('angles').reviewOpts.file; var d=AC.sys.readJSON(f); return d.items[1].picked === true && d.items[1].angle === 'close' && d.items[3].on === false;})()`));
  t.check('angles: Riwayat has the host task', await t.ev(`AC.history.list().some(function(h){return h.tool === 'angles' && h.action === 'host' && h.status === 'ok' && /shot/.test(h.sub || '');})`));
  await t.shot(narrow ? 'build_angles_result_280' : 'build_angles_result');

  /* ---------------- English UI: main pane + review (fresh analysis, the job runs in English too) */
  await t.ev(`(AC.i18n.set('en'), true)`);
  await t.go('tool/angles');
  await t.waitFor(`document.querySelector('[data-screen="tool-angles"] [data-pane="main"] .angles-demo')`, 5000, 'angles main pane (en)');
  await t.wait(400);
  let left = await t.idLeftovers('#app');
  t.check('angles en: main pane has no Indonesian left', left.length === 0, left.join(' | '));
  t.check('angles en: exact English labels (primary, sections, crop chips)', (await t.text('#primary .lbl')) === 'Create angles' &&
    /When to switch angles/.test(await t.text('[data-screen="tool-angles"] [data-pane="main"]')) &&
    /^Wide 100%/.test(await t.text('[data-screen="tool-angles"] .chips-wrap .chip')), await t.text('#primary .lbl'));
  await t.click('#primary');
  await t.waitFor(`location.hash === '#tool/angles/review' && document.querySelectorAll('[data-screen="tool-angles"] .rv').length > 0`, 90000, 'angles review (en)');
  await t.wait(400);
  left = await t.idLeftovers('#app');
  t.check('angles en: review pane has no Indonesian left', left.length === 0, left.join(' | '));
  const nOnEn = await t.ev(`AC.tools.runtime('angles').review.count()`);
  t.check('angles en: review title + primary in English', /shots? set/.test(await t.text('[data-screen="tool-angles"] .rv-title')) &&
    (await t.text('#primary .lbl')) === (nOnEn === 1 ? 'Apply 1 angle' : `Apply ${nOnEn} angles`), await t.text('#primary .lbl'));
  await t.shot(narrow ? 'build_angles_review_en_280' : 'build_angles_review_en');
  await t.ev(`(AC.i18n.set('id'), true)`);

  /* ---------------- 1 clip: notice -> sentence splits -> razor times */
  if (!live) return;
  await t.go('tool/angles');
  await t.setSeq('raw49');
  await t.wait(600);
  t.check('angles: 1-clip notice shown', /cuma 1 clip/.test(await t.text('[data-screen="tool-angles"] .angles-notice') || ''));
  await t.click('[data-screen="tool-angles"] .angles-notice .btn');
  await t.wait(100);
  t.check('angles: notice button turns on sentence splits', await t.ev(`AC.tools.runtime('angles').ctx.state.split === true`) && (await t.count('[data-screen="tool-angles"] .angles-notice .alert')) === 0);
  await t.click('#primary');
  await t.waitFor(`location.hash === '#tool/angles/review'`, 90000, 'angles review (split)');
  await t.wait(300);
  const nSplit = await t.ev(`AC.tools.runtime('angles').review.items().filter(function(it){return it.why === 'sentence';}).length`);
  t.check('angles: sentence splits in the review', nSplit >= 3, String(nSplit));
  await t.click('#primary');
  await t.waitFor(`location.hash === '#tool/angles/result'`, 60000, 'angles result (split)');
  const splits = (await t.calls('bac_angles_split')).reduce((a, c) => a.concat(c[1]), []);
  t.check('angles: razor times sent to the clone, sorted, inside the sequence',
    splits.length === nSplit && splits.every((x, i) => x > 0 && x < 48.93 && (!i || x > splits[i - 1])), JSON.stringify(splits));
  t.check('angles: result shows the new cuts', /Potongan baru/.test(await t.text('[data-screen="tool-angles"] [data-pane="result"] .sum-strip') || ''));
  await t.go('tool/angles');
  await t.ev(`AC.tools.runtime('angles').ctx.state.split = false, AC.tools.runtime('angles').ctx.save(), true`);
}

// Offline check of panel/host/39_angles.jsx (ES3): the real file runs in a Node vm against a small mock of the
// Premiere DOM + QE razor (clips split on every unlocked track). The live behaviour is for the Premiere verifier.
function hostSim(t) {
  const H = path.join(t.root, 'panel', 'host') + path.sep;
  const TPS = 254016000000, FPS = 120, TPF = TPS / FPS;
  const check = (name, ok, d = '') => t.check(name, ok, d);
  function prop(v) { return { v, tv: false, keys: 0, isTimeVarying() { return this.tv; }, setValue(x) { this.v = Array.isArray(x) ? x.slice() : x; this.sets = (this.sets || 0) + 1; }, getValue() { return this.v; } }; }
  function clip(a, b, keyed) {
    const pos = prop([0.5, 0.5]), sc = prop(100);
    if (keyed) sc.tv = true;
    const motion = { matchName: 'AE.ADBE Motion', properties: [pos, sc] };
    return { start: { seconds: a }, end: { seconds: b }, motion, components: { numItems: 2, 0: { matchName: 'AE.ADBE Opacity' }, 1: motion } };
  }
  function coll(arr) { const o = { numItems: arr.length }; arr.forEach((x, i) => { o[i] = x; }); return o; }
  function track(clips, locked) {
    const t = { list: clips, locked: !!locked, targeted: false, sync: false,
      isTargeted() { return this.targeted; }, setTargeted(v) { this.targeted = v; this.tset = (this.tset || 0) + 1; }, isLocked() { return this.locked; } };
    Object.defineProperty(t, 'clips', { get() { return coll(t.list); } });
    return t;
  }
  function tracks(arr) { const o = coll(arr); o.numTracks = arr.length; return o; }

  const KEPT = [[0, 5], [5, 7.5], [7.5, 12], [12, 20]];
  const v1 = track(KEPT.map(([a, b], i) => clip(a, b, i === 2)));
  const v2 = track([clip(1, 3)]);
  const a1 = track(KEPT.map(([a, b]) => clip(a, b)), true);
  let cti = '1200000000';
  const seq = { sequenceID: 'clone-1', name: 'X (AutoCut)', timebase: String(TPF), end: String(20 * TPS),
    videoTracks: tracks([v1, v2]), audioTracks: tracks([a1]),
    getSettings: () => ({ videoDisplayFormat: 998 }), getInPoint: () => '-400000', getOutPoint: () => '-400000',
    setInPoint() { throw new Error('in point touched'); }, setOutPoint() { throw new Error('out point touched'); },
    getPlayerPosition: () => ({ ticks: cti, seconds: Number(cti) / TPS }), setPlayerPosition(t) { cti = String(t); } };
  const allTracks = [v1, v2, a1];
  const razored = [];
  const qeSeq = {
    razor(tc) {
      const m = /^(\d\d):(\d\d):(\d\d):(\d\d\d)$/.exec(tc); if (!m) throw new Error('bad tc ' + tc);
      const t = (+m[1] * 3600 + +m[2] * 60 + +m[3]) + +m[4] / FPS;
      razored.push(t);
      allTracks.forEach((tr) => {
        if (tr.locked) return;                      // razor skips locked tracks (we unlock first)
        const i = tr.list.findIndex((c) => c.start.seconds < t - 1e-9 && t < c.end.seconds - 1e-9);
        if (i < 0) return;
        const c = tr.list[i], n = clip(t, c.end.seconds, c.motion.properties[1].tv);
        c.end = { seconds: t };
        tr.list.splice(i + 1, 0, n);
      });
    },
    getVideoTrackAt(i) { return qeTrack([v1, v2][i]); }, getAudioTrackAt() { return qeTrack(a1); }
  };
  function qeTrack(tr) { return { isLocked: () => tr.locked, setLock(v) { tr.locked = v; }, isSyncLocked: () => tr.sync, setSyncLock(v) { tr.sync = v; } }; }
  const ctx = {
    app: { enableQE() {}, project: { sequences: { numSequences: 1, 0: seq }, activeSequence: seq, openSequence(id) { this.opened = id; } } },
    qe: { project: { getActiveSequence: () => qeSeq } }, $: { global: {} }, File: function () {}, Time: function () {}, ProjectItemType: {}
  };
  vm.createContext(ctx);
  for (const f of ['00_util.jsx', '10_timeline.jsx', '39_angles.jsx']) vm.runInContext(fs.readFileSync(H + f, 'utf8'), ctx, { filename: f });
  // evalScript arguments are literals parsed by ExtendScript itself: build them inside the context
  const L = (x) => vm.runInContext('(' + JSON.stringify(x) + ')', ctx);

  // 1) split at 2.5 (inside clip 0), 7.5 (existing edit: no-op), 16 (inside clip 3), 25 (outside)
  let r = JSON.parse(ctx.bac_angles_split('clone-1', L([2.5, 7.5, 16, 25]), 0));
  check('host sim: split: ok + 3 razors issued (outside skipped)', r.ok && r.done === 3 && razored.length === 3, JSON.stringify(r));
  check('host sim: split: V1 now 6 clips', v1.list.length === 6, v1.list.map((c) => [c.start.seconds, c.end.seconds]).join(' '));
  check('host sim: split: locked A1 restored to locked, untargeted tracks restored', a1.locked === true && v2.targeted === false && v1.targeted === false);
  check('host sim: split: locked A1 was razored too (unlocked during the call)', a1.list.length === 6, String(a1.list.length));
  check('host sim: split: playhead restored', cti === '1200000000');

  // 2) apply: items over the 6 pieces; piece [7.5,12] is keyed (skipped)
  const items = [
    { id: 's0', t0: 0, t1: 2.5, scale: 100, pos: [0.5, 0.5] }, { id: 's1', t0: 2.5, t1: 5, scale: 118, pos: [0.41, 0.59] },
    { id: 's2', t0: 5, t1: 7.5, scale: 140, pos: [0.3, 0.7] }, { id: 's3', t0: 7.5, t1: 12, scale: 118, pos: [0.5, 0.5] },
    { id: 's4', t0: 12, t1: 16, scale: 100, pos: [0.5, 0.5] }, { id: 's5', t0: 16, t1: 20, scale: 140, pos: [0.6, 0.4] }];
  r = JSON.parse(ctx.bac_angles_apply('clone-1', 0, L(items.slice(0, 3))));
  let r2 = JSON.parse(ctx.bac_angles_apply('clone-1', 0, L(items.slice(3))));
  check('host sim: apply: chunk 1 sets 3 clips', r.set === 3 && r.matched === 3 && r.keyed === 0 && r.bad === 0, JSON.stringify(r));
  check('host sim: apply: chunk 2 skips the keyed clip', r2.set === 2 && r2.keyed === 1 && r2.matched === 3, JSON.stringify(r2));
  const vals = v1.list.map((c) => [c.motion.properties[1].v, c.motion.properties[0].v.join(',')]);
  check('host sim: apply: values per clip', JSON.stringify(vals) === JSON.stringify([[100, '0.5,0.5'], [118, '0.41,0.59'], [140, '0.3,0.7'], [100, '0.5,0.5'], [100, '0.5,0.5'], [140, '0.6,0.4']]), JSON.stringify(vals));
  check('host sim: apply: V2 overlay untouched', v2.list.every((c) => c.motion.properties[1].v === 100 && !c.motion.properties[1].sets));
  // 3) read back
  const rd = JSON.parse(ctx.bac_angles_read('clone-1', 0));
  check('host sim: read: 6 clips with scale/pos/keyed', rd.clips.length === 6 && rd.clips[3].keyed === true && rd.clips[5].scale === 140, JSON.stringify(rd.clips[3]));
  // 4) errors
  check('host sim: errors: unknown sequence', ctx.bac_angles_apply('nope', 0, L(items)).indexOf('ERR:') === 0);
  check('host sim: errors: bad track', ctx.bac_angles_apply('clone-1', 5, L(items)).indexOf('ERR:Track V6') === 0);
  // 5) locked camera track: unlocked during apply, locked again after
  v1.locked = true; v1.list[0].motion.properties[1].v = 1;
  r = JSON.parse(ctx.bac_angles_apply('clone-1', 0, L(items.slice(0, 1))));
  check('host sim: apply: locked V1 handled and re-locked', r.set === 1 && v1.locked === true && v1.list[0].motion.properties[1].v === 100, JSON.stringify(r));
}
