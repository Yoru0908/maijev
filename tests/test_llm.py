"""OpenRouter fallback model-id handling — no network."""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import flows.maijev.llm as llm  # noqa: E402


@pytest.mark.parametrize(
    "given, sent",
    [
        ("gemini-3.8-flash", "google/gemini-3.8-flash"),
        ("google/gemini-3.1-pro-preview", "google/gemini-3.1-pro-preview"),
        ("deepseek/deepseek-v4.1-flash", "deepseek/deepseek-v4.1-flash"),
    ],
)
def test_openrouter_adds_vendor_prefix_only_when_missing(
    monkeypatch, given, sent
):
    seen = {}

    def fake_urlopen(req, timeout=0):
        seen["model"] = json.loads(req.data)["model"]
        return io.BytesIO(b'{"choices":[{"message":{"content":"{}"}}]}')

    monkeypatch.setattr(llm.urllib.request, "urlopen", fake_urlopen)
    llm._generate_openrouter(
        "p", system=None, model=given, json_mode=True, timeout=1, api_key="k"
    )
    assert seen["model"] == sent


def test_default_model_is_gemini_3_8_flash():
    assert llm.DEFAULT_MODEL == "gemini-3.8-flash"


@pytest.mark.parametrize("model", llm.SUPPORTED_MODELS)
def test_supported_models_accept_optional_vendor_prefix(model):
    assert llm.validate_model(model) == model
    assert llm.validate_model("google/" + model) == model


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
