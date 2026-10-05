// 30_silence.jsx: Potong Silence host functions (ES3). Loaded after 00_util/05_bridge/10_timeline/20_graphics.
// Cutting (remove_ranges) and markers ([Klipora-SIL]) go through the foundation (AC.apply -> bac_cloneSeq /
// bac_removeRanges / bac_addMarkers). This file only adds the "Senyapkan jeda" mode: hold-key volume dips on the
// audio clips of a CLONE (the panel clones first with bac_cloneSeq; the original is never touched).
// Uses the lab-verified pattern of tlDuck (docs/research/premiere_timeline_api.md section 5): Volume component
// "Internal Volume Stereo" (fallback components[0]), Level = properties[1] (linear, 0 = -inf), keys in clip
// MEDIA time (tlSeqToMedia), interpolation TL_INTERP.HOLD. Never saves the project.

// Mute ranges[from, to) (sequence seconds, sorted) on the audio tracks `tracks` (indexes; empty = all) of
// sequence `seqId`. Locked tracks are skipped (reported by name). Returns {ok, keys, clips, ranges, skipped}.
// Call in chunks (the panel sends 40 ranges per call): each chunk reads the level left by the previous one.
function bac_silence_mute(seqId, ranges, tracks, from, to) {
    try {
        var seq = tlSeqById(String(seqId));
        if (!seq) return acErr(acT("silenceNoSeq", "Sequence hasil tidak ditemukan."));
        var list = acRanges(ranges), a0 = Number(from) || 0;
        var a1 = (to === undefined || to === null) ? list.length : Math.min(list.length, Number(to));
        var part = [], i, k;
        for (i = a0; i < a1; i++) if (list[i][1] > list[i][0]) part.push(list[i]);
        var want = acSilTrackSet(tracks, seq.audioTracks.numTracks);
        var fps = tlFps(seq), keys = 0, clips = 0, skipped = [];
        for (var t = 0; t < seq.audioTracks.numTracks; t++) {
            if (!want[t]) continue;
            var tr = seq.audioTracks[t];
            if (tr.isLocked()) { skipped.push(tr.name || ("A" + (t + 1))); continue; }
            for (i = 0; i < tr.clips.numItems; i++) {
                var c = tr.clips[i], cs = c.start.seconds, ce = c.end.seconds, hits = [];
                if (c.disabled) continue;
                for (k = 0; k < part.length; k++) {
                    var a = Math.max(part[k][0], cs), b = Math.min(part[k][1], ce);
                    if (b - a > 0.5 / fps) hits.push([a, b]);
                }
                if (!hits.length) continue;
                keys += acSilMuteClip(c, hits, fps);
                clips++;
            }
        }
        return acOk({ ok: true, keys: keys, clips: clips, ranges: part.length, skipped: skipped });
    } catch (e) { return acCatch(e, "bac_silence_mute"); }
}

// {index: true} for the wanted audio tracks (empty/missing list = every track).
function acSilTrackSet(tracks, n) {
    var set = {}, any = false, i;
    if (tracks instanceof Array) {
        for (i = 0; i < tracks.length; i++) {
            var v = Number(tracks[i]);
            if (!isNaN(v) && v >= 0 && v < n) { set[v] = true; any = true; }
        }
    }
    if (!any) for (i = 0; i < n; i++) set[i] = true;
    return set;
}

// Level value at media time mt (keys of earlier chunks/user keys respected); static value when not keyed.
function acSilLevelAt(lvl, mt) {
    if (!lvl.isTimeVarying()) return lvl.getValue();
    try { return lvl.getValueAtTime(mt); } catch (e) { return lvl.getValue(); }
}

// Hold keys on one audio TrackItem: level unchanged before each hit, 0 (-inf) inside, restored at its end.
// hits: sorted [[a, b]] sequence seconds inside the clip. Returns the number of keys written.
function acSilMuteClip(clip, hits, fps) {
    var vol = tlComp(clip, "Internal Volume Stereo") || clip.components[0];
    var lvl = vol.properties[1];                         // [0] = Bypass, [1] = Level
    var cs = clip.start.seconds, ce = clip.end.seconds, f = 1 / fps, i, k;
    var pts = [];                                        // [seqSec, value] in time order
    for (i = 0; i < hits.length; i++) {
        var a = hits[i][0], b = hits[i][1];
        var before = acSilLevelAt(lvl, tlSeqToMedia(clip, Math.max(cs, a - f)));
        var after = acSilLevelAt(lvl, tlSeqToMedia(clip, Math.min(b, ce)));
        var last = pts.length ? pts[pts.length - 1][0] : -1;
        if (a - f > cs + 0.5 * f && a - f > last + 0.5 * f) pts.push([a - f, before]);
        pts.push([a, 0]);
        if (b < ce - 0.5 * f) pts.push([b, after]);
    }
    if (!lvl.isTimeVarying()) lvl.setTimeVarying(true);
    for (k = 0; k < pts.length; k++) {
        var mt = tlSeqToMedia(clip, pts[k][0]);
        lvl.addKey(mt);
        lvl.setValueAtKey(mt, pts[k][1], false);
        lvl.setInterpolationTypeAtKey(mt, TL_INTERP.HOLD, k === pts.length - 1);
    }
    return pts.length;
}
