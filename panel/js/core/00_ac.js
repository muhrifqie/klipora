/* 00_ac.js: the window.AC namespace, small utilities, an event emitter, Task (progress model shared by engine
   jobs and host work) and the in-panel error log. Classic script, Chrome 99 / CEP 12, ES5 style.
   Every panel file is an IIFE that only touches window.AC (CEP defines globals like `cep`, `require`, `process`). */
(function () {
  'use strict';
  var AC = window.AC = window.AC || {};
  AC.version = '2.0.0';

  /* ------------------------------------------------------------------ emitter */
  function Emitter() { this._ev = {}; }
  Emitter.prototype.on = function (name, fn) {
    (this._ev[name] = this._ev[name] || []).push(fn);
    if (scope) scope.push([this, name, fn]);   // AC.collectListeners: removed again when a screen is re-rendered
    return this;
  };
  Emitter.prototype.off = function (name, fn) {
    var l = this._ev[name]; if (!l) return this;
    for (var i = l.length - 1; i >= 0; i--) if (l[i] === fn || l[i]._orig === fn) l.splice(i, 1);
    return this;
  };
  Emitter.prototype.once = function (name, fn) {
    var self = this, w = function () { self.off(name, w); return fn.apply(this, arguments); };
    w._orig = fn; return this.on(name, w);
  };
  Emitter.prototype.emit = function (name) {
    var args = Array.prototype.slice.call(arguments, 1), l = (this._ev[name] || []).slice();
    for (var i = 0; i < l.length; i++) {
      try { l[i].apply(this, args); } catch (e) { AC.log.error(e, 'listener ' + name); }
    }
    return l.length;
  };
  AC.Emitter = Emitter;
  // Run fn() and return every Emitter listener it added ([[emitter, name, fn]]), so a screen that is rendered
  // again (language switch) can drop the listeners of its previous render with AC.dropListeners(list).
  var scope = null;
  AC.collectListeners = function (fn) {
    var prev = scope, mine = [];
    scope = mine;
    try { fn(); } finally { scope = prev; if (prev) Array.prototype.push.apply(prev, mine); }
    return mine;
  };
  AC.dropListeners = function (list) { (list || []).forEach(function (l) { l[0].off(l[1], l[2]); }); };
  AC.bus = new Emitter();   // app-wide: 'seq', 'route', 'settings', 'health', 'task', 'theme'

  /* ------------------------------------------------------------------ util */
  var U = AC.util = {};
  U.$ = function (s, r) { return (r || document).querySelector(s); };
  U.$$ = function (s, r) { return Array.prototype.slice.call((r || document).querySelectorAll(s)); };
  U.esc = function (s) {
    return String(s === undefined || s === null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  };
  // Locale-aware number formatting. AC.i18n (i18n.js) switches U.loc: id = decimal comma + dot thousands
  // ("1.500.000", "0,5"), en = decimal dot + comma thousands ("1,500,000", "0.5").
  U.loc = { lang: 'id', dec: ',', thou: '.', sec: 'dtk', min: 'mnt', wib: true };   // i18n-ignore (boot default)
  U.dec = function (n, d) { var s = Number(n || 0).toFixed(d === undefined ? 1 : d); return U.loc.dec === '.' ? s : s.replace('.', U.loc.dec); };
  U.int = function (n) { return String(Math.round(Number(n) || 0)).replace(/\B(?=(\d{3})+(?!\d))/g, U.loc.thou); };
  U.rp = function (n) { return 'Rp ' + U.int(n); };
  U.pct = function (x) { return Math.round((Number(x) || 0) * 100) + '%'; };
  // 0:49 / 1:02:03
  U.mmss = function (s) {
    s = Math.max(0, Number(s) || 0);
    var h = Math.floor(s / 3600), m = Math.floor(s / 60) % 60, r = Math.round(s - Math.floor(s / 60) * 60);
    if (r === 60) { r = 0; m++; if (m === 60) { m = 0; h++; } }
    var mm = h ? (m < 10 ? '0' : '') + m : String(m);
    return (h ? h + ':' : '') + mm + ':' + (r < 10 ? '0' : '') + r;
  };
  // timecode for review rows: 0:11,50 / 1:02:03,25 (en: 0:11.50)
  U.tcode = function (s) {
    s = Math.max(0, Number(s) || 0);
    var cs = Math.round(s * 100), h = Math.floor(cs / 360000), m = Math.floor(cs / 6000) % 60, r = (cs % 6000) / 100;
    var mm = h ? (m < 10 ? '0' : '') + m : String(m);
    return (h ? h + ':' : '') + mm + ':' + (r < 10 ? '0' : '') + U.dec(r, 2);
  };
  // "3,7 dtk" / "12 dtk" / "1 mnt 5 dtk" (en: "3.7 s" / "1 min 5 s")
  U.dtk = function (s) {
    s = Math.max(0, Number(s) || 0);
    if (s >= 90) { var tot = Math.round(s), m = Math.floor(tot / 60), r = tot - m * 60; return m + ' ' + U.loc.min + (r ? ' ' + r + ' ' + U.loc.sec : ''); }
    return U.dec(s, s < 10 ? 1 : 0) + ' ' + U.loc.sec;
  };
  U.clamp = function (v, a, b) { return Math.max(a, Math.min(b, v)); };
  U.debounce = function (fn, ms) {
    var t = null;
    var d = function () { var self = this, a = arguments; clearTimeout(t); t = setTimeout(function () { t = null; fn.apply(self, a); }, ms); };
    d.cancel = function () { clearTimeout(t); t = null; };
    d.flush = function () { if (t) { clearTimeout(t); t = null; fn(); } };
    return d;
  };
  U.uid = function (p) { return (p || 'id') + '-' + Date.now().toString(36) + '-' + Math.floor(Math.random() * 1e6).toString(36); };
  U.copy = function (o) { return o === undefined ? undefined : JSON.parse(JSON.stringify(o)); };
  U.assign = function (t) {
    for (var i = 1; i < arguments.length; i++) { var s = arguments[i]; if (s) for (var k in s) if (Object.prototype.hasOwnProperty.call(s, k)) t[k] = s[k]; }
    return t;
  };
  // Indonesian UI: times are always shown in WIB (UTC+7), independent of the PC time zone. Other locales use the
  // PC's local time (global audience). U.loc.months/days/today/yesterday come from the locale file (i18n.js).
  var MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'Mei', 'Jun', 'Jul', 'Agu', 'Sep', 'Okt', 'Nov', 'Des'];
  var DAYS = ['Minggu', 'Senin', 'Selasa', 'Rabu', 'Kamis', 'Jumat', 'Sabtu'];
  function wib(ms) { return new Date((ms || Date.now()) + 7 * 3600000); }
  function pad2(n) { return (n < 10 ? '0' : '') + n; }
  // Shown date parts {y, mo, d, wd, h, mi} in the UI time zone (WIB for id, local otherwise).
  function parts(ms) {
    if (U.loc.wib) { var w = wib(ms); return { y: w.getUTCFullYear(), mo: w.getUTCMonth(), d: w.getUTCDate(), wd: w.getUTCDay(), h: w.getUTCHours(), mi: w.getUTCMinutes() }; }
    var l = new Date(ms || Date.now());
    return { y: l.getFullYear(), mo: l.getMonth(), d: l.getDate(), wd: l.getDay(), h: l.getHours(), mi: l.getMinutes() };
  }
  U.wibTime = function (ms) { var p = parts(ms); return pad2(p.h) + ':' + pad2(p.mi) + (U.loc.wib ? ' WIB' : ''); };
  U.timeLabel = U.wibTime;
  // Job ids and file stamps: always WIB, so names stay stable whatever the UI language.
  U.wibStamp = function (ms) {
    var d = wib(ms), p = pad2;
    return d.getUTCFullYear() + p(d.getUTCMonth() + 1) + p(d.getUTCDate()) + '-' + p(d.getUTCHours()) + p(d.getUTCMinutes()) + p(d.getUTCSeconds());
  };
  U.dayKey = function (ms) { var p = parts(ms); return p.y + '-' + p.mo + '-' + p.d; };
  U.dayLabel = function (ms) {
    var p = parts(ms), today = U.dayKey(Date.now()), yest = U.dayKey(Date.now() - 86400000), k = U.dayKey(ms);
    var months = U.loc.months || MONTHS, days = U.loc.days || DAYS;
    var base = U.loc.lang === 'en' ? months[p.mo] + ' ' + p.d : p.d + ' ' + months[p.mo];
    if (k === today) return (U.loc.today || 'Hari ini') + ', ' + base;   // i18n-ignore (fallback)
    if (k === yest) return (U.loc.yesterday || 'Kemarin') + ', ' + base;
    return days[p.wd] + ', ' + base;
  };
  // Clipboard that works inside CEP (navigator.clipboard may be blocked there).
  U.copyText = function (text) {
    var ta = document.createElement('textarea');
    ta.value = String(text); ta.setAttribute('readonly', ''); ta.style.position = 'fixed'; ta.style.left = '-9999px';
    document.body.appendChild(ta); ta.select();
    var ok = false;
    try { ok = document.execCommand('copy'); } catch (e) { ok = false; }
    document.body.removeChild(ta);
    return ok;
  };
  function untitled() { return AC.t ? AC.t('misc.untitled') : 'Tanpa nama'; }   // i18n-ignore (before i18n.js)
  U.safeName = function (s) { return String(s || untitled()).replace(/[\\/:*?"<>|\x00-\x1f]+/g, '_').replace(/^[\s.]+|[\s.]+$/g, '').slice(0, 80) || untitled(); };
  U.err = function (code, msg, hint, extra) {
    var e = new Error(msg || code); e.code = code; e.msg = msg || code; e.hint = hint || '';
    if (extra) U.assign(e, extra);
    return e;
  };
  // Brand + legacy names. Results are "<seq> (Klipora)"; projects from before the rename use "(AutoCut)",
  // "AutoCut <X>" tracks/bins, bin "AutoCut BOT" and "[AC-*]" marker tags, which are still recognised.
  AC.brand = {
    name: 'Klipora',
    suffix: ' (Klipora)',
    resultRe: /\((Klipora|AutoCut)( \d+)?\)/,                  // a result sequence name
    suffixRe: / \((Klipora|AutoCut)( \d+)?\)$/,                // strip it: name.replace(AC.brand.suffixRe, '')
    track: function (x) { return 'Klipora ' + x; },             // "Klipora Captions"
    nameAlts: function (n) { n = String(n); return n === 'Klipora' ? [n, 'AutoCut BOT'] : n.indexOf('Klipora ') === 0 ? [n, 'AutoCut ' + n.slice(8)] : [n]; },
    nameIs: function (actual, n) { return AC.brand.nameAlts(n).indexOf(String(actual)) >= 0; }
  };
  // "[Klipora-CH]" <-> "[AC-CH]"
  U.tagAlts = function (tag) { var m = /^\[(Klipora|AC)-(.+)\]$/.exec(String(tag || '')); return m ? ['[Klipora-' + m[2] + ']', '[AC-' + m[2] + ']'] : [String(tag || '')]; };
  U.hasTag = function (text, tag) { if (!tag) return false; var s = String(text || ''); return U.tagAlts(tag).some(function (a) { return s.indexOf(a) >= 0; }); };
  U.errMsg = function (e) { return e ? (e.msg || e.message || String(e)) : ''; };

  /* ------------------------------------------------------------------ local storage (per-viewer conveniences) */
  AC.store = {
    get: function (k, def) { try { var v = localStorage.getItem('ac.' + k); return v === null ? def : JSON.parse(v); } catch (e) { return def; } },
    set: function (k, v) { try { localStorage.setItem('ac.' + k, JSON.stringify(v)); } catch (e) { /* private mode */ } }
  };

  /* ------------------------------------------------------------------ log (ring buffer + error capture) */
  var MAX = 400;
  AC.log = {
    lines: [], errors: [],
    add: function (level, msg) {
      var line = U.wibTime() + ' ' + level + ' ' + msg;
      this.lines.push(line); if (this.lines.length > MAX) this.lines.shift();
      return line;
    },
    info: function (msg) { this.add('INFO', msg); },
    warn: function (msg) { this.add('WARN', msg); if (window.console) console.warn('[AC] ' + msg); },
    // Handled errors: recorded, printed as console.warn (console.error is reserved for real bugs).
    error: function (e, where) {
      var msg = (where ? where + ': ' : '') + (e && (e.stack || e.msg || e.message) || String(e));
      this.errors.push(msg); if (this.errors.length > 50) this.errors.shift();
      this.add('ERROR', msg);
      if (window.console) console.warn('[AC] ' + msg);
    },
    text: function () { return this.lines.join('\n'); }
  };
  window.addEventListener('error', function (ev) { AC.log.add('UNCAUGHT', (ev.message || 'error') + ' @' + (ev.filename || '') + ':' + (ev.lineno || 0)); AC.log.errors.push(ev.message); });
  window.addEventListener('unhandledrejection', function (ev) { var r = ev.reason; AC.log.add('UNHANDLED', r && (r.stack || r.message) || String(r)); AC.log.errors.push(String(r && r.message || r)); });

  /* ------------------------------------------------------------------ Task: progress model
     Shared by engine jobs (AC.engine.run) and host work (AC.apply). Events: 'stage', 'progress', 'stage_done',
     'log', 'warn', 'state', 'result', 'error', 'end'. States: queued -> running -> done | error | cancelled. */
  function Task(meta) {
    Emitter.call(this);
    meta = meta || {};
    this.id = meta.id || U.uid(meta.tool || 'task');
    this.tool = meta.tool || ''; this.action = meta.action || ''; this.title = meta.title || ''; this.label = meta.label || '';
    this.kind = meta.kind || 'engine';
    this.state = 'queued'; this.stages = []; this.cur = -1; this.pct = 0; this.note = '';
    this.logLines = []; this.warnings = []; this.result = undefined; this.error = null;
    this.started = Date.now(); this.ended = 0; this.summary = ''; this.seqName = meta.seqName || '';
    this.meta = meta;
    var self = this;
    this.promise = new Promise(function (res, rej) { self._res = res; self._rej = rej; });
    this.promise.catch(function () { /* handled by listeners */ });
    if (meta.stages) this.plan(meta.stages);
  }
  Task.prototype = Object.create(Emitter.prototype);
  Task.prototype.constructor = Task;
  Task.prototype.then = function (a, b) { return this.promise.then(a, b); };
  Task.prototype.catch = function (b) { return this.promise.catch(b); };
  Task.prototype.isActive = function () { return this.state === 'queued' || this.state === 'running'; };
  Task.prototype.setState = function (s) { if (this.state === s) return; this.state = s; this.emit('state', s); AC.bus.emit('task', this); };
  // stages: [{id, label, w, sub}]
  Task.prototype.plan = function (stages) {
    this.stages = stages.map(function (s) { return { id: s.id, label: s.label || s.id, w: s.w || 1, sub: s.sub || '', state: 'wait', sec: null, note: '', pct: 0 }; });
  };
  Task.prototype._find = function (id) { for (var i = 0; i < this.stages.length; i++) if (this.stages[i].id === id) return i; return -1; };
  Task.prototype.stage = function (ev) {
    if (this.state === 'queued') this.setState('running');
    var i = this._find(ev.id);
    if (i < 0) {
      var at = typeof ev.i === 'number' ? ev.i : -1;
      while (at >= 0 && this.stages.length < at) this.stages.push({ id: '_' + this.stages.length, label: '', w: 1, state: 'wait', pct: 0, placeholder: true });
      if (at >= 0 && this.stages[at] && this.stages[at].placeholder) { i = at; this.stages[i].id = ev.id; this.stages[i].label = ev.id; }
      else { i = this.stages.length; this.stages.push({ id: ev.id, label: ev.id, w: 1, state: 'wait', pct: 0 }); }
    }
    if (typeof ev.n === 'number') while (this.stages.length < ev.n) this.stages.push({ id: '_' + this.stages.length, label: '', w: 1, state: 'wait', pct: 0, placeholder: true });
    var s = this.stages[i];
    if (ev.label) s.label = ev.label;
    if (ev.sub) s.sub = ev.sub;
    if (typeof ev.w === 'number' && ev.w > 0) s.w = ev.w;
    s.placeholder = false;
    for (var k = 0; k < i; k++) if (this.stages[k].state === 'run') this.stages[k].state = 'done';
    s.state = 'run'; s.pct = 0; s.t0 = Date.now(); this.cur = i; this.note = '';
    this._recalc();
    this.emit('stage', s, i);
  };
  Task.prototype.progress = function (pct, all, note) {
    if (this.state === 'queued') this.setState('running');
    var s = this.stages[this.cur];
    if (s && typeof pct === 'number') s.pct = U.clamp(pct, 0, 100);
    this.note = note || '';
    if (typeof all === 'number') this.pct = U.clamp(all, 0, 100); else this._recalc();
    if (!this.stages.length && typeof pct === 'number') this.pct = U.clamp(pct, 0, 100);
    this.emit('progress', this.pct, this.note);
  };
  Task.prototype.stageDone = function (id, sec, note) {
    var i = id ? this._find(id) : this.cur; if (i < 0) i = this.cur;
    var s = this.stages[i]; if (!s) return;
    s.state = 'done'; s.pct = 100; s.sec = typeof sec === 'number' ? sec : (s.t0 ? (Date.now() - s.t0) / 1000 : null);
    if (note) s.note = note;
    this._recalc();
    this.emit('stage_done', s, i);
  };
  Task.prototype._recalc = function () {
    var tot = 0, done = 0;
    for (var i = 0; i < this.stages.length; i++) {
      var s = this.stages[i], w = s.w || 1; tot += w;
      if (s.state === 'done') done += w; else if (s.state === 'run') done += w * (s.pct || 0) / 100;
    }
    if (tot) this.pct = U.clamp(done / tot * 100, 0, 100);
  };
  Task.prototype.log = function (msg, level) {
    var line = (level ? level + ': ' : '') + msg;
    this.logLines.push(line); if (this.logLines.length > 500) this.logLines.shift();
    if (level === 'WARN') { this.warnings.push(msg); this.emit('warn', msg); }
    this.emit('log', line);
  };
  Task.prototype.done = function (result) {
    if (!this.isActive()) return;
    for (var i = 0; i < this.stages.length; i++) if (this.stages[i].state !== 'done' && !this.stages[i].placeholder) { this.stages[i].state = 'done'; this.stages[i].pct = 100; }
    this.pct = 100; this.result = result; this.ended = Date.now();
    this.setState('done'); this.emit('result', result); this.emit('end', this);
    this._res(result);
  };
  Task.prototype.fail = function (err) {
    if (!this.isActive()) return;
    if (!(err instanceof Error)) err = U.err(err && err.code || 'ERROR', err && (err.msg || err.message) || String(err), err && err.hint);
    if (!err.code) err.code = 'ERROR';
    if (!err.log) err.log = this.logLines.slice(-40).join('\n');
    var s = this.stages[this.cur]; if (s && s.state === 'run') s.state = err.code === 'CANCELLED' ? 'wait' : 'err';
    this.error = err; this.ended = Date.now();
    this.setState(err.code === 'CANCELLED' ? 'cancelled' : 'error'); this.emit('error', err); this.emit('end', this);
    this._rej(err);
  };
  Task.prototype.cancel = function () { if (this.isActive()) { if (this._cancel) this._cancel(); else this.fail(U.err('CANCELLED', AC.t ? AC.t('job.cancelled') : 'Cancelled.')); } };
  Task.prototype.elapsed = function () { return ((this.ended || Date.now()) - this.started) / 1000; };
  AC.Task = Task;
})();
