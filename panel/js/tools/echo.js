/* echo.js: developer tool (hidden unless Settings > Developer > Developer mode). Runs the engine tool "echo"
   end to end through the framework: job runner -> progress -> review list -> engine "apply" -> plan -> result.
   It is also the reference implementation for tool agents. By default it never touches the project
   ("Apply to timeline" off = dry run: the plan is only drawn). Texts: locale keys echo.* (registry via text(t)). */
(function () {
  'use strict';
  var AC = window.AC, U = AC.util, ui = AC.ui;

  // Built at render time so the labels follow the UI language.
  function presets() {
    return [
      { value: 'cepat', label: AC.t('echo.pFast'), hint: AC.t('echo.pFastHint', { a: U.dec(0.3, 1), b: 5 }), values: { seconds: 0.3, every: 5 } },
      { value: 'normal', label: AC.t('echo.pNormal'), hint: AC.t('echo.pNormalHint', { a: 1, b: 3 }), values: { seconds: 1, every: 3 } },
      { value: 'banyak', label: AC.t('echo.pMany'), hint: AC.t('echo.pManyHint', { b: U.dec(0.5, 1) }), values: { seconds: 1, every: 0.5 } }
    ];
  }
  function unit(n) { return AC.t('echo.unit', { n: n }); }

  AC.tools.register({
    id: 'echo', group: 'dev', order: 1, hidden: true,
    icon: 'bug', badges: ['dev'],
    text: function (t) {
      return {
        title: t('echo.title'), tab: 'Echo', desc: t('echo.desc'), lead: t('echo.lead'), cta: t('echo.cta'),
        review: { unit: unit },
        next: [{ tool: 'fillers', reason: t('echo.nextFillers') }, { tool: 'captions', reason: t('echo.nextCaptions') }]
      };
    },
    defaults: { preset: 'cepat', seconds: 0.3, every: 5, action: 'analyze', useAudio: false, apply: false },

    render: function (el, ctx) {
      var st = ctx.state;
      el.appendChild(ui.h('div', { class: 'tool-hero', html: '<span class="tool-ic">' + ui.icon('bug') + '</span><div><p>' + U.esc(ctx.def.lead) + '</p><div class="tags">' + ui.badge('dev') + '</div></div>' }));

      var gaya = ui.section({ title: AC.t('ui.style') });
      var pr = ui.presets({ options: presets(), value: st.preset, ariaLabel: AC.t('echo.styleAria'), customHint: AC.t('echo.customHint'), onChange: function (v, p) {
        st.preset = v; st.seconds = p.values.seconds; st.every = p.values.every; secs.set(st.seconds, true); every.set(st.every, true); ctx.save(); adv.setAside('');
      } });
      gaya.add(pr);
      el.appendChild(gaya.el);

      var adv = ui.advanced({ key: 'echo' });
      function manual() { st.preset = pr.sync({ seconds: st.seconds, every: st.every }); adv.setAside(st.preset ? '' : AC.t('common.custom')); ctx.save(); }
      var sec = ' ' + AC.t('unit.sec');
      var secs = ui.slider({ label: AC.t('echo.duration'), min: 0.2, max: 10, step: 0.1, value: st.seconds, unit: sec, help: AC.t('echo.durationHelp'),
                             onInput: function (v) { st.seconds = v; manual(); } });
      var every = ui.slider({ label: AC.t('echo.every'), min: 0.5, max: 20, step: 0.5, value: st.every, unit: sec, help: AC.t('echo.everyHelp'),
                              onInput: function (v) { st.every = v; manual(); } });
      var audio = ui.switch({ label: AC.t('echo.audio'), help: AC.t('echo.audioHelp'), checked: st.useAudio, onChange: function (v) { st.useAudio = v; ctx.save(); } });
      adv.add([secs, every, audio]);
      el.appendChild(adv.el);

      var hasil = ui.section({ title: AC.t('echo.result') });
      hasil.add(ui.radioCards({ ariaLabel: AC.t('echo.actionAria'), value: st.action, onChange: function (v) { st.action = v; ctx.save(); paintDock(); }, options: [
        { value: 'analyze', title: AC.t('echo.aReview'), desc: AC.t('echo.aReviewDesc') },
        { value: 'slow', title: AC.t('echo.aSlow'), desc: AC.t('echo.aSlowDesc') },
        { value: 'fail', title: AC.t('echo.aFail'), desc: AC.t('echo.aFailDesc') }
      ] }));
      hasil.add(ui.switch({ label: AC.t('echo.apply'), help: AC.t('echo.applyHelp'), checked: st.apply, onChange: function (v) { st.apply = v; ctx.save(); } }));
      el.appendChild(hasil.el);

      function paintDock() {
        var label = st.action === 'slow' ? AC.t('echo.runSlow') : st.action === 'fail' ? AC.t('echo.runFail') : ctx.def.cta;
        ctx.dock({ primary: { label: label, icon: 'play', onClick: run } });
      }
      paintDock();

      function run() {
        if (st.action === 'slow') {
          ctx.run({ action: 'slow', params: { seconds: Math.max(3, st.seconds * 5) }, title: AC.t('echo.aSlow'), onResult: function (d, task) {
            ctx.result({ title: AC.t('common.done'), sub: AC.t('echo.slowDone', { t: U.dtk(task.elapsed()) }), statsList: [{ label: AC.t('echo.waited'), value: U.dtk(d.slept || 0), accent: true }] });
          } });
          return;
        }
        if (st.action === 'fail') { ctx.run({ action: 'fail', params: { msg: AC.t('echo.failMsg') }, title: AC.t('echo.aFail') }); return; }
        ctx.run({ action: 'analyze', title: AC.t('echo.searching'), params: { seconds: st.seconds, every: st.every, use_audio: !!st.useAudio }, onResult: function (data) {
          ctx.review({ file: data.review, unit: unit,
            filters: [{ id: 'low', label: AC.t('echo.lowConf'), icon: 'alert', test: function (it) { return typeof it.conf === 'number' && it.conf < 0.7; } }],
            markerTag: '[Klipora-ECHO]',
            primary: { label: function (n) { return AC.t(st.apply ? 'echo.cutN' : 'echo.countN', { n: n, unit: unit(n) }); }, onApply: applyReview } });
        } });
      }

      function applyReview(items, doc, file) {
        ctx.run({ action: 'apply', review: file, title: AC.t('echo.computing'), onResult: function (data, task) {
          var plan = data.plan || { ranges: [] };
          if (st.apply) { ctx.applyPlan(plan, { unit: unit, name: undefined }); return; }
          var removed = (plan.ranges || []).reduce(function (a, r) { return a + r[1] - r[0]; }, 0), dur = doc.duration || 0;
          ctx.result({ title: AC.t('echo.planReady'), sub: AC.t('echo.planReadySub'),
            stats: { before: dur, after: Math.max(0, dur - removed) }, ribbon: { total: dur, spans: plan.ranges || [] },
            legend: [AC.t('echo.kept'), AC.t('apply.summary', { n: (plan.ranges || []).length, t: U.dtk(removed) })],
            onEdit: function () { ctx.go('review'); } });
        } });
      }
    }
  });

  AC.palette.add({ g: 'actions', ic: 'bug', get l() { return AC.t('echo.palLabel'); }, get d() { return AC.t('echo.palDetail'); }, dev: true, run: function () { AC.router.go('tool/echo'); } });
})();
