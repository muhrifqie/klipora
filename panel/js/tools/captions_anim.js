/* captions_anim.js: Auto Caption "Animasi" tab = animation studio + sound effects per word (AC.cap.animUI).
   Engine: engine/ac/captions/anim.py + sfx.py (docs/CAPTIONS_API.md section 11). captions_style.js delegates its
   Animasi tab here (cap.styleUI.animasi -> cap.animUI.render) and keeps the classic pickers
   (cap.styleUI.animasiClassic) which we show inside "Animasi dasar".
   Views (segmented): Pustaka = preset grid per role (Masuk / Kata aktif / Keluar / Loop) with animated sprite-sheet
   previews (engine anim_sprites, cached PNG strips), duration + stagger per role; Studio = keyframe editor (track
   list, dots on a mini timeline: drag / arrow keys to retime, Delete removes, double click adds), value + easing per
   key, live sprite of the draft, "Putar di pratinjau" (applies the draft, then the preview's 3 s loop), save as a
   custom animation (%APPDATA%\Klipora\caption_anims.json); Efek suara = rules (Kata penting, Angka, Emoji, Setiap
   halaman), sound + gain per rule with a play button, volume / per-minute cap / min gap, own sounds, plan list,
   "Terapkan ke timeline" (engine sfx_render -> host bac_csfx_apply on audio track "Klipora SFX"; old projects:
   "AutoCut SFX"). Every UI text goes through AC.t (cap.anim.*, cap.sfx.*); engine catalogs (presets, sounds) come
   translated per job language and are reloaded after a language switch.
   Styles: css/tools/captions.css (section "Animasi studio + efek suara"). Test: tools/ui_tests/captions_anim.mjs. */
(function () {
  'use strict';
  var AC = window.AC, U = AC.util, ui = AC.ui, cap = AC.cap, S = cap.S;
  var ROLES = ['in', 'active', 'out', 'loop'];
  var F = { cat: null, catP: null, sprites: {}, spriteP: null, view: 'pustaka', grids: {}, key: null };
  var X = {};          // sound effects state (Efek suara view)
  cap.animUI = { F: F };
  function roleLabel(role) { return AC.t('cap.anim.role.' + role); }
  function undoAct() { return { label: AC.t('cap.anim.undoAction'), run: function () { cap.undo(); } }; }
  function secs(ms) { return U.dec(ms / 1000, 2) + ' ' + AC.t('unit.sec'); }
  // engine catalogs (preset / track / easing labels, sound + rule labels) are in the job language: catalog() and
  // sfxWarm() refetch when the language differs from the one they were loaded in (see F.catLang / X.libLang).

  function eff() { return cap.eff() || {}; }
  function fxOf() { var f = cap.get(eff(), 'anim.fx'); return f && typeof f === 'object' ? f : {}; }
  function curId(role) {
    var v = fxOf()[role];
    if (!v) return 'none';
    if (typeof v === 'string') return v;
    return v.preset || v.id || 'custom';
  }
  function presetById(id) {
    var all = ((F.cat && F.cat.presets) || []).concat((F.cat && F.cat.user) || []);
    for (var i = 0; i < all.length; i++) if (all[i].id === id) return all[i];
    return null;
  }
  // Effective spec of a role (preset merged with the overrides stored in the doc).
  function specOf(role) {
    var v = fxOf()[role];
    if (!v) return null;
    if (typeof v === 'string') return presetById(v);
    var base = v.preset ? presetById(v.preset) : null;
    return cap.merge(base || {}, v.tracks ? v : U.assign({}, v, { tracks: (base || {}).tracks }));
  }
  function setFx(role, value, label) { return cap.setStyle('anim.fx.' + role, value, { label: label || AC.t('cap.anim.undo') }); }

  /* ---------------------------------------------------------------- engine data */
  function catalog(force) {
    if (F.catP && !force && F.catLang === AC.i18n.lang()) return F.catP;
    F.catLang = AC.i18n.lang();          // labels come in the job language: refetch after a switch
    F.key = null;
    F.catP = cap.worker('anim', {}).promise.then(function (r) {
      var again = !!F.cat;
      F.cat = r;
      if (again && F.d && F.st) paintStudio(true);         // reloaded (language switch): relabel the open draft
      return r;
    }, function (e) { F.catP = null; throw e; });
    return F.catP;
  }
  function loadSprites(force) {
    if (F.spriteP && !force) return F.spriteP;
    F.spriteP = cap.worker('anim_sprites', { ids: 'all', frames: 16, size: [176, 64], force: !!force }).promise.then(function (r) {
      Object.keys(r.sprites || {}).forEach(function (k) { F.sprites[k] = r.sprites[k]; });
      return r;
    }, function (e) { F.spriteP = null; ui.toast(AC.t('cap.anim.errSprites', { err: U.errMsg(e) }), { kind: 'err' }); });
    return F.spriteP;
  }
  function sprite(info, big) {
    var d = ui.h('span', { class: 'cap-afx-spr' + (big ? ' is-big' : ''), 'aria-hidden': 'true' });
    if (!info) { d.classList.add('is-empty'); return d; }
    d.style.backgroundImage = 'url("' + cap.fileUrl(info.png) + '")';
    d.style.backgroundSize = (info.frames * 100) + '% 100%';
    d.style.animationDuration = Math.max(0.4, info.dur * 1.6).toFixed(2) + 's';
    d.style.animationTimingFunction = 'steps(' + info.frames + ', jump-none)';
    return d;
  }

  /* ---------------------------------------------------------------- render */
  cap.animUI.render = function (panel) {
    F.panel = panel;
    var top = ui.h('div', { class: 'cap-afx-top' });
    F.seg = ui.segmented({ small: true, ariaLabel: AC.t('cap.anim.viewsAria'), value: F.view, options: [
      { value: 'pustaka', label: AC.t('cap.anim.viewLib') }, { value: 'studio', label: AC.t('cap.anim.viewStudio') }, { value: 'sfx', label: AC.t('cap.anim.viewSfx') }],
      onChange: function (v) { showView(v); } });
    top.appendChild(F.seg.el);
    panel.appendChild(top);
    F.views = { pustaka: ui.h('div', { class: 'cap-afx-view' }), studio: ui.h('div', { class: 'cap-afx-view' }), sfx: ui.h('div', { class: 'cap-afx-view' }) };
    Object.keys(F.views).forEach(function (k) { panel.appendChild(F.views[k]); });
    buildLibrary(F.views.pustaka);
    buildStudio(F.views.studio);
    buildSfx(F.views.sfx);
    showView(F.view);
    cap.bus.on('doc', paint);
    cap.bus.on('templates', paint);
    cap.bus.on('draft', U.debounce(paint, 60));
    cap.bus.on('tab', function (id) { if (id === 'animasi') warm(); });
    if (S.tab === 'animasi') warm();
  };
  function warm() {
    catalog().then(function () {
      paint();
      if (F.view === 'studio' && !F.d) studioLoad('in', null);
      return loadSprites();
    }).then(function () { F.key = null; paint(); })
      .catch(function (e) { ui.toast(AC.t('cap.anim.errCatalog', { err: U.errMsg(e) }), { kind: 'err' }); });
  }
  function showView(v) {
    F.view = v;
    Object.keys(F.views).forEach(function (k) { F.views[k].hidden = k !== v; });
    if (v === 'studio' && F.cat && !F.d) studioLoad('in', null);
    if (v === 'sfx') sfxWarm();
  }
  cap.animUI.show = function (v) { F.seg.set(v); };

  /* ================================================================ Pustaka */
  function buildLibrary(el) {
    ROLES.forEach(function (role) {
      var r = [role, roleLabel(role)];
      var sec = ui.section({ title: r[1] });
      var g = ui.h('div', { class: 'cap-afx-grid', role: 'radiogroup', 'aria-label': AC.t('cap.anim.gridAria', { role: r[1].toLowerCase() }), dataset: { role: r[0] } });
      g.addEventListener('keydown', gridKeys);
      sec.add(g);
      var dur = ui.slider({ label: r[0] === 'loop' ? AC.t('cap.anim.loopPeriod') : AC.t('cap.anim.duration'), min: 40, max: r[0] === 'loop' ? 4000 : 1500, step: 10, value: 300, decimals: 0,
        format: secs, onChange: function (v) { tweak(r[0], 'dur', v / 1000); } });
      var stg = ui.slider({ label: r[0] === 'loop' ? AC.t('cap.anim.phase') : AC.t('cap.anim.stagger'), min: 0, max: 300, step: 5, value: 50, decimals: 0,
        format: function (v) { return v ? secs(v) : AC.t('cap.anim.together'); }, onChange: function (v) { tweak(r[0], 'stagger', v / 1000); } });
      var edit = ui.button({ label: AC.t('cap.anim.editStudio'), icon: 'sliders', small: true, kind: 'ghost', onClick: function () { studioLoad(r[0], specOf(r[0])); F.seg.set('studio'); } });
      var more = ui.h('div', { class: 'cap-afx-more' }, [ui.h('div', { class: 'cap-ctl-grid' }, [dur.el, stg.el]), edit]);
      sec.add(more);
      F.grids[r[0]] = { sec: sec, grid: g, dur: dur, stg: stg, more: more };
      el.appendChild(sec.el);
    });
    el.appendChild(ui.h('p', { class: 'help', html: U.esc(AC.t('cap.anim.libHelp')).replace('{icon}', ui.icon('play')) }));
    var adv = ui.advanced({ title: AC.t('cap.anim.classic'), key: 'cap-anim-classic' });
    if (cap.styleUI.animasiClassic) cap.styleUI.animasiClassic(adv.body);
    // picking a classic entrance / exit switches the studio animation of that role off (same edit)
    adv.body.addEventListener('click', function (e) {
      var t = e.target.closest ? e.target.closest('.cap-anim') : null;
      if (!t) return;
      var grids = U.$$('.cap-anim-grid', adv.body), gi = grids.indexOf(t.parentNode);
      var role = gi === 0 ? 'in' : gi === 3 ? 'out' : null;
      if (role && fxOf()[role]) setFx(role, null, AC.t('cap.anim.classic'));
    }, true);
    el.appendChild(adv.el);
    F.classic = adv;
  }
  // Tiles per row as laid out (the grid is 2 / 3 / 4 columns by width): count tiles on the first tile's row.
  function gridCols(tiles) {
    var top = tiles.length ? tiles[0].offsetTop : 0, n = 0;
    while (n < tiles.length && tiles[n].offsetTop === top) n++;
    return Math.max(1, n);
  }
  // Radio pattern: arrows move focus AND select the tile.
  function gridKeys(e) {
    var tiles = U.$$('.cap-afx', e.currentTarget), cols = gridCols(tiles);
    var keys = { ArrowRight: 1, ArrowDown: cols, ArrowLeft: -1, ArrowUp: -cols, Home: -9999, End: 9999 };
    if (!(e.key in keys)) return;
    var i = tiles.indexOf(document.activeElement);
    if (i < 0) return;
    e.preventDefault();
    var n = U.clamp(i + keys[e.key], 0, tiles.length - 1);
    if (n === i) return;
    var g = e.currentTarget, id = tiles[n].dataset.id;
    tiles[n].focus();
    tiles[n].click();
    // picking repaints the grid (new tile nodes): keep the focus on the picked tile
    var refocus = function () { var t = U.$('.cap-afx[data-id="' + id + '"]', g); if (t && document.activeElement !== t) t.focus(); };
    refocus();
    setTimeout(refocus, 0);
  }
  function tweak(role, key, v) {
    var cur = fxOf()[role];
    if (!cur) return;
    var o = typeof cur === 'string' ? { preset: cur } : U.copy(cur);
    o[key] = Math.round(v * 1000) / 1000;
    setFx(role, o, key === 'dur' ? AC.t('cap.anim.undoDur') : AC.t('cap.anim.stagger'));
  }
  function pick(role, p) {
    if (!p) return setFx(role, null, AC.t('cap.anim.undoNamed', { name: roleLabel(role) }));
    var cur = fxOf()[role], keep = {};
    if (cur && typeof cur === 'object' && cur.preset) ['dur', 'stagger'].forEach(function (k) { if (cur[k] !== undefined && cur.preset === p.id) keep[k] = cur[k]; });
    if (p.user) return setFx(role, U.assign(U.copy(p), { group: role }), AC.t('cap.anim.undoNamed', { name: p.label }));       // inline: survives a lost file
    return setFx(role, Object.keys(keep).length ? U.assign({ preset: p.id }, keep) : p.id, AC.t('cap.anim.undoNamed', { name: p.label }));
  }
  function tile(role, p, cur) {
    var label = p ? p.label : AC.t('cap.anim.none'), id = p ? p.id : 'none';
    var b = ui.h('button', { class: 'cap-afx', type: 'button', role: 'radio', 'aria-checked': cur === id ? 'true' : 'false', title: p && p.user ? AC.t('cap.anim.mineTitle', { name: label }) : label,
                             tabIndex: cur === id ? 0 : -1, dataset: { id: id } });
    b.appendChild(p ? sprite(F.sprites[id]) : ui.h('span', { class: 'cap-afx-spr is-none', 'aria-hidden': 'true', text: 'Aa' }));
    b.appendChild(ui.h('span', { class: 'cap-afx-l', text: label }));
    if (p && p.user) b.appendChild(ui.h('i', { class: 'cap-afx-mine', text: AC.t('cap.anim.mineTag') }));
    b.addEventListener('click', function () {
      U.$$('.cap-afx', b.parentNode).forEach(function (x) { x.setAttribute('aria-checked', x === b ? 'true' : 'false'); x.tabIndex = x === b ? 0 : -1; });
      pick(role, p);
    });
    return b;
  }
  function paintLibrary() {
    if (!F.cat || !S.doc) return;
    var fx = fxOf(), key = JSON.stringify([fx, Object.keys(F.sprites).length, (F.cat.user || []).map(function (u) { return u.id; })]);
    if (key === F.key) return;
    F.key = key;
    ROLES.forEach(function (role) {
      var r = [role];
      var G = F.grids[r[0]], cur = curId(r[0]);
      if (!G) return;
      if (cur === 'custom') cur = '';
      G.grid.innerHTML = '';
      G.grid.appendChild(tile(r[0], null, cur));
      F.cat.presets.concat(F.cat.user || []).forEach(function (p) { if (p.group === r[0]) G.grid.appendChild(tile(r[0], p, cur)); });
      if (!U.$('[aria-checked="true"]', G.grid)) { var f = G.grid.firstChild; if (f) f.tabIndex = 0; }
      var sp = specOf(r[0]);
      G.sec.setAside(sp ? (sp.label || AC.t('cap.anim.custom')) : AC.t('cap.anim.none'));
      G.more.hidden = !sp;
      if (sp) {
        G.dur.set(Math.round((sp.dur || 0.3) * 1000), true);
        G.stg.set(Math.round((sp.stagger || 0) * 1000), true);
        G.stg.el.hidden = r[0] === 'active' || sp.unit === 'page';
      }
    });
  }

  /* ================================================================ Studio */
  var D = null;          // draft {role, spec, track, key}
  function blankSpec(role) {
    var tr = role === 'active' ? { scale: [{ t: 0, v: 1, e: 'back' }, { t: 1, v: 1.2, e: 'linear' }] }
      : role === 'loop' ? { y: [{ t: 0, v: 0, e: 'in_out' }, { t: 0.5, v: -10, e: 'in_out' }, { t: 1, v: 0, e: 'linear' }] }
      : role === 'out' ? { opacity: [{ t: 0, v: 1, e: 'in' }, { t: 1, v: 0, e: 'linear' }] }
      : { opacity: [{ t: 0, v: 0, e: 'out' }, { t: 1, v: 1, e: 'linear' }] };
    return { id: 'custom', label: AC.t('cap.anim.myAnim'), group: role, dur: role === 'loop' ? 1.6 : 0.35, stagger: role === 'in' ? 0.05 : 0, order: 'forward', unit: 'word', release: 0.15, tracks: tr };
  }
  function trackInfo(id) { var l = (F.cat && F.cat.tracks) || []; for (var i = 0; i < l.length; i++) if (l[i].id === id) return l[i]; return { id: id, label: id }; }
  function studioLoad(role, spec) {
    var s = spec ? U.copy(spec) : blankSpec(role);
    s.group = role;
    if (!s.tracks) s.tracks = {};
    D = F.d = { role: role, spec: s, track: Object.keys(s.tracks)[0] || 'opacity', key: 0, id: spec && spec.user ? spec.id : null };
    D.name = spec && spec.user ? spec.label : (spec ? AC.t('cap.anim.copyName', { name: spec.label }) : AC.t('cap.anim.myAnim'));
    if (!F.st) return;
    F.st.role.set(role, true);
    F.st.name.set(D.name);
    paintStudio(true);
  }
  function buildStudio(el) {
    var st = F.st = {};
    var head = ui.section({ title: AC.t('cap.anim.studioTitle') });
    st.role = ui.segmented({ small: true, ariaLabel: AC.t('cap.anim.roleAria'), options: ROLES.map(function (r) { return { value: r, label: roleLabel(r) }; }), value: D ? D.role : 'in',
      onChange: function (v) { studioLoad(v, specOf(v)); } });
    st.base = ui.select({ ariaLabel: AC.t('cap.anim.startFromAria'), options: [], onChange: function (v) { if (v) studioLoad(D.role, v === '_blank' ? null : presetById(v)); } });
    st.name = ui.input({ value: D ? String(D.name || '') : AC.t('cap.anim.myAnim'), placeholder: AC.t('cap.anim.nameAria'), ariaLabel: AC.t('cap.anim.nameAria'),
      onInput: function (v) { if (D) D.name = v; } });
    head.add([st.role.el, ui.h('div', { class: 'cap-afx-row' }, [ui.h('label', { class: 'lbl', text: AC.t('cap.anim.startFrom') }), st.base.el]),
              ui.h('div', { class: 'cap-afx-row' }, [ui.h('label', { class: 'lbl', text: AC.t('cap.anim.name') }), st.name.el])]);
    // live preview of the draft (sprite strip from the engine)
    st.prev = ui.h('div', { class: 'cap-afx-prev', 'aria-live': 'polite' }, [ui.h('span', { class: 'cap-afx-spr is-big is-empty' })]);
    st.play = ui.button({ label: AC.t('cap.anim.playPreview'), icon: 'play', small: true, onClick: playDraft });
    st.use = ui.button({ label: AC.t('cap.anim.use'), icon: 'check', small: true, kind: 'primary', onClick: function () { applyDraft(true); } });
    head.add([st.prev, ui.h('div', { class: 'cap-afx-btns' }, [st.use, st.play])]);
    el.appendChild(head.el);

    var tim = ui.section({ title: AC.t('cap.anim.timing') });
    st.dur = ui.slider({ label: AC.t('cap.anim.duration'), min: 20, max: 3000, step: 10, value: 350, decimals: 0, format: secs, onInput: function (v) { D.spec.dur = v / 1000; changed(); } });
    st.stg = ui.slider({ label: AC.t('cap.anim.stagger'), min: 0, max: 400, step: 5, value: 50, decimals: 0, format: function (v) { return v ? secs(v) : AC.t('cap.anim.together'); }, onInput: function (v) { D.spec.stagger = v / 1000; changed(); } });
    st.order = ui.select({ ariaLabel: AC.t('cap.anim.orderAria'), options: [], onChange: function (v) { D.spec.order = v; changed(); } });
    st.unit = ui.segmented({ small: true, ariaLabel: AC.t('cap.anim.unitAria'), options: [{ value: 'word', label: AC.t('cap.anim.unitWord') }, { value: 'page', label: AC.t('cap.anim.unitPage') }], onChange: function (v) { D.spec.unit = v; changed(); paintStudio(); } });
    tim.add([ui.h('div', { class: 'cap-ctl-grid' }, [st.dur.el, st.stg.el]),
             ui.h('div', { class: 'cap-afx-row' }, [ui.h('label', { class: 'lbl', text: AC.t('cap.anim.order') }), st.order.el]), st.unit.el]);
    el.appendChild(tim.el);

    var trk = ui.section({ title: AC.t('cap.anim.tracksTitle') });
    st.tracks = ui.h('div', { class: 'cap-afx-tracks', role: 'list', 'aria-label': AC.t('cap.anim.tracksAria') });
    trk.add(st.tracks);
    trk.add(ui.h('p', { class: 'help', text: AC.t('cap.anim.tracksHelp') }));
    st.keyBox = ui.h('div', { class: 'cap-afx-key' });
    trk.add(st.keyBox);
    el.appendChild(trk.el);

    var save = ui.section({ title: AC.t('cap.anim.saveTitle') });
    st.save = ui.button({ label: AC.t('cap.anim.saveMine'), icon: 'plus', small: true, onClick: saveDraft });
    st.del = ui.button({ label: AC.t('cap.anim.deleteThis'), icon: 'trash', small: true, kind: 'ghost-danger', onClick: deleteDraft });
    save.add(ui.h('div', { class: 'cap-afx-btns' }, [st.save, st.del]));
    save.add(ui.h('p', { class: 'help', text: AC.t('cap.anim.saveHelp') }));
    el.appendChild(save.el);
    if (D && F.cat) paintStudio(true);            // re-render (language switch): keep the draft on screen
  }
  function changed() { requestDraftSprite(); }
  var draftT = null, draftSeq = 0;
  function requestDraftSprite() {
    clearTimeout(draftT);
    draftT = setTimeout(function () {
      if (!D) return;
      var my = ++draftSeq;
      F.st.prev.classList.add('is-busy');
      cap.worker('anim_sprites', { specs: { draft: D.spec }, frames: 20, size: [352, 112] }).promise.then(function (r) {
        if (my !== draftSeq) return;
        F.st.prev.classList.remove('is-busy');
        var info = (r.sprites || {}).draft;
        F.st.prev.innerHTML = '';
        F.st.prev.appendChild(info ? sprite(info, true) : ui.h('span', { class: 'cap-afx-spr is-big is-empty', text: AC.t('cap.anim.addKeyFirst') }));
      }, function (e) { F.st.prev.classList.remove('is-busy'); AC.log.warn('anim draft sprite: ' + U.errMsg(e)); });
    }, 350);
  }
  function paintStudio(full) {
    if (!F.st || !F.cat || !D) return;
    var st = F.st, s = D.spec;
    if (full) {
      st.base.setOptions([{ value: '', label: AC.t('cap.anim.pickPreset') }, { value: '_blank', label: AC.t('cap.anim.blank') }].concat(F.cat.presets.concat(F.cat.user || []).filter(function (p) { return p.group === D.role; })
        .map(function (p) { return { value: p.id, label: p.user ? AC.t('cap.anim.mineTitle', { name: p.label }) : p.label }; })));
      st.base.set('', true);
      st.order.setOptions((F.cat.orders || []).map(function (o) { return { value: o.id, label: o.label }; }));
      requestDraftSprite();
    }
    st.dur.set(Math.round((s.dur || 0.3) * 1000), true);
    st.stg.set(Math.round((s.stagger || 0) * 1000), true);
    st.stg.el.hidden = D.role === 'active' || s.unit === 'page';
    st.order.set(s.order || 'forward', true);
    st.unit.set(s.unit || 'word', true);
    st.unit.el.hidden = D.role === 'active' || D.role === 'loop';
    st.del.hidden = !D.id;
    paintTracks();
    paintKey();
  }
  function paintTracks() {
    var box = F.st.tracks, s = D.spec;
    box.innerHTML = '';
    (F.cat.tracks || []).forEach(function (ti) {
      var keys = s.tracks[ti.id] || [];
      var row = ui.h('div', { class: 'cap-afx-trk' + (D.track === ti.id ? ' is-on' : '') + (keys.length ? '' : ' is-off'), role: 'listitem', dataset: { track: ti.id } });
      var name = ui.h('button', { type: 'button', class: 'cap-afx-tn', text: ti.label, 'aria-pressed': D.track === ti.id ? 'true' : 'false', title: keys.length ? AC.t('cap.anim.trackPick', { name: ti.label }) : AC.t('cap.anim.trackAdd', { name: ti.label }) });
      name.addEventListener('click', function () {
        D.track = ti.id;
        if (!keys.length) { s.tracks[ti.id] = [{ t: 0, v: defVal(ti), e: 'out' }, { t: 1, v: defVal(ti), e: 'linear' }]; D.key = 0; changed(); }
        else D.key = Math.min(D.key, keys.length - 1);
        paintTracks(); paintKey();
      });
      var line = ui.h('div', { class: 'cap-afx-tl', title: AC.t('cap.anim.dblAdd') });
      line.addEventListener('dblclick', function (e) {
        if (e.target !== line) return;
        var r = line.getBoundingClientRect(), t = Math.round(U.clamp((e.clientX - r.left) / r.width, 0, 1) * 100) / 100;
        addKey(ti.id, t);
      });
      keys.forEach(function (k, i) { line.appendChild(dot(ti, k, i, line)); });
      row.appendChild(name); row.appendChild(line);
      row.appendChild(ui.h('span', { class: 'cap-afx-n', text: keys.length ? String(keys.length) : '+' }));
      box.appendChild(row);
    });
  }
  function defVal(ti) { return ti.id === 'color' ? '#FFE600' : ti['default']; }
  function dot(ti, k, i, line) {
    var sel = D.track === ti.id && D.key === i;
    var d = ui.h('button', { type: 'button', class: 'cap-afx-dot' + (sel ? ' is-sel' : ''), role: 'slider', 'aria-label': AC.t('cap.anim.dotAria', { track: ti.label, n: i + 1 }),
                             'aria-valuemin': '0', 'aria-valuemax': '100', 'aria-valuenow': String(Math.round(k.t * 100)), title: U.dec(k.t * 100, 0) + '%: ' + fmtVal(ti, k.v) });
    d.style.left = (k.t * 100) + '%';
    d.addEventListener('click', function () { D.track = ti.id; D.key = i; paintTracks(); paintKey(); focusDot(ti.id, i); });
    d.addEventListener('keydown', function (e) {
      if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') {
        e.preventDefault();
        var step = (e.shiftKey ? 0.01 : 0.05) * (e.key === 'ArrowLeft' ? -1 : 1);
        D.track = ti.id; D.key = i; moveKey(ti.id, i, k.t + step); return;
      }
      if (e.key === 'Delete' || e.key === 'Backspace') { e.preventDefault(); D.track = ti.id; delKey(ti.id, i); }
    });
    d.addEventListener('pointerdown', function (e) {
      if (e.button !== 0) return;
      D.track = ti.id; D.key = i;
      var r = line.getBoundingClientRect(), moved = false;
      try { d.setPointerCapture(e.pointerId); } catch (x) { /* no capture */ }
      function mv(ev) { moved = true; var t = U.clamp((ev.clientX - r.left) / r.width, 0, 1); k.t = Math.round(t * 100) / 100; d.style.left = (k.t * 100) + '%'; }
      function up() {
        d.removeEventListener('pointermove', mv); d.removeEventListener('pointerup', up); d.removeEventListener('pointercancel', up);
        if (moved) moveKey(ti.id, i, k.t);
      }
      d.addEventListener('pointermove', mv); d.addEventListener('pointerup', up); d.addEventListener('pointercancel', up);
    });
    return d;
  }
  function focusDot(track, i) {
    var row = U.$('.cap-afx-trk[data-track="' + track + '"]', F.st.tracks), dots = row ? U.$$('.cap-afx-dot', row) : [];
    if (dots[i]) dots[i].focus();
  }
  function sortKeys(track, keep) {
    var keys = D.spec.tracks[track];
    keys.sort(function (a, b) { return a.t - b.t; });
    D.key = keys.indexOf(keep);
  }
  function moveKey(track, i, t) {
    var k = D.spec.tracks[track][i];
    k.t = Math.round(U.clamp(t, 0, 1) * 100) / 100;
    sortKeys(track, k);
    paintTracks(); paintKey(); focusDot(track, D.key); changed();
  }
  function addKey(track, t) {
    var keys = D.spec.tracks[track] || (D.spec.tracks[track] = []), ti = trackInfo(track);
    var near = keys.reduce(function (a, k) { return !a || Math.abs(k.t - t) < Math.abs(a.t - t) ? k : a; }, null);
    var k = { t: t, v: near ? near.v : defVal(ti), e: near ? near.e : 'linear' };
    keys.push(k); D.track = track; sortKeys(track, k);
    paintTracks(); paintKey(); focusDot(track, D.key); changed();
  }
  function delKey(track, i) {
    var keys = D.spec.tracks[track];
    keys.splice(i, 1);
    if (!keys.length) delete D.spec.tracks[track];
    D.key = Math.max(0, Math.min(i, keys.length - 1));
    paintTracks(); paintKey(); if (keys.length) focusDot(track, D.key); changed();
  }
  function fmtVal(ti, v) {
    if (ti.id === 'color') return v || AC.t('cap.anim.origColorShort');
    if (ti.format === 'percent') return Math.round(v * 100) + '%';
    return U.dec(v, ti.decimals || 0) + (ti.unit === 'x' ? 'x' : ti.unit === '%' ? '%' : ti.unit ? ' ' + ti.unit : '');
  }
  function paintKey() {
    var box = F.st.keyBox, ti = trackInfo(D.track), keys = D.spec.tracks[D.track] || [], k = keys[D.key];
    box.innerHTML = '';
    if (!k) { box.appendChild(ui.h('p', { class: 'help', text: AC.t('cap.anim.pickTrack') })); return; }
    box.appendChild(ui.h('div', { class: 'cap-afx-kh', text: AC.t('cap.anim.keyHead', { track: ti.label, n: D.key + 1, total: keys.length, pct: Math.round(k.t * 100) }) }));
    var val;
    if (ti.id === 'color') {
      var col = ui.h('input', { type: 'color', class: 'cap-afx-col', value: (k.v || '#FFFFFF').slice(0, 7), 'aria-label': AC.t('cap.anim.keyColor') });
      var orig = ui.switch({ label: AC.t('cap.anim.origColor'), checked: !k.v, onChange: function (on) { k.v = on ? null : col.value.toUpperCase(); col.disabled = on; changed(); paintTracks(); } });
      col.disabled = !k.v;
      col.addEventListener('change', function () { k.v = col.value.toUpperCase(); changed(); paintTracks(); });
      val = ui.h('div', { class: 'cap-afx-row' }, [col, orig.el]);
    } else {
      var mul = ti.format === 'percent' ? 100 : 1;
      var inp = ui.h('input', { type: 'number', class: 'input cap-afx-num', step: String((ti.step || 1) * mul), min: String(ti.min * mul), max: String(ti.max * mul),
                                value: String(Math.round(k.v * mul * 1000) / 1000), 'aria-label': AC.t('cap.anim.valueAria', { name: ti.label }) });
      inp.addEventListener('change', function () {
        var v = Number(inp.value);
        if (!isFinite(v)) return;
        k.v = U.clamp(v, ti.min * mul, ti.max * mul) / mul; inp.value = String(Math.round(k.v * mul * 1000) / 1000);
        changed(); paintTracks();
      });
      val = ui.h('div', { class: 'cap-afx-row' }, [ui.h('label', { class: 'lbl', text: AC.t('cap.anim.value') }), inp, ui.h('span', { class: 'cap-afx-u', text: ti.format === 'percent' ? '%' : ti.unit === 'x' ? 'x' : ti.unit || '' })]);
    }
    box.appendChild(val);
    var ease = ui.select({ ariaLabel: AC.t('cap.anim.easeAria'), options: (F.cat.easings || []).map(function (e) { return { value: e.id, label: e.label }; }), value: k.e || 'linear',
      onChange: function (v) { k.e = v; changed(); } });
    var last = D.key === keys.length - 1;
    var row = ui.h('div', { class: 'cap-afx-row' }, [ui.h('label', { class: 'lbl', text: AC.t('cap.anim.easeLabel') }), ease.el]);
    if (last) { ease.el.disabled = true; row.title = AC.t('cap.anim.lastKey'); }
    box.appendChild(row);
    var add = ui.button({ label: AC.t('cap.anim.addKey'), icon: 'plus', small: true, kind: 'ghost', onClick: function () {
      var nx = keys[D.key + 1], t = nx ? (k.t + nx.t) / 2 : Math.min(1, k.t + 0.25);
      if (!nx && k.t >= 1) t = Math.max(0, k.t - 0.25);
      addKey(D.track, Math.round(t * 100) / 100);
    } });
    var del = ui.button({ label: AC.t('cap.anim.delKey'), icon: 'trash', small: true, kind: 'ghost-danger', onClick: function () { delKey(D.track, D.key); } });
    box.appendChild(ui.h('div', { class: 'cap-afx-btns' }, [add, del]));
  }
  function draftSpec() {
    var s = U.copy(D.spec);
    s.group = D.role; s.label = String(F.st.name.get() || AC.t('cap.anim.myAnim')).slice(0, 60);
    delete s.builtin; delete s.user; delete s.desc;
    if (!s.id || s.id === 'custom' || !/^u_/.test(s.id)) s.id = 'draft';
    return s;
  }
  function hasKeys() { return Object.keys(D.spec.tracks).some(function (k) { return (D.spec.tracks[k] || []).length; }); }
  function applyDraft(toast) {
    if (!D || !hasKeys()) { ui.toast(AC.t('cap.anim.addKeyFirstDot'), 'info'); return Promise.resolve(false); }
    var p = setFx(D.role, draftSpec(), AC.t('cap.anim.undoStudio'));
    if (toast) ui.toast(AC.t('cap.anim.usedFor', { role: roleLabel(D.role).toLowerCase() }), { icon: 'check', action: undoAct() });
    return p.then(function () { return true; });
  }
  function playDraft() {
    applyDraft(false).then(function (ok) {
      if (!ok) return;
      return cap.idle().then(function () {
        if (cap.preview && cap.preview.stopLoop) cap.preview.stopLoop();
        // the preview's play button (first button of its bar; no aria-label match: that text is translated)
        var b = cap.preview && cap.preview.playLoop ? null : U.$('.cap-bar .ibtn');
        if (cap.preview && cap.preview.playLoop) cap.preview.playLoop();
        else if (b) b.click(); else ui.toast(AC.t('cap.anim.noPlayBtn'), { kind: 'err' });
      });
    });
  }
  function saveDraft() {
    if (!D || !hasKeys()) { ui.toast(AC.t('cap.anim.addKeyFirstDot'), 'info'); return; }
    var s = draftSpec(), name = s.label;
    cap.worker('anim_save', { anim: s, name: name, id: D.id || undefined }).promise.then(function (r) {
      F.cat.user = r.user; D.id = r.id; D.spec = U.copy(r.anim);
      F.st.del.hidden = false;
      F.key = null;
      return loadSprites(true).then(function () { paint(); paintStudio(false); ui.toast(AC.t('cap.anim.saved', { name: r.anim.label }), 'check'); });
    }, function (e) { ui.toast(AC.t('cap.anim.saveFail', { err: U.errMsg(e) }), { kind: 'err' }); });
  }
  function deleteDraft() {
    if (!D || !D.id) return;
    var id = D.id, label = D.spec.label;
    if (!F.delArmed) { F.delArmed = true; ui.toast(AC.t('cap.anim.delConfirm', { name: label }), 'trash'); setTimeout(function () { F.delArmed = false; }, 3000); return; }
    F.delArmed = false;
    cap.worker('anim_save', { 'delete': id }).promise.then(function (r) {
      F.cat.user = r.user; D.id = null; F.key = null; paint(); paintStudio(false);
      ui.toast(AC.t('cap.anim.deleted', { name: label }), 'trash');
    }, function (e) { ui.toast(AC.t('cap.anim.delFail', { err: U.errMsg(e) }), { kind: 'err' }); });
  }

  /* ================================================================ Efek suara */
  function sfxTrack() { return (X.lib && X.lib.track) || 'Klipora SFX'; }       // Premiere name, same in every language
  function ruleLabel(r) { var k = 'cap.sfx.rule.' + (r.id || r); return AC.i18n.has(k) ? AC.t(k) : (r.label || r.id || String(r)); }
  function sfxState() {
    var st = S.ctx.state;
    if (!st.sfx || typeof st.sfx !== 'object') st.sfx = {};
    if (!st.sfx.rules) st.sfx.rules = {};
    return st.sfx;
  }
  function saveState() { if (S.ctx) S.ctx.save(); }
  function buildSfx(el) {
    var sec = ui.section({ title: AC.t('cap.sfx.rulesTitle') });
    X.rules = ui.h('div', { class: 'cap-sfx-rules' });
    sec.add(X.rules);
    sec.add(ui.h('p', { class: 'help', text: AC.t('cap.sfx.rulesHelp') }));
    el.appendChild(sec.el);
    var gen = ui.section({ title: AC.t('cap.sfx.amounts') });
    X.vol = ui.slider({ label: AC.t('cap.sfx.volume'), min: -30, max: 6, step: 1, value: -6, decimals: 0, unit: ' dB', onChange: function (v) { sfxState().volume = v; saveState(); } });
    X.rate = ui.slider({ label: AC.t('cap.sfx.rate'), min: 1, max: 40, step: 1, value: 12, decimals: 0, onChange: function (v) { sfxState().max_per_min = v; saveState(); } });
    X.gap = ui.slider({ label: AC.t('cap.sfx.gap'), min: 0, max: 3000, step: 50, value: 600, decimals: 0, format: secs, onChange: function (v) { sfxState().min_gap = v / 1000; saveState(); } });
    X.ai = ui.switch({ label: AC.t('cap.sfx.ai'), help: AC.t('cap.sfx.aiHelp'), onChange: function (v) { sfxState().ai = v; saveState(); } });
    gen.add([ui.h('div', { class: 'cap-ctl-grid' }, [X.vol.el, X.rate.el]), X.gap.el, X.ai.el]);
    el.appendChild(gen.el);
    var own = ui.section({ title: AC.t('cap.sfx.ownTitle') });
    X.path = ui.input({ placeholder: AC.t('cap.sfx.pathPh'), mono: true, ariaLabel: AC.t('cap.sfx.pathAria') });
    var pickB = ui.button({ label: AC.t('cap.sfx.pickFile'), icon: 'folder', small: true, kind: 'ghost', onClick: pickSound });
    var addB = ui.button({ label: AC.t('cap.sfx.addSound'), icon: 'plus', small: true, onClick: addSound });
    own.add([ui.h('div', { class: 'cap-afx-row' }, [X.path.el, pickB]), addB, ui.h('p', { class: 'help', text: AC.t('cap.sfx.ownHelp') })]);
    el.appendChild(own.el);
    var go = ui.section({ title: AC.t('cap.sfx.planTitle') });
    X.checkB = ui.button({ label: AC.t('cap.sfx.check'), icon: 'search', small: true, kind: 'ghost', onClick: sfxPlan });
    X.applyB = ui.button({ label: AC.t('cap.sfx.apply'), icon: 'wave', small: true, kind: 'primary', onClick: sfxApply });
    X.sum = ui.h('div', { class: 'cap-sfx-sum', 'aria-live': 'polite' });
    X.list = ui.h('ol', { class: 'cap-sfx-list', 'aria-label': AC.t('cap.sfx.listAria') });
    X.where = ui.h('p', { class: 'help', text: AC.t('cap.sfx.where', { track: sfxTrack() }) });
    go.add([ui.h('div', { class: 'cap-afx-btns' }, [X.checkB, X.applyB]), X.sum, X.list, X.where]);
    el.appendChild(go.el);
    if (X.lib) paintSfx();                       // re-render (language switch)
  }
  function sfxWarm() {
    if (X.libP && X.libLang === AC.i18n.lang()) return X.libP;
    X.libLang = AC.i18n.lang();
    X.libP = cap.worker('sfx_library', {}).promise.then(function (r) { X.lib = r; paintSfx(); return r; }, function (e) { X.libP = null; ui.toast(AC.t('cap.sfx.errLib', { err: U.errMsg(e) }), { kind: 'err' }); });
    return X.libP;
  }
  function soundRow(id) { var l = (X.lib && X.lib.sounds) || []; for (var i = 0; i < l.length; i++) if (l[i].id === id) return l[i]; return null; }
  function playSound(id, gain) {
    var s = soundRow(id);
    if (!s) return;
    try {
      if (X.audio) X.audio.pause();
      var a = new Audio(cap.fileUrl(s.path));
      a.volume = U.clamp(Math.pow(10, ((gain || 0) + (sfxState().volume !== undefined ? sfxState().volume : -6)) / 20), 0, 1);
      X.audio = a;
      var p = a.play();
      if (p && p.catch) p.catch(function (e) { AC.log.warn('sfx play: ' + U.errMsg(e)); });
    } catch (e) { AC.log.warn('sfx play: ' + U.errMsg(e)); }
  }
  function ruleVal(id) {
    var def = null;
    ((X.lib && X.lib.rules) || []).forEach(function (r) { if (r.id === id) def = r['default']; });
    return U.assign({}, def || {}, sfxState().rules[id] || {});
  }
  function paintSfx() {
    if (!X.lib || !X.rules) return;
    var st = sfxState(), d = X.lib.defaults || {};
    if (X.where) X.where.textContent = AC.t('cap.sfx.where', { track: sfxTrack() });
    X.rules.innerHTML = '';
    var opts = X.lib.sounds.map(function (s) { return { value: s.id, label: s.source === 'user' ? AC.t('cap.sfx.mine', { name: s.label }) : s.label }; });
    X.lib.rules.forEach(function (r) {
      var v = ruleVal(r.id);
      var row = ui.h('div', { class: 'cap-sfx-rule' + (v.on ? ' is-on' : ''), dataset: { rule: r.id } });
      var sw = ui.switch({ label: ruleLabel(r), checked: !!v.on, onChange: function (on) { set(r.id, 'on', on); row.classList.toggle('is-on', on); } });
      var snd = ui.select({ ariaLabel: AC.t('cap.sfx.soundFor', { rule: ruleLabel(r) }), options: opts, value: v.sound, onChange: function (s) { set(r.id, 'sound', s); playSound(s, ruleVal(r.id).gain); } });
      var gain = ui.slider({ label: AC.t('cap.sfx.gain'), min: -24, max: 6, step: 1, value: v.gain || 0, decimals: 0, unit: ' dB', onChange: function (g) { set(r.id, 'gain', g); } });
      var play = ui.h('button', { type: 'button', class: 'btn btn-sm btn-ghost cap-sfx-play', 'aria-label': AC.t('cap.sfx.listenTo', { name: ruleLabel(r) }), title: AC.t('cap.sfx.listen'), html: ui.icon('play') });
      play.addEventListener('click', function () { var x = ruleVal(r.id); playSound(x.sound, x.gain); });
      row.appendChild(sw.el);
      row.appendChild(ui.h('div', { class: 'cap-afx-row' }, [snd.el, play]));
      row.appendChild(gain.el);
      X.rules.appendChild(row);
    });
    X.vol.set(st.volume !== undefined ? st.volume : d.volume, true);
    X.rate.set(st.max_per_min !== undefined ? st.max_per_min : d.max_per_min, true);
    X.gap.set(Math.round((st.min_gap !== undefined ? st.min_gap : d.min_gap) * 1000), true);
    X.ai.set(!!st.ai, true);
    function set(rid, k, val) { var rr = sfxState().rules; rr[rid] = U.assign(rr[rid] || {}, {}); rr[rid][k] = val; if (k === 'on' && rr[rid].sound === undefined) rr[rid].sound = ruleVal(rid).sound; saveState(); }
  }
  function sfxParams() {
    var st = sfxState(), rules = {};
    ((X.lib && X.lib.rules) || []).forEach(function (r) { var v = ruleVal(r.id); rules[r.id] = { on: !!v.on, sound: v.sound, gain: v.gain || 0 }; });
    var p = { rules: rules, ai: !!st.ai };
    ['volume', 'max_per_min', 'min_gap'].forEach(function (k) { if (st[k] !== undefined) p[k] = st[k]; });   // i18n-ignore (param names)
    return p;
  }
  function ruleShort(id) { var k = 'cap.sfx.ruleShort.' + id; return AC.i18n.has(k) ? AC.t(k) : id; }
  function sfxPlan() {
    X.checkB.disabled = true;
    return cap.idle().then(function () { return cap.worker('sfx_plan', sfxParams()).promise; }).then(function (r) {
      X.checkB.disabled = false;
      X.plan = r;
      paintPlan(r);
      return r;
    }, function (e) { X.checkB.disabled = false; X.sum.textContent = ''; ui.toast(AC.t('cap.sfx.errPlan', { err: U.errMsg(e) }), { kind: 'err' }); });
  }
  function paintPlan(r) {
    var st = r.stats || {}, n = (r.hits || []).length;
    var dropped = (st.dropped_gap || 0) + (st.dropped_rate || 0);
    X.sum.textContent = n ? AC.t('cap.sfx.sumHits', { n: n }) + (dropped ? AC.t('cap.sfx.sumDropped', { n: dropped }) : '') + (r.ai ? AC.t('cap.sfx.sumAi', { src: r.ai }) : '')
      : AC.t('cap.sfx.noHits');
    X.list.innerHTML = '';
    (r.hits || []).slice(0, 60).forEach(function (h) {
      var s = soundRow(h.sound);
      var li = ui.h('li', { class: 'cap-sfx-hit' });
      var go = ui.h('button', { type: 'button', class: 'cap-sfx-t', text: U.tcode(h.at !== undefined ? h.at : h.t), title: AC.t('cap.sfx.showPreview') });
      go.addEventListener('click', function () { if (cap.preview) cap.preview.show(h.at !== undefined ? h.at : h.t, { seek: true }); });
      var play = ui.h('button', { type: 'button', class: 'btn btn-sm btn-ghost cap-sfx-play', 'aria-label': AC.t('cap.sfx.listenTo', { name: s ? s.label : h.sound }), html: ui.icon('play') });
      play.addEventListener('click', function () { playSound(h.sound, h.gain - (sfxState().volume !== undefined ? sfxState().volume : -6)); });
      li.appendChild(go);
      li.appendChild(ui.h('span', { class: 'cap-sfx-w', 'data-i18n-skip': '', text: h.text || '' }));
      li.appendChild(ui.h('span', { class: 'cap-sfx-s', text: (s ? s.label : h.sound) + ' · ' + ruleShort(h.rule) }));
      li.appendChild(play);
      X.list.appendChild(li);
    });
    if (n > 60) X.list.appendChild(ui.h('li', { class: 'cap-sfx-more', text: AC.t('cap.sfx.more', { n: n - 60 }) }));
  }
  function sfxApply() {
    if (!S.seq) { ui.toast(AC.t('cap.sfx.noSeq'), { kind: 'err' }); return; }
    var seqId = S.seq.id;
    X.applyB.disabled = true;
    X.sum.textContent = AC.t('cap.sfx.preparing');
    var task = cap.idle().then(function () { return cap.worker('sfx_render', sfxParams()).promise; }).then(function (r) {
      X.plan = r; paintPlan(r);
      var p = r.plan;
      return AC.host.exec('bac_csfx_apply', [p.path, p.start, seqId], { json: true, timeout: 120000 }).then(function (h) { return { r: r, h: h }; });
    });
    cap.hold(task);
    task.then(function (x) {
      X.applyB.disabled = false;
      var n = (x.r.hits || []).length, trk = x.r.plan.track || sfxTrack();
      // host answers ok:false when it cannot find the placed clip afterwards: never claim success then
      if (!x.h || x.h.ok === false) {
        X.sum.textContent = AC.t('cap.sfx.notPlaced', { track: trk });
        ui.toast(AC.t('cap.sfx.notPlaced', { track: trk }), { kind: 'err' });
        return;
      }
      X.sum.textContent = AC.t('cap.sfx.placed', { n: n, track: trk }) + (x.h.removed ? AC.t('cap.sfx.replacedOld') : '') + AC.t('cap.sfx.placedTail', { track: trk }) +
        (x.h.named === false ? AC.t('cap.sfx.notNamed') : '');
      ui.toast(AC.t('cap.sfx.placedToast', { v: x.r.plan.version }), 'check');
    }, function (e) {
      X.applyB.disabled = false;
      X.sum.textContent = '';
      ui.toast(AC.t('cap.sfx.errApply', { err: U.errMsg(e) }), { kind: 'err' });
    });
    return task;
  }
  function pickSound() {
    var fs = window.cep && window.cep.fs;
    if (!fs || !fs.showOpenDialogEx) { ui.toast(AC.t('cap.sfx.pasteHint'), 'info'); X.path.el.focus(); return; }
    try {
      var r = fs.showOpenDialogEx(false, false, AC.t('cap.sfx.dialogTitle'), '', ['wav', 'mp3', 'm4a', 'ogg', 'flac']);
      if (r && !r.err && r.data && r.data[0]) X.path.set(String(r.data[0]));
    } catch (e) { ui.toast(AC.t('cap.sfx.errDialog', { err: U.errMsg(e) }), { kind: 'err' }); }
  }
  function addSound() {
    var p = String(X.path.get() || '').replace(/^\s*"+|"+\s*$/g, '');
    if (!p) { ui.toast(AC.t('cap.sfx.needPath'), 'info'); X.path.el.focus(); return; }
    cap.worker('sfx_add', { path: p }).promise.then(function (r) {
      X.lib.sounds = r.sounds; X.path.set(''); paintSfx();
      ui.toast(AC.t('cap.sfx.added', { name: r.sound.label }), 'check');
    }, function (e) { ui.toast(AC.t('cap.sfx.errAdd', { err: U.errMsg(e) }), { kind: 'err' }); });
  }
  cap.animUI.sfxPlan = sfxPlan;
  cap.animUI.sfxApply = sfxApply;

  function paint() { paintLibrary(); }
})();
