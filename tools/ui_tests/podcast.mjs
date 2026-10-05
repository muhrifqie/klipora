// podcast.mjs: Podcast Multicam checks (run: node tools/ui_test.mjs tools/ui_tests/podcast.mjs --engine live).
// 1. host/40_podcast.jsx (ES3) runs in node vm against a fake Premiere model (tracks, clips, QE razor, lock, CTI):
//    razor plan -> disable -> exactly the planned camera is enabled at every instant; scope edges; locked track;
//    switch at playhead (+ split); refuses the original sequence.
// 2. Panel with the REAL engine on a SYNTHETIC two-speaker fixture (engine/tests/test_podcast.py --make: the 2 min
//    test speech split into two 'mics'; three camera tracks = the same video): auto mapping, analyze, review
//    (speaker ribbon, filters, camera pick, row off), apply (engine plan -> host calls, stubbed), result, switcher.
import { spawnSync } from 'child_process';
import fs from 'fs';
import path from 'path';
import vm from 'vm';

const MEDIA = globalThis.KLIPORA_MEDIA.seq_2m;   // KLIPORA_TEST_MEDIA/seq_2m.mp4
const TICKS = 254016000000;

/* ------------------------------------------------------------------ fake Premiere for the host code */
function fakePremiere(fps) {
  const seqs = [];
  const item = (s, e) => ({ start: { seconds: s }, end: { seconds: e }, disabled: false });
  function track(spans) {
    const tr = { items: spans.map(([s, e]) => item(s, e)), locked: false, razors: 0, isLocked() { return tr.locked; } };
    Object.defineProperty(tr, 'clips', { get() { const o = { numItems: tr.items.length }; tr.items.forEach((it, i) => { o[i] = it; }); return o; } });
    return tr;
  }
  function seq(id, name, tracks) {
    const vt = tracks.map(track);
    const s = {
      sequenceID: id, name, timebase: String(TICKS / fps), cti: 0, tracks: vt,
      videoTracks: Object.assign({ numTracks: vt.length }, vt),
      getPlayerPosition() { return { ticks: String(Math.round(this.cti * TICKS)), seconds: this.cti }; },
      setPlayerPosition(t) { this.cti = Number(t) / TICKS; },
      getSettings() { return { videoDisplayFormat: 998 }; }
    };
    seqs.push(s);
    return s;
  }
  const app = {
    project: {
      activeSequence: null,
      get sequences() { const o = { numSequences: seqs.length }; seqs.forEach((s, i) => { o[i] = s; }); return o; },
      openSequence(id) { this.activeSequence = seqs.find((s) => s.sequenceID === id) || this.activeSequence; return true; }
    },
    enableQE() {}
  };
  const qe = {
    project: {
      getActiveSequence() {
        const s = app.project.activeSequence;
        return {
          getVideoTrackAt(i) {
            const tr = s.tracks[i];
            return {
              isLocked: () => tr.locked, setLock: (v) => { tr.locked = !!v; },
              razor(tc) {                                   // "HH:MM:SS:FFF" at the nominal rate
                if (tr.locked) return;
                const p = tc.split(':').map(Number), t = ((p[0] * 3600 + p[1] * 60 + p[2]) * fps + p[3]) / fps;
                const k = tr.items.findIndex((it) => it.start.seconds < t - 1e-9 && t < it.end.seconds - 1e-9);
                if (k < 0) return;
                const it = tr.items[k], b = item(t, it.end.seconds);
                b.disabled = it.disabled; it.end = { seconds: t };
                tr.items.splice(k + 1, 0, b); tr.razors++;
              }
            };
          }
        };
      }
    }
  };
  return { app, qe, seq, seqs };
}

function loadHost(root, fake) {
  const ctx = vm.createContext({ app: fake.app, qe: fake.qe, $: { global: {} }, ProjectItemType: { BIN: 2 }, File: function () {}, Time: function () {} });
  for (const f of ['00_util.jsx', '10_timeline.jsx', '40_podcast.jsx']) vm.runInContext(fs.readFileSync(path.join(root, 'panel', 'host', f), 'utf8'), ctx, { filename: f });
  const call = (fn, ...args) => { const r = ctx[fn](...args); if (String(r).startsWith('ERR:')) return { err: String(r).slice(4) }; return JSON.parse(r); };
  return { ctx, call };
}

// Same rules as engine build_plan: razor only the tracks whose state changes, all cameras at scope edges.
function planOf(shots, cams, dur) {
  const cuts = [];
  for (let i = 1; i < shots.length; i++) cuts.push([shots[i][0], [...new Set([shots[i - 1][2], shots[i][2]])].sort()]);
  if (shots[0][0] > 0) cuts.unshift([shots[0][0], cams.slice().sort()]);
  if (shots[shots.length - 1][1] < dur) cuts.push([shots[shots.length - 1][1], cams.slice().sort()]);
  return { shots, cuts, cams, scope: [shots[0][0], shots[shots.length - 1][1]] };
}

function visibleAt(s, cams, t) {
  const on = [];
  for (const c of cams) for (const it of s.tracks[c].items) if (it.start.seconds <= t && t < it.end.seconds && !it.disabled) on.push(c);
  return on;
}

function hostSim(t) {
  const fps = 120, cams = [1, 2, 0];
  const fake = fakePremiere(fps), { ctx, call } = loadHost(t.root, fake);
  const orig = fake.seq('orig', 'Podcast Test', [[[0, 60]], [[0, 60]], [[0, 60]]]);
  const res = fake.seq('res', 'Podcast Test (AutoCut)', [[[0, 60]], [[0, 60]], [[0, 30], [30, 60]]]);   // V3 already has an edit at 30
  ctx.$.global.__acMade = { res: 1 };
  fake.app.project.activeSequence = orig;
  res.tracks[2].locked = true;
  res.cti = 12.34;
  const shots = [[0, 10, 1], [10, 22.5, 2], [22.5, 25, 0], [25, 30, 1], [30, 40, 2], [40, 60, 1]];
  const plan = planOf(shots, cams, 60);
  const r1 = call('bac_podcast_razor', 'res', plan.cuts, 0, 2), r2 = call('bac_podcast_razor', 'res', plan.cuts, 2, plan.cuts.length);
  const want = plan.cuts.reduce((a, c) => a + c[1].length, 0);
  t.check('host sim: razor in chunks (skips existing edit at 30 s)', !r1.err && !r2.err && r1.done + r2.done + r1.skipped + r2.skipped === want && r2.skipped === 1,
    JSON.stringify([r1, r2]));
  t.check('host sim: locked camera track razored and locked again, CTI restored', res.tracks[2].razors > 0 && res.tracks[2].locked && Math.abs(res.cti - 12.34) < 1e-6);
  const d = cams.map((c) => call('bac_podcast_disable', 'res', c, plan.shots, plan.scope));
  t.check('host sim: disable per camera, nothing unsplit', d.every((x) => !x.err && x.unsplit === 0), JSON.stringify(d));
  let bad = 0;
  for (let x = 0.01; x < 60; x += 0.05) {
    const v = visibleAt(res, cams, x), s = shots.find((sh) => sh[0] <= x && x < sh[1]);
    if (v.length !== 1 || v[0] !== s[2]) bad++;
  }
  t.check('host sim: exactly the planned camera is enabled at every instant', bad === 0, bad + ' bad samples');
  t.check('host sim: original sequence refused', !!call('bac_podcast_razor', 'orig', plan.cuts, 0, 1).err && !!call('bac_podcast_disable', 'orig', 1, plan.shots, plan.scope).err &&
    orig.tracks.every((tr) => tr.items.length === 1 && !tr.items[0].disabled));

  // scope 5..55 on a fresh result: every camera cut at the edges, clips outside the scope stay enabled
  const res2 = fake.seq('res2', 'Podcast Test (Klipora 2)', [[[0, 60]], [[0, 60]], [[0, 60]]]);
  const sh2 = [[5, 20, 1], [20, 55, 2]], p2 = planOf(sh2, cams, 60);
  call('bac_podcast_razor', 'res2', p2.cuts, 0, p2.cuts.length);
  cams.forEach((c) => call('bac_podcast_disable', 'res2', c, p2.shots, p2.scope));
  const outside = [2, 57].every((x) => visibleAt(res2, cams, x).length === 3), inside = [6, 30, 54].every((x) => visibleAt(res2, cams, x).length === 1);
  t.check('host sim: In/Out scope (edges razored on all cameras, outside untouched)', outside && inside && res2.tracks[0].items.length === 3);
  t.check('host sim: result names "(Klipora[ n])" and the old "(AutoCut[ n])" are ours, others not',
    ctx.acPodIsResult({ sequenceID: 'n1', name: 'A (Klipora)' }) && ctx.acPodIsResult({ sequenceID: 'n2', name: 'B (AutoCut 3)' }) && !ctx.acPodIsResult({ sequenceID: 'n3', name: 'Podcast Test' }));

  // "Ganti kamera di playhead" on the active result
  fake.app.project.activeSequence = res;
  res.cti = 27;                                                 // shot [25,30) on V2 (track 1)
  const s1 = call('bac_podcast_switchAt', 2, cams, false, '');
  const okSwitch = !s1.err && s1.changed === 2 && visibleAt(res, cams, 26)[0] === 2 && visibleAt(res, cams, 29.9)[0] === 2 && visibleAt(res, cams, 31)[0] === 2 && visibleAt(res, cams, 24)[0] === 0;
  t.check('host sim: switch at playhead swaps the whole shot', okSwitch && s1.from === 25 && s1.to === 30, JSON.stringify(s1));
  let multi = 0;
  for (let x = 0.01; x < 60; x += 0.05) if (visibleAt(res, cams, x).length !== 1) multi++;
  t.check('host sim: still exactly one camera everywhere after the switch', multi === 0, multi + ' bad samples');
  res.cti = 50;                                                 // shot [40,60) on V2 (track 1)
  const s2 = call('bac_podcast_switchAt', 0, cams, true, '');
  t.check('host sim: switch with split only changes from the playhead on', !s2.err && s2.cut >= 1 && visibleAt(res, cams, 45)[0] === 1 && visibleAt(res, cams, 55)[0] === 0 && Math.abs(s2.from - 50) < 1e-6, JSON.stringify(s2));
  const at = call('bac_podcast_camAt', cams, '');
  t.check('host sim: camAt reports the visible camera', at.result === true && at.visible === 0 && at.under.length === 3, JSON.stringify(at));
  fake.app.project.activeSequence = orig;
  const s3 = call('bac_podcast_switchAt', 2, cams, false, '');
  t.check('host sim: switch refuses the original sequence', !!s3.err && /sequence asli/i.test(s3.err), JSON.stringify(s3));
}

/* ------------------------------------------------------------------ panel */
function clip(name, p, end, extra) {
  return Object.assign({ name, path: p, start: 0, end, in: 0, out: end, speed: 1, disabled: false, selected: false, mgt: false, nested: false, nodeId: name + end }, extra || {});
}
function fixtureSeq(dur, micA, micB) {
  const v = (i, name) => ({ index: i, name, muted: false, locked: false, targeted: i === 0, count: 1, clips: [clip('Interview - Part 2.mp4', MEDIA, dur, { nodeId: 'v' + i })] });
  const a = (i, name, p) => ({ index: i, name, muted: false, locked: false, targeted: i === 0, count: 1, clips: [clip(path.basename(p), p, dur, { nodeId: 'a' + i })] });
  return { id: 'seq-podcast', name: 'Podcast Uji', fps: 120, timebase: '2116800000', width: 2292, height: 960, displayFormat: 998, duration: dur,
    inPoint: null, outPoint: null, player: 0, active: true, level: 'full',
    video: [v(0, 'Wide'), v(1, 'Cam Andi'), v(2, 'Cam Budi')], audio: [a(0, 'Mic Andi', micA), a(1, 'Mic Budi', micB)],
    markers: [], selection: [], selectedCount: 0, ms: 3 };
}

export default async function (t) {
  hostSim(t);

  if (!fs.existsSync(MEDIA)) { console.log('skip: podcast panel part needs seq_2m.mp4, set KLIPORA_TEST_MEDIA'); t.check('fixture media present (SKIP panel part)', true); return; }
  const dir = path.join(t.sandbox, 'home', 'Videos', 'podcast_fixture');
  const mk = spawnSync('python', ['-X', 'utf8', path.join(t.root, 'engine', 'tests', 'test_podcast.py'), '--make', dir], { encoding: 'utf8' });
  t.check('synthetic mics written', mk.status === 0, mk.stderr);
  const info = JSON.parse(mk.stdout);
  await t.setSeq(fixtureSeq(info.duration, info.a, info.b));
  await t.wait(400);
  await t.go('tool/podcast');
  await t.waitFor(`document.querySelectorAll('[data-screen="tool-podcast"] .podcast-spk').length === 2`, 5000, 'speaker cards');
  const map = await t.ev(`JSON.stringify(AC.tools.runtime('podcast').ctx.podcastMap())`);
  const m = JSON.parse(map);
  t.check('auto mapping from track names (Mic/Cam Andi, Budi, Wide)',
    m.speakers.length === 2 && m.speakers[0].name === 'Andi' && m.speakers[0].mic === 0 && m.speakers[0].cam === 1 &&
    m.speakers[1].name === 'Budi' && m.speakers[1].mic === 1 && m.speakers[1].cam === 2 && m.wide === 0, map);
  t.check('guess by order when tracks are unnamed (V1 = wide)', await t.ev(`(function(){ var g = AC.podcast.guess({audio:[{index:0,name:'Audio 1',count:1},{index:1,name:'Audio 2',count:1}],
    video:[{index:0,name:'Video 1',count:1},{index:1,name:'Video 2',count:1},{index:2,name:'Video 3',count:1}]});
    return g.wide === 0 && g.speakers[0].cam === 1 && g.speakers[1].cam === 2 && g.speakers[0].name === 'Pembicara 1'; })()`));
  t.check('primary says "Analisis 2 pembicara"', /Analisis 2 pembicara/.test(await t.text('#primary')) && !(await t.ev(`document.getElementById('primary').disabled`)));
  await t.host('bac_podcast_camAt', 'function (cams) { return JSON.stringify({ok:true, t: 0, id: "seq-podcast", name: "Podcast Uji", result: false, visible: 1, under: cams}); }');
  await t.ev(`(AC.tools.runtime('podcast').ctx.podcastRefresh(), true)`);
  await t.wait(300);
  const overflow = await t.ev(`document.querySelector('[data-screen="tool-podcast"]').scrollWidth > document.querySelector('[data-screen="tool-podcast"]').clientWidth + 1`);
  t.check('main pane: no horizontal overflow at ' + t.width + ' px', !overflow);
  await t.shot('build_podcast');

  // invalid mapping: same mic twice -> warning + disabled primary
  await t.ev(`(function(){ var s = document.querySelectorAll('[data-screen="tool-podcast"] .podcast-spk')[1].querySelectorAll('select')[0]; s.value = '0'; s.dispatchEvent(new Event('change')); return true; })()`);
  await t.wait(100);
  t.check('same mic twice -> warning, primary disabled', (await t.ev(`document.getElementById('primary').disabled`)) && /track mic yang sama/.test(await t.text('[data-screen="tool-podcast"] .podcast-issue')));
  await t.ev(`(function(){ var s = document.querySelectorAll('[data-screen="tool-podcast"] .podcast-spk')[1].querySelectorAll('select')[0]; s.value = '1'; s.dispatchEvent(new Event('change')); return true; })()`);
  await t.wait(100);
  t.check('fixed mapping -> primary enabled', !(await t.ev(`document.getElementById('primary').disabled`)));

  // analyze with the real engine
  await t.click('#primary');
  await t.waitFor(`location.hash === '#tool/podcast/review'`, 60000, 'review pane');
  await t.wait(300);
  const rows = await t.count('[data-screen="tool-podcast"] .rv');
  t.check('review: shot rows', rows >= 5, String(rows));
  t.check('review: speaker ribbon shown, cut ribbon hidden', await t.ev(`(function(){ var h = document.querySelector('.podcast-rv .rv-head'); var r = h.querySelector('canvas.podcast-rib'), c = h.querySelector('canvas.ribbon');
    return !!r && r.clientHeight >= 20 && getComputedStyle(c).display === 'none'; })()`));
  t.check('review: legend with screen share per camera', /Andi \d+%/.test(await t.text('.podcast-rv .podcast-legend')) && /Wide \d+%/.test(await t.text('.podcast-rv .podcast-legend')));
  const title0 = await t.text('.podcast-rv .rv-title');
  t.check('review: title counts camera switches', /\d+ pergantian kamera/.test(title0), title0);
  t.check('review: filter chips per camera', /Andi/.test(await t.text('.podcast-rv .rv-tools')) && /Kurang yakin/.test(await t.text('.podcast-rv .rv-tools')));
  const rv = `AC.tools.runtime('podcast').ctx.podcastReview`;
  const sw0 = await t.ev(`AC.podcast.switches(${rv}.items)`);
  // camera pick on row 3 via the row action buttons
  await t.ev(`(function(){ var r = ${rv}; r.list.setActive(2, false); return true; })()`);
  await t.wait(100);
  const acts = await t.ev(`Array.prototype.map.call(document.querySelectorAll('.podcast-rv .rv.is-active .rv-actions button'), function (b) { return b.textContent; }).join('|')`);
  t.check('review: active row has camera buttons', /Andi/.test(acts) && /Budi/.test(acts) && /Wide/.test(acts), acts);
  const before = await t.ev(`${rv}.items[2].cam`);
  const target = before === 0 ? 'Andi' : 'Wide';
  await t.ev(`(function(){ var b = Array.prototype.filter.call(document.querySelectorAll('.podcast-rv .rv.is-active .rv-actions button'), function (x) { return x.textContent.indexOf(${JSON.stringify(target)}) >= 0; })[0]; b.click(); return true; })()`);
  await t.wait(150);
  const after = await t.ev(`JSON.stringify(${rv}.items[2])`);
  t.check('review: camera picked by hand (row 3 -> ' + target + ')', JSON.parse(after).cam === (target === 'Wide' ? 0 : 1) && JSON.parse(after).pick === true && /Manual/.test(await t.text('.podcast-rv .rv.is-active')), after);
  // switch a row off -> previous camera continues, switch count changes
  await t.ev(`(function(){ ${rv}.list.toggle(4, false); return true; })()`);
  await t.wait(200);
  const sw1 = await t.ev(`AC.podcast.switches(${rv}.items)`);
  t.check('review: unchecked row keeps the previous camera', sw1 < sw0 + 2 && /Terapkan \d+ pergantian/.test(await t.text('#primary')), sw0 + ' -> ' + sw1);
  await t.shot('build_podcast_review');

  // apply: engine plan -> host (stubbed recorders)
  await t.host('bac_podcast_razor', 'function (id, cuts, from, to) { var n = 0; for (var i = from; i < to; i++) n += cuts[i][1].length; return JSON.stringify({ok: true, done: n, skipped: 0, from: from, to: to, ms: 5}); }');
  await t.host('bac_podcast_disable', 'function (id, track, shots, scope) { var en = 0, dis = 0; shots.forEach(function (s) { if (s[2] === track) en++; else dis++; }); return JSON.stringify({ok: true, track: track, enabled: en, disabled: dis, changed: dis, unsplit: 0, outside: 0, ms: 3}); }');
  await t.host('bac_podcast_camAt', 'function (cams) { return JSON.stringify({ok:true, t: 12.5, id: "x", name: "Podcast Uji (Klipora)", result: true, visible: cams[0], under: cams}); }');
  await t.host('bac_podcast_switchAt', 'function (cam, cams, split) { return JSON.stringify({ok: true, t: 12.5, cam: cam, changed: 2, found: cams.length, cut: split ? cams.length : 0, from: 10, to: 22.5}); }');
  await t.click('#primary');
  await t.waitFor(`location.hash === '#tool/podcast/result'`, 60000, 'result pane');
  await t.wait(300);
  t.check('result: "Multicam siap" with stats', (await t.text('[data-screen="tool-podcast"] .result-h h2')) === 'Multicam siap' && /Pergantian/.test(await t.text('[data-screen="tool-podcast"] [data-pane="result"] .sum-strip')));
  const razor = await t.calls('bac_podcast_razor'), dis = await t.calls('bac_podcast_disable'), clone = await t.calls('bac_cloneSeq');
  const cuts = razor.length ? razor[0][1] : [];
  let cover = razor.length > 0, next = 0;
  razor.forEach((a) => { if (a[2] !== next) cover = false; next = a[3]; });
  cover = cover && next === cuts.length && cuts.every((c) => c[1].length >= 1 && c[1].length <= 3);
  t.check('apply: clone of the analyzed sequence, razor chunks cover every cut point in order', clone.length === 1 && clone[0][1] === 'seq-podcast' && /\(Klipora\)$/.test(clone[0][0]) && cover,
    JSON.stringify({ clone, chunks: razor.map((a) => [a[2], a[3]]), n: cuts.length }));
  const shots = dis.length ? dis[0][2] : [];
  const contiguous = shots.every((s, i) => i === 0 || Math.abs(shots[i - 1][1] - s[0]) < 1e-9);
  t.check('apply: disable called once per camera with contiguous frame-aligned shots', dis.length === 3 && dis.map((a) => a[1]).sort().join() === '0,1,2' && contiguous &&
    shots.every((s) => Math.abs(s[0] * 120 - Math.round(s[0] * 120)) < 1e-3), JSON.stringify(dis.map((a) => a[1])));
  const pickT = JSON.parse(after), mid = (pickT.t0 + pickT.t1) / 2, shotAt = shots.find((s) => s[0] <= mid && mid < s[1]);
  t.check('apply: hand-picked camera reached the plan', !!shotAt && shotAt[2] === pickT.cam, JSON.stringify(shotAt));
  await t.wait(200);
  t.check('result: switcher knows the visible camera', /Tampil di playhead/.test(await t.text('[data-screen="tool-podcast"] [data-pane="result"] .podcast-status')));
  await t.shot('build_podcast_result');
  await t.ev(`(function(){ document.querySelector('[data-screen="tool-podcast"] [data-pane="result"] .podcast-cams .btn').click(); return true; })()`);
  await t.wait(200);
  const swc = await t.calls('bac_podcast_switchAt');
  t.check('result: camera button calls bac_podcast_switchAt', swc.length === 1 && Array.isArray(swc[0][1]) && swc[0][1].length === 3, JSON.stringify(swc));

  // Alt+2 on the main pane switches to camera 2
  await t.go('tool/podcast');
  await t.wait(200);
  await t.ev(`(document.body.focus(), true)`);
  await t.key('2', { alt: true });
  await t.wait(200);
  const swc2 = await t.calls('bac_podcast_switchAt');
  t.check('Alt+2 = camera 2 at the playhead', swc2.length === 2 && swc2[1][0] === 2, JSON.stringify(swc2));
  // the analyzed sequence changed (different duration) -> apply refuses with a clear message
  await t.ev(`(__acStub.seq.duration = __acStub.seq.duration - 5, true)`);
  await t.go('tool/podcast/review');
  await t.wait(200);
  await t.click('#primary');
  await t.waitFor(`location.hash === '#tool/podcast/error'`, 60000, 'error pane');
  t.check('apply refuses a sequence that changed since the analysis', /berubah sejak dianalisis/.test(await t.text('[data-screen="tool-podcast"] [data-pane="error"]')));
  const hist = await t.ev(`AC.history.list().filter(function (x) { return x.tool === 'podcast'; }).map(function (x) { return x.sub || ''; }).join(' | ')`);
  t.check('Riwayat lines for analyze and apply', /pergantian kamera, 2 pembicara/.test(hist) && /pergantian kamera, sequence/.test(hist), hist);

  // ---- English UI: main pane + review (a fresh analysis in English; the engine gets job.lang = en)
  await t.ev(`(__acStub.seq.duration = __acStub.seq.duration + 5, true)`);
  await t.ev(`(AC.i18n.set('en'), true)`);
  await t.go('tool/podcast');
  await t.waitFor(`document.querySelectorAll('[data-screen="tool-podcast"] .podcast-spk').length === 2`, 5000, 'speaker cards (en)');
  await t.wait(200);
  const leftMain = await t.idLeftovers('#app');
  t.check('en main: no Indonesian leftovers', leftMain.length === 0, JSON.stringify(leftMain));
  const secTitles = await t.ev(`[].map.call(document.querySelectorAll('[data-screen="tool-podcast"] .sec-h span:first-child'), function (e) { return e.textContent; }).join('|')`);
  t.check('en main: exact labels (Speakers, Switching style, Analyze 2 speakers)', /(^|\|)Speakers(\||$)/.test(secTitles) && /Switching style/.test(secTitles) &&
    (await t.text('#primary')).trim().indexOf('Analyze 2 speakers') === 0, secTitles + ' / ' + (await t.text('#primary')));
  await t.shot('build_podcast_en');
  await t.click('#primary');
  await t.waitFor(`location.hash === '#tool/podcast/review'`, 60000, 'review pane (en)');
  await t.wait(300);
  const leftRv = await t.idLeftovers('#app');
  t.check('en review: no Indonesian leftovers', leftRv.length === 0, JSON.stringify(leftRv));
  const rvTitle = await t.text('.podcast-rv .rv-title'), rvDock = (await t.text('#primary')).trim();
  t.check('en review: exact labels (camera switches, Less certain, Apply N switches)', /^\d+ camera switch(es)?\d+ shots?/.test(rvTitle) &&
    /Less certain/.test(await t.text('.podcast-rv .rv-tools')) && /^Apply \d+ switch(es)?(Ctrl|$)/.test(rvDock), rvTitle + ' / ' + rvDock);
  await t.shot('build_podcast_review_en');
  await t.ev(`(AC.i18n.set('id'), true)`);
  await t.go('tool/podcast');
  await t.wait(200);
  t.check('back to Indonesian', /Analisis 2 pembicara/.test(await t.text('#primary')));
}
