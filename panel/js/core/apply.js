/* apply.js: AC.apply, applying engine plans to the timeline (docs/SPEC.md section 4). Always non-destructive.
   AC.apply.removeRanges(ranges, opts) -> Task resolving {mode, id, name, origId, origName, count, removed, before, after, lost}
     <= 150 merged ranges: clone "<name> (Klipora)" + QE extract in chunks (last chunk first, progress per chunk).
     >  150: export FCP XML -> engine job {tool: "xmeml", action: "cut"} -> import + open. Falls back to
        clone + extract when the export would lose effects or the engine action fails.
   AC.apply.markers(list, {tag, replace}) -> Promise<{n}>
   AC.apply.plan(plan, opts) -> Promise (remove_ranges | markers | xml_import; other kinds belong to tools). */
(function () {
  'use strict';
  var AC = window.AC, U = AC.util;
  var A = AC.apply = { MAX_EXTRACT: 150, CHUNK: 25 };

  // Merge in FRAME space exactly like tlMergeRanges (host), so chunk indexes agree on both sides.
  A.mergeFrames = function (ranges, fps, lastFrame) {
    var fr = [];
    (ranges || []).forEach(function (r) {
      var a = Math.max(0, Math.round(Number(r[0]) * fps)), b = Math.round(Number(r[1]) * fps);
      if (lastFrame) b = Math.min(b, lastFrame);
      if (b > a) fr.push([a, b]);
    });
    fr.sort(function (x, y) { return x[0] - y[0]; });
    var out = [];
    fr.forEach(function (f) {
      var last = out[out.length - 1];
      if (last && f[0] <= last[1]) { if (f[1] > last[1]) last[1] = f[1]; } else out.push([f[0], f[1]]);
    });
    return out;
  };

  A.removeRanges = function (ranges, opts) {
    opts = opts || {};
    var max = opts.maxExtract || A.MAX_EXTRACT, chunk = opts.chunk || A.CHUNK;
    var task = new AC.Task({ tool: opts.tool || 'apply', action: 'remove', title: opts.title || AC.t('tool.applyCuts'), label: opts.label || AC.t('tool.applyCuts'), kind: 'host' });
    if (opts.record !== false) AC.history.track(task);
    var cancelled = false;
    task._cancel = function () { cancelled = true; };
    AC.seq.hold(task.promise);

    var seqId = opts.seqId || AC.seq.locked() || '';
    AC.host.json('bac_seqInfo', 'lite', seqId).then(function (s) {
      if (!s) throw U.err('NO_SEQ', AC.t('apply.noSeq'), AC.t('apply.noSeqHint'));
      task.seqName = s.name;
      var lastFrame = Math.round(s.duration * s.fps);
      var fr = A.mergeFrames(ranges, s.fps, lastFrame);
      if (!fr.length) throw U.err('NO_RANGES', AC.t('apply.noRanges'));
      var secs = fr.map(function (f) { return [f[0] / s.fps, f[1] / s.fps]; });
      var removed = fr.reduce(function (a, f) { return a + (f[1] - f[0]); }, 0) / s.fps;
      var name = opts.name || (s.name + AC.brand.suffix);
      var base = { count: fr.length, removed: removed, before: s.duration, after: Math.max(0, s.duration - removed), origId: s.id, origName: s.name };
      var useXml = opts.mode === 'xml' || (opts.mode !== 'extract' && fr.length > max);
      var run = useXml ? viaXml(task, s, secs, name, opts) : Promise.resolve(null);
      return run.then(function (res) {
        if (res) return res;
        return viaExtract(task, s, secs, name, chunk, function () { return cancelled; });
      }).then(function (res) { task.summary = AC.t('apply.summary', { n: fr.length, t: U.dtk(removed) }); task.done(U.assign(base, res)); });
    }).catch(function (e) { task.fail(e); });
    return task;
  };

  function viaExtract(task, s, secs, name, chunk, isCancelled) {
    var n = secs.length, chunks = Math.ceil(n / chunk);
    task.plan([{ id: 'clone', label: AC.t('apply.clone'), w: 0.1 }, { id: 'extract', label: AC.t('apply.cutN', { n: n }), w: 0.9 }]);
    task.stage({ id: 'clone' });
    return AC.host.exec('bac_cloneSeq', [name, s.id], { json: true, timeout: 60000 }).then(function (c) {
      task.stageDone('clone');
      task.stage({ id: 'extract', sub: AC.t('apply.origUntouched') });
      var k = chunks;
      function next() {
        if (k <= 0) return Promise.resolve();
        if (isCancelled()) throw U.err('CANCELLED', AC.t('apply.cancelledPartial', { name: c.name }), AC.t('apply.cancelledPartialHint'));
        k--;
        var from = k * chunk, to = Math.min(n, from + chunk);
        return AC.host.exec('bac_removeRanges', [c.id, secs, from, to], { json: true, timeout: 180000 }).then(function () {
          task.progress((chunks - k) / chunks * 100);
          return next();
        });
      }
      return next().then(function () {
        task.stageDone('extract');
        return { mode: 'extract', id: c.id, name: c.name };
      });
    });
  }

  // XML route for many ranges. Resolves null to fall back to extract.
  function viaXml(task, s, secs, name, opts) {
    var dir = AC.sys.join(AC.settings.workdir(s.name), 'xml');
    var stamp = U.wibStamp();
    var inXml = AC.sys.join(dir, 'cut_' + stamp + '_in.xml'), outXml = AC.sys.join(dir, 'cut_' + stamp + '_out.xml');
    task.plan([{ id: 'export', label: AC.t('apply.exportXml'), w: 0.15 }, { id: 'cut', label: AC.t('apply.cutXml', { n: secs.length }), w: 0.35 }, { id: 'import', label: AC.t('apply.importSeq'), w: 0.5 }]);
    task.stage({ id: 'export' });
    return AC.host.exec('bac_exportXml', [inXml, s.id], { json: true, timeout: 120000 }).then(function (ex) {
      if (!ex || !ex.ok) { task.log(AC.t('apply.xmlExportFail'), 'WARN'); return null; }
      if (ex.lost && ex.lost.length && opts.mode !== 'xml') {
        task.log(AC.t('apply.xmlLost', { n: ex.lost.length }), 'WARN');
        return null;
      }
      task.stageDone('export');
      task.stage({ id: 'cut' });
      var job = AC.engine.run({ tool: 'xmeml', action: 'cut', seq: null, record: false, title: AC.t('apply.xmlTitle'),
                                params: { xml: ex.path || inXml, out: outXml, ranges: secs, name: name } });
      job.on('progress', function () { task.progress(job.pct); });
      return job.promise.then(function (r) {
        task.stageDone('cut');
        task.stage({ id: 'import' });
        return AC.host.exec('bac_importXmlAndOpen', [(r && r.path) || outXml, name], { json: true, timeout: 120000 });
      }).then(function (imp) {
        task.stageDone('import');
        return { mode: 'xml', id: imp.id, name: imp.name, lost: ex.lost || [] };
      }, function (e) {
        if (e && e.code === 'CANCELLED') throw e;
        task.log(AC.t('apply.xmlCutFail', { msg: U.errMsg(e) }), 'WARN');
        return null;
      });
    });
  }

  A.markers = function (list, opts) {
    opts = opts || {};
    var tag = opts.tag || (list[0] && list[0].tag) || '';
    var clear = (opts.replace !== false && tag) ? AC.host.json('bac_clearMarkersByTag', tag, opts.seqId || '') : Promise.resolve({ n: 0 });
    return clear.then(function () {
      var n = 0, i = 0;
      function next() {
        if (i >= list.length) return Promise.resolve({ n: n });
        var part = list.slice(i, i + 200); i += 200;
        part.forEach(function (m) { if (tag && !m.tag) m.tag = tag; });
        return AC.host.exec('bac_addMarkers', [part, opts.seqId || ''], { json: true, timeout: 60000 }).then(function (r) { n += (r && r.n) || 0; return next(); });
      }
      return next();
    });
  };

  // Generic plan dispatcher. remove_ranges returns the Task (has .promise); others return Promises.
  A.plan = function (plan, opts) {
    opts = opts || {};
    if (!plan || !plan.kind) return Promise.reject(U.err('BAD_PLAN', AC.t('apply.emptyPlan')));
    if (plan.kind === 'remove_ranges') {
      if (plan.timebase && plan.timebase !== 'sequence') return Promise.reject(U.err('BAD_PLAN', AC.t('apply.badTimebase')));
      return A.removeRanges(plan.ranges || [], opts).promise;
    }
    if (plan.kind === 'markers') return A.markers(plan.markers || [], U.assign({ tag: plan.tag }, opts));
    if (plan.kind === 'xml_import') return AC.host.exec('bac_importXmlAndOpen', [plan.path, plan.name || ''], { json: true, timeout: 120000 });
    return Promise.reject(U.err('UNSUPPORTED_PLAN', AC.t('apply.unsupported', { kind: plan.kind })));
  };
})();
