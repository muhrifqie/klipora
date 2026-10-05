// viral.mjs: Viral Clips end to end in headless Chrome (fake CEP stub, REAL engine by default).
//   node tools/ui_test.mjs tools/ui_tests/viral.mjs --seq long35            (AI off: rules, no Grok quota)
//   AC_VIRAL_AI=1 node tools/ui_test.mjs tools/ui_tests/viral.mjs --seq long35   (AI on: uses the AI cache when warm)
// The stub does not execute host/*.jsx, so the bac_viral_* host functions are replaced with fakes that record calls.
// Ends with an English pass (AC.i18n.set('en')): setup, review and result without Indonesian leftovers.
export default async function (t) {
  const useAi = process.env.AC_VIRAL_AI === '1';
  const shots = process.env.AC_VIRAL_SHOTS !== '0';
  await t.host('bac_viral_subseq', `function (seqId, t0, t1, name, bin) {
    var id = 'sub-' + Math.round(t0 * 1000); __acStub.made[id] = { id: id, name: name, bin: bin };
    if (window.__vrCancelAt && ++window.__vrN === window.__vrCancelAt) AC.tools.runtime('viral').task.cancel();   // Esc mid-run
    return JSON.stringify({ ok: true, id: id, name: name, origId: seqId, origName: 'x', t0: t0, t1: t1, dur: t1 - t0, ms: 4 }); }`);
  await t.host('bac_viral_play', `function (t0, seqId) { if (__acStub.seq) __acStub.seq.player = Number(t0); return JSON.stringify({ ok: true, t: Number(t0), played: false, how: '' }); }`);
  await t.host('bac_viral_binInfo', `function (name) { return JSON.stringify({ ok: true, exists: false, n: 0 }); }`);
  await t.host('bac_viral_adopt', `function (id, bin, drop) { return JSON.stringify({ ok: true, moved: true, dropped: !!drop }); }`);
  await t.host('bac_viral_stop', `function () { return JSON.stringify({ ok: true, stopped: false, how: '' }); }`);
  await t.host('bac_viral_cleanup', `function (ids, bin) { var n = 0; for (var i = 0; i < ids.length; i++) if (__acStub.made[ids[i]]) { delete __acStub.made[ids[i]]; n++; }
    return JSON.stringify({ ok: true, deleted: n, refused: ids.length - n, bin: false }); }`);

  // ---------------------------------------------------------------- Atur
  await t.ev(`(localStorage.removeItem('ac.tool.viral'), true)`);
  await t.go('tool/viral');
  await t.waitFor(`document.querySelector('.screen[data-screen="tool-viral"] .tool-hero')`, 5000, 'viral page');
  const scr = '.screen[data-screen="tool-viral"]';
  t.check('viral: registered with the real render (not the placeholder)', !(await t.ev(`!!document.querySelector('${scr} .ph-box')`)));
  const dseg = `document.querySelector('${scr} .seg[aria-label="Durasi klip"]')`;
  t.check('viral: duration presets (4) with 20-60 dtk selected', (await t.ev(`${dseg}.querySelectorAll('button').length`)) === 4
    && /20-60/.test(await t.ev(`${dseg}.querySelector('[aria-checked="true"]').textContent`)));
  t.check('viral: 6 style chips, Default on', (await t.count(`${scr} .chips-wrap .chip`)) === 6
    && (await t.ev(`document.querySelector('${scr} .chips-wrap .chip[aria-pressed="true"]').textContent`)) === 'Default');
  t.check('viral: custom prompt hidden until Custom', await t.ev(`document.querySelector('${scr} .viral-prompt').hidden`));
  // 9:16: the resize tool + host/38_resize.jsx are loaded -> native Auto Reframe fallback (bac_resize_native)
  const hasResizeHost = await t.ev(`AC.host.files.some(function (f) { return /resize\.jsx$/i.test(f); })`);
  t.check('viral: 9:16 switch follows the resize tool (native fallback when its host file is loaded)',
    (await t.ev(`document.querySelector('${scr} .viral-vert').hidden`)) === !hasResizeHost, 'resize host ' + hasResizeHost);
  if (hasResizeHost) {
    await t.host('bac_resize_native', `function (num, den, preset, name, seqId) { return JSON.stringify({ ok: true, id: seqId + '-rf', name: name, w: 540, h: 960 }); }`);
    const nat = await t.ev(`AC.viral.verticalApi().vertical({ seqId: 'sub-1', name: 'Viral 01 - X 9x16', ratio: '9:16' }).then(function (r) { return r; })`);
    const nc = await t.calls('bac_resize_native');
    t.check('viral: native 9:16 calls bac_resize_native(9, 16, "default", name, seqId)', nat && nat.id === 'sub-1-rf' && nc.length === 1 && nc[0][0] === 9 && nc[0][1] === 16 && nc[0][2] === 'default' && nc[0][4] === 'sub-1', JSON.stringify(nc));
    t.check('viral: native note shown', !(await t.ev(`document.querySelector('${scr} .viral-vert-note').hidden`)));
  }
  t.check('viral: estimate for 34:36 says about 10 clips', /kira-kira 10 klip/.test(await t.text(`${scr} .viral-est`)), await t.text(`${scr} .viral-est`));
  t.check('viral: dock primary', /Cari klip/.test(await t.text('#primary')), await t.text('#primary'));

  // Custom without text disables the primary; back to Default enables it
  await t.ev(`(Array.prototype.filter.call(document.querySelectorAll('${scr} .chips-wrap .chip'), function (b) { return b.textContent === 'Custom'; })[0].click(), true)`);
  t.check('viral: Custom shows the prompt and disables the run button', !(await t.ev(`document.querySelector('${scr} .viral-prompt').hidden`)) && (await t.ev(`document.getElementById('primary').disabled`)));
  await t.type(`${scr} .viral-prompt`, 'momen saya kaget');
  t.check('viral: typing a custom prompt enables the run button', !(await t.ev(`document.getElementById('primary').disabled`)));
  await t.ev(`(Array.prototype.filter.call(document.querySelectorAll('${scr} .chips-wrap .chip'), function (b) { return b.textContent === 'Default'; })[0].click(), true)`);
  // resize api appears later -> the 9:16 switch shows up (AC.bus 'tools')
  await t.ev(`(AC.tools.get('resize').api = { vertical: function (o) { window.__vert = (window.__vert || []).concat([o]); return Promise.resolve({ id: o.seqId + '-v', name: o.name }); } }, AC.bus.emit('tools'), true)`);
  t.check('viral: 9:16 switch shown when the resize tool exposes api.vertical (preferred, note hidden)', !(await t.ev(`document.querySelector('${scr} .viral-vert').hidden`))
    && (await t.ev(`document.querySelector('${scr} .viral-vert-note').hidden`)));
  await t.ev(`(document.querySelector('${scr} .viral-vert input').click(), true)`);
  if (!useAi) await t.ev(`(function(){ var sw = Array.prototype.filter.call(document.querySelectorAll('${scr} .sw'), function (l) { return /Pakai AI/.test(l.textContent); })[0]; sw.querySelector('input').click(); return true; })()`);
  t.check('viral: AI off relabels the run button', useAi || /tanpa AI/.test(await t.text('#primary')), await t.text('#primary'));
  if (shots) await t.shot('build_viral_setup');

  // ---------------------------------------------------------------- analyze -> Tinjau
  await t.click('#primary');
  await t.waitFor(`location.hash === '#tool/viral/review'`, useAi ? 240000 : 60000, 'viral review');
  await t.wait(250);
  const n = await t.ev(`AC.tools.runtime('viral').review.items().length`);
  const on = await t.ev(`AC.tools.runtime('viral').review.count()`);
  t.check('viral review: clips found, 10 selected', n >= 10 && on === 10, n + ' / ' + on);
  t.check('viral review: list has the viral class and rows', (await t.count('.viral-rv .rv')) > 3);
  t.check('viral review: score badges + titles + snippets', (await t.count('.viral-rv .rv .viral-score')) > 3 && (await t.count('.viral-rv .viral-ttl')) > 3 && (await t.count('.viral-rv .viral-snip')) > 3);
  t.check('viral review: source tag', /Dinilai AI|Tanpa AI|Sebagian/.test(await t.text('.viral-bar')), await t.text('.viral-bar'));
  if (!useAi) t.check('viral review: rows tagged "tanpa AI"', (await t.count('.viral-rv .rv .tag-warn')) > 0);
  const scores = await t.ev(`AC.tools.runtime('viral').review.items().map(function (i) { return i.score; })`);
  t.check('viral review: sorted by score', scores.every((s, i) => !i || scores[i - 1] >= s), JSON.stringify(scores));
  t.check('viral review: "Hemat" legend hidden', await t.ev(`(function(){ var s = document.querySelectorAll('.viral-rv .rv-head .legend span')[2]; return !s || getComputedStyle(s).display === 'none'; })()`));
  t.check('viral review: dock says "Buat 10 klip"', /Buat 10 klip/.test(await t.text('#primary')), await t.text('#primary'));
  // rows do not overlap (fixed-height virtual rows with 2-line content)
  const overlap = await t.ev(`(function(){ var r = Array.prototype.map.call(document.querySelectorAll('.viral-rv .rv'), function (e) { var b = e.getBoundingClientRect(); return [b.top, b.bottom]; });
    r.sort(function (a, b) { return a[0] - b[0]; }); for (var i = 1; i < r.length; i++) if (r[i][0] < r[i - 1][1] - 1) return true; return false; })()`);
  t.check('viral review: rows do not overlap', !overlap);
  t.check('viral review: no horizontal overflow', await t.ev(`document.querySelector('.viral-rv').scrollWidth <= document.querySelector('.viral-rv').clientWidth + 1`));

  // click = seek; Putar = bac_viral_play + toast fallback
  await t.ev(`(document.querySelector('.viral-rv .rv[data-i="0"]').click(), true)`);
  await t.wait(250);
  const t0 = await t.ev(`AC.tools.runtime('viral').review.items()[0].t0`);
  const seeks = await t.calls('bac_seek');
  t.check('viral review: row click seeks to the clip start', seeks.length > 0 && Math.abs(seeks[seeks.length - 1][0] - t0) < 1e-6, JSON.stringify(seeks.slice(-1)));
  await t.ev(`(Array.prototype.filter.call(document.querySelectorAll('.viral-rv .rv.is-active .rv-actions button'), function (b) { return /Putar/.test(b.textContent); })[0].click(), true)`);
  await t.wait(150);
  t.check('viral review: Putar calls bac_viral_play with the analysed sequence', (await t.calls('bac_viral_play')).length === 1);
  t.check('viral review: Putar fallback toast', /Tekan Spasi/.test(await t.text('#toasts')), await t.text('#toasts'));
  t.check('viral review: row toggle says "Lewati klip ini" for a selected clip', /Lewati klip ini/.test(await t.text('.viral-rv .rv.is-active .rv-actions')), await t.text('.viral-rv .rv.is-active .rv-actions'));
  if (shots) await t.shot('build_viral');

  // sort by time, then back by score
  await t.ev(`(document.querySelectorAll('.viral-bar .seg button')[1].click(), true)`);
  const t0s = await t.ev(`AC.tools.runtime('viral').review.items().map(function (i) { return i.t0; })`);
  t.check('viral review: sort by time', t0s.every((s, i) => !i || t0s[i - 1] <= s));
  await t.ev(`(document.querySelectorAll('.viral-bar .seg button')[0].click(), true)`);
  // untick the 2nd clip -> 9 selected
  await t.ev(`(AC.tools.runtime('viral').review.toggle(1, false), true)`);
  await t.wait(50);
  t.check('viral review: toggle updates the dock', /Buat 9 klip/.test(await t.text('#primary')), await t.text('#primary'));

  // markers only (bulk action) -> engine apply -> [Klipora-VR] markers in the stub
  await t.ev(`(Array.prototype.filter.call(document.querySelectorAll('.viral-rv .rv-bulk .linkbtn'), function (b) { return /Tandai/.test(b.textContent); })[0].click(), true)`);
  await t.waitFor(`__acStub.markers.length === 9`, 30000, 'markers only');
  await t.waitFor(`location.hash === '#tool/viral/review'`, 5000, 'back to review after markers');
  const mk = await t.ev(`__acStub.markers.map(function (m) { return [m.tag, m.color, m.name]; })`);
  t.check('viral markers-only: 9 markers tagged [Klipora-VR], colour by score', mk.every((m) => m[0] === '[Klipora-VR]' && [0, 3, 4].indexOf(m[1]) >= 0 && /^Viral \d\d \(\d+\) /.test(m[2])), JSON.stringify(mk.slice(0, 2)));

  // ---------------------------------------------------------------- Terapkan: sub-sequences + markers + 9:16
  await t.click('#primary');
  await t.waitFor(`location.hash === '#tool/viral/result'`, 60000, 'viral result');
  const sub = await t.calls('bac_viral_subseq');
  t.check('viral apply: 9 sub-sequences, best first, named "Viral 01 - ..."', sub.length === 9 && /^Viral 01 - /.test(sub[0][3]) && /^Viral 09 - /.test(sub[8][3]) && sub[0][4] === 'Klipora Viral', JSON.stringify(sub[0]));
  t.check('viral apply: sub-sequences target the analysed sequence id', sub.every((c) => c[0] === 'seq-long35'));
  const calls = await t.ev(`__acStub.calls.map(function (c) { return c.fn; }).filter(function (f) { return /bac_(viral_subseq|clearMarkersByTag|addMarkers)/.test(f); })`);
  const firstSub = calls.indexOf('bac_viral_subseq'), lastClear = calls.lastIndexOf('bac_clearMarkersByTag'), lastAdd = calls.lastIndexOf('bac_addMarkers');
  t.check('viral apply: old markers cleared before, new markers added after the sub-sequences', lastClear < firstSub && lastAdd > firstSub, calls.join(','));
  t.check('viral apply: 9 markers on the source (re-run replaces)', (await t.ev('__acStub.markers.length')) === 9);
  t.check('viral apply: 9:16 through the resize api for each clip', (await t.ev('(window.__vert || []).length')) === 9 && (await t.ev(`window.__vert[0].ratio`)) === '9:16');
  const ad = await t.calls('bac_viral_adopt');
  t.check('viral apply: each 9:16 sequence moved into the viral bin, new reframe bin dropped', ad.length === 9 && ad[0][1] === 'Klipora Viral' && ad[0][2] === 'Auto Reframed Sequences', JSON.stringify(ad[0]));
  const title = await t.text('.screen[data-screen="tool-viral"] [data-pane="result"] .result-h h2');
  t.check('viral result: title', /9 klip viral siap/.test(title), title);
  t.check('viral result: list of created sequences with Buka buttons', (await t.count('.viral-made-i:not(.is-vert) .btn')) === 9 && (await t.count('.viral-made-i.is-vert')) === 9
    && (await t.ev(`document.querySelector('.viral-made-i').nextElementSibling.classList.contains('is-vert')`)));
  t.check('viral result: Buka yang asli + Hapus hasil', /Buka yang asli/.test(await t.text('[data-pane="result"] .btn-row')) && /Hapus hasil/.test(await t.text('[data-pane="result"] .btn-row')));
  await t.ev(`(document.querySelector('.viral-made-i .btn').click(), true)`);
  await t.wait(100);
  const opened = await t.calls('bac_openSequence');
  t.check('viral result: Buka opens the clip sequence', opened.length && /^sub-/.test(opened[opened.length - 1][0]), JSON.stringify(opened.slice(-1)));
  const hist = await t.ev(`AC.history.list().filter(function (h) { return h.tool === 'viral'; }).map(function (h) { return h.sub || ''; })`);
  t.check('viral: Riwayat has the analyse and the clip job', hist.some((s) => /klip, 10 dipilih/.test(s)) && hist.some((s) => /9 klip viral dibuat/.test(s)), JSON.stringify(hist));
  if (shots) await t.shot('build_viral_result');

  // Hapus hasil (two clicks) -> cleanup + markers cleared
  const del = `Array.prototype.filter.call(document.querySelectorAll('[data-pane="result"] .btn-row .btn'), function (b) { return /Hapus|Yakin/.test(b.textContent); })[0]`;
  await t.ev(`(${del}.click(), true)`);
  await t.ev(`(${del}.click(), true)`);
  await t.waitFor(`location.hash === '#tool/viral/review'`, 5000, 'back to review after delete');
  const cl = await t.calls('bac_viral_cleanup');
  t.check('viral delete: cleanup gets the 9 clip ids + 9 vertical ids', cl.length === 1 && cl[0][0].length === 18 && cl[0][1] === 'Klipora Viral', JSON.stringify(cl));
  t.check('viral delete: markers removed', (await t.ev('__acStub.markers.length')) === 0);
  // back on Atur: "Lihat hasil terakhir" reopens the saved review
  await t.go('tool/viral');
  await t.wait(150);
  t.check('viral: "Lihat hasil terakhir" link after a run', /Lihat hasil terakhir \(\d+ klip/.test((await t.text('.viral-resume')) || ''), await t.text('.viral-resume'));
  await t.click('.viral-resume .linkbtn');
  await t.waitFor(`location.hash === '#tool/viral/review'`, 5000, 'resume review');
  t.check('viral: resumed review keeps the manual toggle (9 selected)', (await t.ev(`AC.tools.runtime('viral').review.count()`)) === 9);

  // Esc after the 3rd clip: partial result card (Buka, Hapus hasil) instead of orphaned sequences
  await t.ev(`(window.__vrCancelAt = 3, window.__vrN = 0, true)`);
  await t.click('#primary');
  await t.waitFor(`location.hash === '#tool/viral/result'`, 60000, 'partial result');
  {
    const ptitle = await t.text('.screen[data-screen="tool-viral"] [data-pane="result"] .result-h h2');
    t.check('viral cancel: partial result "3 dari 9 klip dibuat" with Buka per clip', /3 dari 9 klip dibuat/.test(ptitle) && (await t.count('.viral-made-i:not(.is-vert) .btn')) === 3, ptitle);
    t.check('viral cancel: warning + toast say what exists', /Dibatalkan/.test(await t.text('[data-pane="result"] .alert')) && /3 sequence klip sudah dibuat/.test(await t.text('#toasts')), await t.text('#toasts'));
    t.check('viral cancel: no markers added for the partial run', (await t.ev('__acStub.markers.length')) === 0);
  }
  await t.ev(`(window.__vrCancelAt = 0, true)`);
  t.check('viral: no errors logged by the panel', (await t.ev('AC.log.errors.length')) === 0, JSON.stringify(await t.ev('AC.log.errors')));

  // ---------------------------------------------------------------- English UI (live switch, engine job in English)
  await t.ev(`AC.i18n.set('en')`);
  await t.go('tool/viral');
  await t.wait(200);
  let left = await t.idLeftovers('#app');
  t.check('viral en setup: no Indonesian leftovers', left.length === 0, JSON.stringify(left));
  t.check('viral en setup: labels', (await t.ev(`document.querySelector('${scr} .seg[aria-label="Clip length"] [aria-checked="true"]').textContent`)) === '20-60 s'
    && (await t.text('#primary .lbl')) === 'Find clips (no AI)' && /^For 34:36: about \d+ clips of 20-60 s are selected/.test(await t.text(`${scr} .viral-est`)), await t.text(`${scr} .viral-est`));
  await t.click('#primary');
  await t.waitFor(`location.hash === '#tool/viral/review'`, 60000, 'viral review (en)');
  await t.wait(250);
  await t.ev(`(document.querySelector('.viral-rv .rv[data-i="0"]').click(), true)`);   // active row: its actions show
  await t.wait(250);
  left = await t.idLeftovers('#app');
  t.check('viral en review: no Indonesian leftovers (titles and snippets are content)', left.length === 0, JSON.stringify(left));
  t.check('viral en review: labels', /^Make \d+ clips$/.test(await t.text('#primary .lbl')) && /No AI/.test(await t.text('.viral-bar')) && /Skip this clip/.test(await t.text('.viral-rv .rv.is-active .rv-actions')),
    [await t.text('#primary .lbl'), await t.text('.viral-bar')].join(' | '));
  await t.click('#primary');
  await t.waitFor(`location.hash === '#tool/viral/result'`, 60000, 'viral result (en)');
  await t.wait(150);
  left = await t.idLeftovers('#app');
  t.check('viral en result: no Indonesian leftovers', left.length === 0, JSON.stringify(left));
  t.check('viral en result: title', /^\d+ viral clips ready$/.test(await t.text('.screen[data-screen="tool-viral"] [data-pane="result"] .result-h h2')), await t.text('.screen[data-screen="tool-viral"] [data-pane="result"] .result-h h2'));
  await t.ev(`AC.i18n.set('id')`);
  await t.go('tool/viral');
  await t.wait(100);
  t.check('viral: back to id', /Cari klip/.test(await t.text('#primary')), await t.text('#primary'));
}
