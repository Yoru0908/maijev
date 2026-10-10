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
