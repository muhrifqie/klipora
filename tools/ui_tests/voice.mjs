// voice.mjs: Suara Jernih / Clear Voice in headless Chrome + the host jsx against a mocked Premiere DOM (node vm).
//   node tools/ui_test.mjs tools/ui_tests/voice.mjs [--engine live|mock] [--width 280]
// Live engine: real engine/cli.py + worker.py on the 49 s test clip (cached transcript): tracks, preview, analyze ->
// Tinjau -> apply (renders a real WAV into the sandbox workdir) -> stubbed bac_voice_* host calls -> result card.
// A second sequence with a synthetic music WAV on A2 exercises auto ducking. The real ES3 host code runs in section H.
// Section K switches the UI to English (main pane + review pane: no Indonesian left, exact English labels).
import fs from 'fs';
import path from 'path';
import vm from 'vm';

const sel = (s) => `[data-screen="tool-voice"] ${s}`;

// Recording host stubs for the bac_voice_* functions (the real ones run in Premiere; section H runs their code).
const HOST = {
  bac_voice_begin: `function (srcId, name, trackName) { var s = __acStub.seq; var c = JSON.parse(JSON.stringify(s)); c.id = 'made-vc' + Object.keys(__acStub.made).length; c.name = name;
    c.audio.push({ index: c.audio.length, name: trackName, clips: [] }); __acStub.made[c.id] = c;
    return JSON.stringify({ ok: true, id: c.id, name: c.name, origId: s.id, origName: s.name, track: c.audio.length - 1, named: true, trackName: trackName, rerun: false, cleared: 0 }); }`,
  bac_voice_disable: `function (id, clips) { if (!__acStub.made[id]) return 'ERR:Sequence hasil tidak ditemukan.';
    return JSON.stringify({ ok: true, disabled: clips.length, muted: 0, missing: [], locked: [], videoKept: 0 }); }`,
  bac_voice_place: `function (id, track, wav, start) { if (__acStub.failPlace) return 'ERR:import gagal (tes)';
    return JSON.stringify({ ok: true, start: start, end: start + 48.925, name: 'suara.wav', ms: 5 }); }`,
  bac_voice_duck: `function (id, tracks, ranges, opts) { return JSON.stringify({ ok: true, keys: ranges.length * 4, clips: 1, locked: [], reset: 0, ms: 2 }); }`
};

// Mono 16 kHz WAV with a chord + 2 Hz pulse (scores as music in the engine's detector).
function musicWav(file, secs) {
  const sr = 16000, n = sr * secs, buf = Buffer.alloc(44 + n * 2);
  buf.write('RIFF', 0); buf.writeUInt32LE(36 + n * 2, 4); buf.write('WAVEfmt ', 8); buf.writeUInt32LE(16, 16); buf.writeUInt16LE(1, 20);
  buf.writeUInt16LE(1, 22); buf.writeUInt32LE(sr, 24); buf.writeUInt32LE(sr * 2, 28); buf.writeUInt16LE(2, 32); buf.writeUInt16LE(16, 34);
  buf.write('data', 36); buf.writeUInt32LE(n * 2, 40);
  for (let i = 0; i < n; i++) {
    const t = i / sr, v = [220, 277, 330, 440].reduce((a, f) => a + Math.sin(2 * Math.PI * f * t), 0) * 0.1 * (1 + 0.5 * Math.sin(2 * Math.PI * 2 * t));
    buf.writeInt16LE(Math.max(-32767, Math.min(32767, Math.round(v * 30000))), 44 + i * 2);
  }
  fs.writeFileSync(file, buf);
}

export default async function (t) {
  const live = t.engine === 'live';
  const rt = `AC.tools.runtime('voice')`;
  await t.go('tool/voice');
  await t.waitFor(`document.querySelector('${sel('.tool-hero')}')`, 8000, 'voice page');
  t.check('voice: registered in Potong with the right title', await t.ev(`(function(){ var d = AC.tools.get('voice'); return d && d.group === 'potong' && d.title === 'Suara Jernih' && d.desc === 'Bersihkan noise, klik, dan ratakan volume suara'; })()`));
  if (!live) {
    t.check('voice (mock engine): page opens, primary usable', /Analisis suara/.test(await t.text('#primary')));
    console.log('voice.mjs: full checks need --engine live');
    return;
  }
  for (const [fn, src] of Object.entries(HOST)) await t.host(fn, src);

  /* ---------------- A. main pane + tracks overview from the worker */
  await t.waitFor(`/Suara di/.test(document.querySelector('${sel('.voice-tracktag')}').textContent)`, 30000, 'voice track tag');
  t.check('voice: voice track found with its words', /Suara di Audio 1, \d+ kata/.test(await t.text(sel('.voice-tracktag'))), await t.text(sel('.voice-tracktag')));
  t.check('voice: no music on the raw clip', /Tidak ada track lain/.test(await t.text(sel('.voice-music'))));
  t.check('voice: sections present', (await t.ev(`Array.prototype.map.call(document.querySelectorAll('${sel('.sec-h span:first-child')}'), function(e){return e.textContent;}).join('|')`))
    .indexOf('Gaya suara|Target volume|Bersihkan|Musik latar|Suara Saya|Dengar dulu') === 0);
  t.check('voice: primary = Analisis suara', /Analisis suara/.test(await t.text('#primary')));
  await t.shot('voice_main');

  /* ---------------- B. presets, custom, platform */
  const st = `${rt}.ctx.state`;
  await t.click(sel('.seg [data-v="kuat"]'));
  t.check('voice: Kuat preset fills the chain values', await t.ev(`${st}.hp === 100 && ${st}.comp === 4.5 && ${st}.nr === 1 && ${st}.base === 'kuat'`));
  await t.ev(`(function(){ var r = document.querySelectorAll('${sel('details.sec-d input.range')}')[4]; r.value = 1; r.dispatchEvent(new Event('input')); return true; })()`);
  t.check('voice: manual change -> Custom (preset cleared, base kept)', await t.ev(`${st}.preset === null && ${st}.base === 'kuat' && ${st}.presence === 1`) &&
    /Custom/.test(await t.text(sel('details.sec-d .aside'))));
  const p1 = await t.ev(`${rt}.ctx.voice.params()`);
  t.check('voice: params carry the custom chain', p1.preset === 'kuat' && p1.presence === 1 && p1.hp === 100 && p1.lufs === -14, JSON.stringify(p1));
  await t.click(sel('.seg [data-v="natural"]'));
  await t.click(sel('[aria-label="Platform"] [data-v="podcast"]'));
  t.check('voice: Podcast platform -> -16 LUFS', await t.ev(`${st}.lufs === -16 && Array.prototype.some.call(document.querySelectorAll('${sel('.sec-h')}'), function (h) { return /Target volume\s*-16 LUFS/.test(h.textContent); })`));

  /* ---------------- C. "Suara Saya" profile: save, auto-load after reload, delete */
  await t.type(sel('input[aria-label="Nama profil"]'), 'Studio Kamar');
  await t.ev(`(function(){ var b = Array.prototype.filter.call(document.querySelectorAll('${sel('.voice-row .btn')}'), function(x){ return /Simpan/.test(x.textContent); })[0]; b.click(); return true; })()`);
  await t.wait(100);
  const lib = JSON.parse(t.readSandbox(path.join('home', 'AppData', 'Roaming', 'Klipora', 'voice_profiles.json')));
  t.check('voice: profile saved to voice_profiles.json', lib.last === 'Studio Kamar' && lib.profiles.length === 1 && lib.profiles[0].settings.lufs === -16, JSON.stringify(lib));
  await t.ev(`(localStorage.removeItem('ac.tool.voice'), true)`);
  await t.load();
  for (const [fn, src] of Object.entries(HOST)) await t.host(fn, src);
  await t.go('tool/voice');
  await t.waitFor(`${rt} && ${rt}.ctx.voice`, 8000, 'voice page after reload');
  t.check('voice: last profile auto-loaded after reload', await t.ev(`${st}.profile === 'Studio Kamar' && ${st}.lufs === -16 && ${st}.platform === 'podcast'`));
  await t.ev(`(function(){ var b = Array.prototype.filter.call(document.querySelectorAll('${sel('.voice-row .btn')}'), function(x){ return /Hapus/.test(x.textContent); })[0]; b.click(); b.click(); return true; })()`);
  await t.wait(100);
  const lib2 = JSON.parse(t.readSandbox(path.join('home', 'AppData', 'Roaming', 'Klipora', 'voice_profiles.json')));
  t.check('voice: profile deleted (two-step confirm)', lib2.profiles.length === 0 && lib2.last === '', JSON.stringify(lib2));
  await t.click(sel('[aria-label="Platform"] [data-v="youtube"]'));

  /* ---------------- D. "Dengar dulu" 10 s preview (real engine via worker) */
  await t.ev(`(function(){ var b = Array.prototype.filter.call(document.querySelectorAll('${sel('.btn')}'), function(x){ return /Buat contoh/.test(x.textContent); })[0]; b.click(); return true; })()`);
  await t.waitFor(`document.querySelectorAll('${sel('.voice-prev .voice-ab-btn')}').length === 2 || document.querySelector('${sel('.voice-prev .alert')}')`, 60000, 'preview players');
  t.check('voice: preview gives Sebelum / Sesudah players', (await t.count(sel('.voice-prev .voice-ab-btn'))) === 2, await t.text(sel('.voice-prev')));
  await t.ev(`(document.querySelector('${sel('.voice-prev')}').scrollIntoView({ block: 'center' }), true)`);
  await t.wait(80);
  await t.shot('voice_preview');

  /* ---------------- E. "Tiru EQ dari contoh" (engine match_eq through the worker; dialog bypassed) */
  await t.ev(`(window.cep = window.cep || {}, window.cep.fs = window.cep.fs || {}, window.cep.fs.showOpenDialogEx = function () { return { err: 0, data: [${JSON.stringify(globalThis.KLIPORA_MEDIA.seq_2m)}] }; }, true)`);
  await t.ev(`(function(){ var b = Array.prototype.filter.call(document.querySelectorAll('${sel('.btn')}'), function(x){ return /Tiru EQ/.test(x.textContent); })[0]; b.click(); return true; })()`);
  await t.waitFor(`document.querySelectorAll('${sel('.voice-eq-b')}').length === 8`, 60000, 'eq bars');
  t.check('voice: EQ curve from the reference (8 bands, on)', await t.ev(`${st}.eq.length === 8 && ${st}.eqOn === true && ${rt}.ctx.voice.params().eq.length === 8`));
  await t.ev(`(document.querySelector('${sel('.voice-eq')}').scrollIntoView({ block: 'center' }), true)`);
  await t.wait(80);
  await t.shot('voice_eq');
  await t.ev(`(function(){ var i = Array.prototype.filter.call(document.querySelectorAll('${sel('.sw')}'), function(x){ return /Pakai EQ tiruan/.test(x.textContent); })[0].querySelector('input'); i.click(); return true; })()`);
  t.check('voice: EQ switch off -> no EQ sent', await t.ev(`${rt}.ctx.voice.params().eq.length === 0`));

  /* ---------------- F. analyze -> Tinjau */
  await t.click('#primary');
  await t.waitFor(`location.hash === '#tool/voice/review'`, 60000, 'review pane');
  const nRows = await t.count(sel('.rv'));
  t.check('voice: review rows (clicks / breaths)', nRows > 3, String(nRows));
  t.check('voice: review title + primary', /dikecilkan/.test(await t.text(sel('[data-pane="review"]'))) && /Proses suara, \d+ dikecilkan/.test(await t.text('#primary')));
  await t.click(sel('.rv'));
  await t.wait(150);
  const seek = await t.calls('bac_seek');
  t.check('voice: row click seeks 1 s before the item', seek.length >= 1 && seek[seek.length - 1][0] >= 0, JSON.stringify(seek.slice(-1)));
  await t.shot('voice_review');
  await t.ev(`(function(){ var b = Array.prototype.filter.call(document.querySelectorAll('${sel('.rv-actions .btn')}'), function(x){ return /Dengar/.test(x.textContent); })[0]; if (b) b.click(); return !!b; })()`);
  await t.waitFor(`/Memutar|tidak bisa diputar/.test((document.querySelector('#toasts') || {}).textContent || '')`, 30000, 'A/B toast');
  t.check('voice: A/B snippet made', /Memutar sebelum lalu sesudah|tidak bisa diputar/.test(await t.text('#toasts')), await t.text('#toasts'));

  /* ---------------- G. apply -> engine render -> host on a clone -> result */
  await t.click('#primary');
  await t.waitFor(`location.hash === '#tool/voice/result' || location.hash === '#tool/voice/error'`, 120000, 'apply result');
  await t.wait(100);
  t.check('voice: apply ended on the result card', (await t.hash()) === '#tool/voice/result', await t.text(sel('[data-pane="error"]')));
  const begin = await t.calls('bac_voice_begin'), dis = await t.calls('bac_voice_disable'), place = await t.calls('bac_voice_place'), duck = await t.calls('bac_voice_duck');
  t.check('voice: clone + "Klipora Suara" track', begin.length === 1 && begin[0][0] === 'seq-raw49' && begin[0][1] === 'Tutorial - Episode 12 (Klipora)' && begin[0][2] === 'Klipora Suara', JSON.stringify(begin));
  t.check('voice: original voice clip disabled on the clone', dis.length === 1 && /^made-vc/.test(dis[0][0]) && dis[0][1].length === 1 && dis[0][1][0].track === 0, JSON.stringify(dis));
  const wav = place[0] && place[0][2];
  t.check('voice: processed WAV placed at the voice start', place.length === 1 && place[0][1] === 1 && /_suara_\d{8}_\d{6}\.wav$/.test(wav) && place[0][3] === 0 && fs.existsSync(wav), JSON.stringify(place));
  t.check('voice: no ducking without music', duck.length === 0);
  const res = await t.text(sel('[data-pane="result"]'));
  t.check('voice: result shows LUFS before -> after and A/B players', /Sequence baru siap/.test(res) && /ke -14,0/.test(res) && (await t.count(sel('[data-pane="result"] .voice-ab-btn'))) === 2 && /Buka yang asli/.test(res), res.slice(0, 300));
  await t.shot('voice_result');

  /* ---------------- H2. second sequence: music on A2 -> auto duck */
  const mus = path.join(t.sandbox, 'musik_latar.wav');
  musicWav(mus, 50);
  await t.ev(`(function(){ var s = __acStub.fixtures.raw49(); s.id = 'seq-music'; s.name = 'Tutorial musik';
    var c = JSON.parse(JSON.stringify(s.audio[0].clips[0])); c.name = 'musik_latar.wav'; c.path = ${JSON.stringify(mus)};
    s.audio.push({ index: 1, name: 'Musik', muted: false, locked: false, targeted: false, count: 1, clips: [c] }); __acStub.setSeq(s, true); return true; })()`);
  await t.go('tool/voice');
  await t.waitFor(`document.querySelector('${sel('.voice-music .chip')}')`, 30000, 'music chip');
  t.check('voice: music track detected and pre-selected', /Musik \(musik\)/.test(await t.text(sel('.voice-music'))) && (await t.ev(`document.querySelector('${sel('.voice-music .chip')}').getAttribute('aria-pressed')`)) === 'true');
  await t.ev(`(document.querySelector('${sel('.voice-music')}').scrollIntoView({ block: 'center' }), true)`);
  await t.wait(80);
  await t.shot('voice_music');
  await t.click('#primary');
  await t.waitFor(`location.hash === '#tool/voice/review'`, 60000, 'review 2');
  await t.click('#primary');
  await t.waitFor(`location.hash === '#tool/voice/result' || location.hash === '#tool/voice/error'`, 120000, 'apply 2');
  const duck2 = await t.calls('bac_voice_duck');
  t.check('voice: music ducked on A2 with -12 dB, attack/release', duck2.length >= 1 && JSON.stringify(duck2[0][1]) === '[1]' && duck2[0][2].length >= 3 && duck2[0][3].db === -12 && duck2[0][3].attack === 0.25 && duck2[0][3].release === 0.6, JSON.stringify(duck2).slice(0, 300));
  t.check('voice: result mentions the music', /Musik di A2 turun -12 dB/.test(await t.text(sel('[data-pane="result"]'))), await t.text(sel('[data-pane="result"]')));

  /* ---------------- I. host failure mid-way -> clone deleted, error pane */
  await t.ev(`(__acStub.failPlace = true, true)`);
  const before = (await t.calls('bac_deleteSequence')).length;
  await t.go('tool/voice/review');
  await t.wait(150);
  await t.click('#primary');
  await t.waitFor(`location.hash === '#tool/voice/error'`, 120000, 'error pane');
  await t.wait(300);
  const dels = await t.calls('bac_deleteSequence');
  t.check('voice: failed apply deletes its half-made clone', dels.length === before + 1 && /^made-vc/.test(dels[dels.length - 1][0]), JSON.stringify(dels));
  t.check('voice: error explains the cleanup', /sudah dihapus/.test(await t.text(sel('[data-pane="error"]'))));
  await t.ev(`(__acStub.failPlace = false, true)`);

  /* ---------------- J. layout */
  await t.go('tool/voice');
  await t.wait(150);
  t.check(`voice: no horizontal overflow at ${t.width} px`, await t.ev(`document.querySelector('${sel('[data-pane="main"]')}').scrollWidth <= document.querySelector('${sel('[data-pane="main"]')}').clientWidth + 1`));
  if (t.width < 330) await t.shot('voice_main_narrow');

  /* ---------------- K. English UI: main pane + review pane, no Indonesian leftovers */
  await t.setSeq('raw49');                                       // the music test sequence has a track named "Musik"
  await t.go('tool/voice');
  await t.wait(150);
  await t.ev("AC.i18n.set('en')");
  await t.waitFor(`/Analyze voice/.test(document.querySelector('#primary').textContent) && /Voice on/.test(document.querySelector('${sel('.voice-tracktag')}').textContent)`, 30000, 'english main pane');
  const leftMain = await t.idLeftovers(sel('[data-pane="main"]'));
  t.check('voice en: main pane has no Indonesian', leftMain.length === 0, JSON.stringify(leftMain));
  t.check('voice en: sections in English', (await t.ev(`Array.prototype.map.call(document.querySelectorAll('${sel('.sec-h span:first-child')}'), function(e){return e.textContent;}).join('|')`))
    .indexOf('Voice style|Loudness target|Clean up|Background music|My Voice|Listen first') === 0);
  t.check('voice en: track tag', /^Voice on Audio 1, [\d,]+ words$/.test(await t.text(sel('.voice-tracktag'))), await t.text(sel('.voice-tracktag')));
  await t.click('#primary');
  await t.waitFor(`location.hash === '#tool/voice/review' || location.hash === '#tool/voice/result'`, 60000, 'english review pane');
  await t.wait(200);
  t.check('voice en: primary "Process voice, N turned down"', /^Process voice, \d+ turned down$/.test(await t.text('#primary .lbl')), await t.text('#primary .lbl'));
  // Transcript context in the rows (.rv-ctx) is the user's own speech (content), not UI text.
  await t.ev(`(document.querySelectorAll('${sel('.rv-ctx')}').forEach(function (e) { e.setAttribute('data-i18n-skip', ''); }), true)`);
  const leftRv = await t.idLeftovers(sel('[data-pane="review"]'));
  t.check('voice en: review pane has no Indonesian', leftRv.length === 0, JSON.stringify(leftRv));
  await t.shot('voice_review_en');
  await t.ev("AC.i18n.set('id')");
  await t.wait(200);

  hostTests(t);
}

/* ================================================================ fake Premiere for host/43_voice.jsx */
function hostTests(t) {
  const host = path.join(t.panel, 'host');
  const code = ['00_util.jsx', '10_timeline.jsx', '43_voice.jsx'].map((f) => fs.readFileSync(path.join(host, f), 'utf8')).join('\n');
  const LV0 = 0.17782794;
  function Param(name, value) { this.displayName = name; this.value = value; this.keys = []; this.varying = false; }
  Param.prototype = {
    isTimeVarying() { return this.varying; },
    setTimeVarying(v) { this.varying = !!v; if (!v) this.keys = []; },
    getValue() { return this.value; },
    getKeys() { return this.keys.map((k) => ({ seconds: k.t })); },
    addKey(t) { if (!this.keys.some((k) => Math.abs(k.t - t) < 1e-9)) { this.keys.push({ t, v: this.getValueAtTime(t) }); this.keys.sort((a, b) => a.t - b.t); } },
    setValueAtKey(t, v) { const k = this.keys.find((x) => Math.abs(x.t - t) < 1e-9); if (!k) throw new Error('no key at ' + t); k.v = v; },
    getValueAtKey(time) { const s = typeof time === 'number' ? time : time.seconds; const k = this.keys.find((x) => Math.abs(x.t - s) < 1e-9); return k.v; },
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
  function Clip(name, media, start, end, inP, type) {
    this.name = name; this.start = { seconds: start }; this.end = { seconds: end }; this.inPoint = { seconds: inP }; this.disabled = false;
    this.mediaType = type || 'Audio'; this.linked = [];
    this.level = new Param('Level', LV0);
    this.components = coll([{ matchName: 'Internal Volume Stereo', displayName: 'Volume', properties: coll([new Param('Bypass', 0), this.level]) }]);
    this.projectItem = { getMediaPath: () => media };
  }
  Clip.prototype.getSpeed = function () { return 1; };
  Clip.prototype.getLinkedItems = function () { return coll(this.linked); };
  Clip.prototype.remove = function () { const tr = this.track; tr.list.splice(tr.list.indexOf(this), 1); tr.sync(); return true; };
  function Track(name, clips) { this.name = name; this.list = clips; this.locked = false; this.placed = []; this.sync(); }
  Track.prototype.sync = function () { this.list.forEach((c) => { c.track = this; }); this.clips = coll(this.list); };
  Track.prototype.isLocked = function () { return this.locked; };
  Track.prototype.overwriteClip = function (item, sec) { this.placed.push([item.path, sec]); this.list.push(new Clip(item.name, item.path, sec, sec + 48.925, 0)); this.sync(); return true; };
  const TALK = 'C:\\media\\talk.mp4', MUS = 'C:\\media\\music.wav';
  // Premiere toggles linked items together (worst case): the host must put the video back.
  const v1 = new Track('Video 1', [new Clip('talk', TALK, 0, 10, 5, 'Video'), new Clip('talk', TALK, 10, 20, 30, 'Video')]);
  const a1 = new Track('Audio 1', [new Clip('talk', TALK, 0, 10, 5), new Clip('talk', TALK, 10, 20, 30)]);
  const a2 = new Track('Audio 2', [new Clip('music', MUS, 0, 20, 0)]);
  const seqs = [];
  function Seq(id, name, tracks, vtracks) { this.sequenceID = id; this.name = name; this.timebase = '2116800000'; this.tl = tracks; this.vt = vtracks || []; this.sync(); }
  Seq.prototype.sync = function () { this.audioTracks = coll(this.tl, 'numTracks'); this.videoTracks = coll(this.vt, 'numTracks'); };
  Seq.prototype.clone = function () {
    const mk = (x) => { const n = new Clip(x.name, x.projectItem.getMediaPath(), x.start.seconds, x.end.seconds, x.inPoint.seconds, x.mediaType);
      n.disabled = x.disabled; n.level.value = x.level.value; n.level.varying = x.level.varying; n.level.keys = x.level.keys.map((k) => ({ t: k.t, v: k.v })); return n; };
    const vts = this.vt.map((tr) => new Track(tr.name, tr.list.map(mk)));
    const ats = this.tl.map((tr) => new Track(tr.name, tr.list.map(mk)));
    ats.forEach((tr) => tr.list.forEach((c, i) => {             // link audio i of A1 with video i of V1 (same start)
      const v = vts[0] && vts[0].list.find((x) => Math.abs(x.start.seconds - c.start.seconds) < 1e-6 && x.projectItem.getMediaPath() === c.projectItem.getMediaPath());
      if (v) { c.linked = [v]; Object.defineProperty(c, 'disabled', { get() { return this._d; }, set(x) { this._d = x; v.disabled = x; }, configurable: true }); c._d = false; }
    }));
    const c = new Seq('clone' + seqs.length, this.name + ' Copy', ats, vts); seqs.push(c); return true; };
  const src = new Seq('src', 'Tutorial', [a1, a2], [v1]);
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
  const A = (v) => vm.runInContext('(' + JSON.stringify(v) + ')', ctx);     // arrays from the host realm (instanceof Array)
  const J = (s) => { if (String(s).indexOf('ERR:') === 0) throw new Error(s); return JSON.parse(s); };
  const db = (v) => (v <= 0 ? -Infinity : 20 * Math.log10(v) + 15);

  const b = J(ctx.bac_voice_begin('src', 'Tutorial (Klipora)', 'Klipora Suara'));
  const clone = seqs.find((s) => s.sequenceID === b.id);
  t.check('host: begin clones + adds "Klipora Suara"', b.ok && clone && b.track === 2 && b.named && clone.tl[2].name === 'Klipora Suara' && src.tl.length === 2 && !b.rerun, JSON.stringify(b));
  const d = J(ctx.bac_voice_disable(b.id, [{ track: 0, start: 0, end: 10 }, { track: 0, start: 10, end: 20 }, { track: 0, start: 30, end: 40 }]));
  t.check('host: voice clips disabled on the clone, linked video kept enabled', d.disabled === 2 && d.missing.join() === '2' && clone.tl[0].list.every((c) => c.disabled) &&
    clone.vt[0].list.every((c) => !c.disabled) && d.videoKept === 2, JSON.stringify(d));
  t.check('host: original untouched', src.tl[0].list.every((c) => !c.disabled) && src.vt[0].list.every((c) => !c.disabled));
  const sfx = path.join(t.root, 'engine', 'ac', 'assets', 'sfx');
  const w = fs.existsSync(sfx) ? fs.readdirSync(sfx).filter((f) => f.endsWith('.wav')).map((f) => path.join(sfx, f)) : [];
  if (w.length) {
    const pl = J(ctx.bac_voice_place(b.id, 2, w[0], 0));
    t.check('host: WAV imported once and placed at the voice start', pl.ok && imported.length === 1 && clone.tl[2].placed[0][1] === 0 && pl.start === 0, JSON.stringify(pl));
  }
  t.check('host: place refuses non-wav / missing files (no modal dialog)', String(ctx.bac_voice_place(b.id, 2, 'C:\\x.mp4', 0)).indexOf('ERR:') === 0 &&
    String(ctx.bac_voice_place(b.id, 2, 'C:\\nope\\x.wav', 0)).indexOf('ERR:') === 0 && imported.length <= 1);
  // duck: voice 2..5 s and 12..14 s; attack 0.25, release 0.6, -12 dB
  const k = J(ctx.bac_voice_duck(b.id, A([1]), A([[2, 5], [12, 14]]), { db: -12, attack: 0.25, release: 0.6, last: true }));
  const m = clone.tl[1].list[0].level;
  t.check('host: duck keys on the music clip only', k.keys === 8 && k.clips === 1 && !clone.tl[0].list[0].level.varying, JSON.stringify(k));
  t.check('host: -12 dB while speaking, base outside, ramps in between', Math.abs(db(m.getValueAtTime(3)) - (-12)) < 0.01 && Math.abs(m.getValueAtTime(1) - LV0) < 1e-9 &&
    Math.abs(m.getValueAtTime(8) - LV0) < 1e-9 && db(m.getValueAtTime(1.875)) < -0.1 && db(m.getValueAtTime(1.875)) > -12 && Math.abs(db(m.getValueAtTime(13)) - (-12)) < 0.01, JSON.stringify(m.keys));
  // re-run on the result: the output track is reused and cleared, the old duck curve is reset first. The result
  // comes from before the rename: its output track is still called "AutoCut Suara" and must be found.
  clone.tl[2].name = 'AutoCut Suara';
  const r2 = J(ctx.bac_voice_begin(b.id, 'Tutorial (Klipora) (Klipora)', 'Klipora Suara'));
  const cc = seqs.find((s) => s.sequenceID === r2.id);
  t.check('host: re-run reuses + clears the old "AutoCut Suara"', r2.rerun && r2.trackName === 'AutoCut Suara' && r2.track === 2 && cc.tl.length === 3 && cc.tl[2].list.length === 0 && r2.cleared === (w.length ? 1 : 0), JSON.stringify(r2));
  J(ctx.bac_voice_duck(r2.id, A([1]), A([[6, 8]]), { db: -6, attack: 0.25, release: 0.6, reset: true }));
  const m2 = cc.tl[1].list[0].level;
  t.check('host: re-run duck replaces the old curve', Math.abs(m2.getValueAtTime(3) - LV0) < 1e-9 && Math.abs(db(m2.getValueAtTime(7)) - (-6)) < 0.01, JSON.stringify(m2.keys));
  cc.tl[1].locked = true;
  const lk = J(ctx.bac_voice_duck(r2.id, A([1]), A([[9, 10]]), { db: -12 }));
  t.check('host: locked music track skipped + reported', lk.locked.join() === 'Audio 2' && lk.keys === 0, JSON.stringify(lk));
  const pr = J(ctx.bac_voice_probe(b.id, [0], 2, [1], [3, 8]));
  t.check('host: probe reads disabled flags, output clip and duck levels', pr.voice.every((x) => x[3]) && pr.levels[0].db[0] === -12 && pr.levels[1].db[0] === 0, JSON.stringify(pr));
  t.check('host: missing sequence -> ERR', String(ctx.bac_voice_duck('nope', [1], [], {})).indexOf('ERR:') === 0);
}
