from __future__ import annotations

import os
from dataclasses import dataclass

from printcad.providers.anthropic import AnthropicProvider
from printcad.providers.base import Provider
from printcad.providers.google import GoogleProvider
from printcad.providers.openai_compat import OpenAICompatProvider, env_key


@dataclass
class ProviderInfo:
    name: str
    kind: str
    default_model: str
    env_keys: tuple[str, ...]
    notes: str


PROVIDERS: dict[str, ProviderInfo] = {
    "openai": ProviderInfo("openai", "openai", "gpt-4.1", ("OPENAI_API_KEY",), "OpenAI Chat Completions"),
    "anthropic": ProviderInfo("anthropic", "anthropic", "claude-sonnet-4-5", ("ANTHROPIC_API_KEY",), "Claude Messages API"),
    "claude": ProviderInfo("claude", "anthropic", "claude-sonnet-4-5", ("ANTHROPIC_API_KEY",), "Alias for anthropic"),
    "google": ProviderInfo("google", "google", "gemini-2.5-pro", ("GEMINI_API_KEY", "GOOGLE_API_KEY"), "Gemini generateContent"),
    "gemini": ProviderInfo("gemini", "google", "gemini-2.5-pro", ("GEMINI_API_KEY", "GOOGLE_API_KEY"), "Alias for google"),
    "xai": ProviderInfo("xai", "openai", "grok-4", ("XAI_API_KEY", "GROK_API_KEY"), "xAI OpenAI-compatible"),
    "grok": ProviderInfo("grok", "openai", "grok-4", ("XAI_API_KEY", "GROK_API_KEY"), "Alias for xai"),
    "openrouter": ProviderInfo(
        "openrouter",
        "openai",
        "openrouter/free",
        ("OPENROUTER_API_KEY",),
        "OpenRouter; printcad.toml models, or pass --model vendor/model",
    ),
    "ollama": ProviderInfo("ollama", "openai", "llama3.1", (), "Local Ollama /v1; no key required"),
    "compatible": ProviderInfo(
        "compatible",
        "openai",
        "gpt-4o",
        ("PRINTCAD_API_KEY", "OPENAI_API_KEY"),
        "Any OpenAI-compatible server via --base-url",
    ),
}


def list_providers() -> list[ProviderInfo]:
    seen: set[str] = set()
    out: list[ProviderInfo] = []
    for info in PROVIDERS.values():
        if info.name in seen:
            continue
        if info.name in {"claude", "gemini", "grok"}:
            continue
        seen.add(info.name)
        out.append(info)
    return out


def get_provider(name: str, model: str | None, base_url: str | None, api_key: str | None) -> Provider:
    key = name.lower().strip()
    if key not in PROVIDERS:
        known = ", ".join(sorted({p.name for p in list_providers()}))
        raise ValueError(f"Unknown provider '{name}'. Known: {known}")
    info = PROVIDERS[key]
    model = model or os.environ.get("PRINTCAD_MODEL") or info.default_model
    api_key = api_key or env_key(*info.env_keys)

    if info.kind == "anthropic":
        if not api_key:
            raise ValueError("Set ANTHROPIC_API_KEY or pass --api-key")
        return AnthropicProvider(api_key=api_key, model=model)

    if info.kind == "google":
        if not api_key:
            raise ValueError("Set GEMINI_API_KEY or GOOGLE_API_KEY")
        return GoogleProvider(api_key=api_key, model=model)

    if key == "openai":
        url = base_url or os.environ.get("OPENAI_BASE_URL") or "https://api.openai.com/v1"
        if not api_key:
            raise ValueError("Set OPENAI_API_KEY")
        return OpenAICompatProvider("openai", url, api_key, model)

    if key in {"xai", "grok"}:
        url = base_url or "https://api.x.ai/v1"
        if not api_key:
            raise ValueError("Set XAI_API_KEY")
        return OpenAICompatProvider("xai", url, api_key, model)

    if key == "openrouter":
        url = base_url or "https://openrouter.ai/api/v1"
        if not api_key:
            raise ValueError("Set OPENROUTER_API_KEY")
        headers = {
            "HTTP-Referer": os.environ.get("OPENROUTER_REFERER", "https://localhost"),
            "X-Title": "printcad",
        }
        return OpenAICompatProvider("openrouter", url, api_key, model, extra_headers=headers)

    if key == "ollama":
        url = base_url or os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434") + "/v1"
        if not url.endswith("/v1"):
            url = url.rstrip("/") + "/v1"
        return OpenAICompatProvider("ollama", url, api_key or "ollama", model)

    url = base_url or os.environ.get("PRINTCAD_BASE_URL")
    if not url:
        raise ValueError("compatible provider requires --base-url or PRINTCAD_BASE_URL")
    if not url.endswith("/v1"):
        url = url.rstrip("/") + "/v1"
    return OpenAICompatProvider("compatible", url, api_key or "none", model)
