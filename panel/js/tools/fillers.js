/* fillers.js: Hapus Filler. Finds hesitations (eh, emm, eee, hmm, ...) from the transcript, the second "listener"
   pass and the sound itself (engine/ac/tools/fillers.py), plus opt-in habit words (ya, oke, gitu, ...) at clean phrase
   boundaries. Flow: settings -> engine analyze (review file) -> Tinjau (source tags, confidence, "Mepet kata", context,
   click = seek) -> engine apply (remove_ranges plan) -> AC.apply.removeRanges on a clone. No AI per word: the research
   (docs/research/fillers_repeat.md section 7) found Grok inconsistent for this, so every decision is rule based.
   Host helper: host/31_fillers.jsx (bac_fillers_markers). Docs: docs/tools/fillers.md. Texts: locale keys fillers.*
   (AC.t at render time). The filler WORDS (eh, anu, ya, ...) are transcript content, not UI text. */
(function () {
  'use strict';
  var AC = window.AC, U = AC.util, ui = AC.ui;

  function L(k, v) { return AC.t(k, v); }
  var ALWAYS = ['eh', 'ehm', 'em', 'emm', 'eee', 'hmm', 'uh', 'um'];
  // Same keys as engine/ac/tools/_filler_lexicon.py HABITS.
  var HABITS = ['ya', 'oke', 'guys', 'gitu', 'kan', 'sih', 'deh', 'nah', 'nih', 'tuh', 'dong', 'lho', 'baik',
                'apa namanya', 'apa ya', 'apa sih', 'gimana ya', 'istilahnya', 'pokoknya'];
  // Labels + hints: fillers.preset.<value> / fillers.preset.<value>Hint.
  var PRESET_VALUES = [{ value: 'hati', values: { threshold: 0.8 } }, { value: 'seimbang', values: { threshold: 0.65 } }, { value: 'bersih', values: { threshold: 0.5 } }];
  function presets() {
    return PRESET_VALUES.map(function (p) { return { value: p.value, label: L('fillers.preset.' + p.value), hint: L('fillers.preset.' + p.value + 'Hint'), values: p.values }; });
  }
  var SRCS = { transcript: 1, listener: 1, acoustic: 1, silence: 1 };   // source tag fillers.src.<id>, tooltip fillers.srcTip.<id>
  function srcName(s) { return SRCS[s] ? L('fillers.src.' + s) : s; }
  function srcTip(s) { return SRCS[s] ? L('fillers.srcTip.' + s) : s; }
  var MARK_COLOR = { filler: 3, sound: 2, habit: 4, gap: 6 };          // Premiere marker colours: orange, purple, yellow, blue
  var MARK_TAG = '[Klipora-FILLER]';                                   // old markers: [AC-FILLER] (host acHasTag clears both)
  var LEAD = 0.5;                                                      // seek this much before the cut: Space plays into it
  // Silence tool settings passed through for "Sekalian potong jeda" (engine keys; camelCase state keys mapped).
  var SIL_KEYS = { preset: 'preset', offset: 'offset', threshold: 'threshold', min_silence: 'min_silence', minSilence: 'min_silence',
                   pad_before: 'pad_before', padBefore: 'pad_before', pad_after: 'pad_after', padAfter: 'pad_after',
                   min_talk: 'min_talk', minTalk: 'min_talk', guard: 'guard' };

  function silenceSettings() {
    var s = AC.store.get('tool.silence', null), out = {};
    if (!s || typeof s !== 'object') return out;
    Object.keys(SIL_KEYS).forEach(function (k) { if (s[k] !== undefined && s[k] !== null) out[SIL_KEYS[k]] = s[k]; });
    return out;
  }

  function confTag(c) {
    if (typeof c !== 'number') return '';
    var p = Math.round(c * 100), kind = c >= 0.8 ? 'ok' : c >= 0.5 ? 'line' : 'warn';
    return '<span class="tag tag-' + kind + ' conf" title="' + U.esc(L('fillers.conf', { pct: p })) + '">' + p + '%</span>';
  }
  // Row tags: main source (+n when several detectors agree; the tooltip lists them), "Mepet" badge, confidence.
  function rowTags(it) {
    var srcs = it.srcs && it.srcs.length ? it.srcs : (it.src ? [it.src] : []), h = '';
    if (srcs.length) {
      var tip = srcs.map(srcTip).join(' + ');
      h += '<span class="tag tag-line fillers-src" title="' + U.esc(tip) + '">' + U.esc(srcName(srcs[0])) + (srcs.length > 1 ? ' +' + (srcs.length - 1) : '') + '</span>';
    }
    if (it.tight) h += '<span class="tag tag-warn fillers-tight" title="' + U.esc(L('fillers.tightTip')) + '">' + ui.icon('alert') + U.esc(L('fillers.tight')) + '</span>';
    return h + confTag(it.conf);
  }
  function counts(items) {
    var c = { f: 0, g: 0, fOn: 0, gOn: 0 };
    items.forEach(function (it) {
      var gap = it.kind === 'gap';
      c[gap ? 'g' : 'f']++;
      if (it.on) c[gap ? 'gOn' : 'fOn']++;
    });
    return c;
  }
  function applyLabel(items) {
    var c = counts(items);
    if (c.fOn && c.gOn) return L('fillers.applyBoth', { fillers: L('fillers.nFiller', { n: c.fOn }), gaps: L('fillers.nGap', { n: c.gOn }) });
    if (c.gOn) return L('fillers.applyGaps', { n: c.gOn });
    return L('fillers.apply', { n: c.fOn });
  }
  // Review row context: transcript words are user content (data-i18n-skip).
  function rowCtx(it) {
    var pre = it.ctx && it.ctx.pre, post = it.ctx && it.ctx.post, sk = function (x) { return '<span data-i18n-skip>' + U.esc(x) + '</span>'; };
    return (pre ? sk(pre) : '<i>' + U.esc(AC.t('review.start')) + '</i>') + ' <mark data-i18n-skip>' + U.esc(it.label || '') + '</mark> ' + (post ? sk(post) : '<i>' + U.esc(AC.t('review.end')) + '</i>');
  }

  AC.tools.register({
    id: 'fillers', group: 'potong', order: 2, icon: 'filler',
    badges: ['gpu', 'baru'],
    text: function (t) {
      return { title: t('fillers.title'), tab: t('fillers.tab'), desc: t('fillers.desc'), lead: t('fillers.lead'), cta: t('fillers.cta'),
               review: { unit: t('fillers.unit') },
               next: [{ tool: 'repeat', reason: t('fillers.nextRepeat') }, { tool: 'captions', reason: t('fillers.nextCaptions') }] };
    },
    defaults: { always: true, anu: true, habits: [], acoustic: true, preset: 'seimbang', threshold: 0.65, withSilence: false, counts: null },

    render: function (el, ctx) {
      var st = ctx.state;
      if (!Array.isArray(st.habits)) st.habits = [];
      st.habits = st.habits.filter(function (h) { return HABITS.indexOf(h) >= 0; });
      if (typeof st.threshold !== 'number' || isNaN(st.threshold)) st.threshold = 0.65;

      el.appendChild(ui.h('div', { class: 'tool-hero', html: '<span class="tool-ic">' + ui.icon('filler') + '</span><div><p>' + U.esc(ctx.def.lead) + '</p><div class="tags">' + ctx.def.badges.map(ui.badge).join('') + '</div></div>' }));

      /* ---- Kata yang dicari */
      var words = ui.section({ title: L('fillers.secWords') });
      var swAlways = ui.switch({ label: L('fillers.always'), help: L('fillers.alwaysHelp', { words: ALWAYS.join(', ') }), checked: st.always !== false,
        onChange: function (v) { st.always = v; changed(); } });
      var swAnu = ui.switch({ label: L('fillers.anu'), help: L('fillers.anuHelp'), checked: st.anu !== false,
        onChange: function (v) { st.anu = v; changed(); } });
      var habits = ui.chips({ options: habitOptions(), value: st.habits, multi: true, ariaLabel: L('fillers.habits'),
        onChange: function (v) { st.habits = v; changed(); } });
      habits.el.setAttribute('data-i18n-skip', '');                   // the habit words are speech, not UI text
      var habitNote = ui.h('small', { class: 'help fillers-counts' });
      var habitField = ui.h('div', { class: 'field fillers-habits' }, [
        ui.h('div', { class: 'field-top' }, [ui.h('span', { class: 'lbl', text: L('fillers.habits') }), ui.h('button', { class: 'linkbtn', type: 'button', text: L('fillers.habitsOff'), on: { click: function () { habits.set([], false); } } })]),
        habits.el,
        ui.h('small', { class: 'help', text: L('fillers.habitsHelp') }),
        habitNote]);
      words.add([swAlways, swAnu, habitField]);
      el.appendChild(words.el);

      /* ---- Cara deteksi */
      var det = ui.section({ title: L('fillers.secDetect') });
      var swAc = ui.switch({ label: L('fillers.acoustic'), help: L('fillers.acousticHelp'), checked: st.acoustic !== false,
        onChange: function (v) { st.acoustic = v; changed(); } });
      var kep = ui.presets({ options: presets(), value: st.preset, ariaLabel: L('fillers.sensitivity'), customHint: L('fillers.customHint'),
        onChange: function (v, p) { st.preset = v; st.threshold = p.values.threshold; thr.set(st.threshold, true); adv.setAside(''); ctx.save(); } });
      det.add([swAc, ui.h('div', { class: 'field' }, [ui.h('span', { class: 'lbl', text: L('fillers.sensitivity') }), kep.el])]);
      el.appendChild(det.el);

      var adv = ui.advanced({ title: L('common.advanced'), key: 'fillers' });
      var thr = ui.slider({ label: L('fillers.threshold'), min: 0.3, max: 0.95, step: 0.05, value: st.threshold,
        format: function (v) { return Math.round(v * 100) + '%'; },
        help: L('fillers.thresholdHelp'),
        onInput: function (v) { st.threshold = v; st.preset = kep.sync({ threshold: v }); adv.setAside(st.preset ? '' : L('common.custom')); ctx.save(); } });
      adv.add(thr);
      st.preset = kep.sync({ threshold: st.threshold });
      adv.setAside(st.preset ? '' : L('common.custom'));
      el.appendChild(adv.el);

      /* ---- Hasil */
      var hasil = ui.section({ title: L('fillers.secResult'), help: L('fillers.resultHelp', { name: AC.brand.suffix.trim() }) });
      hasil.add(ui.switch({ label: L('fillers.withSilence'), help: L('fillers.withSilenceHelp'), checked: !!st.withSilence,
        onChange: function (v) { st.withSilence = v; changed(); } }));
      el.appendChild(hasil.el);

      ctx.dock({ primary: { label: ctx.def.cta, icon: 'filler', onClick: run } });
      paint();

      /* ---- helpers */
      function knownCounts() {
        var c = st.counts, lite = AC.seq.peek();
        if (!c || !c.n) return null;
        return (!lite || !c.seq || lite.id === c.seq) ? c.n : null;
      }
      function habitOptions() {
        var n = knownCounts();
        return HABITS.map(function (h) { return { value: h, label: h, count: n ? (n[h] || 0) : undefined, title: n ? L('fillers.habitCount', { n: n[h] || 0 }) : undefined }; });
      }
      function enabledCount() { return (st.always !== false ? ALWAYS.length : 0) + (st.anu !== false ? 1 : 0) + st.habits.length; }
      function paint() {
        words.setAside(L('fillers.activeWords', { n: enabledCount() }));
        habitNote.textContent = knownCounts() ? L('fillers.habitNote') : '';
        var any = st.always !== false || st.anu !== false || st.acoustic !== false || st.habits.length > 0 || st.withSilence;
        ctx.setPrimary({ disabled: !any, title: any ? '' : L('fillers.needOne') });
      }
      function changed() { ctx.save(); paint(); }
      ctx.refreshCounts = function () { habits.setOptions(habitOptions()); paint(); };

      function params() {
        var p = { always: st.always !== false, anu: st.anu !== false, habits: st.habits.slice(), acoustic: st.acoustic !== false,
                  threshold: Math.round(st.threshold * 100) / 100, with_silence: !!st.withSilence };
        if (p.with_silence) p.silence = silenceSettings();
        return p;
      }
      function run() {
        var p = params(), stages = [{ id: 'words', label: L('fillers.stWords'), w: 0.4 }, { id: 'listen', label: L('fillers.stListen'), w: 0.2 }, { id: 'sound', label: L('fillers.stSound'), w: 0.25 }];
        if (p.with_silence) stages.push({ id: 'gaps', label: L('fillers.stGaps'), w: 0.1 });
        stages.push({ id: 'review', label: L('fillers.stReview'), w: 0.05 });
        ctx.run({ action: 'analyze', title: L('fillers.jobAnalyze'), params: p, stages: stages, onResult: function (data) {
          var lite = AC.seq.peek();
          st.counts = { seq: lite && lite.id || null, n: data.habit_counts || {} };
          ctx.save(); ctx.refreshCounts();
          if (data.silence && data.silence.ok === false) ui.toast(data.silence.msg || L('fillers.gapsSkipped'), { icon: 'alert' });
          if (!(data.summary && data.summary.n)) {         // an empty Tinjau said "Tidak ada yang cocok dengan saringan ini"
            var whole = !p.scope || p.scope.kind === 'all';   // ctx.run added the source card scope
            var tip = !p.acoustic ? L('fillers.tipAcoustic') : !p.habits.length ? L('fillers.tipHabits') : '';
            ctx.result({ title: L('fillers.noneTitle'), sub: L(whole ? 'fillers.noneWhole' : 'fillers.nonePart') + (tip ? ' ' + tip : ''),
                         primary: { label: L('fillers.adjust'), kbd: false, onClick: function () { ctx.back(); } } });
            return;
          }
          showReview(data.review);
        } });
      }

      // Sequence the open review belongs to. After a cut the CLONE is active, so "Ubah pilihan" + apply, markers
      // and seeks must target this id, not the active sequence (live: a re-apply cut the clone a second time).
      var revSeq = '';

      function showReview(file) {
        var doc;
        try { doc = ui.loadReview(file); } catch (e) { ctx.error(e); return; }
        revSeq = (doc.seq && doc.seq.id) || '';
        var items = doc.items, c = counts(items);
        var filters = [{ id: 'tight', label: L('fillers.fTight'), icon: 'alert', test: function (it) { return !!it.tight; } }];
        if (items.some(function (it) { return it.kind === 'sound'; })) filters.push({ id: 'sound', label: L('fillers.fSound'), test: function (it) { return it.kind === 'sound'; } });
        if (items.some(function (it) { return it.kind === 'habit'; })) filters.push({ id: 'habit', label: L('fillers.fHabit'), test: function (it) { return it.kind === 'habit'; } });
        if (c.g) filters.push({ id: 'gap', label: L('fillers.fGap'), test: function (it) { return it.kind === 'gap'; } });
        ctx.review({
          file: file, doc: doc, unit: L('fillers.unit'), verbOn: L('fillers.removed'), verbOff: L('fillers.kept'), filters: filters,
          tags: rowTags, ctx: rowCtx,
          seekTime: function (it) { return Math.max(0, it.t0 - LEAD); },
          title: function (nOn, nAll) {
            var k = counts(items);
            var what = L('fillers.nFiller', { n: k.fOn }) + (k.g ? ' + ' + L('fillers.nGap', { n: k.gOn }) : '');
            return U.esc(L('fillers.reviewTitle', { what: what })) + '<small>' + U.esc(L('fillers.reviewOf', { n: nAll })) + '</small>';
          },
          bulkAction: { label: L('fillers.toMarkers'), run: sendMarkers },
          primary: { label: function () { return applyLabel(items); }, onApply: applyReview },
          // Persist toggles at once (debounced by the list): the engine's carry_over reads this file on the next
          // run, and the framework only writes it before apply ("uncheck, Batal, Deteksi lagi" lost the choice).
          onChange: function (list) {
            try { list.save(file); } catch (e) { AC.log.warn('fillers: review not saved: ' + U.errMsg(e)); }
          }
        });
      }

      function applyReview(sel, doc, file) {
        ctx.run({ action: 'apply', review: file, title: L('fillers.jobApply'), params: {}, onResult: function (data) {
          var k = (data && data.counts) || {};
          if (!data || !data.plan || !(data.plan.ranges || []).length) { ui.toast(L('fillers.noneChecked')); ctx.go('review'); return; }
          var opts = { unit: L(k.gap && k.filler ? 'fillers.unitCuts' : k.gap ? 'fillers.unitGaps' : 'fillers.unit'), next: ctx.def.next, seqId: revSeq || undefined };
          var task = ctx.applyPlan(data.plan, U.assign({ onDone: function (res) {
            var card = ctx.removeResult(res, data.plan.ranges, U.assign({ secs: task && task.elapsed ? task.elapsed() : 0 }, opts));
            card.onEdit = editAgain;
            ctx.result(card);
          } }, opts));
        } });
      }

      // "Ubah pilihan": bring the reviewed sequence back first, so seeks land on it and the next cut clones it.
      function editAgain() {
        if (revSeq) AC.host.call('bac_openSequence', revSeq).then(null, function (e) { ui.toast(U.errMsg(e), { kind: 'err' }); });
        ctx.go('review');
      }

      function sendMarkers(list) {
        var on = list.selected();
        var marks = on.map(function (it) {
          var gap = it.kind === 'gap';
          return { t: it.t0, end: it.t1, name: gap ? L('fillers.markGap') : L('fillers.markFiller', { label: it.label || '' }), color: MARK_COLOR[it.kind] === undefined ? 3 : MARK_COLOR[it.kind],
                   comment: (it.srcs || [it.src]).map(srcName).join(', ') + (typeof it.conf === 'number' ? ', ' + Math.round(it.conf * 100) + '%' : '') + (it.tight ? ', ' + L('fillers.markTight') : '') };
        });
        AC.host.exec('bac_fillers_markers', [marks, MARK_TAG, revSeq], { json: true, timeout: 60000 }).then(null, function (e) {
          if (e && e.code === 'HOST_EVAL') return AC.apply.markers(marks, { tag: MARK_TAG, replace: true, seqId: revSeq || undefined });   // host file not loaded yet
          throw e;
        }).then(function (r) {
          ui.toast(on.length ? L('fillers.markersMade', { n: (r && r.n) || on.length }) : L('fillers.markersCleared'), 'marker');
        }, function (e) { ui.toast(L('fillers.markersFailed', { msg: U.errMsg(e) }), { kind: 'err' }); });
      }
    },

    onShow: function (ctx) { if (ctx.refreshCounts) ctx.refreshCounts(); },
    onSeq: function (lite, ctx) { if (ctx.refreshCounts) ctx.refreshCounts(); }
  });
})();
