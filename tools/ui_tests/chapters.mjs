// chapters.mjs: Auto Chapters in headless Chrome with the fake CEP (run: node tools/ui_test.mjs tools/ui_tests/chapters.mjs).
// Default: real engine (--engine live), AI switched OFF in settings (rule fallback, no Grok quota), seq raw49.
// AC_CH_AI=1 + --seq long35: AI stays on (real proxy; answers come from %LOCALAPPDATA%\AutoCutBOT\ai_cache when the
// same video was analysed before) and only the analyze -> editor screenshot is taken.
// Host functions of host/35_chapters.jsx are emulated on __acStub.markers (same contract as the jsx: markers tagged
// [Klipora-CH], old [AC-CH] markers recognised too). Ends with an English pass (AC.i18n.set('en')): setup, editor and
// result without Indonesian leftovers (chapter titles / transcript snippets are content, marked data-i18n-skip).
const HOST = {
  bac_chapters_apply: `function (list, id) {
    var s = __acStub.seq, before = __acStub.markers.length;
    __acStub.markers = __acStub.markers.filter(function (m) { return !/(Klipora|AC)-CH/.test(String(m.comment || '')); });
    var removed = before - __acStub.markers.length;
    list.forEach(function (m) { __acStub.markers.push({ t: m.t, end: m.end, name: m.name, comment: (m.comment ? m.comment + '\\n' : '') + '[Klipora-CH]', type: m.type, color: m.color }); });
    s.markers = __acStub.markers.map(function (m) { return { name: m.name, comments: m.comment, start: m.t, end: m.end, type: m.type, color: m.color }; });
    __acStub.lastApply = { list: list, id: id };
    return JSON.stringify({ ok: true, id: s.id, name: s.name, removed: removed, added: list.length, n: list.length, chapters: list.length });
  }`,
  bac_chapters_read: `function (id) {
    var s = __acStub.seq;
    var ch = __acStub.markers.filter(function (m) { return /(Klipora|AC)-CH/.test(String(m.comment || '')); })
      .map(function (m) { return { t: m.t, end: m.end, name: m.name, comment: String(m.comment).replace('[Klipora-CH]', '').replace('[AC-CH]', '').trim(), type: m.type, color: m.color }; });
    return JSON.stringify({ ok: true, id: s.id, name: s.name, duration: s.duration, chapters: ch });
  }`,
  bac_chapters_clear: `function (id) {
    var s = __acStub.seq, before = __acStub.markers.length;
    __acStub.markers = __acStub.markers.filter(function (m) { return !/(Klipora|AC)-CH/.test(String(m.comment || '')); });
    s.markers = [];
    return JSON.stringify({ ok: true, id: s.id, name: s.name, removed: before - __acStub.markers.length });
  }`
};

export default async function (t) {
  const AI = process.env.AC_CH_AI === '1';
  if (t.engine === 'mock') {   // the in-page mock engine only knows "echo": check the page renders, nothing more
    await t.go('tool/chapters');
    t.check('chapters (mock engine): setup renders', (await t.count('.chapters-setup .seg button')) >= 3 && (await t.text('#primary .lbl')) === 'Buat bab');
    return;
  }
  const row = (k, sel) => `.chapters-row:nth-child(${k}) ${sel}`;
  const yt = () => t.text('.chapters-edit .chapters-yt');
  for (const [fn, src] of Object.entries(HOST)) await t.host(fn, src);
  await t.ev(`AC.settings.set('ai', ${AI ? 'true' : 'false'})`);
  await t.ev(`AC.store.set('tool.chapters', { count: 'normal' })`);   // partial old state: defaults must fill in
  await t.waitFor(`AC.engine.lastHealth && !AC.engine.lastHealth.busy`, 20000, 'engine health');

  // ---------------------------------------------------------------- setup view
  await t.go('tool/chapters');
  t.check('chapters: setup renders (hero, 3 presets, instruction)', (await t.count('.chapters-setup .seg button')) >= 3 && (await t.count('.chapters-hint')) === 1);
  t.check('chapters: Normal preset selected', (await t.text('.chapters-setup .seg [aria-checked="true"]')) === 'Normal');
  t.check('chapters: dock primary "Buat bab"', (await t.text('#primary .lbl')) === 'Buat bab');
  t.check('chapters: estimate line', /bab/.test(await t.text('.chapters-est')));
  t.check('chapters: sibling tab Bab', (await t.text('#subnav [aria-selected="true"]')) === 'Bab');
  if (!AI) await t.shot('build_chapters_setup');

  // ---------------------------------------------------------------- analyze -> editor
  await t.click('#primary');
  await t.waitFor(`location.hash === '#tool/chapters/edit'`, AI ? 120000 : 60000, 'editor after analyze');
  await t.wait(150);
  const n0 = await t.count('.chapters-row');
  t.check('editor: chapters listed', n0 >= 3, String(n0));
  t.check('editor: YouTube text starts at 00:00', /^00:00 /.test(await yt()), await yt());
  t.check('editor: source card hidden in editor', await t.ev(`document.querySelector('.screen[data-screen="tool-chapters"] .src').hidden`));
  t.check('editor: dock "Tambah N bab"', (await t.text('#primary .lbl')) === `Tambah ${n0} bab`);
  t.check('editor: AI off -> no "Tulis ulang judul"', AI || await t.ev(`Array.from(document.querySelectorAll('.chapters-tools .btn')).filter(function(b){return /Tulis ulang/.test(b.textContent)})[0].hidden`));
  if (AI) {
    t.check('editor (AI): source AI', /AI/.test(await t.text('.chapters-title small')), await t.text('.chapters-title small'));
    await t.ev(`document.querySelector('#view').scrollTop = 0`);
    await t.shot('build_chapters');
    // "Judul, deskripsi & hashtag": one real AI call (cached afterwards)
    await t.click('.chapters-meta .btn');
    await t.waitFor(`document.querySelector('.chapters-desc') || document.querySelector('.chapters-meta .alert')`, 120000, 'meta result');
    t.check('meta (AI): titles + description + hashtags', (await t.count('.chapters-opt')) >= 3 && /#/.test(await t.text('.chapters-meta')) && /00:00 /.test(await t.ev(`document.querySelector('.chapters-desc').value`)), await t.text('.chapters-meta'));
    await t.ev(`document.querySelector('.chapters-meta').scrollIntoView()`);
    await t.wait(100);
    await t.shot('build_chapters_meta');
    return;
  }
  t.check('editor: rule titles flagged for checking', (await t.count('.chapters-note')) >= 1 && (await t.count('.chapters-notice .alert-info')) >= 1);
  await t.shot('build_chapters_rule');

  // title edit -> YouTube text, saved review
  await t.type(row(2, '.chapters-name'), 'Masuk ke Akun Reseller');
  t.check('edit: title flows into YouTube text', (await yt()).split('\n')[1].endsWith('Masuk ke Akun Reseller'), await yt());
  // time edit (relative mm:ss) -> sorted, exact
  await t.ev(`(function(){var i=document.querySelector(${JSON.stringify(row(3, '.chapters-time'))}); i.value='0:40'; i.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',bubbles:true}));})()`);
  await t.wait(80);
  t.check('edit: time change applied', (await yt()).split('\n')[2].startsWith('00:40 '), await yt());
  // bad time -> refused, unchanged
  await t.ev(`(function(){var i=document.querySelector(${JSON.stringify(row(3, '.chapters-time'))}); i.value='abc'; i.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',bubbles:true}));})()`);
  await t.wait(60);
  t.check('edit: invalid time refused', (await t.ev(`document.querySelector(${JSON.stringify(row(3, '.chapters-time'))}).value`)) === '00:40');
  // merge row 3 into row 2 -> 2 chapters -> YouTube "minimal 3 bab" warning; undo restores
  await t.click(row(3, '.chapters-acts .ibtn:nth-child(3)'));
  await t.wait(60);
  t.check('merge: one chapter less', (await t.count('.chapters-row')) === n0 - 1);
  if (n0 === 3) t.check('merge: YouTube needs 3 chapters warning', /minimal 3 bab/.test(await t.text('.chapters-notice')), await t.text('.chapters-notice'));
  await t.shot('build_chapters_warn');
  await t.click('.chapters-head .ibtn');
  await t.wait(60);
  t.check('undo: merge undone', (await t.count('.chapters-row')) === n0 && (await yt()).split('\n')[2].startsWith('00:40 '));
  // regression (live Premiere): merging a longer part with a flagged keyword title replaced the real title
  await t.ev(`(function(){var E=AC.chapters.state(); E.undo.push(JSON.stringify(E.list)); E.list[0].title='Judul Asli'; E.list[0].generic=false;
    E.list[1].t=5; E.list[1].title='kata kunci'; E.list[1].generic=true; AC.tools.runtime('chapters').ctx._chapters.showEdit(); return true;})()`);
  await t.click(row(2, '.chapters-acts .ibtn:nth-child(3)'));
  await t.wait(60);
  t.check('merge: real title beats a flagged keyword title of a longer part', (await yt()).split('\n')[0] === '00:00 Judul Asli', await yt());
  await t.click('.chapters-head .ibtn');
  await t.click('.chapters-head .ibtn');
  await t.wait(60);
  t.check('undo: back to the edited list', (await t.count('.chapters-row')) === n0 && (await yt()).split('\n')[1].endsWith('Masuk ke Akun Reseller') && (await yt()).split('\n')[2].startsWith('00:40 '), await yt());
  // add at playhead (stub player) -> empty title warning -> delete
  await t.ev(`__acStub.seq.player = 30.5`);
  await t.click('.chapters-tools .btn');
  await t.wait(120);
  t.check('add: chapter at playhead', (await t.count('.chapters-row')) === n0 + 1 && /00:30 *$/m.test(await yt()), await yt());
  t.check('add: empty title flagged', /belum punya judul|Judul kosong/.test(await t.text('.chapters-edit')));
  t.check('add: title input focused', await t.ev(`document.activeElement && document.activeElement.classList.contains('chapters-name')`));
  const idx = await t.ev(`Array.from(document.querySelectorAll('.chapters-row .chapters-time')).findIndex(function(i){return i.value==='00:30'}) + 1`);
  // type like a user (input events only, no "change" yet), keep the focus, then trash without a blur first
  await t.ev(`(function(){var e=document.querySelector(${JSON.stringify(row(idx, '.chapters-name'))}); e.focus(); e.value='Bab Baru'; e.dispatchEvent(new Event('input',{bubbles:true})); return document.activeElement===e;})()`);
  await t.click(row(idx, '.chapters-acts .ibtn:nth-child(4)'));
  await t.wait(60);
  t.check('delete: back to the list', (await t.count('.chapters-row')) === n0);
  // regression (live Premiere): the focused row's "change" pushed the post-delete list, so undo did nothing
  await t.click('.chapters-head .ibtn');
  await t.wait(60);
  t.check('undo: deleted row comes back', (await t.count('.chapters-row')) === n0 + 1 && /00:30 Bab Baru$/m.test(await yt()), await yt());
  await t.click(row(idx, '.chapters-acts .ibtn:nth-child(4)'));
  await t.wait(60);
  t.check('delete again', (await t.count('.chapters-row')) === n0);
  // seek on row click
  await t.click(row(2, '.chapters-acts .ibtn:nth-child(1)'));
  await t.wait(60);
  const seeks = await t.calls('bac_seek');
  t.check('seek: bac_seek with the analysed sequence id', seeks.length > 0 && seeks[seeks.length - 1][1] === 'seq-raw49', JSON.stringify(seeks.slice(-1)));
  // saved review file reflects the edits
  const file = await t.ev(`AC.chapters.state().file`);
  await t.wait(500);
  const doc = JSON.parse(await t.ev(`require('fs').readFileSync(${JSON.stringify(file)}, 'utf8')`));
  t.check('save: review file updated', doc.items.length === n0 && doc.items[1].title === 'Masuk ke Akun Reseller' && doc.items[1].touched === true, JSON.stringify(doc.items[1]));

  // ---------------------------------------------------------------- meta (AI off -> honest fallback)
  await t.click('.chapters-meta .btn');
  await t.waitFor(`document.querySelector('.chapters-desc')`, 60000, 'meta result');
  t.check('meta: no-AI warning + description with chapters', /AI dimatikan|AI tidak terhubung/.test(await t.text('.chapters-meta')) && /^00:00 /.test(await t.ev(`document.querySelector('.chapters-desc').value`)));

  // ---------------------------------------------------------------- apply -> markers + txt + result
  await t.click('#primary');
  await t.waitFor(`location.hash === '#tool/chapters/result'`, 60000, 'result after apply');
  const mk = await t.ev(`__acStub.markers`);
  t.check('apply: markers replaced on the sequence', mk.length === n0 && mk.every((m) => m.type === 'Chapter' && m.color === 7 && /\[Klipora-CH\]/.test(m.comment)), JSON.stringify(mk[0]));
  t.check('apply: first marker at 0, ends chain', mk[0].t === 0 && mk.slice(0, -1).every((m, k) => m.end === mk[k + 1].t));
  t.check('apply: edited title on marker', mk[1].name === 'Masuk ke Akun Reseller');
  t.check('apply: host called with analysed seq id', (await t.ev(`__acStub.lastApply.id`)) === 'seq-raw49');
  const txtPath = file.replace(/chapters_review\.json$/, 'chapters_youtube.txt');
  t.check('apply: chapters_youtube.txt written', /^00:00 /.test(await t.ev(`require('fs').readFileSync(${JSON.stringify(txtPath)}, 'utf8')`)));
  t.check('apply: result card', /bab ditambahkan/.test(await t.text('.screen[data-screen="tool-chapters"] [data-pane="result"] h2')));
  await t.shot('build_chapters_result');
  // re-run replaces (never duplicates)
  await t.go('tool/chapters/edit');
  await t.click('#primary');
  await t.waitFor(`location.hash === '#tool/chapters/result'`, 60000, 'second apply');
  t.check('apply: re-run replaces markers', (await t.ev(`__acStub.markers.length`)) === n0 && /diganti/.test(await t.text('[data-pane="result"] .result-h p')));
  // existing markers on the sequence -> "Edit bab yang ada" loads them back (no engine, no AI)
  await t.go('tool/chapters');
  await t.waitFor(`document.querySelector('.chapters-has')`, 5000, 'existing markers hint');
  t.check('setup: existing chapter markers detected', new RegExp('punya ' + n0 + ' marker bab').test(await t.text('.chapters-has')), await t.text('.chapters-has'));
  await t.ev(`document.querySelector('.chapters-has .btn').click()`);
  await t.waitFor(`location.hash === '#tool/chapters/edit'`, 5000, 'editor from markers');
  t.check('markers -> editor: same chapters, source "Dari timeline"', (await t.count('.chapters-row')) === n0 && /Dari timeline/.test(await t.text('.chapters-title small')) && (await yt()).split('\n')[1].endsWith('Masuk ke Akun Reseller'), await yt());
  await t.click('#primary');
  await t.waitFor(`location.hash === '#tool/chapters/result'`, 60000, 'third apply');
  // "Hapus hasil" (two-step) removes the chapter markers
  await t.ev(`Array.from(document.querySelectorAll('[data-pane="result"] .btn')).filter(function(b){return /Hapus/.test(b.textContent)})[0].click()`);
  await t.ev(`Array.from(document.querySelectorAll('[data-pane="result"] .btn')).filter(function(b){return /Yakin/.test(b.textContent)})[0].click()`);
  await t.wait(80);
  t.check('clear: markers removed', (await t.ev(`__acStub.markers.length`)) === 0);
  t.check('clear: back to the editor (no stale "ditambahkan" card)', (await t.hash()) === '#tool/chapters/edit', await t.hash());

  // ---------------------------------------------------------------- resume + narrow width
  await t.go('tool/chapters');
  await t.wait(120);
  t.check('setup: "Lanjutkan edit" card', /bab tersimpan/.test(await t.text('.chapters-resume')));
  await t.go('tool/chapters/edit');
  const over = await t.ev(`(function(){var v=document.querySelector('#view'); return v.scrollWidth - v.clientWidth;})()`);
  t.check('layout: no horizontal overflow at ' + t.width + ' px', over <= 1, String(over));

  // ---------------------------------------------------------------- English UI (live switch, engine job in English)
  await t.ev(`AC.i18n.set('en')`);
  await t.go('tool/chapters');
  await t.wait(200);
  let left = await t.idLeftovers('#app');
  t.check('en setup: no Indonesian leftovers', left.length === 0, JSON.stringify(left));
  t.check('en setup: labels', (await t.text('#primary .lbl')) === 'Create chapters' && (await t.text('#subnav [aria-selected="true"]')) === 'Chapters'
    && (await t.text('.chapters-setup .seg [aria-checked="true"]')) === 'Normal' && /^For \d+:\d\d, about \d+ chapters?\.$/.test(await t.text('.chapters-est')), await t.text('.chapters-est'));
  await t.click('#primary');
  await t.waitFor(`location.hash === '#tool/chapters/edit'`, 60000, 'editor after analyze (en)');
  await t.wait(200);
  const nEn = await t.count('.chapters-row');
  left = await t.idLeftovers('#app');
  t.check('en editor: no Indonesian leftovers (titles and transcript are content)', left.length === 0, JSON.stringify(left));
  t.check('en editor: labels', (await t.text('#primary .lbl')) === `Add ${nEn} chapters` && (await t.text('.chapters-title h2')) === `${nEn} chapters`
    && /Add chapter at playhead/.test(await t.text('.chapters-tools')), await t.text('#primary .lbl'));
  t.check('en editor: engine warning in English', /pauses and keywords/.test(await t.text('.chapters-notice')), await t.text('.chapters-notice'));
  await t.click('#primary');
  await t.waitFor(`location.hash === '#tool/chapters/result'`, 60000, 'result (en)');
  await t.wait(150);
  left = await t.idLeftovers('#app');
  t.check('en result: no Indonesian leftovers', left.length === 0, JSON.stringify(left));
  t.check('en result: title', new RegExp('^' + nEn + ' chapters added').test(await t.text('[data-pane="result"] .result-h h2')), await t.text('[data-pane="result"] .result-h h2'));
  await t.ev(`AC.i18n.set('id')`);
  await t.go('tool/chapters');
  await t.wait(100);
  t.check('back to id: primary "Buat bab"', (await t.text('#primary .lbl')) === 'Buat bab');
}
