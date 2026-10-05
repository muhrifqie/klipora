// captions_en.mjs: Auto Captions in English (boot with Premiere UI locale en_US) with the REAL engine.
//   node tools/ui_test.mjs tools/ui_tests/captions_en.mjs --engine live
// Walks setup -> editor (every sub tab) -> overlay apply -> result, checks exact English labels and that the parts
// owned by the captions shell (editor head, tabs, preview overlays + bar, Export tab, dock) have no Indonesian
// leftovers. Template / Text / Style / Animation tab bodies are reported per tab (checked when converted).
// Also switches the language live (en -> id -> en) while the editor is open: it must re-render without page errors.
// Host functions bac_captions_* are stubbed like captions.mjs (records calls, fake track state).
export default async function (t) {
  const ev = (s) => t.ev(s);
  if (t.engine !== 'live') { t.check('captions_en: needs --engine live', true); return; }
  const ub = (process.env.APPDATA || '') + String.fromCharCode(92) + 'Python';

  async function stubs() {
    await ev(`(window.process.env.PYTHONUSERBASE = ${JSON.stringify(ub)}, AC.engine.stopWorker(), true)`);
    await ev(`(window.__cap = { status: {hasTrack: false, track: -1, clips: 0, path: null, start: null, end: null, ours: false, locked: false, linear: true, editable: {hasTrack: false, track: -1, clips: 0}},
      placed: [], srt: [], mogrt: [], begin: [], cleared: [], linear: [] }, true)`);
    await t.host('bac_captions_player', 'function (id) { return JSON.stringify({id: __acStub.seq.id, t: __acStub.seq.player || 0}); }');
    await t.host('bac_captions_status', 'function (id) { return JSON.stringify(__cap.status); }');
    await t.host('bac_captions_applyOverlay', `function (p, x, y, relink, id, track) {
      if (!__acStub.fs.existsSync(p)) return 'ERR:Overlay file not found: ' + p;
      var st = __cap.status, mode = relink && st.clips ? 'relink' : 'place';
      __cap.placed.push({path: p, x: x, y: y, relink: relink, mode: mode, track: track});
      st.hasTrack = true; st.track = 2; st.clips = 1; st.path = p; st.start = 0; st.end = 45; st.name = track; st.ours = /_captions_v\\d+\\.mov$/i.test(p);
      return JSON.stringify({mode: mode, track: 2, start: 0, end: 45, path: p}); }`);
    await t.host('bac_captions_clear', `function (id, which) { var n = __cap.status.clips; __cap.status.clips = 0; __cap.status.path = null; __cap.cleared.push(which); return JSON.stringify({ok: true, n: n}); }`);
    await t.host('bac_captions_setLinear', `function (on) { __cap.status.linear = !!on; __cap.linear.push(!!on); return JSON.stringify({ok: true, linear: !!on}); }`);
  }
  const clean = async (name, sel) => {
    const left = await t.idLeftovers(sel);
    t.check(`en: no Indonesian leftovers in ${name}`, left.length === 0, JSON.stringify(left));
    return left;
  };
  const label = () => t.text('#primary .lbl');

  /* ------------------------------------------------------------ boot in English */
  await t.load({ locale: 'en_US' });
  await stubs();
  t.check('en: panel booted in English (Premiere locale en_US)', (await ev('AC.i18n.lang()')) === 'en');
  t.check('en: tool registry texts', (await ev(`AC.tools.get ? AC.tools.get('captions').title : AC.tools.list().filter(function(d){ return d.id === 'captions'; })[0].title`)) === 'Auto Captions');

  /* ------------------------------------------------------------ setup */
  await t.go('tool/captions');
  await t.waitFor(`AC.cap.S.view === 'setup'`, 5000, 'setup view');
  await t.waitFor(`AC.cap.S.tpl && document.querySelectorAll('.cap-setup select option').length >= 19`, 30000, 'templates loaded');
  t.check('en setup: primary = "Create captions"', (await label()) === 'Create captions', await label());
  t.check('en setup: recommended template marked in English', /\(recommended\)$/.test(await ev(`document.querySelector('.cap-setup select').selectedOptions[0].textContent`)));
  t.check('en setup: section titles', /Starting template/.test(await t.text('.cap-setup')) && /Glossary/.test(await t.text('.cap-setup')) && /Hide uh, um, hmm/.test(await t.text('.cap-setup')));
  // template names come from the engine template library (content of package C): skip the select itself
  await ev(`(document.querySelector('.cap-setup select').setAttribute('data-i18n-skip', ''), true)`);
  await clean('setup', '.cap-setup');

  /* ------------------------------------------------------------ build -> editor */
  await t.click('#primary');
  await t.waitFor(`AC.cap.S.view === 'editor'`, 120000, 'editor after build');
  const n = await ev('AC.cap.S.doc.pages.length');
  t.check(`en editor: dock primary = "Apply ${n} captions"`, (await label()) === `Apply ${n} captions`, await label());
  t.check('en editor: dock "Save style"', /Save style/.test(await t.text('#cap-save-style') || ''));
  const tabs = await ev(`Array.prototype.map.call(document.querySelectorAll('.cap-tabs [role=tab]'), function(b){ return b.textContent.trim(); }).join('|')`);
  t.check('en editor: tabs Template | Text | Style | Animation | Export', tabs === 'Template|Text|Style|Animation|Export', tabs);
  t.check('en editor: head "N captions, M words"', new RegExp(`^${n} captions?, [\\d,]+ words?$`).test(await ev(`document.querySelector('.cap-head span:nth-child(3)').textContent`)));
  await t.waitFor(`AC.cap.stats.previews.length > 0 && document.querySelector('.cap-frame').naturalWidth > 0`, 30000, 'first preview');
  t.check('en preview: safe zone label + bar buttons', /^Safe zone /.test(await t.text('.cap-safe span')) &&
    (await t.count('.cap-bar [aria-label="Play 3 s with animation"]')) === 1 && (await t.count('.cap-bar [aria-label="Next caption"]')) === 1);
  await clean('editor head + tabs', '.cap-head');
  await clean('editor tabs', '.cap-tabs');
  await clean('preview (stage, overlays, bar)', '.cap-preview');
  await clean('dock', '#dock');

  /* ------------------------------------------------------------ every sub tab */
  const owners = { template: 'C', teks: 'B', gaya: 'B', animasi: 'C' };
  for (const id of ['template', 'teks', 'gaya', 'animasi', 'ekspor']) {
    await t.click(`#cap-tab-${id}`);
    await t.wait(400);
    const on = await ev(`document.querySelector('#cap-tab-${id}').getAttribute('aria-selected') === 'true' && document.querySelector('[data-p="${id}"]').classList.contains('is-on')`);
    t.check(`en tab ${id}: opens`, on);
    if (id === 'ekspor') continue;
    const left = await t.idLeftovers(`[data-p="${id}"]`);
    // tab bodies of packages B / C: reported here, strict once they are converted
    t.check(`en tab ${id} (package ${owners[id]}): ${left.length ? left.length + ' Indonesian leftover(s): ' + JSON.stringify(left.slice(0, 4)) : 'no Indonesian leftovers'}`,
      process.env.CAP_EN_STRICT ? left.length === 0 : true);
  }
  // Export tab (shell package): exact labels + leftover-free
  t.check('en export: output cards', /Transparent video overlay/.test(await t.text('[data-p="ekspor"]')) && /Recommended/.test(await t.text('[data-p="ekspor"] .opt.is-on')) &&
    /Premiere captions \(SRT\)/.test(await t.text('[data-p="ekspor"]')) && /Editable MOGRT/.test(await t.text('[data-p="ekspor"]')));
  t.check('en export: overlay card names the "Klipora Captions" track', /"Klipora Captions"/.test(await t.text('[data-p="ekspor"] .opt.is-on')));
  t.check('en export: timeline status', /No Klipora overlay in this sequence yet\.|Reading the timeline/.test(await t.text('[data-p="ekspor"]')));
  await ev(`(function(){ var b = document.querySelector('[data-p="ekspor"] details summary, [data-p="ekspor"] .adv-h, [data-p="ekspor"] .adv button'); if (b) b.click(); return true; })()`);
  await clean('Export tab', '[data-p="ekspor"]');

  /* ------------------------------------------------------------ live switch en -> id -> en with the editor open */
  const errs0 = t.errors.length;
  await ev(`(AC.i18n.set('id'), true)`);
  await t.waitFor(`document.querySelector('#cap-tab-teks') && document.querySelector('#cap-tab-teks').textContent.trim() === 'Teks'`, 8000, 'editor re-rendered in Indonesian');
  t.check('switch -> id: editor re-rendered (tabs + dock in Indonesian, same tab)', (await label()) === `Terapkan ${n} caption` &&
    (await t.count('.cap-tabs')) === 1 && (await ev(`document.querySelector('#cap-tab-ekspor').getAttribute('aria-selected')`)) === 'true', await label());
  await ev(`(AC.i18n.set('en'), true)`);
  await t.waitFor(`document.querySelector('#cap-tab-teks') && document.querySelector('#cap-tab-teks').textContent.trim() === 'Text'`, 8000, 'editor re-rendered in English');
  t.check('switch -> en: editor re-rendered (dock, one preview, one tab bar)', (await label()) === `Apply ${n} captions` && (await t.count('.cap-preview')) === 1 && (await t.count('.cap-tabs')) === 1);
  await t.waitFor(`document.querySelector('.cap-frame') && document.querySelector('.cap-frame').naturalWidth > 0`, 30000, 'preview after switch');
  const pBefore = await ev('AC.cap.stats.previews.length');
  await ev(`document.querySelector('.cap-bar [aria-label="Next caption"]').click()`);
  await t.waitFor(`AC.cap.stats.previews.length > ${pBefore}`, 15000, 'preview works after the switch');
  t.check('switch: preview bar works after the re-render', true);
  await clean('Export tab after the switch', '[data-p="ekspor"]');
  t.check('switch: no page errors during the live switch', t.errors.length === errs0, t.errors.slice(errs0).join(' | '));

  /* ------------------------------------------------------------ apply overlay -> English result */
  await t.click('#primary');
  await t.waitFor(`location.hash === '#tool/captions/result'`, 120000, 'overlay result');
  const pl = await ev('__cap.placed[0]');
  t.check('en apply: host got the "Klipora Captions" track name', pl && pl.track === 'Klipora Captions' && /_captions_v1\.mov$/.test(pl.path), JSON.stringify(pl));
  t.check('en apply: result title "Captions placed on the timeline"', (await t.text('[data-pane="result"] .result-h h2')) === 'Captions placed on the timeline');
  const res = await t.text('[data-pane="result"]');
  t.check('en apply: result texts (track note, stats, linear hint)', /Track "Klipora Captions" on top\. Other tracks are not changed\./.test(res) && /Version/.test(res) && /Match colors/.test(res), res.slice(0, 300));
  await clean('result card (captions part)', '[data-pane="result"] .cap-result-files');
  await ev(`(function(){ var a = document.querySelectorAll('[data-pane="result"] .alert'); return true; })()`);
  await clean('result linear hint', '[data-pane="result"] .alert-info');
  await t.click('#primary');                                   // Back to the editor
  await t.waitFor(`AC.cap.S.view === 'editor' && /On the timeline \\(v1\\)/.test(document.querySelector('#primary .lbl').textContent)`, 8000, 'dock up to date');
  t.check('en editor: dock "On the timeline (v1)"', true);
  t.check('en: no page errors', !t.errors.length, t.errors.join(' | '));
}
