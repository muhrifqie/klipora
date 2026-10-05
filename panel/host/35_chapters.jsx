// 35_chapters.jsx: Auto Chapters host functions (tool "chapters"). ES3 only.
// Chapter markers are tagged "[Klipora-CH]" in the comment so a re-run replaces exactly the markers this tool made
// (non-destructive: other markers are never touched). Markers of older versions tagged "[AC-CH]" are recognised
// too (acHasTag / acTagAlts): they are read, replaced and cleared like the new ones. Built on the lab-verified helpers in 10_timeline.jsx:
// tlAddMarker (createMarker + name/comments + end + setTypeAsChapter + setColorByIndex, about 10 ms per marker,
// docs/research/premiere_timeline_api.md section 3). Colour 7 = cyan.

var AC_CH_TAG = "[Klipora-CH]";
var AC_CH_COLOR = 7;

// Sequence by id (must exist) or the active one. Returns null when missing.
function acChSeq(seqId) {
    if (seqId) return tlSeqById(String(seqId));
    return app.project ? app.project.activeSequence : null;
}

function acChIs(m) {
    return acHasTag(m.comments, AC_CH_TAG) || acHasTag(m.name, AC_CH_TAG);
}

// Text without any of our chapter tags (new or old), trimmed.
function acChStrip(s) {
    var alts = acTagAlts(AC_CH_TAG), i;
    s = String(s || "");
    for (i = 0; i < alts.length; i++) s = s.split(alts[i]).join("");
    return s.replace(/^\s+|\s+$/g, "");
}

// [{t, end, name, comment, type, color, guid}] of the tool's markers, sorted by start (comment without the tag).
function acChRead(seq) {
    var out = [], mk = seq.markers, m = mk.getFirstMarker(), guard = 0, i, j;
    while (m && guard++ < 100000) {
        if (acChIs(m)) {
            out.push({ t: m.start.seconds, end: m.end.seconds, name: acChStrip(m.name),
                       comment: acChStrip(m.comments), type: m.type,
                       color: m.getColorByIndex(), guid: m.guid });
        }
        m = mk.getNextMarker(m);
    }
    for (i = 1; i < out.length; i++) {           // insertion sort by t (ES3, small lists)
        var x = out[i];
        for (j = i - 1; j >= 0 && out[j].t > x.t; j--) out[j + 1] = out[j];
        out[j + 1] = x;
    }
    return out;
}

// Delete the tool's markers. Returns the number deleted.
function acChRemove(seq) {
    var mk = seq.markers, m = mk.getFirstMarker(), list = [], guard = 0, i;
    while (m && guard++ < 100000) {
        if (acChIs(m)) list.push(m);
        m = mk.getNextMarker(m);
    }
    for (i = 0; i < list.length; i++) mk.deleteMarker(list[i]);
    return list.length;
}

function acChMissing(seqId) {
    return seqId ? acErr(acT("chaptersSeqGone", "Sequence untuk bab ini tidak ditemukan lagi. Buat bab ulang di sequence yang aktif."))
                 : acErr(acT("chaptersNoSeq", "Buka sequence dulu."));
}

// Replace the chapter markers in ONE call: delete old [Klipora-CH] / [AC-CH] markers, add `list`
// ([{t, end, name, comment, color, type}] sequence seconds), read back. Returns
// {ok, id, name, removed, added, n (tagged markers now on the sequence), chapters (n of type Chapter)}.
function bac_chapters_apply(list, seqId) {
    try {
        var seq = acChSeq(seqId);
        if (!seq) return acChMissing(seqId);
        if (!(list instanceof Array)) return acErr(acT("chaptersBadList", "Daftar bab tidak valid."));
        var dur = Number(seq.end) / TL_TICKS;
        var removed = acChRemove(seq), added = 0, i;
        for (i = 0; i < list.length; i++) {
            var m = list[i], t = Number(m.t);
            if (isNaN(t) || t < 0 || t >= dur) continue;
            var end = Number(m.end);
            if (isNaN(end) || end > dur) end = dur;
            var comment = String(m.comment || "");
            comment = comment ? comment + "\n" + AC_CH_TAG : AC_CH_TAG;
            tlAddMarker(seq, t, String(m.name || ""), comment, m.type || "Chapter",
                        (m.color === undefined || m.color === null) ? AC_CH_COLOR : Number(m.color), end > t ? end - t : 0);
            added++;
        }
        var back = acChRead(seq), nCh = 0;
        for (i = 0; i < back.length; i++) if (back[i].type === "Chapter") nCh++;
        return acOk({ ok: true, id: seq.sequenceID, name: seq.name, removed: removed, added: added, n: back.length,
                      chapters: nCh });
    } catch (e) { return acCatch(e, "bac_chapters_apply"); }
}

// The tool's markers on a sequence (default active): {ok, id, name, duration, chapters: [...]}.
function bac_chapters_read(seqId) {
    try {
        var seq = acChSeq(seqId);
        if (!seq) return acChMissing(seqId);
        return acOk({ ok: true, id: seq.sequenceID, name: seq.name, duration: Number(seq.end) / TL_TICKS,
                      chapters: acChRead(seq) });
    } catch (e) { return acCatch(e, "bac_chapters_read"); }
}

// Remove the tool's markers (result card "Delete result"). Returns {ok, id, name, removed}.
function bac_chapters_clear(seqId) {
    try {
        var seq = acChSeq(seqId);
        if (!seq) return acChMissing(seqId);
        return acOk({ ok: true, id: seq.sequenceID, name: seq.name, removed: acChRemove(seq) });
    } catch (e) { return acCatch(e, "bac_chapters_clear"); }
}
