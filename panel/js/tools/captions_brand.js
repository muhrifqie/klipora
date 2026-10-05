/* captions_brand.js: Auto Caption "Tiru gaya dari gambar" (Template tab) + Brand Kit (Gaya tab header, Settings).
   Engine: captions actions style_from_image, brandkit, brand_emphasis (docs/CAPTIONS_API.md section 12).
   Mounts itself into the editor panels built by captions.js (#cap-panel-template, #cap-panel-gaya) when they appear
   (cap.bus 'tab' / 'doc'), so the core files stay untouched. Public: AC.brandKit (open editor, settings row, tests).
   - Tiru gaya: paste (Ctrl+V), drop, pick a file, Tempel (Windows clipboard read by the engine) or the preview frame.
     The image goes to the engine as a downscaled PNG data URL in its own process (cancelable, AI can take ~30 dtk).
     Result: their image vs our render, attribute chips with confidence, "Pakai gaya ini" (template + doc_style ops
     through the edit queue = one undo step), "Simpan ke Gaya Saya" (engine library save via cap.lib.save).
   - Brand Kit: kits in %APPDATA%\Klipora\brandkit.json via the engine; editor sheet (swatches + hex, fonts from
     the catalog, brand words, emoji policy, tone, logo path). "Terapkan" = engine ops through cap.op (+ re-run of the
     glossary on the cached transcript when brand words were added). "Sorot kata penting (AI)" = reviewable list.
   Texts: AC.t cap.brand.* (Indonesian / English); kit tones + emoji levels come from the engine in the job language. */
(function () {
  'use strict';
  var AC = window.AC, U = AC.util, ui = AC.ui, cap = AC.cap;
  if (!cap) return;
  var S = cap.S;
  var B = AC.brandKit = {};
  var ST = { kits: null, active: null, tones: [], emoji: [], loadP: null, fonts: null, fontsP: null, sty: null, styJob: null,
             emph: null, emphJob: null, mounted: {} };
  var PALETTE = ['#FFFFFF', '#111111', '#FFD400', '#FF6A00', '#FE2C55', '#00A86B', '#22E58B', '#38BDF8', '#7C3AED', '#0B3D2E'];
  var COLOR_ROWS = ['primary', 'accent', 'text', 'background'];
  function colorLabel(k) { return AC.t('cap.brand.color.' + k); }
  function undoAct() { return { label: AC.t('cap.brand.undoAction'), run: function () { cap.undo(); } }; }
  function whyLabel(it) { var k = 'cap.brand.why.' + it.why; return AC.i18n.has(k) ? AC.t(k) : (it.label || it.why); }
  var MAX_SIDE = 1600;

  ui.addIcons('<symbol id="i-image" viewBox="0 0 20 20"><rect x="2.5" y="3.5" width="15" height="13" rx="2"/><circle cx="7.5" cy="8" r="1.5"/><path d="M17 13l-4-4-7 7"/></symbol>' +
              '<symbol id="i-palette" viewBox="0 0 20 20"><path d="M10 2.5a7.5 7.5 0 0 0 0 15c1 0 1.5-.6 1.5-1.4 0-1-1-1.3-1-2.3 0-.9.7-1.3 1.6-1.3H14a3.5 3.5 0 0 0 3.5-3.5c0-3.6-3.4-6.5-7.5-6.5z"/><circle cx="6.5" cy="9" r="1"/><circle cx="9" cy="6" r="1"/><circle cx="13" cy="6.8" r="1"/></symbol>');
  AC.keys.register([{ keyCode: 86, ctrlKey: true }]);          // Ctrl+V reaches the panel in CEP

  /* ---------------------------------------------------------------- engine helpers */
  function wd() { return S.workdir || AC.settings.workRoot(); }
  function work(action, params) {                              // fast calls through the persistent worker
    if (S.workdir) return cap.worker(action, params);
    return AC.engine.worker({ tool: 'captions', action: action, params: params || {}, seq: null, workdir: wd(), record: false, title: 'Auto Caption' });
  }
  function own(action, params, title, opts) {                  // long calls (AI) in their own process: cancelable, never block previews
    return AC.engine.run(U.assign({ tool: 'captions', action: action, params: params || {}, seq: null, workdir: wd(), title: title, record: true }, opts || {}));
  }
  function lbl(prefix, o) { return AC.i18n.has(prefix + o.id) ? AC.t(prefix + o.id) : (o.label || o.id); }
  function pct(c) { return Math.round((Number(c) || 0) * 100) + '%'; }
  function confKind(c) { return c >= 0.7 ? 'ok' : c >= 0.45 ? 'warn' : 'err'; }

  /* ---------------------------------------------------------------- brand kit store (engine `brandkit`) */
  B.load = function (force) {
    if (ST.loadP && !force && ST.loadLang === AC.i18n.lang()) return ST.loadP;
    ST.loadLang = AC.i18n.lang();        // tones / emoji labels come in the job language
    ST.loadP = work('brandkit', { op: 'get' }).promise.then(function (r) {
      ST.kits = r.kits || []; ST.active = r.active; ST.tones = r.tones || []; ST.emoji = r.emoji_levels || []; ST.defaults = r.defaults || {};
      paintBars();
      return r;
    }, function (e) { ST.loadP = null; throw e; });
    return ST.loadP;
  };
  function store(r) { ST.kits = r.kits || ST.kits; ST.active = r.active; ST.loadP = Promise.resolve(r); paintBars(); return r; }
  B.save = function (kit, activate) { return work('brandkit', { op: 'set', kit: kit, activate: !!activate }).promise.then(store); };
  B.remove = function (id) { return work('brandkit', { op: 'delete', id: id }).promise.then(store); };
  B.activate = function (id) { return work('brandkit', { op: 'activate', id: id }).promise.then(store); };
  B.active = function () { return (ST.kits || []).filter(function (k) { return k.id === ST.active; })[0] || null; };
  B.fonts = function () {
    if (ST.fontsP) return ST.fontsP;
    ST.fontsP = work('fonts', { system: true }).promise.then(function (r) { ST.fonts = r.fonts || []; return ST.fonts; }, function (e) { ST.fontsP = null; throw e; });
    return ST.fontsP;
  };

  /* ---------------------------------------------------------------- Terapkan Brand Kit */
  B.apply = function (kitId) {
    if (!S.doc) { ui.toast(AC.t('cap.brand.needDocApply'), 'info'); return Promise.resolve(null); }
    var name = '';
    return cap.idle().then(function () {
      return work('brandkit', { op: 'apply', id: kitId || ST.active || undefined }).promise;
    }).then(function (r) {
      name = r.kit.name;
      return cap.op(r.ops, { label: AC.t('cap.brand.kitName', { name: name }), now: true }).then(function () { return r; });
    }).then(function (r) {
      ui.toast(AC.t('cap.brand.applied', { name: name }) + (r.glossary_added.length ? AC.t('cap.brand.appliedGloss', { n: r.glossary_added.length }) : ''),
        { icon: 'check', action: undoAct() });
      if (r.rebuild && cap.text && cap.text.refreshWords) cap.text.refreshWords();     // brand spelling on the cached transcript
      return r;
    }).catch(function (e) {
      if (e && e.code === 'NO_KIT') { B.open({ fromGaya: true }); return null; }
      ui.toast(AC.t('cap.brand.errApply', { err: U.errMsg(e) }), { kind: 'err' });
      return null;
    });
  };

  /* ================================================================ Tiru gaya dari gambar (Template tab) */
  var T = {};
  function mountStyler(panel) {
    if (ST.mounted.tpl === panel) return;
    ST.mounted.tpl = panel;
    // Collapsed to one row by default (the Template tab's main job is picking a template); remembered per viewer.
    // Pasting an image, Tempel, a running job or a result opens it.
    var card = ui.h('section', { class: 'cap-sty card', 'aria-label': AC.t('cap.brand.styTitle') });
    var tgl = ui.h('button', { type: 'button', class: 'cap-sty-tgl', 'aria-expanded': 'false', 'aria-controls': 'cap-sty-body', title: AC.t('cap.brand.toggle') }, [
      ui.h('span', { class: 'cap-sty-ic', html: ui.icon('image') }),
      ui.h('b', { class: 'grow', text: AC.t('cap.brand.styTitle') }),
      ui.h('span', { class: 'cap-sty-chev', html: ui.icon('chev-d') })
    ]);
    var bQuick = ui.button({ label: AC.t('cap.brand.paste'), icon: 'copy', small: true, kind: 'ghost', title: AC.t('cap.brand.pasteTitle'), onClick: function () { run({ clipboard: true }, 'Clipboard'); } });
    bQuick.classList.add('cap-sty-quick');
    var head = ui.h('div', { class: 'cap-sty-h' }, [tgl, ui.html(ui.tag('AI', 'ai', 'spark')), bQuick]);
    var body = ui.h('div', { class: 'cap-sty-body', id: 'cap-sty-body' }, [
      ui.h('p', { class: 'help', text: AC.t('cap.brand.styHelp') })]);
    tgl.addEventListener('click', function () { setStyOpen(!T.open, true); });
    var file = ui.h('input', { type: 'file', accept: 'image/png,image/jpeg,image/webp,image/bmp', hidden: true, 'aria-hidden': 'true', tabIndex: -1 });
    var drop = ui.h('div', { class: 'cap-sty-drop', tabIndex: 0, role: 'button', 'aria-label': AC.t('cap.brand.dropAria') }, [
      ui.h('span', { html: ui.icon('image') }),
      ui.h('span', { class: 'cap-sty-drop-t', html: AC.i18n.html('cap.brand.dropTitle') }),
      ui.h('span', { class: 'cap-sty-drop-s', text: AC.t('cap.brand.dropSub') })
    ]);
    var bPick = ui.button({ label: AC.t('cap.brand.pickImage'), icon: 'file', small: true, kind: 'ghost', onClick: function () { file.value = ''; file.click(); } });
    var bClip = ui.button({ label: AC.t('cap.brand.paste'), icon: 'copy', small: true, kind: 'ghost', title: AC.t('cap.brand.clipTitle'), onClick: function () { run({ clipboard: true }, 'Clipboard'); } });
    var bFrame = ui.button({ label: AC.t('cap.brand.useFrame'), icon: 'playhead', small: true, kind: 'ghost', title: AC.t('cap.brand.useFrameTitle'), onClick: function () {
      var t = cap.preview && cap.preview.time ? cap.preview.time() : null;
      run({ frame: true, t: t }, AC.t('cap.brand.frameLabel'), { withSeq: true });
    } });
    var aiSw = ui.switch({ label: AC.t('cap.brand.aiLabel'), help: AC.t('cap.brand.aiHelp'), checked: T.ai !== false, onChange: function (v) { T.ai = v; } });
    var prog = ui.h('div', { class: 'cap-sty-prog', hidden: true, role: 'status', 'aria-live': 'polite' });
    var res = ui.h('div', { class: 'cap-sty-res', hidden: true });
    card.appendChild(head);
    body.appendChild(drop);
    body.appendChild(ui.h('div', { class: 'btn-row cap-sty-src' }, [bPick, bClip, bFrame]));
    body.appendChild(aiSw.el);
    body.appendChild(prog);
    body.appendChild(res);
    card.appendChild(body);
    card.appendChild(file);
    panel.insertBefore(card, panel.firstChild);
    T.card = card; T.drop = drop; T.prog = prog; T.res = res; T.btns = [bPick, bClip, bFrame, bQuick]; T.tgl = tgl; T.body = body;
    setStyOpen(!!AC.store.get('cap.sty.open', false) || !!ST.sty || !!ST.styJob, false);

    drop.addEventListener('click', function () { file.value = ''; file.click(); });
    drop.addEventListener('keydown', function (e) { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); file.value = ''; file.click(); } });
    file.addEventListener('change', function () { if (file.files && file.files[0]) fromFile(file.files[0], file.files[0].name); });
    drop.addEventListener('dragover', function (e) { e.preventDefault(); drop.classList.add('is-over'); });
    drop.addEventListener('dragleave', function () { drop.classList.remove('is-over'); });
    drop.addEventListener('drop', function (e) {
      e.preventDefault(); drop.classList.remove('is-over');
      var f = e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0];
      if (f) fromFile(f, f.name); else ui.toast(AC.t('cap.brand.dropNotFile'), 'info');
    });
    if (ST.sty) paintResult(ST.sty);
  }
  function setStyOpen(open, remember) {
    T.open = !!open;
    if (!T.card) return;
    T.card.classList.toggle('is-collapsed', !T.open);
    T.body.hidden = !T.open;
    T.tgl.setAttribute('aria-expanded', T.open ? 'true' : 'false');
    if (remember) AC.store.set('cap.sty.open', T.open);
  }
  // Ctrl+V anywhere while the Template tab is on screen (or the drop zone has focus); works while collapsed too
  document.addEventListener('paste', function (e) {
    if (!T.card || !document.body.contains(T.card) || T.card.offsetParent === null) return;
    if (AC.keys.typing() && document.activeElement !== T.drop) return;
    var items = (e.clipboardData && e.clipboardData.items) || [];
    for (var i = 0; i < items.length; i++) {
      if (items[i].kind === 'file' && /^image\//.test(items[i].type)) {
        var f = items[i].getAsFile();
        if (f) { e.preventDefault(); fromFile(f, AC.t('cap.brand.paste')); return; }
      }
    }
    if (document.activeElement === T.drop) { e.preventDefault(); run({ clipboard: true }, 'Clipboard'); }   // CEP: read the Windows clipboard in the engine
  });

  function fromFile(f, label) {
    if (!/^image\//.test(f.type || '') && !/\.(png|jpe?g|webp|bmp)$/i.test(f.name || '')) { ui.toast(AC.t('cap.brand.notImage'), { kind: 'err' }); return; }
    var rd = new FileReader();
    rd.onload = function () { shrink(String(rd.result), function (url) { run({ image: url }, label || AC.t('cap.brand.imageLabel')); }); };
    rd.onerror = function () { ui.toast(AC.t('cap.brand.imgRead'), { kind: 'err' }); };
    rd.readAsDataURL(f);
  }
  // Downscale big screenshots in the panel (keeps the job file small); PNG keeps the colours exact.
  function shrink(url, done) {
    var img = new Image();
    img.onload = function () {
      var w = img.naturalWidth, h = img.naturalHeight, k = Math.min(1, MAX_SIDE / Math.max(w, h, 1));
      if (k >= 1 && url.length < 6e6) { done(url); return; }
      var c = document.createElement('canvas');
      c.width = Math.max(1, Math.round(w * k)); c.height = Math.max(1, Math.round(h * k));
      c.getContext('2d').drawImage(img, 0, 0, c.width, c.height);
      done(c.toDataURL('image/png'));
    };
    img.onerror = function () { ui.toast(AC.t('cap.brand.imgOpen'), { kind: 'err' }); };
    img.src = url;
  }

  function run(params, label, opts) {
    if (ST.styJob) { ui.toast(AC.t('cap.brand.busy'), 'info'); return; }
    params.ai = T.ai !== false;
    params.portrait = !!(S.seq && S.seq.h > S.seq.w);
    if (S.seq) params.size = [S.seq.w, S.seq.h];
    // seq undefined = the current full timeline (needed for the preview frame); null otherwise
    var job = own('style_from_image', params, AC.t('cap.brand.styTitle'), opts && opts.withSeq ? { seq: undefined } : {});
    ST.styJob = job;
    setStyOpen(true, false);
    T.btns.forEach(function (b) { b.disabled = true; });
    T.prog.hidden = false; T.res.hidden = true;
    var bar = ui.h('div', { class: 'cap-sty-bar' }, [ui.h('i')]);
    var lbl = ui.h('span', { text: AC.t('cap.brand.reading', { src: label }) });
    var cancel = ui.button({ label: AC.t('common.cancelJob'), small: true, kind: 'ghost', onClick: function () { job.cancel(); } });
    T.prog.innerHTML = '';
    T.prog.appendChild(ui.h('div', { class: 'cap-sty-prog-row' }, [lbl, cancel]));
    T.prog.appendChild(bar);
    job.on('stage', function (s) { if (s && s.label) lbl.textContent = s.label + '...'; });
    job.on('progress', function () { bar.firstChild.style.width = Math.max(4, Math.min(100, job.pct || 0)) + '%'; });
    job.promise.then(function (r) {
      ST.sty = r;
      paintResult(r);
      if (r.warnings && r.warnings.length) AC.log.warn('stylist: ' + r.warnings.join(' | '));
    }, function (e) {
      if (e && e.code === 'CANCELLED') { ui.toast(AC.t('cap.brand.cancelled'), 'undo'); return; }
      T.res.hidden = false; T.res.innerHTML = '';
      T.res.appendChild(ui.alert({ kind: 'err', title: errTitle(e), text: (e && e.hint) || U.errMsg(e) }));
    }).then(function () {
      ST.styJob = null;
      T.prog.hidden = true;
      T.btns.forEach(function (b) { b.disabled = false; });
    });
  }
  function errTitle(e) {
    var c = e && e.code;
    return AC.t(c === 'NO_CAPTION_FOUND' ? 'cap.brand.errNoCaption' : c === 'NO_CLIPBOARD' ? 'cap.brand.errNoClip' :
      c === 'BAD_IMAGE' ? 'cap.brand.errBadImage' : c === 'NO_SEQ' ? 'cap.brand.errNoSeq' : 'cap.brand.errStyle');
  }

  function chipEl(c) {
    var kind = confKind(c.conf);
    var val = c.value === null || c.value === undefined ? '-' : String(c.value);
    var kids = [ui.h('span', { class: 'cap-sty-k', text: c.label })];
    var v = ui.h('span', { class: 'cap-sty-v' });
    if (c.color) { var sw = ui.h('i', { class: 'cap-sty-sw' }); sw.style.background = c.color; v.appendChild(sw); }
    v.appendChild(document.createTextNode(val));
    kids.push(v);
    kids.push(ui.h('span', { class: 'cap-sty-c is-' + kind, title: AC.t('cap.brand.confTitle', { pct: pct(c.conf), src: AC.t(c.src === 'ai+lokal' ? 'cap.brand.srcBoth' : c.src === 'ai' ? 'cap.brand.srcAi' : 'cap.brand.srcLocal') }), text: pct(c.conf) }));
    return ui.h('div', { class: 'cap-sty-chip', dataset: { id: c.id } }, kids);
  }
  function paintResult(r) {
    var el = T.res;
    if (!el) return;
    el.hidden = false; el.innerHTML = '';
    // zoomed on the caption by default (same region of both frames); click = whole frame
    var c = r.compare || {}, zoom = !!(c.theirs_zoom && c.ours_zoom);
    var cmp = ui.h('div', { class: 'cap-sty-cmp' + (zoom ? ' is-zoom' : ''), title: zoom ? AC.t('cap.brand.cmpWhole') : '' });
    var fig = function (full, z, cap1) {
      var src = zoom ? z : full;
      var img = src ? ui.h('img', { src: cap.fileUrl(src), alt: cap1 }) : null;
      if (img) { img._full = full; img._zoom = z; }
      return ui.h('figure', {}, [img || ui.h('div', { class: 'cap-sty-noimg', text: AC.t('cap.brand.noImage') }), ui.h('figcaption', { text: cap1 })]);
    };
    cmp.appendChild(fig(c.theirs || r.image, c.theirs_zoom, AC.t('cap.brand.yourImage')));
    cmp.appendChild(fig(c.ours, c.ours_zoom, AC.t('cap.brand.ourResult')));
    if (zoom) cmp.addEventListener('click', function () {
      zoom = !zoom; cmp.classList.toggle('is-zoom', zoom);
      U.$$('img', cmp).forEach(function (i) { var p = zoom ? i._zoom : i._full; if (p) i.src = cap.fileUrl(p); });
      cmp.title = zoom ? AC.t('cap.brand.cmpWhole') : AC.t('cap.brand.cmpZoom');
    });
    el.appendChild(cmp);
    var src = r.ai && r.ai.used ? ui.tag(r.source === 'mixed' ? AC.t('cap.brand.tagMixed') : 'AI', 'ai', 'spark') : ui.tag(AC.t('cap.brand.tagNoAi'), 'warn', 'alert');
    el.appendChild(ui.h('div', { class: 'cap-sty-meta', html: src + ' <span>' + AC.i18n.html('cap.brand.avgConf', { pct: pct(r.conf) }) + '</span>' + (r.text ? ' <span class="cap-sty-txt" data-i18n-skip>"' + U.esc(r.text) + '"</span>' : '') }));
    var grid = ui.h('div', { class: 'cap-sty-chips' });
    (r.chips || []).forEach(function (c) { grid.appendChild(chipEl(c)); });
    el.appendChild(grid);
    if (r.notes && r.notes.length) el.appendChild(ui.h('p', { class: 'help cap-sty-notes', text: r.notes.join('. ') + '.' }));
    if (!(r.ai && r.ai.used) && r.warnings && r.warnings.length) el.appendChild(ui.h('p', { class: 'help', text: AC.t('cap.brand.aiNotUsed') }));
    var bUse = ui.button({ label: AC.t('cap.brand.useStyle'), icon: 'check', small: true, kind: 'primary', onClick: function () { useStyle(r); } });
    var name = ui.h('input', { class: 'input', type: 'text', placeholder: AC.t('cap.brand.styleNamePh'), 'aria-label': AC.t('cap.brand.styleNameAria'), spellcheck: 'false', value: '' });
    var bSave = ui.button({ label: AC.t('cap.brand.saveMine'), icon: 'plus', small: true, onClick: function () { saveStyle(r, name); } });
    name.addEventListener('keydown', function (e) { if (e.key === 'Enter') { e.preventDefault(); saveStyle(r, name); } });
    el.appendChild(ui.h('div', { class: 'btn-row' }, [bUse]));
    el.appendChild(ui.h('div', { class: 'cap-gloss-add' }, [name, bSave]));
  }
  function useStyle(r) {
    if (!S.doc) { ui.toast(AC.t('cap.brand.needDoc'), 'info'); return; }
    cap.op([{ op: 'template', id: r.base }, { op: 'doc_style', style: r.style, replace: true }], { label: AC.t('cap.brand.fromImage'), now: true }).then(function () {
      ui.toast(AC.t('cap.brand.fromImageUsed'), { icon: 'check', action: undoAct() });
      if (cap.preview) cap.preview.request();
    });
  }
  function saveStyle(r, input) {
    var nm = (input.value || '').trim() || AC.t('cap.brand.fromImage');
    // same path as the dock "Simpan gaya" (engine `library save`): placeholder card, toast with Lihat
    if (cap.lib && cap.lib.save) {
      cap.lib.save(nm, { template: r.template, description: r.template.description }).then(function () { input.value = ''; }, function () { /* toast shown by LIB.save */ });
      return;
    }
    work('library', { op: 'save', from_doc: false, template: r.template, name: nm, description: r.template.description }).promise.then(function () {
      input.value = '';
      return cap.templates(true);
    }).then(function () { ui.toast(AC.t('cap.brand.savedMine', { name: nm }), 'check'); },
      function (e) { ui.toast(AC.t('cap.brand.saveStyleFail', { err: U.errMsg(e) }), { kind: 'err' }); });
  }

  /* ================================================================ Gaya tab header: Brand Kit bar + Sorot kata penting */
  var G = {};
  function mountBar(panel) {
    if (ST.mounted.gaya === panel) return;
    ST.mounted.gaya = panel;
    var bar = ui.h('section', { class: 'cap-bk-bar card', 'aria-label': 'Brand Kit' });
    G.dots = ui.h('span', { class: 'cap-bk-dots', 'aria-hidden': 'true' });
    G.name = ui.h('b');
    G.sub = ui.h('span', { class: 'cap-bk-sub' });
    var bApply = ui.button({ label: AC.t('cap.brand.apply'), icon: 'brush', small: true, kind: 'primary', onClick: function () { if (!ST.active) B.open({ fromGaya: true }); else B.apply(); } });
    var bEdit = ui.button({ label: AC.t('cap.brand.manage'), icon: 'palette', small: true, kind: 'ghost', onClick: function () { B.open({ fromGaya: true }); } });
    var bEmph = ui.button({ label: AC.t('cap.brand.emphBtn'), icon: 'marker', small: true, onClick: function () { emphasize(); } });
    bar.appendChild(ui.h('div', { class: 'cap-bk-row' }, [G.dots, ui.h('div', { class: 'grow cap-bk-t' }, [G.name, G.sub]), bEdit, bApply]));
    bar.appendChild(ui.h('div', { class: 'cap-bk-row2' }, [bEmph, ui.h('span', { class: 'help', text: AC.t('cap.brand.emphHelp') })]));
    G.review = ui.h('div', { class: 'cap-bk-review', hidden: true });
    bar.appendChild(G.review);
    G.bar = bar; G.apply = bApply; G.emph = bEmph;
    panel.insertBefore(bar, panel.firstChild);
    paintBars();
    if (ST.emph) paintReview(ST.emph);
  }
  function paintBars() {
    if (!G.bar) return;
    var k = B.active();
    G.dots.innerHTML = '';
    if (k) COLOR_ROWS.forEach(function (c) { var i = ui.h('i'); i.style.background = k.colors[c]; G.dots.appendChild(i); });
    else G.dots.innerHTML = ui.icon('palette');
    G.name.textContent = k ? AC.t('cap.brand.barName', { name: k.name }) : 'Brand Kit';
    G.sub.textContent = k ? ((k.words || []).length ? (k.words || []).slice(0, 3).join(', ') + ((k.words || []).length > 3 ? ', ...' : '') : AC.t('cap.brand.noWords')) :
      (ST.kits ? AC.t('cap.brand.noKit') : AC.t('common.loading'));
    G.apply.querySelector('.lbl') ? (G.apply.querySelector('.lbl').textContent = k ? AC.t('cap.brand.apply') : AC.t('cap.brand.create')) : null;
    if (SET.row) paintSettingsRow();
  }

  function emphasize() {
    if (!S.doc) { ui.toast(AC.t('cap.brand.needDoc'), 'info'); return; }
    if (ST.emphJob) return;
    G.emph.disabled = true;
    G.review.hidden = false; G.review.innerHTML = '';
    var lbl = ui.h('span', { text: AC.t('cap.brand.picking') });
    var cancel = ui.button({ label: AC.t('common.cancelJob'), small: true, kind: 'ghost', onClick: function () { if (ST.emphJob) ST.emphJob.cancel(); } });
    G.review.appendChild(ui.h('div', { class: 'cap-sty-prog-row', role: 'status' }, [lbl, cancel]));
    cap.idle().then(function () {
      var job = ST.emphJob = own('brand_emphasis', { id: ST.active || undefined, max_per_page: 1 }, AC.t('cap.brand.emphTitle'));   // i18n-ignore (param name)
      job.on('stage', function (s) { if (s && s.label) lbl.textContent = s.label + '...'; });
      return job.promise;
    }).then(function (r) {
      ST.emph = r;
      paintReview(r);
    }, function (e) {
      G.review.innerHTML = '';
      if (e && e.code === 'CANCELLED') { G.review.hidden = true; return; }
      G.review.appendChild(ui.alert({ kind: 'err', title: AC.t('cap.brand.emphFail'), text: U.errMsg(e) }));
    }).then(function () { ST.emphJob = null; G.emph.disabled = false; });
  }
  function paintReview(r) {
    var el = G.review;
    if (!el) return;
    el.hidden = false; el.innerHTML = '';
    var items = r.items || [];
    var src = r.source === 'ai' || r.source === 'cache' || r.source === 'mixed' ? ui.tag('AI', 'ai', 'spark') : ui.tag(AC.t('cap.brand.tagRules'), 'line');
    var head = ui.h('div', { class: 'cap-bk-rh', html: AC.i18n.html('cap.brand.suggested', { n: items.length }) + ' ' + src + (r.kit ? ' <span class="cap-bk-sub">' + AC.i18n.html('cap.brand.kitName', { name: r.kit.name }) + '</span>' : '') });
    el.appendChild(head);
    if (!items.length) { el.appendChild(ui.h('p', { class: 'help', text: AC.t('cap.brand.nothing') })); return; }
    var list = ui.h('div', { class: 'cap-bk-list', role: 'list' });
    items.forEach(function (it, i) {
      var cb = ui.h('input', { type: 'checkbox', 'aria-label': AC.t('cap.brand.highlightAria', { word: it.text }) });
      cb.checked = it.on !== false;
      cb.addEventListener('change', function () { it.on = cb.checked; paintCount(); });
      var w = ui.h('span', { class: 'cap-bk-w', 'data-i18n-skip': '', text: it.text });
      w.style.color = (it.style || {}).color || '';
      var row = ui.h('label', { class: 'cap-bk-it', role: 'listitem', dataset: { i: i } }, [cb, w,
        it.style && it.style.emoji ? ui.h('span', { class: 'cap-bk-emo', text: it.style.emoji }) : ui.h('span'),
        ui.html(ui.tag(whyLabel(it), it.why === 'brand' ? 'ai' : it.why === 'ai' ? 'ai' : 'line')),
        ui.h('button', { type: 'button', class: 'cap-bk-t0', title: AC.t('cap.brand.showPreview'), text: U.tcode(it.t0) })]);
      row.querySelector('.cap-bk-t0').addEventListener('click', function (e) { e.preventDefault(); if (cap.preview) cap.preview.show(it.t0 + 0.02); });
      list.appendChild(row);
    });
    el.appendChild(list);
    var bOk = ui.button({ label: AC.t('cap.brand.acceptAll'), icon: 'check', small: true, kind: 'primary', onClick: function () { acceptEmph(r); } });
    var bNo = ui.button({ label: AC.t('cap.brand.rejectAll'), icon: 'x', small: true, kind: 'ghost', onClick: function () { ST.emph = null; el.hidden = true; el.innerHTML = ''; } });
    el.appendChild(ui.h('div', { class: 'btn-row' }, [bOk, bNo]));
    function paintCount() {
      var n = items.filter(function (x) { return x.on !== false; }).length;
      bOk.querySelector('.lbl').textContent = n === items.length ? AC.t('cap.brand.acceptAllN', { n: n }) : AC.t('cap.brand.acceptN', { n: n });
      bOk.disabled = !n;
    }
    paintCount();
  }
  function acceptEmph(r) {
    var on = {};
    r.items.forEach(function (it) { if (it.on !== false) on[it.ids[0]] = true; });
    var ops = (r.ops || []).filter(function (o) { return on[o.ids[0]]; });
    // items switched on whose op was not prepared (should not happen): build it from the item style
    r.items.forEach(function (it) { if (it.on !== false && !ops.some(function (o) { return o.ids[0] === it.ids[0]; })) ops.push({ op: 'style', ids: it.ids.slice(), style: it.style }); });
    if (!ops.length) return;
    cap.op(ops, { label: AC.t('cap.brand.emphTitle'), now: true }).then(function () {
      ui.toast(AC.t('cap.brand.highlighted', { n: ops.length }), { icon: 'check', action: undoAct() });
      ST.emph = null; G.review.hidden = true; G.review.innerHTML = '';
      if (cap.preview) cap.preview.request();
    });
  }

  /* ================================================================ Brand Kit editor (sheet; from Gaya and Settings) */
  var E = {};
  B.open = function (opts) {
    opts = opts || {};
    var ret = E.sheet ? E.ret : document.activeElement;     // reopen (kit switch) keeps the original opener
    B.close(true);
    E.opts = opts;
    E.ret = ret;
    var scrim = ui.h('div', { class: 'cap-bk-scrim' });
    var sheet = ui.h('div', { class: 'cap-bk-sheet', role: 'dialog', 'aria-modal': 'true', 'aria-label': 'Brand Kit' });
    var bClose = ui.h('button', { class: 'ibtn', type: 'button', 'aria-label': AC.t('common.close'), title: AC.t('cap.brand.closeEsc'), html: ui.icon('x') });
    bClose.addEventListener('click', function () { B.close(); });
    scrim.addEventListener('click', function () { B.close(); });
    sheet.appendChild(ui.h('div', { class: 'cap-bk-sh' }, [ui.h('span', { html: ui.icon('palette') }), ui.h('b', { class: 'grow', text: 'Brand Kit' }), bClose]));
    E.kits = ui.h('div', { class: 'cap-bk-kits', 'data-i18n-skip': '', role: 'radiogroup', 'aria-label': AC.t('cap.brand.pickKit') });
    E.form = ui.h('div', { class: 'cap-bk-form' });
    sheet.appendChild(E.kits);
    sheet.appendChild(E.form);
    document.body.appendChild(scrim);
    document.body.appendChild(sheet);
    E.scrim = scrim; E.sheet = sheet;
    sheet.addEventListener('keydown', trapTab);
    E.unEsc = AC.keys.onEscape(function () { if (!E.sheet) return false; B.close(); return true; });
    E.form.appendChild(ui.h('p', { class: 'help', text: AC.t('cap.brand.loadingKit') }));
    Promise.all([B.load(true), B.fonts().catch(function () { return []; })]).then(function () {
      var k = opts.id ? (ST.kits || []).filter(function (x) { return x.id === opts.id; })[0] : B.active();
      edit(k ? U.copy(k) : null);
    }, function (e) { E.form.innerHTML = ''; E.form.appendChild(ui.alert({ kind: 'err', title: AC.t('cap.brand.loadFail'), text: U.errMsg(e) })); });
    setTimeout(function () { bClose.focus(); }, 0);
  };
  B.close = function (keepFocus) {
    if (E.unEsc) { E.unEsc(); E.unEsc = null; }
    var was = !!E.sheet;
    if (E.sheet) { E.sheet.remove(); E.scrim.remove(); }
    E.sheet = null; E.scrim = null;
    // back to the button that opened the sheet (Atur / Buat / Settings row)
    if (was && keepFocus !== true && E.ret && document.body.contains(E.ret) && E.ret.focus) E.ret.focus();
    if (keepFocus !== true) E.ret = null;
  };
  // aria-modal: Tab / Shift+Tab wrap inside the sheet
  function trapTab(e) {
    if (e.key !== 'Tab' || !E.sheet) return;
    var f = U.$$('button, [href], input, select, textarea, [tabindex]', E.sheet).filter(function (x) {
      return !x.disabled && x.tabIndex >= 0 && x.offsetParent !== null;
    });
    if (!f.length) return;
    var first = f[0], last = f[f.length - 1], a = document.activeElement;
    if (e.shiftKey && (a === first || !E.sheet.contains(a))) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && (a === last || !E.sheet.contains(a))) { e.preventDefault(); first.focus(); }
  }
  B.isOpen = function () { return !!E.sheet; };

  function paintKits(cur) {
    E.kits.innerHTML = '';
    (ST.kits || []).forEach(function (k) {
      var b = ui.h('button', { type: 'button', class: 'chip', role: 'radio', 'aria-checked': cur && cur.id === k.id ? 'true' : 'false', dataset: { id: k.id },
        html: '<i class="cap-bk-chipdot" style="background:' + U.esc(k.colors.primary) + '"></i>' + U.esc(k.name) + (k.id === ST.active ? ' ' + ui.icon('check') : '') });
      b.addEventListener('click', function () { edit(U.copy(k)); });
      E.kits.appendChild(b);
    });
    var add = ui.h('button', { type: 'button', class: 'chip chip-add', 'aria-pressed': !cur ? 'true' : 'false', html: ui.icon('plus') + U.esc(AC.t('cap.brand.newKit')) });
    add.addEventListener('click', function () { edit(null); });
    E.kits.appendChild(add);
  }

  function edit(kit) {
    var isNew = !kit;
    var d = ST.defaults || {};
    kit = kit || { name: '', colors: U.copy(d.colors || { primary: '#FF6A00', accent: '#FFD400', text: '#FFFFFF', background: '#111111' }),
      fonts: U.copy(d.fonts || { heading: 'Montserrat Black', body: 'Plus Jakarta Sans Bold' }), words: [], emoji: 'sedikit', tone: 'santai', logo: '' };
    E.kit = kit;
    paintKits(isNew ? null : kit);
    var f = E.form;
    f.innerHTML = '';
    var name = ui.input({ value: kit.name, placeholder: AC.t('cap.brand.namePh'), ariaLabel: AC.t('cap.brand.nameAria'), onInput: function (v) { kit.name = v; paintSample(); } });
    f.appendChild(ui.field({ label: AC.t('cap.brand.name'), control: name.el }));

    /* colours: swatches + hex field (input[type=color] may not open in CEP) */
    var cs = ui.section({ title: AC.t('cap.brand.colors') });
    COLOR_ROWS.forEach(function (key) {
      var row = [key, colorLabel(key)];
      var hex = ui.h('input', { class: 'input cap-bk-hex', type: 'text', value: kit.colors[key], maxlength: 7, spellcheck: 'false', 'aria-label': AC.t('cap.brand.colorCode', { name: row[1] }) });
      var sw = cap.swatches(PALETTE, kit.colors[key], function (c) { if (!c) return; kit.colors[key] = c; hex.value = c; hex.classList.remove('is-bad'); paintSample(); }, { none: false, label: AC.t('cap.brand.colorOf', { name: row[1] }) });
      hex.addEventListener('input', function () {
        var v = hex.value.trim(); if (v && v.charAt(0) !== '#') v = '#' + v;
        var ok = /^#[0-9a-fA-F]{6}$/.test(v);
        hex.classList.toggle('is-bad', !ok);
        if (ok) { kit.colors[key] = v.toUpperCase(); sw.set(kit.colors[key]); paintSample(); }
      });
      cs.add(ui.h('div', { class: 'cap-bk-crow', dataset: { key: key } }, [ui.h('span', { class: 'cap-bk-cl', text: row[1] }), sw, hex]));
    });
    f.appendChild(cs.el);

    /* fonts from the catalog (bundled first, then installed Windows fonts) */
    var fs = ui.section({ title: AC.t('cap.brand.fonts') });
    var opts = fontOptions();
    var head = ui.select({ ariaLabel: AC.t('cap.brand.fontHeadAria'), options: withCurrent(opts, kit.fonts.heading), value: kit.fonts.heading, onChange: function (v) { kit.fonts.heading = v; paintSample(); } });
    var body = ui.select({ ariaLabel: AC.t('cap.brand.fontBodyAria'), options: withCurrent(opts, kit.fonts.body), value: kit.fonts.body, onChange: function (v) { kit.fonts.body = v; } });
    fs.add(ui.h('div', { class: 'cap-ctl-grid' }, [ui.field({ label: AC.t('cap.brand.fontHead'), control: head.el }), ui.field({ label: AC.t('cap.brand.fontBody'), control: body.el })]));
    f.appendChild(fs.el);

    /* brand words */
    var ws = ui.section({ title: AC.t('cap.brand.words') });
    ws.add(ui.h('p', { class: 'help', text: AC.t('cap.brand.wordsHelp') }));
    var chips = ui.h('div', { class: 'chips-wrap cap-bk-words', 'data-i18n-skip': '' });      // user words
    var inp = ui.h('input', { class: 'input', type: 'text', placeholder: AC.t('cap.brand.wordPh'), 'aria-label': AC.t('cap.brand.wordAria'), spellcheck: 'false' });
    var add = ui.button({ label: AC.t('common.add'), icon: 'plus', small: true, onClick: function () { push(); } });
    inp.addEventListener('keydown', function (e) { if (e.key === 'Enter') { e.preventDefault(); push(); } });
    function push() {
      String(inp.value || '').split(/[,;]/).forEach(function (x) {
        x = x.replace(/\s+/g, ' ').trim();
        if (x && !kit.words.some(function (y) { return y.toLowerCase() === x.toLowerCase(); })) kit.words.push(x);
      });
      inp.value = ''; paintWords();
    }
    function paintWords() {
      chips.innerHTML = '';
      if (!kit.words.length) chips.appendChild(ui.h('span', { class: 'help', text: AC.t('cap.brand.noneYet') }));
      kit.words.forEach(function (w, i) {
        var c = ui.h('button', { type: 'button', class: 'chip', title: AC.t('cap.brand.removeWord', { word: w }), html: U.esc(w) + ui.icon('x') });
        c.addEventListener('click', function () { kit.words.splice(i, 1); paintWords(); });
        chips.appendChild(c);
      });
    }
    paintWords();
    ws.add([chips, ui.h('div', { class: 'cap-gloss-add' }, [inp, add.el || add])]);
    f.appendChild(ws.el);

    /* emoji + tone + logo */
    var ms = ui.section({ title: AC.t('cap.brand.styleTitle') });
    // labels by id from the panel locale (the engine list may be from the other language after a switch)
    var emo = ui.segmented({ small: true, ariaLabel: 'Emoji', value: kit.emoji, options: (ST.emoji.length ? ST.emoji : [{ id: 'none' }, { id: 'sedikit' }, { id: 'banyak' }]).map(function (o) { return { value: o.id, label: lbl('cap.brand.emoji.', o) }; }),
      onChange: function (v) { kit.emoji = v; } });
    var tone = ui.select({ ariaLabel: AC.t('cap.brand.tone'), value: kit.tone, options: (ST.tones.length ? ST.tones : [{ id: 'santai' }]).map(function (o) { return { value: o.id, label: lbl('cap.brand.tone.', o) }; }),
      onChange: function (v) { kit.tone = v; } });
    ms.add(ui.h('div', { class: 'cap-ctl-grid' }, [ui.field({ label: 'Emoji', control: emo.el }), ui.field({ label: AC.t('cap.brand.tone'), control: tone.el, help: AC.t('cap.brand.toneHelp') })]));
    var logo = ui.input({ value: kit.logo || '', mono: true, placeholder: AC.t('cap.brand.logoPh'), ariaLabel: AC.t('cap.brand.logoAria'), onInput: function (v) { kit.logo = v.trim(); } });
    ms.add(ui.field({ label: AC.t('cap.brand.logo'), control: logo.el, help: AC.t('cap.brand.logoHelp') }));
    f.appendChild(ms.el);

    /* live sample (CSS, no engine call) */
    E.sample = ui.h('div', { class: 'cap-bk-sample', 'aria-label': AC.t('cap.brand.sampleAria') });
    f.appendChild(E.sample);
    paintSample();

    var bSave = ui.button({ label: E.opts.fromGaya && S.doc ? AC.t('cap.brand.saveApply') : AC.t('common.save'), icon: 'check', kind: 'primary', small: true, onClick: function () { save(isNew, E.opts.fromGaya && S.doc); } });
    var row = [bSave];
    if (!isNew && kit.id !== ST.active) row.push(ui.button({ label: AC.t('cap.brand.makeActive'), icon: 'check', small: true, onClick: function () { B.activate(kit.id).then(function () { ui.toast(AC.t('cap.brand.activeToast', { name: kit.name }), 'check'); edit(U.copy(B.active())); }); } }));
    if (!isNew) {
      var del = ui.button({ label: AC.t('common.delete'), icon: 'trash', small: true, kind: 'ghost-danger' });
      ui.confirmClick(del, function () { B.remove(kit.id).then(function () { ui.toast(AC.t('cap.brand.deletedToast', { name: kit.name }), 'trash'); edit(B.active() ? U.copy(B.active()) : null); }); }, AC.t('common.confirm'));
      row.push(del);
    }
    f.appendChild(ui.h('div', { class: 'btn-row cap-bk-actions' }, row));
    E.status = ui.h('p', { class: 'help', role: 'status' });
    f.appendChild(E.status);
  }
  function save(isNew, andApply) {
    var k = E.kit;
    if (!String(k.name || '').trim()) { E.status.textContent = AC.t('cap.brand.needName'); ui.toast(AC.t('cap.brand.needKitName'), { kind: 'err' }); return; }
    B.save(k, andApply || !ST.active).then(function (r) {          // a new kit becomes active only when applied or the first
      ui.toast(AC.t('cap.brand.savedToast', { name: r.kit.name }), 'check');
      if (r.warnings && r.warnings.length) E.status.textContent = r.warnings.join(' ');
      if (andApply) { B.close(); B.apply(r.kit.id); return; }
      edit(U.copy(r.kit));
      if (r.warnings && r.warnings.length) E.status.textContent = r.warnings.join(' ');
    }, function (e) { E.status.textContent = U.errMsg(e); ui.toast(AC.t('cap.brand.saveFail', { err: U.errMsg(e) }), { kind: 'err' }); });
  }
  function fontOptions() {
    var out = [], seen = {};
    (ST.fonts || []).forEach(function (r) {
      if (seen[r.family]) return;
      seen[r.family] = true;
      out.push({ value: r.family, label: r.family + (r.source === 'system' ? ' (Windows)' : '') });
    });
    return out.slice(0, 500);
  }
  function withCurrent(opts, cur) {
    if (!cur || opts.some(function (o) { return o.value === cur; })) return opts.length ? opts : [{ value: cur || '', label: cur || '-' }];
    return [{ value: cur, label: AC.t('cap.brand.fontMissing', { name: cur }) }].concat(opts);
  }
  function paintSample() {
    if (!E.sample || !E.kit) return;
    var c = E.kit.colors;
    var brand = (E.kit.words && E.kit.words[0]) || E.kit.name || AC.t('cap.brand.sampleBrand');
    E.sample.style.background = c.background;
    E.sample.innerHTML = '<span style="color:' + U.esc(c.text) + '">' + U.esc(AC.t('cap.brand.sampleA')) + '</span><span style="color:' + U.esc(c.primary) + '">' + U.esc(brand) +
      '</span><span style="color:' + U.esc(c.text) + '">' + U.esc(AC.t('cap.brand.sampleB')) + '</span><span style="color:' + U.esc(c.accent) + '">' + U.esc(U.int(50000)) + '</span>';
    E.sample.style.fontFamily = '"' + E.kit.fonts.heading.replace(/"/g, '') + '", var(--font-display)';
  }

  /* ================================================================ Settings row (Pengaturan > Caption) */
  var SET = {};
  B.settingsRow = function () {
    var el = ui.h('div', { class: 'cap-bk-set' });
    SET.dots = ui.h('span', { class: 'cap-bk-dots', 'aria-hidden': 'true' });
    SET.t = ui.h('span', { class: 'cap-bk-sub' });
    var btn = ui.button({ label: AC.t('cap.brand.manageKit'), icon: 'palette', small: true, kind: 'ghost', onClick: function () { B.open({ fromSettings: true }); } });
    el.appendChild(ui.h('div', { class: 'cap-bk-row' }, [SET.dots, ui.h('div', { class: 'grow cap-bk-t' }, [ui.h('b', { text: 'Brand Kit' }), SET.t]), btn]));
    SET.row = el;
    paintSettingsRow();
    B.load().catch(function (e) { AC.log.warn('brandkit: ' + U.errMsg(e)); });
    return el;
  };
  function paintSettingsRow() {
    if (!SET.row) return;
    var k = B.active();
    SET.dots.innerHTML = '';
    if (k) COLOR_ROWS.forEach(function (c) { var i = ui.h('i'); i.style.background = k.colors[c]; SET.dots.appendChild(i); });
    SET.t.textContent = k ? AC.t('cap.brand.activeRow', { name: k.name }) + ((ST.kits || []).length > 1 ? AC.t('cap.brand.kitCount', { n: ST.kits.length }) : '') : AC.t('cap.brand.setHelp');
  }
  AC.bus.on('route', function () { if (SET.row && document.body.contains(SET.row)) B.load(true).catch(function () {}); });

  /* ---------------------------------------------------------------- mount into the editor */
  function mountAll() {
    var tp = document.getElementById('cap-panel-template');
    if (tp) mountStyler(tp);
    var gp = document.getElementById('cap-panel-gaya');   // i18n-ignore (element id)
    if (gp) { mountBar(gp); if (!ST.kits && !ST.loadP) B.load().catch(function (e) { AC.log.warn('brandkit: ' + U.errMsg(e)); }); }
  }
  cap.bus.on('tab', mountAll);
  cap.bus.on('doc', mountAll);
  // language switch: tones / emoji labels come from the engine in the job language; the open sheet is rebuilt
  AC.bus.on('locale', function () {
    ST.loadP = null;
    if (E.sheet) { var o = E.opts, k = E.kit; B.open(U.assign({}, o, { id: k && k.id ? k.id : undefined })); }
    if (SET.row && document.body.contains(SET.row)) paintSettingsRow();
  });

  B.state = function () {
    return { kits: ST.kits, active: ST.active, sty: ST.sty ? { base: ST.sty.base, source: ST.sty.source, conf: ST.sty.conf, chips: (ST.sty.chips || []).length } : null,
             emph: ST.emph ? (ST.emph.items || []).length : null, styBusy: !!ST.styJob, emphBusy: !!ST.emphJob, open: !!E.sheet };
  };
})();
