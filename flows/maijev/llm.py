"""Minimal synchronous LLM client for maijev.

Each model family tries its native API first, then an OpenAI-compatible
relay, then OpenRouter:
  - gemini-*: GEMINI_AGENT_PLATFORM_API_KEY / AGENT_PLATFORM_API_KEY → Vertex
    publisher endpoint {GEMINI_AGENT_PLATFORM_BASE_URL}/{model}:generateContent;
    GEMINI_API_KEY → Google AI Studio
  - claude-*: ANTHROPIC_API_KEY → {ANTHROPIC_BASE_URL}/v1/messages
  - gpt-*: OPENAI_API_KEY → {OPENAI_BASE_URL}/chat/completions
  - LLM_RELAY_BASE_URL + LLM_RELAY_API_KEY → relay chat/completions
  - OPENROUTER_API_KEY → OpenRouter chat/completions
OPENROUTER_API_KEY is also the ASR key, so it is always the last resort.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

# The only models permitted for LLM stages: native id → OpenRouter id.
# Gemini is the tested and recommended family; 3.x takes LLM_THINKING_LEVEL.
DEFAULT_MODEL = "gemini-3.8-flash"
MODELS = {
    "gemini-3.1-pro-preview": "google/gemini-3.1-pro-preview",
    "gemini-3.8-flash": "google/gemini-3.8-flash",
    "gemini-4-argon": "google/gemini-4-argon",
    "claude-sonnet-5-5": "anthropic/claude-sonnet-5.5",
    "claude-opus-5-5": "anthropic/claude-opus-5.5",
    "claude-fable-5-1": "anthropic/claude-fable-5.1",
    "gpt-6.1-sol": "openai/gpt-6.1-sol",
}
SUPPORTED_MODELS = tuple(MODELS)
OPENROUTER_IDS = {v: k for k, v in MODELS.items()}

OPENROUTER_URL = "https://openrouter.ai/api/v1"
ANTHROPIC_URL = "https://api.anthropic.com"
OPENAI_URL = "https://api.openai.com/v1"


def validate_model(model: str) -> str:
    """Reject stale overrides before they can send a paid model request."""
    normalized = OPENROUTER_IDS.get(model, model.removeprefix("google/"))
    if normalized not in SUPPORTED_MODELS:
        raise ValueError(
            f"Unsupported LLM model {model!r}; choose "
            + " / ".join(SUPPORTED_MODELS)
        )
    return normalized

DEFAULT_BASE_URL = (
    "https://aiplatform.googleapis.com/v1beta1/publishers/google/models"
)


@dataclass
class LLMResult:
    text: str
    prompt_tokens: int = 0
    output_tokens: int = 0


def _post(url: str, body: dict, timeout: int = 600) -> dict:
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8")
    return json.loads(raw)


def _route(model: str) -> tuple[str, str, str] | None:
    """(kind, base_url, key) of the first configured backend for model."""
    env = os.environ.get
    if model.startswith("gemini-"):
        if env("GEMINI_AGENT_PLATFORM_API_KEY") or env("AGENT_PLATFORM_API_KEY") \
                or env("GEMINI_API_KEY"):
            return ("gemini", "", "")
    elif model.startswith("claude-") and env("ANTHROPIC_API_KEY"):
        return ("anthropic", env("ANTHROPIC_BASE_URL") or ANTHROPIC_URL,
                env("ANTHROPIC_API_KEY"))
    elif model.startswith("gpt-") and env("OPENAI_API_KEY"):
        return ("openai", env("OPENAI_BASE_URL") or OPENAI_URL, env("OPENAI_API_KEY"))
    if env("LLM_RELAY_BASE_URL") and env("LLM_RELAY_API_KEY"):
        return ("openai", env("LLM_RELAY_BASE_URL"), env("LLM_RELAY_API_KEY"))
    if env("OPENROUTER_API_KEY"):
        return ("openai", OPENROUTER_URL, env("OPENROUTER_API_KEY"))
    return None


def check_model(model: str) -> str:
    """validate_model + a configured backend, before any media work."""
    model = validate_model(model)
    if not _route(model):
        raise ValueError(f"No API key configured for LLM model {model!r}")
    return model


def generate(
    prompt: str,
    *,
    system: str | None = None,
    model: str | None = None,
    json_mode: bool = True,
    timeout: int = 600,
) -> LLMResult:
    model = validate_model(model or os.environ.get("GEMINI_MODEL") or DEFAULT_MODEL)
    route = _route(model)
    if not route:
        raise RuntimeError(f"No API key configured for LLM model {model!r}")
    kind, base, key = route
    if kind == "anthropic":
        return _generate_anthropic(prompt, system=system, model=model,
                                   timeout=timeout, base_url=base, api_key=key)
    if kind == "openai":
        if base == OPENROUTER_URL:
            model = MODELS[model]
        return _generate_openai(prompt, system=system, model=model,
                                json_mode=json_mode, timeout=timeout,
                                base_url=base, api_key=key)

    body: dict[str, Any] = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "maxOutputTokens": int(
                os.environ.get("LLM_MAX_OUTPUT_TOKENS", "65536")
            ),
        },
    }
    if system:
        body["systemInstruction"] = {"parts": [{"text": system}]}
    if json_mode:
        body["generationConfig"]["responseMimeType"] = "application/json"
    # Gemini 3.x models use thinkingLevel (LOW/MEDIUM/HIGH) instead of the
    # 2.x thinkingBudget field; only sent when explicitly configured.
    thinking_level = os.environ.get("LLM_THINKING_LEVEL", "").strip()
    if thinking_level:
        body["generationConfig"]["thinkingConfig"] = {
            "thinkingLevel": thinking_level.upper()
        }

    studio_key = os.environ.get("GEMINI_API_KEY")
    api_key = os.environ.get("GEMINI_AGENT_PLATFORM_API_KEY") or os.environ.get(
        "AGENT_PLATFORM_API_KEY"
    )
    if api_key:
        base = os.environ.get("GEMINI_AGENT_PLATFORM_BASE_URL", DEFAULT_BASE_URL)
        url = f"{base.rstrip('/')}/{model}:generateContent?key={api_key}"
    elif studio_key:
        url = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{model}:generateContent?key={studio_key}"
        )
    else:
        raise RuntimeError(
            "Set GEMINI_API_KEY or GEMINI_AGENT_PLATFORM_API_KEY"
        )

    try:
        payload = _post(url, body, timeout)
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"LLM HTTP {e.code}: {e.read()[:300]!r}") from e

    if "error" in payload:
        raise RuntimeError(f"LLM error: {payload['error']}")

    text_parts = []
    for cand in payload.get("candidates", []):
        for part in (cand.get("content") or {}).get("parts", []):
            if "text" in part:
                text_parts.append(part["text"])
    usage = payload.get("usageMetadata") or {}
    return LLMResult(
        text="".join(text_parts),
        prompt_tokens=usage.get("promptTokenCount", 0),
        output_tokens=usage.get("candidatesTokenCount", 0),
    )

def resolve_model(stage_env: str) -> str:
    """Model for one stage: stage env > GEMINI_MODEL > DEFAULT_MODEL."""
    return validate_model(
        os.environ.get(stage_env)
        or os.environ.get("GEMINI_MODEL")
        or DEFAULT_MODEL
    )


def _send(url: str, body: dict, headers: dict, timeout: int, label: str) -> dict:
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", **headers},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"{label} HTTP {e.code}: {e.read()[:300]!r}") from e
    if "error" in payload:
        raise RuntimeError(f"{label} error: {payload['error']}")
    return payload


def _generate_openai(
    prompt: str,
    *,
    system: str | None,
    model: str,
    json_mode: bool,
    timeout: int,
    base_url: str,
    api_key: str,
) -> LLMResult:
    """OpenAI chat/completions: OpenAI itself, relays and OpenRouter."""
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    body: dict[str, Any] = {"model": model, "messages": messages}
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    payload = _send(f"{base_url.rstrip('/')}/chat/completions", body,
                    {"Authorization": f"Bearer {api_key}"}, timeout, base_url)
    choice = (payload.get("choices") or [{}])[0]
    usage = payload.get("usage") or {}
    return LLMResult(
        text=(choice.get("message") or {}).get("content", ""),
        prompt_tokens=usage.get("prompt_tokens", 0),
        output_tokens=usage.get("completion_tokens", 0),
    )


def _generate_anthropic(
    prompt: str,
    *,
    system: str | None,
    model: str,
    timeout: int,
    base_url: str,
    api_key: str,
) -> LLMResult:
    """Anthropic Messages API; it has no JSON mode, generate_json copes."""
    body: dict[str, Any] = {
        "model": model,
        "max_tokens": int(os.environ.get("LLM_MAX_OUTPUT_TOKENS", "65536")),
        "messages": [{"role": "user", "content": prompt}],
    }
    if system:
        body["system"] = system
    payload = _send(f"{base_url.rstrip('/')}/v1/messages", body,
                    {"x-api-key": api_key, "anthropic-version": "2023-06-01"},
                    timeout, "Anthropic")
    usage = payload.get("usage") or {}
    return LLMResult(
        text="".join(b.get("text", "") for b in payload.get("content", [])
                     if b.get("type") == "text"),
        prompt_tokens=usage.get("input_tokens", 0),
        output_tokens=usage.get("output_tokens", 0),
    )


def generate_json(prompt: str, **kwargs) -> Any:
    """generate() + tolerant JSON extraction (strips code fences)."""
    result = generate(prompt, **kwargs)
    text = result.text.strip()
    # Models without a JSON mode may wrap the object in a fence or a preamble.
    start = min((i for i in (text.find("{"), text.find("[")) if i >= 0), default=0)
    end = max(text.rfind("}"), text.rfind("]")) + 1 or len(text)
    return json.loads(text[start:end])
