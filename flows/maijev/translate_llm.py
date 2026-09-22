"""LLM translation stage for maijev.

Takes merged Japanese lines (from segment_llm) and translates to
Simplified Chinese. The model sees numbered lines and returns
{"lines": [{"id": N, "zh": "..."}]}. Timestamps never leave this file.

Batched (~1000 lines/call) with per-batch JSON cache for resume.
"""

from __future__ import annotations

import hashlib
import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from loguru import logger

from .llm import generate_json, resolve_model
from .segment_llm import MergedLine

# User-validated operating point: 1000 subtitle lines per call;
# two 1000-line batches can run in parallel for longer recordings.
BATCH_SIZE = 1000
MAX_WORKERS = 2

PROMPT_PATH = Path(__file__).parent / "translate_prompt.md"


# --- deterministic post-processing (subtitle-group rules) -------------------
# The model is asked to follow these, but we enforce them mechanically so a
# sloppy batch can't leak violations into the final SRT.

# Banned filler chars must never appear in output.
_BANNED_CHARS = "啊嗯欸"


def _clean_zh(text: str) -> str:
    """Enforce subtitle-group output rules on every dialogue part."""
    # JA lines can never start with " -" (the first part is always plain),
    # so a leading dash in zh output is model noise — drop it.
    text = text.lstrip(" -")
    cleaned_parts = []
    for part in text.split(" -"):
        part = part.replace("，", " ").replace("、", " ")
        part = part.replace("“", "「").replace("”", "」")
        for ch in _BANNED_CHARS:
            part = part.replace(ch, "")
        part = part.strip().lstrip("。，、 ").rstrip("。，、 ")
        if part:
            cleaned_parts.append(part)
    return " -".join(cleaned_parts)
OUTPUT_CONTRACT = """
【输出契约】
输入是带编号的日文字幕行列表（id<TAB>日文）。你必须输出 JSON：
{"lines": [{"id": <int>, "zh": "<简体中文译文>"}, ...]}

- 每个输入 id 必须恰好出现一次，禁止遗漏、禁止新增 id。
- zh 字段只放译文文本，不要注释、不要编号、不要时间轴。
- 日文中已有的 " -" 说话人分隔符必须保留在译文对应位置。
"""


def _load_system(glossary_path: Path | None = None) -> str:
    parts = []
    if PROMPT_PATH.exists():
        style = PROMPT_PATH.read_text(encoding="utf-8").strip()
        if style:
            parts.append(style)

    glossary_parts = []
    if glossary_path and glossary_path.exists():
        auto = glossary_path.read_text(encoding="utf-8").strip()
        if auto:
            glossary_parts.append(
                "【自动术语表（OCR + pre-pass 生成）】\n" + auto
            )
    external_path = os.environ.get("TRANSLATE_GLOSSARY_PATH")
    if external_path:
        external = Path(external_path).read_text(encoding="utf-8").strip()
        if external:
            glossary_parts.append(
                "【外部术语表（人工确认；与上方条目冲突时以此为准）】\n"
                + external
            )
    if glossary_parts:
        parts.append(
            "\n【术语表】\n"
            "以下内容只作为实体写法与译法的一致性参考，不改变输入 id、"
            "行数和 JSON 格式：\n" + "\n\n".join(glossary_parts)
        )

    parts.append(OUTPUT_CONTRACT)
    return "\n".join(parts)


def _fallback_if_empty(line: MergedLine, text: str) -> str:
    cleaned = _clean_zh(text)
    return cleaned if cleaned.strip() else line.text


def _translate_batch(
    lines: list[MergedLine],
    ids: list[int],
    system: str,
    model: str,
) -> dict[int, str]:
    body = "\n".join(f"{i}\t{lines[i].text}" for i in ids)
    result = generate_json(body, system=system, model=model)
    out: dict[int, str] = {}
    for item in result.get("lines", []):
        if isinstance(item, dict) and "id" in item and "zh" in item:
            item_id = int(item["id"])
            if item_id in ids:
                out[item_id] = _fallback_if_empty(lines[item_id], str(item["zh"]))
    missing = [i for i in ids if i not in out]
    if missing:
        raise RuntimeError(f"translation missing ids: {missing[:10]}")
    return out


def translate_lines(
    lines: list[MergedLine],
    cache_dir: Path | None = None,
    glossary_path: Path | None = None,
) -> list[str]:
    system = _load_system(glossary_path)
    model = resolve_model("TRANSLATE_MODEL")
    translations: dict[int, str] = {}
    if cache_dir:
        cache_dir.mkdir(parents=True, exist_ok=True)

    def one_batch(i: int, end: int | None = None) -> dict[int, str]:
        ids = list(range(i, end or min(i + BATCH_SIZE, len(lines))))
        # Key by content hash, not index: line boundaries shift whenever
        # atomize/merge changes, and an index-keyed cache would silently
        # splice stale translations onto the new timeline. System prompt
        # (incl. both glossaries) and model are part of the key.
        body_key_input = "\n".join(
            f"{j}\t{lines[j].text}" for j in ids
        )
        body_key = hashlib.sha1(
            (
                body_key_input
                + "\n<system>\n" + system
                + "\n<model>\n" + model
            ).encode()
        ).hexdigest()[:16]
        cache = cache_dir / f"batch_{body_key}.json" if cache_dir else None
        if cache and cache.exists():
            cached = json.loads(cache.read_text(encoding="utf-8"))
            return {
                int(k): _fallback_if_empty(lines[int(k)], str(v))
                for k, v in cached.items()
            }
        try:
            batch = _translate_batch(lines, ids, system, model)
        except Exception as e:
            if len(ids) > 1:
                # Bisect and retry — a single bad line can corrupt the
                # whole batch's JSON, but halves usually survive.
                mid = i + len(ids) // 2
                logger.warning(
                    f"translate batch {i} failed: {e}; "
                    f"splitting into {i}-{mid} / {mid}-{i+len(ids)}"
                )
                left = one_batch(i, mid)
                right = one_batch(mid, i + len(ids))
                return {**left, **right}
            logger.warning(f"translate line {i} failed: {e}; "
                           f"falling back to source text")
            return {i: lines[i].text}
        # Only persist real translations — a fallback batch must
        # not poison the cache, so re-runs retry that batch.
        if cache:
            cache.write_text(
                json.dumps(batch, ensure_ascii=False), encoding="utf-8"
            )
        return batch

    starts = list(range(0, len(lines), BATCH_SIZE))
    if len(starts) > 1:
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
            results = list(pool.map(one_batch, starts))
    else:
        results = [one_batch(i) for i in starts]
    for batch in results:
        translations.update(batch)

    return [
        _fallback_if_empty(lines[i], translations[i])
        for i in range(len(lines))
    ]



def render_srt(lines: list[MergedLine], texts: list[str]) -> str:
    def ts(t: float) -> str:
        t = max(0.0, t)
        h, rem = divmod(t, 3600)
        m, s = divmod(rem, 60)
        return f"{int(h):02d}:{int(m):02d}:{s:06.3f}".replace(".", ",")

    def has_content(text: str) -> bool:
        # A block needs at least one CJK char or alphanumeric to be shown.
        return any(ch.isalnum() or "一" <= ch <= "鿿" for ch in text)

    def clean_dialogue(text: str) -> str:
        # Drop " -" segments that carry no real text after cleanup.
        parts = text.split(" -")
        kept = [p for p in parts if has_content(p)]
        return " -".join(kept)

    blocks = []
    n = 0
    for line, text in zip(lines, texts):
        text = clean_dialogue(text)
        if not has_content(text):
            # Last-resort preservation for filler-only or malformed model rows.
            # Never silently drop a real timeline line.
            text = clean_dialogue(line.text)
            if not has_content(text):
                continue
        n += 1
        blocks.append(f"{n}\n{ts(line.start)} --> {ts(line.end)}\n{text}\n")
    return "\n".join(blocks)
