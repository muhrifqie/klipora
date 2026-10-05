/* keys.js: AC.keys. Registers the panel's keys with CEP (so Premiere does not swallow Space/J/K/arrows while the
   panel has focus, ux_design.md section 9) and runs the global shortcuts:
   Ctrl+K palette, Ctrl+Enter dock primary, Esc (overlay > popover > page > back), ? shortcut sheet.
   Pages/tools handle their own keys via def.onKey(e, ctx) (return true when handled). Single-letter keys are only
   handled inside focused lists, never globally. */
(function () {
  'use strict';
  var AC = window.AC;
  var BASE = [
    { keyCode: 75, ctrlKey: true }, { keyCode: 13, ctrlKey: true },                      // Ctrl+K, Ctrl+Enter
    { keyCode: 13 }, { keyCode: 27 }, { keyCode: 32 },                                    // Enter, Esc, Space
    { keyCode: 37 }, { keyCode: 38 }, { keyCode: 39 }, { keyCode: 40 },                   // arrows
    { keyCode: 36 }, { keyCode: 35 },                                                     // Home, End
    { keyCode: 74 }, { keyCode: 75 }, { keyCode: 65 }, { keyCode: 72 }, { keyCode: 66 }, { keyCode: 46 }, { keyCode: 113 }, // J K A H B Del F2
    { keyCode: 37, altKey: true }, { keyCode: 39, altKey: true },
    { keyCode: 37, altKey: true, shiftKey: true }, { keyCode: 39, altKey: true, shiftKey: true },
    { keyCode: 49, altKey: true }, { keyCode: 50, altKey: true }, { keyCode: 51, altKey: true }, { keyCode: 52, altKey: true }, { keyCode: 53, altKey: true },
    { keyCode: 191, shiftKey: true }                                                      // ?
  ];
  var interest = BASE.slice(), escHandlers = [];
  var K = AC.keys = {};

  function push() {
    try { if (window.__adobe_cep__ && window.__adobe_cep__.registerKeyEventsInterest) window.__adobe_cep__.registerKeyEventsInterest(JSON.stringify(interest)); }
    catch (e) { AC.log.warn('registerKeyEventsInterest failed: ' + e.message); }
  }
  // Add more keys (Windows virtual-key codes), e.g. AC.keys.register([{keyCode: 83, ctrlKey: true}]).
  K.register = function (list) { interest = interest.concat(list || []); push(); };
  K.interest = function () { return interest.slice(); };

  K.typing = function () {
    var a = document.activeElement;
    if (!a || a === document.body) return false;
    if (a.isContentEditable) return true;
    return /INPUT|TEXTAREA|SELECT/.test(a.tagName) && a.type !== 'checkbox' && a.type !== 'radio' && a.type !== 'range' && a.type !== 'button';
  };
  // Esc layers (popovers etc.): fn() returns true when it closed something. Last registered runs first.
  K.onEscape = function (fn) { escHandlers.push(fn); return function () { escHandlers = escHandlers.filter(function (f) { return f !== fn; }); }; };

  function onKeyDown(e) {
    var key = e.key || '';
    if ((e.ctrlKey || e.metaKey) && key.toLowerCase() === 'k') { e.preventDefault(); AC.palette.toggle(); return; }
    if ((e.ctrlKey || e.metaKey) && key === 'Enter') {
      var p = document.getElementById('primary');
      if (p && !p.disabled && !AC.palette.isOpen()) { e.preventDefault(); p.click(); }
      return;
    }
    if (key === 'Escape') {
      if (AC.palette.isOpen() || AC.palette.keysOpen()) { e.preventDefault(); AC.palette.close(); return; }
      for (var i = escHandlers.length - 1; i >= 0; i--) { if (escHandlers[i](e)) { e.preventDefault(); return; } }
      if (AC.router.escape(e, K.typing())) e.preventDefault();
      return;
    }
    if (AC.palette.isOpen()) return;
    if (AC.router.pageKey(e)) return;
    if (K.typing()) return;
    if (key === '?') { e.preventDefault(); AC.palette.openKeys(); }
  }

  K.init = function () {
    push();
    document.addEventListener('keydown', onKeyDown);
  };
})();
