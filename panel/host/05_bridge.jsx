// 05_bridge.jsx: the bac_* host API used by the panel foundation (docs/PANEL_API.md, "Host functions").
// ES3. Every function catches its own errors and returns JSON text (acOk) or "ERR:<message>" (acErr).
// Times are seconds (Number). Optional `seqId` arguments default to the active sequence.
// Rules (docs/SPEC.md section 0): never save the project, never touch the user's original sequence when cutting
// (work on a clone), check File.exists before any import (a missing file opens a blocking modal dialog).

var AC_UNSET = -1000;            // seq.getInPoint()/getOutPoint() return "-400000" when not set
var AC_EVENT_TYPE = "com.klipora.ev";

function bac_ping() {
    try {
        return acOk({ ok: true, version: app.version, project: app.project ? app.project.name : null,
                      helpers: typeof tlRemoveRanges === "function" && typeof gxPlaceOverlay === "function" });
    } catch (e) { return acCatch(e, "bac_ping"); }
}

// ---------- sequence info (docs/SPEC.md section 3) ----------
// level "lite": header + per-track clip counts + main-track spans + selection count (cheap; used on every event).
// level "full" (default): adds every clip, markers and the selection (the Timeline JSON sent to the engine).
function bac_seqInfo(level, seqId) {
    try {
        var seq = acSeq(seqId);
        if (!seq) return "null";
        var t0 = new Date().getTime();
        var full = level !== "lite";
        var inS = Number(seq.getInPoint()), outS = Number(seq.getOutPoint());
        var o = {
            id: seq.sequenceID, name: seq.name, fps: tlFps(seq), timebase: String(seq.timebase),
            width: seq.frameSizeHorizontal, height: seq.frameSizeVertical,
            displayFormat: seq.getSettings().videoDisplayFormat,
            duration: Number(seq.end) / TL_TICKS,
            inPoint: inS > AC_UNSET ? inS : null, outPoint: outS > AC_UNSET ? outS : null,
            player: seq.getPlayerPosition().seconds,
            active: !!(app.project.activeSequence && app.project.activeSequence.sequenceID === seq.sequenceID),
            level: full ? "full" : "lite"
        };
        o.video = acTracks(seq.videoTracks, full);
        o.audio = acTracks(seq.audioTracks, full);
        var sel = seq.getSelection(), i;
        o.selectedCount = sel ? sel.length : 0;
        if (full) {
            o.markers = tlReadMarkers(seq);
            o.selection = [];
            for (i = 0; sel && i < sel.length; i++) {
                o.selection.push({ name: sel[i].name, kind: sel[i].mediaType === "Audio" ? "audio" : "video",
                                   track: sel[i].parentTrackIndex, start: sel[i].start.seconds, end: sel[i].end.seconds,
                                   nodeId: sel[i].nodeId });
            }
        } else {
            o.nMarkers = seq.markers.numMarkers;
            o.spans = acMainSpans(seq, 3000);
        }
        o.ms = new Date().getTime() - t0;
        return acOk(o);
    } catch (e) { return acCatch(e, "bac_seqInfo"); }
}

function acTracks(tracks, full) {
    var out = [];
    for (var t = 0; t < tracks.numTracks; t++) {
        var tr = tracks[t];
        var o = { index: t, name: tr.name, muted: !!tr.isMuted(), locked: !!tr.isLocked(), targeted: !!tr.isTargeted(),
                  count: tr.clips.numItems };
        if (full) {
            o.clips = [];
            for (var i = 0; i < tr.clips.numItems; i++) {
                var c = tr.clips[i], pi = c.projectItem, path = null, nested = false;
                if (pi) {
                    nested = !!(pi.isSequence && pi.isSequence());
                    path = nested ? null : (pi.getMediaPath() || null);
                }
                o.clips.push({ name: c.name, path: path, start: c.start.seconds, end: c.end.seconds,
                               "in": c.inPoint.seconds, out: c.outPoint.seconds, speed: c.getSpeed(),
                               disabled: !!c.disabled, selected: !!c.isSelected(), mgt: !!c.isMGT(), nested: nested,
                               nodeId: c.nodeId });
            }
        }
        out.push(o);
    }
    return out;
}

// [start, end] of each clip on the first non-empty video track (else audio): the Home cut map ribbon.
function acMainSpans(seq, max) {
    var groups = [seq.videoTracks, seq.audioTracks];
    for (var g = 0; g < 2; g++) {
        for (var t = 0; t < groups[g].numTracks; t++) {
            var clips = groups[g][t].clips;
            if (!clips.numItems) continue;
            var out = [];
            for (var i = 0; i < clips.numItems && i < max; i++) out.push([clips[i].start.seconds, clips[i].end.seconds]);
            return out;
        }
    }
    return [];
}

function bac_activeSeqId() {
    try {
        var s = app.project.activeSequence;
        return s ? acOk({ id: s.sequenceID, name: s.name }) : "null";
    } catch (e) { return acCatch(e, "bac_activeSeqId"); }
}

function bac_openSequence(id) {
    try {
        var s = tlSeqById(String(id));
        if (!s) return acErr(acT("coreSeqGone", "Sequence tidak ditemukan. Mungkin sudah dihapus."));
        app.project.openSequence(s.sequenceID);
        return acOk({ ok: true, id: s.sequenceID, name: s.name });
    } catch (e) { return acCatch(e, "bac_openSequence"); }
}

// Move the playhead (sequence seconds), frame-aligned.
function bac_seek(sec, seqId) {
    try {
        var seq = acSeq(seqId);
        if (!seq) return acErr(acT("coreNoSeq", "Buka sequence dulu."));
        var tpf = Number(seq.timebase), f = Math.max(0, Math.round(Number(sec) * tlFps(seq)));
        seq.setPlayerPosition(String(f * tpf));
        return acOk({ ok: true, t: seq.getPlayerPosition().seconds });
    } catch (e) { return acCatch(e, "bac_seek"); }
}

// ---------- markers ----------
// list: [{t, end, name, comment, tag, color (0..7), type ("Comment"|"Chapter"|"Segmentation")}] or text lines
// "start\tend\tname\tcomment". The tag (e.g. "[AC-CH]") is appended to the comment so re-runs can replace it.
function bac_addMarkers(list, seqId) {
    try {
        var seq = acSeq(seqId);
        if (!seq) return acErr(acT("coreNoSeq", "Buka sequence dulu."));
        var items = acMarkerList(list), n = 0;
        for (var i = 0; i < items.length; i++) {
            var m = items[i], t = Number(m.t);
            if (isNaN(t)) continue;
            var comment = m.comment || "";
            if (m.tag) comment = comment ? comment + "\n" + m.tag : m.tag;
            var dur = (m.end !== undefined && m.end !== null && Number(m.end) > t) ? Number(m.end) - t : 0;
            tlAddMarker(seq, t, m.name || "", comment, m.type || "Comment",
                        (m.color === undefined || m.color === null) ? null : Number(m.color), dur);
            n++;
        }
        return acOk({ ok: true, n: n });
    } catch (e) { return acCatch(e, "bac_addMarkers"); }
}

function acMarkerList(list) {
    if (list instanceof Array) return list;
    var out = [], rows = String(list || "").split(/\r?\n/);
    for (var i = 0; i < rows.length; i++) {
        var c = rows[i].split("\t");
        if (c.length < 3) continue;
        out.push({ t: parseFloat(c[0]), end: parseFloat(c[1]), name: c[2], comment: c[3] || "" });
    }
    return out;
}

// Delete every marker whose name or comment contains `tag`. Returns {n}.
function bac_clearMarkersByTag(tag, seqId) {
    try {
        var seq = acSeq(seqId);
        if (!seq) return acErr(acT("coreNoSeq", "Buka sequence dulu."));
        if (!tag) return acErr(acT("coreNoTag", "Tag marker kosong."));
        var mk = seq.markers, m = mk.getFirstMarker(), list = [], guard = 0;
        while (m && guard++ < 100000) {
            if (acHasTag(m.name, tag) || acHasTag(m.comments, tag)) list.push(m);   // also the old [AC-*] tags
            m = mk.getNextMarker(m);
        }
        for (var i = 0; i < list.length; i++) mk.deleteMarker(list[i]);
        return acOk({ ok: true, n: list.length });
    } catch (e) { return acCatch(e, "bac_clearMarkersByTag"); }
}

// ---------- removing ranges (docs/SPEC.md section 4) ----------
// Clone the sequence as "<name> (Klipora)" (unique). The original is re-activated by tlCloneSequence.
function bac_cloneSeq(name, seqId) {
    try {
        var src = acSeq(seqId);
        if (!src) return acErr(acT("coreNoSeq", "Buka sequence dulu."));
        var want = acUniqueSeqName(name || (src.name + AC_SEQ_SUFFIX));
        var c = tlCloneSequence(src, want, null);
        if (!c) return acErr(acT("coreCloneFail", "Gagal menggandakan sequence."));
        acMarkMade(c);
        return acOk({ ok: true, id: c.sequenceID, name: c.name, origId: src.sequenceID, origName: src.name });
    } catch (e) { return acCatch(e, "bac_cloneSeq"); }
}

// Extract merged ranges [from, to) of `ranges` (sequence seconds) from sequence `seqId` (it becomes active).
// The panel calls this in chunks, LAST chunk first, so earlier positions stay valid.
function bac_removeRanges(seqId, ranges, from, to) {
    try {
        var seq = tlSeqById(String(seqId));
        if (!seq) return acErr(acT("coreResultGone", "Sequence hasil tidak ditemukan."));
        var opts = {};
        if (from !== undefined && from !== null) opts.from = Number(from);
        if (to !== undefined && to !== null) opts.to = Number(to);
        var r = tlRemoveRanges(seq, acRanges(ranges), opts);
        r.ok = true;
        return acOk(r);
    } catch (e) { return acCatch(e, "bac_removeRanges"); }
}

// All-in-one remove (SPEC section 4). opts: {seqId, mode: "auto"|"extract"|"xml", maxExtract: 150, xmlPath}.
// <= maxExtract merged ranges (or mode "extract"): clone + QE extract, returns {mode:"extract", id, name, ...}.
// More (or mode "xml"): exports FCP XML of the source to opts.xmlPath and returns {mode:"xml", xmlPath, lost}
// so the panel can run the engine "xmeml"/"cut" action and then bac_importXmlAndOpen. When the export reports
// lost effects in "auto" mode it falls back to clone + extract (slower, lossless).
function bac_applyRemove(ranges, name, opts) {
    try {
        opts = opts || {};
        var src = acSeq(opts.seqId);
        if (!src) return acErr(acT("coreNoSeq", "Buka sequence dulu."));
        var list = acRanges(ranges);
        var merged = tlMergeRanges(list, tlFps(src)).length;
        var mode = opts.mode || "auto", max = opts.maxExtract || 150, lost = [];
        var want = acUniqueSeqName(name || (src.name + AC_SEQ_SUFFIX));
        if (mode === "xml" || (mode === "auto" && merged > max)) {
            if (!opts.xmlPath) return acErr(acT("coreNoXmlPath", "Lokasi XML belum diisi."));
            var f = new File(opts.xmlPath);
            if (!f.parent.exists) f.parent.create();
            var ex = tlExportXml(src, opts.xmlPath);
            lost = ex.lost;
            if (ex.ok && (!lost.length || mode === "xml")) {
                return acOk({ ok: true, mode: "xml", xmlPath: ex.path, lost: lost, ranges: merged,
                              origId: src.sequenceID, origName: src.name, name: want, ms: ex.ms });
            }
        }
        var c = tlCloneSequence(src, want, null);
        if (!c) return acErr(acT("coreCloneFail", "Gagal menggandakan sequence."));
        acMarkMade(c);
        var r = tlRemoveRanges(c, list, {});
        return acOk({ ok: true, mode: "extract", id: c.sequenceID, name: c.name, origId: src.sequenceID,
                      origName: src.name, ranges: r.ranges, done: r.done, ms: r.ms, endSec: r.endSec, lost: lost });
    } catch (e) { return acCatch(e, "bac_applyRemove"); }
}

// Export FCP7 XML of a sequence (default active). Returns {ok, path, lost: [lines], ms}.
function bac_exportXml(path, seqId) {
    try {
        var seq = acSeq(seqId);
        if (!seq) return acErr(acT("coreNoSeq", "Buka sequence dulu."));
        var f = new File(path);
        if (!f.parent.exists) f.parent.create();
        return acOk(tlExportXml(seq, path));
    } catch (e) { return acCatch(e, "bac_exportXml"); }
}

// Import an xmeml we generated and open the new sequence. Picks the new sequence named `name` when given
// (nested sequences inside the XML import as extra sequences). Returns {id, name, created: n}.
function bac_importXmlAndOpen(path, name) {
    try {
        if (!path || !new File(path).exists) return acErr(acT("coreXmlMissing", "File XML tidak ditemukan: {0}", path));
        if (!/\.xml$/i.test(String(path))) return acErr(acT("coreXmlOnly", "Hanya file .xml yang boleh diimpor."));
        // Same naming as bac_cloneSeq: a second XML-route cut must not become another "<x> (Klipora)"
        // (verified live 26.2.2: the import kept the XML's name; Sequence.name is writable).
        var want = name ? acUniqueSeqName(String(name)) : "";
        var made = tlImportXml(path, null);
        if (!made.length) return acErr(acT("coreXmlNoSeq", "XML diimpor tapi sequence baru tidak ketemu."));
        var pick = made[0];
        for (var i = 0; i < made.length; i++) {
            acMarkMade(made[i]);
            if (name && made[i].name === name) pick = made[i];
        }
        if (want && pick.name !== want) pick.name = want;
        app.project.openSequence(pick.sequenceID);
        return acOk({ ok: true, id: pick.sequenceID, name: pick.name, created: made.length });
    } catch (e) { return acCatch(e, "bac_importXmlAndOpen"); }
}

// Delete a sequence that bac_* created in THIS Premiere session (anything else is refused).
// Verified live on 26.2.2 (seam check 2026-10-05): deletes the sequence and closes its timeline tab.
function bac_deleteSequence(id) {
    try {
        if (!acWasMade(String(id))) return acErr(acT("coreDeleteOnlyOurs", "Hanya sequence buatan Klipora di sesi ini yang boleh dihapus."));
        var s = tlSeqById(String(id));
        if (!s) return acErr(acT("coreSeqNotFound", "Sequence tidak ditemukan."));
        var ok = app.project.deleteSequence(s);
        return acOk({ ok: !!ok });
    } catch (e) { return acCatch(e, "bac_deleteSequence"); }
}

// Composited program frame as PNG (QE exportFramePNG; caption tracks are NOT drawn). `path` without or with
// ".png" (Premiere appends it). The sequence becomes active (QE limitation).
function bac_exportFrame(sec, path, seqId) {
    try {
        var seq = acSeq(seqId);
        if (!seq) return acErr(acT("coreNoSeq", "Buka sequence dulu."));
        var f = new File(String(path).replace(/\.png$/i, ""));
        if (!f.parent.exists) f.parent.create();
        return acOk(tlExportFrame(seq, Number(sec), f.fsName));
    } catch (e) { return acCatch(e, "bac_exportFrame"); }
}

// ---------- events ----------
// Premiere events -> CSXSEvent `type` (default "com.klipora.ev"), data "<eventName>|<activeSeqId>".
function bac_bindEvents(type) {
    try { return acOk({ ok: true, n: tlBindEvents(type || AC_EVENT_TYPE) }); }
    catch (e) { return acCatch(e, "bac_bindEvents"); }
}

function bac_unbindEvents() {
    try { return acOk({ ok: true, n: tlUnbindEvents() }); }
    catch (e) { return acCatch(e, "bac_unbindEvents"); }
}
