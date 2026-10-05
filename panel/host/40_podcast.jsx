// 40_podcast.jsx: Podcast Multicam host side (ES3). Loaded after 00_util / 05_bridge / 10_timeline.
// Output model (docs/research/product_cutting.md section 4): stacked camera tracks, cut at every switch point,
// inactive clips disabled. Multicam source sequences cannot be created by script, so this is the editable form.
//   bac_podcast_razor   QE track razor at the plan's cut points (only the tracks whose state changes), in chunks
//   bac_podcast_disable enable the active camera's clips, disable the others, for ONE camera track
//   bac_podcast_switchAt "Ganti kamera di playhead": the chosen camera becomes the visible one for the shot at the CTI
//   bac_podcast_camAt   which camera is visible at the CTI (panel buttons)
// Plans come from engine/ac/tools/podcast.py (plan.kind "multicam"; times in sequence seconds, frame aligned).
// Safety: every editing function refuses sequences that Klipora did not make (the user's original is never
// touched; the panel clones with bac_cloneSeq first). Never saves the project.
// Lab facts used: qeTrack.razor(tc) cuts one track (premiere_timeline_api.md section 1b), QE needs the active
// sequence and timecodes from tlTimecode, TrackItem.disabled is read/write (Types-for-Adobe; live UNVERIFIED).

// A sequence the podcast tool may edit: made by bac_* in this Premiere session, or named "... (Klipora[ n])" /
// the old "... (AutoCut[ n])" (acIsResultName).
function acPodIsResult(seq) {
    return !!seq && (acWasMade(seq.sequenceID) || acIsResultName(seq.name));
}

// Clips of a track as [{s, e, item}] sorted by start (seconds).
function acPodClips(track) {
    var out = [], clips = track.clips, i;
    for (i = 0; i < clips.numItems; i++) out.push({ s: clips[i].start.seconds, e: clips[i].end.seconds, item: clips[i] });
    out.sort(function (a, b) { return a.s - b.s; });
    return out;
}

// Index of the clip that STRICTLY contains t (more than `tol` away from both edges), or -1 (gap / edit point).
function acPodFind(list, t, tol) {
    var lo = 0, hi = list.length - 1, mid;
    while (lo <= hi) {
        mid = (lo + hi) >> 1;
        if (list[mid].e - tol <= t) lo = mid + 1;
        else if (list[mid].s + tol >= t) hi = mid - 1;
        else return mid;
    }
    return -1;
}

// Index of the clip with s <= t < e, or -1.
function acPodAt(list, t) {
    var lo = 0, hi = list.length - 1, mid;
    while (lo <= hi) {
        mid = (lo + hi) >> 1;
        if (list[mid].e <= t) lo = mid + 1;
        else if (list[mid].s > t) hi = mid - 1;
        else return mid;
    }
    return -1;
}

// Last index k with starts[k] <= t (0 when t is before the first start).
function acPodShotAt(starts, t) {
    var lo = 0, hi = starts.length - 1, k = 0, mid;
    while (lo <= hi) {
        mid = (lo + hi) >> 1;
        if (starts[mid] <= t) { k = mid; lo = mid + 1; } else hi = mid - 1;
    }
    return k;
}

function acPodQe(seq) {
    app.project.openSequence(seq.sequenceID);       // QE works on the active sequence only
    app.enableQE();
    return qe.project.getActiveSequence();
}

// Razor camera tracks of sequence `seqId` at cuts[from..to) where cuts = [[sec, [videoTrackIdx, ...]], ...].
// A cut is skipped on a track when no clip strictly contains that time (gap, or already an edit point).
// Locked camera tracks are unlocked for the razor and locked again; the CTI is restored.
// Measured in the lab: about 64 ms per razor, so the panel sends about 20 cut points per call.
function bac_podcast_razor(seqId, cuts, from, to) {
    try {
        var seq = tlSeqById(String(seqId));
        if (!seq) return acErr(acT("podcastNoResult", "Sequence hasil tidak ditemukan."));
        if (!acPodIsResult(seq)) return acErr(acT("podcastOnlyRazor", "Hanya sequence hasil Klipora yang boleh dipotong. Sequence asli tidak diubah."));
        var q = acPodQe(seq);
        var fps = tlFps(seq), tol = 0.5 / fps, t0 = new Date().getTime();
        from = Number(from) || 0;
        to = (to === undefined || to === null) ? cuts.length : Math.min(cuts.length, Number(to));
        var lists = {}, locked = [], cti = seq.getPlayerPosition().ticks, i, k, tr, qt;
        for (i = from; i < to; i++) {
            for (k = 0; k < cuts[i][1].length; k++) {
                tr = Number(cuts[i][1][k]);
                if (lists[tr] !== undefined || tr < 0 || tr >= seq.videoTracks.numTracks) continue;
                qt = q.getVideoTrackAt(tr);
                if (qt.isLocked()) { qt.setLock(false); locked.push(tr); }
                lists[tr] = acPodClips(seq.videoTracks[tr]);
            }
        }
        var done = 0, skipped = 0;
        try {
            for (i = from; i < to; i++) {
                var frame = Math.round(Number(cuts[i][0]) * fps), ts = frame / fps, tc = tlTimecode(seq, frame);
                for (k = 0; k < cuts[i][1].length; k++) {
                    tr = Number(cuts[i][1][k]);
                    if (!lists[tr] || acPodFind(lists[tr], ts, tol) < 0) { skipped++; continue; }
                    q.getVideoTrackAt(tr).razor(tc);
                    done++;
                }
            }
        } finally {
            for (i = 0; i < locked.length; i++) q.getVideoTrackAt(locked[i]).setLock(true);
            seq.setPlayerPosition(cti);
        }
        return acOk({ ok: true, done: done, skipped: skipped, from: from, to: to, ms: new Date().getTime() - t0 });
    } catch (e) { return acCatch(e, "bac_podcast_razor"); }
}

// For ONE camera track: every clip whose middle lies in [scope[0], scope[1]) is enabled when its shot shows this
// track and disabled otherwise. shots = [[t0, t1, track], ...] sorted, contiguous. Clips outside the scope are left
// alone. `unsplit` counts clips that still span a switch point of this track (a razor that did not happen).
function bac_podcast_disable(seqId, track, shots, scope) {
    try {
        var seq = tlSeqById(String(seqId));
        if (!seq) return acErr(acT("podcastNoResult", "Sequence hasil tidak ditemukan."));
        if (!acPodIsResult(seq)) return acErr(acT("podcastOnlyEdit", "Hanya sequence hasil Klipora yang boleh diubah. Sequence asli tidak diubah."));
        var ti = Number(track);
        if (ti < 0 || ti >= seq.videoTracks.numTracks) return acErr(acT("podcastNoTrack", "Track kamera V{0} tidak ada.", ti + 1));
        if (!shots || !shots.length) return acErr(acT("podcastEmptyPlan", "Rencana kamera kosong."));
        var fps = tlFps(seq), tol = 0.5 / fps, t0 = new Date().getTime(), i, m;
        var starts = [];
        for (i = 0; i < shots.length; i++) starts.push(Number(shots[i][0]));
        var lo = Number(scope ? scope[0] : starts[0]), hi = Number(scope ? scope[1] : shots[shots.length - 1][1]);
        var q = acPodQe(seq), qt = q.getVideoTrackAt(ti), wasLocked = qt.isLocked();
        if (wasLocked) qt.setLock(false);
        var en = 0, dis = 0, changed = 0, unsplit = 0, outside = 0;
        try {
            var clips = seq.videoTracks[ti].clips;
            for (i = 0; i < clips.numItems; i++) {
                var it = clips[i], cs = it.start.seconds, ce = it.end.seconds, mid = (cs + ce) / 2;
                if (mid < lo || mid >= hi) { outside++; continue; }
                var want = Number(shots[acPodShotAt(starts, mid)][2]) !== ti;
                for (m = acPodShotAt(starts, cs + tol) + 1; m < starts.length && starts[m] < ce - tol; m++) {
                    if ((Number(shots[m - 1][2]) === ti) !== (Number(shots[m][2]) === ti)) { unsplit++; break; }
                }
                if (!!it.disabled !== want) { it.disabled = want; changed++; }
                if (want) dis++; else en++;
            }
        } finally {
            if (wasLocked) qt.setLock(true);
        }
        return acOk({ ok: true, track: ti, enabled: en, disabled: dis, changed: changed, unsplit: unsplit,
                      outside: outside, ms: new Date().getTime() - t0 });
    } catch (e) { return acCatch(e, "bac_podcast_disable"); }
}

// "Ganti kamera di playhead": camera track `cam` becomes the only enabled camera for the SHOT under the CTI.
// Camera tracks only have edits where their own state changes, so the shot is the intersection of the clips under
// the CTI on all camera tracks (from the latest edit on any camera track to the next one). split = the shot starts
// at the CTI instead (only the part from the playhead to the next edit changes). Every camera track is razored at
// the shot edges where needed, then `cam` is enabled there and the others disabled. Only on Klipora results.
function bac_podcast_switchAt(cam, cams, split, seqId) {
    try {
        var seq = acSeq(seqId);
        if (!seq) return acErr(acT("podcastOpenSeq", "Buka sequence dulu."));
        if (!acPodIsResult(seq)) return acErr(acT("podcastOriginal", "Ini sequence asli. Buka sequence hasil (Klipora) dulu, sequence asli tidak diubah."));
        cam = Number(cam);
        var fps = tlFps(seq), tpf = Number(seq.timebase), tol = 0.5 / fps, i, ei, ti, list, k;
        var frame = Math.round(Number(seq.getPlayerPosition().ticks) / tpf), t = frame / fps, probe = t + tol;
        if (cam < 0 || cam >= seq.videoTracks.numTracks) return acErr(acT("podcastNoTrackHere", "Track kamera V{0} tidak ada di sequence ini.", cam + 1));
        if (acPodAt(acPodClips(seq.videoTracks[cam]), probe) < 0) return acErr(acT("podcastNoClip", "Track V{0} tidak punya clip di playhead.", cam + 1));
        var tracks = [], a = -1, b = -1;
        for (i = 0; i < cams.length; i++) {
            ti = Number(cams[i]);
            if (ti < 0 || ti >= seq.videoTracks.numTracks) continue;
            list = acPodClips(seq.videoTracks[ti]);
            k = acPodAt(list, probe);
            if (k < 0) continue;
            tracks.push(ti);
            if (a < 0 || list[k].s > a) a = list[k].s;
            if (b < 0 || list[k].e < b) b = list[k].e;
        }
        var fa = split ? frame : Math.round(a * fps), fb = Math.round(b * fps), edges = [fa, fb], cut = 0, q = null;
        for (i = 0; i < tracks.length; i++) {
            list = acPodClips(seq.videoTracks[tracks[i]]);
            for (ei = 0; ei < edges.length; ei++) {
                if (acPodFind(list, edges[ei] / fps, tol) < 0) continue;
                if (!q) q = acPodQe(seq);
                var qt = q.getVideoTrackAt(tracks[i]), lk = qt.isLocked();
                if (lk) qt.setLock(false);
                qt.razor(tlTimecode(seq, edges[ei]));
                if (lk) qt.setLock(true);
                cut++;
            }
        }
        if (q) seq.setPlayerPosition(String(frame * tpf));
        var changed = 0;
        for (i = 0; i < tracks.length; i++) {
            ti = tracks[i];
            list = acPodClips(seq.videoTracks[ti]);
            k = acPodAt(list, probe);
            if (k < 0) continue;
            var want = ti !== cam, qa = null;
            if (!!list[k].item.disabled === want) continue;
            if (seq.videoTracks[ti].isLocked()) { qa = (q || (q = acPodQe(seq))).getVideoTrackAt(ti); qa.setLock(false); }
            list[k].item.disabled = want;
            if (qa) qa.setLock(true);
            changed++;
        }
        return acOk({ ok: true, t: t, cam: cam, changed: changed, found: tracks.length, cut: cut, from: fa / fps, to: fb / fps });
    } catch (e) { return acCatch(e, "bac_podcast_switchAt"); }
}

// Which of `cams` is visible at the CTI (first enabled clip from the TOP track down), plus whether the active
// sequence may be edited by the switch buttons. Read only.
function bac_podcast_camAt(cams, seqId) {
    try {
        var seq = acSeq(seqId);
        if (!seq) return "null";
        var fps = tlFps(seq), t = seq.getPlayerPosition().seconds, probe = Math.round(t * fps) / fps + 0.5 / fps;
        var order = [], i, visible = null, under = [];
        for (i = 0; i < cams.length; i++) order.push(Number(cams[i]));
        order.sort(function (a, b) { return b - a; });
        for (i = 0; i < order.length; i++) {
            if (order[i] < 0 || order[i] >= seq.videoTracks.numTracks) continue;
            var list = acPodClips(seq.videoTracks[order[i]]), k = acPodAt(list, probe);
            if (k < 0) continue;
            under.push(order[i]);
            if (visible === null && !list[k].item.disabled) visible = order[i];
        }
        return acOk({ ok: true, t: t, id: seq.sequenceID, name: seq.name, result: acPodIsResult(seq), visible: visible, under: under });
    } catch (e) { return acCatch(e, "bac_podcast_camAt"); }
}
