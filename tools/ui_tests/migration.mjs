// migration.mjs: first start of Klipora on an old AutoCut install. %APPDATA%\AutoCutBOT (settings.json + history.json)
// is copied once to %APPDATA%\Klipora; the old folder stays; the user's settings (workRoot, density) are kept and an
// install from before the English UI (no uiLang saved) stays in Bahasa Indonesia even when Premiere runs in English.
// Harness sandbox: APPDATA = <sandbox>/home/AppData/Roaming.
import fs from 'fs';
import path from 'path';

export default async function (t) {
  const roaming = path.join(t.sandbox, 'home', 'AppData', 'Roaming');
  const oldDir = path.join(roaming, 'AutoCutBOT'), newDir = path.join(roaming, 'Klipora');
  const workRoot = 'C:\\X\\AutoCut BOT';

  // Leave the page booted by the harness, so nothing writes %APPDATA%\Klipora while we set up the old install.
  await t.ev(`(location.href = 'about:blank', true)`); await t.wait(200);
  fs.rmSync(newDir, { recursive: true, force: true });
  fs.mkdirSync(oldDir, { recursive: true });
  fs.writeFileSync(path.join(oldDir, 'settings.json'), JSON.stringify({ python: 'python', workRoot, density: 'compact' }), 'utf8');
  const rec = { id: 'echo-old-1', tool: 'echo', action: 'analyze', title: 'Echo (tes engine)', status: 'ok', sub: '3 ditemukan', seqName: 'Lama (AutoCut)',
                started: Date.now() - 3600000, sec: 1.2, error: null, log: '', warnings: [], workdir: '' };
  fs.writeFileSync(path.join(oldDir, 'history.json'), JSON.stringify([rec]), 'utf8');
  t.check('migration: no Klipora folder before boot', !fs.existsSync(newDir));

  await t.load({ locale: 'en_US' });
  const sf = path.join(newDir, 'settings.json');
  t.check('migration: Klipora/settings.json exists', fs.existsSync(sf));
  const s = fs.existsSync(sf) ? JSON.parse(fs.readFileSync(sf, 'utf8')) : {};
  t.check('migration: workRoot kept', s.workRoot === workRoot && (await t.ev('AC.settings.workRoot()')) === workRoot, JSON.stringify(s.workRoot));
  t.check('migration: existing install keeps Indonesian (uiLang id)', s.uiLang === 'id' && (await t.ev('AC.i18n.lang()')) === 'id', JSON.stringify(s.uiLang));
  t.check('migration: density applied', s.density === 'compact' && (await t.ev(`document.documentElement.getAttribute('data-density')`)) === 'compact');
  t.check('migration: history.json copied', fs.existsSync(path.join(newDir, 'history.json')) && (await t.ev(`AC.history.list().some(function (r) { return r.id === 'echo-old-1'; })`)));
  t.check('migration: old folder still exists', fs.existsSync(path.join(oldDir, 'settings.json')) && fs.existsSync(path.join(oldDir, 'history.json')));
  t.check('migration: MIGRATED_FROM.txt', fs.existsSync(path.join(newDir, 'MIGRATED_FROM.txt')));
  t.check('migration: AC.sys.paths.appData ends with \\Klipora', /\\Klipora$/.test(await t.ev('AC.sys.paths.appData')), await t.ev('AC.sys.paths.appData'));
  t.check('migration: recorded in AC.migration', (await t.ev(`AC.migration.done.some(function (m) { return /AutoCutBOT$/.test(m.from) && /Klipora$/.test(m.to); })`)));
  t.check('migration: no leftover .migrating folder', !fs.readdirSync(roaming).some((n) => n.indexOf('.migrating-') >= 0));

  // Second boot: nothing is copied again (the Klipora folder wins, the old one is untouched).
  fs.writeFileSync(path.join(oldDir, 'settings.json'), JSON.stringify({ python: 'python', workRoot: 'C:\\Y', density: 'comfortable' }), 'utf8');
  await t.load({ locale: 'en_US' });
  t.check('migration: runs once', (await t.ev('AC.settings.workRoot()')) === workRoot && (await t.ev('AC.migration.done.length')) === 0);
}
