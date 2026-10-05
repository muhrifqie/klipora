"""Censor sounds for the "Sensor Kata Kasar" / "Bleep Profanity" tool: generated beep tones, the user's own sound
converted to a WAV we own, tone placement and the FFmpeg preview render ("Dengar" in the review list).

Tones (docs/research/product_ai.md section 4.3, MEASURED there): sine with an exact RMS level (no FFmpeg `sine`
source, which is fixed at -18 dBFS peak), 5 ms fades baked in so a clip never clicks, one file per 20 ms length
bucket (rounded UP so the whole word is covered), 48 kHz 16-bit STEREO (the lab placed a stereo beep on a new
QE audio track; mono would be upmixed with a -3 dB pan law). Files live in engine/ac/assets/sfx and are reused
by name, so a re-run imports nothing new. Underscore module name: not a tool for ac.tools.

Every sound file ends with TAIL seconds of digital silence after its fade-out. Premiere 26.2.2 (verified live)
floors a placed audio clip's length to whole sequence frames (a 380 ms beep became 45 frames = 375 ms at 120 fps,
366,7 ms at 30 fps), which cut off the baked fade-out and clicked at the end of every beep. The tail is longer than
one frame at 15 fps, so that cut always lands in silence. Returned lengths are the AUDIBLE length (without the tail).
"""
from __future__ import annotations

import math
import wave
from pathlib import Path

import numpy as np

from ..i18n import tr
from ..util import EngineError, appdata_dir, ensure_free, file_hash, run_ffmpeg

SR = 48000
SFX_DIR = Path(__file__).resolve().parents[1] / "assets" / "sfx"
FADE = 0.005
TAIL = 0.07           # silence after the fade-out (> 1 frame at 15 fps): Premiere floors clip length to frames
TONE_STEP = 0.02
TONE_MIN = 0.2
CUSTOM_MAX = 3.0
AUDIO_EXT = (".wav", ".mp3", ".m4a", ".aac", ".ogg", ".flac", ".aif", ".aiff", ".wma", ".opus")


def _write_wav(path, frames):
    """frames: int16 array (n, 2). Atomic enough for tiny files: write tmp then replace."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with wave.open(str(tmp), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(np.ascontiguousarray(frames, dtype="<i2").tobytes())
    tmp.replace(path)
    return path


def bucket(dur, step=TONE_STEP, minimum=TONE_MIN):
    """Tone length for a censor range: rounded UP to the step, never shorter than `minimum`."""
    return round(max(minimum, math.ceil(round(dur / step, 6)) * step), 3)


def tone_path(dur, freq=1000, db=-14.0, folder=None):
    """WAV of a `freq` Hz sine at `db` dBFS RMS, length bucket(dur). Returns (Path, bucket_seconds)."""
    d = bucket(dur)
    freq = int(round(min(4000, max(200, float(freq)))))
    db = round(min(-3.0, max(-40.0, float(db))), 1)
    tail = int(round(TAIL * 1000))
    name = f"beep_{freq}hz_{abs(db):.1f}db_{int(round(d * 1000))}ms_t{tail}.wav".replace(".", "_", 1)
    p = Path(folder or SFX_DIR) / name
    if p.is_file() and p.stat().st_size > 44:
        return p, d
    n, fade = int(round(SR * d)), int(SR * FADE)
    t = np.arange(n) / SR
    amp = 10 ** (db / 20) * math.sqrt(2)                       # RMS dBFS -> peak amplitude
    g = np.minimum(1.0, np.minimum(np.arange(n) / fade, (n - 1 - np.arange(n)) / fade))
    x = np.clip(amp * g * np.sin(2 * math.pi * freq * t), -1, 1)
    s = np.concatenate([np.round(x * 32767).astype(np.int16), np.zeros(int(round(SR * TAIL)), np.int16)])
    _write_wav(p, np.stack([s, s], axis=1))
    return p, d


def wav_duration(path):
    with wave.open(str(path), "rb") as w:
        return w.getnframes() / float(w.getframerate() or SR)


def custom_path(src, max_dur=CUSTOM_MAX, gain_db=0.0):
    """The user's own censor sound as a 48 kHz stereo WAV we generated (Premiere only imports files we made):
    trimmed to max_dur, 10 ms fades in/out, then TAIL seconds of silence. Cached in %APPDATA%\\Klipora\\sfx by
    source identity. Returns (Path, audible seconds without the tail). EngineError NO_SFX when the file is missing
    or not audio."""
    src = Path(str(src or "").strip().strip('"'))
    if not str(src) or not src.is_file():
        raise EngineError("NO_SFX", tr("profanity.err.sfxMissing"), tr("profanity.err.sfxMissingHint"))
    if src.suffix.lower() not in AUDIO_EXT:
        raise EngineError("NO_SFX", tr("profanity.err.sfxFormat", ext=src.suffix or src.name),
                          tr("profanity.err.sfxFormatHint"))
    max_dur = round(min(10.0, max(0.2, float(max_dur))), 2)
    gain_db = round(min(12.0, max(-30.0, float(gain_db))), 1)
    tail = int(round(TAIL * 1000))
    out = appdata_dir() / "sfx" / f"custom_{file_hash(src)}_{int(max_dur * 100)}_{gain_db:+.1f}_t{tail}.wav"
    if out.is_file() and out.stat().st_size > 44:
        return out, round(max(0.0, wav_duration(out) - TAIL), 3)
    out.parent.mkdir(parents=True, exist_ok=True)
    ensure_free(20 * 1024 ** 2, out.parent)
    tmp = out.with_suffix(".tmp.wav")
    # "-t" is an INPUT option: the source is trimmed before the filters, so the reversed fade-out lands on the
    # trimmed end (as an output option a long source was cut at max_dur with no fade at all: a click).
    af = (f"volume={gain_db}dB,afade=t=in:d=0.01,areverse,afade=t=in:d=0.01,areverse,"
          f"apad=pad_dur={TAIL}")
    try:
        run_ffmpeg(["-y", "-t", f"{max_dur}", "-i", str(src), "-vn", "-ac", "2", "-ar", str(SR), "-af", af,
                    "-c:a", "pcm_s16le", str(tmp)])
    except EngineError as e:
        tmp.unlink(missing_ok=True)
        raise EngineError("NO_SFX", tr("profanity.err.sfxUnreadable"), e.msg) from e
    dur = wav_duration(tmp) - TAIL
    if dur < 0.02:
        tmp.unlink(missing_ok=True)
        raise EngineError("NO_SFX", tr("profanity.err.sfxEmpty"), tr("profanity.err.sfxEmptyHint"))
    tmp.replace(out)
    return out, round(dur, 3)


def place(t0, t1, length):
    """Start time of a sound of `length` seconds centred on the censor range [t0, t1] (never before 0)."""
    return round(max(0.0, (t0 + t1) / 2 - length / 2), 4)


def speech_db(db, thr):
    """Typical speech loudness (75th percentile of the voiced 10 ms frames, dBFS) or None."""
    v = np.asarray(db)[np.asarray(db) >= thr]
    return round(float(np.percentile(v, 75)), 1) if len(v) > 50 else None


def auto_tone_db(speech):
    """Beep level that sits with the voice instead of shouting over it: speech - 4 dB, clamped to -32..-12.
    (A fixed -14 dBFS beep was 8-10 dB louder than these screen-recording voices, measured p75 -22 dB.)"""
    if speech is None:
        return -20.0
    return round(min(-12.0, max(-32.0, speech - 4.0)), 1)


def _gain_expr(a, b, g, ramp):
    """FFmpeg volume expression: 1 outside [a, b], g inside, linear `ramp` seconds on both sides."""
    r = max(0.001, ramp)
    return (f"if(lt(t,{a - r:.4f}),1,if(lt(t,{a:.4f}),1-(1-{g:.5f})*(t-{a - r:.4f})/{r:.4f},"
            f"if(lt(t,{b:.4f}),{g:.5f},if(lt(t,{b + r:.4f}),{g:.5f}+(1-{g:.5f})*(t-{b:.4f})/{r:.4f},1))))")


def render_preview(media, s0, s1, out, gain=0.0, lo=None, hi=None, pad=1.2, ramp=0.01, sound=None):
    """Short censored audio clip around one hit (source seconds s0..s1 of `media`, kept inside the clip bounds
    lo..hi) for the panel's "Dengar" button. gain = linear dialog gain inside the range (0 = mute, 0.1 = -20 dB);
    sound = (wav path, seconds) mixed centred on the range (beep / custom). Returns {"path", "dur", "a", "b"}
    (a/b = censor range inside the preview)."""
    lo = 0.0 if lo is None else float(lo)
    hi = float(hi) if hi is not None else s1 + pad
    w0, w1 = max(lo, s0 - pad), max(min(hi, s1 + pad), s0 + 0.05)
    dur = round(w1 - w0, 3)
    a, b = round(s0 - w0, 4), round(s1 - w0, 4)
    g = min(1.0, max(0.0, float(gain)))
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    ensure_free(20 * 1024 ** 2, out.parent)
    chain = (f"[0:a:0]aresample={SR},aformat=channel_layouts=stereo,asetnsamples=n=240,"
             f"volume=volume='{_gain_expr(a, b, g, ramp)}':eval=frame")
    args = ["-y", "-ss", f"{w0:.3f}", "-t", f"{dur:.3f}", "-i", str(media)]
    if sound:
        path, slen = sound
        delay = int(round(max(0.0, place(a, b, slen)) * 1000))
        args += ["-i", str(path)]
        graph = (f"{chain}[d];[1:a]aresample={SR},aformat=channel_layouts=stereo,adelay=delays={delay}:all=1[s];"
                 f"[d][s]amix=inputs=2:normalize=0:duration=first[o]")
    else:
        graph = chain + "[o]"
    args += ["-filter_complex", graph, "-map", "[o]", "-ac", "2", "-ar", str(SR), "-c:a", "pcm_s16le", str(out)]
    run_ffmpeg(args, timeout=60)
    return {"path": str(out), "dur": dur, "a": a, "b": b}
