// foundation.mjs: end-to-end check of the panel foundation (run: node tools/ui_test.mjs [--engine live|mock]).
// Navigation, palette, keys, settings persistence (+ write-only Pexels key), echo tool through the real or mocked
// engine (progress -> virtual review list -> apply -> result), cancel, error, background job, history,
// apply.removeRanges (extract + >150 XML fallback), sequence events + polling fallback, theme, screenshots.
export default async function (t) {
  const shot = (n) => t.shot('foundation_' + n);
  const hash = () => t.hash();

  // ---------------------------------------------------------------- Home
  t.check('home: 4 groups + 13 tools', (await t.count('.screen[data-screen="home"] .tool')) === 13);
  t.check('home: 3 recipes', (await t.count('.recipe')) === 3);
  t.check('home: keys registered with CEP', (await t.ev('__acStub.keys.length')) > 20);
  await t.waitFor(`AC.engine.lastHealth && !AC.engine.lastHealth.busy`, 20000, 'engine health');
  t.check('engine health ran (' + t.engine + ')', await t.ev('AC.engine.lastHealth.ok'), JSON.stringify(await t.ev('AC.engine.lastHealth.error && AC.engine.lastHealth.error.msg')));
  await shot('home');

  // ---------------------------------------------------------------- tool page + sibling tabs + back/Esc
  await t.click('.screen[data-screen="home"] .tool[data-go="tool/silence"]');
  await t.wait(80);
  t.check('nav: tool route', (await hash()) === '#tool/silence');
  t.check('nav: crumb title', (await t.text('#crumbTitle')) === 'Potong Silence');
  // Silence is built now (no "Sedang dibangun" placeholder); its group has 5 tools
  t.check('nav: 5 sibling tabs, Silence selected', (await t.count('#subnav button')) === 5 && (await t.text('#subnav [aria-selected="true"]')) === 'Silence');
  t.check('nav: real tool screen, no placeholder', (await t.count('.screen[data-screen="tool-silence"] .empty h2')) === 0);
  await t.waitFor(`document.querySelector('#primary') && !document.querySelector('#primary').disabled`, 30000, 'silence primary');
  t.check('nav: dock primary enabled once the tool is ready', /\S/.test(await t.text('#primary')));
  t.check('nav: scope selector', (await t.count('.screen[data-screen="tool-silence"] .src .seg button')) === 3);
  await shot('tool_placeholder');
  await t.click('#subnav [data-go="tool/fillers"]'); await t.wait(60);
  t.check('nav: sibling tab switches tool', (await hash()) === '#tool/fillers');
  await t.click('#btnBack'); await t.wait(60);
  t.check('nav: back -> home', (await hash()) === '#home');
  await t.go('tool/captions');
  await t.key('Escape', {}, 'body'); await t.wait(60);
  t.check('nav: Esc -> home', (await hash()) === '#home');
  await t.go('silence');
  t.check('nav: bare tool id alias', (await hash()) === '#silence' && (await t.ev(`AC.router.current().id`)) === 'silence');

  // ---------------------------------------------------------------- palette + shortcut sheet
  await t.go('home');
  await t.key('k', { ctrl: true }, 'body'); await t.wait(60);
  t.check('palette: Ctrl+K opens', await t.ev(`AC.palette.isOpen()`));
  await t.type('#palInput', 'viral'); await t.wait(30);
  t.check('palette: filter', (await t.count('.pal-i')) >= 1 && (await t.text('.pal-i[aria-selected="true"] span')) === 'Klip Viral');
  await shot('palette');
  await t.key('Enter', {}, '#palInput'); await t.wait(80);
  t.check('palette: Enter opens tool', (await hash()) === '#tool/viral' && !(await t.ev('AC.palette.isOpen()')));
  await t.key('k', { ctrl: true }, 'body'); await t.key('Escape', {}, '#palInput'); await t.wait(40);
  t.check('palette: Esc closes', !(await t.ev('AC.palette.isOpen()')) && (await hash()) === '#tool/viral');
  await t.go('home');
  await t.key('?', {}, 'body'); await t.wait(40);
  t.check('keys: ? opens sheet', await t.ev(`AC.palette.keysOpen()`) && (await t.count('#keysBody dt')) > 8);
  await t.key('Escape', {}, 'body'); await t.wait(40);
  t.check('keys: Esc closes sheet', !(await t.ev(`AC.palette.keysOpen()`)));

  // ---------------------------------------------------------------- settings
  await t.click('#btnSettings'); await t.wait(80);
  t.check('settings: route', (await hash()) === '#settings');
  await t.click('#setDensity [data-v="compact"]'); await t.wait(40);
  t.check('settings: density applied', (await t.ev(`document.documentElement.getAttribute('data-density')`)) === 'compact');
  const setPath = await t.ev('AC.settings.path()');
  const saved1 = JSON.parse(await t.ev(`require('fs').readFileSync(${JSON.stringify(setPath)}, 'utf8')`));
  t.check('settings: persisted to %APPDATA%\\Klipora\\settings.json', saved1.density === 'compact' && setPath.toLowerCase().indexOf(t.sandbox.toLowerCase()) === 0 && /\\Klipora\\settings\.json$/.test(setPath), setPath);
  t.check('settings: engine keys present', saved1.python === 'python' && saved1.whisperModel === 'large-v3-turbo' && saved1.lang === 'id' && !!saved1.workRoot);
  const KEY = 'pexelsTESTkey1234567890abcdef';
  await t.type('.screen[data-screen="settings"] input[type="password"]', KEY);
  await t.ev(`Array.from(document.querySelectorAll('.screen[data-screen="settings"] button')).filter(function(b){return b.textContent.trim()==='Simpan'})[0].click()`);
  await t.wait(40);
  const saved2 = JSON.parse(await t.ev(`require('fs').readFileSync(${JSON.stringify(setPath)}, 'utf8')`));
  t.check('settings: pexels key stored', saved2.pexelsKey === KEY);
  t.check('settings: pexels key never in DOM / get()', !(await t.ev(`document.documentElement.outerHTML.indexOf(${JSON.stringify(KEY)}) >= 0`)) && (await t.ev(`AC.settings.get('pexelsKey') === undefined && AC.settings.hasSecret('pexelsKey')`)));
  t.check('settings: no AI key anywhere', !(await t.ev(`/AI_API_KEY|sk-[A-Za-z0-9]{10}/.test(document.documentElement.outerHTML)`)));
  await t.waitFor(`document.querySelector('#aiStatus') && !/Mengecek|Belum dicek/.test(document.querySelector('#aiStatus').textContent)`, 20000, 'AI status pill');
  t.check('settings: AI status pill filled', !!(await t.text('#aiStatus')), await t.text('#aiStatus'));
  await shot('settings');
  await t.click('#setDensity [data-v="comfortable"]');
  // developer mode reveals the echo tool
  await t.ev(`AC.settings.set('developer', true)`); await t.wait(40);
  t.check('settings: developer shows Echo', (await t.ev(`AC.tools.list().some(function(d){return d.id==='echo'})`)));

  // ---------------------------------------------------------------- echo: analyze -> review (virtual) -> apply (dry run) -> result
  await t.setSeq('long35'); await t.wait(400);
  await t.go('tool/echo'); await t.wait(80);
  t.check('echo: page', (await t.text('#crumbTitle')) === 'Echo (tes engine)');
  await t.ev(`(function(){var r=AC.tools.runtime('echo').ctx; r.state.every=2; r.state.seconds=0.6; r.state.action='analyze'; r.state.apply=false; r.save(); return true;})()`);
  await t.key('Enter', { ctrl: true }, 'body');
  await t.waitFor(`location.hash==='#tool/echo/progress'`, 3000, 'progress pane');
  await t.waitFor(`document.querySelectorAll('.screen[data-screen="tool-echo"] .stage-i').length >= 3`, 8000, 'stages listed');
  await shot('progress');
  t.check('echo: progress shows stages + Batalkan', (await t.count('.screen[data-screen="tool-echo"] .stage-i')) >= 3 && (await t.ev(`/Batalkan/.test(document.querySelector('#dock').textContent)`)));
  await t.waitFor(`location.hash==='#tool/echo/review'`, 30000, 'review pane');
  await t.wait(200);
  const total = await t.ev(`AC.tools.runtime('echo').review.items().length`);
  const rendered = await t.count('.screen[data-screen="tool-echo"] .rv');
  t.check('review: 1000+ items virtualized', total >= 1000 && rendered > 5 && rendered < 80, `${rendered} rendered of ${total}`);
  const on0 = await t.ev(`AC.tools.runtime('echo').review.count()`);
  t.check('review: dock label counts', (await t.text('#primary .lbl')) === `Hitung ${on0} temuan`, await t.text('#primary .lbl'));
  await t.ev(`AC.tools.runtime('echo').review.focus()`);
  const seeks0 = (await t.calls('bac_seek')).length;
  await t.key('ArrowDown', {}, '.rv-scroll'); await t.wait(250);
  const seeks = await t.calls('bac_seek');
  t.check('review: arrow moves + seeks Premiere playhead', seeks.length > seeks0 && Math.abs(seeks[seeks.length - 1][0] - 1) < 0.01, JSON.stringify(seeks.slice(-1)));
  await t.key(' ', {}, '.rv-scroll'); await t.wait(150);
  const on1 = await t.ev(`AC.tools.runtime('echo').review.count()`);
  t.check('review: Space toggles', Math.abs(on1 - on0) === 1, `${on0} -> ${on1}`);
  await t.ev(`document.querySelector('.rv-scroll').scrollTop = 30000`); await t.wait(200);
  const firstIdx = await t.ev(`+document.querySelector('.rv-scroll .rv').getAttribute('data-i')`);
  t.check('review: scrolling renders far rows', firstIdx > 300, String(firstIdx));
  await t.click('.rv-tools [data-f="off"]'); await t.wait(80);
  const offCount = await t.ev(`AC.tools.runtime('echo').review.items().filter(function(i){return !i.on}).length`);
  t.check('review: filter Disimpan', (await t.text('.rv-tools [data-f="off"] .n')) === String(offCount));
  await t.click('.rv-tools [data-f="all"]'); await t.wait(50);
  await t.ev(`(function(){var c=document.querySelector('.rv-bulk .ck'); c.checked=true; c.dispatchEvent(new Event('change',{bubbles:true})); return true;})()`); await t.wait(80);
  t.check('review: select all', (await t.ev(`AC.tools.runtime('echo').review.count()`)) === total);
  await t.key('a', {}, '.rv-scroll'); await t.wait(80);
  t.check('review: A toggles all off', (await t.ev(`AC.tools.runtime('echo').review.count()`)) === 0 && (await t.ev(`document.querySelector('#primary').disabled`)));
  await t.key('a', {}, '.rv-scroll'); await t.wait(80);
  await t.ev(`AC.tools.runtime('echo').review.toggle(0, false)`); await t.wait(150);
  await shot('review');
  // apply: engine "apply" reads the edited review file -> dry-run result card
  await t.click('#primary');
  await t.waitFor(`location.hash==='#tool/echo/result'`, 30000, 'result pane');
  const file = await t.ev(`AC.tools.runtime('echo').reviewOpts.file`);
  const doc = JSON.parse(await t.ev(`require('fs').readFileSync(${JSON.stringify(file)}, 'utf8')`));
  t.check('apply: edited review written back', doc.items[0].on === false && doc.items[1].on === true);
  t.check('apply: changed rows marked touched (engine carry_over)', doc.items[0].touched === true && doc.items.every(function (i) { return i.touched === true; }));
  const rw = await t.ev(`(function(){var r=AC.history.list().filter(function(x){return x.tool==='echo'&&x.action==='analyze'&&x.status==='ok'})[0];return r?r.sub:'';})()`);
  t.check('history: engine summary dict -> Riwayat line', /^\d[\d.]* ditemukan, \d[\d.]* dipilih/.test(rw), rw);
  t.check('apply: result card', (await t.text('.screen[data-screen="tool-echo"] .result-h h2')) === 'Rencana siap (uji coba)');
  await shot('result');
  await t.ev(`Array.from(document.querySelectorAll('.screen[data-screen="tool-echo"] .btn')).filter(function(b){return /Ubah pilihan/.test(b.textContent)})[0].click()`); await t.wait(100);
  t.check('result: Ubah pilihan -> review', (await hash()) === '#tool/echo/review');

  // ---------------------------------------------------------------- apply to timeline (clone + extract via host)
  await t.setSeq('raw49'); await t.wait(300);
  await t.go('tool/echo');
  await t.ev(`(function(){var r=AC.tools.runtime('echo').ctx; r.state.every=5; r.state.seconds=0.3; r.state.apply=true; r.save(); return true;})()`);
  await t.click('#primary');
  await t.waitFor(`location.hash==='#tool/echo/review'`, 30000, 'review 2');
  await t.click('#primary');
  await t.waitFor(`location.hash==='#tool/echo/result'`, 30000, 'result 2');
  t.check('apply: host clone + extract called', (await t.calls('bac_cloneSeq')).length === 1 && (await t.calls('bac_removeRanges')).length >= 1);
  t.check('apply: result "Sequence baru siap"', (await t.text('.screen[data-screen="tool-echo"] .result-h h2')) === 'Sequence baru siap');
  await t.ev(`Array.from(document.querySelectorAll('.screen[data-screen="tool-echo"] .btn')).filter(function(b){return /Buka yang asli/.test(b.textContent)})[0].click()`); await t.wait(100);
  t.check('apply: Buka yang asli -> bac_openSequence(original)', JSON.stringify(await t.calls('bac_openSequence')) === JSON.stringify([['seq-raw49']]));

  // ---------------------------------------------------------------- AC.apply: >150 ranges -> XML route (falls back when engine lacks xmeml/cut)
  const big = await t.ev(`(function(){var r=[];for(var i=0;i<200;i++)r.push([i*0.2+0.05,i*0.2+0.1]);
    return AC.apply.removeRanges(r,{record:false}).promise.then(function(x){return {mode:x.mode,count:x.count};});})()`);
  const xmlTried = (await t.calls('bac_exportXml')).length === 1;
  t.check('apply: >150 ranges uses XML route' + (t.engine === 'live' ? ' (engine xmeml/cut)' : ' (mock: falls back to extract)'),
    xmlTried && big.count === 200 && (t.engine !== 'live' || big.mode === 'xml'), JSON.stringify(big));

  // ---------------------------------------------------------------- cancel (Esc) kills the engine process
  await t.go('tool/echo');
  await t.ev(`(function(){var r=AC.tools.runtime('echo').ctx; r.state.action='slow'; r.state.seconds=2; r.save(); return true;})()`);
  await t.go('tool/echo/x'); await t.go('tool/echo');
  await t.ev(`(AC.tools.runtime('echo').ctx.run({action:'slow', params:{seconds:20}, title:'Proses lama'}), true)`);
  await t.waitFor(`location.hash==='#tool/echo/progress'`, 3000, 'slow progress');
  await t.waitFor(`AC.tools.runtime('echo').task.pct > 2`, 15000, 'slow progress moves');
  const pid = await t.ev(`AC.tools.runtime('echo').task.pid || 0`);
  await t.key('Escape', {}, 'body');
  await t.waitFor(`AC.tools.runtime('echo').task.state==='cancelled'`, 6000, 'cancelled');
  await t.wait(100);
  t.check('cancel: Esc cancels, back to main', (await hash()) === '#tool/echo' && /Dibatalkan/.test(await t.text('#toasts')));
  if (t.engine === 'live') {
    await t.wait(800);
    const { execSync } = await import('child_process');
    const alive = execSync(`tasklist /FI "PID eq ${pid}" /NH`).toString().indexOf(String(pid)) >= 0;
    t.check('cancel: python process tree killed (taskkill /T /F)', pid > 0 && !alive, 'pid ' + pid);
  }

  // ---------------------------------------------------------------- error pane
  await t.ev(`(AC.tools.runtime('echo').ctx.run({action:'fail', params:{msg:'Gagal sengaja dari tes.'}}), true)`);
  await t.waitFor(`location.hash==='#tool/echo/error'`, 15000, 'error pane');
  t.check('error: alert with engine message + Salin detail', /Gagal sengaja dari tes/.test(await t.text('.screen[data-screen="tool-echo"] [data-pane="error"] .alert')) && /Salin detail/.test(await t.text('.screen[data-screen="tool-echo"] [data-pane="error"]')));
  t.check('error: dock Coba lagi', (await t.text('#primary .lbl')) === 'Coba lagi');
  await shot('error');

  // ---------------------------------------------------------------- background job + ring + toast
  await t.go('tool/echo');
  await t.ev(`(AC.tools.runtime('echo').ctx.run({action:'slow', params:{seconds:2.5}, title:'Proses lama', onResult:function(d){AC.tools.runtime('echo').ctx.result({title:'Selesai latar'});}}), true)`);
  await t.waitFor(`location.hash==='#tool/echo/progress'`, 3000, 'bg progress');
  await t.ev(`Array.from(document.querySelectorAll('#dock .btn')).filter(function(b){return /Jalankan di latar/.test(b.textContent)})[0].click()`);
  await t.wait(400);
  t.check('background: home + job ring', (await hash()) === '#home' && !(await t.ev(`document.querySelector('#jobRing').hidden`)));
  await shot('background');
  await t.waitFor(`/selesai/.test(document.querySelector('#toasts').textContent)`, 20000, 'finish toast');
  await t.ev(`document.querySelector('#toasts .linkbtn').click()`); await t.wait(100);
  t.check('background: toast Lihat -> result', (await hash()) === '#tool/echo/result' && (await t.text('.screen[data-screen="tool-echo"] .result-h h2')) === 'Selesai latar');

  // ---------------------------------------------------------------- history
  await t.click('#btnJobs'); await t.wait(150);
  t.check('history: route', (await hash()) === '#history');
  const nHist = await t.count('.screen[data-screen="history"] details.hist');
  t.check('history: rows (ok, cancelled, error)', nHist >= 5 && /gagal/.test(await t.text('.screen[data-screen="history"]')) && /dibatalkan/.test(await t.text('.screen[data-screen="history"]')), String(nHist));
  t.check('history: persisted', JSON.parse(await t.ev(`require('fs').readFileSync(AC.sys.join(AC.sys.paths.appData,'history.json'),'utf8')`)).length >= 5);
  await shot('history');

  // ---------------------------------------------------------------- sequence events
  await t.go('home');
  await t.setSeq(null); await t.wait(400);
  t.check('seq: no sequence -> empty state', !(await t.ev(`document.querySelector('.home-empty').hidden`)));
  await shot('empty');
  await t.setSeq('cut15'); await t.wait(400);
  t.check('seq: event refresh -> new sequence', (await t.text('.screen[data-screen="home"] .src-name')) === 'Tutorial - Episode 12 (Klipora)' && /15 clip/.test(await t.text('.screen[data-screen="home"] .src .meta')));
  t.check('seq: events mode', (await t.ev('AC.seq.mode')) === 'events');
  const full = await t.ev(`AC.seq.current().then(function(s){return {n:s.video[0].clips.length, level:s.level}})`);
  t.check('seq: current() returns full Timeline JSON', full.n === 15 && full.level === 'full');

  // ---------------------------------------------------------------- theme follows Premiere
  await t.ev(`__acStub.setSkin({red:212,green:212,blue:212})`); await t.wait(80);
  t.check('theme: light skin', (await t.ev(`document.documentElement.getAttribute('data-theme')`)) === 'light');
  await shot('home_light');
  await t.ev(`__acStub.setSkin({red:35,green:35,blue:35})`); await t.wait(80);
  t.check('theme: dark skin derives surfaces', (await t.ev(`document.documentElement.getAttribute('data-theme')`)) === null && (await t.ev(`document.documentElement.style.getPropertyValue('--bg')`)) === '#232323');

  // ---------------------------------------------------------------- polling fallback when events are unavailable
  await t.load({ events: false });
  t.check('seq: polling fallback', (await t.ev('AC.seq.mode')) === 'poll');
  await t.ev(`(__acStub.seq = __acStub.fixtures.cut15(), true)`);
  await t.waitFor(`document.querySelector('.screen[data-screen="home"] .src-name').textContent.indexOf('(AutoCut)') > 0`, 5000, 'poll picks up new sequence');
  t.check('seq: poll refresh', true);
}
