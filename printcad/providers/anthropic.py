from __future__ import annotations

import uuid
from typing import Any

import httpx

from printcad.providers.base import LLMResponse, Provider, ToolCall
from printcad.providers.openai_compat import raise_httpx, http_timeout


class AnthropicProvider(Provider):
    name = "anthropic"
    kind = "anthropic"

    def __init__(self, api_key: str, model: str):
        self.api_key = api_key
        self.model = model

    def complete(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None) -> LLMResponse:
        system = ""
        body_msgs: list[dict[str, Any]] = []
        for msg in messages:
            if msg.get("role") == "system":
                system = msg.get("content") or ""
                continue
            body_msgs.append(msg)
        payload: dict[str, Any] = {
            "model": self.model,
            "max_tokens": 8192,
            "temperature": 0.2,
            "messages": body_msgs,
        }
        if system:
            payload["system"] = system
        if tools:
            payload["tools"] = tools
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        with httpx.Client(timeout=http_timeout()) as client:
            try:
                resp = client.post("https://api.anthropic.com/v1/messages", headers=headers, json=payload)
                resp.raise_for_status()
            except (httpx.TimeoutException, httpx.HTTPStatusError) as exc:
                raise_httpx(exc)
            data = resp.json()
        text_parts: list[str] = []
        calls: list[ToolCall] = []
        for block in data.get("content") or []:
            btype = block.get("type")
            if btype == "text":
                text_parts.append(block.get("text") or "")
            elif btype == "tool_use":
                calls.append(
                    ToolCall(
                        id=block.get("id") or uuid.uuid4().hex,
                        name=block.get("name") or "",
                        arguments=block.get("input") or {},
                    )
                )
        return LLMResponse(text="\n".join(text_parts), tool_calls=calls, raw=data)

    def format_tool_result(self, call: ToolCall, result: str) -> dict[str, Any]:
        return {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": call.id,
                    "content": result,
                }
            ],
        }


def merge_anthropic_tool_results(results: list[dict[str, Any]]) -> dict[str, Any]:
    blocks = []
    for item in results:
        blocks.extend(item.get("content") or [])
    return {"role": "user", "content": blocks}


def assistant_tool_use_message(response: LLMResponse) -> dict[str, Any]:
    content: list[dict[str, Any]] = []
    if response.text:
        content.append({"type": "text", "text": response.text})
    for call in response.tool_calls:
        content.append(
            {
                "type": "tool_use",
                "id": call.id,
                "name": call.name,
                "input": call.arguments,
            }
        )
    return {"role": "assistant", "content": content}
