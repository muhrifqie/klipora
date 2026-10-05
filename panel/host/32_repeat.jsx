// 32_repeat.jsx: Potong Pengulangan host helpers. ExtendScript ES3 (no let/const, arrows, Array extras, JSON).
// Loaded by the panel after 00_util / 05_bridge / 10_timeline / 20_graphics, so it uses their lab-verified helpers:
// acSeq, acOk, acErr, acCatch (00_util), acMarkerList (05_bridge), tlAddMarker (10_timeline).

// Replace this tool's markers in ONE ExtendScript call (the panel's review "Kirim ke marker", the "Marker saja"
// result mode and the "Transkrip ngaco" flags).
//   list : [{t, end, name, comment, color 0..7}] in sequence seconds (or tab lines "t\tend\tname\tcomment")
//   tag  : e.g. "[Klipora-ULANG]"; every marker whose name or comment contains it (or the old "[AC-ULANG]"
//          spelling, acHasTag) is deleted first, so a re-run
//          replaces the old markers instead of piling them up. The tag is appended to each new comment.
//   seqId: target sequence id ("" = active sequence)
// Returns {ok, n (added), removed, seqId}. Never touches markers without the tag.
function bac_repeat_markers(list, tag, seqId) {
    try {
        var seq = acSeq(seqId);
        if (!seq) return acErr(acT("repeatNoSeq", "Buka sequence dulu."));
        if (!tag) return acErr(acT("repeatNoTag", "Tag marker kosong."));
        tag = String(tag);
        var removed = acRepeatClearTag(seq, tag);
        var items = (list instanceof Array) ? list : acMarkerList(list), n = 0;
        for (var i = 0; i < items.length; i++) {
            var m = items[i], t = Number(m.t);
            if (isNaN(t) || t < 0) continue;
            var end = Number(m.end), dur = (!isNaN(end) && end > t) ? end - t : 0;
            var comment = (m.comment !== undefined && m.comment !== null && String(m.comment) !== "") ? String(m.comment) + "\n" + tag : tag;
            tlAddMarker(seq, t, String(m.name || ""), comment, "Comment", acRepeatColor(m.color), dur);
            n++;
        }
        return acOk({ ok: true, n: n, removed: removed, seqId: seq.sequenceID });
    } catch (e) { return acCatch(e, "bac_repeat_markers"); }
}

// Delete the markers whose name or comment contains `tag` (old [AC-*] spelling too). Returns how many were deleted.
function acRepeatClearTag(seq, tag) {
    var mk = seq.markers, m = mk.getFirstMarker(), hits = [], guard = 0;
    while (m && guard++ < 100000) {
        if (acHasTag(m.name, tag) || acHasTag(m.comments, tag)) hits.push(m);
        m = mk.getNextMarker(m);
    }
    for (var i = 0; i < hits.length; i++) mk.deleteMarker(hits[i]);
    return hits.length;
}

// Marker colour index 0..7 (0 green, 1 red, 2 purple, 3 orange, 4 yellow, 5 white, 6 blue, 7 cyan); default orange.
function acRepeatColor(c) {
    var k = Number(c);
    if (c === undefined || c === null || isNaN(k) || k < 0 || k > 7) return 3;
    return Math.round(k);
}
