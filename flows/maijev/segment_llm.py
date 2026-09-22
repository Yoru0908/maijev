"""LLM-assisted subtitle segmentation for maijev.

Stage 2 of the pipeline. Two layers:

1. ``build_atoms`` — deterministic, word-level. MAI words are split at
   physical boundaries: speaker change, inter-word silence, hard punctuation,
   and a length fallback. A narrow deterministic pass then coalesces ordinary
   ``うん`` backchannels before the LLM; paired cross-speaker ``うん`` atoms
   remain visible.

2. ``merge_utterances`` — the LLM sees numbered atoms (with speaker and
   gap) and returns groups of indices to merge into subtitle lines.
   Timestamps are recomputed from atom boundaries, so the model can never
   corrupt the timeline.

The system prompt lives in ``segment_prompt.md`` — the same file the
seg_lab test bench reads, so prompt iteration in the lab carries over
directly.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from loguru import logger

from .llm import generate_json

# Atoms per LLM call, plus trailing context shown but not decided.
# 1000 atoms ≈ 30min of audio — one call per half hour, two calls for 1h.
BATCH_SIZE = 1000
CONTEXT_TAIL = 20
# Independent batches run in parallel; each is a single LLM call.
MAX_WORKERS = 2

PROMPT_PATH = Path(__file__).parent / "segment_prompt.md"

HARD_PUNCT = set("。！？?!")
SOFT_PUNCT = set("、，,：:；;")

SILENCE_SPLIT_S = 1.0
MAX_ATOM_CHARS = 48

# Merging across a pause this long is almost always wrong; reject such
# groups even if the model returns them.
MAX_MERGE_GAP_S = 2.0
UN_PAIR_GAP_S = 1.0
_UN_RE = re.compile(r"^\s*(?:うん)+[、。！？?!…\s]*$")


def _is_un_atom(atom: dict) -> bool:
    """Return whether an atom is only the Japanese backchannel ``うん``."""
    return bool(_UN_RE.fullmatch(atom["text"]))


def _append_atom(dst: dict, src: dict, *, cross_speaker: bool) -> None:
    """Append ``src`` into ``dst`` while preserving cross-speaker markup."""
    if cross_speaker:
        dst["text"] += " -" + src["text"]
    else:
        dst["text"] += src["text"]
    dst["end"] = src["end"]


def _coalesce_un_atoms(atoms: list[dict]) -> list[dict]:
    """Remove ordinary standalone ``うん`` atoms before the merge LLM.

    Same-speaker fillers are folded into an adjacent atom. A close pair of
    different-speaker fillers is intentionally preserved as two atoms because
    it represents the useful ``うん - うん`` turn-taking signal. Other
    cross-speaker fillers are attached to the closer neighboring atom and
    retain the renderer's `` -`` speaker marker.
    """
    protected: set[int] = set()
    for i, atom in enumerate(atoms[:-1]):
        nxt = atoms[i + 1]
        if (
            _is_un_atom(atom)
            and _is_un_atom(nxt)
            and atom.get("speaker_id") != nxt.get("speaker_id")
            and nxt["start"] - atom["end"] <= UN_PAIR_GAP_S
        ):
            protected.update((i, i + 1))

    out: list[dict] = []
    i = 0
    while i < len(atoms):
        atom = atoms[i]
        if not _is_un_atom(atom) or i in protected:
            out.append(atom)
            i += 1
            continue

        prev = out[-1] if out else None
        nxt = atoms[i + 1] if i + 1 < len(atoms) else None
        prev_gap = atom["start"] - prev["end"] if prev else float("inf")
        next_gap = nxt["start"] - atom["end"] if nxt else float("inf")
        same_prev = (
            prev is not None
            and prev.get("speaker_id") == atom.get("speaker_id")
            and prev_gap <= MAX_MERGE_GAP_S
        )
        same_next = (
            nxt is not None
            and i + 1 not in protected
            and nxt.get("speaker_id") == atom.get("speaker_id")
            and next_gap <= MAX_MERGE_GAP_S
        )
        if same_prev:
            _append_atom(prev, atom, cross_speaker=False)
            i += 1
            continue
        if same_next:
            merged = dict(atom)
            _append_atom(merged, nxt, cross_speaker=False)
            out.append(merged)
            i += 2
            continue

        # This is a response from a different speaker with no same-speaker
        # neighbor. Attach it to the closer ordinary atom so it cannot become
        # a subtitle line that translation later erases.
        if (
            prev is not None
            and i - 1 not in protected
            and prev_gap <= MAX_MERGE_GAP_S
            and prev_gap <= next_gap
        ):
            _append_atom(prev, atom, cross_speaker=True)
            i += 1
            continue
        if nxt is not None and i + 1 not in protected and next_gap <= MAX_MERGE_GAP_S:
            merged = dict(atom)
            _append_atom(merged, nxt, cross_speaker=True)
            out.append(merged)
            i += 2
            continue
        out.append(atom)
        i += 1

    prev_end = None
    for atom in out:
        atom["gap"] = 0.0 if prev_end is None else max(0.0, atom["start"] - prev_end)
        prev_end = atom["end"]
    return out


def _load_system() -> str:
    return PROMPT_PATH.read_text(encoding="utf-8")


@dataclass
class MergedLine:
    start: float
    end: float
    text: str
    utterance_ids: list[int]


def build_atoms(payload: dict) -> list[dict]:
    """Split MAI words into atoms, then coalesce standalone ``うん``.

    Physical boundaries are respected first; the deterministic filler pass
    runs before the merge LLM so ordinary backchannels cannot be dropped by
    translation as empty standalone rows.
    """
    words = sorted(payload["words"], key=lambda w: w["start"])
    atoms: list[dict] = []
    cur: list[dict] = []

    def flush() -> None:
        if not cur:
            return
        atoms.append({
            "speaker_id": cur[0].get("speaker_id"),
            "start": cur[0]["start"],
            "end": cur[-1]["end"],
            "text": "".join(w["text"] for w in cur),
        })
        cur.clear()

    for w in words:
        if cur:
            prev = cur[-1]
            gap = w["start"] - prev["end"]
            text_len = sum(len(x["text"]) for x in cur)
            last = prev["text"][-1]
            if (
                w.get("speaker_id") != prev.get("speaker_id")
                or gap >= SILENCE_SPLIT_S
                or last in HARD_PUNCT
                or last in SOFT_PUNCT
                or text_len >= MAX_ATOM_CHARS
            ):
                flush()
        cur.append(w)
    flush()

    prev_end = None
    for a in atoms:
        a["gap"] = 0.0 if prev_end is None else max(0.0, a["start"] - prev_end)
        prev_end = a["end"]
    return _coalesce_un_atoms(atoms)


def _speaker_tag(speaker_id: str | None) -> str:
    """Normalize a MAI speaker label for both prompt display and rendering.

    MAI labels are only valid within one ASR request; merge.py prefixes them
    with the chunk index ("c03_s1"). Comparing the raw id treats the same
    speaker as different across chunk boundaries, so everything — the LLM
    view and the renderer — uses the local tag ("s1").
    """
    return (speaker_id or "?").split("_")[-1]


def _format_atom(i: int, a: dict) -> str:
    return (
        f"{i} | {_speaker_tag(a.get('speaker_id'))} | "
        f"+{a['gap']:.2f}s | {a['text']}"
    )


def _llm_merge_batch(
    atoms: list[dict], start_idx: int, end_idx: int
) -> list[list[int]]:
    """Ask the LLM for safe merge groups within one batch."""
    lines = []
    ctx_end = min(end_idx + CONTEXT_TAIL, len(atoms))
    for i in range(start_idx, ctx_end):
        marker = "" if i < end_idx else "  (context)"
        lines.append(_format_atom(i, atoms[i]) + marker)
    prompt = (
        "对以下 atom 做合并决策。只对不带 (context) 标记的行做决定；"
        "context 行仅供你判断边界语义。\n\n" + "\n".join(lines)
    )
    result = generate_json(
        prompt,
        system=_load_system(),
        model=os.environ.get("SEGMENT_MODEL", "gemini-2.5-pro"),
    )
    raw_groups = result.get("groups", []) if isinstance(result, dict) else []

    clean: list[list[int]] = []
    used: set[int] = set()
    for raw in raw_groups:
        if not isinstance(raw, list) or len(raw) < 2:
            continue
        if not all(isinstance(i, int) for i in raw):
            continue
        group = raw
        if (
            group != sorted(group)
            or group != list(range(group[0], group[-1] + 1))
            or group[0] < start_idx
            or group[-1] >= end_idx
            or any(i in used for i in group)
        ):
            continue
        # Never let a group bridge a long pause even if the model asked.
        if any(
            atoms[b]["start"] - atoms[a]["end"] >= MAX_MERGE_GAP_S
            for a, b in zip(group, group[1:])
        ):
            continue
        clean.append(group)
        used.update(group)
    return clean


def _merge_one(
    atoms: list[dict], start: int, end: int, cache_dir: Path | None
) -> list[list[int]]:
    """One batch: cache hit, or LLM call with one retry. Failed batches
    are never persisted, so re-runs retry just that batch."""
    # Cache key covers the atoms this batch decides on — atomize changes
    # shift indices, so an index-keyed cache would silently misapply.
    # The system prompt and model are part of the key so prompt/model
    # changes invalidate old batches automatically.
    key_input = "\n".join(
        _format_atom(i, atoms[i]) for i in range(start, end)
    )
    key = hashlib.sha1(
        (
            key_input
            + "\n<system>\n" + _load_system()
            + "\n<model>\n" + os.environ.get("SEGMENT_MODEL", "gemini-2.5-pro")
        ).encode()
    ).hexdigest()[:16]
    cache_file = cache_dir / f"batch_{key}.json" if cache_dir else None
    if cache_file and cache_file.exists():
        return json.loads(cache_file.read_text(encoding="utf-8"))

    batch_groups = None
    for attempt in (1, 2):
        try:
            batch_groups = _llm_merge_batch(atoms, start, end)
            break
        except Exception as e:
            logger.warning(
                f"segment batch {start}-{end} attempt {attempt} "
                f"failed: {e}"
            )
    if batch_groups is None:
        raise RuntimeError(
            f"segment batch {start}-{end} failed after retries"
        )
    if cache_file:
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(
            json.dumps(batch_groups, ensure_ascii=False), encoding="utf-8"
        )
    return batch_groups


def merge_utterances(
    atoms: list[dict],
    cache_dir: Path | None = None,
) -> list[MergedLine]:
    """Return merged subtitle lines.

    Batches of BATCH_SIZE atoms run in parallel (MAX_WORKERS); each
    batch is cached under ``cache_dir`` by content hash so a failed
    batch never poisons the run and re-runs only retry missing files.
    """
    ranges = [
        (i, min(i + BATCH_SIZE, len(atoms)))
        for i in range(0, len(atoms), BATCH_SIZE)
    ]
    if len(ranges) > 1:
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
            results = list(pool.map(
                lambda r: _merge_one(atoms, r[0], r[1], cache_dir),
                ranges,
            ))
    else:
        results = [
            _merge_one(atoms, r[0], r[1], cache_dir) for r in ranges
        ]
    groups: list[list[int]] = [g for batch in results for g in batch]

    # Apply groups; ungrouped atoms become single lines.
    grouped = {i for g in groups for i in g}
    lines: list[MergedLine] = []
    i = 0
    while i < len(atoms):
        g = next((g for g in groups if g[0] == i), None)
        if g:
            parts = []
            prev_spk = None
            for j in g:
                u = atoms[j]
                spk = _speaker_tag(u.get("speaker_id"))
                if prev_spk is not None and spk != prev_spk:
                    parts.append(" -" + u["text"])
                else:
                    parts.append(u["text"])
                prev_spk = spk
            lines.append(MergedLine(
                start=atoms[g[0]]["start"],
                end=atoms[g[-1]]["end"],
                text="".join(parts),
                utterance_ids=g,
            ))
            i = g[-1] + 1
        else:
            u = atoms[i]
            lines.append(MergedLine(
                start=u["start"], end=u["end"],
                text=u["text"], utterance_ids=[i],
            ))
            i += 1
    logger.info(
        f"LLM merge: {len(atoms)} atoms → {len(lines)} lines "
        f"({len(grouped)} merged)"
    )
    return lines
