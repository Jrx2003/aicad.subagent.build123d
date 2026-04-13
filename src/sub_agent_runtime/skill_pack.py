from __future__ import annotations

import json
import re
from typing import Any

from common.blocker_taxonomy import (
    taxonomy_family_ids_from_validation_payload,
    taxonomy_records_from_validation_payload,
)
from sandbox_mcp_server.registry import (
    analyze_requirement_semantics,
    infer_requirement_probe_families,
)


def _kernel_validation_assessment(
    domain_kernel_digest: dict[str, Any] | None,
) -> dict[str, Any]:
    if not isinstance(domain_kernel_digest, dict):
        return {}
    assessment = domain_kernel_digest.get("latest_validation_assessment")
    return assessment if isinstance(assessment, dict) else {}


def _validation_has_insufficient_evidence_guidance(
    latest_validation: dict[str, Any] | None,
    *,
    domain_kernel_digest: dict[str, Any] | None = None,
) -> bool:
    kernel_assessment = _kernel_validation_assessment(domain_kernel_digest)
    assessment_tags = {
        str(tag).strip().lower()
        for tag in (kernel_assessment.get("observation_tags") or [])
        if isinstance(tag, str) and str(tag).strip()
    }
    assessment_hints = {
        str(hint).strip().lower()
        for hint in (kernel_assessment.get("decision_hints") or [])
        if isinstance(hint, str) and str(hint).strip()
    }
    if (
        bool(kernel_assessment.get("insufficient_evidence"))
        or "insufficient_evidence" in assessment_tags
        or "inspect_more_evidence" in assessment_hints
    ):
        return True
    if not isinstance(latest_validation, dict):
        return False
    top_level_tags = {
        str(tag).strip().lower()
        for tag in (latest_validation.get("observation_tags") or [])
        if isinstance(tag, str) and str(tag).strip()
    }
    top_level_hints = {
        str(hint).strip().lower()
        for hint in (latest_validation.get("decision_hints") or [])
        if isinstance(hint, str) and str(hint).strip()
    }
    if (
        bool(latest_validation.get("insufficient_evidence"))
        or "insufficient_evidence" in top_level_tags
        or "inspect_more_evidence" in top_level_hints
    ):
        return True
    for record in taxonomy_records_from_validation_payload(latest_validation):
        observation_tags = {
            str(tag).strip().lower()
            for tag in getattr(record, "observation_tags", [])
            if isinstance(tag, str) and str(tag).strip()
        }
        decision_hints = {
            str(hint).strip().lower()
            for hint in getattr(record, "decision_hints", [])
            if isinstance(hint, str) and str(hint).strip()
        }
        if (
            "insufficient_evidence" in observation_tags
            or "inspect_more_evidence" in decision_hints
            or str(getattr(record, "recommended_repair_lane", "") or "").strip().lower()
            == "inspect_more_evidence"
        ):
            return True
    return False


def _failure_lint_ids(previous_tool_failure_summary: dict[str, Any] | None) -> set[str]:
    if not isinstance(previous_tool_failure_summary, dict):
        return set()
    lint_hits = previous_tool_failure_summary.get("lint_hits")
    if not isinstance(lint_hits, list):
        return set()
    return {
        str(item.get("rule_id") or "").strip()
        for item in lint_hits
        if isinstance(item, dict) and str(item.get("rule_id") or "").strip()
    }


def _skill_pack_prefers_code_first(
    *,
    requirement_lower: str,
    semantics: Any,
    latest_validation: dict[str, Any] | None,
    domain_kernel_digest: dict[str, Any] | None,
) -> bool:
    kernel_assessment = _kernel_validation_assessment(domain_kernel_digest)
    latest_patch_repair_mode = str(
        (domain_kernel_digest or {}).get("latest_patch_repair_mode") or ""
    ).strip()
    latest_packet_repair_mode = str(
        (domain_kernel_digest or {}).get("latest_repair_packet_repair_mode") or ""
    ).strip()
    if latest_patch_repair_mode in {"whole_part_rebuild", "subtree_rebuild"}:
        return True
    if latest_packet_repair_mode and latest_packet_repair_mode != "local_edit":
        return True
    if kernel_assessment and kernel_assessment.get("contradicted_clause_ids"):
        return True
    taxonomy_families = set(taxonomy_family_ids_from_validation_payload(latest_validation))
    if taxonomy_families.intersection(
        {
            "annular_groove",
            "axisymmetric_profile",
            "nested_hollow_section",
            "orthogonal_union",
            "path_sweep",
            "spherical_recess",
            "pattern_distribution",
        }
    ):
        return True
    return (
        bool(getattr(semantics, "mentions_revolved_groove_cut", False))
        or bool(getattr(semantics, "mentions_nested_profile_cutout", False))
        or bool(getattr(semantics, "mentions_profile_region_frame", False))
        or bool(getattr(semantics, "mentions_multi_plane_additive_union", False))
        or bool(getattr(semantics, "mentions_spherical_recess", False))
        or bool(getattr(semantics, "mentions_pattern", False))
        or _requirement_mentions_explicit_path_sweep(requirement_lower)
        or "hollow section" in requirement_lower
        or "inner void" in requirement_lower
        or "axisymmetric" in requirement_lower
        or "shaft" in requirement_lower
        or "stud" in requirement_lower
        or "union" in requirement_lower
        or "orthogonal" in requirement_lower
        or _requirement_mentions_half_shell_with_split_surface(requirement_lower)
    )


def requirement_prefers_code_first_family(
    *,
    requirements: dict[str, Any],
    latest_validation: dict[str, Any] | None = None,
    domain_kernel_digest: dict[str, Any] | None = None,
) -> bool:
    requirement_text = _requirements_text(requirements)
    requirement_lower = requirement_text.lower()
    semantics = analyze_requirement_semantics(requirements, requirement_text)
    return _skill_pack_prefers_code_first(
        requirement_lower=requirement_lower,
        semantics=semantics,
        latest_validation=latest_validation,
        domain_kernel_digest=domain_kernel_digest,
    )


def recommended_feature_probe_families(
    *,
    requirements: dict[str, Any],
    latest_validation: dict[str, Any] | None = None,
    domain_kernel_digest: dict[str, Any] | None = None,
) -> list[str]:
    requirement_text = _requirements_text(requirements)
    requirement_lower = requirement_text.lower()
    semantics = analyze_requirement_semantics(requirements, requirement_text)
    blockers = {
        str(item)
        for item in (latest_validation or {}).get("blockers", [])
        if isinstance(item, str)
    }
    taxonomy_families = taxonomy_family_ids_from_validation_payload(latest_validation)
    taxonomy_present = bool(taxonomy_families)
    families: list[str] = []

    def _append(raw_family_id: Any) -> None:
        family_id = str(raw_family_id or "").strip()
        if family_id:
            families.append(family_id)

    active_feature_instances = (
        (domain_kernel_digest or {}).get("active_feature_instances")
        if isinstance(domain_kernel_digest, dict)
        else None
    )
    if isinstance(active_feature_instances, list):
        for item in active_feature_instances:
            if isinstance(item, dict):
                _append(item.get("family_id"))
    _append((domain_kernel_digest or {}).get("latest_repair_packet_family_id"))
    latest_patch_feature_instances = (
        (domain_kernel_digest or {}).get("latest_patch_feature_instances")
        if isinstance(domain_kernel_digest, dict)
        else None
    )
    if isinstance(latest_patch_feature_instances, list):
        for item in latest_patch_feature_instances:
            if isinstance(item, dict):
                _append(item.get("family_id"))

    families.extend(taxonomy_families)
    families.extend(
        infer_requirement_probe_families(
            requirements=requirements,
            requirement_text=requirement_text,
            semantics=semantics,
        )
    )
    if (
        bool(getattr(semantics, "mentions_revolved_groove_cut", False))
        or (
            not taxonomy_present
            and any("annular" in item or "revolve" in item for item in blockers)
        )
    ):
        families.extend(["annular_groove", "axisymmetric_profile"])
    if _requirement_suggests_mixed_nested_section(
        requirement_lower=requirement_lower,
        blockers=blockers,
    ):
        families.append("nested_hollow_section")
    if bool(getattr(semantics, "mentions_spherical_recess", False)):
        families.append("spherical_recess")
    if bool(getattr(semantics, "mentions_pattern", False)):
        families.append("pattern_distribution")
    if "path_sweep" in taxonomy_families or _requirement_mentions_explicit_path_sweep(
        requirement_lower
    ):
        families.append("path_sweep")
    deduped: list[str] = []
    seen: set[str] = set()
    for family in families:
        if family in seen:
            continue
        seen.add(family)
        deduped.append(family)
    return deduped


def build_runtime_skill_pack(
    *,
    requirements: dict[str, Any],
    latest_validation: dict[str, Any] | None,
    latest_write_health: dict[str, Any] | None,
    previous_tool_failure_summary: dict[str, Any] | None = None,
    domain_kernel_digest: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    requirement_text = _requirements_text(requirements)
    requirement_lower = requirement_text.lower()
    semantics = analyze_requirement_semantics(requirements, requirement_text)
    blockers = {
        str(item)
        for item in (latest_validation or {}).get("blockers", [])
        if isinstance(item, str)
    }
    taxonomy_families = set(
        taxonomy_family_ids_from_validation_payload(latest_validation)
    )
    annular_blockers = {
        "feature_annular_groove",
        "feature_revolved_groove_setup",
        "feature_revolved_groove_alignment",
        "feature_revolved_groove_result",
    }
    annular_blockers_active = bool(blockers.intersection(annular_blockers))
    invalid_signals = {
        str(item)
        for item in (latest_write_health or {}).get("invalid_signals", [])
        if isinstance(item, str)
    }
    latest_tool = str((latest_write_health or {}).get("tool") or "").strip().lower()
    if not latest_tool:
        latest_tool = str(
            (previous_tool_failure_summary or {}).get("tool") or ""
        ).strip().lower()
    previous_failure_kind = str(
        (previous_tool_failure_summary or {}).get("effective_failure_kind")
        or (previous_tool_failure_summary or {}).get("failure_kind")
        or ""
    ).strip()
    previous_failure_lint_ids = _failure_lint_ids(previous_tool_failure_summary)
    same_tool_failure_count = int(
        (previous_tool_failure_summary or {}).get("same_tool_failure_count") or 0
    )
    insufficient_evidence_guidance = _validation_has_insufficient_evidence_guidance(
        latest_validation,
        domain_kernel_digest=domain_kernel_digest,
    )
    code_first_family = requirement_prefers_code_first_family(
        requirements=requirements,
        latest_validation=latest_validation,
        domain_kernel_digest=domain_kernel_digest,
    )

    skills: list[dict[str, Any]] = []

    skills.append(
        {
            "skill_id": "execute_build123d_minimal_script_hygiene",
            "when_relevant": "Use whenever you write or repair execute_build123d code.",
            "guidance": [
                "Keep execute_build123d scripts minimal and builder-first: use BuildPart for the host solid, BuildSketch for sections, BuildLine for rails, and assign the final geometry explicitly to result.",
                "Do not add print statements, f-strings, or temporary string-formatting diagnostics unless the runtime explicitly asks for them; they increase syntax-risk without improving the benchmark feedback loop.",
                "Prefer short named constants plus explicit Plane, Axis, Pos, Rot, and Locations placement over clever inline helpers or implicit origin assumptions.",
                "Keep primitive signatures literal: for boxes use `Box(length, width, height)` or the matching keyword names, and do not invent aliases such as `depth=`.",
                "Remember that `Box(length, width, height)` is centered at the origin by default; on a centered box the top-face plane is at `+height/2`, not `+height`.",
                "If the requirement explicitly says to sketch on `Plane.XY` and extrude upward, preserve that sketch-plus-positive-extrude contract instead of silently swapping in a centered `Box(...)` whose base no longer sits on the named plane.",
                "For shelled bodies, stay on Build123d shell/offset semantics or an explicit inner-solid subtraction; do not invent a bare `shell(...)` helper.",
                "For boolean cuts, build explicit solid cutters and combine them with supported solid booleans or builder subtractive modes; do not invent bare `subtract(...)` or bare `rotate(...)` helpers.",
                "When filtering ShapeLists by axis direction, use `filter_by(Axis.X/Y/Z)` or an explicit predicate; there is no `filter_by_direction(...)` helper.",
                "For axis-parallel selection, do not call `edge.is_parallel(Axis.Y)` or similar guessed edge-instance helpers; filter the source ShapeList with `edges.filter_by(Axis.Y)` or use an explicit predicate.",
                "If you close a `BuildLine` wire and need a face from it, use lowercase `make_face()`; do not invent `MakeFace()`.",
                "For Build123d countersinks, use the exact helper/keyword contract `CounterSinkHole(radius=..., counter_sink_radius=..., depth=..., counter_sink_angle=...)`; do not invent `CountersinkHole(...)`, `CounterSink(...)`, or `countersink_radius=` aliases.",
                "`CounterSinkHole(...)` belongs in `BuildPart`, not `BuildSketch`; if the requirement names a top/front/side host face, place the hole tool on that actual face plane instead of leaving it on the default XY mid-plane.",
                "`Plane.rotated(rotation, ordering=...)` only changes orientation; it does not relocate the workplane.",
                "The plane origin stays where it was after `Plane.rotated(...)`; if you need translation, use `Plane.offset(...)` along the plane normal or place the feature/cutter with `Pos(...)`.",
                "Do not instantiate a detached `Cylinder(...)` cutter inside an active `BuildPart` and then do `result = part.part - cutter`; that primitive is already added to the builder. Build the host in one `BuildPart`, close it, then create the cutter outside the active builder before the explicit boolean.",
                "Every primitive constructor inside an active `BuildPart` mutates that host immediately. Do not use temporary solid arithmetic staging values there such as `outer_cyl = Cylinder(...)`, `inner_cyl = Cylinder(...)`, or `half_space_box = Box(...)` and then reuse them in later boolean/intersection expressions; close the host builder before doing explicit solid arithmetic, or encode the shape through one builder-native sketch/profile recipe.",
                "If you truly need a temporary staging solid inside an active `BuildPart`, create it with `mode=Mode.PRIVATE` so it stays out of the host until the later explicit boolean.",
                "Do not assign back into `part.solid`; inside `BuildPart` prefer builder-native subtraction, and if you need an explicit boolean after the builder, subtract from `part.part` instead.",
                "Do not open a nested `BuildPart()` cutter inside an active `BuildPart` and then mutate `part.part -= cutter.part`; repeated placements can collapse into one origin-centered boolean instead of preserving the intended feature locations.",
            ],
        }
    )

    if latest_tool == "execute_build123d" and previous_failure_lint_ids:
        skills.append(
            {
                "skill_id": "execute_build123d_api_lint_repair_first",
                "when_relevant": "Use when preflight lint already identified a concrete unsupported Build123d API or keyword surface.",
                "guidance": [
                    "Treat lint hits as authoritative repair targets; do not retry the same execute_build123d pattern unchanged.",
                    "If a repair_recipe is available in previous_tool_failure_summary, follow that recipe before opening new generic read turns.",
                    "Keep the next write materially simpler than the rejected one and stay on supported builder-first Build123d surfaces.",
                ],
            }
        )

    if insufficient_evidence_guidance:
        skills.append(
            {
                "skill_id": "insufficient_evidence_query_before_repair",
                "when_relevant": "Use when validation explicitly says evidence is insufficient or the blocker taxonomy carries evidence-gap hints.",
                "guidance": [
                    "Treat insufficient_evidence observation_tags as a stop signal for family-specific repair guidance.",
                    "Query more evidence first with query_feature_probes, query_geometry, or query_kernel_state before rewriting the geometry.",
                    "If the validation record already includes decision_hints such as inspect_more_evidence, follow that evidence-gathering lane before another repair attempt.",
                ],
            }
        )

    if code_first_family:
        skills.append(
            {
                "skill_id": "code_first_global_family_bias",
                "when_relevant": "Use when the requirement is global, family-driven, or likely to need several structured writes before a stable solid exists.",
                "guidance": [
                    "Prefer execute_build123d as the first write for whole-part or subtree construction.",
                    "Builder-first default: BuildPart for host solids, BuildSketch for section profiles, and BuildLine for rails before adding local finishing.",
                    "Use apply_cad_action only when a stable local face/edge/sketch anchor already exists and the local edit is clearly cheaper than a rebuild.",
                    "After a successful code write, prefer query_feature_probes or query_geometry before broad repeated topology/validation loops.",
                    "If standard read tools still leave one geometric-family question unresolved, execute_build123d_probe is the next diagnostic tool instead of another blind rewrite.",
                ],
            }
        )

    if "feature_target_face_additive_merge" in blockers:
        skills.append(
            {
                "skill_id": "whole_part_additive_features_must_merge_into_single_body",
                "when_relevant": "Use when a whole-part build added bosses/studs/pads on a target face but validation reports they did not stay merged into one solid.",
                "guidance": [
                    "Treat additive face features as part of the same body, not as separate result solids.",
                    "Prefer building repeated bosses/studs from the target face workplane of the base solid, or fuse them back into the base and verify the final solid count is 1 before finishing.",
                    "After a code-first rebuild with additive face features, check that the resulting snapshot has one merged solid rather than multiple disconnected solids.",
                ],
            }
        )

    positive_extrude_plane = _detect_positive_extrude_plane(requirement_lower)
    if (
        positive_extrude_plane is not None
        and not _requirement_requests_centered_plane_pose(requirement_lower)
    ):
        plane_name, axis_name = positive_extrude_plane
        skills.append(
            {
                "skill_id": "positive_extrude_from_named_plane_is_not_centered",
                "when_relevant": "Use when a requirement sketches on a named datum plane and then extrudes by a positive distance.",
                "guidance": [
                    f"If the requirement says sketch on the {plane_name} plane and extrude by a positive distance, the default solid spans positive {axis_name} from that plane rather than being centered about {axis_name}=0.",
                    "In Build123d, keep the sketch on the named Plane and extrude it in the positive normal direction rather than simulating the pose with a centered primitive by default.",
                    "If you use a box primitive for convenience, place it explicitly with Plane/Pos so the requested lower bound stays on the named datum plane instead of drifting around the global origin.",
                    "Only use center-aligned or both-sides/midplane semantics when the requirement explicitly says centered, symmetric, or about the plane.",
                ],
            }
        )

    bottom_aligned_box_pose = _detect_named_plane_bottom_aligned_box_pose(
        requirement_lower
    )
    if (
        bottom_aligned_box_pose is not None
        and not _requirement_requests_centered_plane_pose(requirement_lower)
    ):
        plane_name, axis_name = bottom_aligned_box_pose
        skills.append(
            {
                "skill_id": "named_plane_box_bottom_pose_alignment",
                "when_relevant": "Use when a requirement selects a named datum plane, describes a box/base primitive, and only pins the normal-direction lower bound such as bottom on Z=0.",
                "guidance": [
                    f"If the requirement says select the {plane_name} plane and create a box/base with the bottom on {axis_name}=0, only the {axis_name} lower bound is pinned; do not also shift the in-plane footprint into the positive quadrant unless the prompt explicitly gives a corner/origin anchor.",
                    f"For this pose, prefer an explicit centered-in-plane placement such as `Pos(0, 0, height / 2) * Box(...)` or `with Locations(Pos(...)):` so the box keeps its X/Y footprint centered while the bottom stays on {axis_name}=0, instead of drifting into a centered=False-style positive-quadrant pose.",
                    "After the host solid is placed correctly, do later top-face or side-face local edits from the selected face workplane rather than compensating with ad-hoc whole-part translations.",
                ],
            }
        )

    positive_extrude_mismatch = _detect_positive_extrude_bbox_mismatch(
        requirement_lower=requirement_lower,
        latest_write_health=latest_write_health,
    )
    if positive_extrude_mismatch is not None:
        plane_name, axis_name, expected_range, current_range = positive_extrude_mismatch
        skills.append(
            {
                "skill_id": "positive_extrude_bbox_alignment_repair",
                "when_relevant": "Use when the current solid is centered about the datum plane but the requirement implies a positive extrusion from that plane.",
                "guidance": [
                    f"The current solid spans {axis_name}={current_range[0]:.3f}..{current_range[1]:.3f}, but a sketch on the {plane_name} plane extruded by the requested distance should span approximately {axis_name}={expected_range[0]:.3f}..{expected_range[1]:.3f}.",
                    "Center-rectangle wording applies to the sketch in the plane, not to centering the extrusion about the plane normal.",
                    "Repair by sketching on the named Plane and extruding in the positive normal direction; do not simulate the pose with a vague centered primitive.",
                    "If you keep a primitive-based whole-part rebuild, place the host explicitly with Plane/Pos before downstream cuts.",
                ],
            }
        )

    if _requirement_mentions_shelled_host_with_named_face_feature(
        requirement_lower,
        semantics=semantics,
    ):
        skills.append(
            {
                "skill_id": "shelled_host_preserves_named_feature_face",
                "when_relevant": "Use when a shelled body also needs a named-face recess, hole set, or other local feature on that same host.",
                "guidance": [
                    "If a shelled body will later receive a named-face local feature, do not open or remove that same target face while creating the shell.",
                    "When the opening face is unspecified, preserve the named feature face and open the opposite face by default.",
                    "For explicit inner-solid subtraction shells, keep the inner cutout extent and offset chosen so the target face still has material for the later edit.",
                    "For vague reference layouts on a shelled host, keep the recesses, holes, or reference pattern on surviving host material instead of placing them in the hollow void.",
                ],
            }
        )

    if "feature_named_plane_positive_extrude_span" in blockers:
        skills.append(
            {
                "skill_id": "named_plane_positive_extrude_span_blocker_repair",
                "when_relevant": "Use when validation says the solid drifted into a centered pose instead of preserving a positive extrude span from the named plane.",
                "guidance": [
                    "Treat this as a pose bug, not as a generic local-feature bug: the base solid must preserve the datum-plane lower bound before later holes, pockets, or fillets are applied.",
                    "Build123d sketch-plus-extrude already gives the plane-anchored positive span when the sketch stays on the named Plane.",
                    "If a whole-part primitive is clearer, place it explicitly so the named-plane lower bound is preserved before later operations.",
                ],
            }
        )

    axisymmetric_axis_mismatch = _detect_named_axis_axisymmetric_pose_mismatch(
        requirement_lower=requirement_lower,
        latest_write_health=latest_write_health,
    )
    if axisymmetric_axis_mismatch is not None:
        axis_name, perpendicular_axes, bbox_offsets, current_center = axisymmetric_axis_mismatch
        skills.append(
            {
                "skill_id": "named_axis_axisymmetric_pose_alignment_repair",
                "when_relevant": "Use when a revolve / shaft / axisymmetric whole-part rebuild drifted away from the declared global rotation axis.",
                "guidance": [
                    f"The requirement declares the global {axis_name}-axis as the rotation axis, so the final solid should stay centered on that axis rather than drifting along {perpendicular_axes[0]} / {perpendicular_axes[1]}.",
                    f"The current radial bbox center offsets are {bbox_offsets[0]:.3f} and {bbox_offsets[1]:.3f}, and the current center is {current_center}; that indicates the whole part is translated off the named axis.",
                    f"When rebuilding with cylinders/cones for a {axis_name}-axis part, keep every primitive centered on {perpendicular_axes[0]}={0.0} and {perpendicular_axes[1]}={0.0}; only translate along {axis_name} unless the requirement explicitly asks for an offset axis.",
                    "Before finishing, verify that the final bbox straddles the declared rotation axis and that any cylindrical/conical faces use that same axis.",
                ],
            }
        )

    if (
        ("blind hole" in requirement_lower or "hole" in requirement_lower)
        and _requirement_has_explicit_xy_coordinate_pair(requirement_text)
    ):
        skills.append(
            {
                "skill_id": "explicit_face_local_anchor_coordinates",
                "when_relevant": "Use when the requirement gives explicit local coordinates for a face feature.",
                "guidance": [
                    "Treat named local coordinates like (30, 0) as explicit feature anchors, not as optional hints.",
                    "On an XY-aligned top face, local sketch X/Y normally align with global X/Y; place the feature explicitly with Locations((x, y)) or an equivalent Plane/Pos mapping before hole/cut calls.",
                    "When the prompt says to draw points with coordinates on a rectangular host face or plate surface, keep that sketch frame literal; those coordinates may be corner-based within the face sketch rather than already centered around the body origin.",
                    "Do not rely on the default workplane origin for explicit hole centers or local anchor features.",
                ],
            }
        )

    if _requirement_prefers_named_face_local_feature_sequence(requirement_lower):
        guidance = [
            "Even for simple base-solid plus named-face local-feature sequences, default the first write to execute_build123d so the host solid and local feature land in one fresh geometry revision.",
            "For symmetric base spans, keep extrusion-local semantics literal: `symmetrically by N` means a final span of `2N`, so use `extrude(N, both=True)` or an equivalent global primitive with axis span `2N`, not `extrude(2N, both=True)`.",
            "Use apply_cad_action only after a successful code build has established a stable host solid and the remaining work is a bounded local finish.",
            "For the final local fillet/chamfer step, prefer query_topology plus explicit edge_refs once the target solid exists.",
            "If the final fillet/chamfer remains inside whole-part execute_build123d code, use stable selector chains grounded on face orientation and axis direction; avoid lambda-based edge predicates or ad-hoc selector logic.",
            'For directional edge classes such as bottom edges parallel to Y, prefer supported chained selectors like `.edges("<Z").edges("|Y")` over unsupported boolean-expression selectors.',
            'Do not use ad-hoc boolean-expression selectors such as `"<Z and (|X > 29.9)"` inside whole-part code-path fillet/chamfer targeting.',
            "Treat repeated structured bootstrap turns as a cost signal; do not reopen them when a clean code rebuild is cheaper.",
        ]
        if previous_failure_kind in {
            "execute_build123d_timeout",
            "execute_build123d_chain_context_failure",
            "execute_build123d_selector_failure",
        }:
            guidance.append(
                "A recent execute_build123d failure already indicates that another blind whole-part retry is high-risk here; bias toward a materially simpler staged rebuild or a local finish only after the host solid is stable."
            )
        skills.append(
            {
                "skill_id": "named_face_local_feature_sequence",
                "when_relevant": "Use when the requirement reads like base solid first, then a named-face local feature, then a local edge finish.",
                "guidance": guidance,
            }
        )

    if _requirement_prefers_nested_regular_polygon_frame(
        requirement_lower=requirement_lower,
        blockers=blockers,
    ):
        skills.append(
            {
                "skill_id": "nested_regular_polygon_frame_code_first",
                "when_relevant": "Use when the requirement is a concentric regular-polygon or equilateral-triangle frame section that should extrude as one hollow profile.",
                "guidance": [
                    "For concentric regular-polygon frame sections, strongly prefer one same-workplane nested-profile extrude: draw the outer and inner loops on the same workplane or sketch and extrude the frame region directly in one whole-part write.",
                    "Keep the outer and inner regular polygons centroid-aligned and orientation-aligned so the frame region is defined by one nested profile, not by later 3D subtraction.",
                    "Do not start with `.cut()`, `.cutThruAll()`, or another 3D boolean misuse on the first pass; those APIs are the wrong tool before a stable solid exists and often create chain-context failures here.",
                    "If the first whole-part build fails, repair the nested 2D profile construction itself rather than mixing a sketch chain with solid booleans in the next retry.",
                ],
            }
        )

    if _requirement_mentions_regular_polygon_side_length(requirement_lower):
        skills.append(
            {
                "skill_id": "regular_polygon_side_length_build123d_semantics",
                "when_relevant": "Use when a requirement names a regular polygon or equilateral triangle by side length, especially in a whole-part code rebuild.",
                "guidance": [
                    "Build123d regular-polygon sizing should stay explicit: use the true side-length-aware helper/parameter instead of silently reusing a circumradius-like value.",
                    "If the requirement gives side length s for an n-sided regular polygon, convert it deliberately before sketching; do not treat the same numeric value as a radius by default.",
                    "For concentric regular-polygon frame sections, compute both outer and inner polygon sizes from the stated side lengths and keep their centroids coincident; do not halve the scale by passing the wrong sizing mode into the sketch primitive.",
                ],
            }
        )

    if (
        ("shaft" in requirement_lower or "stud" in requirement_lower or "axial direction" in requirement_lower)
        and "radius" in requirement_lower
        and "length" in requirement_lower
    ):
        skills.append(
            {
                "skill_id": "axisymmetric_segmented_primitives_preferred_over_revolve",
                "when_relevant": "Use when an axisymmetric part is described as axial segments with explicit radii and lengths.",
                "guidance": [
                    "For stepped shafts and studs described as consecutive radius/length segments, prefer coaxial cylinders or cones merged along the main axis instead of a handwritten revolve profile on the first attempt.",
                    "Even if the requirement mentions revolve, keep the first whole-part code build on the lower-risk coaxial primitive path unless a primitive-based build has already proven insufficient.",
                    "Do not use cylinder(..., centered=False) for a named-axis-centered solid, because that shifts the primitive off the declared rotation axis before any later translation.",
                    "Reserve revolve for genuinely non-piecewise profiles where the 2D contour is easier to express than the axial primitives.",
                    "Keep the radial center on the declared axis and translate only along the main axis unless the requirement explicitly asks for an offset axis.",
                    "After a primitive-based axisymmetric rebuild, verify that the final bbox stays centered on the declared axis and spans the full requested axial range before another rewrite.",
                ],
            }
        )

    if _requirement_mentions_half_shell_with_split_surface(requirement_lower):
        skills.append(
            {
                "skill_id": "half_shell_profile_from_semicircle_section",
                "when_relevant": "Use when the requirement is a split bearing housing or other half-cylindrical shell with a flat split surface.",
                "guidance": [
                "Build the base shell from one closed semicircular or semi-annular 2D section on the named profile plane, then extrude it along the housing length.",
                "Do not start from a full cylinder and split it later; that frequently preserves the wrong full-diameter envelope.",
                "If the radii are already explicit, prefer the lower-risk same-builder cylinder-subtract-then-intersect recipe on the first pass: create the outer cylinder, subtract the inner cylinder with `mode=Mode.SUBTRACT`, then intersect or trim to the required half-plane before downstream pad/lug edits.",
                "Do not guess `Circle(..., arc_size=180)` for the semicircular section. In Build123d, `Circle(...)` is always full-circle geometry.",
                "`Semicircle(...)` is not a Build123d helper; if you need a true half-profile, use `CenterArc(...)` or `RadiusArc(...)` inside `BuildLine`, close the split edge, and convert it with `make_face()`.",
                "Treat the split surface as the flat closing edge of the semicircle profile, and keep the shell, pad, and lugs in the same half-plane as that semicircular material instead of extending them past the split line.",
                "For split-shell housings, the pad/lugs should widen the orthogonal axis along the split surface, not increase the split-axis depth beyond the outer radius.",
                "If the semicircle is drawn in the positive half-plane of the sketch, keep the pad/lugs in that same positive half-plane instead of mirroring them into the opposite half-plane.",
                "Treat the bore/clearance cut as a subtractive operation on the combined shell/pad host after those bodies are merged.",
                    "Run that merged-host bore cut so it will leave side lugs outside the bore instead of recreating a full-width pad or cutting the lugs away.",
                    "When the bore and lug holes are subtractive, keep those cutters in the same active `BuildPart` with supported subtractive modes instead of nesting cutter parts or calling bare `subtract(...)` helpers.",
                    "For Y-direction lug holes at explicit X/Z anchors, a safe whole-part pattern is `with Locations((x, 0, z)): Cylinder(radius, extent, rotation=(90, 0, 0), mode=Mode.SUBTRACT)` after the host solid already exists.",
                    "On the first whole-part write, expect one radial bbox span to stay near the outer radius while the orthogonal axis shell-plus-lug span stays near or above the outer diameter.",
                ],
            }
        )

    if _requirement_mentions_flange_boss_pattern_holes(requirement_lower):
        skills.append(
            {
                "skill_id": "flange_boss_pattern_hole_host_thickness",
                "when_relevant": "Use when a flange hosts a bottom boss, a central through-hole, and a separate patterned hole set that should only cut the flange.",
                "guidance": [
                    "Treat the central through-hole and the patterned bolt-circle holes as different depth rules on the same part family.",
                    "If the requirement says the pattern holes cut through the flange, the patterned bolt-circle holes belong to the flange host thickness only, not the full flange-plus-boss stack.",
                    "Open the pattern on the flange host face or flange annular face, keep the hole centers on that flange host, and do not retarget the pattern to the boss face.",
                    "Use a bounded subtractive depth equal to the flange thickness for the patterned holes; do not use cutThruAll() when that would continue through the boss.",
                    "Reserve the full-stack cut for the explicitly named central through-hole when the requirement says it passes through the entire solid.",
                ],
            }
        )

    if _requirement_mentions_explicit_path_sweep(requirement_lower) or (
        "path_sweep" in taxonomy_families
    ) or blockers.intersection(
        {
            "feature_path_sweep_rail",
            "feature_path_sweep_profile",
            "feature_path_sweep_frame",
            "feature_path_sweep_result",
        }
    ):
        skills.append(
            {
                "skill_id": "path_sweep_wire_profile_frame_repair",
                "when_relevant": "Use when the requirement explicitly defines a path sweep with a separate endpoint-attached profile sketch.",
                "guidance": [
                    "Treat the sweep rail and the section profile as separate artifacts: build one open connected path wire first, then build one closed profile face for the sweep section.",
                    "Do not collapse the rail and the profile into one sketch window, and do not continue to sweep if the rail is disconnected or the profile is still open.",
                    "If an artifactless execute_build123d failure already had a successful execute_build123d_probe that exposed concrete rail/profile/frame geometry, prefer the next execute_build123d repair immediately; only insert query_kernel_state or query_feature_probes when the probe still leaves the endpoint frame or family binding ambiguous.",
                    "Preserve the path endpoint frame once the profile sketch attaches there; do not overwrite that path endpoint frame with a generic front/top/side plane guess.",
                    "For path-attached profiles, use BuildLine for the rail, BuildSketch on the explicit endpoint Plane for the section, and keep the frame_mode=normal_to_path_tangent or equivalent Frenet endpoint frame before placing the section loops.",
                    "For hollow pipe/tube sweeps, make sure the closed profile face carries both outer and inner wires before the sweep so the section is a true annulus rather than two loose circles.",
                    "For hollow bent-pipe repairs, prefer an explicit BuildLine rail plus an annular BuildSketch section on the endpoint frame before sweeping the section along the path.",
                    "When the path includes a tangent elbow with an explicit radius, prefer one connected rail built from stable line/arc members instead of guessing a midpoint-driven arc recipe.",
                    "Do not repair this family with legacy Workplane-chain helpers or unsupported sweep shortcuts.",
                    "Keep execute_build123d_probe scripts measurement-oriented and minimal; avoid verbose debug print scaffolding that can fail before any geometry evidence is emitted.",
                ],
            }
        )

    if (
        "u-shaped" in requirement_lower
        or "u shape" in requirement_lower
        or "notch" in requirement_lower
        or "cut out a" in requirement_lower
    ) and "extrude" in requirement_lower:
        skills.append(
            {
                "skill_id": "requirement_driven_cross_section_profiles",
                "when_relevant": "Use when the requirement primarily defines a 2D profile on a named plane and then extrudes it.",
                "guidance": [
                    "Prefer one closed 2D cross-section on the named sketch plane, then extrude along the orthogonal axis.",
                    "If the requirement gives notch width, notch depth, wall height, or slot floor offsets, encode those directly in the profile-plane coordinates instead of approximating them with a later 3D subtractive box.",
                    "If a top-face slot is said to span the full part length and leave a U-shaped/channel section, treat that as a cross-section-first whole-profile build on the orthogonal plane rather than a box host plus a loosely aligned top-face cut.",
                    "Avoid top-face cutBlind box recipes for full-span channel sections unless you explicitly prove the local workplane is centered on the host and the slot truly occupies the requested full span.",
                    "When profile alignment matters more than feature history, a clean whole-profile rebuild is safer than another local boolean patch.",
                ],
            }
        )

    if (
        "union" in requirement_lower
        or "orthogonal" in requirement_lower
        or "two orthogonal" in requirement_lower
    ):
        skills.append(
            {
                "skill_id": "whole_part_union_from_global_axis_primitives",
                "when_relevant": "Use when the part is a small set of orthogonal bars/blocks that must intersect and union cleanly.",
                "guidance": [
                    "If an incremental sketch path is burning rounds, rebuild the whole part with a few global-axis solids and combine them directly.",
                    "For orthogonal unions, verify the required spans on each global axis after the rebuild instead of spending extra rounds inspecting stale sketch windows.",
                    "A whole-part write is preferred when the open sketch still needs multiple writes and the remaining round budget is tight.",
                ],
            }
        )

    if "feature_half_shell_profile_envelope" in blockers:
        skills.append(
            {
                "skill_id": "half_shell_profile_envelope_repair",
                "when_relevant": "Use when validation says the split-shell result kept a full-diameter body instead of a one-sided half-shell envelope.",
                "guidance": [
                    "Repair the base section itself instead of adding more inspection or trim stages.",
                    "Replace any full circle/full cylinder plus split workflow with a semicircle or semi-annulus closed along the split line, then extrude that section for the full housing length.",
                    "Keep the split surface flat and one-sided so the half-profile axis stays near the outer radius, not the full outer diameter.",
                    "Keep the pad and lugs in the same half-plane as the shell material so they widen the orthogonal axis instead of increasing split-axis depth.",
                    "If the current bbox grows because pad/lug material crossed the split line, move that pad/lug material back into the shell's half-plane rather than trimming the entire body.",
                    "After the envelope is corrected, run the bore/clearance cut through the merged shell-plus-pad body rather than only through one subfeature.",
                    "That bore/clearance cut through the merged shell-plus-pad body should leave only the outboard pad material as the two side lugs instead of a full-width bridge under the bore.",
                    "After the half-shell envelope is correct, rebuild the bottom pad/lugs and clearance cut as downstream features outside the bore.",
                ],
            }
        )

    if _requirement_uses_named_plane_symmetric_union(requirement_lower):
        skills.append(
            {
                "skill_id": "named_plane_profile_to_global_box_mapping",
                "when_relevant": "Use when a requirement defines two or more named-plane rectangles that are then extruded symmetrically and unioned.",
                "guidance": [
                    "For whole-part code rebuilds, convert each plane-local rectangle-plus-symmetric-extrude statement into one explicit global-axis box before the union.",
                    "Use these global box mappings: XY rectangle (w x h) with symmetric Z extrude d -> box(w, h, d); YZ rectangle (w x h) with symmetric X extrude d -> box(d, w, h); XZ rectangle (w x h) with symmetric Y extrude d -> box(w, d, h).",
                    "Prefer Build123d Box(global_x, global_y, global_z, align=(Align.CENTER, Align.CENTER, Align.CENTER)) for those whole-body primitives instead of re-encoding them through rotated sketch planes.",
                    "After the union, compare the final global bbox spans against the requirement before spending another read-only turn.",
                ],
            }
        )

    if _requirement_suggests_mixed_nested_section(
        requirement_lower=requirement_lower,
        blockers=blockers,
    ):
        skills.append(
            {
                "skill_id": "mixed_nested_profile_section_bootstrap",
                "when_relevant": "Use when the requirement defines two centered closed profiles where one lives inside the other before the first solid.",
                "guidance": [
                    "If the prompt says to draw multiple centered closed profiles and then extrude the section, treat that as a selected enclosed region, not as an automatic union of all wires.",
                    "The structured additive extrude tool is additive-only. Do not assume unsupported payloads such as mode=cut_hollow or shell-style extrude semantics will create the inner void for you.",
                    "For mixed-shape nested sections such as outer circle plus inner square/rectangle, prefer an explicit hollow/base-minus-inner construction if the tool surface cannot guarantee the intended interior region orientation.",
                    "Complete the whole pre-solid section before the first extrude. Do not extrude an empty sketch, and do not spend rounds validating before the hollow/frame intent is actually realized.",
                    "If a later groove, hole, or other local feature depends on that base, stabilize the hollow base first, then add the downstream feature in a second stage or a single whole-part code rebuild.",
                ],
            }
        )

    annular_requirement = (
        "annular groove" in requirement_lower
        or "revolved cut" in requirement_lower
        or ("groove" in requirement_lower and "revolve" in requirement_lower)
    )
    code_first_annular_path = annular_requirement and (
        code_first_family
        or latest_tool == "execute_build123d"
        or previous_failure_kind.startswith("execute_build123d")
    )

    if annular_requirement and not code_first_annular_path and latest_tool != "execute_build123d":
        skills.append(
            {
                "skill_id": "annular_groove_revolve_cut_recipe",
                "when_relevant": "Use when a local rectangular groove profile must be turned into a rotational subtractive cut with an explicit axis frame.",
                "guidance": [
                    "Use this recipe only when you are still on a structured local-tool path and the groove axis/workplane semantics are explicit.",
                    "Build the base solid first, then create a closed rectangular groove profile on an axis-containing plane such as XZ or YZ.",
                    "Use the rectangle so one dimension is radial depth and the other is axial height/location, then revolve it with a subtractive combine mode around the main axis instead of revolving a detached sheet and cutting later.",
                    "After the groove write, require solids>0 and an unchanged outer bbox before spending more read-only turns.",
                    "If repeated revolve-cut attempts keep failing, or if you are already repairing through execute_build123d, switch to a whole-part code rebuild and subtract an explicit annular groove band from the base solid instead of retrying another raw revolve.",
                ],
            }
        )

    if code_first_annular_path:
        skills.append(
            {
                "skill_id": "code_first_annular_band_subtraction",
                "when_relevant": "Use when an annular-groove requirement is on a code-first Build123d build or repair path.",
                "guidance": [
                    "Treat the base outer envelope as authoritative and realize the groove with one whole-part annular-band subtraction, not a raw sketch-plane revolve.",
                    "For the first whole-part code build, the default first-pass whole-part pattern should already be annular-band subtraction rather than a literal revolve-cut recreation.",
                    "Build the hollow base first, then construct an annular band on the same main axis using the outer radius, inner radius, and requested axial window, and subtract that band from the base.",
                    "For cylindrical parts, a typical pattern is: build the outer solid, subtract the inner void, then create a coaxial annular band whose axial span matches the requested groove height/window and cut it from the base.",
                    "Do not open a nested `BuildPart()` just to make the groove band while the host `BuildPart` is still active; either keep the groove subtraction in the same active `BuildPart` or close the host and subtract the annular groove band once.",
                    "Do not treat raw sketch-plane revolve as a co-equal repair recipe once execute_build123d is already the active repair path; only return to revolve if the axis/workplane semantics are explicitly proven and the band subtraction route is impossible.",
                    "After the repair write, verify the outer bbox stays stable while the local ring cut appears at the requested axial location.",
                ],
            }
        )

    if (
        "feature_hole_position_alignment" in blockers
        or "feature_local_anchor_alignment" in blockers
        or (
            "hole" in requirement_lower
            and (
                _requirement_has_explicit_xy_coordinate_pair(requirement_text)
                or _requirement_mentions_directional_drilling(requirement_lower)
            )
        )
    ):
        skills.append(
            {
                "skill_id": "positioned_holes_on_face_workplanes",
                "when_relevant": "Use for hole/recess features with explicit local coordinates or a stated drill direction, regardless of whether the next repair stays structured or switches to whole-part Build123d.",
                "guidance": [
                "On a face workplane, hole() at the workplane origin will place the feature at local (0, 0).",
                "For explicit coordinates, place the feature explicitly with Locations((x, y)), GridLocations(...), or a plane-local Pos transform instead of relying on implicit cursor state.",
                "Choose the workplane whose normal matches the requested drill direction: XY drills along Z, XZ drills along Y, and YZ drills along X.",
                "For XY-based top faces, local workplane X/Y usually match the requirement's X/Y coordinates directly; for XZ or YZ workplanes, remap the stated coordinates into that plane before drilling.",
                "If the requirement says the holes run in the Y direction and gives `x` plus `z` coordinates, use the XZ workplane so the local coordinates are `(x, z)` before drilling along Y.",
                "Use `Plane.offset(...)` only for plane-normal translation: `Plane.XY.offset(d)` shifts along Z, `Plane.XZ.offset(d)` shifts along Y, and `Plane.YZ.offset(d)` shifts along X.",
                "For Y-direction drilling on the XZ workplane, `Plane.XZ.offset(d)` shifts along Y, not Z, so do not encode a Z coordinate with `Plane.XZ.offset(z0)`.",
                "If the named workplane already has the correct normal for the drill direction, keep it as-is instead of calling `Plane.rotated(...)` again; `Plane.rotated(rotation, ordering=...)` changes orientation only and leaves the origin unchanged.",
                "If the host solid was created centered about the origin but the requirement's point coordinates came from a rectangular face sketch, translate those corner-based sketch coordinates into the centered host frame before placing the holes.",
                "For `CounterSinkHole(...)`, keep the operation in `BuildPart` and include the host-face plane translation in the placement itself, for example `Locations((x, y, top_z), ...)` on a centered top face.",
                "For repeated holes or countersinks, keep the cutters in the same active `BuildPart` with supported subtractive placement, or close the host builder before doing an explicit solid boolean; do not create a nested `BuildPart()` cutter at each location and mutate `part.part -= cutter.part` inside the loop.",
                "If the requirement names one center such as (30, 0), encode that coordinate explicitly in the Build123d geometry placement instead of relying on defaults.",
                ],
            }
        )

    centered_face_array_centers = _infer_centered_square_or_rectangular_array_centers(
        requirement_text
    )
    if len(centered_face_array_centers) >= 4 and bool(getattr(semantics, "mentions_pattern", False)):
        skills.append(
            {
                "skill_id": "explicit_centered_face_array_centers",
                "when_relevant": "Use when the requirement defines a centered square/rectangular face array by side length or per-axis offsets.",
                "guidance": [
                    "Treat the centered face-array layout as an explicit center set, not as a vague pattern hint.",
                    f"For this requirement the local centers are {centered_face_array_centers} (equivalently (±4, ±4) when the side length is 8 mm).",
                    "These centers are face-local coordinates around the host-face center/origin, not corner-based global offsets.",
                    "For default centered Rectangle(...) or Box(...) hosts, keep the centered array anchored around (0, 0) on that face unless the host was explicitly translated first.",
                    "On a face workplane, prefer one explicit pushPoints([...]) or rarray(...) layout over chained relative center(...) calls.",
                    "Do not derive later pattern members by repeatedly moving the current cursor with center(...); those moves are relative and often drift the full array into one quadrant.",
                    "After the write, the realized stud/hole/recess centers should still match the centered local layout before you consider the pattern complete.",
                ],
            }
        )

    if (
        bool(getattr(semantics, "mentions_spherical_recess", False))
        and bool(getattr(semantics, "mentions_pattern", False))
    ):
        skills.append(
            {
                "skill_id": "spherical_recess_pattern_code_first",
                "when_relevant": "Use when the requirement asks for repeated hemispherical/spherical recesses on a host face, especially on the first whole-part code build.",
                "guidance": [
                    "Treat this as a spherical-recess pattern family and prefer one whole-part sphere-subtraction build over a literal revolve recreation on the first pass.",
                    "Build the host solid first, identify the host face plane, and place the sphere centers on that host face plane when the prompt says the diameter edge coincides with the face.",
                    "For a hemispherical recess whose diameter edge lies on the top face, set `sphere_center_z = top_face_z`, not `top_face_z - radius`.",
                    "If the host comes from a default centered Rectangle(...) sketch or an origin-centered Box(...), the host-face center stays at local (0, 0); do not translate a centered pattern by (+width/2, +height/2).",
                    "Create spheres with Build123d solid primitives, place them at the explicit center set, then subtract them from the host body.",
                    "For the first pass, prefer one explicit builder recipe such as `with Locations((x, y, top_z), ...): Sphere(radius=..., mode=Mode.SUBTRACT)` inside the same `BuildPart`.",
                    "Enumerate the full repeated center set explicitly for centered 3x3 or linear-pattern layouts instead of deriving one seed recess and hoping later turns recover the array.",
                    "If the prompt mentions revolve, treat that as descriptive user intent for a hemispherical recess, not as a mandatory first-pass modeling recipe when direct sphere subtraction is lower risk.",
                    "Use only valid Build123d sphere-construction helpers; do not invent alternate top-level sphere constructors.",
                    "Do not subtract by mutating `part.solid`; stay in the builder with `mode=Mode.SUBTRACT`, or subtract from `part.part` only after the builder closes.",
                ],
            }
        )

    if (
        latest_tool == "execute_build123d"
        and bool(getattr(semantics, "mentions_spherical_recess", False))
        and bool(getattr(semantics, "mentions_pattern", False))
        and blockers.intersection(
            {
                "feature_hole",
                "feature_hole_position_alignment",
                "feature_local_anchor_alignment",
                "feature_profile_shape_alignment",
                "feature_pattern",
                "feature_pattern_seed_alignment",
            }
        )
    ):
        skills.append(
            {
                "skill_id": "spherical_recess_pattern_code_repair",
                "when_relevant": "Use when a whole-part code repair already produced a host solid for repeated hemispherical recesses but validation still reports profile/layout mismatches.",
                "guidance": [
                    "Treat this as a spherical-recess pattern family, not as an annular groove or generic revolve family.",
                    "Keep the recesses attached to the host face: when the prompt says the diameter edge coincides with the host face, sphere centers should lie on that host face plane rather than below it.",
                    "Use Build123d sphere primitives for the recess cutters.",
                    "For centered repeated layouts, derive and preserve the full center set explicitly instead of moving one seed sphere and hoping later turns recover the array.",
                    "After a repair write, prefer query_feature_probes or execute_build123d_probe before another blind rewrite so the next turn can distinguish shape success from layout failure.",
                ],
            }
        )

    if (
        latest_tool == "execute_build123d"
        and (
            "revolve" in requirement_lower
            or "shaft" in requirement_lower
            or "stud" in requirement_lower
            or "non_positive_volume" in invalid_signals
        )
    ):
        skills.append(
            {
                "skill_id": "revolve_requires_closed_area",
                "when_relevant": "Use when a revolve/extrude write produced a shell, zero volume, or flat bbox.",
                "guidance": [
                    "A successful revolve must start from a closed 2D area, not only a wire or open profile.",
                    "If the result has solids=1 but volume<=0, treat it as invalid and repair the profile definition before more inspection.",
                    "For stepped shafts, confirm the half-profile encloses area away from the rotation axis before revolving.",
                ],
            }
        )

    if _requirement_explicitly_prescribes_revolve_profile(requirement_lower):
        explicit_revolve_guidance = [
            "Keep the primary strategy on an explicit closed 2D profile revolve, not on a fallback primitive approximation.",
            "Build the closed 2D profile on the plane that contains the rotation axis, then revolve that closed area with an explicit rotation axis definition such as revolve(360, axisStart=..., axisEnd=...).",
            "If a previous revolve produced a flat or zero-volume result, first repair the profile closure, workplane choice, and rotation axis before abandoning the revolve recipe.",
            "Treat the centerline/axis-of-rotation instructions as part of the required modeling semantics, not as optional commentary.",
        ]
        if (
            latest_tool == "execute_build123d"
            and "non_positive_volume" in invalid_signals
            and "flat_solid_bbox" in invalid_signals
        ):
            explicit_revolve_guidance.extend(
                [
                    "A repeated flat Build123d revolve means the current profile is still being treated like a wire or sheet instead of a closed area.",
                    "For the repair write, build a closed face explicitly from the profile and revolve that area into a solid instead of retrying minor point-order tweaks on the same wire-only revolve call.",
                    "Keep the coordinate axis that carries the height values aligned with the actual revolve axis: if height values are encoded on Y, revolve around global Y; if height values are encoded on Z for an XZ sketch, revolve around global Z.",
                    "Do not encode the profile with all points at z=0 and then revolve around global Z, or with all points at y=0 and then revolve around global Y; that recreates a planar sheet instead of a positive-volume solid.",
                    "If later code needs another builder stage after the solid revolve, carry the repaired solid forward as the explicit result instead of falling back to legacy workplane chaining.",
                ]
            )
        skills.append(
            {
                "skill_id": "explicit_revolve_profile_recipe",
                "when_relevant": "Use when the requirement explicitly prescribes a sketch plane, rotation axis/centerline, closed profile, and 360-degree revolve workflow.",
                "guidance": explicit_revolve_guidance,
            }
        )

    if (
        latest_tool == "execute_build123d"
        and "non_positive_volume" in invalid_signals
        and (
            "degenerate_bbox" in invalid_signals
            or "flat_solid_bbox" in invalid_signals
        )
        and (
            "revolve" in requirement_lower
            or "shaft" in requirement_lower
            or "stud" in requirement_lower
            or "axial direction" in requirement_lower
            or ("radius" in requirement_lower and "length" in requirement_lower)
        )
        and not _requirement_explicitly_prescribes_revolve_profile(requirement_lower)
    ):
        skills.append(
            {
                "skill_id": "axisymmetric_primitives_after_flat_revolve",
                "when_relevant": "Use when an axisymmetric part keeps producing a sheet-like zero-volume revolve result.",
                "guidance": [
                    "If one bbox axis stays near zero after execute_build123d, treat the result as a flat sheet/surface, not a usable solid.",
                    "For axisymmetric parts defined by radii along axial segments, rebuild with coaxial cylinders or cones merged along the target axis instead of retrying minor point-order variations of the same revolve profile.",
                    "Only keep the revolve strategy if you can clearly construct a positive-area profile that will produce a real solid around the requested axis.",
                    "After the repair write, verify solids>0, volume>0, and all three bbox spans are nonzero before spending more read-only turns.",
                ],
            }
        )

    if (
        latest_tool == "execute_build123d"
        and (
            "feature_multi_plane_additive_union" in blockers
            or "feature_multi_plane_additive_specs" in blockers
            or "union" in requirement_lower
            or "orthogonal" in requirement_lower
        )
    ):
        skills.append(
            {
                "skill_id": "global_axis_primitives_for_multi_body_union",
                "when_relevant": "Use when the part is easier to express as global-axis solids merged together.",
                "guidance": [
                    "For whole-part rebuilds, prefer Build123d Box(x, y, z, align=(...)) to express global-axis boxes directly.",
                    "Do not rely on YZ/XZ workplane intuition for global box dimensions; sketch/workplane orientation is safer for sketch ops than for whole-body primitive dimensions.",
                    "After a union target, compare bbox spans against the required axes before spending another read-only turn.",
                ],
            }
        )

    if (
        latest_tool == "execute_build123d"
        and "feature_cylindrical_slot_alignment" in blockers
    ):
        skills.append(
            {
                "skill_id": "clean_cylindrical_slot_boolean",
                "when_relevant": "Use when an explicit cutting-cylinder slot is aligned correctly in principle but still produces the wrong cylindrical-face topology.",
                "guidance": [
                    "When the requirement already defines a cutting cylinder, model the host block and one tool cylinder directly, then perform a single boolean difference.",
                    "In Build123d, prefer a single `Cylinder(radius, length, align=(Align.CENTER, Align.CENTER, Align.CENTER))` positioned with `Pos(...)` and `Rot(...)` so the requested axis and centerline are literal, instead of rebuilding the slot from stacked partial cuts or improvised profile fragments.",
                    "Do not build this cutter by sketching a circle on the YZ plane and extruding it both ways when validator is already reporting fragmented cylindrical wall faces; that repair pattern tends to preserve the same broken slot topology.",
                    "For an X-axis slot with centerline `(0, 0, z0)`, the default safe pattern is `cutter = Pos(0, 0, z0) * (Rot(Y=90) * Cylinder(...))`, then `result = host.part - cutter`.",
                    "Avoid repair writes that create extra cylindrical wall fragments on one side of the slot; the target should keep one clean cylindrical wall per side, or one continuous trough face when the topology stays connected.",
                    "After the cut, verify the cylindrical wall faces are minimal and symmetric rather than split into multiple same-side patches.",
                ],
            }
        )

    notch_profile_prompt = (
        "u-shaped" in requirement_lower
        or "u shape" in requirement_lower
        or "channel section" in requirement_lower
        or (
            "top face" in requirement_lower
            and "slot" in requirement_lower
            and any(
                token in requirement_lower
                for token in ("spans the full", "spans full", "full length")
            )
        )
    )
    if latest_tool == "execute_build123d" and (
        "feature_notch_profile_alignment" in blockers or notch_profile_prompt
    ):
        skills.append(
            {
                "skill_id": "cross_section_first_notch_profiles",
                "when_relevant": "Use when a U-shape / notch / slot profile exists but keeps failing profile-alignment validation.",
                "guidance": [
                    "Model the named 2D cross-section directly on the requirement's profile plane with `BuildSketch`, then extrude along the orthogonal axis.",
                    "For rectangular U-channels and centered notch sections, prefer one sketch containing an outer `Rectangle(...)` plus an inner `Rectangle(..., mode=Mode.SUBTRACT)` window instead of rebuilding the notch later with loosely aligned subtractive boxes.",
                    "Keep width, wall height, groove width, groove depth, and the floor offset in that same sketch/profile plane; then extrude the finished section with `extrude(amount=...)` on the orthogonal axis.",
                    "Prefer this same-sketch subtract recipe over ad-hoc `subtract(profile.sketch)`, partial `make_face(mode=Mode.SUBTRACT)` repairs, or ambiguous directional extrude retries.",
                    "If validator still says feature_notch_profile_alignment after a nominally successful write, rebuild the whole cross-section cleanly rather than spending more read-only turns on the same misoriented profile.",
                ],
            }
        )

    if (
        latest_tool == "execute_build123d"
        and "feature_fillet" in blockers
    ):
        skills.append(
            {
                "skill_id": "session_backed_local_edge_finishing",
                "when_relevant": "Use when a direct code rebuild succeeded but a local fillet/chamfer feature is still missing.",
                "guidance": [
                    "A successful execute_build123d write already persisted authoritative session geometry for follow-on tools.",
                    "Prefer query_topology to get fresh edge refs, then use apply_cad_action with fillet/chamfer and explicit edge_refs for the local finishing step.",
                    "If query_topology already exposes a requirement-aligned edge candidate set such as bottom_outer_edges or y_parallel_bottom_outer_edges, consume those refs directly on the next write turn instead of spending another read-only round.",
                    "Do not default to reloading model.step inside another Build123d script for a small local finish unless the runtime explicitly exposes a state-import helper.",
                ],
            }
        )

    axis_selector = None
    if "parallel to the x axis" in requirement_lower:
        axis_selector = "|X"
    elif "parallel to the y axis" in requirement_lower:
        axis_selector = "|Y"
    elif "parallel to the z axis" in requirement_lower:
        axis_selector = "|Z"
    edge_scope = None
    if "bottom" in requirement_lower:
        edge_scope = "bottom"
    elif "top" in requirement_lower:
        edge_scope = "top"
    if (
        latest_tool == "execute_build123d"
        and blockers.intersection({"feature_fillet", "feature_chamfer"})
        and axis_selector is not None
        and edge_scope is not None
    ):
        skills.append(
            {
                "skill_id": "axis_constrained_local_edge_finish_selectors",
                "when_relevant": "Use when only a directional fillet/chamfer tail remains after a successful code-path rebuild.",
                "guidance": [
                    "Prefer one local apply_cad_action finish over another whole-part rewrite when the remaining blocker is only a directional fillet/chamfer.",
                    f"For this kind of requirement, use apply_cad_action with edge_scope='{edge_scope}' and edges_selector='{axis_selector}' before falling back to broader chained selectors in execute_build123d.",
                    "Use query_topology only if the selector is still ambiguous; do not spend the default next turn on another blind whole-part code retry.",
                    "Avoid inverted selector chains such as picking the correct axis but the wrong top/bottom side in Build123d code.",
                ],
            }
        )

    if (
        latest_tool == "execute_build123d"
        and previous_failure_kind in {
            "execute_build123d_chain_context_failure",
            "execute_build123d_selector_failure",
            "execute_build123d_timeout",
        }
        and _requirement_prefers_named_face_local_feature_sequence(requirement_lower)
    ):
        skills.append(
            {
                "skill_id": "recover_from_failed_whole_part_retry",
                "when_relevant": "Use when a whole-part code retry is failing on a requirement that already decomposes into bounded local edits.",
                "guidance": [
                    "Treat the failed whole-part code path as evidence about tool choice, not only as a syntax/modeling bug.",
                    "After execute_build123d timeout, broken solid-chain, or selector-based fillet failure, do not default to another end-to-end rewrite on the next turn.",
                    "Either rebuild only to the simpler pre-fillet solid or switch to a bounded local finishing step once a stable host solid and authoritative refs exist.",
                ],
            }
        )

    if annular_blockers_active:
        skills.append(
            {
                "skill_id": "axisymmetric_annular_groove_strategy",
                "when_relevant": "Use when annular/revolved groove intent is under-specified by local repairs and keeps failing semantic completion.",
                "guidance": [
                    "Treat the outer envelope as authoritative and realize the groove as a local rotational subtraction around the same main axis.",
                    "Prefer axisymmetric constructions whose groove depth and axial location can be read back from the final geometry, such as coaxial solid differences or a clearly anchored annular groove band on the main axis.",
                    "When execute_build123d is repairing a cylindrical part, the default whole-part pattern should be: build the base solid, build an annular groove band with outer_radius and inner_radius on the same axis, extrude that band to the requested axial window, then subtract it from the base.",
                    "Only use a raw sketch-plane revolve when you are confident about the local workplane coordinates and rotation axis semantics; otherwise treat revolve as a higher-risk fallback, not the default code-path repair.",
                    "After the repair write, verify that the outer bbox stays stable while the groove introduces the requested local rotational cut.",
                ],
            }
        )

    if (
        latest_tool == "execute_build123d"
        and code_first_family
        and same_tool_failure_count >= 2
    ):
        skills.append(
            {
                "skill_id": "failed_code_family_turn_must_probe_before_retry",
                "when_relevant": "Use when the same whole-part code path has already failed repeatedly on a family-driven geometry problem.",
                "guidance": [
                    "Do not spend the next turn on another equally broad execute_build123d rewrite.",
                    "The next turn should be a targeted diagnostic turn: query_feature_probes first, then query_geometry if needed for bbox/solid confirmation.",
                    "If those reads still leave one family-specific modeling question unresolved, use execute_build123d_probe for a one-off diagnostic script before the next whole-part rewrite.",
                    "Only issue another broad execute_build123d write after the probe turn gives a concrete repair target.",
                ],
            }
        )

    deduped: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for skill in skills:
        skill_id = str(skill.get("skill_id") or "").strip()
        if not skill_id or skill_id in seen_ids:
            continue
        seen_ids.add(skill_id)
        deduped.append(skill)

    deduped.sort(
        key=lambda skill: (
            _skill_priority(
                str(skill.get("skill_id") or "").strip(),
                latest_tool=latest_tool,
                annular_blockers_active=annular_blockers_active,
            ),
            str(skill.get("skill_id") or "").strip(),
        )
    )
    return deduped


def _skill_priority(
    skill_id: str,
    *,
    latest_tool: str,
    annular_blockers_active: bool,
) -> int:
    if latest_tool == "execute_build123d" and annular_blockers_active:
        priorities = {
            "axisymmetric_annular_groove_strategy": 0,
            "mixed_nested_profile_section_bootstrap": 1,
            "revolve_requires_closed_area": 2,
            "annular_groove_revolve_cut_recipe": 5,
        }
        return priorities.get(skill_id, 10)
    general_priorities = {
        "execute_build123d_minimal_script_hygiene": 0,
        "spherical_recess_pattern_code_first": 1,
        "explicit_centered_face_array_centers": 2,
        "spherical_recess_pattern_code_repair": 3,
        "recover_from_failed_whole_part_retry": 4,
        "clean_cylindrical_slot_boolean": 5,
        "explicit_revolve_profile_recipe": 6,
        "axisymmetric_segmented_primitives_preferred_over_revolve": 7,
        "half_shell_profile_envelope_repair": 8,
        "half_shell_profile_from_semicircle_section": 9,
        "path_sweep_wire_profile_frame_repair": 10,
        "named_face_local_feature_sequence": 11,
        "flange_boss_pattern_hole_host_thickness": 12,
        "nested_regular_polygon_frame_code_first": 13,
        "named_axis_axisymmetric_pose_alignment_repair": 14,
        "regular_polygon_side_length_build123d_semantics": 15,
        "positive_extrude_from_named_plane_is_not_centered": 16,
        "positive_extrude_bbox_alignment_repair": 17,
        "whole_part_additive_features_must_merge_into_single_body": 18,
        "named_plane_profile_to_global_box_mapping": 19,
        "whole_part_union_from_global_axis_primitives": 20,
    }
    if skill_id in general_priorities:
        return general_priorities[skill_id]
    return 100


def _requirements_text(requirements: dict[str, Any]) -> str:
    description = requirements.get("description")
    if isinstance(description, str) and description.strip():
        return description
    return json.dumps(requirements, ensure_ascii=False)


def _detect_positive_extrude_plane(requirement_lower: str) -> tuple[str, str] | None:
    if "extrude" not in requirement_lower:
        return None
    plane_map = {
        "xy plane": "z",
        "yz plane": "x",
        "xz plane": "y",
    }
    for plane_name, axis_name in plane_map.items():
        if plane_name in requirement_lower:
            return plane_name.upper(), axis_name.upper()
    return None


def _detect_named_plane_bottom_aligned_box_pose(
    requirement_lower: str,
) -> tuple[str, str] | None:
    if "box" not in requirement_lower:
        return None
    plane_map = {
        "xy plane": ("XY", "Z"),
        "yz plane": ("YZ", "X"),
        "xz plane": ("XZ", "Y"),
    }
    for plane_token, (plane_name, axis_name) in plane_map.items():
        if plane_token not in requirement_lower:
            continue
        axis_lower = axis_name.lower()
        if re.search(
            rf"(?:bottom|base)[^.,;]{{0,32}}{axis_lower}\s*=\s*0(?:\.0+)?",
            requirement_lower,
            re.IGNORECASE,
        ):
            return plane_name, axis_name
    return None


def _centered_tuple_for_positive_span_axis(axis_name: str) -> str:
    axis = axis_name.strip().upper()
    centered_map = {
        "X": "(False, True, True)",
        "Y": "(True, False, True)",
        "Z": "(True, True, False)",
    }
    return centered_map.get(axis, "(True, True, True)")


def _requirement_requests_centered_plane_pose(requirement_lower: str) -> bool:
    if any(
        token in requirement_lower
        for token in (
            "symmetr",
            "midplane",
            "centered about",
            "about the xy plane",
            "about the yz plane",
            "about the xz plane",
        )
    ):
        return True
    return any(
        re.search(pattern, requirement_lower, re.IGNORECASE)
        for pattern in (
            r"center(?:ed)?\s+(?:on|about|around)\s+(?:the\s+)?(?:xy|yz|xz)\s+plane",
            r"extrud(?:e|ed|ing)(?: it)?[^.,;]{0,32}\s+symmetr",
            r"extrud(?:e|ed|ing)(?: it)?[^.,;]{0,32}\s+midplane",
        )
    )


def _requirement_mentions_regular_polygon_side_length(
    requirement_lower: str,
) -> bool:
    if "side length" not in requirement_lower:
        return False
    return any(
        token in requirement_lower
        for token in (
            "equilateral triangle",
            "regular polygon",
            "hexagon",
            "pentagon",
            "octagon",
            "nonagon",
            "decagon",
        )
    )


def _requirement_prefers_named_face_local_feature_sequence(
    requirement_lower: str,
) -> bool:
    if "select the" not in requirement_lower or " face" not in requirement_lower:
        return False
    if not any(
        token in requirement_lower
        for token in (
            "fillet",
            "chamfer",
            "cut-extrude",
            "cut extrude",
            "pocket",
            "blind hole",
            " hole",
            "recess",
            "slot",
            "notch",
        )
    ):
        return False
    return any(
        token in requirement_lower
        for token in (
            "rectangle",
            "box",
            "block",
            "extrude",
            "cylinder",
            "base",
        )
    )


def _requirement_mentions_half_shell_with_split_surface(
    requirement_lower: str,
) -> bool:
    if not requirement_lower:
        return False
    half_shell_tokens = (
        "half-cylindrical",
        "half cylindrical",
        "half cylinder",
        "half a cylinder",
        "semi-cylindrical",
        "semi cylindrical",
        "semicylindrical",
        "half-shell",
        "half shell",
    )
    if not any(token in requirement_lower for token in half_shell_tokens):
        return False
    return any(
        token in requirement_lower
        for token in (
            "split surface",
            "split line",
            "semicircle",
            "semi-circle",
            "bearing housing",
            "bore",
            "lug",
            "flange",
        )
    )


def _requirement_mentions_shelled_host_with_named_face_feature(
    requirement_lower: str,
    *,
    semantics: Any,
) -> bool:
    if not requirement_lower:
        return False
    if not any(
        token in requirement_lower
        for token in ("shell", "shelled", "hollow enclosure", "hollow body")
    ):
        return False
    if not bool(getattr(semantics, "face_targets", ())):
        return False
    return bool(
        getattr(semantics, "mentions_hole", False)
        or getattr(semantics, "mentions_pattern", False)
        or getattr(semantics, "mentions_spherical_recess", False)
        or any(
            token in requirement_lower
            for token in ("recess", "pocket", "groove", "slot", "notch")
        )
    )


def _requirement_mentions_flange_boss_pattern_holes(requirement_lower: str) -> bool:
    if not requirement_lower:
        return False
    if "flange" not in requirement_lower or "boss" not in requirement_lower:
        return False
    if "hole" not in requirement_lower:
        return False
    pattern_tokens = (
        "circular array",
        "evenly distributed",
        "distributed circle",
        "bolt circle",
        "pitch circle",
        "pattern",
    )
    if not any(token in requirement_lower for token in pattern_tokens):
        return False
    return (
        "through the flange" in requirement_lower
        or "cut through the flange" in requirement_lower
    )


def _requirement_mentions_explicit_path_sweep(requirement_lower: str) -> bool:
    if not requirement_lower or "sweep" not in requirement_lower:
        return False
    explicit_sweep_tokens = (
        "execute the sweep command",
        "sweep along the",
        "sweep the annular profile",
        "sweep the profile along",
    )
    if not any(token in requirement_lower for token in explicit_sweep_tokens):
        return False
    has_rail = "path sketch" in requirement_lower or "path" in requirement_lower or "rail" in requirement_lower
    has_profile = "profile sketch" in requirement_lower or "profile" in requirement_lower
    return has_rail and has_profile


def _requirement_uses_named_plane_symmetric_union(requirement_lower: str) -> bool:
    plane_hits = sum(
        1 for token in ("xy plane", "yz plane", "xz plane") if token in requirement_lower
    )
    if plane_hits < 2:
        return False
    if "symmetr" not in requirement_lower and "centered" not in requirement_lower:
        return False
    if "extrude" not in requirement_lower:
        return False
    return (
        "union" in requirement_lower
        or "orthogonal" in requirement_lower
        or "intersect at the origin" in requirement_lower
    )


def _detect_positive_extrude_bbox_mismatch(
    *,
    requirement_lower: str,
    latest_write_health: dict[str, Any] | None,
) -> tuple[str, str, tuple[float, float], tuple[float, float]] | None:
    if not isinstance(latest_write_health, dict):
        return None
    if str(latest_write_health.get("tool") or "").strip().lower() != "execute_build123d":
        return None
    if any(
        token in requirement_lower
        for token in (
            "centered about",
            "symmetr",
            "midplane",
            "about the xy plane",
            "about the yz plane",
            "about the xz plane",
        )
    ):
        return None
    plane_spec = _extract_positive_extrude_spec(requirement_lower)
    if plane_spec is None:
        return None
    plane_name, axis_name, axis_index, distance = plane_spec
    geometry = latest_write_health.get("geometry")
    if not isinstance(geometry, dict):
        return None
    bbox_min = geometry.get("bbox_min")
    bbox_max = geometry.get("bbox_max")
    if not (
        isinstance(bbox_min, list)
        and isinstance(bbox_max, list)
        and len(bbox_min) >= 3
        and len(bbox_max) >= 3
    ):
        return None
    current_min = float(bbox_min[axis_index])
    current_max = float(bbox_max[axis_index])
    expected_range = (0.0, float(distance))
    current_range = (current_min, current_max)
    distance_tolerance = max(1e-3, abs(distance) * 0.08)
    lower_bound_matches = abs(current_min - expected_range[0]) <= distance_tolerance
    upper_bound_matches = abs(current_max - expected_range[1]) <= distance_tolerance
    if lower_bound_matches and upper_bound_matches:
        return None
    return plane_name, axis_name, expected_range, current_range


def _extract_positive_extrude_spec(
    requirement_lower: str,
) -> tuple[str, str, int, float] | None:
    plane_map = {
        "xy plane": ("XY PLANE", "Z", 2),
        "yz plane": ("YZ PLANE", "X", 0),
        "xz plane": ("XZ PLANE", "Y", 1),
    }
    matched_plane: tuple[str, str, int] | None = None
    for plane_token, plane_spec in plane_map.items():
        if plane_token in requirement_lower:
            matched_plane = plane_spec
            break
    if matched_plane is None:
        return None
    import re

    match = re.search(
        r"extrud(?:e|ed|ing)(?: it)?(?: [^.,;]{0,32})? by ([0-9]+(?:\.[0-9]+)?)",
        requirement_lower,
    )
    if match is None:
        match = re.search(
            r"extrud(?:e|ed|ing)(?: it)? ([0-9]+(?:\.[0-9]+)?)(?: millimeters?| mm)?(?: [^.,;]{0,24})? along (?:the )?[xyz]-axis",
            requirement_lower,
        )
    if match is None:
        return None
    distance = float(match.group(1))
    if distance <= 0.0:
        return None
    plane_name, axis_name, axis_index = matched_plane
    return plane_name, axis_name, axis_index, distance


def _detect_named_axis_axisymmetric_pose_mismatch(
    *,
    requirement_lower: str,
    latest_write_health: dict[str, Any] | None,
) -> tuple[str, tuple[str, str], tuple[float, float], tuple[float, float, float]] | None:
    if not isinstance(latest_write_health, dict):
        return None
    if str(latest_write_health.get("tool") or "").strip().lower() != "execute_build123d":
        return None
    axis_spec = _extract_named_axis_axisymmetric_spec(requirement_lower)
    if axis_spec is None:
        return None
    axis_name, axis_index = axis_spec
    geometry = latest_write_health.get("geometry")
    if not isinstance(geometry, dict):
        return None
    bbox_min = geometry.get("bbox_min")
    bbox_max = geometry.get("bbox_max")
    center = geometry.get("center_of_mass")
    if not (
        isinstance(bbox_min, list)
        and isinstance(bbox_max, list)
        and len(bbox_min) >= 3
        and len(bbox_max) >= 3
    ):
        return None
    perpendicular_indices = [idx for idx in range(3) if idx != axis_index]
    perpendicular_names = tuple("XYZ"[idx] for idx in perpendicular_indices)
    radial_span = max(
        float(bbox_max[idx]) - float(bbox_min[idx]) for idx in perpendicular_indices
    )
    tolerance = max(1.0, abs(radial_span) * 0.08)
    bbox_offsets = tuple(
        abs((float(bbox_min[idx]) + float(bbox_max[idx])) / 2.0)
        for idx in perpendicular_indices
    )
    center_tuple = (
        tuple(float(center[idx]) for idx in range(3))
        if isinstance(center, list) and len(center) >= 3
        else (0.0, 0.0, 0.0)
    )
    center_offsets = tuple(abs(center_tuple[idx]) for idx in perpendicular_indices)
    if max((*bbox_offsets, *center_offsets)) <= tolerance:
        return None
    return axis_name, perpendicular_names, bbox_offsets, center_tuple


def _extract_named_axis_axisymmetric_spec(
    requirement_lower: str,
) -> tuple[str, int] | None:
    if not any(
        token in requirement_lower
        for token in ("revolve", "revolution", "rotational", "axisymmetric")
    ):
        return None
    import re

    match = re.search(
        r"(?:around|about)\s+(?:the\s+)?(?P<axis>[xyz])(?:\s*[- ]?\s*axis)\b",
        requirement_lower,
        re.IGNORECASE,
    )
    if match is None:
        return None
    axis_name = str(match.group("axis")).upper()
    axis_index_map = {"X": 0, "Y": 1, "Z": 2}
    return axis_name, axis_index_map[axis_name]


def _requirement_explicitly_prescribes_revolve_profile(requirement_lower: str) -> bool:
    if not any(
        token in requirement_lower
        for token in (
            "rotational addition",
            "revolved boss",
            "revolve",
            "rotate 360",
            "360 degrees",
        )
    ):
        return False
    has_axis_language = (
        "axis of rotation" in requirement_lower
        or "centerline" in requirement_lower
        or _extract_named_axis_axisymmetric_spec(requirement_lower) is not None
    )
    has_profile_language = (
        "closed profile" in requirement_lower
        or "close the profile" in requirement_lower
        or "close the sketch" in requirement_lower
        or "sketch plane" in requirement_lower
    )
    return has_axis_language and has_profile_language


def _requirement_suggests_mixed_nested_section(
    *,
    requirement_lower: str,
    blockers: set[str],
) -> bool:
    if "feature_inner_void_cutout" in blockers:
        return True
    if "extrude" not in requirement_lower:
        return False
    if "center" not in requirement_lower:
        return False
    shape_tokens = {
        token
        for token in ("circle", "square", "rectangle", "triangle", "hexagon", "polygon")
        if token in requirement_lower
    }
    if len(shape_tokens) < 2:
        return False
    return (
        "extrude the section" in requirement_lower
        or "frame" in requirement_lower
        or "hollow" in requirement_lower
        or "inner void" in requirement_lower
        or "cutout" in requirement_lower
    )


def _requirement_prefers_nested_regular_polygon_frame(
    *,
    requirement_lower: str,
    blockers: set[str],
) -> bool:
    if not _requirement_mentions_regular_polygon_side_length(requirement_lower):
        return False
    if "feature_inner_void_cutout" in blockers:
        return True
    if "extrude" not in requirement_lower:
        return False
    return any(
        token in requirement_lower
        for token in (
            "concentric",
            "centroid",
            "centroids coinciding",
            "frame-shaped region",
            "frame",
            "hollow",
            "inner void",
        )
    )


def _requirement_has_explicit_xy_coordinate_pair(requirement_text: str) -> bool:
    if not isinstance(requirement_text, str) or not requirement_text.strip():
        return False
    return bool(
        re.search(
            r"\(\s*[-+]?[0-9]+(?:\.[0-9]+)?\s*,\s*[-+]?[0-9]+(?:\.[0-9]+)?\s*\)",
            requirement_text,
        )
    )


def _requirement_mentions_directional_drilling(requirement_lower: str) -> bool:
    if not requirement_lower:
        return False
    return any(
        phrase in requirement_lower
        for phrase in (
            "in the x direction",
            "in the y direction",
            "in the z direction",
            "along the x direction",
            "along the y direction",
            "along the z direction",
            "drill in the x direction",
            "drill in the y direction",
            "drill in the z direction",
            "through-holes through the lugs in the y direction",
        )
    )


def _infer_centered_square_or_rectangular_array_centers(
    requirement_text: str | None,
) -> list[list[float]]:
    text = str(requirement_text or "").strip().lower()
    if not text or ("array" not in text and "pattern" not in text):
        return []
    if "center" not in text:
        return []

    explicit_xy_offset = re.search(
        r"each\s+\w+\s*'?s?\s+center\s+is\s+([0-9]+(?:\.[0-9]+)?)\s*mm?\s+from\s+the\s+center\s+in\s+the\s+x\s*/\s*y\s+direction",
        text,
        re.IGNORECASE,
    )
    if explicit_xy_offset is not None:
        try:
            offset = float(explicit_xy_offset.group(1))
        except Exception:
            offset = 0.0
        if offset > 0.0:
            return [
                [offset, offset],
                [offset, -offset],
                [-offset, offset],
                [-offset, -offset],
            ]

    square_side_match = re.search(
        r"square\s+array[^.]{0,80}?side\s+length(?:\s+of)?\s+([0-9]+(?:\.[0-9]+)?)",
        text,
        re.IGNORECASE,
    )
    if square_side_match is not None:
        try:
            side_length = float(square_side_match.group(1))
        except Exception:
            side_length = 0.0
        if side_length > 0.0:
            half = side_length / 2.0
            return [
                [half, half],
                [half, -half],
                [-half, half],
                [-half, -half],
            ]

    x_axis_pattern = re.search(
        r"x-axis[^.;]{0,120}?spacing\s+([0-9]+(?:\.[0-9]+)?)\s*mm?[^.;]{0,80}?quantity\s+([0-9]+)",
        text,
        re.IGNORECASE,
    )
    y_axis_pattern = re.search(
        r"y-axis[^.;]{0,120}?spacing\s+([0-9]+(?:\.[0-9]+)?)\s*mm?[^.;]{0,80}?quantity\s+([0-9]+)",
        text,
        re.IGNORECASE,
    )
    if x_axis_pattern is not None and y_axis_pattern is not None:
        try:
            x_spacing = float(x_axis_pattern.group(1))
            x_count = int(x_axis_pattern.group(2))
            y_spacing = float(y_axis_pattern.group(1))
            y_count = int(y_axis_pattern.group(2))
        except Exception:
            x_spacing = 0.0
            x_count = 0
            y_spacing = 0.0
            y_count = 0
        if (
            x_spacing > 0.0
            and y_spacing > 0.0
            and x_count > 1
            and y_count > 1
            and (x_count * y_count) <= 25
        ):
            x_mid = (x_count - 1) / 2.0
            y_mid = (y_count - 1) / 2.0
            x_positions = [round((index - x_mid) * x_spacing, 4) for index in range(x_count)]
            y_positions = [round((index - y_mid) * y_spacing, 4) for index in range(y_count)]
            return [[x_pos, y_pos] for x_pos in x_positions for y_pos in y_positions]

    return []
