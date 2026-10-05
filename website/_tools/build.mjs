// Generates the static pages. Run: node website/_tools/build.mjs  (output is committed; Vercel serves it as-is)
import { layout, write, SITE } from './lib.mjs';
import { home } from './home.mjs';
import { docs } from './docs.mjs';
import { changelog } from './changelog.mjs';

const PAGES = {
  '': {
    fn: home,
    title: { en: 'Klipora: free automatic editing panel for Premiere Pro', id: 'Klipora: panel edit otomatis gratis untuk Premiere Pro' },
    desc: {
      en: 'Open-source Premiere Pro panel that cuts silence, fillers and retakes, adds animated captions, auto zoom and 9:16 reframing, and cleans your voice. Runs locally on Windows.',
      id: 'Panel Premiere Pro open source untuk potong silence, filler, dan take ulang, caption animasi, auto zoom, reframe 9:16, dan pembersih suara. Jalan lokal di Windows.',
    },
  },
  docs: {
    fn: docs,
    title: { en: 'Docs: install and use Klipora', id: 'Dokumentasi: pasang dan pakai Klipora' },
    desc: {
      en: 'Install Klipora for Premiere Pro 2026: Python, FFmpeg and GPU setup, enabling unsigned CEP extensions, every tool, AI providers and troubleshooting.',
      id: 'Pasang Klipora untuk Premiere Pro 2026: Python, FFmpeg, GPU, mengaktifkan ekstensi CEP tanpa tanda tangan, semua alat, penyedia AI, dan pemecahan masalah.',
    },
  },
  changelog: {
    fn: changelog,
    title: { en: 'Changelog: Klipora', id: 'Catatan rilis: Klipora' },
    desc: { en: 'What changed in each Klipora release.', id: 'Perubahan di setiap rilis Klipora.' },
  },
};

const urls = [];
for (const lang of ['en', 'id']) {
  for (const [page, cfg] of Object.entries(PAGES)) {
    const html = layout({ lang, page, title: cfg.title[lang], desc: cfg.desc[lang], body: cfg.fn(lang) });
    const rel = (lang === 'id' ? 'id/' : '') + (page ? page + '/' : '') + 'index.html';
    write(rel, html);
    urls.push({ loc: SITE + (lang === 'id' ? '/id' : '') + (page ? '/' + page : '') + (lang === 'en' && !page ? '/' : ''), page });
  }
}

const today = new Date().toISOString().slice(0, 10);
const alt = (page) => ['en', 'id'].map((l) => `<xhtml:link rel="alternate" hreflang="${l}" href="${SITE}${l === 'id' ? '/id' : ''}${page ? '/' + page : (l === 'en' ? '/' : '')}"/>`).join('');
write('sitemap.xml', `<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" xmlns:xhtml="http://www.w3.org/1999/xhtml">
${urls.map((u) => `  <url><loc>${u.loc}</loc><lastmod>${today}</lastmod>${alt(u.page)}</url>`).join('\n')}
</urlset>
`);
write('robots.txt', `User-agent: *\nAllow: /\nDisallow: /_tools/\nDisallow: /_shots/\n\nSitemap: ${SITE}/sitemap.xml\n`);
