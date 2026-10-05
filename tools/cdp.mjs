// Usage: node cdp.mjs "<js expression>"  -> evaluates in the CEP panel via devtools port 8088
const [t] = await (await fetch('http://localhost:8088/json/list')).json();
const ws = new WebSocket(t.webSocketDebuggerUrl);
await new Promise((r) => ws.addEventListener('open', r));
let id = 0;
const send = (method, params) => new Promise((resolve) => {
  const my = ++id;
  ws.addEventListener('message', function h(ev) {
    const m = JSON.parse(ev.data);
    if (m.id === my) { ws.removeEventListener('message', h); resolve(m); }
  });
  ws.send(JSON.stringify({ id: my, method, params }));
});
const r = await send('Runtime.evaluate', { expression: process.argv[2], awaitPromise: true, returnByValue: true });
console.log(JSON.stringify(r.result?.result?.value ?? r.result, null, 1));
ws.close();
