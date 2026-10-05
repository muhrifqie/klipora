// captions_gallery.mjs: engine template library + animated gallery contract in headless Chrome (real engine).
//   node tools/ui_test.mjs tools/ui_tests/captions_gallery.mjs --engine live
// Runs `templates` (groups, swatches, labels) and `gallery {anim: true}` (sprite strips, lazy batches) through
// AC.engine.worker, then builds a throw-away demo grid in the page that animates each strip with CSS steps()
// (the exact technique documented in docs/CAPTIONS_API.md 3.3.1 for the editor), and checks the frames advance.
// It does not touch the editor DOM (owned by captions_style.js); screenshots: docs/shots/v3_gallery_*.png.
export default async function (t) {
  const ev = (s) => t.ev(s);
  if (t.engine !== 'live') { t.check('captions_gallery: needs --engine live', true); return; }
  const ub = (process.env.APPDATA || '') + String.fromCharCode(92) + 'Python';
  await ev(`(window.process.env.PYTHONUSERBASE = ${JSON.stringify(ub)}, AC.engine.stopWorker(), true)`);
  await t.go('tool/captions');
  await t.wait(300);

  const tp = await ev(`AC.engine.worker({tool: 'captions', action: 'templates', params: {options: false}, seq: null}).promise`);
  t.check('templates: 45+ templates', tp && tp.templates && tp.templates.length >= 45, tp && tp.templates && tp.templates.length);
  t.check('templates: 11 groups with counts', tp.groups.length === 11 && tp.groups.every((g) => g.count > 0), JSON.stringify(tp.groups));
  const row = tp.templates.find((r) => r.id === 'kuning_3d') || {};
  t.check('templates: row metadata', !!(row.group && row.colors && row.colors.length && row.anim_in_label && row.is_new === true), JSON.stringify(row).slice(0, 300));

  // lazy batches: first call renders at most 6 new strips, the rest come back as pending
  const ids = ['kuning_3d', 'stiker_viral', 'gradasi_api', 'komik_ledak', 'neon_gamer', 'promo_spanduk', 'kartu_podcast',
    'berita_terkini', 'stabilo_tutorial', 'pastel_lembut', 'hormozi_kuning', 'esports'];
  const req = (extra) => `AC.engine.worker({tool: 'captions', action: 'gallery', seq: null, params: Object.assign({anim: true, ids: ${JSON.stringify(ids)}}, ${JSON.stringify(extra)})}).promise`;
  const g1 = await ev(req({ limit: 6, force: true }));
  t.check('anim: first batch limited to 6', Object.keys(g1.anims).length === 6 && g1.pending.length === 6, JSON.stringify(g1.pending));
  const g2 = await ev(req({}));
  t.check('anim: second call completes the batch', Object.keys(g2.anims).length === 12 && !g2.pending.length && !Object.keys(g2.errors).length);
  const t0 = Date.now();
  const g3 = await ev(req({}));
  t.check('anim: cached call is fast and all cached', Object.values(g3.anims).every((a) => a.cached) && Date.now() - t0 < 2500, (Date.now() - t0) + ' ms');

  // demo grid: one card per template, strip as background, CSS steps() animation (Chrome 99 safe)
  await ev(`(function (data, rows) {
    var css = document.createElement('style');
    css.textContent = '#gdemo{position:fixed;inset:0;z-index:9999;background:var(--bg,#16181d);overflow:auto;padding:10px;font:12px/1.3 sans-serif;color:#e8e8e8}' +
      '#gdemo .g{display:grid;grid-template-columns:repeat(2,1fr);gap:8px}' +
      '#gdemo .c{border-radius:8px;overflow:hidden;background:#222}' +
      '#gdemo .s{width:100%;aspect-ratio:2/1;background-repeat:no-repeat;background-size:' + (data.frames * 100) + '% 100%;' +
      'animation:gstep ' + (data.frames / data.fps) + 's steps(' + data.frames + ', jump-none) infinite}' +
      '@keyframes gstep{from{background-position:0% 0}to{background-position:100% 0}}' +
      '#gdemo .n{padding:4px 6px;display:flex;justify-content:space-between;gap:4px;align-items:center}' +
      '#gdemo .d{display:inline-block;width:8px;height:8px;border-radius:50%;margin-left:2px;box-shadow:0 0 0 1px #0006}' +
      '#gdemo .h{font-weight:600;margin:2px 0 8px}';
    document.head.appendChild(css);
    var root = document.createElement('div'); root.id = 'gdemo';
    root.innerHTML = '<div class="h">Galeri animasi (demo kontrak engine)</div><div class="g"></div>';
    var grid = root.querySelector('.g');
    Object.keys(data.anims).forEach(function (id) {
      var a = data.anims[id], r = rows.filter(function (x) { return x.id === id; })[0] || {};
      var c = document.createElement('div'); c.className = 'c';
      var s = document.createElement('div'); s.className = 's'; s.setAttribute('data-id', id);
      s.style.backgroundImage = 'url("' + AC.cap.fileUrl(a.png) + '")';
      var n = document.createElement('div'); n.className = 'n';
      var dots = (r.colors || []).map(function (col) { return '<i class="d" style="background:' + col + '"></i>'; }).join('');
      n.innerHTML = '<span>' + (r.name || id) + '</span><span>' + dots + '</span>';
      c.appendChild(s); c.appendChild(n); grid.appendChild(c);
    });
    document.body.appendChild(root);
    return true;
  })(${JSON.stringify({ anims: g3.anims, frames: g3.frames, fps: g3.fps })}, ${JSON.stringify(tp.templates)})`);
  await t.waitFor(`Array.prototype.every.call(document.querySelectorAll('#gdemo .s'), function (e) { return getComputedStyle(e).backgroundImage.indexOf('url(') === 0; })`, 5000, 'strips');
  const loaded = await ev(`Promise.all(Array.prototype.map.call(document.querySelectorAll('#gdemo .s'), function (e) {
    return new Promise(function (res) { var i = new Image(); i.onload = function () { res(i.naturalWidth); }; i.onerror = function () { res(0); };
      i.src = getComputedStyle(e).backgroundImage.slice(5, -2); }); }))`);
  t.check('demo: every strip loads (16 x 320 px wide)', loaded.length === 12 && loaded.every((w) => w === 5120), JSON.stringify(loaded));
  const pos = async () => ev(`Array.prototype.map.call(document.querySelectorAll('#gdemo .s'), function (e) { return getComputedStyle(e).backgroundPositionX; })`);
  await t.wait(500);
  const p1 = await pos();
  await t.shot('v3_gallery_anim_a');
  await t.wait(640);
  const p2 = await pos();
  await t.shot('v3_gallery_anim_b');
  t.check('demo: steps() animation advances frames', p1.some((v, i) => v !== p2[i]), JSON.stringify([p1[0], p2[0]]));
  t.check('demo: positions are whole frames', p2.every((v) => { const f = parseFloat(v) / 100 * 15; return Math.abs(f - Math.round(f)) < 0.02; }), JSON.stringify(p2));

  // template_mix / template_random through the worker, applied to nothing (contract only)
  const mix = await ev(`AC.engine.worker({tool: 'captions', action: 'template_mix', seq: null, params: {parts: {font: 'kuning_3d', box: 'pita_diskon', highlight: 'stiker_viral'}, base: 'hormozi_kuning'}}).promise`);
  t.check('template_mix: ops + style', mix.ops && mix.ops.length === 2 && mix.ops[1].replace === true && mix.template === 'hormozi_kuning', JSON.stringify(mix).slice(0, 200));
  const rnd = await ev(`AC.engine.worker({tool: 'captions', action: 'template_random', seq: {width: 1080, height: 1920}, params: {seed: 42}}).promise`);
  t.check('template_random: seeded style', rnd.seed === 42 && rnd.full && rnd.full.font && rnd.name.indexOf('Acak') === 0, rnd.name);
  await ev(`(document.getElementById('gdemo').remove(), true)`);

  // English UI: the panel sends job.lang = 'en', the engine answers with the same contract and English labels
  // (group chips next to "All" / "Fits" in the Template tab, names, animation labels). "per" is English too.
  const { ID_WORDS } = await import('../i18n_check.mjs');
  const ID = new Set(ID_WORDS.filter((w) => w !== 'per'));
  const idw = (s) => String(s || '').split(/[^A-Za-zÀ-ÿ]+/).filter((w) => ID.has(w.toLowerCase()));
  await ev(`AC.i18n.set('en')`);
  await t.waitFor(`AC.i18n.lang() === 'en'`, 10000, 'en');
  const tpEn = await ev(`AC.engine.worker({tool: 'captions', action: 'templates', params: {options: false}, seq: null}).promise`);
  t.check('en: same templates and groups', JSON.stringify(tpEn.templates.map((r) => r.id)) === JSON.stringify(tp.templates.map((r) => r.id)) &&
    JSON.stringify(tpEn.groups.map((g) => g.id + g.count)) === JSON.stringify(tp.groups.map((g) => g.id + g.count)));
  const grpLeft = tpEn.groups.filter((g) => idw(g.label).length).map((g) => g.label);
  t.check('en: group labels in English', !grpLeft.length && tpEn.groups.some((g, i) => g.label !== tp.groups[i].label), JSON.stringify(grpLeft));
  const animLeft = [...new Set(tpEn.templates.map((r) => r.anim_in_label).filter((x) => idw(x).length))];
  t.check('en: template animation labels in English', !animLeft.length, JSON.stringify(animLeft));
  await ev(`AC.i18n.set('id')`);
  await t.waitFor(`AC.i18n.lang() === 'id'`, 10000, 'id again');
}
