// 41_broll.jsx: B-Roll host side (ES3). Places the engine's pre-rendered b-roll clips (mp4, no audio, exactly the
// sequence frame size, see engine/ac/tools/broll.py "apply") on a NEW top video track "Klipora B-Roll" of a CLONE
// of the source sequence. The original sequence is never touched (docs/SPEC.md rule 1).
// Uses the lab-verified helpers: tlCloneSequence, gxEnsureVideoTrack (QE addTracks + setName), gxClearTrack,
// gxImport (File.exists + extension check before importFiles), overwriteClip(item, ticks), clip.end = seconds,
// Motion [0] Position (normalised) / [1] Scale, Opacity [0] keys at clip MEDIA time (tlSeqToMedia).
// Panel flow: bac_broll_prepare(name) once, then bac_broll_place(seqId, items, opts) in chunks (progress).

// Premiere names (same in every UI language). gxFindVideoTrack / tlFindBin also find the old "AutoCut B-Roll".
var BAC_BROLL_TRACK = AC_BRAND + " B-Roll";
var BAC_BROLL_BIN = AC_BRAND + " B-Roll";

// Clone the active (or seqId) sequence as "<name>" (unique), open it and make sure it has an empty top video
// track "Klipora B-Roll" (re-running on an earlier b-roll result clears that track, or an old-named
// "AutoCut B-Roll" track, in the NEW clone only).
function bac_broll_prepare(name, seqId, trackName) {
    try {
        var src = acSeq(seqId);
        if (!src) return acErr(acT("brollOpenSeq", "Buka sequence dulu."));
        var want = acUniqueSeqName(name || (src.name + AC_SEQ_SUFFIX));
        var c = tlCloneSequence(src, want, null);
        if (!c) return acErr(acT("brollCloneFail", "Gagal menggandakan sequence."));
        acMarkMade(c);
        app.project.openSequence(c.sequenceID);
        var t = gxEnsureVideoTrack(c, trackName || BAC_BROLL_TRACK);
        if (!t) return acErr(acT("brollTrackFail", "Track B-roll gagal dibuat di sequence baru."));
        var cleared = gxClearTrack(t.track);
        return acOk({ ok: true, id: c.sequenceID, name: c.name, origId: src.sequenceID, origName: src.name,
                      track: t.index, tracks: c.videoTracks.numTracks, cleared: cleared });
    } catch (e) { return acCatch(e, "bac_broll_prepare"); }
}

// Place items [{id, path, t0, t1, place: "full"|"pip", scale, pos: [x, y], fade, label}] (sequence seconds) on
// the b-roll track of sequence seqId. opts: {track, bin}. Returns {placed: [{id, start, end}], failed: [{id, err}]}.
function bac_broll_place(seqId, items, opts) {
    try {
        opts = opts || {};
        var seq = tlSeqById(String(seqId));
        if (!seq) return acErr(acT("brollNoSeq", "Sequence B-roll tidak ditemukan. Mungkin sudah dihapus."));
        var t = gxFindVideoTrack(seq, opts.track || BAC_BROLL_TRACK);
        if (!t) return acErr(acT("brollNoTrack", "Track B-roll tidak ditemukan."));
        var bin = tlFindBin(opts.bin || BAC_BROLL_BIN, true);
        var fps = tlFps(seq), tpf = Number(seq.timebase), placed = [], failed = [];
        for (var i = 0; i < items.length; i++) {
            var it = items[i];
            try {
                var r = bacBrollPlaceOne(t.track, bin, it, fps, tpf);
                if (r.ok) placed.push(r); else failed.push({ id: it.id, err: r.err });
            } catch (e1) {
                failed.push({ id: it.id, err: (e1 && e1.message) ? e1.message : String(e1) });
            }
        }
        return acOk({ ok: true, placed: placed, failed: failed, track: t.index });
    } catch (e) { return acCatch(e, "bac_broll_place"); }
}

function bacBrollPlaceOne(track, bin, it, fps, tpf) {
    var path = String(it.path || "");
    if (!/\.mp4$/i.test(path)) return { ok: false, err: acT("brollNotMp4", "bukan file mp4 hasil render") };
    if (!new File(path).exists) return { ok: false, err: acT("brollNoFile", "file render tidak ada: {0}", path) };
    var item = gxImport(path, bin);
    if (!item) return { ok: false, err: acT("brollImportFail", "impor gagal") };
    var f0 = Math.round(Number(it.t0) * fps), f1 = Math.round(Number(it.t1) * fps);
    if (f1 <= f0) return { ok: false, err: acT("brollEmptyDur", "durasi kosong") };
    track.overwriteClip(item, String(f0 * tpf));
    var clip = bacBrollClipAt(track, f0 / fps, 0.5 / fps);
    if (!clip) return { ok: false, err: acT("brollClipLost", "clip tidak ketemu setelah ditaruh") };
    clip.end = f1 / fps;                                   // Number seconds (verified); shorter than the media
    if (it.place === "pip") {
        var mo = tlComp(clip, "AE.ADBE Motion");
        if (mo) {
            mo.properties[1].setValue(Number(it.scale) || 38, false);                 // Scale %
            if (it.pos && it.pos.length === 2) mo.properties[0].setValue([Number(it.pos[0]), Number(it.pos[1])], true);
        }
    }
    var fade = Number(it.fade) || 0;
    if (fade > 0) bacBrollFade(clip, fade, fps);
    try { clip.name = "B-roll " + String(it.label || it.id || ""); } catch (eName) { /* name is cosmetic */ }
    return { ok: true, id: it.id, start: clip.start.seconds, end: clip.end.seconds };
}

// TrackItem starting at `sec` (tolerance in seconds). QE items include gaps, the official API does not.
function bacBrollClipAt(track, sec, tol) {
    for (var i = 0; i < track.clips.numItems; i++) {
        var c = track.clips[i];
        if (Math.abs(c.start.seconds - sec) <= tol) return c;
    }
    return null;
}

// Crossfade = Opacity 0 -> 100 over `d` s after the start and 100 -> 0 before the end (last frame transparent).
function bacBrollFade(clip, d, fps) {
    var comp = tlComp(clip, "AE.ADBE Opacity");
    if (!comp) return false;
    var op = comp.properties[0];
    var st = clip.start.seconds, en = clip.end.seconds - 1 / fps;
    d = Math.min(d, (en - st) / 3);
    if (d <= 0) return false;
    if (!op.isTimeVarying()) op.setTimeVarying(true);
    var pts = [[st, 0], [st + d, 100], [en - d, 100], [en, 0]];
    for (var i = 0; i < pts.length; i++) {
        var mt = tlSeqToMedia(clip, pts[i][0]);
        op.addKey(mt);
        op.setValueAtKey(mt, pts[i][1], i === pts.length - 1);
    }
    return true;
}

// Folder picker fallback when cep.fs is not available (user-initiated modal; returns when the user closes it).
function bac_broll_pickFolder(start) {
    try {
        var f = start ? new Folder(start) : Folder.myDocuments;
        var pick = (f && f.exists) ? f.selectDlg(acT("brollPickFolder", "Pilih folder B-roll")) : Folder.selectDialog(acT("brollPickFolder", "Pilih folder B-roll"));
        return acOk({ ok: !!pick, path: pick ? pick.fsName : null });
    } catch (e) { return acCatch(e, "bac_broll_pickFolder"); }
}
