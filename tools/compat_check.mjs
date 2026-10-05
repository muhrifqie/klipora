// compat_check.mjs: static guard for the CEP 12 runtime (docs/SPEC.md rule 7). Run: node tools/compat_check.mjs
// panel/js/**/*.js : must parse, must be wrapped in an IIFE (no globals except window.AC), no APIs newer than
//                    Chrome 99 or banned by the SPEC (structuredClone, .at(), replaceAll, ES modules, ...).
// panel/css/**/*.css: no :has(), nesting, container queries, color-mix(), dvh/svh/lvh, text-wrap, @layer, oklch().
// panel/host/*.jsx : must parse, ES3 only (no let/const/arrows/template strings/Array extras/JSON/trailing commas).
// Exit code 1 on any finding. Also imported by tools/ui_test.mjs (runs before every UI test).
import fs from 'fs';
import path from 'path';
import vm from 'vm';
import { fileURLToPath, pathToFileURL } from 'url';

const here = path.dirname(fileURLToPath(import.meta.url));
const PANEL = path.resolve(here, '..', 'panel');

function walk(dir, ext, out = []) {
  for (const f of fs.readdirSync(dir, { withFileTypes: true })) {
    const p = path.join(dir, f.name);
    if (f.isDirectory()) walk(p, ext, out); else if (p.endsWith(ext)) out.push(p);
  }
  return out;
}

// Blank out comments and string/template literal contents (keeps line numbers) so token checks see code only.
export function codeOnly(src) {
  let out = '', i = 0, n = src.length;
  while (i < n) {
    const c = src[i], d = src[i + 1];
    if (c === '/' && d === '/') { while (i < n && src[i] !== '\n') { out += ' '; i++; } continue; }
    if (c === '/' && d === '*') { while (i < n && !(src[i] === '*' && src[i + 1] === '/')) { out += src[i] === '\n' ? '\n' : ' '; i++; } out += '  '; i += 2; continue; }
    if (c === '"' || c === "'" || c === '`') {
      const q = c; out += q; i++;
      while (i < n && src[i] !== q) { if (src[i] === '\\') { out += '  '; i += 2; continue; } out += src[i] === '\n' ? '\n' : (q === '`' ? '~' : ' '); i++; }
      out += q; i++; continue;
    }
    out += c; i++;
  }
  return out;
}
const lineOf = (s, idx) => s.slice(0, idx).split('\n').length;

const JS_BANNED = [
  [/\bstructuredClone\s*\(/, 'structuredClone (SPEC rule 7)'], [/\.at\(\s*-?\d/, 'Array/String .at() (SPEC rule 7)'],
  [/\.replaceAll\(/, 'replaceAll (avoid, SPEC rule 7)'], [/\bObject\.hasOwn\(/, 'Object.hasOwn (Chrome 93+, avoid)'],
  [/\.findLast(Index)?\(/, 'findLast (Chrome 97+, avoid)'], [/\.to(Sorted|Reversed|Spliced)\(/, 'change-array-by-copy (Chrome 110+)'],
  [/\bObject\.groupBy\b|\bMap\.groupBy\b/, 'groupBy (Chrome 117+)'], [/\bAbortSignal\.(timeout|any)\b/, 'AbortSignal.timeout (Chrome 103+)'],
  [/^\s*(import|export)\s[^(]/m, 'ES module syntax (CEP panel uses classic scripts)'], [/\.roundRect\(/, 'canvas roundRect (Chrome 99 edge)'],
  [/\bnavigator\.clipboard\b/, 'navigator.clipboard (unreliable in CEP, use AC.util.copyText)'], [/\.showPopover\(|\bpopover=/, 'popover API']
];
const CSS_BANNED = [
  [/:has\(/, ':has() (Chrome 105+)'], [/color-mix\(/, 'color-mix() (Chrome 111+)'], [/\b\d*\.?\d+(dvh|svh|lvh|dvw|svw|lvw)\b/, 'dynamic viewport units (Chrome 108+)'],
  [/@container\b|container-type\s*:/, 'container queries (Chrome 105+)'], [/text-wrap\s*:/, 'text-wrap (Chrome 114+)'], [/@layer\b/, '@layer (avoid)'],
  [/\boklch\(|\boklab\(|\blab\(|\blch\(/, 'modern colour functions'], [/@scope\b/, '@scope'], [/\bsubgrid\b/, 'subgrid (Chrome 117+)']
];
const ES3_BANNED = [
  [/\blet\s+[A-Za-z_$]/, 'let'], [/\bconst\s+[A-Za-z_$]/, 'const'], [/=>/, 'arrow function'], [/`/, 'template literal'],
  [/\.(forEach|map|filter|reduce|some|every)\(/, 'Array extras (ES5)'], [/\bJSON\.(parse|stringify)\b/, 'JSON object (use tlJSON)'],
  [/\bObject\.(keys|create|defineProperty|assign)\(/, 'Object ES5+ API'], [/\bArray\.isArray\(/, 'Array.isArray (ES5)'],
  [/\.trim\(\)/, 'String.trim (ES5)'], [/\bDate\.now\(\)/, 'Date.now (ES5)'], [/,\s*[\]}]/, 'trailing comma'],
  [/\bclass\s+[A-Z]/, 'class'], [/\.\.\.[A-Za-z_$[]/, 'spread']
];

export function check() {
  const findings = [];
  const add = (file, line, msg) => findings.push(`${path.relative(path.resolve(PANEL, '..'), file)}:${line} ${msg}`);
  for (const f of walk(path.join(PANEL, 'js'), '.js')) {
    const src = fs.readFileSync(f, 'utf8');
    try { new vm.Script(src, { filename: f }); } catch (e) { add(f, 0, 'syntax error: ' + e.message); continue; }
    const code = codeOnly(src);
    for (const [re, msg] of JS_BANNED) { const m = re.exec(code); if (m) add(f, lineOf(code, m.index), msg); }
    if (!/^\s*\(function\s*\(/.test(code)) add(f, 1, 'not wrapped in an IIFE (globals collide with CEP: use window.AC)');
    if (src.indexOf(String.fromCharCode(0x2028)) >= 0 || src.indexOf(String.fromCharCode(0x2029)) >= 0) add(f, 0, 'raw U+2028/2029 character (breaks older parsers)');
  }
  for (const f of walk(path.join(PANEL, 'css'), '.css')) {
    const src = fs.readFileSync(f, 'utf8'), code = src.replace(/\/\*[\s\S]*?\*\//g, (m) => m.replace(/[^\n]/g, ' '));
    for (const [re, msg] of CSS_BANNED) { const m = re.exec(code); if (m) add(f, lineOf(code, m.index), msg); }
    // Nesting: a "{" while inside a plain rule (at-rule blocks like @media/@keyframes/@supports may contain rules).
    const stack = []; let sel = '';
    for (let i = 0; i < code.length; i++) {
      const c = code[i];
      if (c === '{') { const isAt = /@[\w-]+[^{}]*$/.test(sel.trim()); if (stack.length && !stack[stack.length - 1]) add(f, lineOf(code, i), 'CSS nesting'); stack.push(isAt); sel = ''; }
      else if (c === '}') { stack.pop(); sel = ''; } else if (c === ';') sel = ''; else sel += c;
    }
  }
  for (const f of walk(path.join(PANEL, 'host'), '.jsx')) {
    const src = fs.readFileSync(f, 'utf8');
    try { new vm.Script(src, { filename: f }); } catch (e) { add(f, 0, 'syntax error: ' + e.message); continue; }
    const code = codeOnly(src).replace(/\/(?![*/])(?:\\.|\[[^\]\n]*\]|[^/\n\\])+\/[gimy]*/g, (m) => m.replace(/./g, ' ')); // drop regex literals
    for (const [re, msg] of ES3_BANNED) {
      const g = new RegExp(re.source, 'g'); let m;
      while ((m = g.exec(code))) add(f, lineOf(code, m.index), 'ES3: ' + msg);
    }
  }
  return findings;
}

if (import.meta.url === pathToFileURL(process.argv[1]).href) {
  const f = check();
  console.log(f.length ? f.join('\n') + `\n${f.length} compat finding(s)` : 'compat: OK (panel js/css Chrome 99, host jsx ES3)');
  process.exit(f.length ? 1 : 0);
}
