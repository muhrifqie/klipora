/* viral.js: Viral Clips (tool id "viral", engine engine/ac/tools/viral.py, host host/36_viral.jsx).
   Setup (#tool/viral): clip length (preset 15-30 / 20-60 / 30-60 / 60-90 s or custom min-max), count
   (Auto / 3 / 5 / 10), style (Default, Education, Funny, Sales, Story, Custom + own instruction), optional topic,
   output switches -> engine "analyze" (AI windows in parallel + one global rescore, or rules "no AI").
   Review: shared review list (score badge, title, snippet, hook/why note, duration; click = seek, "Play" = play
   the range, sort score/time, filters, "Mark on timeline" = markers only).
   Apply: engine "apply" (names "Viral 01 - <title>", colours, frame snap) -> host: old [Klipora-VR] (and [AC-VR])
   markers cleared, one sub-sequence per clip (bac_viral_subseq: createSubsequence between temporary In/Out on the
   analysed sequence, In/Out restored, bin "Klipora Viral"; tlFindBin also finds the old "AutoCut Viral") ->
   markers coloured by score on the analysed sequence -> optional 9:16 through the resize tool when it exposes
   AC.tools.get('resize').api.vertical (else the switch is hidden) -> result card (open each clip, "Open original",
   "Delete result"). All UI text comes from the locale keys viral.* (id + en). Helpers exposed as AC.viral (tests). */
(function () {
  'use strict';
  var AC = window.AC, U = AC.util, ui = AC.ui;
  // Premiere names (the same in every UI language). tlFindBin also finds the old bin "AutoCut Viral".
  var TAG = '[Klipora-VR]', BIN = 'Klipora Viral', REFRAME_BIN = 'Auto Reframed Sequences';
  var NONAME = 'Tanpa nama';   // i18n-ignore (workdir folder name: must not change with the UI language)

  function T(k, v) { return AC.t(k, v); }
  function lengths() {
    var sec = ' ' + T('unit.sec');
    return [
      { value: 'cepat', label: '15-30' + sec, hint: T('viral.len15Hint'), values: { min: 15, max: 30 } },
      { value: 'standar', label: '20-60' + sec, hint: T('viral.len20Hint'), values: { min: 20, max: 60 } },
      { value: 'shorts', label: '30-60' + sec, hint: T('viral.len30Hint'), values: { min: 30, max: 60 } },
      { value: 'cerita', label: '60-90' + sec, hint: T('viral.len60Hint'), values: { min: 60, max: 90 } }
    ];
  }
  var STYLE_IDS = ['default', 'edukasi', 'lucu', 'jualan', 'cerita', 'custom'];
  var STYLE_KEYS = { 'default': 'Default', edukasi: 'Edu', lucu: 'Funny', jualan: 'Sales', cerita: 'Story', custom: 'Custom' };
  function styleOpts() {
    return STYLE_IDS.map(function (v) { return { value: v, label: T('viral.style' + STYLE_KEYS[v]), hint: T('viral.style' + STYLE_KEYS[v] + 'Hint') }; });
  }
  function countOpts() { return [[0, T('common.auto')], [3, '3'], [5, '5'], [10, '10']]; }

  /* ================================================================== pure helpers (mirror viral.py) */
  // About one clip per 3,5 mnt (max 20); short videos get what fits without overlap.
  function autoCount(dur, min, max) {
    var target = min + 0.4 * (max - min), n = Math.round(dur / 210);
    n = Math.max(n, Math.min(3, Math.floor(dur / (target * 1.2))));
    return Math.max(1, Math.min(20, n));
  }
  function scoreClass(s) { return s >= 80 ? 'is-hi' : s >= 60 ? 'is-mid' : 'is-lo'; }
  function scoreBadge(s) {
    s = Math.round(Number(s) || 0);
    return '<span class="viral-score ' + scoreClass(s) + '" title="' + U.esc(T('viral.scoreTitle', { s: s })) + '">' + s + '</span>';
  }
  function sortItems(items, mode) {
    items.sort(mode === 'time' ? function (a, b) { return a.t0 - b.t0; }
                               : function (a, b) { return (b.score || 0) - (a.score || 0) || a.t0 - b.t0; });
    return items;
  }
  function rowCtx(it) {
    var title = it.title || it.label || T('viral.clipWord'), snip = typeof it.ctx === 'string' ? it.ctx : (it.text || '');
    if (title.length > 12 && snip.toLowerCase().indexOf(title.toLowerCase()) === 0) snip = '... ' + snip.slice(title.length).replace(/^[\s,.]+/, '');   // rule titles = first words
    return '<b class="viral-ttl" data-i18n-skip>' + U.esc(title) + '</b><span class="viral-snip" data-i18n-skip>' + (U.esc(snip) || '&nbsp;') + '</span>';
  }
  function rowNote(it) {
    if (it.ungrounded && it.ungrounded.length) return { kind: 'warn', icon: 'alert', text: T('viral.ungrounded', { nums: it.ungrounded.join(', ') }) };
    if (it.hook) return { icon: 'spark', text: T('viral.hookPrefix') + ' ' + it.hook + (it.why ? (/[.!?]$/.test(it.hook) ? ' ' : '. ') + it.why : '') };
    if (it.why) return { icon: it.src === 'ai' ? 'spark' : 'info', text: it.why };
    return null;
  }
  function rowTags(it) {
    var h = scoreBadge(it.score);
    if (it.src && it.src !== 'ai') h += '<span class="tag tag-warn">' + U.esc(T('viral.tagNoAi')) + '</span>';
    if (typeof it.topic === 'number' && it.topic >= 0.5) h += '<span class="tag tag-ok">' + U.esc(T('viral.tagTopic')) + '</span>';
    return h;
  }
  function sourceTag(doc) {
    if (doc.source === 'fallback' || doc.ai === false) return ui.tag(T('viral.srcRule'), 'warn', 'alert');
    if (doc.source === 'mixed') return ui.tag(T('viral.srcMixed'), 'warn', 'spark');
    return ui.tag(T('viral.srcAi'), 'ai', 'spark');
  }
  // 9:16 through the resize tool. Preferred: its public hook api.vertical({seqId, name, ratio, tool}) ->
  // Promise<{id, name}>. Else, when the resize tool and its host file are loaded, its one-call Premiere Auto Reframe
  // (bac_resize_native, host/38_resize.jsx; analysis finishes in the background). Else null = switch hidden.
  function verticalApi() {
    var d = AC.tools.get('resize');
    if (!d) return null;
    if (d.api && typeof d.api.vertical === 'function') return d.api;
    var hostOk = (AC.host.files || []).some(function (f) { return /resize\.jsx$/i.test(f); });
    if (!hostOk) return null;
    return { native: true, vertical: function (o) {
      return AC.host.exec('bac_resize_native', [9, 16, 'default', o.name, o.seqId], { json: true, timeout: 60000 });
    } };
  }
  function each(list, fn) {
    var i = 0;
    function next() { if (i >= list.length) return Promise.resolve(); var k = i++; return Promise.resolve(fn(list[k], k)).then(next); }
    return next();
  }
  AC.viral = { autoCount: autoCount, scoreClass: scoreClass, sortItems: sortItems, rowCtx: rowCtx, rowNote: rowNote, rowTags: rowTags, verticalApi: verticalApi, TAG: TAG, BIN: BIN };

  AC.tools.register({
    id: 'viral', group: 'publikasi', order: 2, icon: 'viral',
    badges: ['ai'],
    text: function (t) {
      return { title: t('viral.title'), tab: t('viral.tab'), desc: t('viral.desc'), lead: t('viral.lead'), cta: t('viral.cta'),
               next: [{ tool: 'captions', reason: t('viral.nextCaptions') }, { tool: 'resize', reason: t('viral.nextResize') }] };
    },
    defaults: { length: 'standar', min: 20, max: 60, count: 0, preset: 'default', prompt: '', topic: '', subseq: true, vertical: false, ai: true, sort: 'score' },

    render: function (el, ctx) {
      var st = ctx.state, current = { doc: null, file: '' }, playTimer = null;

      el.appendChild(ui.h('div', { class: 'tool-hero', html: '<span class="tool-ic">' + ui.icon('viral') + '</span><div><p>' + U.esc(ctx.def.lead) + '</p><div class="tags">' + ui.badge('ai') + '</div></div>' }));
      var aiBox = ui.h('div', { class: 'viral-ai' }), resumeBox = ui.h('div', { class: 'viral-resume' });
      el.appendChild(aiBox); el.appendChild(resumeBox);

      /* ---------- durasi */
      var durSec = ui.section({ title: T('viral.lenTitle') });
      durSec.el.classList.add('viral-len');
      var lens = ui.presets({ options: lengths(), value: st.length, ariaLabel: T('viral.lenTitle'), customHint: T('viral.lenCustomHint'),
        onChange: function (v, p) { st.length = v; st.min = p.values.min; st.max = p.values.max; minS.set(st.min, true); maxS.set(st.max, true); adv.setAside(''); ctx.save(); paintEstimate(); } });
      durSec.add(lens);
      el.appendChild(durSec.el);

      /* ---------- jumlah */
      var cntSec = ui.section({ title: T('viral.countTitle') });
      var estimate = ui.h('p', { class: 'help viral-est' });
      cntSec.add(ui.segmented({ options: countOpts(), value: st.count || 0, ariaLabel: T('viral.countTitle'), onChange: function (v) { st.count = v; ctx.save(); paintEstimate(); } }));
      cntSec.add(estimate);
      el.appendChild(cntSec.el);

      /* ---------- gaya */
      var styleSec = ui.section({ title: T('viral.styleTitle') });
      var STYLES = styleOpts();
      var styleHint = ui.h('p', { class: 'help' });
      var styles = ui.chips({ options: STYLES.map(function (s) { return { value: s.value, label: s.label }; }), value: st.preset, ariaLabel: T('viral.styleAria'),
        onChange: function (v) { st.preset = v; ctx.save(); paintStyle(); paintDock(); } });
      var prompt = ui.h('textarea', { class: 'input viral-prompt', rows: 3, maxlength: 400, spellcheck: 'false', 'aria-label': T('viral.promptAria'),
        placeholder: T('viral.promptPh') });
      prompt.value = st.prompt || '';
      prompt.addEventListener('input', function () { st.prompt = prompt.value; ctx.save(); ctx.setPrimary({ disabled: customEmpty() }); });
      styleSec.add([styles, styleHint, prompt]);
      el.appendChild(styleSec.el);
      function paintStyle() {
        var s = STYLES.filter(function (x) { return x.value === st.preset; })[0] || STYLES[0];
        styleHint.textContent = s.hint;
        prompt.hidden = st.preset !== 'custom';
      }

      /* ---------- topik */
      var topicSec = ui.section({ title: T('viral.topicTitle'), aside: T('viral.optional') });
      var topic = ui.input({ value: st.topic, placeholder: T('viral.topicPh'), ariaLabel: T('viral.topicAria'),
        onInput: function (v) { st.topic = v; ctx.save(); } });
      topicSec.add([topic, ui.h('p', { class: 'help', text: T('viral.topicHelp') })]);
      el.appendChild(topicSec.el);

      /* ---------- hasil */
      var outSec = ui.section({ title: T('viral.outTitle') });
      var subSw = ui.switch({ label: T('viral.subseq'), help: T('viral.subseqHelp', { bin: BIN }),
        checked: st.subseq !== false, onChange: function (v) { st.subseq = v; ctx.save(); paintVertical(); paintDock(); } });
      var vertWrap = ui.h('div', { class: 'viral-vert' });
      var vertSw = ui.switch({ label: T('viral.vertical'), help: T('viral.verticalHelp'), checked: !!st.vertical,
        onChange: function (v) { st.vertical = v; ctx.save(); } });
      var vertNote = ui.h('p', { class: 'help viral-vert-note', text: T('viral.verticalNote') });
      vertWrap.appendChild(vertSw.el);
      vertWrap.appendChild(vertNote);
      outSec.add([subSw, vertWrap, ui.h('p', { class: 'help', text: T('viral.markersHelp', { tag: TAG }) })]);
      el.appendChild(outSec.el);
      function paintVertical() { var api = verticalApi(); vertWrap.hidden = !api || st.subseq === false; vertNote.hidden = !(api && api.native); }

      /* ---------- lanjutan */
      var adv = ui.advanced({ title: T('common.advanced'), key: 'viral' });
      function manualLen() { st.length = lens.sync({ min: st.min, max: st.max }); adv.setAside(st.length ? '' : T('common.custom')); ctx.save(); paintEstimate(); }
      var minS = ui.slider({ label: T('viral.minLen'), min: 5, max: 120, step: 5, value: st.min, unit: ' ' + T('unit.sec'), decimals: 0,
        onInput: function (v) { st.min = v; if (st.max < v + 5) { st.max = v + 5; maxS.set(st.max, true); } manualLen(); } });
      var maxS = ui.slider({ label: T('viral.maxLen'), min: 15, max: 180, step: 5, value: st.max, unit: ' ' + T('unit.sec'), decimals: 0, help: T('viral.maxLenHelp'),
        onInput: function (v) { st.max = v; if (st.min > v - 5) { st.min = Math.max(5, v - 5); minS.set(st.min, true); } manualLen(); } });
      var aiSw = ui.switch({ label: T('viral.useAi'), badge: ui.badge('ai'), help: T('viral.useAiHelp'),
        checked: st.ai !== false, onChange: function (v) { st.ai = v; ctx.save(); paintAi(); paintDock(); } });
      adv.add([minS, maxS, aiSw]);
      if (!lens.match({ min: st.min, max: st.max })) { lens.sync({ min: st.min, max: st.max }); adv.setAside(T('common.custom')); }
      el.appendChild(adv.el);

      function aiOn() { return st.ai !== false && AC.settings.get('ai') !== false; }
      function paintAi() {
        aiBox.innerHTML = '';
        var h = AC.engine.lastHealth;
        if (st.ai === false) return;
        if (AC.settings.get('ai') === false) {
          aiBox.appendChild(ui.alert({ kind: 'info', title: T('viral.aiOffTitle'), text: T('viral.aiOffText') }));
        } else if (h && h.ai && !h.ai.ok && !h.busy) {
          aiBox.appendChild(ui.alert({ kind: 'warn', title: T('viral.aiDownTitle'), text: T('viral.aiDownText') }));
        }
      }
      function scopeDur() {
        var s = AC.seq.peek(); if (!s) return 0;
        var sc = ctx.scope();
        return sc && sc.kind === 'inout' && sc.t1 > sc.t0 ? sc.t1 - sc.t0 : s.duration;
      }
      function paintEstimate() {
        var dur = scopeDur();
        if (!dur) { estimate.textContent = T('viral.estNoSeq'); return; }
        if (dur < st.min * 0.8) {
          var sc = ctx.scope();
          estimate.textContent = T(sc && sc.kind === 'inout' ? 'viral.estTooShortInOut' : 'viral.estTooShort', { dur: U.mmss(dur) });
          return;
        }
        var n = st.count || autoCount(dur, st.min, st.max);
        estimate.textContent = T(st.count ? 'viral.estFixed' : 'viral.estAuto', { dur: U.mmss(dur), n: n, range: st.min + '-' + st.max });
      }
      function reviewPath(name) { return AC.sys.node ? AC.sys.join(AC.settings.workdir(name || NONAME), 'viral_review.json') : ''; }
      function paintResume() {
        resumeBox.innerHTML = '';
        var s = AC.seq.peek(), file = s && reviewPath(s.name);
        if (!file || !AC.sys.exists(file)) return;
        var d = AC.sys.readJSON(file, null);
        if (!d || d.tool !== 'viral' || !d.seq || d.seq.id !== s.id || !(d.items || []).length) return;
        var when = d.created ? Date.parse(d.created) : 0;
        resumeBox.appendChild(ui.h('button', { class: 'linkbtn', type: 'button', html: ui.icon('clock') + U.esc(T(when ? 'viral.lastResultAt' : 'viral.lastResult', { n: d.items.length, time: when ? U.wibTime(when) : '' })),
          on: { click: function () { openReview(file); } } }));
      }
      function customEmpty() { return st.preset === 'custom' && !String(st.prompt || '').trim(); }
      function paintDock() {
        var custom = customEmpty();
        ctx.dock({ primary: { label: aiOn() ? ctx.def.cta : T('viral.ctaNoAi'), icon: 'viral', onClick: run, disabled: custom,
                              title: custom ? T('viral.customFirst') : '' } });
      }
      paintStyle(); paintVertical(); paintAi(); paintEstimate(); paintResume(); paintDock();
      AC.bus.on('health', paintAi);
      AC.settings.on('change', function (k) { if (k === 'ai') { paintAi(); paintDock(); } });
      AC.bus.on('tools', paintVertical);
      ctx.paint = function () { paintVertical(); paintAi(); paintEstimate(); paintResume(); };

      /* ---------- analyze */
      function run() {
        var params = { min: st.min, max: st.max, count: st.count || 0, preset: st.preset, ai: st.ai !== false,
                       prompt: st.preset === 'custom' ? String(st.prompt || '').trim() : '', topic: String(st.topic || '').trim() };
        ctx.run({ action: 'analyze', title: T('viral.runTitle'), params: params, onResult: function (data) { openReview(data.review); } });
      }

      /* ---------- review */
      function openReview(file) {
        var doc;
        try { doc = ui.loadReview(file); } catch (e) { ctx.error(e); return; }
        sortItems(doc.items, st.sort);
        current.doc = doc; current.file = file;
        var filters = [{ id: 'top', label: T('viral.filterTop'), test: function (it) { return (it.score || 0) >= 60; } }];
        if (doc.items.some(function (it) { return typeof it.topic === 'number'; })) filters.push({ id: 'topic', label: T('viral.filterTopic'), test: function (it) { return it.topic >= 0.5; } });
        var list = ctx.review({ doc: doc, file: file, unit: function (n) { return T('viral.unit', { n: n }); }, verbOn: T('viral.verbOn'), verbOff: T('viral.verbOff'), markerTag: TAG,
          title: function (on, all, sec) { return U.esc(T('viral.rvTitle', { n: on })) + '<small>' + U.esc(T('viral.rvSub', { n: all, total: U.dtk(sec) })) + '</small>'; },
          ctx: rowCtx, note: rowNote, tags: rowTags, filters: filters,
          toggleText: function (it) { return it.on ? T('viral.skipThis') : T('viral.pickThis'); },
          rowActions: [{ label: T('viral.play'), icon: 'play', run: playItem }],
          bulkAction: { label: T('viral.markTimeline'), run: markersOnly },
          primary: { label: function (n) { return T(st.subseq !== false ? 'viral.makeN' : 'viral.markN', { n: n }); }, onApply: applyClips } });
        if (!list) return;
        list.el.classList.add('viral-rv');
        var head = list.el.querySelector('.rv-head');
        var bar = ui.h('div', { class: 'viral-bar', html: sourceTag(doc) });
        var sort = ui.segmented({ options: [['score', T('viral.sortScore')], ['time', T('viral.sortTime')]], value: st.sort === 'time' ? 'time' : 'score', small: true, ariaLabel: T('viral.sortAria'),
          onChange: function (v) { st.sort = v; ctx.save(); sortItems(doc.items, v); list.refresh(); } });
        bar.appendChild(ui.h('span', { class: 'viral-sort' }, [ui.h('span', { text: T('viral.sortLabel') }), sort.el]));
        head.insertBefore(bar, head.children[1] || null);
        if (doc.source === 'fallback' || doc.ai === false) {
          head.insertBefore(ui.h('p', { class: 'help viral-why', text: T('viral.ruleWhy') }), bar.nextSibling);
        } else if (doc.warnings && doc.warnings.length) {
          head.insertBefore(ui.h('p', { class: 'help viral-why', text: doc.warnings[0] }), bar.nextSibling);
        }
      }

      function docSeq(doc) { return (doc && doc.seq) || {}; }
      function playItem(it) {
        clearTimeout(playTimer);
        AC.host.json('bac_viral_play', it.t0, docSeq(current.doc).id || '').then(function (r) {
          if (r && r.played) {
            ui.toast(T('viral.playing', { a: U.tcode(it.t0), b: U.tcode(it.t1) }), 'play');
            playTimer = setTimeout(function () { AC.host.json('bac_viral_stop').catch(function () {}); }, Math.max(1, it.t1 - it.t0) * 1000 + 300);   // + Premiere's playback start latency
          } else ui.toast(T('viral.pressSpace'), 'playhead');
        }, function (e) { ui.toast(T('viral.playFail', { msg: U.errMsg(e) }), { kind: 'err' }); });
      }

      function markersOnly(list) {
        if (!list.count()) { ui.toast(T('viral.noneTicked')); return; }
        try { list.save(current.file); } catch (e) { ui.toast(T('viral.saveFail', { msg: U.errMsg(e) }), { kind: 'err' }); return; }
        var doc = current.doc;
        ctx.run({ action: 'apply', review: current.file, seq: null, workdir: AC.sys.dirname(current.file), title: T('viral.markRun'), onResult: function (data) {
          AC.apply.markers((data.plan && data.plan.markers) || [], { tag: TAG, replace: true, seqId: docSeq(doc).id || '' }).then(function (r) {
            ui.toast(T('viral.markersAdded', { n: r.n }), 'marker');
            ctx.go('review');
          }, function (e) { ctx.error(e); });
        } });
      }

      /* ---------- apply */
      function applyClips(items, doc, file) {
        ctx.run({ action: 'apply', review: file, seq: null, workdir: AC.sys.dirname(file), title: T('viral.applyRun'), onResult: function (data) { make(data, doc); } });
      }

      function make(data, doc) {
        var seq = docSeq(doc), seqId = seq.id || '', clips = data.clips || [], markers = (data.plan && data.plan.markers) || [];
        var subseq = st.subseq !== false, api = subseq && st.vertical ? verticalApi() : null;
        var task = new AC.Task({ tool: 'viral', action: 'make', title: ctx.def.title, label: subseq ? T('viral.taskMake') : T('viral.taskMark'), kind: 'host', seqName: seq.name || '' });
        var stages = [];
        if (subseq) stages.push({ id: 'seq', label: T('viral.stageSeq', { n: clips.length }), w: 0.65, sub: T('viral.origUntouched') });
        stages.push({ id: 'markers', label: T('viral.stageMarkers'), w: 0.15 });
        if (api) stages.push({ id: 'vertical', label: T('viral.vertical'), w: 0.5 });
        task.plan(stages);
        AC.history.track(task);
        var cancelled = false, made = [], problems = [], vert = [], nMarkers = 0;
        task._cancel = function () { cancelled = true; };
        function stopIfCancelled() {
          if (!cancelled) return;
          if (made.length) throw { partial: true };   // keep what exists reachable: partial result card (Buka, Hapus hasil)
          throw U.err('CANCELLED', T('viral.cancelledNothing'), '');
        }
        var opts = { title: subseq ? T('viral.makeRun') : T('viral.markRun'), note: T('viral.busyNote'),
                     onDone: function (res) {
                       showResult(res, data, doc);
                       if (res.cancelled) ui.toast(T('viral.cancelledMade', { n: res.made.length, bin: BIN }), 'alert');
                     }, onRetry: function () { make(data, doc); } };
        if (ctx.track) ctx.track(task, opts);
        else task.then(opts.onDone, function (e) { ctx.error(e, { onRetry: opts.onRetry }); });   // older core without ctx.track
        AC.seq.hold(task.promise);
        // Old [Klipora-VR] / [AC-VR] markers go first: createSubsequence copies markers inside the range into the new sequence.
        var p = AC.host.json('bac_clearMarkersByTag', TAG, seqId);
        if (subseq) {
          p = p.then(function () {
            task.stage({ id: 'seq' });
            return each(clips, function (c, i) {
              stopIfCancelled();
              return AC.host.exec('bac_viral_subseq', [seqId, c.t0, c.t1, c.name, BIN], { json: true, timeout: 120000 }).then(function (r) {
                made.push(U.assign({}, c, { seqId: r.id, seqName: r.name, dur: r.dur }));
              }, function (e) {
                if (e && (e.code === 'HOST_TIMEOUT' || e.code === 'NO_CEP')) throw e;
                problems.push(c.name + ': ' + U.errMsg(e));
              }).then(function () { task.progress((i + 1) / clips.length * 100); });
            }).then(function () {
              task.stageDone('seq');
              if (!made.length && problems.length) throw U.err('SUBSEQ', T('viral.subseqFail'), T('viral.subseqFailHint'), { log: problems.join('\n') });
            });
          });
        }
        p = p.then(function () {
          stopIfCancelled();
          task.stage({ id: 'markers' });
          return AC.apply.markers(markers, { tag: TAG, replace: false, seqId: seqId });
        }).then(function (r) { nMarkers = (r && r.n) || 0; task.stageDone('markers'); });
        if (api) {
          var dropBin = '';
          p = p.then(function () {
            task.stage({ id: 'vertical' });
            // Premiere puts Auto Reframe results in a root bin "Auto Reframed Sequences": move each one next to its
            // clip in BIN and drop that bin again when this run created it (so "Delete result" leaves nothing behind).
            return AC.host.json('bac_viral_binInfo', REFRAME_BIN).then(function (b) { dropBin = b && b.exists === false ? REFRAME_BIN : ''; }, function () {});
          }).then(function () {
            return each(made, function (m, i) {
              stopIfCancelled();
              // Auto Reframe works on the sequence in the timeline panel: show the clip first (the source is re-opened at the end)
              return AC.host.json('bac_openSequence', m.seqId).then(function () {
                var out = api.vertical({ seqId: m.seqId, name: m.seqName + ' 9x16', ratio: '9:16', tool: 'viral' });
                return out && out.promise ? out.promise : out;
              }).then(function (r) {
                if (!r || !r.id) return;
                vert.push({ id: r.id, name: r.name || '', of: m.seqId });
                return AC.host.json('bac_viral_adopt', r.id, BIN, dropBin).catch(function () {});
              }, function (e) { problems.push('9:16 ' + m.seqName + ': ' + U.errMsg(e)); }).then(function () { task.progress((i + 1) / made.length * 100); });
            }).then(function () { task.stageDone('vertical'); });
          });
        }
        var partial = false;
        p.then(null, function (e) {
          if (!(e && e.partial)) throw e;
          partial = true;
        }).then(function () {
          return seqId ? AC.host.json('bac_openSequence', seqId).catch(function () { return null; }) : null;
        }).then(function () {
          task.summary = (subseq ? (partial ? T('viral.sumMadeOf', { n: made.length, total: clips.length }) : T('viral.sumMade', { n: made.length })) : T('viral.sumMarked', { n: nMarkers })) +
                         (partial ? T('viral.sumCancelled') : '') + (problems.length ? T('viral.sumFailed', { n: problems.length }) : '');
          task.done({ made: made, problems: problems, vert: vert, markers: nMarkers, secs: task.elapsed(), cancelled: partial });
        }, function (e) { task.fail(e); });
      }

      function showResult(res, data, doc) {
        var seq = docSeq(doc), all = data.clips || [], clips = res.cancelled ? res.made : all, subseq = res.made.length > 0;
        var total = clips.reduce(function (a, c) { return a + (c.t1 - c.t0); }, 0);
        var best = clips.reduce(function (a, c) { return Math.max(a, c.score || 0); }, 0);
        var extra = ui.h('div', { class: 'viral-made' });
        function openBtn(id, name) {
          var b = ui.button({ label: T('common.open'), small: true, kind: 'ghost', title: T('viral.openName', { name: name }), onClick: function () {
            AC.host.json('bac_openSequence', id).then(function () { ui.toast(T('viral.opening', { name: name }), 'seq'); }, function (e) { ui.toast(U.errMsg(e), { kind: 'err' }); });
          } });
          b.setAttribute('data-i18n-skip', '');   // its tooltip carries the sequence name (user content)
          return b;
        }
        (subseq ? res.made : clips).forEach(function (m) {
          var row = ui.h('div', { class: 'viral-made-i', html: scoreBadge(m.score) + '<span class="viral-made-n" data-i18n-skip>' + U.esc(subseq ? m.seqName : m.name) + '</span><small>' + U.esc(U.dtk(m.t1 - m.t0)) + '</small>' });
          if (subseq) row.appendChild(openBtn(m.seqId, m.seqName));
          extra.appendChild(row);
          res.vert.filter(function (v) { return v.of === m.seqId; }).forEach(function (v) {
            var vr = ui.h('div', { class: 'viral-made-i is-vert', html: ui.tag('9:16', 'line') + '<span class="viral-made-n" data-i18n-skip>' + U.esc(v.name) + '</span>' });
            vr.appendChild(openBtn(v.id, v.name));
            extra.appendChild(vr);
          });
        });
        var made = res.made.map(function (m) { return m.seqId; }).concat(res.vert.map(function (v) { return v.id; }));
        ctx.result({
          title: res.cancelled && res.made.length < all.length ? T('viral.resPartial', { n: res.made.length, total: all.length }) : subseq ? T('viral.resReady', { n: res.made.length }) : T('viral.resMarked', { n: res.markers }),
          sub: T('viral.resSub', { time: U.dtk(res.secs || 0) }) + (res.markers ? T('viral.resSubMarkers', { n: res.markers, tag: TAG }) : '.'),
          statsList: [{ label: T('viral.statClips'), value: String(clips.length), accent: true }, { label: T('viral.statTotal'), value: U.dtk(total) }, { label: T('viral.statBest'), value: String(best) }],
          ribbon: { total: doc.duration || 0, spans: clips.map(function (c) { return [c.t0, c.t1]; }) },
          legend: [T('viral.legendOrig'), T('viral.legendClips', { n: clips.length, total: U.dtk(total) })],
          extra: extra,
          original: seq.id ? { id: seq.id, name: seq.name } : null,
          onEdit: function () { ctx.go('review'); },
          onDelete: made.length ? function () { cleanup(made, seq.id || ''); } : null,
          warn: res.cancelled ? { title: T('viral.cancelled'), text: T(res.made.length < all.length ? (res.markers ? 'viral.cancelRestClips' : 'viral.cancelRestClipsNoMarkers') : (res.markers ? 'viral.cancelRestVert' : 'viral.cancelRestVertNoMarkers')) }
              : res.problems.length ? { title: T('viral.stepsFailed', { n: res.problems.length }), text: res.problems.slice(0, 3).join('; ') } : null,
          next: ctx.def.next.filter(function (n) { return !(n.tool === 'resize' && res.vert.length); })
        });
      }

      function cleanup(ids, seqId) {
        AC.host.json('bac_viral_cleanup', ids, BIN).then(function (r) {
          return AC.host.json('bac_clearMarkersByTag', TAG, seqId).then(function (m) {
            ui.toast(T('viral.deleted', { n: r.deleted, m: (m && m.n) || 0 }), 'trash');
            ctx.go('review');
          });
        }, function (e) { ui.toast(T('viral.deleteFail', { msg: U.errMsg(e) }), { kind: 'err' }); });
      }
    },

    onShow: function (ctx) { if (ctx.paint) ctx.paint(); },
    onSeq: function (lite, ctx) { if (ctx.paint) ctx.paint(); },
    onScope: function (scope, ctx) { if (ctx.paint) ctx.paint(); }
  });
})();
