/* history.js: AC.history, the History log. Live tasks (engine jobs + host work) are tracked in memory and
   finished ones are persisted to %APPDATA%\AutoCutBOT\history.json (last 50, log tail only) so they survive
   CEP cache clears. Record: {id, tool, action, title, status: running|ok|err|cancelled, sub, seqName, started,
   sec, error: {code,msg,hint}, log, warnings, workdir}. */
(function () {
  'use strict';
  var AC = window.AC, U = AC.util;
  var MAX = 50, LOG_TAIL = 80;
  var items = null, live = [], file = '';
  var bus = new AC.Emitter();

  function load() {
    if (items) return items;
    file = AC.sys.join(AC.sys.paths.appData, 'history.json');
    var saved = AC.sys.readJSON(file, []);
    items = Array.isArray(saved) ? saved : [];
    return items;
  }
  function persist() { try { AC.sys.writeJSON(file, items.slice(0, MAX)); } catch (e) { AC.log.error(e, 'save history'); } }

  function record(task) {
    var err = task.error;
    return {
      id: task.id, tool: task.tool, action: task.action, title: task.title || task.tool, kind: task.kind,
      status: task.state === 'done' ? 'ok' : task.state === 'error' ? 'err' : task.state === 'cancelled' ? 'cancelled' : 'running',
      sub: task.summary || (err ? (err.msg || err.message) : task.state === 'done' ? (task.label ? AC.t('history.labelDone', { label: task.label }) : AC.t('common.done')) : ''),
      seqName: task.seqName || '', started: task.started, sec: Math.round(task.elapsed() * 10) / 10,
      error: err ? { code: err.code, msg: err.msg || err.message, hint: err.hint || '' } : null,
      log: task.logLines.slice(-LOG_TAIL).join('\n'), warnings: task.warnings.slice(0, 10),
      workdir: task.meta && task.meta.workdir || ''
    };
  }

  var H = AC.history = {
    on: function (n, f) { bus.on(n, f); return H; },
    off: function (n, f) { bus.off(n, f); return H; },
    // Track a Task: shows as "running" until it ends, then it is persisted.
    track: function (task) {
      load();
      live.unshift(task);
      bus.emit('change');
      task.on('end', function () {
        live = live.filter(function (t) { return t !== task; });
        items.unshift(record(task));
        if (items.length > MAX) items.length = MAX;
        persist();
        bus.emit('change');
      });
      return task;
    },
    live: function () { return live.slice(); },
    running: function () { return live.filter(function (t) { return t.isActive(); }); },
    // Newest first: live tasks then persisted records.
    list: function () { load(); return live.map(record).concat(items); },
    recent: function (n) { return H.list().slice(0, n || 2); },
    clear: function () { load(); items = []; persist(); bus.emit('change'); }
  };
})();
