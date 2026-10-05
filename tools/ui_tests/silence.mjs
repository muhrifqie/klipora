// silence.mjs: Potong Silence page in headless Chrome with the fake CEP stub and the REAL engine (--engine live).
// node tools/ui_test.mjs tools/ui_tests/silence.mjs            (default fixture raw49 = the 49 s test clip)
// Checks: preview + live counts, JS detector == engine for every preset/offset/guard (49 s, 15-clip cut seq,
// 35 min), sliders/presets/threshold keys, analyze -> Tinjau -> apply for remove / markers / mute, widths.
export default async function (t) {
  const ev = (s) => t.ev(s);
  const rt = `AC.tools.runtime('silence')`;
  const pv = `${rt}.ctx.silence`;
  if (t.engine !== 'live') {          // mock engine has no silence tool: the page must still open cleanly
    await t.go('tool/silence');
    await t.waitFor(`${rt} && ${pv} && (${pv}.err || ${pv}.data)`, 15000, 'silence preview (mock)');
    t.check('mock engine: page opens, primary usable, reason in the waveform legend', (await t.text('#primary')).indexOf('Deteksi jeda') === 0 &&
      /Grafik belum bisa dibuat/.test(await t.text('[data-screen="tool-silence"] .wave-foot')), await t.text('[data-screen="tool-silence"] .wave-foot'));
    console.log('silence.mjs: full checks need --engine live (real envelopes and engine parity)');
    return;
  }

  await t.go('tool/silence');
  await t.waitFor(`${rt} && ${pv} && ${pv}.data && ${pv}.live`, 30000, 'silence preview');
  const d0 = await ev(`(function(){ var p=${pv}; return {n:p.data.n, base:p.data.base, auto:p.data.auto, words:p.data.words.length, check:p.check, live:p.live.cuts.length, dur:p.data.dur}; })()`);
  t.check('preview: envelope + auto threshold from the engine', d0.n === 4893 && d0.base === -53 && d0.auto === -52.5 && d0.words > 50, JSON.stringify(d0));
  t.check('preview: JS port equals engine count (self-check)', d0.check && d0.check.ok, JSON.stringify(d0.check));
  t.check('main: strip shows live count', (await t.text('.silence-strip div:first-child b')) === String(d0.live));
  t.check('main: dock label = Tinjau N jeda', (await t.text('#primary')).indexOf('Tinjau ' + d0.live + ' jeda') === 0, await t.text('#primary'));
  t.check('main: transcript tag', /kata dilindungi/.test(await t.text('.silence-info')));
  t.check('main: waveform painted', await ev(`(function(){ var c=document.querySelector('[data-screen="tool-silence"] canvas.wave'), d=c.getContext('2d').getImageData(0,0,c.width,c.height).data, n=0; for (var i=3;i<d.length;i+=4) if (d[i]) n++; return n > 800; })()`));
  await t.shot(t.width === 380 ? 'build_silence' : 'build_silence_' + t.width);

  // ---- JS detector vs engine for presets x offsets x guard (each comparison = one engine preview)
  const cmp = async (label) => {
    const r = await ev(`(async function(){
      var bad = [], runs = 0, P = AC.silence.presets;
      for (var i = 0; i < P.length; i++) for (var o of [-6, 0, 5]) for (var g of [true, false]) {
        var v = P[i].values, prm = {preset: P[i].value, min_silence: v.minSilence, pad_before: v.padBefore, pad_after: v.padAfter, min_talk: v.minTalk, offset: o, guard: g};
        if (o === 0 && i === 1) prm.scope = {kind: 'inout', t0: 5, t1: Math.min(30, 0.6 * (AC.seq.peek() ? AC.seq.peek().duration : 30))};
        var d = await AC.engine.worker({tool:'silence', action:'preview', params: prm, record:false}).promise;
        var r = AC.silence.detect(d, AC.silence.decode(d.env, d.floor), {min_silence: prm.min_silence, pad_before: prm.pad_before, pad_after: prm.pad_after, min_talk: prm.min_talk, guard: g}, d.thr);
        runs++;
        if (r.cuts.length !== d.check.n || Math.abs(r.sec - d.check.sec) > 0.002) bad.push([P[i].value, o, g, r.cuts.length, d.check.n, r.sec, d.check.sec]);
      }
      return {runs: runs, bad: bad};
    })()`);
    t.check(`detector parity ${label}: ${r.runs} preset/offset/guard combos`, r.runs === 24 && !r.bad.length, JSON.stringify(r.bad));
  };
  await cmp('raw49');

  // ---- presets, sliders, threshold keys
  await ev(`(document.querySelector('[data-screen="tool-silence"] .tool-body .seg button:nth-child(4)').click(), true)`);   // Kilat
  const kil = await ev(`${pv}.live.cuts.length`);
  t.check('preset Kilat: more cuts than Natural', kil > d0.live, kil + ' vs ' + d0.live);
  t.check('preset Kilat: hint', /Shorts/.test(await t.text('[data-screen="tool-silence"] .tool-body .help')));
  await ev(`(function(){ var i=document.querySelectorAll('[data-screen="tool-silence"] .tool-body input.range')[1]; i.value='1.2'; i.dispatchEvent(new Event('input')); return true; })()`);
  t.check('manual slider -> Custom + fewer cuts', /Custom/.test(await t.text('[data-screen="tool-silence"] details .aside')) && (await ev(`${pv}.live.cuts.length`)) < kil);
  await ev(`(document.querySelector('[data-screen="tool-silence"] .tool-body .seg button:nth-child(2)').click(), true)`);   // back to Natural
  t.check('preset Natural restores the count', (await ev(`${pv}.live.cuts.length`)) === d0.live);
  await ev(`(document.querySelector('[data-screen="tool-silence"] canvas.wave').dispatchEvent(new KeyboardEvent('keydown',{key:'ArrowUp',shiftKey:true,bubbles:true})), true)`);
  const up = await ev(`({off: ${rt}.ctx.state.offset, thr: ${pv}.live.thr, out: document.querySelectorAll('[data-screen="tool-silence"] .tool-body .field output')[0].textContent})`);
  t.check('threshold line: Shift+ArrowUp = +5 dB', up.off === 5 && up.thr === -48 && /-48 dB/.test(up.out), JSON.stringify(up));
  t.check('threshold tag follows the line (no "otomatis" after a change)', /Ambang -48 dB/.test(await t.text('.silence-info')) && !/otomatis/.test(await t.text('.silence-info')), await t.text('.silence-info'));
  await ev(`(document.querySelector('[data-screen="tool-silence"] .tool-body .field .linkbtn').click(), true)`);   // Hitung otomatis
  t.check('Hitung otomatis resets the offset', (await ev(`${rt}.ctx.state.offset`)) === 0 && (await ev(`${pv}.live.cuts.length`)) === d0.live);
  await ev(`(document.querySelector('[data-screen="tool-silence"] .tool-body .sw input').click(), true)`);   // guard off
  const gOff = await ev(`${pv}.live.cuts.length`);
  await ev(`(document.querySelector('[data-screen="tool-silence"] .tool-body .sw input').click(), true)`);   // guard on
  t.check('Lindungi kata toggles the live plan', gOff >= d0.live && /dilindungi/.test(await t.text('.silence-info')), gOff + ' vs ' + d0.live);

  // ---- analyze -> review (real engine) -> apply remove (stub host clone + extract)
  await t.click('#primary');
  await t.waitFor(`location.hash === '#tool/silence/review'`, 30000, 'review pane');
  await t.wait(150);
  const rv = await ev(`(function(){ var l=${rt}.review; return {n: l.items().length, on: l.count(), notes: l.items().filter(function(it){return it.note;}).length,
    chips: Array.prototype.map.call(document.querySelectorAll('[data-screen="tool-silence"] .rv-tools .chip'), function(c){return c.textContent;}), title: document.querySelector('[data-screen="tool-silence"] .rv-title').textContent}; })()`);
  t.check('review: engine items == live count', rv.n === d0.live && rv.on === d0.live, JSON.stringify(rv));
  const near = await ev(`${rt}.review.items().filter(function(it){return it.note && it.note.type === 'guard';}).length`);
  t.check('review: filters (Mepet kata only when a gap touches a protected word) + Di atas 1 dtk', rv.chips.some((c) => /Mepet kata/.test(c)) === near > 0 && rv.chips.some((c) => /Di atas 1 dtk/.test(c)), rv.chips.join('|'));
  t.check('review: rows rendered with context', (await t.count('[data-screen="tool-silence"] .rv')) > 0 && /diam/.test(await t.text('[data-screen="tool-silence"] .rv .rv-ctx mark')));
  await ev(`(document.querySelector('[data-screen="tool-silence"] .rv:nth-child(2)').click(), true)`);
  await t.wait(200);
  const seeks = await t.calls('bac_seek');
  const t1 = await ev(`(function(){ var l=${rt}.review; return l.items()[Number(document.querySelector('[data-screen="tool-silence"] .rv.is-active').getAttribute('data-i'))].t0; })()`);
  t.check('review: row click seeks Premiere to the gap', seeks.length > 0 && Math.abs(seeks[seeks.length - 1][0] - t1) < 1e-6, JSON.stringify(seeks.slice(-1)));
  await ev(`(document.querySelector('[data-screen="tool-silence"] .rv.is-active .ck').click(), true)`);    // keep one gap
  t.check('review: toggle updates the dock label', /Potong \d+ jeda/.test(await t.text('#primary')) && (await ev(`${rt}.review.count()`)) === d0.live - 1);
  if (t.width === 380) await t.shot('build_silence_review');
  await t.click('#primary');
  await t.waitFor(`location.hash === '#tool/silence/result'`, 30000, 'result pane');
  await t.wait(100);
  const res = await ev(`(function(){ var r=document.querySelector('[data-screen="tool-silence"] [data-pane="result"]'); return {h: r.querySelector('h2').textContent, stats: r.querySelector('.stats') ? r.querySelector('.stats').textContent : '', btns: r.textContent}; })()`);
  const rr = (await t.calls('bac_removeRanges')).slice(-1)[0];
  t.check('apply remove: clone + extract of the checked gaps', rr && rr[1].length === d0.live - 1, rr ? String(rr[1].length) : 'no call');
  t.check('result: before/after + Hemat %', res.h === 'Sequence baru siap' && /Sebelum0:49/.test(res.stats.replace(/\s+/g, '')) && /Hemat\d+%/.test(res.stats.replace(/\s+/g, '')), res.stats);
  t.check('result: Buka yang asli + Hapus hasil', /Buka yang asli/.test(res.btns) && /Hapus hasil/.test(res.btns));
  if (t.width === 380) await t.shot('build_silence_result');

  // ---- markers mode without review
  await t.go('tool/silence');
  await ev(`(function(){ var s=document.querySelector('[data-screen="tool-silence"]'); s.querySelectorAll('.opts input')[1].click(); var sw=s.querySelectorAll('.sw input'); sw[sw.length-1].click(); return true; })()`);
  t.check('dock label follows mode (Tandai N jeda)', (await t.text('#primary')).indexOf('Tandai ' + d0.live + ' jeda') === 0, await t.text('#primary'));
  await ev(`(__acStub.markers = [{t: 1, name: 'lama', comment: 'x', tag: '[Klipora-SIL]'}, {t: 2, name: 'punya user', comment: ''}], true)`);
  await t.click('#primary');
  await t.waitFor(`location.hash === '#tool/silence/result'`, 30000, 'markers result');
  await t.wait(100);
  const mk = await ev(`__acStub.markers.map(function(m){return [m.tag||'', m.name, m.end > m.t]})`);
  t.check('markers: old [Klipora-SIL] markers replaced, user marker kept, one per gap', mk.length === d0.live + 1 && mk.filter((m) => m[0] === '[Klipora-SIL]').length === d0.live && mk.some((m) => m[1] === 'punya user') && !mk.some((m) => m[1] === 'lama'), JSON.stringify(mk.slice(0, 3)));
  t.check('markers result card', /marker jeda dibuat/.test(await t.text('[data-screen="tool-silence"] [data-pane="result"] h2')) && /Kalau dipotong/.test(await t.text('[data-screen="tool-silence"] [data-pane="result"] .sum-strip')));

  // ---- mute mode (host stub for bac_silence_mute)
  await t.host('bac_silence_mute', `function (id, ranges, tracks, from, to) { return JSON.stringify({ok: true, keys: (to - from) * 3, clips: 1, ranges: to - from, skipped: []}); }`);
  await t.go('tool/silence');
  await ev(`(document.querySelector('[data-screen="tool-silence"] .opts input[value="mute"]').click(), true)`);
  await t.click('#primary');
  await t.waitFor(`location.hash === '#tool/silence/result'`, 30000, 'mute result');
  await t.wait(100);
  const mc = await t.calls('bac_silence_mute');
  const mt = await t.text('[data-screen="tool-silence"] [data-pane="result"]');
  t.check('mute: clone + chunked bac_silence_mute + open clone', mc.length >= 1 && mc[0][2].join() === '0' && mc[mc.length - 1][4] === d0.live && /made-/.test(mc[0][0]), JSON.stringify(mc.map((c) => [c[0], c[3], c[4]])));
  t.check('mute result card: duration unchanged', /Sequence baru siap/.test(mt) && /Durasi0:49/.test(mt.replace(/\s+/g, '')) && /disenyapkan/.test(mt), mt.slice(0, 200));
  await t.go('tool/silence');
  const lockedSeq = `(function(){ var s=__acStub.fixtures.raw49(); s.audio[0].locked=true; return s; })()`;
  await ev(`(__acStub.setSeq(${lockedSeq}), true)`); await t.fire();
  await t.waitFor(`${pv}.data && ${pv}.live && document.querySelector('#primary').textContent.indexOf('Senyapkan') === 0`, 30000, 'locked seq preview');
  const clones0 = (await t.calls('bac_cloneSeq')).length;
  await t.click('#primary');
  await t.waitFor(`location.hash === '#tool/silence/error'`, 30000, 'locked error pane');
  t.check('mute: every chosen track locked -> "Track terkunci" error, no clone made', /Track terkunci/.test(await t.text('[data-screen="tool-silence"] [data-pane="error"]')) &&
    (await t.calls('bac_cloneSeq')).length === clones0, await t.text('[data-screen="tool-silence"] [data-pane="error"]'));
  await t.setSeq('raw49'); await t.fire();
  await t.go('tool/silence');
  await t.waitFor(`${pv}.data && ${pv}.data.seq.id === 'seq-raw49' && ${pv}.live`, 30000, 'raw49 again');
  await ev(`(function(){ var s=document.querySelector('[data-screen="tool-silence"]'); s.querySelectorAll('.opts input')[0].click(); var sw=s.querySelectorAll('.sw input'); sw[sw.length-1].click(); return true; })()`);
  await t.go('tool/silence');

  // ---- layout at the harness width (run again with --width 280 / 660)
  await ev(`(document.querySelector('[data-screen="tool-silence"] details').open = true, true)`);
  await t.wait(120);
  const ov = await ev(`(function(){ var v=document.querySelector('#view'), b=document.querySelector('[data-screen="tool-silence"] .tool-body'); return Math.max(v.scrollWidth - v.clientWidth, b.scrollWidth - b.clientWidth); })()`);
  t.check(`layout: no horizontal overflow at ${t.width} px`, ov <= 1, String(ov));
  await ev(`(document.querySelector('[data-screen="tool-silence"] details').scrollIntoView(), true)`);
  await t.wait(80);
  await t.shot(t.width === 380 ? 'build_silence_advanced' : 'build_silence_advanced_' + t.width);
  await ev(`(document.querySelector('#view').scrollTop = 0, document.querySelector('[data-screen="tool-silence"] details').open = false, true)`);

  // ---- error path: every audio track muted -> preview says why, analyze shows the error pane
  await ev(`(__acStub.setSeq((function(){ var s=__acStub.fixtures.raw49(); s.id='seq-muted'; s.audio[0].muted=true; return s; })()), true)`);
  await t.fire();
  await t.waitFor(`${pv}.err && /Grafik belum bisa dibuat/.test(document.querySelector('[data-screen="tool-silence"] .wave-foot').textContent)`, 30000, 'muted preview error');
  t.check('no audible track: legend explains + primary still runs', /di-mute \(Audio 1\)/.test(await t.text('[data-screen="tool-silence"] .wave-foot')) && (await t.text('#primary')).indexOf('Deteksi jeda') === 0, await t.text('[data-screen="tool-silence"] .wave-foot'));
  await t.click('#primary');
  await t.waitFor(`location.hash === '#tool/silence/error'`, 30000, 'error pane');
  t.check('no audible track: error pane "Tidak ada audio"', /Tidak ada audio/.test(await t.text('[data-screen="tool-silence"] [data-pane="error"]')));
  await t.go('tool/silence');

  // ---- AutoCut sequence with 15 clips, then the 35 min file: parity + preview time
  await t.setSeq('cut15'); await t.fire();
  await t.waitFor(`${pv}.data && ${pv}.data.seq.id === 'seq-cut15' && ${pv}.live`, 30000, 'cut15 preview');
  const c15 = await ev(`({check: ${pv}.check, live: ${pv}.live.cuts.length, base: ${pv}.data.base})`);
  t.check('cut15: auto threshold from the source media (-53 dB) + parity', c15.base === -53 && c15.check.ok, JSON.stringify(c15));
  await cmp('cut15');
  await t.setSeq('long35'); await t.fire();
  const t0 = Date.now();
  await t.waitFor(`${pv}.data && ${pv}.data.seq.id === 'seq-long35' && ${pv}.live`, 60000, 'long35 preview');
  const l35 = await ev(`({check: ${pv}.check, live: ${pv}.live.cuts.length, n: ${pv}.data.n})`);
  t.check('long35: 34,6 mnt preview + parity', l35.check.ok && l35.live > 300 && l35.n > 200000, JSON.stringify(l35) + ' ' + (Date.now() - t0) + ' ms');
  const tl = await ev(`(function(){ var p=${pv}, s=document.querySelectorAll('[data-screen="tool-silence"] .tool-body input.range')[1], t0=performance.now(); p.maskKey=''; s.value='0.4'; s.dispatchEvent(new Event('input')); return performance.now() - t0; })()`);
  t.check('long35: full live recompute (gate + guard + plan, 207k frames) < 150 ms', tl < 150, tl.toFixed(1) + ' ms');
  await ev(`(document.querySelector('[data-screen="tool-silence"] .tool-body .seg button:nth-child(2)').click(), true)`);
  await t.setSeq('raw49'); await t.fire();
  await t.waitFor(`${pv}.data && ${pv}.data.seq.id === 'seq-raw49'`, 30000, 'back to raw49');
}
