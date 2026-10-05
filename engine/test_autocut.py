import subprocess
import tempfile
from pathlib import Path

from autocut import keep_segments, select_expr

# FFmpeg must accept an expression with thousands of cuts (flat sum fails past 100 terms)
e = select_expr([(i * 0.01, i * 0.01 + 0.005) for i in range(6000)])
assert e.count("between") == 6000
script = Path(tempfile.gettempdir()) / "autocut_expr_test.txt"
script.write_text(f"[0:a]aselect='{e}'[a]")
r = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "anullsrc",
                    "-t", "1", "-/filter_complex", str(script), "-map", "[a]", "-f", "null", "-"],
                   capture_output=True, text=True)
script.unlink()
assert r.returncode == 0, r.stderr[:300]

# speech 0-2, silence 2-5, speech 5-8, trailing silence 8-end
segs = keep_segments([(2.0, 5.0), (8.0, float("inf"))], 10.0, margin=0.2)
assert segs == [(0.0, 2.2), (4.8, 8.2)], segs

# margins that overlap get merged
assert keep_segments([(2.0, 2.3)], 5.0, margin=0.2) == [(0.0, 5.0)]

# leading silence
assert keep_segments([(0.0, 1.0)], 3.0, margin=0.0) == [(1.0, 3.0)]

# tiny blip after trailing silence (AAC padding) is dropped, not padded into a clip
assert keep_segments([(8.0, 10.005)], 10.01, margin=0.2) == [(0.0, 8.2)]

# all silent -> nothing kept
assert keep_segments([(0.0, float("inf"))], 3.0, margin=0.2) == []
print("ok")
