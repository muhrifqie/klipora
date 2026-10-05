/* review.js: AC.ui.reviewList, the shared Review list (ux_design.md 4.4) for 1000+ rows.
   Virtualized (only visible rows are in the DOM), checkbox per row, confidence badge, context text with the removed
   part as <mark>, note line, counted filter chips, select all/none (visible rows), click-to-seek via bac_seek,
   keyboard (Up/Down or J/K move + seek, Space toggles, Enter seeks, A toggles all, Home/End, PageUp/PageDown).
   Review doc shape (SPEC section 2, ux 11.5): {tool, timebase, duration, items: [{id, t0, t1, kind, on, conf, label,
   ctx: {pre, post} | "text", note: "text" | {type: 'guard'|'ai'|'warn'|'info', text, words}, src, ...}]}. */
(function () {
  'use strict';
  var AC = window.AC, U = AC.util, ui = AC.ui;
  var seq = 0;

  // Read + validate a review file written by the engine. Returns the doc or throws a user-facing error.
  ui.loadReview = function (path) {
    var doc = AC.sys.readJSON(path, null);
    if (!doc || !Array.isArray(doc.items)) throw U.err('BAD_REVIEW', AC.t('review.badFile'), String(path));
    doc.items.forEach(function (it, i) {
      if (it.id === undefined) it.id = 'r' + i;
      it.t0 = Number(it.t0) || 0; it.t1 = Number(it.t1) || it.t0;
      if (it.on === undefined) it.on = true;
    });
    return doc;
  };

  // Unit word for n items. unit: string, plural object {one, other} or function(n) -> string; empty -> "bagian"/"parts".
  ui.unitText = function (unit, n) {
    if (typeof unit === 'function') return unit(n);
    if (unit && typeof unit === 'object') return n === 1 && unit.one ? unit.one : (unit.other || unit.one || '');
    return unit || AC.t('review.unit', { n: n });
  };

  function defaultCtx(it) {
    var mark = it.label !== undefined ? it.label : (it.word || AC.t('review.silentDur', { dur: U.dtk(it.t1 - it.t0) }));
    if (typeof it.ctx === 'string') return U.esc(it.ctx);
    var pre = it.ctx && it.ctx.pre, post = it.ctx && it.ctx.post;
    return (pre ? U.esc(pre) : '<i>' + U.esc(AC.t('review.start')) + '</i>') + ' <mark>' + U.esc(mark) + '</mark> ' + (post ? U.esc(post) : '<i>' + U.esc(AC.t('review.end')) + '</i>');
  }
  function defaultNote(it) {
    var n = it.note;
    if (!n && it.ai && it.ai.reason) n = { type: 'ai', text: it.ai.reason };
    if (!n) return null;
    if (typeof n === 'string') return { text: n, icon: 'info' };
    if (n.type === 'guard') return { kind: 'ok', icon: 'shield', text: n.text || AC.t('review.guardNote', { words: '"' + (n.words || []).join('", "') + '"' }) };
    if (n.type === 'ai') return { icon: 'spark', text: n.text || n.reason || '' };
    if (n.type === 'warn') return { kind: 'warn', icon: 'alert', text: n.text || '' };
    return { icon: 'info', text: n.text || '' };
  }
  function confTag(c) {
    if (typeof c !== 'number') return '';
    var p = Math.round(c * 100), kind = c >= 0.8 ? 'ok' : c >= 0.5 ? 'line' : 'warn';
    return '<span class="tag tag-' + kind + ' conf" title="' + U.esc(AC.t('review.confidence', { p: p })) + '">' + p + '%</span>';
  }
  var SRC = { transcript: 'review.srcTranscript', acoustic: 'review.srcAcoustic', ai: 'review.srcAi', rule: 'review.srcRule' };
  function defaultTags(it) {
    var h = '';
    if (it.src) h += '<span class="tag tag-line">' + U.esc(SRC[it.src] ? AC.t(SRC[it.src]) : it.src) + '</span>';
    if (it.mock) h += '<span class="tag tag-warn">' + U.esc(AC.t('review.sample')) + '</span>';
    return h + confTag(it.conf);
  }

  // opts: see docs/PANEL_API.md "Review list".
  ui.reviewList = function (o) {
    o = o || {};
    var id = 'rv' + (++seq);
    var doc = o.doc || { items: [] }, items = doc.items;
    var total = o.total || doc.duration || items.reduce(function (a, it) { return Math.max(a, it.t1); }, 0);
    // unit: string, plural object {one, other} or function(n) -> string (English needs the plural form).
    var verbOn = o.verbOn || AC.t('review.removed'), verbOff = o.verbOff || AC.t('review.kept');
    var unitFor = function (n) { return ui.unitText(o.unit, n); };
    var ctxFn = o.ctx || defaultCtx, noteFn = o.note || defaultNote, tagsFn = o.tags || defaultTags;
    var filters = [{ id: 'all', label: AC.t('common.all'), test: function () { return true; } },
                   { id: 'on', label: verbOn.charAt(0).toUpperCase() + verbOn.slice(1), test: function (it) { return it.on; } },
                   { id: 'off', label: verbOff.charAt(0).toUpperCase() + verbOff.slice(1), test: function (it) { return !it.on; } }].concat(o.filters || []);
    var st = { filter: 'all', active: -1, vis: [], tops: [], heights: [], h: { base: 54, note: 18, act: 32 }, measuredW: 0 };

    var title = ui.h('h2', { class: 'rv-title' });
    var ribbon = ui.ribbon({ size: 'lg', label: AC.t('review.ribbon'), onClick: function (t) { seekNearest(t); } });
    ribbon.el.title = AC.t('review.ribbonTitle');
    var legend = ui.h('div', { class: 'legend' });
    var chips = ui.h('div', { class: 'rv-tools', role: 'toolbar', 'aria-label': AC.t('review.filter') });
    var head = ui.h('div', { class: 'rv-head' }, [title, ribbon.el, legend, chips]);
    var all = ui.h('input', { type: 'checkbox', class: 'ck' });
    var allLbl = ui.h('span', { text: AC.t('review.selectAll') });
    var bulk = ui.h('div', { class: 'rv-bulk' }, [ui.h('label', {}, [all, allLbl])]);
    var bulkAct = o.bulkAction === null ? null : (o.bulkAction || { label: AC.t('review.sendMarkers'), run: sendMarkers });
    if (bulkAct) bulk.appendChild(ui.h('button', { class: 'linkbtn', type: 'button', text: bulkAct.label, on: { click: function () { bulkAct.run(ctrl); } } }));
    var spacer = ui.h('div', { class: 'rv-spacer' });
    var box = ui.h('div', { class: 'rv-scroll', role: 'listbox', tabIndex: 0, 'aria-multiselectable': 'true',
      'aria-label': AC.t('review.listAria', { unit: unitFor(items.length) }) }, [spacer]);
    var el = ui.h('div', { class: 'tool-pane is-fill rv-wrap' }, [head, bulk, box]);

    /* ---------- layout */
    function hasNote(it) { return !!noteFn(it); }
    function measure() {
      var w = box.clientWidth; if (!w) return false;
      if (w === st.measuredW) return true;
      st.measuredW = w;
      var probe = ui.h('div', { style: { position: 'absolute', visibility: 'hidden', left: '0', right: '0', top: '0' } });
      var fake = { t0: 0, t1: 1, on: true, label: 'x', ctx: { pre: 'a', post: 'b' } };
      probe.innerHTML = rowHTML(fake, -1, false, false) + rowHTML(fake, -1, true, false) + rowHTML(fake, -1, true, true);
      spacer.appendChild(probe);
      var rs = probe.children;
      st.h.base = rs[0].offsetHeight || 54; st.h.note = Math.max(0, rs[1].offsetHeight - rs[0].offsetHeight); st.h.act = Math.max(0, rs[2].offsetHeight - rs[1].offsetHeight);
      spacer.removeChild(probe);
      return true;
    }
    function layout() {
      st.vis = []; st.tops = []; st.heights = [];
      var f = filterById(st.filter), y = 0;
      for (var i = 0; i < items.length; i++) {
        if (!f.test(items[i])) continue;
        var h = st.h.base + (hasNote(items[i]) ? st.h.note : 0) + (i === st.active ? st.h.act : 0);
        st.vis.push(i); st.tops.push(y); st.heights.push(h); y += h + 2;
      }
      spacer.style.height = Math.max(0, y) + 'px';
    }
    function filterById(fid) { for (var i = 0; i < filters.length; i++) if (filters[i].id === fid) return filters[i]; return filters[0]; }

    /* ---------- rendering */
    function rowHTML(it, i, withNote, active) {
      var d = it.t1 - it.t0, note = withNote === undefined ? noteFn(it) : (withNote ? { text: 'x', icon: 'info' } : null);
      var acts = '';
      if (active) {
        acts = '<div class="rv-actions"><button class="btn btn-sm" type="button" data-act="seek">' + ui.icon('playhead') + U.esc(AC.t('review.jump')) + '</button>' +
          '<button class="btn btn-sm btn-ghost" type="button" data-act="toggle">' + U.esc(o.toggleText ? o.toggleText(it) : (it.on ? AC.t('review.keepThis') : AC.t('review.removeThis'))) + '</button>' +
          (o.rowActions || []).map(function (a, k) { return '<button class="btn btn-sm btn-ghost" type="button" data-act="x' + k + '">' + (a.icon ? ui.icon(a.icon) : '') + U.esc(a.label) + '</button>'; }).join('') + '</div>';
      }
      return '<div class="rv' + (it.on ? '' : ' is-off') + (active ? ' is-active' : '') + '" role="option" id="' + id + '-' + i + '" aria-selected="' + (it.on ? 'true' : 'false') + '" data-i="' + i + '">' +
        '<input type="checkbox" class="ck" tabindex="-1" aria-label="' + U.esc(verbOn.charAt(0).toUpperCase() + verbOn.slice(1)) + '"' + (it.on ? ' checked' : '') + '>' +
        '<div class="rv-main"><div class="rv-l1"><span class="tc">' + U.tcode(it.t0) + '</span>' + tagsFn(it) + '</div>' +
        '<span class="rv-ctx" data-i18n-skip>' + ctxFn(it) + '</span>' +
        (note ? '<span class="rv-note' + (note.kind ? ' is-' + note.kind : '') + '">' + ui.icon(note.icon || 'info') + '<span>' + U.esc(note.text) + '</span></span>' : '') + '</div>' +
        '<div class="rv-dur">' + U.dtk(d) + '<small>' + (it.on ? verbOn : verbOff) + '</small></div>' + acts + '</div>';
    }
    var rendered = '';
    function renderRows(force) {
      if (!measure()) return;
      var top = box.scrollTop, bottom = top + box.clientHeight, n = st.vis.length;
      var lo = 0, hi = n;                                  // first row whose bottom >= top
      while (lo < hi) { var mid = (lo + hi) >> 1; if (st.tops[mid] + st.heights[mid] < top) lo = mid + 1; else hi = mid; }
      var a = Math.max(0, lo - 6), b = a;
      while (b < n && st.tops[b] < bottom) b++;
      b = Math.min(n, b + 6);
      var key = a + ':' + b + ':' + st.active;
      if (!force && key === rendered) return;
      rendered = key;
      var h = '';
      for (var k = a; k < b; k++) {
        var i = st.vis[k];
        h += rowHTML(items[i], i, undefined, i === st.active).replace('class="rv', 'style="top:' + st.tops[k] + 'px" class="rv');
      }
      spacer.innerHTML = h || '<div class="rv-empty">' + U.esc(AC.t('review.noMatch')) + '</div>';
    }
    function renderHead() {
      var on = 0, removed = 0;
      items.forEach(function (it) { if (it.on) { on++; removed += it.t1 - it.t0; } });
      title.innerHTML = o.title ? o.title(on, items.length, removed) : AC.i18n.html('review.title', { n: on, unit: unitFor(on), verb: verbOn, total: items.length });
      legend.innerHTML = '<span><i class="lk"></i>' + U.esc(verbOff.charAt(0).toUpperCase() + verbOff.slice(1)) + '</span><span><i class="lc"></i>' + U.esc(verbOn.charAt(0).toUpperCase() + verbOn.slice(1)) + '</span>' +
        (o.legendExtra ? o.legendExtra(on, items.length, removed) :   // tools that do not cut (resize) replace "Hemat"
          (total ? '<span>' + AC.i18n.html('review.saves', { sec: U.dtk(removed), left: U.mmss(Math.max(0, total - removed)) }) + '</span>' : ''));
      chips.innerHTML = filters.map(function (f) {
        var n = 0; for (var i = 0; i < items.length; i++) if (f.test(items[i])) n++;
        return '<button class="chip" type="button" aria-pressed="' + (st.filter === f.id) + '" data-f="' + U.esc(f.id) + '">' + (f.icon ? ui.icon(f.icon) : '') + U.esc(f.label) + ' <span class="n">' + n + '</span></button>';
      }).join('');
      var vOn = st.vis.filter(function (i) { return items[i].on; }).length;
      all.checked = st.vis.length > 0 && vOn === st.vis.length; all.indeterminate = vOn > 0 && vOn < st.vis.length;
      allLbl.textContent = AC.t('review.selectAllN', { a: vOn, b: st.vis.length });
      ribbon.set({ total: total, spans: items.map(function (it, i) { return { t0: it.t0, t1: it.t1, off: !it.on, sel: i === st.active }; }),
                   playhead: st.active >= 0 && items[st.active] ? seekTime(items[st.active]) : null });
      box.setAttribute('aria-activedescendant', st.active >= 0 ? id + '-' + st.active : '');
      if (o.onCount) o.onCount(on, items.length, removed);
    }
    function refresh() { layout(); renderHead(); renderRows(true); }
    var changed = U.debounce(function () { if (o.onChange) o.onChange(ctrl); }, 120);

    /* ---------- actions */
    function seekTime(it) { return o.seekTime ? o.seekTime(it) : it.t0; }
    var doSeek = U.debounce(function (t) {
      if (o.seek === false) return;
      AC.host.call('bac_seek', t).then(function () { ui.toast(AC.t('review.seekToast', { tc: U.tcode(t) }), 'playhead'); },
        function (e) { ui.toast(AC.t('review.seekFail', { msg: U.errMsg(e) }), { kind: 'err' }); });
    }, 90);
    function visPos(i) { return st.vis.indexOf(i); }
    function ensureVisible(i) {
      var k = visPos(i); if (k < 0) return;
      var t = st.tops[k], b = t + st.heights[k];
      if (t < box.scrollTop) box.scrollTop = t;
      else if (b > box.scrollTop + box.clientHeight) box.scrollTop = b - box.clientHeight;
    }
    function setActive(i, seekToo) {
      if (i === undefined || i < 0 || i >= items.length) return;
      st.active = i; layout(); renderHead(); ensureVisible(i); renderRows(true);
      if (seekToo) doSeek(seekTime(items[i]));
    }
    function toggle(i, v) {
      var it = items[i]; if (!it) return;
      it.on = v === undefined ? !it.on : !!v;
      it.touched = true;   // engine review.carry_over() keeps manual choices across re-runs
      renderHead(); renderRows(true); changed();
    }
    function setAll(v) {
      st.vis.forEach(function (i) { if (items[i].on !== v) { items[i].on = v; items[i].touched = true; } });
      renderHead(); renderRows(true); changed();
    }
    function seekNearest(t) {
      var best = -1, bd = Infinity;
      items.forEach(function (it, i) { var d = Math.abs((it.t0 + it.t1) / 2 - t); if (d < bd) { bd = d; best = i; } });
      if (best >= 0) { if (visPos(best) < 0) { st.filter = 'all'; } setActive(best, true); }
    }
    function sendMarkers() {
      var on = items.filter(function (it) { return it.on; });
      if (!on.length) { ui.toast(AC.t('review.noneChecked')); return; }
      var tag = o.markerTag || '[Klipora-RV]';
      AC.host.json('bac_clearMarkersByTag', tag).then(function () {
        return AC.host.json('bac_addMarkers', on.map(function (it) { return { t: it.t0, end: it.t1, name: (o.markerName ? o.markerName(it) : unitFor(1) + ' ' + verbOn), tag: tag, color: 1 }; }));
      }).then(function (r) { ui.toast(AC.t('review.markersMade', { n: r && r.n || on.length }), 'marker'); },
        function (e) { ui.toast(AC.t('review.markersFail', { msg: U.errMsg(e) }), { kind: 'err' }); });
    }

    /* ---------- events */
    chips.addEventListener('click', function (e) {
      var b = e.target.closest('[data-f]'); if (!b) return;
      st.filter = b.getAttribute('data-f'); box.scrollTop = 0; refresh();
    });
    all.addEventListener('change', function () { setAll(all.checked); });
    box.addEventListener('scroll', function () { requestAnimationFrame(function () { renderRows(false); }); });
    box.addEventListener('click', function (e) {
      var row = e.target.closest('.rv'); if (!row) return;
      var i = Number(row.getAttribute('data-i'));
      var act = e.target.closest('[data-act]');
      if (act) {
        var a = act.getAttribute('data-act');
        if (a === 'seek') doSeek(seekTime(items[i]));
        else if (a === 'toggle') toggle(i);
        else if (a.charAt(0) === 'x' && o.rowActions) { var ra = o.rowActions[Number(a.slice(1))]; if (ra) ra.run(items[i], ctrl); }
        return;
      }
      if (e.target.classList.contains('ck')) { e.preventDefault(); toggle(i); if (st.active !== i) { st.active = i; refresh(); } return; }
      box.focus();
      setActive(i, true);
    });
    box.addEventListener('keydown', function (e) {
      var k = (e.key || '').toLowerCase(), pos = visPos(st.active), n = st.vis.length;
      if (!n) return;
      var go = function (p) { e.preventDefault(); setActive(st.vis[U.clamp(p, 0, n - 1)], true); };
      if (k === 'arrowdown' || k === 'j') go(pos < 0 ? 0 : pos + 1);
      else if (k === 'arrowup' || k === 'k') go(pos < 0 ? 0 : pos - 1);
      else if (k === 'home') go(0);
      else if (k === 'end') go(n - 1);
      else if (k === 'pagedown') go(pos + 10);
      else if (k === 'pageup') go(pos - 10);
      else if (k === ' ' || k === 'spacebar') { e.preventDefault(); if (st.active < 0) setActive(st.vis[0], false); else toggle(st.active); }
      else if (k === 'enter') { e.preventDefault(); if (st.active >= 0) doSeek(seekTime(items[st.active])); }
      else if (k === 'a' && !e.ctrlKey && !e.altKey) { e.preventDefault(); setAll(!st.vis.every(function (i) { return items[i].on; })); }
    });
    if (window.ResizeObserver) new ResizeObserver(function () { if (box.clientWidth !== st.measuredW) { st.measuredW = 0; if (measure()) refresh(); } else renderRows(false); }).observe(box);
    var onTheme = function () { st.measuredW = 0; if (box.isConnected) { measure(); refresh(); } };
    AC.bus.on('theme', onTheme);

    var ctrl = {
      el: el, box: box,
      doc: function () { return doc; },
      items: function () { return items; },
      selected: function () { return items.filter(function (it) { return it.on; }); },
      ranges: function () { return items.filter(function (it) { return it.on; }).map(function (it) { return [it.t0, it.t1]; }); },
      count: function () { return items.filter(function (it) { return it.on; }).length; },
      setFilter: function (f) { st.filter = f; refresh(); },
      setActive: function (i, s) { setActive(i, s); },
      toggle: toggle, setAll: setAll,
      refresh: refresh,
      focus: function () { box.focus(); },
      // Write the edited doc (only `on` flags change, plus `touched: true` on rows the user changed) back to its file.
      save: function (path) {
        var p = path || o.file;
        if (!p) throw U.err('NO_FILE', AC.t('review.noFile'));
        AC.sys.writeJSON(p, doc);
        return p;
      },
      destroy: function () { AC.bus.off('theme', onTheme); }
    };
    // First paint once attached (needs real widths).
    requestAnimationFrame(function () { measure(); refresh(); });
    layout(); renderHead();
    return ctrl;
  };
})();
