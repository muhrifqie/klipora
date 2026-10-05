/* zoom.js: Auto Zoom (tool id "zoom"). Punch-in zooms planned by the engine (engine/ac/tools/zoom.py) and applied as
   Motion Scale/Position keyframes per clip on a clone "<seq> (Zoom)".
   Flow: main pane (mode, intensity, frequency, animation, ignore zones) -> engine "analyze" -> Review (zoom moments with
   a thumbnail per row, big preview of the active moment, optional video preview render through the worker) ->
   engine "apply" (keyframe plan file) -> host bac_zoom_clone + chunked bac_zoom_keys (panel/host/37_zoom.jsx) ->
   result card. Zooming a zoom result again first clears our earlier keys: the base values of every keyed clip are
   recorded in %APPDATA%\Klipora\zoom_applied.json (key = result sequence id) and sent as params.reset.
   Docs: docs/tools/zoom.md. */
(function () {
  'use strict';
  var AC = window.AC, U = AC.util, ui = AC.ui;

  // Labels are translated at render time (a live language switch re-renders the page).
  function modes() {
    return ['screen', 'speech', 'rhythm'].map(function (v) { return { value: v, title: AC.t('zoom.mode.' + v), desc: AC.t('zoom.mode.' + v + 'Desc') }; });
  }
  // Same ranges as engine INTENSITY (zoom min, max).
  var RANGE = {
    screen: { ringan: [1.2, 1.6], sedang: [1.25, 2.2], kuat: [1.4, 2.8] },
    speech: { ringan: [1.08, 1.18], sedang: [1.12, 1.3], kuat: [1.18, 1.45] },
    rhythm: { ringan: [1.1, 1.1], sedang: [1.15, 1.15], kuat: [1.25, 1.25] }
  };
  var PER_MIN = { screen: 6, speech: 4 };
  var RAMP_SEC = { smooth: { screen: 0.6, talk: 0.45 }, fast: { screen: 0.25, talk: 0.2 } };
  var RAMPS = ['smooth', 'fast', 'jump'];
  function zones() {
    return ['taskbar', 'notif', 'browser', 'edges'].map(function (v) { return { value: v, label: AC.t('zoom.zone.' + v), title: AC.t('zoom.zone.' + v + 'Tip') }; });
  }
  var ANCHORS = ['aksi', 'wajah', 'tengah'];   // engine anchor ids
  var KEY_CHUNK = 1500, CLIP_CHUNK = 60;

  function zx(z) { var s = U.dec(z, 2); return s.replace(/,?0+$/, '') + 'x'; }
  function fileUrl(p) { return 'file:///' + encodeURI(String(p).replace(/\\/g, '/')).replace(/#/g, '%23').replace(/\?/g, '%3F'); }
  function recPath() { return AC.sys.join(AC.sys.paths.appData, 'zoom_applied.json'); }
  function loadRec() { var r = AC.sys.readJSON(recPath(), null); return r && typeof r === 'object' ? r : {}; }
  function saveRec(rec) {
    var ids = Object.keys(rec).sort(function (a, b) { return (rec[b].created || 0) - (rec[a].created || 0); });
    ids.slice(40).forEach(function (id) { delete rec[id]; });   // keep the last 40 results
    try { AC.sys.writeJSON(recPath(), rec); } catch (e) { AC.log.error(e, 'zoom record'); }
  }

  /* ---------------- review rows */
  function rowCtx(it) {
    var th = it.thumb ? '<img src="' + U.esc(fileUrl(it.thumb)) + '" alt="" loading="lazy">' : '';
    // the spoken words are user content (any language): data-i18n-skip keeps them out of the UI language checks
    var said = it.ctx ? '<span data-i18n-skip>' + U.esc(typeof it.ctx === 'string' ? it.ctx : '') + '</span>' : '<i>' + U.esc(AC.t('zoom.noWords')) + '</i>';
    return '<span class="zoom-th' + (th ? '' : ' is-empty') + '">' + th + '</span>' +
      '<span class="zoom-tx"><mark>' + U.esc(it.z ? zx(it.z) : (it.label || 'Zoom')) + '</mark> ' + said + '</span>';
  }
  // Screen moments are all "Aksi layar" (and say "Keluar instan" in their note): tag only what varies.
  function rowTags(it) {
    var h = '';
    if (it.anchor && it.anchor !== 'aksi') h += '<span class="tag tag-line">' + U.esc(ANCHORS.indexOf(it.anchor) >= 0 ? AC.t('zoom.anchor.' + it.anchor) : it.anchor) + '</span>';
    if (it.why && it.why.join && it.why.join(' ').indexOf('AI:') >= 0) h += '<span class="tag tag-ai">AI</span>';
    return h;
  }

  /* ---------------- big preview of the active moment (+ video render) */
  function previewBox(o) {
    var img = ui.h('img', { class: 'zoom-pv-img', alt: '' });
    var vid = null;
    var frame = ui.h('div', { class: 'zoom-pv-frame' }, [img]);
    var msg = ui.h('p', { class: 'zoom-pv-msg', hidden: true });
    var info = ui.h('div', { class: 'zoom-pv-info' });
    var renderBtn = ui.button({ label: AC.t('zoom.pvRender'), icon: 'play', small: true, title: AC.t('zoom.pvRenderTip'), onClick: function () { if (cur) render(cur.it); } });
    var openBtn = ui.button({ label: AC.t('zoom.pvOpen'), icon: 'ext', small: true, kind: 'ghost', onClick: function () { if (lastVideo) AC.sys.openFolder(lastVideo); } });
    openBtn.hidden = true;
    var row = ui.h('div', { class: 'zoom-pv-row' }, [info, ui.h('div', { class: 'zoom-pv-btns' }, [renderBtn, openBtn])]);
    var toggle = ui.h('button', { class: 'linkbtn', type: 'button' });
    var body = ui.h('div', { class: 'zoom-pv-body' }, [frame, row, msg]);
    var el = ui.h('div', { class: 'zoom-pv' }, [ui.h('div', { class: 'zoom-pv-h' }, [ui.h('span', { text: AC.t('zoom.pvTitle') }), toggle]), body]);
    var open = AC.store.get('zoom.preview', true), cur = null, job = null, lastVideo = null;
    function paintToggle() { toggle.textContent = AC.t(open ? 'zoom.pvHide' : 'zoom.pvShow'); body.hidden = !open; el.classList.toggle('is-closed', !open); }
    toggle.addEventListener('click', function () { open = !open; AC.store.set('zoom.preview', open); paintToggle(); });
    paintToggle();
    var aspect = o.doc.width && o.doc.height ? o.doc.width / o.doc.height : 2.3875;
    frame.style.aspectRatio = String(Math.round(aspect * 1000) / 1000);
    frame.style.maxWidth = 'calc(var(--zoom-pv-h) * ' + (Math.round(aspect * 1000) / 1000) + ')';
    img.addEventListener('error', function () { if (img.getAttribute('src')) { msg.textContent = AC.t('zoom.pvImgErr'); msg.hidden = false; } });
    img.addEventListener('load', function () { msg.hidden = true; });
    function dropVideo() { if (vid) { vid.pause(); vid.removeAttribute('src'); vid.load(); vid.remove(); vid = null; } img.hidden = false; }
    function show(i, it) {
      if (!it) return;
      if (cur && cur.it === it) return;
      cur = { i: i, it: it };
      dropVideo(); openBtn.hidden = true; msg.hidden = true;
      if (it.thumb) img.src = fileUrl(it.thumb); else { img.removeAttribute('src'); msg.textContent = AC.t('zoom.pvNoImg'); msg.hidden = false; }
      info.innerHTML = '<b>' + U.esc(zx(it.z)) + '</b> <span>' + AC.i18n.html('zoom.pvSpan', { t0: U.tcode(it.t0), t1: U.tcode(it.t1) }) + '</span>' +
        (it.on ? '' : '<span class="zoom-off">' + U.esc(AC.t('zoom.pvSkipped')) + '</span>');
      info.title = AC.t(it.end === 'cut' ? (it.ease_out === 'lin' ? 'zoom.pvOutJump' : 'zoom.pvOutCut') : 'zoom.pvOutSmooth');
      renderBtn.disabled = false;
    }
    function render(it) {
      if (job && job.isActive()) job.cancel();
      try { o.save(); } catch (e) { /* preview still works with the saved file */ }
      var t0 = Math.max(0, it.t0 - 1), t1 = Math.min(o.doc.duration || it.t1 + 1, it.t1 + 1);
      renderBtn.disabled = true; msg.hidden = false; msg.textContent = AC.t('zoom.pvRendering');
      var j = job = AC.engine.worker({ tool: 'zoom', action: 'preview', review: o.file, record: false, title: AC.t('zoom.pvTitle'),
                                        params: { ids: [it.id], t0: t0, t1: t1 } });
      j.on('progress', function () { if (job === j) msg.textContent = AC.t('zoom.pvRenderingPct', { pct: Math.round(j.pct || 0) }); });
      j.then(function (r) {
        if (job !== j || !cur || cur.it !== it) return;
        renderBtn.disabled = false; lastVideo = r.path; openBtn.hidden = false;
        msg.textContent = AC.t('zoom.pvVideoInfo', { dur: U.dtk(r.t1 - r.t0), size: r.size[0] + 'x' + r.size[1] });
        dropVideo();
        vid = ui.h('video', { class: 'zoom-pv-video', controls: true, playsinline: true });
        vid.muted = true; vid.autoplay = true; vid.loop = true;
        vid.addEventListener('error', function () { msg.textContent = AC.t('zoom.pvVideoErr'); msg.hidden = false; dropVideo(); });
        vid.src = fileUrl(r.path);
        img.hidden = true;
        frame.appendChild(vid);
      }, function (e) {
        if (job !== j) return;
        renderBtn.disabled = false;
        msg.textContent = e && e.code === 'CANCELLED' ? AC.t('zoom.pvCancelled') : AC.t('zoom.pvFailed', { msg: U.errMsg(e) });
        msg.hidden = false;
      });
    }
    return { el: el, show: show, render: render, destroy: function () { if (job && job.isActive()) job.cancel(); dropVideo(); } };
  }

  AC.tools.register({
    id: 'zoom', group: 'kamera', order: 3,
    icon: 'zoom',
    badges: ['baru'],
    text: function (t) {
      return { title: t('zoom.title'), tab: t('zoom.tab'), desc: t('zoom.desc'), lead: t('zoom.lead'), cta: t('zoom.cta'),
               review: { unit: AC.i18n.raw('zoom.unit') },
               next: [{ tool: 'captions', reason: t('zoom.nextCaptions') }, { tool: 'resize', reason: t('zoom.nextResize') }] };
    },
    source: { tags: true },
    defaults: { mode: 'screen', intensity: 'sedang', perMin: { screen: 6, speech: 4 }, ramp: 'smooth', zones: ['taskbar', 'notif'],
                custom: null, rampS: null, ai: false, track: 'auto' },

    render: function (el, ctx) {
      var st = ctx.state;
      if (!st.perMin || typeof st.perMin !== 'object') st.perMin = { screen: 6, speech: 4 };
      var talk = function () { return st.mode !== 'screen'; };

      /* ---------- Mode */
      var secMode = ui.section({ title: AC.t('zoom.secMode') });
      var mode = ui.radioCards({ ariaLabel: AC.t('zoom.modeAria'), value: st.mode, options: modes(), onChange: function (v) {
        st.mode = v; st.custom = null; ctx.save(); paint();
      } });
      secMode.add(mode);
      el.appendChild(secMode.el);

      /* ---------- Gaya zoom */
      var secStyle = ui.section({ title: AC.t('zoom.secStyle') });
      var intHelp = ui.h('p', { class: 'help zoom-help' });
      var intensity = ui.segmented({ ariaLabel: AC.t('zoom.intensityAria'), value: st.intensity, options: ['ringan', 'sedang', 'kuat'].map(function (v) { return [v, AC.t('zoom.int.' + v)]; }),
        onChange: function (v) { st.intensity = v; st.custom = null; ctx.save(); paint(); } });
      var freq = ui.slider({ label: AC.t('zoom.freq'), min: 1, max: 12, step: 1, value: st.perMin[st.mode] || PER_MIN.screen, decimals: 0,
        format: function (v) { return AC.t('zoom.freqFmt', { n: v }); }, help: AC.t('zoom.freqHelp'),
        onInput: function (v) { st.perMin[st.mode === 'rhythm' ? 'screen' : st.mode] = v; ctx.save(); } });
      var rampHelp = ui.h('p', { class: 'help zoom-help' });
      var ramp = ui.segmented({ ariaLabel: AC.t('zoom.rampAria'), value: st.ramp, options: RAMPS.map(function (v) { return [v, AC.t('zoom.ramp.' + v)]; }),
        onChange: function (v) { st.ramp = v; st.rampS = null; ctx.save(); paint(); } });
      secStyle.add([ui.field({ label: AC.t('zoom.strength'), control: intensity.el }), intHelp, freq, ui.field({ label: AC.t('zoom.anim'), control: ramp.el }), rampHelp]);
      el.appendChild(secStyle.el);

      /* ---------- Zona abaikan (screen) */
      var secZones = ui.section({ title: AC.t('zoom.zones'), help: AC.t('zoom.zonesHelp') });
      var zoneChips = ui.chips({ multi: true, ariaLabel: AC.t('zoom.zones'), value: st.zones, options: zones(), onChange: function (v) { st.zones = v; ctx.save(); } });
      secZones.add(zoneChips);
      el.appendChild(secZones.el);

      /* ---------- Pengaturan lanjutan */
      var adv = ui.advanced({ title: AC.t('common.advanced'), key: 'zoom' });
      function custom() { st.custom = { zmin: zmin.get(), zmax: Math.max(zmin.get(), zmax.get()) }; ctx.save(); paintRange(); }
      var zmin = ui.slider({ label: AC.t('zoom.zmin'), min: 1.05, max: 3, step: 0.01, value: 1.25, format: function (v) { return zx(v); }, onInput: custom });
      var zmax = ui.slider({ label: AC.t('zoom.zmax'), min: 1.05, max: 3, step: 0.01, value: 2.2, format: function (v) { return zx(v); },
        help: AC.t('zoom.zmaxHelp'), onInput: custom });
      var rampS = ui.slider({ label: AC.t('zoom.rampS'), min: 0.15, max: 1.2, step: 0.05, value: 0.6, unit: ' ' + AC.t('unit.sec'), decimals: 2,
        help: AC.t('zoom.rampSHelp'), onInput: function (v) { st.rampS = v; ctx.save(); } });
      var ai = ui.switch({ label: AC.t('zoom.ai'), badge: ui.badge('ai'), checked: !!st.ai,
        help: AC.t('zoom.aiHelp'), onChange: function (v) { st.ai = v; ctx.save(); } });
      var track = ui.select({ ariaLabel: AC.t('zoom.trackAria'), options: [['auto', AC.t('zoom.trackAuto')]], value: st.track,
        onChange: function (v) { st.track = v; ctx.save(); } });
      var trackField = ui.field({ label: AC.t('zoom.trackLabel'), help: AC.t('zoom.trackHelp'), control: track.el });
      adv.add([zmin, zmax, rampS, ai, trackField]);
      el.appendChild(adv.el);

      function range() {
        var r = RANGE[st.mode][st.intensity] || RANGE[st.mode].sedang;
        return st.custom ? [st.custom.zmin, st.custom.zmax] : r;
      }
      function paintRange() {
        var r = range();
        intensity.set(st.custom ? null : st.intensity, true);
        adv.setAside(st.custom ? AC.t('common.custom') : '');
        intHelp.textContent = (st.custom ? AC.t('common.custom') + ': ' : '') + (r[0] === r[1] ? AC.t('zoom.rangeOne', { z: zx(r[1]) }) : AC.t('zoom.rangeTwo', { a: zx(r[0]), b: zx(r[1]) }));
      }
      function paint() {
        mode.set(st.mode, true);
        var r = range();
        zmin.set(r[0], true); zmax.set(r[1], true);
        paintRange();
        freq.el.hidden = st.mode === 'rhythm';
        freq.set(st.perMin[st.mode] || PER_MIN[st.mode] || 6, true);
        secZones.el.hidden = st.mode !== 'screen';
        ai.el.hidden = st.mode !== 'speech';
        ramp.set(st.ramp, true);
        rampHelp.textContent = RAMPS.indexOf(st.ramp) >= 0 ? AC.t('zoom.rampHint.' + st.ramp) : '';
        rampS.el.hidden = st.ramp === 'jump';
        rampS.set(st.rampS || RAMP_SEC[st.ramp === 'jump' ? 'smooth' : st.ramp][talk() ? 'talk' : 'screen'], true);
        ctx.setPrimary({ label: ctx.def.cta });
      }
      ctx._paint = paint;
      ctx._tracks = function (n) {
        var opts = [['auto', AC.t('zoom.trackAuto')]];
        for (var i = 0; i < n; i++) opts.push([String(i), 'V' + (i + 1)]);
        track.setOptions(opts);
        if (st.track !== 'auto' && Number(st.track) >= n) { st.track = 'auto'; track.set('auto', true); }
      };
      var lite = AC.seq.peek();
      if (lite && lite.video) ctx._tracks(lite.video.length);

      ctx.dock({ primary: { label: ctx.def.cta, icon: 'zoom', onClick: run } });
      paint();
      cacheTag();

      // "Analisis layar tersimpan" / estimated first-run time on the source card (screen mode only).
      function cacheTag() {
        if (!ctx.source || !AC.sys.node) return;
        ctx.seq().then(function (s) {
          if (!s || st.mode !== 'screen') { ctx.source.setTags(''); return; }
          var tr = (s.video || []).filter(function (t) { return (t.clips || []).length; })[0];
          var paths = {}, est = 0;
          ((tr && tr.clips) || []).forEach(function (c) { if (c.path) paths[c.path] = Math.max(paths[c.path] || 0, c.out || 0); });
          var keys = Object.keys(paths);
          if (!keys.length) { ctx.source.setTags(''); return; }
          var cached = keys.every(function (p) { return AC.sys.exists(AC.sys.join(AC.sys.dirname(p), AC.sys.stem(p) + '_zoomact.npz')); });
          keys.forEach(function (p) { est += paths[p] * 0.045; });
          ctx.source.setTags(cached ? ui.tag(AC.t('zoom.tagCached'), 'ok', 'check')
            : (est > 8 ? ui.tag(AC.t('zoom.tagFirst', { dur: U.dtk(Math.round(est)) }), 'line', 'clock') : ''));
        }, function () { /* no sequence: the source card says so */ });
      }
      ctx._cacheTag = U.debounce(cacheTag, 300);

      function params() {
        var p = { mode: st.mode, intensity: st.intensity, ramp: st.ramp };
        if (st.custom) { p.zmin = st.custom.zmin; p.zmax = st.custom.zmax; }
        if (st.mode !== 'rhythm') p.per_min = st.perMin[st.mode] || PER_MIN[st.mode];
        if (st.rampS && st.ramp !== 'jump') p.ramp_s = st.rampS;
        if (st.mode === 'screen') p.ignore = st.zones.slice();
        if (st.mode === 'speech' && st.ai) p.ai = true;
        if (st.track !== 'auto') p.track = Number(st.track);
        return p;
      }
      function run() {
        var title = AC.t(st.mode === 'screen' ? 'zoom.runScreen' : st.mode === 'speech' ? 'zoom.runSpeech' : 'zoom.runRhythm');
        ctx.run({ action: 'analyze', title: title, params: params(), onResult: showReview });
      }

      /* ---------------- Tinjau */
      var list = null, pv = null, obs = null, doc = null, file = null;
      function showReview(data) {
        file = data.review;
        try { doc = ui.loadReview(file); } catch (e) { ctx.error(e); return; }
        if (!doc.items.length) {
          var why = AC.t('zoom.empty.' + (/^(screen|speech|rhythm)$/.test(doc.mode) ? doc.mode : 'screen'));
          ctx.result({ title: AC.t('zoom.emptyTitle'), sub: why,
                       primary: { label: AC.t('zoom.changeSettings'), kbd: false, onClick: function () { ctx.back(); } } });
          return;
        }
        if (pv) pv.destroy();
        if (obs) obs.disconnect();
        pv = previewBox({ doc: doc, file: file, save: function () { if (list) list.save(file); } });
        var filters = [{ id: 'strong', label: AC.t('zoom.fStrong'), test: function (it) { return it.z >= 1.8; } }];
        if (doc.mode === 'screen') filters.push({ id: 'cut', label: AC.t('zoom.fCut'), test: function (it) { return it.end === 'cut'; } });
        if (doc.mode === 'speech') filters.push({ id: 'ai', label: 'AI', icon: 'spark', test: function (it) { return (it.why || []).join(' ').indexOf('AI:') >= 0; } });
        list = ctx.review({
          doc: doc, file: file, unit: AC.i18n.raw('zoom.unit'), verbOn: AC.t('zoom.verbOn'), verbOff: AC.t('zoom.verbOff'), markerTag: '[Klipora-ZOOM]',
          markerName: function (it) { return AC.t('zoom.markerName', { z: zx(it.z) }); },
          title: function (n, all) { sync(); return AC.i18n.html('zoom.rvTitle', { n: n }) + '<small>' + AC.i18n.html('zoom.rvOf', { n: all }) + '</small>'; },
          ctx: rowCtx, tags: rowTags, filters: filters,
          toggleText: function (it) { return AC.t(it.on ? 'zoom.toggleSkip' : 'zoom.toggleUse'); },
          legendExtra: function (n, all, sec) { return '<span>' + AC.i18n.html('zoom.legendZoomed', { dur: U.dtk(sec) }) + '</span>'; },
          rowActions: [{ label: AC.t('zoom.rowPreview'), icon: 'play', run: function (it) { pv.render(it); } }],
          primary: { label: function (n) { return n ? AC.t('zoom.applyN', { n: n }) : AC.t('zoom.pickFirst'); }, onApply: applyReview }
        });
        if (!list) return;
        list.el.classList.add('zoom-rv');
        var head = list.el.querySelector('.rv-head');
        if (head) head.parentNode.insertBefore(pv.el, head.nextSibling);
        // the active row is only exposed through aria-activedescendant ("rvN-<index>")
        obs = new MutationObserver(sync);
        obs.observe(list.box, { attributes: true, attributeFilter: ['aria-activedescendant'] });
        if (data.warnings && data.warnings.length) ui.toast(data.warnings[0], 'alert');
        sync();
      }
      var sched = 0;
      function sync() {
        cancelAnimationFrame(sched);
        sched = requestAnimationFrame(function () {
          if (!pv || !doc || !doc.items.length) return;
          var id = list && list.box.getAttribute('aria-activedescendant'), m = /-(\d+)$/.exec(id || '');
          var i = m ? Number(m[1]) : 0;
          pv.show(i, doc.items[i]);
        });
      }

      /* ---------------- Terapkan */
      function applyReview(items, d, path) {
        var rec = loadRec(), prev = d.seq && rec[d.seq.id];
        ctx.run({ action: 'apply', review: path, title: AC.t('zoom.runApply'), params: { reset: prev ? prev.clips : [] },
                  onResult: function (data) { applyPlan(data.plan, data); } });
      }
      function chunksOf(clips) {
        var out = [], a = 0, keys = 0;
        for (var i = 0; i < clips.length; i++) {
          keys += (clips[i].keys || []).length;
          if (i + 1 - a >= CLIP_CHUNK || keys >= KEY_CHUNK) { out.push([a, i + 1]); a = i + 1; keys = 0; }
        }
        if (a < clips.length) out.push([a, clips.length]);
        return out;
      }
      function applyPlan(plan, data) {
        if (!plan || plan.kind !== 'keyframes' || !plan.path) { ctx.error(U.err('BAD_PLAN', AC.t('zoom.errBadPlan'))); return; }
        var full = AC.sys.readJSON(plan.path, null);
        if (!full || !full.clips) { ctx.error(U.err('BAD_PLAN', AC.t('zoom.errPlanRead'), plan.path)); return; }
        var parts = chunksOf(full.clips);
        var task = new AC.Task({ tool: 'zoom', action: 'host', title: ctx.def.title, label: AC.t('zoom.taskLabel'), kind: 'host', stages: [
          { id: 'clone', label: AC.t('zoom.stClone'), w: 0.1 },
          { id: 'keys', label: AC.t('zoom.stKeys', { keys: U.int(plan.keys), n: plan.clips }), w: 0.85 },
          { id: 'open', label: AC.t('zoom.stOpen'), w: 0.05 }] });
        AC.history.track(task);
        AC.seq.hold(task.promise);
        var cancelled = false, clone = null;
        task._cancel = function () { cancelled = true; };
        task.seqName = (plan.seq && plan.seq.name) || '';
        ctx.track(task, { title: AC.t('zoom.trackTitle'), note: AC.t('zoom.trackNote'),
                          onDone: function (r) { showResult(r, task); }, onRetry: function () { applyPlan(plan, data); } });
        var tot = { done: 0, keys: 0, reset: 0, skipped: [], base: [] };
        task.stage({ id: 'clone' });
        AC.host.exec('bac_zoom_clone', [(plan.seq && plan.seq.id) || ''], { json: true, timeout: 60000 }).then(function (c) {
          clone = c; task.stageDone('clone');
          task.stage({ id: 'keys' });
          var k = 0;
          function next() {
            if (cancelled) throw U.err('CANCELLED', AC.t('zoom.errCancelled', { name: clone.name }), AC.t('zoom.errCancelledHint'));
            if (k >= parts.length) return Promise.resolve();
            var pr = parts[k];
            return AC.host.exec('bac_zoom_keys', [clone.id, plan.path, pr[0], pr[1]], { json: true, timeout: 120000 }).then(function (r) {
              tot.done += r.done || 0; tot.keys += r.keys || 0; tot.reset += r.reset || 0;
              tot.skipped = tot.skipped.concat(r.skipped || []); tot.base = tot.base.concat(r.base || []);
              k++; task.progress(k / parts.length * 100);
              return next();
            });
          }
          return next();
        }).then(function () {
          task.stageDone('keys');
          task.stage({ id: 'open' });
          return AC.host.json('bac_openSequence', clone.id).catch(function () { return null; });
        }).then(function () {
          var rec = loadRec();
          if (tot.base.length) rec[clone.id] = { name: clone.name, from: clone.origId, created: Date.now(), clips: tot.base };
          saveRec(rec);
          task.summary = AC.t('zoom.summary', { n: plan.events, keys: U.int(tot.keys) });
          task.done({ clone: clone, tot: tot, plan: plan });
        }).catch(function (e) { task.fail(e); });
      }
      function showResult(r, task) {
        var c = r.clone, tot = r.tot, plan = r.plan;
        var on = doc ? doc.items.filter(function (it) { return it.on; }) : [];
        var why = {};
        tot.skipped.forEach(function (s) { why[s.why] = (why[s.why] || 0) + 1; });
        var warn = null;
        if (why.keys) warn = { title: AC.t('zoom.warnSkipped', { n: why.keys }), text: AC.t('zoom.warnKeys') };
        else if (why.uniform) warn = { title: AC.t('zoom.warnSkipped', { n: why.uniform }), text: AC.t('zoom.warnUniform') };
        else if (tot.skipped.length) warn = { title: AC.t('zoom.warnMissingTitle', { n: tot.skipped.length }), text: AC.t('zoom.warnMissing') };
        ctx.result({
          title: AC.t('zoom.resTitle'),
          sub: AC.t('zoom.resSub', { n: plan.events, clips: tot.done, time: task.elapsed() >= 1 ? AC.t('zoom.resTime', { dur: U.dtk(task.elapsed()) }) : '' }),
          statsList: [{ label: AC.t('zoom.statZoom'), value: String(plan.events), accent: true }, { label: AC.t('zoom.statClip'), value: String(tot.done) }, { label: AC.t('zoom.statKeys'), value: U.int(tot.keys) }],
          ribbon: doc ? { total: doc.duration, spans: on.map(function (it) { return [it.t0, it.t1]; }) } : null,
          legend: [AC.t('zoom.legendWide'), AC.t('zoom.legendMoments', { n: on.length }) + (tot.reset ? AC.t('zoom.legendReset', { n: tot.reset }) : '')],
          warn: warn,
          original: { id: c.origId, name: c.origName },
          onEdit: function () { ctx.go('review'); },
          onDelete: function () {
            AC.host.json('bac_deleteSequence', c.id).then(function () {
              var rec = loadRec(); delete rec[c.id]; saveRec(rec);
              ui.toast(AC.t('zoom.deleted', { name: c.name }), 'trash'); ctx.back();
            }, function (e) { ui.toast(U.errMsg(e), { kind: 'err' }); });
          },
          next: ctx.def.next
        });
      }
    },
    onShow: function (ctx) { if (ctx._paint) ctx._paint(); if (ctx._cacheTag) ctx._cacheTag(); },
    onSeq: function (lite, ctx) {
      if (lite && lite.video && ctx._tracks) ctx._tracks(lite.video.length);
      if (ctx._cacheTag && ctx.pane() === 'main') ctx._cacheTag();
    }
  });
})();
