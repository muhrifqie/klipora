// profanity.mjs: Sensor Kata Kasar / Bleep Profanity in headless Chrome + the host jsx against a mocked Premiere DOM (node vm).
//   node tools/ui_test.mjs tools/ui_tests/profanity.mjs [--engine live|mock]
// Live engine: real engine/cli.py + worker.py on the 34,6 min test clip (cached transcripts; the only swear word is
// "anjir" at 22:41, heard only by the "dengar ulang" pass, tier Ringan -> needs Ketat). Host bac_profanity_* are stubbed
// in the page (recording calls); their real ES3 code is exercised separately in section H with a fake Premiere.
// Section I switches the UI to English (main pane + review pane: no Indonesian left, exact English labels).
import fs from 'fs';
import path from 'path';
import vm from 'vm';

const MEDIA35 = globalThis.KLIPORA_MEDIA.talk_35m.replace(/\//g, '\\');   // KLIPORA_TEST_MEDIA/talk_35m.mp4
const sel = (s) => `[data-screen="tool-profanity"] ${s}`;

// In-page mock engine (--engine mock): writes a review with the same shape as engine/ac/tools/profanity.py.
const MOCK = `function (job, emit, done) {
  var fs = __acStub.fs, a = job.action, p = job.params || {};
  if (a === 'analyze') {
    ['words', 'listen', 'scan', 'edges', 'review'].forEach(function (id, i) { emit({ ev: 'stage', id: id, i: i, n: 5 }); emit({ ev: 'stage_done', id: id, sec: 0.01 }); });
    var items = p.level === 'ketat' ? [{ id: 'w136100', t0: 1361.0, t1: 1361.38, kind: 'word', on: true, conf: 0.7, label: 'anjir.', ctx: { pre: 'Oh, mbak', post: 'Tari-tari.' },
      note: { type: 'warn', text: 'Hanya terdengar saat dengar ulang, transkrip utama menulis lain. Dengar dulu.' }, src: 'listen', word: 'anjir.', lemma: 'anjir', tier: 1,
      mask: 'a***r.', w0: 1360.95, w1: 1361.31, words: [], texts: [], media: ${JSON.stringify(MEDIA35)}, track: 0, ai: null, ms0: 1361.0, ms1: 1361.38, lo: 0, hi: 2076.742 }] : [];
    var doc = { v: 1, tool: 'profanity', timebase: 'sequence', duration: job.seq.duration, seq: { id: job.seq.id, name: job.seq.name }, params: p, items: items,
      stats: { words: 2803, below: p.level === 'ketat' ? 0 : 1, n: items.length, on: items.length }, level: p.level, speech_db: -22.3, sensor_track: false, dialog_tracks: [0] };
    var f = job.workdir + '\\\\profanity_review.json';
    fs.writeFileSync(f, JSON.stringify(doc));
    emit({ ev: 'result', data: { review: f, summary: items.length + ' kata ditemukan', stats: doc.stats, n: items.length, on: items.length, level: p.level } }); done(0); return;
  }
  if (a === 'apply') {
    emit({ ev: 'stage', id: 'plan' }); emit({ ev: 'stage_done', id: 'plan' });
    var tone = 'C:\\\\BOT\\\\AutoCut\\\\engine\\\\ac\\\\assets\\\\sfx\\\\beep_1000hz_26_3db_380ms.wav';
    emit({ ev: 'result', data: { plan: { kind: 'censor', mode: p.mode, seq: { id: 'seq-long35', name: 'Tutorial - Full Session' }, track_name: 'Klipora Sensor', tag: '[Klipora-PF]', ramp: 0.01,
      duck_db: -20, tone_db: -26.3, freq: 1000, name: 'Tutorial - Full Session (Klipora)', ranges: [{ t0: 1361.0, t1: 1361.38, paths: [${JSON.stringify(MEDIA35)}], ids: ['w136100'], masks: ['a***r.'] }],
      tones: p.mode === 'beep' ? [{ t: 1361.0, dur: 0.38, path: tone }] : [], markers: [{ t: 1361.0, end: 1361.38, name: 'Sensor', tag: '[Klipora-PF]', color: 1, comment: 'a***r. (Bip)' }] }, summary: '1 kata disensor' } });
    done(0); return;
  }
  if (a === 'preview') { emit({ ev: 'result', data: { path: __acStub.pfWav, dur: 2.78, a: 1.2, b: 1.58 } }); done(0); return; }
  if (a === 'library') { emit({ ev: 'result', data: { library: {}, builtin: { '3': ['kontol'], '2': ['goblok'], '1': ['anjir'], '0': ['anjing'], literal: ['tahi lalat'] } } }); done(0); return; }
  emit({ ev: 'error', code: 'NO_ACTION', msg: a }); done(1);
}`;

// Recording host stubs for the bac_profanity_* functions (the real ones run in Premiere).
const HOST = {
  bac_profanity_begin: `function (srcId, name, trackName, opts) { var s = __acStub.seq; var c = JSON.parse(JSON.stringify(s)); c.id = 'made-pf' + Object.keys(__acStub.made).length; c.name = name;
    var track = trackName && (!opts || opts.track !== false) ? trackName : '';
    if (track) c.audio.push({ index: c.audio.length, name: track, clips: [] }); __acStub.made[c.id] = c;
    return JSON.stringify({ ok: true, id: c.id, name: c.name, origId: s.id, origName: s.name, track: track ? c.audio.length - 1 : -1, named: !!track, trackName: track || '' }); }`,
  bac_profanity_keys: `function (id, ranges, opts) { if (!__acStub.made[id]) return 'ERR:Sequence hasil tidak ditemukan.';
    if (__acStub.failKeys) return 'ERR:keyframe gagal (tes)'; return JSON.stringify({ ok: true, keys: ranges.length * 4, clips: ranges.length, missed: [], locked: [], ms: 3 }); }`,
  bac_profanity_tones: `function (id, track, tones) { return JSON.stringify({ ok: true, placed: tones.length, missing: [], ms: 2 }); }`
};

export default async function (t) {
  const live = t.engine === 'live';
  if (!live) {
    const dir = path.join(t.root, 'engine', 'ac', 'assets', 'sfx');            // any real WAV: the page must load it
    const w = fs.existsSync(dir) ? fs.readdirSync(dir).find((f) => f.endsWith('.wav')) : null;
    await t.ev(`(__acStub.pfWav = ${JSON.stringify(w ? path.join(dir, w) : '')}, __acStub.engineMock.profanity = ${MOCK}, true)`);
  }
  for (const [fn, src] of Object.entries(HOST)) await t.host(fn, src);
  await t.setSeq('long35');
  await t.wait(300);

  /* ---------------- A. main pane */
  await t.go('tool/profanity');
  await t.waitFor(`document.querySelector('${sel('.tool-hero')}')`, 5000, 'profanity page');
  t.check('profanity: level presets Longgar/Normal/Ketat', (await t.ev(`Array.from(document.querySelector('${sel('[aria-label="Tingkat sensor"]')}').querySelectorAll('button')).map(b => b.textContent).join('|')`)) === 'Longgar|Normal|Ketat');
  t.check('profanity: 4 sensor modes, Bip default', (await t.count(sel('.opts .opt'))) === 4 && (await t.ev(`document.querySelector('${sel('.opt.is-on b')}').textContent`)) === 'Bip');
  t.check('profanity: primary "Cari kata kasar"', (await t.text('#primary .lbl')) === 'Cari kata kasar');
  t.check('profanity: two word lists', (await t.count(sel('.profanity-list'))) === 2);
  await t.shot('build_profanity');

  /* ---------------- B. word library editor (writes %APPDATA%\Klipora\profanity.json in the sandbox) */
  const LIB = 'home/AppData/Roaming/Klipora/profanity.json';
  const lib = () => JSON.parse(t.readSandbox(LIB));
  await t.type(sel('.profanity-list[data-kind="block"] input'), '  Dasar   KAMPRET ');
  await t.click(sel('.profanity-list[data-kind="block"] .btn'));
  await t.waitFor(`document.querySelectorAll('${sel('.profanity-list[data-kind="block"] .profanity-tok')}').length === 1`, 3000, 'block token');
  t.check('profanity: block word saved normalized', JSON.stringify(lib().block) === '["dasar kampret"]', t.readSandbox(LIB));
  await t.type(sel('.profanity-list[data-kind="allow"] input'), 'TokoKita');
  await t.key('Enter', {}, sel('.profanity-list[data-kind="allow"] input'));
  await t.waitFor(`document.querySelectorAll('${sel('.profanity-list[data-kind="allow"] .profanity-tok')}').length === 1`, 3000, 'allow token');
  await t.type(sel('.profanity-list[data-kind="allow"] input'), 'dasar kampret');
  await t.click(sel('.profanity-list[data-kind="allow"] .btn'));
  await t.wait(100);
  let l = lib();
  t.check('profanity: adding to allow moves it out of block', l.block.length === 0 && l.allow.join(',') === 'tokokita,dasar kampret', JSON.stringify(l));
  await t.click(sel('.profanity-x[data-w="dasar kampret"]'));
  await t.wait(100);
  t.check('profanity: remove word', lib().allow.join(',') === 'tokokita');
  // captions switch -> library captionStyle
  await t.ev(`(function(){ var sw = Array.from(document.querySelectorAll('${sel('.sw')}')).filter(s => /Sensor juga caption/.test(s.textContent))[0].querySelector('input'); sw.click(); return true; })()`);
  await t.wait(80);
  t.check('profanity: caption switch off -> captionStyle off', lib().captionStyle === 'off');
  await t.ev(`(function(){ var sw = Array.from(document.querySelectorAll('${sel('.sw')}')).filter(s => /Sensor juga caption/.test(s.textContent))[0].querySelector('input'); sw.click(); return true; })()`);
  await t.wait(80);
  t.check('profanity: caption switch on -> stars', lib().captionStyle === 'stars');

  // built-in lexicon (engine "library" action through the worker), shown on demand
  await t.ev(`(function(){ var d = document.querySelector('${sel('.profanity-builtin')}'); d.open = true; d.dispatchEvent(new Event('toggle')); return true; })()`);
  await t.waitFor(`/Kasar/.test(document.querySelector('${sel('.profanity-builtin-body')}').textContent)`, 30000, 'built-in list');
  t.check('profanity: built-in words listed by tier', /kontol/.test(await t.text(sel('.profanity-builtin-body'))) && /anjing/.test(await t.text(sel('.profanity-builtin-body'))));
  await t.ev(`document.querySelector('${sel('.profanity-list')}').scrollIntoView()`);
  await t.wait(100);
  await t.shot('build_profanity_lists');
  await t.ev(`document.querySelector('${sel('.profanity-builtin')}').open = false`);

  /* ---------------- C. custom sound mode disables the run until a file exists */
  await t.ev(`document.querySelectorAll('${sel('.opts .opt input')}')[3].click()`);
  await t.wait(50);
  t.check('profanity: custom mode shows file field + disables run', !(await t.ev(`document.querySelector('${sel('.profanity-custom')}').hidden`)) && (await t.ev(`document.getElementById('primary').disabled`)));
  const wav = path.join(t.root, 'engine', 'ac', 'assets', 'sfx');
  const anyWav = fs.existsSync(wav) ? fs.readdirSync(wav).find((f) => f.endsWith('.wav')) : null;
  if (anyWav) {
    await t.type(sel('.profanity-custom input'), path.join(wav, anyWav));
    await t.wait(50);
    t.check('profanity: existing wav enables run', !(await t.ev(`document.getElementById('primary').disabled`)));
  }
  await t.ev(`document.querySelectorAll('${sel('.opts .opt input')}')[0].click()`);
  await t.wait(50);
  t.check('profanity: back to Bip', !(await t.ev(`document.getElementById('primary').disabled`)) && (await t.ev(`document.querySelector('${sel('.profanity-custom')}').hidden`)));

  /* ---------------- D. Normal finds nothing -> "Tidak ada kata kasar" with a Ketat hint */
  await t.ev(`(function(){ var sw = Array.from(document.querySelectorAll('${sel('.sw')}')).filter(s => /Cek arti ganda/.test(s.textContent))[0].querySelector('input'); if (sw.checked) sw.click(); return true; })()`);   // no AI quota in tests
  await t.click('#primary');
  await t.waitFor(`location.hash === '#tool/profanity/result'`, 60000, 'no-hit result');
  t.check('profanity: Normal -> no hits card', /Tidak ada kata kasar/.test(await t.text(sel('[data-pane="result"] .result-h h2'))));
  t.check('profanity: hint about Ketat', /Cari lagi di Ketat/.test(await t.text(sel('[data-pane="result"]'))));

  /* ---------------- E. Ketat -> review with "anjir" */
  await t.ev(`Array.from(document.querySelectorAll('${sel('[data-pane="result"] .btn')}')).filter(b => /Ketat/.test(b.textContent))[0].click()`);
  await t.waitFor(`location.hash === '#tool/profanity/review'`, 60000, 'review pane');
  await t.wait(200);
  t.check('profanity: 1 row', (await t.count(sel('.rv'))) === 1, String(await t.count(sel('.rv'))));
  t.check('profanity: row shows anjir + Ringan + Dengar ulang', /anjir/.test(await t.text(sel('.rv .rv-ctx mark'))) && /Ringan/.test(await t.text(sel('.rv .rv-l1'))) && /Dengar ulang/.test(await t.text(sel('.rv .rv-l1'))));
  t.check('profanity: primary "Bip 1 kata"', (await t.text('#primary .lbl')) === 'Bip 1 kata', await t.text('#primary .lbl'));
  t.check('profanity: legend says duration stays', /durasi video tetap/.test(await t.text(sel('.rv-head .legend'))));
  await t.click(sel('.rv'));
  await t.wait(150);
  const seekArgs = await t.calls('bac_seek');
  t.check('profanity: row click seeks 1 s before the word', seekArgs.length && Math.abs(seekArgs[seekArgs.length - 1][0] - 1360.0) < 0.05, JSON.stringify(seekArgs.slice(-1)));
  t.check('profanity: row actions', /Dengar/.test(await t.text(sel('.rv.is-active .rv-actions'))) && /Selalu izinkan/.test(await t.text(sel('.rv.is-active .rv-actions'))));
  await t.shot('build_profanity_review');

  // "Dengar" -> engine preview (worker) -> WAV in the job workdir
  await t.ev(`Array.from(document.querySelectorAll('${sel('.rv.is-active .rv-actions .btn')}')).filter(b => /Dengar/.test(b.textContent))[0].click()`);
  await t.waitFor(`/Memutar contoh|tidak bisa diputar/.test((document.querySelector('#toasts') || {}).textContent || '')`, 30000, 'preview toast');
  if (live) {
    const wd = path.join(t.sandbox, 'home', 'Videos', 'Klipora', 'Tutorial - Full Session', 'preview');
    const wavs = fs.existsSync(wd) ? fs.readdirSync(wd).filter((f) => f.endsWith('.wav')) : [];
    t.check('profanity: preview WAV rendered by the engine', wavs.length >= 1, wd);
  } else t.check('profanity: preview toast', true);

  // "Selalu izinkan" -> allow list + row off; "Selalu sensor" -> block list + row on
  await t.ev(`Array.from(document.querySelectorAll('${sel('.rv.is-active .rv-actions .btn')}')).filter(b => /Selalu izinkan/.test(b.textContent))[0].click()`);
  await t.wait(150);
  l = lib();
  t.check('profanity: Selalu izinkan -> allow + row off', l.allow.indexOf('anjir') >= 0 && (await t.ev(`document.querySelector('${sel('.rv')}').classList.contains('is-off')`)), JSON.stringify(l));
  await t.ev(`Array.from(document.querySelectorAll('${sel('.rv.is-active .rv-actions .btn')}')).filter(b => /Selalu sensor/.test(b.textContent))[0].click()`);
  await t.wait(150);
  l = lib();
  t.check('profanity: Selalu sensor -> block, not allow, row on', l.block.indexOf('anjir') >= 0 && l.allow.indexOf('anjir') < 0 && (await t.ev(`!document.querySelector('${sel('.rv')}').classList.contains('is-off')`)), JSON.stringify(l));

  /* ---------------- F. apply -> engine plan -> host calls on a clone -> result */
  await t.click('#primary');
  await t.waitFor(`location.hash === '#tool/profanity/result'`, 60000, 'apply result');
  await t.wait(100);
  const begin = await t.calls('bac_profanity_begin'), keys = await t.calls('bac_profanity_keys'), tones = await t.calls('bac_profanity_tones'), marks = await t.calls('bac_addMarkers');
  t.check('profanity: clone of the analyzed sequence + sensor track', begin.length === 1 && begin[0][0] === 'seq-long35' && begin[0][1] === 'Tutorial - Full Session (Klipora)' && begin[0][2] === 'Klipora Sensor' && begin[0][3].tag === '[Klipora-PF]', JSON.stringify(begin));
  const r0 = keys[0] && keys[0][1][0];
  t.check('profanity: keys for the anjir range on its media only', keys.length === 1 && r0 && Math.abs(r0.t0 - 1361.0) < 0.06 && Math.abs(r0.t1 - 1361.38) < 0.1 && r0.paths[0] === MEDIA35 && keys[0][2].mode === 'beep' && keys[0][2].ramp === 0.01, JSON.stringify(keys));
  const tone = tones[0] && tones[0][2][0];
  t.check('profanity: one beep WAV placed on the sensor track', tones.length === 1 && /^made-pf/.test(tones[0][0]) && tones[0][1] === 1 && /beep_1000hz_.*\.wav$/.test(tone.path) && Math.abs(tone.t - 1361.0) < 0.1, JSON.stringify(tones));
  if (live) t.check('profanity: beep WAV exists', fs.existsSync(tone.path), tone.path);
  t.check('profanity: [Klipora-PF] marker on the clone', marks.length >= 1 && marks[marks.length - 1][0][0].tag === '[Klipora-PF]' && /^made-pf/.test(marks[marks.length - 1][1]), JSON.stringify(marks.slice(-1)));
  t.check('profanity: result card', /Sequence baru siap/.test(await t.text(sel('[data-pane="result"] .result-h h2'))) && /Buka yang asli/.test(await t.text(sel('[data-pane="result"]'))));
  t.check('profanity: next step Auto Caption', /Auto Caption/.test(await t.text('#primary .lbl') || ''));
  await t.shot('build_profanity_result');
  if (live) {
    const dec = MEDIA35.replace(/\.[^.]+$/, '') + '_profanity.json';
    t.check('profanity: no captions decision file for a listener-only hit', !fs.existsSync(dec) || JSON.parse(fs.readFileSync(dec, 'utf8')).words);
  }

  /* ---------------- G. host failure mid-way -> clone deleted, error pane */
  await t.ev(`(__acStub.failKeys = true, true)`);
  const before = (await t.calls('bac_deleteSequence')).length;
  await t.go('tool/profanity/review');
  await t.wait(150);
  await t.click('#primary');
  await t.waitFor(`location.hash === '#tool/profanity/error'`, 60000, 'error pane');
  await t.wait(200);
  const dels = await t.calls('bac_deleteSequence');
  t.check('profanity: failed apply deletes its half-made clone', dels.length === before + 1 && /^made-pf/.test(dels[dels.length - 1][0]), JSON.stringify(dels));
  t.check('profanity: error explains the cleanup', /sudah dihapus/.test(await t.text(sel('[data-pane="error"]'))));
  await t.ev(`(__acStub.failKeys = false, true)`);

  /* ---------------- I. English UI: main pane + review pane, no Indonesian leftovers */
  await t.go('tool/profanity');
  await t.wait(100);
  await t.ev("AC.i18n.set('en')");
  await t.waitFor(`document.querySelector('${sel('[data-pane="main"] .tool-hero')}') && /Find swear words/.test(document.querySelector('#primary .lbl').textContent)`, 5000, 'english main pane');
  const leftMain = await t.idLeftovers(sel('[data-pane="main"]'));
  t.check('profanity en: main pane has no Indonesian', leftMain.length === 0, JSON.stringify(leftMain));
  t.check('profanity en: strictness presets Relaxed/Normal/Strict', (await t.ev(`Array.from(document.querySelector('${sel('[aria-label="Strictness"]')}').querySelectorAll('button')).map(b => b.textContent).join('|')`)) === 'Relaxed|Normal|Strict');
  t.check('profanity en: modes + lists', (await t.ev(`document.querySelector('${sel('.opt.is-on b')}').textContent`)) === 'Beep' &&
    /Always bleep/.test(await t.text(sel('.profanity-list[data-kind="block"]'))) && /Always allow/.test(await t.text(sel('.profanity-list[data-kind="allow"]'))));
  await t.click('#primary');
  await t.waitFor(`location.hash === '#tool/profanity/review'`, 60000, 'english review pane');
  await t.wait(200);
  t.check('profanity en: primary "Beep 1 word"', (await t.text('#primary .lbl')) === 'Beep 1 word', await t.text('#primary .lbl'));
  // "anjir" is on the user's block list now (section E): tier tag Strong; still heard only in the second listen
  t.check('profanity en: row tags Strong + Second listen', /Strong/.test(await t.text(sel('.rv .rv-l1'))) && /Second listen/.test(await t.text(sel('.rv .rv-l1'))), await t.text(sel('.rv .rv-l1')));
  // Transcript context in the rows (.rv-ctx) is the user's own speech (content), not UI text.
  await t.ev(`(document.querySelectorAll('${sel('.rv-ctx')}').forEach(function (e) { e.setAttribute('data-i18n-skip', ''); }), true)`);
  const leftRv = await t.idLeftovers(sel('[data-pane="review"]'));
  t.check('profanity en: review pane has no Indonesian', leftRv.length === 0, JSON.stringify(leftRv));
  await t.shot('build_profanity_review_en');
  await t.ev("AC.i18n.set('id')");
  await t.wait(200);

  /* ---------------- H. host jsx (ES3) against a fake Premiere DOM */
  hostTests(t);

  // narrow panel: no horizontal overflow on the main pane
  await t.go('tool/profanity');
  await t.wait(100);
  t.check('profanity: no horizontal overflow', await t.ev(`document.querySelector('${sel('[data-pane="main"]')}').scrollWidth <= document.querySelector('${sel('[data-pane="main"]')}').clientWidth + 1`));
}

/* ================================================================ fake Premiere for host/33_profanity.jsx */
function hostTests(t) {
  const host = path.join(t.panel, 'host');
  const code = ['00_util.jsx', '10_timeline.jsx', '33_profanity.jsx'].map((f) => fs.readFileSync(path.join(host, f), 'utf8')).join('\n');
  const LV0 = 0.17782794;                                                 // 0 dB
  function Param(name, value) { this.displayName = name; this.value = value; this.keys = []; this.varying = false; }
  Param.prototype = {
    isTimeVarying() { return this.varying; },
    setTimeVarying(v) { this.varying = !!v; if (!v) this.keys = []; },
    getValue() { return this.value; },
    getKeys() { return this.keys.map((k) => ({ seconds: k.t })); },
    addKey(t) { if (!this.keys.some((k) => Math.abs(k.t - t) < 1e-9)) { this.keys.push({ t, v: this.getValueAtTime(t) }); this.keys.sort((a, b) => a.t - b.t); } },
    setValueAtKey(t, v) { const k = this.keys.find((x) => Math.abs(x.t - t) < 1e-9); if (!k) throw new Error('no key at ' + t); k.v = v; },
    getValueAtKey(time) { const s = typeof time === 'number' ? time : time.seconds; const k = this.keys.find((x) => Math.abs(x.t - s) < 1e-9); if (!k) throw new Error('no key at ' + s); return k.v; },
    setValue(v) { this.value = v; },
    removeKey(time) { const s = typeof time === 'number' ? time : time.seconds; this.keys = this.keys.filter((k) => Math.abs(k.t - s) > 1e-9); },
    setInterpolationTypeAtKey() {},
    getValueAtTime(t) {
      const k = this.keys; if (!this.varying || !k.length) return this.value;
      if (t <= k[0].t) return k[0].v; if (t >= k[k.length - 1].t) return k[k.length - 1].v;
      for (let i = 1; i < k.length; i++) if (t <= k[i].t) { const a = k[i - 1], b = k[i]; return a.v + (b.v - a.v) * (t - a.t) / (b.t - a.t); }
      return this.value;
    }
  };
  const coll = (arr, n = 'numItems') => { const o = { [n]: arr.length }; arr.forEach((x, i) => { o[i] = x; }); return o; };
  function Clip(name, media, start, end, inP) {
    this.name = name; this.start = { seconds: start }; this.end = { seconds: end }; this.inPoint = { seconds: inP }; this.disabled = false;
    this.level = new Param('Level', LV0);
    this.components = coll([{ matchName: 'Internal Volume Stereo', displayName: 'Volume', properties: coll([new Param('Bypass', 0), this.level]) },
                            { matchName: 'Internal Channel Volume Stereo', displayName: 'Channel Volume', properties: coll([new Param('Bypass', 0), new Param('Left', 1)]) }]);
    this.projectItem = { getMediaPath: () => media };
  }
  Clip.prototype.getSpeed = function () { return 1; };
  Clip.prototype.remove = function () { const tr = this.track; tr.list.splice(tr.list.indexOf(this), 1); tr.sync(); return true; };
  function Track(name, clips) { this.name = name; this.list = clips; this.locked = false; this.placed = []; this.sync(); }
  Track.prototype.sync = function () { this.list.forEach((c) => { c.track = this; }); this.clips = coll(this.list); };
  Track.prototype.isLocked = function () { return this.locked; };
  Track.prototype.overwriteClip = function (item, sec) { this.placed.push([item.path, sec]); this.list.push(new Clip(item.name, item.path, sec, sec + 0.38, 0)); this.sync(); return true; };
  function Markers(list) { this.list = list || []; }
  Markers.prototype = { getFirstMarker() { return this.list[0] || null; }, getNextMarker(m) { return this.list[this.list.indexOf(m) + 1] || null; },
    deleteMarker(m) { this.list.splice(this.list.indexOf(m), 1); return true; } };
  const marker = (a, b, comment) => ({ name: 'Sensor', comments: comment, start: { seconds: a }, end: { seconds: b } });
  const DIA = 'C:\\media\\talk.mp4', MUS = 'C:\\media\\music.wav';
  const a1 = new Track('Audio 1', [new Clip('talk', DIA, 0, 10, 5), new Clip('talk', DIA, 10, 20, 30)]);
  const a2 = new Track('Audio 2', [new Clip('music', MUS, 0, 20, 0)]);
  const seqs = [];
  function Seq(id, name, tracks) { this.sequenceID = id; this.name = name; this.timebase = '2116800000'; this.tl = tracks; this.markers = new Markers(); this.sync(); }
  Seq.prototype.sync = function () { this.audioTracks = coll(this.tl, 'numTracks'); this.videoTracks = coll([], 'numTracks'); };
  Seq.prototype.clone = function () {
    const c = new Seq('clone' + seqs.length, this.name + ' Copy', this.tl.map((tr) => new Track(tr.name, tr.list.map((x) => {
      const n = new Clip(x.name, x.projectItem.getMediaPath(), x.start.seconds, x.end.seconds, x.inPoint.seconds);
      n.level.value = x.level.value; n.level.varying = x.level.varying; n.level.keys = x.level.keys.map((k) => ({ t: k.t, v: k.v })); return n; }))));
    c.markers = new Markers(this.markers.list.slice()); seqs.push(c); return true; };
  const src = new Seq('src', 'Tutorial', [a1, a2]);
  seqs.push(src);
  let active = src; const imported = [];
  const project = {
    get sequences() { return coll(seqs, 'numSequences'); }, get activeSequence() { return active; },
    openSequence(id) { active = seqs.find((s) => s.sequenceID === id) || active; },
    deleteSequence(s) { seqs.splice(seqs.indexOf(s), 1); return true; },
    rootItem: { children: coll([]), createBin(name) { const b = { name, type: 2, children: coll([]), list: [] }; this.children = coll([b]); return b; } },
    getInsertionBin() { return this.rootItem; },
    importFiles(paths, s, bin) { paths.forEach((p) => { bin.list.push({ type: 1, name: path.basename(p), path: p, getMediaPath: () => p }); }); bin.children = coll(bin.list); imported.push(...paths); }
  };
  const ctx = vm.createContext({
    app: { project, enableQE() {} }, $: { global: {} }, ProjectItemType: { BIN: 2 },
    File: function (p) { this.exists = fs.existsSync(p); },
    qe: { project: { getActiveSequence: () => ({
      addTracks(nv, av, na) { if (na) { active.tl.push(new Track('Audio ' + (active.tl.length + 1), [])); active.sync(); } },
      getAudioTrackAt: (i) => ({ setName: (n) => { active.tl[i].name = n; } }) }) } }
  });
  vm.runInContext(code, ctx, { filename: 'host.jsx' });
  const J = (s) => { if (String(s).indexOf('ERR:') === 0) throw new Error(s); return JSON.parse(s); };
  const db = (v) => (v <= 0 ? -Infinity : 20 * Math.log10(v) + 15);

  // begin: clone + sensor track (named through QE setName when Track.name is read-only in real Premiere)
  const b = J(ctx.bac_profanity_begin('src', 'Tutorial (Klipora)', 'Klipora Sensor'));
  const clone = seqs.find((s) => s.sequenceID === b.id);
  t.check('host: begin clones + adds the sensor track', b.ok && clone && clone.name === 'Tutorial (Klipora)' && b.track === 2 && b.named && clone.tl[2].name === 'Klipora Sensor' && src.tl.length === 2, JSON.stringify(b));
  // keys: 2.0-2.4 s on the dialog (media 7.0-7.4), music untouched; 9.9-10.3 crosses the cut between two dialog clips
  const k = J(ctx.bac_profanity_keys(b.id, [{ t0: 2.0, t1: 2.4, paths: [DIA] }, { t0: 9.9, t1: 10.3, paths: [DIA] }], { mode: 'beep', ramp: 0.01, skipTrack: 2, last: true }));
  const c1 = clone.tl[0].list[0].level, c2 = clone.tl[0].list[1].level, mus = clone.tl[1].list[0].level;
  t.check('host: keys only on dialog clips', k.ok && k.clips === 3 && !mus.varying && k.missed.length === 0, JSON.stringify(k));
  t.check('host: 10 ms ramps in media time', JSON.stringify(c1.keys.slice(0, 4).map((x) => [Math.round(x.t * 1000) / 1000, Math.round(db(x.v))])) === JSON.stringify([[6.99, 0], [7, -Infinity], [7.4, -Infinity], [7.41, 0]]), JSON.stringify(c1.keys));
  t.check('host: muted inside, untouched outside', c1.getValueAtTime(7.2) === 0 && Math.abs(c1.getValueAtTime(6.5) - LV0) < 1e-9 && Math.abs(c1.getValueAtTime(8) - LV0) < 1e-9);
  t.check('host: range across a cut keys both clips without a restore key at the edge', c1.getValueAtTime(14.95) === 0 && c2.keys[0].t === 30 && c2.keys[0].v === 0 && Math.abs(c2.getValueAtTime(30.5) - LV0) < 1e-9, JSON.stringify(c2.keys));
  t.check('host: original sequence untouched', !src.tl[0].list[0].level.varying);
  // duck -20 dB keeps the user's own level automation around the range
  const lv = clone.tl[0].list[1].level;
  const d = J(ctx.bac_profanity_keys(b.id, [{ t0: 15.0, t1: 15.5, paths: [DIA] }], { mode: 'duck', duckDb: -20, ramp: 0.01 }));
  t.check('host: duck = base level -20 dB', d.clips === 1 && Math.abs(db(lv.getValueAtTime(35.2)) - (-20)) < 0.01 && Math.abs(lv.getValueAtTime(36) - LV0) < 1e-9, JSON.stringify(lv.keys));
  // locked dialog track is skipped and reported
  clone.tl[1].locked = true;
  const lk = J(ctx.bac_profanity_keys(b.id, [{ t0: 1.0, t1: 1.3, paths: [] }], { mode: 'mute' }));
  t.check('host: locked track skipped + reported', lk.locked.join() === 'Audio 2' && lk.clips === 1, JSON.stringify(lk));
  // tones: engine WAVs imported once, placed by seconds; non-wav / missing refused (no modal dialog)
  const sfx = path.join(t.root, 'engine', 'ac', 'assets', 'sfx');
  const w = fs.existsSync(sfx) ? fs.readdirSync(sfx).filter((f) => f.endsWith('.wav')).map((f) => path.join(sfx, f)) : [];
  if (w.length) {
    const tn = J(ctx.bac_profanity_tones(b.id, 2, [{ t: 1.98, path: w[0] }, { t: 9.95, path: w[0] }, { t: 12, path: 'C:\\nope\\x.wav' }, { t: 13, path: 'C:\\x.mp4' }]));
    t.check('host: tones placed, imported once, bad paths refused', tn.placed === 2 && tn.missing.length === 2 && imported.length === 1 && JSON.stringify(clone.tl[2].placed.map((x) => x[1])) === '[1.98,9.95]', JSON.stringify(tn));
  }
  const pr = J(ctx.bac_profanity_probe(b.id, [2.2, 3.0], 2));
  t.check('host: probe reads levels back', pr.at[0].levels.some((x) => x.track === 0 && x.db === -999) && pr.at[1].levels.some((x) => x.track === 0 && x.db === 0), JSON.stringify(pr));
  // re-run on a censored sequence (clone of the clone): the old sensor is REPLACED, not stacked (live bug: a
  // "Kecilkan" re-run kept the old mute keys, the old beep and the old marker)
  clone.tl[1].locked = false;
  if (!clone.tl[2].list.length) clone.tl[2].overwriteClip({ name: 'beep.wav', path: 'C:/x/beep.wav' }, 2.0);
  // a project from before the rename: sensor track "AutoCut Sensor", markers [AC-PF] (old) and [Klipora-PF] (new)
  clone.tl[2].name = 'AutoCut Sensor';
  clone.markers.list.push(marker(1.0, 1.3, 'a***r. (Bip)\n[AC-PF]'), marker(2.0, 2.4, 'a***r. (Bip)\n[Klipora-PF]'), marker(9.9, 10.3, 'x (Bip)\n[AC-PF]'),
    marker(15.0, 15.5, 'x (Bip)\n[Klipora-PF]'), marker(5, 5, 'catatan user'));
  const r2 = J(ctx.bac_profanity_begin(b.id, 'Tutorial (Klipora) (Klipora)', 'Klipora Sensor', { track: false, tag: '[Klipora-PF]' }));
  const cc = seqs.find((s) => s.sequenceID === r2.id);
  const lv1 = cc.tl[0].list[0].level, lv2 = cc.tl[0].list[1].level;
  t.check('host: re-run undoes the old sensor in the new clone (old track name + old/new tags)', r2.undo.ranges === 4 && r2.trackName === 'AutoCut Sensor' && r2.undo.clips >= 1 && r2.track === 2 && cc.tl.length === 3 && !cc.tl[2].list.length &&
    cc.markers.list.length === 1 && !lv1.varying && Math.abs(lv1.value - LV0) < 1e-9 && !lv2.varying && Math.abs(lv2.value - LV0) < 1e-9, JSON.stringify({ r2, k1: lv1.keys, k2: lv2.keys, v2: lv2.value }));
  t.check('host: re-run leaves the censored source as it was', clone.tl[2].list.length >= 1 && clone.markers.list.length === 5 && c1.varying && c1.getValueAtTime(7.2) === 0);
  J(ctx.bac_profanity_keys(r2.id, [{ t0: 2.0, t1: 2.4, paths: [DIA] }], { mode: 'duck', duckDb: -20, ramp: 0.01, skipTrack: 2 }));
  t.check('host: re-run duck after an old mute = -20 dB, not muted', Math.abs(db(lv1.getValueAtTime(7.2)) - (-20)) < 0.01 && Math.abs(lv1.getValueAtTime(6.5) - LV0) < 1e-9, JSON.stringify(lv1.keys));
  // without markers (old run had "Tandai dengan marker" off): same range re-keyed with the base read AFTER the old keys left
  J(ctx.bac_profanity_keys(b.id, [{ t0: 2.0, t1: 2.4, paths: [DIA] }], { mode: 'duck', duckDb: -20, ramp: 0.01, skipTrack: 2 }));
  t.check('host: duck over an old mute on the same range = -20 dB', Math.abs(db(c1.getValueAtTime(7.2)) - (-20)) < 0.01, JSON.stringify(c1.keys.slice(0, 6)));
  t.check('host: missing sequence -> ERR', String(ctx.bac_profanity_keys('nope', [], {})).indexOf('ERR:') === 0);
}
