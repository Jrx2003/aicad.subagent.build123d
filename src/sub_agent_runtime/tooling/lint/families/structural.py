from __future__ import annotations

import ast
from typing import Any

from sub_agent_runtime.tooling.lint.ast_utils import (
    _ast_expr_text,
    _ast_name_matches,
    _subscript_index_value,
    _with_context_builder_name,
)


def _find_buildpart_sketch_primitive_context_hits(tree: ast.AST) -> list[dict[str, Any]]:
    if not isinstance(tree, ast.Module):
        return []

    class _Visitor(ast.NodeVisitor):
        _sketch_helper_names = {
            "Circle",
            "Ellipse",
            "Rectangle",
            "RectangleRounded",
            "RegularPolygon",
            "Polygon",
            "SlotCenterToCenter",
            "SlotOverall",
            "Text",
            "Trapezoid",
        }

        def __init__(self) -> None:
            self._context_stack: list[str] = []
            self._hits: list[dict[str, Any]] = []
            self._seen: set[tuple[int, str]] = set()

        @property
        def hits(self) -> list[dict[str, Any]]:
            return self._hits

        def visit_With(self, node: ast.With) -> None:  # noqa: N802
            self._visit_with_like(node.items, node.body)

        def visit_AsyncWith(self, node: ast.AsyncWith) -> None:  # noqa: N802
            self._visit_with_like(node.items, node.body)

        def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
            helper_name = next(
                (
                    name
                    for name in self._sketch_helper_names
                    if _ast_name_matches(node.func, name)
                ),
                None,
            )
            if (
                helper_name is not None
                and "BuildPart" in self._context_stack
                and "BuildSketch" not in self._context_stack
            ):
                line_no = int(getattr(node, "lineno", 0) or 0)
                cache_key = (line_no, helper_name)
                if cache_key not in self._seen:
                    self._seen.add(cache_key)
                    self._hits.append(
                        {"line_no": line_no, "helper_name": helper_name}
                    )
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


def _find_rectanglerounded_radius_bounds_hits(tree: ast.AST) -> list[dict[str, Any]]:
    if not isinstance(tree, ast.Module):
        return []
    numeric_env = _collect_numeric_assignment_env(tree)
    hits: list[dict[str, Any]] = []
    seen_lines: set[int] = set()
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and _ast_name_matches(node.func, "RectangleRounded")):
            continue
        width_expr, height_expr, radius_expr = _rectanglerounded_dimension_args(node)
        width_value = _eval_numeric_expr(width_expr, numeric_env)
        height_value = _eval_numeric_expr(height_expr, numeric_env)
        radius_value = _eval_numeric_expr(radius_expr, numeric_env)
        if width_value is None or height_value is None or radius_value is None:
            continue
        min_span = min(float(width_value), float(height_value))
        if min_span <= 0:
            continue
        if 2.0 * float(radius_value) < min_span:
            continue
        line_no = int(getattr(node, "lineno", 0) or 0)
        if line_no in seen_lines:
            continue
        seen_lines.add(line_no)
        hits.append(
            {
                "line_no": line_no,
                "width": float(width_value),
                "height": float(height_value),
                "radius": float(radius_value),
            }
        )
    return hits


def _rectanglerounded_dimension_args(
    node: ast.Call,
) -> tuple[ast.AST | None, ast.AST | None, ast.AST | None]:
    positional = list(node.args[:3])
    keyword_map = {
        str(getattr(keyword, "arg", "") or "").strip(): keyword.value
        for keyword in node.keywords
        if str(getattr(keyword, "arg", "") or "").strip()
    }
    width_expr = positional[0] if len(positional) >= 1 else keyword_map.get("width")
    height_expr = positional[1] if len(positional) >= 2 else keyword_map.get("height")
    radius_expr = positional[2] if len(positional) >= 3 else keyword_map.get("radius")
    return width_expr, height_expr, radius_expr


def _collect_numeric_assignment_env(tree: ast.AST) -> dict[str, float]:
    ordered_bindings = _collect_ordered_numeric_assignment_bindings(tree)
    env: dict[str, float] = {}
    if not ordered_bindings:
        return env

    max_passes = max(len(ordered_bindings) + 1, 2)
    for _ in range(max_passes):
        next_env = dict(env)
        for name, value_expr in ordered_bindings:
            value = _eval_numeric_expr(value_expr, next_env)
            if value is None:
                continue
            next_env[name] = value
        if next_env == env:
            return next_env
        env = next_env
    return env


def _collect_ordered_numeric_assignment_bindings(
    tree: ast.AST,
) -> list[tuple[str, ast.AST]]:
    if not isinstance(tree, ast.Module):
        return []

    bindings: list[tuple[str, ast.AST]] = []

    class _Visitor(ast.NodeVisitor):
        def visit_Assign(self, node: ast.Assign) -> None:  # noqa: N802
            for target in node.targets:
                if not isinstance(target, ast.Name):
                    continue
                name = str(target.id or "").strip()
                if name:
                    bindings.append((name, node.value))
            self.generic_visit(node)

        def visit_AnnAssign(self, node: ast.AnnAssign) -> None:  # noqa: N802
            if isinstance(node.target, ast.Name) and node.value is not None:
                name = str(node.target.id or "").strip()
                if name:
                    bindings.append((name, node.value))
            self.generic_visit(node)

    _Visitor().visit(tree)
    return bindings


def _eval_numeric_expr(node: ast.AST | None, env: dict[str, float]) -> float | None:
    if node is None:
        return None
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return float(node.value)
    if isinstance(node, ast.Name):
        return env.get(str(node.id or "").strip())
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        operand = _eval_numeric_expr(node.operand, env)
        if operand is None:
            return None
        return operand if isinstance(node.op, ast.UAdd) else -operand
    if isinstance(node, ast.BinOp) and isinstance(
        node.op,
        (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod, ast.Pow),
    ):
        left = _eval_numeric_expr(node.left, env)
        right = _eval_numeric_expr(node.right, env)
        if left is None or right is None:
            return None
        if isinstance(node.op, ast.Add):
            return left + right
        if isinstance(node.op, ast.Sub):
            return left - right
        if isinstance(node.op, ast.Mult):
            return left * right
        if isinstance(node.op, ast.Div):
            return left / right if right != 0 else None
        if isinstance(node.op, ast.FloorDiv):
            return left // right if right != 0 else None
        if isinstance(node.op, ast.Mod):
            return left % right if right != 0 else None
        if isinstance(node.op, ast.Pow):
            return left**right
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        func_name = str(node.func.id or "").strip()
        arg_values = [_eval_numeric_expr(arg, env) for arg in node.args]
        if any(value is None for value in arg_values):
            return None
        numeric_args = [float(value) for value in arg_values if value is not None]
        if func_name == "max" and numeric_args:
            return max(numeric_args)
        if func_name == "min" and numeric_args:
            return min(numeric_args)
        if func_name == "abs" and len(numeric_args) == 1:
            return abs(numeric_args[0])
    return None


def _find_transform_context_manager_hits(tree: ast.AST) -> list[dict[str, Any]]:
    if not isinstance(tree, ast.Module):
        return []
    hits: list[dict[str, Any]] = []
    seen: set[tuple[int, str]] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.With, ast.AsyncWith)):
            continue
        for item in node.items:
            context_expr = item.context_expr
            if not isinstance(context_expr, ast.Call):
                continue
            helper_name = next(
                (
                    name
                    for name in ("Rot", "Pos")
                    if _ast_name_matches(context_expr.func, name)
                ),
                None,
            )
            if helper_name is None:
                continue
            line_no = int(getattr(context_expr, "lineno", 0) or getattr(node, "lineno", 0) or 0)
            cache_key = (line_no, helper_name)
            if cache_key in seen:
                continue
            seen.add(cache_key)
            hits.append({"line_no": line_no, "helper_name": helper_name})
    return hits


def _find_member_fillet_radius_keyword_conflict_hits(
    tree: ast.AST,
) -> list[dict[str, Any]]:
    if not isinstance(tree, ast.Module):
        return []
    hits: list[dict[str, Any]] = []
    seen_lines: set[int] = set()
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "fillet"
            and node.args
        ):
            continue
        if not any(
            str(getattr(keyword, "arg", "") or "").strip() == "radius"
            for keyword in node.keywords
        ):
            continue
        line_no = int(getattr(node, "lineno", 0) or 0)
        if line_no in seen_lines:
            continue
        seen_lines.add(line_no)
        target_label = "solid"
        if isinstance(node.func.value, ast.Name) and node.func.value.id:
            target_label = str(node.func.value.id)
        hits.append({"line_no": line_no, "target_label": target_label})
    return hits


def _find_global_fillet_helper_argument_contract_hits(
    tree: ast.AST,
) -> list[dict[str, Any]]:
    if not isinstance(tree, ast.Module):
        return []
    hits: list[dict[str, Any]] = []
    seen_lines: set[int] = set()
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Call)
            and _ast_name_matches(node.func, "fillet")
            and len(node.args) > 2
        ):
            continue
        line_no = int(getattr(node, "lineno", 0) or 0)
        if line_no in seen_lines:
            continue
        seen_lines.add(line_no)
        target_label = "shape"
        first_arg = node.args[0]
        if isinstance(first_arg, ast.Name) and first_arg.id:
            target_label = str(first_arg.id)
        elif isinstance(first_arg, ast.Attribute):
            target_label = _ast_expr_text(first_arg)
        hits.append({"line_no": line_no, "target_label": target_label})
    return hits


def _find_topology_geometry_attribute_hits(tree: ast.AST) -> list[dict[str, Any]]:
    if not isinstance(tree, ast.Module):
        return []
    hits: list[dict[str, Any]] = []
    seen: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Attribute) or node.attr != "geometry":
            continue
        line_no = int(getattr(node, "lineno", 0) or 0)
        if line_no in seen:
            continue
        seen.add(line_no)
        hits.append({"line_no": line_no})
    return hits


def _expr_is_build123d_vector_like(node: ast.AST) -> bool:
    return isinstance(node, ast.BinOp) and isinstance(node.op, (ast.MatMult, ast.Mod))


def _find_vector_component_indexing_hits(tree: ast.AST) -> list[dict[str, Any]]:
    if not isinstance(tree, ast.Module):
        return []
    vector_aliases: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign) or not _expr_is_build123d_vector_like(node.value):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id:
                vector_aliases.add(target.id)

    hits: list[dict[str, Any]] = []
    seen: set[tuple[int, int]] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Subscript):
            continue
        index_value = _subscript_index_value(node)
        if index_value not in {0, 1, 2}:
            continue
        value = node.value
        if not (
            _expr_is_build123d_vector_like(value)
            or (isinstance(value, ast.Name) and value.id in vector_aliases)
        ):
            continue
        line_no = int(getattr(node, "lineno", 0) or 0)
        cache_key = (line_no, index_value)
        if cache_key in seen:
            continue
        seen.add(cache_key)
        hits.append({"line_no": line_no, "index_value": index_value})
    return hits


__all__ = [name for name in globals() if not name.startswith("__")]
