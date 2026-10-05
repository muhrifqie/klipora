/* voice.js: Suara Jernih / Clear Voice. Makes the creator's voice pleasant: preset chain (Natural / Podcast / Kuat:
   highpass, AI noise removal, de-esser, compressor, presence EQ), loudness target per platform (two-pass loudnorm,
   true peak -1 dBTP), mouse/keyboard clicks and breaths turned down (review list with A/B listening), music ducked
   while the voice speaks, "Suara Saya" profiles (%APPDATA%\Klipora\voice_profiles.json, last one auto-loaded) and
   "Tiru EQ dari contoh". Flow: settings -> engine analyze (engine/ac/tools/voice.py, review file) -> review ->
   engine apply (renders the processed WAV, plan {kind:'voice'}) -> host (host/43_voice.jsx): clone "<seq> (Klipora)",
   original voice clips disabled on the clone, WAV on track "Klipora Suara" (old "AutoCut Suara" reused on re-runs),
   volume keys on music clips. UI texts: locale keys voice.* (Indonesian + English). Doc: docs/tools/voice.md. */
(function () {
  'use strict';
  var AC = window.AC, U = AC.util, ui = AC.ui;
  var t = AC.t, H = AC.i18n.html;

  var TRACK = AC.brand.track('Suara'), DISABLE_CHUNK = 60, DUCK_CHUNK = 40;   // i18n-ignore (track name: the same in every UI language)
  // Must equal engine/ac/tools/_voice_dsp.py PRESETS (engine/tests/test_voice.py checks it).
  var PRESETS = {"natural": {"hp": 80, "nr": 0.6, "deess": 0.25, "comp": 2.0, "presence": 2.0, "warmth": 0.0, "mud": -1.0, "air": 0.0}, "podcast": {"hp": 90, "nr": 0.8, "deess": 0.4, "comp": 3.0, "presence": 3.0, "warmth": 1.5, "mud": -2.0, "air": 1.5}, "kuat": {"hp": 100, "nr": 1.0, "deess": 0.55, "comp": 4.5, "presence": 4.0, "warmth": 1.0, "mud": -2.5, "air": 2.5}};
  var CHAIN_KEYS = ['hp', 'nr', 'deess', 'comp', 'presence', 'warmth', 'mud', 'air'];
  // Option lists are built at render time (the language can change between renders).
  function presetOpts() {
    return ['natural', 'podcast', 'kuat'].map(function (v) { return { value: v, label: t('voice.preset.' + v), hint: t('voice.preset.' + v + 'Hint'), values: PRESETS[v] }; });
  }
  var PLATFORM_LUFS = { youtube: -14, podcast: -16, tiktok: -14 };
  function platformOpts() {
    return [{ value: 'youtube', label: 'YouTube', lufs: -14 }, { value: 'podcast', label: t('voice.platPodcast'), lufs: -16 },
            { value: 'tiktok', label: 'TikTok/Reels', lufs: -14 }, { value: 'custom', label: t('voice.platCustom') }];
  }
  function denoiseOpts() {
    return [{ value: 'ai', label: t('voice.den.ai') }, { value: 'rnnoise', label: t('voice.den.rnnoise') },
            { value: 'fft', label: t('voice.den.fft') }, { value: 'off', label: t('voice.den.off') }];
  }
  var DEN_SHORT = { ai: 'AI', rnnoise: 'RNNoise', fft: 'FFT' };
  function denLabel(v) { return v === 'off' ? t('voice.den.offShort') : DEN_SHORT[v] || '-'; }
  var PROFILE_KEYS = ['preset', 'platform', 'lufs', 'clicks', 'breaths', 'denoise', 'duck', 'duckDb', 'attack', 'release',
                      'clickDb', 'breathDb', 'eq', 'eqOn', 'eqRef'].concat(CHAIN_KEYS);

  /* ================================================================ profiles (%APPDATA%\Klipora\voice_profiles.json) */
  var P = AC.voice = {};
  P.path = function () { return AC.sys.join(AC.sys.paths.appData, 'voice_profiles.json'); };
  P.norm = function (lib) {
    lib = lib && typeof lib === 'object' ? lib : {};
    var list = (Array.isArray(lib.profiles) ? lib.profiles : []).filter(function (p) { return p && typeof p.name === 'string' && p.name.trim() && p.settings && typeof p.settings === 'object'; })
      .map(function (p) { return { name: p.name.trim().slice(0, 40), settings: p.settings, saved: p.saved || null }; });
    var last = typeof lib.last === 'string' && list.some(function (p) { return p.name === lib.last; }) ? lib.last : '';
    return { v: 1, last: last, profiles: list.slice(0, 50) };
  };
  P.load = function () { return P.norm(AC.sys.readJSON(P.path(), null)); };
  P.save = function (lib) { lib = P.norm(lib); AC.sys.writeJSON(P.path(), lib, true); return lib; };
  P.find = function (lib, name) { for (var i = 0; i < lib.profiles.length; i++) if (lib.profiles[i].name === name) return lib.profiles[i]; return null; };
  P.pick = function (st) { var o = {}; PROFILE_KEYS.forEach(function (k) { if (st[k] !== undefined) o[k] = U.copy(st[k]); }); return o; };
  P.put = function (name, st) {
    var lib = P.load(), p = P.find(lib, name), s = P.pick(st);
    if (p) { p.settings = s; p.saved = new Date().toISOString(); } else lib.profiles.push({ name: name, settings: s, saved: new Date().toISOString() });
    lib.last = name;
    return P.save(lib);
  };
  P.remove = function (name) {
    var lib = P.load();
    lib.profiles = lib.profiles.filter(function (p) { return p.name !== name; });
    if (lib.last === name) lib.last = '';
    return P.save(lib);
  };
  P.use = function (name) { var lib = P.load(); lib.last = P.find(lib, name) ? name : ''; return P.save(lib); };

  function fileUrl(p) { return 'file:///' + encodeURI(String(p).replace(/\\/g, '/')).replace(/#/g, '%23'); }
  function lufs(v) { return v === null || v === undefined ? '-' : U.dec(v, 1); }
  // Distance between speech level and pause noise floor (dB): bigger = cleaner. Absolute noise numbers rise with the
  // loudness gain, so the result card shows this instead.
  function snr(m) { return m && typeof m.speech_db === 'number' && typeof m.noise_db === 'number' ? U.dec(m.speech_db - m.noise_db, 0) : '-'; }
  function fnum(v, d) { return v === null || v === undefined ? '-' : U.dec(v, d === undefined ? 1 : d); }
  function trackList(idx, sepKey) { return idx.map(function (i) { return 'A' + (i + 1); }).join(sepKey ? t(sepKey) : ', '); }

  /* ================================================================ A/B player (one shared <audio>) */
  var player = { audio: null, btn: null };
  function stopAudio() {
    if (player.audio) { try { player.audio.pause(); } catch (e) { /* gone */ } }
    if (player.btn) { player.btn.classList.remove('is-playing'); player.btn.setAttribute('aria-pressed', 'false'); }
    player.audio = null; player.btn = null;
  }
  function playFile(path, btn, label) {
    if (player.btn === btn && player.audio) { stopAudio(); return; }
    stopAudio();
    try {
      var a = new Audio(fileUrl(path));
      player.audio = a; player.btn = btn;
      if (btn) { btn.classList.add('is-playing'); btn.setAttribute('aria-pressed', 'true'); }
      a.addEventListener('ended', function () { if (player.audio === a) stopAudio(); });
      var pr = a.play();
      if (pr && pr.catch) pr.catch(function (e) { if (player.audio === a) stopAudio(); ui.toast(t('voice.cantPlay', { msg: U.errMsg(e) }), { kind: 'err' }); });
      if (label) ui.toast(t('voice.playing', { what: label }), 'play');
    } catch (e) { stopAudio(); ui.toast(t('voice.cantPlay', { msg: U.errMsg(e) }), { kind: 'err' }); }
  }
  // Two buttons "Sebelum" / "Sesudah" for a pair of WAVs.
  function abRow(before, after, note) {
    var b1 = ui.button({ label: t('voice.before'), icon: 'play', small: true, kind: 'ghost' });
    var b2 = ui.button({ label: t('voice.after'), icon: 'play', small: true });
    b1.classList.add('voice-ab-btn'); b2.classList.add('voice-ab-btn');
    b1.setAttribute('aria-pressed', 'false'); b2.setAttribute('aria-pressed', 'false');
    b1.addEventListener('click', function () { playFile(before, b1, t('voice.playBefore')); });
    b2.addEventListener('click', function () { playFile(after, b2, t('voice.playAfter')); });
    var row = ui.h('div', { class: 'voice-ab' }, [b1, b2]);
    if (note) row.appendChild(ui.h('small', { class: 'help', text: note }));
    return row;
  }

  function rowTags(it) {
    var k = it.kind === 'breath' ? ['voice.breath', 'line'] : ['voice.click', 'warn'];
    var h = '<span class="tag tag-' + k[1] + '">' + H(k[0]) + '</span>';
    if (typeof it.conf === 'number') {
      var p = Math.round(it.conf * 100), kind = it.conf >= 0.8 ? 'ok' : it.conf >= 0.6 ? 'line' : 'warn';
      h += '<span class="tag tag-' + kind + ' conf" title="' + H('voice.confTip', { p: p }) + '">' + p + '%</span>';
    }
    return h;
  }

  AC.tools.register({
    id: 'voice', group: 'potong', order: 5,
    icon: 'mic', badges: ['ai', 'baru'],
    text: function (t) {
      return { title: t('voice.title'), tab: t('voice.tab'), desc: t('voice.desc'), lead: t('voice.lead'), cta: t('voice.cta'),
        review: { unit: t('voice.unit'), verbOn: t('voice.verbOn'), verbOff: t('voice.verbOff') },
        next: [{ tool: 'silence', reason: t('voice.nextSilence') }, { tool: 'captions', reason: t('voice.nextCaptions') }] };
    },
    defaults: { preset: 'natural', base: 'natural', platform: 'youtube', lufs: -14, clicks: true, breaths: true, denoise: 'ai',
                hp: 80, nr: 0.6, deess: 0.25, comp: 2, presence: 2, warmth: 0, mud: -1, air: 0,
                clickDb: -30, breathDb: -12, duck: true, duckDb: -12, attack: 0.25, release: 0.6,
                voiceTrack: 'auto', duckTracks: null, eq: [], eqOn: false, eqRef: '', profile: '' },

    render: function (el, ctx) {
      var st = ctx.state, info = null, reviewFile = null;
      stopAudio();
      // auto-load the last used profile
      var lib = P.load(), lastP = lib.last ? P.find(lib, lib.last) : null;
      if (lastP) { U.assign(st, lastP.settings); st.profile = lastP.name; }
      el.appendChild(ui.h('div', { class: 'tool-hero', html: '<span class="tool-ic">' + ui.icon('mic') + '</span><div><p>' + U.esc(ctx.def.lead) + '</p><div class="tags">' + ui.badge('ai') + ui.tag(t('voice.heroSafe'), 'ok', 'shield') + '<span class="voice-tracktag"></span></div></div>' }));
      var trackTag = el.querySelector('.voice-tracktag');

      /* ---------------- style preset */
      var sp = ui.section({ title: t('voice.styleTitle') });
      var presets = ui.presets({ options: presetOpts(), value: st.preset, ariaLabel: t('voice.styleTitle'), customHint: t('voice.customHint'),
        onChange: function (v) { st.preset = v; st.base = v; U.assign(st, PRESETS[v]); ctx.save(); syncSliders(); markDirty(); } });
      sp.add(presets);
      el.appendChild(sp.el);

      /* ---------------- loudness target */
      var lt = ui.section({ title: t('voice.targetTitle'), aside: '' });
      var plat = ui.segmented({ options: platformOpts(), value: st.platform, small: true, ariaLabel: t('voice.platAria'), onChange: function (v) {
        st.platform = v; var l = PLATFORM_LUFS[v];
        if (l) { st.lufs = l; lufsSl.set(l, true); }
        ctx.save(); paintTarget(); markDirty();
      } });
      plat.el.classList.add('voice-plat');
      var lufsSl = ui.slider({ label: t('voice.loudness'), min: -24, max: -9, step: 1, value: st.lufs, unit: ' LUFS', decimals: 0, help: t('voice.loudnessHelp'),
        onInput: function (v) { st.lufs = v; st.platform = 'custom'; plat.set('custom', true); ctx.save(); paintTarget(); markDirty(); } });
      lt.add([plat.el, lufsSl.el]);
      el.appendChild(lt.el);
      function paintTarget() { lt.setAside(U.dec(st.lufs, 0) + ' LUFS'); lufsSl.el.hidden = st.platform !== 'custom'; }

      /* ---------------- cleanup */
      var cl = ui.section({ title: t('voice.cleanTitle') });
      var swClicks = ui.switch({ label: t('voice.clicks'), checked: st.clicks, help: t('voice.clicksHelp'),
        onChange: function (v) { st.clicks = v; ctx.save(); markDirty(); } });
      var swBreaths = ui.switch({ label: t('voice.breaths'), checked: st.breaths, help: t('voice.breathsHelp'),
        onChange: function (v) { st.breaths = v; ctx.save(); markDirty(); } });
      var den = ui.select({ options: denoiseOpts(), value: st.denoise, ariaLabel: t('voice.denAria'), onChange: function (v) { st.denoise = v; ctx.save(); markDirty(); } });
      cl.add([swClicks.el, swBreaths.el, ui.inlineField({ label: t('voice.denLabel'), help: t('voice.denHelp'), control: den.el })]);
      el.appendChild(cl.el);

      /* ---------------- music ducking */
      var mu = ui.section({ title: t('voice.musicTitle') });
      var swDuck = ui.switch({ label: t('voice.duck'), checked: st.duck, help: t('voice.duckHelp'),
        onChange: function (v) { st.duck = v; ctx.save(); paintDuck(); markDirty(); } });
      var duckSl = ui.slider({ label: t('voice.duckDb'), min: -24, max: -3, step: 1, value: st.duckDb, unit: ' dB', decimals: 0,
        onInput: function (v) { st.duckDb = v; ctx.save(); markDirty(); } });
      var musicBox = ui.h('div', { class: 'voice-music' });
      mu.add([swDuck.el, duckSl.el, musicBox]);
      el.appendChild(mu.el);
      var musicChips = null;
      function paintDuck() { duckSl.el.hidden = !st.duck; musicBox.hidden = !st.duck; }

      /* ---------------- "Suara Saya" profiles + "Tiru EQ dari contoh" */
      var pr = ui.section({ title: t('voice.profTitle'), help: t('voice.profHelp') });
      var profSel = ui.select({ options: [], ariaLabel: t('voice.profAria'), onChange: function (v) { if (v) loadProfile(v); } });
      var nameIn = ui.input({ placeholder: t('voice.profNamePh'), ariaLabel: t('voice.profNameAria') });
      var btnSave = ui.button({ label: t('common.save'), icon: 'check', small: true, onClick: saveProfile });
      var btnDel = ui.confirmClick(ui.button({ label: t('common.delete'), icon: 'trash', small: true, kind: 'ghost-danger' }), deleteProfile, t('common.confirm'));
      nameIn.el.addEventListener('keydown', function (e) { if (e.key === 'Enter') { e.preventDefault(); e.stopPropagation(); saveProfile(); } });
      var eqBox = ui.h('div', { class: 'voice-eq' });
      var eqRefLbl = ui.h('small', { class: 'help voice-eq-ref' });
      var btnRef = ui.button({ label: t('voice.eqRef'), icon: 'wand', small: true, onClick: pickRef });
      var swEq = ui.switch({ label: t('voice.eqOn'), checked: st.eqOn, help: t('voice.eqOnHelp'),
        onChange: function (v) { st.eqOn = v; ctx.save(); paintEq(); markDirty(); } });
      pr.add([ui.h('div', { class: 'voice-row' }, [profSel.el, btnDel]), ui.h('div', { class: 'voice-row' }, [nameIn.el, btnSave]),
              ui.h('div', { class: 'voice-row' }, [btnRef, eqRefLbl]), eqBox, swEq.el]);
      el.appendChild(pr.el);

      function paintProfiles() {
        var l = P.load();
        profSel.setOptions([{ value: '', label: l.profiles.length ? t('voice.profPick') : t('voice.profNone') }].concat(l.profiles.map(function (p) { return { value: p.name, label: p.name }; })));
        profSel.set(st.profile && P.find(l, st.profile) ? st.profile : '', true);
        btnDel.disabled = !profSel.get();
        if (st.profile && !nameIn.get()) nameIn.set(st.profile);
      }
      function markDirty() { if (st.profile) pr.setAside(t('voice.profDirty')); }
      function saveProfile() {
        var name = String(nameIn.get() || '').replace(/\s+/g, ' ').trim().slice(0, 40);
        if (!name) { nameIn.el.focus(); ui.toast(t('voice.profNameFirst'), 'info'); return; }
        try { P.put(name, st); st.profile = name; ctx.save(); paintProfiles(); pr.setAside(''); ui.toast(t('voice.profSaved', { name: name }), 'check'); }
        catch (e) { ui.toast(t('voice.profSaveFail', { msg: U.errMsg(e) }), { kind: 'err' }); }
      }
      function deleteProfile() {
        var name = profSel.get(); if (!name) return;
        try { P.remove(name); if (st.profile === name) st.profile = ''; ctx.save(); nameIn.set(''); paintProfiles(); pr.setAside(''); ui.toast(t('voice.profDeleted', { name: name }), 'trash'); }
        catch (e) { ui.toast(t('voice.profDeleteFail', { msg: U.errMsg(e) }), { kind: 'err' }); }
      }
      function loadProfile(name) {
        var p = P.find(P.load(), name); if (!p) return;
        U.assign(st, p.settings); st.profile = name; ctx.save();
        try { P.use(name); } catch (e) { /* read-only appdata: still applied */ }
        nameIn.set(name); syncAll(); pr.setAside(''); ui.toast(t('voice.profUsed', { name: name }), 'check');
      }
      function pickRef() {
        var fs = window.cep && window.cep.fs, path = null;
        if (fs && fs.showOpenDialogEx) {
          try {
            var r = fs.showOpenDialogEx(false, false, t('voice.refDialog'), st.eqRef || '', ['wav', 'mp3', 'm4a', 'aac', 'flac', 'ogg', 'mp4', 'mov', 'mkv']);
            if (r && !r.err && r.data && r.data[0]) path = String(r.data[0]);
          } catch (e) { ui.toast(t('voice.dialogFail', { msg: U.errMsg(e) }), { kind: 'err' }); return; }
        } else { ui.toast(t('voice.noDialog'), 'info'); return; }
        if (path) matchEq(path);
      }
      function matchEq(path) {
        btnRef.disabled = true; eqRefLbl.textContent = t('voice.eqComputing', { name: AC.sys.basename(path) });
        ctx.seq().then(function (seq) {
          return AC.engine.worker({ tool: 'voice', action: 'match_eq', seq: seq, record: false, params: U.assign(params(), { ref: path }) }).promise;
        }).then(function (r) {
          st.eq = r.eq; st.eqRef = path; st.eqOn = true; swEq.set(true, true); ctx.save(); paintEq(); markDirty();
          ui.toast(t('voice.eqReady', { name: AC.sys.basename(path) }), 'check');
        }, function (e) { eqRefLbl.textContent = t('voice.eqFailShort', { msg: U.errMsg(e) }); if (e && e.code !== 'CANCELLED') ui.toast(t('voice.eqFail', { msg: U.errMsg(e) }), { kind: 'err' }); })
          .then(function () { btnRef.disabled = false; });
      }
      function paintEq() {
        var eq = st.eq || [];
        eqRefLbl.textContent = st.eqRef ? t('voice.eqRefName', { name: AC.sys.basename(st.eqRef) }) : t('voice.eqRefHelp');
        swEq.el.hidden = !eq.length;
        eqBox.hidden = !eq.length;
        eqBox.classList.toggle('is-off', !st.eqOn);
        eqBox.innerHTML = eq.map(function (b) {
          var g = Number(b[1]) || 0, h = Math.min(50, Math.abs(g) / 6 * 50);
          var f = b[0] >= 1000 ? U.dec(b[0] / 1000, b[0] % 1000 ? 1 : 0) + 'k' : String(b[0]);
          return '<div class="voice-eq-b" title="' + U.esc(f + ' Hz: ' + (g > 0 ? '+' : '') + U.dec(g, 1) + ' dB') + '"><span class="voice-eq-bar ' + (g >= 0 ? 'up' : 'dn') + '" style="height:' + h.toFixed(1) + '%"></span><small>' + U.esc(f) + '</small></div>';
        }).join('');
        eqBox.setAttribute('aria-label', t('voice.eqAria', { bands: eq.map(function (b) { return b[0] + ' Hz ' + U.dec(b[1], 1) + ' dB'; }).join(', ') }));
      }

      /* ---------------- listen first */
      var ls = ui.section({ title: t('voice.listenTitle'), help: t('voice.listenHelp') });
      var btnPrev = ui.button({ label: t('voice.listenBtn'), icon: 'play', small: true, onClick: makePreview });
      var prevBox = ui.h('div', { class: 'voice-prev' });
      ls.add([btnPrev, prevBox]);
      el.appendChild(ls.el);
      var prevJob = null;
      function makePreview() {
        if (prevJob && prevJob.isActive()) return;
        stopAudio();
        btnPrev.disabled = true; prevBox.innerHTML = '<small class="help">' + H('voice.listenBusy') + '</small>';
        var lite = AC.seq.peek(), tp = lite && typeof lite.player === 'number' ? lite.player : null;
        ctx.seq().then(function (seq) {
          prevJob = AC.engine.worker({ tool: 'voice', action: 'preview', seq: seq, record: false, params: U.assign(params(), { t: tp }) });
          return prevJob.promise;
        }).then(function (r) {
          prevBox.innerHTML = '';
          prevBox.appendChild(abRow(r.before, r.after, t('voice.listenNote', { from: U.tcode(r.t0), to: U.tcode(r.t1), den: denLabel(r.info && r.info.denoise) })));
        }, function (e) {
          prevBox.innerHTML = '';
          if (e && e.code === 'CANCELLED') return;
          prevBox.appendChild(ui.alert({ kind: 'err', title: t('voice.listenFail'), text: U.errMsg(e) }));
        }).then(function () { btnPrev.disabled = false; });
      }

      /* ---------------- advanced */
      var adv = ui.advanced({ title: t('common.advanced'), key: 'voice' });
      function chainSlider(key, label, min, max, step, unit, dec, help) {
        return ui.slider({ label: label, min: min, max: max, step: step, value: st[key], unit: unit, decimals: dec, help: help,
          onInput: function (v) { st[key] = v; ctx.save(); syncPreset(); markDirty(); } });
      }
      var sec = ' ' + t('unit.sec');
      var sl = {
        hp: chainSlider('hp', t('voice.hp'), 0, 200, 10, ' Hz', 0, t('voice.hpHelp')),
        nr: ui.slider({ label: t('voice.nr'), min: 0, max: 1, step: 0.05, value: st.nr, decimals: 0, format: function (v) { return Math.round(v * 100) + '%'; }, help: t('voice.nrHelp'),
          onInput: function (v) { st.nr = v; ctx.save(); syncPreset(); markDirty(); } }),
        deess: ui.slider({ label: t('voice.deess'), min: 0, max: 1, step: 0.05, value: st.deess, decimals: 0, format: function (v) { return Math.round(v * 100) + '%'; }, help: t('voice.deessHelp'),
          onInput: function (v) { st.deess = v; ctx.save(); syncPreset(); markDirty(); } }),
        comp: chainSlider('comp', t('voice.comp'), 1, 8, 0.5, ':1', 1, t('voice.compHelp')),
        presence: chainSlider('presence', t('voice.presence'), -6, 8, 0.5, ' dB', 1, ''),
        warmth: chainSlider('warmth', t('voice.warmth'), -6, 6, 0.5, ' dB', 1, ''),
        mud: chainSlider('mud', t('voice.mud'), -8, 4, 0.5, ' dB', 1, ''),
        air: chainSlider('air', t('voice.air'), -6, 6, 0.5, ' dB', 1, '')
      };
      var clickDb = ui.slider({ label: t('voice.clickDb'), min: -60, max: -6, step: 2, value: st.clickDb, unit: ' dB', decimals: 0, onInput: function (v) { st.clickDb = v; ctx.save(); markDirty(); } });
      var breathDb = ui.slider({ label: t('voice.breathDb'), min: -40, max: -3, step: 1, value: st.breathDb, unit: ' dB', decimals: 0, onInput: function (v) { st.breathDb = v; ctx.save(); markDirty(); } });
      var attack = ui.slider({ label: t('voice.attack'), min: 0.05, max: 2, step: 0.05, value: st.attack, unit: sec, decimals: 2, onInput: function (v) { st.attack = v; ctx.save(); markDirty(); } });
      var release = ui.slider({ label: t('voice.release'), min: 0.05, max: 3, step: 0.05, value: st.release, unit: sec, decimals: 2, onInput: function (v) { st.release = v; ctx.save(); markDirty(); } });
      var voiceSel = ui.select({ options: [{ value: 'auto', label: t('voice.trackAuto') }], value: st.voiceTrack, ariaLabel: t('voice.trackLabel'),
        onChange: function (v) { st.voiceTrack = v; ctx.save(); loadTracks(); } });
      adv.add([ui.inlineField({ label: t('voice.trackLabel'), help: t('voice.trackHelp'), control: voiceSel.el }),
               sl.hp.el, sl.nr.el, sl.deess.el, sl.comp.el, sl.presence.el, sl.warmth.el, sl.mud.el, sl.air.el, clickDb.el, breathDb.el, attack.el, release.el]);
      el.appendChild(adv.el);
      function syncPreset() {
        var vals = {}; CHAIN_KEYS.forEach(function (k) { vals[k] = st[k]; });
        var m = presets.sync(vals);
        st.preset = m; if (m) st.base = m;
        adv.setAside(m ? '' : t('common.custom'));
      }
      function syncSliders() { CHAIN_KEYS.forEach(function (k) { sl[k].set(st[k], true); }); adv.setAside(''); }
      function syncAll() {
        syncSliders(); syncPreset();
        plat.set(st.platform, true); lufsSl.set(st.lufs, true); paintTarget();
        swClicks.set(st.clicks, true); swBreaths.set(st.breaths, true); den.set(st.denoise, true);
        swDuck.set(st.duck, true); duckSl.set(st.duckDb, true); paintDuck();
        clickDb.set(st.clickDb, true); breathDb.set(st.breathDb, true); attack.set(st.attack, true); release.set(st.release, true);
        swEq.set(!!st.eqOn, true); paintEq(); paintProfiles();
      }

      /* ---------------- tracks (voice choice, music candidates) */
      var trackJob = null;
      function loadTracks() {
        if (trackJob && trackJob.isActive()) return;
        ctx.seq().then(function (seq) {
          if (!seq) { info = null; paintTracks(); return null; }
          trackJob = AC.engine.worker({ tool: 'voice', action: 'tracks', seq: seq, record: false, params: params() });
          return trackJob.promise.then(function (r) { info = r; paintTracks(); });
        }).catch(function (e) { info = { err: U.errMsg(e) }; paintTracks(); });
      }
      function paintTracks() {
        if (!el.isConnected) return;                                   // an older render (language switch) still loading
        if (!info || info.err) {
          trackTag.innerHTML = info && info.err ? ui.tag(t('voice.tracksUnread'), 'warn', 'alert') : '';
          musicBox.innerHTML = '<small class="help">' + U.esc(info && info.err ? info.err : t('voice.openSeqForMusic')) + '</small>';
          return;
        }
        var rows = info.tracks || [], vo = rows.filter(function (r) { return r.render; })[0];
        var tname = function (r) { return r.name || 'A' + (r.index + 1); };
        trackTag.innerHTML = vo ? ui.tag(vo.words ? t('voice.voiceOnWords', { track: tname(vo), n: vo.words }) : t('voice.voiceOn', { track: tname(vo) }), 'line', 'mic') : '';
        voiceSel.setOptions([{ value: 'auto', label: t('voice.trackAuto') }].concat(rows.map(function (r) { return { value: String(r.index), label: r.words ? t('voice.trackWords', { track: tname(r), n: r.words }) : tname(r) }; })));
        voiceSel.set(st.voiceTrack, true);
        var music = info.music || [];
        musicBox.innerHTML = '';
        if (!music.length) { musicBox.appendChild(ui.h('small', { class: 'help', text: t('voice.noOtherTracks') })); musicChips = null; return; }
        var sel = Array.isArray(st.duckTracks) ? st.duckTracks : music.filter(function (m) { return m.auto; }).map(function (m) { return m.track; });
        musicChips = ui.chips({ multi: true, ariaLabel: t('voice.duckTracksAria'), value: sel,
          options: music.map(function (m) { return { value: m.track, label: m.score >= 0.5 ? t('voice.musicChip', { name: m.name }) : m.name, title: t('voice.musicScore', { p: Math.round(m.score * 100) }) }; }),
          onChange: function (v) { st.duckTracks = v; ctx.save(); markDirty(); } });
        musicBox.appendChild(ui.h('small', { class: 'help', text: t('voice.duckTracksHelp') }));
        musicBox.appendChild(musicChips.el);
      }

      function params() {
        var o = { preset: st.base || 'natural', platform: st.platform, lufs: st.lufs, clicks: !!st.clicks, breaths: !!st.breaths, denoise: st.denoise,
                  click_db: st.clickDb, breath_db: st.breathDb, duck: !!st.duck, duck_db: st.duckDb, attack: st.attack, release: st.release,
                  voice_tracks: st.voiceTrack && st.voiceTrack !== 'auto' ? [Number(st.voiceTrack)] : null,
                  duck_tracks: Array.isArray(st.duckTracks) ? st.duckTracks.slice() : null, eq: st.eqOn ? (st.eq || []) : [] };
        CHAIN_KEYS.forEach(function (k) { o[k] = st[k]; });
        return o;
      }
      ctx.voice = { params: params, info: function () { return info; }, loadTracks: loadTracks, syncAll: syncAll,
                    seqChanged: U.debounce(loadTracks, 800) };

      ctx.dock({ primary: { label: ctx.def.cta, icon: 'search', onClick: run } });
      syncAll();
      loadTracks();

      /* ================================================================ analyze -> review */
      function run() {
        stopAudio();
        ctx.run({ action: 'analyze', title: t('voice.runTitle'), params: params(),
          stages: [{ id: 'audio', label: t('voice.stAudio'), w: 0.35 }, { id: 'words', label: t('voice.stWords'), w: 0.1 }, { id: 'detect', label: t('voice.stDetect'), w: 0.3 },
                   { id: 'measure', label: t('voice.stMeasure'), w: 0.15 }, { id: 'review', label: t('voice.stReview'), w: 0.1 }],
          onResult: onAnalyzed });
      }
      function onAnalyzed(data) {
        if (!data || !data.review) { ctx.error(U.err('NO_RESULT', t('voice.noReview'))); return; }
        reviewFile = data.review;
        if (!data.n) { applyAll(data.review, data.stats); return; }
        openReview(data.review, data.stats);
      }
      function openReview(file, stats) {
        var b = (stats && stats.before) || {};
        ctx.review({ file: file, unit: t('voice.unit'), verbOn: t('voice.verbOn'), verbOff: t('voice.verbOff'), tags: rowTags,
          filters: [{ id: 'click', label: t('voice.click'), icon: 'x', test: function (it) { return it.kind === 'click'; } },
                    { id: 'breath', label: t('voice.breath'), icon: 'wave', test: function (it) { return it.kind === 'breath'; } }],
          seekTime: function (it) { return Math.max(0, it.t0 - 1); },
          markerTag: '[Klipora-VC-RV]', markerName: function (it) { return it.kind === 'breath' ? t('voice.breath') : t('voice.click'); },
          rowActions: [{ label: t('voice.listenAb'), icon: 'play', run: abItem }],
          bulkAction: { label: t('voice.skipAll'), run: function (list) { list.setAll(false); try { list.save(file); } catch (e) { /* written again on apply */ } applyAll(file); } },
          title: function (on, all) { return H('voice.rvTitle', { n: on }) + '<small>' + H('voice.rvTitleSub', { all: all, lufs: lufs(b.lufs) }) + '</small>'; },
          toggleText: function (it) { return it.on ? t('voice.rvOff') : t('voice.rvOn'); },
          legendExtra: function () { return '<span>' + H('voice.rvLegend') + '</span>'; },
          primary: { label: function (n) { return t('voice.applyBtn', { n: n }); }, onApply: function (items, doc, f) { applyAll(f, doc.stats); } } });
      }
      function abItem(it) {
        ui.toast(t('voice.abMaking'), 'clock');
        ctx.seq().then(function (seq) {
          return AC.engine.worker({ tool: 'voice', action: 'snippet', seq: seq, record: false, workdir: reviewFile ? AC.sys.dirname(reviewFile) : undefined,
            params: U.assign(params(), { t0: it.t0, t1: it.t1, kind: it.kind, db: it.kind === 'breath' ? st.breathDb : st.clickDb }) }).promise;
        }).then(function (r) { playFile(r.path, null, t('voice.abPlaying', { dur: U.dtk(r.dur) })); },
          function (e) { if (e && e.code !== 'CANCELLED') ui.toast(t('voice.abFail', { msg: U.errMsg(e) }), { kind: 'err' }); });
      }

      /* ================================================================ apply: engine render -> host on a clone */
      function applyAll(file, stats) {
        if (ctx.job() && ctx.job().isActive()) { ui.toast(t('voice.busy'), 'clock'); return; }
        stopAudio();
        var made = null, eng = null, cancelled = false, plan = null, res = null;
        var task = new AC.Task({ tool: 'voice', action: 'apply', title: ctx.def.title, label: t('voice.taskLabel'), kind: 'host' });
        task.plan([{ id: 'render', label: t('voice.taskLabel'), w: 0.7, sub: t('voice.stRenderSub') }, { id: 'clone', label: t('voice.stClone'), w: 0.06, sub: t('voice.origUntouched') },
                   { id: 'mute', label: t('voice.stMute'), w: 0.06 }, { id: 'place', label: t('voice.stPlace'), w: 0.1 }, { id: 'duck', label: t('voice.stDuck'), w: 0.08 }]);
        task.seqName = (stats && stats.seq && stats.seq.name) || '';
        AC.history.track(task);
        task._cancel = function () { cancelled = true; if (eng && eng.isActive()) eng.cancel(); };
        ctx.track(task, { title: t('voice.applyTitle'), note: t('voice.applyNote'),
          onDone: function (r) { showResult(r, file); }, onRetry: function () { applyAll(file, stats); } });
        function check() { if (cancelled) throw U.err('CANCELLED', t('voice.cancelled')); }
        function chunks(list, n, fn, stageId) {
          var i = 0, total = list.length;
          function next() {
            check();
            if (i >= total) return Promise.resolve();
            var part = list.slice(i, i + n), first = i === 0;
            i += n;
            return fn(part, first, i >= total).then(function () { task.progress(Math.min(100, i / total * 100)); return next(); });
          }
          task.stage({ id: stageId });
          return next().then(function () { task.stageDone(stageId); });
        }
        var info2 = { disabled: 0, muted: 0, missing: [], locked: [], keys: 0, duckClips: 0, duckLocked: [], videoKept: 0 };
        task.stage({ id: 'render' });
        AC.seq.hold(task.promise);
        eng = AC.engine.run({ tool: 'voice', action: 'apply', review: file, params: params(), record: false, title: t('voice.taskLabel') });
        eng.on('progress', function () { task.progress(eng.pct); });
        eng.on('stage', function () { var s = eng.stages && eng.stages[eng.cur]; if (s && s.label) task.note = s.label; });
        eng.on('warn', function (m) { task.log(String(m && m.msg || m), 'warn'); });
        eng.promise.then(function (r) {
          res = r; plan = r.plan; task.stageDone('render'); check();
          if (!plan || plan.kind !== 'voice') throw U.err('NO_RESULT', t('voice.noPlan'));
          task.stage({ id: 'clone' });
          // The host also reuses the old "AutoCut Suara" track of an earlier result.
          return AC.host.exec('bac_voice_begin', [plan.seq.id, plan.seq.name + AC.brand.suffix, plan.track_name || TRACK], { json: true, timeout: 60000 });
        }).then(function (c) {
          made = c; task.stageDone('clone');
          return chunks(plan.mute_clips || [], DISABLE_CHUNK, function (part) {
            return AC.host.exec('bac_voice_disable', [made.id, part], { json: true, timeout: 120000 }).then(function (d) {
              info2.disabled += d.disabled; info2.muted += d.muted; info2.videoKept += d.videoKept || 0;
              (d.missing || []).forEach(function (m) { info2.missing.push(m); });
              (d.locked || []).forEach(function (n) { if (info2.locked.indexOf(n) < 0) info2.locked.push(n); });
            });
          }, 'mute');
        }).then(function () {
          if ((plan.mute_clips || []).length && !info2.disabled && !info2.muted) {
            if (info2.locked.length) throw U.err('LOCKED', t('voice.errLocked', { tracks: info2.locked.join(', ') }), t('voice.errLockedHint'));
            throw U.err('NO_AUDIO', t('voice.errNoAudio'), t('voice.errNoAudioHint'));
          }
          task.stage({ id: 'place' });
          return AC.host.exec('bac_voice_place', [made.id, made.track, plan.wav, plan.start], { json: true, timeout: 180000 });
        }).then(function (pl) {
          info2.placed = pl; task.stageDone('place');
          var duck = plan.duck;
          if (!duck || !duck.ranges || !duck.ranges.length || !duck.tracks.length) { task.stage({ id: 'duck' }); task.stageDone('duck', 0, t('voice.notNeeded')); return null; }
          return chunks(duck.ranges, DUCK_CHUNK, function (part, first, last) {
            return AC.host.exec('bac_voice_duck', [made.id, duck.tracks, part, { db: duck.db, attack: duck.attack, release: duck.release, reset: first && (plan.rerun || made.rerun), last: last }], { json: true, timeout: 180000 })
              .then(function (k) { info2.keys += k.keys; info2.duckClips = Math.max(info2.duckClips, k.clips); (k.locked || []).forEach(function (n) { if (info2.duckLocked.indexOf(n) < 0) info2.duckLocked.push(n); }); });
          }, 'duck');
        }).then(function () {
          var st2 = res.stats || {};
          task.summary = t('voice.summary', { from: lufs(st2.before && st2.before.lufs), to: lufs(st2.after && st2.after.lufs) });
          task.done({ made: made, plan: plan, res: res, info: info2, secs: task.elapsed() });
        }).catch(function (e) {
          if (!made) { task.fail(e); return; }
          var code = e && e.code, own = code === 'LOCKED' || code === 'NO_AUDIO';
          var why = code === 'CANCELLED' ? t('voice.cancelled') : own ? e.msg : t('voice.failMid');
          AC.host.json('bac_deleteSequence', made.id).then(function () { return AC.host.json('bac_openSequence', made.origId); }, function () { return null; })
            .then(function () { return null; }, function () { return null; })
            .then(function () {
              task.fail(U.err(code || 'HOST', t('voice.failCleaned', { why: why, name: made.name }), own ? e.hint : (e && (e.msg || e.message) || '')));
            });
        });
      }

      function showResult(r, file) {
        var s = (r.res && r.res.stats) || {}, b = s.before || {}, a = s.after || {}, made = r.made, plan = r.plan, info2 = r.info;
        var warns = (r.res && r.res.warnings || []).slice();
        if (info2.missing.length) warns.push(t('voice.wMissing', { n: info2.missing.length }));
        if (info2.muted) warns.push(t('voice.wMuted', { n: info2.muted }));
        if (info2.locked.length) warns.push(t('voice.wLocked', { tracks: info2.locked.join(', ') }));
        if (info2.duckLocked.length) warns.push(t('voice.wDuckLocked', { tracks: info2.duckLocked.join(', ') }));
        if (!made.named) warns.push(t('voice.wTrackName', { track: TRACK, idx: 'A' + (made.track + 1) }));
        if (s.normalization && s.normalization !== 'linear') warns.push(t('voice.wDynamic'));
        var twins = (plan.mute_tracks || []).length > 1 ? ' ' + t('voice.twins', { tracks: trackList(plan.mute_tracks, 'voice.and') }) : '';
        var duckTxt = plan.duck && info2.keys ? ' ' + t('voice.duckTxt', { tracks: trackList(plan.duck.tracks), db: U.dec(plan.duck.db, 0), n: plan.duck.ranges.length }) : '';
        var extra = ui.h('div', { class: 'voice-result' });
        if (s.preview && s.preview.before) extra.appendChild(abRow(s.preview.before, s.preview.after, t('voice.resListen', { t: U.tcode(s.preview.t0) })));
        extra.appendChild(ui.h('p', { class: 'help', text: t('voice.resLine1', { name: made.name, track: made.trackName || TRACK }) + ' ' +
          t('voice.resLine2', { clicks: s.clicks, breaths: s.breaths, den: denLabel(s.denoise), tp0: fnum(b.tp), tp1: fnum(a.tp) }) + duckTxt + twins +
          ' ' + t('voice.resLine3', { mb: U.dec(s.size_mb || 0, 0), dur: U.dtk(s.secs || 0) }) }));
        ctx.result({
          title: t('voice.resTitle'),
          sub: t('voice.resSub', { dur: U.dtk(r.secs) }),
          statsList: [{ label: t('voice.statLufs'), value: t('voice.fromTo', { a: lufs(b.lufs), b: lufs(a.lufs) }), accent: true },
                      { label: t('voice.statSnr'), value: t('voice.fromTo', { a: snr(b), b: snr(a) }) + ' dB' },
                      { label: t('voice.statTarget'), value: U.dec(s.target, 0) + ' LUFS' }],
          extra: extra,
          original: { id: made.origId, name: made.origName },
          onEdit: reviewFile ? function () { ctx.go('review'); } : null,
          onDelete: function () {
            AC.host.json('bac_deleteSequence', made.id).then(function () { return AC.host.json('bac_openSequence', made.origId); })
              .then(function () { ui.toast(t('voice.deleted'), 'trash'); ctx.back(); },
                function (e) { ui.toast(t('voice.deleteFail', { msg: U.errMsg(e) }), { kind: 'err' }); });
          },
          actions: [{ label: t('voice.openFolder'), icon: 'folder', onClick: function () { AC.sys.openFolder(AC.sys.dirname(plan.wav)); } }],
          warn: warns.length ? { title: warns.length === 1 ? t('voice.warnTitle1') : t('voice.warnTitleN', { n: warns.length }), text: warns.join(' ') } : null,
          next: ctx.def.next
        });
      }
    },

    onShow: function (ctx) { if (ctx.voice) ctx.voice.loadTracks(); },
    onHide: function () { stopAudio(); },
    onUnmount: function () { stopAudio(); },
    onSeq: function (lite, ctx) { if (ctx.voice) ctx.voice.seqChanged(); }
  });
})();
