from __future__ import annotations

import math
import re
from typing import Any

from sandbox_mcp_server.registry import (
    analyze_requirement_semantics,
    requirement_requests_path_sweep,
)

_STATUS_PASS = "pass"
_STATUS_FAIL = "fail"
_STATUS_INFO = "info"
_STATUS_MISSING = "missing"


def build_relation_feedback(
    *,
    requirements: dict[str, Any],
    action_history: list[dict[str, Any]] | None,
    query_sketch: dict[str, Any] | None,
    query_topology: dict[str, Any] | None,
    query_geometry: dict[str, Any] | None,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    requirement_text = _requirements_text_for_matching(requirements)
    semantics = analyze_requirement_semantics(
        requirements=requirements,
        requirement_text=requirement_text,
    )
    step = _latest_step(query_sketch, query_topology, query_geometry)
    state_mode = _state_mode(query_geometry=query_geometry, action_history=action_history)

    focus_items: list[dict[str, Any]] = []
    if _should_focus_sweep(
        requirement_text=requirement_text,
        semantics=semantics,
        action_history=action_history,
        query_sketch=query_sketch,
    ):
        focus_items.extend(
            _build_sweep_focus_items(
                requirement_text=requirement_text,
                state_mode=state_mode,
                query_sketch=query_sketch,
                query_topology=query_topology,
            )
        )
    elif _requirement_suggests_annular_focus(requirement_text):
        focus_items.extend(
            _build_annular_focus_items(
                requirement_text=requirement_text,
                state_mode=state_mode,
                query_sketch=query_sketch,
                query_topology=query_topology,
            )
        )

    relation_focus = None
    if focus_items:
        relation_focus = {
            "version": "v1",
            "step": step,
            "state_mode": state_mode,
            "selection_basis": "deterministic_requirement_parser_v1",
            "items": focus_items,
            "summary": f"{len(focus_items)} focus item(s)",
        }

    eval_items: list[dict[str, Any]] = []
    for item in focus_items:
        focus_type = str(item.get("focus_type", "")).strip()
        if focus_type == "sweep_path_geometry":
            eval_items.append(
                _eval_sweep_path_geometry(
                    focus=item,
                    requirement_text=requirement_text,
                    query_sketch=query_sketch,
                )
            )
            continue
        if focus_type == "sweep_profile_section":
            eval_items.append(
                _eval_sweep_profile_section(
                    focus=item,
                    requirement_text=requirement_text,
                    query_sketch=query_sketch,
                )
            )
            continue
        if focus_type == "sweep_result_annular_topology":
            eval_items.append(
                _eval_annular_topology(
                    focus=item,
                    requirement_text=requirement_text,
                    query_topology=query_topology,
                    blocking=True,
                )
            )
            continue
        if focus_type == "annular_profile_section":
            eval_items.append(
                _eval_annular_profile_section(
                    focus=item,
                    requirement_text=requirement_text,
                    query_sketch=query_sketch,
                )
            )
            continue
        if focus_type == "annular_topology_core":
            eval_items.append(
                _eval_annular_topology(
                    focus=item,
                    requirement_text=requirement_text,
                    query_topology=query_topology,
                    blocking=False,
                )
            )

    relation_eval = None
    if eval_items:
        blocking_eval_ids = [
            str(item.get("eval_id", "")).strip()
            for item in eval_items
            if bool(item.get("blocking"))
            and str(item.get("status", "")).strip().lower() in {_STATUS_FAIL, _STATUS_MISSING}
        ]
        relation_eval = {
            "version": "v1",
            "step": step,
            "state_mode": state_mode,
            "selection_basis": "deterministic_requirement_parser_v1",
            "items": eval_items,
            "blocking_eval_ids": [item for item in blocking_eval_ids if item],
            "summary": (
                f"{len(eval_items)} eval item(s), "
                f"{len(blocking_eval_ids)} blocking"
            ),
        }

    return relation_focus, relation_eval


def _build_sweep_focus_items(
    *,
    requirement_text: str,
    state_mode: str,
    query_sketch: dict[str, Any] | None,
    query_topology: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    path = _primary_path(query_sketch)
    profile = _primary_profile(query_sketch)
    path_ref = str(path.get("path_ref", "")).strip() if isinstance(path, dict) else ""
    profile_ref = (
        str(profile.get("profile_ref", "")).strip() if isinstance(profile, dict) else ""
    )
    sketch_relation_index = _relation_index(query_sketch)
    topology_relation_index = _relation_index(query_topology)
    path_spec = _extract_sweep_path_requirement_spec(requirement_text)
    profile_spec = _extract_annular_requirement_spec(requirement_text)

    path_relation_ids = _relation_ids_for_prefix(
        sketch_relation_index,
        relation_types={"connected", "tangent"},
        prefix=path_ref,
    )
    profile_relation_ids = _relation_ids_for_prefix(
        sketch_relation_index,
        relation_types={"concentric", "attached_to_path_endpoint"},
        prefix=profile_ref,
    )
    topology_relation_ids = _relation_ids_for_types(
        topology_relation_index,
        relation_types={"coaxial", "equal_radius", "concentric"},
    )
    topology_group_ids = _group_ids_for_types(
        topology_relation_index,
        group_types={"annular_edge_pair", "annular_cylindrical_pair"},
    )

    items.append(
        {
            "focus_id": "sweep_path_geometry",
            "focus_type": "sweep_path_geometry",
            "priority": 1,
            "required_tools": ["query_sketch"],
            "expected_relation_types": ["connected", "tangent"],
            "expected_metrics": path_spec,
            "supporting_entity_ids": [path_ref] if path_ref else [],
            "supporting_relation_ids": path_relation_ids,
            "supporting_group_ids": [],
            "observation": (
                "Requirement explicitly names a sweep rail; use current path relations plus measured line/arc dimensions."
            ),
        }
    )
    items.append(
        {
            "focus_id": "sweep_profile_section",
            "focus_type": "sweep_profile_section",
            "priority": 2,
            "required_tools": ["query_sketch"],
            "expected_relation_types": ["concentric", "attached_to_path_endpoint"],
            "expected_metrics": profile_spec,
            "supporting_entity_ids": [profile_ref] if profile_ref else [],
            "supporting_relation_ids": profile_relation_ids,
            "supporting_group_ids": _group_ids_for_types(
                sketch_relation_index,
                group_types={"annular_profile", "sweep_profile_pair"},
            ),
            "observation": (
                "Requirement explicitly names an annular sweep profile attached to a path endpoint frame."
            ),
        }
    )
    items.append(
        {
            "focus_id": "sweep_result_annular_topology",
            "focus_type": "sweep_result_annular_topology",
            "priority": 3 if state_mode == "post_solid" else 2,
            "required_tools": ["query_topology"],
            "expected_relation_types": ["coaxial", "annular_edge_pair"],
            "expected_metrics": profile_spec,
            "supporting_entity_ids": [],
            "supporting_relation_ids": topology_relation_ids,
            "supporting_group_ids": topology_group_ids,
            "observation": (
                "For hollow sweep results, prefer edge-based annular/coaxial evidence at end sections instead of relying on wall faces being CYLINDER."
            ),
        }
    )
    return items


def _build_annular_focus_items(
    *,
    requirement_text: str,
    state_mode: str,
    query_sketch: dict[str, Any] | None,
    query_topology: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    annular_spec = _extract_annular_requirement_spec(requirement_text)
    sketch_relation_index = _relation_index(query_sketch)
    topology_relation_index = _relation_index(query_topology)
    profile = _primary_profile(query_sketch)
    profile_ref = (
        str(profile.get("profile_ref", "")).strip() if isinstance(profile, dict) else ""
    )

    if state_mode != "post_solid" or query_sketch is not None:
        items.append(
            {
                "focus_id": "annular_profile_section",
                "focus_type": "annular_profile_section",
                "priority": 1,
                "required_tools": ["query_sketch"],
                "expected_relation_types": ["concentric"],
                "expected_metrics": annular_spec,
                "supporting_entity_ids": [profile_ref] if profile_ref else [],
                "supporting_relation_ids": _relation_ids_for_prefix(
                    sketch_relation_index,
                    relation_types={"concentric"},
                    prefix=profile_ref,
                ),
                "supporting_group_ids": _group_ids_for_types(
                    sketch_relation_index,
                    group_types={"annular_profile"},
                ),
                "observation": (
                    "Requirement names concentric inner/outer circular sections, so the sketch profile should expose an annular section."
                ),
            }
        )

    items.append(
        {
            "focus_id": "annular_topology_core",
            "focus_type": "annular_topology_core",
            "priority": 2 if state_mode == "post_solid" else 3,
            "required_tools": ["query_topology"],
            "expected_relation_types": ["coaxial", "annular_edge_pair"],
            "expected_metrics": annular_spec,
            "supporting_entity_ids": [],
            "supporting_relation_ids": _relation_ids_for_types(
                topology_relation_index,
                relation_types={"coaxial", "equal_radius", "concentric"},
            ),
            "supporting_group_ids": _group_ids_for_types(
                topology_relation_index,
                group_types={"annular_edge_pair", "annular_cylindrical_pair"},
            ),
            "observation": (
                "After solid creation, the annular core should be readable as coaxial inner/outer circular pairs in topology."
            ),
        }
    )
    return items


def _eval_sweep_path_geometry(
    *,
    focus: dict[str, Any],
    requirement_text: str,
    query_sketch: dict[str, Any] | None,
) -> dict[str, Any]:
    path = _primary_path(query_sketch)
    expected = _extract_sweep_path_requirement_spec(requirement_text)
    if path is None:
        return _missing_eval(
            focus=focus,
            observation="Current round has no query_sketch path evidence, so the sweep rail cannot be evaluated.",
            blocking=True,
        )

    observed_types = [
        str(item).strip().lower()
        for item in (path.get("segment_types") or [])
        if isinstance(item, str) and item.strip()
    ]
    segments = path.get("segments") if isinstance(path.get("segments"), list) else []
    observed_line_lengths = [
        float(segment.get("length"))
        for segment in segments
        if isinstance(segment, dict)
        and str(segment.get("segment_type", "")).strip().lower() == "line"
        and isinstance(segment.get("length"), (int, float))
    ]
    arc_segment = next(
        (
            segment
            for segment in segments
            if isinstance(segment, dict)
            and str(segment.get("segment_type", "")).strip().lower()
            in {"arc", "tangent_arc"}
        ),
        None,
    )
    observed_arc_radius = (
        float(arc_segment.get("radius"))
        if isinstance(arc_segment, dict) and isinstance(arc_segment.get("radius"), (int, float))
        else None
    )
    observed_arc_angle = (
        float(arc_segment.get("angle_degrees"))
        if isinstance(arc_segment, dict)
        and isinstance(arc_segment.get("angle_degrees"), (int, float))
        else None
    )

    segment_count = len(segments)
    expected_joint_count = max(0, segment_count - 1)
    relation_index = _relation_index(query_sketch)
    connected_ids = _relation_ids_for_prefix(
        relation_index,
        relation_types={"connected"},
        prefix=str(path.get("path_ref", "")).strip(),
    )
    tangent_ids = _relation_ids_for_prefix(
        relation_index,
        relation_types={"tangent"},
        prefix=str(path.get("path_ref", "")).strip(),
    )
    connectivity_score = 1.0 if expected_joint_count == 0 else min(
        1.0,
        len(connected_ids) / expected_joint_count,
    )
    tangent_score = 1.0
    if "tangent_arc" in observed_types or "tangent_arc" in [
        str(item).strip().lower()
        for item in (expected.get("segment_types") or [])
        if isinstance(item, str)
    ]:
        tangent_score = 1.0 if expected_joint_count == 0 else min(
            1.0,
            len(tangent_ids) / expected_joint_count,
        )

    component_scores = [connectivity_score, tangent_score]
    deviation: dict[str, Any] = {
        "missing_connected_joints": max(0, expected_joint_count - len(connected_ids)),
        "missing_tangent_joints": max(0, expected_joint_count - len(tangent_ids)),
    }
    status = _STATUS_PASS

    expected_types = [
        str(item).strip().lower()
        for item in (expected.get("segment_types") or [])
        if isinstance(item, str) and item.strip()
    ]
    if expected_types:
        type_match = observed_types[: len(expected_types)] == expected_types
        component_scores.append(1.0 if type_match else 0.0)
        if not type_match:
            status = _STATUS_FAIL
            deviation["segment_types"] = {
                "expected": expected_types,
                "observed": observed_types,
            }

    expected_line_lengths = [
        float(item)
        for item in (expected.get("line_lengths") or [])
        if isinstance(item, (int, float))
    ]
    if expected_line_lengths:
        if len(observed_line_lengths) < len(expected_line_lengths):
            status = _STATUS_FAIL
            deviation["line_lengths"] = {
                "expected": expected_line_lengths,
                "observed": observed_line_lengths,
            }
            component_scores.append(0.0)
        else:
            line_deltas: list[float] = []
            for observed_value, expected_value in zip(
                observed_line_lengths,
                expected_line_lengths,
            ):
                component_scores.append(
                    _numeric_match_score(
                        observed_value,
                        expected_value,
                        absolute_tolerance=1.0,
                        relative_tolerance=0.08,
                    )
                    or 0.0
                )
                line_deltas.append(round(float(observed_value) - float(expected_value), 6))
                if not _numeric_match_passes(
                    observed_value,
                    expected_value,
                    absolute_tolerance=1.0,
                    relative_tolerance=0.08,
                ):
                    status = _STATUS_FAIL
            deviation["line_length_delta"] = line_deltas

    expected_arc_radius = expected.get("arc_radius")
    if isinstance(expected_arc_radius, (int, float)):
        component_scores.append(
            _numeric_match_score(
                observed_arc_radius,
                float(expected_arc_radius),
                absolute_tolerance=1.0,
                relative_tolerance=0.08,
            )
            or 0.0
        )
        deviation["arc_radius_delta"] = (
            round(float(observed_arc_radius) - float(expected_arc_radius), 6)
            if isinstance(observed_arc_radius, (int, float))
            else None
        )
        if not _numeric_match_passes(
            observed_arc_radius,
            float(expected_arc_radius),
            absolute_tolerance=1.0,
            relative_tolerance=0.08,
        ):
            status = _STATUS_FAIL

    expected_arc_angle = expected.get("arc_angle_degrees")
    if isinstance(expected_arc_angle, (int, float)):
        component_scores.append(
            _numeric_match_score(
                observed_arc_angle,
                float(expected_arc_angle),
                absolute_tolerance=2.0,
                relative_tolerance=0.03,
            )
            or 0.0
        )
        deviation["arc_angle_delta"] = (
            round(float(observed_arc_angle) - float(expected_arc_angle), 6)
            if isinstance(observed_arc_angle, (int, float))
            else None
        )
        if not _numeric_match_passes(
            observed_arc_angle,
            float(expected_arc_angle),
            absolute_tolerance=2.0,
            relative_tolerance=0.03,
        ):
            status = _STATUS_FAIL

    if connectivity_score < 1.0 or tangent_score < 1.0:
        status = _STATUS_FAIL

    score = round(sum(component_scores) / len(component_scores), 6) if component_scores else 0.0
    observation = (
        "Sweep rail matches the required line-arc-line path."
        if status == _STATUS_PASS
        else "Sweep rail relation/geometry drifts from the required path."
    )
    return _make_eval(
        focus=focus,
        status=status,
        blocking=True,
        score=score,
        expected=expected,
        observed={
            "path_ref": path.get("path_ref"),
            "segment_types": observed_types,
            "line_lengths": observed_line_lengths,
            "arc_radius": observed_arc_radius,
            "arc_angle_degrees": observed_arc_angle,
            "connected_relation_count": len(connected_ids),
            "tangent_relation_count": len(tangent_ids),
        },
        deviation=deviation,
        observation=observation,
    )


def _eval_sweep_profile_section(
    *,
    focus: dict[str, Any],
    requirement_text: str,
    query_sketch: dict[str, Any] | None,
) -> dict[str, Any]:
    profile = _primary_profile(query_sketch)
    expected = _extract_annular_requirement_spec(requirement_text)
    if profile is None:
        return _missing_eval(
            focus=focus,
            observation="Current round has no query_sketch profile evidence, so the sweep section cannot be evaluated.",
            blocking=True,
        )

    relation_index = _relation_index(query_sketch)
    annular_group = _best_group(
        relation_index,
        group_types={"annular_profile"},
        expected_outer_radius=expected.get("outer_radius"),
        expected_inner_radius=expected.get("inner_radius"),
    )
    attached_relation_ids = _relation_ids_for_prefix(
        relation_index,
        relation_types={"attached_to_path_endpoint"},
        prefix=str(profile.get("profile_ref", "")).strip(),
    )
    observed_radii = sorted(
        float(radius)
        for radius in (profile.get("loop_radii") or [])
        if isinstance(radius, (int, float))
    )
    observed_outer_radius = observed_radii[-1] if observed_radii else None
    observed_inner_radius = observed_radii[-2] if len(observed_radii) >= 2 else None
    observed_area = (
        float(profile.get("estimated_area"))
        if isinstance(profile.get("estimated_area"), (int, float))
        else None
    )

    component_scores: list[float] = []
    deviation: dict[str, Any] = {
        "attachment_count": len(attached_relation_ids),
        "annular_group_id": annular_group.get("group_id") if isinstance(annular_group, dict) else None,
    }
    status = _STATUS_PASS

    component_scores.append(1.0 if annular_group is not None else 0.0)
    if annular_group is None:
        status = _STATUS_FAIL
    component_scores.append(1.0 if attached_relation_ids else 0.0)
    if not attached_relation_ids:
        status = _STATUS_FAIL

    expected_outer_radius = expected.get("outer_radius")
    if isinstance(expected_outer_radius, (int, float)):
        component_scores.append(
            _numeric_match_score(
                observed_outer_radius,
                float(expected_outer_radius),
                absolute_tolerance=0.5,
                relative_tolerance=0.06,
            )
            or 0.0
        )
        deviation["outer_radius_delta"] = (
            round(float(observed_outer_radius) - float(expected_outer_radius), 6)
            if isinstance(observed_outer_radius, (int, float))
            else None
        )
        if not _numeric_match_passes(
            observed_outer_radius,
            float(expected_outer_radius),
            absolute_tolerance=0.5,
            relative_tolerance=0.06,
        ):
            status = _STATUS_FAIL

    expected_inner_radius = expected.get("inner_radius")
    if isinstance(expected_inner_radius, (int, float)):
        component_scores.append(
            _numeric_match_score(
                observed_inner_radius,
                float(expected_inner_radius),
                absolute_tolerance=0.5,
                relative_tolerance=0.06,
            )
            or 0.0
        )
        deviation["inner_radius_delta"] = (
            round(float(observed_inner_radius) - float(expected_inner_radius), 6)
            if isinstance(observed_inner_radius, (int, float))
            else None
        )
        if not _numeric_match_passes(
            observed_inner_radius,
            float(expected_inner_radius),
            absolute_tolerance=0.5,
            relative_tolerance=0.06,
        ):
            status = _STATUS_FAIL

    expected_area = expected.get("estimated_area")
    if isinstance(expected_area, (int, float)):
        component_scores.append(
            _numeric_match_score(
                observed_area,
                float(expected_area),
                absolute_tolerance=2.0,
                relative_tolerance=0.08,
            )
            or 0.0
        )
        deviation["area_delta"] = (
            round(float(observed_area) - float(expected_area), 6)
            if isinstance(observed_area, (int, float))
            else None
        )
        if not _numeric_match_passes(
            observed_area,
            float(expected_area),
            absolute_tolerance=2.0,
            relative_tolerance=0.08,
        ):
            status = _STATUS_FAIL

    score = round(sum(component_scores) / len(component_scores), 6) if component_scores else 0.0
    observation = (
        "Sweep profile exposes an attached annular section with the required radii."
        if status == _STATUS_PASS
        else "Sweep profile is missing annular/attachment evidence or its radii drift from the requirement."
    )
    return _make_eval(
        focus=focus,
        status=status,
        blocking=True,
        score=score,
        expected=expected,
        observed={
            "profile_ref": profile.get("profile_ref"),
            "loop_radii": observed_radii,
            "estimated_area": observed_area,
            "attached_path_ref": profile.get("attached_path_ref"),
        },
        deviation=deviation,
        observation=observation,
    )


def _eval_annular_profile_section(
    *,
    focus: dict[str, Any],
    requirement_text: str,
    query_sketch: dict[str, Any] | None,
) -> dict[str, Any]:
    profile = _primary_profile(query_sketch)
    expected = _extract_annular_requirement_spec(requirement_text)
    if profile is None:
        return _missing_eval(
            focus=focus,
            observation="Current round has no query_sketch annular profile evidence.",
        )

    relation_index = _relation_index(query_sketch)
    annular_group = _best_group(
        relation_index,
        group_types={"annular_profile"},
        expected_outer_radius=expected.get("outer_radius"),
        expected_inner_radius=expected.get("inner_radius"),
    )
    observed_radii = sorted(
        float(radius)
        for radius in (profile.get("loop_radii") or [])
        if isinstance(radius, (int, float))
    )
    observed_outer_radius = observed_radii[-1] if observed_radii else None
    observed_inner_radius = observed_radii[-2] if len(observed_radii) >= 2 else None
    component_scores = [1.0 if annular_group is not None else 0.0]
    status = _STATUS_PASS if annular_group is not None else _STATUS_FAIL
    deviation: dict[str, Any] = {
        "annular_group_id": annular_group.get("group_id") if isinstance(annular_group, dict) else None,
    }

    for key, observed_value, expected_value in (
        ("outer_radius_delta", observed_outer_radius, expected.get("outer_radius")),
        ("inner_radius_delta", observed_inner_radius, expected.get("inner_radius")),
    ):
        if isinstance(expected_value, (int, float)):
            component_scores.append(
                _numeric_match_score(
                    observed_value,
                    float(expected_value),
                    absolute_tolerance=0.5,
                    relative_tolerance=0.06,
                )
                or 0.0
            )
            deviation[key] = (
                round(float(observed_value) - float(expected_value), 6)
                if isinstance(observed_value, (int, float))
                else None
            )
            if not _numeric_match_passes(
                observed_value,
                float(expected_value),
                absolute_tolerance=0.5,
                relative_tolerance=0.06,
            ):
                status = _STATUS_FAIL

    score = round(sum(component_scores) / len(component_scores), 6) if component_scores else 0.0
    observation = (
        "Current sketch exposes the required annular section."
        if status == _STATUS_PASS
        else "Current sketch does not yet expose a requirement-aligned annular section."
    )
    return _make_eval(
        focus=focus,
        status=status,
        blocking=False,
        score=score,
        expected=expected,
        observed={
            "profile_ref": profile.get("profile_ref"),
            "loop_radii": observed_radii,
        },
        deviation=deviation,
        observation=observation,
    )


def _eval_annular_topology(
    *,
    focus: dict[str, Any],
    requirement_text: str,
    query_topology: dict[str, Any] | None,
    blocking: bool,
) -> dict[str, Any]:
    relation_index = _relation_index(query_topology)
    if relation_index is None:
        return _missing_eval(
            focus=focus,
            observation="Current round has no query_topology relation-base, so annular/coaxial topology cannot be evaluated.",
            blocking=blocking,
        )

    expected = _extract_annular_requirement_spec(requirement_text)
    annular_group = _best_group(
        relation_index,
        group_types={"annular_edge_pair", "annular_cylindrical_pair"},
        expected_outer_radius=expected.get("outer_radius"),
        expected_inner_radius=expected.get("inner_radius"),
    )
    if annular_group is None:
        return _make_eval(
            focus=focus,
            status=_STATUS_FAIL,
            blocking=blocking,
            score=0.0,
            expected=expected,
            observed={},
            deviation={"annular_group_found": False},
            observation=(
                "Current topology does not expose a matching annular pair yet; for sweep-like results this often means the hollow result or query window is still wrong."
            ),
        )

    derived = annular_group.get("derived") if isinstance(annular_group.get("derived"), dict) else {}
    observed_outer_radius = (
        float(derived.get("outer_radius"))
        if isinstance(derived.get("outer_radius"), (int, float))
        else None
    )
    observed_inner_radius = (
        float(derived.get("inner_radius"))
        if isinstance(derived.get("inner_radius"), (int, float))
        else None
    )
    component_scores = [1.0]
    deviation: dict[str, Any] = {
        "group_type": annular_group.get("group_type"),
        "group_id": annular_group.get("group_id"),
    }
    status = _STATUS_PASS

    for key, observed_value, expected_value in (
        ("outer_radius_delta", observed_outer_radius, expected.get("outer_radius")),
        ("inner_radius_delta", observed_inner_radius, expected.get("inner_radius")),
    ):
        if isinstance(expected_value, (int, float)):
            component_scores.append(
                _numeric_match_score(
                    observed_value,
                    float(expected_value),
                    absolute_tolerance=0.75,
                    relative_tolerance=0.08,
                )
                or 0.0
            )
            deviation[key] = (
                round(float(observed_value) - float(expected_value), 6)
                if isinstance(observed_value, (int, float))
                else None
            )
            if not _numeric_match_passes(
                observed_value,
                float(expected_value),
                absolute_tolerance=0.75,
                relative_tolerance=0.08,
            ):
                status = _STATUS_FAIL

    supporting_relation_ids = _supporting_relation_ids_for_group(
        relation_index=relation_index,
        group=annular_group,
        relation_types={"coaxial", "equal_radius", "concentric"},
    )
    score = round(sum(component_scores) / len(component_scores), 6) if component_scores else 0.0
    preferred_basis = (
        "edge_based"
        if str(annular_group.get("group_type", "")).strip() == "annular_edge_pair"
        else "face_based"
    )
    observation = (
        "Topology exposes a requirement-aligned annular pair."
        if status == _STATUS_PASS
        else "Topology annular pair exists, but its radii still drift from the requirement."
    )
    if preferred_basis == "edge_based":
        observation += " Evaluation preferred circular end edges so EXTRUSION/REVOLUTION wall faces do not hide coaxial evidence."
    return _make_eval(
        focus=focus,
        status=status,
        blocking=blocking,
        score=score,
        expected=expected,
        observed={
            "group_type": annular_group.get("group_type"),
            "outer_radius": observed_outer_radius,
            "inner_radius": observed_inner_radius,
            "wall_thickness": derived.get("wall_thickness"),
            "basis": preferred_basis,
        },
        deviation=deviation,
        observation=observation,
        supporting_relation_ids=supporting_relation_ids,
        supporting_group_ids=[str(annular_group.get("group_id", "")).strip()],
    )


def _missing_eval(
    *,
    focus: dict[str, Any],
    observation: str,
    blocking: bool | None = None,
) -> dict[str, Any]:
    return _make_eval(
        focus=focus,
        status=_STATUS_MISSING,
        blocking=bool(focus.get("priority") == 1) if blocking is None else blocking,
        score=0.0,
        expected=focus.get("expected_metrics") if isinstance(focus.get("expected_metrics"), dict) else {},
        observed={},
        deviation={"missing_required_tools": focus.get("required_tools")},
        observation=observation,
    )


def _make_eval(
    *,
    focus: dict[str, Any],
    status: str,
    blocking: bool,
    score: float,
    expected: dict[str, Any],
    observed: dict[str, Any],
    deviation: dict[str, Any],
    observation: str,
    supporting_relation_ids: list[str] | None = None,
    supporting_group_ids: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "eval_id": f"eval:{focus.get('focus_id')}",
        "focus_id": focus.get("focus_id"),
        "relation_family": focus.get("focus_type"),
        "status": status,
        "blocking": blocking,
        "score": round(max(0.0, min(1.0, float(score))), 6),
        "expected": expected,
        "observed": observed,
        "deviation": deviation,
        "supporting_entity_ids": list(focus.get("supporting_entity_ids") or []),
        "supporting_relation_ids": (
            list(dict.fromkeys(supporting_relation_ids))
            if isinstance(supporting_relation_ids, list)
            else list(focus.get("supporting_relation_ids") or [])
        ),
        "supporting_group_ids": (
            list(dict.fromkeys(supporting_group_ids))
            if isinstance(supporting_group_ids, list)
            else list(focus.get("supporting_group_ids") or [])
        ),
        "observation": observation,
    }


def _requirements_text_for_matching(requirements: dict[str, Any]) -> str:
    parts: list[str] = []
    for key in ("description", "prompt", "requirement", "requirement_text"):
        value = requirements.get(key)
        if isinstance(value, str) and value.strip():
            parts.append(value.strip())
    features = requirements.get("features")
    if isinstance(features, list):
        for item in features:
            if isinstance(item, str) and item.strip():
                parts.append(item.strip())
    elif isinstance(features, str) and features.strip():
        parts.append(features.strip())
    return " ".join(parts).lower()


def _state_mode(
    *,
    query_geometry: dict[str, Any] | None,
    action_history: list[dict[str, Any]] | None,
) -> str:
    solids = _solid_count(query_geometry=query_geometry, action_history=action_history)
    return "post_solid" if isinstance(solids, int) and solids > 0 else "pre_solid"


def _solid_count(
    *,
    query_geometry: dict[str, Any] | None,
    action_history: list[dict[str, Any]] | None,
) -> int | None:
    if isinstance(query_geometry, dict):
        geometry = query_geometry.get("geometry")
        if isinstance(geometry, dict) and isinstance(geometry.get("solids"), (int, float)):
            return int(geometry.get("solids"))
    for item in reversed(action_history or []):
        if not isinstance(item, dict):
            continue
        snapshot = item.get("result_snapshot")
        if not isinstance(snapshot, dict):
            continue
        geometry = snapshot.get("geometry")
        if isinstance(geometry, dict) and isinstance(geometry.get("solids"), (int, float)):
            return int(geometry.get("solids"))
    return None


def _latest_step(*payloads: dict[str, Any] | None) -> int | None:
    values: list[int] = []
    for payload in payloads:
        if not isinstance(payload, dict):
            continue
        step = payload.get("step")
        if isinstance(step, int) and step > 0:
            values.append(step)
    return max(values) if values else None


def _should_focus_sweep(
    *,
    requirement_text: str,
    semantics: Any,
    action_history: list[dict[str, Any]] | None,
    query_sketch: dict[str, Any] | None,
) -> bool:
    if requirement_requests_path_sweep(
        None,
        requirement_text=requirement_text,
        semantics=semantics,
    ):
        return True
    if _history_contains_action_type(
        action_history,
        "sweep",
    ):
        return True
    return False


def _history_contains_action_type(
    action_history: list[dict[str, Any]] | None,
    action_type: str,
) -> bool:
    target = str(action_type).strip().lower()
    return any(
        isinstance(item, dict)
        and str(item.get("action_type", "")).strip().lower() == target
        for item in (action_history or [])
    )


def _requirement_suggests_annular_focus(requirement_text: str) -> bool:
    text = str(requirement_text or "").strip().lower()
    if not text:
        return False
    annular_tokens = (
        "concentric",
        "outer diameter",
        "inner diameter",
        "inner hole",
        "washer",
        "annular",
        "ring",
    )
    return any(token in text for token in annular_tokens)


def _expected_path_segment_types(requirement_text: str | None) -> list[str]:
    text = str(requirement_text or "").strip().lower()
    if not text:
        return []
    if "line-arc-line" in text or "line arc line" in text:
        return ["line", "tangent_arc", "line"]
    if "l-shaped" in text or "l shaped" in text or "elbow" in text or "bent pipe" in text:
        return ["line", "tangent_arc", "line"]
    if "arc" in text and "line" in text:
        return ["line", "tangent_arc", "line"]
    return []


def _extract_sweep_path_requirement_spec(requirement_text: str | None) -> dict[str, Any]:
    text = str(requirement_text or "").strip().lower()
    if not text:
        return {}
    line_lengths = [
        float(match.group("value"))
        for match in re.finditer(
            r"(?P<value>\d+(?:\.\d+)?)\s*mm\s+(?:horizontal|vertical|tangent\s+straight|straight|tangent)?\s*line\b",
            text,
        )
    ]
    angle_match = re.search(
        r"(?P<value>\d+(?:\.\d+)?)\s*(?:-| )?degree\s+tangent\s+arc\b",
        text,
    )
    radius_match = re.search(
        r"radius(?:\s+of)?\s+(?P<value>\d+(?:\.\d+)?)\s*mm",
        text,
    )
    spec: dict[str, Any] = {
        "segment_types": _expected_path_segment_types(requirement_text),
        "line_lengths": line_lengths,
    }
    if angle_match is not None:
        spec["arc_angle_degrees"] = float(angle_match.group("value"))
    if radius_match is not None:
        spec["arc_radius"] = float(radius_match.group("value"))
    return spec


def _extract_annular_requirement_spec(requirement_text: str | None) -> dict[str, Any]:
    text = str(requirement_text or "").strip().lower()
    if not text:
        return {}

    def _capture(pattern: str) -> float | None:
        match = re.search(pattern, text)
        if match is None:
            return None
        return float(match.group("value"))

    outer_diameter = _capture(
        r"outer\s+diameter(?:\s+of)?\s+(?P<value>\d+(?:\.\d+)?)\s*mm"
    )
    inner_diameter = _capture(
        r"inner\s+(?:diameter|hole)(?:\s+of)?\s+(?P<value>\d+(?:\.\d+)?)\s*mm"
    )
    outer_radius = _capture(
        r"outer\s+radius(?:\s+of)?\s+(?P<value>\d+(?:\.\d+)?)\s*mm"
    )
    inner_radius = _capture(
        r"inner\s+radius(?:\s+of)?\s+(?P<value>\d+(?:\.\d+)?)\s*mm"
    )
    wall_thickness = _capture(
        r"wall\s+thickness(?:\s+of)?\s+(?P<value>\d+(?:\.\d+)?)\s*mm"
    )
    if outer_radius is None and outer_diameter is not None:
        outer_radius = outer_diameter / 2.0
    if inner_radius is None and inner_diameter is not None:
        inner_radius = inner_diameter / 2.0
    if (
        inner_radius is None
        and outer_radius is not None
        and wall_thickness is not None
    ):
        inner_radius = max(0.0, outer_radius - wall_thickness)

    spec: dict[str, Any] = {}
    if outer_radius is not None:
        spec["outer_radius"] = float(outer_radius)
    if inner_radius is not None:
        spec["inner_radius"] = float(inner_radius)
    if wall_thickness is not None:
        spec["wall_thickness"] = float(wall_thickness)
    if outer_radius is not None:
        area = math.pi * float(outer_radius) ** 2
        if inner_radius is not None and inner_radius > 0.0:
            area -= math.pi * float(inner_radius) ** 2
        spec["estimated_area"] = max(0.0, area)
    return spec


def _numeric_match_score(
    observed: float | None,
    target: float | None,
    *,
    absolute_tolerance: float,
    relative_tolerance: float,
) -> float | None:
    if observed is None or target is None:
        return None
    tolerance = max(float(absolute_tolerance), abs(float(target)) * float(relative_tolerance))
    if tolerance <= 1e-9:
        return 1.0 if abs(float(observed) - float(target)) <= 1e-9 else 0.0
    error = abs(float(observed) - float(target))
    if error <= tolerance:
        return max(0.85, 1.0 - (error / max(tolerance * 4.0, 1e-9)))
    return max(0.0, 0.85 - ((error - tolerance) / max(tolerance * 2.0, 1e-9)))


def _numeric_match_passes(
    observed: float | None,
    target: float | None,
    *,
    absolute_tolerance: float,
    relative_tolerance: float,
) -> bool:
    if observed is None or target is None:
        return False
    tolerance = max(float(absolute_tolerance), abs(float(target)) * float(relative_tolerance))
    return abs(float(observed) - float(target)) <= tolerance


def _relation_index(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    relation_index = payload.get("relation_index")
    return relation_index if isinstance(relation_index, dict) else None


def _sketch_state(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    sketch_state = payload.get("sketch_state")
    return sketch_state if isinstance(sketch_state, dict) else None


def _primary_path(query_sketch: dict[str, Any] | None) -> dict[str, Any] | None:
    sketch_state = _sketch_state(query_sketch)
    if not isinstance(sketch_state, dict):
        return None
    paths = sketch_state.get("paths")
    if not isinstance(paths, list):
        return None
    for item in paths:
        if isinstance(item, dict):
            return item
    return None


def _primary_profile(query_sketch: dict[str, Any] | None) -> dict[str, Any] | None:
    sketch_state = _sketch_state(query_sketch)
    if not isinstance(sketch_state, dict):
        return None
    profiles = sketch_state.get("profiles")
    if not isinstance(profiles, list):
        return None
    attached_profiles = [
        item
        for item in profiles
        if isinstance(item, dict) and str(item.get("attached_path_ref", "")).strip()
    ]
    if attached_profiles:
        return attached_profiles[0]
    for item in profiles:
        if isinstance(item, dict):
            return item
    return None


def _relation_ids_for_prefix(
    relation_index: dict[str, Any] | None,
    *,
    relation_types: set[str],
    prefix: str,
) -> list[str]:
    if not prefix:
        return []
    ids: list[str] = []
    for item in _relation_items(relation_index):
        relation_type = str(item.get("relation_type", "")).strip()
        if relation_type not in relation_types:
            continue
        values = [
            str(item.get("lhs", "")).strip(),
            str(item.get("rhs", "")).strip(),
            *[
                str(member).strip()
                for member in (item.get("members") or [])
                if isinstance(member, str)
            ],
        ]
        if any(value.startswith(prefix) for value in values if value):
            relation_id = str(item.get("relation_id", "")).strip()
            if relation_id:
                ids.append(relation_id)
    return list(dict.fromkeys(ids))


def _relation_ids_for_types(
    relation_index: dict[str, Any] | None,
    *,
    relation_types: set[str],
) -> list[str]:
    ids: list[str] = []
    for item in _relation_items(relation_index):
        relation_type = str(item.get("relation_type", "")).strip()
        if relation_type not in relation_types:
            continue
        relation_id = str(item.get("relation_id", "")).strip()
        if relation_id:
            ids.append(relation_id)
    return list(dict.fromkeys(ids))


def _group_ids_for_types(
    relation_index: dict[str, Any] | None,
    *,
    group_types: set[str],
) -> list[str]:
    ids: list[str] = []
    for item in _group_items(relation_index):
        group_type = str(item.get("group_type", "")).strip()
        if group_type not in group_types:
            continue
        group_id = str(item.get("group_id", "")).strip()
        if group_id:
            ids.append(group_id)
    return list(dict.fromkeys(ids))


def _best_group(
    relation_index: dict[str, Any] | None,
    *,
    group_types: set[str],
    expected_outer_radius: float | None,
    expected_inner_radius: float | None,
) -> dict[str, Any] | None:
    candidates = [
        item
        for item in _group_items(relation_index)
        if str(item.get("group_type", "")).strip() in group_types
    ]
    if not candidates:
        return None
    if expected_outer_radius is None and expected_inner_radius is None:
        preferred = sorted(
            candidates,
            key=lambda item: 0
            if str(item.get("group_type", "")).strip() == "annular_edge_pair"
            else 1,
        )
        return preferred[0]

    def _score(item: dict[str, Any]) -> tuple[float, int]:
        derived = item.get("derived") if isinstance(item.get("derived"), dict) else {}
        outer_radius = derived.get("outer_radius")
        inner_radius = derived.get("inner_radius")
        total = 0.0
        if isinstance(expected_outer_radius, (int, float)):
            total += abs(float(outer_radius or 0.0) - float(expected_outer_radius))
        if isinstance(expected_inner_radius, (int, float)):
            total += abs(float(inner_radius or 0.0) - float(expected_inner_radius))
        edge_penalty = 0 if str(item.get("group_type", "")).strip() == "annular_edge_pair" else 1
        return total, edge_penalty

    return min(candidates, key=_score)


def _supporting_relation_ids_for_group(
    *,
    relation_index: dict[str, Any] | None,
    group: dict[str, Any],
    relation_types: set[str],
) -> list[str]:
    members = {
        str(member).strip()
        for member in (group.get("members") or [])
        if isinstance(member, str) and member.strip()
    }
    ids: list[str] = []
    for item in _relation_items(relation_index):
        relation_type = str(item.get("relation_type", "")).strip()
        if relation_type not in relation_types:
            continue
        lhs = str(item.get("lhs", "")).strip()
        rhs = str(item.get("rhs", "")).strip()
        related_members = {
            lhs,
            rhs,
            *[
                str(member).strip()
                for member in (item.get("members") or [])
                if isinstance(member, str)
            ],
        }
        if members.intersection({member for member in related_members if member}):
            relation_id = str(item.get("relation_id", "")).strip()
            if relation_id:
                ids.append(relation_id)
    return list(dict.fromkeys(ids))


def _relation_items(relation_index: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(relation_index, dict):
        return []
    items = relation_index.get("relations")
    return [item for item in items if isinstance(item, dict)] if isinstance(items, list) else []


def _group_items(relation_index: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(relation_index, dict):
        return []
    items = relation_index.get("relation_groups")
    return [item for item in items if isinstance(item, dict)] if isinstance(items, list) else []
