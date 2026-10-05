// components.mjs: unit-style checks of AC.ui components, host bridge errors, engine worker and AC.seq.lock
// (run: node tools/ui_test.mjs tools/ui_tests/components.mjs [--engine live|mock]).
export default async function (t) {
  const ev = (s) => t.ev(s);
  await ev(`(function(){ var b=document.createElement('div'); b.id='cmp'; b.style.cssText='position:fixed;left:0;top:0;right:0;bottom:0;overflow:auto;background:var(--bg);z-index:99;padding:8px;display:flex;flex-direction:column;gap:8px'; document.body.appendChild(b); window.C={}; return true; })()`);
  const mount = (js) => ev(`(function(){ var c = ${js}; document.getElementById('cmp').appendChild(c.el || c); return true; })()`);

  // segmented: click + arrow keys + disabled option
  await ev(`(C.seg = AC.ui.segmented({options:[['a','Satu'],['b','Dua'],['c','Tiga']], value:'a', ariaLabel:'tes', onChange:function(v){C.segV=v;}}), true)`);
  await mount('C.seg');
  await ev(`(C.seg.el.querySelectorAll('button')[1].click(), true)`);
  t.check('segmented: click sets value', (await ev('C.segV')) === 'b' && (await ev(`C.seg.el.querySelectorAll('button')[1].getAttribute('aria-checked')`)) === 'true');
  await ev(`(C.seg.disable('c', true), C.seg.el.dispatchEvent(new KeyboardEvent('keydown',{key:'ArrowRight',bubbles:true})), true)`);
  t.check('segmented: arrow skips disabled', (await ev('C.segV')) === 'a');

  // presets: match + custom
  await ev(`(C.pre = AC.ui.presets({options:[{value:'h',label:'Halus',hint:'Santai',values:{th:-40,min:0.8}},{value:'n',label:'Normal',hint:'Seimbang',values:{th:-35,min:0.5}}], value:'n', customHint:'Custom: manual'}), true)`);
  await mount('C.pre');
  t.check('presets: match', (await ev(`C.pre.match({th:-40,min:0.8})`)) === 'h');
  await ev(`(C.pre.sync({th:-33,min:0.5}), true)`);
  t.check('presets: custom hint', /Custom/.test(await ev(`C.pre.el.querySelector('.help').textContent`)) && (await ev(`C.pre.el.querySelectorAll('[aria-checked="true"]').length`)) === 0);

  // slider + stepper
  await ev(`(C.sl = AC.ui.slider({label:'Diam minimal', min:0.1, max:2, step:0.05, value:0.5, unit:' dtk', help:'Lebih kecil = ...', onInput:function(v){C.slV=v;}}), true)`);
  await mount('C.sl');
  await ev(`(C.sl.input.value='0.75', C.sl.input.dispatchEvent(new Event('input')), true)`);
  t.check('slider: value + unit + decimal comma', (await ev('C.slV')) === 0.75 && (await ev(`C.sl.el.querySelector('output').textContent`)) === '0,75 dtk');
  await ev(`(C.st = AC.ui.stepper({label:'Sisa sebelum', min:0, max:1, step:0.05, value:0.2, unit:' dtk', onChange:function(v){C.stV=v;}}), true)`);
  await mount('C.st');
  await ev(`(C.st.el.querySelectorAll('button')[1].click(), true)`);
  t.check('stepper: plus', Math.abs((await ev('C.stV')) - 0.25) < 1e-9);

  // switch: real focusable checkbox with role=switch
  await ev(`(C.sw = AC.ui.switch({label:'Lindungi kata', help:'pakai transkrip', checked:false, onChange:function(v){C.swV=v;}}), true)`);
  await mount('C.sw');
  await ev(`(C.sw.input.focus(), true)`);
  t.check('switch: keyboard focusable + role', await ev(`document.activeElement === C.sw.input && C.sw.input.getAttribute('role') === 'switch' && C.sw.input.tabIndex >= 0`));
  await ev(`(C.sw.input.click(), true)`);
  t.check('switch: toggles', (await ev('C.swV')) === true);

  // radio cards + chips
  await ev(`(C.rc = AC.ui.radioCards({options:[{value:'new',title:'Sequence baru',desc:'aman'},{value:'mk',title:'Marker saja'}], value:'new', onChange:function(v){C.rcV=v;}}), true)`);
  await mount('C.rc');
  await ev(`(C.rc.el.querySelectorAll('input')[1].click(), true)`);
  t.check('radio cards: select + is-on', (await ev('C.rcV')) === 'mk' && (await ev(`C.rc.el.querySelectorAll('.opt')[1].classList.contains('is-on')`)));
  await ev(`(C.ch = AC.ui.chips({options:['eee','emm','nih'], value:['eee'], multi:true, onChange:function(v){C.chV=v;}}), true)`);
  await mount('C.ch');
  await ev(`(C.ch.el.querySelectorAll('.chip')[2].click(), true)`);
  t.check('chips: multi toggle', JSON.stringify(await ev('C.chV')) === '["eee","nih"]');

  // waveform: drag + keys change threshold, canvas painted
  await ev(`(function(){ var env=[]; for (var i=0;i<400;i++) env.push(i%40<10 ? -60 : -20 - 5*Math.sin(i/7));
    C.wf = AC.ui.waveform({env:env, dur:20, threshold:-35, cuts:function(th){ var out=[],s=-1; for (var i=0;i<=env.length;i++){ var q=i<env.length&&env[i]<th; if(q&&s<0)s=i; if(!q&&s>=0){out.push([s*0.05,i*0.05]);s=-1;} } return out; }, onThreshold:function(v){C.th=v;}}); return true; })()`);
  await mount('C.wf');
  await t.wait(80);
  await ev(`(function(){ var c=C.wf.canvas, r=c.getBoundingClientRect(); c.dispatchEvent(new PointerEvent('pointerdown',{clientX:r.left+20, clientY:r.top+10, pointerId:1, bubbles:true})); c.dispatchEvent(new PointerEvent('pointerup',{pointerId:1,bubbles:true})); return true; })()`);
  const thDrag = await ev('C.th');
  t.check('waveform: drag sets threshold', typeof thDrag === 'number' && thDrag > -35, String(thDrag));
  await ev(`(C.wf.canvas.dispatchEvent(new KeyboardEvent('keydown',{key:'ArrowDown',bubbles:true})), true)`);
  t.check('waveform: arrow key nudges threshold', (await ev('C.th')) === thDrag - 1);
  t.check('waveform: canvas painted', await ev(`(function(){ var c=C.wf.canvas, d=c.getContext('2d').getImageData(0,0,c.width,c.height).data, n=0; for (var i=3;i<d.length;i+=4) if (d[i]) n++; return n > 500; })()`));

  // ribbon: click -> time, aria summary
  await ev(`(C.rb = AC.ui.ribbon({size:'lg', onClick:function(tt){C.rbT=tt;}}), C.rb.set({total:100, spans:[[10,20],{t0:40,t1:50,off:true}], playhead:30}), true)`);
  await mount('C.rb');
  await t.wait(50);
  await ev(`(function(){ var r=C.rb.el.getBoundingClientRect(); C.rb.el.dispatchEvent(new MouseEvent('click',{clientX:r.left+r.width/2, clientY:r.top+3, bubbles:true})); return true; })()`);
  t.check('ribbon: click maps to time + aria summary', Math.abs((await ev('C.rbT')) - 50) < 2 && /1 bagian dibuang/.test(await ev(`C.rb.el.getAttribute('aria-label')`)));

  // toast with action, alert, confirm click
  await ev(`(AC.ui.toast('Selesai', {action:{label:'Lihat', run:function(){C.toast=1;}}}), true)`);
  await ev(`(document.querySelector('#toasts .linkbtn').click(), true)`);
  t.check('toast: action runs', (await ev('C.toast')) === 1);
  await mount(`AC.ui.alert({kind:'warn', title:'GPU tidak terdeteksi', text:'Pakai CPU', log:'Traceback ...', actions:[{label:'Cek ulang', onClick:function(){C.al=1;}}]})`);
  t.check('alert: log + action', await ev(`!!document.querySelector('#cmp .alert-warn pre.log')`));
  await ev(`(C.cb = AC.ui.confirmClick(AC.ui.button({label:'Hapus hasil', kind:'ghost-danger', small:true}), function(){C.del=(C.del||0)+1;}), document.getElementById('cmp').appendChild(C.cb), C.cb.click(), true)`);
  t.check('confirmClick: first click only arms', !(await ev('C.del')) && /Yakin/.test(await ev('C.cb.textContent')));
  await ev(`(C.cb.click(), true)`);
  t.check('confirmClick: second click fires', (await ev('C.del')) === 1);

  // review list standalone: 3000 rows, End key, ranges()
  await ev(`(function(){ var items=[]; for (var i=0;i<3000;i++) items.push({id:'x'+i,t0:i*0.7,t1:i*0.7+0.3,kind:'gap',on:i%5!==0,conf:(i%10)/10,label:'jeda',ctx:{pre:'a',post:'b'}, note: i%7===0 ? {type:'guard', words:['Nah']} : null});
    var box=document.getElementById('cmp'); box.innerHTML='';
    C.rv = AC.ui.reviewList({doc:{tool:'t',timebase:'sequence',duration:2100,items:items}, unit:'jeda', seek:false, bulkAction:null}); box.appendChild(C.rv.el); return true; })()`);
  await t.wait(150);
  const rows = await t.count('#cmp .rv');
  t.check('review: 3000 rows virtualized', rows > 3 && rows < 80, String(rows));
  t.check('review: guard note text', /Batas digeser, kata "Nah" tetap utuh/.test(await ev(`document.querySelector('#cmp .rv-scroll').textContent`)));
  await ev(`(C.rv.focus(), document.querySelector('#cmp .rv-scroll').dispatchEvent(new KeyboardEvent('keydown',{key:'End',bubbles:true})), true)`);
  await t.wait(120);
  t.check('review: End jumps to last row (rendered)', await ev(`!!document.querySelector('#cmp .rv[data-i="2999"].is-active')`));
  t.check('review: ranges() = checked items', (await ev('C.rv.ranges().length')) === 2400);
  await t.shot('components_review3000');
  await ev(`(document.getElementById('cmp').remove(), true)`);

  // host bridge errors
  const e1 = await ev(`AC.host.json('bac_openSequence','nope').then(function(){return 'ok';}, function(e){return e.code+'|'+e.msg;})`);
  t.check('host: ERR: -> reject with message', e1 === 'HOST|Sequence tidak ditemukan. Mungkin sudah dihapus.', e1);
  const e2 = await ev(`AC.host.call('bac_doesNotExist').then(function(){return 'ok';}, function(e){return e.code;})`);
  t.check('host: unknown function -> HOST_EVAL', e2 === 'HOST_EVAL');
  const e3 = await ev(`AC.host.exec('bac_ping', [], {timeout: 1}).then(function(){return 'ok';}, function(e){return e.code;})`);
  t.check('host: timeout -> HOST_TIMEOUT', e3 === 'HOST_TIMEOUT');
  const lit = await ev(`AC.host.lit('a' + String.fromCharCode(0x2028) + 'b')`);
  t.check('host: U+2028 escaped in literals', lit.indexOf(String.fromCharCode(0x2028)) < 0 && lit.indexOf('u2028') > 0);

  // Indonesian formatters (decimal comma, WIB, Rp)
  const f = await ev(`(function(){ var U=AC.util; return [U.dtk(719.99999), U.dtk(3.66), U.mmss(48.925), U.tcode(11.5), U.rp(1500000), U.wibTime(Date.UTC(2026,9,4,18,13))].join('|'); })()`);
  t.check('util: formatters', f === '12 mnt|3,7 dtk|0:49|0:11,50|Rp 1.500.000|01:13 WIB', f);

  // core texts through AC.t: plurals, units, summary line, review unit helper (id, then en, then back to id)
  const txt = () => ev(`[AC.engine.summaryText({n: 12, on: 9, sec_on: 31.4}), AC.engine.summaryText({n: 1, on: 1}), AC.ui.unitText(null, 1), AC.ui.unitText(null, 3),
    AC.ui.unitText({one: 'pause', other: 'pauses'}, 1), AC.ui.unitText(function (n) { return n + 'x'; }, 2), AC.ui.errorTitle({code: 'NO_SEQ'}), AC.t('tool.applyN', {n: 1500, unit: 'x'})].join('|')`);
  const tid = await txt();
  t.check('i18n core: Indonesian texts', tid === '12 ditemukan, 9 dipilih (31 dtk)|1 ditemukan, 1 dipilih|bagian|bagian|pause|2x|Belum ada sequence|Terapkan 1.500 x', tid);
  await ev(`AC.i18n.switchTo('en')`);
  const ten = await txt();
  t.check('i18n core: English texts', ten === '12 found, 9 selected (31 s)|1 found, 1 selected|part|parts|pause|2x|No sequence yet|Apply 1,500 x', ten);
  t.check('i18n core: brand suffix', (await ev(`AC.brand.suffix + '|' + AC.brand.suffixRe.test('A (AutoCut 2)') + '|' + AC.brand.suffixRe.test('A (Klipora)')`)) === ' (Klipora)|true|true');
  await ev(`AC.i18n.switchTo('id')`);

  // apply helpers
  t.check('apply: mergeFrames merges/sorts/clips', JSON.stringify(await ev(`AC.apply.mergeFrames([[2,3],[0.5,1],[0.9,1.5],[9,20]], 10, 100)`)) === '[[5,15],[20,30],[90,100]]');

  // engine worker (persistent process, request ids)
  const w1 = await ev(`AC.engine.worker({tool:'echo', action:'echo', params:{x:7}, seq:null}).promise.then(function(r){return r.params.x;}, function(e){return 'ERR '+e.code+' '+e.msg;})`);
  t.check('worker: request/response', w1 === 7, String(w1));
  const w2 = await ev(`(function(){ var j = AC.engine.worker({tool:'echo', action:'slow', params:{seconds:10}, seq:null}); setTimeout(function(){ j.cancel(); }, 900);
    return j.promise.then(function(){return 'done';}, function(e){return e.code + '|' + (j.stages[0] && j.stages[0].id) + '|' + (j.pct > 0);}); })()`);
  t.check('worker: stage ids + progress + cancel', w2 === 'CANCELLED|wait|true', String(w2));
  const w3 = await ev(`AC.engine.worker({tool:'echo', action:'echo', params:{x:8}, seq:null}).promise.then(function(r){return r.params.x;}, function(e){return 'ERR '+e.code;})`);
  t.check('worker: next request after cancel', w3 === 8, String(w3));
  await ev(`(AC.engine.stopWorker(), true)`);

  // engine: python missing -> PYTHON error with Settings action
  const py = await ev(`(function(){ AC.settings.set('python','python_tidak_ada_123'); var j = AC.engine.run({tool:'echo', action:'echo', seq:null, record:false});
    return j.promise.then(function(){return 'ok';}, function(e){ AC.settings.set('python','python'); return e.code; }); })()`);
  t.check('engine: missing python -> PYTHON', py === 'PYTHON', String(py));

  // sequence lock keeps the target while the active sequence changes
  await t.setSeq('raw49'); await ev(`AC.seq.refresh().then(function(){return true;})`);
  await ev(`AC.seq.lock(true).then(function(){return true;})`);
  await t.setSeq('cut15'); await ev(`AC.seq.refresh().then(function(){return true;})`);
  t.check('seq: lock keeps target', (await ev('AC.seq.peek().id')) === 'seq-raw49');
  await ev(`AC.seq.lock(false).then(function(){return true;})`); await t.wait(200);
  t.check('seq: unlock follows active', (await ev('AC.seq.peek().id')) === 'seq-cut15');
}
