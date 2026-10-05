# Suara Jernih / Clear Voice (tool id `voice`)

Owner: voice tool agent. Files: `engine/ac/tools/voice.py`, `engine/ac/tools/_voice_dsp.py`,
`engine/ac/tools/_voice_detect.py`, `engine/ac/tools/_voice_df.py`, `engine/ac/assets/models/deepfilternet3/*`,
`engine/ac/assets/models/rnnoise_{sh,bd}.rnnn`, `engine/tests/test_voice.py`, `panel/js/tools/voice.js`,
`panel/css/tools/voice.css`, `panel/host/43_voice.jsx`, `tools/ui_tests/voice.mjs`.
Shared edits: two tags in `panel/index.html` (css + js, next to broll). Registered in group Potong, order 5.

## Klipora names and languages

- The product is called **Klipora**. Results: clone `<seq> (Klipora)`, output track `Klipora Suara`, bin `Klipora`,
  review markers `[Klipora-VC-RV]`. These Premiere names are the same in both UI languages.
- Projects made before the rename keep working: a re-run reuses an old `AutoCut Suara` track (host `acNameIs`, engine
  `TRACK_NAMES`), and every helper track named `Klipora ...` or `AutoCut ...` is ignored when choosing the voice and
  music tracks. Profiles stay in `voice_profiles.json`, now under `%APPDATA%\Klipora` (migrated by the foundation).
- UI language: Indonesian or English (Settings > Display language, default follows Premiere). Panel texts are locale
  keys `voice.*` / `host.voice*`, engine texts (stages, review item labels "Klik" / "Click", "Napas" / "Breath",
  advice, warnings, errors, summary) are `voice.*` in `engine/ac/locales`, picked per job (`job.lang`). English names:
  tool "Clear Voice" (tab "Voice"), presets Natural / Podcast / Strong, "My Voice" profiles, "Match EQ from a
  sample". The labels quoted below are the Indonesian UI.

## What it does

Makes the creator's voice pleasant to listen to, non-destructively. The voice track range is rendered by the engine
to one processed WAV; the host clones the sequence ("<seq> (Klipora)"), disables the original voice clips on the
clone, puts the WAV on a new audio track "Klipora Suara" at the same sequence time, and ducks music clips with
volume keyframes while the voice speaks. The original sequence and media are never changed; nothing is saved.

Pipeline (sequence time, 48 kHz mono internally, dual-mono 16-bit WAV out):

1. Voice track: the audio track with the most transcript words (cached transcripts only), plus its twins (tracks
   carrying the same clips, e.g. a stereo pair split on A1 + A2): the twins are disabled too, only one is rendered.
   Tracks named "Klipora Suara" (or the old "AutoCut Suara") never count; when one exists with clips the source is a re-run (the original voice
   clips are disabled there, so disabled clips are used again).
2. Clicks / breaths (`analyze`, Tinjau list): never inside a word. Words = main transcript + the filler listener
   pass (`_listen.json`, widened 0,1 dtk), +-40 ms guard. Without a transcript the guard is the envelope (every
   loud run) and breaths are not searched.
   * Click: 2 ms frames of the > 2 kHz band at least 14 dB over its 300 ms running median, <= 60 ms, peak within
     6 ms of the onset, mostly high-frequency, and the full-band level must fall >= 10 dB within 6-60 ms (median)
     after it (a word or vocal sound that keeps going is not a click). Clicks closer than 0,35 dtk are one item
     ("3 klik", "Ketikan, 5 klik").
   * Breath: in a pause, 10 ms frames >= pause floor + 6 dB for 0,12-1,2 dtk, peak >= 8 dB under the speech level,
     unvoiced (autocorrelation < 0,5 at 80-400 Hz), spectral centroid >= 1,2 kHz, and not the decay tail of the
     previous word.
   * Attenuation: default -30 dB (klik) / -12 dB (napas) with 5 ms ramps, but never deeper than the sound around
     the item (`floor` = quieter side of 120 ms before/after minus the item level). A click on top of desktop audio
     is evened out to that background instead of leaving a hole (median on the 34,6 min file: -15 dB).
3. Denoise (`apply`): DeepFilterNet3 (ONNX, CPU, see below), strength = attenuation limit 6..50 dB, 100 % =
   unlimited. Fallbacks: RNNoise (`arnndn`, model "somnolent-hogwash" = speech in a normal room) then `afftdn`.
   Delays of the FFmpeg denoisers are measured by cross-correlation and removed (arnndn 480 samples, afftdn 1200).
4. Chain (FFmpeg, zero-latency filters only): highpass, pre-gain to -20 LUFS (mono) so compressor thresholds fit,
   mud cut 350 Hz, warmth 180 Hz, `acompressor` (RMS, 12/160 ms, threshold -22 dBFS at 2:1 .. -27 at 4,5:1),
   `deesser`, presence 3,5 kHz, air shelf 9 kHz, "Tiru EQ" octave bands (125 Hz .. 12 kHz, +-6 dB).
5. Loudness: limiter (`alimiter` at 4x oversampling ~ true peak) whose ceiling is computed from a BS.1770
   measurement so the second loudnorm pass can stay LINEAR (`measured TP + gain <= -1 dBTP`; limiting lowers the
   loudness slightly, so a second limiter pass corrects the ceiling when needed). Then `loudnorm` with the measured
   values, `linear=true` (= one static gain), stereo dual mono. The first loudnorm pass (measurement) is done in
   numpy (BS.1770-4 integrated loudness + 4x true peak, matches FFmpeg `ebur128` within 0,1 LU / 0,1 dB); the
   FFmpeg measurement pass was 36 s per pass on 34,6 min, numpy + limiter ~10 s.
6. Output: `<workdir>\suara\<seq>_suara_YYYYMMDD_HHMMSS.wav` (versioned name: Premiere locks imported files; old
   versions are kept because older AutoCut sequences may use them). 34,6 min = 380 MB.
7. Ducking: voice activity = transcript words merged over gaps < 0,6 dtk (envelope when no transcript), then
   merged over gaps < attack + release + 0,2 dtk. Music tracks: other tracks whose clip envelopes score as music
   (active share > 0,85, few syllable-rate dips; transcribed speech > 0,8 words/s never counts). The panel shows
   them as chips (auto ones pre-selected); keys: base -> base x 10^(dB/20) over `attack` before each span, back over
   `release` after it, in clip media time, linear.

## Settings (panel -> engine params)

| UI | Param | Default | Notes |
|---|---|---|---|
| Gaya suara Natural / Podcast / Kuat | `preset` + chain values | natural | `_voice_dsp.PRESETS` (the panel copy is checked by test_voice.py) |
| Target volume YouTube / Podcast / TikTok/Reels / Atur sendiri | `platform`, `lufs` | youtube, -14 | podcast -16, tiktok -14, custom -24..-9; true peak `tp` -1 dBTP |
| Hapus klik mouse & keyboard | `clicks` | on | review kind `click` |
| Kecilkan napas | `breaths` | on | review kind `breath` |
| Hilangkan noise | `denoise` | ai | ai / rnnoise / fft / off |
| Kecilkan musik saat bicara | `duck`, `duck_db` | on, -12 dB | `duck_tracks` = chips (null = auto) |
| Lanjutan: track suara | `voice_tracks` | auto | one index |
| Lanjutan: dengung bawah, noise %, de-esser %, kompresor, kejelasan, kehangatan, bindeng, kilau | `hp nr deess comp presence warmth mud air` | per preset | changing one shows "Custom" |
| Lanjutan: klik / napas dikecilkan | `click_db`, `breath_db` | -30, -12 dB | deepest; the item `floor` may stop earlier |
| Lanjutan: musik turun / naik dalam | `attack`, `release` | 0,25 / 0,6 dtk | |
| Pakai EQ tiruan | `eq` | [] | `[[freq, dB]]` from `match_eq` |

"Suara Saya": `%APPDATA%\Klipora\voice_profiles.json` = `{v:1, last, profiles:[{name, settings, saved}]}`
(written by the panel through `AC.sys`, `AC.voice.load/put/remove/use`). The last used profile is applied when the
page opens. "Tiru EQ dari contoh" stores the bands and the reference path in the profile too.

## Engine actions

| Action | Result |
|---|---|
| `tracks` | `{tracks:[{index,name,muted,clips,words,voice,render}], voice, mute, music:[{track,name,score,auto,info}], rerun, has_words}` (~ms, cached envelopes) |
| `analyze` | review `voice_review.json` (items `{kind: click/breath, t0, t1, on, conf, label, ctx, db, floor, n, peak}`), `stats: {before: {lufs, lra, tp, speech_db, noise_db}, range, voice, mute, music, clicks, breaths, advice}` |
| `apply` | renders the WAV; `plan: {kind:"voice", seq, wav, start, end, dur, track_name, render_tracks, mute_tracks, mute_clips:[{track,start,end,name}], duck:{tracks, ranges, db, attack, release} | null, rerun}`, `stats: {before, after, target, normalization, denoise, timing, preview:{before, after, t0}, size_mb}` |
| `preview` | 10 s `sebelum_*.wav` / `sesudah_*.wav` at `params.t` (default: the window with the most words), 3 s lead-in rendered and dropped |
| `snippet` | one A/B WAV for a review item: before, 0,35 s pause, after (`{path, dur, split}`) |
| `match_eq` | `{eq: [[125, dB] .. [12000, dB]], ref, ref_name, max}`: per-octave difference of the average speech spectra (max 4 min each), level neutral (250 Hz..4 kHz mean removed), smoothed, x0,8, clamped +-6 dB |

Errors: `NO_AUDIO` (no voice clips / track empty / too quiet), `NO_MEDIA` (reference missing), `TOO_LONG` (> 3 h),
`BAD_REVIEW`, `DISK_FULL` (checked before rendering: ~3x the WAV size for temp files), `FFMPEG`.

## Host functions (`panel/host/43_voice.jsx`, ES3)

| Function | Notes |
|---|---|
| `bac_voice_begin(srcId, name, trackName)` | clone + new audio track named via `Track.name` or QE `setName` (UNVERIFIED live); a re-run reuses and clears an existing "Klipora Suara" (or old "AutoCut Suara") track. Deletes its clone again on failure |
| `bac_voice_disable(seqId, clips)` | `clip.disabled = true` on the matching audio clips (+-1 frame); if Premiere also disables the linked video clip it is turned back on; if `disabled` cannot be set the clip Level becomes 0 (reported `muted`). Locked tracks reported |
| `bac_voice_place(seqId, trackIdx, wav, start)` | only an existing `.wav` (File.exists first: no modal), import into bin "Klipora" (old "AutoCut BOT" reused), `overwriteClip` |
| `bac_voice_duck(seqId, tracks, ranges, opts)` | Level keys per clip, old keys inside each window removed first; `opts.reset` (first chunk of a re-run) drops the previous duck curve |
| `bac_voice_probe(seqId, voiceTracks, outTrack, musicTracks, times)` | read-back for the verifier |

Panel flow (`voice.js`): settings -> `analyze` (CLI job) -> Tinjau (filters Klik / Napas, "Dengar A/B" per row,
"Lewati, proses tanpa ini") -> own `AC.Task`: engine `apply` (render) -> begin -> disable (chunks of 60) -> place ->
duck (chunks of 40) -> result card (LUFS before/after, "Suara di atas noise" = speech level minus pause noise,
Sebelum/Sesudah players, Buka yang asli, Ubah pilihan, Hapus hasil, Buka folder). A failure after cloning deletes
the clone and reopens the original. When analyze finds nothing to review, apply starts directly.

## AI denoise: options evaluated (no torch)

| Option | Result |
|---|---|
| DeepFilterNet3 ONNX via onnxruntime (CPU) | **chosen**. Official `models/DeepFilterNet3_onnx.tar.gz` from github.com/Rikorose/DeepFilterNet (8 MB: enc/erb_dec/df_dec.onnx). No libDF wheel for Python 3.14, so the DSP (Vorbis STFT 960/480, ERB features, unit norm, ERB mask, deep filter order 5 + lookahead 2) is ported to numpy in `_voice_df.py` (exact OLA reconstruction, 0 sample lag). 30 s chunks with 3 s lead-in and 0,25 s crossfade |
| RNNoise via FFmpeg `arnndn` | fallback. Models `sh.rnnn` / `bd.rnnn` (300 KB) from github.com/GregorR/rnnoise-models |
| FFmpeg `afftdn` / `anlmdn` | last fallback (`afftdn`); `anlmdn` was as slow as DeepFilterNet with less effect |

Quality, synthetic test (49 s clip speech + pink noise + 50 Hz hum), SI-SDR in dB (higher = better):

| Input SNR | noisy | DeepFilterNet3 | DF3 limit 18 dB | RNNoise sh | RNNoise bd | afftdn |
|---|---|---|---|---|---|---|
| 20 dB | 20,0 | **24,4** | 24,3 | 16,0 | 15,7 | 21,5 |
| 10 dB | 10,0 | **17,2** | 16,7 | 12,8 | 12,5 | 12,0 |
| 5 dB | 5,0 | **13,6** | 13,0 | 9,8 | 9,8 | 6,9 |

RNNoise makes clean-ish speech worse (16 < 20 dB) while DeepFilterNet3 still helps. The three test recordings are
already very clean (OBS noise suppression: pause floor -77 to -80 dBFS), so on them denoise mostly removes
residual hiss; the gain is in the speech-to-noise distance (see measurements).

## Measurements (this PC, 2026-10-05; CPU shared with other agents, times vary)

| What | 49 s | 2 min | 34,6 min |
|---|---|---|---|
| analyze (decode, detect, measure) | 1,8 s | 1,7 s | 11 s |
| apply total (render + checks) | 2,6 s | 4,8 s | 63-120 s |
| of which DeepFilterNet3 | 0,6 s | 1,4 s | 20-65 s (37x realtime in an idle run) |
| preview 10 s (cold CLI) | | | 1,9 s |
| clicks / breaths found | 8 / 2 | 23 / 1 | 330 / 26 |
| loudness before -> after (target -14) | -20,4 -> -14,0 LUFS | -20,8 -> -14,0 | -21,0 -> -14,0 |
| true peak before -> after | -2,1 -> -1,1 dBTP | -1,7 -> -1,1 | +0,4 -> -1,2 |
| speech minus pause noise | 55 -> 61 dB | 54 -> 56 dB | 52 -> 54 dB |
| loudnorm mode | linear | linear | linear |

Output stays sample-aligned with the source (cross-correlation lag <= 1 sample) and has the exact sequence length.

## Tests

- `python engine/tests/test_voice.py` (~6 s): settings, panel preset parity, DF STFT reconstruction and SI-SDR gain
  (> 4 dB at 5 dB SNR, across chunk seams), FFmpeg denoise lag, BS.1770 vs ebur128, gain curve, music score,
  injected clicks found (6/6, quiet to loud) and never inside words, voiced onset is not a click, injected breath
  found, twins / music / re-run track choice, analyze -> apply on the 49 s clip (+ synthetic music track ->
  duck plan): WAV 48 kHz stereo exact length, -14 +-0,5 LUFS, TP <= -0,9, linear, lag 0, temp files removed, a
  selected click 12 dB quieter relative to speech; preview, snippet, match_eq (self-match ~0 dB).
- `node tools/ui_test.mjs tools/ui_tests/voice.mjs` (live engine, 44 checks, ~1 min; also `--width 280`):
  page, presets/Custom, platform, profile save -> reload auto-load -> delete, preview players, Tiru EQ, Tinjau +
  seek + A/B, apply -> host calls (clone, disable, place) -> result, music track auto-ducked, host failure deletes
  the clone, no horizontal overflow; plus the real `43_voice.jsx` in node vm against a fake Premiere DOM (clone,
  disable with linked video kept, place, duck curve values, re-run reset, locked track, probe).
- Screens (gitignored `--shots` folder): `voice_main.png`, `voice_preview.png`, `voice_eq.png`, `voice_review.png`,
  `voice_result.png`, `voice_music.png`, `voice_main_narrow.png` (280 px).

## Known limits / UNVERIFIED live

- `TrackItem.disabled` on an audio clip of a linked A/V clip, the QE track rename, and `overwriteClip` of a stereo
  WAV on the appended track are UNVERIFIED in Premiere 26.2.2 (the verifier should check them first).
- Clip speed != 1 is resampled linearly (no pitch keep); time-remapped clips are not supported.
- One processed WAV per run covers the whole voice range; edits made later to the clone's timing do not move it.
- `tools/ui_tests/smoke.mjs` and `foundation.mjs` count 12 tools on Home; with this tool it is 13.
