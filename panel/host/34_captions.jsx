// 34_captions.jsx: Auto Caption host side (ES3). Loaded after 00_util / 05_bridge / 10_timeline / 20_graphics.
// Only ever touches the video tracks named "Klipora Captions" (overlay) and "Klipora Captions (Editable)" (MOGRT),
// the bin "Klipora Captions" and the sequence's compositeLinearColor when the user flips it. Never saves.
// Projects from before the rename: the old "AutoCut Captions" / "AutoCut Captions (Editable)" tracks and bin are
// found (gxFindVideoTrack / tlFindBin via acNameAlts) and reused as they are; they are not renamed.
//   bac_captions_player(seqId)                              {id, t}: playhead (the panel polls it; no Premiere event)
//   bac_captions_status(seqId)                              what our tracks hold (+ their names: old projects keep
//                                                           "AutoCut Captions") + linear compositing state
//   bac_captions_applyOverlay(path, xNorm, yNorm, relink, seqId, trackName)  relink in place or place (replace)
//                                                           the overlay; trackName (engine plan.track) must be ours
//   bac_captions_applySrt(path, seqId)                      native caption track from our SRT
//   bac_captions_mogrtBegin(trackName, seqId)               ensure + clear the MOGRT track -> {track}
//   bac_captions_mogrtItems(items, trackIdx, seqId)         insert a chunk of MOGRT pages (pop-in + position)
//   bac_captions_clear(seqId, which)                        remove our clips ('overlay' | 'editable' | 'all')
//   bac_captions_setLinear(on, seqId)                       compositeLinearColor (only on user request)
// Verified helpers used (docs/research/premiere_graphics_api.md): gxPlaceOverlay, gxRelinkOverlay, gxFindVideoTrack,
// gxEnsureVideoTrack, gxClearTrack, gxGet/SetLinearCompositing, gxInsertMogrt, gxPopIn, gxCaptionTrackFromSrt.

var AC_CAP_BIN = "Klipora Captions";              // old projects: "AutoCut Captions" (tlFindBin finds it)
var AC_CAP_EDIT = "Klipora Captions (Editable)";   // old projects: "AutoCut Captions (Editable)" (gxFindVideoTrack)

function acCapFile(path) {
    var f = new File(String(path));
    return f.exists ? f : null;
}

function acCapLocked(track) {
    try { return (typeof track.isLocked === "function") ? !!track.isLocked() : false; } catch (e) { return false; }
}

function acCapName(path) { return String(path).replace(/^.*[\\\/]/, ""); }

// QE (used by gxEnsureVideoTrack to add + name the track) only works on the ACTIVE sequence: when the panel
// targets a locked, non-active sequence, open it first (this switches the Program monitor to it).
function acCapActivate(seq) {
    var a = app.project.activeSequence;
    if (!a || a.sequenceID !== seq.sequenceID) app.project.openSequence(seq.sequenceID);
}

function bac_captions_player(seqId) {
    try {
        var seq = acSeq(seqId);
        if (!seq) return acOk(null);
        return acOk({ id: seq.sequenceID, t: seq.getPlayerPosition().seconds });
    } catch (e) { return acCatch(e, "bac_captions_player"); }
}

function bac_captions_status(seqId) {
    try {
        var seq = acSeq(seqId);
        if (!seq) return acErr(acT("captionsNoSeq", "Buka sequence dulu."));
        var out = { hasTrack: false, track: -1, clips: 0, path: null, start: null, end: null, ours: false, locked: false,
                    linear: null, editable: { hasTrack: false, track: -1, clips: 0 } };
        var hit = gxFindVideoTrack(seq, GX_CAPTION_TRACK);
        if (hit) {
            out.hasTrack = true; out.track = hit.index; out.name = hit.track.name; out.clips = hit.track.clips.numItems; out.locked = acCapLocked(hit.track);
            if (out.clips > 0) {
                var c = hit.track.clips[0];
                out.start = c.start.seconds; out.end = c.end.seconds;
                try { out.path = c.projectItem ? c.projectItem.getMediaPath() : null; } catch (e1) { out.path = null; }
                out.ours = !!(out.path && /_captions_v\d+\.mov$/i.test(out.path));
            }
        }
        var ed = gxFindVideoTrack(seq, AC_CAP_EDIT);
        if (ed) { out.editable.hasTrack = true; out.editable.track = ed.index; out.editable.name = ed.track.name; out.editable.clips = ed.track.clips.numItems; }
        try { out.linear = gxGetLinearCompositing(seq) ? true : false; } catch (e2) { out.linear = null; }
        return acOk(out);
    } catch (e) { return acCatch(e, "bac_captions_status"); }
}

// relink: the engine says the band geometry and codec are unchanged -> swap the media of the existing clip.
// Otherwise (or when there is no clip to relink) place again: gxPlaceOverlay clears only our track.
function bac_captions_applyOverlay(path, xNorm, yNorm, relink, seqId, trackName) {
    try {
        var seq = acSeq(seqId);
        if (!seq) return acErr(acT("captionsNoSeq", "Buka sequence dulu."));
        if (trackName && !acNameIs(trackName, GX_CAPTION_TRACK)) return acErr(acT("captionsBadTrack", "Track caption tidak dikenal: {0}", trackName));
        if (!acCapFile(path)) return acErr(acT("captionsNoOverlay", "File overlay tidak ditemukan: {0}", path));
        if (!/\.mov$/i.test(String(path))) return acErr(acT("captionsOnlyMov", "Hanya file .mov buatan Klipora yang boleh diimpor."));
        var hit = gxFindVideoTrack(seq, GX_CAPTION_TRACK);
        if (hit && acCapLocked(hit.track)) return acErr(acT("captionsLocked", "Track \"{0}\" terkunci. Buka kuncinya dulu.", hit.track.name));
        if ((relink === true || relink === "true") && hit && hit.track.clips.numItems > 0) {
            var r = gxRelinkOverlay(seq, GX_CAPTION_TRACK, path);
            if (r.ok) {
                var clip = hit.track.clips[0];
                try { clip.projectItem.name = acCapName(path); } catch (e1) { /* name is cosmetic */ }
                try { clip.name = GX_CAPTION_TRACK; } catch (e2) { /* cosmetic */ }
                return acOk({ mode: "relink", track: hit.index, start: r.start, end: r.end, path: path });
            }
        }
        acCapActivate(seq);
        var bin = tlFindBin(AC_CAP_BIN, true);
        var p = gxPlaceOverlay(seq, path, { startSec: 0, xNorm: Number(xNorm), yNorm: Number(yNorm), scale: 100, bin: bin });
        if (!p.ok) return acErr(acT("captionsPlaceFailed", "Overlay gagal ditaruh: {0}", p.err || acT("captionsNoClip", "clip tidak muncul di track")));
        var t = gxFindVideoTrack(seq, GX_CAPTION_TRACK);
        if (t && t.track.clips.numItems > 0) { try { t.track.clips[0].name = GX_CAPTION_TRACK; } catch (e3) { /* cosmetic */ } }
        return acOk({ mode: "place", track: p.track, nodeId: p.nodeId, start: p.start, end: p.end, path: path });
    } catch (e) { return acCatch(e, "bac_captions_applyOverlay"); }
}

function bac_captions_applySrt(path, seqId) {
    try {
        var seq = acSeq(seqId);
        if (!seq) return acErr(acT("captionsNoSeq", "Buka sequence dulu."));
        if (!acCapFile(path)) return acErr(acT("captionsNoSrt", "File SRT tidak ditemukan: {0}", path));
        if (!/\.srt$/i.test(String(path))) return acErr(acT("captionsOnlySrt", "Hanya file .srt buatan Klipora yang boleh diimpor."));
        var ok = gxCaptionTrackFromSrt(seq, path, tlFindBin(AC_CAP_BIN, true));
        if (!ok) return acErr(acT("captionsSrtRefused", "Premiere menolak membuat track caption."));
        return acOk({ ok: true, path: path });
    } catch (e) { return acCatch(e, "bac_captions_applySrt"); }
}

function bac_captions_mogrtBegin(trackName, seqId) {
    try {
        var seq = acSeq(seqId);
        if (!seq) return acErr(acT("captionsNoSeq", "Buka sequence dulu."));
        acCapActivate(seq);
        var t = gxEnsureVideoTrack(seq, trackName || AC_CAP_EDIT);
        if (!t) return acErr(acT("captionsMogrtTrack", "Track untuk MOGRT tidak bisa dibuat."));
        if (acCapLocked(t.track)) return acErr(acT("captionsTrackLocked", "Track \"{0}\" terkunci.", t.track.name));
        var n = gxClearTrack(t.track);
        return acOk({ track: t.index, cleared: n });
    } catch (e) { return acCatch(e, "bac_captions_mogrtBegin"); }
}

// items: [{path, start, end, anchor: [x, y], position: [x, y], pop}] (engine plan.items); ~260 ms per item.
function bac_captions_mogrtItems(items, trackIdx, seqId) {
    try {
        var seq = acSeq(seqId);
        if (!seq) return acErr(acT("captionsNoSeq", "Buka sequence dulu."));
        var n = 0, failed = 0, i;
        for (i = 0; i < items.length; i++) {
            var it = items[i];
            if (!/\.mogrt$/i.test(String(it.path)) || !acCapFile(it.path)) { failed++; continue; }
            var c = gxInsertMogrt(seq, it.path, Number(it.start), Number(it.end), Number(trackIdx));
            if (!c) { failed++; continue; }
            try {
                if (it.pop) gxPopIn(c, { pivot: it.anchor });
                if (it.position) tlComp(c, "AE.ADBE Motion").properties[0].setValue(it.position, true);
            } catch (e1) { /* the text is placed; animation/position are best effort */ }
            n++;
        }
        return acOk({ n: n, failed: failed });
    } catch (e) { return acCatch(e, "bac_captions_mogrtItems"); }
}

function bac_captions_clear(seqId, which) {
    try {
        var seq = acSeq(seqId);
        if (!seq) return acErr(acT("captionsNoSeq", "Buka sequence dulu."));
        var n = 0, t;
        if (which !== "editable") { t = gxFindVideoTrack(seq, GX_CAPTION_TRACK); if (t && !acCapLocked(t.track)) n += gxClearTrack(t.track); }
        if (which !== "overlay") { t = gxFindVideoTrack(seq, AC_CAP_EDIT); if (t && !acCapLocked(t.track)) n += gxClearTrack(t.track); }
        return acOk({ ok: true, n: n });
    } catch (e) { return acCatch(e, "bac_captions_clear"); }
}

function bac_captions_setLinear(on, seqId) {
    try {
        var seq = acSeq(seqId);
        if (!seq) return acErr(acT("captionsNoSeq", "Buka sequence dulu."));
        gxSetLinearCompositing(seq, on === true || on === "true");
        return acOk({ ok: true, linear: gxGetLinearCompositing(seq) ? true : false });
    } catch (e) { return acCatch(e, "bac_captions_setLinear"); }
}
