from __future__ import annotations

import ast
import io
import tokenize
from typing import Any

from sub_agent_runtime.tooling.lint.ast_utils import (
    _ast_name_matches,
    _subscript_index_value,
    _with_context_builder_name,
)


def _find_center_arc_keyword_alias_hits(tree: ast.AST) -> list[dict[str, Any]]:
    if not isinstance(tree, ast.Module):
        return []
    hits: list[dict[str, Any]] = []
    seen: set[tuple[int, str]] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not _ast_name_matches(node.func, "CenterArc"):
            continue
        line_no = int(getattr(node, "lineno", 0) or 0)
        for keyword in node.keywords:
            alias_name = str(getattr(keyword, "arg", "") or "").strip()
            if alias_name not in {"arc_angle", "end_angle"}:
                continue
            cache_key = (line_no, alias_name)
            if cache_key in seen:
                continue
            seen.add(cache_key)
            hits.append({"line_no": line_no, "alias_name": alias_name})
    return hits


def _find_explicit_radius_arc_helper_hits(tree: ast.AST) -> list[dict[str, Any]]:
    if not isinstance(tree, ast.Module):
        return []
    hits: list[dict[str, Any]] = []
    seen: set[tuple[int, str]] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        helper_name = None
        if _ast_name_matches(node.func, "TangentArc"):
            helper_name = "TangentArc"
        elif _ast_name_matches(node.func, "JernArc"):
            helper_name = "JernArc"
        if helper_name is None:
            continue
        line_no = int(getattr(node, "lineno", 0) or 0)
        cache_key = (line_no, helper_name)
        if cache_key in seen:
            continue
        seen.add(cache_key)
        hits.append({"line_no": line_no, "helper_name": helper_name})
    return hits


def _find_sweep_path_method_reference_hits(tree: ast.AST) -> list[dict[str, Any]]:
    if not isinstance(tree, ast.Module):
        return []
    hits: list[dict[str, Any]] = []
    seen: set[tuple[int, str]] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not _ast_name_matches(node.func, "sweep"):
            continue
        candidate_nodes: list[ast.AST] = []
        if len(node.args) >= 2:
            candidate_nodes.append(node.args[1])
        for keyword in node.keywords:
            if str(getattr(keyword, "arg", "") or "").strip() == "path":
                candidate_nodes.append(keyword.value)
        for candidate in candidate_nodes:
            if not isinstance(candidate, ast.Attribute) or candidate.attr not in {"wire", "line"}:
                continue
            line_no = int(getattr(candidate, "lineno", 0) or getattr(node, "lineno", 0) or 0)
            cache_key = (line_no, str(candidate.attr))
            if cache_key in seen:
                continue
            seen.add(cache_key)
            hits.append({"line_no": line_no, "attribute_name": str(candidate.attr)})
    return hits


def _find_center_arc_missing_start_angle_hits(tree: ast.AST) -> list[dict[str, Any]]:
    if not isinstance(tree, ast.Module):
        return []
    hits: list[dict[str, Any]] = []
    seen: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not _ast_name_matches(node.func, "CenterArc"):
            continue
        keyword_names = {
            str(getattr(keyword, "arg", "") or "").strip()
            for keyword in node.keywords
            if str(getattr(keyword, "arg", "") or "").strip()
        }
        if "start_angle" in keyword_names:
            continue
        if len(node.args) >= 3:
            continue
        line_no = int(getattr(node, "lineno", 0) or 0)
        if line_no in seen:
            continue
        seen.add(line_no)
        hits.append({"line_no": line_no})
    return hits


def _find_symbolic_degree_constant_hits(code: str) -> list[dict[str, Any]]:
    hits: list[dict[str, Any]] = []
    seen: set[tuple[int, str]] = set()
    try:
        token_stream = tokenize.generate_tokens(io.StringIO(code).readline)
    except (tokenize.TokenError, IndentationError):
        return hits

    pending_name: tokenize.TokenInfo | None = None
    last_significant: tokenize.TokenInfo | None = None
    for token in token_stream:
        if token.type in {
            tokenize.NL,
            tokenize.NEWLINE,
            tokenize.INDENT,
            tokenize.DEDENT,
            tokenize.COMMENT,
            tokenize.ENDMARKER,
        }:
            continue
        if token.type == tokenize.NAME and token.string in {"DEGREE", "DEGREES"}:
            line_no = int(token.start[0] or 0)
            cache_key = (line_no, token.string)
            if last_significant is not None and last_significant.string in {"*", "/"}:
                if cache_key not in seen:
                    seen.add(cache_key)
                    hits.append({"line_no": line_no, "symbol_name": token.string})
                pending_name = None
            else:
                pending_name = token
            last_significant = token
            continue
        if pending_name is not None and token.string in {"*", "/"}:
            line_no = int(pending_name.start[0] or 0)
            cache_key = (line_no, pending_name.string)
            if cache_key not in seen:
                seen.add(cache_key)
                hits.append({"line_no": line_no, "symbol_name": pending_name.string})
        pending_name = None
        last_significant = token
    return hits


def _requirement_prefers_center_arc_for_explicit_radius_path(requirement_lower: str) -> bool:
    lowered = str(requirement_lower or "").strip().lower()
    return (
        "sweep" in lowered
        and any(token in lowered for token in ("path", "rail"))
        and "arc" in lowered
        and "radius" in lowered
        and any(token in lowered for token in ("tangent arc", "90-degree", "90 degree"))
    )


def _find_sweep_section_keyword_alias_hits(tree: ast.AST) -> list[dict[str, Any]]:
    if not isinstance(tree, ast.Module):
        return []
    hits: list[dict[str, Any]] = []
    seen: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not _ast_name_matches(node.func, "sweep"):
            continue
        for keyword in node.keywords:
            if str(getattr(keyword, "arg", "") or "").strip() != "section":
                continue
            line_no = int(getattr(keyword, "lineno", 0) or getattr(node, "lineno", 0) or 0)
            if line_no in seen:
                continue
            seen.add(line_no)
            hits.append({"line_no": line_no})
    return hits


def _find_solid_sweep_invalid_keyword_hits(tree: ast.AST) -> list[dict[str, Any]]:
    if not isinstance(tree, ast.Module):
        return []
    hits: list[dict[str, Any]] = []
    seen: set[tuple[int, str]] = set()
    allowed_keywords = {
        "section",
        "path",
        "inner_wires",
        "make_solid",
        "is_frenet",
        "mode",
        "transition",
    }
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not (
            isinstance(node.func, ast.Attribute)
            and node.func.attr == "sweep"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "Solid"
        ):
            continue
        line_no = int(getattr(node, "lineno", 0) or 0)
        for keyword in node.keywords:
            alias_name = str(getattr(keyword, "arg", "") or "").strip()
            if not alias_name or alias_name in allowed_keywords:
                continue
            cache_key = (line_no, alias_name)
            if cache_key in seen:
                continue
            seen.add(cache_key)
            hits.append({"line_no": line_no, "alias_name": alias_name})
    return hits


def _find_sweep_profile_face_method_reference_hits(tree: ast.AST) -> list[dict[str, Any]]:
    if not isinstance(tree, ast.Module):
        return []
    hits: list[dict[str, Any]] = []
    seen: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not _ast_name_matches(node.func, "sweep"):
            continue
        candidate_nodes: list[ast.AST] = []
        if node.args:
            candidate_nodes.append(node.args[0])
        for keyword in node.keywords:
            if str(getattr(keyword, "arg", "") or "").strip() in {"sections", "section"}:
                candidate_nodes.append(keyword.value)
        for candidate in candidate_nodes:
            if not isinstance(candidate, ast.Attribute) or candidate.attr != "face":
                continue
            line_no = int(getattr(candidate, "lineno", 0) or getattr(node, "lineno", 0) or 0)
            if line_no in seen:
                continue
            seen.add(line_no)
            hits.append({"line_no": line_no})
    return hits


def _find_buildsketch_curve_context_hits(tree: ast.AST) -> list[dict[str, Any]]:
    if not isinstance(tree, ast.Module):
        return []

    class _Visitor(ast.NodeVisitor):
        _curve_helper_names = {"Polyline", "Line", "CenterArc", "RadiusArc"}

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
                    for name in self._curve_helper_names
                    if _ast_name_matches(node.func, name)
                ),
                None,
            )
            if (
                helper_name is not None
                and "BuildSketch" in self._context_stack
                and "BuildLine" not in self._context_stack
            ):
                line_no = int(getattr(node, "lineno", 0) or 0)
                cache_key = (line_no, helper_name)
                if cache_key not in self._seen:
                    self._seen.add(cache_key)
                    self._hits.append({"line_no": line_no, "helper_name": helper_name})
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


def _find_buildsketch_wire_profile_missing_make_face_hits(tree: ast.AST) -> list[dict[str, Any]]:
    if not isinstance(tree, ast.Module):
        return []
    hits: list[dict[str, Any]] = []
    seen_lines: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.With):
            continue
        if not any(
            _with_context_builder_name(item.context_expr) == "BuildSketch" for item in node.items
        ):
            continue
        body_module = ast.Module(body=node.body, type_ignores=[])
        has_make_face = any(
            isinstance(child, ast.Call) and _ast_name_matches(child.func, "make_face")
            for child in ast.walk(body_module)
        )
        if has_make_face:
            continue
        has_nested_buildline = any(
            isinstance(child, ast.With)
            and any(
                _with_context_builder_name(item.context_expr) == "BuildLine"
                for item in child.items
            )
            for child in ast.walk(body_module)
        )
        if not has_nested_buildline:
            continue
        line_no = int(getattr(node, "lineno", 0) or 0)
        if line_no in seen_lines:
            continue
        seen_lines.add(line_no)
        hits.append({"line_no": line_no})
    return hits


def _find_circle_make_face_trim_profile_hits(tree: ast.AST) -> list[dict[str, Any]]:
    if not isinstance(tree, ast.Module):
        return []
    hits: list[dict[str, Any]] = []
    seen_lines: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.With):
            continue
        if not any(
            _with_context_builder_name(item.context_expr) == "BuildSketch" for item in node.items
        ):
            continue
        body_module = ast.Module(body=node.body, type_ignores=[])
        has_circle = any(
            isinstance(child, ast.Call) and _ast_name_matches(child.func, "Circle")
            for child in ast.walk(body_module)
        )
        has_make_face = any(
            isinstance(child, ast.Call) and _ast_name_matches(child.func, "make_face")
            for child in ast.walk(body_module)
        )
        has_nested_buildline = any(
            isinstance(child, ast.With)
            and any(
                _with_context_builder_name(item.context_expr) == "BuildLine"
                for item in child.items
            )
            for child in ast.walk(body_module)
        )
        has_line = any(
            isinstance(child, ast.Call) and _ast_name_matches(child.func, "Line")
            for child in ast.walk(body_module)
        )
        if not (has_circle and has_make_face and has_nested_buildline and has_line):
            continue
        line_no = int(getattr(node, "lineno", 0) or 0)
        if line_no in seen_lines:
            continue
        seen_lines.add(line_no)
        hits.append({"line_no": line_no})
    return hits


def _buildsketch_aliases_with_subtractive_entities(tree: ast.AST) -> set[str]:
    if not isinstance(tree, ast.Module):
        return set()
    aliases: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.With):
            continue
        for item in node.items:
            if not (
                isinstance(item.context_expr, ast.Call)
                and _ast_name_matches(item.context_expr.func, "BuildSketch")
            ):
                continue
            if not isinstance(item.optional_vars, ast.Name):
                continue
            builder_alias = str(item.optional_vars.id)
            for child in ast.walk(ast.Module(body=node.body, type_ignores=[])):
                if not isinstance(child, ast.Call):
                    continue
                for keyword in child.keywords:
                    if str(getattr(keyword, "arg", "") or "").strip() != "mode":
                        continue
                    if (
                        isinstance(keyword.value, ast.Attribute)
                        and keyword.value.attr == "SUBTRACT"
                        and _ast_name_matches(keyword.value.value, "Mode")
                    ):
                        aliases.add(builder_alias)
                        break
                if builder_alias in aliases:
                    break
    return aliases


def _expr_anchors_to_builder_faces(expr: ast.AST, *, builder_alias: str) -> bool:
    if isinstance(expr, ast.Call):
        func = expr.func
        if (
            isinstance(func, ast.Attribute)
            and func.attr == "faces"
            and isinstance(func.value, ast.Name)
            and func.value.id == builder_alias
        ):
            return True
        if isinstance(func, ast.Attribute):
            return _expr_anchors_to_builder_faces(func.value, builder_alias=builder_alias)
    if isinstance(expr, ast.Attribute):
        return _expr_anchors_to_builder_faces(expr.value, builder_alias=builder_alias)
    return False


def _find_annular_profile_face_splitting_hits(tree: ast.AST) -> list[dict[str, Any]]:
    if not isinstance(tree, ast.Module):
        return []
    subtractive_aliases = _buildsketch_aliases_with_subtractive_entities(tree)
    if not subtractive_aliases:
        return []
    hits: list[dict[str, Any]] = []
    seen: set[tuple[str, int]] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Subscript):
            continue
        index_value = _subscript_index_value(node)
        if index_value is None or index_value < 1:
            continue
        line_no = int(getattr(node, "lineno", 0) or 0)
        for builder_alias in subtractive_aliases:
            if not _expr_anchors_to_builder_faces(node.value, builder_alias=builder_alias):
                continue
            cache_key = (builder_alias, line_no)
            if cache_key in seen:
                continue
            seen.add(cache_key)
            hits.append({"line_no": line_no, "builder_alias": builder_alias})
    return hits


def _find_annular_profile_face_extraction_sweep_hits(tree: ast.AST) -> list[dict[str, Any]]:
    if not isinstance(tree, ast.Module):
        return []
    subtractive_aliases = _buildsketch_aliases_with_subtractive_entities(tree)
    if not subtractive_aliases:
        return []

    extracted_face_aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        value = node.value
        if not (
            isinstance(value, ast.Call)
            and isinstance(value.func, ast.Attribute)
            and value.func.attr == "face"
            and isinstance(value.func.value, ast.Name)
            and value.func.value.id in subtractive_aliases
        ):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id:
                extracted_face_aliases[target.id] = value.func.value.id

    hits: list[dict[str, Any]] = []
    seen: set[tuple[str, int]] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not _ast_name_matches(node.func, "sweep"):
            continue
        candidate_nodes: list[ast.AST] = []
        if node.args:
            candidate_nodes.append(node.args[0])
        for keyword in node.keywords:
            if str(getattr(keyword, "arg", "") or "").strip() in {"section", "sections"}:
                candidate_nodes.append(keyword.value)
        for candidate in candidate_nodes:
            builder_alias = None
            if isinstance(candidate, ast.Name):
                builder_alias = extracted_face_aliases.get(candidate.id)
            elif (
                isinstance(candidate, ast.Call)
                and isinstance(candidate.func, ast.Attribute)
                and candidate.func.attr == "face"
                and isinstance(candidate.func.value, ast.Name)
                and candidate.func.value.id in subtractive_aliases
            ):
                builder_alias = candidate.func.value.id
            if not builder_alias:
                continue
            line_no = int(getattr(candidate, "lineno", 0) or getattr(node, "lineno", 0) or 0)
            cache_key = (builder_alias, line_no)
            if cache_key in seen:
                continue
            seen.add(cache_key)
            hits.append({"line_no": line_no, "builder_alias": builder_alias})
    return hits


def collect_path_profile_contract_hits(
    parsed_tree: ast.AST,
    *,
    code_for_lint: str,
    requirement_lower: str,
) -> list[dict[str, Any]]:
    hits: list[dict[str, Any]] = []
    for alias_hit in _find_center_arc_keyword_alias_hits(parsed_tree):
        line_no = int(alias_hit.get("line_no") or 0)
        alias_name = str(alias_hit.get("alias_name") or "").strip()
        if alias_name == "end_angle":
            rule_id = "invalid_build123d_keyword.center_arc_end_angle_alias"
            message = (
                "`CenterArc(...)` uses `arc_size=...` for the sweep span, not "
                "`end_angle=...`."
            )
            repair_hint = (
                "Keep `start_angle=...` for the start direction and replace "
                "`end_angle=` with `arc_size=` when calling `CenterArc(...)`."
            )
        else:
            rule_id = "invalid_build123d_keyword.center_arc_arc_angle_alias"
            message = "`CenterArc(...)` uses `arc_size=...`, not `arc_angle=...`."
            repair_hint = "Rename `arc_angle=` to `arc_size=` when calling `CenterArc(...)`."
        hits.append(
            {
                "rule_id": rule_id,
                "message": message,
                "repair_hint": repair_hint
                + (
                    f" Repair the CenterArc keyword at line {line_no}."
                    if line_no > 0
                    else ""
                ),
            }
        )
    for missing_hit in _find_center_arc_missing_start_angle_hits(parsed_tree):
        line_no = int(missing_hit.get("line_no") or 0)
        hits.append(
            {
                "rule_id": "invalid_build123d_contract.center_arc_missing_start_angle",
                "message": (
                    "`CenterArc(...)` requires an explicit `start_angle` before the arc "
                    "span. Omitting it leaves the arc under-specified."
                ),
                "repair_hint": (
                    "Provide `start_angle=...` (or the third positional argument) and keep "
                    "`arc_size=` for the sweep span when calling `CenterArc(...)`."
                    + (
                        f" Repair the CenterArc call at line {line_no}."
                        if line_no > 0
                        else ""
                    )
                ),
            }
        )
    for symbolic_hit in _find_symbolic_degree_constant_hits(code_for_lint):
        line_no = int(symbolic_hit.get("line_no") or 0)
        symbol_name = str(symbolic_hit.get("symbol_name") or "DEGREES").strip()
        hits.append(
            {
                "rule_id": "invalid_build123d_api.symbolic_degree_constant",
                "message": (
                    "Build123d angle parameters already take plain degree-valued floats; "
                    f"`{symbol_name}` is not a supported symbolic angle constant."
                ),
                "repair_hint": (
                    "Pass literal degree numbers such as `start_angle=-90` and "
                    "`arc_size=90` directly instead of multiplying by "
                    f"`{symbol_name}`."
                    + (
                        f" Repair the symbolic angle constant at line {line_no}."
                        if line_no > 0
                        else ""
                    )
                ),
            }
        )
    if _requirement_prefers_center_arc_for_explicit_radius_path(requirement_lower):
        for helper_hit in _find_explicit_radius_arc_helper_hits(parsed_tree):
            line_no = int(helper_hit.get("line_no") or 0)
            helper_name = str(helper_hit.get("helper_name") or "arc helper").strip()
            hits.append(
                {
                    "rule_id": "invalid_build123d_contract.explicit_radius_arc_prefers_center_arc",
                    "message": (
                        "For a path-sweep rail with an explicit tangent-arc radius, "
                        f"`{helper_name}(...)` is a higher-risk construction lane than an "
                        "explicit `CenterArc(...)` definition and often fails after the "
                        "model guesses the elbow endpoint or tangent."
                    ),
                    "repair_hint": (
                        "Prefer `CenterArc(center=..., radius=..., start_angle=..., arc_size=...)` "
                        "for the explicit-radius elbow, and connect the downstream line from `arc @ 1`."
                        + (
                            f" Repair the arc helper at line {line_no}."
                            if line_no > 0
                            else ""
                        )
                    ),
                }
            )
    for method_hit in _find_sweep_path_method_reference_hits(parsed_tree):
        line_no = int(method_hit.get("line_no") or 0)
        attribute_name = str(method_hit.get("attribute_name") or "").strip()
        if attribute_name == "line":
            hits.append(
                {
                    "rule_id": "invalid_build123d_contract.sweep_path_line_alias",
                    "message": (
                        "`BuildLine.line` exposes only one curve member and can silently drop "
                        "the full multi-segment rail that a path sweep requires."
                    ),
                    "repair_hint": (
                        "Pass `path.wire()` or another real connected `Wire`/`Edge` rail into "
                        "`sweep(...)` instead of `path.line`."
                        + (
                            f" Repair the sweep path object at line {line_no}."
                            if line_no > 0
                            else ""
                        )
                    ),
                }
            )
        else:
            hits.append(
                {
                    "rule_id": "invalid_build123d_contract.sweep_path_wire_method_reference",
                    "message": (
                        "`BuildLine.wire` is a method. `sweep(..., path=path.wire)` passes "
                        "a bound method object instead of the path wire itself."
                    ),
                    "repair_hint": (
                        "Call `path.wire()` when passing the captured path into `sweep(...)`, "
                        "or pass another real `Wire`/`Edge` object as the path."
                        + (
                            f" Repair the sweep path object at line {line_no}."
                            if line_no > 0
                            else ""
                        )
                    ),
                }
            )
    for alias_hit in _find_sweep_section_keyword_alias_hits(parsed_tree):
        line_no = int(alias_hit.get("line_no") or 0)
        hits.append(
            {
                "rule_id": "invalid_build123d_keyword.sweep_section_alias",
                "message": (
                    "`sweep(...)` uses `sections=` (plural) or a positional first "
                    "argument, not `section=`."
                ),
                "repair_hint": (
                    "Pass the profile as the first positional argument to `sweep(...)`, "
                    "or rename `section=` to `sections=`."
                    + (
                        f" Repair the sweep profile keyword at line {line_no}."
                        if line_no > 0
                        else ""
                    )
                ),
            }
        )
    for keyword_hit in _find_solid_sweep_invalid_keyword_hits(parsed_tree):
        line_no = int(keyword_hit.get("line_no") or 0)
        alias_name = str(keyword_hit.get("alias_name") or "").strip()
        hits.append(
            {
                "rule_id": "invalid_build123d_keyword.solid_sweep_unsupported_keyword",
                "message": (
                    "`Solid.sweep(...)` only accepts the verified Build123d keywords "
                    "`section`, `path`, `inner_wires`, `make_solid`, `is_frenet`, "
                    "`mode`, and `transition`; "
                    f"`{alias_name}=` is not part of that contract."
                ),
                "repair_hint": (
                    "Repair the call to use the real `Solid.sweep(...)` signature, or "
                    "prefer `sweep(profile.sketch, path=path_wire)` when the section is "
                    "one annular sketch with inner wires."
                    + (
                        f" Repair the Solid.sweep keyword at line {line_no}."
                        if line_no > 0
                        else ""
                    )
                ),
            }
        )
    for method_hit in _find_sweep_profile_face_method_reference_hits(parsed_tree):
        line_no = int(method_hit.get("line_no") or 0)
        hits.append(
            {
                "rule_id": "invalid_build123d_contract.sweep_profile_face_method_reference",
                "message": (
                    "`BuildSketch.face` is a method. Passing `profile.face` into "
                    "`sweep(...)` uses a bound method object instead of the actual profile face."
                ),
                "repair_hint": (
                    "Call `profile.face()` when extracting a face from the builder, or "
                    "pass `profile.sketch` / another real face object into `sweep(...)`."
                    + (
                        f" Repair the sweep profile object at line {line_no}."
                        if line_no > 0
                        else ""
                    )
                ),
            }
        )
    return hits


__all__ = [name for name in globals() if not name.startswith("__")]
