from __future__ import annotations

import ast
from typing import Any

from sub_agent_runtime.tooling.lint.ast_utils import (
    _ast_name_matches,
    _with_context_builder_name,
)


def _find_buildsketch_countersink_context_hits(tree: ast.AST) -> list[dict[str, Any]]:
    if not isinstance(tree, ast.Module):
        return []

    class _Visitor(ast.NodeVisitor):
        def __init__(self) -> None:
            self._context_stack: list[str] = []
            self._hits: list[dict[str, Any]] = []
            self._seen_lines: set[int] = set()

        @property
        def hits(self) -> list[dict[str, Any]]:
            return self._hits

        def visit_With(self, node: ast.With) -> None:  # noqa: N802
            self._visit_with_like(node.items, node.body)

        def visit_AsyncWith(self, node: ast.AsyncWith) -> None:  # noqa: N802
            self._visit_with_like(node.items, node.body)

        def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
            if _ast_name_matches(node.func, "CounterSinkHole") and "BuildSketch" in self._context_stack:
                line_no = int(getattr(node, "lineno", 0) or 0)
                if line_no not in self._seen_lines:
                    self._seen_lines.add(line_no)
                    self._hits.append({"line_no": line_no})
            self.generic_visit(node)

        def _visit_with_like(
            self,
            items: list[ast.withitem],
            body: list[ast.stmt],
        ) -> None:
            added_contexts: list[str] = []
            for item in items:
                builder_name = _with_context_builder_name(item.context_expr)
                if builder_name is None:
                    continue
                added_contexts.append(builder_name)
                self._context_stack.append(builder_name)
            try:
                for statement in body:
                    self.visit(statement)
            finally:
                for _ in added_contexts:
                    self._context_stack.pop()

    visitor = _Visitor()
    visitor.visit(tree)
    return visitor.hits


def _find_countersink_keyword_alias_hits(tree: ast.AST) -> list[dict[str, Any]]:
    if not isinstance(tree, ast.Module):
        return []
    hits: list[dict[str, Any]] = []
    seen: set[tuple[int, str]] = set()
    valid_helper_names = {"CounterSinkHole", "CountersinkHole", "CounterSink"}
    alias_names = {
        "countersink_radius",
        "countersink_angle",
        "countersink_depth",
        "counter_sink_depth",
        "angle",
        "cone_angle",
        "head_diameter",
        "head_radius",
        "countersink_diameter",
        "counter_sink_head_radius",
        "counter_sink_diameter",
        "head_dia",
        "countersink_dia",
        "counter_sink_dia",
        "thru_diameter",
        "through_diameter",
        "through_hole_diameter",
        "hole_diameter",
        "diameter",
        "thru_dia",
        "through_dia",
        "hole_dia",
    }
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not any(_ast_name_matches(node.func, helper_name) for helper_name in valid_helper_names):
            continue
        line_no = int(getattr(node, "lineno", 0) or 0)
        for keyword in node.keywords:
            alias_name = str(getattr(keyword, "arg", "") or "").strip()
            if alias_name not in alias_names:
                continue
            cache_key = (line_no, alias_name)
            if cache_key in seen:
                continue
            seen.add(cache_key)
            hits.append({"line_no": line_no, "alias_name": alias_name})
    return hits


def collect_countersink_contract_hits(parsed_tree: ast.AST) -> list[dict[str, Any]]:
    hits: list[dict[str, Any]] = []
    for alias_hit in _find_countersink_keyword_alias_hits(parsed_tree):
        line_no = int(alias_hit.get("line_no") or 0)
        alias_name = str(alias_hit.get("alias_name") or "").strip()
        if alias_name == "countersink_radius":
            hits.append(
                {
                    "rule_id": "invalid_build123d_keyword.countersink_radius_alias",
                    "message": (
                        "`CounterSinkHole(...)` uses `counter_sink_radius=...`, not "
                        "`countersink_radius=...`."
                    ),
                    "repair_hint": (
                        "Rename the keyword to `counter_sink_radius=` when calling "
                        "`CounterSinkHole(...)`."
                        + (
                            f" Repair the countersink radius keyword at line {line_no}."
                            if line_no > 0
                            else ""
                        )
                    ),
                }
            )
        if alias_name in {
            "head_diameter",
            "head_radius",
            "countersink_diameter",
            "counter_sink_diameter",
            "countersink_radius",
            "counter_sink_head_radius",
            "head_dia",
            "countersink_dia",
            "counter_sink_dia",
        }:
            hits.append(
                {
                    "rule_id": "invalid_build123d_keyword.countersink_head_diameter_alias",
                    "message": (
                        "`CounterSinkHole(...)` uses `counter_sink_radius=...`, not "
                        f"`{alias_name}=`."
                    ),
                    "repair_hint": (
                        "Convert the requested countersink head diameter to a radius and pass "
                        "it as `counter_sink_radius=` when calling `CounterSinkHole(...)`."
                        + (
                            f" Repair the countersink head-diameter keyword at line {line_no}."
                            if line_no > 0
                            else ""
                        )
                    ),
                }
            )
        if alias_name in {
            "thru_diameter",
            "through_diameter",
            "through_hole_diameter",
            "hole_diameter",
            "diameter",
            "thru_dia",
            "through_dia",
            "hole_dia",
        }:
            hits.append(
                {
                    "rule_id": "invalid_build123d_keyword.countersink_through_diameter_alias",
                    "message": (
                        "`CounterSinkHole(...)` uses `radius=...` for the through-hole size, "
                        f"not `{alias_name}=`."
                    ),
                    "repair_hint": (
                        "Convert the requested through-hole diameter to a radius and pass it "
                        "as `radius=` when calling `CounterSinkHole(...)`."
                        + (
                            f" Repair the countersink through-hole keyword at line {line_no}."
                            if line_no > 0
                            else ""
                        )
                    ),
                }
            )
        if alias_name in {"countersink_angle", "angle", "cone_angle"}:
            wrong_keyword = (
                "`countersink_angle=`"
                if alias_name == "countersink_angle"
                else ("`cone_angle=`" if alias_name == "cone_angle" else "`angle=`")
            )
            hits.append(
                {
                    "rule_id": "invalid_build123d_keyword.countersink_angle_alias",
                    "message": (
                        "`CounterSinkHole(...)` uses `counter_sink_angle=...`, not "
                        f"{wrong_keyword}."
                    ),
                    "repair_hint": (
                        "Rename the keyword to `counter_sink_angle=` when calling "
                        "`CounterSinkHole(...)`."
                        + (
                            f" Repair the countersink angle keyword at line {line_no}."
                            if line_no > 0
                            else ""
                        )
                    ),
                }
            )
        if alias_name in {"countersink_depth", "counter_sink_depth"}:
            hits.append(
                {
                    "rule_id": "invalid_build123d_keyword.countersink_depth_alias",
                    "message": (
                        "`CounterSinkHole(...)` does not accept "
                        f"`{alias_name}=`. Keep `depth=` for the through-hole depth and "
                        "describe the countersink with `counter_sink_radius=` plus "
                        "`counter_sink_angle=`."
                    ),
                    "repair_hint": (
                        "Remove the guessed countersink-depth keyword. Keep `depth=` for the "
                        "through-hole depth only, and express the countersink with "
                        "`counter_sink_radius=` plus `counter_sink_angle=` when calling "
                        "`CounterSinkHole(...)`."
                        + (
                            f" Repair the countersink depth keyword at line {line_no}."
                            if line_no > 0
                            else ""
                        )
                    ),
                }
            )
    return hits


__all__ = [name for name in globals() if not name.startswith("__")]
