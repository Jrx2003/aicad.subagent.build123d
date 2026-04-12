from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any

from sub_agent_runtime.feature_graph import (
    FamilyRepairPacket,
    PatchFeatureGraphInput,
    apply_domain_kernel_patch,
    build_domain_kernel_digest,
)
from sub_agent_runtime.turn_state import RunState, ToolCallRecord, ToolResultRecord


@dataclass(slots=True)
class KernelStateToolAdapter:
    """Thin adapter for runtime-owned semantic/kernel state tools."""

    tool_names: frozenset[str] = frozenset(
        {
            "query_kernel_state",
            "patch_domain_kernel",
        }
    )

    def handles(self, tool_name: str) -> bool:
        return tool_name in self.tool_names

    async def dispatch(
        self,
        *,
        tool_call: ToolCallRecord,
        run_state: RunState | None,
    ) -> ToolResultRecord:
        if run_state is None or run_state.feature_graph is None:
            return ToolResultRecord(
                name=tool_call.name,
                category=tool_call.category,
                success=False,
                payload={},
                error="feature_graph_unavailable",
            )

        args = dict(tool_call.arguments)
        if tool_call.name == "query_kernel_state":
            payload = build_domain_kernel_digest(
                run_state.feature_graph,
                include_nodes=bool(args.get("include_nodes", True)),
                include_edges=bool(args.get("include_edges", False)),
                include_bindings=bool(args.get("include_bindings", False)),
                include_revision_history=bool(args.get("include_revision_history", False)),
                max_nodes=int(args.get("max_nodes", 20) or 20),
                max_edges=int(args.get("max_edges", 20) or 20),
                max_bindings=int(args.get("max_bindings", 8) or 8),
                max_revisions=int(args.get("max_revisions", 8) or 8),
            )
            payload["success"] = True
            payload["state_view"] = "domain_kernel"
            return ToolResultRecord(
                name=tool_call.name,
                category=tool_call.category,
                success=True,
                payload=payload,
            )

        patch = PatchFeatureGraphInput.model_validate(args)
        next_graph, validation = apply_domain_kernel_patch(
            run_state.feature_graph,
            patch,
        )
        payload = {
            "success": bool(validation.get("ok")),
            "reason": patch.reason,
            "update_mode": patch.update_mode,
            "validation": validation,
            "domain_kernel_digest": build_domain_kernel_digest(next_graph),
        }
        if not validation.get("ok"):
            return ToolResultRecord(
                name=tool_call.name,
                category=tool_call.category,
                success=False,
                payload=payload,
                error="invalid_feature_graph_patch",
            )
        run_state.feature_graph = next_graph
        return ToolResultRecord(
            name=tool_call.name,
            category=tool_call.category,
            success=True,
            payload=payload,
        )


_SUPPORTED_RUNTIME_REPAIR_PACKET_RECIPES = frozenset(
    {
        "half_shell_profile_global_xz_lug_hole_recipe",
        "spherical_recess_host_face_center_set",
    }
)


def supports_runtime_repair_packet(packet: dict[str, Any] | None) -> bool:
    if not isinstance(packet, dict):
        return False
    recipe_id = str(packet.get("recipe_id") or "").strip()
    return recipe_id in _SUPPORTED_RUNTIME_REPAIR_PACKET_RECIPES


def compile_runtime_repair_packet_execution(
    *,
    run_state: RunState | None,
    packet_id: str | None = None,
    requirement_text: str = "",
) -> dict[str, Any]:
    if run_state is None or run_state.feature_graph is None:
        return {
            "ok": False,
            "error": "domain_kernel_unavailable",
        }

    graph = run_state.feature_graph
    packet = _select_runtime_repair_packet(graph.repair_packets, packet_id=packet_id)
    if packet is None:
        return {
            "ok": False,
            "error": "repair_packet_unavailable",
            "domain_kernel_digest": build_domain_kernel_digest(graph),
        }

    packet_payload = packet.to_dict()
    recipe_id = str(packet.recipe_id or "").strip()
    if recipe_id == "spherical_recess_host_face_center_set":
        compiled = _compile_spherical_recess_repair_packet(
            run_state=run_state,
            packet=packet,
            requirement_text=requirement_text,
        )
        compiled["packet"] = packet_payload
        compiled["family_id"] = packet.family_id
        compiled["repair_mode"] = packet.repair_mode
        return compiled
    if recipe_id == "half_shell_profile_global_xz_lug_hole_recipe":
        compiled = _compile_half_shell_profile_repair_packet(
            run_state=run_state,
            packet=packet,
            requirement_text=requirement_text,
        )
        compiled["packet"] = packet_payload
        compiled["family_id"] = packet.family_id
        compiled["repair_mode"] = packet.repair_mode
        return compiled

    return {
        "ok": False,
        "error": f"unsupported_repair_packet_recipe:{recipe_id or 'unknown'}",
        "packet": packet_payload,
        "domain_kernel_digest": build_domain_kernel_digest(graph),
    }


def _select_runtime_repair_packet(
    packets: dict[str, FamilyRepairPacket] | None,
    *,
    packet_id: str | None,
) -> FamilyRepairPacket | None:
    if not isinstance(packets, dict) or not packets:
        return None
    normalized_packet_id = str(packet_id or "").strip()
    if normalized_packet_id:
        packet = packets.get(normalized_packet_id)
        if packet is not None and not bool(getattr(packet, "stale", False)):
            return packet
    for packet in reversed(list(packets.values())):
        if not bool(getattr(packet, "stale", False)):
            return packet
    return None


def _compile_spherical_recess_repair_packet(
    *,
    run_state: RunState,
    packet: FamilyRepairPacket,
    requirement_text: str,
) -> dict[str, Any]:
    graph = run_state.feature_graph
    if graph is None:
        return {"ok": False, "error": "domain_kernel_unavailable"}
    feature_instance = graph.feature_instances.get(packet.feature_instance_id)
    if feature_instance is None:
        return {
            "ok": False,
            "error": f"repair_packet_feature_instance_missing:{packet.feature_instance_id}",
            "packet": packet.to_dict(),
            "domain_kernel_digest": build_domain_kernel_digest(graph),
        }
    parameter_bindings = (
        feature_instance.parameter_bindings
        if isinstance(feature_instance.parameter_bindings, dict)
        else {}
    )
    geometry_summary = (
        parameter_bindings.get("geometry_summary")
        if isinstance(parameter_bindings.get("geometry_summary"), dict)
        else {}
    )
    bbox = geometry_summary.get("bbox")
    if not (isinstance(bbox, list) and len(bbox) >= 3):
        return {
            "ok": False,
            "error": "repair_packet_missing_geometry_bbox",
            "packet": packet.to_dict(),
            "domain_kernel_digest": build_domain_kernel_digest(graph),
        }
    try:
        bbox_xyz = [float(bbox[index]) for index in range(3)]
    except (TypeError, ValueError):
        return {
            "ok": False,
            "error": "repair_packet_invalid_geometry_bbox",
            "packet": packet.to_dict(),
            "domain_kernel_digest": build_domain_kernel_digest(graph),
        }

    raw_centers = (
        packet.target_anchor_summary.get("expected_local_centers")
        if isinstance(packet.target_anchor_summary, dict)
        else None
    )
    centers = _coerce_xy_points(raw_centers)
    if not centers:
        centers = _coerce_xy_points(parameter_bindings.get("expected_local_centers"))
    if not centers:
        return {
            "ok": False,
            "error": "repair_packet_missing_expected_local_centers",
            "packet": packet.to_dict(),
            "domain_kernel_digest": build_domain_kernel_digest(graph),
        }

    radius = _infer_spherical_recess_radius(
        requirement_text=requirement_text,
        centers=centers,
    )
    host_face_selector = _host_face_selector_from_packet(packet)
    centers_literal = repr([tuple(center) for center in centers])
    code = "\n".join(
        [
            "from build123d import *",
            "",
            f"_aicad_bbox = ({bbox_xyz[0]}, {bbox_xyz[1]}, {bbox_xyz[2]})",
            f"_aicad_recess_radius = {radius}",
            f"_aicad_recess_points = {centers_literal}",
            f"_aicad_host_face_selector = {host_face_selector!r}",
            "def _aicad_recess_center(local_u, local_v):",
            "    sx, sy, sz = _aicad_bbox",
            "    if _aicad_host_face_selector == '>Z':",
            "        return (local_u, local_v, sz / 2.0)",
            "    if _aicad_host_face_selector == '<Z':",
            "        return (local_u, local_v, -sz / 2.0)",
            "    if _aicad_host_face_selector == '>X':",
            "        return (sx / 2.0, local_u, local_v)",
            "    if _aicad_host_face_selector == '<X':",
            "        return (-sx / 2.0, local_u, local_v)",
            "    if _aicad_host_face_selector == '>Y':",
            "        return (local_u, sy / 2.0, local_v)",
            "    return (local_u, -sy / 2.0, local_v)",
            "with BuildPart() as part:",
            "    Box(*_aicad_bbox)",
            "    for _aicad_local_u, _aicad_local_v in _aicad_recess_points:",
            "        with Locations(_aicad_recess_center(_aicad_local_u, _aicad_local_v)):",
            "            Sphere(_aicad_recess_radius, mode=Mode.SUBTRACT)",
            "result = part.part",
            "show_object(result)",
        ]
    )
    return {
        "ok": True,
        "packet_id": packet.packet_id,
        "family_id": packet.family_id,
        "recipe_id": packet.recipe_id,
        "repair_mode": packet.repair_mode,
        "compiler_summary": (
            "Compile the spherical recess packet into a deterministic host-face workplane "
            "sphere-array subtraction instead of a free-form execute_build123d rewrite."
        ),
        "code": code,
        "packet": packet.to_dict(),
        "compiled_parameters": {
            "bbox": bbox_xyz,
            "radius": radius,
            "expected_local_centers": centers,
            "host_face_selector": host_face_selector,
        },
        "domain_kernel_digest": build_domain_kernel_digest(graph),
    }


def _compile_half_shell_profile_repair_packet(
    *,
    run_state: RunState,
    packet: FamilyRepairPacket,
    requirement_text: str,
) -> dict[str, Any]:
    graph = run_state.feature_graph
    if graph is None:
        return {"ok": False, "error": "domain_kernel_unavailable"}
    feature_instance = graph.feature_instances.get(packet.feature_instance_id)
    if feature_instance is None:
        return {
            "ok": False,
            "error": f"repair_packet_feature_instance_missing:{packet.feature_instance_id}",
            "packet": packet.to_dict(),
            "domain_kernel_digest": build_domain_kernel_digest(graph),
        }
    parsed = _extract_half_shell_repair_parameters(requirement_text=requirement_text)
    missing = [key for key, value in parsed.items() if value in (None, [], {})]
    if missing:
        return {
            "ok": False,
            "error": f"repair_packet_missing_half_shell_parameters:{','.join(sorted(missing))}",
            "packet": packet.to_dict(),
            "compiled_parameters": parsed,
            "domain_kernel_digest": build_domain_kernel_digest(graph),
        }

    outer_radius = float(parsed["outer_radius"])
    inner_radius = float(parsed["inner_radius"])
    length = float(parsed["length"])
    pad_x_min = float(parsed["pad_x_min"])
    pad_x_max = float(parsed["pad_x_max"])
    pad_height = float(parsed["pad_height"])
    bore_diameter = float(parsed["bore_diameter"])
    hole_diameter = float(parsed["hole_diameter"])
    hole_centers_xz = [
        (float(center[0]), float(center[1]))
        for center in parsed["hole_centers_xz"]
    ]
    hole_extent = max(60.0, pad_height + (outer_radius * 2.0) + 10.0)
    code = "\n".join(
        [
            "from build123d import *",
            "",
            f"_aicad_outer_radius = {outer_radius}",
            f"_aicad_inner_radius = {inner_radius}",
            f"_aicad_length = {length}",
            f"_aicad_pad_x_min = {pad_x_min}",
            f"_aicad_pad_x_max = {pad_x_max}",
            f"_aicad_pad_height = {pad_height}",
            f"_aicad_bore_diameter = {bore_diameter}",
            f"_aicad_hole_diameter = {hole_diameter}",
            f"_aicad_hole_centers_xz = {repr(hole_centers_xz)}",
            f"_aicad_hole_extent = {hole_extent}",
            "",
            "with BuildPart() as part:",
            "    Cylinder(_aicad_outer_radius, _aicad_length, align=(Align.CENTER, Align.CENTER, Align.MIN))",
            "    Cylinder(_aicad_inner_radius, _aicad_length, mode=Mode.SUBTRACT, align=(Align.CENTER, Align.CENTER, Align.MIN))",
            "    Box(",
            "        (_aicad_outer_radius * 2.0) + 2.0,",
            "        _aicad_outer_radius + 1.0,",
            "        _aicad_length,",
            "        mode=Mode.INTERSECT,",
            "        align=(Align.CENTER, Align.MIN, Align.MIN),",
            "    )",
            "    with Locations(((_aicad_pad_x_min + _aicad_pad_x_max) / 2.0, _aicad_pad_height / 2.0, _aicad_length / 2.0)):",
            "        Box(_aicad_pad_x_max - _aicad_pad_x_min, _aicad_pad_height, _aicad_length)",
            "    Cylinder(_aicad_bore_diameter / 2.0, _aicad_length, mode=Mode.SUBTRACT, align=(Align.CENTER, Align.CENTER, Align.MIN))",
            "    for _aicad_center_x, _aicad_center_z in _aicad_hole_centers_xz:",
            "        with Locations((_aicad_center_x, 0.0, _aicad_center_z)):",
            "            Cylinder(",
            "                _aicad_hole_diameter / 2.0,",
            "                _aicad_hole_extent,",
            "                rotation=(90, 0, 0),",
            "                mode=Mode.SUBTRACT,",
            "            )",
            "result = part.part",
            "show_object(result)",
        ]
    )
    return {
        "ok": True,
        "packet_id": packet.packet_id,
        "family_id": packet.family_id,
        "recipe_id": packet.recipe_id,
        "repair_mode": packet.repair_mode,
        "compiler_summary": (
            "Compile the half-shell repair packet into a deterministic semi-annulus rebuild "
            "with global X/Z lug-hole anchors and explicit Y-axis cylinder cutters."
        ),
        "code": code,
        "packet": packet.to_dict(),
        "compiled_parameters": {
            **parsed,
            "hole_extent": hole_extent,
        },
        "domain_kernel_digest": build_domain_kernel_digest(graph),
    }


def _coerce_xy_points(raw_points: Any) -> list[list[float]]:
    if not isinstance(raw_points, list):
        return []
    points: list[list[float]] = []
    for item in raw_points:
        if not (isinstance(item, (list, tuple)) and len(item) >= 2):
            continue
        try:
            x_val = float(item[0])
            y_val = float(item[1])
        except (TypeError, ValueError):
            continue
        points.append([x_val, y_val])
    return points


def _infer_spherical_recess_radius(
    *,
    requirement_text: str,
    centers: list[list[float]],
) -> float:
    text = str(requirement_text or "")
    contextual_patterns = (
        r"(?:semicircle|sphere|spherical|hemispherical)[^.]{0,80}?radius(?: of)?\s*([0-9]+(?:\.[0-9]+)?)\s*mm",
        r"radius(?: of)?\s*([0-9]+(?:\.[0-9]+)?)\s*mm",
    )
    for pattern in contextual_patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match is None:
            continue
        try:
            radius = float(match.group(1))
        except (TypeError, ValueError):
            continue
        if radius > 1e-6:
            return radius
    min_spacing = None
    for current in centers:
        for other in centers:
            dx = abs(float(current[0]) - float(other[0]))
            dy = abs(float(current[1]) - float(other[1]))
            for value in (dx, dy):
                if value <= 1e-6:
                    continue
                if min_spacing is None or value < min_spacing:
                    min_spacing = value
    if min_spacing is not None and min_spacing > 1e-6:
        return round(min_spacing / 3.0, 4)
    return 5.0


def _host_face_selector_from_packet(packet: FamilyRepairPacket) -> str:
    host_face = ""
    if isinstance(packet.host_frame, dict):
        host_face = str(packet.host_frame.get("host_face") or "").strip().lower()
    if not host_face and isinstance(packet.target_anchor_summary, dict):
        host_face = str(packet.target_anchor_summary.get("host_face") or "").strip().lower()
    face_map = {
        "top": ">Z",
        ">z": ">Z",
        "bottom": "<Z",
        "<z": "<Z",
        "front": ">Y",
        ">y": ">Y",
        "back": "<Y",
        "<y": "<Y",
        "right": ">X",
        ">x": ">X",
        "left": "<X",
        "<x": "<X",
    }
    if host_face in face_map:
        return face_map[host_face]
    if host_face in {"body.primary", ""}:
        return ">Z"
    return ">Z"


def _extract_half_shell_repair_parameters(
    *,
    requirement_text: str,
) -> dict[str, Any]:
    text = str(requirement_text or "")
    outer_radius = _capture_first_float(
        text,
        (
            r"outer\s+semicircle\s+of\s+radius\s+([0-9]+(?:\.[0-9]+)?)",
            r"outer\s+radius(?:\s+of)?\s+([0-9]+(?:\.[0-9]+)?)",
            r"outer\s+diameter(?:\s+of)?\s+([0-9]+(?:\.[0-9]+)?)",
        ),
    )
    if outer_radius is not None and "outer diameter" in text.lower():
        outer_radius = outer_radius / 2.0
    inner_radius = _capture_first_float(
        text,
        (
            r"inner\s+semicircle\s+of\s+radius\s+([0-9]+(?:\.[0-9]+)?)",
            r"inner\s+radius(?:\s+of)?\s+([0-9]+(?:\.[0-9]+)?)",
            r"inner\s+diameter(?:\s+of)?\s+([0-9]+(?:\.[0-9]+)?)",
        ),
    )
    if inner_radius is not None and "inner diameter" in text.lower():
        inner_radius = inner_radius / 2.0
    length = _capture_first_float(
        text,
        (
            r"extrud(?:e|ing it)\s+([0-9]+(?:\.[0-9]+)?)\s+millimeter",
            r"length(?:\s+of)?\s+([0-9]+(?:\.[0-9]+)?)\s*millimeter",
        ),
    )
    pad_span_match = re.search(
        r"spanning\s+x\s*=\s*([-+]?[0-9]+(?:\.[0-9]+)?)\s*(?:to|-)\s*([-+]?[0-9]+(?:\.[0-9]+)?)",
        text,
        re.IGNORECASE,
    )
    pad_x_min = None
    pad_x_max = None
    if pad_span_match is not None:
        pad_x_min = float(pad_span_match.group(1))
        pad_x_max = float(pad_span_match.group(2))
    pad_height = _capture_first_float(
        text,
        (
            r"height\s+of\s+([0-9]+(?:\.[0-9]+)?)\s*millimeter",
        ),
    )
    bore_diameter = _capture_first_float(
        text,
        (
            r"inner\s+([0-9]+(?:\.[0-9]+)?)\s+millimeter\s+diameter\s+clearance",
            r"([0-9]+(?:\.[0-9]+)?)\s+millimeter\s+diameter\s+clearance",
        ),
    )
    hole_diameter = _capture_first_float(
        text,
        (
            r"two\s+([0-9]+(?:\.[0-9]+)?)\s+millimeter\s+through-holes",
            r"([0-9]+(?:\.[0-9]+)?)\s+millimeter\s+through-holes",
        ),
    )
    hole_centers_xz = _extract_half_shell_hole_centers_xz(text)
    return {
        "outer_radius": outer_radius,
        "inner_radius": inner_radius,
        "length": length,
        "pad_x_min": pad_x_min,
        "pad_x_max": pad_x_max,
        "pad_height": pad_height,
        "bore_diameter": bore_diameter,
        "hole_diameter": hole_diameter,
        "hole_centers_xz": hole_centers_xz,
    }


def _capture_first_float(text: str, patterns: tuple[str, ...]) -> float | None:
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match is None:
            continue
        try:
            value = float(match.group(1))
        except (TypeError, ValueError):
            continue
        if value > 0.0:
            return value
    return None


def _extract_axis_values(text: str, axis: str) -> list[float]:
    values: list[float] = []
    pattern = re.compile(
        rf"\b{re.escape(axis)}\s*=\s*(?P<pm>±\s*)?(?P<value>[-+]?[0-9]+(?:\.[0-9]+)?)",
        re.IGNORECASE,
    )
    for match in pattern.finditer(text):
        try:
            value = float(match.group("value"))
        except (TypeError, ValueError):
            continue
        if match.group("pm"):
            for signed_value in (-abs(value), abs(value)):
                if signed_value not in values:
                    values.append(signed_value)
        elif value not in values:
            values.append(value)
    return values


def _extract_half_shell_hole_centers_xz(text: str) -> list[list[float]]:
    candidate_clauses = [text]
    candidate_clauses.extend(
        clause.strip()
        for clause in re.split(r"[;\n]", text)
        if "hole" in clause.lower()
    )
    for clause in candidate_clauses:
        center_clause_match = re.search(r"\bcenter(?:ed)?\b.*$", clause, re.IGNORECASE)
        x_source = center_clause_match.group(0) if center_clause_match is not None else clause
        x_values = _extract_axis_values(x_source, "x")
        z_values = _extract_axis_values(clause, "z")
        if len(x_values) >= 2 and len(z_values) == 1:
            return [
                [float(x_value), float(z_values[0])]
                for x_value in x_values
            ]
    x_values = _extract_axis_values(text, "x")
    z_values = _extract_axis_values(text, "z")
    if len(x_values) >= 2 and len(z_values) == 1:
        return [
            [float(x_value), float(z_values[0])]
            for x_value in x_values[-2:]
        ]
    return []
