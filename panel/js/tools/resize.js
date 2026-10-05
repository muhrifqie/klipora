/* resize.js: Auto Resize (tool id "resize", engine engine/ac/tools/resize.py, host host/38_resize.jsx).
   Settings: format 9:16 / 1:1 / 4:5 / 16:9, method (Smart | Focus + blur | Premiere Auto Reframe), camera motion
   slow / normal / fast / still, advanced (subject, enlarge, ignore taskbar, background, render fps).
   Smart + Focus: engine "analyze" -> preview (contact sheet PNG of the camera path + stats + advice) ->
     optional review (one row per camera move; unchecked = camera holds) ->
     Smart: engine "apply" -> plan {kind: "reframe"} -> host clone + frame size + Motion keys per clip (chunks);
     Focus: engine "render" -> MP4 -> host import + new sequence.
   Premiere Auto Reframe: host seq.autoReframeSequence -> poll isDoneAnalyzingForVideoEffects.
   The original sequence is never changed. AC.resize exposes small helpers for tests. Docs: docs/tools/resize.md. */
(function () {
  'use strict';
  var AC = window.AC, U = AC.util, ui = AC.ui;
  var KEY_CHUNK = 40;

  // Translated labels are built at render time (a live language switch re-renders the page): hint(t), engines(), speeds().
  var TARGETS = [
    { value: '9x16', label: '9:16', size: [1080, 1920], ratio: 9 / 16 },
    { value: '1x1', label: '1:1', size: [1080, 1080], ratio: 1 },
    { value: '4x5', label: '4:5', size: [1080, 1350], ratio: 4 / 5 },
    { value: '16x9', label: '16:9', size: [1920, 1080], ratio: 16 / 9 }
  ];
  function hint(t) { return AC.t('resize.fmtHint.' + t.value); }
  function engines() {
    return [
      { value: 'smart', title: AC.t('resize.eng.smart'), desc: AC.t('resize.eng.smartDesc') },
      { value: 'focus', title: AC.t('resize.eng.focus'), desc: AC.t('resize.eng.focusDesc'), tag: AC.t('resize.eng.focusTag'), tagKind: 'line' },
      { value: 'native', title: AC.t('resize.eng.native'), desc: AC.t('resize.eng.nativeDesc') }
    ];
  }
  function speeds() {
    return ['slow', 'normal', 'fast', 'none'].map(function (v) {
      return { value: v, label: AC.t('resize.speed.' + v), hint: AC.t('resize.speed.' + v + 'Hint'), values: { speed: v } };
    });
  }
  var NATIVE_PRESET = { slow: 'slower', normal: 'default', fast: 'faster', none: 'slower' };

  function target(v) { for (var i = 0; i < TARGETS.length; i++) if (TARGETS[i].value === v) return TARGETS[i]; return TARGETS[0]; }
  function fileUrl(p) { return 'file:///' + String(p).replace(/\\/g, '/').split('/').map(encodeURIComponent).join('/').replace(/^([A-Za-z])%3A/, '$1:'); }
  // Engine params from the tool state (scope is added by ctx.run from the source card).
  function engineParams(st) {
    return { target: st.target, layout: st.engine === 'focus' ? 'focus' : 'crop', speed: st.speed, subject: st.subject,
             zoom: st.zoom, bands: !!st.bands, fps: st.fps, bg: st.bg };
  }
  function sameAspect(s, t) { return !!s && s.width && s.height && Math.abs(s.width / s.height - t.ratio) < 0.02; }
  // ~45 output frames/s measured for 1080x1920 focus renders; + 0,77 MB per second of video at the 10 Mbps cap.
  function renderEstimate(dur, fps) { return { secs: dur * (fps || 30) / 45, mb: Math.round(dur * (10 * 0.6 + 0.16) / 8) }; }
  function chunks(list, n) { var out = []; for (var i = 0; i < list.length; i += n) out.push(list.slice(i, i + n)); return out; }

  AC.resize = { targets: TARGETS, engineParams: engineParams, nativePreset: NATIVE_PRESET, fileUrl: fileUrl, renderEstimate: renderEstimate, chunks: chunks };

  AC.tools.register({
    id: 'resize', group: 'publikasi', order: 1,
    icon: 'resize',
    badges: ['baru'],
    text: function (t) {
      return { title: t('resize.title'), tab: t('resize.tab'), desc: t('resize.desc'), lead: t('resize.lead'), cta: t('resize.cta'),
               next: [{ tool: 'captions', reason: t('resize.nextCaptions') }] };
    },
    source: { scopes: ['all', 'inout'] },
    defaults: { target: '9x16', engine: 'smart', speed: 'normal', subject: 'auto', zoom: 1, bands: true, fps: 30, bg: 'blur' },

    render: function (el, ctx) {
      var st = ctx.state, last = null;      // last = {data, params, engine} of the newest analysis
      el.appendChild(ui.h('div', { class: 'tool-hero', html: '<span class="tool-ic">' + ui.icon('resize') + '</span><div><p>' + U.esc(ctx.def.lead) + '</p><div class="tags">' + ui.badge('baru') + '</div></div>' }));

      /* ---------------- Format */
      var fmt = ui.section({ title: AC.t('resize.format') });
      var fmtHelp = ui.h('p', { class: 'help resize-fmt-help' });
      var fmtSeg = ui.segmented({ ariaLabel: AC.t('resize.format'), value: st.target, options: TARGETS.map(function (t) { return { value: t.value, label: t.label }; }),
        onChange: function (v) { st.target = v; ctx.save(); paint(); } });
      fmt.add(fmtSeg); fmt.add(fmtHelp);
      el.appendChild(fmt.el);

      /* ---------------- Engine */
      var eng = ui.section({ title: AC.t('resize.method') });
      var engCards = ui.radioCards({ ariaLabel: AC.t('resize.method'), value: st.engine, options: engines(), onChange: function (v) { st.engine = v; ctx.save(); paint(); } });
      eng.add(engCards);
      el.appendChild(eng.el);

      /* ---------------- Speed */
      var cam = ui.section({ title: AC.t('resize.motion') });
      var speed = ui.presets({ ariaLabel: AC.t('resize.motion'), options: speeds(), value: st.speed, onChange: function (v) { st.speed = v; ctx.save(); paint(); } });
      cam.add(speed);
      el.appendChild(cam.el);

      /* ---------------- Advanced */
      var adv = ui.advanced({ title: AC.t('common.advanced'), key: 'resize' });
      var subjSeg = ui.segmented({ small: true, ariaLabel: AC.t('resize.subject'), value: st.subject,
        options: [{ value: 'auto', label: AC.t('resize.subj.auto') }, { value: 'screen', label: AC.t('resize.subj.screen') }, { value: 'face', label: AC.t('resize.subj.face') }],
        onChange: function (v) { st.subject = v; ctx.save(); } });
      var subjField = ui.inlineField({ label: AC.t('resize.subject'), help: AC.t('resize.subjectHelp'), control: subjSeg.el });
      var zoom = ui.slider({ label: AC.t('resize.zoom'), min: 100, max: 160, step: 5, value: Math.round((st.zoom || 1) * 100), unit: '%', decimals: 0,
        help: AC.t('resize.zoomHelp'), onInput: function (v) { st.zoom = v / 100; ctx.save(); } });
      var bands = ui.switch({ label: AC.t('resize.bands'), help: AC.t('resize.bandsHelp'), checked: st.bands,
        onChange: function (v) { st.bands = v; ctx.save(); } });
      var bgSeg = ui.segmented({ small: true, ariaLabel: AC.t('resize.bg'), value: st.bg, options: [{ value: 'blur', label: AC.t('resize.bgBlur') }, { value: 'dark', label: AC.t('resize.bgDark') }],
        onChange: function (v) { st.bg = v; ctx.save(); } });
      var bgField = ui.inlineField({ label: AC.t('resize.bg'), help: AC.t('resize.bgHelp'), control: bgSeg.el });
      var fpsSeg = ui.segmented({ small: true, ariaLabel: AC.t('resize.fps'), value: st.fps, options: [{ value: 30, label: '30 fps' }, { value: 60, label: '60 fps' }],
        onChange: function (v) { st.fps = v; ctx.save(); paint(); } });
      var fpsField = ui.inlineField({ label: AC.t('resize.fps'), help: AC.t('resize.fpsHelp'), control: fpsSeg.el });
      adv.add([subjField, zoom, bands, bgField, fpsField]);
      el.appendChild(adv.el);

      var noteBox = ui.h('div', { class: 'resize-note' });
      el.appendChild(noteBox);

      // st.target stays the user's own choice. When the source already has that aspect (e.g. the 9:16 result is the
      // active sequence right after a run), show and use the first other format without overwriting the choice.
      function effTarget(s) {
        var t = target(st.target);
        if (!sameAspect(s, t)) return t;
        return TARGETS.filter(function (x) { return !sameAspect(s, x); })[0] || t;
      }

      function paint() {
        var s = AC.seq.peek(), t = effTarget(s), native = st.engine === 'native', focus = st.engine === 'focus';
        TARGETS.forEach(function (x) { fmtSeg.disable(x.value, sameAspect(s, x), sameAspect(s, x) ? AC.t('resize.already', { fmt: x.label }) : ''); });
        if (fmtSeg.get() !== t.value) fmtSeg.set(t.value, true);
        fmtHelp.textContent = hint(t) + (native ? ' ' + AC.t('resize.nativeSize', { size: t.size[0] + 'x' + t.size[1] }) : '');
        fmt.setAside(native ? '' : t.size[0] + 'x' + t.size[1]);
        speed.seg.disable('none', native, native ? AC.t('resize.noStill') : '');
        if (native && st.speed === 'none') { st.speed = 'normal'; speed.set('normal', true); }
        subjField.hidden = native; zoom.el.hidden = native; bands.el.hidden = native;
        bgField.hidden = !focus; fpsField.hidden = !focus;
        noteBox.innerHTML = '';
        if (focus && s) {
          var sc = ctx.scope(), est = renderEstimate(sc && sc.kind === 'inout' && sc.t1 > sc.t0 ? sc.t1 - sc.t0 : s.duration, st.fps);
          noteBox.appendChild(ui.alert({ kind: 'info', title: AC.t('resize.focusNote', { dur: U.dtk(Math.max(5, est.secs)), mb: U.int(est.mb) }),
            text: AC.t('resize.focusNoteText') }));
        } else if (native) {
          noteBox.appendChild(ui.alert({ kind: 'info', title: AC.t('resize.nativeNote'), text: AC.t('resize.nativeNoteText') }));
        }
        ctx.dock({ primary: { label: native ? AC.t('resize.ctaNative') : ctx.def.cta, icon: native ? 'resize' : 'play', disabled: !s, onClick: run } });
      }
      ctx._paint = paint;
      paint();

      /* ---------------- run */
      function run() {
        if (st.engine === 'native') { runNative(); return; }
        var params = engineParams(st), engine = st.engine;
        params.target = effTarget(AC.seq.peek()).value;
        ctx.run({ action: 'analyze', params: params, title: AC.t('resize.runAnalyze'),
          stages: [{ id: 'read', label: AC.t('resize.st.read'), w: 0.04 }, { id: 'scan', label: AC.t('resize.st.scan'), w: 0.74 }, { id: 'path', label: AC.t('resize.st.path'), w: 0.07 }, { id: 'preview', label: AC.t('resize.st.preview'), w: 0.15 }],
          onResult: function (data, task) { last = { data: data, params: params, engine: engine, secs: task.elapsed(), seq: task.job && task.job.seq }; showPreview(); } });
      }

      function showPreview() {
        var d = last.data, engine = last.engine, t = target(d.target), s = d.stats || {};
        var extra = ui.h('div', { class: 'resize-prev' });
        var img = ui.h('img', { class: 'resize-sheet', alt: AC.t('resize.sheetAlt', { fmt: t.label }), src: fileUrl(d.preview) + '?v=' + Date.now(), title: AC.t('resize.sheetTip') });
        img.addEventListener('click', function () { AC.sys.openFolder(d.preview); });
        extra.appendChild(img);
        extra.appendChild(ui.h('p', { class: 'help', text: AC.t('resize.sheetHelp') }));
        var kinds = (d.content || []).map(function (c) { return AC.t(c.kind === 'face' ? 'resize.kind.face' : c.kind === 'screen' ? 'resize.kind.screen' : 'resize.kind.still'); });
        var kindTxt = kinds.filter(function (k, i) { return kinds.indexOf(k) === i; }).join(AC.t('resize.and')) || AC.t('resize.kind.video');
        var sub = AC.t('resize.pvSub', { moves: s.moves || 0, jumps: s.jumps || 0, n: s.shots || 0 }) +
          (s.held ? AC.t('resize.pvHeld', { n: s.held }) : '') + '. ' + AC.t('resize.pvContent', { kinds: kindTxt, dur: U.dtk(last.secs) });
        var warn = null;
        if (d.advice && engine === 'smart') warn = { title: AC.t('resize.warnCrop'), text: d.advice.text };
        else if (d.warnings && d.warnings.length) warn = { title: AC.t('resize.warnCheck'), text: d.warnings.join(' ') };
        var actions = [];
        if (d.advice && engine === 'smart') actions.push({ label: AC.t('resize.useFocus'), icon: 'refresh', kind: '', onClick: function () { st.engine = 'focus'; engCards.set('focus', true); ctx.save(); paint(); ctx.back(); run(); } });
        actions.push({ label: AC.t('resize.changeSettings'), onClick: function () { ctx.back(); } });
        var nRows = (s.moves || 0) + (s.jumps || 0);
        if (engine === 'focus') {
          var est = renderEstimate(d.duration, last.params.fps);
          sub += ' ' + AC.t('resize.pvRender', { dur: U.dtk(Math.max(5, est.secs)), mb: U.int(est.mb) });
        }
        ctx.result({
          title: AC.t('resize.pvTitle', { fmt: t.label }),
          sub: sub,
          statsList: [{ label: AC.t('resize.format'), value: t.label, accent: true }, { label: AC.t('resize.statMoves'), value: String(s.moves || 0) }, { label: AC.t('resize.statJumps'), value: String(s.jumps || 0) }],
          extra: extra, warn: warn, actions: actions,
          onEdit: nRows ? openReview : null,
          primary: { label: AC.t(engine === 'focus' ? 'resize.renderVideo' : 'resize.makeSeq', { fmt: t.label }), icon: engine === 'focus' ? 'play' : 'resize', onClick: function () { apply(d.review); } }
        });
      }

      function openReview() {
        var engine = last.engine, t = target(last.data.target);
        ctx.review({ file: last.data.review, unit: AC.i18n.raw('resize.unit'), verbOn: AC.t('resize.verbOn'), verbOff: AC.t('resize.verbOff'), bulkAction: null, cancelLabel: AC.t('common.back'),
          filters: [{ id: 'move', label: AC.t('resize.fMove'), test: function (it) { return it.kind === 'move'; } }, { id: 'jump', label: AC.t('resize.fJump'), test: function (it) { return it.kind === 'jump'; } }],
          toggleText: function (it) { return AC.t(it.on ? 'resize.toggleHold' : 'resize.toggleUse'); },
          legendExtra: function (on, all) { return '<span>' + AC.i18n.html('resize.legendHeld', { n: all - on }) + '</span>'; },
          title: function (on, all) { return AC.i18n.html('resize.rvTitle', { n: on }) + '<small>' + AC.i18n.html('resize.rvOf', { n: all }) + '</small>'; },
          primary: { label: function (n) { return AC.t(engine === 'focus' ? 'resize.applyRender' : 'resize.applyMake', { fmt: t.label, n: n }); },
                     onApply: function (items, doc, file) { apply(file); } } });
        // every row may be off (all held) -> keep the dock usable
        if (ctx.reviewList && !ctx.reviewList.count()) AC.ui.dock.update({ disabled: false });
      }

      // The ANALYSED sequence, re-read now: after a run the new 9:16 sequence is the active one, and "Ubah pilihan"
      // must not reframe (or render) that result instead of the original.
      function analysedSeq() {
        var a = last.seq;
        if (!a || !a.id) return undefined;
        return AC.host.json('bac_seqInfo', 'full', a.id).then(function (s) {
          if (!s || s.id !== a.id) throw U.err('NO_SEQ', AC.t('resize.errNoSeq', { name: a.name }), AC.t('resize.errNoSeqHint'));
          return s;
        });
      }

      function apply(reviewFile) {
        var params = last.params, engine = last.engine;
        if (engine === 'focus') {
          ctx.run({ action: 'render', review: reviewFile, params: params, seq: analysedSeq(), title: AC.t('resize.renderVideo', { fmt: target(params.target).label }),
            stages: [{ id: 'read', label: AC.t('resize.st.read'), w: 0.03 }, { id: 'scan', label: AC.t('resize.st.scan'), w: 0.2 }, { id: 'path', label: AC.t('resize.st.path'), w: 0.02 }, { id: 'audio', label: AC.t('resize.st.audio'), w: 0.05 }, { id: 'render', label: AC.t('resize.st.render'), w: 0.7 }],
            onResult: function (data) { importRender(data); },
            onError: function (err, task) { if (err && err.code === 'CANCELLED') dropPartials(task); return false; } });
          return;
        }
        ctx.run({ action: 'apply', review: reviewFile, params: params, seq: analysedSeq(), title: AC.t('resize.runApply'),
          onResult: function (data) { applyReframe(data.plan, data); } });
      }

      // Cancel = taskkill /F of the engine, so it cannot clean up: remove the unfinished video and temp audio
      // (engine names: <name>.part.mp4, audio_<time>.m4a in <workdir>/resize). Finished renders are never touched.
      function dropPartials(task) {
        var dir = task && task.job && task.job.workdir ? AC.sys.join(task.job.workdir, 'resize') : '';
        if (!dir || !AC.sys.exists(dir)) return;
        setTimeout(function () {
          AC.sys.list(dir).forEach(function (f) {
            if (/\.part\.mp4$/i.test(f) || /^audio_\d+\.m4a$/i.test(f)) AC.sys.remove(AC.sys.join(dir, f));
          });
        }, 500);
      }

      /* ---------------- Smart: plan -> new sequence + Motion keys */
      function applyReframe(plan, data) {
        if (!plan || plan.kind !== 'reframe') { ctx.error(U.err('BAD_PLAN', AC.t('resize.errBadPlan'))); return; }
        var parts = chunks(plan.clips || [], KEY_CHUNK);
        var task = new AC.Task({ tool: 'resize', action: 'host', title: ctx.def.title, label: AC.t('resize.makeSeq', { fmt: plan.label }), kind: 'host',
          stages: [{ id: 'clone', label: AC.t('resize.makeSeq', { fmt: plan.label }), w: 0.15 }, { id: 'keys', label: AC.t('resize.st.keys', { n: (plan.clips || []).length }), w: 0.8 }, { id: 'open', label: AC.t('resize.st.open'), w: 0.05 }] });
        AC.history.track(task);
        AC.seq.hold(task.promise);
        var cancelled = false, made = null, tot = { done: 0, keys: 0, missing: 0, replaced: 0 };
        task._cancel = function () { cancelled = true; };
        ctx.track(task, { title: AC.t('resize.trackMake', { fmt: plan.label }), note: AC.t('resize.trackNote'),
          onDone: function (r) { showReframeResult(r, task, data); }, onRetry: function () { applyReframe(plan, data); } });
        var io = plan.inout || [null, null];
        task.stage({ id: 'clone' });
        AC.host.exec('bac_resize_begin', [plan.name, plan.w, plan.h, plan.seq_id || AC.seq.locked() || '', io[0], io[1]], { json: true, timeout: 60000 }).then(function (c) {
          made = c; task.stageDone('clone');
          task.stage({ id: 'keys', sub: AC.t('resize.origSafe') });
          var k = 0;
          function next() {
            if (k >= parts.length) return Promise.resolve();
            if (cancelled) throw U.err('CANCELLED', AC.t('resize.errCancelled', { name: made.name }), AC.t('resize.errCancelledHint'));
            var part = parts[k];
            return AC.host.exec('bac_resize_keys', [made.id, plan.track, part, k === parts.length - 1], { json: true, timeout: 120000 }).then(function (r) {
              ['done', 'keys', 'missing', 'replaced'].forEach(function (x) { tot[x] += (r && r[x]) || 0; });
              k++; task.progress(k / parts.length * 100);
              return next();
            });
          }
          return next();
        }).then(function () {
          task.stageDone('keys');
          task.stage({ id: 'open' });
          return AC.host.exec('bac_resize_finish', [made.id], { json: true, timeout: 30000 });
        }).then(function (fin) {
          task.stageDone('open');
          task.summary = AC.t('resize.summary', { fmt: plan.label, n: tot.done, keys: tot.keys });
          task.done({ made: made, fin: fin, tot: tot, plan: plan });
        }).catch(function (e) {
          if (!(e && e.code === 'CANCELLED' && made)) { task.fail(e); return; }
          // Cancelled between chunks: delete the half-made sequence (ours, this session) and reopen the source, so a
          // cancel leaves the project as it was (same as Angle). If that fails the message names the partial sequence.
          AC.host.json('bac_deleteSequence', made.id).then(function () {
            return AC.host.json('bac_openSequence', made.origId).catch(function () { return null; });
          }).then(function () { task.fail(U.err('CANCELLED', AC.t('resize.cancelledClean'))); },
                  function () { task.fail(e); });
        });
      }

      function showReframeResult(r, task, data) {
        var p = r.plan, made = r.made, tot = r.tot;
        var warns = [];
        if (tot.missing) warns.push(AC.t('resize.warnMissing', { n: tot.missing }));
        if (tot.replaced) warns.push(AC.t('resize.warnReplaced', { n: tot.replaced }));
        if (made.frameOk === false || (made.w && made.w !== p.w)) warns.push(AC.t('resize.warnFrame', { want: p.w + 'x' + p.h, got: made.w + 'x' + made.h }));
        ((last && last.data && last.data.warnings) || []).forEach(function (w) { warns.push(w); });
        ctx.result({
          title: AC.t('resize.resTitle', { fmt: p.label }),
          sub: AC.t('resize.resSub', { n: tot.done, keys: tot.keys, dur: U.dtk(task.elapsed()) }),
          statsList: [{ label: AC.t('resize.format'), value: p.label, accent: true }, { label: AC.t('resize.statSize'), value: p.w + 'x' + p.h }, { label: AC.t('resize.statKeys'), value: U.int(tot.keys) }],
          warn: warns.length ? { title: AC.t('resize.warnCheck'), text: warns.join(' ') } : null,
          original: { id: made.origId, name: made.origName },
          onEdit: last && last.data.stats && (last.data.stats.moves + last.data.stats.jumps) ? openReview : null,
          onDelete: function () { deleteSeq(made); },
          next: ctx.def.next
        });
      }

      /* ---------------- Focus: rendered MP4 -> new sequence */
      function importRender(data) {
        var plan = data.plan || {};
        var task = new AC.Task({ tool: 'resize', action: 'host', title: ctx.def.title, label: AC.t('resize.importLabel', { fmt: target(last.params.target).label }), kind: 'host',
          stages: [{ id: 'import', label: AC.t('resize.st.import'), w: 1 }] });
        AC.history.track(task);
        ctx.track(task, { title: AC.t('resize.trackImport'), note: AC.t('resize.trackImportNote'),
          onDone: function (r) { showRenderResult(r, data, task); }, onRetry: function () { importRender(data); } });
        task.stage({ id: 'import' });
        var orig = (last && last.seq) || AC.seq.peek();
        AC.host.exec('bac_resize_importRender', [plan.path || data.path, plan.name || ''], { json: true, timeout: 120000 }).then(function (r) {
          r.origId = orig ? orig.id : ''; r.origName = orig ? orig.name : '';
          task.stageDone('import');
          task.summary = data.summary || AC.t('resize.rendered');
          task.done(r);
        }).catch(function (e) { task.fail(e); });
      }

      function showRenderResult(r, data, task) {
        var t = target(last.params.target), rd = data.render || {};
        ctx.result({
          title: AC.t('resize.videoTitle', { fmt: t.label }),
          sub: AC.t('resize.videoSub', { size: (rd.w || r.w) + 'x' + (rd.h || r.h), fps: String(rd.fps || last.params.fps), mb: U.dec(data.size_mb || 0, 1), dur: U.dtk((rd.secs || 0) + task.elapsed()) }),
          statsList: [{ label: AC.t('resize.format'), value: t.label, accent: true }, { label: AC.t('resize.statDur'), value: U.mmss(data.duration || r.duration || 0) }, { label: AC.t('resize.statFile'), value: U.dec(data.size_mb || 0, 1) + ' MB' }],
          original: r.origId ? { id: r.origId, name: r.origName } : null,
          actions: [{ label: AC.t('resize.openFolder'), icon: 'folder', onClick: function () { AC.sys.openFolder(AC.sys.dirname(data.path)); } },
                    { label: AC.t('resize.copyPath'), icon: 'copy', onClick: function () { ui.copy(data.path, AC.t('resize.filePath')); } }],
          onDelete: function () { deleteSeq(r); },
          next: ctx.def.next
        });
      }

      /* ---------------- Premiere Auto Reframe */
      function runNative() {
        var s = AC.seq.peek(), t = effTarget(s);
        if (!s) { ui.toast(AC.t('resize.openSeq'), 'alert'); return; }
        var r = t.value.split('x'), preset = NATIVE_PRESET[st.speed] || 'default';
        var expect = Math.max(6, (s.duration || 30) * 0.35), limit = Math.max(60, (s.duration || 30) * 2);
        var task = new AC.Task({ tool: 'resize', action: 'native', title: ctx.def.title, label: 'Auto Reframe ' + t.label, kind: 'host',
          stages: [{ id: 'make', label: AC.t('resize.st.make'), w: 0.1 }, { id: 'wait', label: AC.t('resize.st.wait'), w: 0.9 }] });
        AC.history.track(task);
        var stop = false, timer = null;
        task._cancel = function () { stop = true; clearTimeout(timer); task.fail(U.err('CANCELLED', AC.t('resize.nativeStop'))); };
        ctx.track(task, { title: 'Premiere Auto Reframe ' + t.label, note: AC.t('resize.nativeTrackNote'),
          onDone: function (res) { showNativeResult(res, task, t); }, onRetry: runNative });
        task.stage({ id: 'make' });
        AC.host.exec('bac_resize_native', [Number(r[0]), Number(r[1]), preset, s.name + ' (' + t.value + ' Premiere)', AC.seq.locked() || ''], { json: true, timeout: 60000 }).then(function (made) {
          task.stageDone('make');
          task.stage({ id: 'wait', sub: AC.t('resize.preset', { name: preset }) });
          var t0 = Date.now();
          function poll() {
            if (stop) return;
            AC.host.json('bac_resize_nativeStatus', made.id).then(function (st2) {
              if (stop) return;
              var el = (Date.now() - t0) / 1000;
              if (st2 && st2.done) { task.stageDone('wait'); task.summary = 'Auto Reframe ' + t.label + ' (' + made.w + 'x' + made.h + ')'; task.done({ made: made, done: true, secs: el }); return; }
              if (el > limit) { task.stageDone('wait', null, AC.t('resize.stillRunning')); task.done({ made: made, done: false, secs: el }); return; }
              task.progress(Math.min(95, el / expect * 100));
              timer = setTimeout(poll, 1000);
            }, function (e) { if (!stop) task.fail(e); });
          }
          poll();
        }).catch(function (e) { task.fail(e); });
      }

      function showNativeResult(res, task, t) {
        var m = res.made;
        ctx.result({
          title: AC.t('resize.nativeTitle'),
          sub: AC.t('resize.nativeSub', { size: m.w + 'x' + m.h, preset: m.preset, dur: U.dtk(task.elapsed()) }) +
               (res.done ? '' : ' ' + AC.t('resize.nativeBusy')) + ' ' + AC.t('resize.origSafeFull'),
          statsList: [{ label: AC.t('resize.statSize'), value: m.w + 'x' + m.h, accent: true }, { label: AC.t('resize.statPreset'), value: m.preset }, { label: AC.t('resize.statAnalysis'), value: AC.t(res.done ? 'resize.analysisDone' : 'resize.analysisRunning') }],
          warn: { title: AC.t('resize.nativeWarn'), text: AC.t('resize.nativeWarnText', { size: t.size[0] + 'x' + t.size[1] }) },
          original: { id: m.origId, name: m.origName },
          onDelete: function () { deleteSeq(m, true); },
          next: ctx.def.next
        });
      }

      function deleteSeq(m, native) {
        AC.host.json('bac_deleteSequence', m.id).then(function () {
          ui.toast(AC.t('resize.deleted', { name: m.name }), 'trash'); ctx.back();
          if (native) AC.host.json('bac_resize_dropBin').then(null, function (e) { AC.log.warn('resize: Auto Reframe bin kept (' + U.errMsg(e) + ')'); });
        }, function (e) { ui.toast(U.errMsg(e), { kind: 'err' }); });
      }
    },
    onShow: function (ctx) { if (ctx._paint) ctx._paint(); },
    onScope: function (scope, ctx) { if (ctx._paint && ctx.pane() === 'main') ctx._paint(); },
    onSeq: function (lite, ctx) { if (ctx._paint && ctx.pane() === 'main') ctx._paint(); }
  });
})();
