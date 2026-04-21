from __future__ import annotations

import ast
from typing import Any

from sub_agent_runtime.tooling.lint.ast_utils import (
    _ast_expr_text,
    _ast_name_matches,
)


def _find_regular_polygon_keyword_alias_hits(tree: ast.AST) -> list[dict[str, Any]]:
    if not isinstance(tree, ast.Module):
        return []
    hits: list[dict[str, Any]] = []
    seen: set[tuple[int, str]] = set()
    alias_names = {"sides", "n_sides", "num_sides", "regular_sides"}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not _ast_name_matches(node.func, "RegularPolygon"):
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


def _find_slot_center_point_radius_alias_hits(tree: ast.AST) -> list[dict[str, Any]]:
    if not isinstance(tree, ast.Module):
        return []
    hits: list[dict[str, Any]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not _ast_name_matches(node.func, "SlotCenterPoint"):
            continue
        for keyword in node.keywords:
            if str(keyword.arg or "").strip() != "radius":
                continue
            hits.append(
                {
                    "line_no": int(getattr(keyword, "lineno", getattr(node, "lineno", 0)) or 0),
                }
            )
            break
    return hits


def _find_slot_center_point_center_alias_hits(tree: ast.AST) -> list[dict[str, Any]]:
    if not isinstance(tree, ast.Module):
        return []
    hits: list[dict[str, Any]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not _ast_name_matches(node.func, "SlotCenterPoint"):
            continue
        for keyword in node.keywords:
            if str(keyword.arg or "").strip() != "center_point":
                continue
            hits.append(
                {
                    "line_no": int(getattr(keyword, "lineno", getattr(node, "lineno", 0)) or 0),
                }
            )
            break
    return hits


def _find_slot_center_to_center_keyword_alias_hits(tree: ast.AST) -> list[dict[str, Any]]:
    if not isinstance(tree, ast.Module):
        return []
    hits: list[dict[str, Any]] = []
    seen: set[tuple[int, str]] = set()
    alias_names = {"center_to_center", "width"}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not _ast_name_matches(node.func, "SlotCenterToCenter"):
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


def _find_plane_keyword_alias_hits(tree: ast.AST) -> list[dict[str, Any]]:
    if not isinstance(tree, ast.Module):
        return []
    hits: list[dict[str, Any]] = []
    seen: set[tuple[int, str]] = set()
    alias_names = {"normal"}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not _ast_name_matches(node.func, "Plane"):
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


def _find_cone_keyword_alias_hits(tree: ast.AST) -> list[dict[str, Any]]:
    if not isinstance(tree, ast.Module):
        return []
    hits: list[dict[str, Any]] = []
    seen: set[tuple[int, str]] = set()
    alias_names = {"upper_radius", "lower_radius"}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not _ast_name_matches(node.func, "Cone"):
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


def _find_filter_by_position_keyword_band_hits(tree: ast.AST) -> list[dict[str, Any]]:
    if not isinstance(tree, ast.Module):
        return []
    hits: list[dict[str, Any]] = []
    seen: set[tuple[int, str]] = set()
    alias_names = {
        "XMin",
        "XMax",
        "YMin",
        "YMax",
        "ZMin",
        "ZMax",
        "x_min",
        "x_max",
        "y_min",
        "y_max",
        "z_min",
        "z_max",
        "min_x",
        "max_x",
        "min_y",
        "max_y",
        "min_z",
        "max_z",
    }
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "filter_by_position"
        ):
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


def _find_filter_by_position_plane_axis_hits(tree: ast.AST) -> list[dict[str, Any]]:
    if not isinstance(tree, ast.Module):
        return []
    hits: list[dict[str, Any]] = []
    seen: set[tuple[int, str]] = set()
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "filter_by_position"
            and node.args
        ):
            continue
        first_arg = node.args[0]
        if not (
            isinstance(first_arg, ast.Attribute)
            and isinstance(first_arg.value, ast.Name)
            and first_arg.value.id == "Plane"
        ):
            continue
        plane_name = str(first_arg.attr or "").strip()
        line_no = int(getattr(node, "lineno", 0) or 0)
        cache_key = (line_no, plane_name)
        if cache_key in seen:
            continue
        seen.add(cache_key)
        hits.append({"line_no": line_no, "plane_name": plane_name})
    return hits


def _find_lowercase_vector_component_attribute_hits(
    tree: ast.AST,
) -> list[dict[str, Any]]:
    if not isinstance(tree, ast.Module):
        return []
    hits: list[dict[str, Any]] = []
    seen: set[tuple[int, str]] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Attribute) or node.attr not in {"x", "y", "z"}:
            continue
        value = node.value
        if not (
            isinstance(value, ast.Call)
            and isinstance(value.func, ast.Attribute)
            and value.func.attr == "center"
        ):
            continue
        line_no = int(getattr(node, "lineno", 0) or 0)
        cache_key = (line_no, node.attr)
        if cache_key in seen:
            continue
        seen.add(cache_key)
        hits.append({"line_no": line_no, "attr_name": node.attr})
    return hits


def collect_keyword_contract_hits(parsed_tree: ast.AST) -> list[dict[str, Any]]:
    hits: list[dict[str, Any]] = []
    for alias_hit in _find_regular_polygon_keyword_alias_hits(parsed_tree):
        line_no = int(alias_hit.get("line_no") or 0)
        alias_name = str(alias_hit.get("alias_name") or "").strip()
        hits.append(
            {
                "rule_id": "invalid_build123d_keyword.regular_polygon_sides_alias",
                "message": (
                    "`RegularPolygon(...)` uses `side_count=...`, not "
                    f"`{alias_name}=`."
                ),
                "repair_hint": (
                    "Rename the keyword to `side_count=` when calling "
                    "`RegularPolygon(...)`."
                    + (
                        f" Repair the regular-polygon side-count keyword at line {line_no}."
                        if line_no > 0
                        else ""
                    )
                ),
            }
        )
    for alias_hit in _find_plane_keyword_alias_hits(parsed_tree):
        line_no = int(alias_hit.get("line_no") or 0)
        alias_name = str(alias_hit.get("alias_name") or "").strip()
        hits.append(
            {
                "rule_id": "invalid_build123d_keyword.plane_normal_alias",
                "message": (
                    "`Plane(...)` uses `z_dir=...` for its normal direction, not "
                    f"`{alias_name}=`."
                ),
                "repair_hint": (
                    "Construct the plane with `Plane(origin=..., z_dir=...)`, and "
                    "only add `x_dir=` when you need to pin the in-plane rotation."
                    + (
                        f" Repair the plane normal keyword at line {line_no}."
                        if line_no > 0
                        else ""
                    )
                ),
            }
        )
    for alias_hit in _find_filter_by_position_keyword_band_hits(parsed_tree):
        line_no = int(alias_hit.get("line_no") or 0)
        alias_name = str(alias_hit.get("alias_name") or "").strip()
        hits.append(
            {
                "rule_id": "invalid_build123d_api.filter_by_position_keyword_band",
                "message": (
                    "`ShapeList.filter_by_position(...)` uses positional `minimum, maximum` "
                    f"arguments (plus optional `inclusive=`), not axis-band alias keywords such as `{alias_name}=`."
                ),
                "repair_hint": (
                    "Keep the axis as the first argument and pass the numeric band as "
                    "positional `minimum, maximum`, for example "
                    "`edges.filter_by_position(Axis.Z, z_min, z_max)`."
                    + (
                        f" Repair the position-band keyword at line {line_no}."
                        if line_no > 0
                        else ""
                    )
                ),
            }
        )
    for plane_hit in _find_filter_by_position_plane_axis_hits(parsed_tree):
        line_no = int(plane_hit.get("line_no") or 0)
        plane_name = str(plane_hit.get("plane_name") or "").strip()
        hits.append(
            {
                "rule_id": "invalid_build123d_api.filter_by_position_plane_axis",
                "message": (
                    "`ShapeList.filter_by_position(...)` expects an `Axis.*` selector as "
                    f"its first argument, not `Plane.{plane_name}`."
                ),
                "repair_hint": (
                    "Replace the first argument with the matching axis band such as "
                    "`Axis.Z`, and keep the numeric band as `minimum, maximum`, for "
                    "example `edges.filter_by_position(Axis.Z, z_min, z_max)`. When "
                    "the edit depends on a named host face or rim subset, prefer "
                    "`query_topology` over guessing a Plane-based band filter."
                    + (
                        f" Repair the Plane-based position filter at line {line_no}."
                        if line_no > 0
                        else ""
                    )
                ),
            }
        )
    for alias_hit in _find_cone_keyword_alias_hits(parsed_tree):
        line_no = int(alias_hit.get("line_no") or 0)
        alias_name = str(alias_hit.get("alias_name") or "").strip()
        expected_keyword = "top_radius" if alias_name == "upper_radius" else "bottom_radius"
        hits.append(
            {
                "rule_id": "invalid_build123d_keyword.cone_radius_alias",
                "message": (
                    "`Cone(...)` uses `bottom_radius=` and `top_radius=...`, not "
                    f"`{alias_name}=`."
                ),
                "repair_hint": (
                    f"Rename `{alias_name}=` to `{expected_keyword}=` when calling `Cone(...)`."
                    + (
                        f" Repair the cone radius keyword at line {line_no}."
                        if line_no > 0
                        else ""
                    )
                ),
            }
        )
    for alias_hit in _find_slot_center_point_radius_alias_hits(parsed_tree):
        line_no = int(alias_hit.get("line_no") or 0)
        hits.append(
            {
                "rule_id": "invalid_build123d_keyword.slot_center_point_radius_alias",
                "message": (
                    "SlotCenterPoint(...) does not accept `radius=...`; Build123d uses "
                    "`center=..., point=..., height=...` plus optional `rotation=`."
                ),
                "repair_hint": (
                    "Keep `SlotCenterPoint(center=..., point=..., height=...)` and express the "
                    "slot span with the center/point pair. If the slot orientation needs control, "
                    "use `rotation=` instead of inventing a rounded-slot `radius=` keyword."
                    + (
                        f" Repair the SlotCenterPoint keyword at line {line_no}."
                        if line_no > 0
                        else ""
                    )
                ),
            }
        )
    for alias_hit in _find_slot_center_point_center_alias_hits(parsed_tree):
        line_no = int(alias_hit.get("line_no") or 0)
        hits.append(
            {
                "rule_id": "invalid_build123d_keyword.slot_center_point_center_alias",
                "message": (
                    "SlotCenterPoint(...) uses `center=...`, not `center_point=...`."
                ),
                "repair_hint": (
                    "Rename `center_point=` to `center=` when calling `SlotCenterPoint(...)`."
                    + (
                        f" Repair the SlotCenterPoint keyword at line {line_no}."
                        if line_no > 0
                        else ""
                    )
                ),
            }
        )
    for alias_hit in _find_slot_center_to_center_keyword_alias_hits(parsed_tree):
        line_no = int(alias_hit.get("line_no") or 0)
        alias_name = str(alias_hit.get("alias_name") or "").strip()
        expected_keyword = "center_separation" if alias_name == "center_to_center" else "height"
        hits.append(
            {
                "rule_id": "invalid_build123d_keyword.slot_center_to_center_alias",
                "message": (
                    "SlotCenterToCenter(...) uses `center_separation=...` and "
                    f"`height=...`, not `{alias_name}=...`."
                ),
                "repair_hint": (
                    f"Rename `{alias_name}=` to `{expected_keyword}=` when calling "
                    "`SlotCenterToCenter(...)`."
                    + (
                        f" Repair the SlotCenterToCenter keyword at line {line_no}."
                        if line_no > 0
                        else ""
                    )
                ),
            }
        )
    return hits


__all__ = [name for name in globals() if not name.startswith("__")]
