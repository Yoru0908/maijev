"""Pure-function tests for segment_llm — no LLM, no ffmpeg."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

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


def _fake_llm(segments):
    """Return a generate_json replacement that emits the given partition."""
    return lambda *a, **k: {"segments": segments}


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


def test_merge_splits_segment_at_long_pause(monkeypatch):
    """A segment crossing a long pause is split there, not dropped: the
    atoms on either side of the pause still merge with their neighbours."""
    atoms = [
        _atom("a", 0.0, 0.5, "c00_s1"),
        _atom("b", 0.6, 1.0, "c00_s1", gap=0.1),
        _atom("c", 5.0, 5.5, "c00_s1", gap=4.0),
        _atom("d", 5.6, 6.0, "c00_s1", gap=0.1),
    ]
    monkeypatch.setattr(seg, "generate_json", _fake_llm([[0, 1, 2, 3]]))
    lines = seg.merge_utterances(atoms, cache_dir=None)
    assert [l.text for l in lines] == ["ab", "cd"]


def _short_pause_atoms():
    return [
        _atom("327号", 0.0, 1.0, "c00_s1"),
        _atom("我是", 1.6, 2.0, "c00_s2", gap=0.6),
        _atom("XXX", 2.0, 2.5, "c00_s2"),
    ]


def test_merge_pause_cap_defaults_to_two_seconds(monkeypatch):
    monkeypatch.delenv("SEGMENT_MAX_GAP_S", raising=False)
    monkeypatch.setattr(seg, "generate_json", _fake_llm([[0, 1, 2]]))
    lines = seg.merge_utterances(_short_pause_atoms(), cache_dir=None)
    assert [l.text for l in lines] == ["327号 -我是XXX"]


def test_merge_pause_cap_is_user_configurable(monkeypatch):
    monkeypatch.setenv("SEGMENT_MAX_GAP_S", "0.5")
    monkeypatch.setattr(seg, "generate_json", _fake_llm([[0, 1, 2]]))
    lines = seg.merge_utterances(_short_pause_atoms(), cache_dir=None)
    assert [l.text for l in lines] == ["327号", "我是XXX"]
    assert (lines[1].start, lines[1].end) == (1.6, 2.5)


def test_changing_pause_cap_reuses_merge_cache(monkeypatch, tmp_path):
    """The cache holds the model's groups; a new cap re-splits them
    without calling the model again."""
    monkeypatch.delenv("SEGMENT_MAX_GAP_S", raising=False)
    monkeypatch.setattr(seg, "generate_json", _fake_llm([[0, 1, 2]]))
    seg.merge_utterances(_short_pause_atoms(), cache_dir=tmp_path)

    def no_llm(*a, **k):
        raise AssertionError("merge model must not be called")

    monkeypatch.setattr(seg, "generate_json", no_llm)
    monkeypatch.setenv("SEGMENT_MAX_GAP_S", "0.5")
    lines = seg.merge_utterances(_short_pause_atoms(), cache_dir=tmp_path)
    assert [l.text for l in lines] == ["327号", "我是XXX"]


@pytest.mark.parametrize("raw", ["0", "-1", "abc", "nan"])
def test_merge_rejects_invalid_pause_cap(monkeypatch, raw):
    monkeypatch.setenv("SEGMENT_MAX_GAP_S", raw)
    monkeypatch.setattr(seg, "generate_json", _fake_llm([[0, 1, 2]]))
    with pytest.raises(ValueError, match="pause cap"):
        seg.merge_utterances(_short_pause_atoms(), cache_dir=None)


def test_merge_accepts_singleton_segments(monkeypatch):
    atoms = [
        _atom("a", 0.0, 0.5, "c00_s1"),
        _atom("b", 0.6, 1.0, "c00_s1"),
        _atom("c", 1.1, 1.5, "c00_s1"),
    ]
    monkeypatch.setattr(seg, "generate_json", _fake_llm([[0], [1, 2]]))
    lines = seg.merge_utterances(atoms, cache_dir=None)
    assert [l.text for l in lines] == ["a", "bc"]


@pytest.mark.parametrize(
    "segments",
    [
        [[0, 1]],            # atom 2 missing
        [[0], [1], [1, 2]],  # atom 1 twice
        [[1, 2], [0]],       # out of order
        [[0, 1, 2, 3]],      # out of range
        [[0], [], [1, 2]],   # empty segment
        [["0"], [1, 2]],     # non-int ids
    ],
)
def test_merge_rejects_non_partitions(monkeypatch, segments):
    """Anything that is not an exact in-order partition fails the batch
    (after one retry) instead of silently losing or duplicating atoms."""
    atoms = [
        _atom("a", 0.0, 0.5, "c00_s1"),
        _atom("b", 0.6, 1.0, "c00_s1"),
        _atom("c", 1.1, 1.5, "c00_s1"),
    ]
    monkeypatch.setattr(seg, "generate_json", _fake_llm(segments))
    with pytest.raises(RuntimeError, match="failed after retries"):
        seg.merge_utterances(atoms, cache_dir=None)


def test_merge_rejects_missing_segments_key(monkeypatch):
    atoms = [_atom("a", 0.0, 0.5, "c00_s1")]
    monkeypatch.setattr(seg, "generate_json", lambda *a, **k: {"groups": []})
    with pytest.raises(RuntimeError, match="failed after retries"):
        seg.merge_utterances(atoms, cache_dir=None)
