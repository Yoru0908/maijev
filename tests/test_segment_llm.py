"""Pure-function tests for segment_llm — no LLM, no ffmpeg."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import flows.maijev.segment_llm as seg  # noqa: E402


def _word(text, start, end, speaker=None):
    return {
        "text": text,
        "start": start,
        "end": end,
        "type": "word",
        "speaker_id": speaker,
    }


def _payload(words):
    return {"words": words, "language_code": "jpn", "segments": []}


def _atom(text, start, end, speaker, gap=0.0):
    return {
        "speaker_id": speaker,
        "start": start,
        "end": end,
        "text": text,
        "gap": gap,
    }


def test_speaker_tag_normalizes_chunk_prefix():
    assert seg._speaker_tag("c03_s1") == "s1"
    assert seg._speaker_tag("c00_s10") == "s10"
    assert seg._speaker_tag(None) == "?"
    assert seg._speaker_tag("") == "?"


def test_build_atoms_splits_on_speaker_gap_and_punct():
    payload = _payload([
        _word("こんにちは", 0.0, 0.5, "c00_s1"),
        _word("。", 0.5, 0.6, "c00_s1"),
        _word("次は", 2.0, 2.4, "c00_s1"),       # gap >= 1s
        _word("です", 2.4, 2.7, "c00_s2"),       # speaker switch
    ])
    atoms = seg.build_atoms(payload)
    assert len(atoms) == 3
    assert atoms[0]["text"] == "こんにちは。"
    assert atoms[1]["speaker_id"] == "c00_s1"
    assert atoms[2]["speaker_id"] == "c00_s2"


def _fake_llm(groups):
    """Return a generate_json replacement that emits the given groups."""
    return lambda *a, **k: {"groups": groups}


def test_merge_does_not_dash_same_speaker_across_chunks(monkeypatch):
    """LLM sees 's1' on both sides of a chunk boundary; the renderer must
    use the same normalized tag instead of the raw c00_/c01_ prefix."""
    atoms = [
        _atom("前半", 0.0, 1.0, "c00_s1"),
        _atom("後半", 1.2, 2.0, "c01_s1", gap=0.2),
    ]
    monkeypatch.setattr(seg, "generate_json", _fake_llm([[0, 1]]))
    lines = seg.merge_utterances(atoms, cache_dir=None)
    assert len(lines) == 1
    assert lines[0].text == "前半後半"


def test_merge_inserts_dash_on_real_speaker_change(monkeypatch):
    atoms = [
        _atom("質問", 0.0, 1.0, "c00_s1"),
        _atom("回答", 1.2, 2.0, "c00_s2", gap=0.2),
    ]
    monkeypatch.setattr(seg, "generate_json", _fake_llm([[0, 1]]))
    lines = seg.merge_utterances(atoms, cache_dir=None)
    assert lines[0].text == "質問 -回答"


def test_merge_rejects_group_bridging_long_pause(monkeypatch):
    """A group spanning >= MAX_MERGE_GAP_S must be dropped even if the
    model returned it; atoms then render as separate lines."""
    atoms = [
        _atom("前文", 0.0, 1.0, "c00_s1"),
        _atom("後文", 5.0, 6.0, "c00_s1", gap=4.0),
    ]
    monkeypatch.setattr(seg, "generate_json", _fake_llm([[0, 1]]))
    lines = seg.merge_utterances(atoms, cache_dir=None)
    assert len(lines) == 2
    assert [l.text for l in lines] == ["前文", "後文"]


def test_merge_rejects_invalid_groups(monkeypatch):
    """Non-contiguous, overlapping, or out-of-range groups are dropped."""
    atoms = [
        _atom("a", 0.0, 0.5, "c00_s1"),
        _atom("b", 0.6, 1.0, "c00_s1"),
        _atom("c", 1.1, 1.5, "c00_s1"),
    ]
    monkeypatch.setattr(
        seg, "generate_json", _fake_llm([[0, 2], [1, 1], [0, 1, 99]])
    )
    lines = seg.merge_utterances(atoms, cache_dir=None)
    assert [l.text for l in lines] == ["a", "b", "c"]
