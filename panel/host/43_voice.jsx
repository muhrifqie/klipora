// 43_voice.jsx: host side of "Suara Jernih" / "Clear Voice" (tool id voice). ES3 only.
// Applies the engine plan {kind: "voice"} (engine/ac/tools/voice.py) NON-DESTRUCTIVELY:
//   1. bac_voice_begin    clone the source sequence as "<name> (Klipora)" and prepare audio track "Klipora Suara"
//                         (a re-run on an earlier result reuses and clears that track, also the old "AutoCut Suara")
//   2. bac_voice_disable  disable the original voice clips (and their stereo twins) on the CLONE; linked video
//                         clips keep their state (restored when Premiere toggles them together)
//   3. bac_voice_place    import the engine-made WAV (File.exists first, .wav only) and overwrite it on the track
//   4. bac_voice_duck     volume keys on the music clips of the clone while the voice speaks (chunks of ranges)
// Helpers used (docs/research/premiere_timeline_api.md 5): Level is linear, 1 = +15 dB (tlDbToLevel), key times are
// clip MEDIA time (tlSeqToMedia), QE addTracks appends an audio track (tlAddAudioTrack), overwriteClip on an empty
// track never ripples. Never saves the project.

var VC_BIN = "Klipora";
var VC_EPS = 0.0005;

function vcLevel(clip) {
    var comp = null, i;
    for (i = 0; i < clip.components.numItems; i++) {
        if (String(clip.components[i].matchName).indexOf("Internal Volume") === 0) { comp = clip.components[i]; break; }
    }
    if (!comp) {
        for (i = 0; i < clip.components.numItems; i++) {
            if (clip.components[i].displayName === "Volume") { comp = clip.components[i]; break; }
        }
    }
    if (!comp) return null;
    return tlProp(comp, "Level") || (comp.properties.numItems > 1 ? comp.properties[1] : null);
}

function vcKeySec(k) { return (typeof k === "number") ? k : Number(k.seconds); }

// Rename audio track idx (Track.name may be read-only: QE setName fallback). UNVERIFIED on 26.2.2.
function vcNameTrack(seq, idx, name) {
    try { seq.audioTracks[idx].name = name; } catch (e1) { /* read-only */ }
    if (String(seq.audioTracks[idx].name) === name) return true;
    try {
        app.enableQE();
        var qt = qe.project.getActiveSequence().getAudioTrackAt(idx);
        if (qt && typeof qt.setName === "function") qt.setName(name);
    } catch (e2) { /* QE without setName */ }
    return String(seq.audioTracks[idx].name) === name;
}

// Clone + output track. Returns {ok, id, name, origId, origName, track, named, trackName, rerun, cleared}.
// On a failure after cloning the half-made clone is deleted again (it is ours).
function bac_voice_begin(srcId, name, trackName) {
    var c = null;
    try {
        var src = acSeq(srcId);
        if (!src) return acErr(acT("voiceSrcGone", "Sequence sumber tidak ditemukan. Buka lagi lalu ulangi."));
        c = tlCloneSequence(src, acUniqueSeqName(name || (src.name + AC_SEQ_SUFFIX)), null);
        if (!c) return acErr(acT("voiceCloneFail", "Gagal menggandakan sequence."));
        acMarkMade(c);
        app.project.openSequence(c.sequenceID);
        var idx = -1, cleared = 0, ti;
        for (ti = 0; ti < c.audioTracks.numTracks; ti++) {
            if (acNameIs(c.audioTracks[ti].name, trackName)) { idx = ti; break; }
        }
        var rerun = idx >= 0, named = idx >= 0;
        if (rerun) {
            var tr0 = c.audioTracks[idx];
            while (tr0.clips.numItems > 0) { tr0.clips[0].remove(false, true); cleared++; }
        } else {
            var tr = tlAddAudioTrack(c);
            if (!tr) { app.project.deleteSequence(c); return acErr(acT("voiceTrackFail", "Gagal menambah track audio untuk suara baru.")); }
            idx = c.audioTracks.numTracks - 1;
            named = vcNameTrack(c, idx, trackName);
        }
        return acOk({ ok: true, id: c.sequenceID, name: c.name, origId: src.sequenceID, origName: src.name,
                      track: idx, named: named, trackName: String(c.audioTracks[idx].name), rerun: rerun, cleared: cleared });
    } catch (e) {
        try { if (c) app.project.deleteSequence(c); } catch (e2) { /* already gone */ }
        return acCatch(e, "bac_voice_begin");
    }
}

// Linked video items of an audio TrackItem with their disabled flags (to undo a linked toggle).
function vcLinked(clip) {
    var out = [];
    try {
        var li = clip.getLinkedItems();
        if (!li) return out;
        for (var i = 0; i < li.numItems; i++) {
            var it = li[i];
            if (it && it.mediaType === "Video") out.push({ item: it, was: !!it.disabled });
        }
    } catch (e) { /* no link info */ }
    return out;
}

// clips: [{track, start, end}] (sequence seconds) -> the matching audio clips of seqId are disabled.
// When `disabled` cannot be set the clip is muted instead (static Level 0). Returns
// {ok, disabled, muted, missing: [i], locked: [track names], videoKept}.
function bac_voice_disable(seqId, clips) {
    try {
        var seq = tlSeqById(String(seqId));
        if (!seq) return acErr(acT("voiceNoResult", "Sequence hasil tidak ditemukan."));
        var f = 1 / tlFps(seq), out = { ok: true, disabled: 0, muted: 0, missing: [], locked: [], videoKept: 0 };
        for (var i = 0; i < clips.length; i++) {
            var want = clips[i], tr = seq.audioTracks[Number(want.track)], hit = null, k;
            if (!tr) { out.missing.push(i); continue; }
            if (tr.isLocked()) {
                var nm = String(tr.name || ("A" + (Number(want.track) + 1))), seen = false;
                for (k = 0; k < out.locked.length; k++) if (out.locked[k] === nm) seen = true;
                if (!seen) out.locked.push(nm);
                out.missing.push(i);
                continue;
            }
            for (k = 0; k < tr.clips.numItems; k++) {
                var c = tr.clips[k];
                if (Math.abs(c.start.seconds - Number(want.start)) < f && Math.abs(c.end.seconds - Number(want.end)) < f) { hit = c; break; }
            }
            if (!hit) { out.missing.push(i); continue; }
            var linked = vcLinked(hit), done = false;
            try { hit.disabled = true; done = !!hit.disabled; } catch (e1) { done = false; }
            if (done) {
                out.disabled++;
                for (k = 0; k < linked.length; k++) {
                    if (!!linked[k].item.disabled !== linked[k].was) {
                        try { linked[k].item.disabled = linked[k].was; out.videoKept++; } catch (e2) { /* keep going */ }
                    }
                }
            } else {
                var lvl = vcLevel(hit);
                if (lvl) {
                    if (lvl.isTimeVarying()) lvl.setTimeVarying(false);
                    lvl.setValue(0, true);
                    out.muted++;
                } else out.missing.push(i);
            }
        }
        return acOk(out);
    } catch (e) { return acCatch(e, "bac_voice_disable"); }
}

// Import the processed WAV (only an existing .wav we generated) into bin "Klipora" (old "AutoCut BOT" reused) and place it on audio track
// trackIdx at startSec. Returns {ok, start, end, name, ms}.
function bac_voice_place(seqId, trackIdx, wavPath, startSec) {
    try {
        var seq = tlSeqById(String(seqId));
        if (!seq) return acErr(acT("voiceNoResult", "Sequence hasil tidak ditemukan."));
        var tr = seq.audioTracks[Number(trackIdx)];
        if (!tr) return acErr(acT("voiceNoTrack", "Track suara baru tidak ditemukan."));
        var p = String(wavPath || "");
        if (!/\.wav$/i.test(p)) return acErr(acT("voiceNotWav", "File suara hasil bukan WAV."));
        if (!new File(p).exists) return acErr(acT("voiceWavMissing", "File suara hasil tidak ditemukan: {0}", p));
        var t0 = new Date().getTime(), bin = tlFindBin(VC_BIN, true);
        var item = tlImportFile(p, bin);
        if (!item) return acErr(acT("voiceImportFail", "File suara hasil gagal diimpor."));
        tr.overwriteClip(item, Number(startSec));
        var placed = null;
        for (var i = 0; i < tr.clips.numItems; i++) {
            var c = tr.clips[i];
            if (Math.abs(c.start.seconds - Number(startSec)) < 0.05) { placed = c; break; }
        }
        if (!placed) return acErr(acT("voiceNotPlaced", "File suara sudah diimpor tapi tidak muncul di track."));
        return acOk({ ok: true, start: placed.start.seconds, end: placed.end.seconds, name: placed.name,
                      ms: new Date().getTime() - t0 });
    } catch (e) { return acCatch(e, "bac_voice_place"); }
}

// Remove Level keys strictly inside (m0, m1) (media seconds).
function vcRemoveKeys(lvl, m0, m1) {
    var keys = lvl.getKeys(), n = 0;
    if (!keys) return 0;
    for (var i = keys.length - 1; i >= 0; i--) {
        var s = vcKeySec(keys[i]);
        if (s > m0 + 1e-6 && s < m1 - 1e-6) {
            try { lvl.removeKey(keys[i]); n++; } catch (e) { /* key vanished */ }
        }
    }
    return n;
}

// Level of the existing curve at sequence time t (static value when not keyed).
function vcBase(lvl, clip, t) {
    if (!lvl.isTimeVarying()) return Number(lvl.getValue());
    return Number(lvl.getValueAtTime(tlSeqToMedia(clip, t)));
}

// Duck one clip for the ranges (sequence seconds, sorted, gaps > attack + release): base -> base*factor over
// `attack` before each range, back to base over `release` after it. Old keys inside each window are removed first
// (re-runs and user keys there would bend the ramp). Returns keys written.
function vcDuckClip(clip, ranges, factor, attack, release, last) {
    var lvl = vcLevel(clip);
    if (!lvl) return 0;
    var s = clip.start.seconds, e = clip.end.seconds, pts = [], i;
    for (i = 0; i < ranges.length; i++) {
        var a = Number(ranges[i][0]), b = Number(ranges[i][1]);
        if (b + release <= s || a - attack >= e) continue;
        var pre = Math.max(s, a - attack), a2 = Math.max(s, a), b2 = Math.min(e, b), post = Math.min(e, b + release);
        if (lvl.isTimeVarying()) {
            vcRemoveKeys(lvl, tlSeqToMedia(clip, pre) - 1e-4, tlSeqToMedia(clip, post) + 1e-4);
            var left = lvl.getKeys();
            if (!left || !left.length) lvl.setTimeVarying(false);
        }
        if (a2 >= e - VC_EPS) continue;                    // only the attack ramp would touch this clip
        if (a - attack > s + VC_EPS) pts.push([pre, vcBase(lvl, clip, pre)]);
        pts.push([a2, vcBase(lvl, clip, a2) * factor]);
        if (b2 - a2 > VC_EPS) pts.push([b2, vcBase(lvl, clip, b2) * factor]);
        if (b + release < e - VC_EPS && post > b2 + VC_EPS) pts.push([post, vcBase(lvl, clip, post)]);
    }
    if (!pts.length) return 0;
    if (!lvl.isTimeVarying()) lvl.setTimeVarying(true);
    var n = 0;
    for (i = 0; i < pts.length; i++) {
        var mt = tlSeqToMedia(clip, pts[i][0]), ui = !!last && i === pts.length - 1;
        lvl.addKey(mt);
        lvl.setValueAtKey(mt, pts[i][1], ui);
        try { lvl.setInterpolationTypeAtKey(mt, TL_INTERP.LINEAR, ui); } catch (e1) { /* audio keys are linear */ }
        n++;
    }
    return n;
}

// On a re-run the clone's music clips still carry the previous duck curve: drop all their Level keys and go back
// to the highest keyed level (our duck curves only go down from the base). Returns clips reset.
function vcResetDuck(seq, tracks) {
    var n = 0;
    for (var t = 0; t < tracks.length; t++) {
        var tr = seq.audioTracks[Number(tracks[t])];
        if (!tr || tr.isLocked()) continue;
        for (var i = 0; i < tr.clips.numItems; i++) {
            var lvl = vcLevel(tr.clips[i]);
            if (!lvl || !lvl.isTimeVarying()) continue;
            var keys = lvl.getKeys(), top = 0, k;
            for (k = 0; keys && k < keys.length; k++) top = Math.max(top, Number(lvl.getValueAtKey(keys[k])));
            lvl.setTimeVarying(false);
            if (top > 0) lvl.setValue(top, false);
            n++;
        }
    }
    return n;
}

// tracks: audio track indexes of the music; ranges: [[a, b]] voice activity (sequence seconds).
// opts {db: -12, attack: 0.25, release: 0.6, reset: bool (first chunk of a re-run), last: bool}.
// Returns {ok, keys, clips, locked: [names], reset, ms}.
function bac_voice_duck(seqId, tracks, ranges, opts) {
    try {
        var seq = tlSeqById(String(seqId));
        if (!seq) return acErr(acT("voiceNoResult", "Sequence hasil tidak ditemukan."));
        opts = opts || {};
        var t0 = new Date().getTime(), f = 1 / tlFps(seq);
        var factor = Math.pow(10, (Number(opts.db) || -12) / 20);
        var attack = Math.max(Number(opts.attack) || 0.25, f), release = Math.max(Number(opts.release) || 0.6, f);
        var list = acRanges(ranges), keys = 0, nClips = 0, locked = [], reset = 0;
        if (opts.reset) reset = vcResetDuck(seq, tracks);
        for (var t = 0; t < tracks.length; t++) {
            var tr = seq.audioTracks[Number(tracks[t])];
            if (!tr) continue;
            if (tr.isLocked()) { if (tr.clips.numItems) locked.push(String(tr.name || ("A" + (Number(tracks[t]) + 1)))); continue; }
            for (var i = 0; i < tr.clips.numItems; i++) {
                var c = tr.clips[i];
                if (c.disabled) continue;
                var n = vcDuckClip(c, list, factor, attack, release, opts.last && t === tracks.length - 1 && i === tr.clips.numItems - 1);
                if (n > 0) { keys += n; nClips++; }
            }
        }
        return acOk({ ok: true, keys: keys, clips: nClips, locked: locked, reset: reset, ms: new Date().getTime() - t0 });
    } catch (e) { return acCatch(e, "bac_voice_duck"); }
}

// Read-back for the verifier: voice clips' disabled flags, clips on the output track, Level dB of music clips at
// the given times. Returns {ok, voice: [[track, start, end, disabled]], out: [[start, end, name]], levels}.
function bac_voice_probe(seqId, voiceTracks, outTrack, musicTracks, times) {
    try {
        var seq = acSeq(seqId);
        if (!seq) return acErr(acT("voiceNoSeq", "Sequence tidak ditemukan."));
        var voice = [], outc = [], levels = [], i, t, c;
        for (t = 0; t < (voiceTracks || []).length; t++) {
            var tr = seq.audioTracks[Number(voiceTracks[t])];
            if (!tr) continue;
            for (i = 0; i < tr.clips.numItems; i++) { c = tr.clips[i]; voice.push([Number(voiceTracks[t]), c.start.seconds, c.end.seconds, !!c.disabled]); }
        }
        var ot = seq.audioTracks[Number(outTrack)];
        if (ot) for (i = 0; i < ot.clips.numItems; i++) { c = ot.clips[i]; outc.push([c.start.seconds, c.end.seconds, c.name]); }
        for (var k = 0; k < (times || []).length; k++) {
            var tt = Number(times[k]), row = [];
            for (t = 0; t < (musicTracks || []).length; t++) {
                var mt = seq.audioTracks[Number(musicTracks[t])];
                if (!mt) continue;
                for (i = 0; i < mt.clips.numItems; i++) {
                    c = mt.clips[i];
                    if (c.start.seconds > tt || c.end.seconds <= tt) continue;
                    var lvl = vcLevel(c);
                    if (!lvl) continue;
                    var v = lvl.isTimeVarying() ? lvl.getValueAtTime(tlSeqToMedia(c, tt)) : lvl.getValue();
                    row.push(Number(v) > 0 ? Math.round(tlLevelToDb(Number(v)) * 10) / 10 : -999);
                }
            }
            levels.push({ t: tt, db: row });
        }
        return acOk({ ok: true, voice: voice, out: outc, levels: levels, nAudio: seq.audioTracks.numTracks });
    } catch (e) { return acCatch(e, "bac_voice_probe"); }
}
