/* ai_settings.js: Pengaturan > AI > Penyedia AI (docs/AI_PROVIDERS.md).
   Profiles live in the engine (`cli.py ai ...`, %APPDATA%\Klipora\ai_profiles.json, no secrets). A key is typed into a
   password field, read once, the field is cleared at once, and the value is written only to the engine's stdin
   (`ai set-key <id>`, encrypted there with Windows DPAPI). The panel only ever sees has_key true/false. Never log,
   store or render a key.
   Texts: locale keys ai.* (Indonesian + English). mount() builds a fresh section every time it is called (the
   Settings page re-renders it after a language switch); module state (profiles, open wizard) survives.
   AC.aiSettings: mount() -> element (settings page), refresh(probe) -> Promise<snapshot>, cli(args, stdinText, ms) ->
   Promise<data>, idle() -> Promise (queued commands done), state() (tests). */
(function () {
  'use strict';
  var AC = window.AC, U = AC.util, ui = AC.ui;
  var A = AC.aiSettings = {};
  var st = { data: null, live: {}, open: '', wiz: null, err: null, msg: {} };
  var root = null, els = {}, chain = Promise.resolve(), iconsAdded = false;
  var SLOT_ID = { fast: 'aiwCepat', smart: 'aiwPintar', vision: 'aiwVision' };   // element ids (tests), language independent
  function T(k, v) { return AC.t(k, v); }
  function slotLabel(slot) { return T('ai.slot.' + slot); }

  /* ---------------- engine bridge: `python -X utf8 engine/cli.py ai <args>` (one result or error event) */
  A.cli = function (args, stdinText, timeoutMs) {
    return new Promise(function (resolve, reject) {
      if (!AC.engine.available()) { reject(U.err('NO_ENGINE', T('ai.errNoEngine'), T('ai.errNoEngineHint'))); return; }
      var proc, result = null, error = null, errTail = '', done = false;
      try {
        proc = AC.sys.spawn(AC.engine.python(), ['-X', 'utf8', AC.sys.paths.cli, 'ai'].concat(args),
          { cwd: AC.sys.paths.engine, env: AC.sys.childEnv() });
      } catch (e) { reject(U.err('PYTHON', T('ai.errPython'), U.errMsg(e))); return; }
      var timer = setTimeout(function () { if (!done) { done = true; AC.sys.killTree(proc.pid); reject(U.err('TIMEOUT', T('ai.errTimeout'), T('ai.errTimeoutHint'))); } }, timeoutMs || 60000);
      var reader = new AC.engine.LineReader(function (line) {
        var ev = null;
        try { ev = JSON.parse(line); } catch (e) { return; }
        if (ev && ev.ev === 'result') result = ev.data;
        else if (ev && ev.ev === 'error') error = ev;
      });
      proc.stdout.on('data', function (d) { reader.push(d); });
      proc.stderr.on('data', function (d) { errTail = (errTail + String(d)).slice(-1500); });
      proc.on('error', function (e) {
        if (done) return; done = true; clearTimeout(timer);
        reject(e && e.code === 'ENOENT' ? U.err('PYTHON', T('ai.errPythonShort'), U.errMsg(e)) : U.err('SPAWN', U.errMsg(e)));
      });
      proc.on('close', function (code) {
        if (done) return; done = true; clearTimeout(timer); reader.flush();
        if (error) reject(U.err(error.code || 'ENGINE', error.msg || T('ai.errEngine'), error.hint || ''));
        else if (result) resolve(result);
        else reject(U.err('EXIT', T('ai.errExit', { code: String(code) }), errTail.slice(-500)));
      });
      // stdin is always closed: `save` / `set-key` read it to EOF, the rest ignore it.
      try { if (stdinText) proc.stdin.write(stdinText); proc.stdin.end(); } catch (e) { /* process already gone */ }
    });
  };
  // Serialize writes (the profiles file is rewritten by every command).
  function op(fn) { var p = chain.then(fn); chain = p.then(function () {}, function () {}); return p; }
  A.idle = function () { return chain; };   // resolves when every queued engine command has finished

  /* ---------------- helpers */
  function ms(v) { return typeof v === 'number' ? (v < 1000 ? v + ' ms' : U.dec(v / 1000, 1) + ' ' + T('unit.sec')) : ''; }
  function ctxLabel(n) { return !n ? '' : n >= 1e6 ? U.dec(n / 1e6, n % 1e6 ? 1 : 0) + 'M' : Math.round(n / 1000) + 'K'; }
  function presets() { return (st.data && st.data.presets) || []; }
  function preset(kind) { var l = presets(); for (var i = 0; i < l.length; i++) if (l[i].kind === kind) return l[i]; return null; }
  function profiles() { return (st.data && st.data.profiles) || []; }
  function prof(id) { var l = profiles(); for (var i = 0; i < l.length; i++) if (l[i].id === id) return l[i]; return null; }
  function ordered() {
    var d = st.data; if (!d) return [];
    return d.order.map(prof).filter(function (p) { return p && !p.draft; });
  }
  function health(p) {
    var h = p.health || null, l = st.live[p.id];
    if (l && (!h || !h.at || l.at > h.at * 1000)) return l;
    return h;
  }
  function status(p) {
    if (!p.enabled) return { state: 'off', text: T('ai.st.off') };
    if (p.key_error && !p.has_key) return { state: 'warn', text: T('ai.st.keyBroken') };
    if (p.needs_key && !p.has_key) return { state: 'warn', text: T('ai.st.noKey') };
    var h = health(p);
    if (!h) return { state: 'unk', text: T('common.notChecked') };
    if (h.ok) return { state: 'ok', text: typeof h.ms === 'number' ? T('ai.st.okMs', { ms: ms(h.ms) }) : T('ai.st.ok') };
    return { state: 'err', text: h.why ? T('ai.st.failed', { why: h.why }) : T('ai.st.offline') };
  }
  function showErr(e, where) {
    AC.log.warn('AI ' + (where || '') + ': ' + U.errMsg(e));
    ui.toast(U.errMsg(e) + (e && e.hint ? ' ' + e.hint : ''), { kind: 'err' });
  }

  /* ---------------- list */
  A.refresh = function (probe) {
    if (!root) return Promise.resolve(null);
    if (!AC.engine.available()) { st.err = U.err('NO_ENGINE', T('ai.errNoEngine')); render(); return Promise.resolve(null); }
    if (probe) els.list.classList.add('is-busy');
    return op(function () { return A.cli(probe ? ['profiles', '--probe'] : ['profiles'], '', probe ? 60000 : 30000); })
      .then(function (d) { st.data = d; st.err = null; render(); return d; },
            function (e) { st.err = e; render(); return null; })
      .then(function (d) { els.list.classList.remove('is-busy'); return d; });
  };
  A.state = function () { return { data: st.data, wiz: st.wiz ? { id: st.wiz.id, kind: st.wiz.kind, hasKey: st.wiz.hasKey, models: st.wiz.models ? st.wiz.models.length : null } : null, open: st.open }; };

  function render() {
    if (!root) return;
    els.list.innerHTML = '';
    if (!st.data) {
      els.list.appendChild(st.err ? ui.alert({ kind: 'warn', title: T('ai.listErr'), text: U.errMsg(st.err), log: st.err.hint || '' })
                                  : ui.h('p', { class: 'help', text: T('ai.loading') }));
      els.chain.textContent = '';
      return;
    }
    var rows = ordered(), route = st.data.route || [];
    rows.forEach(function (p, i) { els.list.appendChild(rowEl(p, i, rows.length, route.indexOf(p.id))); });
    if (!rows.length) els.list.appendChild(ui.h('p', { class: 'help', text: T('ai.empty') }));
    var names = route.map(function (id) { var p = prof(id); return p ? p.name : id; });
    els.chain.textContent = st.data.disabled ? T('ai.chainOff')
      : (names.length ? T('ai.chain', { names: names.join(T('ai.chainSep')) }) : T('ai.chainNone'));
    els.grokNote.hidden = !profiles().some(function (p) { return p.kind === 'grok_local' && p.enabled && !p.draft; });
  }

  function rowEl(p, idx, n, routeIdx) {
    var s = status(p), open = st.open === p.id;
    var role = !p.enabled ? [T('ai.role.off'), 'line'] : routeIdx === 0 ? [T('ai.role.main'), 'ai'] : [T('ai.role.backup', { n: routeIdx }), 'line'];
    var row = ui.h('div', { class: 'aip-row' + (open ? ' is-open' : '') + (p.enabled ? '' : ' is-off'), dataset: { id: p.id } });
    var main = ui.h('button', { type: 'button', class: 'aip-main', 'aria-expanded': open ? 'true' : 'false', title: s.text,
      on: { click: function () { st.open = open ? '' : p.id; render(); } } });
    main.appendChild(ui.h('span', { class: 'aip-dot is-' + s.state, 'aria-hidden': 'true' }));
    var txt = ui.h('span', { class: 'aip-txt' }, [ui.h('b', { text: p.name, 'data-i18n-skip': '' }), ui.h('small', { text: s.text + (p.model_fast ? ', ' + p.model_fast : '') })]);
    main.appendChild(txt);
    main.appendChild(ui.html(ui.tag(role[0], role[1])));
    var up = ui.h('button', { type: 'button', class: 'ibtn aip-mv', 'aria-label': T('ai.moveUp', { name: p.name }), title: T('ai.moveUpTitle'), disabled: idx === 0, html: ui.icon('chev-u'),
      on: { click: function () { move(p.id, -1); } } });
    var down = ui.h('button', { type: 'button', class: 'ibtn aip-mv', 'aria-label': T('ai.moveDown', { name: p.name }), title: T('ai.moveDownTitle'), disabled: idx === n - 1, html: ui.icon('chev-d'),
      on: { click: function () { move(p.id, 1); } } });
    row.appendChild(ui.h('div', { class: 'aip-head' }, [main, up, down]));
    if (open) row.appendChild(detailEl(p, routeIdx));
    return row;
  }

  function keyText(p) {
    if (p.key_error && !p.has_key) return T('ai.key.broken');
    if (!p.needs_key && !p.has_key) return p.kind === 'ollama' ? T('ai.key.notNeeded') : T('ai.key.none');
    if (p.key_from_env) return T('ai.key.env');
    return p.has_key ? T('ai.key.saved') : T('ai.key.missing');
  }

  function detailEl(p, routeIdx) {
    var box = ui.h('div', { class: 'aip-detail' });
    var h = health(p) || {}, pre = preset(p.kind);
    var kv = [[T('ai.kv.kind'), pre ? pre.name : p.kind],
              [T('ai.kv.url'), p.base_url || (p.base_url_effective ? T('ai.kv.fromEnv', { url: p.base_url_effective }) : '')],
              [T('ai.kv.key'), keyText(p)],
              [T('ai.kv.fast'), p.model_fast || T('ai.kv.notChosen')], [T('ai.kv.smart'), p.model_smart || T('ai.kv.notChosen')],
              [T('ai.kv.vision'), p.vision_model || T('ai.kv.none')]];
    if (h.tested) kv.push([T('ai.kv.lastTest'), (h.ok ? T('ai.kv.ok') : T('ai.kv.fail')) + (typeof h.ms === 'number' ? ', ' + ms(h.ms) : '') +
                            (h.ok && h.format ? (h.format === 'response_format' ? T('ai.kv.jsonMode') : T('ai.kv.jsonPrompt')) : '') +
                            (h.vision === true ? T('ai.kv.visionOk') : h.vision === false ? T('ai.kv.visionFail') : '')]);
    if (h.why && !h.ok) kv.push([T('ai.kv.why'), h.why]);
    box.appendChild(ui.h('div', { class: 'kv aip-kv', html: kv.map(function (r) { return '<span>' + U.esc(r[0]) + '</span><b>' + U.esc(r[1]) + '</b>'; }).join('') }));
    box.appendChild(ui.switch({ label: T('ai.use'), help: p.enabled ? T('ai.useOn') : T('ai.useOff'), checked: p.enabled,
      onChange: function (v) { save({ id: p.id, enabled: v }, T(v ? 'ai.toastOn' : 'ai.toastOff', { name: p.name })); } }).el);
    var btns = ui.h('div', { class: 'btn-row' });
    btns.appendChild(ui.button({ label: T('ai.test'), icon: 'refresh', small: true, onClick: function (e) { testRow(p, e.currentTarget); } }));
    if (routeIdx !== 0) btns.appendChild(ui.button({ label: T('ai.makeMain'), icon: 'check', small: true, kind: 'ghost', onClick: function () { activate(p); } }));
    btns.appendChild(ui.button({ label: T('common.edit'), icon: 'sliders', small: true, kind: 'ghost', onClick: function () { openWizard(p); } }));
    if (p.has_key && !p.key_from_env) btns.appendChild(ui.confirmClick(ui.button({ label: T('ai.delKey'), icon: 'lock', small: true, kind: 'ghost-danger' }), function () {
      op(function () { return A.cli(['delete-key', p.id]); }).then(function () { ui.toast(T('ai.keyDeleted', { name: p.name }), 'trash'); A.refresh(); }, function (e) { showErr(e, 'delete key'); });
    }, T('common.confirm')));
    btns.appendChild(ui.confirmClick(ui.button({ label: T('common.delete'), icon: 'trash', small: true, kind: 'ghost-danger' }), function () {
      op(function () { return A.cli(['remove', p.id]); }).then(function () { st.open = ''; ui.toast(T('ai.deleted', { name: p.name }), 'trash'); A.refresh(); }, function (e) { showErr(e, 'delete'); });
    }, T('common.confirm')));
    box.appendChild(btns);
    if (st.msg[p.id]) box.appendChild(st.msg[p.id]);
    return box;
  }

  function save(patch, okMsg) {
    return op(function () { return A.cli(['save'], JSON.stringify(patch)); })
      .then(function () { if (okMsg) ui.toast(okMsg, 'check'); return A.refresh(); }, function (e) { showErr(e, 'save'); A.refresh(); });
  }
  function move(id, dir) {
    var ids = ordered().map(function (p) { return p.id; }), i = ids.indexOf(id), j = i + dir;
    if (i < 0 || j < 0 || j >= ids.length) return;
    ids[i] = ids[j]; ids[j] = id;
    st.data.order = ids.concat(st.data.order.filter(function (x) { return ids.indexOf(x) < 0; }));
    render();                                     // optimistic, the engine answer re-renders
    op(function () { return A.cli(['order'], JSON.stringify(ids)); }).then(function () { return A.refresh(); }, function (e) { showErr(e, 'order'); A.refresh(); });
  }
  function activate(p) {
    op(function () { return A.cli(['activate', p.id]); })
      .then(function () { ui.toast(T('ai.madeMain', { name: p.name }), 'check'); return A.refresh(); }, function (e) { showErr(e, 'activate'); });
  }
  function testResult(r) {
    var ok = r && r.ok, c = (r && r.chat) || {}, v = r && r.vision, fmt = c.format === 'response_format';
    var text = ok ? T('ai.tr.connected', { ms: ms(c.ms) }) + ' ' + T(c.right ? (fmt ? 'ai.tr.jsonRightMode' : 'ai.tr.jsonRight') : (fmt ? 'ai.tr.jsonReadMode' : 'ai.tr.jsonRead'))
                  : (r && r.why) || T('ai.tr.offline');
    if (v) text += ' ' + (v.ok ? T('ai.tr.visionOk') : v.why ? T('ai.tr.visionFailWhy', { why: v.why }) : T('ai.tr.visionFail'));
    return ui.alert({ kind: ok ? 'info' : 'warn', icon: ok ? 'check' : 'alert', title: ok ? T('ai.tr.okTitle') : T('ai.tr.failTitle'), text: text });
  }
  function testRow(p, btn) {
    if (btn) btn.disabled = true;
    st.msg[p.id] = ui.h('p', { class: 'help', text: T('ai.testing', { name: p.name }) });
    render();
    op(function () { return A.cli(['test', p.id].concat(p.vision_model ? ['--vision'] : []), '', 240000); })
      .then(function (r) { st.msg[p.id] = testResult(r); ui.toast(T(r.ok ? 'ai.toastConnected' : 'ai.toastFailed', { name: p.name }), r.ok ? 'check' : { kind: 'err' }); return A.refresh(); },
            function (e) { st.msg[p.id] = ui.alert({ kind: 'warn', title: T('ai.testFailed'), text: U.errMsg(e), log: e.hint || '' }); render(); });
  }

  /* ---------------- wizard: preset -> URL + key -> models -> test -> save */
  function openWizard(p) {
    var editing = !!p;
    st.wiz = { editing: editing, id: editing ? p.id : null, kind: editing ? p.kind : '', orig: editing ? U.copy(p) : null,
               hasKey: editing ? !!p.has_key : false, keyFromEnv: editing && !!p.key_from_env, models: null, unsupported: false,
               slot: 'fast', q: '', f: {}, testRes: null };
    renderWizard();
    els.add.hidden = true;
    setTimeout(function () { if (els.wiz.scrollIntoView) els.wiz.scrollIntoView({ block: 'nearest' }); }, 0);
  }
  function closeWizard() { st.wiz = null; els.wiz.innerHTML = ''; els.wiz.hidden = true; els.add.hidden = false; }
  function step(n, key) { return ui.h('div', { class: 'aip-step', text: n + '. ' + T(key) }); }

  function renderWizard() {
    var w = st.wiz; if (!w) return;
    var f = w.f, keep = {};
    ['name', 'url', 'fast', 'smart', 'vision'].forEach(function (k) { if (f[k]) keep[k] = f[k].get(); });
    if (f.json) keep.json = f.json.get();
    els.wiz.innerHTML = ''; els.wiz.hidden = false;
    var card = ui.h('div', { class: 'card aip-wiz', id: 'aiWiz' });
    card.appendChild(ui.h('div', { class: 'aip-wiz-h' }, [ui.h('b', { text: w.editing ? T('ai.wiz.editTitle', { name: w.orig.name }) : T('ai.wiz.addTitle') }),
      ui.h('button', { type: 'button', class: 'ibtn', 'aria-label': T('common.close'), html: ui.icon('x'), on: { click: cancelWizard } })]));
    // 1. preset
    if (!w.editing) {
      card.appendChild(step(1, 'ai.wiz.stepPick'));
      var grid = ui.h('div', { class: 'aip-presets', role: 'radiogroup', 'aria-label': T('ai.wiz.providers') });
      var hasGrok = ((st.data && st.data.profiles) || []).some(function (p) { return p.kind === 'grok_local' && !p.draft; });
      presets().forEach(function (pr) {
        if (pr.kind === 'grok_local' && hasGrok && w.kind !== 'grok_local') return;   // only one local Grok profile (it may use the .env key)
        grid.appendChild(ui.h('button', { type: 'button', class: 'chip aip-preset', role: 'radio', dataset: { kind: pr.kind }, 'aria-checked': w.kind === pr.kind ? 'true' : 'false',
          'aria-pressed': w.kind === pr.kind ? 'true' : 'false', text: pr.name, on: { click: function () { pickPreset(pr.kind); } } }));
      });
      card.appendChild(grid);
    }
    var pre = preset(w.kind);
    if (!pre) { els.wiz.appendChild(card); return; }
    if (pre.note) card.appendChild(ui.h('p', { class: 'help aip-note', text: pre.note }));
    if (pre.paid) card.appendChild(ui.h('p', { class: 'help aip-note', text: T('ai.wiz.paid') }));
    // 2. connection
    card.appendChild(step(w.editing ? 1 : 2, 'ai.wiz.stepConnect'));
    f.name = ui.input({ value: keep.name !== undefined ? keep.name : (w.editing ? w.orig.name : pre.name), ariaLabel: T('ai.wiz.nameAria') });
    f.name.el.id = 'aiwName'; f.name.el.maxLength = 40;
    card.appendChild(ui.field({ label: T('ai.wiz.name'), control: f.name.el }));
    f.url = ui.input({ value: keep.url !== undefined ? keep.url : (w.editing ? w.orig.base_url : (w.kind === 'grok_local' ? '' : pre.base_url)), mono: true, ariaLabel: T('ai.wiz.url'),
      placeholder: w.kind === 'grok_local' ? T('ai.wiz.urlPhGrok') : 'https://.../v1' });
    f.url.el.id = 'aiwUrl';
    card.appendChild(ui.field({ label: T('ai.wiz.url'), control: f.url.el, help: w.kind === 'custom' ? T('ai.wiz.urlHelpCustom') : '' }));
    var needKey = pre.needs_key || w.kind === 'custom';
    if (w.kind !== 'ollama') {
      f.key = ui.input({ type: 'password', ariaLabel: T('ai.wiz.keyAria'), placeholder: w.hasKey ? T('ai.wiz.keyPhSaved') : (w.kind === 'grok_local' ? T('ai.wiz.keyPhGrok') : T('ai.wiz.keyPh')) });
      f.key.el.id = 'aiwKey'; f.key.el.setAttribute('autocomplete', 'new-password');
      var keyBtn = ui.button({ label: T('ai.wiz.saveKey'), small: true, id: 'aiwKeySave', onClick: function () {
        op(function () { return saveKey(); }).then(function (did) { if (did) ui.toast(T('ai.wiz.keySavedToast'), 'lock'); }, function (e) { showErr(e, 'key'); });
      } });
      card.appendChild(ui.field({ label: needKey && pre.needs_key ? T('ai.wiz.keyLabel') : T('ai.wiz.keyLabelOpt'), control: ui.h('div', { class: 'input-row' }, [f.key.el, keyBtn]) }));
      els.keyState = ui.h('small', { class: 'help aip-keystate' });
      card.appendChild(els.keyState);
      paintKeyState();
    } else {
      f.key = null;
      card.appendChild(ui.h('p', { class: 'help', text: T('ai.wiz.ollamaNoKey') }));
    }
    // 3. models
    card.appendChild(step(w.editing ? 2 : 3, 'ai.wiz.stepModels'));
    var mrow = ui.h('div', { class: 'aip-slots' });
    [['fast', 'model_fast'], ['smart', 'model_smart'], ['vision', 'vision_model']].forEach(function (s) {
      var slot = s[0], label = slotLabel(slot);
      var val = keep[slot] !== undefined ? keep[slot] : (w.editing ? w.orig[s[1]] : pre[s[1]]) || '';
      f[slot] = ui.input({ value: val, mono: true, ariaLabel: T('ai.wiz.modelAria', { slot: label }), placeholder: slot === 'vision' ? T('ai.wiz.visionPh') : T('ai.wiz.modelPh') });
      f[slot].el.id = SLOT_ID[slot];
      f[slot].el.addEventListener('focus', function () { if (w.slot !== slot) { w.slot = slot; paintSlot(); } });
      var lab = ui.h('button', { type: 'button', class: 'aip-slot' + (w.slot === slot ? ' is-on' : ''), dataset: { slot: slot }, 'aria-pressed': w.slot === slot ? 'true' : 'false',
        title: T('ai.wiz.slotTitle', { slot: label }), html: '<b>' + U.esc(label) + '</b><small>' + U.esc(T('ai.slot.' + slot + 'Help')) + '</small>',
        on: { click: function () { w.slot = slot; paintSlot(); } } });
      mrow.appendChild(ui.h('div', { class: 'aip-slotrow' }, [lab, f[slot].el]));
    });
    card.appendChild(mrow);
    card.appendChild(ui.h('div', { class: 'btn-row' }, [ui.button({ label: T('ai.wiz.fetchModels'), icon: 'refresh', small: true, id: 'aiwModels', onClick: fetchModels })]));
    els.mbox = ui.h('div', { class: 'aip-mbox' });
    card.appendChild(els.mbox);
    renderModels();
    // 4. test + save
    card.appendChild(step(w.editing ? 3 : 4, 'ai.wiz.stepTest'));
    var adv = ui.advanced({ title: T('common.advanced'), key: 'ai.wiz.adv' });
    f.json = ui.select({ options: [['auto', T('common.auto')], ['on', T('ai.wiz.jsonOn')], ['off', T('ai.wiz.jsonOff')]], ariaLabel: T('ai.wiz.jsonMode'),
      value: keep.json || (w.editing ? w.orig.json_mode : (w.kind === 'grok_local' ? 'off' : 'auto')) });
    adv.add(ui.inlineField({ label: T('ai.wiz.jsonMode'), help: T('ai.wiz.jsonHelp'), control: f.json.el }));
    card.appendChild(adv.el);
    els.testBox = ui.h('div', { class: 'aip-test', 'aria-live': 'polite' });
    if (w.testRes) els.testBox.appendChild(w.testRes);
    card.appendChild(els.testBox);
    card.appendChild(ui.h('div', { class: 'btn-row aip-actions' }, [
      ui.button({ label: T('ai.test'), icon: 'refresh', small: true, id: 'aiwTest', onClick: testWizard }),
      ui.button({ label: T('common.cancel'), small: true, kind: 'ghost', id: 'aiwCancel', onClick: cancelWizard }),
      ui.button({ label: T('common.save'), icon: 'check', small: true, kind: 'primary', id: 'aiwSave', onClick: finishWizard })]));
    els.wiz.appendChild(card);
  }

  function paintKeyState() {
    var w = st.wiz; if (!w || !els.keyState) return;
    var pre = preset(w.kind) || {};
    els.keyState.innerHTML = '';
    if (w.hasKey) els.keyState.innerHTML = ui.icon('lock') + U.esc(w.keyFromEnv ? T('ai.wiz.keyFromEnv') : T('ai.wiz.keyStored'));
    else els.keyState.textContent = pre.key_url ? T('ai.wiz.keyGet', { url: pre.key_url }) : (w.kind === 'grok_local' ? T('ai.wiz.keyEnvHint') : '');
  }
  function paintSlot() {
    var w = st.wiz; if (!w) return;
    U.$$('.aip-slot', els.wiz).forEach(function (b) { var on = b.dataset.slot === w.slot; b.classList.toggle('is-on', on); b.setAttribute('aria-pressed', on ? 'true' : 'false'); });
    renderModels();
  }
  function pickPreset(kind) {
    var w = st.wiz; if (!w || w.editing) return;
    var go = function () { w.kind = kind; w.id = null; w.hasKey = false; w.models = null; w.testRes = null; w.f = {}; renderWizard(); };
    if (w.id) { var old = w.id; op(function () { return A.cli(['remove', old]); }).then(go, go); }   // drop the previous draft and its key
    else go();
  }

  function formProfile() {
    var w = st.wiz, f = w.f, pre = preset(w.kind) || {};
    var o = { kind: w.kind, name: f.name.get().trim() || pre.name, base_url: f.url.get().trim(), model_fast: f.fast.get().trim(),
              model_smart: f.smart.get().trim(), vision_model: f.vision.get().trim(), json_mode: f.json ? f.json.get() : 'auto' };
    if (w.id) o.id = w.id;
    return o;
  }
  // New profiles are drafts (not used by tools) until "Simpan"; edits are written in place.
  function persist(final) {
    var w = st.wiz, body = formProfile();
    if (!w.editing) { body.draft = !final; body.enabled = !!final; }
    return A.cli(['save'], JSON.stringify(body)).then(function (r) { w.id = r.saved; return r; });
  }
  function saveKey() {
    var w = st.wiz, f = w && w.f;
    if (!f || !f.key) return Promise.resolve(false);
    var v = f.key.get().trim();
    f.key.set('');                                  // cleared at once, before anything else happens
    if (!v) return Promise.resolve(false);
    if (v.length < 8 || /\s/.test(v)) return Promise.reject(U.err('AI_KEY', T('ai.wiz.keyShort'), T('ai.wiz.keyShortHint')));
    return (w.id ? Promise.resolve() : persist(false)).then(function () {
      var p = A.cli(['set-key', w.id], v + '\n', 30000);
      v = null;
      return p;
    }).then(function () { w.hasKey = true; w.keyFromEnv = false; w.testRes = null; paintKeyState(); return true; });
  }
  function busy(id, on) { var b = document.getElementById(id); if (b) b.disabled = !!on; }

  function fetchModels() {
    var w = st.wiz; if (!w) return;
    busy('aiwModels', true);
    els.mbox.innerHTML = ''; els.mbox.appendChild(ui.h('p', { class: 'help', text: T('ai.wiz.fetching') }));
    op(function () { return saveKey().then(function () { return persist(false); }).then(function () { return A.cli(['models', w.id], '', 60000); }); })
      .then(function (r) {
        if (st.wiz !== w) return;
        w.models = r.models || []; w.unsupported = !!r.unsupported; w.modelsMs = r.ms;
        renderModels();
      }, function (e) { if (st.wiz === w) { els.mbox.innerHTML = ''; els.mbox.appendChild(ui.alert({ kind: 'warn', title: T('ai.wiz.modelsErr'), text: U.errMsg(e), log: e.hint || '' })); } })
      .then(function () { busy('aiwModels', false); });
  }

  function renderModels() {
    var w = st.wiz; if (!w || !els.mbox) return;
    els.mbox.innerHTML = '';
    if (!w.models) return;
    if (w.unsupported || !w.models.length) {
      els.mbox.appendChild(ui.h('p', { class: 'help', text: w.unsupported ? T('ai.wiz.noModelList') : T('ai.wiz.modelsEmpty') }));
      return;
    }
    var search = ui.input({ value: w.q, placeholder: T('ai.wiz.search'), ariaLabel: T('ai.wiz.search'), onInput: function (v) { w.q = v; paintList(); } });
    search.el.id = 'aiwSearch';
    els.mbox.appendChild(ui.h('div', { class: 'aip-mhead' }, [search.el, ui.h('small', { class: 'aip-mcount' })]));
    els.mlist = ui.h('div', { class: 'aip-mlist', role: 'listbox', 'aria-label': T('ai.wiz.modelList'), 'data-i18n-skip': '' });
    els.mbox.appendChild(els.mlist);
    els.mbox.appendChild(ui.h('small', { class: 'help aip-mtarget' }));
    paintList();
  }
  function paintList() {
    var w = st.wiz; if (!w || !els.mlist) return;
    var q = String(w.q || '').toLowerCase().trim(), cur = w.f[w.slot] ? w.f[w.slot].get().trim() : '';
    var list = w.models.filter(function (m) { return !q || (m.id + ' ' + (m.name || '')).toLowerCase().indexOf(q) >= 0; });
    if (w.slot === 'vision') list = list.filter(function (m) { return m.vision !== false; });
    els.mlist.innerHTML = '';
    list.slice(0, 120).forEach(function (m) {
      var meta = [ctxLabel(m.ctx), m.vision ? T('ai.wiz.tagImage') : '', m.free ? T('ai.wiz.tagFree') : ''].filter(Boolean).join(', ');
      els.mlist.appendChild(ui.h('button', { type: 'button', class: 'aip-model' + (m.id === cur ? ' is-on' : ''), role: 'option', 'aria-selected': m.id === cur ? 'true' : 'false',
        dataset: { id: m.id }, title: m.name || m.id, html: '<span>' + U.esc(m.id) + '</span>' + (meta ? '<small>' + U.esc(meta) + '</small>' : ''),
        on: { click: function () { pickModel(m.id); } } }));
    });
    var c = U.$('.aip-mcount', els.mbox), tg = U.$('.aip-mtarget', els.mbox);
    if (c) c.textContent = T('ai.wiz.count', { shown: list.length, n: w.models.length });
    if (tg) tg.textContent = T('ai.wiz.target', { slot: slotLabel(w.slot) }) + (list.length > 120 ? ' ' + T('ai.wiz.typeToFilter') : '');
  }
  function pickModel(id) {
    var w = st.wiz; if (!w) return;
    w.f[w.slot].set(id);
    w.testRes = null;
    var next = { fast: 'smart', smart: 'vision', vision: 'vision' }[w.slot];
    if (w.slot !== 'vision' && !w.f[next].get().trim()) w.slot = next;
    paintSlot();
  }

  function testWizard() {
    var w = st.wiz; if (!w || !w.kind) return;
    var err = checkForm(false); if (err) { ui.toast(err, { kind: 'err' }); return; }
    busy('aiwTest', true);
    els.testBox.innerHTML = ''; els.testBox.appendChild(ui.h('p', { class: 'help', text: T(w.f.vision.get().trim() ? 'ai.wiz.testingImg' : 'ai.wiz.testing') }));
    op(function () {
      return saveKey().then(function () { return persist(false); })
        .then(function () { return A.cli(['test', w.id].concat(w.f.vision.get().trim() ? ['--vision'] : []), '', 240000); });
    }).then(function (r) {
      if (st.wiz !== w) return;
      w.testRes = testResult(r); els.testBox.innerHTML = ''; els.testBox.appendChild(w.testRes);
    }, function (e) {
      if (st.wiz !== w) return;
      w.testRes = ui.alert({ kind: 'warn', title: T('ai.testFailed'), text: U.errMsg(e), log: e.hint || '' });
      els.testBox.innerHTML = ''; els.testBox.appendChild(w.testRes);
    }).then(function () { busy('aiwTest', false); });
  }
  function checkForm(final) {
    var w = st.wiz, f = w.f, pre = preset(w.kind) || {};
    if (!w.kind) return T('ai.wiz.needKind');
    if (!f.url.get().trim() && w.kind !== 'grok_local') return T('ai.wiz.needUrl');
    if (pre.needs_key && w.kind !== 'grok_local' && !w.hasKey && !(f.key && f.key.get().trim())) return T('ai.wiz.needKey');
    if (final && !f.fast.get().trim() && !f.smart.get().trim()) return T('ai.wiz.needModel');
    return '';
  }
  function finishWizard() {
    var w = st.wiz; if (!w) return;
    var err = checkForm(true); if (err) { ui.toast(err, { kind: 'err' }); return; }
    busy('aiwSave', true);
    op(function () { return saveKey().then(function () { return persist(true); }); })
      .then(function (r) {
        var pr = r.profile || {};
        if (w.hasKey && !w.keyFromEnv && !pr.has_key) {             // the stored key vanished meanwhile (stale draft pruned)
          w.hasKey = false; paintKeyState(); busy('aiwSave', false);
          ui.toast(T('ai.wiz.keyLost'), { kind: 'err' });
          return;
        }
        var name = (r.profile && r.profile.name) || T('ai.wiz.provider');
        (r.warnings || []).forEach(function (x) { AC.log.warn('AI: ' + x); });
        closeWizard();
        st.open = r.saved || '';
        ui.toast(T(w.editing ? 'ai.wiz.updated' : 'ai.wiz.saved', { name: name }) + (r.warnings && r.warnings.length ? '. ' + r.warnings[0] : ''), r.warnings && r.warnings.length ? 'alert' : 'check');
        return A.refresh();
      }, function (e) { busy('aiwSave', false); showErr(e, 'save'); });
  }
  function cancelWizard() {
    var w = st.wiz; if (!w) return;
    if (w.f.key) w.f.key.set('');
    closeWizard();
    if (!w.editing && w.id) op(function () { return A.cli(['remove', w.id]); }).then(function () { A.refresh(); }, function () { A.refresh(); });
    else if (w.editing && w.id) {
      var o = w.orig, back = { id: o.id, name: o.name, base_url: o.base_url, model_fast: o.model_fast, model_smart: o.model_smart, vision_model: o.vision_model, json_mode: o.json_mode };
      op(function () { return A.cli(['save'], JSON.stringify(back)); }).then(function () { A.refresh(); }, function () { A.refresh(); });
    }
  }

  /* ---------------- mount (Pengaturan > AI): a fresh section on every call (Settings re-renders on a language
     switch); the profile list, open rows and an open wizard are kept in `st` and painted again. */
  A.mount = function () {
    if (!iconsAdded) { ui.addIcons('<symbol id="i-chev-u" viewBox="0 0 20 20"><path d="m6 12 4-4 4 4"/></symbol>'); iconsAdded = true; }
    var relang = !!root;
    root = ui.h('div', { class: 'aip', id: 'aiProv' });
    els = {};
    var head = ui.h('div', { class: 'aip-top' }, [ui.h('b', { text: T('ai.title') }),
      ui.button({ label: T('ai.probe'), icon: 'refresh', small: true, kind: 'ghost', id: 'aiProbe', onClick: function () { A.refresh(true); } })]);
    root.appendChild(head);
    els.list = ui.h('div', { class: 'aip-list' });
    root.appendChild(els.list);
    els.chain = ui.h('p', { class: 'help aip-chain' });
    root.appendChild(els.chain);
    els.add = ui.button({ label: T('ai.add'), icon: 'plus', small: true, id: 'aiAdd', onClick: function () { openWizard(null); } });
    root.appendChild(ui.h('div', { class: 'btn-row' }, [els.add]));
    els.wiz = ui.h('div', { class: 'aip-wizwrap', hidden: true });
    root.appendChild(els.wiz);
    root.appendChild(ui.alert({ kind: 'info', icon: 'info', title: T('ai.privacy'), text: T('ai.privacyText') }));
    els.grokNote = ui.alert({ kind: 'warn', title: T('ai.grokRisk'), text: T('ai.grokRiskText') });
    els.grokNote.hidden = true;
    root.appendChild(els.grokNote);
    // `cli.py health` (Status pill / Tes koneksi) also reports every profile in the route: update the dots.
    // Registered during the Settings render, so a re-render drops it (AC.collectListeners); the check below
    // ignores a stale section anyway.
    var mine = root;
    AC.bus.on('health', function (h) {
      if (root !== mine) return;
      var rows = h && h.data && h.data.ai && h.data.ai.route;
      if (!rows || !rows.length) return;
      rows.forEach(function (r) { st.live[r.id] = { ok: !!r.ok, ms: r.ms, why: r.why || '', at: Date.now() }; });
      render();
    });
    st.msg = {};                                    // test result boxes belong to the old section (old language)
    if (st.wiz) { st.wiz.testRes = null; renderWizard(); els.add.hidden = true; }
    render();
    if (relang && st.data) A.refresh();            // engine texts (preset names, notes, reasons) in the new language
    return root;
  };
})();
