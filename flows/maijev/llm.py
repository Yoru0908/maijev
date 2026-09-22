"""Minimal synchronous Gemini client for maijev.

Backend priority:
  - GEMINI_AGENT_PLATFORM_API_KEY / AGENT_PLATFORM_API_KEY → Vertex publisher
    endpoint {GEMINI_AGENT_PLATFORM_BASE_URL}/{model}:generateContent
  - GEMINI_API_KEY → Google AI Studio endpoint
    generativelanguage.googleapis.com/v1beta/models/{model}:generateContent
  - OPENROUTER_API_KEY → OpenRouter chat/completions fallback
    (model from LLM_MODEL env, default google/gemini-2.5-flash)
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

DEFAULT_BASE_URL = (
    "https://aiplatform.googleapis.com/v1beta1/publishers/google/models"
)
FALLBACK_BASE_URL = (
    "https://us-central1-aiplatform.googleapis.com/v1beta1"
    "/publishers/google/models"
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


def generate(
    prompt: str,
    *,
    system: str | None = None,
    model: str | None = None,
    json_mode: bool = True,
    timeout: int = 600,
) -> LLMResult:
    openrouter_key = os.environ.get("OPENROUTER_API_KEY")
    agent_key = os.environ.get("GEMINI_AGENT_PLATFORM_API_KEY") or os.environ.get(
        "AGENT_PLATFORM_API_KEY"
    )
    # Agent Platform (Vertex publisher endpoint) is the primary backend;
    # OpenRouter is only a fallback when no Gemini-native key exists —
    # the ASR stage shares OPENROUTER_API_KEY, so it must not hijack LLM calls.
    if (
        openrouter_key
        and not agent_key
        and not os.environ.get("GEMINI_API_KEY")
    ):
        return _generate_openrouter(
            prompt, system=system, model=model,
            json_mode=json_mode, timeout=timeout, api_key=openrouter_key,
        )

    model = model or os.environ.get("GEMINI_MODEL", "gemini-3-flash-preview")
    body: dict[str, Any] = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "maxOutputTokens": int(
                os.environ.get("LLM_MAX_OUTPUT_TOKENS", "65000")
            ),
        },
    }
    if system:
        body["systemInstruction"] = {"parts": [{"text": system}]}
    if json_mode:
        body["generationConfig"]["responseMimeType"] = "application/json"

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
        # Vertex publisher endpoint 404 → retry on the global host.
        if e.code == 404 and api_key and "us-central1" not in url:
            url = f"{FALLBACK_BASE_URL}/{model}:generateContent?key={api_key}"
            payload = _post(url, body, timeout)
        else:
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

def _generate_openrouter(
    prompt: str,
    *,
    system: str | None,
    model: str | None,
    json_mode: bool,
    timeout: int,
    api_key: str,
) -> LLMResult:
    model = model or os.environ.get("LLM_MODEL", "google/gemini-2.5-flash")
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    body: dict[str, Any] = {"model": model, "messages": messages}
    if json_mode:
        body["response_format"] = {"type": "json_object"}

    req = urllib.request.Request(
        "https://openrouter.ai/api/v1/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"OpenRouter HTTP {e.code}: {e.read()[:300]!r}") from e

    if "error" in payload:
        raise RuntimeError(f"OpenRouter error: {payload['error']}")
    choice = (payload.get("choices") or [{}])[0]
    usage = payload.get("usage") or {}
    return LLMResult(
        text=(choice.get("message") or {}).get("content", ""),
        prompt_tokens=usage.get("prompt_tokens", 0),
        output_tokens=usage.get("completion_tokens", 0),
    )


def generate_json(prompt: str, **kwargs) -> Any:
    """generate() + tolerant JSON extraction (strips code fences)."""
    result = generate(prompt, **kwargs)
    text = result.text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0]
    return json.loads(text)
