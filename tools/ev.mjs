// node ev.mjs "<extendscript>" : run ExtendScript inside Premiere via the panel
const [t] = await (await fetch('http://localhost:8088/json/list')).json();
const ws = new WebSocket(t.webSocketDebuggerUrl);
await new Promise((r) => ws.addEventListener('open', r));
const expr = `new Promise(r => window.__adobe_cep__.evalScript(${JSON.stringify(process.argv[2])}, r))`;
ws.addEventListener('message', (ev) => { const m = JSON.parse(ev.data); if (m.id === 1) { console.log(m.result?.result?.value); ws.close(); } });
ws.send(JSON.stringify({ id: 1, method: 'Runtime.evaluate', params: { expression: expr, awaitPromise: true, returnByValue: true } }));
