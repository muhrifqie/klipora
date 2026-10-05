/* history_page.js: History (ux_design.md 4.9). Live jobs on top, then finished ones grouped by day (WIB in id).
   Rows expand to show the sequence, actions (run again / open folder / copy log) and, for failures, the real log.
   Recorded texts (summary, error, warnings) stay in the language of that run (data-i18n-skip). */
(function () {
  'use strict';
  var AC = window.AC, U = AC.util, ui = AC.ui;
  var root = null, open = {};

  function row(r) {
    var d = AC.tools.get(r.tool);
    var badge = r.status === 'ok' ? '<i class="ok">' + ui.icon('check') + '</i>' : r.status === 'running' ? '<i class="run">' + ui.icon('clock') + '</i>' : '<i class="err">' + ui.icon('x') + '</i>';
    var name = (d && d.title) || r.title || r.tool || AC.t('history.job');
    var title = r.status === 'err' ? AC.t('tool.failedToast', { title: name }) : r.status === 'cancelled' ? AC.t('history.cancelled', { title: name }) : name;
    var det = ui.h('details', { class: 'hist' + (r.status === 'running' ? ' hist-live' : ''), open: !!open[r.id] });
    det.addEventListener('toggle', function () { open[r.id] = det.open; });
    det.appendChild(ui.h('summary', { html: '<span class="h-ic">' + ui.icon(d ? d.icon : (r.kind === 'host' ? 'seq' : 'spark')) + badge + '</span>' +
      '<span style="min-width:0"><b>' + U.esc(title) + '</b>' + (r.status === 'running' ? '<span class="sub">' + U.esc(AC.t('history.running')) + '</span>' : '<span class="sub" data-i18n-skip>' + U.esc(r.sub || '') + '</span>') + '</span>' +
      '<span class="when">' + U.wibTime(r.started) + (r.status !== 'running' ? '<br>' + U.esc(U.dtk(r.sec || 0)) : '') + '</span>' }));
    var body = ui.h('div', { class: 'hist-body' });
    if (r.seqName) body.appendChild(ui.h('div', { class: 'meta', html: '<span data-i18n-skip>' + U.esc(r.seqName) + '</span>' + (r.action ? '<span>' + U.esc(r.action) + '</span>' : '') }));
    if (r.error) body.appendChild(ui.h('p', { class: 'help', 'data-i18n-skip': true, text: (r.error.msg || '') + (r.error.hint && r.error.hint.length < 200 ? ' ' + r.error.hint : '') }));
    if (r.status === 'err' && r.log) body.appendChild(ui.h('pre', { class: 'log', text: r.log }));
    (r.warnings || []).slice(0, 3).forEach(function (w) { body.appendChild(ui.h('p', { class: 'help', 'data-i18n-skip': true, html: ui.icon('alert') + ' ' + U.esc(w) })); });
    var btns = ui.h('div', { class: 'btn-row' });
    if (r.status === 'running' && d) btns.appendChild(ui.button({ label: AC.t('history.viewJob'), small: true, onClick: function () { AC.router.go('tool/' + d.id + '/progress'); } }));
    else if (d) btns.appendChild(ui.button({ label: AC.t('history.again'), icon: 'refresh', small: true, onClick: function () { AC.router.go('tool/' + d.id); } }));
    if (r.workdir) btns.appendChild(ui.button({ label: AC.t('history.openFolder'), icon: 'folder', small: true, kind: 'ghost', onClick: function () { if (!AC.sys.openFolder(r.workdir)) ui.toast(AC.t('history.folderFail'), { kind: 'err' }); } }));
    if (r.log) btns.appendChild(ui.button({ label: AC.t('history.copyLog'), icon: 'copy', small: true, kind: 'ghost', onClick: function () { ui.copy((title + '\n' + (r.error ? r.error.code + ': ' + r.error.msg + '\n' : '') + r.log), 'Log'); } }));
    if (btns.children.length) body.appendChild(btns);
    det.appendChild(body);
    return det;
  }

  function render() {
    if (!root) return;
    root.innerHTML = '';
    var rows = AC.history.list();
    if (!rows.length) { root.appendChild(ui.empty({ icon: 'clock', title: AC.t('history.empty'), text: AC.t('history.emptyText') })); return; }
    var lastDay = '';
    rows.forEach(function (r) {
      var day = U.dayKey(r.started);
      if (day !== lastDay) { root.appendChild(ui.h('div', { class: 'hist-day', text: U.dayLabel(r.started) })); lastDay = day; }
      root.appendChild(row(r));
    });
  }
  var later = U.debounce(render, 150);

  AC.router.page('history', {
    title: function () { return AC.t('history.title'); }, back: 'home',
    render: function (el) { root = el; AC.history.on('change', function () { if (AC.router.current().screen === 'history') later(); }); },
    onShow: function () { render(); }
  });
})();
