"""ac.ranges: interval algebra."""
import _common  # noqa: F401

from ac import ranges as R

assert R.norm([[3, 4], [1, 2], [2, 2.5], [5, 5], [6, 5.5]]) == [[1.0, 2.5], [3.0, 4.0]]
assert R.merge_gaps([[0, 1], [1.2, 2], [3, 4]], 0.3) == [[0.0, 2.0], [3.0, 4.0]]
assert R.union([[0, 1]], [[0.5, 2]], [[3, 4]]) == [[0.0, 2.0], [3.0, 4.0]]
assert R.intersect([[0, 5], [6, 8]], [[1, 2], [4, 7]]) == [[1.0, 2.0], [4.0, 5.0], [6.0, 7.0]]
assert R.subtract([[0, 10]], [[1, 2], [5, 6], [9, 12]]) == [[0.0, 1.0], [2.0, 5.0], [6.0, 9.0]]
assert R.subtract([[0, 1], [2, 3]], []) == [[0.0, 1.0], [2.0, 3.0]]
assert R.invert([[1, 2], [5, 6]], 0, 8) == [[0.0, 1.0], [2.0, 5.0], [6.0, 8.0]]
assert R.invert([], 0, 3) == [[0.0, 3.0]]
assert R.pad([[1, 2], [2.3, 3]], 0.1, 0.2, 0, 3.1) == [[0.9, 3.1]]
assert R.pad([[1, 2]], -0.2, -0.2) == [[1.2, 1.8]]
assert R.drop_short([[0, 0.05], [1, 2]], 0.1) == [[1.0, 2.0]]
assert abs(R.total([[0, 1], [0.5, 2], [3, 3.5]]) - 2.5) < 1e-9
assert R.contains([[1, 2], [3, 4]], 3.5) and not R.contains([[1, 2]], 2.0) and R.contains([[1, 2]], 1.0)
assert R.overlap(0, 2, 1, 5) == 1 and R.overlap(0, 1, 2, 3) == 0
# frame snapping at 120 fps: removed ranges shrink (inner), kept ranges grow (outer)
fps = 120
inner = R.to_frames([[1.004, 2.006]], fps, "inner")
assert inner == [[121 / fps, 240 / fps]], inner
outer = R.to_frames([[1.004, 2.006]], fps, "outer")
assert outer == [[120 / fps, 241 / fps]], outer
assert R.to_frames([[1.001, 1.005]], fps, "inner") == []          # vanishes: no frame fully inside
# ripple mapper: removing [2,3] and [5,7]
m = R.ripple_mapper([[5, 7], [2, 3]])
assert [m(t) for t in (1, 2.5, 3, 4, 6, 8)] == [1, 2, 2, 3, 4, 5]
assert R.snap_edges([[1.02, 2.04]], lambda t: round(t, 1)) == [[1.0, 2.0]]
assert R.round_list([[1.23456, 2.5]], 2) == [[1.23, 2.5]]
# subtract with many ranges stays exact
big = [[i, i + 0.5] for i in range(0, 2000)]
left = R.subtract([[0, 2000]], big)
assert len(left) == 2000 and abs(R.total(left) - 1000) < 1e-6
print("ok")
