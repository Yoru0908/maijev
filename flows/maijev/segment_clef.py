"""Clef boundary judgements for the merge stage (``SEGMENT_BACKEND=clef``).

Cloudflare Workers AI ``@cf/cloudflare/clef`` is a decision model: it does
not write text, it returns per-option probabilities. Every adjacent-atom
boundary with a pause shorter than the merge-gap limit becomes one
join/break question; a boundary joins when p(join) >= JOIN_THRESHOLD.
Boundaries at long pauses are never asked, so groups never cross them.

The returned groups have the same shape as the Gemini partition result,
so caching, rendering and the speaker " -" marker are unchanged.
"""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor

from .jev import _post_json
from .segment_llm import MAX_MERGE_GAP_S, _speaker_tag

CLEF_MODEL = "clef"
MAX_QUESTIONS = 64  # Clef per-request question cap
CONTEXT = 20  # atoms of read-only context on each side of a chunk
JOIN_THRESHOLD = 0.5
MAX_WORKERS = 4
TIMEOUT_S = 120

TASK = (
    "Japanese subtitle line segmentation. Atoms are ASR fragments in time "
    "order. For each asked boundary, decide whether the two neighbouring "
    "atoms belong on the same subtitle line."
)
RULES = [
    "JOIN if the earlier atom is an unfinished phrase (ends with "
    "は/が/を/に/で/て/と/けど/、 or is cut mid-clause).",
    "JOIN an isolated filler or short reaction (え、/あの/うん/はい) to the "
    "neighbouring sentence it belongs to.",
    "BREAK if the earlier atom is a complete sentence.",
    "BREAK at a clear topic change.",
    "Speaker labels (spk) come from automatic diarization and may be wrong. "
    "Trust the text meaning over spk.",
    "gap = seconds of silence before this atom. Long gaps suggest a break "
    "but are not decisive.",
    "Atoms marked ctx are context only.",
]
EXAMPLES = [
    {"a": "え、", "b": "この番組では、", "answer": "join"},
    {"a": "この番組では、", "b": "毎週ゲストをお招きしています。", "answer": "join"},
    {"a": "よろしくお願いします。", "b": "さて、今日は", "answer": "break"},
]
# Part of the merge cache key: editing the prompt invalidates old batches.
CACHE_TAG = "\n".join([CLEF_MODEL, TASK, *RULES, repr(EXAMPLES)])


def _gap(atoms: list[dict], i: int) -> float:
    """Silence between atom i and atom i+1."""
    return atoms[i + 1]["start"] - atoms[i]["end"]


def _credentials() -> tuple[str, str]:
    account = os.environ.get("CLOUDFLARE_ACCOUNT_ID", "").strip()
    token = os.environ.get("CLOUDFLARE_API_TOKEN", "").strip()
    if not account or not token:
        raise RuntimeError(
            "SEGMENT_BACKEND=clef requires CLOUDFLARE_ACCOUNT_ID and "
            "CLOUDFLARE_API_TOKEN"
        )
    return account, token


def _question(atoms: list[dict], i: int) -> dict:
    a, b = atoms[i], atoms[i + 1]
    spk_a, spk_b = _speaker_tag(a.get("speaker_id")), _speaker_tag(b.get("speaker_id"))
    spk = f", spk label changes {spk_a}→{spk_b}" if spk_a != spk_b else ""
    return {
        "type": "choice",
        "instructions": (
            f"Boundary between atom {i} 「{a['text']}」 and atom {i + 1} "
            f"「{b['text']}」 (gap {_gap(atoms, i):.1f}s{spk}). "
            "Same subtitle line?"
        ),
        "criteria": {
            "join": f"Same line: {i} is unfinished, or a filler that "
            f"belongs with {i + 1}.",
            "break": f"New line: {i} is complete, or {i + 1} starts a new "
            "sentence.",
        },
    }


def _request(atoms: list[dict], chunk: list[int]) -> dict:
    """State + questions for one chunk of boundaries."""
    lo, hi = chunk[0], chunk[-1] + 1  # atoms the chunk decides on
    rows = []
    for i in range(max(0, lo - CONTEXT), min(len(atoms), hi + 1 + CONTEXT)):
        a = atoms[i]
        row = {
            "id": i,
            "spk": _speaker_tag(a.get("speaker_id")),
            "gap": round(a["gap"], 2),
            "text": a["text"],
        }
        if not lo <= i <= hi:
            row["ctx"] = True
        rows.append(row)
    return {
        "model": CLEF_MODEL,
        "state": {"task": TASK, "rules": RULES, "examples": EXAMPLES, "atoms": rows},
        "questions": {f"b{i}": _question(atoms, i) for i in chunk},
    }


def _ask(atoms: list[dict], chunk: list[int], account: str, token: str) -> set[int]:
    url = (
        "https://api.cloudflare.com/client/v4/accounts/"
        f"{account}/ai/run/@cf/cloudflare/{CLEF_MODEL}"
    )
    response = _post_json(url, token, _request(atoms, chunk), TIMEOUT_S, "Cloudflare Clef")
    answers = (response.get("result") or {}).get("answers") or {}
    joins = set()
    for i in chunk:
        answer = answers.get(f"b{i}")
        if not answer:
            raise ValueError(f"Clef returned no answer for boundary {i}")
        if answer["probabilities"]["join"] >= JOIN_THRESHOLD:
            joins.add(i)
    return joins


def _groups(joins: set[int], start: int, end: int) -> list[list[int]]:
    """Runs of joined boundaries → multi-atom groups."""
    groups, run = [], [start]
    for i in range(start, end - 1):
        if i not in joins:
            groups.append(run)
            run = []
        run.append(i + 1)
    groups.append(run)
    return [g for g in groups if len(g) >= 2]


def clef_merge_batch(atoms: list[dict], start: int, end: int) -> list[list[int]]:
    """Merge groups for atoms ``start .. end-1`` from Clef judgements."""
    account, token = _credentials()
    asked = [i for i in range(start, end - 1) if _gap(atoms, i) < MAX_MERGE_GAP_S]
    chunks = [asked[k:k + MAX_QUESTIONS] for k in range(0, len(asked), MAX_QUESTIONS)]
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        results = pool.map(lambda c: _ask(atoms, c, account, token), chunks)
        joins = set().union(*results)
    return _groups(joins, start, end)
