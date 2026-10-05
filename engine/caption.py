"""Legacy auto-caption CLI (kept for the current panel): transcribe source media, map words onto timeline
clips, write SRT. Thin wrapper over ac.transcript (words cache v3, GPU lock, hallucination filter); the new
caption tool lives in ac/captions and runs through cli.py.

clips.json = [{"path", "start", "end", "in", "out"}] in seconds (timeline start/end, source in/out),
so captions line up with any sequence, including Klipora sequences made of many cuts.
Prints "PROGRESS n" lines and "<n> caption" (parsed by the legacy panel; keep that format).
Error messages follow AC_LANG (ac.i18n).
"""
import argparse
import json
import string
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from ac import transcript as _T  # noqa: E402
from ac.i18n import tr  # noqa: E402
from ac.util import EngineError, cuda_setup  # noqa: E402

MODEL = "large-v3-turbo"
PUNCT = string.punctuation + "“”‘’…"
CACHE_VERSION = _T.CACHE_V


def _cuda_dlls():
    """Kept for old imports: cuBLAS/cuDNN from the pip wheels (see ac.util.cuda_setup)."""
    cuda_setup()


def progress(pct):
    print(f"PROGRESS {pct:.0f}", flush=True)


class _LegacyEmit:
    """Minimal emitter for ac.transcript: maps 0..100 onto done..done+share as 'PROGRESS n' lines."""

    def __init__(self, done, share):
        self.done, self.share, self.last = done, share, -1

    def progress(self, pct, note=None, force=False):
        v = int(self.done + self.share * max(0.0, min(100.0, pct)) / 100)
        if v != self.last:
            self.last = v
            progress(v)

    def wait(self, note):
        print(note, flush=True)

    def warn(self, msg):
        print(msg, flush=True)

    log = warn

    def done_note(self, note):
        pass

    def check_cancel(self):
        pass


def transcribe(path, model_name=MODEL, lang="id", done=0.0, share=100.0):
    """Word list [[text, start, end, segment_end]] for a media file (legacy 4-field rows from the v3 cache).
    segment_end=1 marks Whisper's phrase boundary: the only sentence cue left once silences are cut."""
    try:
        rows = _T.words(path, model=model_name, lang=lang, emit=_LegacyEmit(done, share))
    except EngineError as e:
        raise SystemExit(e.msg) from None
    progress(done + share)
    return [[r[0], r[1], r[2], r[3]] for r in rows]


def _same(heard, want):
    """Whisper wobbles on unknown names ("kuih" vs "kui"): allow a 1-letter tail difference."""
    if heard == want:
        return True
    a, b = sorted((heard, want), key=len)
    return len(a) >= 3 and len(b) - len(a) <= 1 and b.startswith(a)


def apply_fixes(words, fixes):
    """fixes: [(wrong phrase, right)] matched case-insensitively across consecutive words."""
    norm = lambda t: t.strip().strip(PUNCT).lower()  # noqa: E731
    for wrong, right in fixes:
        target = wrong.lower().split()
        n, out, i = len(target), [], 0
        while i < len(words):
            chunk = words[i:i + n]
            if n and len(chunk) == n and all(_same(norm(w[0]), t) for w, t in zip(chunk, target)):
                last = chunk[-1][0].strip()
                tail = last[len(last.rstrip(PUNCT)):]
                lead = " " if chunk[0][0].startswith(" ") else ""
                out.append([lead + right + tail, chunk[0][1], chunk[-1][2], chunk[-1][3]])
                i += n
            else:
                out.append(words[i])
                i += 1
        words = out
    return words


def map_to_timeline(clips, words_by_path):
    """Keep words whose midpoint falls inside each clip's source range, shifted to timeline time."""
    out = []
    for c in clips:
        for w in words_by_path[c["path"]]:
            text, ws, we, seg_end = w[0], w[1], w[2], w[3]
            if c["in"] <= (ws + we) / 2 < c["out"]:
                ts = c["start"] + max(ws, c["in"]) - c["in"]
                te = c["start"] + min(we, c["out"]) - c["in"]
                out.append([text, ts, max(te, ts + 0.05), seg_end])
    return sorted(out, key=lambda w: w[1])


def chunk(words, max_chars=32, max_gap=0.6, max_dur=3.5):
    """Group words into caption lines: break on length, pauses, long duration, or sentence end."""
    lines, cur = [], []
    for w in words:
        if cur:
            text = "".join(x[0] for x in cur + [w]).strip()
            if (len(text) > max_chars or w[1] - cur[-1][2] > max_gap
                    or w[2] - cur[0][1] > max_dur or cur[-1][3] or cur[-1][0].strip()[-1:] in ".?!"):
                lines.append(cur)
                cur = []
        cur.append(w)
    if cur:
        lines.append(cur)
    caps = [["".join(x[0] for x in ln).strip(), ln[0][1], ln[-1][2]] for ln in lines]
    for a, b in zip(caps, caps[1:]):  # no overlaps
        a[2] = min(a[2], b[1])
    return caps


def srt_time(t):
    ms = round(t * 1000)
    return f"{ms // 3600000:02}:{ms // 60000 % 60:02}:{ms // 1000 % 60:02},{ms % 1000:03}"


def to_srt(caps):
    return "\n".join(f"{i}\n{srt_time(s)} --> {srt_time(e)}\n{t}\n" for i, (t, s, e) in enumerate(caps, 1))


def parse_fixes(text):
    fixes = []
    for line in (text or "").replace(";", "\n").splitlines():
        if "=" in line:
            wrong, right = (x.strip() for x in line.split("=", 1))
            if wrong and right:
                fixes.append((wrong, right))
    return fixes


def main():
    p = argparse.ArgumentParser(description="Auto caption a timeline (Indonesian by default).")
    p.add_argument("clips", help="clips.json from the panel")
    p.add_argument("out", help="output .srt")
    p.add_argument("--max-chars", type=int, default=32)
    p.add_argument("--model", default=MODEL)
    p.add_argument("--lang", default="id")
    p.add_argument("--fix", default="", help='"salah = benar" pairs, separated by newline or ;')
    args = p.parse_args()

    clips = json.loads(Path(args.clips).read_text(encoding="utf-8"))
    if not clips:
        raise SystemExit(tr("legacy.noClips"))
    fixes = parse_fixes(args.fix)
    paths = list(dict.fromkeys(c["path"] for c in clips))
    words_by_path = {}
    for i, path in enumerate(paths):
        if not Path(path).is_file():
            raise SystemExit(tr("err.noMedia.msg", path=path))
        share = 100 / len(paths)
        words_by_path[path] = apply_fixes(transcribe(path, args.model, args.lang, i * share, share), fixes)

    caps = chunk(map_to_timeline(clips, words_by_path), args.max_chars)
    Path(args.out).write_text(to_srt(caps), encoding="utf-8")
    print(f"{len(caps)} caption\n{args.out}")


if __name__ == "__main__":
    main()
