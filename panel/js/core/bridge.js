/* bridge.js: AC.host, the panel <-> ExtendScript bridge.
   AC.host.call(fn, ...args) -> Promise<string>   ("ERR:..." / "EvalScript error." / timeout -> reject)
   AC.host.json(fn, ...args) -> Promise<any>      (JSON.parse of the result)
   AC.host.exec(fn, args, {timeout, json})        (custom timeout, e.g. long QE extracts)
   AC.host.eval(code, timeoutMs) -> Promise<string> raw evalScript, no ERR handling
   AC.host.load() -> $.evalFile every panel/host/*.jsx (sorted) on every panel load; calls wait for it.
   Arguments are encoded as JS literals with JSON.stringify (objects/arrays arrive as ES3 object literals). */
(function () {
  'use strict';
  var AC = window.AC, U = AC.util;
  var cep = window.__adobe_cep__;
  var DEFAULT_TIMEOUT = 20000;
  var readyResolve, loaded = false;
  var host = AC.host = {
    available: !!(cep && cep.evalScript),
    files: [], loadErrors: [],
    ready: new Promise(function (r) { readyResolve = r; })
  };

  // U+2028/2029 are valid in JSON but end a string literal in ES3/ES5 source: escape them.
  var LS = new RegExp(String.fromCharCode(0x2028), 'g'), PS = new RegExp(String.fromCharCode(0x2029), 'g');
  function lit(v) {
    if (v === undefined) return 'undefined';
    return JSON.stringify(v).replace(LS, '\\u2028').replace(PS, '\\u2029');
  }
  host.lit = lit;

  host.eval = function (code, timeout) {
    return new Promise(function (resolve, reject) {
      if (!host.available) { reject(U.err('NO_CEP', AC.t('bridge.noCep'), AC.t('bridge.noCepHint'))); return; }
      var done = false, ms = timeout || DEFAULT_TIMEOUT;
      var t = setTimeout(function () {
        if (done) return; done = true;
        reject(U.err('HOST_TIMEOUT', AC.t('bridge.timeout', { sec: Math.round(ms / 1000) }), AC.t('bridge.timeoutHint')));
      }, ms);
      try {
        cep.evalScript(code, function (res) { if (done) return; done = true; clearTimeout(t); resolve(res === undefined || res === null ? '' : String(res)); });
      } catch (e) { done = true; clearTimeout(t); reject(U.err('HOST', e.message)); }
    });
  };

  host.exec = function (fn, args, opts) {
    opts = opts || {};
    if (!/^[A-Za-z_$][\w$.]*$/.test(fn)) return Promise.reject(U.err('HOST', AC.t('bridge.badFn', { fn: fn })));
    var code = fn + '(' + (args || []).map(lit).join(',') + ')';
    var wait = (opts.noWait || loaded) ? Promise.resolve() : host.ready;
    return wait.then(function () { return host.eval(code, opts.timeout); }).then(function (res) {
      if (res === 'EvalScript error.') throw U.err('HOST_EVAL', AC.t('bridge.evalFail', { fn: fn }), AC.t('bridge.evalFailHint'));
      if (res.indexOf('ERR:') === 0) throw U.err('HOST', res.slice(4), '', { fn: fn });
      if (!opts.json) return res;
      if (res === '' || res === 'undefined') return null;
      try { return JSON.parse(res); } catch (e) { throw U.err('HOST_JSON', AC.t('bridge.badJson', { fn: fn }), res.slice(0, 200)); }
    });
  };
  host.call = function (fn) { return host.exec(fn, Array.prototype.slice.call(arguments, 1)); };
  host.json = function (fn) { return host.exec(fn, Array.prototype.slice.call(arguments, 1), { json: true }); };

  // User-facing host texts (acT in host/00_util.jsx) in the panel language: every "host.*" locale key.
  host.sendStrings = function () {
    if (!host.available || !AC.i18n) return Promise.resolve(false);
    return host.exec('bac_setStrings', [AC.i18n.hostStrings()], { noWait: true }).then(function () { return true; }, function (e) { AC.log.warn('host strings: ' + U.errMsg(e)); return false; });
  };

  // Load every host/*.jsx in name order. ScriptPath in the manifest runs only once per Premiere session, so the
  // panel re-evaluates the files on every load (picks up edits without restarting Premiere).
  host.load = function () {
    if (!host.available) { loaded = true; readyResolve(false); return Promise.resolve({ files: [], errors: ['no CEP'] }); }
    var dir = AC.sys.paths.host;
    var files = AC.sys.list(dir).filter(function (f) { return /\.jsx$/i.test(f); }).sort();
    host.files = files;
    var code = 'var __acLoad = [];';
    files.forEach(function (f) {
      var p = AC.sys.fwd(AC.sys.join(dir, f));
      code += 'try { $.evalFile(' + lit(p) + '); __acLoad.push("ok"); } catch (e) { __acLoad.push(' + lit('ERR:' + f + ':') + ' + e.line + ": " + e); }';
    });
    code += '__acLoad.join("\\n");';
    var t0 = Date.now();
    return host.eval(code, 60000).then(function (res) {
      var rows = String(res).split('\n');
      host.loadErrors = rows.filter(function (r) { return r.indexOf('ERR:') === 0; });
      if (res === 'EvalScript error.') host.loadErrors.push('EvalScript error.');
      host.loadedMs = Date.now() - t0;
      host.loadErrors.forEach(function (e) { AC.log.error(e, 'host load'); });
      AC.log.info('host: ' + files.length + ' jsx files loaded in ' + host.loadedMs + ' ms');
      loaded = true; readyResolve(true);
      host.sendStrings();
      return { files: files, errors: host.loadErrors };
    }, function (e) {
      AC.log.error(e, 'host load'); host.loadErrors.push(U.errMsg(e));
      loaded = true; readyResolve(false);
      return { files: files, errors: host.loadErrors };
    });
  };
})();
