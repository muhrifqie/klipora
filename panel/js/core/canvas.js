/* canvas.js: canvas components.
   AC.ui.ribbon(opts)   the cut-map ribbon (signature element): kept = grey base, removed = ember (hatched when large),
                        "off" spans outlined, selected span ringed, optional edit ticks and playhead. Handles 1000+ spans.
   AC.ui.waveform(opts) RMS envelope (dB per step) with removed-span shading and a draggable threshold line.
   Both redraw on resize (ResizeObserver) and on theme changes. Colours come from the CSS tokens. */
(function () {
  'use strict';
  var AC = window.AC, U = AC.util, ui = AC.ui;

  function css(name) { return getComputedStyle(document.documentElement).getPropertyValue(name).trim(); }
  function hexA(hex, a) {
    var m = /^#([0-9a-f]{6})$/i.exec(hex || '');
    if (!m) return hex;
    var n = parseInt(m[1], 16);
    return 'rgba(' + (n >> 16 & 255) + ',' + (n >> 8 & 255) + ',' + (n & 255) + ',' + a + ')';
  }
  ui.css = css; ui.hexA = hexA;

  // Size the backing store to the element (device pixel ratio aware). Returns the 2D context or null.
  function fit(c) {
    var w = c.clientWidth, h = c.clientHeight;
    if (!w || !h) return null;
    var dpr = window.devicePixelRatio || 1;
    if (c.width !== Math.round(w * dpr) || c.height !== Math.round(h * dpr)) { c.width = Math.round(w * dpr); c.height = Math.round(h * dpr); }
    var g = c.getContext('2d'); g.setTransform(dpr, 0, 0, dpr, 0, 0); g.clearRect(0, 0, w, h);
    return { g: g, w: w, h: h };
  }
  // Redraw on resize + theme change; drops its listeners once the canvas was shown and then removed from the DOM.
  function observe(el, fn) {
    var raf = 0, ro = null;
    var go = function () { cancelAnimationFrame(raf); raf = requestAnimationFrame(function () { if (el.isConnected) { el._seen = true; fn(); } }); };
    var onTheme = function () {
      if (!el.isConnected && el._seen) { AC.bus.off('theme', onTheme); if (ro) ro.disconnect(); return; }
      go();
    };
    if (window.ResizeObserver) { ro = new ResizeObserver(go); ro.observe(el); } else window.addEventListener('resize', go);
    AC.bus.on('theme', onTheme);
    return go;
  }
  function hatch(g, color) {
    var p = document.createElement('canvas'); p.width = 4; p.height = 4;
    var q = p.getContext('2d');
    q.fillStyle = color; q.fillRect(0, 0, 4, 4);
    q.strokeStyle = 'rgba(0,0,0,0.25)'; q.lineWidth = 1.4;
    q.beginPath(); q.moveTo(0, 4); q.lineTo(4, 0); q.moveTo(-1, 1); q.lineTo(1, -1); q.moveTo(3, 5); q.lineTo(5, 3); q.stroke();
    return g.createPattern(p, 'repeat');
  }
  function normSpans(spans) {
    return (spans || []).map(function (s) { return Array.isArray(s) ? { t0: s[0], t1: s[1], off: !!s.off, sel: !!s.sel } : s; });
  }

  /* ---------------- cut-map ribbon */
  // opts: {size: 'sm'|'lg', label, onClick(t)}  -> {el, set({total, spans, edges, playhead}), redraw(), summary()}
  ui.ribbon = function (o) {
    o = o || {};
    var c = ui.h('canvas', { class: 'ribbon' + (o.size === 'lg' ? ' lg' : ''), role: 'img', 'aria-label': o.label || AC.t('review.ribbon') });
    var st = { total: 0, spans: [], edges: [], playhead: null };
    function draw() {
      var f = fit(c); if (!f) return;
      var g = f.g, w = f.w, h = f.h, T = st.total || 1;
      var kept = css('--kept'), cut = css('--cut') || css('--accent'), line = css('--accent-line'), text = css('--text-1'), bg = css('--bg');
      g.fillStyle = kept; g.fillRect(0, 0, w, h);
      var pat = o.size === 'lg' ? hatch(g, cut) : null;
      st.spans.forEach(function (s) {
        var x0 = s.t0 / T * w, x1 = s.t1 / T * w, ww = Math.max(1, x1 - x0);
        if (s.off) { g.strokeStyle = line; g.lineWidth = 1; g.strokeRect(x0 + 0.5, 0.5, Math.max(0, ww - 1), h - 1); }
        else { g.fillStyle = pat || hexA(cut, 0.85); g.fillRect(x0, 0, ww, h); }
      });
      st.spans.forEach(function (s) {
        if (!s.sel) return;
        var x0 = s.t0 / T * w, ww = Math.max(2, (s.t1 - s.t0) / T * w);
        g.strokeStyle = text; g.lineWidth = 1.5; g.strokeRect(x0 + 0.75, 0.75, Math.max(0, ww - 1.5), h - 1.5);
      });
      if (st.edges && st.edges.length && st.edges.length < w / 2) {
        g.fillStyle = bg;
        st.edges.forEach(function (t) { g.fillRect(Math.round(t / T * w), 0, 1, h); });
      }
      if (st.playhead !== null && st.playhead !== undefined && st.total) {
        g.fillStyle = text; g.fillRect(Math.max(0, Math.min(w - 2, st.playhead / T * w - 1)), 0, 2, h);
      }
    }
    var redraw = observe(c, draw);
    if (o.onClick) c.addEventListener('click', function (e) { var r = c.getBoundingClientRect(); o.onClick((e.clientX - r.left) / r.width * (st.total || 0)); });
    return {
      el: c,
      set: function (p) {
        if (p.total !== undefined) st.total = p.total;
        if (p.spans !== undefined) st.spans = normSpans(p.spans);
        if (p.edges !== undefined) st.edges = p.edges || [];
        if (p.playhead !== undefined) st.playhead = p.playhead;
        var on = st.spans.filter(function (s) { return !s.off; }), sec = on.reduce(function (a, s) { return a + s.t1 - s.t0; }, 0);
        c.setAttribute('aria-label', AC.t('ui.ribbonAria', { label: o.label || AC.t('review.ribbon'), n: on.length, t: U.dtk(sec), total: U.mmss(st.total) }));
        draw();
      },
      redraw: redraw
    };
  };

  /* ---------------- waveform with draggable threshold */
  // opts: {env: [dB...], dur, threshold, min: -70, max: -12, cuts: fn(threshold) -> [[t0,t1]] | array,
  //        onThreshold(db) (drag/keys), label, tip, legend}
  ui.waveform = function (o) {
    o = o || {};
    var st = { env: o.env || [], dur: o.dur || 0, th: o.threshold !== undefined ? o.threshold : -35, cuts: [] };
    var lo = o.min !== undefined ? o.min : -70, hi = o.max !== undefined ? o.max : -12, thMin = o.thMin !== undefined ? o.thMin : -60, thMax = o.thMax !== undefined ? o.thMax : -15;
    var c = ui.h('canvas', { class: 'wave', role: 'img', tabIndex: 0, 'aria-label': o.label || AC.t('ui.waveAria') });
    var t0 = ui.h('span', { text: '0:00' }), legend = ui.h('span', { text: o.legend || AC.t('ui.waveLegend') }), t1 = ui.h('span', { text: U.mmss(st.dur) });
    var el = ui.h('div', { class: 'wave-wrap' }, [c, ui.h('span', { class: 'wave-tip', text: o.tip || AC.t('ui.waveTip') }), ui.h('div', { class: 'wave-foot' }, [t0, legend, t1])]);
    function cuts() { var k = typeof o.cuts === 'function' ? o.cuts(st.th) : (st.cuts || []); return normSpans(k); }
    function y(db, h) { return h - 4 - (Math.max(lo, Math.min(hi, db)) - lo) / (hi - lo) * (h - 10); }
    function draw() {
      var f = fit(c); if (!f) return;
      var g = f.g, w = f.w, h = f.h, env = st.env, n = env.length, D = st.dur || 1;
      var accent = css('--accent'), kept = css('--text-3'), ks = cuts().filter(function (k) { return !k.off; });
      ks.forEach(function (k) {
        var x0 = k.t0 / D * w, x1 = k.t1 / D * w;
        g.fillStyle = hexA(accent, 0.08); g.fillRect(x0, 0, x1 - x0, h);
        g.fillStyle = accent; g.fillRect(x0, h - 3, x1 - x0, 3);
      });
      var ki = 0;
      for (var x = 0; x < w && n; x += 2) {
        var i0 = Math.floor(x / w * n), i1 = Math.max(i0 + 1, Math.floor((x + 2) / w * n)), m = -90;
        for (var i = i0; i < i1 && i < n; i++) if (env[i] > m) m = env[i];
        var t = x / w * D;
        while (ki < ks.length && ks[ki].t1 < t) ki++;
        var inCut = ki < ks.length && t >= ks[ki].t0 && t <= ks[ki].t1;
        var top = y(m, h);
        g.fillStyle = inCut ? hexA(accent, 0.55) : kept;
        g.fillRect(x, top, 1.4, Math.max(1, h - 4 - top));
      }
      var ty = y(st.th, h);
      g.strokeStyle = accent; g.lineWidth = 1; g.setLineDash([4, 3]);
      g.beginPath(); g.moveTo(0, ty + 0.5); g.lineTo(w, ty + 0.5); g.stroke(); g.setLineDash([]);
      g.fillStyle = accent; g.font = '600 10px "Segoe UI Variable Text","Segoe UI",sans-serif';
      g.fillText(Math.round(st.th) + ' dB', 6, Math.max(11, ty - 4));
    }
    var redraw = observe(c, draw);
    function setTh(v, fire) {
      v = Math.round(U.clamp(v, thMin, thMax));
      if (v === st.th) return;
      st.th = v; draw();
      if (fire && o.onThreshold) o.onThreshold(v);
    }
    var drag = false;
    function fromY(e) { var r = c.getBoundingClientRect(), h = r.height; setTh(lo + (h - 4 - (e.clientY - r.top)) / (h - 10) * (hi - lo), true); }
    c.addEventListener('pointerdown', function (e) { drag = true; try { c.setPointerCapture(e.pointerId); } catch (x) { /* synthetic */ } fromY(e); });
    c.addEventListener('pointermove', function (e) { if (drag) fromY(e); });
    c.addEventListener('pointerup', function () { drag = false; });
    c.addEventListener('keydown', function (e) {
      if (e.key !== 'ArrowUp' && e.key !== 'ArrowDown') return;
      e.preventDefault(); setTh(st.th + (e.key === 'ArrowUp' ? 1 : -1) * (e.shiftKey ? 5 : 1), true);
    });
    return {
      el: el, canvas: c,
      set: function (p) { if (p.env) st.env = p.env; if (p.dur !== undefined) { st.dur = p.dur; t1.textContent = U.mmss(p.dur); } if (p.legend !== undefined) legend.textContent = p.legend; draw(); },
      setThreshold: function (v) { st.th = v; draw(); },
      threshold: function () { return st.th; },
      setCuts: function (k) { st.cuts = k || []; draw(); },
      redraw: redraw
    };
  };
})();
