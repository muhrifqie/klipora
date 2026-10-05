/* cep_stub.js: fake CEP + Node environment so panel/index.html runs in plain (headless) Chrome.
   Injected by tools/ui_test.mjs before any page script (Page.addScriptToEvaluateOnNewDocument), after
   window.__AC_STUB_CONFIG = {...}. Does nothing inside real CEP (window.__adobe_cep__ already exists).

   Provides
   - window.__adobe_cep__: evalScript (canned bac_* answers, overridable), getSystemPath, getHostEnvironment,
     addEventListener/removeEventListener, registerKeyEventsInterest.
   - window.require('fs' | 'path' | 'child_process' | 'os') + window.process:
       fs: backend "http" (real files through the harness, writes only inside the sandbox) or "mem" (in memory).
       child_process.spawn: engine "live" (real processes through the harness) or "mock" (in-page fake engine:
       cli.py health/run, worker.py, --version, taskkill).
   - window.__acStub: test API. calls (every evalScript call), host (handlers), seq (fixture), fixtures,
     fire(type, data), setSeq(obj, fire), markers, engineMock[tool] = function (job, emit, done),
     fs (backend), mode.
   Config keys: server ("http://127.0.0.1:port"), engine ("mock" | "live"), extPath, sandbox, env {...},
     seq (fixture name or object or null), hostDelay (ms), skin ({red,green,blue}), node (false = no require),
     events (false = bac_bindEvents fails -> panel polls), files ({path: text} for mem fs), hostFiles ([...]),
     sampleXml (xmeml copied by bac_exportXml), python (false = mock spawn of python fails with ENOENT),
     locale (Premiere UI locale for getHostEnvironment().appUILocale, default "id_ID"; "en_US" = English UI with uiLang auto). */
(function () {
  'use strict';
  if (window.__adobe_cep__) return;
  var cfg = window.__AC_STUB_CONFIG || {};
  var server = cfg.server || null;
  var extPath = (cfg.extPath || 'C:\\klipora\\panel').replace(/\//g, '\\');
  var root = extPath.replace(/\\[^\\]+$/, '');
  var sandbox = (cfg.sandbox || 'C:\\ac_stub_sandbox').replace(/\//g, '\\');
  var env = Object.assign({
    USERPROFILE: sandbox + '\\home', APPDATA: sandbox + '\\home\\AppData\\Roaming', LOCALAPPDATA: sandbox + '\\home\\AppData\\Local',
    TEMP: sandbox + '\\temp', TMP: sandbox + '\\temp', SystemDrive: 'C:'
  }, cfg.env || {});

  /* ================================================================== fixtures (docs/SPEC.md section 3) */
  // Media paths come from the harness (KLIPORA_TEST_MEDIA, see tools/ui_test.mjs); names shown in the UI are neutral.
  var media = cfg.media || {};
  var MEDIA = String(media.talk_49s || 'C:/klipora_test_media/talk_49s.mp4').replace(/\//g, '\\');
  var MEDIA35 = String(media.talk_35m || 'C:/klipora_test_media/talk_35m.mp4').replace(/\//g, '\\');
  var NAME49 = 'Tutorial - Episode 12', NAME35 = 'Tutorial - Full Session';
  var DUR = 48.925;
  function clip(name, path, start, end, inP, extra) {
    return Object.assign({ name: name, path: path, start: start, end: end, 'in': inP, out: inP + (end - start), speed: 1, disabled: false, selected: false, mgt: false, nested: false, nodeId: 'n' + Math.round(start * 1000) }, extra || {});
  }
  function seqFrom(name, id, clipsV, clipsA, extra) {
    return Object.assign({ id: id, name: name, fps: 120, timebase: '2116800000', width: 2292, height: 960, displayFormat: 998,
      duration: Math.max.apply(null, clipsV.concat(clipsA).map(function (c) { return c.end; }).concat([0])),
      inPoint: null, outPoint: null, player: 0, active: true, level: 'full',
      video: [{ index: 0, name: 'Video 1', muted: false, locked: false, targeted: true, count: clipsV.length, clips: clipsV },
              { index: 1, name: 'Video 2', muted: false, locked: false, targeted: false, count: 0, clips: [] }],
      audio: [{ index: 0, name: 'Audio 1', muted: false, locked: false, targeted: true, count: clipsA.length, clips: clipsA }],
      markers: [], selection: [], selectedCount: 0, ms: 3 }, extra || {});
  }
  var KEPT = [[1.62, 2.56], [2.96, 7.47], [8.46, 10.36], [10.59, 11.5], [15.17, 17.92], [19.49, 22.51], [23.48, 24.98], [25.65, 26.85], [27.2, 29.96], [31.06, 31.79], [32.02, 36.61], [38.14, 40.84], [41.65, 42.46], [42.74, 44.3], [44.49, 45.87]];
  function cut15() {
    var t = 0, v = [], a = [];
    KEPT.forEach(function (k, i) {
      var d = k[1] - k[0];
      v.push(clip(NAME49 + '.mp4', MEDIA, t, t + d, k[0], { nodeId: 'v' + i }));
      a.push(clip(NAME49 + '.mp4', MEDIA, t, t + d, k[0], { nodeId: 'a' + i }));
      t += d;
    });
    return seqFrom(NAME49 + ' (Klipora)', 'seq-cut15', v, a, { player: 16.16 });
  }
  var fixtures = {
    raw49: function () { return seqFrom(NAME49, 'seq-raw49', [clip(NAME49 + '.mp4', MEDIA, 0, DUR, 0)], [clip(NAME49 + '.mp4', MEDIA, 0, DUR, 0)]); },
    cut15: cut15,
    long35: function () {
      var p = MEDIA35, d = 2076.4;
      return seqFrom(NAME35, 'seq-long35', [clip(NAME35 + '.mp4', p, 0, d, 0)], [clip(NAME35 + '.mp4', p, 0, d, 0)]);
    }
  };
  function pickSeq(v) { if (v === null) return null; if (typeof v === 'string') return fixtures[v] ? fixtures[v]() : null; if (v && typeof v === 'object') return v; return fixtures.raw49(); }

  var stub = window.__acStub = {
    mode: { server: !!server, engine: cfg.engine || 'mock', fs: server ? 'http' : 'mem' },
    calls: [], keys: [], host: {}, fixtures: fixtures, markers: [], made: {}, all: {}, engineMock: {}, procs: {},
    seq: pickSeq(cfg.seq === undefined ? 'raw49' : cfg.seq), listeners: {}
  };

  /* ================================================================== fake __adobe_cep__ */
  function lite(s) {
    if (!s) return null;
    var o = JSON.parse(JSON.stringify(s));
    o.level = 'lite';
    var tracks = o.video.concat(o.audio);
    tracks.forEach(function (t) { t.count = t.clips.length; delete t.clips; });
    var main = s.video.filter(function (t) { return t.clips.length; })[0] || s.audio.filter(function (t) { return t.clips.length; })[0];
    o.spans = main ? main.clips.map(function (c) { return [c.start, c.end]; }) : [];
    o.nMarkers = (s.markers || []).length;
    o.selectedCount = (s.selection || []).length;
    delete o.markers; delete o.selection;
    return o;
  }
  function J(v) { return JSON.stringify(v === undefined ? null : v); }
  // Active sequence, or any sequence seen before by id (so AC.seq.lock() can target a non-active sequence).
  function cur(id) { if (stub.seq) stub.all[stub.seq.id] = stub.seq; if (!id) return stub.seq; return stub.all[id] || stub.made[id] || null; }
  var H = stub.host;
  H.bac_ping = function () { return J({ ok: true, version: '26.2.2 (stub)', project: 'Stub.prproj', helpers: true }); };
  H.bac_seqInfo = function (level, id) { var s = cur(id); if (!s) return 'null'; return J(level === 'lite' ? lite(s) : s); };
  H.bac_activeSeqId = function () { return stub.seq ? J({ id: stub.seq.id, name: stub.seq.name }) : 'null'; };
  H.bac_openSequence = function (id) { var s = cur(id); return s ? J({ ok: true, id: s.id, name: s.name }) : 'ERR:Sequence tidak ditemukan. Mungkin sudah dihapus.'; };
  H.bac_seek = function (t) { if (!stub.seq) return 'ERR:Buka sequence dulu.'; stub.seq.player = Number(t); return J({ ok: true, t: Number(t) }); };
  H.bac_addMarkers = function (list) { var n = 0; (list || []).forEach(function (m) { stub.markers.push(m); n++; }); return J({ ok: true, n: n }); };
  H.bac_clearMarkersByTag = function (tag) { var b = stub.markers.length; stub.markers = stub.markers.filter(function (m) { return (m.tag || '') !== tag && String(m.comment || '').indexOf(tag) < 0; }); return J({ ok: true, n: b - stub.markers.length }); };
  function cloneOf(name) { var c = JSON.parse(JSON.stringify(stub.seq)); c.id = 'made-' + Object.keys(stub.made).length; c.name = name || stub.seq.name + ' (Klipora)'; stub.made[c.id] = c; return c; }
  H.bac_cloneSeq = function (name) { if (!stub.seq) return 'ERR:Buka sequence dulu.'; var c = cloneOf(name); return J({ ok: true, id: c.id, name: c.name, origId: stub.seq.id, origName: stub.seq.name }); };
  H.bac_removeRanges = function (id, ranges, from, to) {
    var s = stub.made[id]; if (!s) return 'ERR:Sequence hasil tidak ditemukan.';
    var part = ranges.slice(from || 0, to === undefined || to === null ? ranges.length : to), rm = 0;
    part.forEach(function (r) { rm += r[1] - r[0]; });
    s.duration = Math.max(0, s.duration - rm);
    return J({ ok: true, ranges: ranges.length, done: part.length, ms: 5 * part.length, endSec: s.duration });
  };
  H.bac_applyRemove = function (ranges, name) { var c = cloneOf(name); return H.bac_removeRanges(c.id, ranges, 0, ranges.length).replace('"ok":true', '"ok":true,"mode":"extract","id":"' + c.id + '","name":' + J(c.name)); };
  // With the harness (real fs) the "export" copies a real xmeml (cfg.sampleXml) so the live engine can cut it.
  H.bac_exportXml = function (path) {
    if (cfg.sampleXml && stub.fs && stub.fs.existsSync(cfg.sampleXml)) { try { stub.fs.writeFileSync(path, stub.fs.readFileSync(cfg.sampleXml, 'utf8')); } catch (e) { return 'ERR:' + e.message; } }
    return J({ ok: true, ms: 300, path: path, lost: [] });
  };
  H.bac_importXmlAndOpen = function (path, name) { var c = cloneOf(name); return J({ ok: true, id: c.id, name: c.name, created: 1 }); };
  H.bac_deleteSequence = function (id) { if (!stub.made[id]) return 'ERR:Hanya sequence buatan Klipora di sesi ini yang boleh dihapus.'; delete stub.made[id]; return J({ ok: true }); };
  H.bac_exportFrame = function (sec, path) { return J({ ok: true, tc: '00:00:00:000', path: path + '.png', exists: false }); };
  H.bac_bindEvents = function () { return cfg.events === false ? 'ERR:PlugPlugExternalObject tidak tersedia (stub)' : J({ ok: true, n: 6 }); };
  H.bac_unbindEvents = function () { return J({ ok: true, n: 6 }); };

  function evalHost(code) {
    var loads = code.match(/\$\.evalFile\(/g);
    if (loads) return loads.map(function () { return 'ok'; }).join('\n');
    var m = /^\s*([A-Za-z_$][\w$.]*)\(([\s\S]*)\)\s*;?\s*$/.exec(code);
    if (!m) return '';
    var fn = m[1], args;
    try { args = (new Function('return [' + m[2] + '];'))(); } catch (e) { return 'EvalScript error.'; }
    stub.calls.push({ fn: fn, args: args, t: Date.now() });
    var h = H[fn];
    if (typeof h !== 'function') return 'EvalScript error.';
    try { var r = h.apply(null, args); return r === undefined ? 'undefined' : String(r); } catch (e) { return 'EvalScript error.'; }
  }
  var skin = cfg.skin || { red: 29, green: 29, blue: 29 };
  window.__adobe_cep__ = {
    evalScript: function (code, cb) { var r = evalHost(String(code)); setTimeout(function () { if (cb) cb(r); }, cfg.hostDelay === undefined ? 4 : cfg.hostDelay); },
    getSystemPath: function (type) {
      if (type === 'extension') return 'file:///' + extPath.replace(/\\/g, '/');
      if (type === 'userData') return 'file:///' + env.APPDATA.replace(/\\/g, '/');
      return 'file:///' + sandbox.replace(/\\/g, '/');
    },
    getHostEnvironment: function () {
      return JSON.stringify({ appName: 'PPRO', appVersion: '26.2.2', appLocale: cfg.locale || 'id_ID', appUILocale: cfg.locale || 'id_ID', appSkinInfo: { panelBackgroundColor: { antialiasLevel: 0, color: { red: skin.red, green: skin.green, blue: skin.blue, alpha: 255 } } } });
    },
    addEventListener: function (type, fn) { (stub.listeners[type] = stub.listeners[type] || []).push(fn); },
    removeEventListener: function (type, fn) { stub.listeners[type] = (stub.listeners[type] || []).filter(function (f) { return f !== fn; }); },
    registerKeyEventsInterest: function (json) { stub.keys = JSON.parse(json); },
    dispatchEvent: function () {}, getCurrentApiVersion: function () { return JSON.stringify({ major: 12, minor: 0, micro: 0 }); }
  };
  if (cfg.events === false) delete window.__adobe_cep__.addEventListener;
  stub.fire = function (type, data) { (stub.listeners[type] || []).slice().forEach(function (f) { f({ type: type, data: data }); }); };
  // Replace the fixture (object, fixture name or null) and optionally fire a Premiere event.
  stub.setSeq = function (s, fire) { stub.seq = pickSeq(s); if (fire !== false) stub.fire('com.klipora.ev', 'onSequenceActivated|' + (stub.seq ? stub.seq.id : '')); };
  stub.setSkin = function (c) { skin = c; stub.fire('com.adobe.csxs.events.ThemeColorChanged', ''); };

  if (cfg.node === false) return;

  /* ================================================================== Node shim: path (win32) */
  function norm(p) {
    p = String(p).replace(/\//g, '\\');
    var drive = (/^[A-Za-z]:/.exec(p) || [''])[0], rest = p.slice(drive.length), abs = rest.charAt(0) === '\\';
    var out = [];
    rest.split('\\').forEach(function (seg) { if (!seg || seg === '.') return; if (seg === '..') out.pop(); else out.push(seg); });
    return drive + (abs ? '\\' : '') + out.join('\\');
  }
  var path = {
    sep: '\\', delimiter: ';',
    join: function () { return norm(Array.prototype.slice.call(arguments).filter(function (x) { return x !== ''; }).join('\\')); },
    resolve: function () { var a = Array.prototype.slice.call(arguments), p = ''; a.forEach(function (x) { x = String(x); p = /^[A-Za-z]:/.test(x) ? x : p + '\\' + x; }); return norm(p); },
    normalize: norm,
    dirname: function (p) { p = norm(p); var i = p.lastIndexOf('\\'); return i <= 2 ? p.slice(0, i + 1) : p.slice(0, i); },
    basename: function (p, ext) { var b = norm(p).split('\\').pop(); if (ext && b.slice(-ext.length) === ext) b = b.slice(0, -ext.length); return b; },
    extname: function (p) { var b = norm(p).split('\\').pop(), i = b.lastIndexOf('.'); return i > 0 ? b.slice(i) : ''; },
    isAbsolute: function (p) { return /^[A-Za-z]:[\\/]/.test(p); }
  };
  path.win32 = path;

  /* ================================================================== Node shim: fs */
  function fsErr(code, msg) { var e = new Error(code + ': ' + msg); e.code = code; return e; }
  function xhr(method, url, body, sync, cb) {
    var x = new XMLHttpRequest();
    x.open(method, url, !sync);
    if (!sync) x.onload = function () { cb(null, JSON.parse(x.responseText)); };
    if (!sync) x.onerror = function () { cb(new Error('harness unreachable')); };
    x.send(body === undefined ? null : JSON.stringify(body));
    if (sync) return JSON.parse(x.responseText);
  }
  function remote(op, p, extra) {
    var r = xhr('POST', server + '/fs', Object.assign({ op: op, p: p }, extra || {}), true);
    if (r.error) throw fsErr(r.error.code || 'EIO', r.error.message || op);
    return r.result;
  }
  var mem = {};                                   // lower-case path -> {p, data, mtime, dir}
  function mkey(p) { return norm(p).toLowerCase(); }
  function memDir(p) { var k = mkey(p); while (k && !mem[k]) { mem[k] = { p: norm(p), dir: true, mtime: Date.now() }; p = path.dirname(p); k = mkey(p); if (/^[a-z]:\\?$/.test(k)) break; } }
  Object.keys(cfg.files || {}).forEach(function (p) { memDir(path.dirname(p)); mem[mkey(p)] = { p: norm(p), data: cfg.files[p], mtime: Date.now() }; });
  (cfg.hostFiles || ['00_util.jsx', '05_bridge.jsx', '10_timeline.jsx', '20_graphics.jsx']).forEach(function (f) { var p = extPath + '\\host\\' + f; if (!server) { memDir(path.dirname(p)); mem[mkey(p)] = { p: p, data: '// stub', mtime: Date.now() }; } });
  if (!server) { var cli = root + '\\engine\\cli.py'; memDir(path.dirname(cli)); mem[mkey(cli)] = { p: cli, data: '# mock engine', mtime: Date.now() }; }
  var fs = server ? {
    existsSync: function (p) { return remote('exists', p); },
    readFileSync: function (p, enc) { return remote('read', p); },
    writeFileSync: function (p, data) { remote('write', p, { data: String(data) }); },
    appendFileSync: function (p, data) { remote('append', p, { data: String(data) }); },
    mkdirSync: function (p) { remote('mkdir', p); },
    readdirSync: function (p) { return remote('readdir', p); },
    unlinkSync: function (p) { remote('unlink', p); },
    renameSync: function (a, b) { remote('rename', a, { to: b }); },
    statSync: function (p) { var s = remote('stat', p); return { size: s.size, mtime: new Date(s.mtime), isDirectory: function () { return s.dir; }, isFile: function () { return !s.dir; } }; },
    realpathSync: function (p) { return remote('realpath', p); }
  } : {
    existsSync: function (p) { return !!mem[mkey(p)]; },
    readFileSync: function (p) { var f = mem[mkey(p)]; if (!f || f.dir) throw fsErr('ENOENT', 'no such file ' + p); return f.data; },
    writeFileSync: function (p, data) { memDir(path.dirname(p)); mem[mkey(p)] = { p: norm(p), data: String(data), mtime: Date.now() }; },
    appendFileSync: function (p, data) { var f = mem[mkey(p)]; fs.writeFileSync(p, (f ? f.data : '') + data); },
    mkdirSync: function (p) { memDir(p); },
    readdirSync: function (p) {
      var k = mkey(p) + '\\', out = [];
      Object.keys(mem).forEach(function (q) { if (q.indexOf(k) === 0 && q.slice(k.length).indexOf('\\') < 0 && q.length > k.length) out.push(mem[q].p.split('\\').pop()); });
      return out;
    },
    unlinkSync: function (p) { if (!mem[mkey(p)]) throw fsErr('ENOENT', p); delete mem[mkey(p)]; },
    renameSync: function (a, b) { var f = mem[mkey(a)]; if (!f) throw fsErr('ENOENT', a); delete mem[mkey(a)]; f.p = norm(b); mem[mkey(b)] = f; },
    statSync: function (p) { var f = mem[mkey(p)]; if (!f) throw fsErr('ENOENT', p); return { size: (f.data || '').length, mtime: new Date(f.mtime), isDirectory: function () { return !!f.dir; }, isFile: function () { return !f.dir; } }; },
    realpathSync: function (p) { return norm(p); }
  };
  stub.fs = fs;

  /* ================================================================== Node shim: child_process */
  function Emitter() { this._l = {}; }
  Emitter.prototype.on = function (n, f) { (this._l[n] = this._l[n] || []).push(f); return this; };
  Emitter.prototype.once = Emitter.prototype.on;
  Emitter.prototype.emit = function (n) { var a = Array.prototype.slice.call(arguments, 1); (this._l[n] || []).slice().forEach(function (f) { f.apply(null, a); }); };
  function Child(pid) {
    Emitter.call(this);
    this.pid = pid; this.stdout = new Emitter(); this.stderr = new Emitter(); this.exitCode = null;
    var self = this;
    this._queued = [];   // stdin written before the (async) process is ready
    this.stdin = { write: function (d) { if (self._stdin) self._stdin(String(d)); else self._queued.push(String(d)); return true; }, end: function () { if (self._stdinEnd) self._stdinEnd(); } };
  }
  Child.prototype = Object.create(Emitter.prototype);
  Child.prototype.kill = function () { if (this._kill) this._kill(); };

  var nextPid = 41000;
  function liveSpawn(cmd, args, opts) {
    var ch = new Child(0), since = 0, id = null, done = false;
    xhr('POST', server + '/spawn', { cmd: cmd, args: args, cwd: opts && opts.cwd, env: opts && opts.env }, false, function (err, r) {
      if (err || r.error) { ch.emit('error', Object.assign(new Error((r && r.error && r.error.message) || 'spawn failed'), { code: r && r.error && r.error.code || 'ENOENT' })); ch.emit('close', -1); return; }
      id = r.id; ch.pid = r.pid;
      (function poll() {
        xhr('GET', server + '/proc?id=' + id + '&since=' + since, undefined, false, function (e2, p) {
          if (e2) { ch.emit('error', e2); return; }
          p.events.forEach(function (ev) {
            if (ev.t === 'out') ch.stdout.emit('data', ev.d);
            else if (ev.t === 'err') ch.stderr.emit('data', ev.d);
            else if (ev.t === 'error') ch.emit('error', Object.assign(new Error(ev.d.message), { code: ev.d.code }));
            else if (ev.t === 'exit') { done = true; ch.exitCode = ev.d; ch.emit('exit', ev.d); ch.emit('close', ev.d); }
          });
          since = p.next;
          if (!done && !p.done) setTimeout(poll, 30);
        });
      })();
    });
    ch._stdin = function (d) { var send = function () { if (id === null) { setTimeout(send, 20); return; } xhr('POST', server + '/stdin', { id: id, data: d }, false, function () {}); }; send(); };
    ch._stdinEnd = function () { var send = function () { if (id === null) { setTimeout(send, 20); return; } xhr('POST', server + '/stdin', { id: id, end: true }, false, function () {}); }; send(); };
    ch._kill = function () { if (id !== null) xhr('POST', server + '/kill', { id: id }, false, function () {}); };
    return ch;
  }

  /* ---------------- mock engine (in page) */
  function readJob(p) { try { return JSON.parse(fs.readFileSync(p, 'utf8')); } catch (e) { return null; } }
  function mockEcho(job, emit, done) {
    var p = job.params || {}, a = job.action, dur = (job.seq && job.seq.duration) || 30;
    if (a === 'fail') { emit({ ev: 'error', code: p.code || 'ECHO_FAIL', msg: p.msg || 'Gagal sengaja (tes).', hint: 'Ini hanya tes.' }); return done(1); }
    if (a === 'echo') { emit({ ev: 'result', data: { params: p } }); return done(0); }
    if (a === 'slow') {
      var secs = Number(p.seconds || 5), t0 = Date.now();
      emit({ ev: 'stage', id: 'wait', label: 'Menunggu', i: 0, n: 1, w: 1 });
      var iv = setInterval(function () {
        var f = Math.min(1, (Date.now() - t0) / (secs * 1000));
        emit({ ev: 'progress', pct: Math.round(f * 1000) / 10, all: Math.round(f * 1000) / 10 });
        if (f >= 1) { clearInterval(iv); emit({ ev: 'stage_done', id: 'wait', sec: secs }); emit({ ev: 'result', data: { slept: secs } }); done(0); }
      }, 100);
      return function () { clearInterval(iv); };
    }
    if (a === 'apply') {
      var doc = readJob(job.review) || { items: [] }, rs = doc.items.filter(function (it) { return it.on; }).map(function (it) { return [it.t0, it.t1]; });
      emit({ ev: 'stage', id: 'plan', label: 'Hitung potongan', i: 0, n: 1, w: 1 });
      emit({ ev: 'stage_done', id: 'plan', sec: 0.01 });
      emit({ ev: 'result', data: { plan: { kind: 'remove_ranges', ranges: rs, timebase: 'sequence' }, summary: { n: doc.items.length, on: rs.length } } });
      return done(0);
    }
    // analyze
    var plan = [['read', 'Baca sequence', 0.1], ['scan', 'Cari bagian', 0.7], ['review', 'Siapkan review', 0.2]], k = 0;
    var total = Number(p.seconds || 0.6) * 1000, every = Number(p.every || 5), len = Number(p.len || 0.6);
    var timers = [];
    function step(i) {
      if (i >= plan.length) {
        var items = [], t = every / 2, n = 0;
        while (t + len < dur) { var kind = ['gap', 'filler', 'repeat'][n % 3]; items.push({ id: kind[0] + Math.round(t * 100), t0: Math.round(t * 1000) / 1000, t1: Math.round((t + len) * 1000) / 1000, kind: kind, on: n % 4 !== 3, conf: Math.round((0.5 + 0.1 * (n % 5)) * 100) / 100, label: 'Contoh ' + kind + ' #' + (n + 1), ctx: { pre: 'kata sebelum', post: 'kata sesudah' } }); t += every; n++; }
        var rp = (job.workdir || sandbox + '\\work') + '\\echo_review.json';
        fs.mkdirSync(path.dirname(rp), { recursive: true });
        fs.writeFileSync(rp, JSON.stringify({ v: 1, tool: 'echo', timebase: 'sequence', duration: dur, seq: job.seq ? { id: job.seq.id, name: job.seq.name } : null, params: p, stats: {}, items: items }));
        emit({ ev: 'result', data: { review: rp, summary: { n: items.length, on: items.filter(function (x) { return x.on; }).length }, duration: dur } });
        return done(0);
      }
      emit({ ev: 'stage', id: plan[i][0], label: plan[i][1], i: i, n: plan.length, w: plan[i][2] });
      var ticks = 5;
      for (var j = 1; j <= ticks; j++) (function (j) {
        timers.push(setTimeout(function () {
          emit({ ev: 'progress', pct: j / ticks * 100 });
          if (j === ticks) { emit({ ev: 'stage_done', id: plan[i][0], sec: Math.round(total * plan[i][2]) / 1000 }); step(i + 1); }
        }, total * plan[i][2] / ticks * j));
      })(j);
    }
    step(0);
    return function () { timers.forEach(clearTimeout); };
  }
  var HEALTH = { ok: true, python: { ok: true, version: '3.14.3' }, ffmpeg: { ok: true, version: 'ffmpeg version 8.1 (stub)', libass: true, nvenc: true },
                 gpu: { ok: true, name: 'NVIDIA GeForce RTX 5050 Laptop GPU', mem_total_mb: 8151 }, ai: { ok: true, ms: 12, base_url: 'http://127.0.0.1:8168/v1', model: 'grok-fast', has_key: true, disabled: false },
                 disk: { ok: true, free_gb: { 'C:': 5.6 } }, issues: [], ms: 40 };
  stub.health = HEALTH;
  function mockSpawn(cmd, args, opts) {
    var ch = new Child(++nextPid), alive = true, stopper = null;
    stub.procs[ch.pid] = ch;
    var out = function (o) { if (alive) ch.stdout.emit('data', JSON.stringify(o) + '\n'); };
    var close = function (code) { if (!alive) return; alive = false; delete stub.procs[ch.pid]; ch.exitCode = code; ch.emit('exit', code); ch.emit('close', code); };
    ch._kill = function () { if (stopper) stopper(); close(1); };
    args = args || [];
    setTimeout(function () {
      var base = String(cmd).split(/[\\/]/).pop().toLowerCase();
      if (base === 'taskkill' || base === 'taskkill.exe') {
        var pid = Number(args[args.indexOf('/pid') + 1]), target = stub.procs[pid];
        if (target) target._kill();
        return close(0);
      }
      if (base === 'explorer' || base === 'explorer.exe') return close(1);
      if (cfg.python === false || !/^(python3?|py)(\.exe)?$/.test(base)) { alive = false; var e = new Error('spawn ' + cmd + ' ENOENT'); e.code = 'ENOENT'; ch.emit('error', e); ch.emit('close', -2); return; }
      if (args.indexOf('--version') >= 0) { ch.stdout.emit('data', 'Python 3.14.3\n'); return close(0); }
      var script = args.filter(function (a) { return /\.py$/i.test(a); })[0] || '';
      var sub = args[args.indexOf(script) + 1];
      if (/worker\.py$/i.test(script)) {
        out({ ev: 'ready', pid: ch.pid, version: 'stub' });
        var buf = '';
        ch._stdin = function (d) {
          buf += d; var lines = buf.split('\n'); buf = lines.pop();
          lines.forEach(function (l) {
            if (!l.trim()) return;
            var req = JSON.parse(l);
            if (req.cmd === 'cancel') { out({ id: req.id, ev: 'result', data: { cancelled: true } }); return; }
            if (!req.job) return;
            var fn = stub.engineMock[req.job.tool] || mockEcho;
            // like worker.py: the request id wins, the stage id travels in "stage"
            fn(req.job, function (o) { var w = Object.assign({}, o, { id: req.id }); if (o.ev === 'stage' || o.ev === 'stage_done') w.stage = o.id; out(w); }, function () {});
          });
        };
        ch._queued.splice(0).forEach(ch._stdin);
        return;
      }
      if (sub === 'health') { out({ ev: 'result', data: HEALTH }); return close(0); }
      if (sub === 'tools') { out({ ev: 'result', data: { tools: [{ id: 'echo', ok: true, actions: ['analyze', 'apply', 'slow', 'fail', 'echo'] }] } }); return close(0); }
      if (sub === 'run') {
        var job = readJob(args[args.indexOf('run') + 1]);
        if (!job) { out({ ev: 'error', code: 'BAD_JOB', msg: 'File job tidak terbaca' }); return close(1); }
        var fn = stub.engineMock[job.tool] || (job.tool === 'echo' ? mockEcho : null);
        if (!fn) { out({ ev: 'error', code: 'NO_TOOL', msg: 'Tool tidak dikenal: ' + job.tool, hint: 'Perbarui panel / engine.' }); return close(1); }
        stopper = fn(job, out, close);
        return;
      }
      ch.stderr.emit('data', 'usage: cli.py run|health|tools\n'); close(2);
    }, 5);
    return ch;
  }
  var child_process = { spawn: function (cmd, args, opts) { return (server && stub.mode.engine === 'live') ? liveSpawn(cmd, args, opts) : mockSpawn(cmd, args, opts); } };
  var os = { tmpdir: function () { return env.TEMP; }, homedir: function () { return env.USERPROFILE; }, EOL: '\r\n', platform: function () { return 'win32'; } };
  var mods = { fs: fs, path: path, child_process: child_process, os: os };
  window.require = function (name) { if (!mods[name]) throw new Error('Cannot find module ' + name + ' (stub)'); return mods[name]; };
  window.process = { env: env, platform: 'win32', pid: 4242, versions: { node: '16.0.0-stub' } };
})();
