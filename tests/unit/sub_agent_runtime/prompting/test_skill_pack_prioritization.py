from sub_agent_runtime.prompting import build_runtime_skill_pack
from sub_agent_runtime.prompting.skill_assembly import (
    recommended_feature_probe_families,
    requirement_prefers_code_first_family,
)


_ANNULAR_GROOVE_REQUIREMENT = (
    "Select the XY plane, draw a circle with a diameter of 50.0 mm and a square with "
    "a side length of 25.0 mm centered. Extrude the section by 60.0 mm. Select the "
    "front view plane, at a height of 30.0 mm, draw a 5.0 mm x 2.0 mm rectangle "
    "aligned with the edge, and use a revolved cut to create an annular groove."
)

def test_requirement_prefers_code_first_from_domain_kernel_repair_mode() -> None:
    assert requirement_prefers_code_first_family(
        requirements={"description": "add one simple hole"},
        latest_validation={},
        domain_kernel_digest={"latest_patch_repair_mode": "whole_part_rebuild"},
    )

def test_recommended_feature_probe_families_include_domain_kernel_family_first() -> None:
    families = recommended_feature_probe_families(
        requirements={"description": "simple part"},
        latest_validation={},
        domain_kernel_digest={
            "active_feature_instances": [{"family_id": "path_sweep"}],
            "latest_repair_packet_family_id": "spherical_recess",
        },
    )

    assert families[:2] == ["path_sweep", "spherical_recess"]

def test_recommended_feature_probe_families_include_named_face_local_edit_for_local_finish_requirements() -> None:
    families = recommended_feature_probe_families(
        requirements={
            "description": (
                "Create a bracket and finish it with local edge fillets around the top opening "
                "plus a countersink on the mounting face so that a topology-aware local finishing pass is useful."
            )
        },
        latest_validation={},
    )

    assert "named_face_local_edit" in families

def test_build_runtime_skill_pack_adds_insufficient_evidence_guidance() -> None:
    skills = build_runtime_skill_pack(
        requirements={"description": "make a bracket"},
        latest_validation={
            "success": True,
            "is_complete": False,
            "blockers": ["feature_hole"],
            "insufficient_evidence": True,
            "decision_hints": ["inspect_more_evidence"],
        },
        latest_write_health={"tool": "execute_build123d"},
    )

    skill_ids = {item["skill_id"] for item in skills}
    assert "insufficient_evidence_query_before_repair" in skill_ids

def test_build_runtime_skill_pack_surfaces_latest_repair_packet_recipe() -> None:
    skills = build_runtime_skill_pack(
        requirements={
            "description": (
                "Create a plate and add countersunk holes at explicit positions on the top face."
            )
        },
        latest_validation={
            "blockers": [
                "feature_hole_position_alignment",
                "feature_local_anchor_alignment",
            ]
        },
        latest_write_health={"tool": "execute_build123d"},
        domain_kernel_digest={
            "latest_repair_packet_family_id": "explicit_anchor_hole",
            "latest_repair_packet_repair_mode": "subtree_rebuild",
            "latest_repair_packet_recipe_id": "explicit_anchor_hole_helper_contract_fallback",
            "latest_repair_packet_recipe_summary": (
                "Keep the host body authoritative, prefer helper-based hole/countersink creation "
                "on the host face, and avoid manual cone/cylinder cutters inside the active BuildPart "
                "when the center layout is not yet fully grounded."
            ),
            "latest_repair_packet_recipe_skeleton": {
                "mode": "subtree_rebuild_via_execute_build123d",
                "host_face": "top",
                "workplane_frame": "host_face_local",
                "center_source_key": "derive_from_requirement_or_validation",
                "hole_call": "CounterSinkHole_or_Hole",
                "cutter_strategy": "avoid_manual_cone_cylinder_inside_active_builder",
            },
            "latest_repair_packet_target_anchor_summary": {},
            "latest_repair_packet_host_frame": {
                "frame_kind": "host_face_local",
                "host_face": "top",
            },
        },
    )

    assert skills[1]["skill_id"] == "kernel_repair_packet_recipe"
    guidance = "\n".join(skills[1]["guidance"])

    assert "family=explicit_anchor_hole" in guidance
    assert "recipe=explicit_anchor_hole_helper_contract_fallback" in guidance
    assert "descriptive-only packet" in guidance
    assert "keep the next execute_build123d attempt on this recipe lane" in guidance
    assert "center_source_key=\"derive_from_requirement_or_validation\"" in guidance
    assert "Prefer the native hole helper contract on the target host face" in guidance
    assert "do not fall back to manual cone/cylinder cutters inside an active BuildPart" in guidance

def test_build_runtime_skill_pack_marks_supported_packet_as_execute_repair_packet_lane() -> None:
    skills = build_runtime_skill_pack(
        requirements={
            "description": "Create a host block with four hemispherical recesses on the top face."
        },
        latest_validation={
            "blockers": [
                "feature_spherical_recess_position_alignment",
            ]
        },
        latest_write_health={"tool": "execute_build123d"},
        domain_kernel_digest={
            "latest_repair_packet_family_id": "spherical_recess",
            "latest_repair_packet_repair_mode": "subtree_rebuild",
            "latest_repair_packet_recipe_id": "spherical_recess_host_face_center_set",
            "latest_repair_packet_recipe_summary": (
                "Rebuild the host and subtract the recess spheres from the host face centers."
            ),
            "latest_repair_packet_recipe_skeleton": {
                "mode": "subtree_rebuild_via_execute_repair_packet",
                "center_source_key": "expected_local_centers",
            },
            "latest_repair_packet_target_anchor_summary": {
                "expected_local_centers": [[-10.0, -10.0], [10.0, 10.0]],
            },
            "latest_repair_packet_host_frame": {
                "frame_kind": "host_face_local",
                "host_face": "top",
            },
        },
    )

    packet_skill = next(
        item for item in skills if item["skill_id"] == "kernel_repair_packet_recipe"
    )
    guidance = "\n".join(packet_skill["guidance"])

    assert "runtime-supported packet" in guidance
    assert "prefer `execute_repair_packet`" in guidance

def test_build_runtime_skill_pack_adds_api_lint_repair_guidance() -> None:
    skills = build_runtime_skill_pack(
        requirements={"description": "make a shaft"},
        latest_validation={},
        latest_write_health={"tool": "execute_build123d"},
        previous_tool_failure_summary={
            "tool": "execute_build123d",
            "lint_hits": [
                {
                    "rule_id": "wrong_keyword.Circle.radius",
                    "message": "example lint",
                }
            ],
        },
    )

    skill_ids = {item["skill_id"] for item in skills}
    assert "execute_build123d_api_lint_repair_first" in skill_ids

def test_build_runtime_skill_pack_surfaces_direct_failure_lint_contract_hint() -> None:
    skills = build_runtime_skill_pack(
        requirements={"description": "make a countersunk bracket"},
        latest_validation={},
        latest_write_health={"tool": "execute_build123d"},
        previous_tool_failure_summary={
            "tool": "execute_build123d",
            "lint_hits": [
                {
                    "rule_id": "invalid_build123d_context.transform_context_manager",
                    "message": "Rot is not a context manager",
                    "repair_hint": (
                        "Use `Locations(...)` for scoped placement, or apply the transform with "
                        "`Rot(...) * solid` on a detached solid instead of `with Rot(...):`."
                    ),
                    "recommended_recipe_id": "build123d_transform_placement_contract",
                }
            ],
        },
    )

    lint_skill = next(
        item
        for item in skills
        if item["skill_id"] == "execute_build123d_failure_lint_contract"
    )
    guidance = "\n".join(lint_skill["guidance"])

    assert "rule=invalid_build123d_context.transform_context_manager" in guidance
    assert "recommended_recipe_id=build123d_transform_placement_contract" in guidance
    assert "instead of `with Rot(...):`" in guidance

def test_build_runtime_skill_pack_surfaces_structural_priority_for_mixed_failure_lints() -> None:
    skills = build_runtime_skill_pack(
        requirements={
            "description": "Create a two-part rounded clamshell enclosure with a hollow base and lid."
        },
        latest_validation={},
        latest_write_health={"tool": "execute_build123d"},
        previous_tool_failure_summary={
            "tool": "execute_build123d",
            "lint_hits": [
                {
                    "rule_id": "invalid_build123d_contract.active_builder_part_mutation",
                    "message": "Do not mutate host.part while the builder is still open.",
                },
                {
                    "rule_id": "invalid_build123d_keyword.cylinder_axis",
                    "message": "Cylinder(...) does not accept axis=.",
                },
            ],
        },
    )

    lint_skill = next(
        item
        for item in skills
        if item["skill_id"] == "execute_build123d_failure_lint_contract"
    )
    guidance = "\n".join(lint_skill["guidance"])

    assert "repair the builder-authority contract first" in guidance
    assert "rule=invalid_build123d_contract.active_builder_part_mutation" in guidance

def test_build_runtime_skill_pack_surfaces_transform_rebind_repair_lane_for_failure_lint() -> None:
    skills = build_runtime_skill_pack(
        requirements={
            "description": (
                "Create a two-part rounded clamshell enclosure with a pin hinge and front thumb notch."
            )
        },
        latest_validation={},
        latest_write_health={"tool": "execute_build123d"},
        previous_tool_failure_summary={
            "tool": "execute_build123d",
            "lint_hits": [
                {
                    "rule_id": "invalid_build123d_contract.active_builder_temporary_primitive_transform_rebind",
                    "message": "Primitive was rebound with Rot(...) after already entering the active host.",
                }
            ],
        },
    )

    lint_skill = next(
        item
        for item in skills
        if item["skill_id"] == "execute_build123d_failure_lint_contract"
    )
    guidance = "\n".join(lint_skill["guidance"])

    assert "inside an active host, place the primitive correctly with `Locations(...)` at creation time" in guidance
    assert "close that builder first and only then orient/place the detached solid with `Rot(...) * part` or `Pos(...) * Rot(...) * part`" in guidance

def test_build_runtime_skill_pack_adds_detached_cylindrical_cutter_contract_guidance() -> None:
    skills = build_runtime_skill_pack(
        requirements={
            "description": (
                "Create a snap clamshell enclosure with corner magnet slots, a thumb notch, and a pin hinge."
            )
        },
        latest_validation={},
        latest_write_health={"tool": "execute_build123d"},
        previous_tool_failure_summary={
            "tool": "execute_build123d",
            "lint_hits": [
                {
                    "rule_id": "invalid_build123d_contract.detached_subtractive_builder_without_host",
                    "message": "Detached subtractive builder started without a host.",
                },
                {
                    "rule_id": "invalid_build123d_keyword.cylinder_axis",
                    "message": "Cylinder(...) does not accept axis=.",
                },
            ],
        },
    )

    cutter_skill = next(
        item
        for item in skills
        if item["skill_id"] == "execute_build123d_detached_cylindrical_cutter_contract"
    )
    guidance = "\n".join(cutter_skill["guidance"])

    assert "do not start a detached builder with `mode=Mode.SUBTRACT`" in guidance
    assert "do not pass `axis=Axis.X`" in guidance
    assert "orient it with `Rot(...)`" in guidance
    assert "only use detached boolean subtraction after the host builder closes" in guidance

def test_build_runtime_skill_pack_adds_explicit_cylindrical_slot_recipe_guidance() -> None:
    skills = build_runtime_skill_pack(
        requirements={
            "description": (
                "Create a snap clamshell enclosure with corner magnet slots, a thumb notch, and a pin hinge."
            )
        },
        latest_validation={},
        latest_write_health={"tool": "execute_build123d"},
        previous_tool_failure_summary={
            "tool": "execute_build123d",
            "lint_hits": [
                {
                    "rule_id": "invalid_build123d_contract.active_builder_temporary_primitive_transform_rebind",
                    "message": "Do not rebind active-builder primitives with Pos(...) after creation.",
                },
                {
                    "rule_id": "invalid_build123d_keyword.plane_normal_alias",
                    "message": "Plane(...) uses z_dir=..., not normal=.",
                },
                {
                    "rule_id": "invalid_build123d_keyword.cylinder_axis",
                    "message": "Cylinder(...) does not accept axis=.",
                },
            ],
            "repair_recipe": {
                "recipe_id": "explicit_cylindrical_slot_boolean_safe_recipe",
                "recipe_summary": (
                    "Keep the host builder authoritative and use a literal detached cylinder "
                    "only after the host closes."
                ),
            },
        },
    )

    recipe_skill = next(
        item
        for item in skills
        if item["skill_id"] == "execute_build123d_explicit_cylindrical_slot_recipe_contract"
    )
    guidance = "\n".join(recipe_skill["guidance"])

    assert "relocate it with `Pos(...) * lid_outer`" in guidance
    assert "`Plane(origin=..., z_dir=...)`" in guidance
    assert "do not pass `axis=` into `Cylinder(...)`" in guidance
    assert "host builder authoritative" in guidance
