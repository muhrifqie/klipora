// 39_angles.jsx: host side of "Auto Angles" (Angle Otomatis) (tool id "angles"). ES3 only (no let/const, arrows, JSON,
// Array.prototype.indexOf/forEach/map, trailing commas). Every bac_angles_* function returns acOk(...) or acErr(...).
// The panel applies the engine plan {kind: "keyframes", mode: "static", splits, items} on a CLONE made with
// bac_cloneSeq, so the user's sequence is never touched:
//   1. bac_angles_split(cloneId, times)        QE razor at sequence seconds (only when the plan has splits),
//   2. bac_angles_apply(cloneId, track, items) static Motion Scale + Position per clip (no keyframes),
//   3. bac_angles_read(cloneId, track)          read back for checks (verifier / tests).
// Verified facts used (docs/research/premiere_timeline_api.md): QE razor(timecode) cuts every track (1b);
// tlTimecode is frame exact; Motion = matchName "AE.ADBE Motion", properties[0] Position (normalized 0..1 in
// 26.x), [1] Scale (section 4); QE only works on the active sequence. setValue on a static Motion param is the
// documented ComponentParam API but was not run in the lab: the verifier checks it (docs/tools/angles.md).

// Unlock + sync-lock + target every track so one razor cuts them all (restored with tlApplyTrackStates).
function acAnglesPrepare(seq) {
    tlPrepareAllTracks(seq);
    var g, i, groups = [seq.videoTracks, seq.audioTracks];
    for (g = 0; g < 2; g++) {
        for (i = 0; i < groups[g].numTracks; i++) {
            if (!groups[g][i].isTargeted()) groups[g][i].setTargeted(true, true);
        }
    }
}

function acAnglesNums(arg) {
    var out = [], i;
    if (!(arg instanceof Array)) return out;
    for (i = 0; i < arg.length; i++) {
        var v = Number(arg[i]);
        if (!isNaN(v)) out.push(v);
    }
    return out;
}

// Razor the whole sequence at `times` (sequence seconds) = Add Edit to all tracks. Times at an existing edit or
// outside the sequence are skipped by Premiere. Track lock/sync/target states, In/Out and the playhead are restored.
// -> {ok, done, ms, clips (count on V<track+1>)}
function bac_angles_split(seqId, times, track) {
    try {
        var seq = tlSeqById(String(seqId));
        if (!seq) return acErr(acT("anglesNoResult", "Sequence hasil tidak ditemukan."));
        var list = acAnglesNums(times);
        app.project.openSequence(seq.sequenceID);          // QE works on the active sequence only
        app.enableQE();
        var q = qe.project.getActiveSequence();
        var fps = tlFps(seq), tpf = Number(seq.timebase);
        var lastFrame = Math.round(Number(seq.end) / tpf);
        var states = tlTrackStates(seq);
        var inS = Number(seq.getInPoint()), outS = Number(seq.getOutPoint());
        var cti = String(seq.getPlayerPosition().ticks);
        var t0 = new Date().getTime(), done = 0, i;
        acAnglesPrepare(seq);
        try {
            for (i = 0; i < list.length; i++) {
                var f = Math.round(list[i] * fps);
                if (f <= 0 || f >= lastFrame) continue;
                q.razor(tlTimecode(seq, f));
                done++;
            }
        } finally {
            tlApplyTrackStates(seq, states);
            if (inS > -1000 && Number(seq.getInPoint()) !== inS) seq.setInPoint(String(Math.round(inS * fps) * tpf));
            if (outS > -1000 && Number(seq.getOutPoint()) !== outS) seq.setOutPoint(String(Math.round(outS * fps) * tpf));
            if (String(seq.getPlayerPosition().ticks) !== cti) seq.setPlayerPosition(cti);
        }
        var tr = seq.videoTracks[Number(track) || 0];
        return acOk({ ok: true, done: done, ms: new Date().getTime() - t0, clips: tr ? tr.clips.numItems : 0 });
    } catch (e) { return acCatch(e, "bac_angles_split"); }
}

// First clip index whose end is after `t` (clips are in timeline order).
function acAnglesFirstClip(clips, t) {
    var lo = 0, hi = clips.numItems;
    while (lo < hi) {
        var mid = Math.floor((lo + hi) / 2);
        if (clips[mid].end.seconds <= t) lo = mid + 1; else hi = mid;
    }
    return lo;
}

// Static Motion per clip: every clip of video track `track` whose middle lies inside an item [t0, t1) gets the
// item's Scale (%) and Position ([x, y] normalized). Clips whose Scale or Position already have keyframes are left
// alone (counted in `keyed`). items: [{id, t0, t1, scale, pos: [x, y]}] sorted by t0 (one chunk per call).
// -> {ok, set, keyed, nomotion, items, matched, bad (read-back mismatch), ms}
function bac_angles_apply(seqId, track, items) {
    try {
        var seq = tlSeqById(String(seqId));
        if (!seq) return acErr(acT("anglesNoResult", "Sequence hasil tidak ditemukan."));
        var ti = Number(track) || 0;
        if (ti < 0 || ti >= seq.videoTracks.numTracks) return acErr(acT("anglesNoTrackResult", "Track V{0} tidak ada di sequence hasil.", ti + 1));
        var list = (items instanceof Array) ? items : [];
        var res = { ok: true, set: 0, keyed: 0, nomotion: 0, items: list.length, matched: 0, bad: 0, ms: 0 };
        if (!list.length) return acOk(res);
        var t0 = new Date().getTime();
        var qv = null;
        if (seq.videoTracks[ti].isLocked()) {                    // a locked camera track: unlock while we work
            app.project.openSequence(seq.sequenceID);
            app.enableQE();
            qv = qe.project.getActiveSequence().getVideoTrackAt(ti);
            qv.setLock(false);
        }
        try {
            acAnglesSet(seq.videoTracks[ti].clips, list, res);
        } finally {
            if (qv) qv.setLock(true);
        }
        res.ms = new Date().getTime() - t0;
        return acOk(res);
    } catch (e) { return acCatch(e, "bac_angles_apply"); }
}

// Sweep clips and items (both in time order) and set the static Motion values. Updates `res` counters.
function acAnglesSet(clips, list, res) {
    var n = clips.numItems, k = 0, lastHit = -1, last = null, i;
    for (i = acAnglesFirstClip(clips, Number(list[0].t0)); i < n && k < list.length; i++) {
        var c = clips[i];
        var mid = (c.start.seconds + c.end.seconds) / 2;
        while (k < list.length && Number(list[k].t1) <= mid) k++;
        if (k >= list.length) break;
        var it = list[k];
        if (mid < Number(it.t0)) continue;
        if (lastHit !== k) { res.matched++; lastHit = k; }
        var m = tlComp(c, "AE.ADBE Motion");
        if (!m) { res.nomotion++; continue; }
        var pos = m.properties[0], sc = m.properties[1];   // language independent indexes (lab item 4)
        if (sc.isTimeVarying() || pos.isTimeVarying()) { res.keyed++; continue; }
        var want = Number(it.scale);
        sc.setValue(want, false);
        pos.setValue([Number(it.pos[0]), Number(it.pos[1])], false);
        if (Math.abs(Number(sc.getValue()) - want) > 0.01) res.bad++;
        last = [sc, want];
        res.set++;
    }
    if (last) last[0].setValue(last[1], true);              // one UI refresh per chunk
}

// Read back Motion of every clip on video track `track`: {ok, clips: [{start, end, scale, pos, keyed}]}.
function bac_angles_read(seqId, track) {
    try {
        var seq = acSeq(seqId);
        if (!seq) return acErr(acT("anglesOpenSeq", "Buka sequence dulu."));
        var ti = Number(track) || 0;
        if (ti < 0 || ti >= seq.videoTracks.numTracks) return acErr(acT("anglesNoTrack", "Track V{0} tidak ada.", ti + 1));
        var clips = seq.videoTracks[ti].clips, out = [], i;
        for (i = 0; i < clips.numItems; i++) {
            var c = clips[i], m = tlComp(c, "AE.ADBE Motion"), row = { start: c.start.seconds, end: c.end.seconds };
            if (m) {
                var p = m.properties[0].getValue();
                row.scale = Number(m.properties[1].getValue());
                row.pos = (p && p.length >= 2) ? [Number(p[0]), Number(p[1])] : null;
                row.keyed = !!(m.properties[1].isTimeVarying() || m.properties[0].isTimeVarying());
            }
            out.push(row);
        }
        return acOk({ ok: true, name: seq.name, clips: out });
    } catch (e) { return acCatch(e, "bac_angles_read"); }
}
