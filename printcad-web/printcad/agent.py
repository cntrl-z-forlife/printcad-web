from __future__ import annotations

import json
from typing import Any, Callable

from printcad.prompts import SYSTEM
from printcad.providers.anthropic import assistant_tool_use_message, merge_anthropic_tool_results
from printcad.providers.base import LLMResponse, Provider, ToolCall
from printcad.providers.google import assistant_function_message, google_contents
from printcad.providers.openai_compat import openai_messages
from printcad.schema import Params, Spec, anthropic_tools, google_tools, openai_tools
from printcad.session import Session
from printcad import runner


LogFn = Callable[[str], None]
AskFn = Callable[[list[str], list[str]], dict[str, str]]


class Agent:
    def __init__(
        self,
        session: Session,
        provider: Provider,
        log: LogFn | None = None,
        max_iters: int = 10,
        ask: AskFn | None = None,
        assume_defaults: bool = False,
    ):
        self.session = session
        self.provider = provider
        self.log = log or (lambda _msg: None)
        self.max_iters = max_iters
        self.ask = ask
        self.assume_defaults = assume_defaults
        self.finished = False
        self.finish_summary = ""
        self.finish_ok = False
        self.tool_calls_made = 0

    def run(self, user_text: str, mode: str) -> str:
        bundle = self.session.context_bundle()
        user = (
            f"MODE: {mode}\n"
            f"USER REQUEST:\n{user_text}\n\n"
            f"{bundle}"
        )
        history = self._seed_history(user)
        last_text = ""
        for step in range(1, self.max_iters + 1):
            self.log(f"[{step}/{self.max_iters}] {self.provider.name}:{getattr(self.provider, 'model', '')}")
            try:
                response = self._call(history)
            except RuntimeError as exc:
                self.finished = True
                self.finish_ok = False
                self.finish_summary = str(exc)
                self.log(str(exc))
                break
            if response.text:
                last_text = response.text
                self.log(response.text.strip()[:500])
            if not response.tool_calls:
                self.finished = True
                self.finish_summary = response.text or last_text
                break
            history = self._append_assistant(history, response)
            results: list[tuple[ToolCall, str]] = []
            for call in response.tool_calls:
                self.tool_calls_made += 1
                self.log(f"  tool {call.name}({_short_args(call.arguments)})")
                result = self._dispatch(call)
                results.append((call, result))
                if self.finished:
                    break
            history = self._append_tool_results(history, results)
            if self.finished:
                break
        self.session.append_history(
            {
                "mode": mode,
                "user": user_text,
                "summary": self.finish_summary or last_text,
                "ok": self.finish_ok or bool(self.session.report() and self.session.report().ok),
            }
        )
        return self.finish_summary or last_text or "Stopped without a finish message."

    def _seed_history(self, user: str) -> list[dict[str, Any]]:
        if self.provider.kind == "google":
            return [{"role": "user", "parts": [{"text": user}]}]
        return [{"role": "user", "content": user}]

    def _call(self, history: list[dict[str, Any]]) -> LLMResponse:
        if self.provider.kind == "anthropic":
            messages = [{"role": "system", "content": SYSTEM}, *history]
            return self.provider.complete(messages, tools=anthropic_tools())
        if self.provider.kind == "google":
            messages = google_contents(SYSTEM, history)
            return self.provider.complete(messages, tools=google_tools())
        messages = openai_messages(SYSTEM, history)
        return self.provider.complete(messages, tools=openai_tools())

    def _append_assistant(self, history: list[dict[str, Any]], response: LLMResponse) -> list[dict[str, Any]]:
        if self.provider.kind == "anthropic":
            return history + [assistant_tool_use_message(response)]
        if self.provider.kind == "google":
            return history + [assistant_function_message(response)]
        tool_calls = []
        for call in response.tool_calls:
            raw_args = json.dumps(call.arguments)
            if len(raw_args) > 6000:
                raw_args = json.dumps({"rejected": "arguments omitted from history; too large"})
            tool_calls.append(
                {
                    "id": call.id,
                    "type": "function",
                    "function": {
                        "name": call.name,
                        "arguments": raw_args,
                    },
                }
            )
        history = history + [
            {
                "role": "assistant",
                "content": response.text or None,
                "tool_calls": tool_calls,
            }
        ]
        return history

    def _append_tool_results(
        self, history: list[dict[str, Any]], results: list[tuple[ToolCall, str]]
    ) -> list[dict[str, Any]]:
        if self.provider.kind == "anthropic":
            packed = [self.provider.format_tool_result(call, result) for call, result in results]
            return history + [merge_anthropic_tool_results(packed)]
        extras = [self.provider.format_tool_result(call, result) for call, result in results]
        return history + extras

    def _dispatch(self, call: ToolCall) -> str:
        name = call.name
        args = call.arguments or {}
        try:
            if name == "set_spec":
                spec = Spec(
                    summary=args.get("summary") or "",
                    process=args.get("process") or "FDM",
                    envelope_mm=args.get("envelope_mm"),
                    print_orientation=args.get("print_orientation") or "",
                    family=args.get("family") or "",
                    notes=list(args.get("notes") or []),
                    constraints=list(args.get("constraints") or []),
                )
                self.session.write_spec(spec)
                return "spec saved"
            if name == "set_params":
                values = {str(k): float(v) for k, v in (args.get("values") or {}).items()}
                units = {str(k): str(v) for k, v in (args.get("units") or {}).items()}
                current = Params() if args.get("replace") else self.session.params()
                self.session.write_params(current.merged(values, units))
                return json.dumps(self.session.params().model_dump())
            if name == "write_model":
                from printcad.lint_model import lint_model

                code = args.get("code") or ""
                if len(code) > 8000:
                    return (
                        "ERROR: model.py is too long "
                        f"({len(code)} chars). Rewrite under 8000 chars. "
                        "One build(params), no comments essay, no second copy of the file."
                    )
                problem = lint_model(code)
                if problem:
                    return problem
                self.session.write_model(code)
                return f"wrote model.py ({len(code)} chars)"
            if name == "apply_patch":
                msg = self.session.apply_replace(args["target"], args["old_str"], args["new_str"])
                if args.get("target") == "model.py":
                    from printcad.lint_model import lint_model

                    problem = lint_model(self.session.model_path.read_text())
                    if problem:
                        return problem
                return msg
            if name == "run_model":
                report = runner.run_model(self.session)
                return report.model_dump_json()
            if name == "inspect":
                report = runner.inspect_step(self.session)
                return report.model_dump_json()
            if name == "lookup_device":
                from .catalog import case_params_from_device, lookup_device

                found = lookup_device(str(args.get("query") or ""))
                if not found:
                    return json.dumps({"found": False, "hint": "unknown device; ask_user or refuse to guess body size"})
                suggested = case_params_from_device(found)
                return json.dumps({"found": True, "device": found, "suggested_case_params": suggested})
            if name == "ask_user":
                questions = [str(q) for q in (args.get("questions") or [])]
                assumptions = [str(a) for a in (args.get("assumptions") or [])]
                if self.assume_defaults or self.ask is None:
                    payload = {
                        "status": "defaults_applied",
                        "assumptions": assumptions,
                        "answers": {},
                    }
                    return json.dumps(payload)
                answers = self.ask(questions, assumptions)
                return json.dumps({"status": "answered", "answers": answers, "assumptions": assumptions})
            if name == "finish":
                self.finished = True
                self.finish_summary = args.get("summary") or ""
                self.finish_ok = bool(args.get("ok"))
                extra = args.get("follow_up")
                if extra:
                    self.finish_summary = self.finish_summary + "\n" + extra
                return "finished"
            return f"unknown tool {name}"
        except Exception as exc:
            return f"ERROR: {exc}"


def _short_args(args: dict[str, Any]) -> str:
    if not args:
        return ""
    blob = json.dumps(args)
    if "code" in args:
        return f"code={len(str(args.get('code') or ''))} chars"
    return blob if len(blob) < 180 else blob[:177] + "..."
