"""Merge per-chunk MAI responses into one ElevenLabs-shaped payload.

srt_builder._extract_tokens() consumes payload["words"] items with:
    text       — token text (MAI emits per-character tokens for Japanese)
    start/end  — absolute seconds
    speaker_id — string; used only for speaker-turn detection
    type       — "word" (anything not in ignored_word_types passes)

MAI speaker labels are only valid within a single request, so we prefix
them with the chunk index ("c03_s1") — same-speaker turns never merge
across a chunk boundary, which is correct since labels can't be trusted
to be the same person anyway.
"""

from __future__ import annotations

from typing import Any

from .chunker import Chunk


def merge_chunks(
    chunks: list[Chunk], responses: list[dict[str, Any]]
) -> dict[str, Any]:
    words: list[dict[str, Any]] = []
    segments: list[dict[str, Any]] = []
    texts: list[str] = []
    total_cost = 0.0
    total_seconds = 0.0
    language = None

    for chunk, resp in zip(chunks, responses):
        offset = chunk.start
        usage = resp.get("usage") or {}
        total_cost += float(usage.get("cost") or 0.0)
        total_seconds += float(usage.get("seconds") or (chunk.end - chunk.start))
        language = language or resp.get("language")

        if resp.get("text"):
            texts.append(resp["text"])

        for seg in resp.get("segments") or []:
            speaker = seg.get("speaker")
            segments.append({
                "start": seg["start"] + offset,
                "end": seg["end"] + offset,
                "text": seg.get("text", ""),
                "speaker_id": (
                    f"c{chunk.index:02d}_s{speaker}"
                    if speaker is not None else None
                ),
            })

        for w in resp.get("words") or []:
            speaker = w.get("speaker")
            words.append({
                "text": w.get("word", ""),
                "start": w["start"] + offset,
                "end": w["end"] + offset,
                "type": "word",
                "speaker_id": (
                    f"c{chunk.index:02d}_s{speaker}"
                    if speaker is not None else None
                ),
            })

    words.sort(key=lambda w: (w["start"], w["end"]))
    segments.sort(key=lambda s: s["start"])

    return {
        "language_code": language or "jpn",
        "text": "".join(texts),
        "words": words,
        "segments": segments,
        "_mai_meta": {
            "model": "microsoft/mai-transcribe-2",
            "chunks": len(chunks),
            "audio_seconds": total_seconds,
            "cost_usd": total_cost,
        },
    }
