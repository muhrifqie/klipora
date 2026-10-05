// 36_viral.jsx: Viral Clips host functions (tool "viral"). ES3 only.
// One sub-sequence per selected clip via Sequence.createSubsequence(ignoreTrackTargeting) between temporary In/Out
// points on the SOURCE sequence: every track, effect, caption and keyframe inside the range is copied into a new
// sequence; the source's content is never changed and its own In/Out are restored right after (unset = -400000,
// docs/research/premiere_timeline_api.md section 0 item 6). New sequences go to the bin "Klipora Viral" (tlFindBin
// also finds the old "AutoCut Viral") and are registered with acMarkMade so "Delete result" (bac_viral_cleanup) may
// delete them, and only them. Markers are added by the panel with bac_addMarkers (tag "[Klipora-VR]", colour by
// score) AFTER the sub-sequences, so they are not copied into the clips. "Play" seeks and starts playback through QE (UNVERIFIED in 26.2.2:
// falls back to a plain seek and the panel tells the user to press Space).

var AC_VR_BIN = "Klipora Viral";

function acVrSeq(seqId) {
    if (seqId) return tlSeqById(String(seqId));
    return app.project ? app.project.activeSequence : null;
}

function acVrMissing(seqId) {
    return seqId ? acErr(acT("viralSeqGone", "Sequence sumber klip tidak ditemukan lagi. Cari klip ulang di sequence yang aktif."))
                 : acErr(acT("viralNoSeq", "Buka sequence dulu."));
}

// Re-activate `seq` in the timeline panel when another sequence is active (createSubsequence may open the new one).
function acVrActivate(seq) {
    var a = app.project.activeSequence;
    if (!a || a.sequenceID !== seq.sequenceID) app.project.openSequence(seq.sequenceID);
}

// In/Out back to what the user had: seconds as returned by getInPoint()/getOutPoint() ("-400000" = unset clears it).
// The In point goes to 0 first so the Out point can always be set.
function acVrRestoreInOut(seq, oldIn, oldOut) {
    try { seq.setInPoint("0"); } catch (e0) {}
    try { seq.setOutPoint(Number(oldOut)); } catch (e1) {}
    try { seq.setInPoint(Number(oldIn)); } catch (e2) {}
}

// Create one sub-sequence of [t0, t1) (sequence seconds, snapped outwards to frames) of `seqId` (default: active),
// named `name` (made unique), moved into bin `binName`. Returns {ok, id, name, origId, origName, t0, t1, dur, ms}.
function bac_viral_subseq(seqId, t0, t1, name, binName) {
    try {
        var seq = acVrSeq(seqId);
        if (!seq) return acVrMissing(seqId);
        if (typeof seq.createSubsequence !== "function") return acErr(acT("viralNoSubseqApi", "Versi Premiere ini belum mendukung sub-sequence dari script."));
        var started = new Date().getTime();
        acVrActivate(seq);
        var tpf = Number(seq.timebase), fps = tlFps(seq);
        var endF = Math.round(Number(seq.end) / tpf);
        // Times arrive frame-snapped but rounded to 6 decimals (343 frames -> 2.858333 s -> 342.99996 frames),
        // so the tolerance must be far above 1e-6 frames or floor/ceil add a frame on each side.
        var f0 = Math.max(0, Math.floor(Number(t0) * fps + 0.01));
        var f1 = Math.min(endF, Math.ceil(Number(t1) * fps - 0.01));
        if (!(f1 > f0)) return acErr(acT("viralEmptyRange", "Rentang klip kosong atau di luar sequence."));
        var oldIn = String(seq.getInPoint()), oldOut = String(seq.getOutPoint());
        var before = tlSeqIdSet(), sub = null, fail = null;
        try {
            seq.setInPoint("0");                       // ticks; In <= any Out, then Out, then the real In
            seq.setOutPoint(String(f1 * tpf));
            seq.setInPoint(String(f0 * tpf));
            sub = seq.createSubsequence(true);         // true = ignore track targeting: every track is copied
        } catch (e1) { fail = e1; }
        acVrRestoreInOut(seq, oldIn, oldOut);
        if (fail) throw fail;
        if (!sub || !sub.sequenceID) {
            var made = tlNewSeqsSince(before);
            sub = made.length ? made[0] : null;
        }
        if (!sub) return acErr(acT("viralNoSub", "Premiere tidak membuat sub-sequence untuk klip ini."));
        acMarkMade(sub);
        sub.name = acUniqueSeqName(name || (seq.name + " (Viral)"));
        // createSubsequence keeps the source timecode as start time (ruler at 08:13:021); a Shorts clip starts at 0.
        // Only the displayed timecode changes: clips, markers and captions stay put (verified live 26.2.2).
        try { if (Number(sub.zeroPoint) !== 0) sub.setZeroPoint("0"); } catch (e4) {}
        if (binName) {
            try {
                var bin = tlFindBin(binName, false);
                if (!bin) { bin = tlFindBin(binName, true); $.global.__acVrBin = binName; }
                if (bin) sub.projectItem.moveBin(bin);
            } catch (e3) {}
        }
        acVrActivate(seq);
        return acOk({ ok: true, id: sub.sequenceID, name: sub.name, origId: seq.sequenceID, origName: seq.name,
                      t0: f0 / fps, t1: f1 / fps, dur: Number(sub.end) / TL_TICKS, ms: new Date().getTime() - started });
    } catch (e) { return acCatch(e, "bac_viral_subseq"); }
}

// Seek to t0 (frame-aligned) and try to start playback (only when the sequence is the active one).
// Returns {ok, t, played, how}. played=false -> the panel asks the user to press Space.
function bac_viral_play(t0, seqId) {
    try {
        var seq = acVrSeq(seqId);
        if (!seq) return acVrMissing(seqId);
        var tpf = Number(seq.timebase), f = Math.max(0, Math.round(Number(t0) * tlFps(seq)));
        seq.setPlayerPosition(String(f * tpf));
        var how = "", act = app.project.activeSequence;
        if (act && act.sequenceID === seq.sequenceID) {
            app.enableQE();
            try {
                var q = qe.project.getActiveSequence();
                if (q && q.player && q.player.play) { q.player.play(1); how = "qe.player"; }
            } catch (e1) { how = ""; }
            if (!how) {
                try { if (qe.startPlayback) { qe.startPlayback(); how = "qe"; } } catch (e2) { how = ""; }
            }
        }
        return acOk({ ok: true, t: seq.getPlayerPosition().seconds, played: how !== "", how: how });
    } catch (e) { return acCatch(e, "bac_viral_play"); }
}

// Stop playback started by bac_viral_play. Returns {ok, stopped, how}.
function bac_viral_stop() {
    try {
        app.enableQE();
        var how = "";
        try {
            var q = qe.project.getActiveSequence();
            if (q && q.player && q.player.stop) { q.player.stop(); how = "qe.player"; }
        } catch (e1) { how = ""; }
        if (!how) {
            try { if (qe.stopPlayback) { qe.stopPlayback(); how = "qe"; } } catch (e2) { how = ""; }
        }
        return acOk({ ok: true, stopped: how !== "", how: how });
    } catch (e) { return acCatch(e, "bac_viral_stop"); }
}

// "Delete result": delete the sub-sequences this panel made in this Premiere session (others are refused) and the
// bin when this session created it and it is now empty. Returns {ok, deleted, refused, bin}.
function bac_viral_cleanup(ids, binName) {
    try {
        var deleted = 0, refused = 0, i, list = (ids instanceof Array) ? ids : [];
        for (i = 0; i < list.length; i++) {
            var id = String(list[i]);
            if (!acWasMade(id)) { refused++; continue; }
            var s = tlSeqById(id);
            if (s && app.project.deleteSequence(s)) deleted++;
        }
        var binGone = false;
        if (binName && $.global.__acVrBin === binName) {
            var bin = tlFindBin(binName, false);
            if (bin && bin.children.numItems === 0) { bin.deleteBin(); binGone = true; $.global.__acVrBin = null; }
        }
        return acOk({ ok: true, deleted: deleted, refused: refused, bin: binGone });
    } catch (e) { return acCatch(e, "bac_viral_cleanup"); }
}

// Root bin lookup for the panel: {ok, exists, n}.
function bac_viral_binInfo(name) {
    try {
        var bin = tlFindBin(String(name), false);
        return acOk({ ok: true, exists: !!bin, n: bin ? bin.children.numItems : 0 });
    } catch (e) { return acCatch(e, "bac_viral_binInfo"); }
}

// Move a sequence made for a clip (the 9:16 Auto Reframe version) into `binName` next to the clips. `dropBin` names a
// root bin that did not exist before this run (Premiere's "Auto Reframed Sequences"): it is deleted once empty.
// Only sequences made by bac_* in this session are moved. Returns {ok, moved, dropped}.
function bac_viral_adopt(seqId, binName, dropBin) {
    try {
        var s = tlSeqById(String(seqId)), moved = false, dropped = false;
        if (!s) return acErr(acT("viralSeqMissing", "Sequence tidak ditemukan."));
        if (!acWasMade(s.sequenceID)) return acErr(acT("viralNotOurs", "Sequence ini bukan buatan Klipora."));
        var bin = binName ? tlFindBin(String(binName), false) : null;
        if (bin) { s.projectItem.moveBin(bin); moved = true; }
        if (dropBin) {
            var rb = tlFindBin(String(dropBin), false);
            if (rb && rb.children.numItems === 0) { rb.deleteBin(); dropped = true; }
        }
        return acOk({ ok: true, moved: moved, dropped: dropped });
    } catch (e) { return acCatch(e, "bac_viral_adopt"); }
}
