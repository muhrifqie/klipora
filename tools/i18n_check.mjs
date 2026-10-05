// i18n_check.mjs: static checks for the Indonesian + English UI. Run: node tools/i18n_check.mjs [--quiet] [paths...]
//  1. panel/locales/{id,en}.json and engine/ac/locales/{id,en}.json: same keys, same {placeholders}, plural
//     objects ({"one","other"}) valid, no empty strings.
//  2. Hardcoded Indonesian left in code (heuristic on string literals, comments ignored):
//     panel/js/**/*.js, panel/host/*.jsx (acT("key", "fallback") fallbacks are allowed), engine/ac/**/*.py.
//     A literal is flagged when it reads like Indonesian UI text: several words incl. a common Indonesian word, a
//     capitalised Indonesian word ("Batal"), or the units "dtk"/"mnt". Lowercase one-word ids ('potong') pass.
//     Mark intentional data (lexicons, regexes for Indonesian speech, test fixtures) with a line comment
//     containing "i18n-ignore" (JS: // i18n-ignore, Python: # i18n-ignore), or "i18n-ignore-file" anywhere in a file.
// `paths` limits part 2 to files under those paths (part 1 always runs). Exit code 1 on any finding.
import fs from 'fs';
import path from 'path';
import { fileURLToPath, pathToFileURL } from 'url';

const here = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(here, '..');
const LOCALES = [['panel', path.join(ROOT, 'panel', 'locales')], ['engine', path.join(ROOT, 'engine', 'ac', 'locales')]];

// Common Indonesian words that practically never appear in English UI text or code.
export const ID_WORDS = ('yang dan tidak dengan untuk sudah belum dulu kamu sedang gagal berhasil selesai potong terapkan batal ' +
  'batalkan tinjau hapus simpan buka pilih semua lanjut kembali pengaturan jeda ditemukan dipilih dibuang disimpan ' +
  'coba lagi menunggu memuat mengecek sebentar bagian kata ukuran warna posisi judul klip kamera suara musik latar ' +
  'huruf tebal miring garis bawah atas kiri kanan tengah otomatis aturan tanpa nanti harus bisa akan juga atau saja ' +
  'lebih kurang cepat lambat panjang pendek besar kecil baru lama asli hasil proses berjalan ulang salin tutup lihat ' +
  'ubah tambah kosong isi nama dari ini itu ada pakai dipakai cek periksa tunggu masih terlalu sama setiap buat ' +
  'dibuat ganti diganti jalankan mati nyala detik menit kunci tersimpan alat dalam luar sini sana jika kalau karena ' +
  'agar supaya oleh pada bila belum mulai tampil tampilkan sembunyikan gambar gaya rapi rapikan sesuai mengikuti ' +
  'ikuti terlihat tetap sudah ditambah ditaruh taruh dapat tidak menemukan ketemu temukan baca tulis gandakan ' +
  'urutan perlu masuk keluar pertama terakhir lewati dilewati sorot catatan pindah pindahkan atur diatur').split(/\s+/);
const IDSET = new Set(ID_WORDS);
const UNITS = /(^|[^A-Za-z])(dtk|mnt)([^A-Za-z]|$)/;

function walk(dir, ext, out = []) {
  if (!fs.existsSync(dir)) return out;
  for (const f of fs.readdirSync(dir, { withFileTypes: true })) {
    const p = path.join(dir, f.name);
    if (f.isDirectory()) { if (!/^(__pycache__|node_modules|locales|tests|fonts|templates)$/.test(f.name)) walk(p, ext, out); }
    else if (p.endsWith(ext)) out.push(p);
  }
  return out;
}

/* ---------------------------------------------------------------- 1. locale files */
function placeholders(v) {
  const s = typeof v === 'string' ? v : Object.values(v || {}).join(' ');
  return [...new Set((s.match(/\{\w+\}/g) || []))].sort().join(',');
}
function checkLocales(findings) {
  const stats = [];
  for (const [side, dir] of LOCALES) {
    const read = (l) => {
      const f = path.join(dir, l + '.json');
      if (!fs.existsSync(f)) { findings.push(`${side}: ${path.relative(ROOT, f)} missing`); return {}; }
      try { return JSON.parse(fs.readFileSync(f, 'utf8').replace(/^\uFEFF/, '')); } catch (e) { findings.push(`${side}: ${l}.json is not valid JSON: ${e.message}`); return {}; }
    };
    const id = read('id'), en = read('en');
    for (const k of Object.keys(id)) if (!(k in en)) findings.push(`${side}: key "${k}" missing in en.json`);
    for (const k of Object.keys(en)) if (!(k in id)) findings.push(`${side}: key "${k}" missing in id.json`);
    for (const k of Object.keys(id)) {
      if (!(k in en)) continue;
      for (const [l, v] of [['id', id[k]], ['en', en[k]]]) {
        if (v && typeof v === 'object') {
          if (typeof v.other !== 'string') findings.push(`${side}: ${l} "${k}" plural object needs "other"`);
          for (const f of Object.keys(v)) if (!/^(zero|one|two|few|many|other)$/.test(f)) findings.push(`${side}: ${l} "${k}" bad plural form "${f}"`);
        } else if (typeof v !== 'string') findings.push(`${side}: ${l} "${k}" must be a string or a plural object`);
        else if (!v.trim() && k !== 'common.empty') findings.push(`${side}: ${l} "${k}" is empty`);
      }
      if (placeholders(id[k]) !== placeholders(en[k])) findings.push(`${side}: "${k}" placeholders differ: id {${placeholders(id[k])}} vs en {${placeholders(en[k])}}`);
      if (typeof en[k] === 'string' && /\u2014/.test(en[k])) findings.push(`${side}: en "${k}" uses an em dash`);
    }
    stats.push(`${side} ${Object.keys(id).length} keys`);
  }
  return stats;
}

/* ---------------------------------------------------------------- 2. hardcoded text */
function looksIndonesian(s) {
  const t = s.replace(/\$\{[^}]*\}|\{[\w.]+\}|%[sd]/g, ' ').trim();
  if (!t || /^[\w.-]+$/.test(t) && t.indexOf('.') > 0) return false;           // a locale key
  if (/^(https?:|[A-Za-z]:\\|\.?\/|#|\[)/.test(t)) return false;                // url, path, selector, tag
  if (UNITS.test(t)) return true;
  const words = t.split(/[^A-Za-zÀ-ÿ]+/).filter(Boolean);
  if (!words.length) return false;
  if (words.length === 1) return /^[A-Z]/.test(words[0]) && IDSET.has(words[0].toLowerCase());
  return words.some((w) => IDSET.has(w.toLowerCase()));
}

// JS/JSX literals with line numbers; comments skipped. Template literals: the raw text.
function jsLiterals(src) {
  const out = []; let i = 0, line = 1; const n = src.length;
  let prevSig = '';
  while (i < n) {
    const c = src[i], d = src[i + 1];
    if (c === '\n') { line++; i++; continue; }
    if (c === '/' && d === '/') { while (i < n && src[i] !== '\n') i++; continue; }
    if (c === '/' && d === '*') { i += 2; while (i < n && !(src[i] === '*' && src[i + 1] === '/')) { if (src[i] === '\n') line++; i++; } i += 2; continue; }
    if (c === '/' && /[(,=:[!&|?{};+\-*%<>~^]|^$|return$|typeof$/.test(prevSig)) {   // regex literal
      i++; let cls = false;
      while (i < n && src[i] !== '\n') { if (src[i] === '\\') { i += 2; continue; } if (src[i] === '[') cls = true; else if (src[i] === ']') cls = false; else if (src[i] === '/' && !cls) break; i++; }
      i++; while (/[a-z]/.test(src[i] || '')) i++; prevSig = 'x'; continue;
    }
    if (c === '"' || c === "'" || c === '`') {
      const q = c, start = line, st = i; let s = ''; i++;
      while (i < n && src[i] !== q) {
        if (src[i] === '\\') { s += src[i + 1] === 'n' ? '\n' : src[i + 1]; i += 2; continue; }
        if (src[i] === '\n') line++;
        s += src[i]; i++;
      }
      i++; out.push({ s, line: start, at: st }); prevSig = 'x'; continue;
    }
    if (!/\s/.test(c)) { prevSig = /[A-Za-z_$0-9]/.test(c) ? (prevSig.match(/^[A-Za-z_$0-9]*$/) ? prevSig + c : c) : c; }
    i++;
  }
  return out;
}

// Python literals with line numbers; comments and docstrings (a string that is a whole statement) skipped.
function pyLiterals(src) {
  const out = []; let i = 0, line = 1; const n = src.length; let lineStart = true;
  while (i < n) {
    const c = src[i];
    if (c === '\n') { line++; i++; lineStart = true; continue; }
    if (c === '#') { while (i < n && src[i] !== '\n') i++; continue; }
    const pre = /^[rRbBfFuU]{0,2}/.exec(src.slice(i, i + 3))[0];
    const q0 = src[i + pre.length];
    if ((q0 === '"' || q0 === "'") && (!pre || !/[A-Za-z0-9_]/.test(src[i - 1] || ''))) {
      const start = line, raw = /r/i.test(pre); let j = i + pre.length; const triple = src.slice(j, j + 3) === q0.repeat(3);
      const qq = triple ? q0.repeat(3) : q0; j += qq.length; let s = '';
      while (j < n && src.slice(j, j + qq.length) !== qq) {
        if (src[j] === '\\' && !raw) { s += src[j + 1]; j += 2; continue; }
        if (src[j] === '\\' && raw) { s += src[j] + src[j + 1]; j += 2; continue; }
        if (src[j] === '\n') { line++; if (!triple) break; }
        s += src[j]; j++;
      }
      j += qq.length;
      const rest = (src.slice(j).split('\n')[0] || '').trim();
      const isDoc = triple && lineStart && (!rest || rest[0] === '#');
      if (!isDoc) out.push({ s, line: start });
      i = j; lineStart = false; continue;
    }
    if (!/\s/.test(c)) lineStart = false;
    i++;
  }
  return out;
}

const PY_SKIP = /[\\/](ai[\\/]prompts\.py|_filler_lexicon\.py|_profanity_lexicon\.py|captions[\\/]fonts[\\/])/;
function checkCode(findings, only) {
  const files = [
    ...walk(path.join(ROOT, 'panel', 'js'), '.js').map((f) => [f, 'js']),
    ...walk(path.join(ROOT, 'panel', 'host'), '.jsx').map((f) => [f, 'jsx']),
    ...walk(path.join(ROOT, 'engine', 'ac'), '.py').filter((f) => !PY_SKIP.test(f)).map((f) => [f, 'py'])
  ].filter(([f]) => !only.length || only.some((o) => path.resolve(f).toLowerCase().startsWith(o)));
  let n = 0;
  for (const [f, kind] of files) {
    let src = fs.readFileSync(f, 'utf8');
    if (src.includes('i18n-ignore-file')) continue;
    n++;
    const lines = src.split('\n');
    if (kind === 'jsx') src = src.replace(/(acT\(\s*"[^"\\]*"\s*,\s*)"(?:[^"\\]|\\.)*"/g, (m, a) => a + '""');
    const lits = kind === 'py' ? pyLiterals(src) : jsLiterals(src);
    for (const { s, line } of lits) {
      if (/i18n-ignore/.test(lines[line - 1] || '')) continue;
      if (looksIndonesian(s)) findings.push(`${path.relative(ROOT, f)}:${line} hardcoded text: ${JSON.stringify(s.length > 70 ? s.slice(0, 70) + '...' : s)}`);
    }
  }
  return n;
}

export function check(only = []) {
  const findings = [];
  const stats = checkLocales(findings);
  const n = checkCode(findings, only.map((p) => path.resolve(ROOT, p).toLowerCase()));
  return { findings, stats, files: n };
}

if (import.meta.url === pathToFileURL(process.argv[1]).href) {
  const args = process.argv.slice(2), quiet = args.includes('--quiet');
  const { findings, stats, files } = check(args.filter((a) => !a.startsWith('--')));
  if (findings.length) console.log((quiet ? findings.slice(0, 40) : findings).join('\n') + (quiet && findings.length > 40 ? `\n... ${findings.length - 40} more` : ''));
  console.log(`i18n: ${stats.join(', ')}, ${files} code files scanned, ${findings.length} finding(s)`);
  process.exit(findings.length ? 1 : 0);
}
