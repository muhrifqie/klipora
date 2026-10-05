/* podcast.js: Podcast Multicam. Each speaker = one mic track + one camera track (auto-guessed from track names and
   order, editable), optional wide camera, rhythm presets (Tenang / Normal / Dinamis). "Analisis" runs the engine
   (engine/ac/tools/podcast.py: per-mic loudness on the timeline -> shots + sync check) -> Tinjau: speaker ribbon +
   one row per shot (unchecked = keep the previous camera, row buttons pick another camera) -> engine apply -> plan
   "multicam" -> host/40_podcast.jsx on a clone: razor the camera tracks at the switch points, disable the inactive
   clips. "Ganti kamera di playhead" (buttons, Alt+1..5) fixes single shots afterwards, only on Klipora results (old "(AutoCut)" names too).
   AC.podcast exposes guess/validate/effective for tests. Docs: docs/tools/podcast.md, docs/PANEL_API.md. */
(function () {
  'use strict';
  var AC = window.AC, U = AC.util, ui = AC.ui;
  var TAG = '[Klipora-POD]', RAZOR_CHUNK = 20, MAX_SPK = 6, MAX_MAPS = 20;
  function T(k, v) { return AC.t('podcast.' + k, v); }
  // Labels are translated at render time (live language switch).
  function presetsList() {
    return [
      { value: 'tenang', label: T('preset.tenang'), hint: T('preset.tenangHint'), values: { confirm: 1, minShot: 4, maxShot: 30 } },
      { value: 'normal', label: T('preset.normal'), hint: T('preset.normalHint'), values: { confirm: 0.8, minShot: 2, maxShot: 15 } },
      { value: 'dinamis', label: T('preset.dinamis'), hint: T('preset.dinamisHint'), values: { confirm: 0.6, minShot: 1.8, maxShot: 8 } }
    ];
  }
  function selinganOpts() {
    var sec = function (n) { return n + ' ' + AC.t('unit.sec'); };
    return [{ value: 0, label: AC.t('common.off') }, { value: 30, label: sec(30) }, { value: 15, label: sec(15) }, { value: 8, label: sec(8) }];
  }
  var WHY = { awal: 1, bersahutan: 1, diam: 1, selingan: 1, reaksi: 1 };   // engine "why" ids with a tag (podcast.why.*)
  var MARKER_COLOR = [3, 6, 0, 2, 4, 1];          // Premiere marker colour per speaker (orange, blue, green, ...)

  /* ================================================================ mapping logic (pure, exposed for tests) */
  var P = AC.podcast = {};
  var DEFAULT_NAME = /^(video|audio|v|a)\s*\d+$/i;
  var MIC_RE = /\b(mic|mik|mike|spk|speaker|pembicara|host|guest|tamu|narasumber|lav|voice|vo|suara)\b/i;
  var WIDE_RE = /\b(wide|lebar|master|semua|all|group|grup|ws|2shot|twoshot|dua)\b/i;
  var STOP = /^(mic|mik|mike|cam|kamera|camera|audio|video|track|spk|speaker|close|cu|ws|wide|angle|the|dan|and|\d+)$/;

  function words(name) {
    return String(name || '').toLowerCase().replace(/[^a-z0-9]+/g, ' ').split(' ').filter(function (w) { return w.length > 1 && !STOP.test(w); });
  }
  function isDefault(name) { return DEFAULT_NAME.test(String(name || '').trim()); }
  P.trackLabel = function (t, kind) {
    var tag = (kind === 'video' ? 'V' : 'A') + (t.index + 1), nm = String(t.name || '').trim();
    return tag + (nm && !isDefault(nm) ? ' · ' + nm : '') + (t.count ? '' : ' (' + T('trackEmpty') + ')');
  };
  function defName(k) { return T('speakerN', { n: k + 1 }); }
  function speakerName(t, k) {
    if (!t || isDefault(t.name)) return defName(k);
    var w = String(t.name).replace(/\b(mic|mik|mike|audio|track|lav|vo)\b/ig, ' ').replace(/[-_]+/g, ' ').replace(/\s+/g, ' ').trim();
    return (w || t.name).slice(0, 40);
  }
  function filled(list) { return (list || []).filter(function (t) { return t.count > 0; }); }

  // Track layout -> {speakers: [{name, mic, cam}], wide}. Named tracks win ("Mic Andi" <-> "Cam Andi", "Wide");
  // otherwise order: k-th mic track -> k-th camera track; one camera more than speakers = wide (the lowest track,
  // usually the master shot on V1).
  P.guess = function (lite) {
    var aud = filled(lite && lite.audio), vid = filled(lite && lite.video);
    var mics = aud.filter(function (t) { return MIC_RE.test(t.name); });
    if (mics.length < 2) mics = aud;
    mics = mics.slice(0, MAX_SPK);
    var wide = null;
    vid.forEach(function (t) { if (wide === null && WIDE_RE.test(t.name)) wide = t.index; });
    var cams = vid.filter(function (t) { return t.index !== wide; });
    var used = {};
    var spk = mics.map(function (m, k) {
      var mw = words(m.name), cam = null;
      cams.forEach(function (v) {
        if (cam === null && !used[v.index] && mw.length && words(v.name).some(function (w) { return mw.indexOf(w) >= 0; })) { cam = v.index; used[v.index] = 1; }
      });
      return { name: speakerName(m, k), mic: m.index, cam: cam };
    });
    var free = cams.filter(function (v) { return !used[v.index]; });
    if (wide === null && free.length > spk.filter(function (s) { return s.cam === null; }).length && free.length >= 1 && vid.length > spk.length) {
      wide = free[0].index; free = free.slice(1);
    }
    spk.forEach(function (s) { if (s.cam === null && free.length) s.cam = free.shift().index; });
    return { speakers: spk, wide: wide };
  };

  // -> {ok, msg} for the current mapping on the lite sequence info.
  P.validate = function (map, lite) {
    if (!lite) return { ok: false, msg: T('v.noSeq') };
    var aud = {}, vid = {};
    (lite.audio || []).forEach(function (t) { aud[t.index] = t; });
    (lite.video || []).forEach(function (t) { vid[t.index] = t; });
    if (filled(lite.audio).length < 2) return { ok: false, msg: T('v.needMics') };
    var spk = (map && map.speakers) || [];
    if (spk.length < 2) return { ok: false, msg: T('v.needSpeakers') };
    var mics = {}, cams = {}, i;
    for (i = 0; i < spk.length; i++) {
      var s = spk[i], nm = s.name || defName(i);
      if (s.mic === null || s.mic === undefined || !aud[s.mic]) return { ok: false, msg: T('v.pickMic', { name: nm }) };
      if (!aud[s.mic].count) return { ok: false, msg: T('v.micEmpty', { track: 'A' + (s.mic + 1), name: nm }) };
      if (mics[s.mic] !== undefined) return { ok: false, msg: T('v.sameMic', { a: mics[s.mic], b: nm, track: 'A' + (s.mic + 1) }) };
      mics[s.mic] = nm;
      if (s.cam === null || s.cam === undefined || !vid[s.cam]) return { ok: false, msg: T('v.pickCam', { name: nm }) };
      if (!vid[s.cam].count) return { ok: false, msg: T('v.camEmpty', { track: 'V' + (s.cam + 1), name: nm }) };
      cams[s.cam] = 1;
    }
    var wide = map.wide;
    if (wide !== null && wide !== undefined) {
      if (!vid[wide] || !vid[wide].count) return { ok: false, msg: T('v.wideEmpty', { track: 'V' + (wide + 1) }) };
      if (cams[wide]) return { ok: false, msg: T('v.wideUsed', { track: 'V' + (wide + 1) }) };
      cams[wide] = 1;
    }
    if (Object.keys(cams).length < 2) return { ok: false, msg: T('v.sameCam') };
    return { ok: true, msg: '' };
  };

  // Review rows -> effective shots [{t0, t1, cam, i}] (unchecked row = previous camera; the first row always counts).
  P.effective = function (items) {
    var out = [];
    (items || []).forEach(function (it, i) {
      var cam = (it.on || !out.length) ? it.cam : out[out.length - 1].cam;
      var last = out[out.length - 1];
      if (last && last.cam === cam) last.t1 = it.t1; else out.push({ t0: it.t0, t1: it.t1, cam: cam, i: i });
    });
    return out;
  };
  P.switches = function (items) { return Math.max(0, P.effective(items).length - 1); };

  // Camera list of a mapping: [{track, name, kind, k}] (speakers sharing a track are joined, wide last).
  P.cams = function (map) {
    var out = [];
    ((map && map.speakers) || []).forEach(function (s, k) {
      if (s.cam === null || s.cam === undefined) return;
      var c = out.filter(function (x) { return x.track === s.cam; })[0];
      if (c) c.name += ' + ' + (s.name || defName(k));
      else out.push({ track: s.cam, name: s.name || defName(k), kind: 'spk', k: k });
    });
    if (map && map.wide !== null && map.wide !== undefined) out.push({ track: map.wide, name: 'Wide', kind: 'wide', k: -1 });
    return out;
  };

  /* ================================================================ colours (tool tokens in css/tools/podcast.css) */
  function colorOf(k) { return k < 0 ? (ui.css('--pod-wide') || ui.css('--text-3')) : (ui.css('--pod-c' + (k % 6)) || ui.css('--accent')); }
  // DOM elements use the CSS variable itself (follows theme changes); only the canvas needs resolved colours.
  function colorVar(k) { return k < 0 ? 'var(--pod-wide)' : 'var(--pod-c' + (k % 6) + ')'; }
  function camVar(cams, track) {
    for (var i = 0; i < cams.length; i++) if (cams[i].track === track) return colorVar(cams[i].k);
    return 'var(--text-3)';
  }
  function camColor(cams, track) {
    for (var i = 0; i < cams.length; i++) if (cams[i].track === track) return colorOf(cams[i].k);
    return ui.css('--text-3');
  }
  function camName(cams, track) {
    for (var i = 0; i < cams.length; i++) if (cams[i].track === track) return cams[i].name;
    return 'V' + (track + 1);
  }

  /* ================================================================ speaker ribbon (lanes + camera bar) */
  // opts: {label, onClick(t)} -> {el, set({total, lanes: [[spans]...], cross: spans, shots: [{t0,t1,cam}], cams, active, playhead})}
  function ribbon(o) {
    o = o || {};
    var c = ui.h('canvas', { class: 'podcast-rib', role: 'img', 'aria-label': o.label || T('ribLabel') });
    var st = { total: 0, lanes: [], cross: [], shots: [], cams: [], active: null, playhead: null };
    function fit() {
      var w = c.clientWidth, h = c.clientHeight;
      if (!w || !h) return null;
      var dpr = window.devicePixelRatio || 1;
      if (c.width !== Math.round(w * dpr) || c.height !== Math.round(h * dpr)) { c.width = Math.round(w * dpr); c.height = Math.round(h * dpr); }
      var g = c.getContext('2d'); g.setTransform(dpr, 0, 0, dpr, 0, 0); g.clearRect(0, 0, w, h);
      return { g: g, w: w, h: h };
    }
    function draw() {
      var f = fit(); if (!f) return;
      var g = f.g, w = f.w, h = f.h, T = st.total || 1, n = st.lanes.length + (st.cross.length ? 1 : 0);
      var laneH = 5, gap = 2, top = n ? n * (laneH + gap) + 3 : 0, barH = Math.max(8, h - top);
      var x = function (t) { return t / T * w; };
      g.fillStyle = ui.css('--surface-0'); g.fillRect(0, 0, w, h);
      st.lanes.forEach(function (spans, k) {
        var y = k * (laneH + gap);
        g.fillStyle = ui.hexA(ui.css('--border'), 1); g.fillRect(0, y + 2, w, 1);
        g.fillStyle = colorOf(k);
        spans.forEach(function (s) { g.fillRect(x(s[0]), y, Math.max(1, x(s[1]) - x(s[0])), laneH); });
      });
      if (st.cross.length) {
        var yx = st.lanes.length * (laneH + gap);
        g.fillStyle = ui.css('--text-3');
        st.cross.forEach(function (s) { g.fillRect(x(s[0]), yx + 1, Math.max(1, x(s[1]) - x(s[0])), laneH - 2); });
      }
      var bg = ui.css('--bg');
      st.shots.forEach(function (s) {
        var x0 = x(s.t0), x1 = x(s.t1);
        g.fillStyle = camColor(st.cams, s.cam); g.fillRect(x0, top, Math.max(1, x1 - x0), barH);
        g.fillStyle = bg; g.fillRect(Math.round(x0), top, 1, barH);
      });
      if (st.active) {
        g.strokeStyle = ui.css('--text-1'); g.lineWidth = 1.5;
        g.strokeRect(x(st.active.t0) + 0.75, top + 0.75, Math.max(2, x(st.active.t1) - x(st.active.t0) - 1.5), barH - 1.5);
      }
      if (st.playhead !== null && st.playhead !== undefined) {
        g.fillStyle = ui.css('--text-1'); g.fillRect(Math.max(0, Math.min(w - 2, x(st.playhead) - 1)), 0, 2, h);
      }
    }
    var raf = 0, go = function () { cancelAnimationFrame(raf); raf = requestAnimationFrame(function () { if (c.isConnected) { c._seen = true; draw(); } }); };
    var onTheme = function () { if (!c.isConnected && c._seen) { AC.bus.off('theme', onTheme); return; } go(); };
    if (window.ResizeObserver) new ResizeObserver(go).observe(c);
    AC.bus.on('theme', onTheme);
    if (o.onClick) c.addEventListener('click', function (e) { var r = c.getBoundingClientRect(); o.onClick((e.clientX - r.left) / r.width * (st.total || 0)); });
    return {
      el: c,
      set: function (p) {
        U.assign(st, p);
        var lanes = st.lanes.length + (st.cross.length ? 1 : 0);
        c.style.height = (lanes * 7 + 3 + 18) + 'px';
        c.setAttribute('aria-label', T('ribAria', { label: o.label || T('ribCam'), n: Math.max(0, st.shots.length - 1), dur: U.mmss(st.total) }));
        go();
      },
      redraw: go
    };
  }

  // Legend with colour dots and screen share per camera.
  function legendHtml(shots, cams, total) {
    var share = {};
    shots.forEach(function (s) { share[s.cam] = (share[s.cam] || 0) + (s.t1 - s.t0); });
    return cams.map(function (c) {
      return '<span><i class="podcast-key" style="background:' + colorVar(c.k) + '"></i>' + U.esc(c.name) + ' ' + U.pct((share[c.track] || 0) / (total || 1)) + '</span>';
    }).join('');
  }

  /* ================================================================ tool */
  AC.tools.register({
    id: 'podcast', group: 'kamera', order: 1,
    icon: 'mic',
    badges: [],
    text: function (t) {
      return { title: t('podcast.title'), tab: t('podcast.tab'), desc: t('podcast.desc'), lead: t('podcast.lead'), cta: t('podcast.cta'),
               review: { unit: t('podcast.reviewUnit') },
               next: [{ tool: 'silence', reason: t('podcast.nextSilence') }, { tool: 'chapters', reason: t('podcast.nextChapters') }] };
    },
    source: { scopes: ['all', 'inout'] },
    defaults: { preset: 'normal', confirm: 0.8, minShot: 2, maxShot: 15, react: true, crossWide: true, silenceWide: true,
                domDb: 6, preroll: 0.25, sync: true, split: false, prio: -1, maps: {} },

    render: function (el, ctx) {
      var st = ctx.state, def = ctx.def, SEC = ' ' + AC.t('unit.sec');
      if (!st.maps || typeof st.maps !== 'object') st.maps = {};
      var lite = AC.seq.peek(), map = null, valid = { ok: false, msg: '' };

      el.appendChild(ui.h('div', { class: 'tool-hero', html: '<span class="tool-ic">' + ui.icon('mic') + '</span><div><p>' + U.esc(def.lead + ' ' + T('leadMore')) + '</p></div>' }));

      /* ---------- Pembicara */
      var secSpk = ui.section({ title: T('secSpeakers'), action: { label: T('reguess'), onClick: function () { regues(true); } } });
      var listEl = ui.h('div', { class: 'podcast-list' });
      var addBtn = ui.button({ label: T('addSpeaker'), icon: 'plus', small: true, kind: 'ghost', onClick: addSpeaker });
      var wideSel = ui.select({ ariaLabel: T('wideCam'), options: [], onChange: function (v) { map.wide = v === -1 ? null : v; touched(); } });
      var wideRow = ui.inlineField({ label: T('wideCam'), help: T('wideHelp'), control: wideSel.el });
      wideRow.classList.add('podcast-wide');
      var issue = ui.h('div', { class: 'podcast-issue' });
      secSpk.add([listEl, ui.h('div', { class: 'btn-row' }, [addBtn]), wideRow, issue]);
      el.appendChild(secSpk.el);
      var emptyBox = ui.h('div', { class: 'podcast-empty', hidden: true });
      emptyBox.appendChild(ui.empty({ icon: 'mic', title: T('emptyTitle'), text: T('emptyText'),
        steps: [T('emptyStep1'), T('emptyStep2'), T('emptyStep3')] }));
      el.appendChild(emptyBox);

      /* ---------- Gaya */
      var secGaya = ui.section({ title: T('secStyle') });
      var presets = ui.presets({ options: presetsList(), value: st.preset, ariaLabel: T('secStyle'), customHint: T('customHint'), onChange: function (v, p) {
        st.preset = v; st.confirm = p.values.confirm; st.minShot = p.values.minShot; st.maxShot = p.values.maxShot;
        confirm.set(st.confirm, true); minShot.set(st.minShot, true); selingan.set(st.maxShot, true); ctx.save();
      } });
      function manual() { st.preset = presets.sync({ confirm: st.confirm, minShot: st.minShot, maxShot: st.maxShot }); ctx.save(); }
      var minShot = ui.slider({ label: T('minShot'), min: 1, max: 6, step: 0.1, value: st.minShot, unit: SEC, help: T('minShotHelp'),
                                onInput: function (v) { st.minShot = v; manual(); } });
      var confirm = ui.slider({ label: T('confirm'), min: 0.2, max: 2, step: 0.1, value: st.confirm, unit: SEC, help: T('confirmHelp'),
                                onInput: function (v) { st.confirm = v; manual(); } });
      var selingan = ui.segmented({ options: selinganOpts(), value: st.maxShot, ariaLabel: T('cutawayAria'), onChange: function (v) { st.maxShot = v; manual(); } });
      var selField = ui.field({ label: T('cutaway'), control: selingan.el, help: T('cutawayHelp') });
      var react = ui.switch({ label: T('react'), help: T('reactHelp'), checked: st.react, onChange: function (v) { st.react = v; ctx.save(); } });
      var cross = ui.switch({ label: T('cross'), help: T('crossHelp'), checked: st.crossWide, onChange: function (v) { st.crossWide = v; ctx.save(); } });
      secGaya.add([presets, minShot, confirm, selField, react, cross]);
      el.appendChild(secGaya.el);

      /* ---------- Pengaturan lanjutan */
      var adv = ui.advanced({ title: AC.t('common.advanced'), key: 'podcast' });
      var silence = ui.switch({ label: T('silence'), help: T('silenceHelp'), checked: st.silenceWide, onChange: function (v) { st.silenceWide = v; ctx.save(); } });
      var dom = ui.slider({ label: T('dom'), min: 3, max: 12, step: 1, value: st.domDb, unit: ' dB', decimals: 0, help: T('domHelp'),
                            onInput: function (v) { st.domDb = v; ctx.save(); } });
      var pre = ui.slider({ label: T('pre'), min: 0, max: 0.5, step: 0.05, value: st.preroll, unit: SEC, decimals: 2, help: T('preHelp'),
                            onInput: function (v) { st.preroll = v; ctx.save(); } });
      var prioSel = ui.select({ ariaLabel: T('prioAria'), options: [], onChange: function (v) { st.prio = v; ctx.save(); } });
      var sync = ui.switch({ label: T('sync'), help: T('syncHelp'), checked: st.sync, onChange: function (v) { st.sync = v; ctx.save(); } });
      adv.add([silence, dom, pre, ui.field({ label: T('prio'), control: prioSel.el, help: T('prioHelp') }), sync]);
      el.appendChild(adv.el);

      /* ---------- Ganti kamera di playhead */
      var secSw = ui.section({ title: T('secSwitch'), help: T('secSwitchHelp') });
      var sw = switcher(ctx, function () { return P.cams(map); });
      secSw.add(sw.el);
      el.appendChild(secSw.el);

      /* ---------- mapping UI */
      function seqKey() { return lite ? lite.id : ''; }
      function remember() {
        var keys = Object.keys(st.maps);
        if (keys.length > MAX_MAPS) keys.slice(0, keys.length - MAX_MAPS).forEach(function (k) { delete st.maps[k]; });
        ctx.save();
      }
      function touched() { if (map) { map.manual = true; st.maps[seqKey()] = map; remember(); } paint(); }
      function regues(force) {
        lite = AC.seq.peek();
        if (!lite) { map = null; paint(); return; }
        var saved = st.maps[lite.id];
        if (!force && saved && saved.manual) { map = saved; paint(); return; }
        var g = P.guess(lite);
        map = { speakers: g.speakers, wide: g.wide, manual: false, sig: sig(lite) };
        st.maps[lite.id] = map; remember();
        paint();
        if (force) ui.toast(T('reguessToast'), 'refresh');
      }
      function sig(s) { return (s.audio || []).concat(s.video || []).map(function (t) { return t.index + ':' + t.name + ':' + (t.count ? 1 : 0); }).join('|'); }
      function addSpeaker() {
        if (!map) return;
        var usedM = {}, usedC = {};
        map.speakers.forEach(function (s) { usedM[s.mic] = 1; usedC[s.cam] = 1; });
        var m = filled(lite.audio).filter(function (t) { return !usedM[t.index]; })[0];
        var c = filled(lite.video).filter(function (t) { return !usedC[t.index] && t.index !== map.wide; })[0];
        map.speakers.push({ name: m ? speakerName(m, map.speakers.length) : defName(map.speakers.length), mic: m ? m.index : null, cam: c ? c.index : null });
        touched();
      }
      function options(kind, withNone) {
        var list = (lite && lite[kind]) || [], out = withNone ? [{ value: -1, label: withNone }] : [];
        if (!withNone) out.push({ value: -1, label: T('pickTrack') });
        list.forEach(function (t) { out.push({ value: t.index, label: P.trackLabel(t, kind) }); });
        return out;
      }
      function speakerCard(s, k) {
        var name = ui.input({ value: s.name, ariaLabel: T('nameAria', { n: k + 1 }), placeholder: defName(k),
          onInput: function (v) { s.name = v.slice(0, 40); map.manual = true; st.maps[seqKey()] = map; remember(); sw.repaint(); paintPrio(); } });
        name.el.maxLength = 40;
        var who = s.name || String(k + 1);
        var mic = ui.select({ ariaLabel: T('micAria', { name: who }), options: options('audio'), value: s.mic === null ? -1 : s.mic,
          onChange: function (v) { s.mic = v === -1 ? null : v; touched(); } });
        var cam = ui.select({ ariaLabel: T('camAria', { name: who }), options: options('video'), value: s.cam === null ? -1 : s.cam,
          onChange: function (v) { s.cam = v === -1 ? null : v; touched(); } });
        var del = ui.h('button', { class: 'ibtn', type: 'button', 'aria-label': T('delAria', { name: s.name || T('speaker') }), title: T('delTitle'), html: ui.icon('x'),
          disabled: map.speakers.length <= 2, on: { click: function () { map.speakers.splice(k, 1); if (st.prio >= map.speakers.length) st.prio = -1; touched(); } } });
        var head = ui.h('div', { class: 'podcast-spk-h' }, [ui.h('i', { class: 'podcast-key', style: { background: colorVar(k) }, 'aria-hidden': 'true' }), name.el, del]);
        var io = ui.h('div', { class: 'podcast-io' }, [ui.field({ label: T('mic'), control: mic.el }), ui.field({ label: T('camera'), control: cam.el })]);
        return ui.h('div', { class: 'card podcast-spk', dataset: { k: String(k) } }, [head, io]);
      }
      function paintPrio() {
        var opts = [{ value: -1, label: AC.t('common.none') }];
        (map ? map.speakers : []).forEach(function (s, k) { opts.push({ value: k, label: s.name || defName(k) }); });
        prioSel.setOptions(opts); prioSel.set(st.prio, true);
      }
      function paint() {
        lite = AC.seq.peek();
        var has = !!lite && ((lite.audio || []).length + (lite.video || []).length) > 0;
        emptyBox.hidden = has; secSpk.el.hidden = !has;
        listEl.innerHTML = '';
        if (has && map) {
          map.speakers.forEach(function (s, k) { listEl.appendChild(speakerCard(s, k)); });
          addBtn.disabled = map.speakers.length >= Math.min(MAX_SPK, filled(lite.audio).length);
          wideSel.setOptions(options('video', AC.t('common.none')));
          wideSel.set(map.wide === null || map.wide === undefined ? -1 : map.wide, true);
          var nm = filled(lite.audio).length, nv = filled(lite.video).length;
          secSpk.setAside(T('aside', { mics: nm, cams: nv }));
        }
        valid = P.validate(map, lite);
        issue.innerHTML = '';
        if (has && !valid.ok) issue.appendChild(ui.alert({ kind: 'warn', title: T('notReady'), text: valid.msg }));
        cross.input.disabled = !map || map.wide === null || map.wide === undefined;
        silence.input.disabled = cross.input.disabled;
        paintPrio();
        sw.repaint();
        paintDock();
      }
      function paintDock() {
        var n = map ? map.speakers.length : 0;
        ctx.dock({ primary: { label: valid.ok ? T('analyzeN', { n: n }) : def.cta, icon: 'play', disabled: !valid.ok, title: valid.ok ? '' : valid.msg, onClick: run } });
      }
      ctx.podcastRefresh = function () {
        var s = AC.seq.peek();
        if (!s) { lite = null; map = null; paint(); return; }
        var saved = st.maps[s.id];
        if (!saved || (!saved.manual && saved.sig !== sig(s))) regues(false);
        else { map = saved; paint(); }
        sw.refresh();
      };
      ctx.podcastMap = function () { return map; };
      ctx.podcastRefresh();

      /* ---------- analyze -> review -> apply */
      function params() {
        return { speakers: map.speakers.map(function (s, k) { return { name: s.name || defName(k), mic: s.mic, cam: s.cam, prio: st.prio === k ? 1 : 0 }; }),
                 wide: map.wide === undefined ? null : map.wide, confirm: st.confirm, min_shot: st.minShot, max_shot: st.maxShot, react: !!st.react,
                 cross_wide: !!st.crossWide, silence_wide: !!st.silenceWide, dom_db: st.domDb, preroll: st.preroll, sync: !!st.sync };
      }
      function run() {
        paint();
        if (!valid.ok) { ui.toast(valid.msg, { kind: 'err' }); return; }
        var stages = [{ id: 'audio', label: T('stage.audio'), w: 0.35 }, { id: 'owner', label: T('stage.owner'), w: 0.15 }];
        if (st.sync) stages.push({ id: 'sync', label: T('stage.sync'), w: 0.35 });
        stages.push({ id: 'shots', label: T('stage.shots'), w: 0.15 });
        ctx.run({ action: 'analyze', title: T('runTitle'), params: params(), stages: stages, onResult: function (data) { openReview(data.review); } });
      }
      ctx.podcastRun = run;

      function openReview(file) {
        var doc;
        try { doc = ui.loadReview(file); } catch (e) { ctx.error(e); return; }
        var cams = (doc.cams || []).map(function (c) { return { track: c.track, name: c.name, kind: c.kind, k: c.kind === 'wide' ? -1 : (c.spk || [0])[0] }; });
        var items = doc.items;
        var rib = ribbon({ label: T('ribLabel'), onClick: function (t) { nearest(t); } });
        var legend = ui.h('div', { class: 'legend podcast-legend' });
        var warnBox = ui.h('div', { class: 'podcast-warns' });
        var list = null;
        function activeIndex() { var a = list && list.el.querySelector('.rv.is-active'); return a ? Number(a.getAttribute('data-i')) : -1; }
        function redraw() {
          var eff = P.effective(items), ai = activeIndex();
          var lanes = (doc.speakers || []).map(function (s, k) { return (doc.lanes && doc.lanes[String(k)]) || []; });
          rib.set({ total: doc.duration, lanes: lanes, cross: (doc.lanes && doc.lanes.x) || [], shots: eff, cams: cams,
                    active: ai >= 0 ? items[ai] : null, playhead: ai >= 0 ? items[ai].t0 : null });
          var sc = doc.scope || [0, doc.duration];
          legend.innerHTML = legendHtml(eff, cams, sc[1] - sc[0]);
        }
        function nearest(t) {
          for (var i = 0; i < items.length; i++) if (t >= items[i].t0 && t < items[i].t1) { list.setFilter('all'); list.setActive(i, true); redraw(); return; }
        }
        function pick(it, c) {
          if (it.cam === c.track && it.on) { ui.toast(T('alreadyCam', { cam: c.name }), 'check'); return; }
          it.cam = c.track; it.label = c.name; it.pick = it.auto !== undefined && it.auto !== c.track; it.on = true; it.touched = true;
          list.refresh(); redraw();
        }
        var filters = cams.map(function (c) { return { id: 'c' + c.track, label: c.name, test: function (it) { return it.cam === c.track; } }; });
        filters.push({ id: 'low', label: T('filterLow'), icon: 'alert', test: function (it) { return typeof it.conf === 'number' && it.conf < 0.6 && it.kind === 'spk'; } });
        list = ctx.review({
          file: file, doc: doc, unit: T('reviewUnit'), verbOn: T('verbOn'), verbOff: T('verbOff'), markerTag: TAG, filters: filters,
          title: function (on, all) {
            var off = all - on;
            return U.esc(T('rvTitle', { n: P.switches(items) })) + '<small>' + U.esc(off ? T('rvShotsSkipped', { n: all, off: off }) : T('rvShots', { n: all })) + '</small>';
          },
          ctx: function (it) {
            if (it.cam === undefined) return '<mark>' + U.esc(it.label || 'x') + '</mark>';
            return '<i class="podcast-key" style="background:' + camVar(cams, it.cam) + '"></i><mark>' + U.esc(camName(cams, it.cam)) + '</mark>' +
              (it.text ? ' <span data-i18n-skip>' + U.esc('"' + it.text + '"') + '</span>' : '');   // transcript = user content
          },
          tags: function (it) {
            var h = '';
            if (WHY[it.why]) h += '<span class="tag tag-line">' + U.esc(T('why.' + it.why)) + '</span>';
            if (it.pick) h += '<span class="tag tag-warn">' + U.esc(T('tagManual')) + '</span>';
            if (typeof it.conf === 'number' && it.kind === 'spk' && !it.pick) {
              var p = Math.round(it.conf * 100);
              h += '<span class="tag tag-' + (it.conf >= 0.8 ? 'ok' : it.conf >= 0.6 ? 'line' : 'warn') + ' conf" title="' + U.esc(T('confTitle', { p: p })) + '">' + p + '%</span>';
            }
            return h;
          },
          note: function (it) {
            if (it.pick) return { kind: 'ok', icon: 'check', text: T('pickedNote', { cam: camName(cams, it.auto) }) };
            var n = it.note;
            if (!n) return null;
            if (typeof n === 'string') return { icon: 'info', text: n };
            return { kind: n.type === 'warn' ? 'warn' : '', icon: n.type === 'warn' ? 'alert' : 'info', text: n.text || '' };
          },
          rowActions: cams.map(function (c) { return { label: c.name, icon: c.kind === 'wide' ? 'grid' : 'mic', run: function (it) { pick(it, c); } }; }),
          bulkAction: { label: T('toMarkers'), run: function () { sendMarkers(items, cams); } },
          onChange: function () { redraw(); },
          primary: { label: function () { return T('applyN', { n: P.switches(items) }); }, onApply: function (sel, d, f) { applyReview(d, f); } }
        });
        if (!list) return;
        list.el.classList.add('podcast-rv');
        var head = list.el.querySelector('.rv-head');
        head.insertBefore(rib.el, head.querySelector('.legend'));
        head.insertBefore(legend, head.querySelector('.rv-tools'));
        head.insertBefore(warnBox, head.querySelector('.rv-tools'));
        (doc.notes || []).forEach(function (n) { warnBox.appendChild(ui.h('p', { class: 'podcast-warn', html: ui.icon('info') + '<span>' + U.esc(n) + '</span>' })); });
        (doc.sync || []).forEach(function (r) {
          if (!r.warn) return;
          var d = U.dec(Math.abs(r.offset), 2) + ' ' + AC.t('unit.sec'), who = T(r.kind === 'mic' ? 'syncMic' : 'syncCam', { name: r.name });
          warnBox.appendChild(ui.h('p', { class: 'podcast-warn is-warn', html: ui.icon('alert') + '<span>' + U.esc(T(r.offset > 0 ? 'syncLate' : 'syncEarly', { who: who, d: d })) + '</span>' }));
        });
        list.box.addEventListener('click', function () { setTimeout(redraw, 0); });
        list.box.addEventListener('keydown', function () { setTimeout(redraw, 0); });
        redraw();
        ctx.podcastReview = { list: list, items: items, doc: doc, pick: pick, cams: cams, redraw: redraw };
      }

      function sendMarkers(items, cams) {
        var eff = P.effective(items);
        if (!eff.length) { ui.toast(T('noShots')); return; }
        var list = eff.map(function (s) {
          var c = cams.filter(function (x) { return x.track === s.cam; })[0] || { k: -1, name: 'V' + (s.cam + 1) };
          return { t: s.t0, name: T('markerName', { name: c.name }), comment: def.title, tag: TAG, color: c.k < 0 ? 5 : MARKER_COLOR[c.k % MARKER_COLOR.length] };
        });
        AC.apply.markers(list, { tag: TAG }).then(function (r) { ui.toast(T('markersMade', { n: (r && r.n) || list.length }), 'marker'); },
          function (e) { ui.toast(T('markersFail', { msg: U.errMsg(e) }), { kind: 'err' }); });
      }

      function applyReview(doc, file) {
        ctx.run({ action: 'apply', review: file, title: T('applyTitle'), onResult: function (data) { applyPlan(data.plan, doc); } });
      }

      // Host side of the plan: clone -> razor camera tracks (chunks) -> enable/disable per camera -> open the clone.
      function applyPlan(plan, doc) {
        if (!plan || plan.kind !== 'multicam') { ctx.error(U.err('BAD_PLAN', T('badPlan'))); return; }
        var task = new AC.Task({ tool: 'podcast', action: 'multicam', title: def.title, label: T('taskLabel'), kind: 'host' });
        AC.history.track(task);
        AC.seq.hold(task.promise);
        var cancelled = false, cuts = plan.cuts || [], chunks = Math.ceil(cuts.length / RAZOR_CHUNK), t0 = Date.now();
        task._cancel = function () { cancelled = true; };
        task.plan([{ id: 'clone', label: T('stClone'), w: 0.05 },
                   { id: 'razor', label: T('stRazor', { n: cuts.length }), w: 0.8, sub: T('untouched') },
                   { id: 'disable', label: T('stDisable'), w: 0.15 }]);
        ctx.track(task, { title: T('trackTitle'), note: T('trackNote'),
          onDone: function (res) { showResult(res, plan, doc); }, onRetry: function () { applyPlan(plan, doc); } });
        var srcId = (doc.seq && doc.seq.id) || AC.seq.locked() || '', res = { razor: 0, skipped: 0, enabled: 0, disabled: 0, unsplit: 0 };
        task.stage({ id: 'clone' });
        var name = ((doc.seq && doc.seq.name) || (AC.seq.peek() || {}).name || 'Podcast') + AC.brand.suffix;
        AC.host.json('bac_seqInfo', 'lite', srcId).then(function (s) {
          if (!s) throw U.err('NO_SEQ', T('noSeq'), T('noSeqHint'));
          if (Math.abs(s.duration - doc.duration) > 0.05) throw U.err('SEQ_CHANGED', T('seqChanged', { name: s.name }), T('seqChangedHint'));
          return AC.host.exec('bac_cloneSeq', [name, srcId], { json: true, timeout: 60000 });
        }).then(function (c) {
          U.assign(res, { id: c.id, name: c.name, origId: c.origId, origName: c.origName });
          task.seqName = c.origName;
          task.stageDone('clone');
          task.stage({ id: 'razor', sub: T('untouched') });
          var k = 0;
          function nextChunk() {
            if (k >= chunks) return Promise.resolve();
            if (cancelled) throw U.err('CANCELLED', T('cancelPartial', { name: c.name }), T('cancelHint'));
            var from = k * RAZOR_CHUNK, to = Math.min(cuts.length, from + RAZOR_CHUNK);
            k++;
            return AC.host.exec('bac_podcast_razor', [c.id, cuts, from, to], { json: true, timeout: 180000 }).then(function (r) {
              res.razor += r.done || 0; res.skipped += r.skipped || 0;
              task.progress(k / chunks * 100);
              return nextChunk();
            });
          }
          return nextChunk().then(function () {
            task.stageDone('razor', null, T('cutsDone', { n: res.razor }));
            task.stage({ id: 'disable' });
            var i = 0, cams = plan.cams || [];
            function nextCam() {
              if (i >= cams.length) return Promise.resolve();
              if (cancelled) throw U.err('CANCELLED', T('cancelUnfinished', { name: c.name }), T('cancelHint'));
              var tr = cams[i++];
              return AC.host.exec('bac_podcast_disable', [c.id, tr, plan.shots, plan.scope], { json: true, timeout: 120000 }).then(function (r) {
                res.enabled += r.enabled || 0; res.disabled += r.disabled || 0; res.unsplit += r.unsplit || 0;
                task.progress(i / cams.length * 100);
                return nextCam();
              });
            }
            return nextCam();
          }).then(function () {
            task.stageDone('disable');
            return AC.host.json('bac_openSequence', c.id).catch(function () { return null; });
          });
        }).then(function () {
          res.secs = (Date.now() - t0) / 1000;
          task.summary = T('summaryApply', { n: plan.switches, name: res.name });
          task.done(res);
        }).catch(function (e) { task.fail(e); });
        return task;
      }

      function showResult(res, plan, doc) {
        var cams = P.cams({ speakers: (doc.speakers || []).map(function (s) { return { name: s.name, cam: s.cam }; }), wide: doc.wide });
        var shots = (plan.shots || []).map(function (s) { return { t0: s[0], t1: s[1], cam: s[2] }; });
        var span = plan.scope ? plan.scope[1] - plan.scope[0] : doc.duration, avg = shots.length ? span / shots.length : 0;
        var extra = ui.h('div', { class: 'sec podcast-result' });
        var rib = ribbon({ label: T('ribNew') });
        extra.appendChild(rib.el);
        extra.appendChild(ui.h('div', { class: 'legend podcast-legend', html: legendHtml(shots, cams, span) }));
        setTimeout(function () { rib.set({ total: doc.duration, lanes: [], cross: [], shots: shots, cams: cams }); }, 0);
        var wideShare = 0;
        shots.forEach(function (s) { if (doc.wide !== null && s.cam === doc.wide) wideShare += s.t1 - s.t0; });
        var swSec = ui.h('div', { class: 'sec podcast-sw-result' }, [ui.h('h2', { class: 'sec-h', html: '<span>' + U.esc(T('secSwitch')) + '</span>' })]);
        var swr = switcher(ctx, function () { return cams; });
        swSec.appendChild(swr.el);
        extra.appendChild(swSec);
        ctx.result({
          title: T('resTitle'),
          sub: T('resSub', { name: res.name, time: U.dtk(res.secs || 0) }),
          statsList: [{ label: T('statSwitches'), value: String(plan.switches), accent: true }, { label: T('statAvg'), value: U.dtk(avg) },
                      doc.wide !== null && doc.wide !== undefined ? { label: 'Wide', value: U.pct(wideShare / (span || 1)) } : { label: T('statCams'), value: String(cams.length) }],
          extra: extra,
          original: { id: res.origId, name: res.origName },
          onEdit: function () { AC.router.go('tool/podcast/review'); },
          onDelete: function () {
            AC.host.json('bac_deleteSequence', res.id).then(function () { ui.toast(T('resDeleted'), 'trash'); AC.router.go('tool/podcast'); },
              function (e) { ui.toast(T('deleteFail', { msg: U.errMsg(e) }), { kind: 'err' }); });
          },
          warn: res.unsplit ? { title: T('unsplitTitle', { n: res.unsplit }), text: T('unsplitText') } : null,
          next: def.next
        });
        setTimeout(swr.refresh, 0);
      }
    },

    onShow: function (ctx) { if (ctx.podcastRefresh) ctx.podcastRefresh(); },
    onSeq: function (lite, ctx) { if (ctx.podcastRefresh) ctx.podcastRefresh(); },
    // Alt+1..5 = "Ganti kamera di playhead" to camera 1..5 of the current mapping.
    onKey: function (e, ctx) {
      if (!e.altKey || e.ctrlKey || AC.keys.typing()) return false;
      var n = Number(e.key);
      if (!(n >= 1 && n <= 5) || !ctx.podcastMap) return false;
      var cams = P.cams(ctx.podcastMap());
      if (!cams[n - 1]) return false;
      e.preventDefault();
      switchTo(ctx, cams[n - 1], cams);
      return true;
    }
  });

  /* ================================================================ "Ganti kamera di playhead" */
  function switchTo(ctx, c, cams) {
    var tracks = cams.map(function (x) { return x.track; }), split = !!ctx.state.split;
    return AC.host.exec('bac_podcast_switchAt', [c.track, tracks, split, ''], { json: true, timeout: 30000 }).then(function (r) {
      var hasSpan = r.from !== null && r.from !== undefined;
      var msg = !r.changed ? T('swAlready', { name: c.name })
        : hasSpan ? T('swShownSpan', { name: c.name, from: U.tcode(r.from), to: U.tcode(r.to) }) : T('swShown', { name: c.name });
      ui.toast(msg + (r.cut ? T('swCut') : '') + '.', r.changed ? 'check' : 'info');
      AC.bus.emit('podcast-switch', r);
      return r;
    }, function (e) { ui.toast(U.errMsg(e), { kind: 'err' }); return null; });
  }

  // Camera buttons + "Potong dulu di playhead" + what is visible at the playhead now. getCams() -> [{track, name, k}]
  function switcher(ctx, getCams) {
    var st = ctx.state;
    var row = ui.h('div', { class: 'btn-row podcast-cams', role: 'group', 'aria-label': T('secSwitch') });
    var status = ui.h('small', { class: 'help podcast-status', text: T('swHint') });
    var split = ui.switch({ label: T('split'), help: T('splitHelp'), checked: !!st.split,
                            onChange: function (v) { st.split = v; ctx.save(); } });
    var el = ui.h('div', { class: 'sec podcast-switch' }, [row, split, status]);
    var visible = null;
    function repaint() {
      var cams = getCams();
      row.innerHTML = '';
      if (!cams.length) { row.appendChild(ui.h('span', { class: 'help', text: T('setupFirst') })); return; }
      cams.forEach(function (c, i) {
        var b = ui.button({ label: c.name, small: true, kbd: i < 5 ? 'Alt ' + (i + 1) : '', title: T('showAt', { name: c.name }), onClick: function () {
          switchTo(ctx, c, cams).then(function (r) { if (r) refresh(); });
        } });
        b.setAttribute('aria-pressed', visible === c.track ? 'true' : 'false');
        b.insertBefore(ui.h('i', { class: 'podcast-key', style: { background: colorVar(c.k) }, 'aria-hidden': 'true' }), b.firstChild);
        row.appendChild(b);
      });
    }
    function refresh() {
      var cams = getCams();
      if (!cams.length || !AC.host.available) return;
      AC.host.json('bac_podcast_camAt', cams.map(function (c) { return c.track; }), '').then(function (r) {
        if (!r) { status.textContent = T('noActiveSeq'); visible = null; repaint(); return; }
        visible = r.visible;
        var nm = null;
        cams.forEach(function (c) { if (c.track === r.visible) nm = c.name; });
        status.textContent = !r.result ? T('notResult', { name: r.name })
          : nm ? T('visibleAt', { t: U.tcode(r.t), name: nm }) : T('noCamAt', { t: U.tcode(r.t) });
        repaint();
      }, function () { /* host not ready: keep the hint */ });
    }
    AC.bus.on('podcast-switch', function () { if (el.isConnected) refresh(); });
    AC.seq.on('change', function () { if (el.isConnected) refresh(); });
    repaint();
    return { el: el, repaint: repaint, refresh: refresh };
  }
})();
