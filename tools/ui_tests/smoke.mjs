// smoke.mjs: boots the panel and checks that Home renders (fast sanity check for any panel change).
export default async function (t) {
  t.check('AC loaded', await t.ev('!!window.AC && !!AC.tools'));
  t.check('home screen visible', await t.ev(`!!document.querySelector('.screen[data-screen="home"].is-on')`));
  t.check('13 tools on Home', (await t.count('#view .tool')) === 13, String(await t.count('#view .tool')));
  t.check('source card shows sequence', (await t.text('.screen[data-screen="home"] .src-name')) === 'Tutorial - Episode 12');
  await t.shot('smoke_home');
}
