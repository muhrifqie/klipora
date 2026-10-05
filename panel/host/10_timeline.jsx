// 10_timeline.jsx: verified ES3 timeline helpers for Premiere Pro 26.2.2 (CEP 12), ported UNCHANGED from
// proto/lab/timeline_helpers.jsx (see docs/research/premiere_timeline_api.md). TL_TICKS, tlJSON, tlPad, tlFps,
// bins/import and sequence-id helpers moved to 00_util.jsx (bodies unchanged). Loaded after 00_util.jsx.
// Conventions: seconds are Numbers; "ticks" are Strings (254016000000 ticks = 1 s); functions return plain values;
// helpers never call app.project.save().

// ---------- sequence info (item 9) ----------
function tlSeqInfo(seq, withClips) {
    var st = seq.getSettings();
    var o = {
        name: seq.name, id: seq.sequenceID, fps: tlFps(seq), ticksPerFrame: seq.timebase,
        width: seq.frameSizeHorizontal, height: seq.frameSizeVertical,
        displayFormat: st.videoDisplayFormat,                 // 998=120fps "HH:MM:SS:FFF", 104=30, 103/102=29.97 NDF/DF ...
        endSec: Number(seq.end) / TL_TICKS,
        playerSec: seq.getPlayerPosition().seconds,
        inSec: Number(seq.getInPoint()), outSec: Number(seq.getOutPoint()), // -400000 = not set
        nVideo: seq.videoTracks.numTracks, nAudio: seq.audioTracks.numTracks,
        markers: seq.markers.numMarkers
    };
    if (withClips) {
        o.video = tlTracksInfo(seq.videoTracks);
        o.audio = tlTracksInfo(seq.audioTracks);
        o.selection = [];
        var sel = seq.getSelection();
        for (var i = 0; i < sel.length; i++) {
            o.selection.push({ name: sel[i].name, mediaType: sel[i].mediaType, track: sel[i].parentTrackIndex, start: sel[i].start.seconds });
        }
    }
    return o;
}

function tlTracksInfo(tracks) {
    var out = [];
    for (var t = 0; t < tracks.numTracks; t++) {
        var tr = tracks[t];
        var tt = { idx: t, name: tr.name, muted: tr.isMuted(), locked: tr.isLocked(), targeted: tr.isTargeted(), clips: [] };
        for (var i = 0; i < tr.clips.numItems; i++) {
            var c = tr.clips[i], pi = c.projectItem, li = c.getLinkedItems();
            tt.clips.push({
                name: c.name, start: c.start.seconds, end: c.end.seconds, inPoint: c.inPoint.seconds,
                outPoint: c.outPoint.seconds, speed: c.getSpeed(), disabled: c.disabled, selected: c.isSelected(),
                isMGT: c.isMGT(), media: pi ? pi.getMediaPath() : null, linked: li ? li.numItems : 0
            });
        }
        out.push(tt);
    }
    return out;
}

// ---------- timecode (QE needs timecode strings) ----------
// Frame index -> timecode string in the sequence's display format. Verified identical to qe CTI.timecode
// for 120p(998), 30p(104), 29.97 NDF(103)/DF(102), 59.94 NDF(107)/DF(106), 23.976(110).
function tlTimecode(seq, frame) {
    var nominal = Math.round(tlFps(seq));
    var disp = seq.getSettings().videoDisplayFormat;
    var drop = (disp === 102 || disp === 106);
    var f = Math.round(frame), sep = ":";
    if (drop) {
        var d = Math.round(nominal / 15);
        var perMin = nominal * 60 - d, per10 = perMin * 10 + d;
        var tens = Math.floor(f / per10), rem = f % per10;
        f = f + 9 * d * tens + (rem > d ? d * Math.floor((rem - d) / perMin) : 0);
        sep = ";";
    }
    var sec = Math.floor(f / nominal);
    return tlPad(Math.floor(sec / 3600), 2) + sep + tlPad(Math.floor(sec / 60) % 60, 2) + sep +
           tlPad(sec % 60, 2) + sep + tlPad(f % nominal, nominal >= 100 ? 3 : 2);
}

function tlSecToFrame(seq, sec) { return Math.round(sec * tlFps(seq)); }

// ---------- clone (item 2) ----------
// seq.clone() returns Boolean true (not the Sequence), names the copy "<name> Copy", puts it in the ROOT bin
// and makes it the ACTIVE sequence. This wrapper finds it, renames, moves it, and re-activates the original.
function tlCloneSequence(seq, newName, bin) {
    var before = tlSeqIdSet();
    if (!seq.clone()) return null;
    var made = tlNewSeqsSince(before);
    if (!made.length) return null;
    var c = made[0];
    if (newName) c.name = newName;
    if (bin) c.projectItem.moveBin(bin);
    app.project.openSequence(seq.sequenceID);
    return c;
}

// ---------- track states ----------
function tlTrackStates(seq) {
    app.enableQE();
    var q = qe.project.getActiveSequence();       // seq must be active
    var st = { v: [], a: [] };
    for (var i = 0; i < seq.videoTracks.numTracks; i++) {
        var qv = q.getVideoTrackAt(i);
        st.v.push({ locked: qv.isLocked(), sync: qv.isSyncLocked(), targeted: seq.videoTracks[i].isTargeted() });
    }
    for (i = 0; i < seq.audioTracks.numTracks; i++) {
        var qa = q.getAudioTrackAt(i);
        st.a.push({ locked: qa.isLocked(), sync: qa.isSyncLocked(), targeted: seq.audioTracks[i].isTargeted() });
    }
    return st;
}

function tlApplyTrackStates(seq, st) {
    // Only touch what differs: every setter call is an undo-stack entry.
    app.enableQE();
    var q = qe.project.getActiveSequence();
    var groups = [[st.v, seq.videoTracks, "getVideoTrackAt"], [st.a, seq.audioTracks, "getAudioTrackAt"]];
    for (var g = 0; g < 2; g++) {
        var want = groups[g][0], tracks = groups[g][1];
        for (var i = 0; i < want.length && i < tracks.numTracks; i++) {
            var qt = q[groups[g][2]](i);
            if (qt.isSyncLocked() !== want[i].sync) qt.setSyncLock(want[i].sync);
            if (qt.isLocked() !== want[i].locked) qt.setLock(want[i].locked);
            if (tracks[i].isTargeted() !== want[i].targeted) tracks[i].setTargeted(want[i].targeted, true);
        }
    }
}

// All tracks unlocked + sync-locked so Extract ripples every track (a track is skipped by Extract when it is
// locked, or when it is BOTH untargeted and not sync-locked -> desync).
function tlPrepareAllTracks(seq) {
    app.enableQE();
    var q = qe.project.getActiveSequence();
    for (var i = 0; i < seq.videoTracks.numTracks; i++) {
        var qv = q.getVideoTrackAt(i);
        if (qv.isLocked()) qv.setLock(false);
        if (!qv.isSyncLocked()) qv.setSyncLock(true);
    }
    for (i = 0; i < seq.audioTracks.numTracks; i++) {
        var qa = q.getAudioTrackAt(i);
        if (qa.isLocked()) qa.setLock(false);
        if (!qa.isSyncLocked()) qa.setSyncLock(true);
    }
}

// ---------- remove time ranges in place (item 1b) ----------
// ranges: [[startSec, endSec], ...] in SEQUENCE time, end exclusive. Sorted+merged here.
// Uses QE Extract (ripple delete across all tracks, shifts sequence markers, keeps every effect/keyframe/MOGRT).
// Measured: ~70-110 ms per range (50 ranges ~4.7 s, 300 ranges ~32 s on a 34-min 120fps sequence).
// For progress UI call it in chunks: tlRemoveRanges(seq, ranges, {from: i0, to: i1}) processing the LAST ranges
// first (earlier positions stay valid). Each Extract = 3 undo-stack entries (1 visible Ctrl+Z).
function tlMergeRanges(ranges, fps) {
    var fr = [];
    for (var i = 0; i < ranges.length; i++) {
        var a = Math.round(ranges[i][0] * fps), b = Math.round(ranges[i][1] * fps);
        if (b > a) fr.push([a, b]);
    }
    fr.sort(function (x, y) { return x[0] - y[0]; });
    var out = [];
    for (i = 0; i < fr.length; i++) {
        if (out.length && fr[i][0] <= out[out.length - 1][1]) {
            if (fr[i][1] > out[out.length - 1][1]) out[out.length - 1][1] = fr[i][1];
        } else out.push([fr[i][0], fr[i][1]]);
    }
    return out; // frames
}

function tlRemoveRanges(seq, ranges, opts) {
    opts = opts || {};
    app.project.openSequence(seq.sequenceID);     // QE works on the active sequence only
    app.enableQE();
    var q = qe.project.getActiveSequence();
    var fr = tlMergeRanges(ranges, tlFps(seq));
    var lastFrame = Math.round(Number(seq.end) / Number(seq.timebase));
    var states = tlTrackStates(seq);
    // GOTCHA: Extract clears the sequence In/Out points and moves the CTI -> save and restore (ripple-mapped).
    var tpf = Number(seq.timebase);
    var inS = Number(seq.getInPoint()), outS = Number(seq.getOutPoint());
    var ctiF = Math.round(Number(seq.getPlayerPosition().ticks) / tpf);
    tlPrepareAllTracks(seq);
    var t0 = new Date().getTime(), done = 0;
    var from = opts.from || 0, to = (opts.to === undefined) ? fr.length : opts.to;
    var mapF = function (f) {               // frame -> frame after removing fr[from..to)
        var shift = 0;
        for (var k = from; k < to; k++) {
            if (f >= fr[k][1]) shift += fr[k][1] - fr[k][0];
            else if (f > fr[k][0]) { shift += f - fr[k][0]; break; }
            else break;
        }
        return f - shift;
    };
    try {
        for (var i = to - 1; i >= from; i--) {
            var a = fr[i][0], b = Math.min(fr[i][1], lastFrame);
            if (b <= a) continue;
            q.extract(tlTimecode(seq, a), tlTimecode(seq, b));
            done++;
        }
    } finally {
        tlApplyTrackStates(seq, states);
        if (inS > -1000) seq.setInPoint(String(mapF(Math.round(inS * tlFps(seq))) * tpf));
        if (outS > -1000) seq.setOutPoint(String(mapF(Math.round(outS * tlFps(seq))) * tpf));
        seq.setPlayerPosition(String(mapF(ctiF) * tpf));
    }
    return { ranges: fr.length, done: done, ms: new Date().getTime() - t0, endSec: Number(seq.end) / TL_TICKS };
}

// ---------- FCP XML (item 1a, 8) ----------
// Export also writes "FCP Translation Results YYYY-MM-DD HH-MM.txt" next to the XML whenever something could
// not be translated (one "Translation issue:" + "\tSequence <..> at , video track 1: Effect <X> on Clip <Y>
// not translated." per item). `lost` = those lines, minus the always-present harmless
// "Internal Channel Volume Stereo". lost.length > 0  =>  the XML round-trip would drop effects -> use tlRemoveRanges.
function tlExportXml(seq, pathWin) {
    var t0 = new Date().getTime();
    var f = new File(pathWin);
    var ok = seq.exportAsFinalCutProXML(f.fsName, 1);
    var ms = new Date().getTime() - t0;
    var lost = [];
    var reports = f.parent.getFiles("FCP Translation Results*.txt");
    for (var i = 0; i < reports.length; i++) {
        if (reports[i].modified.getTime() < t0 - 1000) continue;
        reports[i].open("r");
        var lines = reports[i].read().split(/\r?\n/);
        reports[i].close();
        for (var k = 0; k < lines.length; k++) {
            var ln = lines[k].replace(/^\s+|\s+$/g, "");
            if (ln.indexOf("not translated") < 0 || ln.indexOf("Internal Channel Volume") >= 0) continue;
            lost.push(ln);
        }
    }
    return { ok: ok, ms: ms, path: f.fsName, lost: lost };
}

// Imports an xmeml into bin and returns the NEW sequences (diff by sequenceID; order of app.project.sequences
// is not creation order). GOTCHA: nested sequences inside the XML are imported as duplicate sequences.
function tlImportXml(path, bin) {
    var before = tlSeqIdSet();
    if (!new File(path).exists) return [];
    app.project.importFiles([path], true, bin || app.project.getInsertionBin(), false);
    return tlNewSeqsSince(before);
}

// ---------- markers (item 3) ----------
// colorIdx 0..7 only (0 green,1 red,2 purple,3 orange,4 yellow,5 white,6 blue,7 cyan); >7 silently becomes 0.
function tlAddMarker(seq, sec, name, comments, type, colorIdx, durSec) {
    var m = seq.markers.createMarker(sec);
    m.name = name || "";
    m.comments = comments || "";
    if (durSec) m.end = sec + durSec;
    if (type === "Chapter") m.setTypeAsChapter();
    else if (type === "Segmentation") m.setTypeAsSegmentation();
    else if (type === "WebLink") m.setTypeAsWebLink(comments || "", "");
    else m.setTypeAsComment();
    if (colorIdx !== undefined && colorIdx !== null) m.setColorByIndex(colorIdx);
    return m;
}

function tlReadMarkers(seq) {
    var out = [], mk = seq.markers, m = mk.getFirstMarker(), guard = 0;
    while (m && guard++ < 100000) {
        out.push({ name: m.name, comments: m.comments, start: m.start.seconds, end: m.end.seconds, type: m.type,
                   color: m.getColorByIndex(), guid: m.guid });
        m = mk.getNextMarker(m);
    }
    return out;
}

function tlClearMarkers(seq, onlyName) {
    var mk = seq.markers, m = mk.getFirstMarker(), n = 0, list = [];
    while (m) { if (!onlyName || m.name === onlyName) list.push(m); m = mk.getNextMarker(m); }
    for (var i = 0; i < list.length; i++) { mk.deleteMarker(list[i]); n++; }
    return n;
}

// ---------- components / keyframes (items 4, 5) ----------
function tlComp(clip, matchName) {
    for (var i = 0; i < clip.components.numItems; i++) if (clip.components[i].matchName === matchName) return clip.components[i];
    return null;
}
function tlProp(comp, displayName) {
    for (var i = 0; i < comp.properties.numItems; i++) if (comp.properties[i].displayName === displayName) return comp.properties[i];
    return null;
}

// Keyframe times are CLIP MEDIA time (source time), not sequence time: media = inPoint + (seq - start) * speed.
function tlSeqToMedia(clip, seqSec) { return clip.inPoint.seconds + (seqSec - clip.start.seconds) * (clip.getSpeed() || 1); }

// Interpolation codes measured on 26.2 (setInterpolationTypeAtKey on both keys of a 100->200 ramp):
// 0 = linear, 4 = hold, 3 = smooth ease-in/out (S-curve: 25%->14.9%), 2 = ease-out of first key only,
// 1 = fast-start curve, 5/6 = linear-looking (bezier with default handles). Names beyond 0/4 UNVERIFIED.
var TL_INTERP = { LINEAR: 0, HOLD: 4, EASE: 3 };

// keys: [{t: seqSec, scale: 120, pos: [nx, ny]}]  (Position is NORMALIZED 0..1 of the frame in 26.x;
// pixels = n * frameSize). interp: TL_INTERP.*. ~25 ms per 100 keys with updateUI=false.
function tlZoomKeys(clip, keys, interp) {
    var m = tlComp(clip, "AE.ADBE Motion");
    var sc = m.properties[1], pos = m.properties[0];   // [0]=Position [1]=Scale (language independent)
    var useScale = false, usePos = false, i;
    for (i = 0; i < keys.length; i++) { if (keys[i].scale !== undefined) useScale = true; if (keys[i].pos) usePos = true; }
    if (useScale && !sc.isTimeVarying()) sc.setTimeVarying(true);
    if (usePos && !pos.isTimeVarying()) pos.setTimeVarying(true);
    for (i = 0; i < keys.length; i++) {
        var mt = tlSeqToMedia(clip, keys[i].t), last = (i === keys.length - 1);
        if (keys[i].scale !== undefined) {
            sc.addKey(mt); sc.setValueAtKey(mt, keys[i].scale, false);
            if (interp !== undefined) sc.setInterpolationTypeAtKey(mt, interp, last);
        }
        if (keys[i].pos) {
            pos.addKey(mt); pos.setValueAtKey(mt, keys[i].pos, false);
            if (interp !== undefined) pos.setInterpolationTypeAtKey(mt, interp, last);
        }
    }
    return { scaleKeys: useScale ? sc.getKeys().length : 0, posKeys: usePos ? pos.getKeys().length : 0 };
}

function tlClearKeys(prop) { prop.setTimeVarying(false); }   // drops all keys, keeps current static value

// Motion Position that puts the normalized clip point (px,py) at the frame centre at scalePct, clamped so the
// scaled clip still covers the whole frame (no black edges). Position = where the clip's centre (anchor 0.5,0.5)
// lands, normalized to the frame. Assumes clip pixel size == sequence frame size.
function tlZoomPos(px, py, scalePct) {
    var s = scalePct / 100;
    var x = 0.5 - (px - 0.5) * s, y = 0.5 - (py - 0.5) * s;
    if (s >= 1) {
        var lo = 1 - s / 2, hi = s / 2;
        x = Math.min(hi, Math.max(lo, x)); y = Math.min(hi, Math.max(lo, y));
    }
    return [x, y];
}

// Audio "Level" param is linear 0..1 where 1 = +15 dB:  level = 10^((dB-15)/20); 0 dB = 0.17782794; 0 = -inf.
function tlDbToLevel(db) { return db <= -200 ? 0 : Math.pow(10, (db - 15) / 20); }
function tlLevelToDb(v) { return v <= 0 ? -Infinity : 20 * Math.log(v) / Math.LN10 + 15; }

// Mute/duck [a,b) (sequence seconds) on an audio TrackItem with hold keys. db=-Infinity mutes.
function tlDuck(audioClip, a, b, db, baseDb) {
    var vol = tlComp(audioClip, "Internal Volume Stereo") || audioClip.components[0];
    var lvl = vol.properties[1];                       // [0]=Bypass [1]=Level
    if (baseDb === undefined) baseDb = tlLevelToDb(lvl.getValue());
    if (!lvl.isTimeVarying()) lvl.setTimeVarying(true);
    var pts = [[a - 0.001, baseDb], [a, db], [b, baseDb]];
    for (var i = 0; i < pts.length; i++) {
        var mt = tlSeqToMedia(audioClip, pts[i][0]);
        lvl.addKey(mt);
        lvl.setValueAtKey(mt, (pts[i][1] === -Infinity) ? 0 : tlDbToLevel(pts[i][1]), false);
        lvl.setInterpolationTypeAtKey(mt, TL_INTERP.HOLD, i === pts.length - 1);
    }
    return lvl.getKeys().length;
}

// ---------- tracks & clips (item 5) ----------
// QE addTracks(nVideo, afterVideoIdx, nAudio, audioType, afterAudioIdx, nSubmix, submixType, afterSubmixIdx).
// Verified: addTracks(0,0,1,1,numAudio,0,0,0) appends one audio track. Returns the new audio Track.
function tlAddAudioTrack(seq) {
    app.project.openSequence(seq.sequenceID);
    app.enableQE();
    var n = seq.audioTracks.numTracks;
    qe.project.getActiveSequence().addTracks(0, 0, 1, 1, n, 0, 0, 0);
    return seq.audioTracks.numTracks > n ? seq.audioTracks[seq.audioTracks.numTracks - 1] : null;
}

function tlAddVideoTrack(seq) {
    app.project.openSequence(seq.sequenceID);
    app.enableQE();
    var n = seq.videoTracks.numTracks;
    qe.project.getActiveSequence().addTracks(1, n, 0, 0, 0, 0, 0, 0);   // verified: appends one video track
    return seq.videoTracks.numTracks > n ? seq.videoTracks[seq.videoTracks.numTracks - 1] : null;
}

// Place projectItem on a track at seqSec, optionally only [inSec,outSec) of the source.
// overwriteClip on a VIDEO track also lays down the linked audio on the matching audio track (and vice versa).
function tlPlaceClip(track, projectItem, seqSec, inSec, outSec) {
    if (inSec !== undefined) { projectItem.setInPoint(inSec, 4); projectItem.setOutPoint(outSec, 4); } // 4 = all media
    var ok = track.overwriteClip(projectItem, seqSec);
    if (inSec !== undefined) { projectItem.clearInPoint(); projectItem.clearOutPoint(); }
    return ok;
}

// ---------- frame export (item 7) ----------
// Renders the COMPOSITED program frame (all tracks, effects, Motion, graphics; caption tracks NOT drawn).
// Synchronous: file exists when it returns (~90-250 ms at 2292x960). Premiere ALWAYS appends ".png".
// Path must be a Windows backslash path (forward slashes -> "Unknown error exception").
function tlExportFrame(seq, sec, pathNoExt) {
    app.project.openSequence(seq.sequenceID);
    app.enableQE();
    var q = qe.project.getActiveSequence();
    var tc = tlTimecode(seq, tlSecToFrame(seq, sec));
    var p = new File(pathNoExt).fsName.replace(/\.png$/i, "");
    var ok = q.exportFramePNG(tc, p);
    return { ok: ok, tc: tc, path: p + ".png", exists: new File(p + ".png").exists };
}

// ---------- sequence settings (item 8) ----------
// fps optional (e.g. 30 or 30000/1001). Also sets preview size to match (otherwise previews keep the old size).
function tlSetFrame(seq, w, h, fps) {
    var st = seq.getSettings();
    st.videoFrameWidth = w; st.videoFrameHeight = h;
    st.previewFrameWidth = w; st.previewFrameHeight = h;
    if (fps) { var t = new Time(); t.ticks = String(Math.round(TL_TICKS / fps)); st.videoFrameRate = t; }
    var ok = seq.setSettings(st);
    return { ok: ok, w: seq.frameSizeHorizontal, h: seq.frameSizeVertical, fps: tlFps(seq) };
}

// ---------- auto reframe (item 6) ----------
// Returns the new Sequence immediately (~80 ms); analysis continues in background (49 s clip: <16 s).
// New sequence lands in a root bin "Auto Reframed Sequences". Size = source height x (height*num/den).
function tlAutoReframe(seq, num, den, preset, newName) {
    var before = tlSeqIdSet();
    var r = seq.autoReframeSequence(num, den, preset || "default", newName, false);
    if (r && r.sequenceID) return r;
    var made = tlNewSeqsSince(before);
    return made.length ? made[0] : null;
}
function tlReframeDone(seq) { return seq.isDoneAnalyzingForVideoEffects(); }

// ---------- events (item 10) ----------
// app.bind(name, fn) REPLACES any previous handler for that name (verified: rebinding does not duplicate),
// so re-running this after a panel reload is safe. Panel side:
//   window.__adobe_cep__.addEventListener('com.klipora.ev', e => ... e.data ...)
// Verified to fire: onSequenceActivated (openSequence/switch tab), onActiveSequenceSelectionChanged,
// onActiveSequenceChanged (any edit), onActiveSequenceTrackItemAdded/Removed, onProjectChanged.
var TL_EVENTS = ["onSequenceActivated", "onActiveSequenceSelectionChanged", "onActiveSequenceChanged",
                 "onActiveSequenceTrackItemAdded", "onActiveSequenceTrackItemRemoved", "onProjectChanged"];

function tlBindEvents(csxsType, names) {
    if (!$.global.__tlPlug) $.global.__tlPlug = new ExternalObject("lib:PlugPlugExternalObject");
    names = names || TL_EVENTS;
    for (var i = 0; i < names.length; i++) {
        (function (n) {
            app.bind(n, function () {
                var ev = new CSXSEvent();
                ev.type = csxsType;
                var s = app.project.activeSequence;
                ev.data = n + "|" + (s ? s.sequenceID : "");
                ev.dispatch();
            });
        })(names[i]);
    }
    return names.length;
}

function tlUnbindEvents(names) {
    names = names || TL_EVENTS;
    for (var i = 0; i < names.length; i++) app.bind(names[i], function () {}); // replace with no-op
    return names.length;
}
