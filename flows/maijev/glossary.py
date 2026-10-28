"""One conflict-free glossary table for the translate stage.

Every source — the pre-pass ``glossary.md``, the .env base glossary and the
GUI's manual layers — shares one line grammar::

    [人名] 野比のび太 / のび太 / ノビ君 -> 大雄  # note

Left of ``->``: every spelling of one entity; right: the only rendering the
translation may use. ``[kind]`` and ``  # note`` are optional, so a plain
``原文 -> 写法`` row is a one-key entity. Lines without ``->`` are free text
and pass through untouched.

Layers merge per entity, not per line: a later row whose key (or rendering)
hits an earlier entity takes over that entity's rendering and adds its keys,
so the model never sees two answers for one entity. Keys written by a person
are also enforced after translation (``enforcement``); pre-pass variants are
model guesses and only ever reach the prompt.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

KIND_LABELS = {
    "person": "人名", "person_name": "人名",
    "program": "节目", "program_title": "节目",
    "place": "地名", "place_or_brand": "地名",
    "term": "术语",
}
HUMAN_GLOSSARY_ENV = ("TRANSLATE_BASE_GLOSSARY_PATH", "TRANSLATE_GLOSSARY_PATH")

_KIND = re.compile(r"^\[([^\]]+)\]\s*")
_KEY_SEP = re.compile(r"\s+/\s+|／")
_KANA = re.compile(r"[ぁ-ゖァ-ヺ]")


@dataclass
class Entity:
    keys: list[str]
    tgt: str
    kind: str = ""
    human_keys: set[str] = field(default_factory=set)


@dataclass
class Table:
    entities: list[Entity] = field(default_factory=list)
    notes: list[tuple[bool, str]] = field(default_factory=list)  # (human, line)


def format_row(kind: str, keys: list[str], tgt: str, note: str = "") -> str:
    label = KIND_LABELS.get(kind, kind)
    row = f"{f'[{label}] ' if label else ''}{' / '.join(keys)} -> {tgt}"
    return f"{row}  # {note}" if note else row


def parse_row(line: str) -> tuple[str, list[str], str] | None:
    """(kind, keys, tgt), or None for a free-text line."""
    line = line.split("  #", 1)[0].strip()
    if "->" not in line:
        return None
    left, tgt = (part.strip() for part in line.split("->", 1))
    kind_match = _KIND.match(left)
    kind = kind_match.group(1) if kind_match else ""
    left = left[kind_match.end():] if kind_match else left
    keys = [k.strip() for k in _KEY_SEP.split(left) if k.strip()]
    return (kind, keys, tgt) if keys and tgt else None


def _absorb(entities: list[Entity], kind: str, keys: list[str], tgt: str,
            human: bool) -> None:
    hits = [e for e in entities if set(keys) & {*e.keys, e.tgt}]
    ent = hits[0] if hits else Entity([], tgt)
    if not hits:
        entities.append(ent)
    for other in hits[1:]:  # a row naming two entities says they are one
        ent.keys += [k for k in other.keys if k not in ent.keys]
        ent.human_keys |= other.human_keys
        ent.kind = ent.kind or other.kind
        entities.remove(other)
    ent.keys += [k for k in keys if k not in ent.keys]
    ent.tgt = tgt
    ent.kind = kind or ent.kind
    if human:
        ent.human_keys.update(keys)


def merge(layers: list[tuple[str, bool]]) -> Table:
    """Merge ``(text, human)`` layers in priority order, later wins."""
    table = Table()
    for text, human in layers:
        for line in text.splitlines():
            row = parse_row(line)
            if row:
                _absorb(table.entities, *row, human)
            elif line.strip():
                table.notes.append((human, line.rstrip()))
    return table


def human_layers() -> list[str]:
    """Glossaries a person maintains: .env base, then GUI/.env manual."""
    return [
        Path(path).read_text(encoding="utf-8")
        for path in (os.environ.get(var) for var in HUMAN_GLOSSARY_ENV)
        if path
    ]


def load(auto_path: Path | None = None) -> Table:
    """Pre-pass glossary < base < manual, merged into one table."""
    layers = []
    if auto_path and auto_path.exists():
        layers.append((auto_path.read_text(encoding="utf-8"), False))
    layers += [(text, True) for text in human_layers()]
    return merge(layers)


def render(table: Table) -> str:
    """Prompt block: theme, one row per entity, then human free text."""
    # A row mapping a word to itself is a decision too: keep it as written
    # (櫻坂46, a programme title); a human row for the entity overrides it.
    rows = [format_row(e.kind, e.keys, e.tgt) for e in table.entities]
    parts = [line for human, line in table.notes if not human]
    if rows:
        parts.append(
            "【术语表】每行一个实体：「->」左边是该实体在原文中可能出现的各种"
            "写法（以 / 分隔），右边是译文必须使用的写法。术语表不改变输入 id、"
            "行数和 JSON 格式。\n" + "\n".join(rows)
        )
    human_notes = [line for human, line in table.notes if human]
    if human_notes:
        parts.append(
            "【基础词库（人工维护，供确认实体身份与写法）】\n"
            + "\n".join(human_notes)
        )
    return "\n\n".join(parts)


def enforcement(table: Table) -> dict[str, str]:
    """Human-written kana keys that must never survive into the Chinese.

    Kana in a zh line is always an untranslated leftover; kanji keys are
    skipped because a surname such as 山川 is legitimately kept, and a key
    inside its own rendering (のび太 -> 野比のび太) cannot be replaced safely.
    """
    return {
        key: e.tgt
        for e in table.entities
        for key in e.human_keys
        if len(key) >= 2 and _KANA.search(key) and key not in e.tgt
    }


def enforce(text: str, rules: dict[str, str]) -> tuple[str, int]:
    """Replace every rule key in one pass, longest first; return (text, hits)."""
    if not rules:
        return text, 0
    pattern = re.compile(
        "|".join(re.escape(k) for k in sorted(rules, key=len, reverse=True))
    )
    return pattern.subn(lambda m: rules[m.group()], text)
