// 42_caption_sfx.jsx: Auto Caption sound effects (ES3). Places the engine's sfx_render result on the audio track
// named "Klipora SFX" (or the old "AutoCut SFX") of the caption's sequence (created when missing, replaced on re-run). Never touches other
// tracks, never saves, imports only engine-made .wav files after File.exists (tlImportFile).
//   bac_csfx_status(seqId)                   {hasTrack, track, clips, ours, foreign, locked, path, start, end}
//   bac_csfx_apply(path, start, seqId)       mixed WAV (plan kind "audio_track"): our old clips out, new one in
//   bac_csfx_clips(items, first, seqId)      per-hit WAVs (plan kind "audio_clips"), chunked; first=true clears
//   bac_csfx_clear(seqId)                    remove our clips from the track (the empty track stays)
// Our clips = media named <seq>_sfx_v<n>.wav or sfx_<sound>_<gain>.wav (engine/ac/captions/sfx.py).

var AC_SFX_TRACK = "Klipora SFX";          // acNameIs also finds the old "AutoCut SFX" track
var AC_SFX_BIN = "Klipora Captions";       // tlFindBin also finds the old "AutoCut Captions" bin

function acSfxOurs(path) {
    var p = String(path || "");
    return /_sfx_v\d+\.wav$/i.test(p) || /[\\\/]sfx_[a-z0-9_]+\.wav$/i.test(p);
}

function acSfxLocked(track) {
    try { return (typeof track.isLocked === "function") ? !!track.isLocked() : false; } catch (e) { return false; }
}

// By name first. Fallback (audio track naming is UNVERIFIED in 26.2.2): the last audio track that holds only our
// clips, so a failed rename does not stack a new unnamed track (and a second copy of the mix) on every run.
function acSfxFind(seq) {
    var i;
    for (i = 0; i < seq.audioTracks.numTracks; i++) {
        if (acNameIs(seq.audioTracks[i].name, AC_SFX_TRACK)) return { track: seq.audioTracks[i], index: i };
    }
    for (i = seq.audioTracks.numTracks - 1; i >= 0; i--) {
        var tr = seq.audioTracks[i], n = tr.clips.numItems, all = n > 0;
        for (var k = 0; k < n && all; k++) if (!acSfxOurs(acSfxClipPath(tr.clips[k]))) all = false;
        if (all) return { track: tr, index: i, byContent: true };
    }
    return null;
}

// Append an audio track and name it (Track.name may be read-only: QE setName fallback). QE needs the sequence
// active, so it is opened first (switches the Program monitor to it).
function acSfxEnsure(seq) {
    var hit = acSfxFind(seq);
    if (hit) return hit;
    var a = app.project.activeSequence;
    if (!a || a.sequenceID !== seq.sequenceID) app.project.openSequence(seq.sequenceID);
    var tr = tlAddAudioTrack(seq);
    if (!tr) return null;
    var idx = seq.audioTracks.numTracks - 1;
    try { seq.audioTracks[idx].name = AC_SFX_TRACK; } catch (e1) { /* read-only */ }
    if (String(seq.audioTracks[idx].name) !== AC_SFX_TRACK) {
        try {
            app.enableQE();
            var qt = qe.project.getActiveSequence().getAudioTrackAt(idx);
            if (qt && typeof qt.setName === "function") qt.setName(AC_SFX_TRACK);
        } catch (e2) { /* QE without setName */ }
    }
    return { track: seq.audioTracks[idx], index: idx, named: String(seq.audioTracks[idx].name) === AC_SFX_TRACK };
}

function acSfxClipPath(c) {
    try { return c.projectItem ? String(c.projectItem.getMediaPath() || "") : ""; } catch (e) { return ""; }
}

// Remove our clips (from the end, no ripple). Returns {removed, foreign: [[start, end]]}.
function acSfxClearOurs(track) {
    var removed = 0, foreign = [];
    for (var i = track.clips.numItems - 1; i >= 0; i--) {
        var c = track.clips[i];
        if (acSfxOurs(acSfxClipPath(c))) { c.remove(false, true); removed++; }
        else foreign.push([c.start.seconds, c.end.seconds]);
    }
    return { removed: removed, foreign: foreign };
}

// Time ranges [[start, end]] of clips on the track that are not ours. Read-only.
function acSfxForeign(track) {
    var foreign = [];
    for (var i = 0; i < track.clips.numItems; i++) {
        var c = track.clips[i];
        if (!acSfxOurs(acSfxClipPath(c))) foreign.push([c.start.seconds, c.end.seconds]);
    }
    return foreign;
}

function acSfxDur(item, fallback) {
    var d = 0;
    try { d = item.getOutPoint().seconds - item.getInPoint().seconds; } catch (e) { d = 0; }
    return d > 0 ? d : fallback;
}

function acSfxOverlap(foreign, a, b) {
    for (var i = 0; i < foreign.length; i++) if (foreign[i][0] < b && foreign[i][1] > a) return true;
    return false;
}

function bac_csfx_status(seqId) {
    try {
        var seq = acSeq(seqId);
        if (!seq) return acErr(acT("csfxNoSeq", "Buka sequence dulu."));
        var out = { hasTrack: false, track: -1, clips: 0, ours: 0, foreign: 0, locked: false, path: null, start: null, end: null };
        var hit = acSfxFind(seq);
        if (hit) {
            out.hasTrack = true; out.track = hit.index; out.locked = acSfxLocked(hit.track);
            out.clips = hit.track.clips.numItems;
            for (var i = 0; i < out.clips; i++) {
                var c = hit.track.clips[i], p = acSfxClipPath(c);
                if (acSfxOurs(p)) {
                    out.ours++;
                    if (out.start === null || c.start.seconds < out.start) out.start = c.start.seconds;
                    if (out.end === null || c.end.seconds > out.end) out.end = c.end.seconds;
                    if (/_sfx_v\d+\.wav$/i.test(p)) out.path = p;
                } else out.foreign++;
            }
        }
        return acOk(out);
    } catch (e) { return acCatch(e, "bac_csfx_status"); }
}

function bac_csfx_apply(path, start, seqId) {
    try {
        var seq = acSeq(seqId);
        if (!seq) return acErr(acT("csfxNoSeq", "Buka sequence dulu."));
        path = String(path || "");
        if (!/\.wav$/i.test(path) || !acSfxOurs(path)) return acErr(acT("csfxOnlyOurs", "Hanya file .wav efek suara buatan Klipora yang boleh diimpor."));
        if (!new File(path).exists) return acErr(acT("csfxNoFile", "File efek suara tidak ditemukan: {0}", path));
        var t = acSfxEnsure(seq);
        if (!t) return acErr(acT("csfxAddFail", "Gagal menambah track audio \"{0}\".", AC_SFX_TRACK));
        if (acSfxLocked(t.track)) return acErr(acT("csfxLocked", "Track \"{0}\" terkunci. Buka kuncinya dulu.", String(t.track.name)));
        // Import and check overlap BEFORE removing anything: a refused apply keeps the placed SFX untouched.
        var item = tlImportFile(path, tlFindBin(AC_SFX_BIN, true));
        if (!item) return acErr(acT("csfxImportFail", "File efek suara gagal diimpor: {0}", path));
        var s = Number(start) || 0, dur = acSfxDur(item, 0.01);
        if (acSfxOverlap(acSfxForeign(t.track), s, s + dur)) {
            return acErr(acT("csfxOverlap", "Track \"{0}\" berisi clip lain di posisi itu. Pindahkan clip itu dulu.", String(t.track.name)));
        }
        var cl = acSfxClearOurs(t.track);
        t.track.overwriteClip(item, s);
        var placed = null;
        for (var i = 0; i < t.track.clips.numItems; i++) {
            var c = t.track.clips[i];
            if (Math.abs(c.start.seconds - s) < 0.05 && acSfxClipPath(c).toLowerCase() === path.toLowerCase()) placed = c;
        }
        return acOk({ ok: !!placed, mode: cl.removed ? "replace" : "place", track: t.index, removed: cl.removed,
                      start: placed ? placed.start.seconds : s, end: placed ? placed.end.seconds : null, path: path,
                      named: acNameIs(t.track.name, AC_SFX_TRACK) });
    } catch (e) { return acCatch(e, "bac_csfx_apply"); }
}

// items: [{t, path}] (one chunk). first: true clears our old clips before placing.
function bac_csfx_clips(items, first, seqId) {
    try {
        var seq = acSeq(seqId);
        if (!seq) return acErr(acT("csfxNoSeq", "Buka sequence dulu."));
        var t = acSfxEnsure(seq);
        if (!t) return acErr(acT("csfxAddFail", "Gagal menambah track audio \"{0}\".", AC_SFX_TRACK));
        if (acSfxLocked(t.track)) return acErr(acT("csfxLocked", "Track \"{0}\" terkunci. Buka kuncinya dulu.", String(t.track.name)));
        // Foreign ranges on every chunk (not only the first), read before anything is removed.
        var removed = 0, foreign = acSfxForeign(t.track);
        if (first === true || first === "true") removed = acSfxClearOurs(t.track).removed;
        var bin = tlFindBin(AC_SFX_BIN, true), cache = {}, placed = 0, skipped = 0;
        for (var i = 0; i < items.length; i++) {
            var p = String(items[i].path || "");
            if (!/\.wav$/i.test(p) || !acSfxOurs(p)) { skipped++; continue; }
            var item = cache[p];
            if (item === undefined) { item = tlImportFile(p, bin); cache[p] = item; }
            if (!item) { skipped++; continue; }
            if (acSfxOverlap(foreign, Number(items[i].t), Number(items[i].t) + acSfxDur(item, 0.05))) { skipped++; continue; }
            t.track.overwriteClip(item, Number(items[i].t));
            placed++;
        }
        return acOk({ ok: true, track: t.index, placed: placed, skipped: skipped, removed: removed });
    } catch (e) { return acCatch(e, "bac_csfx_clips"); }
}

function bac_csfx_clear(seqId) {
    try {
        var seq = acSeq(seqId);
        if (!seq) return acErr(acT("csfxNoSeq", "Buka sequence dulu."));
        var hit = acSfxFind(seq);
        if (!hit) return acOk({ ok: true, removed: 0 });
        if (acSfxLocked(hit.track)) return acErr(acT("csfxLocked", "Track \"{0}\" terkunci. Buka kuncinya dulu.", String(hit.track.name)));
        return acOk({ ok: true, removed: acSfxClearOurs(hit.track).removed });
    } catch (e) { return acCatch(e, "bac_csfx_clear"); }
}
