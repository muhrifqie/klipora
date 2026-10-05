/* repeat.js: Potong Pengulangan (engine tool "repeats", engine/ac/tools/repeats.py; host/32_repeat.jsx).
   Finds stutters, false starts, corrections and retakes on the transcript and keeps the LAST take. The Tinjau rows
   show the removed attempt struck through next to the kept take ("Dengar" plays the edit). Whisper hallucination
   runs are shown as "Transkrip ngaco" flags and are never cut. "Minta pendapat AI" only adds reasons and labels:
   the checkboxes always come from the rules. Apply = engine plan -> AC.apply.removeRanges on a clone, or markers.
   Texts: locale keys repeat.* (AC.t at render time); transcript text in the rows is user content (data-i18n-skip). */
(function () {
  'use strict';
  var AC = window.AC, U = AC.util, ui = AC.ui;
  var TAG = '[Klipora-ULANG]', FLAG_TAG = '[Klipora-NGACO]';         // old [AC-ULANG] / [AC-NGACO] cleared too (acHasTag)
  function L(k, v) { return AC.t(k, v); }
  var KIND_IDS = ['stutter', 'restart', 'correction', 'retake'];     // label repeat.kind.<id>, tooltip repeat.kindTip.<id>
  function kinds() { return KIND_IDS.map(function (k) { return { value: k, label: L('repeat.kind.' + k), title: L('repeat.kindTip.' + k) }; }); }
  function kindLabel(k) { return KIND_IDS.indexOf(k) >= 0 ? L('repeat.kind.' + k) : k; }
  var PRESETS = [                                                    // label repeat.preset.<value>, hint repeat.preset.<value>Hint
    { value: 'ketat', values: { minOn: 80, sim: 90, window: 10, minShow: 55 } },
    { value: 'normal', values: { minOn: 65, sim: 80, window: 20, minShow: 45 } },
    { value: 'longgar', values: { minOn: 55, sim: 65, window: 40, minShow: 35 } }
  ];
  function presetOptions() {
    return PRESETS.map(function (p) { return { value: p.value, label: L('repeat.preset.' + p.value), hint: L('repeat.preset.' + p.value + 'Hint'), values: p.values }; });
  }
  function stages() {
    return [{ id: 'words', label: L('repeat.stWords'), w: 0.6 }, { id: 'audio', label: L('repeat.stAudio'), w: 0.1 }, { id: 'find', label: L('repeat.stFind'), w: 0.15 }];
  }

  /* ---------------- review row rendering (pure helpers) */
  function words(s, n, fromEnd) {
    var w = String(s || '').split(/\s+/).filter(Boolean);
    if (w.length <= n) return w.join(' ');
    return fromEnd ? '... ' + w.slice(w.length - n).join(' ') : w.slice(0, n).join(' ') + ' ...';
  }
  function shortDrop(s) {   // long attempts: head + tail so the kept take stays visible on two lines
    var w = String(s || '').split(/\s+/).filter(Boolean);
    return w.length <= 10 ? w.join(' ') : w.slice(0, 6).join(' ') + ' ... ' + w.slice(w.length - 3).join(' ');
  }
  function rowCtx(it) {
    var drop = it.drop !== undefined ? it.drop : (it.label || '');
    var keep = it.keep !== undefined ? it.keep : (it.ctx && it.ctx.post) || '';
    var pre = words(it.ctx && it.ctx.pre, 3, true), after = words(it.after, 3);
    var full = (it.ctx && it.ctx.pre ? it.ctx.pre + ' ' : '') + '[' + drop + '] ' + keep + (it.after ? ' ' + it.after : '');
    return '<span class="repeat-ctx" data-i18n-skip title="' + U.esc(full) + '">' + (pre ? '<span class="repeat-pre">' + U.esc(pre) + '</span> ' : '') +
      '<del>' + U.esc(shortDrop(drop)) + '</del> <ins>' + U.esc(keep) + '</ins>' + (after ? ' <span class="repeat-post">' + U.esc(after) + '</span>' : '') + '</span>';
  }
  function confTag(c) {
    if (typeof c !== 'number') return '';
    var p = Math.round(c * 100), kind = c >= 0.8 ? 'ok' : c >= 0.5 ? 'line' : 'warn';
    return '<span class="tag tag-' + kind + ' conf" title="' + U.esc(L('repeat.conf', { pct: p })) + '">' + p + '%</span>';
  }
  function aiDoubt(it) { return !!(it.ai && it.ai.verdict && it.ai.verdict !== 'cut'); }
  function rowTags(it) {
    var h = '<span class="tag tag-line repeat-kind">' + U.esc(KIND_IDS.indexOf(it.kind) >= 0 ? kindLabel(it.kind) : it.kind_label || it.kind) + '</span>';
    if (it.ai && it.ai.verdict) h += aiDoubt(it) ? '<span class="tag tag-warn" title="' + U.esc(L('repeat.aiDoubtTip')) + '">' + U.esc(L('repeat.aiDoubt')) + '</span>'
      : '<span class="tag tag-ai" title="' + U.esc(L('repeat.aiAgreeTip')) + '">' + U.esc(L('repeat.aiAgree')) + '</span>';
    return h + confTag(it.conf);
  }
  function rowNote(it) {
    var a = it.ai;
    if (a && a.verdict === 'keep') return { kind: 'warn', icon: 'spark', text: L('repeat.noteAiKeep', { reason: a.reason || '' }) };
    if (a && a.verdict === 'other') return { kind: 'warn', icon: 'spark', text: L('repeat.noteAiOther', { reason: a.reason || '' }) };
    if (it.tight) return { kind: 'warn', icon: 'alert', text: L('repeat.noteTight') };
    if (a && a.reason) return { icon: 'spark', text: L('repeat.noteAi', { reason: a.reason }) };
    if (it.long_gap) return { icon: 'info', text: L('repeat.noteLongGap', { sec: U.dec(it.long_gap, 1) + ' ' + L('unit.sec') }) };
    if (it.why) return { icon: 'repeat', text: it.why };
    return null;
  }
  function fileUrl(p) {
    return 'file:///' + encodeURI(String(p).replace(/\\/g, '/')).replace(/#/g, '%23').replace(/\?/g, '%3F');
  }

  AC.tools.register({
    id: 'repeat', engineTool: 'repeats', group: 'potong', order: 3, icon: 'repeat', source: { tags: true },
    badges: ['ai'],
    text: function (t) {
      return { title: t('repeat.title'), tab: t('repeat.tab'), desc: t('repeat.desc'), lead: t('repeat.lead'), cta: t('repeat.cta'),
               next: [{ tool: 'silence', reason: t('repeat.nextSilence') }, { tool: 'captions', reason: t('repeat.nextCaptions') }] };
    },
    defaults: { preset: 'normal', minOn: 65, sim: 80, window: 20, minShow: 45, kinds: ['stutter', 'restart', 'correction', 'retake'], ai: false, mode: 'cut' },

    render: function (el, ctx) {
      var st = ctx.state, def = ctx.def;
      var audio = null, playingId = null;
      if (!st.kinds || !st.kinds.length) st.kinds = def.defaults.kinds.slice();

      el.appendChild(ui.h('div', { class: 'tool-hero', html: '<span class="tool-ic">' + ui.icon('repeat') + '</span><div><p>' + U.esc(def.lead) +
        '</p><div class="tags">' + ui.badge('ai') + ui.tag(L('repeat.keepLast'), 'line', 'check') + '</div></div>' }));

      /* ---- Gaya */
      var gaya = ui.section({ title: L('repeat.secStyle') });
      var presets = ui.presets({ options: presetOptions(), value: st.preset, ariaLabel: L('repeat.styleAria'),
        customHint: L('repeat.customHint'),
        onChange: function (v, p) {
          st.preset = v; U.assign(st, p.values);
          minOn.set(st.minOn, true); sim.set(st.sim, true); win.set(st.window, true);
          adv.setAside(''); ctx.save();
        } });
      gaya.add(presets);
      el.appendChild(gaya.el);

      /* ---- Cari */
      var cari = ui.section({ title: L('repeat.secFind'), help: L('repeat.findHelp') });
      var kindChips = ui.chips({ multi: true, ariaLabel: L('repeat.kindsAria'), options: kinds(), value: st.kinds, onChange: function (v) {
        st.kinds = v; ctx.save(); paintDock();
      } });
      cari.add(kindChips);
      el.appendChild(cari.el);

      /* ---- Pengaturan lanjutan */
      var adv = ui.advanced({ title: L('common.advanced'), key: 'repeat' });
      function manual() {
        st.preset = presets.sync({ minOn: st.minOn, sim: st.sim, window: st.window, minShow: st.minShow });
        adv.setAside(st.preset ? '' : L('common.custom')); ctx.save();
      }
      var minOn = ui.slider({ label: L('repeat.minOn'), min: 50, max: 95, step: 5, value: st.minOn, unit: '%', decimals: 0,
        help: L('repeat.minOnHelp'), onInput: function (v) { st.minOn = v; manual(); } });
      var sim = ui.slider({ label: L('repeat.sim'), min: 60, max: 95, step: 5, value: st.sim, unit: '%', decimals: 0,
        help: L('repeat.simHelp'), onInput: function (v) { st.sim = v; manual(); } });
      var win = ui.slider({ label: L('repeat.window'), min: 5, max: 60, step: 5, value: st.window, unit: ' ' + L('unit.sec'), decimals: 0,
        help: L('repeat.windowHelp'), onInput: function (v) { st.window = v; manual(); } });
      adv.add([minOn, sim, win]);
      el.appendChild(adv.el);
      if (!presets.match({ minOn: st.minOn, sim: st.sim, window: st.window, minShow: st.minShow })) adv.setAside(L('common.custom'));

      /* ---- AI */
      var aiSec = ui.section({ title: 'AI' });
      var aiSw = ui.switch({ label: L('repeat.ai'), badge: ui.badge('ai'), checked: !!st.ai,
        help: L('repeat.aiHelp'),
        onChange: function (v) { st.ai = v; ctx.save(); paintAi(); } });
      var aiNote = ui.h('small', { class: 'help repeat-ai-note' });
      aiSec.add([aiSw, aiNote]);
      el.appendChild(aiSec.el);
      function aiOff() { return AC.settings.get('ai') === false; }
      function paintAi() {
        var off = aiOff(), h = AC.engine.lastHealth, offline = h && h.ai && h.ai.ok === false;
        aiSw.input.disabled = off;
        aiNote.textContent = off ? L('repeat.aiOff') : offline && st.ai ? L('repeat.aiOffline') : st.ai ? L('repeat.aiCost') : '';
        aiNote.hidden = !aiNote.textContent;
      }
      paintAi();

      /* ---- Hasil */
      var hasil = ui.section({ title: L('repeat.secResult') });
      hasil.add(ui.radioCards({ ariaLabel: L('repeat.secResult'), value: st.mode, onChange: function (v) { st.mode = v; ctx.save(); }, options: [
        { value: 'cut', title: L('repeat.modeCut'), desc: L('repeat.modeCutDesc', { name: AC.brand.suffix.trim() }) },
        { value: 'markers', title: L('repeat.modeMarkers'), desc: L('repeat.modeMarkersDesc') }
      ] }));
      el.appendChild(hasil.el);

      function paintDock() {
        var none = !st.kinds || !st.kinds.length;
        ctx.dock({ primary: { label: def.cta, icon: 'play', onClick: run, disabled: none, title: none ? L('repeat.needKind') : '' } });
      }
      paintDock();

      var onHealth = function () { paintAi(); };
      AC.bus.on('health', onHealth);
      AC.settings.on('change', function (k) { if (k === 'ai') paintAi(); });
      ctx._repeatTags = paintTranscriptTag;

      /* ---- transcript tag on the source card */
      function paintTranscriptTag() {
        if (!ctx.source || !AC.seq.peek()) return;
        AC.seq.current().then(function (s) {
          if (!s || !ctx.source) return;
          var has = AC.seq.hasTranscript(s);
          ctx.source.setTags(has ? ui.tag(L('repeat.hasTranscript'), 'ok', 'check') : ui.tag(L('repeat.needTranscript'), 'warn', 'clock'));
        }, function () { /* no sequence: the source card says so */ });
      }
      paintTranscriptTag();

      /* ---- run */
      function run() {
        var useAi = !!st.ai && !aiOff();
        ctx.run({ action: 'analyze', title: L('repeat.jobAnalyze'),
          params: { min_on: st.minOn / 100, min_show: st.minShow / 100, sim: st.sim / 100, window: st.window, kinds: st.kinds.slice(), ai: useAi },
          stages: stages().concat(useAi ? [{ id: 'ai', label: L('repeat.stAi'), w: 0.2 }] : [], [{ id: 'review', label: L('repeat.stReview'), w: 0.05 }]),
          onResult: showResult });
      }

      function showResult(data) {
        data = data || {};
        var flags = data.flags || [], summ = data.summary || {};
        if (!summ.n) {
          ctx.result({ title: L('repeat.noneTitle'), sub: L(flags.length ? 'repeat.noneSubFlags' : 'repeat.noneSub'),
            statsList: [{ label: L('repeat.statWords'), value: U.int((data.stats && data.stats.words) || 0) }, { label: L('repeat.flags'), value: String(flags.length), accent: flags.length > 0 }],
            extra: flags.length ? flagsBlock(flags) : null, next: def.next });
          return;
        }
        showReview(data.review);
      }

      function showReview(file) {
        var list = ctx.review({ file: file, unit: L('repeat.unit'), verbOn: L('repeat.removed'), verbOff: L('repeat.kept'),
          ctx: rowCtx, tags: rowTags, note: rowNote,
          filters: kinds().map(function (k) { return { id: k.value, label: k.label, test: function (it) { return it.kind === k.value; } }; }).concat([
            { id: 'ragu', label: L('repeat.fCheck'), icon: 'alert', test: function (it) { return it.tight || aiDoubt(it) || (typeof it.conf === 'number' && it.conf < st.minOn / 100); } }]),
          rowActions: [{ label: L('repeat.listen'), icon: 'play', run: function (it) { listen(it); } }],
          bulkAction: { label: L('repeat.toMarkers'), run: function (l) { sendMarkers(l.selected(), l.doc(), false); } },
          primary: { label: function (n) { return L(st.mode === 'markers' ? 'repeat.ctaMark' : 'repeat.ctaCut', { n: n }); }, onApply: applyReview } });
        if (!list) return;
        var doc = list.doc(), head = list.el.querySelector('.rv-head');
        if (head) head.appendChild(ui.h('p', { class: 'help repeat-legend', html: AC.i18n.html('repeat.legend') }));
        if (doc.ai && doc.ai.used && doc.ai.source !== 'ai' && doc.ai.source !== 'cache') {
          list.el.insertBefore(ui.alert({ kind: 'warn', title: L('repeat.aiUnused'), text: L('repeat.aiUnusedText') }), list.el.children[1]);
        }
        if (doc.flags && doc.flags.length) list.el.insertBefore(flagsBlock(doc.flags), list.el.children[1]);
      }

      /* ---- "Transkrip ngaco" flags: shown, seekable, can become markers, never cut */
      function flagsBlock(flags) {
        var box = ui.h('div', { class: 'repeat-flags', role: 'group', 'aria-label': L('repeat.flags'), title: L('repeat.flagsTip') });
        var head = ui.h('div', { class: 'repeat-flags-h', html: ui.icon('flag') + '<b>' + U.esc(L('repeat.flagsHead', { n: flags.length })) + '</b><span>' + U.esc(L('repeat.flagsNotCut')) + '</span>' });
        head.appendChild(ui.h('button', { class: 'linkbtn', type: 'button', text: L('repeat.flagsMark'), title: L('repeat.flagsMarkTip', { tag: FLAG_TAG }),
          on: { click: function () { markFlags(flags); } } }));
        var row = ui.h('div', { class: 'repeat-flag-list', 'data-i18n-skip': '' });   // transcript text = user content
        flags.slice(0, 12).forEach(function (f) {
          var b = ui.h('button', { class: 'chip', type: 'button', title: (f.label || '') + ': ' + (f.text || ''),
            html: '<span class="tc">' + U.esc(U.mmss(f.t0)) + '</span> ' + U.esc(words(f.text, 3)) });
          b.addEventListener('click', function () { seek(f.t0); });
          row.appendChild(b);
        });
        box.appendChild(head); box.appendChild(row);
        return box;
      }
      function seek(t) {
        AC.host.call('bac_seek', t).then(function () { ui.toast(L('repeat.seekTo', { tc: U.tcode(t) }), 'playhead'); },
          function (e) { ui.toast(L('repeat.seekFailed', { msg: U.errMsg(e) }), { kind: 'err' }); });
      }

      /* ---- markers (host/32_repeat.jsx; falls back to the foundation marker API when the host file is missing) */
      function putMarkers(list, tag, seqId) {
        return AC.host.exec('bac_repeat_markers', [list, tag, seqId || ''], { json: true, timeout: 60000 }).then(null, function (e) {
          if (!e || e.code !== 'HOST_EVAL') throw e;
          AC.log.warn('bac_repeat_markers missing, using AC.apply.markers');
          return AC.apply.markers(list, { tag: tag, replace: true, seqId: seqId });
        });
      }
      function markerList(items) {
        return items.map(function (it) {
          return { t: it.t0, end: it.t1, color: 3, name: L('repeat.markName', { kind: kindLabel(it.kind), text: words(it.drop, 6) }),
                   comment: L('repeat.markKeep', { text: words(it.keep, 8) }) + (it.why ? '\n' + it.why : '') };
        });
      }
      function sendMarkers(items, doc, asResult) {
        if (!items.length) { ui.toast(L('repeat.noneChecked')); return; }
        var seqId = doc && doc.seq && doc.seq.id;
        putMarkers(markerList(items), TAG, seqId).then(function (r) {
          var n = (r && r.n) || items.length;
          if (!asResult) { ui.toast(L('repeat.markersMade', { n: n }), 'marker'); return; }
          ctx.result({ title: L('repeat.mkTitle', { n: n }), sub: L('repeat.mkSub', { tag: TAG }),
            statsList: [{ label: L('repeat.statRepeats'), value: String(n), accent: true }, { label: L('repeat.statDur'), value: U.dtk(items.reduce(function (a, it) { return a + it.t1 - it.t0; }, 0)) }],
            onEdit: function () { AC.router.go('tool/repeat/review'); } });
        }, function (e) { if (asResult) ctx.error(e); else ui.toast(L('repeat.markersFailed', { msg: U.errMsg(e) }), { kind: 'err' }); });
      }
      function markFlags(flags) {
        putMarkers(flags.map(function (f) {
          return { t: f.t0, end: f.t1, color: 4, name: L('repeat.flagMarkName', { text: words(f.text, 5) }), comment: L('repeat.flagMarkComment', { label: f.label || '' }) };
        }), FLAG_TAG, AC.seq.locked() || '').then(function (r) {
          ui.toast(L('repeat.flagsMade', { n: (r && r.n) || flags.length }), 'flag');
        }, function (e) { ui.toast(L('repeat.markersFailed', { msg: U.errMsg(e) }), { kind: 'err' }); });
      }

      /* ---- apply */
      function applyReview(items, doc, file) {
        if (st.mode === 'markers') { sendMarkers(items, doc, true); return; }
        ctx.run({ action: 'apply', review: file, title: L('repeat.jobApply'), params: {},
          apply: { seqId: (doc.seq && doc.seq.id) || undefined, unit: L('repeat.unit') },
          onError: function (err) {
            if (err && err.code === 'SEQ_CHANGED') { ctx.error(err, { onRetry: run }); return true; }
            return false;
          } });
      }

      /* ---- Dengar: the audio around one cut with the cut applied (engine preview via the worker) */
      function listen(it) {
        if (audio && playingId === it.id && !audio.paused) { audio.pause(); playingId = null; ui.toast(L('repeat.stopped'), 'pause'); return; }
        ui.toast(L('repeat.preparing'), 'clock');
        AC.engine.worker({ tool: def.engineTool, action: 'preview', title: L('repeat.listenJob'), record: false,
                           params: { cut: [it.t0, it.t1], pre: 2, post: 1.5, id: it.id } })
          .then(function (d) { play(d, it); }, function (e) {
            if (e && e.code === 'CANCELLED') return;
            ui.toast(L('repeat.prepFailed', { msg: U.errMsg(e) }), { kind: 'err' });
          });
      }
      function play(d, it) {
        if (!audio) {
          audio = ui.h('audio', { class: 'repeat-audio', preload: 'auto' });
          audio.addEventListener('ended', function () { playingId = null; });
          audio.addEventListener('error', function () { playingId = null; ui.toast(L('repeat.audioOpenFailed'), { kind: 'err' }); });
          document.body.appendChild(audio);
        }
        audio.src = fileUrl(d.path);
        audio.setAttribute('data-id', it.id);
        playingId = it.id;
        var p = audio.play();
        var ok = function () { ui.toast(L('repeat.playing', { sec: U.dtk(d.pre || 0) }), 'play'); };
        if (p && p.then) p.then(ok, function (e) { playingId = null; ui.toast(L('repeat.playFailed', { msg: String((e && e.message) || e) }), { kind: 'err' }); });
        else ok();
      }
    },

    onShow: function (ctx) { if (ctx._repeatTags) ctx._repeatTags(); },
    onSeq: function (lite, ctx) { if (ctx._repeatTags) ctx._repeatTags(); }
  });
})();
