"""Cheap checks for generated model.py before OpenCascade runs."""

from __future__ import annotations

import ast
import re


HINT_BUILDER_ADD = (
    "ERROR: do not combine a BuildPart builder with += / +. "
    "Use `with BuildPart() as bp:` then `return bp.part`. "
    "To fuse solids: `solid = bp.part; solid = solid + other` "
    "or `add(other)` inside the `with` block. "
    "Never `part += shape` when `part` is the BuildPart context."
)


def lint_model(code: str) -> str | None:
    if "def build(" not in code:
        return "ERROR: model.py must define build(params)"
    if re.search(r"\bpart\s*\+=", code) and "BuildPart" in code:
        return HINT_BUILDER_ADD
    if re.search(r"\b(from\s+build123d\.operations_generic\s+import\s+[^\n]*\bhull\b|\bimport\s+hull\b)", code):
        return (
            "ERROR: hull is not in this build123d (operations_generic has no hull). "
            "Do not import it. For a regular dodecahedron: 1 cm = 10 mm; "
            "put side length in params['side'] as 10; build the 20 golden-ratio "
            "vertices, scale so edge length is side, then Solid/Shell from the "
            "12 pentagon faces. Or call regular_dodecahedron(side) — compat injects it. "
            "Do not stack cubes and roofs."
        )
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        return f"ERROR: syntax: {exc}"
    builder_names = _builder_aliases(tree)
    for node in ast.walk(tree):
        if isinstance(node, ast.AugAssign) and isinstance(node.op, ast.Add):
            if isinstance(node.target, ast.Name) and node.target.id in builder_names:
                return HINT_BUILDER_ADD
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            if isinstance(node.left, ast.Name) and node.left.id in builder_names:
                return HINT_BUILDER_ADD
    return None


def _builder_aliases(tree: ast.AST) -> set[str]:
    names = {"part"} if _uses_buildpart(tree) else set()
    for node in ast.walk(tree):
        # with BuildPart() as foo
        if isinstance(node, ast.With):
            for item in node.items:
                call = item.context_expr
                if _is_buildpart_call(call) and item.optional_vars and isinstance(item.optional_vars, ast.Name):
                    names.add(item.optional_vars.id)
    return names


def _uses_buildpart(tree: ast.AST) -> bool:
    return any(_is_buildpart_call(n) or (isinstance(n, ast.Name) and n.id == "BuildPart") for n in ast.walk(tree))


def _is_buildpart_call(node: ast.AST) -> bool:
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    return (isinstance(func, ast.Name) and func.id == "BuildPart") or (
        isinstance(func, ast.Attribute) and func.attr == "BuildPart"
    )
