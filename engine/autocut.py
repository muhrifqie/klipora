"""Legacy silence-cut CLI (Klipora) (kept for the current panel): detect silence with FFmpeg, export a cut timeline
(FCP7 XML) for Premiere/Resolve. Thin wrapper over ac.media / ac.xmeml; the new silence tool lives in
ac/tools/silence.py and runs through cli.py.

  python engine/autocut.py <media> [--threshold -35] [--min-silence 0.5] [--margin 0.2] [--render]
Writes <stem>_autocut.xml and <stem>_autocut.json next to the media (unchanged contract).
"""
import argparse
import json
import re
import subprocess
import sys
from fractions import Fraction
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from ac import media as _media  # noqa: E402
from ac import xmeml as _xmeml  # noqa: E402
from ac.i18n import tr  # noqa: E402
from ac.util import EngineError, ensure_free, ffmpeg_exe, run  # noqa: E402


def probe(path):
    """Legacy shape: fps as Fraction, SystemExit when the file has no audio."""
    try:
        info = _media.probe(path)
    except EngineError as e:
        raise SystemExit(e.msg) from None
    if not info["has_audio"]:
        raise SystemExit(tr("err.noAudio.file", name=Path(path).name))
    return {
        "duration": info["duration"],
        "fps": Fraction(info["fps_frac"]) if info["has_video"] else Fraction(30),
        "width": info["width"] or 1920,
        "height": info["height"] or 1080,
        "has_video": info["has_video"],
        "sample_rate": info["sample_rate"],
        "channels": info["channels"],
    }


def detect_silence(path, threshold_db=-35.0, min_silence=0.5):
    """Return [(start, end)] of silent ranges in seconds (FFmpeg silencedetect, fixed dB)."""
    err = run([ffmpeg_exe(), "-hide_banner", "-nostats", "-i", str(path), "-vn",
               "-af", f"silencedetect=noise={threshold_db}dB:d={min_silence}", "-f", "null", "-"],
              text=True).stderr or ""
    starts = [float(x) for x in re.findall(r"silence_start: (-?[\d.]+)", err)]
    ends = [float(x) for x in re.findall(r"silence_end: ([\d.]+)", err)]
    # A trailing silence has a start but no end; caller clamps it to duration.
    return [(max(0.0, s), ends[i] if i < len(ends) else float("inf")) for i, s in enumerate(starts)]


def keep_segments(silences, duration, margin=0.2, min_keep=0.1):
    """Invert silent ranges into kept ranges, drop blips shorter than min_keep, pad by margin, merge."""
    keeps, cursor = [], 0.0
    for s, e in silences:
        if s > cursor:
            keeps.append((cursor, s))
        cursor = max(cursor, e)
    if cursor < duration:
        keeps.append((cursor, duration))

    merged = []
    for s, e in keeps:
        if e - s < min_keep:  # clicks, encoder padding at file end
            continue
        s, e = max(0.0, s - margin), min(duration, e + margin)
        if merged and s <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], e))
        else:
            merged.append((s, e))
    return merged


def to_xmeml(path, info, segments, name=None):
    """FCP7 XML (xmeml v4) sequence with one clip per kept segment (single source). See ac.xmeml."""
    return _xmeml.to_xmeml(path, info, segments, name)


def select_expr(segments, group=20):
    """OR of between() terms, nested in parens: FFmpeg's expression parser fails past ~100 flat terms.
    group=20 measured OK up to 20000 cuts; group=50 broke at 6000."""
    terms = [f"between(t,{s:.3f},{e:.3f})" for s, e in segments]
    while len(terms) > group:
        terms = ["(" + "+".join(terms[i:i + group]) + ")" for i in range(0, len(terms), group)]
    return "+".join(terms)


def render(path, segments, out, has_video=True):
    """Render kept segments straight to a media file (no editor needed)."""
    kept = sum(e - s for s, e in segments)
    try:  # rough upper bound: 20 Mbit/s for video, 320 kbit/s audio-only
        ensure_free(int(kept * (2.5e6 if has_video else 4e4)), Path(out).parent)
    except EngineError as e:
        raise SystemExit(e.msg) from None
    expr = select_expr(segments)
    graph = f"[0:a]aselect='{expr}',asetpts=N/SR/TB[a]"
    maps = ["-map", "[a]"]
    if has_video:
        graph = f"[0:v]select='{expr}',setpts=N/FRAME_RATE/TB[v];" + graph
        maps = ["-map", "[v]"] + maps
    # Filter goes through a file: hundreds of cuts overflow the 32K Windows command line.
    script = Path(out).with_suffix(".filter.txt")
    script.write_text(graph, encoding="utf-8")
    base = [ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-stats", "-y", "-i", str(path),
            "-/filter_complex", str(script), *maps]
    try:
        for venc in (["-c:v", "h264_nvenc", "-preset", "p5", "-cq", "21"], ["-c:v", "libx264", "-crf", "20"]):
            if subprocess.run(base + (venc if has_video else []) + [str(out)]).returncode == 0:
                return
            if not has_video:
                break
            print(tr("legacy.nvencRetry"))
        raise SystemExit(tr("legacy.renderFailed"))
    finally:
        script.unlink(missing_ok=True)


def main():
    p = argparse.ArgumentParser(description="Remove silence and export a cut timeline.")
    p.add_argument("input")
    p.add_argument("--threshold", type=float, default=-35.0, help="silence level in dB (default -35)")
    p.add_argument("--min-silence", type=float, default=0.5, help="min silence length in s (default 0.5)")
    p.add_argument("--margin", type=float, default=0.2, help="padding kept around speech in s (default 0.2)")
    p.add_argument("--render", action="store_true", help="also render a cut mp4")
    args = p.parse_args()

    src = Path(args.input)
    if not src.is_file():
        raise SystemExit(tr("legacy.fileNotFound", path=str(src.resolve())))
    info = probe(src)
    silences = detect_silence(src, args.threshold, args.min_silence)
    segs = keep_segments(silences, info["duration"], args.margin)
    kept = sum(e - s for s, e in segs)

    xml_out = src.with_name(f"{src.stem}_autocut.xml")
    xml_out.write_text(to_xmeml(src, info, segs), encoding="utf-8")
    json_out = src.with_name(f"{src.stem}_autocut.json")
    json_out.write_text(json.dumps({"source": str(src.resolve()), "duration": info["duration"],
                                    "segments": segs}, indent=1), encoding="utf-8")
    print(f"{len(segs)} segments, {info['duration']:.1f}s -> {kept:.1f}s "
          f"(-{info['duration'] - kept:.1f}s)\n{xml_out}\n{json_out}")

    if args.render:
        mp4_out = src.with_name(f"{src.stem}_autocut{src.suffix}")
        render(src, segs, mp4_out, info["has_video"])
        print(mp4_out)


if __name__ == "__main__":
    main()
