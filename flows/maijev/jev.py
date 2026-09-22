"""Cloudflare Workers AI Jev context classification.

This module only accepts OCR observations at the Jev boundary. It does not
classify ASR lines or images; the pipeline passes only selected OCR text onward.
The client is provider-specific, while the pipeline consumes generic context.
"""

from __future__ import annotations

import hashlib
import json
import os
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


JEV_MODEL = "typesafe/jev"
DEFAULT_BATCH_SIZE = 25
CONTEXT_KINDS = {
    "person_name": "A person's name, performer name, or visible nameplate",
    "program_title": "A television program, show, segment, or work title",
    "place_or_brand": "A place, company, product, group, or brand name",
    "dialogue_caption": "Ordinary spoken dialogue or subtitle text",
    "effect_text": "Decorative variety-show effect text or a sound effect",
    "noise": "A watermark, timestamp, garbled fragment, or meaningless noise",
    "unknown": "Cannot determine reliably from the supplied context",
}


@dataclass(frozen=True)
class ContextItem:
    """A timestamped text observation suitable for Jev classification."""

    item_id: str
    text: str
    source: str = "ocr"
    start: float | None = None
    end: float | None = None
    score: float | None = None
    bbox: tuple[float, ...] | None = None
    repeat_count: int | None = None

    def to_state(self) -> dict[str, Any]:
        value = asdict(self)
        value["id"] = value.pop("item_id")
        if self.bbox is not None:
            value["bbox"] = list(self.bbox)
        return {key: value for key, value in value.items() if value is not None}

    @classmethod
    def from_mapping(
        cls,
        value: dict[str, Any],
        *,
        default_source: str = "ocr",
    ) -> "ContextItem":
        item_id = value.get("item_id") or value.get("track_id") or value.get("id")
        text = value.get("text")
        if not item_id:
            raise ValueError("context item is missing item_id, track_id, or id")
        if not isinstance(text, str) or not text.strip():
            raise ValueError(f"context item {item_id!r} has no non-empty text")
        bbox = value.get("bbox")
        if bbox is not None:
            if not isinstance(bbox, (list, tuple)):
                raise ValueError(f"context item {item_id!r} has invalid bbox")
            bbox = tuple(float(part) for part in bbox)
        source = str(value.get("source") or default_source)
        return cls(
            item_id=str(item_id),
            text=text.strip(),
            source=source,
            start=_optional_float(value.get("start", value.get("first_seen"))),
            end=_optional_float(value.get("end", value.get("last_seen"))),
            score=_optional_float(value.get("score", value.get("ocr_score"))),
            bbox=bbox,
            repeat_count=_optional_int(value.get("repeat_count")),
        )


def _optional_float(value: Any) -> float | None:
    return None if value is None else float(value)


def _optional_int(value: Any) -> int | None:
    return None if value is None else int(value)




def load_ocr_items(path: Path) -> list[ContextItem]:
    """Load OCR observations from a JSON file.

    Accepted shapes are a bare list, ``{"items": [...]}``, or
    ``{"ocr_tracks": [...]}``. Every item is treated as OCR; no ASR lines are
    accepted by this boundary.
    """

    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        raw_items = payload
    elif isinstance(payload, dict) and isinstance(payload.get("items"), list):
        raw_items = payload["items"]
    elif isinstance(payload, dict) and isinstance(payload.get("ocr_tracks"), list):
        raw_items = payload["ocr_tracks"]
    else:
        raise ValueError(f"{path} must contain an OCR item list")
    return [
        ContextItem.from_mapping(item, default_source="ocr")
        for item in raw_items
    ]



class JevClient:
    """Small stdlib-only client for the Cloudflare Jev endpoint."""

    def __init__(
        self,
        account_id: str,
        api_token: str,
        *,
        model: str = JEV_MODEL,
        timeout: int = 120,
    ) -> None:
        self.account_id = account_id
        self.api_token = api_token
        self.model = model
        self.timeout = timeout

    @classmethod
    def from_env(cls) -> "JevClient":
        account_id = os.environ.get("CLOUDFLARE_ACCOUNT_ID", "").strip()
        api_token = os.environ.get("CLOUDFLARE_API_TOKEN", "").strip()
        missing = []
        if not account_id:
            missing.append("CLOUDFLARE_ACCOUNT_ID")
        if not api_token:
            missing.append("CLOUDFLARE_API_TOKEN")
        if missing:
            raise RuntimeError(
                "Jev context requires " + ", ".join(missing)
            )
        return cls(account_id, api_token)

    @property
    def url(self) -> str:
        return (
            "https://api.cloudflare.com/client/v4/accounts/"
            f"{self.account_id}/ai/run"
        )

    def _post(self, body: dict[str, Any]) -> dict[str, Any]:
        request = urllib.request.Request(
            self.url,
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_token}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:500]
            raise RuntimeError(
                f"Cloudflare Jev HTTP {exc.code}: {detail}"
            ) from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Cloudflare Jev connection failed: {exc}") from exc

    def classify(
        self,
        items: list[ContextItem],
        *,
        cache_dir: Path | None = None,
        batch_size: int = DEFAULT_BATCH_SIZE,
    ) -> list[dict[str, Any]]:
        """Classify items and return one result per input item.

        Cache keys include the complete request body, so changing an item or
        the criteria never reuses a stale classification.
        """

        if not items:
            return []
        if batch_size < 1:
            raise ValueError("batch_size must be positive")
        ids = [item.item_id for item in items]
        if len(ids) != len(set(ids)):
            raise ValueError("context item IDs must be unique")
        if cache_dir:
            cache_dir.mkdir(parents=True, exist_ok=True)

        results: list[dict[str, Any]] = []
        for offset in range(0, len(items), batch_size):
            batch = items[offset : offset + batch_size]
            state = {"items": {item.item_id: item.to_state() for item in batch}}
            question_map: dict[str, ContextItem] = {}
            questions: dict[str, dict[str, Any]] = {}
            for index, item in enumerate(batch):
                question_id = f"item_{offset + index:06d}"
                question_map[question_id] = item
                questions[question_id] = {
                    "type": "choice",
                    "instructions": (
                        f"Classify the text of context item {item.item_id} "
                        "by semantic type. Use the surrounding fields as context."
                    ),
                    "criteria": CONTEXT_KINDS,
                }
            body = {
                "model": self.model,
                "input": {"state": state, "questions": questions},
            }
            key = hashlib.sha1(
                json.dumps(body, sort_keys=True, ensure_ascii=False).encode("utf-8")
            ).hexdigest()[:16]
            cache_path = (
                cache_dir / f"batch_{offset:06d}_{key}.json"
                if cache_dir
                else None
            )
            if cache_path and cache_path.exists():
                response = json.loads(cache_path.read_text(encoding="utf-8"))
            else:
                response = self._post(body)
                if cache_path:
                    cache_path.write_text(
                        json.dumps(response, ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )
            result = response.get("result", response)
            if (
                isinstance(result, dict)
                and isinstance(result.get("result"), dict)
            ):
                result = result["result"]
            answers = result.get("answers") if isinstance(result, dict) else None
            if not isinstance(answers, dict):
                raise RuntimeError("Cloudflare Jev response has no answers")
            for question_id, item in question_map.items():
                answer = answers.get(question_id)
                if not isinstance(answer, dict) or answer.get("type") != "choice":
                    raise RuntimeError(
                        f"Cloudflare Jev response missing {question_id}"
                    )
                choice = str(answer.get("choice", "unknown"))
                if choice not in CONTEXT_KINDS:
                    choice = "unknown"
                results.append(
                    {
                        "item_id": item.item_id,
                        "text": item.text,
                        "source": item.source,
                        "choice": choice,
                        "confidence": float(answer.get("confidence", 0.0)),
                        "probabilities": answer.get("probabilities", {}),
                    }
                )
        return results


ENTITY_KINDS = {"person_name", "program_title", "place_or_brand"}


def select_anchors(
    items: list[ContextItem],
    results: list[dict[str, Any]],
    *,
    review_threshold: float = 0.75,
) -> list[tuple[ContextItem, str, float]]:
    """Select items worth passing downstream.

    High-confidence entities keep their kind label; anything below the
    threshold is labelled ``review``; the rest is dropped.
    """
    item_by_id = {item.item_id: item for item in items}
    selected: list[tuple[ContextItem, str, float]] = []
    for result in results:
        item = item_by_id.get(str(result.get("item_id")))
        if item is None:
            continue
        choice = str(result.get("choice", "unknown"))
        confidence = float(result.get("confidence", 0.0))
        if choice not in ENTITY_KINDS and confidence >= review_threshold:
            continue
        label = choice if confidence >= review_threshold else "review"
        selected.append((item, label, confidence))
    return selected


def render_context(
    items: list[ContextItem],
    results: list[dict[str, Any]],
    *,
    review_threshold: float = 0.75,
) -> str:
    """Render only useful anchors and uncertain items for the translation LLM."""

    rows: list[str] = []
    for item, label, confidence in select_anchors(
        items, results, review_threshold=review_threshold
    ):
        timing = ""
        if item.start is not None and item.end is not None:
            timing = f"[{item.start:.3f}-{item.end:.3f}] "
        extra = [f"source={item.source}", f"confidence={confidence:.2f}"]
        if item.bbox is not None:
            extra.append("bbox=" + ",".join(f"{part:g}" for part in item.bbox))
        rows.append(f"{timing}{label}: {item.text} ({'; '.join(extra)})")
    return "\n".join(rows)
