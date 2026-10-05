/* i18n.js: AC.i18n + AC.t, the panel's UI language (Bahasa Indonesia / English).
   Locale files: panel/locales/<lang>.json, flat keys -> string, or plural objects {"one": "...", "other": "..."}.
     AC.t('silence.cta', {n: 16})          -> "Potong 16 jeda" / "Cut 16 pauses"
     AC.t('x', {name: 'A'})                 -> "{name}" placeholders; integers are formatted per locale (1.500 / 1,500)
     AC.i18n.html('x', {name: s})           -> same, but the placeholder values are HTML-escaped (template markup kept)
     AC.i18n.has(key), AC.i18n.lang() ('id' | 'en'), AC.i18n.setting() ('auto' | 'id' | 'en'), AC.i18n.set(v)
   Setting `uiLang` (AC.settings): 'auto' follows Premiere's UI locale (__adobe_cep__.getHostEnvironment().appUILocale:
   id_* -> Indonesian, anything else -> English). Missing keys fall back to Indonesian, then to the key itself.
   Static markup uses data-i18n="key" (textContent), data-i18n-aria / -title / -ph (aria-label / title / placeholder);
   AC.i18n.apply(root) fills them. Switching the language re-renders the screens (router.js remount) and emits
   AC.bus 'locale'. Tools translate their registry texts with def.text(t) (tools.js). Loaded right after settings.js. */
(function () {
  'use strict';
  var AC = window.AC, U = AC.util;
  var LANGS = ['id', 'en'];
  var dicts = {}, cur = 'id', missing = {};
  var plural = {};

  function file(lang) { return AC.sys.join(AC.sys.paths.ext, 'locales', lang + '.json'); }
  function readFile(lang) {
    var txt = AC.sys.node ? AC.sys.readText(file(lang)) : null;
    if (txt === null) {
      // No Node (plain browser): synchronous XHR next to index.html.
      try { var x = new XMLHttpRequest(); x.open('GET', 'locales/' + lang + '.json', false); x.send(null); if (x.status === 0 || x.status === 200) txt = x.responseText; } catch (e) { txt = null; }
    }
    if (!txt) return {};
    try { return JSON.parse(String(txt).replace(/^﻿/, '')); } catch (e) { AC.log.error(e, 'locale ' + lang); return {}; }
  }
  function dict(lang) { if (!dicts[lang]) dicts[lang] = readFile(lang); return dicts[lang]; }

  // Premiere UI locale ("en_US", "id_ID", ...), or the browser language outside CEP.
  function hostLocale() {
    try {
      if (window.__adobe_cep__ && window.__adobe_cep__.getHostEnvironment) {
        var env = JSON.parse(window.__adobe_cep__.getHostEnvironment());
        if (env && (env.appUILocale || env.appLocale)) return String(env.appUILocale || env.appLocale);
      }
    } catch (e) { /* not in CEP */ }
    return String((navigator && (navigator.language || navigator.userLanguage)) || 'id');
  }
  function resolve(setting) {
    if (LANGS.indexOf(setting) >= 0) return setting;
    return /^(id|in)([_-]|$)/i.test(hostLocale()) ? 'id' : 'en';
  }

  function pluralForm(lang, n) {
    if (typeof n !== 'number' || !isFinite(n)) return 'other';
    try {
      if (!plural[lang]) plural[lang] = new Intl.PluralRules(lang);
      return plural[lang].select(n);
    } catch (e) { return lang === 'en' && Math.abs(n) === 1 ? 'one' : 'other'; }
  }

  function lookup(key) {
    var v = dict(cur)[key];
    if (v === undefined && cur !== 'id') v = dict('id')[key];
    if (v === undefined) {
      if (!missing[key]) { missing[key] = 1; AC.log.warn('i18n: missing key ' + key); }
      return undefined;
    }
    return v;
  }
  function fmtVal(v) {
    if (typeof v === 'number' && isFinite(v)) return v === Math.round(v) ? U.int(v) : String(v).replace('.', U.loc.dec);
    return v === undefined || v === null ? '' : String(v);
  }
  function render(key, vars, esc) {
    var v = lookup(key);
    if (v === undefined) return key;
    if (v && typeof v === 'object') {
      var n = vars ? (typeof vars.n === 'number' ? vars.n : vars.count) : undefined;
      var f = pluralForm(cur, n);
      v = v[f] !== undefined ? v[f] : v.other;
      if (v === undefined) return key;
    }
    v = String(v);
    if (!vars) return v;
    return v.replace(/\{(\w+)\}/g, function (m, k) {
      if (!Object.prototype.hasOwnProperty.call(vars, k)) return m;
      var s = fmtVal(vars[k]);
      return esc ? U.esc(s) : s;
    });
  }

  function applyLoc() {
    var d = dict(cur);
    var arr = function (k, def) { var v = d[k]; return typeof v === 'string' && v ? v.split(',') : def; };
    U.loc = {
      lang: cur,
      dec: cur === 'en' ? '.' : ',', thou: cur === 'en' ? ',' : '.',
      sec: d['unit.sec'] || (cur === 'en' ? 's' : 'dtk'), min: d['unit.min'] || (cur === 'en' ? 'min' : 'mnt'),   // i18n-ignore (fallback)
      wib: cur !== 'en',
      months: arr('date.months', null), days: arr('date.days', null),
      today: d['date.today'] || null, yesterday: d['date.yesterday'] || null
    };
    try { document.documentElement.setAttribute('lang', cur); } catch (e) { /* no DOM */ }
  }

  var I = AC.i18n = {
    langs: LANGS,
    lang: function () { return cur; },
    setting: function () { var s = AC.settings && AC.settings.get('uiLang'); return s || 'auto'; },
    hostLocale: hostLocale,
    resolve: resolve,
    t: function (key, vars) { return render(key, vars, false); },
    html: function (key, vars) { return render(key, vars, true); },
    has: function (key) { return dict(cur)[key] !== undefined || dict('id')[key] !== undefined; },
    // Raw entry (string or plural object), e.g. for lists stored in the locale file.
    raw: function (key) { return lookup(key); },
    missing: function () { return Object.keys(missing); },
    dict: function (lang) { return dict(lang || cur); },
    reload: function () { dicts = {}; missing = {}; applyLoc(); },
    // Pick the language from the setting (boot). Does not re-render.
    init: function () {
      cur = resolve(I.setting());
      applyLoc();
      I.apply(document);
      return cur;
    },
    // Fill data-i18n attributes under root.
    apply: function (root) {
      root = root || document;
      if (!root.querySelectorAll) return;
      U.$$('[data-i18n]', root).forEach(function (el) { el.textContent = I.t(el.getAttribute('data-i18n')); });
      [['data-i18n-aria', 'aria-label'], ['data-i18n-title', 'title'], ['data-i18n-ph', 'placeholder']].forEach(function (p) {
        U.$$('[' + p[0] + ']', root).forEach(function (el) { el.setAttribute(p[1], I.t(el.getAttribute(p[0]))); });
      });
    },
    // Change the setting ('auto' | 'id' | 'en'); when the effective language changes, the UI is re-rendered.
    set: function (setting) {
      if (AC.settings && AC.settings.get('uiLang') !== setting) AC.settings.set('uiLang', setting);
      return I.switchTo(resolve(setting));
    },
    switchTo: function (lang) {
      if (LANGS.indexOf(lang) < 0) lang = 'id';
      if (lang === cur) return false;
      var prev = cur;
      cur = lang;
      applyLoc();
      I.apply(document);
      if (AC.host && AC.host.sendStrings) AC.host.sendStrings();
      if (AC.tools && AC.tools.relabel) AC.tools.relabel();
      if (AC.router && AC.router.remount) AC.router.remount();
      AC.bus.emit('locale', cur, prev);
      return true;
    },
    // Strings for the ExtendScript side (host/*.jsx acT): every "host.*" key of the current language.
    hostStrings: function () {
      var out = {}, d = dict(cur), base = dict('id'), k;
      for (k in base) if (k.indexOf('host.') === 0) out[k.slice(5)] = base[k];
      for (k in d) if (k.indexOf('host.') === 0) out[k.slice(5)] = d[k];
      return out;
    }
  };
  AC.t = I.t;

  if (AC.settings && !AC.settings.loaded()) { try { AC.settings.load(); } catch (e) { AC.log.error(e, 'settings (i18n)'); } }
  try { I.init(); } catch (e) { AC.log.error(e, 'i18n init'); }
})();
