/* tools.js: AC.tools, the tool registry and the per-tool page runtime (ctx) with the job flow:
   settings (main pane) -> progress -> review -> apply -> result | error.
   A tool registers once: AC.tools.register({id, group, order, title, tab, icon, desc, badges, lead, cta,
   source, render(el, ctx), onShow(ctx), onHide(ctx), onSeq(lite, ctx), onKey(e, ctx)}). See docs/PANEL_API.md. */
(function () {
  'use strict';
  var AC = window.AC, U = AC.util, ui = AC.ui;
  // Group names come from the locale file ("group.<id>"); relabel() refreshes them on a language switch.
  var GROUPS = [
    { id: 'potong' }, { id: 'teks' }, { id: 'kamera' }, { id: 'publikasi' }, { id: 'dev', hidden: true }
  ];
  var defs = {}, order = [], rts = {};
  var PANES = ['main', 'progress', 'review', 'result', 'error'];

  var T = AC.tools = { groups: GROUPS };

  // def.text(t) (optional) returns the translated texts {title, tab, desc, lead, cta, next, review, ...}; it is
  // merged into def now and again after every language switch, so readers keep using def.title etc.
  function texts(def) {
    if (typeof def.text !== 'function') return;
    try { U.assign(def, def.text(AC.t) || {}); } catch (e) { AC.log.error(e, 'tool text ' + def.id); }
  }
  T.relabel = function () {
    GROUPS.forEach(function (g) { g.name = AC.t('group.' + g.id); });
    order.forEach(function (id) { texts(defs[id]); });
    AC.bus.emit('tools');
  };
  GROUPS.forEach(function (g) { g.name = AC.t('group.' + g.id); });

  T.register = function (def) {
    if (!def || !def.id) throw U.err('TOOL', 'AC.tools.register needs an id');
    if (defs[def.id]) AC.log.warn('tool ' + def.id + ' registered again');
    texts(def);
    def = U.assign({ group: 'potong', order: 99, title: def.id, tab: def.title || def.id, icon: 'spark', desc: '', badges: [], source: true, engineTool: def.id }, def);
    defs[def.id] = def;
    if (order.indexOf(def.id) < 0) order.push(def.id);
    AC.bus.emit('tools');
    return def;
  };
  T.get = function (id) { return defs[id] || null; };
  T.group = function (gid) { for (var i = 0; i < GROUPS.length; i++) if (GROUPS[i].id === gid) return GROUPS[i]; return null; };
  function visible(def) { return !def.hidden || AC.settings.get('developer'); }
  // Visible tools sorted by group, then order.
  T.list = function (opts) {
    var all = opts && opts.all;
    var gi = function (d) { for (var i = 0; i < GROUPS.length; i++) if (GROUPS[i].id === d.group) return i; return 99; };
    return order.map(function (id) { return defs[id]; }).filter(function (d) { return all || visible(d); })
      .sort(function (a, b) { return gi(a) - gi(b) || a.order - b.order; });
  };
  T.inGroup = function (gid) { return T.list().filter(function (d) { return d.group === gid; }); };
  T.siblings = function (id) { var d = defs[id]; return d ? T.inGroup(d.group) : []; };
  T.runtime = function (id) { return rts[id] || null; };
  T.running = function () {
    var out = [];
    Object.keys(rts).forEach(function (id) { var t = rts[id].task; if (t && t.isActive()) out.push(t); });
    return out;
  };

  /* ---------------- placeholder body for tools that are not built yet */
  T.placeholder = function (el, ctx) {
    var d = ctx.def;
    el.appendChild(ui.h('div', { class: 'tool-hero', html: '<span class="tool-ic">' + ui.icon(d.icon) + '</span><div><p>' + U.esc(d.lead || d.desc) + '</p><div class="tags">' + (d.badges || []).map(ui.badge).join('') + '</div></div>' }));
    var box = ui.h('div', { class: 'ph-box' });
    box.appendChild(ui.empty({ icon: 'hammer', title: AC.t('tool.building'), text: AC.t('tool.buildingText') }));
    el.appendChild(box);
    ctx.dock({ primary: { label: d.cta || AC.t('tool.run'), disabled: true, title: AC.t('tool.building') } });
  };

  /* ---------------- runtime per tool page */
  function mount(id) {
    if (rts[id]) return rts[id];
    var def = defs[id];
    var screen = ui.h('section', { class: 'screen', 'data-screen': 'tool-' + id, 'aria-label': def.title });
    var panes = {};
    PANES.forEach(function (p) { panes[p] = ui.h('div', { class: 'tool-pane' + (p === 'review' ? ' is-fill' : ''), 'data-pane': p, hidden: p !== 'main' }); screen.appendChild(panes[p]); });
    document.getElementById('view').appendChild(screen);
    var rt = { id: id, def: def, screen: screen, panes: panes, pane: 'main', docks: {}, task: null, mounted: false, pending: null };
    rt.ctx = makeCtx(rt);
    rts[id] = rt;
    return rt;
  }

  function isForeground(rt) { var r = AC.router.current(); return r.screen === 'tool' && r.id === rt.id; }

  function setPane(rt, pane) {
    if (!rt.panes[pane] || (pane !== 'main' && !rt.panes[pane].firstChild)) pane = 'main';
    var prev = rt.pane;
    rt.pane = pane;
    PANES.forEach(function (p) { rt.panes[p].hidden = p !== pane; });
    rt.screen.classList.toggle('is-fill', pane === 'review');
    if (isForeground(rt)) {
      ui.dock.show(rt.docks[pane] || null);
      if (prev !== pane && pane !== 'main' && rt.def.onHide && prev === 'main') safe(rt.def.onHide, rt.ctx);
      if (pane === 'main' && rt.def.onShow) safe(rt.def.onShow, rt.ctx);
      if (pane === 'review' && rt.review) setTimeout(function () { rt.review.focus(); }, 30);
    }
    return pane;
  }
  function safe(fn, a, b) { try { return fn(a, b); } catch (e) { AC.log.error(e, 'tool'); ui.toast(AC.t('tool.errToast', { msg: U.errMsg(e) }), { kind: 'err' }); } }

  // Show a flow pane: navigate when the tool is on screen, otherwise keep it pending and offer "View".
  function flowShow(rt, pane, doneMsg) {
    if (isForeground(rt)) { AC.router.go('tool/' + rt.id + (pane === 'main' ? '' : '/' + pane), { replace: true }); return; }
    rt.pending = pane;
    if (doneMsg) ui.toast(doneMsg, { icon: pane === 'error' ? 'alert' : 'check', kind: pane === 'error' ? 'err' : 'ok', action: { label: AC.t('common.view'), run: function () { AC.router.go('tool/' + rt.id + '/' + pane); } } });
  }

  // Router entry point: show tool `id` with sub route `sub`.
  T.show = function (id, sub) {
    var def = defs[id]; if (!def) return null;
    var rt = mount(id);
    if (!rt.mounted) {
      rt.mounted = true;
      if (def.source) {
        var so = typeof def.source === 'object' ? def.source : {};
        rt.source = ui.sourceCard(U.assign({ scope: so.scope !== false, scopes: so.scopes, onScope: function (sc) { if (def.onScope) safe(def.onScope, sc, rt.ctx); } }, so));
        rt.panes.main.appendChild(rt.source.el);
        rt.ctx.source = rt.source;
      }
      rt.body = ui.h('div', { class: 'tool-pane tool-body' });
      rt.panes.main.appendChild(rt.body);
      rt.ctx.el = rt.body;
      rt.listeners = AC.collectListeners(function () {
        AC.seq.on('change', function (lite) { if (def.onSeq) safe(def.onSeq, lite, rt.ctx); });
        try { if (def.render) def.render(rt.body, rt.ctx); }
        catch (e) { AC.log.error(e, 'render ' + id); rt.body.appendChild(ui.errorView(U.err('RENDER', AC.t('err.toolRender', { msg: U.errMsg(e) }), '', { log: e.stack || '' }))); }
      });
    }
    rt.ctx.sub = sub || '';
    var want = PANES.indexOf(sub) > 0 ? sub : (!sub && rt.pending ? rt.pending : 'main');
    if (!sub && !rt.pending && rt.task && rt.task.isActive()) want = 'progress';
    rt.pending = null;
    return setPane(rt, want);
  };
  // Drop the mounted page of every idle tool (language switch): the next show() renders it again from scratch.
  // Tools with a running job keep their page (the job's progress, review and result stay intact).
  T.unmountIdle = function () {
    Object.keys(rts).forEach(function (id) {
      var rt = rts[id];
      if (rt.task && rt.task.isActive()) return;
      if (rt.pane === 'main' && rt.def.onHide && rt.mounted) safe(rt.def.onHide, rt.ctx);
      if (rt.def.onUnmount) safe(rt.def.onUnmount, rt.ctx);
      AC.dropListeners(rt.listeners);
      if (rt.review && rt.review.destroy) { try { rt.review.destroy(); } catch (e) { /* gone */ } }
      if (rt.progress && rt.progress.destroy) { try { rt.progress.destroy(); } catch (e) { /* gone */ } }
      if (rt.screen.parentNode) rt.screen.parentNode.removeChild(rt.screen);
      delete rts[id];
    });
  };
  T.hide = function (id) { var rt = rts[id]; if (rt && rt.pane === 'main' && rt.def.onHide) safe(rt.def.onHide, rt.ctx); };
  T.pane = function (id) { return rts[id] ? rts[id].pane : null; };
  T.escape = function (id) {
    var rt = rts[id]; if (!rt) return false;
    if (rt.pane === 'progress' && rt.task && rt.task.isActive()) { rt.task.cancel(); return true; }
    return false;
  };
  T.back = function (id) {
    var rt = rts[id]; if (!rt) return 'home';
    if (rt.pane === 'review' || rt.pane === 'progress' || rt.pane === 'error') return 'tool/' + id;
    return 'home';
  };

  /* ---------------- ctx: what a tool's render(el, ctx) gets */
  function makeCtx(rt) {
    var def = rt.def;
    var ctx = {
      id: rt.id, def: def, el: null, source: null, sub: '',
      state: AC.store.get('tool.' + rt.id, U.copy(def.defaults || {})) || {},
      save: function () { AC.store.set('tool.' + rt.id, ctx.state); },
      toast: ui.toast,
      seq: function (opts) { return AC.seq.current(opts); },
      scope: function () { return rt.source ? rt.source.scope() : { kind: 'all' }; },
      isActive: function () { return isForeground(rt); },
      pane: function () { return rt.pane; },
      job: function () { return rt.task; },
      go: function (sub) { AC.router.go('tool/' + rt.id + (sub ? '/' + sub : '')); },
      back: function () { AC.router.go('tool/' + rt.id); },
      // Dock of the main pane. Returns {update(patch)} for live labels ("Cut 16 pauses").
      dock: function (spec) {
        rt.docks.main = spec;
        if (isForeground(rt) && rt.pane === 'main') ui.dock.show(spec);
        return { update: ctx.setPrimary };
      },
      setPrimary: function (patch) {
        if (rt.docks.main && rt.docks.main.primary) U.assign(rt.docks.main.primary, patch);
        if (isForeground(rt) && rt.pane === 'main') ui.dock.update(patch);
      }
    };

    function progressPane(task, opts) {
      if (rt.progress) rt.progress.destroy();
      rt.panes.progress.innerHTML = '';
      rt.progress = ui.progressView(task, opts || {});
      rt.panes.progress.appendChild(rt.progress.el);
      rt.docks.progress = { left: [{ label: AC.t('tool.background'), kind: 'ghost', onClick: function () { AC.router.go('home'); ui.toast(AC.t('tool.backgroundToast'), 'clock'); } }], grow: true,
                            right: [{ label: AC.t('common.cancelJob'), kind: 'danger', kbd: 'Esc', onClick: function () { task.cancel(); } }] };
    }
    function failed(err, retry) {
      if (err && err.code === 'CANCELLED') {
        var plain = !err.msg || err.msg === AC.t('job.cancelled') || /^(Dibatalkan|Cancelled)\.?$/.test(err.msg);
        ui.toast(plain ? AC.t('tool.cancelledToast') : err.msg, 'x');
        if (rt.pane === 'progress' && isForeground(rt)) AC.router.go('tool/' + rt.id, { replace: true });
        return;
      }
      ctx.error(err, { onRetry: retry });
    }

    // Run an engine action with the progress pane. opts: {action, params, title, stages, seq, review, worker,
    // onResult(data, task), onError(err, task)}. Default result handling: data.review -> review pane,
    // data.plan -> apply it, else a simple result card.
    ctx.run = function (opts) {
      opts = opts || {};
      if (rt.task && rt.task.isActive()) { ui.toast(AC.t('tool.busy'), 'clock'); flowShow(rt, 'progress'); return rt.task; }
      var params = opts.params || {};
      if (rt.source && params.scope === undefined && opts.scope !== false) params.scope = rt.source.scope();
      var task = (opts.worker ? AC.engine.worker : AC.engine.run)(U.assign({ tool: def.engineTool || def.id, record: true }, opts, { params: params, title: def.title, label: opts.title || '' }));
      rt.task = task; rt.lastRun = opts;
      progressPane(task, { title: opts.title || def.title });
      flowShow(rt, 'progress');
      task.then(function (data) {
        if (rt.task !== task) return;
        try {
          if (opts.onResult) opts.onResult(data, task);
          else ctx.defaultResult(data, task, opts);
        } catch (e) { AC.log.error(e, 'onResult'); ctx.error(U.err('RESULT', AC.t('tool.resultErr', { msg: U.errMsg(e) }), '', { log: e.stack || '' })); }
      }, function (err) {
        if (rt.task !== task) return;
        if (opts.onError && opts.onError(err, task) !== false) return;
        failed(err, function () { ctx.run(opts); });
      });
      return task;
    };

    ctx.defaultResult = function (data, task, opts) {
      data = data || {};
      if (data.review) { ctx.review(U.assign({ file: data.review }, def.review || {})); return; }
      if (data.plan) { ctx.applyPlan(data.plan, opts && opts.apply); return; }
      ctx.result({ title: AC.t('common.done'), sub: AC.t('tool.doneIn', { t: U.dtk(task.elapsed()) }) });
    };

    // Review pane. opts: {file | doc, unit, verbOn, verbOff, filters, ctx, note, tags, seekTime, rowActions,
    // bulkAction, markerTag, title, primary: {label: fn(nOn, nAll, sec) | string, onApply(items, doc, file)}}
    ctx.review = function (opts) {
      opts = opts || {};
      var doc = opts.doc;
      if (!doc) { try { doc = ui.loadReview(opts.file); } catch (e) { ctx.error(e); return null; } }
      if (rt.review) rt.review.destroy();
      rt.panes.review.innerHTML = '';
      var prim = opts.primary || {};
      var label = function (n, all, sec) {
        if (typeof prim.label === 'function') return prim.label(n, all, sec);
        if (prim.label) return prim.label;
        return AC.t('tool.applyN', { n: n, unit: ui.unitText(opts.unit, n) });
      };
      var list = ui.reviewList(U.assign({}, opts, { doc: doc, onCount: function (n, all, sec) {
        if (rt.docks.review && rt.docks.review.primary) { rt.docks.review.primary.label = label(n, all, sec); rt.docks.review.primary.disabled = n === 0; }
        if (isForeground(rt) && rt.pane === 'review') ui.dock.update({ label: label(n, all, sec), disabled: n === 0 });
      } }));
      rt.review = list; rt.reviewOpts = opts; ctx.reviewList = list;
      rt.panes.review.appendChild(list.el);
      var n0 = list.count();
      rt.docks.review = {
        left: [{ label: opts.cancelLabel || AC.t('common.cancel'), onClick: function () { AC.router.go('tool/' + rt.id); } }],
        primary: { label: label(n0, doc.items.length, 0), disabled: n0 === 0, onClick: function () {
          var file = opts.file;
          try { if (file) list.save(file); } catch (e) { ui.toast(AC.t('tool.saveFail', { msg: U.errMsg(e) }), { kind: 'err' }); return; }
          if (prim.onApply) prim.onApply(list.selected(), doc, file, list);
          else ctx.run({ action: 'apply', review: file, title: AC.t('tool.applying'), params: (doc.params || {}) });
        } }
      };
      flowShow(rt, 'review', AC.t('tool.reviewReady', { title: def.title }));
      return list;
    };

    // Apply an engine plan. remove_ranges goes through ctx.applyRemove (progress + result card).
    ctx.applyPlan = function (plan, opts) {
      opts = opts || {};
      if (plan && plan.kind === 'remove_ranges') return ctx.applyRemove(plan.ranges || [], opts);
      var t0 = Date.now();
      return AC.apply.plan(plan, opts).then(function (r) {
        if (opts.onDone) return opts.onDone(r);
        var what = plan.kind === 'markers' ? AC.t('tool.markersMade', { n: (r && r.n) || 0 }) : AC.t('tool.newSeqOpened');
        ctx.result({ title: what, sub: AC.t('tool.doneIn', { t: U.dtk((Date.now() - t0) / 1000) }) });
      }, function (e) { failed(e); });
    };

    // Remove ranges (sequence seconds) into a new sequence, with progress pane and a default result card.
    // opts: {name, unit (string | {one, other} | fn(n)), next: [{tool, reason}], onDone(res), mode}
    ctx.applyRemove = function (ranges, opts) {
      opts = opts || {};
      if (rt.task && rt.task.isActive()) { ui.toast(AC.t('tool.busy'), 'clock'); return rt.task; }
      var task = AC.apply.removeRanges(ranges, U.assign({ tool: rt.id, title: def.title, label: AC.t('tool.applyCuts') }, opts));
      rt.task = task;
      progressPane(task, { title: AC.t('tool.applyingCuts'), note: AC.t('tool.applyNote') });
      flowShow(rt, 'progress');
      task.then(function (res) {
        if (rt.task !== task) return;
        if (opts.onDone) { opts.onDone(res); return; }
        ctx.result(ctx.removeResult(res, ranges, U.assign({ secs: task.elapsed() }, opts)));
      }, function (err) { if (rt.task === task) failed(err, function () { ctx.applyRemove(ranges, opts); }); });
      return task;
    };

    // Progress pane for an AC.Task the tool drives itself (host work such as keyframes on a clone), then
    // opts.onDone(result, task) or the error pane. opts: {title, note, onDone, onRetry}. Returns the task.
    ctx.track = function (task, opts) {
      opts = opts || {};
      if (rt.task && rt.task.isActive()) { ui.toast(AC.t('tool.busy'), 'clock'); return rt.task; }
      rt.task = task;
      progressPane(task, { title: opts.title || def.title, note: opts.note });
      flowShow(rt, 'progress');
      task.then(function (res) { if (rt.task === task && opts.onDone) opts.onDone(res, task); },
        function (err) { if (rt.task === task) failed(err, opts.onRetry); });
      return task;
    };

    // Default result card for a remove_ranges result.
    ctx.removeResult = function (res, ranges, opts) {
      opts = opts || {};
      var fps = 1000, fr = AC.apply.mergeFrames(ranges, fps), spans = fr.map(function (f) { return [f[0] / fps, f[1] / fps]; });
      var kept = 0, prev = 0;
      spans.forEach(function (s) { if (s[0] - prev > 0.01) kept++; prev = s[1]; });
      if (res.before - prev > 0.01) kept++;
      return {
        title: AC.t('tool.newSeqReady'),
        sub: AC.t('tool.doneInOrig', { t: U.dtk(opts.secs || 0) }),
        stats: { before: res.before, after: res.after },
        ribbon: { total: res.before, spans: spans },
        legend: [AC.t('tool.keptParts', { n: kept }), AC.t('tool.removedUnits', { n: res.count, unit: ui.unitText(opts.unit, res.count), t: U.dtk(res.removed) })],
        original: { id: res.origId, name: res.origName },
        onEdit: rt.review ? function () { AC.router.go('tool/' + rt.id + '/review'); } : null,
        warn: res.lost && res.lost.length ? { title: AC.t('tool.lostTitle', { n: res.lost.length }), text: AC.t('tool.lostText', { list: res.lost.slice(0, 3).join('; ') }) } : null,
        next: opts.next || def.next || []
      };
    };

    ctx.result = function (opts) {
      opts = opts || {};
      rt.panes.result.innerHTML = '';
      rt.panes.result.appendChild(ui.resultCard(opts));
      var nx = (opts.next || [])[0], nd = nx && T.get(nx.tool);
      rt.docks.result = opts.primary ? { primary: opts.primary }
        : nd ? { primary: { label: AC.t('tool.nextTo', { title: nd.title }), icon: nd.icon, kbd: false, onClick: function () { AC.router.go('tool/' + nd.id); } } }
        : { primary: { label: AC.t('common.done'), kbd: false, onClick: function () { AC.router.go('home'); } } };
      flowShow(rt, 'result', AC.t('tool.doneToast', { title: def.title }));
    };

    ctx.error = function (err, opts) {
      opts = opts || {};
      rt.panes.error.innerHTML = '';
      rt.panes.error.appendChild(ui.errorView(err, opts));
      rt.docks.error = { left: [{ label: AC.t('common.back'), onClick: function () { AC.router.go('tool/' + rt.id); } }],
                         primary: opts.onRetry ? { label: AC.t('common.retry'), icon: 'refresh', onClick: opts.onRetry } : null };
      flowShow(rt, 'error', AC.t('tool.failedToast', { title: def.title }));
    };
    return ctx;
  }
})();
