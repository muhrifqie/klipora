// 31_fillers.jsx: Hapus Filler host helpers. ES3 only (no let/const, arrow functions, JSON, Array extras,
// trailing commas). Uses the lab-verified tlAddMarker (10_timeline.jsx) and the bac_* conventions of 00_util.jsx.
// Never saves the project; only touches markers whose name or comment carries our tag (the old "[AC-FILLER]"
// spelling counts too: acHasTag).

// Replace the filler review markers on a sequence in ONE call: delete every marker tagged `tag`
// (default "[Klipora-FILLER]", old "[AC-FILLER]" markers included), then add one marker per item.
// list: [{t, end, name, comment, color 0..7}] (sequence seconds). Returns {ok, n, cleared}.
function bac_fillers_markers(list, tag, seqId) {
    try {
        var seq = acSeq(seqId);
        if (!seq) return acErr(acT("fillersNoSeq", "Buka sequence dulu."));
        tag = tag || "[Klipora-FILLER]";
        var cleared = acFillersClear(seq, tag);
        var n = 0, items = (list instanceof Array) ? list : [];
        for (var i = 0; i < items.length; i++) {
            var m = items[i], t = Number(m.t);
            if (isNaN(t) || t < 0) continue;
            var end = Number(m.end), dur = (!isNaN(end) && end > t) ? end - t : 0;
            var comment = (m.comment ? m.comment + "\n" : "") + tag;
            var color = (m.color === undefined || m.color === null) ? 3 : Number(m.color);
            tlAddMarker(seq, t, String(m.name || "Filler"), comment, "Comment", color, dur);
            n++;
        }
        return acOk({ ok: true, n: n, cleared: cleared });
    } catch (e) { return acCatch(e, "bac_fillers_markers"); }
}

// Delete the filler markers only. Returns {ok, n}.
function bac_fillers_clearMarkers(tag, seqId) {
    try {
        var seq = acSeq(seqId);
        if (!seq) return acErr(acT("fillersNoSeq", "Buka sequence dulu."));
        return acOk({ ok: true, n: acFillersClear(seq, tag || "[Klipora-FILLER]") });
    } catch (e) { return acCatch(e, "bac_fillers_clearMarkers"); }
}

function acFillersClear(seq, tag) {
    var mk = seq.markers, m = mk.getFirstMarker(), list = [], guard = 0;
    while (m && guard++ < 100000) {
        if (acHasTag(m.name, tag) || acHasTag(m.comments, tag)) list.push(m);
        m = mk.getNextMarker(m);
    }
    for (var i = 0; i < list.length; i++) mk.deleteMarker(list[i]);
    return list.length;
}
