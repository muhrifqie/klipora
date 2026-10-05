/* angles.js: Auto Angles / Angle Otomatis (tool id "angles"). Single-camera virtual multicam: every clip of the camera
   track gets a static punch-in (wide / medium / close) that never repeats across a cut, anchored on the on-screen action
   or a face. Flow: main pane -> engine "analyze" -> review (shot list + angle lane + frame preview with crop boxes,
   angle editable per shot: buttons, 1/2/3 keys, click a box) -> engine "apply" (plan) -> host on a clone:
   bac_cloneSeq, bac_angles_split (optional sentence splits), bac_angles_apply (static Motion) -> result card.
   Engine: engine/ac/tools/angles.py. Host: panel/host/39_angles.jsx. Docs: docs/tools/angles.md. */
(function () {
  'use strict';
  var AC = window.AC, U = AC.util, ui = AC.ui;
  var ANG = ['wide', 'medium', 'close'];
  // Labels are translated when used (a live language switch re-renders the page).
  function NAME(a) { return AC.t('angles.name.' + a); }
  var SRCS = ['activity', 'face', 'center', 'hold'];
  function anchorSrcName(s) { return AC.t('angles.src.' + (SRCS.indexOf(s) >= 0 ? s : 'center')); }
  function presetList() {
    return [
      { value: 'halus', label: AC.t('angles.preset.halus'), hint: AC.t('angles.preset.halusHint'), values: { s0: 100, s1: 112, s2: 125, minShot: 3, every: 8 } },
      { value: 'normal', label: AC.t('angles.preset.normal'), hint: AC.t('angles.preset.normalHint'), values: { s0: 100, s1: 118, s2: 140, minShot: 2.5, every: 6 } },
      { value: 'sering', label: AC.t('angles.preset.sering'), hint: AC.t('angles.preset.seringHint'), values: { s0: 100, s1: 120, s2: 150, minShot: 2, every: 4 } }
    ];
  }
  var SPLIT_CHUNK = 20, SPLIT_MAX = 60, APPLY_CHUNK = 60;   // razor chunks adapt (see chunks())

  function scaleOf(st, a) { return a === 'wide' ? st.s0 : a === 'medium' ? st.s1 : st.s2; }
  function pct(v) { return U.dec(v, Math.abs(v - Math.round(v)) < 0.05 ? 0 : 1) + '%'; }
  function angleName(doc, a) { var s = doc && doc.angles && doc.angles[a]; return NAME(a) + (s ? ' ' + pct(s.scale) : ''); }

  /* ---------------- small visuals */
  // Nested crop boxes of the three angles on a frame of the sequence aspect (main pane).
  function cropDiagram() {
    var inner = ui.h('div', { class: 'angles-demo-in' });
    var el = ui.h('div', { class: 'angles-demo', 'aria-hidden': 'true' }, [inner]);
    function set(st, aspect) {
      inner.style.paddingTop = (100 / (aspect || 2.3875)) + '%';   // % padding = share of the parent width
      inner.innerHTML = ANG.map(function (a) {
        if (st.use.indexOf(a) < 0) return '';
        var w = 100 / (scaleOf(st, a) / 100);
        return '<span class="angles-demo-box is-' + a + '" style="width:' + w + '%;height:' + w + '%;left:' + (50 - w / 2) + '%;top:' + (50 - w / 2) + '%"><i>' + U.esc(NAME(a)) + '</i></span>';
      }).join('');
    }
    return { el: el, set: set };
  }

  // Angle lane: one block per shot, height = how close the angle is; off = outlined; active = ringed.
  function angleLane(o) {
    var c = ui.h('canvas', { class: 'angles-lane', role: 'img', 'aria-label': AC.t('angles.laneAria') });
    var st = { items: [], total: 0, active: -1 };
    var H = { wide: 0.42, medium: 0.7, close: 1 }, A = { wide: 0.38, medium: 0.62, close: 0.92 };
    function draw() {
      var w = c.clientWidth, h = c.clientHeight;
      if (!w || !h) return;
      var dpr = window.devicePixelRatio || 1;
      if (c.width !== Math.round(w * dpr) || c.height !== Math.round(h * dpr)) { c.width = Math.round(w * dpr); c.height = Math.round(h * dpr); }
      var g = c.getContext('2d'), T = st.total || 1;
      g.setTransform(dpr, 0, 0, dpr, 0, 0); g.clearRect(0, 0, w, h);
      var acc = ui.css('--accent') || '#f7843e', line = ui.css('--border-strong') || '#666', text = ui.css('--text-1') || '#eee';
      g.fillStyle = ui.css('--surface-2') || '#333'; g.fillRect(0, 0, w, h);
      st.items.forEach(function (it, i) {
        var x0 = it.t0 / T * w, ww = Math.max(1, (it.t1 - it.t0) / T * w), bh = Math.round(h * (H[it.angle] || 0.5));
        if (it.on) { g.fillStyle = ui.hexA(acc, A[it.angle] || 0.5); g.fillRect(x0, h - bh, ww, bh); }
        else { g.strokeStyle = line; g.lineWidth = 1; g.strokeRect(x0 + 0.5, h - bh + 0.5, Math.max(0, ww - 1), bh - 1); }
        if (ww > 3 && i > 0) { g.fillStyle = ui.css('--bg') || '#000'; g.fillRect(Math.round(x0), 0, 1, h); }
      });
      var a = st.items[st.active];
      if (a) { g.strokeStyle = text; g.lineWidth = 1.5; g.strokeRect(a.t0 / T * w + 0.75, 0.75, Math.max(2, (a.t1 - a.t0) / T * w) - 1.5, h - 1.5); }
    }
    var raf = 0;
    function redraw() { cancelAnimationFrame(raf); raf = requestAnimationFrame(draw); }
    if (window.ResizeObserver) new ResizeObserver(redraw).observe(c);
    AC.bus.on('theme', redraw);
    c.addEventListener('click', function (e) {
      if (!o || !o.onPick) return;
      var r = c.getBoundingClientRect(), t = (e.clientX - r.left) / r.width * (st.total || 0);
      for (var i = 0; i < st.items.length; i++) if (t >= st.items[i].t0 && t < st.items[i].t1) { o.onPick(i); return; }
    });
    return {
      el: c,
      set: function (p) {
        if (p.items) st.items = p.items;
        if (p.total !== undefined) st.total = p.total;
        if (p.active !== undefined) st.active = p.active;
        var n = { wide: 0, medium: 0, close: 0 };
        st.items.forEach(function (it) { if (it.on) n[it.angle]++; });
        c.setAttribute('aria-label', AC.t('angles.laneAria') + ': ' + ANG.map(function (k) { return n[k] + ' ' + NAME(k); }).join(', '));
        redraw();
      },
      destroy: function () { AC.bus.off('theme', redraw); }
    };
  }

  // Frame of the active shot with the crop box of every angle; click a box (or 1/2/3) to pick it.
  function framePreview(o) {
    var img = ui.h('img', { class: 'angles-pv-img', alt: '' });
    var boxes = ui.h('div', { class: 'angles-pv-boxes' });
    var frame = ui.h('div', { class: 'angles-pv-frame' }, [img, boxes]);
    var msg = ui.h('div', { class: 'angles-pv-msg', text: AC.t('angles.pvLoading') });
    frame.appendChild(msg);
    var seg = ui.segmented({ small: true, ariaLabel: AC.t('angles.pvSegAria'), options: o.angles.map(function (a) { return { value: a, label: angleName(o.doc, a) }; }),
                             onChange: function (v) { if (cur) o.onSet(cur.i, v); } });
    var info = ui.h('div', { class: 'angles-pv-info' });
    var toggle = ui.h('button', { class: 'linkbtn', type: 'button' });
    var body = ui.h('div', { class: 'angles-pv-body' }, [frame, ui.h('div', { class: 'angles-pv-ctl' }, [seg.el, info])]);
    var el = ui.h('div', { class: 'angles-pv' }, [ui.h('div', { class: 'angles-pv-h' }, [ui.h('span', { text: AC.t('angles.pvTitle') }), toggle]), body]);
    var cur = null, cache = {}, want = '', open = AC.store.get('angles.preview', true);
    function paintToggle() { toggle.textContent = AC.t(open ? 'angles.pvHide' : 'angles.pvShow'); body.hidden = !open; el.classList.toggle('is-closed', !open); }
    toggle.addEventListener('click', function () { open = !open; AC.store.set('angles.preview', open); paintToggle(); if (open && cur) show(cur.i, cur.it, true); });
    paintToggle();
    var aspect = o.doc.size ? o.doc.size[0] / o.doc.size[1] : 2.3875;
    frame.style.paddingTop = (100 / aspect) + '%';
    boxes.addEventListener('click', function (e) {
      var b = e.target.closest('[data-a]'); if (b && cur) o.onSet(cur.i, b.getAttribute('data-a'));
    });
    var load = U.debounce(function (it) {
      var key = it.media + '@' + it.sm;
      if (cache[key]) { img.src = cache[key]; return; }
      if (!it.media) { msg.textContent = AC.t('angles.pvNoMedia'); msg.hidden = false; return; }
      want = key;
      var job = AC.engine.worker({ tool: 'angles', action: 'frame', seq: null, record: false,
                                   params: { media: it.media, t: it.sm, width: 480, dir: o.dir } });
      job.then(function (r) {
        var url = 'file:///' + String(r.path).replace(/\\/g, '/').split('/').map(encodeURIComponent).join('/').replace(/^([A-Za-z])%3A/, '$1:');
        cache[key] = url;
        if (want === key) img.src = url;
      }, function (e) { if (want === key) { msg.textContent = AC.t('angles.pvLoadFail', { msg: U.errMsg(e) }); msg.hidden = false; } });
    }, 180);
    img.addEventListener('load', function () { msg.hidden = true; el.classList.add('has-img'); });
    img.addEventListener('error', function () { msg.textContent = AC.t('angles.pvImgErr'); msg.hidden = false; });
    function show(i, it, force) {
      var same = cur && cur.i === i && cur.it === it;
      cur = { i: i, it: it };
      if (!it) { info.textContent = ''; boxes.innerHTML = ''; return; }
      seg.set(it.angle, true);
      boxes.innerHTML = o.angles.map(function (a) {
        var op = it.opts && it.opts[a]; if (!op || !op.view) return '';
        var v = op.view;
        return '<button type="button" class="angles-pv-box is-' + a + (a === it.angle ? ' is-on' : '') + '" data-a="' + a + '" title="' + AC.i18n.html('angles.pvUse', { name: angleName(o.doc, a) }) + '" ' +
          'style="left:' + (v[0] * 100) + '%;top:' + (v[1] * 100) + '%;width:' + (v[2] * 100) + '%;height:' + (v[3] * 100) + '%"><i>' + U.esc(NAME(a)) + '</i></button>';
      }).join('') + (it.anchor && it.anchor.src !== 'center' ? '<span class="angles-pv-dot" style="left:' + (it.anchor.x * 100) + '%;top:' + (it.anchor.y * 100) + '%"></span>' : '');
      var prev = o.items()[i - 1], next = o.items()[i + 1];
      var same2 = (prev && prev.on && prev.angle === it.angle && Math.abs(prev.t1 - it.t0) < 0.01) || (next && next.on && next.angle === it.angle && Math.abs(it.t1 - next.t0) < 0.01);
      info.innerHTML = AC.i18n.html('angles.pvFocus', { name: anchorSrcName(it.anchor && it.anchor.src) }) +
        (same2 ? '<span class="angles-warn">' + ui.icon('alert') + U.esc(AC.t('angles.pvSame')) + '</span>' : '');
      if (open && (!same || force)) { msg.textContent = AC.t('angles.pvLoading'); load(it); }
    }
    return { el: el, show: show };
  }

  AC.tools.register({
    id: 'angles', group: 'kamera', order: 2,
    icon: 'angles',
    badges: ['baru'],
    text: function (t) {
      return { title: t('angles.title'), tab: t('angles.tab'), desc: t('angles.desc'), lead: t('angles.lead'), cta: t('angles.cta'),
               next: [{ tool: 'zoom', reason: t('angles.nextZoom') }, { tool: 'captions', reason: t('angles.nextCaptions') }] };
    },
    defaults: { preset: 'normal', s0: 100, s1: 118, s2: 140, minShot: 2.5, every: 6, use: ['wide', 'medium', 'close'], mode: 'cut',
                split: false, anchor: 'auto', balance: 'lebar', track: 'auto' },

    render: function (el, ctx) {
      var st = ctx.state, defs = ctx.def.defaults;
      Object.keys(defs).forEach(function (k) { if (st[k] === undefined || st[k] === null) st[k] = U.copy(defs[k]); });   // old / partial state
      if (!Array.isArray(st.use) || st.use.length < 2) st.use = ANG.slice();

      el.appendChild(ui.h('div', { class: 'tool-hero', html: '<span class="tool-ic">' + ui.icon('angles') + '</span><div><p>' + U.esc(ctx.def.lead) + '</p><div class="tags">' + ui.badge('baru') + '</div></div>' }));
      var notice = ui.h('div', { class: 'angles-notice' });
      el.appendChild(notice);

      /* style */
      var gaya = ui.section({ title: AC.t('angles.secStyle') });
      var presets = ui.presets({ options: presetList(), value: st.preset, ariaLabel: AC.t('angles.secStyle'), customHint: AC.t('angles.customHint'),
        onChange: function (v, p) {
          st.preset = v; U.assign(st, p.values);
          s0.set(st.s0, true); s1.set(st.s1, true); s2.set(st.s2, true); minShot.set(st.minShot, true); every.set(st.every, true);
          adv.setAside(''); paint(); ctx.save();
        } });
      gaya.add(presets);
      el.appendChild(gaya.el);

      /* variasi crop */
      var crop = ui.section({ title: AC.t('angles.secCrop'), help: AC.t('angles.secCropHelp') });
      var demo = cropDiagram();
      var chips = ui.chips({ multi: true, ariaLabel: AC.t('angles.chipsAria'), value: st.use, options: [], onChange: function (v) {
        if (v.length < 2) { ui.toast(AC.t('angles.min2'), 'alert'); chips.set(st.use, true); return; }
        st.use = ANG.filter(function (a) { return v.indexOf(a) >= 0; }); paint(); ctx.save();
      } });
      crop.add([demo.el, chips]);
      el.appendChild(crop.el);

      /* kapan ganti */
      var when = ui.section({ title: AC.t('angles.secWhen') });
      var mode = ui.radioCards({ ariaLabel: AC.t('angles.secWhen'), value: st.mode, onChange: function (v) { st.mode = v; paint(); ctx.save(); }, options: [
        { value: 'cut', title: AC.t('angles.mode.cut'), desc: AC.t('angles.mode.cutDesc') },
        { value: 'rhythm', title: AC.t('angles.mode.rhythm'), desc: AC.t('angles.mode.rhythmDesc') }
      ] });
      function manual() { st.preset = presets.sync({ s0: st.s0, s1: st.s1, s2: st.s2, minShot: st.minShot, every: st.every }); adv.setAside(st.preset ? '' : AC.t('common.custom')); paint(); ctx.save(); }
      var minShot = ui.slider({ label: AC.t('angles.minShot'), min: 1, max: 6, step: 0.5, value: st.minShot, unit: ' ' + AC.t('unit.sec'), decimals: 1,
        help: AC.t('angles.minShotHelp'), onInput: function (v) { st.minShot = v; manual(); } });
      var split = ui.switch({ label: AC.t('angles.split'), checked: st.split,
        help: AC.t('angles.splitHelp'),
        onChange: function (v) { st.split = v; paint(); ctx.save(); } });
      var every = ui.slider({ label: AC.t('angles.every'), min: 3, max: 12, step: 1, value: st.every, unit: ' ' + AC.t('unit.sec'), decimals: 0,
        help: AC.t('angles.everyHelp'), onInput: function (v) { st.every = v; manual(); } });
      when.add([mode, minShot, split, every]);
      el.appendChild(when.el);

      /* focus point */
      var fokus = ui.section({ title: AC.t('angles.secFocus') });
      var anchorHelp = ui.h('p', { class: 'help' });
      var anchor = ui.segmented({ ariaLabel: AC.t('angles.secFocus'), value: st.anchor, onChange: function (v) { st.anchor = v; paint(); ctx.save(); },
        options: ['auto', 'activity', 'face', 'center'].map(function (v) { return { value: v, label: AC.t('angles.anchor.' + v) }; }) });
      anchor.el.classList.add('angles-anchor');
      fokus.add([anchor, anchorHelp]);
      el.appendChild(fokus.el);

      /* lanjutan */
      var adv = ui.advanced({ title: AC.t('common.advanced'), key: 'angles' });
      var s0 = ui.slider({ label: AC.t('angles.scale', { name: NAME('wide') }), min: 100, max: 130, step: 1, value: st.s0, unit: '%', decimals: 0, onInput: function (v) { st.s0 = v; manual(); } });
      var s1 = ui.slider({ label: AC.t('angles.scale', { name: NAME('medium') }), min: 104, max: 170, step: 1, value: st.s1, unit: '%', decimals: 0, onInput: function (v) { st.s1 = v; manual(); } });
      var s2 = ui.slider({ label: AC.t('angles.scale', { name: NAME('close') }), min: 110, max: 220, step: 1, value: st.s2, unit: '%', decimals: 0,
        help: AC.t('angles.scaleHelp'), onInput: function (v) { st.s2 = v; manual(); } });
      var balance = ui.segmented({ small: true, ariaLabel: AC.t('angles.balance'), value: st.balance, onChange: function (v) { st.balance = v; ctx.save(); },
        options: ['lebar', 'rata', 'dekat'].map(function (v) { return { value: v, label: AC.t('angles.bal.' + v) }; }) });
      balance.el.classList.add('angles-balance');
      var track = ui.select({ ariaLabel: AC.t('angles.track'), value: st.track, options: [{ value: 'auto', label: AC.t('angles.anchor.auto') }], onChange: function (v) { st.track = v; ctx.save(); } });
      var scaleWarn = ui.h('p', { class: 'help angles-bad', hidden: true, text: AC.t('angles.scaleOrder') });
      adv.add([s0, s1, s2, scaleWarn, ui.field({ label: AC.t('angles.balance'), help: AC.t('angles.balanceHelp'), control: balance }), ui.field({ label: AC.t('angles.track'), help: AC.t('angles.trackHelp'), control: track })]);
      el.appendChild(adv.el);

      function scalesOk() {
        var v = st.use.map(function (a) { return scaleOf(st, a); });
        for (var i = 1; i < v.length; i++) if (v[i] - v[i - 1] < 2) return false;
        return true;
      }
      function mainCount() {   // clips on the camera track (lite info), null = no sequence
        var s = AC.seq.peek(); if (!s) return null;
        var cnt = function (t) { return t ? (t.count !== undefined ? t.count : (t.clips || []).length) : 0; };
        if (st.track !== 'auto') return cnt((s.video || [])[Number(st.track)]);
        var best = 0;
        (s.video || []).forEach(function (t) { if (!best) best = cnt(t); });
        return best;
      }
      function paint() {
        demo.set(st, (function () { var s = AC.seq.peek(); return s && s.width && s.height ? s.width / s.height : 2.3875; })());
        chips.setOptions(ANG.map(function (a) { return { value: a, label: NAME(a) + ' ' + pct(scaleOf(st, a)) }; }));
        chips.set(st.use, true);
        every.el.hidden = !st.split;
        anchorHelp.textContent = /^(auto|activity|face|center)$/.test(st.anchor) ? AC.t('angles.anchorHelp.' + st.anchor) : '';
        var ok = scalesOk();
        scaleWarn.hidden = ok;
        notice.innerHTML = '';
        var n = mainCount();
        if (n === 0) notice.appendChild(ui.alert({ kind: 'warn', title: AC.t('angles.noClips'), text: AC.t('angles.noClipsText') }));
        else if (n !== null && n < 3 && !st.split) {
          notice.appendChild(ui.alert({ kind: 'info', title: AC.t('angles.fewClips', { n: n }),
            text: AC.t('angles.fewClipsText'),
            actions: [{ label: AC.t('angles.fewClipsAction'), onClick: function () { split.set(true); } }] }));
        }
        ctx.setPrimary({ label: ctx.def.cta, disabled: !ok, title: ok ? '' : AC.t('angles.scaleBad') });
      }
      function fillTracks() {
        var s = AC.seq.peek(), opts = [{ value: 'auto', label: AC.t('angles.anchor.auto') }];
        ((s && s.video) || []).forEach(function (t, i) {
          var c = t.count !== undefined ? t.count : (t.clips || []).length;
          if (c) opts.push({ value: String(i), label: 'V' + (i + 1) + (t.name ? ' ' + t.name : '') + ' (' + AC.t('angles.clipCount', { n: c }) + ')' });
        });
        track.setOptions(opts);
        if (!opts.some(function (o) { return o.value === st.track; })) { st.track = 'auto'; track.set('auto', true); }
      }
      ctx._paint = function () { fillTracks(); paint(); };

      ctx.dock({ primary: { label: ctx.def.cta, icon: 'angles', onClick: run } });
      fillTracks();
      paint();

      function params() {
        return { scales: { wide: st.s0, medium: st.s1, close: st.s2 }, use: st.use.slice(), mode: st.mode, min_shot: st.minShot,
                 split: !!st.split, every: st.every, anchor: st.anchor, weights: st.balance, track: st.track };
      }
      function run() {
        if (!scalesOk()) { ui.toast(AC.t('angles.scaleOrder'), 'alert'); return; }
        var stages = [{ id: 'read', label: AC.t('angles.st.read'), w: 0.05 }, { id: 'words', label: AC.t(st.split ? 'angles.st.sentences' : 'angles.st.words'), w: st.split ? 0.3 : 0.05 },
                      { id: 'vision', label: AC.t('angles.st.vision'), w: st.anchor === 'center' ? 0.02 : 0.55 }, { id: 'plan', label: AC.t('angles.st.plan'), w: 0.1 }];
        ctx.run({ action: 'analyze', title: AC.t('angles.runAnalyze'), params: params(), stages: stages, onResult: function (data) { openReview(data.review); } });
      }

      /* ---------------- Review */
      var lane = null, pv = null, list = null, doc = null, obs = null;
      function openReview(path) {
        try { doc = ui.loadReview(path); } catch (e) { ctx.error(e); return; }
        var angles = ANG.filter(function (a) { return doc.angles ? !!doc.angles[a] : true; });
        var items = doc.items;
        if (lane) lane.destroy();
        if (obs) obs.disconnect();
        lane = angleLane({ onPick: function (i) { list.setActive(i, true); } });
        pv = framePreview({ doc: doc, angles: angles, dir: AC.sys.dirname(path), items: function () { return items; }, onSet: function (i, a) { setAngle(i, a); } });
        var sched = 0;
        function sync() {
          cancelAnimationFrame(sched);
          sched = requestAnimationFrame(function () {
            var i = active();
            lane.set({ items: items, total: doc.duration, active: i });
            pv.show(i < 0 ? 0 : i, items[i < 0 ? 0 : i]);
          });
        }
        list = ctx.review({
          doc: doc, file: path, unit: AC.i18n.raw('angles.unit'), verbOn: AC.t('angles.verbOn'), verbOff: AC.t('angles.verbOff'), markerTag: '[Klipora-ANG]',
          markerName: function (it) { return AC.t('angles.markerName', { name: angleName(doc, it.angle) }); },
          title: function (n, all) {
            sync();
            var by = {};
            items.forEach(function (it) { if (it.on) by[it.angle] = (by[it.angle] || 0) + 1; });
            var parts = angles.filter(function (a) { return by[a]; }).map(function (a) { return by[a] + ' ' + NAME(a); });
            return AC.i18n.html('angles.rvTitle', { n: n }) + '<small>' + AC.i18n.html('angles.rvOf', { n: all }) + (parts.length ? ', ' + U.esc(parts.join(', ')) : '') + '</small>';
          },
          ctx: function (it) {
            return '<mark class="angles-m is-' + U.esc(it.angle) + '">' + U.esc(angleName(doc, it.angle)) + '</mark> ' + (it.ctx ? '<span data-i18n-skip>' + U.esc(it.ctx) + '</span>' : '<i>' + U.esc(AC.t('angles.noWords')) + '</i>');
          },
          tags: function (it) {
            var h = '<span class="tag tag-line">' + U.esc(anchorSrcName(it.anchor && it.anchor.src)) + '</span>';
            if (it.why === 'sentence') h += '<span class="tag tag-ai">' + U.esc(AC.t('angles.newCut')) + '</span>';
            if (it.picked) h += '<span class="tag tag-ok">' + U.esc(AC.t('angles.picked')) + '</span>';
            return h;
          },
          filters: angles.map(function (a) { return { id: a, label: NAME(a), test: function (it) { return it.angle === a; } }; }).concat([
            { id: 'split', label: AC.t('angles.newCut'), icon: 'split', test: function (it) { return it.why === 'sentence'; } },
            { id: 'warn', label: AC.t('angles.check'), icon: 'alert', test: function (it) { return !!(it.note && it.note.type === 'warn'); } }]),
          rowActions: angles.map(function (a) { return { label: angleName(doc, a), run: function (it) { setAngle(items.indexOf(it), a); } }; }),
          primary: { label: function (n) { return AC.t('angles.applyN', { n: n }); }, onApply: applyReview }
        });
        if (!list) return;
        var head = list.el.querySelector('.rv-head');
        var title = head && head.querySelector('.rv-title');
        if (title) {
          var legend = ui.h('div', { class: 'legend angles-legend', html: angles.map(function (a) { return '<span><i class="angles-lg is-' + a + '"></i>' + U.esc(angleName(doc, a)) + '</span>'; }).join('') });
          title.parentNode.insertBefore(legend, title.nextSibling);
          title.parentNode.insertBefore(lane.el, title.nextSibling);
          head.parentNode.insertBefore(pv.el, head.nextSibling);
        }
        // the active row is only exposed through aria-activedescendant ("rvN-<index>")
        obs = new MutationObserver(sync);
        obs.observe(list.box, { attributes: true, attributeFilter: ['aria-activedescendant'] });
        list.box.addEventListener('keydown', function (e) {
          if (e.ctrlKey || e.altKey || e.metaKey) return;
          var k = { '1': angles[0], '2': angles[1], '3': angles[2] }[e.key];
          if (!k) return;
          var i = active(); if (i < 0) return;
          e.preventDefault(); setAngle(i, k);
        });
        sync();
      }
      function active() {
        var id = list && list.box.getAttribute('aria-activedescendant');
        var m = /-(\d+)$/.exec(id || '');
        return m ? Number(m[1]) : -1;
      }
      function setAngle(i, a) {
        var it = doc && doc.items[i];
        if (!it || !it.opts || !it.opts[a]) return;
        var o = it.opts[a];
        it.angle = a; it.scale = o.scale; it.pos = o.pos; it.label = angleName(doc, a);
        it.picked = true; it.touched = true;
        if (!it.on) it.on = true;
        list.refresh();
        if (pv) pv.show(i, it, false);
      }

      /* ---------------- Apply */
      function applyReview(items, d, path) {
        ctx.run({ action: 'apply', review: path, title: AC.t('angles.runApply'), params: {}, onResult: function (data) { applyPlan(data.plan, data); } });
      }
      function applyPlan(plan, data) {
        if (!plan || plan.kind !== 'keyframes' || plan.tool !== 'angles') { ctx.error(U.err('BAD_PLAN', AC.t('angles.errBadPlan'))); return; }
        var splits = plan.splits || [], items = plan.items || [];
        var stages = [{ id: 'clone', label: AC.t('angles.st.clone'), w: 0.1 }];
        if (splits.length) stages.push({ id: 'split', label: AC.t('angles.st.split', { n: splits.length }), w: 0.4 });
        stages.push({ id: 'angles', label: AC.t('angles.st.angles', { n: items.length }), w: splits.length ? 0.5 : 0.9 });
        var task = new AC.Task({ tool: 'angles', action: 'host', title: ctx.def.title, label: AC.t('angles.taskLabel'), kind: 'host', stages: stages });
        AC.history.track(task);
        AC.seq.hold(task.promise);
        var cancelled = false;
        task._cancel = function () { cancelled = true; };
        ctx.track(task, { title: AC.t('angles.trackTitle'), note: AC.t('angles.trackNote'),
                          onDone: function (r) { showResult(r, task); }, onRetry: function () { applyPlan(plan, data); } });
        var srcId = (plan.seq && plan.seq.id) || AC.seq.locked() || '';
        var srcName = (plan.seq && plan.seq.name) || '';
        task.seqName = srcName;
        var clone = null, tot = { set: 0, keyed: 0, nomotion: 0, matched: 0, bad: 0, items: items.length };
        function stop() {
          if (cancelled) throw U.err('CANCELLED', AC.t('angles.errCancelled', { name: clone ? clone.name : '' }));
        }
        // `max` > 0 = adaptive size: aim for ~1,2 s per host call so Premiere stays usable between calls (live
        // 26.2.2: QE razor slows down as clips pile up, 40 razors took 3,5 s on a 290-clip clone).
        function chunks(list, size, fn, stageId, max) {
          var at = 0;
          function next() {
            if (at >= list.length) return Promise.resolve();
            stop();
            var part = list.slice(at, at + size), t0 = Date.now();
            return fn(part).then(function () {
              at += part.length; task.progress(at / list.length * 100);
              if (max) size = Math.max(8, Math.min(max, Math.round(part.length * 1200 / Math.max(50, Date.now() - t0))));
              return next();
            });
          }
          task.stage({ id: stageId });
          return next().then(function () { task.stageDone(stageId); });
        }
        task.stage({ id: 'clone' });
        var base = String(srcName || 'Sequence').replace(AC.brand.suffixRe, '');
        AC.host.exec('bac_cloneSeq', [base + AC.brand.suffix, srcId], { json: true, timeout: 60000 }).then(function (c) {
          clone = c; task.stageDone('clone');
          if (!splits.length) return null;
          return chunks(splits, SPLIT_CHUNK, function (part) {
            return AC.host.exec('bac_angles_split', [c.id, part, plan.track], { json: true, timeout: 180000 });
          }, 'split', SPLIT_MAX);
        }).then(function () {
          return chunks(items, APPLY_CHUNK, function (part) {
            return AC.host.exec('bac_angles_apply', [clone.id, plan.track, part], { json: true, timeout: 120000 }).then(function (r) {
              ['set', 'keyed', 'nomotion', 'matched', 'bad'].forEach(function (k) { tot[k] += (r && r[k]) || 0; });
            });
          }, 'angles');
        }).then(function () {
          return AC.host.json('bac_openSequence', clone.id).catch(function () { return null; });
        }).then(function () {
          task.summary = data && data.summary ? data.summary : AC.t('angles.summary', { n: items.length });
          task.done({ clone: clone, tot: tot, plan: plan, by: (data && data.by_angle) || {} });
        }).catch(function (e) {
          if (!(e && e.code === 'CANCELLED' && clone)) { task.fail(e); return; }
          // Cancelled between chunks: the half-made clone (ours, this session) is removed and the source reopened,
          // so a cancel leaves the project as it was. If that fails the message names the partial clone.
          AC.host.json('bac_deleteSequence', clone.id).then(function () {
            return AC.host.json('bac_openSequence', srcId).catch(function () { return null; });
          }).then(function () { task.fail(U.err('CANCELLED', AC.t('angles.cancelledClean'))); },
                  function () { task.fail(e); });
        });
      }
      function showResult(r, task) {
        var c = r.clone, tot = r.tot, plan = r.plan, by = r.by;
        var statsList = ANG.filter(function (a) { return by[a]; }).map(function (a) { return { label: NAME(a), value: String(by[a]) }; });
        if (plan.splits && plan.splits.length) statsList.push({ label: AC.t('angles.newCut'), value: String(plan.splits.length), accent: true });
        var lane2 = angleLane({});
        var shown = (doc && doc.items) ? doc.items : plan.items.map(function (x) { return { t0: x.t0, t1: x.t1, angle: x.angle, on: true }; });
        setTimeout(function () { lane2.set({ items: shown, total: (doc && doc.duration) || 0, active: -1 }); }, 0);
        var warn = null;
        if (tot.keyed) warn = { title: AC.t('angles.warnSkipped', { n: tot.keyed }), text: AC.t('angles.warnKeyed') };
        else if (tot.bad || tot.matched < tot.items) warn = { title: AC.t('angles.warnPartial'), text: AC.t('angles.warnPartialText', { n: tot.items - tot.matched, bad: tot.bad }) };
        ctx.result({
          title: AC.t('angles.resTitle'),
          sub: AC.t('angles.resSub', { n: tot.set, time: task.elapsed() >= 1 ? AC.t('angles.resTime', { dur: U.dtk(task.elapsed()) }) : '' }),
          statsList: statsList, extra: lane2.el, warn: warn,
          original: { id: c.origId, name: c.origName },
          onEdit: function () { ctx.go('review'); },
          onDelete: function () {
            AC.host.json('bac_deleteSequence', c.id).then(function () { ui.toast(AC.t('angles.deleted', { name: c.name }), 'trash'); ctx.back(); },
              function (e) { ui.toast(U.errMsg(e), { kind: 'err' }); });
          },
          next: ctx.def.next
        });
      }
    },
    onShow: function (ctx) { if (ctx._paint) ctx._paint(); },
    onSeq: function (lite, ctx) { if (ctx._paint && ctx.pane() === 'main') ctx._paint(); }
  });
})();
