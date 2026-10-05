// Shared layout + helpers for the static page generator (node website/_tools/build.mjs).
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

export const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
export const SITE = 'https://klipora.vercel.app'; // TODO: replace with the final domain before deploy
export const GH = 'https://github.com/muhrifqie/klipora';

const SPRITE = fs.readFileSync(path.join(ROOT, 'assets', 'icons.svg'), 'utf8');

export const ic = (n, cls = 'icon') => `<svg class="${cls}" aria-hidden="true"><use href="#i-${n}"/></svg>`;

export const esc = (s) => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');

// Code block: lines starting with "#" are rendered as comments and not copied.
export function code(lines, t) {
  const body = lines.map((l) => l.startsWith('#')
    ? `<span class="c">${esc(l)}</span>`
    : `<span class="ln">${esc(l)}</span>`).join('\n');
  const label = t('Copy command', 'Salin perintah');
  const copied = t('Copied', 'Tersalin');
  return `<div class="code"><pre><code>${body}</code></pre><button class="copy-btn" type="button" aria-label="${label}" data-label="${label}" data-copied="${copied}">${ic('copy')}</button></div>`;
}

// URL helpers. page = '' | 'docs' | 'changelog'
export const urlFor = (lang, page) => (lang === 'id' ? '/id' : '') + (page ? '/' + page : '') || '/';
export const href = (lang, page, hash = '') => (urlFor(lang, page) || '/') + hash;

export function layout({ lang, page, title, desc, body }) {
  const t = (en, id) => (lang === 'en' ? en : id);
  const self = urlFor(lang, page) || '/';
  const en = urlFor('en', page) || '/';
  const id = urlFor('id', page);
  const abs = (p) => SITE + (p === '/' ? '/' : p);
  const navItems = [
    [href(lang, '', '#features'), t('Features', 'Fitur'), false],
    [href(lang, '', '#install'), t('Install', 'Pasang'), false],
    [href(lang, 'docs'), t('Docs', 'Dokumentasi'), page === 'docs'],
    [href(lang, 'changelog'), t('Changelog', 'Catatan rilis'), page === 'changelog'],
  ];
  const links = navItems.map(([h, l, cur]) => `<a href="${h}"${cur ? ' aria-current="page"' : ''}>${l}</a>`).join('');
  const ogLocale = lang === 'en' ? 'en_US' : 'id_ID';
  return `<!doctype html>
<html lang="${lang}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>${esc(title)}</title>
<meta name="description" content="${esc(desc)}">
<link rel="canonical" href="${abs(self)}">
<link rel="alternate" hreflang="en" href="${abs(en)}">
<link rel="alternate" hreflang="id" href="${abs(id)}">
<link rel="alternate" hreflang="x-default" href="${abs(en)}">
<meta name="theme-color" content="#111110">
<meta name="color-scheme" content="dark">
<meta property="og:type" content="website">
<meta property="og:site_name" content="Klipora">
<meta property="og:title" content="${esc(title)}">
<meta property="og:description" content="${esc(desc)}">
<meta property="og:url" content="${abs(self)}">
<meta property="og:image" content="${SITE}/assets/og.jpg">
<meta property="og:image:width" content="1200">
<meta property="og:image:height" content="630">
<meta property="og:image:alt" content="${esc(t('Klipora: automatic editing panel for Premiere Pro', 'Klipora: panel edit otomatis untuk Premiere Pro'))}">
<meta property="og:locale" content="${ogLocale}">
<meta name="twitter:card" content="summary_large_image">
<link rel="icon" href="/favicon.svg" type="image/svg+xml">
<link rel="icon" href="/assets/favicon-32.png" sizes="32x32" type="image/png">
<link rel="apple-touch-icon" href="/assets/apple-touch-icon.png">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Bricolage+Grotesque:opsz,wght@12..96,600;12..96,700;12..96,800&family=Geist:wght@400;500;600&family=Geist+Mono:wght@400;500;600&display=swap">
<link rel="stylesheet" href="/assets/site.css">
<script defer src="/assets/site.js"></script>
</head>
<body>
${SPRITE}
<a class="skip" href="#main">${t('Skip to content', 'Lewati ke konten')}</a>
<header class="site-header">
  <div class="wrap nav">
    <a class="brand" href="${href(lang, '')}"><img src="/assets/logo.svg" alt="" width="28" height="28">Klipora</a>
    <nav class="nav-links" aria-label="${t('Main', 'Utama')}">${links}</nav>
    <div class="nav-end">
      <div class="lang" role="group" aria-label="${t('Language', 'Bahasa')}">
        <a href="${en}" hreflang="en" lang="en"${lang === 'en' ? ' aria-current="true"' : ''}>EN</a>
        <a href="${id}" hreflang="id" lang="id"${lang === 'id' ? ' aria-current="true"' : ''}>ID</a>
      </div>
      <a class="btn btn-ghost btn-sm gh-btn" href="${GH}">${ic('brand-github')}GitHub</a>
      <details class="menu">
        <summary aria-label="${t('Menu', 'Menu')}">${ic('menu-2', 'icon i-open')}${ic('x', 'icon i-close')}</summary>
        <nav class="menu-panel" aria-label="${t('Mobile', 'Seluler')}">${links}<a href="${GH}">GitHub</a></nav>
      </details>
    </div>
  </div>
</header>
<main id="main">
${body}
</main>
<footer class="site-footer">
  <div class="wrap foot">
    <a class="brand" href="${href(lang, '')}"><img src="/assets/logo.svg" alt="" width="24" height="24">Klipora</a>
    <nav aria-label="${t('Footer', 'Kaki halaman')}">
      <a href="${href(lang, 'docs')}">${t('Docs', 'Dokumentasi')}</a>
      <a href="${href(lang, 'docs', '#troubleshooting')}">${t('Troubleshooting', 'Pemecahan masalah')}</a>
      <a href="${href(lang, 'changelog')}">${t('Changelog', 'Catatan rilis')}</a>
      <a href="${GH}">GitHub</a>
      <a href="${GH}/issues">${t('Report a bug', 'Lapor bug')}</a>
    </nav>
    <p>${t('Open source. Not affiliated with Adobe. Premiere Pro is a trademark of Adobe.', 'Open source. Tidak berafiliasi dengan Adobe. Premiere Pro adalah merek dagang Adobe.')}</p>
  </div>
</footer>
</body>
</html>
`;
}

export function write(rel, html) {
  const out = path.join(ROOT, rel);
  fs.mkdirSync(path.dirname(out), { recursive: true });
  fs.writeFileSync(out, html);
  console.log('wrote', rel, Math.round(html.length / 1024) + ' KB');
}
