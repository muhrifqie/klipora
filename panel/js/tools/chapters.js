/* chapters.js: Auto Chapters (tool id "chapters", engine engine/ac/tools/chapters.py, host host/35_chapters.jsx).
   Setup view (#tool/chapters): count preset less/normal/more, optional instruction, advanced (title style,
   minimum length, AI on/off, auto copy) -> engine "analyze" (AI parts in parallel, or rule fallback).
   Editor view (#tool/chapters/edit): editable titles and times, merge with previous, delete, add at the playhead,
   undo (Ctrl+Z), live YouTube rule warnings, YouTube text, "rewrite titles" (AI) and "title, description &
   hashtags" (one AI call). Edits are saved to the review file (chapters_review.json in the workdir).
   Apply: engine "apply" (validated list + chapters_youtube.txt) -> host bac_chapters_apply (replaces the
   [Klipora-CH] markers (and the old [AC-CH] ones) of the analysed sequence, type Chapter, cyan) -> clipboard ->
   result card. Markers never touch media. All UI text comes from the locale keys chapters.* (id + en).
   Pure helpers are exposed as AC.chapters (tests). */
(function () {
  'use strict';
  var AC = window.AC, U = AC.util, ui = AC.ui;
  var TAG = '[Klipora-CH]', YT_MIN = 10, MAX_UNDO = 30;
  var NONAME = 'Tanpa nama';   // i18n-ignore (workdir folder name: must not change with the UI language)

  function T(k, v) { return AC.t(k, v); }
  function counts() {
    return [
      { value: 'less', label: T('chapters.countLess'), hint: T('chapters.countLessHint') },
      { value: 'normal', label: T('chapters.countNormal'), hint: T('chapters.countNormalHint') },
      { value: 'more', label: T('chapters.countMore'), hint: T('chapters.countMoreHint') }
    ];
  }
  function styles() {
    return [{ value: 'langkah', label: T('chapters.styleSteps') }, { value: 'netral', label: T('chapters.styleNeutral') }, { value: 'menarik', label: T('chapters.styleCatchy') }];
  }
  function styleHelp(v) {
    var k = { langkah: 'chapters.styleStepsHelp', netral: 'chapters.styleNeutralHelp', menarik: 'chapters.styleCatchyHelp' }[v];
    return k ? T(k) : '';
  }
  function sourceLabel(src) {
    var k = { ai: 'chapters.srcAi', mixed: 'chapters.srcMixed', rule: 'chapters.srcRule', timeline: 'chapters.srcTimeline' }[src];
    return k ? T(k) : '';
  }
  function untitled(title) { return title || T('chapters.untitled'); }

  /* ================================================================== pure helpers (mirror chapters.py) */
  function pad(n) { return (n < 10 ? '0' : '') + n; }
  function targetCount(dur, mode) {
    var base = Math.max(3, Math.min(15, Math.round(dur / 240)));
    if (mode === 'less') return Math.max(3, Math.round(base * 0.6));
    if (mode === 'more') return Math.min(25, Math.round(base * 1.6));
    return base;
  }
  // YouTube timestamp (floor to the second): 04:05, or 1:02:05 for videos of one hour or more.
  function ytTime(t, hours) {
    t = Math.floor(Math.max(0, Number(t) || 0) + 1e-6);
    var h = Math.floor(t / 3600), m = Math.floor(t / 60) % 60, s = t % 60;
    return hours ? h + ':' + pad(m) + ':' + pad(s) : pad(Math.floor(t / 60)) + ':' + pad(s);
  }
  // "4:05", "04:05", "1:02:03", "245", "4.05" (= 4:05), "4:05,5" -> seconds, NaN when not a time.
  function parseTime(s) {
    s = String(s === undefined || s === null ? '' : s).trim().replace(/,/g, '.');
    if (!s) return NaN;
    if (s.indexOf(':') < 0 && /^\d+\.\d\d$/.test(s)) s = s.replace('.', ':');
    var parts = s.split(':');
    if (parts.length > 3) return NaN;
    var t = 0;
    for (var i = 0; i < parts.length; i++) {
      if (!/^\d+(\.\d+)?$/.test(parts[i]) || (i > 0 && parseFloat(parts[i]) >= 60)) return NaN;
      t = t * 60 + parseFloat(parts[i]);
    }
    return t;
  }
  function youtubeText(list, offset) {
    if (!list.length) return '';
    var lo = offset || 0, hours = list[list.length - 1].t - lo >= 3600;
    return list.map(function (c) { return ytTime(c.t - lo, hours) + ' ' + String(c.title || '').trim(); }).join('\n');
  }
  // YouTube chapter rules (support.google.com/youtube/answer/9884579): >= 3 chapters, first at 00:00, each >= 10 s.
  function issues(list, lo, hi) {
    var out = [], n = list.length;
    if (n < 3) out.push({ code: 'FEW', msg: T('chapters.issueFew', { n: n }) });
    if (n && list[0].t - lo > 0.5) out.push({ code: 'FIRST', msg: T('chapters.issueFirst') });
    list.forEach(function (c, k) {
      var len = (k + 1 < n ? list[k + 1].t : hi) - c.t;
      if (len < YT_MIN - 1e-6) out.push({ code: 'SHORT', i: k, msg: T('chapters.issueShort', { k: k + 1, s: U.dec(Math.max(0, len), 0) }) });
      if (!String(c.title || '').trim()) out.push({ code: 'EMPTY', i: k, msg: T('chapters.issueEmpty', { k: k + 1 }) });
    });
    return out;
  }
  function round3(x) { return Math.round(Number(x) * 1000) / 1000; }

  /* ================================================================== editor state */
  var E = { doc: null, file: '', list: [], lo: 0, hi: 0, seqId: '', seqName: '', source: '', warnings: [], undo: [], active: -1, meta: null, uid: 0 };

  function loadDoc(doc, file) {
    E.doc = doc; E.file = file || '';
    E.lo = Number(doc.offset) || 0;
    E.hi = Number(doc.end) || Number(doc.duration) || 0;
    E.seqId = (doc.seq && doc.seq.id) || ''; E.seqName = (doc.seq && doc.seq.name) || '';
    E.source = doc.source || 'rule'; E.warnings = doc.warnings || [];
    E.list = (doc.items || []).filter(function (it) { return it.on !== false; })
      .sort(function (a, b) { return a.t0 - b.t0; })
      .map(function (it) {
        return { id: it.id, t: Number(it.t0), title: String(it.title || it.label || ''), ctx: typeof it.ctx === 'string' ? it.ctx : '',
                 generic: !!it.generic, src: it.src || '', keywords: it.keywords || [], touched: !!it.touched };
      });
    E.meta = doc.meta || null; E.undo = []; E.active = E.list.length ? 0 : -1;
  }
  function docFromState() {
    var d = U.assign({}, E.doc);
    d.items = E.list.map(function (c, k) {
      var end = k + 1 < E.list.length ? E.list[k + 1].t : E.hi;
      return { id: c.id, t0: round3(c.t), t1: round3(Math.max(c.t, end)), kind: 'chapter', on: true, label: c.title, title: c.title,
               ctx: c.ctx || '', generic: !!c.generic, src: c.src || 'user', keywords: c.keywords || [], touched: !!c.touched };
    });
    d.youtube = youtubeText(E.list, E.lo);
    d.meta = E.meta || null;
    d.edited = Date.now();
    return d;
  }
  function snapshot() {
    E.undo.push(JSON.stringify(E.list));
    if (E.undo.length > MAX_UNDO) E.undo.shift();
  }
  function newId() { E.uid++; return 'u' + Date.now().toString(36) + E.uid; }
  function relLabel(t) { return ytTime(t - E.lo, E.hi - E.lo >= 3600); }

  AC.chapters = { targetCount: targetCount, ytTime: ytTime, parseTime: parseTime, youtubeText: youtubeText, issues: issues,
                  TAG: TAG, state: function () { return E; } };

  /* ================================================================== tool */
  AC.tools.register({
    id: 'chapters', group: 'teks', order: 2, icon: 'chapters',
    badges: ['ai'],
    text: function (t) {
      return { title: t('chapters.title'), tab: t('chapters.tab'), desc: t('chapters.desc'), lead: t('chapters.lead'), cta: t('chapters.cta') };
    },
    defaults: { count: 'normal', hint: '', style: 'langkah', minLen: 10, ai: true, copy: true },

    render: function (el, ctx) {
      var st = ctx.state, defs = ctx.def.defaults;
      Object.keys(defs).forEach(function (k) { if (st[k] === undefined || st[k] === null) st[k] = defs[k]; });   // state saved by an older version
      var setupEl = ui.h('div', { class: 'chapters-setup' }), editEl = ui.h('div', { class: 'chapters-edit', hidden: true });
      el.appendChild(setupEl); el.appendChild(editEl);
      var saveSoon = U.debounce(saveNow, 400);
      var view = 'setup';

      /* ---------------- setup view */
      setupEl.appendChild(ui.h('div', { class: 'tool-hero', html: '<span class="tool-ic">' + ui.icon('chapters') + '</span><div><p>' + U.esc(ctx.def.lead) + '</p><div class="tags">' + ui.badge('ai') + '</div></div>' }));
      var resumeBox = ui.h('div', { class: 'chapters-resume' });
      setupEl.appendChild(resumeBox);
      var aiBox = ui.h('div');
      setupEl.appendChild(aiBox);

      var secCount = ui.section({ title: T('chapters.countTitle') });
      var presets = ui.presets({ options: counts(), value: st.count, ariaLabel: T('chapters.countTitle'), onChange: function (v) { st.count = v; ctx.save(); paintEstimate(); } });
      var estimate = ui.h('p', { class: 'help chapters-est' });
      secCount.add(presets); secCount.add(estimate);
      setupEl.appendChild(secCount.el);

      var secHint = ui.section({ title: T('chapters.hintTitle'), aside: T('chapters.optional') });
      var hint = ui.h('textarea', { class: 'input chapters-hint', rows: 2, maxlength: 300, spellcheck: 'false', 'aria-label': T('chapters.hintAria'),
                                    placeholder: T('chapters.hintPh') });
      hint.value = st.hint || '';
      hint.addEventListener('input', function () { st.hint = hint.value; ctx.save(); });
      secHint.add(hint);
      secHint.add(ui.h('p', { class: 'help', text: T('chapters.hintHelp') }));
      setupEl.appendChild(secHint.el);

      var adv = ui.advanced({ title: T('common.advanced'), key: 'chapters' });
      var styleHelpEl = ui.h('small', { class: 'help', text: styleHelp(st.style) });
      var style = ui.segmented({ options: styles(), value: st.style, small: true, ariaLabel: T('chapters.styleTitle'), onChange: function (v) { st.style = v; ctx.save(); styleHelpEl.textContent = styleHelp(v); } });
      var styleField = ui.h('div', { class: 'field' }, [ui.h('span', { class: 'lbl', text: T('chapters.styleTitle') }), style.el, styleHelpEl]);
      var minLen = ui.slider({ label: T('chapters.minLen'), min: 10, max: 180, step: 5, value: st.minLen, unit: ' ' + T('unit.sec'), decimals: 0,
        help: T('chapters.minLenHelp'), onInput: function (v) { st.minLen = v; ctx.save(); } });
      var aiSw = ui.switch({ label: T('chapters.useAi'), badge: ui.badge('ai'), help: T('chapters.useAiHelp'), checked: st.ai !== false,
        onChange: function (v) { st.ai = v; ctx.save(); paintAi(); paintEstimate(); paintSetupDock(); } });
      var copySw = ui.switch({ label: T('chapters.autoCopy'), help: T('chapters.autoCopyHelp'), checked: st.copy !== false,
        onChange: function (v) { st.copy = v; ctx.save(); } });
      adv.add([styleField, minLen, aiSw, copySw]);
      setupEl.appendChild(adv.el);

      function aiOn() { return st.ai !== false && AC.settings.get('ai') !== false; }
      function paintAi() {
        aiBox.innerHTML = '';
        var h = AC.engine.lastHealth;
        if (st.ai === false) return;
        if (AC.settings.get('ai') === false) {
          aiBox.appendChild(ui.alert({ kind: 'info', title: T('chapters.aiOffTitle'), text: T('chapters.aiOffText') }));
        } else if (h && h.ai && !h.ai.ok) {
          aiBox.appendChild(ui.alert({ kind: 'warn', title: T('chapters.aiDownTitle'), text: T('chapters.aiDownText') }));
        }
      }
      function paintEstimate() {
        var s = AC.seq.peek();
        if (!s) { estimate.textContent = ''; return; }
        var sc = ctx.scope(), dur = sc.kind === 'inout' && sc.t1 > sc.t0 ? sc.t1 - sc.t0 : s.duration;
        var n = targetCount(dur, st.count);
        estimate.textContent = dur < 3 * YT_MIN ? T('chapters.estShort')
          : T('chapters.est', { dur: U.mmss(dur), n: n }) + (aiOn() && dur > 900 ? ' ' + T('chapters.estParallel') : '');
      }

      // "continue editing" when a review of the current sequence exists; "edit existing chapters" when it has markers.
      function paintResume() {
        resumeBox.innerHTML = '';
        var s = AC.seq.peek();
        if (!s) return;
        var file = reviewPath(s.name), doc = E.doc && E.seqId === s.id ? E.doc : null;
        if (!doc && file && AC.sys.exists(file)) {
          var d = AC.sys.readJSON(file, null);
          if (d && d.tool === 'chapters' && d.seq && d.seq.id === s.id && (d.items || []).length) doc = d;
        }
        if (doc) {
          var n = E.doc === doc ? E.list.length : doc.items.length, when = doc.edited || AC.sys.mtime(file);
          var card = ui.h('div', { class: 'card chapters-resume-card' });
          card.appendChild(ui.h('div', { class: 'chapters-resume-txt', html: '<b>' + U.esc(T('chapters.saved', { n: n })) + '</b><small>' + U.esc(sourceLabel(doc.source) + (when ? T('chapters.editedAt', { time: U.wibTime(when) }) : '')) + '</small>' }));
          card.appendChild(ui.button({ label: T('chapters.resume'), icon: 'chapters', small: true, onClick: function () {
            if (E.doc !== doc) loadDoc(doc, file);
            AC.router.go('tool/chapters/edit');
          } }));
          resumeBox.appendChild(card);
        }
        if (s.nMarkers) {
          AC.host.json('bac_chapters_read', s.id).then(function (r) {
            if (!r || !r.chapters || !r.chapters.length || resumeBox.querySelector('.chapters-has') || !resumeBox.isConnected) return;
            var box = ui.alert({ kind: 'info', icon: 'marker', title: T('chapters.hasMarkers', { n: r.chapters.length }),
              text: T('chapters.hasMarkersText'),
              actions: [{ label: T('chapters.editExisting'), icon: 'chapters', onClick: function () { openFromMarkers(r); } }] });
            box.classList.add('chapters-has');
            resumeBox.appendChild(box);
          }, function () { /* host not ready: no hint */ });
        }
      }

      function paintSetupDock() {
        ctx.dock({ primary: { label: ctx.def.cta, icon: aiOn() ? 'spark' : 'chapters', onClick: analyze } });
      }

      function analyze() {
        var params = { count: st.count, hint: (st.hint || '').trim(), style: st.style, min_len: st.minLen, ai: st.ai !== false };
        ctx.run({
          action: 'analyze', params: params, title: T('chapters.runTitle'),
          stages: [{ id: 'words', label: T('chapters.stageWords'), w: 0.3 }, { id: 'units', label: T('chapters.stageUnits'), w: 0.05 },
                   { id: 'find', label: aiOn() ? T('chapters.stageFindAi') : T('chapters.stageFindRule'), w: 0.55 }, { id: 'check', label: T('chapters.stageCheck'), w: 0.1 }],
          onResult: function (data) {
            var doc = AC.sys.readJSON(data.review, null);
            if (!doc) { ctx.error(U.err('BAD_REVIEW', T('chapters.badReview', { file: data.review }), T('chapters.badReviewHint'))); return; }
            loadDoc(doc, data.review);
            var msg = T('chapters.readyToEdit', { n: E.list.length });
            if (ctx.isActive()) { AC.router.go('tool/chapters/edit', { replace: true }); ui.toast(msg + (E.source === 'ai' ? '' : T('chapters.checkTitles')), 'chapters'); }
            else ui.toast(ctx.def.title + ': ' + msg, { icon: 'chapters', kind: 'ok', action: { label: T('common.view'), run: function () { AC.router.go('tool/chapters/edit'); } } });
          }
        });
      }

      function openFromMarkers(r) {
        var s = AC.seq.peek() || {};
        var items = r.chapters.map(function (c, k) {
          return { id: 'm' + Math.round(c.t * 100), t0: c.t, t1: k + 1 < r.chapters.length ? r.chapters[k + 1].t : r.duration, kind: 'chapter', on: true,
                   label: c.name, title: c.name, ctx: c.comment || '', generic: false, src: 'timeline' };
        });
        var doc = { v: 1, tool: 'chapters', timebase: 'sequence', duration: r.duration, created: new Date().toISOString(), seq: { id: r.id, name: r.name },
                    params: {}, stats: { n: items.length }, items: items, offset: 0, end: r.duration, source: 'timeline', warnings: [] };
        if (E.doc && E.seqId === r.id) {   // same sequence as the last analysis: keep its scope and the title/description
          doc.params = E.doc.params || {}; doc.offset = E.lo; doc.end = E.hi; doc.meta = E.meta || null;
          if (items.length) items[items.length - 1].t1 = Math.max(items[items.length - 1].t0, E.hi);
        }
        var file = reviewPath(r.name || s.name);
        try { AC.sys.mkdirp(AC.sys.dirname(file)); AC.sys.writeJSON(file, doc, true); } catch (e) { file = ''; }
        loadDoc(doc, file);
        AC.router.go('tool/chapters/edit');
      }

      /* ---------------- editor view */
      var head = ui.h('div', { class: 'chapters-head' });
      // the strip shows chapter titles (user / AI content)
      var strip = ui.h('div', { class: 'chapters-strip', role: 'group', 'aria-label': T('chapters.stripAria'), 'data-i18n-skip': '' });
      var notice = ui.h('div', { class: 'chapters-notice' });
      var listEl = ui.h('ol', { class: 'chapters-list', 'aria-label': T('chapters.listAria') });
      var tools = ui.h('div', { class: 'btn-row chapters-tools' });
      var secYt = ui.section({ title: T('chapters.ytTitle') });
      var ytPre = ui.h('pre', { class: 'chapters-yt', tabIndex: 0, 'aria-label': T('chapters.ytAria'), 'data-i18n-skip': '' });
      var ytRow = ui.h('div', { class: 'btn-row' });
      secYt.add(ytPre); secYt.add(ytRow);
      var secMeta = ui.section({ title: T('chapters.metaTitle') });
      var metaBody = ui.h('div', { class: 'chapters-meta' });
      secMeta.add(metaBody);
      [head, strip, notice, listEl, tools, secYt.el, secMeta.el].forEach(function (x) { editEl.appendChild(x); });

      tools.appendChild(ui.button({ label: T('chapters.addAtPlayhead'), icon: 'plus', small: true, onClick: addAtPlayhead }));
      var retitleBtn = ui.button({ label: T('chapters.retitle'), icon: 'spark', small: true, kind: 'ghost', title: T('chapters.retitleHelp'), onClick: retitle });
      tools.appendChild(retitleBtn);
      ytRow.appendChild(ui.button({ label: T('common.copy'), icon: 'copy', small: true, onClick: function () { ui.copy(youtubeText(E.list, E.lo), T('chapters.ytTitle')); } }));

      function paintHead() {
        var dur = E.hi - E.lo, src = sourceLabel(E.source);
        head.innerHTML = '<div class="chapters-title"><h2>' + U.esc(T('chapters.nChapters', { n: E.list.length })) + '</h2><small></small></div>';
        var small = head.querySelector('small');
        if (E.seqName) { small.appendChild(ui.h('span', { 'data-i18n-skip': '', text: E.seqName })); small.appendChild(document.createTextNode(', ')); }
        small.appendChild(document.createTextNode(U.mmss(dur) + (src ? ', ' + src : '')));
        var undo = ui.h('button', { class: 'ibtn', type: 'button', 'aria-label': T('chapters.undoAria'), title: T('chapters.undoTitle'), html: ui.icon('undo'), disabled: !E.undo.length,
                                    on: { click: undo1 } });
        head.appendChild(undo);
      }
      function paintStrip() {
        strip.innerHTML = '';
        var total = Math.max(0.001, E.hi - E.lo);
        E.list.forEach(function (c, k) {
          var end = k + 1 < E.list.length ? E.list[k + 1].t : E.hi;
          var b = ui.h('button', { type: 'button', class: 'chapters-seg' + (k === E.active ? ' is-on' : '') + (k % 2 ? ' is-alt' : ''),
                                   title: (k + 1) + '. ' + untitled(c.title) + ' (' + relLabel(c.t) + ')', 'aria-label': T('chapters.segAria', { k: k + 1, title: untitled(c.title) }) });
          b.style.flex = Math.max(0.001, (end - c.t) / total) + ' 1 0';
          b.addEventListener('click', function () { setActive(k, true); });
          strip.appendChild(b);
        });
      }
      function paintNotice() {
        notice.innerHTML = '';
        var iss = issues(E.list, E.lo, E.hi).filter(function (x) { return x.i === undefined; });
        var s = AC.seq.peek();
        if (s && E.seqId && s.id !== E.seqId) {
          notice.appendChild(ui.alert({ kind: 'info', icon: 'seq', title: T('chapters.otherSeq', { name: E.seqName || T('chapters.otherSeqName') }),
            text: T('chapters.otherSeqText', { active: s.name, name: E.seqName || T('chapters.origSeqName') }) }));
        } else if (s && E.doc && E.doc.duration && Math.abs(s.duration - E.doc.duration) > 0.5) {
          notice.appendChild(ui.alert({ kind: 'warn', icon: 'seq', title: T('chapters.seqChanged'), text: T('chapters.seqChangedText', { now: U.mmss(s.duration), was: U.mmss(E.doc.duration) }) }));
        }
        iss.forEach(function (x) {
          var actions = x.code === 'FIRST' ? [{ label: T('chapters.moveToZero'), onClick: function () { snapshot(); E.list[0].t = E.lo; E.list[0].touched = true; changed(true); } }] : null;
          notice.appendChild(ui.alert({ kind: 'warn', title: x.msg, actions: actions }));
        });
        var perRow = issues(E.list, E.lo, E.hi).filter(function (x) { return x.i !== undefined; }).length;
        if (perRow) notice.appendChild(ui.alert({ kind: 'warn', title: T('chapters.needCheck', { n: perRow }), text: T('chapters.needCheckText') }));
        if (E.list.some(function (c) { return c.generic; })) {   // engine warnings are all about titles still to check
          var w = (E.warnings || []).slice();
          notice.appendChild(ui.alert({ kind: 'info', icon: 'info', title: w.length ? w.shift() : T('chapters.noAiTitles'),
            text: (w.length ? w.join(' ') + ' ' : '') + T('chapters.genericText') }));
        }
      }
      function paintYt() {
        var txt = youtubeText(E.list, E.lo), iss = issues(E.list, E.lo, E.hi);
        ytPre.textContent = txt || T('chapters.none');
        secYt.setAside(!E.list.length ? '' : iss.length ? T('chapters.ytInvalid') : T('chapters.ytReady'));
        secYt.head.classList.toggle('is-ok', !iss.length);
      }
      function rowNotes(k) {
        var out = [], c = E.list[k], n = E.list.length, len = (k + 1 < n ? E.list[k + 1].t : E.hi) - c.t;
        if (len < YT_MIN - 1e-6) out.push({ kind: 'warn', text: T('chapters.rowShort', { s: U.dec(Math.max(0, len), 0) }) });
        if (!String(c.title || '').trim()) out.push({ kind: 'warn', text: T('chapters.rowEmpty') });
        else if (c.generic) out.push({ kind: 'info', text: T('chapters.rowGeneric') });
        return out;
      }
      function paintRowState(li, k) {
        var notes = rowNotes(k), box = li.querySelector('.chapters-notes'), c = E.list[k], n = E.list.length;
        var len = (k + 1 < n ? E.list[k + 1].t : E.hi) - c.t;
        li.classList.toggle('is-warn', notes.some(function (x) { return x.kind === 'warn'; }));
        li.classList.toggle('is-on', k === E.active);
        box.innerHTML = notes.map(function (x) { return '<span class="chapters-note' + (x.kind === 'warn' ? ' is-warn' : '') + '">' + ui.icon(x.kind === 'warn' ? 'alert' : 'info') + U.esc(x.text) + '</span>'; }).join('');
        li.querySelector('.chapters-len').textContent = U.dtk(Math.max(0, len));
      }
      function paintList() {
        listEl.innerHTML = '';
        if (!E.list.length) {
          listEl.appendChild(ui.h('li', { class: 'chapters-empty' }, [ui.empty({ icon: 'chapters', title: T('chapters.emptyTitle'), text: T('chapters.emptyText') })]));
          return;
        }
        E.list.forEach(function (c, k) { listEl.appendChild(row(c, k)); });
      }
      function row(c, k) {
        var li = ui.h('li', { class: 'chapters-row', dataset: { id: c.id } });
        var time = ui.h('input', { class: 'input input-mono chapters-time', type: 'text', value: relLabel(c.t), spellcheck: 'false', autocomplete: 'off',
                                   'aria-label': T('chapters.timeAria', { k: k + 1 }), title: k === 0 ? T('chapters.timeFirst') : T('chapters.timeHelp') });
        var name = ui.h('input', { class: 'input chapters-name', type: 'text', value: c.title, maxlength: 100, spellcheck: 'false', autocomplete: 'off',
                                   placeholder: T('chapters.namePh'), 'aria-label': T('chapters.nameAria', { k: k + 1 }) });
        var acts = ui.h('span', { class: 'chapters-acts' });
        function act(icon, label, fn, dis) {
          var b = ui.h('button', { class: 'ibtn', type: 'button', 'aria-label': T('chapters.actAria', { label: label, k: k + 1 }), title: label, html: ui.icon(icon), disabled: !!dis });
          b.addEventListener('click', function (e) { e.stopPropagation(); fn(); });
          acts.appendChild(b);
        }
        act('play', T('chapters.actSeek'), function () { setActive(k, true); });
        act('playhead', T('chapters.actPlayhead'), function () { toPlayhead(k); }, k === 0);
        act('join', T('chapters.actMerge'), function () { merge(k); }, k === 0);
        act('trash', T('chapters.actDelete'), function () { remove(k); });
        li.appendChild(ui.h('span', { class: 'chapters-n', text: String(k + 1) }));
        li.appendChild(time); li.appendChild(name);
        // .chapters-ctx = what is said at the chapter start (transcript content)
        li.appendChild(ui.h('div', { class: 'chapters-sub' }, [ui.h('span', { class: 'chapters-info' }, [ui.h('span', { class: 'chapters-len' }), ui.h('span', { class: 'chapters-ctx', 'data-i18n-skip': '', text: c.ctx || '' })]), acts]));
        li.appendChild(ui.h('div', { class: 'chapters-notes' }));
        paintRowState(li, k);

        var before = c.title;
        name.addEventListener('focus', function () { before = c.title; setActive(k, false); });
        name.addEventListener('input', function () { c.title = name.value; c.touched = true; c.generic = false; derived(); paintRowState(li, k); saveSoon(); });
        // A focused row removed without a blur first (delete/merge/undo repaint) still fires "change": the row is
        // gone from E.list then, and pushing that list would make the next undo a no-op (seen live).
        name.addEventListener('change', function () { if (E.list.indexOf(c) < 0) return; if (name.value !== before) { E.undo.push(JSON.stringify(E.list.map(function (x) { return x.id === c.id ? U.assign({}, x, { title: before }) : x; }))); paintHead(); } before = c.title; });
        time.addEventListener('focus', function () { setActive(k, false); time.select(); });
        time.addEventListener('keydown', function (e) {
          if (e.key === 'Enter') { e.preventDefault(); commitTime(k, time); }
          else if (e.key === 'Escape') { time.value = relLabel(c.t); time.blur(); e.stopPropagation(); }
        });
        time.addEventListener('change', function () { commitTime(k, time); });
        li.addEventListener('click', function (e) { if (e.target === li || e.target.classList.contains('chapters-ctx') || e.target.classList.contains('chapters-n')) setActive(k, true); });
        return li;
      }

      // Repaint everything that depends on the list but keeps the row inputs (typing keeps focus).
      function derived() { paintHead(); paintStrip(); paintNotice(); paintYt(); paintEditDock(); }
      function changed(structural) {
        if (structural) paintList(); else Array.prototype.forEach.call(listEl.children, function (li, k) { if (E.list[k]) paintRowState(li, k); });
        derived(); saveSoon();
      }
      function setActive(k, seek) {
        if (k < 0 || k >= E.list.length) return;
        E.active = k;
        Array.prototype.forEach.call(listEl.children, function (li, i) { li.classList.toggle('is-on', i === k); });
        Array.prototype.forEach.call(strip.children, function (b, i) { b.classList.toggle('is-on', i === k); });
        if (seek) {
          var t = E.list[k].t;
          AC.host.json('bac_seek', t, E.seqId || '').then(function () { ui.toast(T('chapters.seekTo', { t: U.tcode(t - E.lo) }), 'playhead'); },
            function (e) { ui.toast(T('chapters.seekFail', { msg: U.errMsg(e) }), { kind: 'err' }); });
          var li = listEl.children[k]; if (li && li.scrollIntoView) li.scrollIntoView({ block: 'nearest' });
        }
      }
      function commitTime(k, input) {
        var c = E.list[k]; if (!c) return;
        var v = parseTime(input.value);
        if (isNaN(v)) { ui.toast(T('chapters.badTime'), { kind: 'err' }); input.value = relLabel(c.t); return; }
        var t = E.lo + v;
        if (t >= E.hi) { ui.toast(T('chapters.pastEnd', { t: relLabel(E.hi) }), { kind: 'err' }); input.value = relLabel(c.t); return; }
        if (Math.abs(Math.floor(t) - Math.floor(c.t)) < 1 && Math.abs(t - c.t) < 1) { input.value = relLabel(c.t); return; }   // same second: keep exact time
        if (E.list.some(function (x, i) { return i !== k && Math.abs(x.t - t) < 1; })) { ui.toast(T('chapters.exists', { t: relLabel(t) }), { kind: 'err' }); input.value = relLabel(c.t); return; }
        snapshot();
        c.t = round3(t); c.touched = true;
        sortKeep(c.id);
        changed(true);
        focusRow(c.id, '.chapters-time');
      }
      function sortKeep(id) {
        E.list.sort(function (a, b) { return a.t - b.t; });
        for (var i = 0; i < E.list.length; i++) if (E.list[i].id === id) E.active = i;
      }
      function focusRow(id, sel) {
        var li = listEl.querySelector('[data-id="' + String(id).replace(/"/g, '') + '"]');
        var inp = li && li.querySelector(sel);
        if (inp) { inp.focus(); if (sel === '.chapters-name' && inp.setSelectionRange) inp.setSelectionRange(inp.value.length, inp.value.length); }
      }
      function undoToast(msg) { ui.toast(msg, { icon: 'undo', action: { label: T('chapters.undo'), run: undo1 } }); }
      function merge(k) {
        if (k <= 0) return;
        snapshot();
        var a = E.list[k - 1], b = E.list[k];
        var lenA = b.t - a.t, lenB = (k + 1 < E.list.length ? E.list[k + 1].t : E.hi) - b.t;
        // Keep the longer part's title, but a real (AI/user) title always beats a flagged keyword title or an empty one.
        var hasA = !!String(a.title || '').trim(), hasB = !!String(b.title || '').trim();
        var takeB = hasB && (!hasA || (a.generic && !b.generic) || (!!a.generic === !!b.generic && lenB > lenA));
        if (takeB) { a.title = b.title; a.generic = b.generic; }
        a.touched = true;
        E.list.splice(k, 1);
        E.active = k - 1;
        changed(true);
        undoToast(T('chapters.merged', { a: k + 1, b: k }));
      }
      function remove(k) {
        snapshot();
        var c = E.list.splice(k, 1)[0];
        if (k === 0 && E.list.length) { E.list[0].t = E.lo; E.list[0].touched = true; }   // the next chapter becomes the first (00:00)
        E.active = Math.min(k, E.list.length - 1);
        changed(true);
        undoToast(T('chapters.deleted', { title: untitled(c.title) }));
      }
      function undo1() {
        if (!E.undo.length) { ui.toast(T('chapters.nothingToUndo'), 'undo'); return; }
        E.list = JSON.parse(E.undo.pop());
        E.active = Math.min(Math.max(0, E.active), E.list.length - 1);
        changed(true);
        ui.toast(T('chapters.undone'), 'undo');
      }
      function playhead() {
        return AC.host.json('bac_seqInfo', 'lite', E.seqId || '').then(function (s) {
          if (!s) throw U.err('NO_SEQ', T('chapters.seqNotOpen'));
          return Number(s.player) || 0;
        });
      }
      function addAtPlayhead() {
        playhead().then(function (t) {
          if (t < E.lo || t >= E.hi - 1) { ui.toast(T('chapters.outsideAt', { t: U.tcode(t) }), { kind: 'err' }); return; }
          if (E.list.some(function (x) { return Math.abs(x.t - t) < 1; })) { ui.toast(T('chapters.exists', { t: relLabel(t) }), { kind: 'err' }); return; }
          snapshot();
          var c = { id: newId(), t: round3(t), title: '', ctx: '', generic: false, src: 'user', keywords: [], touched: true };
          E.list.push(c);
          sortKeep(c.id);
          changed(true);
          focusRow(c.id, '.chapters-name');
          ui.toast(T('chapters.added', { t: relLabel(t) }), 'plus');
        }, function (e) { ui.toast(T('chapters.playheadFail', { msg: U.errMsg(e) }), { kind: 'err' }); });
      }
      function toPlayhead(k) {
        playhead().then(function (t) {
          var c = E.list[k];
          if (!c) return;
          if (t < E.lo || t >= E.hi) { ui.toast(T('chapters.outside'), { kind: 'err' }); return; }
          if (E.list.some(function (x, i) { return i !== k && Math.abs(x.t - t) < 1; })) { ui.toast(T('chapters.exists', { t: relLabel(t) }), { kind: 'err' }); return; }
          snapshot();
          c.t = round3(t); c.touched = true;
          sortKeep(c.id);
          changed(true);
          ui.toast(T('chapters.moved', { t: relLabel(t) }), 'playhead');
        }, function (e) { ui.toast(T('chapters.playheadFail', { msg: U.errMsg(e) }), { kind: 'err' }); });
      }

      function saveNow() {
        saveSoon.cancel();
        if (!E.doc || !E.file) return true;
        try { E.doc = docFromState(); AC.sys.writeJSON(E.file, E.doc, true); return true; }
        catch (e) { AC.log.error(e, 'chapters save'); ui.toast(T('chapters.saveFail', { msg: U.errMsg(e) }), { kind: 'err' }); return false; }
      }
      function reviewPath(seqName) { return AC.sys.node ? AC.sys.join(AC.settings.workdir(seqName || NONAME), 'chapters_review.json') : ''; }
      // Full Timeline JSON of the analysed sequence (the active one when it is the same; else by id).
      function seqJson() {
        var s = AC.seq.peek();
        if (!E.seqId || (s && s.id === E.seqId)) return AC.seq.current();
        return AC.host.exec('bac_seqInfo', ['full', E.seqId], { json: true, timeout: 60000 });
      }
      function scopeParam() { return (E.doc && E.doc.params && E.doc.params.scope) || { kind: 'all' }; }
      function liveList() { return E.list.map(function (c) { return { t: c.t, title: c.title }; }); }

      /* ---------------- AI helpers in the editor (inline, the editor stays on screen) */
      var aiJob = null;
      function inlineJob(action, params, title, box, done) {
        if (aiJob && aiJob.isActive()) { ui.toast(T('chapters.aiBusy'), 'clock'); return; }
        var status = ui.h('div', { class: 'chapters-busy', role: 'status', html: '<span class="chapters-spin"></span><span>' + U.esc(title) + '...</span>' });
        box.appendChild(status);
        seqJson().then(function (seq) {
          if (!seq) throw U.err('NO_SEQ', T('chapters.seqNotOpen'));
          aiJob = AC.engine.run({ tool: 'chapters', action: action, seq: seq, params: params, title: title, label: title, workdir: AC.sys.node ? AC.sys.dirname(E.file || reviewPath(E.seqName)) : undefined });
          return aiJob.promise;
        }).then(function (data) {
          if (status.parentNode) status.parentNode.removeChild(status);
          done(data);
        }, function (e) {
          if (status.parentNode) status.parentNode.removeChild(status);
          box.appendChild(ui.alert({ kind: e && e.code === 'NO_AI' ? 'warn' : 'err', title: e && e.code === 'NO_AI' ? T('chapters.aiDownTitle') : T('chapters.jobFailed', { title: title }), text: U.errMsg(e) + (e && e.hint && String(e.hint).length < 160 ? ' ' + e.hint : '') }));
        });
      }
      var retitleBox = ui.h('div', { class: 'chapters-inline' });
      tools.parentNode.insertBefore(retitleBox, tools.nextSibling);
      function retitle() {
        if (!E.list.length) return;
        retitleBox.innerHTML = '';
        inlineJob('retitle', { chapters: liveList(), offset: E.lo, style: st.style, hint: (st.hint || '').trim(), ai: st.ai !== false, scope: scopeParam() }, T('chapters.retitleRun'), retitleBox, function (data) {
          var titles = data.titles || [];
          if (titles.length !== E.list.length) { ui.toast(T('chapters.retitleChanged'), { kind: 'err' }); return; }
          snapshot();
          E.list.forEach(function (c, k) { if (titles[k]) { c.title = titles[k]; c.generic = false; c.touched = true; } });
          changed(true);
          undoToast(T('chapters.retitled', { n: titles.length }));
        });
      }

      function paintMeta() {
        metaBody.innerHTML = '';
        var m = E.meta;
        var btn = ui.button({ label: m ? T('chapters.metaRedo') : T('chapters.metaMake'), icon: 'spark', small: true, kind: m ? 'ghost' : '', onClick: genMeta });
        if (!m) {
          metaBody.appendChild(ui.h('p', { class: 'help', text: T('chapters.metaHelp') }));
          metaBody.appendChild(ui.h('div', { class: 'btn-row' }, [btn]));
          return;
        }
        if (!m.ai) {   // say why: switched off, proxy down, or the AI answered but its output was unusable
          var off = st.ai === false || AC.settings.get('ai') === false, why = String(m.why || '');
          var failed = !off && why && !/offline|unavailable|circuit|disabled|dimatikan|turned off|switched off/i.test(why);   // i18n-ignore
          metaBody.appendChild(ui.alert({ kind: 'warn', title: off ? T('chapters.metaAiOff') : failed ? T('chapters.metaAiFailed') : T('chapters.aiDownTitle'),
            text: T('chapters.metaNoAiText') + (failed ? ' ' + T('chapters.metaTryRedo') : ''),
            log: failed ? why : undefined }));
        }
        if ((m.titles || []).length) {
          var tl = ui.h('div', { class: 'chapters-titles', 'data-i18n-skip': '' });
          m.titles.forEach(function (t, i) {
            var r = ui.h('div', { class: 'chapters-opt' }, [ui.h('span', { class: 'chapters-opt-n', text: String(i + 1) }), ui.h('span', { class: 'chapters-opt-t', text: t })]);
            r.appendChild(ui.h('button', { class: 'ibtn', type: 'button', title: T('chapters.copyTitle'), 'aria-label': T('chapters.copyTitleN', { i: i + 1 }), html: ui.icon('copy'), on: { click: function () { ui.copy(t, T('chapters.titleWord')); } } }));
            tl.appendChild(r);
          });
          metaBody.appendChild(ui.field({ label: T('chapters.videoTitle'), control: tl }));
        }
        var desc = ui.h('textarea', { class: 'input chapters-desc', rows: 7, spellcheck: 'false', 'aria-label': T('chapters.descAria') });
        desc.value = m.description || '';
        desc.addEventListener('input', function () { m.description = desc.value; saveSoon(); });
        metaBody.appendChild(ui.field({ label: T('chapters.descLabel'), control: desc }));
        if ((m.hashtags || []).length) metaBody.appendChild(ui.field({ label: T('chapters.hashtags'), control: ui.h('div', { class: 'chapters-tagbox', 'data-i18n-skip': '', text: m.hashtags.join(' ') }) }));
        if ((m.tags || []).length) metaBody.appendChild(ui.field({ label: T('chapters.tags'), control: ui.h('div', { class: 'chapters-tagbox', 'data-i18n-skip': '', text: m.tags.join(', ') }) }));
        var row2 = ui.h('div', { class: 'btn-row' });
        row2.appendChild(ui.button({ label: T('chapters.copyDesc'), icon: 'copy', small: true, onClick: function () { ui.copy(m.description || '', T('chapters.descLabel')); } }));
        if ((m.hashtags || []).length) row2.appendChild(ui.button({ label: T('chapters.copyHashtags'), icon: 'copy', small: true, kind: 'ghost', onClick: function () { ui.copy(m.hashtags.join(' '), T('chapters.hashtags')); } }));
        if ((m.tags || []).length) row2.appendChild(ui.button({ label: T('chapters.copyTags'), icon: 'copy', small: true, kind: 'ghost', onClick: function () { ui.copy(m.tags.join(', '), T('chapters.tags')); } }));
        row2.appendChild(btn);
        metaBody.appendChild(row2);
      }
      function genMeta() {
        if (!E.list.length) { ui.toast(T('chapters.makeFirst'), { kind: 'err' }); return; }
        saveNow();
        Array.prototype.slice.call(metaBody.querySelectorAll('.alert')).forEach(function (a) { a.parentNode.removeChild(a); });
        inlineJob('meta', { chapters: liveList(), offset: E.lo, scope: scopeParam(), ai: st.ai !== false }, T('chapters.metaRun'), metaBody, function (data) {
          E.meta = { titles: data.titles || [], description: data.description || '', hashtags: data.hashtags || [], tags: data.tags || [], ai: !!data.ai,
                     why: data.ai ? '' : String((data.warnings || [])[0] || ''), at: Date.now() };
          paintMeta(); saveNow();
          ui.toast(data.ai ? T('chapters.metaReady') : T('chapters.metaNoAi'), data.ai ? 'spark' : 'info');
        });
      }

      /* ---------------- apply */
      function paintEditDock() {
        var n = E.list.length;
        ctx.dock({ left: [{ label: T('common.reset'), kind: 'ghost', onClick: function () { saveNow(); AC.router.go('tool/chapters'); } }],
                   primary: { label: n ? T('chapters.addN', { n: n }) : T('chapters.emptyTitle'), icon: 'marker', disabled: !n, title: T('chapters.addHelp'), onClick: applyAll },
                   right: [{ iconOnly: true, icon: 'copy', label: T('chapters.copyYt'), onClick: function () { ui.copy(youtubeText(E.list, E.lo), T('chapters.ytTitle')); } }] });
      }
      function applyAll() {
        if (!E.list.length) return;
        if (!saveNow() || !E.file) { ui.toast(T('chapters.notSaved'), { kind: 'err' }); return; }
        seqJson().then(function (seq) {
          ctx.run({ action: 'apply', review: E.file, seq: seq || null, params: { scope: scopeParam() }, title: T('chapters.applyRun'), workdir: AC.sys.dirname(E.file),
            stages: [{ id: 'check', label: T('chapters.stageCheckList'), w: 0.5 }, { id: 'write', label: T('chapters.stageWrite'), w: 0.5 }],
            onResult: function (data) {
              var seqId = (data.seq && data.seq.id) || E.seqId || '';
              AC.host.exec('bac_chapters_apply', [data.plan.markers, seqId], { json: true, timeout: 60000 }).then(function (r) {
                AC.seq.refresh('event');
                var copied = st.copy !== false && U.copyText(data.youtube || '');
                showResult(data, r, copied);
              }, function (e) { ctx.error(e, { onRetry: applyAll, title: T('chapters.applyFail') }); });
            } });
        }, function (e) { ctx.error(e, { onRetry: applyAll }); });
      }
      function showResult(data, r, copied) {
        var iss = data.issues || [], ok = !iss.length, n = r.added || 0;
        var avg = n ? (E.hi - E.lo) / n : 0;
        var notes = (data.notes || []).concat(r.n !== n ? [T('chapters.countMismatch', { n: r.n, m: n })] : []);
        ctx.result({
          title: T('chapters.resultTitle', { n: n }),
          sub: T('chapters.resultSub', { name: r.name }) + (r.removed ? ' ' + T('chapters.resultReplaced', { n: r.removed }) : '') + (copied ? ' ' + T('chapters.resultCopied') : '') + ' ' + T('chapters.resultMedia'),
          statsList: [{ label: T('chapters.statChapters'), value: String(n), accent: true }, { label: T('chapters.statAvg'), value: U.dtk(avg) }, { label: 'YouTube', value: ok ? T('common.ready') : T('chapters.notValid') }],
          warn: !ok ? { title: T('chapters.ytRulesFail'), text: iss.map(function (x) { return x.msg; }).join(' ') } : notes.length ? { title: T('chapters.notes'), text: notes.join(' ') } : null,
          extra: ui.h('pre', { class: 'chapters-yt', 'data-i18n-skip': '', text: data.youtube || '' }),
          actions: [{ label: T('chapters.copyYt'), icon: 'copy', kind: '', onClick: function () { ui.copy(data.youtube || '', T('chapters.ytTitle')); } },
                    { label: T('chapters.editChapters'), icon: 'chapters', onClick: function () { AC.router.go('tool/chapters/edit'); } },
                    { label: T('chapters.openFolder'), icon: 'folder', onClick: function () { AC.sys.openFolder(AC.sys.dirname(data.txt)); } }],
          onDelete: function () {
            // Back to the editor: the result card would still say "N chapters added" (the list itself is kept).
            AC.host.json('bac_chapters_clear', r.id).then(function (x) { AC.seq.refresh('event'); ui.toast(T('chapters.cleared', { n: x.removed, name: x.name }), 'trash'); if (ctx.isActive()) AC.router.go('tool/chapters/edit', { replace: true }); },
              function (e) { ui.toast(T('chapters.clearFail', { msg: U.errMsg(e) }), { kind: 'err' }); });
          }
        });
      }

      /* ---------------- views */
      function showSetup() {
        view = 'setup';
        setupEl.hidden = false; editEl.hidden = true;
        if (ctx.source) ctx.source.el.hidden = false;
        paintAi(); paintEstimate(); paintResume(); paintSetupDock();
      }
      function showEdit() {
        if (!E.doc) {   // deep link / panel reload: reopen the saved review of the current sequence
          var s = AC.seq.peek(), file = s && reviewPath(s.name), d = file && AC.sys.readJSON(file, null);
          if (d && d.tool === 'chapters' && d.seq && d.seq.id === s.id) loadDoc(d, file);
          else { AC.router.go('tool/chapters', { replace: true }); return; }
        }
        view = 'edit';
        setupEl.hidden = true; editEl.hidden = false;
        if (ctx.source) ctx.source.el.hidden = true;
        paintList(); derived(); paintMeta();
        retitleBtn.hidden = !aiOn();
      }
      ctx._chapters = { showSetup: showSetup, showEdit: showEdit, undo: undo1, isEdit: function () { return view === 'edit'; }, save: saveNow, paintNotice: paintNotice, paintSetup: function () { paintAi(); paintEstimate(); paintResume(); } };
      AC.bus.on('health', function () { if (view === 'setup') paintAi(); });
    },

    onShow: function (ctx) {
      var c = ctx._chapters; if (!c) return;
      if (ctx.sub === 'edit') c.showEdit(); else c.showSetup();
    },
    onHide: function (ctx) { if (ctx._chapters) ctx._chapters.save(); },
    onSeq: function (lite, ctx) {
      var c = ctx._chapters; if (!c || !ctx.isActive()) return;
      if (c.isEdit()) c.paintNotice(); else c.paintSetup();
    },
    onScope: function (scope, ctx) { if (ctx._chapters && !ctx._chapters.isEdit()) ctx._chapters.paintSetup(); },
    onKey: function (e, ctx) {
      var c = ctx._chapters;
      if (!c || !c.isEdit() || AC.keys.typing()) return false;
      if ((e.ctrlKey || e.metaKey) && !e.shiftKey && (e.key === 'z' || e.key === 'Z')) { c.undo(); return true; }
      return false;
    }
  });

  // label/detail are getters: the palette reads them when it opens, so they follow the current language
  AC.palette.add({ g: 'Aksi', ic: 'chapters', get l() { return AC.t('chapters.palLabel'); }, get d() { return AC.t('chapters.title'); }, run: function () { AC.router.go('tool/chapters'); } });
})();
