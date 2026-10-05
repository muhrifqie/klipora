// 00_util.jsx: shared ES3 helpers, loaded first by the panel host loader (host/*.jsx, sorted by name).
// The tl* functions are split unchanged from proto/lab/timeline_helpers.jsx (verified live on Premiere 26.2.2,
// see docs/research/premiere_timeline_api.md). The ac* functions are the bac_* calling conventions.
// ES3 only: no let/const, arrow functions, JSON, Array.prototype.indexOf/forEach/map, trailing commas.
// Never call app.project.save() from host code.

var TL_TICKS = 254016000000;
var TL_BS = String.fromCharCode(92); // backslash; avoids shell/JSON escaping accidents

// ---------- JSON (ExtendScript has no JSON object) ----------
function tlJSON(v) {
    var t = typeof v;
    if (v === null || v === undefined) return "null";
    if (t === "number") return isFinite(v) ? String(v) : "null";
    if (t === "boolean") return String(v);
    if (t === "string") {
        return '"' + v.replace(/\\/g, "\\\\").replace(/"/g, '\\"').replace(/\n/g, "\\n").replace(/\r/g, "\\r").replace(/\t/g, "\\t") + '"';
    }
    if (v instanceof Array) {
        var a = [];
        for (var i = 0; i < v.length; i++) a.push(tlJSON(v[i]));
        return "[" + a.join(",") + "]";
    }
    var o = [];
    for (var k in v) {
        if (k.indexOf("__") === 0) continue;           // never touch __proto__ & friends
        if (v.hasOwnProperty && !v.hasOwnProperty(k)) continue;
        o.push(tlJSON(String(k)) + ":" + tlJSON(v[k]));
    }
    return "{" + o.join(",") + "}";
}

function tlPad(n, w) { var s = String(n); while (s.length < w) s = "0" + s; return s; }

// ---------- project / bins ----------
// Our bins are also found by their pre-rename name ("Klipora" -> "AutoCut BOT", "Klipora X" -> "AutoCut X").
function tlFindBin(name, create) {
    var root = app.project.rootItem, alts = acNameAlts(name);
    for (var a = 0; a < alts.length; a++) {
        for (var i = 0; i < root.children.numItems; i++) {
            var it = root.children[i];
            if (it.type === ProjectItemType.BIN && it.name === alts[a]) return it;
        }
    }
    return create ? root.createBin(name) : null;
}

function tlNormPath(p) { return String(p).toLowerCase().replace(/\//g, TL_BS); }

function tlFindItemByPath(bin, path) {
    var want = tlNormPath(path);
    for (var i = 0; i < bin.children.numItems; i++) {
        var it = bin.children[i];
        if (it.type === ProjectItemType.BIN) {
            var f = tlFindItemByPath(it, path);
            if (f) return f;
        } else if (tlNormPath(it.getMediaPath()) === want) return it;
    }
    return null;
}

// Import a file into `bin` (reuses an existing item in that bin). Returns ProjectItem or null.
// GOTCHA: a missing/unreadable path opens a MODAL "File Import Failure" dialog even with suppressUI=true,
// which blocks ExtendScript until a human clicks OK -> always check File.exists first.
function tlImportFile(path, bin) {
    bin = bin || app.project.getInsertionBin();
    var hit = tlFindItemByPath(bin, path);
    if (hit) return hit;
    if (!new File(path).exists) return null;
    app.project.importFiles([path], true, bin, false);
    return tlFindItemByPath(bin, path);
}

function tlSeqById(id) {
    var s = app.project.sequences;
    for (var i = 0; i < s.numSequences; i++) if (s[i].sequenceID === id) return s[i];
    return null;
}

function tlSeqIdSet() {
    var o = {}, s = app.project.sequences;
    for (var i = 0; i < s.numSequences; i++) o[s[i].sequenceID] = 1;
    return o;
}

function tlNewSeqsSince(before) {
    var out = [], s = app.project.sequences;
    for (var i = 0; i < s.numSequences; i++) if (!before[s[i].sequenceID]) out.push(s[i]);
    return out;
}

function tlFps(seq) { return TL_TICKS / Number(seq.timebase); }  // seq.timebase = ticks per frame (String)

// ---------- bac_* conventions ----------
// Every bac_* function returns a String: JSON text on success (acOk), "ERR:<message in the UI language, see acT>" on failure.
// The panel (AC.host.json) rejects on "ERR:" and on "EvalScript error.".
function acOk(v) { return tlJSON(v); }
function acErr(msg) { return "ERR:" + msg; }
function acCatch(e, where) {
    var m = (e && e.message) ? e.message : String(e);
    return "ERR:" + (where ? where + ": " : "") + m + ((e && e.line) ? " (" + acT("line", "baris") + " " + e.line + ")" : "");
}

// Sequence by id, or the active sequence when id is empty. Returns null when none.
function acSeq(id) {
    if (id) return tlSeqById(String(id));
    return app.project ? app.project.activeSequence : null;
}

// Ranges arrive either as an Array [[t0,t1],...] or as text "t0\tt1\n..." (seconds). Returns [[t0,t1],...].
function acRanges(arg) {
    var out = [], i;
    if (arg instanceof Array) {
        for (i = 0; i < arg.length; i++) {
            if (arg[i] && arg[i].length >= 2) out.push([Number(arg[i][0]), Number(arg[i][1])]);
        }
        return out;
    }
    var rows = String(arg || "").split(/\r?\n/);
    for (i = 0; i < rows.length; i++) {
        var c = rows[i].split("\t");
        if (c.length < 2) continue;
        var a = parseFloat(c[0]), b = parseFloat(c[1]);
        if (!isNaN(a) && !isNaN(b)) out.push([a, b]);
    }
    return out;
}

// Sequences created by bac_* functions in this Premiere session (only these may be deleted by the panel).
function acMarkMade(seq) {
    if (!$.global.__acMade) $.global.__acMade = {};
    if (seq) $.global.__acMade[seq.sequenceID] = 1;
}
function acWasMade(id) { return !!($.global.__acMade && $.global.__acMade[id]); }

// "<name> (Klipora)" or "<name> (Klipora 2)" ... so result sequences never share a name.
function acUniqueSeqName(name) {
    var taken = {}, s = app.project.sequences, i;
    for (i = 0; i < s.numSequences; i++) taken[s[i].name] = 1;
    if (!taken[name]) return name;
    var base = name, m = name.match(/^(.*) \((Klipora|AutoCut)\)$/);
    for (i = 2; i < 1000; i++) {
        var cand = m ? (m[1] + " (" + m[2] + " " + i + ")") : (base + " " + i);
        if (!taken[cand]) return cand;
    }
    return name;
}

// ---------- brand names (Klipora; projects made before the rename still say "AutoCut") ----------
var AC_BRAND = "Klipora";
var AC_SEQ_SUFFIX = " (Klipora)";
// Result sequences: "... (Klipora)", "... (Klipora 2)", and the old "... (AutoCut[ n])".
var AC_RESULT_RE = /\((Klipora|AutoCut)( \d+)?\)/;
function acIsResultName(name) { return AC_RESULT_RE.test(String(name)); }
// Every name we recognise for one of our tracks/bins: "Klipora Captions" -> ["Klipora Captions", "AutoCut Captions"].
// "Klipora" (our own bin) also matches the old bin "AutoCut BOT".
function acNameAlts(name) {
    name = String(name);
    var out = [name];
    if (name === AC_BRAND) out.push("AutoCut BOT");
    else if (name.indexOf(AC_BRAND + " ") === 0) out.push("AutoCut " + name.slice(AC_BRAND.length + 1));
    return out;
}
function acNameIs(actual, name) {
    var alts = acNameAlts(name), a = String(actual);
    for (var i = 0; i < alts.length; i++) if (alts[i] === a) return true;
    return false;
}
// Marker tags: "[Klipora-CH]" also matches the old "[AC-CH]".
function acTagAlts(tag) {
    tag = String(tag || "");
    var m = tag.match(/^\[(Klipora|AC)-(.+)\]$/);
    if (!m) return [tag];
    return ["[Klipora-" + m[2] + "]", "[AC-" + m[2] + "]"];
}
function acHasTag(text, tag) {
    if (!tag) return false;
    var alts = acTagAlts(tag), s = String(text || "");
    for (var i = 0; i < alts.length; i++) if (s.indexOf(alts[i]) >= 0) return true;
    return false;
}

// ---------- user-facing text (the panel sends the current language with bac_setStrings) ----------
// acT("noSeq", "Buka sequence dulu.", a, b) -> the panel string "host.noSeq" with {0}, {1} filled, else the fallback.
function bac_setStrings(obj) {
    $.global.__acStrings = obj || {};
    var n = 0; for (var k in $.global.__acStrings) n++;
    return acOk({ ok: true, n: n });
}
function acT(key, fallback) {
    var d = $.global.__acStrings, s = (d && d[key] !== undefined && d[key] !== null) ? String(d[key]) : String(fallback);
    for (var i = 2; i < arguments.length; i++) s = s.split("{" + (i - 2) + "}").join(String(arguments[i]));
    return s;
}
