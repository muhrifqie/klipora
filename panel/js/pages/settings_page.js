/* settings_page.js: Settings (ux_design.md 4.8). Every control writes AC.settings immediately
   (%APPDATA%\Klipora\settings.json). The AI key is never shown: only "saved" / "not set" from the
   engine health check. The Pexels key field is write-only (the stored value is never put back into the DOM). */
(function () {
  'use strict';
  var AC = window.AC, U = AC.util, ui = AC.ui, S = AC.settings;
  var els = {}, ctl = {};

  // Option lists are built at render time (translated labels).
  function models() { return [['large-v3-turbo', 'large-v3-turbo'], ['large-v3', 'large-v3'], ['medium', 'medium'], ['small', 'small (CPU)']]; }
  function speechLangs() { return [['id', AC.t('settings.speechId')], ['en', AC.t('settings.speechEn')], ['auto', AC.t('settings.speechAuto')]]; }
  function templates() {
    return ['sorot', 'karaoke', 'kotak', 'pil', 'bersih', 'satu', 'stabilo', 'ketik', 'merek'].map(function (id) { return [id, AC.t('settings.tpl.' + id)]; });
  }
  function secs(ms) { return ms < 1000 ? ms + ' ms' : U.dec(ms / 1000, 1) + ' ' + AC.t('unit.sec'); }

  function row(label, help, right, cls) {
    var left = ui.h('div', { html: U.esc(label) + (help ? '<small>' + U.esc(help) + '</small>' : '') });
    return ui.h('div', { class: 'set-i' + (cls ? ' ' + cls : '') }, [left, right]);
  }
  function statusPill(el, state, text) { el.className = 'status-pill' + (state === 'busy' ? ' is-busy' : state === 'err' ? ' is-err' : ''); el.textContent = text; }

  function paintHealth(h) {
    if (!els.ai) return;
    if (!h || h.busy) { statusPill(els.ai, 'busy', AC.t('common.checking')); return; }
    var ai = h.ai || {};
    if (!S.get('ai')) statusPill(els.ai, 'busy', AC.t('settings.aiOff'));
    else if (ai.ok) statusPill(els.ai, 'ok', AC.t('settings.aiOn') + (typeof ai.ms === 'number' ? ', ' + secs(ai.ms) : ''));
    else statusPill(els.ai, 'err', ai.msg ? AC.t('settings.aiOffline') : AC.t('settings.aiNotConnected'));
    els.aiWhy.textContent = ai.ok ? '' : (ai.msg || (h.error ? U.errMsg(h.error) : ''));
    var prov = h.data && h.data.ai && h.data.ai.profile;   // ai_settings.js: provider profiles
    els.aiHost.textContent = (prov && prov.name ? prov.name + ', ' : '') + (ai.base || '127.0.0.1:8168');
    var d = h.data || {};
    if (d.ffmpeg) els.ffmpeg.innerHTML = d.ffmpeg.ok ? ui.tag(AC.t('common.ready'), 'ok', 'check') : ui.tag(AC.t('settings.missing'), 'err', 'x');
    if (d.ffmpeg) els.ffmpegSub.textContent = d.ffmpeg.ok ? String(d.ffmpeg.version || '').replace(/^ffmpeg version\s*/, '').split(' ')[0] + (d.ffmpeg.libass ? ', libass' : '') + (d.ffmpeg.nvenc ? ', NVENC' : '') : (d.ffmpeg.why || '');
    if (d.gpu) els.gpu.innerHTML = d.gpu.ok ? ui.tag(AC.t('common.ready'), 'ok', 'check') : ui.tag('CPU', 'warn', 'chip');
    if (d.gpu) els.gpuSub.textContent = d.gpu.ok ? (d.gpu.name || 'GPU') + (d.gpu.mem_total_mb ? ', ' + Math.round(d.gpu.mem_total_mb / 1024) + ' GB' : '') : (d.gpu.why || AC.t('settings.noGpu'));
    if (d.python) els.pyVer.textContent = 'Python ' + (d.python.version || '');
    if (d.python) els.pyTag.innerHTML = d.python.ok ? ui.tag(AC.t('common.ready'), 'ok', 'check') : ui.tag(AC.t('settings.oldVersion'), 'warn', 'alert');
    els.issues.innerHTML = '';
    (h.issues || []).slice(0, 4).forEach(function (t) { els.issues.appendChild(ui.alert({ kind: 'warn', title: t })); });
  }

  function testPython() {
    var py = ctl.python.get().trim() || 'python';
    S.set('python', py);
    els.pyTag.innerHTML = ui.tag(AC.t('settings.checkingTag'), 'line');
    AC.engine.pythonVersion(py).then(function (v) {
      els.pyVer.textContent = v; els.pyTag.innerHTML = ui.tag(AC.t('common.ready'), 'ok', 'check');
      ui.toast(AC.t('settings.pyReady', { v: v }), 'check');
      return AC.engine.health().then(function (h) { if (!h.ok && h.error) ui.toast(AC.t('settings.engineErr', { msg: U.errMsg(h.error) }), { kind: 'err' }); });
    }, function (e) {
      els.pyVer.textContent = U.errMsg(e); els.pyTag.innerHTML = ui.tag(AC.t('common.failed'), 'err', 'x');
      ui.toast(AC.t('settings.pyFail', { msg: U.errMsg(e) }), { kind: 'err' });
    });
  }

  function build(el) {
    /* Engine */
    var mesin = ui.section({ title: AC.t('settings.engine'), action: { label: AC.t('settings.recheck'), onClick: function () { AC.engine.health(); } } });
    var list = ui.h('div', { class: 'set-list' });
    els.pyVer = ui.h('small', { class: 'tc', text: AC.t('common.notChecked') });
    els.pyTag = ui.h('span', { html: ui.tag(AC.t('common.notChecked'), 'line') });
    var pyHead = ui.h('div', { class: 'set-i' }, [ui.h('div', {}, ['Python', els.pyVer]), els.pyTag]);
    ctl.python = ui.input({ value: S.get('python'), mono: true, placeholder: AC.t('settings.pyPh'), ariaLabel: AC.t('settings.pyAria'), onChange: function (v) { S.set('python', v.trim() || 'python'); } });
    var pyRow = ui.h('div', { class: 'input-row' }, [ctl.python.el, ui.button({ label: AC.t('common.test'), icon: 'refresh', small: true, onClick: testPython })]);
    list.appendChild(pyHead); list.appendChild(pyRow);
    els.ffmpegSub = ui.h('small', { text: AC.t('common.notChecked') }); els.ffmpeg = ui.h('span');
    list.appendChild(ui.h('div', { class: 'set-i' }, [ui.h('div', {}, ['FFmpeg', els.ffmpegSub]), els.ffmpeg]));
    els.gpuSub = ui.h('small', { text: AC.t('common.notChecked') }); els.gpu = ui.h('span');
    list.appendChild(ui.h('div', { class: 'set-i' }, [ui.h('div', {}, ['GPU', els.gpuSub]), els.gpu]));
    ctl.gpu = ui.switch({ label: AC.t('settings.gpu'), help: AC.t('settings.gpuHelp'), checked: S.get('gpu'), onChange: function (v) { S.set('gpu', v); } });
    list.appendChild(ctl.gpu.el);
    ctl.model = ui.select({ options: models(), value: S.get('whisperModel'), ariaLabel: AC.t('settings.model'), onChange: function (v) { S.set('whisperModel', v); } });
    list.appendChild(row(AC.t('settings.model'), AC.t('settings.modelHelp'), ctl.model.el));
    ctl.lang = ui.select({ options: speechLangs(), value: S.get('lang'), ariaLabel: AC.t('settings.speechLang'), onChange: function (v) { S.set('lang', v); } });
    list.appendChild(row(AC.t('settings.speechLang'), '', ctl.lang.el));
    mesin.add(list);
    var wr = ui.h('div', { class: 'set-stack' });
    wr.appendChild(ui.h('div', { html: U.esc(AC.t('settings.workRoot')) + '<small>' + U.esc(AC.t('settings.workRootHelp')) + '</small>' }));
    ctl.work = ui.input({ value: S.workRoot(), mono: true, ariaLabel: AC.t('settings.workRoot'), onChange: function (v) { S.set('workRoot', v.trim() || AC.sys.join(AC.sys.paths.videos, 'Klipora')); ctl.work.set(S.workRoot()); } });
    wr.appendChild(ui.h('div', { class: 'input-row' }, [ctl.work.el, ui.button({ label: AC.t('common.open'), icon: 'folder', small: true, kind: 'ghost', onClick: function () {
      try { AC.sys.mkdirp(S.workRoot()); AC.sys.openFolder(S.workRoot()); } catch (e) { ui.toast(U.errMsg(e), { kind: 'err' }); }
    } })]));
    mesin.add(wr);
    var od = ui.h('div', { class: 'set-stack' });
    od.appendChild(ui.h('div', { html: U.esc(AC.t('settings.outDir')) + '<small>' + U.esc(AC.t('settings.outDirHelp')) + '</small>' }));
    ctl.out = ui.input({ value: S.get('outputDir'), mono: true, placeholder: AC.t('settings.outDirPh'), ariaLabel: AC.t('settings.outDir'), onChange: function (v) { S.set('outputDir', v.trim()); } });
    od.appendChild(ctl.out.el);
    mesin.add(od);
    els.issues = ui.h('div', { class: 'sec', style: { gap: '6px' } });
    mesin.add(els.issues);
    el.appendChild(mesin.el);

    /* AI */
    var ai = ui.section({ title: 'AI' });
    ctl.ai = ui.switch({ label: AC.t('settings.useAi'), help: AC.t('settings.useAiHelp'), checked: S.get('ai'), onChange: function (v) { S.set('ai', v); paintHealth(AC.engine.lastHealth); } });
    ai.add(ctl.ai);
    var al = ui.h('div', { class: 'set-list' });
    els.aiHost = ui.h('small', { class: 'tc', text: '127.0.0.1:8168' });
    els.ai = ui.h('span', { class: 'status-pill is-busy', id: 'aiStatus', text: AC.t('common.notChecked') });
    els.aiWhy = ui.h('small');
    al.appendChild(ui.h('div', { class: 'set-i' }, [ui.h('div', {}, [AC.t('settings.status'), els.aiHost, els.aiWhy]), els.ai]));
    ai.add(al);
    ai.add(ui.h('div', { class: 'btn-row' }, [ui.button({ label: AC.t('settings.testConn'), icon: 'refresh', small: true, id: 'btnAiTest', onClick: function () {
      AC.engine.health().then(function (h) {
        if (h.ai && h.ai.ok) ui.toast(AC.t('settings.aiAnswered', { t: secs(h.ai.ms || 0) }), 'check');
        else ui.toast(AC.t('settings.aiFail', { msg: h.ai && h.ai.msg || U.errMsg(h.error) }), { kind: 'err' });
      });
    } })]));
    if (AC.aiSettings) ai.add(AC.aiSettings.mount());   // AI providers: profiles, keys (write-only), order, tests
    el.appendChild(ai.el);

    /* Stock media (B-Roll) */
    var media = ui.section({ title: AC.t('settings.stock') });
    var pex = ui.h('div', { class: 'set-stack' });
    els.pexState = ui.h('span', { class: 'val' });
    var pexHead = ui.h('div', { class: 'set-i' }, [ui.h('div', { html: U.esc(AC.t('settings.pexels')) + '<small>' + U.esc(AC.t('settings.pexelsHelp')) + '</small>' }), els.pexState]);
    ctl.pex = ui.input({ type: 'password', placeholder: AC.t('settings.pexelsPh'), ariaLabel: AC.t('settings.pexelsAria') });
    var save = ui.button({ label: AC.t('common.save'), small: true, onClick: function () {
      var v = ctl.pex.get().trim();
      if (v.length < 10) { ui.toast(AC.t('settings.pexelsShort'), { kind: 'err' }); return; }
      S.setSecret('pexelsKey', v); ctl.pex.set(''); paintPexels(); ui.toast(AC.t('settings.pexelsSaved'), 'lock');
    } });
    els.pexDel = ui.confirmClick(ui.button({ label: AC.t('common.delete'), small: true, kind: 'ghost-danger' }), function () { S.setSecret('pexelsKey', ''); paintPexels(); ui.toast(AC.t('settings.pexelsDeleted'), 'trash'); }, AC.t('settings.sure'));
    pex.appendChild(pexHead);
    pex.appendChild(ui.h('div', { class: 'input-row' }, [ctl.pex.el, save, els.pexDel]));
    media.add(pex);
    el.appendChild(media.el);

    /* Caption */
    var cap = ui.section({ title: AC.t('settings.captions') });
    ctl.tpl = ui.select({ options: templates(), value: S.get('captionTemplate'), ariaLabel: AC.t('settings.tplAria'), onChange: function (v) { S.set('captionTemplate', v); } });
    cap.add(ui.h('div', { class: 'set-list' }, [row(AC.t('settings.tpl'), AC.t('settings.tplHelp'), ctl.tpl.el)]));
    if (AC.brandKit) cap.add(AC.brandKit.settingsRow());   // Brand Kit (js/tools/captions_brand.js)
    el.appendChild(cap.el);

    /* Appearance */
    var look = ui.section({ title: AC.t('settings.look') });
    // Display language: switching re-renders every screen at once (router remount), no panel reload.
    ctl.uiLang = ui.select({ options: [['auto', AC.t('lang.auto')], ['id', AC.t('lang.id')], ['en', AC.t('lang.en')]], value: AC.i18n.setting(),
      ariaLabel: AC.t('settings.uiLang'), onChange: function (v) { AC.i18n.set(v); } });
    ctl.uiLang.el.id = 'setUiLang';
    look.add(ui.h('div', { class: 'set-list' }, [row(AC.t('settings.uiLang'), AC.t('settings.uiLangHelp'), ctl.uiLang.el)]));
    ctl.density = ui.segmented({ small: true, width: '150px', ariaLabel: AC.t('settings.density'), value: S.get('density'), options: [['comfortable', AC.t('settings.densityComfy')], ['compact', AC.t('settings.densityCompact')]], onChange: function (v) { S.set('density', v); } });
    ctl.density.el.id = 'setDensity';
    look.add(ui.inlineField({ label: AC.t('settings.density'), help: AC.t('settings.densityHelp'), control: ctl.density.el }));
    ctl.follow = ui.switch({ label: AC.t('settings.follow'), help: AC.t('settings.followHelp'), checked: S.get('followTheme'), onChange: function (v) { S.set('followTheme', v); } });
    ctl.motion = ui.switch({ label: AC.t('settings.motion'), help: AC.t('settings.motionHelp'), checked: S.get('reduceMotion'), onChange: function (v) { S.set('reduceMotion', v); } });
    look.add([ctl.follow, ctl.motion]);
    el.appendChild(look.el);

    /* Data */
    var data = ui.section({ title: AC.t('settings.data') });
    var dl = ui.h('div', { class: 'set-list' });
    dl.appendChild(row(AC.t('settings.history'), AC.t('settings.historyHelp', { path: AC.sys.join(AC.sys.paths.appData, 'history.json') }),
      ui.confirmClick(ui.button({ label: AC.t('settings.clear'), small: true, kind: 'ghost' }), function () { AC.history.clear(); ui.toast(AC.t('settings.historyCleared'), 'trash'); })));
    dl.appendChild(row(AC.t('keys.title'), '', ui.button({ label: AC.t('common.view'), icon: 'kbd', small: true, kind: 'ghost', onClick: function () { AC.palette.openKeys(); } })));
    dl.appendChild(row(AC.t('settings.reset'), AC.t('settings.resetHelp'),
      ui.confirmClick(ui.button({ label: AC.t('common.reset'), icon: 'undo', small: true, kind: 'ghost-danger' }), function () { S.reset(); AC.i18n.set(S.get('uiLang')); sync(); ui.toast(AC.t('settings.resetDone'), 'undo'); })));
    data.add(dl);
    el.appendChild(data.el);

    /* Developer */
    var dev = ui.advanced({ title: AC.t('group.dev'), key: 'settings.dev' });
    ctl.dev = ui.switch({ label: AC.t('settings.devMode'), help: AC.t('settings.devModeHelp'), checked: S.get('developer'), onChange: function (v) { S.set('developer', v); els.devBtns.hidden = !v; } });
    dev.add(ctl.dev);
    els.devBtns = ui.h('div', { class: 'btn-row', hidden: !S.get('developer') }, [
      ui.button({ label: AC.t('settings.openEcho'), icon: 'bug', small: true, onClick: function () { AC.router.go('tool/echo'); } }),
      ui.button({ label: AC.t('settings.reloadHost'), icon: 'refresh', small: true, kind: 'ghost', onClick: function () {
        AC.host.load().then(function (r) { ui.toast(r.errors.length ? AC.t('settings.hostErr', { n: r.errors.length }) : AC.t('settings.hostOk', { n: r.files.length }), r.errors.length ? { kind: 'err' } : 'check'); paintInfo(); });
      } }),
      ui.button({ label: AC.t('settings.copyLog'), icon: 'copy', small: true, kind: 'ghost', onClick: function () { ui.copy(AC.log.text(), AC.t('settings.panelLog')); } })
    ]);
    dev.add(els.devBtns);
    els.info = ui.h('div', { class: 'kv' });
    dev.add(els.info);
    el.appendChild(dev.el);
    paintPexels();
  }

  function paintPexels() {
    var has = S.hasSecret('pexelsKey');
    els.pexState.innerHTML = has ? ui.icon('lock') + U.esc(AC.t('settings.saved')) : U.esc(AC.t('settings.notSet'));
    els.pexDel.hidden = !has;
  }
  function paintInfo() {
    var kv = [['settings.kvVersion', AC.version], ['settings.kvExt', AC.sys.paths.ext], ['settings.kvEngine', AC.sys.paths.engine], ['app.settings', S.path()],
              ['settings.kvSync', AC.t(AC.seq.mode === 'events' ? 'settings.syncEvents' : AC.seq.mode === 'poll' ? 'settings.syncPoll' : 'settings.syncOff')],
              ['settings.kvHost', AC.host.files.join(', ') + (AC.host.loadErrors.length ? ' ' + AC.t('settings.kvHostErr', { n: AC.host.loadErrors.length }) : '')]];
    // Values are paths / file names (data): data-i18n-skip.
    els.info.innerHTML = kv.map(function (r) { return '<span>' + U.esc(AC.t(r[0])) + '</span><b data-i18n-skip>' + U.esc(r[1]) + '</b>'; }).join('');
  }
  function sync() {
    ctl.python.set(S.get('python')); ctl.work.set(S.workRoot()); ctl.out.set(S.get('outputDir')); ctl.model.set(S.get('whisperModel'), true); ctl.lang.set(S.get('lang'), true);
    ctl.gpu.set(S.get('gpu'), true); ctl.ai.set(S.get('ai'), true); ctl.tpl.set(S.get('captionTemplate'), true);
    ctl.density.set(S.get('density'), true); ctl.uiLang.set(AC.i18n.setting(), true); ctl.follow.set(S.get('followTheme'), true); ctl.motion.set(S.get('reduceMotion'), true);
    ctl.dev.set(S.get('developer'), true); els.devBtns.hidden = !S.get('developer');
    paintPexels(); paintInfo(); paintHealth(AC.engine.lastHealth);
  }

  AC.router.page('settings', {
    title: function () { return AC.t('settings.title'); }, back: 'home',
    render: function (el) { build(el); AC.bus.on('health', paintHealth); },
    onShow: function () {
      sync();
      if (AC.aiSettings) AC.aiSettings.refresh();
      if (!AC.engine.lastHealth && AC.engine.available()) AC.engine.health();
    }
  });
})();
