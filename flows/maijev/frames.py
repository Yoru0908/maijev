"""Sparse representative-frame extraction for OCR/JEV context.

Ported from bangumi-grillmaster's verified sampling strategy, but kept
stdlib/subprocess-only to match this repository's ffmpeg wrappers:

- skip the first few seconds to avoid TV intro/logo frames;
- sample on an absolute interval lattice;
- always include the final safe timestamp;
- leave a trailing GOP safety margin for fast ``-ss`` seeks;
- scale the longest side down for OCR;
- cache frames and write a manifest for resume.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

from loguru import logger

DEFAULT_INTERVAL_SECONDS = 120.0
DEFAULT_INTRO_SKIP_SECONDS = 3.0
DEFAULT_MAX_SIDE = 768
LAST_FRAME_OFFSET_SECONDS = 1.5


@dataclass(frozen=True)
class FrameAsset:
    timestamp_seconds: float
    path: Path


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


def absolute_interval_timestamps(
    start_seconds: float,
    end_seconds: float,
    interval_seconds: float,
    *,
    include_start: bool = True,
    include_end: bool = True,
) -> list[float]:
    """Return deterministic absolute timestamps within a time range.

    ``include_end`` always appends ``end_seconds`` even when it does not align
    with the interval lattice.
    """
    if interval_seconds <= 0:
        raise ValueError("interval_seconds must be positive")
    timestamps: set[float] = set()
    if include_start:
        timestamps.add(round(start_seconds, 3))

    first_slot = int(start_seconds // interval_seconds)
    current = first_slot * interval_seconds
    if current < start_seconds:
        current += interval_seconds

    while current < end_seconds:
        if start_seconds <= current:
            timestamps.add(round(current, 3))
        current += interval_seconds

    if include_end:
        timestamps.add(round(end_seconds, 3))

    return sorted(timestamps)


def extract_video_frame(
    input_path: Path,
    output_path: Path,
    timestamp_seconds: float,
    max_side: int,
) -> Path:
    """Extract one JPEG frame with the longest side constrained."""
    if max_side <= 0:
        raise ValueError("max_side must be positive")
    if output_path.exists():
        logger.debug(f"Reusing cached frame: {output_path}")
        return output_path

    output_path.parent.mkdir(parents=True, exist_ok=True)
    logger.info(
        f"Extracting frame at {timestamp_seconds:.3f}s to {output_path}"
    )
    scale_filter = (
        f"scale='if(gte(iw,ih),{max_side},-2)':"
        f"'if(gte(iw,ih),-2,{max_side})'"
    )
    subprocess.run(
        [
            "ffmpeg", "-y", "-v", "error",
            "-ss", f"{timestamp_seconds:.3f}",
            "-i", str(input_path),
            "-vf", scale_filter,
            "-vframes", "1",
            "-f", "image2",
            "-c:v", "mjpeg",
            "-q:v", "2",
            str(output_path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return output_path


def prepare_reference_frames(
    video_path: Path,
    cache_root: Path,
    *,
    interval_seconds: float = DEFAULT_INTERVAL_SECONDS,
    max_side: int = DEFAULT_MAX_SIDE,
    intro_skip_seconds: float = DEFAULT_INTRO_SKIP_SECONDS,
) -> tuple[list[FrameAsset], Path]:
    """Extract sparse representative frames and write a cache manifest."""
    cache_root.mkdir(parents=True, exist_ok=True)
    frame_dir = cache_root / "frames"
    manifest_path = cache_root / "frames_manifest.json"

    duration = probe_duration(video_path)
    end_seconds = max(0.0, duration - LAST_FRAME_OFFSET_SECONDS)
    start_seconds = min(max(0.0, intro_skip_seconds), end_seconds)
    timestamps = absolute_interval_timestamps(
        start_seconds,
        end_seconds,
        interval_seconds,
        include_start=True,
        include_end=True,
    )

    frames: list[FrameAsset] = []
    for timestamp in timestamps:
        output_path = frame_dir / f"frame_{timestamp:010.3f}_{max_side}.jpg"
        try:
            extract_video_frame(
                video_path,
                output_path,
                timestamp,
                max_side,
            )
        except (subprocess.CalledProcessError, ValueError) as exc:
            logger.warning(
                f"Skipping frame at {timestamp:.3f}s: {exc}"
            )
            continue
        frames.append(FrameAsset(timestamp, output_path))

    manifest_path.write_text(
        json.dumps(
            {
                "video_path": str(video_path),
                "duration_seconds": duration,
                "interval_seconds": interval_seconds,
                "intro_skip_seconds": intro_skip_seconds,
                "last_frame_offset_seconds": LAST_FRAME_OFFSET_SECONDS,
                "max_side": max_side,
                "frames": [
                    {
                        "timestamp_seconds": frame.timestamp_seconds,
                        "path": str(frame.path),
                    }
                    for frame in frames
                ],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return frames, manifest_path
