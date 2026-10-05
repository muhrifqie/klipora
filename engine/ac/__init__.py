"""Klipora engine core (`ac`).

Foundation modules (see docs/ENGINE_API.md for the exact API):
  util        settings, workdir, json io, ffmpeg helpers, disk guard, file hash, GPU lock, logging
  progress    JSON-lines events (stage / progress / stage_done / log / warn / result / error)
  media       probe, 16 kHz audio, 10 ms dB envelope (+cache), Otsu threshold, frame grab
  transcript  Whisper words cache v3 (merge, hallucination filter, de-stretch, hole repair), listener pass
  timeline    Timeline model from the host JSON, source <-> sequence mapping, words/envelope on timeline
  ranges      interval algebra in seconds
  review      review file helpers (stable ids, load/save/validate/apply selections)
  xmeml       FCP7 XML writer (multi-source) + xml_cut (remove ranges from an exported sequence)
  ai          AI provider client, prompts, cached tasks (always optional, rule fallback)
  tools       one module per tool exposing ACTIONS = {"analyze": fn(job, emit), ...}

Heavy imports (numpy, faster_whisper) stay inside the modules/functions that need them so
`python engine/cli.py tools` and the worker start fast.
"""

__version__ = "2.0.0"
