// 37_zoom.jsx: Auto Zoom host functions (ES3). The engine (engine/ac/tools/zoom.py, action "apply") writes the plan
// <workdir>\zoom_keys.json; the panel clones the sequence (bac_zoom_clone), then applies the plan in chunks
// (bac_zoom_keys) as Motion Scale/Position keyframes in clip MEDIA time (docs/research/premiere_timeline_api.md 4:
// Motion = "AE.ADBE Motion", [0] Position normalised 0..1, [1] Scale, [3] Uniform Scale; ~25 ms per 100 keys with
// updateUI=false). The user's original sequence is never touched. Never saves the project.
// Plan clip: {track, start, end, in, speed, w, h, keys: [[mediaSec, s, cx, cy], ...], reset: {scale, pos} | absent}
//   Scale    = base * s
//   Position = basePos + (0.5 - c) * s * (mediaSize * base / 100) / frameSize      (zoom to viewport centre c)
// `reset` = the clip carries Klipora zoom keys from an earlier run: drop them and restore these values first.
// A clip with Motion keys that are not ours is skipped (never overwrite the user's own animation).

var AC_ZOOM_INTERP = { linear: 0, ease: 3 };   // codes measured on 26.2 (TL_INTERP): 0 linear, 3 ease in/out

// Read our own plan file (trusted, ASCII JSON written by the engine). Cached per path + size + mtime.
function acZoomPlan(path) {
    var f = new File(path);
    if (!f.exists) return null;
    var key = f.fsName + "|" + f.length + "|" + (f.modified ? f.modified.getTime() : 0);
    var g = $.global.__acZoomPlan;
    if (g && g.key === key) return g.plan;
    f.encoding = "UTF-8";
    if (!f.open("r")) return null;
    var txt = f.read();
    f.close();
    if (!/^\s*\{/.test(txt)) return null;
    var plan = eval("(" + txt + ")");
    $.global.__acZoomPlan = { key: key, plan: plan };
    return plan;
}

// "<name> (Zoom)", "<name> (Zoom 2)" ... (a zoom result zoomed again keeps the short base name).
function acZoomName(src) {
    var base = String(src.name).replace(/ \(Zoom( \d+)?\)$/, "");
    var taken = {}, s = app.project.sequences, i;
    for (i = 0; i < s.numSequences; i++) taken[s[i].name] = 1;
    var name = base + " (Zoom)";
    for (i = 2; taken[name] && i < 1000; i++) name = base + " (Zoom " + i + ")";
    return name;
}

// Track item index by start frame (and by in-point frame as a fallback after the user moved clips).
function acZoomIndex(track, fps) {
    var byStart = {}, byIn = {}, clips = track.clips;
    for (var i = 0; i < clips.numItems; i++) {
        var c = clips[i];
        byStart["f" + Math.round(c.start.seconds * fps)] = c;
        byIn["f" + Math.round(c.inPoint.seconds * fps)] = c;
    }
    return { start: byStart, inp: byIn };
}

function acZoomFind(idx, fps, start, inSec) {
    var f = Math.round(Number(start) * fps), c = idx.start["f" + f] || idx.start["f" + (f - 1)] || idx.start["f" + (f + 1)];
    if (c) return c;
    if (inSec === undefined || inSec === null) return null;
    var g = Math.round(Number(inSec) * fps);
    return idx.inp["f" + g] || idx.inp["f" + (g - 1)] || idx.inp["f" + (g + 1)] || null;
}

function acZoomKeySec(k) { return (typeof k === "number") ? k : k.seconds; }

// Clone the source sequence for the zoom result. Returns {ok, id, name, origId, origName}; the original stays active.
function bac_zoom_clone(seqId) {
    try {
        var src = acSeq(seqId);
        if (!src) return acErr(acT("zoomNoSource", "Sequence sumber tidak ditemukan. Buka sequence yang dianalisis lalu coba lagi."));
        var c = tlCloneSequence(src, acZoomName(src), null);
        if (!c) return acErr(acT("zoomCloneFail", "Gagal menggandakan sequence."));
        acMarkMade(c);
        return acOk({ ok: true, id: c.sequenceID, name: c.name, origId: src.sequenceID, origName: src.name });
    } catch (e) { return acCatch(e, "bac_zoom_clone"); }
}

// Apply plan.clips[from, to) to sequence seqId (the clone). Returns {ok, done, keys, reset, skipped: [{start, why}],
// base: [{track, start, end, in, scale, pos}] (values before our keys, for the panel's re-run record), ms}.
function bac_zoom_keys(seqId, planPath, from, to) {
    try {
        var t0 = new Date().getTime();
        var seq = tlSeqById(String(seqId));
        if (!seq) return acErr(acT("zoomNoResult", "Sequence hasil tidak ditemukan."));
        var plan = acZoomPlan(planPath);
        if (!plan || !plan.clips) return acErr(acT("zoomPlanUnreadable", "Rencana zoom tidak terbaca: {0}", planPath));
        var fps = tlFps(seq), sw = seq.frameSizeHorizontal, sh = seq.frameSizeVertical;
        var code = AC_ZOOM_INTERP[plan.interp];
        if (code === undefined) code = 0;
        var a = Math.max(0, Number(from) || 0), b = Math.min(plan.clips.length, (to === undefined || to === null) ? plan.clips.length : Number(to));
        var indexes = {}, skipped = [], bases = [], nKeys = 0, nReset = 0, done = 0, last = null;
        for (var n = a; n < b; n++) {
            var pc = plan.clips[n], tr = Number(pc.track) || 0;
            if (tr >= seq.videoTracks.numTracks) { skipped.push({ start: pc.start, why: "track" }); continue; }
            if (!indexes["t" + tr]) indexes["t" + tr] = acZoomIndex(seq.videoTracks[tr], fps);
            var item = acZoomFind(indexes["t" + tr], fps, pc.start, pc.reset ? pc["in"] : null);
            if (!item) { skipped.push({ start: pc.start, why: "missing" }); continue; }
            var m = tlComp(item, "AE.ADBE Motion");
            if (!m) { skipped.push({ start: pc.start, why: "motion" }); continue; }
            var pos = m.properties[0], sc = m.properties[1], uni = m.properties.numItems > 3 ? m.properties[3] : null;
            if (uni) {
                var u = uni.getValue();
                if (u === false || u === 0) { skipped.push({ start: pc.start, why: "uniform" }); continue; }
            }
            var base;
            if (pc.reset) {
                if (sc.isTimeVarying()) sc.setTimeVarying(false);
                if (pos.isTimeVarying()) pos.setTimeVarying(false);
                sc.setValue(Number(pc.reset.scale), false);
                pos.setValue([Number(pc.reset.pos[0]), Number(pc.reset.pos[1])], false);
                base = { scale: Number(pc.reset.scale), pos: [Number(pc.reset.pos[0]), Number(pc.reset.pos[1])] };
                nReset++;
            } else if (sc.isTimeVarying() || pos.isTimeVarying()) {
                skipped.push({ start: pc.start, why: "keys" });
                continue;
            } else {
                var pv = pos.getValue();
                base = { scale: Number(sc.getValue()), pos: [Number(pv[0]), Number(pv[1])] };
            }
            var keys = pc.keys || [];
            if (!keys.length) { done++; continue; }
            var bs = base.scale / 100, kx = (Number(pc.w) || sw) * bs / sw, ky = (Number(pc.h) || sh) * bs / sh;
            if (!sc.isTimeVarying()) sc.setTimeVarying(true);
            if (!pos.isTimeVarying()) pos.setTimeVarying(true);
            var want = [];
            for (var j = 0; j < keys.length; j++) {
                var k = keys[j], t = Number(k[0]), s = Number(k[1]);
                var sv = base.scale * s;
                var pv2 = [base.pos[0] + (0.5 - Number(k[2])) * kx * s, base.pos[1] + (0.5 - Number(k[3])) * ky * s];
                sc.addKey(t); sc.setValueAtKey(t, sv, false);
                pos.addKey(t); pos.setValueAtKey(t, pv2, false);
                sc.setInterpolationTypeAtKey(t, code, false);
                pos.setInterpolationTypeAtKey(t, code, false);
                want.push(t);
                last = { p: sc, t: t, v: sv };
                nKeys++;
            }
            // setTimeVarying(true) may add a key at the playhead: remove any key that is not ours
            acZoomDropStray(sc, want, 0.6 / fps);
            acZoomDropStray(pos, want, 0.6 / fps);
            bases.push({ track: tr, start: item.start.seconds, end: item.end.seconds, "in": item.inPoint.seconds,
                         scale: base.scale, pos: base.pos });
            done++;
        }
        if (last) last.p.setValueAtKey(last.t, last.v, true);   // one UI refresh per chunk
        return acOk({ ok: true, done: done, keys: nKeys, reset: nReset, skipped: skipped, base: bases,
                      from: a, to: b, total: plan.clips.length, ms: new Date().getTime() - t0 });
    } catch (e) { return acCatch(e, "bac_zoom_keys"); }
}

// Remove keys farther than `tol` seconds from every wanted time (`want` ascending media seconds).
function acZoomDropStray(prop, want, tol) {
    var ks = prop.getKeys();
    if (!ks || !want.length) return 0;
    var n = 0;
    for (var i = ks.length - 1; i >= 0; i--) {
        var sec = acZoomKeySec(ks[i]), lo = 0, hi = want.length - 1;
        while (lo < hi) { var mid = Math.floor((lo + hi) / 2); if (want[mid] < sec) lo = mid + 1; else hi = mid; }
        var d = Math.abs(want[lo] - sec);
        if (lo > 0) d = Math.min(d, Math.abs(want[lo - 1] - sec));
        if (d > tol) { prop.removeKey(ks[i]); n++; }
    }
    return n;
}

// Motion state of every clip on a video track (verification + "does this sequence carry keys?").
// -> {ok, id, name, track, clips: [{name, start, end, in, scaleTV, posTV, nScale, nPos, scale, pos}]}
function bac_zoom_scan(seqId, track, max) {
    try {
        var seq = acSeq(seqId);
        if (!seq) return acErr(acT("zoomOpenSeq", "Buka sequence dulu."));
        var ti = Number(track) || 0;
        if (ti >= seq.videoTracks.numTracks) return acErr(acT("zoomNoTrack", "Track video V{0} tidak ada.", ti + 1));
        var tr = seq.videoTracks[ti], out = [], lim = Number(max) || 2000;
        for (var i = 0; i < tr.clips.numItems && i < lim; i++) {
            var c = tr.clips[i], m = tlComp(c, "AE.ADBE Motion");
            var o = { name: c.name, start: c.start.seconds, end: c.end.seconds, "in": c.inPoint.seconds };
            if (m) {
                var pos = m.properties[0], sc = m.properties[1];
                o.scaleTV = !!sc.isTimeVarying(); o.posTV = !!pos.isTimeVarying();
                o.nScale = o.scaleTV ? sc.getKeys().length : 0;
                o.nPos = o.posTV ? pos.getKeys().length : 0;
                o.scale = o.scaleTV ? null : sc.getValue();
                o.pos = o.posTV ? null : pos.getValue();
            }
            out.push(o);
        }
        return acOk({ ok: true, id: seq.sequenceID, name: seq.name, track: ti, clips: out });
    } catch (e) { return acCatch(e, "bac_zoom_scan"); }
}

// Scale/Position of the clip under sequence time t (track) at each sequence time in `times` (verification).
// -> {ok, values: [{t, scale, pos}]}
function bac_zoom_values(seqId, track, times) {
    try {
        var seq = acSeq(seqId);
        if (!seq) return acErr(acT("zoomOpenSeq", "Buka sequence dulu."));
        var tr = seq.videoTracks[Number(track) || 0], out = [];
        for (var i = 0; i < times.length; i++) {
            var t = Number(times[i]), item = null;
            for (var j = 0; j < tr.clips.numItems && !item; j++) {
                var c = tr.clips[j];
                if (c.start.seconds - 1e-6 <= t && t < c.end.seconds - 1e-6) item = c;
            }
            if (!item) { out.push({ t: t, scale: null, pos: null }); continue; }
            var m = tlComp(item, "AE.ADBE Motion"), mt = tlSeqToMedia(item, t);
            out.push({ t: t, media: mt, scale: m.properties[1].getValueAtTime(mt), pos: m.properties[0].getValueAtTime(mt) });
        }
        return acOk({ ok: true, values: out });
    } catch (e) { return acCatch(e, "bac_zoom_values"); }
}
