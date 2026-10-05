/* captions.js: Auto Caption editor (tool id "captions"), core. Engine: engine/ac/captions (docs/CAPTIONS_API.md),
   host: host/34_captions.jsx, styles: css/tools/captions.css, docs: docs/tools/captions_ui.md.
   Split into focused files that all hang off AC.cap (loaded in this order by index.html):
     captions.js          state, document load/restore, edit queue (serial, coalesced) + undo/redo, layout, flow
     captions_preview.js  sticky preview stage: worker previews at the playhead / selected page, drag-to-position
     captions_text.js     Teks tab: page cards with word chips, word popover, multi-select, find & replace, kamus,
                          AI cleanup diff, orphan-merge suggestions
     captions_style.js    Template (gallery), Gaya (font, colours, background, position, safe zone), Animasi tabs
     captions_export.js   Ekspor tab, "Terapkan ke timeline" (render overlay -> place/relink, SRT, MOGRT), result card
   Flow: no captions.json in the workdir -> setup (Buat caption = engine doc, transcribes once per media) ->
   editor (every edit = engine doc {ops} through the persistent worker, then a preview) -> Terapkan.
   The document lives in <workdir>\captions.json (one per sequence); reopening the tool restores it. */
(function () {
  'use strict';
  var AC = window.AC, U = AC.util, ui = AC.ui;
  var cap = AC.cap = AC.cap || {};
  cap.bus = new AC.Emitter();      // 'doc' (reloaded), 'draft', 'templates', 'placed', 'stale', 'tab', 'time', 'busy', 'free', 'undo'
  cap.stats = { previews: [], ops: [] };

  // Tab ids stay Indonesian (URLs #tool/captions/<id>, saved state); labels come from cap.tab.<id> at render time.
  cap.TABS = [
    { id: 'template', icon: 'grid' }, { id: 'teks', icon: 'type' }, { id: 'gaya', icon: 'brush' },
    { id: 'animasi', icon: 'motion' }, { id: 'ekspor', icon: 'export' }
  ];
  cap.tabLabel = function (id) { return AC.t('cap.tab.' + id); };
  var TAB_IDS = cap.TABS.map(function (t) { return t.id; });

  var S = cap.S = {
    ctx: null, el: null, seq: null, workdir: '', path: '', doc: null, words: {}, pageOf: {},
    tpl: null, tplP: null, placed: null, stale: null, tab: 'template', busy: 0, undo: [], redo: [], rev: 0,
    draft: null, view: ''
  };

  /* ---------------------------------------------------------------- small helpers */
  cap.fileUrl = function (p) {
    return 'file:///' + String(p).replace(/\\/g, '/').split('/').map(encodeURIComponent).join('/').replace(/^([A-Za-z])%3A/, '$1:');
  };
  // layout.deep_merge semantics (engine): dicts merge, everything else (null included) replaces.
  cap.merge = function (base, over) {
    var out = U.copy(base || {});
    Object.keys(over || {}).forEach(function (k) {
      var v = over[k];
      if (v && typeof v === 'object' && !Array.isArray(v) && out[k] && typeof out[k] === 'object' && !Array.isArray(out[k])) out[k] = cap.merge(out[k], v);
      else out[k] = U.copy(v);
    });
    return out;
  };
  cap.get = function (o, path) {
    var parts = path.split('.');
    for (var i = 0; i < parts.length; i++) { if (o === null || o === undefined) return undefined; o = o[parts[i]]; }
    return o;
  };
  function setPath(o, path, v) {
    var parts = path.split('.'), cur = o;
    for (var i = 0; i < parts.length - 1; i++) { if (!cur[parts[i]] || typeof cur[parts[i]] !== 'object') cur[parts[i]] = {}; cur = cur[parts[i]]; }
    cur[parts[parts.length - 1]] = v;
  }
  function delPath(o, path) {
    var parts = path.split('.'), stack = [], cur = o;
    for (var i = 0; i < parts.length - 1; i++) { if (!cur[parts[i]] || typeof cur[parts[i]] !== 'object') return; stack.push([cur, parts[i]]); cur = cur[parts[i]]; }
    delete cur[parts[parts.length - 1]];
    for (var j = stack.length - 1; j >= 0; j--) { var p = stack[j][0], k = stack[j][1]; if (Object.keys(p[k]).length === 0) delete p[k]; }
  }
  cap.norm = function (s) { return String(s || '').toLowerCase().replace(/[^0-9a-zÀ-ɏ-]+/g, ''); };
  cap.seqLite = function () { return AC.seq.peek(); };

  /* ---------------------------------------------------------------- engine calls (persistent worker, FIFO) */
  // Background engine call (no progress pane). seq: null unless opts.seq is given (undefined = current full JSON).
  cap.worker = function (action, params, opts) {
    opts = opts || {};
    var req = U.assign({ tool: 'captions', action: action, params: params || {}, seq: null, workdir: S.workdir, record: false, title: AC.t('cap.title') }, opts);
    if (opts.withSeq) { delete req.seq; delete req.withSeq; }
    return AC.engine.worker(req);
  };

  /* ---------------------------------------------------------------- the document */
  function indexDoc(d) {
    var words = {}, pageOf = {};
    (d.words || []).forEach(function (w) { words[w.id] = w; });
    (d.pages || []).forEach(function (p, i) { p.lines.forEach(function (ln) { ln.forEach(function (id) { pageOf[id] = i; }); }); });
    S.words = words; S.pageOf = pageOf;
  }
  cap.setDoc = function (d) {
    S.doc = d; S.rev++;
    if (d) indexDoc(d);
    cap.bus.emit('doc', d);
    cap.paintDock();
  };
  cap.readDoc = function () {
    var d = S.path ? AC.sys.readJSON(S.path, null) : null;
    return d && Array.isArray(d.words) && Array.isArray(d.pages) ? d : null;
  };
  cap.reload = function () { var d = cap.readDoc(); if (d) cap.setDoc(d); return d; };
  cap.pageAt = function (t) {
    var pages = (S.doc && S.doc.pages) || [];
    for (var i = 0; i < pages.length; i++) if (t >= pages[i].t0 && t < pages[i].t1) return i;
    return -1;
  };
  cap.nearestPage = function (t) {
    var pages = (S.doc && S.doc.pages) || [], best = -1, bd = Infinity;
    for (var i = 0; i < pages.length; i++) {
      var d = t < pages[i].t0 ? pages[i].t0 - t : t >= pages[i].t1 ? t - pages[i].t1 : 0;
      if (d < bd) { bd = d; best = i; }
    }
    return best;
  };
  // Effective template of the document (template + doc.style, including not-yet-applied edits).
  cap.style = function () { return S.draft || (S.doc && S.doc.style) || {}; };
  cap.eff = function () {
    if (!S.doc) return null;
    var full = S.tpl && S.tpl.full && (S.tpl.full[S.doc.template] || S.tpl.full[S.tpl['default']]);
    return cap.merge(full || (S.tpl && S.tpl.options && S.tpl.options.defaults) || {}, cap.style());
  };

  /* ---------------------------------------------------------------- busy gate (render, AI, rebuild hold the doc) */
  cap.hold = function (task) {
    S.busy++; cap.bus.emit('busy', S.busy);
    var done = function () { S.busy = Math.max(0, S.busy - 1); cap.bus.emit('busy', S.busy); if (!S.busy) cap.bus.emit('free'); };
    (task && task.promise ? task.promise : Promise.resolve(task)).then(done, done);
    return task;
  };
  function waitFree() { return S.busy ? new Promise(function (r) { cap.bus.once('free', r); }) : Promise.resolve(); }

  /* ---------------------------------------------------------------- edit queue + undo
     Every change is an engine `doc` op (seq: null). Ops are batched (120 ms), sent in order through the worker
     (FIFO with previews and renders), then the doc is re-read. Doc-level style edits are sent as the full new
     doc.style (op doc_style, replace: true) so explicit nulls ("no highlight colour") survive. */
  var chain = Promise.resolve(), batch = null;
  function enqueue(fn) {
    var p = chain.then(waitFree).then(fn);
    chain = p.then(function () {}, function () {});
    return p;
  }
  function snapshot(d) {
    var w = {};
    (d.words || []).forEach(function (x) { if (x.edit || x.style) w[x.id] = { edit: x.edit ? U.copy(x.edit) : null, style: x.style ? U.copy(x.style) : null }; });
    return { template: d.template, style: U.copy(d.style || {}), params: U.copy(d.params || {}), page_over: U.copy(d.page_over || {}),
             ranges: U.copy(d.ranges || []), words: w };
  }

  // cap.op(op | [ops], {now, label}) -> Promise of the engine result (after the doc was re-read).
  cap.op = function (ops, opts) {
    opts = opts || {};
    if (!S.doc) return Promise.reject(U.err('NO_CAPTIONS', AC.t('cap.err.noDoc')));
    ops = Array.isArray(ops) ? ops : [ops];
    if (!batch) {
      var b = batch = { ops: [], waiters: [], wd: S.workdir, path: S.path, timer: null, label: '' };
      b.promise = new Promise(function (res, rej) { b.res = res; b.rej = rej; });
    }
    ops.forEach(function (op) {
      var last = batch.ops[batch.ops.length - 1];
      if (op.op === 'doc_style' && op.replace && last && last.op === 'doc_style' && last.replace) batch.ops[batch.ops.length - 1] = op;
      else batch.ops.push(op);
    });
    if (opts.label) batch.label = opts.label;
    clearTimeout(batch.timer);
    var cur = batch;
    cur.timer = setTimeout(function () { sendBatch(cur); }, opts.now ? 0 : 120);
    return cur.promise;
  };
  function sendBatch(b) {
    if (batch === b) batch = null;
    enqueue(function () {
      if (b.wd !== S.workdir) {                       // the user switched sequence: apply to the old doc, do not reload
        return cap.worker('doc', { ops: b.ops }, { workdir: b.wd }).promise;
      }
      var before = S.doc ? snapshot(S.doc) : null, t0 = Date.now();
      return cap.worker('doc', { ops: b.ops }, { workdir: b.wd }).promise.then(function (r) {
        cap.stats.ops.push({ n: b.ops.length, ms: Date.now() - t0 });
        if (before) { S.undo.push({ snap: before, label: b.label }); if (S.undo.length > 60) S.undo.shift(); S.redo = []; }
        if (!batch || batch.ops.every(function (o) { return o.op !== 'doc_style'; })) S.draft = null;
        cap.reload();
        return r;
      });
    }).then(function (r) { b.res(r); notes(r); }, function (e) {
      S.draft = null;
      if (b.wd === S.workdir) cap.reload();
      ui.toast(AC.t('cap.toast.opFailed', { msg: U.errMsg(e) }), { kind: 'err' });
      AC.log.error(e, 'captions op');
      b.rej(e);
    });
  }
  function notes(r) {
    ((r && r.notes) || []).forEach(function (n) {
      if (n.op === 'replace') ui.toast(n.n ? AC.t('cap.toast.replaced', { n: n.n }) : AC.t('cap.toast.noMatch'), n.n ? 'check' : 'info');
      if (n.op === 'auto_emoji') ui.toast(n.n ? AC.t('cap.toast.emojiAdded', { n: n.n }) : AC.t('cap.toast.noEmoji'), n.n ? 'check' : 'info');
    });
  }
  // Resolves when every queued edit reached the engine.
  cap.idle = function () {
    var b = batch;
    if (b) { clearTimeout(b.timer); sendBatch(b); }
    return chain.then(function () {});
  };

  // Doc-level template keys: set (value may be null = explicit "none") or reset to the template value.
  cap.setStyle = function (path, value, opts) {
    var st = U.copy(cap.style());
    if (value === undefined) delPath(st, path); else setPath(st, path, value);
    return cap.setStyleAll(st, opts);
  };
  cap.patchStyle = function (patch, opts) { return cap.setStyleAll(cap.merge(cap.style(), patch), opts); };
  cap.setStyleAll = function (st, opts) {
    S.draft = st;
    cap.bus.emit('draft', st);
    return cap.op({ op: 'doc_style', style: st, replace: true }, U.assign({ label: AC.t('cap.undo.style') }, opts || {}));
  };

  function restore(snap) {
    return enqueue(function () {
      var d = cap.readDoc();
      if (!d) throw U.err('NO_CAPTIONS', AC.t('cap.err.docMissing'));
      var now = snapshot(d);
      d.template = snap.template; d.style = snap.style; d.params = snap.params; d.page_over = snap.page_over; d.ranges = snap.ranges;
      d.words.forEach(function (w) {
        delete w.edit; delete w.style;
        var s = snap.words[w.id];
        if (s) { if (s.edit) w.edit = s.edit; if (s.style) w.style = s.style; }
      });
      AC.sys.writeJSON(S.path, d);
      return cap.worker('doc', { ops: [] }).promise.then(function () { cap.reload(); return now; });
    });
  }
  cap.undo = function () {
    return cap.idle().then(function () {
      var it = S.undo.pop();
      if (!it) { ui.toast(AC.t('cap.toast.nothingUndo'), 'info'); return; }
      return restore(it.snap).then(function (now) { S.redo.push({ snap: now, label: it.label }); ui.toast(it.label ? AC.t('cap.toast.undoneWhat', { what: it.label }) : AC.t('cap.toast.undone'), 'undo'); cap.bus.emit('undo'); });
    }).catch(function (e) { ui.toast(AC.t('cap.toast.undoFailed', { msg: U.errMsg(e) }), { kind: 'err' }); });
  };
  cap.redo = function () {
    return cap.idle().then(function () {
      var it = S.redo.pop();
      if (!it) { ui.toast(AC.t('cap.toast.nothingRedo'), 'info'); return; }
      return restore(it.snap).then(function (now) { S.undo.push({ snap: now, label: it.label }); ui.toast(it.label ? AC.t('cap.toast.redoneWhat', { what: it.label }) : AC.t('cap.toast.redone'), 'refresh'); cap.bus.emit('undo'); });
    }).catch(function (e) { ui.toast(AC.t('cap.toast.redoFailed', { msg: U.errMsg(e) }), { kind: 'err' }); });
  };
  cap.canUndo = function () { return S.undo.length > 0; };
  cap.canRedo = function () { return S.redo.length > 0; };

  /* ---------------------------------------------------------------- templates (once per panel session) */
  cap.templates = function (force) {
    if (S.tplP && !force) return S.tplP;
    S.tplP = cap.worker('templates', { options: true, full: true, system_fonts: true }, { workdir: S.workdir || AC.settings.workRoot() }).promise.then(function (r) {
      S.tpl = r; cap.bus.emit('templates', r); return r;
    }, function (e) { S.tplP = null; throw e; });
    return S.tplP;
  };
  cap.defaultTemplate = function () {
    var s = S.seq, set = AC.settings.get('captionTemplate'), full = (S.tpl && S.tpl.full) || {};
    if (set && full[set]) return set;
    if (s && s.h > s.w * 1.15) return 'hormozi_kuning';
    return (S.tpl && S.tpl['default']) || 'tutorial_bersih';
  };

  /* ---------------------------------------------------------------- last used look (engine last_style, per aspect)
     A new document starts from the last used look of its aspect class (vertical / horizontal / square), else the
     Settings default template, else the aspect default. S.useLast = the setup screen will build with it. */
  cap.aspectClass = function (w, h) { var r = (w || 16) / Math.max(1, h || 9); return r < 0.85 ? 'vertical' : r < 1.2 ? 'square' : 'horizontal'; };
  cap.loadLast = function () {
    var s = S.seq, wd = S.workdir;
    if (!s) return Promise.resolve(null);
    return cap.worker('last_style', { op: 'get', w: s.w, h: s.h, thumb: true }).promise.then(function (r) {
      if (S.workdir !== wd) return null;
      S.last = (r && r.last) || null;
      S.useLast = !!(S.last && S.last.ops);
      cap.bus.emit('last', S.last);
      return S.last;
    }, function (e) { AC.log.warn('captions last_style: ' + U.errMsg(e)); return null; });
  };
  // Remember that this document was built from the last used look (Template tab badge + "Pakai template asli").
  cap.fromLast = function (entry) {
    var all = AC.store.get('cap.fromLast', {}) || {};
    if (entry === undefined) {
      var e = all[S.workdir], d = S.doc;
      return e && d && e.template === d.template && e.style === JSON.stringify(d.style || {}) ? e : null;
    }
    if (entry) all[S.workdir] = { template: entry.ops[0].id, style: JSON.stringify(entry.ops[1].style || {}), name: entry.name, at: Date.now() };
    else delete all[S.workdir];
    var keys = Object.keys(all).sort(function (a, b) { return all[b].at - all[a].at; });
    keys.slice(30).forEach(function (k) { delete all[k]; });
    AC.store.set('cap.fromLast', all);
    return null;
  };

  /* ---------------------------------------------------------------- Premiere state of our tracks */
  cap.hostStatus = function () {
    if (!S.seq || !AC.host.available) return Promise.resolve(null);
    var id = S.seq.id;
    return AC.host.json('bac_captions_status', id).then(function (st) {
      if (!S.seq || S.seq.id !== id) return null;
      S.placed = st; cap.bus.emit('placed', st); cap.paintDock(); return st;
    }, function (e) { AC.log.warn('bac_captions_status: ' + U.errMsg(e)); return null; });
  };
  // Is the current overlay render the clip on the "Klipora Captions" track (or the old "AutoCut Captions" one)?
  cap.placedVersion = function () {
    var p = S.placed, r = S.doc && S.doc.render;
    if (!p || !p.path || !r || !r.path) return null;
    return p.path.toLowerCase().replace(/\//g, '\\') === String(r.path).toLowerCase().replace(/\//g, '\\') ? r.version : null;
  };

  /* ---------------------------------------------------------------- open / restore per sequence */
  cap.open = function (force) {
    var s = AC.seq.peek();
    if (!s) { S.seq = null; S.doc = null; showView('empty'); return; }
    var same = S.seq && S.seq.id === s.id && S.seq.name === s.name;
    if (same && !force && S.view !== 'empty') { if (S.doc) { checkStale(); cap.hostStatus(); } return; }
    if (batch) cap.idle();
    S.seq = { id: s.id, name: s.name, w: s.width, h: s.height, fps: s.fps, duration: s.duration };
    S.workdir = AC.settings.workdir(s.name);
    S.path = AC.sys.join(S.workdir, 'captions.json');
    S.undo = []; S.redo = []; S.draft = null; S.stale = null; S.placed = null; S.last = null; S.useLast = false;
    var d = cap.readDoc();
    cap.setDoc(d);
    showView(d ? 'editor' : 'setup');
    cap.templates().catch(function (e) { AC.log.error(e, 'captions templates'); });
    cap.loadLast();
    if (d) { checkStale(); cap.hostStatus(); }
  };

  var staleT = null;
  function checkStale() {
    clearTimeout(staleT);
    staleT = setTimeout(function () {
      if (!S.doc || S.busy) return;
      var id = S.seq && S.seq.id;
      cap.worker('doc', { check_only: true }, { withSeq: true }).promise.then(function (r) {
        if (!S.seq || S.seq.id !== id) return;
        var other = S.doc && S.doc.seq && S.doc.seq.id && S.doc.seq.id !== id;
        S.stale = r && (r.stale || other) ? { stale: true, other: other } : null;
        cap.bus.emit('stale', S.stale);
      }, function (e) { AC.log.warn('captions check: ' + U.errMsg(e)); });
    }, 400);
  }
  cap.checkStale = checkStale;

  /* ---------------------------------------------------------------- views */
  var V = {};
  function showView(name) {
    S.view = name;
    if (!V.root) return;
    V.empty.hidden = name !== 'empty'; V.setup.hidden = name !== 'setup'; V.editor.hidden = name !== 'editor';
    if (S.ctx && S.ctx.source) S.ctx.source.el.hidden = name === 'editor';
    if (name === 'setup') cap.paintSetup();
    if (name === 'editor') buildEditor();
    if (cap.preview) cap.preview.active(name === 'editor' && S.ctx && S.ctx.isActive() && S.ctx.pane() === 'main');
    cap.paintDock();
  }
  cap.showView = showView;

  function buildEditor() {
    if (V.built) { selectTab(S.tab, true); return; }
    V.built = true;
    var layout = ui.h('div', { class: 'cap-layout' });
    V.prev = cap.preview.create();
    layout.appendChild(V.prev.el);
    var side = ui.h('div', { class: 'cap-side' });
    V.head = ui.h('div', { class: 'cap-head' });
    side.appendChild(V.head);
    V.banner = ui.h('div', { class: 'cap-banner' });
    side.appendChild(V.banner);
    var tabs = ui.h('div', { class: 'cap-tabs', role: 'tablist', 'aria-label': AC.t('cap.editorAria') });
    V.tabBtns = {}; V.panels = {};
    cap.TABS.forEach(function (t, i) {
      var label = cap.tabLabel(t.id);
      var b = ui.h('button', { type: 'button', role: 'tab', id: 'cap-tab-' + t.id, 'aria-controls': 'cap-panel-' + t.id, title: label + ' (Alt ' + (i + 1) + ')', html: ui.icon(t.icon) + U.esc(label), dataset: { t: t.id } });
      b.addEventListener('click', function () { selectTab(t.id); });
      tabs.appendChild(b); V.tabBtns[t.id] = b;
      var p = ui.h('div', { class: 'cap-panel', role: 'tabpanel', id: 'cap-panel-' + t.id, 'aria-labelledby': 'cap-tab-' + t.id, dataset: { p: t.id } });
      V.panels[t.id] = p;
    });
    tabs.addEventListener('keydown', function (e) {
      if (e.key !== 'ArrowRight' && e.key !== 'ArrowLeft') return;
      var i = TAB_IDS.indexOf(S.tab), n = TAB_IDS[(i + (e.key === 'ArrowRight' ? 1 : TAB_IDS.length - 1)) % TAB_IDS.length];
      e.preventDefault(); selectTab(n); V.tabBtns[n].focus();
    });
    side.appendChild(tabs);
    TAB_IDS.forEach(function (id) { side.appendChild(V.panels[id]); });
    layout.appendChild(side);
    V.editor.appendChild(layout);
    cap.styleUI.template(V.panels.template);
    cap.text.create(V.panels.teks);
    cap.styleUI.gaya(V.panels.gaya);
    cap.styleUI.animasi(V.panels.animasi);
    cap.exportUI.create(V.panels.ekspor);
    paintHead(); paintBanner();
    selectTab(S.tab, true);
  }

  function selectTab(id, silent) {
    if (TAB_IDS.indexOf(id) < 0) id = 'template';
    S.tab = id;
    if (S.ctx) { S.ctx.state.tab = id; S.ctx.save(); }
    if (!V.tabBtns) return;
    TAB_IDS.forEach(function (t) {
      var on = t === id;
      V.tabBtns[t].setAttribute('aria-selected', on ? 'true' : 'false');
      V.tabBtns[t].tabIndex = on ? 0 : -1;
      V.panels[t].classList.toggle('is-on', on);
    });
    if (!silent && S.ctx && S.ctx.isActive()) { try { history.replaceState(null, '', '#tool/captions/' + id); } catch (e) { /* file:// */ } }
    cap.bus.emit('tab', id);
  }
  cap.selectTab = selectTab;

  function paintHead() {
    if (!V.head) return;
    var d = S.doc, st = (d && d.stats) || {};
    V.head.innerHTML = '';
    V.head.appendChild(ui.h('span', { html: ui.icon('cc') }));
    V.head.appendChild(ui.h('b', { text: S.seq ? S.seq.name : '', title: S.seq ? S.seq.name : '', 'data-i18n-skip': true }));
    V.head.appendChild(ui.h('span', { text: AC.t('cap.head.pages', { n: st.pages || 0 }) + ', ' + AC.t('cap.head.words', { n: st.words || 0 }) }));
    V.head.appendChild(ui.h('span', { class: 'grow' }));
    var v = cap.placedVersion();
    if (v) V.head.appendChild(ui.html(ui.tag(d.hash === (d.render || {}).hash ? AC.t('cap.head.onTimeline', { v: v }) : AC.t('cap.head.changed'), d.hash === (d.render || {}).hash ? 'ok' : 'warn', d.hash === (d.render || {}).hash ? 'check' : 'alert')));
  }
  function paintBanner() {
    if (!V.banner) return;
    V.banner.innerHTML = '';
    if (S.stale && S.stale.stale) {
      V.banner.appendChild(ui.alert({ kind: 'warn', title: S.stale.other ? AC.t('cap.stale.other') : AC.t('cap.stale.title'),
        text: AC.t('cap.stale.text'),
        actions: [{ label: AC.t('cap.stale.action'), icon: 'refresh', onClick: function () { cap.build(true); } }] }));
    }
  }
  cap.bus.on('doc', function () { paintHead(); });
  cap.bus.on('placed', function () { paintHead(); });
  cap.bus.on('templates', function () { if (V.fillTpl && S.view === 'setup') V.fillTpl(); });
  cap.bus.on('last', function () { if (S.view === 'setup' && V.setup) cap.paintSetup(); });
  cap.bus.on('stale', function () { paintBanner(); });

  /* ---------------------------------------------------------------- setup (no document yet) */
  cap.paintSetup = function () {
    var ctx = S.ctx, st = ctx.state, el = V.setup;
    el.innerHTML = '';
    el.appendChild(ui.h('div', { class: 'tool-hero', html: '<span class="tool-ic">' + ui.icon('cc') + '</span><div><p>' + U.esc(ctx.def.lead) + '</p><div class="tags">' + ui.badge('gpu') + ui.tag(AC.t('cap.setup.overlayTag'), 'line') + '</div></div>' }));
    var full = AC.seq.peek() ? true : false;
    if (!full) return;
    // Look to start with: the last used look for this aspect (engine last_style), else a template
    var sec = ui.section({ title: AC.t('cap.setup.startTpl'), aside: AC.t('cap.setup.changeLater') });
    var tplSel = ui.select({ ariaLabel: AC.t('cap.setup.startTpl'), options: [{ value: '', label: AC.t('cap.setup.loadingTpl') }], onChange: function (v) { st.startTpl = v; ctx.save(); } });
    var lastBox = ui.h('div', { class: 'cap-last-start' });
    sec.add([lastBox, tplSel]);
    var L = S.last;
    if (L && L.params && S.lastPrefsFor !== S.workdir) {        // text rules of the last used look, once per sequence
      S.lastPrefsFor = S.workdir;
      if (L.params.hide_fillers !== undefined) st.hideFillers = !!L.params.hide_fillers;
      if (L.params.numbers !== undefined) st.numbers = !!L.params.numbers;
      if (L.params.censor) st.censor = L.params.censor;
      if (Array.isArray(L.params.glossary) && L.params.glossary.length) st.glossary = L.params.glossary.slice(0, 50);
      ctx.save();
    }
    if (L && S.useLast) {
      tplSel.el.hidden = true;
      var card = ui.h('div', { class: 'cap-last-card' });
      var th = ui.h('div', { class: 'cap-tpl-thumb' });
      if (L.thumb) th.appendChild(ui.h('img', { src: cap.fileUrl(L.thumb), alt: '' }));
      card.appendChild(th);
      var when = L.updated_ms ? U.dayLabel(L.updated_ms) + ' ' + U.wibTime(L.updated_ms) : '';
      card.appendChild(ui.h('div', { class: 'cap-last-meta', html: ui.tag(AC.t('cap.setup.lastLook'), 'ai', 'refresh') + '<b data-i18n-skip>' + U.esc(L.name || L.template) + '</b>' +
        '<small>' + (L.seq ? AC.i18n.html('cap.setup.fromSeq', { name: L.seq }) + (when ? ', ' : '') : '') + U.esc(when) + '</small>' }));
      lastBox.appendChild(card);
      lastBox.appendChild(ui.button({ label: AC.t('cap.setup.useOriginal'), icon: 'undo', small: true, kind: 'ghost', title: AC.t('cap.setup.useOriginalTitle'),
        onClick: function () { S.useLast = false; cap.paintSetup(); } }));
    } else if (L) {
      lastBox.appendChild(ui.button({ label: AC.t('cap.setup.useLast'), icon: 'refresh', small: true, kind: 'ghost',
        onClick: function () { S.useLast = true; cap.paintSetup(); } }));
    }
    V.fillTpl = function () {
      if (!S.tpl || !tplSel.el.isConnected) return;
      var def = cap.defaultTemplate();
      tplSel.setOptions(S.tpl.templates.map(function (r) { return { value: r.id, label: r.id === def ? AC.t('cap.setup.recommended', { name: r.name }) : r.name }; }));
      tplSel.set(st.startTpl && S.tpl.full[st.startTpl] ? st.startTpl : def, true);
    };
    el.appendChild(sec.el);
    V.fillTpl();                                     // after appending: fillTpl skips detached selects
    // Text rules applied while building (all editable afterwards)
    var tx = ui.section({ title: AC.t('cap.setup.text') });
    var fill = ui.switch({ label: AC.t('cap.setup.hideFillers'), help: AC.t('cap.setup.hideFillersHelp'), checked: st.hideFillers !== false, onChange: function (v) { st.hideFillers = v; ctx.save(); } });
    var num = ui.switch({ label: AC.t('cap.setup.numbers'), help: AC.t('cap.setup.numbersHelp'), checked: st.numbers !== false, onChange: function (v) { st.numbers = v; ctx.save(); } });
    var cen = ui.select({ ariaLabel: AC.t('cap.setup.censorAria'), width: '170px', value: st.censor || 'auto',
      options: [{ value: 'auto', label: AC.t('cap.setup.censorAuto') }, { value: 'stars', label: 'a****g' }, { value: 'first', label: 'a*****' }, { value: 'full', label: '******' }, { value: 'off', label: AC.t('cap.setup.censorOff') }],
      onChange: function (v) { st.censor = v; ctx.save(); } });
    tx.add([fill, num, ui.inlineField({ label: AC.t('cap.setup.profanity'), control: cen.el })]);
    el.appendChild(tx.el);
    var gl = ui.section({ title: AC.t('cap.setup.glossary'), aside: AC.t('cap.setup.glossaryAside') });
    gl.add(ui.h('p', { class: 'help', text: AC.t('cap.setup.glossaryHelp') }));
    var terms = (st.glossary || []).slice();
    var chips = ui.chips({ options: [], multi: true, ariaLabel: AC.t('cap.setup.glossary'), onChange: function () {} });
    chips.el.setAttribute('data-i18n-skip', '');
    function paintTerms() {
      chips.setOptions(terms.map(function (x) { return { value: x, label: x, icon: 'x', title: AC.t('cap.setup.removeTerm', { term: x }) }; }));
      chips.set(terms, true);
    }
    chips.el.addEventListener('click', function (e) {
      var b = e.target.closest('.chip'); if (!b) return;
      var i = U.$$('.chip', chips.el).indexOf(b);
      if (i >= 0 && i < terms.length) { terms.splice(i, 1); st.glossary = terms.slice(); ctx.save(); paintTerms(); }
    }, true);
    var inp = ui.input({ placeholder: AC.t('cap.setup.addTermPh'), ariaLabel: AC.t('cap.setup.addTermAria') });
    inp.el.addEventListener('keydown', function (e) {
      if (e.key !== 'Enter') return;
      e.preventDefault();
      var v = inp.get().trim();
      if (v && terms.indexOf(v) < 0) { terms.push(v); st.glossary = terms.slice(); ctx.save(); paintTerms(); }
      inp.set('');
    });
    paintTerms();
    gl.add([chips, inp]);
    el.appendChild(gl.el);
    var s = AC.seq.peek();
    if (s && s.duration > 900) el.appendChild(ui.alert({ kind: 'info', title: AC.t('cap.setup.longTitle', { dur: U.mmss(s.duration) }), text: AC.t('cap.setup.longText') }));
  };

  // Build (or rebuild after timeline edits) the document. Rebuild keeps every user edit (engine carry-over).
  cap.build = function (rebuild) {
    var ctx = S.ctx, st = ctx.state;
    if (rebuild && !S.doc) rebuild = false;
    var params = rebuild ? { scope: (S.doc.params || {}).scope || { kind: 'all' } } : {
      template: st.startTpl && S.tpl && S.tpl.full[st.startTpl] ? st.startTpl : cap.defaultTemplate(),
      hide_fillers: st.hideFillers !== false, numbers: st.numbers !== false, censor: st.censor || 'auto',
      glossary: (st.glossary || []).slice()
    };
    var last = !rebuild && S.useLast && S.last && S.last.ops ? S.last : null;
    if (last) { params.template = last.ops[0].id; params.ops = [last.ops[1]]; }
    var go = function () {
      var task = ctx.run({ action: 'doc', params: params, title: rebuild ? AC.t('cap.build.updating') : AC.t('cap.build.creating'), workdir: S.workdir, scope: rebuild ? false : undefined,
        stages: [{ id: 'words', label: AC.t('cap.build.stageWords'), w: 0.75 }, { id: 'doc', label: AC.t('cap.build.stageDoc'), w: 0.25 }],
        onResult: function (data) {
          S.draft = null; S.stale = null; cap.bus.emit('stale', null);
          if (!rebuild) { S.tab = 'template'; S.undo = []; S.redo = []; }
          if (!rebuild) cap.fromLast(last);
          var d = cap.reload();
          showView(d ? 'editor' : 'setup');
          AC.router.go('tool/captions' + (d ? '/' + S.tab : ''), { replace: true });
          var info = (data && data.stats) || {};
          var what = AC.t('cap.head.pages', { n: info.pages || 0 }) + (info.fixes ? AC.t('cap.build.fixes', { n: info.fixes }) : '');
          ui.toast(AC.t(rebuild ? 'cap.build.updated' : 'cap.build.ready', { what: what }), 'check');
          cap.hostStatus();
        } });
      cap.hold(task);
      return task;
    };
    return rebuild ? cap.idle().then(go) : go();
  };

  /* ---------------------------------------------------------------- dock */
  cap.paintDock = function () {
    var ctx = S.ctx; if (!ctx) return;
    if (S.view === 'setup') {
      ctx.dock({ primary: { label: AC.t('cap.cta'), icon: 'cc', onClick: function () { cap.build(false); } } });
    } else if (S.view === 'editor' && cap.exportUI) {
      var p = cap.exportUI.primary();
      // "Simpan gaya" = save the current look into Gaya Saya (captions_style.js save sheet)
      ctx.dock({ primary: p, left: cap.styleUI && cap.styleUI.saveDialog ? [{ label: AC.t('cap.dock.saveStyle'), icon: 'star', id: 'cap-save-style', title: AC.t('cap.dock.saveStyleTitle'), onClick: function () { cap.styleUI.saveDialog(); } }] : [] });
    } else {
      ctx.dock({ primary: { label: AC.t('cap.cta'), icon: 'cc', disabled: true, title: AC.t('cap.dock.openSeq') } });
    }
  };

  /* ---------------------------------------------------------------- keys */
  function onKey(e) {
    if (S.view !== 'editor') return false;
    var k = e.key || '', ctrl = e.ctrlKey || e.metaKey, typing = AC.keys.typing();
    if (e.altKey && /^[1-5]$/.test(k)) { e.preventDefault(); selectTab(TAB_IDS[Number(k) - 1]); return true; }
    if (ctrl && !typing && (k === 'z' || k === 'Z')) { e.preventDefault(); if (e.shiftKey) cap.redo(); else cap.undo(); return true; }
    if (ctrl && !typing && (k === 'y' || k === 'Y')) { e.preventDefault(); cap.redo(); return true; }
    if (ctrl && (k === 'f' || k === 'F' || k === 'h' || k === 'H')) { e.preventDefault(); selectTab('teks'); cap.text.find(); return true; }
    if (S.tab === 'teks' && cap.text && cap.text.onKey(e, typing)) return true;
    return false;
  }

  /* ---------------------------------------------------------------- registration */
  AC.tools.register({
    id: 'captions', group: 'teks', order: 1,
    icon: 'cc',
    badges: ['gpu'],
    text: function (t) {
      return { title: t('cap.title'), tab: t('cap.tabName'), desc: t('cap.desc'), lead: t('cap.lead'), cta: t('cap.cta'),
               next: [{ tool: 'chapters', reason: t('cap.nextChapters') }] };
    },
    defaults: { tab: 'template', out: 'overlay', srtToo: false, codec: 'qtrle', band: 'auto', safe: true, follow: true, hideFillers: true, numbers: true, censor: 'auto', glossary: [] },

    render: function (el, ctx) {
      S.ctx = ctx;
      S.tab = TAB_IDS.indexOf(ctx.state.tab) >= 0 ? ctx.state.tab : 'template';
      AC.keys.register([{ keyCode: 90, ctrlKey: true }, { keyCode: 90, ctrlKey: true, shiftKey: true }, { keyCode: 89, ctrlKey: true },
                        { keyCode: 70, ctrlKey: true }, { keyCode: 72, ctrlKey: true }, { keyCode: 9 }, { keyCode: 9, shiftKey: true }]);
      V = { root: el };                              // fresh refs: render() runs again after a language switch
      V.empty = ui.h('div', { class: 'tool-pane', hidden: true });
      V.empty.appendChild(ui.empty({ icon: 'seq', title: AC.t('cap.empty.title'), text: AC.t('cap.empty.text'),
        steps: [AC.t('cap.empty.step1'), AC.t('cap.empty.step2'), AC.t('cap.empty.step3')], action: { label: AC.t('cap.empty.redetect'), icon: 'refresh', onClick: function () { AC.seq.refresh(); } } }));
      V.setup = ui.h('div', { class: 'tool-pane cap-setup', hidden: true });
      V.editor = ui.h('div', { class: 'tool-pane cap-editor', hidden: true });
      el.appendChild(V.empty); el.appendChild(V.setup); el.appendChild(V.editor);
      cap.open(true);
    },
    onShow: function (ctx) {
      if (TAB_IDS.indexOf(ctx.sub) >= 0) S.tab = ctx.sub;
      cap.open();
      if (S.view === 'editor') selectTab(S.tab, true);
      if (cap.preview) cap.preview.active(S.view === 'editor');
      cap.paintDock();
    },
    onHide: function () {
      if (cap.preview) cap.preview.active(false);
      if (cap.text) cap.text.closePop();
    },
    // language switch: the page is dropped and rendered again (stop the playhead poll / loop of the old one)
    onUnmount: function () {
      if (cap.preview) cap.preview.active(false);
      if (cap.text && cap.text.closePop) cap.text.closePop();
    },
    onSeq: function (lite, ctx) {
      if (!lite) { cap.open(); return; }
      if (!S.seq || lite.id !== S.seq.id || lite.name !== S.seq.name) { cap.open(); return; }
      S.seq.w = lite.width; S.seq.h = lite.height; S.seq.fps = lite.fps; S.seq.duration = lite.duration;
      // re-check only when the cut could have changed (selection clicks also fire 'change')
      var sig = JSON.stringify([lite.duration, lite.spans, (lite.audio || []).map(function (t) { return [t.count, t.muted]; }), lite.width, lite.height]);
      if (S.doc && ctx.isActive() && sig !== S.seqSig) { S.seqSig = sig; checkStale(); }
    },
    onKey: function (e) { return onKey(e); }
  });
})();
