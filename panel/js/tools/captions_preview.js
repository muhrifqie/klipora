/* captions_preview.js: Auto Caption sticky preview (AC.cap.preview). Engine `preview` through the persistent worker
   (docs/CAPTIONS_API.md section 8): one request in flight, latest wins, results for an old time are dropped, the
   previous PNG stays until the next one has loaded. Follows the Premiere playhead (host poll every 500 ms while the
   editor is on screen; a preview is made when the playhead stops) or the page picked in the editor.
   Overlays: platform safe zone (dashed) and a drag handle at the page's y (drop = engine op page_pos).
   Bar: 3 s loop (engine burn -> <video>), previous / next caption, position, undo / redo, safe-zone toggle,
   follow-playhead toggle. Timings of every preview land in AC.cap.stats.previews (tests). */
(function () {
  'use strict';
  var AC = window.AC, U = AC.util, ui = AC.ui, cap = AC.cap, S = cap.S;

  // Platform safe zones (fractions top/bottom/left/right), mirror of engine/ac/captions/layout.py SAFE_ZONES.
  var SAFE = {
    none: [0, 0, 0, 0], title: [0.05, 0.05, 0.05, 0.05], youtube: [0.05, 0.10, 0.05, 0.05],
    tiktok: [130 / 1920, 484 / 1920, 44 / 1080, 140 / 1080], reels: [0.14, 0.35, 0.06, 0.06],
    shorts: [180 / 1920, 390 / 1920, 60 / 1080, 120 / 1080], square: [0.05, 0.05, 0.05, 0.05], feed45: [0.05, 0.05, 0.05, 0.05]
  };
  SAFE.vertical_universal = [0, 1, 2, 3].map(function (i) { return Math.max(SAFE.tiktok[i], SAFE.reels[i], SAFE.shorts[i]); });
  // Overlay label per zone (translated when shown: the language can change while the panel runs).
  function safeLabel(id) {
    return id === 'vertical_universal' ? AC.t('cap.preview.safeVertical') : id === 'title' ? AC.t('cap.preview.safeTitle')
      : ({ youtube: 'YouTube', tiktok: 'TikTok', reels: 'Reels', shorts: 'Shorts', square: '1:1', feed45: '4:5' })[id] || '';
  }
  function resolveSafe(name, w, h) {
    if (!name || name === 'auto') { var r = w / Math.max(1, h); return r < 0.85 ? 'tiktok' : r < 1.2 ? 'square' : 'youtube'; }
    return SAFE[name] ? name : 'none';
  }
  cap.safeZone = function (name) { var s = S.seq || { w: 16, h: 9 }; var id = resolveSafe(name, s.w, s.h); return { id: id, f: SAFE[id], label: safeLabel(id) }; };

  var P = { el: null, want: null, inflight: false, t: 0, page: -1, active: false, poll: null, lastPoll: null, shownT: null, video: null };

  function scale() { return window.innerWidth >= 900 ? 0.6 : 0.5; }

  cap.preview = {
    create: function () {
      var st = S.ctx.state;
      var stage = ui.h('div', { class: 'cap-stage', 'aria-label': AC.t('cap.preview.aria') });
      var img = ui.h('img', { class: 'cap-frame', alt: '' });
      var empty = ui.h('div', { class: 'cap-empty', text: AC.t('cap.preview.loading') });
      var safe = ui.h('div', { class: 'cap-safe', hidden: true }, [ui.h('span')]);
      var ypos = ui.h('div', { class: 'cap-ypos', tabIndex: 0, role: 'slider', 'aria-label': AC.t('cap.preview.yposAria'), 'aria-valuemin': '0', 'aria-valuemax': '100', hidden: true }, [ui.h('i')]);
      var busy = ui.h('span', { class: 'cap-busy', 'aria-hidden': 'true' });
      var ms = ui.h('span', { class: 'cap-ms' });
      stage.appendChild(empty); stage.appendChild(img); stage.appendChild(safe); stage.appendChild(ypos); stage.appendChild(busy); stage.appendChild(ms);
      var box = ui.h('div', { class: 'cap-stage-box' }, [stage]);

      var bPlay = ibtn('play', AC.t('cap.preview.play'), function () { playLoop(); });
      var bPrev = ibtn('chev-l', AC.t('cap.preview.prev'), function () { step(-1); });
      var pos = ui.h('span', { class: 'cap-pos', 'aria-live': 'polite' });
      var bNext = ibtn('chev-r', AC.t('cap.preview.next'), function () { step(1); });
      var bUndo = ibtn('undo', AC.t('cap.preview.undo'), function () { cap.undo(); });
      var bRedo = ibtn('refresh', AC.t('cap.preview.redo'), function () { cap.redo(); });
      var bSafe = ibtn('pos', AC.t('cap.preview.safe'), function () { st.safe = !st.safe; S.ctx.save(); paintOverlays(); });
      var bFollow = ibtn('playhead', AC.t('cap.preview.follow'), function () { st.follow = !st.follow; S.ctx.save(); paintBar(); restartPoll(); if (st.follow) pollOnce(true); });
      var bZoom = ibtn('zoom', AC.t('cap.preview.zoom'), function () { st.zoom = !(zoomFactor() > 1); S.ctx.save(); paintBar(); paintOverlays(); });
      var bar = ui.h('div', { class: 'cap-bar' }, [bPlay, bPrev, pos, bNext, bUndo, bRedo, bZoom, bSafe, bFollow]);
      var el = ui.h('div', { class: 'cap-preview' }, [box, bar]);
      P.el = el; P.stage = stage; P.img = img; P.empty = empty; P.safe = safe; P.ypos = ypos; P.ms = ms; P.pos = pos;
      P.b = { play: bPlay, prev: bPrev, next: bNext, undo: bUndo, redo: bRedo, safe: bSafe, follow: bFollow, zoom: bZoom };

      function sizeStage() {
        var s = S.seq || { w: 16, h: 9 };
        stage.style.aspectRatio = s.w + ' / ' + s.h;
        // very tall frames would push the editor out of view: cap the stage height
        var maxH = Math.max(150, Math.round(window.innerHeight * (window.innerWidth >= 600 ? 0.7 : 0.36)));
        stage.style.maxWidth = (s.h > s.w ? Math.round(maxH * s.w / s.h) + 'px' : '');
      }
      P.sizeStage = sizeStage;
      sizeStage();
      dragY(ypos);
      wire();
      // first position: Premiere playhead if it sits on a caption, else the first page
      var s = AC.seq.peek(), t = s ? s.player || 0 : 0;
      P.t = S.doc && cap.pageAt(t) < 0 && S.doc.pages.length ? S.doc.pages[0].t0 + 0.05 : t;
      P.page = cap.pageAt(P.t);
      paintBar(); paintOverlays();
      return { el: el };
    },
    // Show sequence time t (from the editor: page click, word click). seek: also move the Premiere playhead.
    show: function (t, opts) {
      opts = opts || {};
      P.t = Math.max(0, Number(t) || 0);
      P.page = cap.pageAt(P.t);
      paintBar(); paintOverlays();
      cap.bus.emit('time', P.t, P.page);
      if (opts.seek !== false && AC.host.available && S.seq) {
        P.head = P.t; P.lastPoll = P.t;      // our own seek is not a user playhead move
        AC.host.json('bac_seek', P.t, S.seq.id).catch(function (e) { AC.log.warn('bac_seek: ' + U.errMsg(e)); });
      }
      cap.preview.request();
    },
    showPage: function (i, opts) {
      var pg = S.doc && S.doc.pages[i]; if (!pg) return;
      cap.preview.show(pg.t0 + Math.min(0.35, (pg.t1 - pg.t0) / 2), opts);
    },
    time: function () { return P.t; },
    page: function () { return P.page; },
    request: function () {
      if (!P.el) return;
      if (!S.doc || !S.doc.pages || !S.doc.pages.length) { P.empty.hidden = false; P.empty.textContent = AC.t('cap.preview.none'); return; }
      P.want = { t: P.t, rev: S.rev, at: Date.now() };
      pump();
    },
    // editor on screen (main pane): poll the playhead
    active: function (on) { if (!P.el) return; P.active = !!on; restartPoll(); if (on) { if (S.ctx.state.follow) pollOnce(true); else cap.preview.request(); } else stopLoop(); },
    paint: function () { paintBar(); paintOverlays(); },
    safeZone: cap.safeZone
  };

  // Listeners once per panel session: create() runs again after a language switch and P always holds the newest
  // stage, so the handlers act on whatever is on screen now.
  var wired = false;
  function wire() {
    if (wired) return;
    wired = true;
    window.addEventListener('resize', U.debounce(function () { if (P.sizeStage) P.sizeStage(); paintOverlays(); }, 150));
    cap.bus.on('doc', function () { if (!P.el) return; P.sizeStage(); P.page = cap.pageAt(P.t); paintBar(); paintOverlays(); if (S.doc) cap.preview.request(); });
    cap.bus.on('draft', function () { paintOverlays(); });
    cap.bus.on('templates', function () { paintOverlays(); });
    cap.bus.on('undo', function () { paintBar(); });
    cap.bus.on('busy', function (n) { if (!n) cap.preview.request(); });
  }

  function ibtn(icon, label, fn) {
    var b = ui.h('button', { class: 'ibtn', type: 'button', 'aria-label': label, title: label, html: ui.icon(icon) });
    b.addEventListener('click', fn);
    return b;
  }

  /* ---------------------------------------------------------------- engine preview (latest wins) */
  function pump() {
    if (P.inflight || !P.want) return;
    if (S.busy) { P.stage.classList.add('is-busy'); return; }        // render/AI running: resumed on 'busy' 0
    var w = P.want; P.want = null; P.inflight = true;
    P.stage.classList.add('is-busy');
    var t0 = Date.now();
    var job = cap.worker('preview', { t: w.t, mode: 'frame', scale: scale() }, { withSeq: true });
    job.promise.then(function (r) {
      var im = new Image();
      im.onload = function () {
        P.inflight = false;
        // drop results for a time that is no longer wanted (the next request is already queued)
        if (!P.want || Math.abs(P.want.t - w.t) < 1e-6) {
          P.img.src = im.src; P.shownT = w.t; P.empty.hidden = true;
          var ms = Date.now() - t0;
          cap.stats.previews.push({ t: w.t, ms: ms, engine: r.ms, cached: !!r.frame_cached, at: w.at, shown: Date.now() });
          if (cap.stats.previews.length > 200) cap.stats.previews.shift();
          P.ms.textContent = AC.settings.get('developer') ? ms + ' ms' : '';
          P.stage.setAttribute('data-t', String(w.t));
        }
        if (!P.want) P.stage.classList.remove('is-busy');
        pump();
      };
      im.onerror = function () { P.inflight = false; P.stage.classList.remove('is-busy'); AC.log.warn('preview png failed to load: ' + r.png); pump(); };
      im.src = cap.fileUrl(r.png);
    }, function (e) {
      P.inflight = false;
      P.stage.classList.remove('is-busy');
      if (e && e.code === 'CANCELLED') { pump(); return; }
      P.empty.hidden = false; P.empty.textContent = AC.t('cap.preview.failed', { msg: U.errMsg(e) });
      AC.log.warn('captions preview: ' + U.errMsg(e));
      pump();
    });
  }

  /* ---------------------------------------------------------------- playhead follow (no Premiere event exists for it) */
  function restartPoll() {
    clearInterval(P.poll); P.poll = null;
    if (P.active && S.ctx.state.follow && AC.host.available) P.poll = setInterval(function () { if (!document.hidden) pollOnce(false); }, 500);
  }
  // P.lastPoll = previous read (stop detection), P.head = playhead position we already reacted to.
  function pollOnce(force) {
    if (!S.seq || !AC.host.available) { cap.preview.request(); return; }
    var id = S.seq.id;
    AC.host.json('bac_captions_player', id).then(function (r) {
      if (!r || !S.seq || S.seq.id !== id) return;
      var t = Number(r.t) || 0, prev = P.lastPoll;
      P.lastPoll = t;
      if (force) {
        // first look: the playhead's caption, or the nearest one when the playhead sits in a gap
        P.head = t;
        var i = cap.pageAt(t), n = i < 0 ? cap.nearestPage(t) : i, pg = S.doc && S.doc.pages[n];
        setT(i < 0 && pg ? pg.t0 + Math.min(0.35, (pg.t1 - pg.t0) / 2) : t);
        return;
      }
      // the playhead stopped at a new place (two equal reads): preview there
      var tol = 0.5 / ((S.seq && S.seq.fps) || 30);
      if (prev !== null && Math.abs(t - prev) < 1e-3 && (P.head === null || P.head === undefined || Math.abs(t - P.head) > tol)) { P.head = t; setT(t); }
    }, function (e) { if (force) cap.preview.request(); AC.log.warn('bac_captions_player: ' + U.errMsg(e)); });
  }
  function setT(t) {
    P.t = t; P.page = cap.pageAt(t);
    paintBar(); paintOverlays();
    cap.bus.emit('time', t, P.page);
    cap.preview.request();
  }
  function step(d) {
    var pages = (S.doc && S.doc.pages) || []; if (!pages.length) return;
    var i = P.page >= 0 ? P.page + d : (d > 0 ? firstAfter(P.t) : lastBefore(P.t));
    i = U.clamp(i, 0, pages.length - 1);
    cap.preview.showPage(i);
  }
  function firstAfter(t) { var pages = S.doc.pages; for (var i = 0; i < pages.length; i++) if (pages[i].t0 >= t) return i; return pages.length - 1; }
  function lastBefore(t) { var pages = S.doc.pages; for (var i = pages.length - 1; i >= 0; i--) if (pages[i].t1 <= t) return i; return 0; }

  /* ---------------------------------------------------------------- bar + overlays */
  function paintBar() {
    if (!P.pos) return;
    var pages = (S.doc && S.doc.pages) || [], n = pages.length, i = P.page;
    P.pos.innerHTML = (i >= 0 ? '<b>' + (i + 1) + '</b> / ' + n : U.esc(n ? AC.t('cap.preview.noneHere') : AC.t('cap.preview.zero'))) + ' <span class="tc">' + U.esc(U.tcode(P.t)) + '</span>';
    P.b.prev.disabled = !n || i === 0; P.b.next.disabled = !n || i === n - 1;
    P.b.undo.disabled = !cap.canUndo(); P.b.redo.disabled = !cap.canRedo();
    P.b.safe.setAttribute('aria-pressed', S.ctx.state.safe ? 'true' : 'false');
    P.b.follow.setAttribute('aria-pressed', S.ctx.state.follow ? 'true' : 'false');
    P.b.zoom.setAttribute('aria-pressed', zoomFactor() > 1 ? 'true' : 'false');
  }

  /* ---------------------------------------------------------------- zoom to the caption (wide screen recordings)
     A 2292 px frame in a 360 px panel makes captions ~7 px tall. Zoom shows the frame at Z x around the caption
     block (Z from the template's line length, so the longest line still fits). Auto = on when Z >= 1,3. */
  function zoomFactor() {
    var s = S.seq, e = cap.eff();
    if (!s || !e || !e.font || !e.layout) return 1;
    var fs = e.font.size * Math.min(s.w, s.h) / 1080;
    var lineW = Math.min(s.w * (e.layout.width || 80) / 100, (e.layout.max_chars || 30) * fs * 0.56) * 1.12;
    var z = U.clamp(s.w / Math.max(1, lineW), 1, 2.5);
    var want = S.ctx.state.zoom;
    if (want === false) return 1;
    if (want === true) return Math.max(1.3, z);
    return z >= 1.3 ? z : 1;
  }
  // Geometry of the frame inside the stage (percent of the stage): left/top offset and zoom.
  function geom() {
    var z = zoomFactor(), s = S.seq || { w: 16, h: 9 }, e = cap.eff() || {}, lay = e.layout || {};
    if (z <= 1) return { z: 1, left: 0, top: 0 };
    var pg = S.doc && S.doc.pages[P.page];
    var y = (pg ? pg.y : (lay.y || 80)) / 100;
    var fs = (e.font ? e.font.size : 60) * Math.min(s.w, s.h) / 1080;
    var half = Math.min(s.w * (lay.width || 80) / 100, (lay.max_chars || 30) * fs * 0.56) / 2 / s.w;
    var cx = lay.align === 'left' ? (lay.x || 6) / 100 + half : lay.align === 'right' ? (lay.x || 94) / 100 - half : (lay.x || 50) / 100;
    var left = U.clamp(50 - cx * z * 100, (1 - z) * 100, 0), top = U.clamp(50 - y * z * 100, (1 - z) * 100, 0);
    return { z: z, left: left, top: top };
  }
  function applyGeom() {
    var g = geom(); P.g = g;
    var st = P.img.style;
    st.width = (g.z * 100) + '%'; st.height = (g.z * 100) + '%'; st.left = g.left + '%'; st.top = g.top + '%';
    P.stage.classList.toggle('is-zoom', g.z > 1);
    return g;
  }
  function mapX(f) { return P.g.left + f * P.g.z * 100; }
  function mapY(f) { return P.g.top + f * P.g.z * 100; }
  function paintOverlays() {
    if (!P.stage) return;
    applyGeom();
    var eff = cap.eff(), z = cap.safeZone(eff && eff.layout ? eff.layout.safe : 'auto');
    var show = S.ctx.state.safe && z.id !== 'none';
    P.safe.hidden = !show;
    if (show) {
      P.safe.style.top = mapY(z.f[0]) + '%'; P.safe.style.bottom = (100 - mapY(1 - z.f[1])) + '%';
      P.safe.style.left = mapX(z.f[2]) + '%'; P.safe.style.right = (100 - mapX(1 - z.f[3])) + '%';
      P.safe.firstChild.textContent = AC.t('cap.preview.safeZone', { name: z.label });
    }
    var pg = S.doc && S.doc.pages[P.page];
    P.ypos.hidden = !pg || P.dragging;
    if (pg && !P.dragging) {
      P.ypos.style.top = mapY(pg.y / 100) + '%';
      P.ypos.setAttribute('aria-valuenow', String(Math.round(pg.y)));
      P.ypos.querySelector('i').textContent = AC.t(pg.pos === 'manual' ? 'cap.preview.posManual' : pg.pos === 'auto' ? 'cap.preview.posAuto' : 'cap.preview.posDrag', { pct: Math.round(pg.y) });
    }
  }

  // Drag the handle vertically: preview the line live, send page_pos on release. Arrow keys move 1 % (Shift 5 %).
  function dragY(h) {
    var startY = 0, startPct = 0, rect = null;
    h.addEventListener('pointerdown', function (e) {
      var pg = S.doc && S.doc.pages[P.page]; if (!pg) return;
      e.preventDefault(); h.setPointerCapture(e.pointerId);
      rect = P.stage.getBoundingClientRect(); startY = e.clientY; startPct = pg.y;
      P.dragging = true; h.hidden = false; h.classList.add('is-drag');
    });
    h.addEventListener('pointermove', function (e) {
      if (!P.dragging) return;
      var pct = U.clamp(startPct + (e.clientY - startY) / (rect.height * P.g.z) * 100, 3, 97);
      h.style.top = mapY(pct / 100) + '%'; h.querySelector('i').textContent = Math.round(pct) + '%';
      h._pct = pct;
    });
    function end() {
      if (!P.dragging) return;
      P.dragging = false; h.classList.remove('is-drag');
      var pg = S.doc && S.doc.pages[P.page];
      if (pg && h._pct !== undefined && Math.abs(h._pct - pg.y) >= 0.5) setY(pg, h._pct);
      h._pct = undefined;
      paintOverlays();
    }
    h.addEventListener('pointerup', end);
    h.addEventListener('pointercancel', end);
    h.addEventListener('dblclick', function () { var pg = S.doc && S.doc.pages[P.page]; if (pg && pg.pos) cap.op({ op: 'page_pos', page: pg.id, y: null }, { label: AC.t('cap.preview.posLabel'), now: true }); });
    h.addEventListener('keydown', function (e) {
      var pg = S.doc && S.doc.pages[P.page]; if (!pg) return;
      if (e.key !== 'ArrowUp' && e.key !== 'ArrowDown') return;
      e.preventDefault(); e.stopPropagation();
      setY(pg, U.clamp(pg.y + (e.key === 'ArrowUp' ? -1 : 1) * (e.shiftKey ? 5 : 1), 3, 97));
    });
  }
  function setY(pg, y) {
    pg.y = Math.round(y * 10) / 10; pg.pos = 'manual';
    paintOverlays();
    cap.op({ op: 'page_pos', page: pg.id, y: pg.y }, { label: AC.t('cap.preview.posLabel') });
  }

  /* ---------------------------------------------------------------- 3 s loop with animation + audio (engine burn) */
  function playLoop() {
    if (P.video) { stopLoop(); return; }
    var pg = S.doc && S.doc.pages[P.page], t0 = pg ? Math.max(0, pg.t0 - 0.1) : P.t;
    P.b.play.disabled = true; P.stage.classList.add('is-busy');
    cap.idle().then(function () {
      return cap.worker('burn', { t0: t0, dur: 3, scale: 0.5 }, { withSeq: true }).promise;
    }).then(function (r) {
      P.b.play.disabled = false; P.stage.classList.remove('is-busy');
      var v = ui.h('video', { src: cap.fileUrl(r.path), loop: true, playsinline: true, 'aria-label': AC.t('cap.preview.loopAria') });
      v.addEventListener('click', stopLoop);
      P.stage.appendChild(v); P.video = v;
      P.b.play.innerHTML = ui.icon('pause'); P.b.play.setAttribute('aria-label', AC.t('cap.preview.stopLoop')); P.b.play.title = AC.t('cap.preview.stopLoop');
      safePlay(v, true);
    }, function (e) {
      P.b.play.disabled = false; P.stage.classList.remove('is-busy');
      ui.toast(AC.t('cap.preview.loopFailed', { msg: U.errMsg(e) }), { kind: 'err' });
    });
  }
  // play() rejects when pause() comes first (AbortError) or autoplay with sound is blocked: retry muted once.
  function safePlay(v, retry) {
    var p = v.play && v.play();
    if (p && p.catch) p.catch(function (e) {
      if (!retry || (e && e.name === 'AbortError') || P.video !== v) return;
      v.muted = true; safePlay(v, false);
    });
  }
  function stopLoop() {
    if (!P.video) return;
    try { P.video.pause(); } catch (e) { /* gone */ }
    if (P.video.parentNode) P.video.parentNode.removeChild(P.video);
    P.video = null;
    P.b.play.innerHTML = ui.icon('play'); P.b.play.setAttribute('aria-label', AC.t('cap.preview.play')); P.b.play.title = AC.t('cap.preview.play');
  }
  cap.preview.stopLoop = stopLoop;
})();
