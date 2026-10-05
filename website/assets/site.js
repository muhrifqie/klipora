// Progressive enhancement only: every page is fully readable without this file.
(function () {
  var doc = document.documentElement;
  doc.classList.add('js');

  // Reveal on scroll
  var items = document.querySelectorAll('.reveal');
  if ('IntersectionObserver' in window) {
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (e) {
        if (e.isIntersecting) { e.target.classList.add('in'); io.unobserve(e.target); }
      });
    }, { rootMargin: '0px 0px -8% 0px', threshold: 0.08 });
    items.forEach(function (el) { io.observe(el); });
  } else {
    items.forEach(function (el) { el.classList.add('in'); });
  }

  // Copy buttons on code blocks
  document.querySelectorAll('.copy-btn').forEach(function (btn) {
    btn.addEventListener('click', function () {
      var pre = btn.parentNode.querySelector('pre');
      var text = Array.prototype.map.call(pre.querySelectorAll('.ln'), function (l) { return l.textContent; }).join('\n') || pre.textContent;
      var done = function () {
        btn.setAttribute('data-done', '');
        btn.setAttribute('aria-label', btn.getAttribute('data-copied'));
        setTimeout(function () { btn.removeAttribute('data-done'); btn.setAttribute('aria-label', btn.getAttribute('data-label')); }, 1600);
      };
      if (navigator.clipboard) navigator.clipboard.writeText(text).then(done, function () {});
    });
  });

  // Close the mobile menu after picking a link
  document.querySelectorAll('.menu-panel a, .toc-mobile a').forEach(function (a) {
    a.addEventListener('click', function () {
      var d = a.closest('details');
      if (d) d.removeAttribute('open');
    });
  });

  // Docs: highlight the current section in the table of contents
  var toc = document.querySelectorAll('.doc-layout > .toc a[href^="#"]');
  if (toc.length && 'IntersectionObserver' in window) {
    var map = {};
    toc.forEach(function (a) { map[a.getAttribute('href').slice(1)] = a; });
    var spy = new IntersectionObserver(function (entries) {
      entries.forEach(function (e) {
        if (!e.isIntersecting) return;
        toc.forEach(function (a) { a.classList.remove('active'); });
        var a = map[e.target.id];
        if (a) a.classList.add('active');
      });
    }, { rootMargin: '-20% 0px -70% 0px' });
    Object.keys(map).forEach(function (id) { var el = document.getElementById(id); if (el) spy.observe(el); });
  }
})();
