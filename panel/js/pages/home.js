/* home.js: Home (ux_design.md 2.3). Source card of the active sequence (or the empty state), recipe carousel
   (placeholders), the 4 tool groups with every registered tool + badges, and Recent (last 2 jobs). */
(function () {
  'use strict';
  var AC = window.AC, U = AC.util, ui = AC.ui;
  var els = {}, src = null;

  function renderGroups() {
    els.groups.innerHTML = '';
    AC.tools.groups.forEach(function (g) {
      var list = AC.tools.inGroup(g.id);
      if (!list.length) return;
      var sec = ui.section({ title: g.name, aside: AC.t('home.toolCount', { n: list.length }) });
      var box = ui.h('div', { class: 'tools' });
      list.forEach(function (d) {
        var b = ui.h('button', { class: 'tool', type: 'button', 'data-go': 'tool/' + d.id });
        b.innerHTML = '<span class="tool-ic">' + ui.icon(d.icon) + '</span><span class="tool-txt"><span class="tool-name">' + U.esc(d.title) + '</span><span class="tool-desc">' + U.esc(d.desc) + '</span></span><span class="tool-end">' + (d.badges || []).map(ui.badge).join('') + '</span>';
        box.appendChild(b);
      });
      sec.add(box);
      els.groups.appendChild(sec.el);
    });
  }

  function renderRecipes() {
    els.recipes.innerHTML = '';
    AC.recipes.forEach(function (r) {
      var b = ui.h('button', { class: 'recipe', type: 'button' });
      b.innerHTML = '<div class="recipe-top"><span class="recipe-name">' + U.esc(r.name) + '</span><span class="go">' + ui.icon('play') + '</span></div>' +
        '<div class="recipe-steps">' + r.steps.map(function (s, i) { var d = AC.tools.get(s); return (i ? '<span class="sep"></span>' : '') + '<span class="st" title="' + U.esc(d ? d.title : s) + '">' + ui.icon(d ? d.icon : 'spark') + '</span>'; }).join('') + '</div>' +
        '<div class="recipe-meta">' + U.esc(r.meta) + '</div>';
      b.addEventListener('click', function () { AC.runRecipe(r); });
      els.recipes.appendChild(b);
    });
  }

  function renderRecent() {
    var rows = AC.history.recent(2);
    els.recent.innerHTML = '';
    if (!rows.length) { els.recent.appendChild(ui.h('p', { class: 'help', text: AC.t('home.noRecent') })); return; }
    rows.forEach(function (r) {
      var d = AC.tools.get(r.tool);
      var ic = r.status === 'ok' ? ui.icon('check', 'ok-ic') : r.status === 'running' ? ui.icon('clock') : ui.icon(r.status === 'cancelled' ? 'x' : 'alert', 'err-ic');
      var b = ui.h('button', { class: 'recent-i', type: 'button', 'data-go': d ? 'tool/' + d.id : 'history' });
      // r.sub was recorded in the language of that run: user content, not re-translated.
      b.innerHTML = ic + '<span><b>' + U.esc((d && d.title) || r.title || r.tool) + '</b> <span class="muted" data-i18n-skip>' + U.esc(r.sub || '') + '</span></span><span class="t">' + U.wibTime(r.started) + '</span>';
      els.recent.appendChild(b);
    });
  }

  function paintSeq(s) {
    els.empty.hidden = !!s; src.el.hidden = !s;
    if (s) checkTranscript();
  }
  // The "transcript saved" tag needs the clip paths (full info): fetched lazily, only while Home is visible.
  var checkTranscript = U.debounce(function () {
    if (AC.router.current().screen !== 'home' || !AC.seq.peek()) return;
    AC.seq.current().then(function (full) {
      src.setTags(full && AC.seq.hasTranscript(full) ? ui.tag(AC.t('home.transcriptSaved'), 'ok', 'check') : '');
    }, function () { /* keep the card as is */ });
  }, 600);

  AC.router.page('home', {
    title: function () { return AC.t('home.title'); }, back: null,
    render: function (el) {
      src = ui.sourceCard({ scope: false, ribbon: true, tags: true });
      els.empty = ui.h('div', { class: 'card home-empty' });
      els.empty.appendChild(ui.empty({ icon: 'seq', title: AC.t('palette.noSeq'), text: AC.t('home.emptyText'),
        steps: [AC.t('home.step1'), AC.t('home.step2'), AC.t('home.step3')],
        action: { label: AC.t('ui.redetect'), icon: 'refresh', onClick: function () { AC.seq.refresh().then(function (s) { if (!s) ui.toast(AC.t('home.stillNoSeq'), 'seq'); }); } } }));
      el.appendChild(src.el); el.appendChild(els.empty);
      var rec = ui.section({ title: AC.t('home.recipes'), action: { label: AC.t('home.recipesEdit'), onClick: function () { ui.toast(AC.t('home.recipesSoon'), 'layers'); } } });
      els.recipes = ui.h('div', { class: 'recipes' }); rec.add(els.recipes); el.appendChild(rec.el);
      els.groups = ui.h('div', { class: 'sec', style: { gap: 'calc(var(--gap) + 6px)' } }); el.appendChild(els.groups);
      var last = ui.section({ title: AC.t('home.recent'), action: { label: AC.t('history.title'), onClick: function () { AC.router.go('history'); } } });
      els.recent = ui.h('div', { class: 'recent' }); last.add(els.recent); el.appendChild(last.el);
      renderRecipes(); renderGroups(); renderRecent();
      paintSeq(AC.seq.peek());
      AC.seq.on('change', paintSeq);
      AC.history.on('change', renderRecent);
      AC.bus.on('tools', renderGroups);
      AC.bus.on('health', function () { checkTranscript(); });
      AC.settings.on('change', function (k) { if (k === 'developer' || k === 'ai' || k === '*') { renderGroups(); checkTranscript(); } });
    },
    onShow: function () { renderRecent(); paintSeq(AC.seq.peek()); src.refresh(); }
  });
})();
