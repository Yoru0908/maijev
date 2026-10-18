"""Model whitelist and backend routing — no network."""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import flows.maijev.llm as llm  # noqa: E402

KEYS = ("GEMINI_AGENT_PLATFORM_API_KEY", "AGENT_PLATFORM_API_KEY", "GEMINI_API_KEY",
        "ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL", "OPENAI_API_KEY", "OPENAI_BASE_URL",
        "LLM_RELAY_BASE_URL", "LLM_RELAY_API_KEY", "OPENROUTER_API_KEY", "GEMINI_MODEL")


@pytest.fixture
def sent(monkeypatch):
    """Clear every key and record each outgoing request."""
    for k in KEYS:
        monkeypatch.delenv(k, raising=False)
    calls = []

    def fake_urlopen(req, timeout=0):
        calls.append({"url": req.full_url, "body": json.loads(req.data),
                      "headers": {k.lower(): v for k, v in req.header_items()}})
        if "/v1/messages" in req.full_url:
            reply = {"content": [{"type": "text", "text": 'Here you go:\n{"ok": 1}'}]}
        elif "generateContent" in req.full_url:
            reply = {"candidates": [{"content": {"parts": [{"text": '{"ok": 1}'}]}}]}
        else:
            reply = {"choices": [{"message": {"content": '```json\n{"ok": 1}\n```'}}]}
        return io.BytesIO(json.dumps(reply).encode())

    monkeypatch.setattr(llm.urllib.request, "urlopen", fake_urlopen)
    return calls


def test_gemini_key_beats_the_asr_openrouter_key(monkeypatch, sent):
    monkeypatch.setenv("AGENT_PLATFORM_API_KEY", "g")
    monkeypatch.setenv("OPENROUTER_API_KEY", "or")
    assert llm.generate_json("p", model="gemini-3.8-flash") == {"ok": 1}
    assert "aiplatform.googleapis.com" in sent[0]["url"]


def test_openrouter_gets_vendor_ids(monkeypatch, sent):
    monkeypatch.setenv("OPENROUTER_API_KEY", "or")
    for model in ("gemini-4-argon", "claude-sonnet-5-5", "gpt-6.1-sol"):
        assert llm.generate_json("p", model=model) == {"ok": 1}
    assert [c["body"]["model"] for c in sent] == [
        "google/gemini-4-argon", "anthropic/claude-sonnet-5.5", "openai/gpt-6.1-sol"]
    assert all(c["url"] == "https://openrouter.ai/api/v1/chat/completions" for c in sent)


def test_anthropic_native_request_shape(monkeypatch, sent):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "a")
    monkeypatch.setenv("OPENROUTER_API_KEY", "or")
    assert llm.generate_json("p", system="s", model="claude-opus-5-5") == {"ok": 1}
    [c] = sent
    assert c["url"] == "https://api.anthropic.com/v1/messages"
    assert c["headers"]["x-api-key"] == "a" and c["headers"]["anthropic-version"]
    assert c["body"]["system"] == "s" and c["body"]["max_tokens"] == 65536
    assert c["body"]["model"] == "claude-opus-5-5"


def test_openai_native_sends_no_output_cap(monkeypatch, sent):
    monkeypatch.setenv("OPENAI_API_KEY", "o")
    llm.generate_json("p", model="gpt-6.1-sol")
    [c] = sent
    assert c["url"] == "https://api.openai.com/v1/chat/completions"
    assert c["headers"]["authorization"] == "Bearer o"
    assert c["body"]["model"] == "gpt-6.1-sol"
    assert "max_tokens" not in c["body"]


def test_relay_takes_native_ids_before_openrouter(monkeypatch, sent):
    monkeypatch.setenv("LLM_RELAY_BASE_URL", "https://relay.example/v1/")
    monkeypatch.setenv("LLM_RELAY_API_KEY", "r")
    monkeypatch.setenv("OPENROUTER_API_KEY", "or")
    llm.generate_json("p", model="claude-sonnet-5-5")
    assert sent[0]["url"] == "https://relay.example/v1/chat/completions"
    assert sent[0]["body"]["model"] == "claude-sonnet-5-5"


def test_model_without_backend_fails_before_sending(sent):
    with pytest.raises(ValueError, match="No API key"):
        llm.check_model("claude-sonnet-5-5")
    with pytest.raises(RuntimeError, match="No API key"):
        llm.generate("p", model="gpt-6.1-sol")
    assert sent == []


def test_default_model_is_gemini_3_8_flash():
    assert llm.DEFAULT_MODEL == "gemini-3.8-flash"


@pytest.mark.parametrize("model", llm.SUPPORTED_MODELS)
def test_supported_models_accept_their_openrouter_id(model):
    assert llm.validate_model(model) == model
    assert llm.validate_model(llm.MODELS[model]) == model


def test_gemini_ids_accept_bare_google_prefix():
    assert llm.validate_model("google/gemini-3.8-flash") == "gemini-3.8-flash"


def test_stale_environment_model_is_rejected(monkeypatch):
    monkeypatch.delenv("TRANSLATE_MODEL", raising=False)
    monkeypatch.setenv("GEMINI_MODEL", "gemini-3-flash-preview")
    with pytest.raises(ValueError, match="Unsupported LLM model"):
        llm.resolve_model("TRANSLATE_MODEL")


def test_unsupported_model_never_sends_request(monkeypatch):
    monkeypatch.setattr(llm, "_post", lambda *a, **k: pytest.fail("sent a model request"))
    with pytest.raises(ValueError, match="Unsupported LLM model"):
        llm.generate("p", model="gemini-3-flash-preview")


def test_invalid_model_fails_before_media_work(monkeypatch, tmp_path):
    from flows.maijev.pipeline import run
    monkeypatch.delenv("SEGMENT_MODEL", raising=False)
    monkeypatch.setenv("GEMINI_MODEL", "gemini-3-flash-preview")
    work = tmp_path / "work"
    with pytest.raises(ValueError, match="Unsupported LLM model"):
        run(tmp_path / "missing.mp4", work, llm_segment=True)
    assert not work.exists()


def test_gui_rejects_invalid_model_before_creating_job(monkeypatch, tmp_path):
    from fastapi import HTTPException
    from flows.maijev.gui import server
    monkeypatch.setattr(server, "PROCS", {})
    work = tmp_path / "job"
    req = server.JobRequest(input="missing.mp4", model="gemini-3-flash-preview")
    with pytest.raises(HTTPException) as exc:
        server._launch(work, req)
    assert exc.value.status_code == 400
    assert not work.exists()


def test_vertex_404_reports_the_original_error_without_retry(monkeypatch, sent):
    monkeypatch.setenv("AGENT_PLATFORM_API_KEY", "g")

    def not_found(req, timeout=0):
        sent.append(req.full_url)
        raise llm.urllib.error.HTTPError(req.full_url, 404, "nf", {}, io.BytesIO(b"model gone"))

    monkeypatch.setattr(llm.urllib.request, "urlopen", not_found)
    with pytest.raises(RuntimeError, match="LLM HTTP 404.*model gone"):
        llm.generate("p", model="gemini-3.8-flash")
    assert len(sent) == 1


def test_request_timeout_defaults_long_and_follows_env(monkeypatch, sent):
    monkeypatch.setenv("OPENROUTER_API_KEY", "or")
    seen = []
    monkeypatch.setattr(llm, "_generate_openai", lambda *a, **k: seen.append(k["timeout"]) or llm.LLMResult("{}"))
    llm.generate("p", model="gpt-6.1-sol")
    monkeypatch.setenv("LLM_TIMEOUT", "300")
    llm.generate("p", model="gpt-6.1-sol")
    assert seen == [1800, 300]
