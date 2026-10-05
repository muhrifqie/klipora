/* profanity.js: Sensor Kata Kasar / Bleep Profanity. Finds swear words in the transcript (+ the second "dengar ulang"
   pass, which catches swears Whisper cleaned up), with a tiered ID + EN lexicon (Longgar / Normal / Ketat), the user's
   own word lists, and a context check for words that also have a literal meaning (AI 3-vote, rules when AI is off).
   Flow: settings -> engine analyze (engine/ac/tools/profanity.py, review file) -> review (tier tags, AI votes,
   "Dengar" preview, "Selalu izinkan" / "Selalu sensor") -> engine apply (plan {kind:'censor'}) -> host
   (host/33_profanity.jsx): clone "<seq> (Klipora)", volume keys with 10 ms ramps on the dialog clips, beep / own
   sound clips on a new track "Klipora Sensor", markers [Klipora-PF] (old "AutoCut Sensor" / [AC-PF] still recognised).
   The same choices mask captions (engine profanity.hits_for_timeline). Word lists: %APPDATA%\Klipora\profanity.json.
   UI texts: locale keys profanity.* (Indonesian + English). Doc: docs/tools/profanity.md. */
(function () {
  'use strict';
  var AC = window.AC, U = AC.util, ui = AC.ui;
  var t = AC.t, H = AC.i18n.html;

  var TAG = '[Klipora-PF]', TRACK = AC.brand.track('Sensor'), KEY_CHUNK = 40, TONE_CHUNK = 25, MARK_CHUNK = 200, LEAD = 1;
  var LEVEL_IDS = ['longgar', 'normal', 'ketat'];
  // Option lists are built at render time (the language can change between renders).
  function levelOpts() {
    return LEVEL_IDS.map(function (v) { return { value: v, label: t('profanity.level.' + v), hint: t('profanity.level.' + v + 'Hint'), values: { level: v } }; });
  }
  function levelLabel(v) { return t('profanity.level.' + (LEVEL_IDS.indexOf(v) >= 0 ? v : 'normal')); }
  function modeOpts() {
    return ['beep', 'mute', 'duck', 'custom'].map(function (v) { return { value: v, title: t('profanity.mode.' + v), desc: t('profanity.mode.' + v + 'Desc') }; });
  }
  function modeLabel(m) { return t('profanity.mode.' + m); }
  // Caption mask examples are content (what the caption will show), not UI text.
  var STYLES = [{ value: 'stars', label: 'k****l' }, { value: 'full', label: '******' }, { value: 'bip', label: '[bip]' }];
  var TIER = { 3: ['profanity.tier3', 'err'], 2: ['profanity.tier2', 'warn'], 1: ['profanity.tier1', 'line'], 0: ['profanity.tier0', 'ai'] };
  var SRC_TAG = { user: ['profanity.srcUser', 'profanity.srcUserTip'], listen: ['profanity.srcListen', 'profanity.srcListenTip'] };

  /* ================================================================ word library (%APPDATA%\Klipora\profanity.json) */
  var L = AC.profanity = {};
  L.path = function () { return AC.sys.join(AC.sys.paths.appData, 'profanity.json'); };
  // Same rules as engine normalize_library(): lowercase, single spaces, max 60 chars, unique, block beats allow.
  L.clean = function (s) { return String(s || '').replace(/\s+/g, ' ').replace(/^\s+|\s+$/g, '').toLowerCase().slice(0, 60); };
  L.norm = function (lib) {
    lib = lib && typeof lib === 'object' ? lib : {};
    var uniq = function (xs) {
      var out = [];
      (Array.isArray(xs) ? xs : []).forEach(function (x) { var c = L.clean(x); if (c && /[a-z0-9*]/.test(c) && out.indexOf(c) < 0) out.push(c); });
      return out.slice(0, 500);
    };
    var block = uniq(lib.block), allow = uniq(lib.allow).filter(function (a) { return block.indexOf(a) < 0; });
    var style = ['stars', 'full', 'bip', 'off'].indexOf(lib.captionStyle) >= 0 ? lib.captionStyle : 'stars';
    return { v: 1, block: block, allow: allow, captionStyle: style, updated: lib.updated || null };
  };
  L.load = function () { return L.norm(AC.sys.readJSON(L.path(), null)); };
  L.save = function (lib) {
    lib = L.norm(lib);
    lib.updated = new Date().toISOString();
    AC.sys.writeJSON(L.path(), lib, true);
    AC.bus.emit('profanity-lib', lib);
    return lib;
  };
  // kind 'block' | 'allow': add a word (removed from the other list). Returns the saved library.
  L.add = function (kind, word) {
    var lib = L.load(), w = L.clean(word), other = kind === 'block' ? 'allow' : 'block';
    if (!w) return lib;
    if (lib[kind].indexOf(w) < 0) lib[kind].push(w);
    lib[other] = lib[other].filter(function (x) { return x !== w; });
    return L.save(lib);
  };
  L.remove = function (kind, word) {
    var lib = L.load();
    lib[kind] = lib[kind].filter(function (x) { return x !== word; });
    return L.save(lib);
  };

  function fileUrl(p) { return 'file:///' + encodeURI(String(p).replace(/\\/g, '/')).replace(/#/g, '%23'); }
  function confTag(c) {
    if (typeof c !== 'number') return '';
    var p = Math.round(c * 100), kind = c >= 0.8 ? 'ok' : c >= 0.5 ? 'line' : 'warn';
    return '<span class="tag tag-' + kind + ' conf" title="' + H('profanity.confTip', { p: p }) + '">' + p + '%</span>';
  }
  function rowTags(it) {
    var tr = TIER[it.tier] || ['profanity.tierWord', 'line'], h = '<span class="tag tag-' + tr[1] + ' profanity-tier">' + H(tr[0]) + '</span>';
    if (it.ai && typeof it.ai.votes === 'number') h += '<span class="tag tag-ai" title="' + H('profanity.aiVotesTip') + '">' + H('profanity.aiVotes', { v: it.ai.votes, of: it.ai.of }) + '</span>';
    var s = SRC_TAG[it.src];
    if (s) h += '<span class="tag tag-line profanity-src" title="' + H(s[1]) + '">' + H(s[0]) + '</span>';
    return h + confTag(it.conf);
  }

  AC.tools.register({
    id: 'profanity', group: 'potong', order: 4,
    icon: 'mute', badges: ['ai'],
    text: function (t) {
      return { title: t('profanity.title'), tab: t('profanity.tab'), desc: t('profanity.desc'), lead: t('profanity.lead'), cta: t('profanity.cta'),
        review: { unit: t('profanity.unit'), verbOn: t('profanity.verbOn'), verbOff: t('profanity.verbOff') },
        next: [{ tool: 'captions', reason: t('profanity.nextCaptions') }] };
    },
    defaults: { level: 'normal', mode: 'beep', ai: true, deep: true, captions: true, style: 'stars', freq: 1000, toneAuto: true,
                toneDb: -20, duckDb: -20, custom: '', markers: true, pad: 20, minLen: 0.25 },

    render: function (el, ctx) {
      var st = ctx.state, lib = L.load(), audio = null;
      if (lib.captionStyle === 'off') st.captions = false; else { st.captions = true; st.style = lib.captionStyle; }
      el.appendChild(ui.h('div', { class: 'tool-hero', html: '<span class="tool-ic">' + ui.icon('mute') + '</span><div><p>' + U.esc(ctx.def.lead) + '</p><div class="tags">' + ui.badge('ai') + ui.tag(t('profanity.heroSafe'), 'ok', 'shield') + '</div></div>' }));

      /* ---------------- level */
      var lv = ui.section({ title: t('profanity.levelTitle') });
      var levels = ui.presets({ options: levelOpts(), value: st.level, ariaLabel: t('profanity.levelTitle'), onChange: function (v) { st.level = v; ctx.save(); } });
      lv.add(levels);
      el.appendChild(lv.el);

      /* ---------------- mode */
      var md = ui.section({ title: t('profanity.modeTitle') });
      var modes = ui.radioCards({ ariaLabel: t('profanity.modeTitle'), value: st.mode, options: modeOpts(), onChange: function (v) { st.mode = v; ctx.save(); paintMode(); } });
      md.add(modes);
      var customIn = ui.input({ value: st.custom, placeholder: t('profanity.customPh'), mono: true, ariaLabel: t('profanity.customAria'), onChange: function (v) { st.custom = String(v || '').replace(/^\s*"+|"+\s*$/g, ''); if (st.custom !== v) customIn.set(st.custom, true); ctx.save(); paintMode(); } });
      var pick = ui.button({ label: t('profanity.pickFile'), icon: 'folder', small: true, onClick: pickFile });
      var customMsg = ui.h('small', { class: 'help' });
      var customBox = ui.h('div', { class: 'profanity-custom' }, [ui.h('div', { class: 'profanity-row' }, [customIn.el, pick]), customMsg]);
      md.add(customBox);
      el.appendChild(md.el);

      function pickFile() {
        var fs = window.cep && window.cep.fs;
        if (!fs || !fs.showOpenDialogEx) { ui.toast(t('profanity.pastePath'), 'info'); customIn.el.focus(); return; }
        try {
          var r = fs.showOpenDialogEx(false, false, t('profanity.pickDialog'), st.custom || '', ['wav', 'mp3', 'm4a', 'ogg', 'flac', 'aif', 'aiff']);
          if (r && !r.err && r.data && r.data[0]) { st.custom = String(r.data[0]); customIn.set(st.custom); ctx.save(); paintMode(); }
        } catch (e) { ui.toast(t('profanity.dialogFail', { msg: U.errMsg(e) }), { kind: 'err' }); }
      }
      function customOk() { return !!st.custom && AC.sys.exists(st.custom) && /\.(wav|mp3|m4a|aac|ogg|flac|aif|aiff|wma|opus)$/i.test(st.custom); }
      function paintMode() {
        customBox.hidden = st.mode !== 'custom';
        customMsg.textContent = !st.custom ? t('profanity.customHelp') : customOk() ? t('profanity.customReady', { name: AC.sys.basename(st.custom) }) : t('profanity.customBad');
        customMsg.classList.toggle('is-warn', !!st.custom && !customOk());
        duck.el.hidden = st.mode !== 'duck';
        toneBox.hidden = st.mode !== 'beep';
        ctx.setPrimary({ disabled: st.mode === 'custom' && !customOk(), title: st.mode === 'custom' && !customOk() ? t('profanity.pickFirst') : '' });
      }

      /* ---------------- checks */
      var ck = ui.section({ title: t('profanity.checksTitle') });
      var aiOff = AC.settings.get('ai') === false;
      ck.add(ui.switch({ label: t('profanity.aiLabel'), badge: ui.badge('ai'), checked: st.ai && !aiOff, disabled: aiOff,
        help: aiOff ? t('profanity.aiOffHelp') : t('profanity.aiHelp'),
        onChange: function (v) { st.ai = v; ctx.save(); } }));
      ck.add(ui.switch({ label: t('profanity.deepLabel'), checked: st.deep,
        help: t('profanity.deepHelp'),
        onChange: function (v) { st.deep = v; ctx.save(); } }));
      ck.add(ui.switch({ label: t('profanity.capLabel'), checked: st.captions,
        help: t('profanity.capHelp'),
        onChange: function (v) { st.captions = v; ctx.save(); saveStyle(); } }));
      el.appendChild(ck.el);
      function saveStyle() {
        try { var l = L.load(); l.captionStyle = st.captions ? st.style : 'off'; L.save(l); }
        catch (e) { ui.toast(t('profanity.capSaveFail', { msg: U.errMsg(e) }), { kind: 'err' }); }
      }

      /* ---------------- word lists */
      var wl = ui.section({ title: t('profanity.listsTitle'), help: t('profanity.listsHelp') });
      var blockList = wordList('block', t('profanity.listBlock'), t('profanity.listBlockHelp'));
      var allowList = wordList('allow', t('profanity.listAllow'), t('profanity.listAllowHelp'));
      wl.add([blockList.el, allowList.el]);
      var builtin = ui.h('details', { class: 'profanity-builtin' }, [ui.h('summary', { text: t('profanity.builtinShow') }), ui.h('div', { class: 'profanity-builtin-body help', text: t('common.loading') })]);
      builtin.addEventListener('toggle', function () { if (builtin.open && !builtin._loaded) loadBuiltin(); });
      wl.add(builtin);
      el.appendChild(wl.el);
      function paintLists() { lib = L.load(); blockList.paint(); allowList.paint(); wl.setAside(lib.block.length + lib.allow.length ? t('profanity.listsAside', { b: lib.block.length, a: lib.allow.length }) : ''); }
      var onLib = function () { paintLists(); };
      AC.bus.on('profanity-lib', onLib);

      function wordList(kind, title, help) {
        var box = ui.h('div', { class: 'profanity-list', 'data-kind': kind });
        var toks = ui.h('div', { class: 'profanity-tokens', role: 'list', 'aria-label': title });
        var inp = ui.input({ placeholder: t('profanity.addPh'), ariaLabel: t('profanity.addAria', { list: title }) });
        var add = ui.button({ label: t('profanity.add'), icon: 'plus', small: true, onClick: function () { doAdd(); } });
        inp.el.addEventListener('keydown', function (e) { if (e.key === 'Enter') { e.preventDefault(); e.stopPropagation(); doAdd(); } });
        box.appendChild(ui.h('div', { class: 'profanity-list-h', html: '<b>' + U.esc(title) + '</b><small>' + U.esc(help) + '</small>' }));
        box.appendChild(toks);
        box.appendChild(ui.h('div', { class: 'profanity-row' }, [inp.el, add]));
        function doAdd() {
          var w = L.clean(inp.get());
          if (!w) { inp.el.focus(); return; }
          try {
            var before = L.load(), moved = before[kind === 'block' ? 'allow' : 'block'].indexOf(w) >= 0;
            L.add(kind, w);
            inp.set('');
            ui.toast(t(kind === 'block' ? 'profanity.addedBlock' : 'profanity.addedAllow', { w: w, moved: moved ? t('profanity.movedFromOther') : '' }), kind === 'block' ? 'mute' : 'check');
          } catch (e) { ui.toast(t('profanity.listSaveFail', { msg: U.errMsg(e) }), { kind: 'err' }); }
          inp.el.focus();
        }
        toks.addEventListener('click', function (e) {
          var b = e.target.closest('[data-w]'); if (!b) return;
          try { L.remove(kind, b.getAttribute('data-w')); } catch (err) { ui.toast(t('profanity.listSaveFail', { msg: U.errMsg(err) }), { kind: 'err' }); }
        });
        return {
          el: box,
          paint: function () {
            var words = lib[kind];
            toks.innerHTML = words.length ? words.map(function (w) {
              return '<span class="chip profanity-tok" role="listitem" data-i18n-skip><span>' + U.esc(w) + '</span><button type="button" class="profanity-x" data-w="' + U.esc(w) + '" aria-label="' + H('profanity.removeAria', { w: w }) + '" title="' + H('common.delete') + '">' + ui.icon('x') + '</button></span>';
            }).join('') : '<span class="help">' + H('profanity.listEmpty') + '</span>';
          }
        };
      }
      function loadBuiltin() {
        var body = builtin.querySelector('.profanity-builtin-body');
        var job = AC.engine.worker({ tool: 'profanity', action: 'library', seq: null, params: { op: 'get' }, record: false });
        job.then(function (r) {
          builtin._loaded = true;
          var b = r.builtin || {}, rows = [['3', 'profanity.tier3'], ['2', 'profanity.tier2'], ['1', 'profanity.builtin1'], ['0', 'profanity.builtin0']];
          // The words themselves are content (Indonesian + English lexicon): skipped by the language checks.
          body.innerHTML = rows.map(function (x) { return '<p><b>' + H(x[1]) + '</b> <span data-i18n-skip>' + U.esc((b[x[0]] || []).join(', ')) + '</span></p>'; }).join('') +
            '<p><b>' + H('profanity.builtinLiteral') + '</b> <span data-i18n-skip>' + U.esc((b.literal || []).join(', ')) + '</span></p>';
        }, function (e) { body.textContent = t('profanity.builtinFail', { msg: U.errMsg(e) }); });
      }

      /* ---------------- advanced */
      var adv = ui.advanced({ title: t('common.advanced'), key: 'profanity' });
      var freq = ui.slider({ label: t('profanity.freq'), min: 400, max: 2000, step: 50, value: st.freq, unit: ' Hz', decimals: 0, help: t('profanity.freqHelp'),
        onInput: function (v) { st.freq = v; ctx.save(); } });
      var toneAuto = ui.switch({ label: t('profanity.toneAuto'), checked: st.toneAuto, help: t('profanity.toneAutoHelp'),
        onChange: function (v) { st.toneAuto = v; ctx.save(); toneDb.input.disabled = v; } });
      var toneDb = ui.slider({ label: t('profanity.toneDb'), min: -32, max: -6, step: 1, value: st.toneDb, unit: ' dB', decimals: 0, help: t('profanity.toneDbHelp'),
        onInput: function (v) { st.toneDb = v; ctx.save(); } });
      toneDb.input.disabled = !!st.toneAuto;
      var toneBox = ui.h('div', { class: 'sec' }, [freq.el, toneAuto.el, toneDb.el]);
      var duck = ui.slider({ label: t('profanity.duckDb'), min: -40, max: -6, step: 1, value: st.duckDb, unit: ' dB', decimals: 0, help: t('profanity.duckDbHelp'),
        onInput: function (v) { st.duckDb = v; ctx.save(); } });
      var pad = ui.slider({ label: t('profanity.pad'), min: 0, max: 80, step: 5, value: st.pad, unit: ' ms', decimals: 0, help: t('profanity.padHelp'),
        onInput: function (v) { st.pad = v; ctx.save(); } });
      var minLen = ui.slider({ label: t('profanity.minLen'), min: 0.15, max: 0.6, step: 0.05, value: st.minLen, unit: ' ' + t('unit.sec'), decimals: 2, help: t('profanity.minLenHelp'),
        onInput: function (v) { st.minLen = v; ctx.save(); } });
      var style = ui.segmented({ options: STYLES, value: st.style, small: true, ariaLabel: t('profanity.styleAria'), onChange: function (v) { st.style = v; ctx.save(); saveStyle(); } });
      adv.add([toneBox, duck, pad, minLen,
        ui.switch({ label: t('profanity.markers'), checked: st.markers, help: t('profanity.markersHelp', { tag: TAG }), onChange: function (v) { st.markers = v; ctx.save(); } }),
        ui.field({ label: t('profanity.styleLabel'), control: style.el, help: t('profanity.styleHelp') })]);
      el.appendChild(adv.el);

      ctx.dock({ primary: { label: ctx.def.cta, icon: 'search', onClick: run } });
      paintMode();
      paintLists();

      /* ================================================================ analyze */
      function soundParams() {
        return { mode: st.mode, freq: st.freq, tone_db: st.toneAuto ? 'auto' : st.toneDb, duck_db: st.duckDb, custom: st.custom, markers: !!st.markers };
      }
      function run() {
        if (st.mode === 'custom' && !customOk()) { ui.toast(t('profanity.pickCustomFirst'), { kind: 'err' }); return; }
        var stages = [{ id: 'words', label: t('profanity.stWords'), w: 0.45 }];
        if (st.deep) stages.push({ id: 'listen', label: t('profanity.stListen'), w: 0.2 });
        stages.push({ id: 'scan', label: t('profanity.stScan'), w: 0.05 }, { id: 'edges', label: t('profanity.stEdges'), w: 0.1 });
        if (st.ai && !aiOff) stages.push({ id: 'ai', label: t('profanity.stAi'), w: 0.15 });
        stages.push({ id: 'review', label: t('profanity.stReview'), w: 0.05 });
        ctx.run({ action: 'analyze', title: t('profanity.runTitle'), stages: stages,
          params: { level: st.level, ai: !!st.ai && !aiOff, deep: !!st.deep, pad: st.pad / 1000, min_len: st.minLen },
          onResult: onAnalyzed });
      }

      function onAnalyzed(data) {
        if (!data || !data.review) { ctx.error(U.err('NO_RESULT', t('profanity.noReview'))); return; }
        if (!data.n) { noHits(data); return; }
        openReview(data.review);
      }

      function noHits(data) {
        var s = data.stats || {}, lvId = LEVEL_IDS.indexOf(data.level || st.level) >= 0 ? (data.level || st.level) : 'normal', lvName = levelLabel(lvId);
        var tip = s.below ? t('profanity.noHitsBelow', { n: s.below, level: lvName }) : t('profanity.noHitsTip');
        ctx.result({ title: t('profanity.noHitsTitle'), sub: t('profanity.noHitsSub', { n: s.words || 0, level: lvName }),
          statsList: [{ label: t('profanity.statChecked'), value: U.int(s.words || 0) }, { label: t('profanity.statMild'), value: U.int(s.below || 0), accent: !!s.below }, { label: t('profanity.statLevel'), value: lvName }],
          extra: ui.h('p', { class: 'help', text: tip }),
          actions: s.below && lvId !== 'ketat' ? [{ label: t('profanity.retryStrict'), icon: 'search', onClick: function () { levels.set('ketat'); run(); } }] : [],
          primary: { label: t('profanity.changeSettings'), kbd: false, onClick: function () { ctx.back(); } } });
      }

      /* ================================================================ review */
      var reviewFile = null, reviewDoc = null;
      function openReview(file) {
        reviewFile = file;
        var list = ctx.review({ file: file, unit: t('profanity.unit'), verbOn: t('profanity.verbOn'), verbOff: t('profanity.verbOff'), tags: rowTags,
          filters: [{ id: 'amb', label: t('profanity.tier0'), icon: 'spark', test: function (it) { return it.tier === 0; } },
                    { id: 'listen', label: t('profanity.srcListen'), icon: 'wave', test: function (it) { return it.src === 'listen'; } }],
          seekTime: function (it) { return Math.max(0, it.t0 - LEAD); },
          markerTag: '[Klipora-PF-RV]', markerName: function (it) { return t('profanity.rvMarker', { mask: it.mask || '' }); },
          rowActions: [{ label: t('profanity.listen'), icon: 'play', run: preview },
                       { label: t('profanity.listAllow'), icon: 'check', run: function (it, l) { remember(it, l, 'allow'); } },
                       { label: t('profanity.listBlock'), icon: 'mute', run: function (it, l) { remember(it, l, 'block'); } }],
          title: function (on, all) { return H('profanity.rvTitle', { n: on }) + '<small>' + H('profanity.rvTitleSub', { all: all, mode: modeLabel(st.mode) }) + '</small>'; },
          toggleText: function (it) { return it.on ? t('profanity.rvOff') : t('profanity.rvOn'); },
          legendExtra: function (on, all, sec) { return '<span>' + H('profanity.rvLegend', { dur: U.dtk(sec) }) + '</span>'; },
          primary: { label: function (n) { return t('profanity.apply.' + st.mode, { n: n }); }, onApply: applyReview } });
        reviewDoc = list ? list.doc() : null;
        if (reviewDoc && reviewDoc.sensor_track) ui.toast(t('profanity.rvAgain'), 'alert');
      }

      // "Selalu izinkan" / "Selalu sensor": remember the word in the library and flip this row.
      function remember(it, list, kind) {
        var word = it.src === 'user' ? it.lemma : (it.lemma || L.clean(String(it.label || '').replace(/[^\w\s*-]/g, '')));
        try {
          if (kind === 'allow' && it.src === 'user') L.remove('block', it.lemma); else L.add(kind, word);
        } catch (e) { ui.toast(t('profanity.listSaveFail', { msg: U.errMsg(e) }), { kind: 'err' }); return; }
        list.toggle(list.items().indexOf(it), kind === 'block');
        ui.toast(t(kind === 'allow' ? 'profanity.rememberAllow' : 'profanity.rememberBlock', { w: word }), kind === 'allow' ? 'check' : 'mute');
      }

      function preview(it) {
        if (!it.media || typeof it.ms0 !== 'number') { ui.toast(t('profanity.pvNone'), 'info'); return; }
        if (st.mode === 'custom' && !customOk()) { ui.toast(t('profanity.pickCustomFirst'), { kind: 'err' }); return; }
        ui.toast(t('profanity.pvMaking'), 'clock');
        var job = AC.engine.worker({ tool: 'profanity', action: 'preview', seq: null, record: false, workdir: AC.sys.dirname(reviewFile),
          params: U.assign(soundParams(), { media: it.media, s0: it.ms0, s1: it.ms1, lo: it.lo, hi: it.hi, id: it.id, speech_db: reviewDoc && reviewDoc.speech_db }) });
        job.then(function (r) {
          try {
            if (audio) audio.pause();
            audio = new Audio(fileUrl(r.path));
            var p = audio.play();
            ui.toast(t('profanity.pvPlaying', { mode: modeLabel(st.mode).toLowerCase(), dur: U.dtk(r.dur) }), 'play');
            if (p && p.catch) p.catch(function (e) { ui.toast(t('profanity.pvCantPlay', { msg: U.errMsg(e) }), { kind: 'err' }); });
          } catch (e) { ui.toast(t('profanity.pvCantPlay', { msg: U.errMsg(e) }), { kind: 'err' }); }
        }, function (e) { if (e && e.code !== 'CANCELLED') ui.toast(t('profanity.pvFail', { msg: U.errMsg(e) }), { kind: 'err' }); });
      }

      /* ================================================================ apply (engine plan -> host on a clone) */
      function applyReview(items, doc, file) {
        if (ctx.job() && ctx.job().isActive()) { ui.toast(t('profanity.busy'), 'clock'); return; }
        var made = null, eng = null, cancelled = false;
        var task = new AC.Task({ tool: 'profanity', action: 'apply', title: ctx.def.title, label: t('profanity.taskLabel'), kind: 'host' });
        task.plan([{ id: 'plan', label: t('profanity.stPlan'), w: 0.15 }, { id: 'clone', label: t('profanity.stClone'), w: 0.1, sub: t('profanity.origUntouched') },
                   { id: 'keys', label: t('profanity.stKeys', { n: items.length }), w: 0.45 }, { id: 'tones', label: t('profanity.stTones'), w: 0.2 },
                   { id: 'marks', label: t('profanity.stMarks'), w: 0.1 }]);
        task.seqName = doc.seq && doc.seq.name || '';
        AC.history.track(task);
        task._cancel = function () { cancelled = true; if (eng && eng.isActive()) eng.cancel(); };
        AC.seq.hold(task.promise);
        ctx.track(task, { title: t('profanity.applyTitle'), note: t('profanity.applyNote'),
          onDone: function (res) { showResult(res, doc); }, onRetry: function () { applyReview(items, doc, file); } });
        function check() { if (cancelled) throw U.err('CANCELLED', t('profanity.cancelled')); }
        function chunks(list, n, fn, stageId) {
          var i = 0, total = list.length;
          function next() {
            check();
            if (i >= total) return Promise.resolve();
            var part = list.slice(i, i + n), from = i;
            i += n;
            return fn(part, from, i >= total).then(function () { task.progress(Math.min(100, i / total * 100)); return next(); });
          }
          task.stage({ id: stageId });
          return next().then(function () { task.stageDone(stageId); });
        }
        var plan, info = { keys: 0, clips: 0, missed: [], locked: [], placed: 0, missing: [] };
        task.stage({ id: 'plan' });
        eng = AC.engine.run({ tool: 'profanity', action: 'apply', review: file, seq: null, workdir: AC.sys.dirname(file), params: soundParams(), record: false, title: t('profanity.engTitle') });
        eng.on('progress', function () { task.progress(eng.pct); });
        eng.promise.then(function (r) {
          plan = r.plan; task.stageDone('plan'); check();
          task.stage({ id: 'clone' });
          var srcId = (plan.seq && plan.seq.id) || (doc.seq && doc.seq.id) || '';
          // The sensor track name is always passed: a re-run on an already censored sequence reuses (and clears) it
          // (the host also recognises the old "AutoCut Sensor" track and [AC-PF] markers).
          return AC.host.exec('bac_profanity_begin', [srcId, plan.name, TRACK, { track: plan.tones.length > 0, tag: TAG }], { json: true, timeout: 60000 });
        }).then(function (c) {
          made = c; task.stageDone('clone');
          return chunks(plan.ranges, KEY_CHUNK, function (part, from, last) {
            return AC.host.exec('bac_profanity_keys', [made.id, part, { mode: plan.mode, duckDb: plan.duck_db, ramp: plan.ramp, skipTrack: made.track, last: last }], { json: true, timeout: 180000 })
              .then(function (k) {
                info.keys += k.keys; info.clips += k.clips;
                (k.missed || []).forEach(function (m) { info.missed.push(from + m); });
                (k.locked || []).forEach(function (n) { if (info.locked.indexOf(n) < 0) info.locked.push(n); });
              });
          }, 'keys');
        }).then(function () {
          // Nothing could be keyed (dialog on a locked track, clips gone): a clone with only markers (or beeps over
          // the audible word) would look finished. Fail instead; the catch below deletes the clone.
          if (plan.ranges.length && info.missed.length >= plan.ranges.length) {
            if (info.locked.length) throw U.err('LOCKED', t('profanity.errLocked', { tracks: info.locked.join(', ') }), t('profanity.errLockedHint'));
            throw U.err('NO_AUDIO', t('profanity.errNoAudio'), t('profanity.errNoAudioHint'));
          }
          if (!plan.tones.length) { task.stage({ id: 'tones' }); task.stageDone('tones', 0, t('profanity.notNeeded')); return null; }
          return chunks(plan.tones, TONE_CHUNK, function (part) {
            return AC.host.exec('bac_profanity_tones', [made.id, made.track, part], { json: true, timeout: 180000 })
              .then(function (r) { info.placed += r.placed; info.missing = info.missing.concat(r.missing || []); });
          }, 'tones');
        }).then(function () {
          if (!plan.markers.length) { task.stage({ id: 'marks' }); task.stageDone('marks', 0, t('profanity.off')); return null; }
          return chunks(plan.markers, MARK_CHUNK, function (part) { return AC.host.exec('bac_addMarkers', [part, made.id], { json: true, timeout: 60000 }); }, 'marks');
        }).then(function () {
          task.summary = t('profanity.summary', { n: items.length, mode: modeLabel(plan.mode) });
          task.done({ made: made, plan: plan, info: info, n: items.length, secs: task.elapsed() });
        }).catch(function (e) {
          if (!made) { task.fail(e); return; }
          // A half-censored copy is a trap (it looks finished): remove the clone we made, reopen the original.
          var code = e && e.code, own = code === 'LOCKED' || code === 'NO_AUDIO';
          var why = code === 'CANCELLED' ? t('profanity.cancelled') : own ? e.msg : t('profanity.failMid');
          AC.host.json('bac_deleteSequence', made.id).then(function () { return AC.host.json('bac_openSequence', made.origId); }, function () { return null; })
            .then(function () { return null; }, function () { return null; })
            .then(function () {
              var err = U.err(code || 'HOST', t('profanity.failCleaned', { why: why, name: made.name }), own ? e.hint : (e && (e.msg || e.message) || ''));
              task.fail(err);
            });
        });
      }

      // Words captions can mask: checked rows that exist in the main transcript (dengar-ulang-only rows are not captioned).
      function capWords(doc) { return (doc.items || []).filter(function (it) { return it.on && it.words && it.words.length; }).length; }

      function showResult(res, doc) {
        var plan = res.plan, info = res.info, made = res.made, ranges = plan.ranges || [];
        var sec = ranges.reduce(function (a, r) { return a + (r.t1 - r.t0); }, 0), first7 = ranges.filter(function (r) { return r.t0 < 7; }).length;
        var warns = [];
        if (info.missed.length) warns.push(t('profanity.wMissed', { n: info.missed.length, tag: TAG }));
        if (info.locked.length) warns.push(t('profanity.wLocked', { tracks: info.locked.join(', ') }));
        if (info.missing.length) warns.push(t('profanity.wImport', { n: info.missing.length }));
        if (plan.tones.length && !made.named) warns.push(t('profanity.wTrackName', { track: TRACK, idx: 'A' + (made.track + 1) }));
        var undo = made.undo || {};
        var undoTxt = undo.ranges || undo.clips ? ' ' + t('profanity.undoTxt', { n: undo.ranges || undo.clips }) : '';
        if (doc.sensor_track && !undo.ranges && !undo.clips) warns.push(t('profanity.wSensorSrc', { track: TRACK }));
        // Frequency as a plain string: "1000 Hz", never a thousands separator.
        var modeTxt = modeLabel(plan.mode) + (plan.mode === 'beep' ? ' ' + String(plan.freq) + ' Hz, ' + U.dec(plan.tone_db, 0) + ' dB' : plan.mode === 'duck' ? ' ' + U.dec(plan.duck_db, 0) + ' dB' : '');
        var extra = t('profanity.resKeys', { name: made.name, mode: modeTxt, n: info.keys }) +
          (plan.tones.length ? t('profanity.resTones', { n: info.placed, track: made.trackName || TRACK }) : '') + '.' +
          (st.captions && capWords(doc) ? ' ' + t('profanity.resCaptions', { n: capWords(doc) }) : '') + undoTxt;
        ctx.result({
          title: t('profanity.resTitle'),
          sub: t('profanity.resSub', { n: res.n, dur: U.dtk(res.secs) }),
          statsList: [{ label: t('profanity.statDone'), value: t('profanity.nWords', { n: res.n }), accent: true }, { label: t('profanity.statMode'), value: modeLabel(plan.mode) },
                      { label: t('profanity.statFirst7'), value: t('profanity.nWords', { n: first7 }) }],
          ribbon: { total: doc.duration || 0, spans: ranges.map(function (r) { return [r.t0, r.t1]; }) },
          legend: [t('profanity.legendIntact'), t('profanity.legendCensored', { n: ranges.length, dur: U.dtk(sec) })],
          extra: ui.h('p', { class: 'help', text: extra }),
          original: { id: made.origId, name: made.origName },
          onEdit: function () { ctx.go('review'); },
          onDelete: function () {
            AC.host.json('bac_deleteSequence', made.id).then(function () { return AC.host.json('bac_openSequence', made.origId); })
              .then(function () { ui.toast(t('profanity.deleted'), 'trash'); ctx.go('review'); },
                function (e) { ui.toast(t('profanity.deleteFail', { msg: U.errMsg(e) }), { kind: 'err' }); });
          },
          actions: [{ label: t('profanity.copyList'), icon: 'copy', onClick: function () {
            ui.copy(ranges.map(function (r) { return U.tcode(r.t0) + '  ' + r.masks.join(', '); }).join('\n'), t('profanity.listsTitle'));
          } }],
          warn: warns.length ? { title: warns.length === 1 ? t('profanity.warnTitle1') : t('profanity.warnTitleN', { n: warns.length }), text: warns.join(' ') } : null,
          next: ctx.def.next
        });
      }
    },

    onShow: function (ctx) { if (ctx.state && ctx.el) { /* library may have changed in another panel */ AC.bus.emit('profanity-lib', AC.profanity.load()); } }
  });
})();
