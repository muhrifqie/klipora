// smoke_en.mjs: the core screens in English (Premiere UI locale en_US, uiLang auto) + a live language switch.
//   Home (13 tools, English group names and labels), Settings, History, a tool page (core-owned parts: source card
//   and dock), the Ctrl+K palette and the shortcut sheet: no visible Indonesian left (t.idLeftovers).
//   Live switch: boot Indonesian, AC.i18n.set('en') on Settings re-renders it in English and saves uiLang; back to 'id'.
const SEC_H = (screen) => `Array.prototype.map.call(document.querySelectorAll('.screen[data-screen="${screen}"] .sec-h > span:first-child'), function (e) { return e.textContent.trim(); })`;

export default async function (t) {
  const left = async (name, sel) => { const l = await t.idLeftovers(sel); t.check(`en: no Indonesian left on ${name}`, l.length === 0, JSON.stringify(l)); };

  // ---------------------------------------------------------------- fresh boot in English
  await t.load({ locale: 'en_US' });
  t.check('en: language follows Premiere (en_US)', (await t.ev('AC.i18n.lang()')) === 'en' && (await t.ev('AC.i18n.setting()')) === 'auto');
  t.check('en: <html lang="en">', (await t.ev('document.documentElement.lang')) === 'en');

  // Home
  await t.go('home'); await t.wait(80);
  t.check('en: home: 13 tools', (await t.count('#view .tool')) === 13, String(await t.count('#view .tool')));
  const groups = await t.ev(SEC_H('home'));
  t.check('en: home: English group names', ['Cut', 'Text', 'Camera', 'Publish'].every((g) => groups.indexOf(g) >= 0), JSON.stringify(groups));
  t.check('en: home: recipes + recent sections', groups.indexOf('One-click recipes') >= 0 && groups.indexOf('Recent') >= 0, JSON.stringify(groups));
  t.check('en: search button label', (await t.text('#btnSearch span')) === 'Search tools or actions' &&
    (await t.ev(`document.getElementById('btnSearch').getAttribute('aria-label')`)) === 'Search tools or actions (Ctrl K)');
  t.check('en: tool card title', (await t.ev(`Array.prototype.some.call(document.querySelectorAll('#view .tool-name'), function (e) { return e.textContent === 'Cut Silences'; })`)),
    await t.ev(`Array.prototype.map.call(document.querySelectorAll('#view .tool-name'), function (e) { return e.textContent; }).join(' | ')`));
  const asides = await t.ev(`Array.prototype.map.call(document.querySelectorAll('.screen[data-screen="home"] .sec-h .aside'), function (e) { return e.textContent; }).filter(Boolean)`);
  t.check('en: tool count asides', asides.length >= 4 && asides.every((x) => /^\d+ tools?$/.test(x)), JSON.stringify(asides));
  await left('Home', '#app');
  await t.shot('en_home');

  // Settings
  await t.go('settings'); await t.wait(120);
  t.check('en: settings title', (await t.text('#crumbTitle')) === 'Settings');
  const setH = await t.ev(SEC_H('settings'));
  t.check('en: settings sections', ['Engine', 'AI', 'Captions', 'Appearance', 'Data'].every((g) => setH.indexOf(g) >= 0), JSON.stringify(setH));
  t.check('en: display language select', (await t.ev(`document.getElementById('setUiLang').getAttribute('aria-label')`)) === 'Display language');
  await left('Settings', '#app');
  await t.shot('en_settings');

  // History (empty state or rows)
  await t.go('history'); await t.wait(120);
  t.check('en: history title', (await t.text('#crumbTitle')) === 'History');
  await left('History', '#app');

  // Tool page: the core-owned parts (source card, dock)
  await t.go('tool/silence'); await t.wait(150);
  t.check('en: source card scope labels', (await t.ev(`Array.prototype.map.call(document.querySelectorAll('.screen[data-screen="tool-silence"] .src .seg button'), function (b) { return b.textContent; }).join('|')`)) === 'Whole sequence|In/Out|Selected clips');
  await left('tool source card', '.screen[data-screen="tool-silence"] .src');
  await left('tool dock', '#dock');

  // Palette (Ctrl+K) + shortcut sheet
  await t.go('home');
  await t.key('k', { ctrl: true }, 'body'); await t.wait(60);
  t.check('en: palette opens', await t.ev('AC.palette.isOpen()'));
  t.check('en: palette placeholder', (await t.ev(`document.getElementById('palInput').placeholder`)) === 'Search tools, recipes, or actions');
  const palG = await t.ev(`Array.prototype.map.call(document.querySelectorAll('#palList .pal-g'), function (e) { return e.textContent; })`);
  t.check('en: palette groups', ['Tools', 'Recipes', 'Actions'].every((g) => palG.indexOf(g) >= 0), JSON.stringify(palG));
  await t.type('#palInput', 'history'); await t.wait(30);
  t.check('en: palette search finds an action', (await t.text('.pal-i[aria-selected="true"] span')) === 'Open history', await t.text('.pal-i[aria-selected="true"] span'));
  await t.type('#palInput', ''); await t.wait(30);
  await left('palette', '#ovPalette');
  await t.key('Escape', {}, '#palInput'); await t.wait(40);
  await t.ev('AC.palette.openKeys()'); await t.wait(40);
  t.check('en: shortcut sheet', (await t.text('#keysBody .sec-h span')) === 'General' && (await t.count('#keysBody dt')) > 8);
  await left('shortcut sheet', '#ovKeys');
  await t.ev('AC.palette.close()');

  // Shared job views (review list header/bulk bar, docks, result card) through the Echo dev tool. Row texts and
  // stage labels come from the engine (engine/ac/tools/echo.py), so only the core-owned parts are checked here.
  await t.ev(`AC.settings.set('developer', true)`);
  await t.go('tool/echo'); await t.wait(100);
  await t.ev(`(function(){var r=AC.tools.runtime('echo').ctx; r.state.every=4; r.state.seconds=0.3; r.state.action='analyze'; r.state.apply=false; r.save(); return true;})()`);
  await t.go('tool/silence'); await t.go('tool/echo'); await t.wait(100);
  await left('Echo main pane', '.screen[data-screen="tool-echo"] [data-pane="main"]');
  await t.click('#primary');
  await t.waitFor(`location.hash==='#tool/echo/progress'`, 3000, 'echo progress');
  t.check('en: progress dock', /Run in background/.test(await t.text('#dock')) && /Cancel/.test(await t.text('#dock')), await t.text('#dock'));
  await t.waitFor(`location.hash==='#tool/echo/review'`, 30000, 'echo review');
  await t.wait(200);
  const n = await t.ev(`AC.tools.runtime('echo').review.count()`);
  t.check('en: review dock label', (await t.text('#primary .lbl')) === `Count ${n} finding${n === 1 ? '' : 's'}`, await t.text('#primary .lbl'));
  t.check('en: review title', new RegExp(`^${n} findings? removed`).test(await t.text('.screen[data-screen="tool-echo"] .rv-title')), await t.text('.screen[data-screen="tool-echo"] .rv-title'));
  await left('review header', '.screen[data-screen="tool-echo"] .rv-head');
  await left('review bulk bar', '.screen[data-screen="tool-echo"] .rv-bulk');
  await left('review dock', '#dock');
  await t.click('#primary');
  await t.waitFor(`location.hash==='#tool/echo/result'`, 30000, 'echo result');
  t.check('en: result card', (await t.text('.screen[data-screen="tool-echo"] .result-h h2')) === 'Plan ready (dry run)');
  await left('result card', '.screen[data-screen="tool-echo"] [data-pane="result"]');
  await left('result dock', '#dock');
  await t.ev(`AC.settings.set('developer', false)`);

  // ---------------------------------------------------------------- live switch id -> en -> id on Settings
  await t.ev(`AC.settings.set('uiLang', 'auto')`);
  await t.load({ locale: 'id_ID' });
  await t.go('settings'); await t.wait(120);
  t.check('switch: boots Indonesian', (await t.ev('AC.i18n.lang()')) === 'id' && (await t.text('#crumbTitle')) === 'Pengaturan' && (await t.ev(SEC_H('settings'))).indexOf('Mesin') >= 0);
  await t.ev(`AC.i18n.set('en')`); await t.wait(150);
  const enH = await t.ev(SEC_H('settings'));
  t.check('switch: settings re-rendered in English', (await t.text('#crumbTitle')) === 'Settings' && enH.indexOf('Engine') >= 0 && enH.indexOf('Mesin') < 0, JSON.stringify(enH));
  t.check('switch: one settings screen', (await t.count('.screen[data-screen="settings"]')) === 1);
  t.check('switch: select shows English', (await t.ev(`document.getElementById('setUiLang').value`)) === 'en');
  const saved = JSON.parse(t.readSandbox('home/AppData/Roaming/Klipora/settings.json'));
  t.check('switch: uiLang saved', saved.uiLang === 'en', JSON.stringify(saved.uiLang));
  await left('Settings after live switch', '#app');
  await t.ev(`AC.i18n.set('id')`); await t.wait(150);
  t.check('switch: back to Indonesian', (await t.text('#crumbTitle')) === 'Pengaturan' && (await t.ev(SEC_H('settings'))).indexOf('Mesin') >= 0 &&
    JSON.parse(t.readSandbox('home/AppData/Roaming/Klipora/settings.json')).uiLang === 'id');
  await t.go('home'); await t.wait(80);
  t.check('switch: home Indonesian again', (await t.ev(SEC_H('home'))).indexOf('Potong') >= 0 && (await t.count('#view .tool')) === 13);
}
