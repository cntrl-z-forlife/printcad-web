from __future__ import annotations

import json
import os
import uuid
from typing import Any

import httpx

from printcad.providers.base import LLMResponse, Provider, ToolCall


class OpenAICompatProvider(Provider):
    kind = "openai"

    def __init__(
        self,
        name: str,
        base_url: str,
        api_key: str,
        model: str,
        extra_headers: dict[str, str] | None = None,
    ):
        self.name = name
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.extra_headers = extra_headers or {}

    def complete(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None) -> LLMResponse:
        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}" if self.api_key else "",
            "Content-Type": "application/json",
            **self.extra_headers,
        }
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.2,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        with httpx.Client(timeout=http_timeout()) as client:
            try:
                resp = client.post(url, headers=headers, json=payload)
                resp.raise_for_status()
            except (httpx.TimeoutException, httpx.HTTPStatusError) as exc:
                raise_httpx(exc)
            data = resp.json()
        message = _chat_message(data, resp.status_code)
        text = message.get("content") or ""
        calls: list[ToolCall] = []
        for raw in message.get("tool_calls") or []:
            fn = raw.get("function") or {}
            args = fn.get("arguments") or "{}"
            if isinstance(args, str):
                try:
                    parsed = json.loads(args or "{}")
                except json.JSONDecodeError:
                    parsed = {}
            else:
                parsed = args
            calls.append(
                ToolCall(
                    id=raw.get("id") or uuid.uuid4().hex,
                    name=fn.get("name") or "",
                    arguments=parsed,
                )
            )
        return LLMResponse(text=text, tool_calls=calls, raw=data)

    def format_tool_result(self, call: ToolCall, result: str) -> dict[str, Any]:
        return {"role": "tool", "tool_call_id": call.id, "content": result}


def _chat_message(data: Any, status: int) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise RuntimeError(f"LLM response was not a JSON object (HTTP {status}): {str(data)[:800]}")
    err = data.get("error")
    if err:
        raise RuntimeError(f"LLM error (HTTP {status}): {_short(err)}")
    choices = data.get("choices")
    if not choices:
        raise RuntimeError(
            "LLM response had no choices (HTTP "
            f"{status}). Free-tier hosts often do this on rate limit or context overflow. "
            f"Body: {_short(data)}"
        )
    first = choices[0] if isinstance(choices, list) else None
    if not isinstance(first, dict):
        raise RuntimeError(f"LLM choices[0] was not an object (HTTP {status}): {_short(first)}")
    message = first.get("message") or {}
    if not isinstance(message, dict):
        raise RuntimeError(f"LLM message was not an object (HTTP {status}): {_short(message)}")
    finish = first.get("finish_reason")
    if finish == "length" and not message.get("tool_calls") and not message.get("content"):
        raise RuntimeError("LLM hit max output length before a tool call or text. Retry or use a smaller model.py.")
    return message


def _short(value: Any) -> str:
    try:
        text = value if isinstance(value, str) else json.dumps(value)
    except TypeError:
        text = str(value)
    return text[:1200]


def openai_messages(system: str, history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{"role": "system", "content": system}, *history]


def http_timeout() -> httpx.Timeout:
    raw = os.environ.get("PRINTCAD_HTTP_TIMEOUT", "300")
    try:
        seconds = float(raw)
    except ValueError:
        seconds = 300.0
    return httpx.Timeout(seconds, connect=min(30.0, seconds))


def raise_httpx(exc: Exception) -> None:
    if isinstance(exc, httpx.TimeoutException):
        seconds = os.environ.get("PRINTCAD_HTTP_TIMEOUT", "300")
        raise RuntimeError(
            f"LLM HTTP timeout after {seconds}s ({exc}). "
            "Slow cloud models (Ollama Cloud, large tool calls) often need "
            "--http-timeout 600 or PRINTCAD_HTTP_TIMEOUT=600."
        ) from exc
    if isinstance(exc, httpx.HTTPStatusError):
        raise RuntimeError(_http_error(exc)) from exc
    raise RuntimeError(str(exc)) from exc


def _http_error(exc: httpx.HTTPStatusError) -> str:
    body = exc.response.text[:1500]
    return f"HTTP {exc.response.status_code} from {exc.request.url}: {body}"


def env_key(*names: str) -> str:
    for name in names:
        val = os.environ.get(name)
        if val:
            return val
    return ""
