"""Audio extraction, loudness curve and speech-to-text (faster-whisper, runs free on CPU)."""
from __future__ import annotations

import json
import wave
from pathlib import Path

from .util import log, need, run

SR = 16000


def extract_audio(video: Path, out: Path) -> Path:
    need("ffmpeg")
    run(["ffmpeg", "-y", "-v", "error", "-i", str(video), "-vn", "-ac", "1", "-ar", str(SR),
         "-c:a", "pcm_s16le", str(out)])
    return out


def loudness_curve(wav_path: Path, hop: float = 0.5) -> list[float]:
    """RMS loudness in dB for every `hop` seconds."""
    import numpy as np  # noqa: PLC0415

    with wave.open(str(wav_path), "rb") as w:
        n = w.getnframes()
        raw = w.readframes(n)
    x = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    step = int(SR * hop)
    if len(x) < step:
        return [-90.0]
    frames = len(x) // step
    x = x[: frames * step].reshape(frames, step)
    rms = np.sqrt((x ** 2).mean(axis=1) + 1e-12)
    return (20 * np.log10(rms + 1e-9)).round(2).tolist()


def transcribe(wav_path: Path, model_name: str = "base", language: str | None = None,
               cache: Path | None = None) -> list[dict]:
    """Returns [{start, end, text, words:[{start,end,word}]}]. Empty list if no speech."""
    if cache and cache.exists():
        return json.loads(cache.read_text())
    try:
        from faster_whisper import WhisperModel  # noqa: PLC0415
    except ImportError:
        log.warning("faster-whisper not installed; continuing without a transcript (no captions)")
        return []
    log.info("Transcribing with whisper '%s' (CPU)...", model_name)
    model = WhisperModel(model_name, device="cpu", compute_type="int8")
    # Pass the audio as numbers, not a file path: newer PyAV versions dropped an argument that
    # faster-whisper uses when it opens files itself ("unexpected keyword 'metadata_errors'").
    import numpy as np  # noqa: PLC0415

    with wave.open(str(wav_path), "rb") as w:
        audio = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0
    segs, info = model.transcribe(
        audio, language=language or None, word_timestamps=True, vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 400}, beam_size=1, condition_on_previous_text=False,
    )
    out = []
    for s in segs:
        words = [{"start": round(w.start, 2), "end": round(w.end, 2), "word": w.word.strip()}
                 for w in (s.words or []) if w.word.strip()]
        out.append({"start": round(s.start, 2), "end": round(s.end, 2), "text": s.text.strip(), "words": words})
    log.info("Transcript: %d segments, language=%s", len(out), getattr(info, "language", "?"))
    if cache:
        cache.write_text(json.dumps(out))
    return out
