/* sys.js: the only place that touches Node (CEP --enable-nodejs --mixed-context). Everything else calls AC.sys.
   In headless tests tools/cep_stub.js provides a fake `require` (sandboxed fs + real or mocked processes).
   Paths: AC.sys.paths.{ext, root, engine, cli, worker, host, appData, local, temp, jobs, home, videos}. */
(function () {
  'use strict';
  var AC = window.AC, U = AC.util;
  var node = typeof window.require === 'function';
  var fs = null, path = null, cp = null, os = null;
  if (node) {
    try { fs = window.require('fs'); path = window.require('path'); cp = window.require('child_process'); os = window.require('os'); }
    catch (e) { node = false; AC.log.error(e, 'require'); }
  }
  var env = (node && window.process && window.process.env) || {};

  var sys = AC.sys = { node: node };
  sys.env = function (k) { return env[k]; };

  /* ---------------- paths (Windows) */
  function joinFallback() { return Array.prototype.slice.call(arguments).join('\\').replace(/[\\/]+/g, '\\'); }
  sys.join = function () { return path ? path.join.apply(path, arguments) : joinFallback.apply(null, arguments); };
  sys.dirname = function (p) { return path ? path.dirname(p) : String(p).replace(/[\\/][^\\/]*$/, ''); };
  sys.basename = function (p, ext) { return path ? path.basename(p, ext) : String(p).split(/[\\/]/).pop(); };
  sys.extname = function (p) { return path ? path.extname(p) : ((String(p).match(/\.[^.\\/]*$/) || [''])[0]); };
  sys.stem = function (p) { var b = sys.basename(p); return b.replace(/\.[^.]*$/, ''); };
  sys.fwd = function (p) { return String(p).replace(/\\/g, '/'); };

  function extDir() {
    var raw = '';
    try { if (window.__adobe_cep__) raw = window.__adobe_cep__.getSystemPath('extension'); } catch (e) { raw = ''; }
    if (!raw) raw = decodeURI(location.pathname).replace(/\/[^/]*$/, '');
    raw = decodeURI(String(raw)).replace(/^file:\/{2,3}/, '').replace(/^\/([A-Za-z]:)/, '$1');
    if (node) { try { raw = fs.realpathSync(raw); } catch (e) { /* junction unresolved: keep */ } }
    return raw.replace(/\//g, '\\');
  }
  var ext = extDir();
  var home = env.USERPROFILE || (os ? os.homedir() : 'C:\\Users\\Public');
  var temp = env.TEMP || env.TMP || (os ? os.tmpdir() : home + '\\AppData\\Local\\Temp');
  var migration = AC.migration = { done: [], failed: [] };   // filled by migrate() below
  var roaming = env.APPDATA || sys.join(home, 'AppData', 'Roaming'), localApp = env.LOCALAPPDATA || sys.join(home, 'AppData', 'Local');
  sys.paths = {
    ext: ext,
    root: sys.dirname(ext),
    engine: sys.join(sys.dirname(ext), 'engine'),
    host: sys.join(ext, 'host'),
    home: home,
    videos: sys.join(home, 'Videos'),
    // Klipora data folders. Older installs used "AutoCutBOT": migrated once below (engine/ac/util.py does the same).
    appData: migrate(sys.join(roaming, 'AutoCutBOT'), sys.join(roaming, 'Klipora')),
    local: migrate(sys.join(localApp, 'AutoCutBOT'), sys.join(localApp, 'Klipora')),
    temp: sys.join(temp, 'Klipora')
  };
  sys.paths.cli = sys.join(sys.paths.engine, 'cli.py');
  sys.paths.worker = sys.join(sys.paths.engine, 'worker.py');
  sys.paths.jobs = sys.join(sys.paths.temp, 'jobs');

  /* ---------------- one-time data migration %APPDATA%\AutoCutBOT -> %APPDATA%\Klipora (same for LOCALAPPDATA)
     Copy (never move) into "<new>.migrating-<pid>", then rename to <new>, so a crash or a second process never
     sees half a folder. The old folder stays as it is (backup; nothing else uses it). Lock/tmp files are skipped.
     When the copy fails the old folder keeps being used, so the panel still works with the user's settings. */
  function copyTree(a, b) {
    fs.mkdirSync(b, { recursive: true });
    fs.readdirSync(a).forEach(function (n) {
      if (/\.(lock|tmp)$/i.test(n)) return;
      var pa = path.join(a, n), pb = path.join(b, n);
      if (fs.statSync(pa).isDirectory()) copyTree(pa, pb);
      else if (fs.copyFileSync) fs.copyFileSync(pa, pb);
      else fs.writeFileSync(pb, fs.readFileSync(pa));
    });
  }
  function migrate(oldDir, newDir) {
    if (!node) return newDir;
    try {
      if (fs.existsSync(newDir) || !fs.existsSync(oldDir)) return newDir;
      var tmp = newDir + '.migrating-' + process_pid();
      copyTree(oldDir, tmp);
      fs.writeFileSync(path.join(tmp, 'MIGRATED_FROM.txt'), 'Copied from ' + oldDir + ' on first start of Klipora. The old folder is no longer used and can be deleted.\r\n');
      try { fs.renameSync(tmp, newDir); }
      catch (e) { if (!fs.existsSync(newDir)) throw e; /* another process won the race: use its copy */ }
      migration.done.push({ from: oldDir, to: newDir });
      return newDir;
    } catch (e) {
      migration.failed.push({ from: oldDir, to: newDir, error: String(e && e.message || e) });
      try { AC.log.error(e, 'migrate ' + oldDir); } catch (e2) { /* log not ready */ }
      return fs.existsSync(newDir) ? newDir : oldDir;
    }
  }

  /* ---------------- files (sync; CEP Node fs) */
  function need() { if (!node) throw U.err('NO_NODE', AC.t ? AC.t('engine.noNode') : 'Node.js is not enabled in this panel.', AC.t ? AC.t('misc.noNodeHint') : ''); }
  sys.exists = function (p) { if (!node) return false; try { return fs.existsSync(p); } catch (e) { return false; } };
  sys.mkdirp = function (d) { need(); if (!fs.existsSync(d)) fs.mkdirSync(d, { recursive: true }); return d; };
  sys.readText = function (p) { if (!node) return null; try { return fs.readFileSync(p, 'utf8').replace(/^\uFEFF/, ''); } catch (e) { return null; } };
  // Atomic write: tmp file + rename, so a crash never leaves half a settings/review file.
  sys.writeText = function (p, text) {
    need();
    sys.mkdirp(sys.dirname(p));
    var tmp = p + '.' + process_pid() + '.tmp';
    fs.writeFileSync(tmp, text, 'utf8');
    try { fs.renameSync(tmp, p); } catch (e) { fs.writeFileSync(p, text, 'utf8'); try { fs.unlinkSync(tmp); } catch (e2) { /* ignore */ } }
    return p;
  };
  function process_pid() { return (window.process && window.process.pid) || Math.floor(Math.random() * 1e6); }
  sys.readJSON = function (p, def) {
    var t = sys.readText(p);
    if (t === null) return def;
    try { return JSON.parse(t); } catch (e) { AC.log.warn('bad JSON: ' + p); return def; }
  };
  sys.writeJSON = function (p, obj, pretty) { return sys.writeText(p, JSON.stringify(obj, null, pretty ? 2 : 0)); };
  sys.list = function (d) { if (!node) return []; try { return fs.readdirSync(d); } catch (e) { return []; } };
  sys.remove = function (p) { if (!node) return false; try { fs.unlinkSync(p); return true; } catch (e) { return false; } };
  sys.mtime = function (p) { if (!node) return 0; try { return fs.statSync(p).mtime.getTime(); } catch (e) { return 0; } };

  /* ---------------- processes */
  // spawn without a console window. Returns the Node ChildProcess.
  sys.spawn = function (cmd, args, opts) {
    need();
    opts = U.assign({ windowsHide: true }, opts || {});
    return cp.spawn(cmd, args || [], opts);
  };
  // Kill a process and all its children (python -> ffmpeg). Node's child.kill() leaves ffmpeg running on Windows.
  sys.killTree = function (pid) {
    if (!node || !pid) return;
    try { cp.spawn('taskkill', ['/pid', String(pid), '/T', '/F'], { windowsHide: true }); } catch (e) { AC.log.error(e, 'taskkill'); }
  };
  sys.openFolder = function (p) {
    if (!node || !p) return false;
    try { cp.spawn('explorer', [String(p).replace(/\//g, '\\')], { windowsHide: false, detached: true }); return true; } catch (e) { return false; }
  };
  // AC_LANG = the UI language, so engine messages (health issues, `cli.py ai`, errors) match the panel.
  sys.childEnv = function (extra) { return U.assign({}, env, { PYTHONIOENCODING: 'utf-8', PYTHONUNBUFFERED: '1', AC_LANG: AC.i18n ? AC.i18n.lang() : 'id' }, extra || {}); };
})();
