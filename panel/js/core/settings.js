/* settings.js: AC.settings, persisted to %APPDATA%\Klipora\settings.json (shared with the engine:
   engine/ac/util.py settings() reads the same keys). The AI key is NEVER stored here (it lives in <repo>\.env
   and only the engine reads it). pexelsKey is write-only: AC.settings.get('pexelsKey') returns undefined, use
   AC.settings.hasSecret('pexelsKey') / setSecret('pexelsKey', value). */
(function () {
  'use strict';
  var AC = window.AC, U = AC.util;
  var SECRET = { pexelsKey: 1 };
  var DEFAULTS = {
    python: 'python',                 // python.exe path or command
    workRoot: '',                     // filled at load: %USERPROFILE%\Videos\Klipora
    gpu: true,
    whisperModel: 'large-v3-turbo',
    lang: 'id',                       // speech (transcription) language, NOT the UI language
    uiLang: 'auto',                   // UI language: 'auto' (Premiere UI locale) | 'id' | 'en' (i18n.js)
    ai: true,
    aiModel: '',                      // '' = per-task default (engine decides)
    captionTemplate: 'sorot',
    outputDir: '',                    // '' = workdir
    density: 'comfortable',           // 'comfortable' (Lega) | 'compact' (Ringkas)
    followTheme: true,
    reduceMotion: false,
    developer: false
  };
  var data = U.copy(DEFAULTS), file = '', loadedOk = false;
  var bus = new AC.Emitter();

  var S = AC.settings = {
    defaults: DEFAULTS,
    on: function (n, f) { bus.on(n, f); return S; },
    off: function (n, f) { bus.off(n, f); return S; },
    path: function () { return file; },
    load: function () {
      file = AC.sys.join(AC.sys.paths.appData, 'settings.json');
      var saved = AC.sys.readJSON(file, null);
      data = U.copy(DEFAULTS);
      if (saved && typeof saved === 'object') { for (var k in saved) if (saved[k] !== null && saved[k] !== undefined) data[k] = saved[k]; }
      // Installs from before the English UI (no uiLang saved) keep Bahasa Indonesia; new installs follow Premiere.
      if (saved && typeof saved === 'object' && saved.uiLang === undefined) { data.uiLang = 'id'; var keepId = true; }
      // The engine treats workRoot literally (Path('') would be the cwd), so never leave it empty.
      if (!data.workRoot) data.workRoot = AC.sys.join(AC.sys.paths.videos, 'Klipora');
      if (keepId) save();
      loadedOk = true;
      return S;
    },
    get: function (k) { if (SECRET[k]) return undefined; return data[k] === undefined ? DEFAULTS[k] : data[k]; },
    // Copy without secrets (safe to show or log).
    all: function () { var o = U.copy(data); for (var k in SECRET) delete o[k]; return o; },
    set: function (k, v) {
      if (SECRET[k]) return S.setSecret(k, v);
      if (data[k] === v) return S;
      var prev = data[k]; data[k] = v;
      save(); bus.emit('change', k, v, prev); AC.bus.emit('settings', k, v);
      return S;
    },
    hasSecret: function (k) { return !!(data[k] && String(data[k]).length); },
    setSecret: function (k, v) {
      if (!SECRET[k]) throw U.err('SETTINGS', 'Not a secret key: ' + k);
      data[k] = v ? String(v).trim() : '';
      save(); bus.emit('change', k, '***'); AC.bus.emit('settings', k, '***');
      return S;
    },
    reset: function () {
      var keep = {}; for (var k in SECRET) if (data[k]) keep[k] = data[k];
      data = U.assign(U.copy(DEFAULTS), keep);
      data.workRoot = AC.sys.join(AC.sys.paths.videos, 'Klipora');
      save(); bus.emit('change', '*'); AC.bus.emit('settings', '*');
      return S;
    },
    workRoot: function () { return data.workRoot || AC.sys.join(AC.sys.paths.videos, 'Klipora'); },
    workdir: function (seqName) { return AC.sys.join(S.workRoot(), U.safeName(seqName)); },
    loaded: function () { return loadedOk; }
  };

  function save() {
    if (!file) return;
    try { AC.sys.writeJSON(file, data, true); }
    catch (e) { AC.log.error(e, 'save settings'); if (AC.ui && AC.ui.toast) AC.ui.toast(AC.t('settings.saveFail', { msg: U.errMsg(e) }), { kind: 'err' }); }
  }
})();
