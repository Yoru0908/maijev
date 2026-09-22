"""Prompt-iteration test bench for maijev segmentation.

Reads a cached asr.json (MAI payload), atomizes it via
``segment_llm.build_atoms``, then runs ONE LLM merge batch over a chosen
time window and prints the prompt, raw response, and resulting mini-SRT.

Shares ``segment_prompt.md`` and ``build_atoms`` with the real pipeline —
what you tune here is what production runs.

Usage (from repo root):

    uv run python -m flows.maijev.seg_lab <asr.json> [options]

Options:
    --start S       window start seconds (default 0)
    --end S         window end seconds (default 120)
    --atoms         print atom list and exit (no LLM call)
    --prompt-only   print prompt and exit (no LLM call)
    --system PATH   override system prompt file (default: segment_prompt.md)
    --model NAME    override model (sets SEGMENT_MODEL)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

from flows.maijev.llm import generate_json  # noqa: E402
from flows.maijev.segment_llm import (  # noqa: E402
    PROMPT_PATH,
    _format_atom,
    build_atoms,
)


def llm_merge(atoms: list[dict], system: str) -> list[list[int]]:
    lines = [_format_atom(i, a) for i, a in enumerate(atoms)]
    prompt = (
        "对以下 atom 做合并决策。只输出需要合并的组。\n\n" + "\n".join(lines)
    )
    print("=" * 60, "\nPROMPT:\n", prompt, "\n", "=" * 60, sep="")
    result = generate_json(
        prompt, system=system, model=os.environ.get("SEGMENT_MODEL") or None
    )
    print("RAW RESPONSE:\n", json.dumps(result, ensure_ascii=False, indent=1))

    raw_groups = result.get("groups", []) if isinstance(result, dict) else []
    clean, used = [], set()
    for g in raw_groups:
        if (
            isinstance(g, list)
            and len(g) >= 2
            and all(isinstance(i, int) for i in g)
            and g == list(range(g[0], g[-1] + 1))
            and 0 <= g[0] and g[-1] < len(atoms)
            and not any(i in used for i in g)
        ):
            clean.append(g)
            used.update(g)
    return clean


def render(atoms: list[dict], groups: list[list[int]]) -> str:
    def ts(t: float) -> str:
        t = max(0.0, t)
        h, rem = divmod(t, 3600)
        m, s = divmod(rem, 60)
        return f"{int(h):02d}:{int(m):02d}:{s:06.3f}".replace(".", ",")

    blocks, n, i = [], 0, 0
    while i < len(atoms):
        g = next((g for g in groups if g[0] == i), None)
        ids = g if g else [i]
        parts, prev_spk = [], None
        for j in ids:
            spk = atoms[j].get("speaker_id")
            parts.append(
                (" -" if prev_spk is not None and spk != prev_spk else "")
                + atoms[j]["text"]
            )
            prev_spk = spk
        n += 1
        blocks.append(
            f"{n}\n{ts(atoms[ids[0]]['start'])} --> {ts(atoms[ids[-1]]['end'])}"
            f"\n{''.join(parts)}\n"
        )
        i = ids[-1] + 1
    return "\n".join(blocks)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("asr_json", type=Path)
    ap.add_argument("--start", type=float, default=0.0)
    ap.add_argument("--end", type=float, default=120.0)
    ap.add_argument("--atoms", action="store_true")
    ap.add_argument("--prompt-only", action="store_true")
    ap.add_argument("--system", type=Path, default=PROMPT_PATH)
    ap.add_argument("--model", default=None)
    args = ap.parse_args()

    if args.model:
        os.environ["SEGMENT_MODEL"] = args.model

    payload = json.loads(args.asr_json.read_text(encoding="utf-8"))
    atoms = [
        a for a in build_atoms(payload)
        if a["end"] >= args.start and a["start"] <= args.end
    ]
    print(f"{len(atoms)} atoms in [{args.start}, {args.end}]s")

    if args.atoms:
        for i, a in enumerate(atoms):
            print(_format_atom(i, a))
        return

    system = args.system.read_text(encoding="utf-8")
    if args.prompt_only:
        print(system)
        print("\n".join(_format_atom(i, a) for i, a in enumerate(atoms)))
        return

    groups = llm_merge(atoms, system)
    print("=" * 60, "\nSRT:\n", render(atoms, groups), sep="")


if __name__ == "__main__":
    main()
