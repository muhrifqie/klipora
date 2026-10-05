/* theme.js: AC.theme. Follows Premiere's panel brightness (appSkinInfo + ThemeColorChanged, ux_design.md 6.3),
   falls back to the dark tokens. Also applies density (Lega/Ringkas) and "Kurangi animasi" from settings. */
(function () {
  'use strict';
  var AC = window.AC;
  var root = document.documentElement;
  var STEPS = { '--surface-0': -0.241, '--surface-1': 0.022, '--surface-2': 0.053, '--surface-3': 0.089, '--surface-4': 0.133,
                '--surface-pop': 0.062, '--border-subtle': 0.058, '--border': 0.111, '--border-strong': 0.204 };
  var T = AC.theme = { host: null };

  function hex(r, g, b) {
    return '#' + [r, g, b].map(function (v) { return ('0' + Math.round(Math.max(0, Math.min(255, v))).toString(16)).slice(-2); }).join('');
  }
  function clearVars() { root.style.removeProperty('--bg'); Object.keys(STEPS).forEach(function (k) { root.style.removeProperty(k); }); }

  // Read Premiere's panel background colour ({red, green, blue} 0-255) or null.
  T.hostColor = function () {
    try {
      var env = JSON.parse(window.__adobe_cep__.getHostEnvironment());
      var c = env.appSkinInfo.panelBackgroundColor.color;
      if (c && typeof c.red === 'number') return c;
    } catch (e) { /* not in CEP */ }
    return null;
  };

  T.sync = function () {
    var follow = AC.settings.get('followTheme') !== false;
    var c = follow ? T.hostColor() : null;
    T.host = c;
    if (!c) { root.removeAttribute('data-theme'); clearVars(); AC.bus.emit('theme', 'dark'); return 'dark'; }
    var lum = (0.2126 * c.red + 0.7152 * c.green + 0.0722 * c.blue) / 255;
    var light = lum > 0.45;
    if (light) root.setAttribute('data-theme', 'light'); else root.removeAttribute('data-theme');
    clearVars();
    root.style.setProperty('--bg', hex(c.red, c.green, c.blue));
    if (!light) {
      Object.keys(STEPS).forEach(function (k) {
        var t = STEPS[k], to = t > 0 ? 255 : 0, a = Math.abs(t);
        root.style.setProperty(k, hex(c.red + (to - c.red) * a, c.green + (to - c.green) * a, c.blue + (to - c.blue) * a));
      });
    }
    AC.bus.emit('theme', light ? 'light' : 'dark');
    return light ? 'light' : 'dark';
  };

  T.applyPrefs = function () {
    root.setAttribute('data-density', AC.settings.get('density') === 'compact' ? 'compact' : 'comfortable');
    if (AC.settings.get('reduceMotion')) root.setAttribute('data-motion', 'reduce'); else root.removeAttribute('data-motion');
  };

  T.init = function () {
    T.applyPrefs();
    T.sync();
    try { if (window.__adobe_cep__ && window.__adobe_cep__.addEventListener) window.__adobe_cep__.addEventListener('com.adobe.csxs.events.ThemeColorChanged', T.sync); }
    catch (e) { AC.log.warn('ThemeColorChanged unavailable'); }
    AC.settings.on('change', function (k) {
      if (k === 'followTheme' || k === '*') T.sync();
      if (k === 'density' || k === 'reduceMotion' || k === '*') { T.applyPrefs(); AC.bus.emit('theme', root.getAttribute('data-theme') || 'dark'); }
    });
  };
})();
