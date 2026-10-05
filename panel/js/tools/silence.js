/* silence.js: Potong Silence. Live waveform + counts from the engine "preview" (floored 10 ms envelope of the
   whole sequence), a JS port of the engine detector (engine/ac/tools/silence.py plan(): same integer threshold,
   4 dB hysteresis, word guard, min silence / min talk, asymmetric pads, frame snap) so sliders and the draggable
   threshold line update instantly and match the engine count exactly. "Deteksi" runs the engine analyze ->
   Tinjau list -> engine apply -> plan: remove (clone + ripple), markers [Klipora-SIL] or mute (clone + volume keys,
   host/30_silence.jsx). AC.silence exposes the detector for tests. API: docs/PANEL_API.md, docs/tools/silence.md.
   Texts: locale keys silence.* (AC.t at render time, never at load time). */
(function () {
  'use strict';
  var AC = window.AC, U = AC.util, ui = AC.ui;
  var HOP = 0.01, TAG = '[Klipora-SIL]', MUTE_CHUNK = 40;     // old projects: [AC-SIL] (cleared too, U.hasTag)
  function L(k, v) { return AC.t(k, v); }
  var ALGO = { hyst: 4, guard_below: 6, guard_dilate: 3, min_cut: 0.05 };   // overwritten by the engine preview

  /* ================================================================ detector (mirror of silence.py plan()) */
  var D = AC.silence = {};
  D.fidx = function (t) { return Math.floor(t / HOP + 1e-9); };
  D.nframes = function (sec) { return Math.floor(sec / HOP + 0.5); };
  D.r4 = function (v) { return Math.round(Number(v) * 1e4) / 1e4; };
  // base64 uint8 (dB + 90) -> Int16Array of whole dB
  D.decode = function (b64, floor) {
    var s = atob(b64 || ''), n = s.length, e = new Int16Array(n), f = floor === undefined ? -90 : floor;
    for (var i = 0; i < n; i++) e[i] = s.charCodeAt(i) + f;
    return e;
  };
  function spanMask(spans, n) {
    var m = new Uint8Array(n);
    (spans || []).forEach(function (r) {
      var i = Math.max(0, D.fidx(r[0])), j = Math.min(n, D.fidx(r[1]) + 1);
      for (var k = i; k < j; k++) m[k] = 1;
    });
    return m;
  }
  function gate(env, thr, hyst) {
    var n = env.length, on = new Uint8Array(n), lo = thr - hyst, i = 0;
    while (i < n) {
      if (env[i] < lo) { i++; continue; }
      var a = i, hard = false;
      while (i < n && env[i] >= lo) { if (env[i] >= thr) hard = true; i++; }
      if (hard) for (var k = a; k < i; k++) on[k] = 1;
    }
    return on;
  }
  function dilate(m, k) {
    var n = m.length, out = new Uint8Array(n);
    for (var i = 0; i < n; i++) {
      if (!m[i]) continue;
      for (var j = Math.max(0, i - k); j <= Math.min(n - 1, i + k); j++) out[j] = 1;
    }
    return out;
  }
  // -> {on, forced}: sound frames and forced frames (guarded word frames | protected spans)
  D.mask = function (env, thr, words, protect, guard, algo) {
    algo = algo || ALGO;
    var n = env.length, on = gate(env, thr, algo.hyst), forced = new Uint8Array(n), i;
    if (guard && words && words.length) {
      var w = spanMask(words, n), lo = thr - algo.guard_below;
      for (i = 0; i < n; i++) w[i] = w[i] && env[i] >= lo ? 1 : 0;
      forced = dilate(w, algo.guard_dilate);
    }
    if (protect && protect.length) { var p = spanMask(protect, n); for (i = 0; i < n; i++) if (p[i]) forced[i] = 1; }
    for (i = 0; i < n; i++) if (forced[i]) on[i] = 1;
    return { on: on, forced: forced };
  };
  function complement(keeps, lo, hi) {
    var out = [], cur = lo;
    keeps.forEach(function (k) { if (k[0] > cur) out.push([cur, k[0]]); cur = Math.max(cur, k[1]); });
    if (hi > cur) out.push([cur, hi]);
    return out;
  }
  function intersect(a, b) {
    var out = [], i = 0, j = 0;
    while (i < a.length && j < b.length) {
      var lo = Math.max(a[i][0], b[j][0]), hi = Math.min(a[i][1], b[j][1]);
      if (hi > lo) out.push([lo, hi]);
      if (a[i][1] < b[j][1]) i++; else j++;
    }
    return out;
  }
  // cfg: {min_silence, pad_before, pad_after, min_talk}; m = D.mask(...). -> {cuts, keeps, talks, sec}
  D.plan = function (m, cfg, dur, fps, scope, algo) {
    algo = algo || ALGO;
    var on = m.on, forced = m.forced, n = on.length, ms = D.nframes(cfg.min_silence), mt = D.nframes(cfg.min_talk);
    var merged = [], i = 0, a;
    while (i < n) {
      if (!on[i]) { i++; continue; }
      a = i; while (i < n && on[i]) i++;
      if (merged.length && a - merged[merged.length - 1][1] < ms) merged[merged.length - 1][1] = i; else merged.push([a, i]);
    }
    var fc = new Int32Array(n + 1);
    for (i = 0; i < n; i++) fc[i + 1] = fc[i] + forced[i];
    var pb = Number(cfg.pad_before), pa = Number(cfg.pad_after), keeps = [], talks = 0;
    merged.forEach(function (r) {
      if (r[1] - r[0] < mt && fc[r[1]] - fc[r[0]] === 0) return;
      talks++;
      var x0 = Math.max(0, r[0] * HOP - pb), x1 = Math.min(dur, r[1] * HOP + pa);
      if (keeps.length && x0 <= keeps[keeps.length - 1][1]) keeps[keeps.length - 1][1] = Math.max(keeps[keeps.length - 1][1], x1);
      else keeps.push([x0, x1]);
    });
    var sc = (scope && scope.length ? scope : [[0, dur]]).map(function (r) { return [Math.max(0, r[0]), Math.min(dur, r[1])]; })
      .filter(function (r) { return r[1] > r[0]; });
    var cuts = [], sec = 0;
    intersect(complement(keeps, 0, dur), sc).forEach(function (r) {
      var x = r[0], y = r[1];
      if (fps) { x = Math.ceil(x * fps - 1e-6) / fps; y = Math.floor(y * fps + 1e-6) / fps; }
      if (y - x >= algo.min_cut - 1e-9) { cuts.push([x, y]); sec += y - x; }
    });
    return { cuts: cuts, keeps: keeps, talks: talks, sec: sec };
  };
  // Whole pipeline on a preview payload (engine `preview` result). cfg keys as above + guard; thr integer dB.
  D.detect = function (data, env, cfg, thr) {
    var algo = data.algo || ALGO;
    var m = D.mask(env, thr, data.words, data.protect, cfg.guard !== false, algo);
    return D.plan(m, cfg, data.dur, data.fps, data.scope, algo);
  };

  /* ================================================================ presets + copy */
  // Labels + hints: silence.preset.<value> / silence.preset.<value>Hint (presetOptions() at render time).
  var PRESETS = [
    { value: 'santai', values: { minSilence: 0.8, padBefore: 0.1, padAfter: 0.25, minTalk: 0.2 } },
    { value: 'natural', values: { minSilence: 0.5, padBefore: 0.06, padAfter: 0.15, minTalk: 0.15 } },
    { value: 'cepat', values: { minSilence: 0.3, padBefore: 0.04, padAfter: 0.1, minTalk: 0.12 } },
    { value: 'kilat', values: { minSilence: 0.2, padBefore: 0.02, padAfter: 0.06, minTalk: 0.1 } }
  ];
  D.presets = PRESETS;
  function presetOptions() {
    return PRESETS.map(function (p) { return { value: p.value, label: L('silence.preset.' + p.value), hint: L('silence.preset.' + p.value + 'Hint'), values: p.values }; });
  }
  // Result modes: dock/review label key silence.cta.<mode> ({n}), "done" verb silence.done.<mode>.
  var MODES = { remove: 1, markers: 1, mute: 1 };
  // What the preview depends on in the lite sequence info (audio clip counts + mute flags: default tracks = unmuted).
  function seqSig(s) {
    return s ? [s.id, s.duration, s.inPoint, s.outPoint, s.selectedCount,
                (s.audio || []).map(function (t) { return t.count + (t.muted ? 'm' : ''); }).join(',')].join('|') : '';
  }
  // Review row context: transcript words are user content (data-i18n-skip), the label is the engine's text.
  function rowCtx(it) {
    var pre = it.ctx && it.ctx.pre, post = it.ctx && it.ctx.post, sk = function (x) { return '<span data-i18n-skip>' + U.esc(x) + '</span>'; };
    return (pre ? sk(pre) : '<i>' + U.esc(AC.t('review.start')) + '</i>') + ' <mark>' + U.esc(it.label || '') + '</mark> ' + (post ? sk(post) : '<i>' + U.esc(AC.t('review.end')) + '</i>');
  }
  function presetOf(v) { for (var i = 0; i < PRESETS.length; i++) if (PRESETS[i].value === v) return PRESETS[i]; return null; }

  AC.tools.register({
    id: 'silence', group: 'potong', order: 1, icon: 'wave',
    badges: [],
    text: function (t) {
      return { title: t('silence.title'), tab: t('silence.tab'), desc: t('silence.desc'), lead: t('silence.lead'), cta: t('silence.cta'),
               review: { unit: t('silence.unit') },
               next: [{ tool: 'fillers', reason: t('silence.nextFillers') }, { tool: 'captions', reason: t('silence.nextCaptions') }] };
    },
    defaults: { preset: 'natural', minSilence: 0.5, padBefore: 0.06, padAfter: 0.15, minTalk: 0.15, offset: 0,
                guard: true, transcribe: false, mode: 'remove', review: true, tracks: null },

    render: function (el, ctx) {
      var st = ctx.state;
      U.assign(st, U.assign(U.copy(ctx.def.defaults), st));
      var pv = { data: null, env: null, mask: null, maskKey: '', token: 0, err: null, live: null, check: null, sig: '' };
      var lastDoc = null, reviewShown = false;
      ctx.silence = pv;

      /* ---------------- Deteksi: waveform + summary + presets */
      var det = ui.section({ title: L('silence.secDetect') });
      var wave = ui.waveform({ env: [], dur: 0, threshold: -53, min: -80, max: -10, thMin: -85, thMax: -10,
        legend: L('silence.reading'), tip: L('silence.waveTip'),
        onThreshold: function (db) {
          if (!pv.data) return;
          st.offset = U.clamp(db - pv.data.base, -20, 20); thr.set(st.offset, true); ctx.save(); recompute();
        } });
      det.add(wave);
      var strip = ui.h('div', { class: 'sum-strip silence-strip', 'aria-live': 'polite' });
      det.add(strip);
      var info = ui.h('div', { class: 'tags silence-info' });
      det.add(info);
      var presets = ui.presets({ options: presetOptions(), value: st.preset, ariaLabel: L('silence.styleAria'), customHint: L('silence.customHint'),
        onChange: function (v, p) {
          if (!p) return;
          st.preset = v; U.assign(st, p.values); ctx.save();
          minS.set(st.minSilence, true); padB.set(st.padBefore, true); padA.set(st.padAfter, true); minT.set(st.minTalk, true);
          adv.setAside(''); recompute();
        } });
      det.add(presets);
      el.appendChild(det.el);

      /* ---------------- Pengaturan lanjutan */
      var adv = ui.advanced({ title: L('common.advanced'), key: 'silence' });
      function manual() {
        st.preset = presets.sync({ minSilence: st.minSilence, padBefore: st.padBefore, padAfter: st.padAfter, minTalk: st.minTalk });
        adv.setAside(st.preset ? '' : L('common.custom')); ctx.save(); recompute();
      }
      var thr = ui.slider({ label: L('silence.thr'), min: -20, max: 20, step: 1, value: st.offset,
        format: function (v) { return pv.data ? L(v === 0 ? 'silence.thrDbAuto' : 'silence.thrDb', { db: String(pv.data.base + v) }) : (v === 0 ? L('silence.auto') : (v > 0 ? '+' : '') + v + ' dB'); },
        help: L('silence.thrHelp'),
        link: { label: L('silence.thrAutoLink'), onClick: function () { st.offset = 0; thr.set(0, true); ctx.save(); recompute(); if (pv.data) ctx.toast(L('silence.thrReset', { db: String(pv.data.base) }), 'gauge'); } },
        onInput: function (v) { st.offset = v; ctx.save(); recompute(); } });
      var sec = ' ' + L('unit.sec');
      var minS = ui.slider({ label: L('silence.minSilence'), min: 0.1, max: 2, step: 0.05, value: st.minSilence, unit: sec, decimals: 2,
        help: L('silence.minSilenceHelp'), onInput: function (v) { st.minSilence = v; manual(); } });
      var padB = ui.stepper({ label: L('silence.padBefore'), min: 0, max: 0.5, step: 0.01, value: st.padBefore, unit: sec, decimals: 2,
        onChange: function (v) { st.padBefore = v; manual(); } });
      var padA = ui.stepper({ label: L('silence.padAfter'), min: 0, max: 0.5, step: 0.01, value: st.padAfter, unit: sec, decimals: 2,
        onChange: function (v) { st.padAfter = v; manual(); } });
      var pads = ui.h('div', { class: 'field-row' }, [padB.el, padA.el]);
      var minT = ui.slider({ label: L('silence.minTalk'), min: 0, max: 0.5, step: 0.01, value: st.minTalk, unit: sec, decimals: 2,
        help: L('silence.minTalkHelp'), onInput: function (v) { st.minTalk = v; manual(); } });
      var guard = ui.switch({ label: L('silence.guard'), help: L('silence.guardHelp'), checked: st.guard,
        onChange: function (v) { st.guard = v; ctx.save(); paintGuard(); recompute(); } });
      var tx = ui.switch({ label: L('silence.transcribe'), badge: ui.badge('gpu'), checked: st.transcribe,
        help: L('silence.transcribeHelp'),
        onChange: function (v) { st.transcribe = v; ctx.save(); } });
      var trackBox = ui.h('div', { class: 'field silence-tracks' });
      adv.add([thr, minS, pads, minT, guard, tx, trackBox]);
      el.appendChild(adv.el);
      if (!presetOf(st.preset)) adv.setAside(L('common.custom'));

      /* ---------------- Hasil */
      var hasil = ui.section({ title: L('silence.secResult') });
      var modes = ui.radioCards({ ariaLabel: L('silence.resultAria'), value: st.mode, onChange: function (v) { st.mode = v; ctx.save(); paintDock(); }, options: [
        { value: 'remove', title: L('silence.modeRemove'), desc: L('silence.modeRemoveDesc') },
        { value: 'markers', title: L('silence.modeMarkers'), desc: L('silence.modeMarkersDesc', { tag: TAG }) },
        { value: 'mute', title: L('silence.modeMute'), desc: L('silence.modeMuteDesc') }
      ] });
      hasil.add(modes);
      hasil.add(ui.switch({ label: L('silence.reviewFirst'), help: L('silence.reviewFirstHelp'), checked: st.review,
        onChange: function (v) { st.review = v; ctx.save(); paintDock(); } }));
      el.appendChild(hasil.el);

      /* ---------------- params */
      function cfgNow() {
        return { min_silence: D.r4(st.minSilence), pad_before: D.r4(st.padBefore), pad_after: D.r4(st.padAfter), min_talk: D.r4(st.minTalk), guard: !!st.guard };
      }
      function thrNow() { return pv.data ? U.clamp(pv.data.base + st.offset, -85, -10) : null; }
      function tracksParam() {
        var s = AC.seq.peek();
        return st.tracks && s && st.tracks.seq === s.id && st.tracks.list && st.tracks.list.length ? st.tracks.list.slice() : null;
      }
      function paramsNow() {
        var c = cfgNow();
        return { preset: st.preset || 'custom', min_silence: c.min_silence, pad_before: c.pad_before, pad_after: c.pad_after, min_talk: c.min_talk,
                 offset: st.offset, guard: !!st.guard, transcribe: !!(st.guard && st.transcribe && pv.data && !pv.data.has_words), mode: st.mode, tracks: tracksParam(),
                 carry: !!st.review };   // re-runs keep manual Tinjau choices; without Tinjau everything found is applied
      }

      /* ---------------- live detection */
      function recompute() {
        thr.set(st.offset, true);
        if (!pv.data || !pv.env) { pv.live = null; wave.setCuts([]); paintStrip(null); paintInfo(); paintDock(); return; }
        var t = thrNow(), key = t + '|' + (st.guard ? 1 : 0);
        if (pv.maskKey !== key) { pv.mask = D.mask(pv.env, t, pv.data.words, pv.data.protect, !!st.guard, pv.data.algo); pv.maskKey = key; }
        pv.live = D.plan(pv.mask, cfgNow(), pv.data.dur, pv.data.fps, pv.data.scope, pv.data.algo);
        pv.live.thr = t;
        wave.setThreshold(t);
        wave.setCuts(pv.live.cuts);
        paintStrip(pv.live); paintInfo(); paintDock();   // the threshold tag follows drags/keys/slider
      }
      function paintStrip(r) {
        var dur = pv.data ? pv.data.dur : 0;
        strip.innerHTML = '<div><span>' + U.esc(L('silence.stripPauses')) + '</span><b>' + (r ? U.int(r.cuts.length) : '-') + '</b></div>' +
          '<div class="hl"><span>' + U.esc(L('silence.stripRemoved')) + '</span><b>' + (r ? U.esc(U.dtk(r.sec)) : '-') + '</b></div>' +
          '<div><span>' + U.esc(L('silence.stripAfter')) + '</span><b>' + (r ? U.mmss(Math.max(0, dur - r.sec)) : '-') + '</b></div>';
        det.setAside(r && dur ? L('silence.saves', { pct: Math.round(r.sec / dur * 100) }) : '');
      }
      function paintInfo() {
        var d = pv.data, h = '';
        if (!d) { info.innerHTML = ''; return; }
        h += ui.tag(L(st.offset ? 'silence.tagThr' : 'silence.tagThrAuto', { db: String(d.base + st.offset) }), 'line', 'gauge');
        if (d.has_words) h += ui.tag(L(st.guard ? 'silence.tagWordsOn' : 'silence.tagWordsOff', { n: d.words_n || d.words.length }), st.guard ? 'ok' : 'warn', 'shield');
        else h += ui.tag(L('silence.tagNoTranscript'), 'warn', 'alert');
        if (d.video_only) h += ui.tag(L('silence.tagVideoOnly', { n: d.video_only }), 'line');
        if (d.unreadable) h += ui.tag(L('silence.tagOffline', { n: d.unreadable }), 'warn', 'alert');
        info.innerHTML = h;
      }
      function paintGuard() {
        var has = !!(pv.data && pv.data.has_words);
        tx.el.hidden = has || !st.guard;
        guard.el.querySelector('small').textContent = L(!has && pv.data ? 'silence.guardNoTranscript' : 'silence.guardHelp');
        paintInfo();
      }
      function legendText() {
        var d = pv.data; if (!d) return '';
        var sc = ctx.scope();
        var where = sc.kind === 'inout' ? L('silence.whereInOut', { a: U.tcode(sc.t0), b: U.tcode(sc.t1) }) : L(sc.kind === 'selected' ? 'silence.whereSelected' : 'silence.whereAll');
        return L('silence.legend', { where: where });
      }

      /* ---------------- tracks (chips, default = unmuted tracks) */
      function paintTracks(seq) {
        trackBox.innerHTML = '';
        var tr = (seq && seq.audio) || [];
        if (!tr.length) return;
        var sel = tracksParam() || tr.filter(function (t) { return !t.muted; }).map(function (t) { return t.index; });
        var chips = ui.chips({ multi: true, ariaLabel: L('silence.tracks'), value: sel,
          options: tr.map(function (t) {
            var n = t.clips ? t.clips.length : t.count;
            return { value: t.index, label: 'A' + (t.index + 1) + (t.muted ? ' (mute)' : ''), title: L(t.locked ? 'silence.trackTitleLocked' : 'silence.trackTitle', { name: t.name || '', n: n || 0 }) };
          }),
          onChange: function (v) {
            if (!v.length) { ctx.toast(L('silence.pickTrack'), 'alert'); chips.set(sel, true); return; }
            sel = v.slice().sort(function (a, b) { return a - b; });
            var s = AC.seq.peek(), dflt = tr.filter(function (t) { return !t.muted; }).map(function (t) { return t.index; });
            // Back to the default set (every unmuted track) = no explicit list, so muting a track later excludes it.
            st.tracks = sel.join(',') === dflt.join(',') ? null : { seq: s ? s.id : '', list: sel }; ctx.save(); schedulePreview(0);
          } });
        trackBox.appendChild(ui.h('div', { class: 'field-top' }, [ui.h('span', { class: 'lbl', text: L('silence.tracks') })]));
        trackBox.appendChild(chips.el);
        trackBox.appendChild(ui.h('small', { class: 'help', text: L('silence.tracksHelp') }));
      }

      /* ---------------- preview (engine worker, cached envelope: ~0,1 dtk) */
      var previewTimer = 0;
      function schedulePreview(ms) { clearTimeout(previewTimer); previewTimer = setTimeout(loadPreview, ms === undefined ? 300 : ms); }
      function loadPreview() {
        var token = ++pv.token, sent = paramsNow();
        pv.sig = seqSig(AC.seq.peek()) + '|' + JSON.stringify(ctx.scope());
        wave.set({ legend: L('silence.reading') });
        if (!AC.engine.available()) { pv.err = U.err('NO_ENGINE', L('silence.noEngine')); wave.set({ legend: L('silence.waitEngine') }); return; }
        AC.seq.current().then(function (seq) {
          if (token !== pv.token) return null;
          paintTracks(seq);
          if (!seq) { pv.data = null; wave.set({ env: [], dur: 0, legend: L('silence.openSeq') }); recompute(); return null; }
          var job = AC.engine.worker({ tool: 'silence', action: 'preview', seq: seq, record: false, title: L('silence.previewJob'),
                                       params: { tracks: sent.tracks, scope: ctx.scope(), preset: sent.preset, min_silence: sent.min_silence, pad_before: sent.pad_before,
                                                 pad_after: sent.pad_after, min_talk: sent.min_talk, offset: sent.offset, guard: sent.guard } });
          return job.then(function (d) {
            if (token !== pv.token) return;
            pv.data = d; pv.err = null; pv.env = D.decode(d.env, d.floor); pv.maskKey = '';
            if (d.algo) ALGO = d.algo;
            wave.set({ env: pv.env, dur: d.dur, legend: legendText() });
            paintGuard();
            // Self-check: the JS port must reproduce the engine's own count for the params it was sent.
            var r = D.detect(d, pv.env, { min_silence: sent.min_silence, pad_before: sent.pad_before, pad_after: sent.pad_after, min_talk: sent.min_talk, guard: sent.guard }, d.thr);
            pv.check = { engine: d.check, js: { n: r.cuts.length, sec: Math.round(r.sec * 1000) / 1000 }, ok: r.cuts.length === d.check.n && Math.abs(r.sec - d.check.sec) < 0.002 };
            if (!pv.check.ok) AC.log.warn('silence: panel count ' + r.cuts.length + ' differs from the engine ' + d.check.n);
            recompute();
          });
        }).catch(function (e) {
          if (token !== pv.token) return;
          pv.err = e; pv.data = null; pv.env = null;
          wave.set({ env: [], dur: 0, legend: L('silence.waveErr', { msg: U.errMsg(e) }) });
          recompute();
        });
      }
      ctx.refreshPreview = function () { schedulePreview(0); };

      /* ---------------- dock */
      function primaryLabel() {
        var r = pv.live;
        if (!r) return ctx.def.cta;
        if (!r.cuts.length) return L('silence.none');
        return L(st.review ? 'silence.cta.review' : 'silence.cta.' + st.mode, { n: r.cuts.length });
      }
      function paintDock() {
        var none = !!(pv.live && !pv.live.cuts.length);
        ctx.setPrimary({ label: primaryLabel(), disabled: none, title: none ? L('silence.noneTip') : '' });
      }
      ctx.dock({ primary: { label: primaryLabel(), icon: 'play', onClick: run } });

      /* ---------------- run: analyze -> review -> apply */
      function run() {
        var params = paramsNow();
        reviewShown = false;
        ctx.run({ action: 'analyze', title: L('silence.jobAnalyze'), params: params, onResult: function (data) { analyzed(data, params); } });
      }
      function analyzed(data, params) {
        if (params.transcribe) schedulePreview(0);        // the transcript exists now: refresh the live view
        var n = data && data.summary ? data.summary.n : 0;
        if (!n) {
          ctx.result({ title: L('silence.noneTitle'), sub: L('silence.noneSub', { sec: U.dtk(params.min_silence) }),
                       primary: { label: L('silence.adjust'), kbd: false, onClick: function () { ctx.back(); } } });
          return;
        }
        if (st.review) showReview(data);
        else applyFile(data.review, null, data.stats);
      }
      function showReview(data) {
        var mode = MODES[st.mode] ? st.mode : 'remove', stats = data.stats || {};
        var filters = [{ id: 'guard', label: L('silence.fGuard'), icon: 'shield', test: function (it) { return !!(it.note && it.note.type === 'guard'); } },
                       { id: 'long', label: L('silence.fLong'), test: function (it) { return it.t1 - it.t0 >= 1; } }];
        if (!stats.near_words) filters.shift();          // no gap touches a protected word: hide the empty chip
        reviewShown = true;
        if (stats.lost) filters.push({ id: 'lost', label: L('silence.fLost'), icon: 'alert', test: function (it) { return !!(it.note && it.note.type === 'warn'); } });
        ctx.review({ file: data.review, unit: L('silence.unit'), verbOn: L('silence.done.' + mode), verbOff: L('silence.kept'), filters: filters, markerTag: TAG, ctx: rowCtx,
          markerName: function (it) { return L('silence.marker', { sec: U.dtk(it.t1 - it.t0) }); },
          primary: { label: function (n) { return L('silence.cta.' + mode, { n: n }); }, onApply: function (items, doc, file) { applyFile(file, doc, stats); } } });
      }
      function applyFile(file, doc, stats) {
        ctx.run({ action: 'apply', review: file, params: { mode: st.mode }, title: L('silence.jobApply'), onResult: function (data) {
          lastDoc = doc || ui.loadReview(file);
          applyPlan(data, lastDoc, stats || {});
        } });
      }
      function guardWarn(stats) {
        var w = stats.protected_words || [];
        if (!stats.protected || !w.length) return null;
        var q = w.slice(0, 5).map(function (x) { return '"' + x + '"'; }).join(', ');
        return { title: L('silence.guardWarnTitle', { n: stats.protected }), text: L('silence.guardWarnText', { words: q + (stats.protected > 5 ? L('silence.andMore') : '') }) };
      }
      function deleteSeq(id) {
        return function () {
          AC.host.json('bac_deleteSequence', id).then(function () { ui.toast(L('silence.deleted'), 'trash'); ctx.back(); },
            function (e) { ui.toast(U.errMsg(e), { kind: 'err' }); });
        };
      }
      function backToReview() { return reviewShown && ctx.reviewList ? function () { ctx.go('review'); } : null; }
      function applyPlan(data, doc, stats) {
        var plan = data.plan || {}, seqId = plan.seq && plan.seq.id ? plan.seq.id : undefined, ranges = plan.ranges || [];
        if (plan.kind === 'remove_ranges') {
          var task = ctx.applyRemove(ranges, { seqId: seqId, unit: L('silence.unit'), onDone: function (res) {
            var o = ctx.removeResult(res, ranges, { secs: task.elapsed(), unit: L('silence.unit') });
            if (!o.warn) o.warn = guardWarn(stats);
            if (res.id) o.onDelete = deleteSeq(res.id);
            o.onEdit = backToReview();
            ctx.result(o);
          } });
          return;
        }
        if (plan.kind === 'markers') {
          var t0 = Date.now(), dur = doc.duration || 0, sec = (data.stats && data.stats.sec) || 0, n = (plan.markers || []).length;
          ctx.applyPlan(plan, { seqId: seqId, onDone: function (r) {
            ctx.result({ title: L('silence.mkTitle', { n: (r && r.n) || n }), sub: L('silence.mkSub', { secs: U.dtk((Date.now() - t0) / 1000), tag: TAG }),
              statsList: [{ label: L('silence.mkStatMarked'), value: U.int(n) }, { label: L('silence.mkStatIfCut'), value: U.mmss(Math.max(0, dur - sec)), accent: true }, { label: L('silence.statSaves'), value: (dur ? Math.round(sec / dur * 100) : 0) + '%' }],
              ribbon: { total: dur, spans: (plan.markers || []).map(function (m) { return [m.t, m.end]; }) },
              legend: [L('silence.legendKept'), L('silence.mkLegend', { n: n, sec: U.dtk(sec) })],
              onEdit: backToReview(),
              actions: [{ label: L('silence.mkClear'), icon: 'trash', onClick: function () {
                AC.host.json('bac_clearMarkersByTag', TAG, seqId || '').then(function (x) { ui.toast(L('silence.mkCleared', { n: (x && x.n) || 0, tag: TAG }), 'trash'); },
                  function (e) { ui.toast(U.errMsg(e), { kind: 'err' }); });
              } }],
              next: ctx.def.next });
          } });
          return;
        }
        if (plan.kind === 'mute_ranges') { mute(plan, doc, stats); return; }
        ctx.error(U.err('BAD_PLAN', L('silence.badPlan', { kind: String(plan.kind) })));
      }

      /* ---------------- mute: clone + hold-key volume dips (host/30_silence.jsx) */
      function mute(plan, doc, stats) {
        var ranges = plan.ranges || [], tracks = plan.tracks || [], seqId = plan.seq && plan.seq.id ? plan.seq.id : '';
        var task = new AC.Task({ tool: 'silence', action: 'mute', title: ctx.def.title, label: L('silence.modeMute'), kind: 'host' });
        AC.history.track(task);
        var cancelled = false, made = null;
        task._cancel = function () { cancelled = true; };
        AC.seq.hold(task.promise);
        task.plan([{ id: 'clone', label: L('silence.stClone'), w: 0.1 }, { id: 'mute', label: L('silence.cta.mute', { n: ranges.length }), w: 0.85 }, { id: 'open', label: L('silence.stOpen'), w: 0.05 }]);
        ctx.track(task, { title: L('silence.muting'), note: L('silence.muteNote'), onRetry: function () { mute(plan, doc, stats); },
          onDone: function (res) { muteResult(res, ranges, doc, task); } });
        var tot = { keys: 0, clips: 0, skipped: [] };
        AC.host.json('bac_seqInfo', 'lite', seqId).then(function (s) {
          if (!s) throw U.err('NO_SEQ', L('silence.errNoSrc'), L('silence.errNoSrcHint'));
          // Locked tracks are skipped by the host (the clone inherits the lock): when every chosen track with
          // clips is locked nothing could be muted, so stop before making a useless clone.
          var locked = [], open = 0;
          (s.audio || []).forEach(function (t) {
            if ((tracks.length && tracks.indexOf(t.index) < 0) || !t.count) return;
            if (t.locked) locked.push(t.name || ('A' + (t.index + 1))); else open++;
          });
          if (!open && locked.length) throw U.err('LOCKED', L('silence.errLocked', { tracks: locked.join(', ') }), L('silence.errLockedHint'));
          task.seqName = s.name;
          task.stage({ id: 'clone' });
          return AC.host.exec('bac_cloneSeq', [s.name.replace(AC.brand.suffixRe, '') + AC.brand.suffix, s.id], { json: true, timeout: 60000 }).then(function (c) {
            made = { id: c.id, name: c.name, origId: s.id, origName: s.name, dur: s.duration };
            task.stageDone('clone');
            task.stage({ id: 'mute', sub: L('silence.origUntouched') });
            var from = 0;
            function next() {
              if (from >= ranges.length) return Promise.resolve();
              if (cancelled) throw U.err('CANCELLED', L('silence.errCancelled', { name: c.name }), L('silence.errCancelledHint'));
              var to = Math.min(ranges.length, from + MUTE_CHUNK);
              return AC.host.exec('bac_silence_mute', [c.id, ranges, tracks, from, to], { json: true, timeout: 180000 }).then(function (r) {
                tot.keys += (r && r.keys) || 0; tot.clips += (r && r.clips) || 0;
                ((r && r.skipped) || []).forEach(function (x) { if (tot.skipped.indexOf(x) < 0) tot.skipped.push(x); });
                from = to; task.progress(from / ranges.length * 100);
                return next();
              });
            }
            return next();
          });
        }).then(function () {
          if (ranges.length && !tot.keys) {      // nothing muted (disabled clips, locked tracks): drop the unchanged clone
            var why = tot.skipped.length ? L('silence.lockedPrefix', { tracks: tot.skipped.join(', ') }) + ' ' : '';
            return AC.host.json('bac_deleteSequence', made.id).catch(function () { return null; }).then(function () {
              throw U.err('NO_AUDIO', L('silence.errNoMute'), why + L('silence.errNoMuteHint'));
            });
          }
          task.stageDone('mute');
          task.stage({ id: 'open' });
          return AC.host.json('bac_openSequence', made.id);
        }).then(function () {
          var sec = ranges.reduce(function (a, r) { return a + r[1] - r[0]; }, 0);
          task.summary = L('silence.muteSummary', { n: ranges.length, sec: U.dtk(sec) });
          task.done(U.assign({ mode: 'mute', n: ranges.length, sec: sec, stats: stats }, made, tot));
        }).catch(function (e) { task.fail(e); });
      }
      function muteResult(res, ranges, doc, task) {
        var dur = res.dur || doc.duration || 0;
        ctx.result({ title: L('silence.newSeqReady'), sub: L('silence.muteSub', { secs: U.dtk(task.elapsed()), n: res.n }),
          statsList: [{ label: L('silence.statMuted'), value: U.int(res.n) }, { label: L('silence.statSilent'), value: U.dtk(res.sec), accent: true }, { label: L('silence.statDur'), value: U.mmss(dur) }],
          ribbon: { total: dur, spans: ranges }, legend: [L('silence.legendSound'), L('silence.muteSummary', { n: res.n, sec: U.dtk(res.sec) })],
          original: { id: res.origId, name: res.origName }, onEdit: backToReview(), onDelete: deleteSeq(res.id),
          warn: res.skipped && res.skipped.length ? { title: L('silence.lockedSkipped'), text: L('silence.lockedSkippedText', { tracks: res.skipped.join(', ') }) } : guardWarn(res.stats || {}),
          next: ctx.def.next });
      }

      /* ---------------- first paint */
      paintStrip(null); paintGuard();
      schedulePreview(0);
    },

    onShow: function (ctx) {
      var pv = ctx.silence; if (!pv) return;
      var sig = seqSig(AC.seq.peek()) + '|' + JSON.stringify(ctx.scope());
      if (!pv.data || sig !== pv.sig) ctx.refreshPreview();
    },
    onSeq: function (lite, ctx) { if (ctx.silence && ctx.isActive() && ctx.pane() === 'main') ctx.refreshPreview(); },
    onScope: function (scope, ctx) { if (ctx.silence) ctx.refreshPreview(); }
  });
})();
