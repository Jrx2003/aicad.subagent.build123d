from __future__ import annotations

import ast
from typing import Any

from sub_agent_runtime.tooling.execution import (
    _build123d_exported_symbol_names,
    _buildpart_with_alias,
    _call_is_subtractive_buildpart,
    _call_materializes_additive_host,
    _call_subtractive_without_host_operation_name,
    _call_targets_named_part_transform_method,
    _call_uses_mode_private,
    _call_uses_mode_subtract,
    _expression_references_name,
    _expression_references_part_attr,
    _host_part_arithmetic_assignment,
    _locations_context_suggests_local_feature_placement,
    _module_imports_build123d_symbols,
    _primitive_constructor_name,
    _temporary_primitive_arithmetic_expr,
    _temporary_primitive_transform_expr,
    _with_context_builder_name,
    _with_context_is_locations,
)
from sub_agent_runtime.tooling.lint.ast_utils import (
    _ast_expr_text,
    _ast_name_matches,
    _collect_module_local_binding_names,
)


def _find_nested_buildpart_part_arithmetic_hits(tree: ast.AST) -> list[dict[str, Any]]:
    if not isinstance(tree, ast.Module):
        return []
    hits: list[dict[str, Any]] = []
    seen: set[tuple[str, tuple[str, ...], int]] = set()
    for node in ast.walk(tree):
        host_alias = _buildpart_with_alias(node)
        if not host_alias:
            continue
        outer_body = ast.Module(body=list(getattr(node, "body", [])), type_ignores=[])
        nested_aliases = {
            alias
            for child in ast.walk(outer_body)
            if (alias := _buildpart_with_alias(child))
        }
        nested_aliases.discard(host_alias)
        if not nested_aliases:
            continue
        for child in ast.walk(outer_body):
            assignment = _host_part_arithmetic_assignment(
                node=child,
                host_alias=host_alias,
            )
            if assignment is None:
                continue
            value_expr, line_no = assignment
            referenced_aliases = tuple(
                sorted(
                    alias
                    for alias in nested_aliases
                    if _expression_references_part_attr(value_expr, alias)
                )
            )
            if not referenced_aliases:
                continue
            key = (host_alias, referenced_aliases, line_no)
            if key in seen:
                continue
            seen.add(key)
            hits.append(
                {
                    "host_alias": host_alias,
                    "nested_aliases": list(referenced_aliases),
                    "line_no": line_no,
                }
            )
    return hits


def _find_nested_buildpart_part_transform_hits(tree: ast.AST) -> list[dict[str, Any]]:
    if not isinstance(tree, ast.Module):
        return []
    hits: list[dict[str, Any]] = []
    seen: set[tuple[str, str, int]] = set()
    for node in ast.walk(tree):
        host_alias = _buildpart_with_alias(node)
        if not host_alias:
            continue
        outer_body = ast.Module(body=list(getattr(node, "body", [])), type_ignores=[])
        nested_aliases = {
            alias
            for child in ast.walk(outer_body)
            if (alias := _buildpart_with_alias(child))
        }
        nested_aliases.discard(host_alias)
        if not nested_aliases:
            continue
        for child in ast.walk(outer_body):
            if not isinstance(child, ast.Call):
                continue
            for nested_alias in nested_aliases:
                method_name = _call_targets_named_part_transform_method(child, nested_alias)
                if not method_name:
                    continue
                line_no = int(getattr(child, "lineno", 0) or 0)
                key = (nested_alias, method_name, line_no)
                if key in seen:
                    continue
                seen.add(key)
                hits.append(
                    {
                        "host_alias": host_alias,
                        "nested_alias": nested_alias,
                        "method_name": method_name,
                        "line_no": line_no,
                    }
                )
    return hits


def _find_nested_subtractive_buildpart_hits(tree: ast.AST) -> list[dict[str, Any]]:
    if not isinstance(tree, ast.Module):
        return []

    class _Visitor(ast.NodeVisitor):
        def __init__(self) -> None:
            self._builder_stack: list[str] = []
            self._locations_depth = 0
            self._hits: list[dict[str, Any]] = []
            self._seen: set[tuple[int, bool]] = set()

        @property
        def hits(self) -> list[dict[str, Any]]:
            return self._hits

        def visit_With(self, node: ast.With) -> None:  # noqa: N802
            self._visit_with_like(node.items, node.body)

        def visit_AsyncWith(self, node: ast.AsyncWith) -> None:  # noqa: N802
            self._visit_with_like(node.items, node.body)

        def _visit_with_like(
            self,
            items: list[ast.withitem],
            body: list[ast.stmt],
        ) -> None:
            pushed_builders = 0
            pushed_locations = 0
            try:
                for item in items:
                    context_expr = item.context_expr
                    if _with_context_builder_name(context_expr) == "BuildPart":
                        if _call_is_subtractive_buildpart(context_expr) and self._builder_stack:
                            line_no = int(
                                getattr(context_expr, "lineno", 0)
                                or getattr(item, "lineno", 0)
                                or 0
                            )
                            cache_key = (line_no, self._locations_depth > 0)
                            if cache_key not in self._seen:
                                self._seen.add(cache_key)
                                self._hits.append(
                                    {
                                        "line_no": line_no,
                                        "inside_locations": self._locations_depth > 0,
                                    }
                                )
                        self._builder_stack.append("BuildPart")
                        pushed_builders += 1
                        continue
                    if _with_context_is_locations(context_expr):
                        self._locations_depth += 1
                        pushed_locations += 1
                for statement in body:
                    self.visit(statement)
            finally:
                for _ in range(pushed_builders):
                    self._builder_stack.pop()
                self._locations_depth = max(0, self._locations_depth - pushed_locations)

    visitor = _Visitor()
    visitor.visit(tree)
    return visitor.hits


def _find_detached_subtractive_builder_without_host_hits(
    tree: ast.AST,
) -> list[dict[str, Any]]:
    if not isinstance(tree, ast.Module):
        return []

    class _Visitor(ast.NodeVisitor):
        def __init__(self, *, builder_alias: str) -> None:
            self._builder_alias = builder_alias
            self._host_materialized = False
            self._hits: list[dict[str, Any]] = []
            self._seen_lines: set[int] = set()

        @property
        def hits(self) -> list[dict[str, Any]]:
            return self._hits

        def visit_With(self, node: ast.With) -> None:  # noqa: N802
            if any(
                _with_context_builder_name(item.context_expr) == "BuildPart"
                for item in node.items
            ):
                return
            self.generic_visit(node)

        def visit_AsyncWith(self, node: ast.AsyncWith) -> None:  # noqa: N802
            if any(
                _with_context_builder_name(item.context_expr) == "BuildPart"
                for item in node.items
            ):
                return
            self.generic_visit(node)

        def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
            operation_name = _call_subtractive_without_host_operation_name(node)
            if operation_name is not None and not self._host_materialized:
                line_no = int(getattr(node, "lineno", 0) or 0)
                if line_no not in self._seen_lines:
                    self._seen_lines.add(line_no)
                    self._hits.append(
                        {
                            "line_no": line_no,
                            "builder_alias": self._builder_alias,
                            "operation_name": operation_name,
                        }
                    )
            if _call_materializes_additive_host(node):
                self._host_materialized = True
            self.generic_visit(node)

    hits: list[dict[str, Any]] = []
    seen: set[tuple[str, int]] = set()
    for node in ast.walk(tree):
        builder_alias = _buildpart_with_alias(node)
        if not builder_alias:
            continue
        visitor = _Visitor(builder_alias=builder_alias)
        for statement in list(getattr(node, "body", [])):
            visitor.visit(statement)
        for hit in visitor.hits:
            line_no = int(hit.get("line_no") or 0)
            cache_key = (builder_alias, line_no)
            if cache_key in seen:
                continue
            seen.add(cache_key)
            hits.append(hit)
    return hits


def _find_clamshell_unrotated_default_hinge_cylinder_hits(
    tree: ast.AST,
    *,
    code_for_lint: str,
) -> list[dict[str, Any]]:
    if not isinstance(tree, ast.Module):
        return []

    def _expr_text(node: ast.AST | None) -> str:
        if node is None:
            return ""
        try:
            return ast.unparse(node).replace(" ", "").lower()
        except Exception:  # pragma: no cover - defensive fallback
            return ""

    def _locations_target_back_hinge_seam(node: ast.With) -> bool:
        for item in node.items:
            context_expr = item.context_expr
            if not isinstance(context_expr, ast.Call):
                continue
            func = context_expr.func
            if not isinstance(func, ast.Name) or func.id != "Locations":
                continue
            for arg in context_expr.args:
                if not isinstance(arg, ast.Tuple) or len(arg.elts) < 2:
                    continue
                y_text = _expr_text(arg.elts[1])
                if "hinge_y" in y_text or "-depth/2" in y_text:
                    return True
        return False

    hits: list[dict[str, Any]] = []
    seen_lines: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.With) or not _locations_target_back_hinge_seam(node):
            continue
        for statement in node.body:
            statement_source = ast.get_source_segment(code_for_lint, statement) or ""
            normalized = statement_source.replace(" ", "")
            if "Cylinder(" not in statement_source:
                continue
            if any(token in statement_source for token in ("Rot(", ".rotate(", "rotation=")):
                continue
            if "mode=Mode.SUBTRACT" in normalized:
                continue
            line_no = int(getattr(statement, "lineno", 0) or 0)
            if line_no in seen_lines:
                continue
            seen_lines.add(line_no)
            hits.append({"line_no": line_no})
    return hits


def _extract_broad_shell_axis_selector_builder(node: ast.AST) -> str | None:
    if not (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "filter_by"
        and len(node.args) == 1
        and _ast_expr_text(node.args[0]) == "Axis.Z"
    ):
        return None
    edges_call = node.func.value
    if not (
        isinstance(edges_call, ast.Call)
        and isinstance(edges_call.func, ast.Attribute)
        and edges_call.func.attr == "edges"
    ):
        return None
    builder_expr = edges_call.func.value
    builder_label = _ast_expr_text(builder_expr).strip()
    return builder_label or "part"


def _extract_broad_edge_selector_builder(node: ast.AST) -> str | None:
    current = node
    saw_selector = False
    while (
        isinstance(current, ast.Call)
        and isinstance(current.func, ast.Attribute)
        and current.func.attr in {"filter_by", "filter_by_position"}
    ):
        saw_selector = True
        current = current.func.value
    if not saw_selector:
        return None
    if not (
        isinstance(current, ast.Call)
        and isinstance(current.func, ast.Attribute)
        and current.func.attr == "edges"
    ):
        return None
    builder_expr = current.func.value
    builder_label = _ast_expr_text(builder_expr).strip()
    return builder_label or "part"


def _find_broad_shell_axis_fillet_hits(tree: ast.AST) -> list[dict[str, Any]]:
    if not isinstance(tree, ast.Module):
        return []
    selector_bindings: dict[str, str] = {}
    for node in ast.walk(tree):
        target_name: str | None = None
        value: ast.AST | None = None
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
        ):
            target_name = node.targets[0].id
            value = node.value
        elif (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.value is not None
        ):
            target_name = node.target.id
            value = node.value
        if not target_name or value is None:
            continue
        builder_label = _extract_broad_shell_axis_selector_builder(value)
        if builder_label:
            selector_bindings[target_name] = builder_label

    hits: list[dict[str, Any]] = []
    seen_lines: set[int] = set()
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Call)
            and _ast_name_matches(node.func, "fillet")
            and node.args
        ):
            continue
        first_arg = node.args[0]
        builder_label: str | None = None
        if isinstance(first_arg, ast.Name):
            builder_label = selector_bindings.get(first_arg.id)
        if builder_label is None:
            builder_label = _extract_broad_shell_axis_selector_builder(first_arg)
        if builder_label is None:
            continue
        line_no = int(getattr(node, "lineno", 0) or 0)
        if line_no in seen_lines:
            continue
        seen_lines.add(line_no)
        hits.append({"line_no": line_no, "builder_label": builder_label})
    return hits


def _find_broad_local_finish_tail_fillet_hits(tree: ast.AST) -> list[dict[str, Any]]:
    if not isinstance(tree, ast.Module):
        return []
    selector_bindings: dict[str, str] = {}
    for node in ast.walk(tree):
        target_name: str | None = None
        value: ast.AST | None = None
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
        ):
            target_name = node.targets[0].id
            value = node.value
        elif (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.value is not None
        ):
            target_name = node.target.id
            value = node.value
        if not target_name or value is None:
            continue
        builder_label = _extract_broad_edge_selector_builder(value)
        if builder_label:
            selector_bindings[target_name] = builder_label

    hits: list[dict[str, Any]] = []
    seen_lines: set[int] = set()
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Call)
            and _ast_name_matches(node.func, "fillet")
            and node.args
        ):
            continue
        first_arg = node.args[0]
        builder_label: str | None = None
        if isinstance(first_arg, ast.Name):
            builder_label = selector_bindings.get(first_arg.id)
        if builder_label is None:
            builder_label = _extract_broad_edge_selector_builder(first_arg)
        if builder_label is None:
            continue
        line_no = int(getattr(node, "lineno", 0) or 0)
        if line_no in seen_lines:
            continue
        seen_lines.add(line_no)
        hits.append({"line_no": line_no, "builder_label": builder_label})
    return hits


def _find_builder_method_reference_assignment_hits(tree: ast.AST) -> list[dict[str, Any]]:
    if not isinstance(tree, ast.Module):
        return []
    builder_aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.With, ast.AsyncWith)):
            continue
        for item in node.items:
            builder_name = _with_context_builder_name(item.context_expr)
            if builder_name not in {"BuildLine", "BuildSketch"}:
                continue
            optional_vars = getattr(item, "optional_vars", None)
            if isinstance(optional_vars, ast.Name) and optional_vars.id:
                builder_aliases[optional_vars.id] = builder_name
    if not builder_aliases:
        return []
    hits: list[dict[str, Any]] = []
    seen: set[tuple[int, str]] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        value = node.value
        if not (
            isinstance(value, ast.Attribute)
            and isinstance(value.value, ast.Name)
            and value.value.id in builder_aliases
            and value.attr in {"wire", "face"}
        ):
            continue
        line_no = int(getattr(value, "lineno", 0) or getattr(node, "lineno", 0) or 0)
        cache_key = (line_no, value.attr)
        if cache_key in seen:
            continue
        seen.add(cache_key)
        hits.append(
            {
                "line_no": line_no,
                "builder_alias": value.value.id,
                "builder_name": builder_aliases[value.value.id],
                "method_name": value.attr,
            }
        )
    return hits


def _find_active_buildpart_temporary_primitive_arithmetic_hits(
    tree: ast.AST,
) -> list[dict[str, Any]]:
    if not isinstance(tree, ast.Module):
        return []
    hits: list[dict[str, Any]] = []
    seen: set[tuple[str, tuple[str, ...], int]] = set()
    for node in ast.walk(tree):
        host_alias = _buildpart_with_alias(node)
        if not host_alias:
            continue
        outer_body = ast.Module(body=list(getattr(node, "body", [])), type_ignores=[])
        primitive_assignments: dict[str, dict[str, Any]] = {}
        for child in ast.walk(outer_body):
            if not isinstance(child, ast.Assign) or len(child.targets) != 1:
                continue
            target = child.targets[0]
            if not isinstance(target, ast.Name):
                continue
            primitive_name = _primitive_constructor_name(child.value)
            if primitive_name is None:
                continue
            primitive_assignments[target.id] = {
                "primitive_name": primitive_name,
                "line_no": int(getattr(child, "lineno", 0) or 0),
            }
        if not primitive_assignments:
            continue
        for child in ast.walk(outer_body):
            arithmetic_expr, line_no = _temporary_primitive_arithmetic_expr(child)
            if arithmetic_expr is None:
                continue
            referenced_vars = tuple(
                sorted(
                    variable_name
                    for variable_name in primitive_assignments
                    if _expression_references_name(arithmetic_expr, variable_name)
                )
            )
            if not referenced_vars:
                continue
            key = (host_alias, referenced_vars, line_no)
            if key in seen:
                continue
            seen.add(key)
            hits.append(
                {
                    "host_alias": host_alias,
                    "primitive_vars": list(referenced_vars),
                    "line_no": line_no,
                }
            )
    return hits


def _find_compound_positional_children_contract_hits(
    tree: ast.AST,
) -> list[dict[str, Any]]:
    if not isinstance(tree, ast.Module):
        return []
    hits: list[dict[str, Any]] = []
    seen_lines: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not _ast_name_matches(node.func, "Compound"):
            continue
        if len(node.args) <= 1:
            continue
        line_no = int(getattr(node, "lineno", 0) or 0)
        if line_no in seen_lines:
            continue
        seen_lines.add(line_no)
        hits.append(
            {
                "line_no": line_no,
                "arg_count": len(node.args),
            }
        )
    return hits


def _find_active_buildpart_host_part_mutation_hits(
    tree: ast.AST,
) -> list[dict[str, Any]]:
    if not isinstance(tree, ast.Module):
        return []
    hits: list[dict[str, Any]] = []
    seen: set[tuple[str, tuple[str, ...], int]] = set()
    for node in ast.walk(tree):
        host_alias = _buildpart_with_alias(node)
        if not host_alias:
            continue
        outer_body = ast.Module(body=list(getattr(node, "body", [])), type_ignores=[])
        primitive_assignments: dict[str, dict[str, Any]] = {}
        for child in ast.walk(outer_body):
            if not isinstance(child, ast.Assign) or len(child.targets) != 1:
                continue
            target = child.targets[0]
            if not isinstance(target, ast.Name):
                continue
            primitive_name = _primitive_constructor_name(child.value)
            if primitive_name is None:
                continue
            primitive_assignments[target.id] = {
                "primitive_name": primitive_name,
                "line_no": int(getattr(child, "lineno", 0) or 0),
            }
        if not primitive_assignments:
            continue
        for child in ast.walk(outer_body):
            assignment = _host_part_arithmetic_assignment(node=child, host_alias=host_alias)
            if assignment is None:
                continue
            value_expr, line_no = assignment
            referenced_vars = tuple(
                sorted(
                    variable_name
                    for variable_name in primitive_assignments
                    if _expression_references_name(value_expr, variable_name)
                )
            )
            if not referenced_vars:
                continue
            key = (host_alias, referenced_vars, line_no)
            if key in seen:
                continue
            seen.add(key)
            hits.append(
                {
                    "host_alias": host_alias,
                    "primitive_vars": list(referenced_vars),
                    "line_no": line_no,
                }
            )
    return hits


def _find_active_buildpart_temporary_primitive_transform_hits(
    tree: ast.AST,
) -> list[dict[str, Any]]:
    if not isinstance(tree, ast.Module):
        return []
    hits: list[dict[str, Any]] = []
    seen: set[tuple[str, tuple[str, ...], tuple[str, ...], int]] = set()
    for node in ast.walk(tree):
        host_alias = _buildpart_with_alias(node)
        if not host_alias:
            continue
        outer_body = ast.Module(body=list(getattr(node, "body", [])), type_ignores=[])
        primitive_assignments: dict[str, dict[str, Any]] = {}
        for child in ast.walk(outer_body):
            if not isinstance(child, ast.Assign) or len(child.targets) != 1:
                continue
            target = child.targets[0]
            if not isinstance(target, ast.Name):
                continue
            primitive_name = _primitive_constructor_name(child.value)
            if primitive_name is None:
                continue
            primitive_assignments[target.id] = {
                "primitive_name": primitive_name,
                "line_no": int(getattr(child, "lineno", 0) or 0),
            }
        if not primitive_assignments:
            continue
        for child in ast.walk(outer_body):
            transform_expr, line_no, transform_kinds = _temporary_primitive_transform_expr(child)
            if transform_expr is None:
                continue
            referenced_vars = tuple(
                sorted(
                    variable_name
                    for variable_name in primitive_assignments
                    if _expression_references_name(transform_expr, variable_name)
                )
            )
            if (
                not referenced_vars
                and isinstance(child, ast.Assign)
                and len(child.targets) == 1
                and isinstance(child.targets[0], ast.Name)
                and child.targets[0].id in primitive_assignments
            ):
                referenced_vars = (child.targets[0].id,)
            if not referenced_vars:
                continue
            key = (host_alias, referenced_vars, tuple(sorted(transform_kinds)), line_no)
            if key in seen:
                continue
            seen.add(key)
            hits.append(
                {
                    "host_alias": host_alias,
                    "primitive_vars": list(referenced_vars),
                    "transform_kinds": sorted(transform_kinds),
                    "line_no": line_no,
                }
            )
    return hits


def _find_explicit_anchor_manual_cutter_missing_subtract_hits(
    tree: ast.AST,
) -> list[dict[str, Any]]:
    if not isinstance(tree, ast.Module):
        return []

    class _Visitor(ast.NodeVisitor):
        _primitive_names = {"Cone", "Cylinder"}

        def __init__(self) -> None:
            self._context_stack: list[str] = []
            self._feature_locations_stack: list[bool] = []
            self._hits: list[dict[str, Any]] = []
            self._seen: set[tuple[str, int]] = set()

        @property
        def hits(self) -> list[dict[str, Any]]:
            return self._hits

        def visit_With(self, node: ast.With) -> None:  # noqa: N802
            self._visit_with_like(node.items, node.body)

        def visit_AsyncWith(self, node: ast.AsyncWith) -> None:  # noqa: N802
            self._visit_with_like(node.items, node.body)

        def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
            primitive_name = next(
                (
                    name
                    for name in self._primitive_names
                    if _ast_name_matches(node.func, name)
                ),
                None,
            )
            if (
                primitive_name is not None
                and "BuildPart" in self._context_stack
                and any(self._feature_locations_stack)
                and not _call_uses_mode_subtract(node)
                and not _call_uses_mode_private(node)
            ):
                line_no = int(getattr(node, "lineno", 0) or 0)
                cache_key = (primitive_name, line_no)
                if cache_key not in self._seen:
                    self._seen.add(cache_key)
                    self._hits.append(
                        {"line_no": line_no, "primitive_name": primitive_name}
                    )
            self.generic_visit(node)

        def _visit_with_like(
            self,
            items: list[ast.withitem],
            body: list[ast.stmt],
        ) -> None:
            pushed_contexts = 0
            pushed_feature_locations = 0
            try:
                for item in items:
                    context_expr = item.context_expr
                    builder_name = _with_context_builder_name(context_expr)
                    if builder_name is not None:
                        self._context_stack.append(builder_name)
                        pushed_contexts += 1
                        continue
                    if _with_context_is_locations(context_expr):
                        self._feature_locations_stack.append(
                            _locations_context_suggests_local_feature_placement(
                                context_expr
                            )
                        )
                        pushed_feature_locations += 1
                for statement in body:
                    self.visit(statement)
            finally:
                for _ in range(pushed_contexts):
                    self._context_stack.pop()
                for _ in range(pushed_feature_locations):
                    if self._feature_locations_stack:
                        self._feature_locations_stack.pop()

    visitor = _Visitor()
    visitor.visit(tree)
    return visitor.hits


def _find_display_only_helper_hits(tree: ast.AST) -> list[dict[str, Any]]:
    hits: list[dict[str, Any]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module_name = str(node.module or "").strip().lower()
            if module_name == "ocp_vscode":
                imported_names = [
                    alias.name
                    for alias in node.names
                    if isinstance(alias.name, str) and alias.name.strip()
                ]
                hits.append(
                    {
                        "line_no": int(getattr(node, "lineno", 0) or 0),
                        "helper_label": (
                            f"from ocp_vscode import {', '.join(imported_names)}"
                            if imported_names
                            else "from ocp_vscode import ..."
                        ),
                    }
                )
        elif isinstance(node, ast.Import):
            for alias in node.names:
                module_name = str(alias.name or "").strip().lower()
                if module_name == "ocp_vscode":
                    hits.append(
                        {
                            "line_no": int(getattr(node, "lineno", 0) or 0),
                            "helper_label": "import ocp_vscode",
                        }
                    )
        elif isinstance(node, ast.Call):
            callee = None
            if isinstance(node.func, ast.Name):
                callee = str(node.func.id or "").strip()
            elif isinstance(node.func, ast.Attribute):
                callee = str(node.func.attr or "").strip()
            if callee in {"show", "show_object"}:
                hits.append(
                    {
                        "line_no": int(getattr(node, "lineno", 0) or 0),
                        "helper_label": f"{callee}(...)",
                    }
                )
    return hits


def _find_case_drift_local_symbol_hits(tree: ast.AST) -> list[dict[str, Any]]:
    if not isinstance(tree, ast.Module):
        return []
    bound_names = _collect_module_local_binding_names(tree)
    if not bound_names:
        return []
    known_build123d_symbols = (
        _build123d_exported_symbol_names()
        if _module_imports_build123d_symbols(tree)
        else set()
    )
    lower_to_names: dict[str, set[str]] = {}
    for name in bound_names:
        lowered = name.lower()
        if not lowered:
            continue
        lower_to_names.setdefault(lowered, set()).add(name)

    class _Visitor(ast.NodeVisitor):
        def __init__(self) -> None:
            self.hits: list[dict[str, Any]] = []
            self._seen: set[tuple[int, str, str]] = set()

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
            return None

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:  # noqa: N802
            return None

        def visit_ClassDef(self, node: ast.ClassDef) -> None:  # noqa: N802
            return None

        def visit_Lambda(self, node: ast.Lambda) -> None:  # noqa: N802
            return None

        def visit_Name(self, node: ast.Name) -> None:  # noqa: N802
            if not isinstance(node.ctx, ast.Load):
                return
            name = str(node.id or "").strip()
            if not name or name in bound_names or name in known_build123d_symbols:
                return
            candidates = sorted(
                candidate
                for candidate in lower_to_names.get(name.lower(), set())
                if candidate != name
            )
            if not candidates:
                return
            suggested_name = candidates[0]
            cache_key = (int(getattr(node, "lineno", 0) or 0), name, suggested_name)
            if cache_key in self._seen:
                return
            self._seen.add(cache_key)
            self.hits.append(
                {
                    "line_no": int(getattr(node, "lineno", 0) or 0),
                    "undefined_name": name,
                    "suggested_name": suggested_name,
                }
            )

    visitor = _Visitor()
    visitor.visit(tree)
    return visitor.hits


def _find_buildpart_topology_access_inside_buildsketch_hits(
    tree: ast.AST,
) -> list[dict[str, Any]]:
    if not isinstance(tree, ast.Module):
        return []

    class _Visitor(ast.NodeVisitor):
        def __init__(self) -> None:
            self._buildpart_alias_stack: list[str] = []
            self._buildsketch_depth = 0
            self._hits: list[dict[str, Any]] = []
            self._seen: set[tuple[str, str, int]] = set()

        @property
        def hits(self) -> list[dict[str, Any]]:
            return self._hits

        def visit_With(self, node: ast.With) -> None:  # noqa: N802
            self._visit_with_like(node.items, node.body)

        def visit_AsyncWith(self, node: ast.AsyncWith) -> None:  # noqa: N802
            self._visit_with_like(node.items, node.body)

        def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
            if (
                self._buildsketch_depth > 0
                and self._buildpart_alias_stack
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in {"vertices", "edges", "faces"}
                and isinstance(node.func.value, ast.Name)
            ):
                builder_alias = str(node.func.value.id)
                if builder_alias in self._buildpart_alias_stack:
                    line_no = int(getattr(node, "lineno", 0) or 0)
                    cache_key = (builder_alias, str(node.func.attr), line_no)
                    if cache_key not in self._seen:
                        self._seen.add(cache_key)
                        self._hits.append(
                            {
                                "line_no": line_no,
                                "builder_alias": builder_alias,
                                "accessor": str(node.func.attr),
                            }
                        )
            self.generic_visit(node)

        def _visit_with_like(
            self,
            items: list[ast.withitem],
            body: list[ast.stmt],
        ) -> None:
            pushed_buildparts = 0
            pushed_buildsketches = 0
            try:
                for item in items:
                    builder_name = _with_context_builder_name(item.context_expr)
                    if builder_name == "BuildPart":
                        buildpart_alias = "__active_buildpart__"
                        if isinstance(item.optional_vars, ast.Name) and item.optional_vars.id:
                            buildpart_alias = str(item.optional_vars.id)
                        self._buildpart_alias_stack.append(buildpart_alias)
                        pushed_buildparts += 1
                    elif builder_name == "BuildSketch":
                        self._buildsketch_depth += 1
                        pushed_buildsketches += 1
                for statement in body:
                    self.visit(statement)
            finally:
                for _ in range(pushed_buildparts):
                    self._buildpart_alias_stack.pop()
                self._buildsketch_depth = max(
                    0, self._buildsketch_depth - pushed_buildsketches
                )

    visitor = _Visitor()
    visitor.visit(tree)
    return visitor.hits


def collect_builder_context_hits(
    *,
    parsed_tree: ast.AST,
    requirement_lower: str,
    code: str,
    candidate_family_id_set: set[str],
) -> list[dict[str, Any]]:
    hits: list[dict[str, Any]] = []
    for nested_hit in _find_nested_buildpart_part_arithmetic_hits(parsed_tree):
        line_no = int(nested_hit.get("line_no") or 0)
        hits.append(
            {
                "rule_id": "invalid_build123d_api.nested_buildpart_cutter_part_arithmetic",
                "message": (
                    "Do not open a nested BuildPart cutter inside an active BuildPart "
                    "and then mutate the host with `part.part -= cutter.part`; that "
                    "pattern does not reliably preserve the active placement context "
                    "for repeated subtractive features."
                ),
                "repair_hint": (
                    "Keep repeated cutters in the same active `BuildPart` with "
                    "`mode=Mode.SUBTRACT`, or close the host builder before doing an "
                    "explicit `result = host.part - cutter` boolean. "
                    + (
                        f"Repair the nested cutter arithmetic at line {line_no}."
                        if line_no > 0
                        else "Repair the nested cutter arithmetic."
                    )
                ),
            }
        )
    for transform_hit in _find_nested_buildpart_part_transform_hits(parsed_tree):
        line_no = int(transform_hit.get("line_no") or 0)
        nested_alias = str(transform_hit.get("nested_alias") or "cutter").strip() or "cutter"
        method_name = (
            str(transform_hit.get("method_name") or "move").strip() or "move"
        )
        hits.append(
            {
                "rule_id": "invalid_build123d_api.nested_buildpart_part_transform",
                "message": (
                    "Do not treat a nested BuildPart alias like `"
                    f"{nested_alias}.part.{method_name}(...)` as a detached solid transform "
                    "surface while the outer host BuildPart is still active."
                ),
                "repair_hint": (
                    "If the nested builder is only a cutter or secondary local feature, keep "
                    "that geometry builder-native inside the active host with explicit "
                    "`Locations(...)` / `mode=Mode.SUBTRACT`. Otherwise close the host builder "
                    "first, then transform the detached solid outside it before one explicit "
                    "boolean. "
                    + (
                        f"Repair the nested BuildPart part transform at line {line_no}."
                        if line_no > 0
                        else "Repair the nested BuildPart part transform."
                    )
                ),
            }
        )
    for display_hit in _find_display_only_helper_hits(parsed_tree):
        line_no = int(display_hit.get("line_no") or 0)
        helper_label = str(display_hit.get("helper_label") or "display helper").strip()
        hits.append(
            {
                "rule_id": "invalid_build123d_runtime.display_only_helper_import",
                "message": (
                    "Sandbox execution should return geometry through `result` only; "
                    "display/debug helpers such as ocp_vscode imports or `show(...)` "
                    "calls are not available inside the runtime container."
                ),
                "repair_hint": (
                    f"Remove the display/debug helper usage ({helper_label})"
                    + (f" at line {line_no}" if line_no > 0 else "")
                    + ", keep the Build123d geometry construction only, and expose the "
                    "final part/compound via `result = ...`."
                ),
            }
        )
    for identifier_hit in _find_case_drift_local_symbol_hits(parsed_tree):
        line_no = int(identifier_hit.get("line_no") or 0)
        undefined_name = str(
            identifier_hit.get("undefined_name") or "local_symbol"
        ).strip()
        suggested_name = str(
            identifier_hit.get("suggested_name") or "local_symbol"
        ).strip()
        hits.append(
            {
                "rule_id": "invalid_build123d_identifier.case_drift_local_symbol",
                "message": (
                    f"`{undefined_name}` is not defined in this execute_build123d snippet, "
                    f"but `{suggested_name}` is already bound with different casing."
                ),
                "repair_hint": (
                    f"Rename `{undefined_name}` to the existing local symbol "
                    f"`{suggested_name}`, or define `{undefined_name}` explicitly before "
                    "it is used."
                    + (
                        f" Repair the local identifier casing at line {line_no}."
                        if line_no > 0
                        else ""
                    )
                ),
            }
        )
    for topology_hit in _find_buildpart_topology_access_inside_buildsketch_hits(
        parsed_tree
    ):
        line_no = int(topology_hit.get("line_no") or 0)
        builder_alias = str(topology_hit.get("builder_alias") or "part").strip()
        accessor = str(topology_hit.get("accessor") or "topology accessor").strip()
        hits.append(
            {
                "rule_id": "invalid_build123d_context.buildpart_topology_access_inside_buildsketch",
                "message": (
                    "Do not query the enclosing `BuildPart` topology from inside "
                    "`BuildSketch`; the host solid may not exist yet, and sketch-time "
                    "shape edits should stay on sketch geometry instead of "
                    f"`{builder_alias}.{accessor}()`."
                ),
                "repair_hint": (
                    "Keep the `BuildSketch` self-contained: use sketch-native profile "
                    "construction for rounded corners, or extrude/revolve first and then "
                    "apply solid-edge edits on the finished part. "
                    + (
                        f"Repair the `{builder_alias}.{accessor}()` access at line {line_no}."
                        if line_no > 0
                        else f"Repair the `{builder_alias}.{accessor}()` access."
                    )
                ),
            }
        )
    for nested_subtractive_hit in _find_nested_subtractive_buildpart_hits(parsed_tree):
        line_no = int(nested_subtractive_hit.get("line_no") or 0)
        inside_locations = bool(nested_subtractive_hit.get("inside_locations"))
        hits.append(
            {
                "rule_id": "invalid_build123d_context.nested_subtractive_buildpart_inside_active_builder",
                "message": (
                    "Do not open a nested `BuildPart(mode=Mode.SUBTRACT)` inside an "
                    "active `BuildPart`; that pattern does not reliably preserve the "
                    "host placement/workplane context for repeated local subtractive features."
                    + (
                        " This is especially brittle when the nested subtractive builder sits "
                        "inside an outer `Locations(...)` placement."
                        if inside_locations
                        else ""
                    )
                ),
                "repair_hint": (
                    "Keep repeated subtractive features in the same active `BuildPart` with "
                    "direct builder-native subtractive calls, or close the host builder before "
                    "doing an explicit `result = host.part - cutter` boolean. "
                    + (
                        f"Repair the nested subtractive BuildPart at line {line_no}."
                        if line_no > 0
                        else "Repair the nested subtractive BuildPart."
                    )
                ),
            }
        )
    for detached_subtractive_hit in _find_detached_subtractive_builder_without_host_hits(
        parsed_tree
    ):
        line_no = int(detached_subtractive_hit.get("line_no") or 0)
        builder_alias = (
            str(detached_subtractive_hit.get("builder_alias") or "part").strip()
            or "part"
        )
        operation_name = (
            str(detached_subtractive_hit.get("operation_name") or "subtractive operation")
            .strip()
            or "subtractive operation"
        )
        hits.append(
            {
                "rule_id": "invalid_build123d_contract.detached_subtractive_builder_without_host",
                "message": (
                    "A detached `BuildPart` cannot start by removing material before any "
                    "additive host exists; the first materializing operation in that builder "
                    f"is subtractive (`{operation_name}`), so Build123d has nothing to subtract from."
                ),
                "repair_hint": (
                    "Do not open a standalone builder whose first real operation is "
                    "subtractive. If the cut belongs to an existing host, keep it inside the "
                    "authoritative host builder with explicit placement and "
                    "`mode=Mode.SUBTRACT`. If a detached cutter is required, build it as a "
                    "positive or private solid first and subtract it only after the host "
                    "builder closes. "
                    + (
                        f"Repair the subtract-without-host builder `{builder_alias}` at line {line_no}."
                        if line_no > 0
                        else f"Repair the subtract-without-host builder `{builder_alias}`."
                    )
                ),
            }
        )
    if (
        ("pin hinge" in requirement_lower or "mechanical hinge" in requirement_lower)
        and (
            "clamshell" in requirement_lower
            or ("lid" in requirement_lower and "base" in requirement_lower)
            or "top lid" in requirement_lower
            or "bottom base" in requirement_lower
        )
    ):
        for hinge_axis_hit in _find_clamshell_unrotated_default_hinge_cylinder_hits(
            parsed_tree,
            code_for_lint=code,
        ):
            line_no = int(hinge_axis_hit.get("line_no") or 0)
            hits.append(
                {
                    "rule_id": "invalid_build123d_contract.clamshell_hinge_unrotated_default_cylinder",
                    "message": (
                        "For a clamshell back-edge pin/mechanical hinge, dropping an "
                        "unrotated default `Cylinder(...)` onto the hinge seam leaves the "
                        "cylinder axis on Z instead of the requested hinge axis, usually X/width."
                    ),
                    "repair_hint": (
                        "Keep the seam Y coordinate literal, but choose a supported "
                        "orientation lane for the hinge cylinder. If the hinge geometry is "
                        "host-owned, build it with an axis-correct host-native pattern. If "
                        "detached hinge hardware is truly required, build the cylinder "
                        "positively first, close that builder, then orient the closed solid "
                        "with `Rot(...)` before assembly. "
                        + (
                            f"Repair the unrotated hinge cylinder at line {line_no}."
                            if line_no > 0
                            else "Repair the unrotated hinge cylinder."
                        )
                    ),
                }
            )
    for host_part_hit in _find_active_buildpart_host_part_mutation_hits(parsed_tree):
        line_no = int(host_part_hit.get("line_no") or 0)
        host_alias = str(host_part_hit.get("host_alias") or "part").strip() or "part"
        primitive_names = ", ".join(
            str(item) for item in (host_part_hit.get("primitive_vars") or [])
        )
        hits.append(
            {
                "rule_id": "invalid_build123d_contract.active_builder_part_mutation",
                "message": (
                    "Do not reassign or mutate `"
                    f"{host_alias}.part` while its `BuildPart` context is still active; "
                    "the builder output is not a detached staging solid until the host "
                    "builder closes."
                ),
                "repair_hint": (
                    "Keep additive/subtractive edits builder-native inside the active "
                    "`BuildPart` with `mode=Mode.ADD` / `mode=Mode.SUBTRACT` and explicit "
                    "`Locations(...)` placement. If detached boolean arithmetic is truly "
                    "required, close the host builder first and only then compute "
                    f"`result = {host_alias}.part +/- cutter`. "
                    + (
                        f"Repair the active builder part mutation at line {line_no}"
                        f" for {primitive_names}."
                        if line_no > 0 and primitive_names
                        else (
                            f"Repair the active builder part mutation at line {line_no}."
                            if line_no > 0
                            else "Repair the active builder part mutation."
                        )
                    )
                ),
            }
        )
    for temporary_hit in _find_active_buildpart_temporary_primitive_arithmetic_hits(
        parsed_tree
    ):
        line_no = int(temporary_hit.get("line_no") or 0)
        primitive_names = ", ".join(
            str(item) for item in (temporary_hit.get("primitive_vars") or [])
        )
        hits.append(
            {
                "rule_id": "invalid_build123d_contract.active_builder_temporary_primitive_arithmetic",
                "message": (
                    "Primitive constructors inside an active `BuildPart` mutate the "
                    "host immediately, so reusing those temporary solids in later "
                    "boolean/intersection arithmetic does not preserve an isolated "
                    "intermediate-solid contract."
                ),
                "repair_hint": (
                    "Keep the active builder authoritative: encode the shape with one "
                    "builder-native sketch/profile recipe, or close the host builder "
                    "before doing explicit solid arithmetic with temporary solids. "
                    + (
                        f"Repair the temporary solid arithmetic at line {line_no}"
                        f" for {primitive_names}."
                        if line_no > 0 and primitive_names
                        else (
                            f"Repair the temporary solid arithmetic at line {line_no}."
                            if line_no > 0
                            else "Repair the temporary solid arithmetic."
                        )
                    )
                ),
            }
        )
    for transform_hit in _find_active_buildpart_temporary_primitive_transform_hits(
        parsed_tree
    ):
        line_no = int(transform_hit.get("line_no") or 0)
        primitive_names = ", ".join(
            str(item) for item in (transform_hit.get("primitive_vars") or [])
        )
        transform_kinds = ", ".join(
            str(item) for item in (transform_hit.get("transform_kinds") or [])
        )
        hits.append(
            {
                "rule_id": "invalid_build123d_contract.active_builder_temporary_primitive_transform_rebind",
                "message": (
                    "A primitive created inside an active `BuildPart` is already added to "
                    "that host immediately, so rebinding it with `Pos(...) * solid`, "
                    "`Rot(...) * solid`, or similar transform multiplication does not move "
                    "the already-added host geometry."
                ),
                "repair_hint": (
                    "Inside the active builder, place translated features with "
                    "`Locations(...)` / explicit local frames, or close the host builder "
                    "before transforming a detached solid. Do not expect "
                    "`solid = Pos(...) * solid` or `solid = Rot(...) * solid` to relocate "
                    "geometry that was already added to the active host. "
                    + (
                        f"Repair the temporary primitive transform at line {line_no}"
                        f" for {primitive_names} via {transform_kinds}."
                        if line_no > 0 and primitive_names and transform_kinds
                        else (
                            f"Repair the temporary primitive transform at line {line_no}."
                            if line_no > 0
                            else "Repair the temporary primitive transform."
                        )
                    )
                ),
            }
        )
    for compound_hit in _find_compound_positional_children_contract_hits(parsed_tree):
        line_no = int(compound_hit.get("line_no") or 0)
        arg_count = int(compound_hit.get("arg_count") or 0)
        hits.append(
            {
                "rule_id": "invalid_build123d_contract.compound_positional_children_contract",
                "message": (
                    "Do not pass multiple detached parts/shapes to `Compound(...)` as "
                    "separate positional arguments; after the first positional `obj`, later "
                    "positional slots bind to `label`, `color`, or other metadata instead of "
                    "additional child shapes."
                ),
                "repair_hint": (
                    "Wrap detached solids in one iterable such as "
                    "`Compound([base_solid, lid_solid, hinge_solid])`, or use an explicit "
                    "`children=[...]` keyword payload. Do not write "
                    "`Compound(base_solid, lid_solid, hinge_solid)` expecting a variadic "
                    "assembly constructor. "
                    + (
                        f"Repair the Compound positional-child contract at line {line_no} "
                        f"with {arg_count} positional arguments."
                        if line_no > 0 and arg_count > 0
                        else (
                            f"Repair the Compound positional-child contract at line {line_no}."
                            if line_no > 0
                            else "Repair the Compound positional-child contract."
                        )
                    )
                ),
            }
        )
    if "explicit_anchor_hole" in candidate_family_id_set:
        for cutter_hit in _find_explicit_anchor_manual_cutter_missing_subtract_hits(
            parsed_tree
        ):
            line_no = int(cutter_hit.get("line_no") or 0)
            primitive_name = str(cutter_hit.get("primitive_name") or "primitive").strip()
            hits.append(
                {
                    "rule_id": "invalid_build123d_contract.explicit_anchor_manual_cutter_requires_subtract_mode",
                    "message": (
                        "Manual countersink / through-hole cutters inside an active "
                        "`BuildPart` placement must use `mode=Mode.SUBTRACT` (or stay "
                        "`mode=Mode.PRIVATE` for a later boolean), otherwise they add "
                        "material instead of cutting it."
                    ),
                    "repair_hint": (
                        "When realizing explicit hole arrays with manual "
                        f"`{primitive_name}(...)` cutters inside `Locations(...)`, add "
                        "`mode=Mode.SUBTRACT` on the cutter itself, or build the cutter "
                        "privately and subtract it after the host builder closes. "
                        + (
                            f"Repair the non-subtractive manual cutter at line {line_no}."
                            if line_no > 0
                            else "Repair the non-subtractive manual cutter."
                        )
                    ),
                }
            )
    return hits


__all__ = ["collect_builder_context_hits"]
