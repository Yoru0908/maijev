"""Text-only pre-pass: JEV-filtered OCR anchors + merged JA lines -> glossary.

One shared LLM call replaces the per-batch pre-analysis that the sakamichi
prompt did inside every translate batch: entities are resolved once for the
whole video and injected into every translate batch as a ``原文 -> 写法``
glossary. Images and audio are never sent — OCR text is the authoritative
source for kanji spellings, and the merged subtitle lines supply context for
entities that never appeared on screen.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from loguru import logger

from .jev import ContextItem, select_anchors
from .llm import generate_json, resolve_model
from .segment_llm import MergedLine

PROMPT_PATH = Path(__file__).parent / "prepass_prompt.md"


def _load_system() -> str:
    return PROMPT_PATH.read_text(encoding="utf-8")


def build_anchors(
    items: list[ContextItem], results: list[dict[str, Any]] | None
) -> list[dict[str, Any]]:
    """Serialise anchors for the pre-pass prompt.

    ``results=None`` means JEV never ran (no Cloudflare credentials): every
    OCR observation is passed through unfiltered with kind ``ocr``.
    """
    if results is None:
        return [
            {"text": item.text, "kind": "ocr"}
            for item in items
        ]
    anchors: list[dict[str, Any]] = []
    for item, label, confidence in select_anchors(items, results):
        anchor: dict[str, Any] = {
            "text": item.text,
            "kind": label,
            "confidence": round(confidence, 2),
        }
        if item.start is not None and item.end is not None:
            anchor["start"] = item.start
            anchor["end"] = item.end
        if item.repeat_count:
            anchor["repeat"] = item.repeat_count
        anchors.append(anchor)
    return anchors


def render_glossary(result: dict[str, Any]) -> str:
    """Render pre-pass entities as ``原文 -> 写法`` lines.

    Every variant gets its own line pointing at the same canonical form, so
    translate batches never have to merge variants themselves. The format is
    identical to the external ``TRANSLATE_GLOSSARY_PATH`` file.
    """
    lines: list[str] = []
    seen: set[str] = set()
    entries = result.get("entities") if isinstance(result, dict) else None
    for entry in entries or []:
        if not isinstance(entry, dict):
            continue
        canonical = str(entry.get("canonical") or "").strip()
        if not canonical:
            continue
        for src in entry.get("src") or []:
            src = str(src).strip()
            if not src:
                continue
            line = f"{src} -> {canonical}"
            if line not in seen:
                seen.add(line)
                lines.append(line)
    return "\n".join(lines)


def run_prepass(
    items: list[ContextItem],
    results: list[dict[str, Any]] | None,
    lines: list[MergedLine],
    *,
    extra_anchors: list[dict[str, Any]] | None = None,
    cache_dir: Path | None = None,
) -> str:
    """Run the shared text-only pre-pass; return glossary text.

    Called once per run when ``--ocr-json`` or ``--prepass`` is given.
    ``results=None`` means JEV never ran and ``items`` are raw OCR text.
    ``extra_anchors`` are pre-classified anchors (e.g. cast names from the
    video source metadata) injected verbatim, bypassing JEV filtering.
    The cache key covers all inputs (anchors + subtitle lines) plus system
    prompt and model, so any change invalidates the cache automatically.
    """
    anchors = build_anchors(items, results) + list(extra_anchors or [])
    system = _load_system()
    model = os.environ.get("PREPASS_MODEL") or resolve_model("TRANSLATE_MODEL")
    body = (
        "【画面文字】\n"
        + json.dumps(anchors, ensure_ascii=False, indent=1)
        + "\n\n【日文字幕行】\n"
        + "\n".join(f"{i}\t{line.text}" for i, line in enumerate(lines))
    )
    key = hashlib.sha1(
        (body + "\n<system>\n" + system + "\n<model>\n" + model).encode()
    ).hexdigest()[:16]
    cache_file = cache_dir / f"prepass_{key}.json" if cache_dir else None
    if cache_file and cache_file.exists():
        result = json.loads(cache_file.read_text(encoding="utf-8"))
    else:
        result = generate_json(body, system=system, model=model)
        if cache_file:
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            cache_file.write_text(
                json.dumps(result, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
    glossary = render_glossary(result)
    review_n = (
        len(result.get("review") or []) if isinstance(result, dict) else 0
    )
    logger.info(
        f"pre-pass: {len(anchors)} anchors + {len(lines)} lines -> "
        f"{len(glossary.splitlines()) if glossary else 0} glossary rows, "
        f"{review_n} review items"
    )
    return glossary
