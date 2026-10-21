"""Retry policy for the MAI transcription call — no network."""

from __future__ import annotations

import io
import json
import sys
import urllib.error
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import flows.maijev.transcriber as tr  # noqa: E402


def _http_error(code: int) -> urllib.error.HTTPError:
    body = json.dumps({"error": {"message": f"Provider returned {code}", "code": code}})
    return urllib.error.HTTPError(tr.ENDPOINT, code, "error", {}, io.BytesIO(body.encode()))


@pytest.fixture
def chunk(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test")
    monkeypatch.setattr(tr.time, "sleep", lambda s: None)
    wav = tmp_path / "chunk_000.wav"
    wav.write_bytes(b"RIFF")
    return wav, tmp_path / "chunk_000.json"


def _serve(monkeypatch, responses):
    """urlopen that raises/returns the given responses in order."""
    calls = []

    def fake_urlopen(req, timeout=0):
        calls.append(req)
        r = responses[len(calls) - 1]
        if isinstance(r, Exception):
            raise r
        return io.BytesIO(json.dumps(r).encode())

    monkeypatch.setattr(tr.urllib.request, "urlopen", fake_urlopen)
    return calls


@pytest.mark.parametrize("code", [429, 500, 525, 529])
def test_transient_errors_are_retried(monkeypatch, chunk, code):
    """A provider-side 52x (Cloudflare SSL/origin failure) is as transient as 502."""
    calls = _serve(monkeypatch, [_http_error(code), {"words": []}])
    assert tr.transcribe_chunk(*chunk) == {"words": []}
    assert len(calls) == 2
    assert json.loads(chunk[1].read_text()) == {"words": []}


@pytest.mark.parametrize("code", [400, 401, 402, 403])
def test_client_errors_fail_fast(monkeypatch, chunk, code):
    calls = _serve(monkeypatch, [_http_error(code)])
    with pytest.raises(tr.TranscribeError, match=f"HTTP {code}"):
        tr.transcribe_chunk(*chunk)
    assert len(calls) == 1


def test_persistent_5xx_gives_up_after_retries(monkeypatch, chunk):
    calls = _serve(monkeypatch, [_http_error(525)] * tr.MAX_RETRIES)
    with pytest.raises(tr.TranscribeError, match="failed after 4 attempts: HTTP 525"):
        tr.transcribe_chunk(*chunk)
    assert len(calls) == tr.MAX_RETRIES
    assert not chunk[1].exists()
