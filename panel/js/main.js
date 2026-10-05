/* main.js: boot. Loaded last, after core, pages and tools have registered.
   settings -> theme -> palette/keys -> router (renders Home at once) -> host jsx load -> sequence sync ->
   background: engine health + old job file cleanup. AC.ready resolves when the sequence sync is running. */
(function () {
  'use strict';
  var AC = window.AC, ui = AC.ui;
  var resolveReady;
  AC.readyState = 'booting';
  AC.ready = new Promise(function (r) { resolveReady = r; });
  function readyResolve(ok) { AC.readyState = ok ? 'ready' : 'failed'; resolveReady(ok); }

  function fatal(e) {
    AC.log.error(e, 'boot');
    var v = document.getElementById('view');
    if (v) {
      v.innerHTML = '';
      var box = ui.h('div', { class: 'boot-err' });
      box.appendChild(ui.errorView(e && e.code ? e : AC.util.err('BOOT', AC.t('misc.bootFail', { msg: AC.util.errMsg(e) }), '', { log: e && e.stack || '' })));
      v.appendChild(box);
    }
  }

  try {
    AC.settings.load();
    AC.theme.init();
    AC.palette.init();
    AC.keys.init();
    AC.router.start();
  } catch (e) { fatal(e); readyResolve(false); return; }

  AC.host.load().then(function (r) {
    if (r.errors.length) ui.toast(AC.t('misc.hostLoadFail', { n: r.errors.length }), { kind: 'err' });
    return AC.seq.start();
  }).then(function () { readyResolve(true); }, function (e) { AC.log.error(e, 'seq start'); readyResolve(false); });

  setTimeout(function () {
    try { AC.engine.cleanup(); } catch (e) { AC.log.error(e, 'cleanup'); }
    if (AC.engine.available()) AC.engine.health();
  }, 1500);
})();
