"""Local printcad.toml: OpenRouter key and the free models a prompt-only run tries."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    tomllib = None  # type: ignore[assignment]

PLACEHOLDER_KEY = "PASTE_OPENROUTER_API_KEY"

# Upper-end free models with tool calling on the OpenRouter catalog, 2026-10-07.
# Largest reasoning / coding-agent endpoints first; the free router is the last resort.
DEFAULT_MODELS = (
    "nvidia/nemotron-3-ultra-550b-a55b:free",
    "nvidia/nemotron-3-super-120b-a12b:free",
    "cohere/north-mini-code:free",
    "poolside/laguna-s-2.1:free",
    "google/gemma-4-31b-it:free",
    "openrouter/free",
)

RETRYABLE_MARKERS = (
    "http 402",
    "http 404",
    "http 408",
    "http 409",
    "http 429",
    "http 500",
    "http 502",
    "http 503",
    "http 504",
    "timeout",
    "rate limit",
    "rate-limit",
    "no endpoints",
    "overloaded",
    "provider_overloaded",
    "upstream error",
    "service temporarily",
    "provider returned error",
    "free-tier",
)


@dataclass
class Config:
    path: Path
    api_key: str
    models: list[str]
    session: Path
    assume_defaults: bool
    max_iters: int


def config_search_paths() -> list[Path]:
    paths: list[Path] = []
    env = os.environ.get("PRINTCAD_CONFIG")
    if env:
        paths.append(Path(env))
    paths.append(Path.cwd() / "printcad.toml")
    paths.append(Path(__file__).resolve().parents[1] / "printcad.toml")
    paths.append(Path.home() / ".config" / "printcad" / "config.toml")
    unique: list[Path] = []
    seen: set[Path] = set()
    for path in paths:
        resolved = path.expanduser()
        if resolved in seen:
            continue
        seen.add(resolved)
        unique.append(resolved)
    return unique


def find_config() -> Path | None:
    for path in config_search_paths():
        if path.is_file():
            return path
    return None


def load_config(path: Path | None = None) -> Config:
    found = path or find_config()
    if found is None:
        searched = ", ".join(str(p) for p in config_search_paths())
        raise FileNotFoundError(
            "No printcad.toml. Copy printcad.toml.example to printcad.toml and paste an OpenRouter key. "
            f"Looked in: {searched}"
        )
    data = _loads_toml(found.read_text())
    if not isinstance(data, dict):
        raise ValueError(f"{found} must be a TOML table")
    raw_key = str(data.get("openrouter_api_key") or "").strip()
    if not raw_key or raw_key == PLACEHOLDER_KEY:
        raw_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    models = data.get("models") or list(DEFAULT_MODELS)
    if not isinstance(models, list) or not all(isinstance(item, str) and item.strip() for item in models):
        raise ValueError(f"{found} models must be a list of model ids")
    session_raw = str(data.get("session") or "session")
    session = Path(session_raw)
    if not session.is_absolute():
        session = found.parent / session
    max_iters = int(data.get("max_iters") or 14)
    assume = data.get("assume_defaults", True)
    return Config(
        path=found,
        api_key=raw_key,
        models=[item.strip() for item in models],
        session=session,
        assume_defaults=bool(assume),
        max_iters=max_iters,
    )


def require_api_key(cfg: Config) -> str:
    if cfg.api_key:
        return cfg.api_key
    raise ValueError(
        f"OpenRouter API key missing in {cfg.path}. "
        f"Set openrouter_api_key (replace {PLACEHOLDER_KEY}) or export OPENROUTER_API_KEY."
    )


def retryable_failure(summary: str, tool_calls: int) -> bool:
    text = (summary or "").lower()
    if any(marker in text for marker in RETRYABLE_MARKERS):
        return True
    return tool_calls == 0


def _loads_toml(text: str) -> dict:
    if tomllib is not None:
        data = tomllib.loads(text)
        if not isinstance(data, dict):
            raise ValueError("config must be a TOML table")
        return data
    return _parse_simple_toml(text)


def _parse_simple_toml(text: str) -> dict:
    """Enough of TOML for the flat printcad.toml schema on Python 3.10."""
    data: dict = {}
    lines = text.splitlines()
    index = 0
    while index < len(lines):
        raw = lines[index].strip()
        index += 1
        if not raw or raw.startswith("#"):
            continue
        if "=" not in raw:
            raise ValueError(f"unsupported config line: {raw}")
        key, value = raw.split("=", 1)
        key = key.strip()
        value = value.strip()
        if value.startswith("["):
            items: list[str] = []
            body = value
            while "]" not in body:
                if index >= len(lines):
                    raise ValueError(f"unclosed array for {key}")
                body += "\n" + lines[index]
                index += 1
            inner = body[body.index("[") + 1 : body.rindex("]")]
            for part in inner.split(","):
                item = part.strip().strip('"').strip("'")
                if item and not item.startswith("#"):
                    items.append(item)
            data[key] = items
            continue
        data[key] = _parse_scalar(value)
    return data


def _parse_scalar(value: str):
    if value.startswith('"') or value.startswith("'"):
        quote = value[0]
        if len(value) < 2 or not value.endswith(quote):
            raise ValueError(f"unterminated string: {value}")
        return value[1:-1]
    lowered = value.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    if value.isdigit():
        return int(value)
    raise ValueError(f"unsupported config value: {value}")
