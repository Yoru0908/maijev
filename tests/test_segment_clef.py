"""Clef merge backend — fake Cloudflare responses, no network."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import flows.maijev.segment_clef as clef  # noqa: E402
import flows.maijev.segment_llm as seg  # noqa: E402


def _atoms(texts, gaps=None):
    """Consecutive atoms; gaps[i] = silence before atom i."""
    gaps = gaps or [0.2] * len(texts)
    atoms, t = [], 0.0
    for text, gap in zip(texts, gaps):
        t += gap
        atoms.append({"speaker_id": "c00_s0", "start": t, "end": t + 1.0,
                      "text": text, "gap": gap})
        t += 1.0
    return atoms


def _fake_clef(join_ids, calls):
    """Answer every asked boundary; join those in join_ids."""
    def post(url, token, body, timeout, provider):
        calls.append(body)
        answers = {
            qid: {"probabilities": {"join": 0.9 if int(qid[1:]) in join_ids else 0.1}}
            for qid in body["questions"]
        }
        return {"result": {"answers": answers}}
    return post


@pytest.fixture
def creds(monkeypatch):
    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "acc")
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "tok")


def test_joined_boundaries_become_groups(monkeypatch, creds):
    calls = []
    monkeypatch.setattr(clef, "_post_json", _fake_clef({0, 1, 3}, calls))
    atoms = _atoms(["え、", "この番組では、", "毎週です。", "あの、", "はい。", "以上。"])
    assert clef.clef_merge_batch(atoms, 0, 6) == [[0, 1, 2], [3, 4]]
    assert set(calls[0]["questions"]) == {"b0", "b1", "b2", "b3", "b4"}


def test_long_pause_is_never_asked_or_joined(monkeypatch, creds):
    calls = []
    monkeypatch.setattr(clef, "_post_json", _fake_clef({0, 1}, calls))
    atoms = _atoms(["a、", "b、", "c。"], gaps=[0.0, 0.2, 3.0])
    assert clef.clef_merge_batch(atoms, 0, 3) == [[0, 1]]
    assert set(calls[0]["questions"]) == {"b0"}


def test_boundaries_are_chunked_at_question_cap(monkeypatch, creds):
    calls = []
    monkeypatch.setattr(clef, "_post_json", _fake_clef(set(), calls))
    atoms = _atoms([f"{i}。" for i in range(100)])
    assert clef.clef_merge_batch(atoms, 0, 100) == []
    sizes = sorted(len(c["questions"]) for c in calls)
    assert sizes == [35, 64]
    # Context atoms are marked; decided atoms are not.
    rows = {r["id"]: r for c in calls for r in c["state"]["atoms"] if "b0" in c["questions"]}
    assert "ctx" not in rows[0] and rows[84].get("ctx")


def test_missing_answer_is_an_error(monkeypatch, creds):
    monkeypatch.setattr(clef, "_post_json", lambda *a: {"result": {"answers": {}}})
    with pytest.raises(ValueError, match="no answer"):
        clef.clef_merge_batch(_atoms(["a、", "b。"]), 0, 2)


def test_missing_credentials_is_an_error(monkeypatch):
    monkeypatch.delenv("CLOUDFLARE_ACCOUNT_ID", raising=False)
    monkeypatch.delenv("CLOUDFLARE_API_TOKEN", raising=False)
    with pytest.raises(RuntimeError, match="CLOUDFLARE_ACCOUNT_ID"):
        clef.clef_merge_batch(_atoms(["a、", "b。"]), 0, 2)


def test_failed_clef_batch_falls_back_to_gemini(monkeypatch, creds):
    monkeypatch.setenv("SEGMENT_BACKEND", "clef")

    def broken(*a):
        raise RuntimeError("Cloudflare Clef HTTP 500")

    monkeypatch.setattr(clef, "_post_json", broken)
    monkeypatch.setattr(seg, "_llm_merge_batch", lambda atoms, s, e, m: [[0, 1]])
    lines = seg.merge_utterances(_atoms(["a、", "b。"]))
    assert [line.utterance_ids for line in lines] == [[0, 1]]


def test_clef_result_is_cached_separately(monkeypatch, creds, tmp_path):
    monkeypatch.setenv("SEGMENT_BACKEND", "clef")
    calls = []
    monkeypatch.setattr(clef, "_post_json", _fake_clef({0}, calls))
    atoms = _atoms(["a、", "b。"])
    seg.merge_utterances(atoms, cache_dir=tmp_path)
    seg.merge_utterances(atoms, cache_dir=tmp_path)
    assert len(calls) == 1
    monkeypatch.setenv("SEGMENT_BACKEND", "gemini")
    monkeypatch.setattr(seg, "_llm_merge_batch", lambda atoms, s, e, m: [])
    assert [line.utterance_ids for line in seg.merge_utterances(atoms, cache_dir=tmp_path)] == [[0], [1]]


def test_unknown_backend_is_rejected(monkeypatch):
    monkeypatch.setenv("SEGMENT_BACKEND", "jev")
    with pytest.raises(ValueError, match="Unsupported SEGMENT_BACKEND"):
        seg.segment_backend()
