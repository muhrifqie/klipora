// 38_resize.jsx: Auto Resize host side (ES3). Loaded after 00_util / 05_bridge / 10_timeline / 20_graphics.
// The ORIGINAL sequence is never modified: every function works on a sequence it creates (clone, import, native).
//   bac_resize_begin(name, w, h, seqId, inS, outS)   clone the source as a new sequence + set the frame size
//   bac_resize_keys(seqId, track, items, last)       Motion Scale + Position per clip (static or keyframed)
//   bac_resize_finish(seqId)                         open the new sequence, report what it holds
//   bac_resize_importRender(path, name)              import a rendered .mp4 (we made it) as a new sequence
//   bac_resize_native(num, den, preset, name, seqId) Premiere Auto Reframe -> new sequence (analysis runs in background)
//   bac_resize_nativeStatus(seqId)                   {done} when Premiere finished analysing
//   bac_resize_dropBin()                             "Delete result": drop Premiere's empty "Auto Reframed Sequences"
// Verified building blocks (docs/research/premiere_timeline_api.md): clone (2), Motion keys in clip MEDIA time with
// NORMALISED Position (4), autoReframeSequence (6), setSettings frame size keeps clips at 100 % (8),
// createNewSequenceFromClips (8). Never call app.project.save(); check File.exists before importing.

var AC_RESIZE_BIN = "Klipora";                    // tlFindBin also finds the old bin "AutoCut BOT"
var AC_RESIZE_ARBIN = "Auto Reframed Sequences";     // root bin Premiere's autoReframeSequence creates

// Find the clip of `track` that starts at `start` (sequence seconds); `index` is only a hint (QE gaps differ).
function acResizeClip(track, index, start, fps) {
    var half = 0.5 / (fps || 30), c;
    if (index >= 0 && index < track.clips.numItems) {
        c = track.clips[index];
        if (Math.abs(c.start.seconds - start) <= half) return c;
    }
    for (var i = 0; i < track.clips.numItems; i++) {
        c = track.clips[i];
        if (Math.abs(c.start.seconds - start) <= half) return c;
    }
    return null;
}

function bac_resize_begin(name, w, h, seqId, inS, outS) {
    try {
        var src = acSeq(seqId);
        if (!src) return acErr(acT("resizeOpenSeq", "Buka sequence dulu."));
        var want = acUniqueSeqName(name || (src.name + " (resize)"));
        var c = tlCloneSequence(src, want, null);              // re-opens the original afterwards
        if (!c) return acErr(acT("resizeCloneFail", "Gagal menggandakan sequence."));
        acMarkMade(c);
        app.project.openSequence(c.sequenceID);                 // settings are changed on the active (new) sequence
        var fr = tlSetFrame(c, Number(w), Number(h));
        if (inS !== undefined && inS !== null && outS !== undefined && outS !== null && Number(outS) > Number(inS)) {
            c.setInPoint(Number(inS));
            c.setOutPoint(Number(outS));
        }
        return acOk({ ok: true, id: c.sequenceID, name: c.name, origId: src.sequenceID, origName: src.name,
                      w: fr.w, h: fr.h, fps: fr.fps, frameOk: !!fr.ok });
    } catch (e) { return acCatch(e, "bac_resize_begin"); }
}

// items: [{index, start, scale, pos: [x, y]} | {index, start, scale, keys: [[seqSec, x, y], ...]}]
// Position is normalised to the frame (0.5, 0.5 = centred); keys are sequence seconds -> clip media time here.
function bac_resize_keys(seqId, track, items, last) {
    try {
        var seq = tlSeqById(String(seqId));
        if (!seq) return acErr(acT("resizeNoResult", "Sequence hasil tidak ditemukan."));
        var tr = seq.videoTracks[Number(track)];
        if (!tr) return acErr(acT("resizeNoTrack", "Track video {0} tidak ada.", Number(track) + 1));
        var fps = tlFps(seq), done = 0, keys = 0, missing = 0, replaced = 0, i, k;
        for (i = 0; i < items.length; i++) {
            var it = items[i];
            var clip = acResizeClip(tr, Number(it.index), Number(it.start), fps);
            if (!clip) { missing++; continue; }
            var m = tlComp(clip, "AE.ADBE Motion");
            if (!m) { missing++; continue; }
            var pos = m.properties[0], sc = m.properties[1];   // [0] Position [1] Scale (language independent)
            if (sc.isTimeVarying()) { sc.setTimeVarying(false); replaced++; }
            if (pos.isTimeVarying()) { pos.setTimeVarying(false); replaced++; }
            sc.setValue(Number(it.scale), false);
            if (it.keys && it.keys.length > 1) {
                pos.setTimeVarying(true);
                var endMedia = tlSeqToMedia(clip, clip.end.seconds);
                for (k = 0; k < it.keys.length; k++) {
                    var kk = it.keys[k], mt = tlSeqToMedia(clip, Number(kk[0]));
                    if (mt >= endMedia) continue;                // keys outside the clip silently do nothing
                    pos.addKey(mt);
                    pos.setValueAtKey(mt, [Number(kk[1]), Number(kk[2])], false);
                    pos.setInterpolationTypeAtKey(mt, TL_INTERP.LINEAR, false);
                    keys++;
                }
            } else {
                var p = it.pos || (it.keys && it.keys[0] ? [it.keys[0][1], it.keys[0][2]] : [0.5, 0.5]);
                pos.setValue([Number(p[0]), Number(p[1])], false);
            }
            done++;
        }
        if (last && done) {                                    // one UI refresh at the very end
            var lc = acResizeClip(tr, Number(items[items.length - 1].index), Number(items[items.length - 1].start), fps);
            if (lc) { var lm = tlComp(lc, "AE.ADBE Motion"); if (lm) lm.properties[1].setValue(lm.properties[1].getValue(), true); }
        }
        return acOk({ ok: true, done: done, keys: keys, missing: missing, replaced: replaced });
    } catch (e) { return acCatch(e, "bac_resize_keys"); }
}

function bac_resize_finish(seqId) {
    try {
        var seq = tlSeqById(String(seqId));
        if (!seq) return acErr(acT("resizeNoResult", "Sequence hasil tidak ditemukan."));
        app.project.openSequence(seq.sequenceID);
        return acOk({ ok: true, id: seq.sequenceID, name: seq.name, w: seq.frameSizeHorizontal, h: seq.frameSizeVertical,
                      duration: Number(seq.end) / TL_TICKS });
    } catch (e) { return acCatch(e, "bac_resize_finish"); }
}

// Import a rendered MP4 (engine output) into bin "Klipora" (or the old "AutoCut BOT") and make a sequence from it (inherits its size/fps).
function bac_resize_importRender(path, name) {
    try {
        if (!path || !/\.mp4$/i.test(String(path))) return acErr(acT("resizeOnlyMp4", "Hanya file .mp4 hasil render yang boleh diimpor."));
        var f = new File(String(path));
        if (!f.exists) return acErr(acT("resizeNoRenderFile", "File render tidak ditemukan: {0}", path));
        var bin = tlFindBin(AC_RESIZE_BIN, true);
        var item = tlImportFile(f.fsName, bin);
        if (!item) return acErr(acT("resizeImportFail", "Video hasil render gagal diimpor."));
        var before = tlSeqIdSet();
        var want = acUniqueSeqName(name || "Klipora resize");
        var seq = app.project.createNewSequenceFromClips(want, [item], bin);
        if (!seq || !seq.sequenceID) {
            var made = tlNewSeqsSince(before);
            seq = made.length ? made[0] : null;
        }
        if (!seq) return acErr(acT("resizeNoRenderSeq", "Sequence baru dari video render tidak terbuat."));
        acMarkMade(seq);
        app.project.openSequence(seq.sequenceID);
        return acOk({ ok: true, id: seq.sequenceID, name: seq.name, w: seq.frameSizeHorizontal, h: seq.frameSizeVertical,
                      fps: tlFps(seq), duration: Number(seq.end) / TL_TICKS, bin: AC_RESIZE_BIN });
    } catch (e) { return acCatch(e, "bac_resize_importRender"); }
}

// preset: "slower" | "default" | "faster". Result size keeps the source height (9:16 of 960 tall = 540x960).
function bac_resize_native(num, den, preset, name, seqId) {
    try {
        var src = acSeq(seqId);
        if (!src) return acErr(acT("resizeOpenSeq", "Buka sequence dulu."));
        var ok = { slower: 1, "default": 1, faster: 1 };
        if (!ok[preset]) preset = "default";
        var want = acUniqueSeqName(name || (src.name + " (Auto Reframe)"));
        var had = !!tlFindBin(AC_RESIZE_ARBIN, false);
        var r = tlAutoReframe(src, Number(num), Number(den), preset, want);
        if (!had && tlFindBin(AC_RESIZE_ARBIN, false)) $.global.__acRzArBin = true;   // ours: may be dropped once empty
        if (!r) return acErr(acT("resizeNativeFail", "Premiere tidak membuat sequence Auto Reframe."));
        acMarkMade(r);
        return acOk({ ok: true, id: r.sequenceID, name: r.name, w: r.frameSizeHorizontal, h: r.frameSizeVertical,
                      origId: src.sequenceID, origName: src.name, preset: preset });
    } catch (e) { return acCatch(e, "bac_resize_native"); }
}

function bac_resize_nativeStatus(seqId) {
    try {
        var s = tlSeqById(String(seqId));
        if (!s) return acErr(acT("resizeNativeMissing", "Sequence Auto Reframe tidak ditemukan."));
        return acOk({ ok: true, done: !!tlReframeDone(s) });
    } catch (e) { return acCatch(e, "bac_resize_nativeStatus"); }
}

// After "Delete result" of a native result: delete the root bin "Auto Reframed Sequences" only when one of our runs
// created it (this Premiere session) and it is empty now. Returns {ok, bin: deleted?}.
function bac_resize_dropBin() {
    try {
        var gone = false;
        if ($.global.__acRzArBin) {
            var b = tlFindBin(AC_RESIZE_ARBIN, false);
            if (!b) $.global.__acRzArBin = false;
            else if (b.children.numItems === 0) { b.deleteBin(); gone = true; $.global.__acRzArBin = false; }
        }
        return acOk({ ok: true, bin: gone });
    } catch (e) { return acCatch(e, "bac_resize_dropBin"); }
}
