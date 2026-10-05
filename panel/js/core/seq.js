/* seq.js: AC.seq, the active Premiere sequence.
   AC.seq.peek()      -> last "lite" info (sync, may be null): header, track counts, main-track spans, selection count.
   AC.seq.current()   -> Promise<full Timeline JSON | null> (docs/SPEC.md section 3), cached until Premiere reports a
                         change (host tlBindEvents -> CSXS event "com.klipora.ev"); 2 s polling only when events
                         are unavailable.
   AC.seq.on('change', fn(lite, prev)) fires when the target sequence or its header/selection changes.
   AC.seq.lock(true) keeps the current sequence as the target while the user browses other sequences.
   AC.seq.hold(promise) pauses refreshes while host-heavy work runs (e.g. many QE extracts). */
(function () {
  'use strict';
  var AC = window.AC, U = AC.util;
  var EVENT = 'com.klipora.ev';
  var bus = new AC.Emitter();
  var lite = null, full = null, dirty = true, fullP = null, sig = '', lockedId = null, holds = 0, pollTimer = null;
  var M = AC.seq = {
    mode: 'off',            // 'events' | 'poll' | 'off'
    on: function (n, f) { bus.on(n, f); return M; },
    off: function (n, f) { bus.off(n, f); return M; },
    peek: function () { return lite; },
    locked: function () { return lockedId; }
  };

  function signature(s) {
    if (!s) return 'none';
    // Mute/lock flags count as a change: tools that follow the unmuted tracks (Potong Silence) must re-read.
    var counts = (s.video || []).concat(s.audio || []).map(function (t) { return t.count + (t.muted ? 'm' : '') + (t.locked ? 'l' : ''); }).join(',');
    var spans = s.spans ? s.spans.length + ':' + (s.spans.length ? s.spans[s.spans.length - 1][1] : 0) : '';
    return [s.id, s.name, s.duration, s.inPoint, s.outPoint, s.selectedCount, counts, spans, s.nMarkers].join('|');
  }

  // Refresh the lite info. Emits 'change' when anything visible changed; marks the full info dirty on edits.
  M.refresh = function (reason) {
    if (!AC.host.available) return Promise.resolve(null);
    if (holds) return Promise.resolve(lite);
    return AC.host.json('bac_seqInfo', 'lite', lockedId || '').then(function (s) {
      if (lockedId && !s) {                       // locked sequence was deleted or closed
        lockedId = null; bus.emit('lock', false);
        if (AC.ui && AC.ui.toast) AC.ui.toast(AC.t('seq.lockGone'), { icon: 'unlock' });
        return M.refresh(reason);
      }
      var prev = lite, ns = signature(s);
      lite = s;
      if (ns !== sig || reason === 'event') { dirty = true; }
      if (ns !== sig) { sig = ns; bus.emit('change', lite, prev); AC.bus.emit('seq', lite, prev); }
      else if (s && prev && s.player !== prev.player) bus.emit('player', s.player);
      return lite;
    }, function (e) {
      AC.log.error(e, 'bac_seqInfo');
      return lite;
    });
  };

  // Full Timeline JSON (what the engine receives). Cached while nothing changed.
  M.current = function (opts) {
    opts = opts || {};
    if (!AC.host.available) return Promise.resolve(null);
    if (!dirty && full && !opts.force) return Promise.resolve(full);
    if (fullP) return fullP;
    fullP = AC.host.exec('bac_seqInfo', ['full', lockedId || ''], { json: true, timeout: 60000 }).then(function (s) {
      fullP = null; full = s; dirty = false;
      if (s && (!lite || lite.id !== s.id)) M.refresh();
      return s;
    }, function (e) { fullP = null; throw e; });
    return fullP;
  };

  M.lock = function (on) {
    lockedId = on && lite ? lite.id : null;
    dirty = true; bus.emit('lock', !!lockedId);
    return M.refresh();
  };

  M.hold = function (p) {
    holds++;
    var release = function () { holds = Math.max(0, holds - 1); dirty = true; if (!holds) M.refresh('event'); };
    Promise.resolve(p).then(release, release);
    return p;
  };

  // Clip helpers on a full Timeline JSON.
  M.mainTrack = function (s) {
    s = s || full; if (!s) return null;
    var groups = [s.video || [], s.audio || []];
    for (var g = 0; g < 2; g++) for (var i = 0; i < groups[g].length; i++) if ((groups[g][i].count || (groups[g][i].clips || []).length) > 0) return groups[g][i];
    return null;
  };
  M.mediaPaths = function (s) {
    s = s || full; var seen = {}, out = [];
    if (!s) return out;
    (s.video || []).concat(s.audio || []).forEach(function (t) {
      (t.clips || []).forEach(function (c) { if (c.path && !seen[c.path.toLowerCase()]) { seen[c.path.toLowerCase()] = 1; out.push(c.path); } });
    });
    return out;
  };
  // Heuristic for the "Transkrip tersimpan" tag: every media file has <stem>_words.json next to it.
  M.hasTranscript = function (s) {
    var paths = M.mediaPaths(s);
    if (!paths.length || !AC.sys.node) return false;
    return paths.every(function (p) { return AC.sys.exists(AC.sys.join(AC.sys.dirname(p), AC.sys.stem(p) + '_words.json')); });
  };
  // Time range of a scope from the source card: {kind:'all'|'inout'|'selected'} -> [t0, t1] in sequence seconds.
  M.scopeRange = function (scope, s) {
    s = s || lite || full; if (!s) return null;
    if (scope && scope.kind === 'inout' && s.inPoint !== null && s.outPoint !== null) return [s.inPoint, s.outPoint];
    if (scope && scope.kind === 'selected' && scope.t0 !== undefined) return [scope.t0, scope.t1];
    return [0, s.duration];
  };

  /* ---------------- events / polling */
  var onEvent = U.debounce(function () { M.refresh('event'); }, 250);
  function startPolling() {
    M.mode = 'poll';
    clearInterval(pollTimer);
    pollTimer = setInterval(function () { if (!document.hidden) M.refresh('poll'); }, 2000);
  }
  M.start = function () {
    if (!AC.host.available) { M.mode = 'off'; return Promise.resolve(null); }
    var cep = window.__adobe_cep__;
    var canListen = cep && typeof cep.addEventListener === 'function';
    var bindP = canListen ? AC.host.json('bac_bindEvents', EVENT) : Promise.reject(U.err('NO_EVENTS', 'addEventListener missing'));
    return bindP.then(function () {
      cep.addEventListener(EVENT, function () { dirty = true; onEvent(); });
      M.mode = 'events';
      AC.log.info('seq: Premiere events active');
    }, function (e) {
      AC.log.warn('seq: Premiere events unavailable, polling every 2 s (' + U.errMsg(e) + ')');
      startPolling();
    }).then(function () { return M.refresh(); });
  };
  // Cheap catch-up when the panel becomes visible again (events may have been missed while hidden).
  document.addEventListener('visibilitychange', function () { if (!document.hidden && M.mode !== 'off') M.refresh(); });
  window.addEventListener('focus', function () { if (M.mode !== 'off') M.refresh(); });
})();
