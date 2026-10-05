"""Shared test setup: import path, test media, scratch folder, tiny assertion helpers.
Tests are plain scripts (python engine/tests/test_x.py) and never touch Premiere or the GPU unless asked."""
import atexit
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

ENGINE = Path(__file__).resolve().parents[1]
ROOT = ENGINE.parent
sys.path.insert(0, str(ENGINE))

# Real test media (optional). KLIPORA_TEST_MEDIA = a folder with:
#   talk_49s.mp4  ~49 s screen-recorded tutorial with Indonesian speech (most media tests)
#   talk_35m.mp4  ~35 min tutorial (long-file paths: chapters, profanity, podcast frame checks)
#   seq_2m.mp4    ~2 min talking clip (fillers, podcast fixture, chapters meta)
#   media.json    optional: {"talk_49s": "D:/any/clip.mp4", ..., "glossary": ["BrandSaidInClip"]} maps the names
#                 above to files elsewhere (relative paths resolve against the folder) and lists brand terms the
#                 speaker actually says (used by glossary checks on the real transcript).
# Missing media -> those tests print "skip: ... set KLIPORA_TEST_MEDIA" and pass; synth_clip() is the fallback
# for tests that only need some audio/video.
MEDIA_NAMES = {"talk_49s": "talk_49s.mp4", "talk_35m": "talk_35m.mp4", "seq_2m": "seq_2m.mp4"}
MEDIA_DIR = Path(os.environ["KLIPORA_TEST_MEDIA"]) if os.environ.get("KLIPORA_TEST_MEDIA") else None


def _media_conf():
    if MEDIA_DIR is None:
        return {}
    try:
        import json
        return json.loads((MEDIA_DIR / "media.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


MEDIA_CONF = _media_conf()


def _media(key):
    if MEDIA_DIR is None:   # placeholder that never exists: have() / is_file() report it missing
        return Path(tempfile.gettempdir()) / "klipora_no_test_media" / MEDIA_NAMES[key]
    p = Path(MEDIA_CONF.get(key) or MEDIA_NAMES[key])
    return p if p.is_absolute() else MEDIA_DIR / p


MEDIA_49 = _media("talk_49s")
MEDIA_34M = _media("talk_35m")
MEDIA_2M = _media("seq_2m")
# brand terms spoken in the real clip (media.json "glossary"); empty -> real-transcript glossary checks are skipped
MEDIA_GLOSSARY = [g for g in MEDIA_CONF.get("glossary") or [] if isinstance(g, str) and g]
LAB = ROOT / "proto" / "lab" / "out"   # internal lab outputs (not in the public repo): tests skip without them


def scratch(name):
    """Fresh folder under %TEMP%\\autocut_tests\\<name>_<pid> (deleted first and at exit unless AC_TEST_KEEP=1)."""
    d = _scratch_path(name)
    for old in d.parent.glob(f"{name}_*"):     # leftovers of earlier runs (files a late writer recreated after exit)
        try:
            if old != d and old.name[len(name) + 1:].isdigit() and time.time() - old.stat().st_mtime > 3600:
                shutil.rmtree(old, ignore_errors=True)
        except OSError:
            pass
    shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True, exist_ok=True)
    if os.environ.get("AC_TEST_KEEP") != "1":
        atexit.register(shutil.rmtree, d, True)
    return d


def _scratch_path(name):
    # per process (<name>_<pid>): several agents / run_all runs test at once and must not delete each other's files
    return Path(tempfile.gettempdir()) / "autocut_tests" / f"{name}_{os.getpid()}"


def cleanup(name):
    shutil.rmtree(_scratch_path(name), ignore_errors=True)


def have(*paths):
    missing = [Path(p).name for p in paths if not Path(p).is_file()]
    if missing:
        media = {MEDIA_49.name, MEDIA_34M.name, MEDIA_2M.name}
        hint = ", set KLIPORA_TEST_MEDIA" if any(m in media for m in missing) else ""
        print(f"skip: missing {', '.join(missing)}{hint}")
    return not missing


def synth_clip(seconds=12.0, w=640, h=360, fps=30):
    """Tiny synthetic test clip (FFmpeg lavfi): moving test pattern + a 440 Hz tone in 1.5 s bursts separated
    by 0.8 s of silence (so silence / envelope code sees real gaps). Cached in %TEMP%/autocut_tests/_synth.
    Returns the Path, or None when FFmpeg is missing or fails."""
    import subprocess
    out = Path(tempfile.gettempdir()) / "autocut_tests" / "_synth" / f"synth_{seconds:g}s_{w}x{h}_{fps}.mp4"
    if out.is_file() and out.stat().st_size > 0:
        return out
    exe = shutil.which("ffmpeg")
    if not exe:
        return None
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(f"{out.stem}.{os.getpid()}.mp4")
    cmd = [exe, "-hide_banner", "-loglevel", "error", "-y",
           "-f", "lavfi", "-i", f"testsrc2=size={w}x{h}:rate={fps}:duration={seconds}",
           "-f", "lavfi", "-i", f"sine=frequency=440:sample_rate=48000:duration={seconds}",
           "-af", "volume='if(lt(mod(t,2.3),1.5),0.5,0)':eval=frame",
           "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "96k",
           "-shortest", str(tmp)]
    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=120)
        os.replace(tmp, out)
    except (OSError, subprocess.SubprocessError):
        tmp.unlink(missing_ok=True)
        return None
    return out


def approx(a, b, tol=1e-6):
    return abs(float(a) - float(b)) <= tol


def flag(name):
    return name in sys.argv[1:] or os.environ.get("AC_TEST_" + name.strip("-").upper()) == "1"
