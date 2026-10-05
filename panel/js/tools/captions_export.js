/* captions_export.js: Auto Caption "Ekspor" tab + "Terapkan ke timeline" (AC.cap.exportUI).
   Outputs (docs/CAPTIONS_API.md section 7):
     overlay (recommended): engine render (worker, versioned qtrle .mov, band, chunk cache) -> host
       bac_captions_applyOverlay: relink in place when plan.relink (same band/codec), else place on the top video
       track "Klipora Captions" (only that track is cleared; an old "AutoCut Captions" track is reused as is).
       Optional .srt next to it.
     srt: engine srt -> host bac_captions_applySrt (native caption track, basic style only).
     mogrt (short videos): engine mogrt {enable} -> host bac_captions_mogrtBegin + bac_captions_mogrtItems in chunks.
   One composite AC.Task (render + place) drives the progress pane (ctx.track), then a result card.
   "Samakan warna dengan pratinjau" = compositeLinearColor off for this sequence, only when the user flips it. */
(function () {
  'use strict';
  var AC = window.AC, U = AC.util, ui = AC.ui, cap = AC.cap, S = cap.S;
  var MOGRT_MAX = 300, MOGRT_SEC = 0.26, CHUNK = 20;
  var E = {};

  function st() { return S.ctx.state; }
  function pages() { return (S.doc && S.doc.pages) ? S.doc.pages.length : 0; }
  function mb(x) { return U.dec(x, x < 10 ? 1 : 0) + ' MB'; }
  function T(k, v) { return AC.t('cap.export.' + k, v); }
  // Our overlay track: the name Premiere shows (host status; old projects keep "AutoCut Captions"), else the new one.
  function trackName() { return (S.placed && S.placed.name) || AC.brand.track('Captions'); }
  function editName() { return (S.placed && S.placed.editable && S.placed.editable.name) || AC.brand.track('Captions (Editable)'); }
  var wired = false;

  cap.exportUI = {
    create: function (panel) {
      var out = ui.section({ title: T('outTitle') });
      E.cards = ui.radioCards({ ariaLabel: T('outAria'), value: st().out, options: [
        { value: 'overlay', title: T('overlayTitle'), desc: T('overlayDesc', { track: AC.brand.track('Captions') }), tag: T('recommended'), tagKind: 'ai' },
        { value: 'srt', title: T('srtTitle'), desc: T('srtDesc') },
        { value: 'mogrt', title: T('mogrtTitle'), desc: T('mogrtDesc') }
      ], onChange: function (v) { st().out = v; S.ctx.save(); paint(); cap.paintDock(); } });
      out.add(E.cards);
      E.mogrtNote = ui.h('div', { class: 'cap-out-note' });
      out.add(E.mogrtNote);
      panel.appendChild(out.el);

      // overlay options
      E.ovSec = ui.section({ title: 'Overlay' });
      E.srtToo = ui.switch({ label: T('srtToo'), help: T('srtTooHelp'), checked: !!st().srtToo,
        onChange: function (v) { st().srtToo = v; S.ctx.save(); } });
      E.linear = ui.switch({ label: T('linear'), help: T('linearHelp'),
        onChange: function (v) { setLinear(v); } });
      E.ovSec.add([E.srtToo, E.linear]);
      var adv = ui.advanced({ title: T('renderSettings'), key: 'captions-render' });
      E.codec = ui.select({ ariaLabel: T('codec'), width: '190px', value: st().codec || 'qtrle', options: [
        { value: 'qtrle', label: 'QuickTime Animation' }, { value: 'png', label: T('codecPng') }, { value: 'prores', label: T('codecProres') }],
        onChange: function (v) { st().codec = v; S.ctx.save(); } });
      E.band = ui.segmented({ small: true, ariaLabel: T('band'), value: st().band || 'auto', options: [{ value: 'auto', label: T('bandAuto') }, { value: 'off', label: T('bandFull') }],
        onChange: function (v) { st().band = v; S.ctx.save(); } });
      adv.add([ui.inlineField({ label: T('codec'), help: T('codecHelp'), control: E.codec.el }),
               ui.inlineField({ label: T('band'), help: T('bandHelp'), control: E.band.el })]);
      E.ovSec.add(adv);
      panel.appendChild(E.ovSec.el);

      // save the current look into Gaya Saya (same sheet as the dock action "Simpan gaya")
      var save = ui.section({ title: AC.t('cap.dock.saveStyle') });
      save.add(ui.h('p', { class: 'help', text: T('saveHelp') }));
      save.add(ui.h('div', { class: 'btn-row' }, [ui.button({ label: T('saveThis'), icon: 'star', small: true, onClick: function () { cap.styleUI.saveDialog(); } })]));
      panel.appendChild(save.el);

      // what is on the timeline now
      E.now = ui.section({ title: T('nowTitle') });
      E.nowBody = ui.h('div', { class: 'sec', style: { gap: '6px' } });
      E.now.add(E.nowBody);
      panel.appendChild(E.now.el);

      if (!wired) { wired = true; cap.bus.on('placed', function () { paint(); }); cap.bus.on('doc', function () { paint(); }); }   // once: create() runs again after a language switch
      paint();
    },
    // Dock primary for the editor.
    primary: function () {
      var n = pages(), mode = st().out, v = cap.placedVersion(), d = S.doc || {};
      var label = T('applyN', { n: n }), title = T('applyTitle', { track: trackName() });
      if (mode === 'overlay' && v && d.hash === (d.render || {}).hash) { label = T('placedV', { v: v }); title = T('placedTitle'); }
      else if (mode === 'overlay' && S.placed && S.placed.clips && S.placed.ours) { label = T('update'); title = T('updateTitle'); }
      else if (mode === 'srt') { label = T('srtN', { n: n }); title = T('srtNTitle'); }
      else if (mode === 'mogrt') { label = T('mogrtN', { n: n }); title = T('mogrtNTitle'); }
      return { label: label, icon: 'export', title: title, disabled: !n || (mode === 'mogrt' && n > MOGRT_MAX), onClick: function () { cap.exportUI.apply(); } };
    },
    apply: function () { apply(); }
  };

  function paint() {
    if (!E.cards) return;
    var n = pages(), mode = st().out;
    E.cards.set(mode, true);
    E.ovSec.el.hidden = mode !== 'overlay';
    E.mogrtNote.innerHTML = '';
    if (mode === 'mogrt') {
      if (n > MOGRT_MAX) E.mogrtNote.appendChild(ui.alert({ kind: 'warn', title: T('mogrtTooMany', { n: n }), text: T('mogrtTooManyText', { max: MOGRT_MAX }) }));
      else E.mogrtNote.appendChild(ui.alert({ kind: 'info', title: T('mogrtEstimate', { n: n, dur: U.dtk(Math.max(1, n * MOGRT_SEC)) }), text: T('mogrtEstimateText') }));
    }
    if (mode === 'srt') E.mogrtNote.appendChild(ui.alert({ kind: 'info', title: T('srtOldTitle'), text: T('srtOldText') }));
    var p = S.placed;
    E.linear.set(!!(p && p.linear === false), true);
    E.linear.input.disabled = !p || p.linear === undefined || p.linear === null;
    E.nowBody.innerHTML = '';
    if (!AC.host.available) { E.nowBody.appendChild(ui.h('p', { class: 'help', text: T('nowNoHost') })); return; }
    if (!p) { E.nowBody.appendChild(ui.h('p', { class: 'help', text: T('nowReading') })); return; }
    var v = cap.placedVersion(), d = S.doc || {};
    var lines = [];
    if (p.clips) lines.push(v ? T(d.hash === (d.render || {}).hash ? 'nowLatest' : 'nowChanged', { v: v }) : T('nowTrack', { track: trackName(), n: p.clips }) + (p.ours ? '' : T('notOurs')));
    else lines.push(T('nowNone'));
    if (p.editable && p.editable.clips) lines.push(T('nowEditable', { track: editName(), n: p.editable.clips }));
    lines.forEach(function (l) { E.nowBody.appendChild(ui.h('p', { class: 'help', text: l })); });
    var row = ui.h('div', { class: 'btn-row' });
    row.appendChild(ui.button({ label: T('openRenderFolder'), icon: 'folder', small: true, kind: 'ghost', onClick: function () { AC.sys.openFolder(AC.sys.join(S.workdir, 'captions_render')); } }));
    if (p.clips || (p.editable && p.editable.clips)) {
      row.appendChild(ui.confirmClick(ui.button({ label: T('remove'), icon: 'trash', small: true, kind: 'ghost-danger' }), function () { clearTimeline(); }, T('removeConfirm')));
    }
    E.nowBody.appendChild(row);
  }

  function setLinear(match) {
    if (!S.seq) return;
    AC.host.json('bac_captions_setLinear', !match, S.seq.id).then(function (r) {
      if (S.placed) S.placed.linear = r.linear;
      ui.toast(match ? T('linearOff') : T('linearOn'), 'check');
      paint();
    }, function (e) { ui.toast(T('linearFailed', { msg: U.errMsg(e) }), { kind: 'err' }); paint(); });
  }
  function clearTimeline() {
    AC.host.json('bac_captions_clear', S.seq.id, 'all').then(function (r) {
      ui.toast(T('removed', { n: r.n || 0 }), 'trash');
      cap.hostStatus();
    }, function (e) { ui.toast(T('removeFailed', { msg: U.errMsg(e) }), { kind: 'err' }); });
  }

  /* ---------------------------------------------------------------- apply */
  function apply() {
    var ctx = S.ctx, mode = st().out;
    if (!S.doc || !S.seq) return;
    var seqId = S.seq.id, wd = S.workdir;
    var task = new AC.Task({ tool: 'captions', action: 'apply', title: AC.t('cap.title'), label: T('applyLabel'), kind: 'engine' });
    task.seqName = S.seq.name;
    var plan = mode === 'overlay' ? [{ id: 'render', label: T('stRender'), w: 0.82 }, { id: 'place', label: T('stPlace'), w: 0.13 }].concat(st().srtToo ? [{ id: 'srt', label: T('stSrt'), w: 0.05 }] : [])
      : mode === 'srt' ? [{ id: 'srt', label: T('stSrt'), w: 0.3 }, { id: 'place', label: T('stTrack'), w: 0.7 }]
      : [{ id: 'mogrt', label: T('stMogrt'), w: 0.3 }, { id: 'place', label: T('stPlace'), w: 0.7 }];
    task.plan(plan);
    AC.history.track(task);
    var job = null, stop = false;
    task._cancel = function () { stop = true; if (job && job.isActive()) job.cancel(); else task.fail(U.err('CANCELLED', T('cancelled'))); };
    ctx.track(task, { title: T('applying'), note: T('applyNote', { track: mode === 'mogrt' ? editName() : trackName() }),
      onDone: function (res) { showResult(res, task); }, onRetry: function () { apply(); } });
    var engine = function (action, params, stageId) {
      task.stage({ id: stageId });
      job = cap.worker(action, params, { workdir: wd });
      job.on('progress', function () { task.progress(job.pct); });
      job.on('warn', function (m) { task.log(m, 'WARN'); });
      return job.promise.then(function (r) { task.stageDone(stageId); return r; });
    };
    // pending edits first (they wait for the busy gate, so the gate is taken only after them)
    cap.idle().then(function () {
      if (stop || !task.isActive()) throw U.err('CANCELLED', T('cancelled'));
      cap.hold(task);
      if (mode === 'overlay') {
        var out = {};
        return engine('render', { codec: st().codec || 'qtrle', band: st().band || 'auto' }, 'render').then(function (r) {
          out.render = r;
          if (stop) throw U.err('CANCELLED', T('cancelled'));
          var p = r.plan;
          if (!p || !p.path) throw U.err('NO_PLAN', T('noPlan'));
          task.stage({ id: 'place' });
          return AC.host.exec('bac_captions_applyOverlay', [p.path, p.xNorm, p.yNorm, !!p.relink, seqId, p.track || ''], { json: true, timeout: 120000 });
        }).then(function (pl) {
          out.place = pl; task.stageDone('place');
          if (!st().srtToo) return out;
          return engine('srt', {}, 'srt').then(function (s) { out.srt = s; return out; });
        }).then(function (o) { o.mode = 'overlay'; return o; });
      }
      if (mode === 'srt') {
        return engine('srt', {}, 'srt').then(function (s) {
          task.stage({ id: 'place' });
          return AC.host.exec('bac_captions_applySrt', [s.path, seqId], { json: true, timeout: 60000 }).then(function (pl) { task.stageDone('place'); return { mode: 'srt', srt: s, place: pl }; });
        });
      }
      return engine('mogrt', { enable: true }, 'mogrt').then(function (m) {
        var items = (m.plan && m.plan.items) || [];
        task.stage({ id: 'place', sub: T('graphicsN', { n: items.length }) });
        return AC.host.exec('bac_captions_mogrtBegin', [m.plan.track, seqId], { json: true, timeout: 60000 }).then(function (b) {
          var done = 0, failed = 0;
          function next() {
            if (done >= items.length) return Promise.resolve();
            if (stop) throw U.err('CANCELLED', T('cancelledPart', { done: done, n: items.length }));
            var part = items.slice(done, done + CHUNK);
            return AC.host.exec('bac_captions_mogrtItems', [part, b.track, seqId], { json: true, timeout: 180000 }).then(function (r) {
              failed += r.failed || 0; done += part.length;
              task.progress(done / items.length * 100);
              return next();
            });
          }
          return next().then(function () { task.stageDone('place'); return { mode: 'mogrt', mogrt: m, place: { n: items.length - failed, failed: failed, track: b.track } }; });
        });
      });
    }).then(function (res) {
      if (!task.isActive()) return;
      task.summary = summary(res);
      task.done(res);
      cap.reload();
      cap.hostStatus();
    }, function (e) { task.fail(e); cap.hostStatus(); });
  }
  function summary(r) {
    if (r.mode === 'overlay') return T(r.place && r.place.mode === 'relink' ? 'sumRelinked' : 'sumPlaced', { v: r.render.version });
    if (r.mode === 'srt') return T('sumSrt', { n: r.srt.cues });
    return T('sumMogrt', { n: r.place.n });
  }

  /* ---------------------------------------------------------------- result card */
  function showResult(res, task) {
    var ctx = S.ctx, title, sub, stats, files = [];
    if (res.mode === 'overlay') {
      var r = res.render, pl = res.place || {};
      title = pl.mode === 'relink' ? T('resUpdated') : T('resPlaced');
      sub = (r.skipped ? T('resSkipped') : T('resRender', { dur: U.dtk(r.seconds || 0) }) + (r.chunks ? T('resChunks', { done: r.rendered_chunks || 0, n: r.chunks }) : '') + '. ') +
            (pl.mode === 'relink' ? T('resRelinkNote') : T('resPlaceNote', { track: trackName() }));
      stats = [{ label: T('stCaptions'), value: String(pages()) }, { label: T('stVersion'), value: 'v' + (r.version || (r.plan || {}).version || 1), accent: true }, { label: T('stSize'), value: r.size_mb !== undefined ? mb(r.size_mb) : '-' }];
      files.push(['Overlay', r.path]);
      if (res.srt) files.push(['SRT', res.srt.path]);
    } else if (res.mode === 'srt') {
      title = T('resSrt');
      sub = T('resSrtSub', { n: res.srt.cues });
      stats = [{ label: T('stCaptions'), value: String(res.srt.cues) }, { label: T('stFormat'), value: 'SRT' }, { label: T('stTime'), value: U.dtk(task.elapsed()) }];
      files.push(['SRT', res.srt.path]);
    } else {
      title = T('sumMogrt', { n: res.place.n });
      sub = T('resMogrtSub', { track: editName() }) + (res.place.failed ? ' ' + T('resMogrtFailed', { n: res.place.failed }) : '');
      stats = [{ label: T('stGraphics'), value: String(res.place.n) }, { label: T('stFailed'), value: String(res.place.failed || 0) }, { label: T('stTime'), value: U.dtk(task.elapsed()) }];
      files.push([T('fileFolder'), res.mogrt.dir]);
    }
    var extra = ui.h('div', { class: 'sec', style: { gap: '8px' } });
    var fl = ui.h('div', { class: 'cap-result-files' });
    files.forEach(function (f) { if (f[1]) fl.appendChild(ui.h('div', { html: '<b>' + U.esc(f[0]) + ':</b> ' + U.esc(f[1]) })); });
    extra.appendChild(fl);
    if (res.mode === 'overlay' && S.placed && S.placed.linear === true) {
      extra.appendChild(ui.alert({ kind: 'info', title: T('linearHintTitle'), text: T('linearHintText'),
        actions: [{ label: T('linearHintAction'), onClick: function () { setLinear(true); } }] }));
    }
    var warn = (task.warnings || []).length ? { title: T('engineNotes', { n: task.warnings.length }), text: task.warnings.slice(0, 2).join(' ') } : null;
    ctx.result({ title: title, sub: sub, statsList: stats, extra: extra, warn: warn,
      actions: [{ label: T('openFolder'), icon: 'folder', onClick: function () { AC.sys.openFolder(res.mode === 'mogrt' ? res.mogrt.dir : AC.sys.dirname(files[0][1] || AC.sys.join(S.workdir, 'x'))); } }],
      onDelete: res.mode === 'srt' ? null : function () {
        AC.host.json('bac_captions_clear', S.seq.id, res.mode === 'mogrt' ? 'editable' : 'overlay').then(function (r) {
          ui.toast(T('removed', { n: r.n || 0 }), 'trash'); cap.hostStatus(); AC.router.go('tool/captions/ekspor');
        }, function (e) { ui.toast(U.errMsg(e), { kind: 'err' }); });
      },
      primary: { label: T('backToEditor'), icon: 'cc', kbd: false, onClick: function () { AC.router.go('tool/captions/' + S.tab); } } });
  }
})();
