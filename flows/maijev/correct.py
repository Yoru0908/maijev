"""Bilingual only: Gemini listens to the audio and fixes misheard Japanese.

Runs after the pre-pass and before translation, so the Chinese is translated
from the corrected Japanese and both halves of the bilingual SRT agree. The
model only rewrites the text of existing lines — ids, line count and timing
stay with the program — and every edit is logged for review.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import subprocess
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

from loguru import logger

from . import glossary
from .llm import generate_json, resolve_model, validate_model
from .segment_llm import MergedLine

PROMPT_PATH = Path(__file__).parent / "correct_prompt.md"
PAD_S = 0.5
MAX_WORKERS = 2


def correct_model() -> str:
    """CORRECT_MODEL > TRANSLATE_MODEL > GEMINI_MODEL > default."""
    return validate_model(
        os.environ.get("CORRECT_MODEL") or resolve_model("TRANSLATE_MODEL")
    )


def _load_system() -> str:
    """CORRECT_PROMPT_PATH swaps in a private prompt; the repo one is generic."""
    path = os.environ.get("CORRECT_PROMPT_PATH") or PROMPT_PATH
    return Path(path).read_text(encoding="utf-8")


def segments(lines: list[MergedLine], span: float) -> list[range]:
    """Consecutive line ranges whose audio spans at most ``span`` seconds."""
    out, first = [], 0
    for i in range(1, len(lines) + 1):
        if i == len(lines) or lines[i].end - lines[first].start > span:
            out.append(range(first, i))
            first = i
    return out


def context(table: glossary.Table, review: list[str]) -> str:
    """Japanese side of the glossary plus text-stage doubts.

    Chinese renderings are left out: they mean nothing to a Japanese
    transcript and would tempt the model to write them in. Pre-pass reasons
    are left out too — they are text-only guesses and the audio decides.
    """
    theme = [line for human, line in table.notes if not human]
    names = [
        (f"[{e.kind}] " if e.kind else "") + " / ".join(e.keys)
        for e in table.entities
    ]
    return (
        "【本片资料】\n" + ("\n".join(theme + names) or "（无）")
        + "\n\n【重点确认】\n" + ("、".join(review) or "（无）")
    )


def _ts(t: float) -> str:
    m, s = divmod(max(0.0, t), 60)
    return f"{int(m):02d}:{s:04.1f}"


def _clip(wav: Path, start: float, end: float, out: Path) -> bytes:
    """Ogg/Opus slice of the run's audio (~14 MB per hour), cached on disk."""
    if not out.exists():
        part = out.with_name(out.stem + ".part.ogg")
        subprocess.run(
            [
                "ffmpeg", "-y", "-v", "error",
                "-ss", f"{start:.3f}", "-t", f"{end - start:.3f}", "-i", str(wav),
                "-vn", "-ac", "1", "-c:a", "libopus", "-b:a", "32k", str(part),
            ],
            check=True,
        )
        part.replace(out)
    return out.read_bytes()


def _ask(lines: list[MergedLine], seg: range, wav: Path, ctx: str,
         system: str, model: str, cache_dir: Path) -> list:
    """Model edits for one segment; only real answers are cached."""
    t0 = max(0.0, lines[seg[0]].start - PAD_S)
    t1 = lines[seg[-1]].end + PAD_S
    audio = _clip(wav, t0, t1, cache_dir / f"audio_{t0:.2f}-{t1:.2f}.ogg")
    body = ctx + "\n\n【字幕行】\n" + "\n".join(
        f"{i}\t{_ts(lines[i].start - t0)}-{_ts(lines[i].end - t0)}\t{lines[i].text}"
        for i in seg
    )
    key = hashlib.sha1(
        audio + (body + "\n<system>\n" + system + "\n<model>\n" + model).encode()
    ).hexdigest()[:16]
    cache = cache_dir / f"correct_{key}.json"
    if cache.exists():
        result = json.loads(cache.read_text(encoding="utf-8"))
    else:
        result = generate_json(
            body, system=system, model=model,
            parts=[{"inlineData": {"mimeType": "audio/ogg",
                                   "data": base64.b64encode(audio).decode()}}],
        )
        cache.write_text(json.dumps(result, ensure_ascii=False, indent=2),
                         encoding="utf-8")
    edits = result.get("edits") if isinstance(result, dict) else None
    return edits if isinstance(edits, list) else []


def _record(lines: list[MergedLine], seg: range, edit: object) -> dict | None:
    """One reviewable edit; ``applied`` is False when the program refuses it."""
    if not isinstance(edit, dict):
        return None
    try:
        i = int(edit.get("id"))
    except (TypeError, ValueError):
        return None
    text = str(edit.get("ja") or "").strip()
    if i not in seg or not text or text == lines[i].text:
        return None
    before = lines[i].text
    return {
        "id": i,
        "time": f"{_ts(lines[i].start)}-{_ts(lines[i].end)}",
        "before": before,
        "after": text,
        "reason": str(edit.get("reason") or ""),
        # a correction fixes words; a line that doubles is a new sentence
        "applied": len(text) <= 2 * len(before) + 10,
    }


def correct_lines(
    lines: list[MergedLine],
    wav: Path,
    table: glossary.Table,
    review: list[str],
    cache_dir: Path,
) -> tuple[list[MergedLine], list[dict], list[str]]:
    """Return (corrected lines, edit records, failed segment time ranges)."""
    system, model = _load_system(), correct_model()
    ctx = context(table, review)
    cache_dir.mkdir(parents=True, exist_ok=True)
    segs = segments(lines, float(os.environ.get("CORRECT_SEGMENT_SECONDS", "600")))

    def one(seg: range) -> list | None:
        try:
            return _ask(lines, seg, wav, ctx, system, model, cache_dir)
        except Exception as e:  # noqa: BLE001 — reported, never swallowed
            logger.warning(f"correct lines {seg[0]}-{seg[-1]} failed ({model}): {e}")
            return None

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        answers = list(pool.map(one, segs))
    out = list(lines)
    records: list[dict] = []
    failed: list[str] = []
    for seg, edits in zip(segs, answers):
        if edits is None:
            failed.append(f"{_ts(lines[seg[0]].start)}-{_ts(lines[seg[-1]].end)}")
            continue
        for rec in filter(None, (_record(lines, seg, e) for e in edits)):
            records.append(rec)
            if rec["applied"]:
                out[rec["id"]] = replace(lines[rec["id"]], text=rec["after"])
    applied = sum(r["applied"] for r in records)
    logger.info(
        f"correct: {len(segs)} segments ({model}) -> {applied} lines fixed, "
        f"{len(records) - applied} refused, {len(failed)} segments failed"
    )
    return out, records, failed
