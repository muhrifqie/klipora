// 20_graphics.jsx: verified ES3 ExtendScript helpers for captions/graphics in Premiere Pro 26.2.2 (CEP 12).
// Exercised live by the Premiere lab part 2 (see docs/research/premiere_graphics_api.md, section "Helpers").
// Ported UNCHANGED from proto/lab/graphics_helpers.jsx. Requires 00_util.jsx + 10_timeline.jsx (loaded before it).
// Conventions: seconds are Numbers; functions never call app.project.save().

var GX_CAPTION_TRACK = "Klipora Captions";   // older projects: "AutoCut Captions" (found too, see gxFindVideoTrack)
// Only these extensions imported/composited correctly with alpha in 26.2.2 (VERIFIED: qtrle .mov, PNG-in-MOV .mov,
// ProRes 4444 .mov, PNG stills). .webm (VP9 alpha) is NOT supported: importFiles opens a MODAL "File format not
// supported" dialog that blocks ExtendScript until a human clicks OK -> never pass it to importFiles.
var GX_SAFE_EXT = { mov: 1, png: 1, mp4: 1, srt: 1 };
var GX_ALPHA = { AUTO: 0, STRAIGHT: 1, PREMULTIPLIED: 2, IGNORE: 3 }; // FootageInterpretation.alphaUsage

function gxExt(path) { var m = String(path).match(/\.([A-Za-z0-9]+)$/); return m ? m[1].toLowerCase() : ""; }

// Import a file safely (exists + extension check first; a failed import shows a blocking modal dialog).
function gxImport(path, bin) {
    if (!GX_SAFE_EXT[gxExt(path)]) return null;
    if (!new File(path).exists) return null;
    return tlImportFile(path, bin);
}

// ---------- tracks ----------
// Find a video track by name; optionally create it on top. QE setName() renames the track and the official
// Track.name reflects it immediately (VERIFIED). Returns {track, index} or null.
// Our track names also match their pre-rename spelling ("Klipora X" -> "AutoCut X", acNameAlts in 00_util.jsx).
function gxFindVideoTrack(seq, name) {
    var alts = acNameAlts(name);
    for (var a = 0; a < alts.length; a++) {
        for (var i = 0; i < seq.videoTracks.numTracks; i++) if (seq.videoTracks[i].name === alts[a]) return { track: seq.videoTracks[i], index: i };
    }
    return null;
}

function gxEnsureVideoTrack(seq, name) {
    var hit = gxFindVideoTrack(seq, name);
    if (hit) return hit;
    var tr = tlAddVideoTrack(seq);                      // QE addTracks(1, n, 0,...): appends one track on top; opens seq
    if (!tr) return null;
    var idx = seq.videoTracks.numTracks - 1;
    app.enableQE();
    qe.project.getActiveSequence().getVideoTrackAt(idx).setName(name);
    return { track: seq.videoTracks[idx], index: idx };
}

// Remove every clip on a track (no ripple). ~1-20 ms per clip.
function gxClearTrack(track) {
    var n = 0;
    while (track.clips.numItems > 0) { track.clips[0].remove(false, true); n++; }
    return n;
}

// ---------- alpha overlay (rendered captions) ----------
// Place a rendered alpha overlay (qtrle / PNG-in-MOV / ProRes 4444 .mov) on the named top track.
// opts: {trackName, replace (default true), startSec (0), yNorm (Motion Position y, 0..1), xNorm, scale (%), bin,
//        alpha (GX_ALPHA.STRAIGHT default)}
// Position is normalized 0..1 of the FRAME (VERIFIED: a 1080x300 strip at y=0.8 lands centred at 1536 px of 1920).
function gxPlaceOverlay(seq, path, opts) {
    opts = opts || {};
    var item = gxImport(path, opts.bin || null);
    if (!item) return { ok: false, err: "import failed or unsupported/missing file: " + path };
    var fi = item.getFootageInterpretation();
    var want = (opts.alpha === undefined) ? GX_ALPHA.STRAIGHT : opts.alpha;
    if (fi && fi.alphaUsage !== want) { fi.alphaUsage = want; item.setFootageInterpretation(fi); }
    var t = gxEnsureVideoTrack(seq, opts.trackName || GX_CAPTION_TRACK);
    if (!t) return { ok: false, err: "could not create track" };
    if (opts.replace !== false) gxClearTrack(t.track);
    var start = opts.startSec || 0;
    t.track.overwriteClip(item, start);                   // seconds (Number) or ticks (String) both accepted
    var clip = gxClipAt(t.track, start);
    if (clip && (opts.yNorm !== undefined || opts.xNorm !== undefined || opts.scale !== undefined)) {
        var m = tlComp(clip, "AE.ADBE Motion");
        if (opts.yNorm !== undefined || opts.xNorm !== undefined) {
            var p = m.properties[0].getValue();
            m.properties[0].setValue([opts.xNorm !== undefined ? opts.xNorm : p[0], opts.yNorm !== undefined ? opts.yNorm : p[1]], true);
        }
        if (opts.scale !== undefined) m.properties[1].setValue(opts.scale, true);
    }
    return { ok: !!clip, track: t.index, nodeId: item.nodeId, start: clip ? clip.start.seconds : null, end: clip ? clip.end.seconds : null };
}

function gxClipAt(track, sec) {
    for (var i = 0; i < track.clips.numItems; i++) {
        var c = track.clips[i];
        if (Math.abs(c.start.seconds - sec) < 0.0005) return c;
    }
    return null;
}

// Swap the overlay's media for a re-rendered file IN PLACE (keeps track, position, keyframes; ~15 ms).
// VERIFIED: the timeline shows the new frames immediately; the clip does NOT grow by itself when the new file is
// longer, so end/outPoint are re-set from the project item's new out point.
// GOTCHA: Premiere keeps media files open (the originally imported file and the current one stayed locked;
// rename -> WinError 32). Always render to a NEW versioned file name, never overwrite the linked file.
function gxRelinkOverlay(seq, trackName, newPath) {
    var t = gxFindVideoTrack(seq, trackName || GX_CAPTION_TRACK);
    if (!t || t.track.clips.numItems === 0) return { ok: false, err: "no overlay clip" };
    if (!GX_SAFE_EXT[gxExt(newPath)] || !new File(newPath).exists) return { ok: false, err: "bad file" };
    var clip = t.track.clips[0], pi = clip.projectItem;
    if (!pi.canChangeMediaPath()) return { ok: false, err: "cannot change media path" };
    var ok = pi.changeMediaPath(new File(newPath).fsName, true);
    var dur = pi.getOutPoint().seconds - pi.getInPoint().seconds;    // new media duration
    var endSec = clip.start.seconds + dur;
    clip.end = endSec;                                     // Number seconds accepted (VERIFIED)
    var o = new Time(); o.seconds = clip.inPoint.seconds + dur; clip.outPoint = o;
    clip.end = endSec;                                     // set again: end/outPoint are applied independently
    return { ok: ok, start: clip.start.seconds, end: clip.end.seconds, media: pi.getMediaPath() };
}

// ---------- sequence compositing ----------
// Sequences default to compositeLinearColor=true. Then soft edges / shadows / semi-transparent boxes of an
// overlay blend in LINEAR light and differ from an sRGB (FFmpeg/libass) preview by up to ~63 levels.
// With false the overlay composite is pixel-identical to the FFmpeg ground truth (VERIFIED, mean diff 0.00).
function gxGetLinearCompositing(seq) { return seq.getSettings().compositeLinearColor; }
function gxSetLinearCompositing(seq, on) {
    var st = seq.getSettings(); st.compositeLinearColor = !!on; return seq.setSettings(st);
}

// ---------- MOGRT ----------
// importMGT(path, ticksString, vTrackIdx, aTrackIdx) -> TrackItem (VERIFIED ~200-330 ms per call; 50 calls 13.1 s).
// Clip duration: TrackItem.end = seconds works (~10 ms). Graphic clip media time starts at 3600 s (01:00:00:00),
// so keyframe helpers must convert with tlSeqToMedia(clip, seqSec) (it uses clip.inPoint).
function gxInsertMogrt(seq, mogrtPath, startSec, endSec, vIdx) {
    if (!new File(mogrtPath).exists) return null;
    var clip = seq.importMGT(mogrtPath, String(Math.round(startSec * TL_TICKS)), vIdx, vIdx);
    if (clip && endSec !== undefined && endSec !== null) clip.end = endSec;
    return clip;
}

// Pop-in animation on any clip (MOGRT or overlay) using clip-level Motion Scale + Opacity keys.
// VERIFIED on a MOGRT clip: text width 468 -> 720 (112%) -> 643 (100%) px at +34/+100/+200 ms.
// GOTCHA: Motion scales about the FRAME centre, so a caption near the bottom drifts while scaling. Pass
// opts.pivot = [x, y] (normalized centre of the text): anchor = position = pivot keeps the text in place
// (VERIFIED: centre stayed at 543,1673 px while width went 406 -> 624 -> 557).
function gxPopIn(clip, opts) {
    opts = opts || {};
    if (opts.pivot) {
        var mo = tlComp(clip, "AE.ADBE Motion");
        mo.properties[5].setValue(opts.pivot, false);   // Anchor Point (normalized)
        mo.properties[0].setValue(opts.pivot, true);    // Position (normalized)
    }
    var st = clip.start.seconds, d = opts.dur || 0.167;
    var from = opts.from === undefined ? 60 : opts.from, peak = opts.peak === undefined ? 112 : opts.peak;
    tlZoomKeys(clip, [{ t: st, scale: from }, { t: st + d * 0.6, scale: peak }, { t: st + d, scale: 100 }], TL_INTERP.EASE);
    if (opts.fade !== false) {
        var op = tlComp(clip, "AE.ADBE Opacity").properties[0];
        if (!op.isTimeVarying()) op.setTimeVarying(true);
        var a = tlSeqToMedia(clip, st), b = tlSeqToMedia(clip, st + Math.min(0.07, d));
        op.addKey(a); op.setValueAtKey(a, 0, false);
        op.addKey(b); op.setValueAtKey(b, 100, true);
    }
    return true;
}

// AE-authored MOGRT ("Graphic Parameters", matchName AE.ADBE Capsule) only. Premiere-authored MOGRTs import as
// native graphics: getMGTComponent() returns null and their Text "Source Text" cannot be read or set by script.
function gxCapsuleParam(clip, displayName) {
    var m = clip.getMGTComponent();
    if (!m) return null;
    for (var i = 0; i < m.properties.numItems; i++) if (m.properties[i].displayName === displayName) return m.properties[i];
    return null;
}

// Set a capsule text param. runs: [{len, font, size, bold, italic, caps}] (optional). Per-run FONT and faux
// styles apply (VERIFIED: "HALO "/"DUNIA "(Impact italic)/"KITA"); size per run applies only when the template
// allows it (capPropFontSizeEdit). There is NO per-run colour in this JSON.
function gxSetCapsuleText(clip, displayName, text, runs) {
    var p = gxCapsuleParam(clip, displayName);
    if (!p) return false;
    var cur = eval("(" + p.getValue() + ")");          // value is a JSON string; ES3 has no JSON.parse
    cur.textEditValue = text;
    if (runs && runs.length) {
        var keys = { font: "fontEditValue", size: "fontSizeEditValue", bold: "fontFSBoldValue", italic: "fontFSItalicValue", caps: "fontFSAllCapsValue" };
        var base = {}; for (var k in keys) base[k] = cur[keys[k]][0];
        var smallCaps = cur.fontFSSmallCapsValue[0];
        cur.fontTextRunLength = []; cur.fontFSSmallCapsValue = [];
        for (k in keys) cur[keys[k]] = [];
        for (var i = 0; i < runs.length; i++) {
            cur.fontTextRunLength.push(runs[i].len);
            cur.fontFSSmallCapsValue.push(smallCaps);
            for (k in keys) cur[keys[k]].push(runs[i][k] !== undefined ? runs[i][k] : base[k]);
        }
        cur.capPropTextRunCount = runs.length;
    } else {
        cur.fontTextRunLength = [text.length];
    }
    return p.setValue(tlJSON(cur), true);
}

// Capsule colour param: getColorValue() -> [a, r, g, b]; setColorValue(a, r, g, b, updateUI) (VERIFIED).
function gxSetCapsuleColor(clip, displayName, r, g, b) {
    var p = gxCapsuleParam(clip, displayName);
    if (!p) return false;
    return p.setColorValue(255, r, g, b, true);
}

// ---------- native caption track ----------
// createCaptionTrack returns Boolean true. There is NO ExtendScript/QE API to list caption tracks, read caption
// items, or set caption font/colour/background/position (VERIFIED by reflection + probes). exportFramePNG does not
// draw caption tracks. SRT inline tags (<font color>, <b>, <i>) ARE parsed into styled runs (VERIFIED in the
// saved caption block data); on-screen rendering of those runs is UNVERIFIED.
function gxCaptionTrackFromSrt(seq, srtPath, bin) {
    var item = gxImport(srtPath, bin);
    if (!item) return false;
    return seq.createCaptionTrack(item, 0, Sequence.CAPTION_FORMAT_SUBTITLE);
}
