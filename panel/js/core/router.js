/* router.js: AC.router, hash routes + app bar + sibling tabs + dock switching (ux_design.md 2.2).
   Routes: home | settings | history | tool/<id>[/<sub>]  (+palette / +keys overlay suffix, used for deep links).
   A bare tool id ("silence") is an alias of "tool/silence". Back = explicit parent (never browser history):
   tool -> home, tool/<id>/review|progress|error -> tool/<id>, result -> home, settings/history -> home.
   Pages register with AC.router.page(name, {title, back, render(el), onShow(el), onHide(), dock(), onKey(e)});
   `title` may be a function (translated on every render). R.remount() (language switch) re-renders every page and
   idle tool from scratch, dropping the listeners their previous render added (AC.collectListeners). */
(function () {
  'use strict';
  var AC = window.AC, U = AC.util, ui = AC.ui;
  var pages = {}, screens = {}, pageListeners = {}, cur = { screen: 'home', id: '', sub: '', route: 'home' }, prevTool = null;
  var R = AC.router = {};

  R.page = function (name, def) { pages[name] = def; return def; };
  R.current = function () { return cur; };
  function ptitle(p) { return p ? (typeof p.title === 'function' ? p.title() : p.title) : ''; }
  R.title = ptitle;

  function parse(hash) {
    var h = decodeURIComponent(String(hash || '').replace(/^#/, '')) || 'home';
    var overlay = h.split('+')[1] || '';
    var parts = h.split('+')[0].split('/');
    if (parts[0] !== 'tool' && AC.tools.get(parts[0])) parts.unshift('tool');
    if (parts[0] === 'tool') {
      var def = AC.tools.get(parts[1]);
      if (!def || (def.hidden && !AC.settings.get('developer'))) return { screen: 'home', id: '', sub: '', overlay: overlay, route: 'home' };
      return { screen: 'tool', id: parts[1], sub: parts.slice(2).join('/'), overlay: overlay, route: parts.join('/') };
    }
    var name = pages[parts[0]] ? parts[0] : 'home';
    return { screen: name, id: '', sub: parts.slice(1).join('/'), overlay: overlay, route: name + (parts.length > 1 ? '/' + parts.slice(1).join('/') : '') };
  }

  R.go = function (route, opts) {
    opts = opts || {};
    var h = '#' + route;
    if (location.hash === h) { render(); return; }
    if (opts.replace) { history.replaceState(null, '', h); render(); return; }
    location.hash = route;
  };

  function pageScreen(name) {
    if (screens[name]) return screens[name];
    var el = ui.h('section', { class: 'screen', 'data-screen': name, 'aria-label': ptitle(pages[name]) || name });
    document.getElementById('view').appendChild(el);
    screens[name] = el;
    pageListeners[name] = AC.collectListeners(function () {
      try { if (pages[name].render) pages[name].render(el); } catch (e) { AC.log.error(e, 'page ' + name); el.appendChild(ui.errorView(U.err('RENDER', AC.t('err.pageRender', { msg: U.errMsg(e) }), '', { log: e.stack || '' }))); }
    });
    return el;
  }

  function appbar(isHome, title) {
    U.$('#btnBack').hidden = isHome; U.$('#btnLogo').hidden = !isHome;
    U.$('#btnSearch').hidden = !isHome; U.$('#btnCrumb').hidden = isHome;
    U.$('#crumbTitle').textContent = title || '';
  }

  function subnav(toolId) {
    var sn = U.$('#subnav');
    var sib = toolId ? AC.tools.siblings(toolId) : [];
    if (sib.length < 2) { sn.hidden = true; sn.innerHTML = ''; return; }
    sn.hidden = false;
    sn.innerHTML = sib.map(function (d) {
      return '<button type="button" role="tab" aria-selected="' + (d.id === toolId) + '" data-go="tool/' + d.id + '">' + U.esc(d.tab) + '</button>';
    }).join('');
  }

  function render() {
    var r = parse(location.hash);
    var prev = cur;
    if (prev.screen === 'tool' && (r.screen !== 'tool' || r.id !== prev.id)) AC.tools.hide(prev.id);
    if (prev.screen !== r.screen && pages[prev.screen] && pages[prev.screen].onHide) pages[prev.screen].onHide();
    cur = r;
    U.$$('#view > .screen').forEach(function (s) { s.classList.remove('is-on'); });
    var boot = document.getElementById('boot'); if (boot) boot.parentNode.removeChild(boot);
    var view = document.getElementById('view');
    if (r.screen === 'tool') {
      var def = AC.tools.get(r.id);
      appbar(false, def.title);
      subnav(r.id);
      var pane = AC.tools.show(r.id, r.sub);
      var rt = AC.tools.runtime(r.id);
      rt.screen.classList.add('is-on');
      if (prev.route !== r.route && !(prev.id === r.id && pane !== 'main')) view.scrollTop = 0;
      prevTool = r.id;
    } else {
      var p = pages[r.screen];
      appbar(r.screen === 'home', ptitle(p));
      subnav(null);
      var el = pageScreen(r.screen);
      el.classList.add('is-on');
      ui.dock.show(p.dock ? p.dock() : null);
      if (p.onShow) { try { p.onShow(el, r.sub); } catch (e) { AC.log.error(e, 'onShow ' + r.screen); } }
      if (prev.screen !== r.screen) view.scrollTop = 0;
    }
    if (r.overlay === 'palette') AC.palette.open();
    else if (r.overlay === 'keys') AC.palette.openKeys();
    AC.bus.emit('route', r, prev);
  }
  R.render = render;

  // Language switch: drop every rendered page and idle tool page, then render the current route again.
  R.remount = function () {
    Object.keys(screens).forEach(function (name) {
      if (pages[name] && pages[name].onUnmount) { try { pages[name].onUnmount(); } catch (e) { AC.log.error(e, 'unmount ' + name); } }
      AC.dropListeners(pageListeners[name]); delete pageListeners[name];
      var el = screens[name]; if (el && el.parentNode) el.parentNode.removeChild(el);
      delete screens[name];
    });
    AC.tools.unmountIdle();
    if (AC.palette && AC.palette.close) { try { AC.palette.close(); } catch (e) { /* closed */ } }
    cur = { screen: '', id: '', sub: '', route: '' };   // force a full render (no "same screen" shortcuts)
    render();
    ring();
  };

  R.back = function () {
    if (cur.screen === 'tool') { R.go(AC.tools.back(cur.id)); return; }
    var p = pages[cur.screen];
    R.go(p && p.back !== undefined && p.back !== null ? p.back : 'home');
  };
  // Esc: cancel a running job on its progress pane, else go back (not while typing, not on Home).
  R.escape = function (e, typing) {
    if (cur.screen === 'tool' && AC.tools.escape(cur.id)) return true;
    if (typing) { document.activeElement.blur(); return true; }
    if (cur.screen === 'home') return false;
    R.back(); return true;
  };
  R.pageKey = function (e) {
    if (cur.screen === 'tool') {
      var def = AC.tools.get(cur.id), rt = AC.tools.runtime(cur.id);
      if (def && def.onKey && rt && rt.pane === 'main') { try { return !!def.onKey(e, rt.ctx); } catch (x) { AC.log.error(x, 'onKey'); } }
      return false;
    }
    var p = pages[cur.screen];
    if (p && p.onKey) { try { return !!p.onKey(e); } catch (x) { AC.log.error(x, 'onKey'); } }
    return false;
  };

  /* ---------------- job ring on the clock button */
  var ringTimer = null;
  function ring() {
    var run = AC.history.running(), r = U.$('#jobRing'), btn = U.$('#btnJobs');
    if (!run.length) { r.hidden = true; clearInterval(ringTimer); ringTimer = null; btn.setAttribute('aria-label', AC.t('app.jobsAria')); return; }
    var p = Math.round(run[0].pct || 0);
    r.hidden = false;
    r.querySelector('.fgc').setAttribute('stroke-dashoffset', String(56.5 * (1 - p / 100)));
    btn.setAttribute('aria-label', AC.t('app.jobsRunningAria', { pct: p }));
    if (!ringTimer) ringTimer = setInterval(ring, 500);
  }

  R.start = function () {
    window.addEventListener('hashchange', render);
    document.addEventListener('click', function (e) {
      var g = e.target.closest('[data-go]');
      if (g) { e.preventDefault(); R.go(g.getAttribute('data-go')); }
    });
    U.$('#btnBack').addEventListener('click', R.back);
    U.$('#btnLogo').addEventListener('click', function () { R.go('home'); });
    U.$('#btnSearch').addEventListener('click', function () { AC.palette.open(); });
    U.$('#btnCrumb').addEventListener('click', function () { AC.palette.open(); });
    U.$('#btnSettings').addEventListener('click', function () { R.go('settings'); });
    U.$('#btnJobs').addEventListener('click', function () {
      var run = AC.history.running()[0];
      if (run && AC.tools.get(run.tool)) R.go('tool/' + run.tool + '/progress'); else R.go('history');
    });
    AC.bus.on('task', ring);
    AC.history.on('change', ring);
    AC.settings.on('change', function (k) { if (k === 'developer') { if (cur.screen === 'tool') subnav(cur.id); } });
    render();
  };
})();
