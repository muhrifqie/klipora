/* captions_style.js: Auto Caption "Template", "Gaya" and "Animasi" tabs (AC.cap.styleUI) + the "Gaya Saya"
   library (AC.cap.lib). Engine: docs/CAPTIONS_API.md 3.2-3.3.1 (templates, gallery), 3.12 (fonts), 3.13-3.14 (mix,
   random), 13 (library, last_style), 6.5 (option metadata). Docs: docs/tools/captions_ui.md.
   Template: search, "Acak gaya" (template_random), "Gabung gaya" (template_mix: parts from 2 templates), badge
     "Gaya terakhir dipakai" + "Pakai template asli", Gaya Saya (engine `library`: favourites first, menu Ganti nama /
     Duplikat / Favorit / Ekspor / Hapus, "Impor gaya"), Terakhir dipakai (engine last_style), built-in groups with
     filter chips. Cards show the static engine thumbnail and play the animated sprite strip (`gallery {anim}`, CSS
     steps()) on hover / focus; static when motion is reduced. Pick = op template (Ctrl Z undoes it).
   Gaya ("Gaya Pro"): controls generated from options.schema / options.groups (every Pro key) + special pickers
     (font picker with categories, bundled / Windows badge and a live sample in the real font; background shape
     tiles; active-word looks; position grid; auto position). Collapsible groups with reset per group. Every change
     = op doc_style with the full doc.style (explicit nulls kept), batched 120 ms, undoable, then a new preview.
   Last used look: an explicit style change or a new overlay render sends template + style + text rules to the
     engine (`last_style set`, debounced) for the sequence's aspect class; captions.js starts new docs from it.
   Animasi: classic pickers (captions_anim.js renders the studio around them when loaded). */
(function () {
  'use strict';
  var AC = window.AC, U = AC.util, ui = AC.ui, cap = AC.cap, S = cap.S;

  var TEXT_COLORS = ['#FFFFFF', '#FFE600', '#FFD24A', '#22E58B', '#7DF9FF', '#FE2C55', '#111111'];
  var ACTIVE_COLORS = ['#FFE600', '#FFE066', '#22E58B', '#38BDF8', '#FE2C55', '#A78BFA'];
  var LINE_COLORS = ['#000000', '#FFFFFF', '#1F2937', '#7C3AED', '#FE2C55'];
  var BOX_COLORS = ['#000000', '#0B0F19', '#FFFFFF', '#7C3AED', '#FE2C55', '#FFE600'];
  var GLOW_COLORS = ['#FF2BD6CC', '#22E58BCC', '#38BDF8CC', '#FFE600CC', '#FFFFFFCC', '#FF8A00CC'];
  var ANIM_IN = { none: '', fade: 'cap-in-fade', pop: 'cap-in-pop', scale: 'cap-in-scale', zoom: 'cap-in-zoom', slide_up: 'cap-in-up', slide_down: 'cap-in-down',
                  slide_left: 'cap-in-left', slide_right: 'cap-in-right', bounce: 'cap-in-bounce', blur: 'cap-in-blur', zoom_blur: 'cap-in-zoomblur',
                  rotate: 'cap-in-rotate', drop: 'cap-in-drop', typewriter: 'cap-in-type' };
  var ANIM_OUT = { none: '', fade: 'cap-out-fade', pop: 'cap-out-pop', scale: 'cap-out-scale', zoom: 'cap-out-zoom', slide_up: 'cap-out-up', slide_down: 'cap-out-down', blur: 'cap-out-blur' };
  var HL = { none: '', color: 'cap-hl-color', scale: 'cap-hl-scale', pill: 'cap-hl-pill', sweep: 'sweep', underline: 'cap-hl-under', dim: 'cap-hl-dim', reveal: 'cap-hl-reveal', one_word: 'cap-hl-one' };
  // labels are locale keys, translated at render time (the language can change while the panel runs)
  var SHAPE_KEY = { none: 'cap.style.none', line: 'cap.style.shapeLine', rounded: 'cap.style.shapeRounded', page: 'cap.style.shapePage', word: 'cap.style.shapeWord',
                    marker: 'cap.style.shapeMarker', banner: 'cap.style.shapeBanner', ribbon: 'cap.style.shapeRibbon', outline: 'cap.style.shapeOutline', sticker: 'cap.style.shapeSticker' };
  var MIX_PARTS = [['font', 'cap.gallery.mixFont'], ['style', 'cap.gallery.mixStyle'], ['box', 'cap.gallery.mixBox'], ['highlight', 'cap.gallery.mixHighlight'], ['anim', 'cap.gallery.mixAnim'], ['layout', 'cap.gallery.mixLayout']];
  var META_KEYS = ['id', 'source', 'name', 'description', 'aspect', 'default_for', 'tags', 'group', 'groups', 'base'];

  ui.addIcons('<symbol id="i-star" viewBox="0 0 20 20"><path d="M10 2.8l2.2 4.6 5 .7-3.6 3.5.9 5-4.5-2.4-4.5 2.4.9-5L2.8 8.1l5-.7z"/></symbol>' +
    '<symbol id="i-shuffle" viewBox="0 0 20 20"><path d="M3 6h3c4 0 5 8 9 8h2M3 14h3c1.5 0 2.5-1.2 3.3-2.7M12.4 8.4C13.2 7 14.1 6 15.5 6H17M14.5 3.5 17 6l-2.5 2.5M14.5 11.5 17 14l-2.5 2.5"/></symbol>' +
    '<symbol id="i-import" viewBox="0 0 20 20"><path d="M10 3v9M6.5 8.5 10 12l3.5-3.5"/><path d="M4 10.5v5A1.5 1.5 0 0 0 5.5 17h9a1.5 1.5 0 0 0 1.5-1.5v-5"/></symbol>');

  function tplRow(id) { var l = (S.tpl && S.tpl.templates) || []; for (var i = 0; i < l.length; i++) if (l[i].id === id) return l[i]; return null; }
  function optLabel(list, id) { for (var i = 0; i < (list || []).length; i++) if (list[i].id === id) return list[i].label; return id || ''; }
  function eff() { return cap.eff() || {}; }
  function g(path) { return cap.get(eff(), path); }
  function set(path, v, label) { return cap.setStyle(path, v, { label: label || AC.t('cap.style.opStyle') }); }
  function opts() { return (S.tpl && S.tpl.options) || {}; }
  function reducedMotion() {
    return document.documentElement.getAttribute('data-motion') === 'reduce' || !!(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches);
  }
  function fileName(name) { return (U.safeName ? U.safeName(name) : String(name).replace(/[\\/:*?"<>|]+/g, ' ')).trim() || AC.t('cap.gallery.fileFallback'); }
  // A full look (template + doc.style) without the metadata keys: doc_style replace with it reproduces the look
  // on any base template (used before deleting a user template the document still uses).
  function lookOf(full) { var o = cap.merge(full || {}, cap.style()); META_KEYS.forEach(function (k) { delete o[k]; }); return o; }

  cap.styleUI = cap.styleUI || {};

  /* ================================================================ Gaya Saya library (engine `library`) */
  var LIB = cap.lib = { rows: null, p: null, seq: 0 };
  function libCall(params) { return cap.worker('library', params, { workdir: S.workdir || AC.settings.workRoot() }).promise; }
  function sortRows() {
    (LIB.rows || []).sort(function (a, b) { return (b.favorite ? 1 : 0) - (a.favorite ? 1 : 0) || (b.updated_ms || 0) - (a.updated_ms || 0) || String(a.name).localeCompare(String(b.name)); });
  }
  LIB.load = function (force) {
    if (LIB.p && !force) return LIB.p;
    LIB.p = libCall({ op: 'list' }).then(function (r) {
      LIB.rows = (r && r.templates) || []; sortRows(); cap.bus.emit('library', LIB.rows); return LIB.rows;
    }, function (e) { LIB.p = null; LIB.rows = LIB.rows || []; AC.log.warn('captions library: ' + U.errMsg(e)); cap.bus.emit('library', LIB.rows); return LIB.rows; });
    return LIB.p;
  };
  function upsert(row, replaceId) {
    LIB.rows = (LIB.rows || []).filter(function (r) { return r.id !== row.id && r.id !== replaceId; });
    LIB.rows.push(row); sortRows(); cap.bus.emit('library', LIB.rows);
  }
  function dropRow(id) { LIB.rows = (LIB.rows || []).filter(function (r) { return r.id !== id; }); cap.bus.emit('library', LIB.rows); }
  LIB.row = function (id) { var l = LIB.rows || []; for (var i = 0; i < l.length; i++) if (l[i].id === id) return l[i]; return null; };

  // Save the current look into Gaya Saya. The card appears at once (placeholder), the engine row replaces it.
  // opts.template: save that template object instead of the doc look (Tiru gaya dari gambar), opts.description.
  LIB.save = function (name, opts) {
    opts = opts || {};
    var fromDoc = !opts.template;
    if (fromDoc && !S.doc) return Promise.reject(U.err('NO_CAPTIONS', AC.t('cap.gallery.noDoc')));
    var tmpId = '__saving_' + (++LIB.seq), nm = String(name || '').trim() || AC.t('cap.gallery.defaultName');
    upsert({ id: tmpId, name: nm, source: 'user', saving: true, favorite: false, updated_ms: Date.now() + 1e9, colors: [] });
    return (fromDoc ? cap.idle() : Promise.resolve()).then(function () {
      return libCall(fromDoc ? { op: 'save', name: nm, from_doc: true }
        : { op: 'save', name: nm, from_doc: false, template: opts.template, description: opts.description });
    }).then(function (r) {
      upsert(r.template, tmpId);
      cap.templates(true);
      ui.toast(AC.t('cap.gallery.saved', { name: r.template.name }), { icon: 'check', action: { label: AC.t('common.view'), run: function () { showCard(r.id); } } });
      return r.template;
    }, function (e) { dropRow(tmpId); ui.toast(AC.t('cap.gallery.saveFail', { msg: U.errMsg(e) }), { kind: 'err' }); throw e; });
  };
  function showCard(id) {
    cap.selectTab('template');
    try { history.replaceState(null, '', '#tool/captions/template'); } catch (e) { /* file:// */ }
    setTimeout(function () { var c = TP.mineGrid && TP.mineGrid.querySelector('[data-id="' + id + '"]'); if (c) { c.scrollIntoView({ block: 'center' }); c.focus(); } }, 60);
  }

  /* ---------------------------------------------------------------- "Simpan gaya" sheet (dock action) */
  var SV = {};
  cap.styleUI.saveDialog = function (prefill) {
    if (!S.doc) return;
    if (SV.el && SV.lang !== AC.i18n.lang() && SV.el.hidden) { if (SV.el.parentNode) SV.el.parentNode.removeChild(SV.el); SV.el = null; }   // built in another language
    if (!SV.el) {
      SV.lang = AC.i18n.lang();
      SV.input = ui.h('input', { class: 'input', type: 'text', maxlength: '60', placeholder: AC.t('cap.style.saveNamePh'), 'aria-label': AC.t('cap.style.saveNameAria'), spellcheck: 'false' });
      SV.ok = ui.button({ label: AC.t('common.save'), icon: 'check', kind: 'primary', small: true, onClick: function () { SV.submit(); } });
      SV.el = ui.h('div', { class: 'cap-save', role: 'dialog', 'aria-label': AC.t('cap.style.saveAria'), hidden: true }, [
        ui.h('div', { class: 'cap-save-h', html: ui.icon('star') + AC.t('cap.style.saveHeadHtml') }),
        ui.h('div', { class: 'cap-save-row' }, [SV.input, SV.ok, ui.button({ label: AC.t('common.cancel'), kind: 'ghost', small: true, onClick: function () { SV.close(); } })])
      ]);
      SV.input.addEventListener('keydown', function (e) { if (e.key === 'Enter') { e.preventDefault(); SV.submit(); } });
      document.body.appendChild(SV.el);
      if (!SV.hooked) { SV.hooked = true; AC.bus.on('route', function () { if (SV.close) SV.close(); }); }
    }
    SV.submit = submit; SV.close = close;
    var row = S.doc && tplRow(S.doc.template);
    SV.input.value = prefill || AC.t('cap.style.saveDefault', { name: row ? row.name : AC.t('cap.style.opStyle') });
    if (SV.el.hidden) SV.ret = document.activeElement;   // focus goes back to the opener on close
    SV.el.hidden = false;
    SV.input.focus(); SV.input.select();
    if (SV.unEsc) SV.unEsc();
    SV.unEsc = AC.keys.onEscape(function () { if (SV.el.hidden) return false; close(); return true; });
    function submit() { var nm = SV.input.value.trim(); if (!nm) { SV.input.focus(); return; } close(); LIB.save(nm); }
    function close() {
      var was = SV.el && !SV.el.hidden;
      if (SV.el) SV.el.hidden = true;
      if (SV.unEsc) { SV.unEsc(); SV.unEsc = null; }
      if (was && SV.ret && document.body.contains(SV.ret) && SV.ret.focus) SV.ret.focus();
      SV.ret = null;
    }
  };
  cap.styleUI.saveTemplate = function (input) {          // compat: an input with a name (old callers)
    var nm = input && input.value ? input.value.trim() : '';
    if (!nm) { cap.styleUI.saveDialog(); return; }
    input.value = '';
    LIB.save(nm);
  };

  /* ---------------------------------------------------------------- last used look: remember (engine last_style set) */
  var REM = { wd: null, sig: '', rv: 0, t: null };
  function sigOf(d) { var p = d.params || {}; return JSON.stringify([d.template, d.style || {}, p.hide_fillers, p.numbers, p.censor, p.glossary || []]); }
  function remember(reason) {
    clearTimeout(REM.t);
    REM.t = setTimeout(function () {
      var d = S.doc, s = S.seq;
      if (!d || !s) return;
      cap.worker('last_style', { op: 'set', template: d.template, style: d.style || {}, params: d.params || {}, w: s.w, h: s.h, seq_name: s.name, reason: reason }).promise
        .then(function () {}, function (e) { AC.log.warn('captions last_style set: ' + U.errMsg(e)); });
    }, 1500);
  }
  cap.bus.on('doc', function (d) {
    if (!d) return;
    var sig = sigOf(d), rv = (d.render && d.render.version) || 0;
    if (REM.wd !== S.workdir) { REM.wd = S.workdir; REM.sig = sig; REM.rv = rv; return; }   // just opened: not a "use"
    if (sig !== REM.sig) { REM.sig = sig; REM.rv = rv; remember('style'); return; }
    if (rv > REM.rv) { REM.rv = rv; remember('render'); }
  });

  // Language switch: template names / groups / Gaya Pro schema labels come from the engine in the job language, so
  // fetch them again (the 'templates' event repaints the Template and Gaya tabs with the new labels).
  AC.bus.on('locale', function () { FP.p = null; if (S.tpl || S.tplP) cap.templates(true).then(null, function (e) { AC.log.warn('captions templates (locale): ' + U.errMsg(e)); }); });

  /* ================================================================ Template */
  var TP = { group: 'all', q: '', thumbs: {}, anims: {}, framed: false, galleryP: null, animP: null, mixOpen: false };
  cap.styleUI.template = function (panel) {
    // render() can run again (language switch): forget the painted keys of the previous render
    closeMenu();
    TP.key = TP.ckey = TP.mkey = null; TP.q = ''; TP.mixOpen = false;
    var search = ui.h('input', { class: 'input cap-tpl-search', type: 'search', placeholder: AC.t('cap.gallery.searchPh'), 'aria-label': AC.t('cap.gallery.searchAria'), spellcheck: 'false' });
    search.addEventListener('input', U.debounce(function () { TP.q = search.value.trim().toLowerCase(); paint(); paintMine(); }, 120));
    var bRand = ui.button({ label: AC.t('cap.gallery.random'), icon: 'shuffle', small: true, title: AC.t('cap.gallery.randomTitle'), onClick: function () { randomLook(bRand); } });
    var bMix = ui.button({ label: AC.t('cap.gallery.mix'), icon: 'join', small: true, title: AC.t('cap.gallery.mixTitle'), onClick: function () { TP.mixOpen = !TP.mixOpen; paintMix(); } });
    bMix.setAttribute('aria-expanded', 'false');
    TP.bMix = bMix;
    panel.appendChild(ui.h('div', { class: 'cap-tpl-top' }, [search, ui.h('div', { class: 'cap-tpl-acts' }, [bRand, bMix])]));
    TP.lastBar = ui.h('div', { class: 'cap-lastbar', hidden: true });
    TP.mix = ui.h('div', { class: 'cap-mix', hidden: true });
    panel.appendChild(TP.lastBar);
    panel.appendChild(TP.mix);

    // Gaya Saya
    var mine = ui.section({ title: AC.t('cap.gallery.mine'), action: { label: AC.t('cap.gallery.importBtn'), onClick: function () { importStyles(); } } });
    TP.mineGrid = ui.h('div', { class: 'cap-tpl-grid cap-mine', role: 'radiogroup', 'aria-label': AC.t('cap.gallery.mine') });
    TP.mineNote = ui.h('div', { class: 'cap-mine-note' });
    mine.add([TP.mineGrid, TP.mineNote]);
    TP.mineSec = mine;
    panel.appendChild(mine.el);
    // Terakhir dipakai
    var lastSec = ui.section({ title: AC.t('cap.gallery.lastUsed') });
    TP.lastGrid = ui.h('div', { class: 'cap-tpl-grid cap-lastgrid' });
    lastSec.add(TP.lastGrid);
    TP.lastSec = lastSec;
    panel.appendChild(lastSec.el);
    // Built-in templates
    var bsec = ui.section({ title: 'Template' });
    var cats = ui.h('div', { class: 'cap-tpl-cats', role: 'toolbar', 'aria-label': AC.t('cap.gallery.catsAria') });
    var grid = ui.h('div', { class: 'cap-tpl-grid', role: 'radiogroup', 'aria-label': AC.t('cap.gallery.gridAria') });
    var note = ui.h('p', { class: 'help' });
    var bFrame = ui.button({ label: AC.t('cap.gallery.useFrame'), icon: 'file', small: true, kind: 'ghost', onClick: function () { gallery(true); } });
    var bDef = ui.button({ label: AC.t('cap.gallery.setDefault'), icon: 'check', small: true, kind: 'ghost', title: AC.t('cap.gallery.setDefaultTitle'), onClick: function () {
      if (!S.doc) return;
      AC.settings.set('captionTemplate', S.doc.template);
      ui.toast(AC.t('cap.gallery.defaultSet', { name: (tplRow(S.doc.template) || {}).name || S.doc.template }), 'check');
      TP.key = null; paint();
    } });
    bsec.add([cats, grid, note, ui.h('div', { class: 'btn-row' }, [bFrame, bDef])]);
    TP.bSec = bsec;
    panel.appendChild(bsec.el);
    TP.cats = cats; TP.grid = grid; TP.note = note; TP.bFrame = bFrame;
    grid.addEventListener('keydown', gridNav);
    TP.mineGrid.addEventListener('keydown', gridNav);

    cap.bus.on('templates', function () { TP.key = null; paint(); paintMine(); paintLast(); gallery(false); anims(); if (TP.mixOpen) paintMix(); if (LIB.rows) LIB.load(true); });
    cap.bus.on('doc', function () { paint(); paintMine(); paintLastBar(); });
    cap.bus.on('library', function () { paintMine(); anims(); });
    cap.bus.on('last', function () { paintLast(); });
    cap.bus.on('tab', function (id) { if (id === 'template') anims(); });
    paint(); paintMine(); paintLast(); paintLastBar();
    if (S.tpl) { gallery(false); anims(); }
    LIB.load();
  };

  // filter chips: Semua, Cocok (aspect of this sequence), then the engine groups (Shorts Viral, Tutorial, ...)
  function aspectFits(r) {
    var s = S.seq; if (!s) return true;
    var q = s.w / Math.max(1, s.h);
    if (r.aspect_fit) return r.aspect_fit === 'both' || (q < 0.85 ? r.aspect_fit === '9:16' : r.aspect_fit === '16:9');
    var want = q < 0.85 ? '9:16' : q < 1.2 ? '1:1' : (q > 2 ? '21:9' : '16:9'), a = r.aspect || [];
    return !a.length || a.indexOf(want) >= 0 || (want === '21:9' && a.indexOf('16:9') >= 0);
  }
  function cats() {
    var list = [{ id: 'all', label: AC.t('common.all'), test: function () { return true; } }, { id: 'fit', label: AC.t('cap.gallery.fit'), title: AC.t('cap.gallery.fitTitle'), test: aspectFits }];
    ((S.tpl && S.tpl.groups) || []).forEach(function (gr) {
      list.push({ id: gr.id, label: gr.label, test: function (r) { return (r.groups || [r.group]).indexOf(gr.id) >= 0; } });
    });
    return list;
  }
  function matchQ(r) {
    if (!TP.q) return true;
    var hay = [r.name, r.description, r.group_label, r.font, (r.effects || []).map(function (x) { return x.label; }).join(' ')].join(' ').toLowerCase();
    return TP.q.split(/\s+/).every(function (w) { return hay.indexOf(w) >= 0; });
  }
  function builtinRows() { return ((S.tpl && S.tpl.templates) || []).filter(function (r) { return r.source !== 'user'; }); }

  function paint() {
    if (!TP.grid) return;
    var rows = builtinRows(), list = cats(), cur = S.doc && S.doc.template, def = AC.settings.get('captionTemplate');
    var cat = list.filter(function (c) { return c.id === TP.group; })[0] || list[0];
    // chips
    var ckey = JSON.stringify([rows.length, TP.group, list.map(function (c) { return c.id + c.label; })]);
    if (ckey !== TP.ckey) {
      TP.ckey = ckey;
      TP.cats.innerHTML = '';
      list.forEach(function (c) {
        var n = rows.filter(c.test).length;
        if (!n && c.id !== 'all') return;
        var b = ui.h('button', { class: 'chip', type: 'button', 'aria-pressed': TP.group === c.id ? 'true' : 'false', title: c.title || '', dataset: { g: c.id }, html: U.esc(c.label) + ' <span class="n">' + n + '</span>' });
        b.addEventListener('click', function () { TP.group = c.id; TP.ckey = null; paint(); });
        TP.cats.appendChild(b);
      });
    }
    rows = rows.filter(cat.test).filter(matchQ);
    var row0 = cur && (tplRow(cur) || LIB.row(cur));
    TP.note.textContent = row0 ? AC.t('cap.gallery.inUse', { name: row0.name, desc: row0.description || '' }) + (S.doc && S.doc.style && Object.keys(S.doc.style).length ? ' ' + AC.t('cap.gallery.manualOver') : '') : '';
    // rebuild the cards only when the list / thumbnails / default changed; otherwise just move the ring
    var key = JSON.stringify([AC.i18n.lang(), !!S.tpl, TP.group, TP.q, def, rows.map(function (r) { return [r.id, r.name, TP.thumbs[r.id] || r.thumb]; })]);
    if (key === TP.key) { ring(); return; }
    TP.key = key;
    TP.grid.innerHTML = '';
    if (!S.tpl) { TP.grid.appendChild(ui.h('p', { class: 'help', text: AC.t('cap.gallery.loading') })); return; }
    if (!rows.length) TP.grid.appendChild(ui.h('p', { class: 'help', text: TP.q ? AC.t('cap.gallery.noMatchQ', { q: TP.q }) : AC.t('cap.gallery.emptyCat') }));
    rows.forEach(function (r) { TP.grid.appendChild(card(r, 'builtin', cur, def)); });
    rove(TP.grid);
    TP.bFrame.hidden = TP.framed;
  }
  function ring() {
    var cur = S.doc && S.doc.template;
    [TP.grid, TP.mineGrid].forEach(function (gr) {
      if (gr) U.$$('.cap-tpl[data-id]', gr).forEach(function (b) { b.setAttribute('aria-checked', b.dataset.id === cur ? 'true' : 'false'); });
      rove(gr);
    });
  }
  // Roving tabindex: one Tab stop per grid (the focused card, else the checked one, else the first).
  function rove(gr) {
    if (!gr) return;
    var cards = U.$$('.cap-tpl[data-id]', gr);
    if (!cards.length) return;
    var a = cards.indexOf(document.activeElement), on = a >= 0 ? cards[a] : null;
    if (!on) cards.forEach(function (b) { if (!on && b.getAttribute('aria-checked') === 'true') on = b; });
    on = on || cards[0];
    cards.forEach(function (b) { b.tabIndex = b === on ? 0 : -1; });
  }
  // Arrow keys move focus by the laid-out column count, Home / End jump to the ends. Enter / Space pick.
  function gridNav(e) {
    if (e.target && /^(INPUT|TEXTAREA|SELECT)$/.test(e.target.tagName)) return;
    var cards = U.$$('.cap-tpl[data-id]', e.currentTarget), i = cards.indexOf(document.activeElement);
    if (i < 0) return;
    var top = cards[0].offsetTop, cols = 0;
    while (cols < cards.length && cards[cols].offsetTop === top) cols++;
    cols = Math.max(1, cols);
    var step = { ArrowRight: 1, ArrowLeft: -1, ArrowDown: cols, ArrowUp: -cols, Home: -1e6, End: 1e6 }[e.key];
    if (step === undefined) return;
    e.preventDefault();
    var n = U.clamp(i + step, 0, cards.length - 1);
    cards.forEach(function (b, k) { b.tabIndex = k === n ? 0 : -1; });
    cards[n].focus();
  }

  // One template card. kind: 'builtin' (button) | 'user' (div with a menu button) | 'last' (last used look)
  function card(r, kind, cur, def) {
    var isUser = kind === 'user';
    var attrs = { class: 'cap-tpl' + (isUser ? ' cap-tpl-user' : '') + (kind === 'last' ? ' cap-tpl-last' : '') + (r.saving ? ' is-saving' : ''),
                  role: kind === 'last' ? null : 'radio', 'aria-checked': kind === 'last' ? null : (r.id === cur ? 'true' : 'false'),
                  title: r.description || r.name, tabIndex: kind === 'last' || r.id === cur ? 0 : -1 };   // roving (rove())
    if (kind === 'last') attrs.dataset = { last: r.aspect || '' }; else attrs.dataset = { id: r.id };
    if (!isUser) attrs.type = 'button';
    else attrs['data-i18n-skip'] = true;   // Gaya Saya card: the user's name + the description stored when it was saved
    var el = ui.h(isUser ? 'div' : 'button', attrs);
    var th = ui.h('div', { class: 'cap-tpl-thumb' });
    var src = TP.thumbs[r.id] || r.thumb;
    if (src) th.appendChild(ui.h('img', { src: cap.fileUrl(src), alt: '', loading: 'lazy' }));
    else th.appendChild(ui.h('span', { class: 'cap-tpl-ph', text: r.saving ? AC.t('cap.gallery.saving') : (r.uppercase ? 'AA' : 'Aa') }));
    if (kind !== 'last') th.appendChild(ui.h('div', { class: 'cap-tpl-spr', 'aria-hidden': 'true' }));
    el.appendChild(th);
    var sub = kind === 'last' ? (r.aspect_label || '') : isUser ? (r.updated_ms ? U.dayLabel(r.updated_ms) : (r.saving ? AC.t('cap.gallery.savingShort') : '')) : (r.anim_in_label || optLabel(opts().anim_in, r.anim_in));
    var meta = ui.h('div', { class: 'cap-tpl-meta' }, [ui.h('b', { text: r.name }), ui.h('span', { text: sub || '' })]);
    el.appendChild(meta);
    if ((r.colors || []).length) {
      el.appendChild(ui.h('span', { class: 'cap-tpl-dots', 'aria-hidden': 'true', html: r.colors.slice(0, 4).map(function (c) { return '<i style="background:' + U.esc(c) + '"></i>'; }).join('') }));
    }
    var badges = ui.h('span', { class: 'cap-tpl-badge' });
    if (isUser && r.favorite) badges.appendChild(ui.h('span', { class: 'cap-fav', title: AC.t('cap.gallery.favorite'), html: ui.icon('star') }));
    if (r.id === def && kind !== 'last') badges.insertAdjacentHTML('beforeend', ui.tag('Default', 'ai'));
    if (badges.childNodes.length) el.appendChild(badges);
    if (isUser && !r.saving) {
      var more = ui.h('button', { class: 'cap-tpl-more', type: 'button', 'aria-haspopup': 'menu', 'aria-label': 'Menu ' + r.name, title: 'Menu', html: ui.icon('dots') });
      more.addEventListener('click', function (e) { e.stopPropagation(); openMenu(more, r, el); });
      more.addEventListener('keydown', function (e) { e.stopPropagation(); });
      el.appendChild(more);
    }
    var go = function () {
      if (r.saving) return;
      if (kind === 'last') applyLast(r); else pick(r);
    };
    el.addEventListener('click', go);
    if (isUser) el.addEventListener('keydown', function (e) { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); go(); } });
    if (kind !== 'last') {
      var on = function () { play(el, r.id, true); }, off = function () { play(el, r.id, false); };
      el.addEventListener('mouseenter', on); el.addEventListener('mouseleave', off);
      el.addEventListener('focus', on); el.addEventListener('blur', off);
    }
    return el;
  }

  function pick(r) {
    if (!S.doc || S.doc.template === r.id && !Object.keys(cap.style()).length) return;
    var manual = Object.keys(cap.style()).length > 0;
    S.draft = null;
    U.$$('.cap-tpl[data-id]', TP.grid.parentNode.parentNode).forEach(function (b) { b.setAttribute('aria-checked', b.dataset.id === r.id ? 'true' : 'false'); });
    cap.op({ op: 'template', id: r.id }, { label: AC.t('cap.gallery.opTemplate', { name: r.name }), now: true }).then(function () {
      if (manual) ui.toast(AC.t('cap.gallery.picked', { name: r.name }), { icon: 'check', action: { label: AC.t('cap.gallery.undo'), run: function () { cap.undo(); } } });
    });
  }

  /* ---------------------------------------------------------------- thumbnails: static (gallery) + sprite strips (gallery anim) */
  function gallery(framed) {
    if (!S.tpl) return;
    if (!framed && TP.galleryP) return;
    var params = framed ? { t: cap.preview ? cap.preview.time() : 0 } : {};
    var p = cap.worker('gallery', params, framed ? { withSeq: true } : {}).promise.then(function (r) {
      Object.keys(r.thumbs || {}).forEach(function (k) { if (r.thumbs[k]) TP.thumbs[k] = r.thumbs[k]; });
      if (framed) TP.framed = true;
      TP.key = null; paint(); paintMine();
      return r;
    }, function (e) { if (!framed) TP.galleryP = null; ui.toast(AC.t('cap.gallery.thumbFail', { msg: U.errMsg(e) }), { kind: 'err' }); });
    if (!framed) TP.galleryP = p;
    else { TP.bFrame.disabled = true; p.then(function () { TP.bFrame.disabled = false; }); }
  }
  // Sprite strips: what is cached comes at once (worker); the rest is rendered once in a background engine process
  // (not the worker, so previews never wait behind it).
  // Incremental: only ids without a strip that were never requested (new Gaya Saya cards after a save / import /
  // duplicate get theirs too). Requests run one after another (TP.animP chain).
  TP.animReq = {};
  function anims() {
    if (!S.tpl || reducedMotion() || S.tab !== 'template') return;
    var ids = builtinRows().map(function (r) { return r.id; }).concat((LIB.rows || []).map(function (r) { return r.id; }).filter(function (i) { return i.indexOf('__') !== 0; }))
      .filter(function (i) { return !TP.anims[i] && !TP.animReq[i]; });
    if (!ids.length) return;
    ids.forEach(function (i) { TP.animReq[i] = true; });
    TP.animP = (TP.animP || Promise.resolve()).then(function () {
      return cap.worker('gallery', { anim: true, ids: ids, cached_only: true }).promise;
    }).then(function (r) {
      takeAnims(r);
      if (!(r.pending || []).length) return r;
      var job = AC.engine.run({ tool: 'captions', action: 'gallery', params: { anim: true, ids: r.pending }, seq: null, record: false, workdir: S.workdir || AC.settings.workRoot(), title: AC.t('cap.gallery.animJob') });
      return job.promise.then(takeAnims, function (e) { AC.log.warn('captions anim gallery: ' + U.errMsg(e)); });
    }, function (e) { ids.forEach(function (i) { delete TP.animReq[i]; }); AC.log.warn('captions anim gallery: ' + U.errMsg(e)); });
  }
  function takeAnims(r) {
    Object.keys((r && r.anims) || {}).forEach(function (k) { TP.anims[k] = r.anims[k]; });
    return r;
  }
  function play(el, id, on) {
    var spr = el.querySelector('.cap-tpl-spr'), a = TP.anims[id];
    if (!spr) return;
    if (!on || !a || reducedMotion()) { el.classList.remove('is-play'); return; }
    if (!spr.dataset.png) {
      spr.dataset.png = a.png;
      spr.style.backgroundImage = 'url("' + cap.fileUrl(a.png) + '")';
      spr.style.backgroundSize = (a.frames * 100) + '% 100%';
      spr.style.animationDuration = a.dur + 's';
      spr.style.animationTimingFunction = 'steps(' + a.frames + ', jump-none)';
    }
    el.classList.add('is-play');
  }

  /* ---------------------------------------------------------------- Gaya Saya cards + menu */
  function paintMine() {
    if (!TP.mineGrid) return;
    var rows = (LIB.rows || []).filter(matchQ), cur = S.doc && S.doc.template;
    TP.mineSec.setAside(LIB.rows ? AC.t('cap.gallery.mineCount', { n: LIB.rows.filter(function (r) { return !r.saving; }).length }) : '');
    var key = JSON.stringify([AC.i18n.lang(), TP.q, !!LIB.rows, rows.map(function (r) { return [r.id, r.name, r.favorite, r.thumb, r.saving, r.updated_ms]; })]);
    if (key === TP.mkey) { ring(); return; }
    // an open card menu lives inside the grid: repaint after it closes, or a library refresh would drop it mid-click
    if (MENU && MENU.el && TP.mineGrid.contains(MENU.el)) { MENU.defer = true; return; }
    TP.mkey = key;
    TP.mineGrid.innerHTML = '';
    TP.mineNote.innerHTML = '';
    if (!LIB.rows) { TP.mineGrid.appendChild(ui.h('p', { class: 'help', text: AC.t('cap.gallery.mineLoading') })); return; }
    if (!rows.length) {
      TP.mineNote.appendChild(ui.h('p', { class: 'help', html: TP.q ? U.esc(AC.t('cap.gallery.mineNoMatch')) : AC.t('cap.gallery.mineEmptyHtml', { icon: ui.icon('star') }) }));
      return;
    }
    rows.forEach(function (r) { TP.mineGrid.appendChild(card(r, 'user', cur, null)); });
    rove(TP.mineGrid);
  }

  var MENU = { el: null, un: null };
  function closeMenu() {
    if (MENU.el && MENU.el.parentNode) MENU.el.parentNode.removeChild(MENU.el);
    MENU.el = null;
    if (MENU.un) { MENU.un(); MENU.un = null; }
    document.removeEventListener('mousedown', outside, true);
    if (MENU.defer) { MENU.defer = false; setTimeout(paintMine, 0); }
  }
  function outside(e) { if (MENU.el && !MENU.el.contains(e.target)) closeMenu(); }
  function openMenu(btn, r, cardEl) {
    closeMenu();
    var m = ui.h('div', { class: 'cap-menu', role: 'menu', 'aria-label': 'Menu ' + r.name });
    var items = [
      [AC.t('cap.gallery.rename'), 'type', function () { renameInline(cardEl, r); }],
      [AC.t('cap.gallery.duplicate'), 'copy', function () { duplicate(r); }],
      [AC.t(r.favorite ? 'cap.gallery.unfav' : 'cap.gallery.fav'), 'star', function () { favorite(r); }],
      [AC.t('cap.gallery.export'), 'export', function () { exportStyle(r); }],
      [AC.t('common.delete'), 'trash', null]
    ];
    items.forEach(function (it, i) {
      var b = ui.h('button', { class: 'cap-menu-i' + (it[1] === 'trash' ? ' is-danger' : ''), type: 'button', role: 'menuitem', tabIndex: -1, html: ui.icon(it[1]) + '<span>' + U.esc(it[0]) + '</span>' });
      if (it[2]) b.addEventListener('click', function (e) { e.stopPropagation(); closeMenu(); it[2](); });
      else {
        var armed = false;
        b.addEventListener('click', function (e) {
          e.stopPropagation();
          if (!armed) { armed = true; b.querySelector('span').textContent = AC.t('common.confirm'); return; }
          closeMenu(); deleteStyle(r);
        });
      }
      m.appendChild(b);
    });
    m.addEventListener('keydown', function (e) {
      var list = U.$$('.cap-menu-i', m), i = list.indexOf(document.activeElement);
      if (e.key === 'ArrowDown' || e.key === 'ArrowUp') { e.preventDefault(); list[(i + (e.key === 'ArrowDown' ? 1 : list.length - 1)) % list.length].focus(); }
      if (e.key === 'Tab') closeMenu();
      if (e.key === 'Escape') { e.preventDefault(); closeMenu(); btn.focus(); }
      e.stopPropagation();
    });
    m.addEventListener('click', function (e) { e.stopPropagation(); });
    cardEl.appendChild(m);
    MENU.el = m;
    MENU.un = AC.keys.onEscape(function () { if (!MENU.el) return false; closeMenu(); btn.focus(); return true; });
    setTimeout(function () { document.addEventListener('mousedown', outside, true); }, 0);
    m.firstChild.focus();
  }
  function renameInline(cardEl, r) {
    var b = cardEl.querySelector('.cap-tpl-meta b');
    if (!b) return;
    var inp = ui.h('input', { class: 'input cap-tpl-rename', type: 'text', maxlength: '60', value: r.name, 'aria-label': AC.t('cap.gallery.renameAria', { name: r.name }), spellcheck: 'false' });
    b.parentNode.replaceChild(inp, b);
    inp.focus(); inp.select();
    var done = false;
    var finish = function (save) {
      if (done) return; done = true;
      var nm = inp.value.trim();
      if (!save || !nm || nm === r.name) { TP.mkey = null; paintMine(); return; }
      libCall({ op: 'rename', id: r.id, name: nm }).then(function (x) {
        upsert(x.template); cap.templates(true); ui.toast(AC.t('cap.gallery.renamed', { name: x.template.name }), 'check');
      }, function (e) { TP.mkey = null; paintMine(); ui.toast(AC.t('cap.gallery.renameFail', { msg: U.errMsg(e) }), { kind: 'err' }); });
    };
    inp.addEventListener('click', function (e) { e.stopPropagation(); });
    inp.addEventListener('keydown', function (e) {
      e.stopPropagation();
      if (e.key === 'Enter') { e.preventDefault(); finish(true); }
      if (e.key === 'Escape') { e.preventDefault(); finish(false); }
    });
    inp.addEventListener('blur', function () { finish(true); });
  }
  function duplicate(r) {
    libCall({ op: 'duplicate', id: r.id }).then(function (x) {
      upsert(x.template); cap.templates(true); ui.toast(AC.t('cap.gallery.duplicated', { name: x.template.name }), 'copy');
    }, function (e) { ui.toast(AC.t('cap.gallery.duplicateFail', { msg: U.errMsg(e) }), { kind: 'err' }); });
  }
  function favorite(r) {
    libCall({ op: 'favorite', id: r.id, on: !r.favorite }).then(function (x) {
      upsert(x.template); ui.toast(AC.t(x.template.favorite ? 'cap.gallery.favOn' : 'cap.gallery.favOff', { name: r.name }), 'star');
    }, function (e) { ui.toast(AC.t('cap.gallery.failMsg', { msg: U.errMsg(e) }), { kind: 'err' }); });
  }
  function deleteStyle(r) {
    var pre = Promise.resolve();
    if (S.doc && S.doc.template === r.id && S.tpl && S.tpl.full && S.tpl.full[r.id]) {
      // the document uses this style: keep its look on the default template before the file goes away
      var look = lookOf(S.tpl.full[r.id]);
      pre = cap.op([{ op: 'template', id: cap.defaultTemplate() }, { op: 'doc_style', style: look, replace: true }], { label: AC.t('cap.gallery.detach', { name: r.name }), now: true });
    }
    pre.then(function () { return libCall({ op: 'delete', id: r.id }); }).then(function () {
      dropRow(r.id); cap.templates(true); ui.toast(AC.t('cap.gallery.deleted', { name: r.name }), 'trash');
    }, function (e) { ui.toast(AC.t('cap.gallery.deleteFail', { msg: U.errMsg(e) }), { kind: 'err' }); });
  }

  // CEP file dialogs (window.cep.fs.showOpenDialogEx / showSaveDialogEx); outside CEP a file input / default folder.
  function cepFs() { return window.cep && window.cep.fs; }
  function exportStyle(r) {
    var fs = cepFs(), def = fileName(r.name) + '.json', path = null;
    if (fs && fs.showSaveDialogEx) {
      var x = fs.showSaveDialogEx(AC.t('cap.gallery.exportDlg'), AC.sys.paths.videos || '', ['json'], def);
      if (!x || x.err || !x.data) return;
      path = x.data;
    } else path = AC.sys.join(AC.settings.workRoot(), AC.t('cap.gallery.exportFolder'), def);
    libCall({ op: 'export', ids: [r.id], path: path }).then(function (res) {
      ui.toast(AC.t('cap.gallery.exported', { file: AC.sys.basename(res.path) }), { icon: 'export', action: { label: AC.t('cap.gallery.openFolder'), run: function () { AC.sys.openFolder(AC.sys.dirname(res.path)); } } });
      noteWarnings(AC.t('cap.gallery.exportNotes'), res.warnings);
    }, function (e) { ui.toast(AC.t('cap.gallery.exportFail', { msg: U.errMsg(e) }), { kind: 'err' }); });
  }
  function importStyles() {
    var fs = cepFs();
    if (fs && fs.showOpenDialogEx) {
      var x = fs.showOpenDialogEx(false, false, AC.t('cap.gallery.importDlg'), '', ['json']);
      if (x && !x.err && x.data && x.data[0]) runImport(x.data[0], null);
      return;
    }
    var inp = ui.h('input', { type: 'file', accept: '.json,application/json', hidden: true });
    inp.addEventListener('change', function () {
      var f = inp.files && inp.files[0];
      if (inp.parentNode) inp.parentNode.removeChild(inp);
      if (!f) return;
      var rd = new FileReader();
      rd.onload = function () {
        var p = AC.sys.join(AC.sys.paths.temp, 'import_style_' + Date.now() + '.json');
        try { AC.sys.writeText(p, String(rd.result || '')); } catch (e) { ui.toast(AC.t('cap.gallery.readFail', { msg: U.errMsg(e) }), { kind: 'err' }); return; }
        runImport(p, function () { AC.sys.remove(p); });
      };
      rd.readAsText(f);
    });
    document.body.appendChild(inp);
    inp.click();
  }
  cap.lib.importFile = function (path) { return runImport(path, null); };
  function runImport(path, cleanup) {
    return libCall({ op: 'import', path: path }).then(function (r) {
      if (cleanup) cleanup();
      (r.imported || []).forEach(function (row) { upsert(row); });
      cap.templates(true);
      ui.toast(r.summary || AC.t('cap.gallery.imported', { n: (r.imported || []).length }), 'check');
      noteWarnings(AC.t('cap.gallery.importNotes'), r.warnings);
      return r;
    }, function (e) { if (cleanup) cleanup(); ui.toast(AC.t('cap.gallery.importFail', { msg: U.errMsg(e) }), { kind: 'err' }); throw e; });
  }
  function noteWarnings(title, list) {
    if (!TP.mineNote || !(list || []).length) return;
    TP.mineNote.innerHTML = '';
    TP.mineNote.appendChild(ui.alert({ kind: 'info', title: title, text: list.slice(0, 3).join(' ') }));
  }

  /* ---------------------------------------------------------------- last used look (card + badge) */
  function paintLast() {
    if (!TP.lastGrid) return;
    var L = S.last;
    TP.lastSec.el.hidden = !L;
    TP.lastGrid.innerHTML = '';
    if (!L) return;
    var r = { id: L.template, name: L.name || L.template, thumb: L.thumb, aspect: L.aspect, aspect_label: (L.aspect_label || '') + (L.updated_ms ? ', ' + U.dayLabel(L.updated_ms) : ''),
              description: AC.t('cap.gallery.lastDesc', { aspect: String(L.aspect_label || '').toLowerCase() }) + (L.seq ? ' (' + L.seq + ')' : ''), colors: [] };
    TP.lastGrid.appendChild(card(r, 'last'));
  }
  function applyLast(r) {
    var L = S.last;
    if (!L || !L.ops || !S.doc) return;
    cap.fromLast(L);
    cap.op(L.ops, { label: AC.t('cap.gallery.lastTag'), now: true }).then(function () { ui.toast(AC.t('cap.gallery.lastApplied', { name: L.name || L.template }), 'check'); });
  }
  function paintLastBar() {
    if (!TP.lastBar) return;
    var e = S.doc ? cap.fromLast() : null;
    TP.lastBar.hidden = !e;
    TP.lastBar.innerHTML = '';
    if (!e) return;
    TP.lastBar.appendChild(ui.html(ui.tag(AC.t('cap.gallery.lastTag'), 'ai', 'refresh')));
    TP.lastBar.appendChild(ui.h('span', { class: 'grow', text: e.name || '' }));
    TP.lastBar.appendChild(ui.button({ label: AC.t('cap.gallery.useOriginal'), icon: 'undo', small: true, kind: 'ghost', title: AC.t('cap.gallery.useOriginalTitle'),
      onClick: function () { var row = tplRow(S.doc.template); cap.op({ op: 'template', id: S.doc.template }, { label: AC.t('cap.gallery.opOriginal'), now: true }).then(function () { ui.toast(AC.t('cap.gallery.originalApplied', { name: row ? row.name : S.doc.template }), { icon: 'check', action: { label: AC.t('cap.gallery.undo'), run: function () { cap.undo(); } } }); }); } }));
  }

  /* ---------------------------------------------------------------- Acak gaya / Gabung gaya (engine template_random / template_mix) */
  function applyDraft(r, label) {
    return cap.op(r.ops, { label: label, now: true }).then(function () {
      var fixes = (r.fixes || []).length;
      ui.toast(fixes ? AC.t('cap.gallery.draftFixes', { name: r.name, n: fixes }) : r.name, { icon: 'check', action: { label: AC.t('common.save'), run: function () { cap.styleUI.saveDialog(r.name); } } });
    });
  }
  function randomLook(btn) {
    if (!S.doc) return;
    btn.disabled = true;
    cap.idle().then(function () {
      return cap.worker('template_random', { seed: Math.floor(Math.random() * 100000) }, { withSeq: true }).promise;
    }).then(function (r) { return applyDraft(r, AC.t('cap.gallery.random')); }, function (e) { ui.toast(AC.t('cap.gallery.randomFail', { msg: U.errMsg(e) }), { kind: 'err' }); })
      .then(function () { btn.disabled = false; }, function () { btn.disabled = false; });
  }
  var MX = { a: null, b: null, parts: { font: 'a', style: 'b', box: 'b', highlight: 'a', anim: 'a', layout: 'a' } };
  function paintMix() {
    if (!TP.mix) return;
    TP.mix.hidden = !TP.mixOpen;
    TP.bMix.setAttribute('aria-expanded', TP.mixOpen ? 'true' : 'false');
    TP.mix.innerHTML = '';
    if (!TP.mixOpen || !S.tpl) return;
    var all = ((S.tpl && S.tpl.templates) || []).map(function (r) { return { value: r.id, label: r.source === 'user' ? AC.t('cap.gallery.mineSuffix', { name: r.name }) : r.name }; });
    if (!MX.a || !S.tpl.full[MX.a]) MX.a = (S.doc && S.tpl.full[S.doc.template]) ? S.doc.template : all[0].value;
    if (!MX.b || !S.tpl.full[MX.b] || MX.b === MX.a) MX.b = (all.filter(function (o) { return o.value !== MX.a && o.value === 'kuning_3d'; })[0] || all.filter(function (o) { return o.value !== MX.a; })[0]).value;
    var selA = ui.select({ ariaLabel: 'Template A', options: all, value: MX.a, onChange: function (v) { MX.a = v; } });
    var selB = ui.select({ ariaLabel: 'Template B', options: all, value: MX.b, onChange: function (v) { MX.b = v; } });
    TP.mix.appendChild(ui.h('div', { class: 'cap-mix-h', html: AC.t('cap.gallery.mixHeadHtml') }));
    TP.mix.appendChild(ui.h('div', { class: 'cap-mix-ab' }, [ui.h('span', { class: 'cap-mix-l', text: 'A' }), selA.el, ui.h('span', { class: 'cap-mix-l', text: 'B' }), selB.el]));
    var rows = ui.h('div', { class: 'cap-mix-parts' });
    MIX_PARTS.forEach(function (pt) {
      var seg = ui.segmented({ small: true, ariaLabel: AC.t(pt[1]), value: MX.parts[pt[0]], options: [{ value: 'a', label: 'A' }, { value: 'b', label: 'B' }], onChange: function (v) { MX.parts[pt[0]] = v; } });
      rows.appendChild(ui.h('div', { class: 'cap-mix-row', dataset: { part: pt[0] } }, [ui.h('span', { text: AC.t(pt[1]) }), seg.el]));
    });
    TP.mix.appendChild(rows);
    var go = ui.button({ label: AC.t('cap.gallery.mixGo'), icon: 'join', kind: 'primary', small: true, onClick: function () {
      var parts = {};
      MIX_PARTS.forEach(function (pt) { parts[pt[0]] = MX.parts[pt[0]] === 'b' ? MX.b : MX.a; });
      go.disabled = true;
      cap.idle().then(function () { return cap.worker('template_mix', { parts: parts, base: MX.a }, { withSeq: true }).promise; })
        .then(function (r) { return applyDraft(r, AC.t('cap.gallery.mix')); }, function (e) { ui.toast(AC.t('cap.gallery.mixFail', { msg: U.errMsg(e) }), { kind: 'err' }); })
        .then(function () { go.disabled = false; }, function () { go.disabled = false; });
    } });
    TP.mix.appendChild(ui.h('div', { class: 'btn-row' }, [go, ui.button({ label: AC.t('common.close'), kind: 'ghost', small: true, onClick: function () { TP.mixOpen = false; paintMix(); } })]));
  }
  cap.styleUI.gallery = function (framed) { gallery(framed); };
  cap.styleUI.mix = function (parts, a, b) { MX.a = a; MX.b = b; U.assign(MX.parts, parts || {}); TP.mixOpen = true; paintMix(); };

  /* ================================================================ Gaya ("Gaya Pro", generated from options.schema) */
  var GP = { ctl: [], groups: [], built: false };
  var SKIP = { 'font.family': 1, 'box.enabled': 1, 'box.shape': 1, 'layout.x': 1, 'layout.align': 1 };
  var EXTRA_RESET = { huruf: ['font.uppercase'], latar: ['box'], posisi: ['layout'], sorot: ['highlight.pill_pad_x', 'highlight.pill_pad_y'] };
  var OPEN_DEFAULT = { huruf: true, isi: true, latar: true, posisi: true };
  var ZERO_OFF = { 'style.outline': 1, 'style.shadow': 1, 'style.outline2': 1, 'box.border': 1, 'highlight.wiggle': 1, 'layout.rotate_jitter': 1, 'style.shadow_blur': 1 };
  var NUL = '__null';

  function paletteFor(path) {
    if (/glow/.test(path)) return GLOW_COLORS;
    if (/^box\./.test(path)) return BOX_COLORS;
    if (/^highlight\./.test(path)) return ACTIVE_COLORS;
    if (/fill|gradient|color_cycle/.test(path)) return TEXT_COLORS;
    return LINE_COLORS;
  }
  function isOn(v) { return !(v === null || v === undefined || v === false || v === 0 || v === ''); }
  function visible(f, e) {
    var c = f.show_if; if (!c) return true;
    return Object.keys(c).every(function (p) {
      var want = c[p], v = cap.get(e, p);
      if (want === '$on') return isOn(v);
      if (Array.isArray(want)) return want.indexOf(v) >= 0;
      return v === want;
    });
  }
  function alphaOf(c) { var m = /^#[0-9a-f]{6}([0-9a-f]{2})$/i.exec(c || ''); return m ? parseInt(m[1], 16) / 255 : 1; }
  function keepAlpha(f, c, cur) {
    if (f.alpha && c && c.length === 7 && cur && /^#[0-9a-f]{8}$/i.test(cur)) return c + cur.slice(7);
    return c;
  }
  function fmtNum(f, v) {
    var dec = f.decimals !== undefined ? f.decimals : 0;
    if (ZERO_OFF[f.path] && !v) return AC.t('cap.style.none');
    if (f.format === 'percent') return Math.round(v * 100) + ' %';
    var u = f.unit || '';
    return U.dec(v, dec) + (u === 'px' ? ' px' : u === 'dtk' || u === 's' ? ' ' + AC.t('unit.sec') : u === '%' ? ' %' : u);   // schema unit ids // i18n-ignore
  }
  function nullNum(f, e) {
    if (f.path === 'style.outline_opacity') return alphaOf(cap.get(e, 'style.outline_color'));
    if (f.path === 'box.opacity') return alphaOf(cap.get(e, 'box.color'));
    if (f.path === 'style.shadow_x' || f.path === 'style.shadow_y') return Number(cap.get(e, 'style.shadow')) || 0;
    return f['default'] !== null && f['default'] !== undefined ? f['default'] : f.min;
  }
  function enumOptions(f) {
    var list = f.options || (f.options_ref ? opts()[f.options_ref] : []) || [];
    var out = list.map(function (o) { return { value: o.id, label: o.label }; });
    if (f.nullable) out.unshift({ value: NUL, label: f.null_label || AC.t('cap.style.followTpl') });
    return out;
  }
  // write one schema field (special cases keep related keys in sync)
  function write(f, v) {
    if (f.path === 'font.case') {
      var st = U.copy(cap.style());
      st.font = st.font || {};
      if (v === null) { delete st.font['case']; delete st.font.uppercase; if (!Object.keys(st.font).length) delete st.font; }
      else { st.font['case'] = v; st.font.uppercase = v === 'upper'; }
      return cap.setStyleAll(st, { label: f.label });
    }
    return set(f.path, v, f.label);
  }

  function ctlNumber(f) {
    var s = ui.slider({ label: f.label, min: f.min, max: f.max, step: f.step || 1, value: f.min, decimals: f.decimals, help: f.help,
      format: function (v) { return fmtNum(f, v); }, onInput: function (v) { write(f, f.type === 'int' ? Math.round(v) : v); } });
    return { el: s.el, paint: function (e) { var v = cap.get(e, f.path); if (v === null || v === undefined) v = nullNum(f, e); s.set(Number(v), true); } };
  }
  function ctlBool(f) {
    var s = ui.switch({ label: f.label, help: f.help, onChange: function (v) { write(f, v); } });
    return { el: s.el, paint: function (e) { s.set(!!cap.get(e, f.path), true); } };
  }
  function ctlColor(f) {
    var sw = cap.swatches(paletteFor(f.path), null, function (c) { write(f, keepAlpha(f, c, g(f.path))); }, { none: !!f.nullable, label: f.label });
    var el = ui.h('div', { class: 'cap-color-row' }, [ui.h('span', { text: f.label }), sw]);
    return { el: el, paint: function (e) { sw.set(cap.get(e, f.path) || null); } };
  }
  function ctlColors(f) {
    var el = ui.h('div', { class: 'cap-colors-ed' });
    var lab = ui.h('span', { class: 'lbl', text: f.label });
    var sw = f.nullable ? ui.switch({ label: f.label, help: f.help, onChange: function (on) { write(f, on ? U.copy(f['default'] || ['#FFFFFF', '#FFE600']) : null); } }) : null;
    var list = ui.h('div', { class: 'cap-colors-list' });
    var bar = ui.h('div', { class: 'cap-grad', 'aria-hidden': 'true' });
    el.appendChild(sw ? sw.el : lab);
    el.appendChild(ui.h('div', { class: 'cap-colors-row' }, [list, bar]));
    var curKey = '';
    function render(cols) {
      var k = JSON.stringify(cols);
      if (k === curKey) return;
      curKey = k;
      list.innerHTML = '';
      cols.forEach(function (c, i) {
        var inp = ui.h('input', { type: 'color', value: String(c).slice(0, 7).toLowerCase(), 'aria-label': f.label + ' ' + (i + 1) });
        inp.addEventListener('change', function () { var n = cols.slice(); n[i] = keepAlpha(f, inp.value.toUpperCase(), cols[i]); write(f, n); });
        var cell = ui.h('label', { class: 'cap-swatch cap-cc', title: c }, [inp]);
        cell.style.setProperty('--c', c);
        list.appendChild(cell);
        if (cols.length > (f.min_items || 1)) {
          var rm = ui.h('button', { class: 'cap-cc-x', type: 'button', 'aria-label': AC.t('cap.style.removeColorN', { n: i + 1 }), title: AC.t('cap.style.removeColor'), html: ui.icon('x') });
          rm.addEventListener('click', function () { var n = cols.slice(); n.splice(i, 1); write(f, n); });
          list.appendChild(rm);
        }
      });
      if (cols.length < (f.max_items || 3)) {
        var add = ui.h('button', { class: 'cap-cc-add', type: 'button', 'aria-label': AC.t('cap.style.addColor'), title: AC.t('cap.style.addColor'), html: ui.icon('plus') });
        add.addEventListener('click', function () { var n = cols.slice(); n.push(cols[cols.length - 1] || '#FFFFFF'); write(f, n); });
        list.appendChild(add);
      }
      bar.style.background = cols.length > 1 ? 'linear-gradient(90deg,' + cols.map(function (c) { return String(c).slice(0, 7); }).join(',') + ')' : String(cols[0] || '#fff').slice(0, 7);
    }
    return { el: el, paint: function (e) {
      var v = cap.get(e, f.path), on = Array.isArray(v) && v.length > 0;
      if (sw) sw.set(on, true);
      el.querySelector('.cap-colors-row').hidden = !on;
      if (on) render(v);
    } };
  }
  function ctlEnum(f) {
    var list = enumOptions(f), short = list.length <= 4 && list.every(function (o) { return String(o.label).length <= 13; });
    var c, el;
    var on = function (v) { write(f, v === NUL ? null : v); };
    if (short) { c = ui.segmented({ small: true, ariaLabel: f.label, options: list, onChange: on }); el = ui.field({ label: f.label, help: f.help, control: c.el }); }
    else { c = ui.select({ ariaLabel: f.label, options: list, width: '170px', onChange: on }); el = ui.inlineField({ label: f.label, help: f.help, control: c.el }); }
    return { el: el, paint: function (e) { var v = cap.get(e, f.path); c.set(v === null || v === undefined ? (f.nullable ? NUL : f['default']) : v, true); } };
  }
  function ctlObject(f) {
    var s = ui.switch({ label: f.label, help: f.help, onChange: function (on) { write(f, on ? U.copy(f.default_on) : null); } });
    s.el.classList.add('cap-obj');
    return { el: s.el, paint: function (e) { s.set(isOn(cap.get(e, f.path)), true); } };
  }
  function control(f) {
    var c = f.type === 'number' || f.type === 'int' ? ctlNumber(f) : f.type === 'bool' ? ctlBool(f) : f.type === 'color' ? ctlColor(f)
      : f.type === 'colors' ? ctlColors(f) : f.type === 'enum' ? ctlEnum(f) : f.type === 'object' ? ctlObject(f) : null;
    if (!c) return null;
    c.f = f;
    c.el.classList.add('cap-f');
    c.el.setAttribute('data-path', f.path);
    if (f.parent) c.el.classList.add('cap-f-sub');
    return c;
  }

  /* ---------------------------------------------------------------- font picker (engine `fonts`) */
  var FP = { data: null, p: null, cat: 'all', src: 'all', q: '', open: false };
  function fontsData() {
    if (FP.p) return FP.p;
    FP.p = cap.worker('fonts', { system: true }).promise.then(function (r) { FP.data = r; injectFaces(r); return r; }, function (e) { FP.p = null; throw e; });
    return FP.p;
  }
  function cssName(fam) { return String(fam).replace(/["\\]/g, ''); }
  // Bundled fonts are not installed in Windows: load them from the engine folder so the samples use the real face.
  function injectFaces(r) {
    if (FP.styled) return;
    FP.styled = true;
    var dir = AC.sys.join(AC.sys.paths.engine, 'ac', 'captions', 'fonts'), css = '';
    (r.fonts || []).forEach(function (f) {
      if (f.source === 'bundled' && f.file) css += '@font-face{font-family:"acf ' + cssName(f.family) + '";src:url("' + cap.fileUrl(AC.sys.join(dir, f.file)) + '");font-display:swap}\n';
    });
    document.head.appendChild(ui.h('style', { id: 'cap-font-faces', text: css }));
  }
  function fontInfo(fam) { var l = (FP.data && FP.data.fonts) || []; for (var i = 0; i < l.length; i++) if (l[i].family === fam) return l[i]; return null; }
  function cssFont(fam) { var fi = fontInfo(fam); return fi && fi.source === 'bundled' ? '"acf ' + cssName(fam) + '"' : '"' + cssName(fam) + '", sans-serif'; }
  function familyOf(fam) {
    var l = (FP.data && FP.data.families) || [];
    for (var i = 0; i < l.length; i++) for (var j = 0; j < l[i].faces.length; j++) if (l[i].faces[j].family === fam) return l[i];
    return null;
  }
  function weightLabel(w) { var k = 'cap.style.w' + w; return AC.i18n.has(k) ? AC.t(k) : String(w); }   // 100..900
  function setFamily(fam) {
    var old = g('font.family'), size = Number(g('font.size')) || 80, a = fontInfo(old), b = fontInfo(fam);
    var patch = { font: { family: fam } };
    if (a && b && a.cap && b.cap && old !== fam) patch.font.size = Math.max(24, Math.min(200, Math.round(size * a.cap / b.cap / 2) * 2));   // same visual size
    cap.patchStyle(patch, { label: AC.t('cap.style.opFont') });
  }
  function pickFamily(fr) {
    var cur = fontInfo(g('font.family')), want = cur ? cur.weight : 700, best = fr.faces[0];
    fr.faces.forEach(function (fc) { if (Math.abs((fc.weight || 400) - want) < Math.abs((best.weight || 400) - want)) best = fc; });
    setFamily(best.family);
  }
  function sampleText() {
    var d = S.doc, i = cap.preview && cap.preview.page ? cap.preview.page() : 0;
    var p = d && d.pages && (d.pages[i >= 0 ? i : 0] || d.pages[0]);
    var t = p ? p.text.replace(/\n/g, ' ') : AC.t('cap.style.sample');
    return t.length > 42 ? t.slice(0, 40) + '...' : t;
  }
  function caseOf(t, e) {
    var c = cap.get(e, 'font.case') || (cap.get(e, 'font.uppercase') ? 'upper' : 'as_is');
    if (c === 'upper') return t.toUpperCase();
    if (c === 'lower') return t.toLowerCase();
    if (c === 'title') return t.replace(/(^|\s)(\S)/g, function (m, a, b) { return a + b.toUpperCase(); });
    return t;
  }
  function fontPicker() {
    FP.open = false; FP.q = '';   // a new picker (re-render) starts closed, with an empty search
    var el = ui.h('div', { class: 'cap-fontpick' });
    var btn = ui.h('button', { class: 'cap-font-btn', type: 'button', 'aria-expanded': 'false', 'aria-label': AC.t('cap.style.fontFamily'), 'data-i18n-skip': true });   // font names
    var sample = ui.h('div', { class: 'cap-font-sample', 'aria-label': AC.t('cap.style.fontSample'), 'data-i18n-skip': true });   // caption text
    var weightWrap = ui.h('div', { class: 'cap-font-w' });
    var listWrap = ui.h('div', { class: 'cap-font-panel', hidden: true });
    var search = ui.h('input', { class: 'input', type: 'search', placeholder: AC.t('cap.style.fontSearch'), 'aria-label': AC.t('cap.style.fontSearch'), spellcheck: 'false' });
    var chips = ui.h('div', { class: 'cap-tpl-cats cap-font-cats', role: 'toolbar', 'aria-label': AC.t('cap.style.fontCats') });
    var srcSeg = ui.segmented({ small: true, ariaLabel: AC.t('cap.style.fontSource'), value: FP.src, options: [{ value: 'all', label: AC.t('common.all') }, { value: 'bundled', label: AC.t('cap.style.bundled') }, { value: 'system', label: 'Windows' }], onChange: function (v) { FP.src = v; renderList(); } });
    var list = ui.h('div', { class: 'cap-font-list', role: 'listbox', 'aria-label': AC.t('cap.style.fontList') });
    listWrap.appendChild(ui.h('div', { class: 'cap-font-tools' }, [search, srcSeg.el]));
    listWrap.appendChild(chips);
    listWrap.appendChild(list);
    el.appendChild(ui.field({ label: AC.t('cap.style.fontFamily'), control: btn }));
    el.appendChild(listWrap);
    el.appendChild(sample);
    el.appendChild(weightWrap);
    search.addEventListener('input', U.debounce(function () { FP.q = search.value.trim().toLowerCase(); renderList(); }, 100));
    btn.addEventListener('click', function () { FP.open = !FP.open; toggle(); });
    var unEsc = null;
    function close() { FP.open = false; toggle(); btn.focus(); }
    // keyboard: ArrowDown from the search goes into the list, Up / Down walk the rows (Enter picks: it is a button)
    search.addEventListener('keydown', function (e) {
      if (e.key !== 'ArrowDown') return;
      var r = list.querySelector('.cap-font-row');
      if (r) { e.preventDefault(); r.focus(); }
    });
    list.addEventListener('keydown', function (e) {
      if (e.key !== 'ArrowDown' && e.key !== 'ArrowUp' && e.key !== 'Home' && e.key !== 'End') return;
      var rows = U.$$('.cap-font-row', list), i = rows.indexOf(document.activeElement);
      if (i < 0) return;
      e.preventDefault();
      if (e.key === 'ArrowUp' && i === 0) { search.focus(); return; }
      var n = e.key === 'Home' ? 0 : e.key === 'End' ? rows.length - 1 : U.clamp(i + (e.key === 'ArrowDown' ? 1 : -1), 0, rows.length - 1);
      rows[n].focus();
    });
    function toggle() {
      listWrap.hidden = !FP.open; btn.setAttribute('aria-expanded', FP.open ? 'true' : 'false');
      if (FP.open && !unEsc) unEsc = AC.keys.onEscape(function () { if (!FP.open || listWrap.hidden) return false; close(); return true; });
      if (!FP.open && unEsc) { unEsc(); unEsc = null; }
      if (FP.open) fontsData().then(function () { renderChips(); renderList(); search.focus(); }, function (e) { list.innerHTML = ''; list.appendChild(ui.h('p', { class: 'help', text: AC.t('cap.style.fontsFail', { msg: U.errMsg(e) }) })); });
    }
    function renderChips() {
      chips.innerHTML = '';
      [{ id: 'all', label: AC.t('common.all') }].concat(FP.data.categories || []).forEach(function (c) {
        var b = ui.h('button', { class: 'chip', type: 'button', 'aria-pressed': FP.cat === c.id ? 'true' : 'false', text: c.label });
        b.addEventListener('click', function () { FP.cat = c.id; renderChips(); renderList(); });
        chips.appendChild(b);
      });
    }
    function renderList() {
      if (!FP.data) return;
      var cur = g('font.family'), curFam = familyOf(cur);
      var fams = (FP.data.families || []).filter(function (fr) {
        if (FP.src !== 'all' && fr.source !== FP.src) return false;
        if (FP.cat !== 'all') { var fi = fontInfo(fr.faces[0].family); if (!fi || (fi.categories || [fi.category]).indexOf(FP.cat) < 0) return false; }
        return !FP.q || fr.base.toLowerCase().indexOf(FP.q) >= 0;
      });
      list.innerHTML = '';
      if (!fams.length) { list.appendChild(ui.h('p', { class: 'help', text: AC.t('cap.style.fontNoMatch') })); return; }
      fams.slice(0, 400).forEach(function (fr) {
        var face = fr.faces[fr.faces.length - 1].family, on = curFam && curFam.base === fr.base && curFam.source === fr.source;
        var row = ui.h('button', { class: 'cap-font-row', type: 'button', role: 'option', 'aria-selected': on ? 'true' : 'false', dataset: { base: fr.base } });
        var s = ui.h('span', { class: 'cap-font-s', text: AC.t('cap.style.fontRowSample') });
        s.style.fontFamily = cssFont(face);
        row.appendChild(s);
        row.appendChild(ui.h('span', { class: 'cap-font-n', html: '<span data-i18n-skip>' + U.esc(fr.base) + '</span> <i class="cap-src cap-src-' + fr.source + '">' + (fr.source === 'bundled' ? U.esc(AC.t('cap.style.bundled')) : 'Windows') + '</i>' }));
        row.addEventListener('click', function () { pickFamily(fr); close(); });
        list.appendChild(row);
      });
    }
    return { el: el, f: { path: 'font.family' }, paint: function (e) {
      var fam = cap.get(e, 'font.family') || '', fi = fontInfo(fam), fr = familyOf(fam);
      btn.innerHTML = '<span class="cap-font-cur">' + U.esc(fr ? fr.base : fam) + '</span>' + (fi ? '<i class="cap-src cap-src-' + fi.source + '">' + (fi.source === 'bundled' ? U.esc(AC.t('cap.style.bundled')) : 'Windows') + '</i>' : '') + ui.icon('chev-d');
      btn.querySelector('.cap-font-cur').style.fontFamily = FP.data ? cssFont(fam) : '';
      sample.textContent = caseOf(sampleText(), e);
      sample.style.fontFamily = FP.data ? cssFont(fam) : '';
      sample.style.color = String(cap.get(e, 'style.fill') || '#FFFFFF').slice(0, 7);
      sample.style.fontStyle = cap.get(e, 'font.italic') ? 'italic' : 'normal';
      var oc = String(cap.get(e, 'style.outline_color') || '#000000').slice(0, 7), ow = Number(cap.get(e, 'style.outline')) ? 1.5 : 0;
      sample.style.textShadow = ow ? [[1, 0], [-1, 0], [0, 1], [0, -1], [1, 1], [-1, -1], [1, -1], [-1, 1]].map(function (d) { return d[0] * ow + 'px ' + d[1] * ow + 'px 0 ' + oc; }).join(',') : 'none';
      sample.style.transform = Number(cap.get(e, 'font.skew')) ? 'skewX(' + (-Number(cap.get(e, 'font.skew')) * 45) + 'deg)' : '';
      // weights of the same design (bundled faces are separate families)
      var key = fam + '|' + !!FP.data + '|' + AC.i18n.lang();
      if (weightWrap.dataset.k !== key) {
        weightWrap.dataset.k = key;
        weightWrap.innerHTML = '';
        if (fr && fr.faces.length > 1) {
          var seg = ui.segmented({ small: true, ariaLabel: AC.t('cap.style.weight'), value: fam, options: fr.faces.map(function (fc) { return { value: fc.family, label: weightLabel(fc.weight) }; }),
            onChange: function (v) { setFamily(v); } });
          weightWrap.appendChild(ui.field({ label: AC.t('cap.style.weight'), control: seg.el }));
        }
      }
      if (FP.open) renderList();
    } };
  }

  /* ---------------------------------------------------------------- special pickers: background shapes, active-word looks, position */
  function matches(patch, base) {
    return Object.keys(patch).every(function (k) {
      var v = patch[k], b = base ? base[k] : undefined;
      if (v && typeof v === 'object' && !Array.isArray(v)) return matches(v, b || {});
      return (v === null ? b === null || b === undefined : v === b);
    });
  }
  function matchPreset(list) { var e = eff(); for (var i = 0; i < list.length; i++) if (matches(list[i].patch || {}, e)) return list[i].id; return null; }
  function closestBox(b) {
    if (!b || !b.enabled) return 'none';
    if (b.shape && b.shape !== 'rect') return b.shape;
    if (b.filled === false) return 'outline';
    if (b.per === 'word' || b.per === 'page') return b.per;
    return (b.radius || 0) >= 16 ? 'rounded' : 'line';
  }
  function shapePicker() {
    var el = ui.h('div', { class: 'cap-shapes', role: 'radiogroup', 'aria-label': AC.t('cap.style.shapesAria') });
    var key = '';
    return { el: el, f: { path: 'box.shape' }, paint: function (e) {
      var list = opts().box_shapes || opts().box_presets || [];
      var cur = matchPreset(list) || closestBox(e.box);
      var k = list.length + '|' + cur;
      if (k === key) return;
      key = k;
      el.innerHTML = '';
      list.forEach(function (p) {
        var b = ui.h('button', { class: 'cap-shape', type: 'button', role: 'radio', 'aria-checked': p.id === cur ? 'true' : 'false', title: p.label, dataset: { id: p.id } });
        b.appendChild(ui.h('span', { class: 'cap-shape-g s-' + p.id, html: '<span>Aa</span>' }));
        b.appendChild(ui.h('span', { class: 'cap-shape-l', text: SHAPE_KEY[p.id] ? AC.t(SHAPE_KEY[p.id]) : p.label }));
        b.addEventListener('click', function () { cap.patchStyle(p.patch, { label: AC.t('cap.style.opBox') }); });
        el.appendChild(b);
      });
    } };
  }
  function presetChips(listName, label, aria) {
    var el = ui.h('div', { class: 'cap-looks', role: 'radiogroup', 'aria-label': aria });
    var key = '';
    return { el: el, f: { path: listName }, paint: function () {
      var list = opts()[listName] || [], cur = matchPreset(list), k = list.length + '|' + cur;
      if (k === key) return;
      key = k;
      el.innerHTML = '';
      list.forEach(function (p) {
        var b = ui.h('button', { class: 'chip', type: 'button', role: 'radio', 'aria-checked': p.id === cur ? 'true' : 'false', text: p.label, dataset: { id: p.id } });
        b.addEventListener('click', function () { cap.patchStyle(p.patch, { label: label }); });
        el.appendChild(b);
      });
    } };
  }
  function setAnchor(r, c) {
    var ys = [15, 50, null][r], st = U.copy(cap.style());
    var safe = cap.safeZone(g('layout.safe'));
    var x = c === 0 ? Math.round(safe.f[2] * 100 + 2) : c === 2 ? Math.round(100 - safe.f[3] * 100 - 2) : 50;
    st.layout = st.layout || {};
    st.layout.align = ['left', 'center', 'right'][c];
    st.layout.x = x;
    if (ys === null) delete st.layout.y; else st.layout.y = ys;
    if (c !== 1 && (g('layout.width') || 80) > 60) st.layout.width = 56;
    if (c === 1 && st.layout.width === 56) delete st.layout.width;
    cap.setStyleAll(st, { label: AC.t('cap.style.opPos') });
  }
  function posGrid() {
    var gridEl = ui.h('div', { class: 'cap-posgrid', role: 'radiogroup', 'aria-label': AC.t('cap.style.posAria') });
    for (var r = 0; r < 3; r++) for (var c = 0; c < 3; c++) (function (r, c) {
      var aria = AC.t(['cap.style.posTop', 'cap.style.posMiddle', 'cap.style.posBottom'][r]) + ' ' + AC.t(['cap.style.posLeft', 'cap.style.posCenter', 'cap.style.posRight'][c]);
      var b = ui.h('button', { type: 'button', role: 'radio', 'aria-label': aria, dataset: { r: r, c: c } });
      b.addEventListener('click', function () { setAnchor(r, c); });
      gridEl.appendChild(b);
    })(r, c);
    var el = ui.h('div', { class: 'cap-pos-row' }, [gridEl, ui.h('p', { class: 'help', text: AC.t('cap.style.posHelp') })]);
    return { el: el, f: { path: 'layout.align' }, paint: function (e) {
      var al = cap.get(e, 'layout.align') || 'center', y = Number(cap.get(e, 'layout.y')) || 86;
      var a = { r: y < 33 ? 0 : y < 66 ? 1 : 2, c: al === 'left' ? 0 : al === 'right' ? 2 : 1 };
      var anyAuto = ((S.doc && S.doc.pages) || []).some(function (p) { return p.pos === 'auto'; });
      U.$$('button', gridEl).forEach(function (b) { b.setAttribute('aria-checked', Number(b.dataset.r) === a.r && Number(b.dataset.c) === a.c ? 'true' : 'false'); });
      gridEl.classList.toggle('is-auto', anyAuto);
    } };
  }
  function autoSwitch() {
    var s = ui.switch({ label: AC.t('cap.style.autoPos'), help: AC.t('cap.style.autoPosHelp'),
      onChange: function (v) { autoPos(v); } });
    return { el: s.el, f: { path: '_auto' }, paint: function () { s.set(((S.doc && S.doc.pages) || []).some(function (p) { return p.pos === 'auto'; }), true); } };
  }
  function autoPos(on) {
    var ctx = S.ctx;
    cap.idle().then(function () {
      var task = ctx.run({ action: 'autoposition', worker: true, workdir: S.workdir, scope: false, params: on ? {} : { clear: true }, title: AC.t(on ? 'cap.style.autoPosJob' : 'cap.style.autoPosClearJob'),
        stages: [{ id: 'analyze', label: AC.t('cap.style.stageAnalyze'), w: 0.95 }, { id: 'save', label: AC.t('cap.style.stageSave'), w: 0.05 }],
        onResult: function (r) {
          cap.reload();
          AC.router.go('tool/captions/gaya', { replace: true });   // route id // i18n-ignore
          cap.selectTab('gaya');
          ui.toast(on ? (r.moved ? AC.t('cap.style.autoPosMoved', { n: r.moved, total: r.pages }) : AC.t('cap.style.autoPosSafe')) : AC.t('cap.style.autoPosCleared'), 'check');
        } });
      cap.hold(task);
    });
  }
  var SPECIAL = {
    huruf: { before: [fontPicker] },
    latar: { before: [shapePicker] },
    sorot: { before: [function () { return presetChips('highlight_presets_all', AC.t('cap.style.opActive'), AC.t('cap.style.activeAria')); }] },
    posisi: { before: [posGrid], after: [autoSwitch] }
  };

  /* ---------------------------------------------------------------- Gaya tab */
  cap.styleUI.gaya = function (panel) {
    GP.panel = panel; GP.built = false; GP.sig = null;   // render() can run again (language switch): build anew
    GP.top = ui.h('div', { class: 'cap-pro-top' });
    GP.body = ui.h('div', { class: 'cap-pro' });
    panel.appendChild(GP.top);
    panel.appendChild(GP.body);
    var reset = ui.button({ label: AC.t('cap.style.resetAll'), icon: 'undo', small: true, kind: 'ghost', onClick: function () { cap.setStyleAll({}, { label: AC.t('cap.style.opReset') }); } });
    GP.reset = reset;
    panel.appendChild(ui.h('div', { class: 'cap-reset' }, [reset]));
    build();
    cap.bus.on('templates', function () { build(); paintGaya(); });
    cap.bus.on('doc', paintGaya);
    cap.bus.on('draft', U.debounce(paintGaya, 60));
    // font catalog (names, categories, cap heights, bundled files for the live samples) once the tab is used
    var loadFonts = function () { fontsData().then(paintGaya, function (e) { AC.log.warn('captions fonts: ' + U.errMsg(e)); }); };
    cap.bus.on('tab', function (id) { if (id === 'gaya') loadFonts(); });
    if (S.tab === 'gaya') loadFonts();
    paintGaya();
  };
  function groupOpen(id) { var o = (S.ctx && S.ctx.state.gayaOpen) || {}; return o[id] !== undefined ? !!o[id] : !!OPEN_DEFAULT[id]; }
  // Built once per language + schema (labels come from the engine options in the job language): a templates
  // reload after a language switch rebuilds the controls with the new labels.
  function build() {
    if (!S.tpl || !GP.body) return;
    var o = opts(), schema = o.schema || [], groups = (o.groups || []).filter(function (x) { return x.id !== 'animasi'; });
    if (!schema.length) return;
    var sig = JSON.stringify([AC.i18n.lang(), groups.map(function (x) { return x.id + x.label; }), schema.length, schema[0].label]);
    if (GP.built && GP.sig === sig) return;
    GP.built = true; GP.sig = sig;
    GP.ctl = []; GP.groups = [];
    GP.body.innerHTML = '';
    // one-click looks (effect presets)
    GP.top.innerHTML = '';
    GP.top.appendChild(ui.h('div', { class: 'cap-pro-h', text: AC.t('cap.style.quickFx') }));
    var fx = presetChips('effect_presets', AC.t('cap.style.opFx'), AC.t('cap.style.quickFx'));
    fx.el.classList.add('cap-fx');
    GP.top.appendChild(fx.el);
    GP.ctl.push(fx);
    groups.forEach(function (gr) {
      var d = ui.h('details', { class: 'cap-grp', open: groupOpen(gr.id), dataset: { g: gr.id } });
      var mod = ui.h('span', { class: 'cap-grp-mod', hidden: true, html: ui.tag(AC.t('cap.style.changed'), 'line') });
      d.appendChild(ui.h('summary', { class: 'sec-h' }, [ui.h('span', { text: gr.label }), mod]));
      var body = ui.h('div', { class: 'cap-grp-body' });
      d.appendChild(body);
      d.addEventListener('toggle', function () {
        if (!S.ctx) return;
        var st = S.ctx.state.gayaOpen = S.ctx.state.gayaOpen || {};
        st[gr.id] = d.open; S.ctx.save();
      });
      var sp = SPECIAL[gr.id] || {};
      var add = function (c) { if (!c) return; body.appendChild(c.el); GP.ctl.push(c); };
      (sp.before || []).forEach(function (mk) { add(mk()); });
      var paths = [];
      schema.filter(function (f) { return f.group === gr.id; }).forEach(function (f) {
        if (!f.parent) paths.push(f.path);
        if (SKIP[f.path]) return;
        add(control(f));
      });
      (sp.after || []).forEach(function (mk) { add(mk()); });
      var roots = paths.concat(EXTRA_RESET[gr.id] || []);
      var rb = ui.button({ label: AC.t('cap.style.resetGroup', { group: gr.label.toLowerCase() }), icon: 'undo', small: true, kind: 'ghost', onClick: function () { resetGroup(gr, roots); } });
      body.appendChild(ui.h('div', { class: 'cap-grp-foot' }, [rb]));
      GP.groups.push({ id: gr.id, el: d, mod: mod, roots: roots, reset: rb });
      GP.body.appendChild(d);
    });
  }
  function delPath(o, path) {
    var parts = path.split('.'), cur = o, stack = [];
    for (var i = 0; i < parts.length - 1; i++) { if (!cur[parts[i]] || typeof cur[parts[i]] !== 'object') return false; stack.push([cur, parts[i]]); cur = cur[parts[i]]; }
    if (!(parts[parts.length - 1] in cur)) return false;
    delete cur[parts[parts.length - 1]];
    for (var j = stack.length - 1; j >= 0; j--) { var p = stack[j][0], k = stack[j][1]; if (p[k] && typeof p[k] === 'object' && !Object.keys(p[k]).length) delete p[k]; }
    return true;
  }
  function hasPath(o, path) { var v = cap.get(o, path); return v !== undefined; }
  function resetGroup(gr, roots) {
    var st = U.copy(cap.style()), n = 0;
    roots.forEach(function (p) { if (delPath(st, p)) n++; });
    if (!n) { ui.toast(AC.t('cap.style.groupSame', { group: gr.label }), 'info'); return; }
    cap.setStyleAll(st, { label: AC.t('cap.style.resetGroup', { group: gr.label.toLowerCase() }) });
  }
  function paintGaya() {
    if (!GP.body) return;
    build();
    if (!S.doc || !GP.built) return;
    var e = eff(), st = cap.style();
    GP.ctl.forEach(function (c) {
      if (c.f && c.f.show_if) c.el.hidden = !visible(c.f, e);
      if (!c.el.hidden) c.paint(e);
    });
    GP.groups.forEach(function (gr) {
      var changed = gr.roots.some(function (p) { return hasPath(st, p); });
      gr.mod.hidden = !changed;
      gr.reset.disabled = !changed;
    });
    GP.reset.disabled = !Object.keys(st).length;
  }
  cap.styleUI.paintGaya = function () { paintGaya(); };

  /* ================================================================ Animasi */
  var A = {};
  // Animasi tab: the animation studio (captions_anim.js, cap.animUI) when loaded; it shows these classic pickers
  // inside its "Animasi dasar" section (cap.styleUI.animasiClassic).
  cap.styleUI.animasi = function (panel) { return cap.animUI ? cap.animUI.render(panel) : cap.styleUI.animasiClassic(panel); };
  cap.styleUI.animasiClassic = function (panel) {
    A.key = null;   // render() can run again (language switch)
    A.inSec = ui.section({ title: AC.t('cap.style.animIn') }); A.inGrid = grid(AC.t('cap.style.animInAria')); A.inSec.add(A.inGrid);
    A.hlSec = ui.section({ title: AC.t('cap.style.opActive') }); A.hlGrid = grid(AC.t('cap.style.hlAria')); A.hlSec.add(A.hlGrid);
    A.wiSec = ui.section({ title: AC.t('cap.style.wordIn') }); A.wiGrid = grid(AC.t('cap.style.wordInAria')); A.wiSec.add(A.wiGrid);
    A.outSec = ui.section({ title: AC.t('cap.style.animOut') }); A.outGrid = grid(AC.t('cap.style.animOutAria')); A.outSec.add(A.outGrid);
    var tsec = ui.section({ title: AC.t('cap.style.timing') });
    A.inDur = ui.slider({ label: AC.t('cap.style.inDur'), min: 40, max: 600, step: 20, value: 200, decimals: 0, unit: ' ms', onInput: function (v) { set('anim.in_dur', v / 1000, AC.t('cap.style.inDur')); } });
    A.outDur = ui.slider({ label: AC.t('cap.style.outDur'), min: 0, max: 600, step: 20, value: 120, decimals: 0, unit: ' ms', onInput: function (v) { set('anim.out_dur', v / 1000, AC.t('cap.style.outDur')); } });
    A.wordDur = ui.slider({ label: AC.t('cap.style.wordDur'), min: 40, max: 400, step: 20, value: 140, decimals: 0, unit: ' ms', onInput: function (v) { set('anim.word_in_dur', v / 1000, AC.t('cap.style.opWordDur')); } });
    A.pop = ui.slider({ label: AC.t('cap.style.pop'), min: 100, max: 140, step: 2, value: 100, decimals: 0, unit: '%', onInput: function (v) { set('highlight.scale', v / 100, AC.t('cap.style.opPop')); } });
    tsec.add([ui.h('div', { class: 'cap-ctl-grid' }, [A.inDur.el, A.outDur.el]), ui.h('div', { class: 'cap-ctl-grid' }, [A.wordDur.el, A.pop.el])]);
    tsec.add(ui.h('p', { class: 'help', html: AC.t('cap.style.animHelp', { icon: ui.icon('play'), sec: U.esc(AC.t('unit.sec')) }) }));
    [A.inSec, A.hlSec, A.wiSec, A.outSec, tsec].forEach(function (s) { panel.appendChild(s.el); });
    cap.bus.on('doc', paintAnim);
    cap.bus.on('templates', paintAnim);
    cap.bus.on('draft', U.debounce(paintAnim, 60));
    paintAnim();
  };
  function grid(label) { return ui.h('div', { class: 'cap-anim-grid', role: 'radiogroup', 'aria-label': label }); }
  function tile(label, cur, demo, onPick) {
    var b = ui.h('button', { class: 'cap-anim', type: 'button', role: 'radio', 'aria-checked': cur ? 'true' : 'false', title: label });
    b.appendChild(demo);
    b.appendChild(ui.h('span', { text: label }));
    b.addEventListener('click', function () {
      U.$$('.cap-anim', b.parentNode).forEach(function (x) { x.setAttribute('aria-checked', x === b ? 'true' : 'false'); });
      onPick();
    });
    return b;
  }
  function demoIn(an) {
    var d = ui.h('span', { class: 'cap-anim-g' });
    var s = ui.h('span', { class: an ? 'a' : '', text: 'Aa' });
    if (an) s.style.setProperty('--an', an);
    d.appendChild(s);
    return d;
  }
  function demoHl(id) {
    var d = ui.h('span', { class: 'cap-anim-g' }), an = HL[id];
    ['ka', 'ta', 'ku'].forEach(function (w, i) {
      var s = ui.h('span', { class: 'hw', text: w });
      if (i === 1 && an && an !== 'sweep') { s.className = 'hw a'; s.style.setProperty('--an', an); }
      if (i === 1 && an === 'sweep') s.className = 'hw hl-sweep';
      if (id === 'one_word' && i !== 1) s.style.visibility = 'hidden';
      if (id === 'dim' && i === 2) s.style.opacity = '.35';
      d.appendChild(s);
    });
    return d;
  }
  function paintAnim() {
    if (!A.inGrid || !S.doc || !S.tpl) return;
    var opts = S.tpl.options || {}, e = eff();
    var an = e.anim || {}, mode = (e.timing || {}).mode;
    var key = JSON.stringify([AC.i18n.lang(), an, mode, e.highlight, (opts.anim_in || []).length]);
    if (key === A.key) return;            // nothing changed: keep the tiles (and their running demos)
    A.key = key;
    A.inGrid.innerHTML = '';
    (opts.anim_in || []).forEach(function (o) {
      A.inGrid.appendChild(tile(o.label, an['in'] === o.id, demoIn(ANIM_IN[o.id]), function () { set('anim.in', o.id, AC.t('cap.style.animInAria')); }));
    });
    A.inSec.setAside(optLabel(opts.anim_in, an['in']));
    A.hlGrid.innerHTML = '';
    var hp = opts.highlight_presets || [], curHl = matchPreset(hp);
    hp.forEach(function (p) {
      A.hlGrid.appendChild(tile(p.label, curHl === p.id, demoHl(p.id), function () { cap.patchStyle(p.patch, { label: AC.t('cap.style.opActive') }); }));
    });
    A.hlSec.setAside(curHl ? optLabel(hp, curHl) : AC.t('common.custom'));
    A.wiSec.el.hidden = mode !== 'reveal';
    A.wiGrid.innerHTML = '';
    (opts.word_in || []).forEach(function (o) {
      A.wiGrid.appendChild(tile(o.label, an.word_in === o.id, demoIn(ANIM_IN[o.id]), function () { set('anim.word_in', o.id, AC.t('cap.style.opWordIn')); }));
    });
    A.outGrid.innerHTML = '';
    (opts.anim_out || []).forEach(function (o) {
      A.outGrid.appendChild(tile(o.label, an.out === o.id, demoIn(ANIM_OUT[o.id]), function () { set('anim.out', o.id, AC.t('cap.style.animOutAria')); }));
    });
    A.outSec.setAside(optLabel(opts.anim_out, an.out));
    A.inDur.set(Math.round((an.in_dur || 0.2) * 1000), true);
    A.outDur.set(Math.round((an.out_dur || 0) * 1000), true);
    A.wordDur.set(Math.round((an.word_in_dur || 0.14) * 1000), true);
    A.wordDur.el.hidden = mode !== 'reveal';
    A.pop.set(Math.round(((e.highlight || {}).scale || 1) * 100), true);
    A.pop.el.hidden = mode !== 'karaoke';
  }
})();
