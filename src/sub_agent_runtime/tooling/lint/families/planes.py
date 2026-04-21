from __future__ import annotations

import ast
import re
from typing import Any, Iterable, Sequence

from sub_agent_runtime.tooling.lint.ast_utils import (
    _ast_dotted_name,
    _ast_expr_is_face_plane_constructor,
    _ast_expr_is_plane_like,
    _ast_expr_text,
    _ast_name_matches,
    _build_parent_map,
    _call_materializes_additive_host,
    _call_subtractive_without_host_operation_name,
    _collect_module_plane_binding_names,
    _looks_like_plane_expr,
    _looks_like_vector_tuple,
    _looks_like_xyz_coordinate_tuple,
    _with_context_builder_name,
    _with_context_is_locations,
)
from sub_agent_runtime.tooling.lint.families.countersinks import (
    _find_buildsketch_countersink_context_hits,
)


def _buildsketch_candidate_is_host_profile(
    node: ast.AST,
    *,
    parent_map: dict[ast.AST, ast.AST],
    buildpart_host_span_cache: dict[int, set[str]],
) -> bool:
    if not (isinstance(node, ast.Call) and _ast_name_matches(node.func, "BuildSketch")):
        return False
    sketch_with = _enclosing_with_for_context_call(node, parent_map=parent_map)
    if sketch_with is None:
        return False
    buildpart_with = _enclosing_buildpart_with(sketch_with, parent_map=parent_map)
    if buildpart_with is None:
        return False
    cache_key = id(buildpart_with)
    host_span_ids = buildpart_host_span_cache.get(cache_key)
    if host_span_ids is None:
        host_span_ids = _collect_buildpart_host_span_ids(buildpart_with)
        buildpart_host_span_cache[cache_key] = host_span_ids
    if len(host_span_ids) < 2:
        return False
    host_profile_alias_ids = _collect_buildpart_host_profile_alias_ids(
        buildpart_with,
        seed_ids=host_span_ids,
    )
    sketch_ids = _strip_host_profile_modifier_ids(
        _collect_sketch_size_identifier_names(sketch_with.body)
    )
    host_profile_ids = host_span_ids | host_profile_alias_ids
    return len(sketch_ids) >= 2 and sketch_ids.issubset(host_profile_ids)


def _buildsketch_candidate_is_inert_placeholder_builder(
    node: ast.AST,
    *,
    parent_map: dict[ast.AST, ast.AST],
) -> bool:
    if not (isinstance(node, ast.Call) and _ast_name_matches(node.func, "BuildSketch")):
        return False
    sketch_with = _enclosing_with_for_context_call(node, parent_map=parent_map)
    if sketch_with is None:
        return False
    buildpart_with = _enclosing_buildpart_with(sketch_with, parent_map=parent_map)
    if buildpart_with is None:
        return False
    return not _buildpart_contains_materializing_ops(buildpart_with.body)


_AXISYMMETRIC_SKETCH_PRIMITIVE_NAMES = {
    "Circle",
    "Ellipse",
}

_DETACHED_POSITIVE_BUILDER_SUBTRACTIVE_HELPERS = {
    "Hole",
    "CounterBoreHole",
    "CounterSinkHole",
}


def _buildsketch_candidate_is_detached_axisymmetric_positive_helper(
    node: ast.AST,
    *,
    parent_map: dict[ast.AST, ast.AST],
    buildpart_host_span_cache: dict[int, set[str]],
) -> bool:
    if not (isinstance(node, ast.Call) and _ast_name_matches(node.func, "BuildSketch")):
        return False
    sketch_with = _enclosing_with_for_context_call(node, parent_map=parent_map)
    if sketch_with is None:
        return False
    buildpart_with = _enclosing_buildpart_with(sketch_with, parent_map=parent_map)
    if buildpart_with is None:
        return False
    cache_key = id(buildpart_with)
    host_span_ids = buildpart_host_span_cache.get(cache_key)
    if host_span_ids is None:
        host_span_ids = _collect_buildpart_host_span_ids(buildpart_with)
        buildpart_host_span_cache[cache_key] = host_span_ids
    if len(host_span_ids) >= 2:
        return False
    primitive_names = _collect_sketch_primitive_helper_names(sketch_with.body)
    if not primitive_names or not primitive_names.issubset(
        _AXISYMMETRIC_SKETCH_PRIMITIVE_NAMES
    ):
        return False
    return not _buildpart_contains_subtractive_ops(buildpart_with.body)


_HOST_PROFILE_MODIFIER_IDS = {
    "wall",
    "thickness",
    "offset",
    "clearance",
    "gap",
    "radius",
    "corner_radius",
    "fillet",
    "draft",
    "lip",
    "shell",
}

_HOST_PROFILE_MODIFIER_TOKENS = _HOST_PROFILE_MODIFIER_IDS | {
    "thick",
}

_HOST_PROFILE_IGNORED_TOKENS = {
    "mm",
}


def _identifier_tokens(name: str) -> list[str]:
    normalized = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", name or "").lower()
    return [token for token in re.split(r"[^a-z0-9]+", normalized) if token]


def _is_host_profile_modifier_id(name: str | None) -> bool:
    cleaned = str(name or "").strip()
    if not cleaned:
        return False
    lowered = cleaned.lower()
    if lowered in _HOST_PROFILE_MODIFIER_IDS:
        return True
    tokens = [
        token
        for token in _identifier_tokens(cleaned)
        if token not in _HOST_PROFILE_IGNORED_TOKENS
    ]
    return bool(tokens) and all(token in _HOST_PROFILE_MODIFIER_TOKENS for token in tokens)


def _strip_host_profile_modifier_ids(names: set[str]) -> set[str]:
    return {name for name in names if not _is_host_profile_modifier_id(name)}


def _enclosing_with_for_context_call(
    node: ast.AST,
    *,
    parent_map: dict[ast.AST, ast.AST],
) -> ast.With | ast.AsyncWith | None:
    current = parent_map.get(node)
    while current is not None:
        if isinstance(current, ast.withitem):
            parent = parent_map.get(current)
            if isinstance(parent, (ast.With, ast.AsyncWith)):
                return parent
        current = parent_map.get(current)
    return None


def _enclosing_buildpart_with(
    node: ast.AST,
    *,
    parent_map: dict[ast.AST, ast.AST],
) -> ast.With | ast.AsyncWith | None:
    current = node
    while current is not None:
        if isinstance(current, (ast.With, ast.AsyncWith)) and any(
            _with_context_builder_name(item.context_expr) == "BuildPart"
            for item in current.items
        ):
            return current
        current = parent_map.get(current)
    return None


def _collect_buildpart_host_span_ids(node: ast.With | ast.AsyncWith) -> set[str]:
    for stmt in node.body:
        ids = _extract_host_span_ids_from_stmt(stmt)
        if len(ids) >= 2:
            return ids
    return set()


def _collect_sketch_size_identifier_names(body: list[ast.stmt]) -> set[str]:
    ids: set[str] = set()
    for stmt in body:
        for node in ast.walk(stmt):
            if not isinstance(node, ast.Call):
                continue
            if not any(
                _ast_name_matches(node.func, shape_name)
                for shape_name in (
                    "Rectangle",
                    "RectangleRounded",
                    "SlotOverall",
                    "SlotCenterToCenter",
                )
            ):
                continue
            ids |= _collect_identifier_names_from_exprs(node.args[:2])
    return ids


def _collect_sketch_primitive_helper_names(body: list[ast.stmt]) -> set[str]:
    names: set[str] = set()
    for stmt in body:
        for node in ast.walk(stmt):
            if not isinstance(node, ast.Call):
                continue
            for helper_name in _AXISYMMETRIC_SKETCH_PRIMITIVE_NAMES:
                if _ast_name_matches(node.func, helper_name):
                    names.add(helper_name)
    return names


def _buildpart_contains_subtractive_ops(body: list[ast.stmt]) -> bool:
    for stmt in body:
        for node in ast.walk(stmt):
            if not isinstance(node, ast.Call):
                continue
            if any(
                _ast_name_matches(node.func, helper)
                for helper in _DETACHED_POSITIVE_BUILDER_SUBTRACTIVE_HELPERS
            ):
                return True
            for keyword in node.keywords or []:
                if str(keyword.arg or "").strip() != "mode":
                    continue
                if _ast_dotted_name(keyword.value) == "Mode.SUBTRACT":
                    return True
    return False


def _buildpart_contains_materializing_ops(body: list[ast.stmt]) -> bool:
    for stmt in body:
        for node in ast.walk(stmt):
            if not isinstance(node, ast.Call):
                continue
            if _call_materializes_additive_host(node):
                return True
            if _call_subtractive_without_host_operation_name(node) is not None:
                return True
    return False


def _collect_buildpart_host_profile_alias_ids(
    node: ast.With | ast.AsyncWith,
    *,
    seed_ids: set[str],
) -> set[str]:
    known_ids = set(seed_ids)
    alias_ids: set[str] = set()
    changed = True
    while changed:
        changed = False
        for stmt in node.body:
            for target_name, value_ids in _iter_assignment_name_dependencies(stmt):
                if not target_name:
                    continue
                if target_name in known_ids or _is_host_profile_modifier_id(target_name):
                    continue
                cleaned_value_ids = _strip_host_profile_modifier_ids(
                    {item for item in value_ids if item}
                )
                if not cleaned_value_ids:
                    continue
                if not cleaned_value_ids.issubset(known_ids):
                    continue
                known_ids.add(target_name)
                alias_ids.add(target_name)
                changed = True
    return alias_ids


def _iter_assignment_name_dependencies(stmt: ast.stmt) -> Iterable[tuple[str, set[str]]]:
    for node in ast.walk(stmt):
        if isinstance(node, ast.Assign):
            value_ids = _collect_identifier_names_from_exprs([node.value])
            for target in node.targets:
                if isinstance(target, ast.Name):
                    target_name = str(target.id or "").strip()
                    if target_name:
                        yield target_name, value_ids
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            target_name = str(node.target.id or "").strip()
            if target_name:
                value_ids = (
                    _collect_identifier_names_from_exprs([node.value])
                    if node.value
                    else set()
                )
                yield target_name, value_ids


def _extract_host_span_ids_from_stmt(stmt: ast.stmt) -> set[str]:
    if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call) and _ast_name_matches(
        stmt.value.func, "Box"
    ):
        return _strip_host_profile_modifier_ids(
            _collect_identifier_names_from_exprs(stmt.value.args[:3])
        )
    if isinstance(stmt, (ast.With, ast.AsyncWith)):
        if any(
            _with_context_builder_name(item.context_expr) == "BuildSketch"
            for item in stmt.items
        ):
            return _strip_host_profile_modifier_ids(
                _collect_sketch_size_identifier_names(stmt.body)
            )
        if any(_with_context_is_locations(item.context_expr) for item in stmt.items):
            for inner_stmt in stmt.body:
                ids = _extract_host_span_ids_from_stmt(inner_stmt)
                if len(ids) >= 2:
                    return ids
    return set()


def _collect_identifier_names_from_exprs(exprs: Sequence[ast.AST]) -> set[str]:
    ids: set[str] = set()
    for expr in exprs:
        for node in ast.walk(expr):
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                name = str(node.id or "").strip()
                if name:
                    ids.add(name)
    return ids


def _is_plane_rotated_call(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "rotated"
        and _looks_like_plane_expr(node.func.value)
    )


def _is_buildsketch_with_plane_offset(node: ast.AST, *, plane_name: str) -> bool:
    if not isinstance(node, ast.Call) or not _ast_name_matches(node.func, "BuildSketch"):
        return False
    if not node.args:
        return False
    plane_expr = node.args[0]
    if not (
        isinstance(plane_expr, ast.Call)
        and isinstance(plane_expr.func, ast.Attribute)
        and plane_expr.func.attr == "offset"
    ):
        return False
    return _is_named_plane_expr(plane_expr.func.value, plane_name=plane_name)


def _is_named_plane_expr(node: ast.AST, *, plane_name: str) -> bool:
    return (
        isinstance(node, ast.Attribute)
        and node.attr == plane_name
        and isinstance(node.value, ast.Name)
        and node.value.id == "Plane"
    )


def _plane_offset_argument(node: ast.AST) -> ast.AST | None:
    if not (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "offset"
    ):
        return None
    if node.args:
        return node.args[0]
    for keyword in node.keywords:
        if str(getattr(keyword, "arg", "") or "").strip() in {"amount", "distance", "offset"}:
            return keyword.value
    return None


def _is_zero_literal(node: ast.AST) -> bool:
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return float(node.value) == 0.0
    if (
        isinstance(node, ast.UnaryOp)
        and isinstance(node.op, (ast.UAdd, ast.USub))
        and isinstance(node.operand, ast.Constant)
        and isinstance(node.operand.value, (int, float))
    ):
        value = float(node.operand.value)
        return (-value if isinstance(node.op, ast.USub) else value) == 0.0
    return False


def _extract_centered_box_span_exprs(node: ast.Call) -> dict[str, str]:
    if not (isinstance(node, ast.Call) and _ast_name_matches(node.func, "Box")):
        return {}
    positional = list(node.args[:3])
    keyword_map = {
        str(getattr(keyword, "arg", "") or "").strip(): keyword.value
        for keyword in node.keywords
        if str(getattr(keyword, "arg", "") or "").strip()
    }
    x_arg = positional[0] if len(positional) >= 1 else keyword_map.get("length")
    y_arg = positional[1] if len(positional) >= 2 else keyword_map.get("width")
    z_arg = positional[2] if len(positional) >= 3 else keyword_map.get("height")
    x_span = _ast_expr_text(x_arg) if x_arg is not None else ""
    y_span = _ast_expr_text(y_arg) if y_arg is not None else ""
    z_span = _ast_expr_text(z_arg) if z_arg is not None else ""
    if not (x_span and y_span and z_span):
        return {}
    return {"x_span": x_span, "y_span": y_span, "z_span": z_span}


def _named_face_requirement_plane_groups(requirement_lower: str) -> set[str]:
    lowered = str(requirement_lower or "").lower()
    groups: set[str] = set()
    if any(
        token in lowered
        for token in (
            "top face",
            "top-face",
            "bottom face",
            "bottom-face",
            "mating face",
            "mating faces",
            "mating surface",
            "mating surfaces",
        )
    ):
        groups.add("top_bottom")
    if any(token in lowered for token in ("front face", "front-face", "back face", "back-face")):
        groups.add("front_back")
    if any(token in lowered for token in ("left face", "left-face", "right face", "right-face")):
        groups.add("left_right")
    return groups


def _plane_name_for_named_face_group(group: str) -> str:
    return {
        "top_bottom": "XY",
        "front_back": "XZ",
        "left_right": "YZ",
    }.get(group, "")


def _collect_named_plane_aliases(tree: ast.AST) -> dict[str, str]:
    aliases: dict[str, str] = {}
    if not isinstance(tree, ast.Module):
        return aliases
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            plane_name = _named_plane_root_name(node.value, plane_aliases=aliases)
            if not plane_name:
                continue
            for target in node.targets:
                if isinstance(target, ast.Name):
                    aliases[str(target.id or "").strip()] = plane_name
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            plane_name = _named_plane_root_name(node.value, plane_aliases=aliases)
            if plane_name:
                aliases[str(node.target.id or "").strip()] = plane_name
    return aliases


def _named_plane_root_name(
    expr: ast.AST | None,
    *,
    plane_aliases: dict[str, str] | None = None,
) -> str | None:
    if expr is None:
        return None
    if isinstance(expr, ast.Name):
        key = str(expr.id or "").strip()
        if not key:
            return None
        return (plane_aliases or {}).get(key)
    if (
        isinstance(expr, ast.Attribute)
        and isinstance(expr.value, ast.Name)
        and expr.value.id == "Plane"
        and expr.attr in {"XY", "XZ", "YZ"}
    ):
        return str(expr.attr)
    if isinstance(expr, ast.Call) and isinstance(expr.func, ast.Attribute):
        method_name = str(expr.func.attr or "").strip()
        if method_name in {
            "offset",
            "move",
            "shift_origin",
            "rotated",
            "rotate",
            "moved",
            "located",
        }:
            return _named_plane_root_name(expr.func.value, plane_aliases=plane_aliases)
    return None


def _is_plain_named_plane_expr(
    expr: ast.AST | None,
    *,
    plane_aliases: dict[str, str] | None = None,
) -> bool:
    if expr is None:
        return False
    if isinstance(expr, ast.Name):
        key = str(expr.id or "").strip()
        return bool(key and key in (plane_aliases or {}))
    return (
        isinstance(expr, ast.Attribute)
        and isinstance(expr.value, ast.Name)
        and expr.value.id == "Plane"
        and expr.attr in {"XY", "XZ", "YZ"}
    )


def _directional_drill_workplane_with_in_plane_anchor(
    requirement_lower: str,
) -> str | None:
    if not requirement_lower:
        return None
    mentions_x = "x =" in requirement_lower or " at x" in requirement_lower
    mentions_y = "y =" in requirement_lower or " at y" in requirement_lower
    mentions_z = "z =" in requirement_lower or " at z" in requirement_lower
    if (
        any(
            phrase in requirement_lower
            for phrase in (
                "in the y direction",
                "along the y direction",
                "drill in the y direction",
                "drill through the lugs in the y direction",
            )
        )
        and mentions_x
        and mentions_z
    ):
        return "XZ"
    if (
        any(
            phrase in requirement_lower
            for phrase in (
                "in the x direction",
                "along the x direction",
                "drill in the x direction",
            )
        )
        and mentions_y
        and mentions_z
    ):
        return "YZ"
    return None



def _find_face_plane_shift_origin_global_coordinate_hits(
    tree: ast.AST,
) -> list[dict[str, Any]]:
    if not isinstance(tree, ast.Module):
        return []
    hits: list[dict[str, Any]] = []
    seen_lines: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (
            isinstance(func, ast.Attribute)
            and str(func.attr or "").strip() == "shift_origin"
            and _ast_expr_is_face_plane_constructor(func.value)
        ):
            continue
        locator_expr: ast.AST | None = node.args[0] if node.args else None
        if locator_expr is None:
            for keyword in node.keywords:
                if str(getattr(keyword, "arg", "") or "").strip() == "locator":
                    locator_expr = keyword.value
                    break
        if locator_expr is None or not _looks_like_xyz_coordinate_tuple(locator_expr):
            continue
        line_no = int(getattr(node, "lineno", 0) or 0)
        if line_no in seen_lines:
            continue
        seen_lines.add(line_no)
        hits.append({"line_no": line_no})
    return hits


def _find_plane_located_call_hits(tree: ast.AST) -> list[dict[str, Any]]:
    if not isinstance(tree, ast.Module):
        return []
    plane_binding_names = _collect_module_plane_binding_names(tree)
    hits: list[dict[str, Any]] = []
    seen_lines: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (
            isinstance(func, ast.Attribute)
            and str(func.attr or "").strip() == "located"
            and _ast_expr_is_plane_like(func.value, plane_binding_names)
        ):
            continue
        line_no = int(getattr(node, "lineno", 0) or 0)
        if line_no in seen_lines:
            continue
        seen_lines.add(line_no)
        hits.append({"line_no": line_no})
    return hits


def _find_plane_moved_call_hits(tree: ast.AST) -> list[dict[str, Any]]:
    if not isinstance(tree, ast.Module):
        return []
    plane_binding_names = _collect_module_plane_binding_names(tree)
    hits: list[dict[str, Any]] = []
    seen_lines: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (
            isinstance(func, ast.Attribute)
            and str(func.attr or "").strip() == "moved"
            and _ast_expr_is_plane_like(func.value, plane_binding_names)
        ):
            continue
        line_no = int(getattr(node, "lineno", 0) or 0)
        if line_no in seen_lines:
            continue
        seen_lines.add(line_no)
        hits.append({"line_no": line_no})
    return hits


def _find_plane_rotate_method_hits(tree: ast.AST) -> list[dict[str, Any]]:
    if not isinstance(tree, ast.Module):
        return []
    hits: list[dict[str, Any]] = []
    seen_lines: set[int] = set()
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "rotate"
            and _looks_like_plane_expr(node.func.value)
        ):
            continue
        line_no = int(getattr(node, "lineno", 0) or 0)
        if line_no in seen_lines:
            continue
        seen_lines.add(line_no)
        hits.append({"line_no": line_no})
    return hits


def _find_named_face_plane_family_mismatch_hits(
    tree: ast.AST,
    *,
    requirement_lower: str,
) -> list[dict[str, Any]]:
    if not isinstance(tree, ast.Module):
        return []
    mentioned_groups = _named_face_requirement_plane_groups(requirement_lower)
    if not mentioned_groups:
        return []
    allowed_planes = {_plane_name_for_named_face_group(group) for group in mentioned_groups}
    plane_aliases = _collect_named_plane_aliases(tree)
    parent_map = _build_parent_map(tree)
    buildpart_host_span_cache: dict[int, set[str]] = {}

    allowed_plane_seen = False
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Call)
            and (
                _ast_name_matches(node.func, "BuildSketch")
                or _ast_name_matches(node.func, "Locations")
            )
            and node.args
        ):
            continue
        plane_name = _named_plane_root_name(node.args[0], plane_aliases=plane_aliases)
        if plane_name in allowed_planes:
            allowed_plane_seen = True
            break

    hits: list[dict[str, Any]] = []
    seen_lines: set[int] = set()
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Call)
            and (
                _ast_name_matches(node.func, "BuildSketch")
                or _ast_name_matches(node.func, "Locations")
            )
            and node.args
        ):
            continue
        plane_name = _named_plane_root_name(node.args[0], plane_aliases=plane_aliases)
        if not plane_name or plane_name in allowed_planes:
            continue
        if allowed_plane_seen and _is_plain_named_plane_expr(
            node.args[0],
            plane_aliases=plane_aliases,
        ):
            continue
        if _buildsketch_candidate_is_host_profile(
            node,
            parent_map=parent_map,
            buildpart_host_span_cache=buildpart_host_span_cache,
        ):
            continue
        if _buildsketch_candidate_is_inert_placeholder_builder(
            node,
            parent_map=parent_map,
        ):
            continue
        if _buildsketch_candidate_is_detached_axisymmetric_positive_helper(
            node,
            parent_map=parent_map,
            buildpart_host_span_cache=buildpart_host_span_cache,
        ):
            continue
        line_no = int(getattr(node, "lineno", 0) or 0)
        if line_no in seen_lines:
            continue
        seen_lines.add(line_no)
        hits.append(
            {
                "line_no": line_no,
                "plane_name": plane_name,
                "expected_planes": sorted(allowed_planes),
            }
        )
    return hits


def _find_plane_rotated_origin_guess_hits(tree: ast.AST) -> list[dict[str, Any]]:
    if not isinstance(tree, ast.Module):
        return []
    hits: list[dict[str, Any]] = []
    seen: set[tuple[str, int]] = set()
    for node in ast.walk(tree):
        if not _is_plane_rotated_call(node):
            continue
        line_no = int(getattr(node, "lineno", 0) or 0)
        if len(node.args) >= 2 and _looks_like_vector_tuple(node.args[1]):
            key = ("tuple_ordering_guess", line_no)
            if key not in seen:
                seen.add(key)
                hits.append({"line_no": line_no})
        for keyword in node.keywords:
            if str(getattr(keyword, "arg", "") or "").strip() != "origin":
                continue
            key = ("origin_keyword_guess", line_no)
            if key in seen:
                continue
            seen.add(key)
            hits.append({"line_no": line_no})
    return hits


def _find_directional_drill_plane_offset_coordinate_hits(
    tree: ast.AST,
    *,
    requirement_lower: str,
) -> list[dict[str, Any]]:
    if not isinstance(tree, ast.Module):
        return []
    target_plane_name = _directional_drill_workplane_with_in_plane_anchor(requirement_lower)
    if target_plane_name is None:
        return []
    hits: list[dict[str, Any]] = []
    seen_lines: set[int] = set()
    for node in ast.walk(tree):
        if not _is_buildsketch_with_plane_offset(node, plane_name=target_plane_name):
            continue
        plane_expr = node.args[0]
        offset_arg = _plane_offset_argument(plane_expr)
        if offset_arg is None or _is_zero_literal(offset_arg):
            continue
        line_no = int(getattr(node, "lineno", 0) or 0)
        if line_no in seen_lines:
            continue
        seen_lines.add(line_no)
        hits.append({"line_no": line_no})
    return hits


def _find_centered_box_face_plane_offset_span_mismatch_hits(
    tree: ast.AST,
    *,
    requirement_lower: str,
) -> list[dict[str, Any]]:
    if not isinstance(tree, ast.Module):
        return []
    if not any(
        token in requirement_lower
        for token in (
            "top face",
            "bottom face",
            "front face",
            "back face",
            "side face",
            "left face",
            "right face",
        )
    ):
        return []

    centered_box_spans: list[dict[str, str]] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and _ast_name_matches(node.func, "Box")):
            continue
        if any(str(getattr(keyword, "arg", "") or "").strip() == "align" for keyword in node.keywords):
            continue
        spans = _extract_centered_box_span_exprs(node)
        if spans:
            centered_box_spans.append(spans)
    if not centered_box_spans:
        return []

    hits: list[dict[str, Any]] = []
    seen_lines: set[int] = set()
    plane_to_span_key = {"XY": "z_span", "XZ": "y_span", "YZ": "x_span"}
    for node in ast.walk(tree):
        offset_arg = _plane_offset_argument(node)
        if offset_arg is None:
            continue
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "offset"
            and isinstance(node.func.value, ast.Attribute)
            and isinstance(node.func.value.value, ast.Name)
            and node.func.value.value.id == "Plane"
        ):
            continue
        plane_name = str(node.func.value.attr or "").strip()
        span_key = plane_to_span_key.get(plane_name)
        if span_key is None:
            continue
        offset_text = _ast_expr_text(offset_arg)
        if not offset_text:
            continue
        for spans in centered_box_spans:
            span_expr = str(spans.get(span_key) or "").strip()
            if not span_expr or span_expr != offset_text:
                continue
            line_no = int(getattr(node, "lineno", 0) or 0)
            if line_no in seen_lines:
                continue
            seen_lines.add(line_no)
            hits.append(
                {
                    "line_no": line_no,
                    "plane_name": plane_name,
                    "span_expr": span_expr,
                }
            )
    return hits


def collect_plane_transform_hits(
    *,
    parsed_tree: ast.AST,
) -> list[dict[str, Any]]:
    hits: list[dict[str, Any]] = []
    for plane_located_hit in _find_plane_located_call_hits(parsed_tree):
        line_no = int(plane_located_hit.get("line_no") or 0)
        hits.append(
            {
                "rule_id": "invalid_build123d_api.plane_located_shape_method_guess",
                "message": (
                    "Build123d Plane objects do not support a shape-style `.located(...)` "
                    "method for repositioning workplanes."
                ),
                "repair_hint": (
                    "Translate or re-anchor the workplane with `Plane.offset(...)`, "
                    "`Plane.move(Location(...))`, or `Plane.shift_origin(...)`; only call "
                    "`.located(...)` on actual shapes when you need to reposition geometry. "
                    + (
                        f"Repair the Plane placement call at line {line_no}."
                        if line_no > 0
                        else "Repair the Plane placement call."
                    )
                ),
            }
        )
    for plane_moved_hit in _find_plane_moved_call_hits(parsed_tree):
        line_no = int(plane_moved_hit.get("line_no") or 0)
        hits.append(
            {
                "rule_id": "invalid_build123d_api.plane_moved_shape_method_guess",
                "message": (
                    "Build123d Plane objects do not support a shape-style `.moved(...)` "
                    "method for repositioning workplanes."
                ),
                "repair_hint": (
                    "Translate or re-anchor the workplane with `Plane.offset(...)`, "
                    "`Plane.move(Location(...))`, or `Plane.shift_origin(...)`; only call "
                    "`.moved(...)` on actual shapes when you need to reposition geometry. "
                    + (
                        f"Repair the Plane placement call at line {line_no}."
                        if line_no > 0
                        else "Repair the Plane placement call."
                    )
                ),
            }
        )
    for plane_rotate_hit in _find_plane_rotate_method_hits(parsed_tree):
        line_no = int(plane_rotate_hit.get("line_no") or 0)
        hits.append(
            {
                "rule_id": "invalid_build123d_api.plane_rotate_shape_method_guess",
                "message": (
                    "Build123d Plane objects do not support a shape-style `.rotate(...)` "
                    "method for orienting workplanes."
                ),
                "repair_hint": (
                    "Orient the workplane with `Plane.rotated((rx, ry, rz), ordering=...)` "
                    "or keep the named plane when it already matches the target normal; "
                    "use `Plane.offset(...)` only for plane-normal translation. "
                    + (
                        f"Repair the Plane rotation call at line {line_no}."
                        if line_no > 0
                        else "Repair the Plane rotation call."
                    )
                ),
            }
        )
    for shift_origin_hit in _find_face_plane_shift_origin_global_coordinate_hits(
        parsed_tree
    ):
        line_no = int(shift_origin_hit.get("line_no") or 0)
        hits.append(
            {
                "rule_id": "invalid_build123d_contract.face_plane_shift_origin_global_coordinate_guess",
                "message": (
                    "When a workplane is derived from `Plane(face_like_expr)`, passing a "
                    "raw global `(x, y, z)` tuple into `.shift_origin(...)` often chooses "
                    "a point that is not on the host face plane and will fail before any "
                    "geometry is created."
                ),
                "repair_hint": (
                    "Keep the face-derived workplane on the host plane, then place the "
                    "sketch/profile with local 2D coordinates inside `BuildSketch(...)`; "
                    "if you truly need a re-anchored face plane, rebuild it from the host "
                    "face origin/normal instead of guessing a world-space XYZ tuple. "
                    + (
                        f"Repair the face-plane shift_origin call at line {line_no}."
                        if line_no > 0
                        else "Repair the face-plane shift_origin call."
                    )
                ),
            }
        )
    return hits


def collect_plane_contract_hits(
    *,
    parsed_tree: ast.AST,
    requirement_lower: str,
) -> list[dict[str, Any]]:
    hits: list[dict[str, Any]] = []
    for rotated_hit in _find_plane_rotated_origin_guess_hits(parsed_tree):
        line_no = int(rotated_hit.get("line_no") or 0)
        hits.append(
            {
                "rule_id": "invalid_build123d_api.plane_rotated_origin_guess",
                "message": (
                    "`Plane.rotated(rotation, ordering=...)` only changes plane "
                    "orientation; the second positional argument is ordering, not "
                    "an origin tuple."
                ),
                "repair_hint": (
                    "Keep the named workplane unrotated when it already matches the "
                    "required normal, or move it with `offset(...)` / feature placement "
                    "instead of guessing an origin argument to `Plane.rotated(...)`. "
                    + (
                        f"Repair the plane rotation call at line {line_no}."
                        if line_no > 0
                        else "Repair the plane rotation call."
                    )
                ),
            }
        )
    for plane_offset_hit in _find_directional_drill_plane_offset_coordinate_hits(
        parsed_tree,
        requirement_lower=requirement_lower,
    ):
        line_no = int(plane_offset_hit.get("line_no") or 0)
        hits.append(
            {
                "rule_id": "invalid_build123d_contract.directional_drill_plane_offset_coordinate_mixup",
                "message": (
                    "For directional drilling on the XZ/YZ workplane, `Plane.offset(...)` "
                    "moves along the drill axis normal, not along the in-plane anchor "
                    "coordinate that the requirement usually gives."
                ),
                "repair_hint": (
                    "Keep the directional-drill workplane at the correct normal-axis "
                    "datum and place the named local coordinates inside that workplane "
                    "instead of encoding them with `Plane.offset(...)`. "
                    + (
                        f"Repair the workplane offset at line {line_no}."
                        if line_no > 0
                        else "Repair the workplane offset."
                    )
                ),
            }
        )
    for plane_family_hit in _find_named_face_plane_family_mismatch_hits(
        parsed_tree,
        requirement_lower=requirement_lower,
    ):
        line_no = int(plane_family_hit.get("line_no") or 0)
        plane_name = str(plane_family_hit.get("plane_name") or "").strip() or "unknown"
        expected_planes = ", ".join(
            str(item).strip() for item in plane_family_hit.get("expected_planes", []) if str(item).strip()
        ) or "XY/XZ/YZ"
        hits.append(
            {
                "rule_id": "invalid_build123d_contract.named_face_plane_family_mismatch",
                "message": (
                    "Named-face local edits must use the plane family whose normal matches the "
                    f"requested host face. `Plane.{plane_name}` does not match the named-face "
                    f"orientation implied here; expected plane family/families: {expected_planes}."
                ),
                "repair_hint": (
                    "Map named host faces to plane families by normal axis before sketching or "
                    "placing cutters: `top/bottom -> Plane.XY`, `front/back -> Plane.XZ`, "
                    "`left/right -> Plane.YZ`. If the host face has already been selected from "
                    "topology, prefer `Plane(face)` or an explicit `Plane(origin=..., z_dir=...)` "
                    "built from that face instead of guessing a mismatched named plane. "
                    + (
                        f"Repair the named-face workplane at line {line_no}."
                        if line_no > 0
                        else "Repair the named-face workplane."
                    )
                ),
            }
        )
    for plane_offset_hit in _find_centered_box_face_plane_offset_span_mismatch_hits(
        parsed_tree,
        requirement_lower=requirement_lower,
    ):
        line_no = int(plane_offset_hit.get("line_no") or 0)
        plane_name = str(plane_offset_hit.get("plane_name") or "").strip() or "XY"
        span_expr = str(plane_offset_hit.get("span_expr") or "").strip() or "dimension"
        hits.append(
            {
                "rule_id": "invalid_build123d_contract.centered_box_face_plane_full_span_offset",
                "message": (
                    "A default centered `Box(...)` spans equally about the origin, so "
                    f"`Plane.{plane_name}.offset({span_expr})` overshoots the actual host "
                    "face by using the full span instead of the half-span datum."
                ),
                "repair_hint": (
                    "When a feature belongs on a named face of a centered host, place the "
                    "workplane on the real face datum: use the half-span (`height/2`, "
                    "`width/2`, `length/2`) or bind to an actual face reference/topology "
                    "query instead of offsetting by the full box dimension. "
                    + (
                        f"Repair the centered-host face offset at line {line_no}."
                        if line_no > 0
                        else "Repair the centered-host face offset."
                    )
                ),
            }
        )
    for context_hit in _find_buildsketch_countersink_context_hits(parsed_tree):
        line_no = int(context_hit.get("line_no") or 0)
        hits.append(
            {
                "rule_id": "invalid_build123d_context.countersinkhole_requires_buildpart",
                "message": (
                    "`CounterSinkHole(...)` is a BuildPart operation, not a "
                    "BuildSketch entity. Calling it inside `BuildSketch` will fail "
                    "before any geometry is created."
                ),
                "repair_hint": (
                    "Move `CounterSinkHole(...)` back into the active `BuildPart`, "
                    "and place it on the target host-face plane with an explicit "
                    "face-local placement such as `Locations((x, y, top_z))`. "
                    + (
                        f"Repair the BuildSketch countersink misuse at line {line_no}."
                        if line_no > 0
                        else "Repair the BuildSketch countersink misuse."
                    )
                ),
            }
        )
    return hits


__all__ = ["collect_plane_contract_hits", "collect_plane_transform_hits"]
