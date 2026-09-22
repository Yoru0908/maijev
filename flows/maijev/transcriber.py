"""MAI-Transcribe-2 client via OpenRouter's speech-to-text endpoint.

POST https://openrouter.ai/api/v1/audio/transcriptions
Body: base64 audio + verbose_json + word timestamps + Azure passthrough
options (diarization, phraseList biasing, transcribeStyle).

Auth: OPENROUTER_API_KEY env var (or .env via pydantic-settings upstream).
"""

from __future__ import annotations

import base64
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from loguru import logger

ENDPOINT = "https://openrouter.ai/api/v1/audio/transcriptions"
MODEL = "microsoft/mai-transcribe-2"
MAX_RETRIES = 4

# Optional domain terms for phraseList biasing. Keep the public default empty;
# callers can pass their own list to build_payload/transcribe_chunk.
DEFAULT_PHRASES: list[str] = []


class TranscribeError(RuntimeError):
    pass


def _api_key() -> str:
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        raise TranscribeError("OPENROUTER_API_KEY not set")
    return key


def build_payload(wav_path: Path, phrases: list[str] | None = None) -> dict:
    b64 = base64.b64encode(wav_path.read_bytes()).decode("ascii")
    return {
        "model": MODEL,
        "input_audio": {"data": b64, "format": "wav"},
        "response_format": "verbose_json",
        "timestamp_granularities": ["word"],
        "provider": {
            "options": {
                "azure": {
                    "diarization": {"enabled": True},
                    "phraseList": {"phrases": phrases or DEFAULT_PHRASES},
                    # verbatim keeps fillers/false starts — matches the
                    # existing subtitle style and survives speaker-turn
                    # segmentation better than "clean".
                    "enhancedMode": {
                        "modelOptions": {"transcribeStyle": "verbatim"}
                    },
                }
            }
        },
    }


def transcribe_chunk(
    wav_path: Path,
    cache_path: Path,
    phrases: list[str] | None = None,
) -> dict[str, Any]:
    """Transcribe one chunk; cache raw JSON next to the wav for resume."""
    if cache_path.exists():
        return json.loads(cache_path.read_text(encoding="utf-8"))

    body = json.dumps(build_payload(wav_path, phrases)).encode("utf-8")
    request = urllib.request.Request(
        ENDPOINT,
        data=body,
        headers={
            "Authorization": f"Bearer {_api_key()}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    last_error: Exception | None = None
    for attempt in range(MAX_RETRIES):
        try:
            with urllib.request.urlopen(request, timeout=600) as resp:
                result = json.loads(resp.read().decode("utf-8"))
            cache_path.write_text(
                json.dumps(result, ensure_ascii=False), encoding="utf-8"
            )
            return result
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:300]
            last_error = TranscribeError(f"HTTP {e.code}: {detail}")
            # 429/5xx are transient; 4xx auth/quota errors are not.
            if e.code not in (429, 500, 502, 503, 504):
                raise last_error
            wait = 5 * (attempt + 1)
            logger.warning(f"{wav_path.name}: HTTP {e.code}, retry in {wait}s")
            time.sleep(wait)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
            last_error = e
            wait = 5 * (attempt + 1)
            logger.warning(f"{wav_path.name}: {e}, retry in {wait}s")
            time.sleep(wait)

    raise TranscribeError(
        f"{wav_path.name} failed after {MAX_RETRIES} attempts: {last_error}"
    )
