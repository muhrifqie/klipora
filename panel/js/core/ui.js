/* ui.js: AC.ui shared components (anatomy from docs/research/ux_design.md section 3 + 5, CSS in css/base.css).
   Every component returns a controller {el, get(), set(v, silent), ...}; append ctrl.el where you need it.
   Text is always set with textContent or escaped (U.esc); never put secrets into the DOM. */
(function () {
  'use strict';
  var AC = window.AC, U = AC.util;
  var ui = AC.ui = {};
  var uid = 0;
  function nid(p) { return 'ac-' + (p || 'c') + '-' + (++uid); }

  /* ---------------- DOM helpers */
  ui.icon = function (name, cls) { return '<svg class="svg-i ' + (cls || '') + '" aria-hidden="true"><use href="#i-' + U.esc(name) + '"/></svg>'; };
  // Tools add their own <symbol id="i-..."> icons: AC.ui.addIcons('<symbol id="i-foo" viewBox="0 0 20 20">...</symbol>')
  ui.addIcons = function (symbols) {
    var defs = document.querySelector('svg defs');
    if (!defs) return;
    var tmp = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    tmp.innerHTML = symbols;
    while (tmp.firstChild) defs.appendChild(tmp.firstChild);
  };
  // h('div', {class: 'x', text: 'hi', on: {click: fn}, aria-label: '..'}, [children])
  ui.h = function (tag, attrs, children) {
    var el = document.createElement(tag);
    attrs = attrs || {};
    Object.keys(attrs).forEach(function (k) {
      var v = attrs[k];
      if (v === undefined || v === null || v === false) return;
      if (k === 'class') el.className = v;
      else if (k === 'text') el.textContent = v;
      else if (k === 'html') el.innerHTML = v;
      else if (k === 'style' && typeof v === 'object') U.assign(el.style, v);
      else if (k === 'on') Object.keys(v).forEach(function (ev) { el.addEventListener(ev, v[ev]); });
      else if (k === 'dataset') Object.keys(v).forEach(function (d) { el.dataset[d] = v[d]; });
      else if (k === 'value' || k === 'checked' || k === 'disabled' || k === 'hidden' || k === 'open' || k === 'id' || k === 'type' || k === 'name' || k === 'tabIndex') el[k] = v;
      else el.setAttribute(k, v === true ? '' : v);
    });
    ui.append(el, children);
    return el;
  };
  ui.append = function (el, children) {
    if (children === undefined || children === null) return el;
    if (!Array.isArray(children)) children = [children];
    children.forEach(function (c) {
      if (c === undefined || c === null || c === false) return;
      if (typeof c === 'string' || typeof c === 'number') el.appendChild(document.createTextNode(String(c)));
      else if (c.el instanceof Node) el.appendChild(c.el);
      else el.appendChild(c);
    });
    return el;
  };
  ui.html = function (html) { var t = document.createElement('div'); t.innerHTML = html; return t.firstElementChild; };
  ui.tag = function (text, kind, icon) { return '<span class="tag ' + (kind ? 'tag-' + kind : '') + '">' + (icon ? ui.icon(icon) : '') + U.esc(text) + '</span>'; };
  ui.BADGES = { ai: ['AI', 'ai'], baru: ['ui.badgeNew', 'line'], gpu: ['GPU', 'line'], beta: ['Beta', 'line'], dev: ['Dev', 'line'] };
  ui.badge = function (b) { var x = ui.BADGES[b] || [b, 'line']; return ui.tag(x[0].indexOf('.') > 0 && AC.i18n.has(x[0]) ? AC.t(x[0]) : x[0], x[1]); };

  /* ---------------- buttons */
  // kind: 'primary' | 'ghost' | 'danger' | '' ; small: btn-sm
  ui.button = function (o) {
    var cls = 'btn' + (o.kind === 'primary' ? ' btn-primary' : o.kind === 'ghost' ? ' btn-ghost' : o.kind === 'danger' ? ' btn-danger' : o.kind === 'ghost-danger' ? ' btn-ghost btn-danger' : '') + (o.small ? ' btn-sm' : '');
    var b = ui.h('button', { class: cls, type: 'button', id: o.id, title: o.title, disabled: !!o.disabled, 'aria-label': o.ariaLabel });
    b.innerHTML = (o.icon ? ui.icon(o.icon) : '') + (o.kind === 'primary' ? '<span class="lbl">' + U.esc(o.label) + '</span>' : U.esc(o.label || '')) + (o.kbd ? '<span class="kbd">' + U.esc(o.kbd) + '</span>' : '');
    if (o.onClick) b.addEventListener('click', function (e) { if (!b.disabled) o.onClick(e); });
    return b;
  };
  // Two-step confirm for destructive actions: first click asks common.confirm ("Sure? Click again").
  ui.confirmClick = function (btn, fn, ask) {
    var armed = false, label = btn.innerHTML, t = null;
    btn.addEventListener('click', function (e) {
      if (!armed) { armed = true; btn.innerHTML = U.esc(ask || AC.t('common.confirm')); t = setTimeout(function () { armed = false; btn.innerHTML = label; }, 3000); return; }
      clearTimeout(t); armed = false; btn.innerHTML = label; fn(e);
    });
    return btn;
  };

  /* ---------------- sections */
  ui.section = function (o) {
    o = o || {};
    var el = ui.h('div', { class: 'sec' });
    var h = ui.h('h2', { class: 'sec-h' }, [ui.h('span', { text: o.title || '' })]);
    var aside = ui.h('span', { class: 'aside', text: o.aside || '' });
    h.appendChild(aside);
    if (o.action) h.appendChild(ui.h('button', { class: 'linkbtn act', type: 'button', text: o.action.label, on: { click: o.action.onClick } }));
    if (o.title !== undefined) el.appendChild(h);
    if (o.help) el.appendChild(ui.h('p', { class: 'help', text: o.help }));
    return { el: el, body: el, head: h, setAside: function (t) { aside.textContent = t || ''; }, add: function (c) { ui.append(el, c); return this; } };
  };
  // Collapsible "Advanced settings" (common.advanced). key: remembers open state in localStorage.
  ui.advanced = function (o) {
    o = o || {};
    var open = o.key ? AC.store.get('adv.' + o.key, !!o.open) : !!o.open;
    var d = ui.h('details', { class: 'sec-d', open: open });
    var aside = ui.h('span', { class: 'aside', text: o.aside || '' });
    d.appendChild(ui.h('summary', { class: 'sec-h' }, [ui.h('span', { text: o.title || AC.t('common.advanced') }), aside]));
    var body = ui.h('div', { class: 'sec' });
    d.appendChild(body);
    if (o.key) d.addEventListener('toggle', function () { AC.store.set('adv.' + o.key, d.open); });
    return { el: d, body: body, setAside: function (t) { aside.textContent = t || ''; }, add: function (c) { ui.append(body, c); return this; } };
  };

  /* ---------------- segmented control (radiogroup) */
  function normOpts(options) {
    return (options || []).map(function (o) {
      if (Array.isArray(o)) return { value: o[0], label: o[1] };
      if (typeof o !== 'object') return { value: o, label: String(o) };
      return o;
    });
  }
  ui.segmented = function (o) {
    var opts = normOpts(o.options), value = o.value !== undefined ? o.value : (opts[0] && opts[0].value);
    var el = ui.h('div', { class: 'seg' + (o.small ? ' seg-sm' : ''), role: 'radiogroup', 'aria-label': o.ariaLabel || o.label });
    if (o.width) el.style.width = o.width;
    opts.forEach(function (op) {
      var b = ui.h('button', { type: 'button', role: 'radio', title: op.title, disabled: !!op.disabled, dataset: { v: String(op.value) } });
      b.innerHTML = (op.icon ? ui.icon(op.icon) : '') + U.esc(op.label);
      b._v = op.value;
      el.appendChild(b);
    });
    function paint() {
      U.$$('button', el).forEach(function (b) {
        var on = b._v === value;
        b.setAttribute('aria-checked', on ? 'true' : 'false');
        b.tabIndex = on ? 0 : -1;
      });
    }
    var ctrl = {
      el: el,
      get: function () { return value; },
      set: function (v, silent) { value = v; paint(); if (!silent && o.onChange) o.onChange(v); },
      disable: function (v, dis, title) { U.$$('button', el).forEach(function (b) { if (b._v === v) { b.disabled = !!dis; if (title !== undefined) b.title = title; } }); }
    };
    el.addEventListener('click', function (e) {
      var b = e.target.closest('button'); if (!b || b.disabled || !el.contains(b)) return;
      ctrl.set(b._v);
    });
    el.addEventListener('keydown', function (e) {
      if (e.key !== 'ArrowRight' && e.key !== 'ArrowLeft') return;
      var bs = U.$$('button', el).filter(function (b) { return !b.disabled; });
      var i = bs.map(function (b) { return b._v; }).indexOf(value);
      var n = bs[(i + (e.key === 'ArrowRight' ? 1 : bs.length - 1)) % bs.length];
      if (n) { e.preventDefault(); ctrl.set(n._v); n.focus(); }
    });
    paint();
    return ctrl;
  };

  // Preset segmented control + hint line. options: [{value, label, hint, values: {...}}]. "Custom" when the
  // values no longer match any preset (call ctrl.sync(currentValues) after manual edits).
  ui.presets = function (o) {
    var opts = normOpts(o.options), wrap = ui.h('div', { class: 'sec', style: { gap: '6px' } });
    var hint = ui.h('p', { class: 'help' });
    var seg = ui.segmented({ options: opts, value: o.value, ariaLabel: o.ariaLabel || AC.t('ui.style'), onChange: function (v) {
      var p = find(v); hint.textContent = p && p.hint || '';
      if (o.onChange) o.onChange(v, p);
    } });
    wrap.appendChild(seg.el); wrap.appendChild(hint);
    function find(v) { for (var i = 0; i < opts.length; i++) if (opts[i].value === v) return opts[i]; return null; }
    function eq(a, b) { return typeof a === 'number' && typeof b === 'number' ? Math.abs(a - b) < 1e-6 : a === b; }
    var ctrl = {
      el: wrap, seg: seg,
      get: function () { return seg.get(); },
      set: function (v, silent) { seg.set(v, silent); var p = find(v); hint.textContent = p ? (p.hint || '') : (o.customHint || AC.t('ui.customHint')); },
      // Returns the matching preset value or null (and shows the custom hint).
      match: function (vals) {
        for (var i = 0; i < opts.length; i++) {
          var pv = opts[i].values || {}, ok = true;
          for (var k in pv) if (!eq(pv[k], vals[k])) { ok = false; break; }
          if (ok) return opts[i].value;
        }
        return null;
      },
      sync: function (vals) {
        var m = ctrl.match(vals);
        if (m === null) { seg.set(null, true); hint.textContent = o.customHint || AC.t('ui.customHint'); }
        else ctrl.set(m, true);
        return m;
      }
    };
    ctrl.set(o.value, true);
    return ctrl;
  };

  /* ---------------- slider with value + unit + helper */
  ui.slider = function (o) {
    var id = o.id || nid('r'), dec = o.decimals !== undefined ? o.decimals : ((String(o.step || 1).split('.')[1] || '').length);
    var fmt = o.format || function (v) { return U.dec(v, dec) + (o.unit || ''); };
    var out = ui.h('output', { for: id });
    var input = ui.h('input', { class: 'range', id: id, type: 'range', min: o.min, max: o.max, step: o.step || 1, value: o.value });
    var el = ui.h('div', { class: 'field' }, [ui.h('div', { class: 'field-top' }, [ui.h('label', { for: id, text: o.label }), out]), input]);
    var helpRow = null;
    if (o.help || o.link) {
      helpRow = ui.h('div', { class: 'field-top' }, [ui.h('small', { class: 'help', text: o.help || '' })]);
      if (o.link) helpRow.appendChild(ui.h('button', { class: 'linkbtn', type: 'button', text: o.link.label, on: { click: o.link.onClick } }));
      el.appendChild(helpRow);
    }
    function paint() {
      var v = Number(input.value), p = (v - Number(input.min)) / (Number(input.max) - Number(input.min)) * 100;
      input.style.setProperty('--p', p + '%'); out.textContent = fmt(v);
    }
    input.addEventListener('input', function () { paint(); if (o.onInput) o.onInput(Number(input.value)); });
    input.addEventListener('change', function () { if (o.onChange) o.onChange(Number(input.value)); });
    paint();
    return { el: el, input: input, get: function () { return Number(input.value); },
             set: function (v, silent) { input.value = v; paint(); if (!silent && o.onInput) o.onInput(Number(input.value)); } };
  };

  ui.stepper = function (o) {
    var v = Number(o.value) || 0, step = o.step || 1, dec = o.decimals !== undefined ? o.decimals : ((String(step).split('.')[1] || '').length);
    var fmt = o.format || function (x) { return U.dec(x, dec) + (o.unit || ''); };
    var out = ui.h('output', { class: 'num' });
    var minus = ui.h('button', { type: 'button', 'aria-label': AC.t('ui.decrease', { label: o.label || '' }), html: ui.icon('minus') });
    var plus = ui.h('button', { type: 'button', 'aria-label': AC.t('ui.increase', { label: o.label || '' }), html: ui.icon('plus') });
    var box = ui.h('div', { class: 'stepper' }, [minus, out, plus]);
    var el = o.label ? ui.h('div', { class: 'field' }, [ui.h('span', { class: 'lbl', text: o.label }), box]) : box;
    function set(x, silent) {
      x = Math.round(U.clamp(x, o.min !== undefined ? o.min : -Infinity, o.max !== undefined ? o.max : Infinity) / step) * step;
      v = Number(x.toFixed(6)); out.textContent = fmt(v);
      if (!silent && o.onChange) o.onChange(v);
    }
    minus.addEventListener('click', function () { set(v - step); });
    plus.addEventListener('click', function () { set(v + step); });
    set(v, true);
    return { el: el, get: function () { return v; }, set: set };
  };

  /* ---------------- switch (real checkbox, role=switch, keyboard focusable) */
  ui.switch = function (o) {
    var input = ui.h('input', { type: 'checkbox', role: 'switch', checked: !!o.checked, disabled: !!o.disabled });
    var text = ui.h('span', { class: 'sw-text' });
    text.innerHTML = U.esc(o.label) + (o.badge ? ' ' + o.badge : '') + (o.help ? '<small>' + U.esc(o.help) + '</small>' : '');
    var el = ui.h('label', { class: 'sw' }, [text, input, ui.h('span', { class: 'sw-track' })]);
    input.addEventListener('change', function () { if (o.onChange) o.onChange(input.checked); });
    return { el: el, input: input, get: function () { return input.checked; },
             set: function (v, silent) { input.checked = !!v; if (!silent && o.onChange) o.onChange(input.checked); } };
  };

  /* ---------------- option cards (radio list). First option should be the safe one. */
  ui.radioCards = function (o) {
    var name = o.name || nid('opt'), value = o.value, opts = normOpts(o.options);
    var el = ui.h('div', { class: 'opts', role: 'radiogroup', 'aria-label': o.ariaLabel || '' });
    opts.forEach(function (op) {
      var input = ui.h('input', { class: 'rd', type: 'radio', name: name, value: String(op.value) });
      input._v = op.value;
      var span = ui.h('span', { html: '<b>' + U.esc(op.title || op.label) + '</b>' + (op.desc ? '<span>' + U.esc(op.desc) + '</span>' : '') });
      var lab = ui.h('label', { class: 'opt' }, [input, span]);
      if (op.tag) lab.appendChild(ui.html(ui.tag(op.tag, op.tagKind || 'ai')));
      input.addEventListener('change', function () { if (input.checked) ctrl.set(op.value); });
      el.appendChild(lab);
    });
    function paint() { U.$$('input', el).forEach(function (i) { i.checked = i._v === value; i.parentNode.classList.toggle('is-on', i.checked); }); }
    var ctrl = { el: el, get: function () { return value; }, set: function (v, silent) { value = v; paint(); if (!silent && o.onChange) o.onChange(v); } };
    paint();
    return ctrl;
  };

  /* ---------------- chips (toggle tokens). multi: value is an array */
  ui.chips = function (o) {
    var opts = normOpts(o.options), multi = !!o.multi;
    var value = multi ? (o.value || []).slice() : o.value;
    var el = ui.h('div', { class: 'chips-wrap', role: multi ? 'group' : 'radiogroup', 'aria-label': o.ariaLabel || '' });
    function isOn(v) { return multi ? value.indexOf(v) >= 0 : value === v; }
    function render() {
      el.innerHTML = '';
      opts.forEach(function (op) {
        var b = ui.h('button', { class: 'chip', type: 'button', title: op.title, 'aria-pressed': isOn(op.value) ? 'true' : 'false',
                                 html: (op.icon ? ui.icon(op.icon) : '') + U.esc(op.label) + (op.count !== undefined ? ' <span class="n">' + U.esc(op.count) + '</span>' : '') });
        b.addEventListener('click', function () {
          if (multi) { var i = value.indexOf(op.value); if (i >= 0) value.splice(i, 1); else value.push(op.value); }
          else value = op.value;
          render(); if (o.onChange) o.onChange(multi ? value.slice() : value);
        });
        el.appendChild(b);
      });
      if (o.add) el.appendChild(ui.h('button', { class: 'chip chip-add', type: 'button', html: ui.icon('plus') + U.esc(o.add.label || AC.t('common.add')), on: { click: o.add.onAdd } }));
    }
    render();
    return { el: el, get: function () { return multi ? value.slice() : value; },
             set: function (v, silent) { value = multi ? (v || []).slice() : v; render(); if (!silent && o.onChange) o.onChange(ctrlGet()); },
             setOptions: function (list) { opts = normOpts(list); render(); } };
    function ctrlGet() { return multi ? value.slice() : value; }
  };

  /* ---------------- select / input */
  ui.select = function (o) {
    var el = ui.h('select', { class: 'select', 'aria-label': o.ariaLabel || o.label });
    if (o.width) el.style.width = o.width;
    function fill(list) {
      el.innerHTML = '';
      normOpts(list).forEach(function (op) { var opt = ui.h('option', { value: String(op.value), text: op.label }); opt._v = op.value; el.appendChild(opt); });
    }
    fill(o.options);
    function get() { var s = el.options[el.selectedIndex]; return s ? s._v : undefined; }
    function set(v, silent) { for (var i = 0; i < el.options.length; i++) if (el.options[i]._v === v) el.selectedIndex = i; if (!silent && o.onChange) o.onChange(get()); }
    if (o.value !== undefined) set(o.value, true);
    el.addEventListener('change', function () { if (o.onChange) o.onChange(get()); });
    return { el: el, get: get, set: set, setOptions: function (l) { var v = get(); fill(l); set(v, true); } };
  };
  ui.input = function (o) {
    var el = ui.h('input', { class: 'input' + (o.mono ? ' input-mono' : ''), type: o.type || 'text', value: o.value || '', placeholder: o.placeholder || '',
                             'aria-label': o.ariaLabel || o.label, spellcheck: 'false', autocomplete: 'off' });
    if (o.onInput) el.addEventListener('input', function () { o.onInput(el.value); });
    if (o.onChange) el.addEventListener('change', function () { o.onChange(el.value); });
    return { el: el, get: function () { return el.value; }, set: function (v) { el.value = v === undefined || v === null ? '' : v; } };
  };
  // Row: label (+ help) on the left, control on the right.
  ui.inlineField = function (o) {
    var left = ui.h('span', { html: U.esc(o.label) + (o.help ? '<small>' + U.esc(o.help) + '</small>' : '') });
    return ui.h('div', { class: 'inline-field' }, [left, o.control]);
  };
  // Stacked: label on top, control, help below.
  ui.field = function (o) {
    var el = ui.h('div', { class: 'field' }, [ui.h('span', { class: 'lbl', text: o.label }), o.control]);
    if (o.help) el.appendChild(ui.h('small', { class: 'help', text: o.help }));
    return el;
  };

  /* ---------------- alerts + empty states */
  // kind: 'err' (default) | 'warn' | 'info'. log: raw error text (shown in a scrollable mono block).
  ui.alert = function (o) {
    var kind = o.kind || 'err', icon = o.icon || (kind === 'warn' ? 'alert' : kind === 'info' ? 'info' : 'alert');
    var body = ui.h('div', { html: '<b>' + U.esc(o.title || '') + '</b>' + (o.text ? '<p>' + U.esc(o.text) + '</p>' : '') });
    if (o.log) body.appendChild(ui.h('pre', { class: 'log', text: o.log }));
    if (o.actions && o.actions.length) {
      var row = ui.h('div', { class: 'btn-row' });
      o.actions.forEach(function (a, i) { row.appendChild(ui.button({ label: a.label, icon: a.icon, small: true, kind: a.kind || (i ? 'ghost' : ''), onClick: a.onClick })); });
      body.appendChild(row);
    }
    var el = ui.h('div', { class: 'alert' + (kind === 'warn' ? ' alert-warn' : kind === 'info' ? ' alert-info' : ''), role: kind === 'err' ? 'alert' : 'status', html: ui.icon(icon) });
    el.appendChild(body);
    return el;
  };
  ui.empty = function (o) {
    var el = ui.h('div', { class: 'empty' });
    el.innerHTML = '<span class="empty-ic">' + ui.icon(o.icon || 'info') + '</span><h2>' + U.esc(o.title || '') + '</h2>' + (o.text ? '<p>' + U.esc(o.text) + '</p>' : '');
    if (o.steps && o.steps.length) {
      el.appendChild(ui.h('ol', { class: 'steps-list', html: o.steps.map(function (s, i) { return '<li><b>' + (i + 1) + '</b>' + U.esc(s) + '</li>'; }).join('') }));
    }
    if (o.action) el.appendChild(ui.button({ label: o.action.label, icon: o.action.icon, small: true, onClick: o.action.onClick }));
    return el;
  };

  /* ---------------- toasts (one at a time; aria-live container) */
  var toastTimer = null;
  ui.toast = function (msg, opts) {
    if (typeof opts === 'string') opts = { icon: opts };
    opts = opts || {};
    var box = document.getElementById('toasts'); if (!box) return;
    var kind = opts.kind || '';
    var icon = opts.icon || (kind === 'err' ? 'alert' : kind === 'ok' ? 'check' : 'info');
    var t = ui.h('div', { class: 'toast' + (kind ? ' is-' + kind : ''), html: ui.icon(icon) });
    t.appendChild(ui.h('span', { text: msg }));
    if (opts.action) t.appendChild(ui.h('button', { class: 'linkbtn', type: 'button', text: opts.action.label, on: { click: function () { box.innerHTML = ''; opts.action.run(); } } }));
    box.innerHTML = ''; box.appendChild(t);
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { if (t.parentNode === box) box.innerHTML = ''; }, opts.ms || (opts.action ? 6000 : 2600));
  };
  ui.copy = function (text, what) {
    var ok = U.copyText(text);
    ui.toast(ok ? AC.t('ui.copied', { what: what || AC.t('ui.copyDetail') }) : AC.t('ui.copyFailed'), { icon: ok ? 'copy' : 'alert', kind: ok ? '' : 'err' });
    return ok;
  };

  /* ---------------- dock (sticky bottom bar; one primary action; Ctrl+Enter clicks #primary) */
  var dockSpec = null;
  ui.dock = {
    // spec: {primary: {label, icon, onClick, disabled, kbd: true}, left: [buttonOpts], right: [buttonOpts]} or null
    show: function (spec) {
      dockSpec = spec || null;
      var d = document.getElementById('dock'); if (!d) return;
      d.innerHTML = '';
      if (!spec) return;
      (spec.left || []).forEach(function (b) { d.appendChild(ui.button(U.assign({ kind: b.kind || '' }, b))); });
      if (spec.grow) d.appendChild(ui.h('span', { class: 'grow' }));
      if (spec.primary) {
        var p = spec.primary;
        d.appendChild(ui.button({ id: 'primary', kind: 'primary', label: p.label, icon: p.icon, disabled: !!p.disabled,
                                  kbd: p.kbd === false ? '' : 'Ctrl Enter', title: p.title, onClick: function (e) { if (p.onClick) p.onClick(e); } }));
      }
      (spec.right || []).forEach(function (b) {
        if (b.iconOnly) d.appendChild(ui.h('button', { class: 'ibtn', type: 'button', 'aria-label': b.label, title: b.label, html: ui.icon(b.icon), on: { click: b.onClick } }));
        else d.appendChild(ui.button(U.assign({ kind: b.kind || '' }, b)));
      });
    },
    // Live label/disabled updates without re-rendering ("Cut 16 pauses").
    update: function (patch) {
      if (!dockSpec || !dockSpec.primary) return;
      U.assign(dockSpec.primary, patch);
      var b = document.getElementById('primary'); if (!b) return;
      if (patch.label !== undefined) { var l = b.querySelector('.lbl'); if (l) l.textContent = patch.label; }
      if (patch.disabled !== undefined) b.disabled = !!patch.disabled;
      if (patch.title !== undefined) b.title = patch.title || '';
    },
    spec: function () { return dockSpec; }
  };

  /* ---------------- source card (what will be processed) */
  function scopes() { return [{ value: 'all', label: AC.t('ui.scopeAll') }, { value: 'inout', label: AC.t('ui.scopeInOut') }, { value: 'selected', label: AC.t('ui.scopeSelected') }]; }
  ui.fps = function (f) { return (Math.abs(f - Math.round(f)) < 0.01 ? String(Math.round(f)) : U.dec(f, 2)) + ' fps'; };
  // opts: {scope: bool, ribbon: bool, tags: bool, scopes: ['all','inout','selected'], onScope(scope)}
  ui.sourceCard = function (o) {
    o = o || {};
    var el = ui.h('div', { class: 'card src' });
    var scope = o.scope ? AC.store.get('scope', 'all') : 'all';
    var seg = null, ribbon = null, hint = null, tags = null, lastId = null, extraTags = '';
    var row = ui.h('div', { class: 'src-row' });
    el.appendChild(row);
    if (o.ribbon) { ribbon = ui.ribbon({ size: 'sm', label: AC.t('ui.ribbonLabel') }); el.appendChild(ribbon.el); }
    if (o.tags) { tags = ui.h('div', { class: 'tags' }); el.appendChild(tags); }
    if (o.scope) {
      var allowed = o.scopes || ['all', 'inout', 'selected'];
      seg = ui.segmented({ small: true, ariaLabel: AC.t('ui.scope'), value: scope,
        options: scopes().filter(function (s) { return allowed.indexOf(s.value) >= 0; }),
        onChange: function (v) { scope = v; AC.store.set('scope', v); paintHint(AC.seq.peek()); if (o.onScope) o.onScope(ctrl.scope()); } });
      el.appendChild(seg.el);
      hint = ui.h('small', { class: 'seg-hint' }); el.appendChild(hint);
    }
    function meta(s) {
      var main = AC.seq.mainTrack(s), n = main ? main.count : 0;
      return [U.mmss(s.duration), AC.t('ui.clipCount', { n: n }), s.width + 'x' + s.height, ui.fps(s.fps)];
    }
    function paintHint(s) {
      if (!seg || !s) return;
      var hasIO = s.inPoint !== null && s.outPoint !== null && s.outPoint > s.inPoint;
      seg.disable('inout', !hasIO, hasIO ? '' : AC.t('ui.noInOut'));
      seg.disable('selected', !s.selectedCount, s.selectedCount ? '' : AC.t('ui.noSelection'));
      if ((scope === 'inout' && !hasIO) || (scope === 'selected' && !s.selectedCount)) { scope = 'all'; seg.set('all', true); }
      var txt = scope === 'inout' ? AC.t('ui.hintInOut', { a: U.tcode(s.inPoint), b: U.tcode(s.outPoint), dur: U.dtk(s.outPoint - s.inPoint) })
        : scope === 'selected' ? AC.t('ui.hintSelected', { n: s.selectedCount }) : AC.t('ui.hintAll', { dur: U.mmss(s.duration) });
      hint.textContent = txt;
    }
    function paint(s) {
      var locked = !!AC.seq.locked();
      if (!s) {
        row.innerHTML = '<span class="src-ic is-off">' + ui.icon('seq') + '</span><div class="src-main"><div class="src-name">' + U.esc(AC.t('ui.noSeq')) + '</div><div class="meta"><span>' + U.esc(AC.t('ui.noSeqHint')) + '</span></div></div>';
        row.appendChild(ui.h('button', { class: 'ibtn', type: 'button', 'aria-label': AC.t('ui.redetect'), title: AC.t('ui.redetect'), html: ui.icon('refresh'), on: { click: function () { AC.seq.refresh(); } } }));
        if (ribbon) ribbon.el.hidden = true;
        if (tags) tags.innerHTML = '';
        if (seg) { seg.el.hidden = true; hint.hidden = true; }
        el.classList.add('is-empty');
        return;
      }
      el.classList.remove('is-empty');
      row.innerHTML = '<span class="src-ic">' + ui.icon('seq') + '</span><div class="src-main"><div class="src-name" data-i18n-skip></div><div class="meta">' +
        meta(s).map(function (m) { return '<span>' + U.esc(m) + '</span>'; }).join('') + '</div></div>';
      var nm = row.querySelector('.src-name'); nm.textContent = s.name; nm.title = s.name;
      var lockBtn = ui.h('button', { class: 'ibtn', type: 'button', 'aria-pressed': locked ? 'true' : 'false', 'aria-label': AC.t('ui.lockSource'),
        title: AC.t('ui.lockSourceTitle'), html: ui.icon(locked ? 'lock' : 'unlock') });
      lockBtn.addEventListener('click', function () {
        var on = !AC.seq.locked();
        AC.seq.lock(on);
        ui.toast(on ? AC.t('ui.lockedToast') : AC.t('ui.unlockedToast'), on ? 'lock' : 'unlock');
      });
      row.appendChild(lockBtn);
      if (ribbon) {
        ribbon.el.hidden = false;
        var spans = s.spans || [], gaps = [], prev = 0;
        spans.forEach(function (sp) { if (sp[0] - prev > 0.05) gaps.push({ t0: prev, t1: sp[0], off: true }); prev = Math.max(prev, sp[1]); });
        ribbon.set({ total: s.duration, spans: gaps, edges: spans.map(function (sp) { return sp[0]; }).slice(1), playhead: s.player });
      }
      if (lastId !== s.id) { lastId = s.id; extraTags = ''; }
      paintTags();
      if (seg) { seg.el.hidden = false; hint.hidden = false; paintHint(s); }
    }
    function paintTags() {
      if (!tags) return;
      var h = extraTags;
      if (AC.engine.lastHealth && AC.engine.lastHealth.ai && AC.engine.lastHealth.ai.ok && AC.settings.get('ai')) h += ui.tag(AC.t('ui.aiConnected'), 'ai', 'spark');
      tags.innerHTML = h;
    }
    var onChange = function (s) { paint(s); };
    var onLock = function () { paint(AC.seq.peek()); };
    var onHealth = function () { paintTags(); };
    AC.seq.on('change', onChange); AC.seq.on('lock', onLock); AC.bus.on('health', onHealth);
    var ctrl = {
      el: el,
      // {kind:'all'|'inout'|'selected', t0, t1}. 'selected': the engine reads job.seq.selection.
      scope: function () {
        var s = AC.seq.peek();
        if (!s) return { kind: scope };
        if (scope === 'inout' && s.inPoint !== null && s.outPoint !== null) return { kind: 'inout', t0: s.inPoint, t1: s.outPoint };
        if (scope === 'selected' && s.selectedCount) return { kind: 'selected' };
        return { kind: 'all', t0: 0, t1: s.duration };
      },
      // Extra tags before the automatic "AI connected" tag (e.g. a saved transcript tag); reset when the sequence changes.
      setTags: function (html) { extraTags = html || ''; paintTags(); },
      refresh: function () { paint(AC.seq.peek()); },
      destroy: function () { AC.seq.off('change', onChange); AC.seq.off('lock', onLock); AC.bus.off('health', onHealth); }
    };
    paint(AC.seq.peek());
    return ctrl;
  };
})();
