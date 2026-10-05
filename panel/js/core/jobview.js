/* jobview.js: job lifecycle views (ux_design.md 4.5, 4.6, 4.12).
   AC.ui.progressView(task, opts) -> {el, destroy}   named stages, %, segmented bar, ETA (cancel/background live in the dock)
   AC.ui.resultCard(opts) -> el                       before/after, cut ribbon, "Open original", next steps
   AC.ui.errorView(err, opts) -> el                   title + reason + real log + fix action + "Copy details"
   AC.ui.errorTitle(err) -> translated title for an error code. */
(function () {
  'use strict';
  var AC = window.AC, U = AC.util, ui = AC.ui;

  // Static card of the sequence a job runs on (does not follow the active sequence).
  ui.seqCardStatic = function (s) {
    if (!s) return null;
    var main = AC.seq.mainTrack(s);
    var meta = [U.mmss(s.duration), AC.t('ui.clipCount', { n: main ? (main.count || (main.clips || []).length) : 0 }), s.width + 'x' + s.height, ui.fps(s.fps)];
    var el = ui.h('div', { class: 'card src' });
    el.innerHTML = '<div class="src-row"><span class="src-ic">' + ui.icon('seq') + '</span><div class="src-main"><div class="src-name" data-i18n-skip></div><div class="meta">' +
      meta.map(function (m) { return '<span>' + U.esc(m) + '</span>'; }).join('') + '</div></div></div>';
    var n = el.querySelector('.src-name'); n.textContent = s.name; n.title = s.name;
    return el;
  };

  /* ---------------- progress */
  ui.progressView = function (task, opts) {
    opts = opts || {};
    var el = ui.h('div', { class: 'tool-pane' });
    var srcSlot = ui.h('div');
    var h2 = ui.h('h2', { text: opts.title || task.label || task.title || AC.t('job.processing') }), pct = ui.h('span', { class: 'prog-pct', text: '0%' });
    var bar = ui.h('div', { class: 'pbar', 'aria-hidden': 'true' }), list = ui.h('ol', { class: 'stages' });
    var elapsed = ui.h('span'), eta = ui.h('span');
    var card = ui.h('div', { class: 'card prog-card', role: 'status', 'aria-live': 'polite' }, [ui.h('div', { class: 'prog-top' }, [h2, pct]), bar, list, ui.h('div', { class: 'prog-foot' }, [elapsed, eta])]);
    el.appendChild(srcSlot); el.appendChild(card);
    el.appendChild(ui.h('p', { class: 'help', text: opts.note || AC.t('job.note') }));
    var warnBox = ui.h('div', { class: 'sec', style: { gap: '6px' } }); el.appendChild(warnBox);

    function stages() {
      if (task.stages.length) return task.stages;
      return [{ id: '_', label: opts.title || task.title || AC.t('job.processing'), w: 1, state: task.state === 'done' ? 'done' : 'run', pct: task.pct }];
    }
    function paint() {
      if (!srcSlot.firstChild && task.job && task.job.seq) { var c = ui.seqCardStatic(task.job.seq); if (c) srcSlot.appendChild(c); }
      var ss = stages(), p = Math.round(task.pct || 0), cur = ss[task.cur] || null;
      pct.textContent = p + '%';
      h2.textContent = task.state === 'done' ? AC.t('common.done') : cur && cur.label ? cur.label : (opts.title || task.title || AC.t('job.processing'));
      bar.innerHTML = ss.map(function (s) {
        var f = s.state === 'done' ? 100 : s.state === 'run' ? (s.pct || 0) : 0;
        return '<span class="' + (s.state === 'done' ? 'is-done' : '') + '" style="--w:' + (s.w || 1) + ';--f:' + f + '%"><i></i></span>';
      }).join('');
      list.innerHTML = ss.map(function (s, i) {
        var cls = s.state === 'done' ? 'is-done' : s.state === 'run' ? 'is-run' : s.state === 'err' ? 'is-err' : '';
        var ic = s.state === 'done' ? ui.icon('check') : s.state === 'err' ? ui.icon('x') : '';
        var right = s.state === 'done' ? (s.note || (typeof s.sec === 'number' ? U.dtk(s.sec) : '')) : s.state === 'run' ? Math.round(s.pct || 0) + '%' : '';
        var label = s.label || (s.placeholder ? AC.t('job.stageN', { n: i + 1 }) : s.id);
        var sub = s.state === 'run' && task.note ? '<small class="note">' + U.esc(task.note) + '</small>' : (s.sub ? '<small>' + U.esc(s.sub) + '</small>' : '');
        return '<li class="stage-i ' + cls + '"><span class="st-ic">' + ic + '</span><span><b>' + U.esc(label) + '</b>' + sub + '</span><span class="t">' + U.esc(right) + '</span></li>';
      }).join('');
      tick();
    }
    function tick() {
      var sec = task.elapsed(), p = task.pct || 0;
      elapsed.textContent = AC.t('job.elapsed', { t: U.dtk(sec).replace(U.loc.dec + '0 ' + U.loc.sec, ' ' + U.loc.sec) });
      eta.textContent = (task.isActive() && p > 3 && sec > 2) ? AC.t('job.eta', { t: U.dtk(Math.max(1, Math.round(sec * (100 - p) / p))) }) : '';
    }
    function warn(msg) { warnBox.appendChild(ui.alert({ kind: 'warn', title: msg })); }
    var timer = setInterval(tick, 1000);
    var evs = ['stage', 'progress', 'stage_done', 'state', 'end'];
    evs.forEach(function (n) { task.on(n, paint); });
    task.on('warn', warn);
    task.warnings.forEach(warn);
    paint();
    return { el: el, destroy: function () { clearInterval(timer); evs.forEach(function (n) { task.off(n, paint); }); task.off('warn', warn); } };
  };

  /* ---------------- result */
  // opts: {title, sub, stats: {before, after} (seconds) | statsList: [{label, value, accent}], ribbon: {total, spans},
  //        legend: [text, text], warn: {title, text}, original: {id, name}, onEdit, onDelete, actions: [{label, icon, onClick}],
  //        next: [{tool, reason}], extra: element}
  ui.resultCard = function (o) {
    o = o || {};
    var wrap = ui.h('div', { class: 'tool-pane' });
    var card = ui.h('div', { class: 'card sec' });
    card.appendChild(ui.h('div', { class: 'result-h', html: '<span class="done">' + ui.icon('check') + '</span><div><h2>' + U.esc(o.title || AC.t('common.done')) + '</h2>' + (o.sub ? '<p>' + U.esc(o.sub) + '</p>' : '') + '</div>' }));
    if (o.stats && typeof o.stats.before === 'number') {
      var b = o.stats.before, a = o.stats.after, save = b > 0 ? Math.round((1 - a / b) * 100) : 0;
      card.appendChild(ui.h('div', { class: 'stats', html: '<div><span>' + U.esc(AC.t('job.before')) + '</span><b>' + U.mmss(b) + '</b></div><div class="arrow">' + ui.icon('arrow-r') + '</div><div><span>' + U.esc(AC.t('job.after')) + '</span><b>' + U.mmss(a) + '</b></div><div class="save"><span>' + U.esc(AC.t('job.saved')) + '</span><b>' + save + '%</b></div>' }));
    } else if (o.statsList) {
      var sl = ui.h('div', { class: 'sum-strip' });
      o.statsList.forEach(function (s) { sl.appendChild(ui.h('div', { class: s.accent ? 'hl' : '', html: '<span>' + U.esc(s.label) + '</span><b>' + U.esc(s.value) + '</b>' })); });
      card.appendChild(sl);
    }
    if (o.ribbon) {
      var r = ui.ribbon({ size: 'lg', label: AC.t('job.resultMap') });
      card.appendChild(r.el);
      setTimeout(function () { r.set(o.ribbon); }, 0);
    }
    if (o.legend) card.appendChild(ui.h('div', { class: 'legend', html: '<span><i class="lk"></i>' + U.esc(o.legend[0] || '') + '</span>' + (o.legend[1] ? '<span><i class="lc"></i>' + U.esc(o.legend[1]) + '</span>' : '') }));
    if (o.extra) card.appendChild(o.extra);
    var row = ui.h('div', { class: 'btn-row' });
    if (o.original && o.original.id) {
      row.appendChild(ui.button({ label: AC.t('job.openOriginal'), icon: 'undo', small: true, onClick: function () {
        AC.host.json('bac_openSequence', o.original.id).then(function () { ui.toast(AC.t('job.openingOriginal', { name: o.original.name || '' }), 'undo'); },
          function (e) { ui.toast(U.errMsg(e), { kind: 'err' }); });
      } }));
    }
    if (o.onEdit) row.appendChild(ui.button({ label: AC.t('job.editChoice'), small: true, kind: 'ghost', onClick: o.onEdit }));
    (o.actions || []).forEach(function (a) { row.appendChild(ui.button({ label: a.label, icon: a.icon, small: true, kind: a.kind || 'ghost', onClick: a.onClick })); });
    if (o.onDelete) row.appendChild(ui.confirmClick(ui.button({ label: AC.t('job.deleteResult'), icon: 'trash', small: true, kind: 'ghost-danger' }), o.onDelete, AC.t('job.deleteConfirm')));
    if (row.children.length) card.appendChild(row);
    wrap.appendChild(card);
    if (o.warn) wrap.appendChild(ui.alert({ kind: 'warn', title: o.warn.title, text: o.warn.text }));
    if (o.next && o.next.length) {
      var sec = ui.section({ title: AC.t('job.nextTitle') }), list = ui.h('div', { class: 'next-list' });
      o.next.forEach(function (n) {
        var def = AC.tools.get(n.tool); if (!def) return;
        var b = ui.h('button', { class: 'next-i', type: 'button', html: '<span class="tool-ic">' + ui.icon(def.icon) + '</span><span><b>' + U.esc(def.title) + '</b><small>' + U.esc(n.reason || def.desc || '') + '</small></span>' + ui.icon('chev-r') });
        b.addEventListener('click', function () { AC.router.go('tool/' + def.id); });
        list.appendChild(b);
      });
      sec.add(list); wrap.appendChild(sec.el);
    }
    return wrap;
  };

  /* ---------------- errors */
  // error code -> locale key of its title
  var TITLES = {
    PYTHON: 'err.python', NO_ENGINE: 'err.noEngine', NO_NODE: 'err.noCep', NO_CEP: 'err.noCep', NO_SEQ: 'err.noSeq',
    HOST_TIMEOUT: 'err.hostTimeout', HOST: 'err.host', HOST_EVAL: 'err.host', HOST_JSON: 'err.hostJson',
    EXIT: 'err.exit', NO_RESULT: 'err.noResult', WORKER_EXIT: 'err.exit', DISK_FULL: 'err.diskFull',
    GPU_BUSY: 'err.gpuBusy', NO_AUDIO: 'err.noAudio', NO_MEDIA: 'err.noMedia', CANCELLED: 'err.cancelled',
    BAD_REVIEW: 'err.badReview', TIMEOUT: 'err.timeout', LOCKED: 'err.locked'
  };
  ui.errorTitle = function (e) { return AC.t((e && TITLES[e.code]) || 'err.failed'); };
  ui.errorDetail = function (e, title) {
    return [title || ui.errorTitle(e), (e && e.code ? AC.t('err.codeLine', { code: e.code }) : ''), U.errMsg(e), e && e.hint ? AC.t('err.hintLine', { hint: e.hint }) : '', e && e.log ? '\n' + e.log : ''].filter(Boolean).join('\n');
  };
  // opts: {onRetry, title, actions: [...]}
  ui.errorView = function (e, opts) {
    opts = opts || {};
    var title = opts.title || ui.errorTitle(e);
    var actions = [];
    if (e && (e.code === 'PYTHON' || e.action === 'settings')) actions.push({ label: AC.t('err.openSettings'), onClick: function () { AC.router.go('settings'); } });
    if (opts.onRetry) actions.push({ label: AC.t('common.retry'), icon: 'refresh', onClick: opts.onRetry });
    (opts.actions || []).forEach(function (a) { actions.push(a); });
    actions.push({ label: AC.t('err.copyDetail'), icon: 'copy', kind: 'ghost', onClick: function () { ui.copy(ui.errorDetail(e, title)); } });
    var text = U.errMsg(e) + (e && e.hint && String(e.hint).length < 160 && String(e.hint).indexOf('\n') < 0 ? ' ' + e.hint : '');
    var log = (e && e.log) || (e && e.hint && (String(e.hint).length >= 160 || String(e.hint).indexOf('\n') >= 0) ? e.hint : '') || (e && e.stack && !e.code ? e.stack : '');
    return ui.alert({ kind: e && e.code === 'CANCELLED' ? 'info' : 'err', title: title, text: text, log: log, actions: actions });
  };
})();
