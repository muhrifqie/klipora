const [t] = await (await fetch('http://localhost:8088/json/list')).json();
const ws = new WebSocket(t.webSocketDebuggerUrl);
await new Promise((r) => ws.addEventListener('open', r));
ws.addEventListener('message', (ev) => {
  const m = JSON.parse(ev.data);
  if (m.method === 'Runtime.exceptionThrown') console.log('EXC', JSON.stringify(m.params.exceptionDetails.exception?.description || m.params.exceptionDetails.text), m.params.exceptionDetails.lineNumber);
  if (m.method === 'Runtime.consoleAPICalled') console.log('LOG', m.params.args.map(a => a.value ?? a.description).join(' '));
});
ws.send(JSON.stringify({ id: 1, method: 'Runtime.enable' }));
ws.send(JSON.stringify({ id: 2, method: 'Page.reload', params: { ignoreCache: true } }));
await new Promise((r) => setTimeout(r, 5000));
ws.close();
