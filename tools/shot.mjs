import fs from 'fs';
const [t] = await (await fetch('http://localhost:8088/json/list')).json();
const ws = new WebSocket(t.webSocketDebuggerUrl);
await new Promise((r) => ws.addEventListener('open', r));
ws.addEventListener('message', (ev) => { const m = JSON.parse(ev.data); if (m.id === 1) { fs.writeFileSync(process.argv[2], Buffer.from(m.result.data, 'base64')); ws.close(); } });
ws.send(JSON.stringify({ id: 1, method: 'Page.captureScreenshot', params: { format: 'png' } }));
