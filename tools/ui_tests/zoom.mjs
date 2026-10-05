// zoom.mjs: Auto Zoom in headless Chrome with the fake CEP stub and (by default) the REAL engine.
//   node tools/ui_test.mjs tools/ui_tests/zoom.mjs [--engine live|mock] [--width 280]
// 1. hostSim(): runs the REAL panel/host/37_zoom.jsx (+ 00_util/10_timeline) in a Node vm against a mocked Premiere DOM
//    (Motion Scale/Position with keys): clone naming, keys + values, stray key removal, user keys skipped, reset of
//    earlier AutoCut keys, letterboxed clip, scan/values read-back.
// 2. Page flow (raw49): main pane (modes show/hide their settings) -> analyze (real engine: cached activity, thumbnails)
//    -> Tinjau (thumbnail rows, big preview follows the active row, one zoom unchecked, video preview render through
//    worker.py) -> engine apply with params.reset from the record -> host chain (bac_zoom_clone, bac_zoom_keys chunks,
//    bac_openSequence) -> result card -> record written -> "Hapus hasil" deletes the clone and its record.
// Screenshots: docs/shots/build_zoom.png (main), build_zoom_review.png, build_zoom_result.png.
import fs from 'fs';
import path from 'path';
import vm from 'vm';

export default async function (t) {
  hostSim(t);
  const live = t.engine === 'live';
  const narrow = t.width < 340;
  const S = '[data-screen="tool-zoom"]';
  await t.ev(`(localStorage.removeItem('ac.tool.zoom'), true)`);
  await t.host('bac_zoom_clone', `function (id) {
    var s = __acStub.seq; var c = JSON.parse(JSON.stringify(s)); c.id = 'seq-zoom-1'; c.name = s.name + ' (Zoom)';
    __acStub.made[c.id] = c;
    return JSON.stringify({ ok: true, id: c.id, name: c.name, origId: id, origName: s.name }); }`);
  await t.host('bac_zoom_keys', `function (id, p, a, b) {
    var plan = JSON.parse(require('fs').readFileSync(p, 'utf8')), keys = 0, base = [], reset = 0;
    for (var i = a; i < b; i++) { var c = plan.clips[i]; keys += c.keys.length; if (c.reset) reset++;
      if (c.keys.length) base.push({ track: c.track, start: c.start, end: c.end, 'in': c['in'], scale: 100, pos: [0.5, 0.5] }); }
    (window.__zoomCalls = window.__zoomCalls || []).push({ id: id, a: a, b: b, n: plan.clips.length, reset: reset, interp: plan.interp });
    return JSON.stringify({ ok: true, done: b - a, keys: keys, reset: reset, skipped: [], base: base, ms: 3 }); }`);
  if (!live) mockEngine(t);

  /* ---------------- main pane */
  await t.setSeq('raw49');
  await t.go('tool/zoom');
  await t.waitFor(`document.querySelector('${S} .opts')`, 5000, 'zoom main pane');
  await t.wait(250);
  t.check('zoom: 3 mode cards, screen mode selected', (await t.count(`${S} .opts .opt`)) === 3 && /Ikuti aksi layar/.test(await t.text(`${S} .opts .opt.is-on`)));
  t.check('zoom: dock primary "Cari momen zoom"', (await t.text('#primary .lbl')) === 'Cari momen zoom');
  await t.waitFor(`/Analisis layar tersimpan/.test((document.querySelector('${S} .src .tags') || {}).textContent || '')`, 5000, 'cache tag')
    .then(() => t.check('zoom: source card says the screen analysis is cached', true), () => t.check('zoom: source card says the screen analysis is cached', false, 'activity cache next to the 49 s media missing?'));
  t.check('zoom: ignore-zone chips (Taskbar + Notifikasi on)', (await t.count(`${S} .chips-wrap .chip[aria-pressed="true"]`)) === 2);
  t.check('zoom: intensity hint shows the range', /1,25x sampai 2,2x/.test(await t.text(`${S} .zoom-help`)));
  const visible = (txt) => t.ev(`(function(){var f=[].slice.call(document.querySelectorAll('${S} .field, ${S} .sec, ${S} label.sw')).filter(function(x){return x.textContent.indexOf(${JSON.stringify(txt)})===0 || (x.querySelector('h2') && x.querySelector('h2').textContent.indexOf(${JSON.stringify(txt)})===0);})[0]; if(!f) return 'missing'; while (f) { if (f.hidden) return false; f = f.parentElement; } return true;})()`);
  t.check('zoom: screen mode shows frequency + zones, hides AI', (await visible('Maks zoom per menit')) === true && (await visible('Zona abaikan')) === true && (await visible('Momen penting dari AI')) === false);
  await t.click(`${S} .opts .opt:nth-child(2) input`);
  await t.wait(80);
  t.check('zoom: speech mode: zones hidden, AI switch shown, talk range', (await visible('Zona abaikan')) === false && (await visible('Momen penting dari AI')) === true && /1,12x sampai 1,3x/.test(await t.text(`${S} .zoom-help`)));
  await t.click(`${S} .opts .opt:nth-child(3) input`);
  await t.wait(80);
  t.check('zoom: rhythm mode: no frequency slider, single zoom level', (await visible('Maks zoom per menit')) === false && /Zoom 1,15x di kalimat/.test(await t.text(`${S} .zoom-help`)));
  await t.click(`${S} .opts .opt:nth-child(1) input`);
  await t.wait(80);
  t.check('zoom: back to screen mode', (await t.ev(`AC.tools.runtime('zoom').ctx.state.mode`)) === 'screen');
  // custom zoom range via the advanced sliders -> "Custom"
  await t.ev(`(document.querySelector('${S} details.sec-d').open = true, true)`);
  await t.ev(`(function(){var r=document.querySelectorAll('${S} details.sec-d input.range')[1]; r.value='2.6'; r.dispatchEvent(new Event('input',{bubbles:true})); return true;})()`);
  t.check('zoom: manual zoom max -> Custom', /Custom/.test(await t.text(`${S} details.sec-d .aside`)) && (await t.ev(`AC.tools.runtime('zoom').ctx.state.custom.zmax`)) === 2.6);
  await t.click(`${S} .seg[aria-label="Kekuatan zoom"] [data-v="sedang"]`);
  t.check('zoom: preset clears Custom', (await t.ev(`AC.tools.runtime('zoom').ctx.state.custom`)) === null);
  await t.ev(`(document.querySelector('${S} details.sec-d').open = false, true)`);
  const overflow = await t.ev('document.documentElement.scrollWidth - document.documentElement.clientWidth');
  t.check(`zoom: no horizontal overflow at ${t.width} px`, overflow <= 0, String(overflow));
  await t.wait(300);
  await t.shot(narrow ? 'build_zoom_280' : 'build_zoom');

  /* ---------------- analyze -> Tinjau */
  await t.click('#primary');
  await t.waitFor(`location.hash === '#tool/zoom/review'`, 120000, 'zoom review');
  await t.wait(400);
  const rt = `AC.tools.runtime('zoom')`;
  const n = await t.ev(`${rt}.review.items().length`);
  t.check('zoom: review lists the zoom moments', live ? n === 5 : n === 3, String(n));
  t.check('zoom: title "N zoom dipakai"', /zoom dipakai/.test(await t.text(`${S} .rv-title`)));
  t.check('zoom: legend says time zoomed, not "Hemat"', /ter-zoom/.test(await t.text(`${S} .rv-head .legend`)) && !/Hemat/.test(await t.text(`${S} .rv-head .legend`)));
  t.check('zoom: rows carry a thumbnail slot', (await t.count(`${S} .rv .zoom-th`)) >= Math.min(n, 3));
  if (live) {
    await t.waitFor(`(function(){var i=document.querySelector('${S} .rv .zoom-th img'); return i && i.complete && i.naturalWidth > 0;})()`, 15000, 'row thumbnail');
    t.check('zoom: row thumbnail PNG loaded (frame + zoom rectangle)', true);
    await t.waitFor(`(function(){var i=document.querySelector('${S} .zoom-pv-img'); return i && i.complete && i.naturalWidth > 0;})()`, 15000, 'preview image');
    t.check('zoom: big preview shows the first moment', /2026|x/.test(await t.text(`${S} .zoom-pv-info b`)));
  }
  const info0 = await t.text(`${S} .zoom-pv-info`);
  await t.click(`${S} .rv[data-i="1"] .rv-main`);
  await t.wait(250);
  const info1 = await t.text(`${S} .zoom-pv-info`);
  t.check('zoom: preview follows the active row', info1 && info1 !== info0, info0 + ' | ' + info1);
  t.check('zoom: row seek went to Premiere', (await t.calls('bac_seek')).length >= 1);
  t.check('zoom: toggle text is zoom wording', /Lewati zoom ini/.test(await t.text(`${S} .rv.is-active .rv-actions`)));
  await t.click(`${S} .rv[data-i="2"] .ck`);
  await t.wait(150);
  t.check('zoom: one zoom unchecked -> primary counts', (await t.text('#primary .lbl')) === `Terapkan ${n - 1} zoom`, await t.text('#primary .lbl'));
  await t.shot(narrow ? 'build_zoom_review_280' : 'build_zoom_review');

  if (live) {
    // video preview of the active moment (engine worker, NVENC or libx264)
    await t.click(`${S} .zoom-pv-row .btn`);
    await t.waitFor(`(function(){var v=document.querySelector('${S} .zoom-pv-video'); return v && v.readyState >= 1;})() || /gagal|tidak bisa/.test((document.querySelector('${S} .zoom-pv-msg')||{}).textContent||'')`, 90000, 'preview video');
    const vmsg = await t.text(`${S} .zoom-pv-msg`);
    t.check('zoom: preview video rendered and playable', (await t.count(`${S} .zoom-pv-video`)) === 1 && /Video/.test(vmsg), vmsg);
    t.check('zoom: "Buka di pemutar" offered', await t.ev(`!document.querySelector('${S} .zoom-pv-row .btn-ghost').hidden`));
  }

  /* ---------------- Terapkan -> host chain -> result */
  // record of an earlier zoom on this sequence -> apply must send params.reset (plan clips get "reset")
  const recPath = await t.ev(`AC.sys.join(AC.sys.paths.appData, 'zoom_applied.json')`);
  await t.ev(`(AC.sys.writeJSON(${JSON.stringify(recPath)}, { 'seq-raw49': { name: 'x', created: 1, clips: [{ track: 0, start: 0, end: 48.925, 'in': 0, scale: 100, pos: [0.5, 0.5] }] } }), true)`);
  await t.click('#primary');
  await t.waitFor(`location.hash === '#tool/zoom/result'`, 90000, 'zoom result');
  await t.wait(300);
  const calls = await t.ev('window.__zoomCalls || []');
  t.check('zoom: clone requested for the analyzed sequence', JSON.stringify(await t.calls('bac_zoom_clone')) === JSON.stringify([['seq-raw49']]));
  t.check('zoom: keys applied in chunks covering the plan', calls.length >= 1 && calls[0].a === 0 && calls[calls.length - 1].b === calls[0].n && calls[0].interp === 'linear', JSON.stringify(calls));
  t.check('zoom: earlier AutoCut zoom keys are reset first (params.reset -> plan)', calls.reduce((a, c) => a + c.reset, 0) === 1, JSON.stringify(calls));
  t.check('zoom: result sequence opened', (await t.calls('bac_openSequence')).some((a) => a[0] === 'seq-zoom-1'));
  t.check('zoom: result card', (await t.text(`${S} .result-h h2`)) === 'Zoom terpasang' && /Sequence asli tidak diubah/.test(await t.text(`${S} .result-h p`)));
  t.check('zoom: result stats (zoom, clip, keyframe)', (await t.count(`${S} .sum-strip > div`)) === 3);
  const rec = JSON.parse(await t.ev(`require('fs').readFileSync(${JSON.stringify(recPath)}, 'utf8')`));
  t.check('zoom: record of the new zoom result written', rec['seq-zoom-1'] && rec['seq-zoom-1'].clips.length >= 1 && rec['seq-zoom-1'].from === 'seq-raw49', JSON.stringify(rec).slice(0, 200));
  await t.shot(narrow ? 'build_zoom_result_280' : 'build_zoom_result');
  // "Hapus hasil" (two-step) deletes the clone and its record
  const del = `[...document.querySelectorAll('${S} .btn-row button')].find(function (b) { return /Hapus hasil|Yakin/.test(b.textContent); })`;
  await t.ev(`(${del}.click(), true)`); await t.wait(50); await t.ev(`(${del}.click(), true)`); await t.wait(250);
  t.check('zoom: Hapus hasil deletes the clone', (await t.calls('bac_deleteSequence')).some((a) => a[0] === 'seq-zoom-1'));
  const rec2 = JSON.parse(await t.ev(`require('fs').readFileSync(${JSON.stringify(recPath)}, 'utf8')`));
  t.check('zoom: record entry removed with the clone', !rec2['seq-zoom-1']);

  /* ---------------- English UI: main pane + review (fresh analysis, the job runs in English too) */
  await t.ev(`(AC.i18n.set('en'), true)`);
  await t.go('tool/zoom');
  await t.waitFor(`document.querySelector('${S} [data-pane="main"] .opts')`, 5000, 'zoom main pane (en)');
  await t.wait(400);
  let left = await t.idLeftovers('#app');
  t.check('zoom en: main pane has no Indonesian left', left.length === 0, left.join(' | '));
  t.check('zoom en: exact English labels (primary, mode card, section)', (await t.text('#primary .lbl')) === 'Find zoom moments' &&
    /^Follow screen action/.test(await t.text(`${S} .opts .opt.is-on`)) &&
    (await t.ev(`!!document.querySelector('${S} .seg[aria-label="Zoom strength"]')`)), await t.text('#primary .lbl'));
  await t.click('#primary');
  await t.waitFor(`location.hash === '#tool/zoom/review' && document.querySelectorAll('${S} .rv').length > 0`, 120000, 'zoom review (en)');
  await t.wait(400);
  left = await t.idLeftovers('#app');
  t.check('zoom en: review pane has no Indonesian left', left.length === 0, left.join(' | '));
  const nOn = await t.ev(`AC.tools.runtime('zoom').review.items().filter(function (x) { return x.on; }).length`);
  t.check('zoom en: review title + primary in English', /zooms? used/.test(await t.text(`${S} .rv-title`)) &&
    (await t.text('#primary .lbl')) === (nOn === 1 ? 'Apply 1 zoom' : `Apply ${nOn} zooms`), await t.text('#primary .lbl'));
  await t.shot(narrow ? 'build_zoom_review_en_280' : 'build_zoom_review_en');
  await t.ev(`(AC.i18n.set('id'), true)`);
  await t.ev(`(localStorage.removeItem('ac.tool.zoom'), true)`);
}

/* ------------------------------------------------------------------ mock engine (only for --engine mock) */
function mockEngine(t) {
  return t.ev(`(__acStub.engineMock.zoom = function (job, emit, done) {
    var fs = require('fs');
    if (job.action === 'analyze') {
      var items = [0, 1, 2].map(function (k) { var a = 5 + k * 12; return { id: 'z' + a * 100, t0: a, t1: a + 4, kind: 'zoom', on: true, label: 'Zoom 2x', ctx: 'kata ' + k,
        t_full: a + 0.6, t_hold: a + 3.3, z: 1.5 + k * 0.3, cx: 0.4, cy: 0.5, ease_in: 'smooth', ease_out: 'smooth', end: 'ease', anchor: 'aksi', why: ['aksi layar'] }; });
      var p = job.workdir + '\\\\zoom_review.json';
      fs.writeFileSync(p, JSON.stringify({ v: 1, tool: 'zoom', timebase: 'sequence', duration: 48.925, seq: { id: job.seq.id, name: job.seq.name }, mode: 'screen', track: 0, width: 2292, height: 960, items: items }));
      emit({ ev: 'result', data: { review: p, summary: '3 zoom', mode: 'screen', warnings: [] } }); done(0);
    } else if (job.action === 'apply') {
      var d = JSON.parse(fs.readFileSync(job.review, 'utf8')), on = d.items.filter(function (x) { return x.on; });
      var clips = [{ track: 0, start: 0, end: 48.925, 'in': 0, w: 2292, h: 960, keys: on.map(function (x) { return [x.t_full, x.z, x.cx, x.cy]; }) }];
      (job.params.reset || []).forEach(function (r) { clips[0].reset = { scale: r.scale, pos: r.pos }; });
      var pp = job.workdir + '\\\\zoom_keys.json';
      fs.writeFileSync(pp, JSON.stringify({ v: 1, interp: 'linear', seq: d.seq, clips: clips }));
      emit({ ev: 'result', data: { plan: { kind: 'keyframes', path: pp, seq: d.seq, events: on.length, clips: 1, keys: on.length, reset: (job.params.reset || []).length, interp: 'linear' }, summary: on.length + ' zoom' } }); done(0);
    } else { emit({ ev: 'error', code: 'NO_MEDIA', msg: 'mock' }); done(1); }
    return function () {};
  }, true)`);
}

/* ------------------------------------------------------------------ host simulation (real 37_zoom.jsx in a vm) */
function hostSim(t) {
  const H = path.join(t.root, 'panel', 'host') + path.sep;
  const TPS = 254016000000, FPS = 120, TPF = TPS / FPS;
  const check = (name, ok, d = '') => t.check(name, ok, d);
  const coll = (arr) => { const o = { numItems: arr.length }; arr.forEach((x, i) => { o[i] = x; }); return o; };
  function prop(v) {
    return {
      v, tv: false, keys: [], interp: {}, sets: 0,
      isTimeVarying() { return this.tv; },
      // Premiere may drop a key at the playhead when animation is switched on: simulate one at media 99 s
      setTimeVarying(on) { if (on && !this.tv) { this.tv = true; this.keys = [{ t: 99, v: this.v }]; } else if (!on) { this.tv = false; this.keys = []; } },
      addKey(tt) { if (!this.keys.some((k) => Math.abs(k.t - tt) < 1e-9)) { this.keys.push({ t: tt, v: this.v }); this.keys.sort((a, b) => a.t - b.t); } },
      setValueAtKey(tt, x) { const k = this.keys.find((k) => Math.abs(k.t - tt) < 1e-9); if (!k) throw new Error('no key at ' + tt); k.v = Array.isArray(x) ? x.slice() : x; },
      setInterpolationTypeAtKey(tt, c) { this.interp[tt] = c; },
      getKeys() { return this.keys.map((k) => ({ seconds: k.t })); },
      removeKey(tm) { const s = typeof tm === 'number' ? tm : tm.seconds; this.keys = this.keys.filter((k) => Math.abs(k.t - s) > 1e-9); },
      setValue(x) { this.v = Array.isArray(x) ? x.slice() : x; this.sets++; },
      getValue() { return this.v; },
      getValueAtTime(tt) {
        if (!this.tv || !this.keys.length) return this.v;
        const ks = this.keys; if (tt <= ks[0].t) return ks[0].v; if (tt >= ks[ks.length - 1].t) return ks[ks.length - 1].v;
        const i = ks.findIndex((k) => k.t > tt), a = ks[i - 1], b = ks[i], u = (tt - a.t) / (b.t - a.t);
        return Array.isArray(a.v) ? a.v.map((x, j) => x + (b.v[j] - x) * u) : a.v + (b.v - a.v) * u;
      }
    };
  }
  function clip(a, b, inp, opts = {}) {
    const pos = prop([0.5, 0.5]), sc = prop(opts.scale || 100), scw = prop(100), uni = prop(opts.uniform === false ? false : true);
    if (opts.userKeys) { sc.tv = true; sc.keys = [{ t: inp + 1, v: 120 }]; }
    if (opts.ourKeys) { sc.tv = true; pos.tv = true; sc.keys = [{ t: inp, v: 180 }]; pos.keys = [{ t: inp, v: [0.9, 0.7] }]; }
    const motion = { matchName: 'AE.ADBE Motion', properties: coll([pos, sc, scw, uni]) };
    return { name: 'x.mp4', start: { seconds: a }, end: { seconds: b }, inPoint: { seconds: inp }, getSpeed: () => 1, motion,
             components: coll([{ matchName: 'AE.ADBE Opacity', properties: coll([]) }, motion]) };
  }
  const track = (clips) => ({ list: clips, get clips() { return coll(this.list); } });
  const tracks = (arr) => { const o = coll(arr); o.numTracks = arr.length; return o; };
  function mkSeq(id, name, v1) {
    return { sequenceID: id, name, timebase: String(TPF), frameSizeHorizontal: 2292, frameSizeVertical: 960, end: String(30 * TPS),
             videoTracks: tracks([track(v1), track([clip(1, 3, 0)])]), audioTracks: tracks([]),
             clone() { const c = mkSeq('clone-' + (seqs.length + 1), name + ' Copy', this.videoTracks[0].list.map((x) => clip(x.start.seconds, x.end.seconds, x.inPoint.seconds, x.opts))); seqs.push(c); return true; } };
  }
  const seqs = [];
  const orig = mkSeq('orig-1', 'Tutorial (AutoCut)', [
    Object.assign(clip(0, 5, 10), { opts: {} }),
    Object.assign(clip(5, 7.5, 20, { userKeys: true }), { opts: { userKeys: true } }),
    Object.assign(clip(7.5, 12, 30), { opts: {} }),
    Object.assign(clip(12, 20, 40, { ourKeys: true }), { opts: { ourKeys: true } })]);
  seqs.push(orig);
  const sequences = { get numSequences() { return seqs.length; } };
  for (let i = 0; i < 10; i++) Object.defineProperty(sequences, i, { get() { return seqs[i]; } });
  function FileMock(p) { this.p = String(p); this.fsName = this.p; this.exists = fs.existsSync(this.p); if (this.exists) { const st = fs.statSync(this.p); this.length = st.size; this.modified = st.mtime; } }
  FileMock.prototype.open = function () { return this.exists; };
  FileMock.prototype.read = function () { return fs.readFileSync(this.p, 'utf8'); };
  FileMock.prototype.close = function () { return true; };
  const ctx = { app: { enableQE() {}, project: { sequences, activeSequence: orig, openSequence(id) { this.opened = id; } } },
                $: { global: {} }, File: FileMock, Time: function () {}, ProjectItemType: {} };
  vm.createContext(ctx);
  for (const f of ['00_util.jsx', '10_timeline.jsx', '37_zoom.jsx']) vm.runInContext(fs.readFileSync(H + f, 'utf8'), ctx, { filename: f });
  const L = (x) => vm.runInContext('(' + JSON.stringify(x) + ')', ctx);

  // clone naming
  let r = JSON.parse(ctx.bac_zoom_clone('orig-1'));
  check('host sim: clone "<name> (Zoom)", original re-activated', r.ok && r.name === 'Tutorial (AutoCut) (Zoom)' && r.origId === 'orig-1' && ctx.app.project.opened === 'orig-1', JSON.stringify(r));
  const clone = seqs[seqs.length - 1];
  const r2 = JSON.parse(ctx.bac_zoom_clone(clone.sequenceID));
  check('host sim: zooming a zoom result -> "(Zoom 2)", not "(Zoom) (Zoom)"', r2.name === 'Tutorial (AutoCut) (Zoom 2)', r2.name);

  // plan: clip 0 keyed, clip 1 has the user's own keys (skip), clip 2 letterboxed media, clip 3 = our old keys (reset only)
  const planPath = path.join(t.sandbox, 'temp', 'zoom_keys_sim.json');
  fs.mkdirSync(path.dirname(planPath), { recursive: true });
  fs.writeFileSync(planPath, JSON.stringify({ v: 1, interp: 'linear', seq: { id: 'orig-1' }, clips: [
    { track: 0, start: 0, end: 5, in: 10, w: 2292, h: 960, keys: [[10, 1, 0.5, 0.5], [11, 2, 0.3, 0.4], [13, 2, 0.3, 0.4], [14, 1, 0.5, 0.5]] },
    { track: 0, start: 5, end: 7.5, in: 20, w: 2292, h: 960, keys: [[20, 1.5, 0.5, 0.5]] },
    { track: 0, start: 7.5, end: 12, in: 30, w: 1146, h: 480, keys: [[30, 2, 0.3, 0.4]] },
    { track: 0, start: 12, end: 20, in: 40, keys: [], reset: { scale: 100, pos: [0.5, 0.5] } }] }));
  const pa = JSON.parse(ctx.bac_zoom_keys(r.id, planPath, 0, 2));
  const pb = JSON.parse(ctx.bac_zoom_keys(r.id, planPath, 2, 4));
  check('host sim: chunk 1 keys clip 0 and skips the clip with user keys', pa.ok && pa.done === 1 && pa.keys === 4 && pa.skipped.length === 1 && pa.skipped[0].why === 'keys', JSON.stringify(pa));
  check('host sim: chunk 2 keys the letterboxed clip and resets our old keys', pb.done === 2 && pb.keys === 1 && pb.reset === 1, JSON.stringify(pb));
  const v = clone.videoTracks[0].list;
  const sc0 = v[0].motion.properties[1], pos0 = v[0].motion.properties[0];
  check('host sim: Scale keys = base x zoom, in media time', JSON.stringify(sc0.keys.map((k) => [k.t, k.v])) === JSON.stringify([[10, 100], [11, 200], [13, 200], [14, 100]]), JSON.stringify(sc0.keys));
  const p11 = pos0.keys.find((k) => k.t === 11).v;
  check('host sim: Position = 0.5 + (0.5 - c) * s (zoom to the viewport centre)', Math.abs(p11[0] - 0.9) < 1e-9 && Math.abs(p11[1] - 0.7) < 1e-9, JSON.stringify(p11));
  check('host sim: stray key from setTimeVarying removed', !sc0.keys.some((k) => k.t === 99) && !pos0.keys.some((k) => k.t === 99));
  check('host sim: linear interpolation set on every key', Object.keys(sc0.interp).length === 4 && Object.values(sc0.interp).every((c) => c === 0));
  check('host sim: user keyed clip untouched', v[1].motion.properties[1].keys.length === 1 && v[1].motion.properties[1].keys[0].v === 120);
  const p2 = v[2].motion.properties[0].keys[0].v;
  check('host sim: letterboxed media (half size) offsets Position by its displayed size', Math.abs(p2[0] - 0.7) < 1e-9 && Math.abs(p2[1] - 0.6) < 1e-9, JSON.stringify(p2));
  const sc3 = v[3].motion.properties[1], pos3 = v[3].motion.properties[0];
  check('host sim: old AutoCut zoom keys cleared and base restored', !sc3.tv && !pos3.tv && sc3.v === 100 && pos3.v.join() === '0.5,0.5');
  check('host sim: base values returned for the re-run record', pa.base.length === 1 && pa.base[0].scale === 100 && pa.base[0].start === 0 && pb.base.length === 1, JSON.stringify(pa.base));
  check('host sim: original sequence untouched', orig.videoTracks[0].list[0].motion.properties[1].keys.length === 0 && !orig.videoTracks[0].list[0].motion.properties[1].tv);
  const scan = JSON.parse(ctx.bac_zoom_scan(r.id, 0));
  check('host sim: scan reports keys per clip', scan.clips.length === 4 && scan.clips[0].nScale === 4 && scan.clips[3].scaleTV === false && scan.clips[3].scale === 100, JSON.stringify(scan.clips[0]));
  const vals = JSON.parse(ctx.bac_zoom_values(r.id, 0, L([1.5, 3.5, 6])));
  check('host sim: values read back at sequence times (media time conversion)', vals.values[0].scale === 200 && Math.abs(vals.values[1].scale - 150) < 1e-9 && vals.values[2].media === 21, JSON.stringify(vals.values));
  const bad = ctx.bac_zoom_keys('nope', planPath, 0, 1);
  check('host sim: unknown sequence -> ERR:', /^ERR:/.test(bad), bad);
  fs.rmSync(planPath, { force: true });
}
