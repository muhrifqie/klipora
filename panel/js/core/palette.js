/* palette.js: AC.palette, the Ctrl+K command palette (groups tools / recipes / actions) and the shortcut sheet (?).
   Tools/pages add entries with AC.palette.add({g: 'Aksi', ic: 'refresh', l: 'Label', d: 'detail', run: fn}).
   g 'Aksi' (or 'actions') is shown as the translated actions group; l / d may be getters for live translation. */
(function () {
  'use strict';
  var AC = window.AC, U = AC.util, ui = AC.ui;
  var extra = [], items = [], sel = 0, lastFocus = null;
  var P = AC.palette = {};

  // Recipes are placeholders until the recipe runner exists (ux_design.md 2.3).
  // name / meta are getters, so they follow the UI language (keys recipe.<id> + recipe.<id>Meta).
  function recipe(id, steps) {
    return { id: id, steps: steps, get name() { return AC.t('recipe.' + id); }, get meta() { return AC.t('recipe.' + id + 'Meta'); } };
  }
  AC.recipes = [
    recipe('shorts', ['silence', 'fillers', 'captions', 'zoom', 'resize']),
    recipe('tutorial', ['silence', 'fillers', 'captions', 'chapters']),
    recipe('podcast', ['podcast', 'silence', 'chapters', 'viral'])
  ];
  AC.runRecipe = function (r) { ui.toast(AC.t('recipe.soon', { name: r.name, n: r.steps.length }), 'layers'); };
  // Group ids (and the legacy Indonesian names other files pass as g) -> locale keys.
  var GROUP_KEYS = { tools: 'palette.gTools', recipes: 'palette.gRecipes', actions: 'palette.gActions', Alat: 'palette.gTools', Resep: 'palette.gRecipes', Aksi: 'palette.gActions' };
  function groupLabel(g) { return GROUP_KEYS[g] ? AC.t(GROUP_KEYS[g]) : g; }

  P.add = function (it) { extra.push(it); };

  function all() {
    var out = [];
    AC.tools.list().forEach(function (d) {
      var g = AC.tools.group(d.group);
      out.push({ g: 'tools', ic: d.icon, l: d.title, d: g ? g.name : '', run: function () { AC.router.go('tool/' + d.id); } });
    });
    AC.recipes.forEach(function (r) { out.push({ g: 'recipes', ic: 'layers', l: r.name, d: AC.t('recipe.steps', { n: r.steps.length }), run: function () { AC.runRecipe(r); } }); });
    out.push({ g: 'actions', ic: 'refresh', l: AC.t('palette.aiTest'), d: AC.t('palette.aiTestDetail'), run: function () { AC.router.go('settings'); AC.engine.health(); } });
    out.push({ g: 'actions', ic: 'seq', l: AC.t('palette.redetect'), d: '', run: function () { AC.seq.refresh().then(function (s) { ui.toast(s ? AC.t('palette.activeSeq', { name: s.name }) : AC.t('palette.noSeq'), 'seq'); }); } });
    out.push({ g: 'actions', ic: 'clock', l: AC.t('palette.openHistory'), d: '', run: function () { AC.router.go('history'); } });
    out.push({ g: 'actions', ic: 'kbd', l: AC.t('keys.title'), d: '?', run: function () { P.openKeys(); } });
    out.push({ g: 'actions', ic: 'sliders', l: AC.t('app.settings'), d: '', run: function () { AC.router.go('settings'); } });
    return out.concat(extra.filter(function (x) { return !x.dev || AC.settings.get('developer'); }));
  }

  function render() {
    var q = U.$('#palInput').value.toLowerCase().trim();
    items = all().filter(function (it) { return !q || (it.l + ' ' + (it.d || '') + ' ' + groupLabel(it.g)).toLowerCase().indexOf(q) >= 0; });
    sel = Math.min(sel, Math.max(0, items.length - 1));
    var last = '', h = '';
    items.forEach(function (it, i) {
      var gl = groupLabel(it.g);
      if (gl !== last) { h += '<div class="pal-g" role="presentation">' + U.esc(gl) + '</div>'; last = gl; }
      h += '<div class="pal-i" role="option" id="pal-' + i + '" aria-selected="' + (i === sel) + '" data-i="' + i + '">' + ui.icon(it.ic) + '<span>' + U.esc(it.l) + '</span><small>' + U.esc(it.d || '') + '</small></div>';
    });
    var list = U.$('#palList');
    list.innerHTML = h || '<div class="pal-g">' + U.esc(AC.t('palette.noMatch')) + '</div>';
    U.$('#palInput').setAttribute('aria-activedescendant', items.length ? 'pal-' + sel : '');
    var s = U.$('#pal-' + sel); if (s && s.scrollIntoView) s.scrollIntoView({ block: 'nearest' });
  }
  function runAt(i) { var it = items[i]; if (!it) return; P.close(); it.run(); }

  P.isOpen = function () { return U.$('#ovPalette').classList.contains('is-open'); };
  P.keysOpen = function () { return U.$('#ovKeys').classList.contains('is-open'); };
  P.open = function () {
    if (!P.isOpen() && !P.keysOpen()) lastFocus = document.activeElement;
    P.close(true);
    U.$('#ovPalette').classList.add('is-open');
    U.$('#palInput').value = ''; sel = 0; render();
    setTimeout(function () { U.$('#palInput').focus(); }, 10);
  };
  P.toggle = function () { if (P.isOpen()) P.close(); else P.open(); };
  P.close = function (keepFocus) {
    U.$$('.overlay').forEach(function (o) { o.classList.remove('is-open'); });
    if (!keepFocus && lastFocus && lastFocus.focus && document.contains(lastFocus)) { try { lastFocus.focus(); } catch (e) { /* ignore */ } }
  };

  // Shortcut sheet: [group key, [[label key, keys]]]; 'space' is shown as the translated Space key name.
  var KEYS = [
    ['palette.kGeneral', [['app.search', ['Ctrl', 'K']], ['palette.kPrimary', ['Ctrl', 'Enter']], ['palette.kEsc', ['Esc']], ['palette.kList', ['?']]]],
    ['palette.kReview', [['palette.kMoveRow', ['\u2191', '\u2193', 'J', 'K']], ['palette.kToggle', ['space']], ['palette.kSeek', ['Enter']], ['review.selectAll', ['A']]]],
    ['palette.kWave', [['palette.kThreshold', ['\u2191', '\u2193']], ['palette.kStep5', ['Shift', '\u2191']]]],
    ['palette.kCaption', [['palette.kEditorTab', ['Alt', '1-5']], ['palette.kWord', ['\u2190', '\u2192']], ['palette.kEditWord', ['Enter', 'F2']], ['palette.kStyle', ['B', 'H', 'Del']], ['palette.kNudge', ['Alt', '\u2190', '\u2192']], ['palette.kPlay', ['space']]]]
  ];
  P.openKeys = function () {
    if (!P.isOpen() && !P.keysOpen()) lastFocus = document.activeElement;
    P.close(true);
    U.$('#keysBody').innerHTML = KEYS.map(function (g) {
      return '<div><div class="sec-h" style="margin-bottom:6px"><span>' + U.esc(AC.t(g[0])) + '</span></div><dl class="keys">' + g[1].map(function (k) {
        return '<dt>' + U.esc(AC.t(k[0])) + '</dt><dd>' + k[1].map(function (x) { return '<span class="kbd">' + U.esc(x === 'space' ? AC.t('palette.kSpace') : x) + '</span>'; }).join('') + '</dd>';
      }).join('') + '</dl></div>';
    }).join('');
    U.$('#ovKeys').classList.add('is-open');
    var c = U.$('#ovKeys [data-close]'); if (c) c.focus();
  };

  P.init = function () {
    U.$('#palInput').addEventListener('input', function () { sel = 0; render(); });
    U.$('#palInput').addEventListener('keydown', function (e) {
      if (e.key === 'ArrowDown') { e.preventDefault(); sel = Math.min(items.length - 1, sel + 1); render(); }
      else if (e.key === 'ArrowUp') { e.preventDefault(); sel = Math.max(0, sel - 1); render(); }
      else if (e.key === 'Enter') { e.preventDefault(); runAt(sel); }
    });
    U.$('#palList').addEventListener('click', function (e) { var it = e.target.closest('.pal-i'); if (it) runAt(Number(it.getAttribute('data-i'))); });
    U.$$('.overlay').forEach(function (o) {
      o.addEventListener('click', function (e) { if (e.target === o || e.target.closest('[data-close]')) P.close(); });
    });
  };
})();
