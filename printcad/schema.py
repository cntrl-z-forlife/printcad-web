from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class Spec(BaseModel):
    summary: str = ""
    process: str = "FDM"
    units: str = "mm"
    envelope_mm: list[float] | None = None
    print_orientation: str = ""
    notes: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    family: str = ""


class Params(BaseModel):
    values: dict[str, float] = Field(default_factory=dict)
    units: dict[str, str] = Field(default_factory=dict)

    def merged(self, updates: dict[str, float], unit_updates: dict[str, str] | None = None) -> "Params":
        values = dict(self.values)
        units = dict(self.units)
        values.update(updates)
        if unit_updates:
            units.update(unit_updates)
        for key in updates:
            units.setdefault(key, "mm")
        return Params(values=values, units=units)


class InspectReport(BaseModel):
    ok: bool
    valid: bool | None = None
    bbox_mm: dict[str, float] | None = None
    size_mm: list[float] | None = None
    volume_mm3: float | None = None
    step_path: str | None = None
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    extras: dict[str, Any] = Field(default_factory=dict)


TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "name": "set_spec",
        "description": (
            "Create or replace the engineering spec for this session. "
            "Call this first on a new part. Units are always millimeters."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "summary": {"type": "string"},
                "process": {
                    "type": "string",
                    "description": "Manufacturing process, e.g. FDM, SLA, SLS.",
                },
                "envelope_mm": {
                    "type": "array",
                    "items": {"type": "number"},
                    "minItems": 3,
                    "maxItems": 3,
                    "description": "Max X Y Z size in mm.",
                },
                "print_orientation": {"type": "string"},
                "family": {
                    "type": "string",
                    "description": "plate | l_bracket | box | clip | bushing | phone_case | enclosure | other",
                },
                "notes": {"type": "array", "items": {"type": "string"}},
                "constraints": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["summary"],
        },
    },
    {
        "name": "set_params",
        "description": (
            "Create or merge named numeric parameters. Prefer this for second-take "
            "dimensional changes. Do not put raw dimensions in model.py."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "values": {
                    "type": "object",
                    "additionalProperties": {"type": "number"},
                    "description": "Parameter name to numeric value. Example: {\"length\": 80, \"hole_d\": 3.4}",
                },
                "units": {
                    "type": "object",
                    "additionalProperties": {"type": "string"},
                    "description": "Optional unit labels; default mm.",
                },
                "replace": {
                    "type": "boolean",
                    "description": "If true, replace the whole param set. Default merge.",
                },
            },
            "required": ["values"],
        },
    },
    {
        "name": "write_model",
        "description": (
            "Write the full build123d model.py. Must define build(params: dict) "
            "and return a single solid. Read every dimension from params."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "Complete Python source for model.py"},
            },
            "required": ["code"],
        },
    },
    {
        "name": "apply_patch",
        "description": (
            "Replace an exact substring in model.py or params. Use for second-take "
            "feature edits instead of rewriting the whole file."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "target": {"type": "string", "enum": ["model.py", "params.json"]},
                "old_str": {"type": "string"},
                "new_str": {"type": "string"},
            },
            "required": ["target", "old_str", "new_str"],
        },
    },
    {
        "name": "run_model",
        "description": "Execute model.py with current params and export STEP. Always inspect after.",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "inspect",
        "description": (
            "Measure the last STEP: validity, bounding box, volume, envelope, "
            "and compare hole count/size to the user prompt. "
            "prompt_check.mismatches means the solid does not match the request — fix and rerun."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "lookup_device",
        "description": (
            "Look up published outer dimensions for a named phone or similar device. "
            "Use this before modeling a case, holder, or sleeve. Returns body size and "
            "suggested case params (inner = device + clearance, outer = inner + walls)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "e.g. iPhone 12 mini"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "ask_user",
        "description": (
            "Ask the user questions in the CLI, then continue this same take with their answers. "
            "Use when required dimensions are missing. Do not call finish after this; "
            "wait for the tool result and keep modeling."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "questions": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Short questions the user must answer.",
                },
                "assumptions": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "What you will assume if they say 'just proceed'.",
                },
            },
            "required": ["questions"],
        },
    },
    {
        "name": "finish",
        "description": "Stop the tool loop and report the result to the user.",
        "parameters": {
            "type": "object",
            "properties": {
                "summary": {"type": "string"},
                "ok": {"type": "boolean"},
                "follow_up": {"type": "string", "description": "Optional question or next-step hint."},
            },
            "required": ["summary", "ok"],
        },
    },
]


def openai_tools() -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t["description"],
                "parameters": t["parameters"],
            },
        }
        for t in TOOL_DEFINITIONS
    ]


def anthropic_tools() -> list[dict[str, Any]]:
    return [
        {
            "name": t["name"],
            "description": t["description"],
            "input_schema": t["parameters"],
        }
        for t in TOOL_DEFINITIONS
    ]


def google_tools() -> list[dict[str, Any]]:
    decls = []
    for t in TOOL_DEFINITIONS:
        decls.append(
            {
                "name": t["name"],
                "description": t["description"],
                "parameters": t["parameters"],
            }
        )
    return [{"functionDeclarations": decls}]
