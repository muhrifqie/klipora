// silence_en.mjs: Cut Silences in English (Premiere UI locale en_US) with the REAL engine (--engine live).
// node tools/ui_test.mjs tools/ui_tests/silence_en.mjs            (fixture raw49 = the 49 s test clip, cached transcript)
// Checks: exact English title / tab / CTA, English review rows ("Pause 0.42 s", "removed"), the result card, and no
// visible Indonesian leftovers on the main, review and result panes (user content is marked data-i18n-skip).
export default async function (t) {
  const ev = (s) => t.ev(s);
  const rt = `AC.tools.runtime('silence')`;
  const pv = `${rt}.ctx.silence`;
  const S = '[data-screen="tool-silence"]';
  // Quoted transcript words inside notes ("Edge moved, the word "jadi" stays intact") are user content, not UI.
  const leftovers = async (sel) => {
    const raw = await t.idLeftovers(sel);
    const { ID_WORDS } = await import(new URL('../i18n_check.mjs', import.meta.url).href);
    const set = new Set(ID_WORDS);
    return raw.filter((s) => {
      const bare = s.replace(/"[^"]*"/g, ' ');
      return /(^|[^A-Za-z])(dtk|mnt)([^A-Za-z]|$)/.test(bare) || bare.split(/[^A-Za-zÀ-ÿ]+/).some((w) => set.has(w.toLowerCase()));
    });
  };

  await t.load({ locale: 'en_US' });
  t.check('boot: English UI from the Premiere locale', (await ev('AC.i18n.lang()')) === 'en');
  if (t.engine !== 'live') {
    await t.go('tool/silence');
    t.check('mock engine: English CTA', (await t.text('#primary')).indexOf('Detect pauses') === 0, await t.text('#primary'));
    console.log('silence_en.mjs: full checks need --engine live');
    return;
  }

  // ---- main pane
  await t.go('tool/silence');
  await t.waitFor(`${rt} && ${pv} && ${pv}.data && ${pv}.live`, 30000, 'silence preview');
  const def = await ev(`(function(){ var d=${rt}.ctx.def; return {title: d.title, tab: d.tab, cta: d.cta, unit: d.review && d.review.unit}; })()`);
  t.check('texts: title / tab / cta / review unit', def.title === 'Cut Silences' && def.tab === 'Silences' && def.cta === 'Detect pauses' && def.unit === 'pauses', JSON.stringify(def));
  t.check('appbar title', (await t.text('#crumbTitle')) === 'Cut Silences', await t.text('#crumbTitle'));
  const tabs = await ev(`Array.prototype.map.call(document.querySelectorAll('#subnav button'), function(b){return b.textContent;})`);
  t.check('subnav tab "Silences"', tabs.indexOf('Silences') >= 0, tabs.join('|'));
  const n = await ev(`${pv}.live.cuts.length`);
  t.check('dock: "Review N pauses"', (await t.text('#primary')).indexOf('Review ' + n + ' pauses') === 0, await t.text('#primary'));
  t.check('strip + tags in English', /Pauses/.test(await t.text(`${S} .silence-strip`)) && /Threshold -53 dB, auto/.test(await t.text(`${S} .silence-info`)) &&
    /words protected/.test(await t.text(`${S} .silence-info`)), await t.text(`${S} .silence-info`));
  t.check('preset hint in English', /Pauses over 0\.5 s are removed/.test(await t.text(`${S} .tool-body .help`)), await t.text(`${S} .tool-body .help`));
  await ev(`(document.querySelector('${S} details').open = true, true)`);
  await t.wait(120);
  t.check('advanced: slider units "s"', /0\.50 s/.test(await t.text(`${S} details`)) && !/dtk/.test(await t.text(`${S} details`)), (await t.text(`${S} details`)).slice(0, 200));
  let left = await leftovers('#app');
  t.check('main pane: no Indonesian leftovers', left.length === 0, JSON.stringify(left));
  await ev(`(document.querySelector('${S} details').open = false, true)`);

  // ---- analyze (live engine) -> review
  await t.click('#primary');
  await t.waitFor(`location.hash === '#tool/silence/review'`, 60000, 'review pane');
  await t.wait(200);
  const rows = await ev(`Array.prototype.map.call(document.querySelectorAll('${S} .rv .rv-ctx mark'), function(m){return m.textContent;})`);
  t.check('review rows: English labels ("Pause 0.42 s")', rows.length > 0 && rows.every((r) => /^Pause \d+\.\d+ s$/.test(r)), JSON.stringify(rows.slice(0, 4)));
  t.check('review rows: "removed" verb', /removed/.test(await t.text(`${S} .rv .rv-dur small`)), await t.text(`${S} .rv .rv-dur small`));
  const chips = await ev(`Array.prototype.map.call(document.querySelectorAll('${S} .rv-tools .chip'), function(c){return c.textContent;})`);
  t.check('review filters in English', chips.some((c) => /Over 1 s/.test(c)), chips.join('|'));
  t.check('review dock: "Cut N pauses"', /^Cut \d+ pauses?(?![a-z])/.test(await t.text('#primary')), await t.text('#primary'));
  left = await leftovers('#app');
  t.check('review pane: no Indonesian leftovers', left.length === 0, JSON.stringify(left));

  // ---- apply -> result card
  await t.click('#primary');
  await t.waitFor(`location.hash === '#tool/silence/result'`, 60000, 'result pane');
  await t.wait(150);
  t.check('result: English title', (await t.text(`${S} [data-pane="result"] h2`)) === 'New sequence ready', await t.text(`${S} [data-pane="result"] h2`));
  const clone = (await t.calls('bac_cloneSeq')).slice(-1)[0];
  t.check('result: clone named "(Klipora)"', clone && / \(Klipora\)$/.test(clone[0]), JSON.stringify(clone));
  left = await leftovers('#app');
  t.check('result pane: no Indonesian leftovers', left.length === 0, JSON.stringify(left));
  await t.shot('build_silence_en_result');
}
