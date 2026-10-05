// broll.mjs: B-Roll page in headless Chrome with the fake CEP stub and the REAL engine (--engine live).
// node tools/ui_test.mjs tools/ui_tests/broll.mjs            (default fixture raw49 = the 49 s test clip)
// No Grok quota and no network: "Pilih momen pakai AI" is switched off (rules planner), no Pexels key during the run,
// AI image rows are left unchecked before apply. A small local library is generated with FFmpeg in the sandbox.
// Checks: settings UI, Pexels switch follows the write-only key, folder count, analyze -> Tinjau with thumbnails,
// Ganti sumber / Penuh-PiP / Kata lain (engine fetch), apply (engine pre-render) -> host placement (stubbed
// bac_broll_prepare / bac_broll_place) -> result card, renders on disk (2292x960, no audio), widths 280/660.
import { spawnSync } from 'child_process';
import fs from 'fs';
import path from 'path';

export default async function (t) {
  const ev = (s) => t.ev(s);
  const rt = `AC.tools.runtime('broll')`;
  const scr = `document.querySelector('[data-screen="tool-broll"]')`;
  if (t.engine !== 'live' && !(t.media && t.media.have.talk_49s)) { console.log('skip: broll needs --engine live + test media, set KLIPORA_TEST_MEDIA'); t.check('broll: skipped (no test media)', true); return; }
  if (t.engine !== 'live') { t.check('broll: needs --engine live (real analyze + renders)', false, 'engine=' + t.engine); return; }

  // ---- sample library in the sandbox (names drive the rule matcher: "reseller", "email")
  const lib = path.join(t.sandbox, 'home', 'Videos', 'B-Roll');
  fs.mkdirSync(path.join(lib, 'Bisnis'), { recursive: true });
  const ff = (args) => spawnSync('ffmpeg', ['-hide_banner', '-loglevel', 'error', '-y', ...args], { windowsHide: true });
  ff(['-f', 'lavfi', '-i', 'testsrc2=s=640x360:r=30:d=4', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', path.join(lib, 'Bisnis', 'Reseller packing paket.mp4')]);
  ff(['-f', 'lavfi', '-i', 'testsrc=s=1280x720:r=25:d=6', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', path.join(lib, 'uang tunai.mp4')]);
  ff(['-f', 'lavfi', '-i', 'color=c=0x3060d0:s=800x600:d=1', '-frames:v', '1', path.join(lib, 'email notifikasi.png')]);
  t.check('library generated', fs.readdirSync(lib).length === 3 && fs.existsSync(path.join(lib, 'Bisnis', 'Reseller packing paket.mp4')));

  await t.go('tool/broll');
  await t.waitFor(`${rt} && ${rt}.ctx.brollPaint`, 10000, 'broll page');
  t.check('main: sections Gaya/Sumber/Penempatan/Jumlah', await ev(`(function(){ var s=[].map.call(${scr}.querySelectorAll('.tool-body .sec-h span:first-child'), function(e){return e.textContent;}); return ['Gaya','Sumber','Penempatan','Jumlah'].every(function(x){return s.indexOf(x)>=0;}); })()`));
  t.check('main: dock = Cari B-roll', /Cari B-roll/.test(await t.text('#primary')));
  t.check('main: estimate strip', /1 B-roll/.test(await t.text('.broll-strip')) && /0:49/.test(await t.text('.broll-strip')), await t.text('.broll-strip'));
  const pexSel = `${scr}.querySelectorAll('.tool-body .sw input')[1]`;
  t.check('Pexels off without a key (+ link to Pengaturan)', await ev(`${pexSel}.disabled && /Pengaturan/.test(${scr}.querySelector('.broll-pex').textContent)`));
  await ev(`(AC.settings.setSecret('pexelsKey', 'TESTKEY-not-real-123456'), true)`);
  t.check('Pexels enabled once a key is saved; key not in the DOM', await ev(`!${pexSel}.disabled && ${pexSel}.checked && document.documentElement.outerHTML.indexOf('TESTKEY-not-real') < 0`));
  await ev(`(AC.settings.setSecret('pexelsKey', ''), true)`);
  t.check('Pexels disabled again after the key is removed', await ev(`${pexSel}.disabled`));
  await t.shot(t.width === 380 ? 'build_broll_main' : 'build_broll_main_' + t.width);

  // folder: typing a path turns the source on and counts the files
  await t.type('.broll-folder .input', lib);
  t.check('folder: count + switch on', /2 video, 1 gambar/.test(await t.text('.broll-folder-info')) && await ev(`${rt}.ctx.state.srcLocal`), await t.text('.broll-folder-info'));
  // presets + sliders
  await ev(`(${scr}.querySelector('.tool-body .seg button:nth-child(3)').click(), true)`);   // Sering
  t.check('preset Sering: 4 per mnt, 3 dtk', await ev(`${rt}.ctx.state.density === 4 && ${rt}.ctx.state.maxDur === 3`) && /3 B-roll/.test(await t.text('.broll-strip')), await t.text('.broll-strip'));
  await ev(`(${scr}.querySelector('.tool-body .seg button:nth-child(1)').click(), true)`);   // Jarang
  await ev(`(function(){ var i=${scr}.querySelectorAll('.tool-body input.range')[0]; i.value='3'; i.dispatchEvent(new Event('input')); return true; })()`);
  t.check('manual density -> Custom hint', /Custom/.test(await t.text(`[data-screen="tool-broll"] .tool-body .sec .help`)) || await ev(`${rt}.ctx.state.preset === null`));
  // placement help follows the choice
  const placeSec = `[].filter.call(${scr}.querySelectorAll('.tool-body .sec'), function(s){ var h=s.querySelector('.sec-h'); return h && /Penempatan/.test(h.textContent); })[0]`;
  await ev(`(${placeSec}.querySelector('.seg button:nth-child(3)').click(), true)`);  // PiP
  t.check('placement PiP + help', await ev(`${rt}.ctx.state.placement === 'pip'`) && /pojok/.test(await ev(`${placeSec}.querySelector('.help').textContent`)));
  await ev(`(${placeSec}.querySelector('.seg button:nth-child(1)').click(), true)`);  // Otomatis
  // no Grok quota in tests: switch "Pilih momen pakai AI" off through its real control
  await ev(`(function(){ var l=[].filter.call(${scr}.querySelectorAll('.sw'), function(s){ return /Pilih momen pakai AI/.test(s.textContent); })[0]; l.querySelector('input').click(); return true; })()`);
  t.check('use AI off', await ev(`${rt}.ctx.state.useAi === false`));

  // ---- analyze -> review
  await t.click('#primary');
  await t.waitFor(`location.hash === '#tool/broll/review'`, 90000, 'broll review');
  await t.wait(300);
  const rv = await ev(`(function(){ var l=${rt}.ctx.reviewList, it=l.items(); return {n: it.length, on: l.count(), local: it.filter(function(x){return x.cands[x.pick] && x.cands[x.pick].src==='local';}).length,
    ai: it.filter(function(x){return x.cands.some(function(c){return c.src==='ai';});}).length, rows: document.querySelectorAll('.broll-rv .rv').length,
    imgs: [].filter.call(document.querySelectorAll('.broll-rv img.broll-th'), function(i){return i.complete && i.naturalWidth > 0;}).length,
    title: document.querySelector('.broll-rv .rv-title').textContent, legend: document.querySelector('.broll-rv .legend').textContent, dock: document.getElementById('primary').textContent}; })()`);
  t.check('review: rows with sources', rv.n >= 2 && rv.rows >= 2 && rv.local >= 1 && rv.ai === rv.n, JSON.stringify(rv));
  t.check('review: thumbnails load (file:// jpg)', rv.imgs >= 1, JSON.stringify(rv));
  t.check('review: copy (dipasang / momen / tertutup B-roll, no "Hemat")', /B-roll dipasang/.test(rv.title) && /momen/.test(rv.title) && /tertutup B-roll/.test(rv.legend) && !/Hemat/.test(rv.legend), rv.title + ' | ' + rv.legend);
  t.check('review: dock = Pasang N B-roll', /Pasang \d+ B-roll/.test(rv.dock), rv.dock);

  // row actions on the first row
  await ev(`(document.querySelector('.broll-rv .rv').click(), true)`);
  await t.wait(150);
  const acts = await ev(`[].map.call(document.querySelectorAll('.broll-rv .rv.is-active .rv-actions button'), function(b){return b.textContent;})`);
  t.check('row actions', ['Ganti sumber', 'Penuh/PiP', 'Kata lain', 'Buat gambar AI'].every((a) => acts.some((x) => x.indexOf(a) >= 0)) && acts.some((x) => /Lewati B-roll ini/.test(x)), JSON.stringify(acts));
  const p0 = await ev(`${rt}.ctx.reviewList.items()[0].pick`);
  await ev(`(document.querySelector('.broll-rv .rv.is-active [data-act="x0"]').click(), true)`);
  const p1 = await ev(`${rt}.ctx.reviewList.items()[0]`);
  t.check('Ganti sumber cycles the candidate', p1.pick !== p0 && p1.src === p1.cands[p1.pick].src && p1.touched, p0 + ' -> ' + p1.pick);
  const pl0 = p1.place;
  await ev(`(document.querySelector('.broll-rv .rv.is-active [data-act="x1"]').click(), true)`);
  t.check('Penuh/PiP flips placement + tag', await ev(`${rt}.ctx.reviewList.items()[0].place`) !== pl0 && /PiP|Penuh/.test(await t.text('.broll-rv .rv.is-active .rv-l1')));
  await t.shot(t.width === 380 ? 'build_broll' : 'build_broll_' + t.width);

  // Kata lain -> engine fetch with a new keyword -> list reloads, row keeps focus
  await ev(`(document.querySelector('.broll-rv .rv.is-active [data-act="x2"]').click(), true)`);
  t.check('Kata lain opens the search box', await ev(`!!document.querySelector('.broll-ask input')`));
  await t.type('.broll-ask input', 'uang tunai');
  await ev(`([].filter.call(document.querySelectorAll('.broll-ask button'), function(b){return /Cari/.test(b.textContent);})[0].click(), true)`);
  await t.waitFor(`location.hash === '#tool/broll/review' && ${rt}.ctx.reviewList.items()[0].kw === 'uang tunai'`, 60000, 'fetch with query');
  const r0 = await ev(`${rt}.ctx.reviewList.items()[0]`);
  t.check('Kata lain: new local match first', r0.cands[0].src === 'local' && /uang tunai/.test(r0.cands[0].name) && r0.pick === 0, JSON.stringify(r0.cands.map((c) => c.name)));

  // ---- apply: only rows with a non-AI source (no Grok Imagine in tests) -> engine pre-render -> host (stub)
  await t.host('bac_broll_prepare', `function (name, seqId, track) { var c = JSON.parse(JSON.stringify(__acStub.seq)); c.id = 'made-broll'; c.name = name; __acStub.made[c.id] = c;
    return JSON.stringify({ok: true, id: c.id, name: name, origId: __acStub.seq.id, origName: __acStub.seq.name, track: 1, tracks: 2, cleared: 0}); }`);
  await t.host('bac_broll_place', `function (id, items, opts) { window.__brollPlaced = (window.__brollPlaced || []).concat(items);
    return JSON.stringify({ok: true, track: 1, failed: [], placed: items.map(function (x) { return {id: x.id, start: x.t0, end: x.t1}; })}); }`);
  await ev(`(function(){ var l=${rt}.ctx.reviewList; l.items().forEach(function(it, i){ var k=-1; it.cands.forEach(function(c, j){ if (k<0 && c.src!=='ai') k=j; }); if (k>=0) it.pick=k; l.toggle(i, k>=0); }); l.refresh(); return true; })()`);
  const nOn = await ev(`${rt}.ctx.reviewList.count()`);
  await t.click('#primary');
  await t.waitFor(`location.hash === '#tool/broll/result'`, 120000, 'broll result');
  const placed = await ev(`window.__brollPlaced || []`);
  const prep = await t.calls('bac_broll_prepare');
  t.check('host: clone named "(Klipora)" from the analyzed sequence, track "Klipora B-Roll"', prep.length === 1 && /\(Klipora\)$/.test(prep[0][0]) && prep[0][1] === 'seq-raw49' && prep[0][2] === 'Klipora B-Roll', JSON.stringify(prep));
  t.check('host: every checked row placed', placed.length === nOn && nOn >= 1, placed.length + ' vs ' + nOn);
  const files = placed.map((x) => x.path);
  t.check('renders exist (mp4 in the workdir)', files.every((f) => /\.mp4$/.test(f) && fs.existsSync(f)), JSON.stringify(files));
  const probe = spawnSync('ffprobe', ['-v', 'error', '-show_entries', 'stream=codec_type,width,height', '-of', 'json', files[0]], { encoding: 'utf8' });
  const streams = JSON.parse(probe.stdout || '{}').streams || [];
  t.check('render: 2292x960, video only (audio stripped)', streams.length === 1 && streams[0].width === 2292 && streams[0].height === 960, probe.stdout);
  t.check('plan items: frame times, fade, PiP geometry', placed.every((x) => x.t1 > x.t0 && x.fade === 0.25 && x.pos.length === 2 && x.pos[0] > 0 && x.pos[0] < 1 && x.scale === 38));
  const res = await ev(`({t: document.querySelector('[data-screen="tool-broll"] [data-pane="result"] h2').textContent, txt: document.querySelector('[data-screen="tool-broll"] [data-pane="result"]').textContent})`);
  t.check('result card', /B-roll terpasang/.test(res.t) && /Buka yang asli/.test(res.txt) && /Sequence asli tidak diubah/.test(res.txt) && /Layar penuh/.test(res.txt), res.t);
  t.check('Riwayat line', await ev(`AC.history.list().some(function(h){ return h.tool === 'broll' && /B-roll dipasang/.test(h.sub || ''); })`));
  await t.shot(t.width === 380 ? 'build_broll_result' : 'build_broll_result_' + t.width);

  // ---- layout at other widths (no horizontal overflow)
  for (const w of [280, 660]) {
    await t.ev(`(function(){ document.documentElement.style.width='${w}px'; return true; })()`);
    await t.go('tool/broll');
    await t.wait(120);
    const over = await ev(`(function(){ var b=${scr}; return b.scrollWidth - b.clientWidth; })()`);
    t.check(`main at ${w}px: no horizontal overflow`, over <= 1, String(over));
    await t.go('tool/broll/review');
    await t.wait(150);
    const over2 = await ev(`(function(){ var b=document.querySelector('.broll-rv'); return b ? b.scrollWidth - b.clientWidth : 0; })()`);
    t.check(`review at ${w}px: no horizontal overflow`, over2 <= 1, String(over2));
  }
  await t.ev(`(function(){ document.documentElement.style.width=''; return true; })()`);

  // ---- English UI: main pane + review (fresh analysis; the engine gets job.lang = en)
  await ev(`(AC.i18n.set('en'), true)`);
  await t.go('tool/broll');
  await t.waitFor(`${rt} && ${rt}.ctx.brollPaint`, 10000, 'broll page (en)');
  await t.wait(200);
  const leftMain = await t.idLeftovers('#app');
  t.check('en main: no Indonesian leftovers', leftMain.length === 0, JSON.stringify(leftMain));
  const secs = await ev(`[].map.call(${scr}.querySelectorAll('.tool-body .sec-h span:first-child'), function(e){return e.textContent;}).join('|')`);
  t.check('en main: exact labels (Style, Sources, Placement, Amount, Find B-roll)', ['Style', 'Sources', 'Placement', 'Amount'].every((x) => secs.split('|').indexOf(x) >= 0) &&
    (await t.text('#primary')).trim().indexOf('Find B-roll') === 0 && /Videos: 2, images: 1/.test(await t.text('.broll-folder-info')), secs + ' / ' + (await t.text('#primary')));
  await t.shot('build_broll_main_en');
  await t.click('#primary');
  await t.waitFor(`location.hash === '#tool/broll/review'`, 90000, 'broll review (en)');
  await t.wait(300);
  const leftRv = await t.idLeftovers('#app');
  t.check('en review: no Indonesian leftovers', leftRv.length === 0, JSON.stringify(leftRv));
  const rvEn = await ev(`({title: document.querySelector('.broll-rv .rv-title').textContent, dock: document.getElementById('primary').textContent,
    legend: document.querySelector('.broll-rv .legend').textContent})`);
  t.check('en review: exact labels (B-rolls placed / of N moments / covered by B-roll / Place N B-rolls)', /^\d+ B-rolls? placedof \d+ moments$/.test(rvEn.title) &&
    /covered by B-roll/.test(rvEn.legend) && /^Place \d+ B-rolls?/.test(rvEn.dock), JSON.stringify(rvEn));
  await ev(`(document.querySelector('.broll-rv .rv').click(), true)`);
  await t.wait(150);
  const actsEn = await ev(`[].map.call(document.querySelectorAll('.broll-rv .rv.is-active .rv-actions button'), function(b){return b.textContent;})`);
  t.check('en review: row actions', ['Change source', 'Full/PiP', 'Other keyword', 'Create AI image'].every((a) => actsEn.some((x) => x.indexOf(a) >= 0)), JSON.stringify(actsEn));
  await t.shot('build_broll_review_en');
  await ev(`(AC.i18n.set('id'), true)`);
  await t.go('tool/broll');
  await t.wait(200);
  t.check('back to Indonesian', /Cari B-roll/.test(await t.text('#primary')));
}
