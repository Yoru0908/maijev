"""MAI-Transcribe-2 ASR flow (OpenRouter) — self-contained pipeline.

Replaces the ElevenLabs ASR stage with microsoft/mai-transcribe-2 via
OpenRouter's speech-to-text endpoint, then reuses the existing
services/elevenlabs/srt_builder.py word-level segmentation engine.

Usage:
    python -m flows.maijev.pipeline <video_or_audio> <work_dir>

Outputs in <work_dir>:
    audio.wav            extracted 16kHz mono audio
    chunks/chunk_XX.wav  silence-aligned ~5min slices
    chunks/*.json        per-chunk raw API responses (resumable cache)
    asr.json             merged ElevenLabs-shaped payload
    out.srt              final subtitles via srt_builder
"""


def run(*args, **kwargs):
    """Lazily import the pipeline so ``python -m`` has no double-import warning."""
    from .pipeline import run as _run

    return _run(*args, **kwargs)


__all__ = ["run"]
