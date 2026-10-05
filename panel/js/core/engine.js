/* engine.js: AC.engine, the job runner for the Python engine (docs/SPEC.md section 2).
   AC.engine.run({tool, action, params, seq?, workdir?, review?, title?, stages?}) -> Job (an AC.Task)
     writes %TEMP%\AutoCutBOT\jobs\<id>.json, spawns `python -X utf8 engine/cli.py run <job.json>`, parses the
     JSON-lines events, cancel() = taskkill /T /F, tracked in AC.history.
   AC.engine.worker(req) -> Job through the persistent engine/worker.py (request ids, auto-restart; falls back to
     run() when worker.py is missing).
   AC.engine.health() -> Promise<data> (`cli.py health`), AC.engine.cli(args) -> Promise<{events, result, log}>. */
(function () {
  'use strict';
  var AC = window.AC, U = AC.util;
  var E = AC.engine = { jobs: [] };

  E.python = function () { return AC.settings.get('python') || 'python'; };
  E.available = function () { return AC.sys.node && AC.sys.exists(AC.sys.paths.cli); };

  /* ---------------- JSON-lines parsing */
  function LineReader(onLine) { this.buf = ''; this.onLine = onLine; }
  LineReader.prototype.push = function (chunk) {
    this.buf += String(chunk);
    var parts = this.buf.split(/\r?\n/);
    this.buf = parts.pop();
    for (var i = 0; i < parts.length; i++) if (parts[i].trim()) this.onLine(parts[i]);
  };
  LineReader.prototype.flush = function () { if (this.buf.trim()) this.onLine(this.buf); this.buf = ''; };
  E.LineReader = LineReader;

  // Route one stdout line into a Task. Returns the parsed event (or null for plain log lines).
  function feed(task, line) {
    var s = line.trim(), ev = null;
    if (s.charAt(0) === '{') { try { ev = JSON.parse(s); } catch (e) { ev = null; } }
    if (!ev || typeof ev !== 'object' || !ev.ev) {
      var m = /^PROGRESS\s+(\d+(?:\.\d+)?)/.exec(s);   // legacy engine/autocut.py + caption.py
      if (m) { task.progress(parseFloat(m[1])); return { ev: 'progress', pct: parseFloat(m[1]) }; }
      task.log(s);
      return null;
    }
    switch (ev.ev) {
      // worker.py wraps events with the request id, so the stage id travels in `stage` (cli.py sends both).
      case 'stage': task.stage(U.assign({}, ev, { id: ev.stage || ev.id })); break;
      case 'progress': task.progress(typeof ev.pct === 'number' ? ev.pct : undefined, ev.all, ev.note); break;
      case 'stage_done': task.stageDone(ev.stage || ev.id, ev.sec, ev.note); break;
      case 'log': task.log(ev.msg); break;
      case 'warn': task.log(ev.msg, 'WARN'); break;
      case 'result': task._result = ev.data; task._hasResult = true; break;
      case 'error': task._error = ev; task.log((ev.code || 'ERROR') + ': ' + ev.msg + (ev.hint ? ' (' + ev.hint + ')' : ''), 'ERROR'); break;
      default: task.log(s);
    }
    return ev;
  }
  E.feed = feed;

  function engineError(ev) { return U.err(ev.code || 'ENGINE', ev.msg || AC.t('engine.failed'), ev.hint || ''); }

  // History line from result.summary: a string as is, or the engine's review.summary() dict {n, on, sec_on}.
  function summaryText(s) {
    if (typeof s === 'string') return s;
    if (!s || typeof s !== 'object' || typeof s.n !== 'number') return '';
    return AC.t('engine.sumFound', { n: s.n }) + (typeof s.on === 'number' ? ', ' + AC.t('engine.sumSelected', { n: s.on }) : '') +
      (s.sec_on ? ' (' + U.dtk(s.sec_on) + ')' : '');
  }
  E.summaryText = summaryText;

  function pythonError(e) {
    return U.err('PYTHON', AC.t('engine.noPython'),
      String(e && e.message || e), { action: 'settings' });
  }

  /* ---------------- job files */
  function jobId(tool) { return U.safeName(tool || 'job') + '-' + U.wibStamp() + '-' + Math.floor(Math.random() * 1e4); }

  // Delete our own job files older than 2 days (they are small, but never let %TEMP% grow forever).
  E.cleanup = function () {
    if (!AC.sys.node) return 0;
    var dir = AC.sys.paths.jobs, n = 0, old = Date.now() - 2 * 86400000;
    AC.sys.list(dir).forEach(function (f) {
      if (!/\.json$/i.test(f)) return;
      var p = AC.sys.join(dir, f);
      if (AC.sys.mtime(p) < old && AC.sys.remove(p)) n++;
    });
    return n;
  };

  // Build the job object (SPEC section 2). seq: undefined -> current full Timeline JSON; null -> none.
  function buildJob(opts) {
    var seqP = opts.seq !== undefined ? Promise.resolve(opts.seq) : AC.seq.current();
    return seqP.then(function (seq) {
      if (opts.seq === undefined && !seq) throw U.err('NO_SEQ', AC.t('apply.noSeq'), AC.t('apply.noSeqHint'));
      // lang = UI language for every user-facing engine text (stage labels, warnings, errors, option labels).
      var job = { id: opts.id || jobId(opts.tool), tool: opts.tool, action: opts.action || 'analyze', seq: seq || null, params: opts.params || {},
                  lang: AC.i18n ? AC.i18n.lang() : 'id' };
      job.workdir = opts.workdir || (seq ? AC.settings.workdir(seq.name) : AC.settings.workRoot());
      if (opts.review) job.review = opts.review;
      return job;
    });
  }
  E.buildJob = buildJob;

  /* ---------------- run (one process per job) */
  E.run = function (opts) {
    opts = opts || {};
    var task = new AC.Task({ id: opts.id || jobId(opts.tool), tool: opts.tool, action: opts.action, title: opts.title, label: opts.label,
                             stages: opts.stages, kind: 'engine' });
    if (opts.record !== false) AC.history.track(task);
    E.jobs.push(task);
    task.on('end', function () { E.jobs = E.jobs.filter(function (j) { return j !== task; }); });
    var proc = null, cancelled = false;
    task._cancel = function () {
      cancelled = true;
      if (proc && proc.pid) AC.sys.killTree(proc.pid);
      setTimeout(function () { task.fail(U.err('CANCELLED', AC.t('job.cancelled'))); }, proc ? 3000 : 0);
    };
    if (!AC.sys.node) { task.fail(U.err('NO_NODE', AC.t('engine.noNode'), AC.t('engine.noNodeHint'))); return task; }
    if (!AC.sys.exists(AC.sys.paths.cli)) { task.fail(U.err('NO_ENGINE', AC.t('engine.noEngineAt', { path: AC.sys.paths.cli }), AC.t('engine.noEngineHint'))); return task; }

    buildJob(U.assign({}, opts, { id: task.id })).then(function (job) {
      if (cancelled) return;
      task.job = job; task.seqName = job.seq ? job.seq.name : ''; task.meta.workdir = job.workdir;
      var jobPath = AC.sys.join(AC.sys.paths.jobs, job.id + '.json');
      AC.sys.writeJSON(jobPath, job);
      task.jobPath = jobPath;
      var args = ['-X', 'utf8', AC.sys.paths.cli, 'run', jobPath];
      task.log('$ ' + E.python() + ' ' + args.join(' '));
      task.setState('running');
      try {
        proc = AC.sys.spawn(E.python(), args, { cwd: AC.sys.paths.engine, env: AC.sys.childEnv() });
      } catch (e) { task.fail(pythonError(e)); return; }
      task.pid = proc.pid;
      var out = new LineReader(function (l) { feed(task, l); });
      var err = new LineReader(function (l) { task.log(l, 'STDERR'); });
      proc.stdout.on('data', function (d) { if (!task.pid) task.pid = proc.pid; out.push(d); });
      proc.stderr.on('data', function (d) { err.push(d); });
      proc.on('error', function (e) { task.fail(e && e.code === 'ENOENT' ? pythonError(e) : U.err('SPAWN', U.errMsg(e))); });
      proc.on('close', function (code) {
        out.flush(); err.flush();
        if (cancelled) { task.fail(U.err('CANCELLED', AC.t('job.cancelled'))); return; }
        if (task._error) { task.fail(engineError(task._error)); return; }
        if (code === 0 && task._hasResult) {
          if (task._result && !task.summary) task.summary = summaryText(task._result.summary);
          task.done(task._result); return;
        }
        var tail = task.logLines.slice(-6).join('\n');
        if (code === 0) task.fail(U.err('NO_RESULT', AC.t('engine.noResult'), tail));
        else task.fail(U.err('EXIT', AC.t('engine.exit', { code: String(code) }), tail));
      });
    }, function (e) { task.fail(e); });
    return task;
  };

  /* ---------------- persistent worker (engine/worker.py) */
  var W = { proc: null, next: 1, pending: {}, crashes: [], reader: null };
  function workerStart() {
    if (W.proc) return W.proc;
    var p = AC.sys.spawn(E.python(), ['-X', 'utf8', AC.sys.paths.worker], { cwd: AC.sys.paths.engine, env: AC.sys.childEnv() });
    W.proc = p;
    W.reader = new LineReader(function (line) {
      var obj = null;
      if (line.trim().charAt(0) === '{') { try { obj = JSON.parse(line); } catch (e) { obj = null; } }
      var t = obj && obj.id !== undefined ? W.pending[obj.id] : null;
      if (!t) { AC.log.info('worker: ' + line.slice(0, 300)); return; }
      var copy = U.assign({}, obj); delete copy.id;
      if (copy.stage !== undefined) copy.id = copy.stage;   // worker: "id" = request id, stage id is in "stage"
      feed(t, JSON.stringify(copy));
      if (copy.ev === 'result') {
        delete W.pending[obj.id];
        if (copy.data && !t.summary) t.summary = summaryText(copy.data.summary);
        t.done(copy.data);
      }
      else if (copy.ev === 'error') { delete W.pending[obj.id]; t.fail(engineError(copy)); }
    });
    p.stdout.on('data', function (d) { W.reader.push(d); });
    p.stderr.on('data', function (d) { AC.log.info('worker stderr: ' + String(d).slice(0, 500)); });
    p.on('error', function (e) { AC.log.error(e, 'worker'); });
    p.on('close', function (code) {
      W.proc = null;
      W.crashes.push(Date.now());
      var ids = Object.keys(W.pending);
      ids.forEach(function (id) { var t = W.pending[id]; delete W.pending[id]; t.fail(U.err('WORKER_EXIT', AC.t('engine.workerExit', { code: String(code) }), AC.t('engine.workerExitHint'))); });
      // Auto-restart (keeps models warm) unless it keeps crashing: max 3 restarts per minute.
      W.crashes = W.crashes.filter(function (t) { return Date.now() - t < 60000; });
      if (ids.length && W.crashes.length < 3) setTimeout(function () { try { workerStart(); } catch (e) { AC.log.error(e, 'worker restart'); } }, 500);
    });
    return p;
  }
  E.worker = function (req) {
    req = req || {};
    if (!AC.sys.exists(AC.sys.paths.worker)) return E.run(req);
    var task = new AC.Task({ tool: req.tool, action: req.action, title: req.title, label: req.label, stages: req.stages, kind: 'worker' });
    if (req.record) AC.history.track(task);
    var id = W.next++;
    task._cancel = function () {
      delete W.pending[id];
      // worker.py protocol: {"id": <new>, "cmd": "cancel", "target": <job request id>}
      try { if (W.proc) W.proc.stdin.write(JSON.stringify({ id: W.next++, cmd: 'cancel', target: id }) + '\n'); } catch (e) { /* worker gone */ }
      task.fail(U.err('CANCELLED', AC.t('job.cancelled')));
    };
    buildJob(req).then(function (job) {
      if (!task.isActive()) return;
      task.job = job; task.setState('running');
      try { workerStart(); } catch (e) { task.fail(pythonError(e)); return; }
      W.pending[id] = task;
      W.proc.stdin.write(JSON.stringify({ id: id, job: job }) + '\n');
    }, function (e) { task.fail(e); });
    return task;
  };
  E.stopWorker = function () { if (W.proc) { AC.sys.killTree(W.proc.pid); W.proc = null; } };

  /* ---------------- one-shot CLI commands (health, tools) */
  E.cli = function (args, timeoutMs) {
    return new Promise(function (resolve, reject) {
      if (!E.available()) { reject(U.err('NO_ENGINE', AC.t('engine.noEngine'))); return; }
      var task = new AC.Task({ tool: 'cli', action: args[0], kind: 'cli' });
      var proc;
      try { proc = AC.sys.spawn(E.python(), ['-X', 'utf8', AC.sys.paths.cli].concat(args), { cwd: AC.sys.paths.engine, env: AC.sys.childEnv() }); }
      catch (e) { reject(pythonError(e)); return; }
      var events = [], timer = setTimeout(function () { AC.sys.killTree(proc.pid); reject(U.err('TIMEOUT', AC.t('engine.timeout'))); }, timeoutMs || 30000);
      var out = new LineReader(function (l) { var ev = feed(task, l); if (ev) events.push(ev); });
      proc.stdout.on('data', function (d) { out.push(d); });
      proc.stderr.on('data', function (d) { task.log(String(d), 'STDERR'); });
      proc.on('error', function (e) { clearTimeout(timer); reject(e && e.code === 'ENOENT' ? pythonError(e) : e); });
      proc.on('close', function (code) {
        clearTimeout(timer); out.flush();
        if (task._error) { reject(engineError(task._error)); return; }
        resolve({ code: code, events: events, result: task._hasResult ? task._result : null, log: task.logLines.join('\n') });
      });
    });
  };

  // Engine + AI status (Settings health dot). Expected result data (flexible): {python, ffmpeg, gpu, ai: {ok, ms, model, key}}.
  E.lastHealth = null;
  E.health = function () {
    AC.bus.emit('health', { busy: true });
    var t0 = Date.now();
    return E.cli(['health'], 45000).then(function (r) {
      var d = r.result || {};
      var ai = d.ai || {};
      // engine/cli.py health(): ai = {ok, ms, base_url, model, has_key, disabled, why}; the key itself is never sent.
      var h = { busy: false, ok: r.code === 0 && d.ok !== false, ms: Date.now() - t0, data: d, issues: d.issues || [],
                ai: { ok: !!ai.ok, ms: typeof ai.ms === 'number' ? ai.ms : null, model: ai.model || '', hasKey: ai.has_key,
                      base: String(ai.base_url || '').replace(/^https?:\/\//, '').replace(/\/.*$/, ''), disabled: !!ai.disabled, msg: ai.why || '' } };
      E.lastHealth = h; AC.bus.emit('health', h); return h;
    }, function (e) {
      var h = { busy: false, ok: false, error: e, ai: { ok: false, msg: U.errMsg(e) } };
      E.lastHealth = h; AC.bus.emit('health', h); return h;
    });
  };

  // Python version check for Settings ("Test"): resolves "Python 3.14.3".
  E.pythonVersion = function (py) {
    return new Promise(function (resolve, reject) {
      var p, out = '';
      try { p = AC.sys.spawn(py || E.python(), ['--version'], { env: AC.sys.childEnv() }); } catch (e) { reject(pythonError(e)); return; }
      p.stdout.on('data', function (d) { out += d; });
      p.stderr.on('data', function (d) { out += d; });
      p.on('error', function (e) { reject(pythonError(e)); });
      p.on('close', function (code) { if (code === 0) resolve(out.trim()); else reject(U.err('PYTHON', AC.t('engine.pythonFail', { code: String(code) }), out.trim())); });
    });
  };

  // Never leave orphan python/ffmpeg processes when the panel reloads or closes.
  window.addEventListener('beforeunload', function () {
    E.jobs.forEach(function (j) { if (j.pid) AC.sys.killTree(j.pid); });
    E.stopWorker();
  });
})();
