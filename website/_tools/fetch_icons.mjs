// Builds assets/icons.svg sprite from Tabler Icons (MIT) outline set.
import fs from 'node:fs';
const names = ['wave-sine','message-circle-off','repeat-off','volume-off','badge-cc','list-numbers','flame','zoom-in','device-mobile','focus-centered','microphone-2','photo-video','ear','cpu','lock','shield-check','brand-github','arrow-right','download','terminal-2','plug-connected','language','check','copy','menu-2','x','brand-windows','sparkles','external-link','chevron-down','palette','typography','music','key','folder','alert-triangle','info-circle','player-play','scissors','text-caption','video','upload','git-fork','eye','heart','book','history','arrow-up-right','cloud-off','server'];
let out = '<svg xmlns="http://www.w3.org/2000/svg" style="display:none">\n';
for (const n of names) {
  const r = await fetch(`https://unpkg.com/@tabler/icons@3.19.0/icons/outline/${n}.svg`);
  if (!r.ok) { console.error('missing', n); continue; }
  const s = await r.text();
  const inner = s.replace(/^[\s\S]*?<svg[^>]*>/, '').replace(/<\/svg>\s*$/, '').replace(/<path stroke="none" d="M0 0h24v24H0z" fill="none"\s*\/>/, '').trim();
  out += `<symbol id="i-${n}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round">${inner.replace(/\s+/g,' ')}</symbol>\n`;
}
out += '</svg>\n';
fs.mkdirSync('../assets', { recursive: true });
fs.writeFileSync('../assets/icons.svg', out);
console.log('ok', out.length);
