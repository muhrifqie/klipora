/* broll.js: B-Roll. Sources (local folder / Pexels with the key from Settings / AI image stills from the local proxy), placement
   (Otomatis = by screen activity, Layar penuh, PiP), density, max length, transition -> engine "analyze" (one AI
   planning call, cached; rules when AI is off) -> Tinjau list with thumbnail, keyword, source and placement per moment
   (Ganti sumber, Penuh/PiP, Kata lain = engine "fetch" with a new keyword, Buat gambar AI = engine "fetch") ->
   engine "apply" (downloads / AI images / pre-render at the sequence size, no audio) -> host/41_broll.jsx: clone of
   the sequence + top track "Klipora B-Roll" (PiP via Motion, crossfade via opacity keys).
   API: docs/PANEL_API.md, engine: engine/ac/tools/broll.py, notes + verifier steps: docs/tools/broll.md. */
(function () {
  'use strict';
  var AC = window.AC, U = AC.util, ui = AC.ui;
  var TAG = '[Klipora-BROLL]', CHUNK = 4;
  function T(k, v) { return AC.t('broll.' + k, v); }
  var VID = { '.mp4': 1, '.mov': 1, '.m4v': 1, '.mkv': 1, '.webm': 1, '.avi': 1, '.wmv': 1, '.mts': 1, '.m2ts': 1 };
  var IMG = { '.jpg': 1, '.jpeg': 1, '.png': 1, '.webp': 1, '.bmp': 1 };
  // Labels are translated when used (live language switch).
  function srcName(src) { return AC.i18n.has('broll.src.' + src) ? T('src.' + src) : src; }
  function presetsList() {
    return [
      { value: 'jarang', label: T('preset.jarang'), hint: T('preset.jarangHint'), values: { density: 1, maxDur: 4 } },
      { value: 'sedang', label: T('preset.sedang'), hint: T('preset.sedangHint'), values: { density: 2, maxDur: 3.5 } },
      { value: 'sering', label: T('preset.sering'), hint: T('preset.seringHint'), values: { density: 4, maxDur: 3 } }
    ];
  }
  function placeHelpOf(v) { return T('placeHelp.' + (v === 'full' || v === 'pip' ? v : 'auto')); }
  function trackName() { return AC.brand.track('B-Roll'); }      // "Klipora B-Roll" (a Premiere name, not translated)
  // Our own work folders inside a b-roll library: the new "Klipora" and the old "AutoCut BOT".
  var OWN_DIRS = { 'klipora': 1, 'autocut bot': 1 };
  var B = AC.broll = {};   // helpers exposed for tests

  B.fileUrl = function (p) {
    return 'file:///' + encodeURI(String(p).replace(/\\/g, '/')).replace(/#/g, '%23').replace(/\?/g, '%3F');
  };
  // Count video + image files of a folder (3 levels deep, max 3000 entries) without the engine.
  B.countMedia = function (dir) {
    var out = { videos: 0, images: 0, exists: AC.sys.exists(dir) }, seen = 0;
    if (!out.exists) return out;
    (function walk(d, depth) {
      AC.sys.list(d).forEach(function (n) {
        if (seen++ > 3000) return;
        var ext = AC.sys.extname(n).toLowerCase();
        if (VID[ext]) out.videos++;
        else if (IMG[ext]) out.images++;
        else if (!ext && depth < 3 && n.charAt(0) !== '.' && !OWN_DIRS[n.toLowerCase()]) walk(AC.sys.join(d, n), depth + 1);
      });
    })(dir, 0);
    return out;
  };
  B.pickOf = function (it) { return it && it.cands && it.pick >= 0 ? it.cands[it.pick] || null : null; };

  AC.tools.register({
    id: 'broll', group: 'kamera', order: 4,
    icon: 'broll',
    badges: ['ai'],
    text: function (t) {
      return { title: t('broll.title'), tab: t('broll.tab'), desc: t('broll.desc'), lead: t('broll.lead'), cta: t('broll.cta'),
               review: { unit: t('broll.reviewUnit') },
               next: [{ tool: 'captions', reason: t('broll.nextCaptions') }, { tool: 'zoom', reason: t('broll.nextZoom') }] };
    },
    defaults: { preset: 'jarang', density: 1, maxDur: 4, placement: 'auto', transition: 'fade', srcLocal: false, folder: '',
                srcPexels: true, srcAi: true, aiMax: 3, useAi: true, aiRank: true, style: 'foto' },

    render: function (el, ctx) {
      var st = ctx.state;
      U.assign(st, U.assign(U.copy(ctx.def.defaults), st));
      var lastFile = null, lastDoc = null, lastWarn = [];

      var heroTags = ui.h('div', { class: 'tags' });
      var hero = ui.h('div', { class: 'tool-hero' }, [ui.h('span', { class: 'tool-ic', html: ui.icon('broll') }),
        ui.h('div', {}, [ui.h('p', { text: ctx.def.lead }), heroTags])]);
      el.appendChild(hero);

      /* ---------------- Gaya */
      var gaya = ui.section({ title: T('secStyle') });
      var presets = ui.presets({ options: presetsList(), value: st.preset, ariaLabel: T('densAria'), customHint: T('customHint'),
        onChange: function (v, p) {
          if (!p) return;
          st.preset = v; st.density = p.values.density; st.maxDur = p.values.maxDur; ctx.save();
          dens.set(st.density, true); maxd.set(st.maxDur, true); paintEstimate();
        } });
      gaya.add(presets);
      el.appendChild(gaya.el);

      /* ---------------- Sumber */
      var sumber = ui.section({ title: T('secSources') });
      var localSw = ui.switch({ label: T('local'), help: T('localHelp'), checked: st.srcLocal,
        onChange: function (v) { st.srcLocal = v; ctx.save(); paintSources(); } });
      var folderIn = ui.input({ value: st.folder, placeholder: T('folderPh'), mono: true, ariaLabel: T('folderAria'),
        onChange: function (v) { setFolder(v); } });
      var pickBtn = ui.button({ label: T('pick'), icon: 'folder', small: true, onClick: pickFolder });
      var folderInfo = ui.h('small', { class: 'help broll-folder-info' });
      var folderBox = ui.h('div', { class: 'broll-sub' }, [ui.h('div', { class: 'broll-folder' }, [folderIn.el, pickBtn]), folderInfo]);
      var pexSw = ui.switch({ label: T('pexels'), help: '', checked: st.srcPexels,
        onChange: function (v) { st.srcPexels = v; ctx.save(); paintSources(); } });
      var pexHelp = ui.h('div', { class: 'broll-sub broll-pex' });
      var aiSw = ui.switch({ label: T('ai'), badge: ui.badge('ai'), help: T('aiHelp'), checked: st.srcAi,
        onChange: function (v) { st.srcAi = v; ctx.save(); paintSources(); } });
      var aiMax = ui.stepper({ label: T('aiMax'), min: 1, max: 8, step: 1, value: st.aiMax, decimals: 0,
        onChange: function (v) { st.aiMax = v; ctx.save(); } });
      var aiBox = ui.h('div', { class: 'broll-sub' }, [aiMax.el, ui.h('small', { class: 'help broll-quota',
        text: T('quota') })]);
      sumber.add([localSw, folderBox, pexSw, pexHelp, aiSw, aiBox]);
      el.appendChild(sumber.el);

      /* ---------------- Penempatan */
      var tempat = ui.section({ title: T('secPlace') });
      var placeHelp = ui.h('p', { class: 'help' });
      var placeSeg = ui.segmented({ options: [['auto', T('placeAuto')], ['full', T('placeFull')], ['pip', 'PiP']], value: st.placement, ariaLabel: T('placeAria'),
        onChange: function (v) { st.placement = v; ctx.save(); placeHelp.textContent = placeHelpOf(v); } });
      placeHelp.textContent = placeHelpOf(st.placement);
      tempat.add([placeSeg, placeHelp]);
      el.appendChild(tempat.el);

      /* ---------------- Jumlah */
      var jumlah = ui.section({ title: T('secAmount') });
      var dens = ui.slider({ label: T('density'), min: 0.5, max: 6, step: 0.5, value: st.density,
        format: function (v) { return T('densFmt', { v: U.dec(v, v % 1 ? 1 : 0) }); }, help: T('densHelp'),
        onInput: function (v) { st.density = v; manual(); } });
      var maxd = ui.slider({ label: T('maxDur'), min: 1.5, max: 8, step: 0.5, value: st.maxDur, unit: ' ' + AC.t('unit.sec'), decimals: 1,
        help: T('maxDurHelp'), onInput: function (v) { st.maxDur = v; manual(); } });
      var strip = ui.h('div', { class: 'sum-strip broll-strip', 'aria-live': 'polite' });
      jumlah.add([dens, maxd, strip]);
      el.appendChild(jumlah.el);
      function manual() { st.preset = presets.sync({ density: st.density, maxDur: st.maxDur }); ctx.save(); paintEstimate(); }

      /* ---------------- Pengaturan lanjutan */
      var adv = ui.advanced({ title: AC.t('common.advanced'), key: 'broll' });
      var trans = ui.segmented({ options: [['cut', T('transCut')], ['fade', T('transFade')]], value: st.transition, ariaLabel: T('trans'),
        onChange: function (v) { st.transition = v; ctx.save(); } });
      var useAi = ui.switch({ label: T('useAi'), badge: ui.badge('ai'), help: T('useAiHelp'),
        checked: st.useAi, onChange: function (v) { st.useAi = v; ctx.save(); paintSources(); } });
      var aiRank = ui.switch({ label: T('aiRank'), help: T('aiRankHelp'),
        checked: st.aiRank, onChange: function (v) { st.aiRank = v; ctx.save(); } });
      var style = ui.select({ options: [['foto', T('style.foto')], ['ilustrasi', T('style.ilustrasi')], ['sketsa', T('style.sketsa')]], value: st.style, ariaLabel: T('aiStyle'),
        onChange: function (v) { st.style = v; ctx.save(); } });
      adv.add([ui.inlineField({ label: T('trans'), help: T('transHelp'), control: trans.el }), useAi, aiRank,
               ui.inlineField({ label: T('aiStyle'), control: style.el })]);
      el.appendChild(adv.el);

      ctx.dock({ primary: { label: ctx.def.cta, icon: 'search', onClick: run } });

      /* ---------------- painting */
      function hasKey() { return AC.settings.hasSecret('pexelsKey'); }
      function aiState() { var h = AC.engine.lastHealth; return h && h.ai ? h.ai : null; }
      function paintSources() {
        folderBox.classList.toggle('is-off', !st.srcLocal);
        paintFolder();
        var key = hasKey();
        pexSw.input.disabled = !key;
        pexSw.set(st.srcPexels && key, true);
        pexHelp.innerHTML = '';
        if (key) pexHelp.appendChild(ui.h('small', { class: 'help', text: T('pexKey') }));
        else {
          pexHelp.appendChild(ui.h('small', { class: 'help', text: T('pexNoKey') }));
          pexHelp.appendChild(ui.h('button', { class: 'linkbtn', type: 'button', text: T('openSettings'), on: { click: function () { AC.router.go('settings'); } } }));
        }
        aiBox.classList.toggle('is-off', !st.srcAi);
        aiRank.input.disabled = !(st.useAi && st.srcLocal);
        var a = aiState();
        heroTags.innerHTML = ui.badge('ai') + (a && !a.ok && (st.useAi || st.srcAi) ? ui.tag(T('aiOffline'), 'warn', 'alert') : '');
        paintEstimate();
      }
      function paintFolder() {
        if (!st.folder) { folderInfo.textContent = T('noFolder'); folderInfo.className = 'help broll-folder-info'; return; }
        var c = B.countMedia(st.folder);
        folderInfo.className = 'help broll-folder-info' + (c.exists && c.videos + c.images ? '' : ' is-warn');
        folderInfo.textContent = !c.exists ? T('folderMissing') : (c.videos + c.images) ? T('folderCount', { v: c.videos, i: c.images }) : T('folderEmpty');
      }
      function setFolder(v) {
        st.folder = String(v || '').replace(/^"+|"+$/g, '').trim();
        if (st.folder && !st.srcLocal) { st.srcLocal = true; localSw.set(true, true); }
        folderIn.set(st.folder); ctx.save(); paintSources();
      }
      function pickFolder() {
        var cep = window.cep;
        if (cep && cep.fs && cep.fs.showOpenDialogEx) {
          var r = cep.fs.showOpenDialogEx(false, true, T('pickTitle'), st.folder || '');
          if (r && !r.err && r.data && r.data.length) setFolder(r.data[0]);
          return;
        }
        AC.host.exec('bac_broll_pickFolder', [st.folder || ''], { json: true, timeout: 600000 }).then(function (r) {
          if (r && r.path) setFolder(r.path);
        }, function () { ui.toast(T('pasteFolder'), 'folder'); });
      }
      function sourcesText() {
        var a = [];
        if (st.srcLocal && st.folder) a.push(T('src.local'));
        if (st.srcPexels && hasKey()) a.push('Pexels');
        if (st.srcAi) a.push('AI');
        return a.length ? a.join(' + ') : T('srcNone');
      }
      function paintEstimate() {
        var s = AC.seq.peek(), sc = ctx.scope(), dur = 0;
        if (s) dur = (sc && sc.t1 !== undefined && sc.kind !== 'selected') ? Math.max(0, sc.t1 - (sc.t0 || 0)) : s.duration;
        var n = dur ? Math.max(1, Math.round(dur / 60 * st.density)) : 0;
        strip.innerHTML = '<div class="hl"><span>' + U.esc(T('estimate')) + '</span><b>' + (n ? U.esc(T('nBroll', { n: n })) : '-') + '</b></div>' +
          '<div><span>' + U.esc(T('range')) + '</span><b>' + (dur ? U.mmss(dur) : '-') + '</b></div><div><span>' + U.esc(T('secSources')) + '</span><b class="broll-srcs">' + U.esc(sourcesText()) + '</b></div>';
      }
      ctx.brollPaint = paintSources;
      AC.settings.on('change', function (k) { if (k === 'pexelsKey' || k === 'ai' || k === '*') paintSources(); });
      AC.bus.on('health', function () { paintSources(); });

      /* ---------------- run analyze */
      function params() {
        return { density: st.density, max_dur: st.maxDur, placement: st.placement, transition: st.transition,
                 src_local: !!(st.srcLocal && st.folder), folder: st.folder || '', src_pexels: !!(st.srcPexels && hasKey()),
                 src_ai: !!st.srcAi, ai_max: st.aiMax, use_ai: !!st.useAi, ai_rank: !!st.aiRank, style: st.style };
      }
      function run() {
        var p = params();
        if (st.srcLocal && !st.folder) { ui.toast(T('needFolder'), { kind: 'err' }); return; }
        if (p.src_local && !AC.sys.exists(p.folder)) { ui.toast(T('folderNotFound', { path: p.folder }), { kind: 'err' }); return; }
        if (!p.src_local && !p.src_pexels && !p.src_ai) { ui.toast(T('needSource'), { kind: 'err' }); return; }
        ctx.run({ action: 'analyze', title: T('runTitle'), params: p,
          stages: [{ id: 'words', label: T('stage.words'), w: 0.3 }, { id: 'plan', label: T('stage.plan'), w: 0.25 },
                   { id: 'screen', label: T('stage.screen'), w: 0.25 }, { id: 'source', label: T('stage.source'), w: 0.2 }],
          onResult: function (data) { lastWarn = data.warnings || []; showReview(data.review); } });
      }

      /* ---------------- Tinjau */
      function rowCtx(it) {
        var c = B.pickOf(it);
        var th = c && c.thumb ? '<img class="broll-th" src="' + U.esc(B.fileUrl(c.thumb)) + '" alt="">'
          : '<span class="broll-th is-empty">' + ui.icon(c && c.src === 'ai' ? 'spark' : 'broll') + '</span>';
        var src = !c ? T('noSource') : c.src === 'local' ? T('srcLocalN', { name: c.name || '' }) : c.src === 'pexels' ? T('srcPexelsN', { name: c.user || c.name || '' })
          : c.thumb ? T('src.ai') : T('aiLater');
        return '<span class="broll-row">' + th + '<span class="broll-tx"><b data-i18n-skip>' + U.esc(it.kw || it.label || 'B-roll') + '</b>' +
          '<span class="broll-say" data-i18n-skip>' + U.esc(it.text || (it.ctx && it.ctx.post) || '') + '</span><span class="broll-src">' + U.esc(src) + '</span></span></span>';
      }
      function rowTags(it) {
        var c = B.pickOf(it), h = '<span class="tag tag-line">' + (it.place === 'pip' ? 'PiP' : U.esc(T('tagFull'))) + '</span>';
        if (c) h += '<span class="tag ' + (c.src === 'ai' ? 'tag-ai' : 'tag-line') + '">' + U.esc(srcName(c.src)) + '</span>';
        if (it.cands && it.cands.length > 1) h += '<span class="tag tag-line" title="' + U.esc(T('srcOptions')) + '">' + (it.pick + 1) + '/' + it.cands.length + '</span>';
        return h;
      }
      function showReview(file, keepId) {
        lastFile = file;
        var list = ctx.review({ file: file, unit: T('reviewUnit'), verbOn: T('verbOn'), verbOff: T('verbOff'),
          title: function (n, all) { return U.esc(T('rvTitle', { n: n })) + '<small>' + U.esc(T('rvOf', { all: all })) + '</small>'; },
          filters: [{ id: 'local', label: T('src.local'), test: function (it) { var c = B.pickOf(it); return !!c && c.src === 'local'; } },
                    { id: 'pexels', label: 'Pexels', test: function (it) { var c = B.pickOf(it); return !!c && c.src === 'pexels'; } },
                    { id: 'ai', label: 'AI', icon: 'spark', test: function (it) { var c = B.pickOf(it); return !!c && c.src === 'ai'; } },
                    { id: 'busy', label: T('filterBusy'), icon: 'alert', test: function (it) { return it.auto === 'skip'; } }],
          ctx: rowCtx, tags: rowTags,
          toggleText: function (it) { return it.on ? T('skipThis') : T('placeThis'); },
          legendExtra: function (n, all, sec) { return '<span>' + U.esc(T('covered', { time: U.dtk(sec) })) + '</span>'; },
          rowActions: [{ label: T('actCycle'), icon: 'refresh', run: cycle }, { label: T('actFlip'), icon: 'layers', run: flipPlace },
                       { label: T('actOther'), icon: 'search', run: askQuery }, { label: T('actAi'), icon: 'spark', run: makeAi }],
          markerTag: TAG, markerName: function (it) { return 'B-roll: ' + (it.kw || it.label || ''); },
          primary: { label: function (n) { return T('placeN', { n: n }); }, onApply: function (items, doc, f) { applyFile(f, doc); } } });
        if (!list) return;
        lastDoc = list.doc();
        list.el.classList.add('broll-rv');
        var wb = lastWarn.filter(function (w) { return /AI|Pexels|kuota|quota|Folder/i.test(w); });
        if (wb.length) list.el.insertBefore(ui.alert({ kind: 'warn', title: wb[0], text: wb.length > 1 ? wb.slice(1, 3).join(' ') : '' }), list.box);
        if (keepId) {
          var items = list.items();
          for (var i = 0; i < items.length; i++) if (items[i].id === keepId) { list.setActive(i, false); break; }
        }
      }
      function cycle(it, list) {
        if (!it.cands || it.cands.length < 2) { ui.toast(T('noOther'), 'info'); return; }
        it.pick = (it.pick + 1) % it.cands.length; it.src = it.cands[it.pick].src; it.touched = true;
        list.refresh();
        var c = B.pickOf(it);
        ui.toast(T('srcToast', { src: srcName(c.src) }) + (c.name ? ', ' + c.name : ''), 'refresh');
      }
      function flipPlace(it, list) {
        it.place = it.place === 'pip' ? 'full' : 'pip'; it.touched = true; list.refresh();
        ui.toast(it.place === 'pip' ? T('placedPip') : T('placedFull'), 'layers');
      }
      function makeAi(it, list) {
        var k = -1;
        (it.cands || []).forEach(function (c, i) { if (c.src === 'ai' && k < 0) k = i; });
        if (k < 0) { ui.toast(T('aiOff'), 'info'); return; }
        it.pick = k; it.src = 'ai'; it.touched = true;
        if (it.cands[k].thumb) { list.refresh(); ui.toast(T('aiHave'), 'spark'); return; }
        fetchRows({ ids: [it.id] }, it.id, T('aiMaking'));
      }
      var askBox = null, askOff = null;
      function closeAsk() { if (askBox && askBox.parentNode) askBox.parentNode.removeChild(askBox); askBox = null; if (askOff) { askOff(); askOff = null; } }
      function askQuery(it, list) {
        closeAsk();
        var inp = ui.input({ value: it.kw || '', placeholder: T('askPh'), ariaLabel: T('askAria') });
        function go() {
          var q = inp.get().trim();
          if (!q) { inp.el.focus(); return; }
          closeAsk(); fetchRows({ ids: [it.id], query: q }, it.id, T('searching', { q: q }));
        }
        inp.el.addEventListener('keydown', function (e) { if (e.key === 'Enter') { e.preventDefault(); e.stopPropagation(); go(); } });
        askBox = ui.h('div', { class: 'card broll-ask' }, [
          ui.h('b', { text: T('askTitle', { t: U.tcode(it.t0) }) }),
          ui.h('small', { class: 'help', text: T('askHelp') }), inp.el,
          ui.h('div', { class: 'btn-row' }, [ui.button({ label: T('search'), icon: 'search', small: true, onClick: go }), ui.button({ label: AC.t('common.cancel'), small: true, kind: 'ghost', onClick: closeAsk })])]);
        list.el.insertBefore(askBox, list.box);
        askOff = AC.keys.onEscape(function () { if (!askBox) return false; closeAsk(); return true; });
        setTimeout(function () { inp.el.focus(); inp.el.select(); }, 30);
      }
      function fetchRows(prm, keepId, title) {
        try { ctx.reviewList.save(lastFile); } catch (e) { ui.toast(T('saveFail', { msg: U.errMsg(e) }), { kind: 'err' }); return; }
        ctx.run({ action: 'fetch', review: lastFile, seq: null, workdir: AC.sys.dirname(lastFile), title: title, params: prm, scope: false,
          onResult: function (data) {
            lastWarn = [];
            showReview(data.review || lastFile, keepId);
            if (data.warnings && data.warnings.length) ui.toast(data.warnings[0], { kind: 'err' });
          } });
      }

      /* ---------------- Terapkan: engine pre-render, then host placement on a clone */
      function applyFile(file, doc) {
        ctx.run({ action: 'apply', review: file, seq: null, workdir: AC.sys.dirname(file), title: T('applyTitle'), scope: false,
          stages: [{ id: 'source', label: T('stSource'), w: 0.45 }, { id: 'render', label: T('stRender'), w: 0.55 }],
          onResult: function (data) { place(data, doc); } });
      }
      function place(data, doc) {
        var plan = data.plan || {}, items = plan.items || [], seqId = (doc && doc.seq && doc.seq.id) || '';
        var task = new AC.Task({ tool: 'broll', action: 'place', title: ctx.def.title, label: T('taskLabel'), kind: 'host' });
        AC.history.track(task);
        var cancelled = false, made = null, tot = { placed: [], failed: [] };
        task._cancel = function () { cancelled = true; };
        AC.seq.hold(task.promise);
        var track = plan.track || trackName();
        task.plan([{ id: 'clone', label: T('stClone'), w: 0.15 }, { id: 'place', label: T('placeN', { n: items.length }), w: 0.8 },
                   { id: 'open', label: T('stOpen'), w: 0.05 }]);
        ctx.track(task, { title: T('trackTitle'), note: T('trackNote'),
          onRetry: function () { place(data, doc); }, onDone: function (res) { placed(res, data, doc, task); } });
        task.stage({ id: 'clone' });
        AC.host.exec('bac_broll_prepare', [plan.name || '', seqId, track], { json: true, timeout: 90000 }).then(function (c) {
          made = c; task.seqName = c.origName;
          task.stageDone('clone');
          task.stage({ id: 'place', sub: T('stPlaceSub', { track: track }) });
          var from = 0;
          function next() {
            if (from >= items.length) return Promise.resolve();
            if (cancelled) throw U.err('CANCELLED', T('cancelled', { name: c.name }), T('cancelHint'));
            var part = items.slice(from, from + CHUNK);
            return AC.host.exec('bac_broll_place', [c.id, part, { track: track, bin: plan.bin || track }], { json: true, timeout: 180000 }).then(function (r) {
              tot.placed = tot.placed.concat((r && r.placed) || []); tot.failed = tot.failed.concat((r && r.failed) || []);
              from += part.length; task.progress(from / items.length * 100);
              return next();
            });
          }
          return next();
        }).then(function () {
          task.stageDone('place');
          task.stage({ id: 'open' });
          return AC.host.json('bac_openSequence', made.id);
        }).then(function () {
          task.summary = T('summary', { n: tot.placed.length });
          task.done(U.assign({ placed: tot.placed, failed: tot.failed }, made));
        }).catch(function (e) { task.fail(e); });
      }
      function placed(res, data, doc, task) {
        var plan = data.plan || {}, byId = {}, n = res.placed.length, nf = 0, spans = [];
        (plan.items || []).forEach(function (x) { byId[x.id] = x; });
        res.placed.forEach(function (p) { var x = byId[p.id]; if (x && x.place === 'full') nf++; spans.push([p.start, p.end]); });
        var failed = (res.failed || []).map(function (f) { return (byId[f.id] ? U.tcode(byId[f.id].t0) + ' ' : '') + f.err; })
          .concat((data.failed || []).map(function (f) { return U.tcode(f.t0) + ' ' + f.msg; }));
        var actions = [{ label: T('openRender'), icon: 'folder', onClick: function () { AC.sys.openFolder(data.folder); } }];
        if (data.credits_text) actions.push({ label: T('copyCredits'), icon: 'copy', onClick: function () { ui.copy(data.credits_text, T('creditsLabel')); } });
        ctx.result({ title: n ? T('resTitle') : T('resNone'),
          sub: T('resSub', { time: U.dtk(task.elapsed()), name: res.name, track: plan.track || trackName() }),
          statsList: [{ label: 'B-roll', value: U.int(n), accent: true }, { label: T('placeFull'), value: U.int(nf) }, { label: 'PiP', value: U.int(n - nf) }],
          ribbon: { total: plan.duration || (doc && doc.duration) || 0, spans: spans },
          legend: [T('legendOrig'), T('legendTop', { n: n })],
          original: { id: res.origId, name: res.origName },
          onEdit: lastDoc ? function () { ctx.go('review'); } : null,
          onDelete: function () {
            AC.host.json('bac_deleteSequence', res.id).then(function () { ui.toast(T('resDeleted'), 'trash'); ctx.back(); },
              function (e) { ui.toast(U.errMsg(e), { kind: 'err' }); });
          },
          actions: actions,
          warn: failed.length ? { title: T('failN', { n: failed.length }), text: failed.slice(0, 3).join('; ') } : null,
          next: ctx.def.next });
      }

      paintSources();
    },

    onShow: function (ctx) { if (ctx.brollPaint) ctx.brollPaint(); },
    onSeq: function (lite, ctx) { if (ctx.brollPaint && ctx.isActive() && ctx.pane() === 'main') ctx.brollPaint(); },
    onScope: function (scope, ctx) { if (ctx.brollPaint) ctx.brollPaint(); }
  });
})();
