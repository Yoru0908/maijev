"""Audio extraction and silence-aligned chunking for MAI-Transcribe-2.

The OpenRouter endpoint accepts base64 audio in a JSON body, so request
size is the binding constraint: ~5min of 16kHz mono PCM ≈ 9.6MB raw /
~13MB base64 — comfortably under gateway limits, and a failed chunk only
costs a 5-minute retry.
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from loguru import logger

TARGET_CHUNK_S = 300.0
# Don't create a tail chunk shorter than this — merge it into the previous.
MIN_TAIL_S = 60.0
# Search window around each target boundary for a silence midpoint.
SILENCE_WINDOW_S = 60.0
SILENCE_NOISE_DB = "-35dB"
SILENCE_MIN_DURATION_S = 0.8


@dataclass(frozen=True)
class Chunk:
    index: int
    start: float
    end: float
    path: Path

    @property
    def cache_path(self) -> Path:
        return self.path.with_suffix(".wav.json")


def probe_duration(path: Path) -> float:
    out = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        capture_output=True, text=True, check=True,
    )
    return float(out.stdout.strip())


def extract_audio(input_path: Path, output_path: Path) -> Path:
    """Extract/convert to 16kHz mono PCM WAV (what we feed the API)."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg", "-y", "-v", "error",
            "-i", str(input_path),
            "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le",
            str(output_path),
        ],
        check=True,
    )
    logger.info(f"Extracted audio: {output_path}")
    return output_path


def detect_silences(wav_path: Path) -> list[float]:
    """Return midpoints of detected silence regions."""
    proc = subprocess.run(
        [
            "ffmpeg", "-i", str(wav_path),
            "-af", f"silencedetect=noise={SILENCE_NOISE_DB}:d={SILENCE_MIN_DURATION_S}",
            "-f", "null", "-",
        ],
        capture_output=True, text=True,
    )
    mids = []
    for m in re.finditer(
        r"silence_start: ([\d.]+)\s*\n.*?silence_end: ([\d.]+)",
        proc.stderr, re.S,
    ):
        mids.append((float(m.group(1)) + float(m.group(2))) / 2)
    return mids


def choose_split_points(silences: list[float], total: float) -> list[float]:
    """Pick split points near each TARGET_CHUNK_S boundary, snapped to silence."""
    points: list[float] = []
    target = TARGET_CHUNK_S
    while target < total - MIN_TAIL_S:
        window = [
            s for s in silences
            if target - SILENCE_WINDOW_S < s < target + SILENCE_WINDOW_S
            and (not points or s > points[-1] + MIN_TAIL_S)
        ]
        point = min(window, key=lambda s: abs(s - target)) if window else target
        points.append(point)
        target = point + TARGET_CHUNK_S
    return points


def make_chunks(wav_path: Path, chunks_dir: Path, total: float) -> list[Chunk]:
    silences = detect_silences(wav_path)
    logger.info(f"Detected {len(silences)} silence regions")
    points = choose_split_points(silences, total)
    logger.info(f"Split points: {[round(p, 1) for p in points]}")

    chunks_dir.mkdir(parents=True, exist_ok=True)
    bounds = [0.0] + points + [total]
    chunks: list[Chunk] = []
    for i, (start, end) in enumerate(zip(bounds, bounds[1:])):
        path = chunks_dir / f"chunk_{i:02d}.wav"
        if not path.exists():
            subprocess.run(
                [
                    "ffmpeg", "-y", "-v", "error",
                    "-i", str(wav_path),
                    "-ss", str(start), "-to", str(end),
                    "-c", "copy", str(path),
                ],
                check=True,
            )
        chunks.append(Chunk(index=i, start=start, end=end, path=path))
    logger.info(f"{len(chunks)} chunks ready in {chunks_dir}")
    return chunks


def write_manifest(chunks: list[Chunk], path: Path) -> None:
    path.write_text(
        json.dumps(
            [
                {"index": c.index, "start": c.start, "end": c.end,
                 "file": c.path.name}
                for c in chunks
            ],
            indent=2,
        ),
        encoding="utf-8",
    )
