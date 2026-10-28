"""Text-only pre-pass: static glossary + OCR anchors + merged JA lines -> glossary.

One shared LLM call replaces the per-batch pre-analysis that the sakamichi
prompt did inside every translate batch: entities are resolved once for the
whole video — variants grouped, Chinese rendering decided — and injected into
every translate batch as one ``[kind] 原文 / 变体 -> 译名`` row per entity.
Images and audio are never sent — OCR text is the authoritative source for
kanji spellings, and the merged subtitle lines supply context for entities
that never appeared on screen.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from loguru import logger

from . import glossary
from .jev import ContextItem, select_anchors
from .llm import generate_json, resolve_model
from .segment_llm import MergedLine

PROMPT_PATH = Path(__file__).parent / "prepass_prompt.md"


def _load_system() -> str:
    """PREPASS_PROMPT_PATH swaps in a private prompt; the repo one is generic."""
    path = os.environ.get("PREPASS_PROMPT_PATH") or PROMPT_PATH
    return Path(path).read_text(encoding="utf-8")


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
    """Render pre-pass output as glossary.md: a theme line + one row per entity.

    Rows use the glossary.py grammar, ``[kind] ja / 变体 -> tgt  # note``;
    the note is for people reading the file and never reaches the translate
    prompt. The older ``src``/``canonical`` shape is still read.
    """
    if not isinstance(result, dict):
        return ""
    lines: list[str] = []
    theme = " ".join(str(result.get("theme") or "").split()).replace("->", "→")
    if theme:
        lines.append(f"【本片主题】{theme}")
    for entry in result.get("entities") or []:
        if not isinstance(entry, dict):
            continue
        ja = str(entry.get("ja") or entry.get("canonical") or "").strip()
        tgt = str(entry.get("tgt") or ja).strip()
        variants = entry.get("variants") or entry.get("src") or []
        if isinstance(variants, str):
            variants = [variants]
        keys = list(dict.fromkeys(
            s for s in (str(v).strip() for v in [ja, *variants]) if s
        ))
        if not tgt or not keys:
            continue
        note = " ".join(str(entry.get("note") or "").split())
        lines.append(
            glossary.format_row(str(entry.get("kind") or ""), keys, tgt, note)
        )
    return "\n".join(lines)


def run_prepass(
    items: list[ContextItem],
    results: list[dict[str, Any]] | None,
    lines: list[MergedLine],
    *,
    extra_anchors: list[dict[str, Any]] | None = None,
    cache_dir: Path | None = None,
) -> tuple[str, list[str]]:
    """Run the shared text-only pre-pass; return (glossary text, doubts).

    Doubts are the review spellings only — the reasons are text-only guesses
    and stay out of the audio correction, which must decide by ear.

    Called once per run when ``--ocr-json`` or ``--prepass`` is given.
    ``results=None`` means JEV never ran and ``items`` are raw OCR text.
    ``extra_anchors`` are pre-classified anchors (e.g. cast names from the
    video source metadata) injected verbatim, bypassing JEV filtering.
    The human glossaries (.env base + manual) go in as the static appendix,
    so the pre-pass reuses their spellings instead of inventing new ones.
    The cache key covers all inputs (glossaries + anchors + subtitle lines)
    plus system prompt and model, so any change invalidates it automatically.
    """
    anchors = build_anchors(items, results) + list(extra_anchors or [])
    system = _load_system()
    model = os.environ.get("PREPASS_MODEL") or resolve_model("TRANSLATE_MODEL")
    static = "\n\n".join(
        text.strip() for text in glossary.human_layers() if text.strip()
    )
    body = (
        "【附录：静态词库】\n"
        + (static or "（无）")
        + "\n\n【画面文字】\n"
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
    text = render_glossary(result)
    review = result.get("review") if isinstance(result, dict) else None
    doubts = [
        str(r.get("src")).strip()
        for r in (review if isinstance(review, list) else [])
        if isinstance(r, dict) and str(r.get("src") or "").strip()
    ]
    logger.info(
        f"pre-pass: {len(anchors)} anchors + {len(lines)} lines -> "
        f"{sum('->' in row for row in text.splitlines())} entities, "
        f"{len(doubts)} review items"
    )
    return text, doubts
