from caption import apply_fixes, chunk, map_to_timeline, srt_time

# brand fix across two words, keeps leading space / trailing punctuation, tolerates "kita" vs "kitah"
w = [[" buka", 0, 1, 0], [" toko", 1, 2, 0], [" Kita,", 2, 3, 1], [" lalu", 3, 4, 0]]
assert apply_fixes(w, [("toko kitah", "TokoKita")]) == [[" buka", 0, 1, 0], [" TokoKita,", 1, 3, 1], [" lalu", 3, 4, 0]]
# short words are not fuzzy-matched
assert apply_fixes([[" ya", 0, 1, 0]], [("yah", "iya")]) == [[" ya", 0, 1, 0]]

# timeline mapping: second clip uses source 10-12s placed at timeline 2s; word in the cut gap is dropped
words = {"a.mp4": [[" satu", 0.5, 1.0, 0], [" dibuang", 5.0, 5.5, 0], [" dua", 10.5, 11.0, 1]]}
clips = [{"path": "a.mp4", "start": 0, "end": 2, "in": 0, "out": 2},
         {"path": "a.mp4", "start": 2, "end": 4, "in": 10, "out": 12}]
assert map_to_timeline(clips, words) == [[" satu", 0.5, 1.0, 0], [" dua", 2.5, 3.0, 1]]

# chunking: break at sentence end, Whisper phrase end (no pause left after cuts), long pause
caps = chunk([[" Halo", 0, 0.4, 0], [" semua.", 0.4, 0.8, 0], [" Kita", 0.9, 1.2, 1],
              [" mulai", 1.2, 1.5, 0], [" lagi", 3.0, 3.4, 0]])
assert [c[0] for c in caps] == ["Halo semua.", "Kita", "mulai", "lagi"], caps
assert all(a[2] <= b[1] for a, b in zip(caps, caps[1:]))

assert srt_time(3725.5) == "01:02:05,500"
print("ok")
