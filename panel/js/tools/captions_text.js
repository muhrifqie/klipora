/* captions_text.js: Auto Caption "Teks" tab (AC.cap.text). One card per caption page (engine doc.pages) with the
   words as chips; hidden words (fillers, user-hidden) are slotted back in by time so they can be shown again.
   Chips: click = select + word popover (text, colour, highlight pill, size, bold/italic, emoji, hide, sensor, page /
   line break, reset, "terapkan ke semua kata yang sama"), double-click / Enter / F2 = inline edit, Shift+click =
   range select, Ctrl+click = add/remove -> selection bar. Keys: Left/Right move, Del hides, H toggles emphasis.
   Above the list: AI cleanup diff (accept / reject per word or all, or the shared Tinjau list), orphan-merge and
   low-confidence suggestions, find & replace (Ctrl F). Below: Kamus (per video + all videos) and text rules.
   Every change is an engine op through AC.cap.op (serial queue, undo). Long docs: content-visibility + keyed
   re-render of changed cards only. */
(function () {
  'use strict';
  var AC = window.AC, U = AC.util, ui = AC.ui, cap = AC.cap, S = cap.S;

  var COLORS = ['#FFFFFF', '#FFE600', '#22E58B', '#38BDF8', '#FF4D4D', '#F472B6', '#A78BFA'];
  var PILLS = ['#7C3AED', '#16A34A', '#E11D48', '#F59E0B', '#0EA5E9'];
  var EMOJIS = ['\uD83D\uDD25', '\uD83D\uDCB0', '\u2705', '\uD83D\uDC49', '\uD83D\uDE02', '\u2764\uFE0F', '\u26A1', '\uD83C\uDFAF', '\uD83D\uDE80', '\u26A0\uFE0F'];

  var T = { el: null, cards: {}, sigs: {}, order: [], sel: [], anchor: null, sug: null, find: { q: '', hits: [], at: -1 }, pop: null, editing: null };

  cap.text = {
    create: function (panel) {
      // render() can run again (language switch): drop the cards, popover and selection of the previous render
      closePop();
      T.el = panel; T.cards = {}; T.sigs = {}; T.editing = null; curCard = null; roved = null;
      // toolbar
      var bAi = ui.button({ label: AC.t('cap.text.aiCleanup'), icon: 'spark', small: true, onClick: aiCleanup });
      var bFind = ui.button({ label: AC.t('cap.text.findReplace'), icon: 'search', small: true, kind: 'ghost', onClick: function () { cap.text.find(); } });
      T.bar = ui.h('div', { class: 'cap-tx-bar' }, [bAi, bFind]);
      panel.appendChild(T.bar);
      T.findBox = buildFind();
      panel.appendChild(T.findBox);
      T.sugBox = ui.h('div', { class: 'sec', style: { gap: '6px' } });
      panel.appendChild(T.sugBox);
      T.list = ui.h('div', { class: 'cap-lines', role: 'list', 'aria-label': AC.t('cap.text.listAria') });
      panel.appendChild(T.list);
      T.selbar = buildSelbar();
      panel.appendChild(T.selbar);
      panel.appendChild(buildGlossary());
      panel.appendChild(buildRules());
      T.list.addEventListener('click', onListClick);
      T.list.addEventListener('dblclick', onListDbl);
      T.list.addEventListener('keydown', onListKey);
      cap.bus.on('doc', render);
      cap.bus.on('time', function (t, i) { markCurrent(i, true); });
      cap.bus.on('templates', render);
      render();
    },
    find: function () { openFind(); },
    closePop: function () { closePop(); },
    onKey: function (e, typing) { return onKey(e, typing); },
    selection: function () { return T.sel.slice(); },
    select: function (ids) { setSel(ids); },
    suggestions: function () { return T.sug; },
    chipIds: function () { return U.$$('.cap-wchip', T.list).map(function (c) { return c.dataset.id; }); }
  };

  /* ---------------------------------------------------------------- data: pages with hidden words slotted in */
  function pageRows() {
    var d = S.doc; if (!d) return [];
    var pages = d.pages || [], starts = pages.map(function (p) { var w = S.words[p.lines[0][0]]; return w ? w.t0 : p.t0; });
    var extra = {};
    (d.words || []).forEach(function (w) {
      if (!w.hide || w.merged) return;
      var lo = 0, hi = starts.length - 1, k = 0;
      while (lo <= hi) { var m = (lo + hi) >> 1; if (starts[m] <= w.t0) { k = m; lo = m + 1; } else hi = m - 1; }
      (extra[k] = extra[k] || []).push(w);
    });
    return pages.map(function (p, i) {
      var lines = p.lines.map(function (l) { return l.slice(); });
      (extra[i] || []).forEach(function (h) {
        var placed = false;
        for (var li = 0; li < lines.length && !placed; li++) {
          for (var j = 0; j < lines[li].length; j++) {
            var w = S.words[lines[li][j]];
            if (w && w.t0 > h.t0) { lines[li].splice(j, 0, h.id); placed = true; break; }
          }
        }
        if (!placed) lines[lines.length - 1].push(h.id);
      });
      return { page: p, i: i, lines: lines };
    });
  }
  function sugFor(id) {
    if (!T.sug) return null;
    for (var i = 0; i < T.sug.items.length; i++) if (T.sug.items[i].ids.indexOf(id) >= 0) return T.sug.items[i];
    return null;
  }
  function chipState(w) {
    var st = w.style || {}, e = w.edit || {}, cls = ['cap-wchip'];
    if (w.hide) cls.push('is-hidden');
    if ((w.p || 1) < 0.5 && !w.hide) cls.push('is-low');
    if (e.src === 'ai') cls.push('is-ai'); else if (e.text !== undefined || e.brk) cls.push('is-edited');
    if (sugFor(w.id)) cls.push('is-sug');
    if (st.color || st.active_color) cls.push('is-emph');
    if (st.bold) cls.push('is-b');
    if (st.italic) cls.push('is-i');
    if (st.scale && st.scale > 1.1) cls.push('is-big');
    return cls.join(' ');
  }
  function chipHtml(w) {
    var st = w.style || {}, h = '<span>' + U.esc(w.text || w.raw || '') + '</span>';
    if (st.color) h += '<i class="cap-wsw" style="--c:' + U.esc(st.color) + '"></i>';
    if (st.pill) h += '<i class="cap-wpill" style="--c:' + U.esc(st.pill) + '"></i>';
    if (st.emoji) h += '<i class="cap-wemo">' + U.esc(st.emoji) + '</i>';
    if (w.censor && !w.hide) h += '<i class="cap-wtag">' + U.esc(AC.t('cap.text.censorTag')) + '</i>';
    if (w.edit && w.edit.brk === 'page') h = '<i class="cap-wtag" title="' + U.esc(AC.t('cap.text.pageBreakHere')) + '">\u00b6</i>' + h;
    if (w.edit && w.edit.brk === 'line') h = '<i class="cap-wtag" title="' + U.esc(AC.t('cap.text.lineBreakHere')) + '">\u21b5</i>' + h;
    return h;
  }
  function chipTitle(w) {
    var parts = [AC.t('cap.text.timeRange', { a: U.tcode(w.t0), b: U.tcode(w.t1) })];
    if (typeof w.p === 'number') parts.push(AC.t(w.p < 0.5 ? 'cap.text.confLow' : 'cap.text.conf', { p: String(Math.round(w.p * 100)) }));
    if (w.auto) parts.push(AC.t('cap.text.tipGlossary', { old: w.auto.old, 'new': w.auto.text }));
    if (w.edit && w.edit.text !== undefined && w.raw) parts.push(AC.t('cap.text.tipOriginal', { text: w.raw }));
    var s = sugFor(w.id); if (s) parts.push(AC.t('cap.text.tipAi', { old: s.old, 'new': s['new'] }));
    if (w.hide) parts.push(AC.t(w.filler && !(w.edit && w.edit.hide) ? 'cap.text.tipFillerHidden' : 'cap.text.tipHidden'));
    return parts.join('\n');
  }

  /* ---------------------------------------------------------------- render (keyed: only changed cards) */
  function render() {
    if (!T.list) return;
    // AI suggestions die with their words (other sequence, or the text was changed since)
    if (T.sug) {
      T.sug.items = T.sug.items.filter(function (s) {
        return s.ids.every(function (id) { return S.words[id] && !S.words[id].hide; }) &&
          s.ids.map(function (id) { return (S.words[id].text || '').trim(); }).join(' ') === s.old;
      });
      if (!T.sug.items.length) T.sug = null;
    }
    var rows = pageRows(), seen = {}, order = [];
    var eff = cap.eff(), oneWord = eff && eff.timing && eff.timing.mode === 'word';
    rows.forEach(function (r) {
      var p = r.page, key = p.id;
      var ws = r.lines.map(function (l) { return l.map(function (id) { var w = S.words[id] || {}; return [id, w.text, w.hide, w.censor, w.p < 0.5, w.style, w.edit, !!sugFor(id)]; }); });
      var sig = JSON.stringify([AC.i18n.lang(), p.i, p.t0, p.t1, p.fast, p.pos, p.y, ws, oneWord]);
      var card = T.cards[key];
      if (!card || T.sigs[key] !== sig) {
        var fresh = buildCard(r, oneWord);
        if (card && card.parentNode) card.parentNode.replaceChild(fresh, card);
        card = fresh; T.cards[key] = card; T.sigs[key] = sig;
      }
      card.dataset.i = String(r.i);
      seen[key] = 1; order.push(card);
    });
    Object.keys(T.cards).forEach(function (k) { if (!seen[k]) { var c = T.cards[k]; if (c.parentNode) c.parentNode.removeChild(c); delete T.cards[k]; delete T.sigs[k]; } });
    // order: append moves nodes only when they are out of place
    var cur = T.list.firstChild;
    order.forEach(function (c) { if (c === cur) cur = cur.nextSibling; else T.list.insertBefore(c, cur); });
    T.sel = T.sel.filter(function (id) { return S.words[id]; });
    paintSel();
    markCurrent(cap.preview ? cap.preview.page() : -1, false);
    paintSuggestions();
    if (T.find.q) runFind(false);
    paintGlossary();
    paintRules();
  }
  function buildCard(r, oneWord) {
    var p = r.page, nWords = 0;
    var card = ui.h('div', { class: 'cap-line', role: 'listitem', dataset: { page: p.id } });
    var meta = ui.h('div', { class: 'cap-line-meta' });
    meta.appendChild(ui.h('button', { class: 'tc', type: 'button', text: U.tcode(p.t0), title: AC.t('cap.text.seekTitle'), dataset: { act: 'seek' } }));
    meta.appendChild(ui.h('span', { text: U.dtk(p.t1 - p.t0) }));
    var tag = function (cls, title, text) { return ui.h('span', { class: 'tag ' + cls, title: title, text: text }); };
    if (p.fast) meta.appendChild(tag('tag-warn', AC.t('cap.text.fastTitle'), AC.t('cap.text.fast')));
    if (p.pos === 'manual') meta.appendChild(tag('tag-line', AC.t('cap.text.posManualTitle'), AC.t('cap.text.posManual', { y: String(Math.round(p.y)) })));
    else if (p.pos === 'auto') meta.appendChild(tag('tag-ai', AC.t('cap.text.posAutoTitle'), AC.t('cap.text.posAuto')));
    if (p.low && p.low.length) meta.appendChild(tag('tag-warn', AC.t('cap.text.lowTitle'), AC.t('cap.text.lowCount', { n: p.low.length })));
    meta.appendChild(ui.h('span', { class: 'sp' }));
    if (r.i > 0 && !oneWord) meta.appendChild(ui.h('button', { class: 'ibtn', type: 'button', title: AC.t('cap.text.joinPrev'), 'aria-label': AC.t('cap.text.joinPrev'), html: ui.icon('join'), dataset: { act: 'join' } }));
    card.appendChild(meta);
    r.lines.forEach(function (ln) {
      var row = ui.h('div', { class: 'cap-wl cap-wchips', 'data-i18n-skip': true });   // transcript words = user content
      ln.forEach(function (id) {
        var w = S.words[id]; if (!w) return;
        nWords++;
        var b = ui.h('button', { type: 'button', class: chipState(w), 'aria-pressed': T.sel.indexOf(id) >= 0 ? 'true' : 'false', dataset: { id: id }, title: chipTitle(w), tabIndex: -1 });
        b.innerHTML = chipHtml(w);
        row.appendChild(b);
      });
      card.appendChild(row);
    });
    card.setAttribute('aria-label', AC.t('cap.text.cardAria', { i: r.i + 1, tc: U.tcode(p.t0), n: nWords }));
    return card;
  }
  function chipEl(id) { return T.list.querySelector('.cap-wchip[data-id="' + cssEsc(id) + '"]'); }
  function cssEsc(s) { return String(s).replace(/["\\]/g, '\\$&'); }

  var curCard = null;
  function markCurrent(i, scroll) {
    if (curCard) curCard.classList.remove('is-cur');
    curCard = null;
    var pg = S.doc && S.doc.pages[i]; if (!pg) return;
    curCard = T.cards[pg.id];
    if (!curCard) return;
    curCard.classList.add('is-cur');
    if (scroll && S.tab === 'teks' && !T.pop) revealCard(curCard);
  }
  function revealCard(card) {
    var view = document.getElementById('view'); if (!view || !card.offsetParent) return;
    var vr = view.getBoundingClientRect(), cr = card.getBoundingClientRect();
    var prev = document.querySelector('.cap-preview'), top = vr.top;
    if (prev && window.innerWidth < 600) top = Math.max(top, prev.getBoundingClientRect().bottom);
    if (cr.top < top + 4) view.scrollTop -= (top + 4 - cr.top);
    else if (cr.bottom > vr.bottom - 4) view.scrollTop += (cr.bottom - vr.bottom + 4);
  }

  /* ---------------------------------------------------------------- selection */
  function visibleIds() { return U.$$('.cap-wchip', T.list).map(function (c) { return c.dataset.id; }); }
  function setSel(ids, anchor) {
    T.sel = ids.filter(function (id, i) { return S.words[id] && ids.indexOf(id) === i; });
    if (anchor !== undefined) T.anchor = anchor;
    paintSel();
  }
  function paintSel() {
    U.$$('.cap-wchip[aria-pressed="true"]', T.list).forEach(function (c) { if (T.sel.indexOf(c.dataset.id) < 0) c.setAttribute('aria-pressed', 'false'); });
    T.sel.forEach(function (id) { var c = chipEl(id); if (c) c.setAttribute('aria-pressed', 'true'); });
    var n = T.sel.length;
    T.selbar.hidden = n < 2;
    if (n >= 2) T.selCount.textContent = AC.t('cap.text.selCount', { n: n });
    rove();
  }
  // Roving tabindex: exactly one chip (the last selected, else the first) is reachable with Tab.
  var roved = null;
  function rove() {
    var c = (T.sel.length && chipEl(T.sel[T.sel.length - 1])) || T.list.querySelector('.cap-wchip');
    if (roved && roved !== c) roved.tabIndex = -1;
    if (c) c.tabIndex = 0;
    roved = c;
  }
  function onListClick(e) {
    var act = e.target.closest('[data-act]');
    if (act && T.list.contains(act)) {
      var card = act.closest('.cap-line'), i = Number(card.dataset.i), pg = S.doc.pages[i];
      if (act.dataset.act === 'seek') cap.preview.showPage(i);
      if (act.dataset.act === 'join' && pg) cap.op({ op: 'break', ids: [pg.lines[0][0]], kind: 'join' }, { label: AC.t('cap.text.opJoin'), now: true });
      return;
    }
    var chip = e.target.closest('.cap-wchip'); if (!chip || T.editing) return;
    var id = chip.dataset.id;
    if (e.shiftKey && T.anchor) {
      var all = visibleIds(), a = all.indexOf(T.anchor), b = all.indexOf(id);
      if (a >= 0 && b >= 0) { setSel(all.slice(Math.min(a, b), Math.max(a, b) + 1)); chip.focus(); closePop(); return; }
    }
    if (e.ctrlKey || e.metaKey) {
      var s = T.sel.slice(), k = s.indexOf(id);
      if (k >= 0) s.splice(k, 1); else s.push(id);
      setSel(s, id); chip.focus(); closePop(); return;
    }
    setSel([id], id);
    chip.focus();
    var w = S.words[id];
    if (w) cap.preview.show(Math.min(w.t0 + 0.02, w.t1));
    openPop(chip);
  }
  function onListDbl(e) {
    var chip = e.target.closest('.cap-wchip'); if (!chip) return;
    closePop(); inlineEdit(chip);
  }
  function onListKey(e) {
    var chip = e.target.closest && e.target.closest('.cap-wchip');
    if (!chip || T.editing) return;
    if (e.key === 'Enter' || e.key === 'F2') { e.preventDefault(); closePop(); inlineEdit(chip); }
  }
  function onKey(e, typing) {
    if (typing || T.editing) return false;
    var k = e.key, a = document.activeElement;
    if (!T.sel.length && a && a.classList && a.classList.contains('cap-wchip') && /^(ArrowLeft|ArrowRight|Delete|h|H)$/.test(k)) setSel([a.dataset.id], a.dataset.id);
    if (!T.sel.length) return false;
    if (k === 'ArrowLeft' || k === 'ArrowRight' || (k === 'Tab' && document.activeElement && document.activeElement.classList.contains('cap-wchip'))) {
      var all = visibleIds(), i = all.indexOf(T.sel[T.sel.length - 1]);
      var back = k === 'ArrowLeft' || (k === 'Tab' && e.shiftKey);
      var n = all[U.clamp(i + (back ? -1 : 1), 0, all.length - 1)];
      e.preventDefault(); closePop(); setSel([n], n);
      var c = chipEl(n); if (c) { c.focus(); var w = S.words[n]; if (w) cap.preview.show(w.t0 + 0.02); }
      return true;
    }
    if (k === 'Delete') { e.preventDefault(); toggleHide(T.sel.slice()); return true; }
    if (k === 'h' || k === 'H') { e.preventDefault(); toggleEmph(T.sel.slice()); return true; }
    if (k === 'Escape' && T.sel.length) { setSel([]); return true; }
    return false;
  }

  function toggleHide(ids) {
    var allHidden = ids.every(function (id) { return S.words[id] && S.words[id].hide; });
    return cap.op({ op: 'hide', ids: ids, on: !allHidden }, { label: AC.t(allHidden ? 'cap.text.opShow' : 'cap.text.opHide'), now: true });
  }
  function emphColor() { var e = cap.eff(); return (e && e.highlight && e.highlight.color) || (e && e.emphasis && e.emphasis.color) || '#FFE600'; }
  function toggleEmph(ids) {
    var on = ids.every(function (id) { return S.words[id] && (S.words[id].style || {}).color; });
    return cap.op({ op: 'style', ids: ids, style: { color: on ? null : emphColor() } }, { label: AC.t('cap.text.opEmph'), now: true });
  }

  /* ---------------------------------------------------------------- inline edit */
  function inlineEdit(chip) {
    var id = chip.dataset.id, w = S.words[id]; if (!w) return;
    var inp = ui.h('input', { class: 'cap-wchip-edit', type: 'text', value: w.text || '', 'aria-label': AC.t('cap.text.editWordAria'), spellcheck: 'false' });
    inp.style.width = Math.max(6, (w.text || '').length + 2) + 'ch';
    chip.hidden = true; chip.parentNode.insertBefore(inp, chip.nextSibling);
    T.editing = inp;
    inp.focus(); inp.select();
    var done = false;
    function finish(save) {
      if (done) return; done = true; T.editing = null;
      var v = inp.value.trim();
      if (inp.parentNode) inp.parentNode.removeChild(inp);
      chip.hidden = false; chip.focus();
      if (save && v !== (w.text || '')) commitText(id, v);
    }
    inp.addEventListener('input', function () { inp.style.width = Math.max(6, inp.value.length + 2) + 'ch'; });
    inp.addEventListener('keydown', function (e) {
      if (e.key === 'Enter') { e.preventDefault(); e.stopPropagation(); finish(true); }
      else if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); finish(false); }
    });
    inp.addEventListener('blur', function () { finish(true); });
  }
  function commitText(id, text) {
    var w = S.words[id];
    if (!text) return cap.op({ op: 'hide', ids: [id], on: true }, { label: AC.t('cap.text.opHide'), now: true });
    var ops = [{ op: 'text', ids: [id], text: text }];
    if (w && w.hide) ops.push({ op: 'hide', ids: [id], on: false });
    return cap.op(ops, { label: AC.t('cap.text.opText'), now: true });
  }

  /* ---------------------------------------------------------------- word popover */
  function sameWordIds(w) {
    var n = cap.norm(w.text);
    if (!n) return [w.id];
    return (S.doc.words || []).filter(function (x) { return !x.merged && cap.norm(x.text) === n; }).map(function (x) { return x.id; });
  }
  function openPop(chip) {
    closePop();
    var ids = T.sel.length ? T.sel.slice() : [chip.dataset.id];
    var w = S.words[ids[0]]; if (!w) return;
    var multi = ids.length > 1, st = w.style || {};
    var same = multi ? ids : sameWordIds(w);
    var allCk = null;
    var targets = function () { return allCk && allCk.checked ? same : ids; };
    var pop = ui.h('div', { class: 'pop cap-pop is-open', role: 'dialog', 'aria-label': multi ? AC.t('cap.text.popAriaN', { n: ids.length }) : AC.t('cap.text.popAria', { word: w.text }) });
    // header: text field (single word) or a count
    var head = ui.h('div', { class: 'pop-h' });
    var txt = null;
    if (!multi) {
      txt = ui.h('input', { class: 'input', type: 'text', value: w.text || '', 'aria-label': AC.t('cap.text.wordText'), spellcheck: 'false' });
      txt.addEventListener('keydown', function (e) {
        if (e.key === 'Enter') { e.preventDefault(); e.stopPropagation(); saveText(); closePop(); }
      });
      head.appendChild(txt);
    } else head.appendChild(ui.h('b', { style: { flex: '1' }, text: AC.t('cap.text.selCount', { n: ids.length }) }));
    head.appendChild(ui.h('button', { class: 'ibtn', type: 'button', 'aria-label': AC.t('common.close'), html: ui.icon('x'), on: { click: function () { saveText(); closePop(); } } }));
    pop.appendChild(head);
    function saveText() {
      if (!txt) return;
      var v = txt.value.trim();
      if (v === (w.text || '')) return;
      if (allCk && allCk.checked && same.length > 1 && v) cap.op({ op: 'replace', find: w.text, replace: v, whole: true }, { label: AC.t('cap.text.opReplaceAll', { word: w.text }), now: true });
      else commitText(w.id, v);
    }
    // time + jump
    if (!multi) {
      var tr = ui.h('div', { class: 'pop-row' }, [ui.h('span', { text: AC.t('cap.text.time') }), ui.h('div', { class: 'cap-pop-t' })]);
      tr.lastChild.innerHTML = AC.i18n.html('cap.text.timeRangeHtml', { a: U.tcode(w.t0), b: U.tcode(w.t1) }) + (typeof w.p === 'number' ? ' \u00b7 ' + U.esc(AC.t('cap.text.conf', { p: String(Math.round(w.p * 100)) })) : '');
      pop.appendChild(tr);
    }
    // AI suggestion for this word
    var sg = !multi && sugFor(w.id);
    if (sg) {
      var box = ui.h('div', { class: 'cap-pop-sug', html: ui.icon('spark') + ' ' + AC.i18n.html('cap.text.aiSugHtml', { old: sg.old, 'new': sg['new'] }) });
      box.appendChild(ui.h('div', { class: 'btn-row' }, [
        ui.button({ label: AC.t('cap.text.accept'), small: true, icon: 'check', onClick: function () { acceptSug([sg]); closePop(); } }),
        ui.button({ label: AC.t('cap.text.reject'), small: true, kind: 'ghost', onClick: function () { rejectSug([sg]); closePop(); } })]));
      pop.appendChild(box);
    }
    var style = function (patch, label) { return cap.op({ op: 'style', ids: targets(), style: patch }, { label: label || AC.t('cap.text.opStyle'), now: true }); };
    pop.appendChild(row(AC.t('cap.text.color'), swatches(COLORS, st.color || null, function (c) { style({ color: c }, AC.t('cap.text.opColor')); })));
    pop.appendChild(row(AC.t('cap.text.pill'), swatches(PILLS, st.pill || null, function (c) { style({ pill: c }, AC.t('cap.text.opEmph')); }, { label: AC.t('cap.text.pill') })));
    var size = ui.segmented({ small: true, ariaLabel: AC.t('cap.text.opSize'), value: st.scale ? Math.round(st.scale * 100) / 100 : 1,
      options: [{ value: 1, label: '100%' }, { value: 1.25, label: '125%' }, { value: 1.5, label: '150%' }],
      onChange: function (v) { style({ scale: v === 1 ? null : v }, AC.t('cap.text.opSize')); } });
    pop.appendChild(row(AC.t('cap.text.size'), size.el));
    var bB = toggleBtn(AC.t('cap.text.bold'), 'bold', !!st.bold, function (on) { style({ bold: on || null }, AC.t('cap.text.bold')); });
    var bI = toggleBtn(AC.t('cap.text.italic'), null, !!st.italic, function (on) { style({ italic: on || null }, AC.t('cap.text.italic')); });
    pop.appendChild(row(AC.t('cap.text.font'), ui.h('div', { class: 'btn-row' }, [bB, bI])));
    var em = ui.h('div', { class: 'cap-emojis', role: 'radiogroup', 'aria-label': 'Emoji' });
    [''].concat(EMOJIS).forEach(function (x) {
      var b = ui.h('button', { type: 'button', role: 'radio', 'aria-checked': (st.emoji || '') === x ? 'true' : 'false', title: x ? 'Emoji ' + x : AC.t('cap.text.noEmoji'), text: x || '\u2205' });
      b.addEventListener('click', function () { style({ emoji: x || null }, 'Emoji'); closePop(); });
      em.appendChild(b);
    });
    pop.appendChild(row('Emoji', em));
    // actions
    var hidden = ids.every(function (id) { return S.words[id] && S.words[id].hide; });
    var censored = ids.every(function (id) { return S.words[id] && S.words[id].censor; });
    var acts = ui.h('div', { class: 'pop-acts' });
    acts.appendChild(ui.button({ label: AC.t(hidden ? 'cap.text.show' : 'cap.text.hide'), icon: 'eyeoff', small: true, onClick: function () { cap.op({ op: 'hide', ids: targets(), on: !hidden }, { label: AC.t(hidden ? 'cap.text.opShow' : 'cap.text.opHide'), now: true }); closePop(); } }));
    acts.appendChild(ui.button({ label: AC.t(censored ? 'cap.text.uncensor' : 'cap.text.censor'), small: true, onClick: function () { cap.op({ op: 'censor', ids: targets(), on: !censored }, { label: AC.t('cap.text.censor'), now: true }); closePop(); } }));
    if (!multi) {
      var brk = (w.edit || {}).brk;
      if (brk === 'page' || brk === 'line') acts.appendChild(ui.button({ label: AC.t('cap.text.removeBreak'), icon: 'join', small: true, onClick: function () { cap.op({ op: 'break', ids: [w.id], kind: null }, { label: AC.t('cap.text.removeBreak'), now: true }); closePop(); } }));
      else {
        acts.appendChild(ui.button({ label: AC.t('cap.text.splitHere'), icon: 'split', small: true, title: AC.t('cap.text.splitTitle'), onClick: function () { cap.op({ op: 'break', ids: [w.id], kind: 'page' }, { label: AC.t('cap.text.opSplit'), now: true }); closePop(); } }));
        acts.appendChild(ui.button({ label: AC.t('cap.text.newLine'), small: true, title: AC.t('cap.text.newLineTitle'), onClick: function () { cap.op({ op: 'break', ids: [w.id], kind: 'line' }, { label: AC.t('cap.text.newLine'), now: true }); closePop(); } }));
      }
    }
    if (ids.some(function (id) { var x = S.words[id]; return x && (x.edit || x.style); })) {
      acts.appendChild(ui.button({ label: AC.t('cap.text.reset'), icon: 'undo', small: true, kind: 'ghost', title: AC.t('cap.text.resetTitle'), onClick: function () { cap.op({ op: 'reset', ids: targets() }, { label: AC.t('cap.text.opReset'), now: true }); closePop(); } }));
    }
    pop.appendChild(acts);
    if (!multi && same.length > 1) {
      allCk = ui.h('input', { type: 'checkbox', class: 'ck' });
      pop.appendChild(ui.h('label', { class: 'pop-foot' }, [allCk, ui.h('span', { text: AC.t('cap.text.applyAllSame', { word: w.text, n: same.length }) })]));
    }
    var scrim = ui.h('div', { class: 'cap-pop-scrim' });
    scrim.addEventListener('mousedown', function () { saveText(); closePop(); });
    document.body.appendChild(scrim);
    document.body.appendChild(pop);
    T.pop = { el: pop, scrim: scrim, chip: chip, save: saveText, unEsc: AC.keys.onEscape(function () { if (!T.pop) return false; closePop(); chip.focus(); return true; }) };
    placePop(pop, chip);
    if (window.innerWidth >= 360 && txt) txt.focus();
  }
  function placePop(pop, chip) {
    var r = chip.getBoundingClientRect(), pw = pop.offsetWidth, ph = pop.offsetHeight;
    var top = r.bottom + 6;
    if (top + ph > window.innerHeight - 8) top = Math.max(8, r.top - ph - 6);
    pop.style.top = top + 'px';
    pop.style.left = U.clamp(r.left, 8, Math.max(8, window.innerWidth - pw - 8)) + 'px';
  }
  function closePop() {
    if (!T.pop) return;
    var p = T.pop; T.pop = null;
    p.unEsc();
    if (p.el.parentNode) p.el.parentNode.removeChild(p.el);
    if (p.scrim.parentNode) p.scrim.parentNode.removeChild(p.scrim);
  }
  function row(label, ctl) { return ui.h('div', { class: 'pop-row' }, [ui.h('span', { text: label }), ctl.el || ctl]); }
  function toggleBtn(label, icon, on, fn) {
    var b = ui.button({ label: label, icon: icon, small: true });
    b.setAttribute('aria-pressed', on ? 'true' : 'false');
    b.addEventListener('click', function () { on = !on; b.setAttribute('aria-pressed', on ? 'true' : 'false'); fn(on); });
    return b;
  }
  // Swatch row: ["none" +] colours + custom picker. fn(colour | null). Returns the element with el.set(colour).
  // opts.none (default true), opts.label (aria).
  function swatches(list, cur, fn, opts) {
    opts = opts || {};
    var el = ui.h('div', { class: 'cap-swatches', role: 'radiogroup', 'aria-label': opts.label || AC.t('cap.text.color') });
    var mk = function (c) {
      var b = ui.h('button', { type: 'button', role: 'radio', class: 'cap-swatch' + (c ? '' : ' cap-swatch-none'), title: c || AC.t('cap.text.none'), 'aria-label': c || AC.t('cap.text.none') });
      b._c = c;
      if (c) b.style.setProperty('--c', c);
      b.addEventListener('click', function () { paint(c); fn(c); });
      return b;
    };
    if (opts.none !== false) el.appendChild(mk(null));
    list.forEach(function (c) { el.appendChild(mk(c)); });
    var pick = ui.h('label', { class: 'cap-swatch cap-swatch-pick', title: AC.t('cap.text.otherColor') });
    var inp = ui.h('input', { type: 'color', value: '#ffffff', 'aria-label': AC.t('cap.text.otherColor') });
    inp.addEventListener('change', function () { var c = inp.value.toUpperCase(); paint(c); fn(c); });
    pick.appendChild(inp); el.appendChild(pick);
    function key(c) { return c ? String(c).slice(0, 7).toLowerCase() : ''; }
    function paint(c) {
      var hit = false;
      U.$$('.cap-swatch[role="radio"]', el).forEach(function (b) { var on = key(b._c) === key(c); if (on) hit = true; b.setAttribute('aria-checked', on ? 'true' : 'false'); });
      pick.setAttribute('aria-checked', !hit && c ? 'true' : 'false');
      if (c && /^#[0-9a-f]{6}/i.test(c)) { inp.value = key(c); pick.style.boxShadow = !hit ? '0 0 0 2px var(--surface-pop), 0 0 0 3.5px var(--text-1)' : ''; }
      else pick.style.boxShadow = '';
    }
    paint(cur || null);
    el.set = paint;
    return el;
  }
  cap.swatches = swatches;

  /* ---------------------------------------------------------------- selection bar (2+ words) */
  function buildSelbar() {
    T.selCount = ui.h('b');
    var bar = ui.h('div', { class: 'cap-selbar', hidden: true, role: 'toolbar', 'aria-label': AC.t('cap.text.selbarAria') }, [T.selCount]);
    var mk = function (icon, label, fn) { var b = ui.h('button', { class: 'ibtn', type: 'button', 'aria-label': label, title: label, html: ui.icon(icon) }); b.addEventListener('click', fn); bar.appendChild(b); return b; };
    mk('brush', AC.t('cap.text.setStyle'), function () { var c = chipEl(T.sel[0]); if (c) openPop(c); });
    mk('bold', AC.t('cap.text.bold'), function () { var on = T.sel.every(function (id) { return (S.words[id].style || {}).bold; }); cap.op({ op: 'style', ids: T.sel.slice(), style: { bold: on ? null : true } }, { label: AC.t('cap.text.bold'), now: true }); });
    mk('marker', AC.t('cap.text.emphKey'), function () { toggleEmph(T.sel.slice()); });
    mk('eyeoff', AC.t('cap.text.hideKey'), function () { toggleHide(T.sel.slice()); });
    mk('x', AC.t('cap.text.deselect'), function () { setSel([]); });
    return bar;
  }

  /* ---------------------------------------------------------------- suggestions: AI diff, orphans, low confidence */
  function paintSuggestions() {
    var box = T.sugBox; box.innerHTML = '';
    if (!S.doc) return;
    if (T.sug && T.sug.items.length) {
      var n = T.sug.items.length, prevw = T.sug.items.slice(0, 3).map(function (s) { return '"' + s.old + '" \u2192 "' + s['new'] + '"'; }).join('; ');
      box.appendChild(card('spark', '', AC.t('cap.text.sugTitle', { n: n }), AC.t('cap.text.sugText', { ex: prevw + (n > 3 ? '; ...' : '') }), [
        { label: AC.t('cap.text.acceptAll'), icon: 'check', onClick: function () { acceptSug(T.sug.items.slice()); } },
        { label: AC.t('cap.text.reviewEach'), kind: 'ghost', onClick: reviewSug },
        { label: AC.t('cap.text.rejectAll'), kind: 'ghost', onClick: function () { rejectSug(T.sug.items.slice()); } }]));
    }
    var eff = cap.eff();
    if (eff && eff.timing && eff.timing.mode !== 'word' && (eff.layout || {}).max_words > 1) {
      var orph = S.doc.pages.filter(function (p, i) { return i > 0 && p.lines.length === 1 && p.lines[0].length === 1; });
      if (orph.length) {
        var names = orph.slice(0, 3).map(function (p) { var w = S.words[p.lines[0][0]]; return w ? w.text : ''; }).join(', ');
        box.appendChild(card('join', 'is-warn', AC.t('cap.text.orphTitle', { n: orph.length, names: names + (orph.length > 3 ? ', ...' : '') }), AC.t('cap.text.orphText'), [
          { label: AC.t('cap.text.joinAll'), icon: 'join', onClick: function () { cap.op(orph.map(function (p) { return { op: 'break', ids: [p.lines[0][0]], kind: 'join' }; }), { label: AC.t('cap.text.opJoinOrph'), now: true }); } }]));
      }
    }
    var low = (S.doc.words || []).filter(function (w) { return (w.p || 1) < 0.5 && !w.hide && !w.merged && !(w.edit && w.edit.text !== undefined); });
    if (low.length) {
      box.appendChild(card('alert', 'is-warn', AC.t('cap.text.lowTitleN', { n: low.length }), AC.t('cap.text.lowText'), [
        { label: AC.t('cap.text.nextLow'), onClick: function () { nextLow(low); } }]));
    }
    var fixes = (S.doc.words || []).filter(function (w) { return w.auto && w.auto.kind === 'glossary'; });
    if (fixes.length) {
      var ex = fixes.slice(0, 2).map(function (w) { return AC.t('cap.text.fixEx', { old: w.auto.old, 'new': w.auto.text }); }).join(', ');
      box.appendChild(card('check', '', AC.t('cap.text.fixTitle', { n: fixes.length }), ex + (fixes.length > 2 ? ', ...' : ''), []));
    }
  }
  function card(icon, cls, title, text, actions) {
    var c = ui.h('div', { class: 'cap-suggest ' + cls, html: ui.icon(icon) });
    var body = ui.h('div', { html: '<b>' + U.esc(title) + '</b>' + (text ? '<p>' + U.esc(text) + '</p>' : '') });
    if (actions.length) {
      var r = ui.h('div', { class: 'btn-row' });
      actions.forEach(function (a) { r.appendChild(ui.button({ label: a.label, icon: a.icon, small: true, kind: a.kind || '', onClick: a.onClick })); });
      body.appendChild(r);
    }
    c.appendChild(body);
    return c;
  }
  var lowAt = -1;
  function nextLow(list) {
    lowAt = (lowAt + 1) % list.length;
    var w = list[lowAt], c = chipEl(w.id);
    setSel([w.id], w.id);
    if (c) { revealCard(c.closest('.cap-line')); c.focus(); }
    cap.preview.show(w.t0 + 0.02);
  }

  /* ---------------------------------------------------------------- AI cleanup (engine cleanup -> diff -> accept) */
  function aiCleanup() {
    var ctx = S.ctx;
    cap.idle().then(function () {
      var task = ctx.run({ action: 'cleanup', worker: true, seq: null, workdir: S.workdir, scope: false, params: {}, title: AC.t('cap.text.aiJob'),
        stages: [{ id: 'ai', label: AC.t('cap.text.aiStage'), w: 0.95 }, { id: 'review', label: AC.t('cap.text.reviewStage'), w: 0.05 }],
        onResult: function (data) {
          var items = (data.edits || []).map(function (e) { return { id: e.id, ids: e.ids || [], old: e.old, 'new': e['new'], kind: e.kind }; })
            .filter(function (e) { return e.ids.length && e.ids.every(function (id) { return S.words[id]; }); });
          T.sug = items.length ? { file: data.review, items: items, source: data.source } : null;
          AC.router.go('tool/captions/teks', { replace: true });
          cap.selectTab('teks');
          render();
          if (items.length) ui.toast(AC.t('cap.text.aiReady', { n: items.length }), 'spark');
          else if (data.source === 'fallback' || data.source === 'none') ui.toast(AC.t('cap.text.aiOffline'), { icon: 'alert' });
          else ui.toast(AC.t('cap.text.aiNothing'), 'check');
        } });
      cap.hold(task);
    });
  }
  function acceptSug(list) {
    if (!list.length) return;
    var ids = {};
    list.forEach(function (s) { ids[s.id] = 1; });
    var p = cap.op({ op: 'accept', edits: list.map(function (s) { return { ids: s.ids, 'new': s['new'] }; }) }, { label: AC.t('cap.text.opAccept', { n: list.length }), now: true });
    T.sug.items = T.sug.items.filter(function (s) { return !ids[s.id]; });
    if (!T.sug.items.length) T.sug = null;
    render();
    return p.then(function () { ui.toast(AC.t('cap.text.accepted', { n: list.length }), 'check'); });
  }
  function rejectSug(list) {
    var ids = {};
    list.forEach(function (s) { ids[s.id] = 1; });
    T.sug.items = T.sug.items.filter(function (s) { return !ids[s.id]; });
    if (!T.sug.items.length) T.sug = null;
    render();
  }
  // The shared Tinjau list for the AI file; "Terima n" = engine cleanup mode apply with the edited review.
  function reviewSug() {
    var ctx = S.ctx, sug = T.sug; if (!sug) return;
    var keep = {};
    sug.items.forEach(function (s) { keep[s.id] = 1; });
    var doc;
    try { doc = ui.loadReview(sug.file); } catch (e) { ui.toast(U.errMsg(e), { kind: 'err' }); return; }
    doc.items = doc.items.filter(function (it) { return keep[it.id]; });
    AC.sys.writeJSON(sug.file, doc);
    ctx.review({ file: sug.file, unit: function (n) { return AC.t('cap.text.revUnit', { n: n }); }, verbOn: AC.t('cap.text.revOn'), verbOff: AC.t('cap.text.revOff'), bulkAction: null, cancelLabel: AC.t('common.back'),
      title: function (n, all) { return AC.i18n.html('cap.text.revTitle', { n: n, all: all }); },
      primary: { label: function (n) { return AC.t('cap.text.revApply', { n: n }); }, onApply: function (items, d, file) {
        cap.idle().then(function () {
          var task = ctx.run({ action: 'cleanup', worker: true, seq: null, workdir: S.workdir, scope: false, review: file, params: { mode: 'apply' }, title: AC.t('cap.text.applyJob'),
            onResult: function (r) {
              T.sug = null;
              cap.reload();
              AC.router.go('tool/captions/teks', { replace: true });
              cap.selectTab('teks');
              ui.toast(AC.t('cap.text.applied', { n: r.applied || 0 }), 'check');
            } });
          cap.hold(task);
        });
      } } });
  }

  /* ---------------------------------------------------------------- find & replace */
  function buildFind() {
    var q = ui.h('input', { class: 'input', type: 'search', placeholder: AC.t('cap.text.findPh'), 'aria-label': AC.t('cap.text.findPh'), spellcheck: 'false' });
    var r = ui.h('input', { class: 'input', type: 'text', placeholder: AC.t('cap.text.replacePh'), 'aria-label': AC.t('cap.text.replacePh'), spellcheck: 'false' });
    var go = ui.button({ label: AC.t('cap.text.replaceAll'), small: true, onClick: function () { replaceAll(); } });
    var ck = ui.h('input', { type: 'checkbox', class: 'ck' });
    var count = ui.h('span', { class: 'cap-find-n' });
    var close = ui.h('button', { class: 'linkbtn', type: 'button', text: AC.t('common.close'), on: { click: function () { box.hidden = true; T.find.q = ''; q.value = ''; runFind(false); } } });
    var meta = ui.h('div', { class: 'cap-find-meta' }, [count, ui.h('label', {}, [ck, ui.h('span', { text: AC.t('cap.text.matchCase') })]), ui.h('span', { style: { flex: '1' } }), close]);
    var box = ui.h('div', { class: 'cap-find', hidden: true, role: 'search' }, [q, r, go, meta]);
    q.addEventListener('input', U.debounce(function () { T.find.q = q.value.trim(); T.find.at = -1; runFind(false); }, 120));
    q.addEventListener('keydown', function (e) { if (e.key === 'Enter') { e.preventDefault(); runFind(true); } });
    r.addEventListener('keydown', function (e) { if (e.key === 'Enter') { e.preventDefault(); replaceAll(); } });
    ck.addEventListener('change', function () { T.find.at = -1; runFind(false); });
    T.findQ = q; T.findR = r; T.findCk = ck; T.findN = count;
    function replaceAll() {
      var f = q.value.trim(); if (!f) { q.focus(); return; }
      cap.op({ op: 'replace', find: f, replace: r.value.trim(), 'case': ck.checked, whole: true }, { label: AC.t('cap.text.opReplace', { word: f }), now: true });
    }
    return box;
  }
  function openFind() {
    T.findBox.hidden = false;
    var w = T.sel.length === 1 && S.words[T.sel[0]];
    if (w && !T.findQ.value) { T.findQ.value = w.text; T.find.q = w.text; runFind(false); }
    T.findQ.focus(); T.findQ.select();
  }
  function runFind(jump) {
    U.$$('.cap-wchip.is-hit', T.list).forEach(function (c) { c.classList.remove('is-hit'); });
    U.$$('.cap-line.is-hit', T.list).forEach(function (c) { c.classList.remove('is-hit'); });
    var q = T.find.q, hits = [];
    if (q && S.doc) {
      var cs = T.findCk.checked, nm = function (s) { return cs ? String(s || '').replace(/[^\w\u00c0-\u024f-]+/g, '') : cap.norm(s); };
      var tgt = q.split(/\s+/).map(nm).filter(Boolean);
      var vis = (S.doc.words || []).filter(function (w) { return !w.merged && !w.hide; });
      for (var i = 0; i + tgt.length <= vis.length; i++) {
        var ok = true;
        for (var j = 0; j < tgt.length; j++) if (nm(vis[i + j].text) !== tgt[j]) { ok = false; break; }
        if (ok) { hits.push(vis.slice(i, i + tgt.length).map(function (w) { return w.id; })); i += tgt.length - 1; }
      }
    }
    T.find.hits = hits;
    hits.forEach(function (h) { h.forEach(function (id) { var c = chipEl(id); if (c) { c.classList.add('is-hit'); c.closest('.cap-line').classList.add('is-hit'); } }); });
    T.findN.textContent = q ? (hits.length ? AC.t('cap.text.hits', { n: hits.length }) : AC.t('cap.text.noHits')) : '';
    if (jump && hits.length) {
      T.find.at = (T.find.at + 1) % hits.length;
      var id = hits[T.find.at][0], c = chipEl(id);
      if (c) revealCard(c.closest('.cap-line'));
      var w = S.words[id]; if (w) cap.preview.show(w.t0 + 0.02);
      T.findN.textContent = AC.t('cap.text.hitAt', { i: T.find.at + 1, n: hits.length });
    }
  }

  /* ---------------------------------------------------------------- Kamus (glossary) */
  var G = {};
  function buildGlossary() {
    var adv = ui.advanced({ title: AC.t('cap.text.glossary'), key: 'captions-kamus' });
    adv.add(ui.h('p', { class: 'help', text: AC.t('cap.text.glossaryHelp') }));
    G.doc = termList(AC.t('cap.text.thisVideo'), function () { return ((S.doc && S.doc.params) || {}).glossary || []; }, function (list) {
      cap.op({ op: 'params', glossary: list }, { label: AC.t('cap.text.glossary'), now: true }).then(cap.text.refreshWords);
    });
    G.all = termList(AC.t('cap.text.allVideos'), function () { var g = AC.settings.get('glossary'); return Array.isArray(g) ? g : (g ? String(g).split(/[,;\n]/).map(function (s) { return s.trim(); }).filter(Boolean) : []); }, function (list) {
      AC.settings.set('glossary', list);
      cap.text.refreshWords();
    });
    adv.add([G.doc.el, G.all.el]);
    G.adv = adv;
    return adv.el;
  }
  function termList(label, get, set) {
    var wrap = ui.h('div', { class: 'cap-gloss' });
    var head = ui.h('span', { class: 'lbl', text: label });
    var chips = ui.h('div', { class: 'chips-wrap', 'data-i18n-skip': true });   // the user's own terms
    var inp = ui.h('input', { class: 'input', type: 'text', placeholder: AC.t('cap.text.addPh'), 'aria-label': AC.t('cap.text.addAria', { list: label.toLowerCase() }), spellcheck: 'false' });
    var add = ui.button({ label: AC.t('common.add'), icon: 'plus', small: true, onClick: function () { push(); } });
    inp.addEventListener('keydown', function (e) { if (e.key === 'Enter') { e.preventDefault(); push(); } });
    function push() {
      var v = inp.value.trim(); if (!v) return;
      var list = get().slice();
      if (list.indexOf(v) < 0) { list.push(v); set(list); }
      inp.value = '';
    }
    function paint() {
      chips.innerHTML = '';
      var list = get();
      if (!list.length) chips.appendChild(ui.h('span', { class: 'help', text: AC.t('cap.text.empty') }));
      list.forEach(function (x) {
        var b = ui.h('button', { class: 'chip cap-term', type: 'button', title: AC.t('cap.text.removeTerm', { term: x }), html: U.esc(x) + ui.icon('x') });
        b.addEventListener('click', function () { var l = get().filter(function (y) { return y !== x; }); set(l); });
        chips.appendChild(b);
      });
    }
    wrap.appendChild(head); wrap.appendChild(chips); wrap.appendChild(ui.h('div', { class: 'cap-gloss-add' }, [inp, add]));
    paint();
    return { el: wrap, paint: paint };
  }
  function paintGlossary() { if (G.doc) { G.doc.paint(); G.all.paint(); } }
  AC.settings.on('change', function (k) { if (k === 'glossary') paintGlossary(); });

  // Re-run the deterministic text rules (kamus, angka) on the cached transcript; user edits are kept.
  cap.text.refreshWords = function () {
    var wd = S.workdir;
    return cap.idle().then(function () {
      var job = cap.worker('doc', { transcribe: false, scope: ((S.doc && S.doc.params) || {}).scope || { kind: 'all' } }, { withSeq: true, workdir: wd });
      cap.hold(job);
      return job.promise;
    }).then(function (r) {
      if (wd !== S.workdir) return;
      cap.reload();
      var f = r && r.stats ? r.stats.fixes : 0;
      ui.toast(f ? AC.t('cap.text.rebuiltFixes', { n: f }) : AC.t('cap.text.rebuilt'), 'check');
    }, function (e) {
      ui.toast(e && e.code === 'NO_WORDS' ? AC.t('cap.text.noWords') : AC.t('cap.text.rebuildFail', { msg: U.errMsg(e) }), { kind: 'err' });
    });
  };

  /* ---------------------------------------------------------------- text rules (doc params) + emoji */
  var R = {};
  function buildRules() {
    var adv = ui.advanced({ title: AC.t('cap.text.rulesTitle'), key: 'captions-rules' });
    R.fill = ui.switch({ label: AC.t('cap.text.hideFillers'), help: AC.t('cap.text.hideFillersHelp'), checked: true,
      onChange: function (v) { cap.op({ op: 'params', hide_fillers: v }, { label: AC.t('cap.text.opFillers'), now: true }); } });
    R.num = ui.switch({ label: AC.t('cap.text.numbers'), help: AC.t('cap.text.numbersHelp'), checked: true,
      onChange: function (v) { cap.op({ op: 'params', numbers: v }, { label: AC.t('cap.text.opNumbers'), now: true }).then(cap.text.refreshWords); } });
    // option values are engine ids (doc.params.censor); the masks are shown as they are
    R.cen = ui.select({ ariaLabel: AC.t('cap.text.censorAria'), width: '170px', value: 'auto',
      options: [{ value: 'auto', label: AC.t('cap.text.censorAuto') }, { value: 'stars', label: 'a****g' }, { value: 'first', label: 'a*****' }, { value: 'full', label: '******' }, { value: 'bleep', label: '[sensor]' }, { value: 'off', label: AC.t('cap.text.censorOff') }],
      onChange: function (v) { cap.op({ op: 'params', censor: v }, { label: AC.t('cap.text.censor'), now: true }); } });
    var emo = ui.h('div', { class: 'btn-row' });
    [['sedikit', 'cap.text.emoFew'], ['normal', 'cap.text.emoNormal'], ['banyak', 'cap.text.emoMany']].forEach(function (x) {   // density ids stay
      emo.appendChild(ui.button({ label: AC.t(x[1]), small: true, onClick: function () { cap.op({ op: 'auto_emoji', density: x[0] }, { label: AC.t('cap.text.autoEmoji'), now: true }); } }));
    });
    emo.appendChild(ui.button({ label: AC.t('cap.text.clearEmoji'), small: true, kind: 'ghost', onClick: function () { cap.op({ op: 'clear_emoji' }, { label: AC.t('cap.text.clearEmoji'), now: true }); } }));
    adv.add([R.fill, R.num, ui.inlineField({ label: AC.t('cap.text.profanity'), control: R.cen.el }),
             ui.field({ label: AC.t('cap.text.autoEmoji'), help: AC.t('cap.text.autoEmojiHelp', { sec: AC.t('unit.sec') }), control: emo })]);
    return adv.el;
  }
  function paintRules() {
    if (!R.fill || !S.doc) return;
    var p = S.doc.params || {};
    R.fill.set(p.hide_fillers !== false, true);
    R.num.set(p.numbers !== false, true);
    R.cen.set(p.censor || 'auto', true);
  }
})();
