"""Reference tool for integration tests (panel foundation, cli, worker). Not shown to users.

Actions:
  analyze  stages + progress, writes a review file of fake findings (or real pauses from the sequence audio
           when params.use_audio is true) and returns {"review": path, "summary", "items_preview", ...}
  apply    reads the edited review (job["review"]) -> {"plan": {"kind": "remove_ranges", "ranges", "timebase"}}
  slow     ~params.seconds of progress (cancel test: emit.progress raises Cancelled when cancelled)
  fail     raises EngineError(params.code or "ECHO_FAIL") (error-path test)
  crash    raises a plain exception (internal-error path test)
  echo     returns the job's params unchanged
Params (analyze): every (s, default 5), len (s, default 0.6), use_audio (bool), seconds (s, default 0.6).
"""
from __future__ import annotations

import time

from .. import review as RV
from ..timeline import Timeline
from ..i18n import tr
from ..util import EngineError, fmt_sec, workdir

TITLE = "Echo (tes integrasi)"     # fallback; `cli.py tools` shows tool.echo.title / tool.echo.desc
DESCRIPTION = "Tool contoh: hasil palsu untuk menguji panel, job runner dan worker."  # i18n-ignore


def _timeline(job):
    seq = job.get("seq") or {}
    if seq.get("video") or seq.get("audio"):
        return Timeline.from_json(seq)
    media = (job.get("params") or {}).get("media")
    if media:
        return Timeline.from_media(media)
    return Timeline({"name": "Echo", "fps": 30, "duration": 30.0, "video": [], "audio": []})


def analyze(job, emit):
    p = job.get("params") or {}
    emit.plan([("read", tr("stage.readSeq"), 0.1), ("scan", tr("echo.scan"), 0.7), ("review", tr("stage.reviewPrep"), 0.2)])
    with emit.step("read"):
        tl = _timeline(job)
        emit.progress(100)
    items = []
    with emit.step("scan"):
        steps = 10
        for k in range(steps):
            time.sleep(float(p.get("seconds", 0.6)) / steps)
            emit.progress(100 * (k + 1) / steps)
        if p.get("use_audio") and tl.audio_clips():
            from .. import media as M
            db = tl.envelope_on_timeline()
            thr = M.otsu_db(db)
            sound = M.voiced_spans(db, thr)
            from .. import ranges as R
            for a, b in R.invert(sound, 0.0, tl.duration):
                if b - a >= 1.0:
                    items.append(RV.item(a, b, "gap", on=True, conf=0.9, label=tr("echo.pause", dur=fmt_sec(b - a))))
            emit.done_note(tr("note.threshold", db=f"{thr:.0f}"))
        else:
            every, ln = float(p.get("every", 5.0)), float(p.get("len", 0.6))
            t = every / 2
            k = 0
            while t + ln < tl.duration:
                kind = ("gap", "filler", "repeat")[k % 3]
                items.append(RV.item(t, t + ln, kind, on=(k % 4 != 3), conf=round(0.5 + 0.1 * (k % 5), 2),
                                     label=tr("echo.sample", kind=kind, k=k + 1),
                                     ctx={"pre": tr("echo.wordBefore"), "post": tr("echo.wordAfter")}))
                t += every
                k += 1
    with emit.step("review"):
        doc = RV.new("echo", items, tl.duration, seq=tl, params=p)
        path = RV.save(doc, RV.path_for(workdir(job, tl.name), "echo"))
        emit.progress(100)
    return {"review": str(path), "summary": RV.summary(doc), "duration": tl.duration,
            "items_preview": doc["items"][:5]}


def apply(job, emit):
    with emit.step("plan", tr("stage.cutPlan")):
        doc = RV.from_job(job)
        rng = RV.selected_ranges(doc)
        emit.progress(100)
    return {"plan": {"kind": "remove_ranges", "ranges": [[round(a, 3), round(b, 3)] for a, b in rng],
                     "timebase": "sequence"},
            "summary": RV.summary(doc)}


def slow(job, emit):
    secs = float((job.get("params") or {}).get("seconds", 5.0))
    with emit.step("wait", tr("echo.waiting")):
        t0 = time.time()
        while time.time() - t0 < secs:
            time.sleep(0.05)
            emit.progress(100 * (time.time() - t0) / secs, force=True)
    return {"slept": secs}


def fail(job, emit):
    p = job.get("params") or {}
    raise EngineError(p.get("code", "ECHO_FAIL"), p.get("msg") or tr("echo.failMsg"), tr("echo.failHint"))


def crash(job, emit):
    raise RuntimeError("echo crash (test)")


def echo(job, emit):
    return {"params": job.get("params") or {}}


ACTIONS = {"analyze": analyze, "apply": apply, "slow": slow, "fail": fail, "crash": crash, "echo": echo}
