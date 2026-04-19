from sub_agent_runtime.skill_pack import (
    build_runtime_skill_pack,
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
    assert "center_source_key=\"derive_from_requirement_or_validation\"" in guidance
    assert "Prefer the native hole helper contract on the target host face" in guidance
    assert "do not fall back to manual cone/cylinder cutters inside an active BuildPart" in guidance


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


def test_build_runtime_skill_pack_adds_clamshell_split_axis_and_hinge_contract() -> None:
    skills = build_runtime_skill_pack(
        requirements={
            "description": (
                "Create a two-part clamshell enclosure with a top lid, bottom base, pin hinge, "
                "and overall dimensions 72 mm x 64 mm x 26 mm."
            )
        },
        latest_validation={},
        latest_write_health={"tool": "execute_build123d"},
    )

    split_skill = next(
        item
        for item in skills
        if item["skill_id"] == "clamshell_split_axis_and_hinge_contract"
    )
    guidance = "\n".join(split_skill["guidance"])

    assert "share the same outer width/depth footprint" in guidance
    assert "split the parts only along the closure axis" in guidance
    assert "`Cylinder(...)` points along +Z by default" in guidance
    assert "hinge seam location from the hinge axis direction" in guidance
    assert "`Cylinder(...)` is centered along its own axis by default" in guidance
    assert "do not let the hinge barrel or pin protrude outside the declared bounding box" in guidance
    assert "treat that as a two-part target by default" in guidance
    assert "Only keep hinge pins, hinge barrels, or other hinge hardware as detached shapes" in guidance
    assert "prefer `Compound([base.part, lid.part])`" in guidance


def test_build_runtime_skill_pack_frontloads_enclosure_first_write_skills() -> None:
    skills = build_runtime_skill_pack(
        requirements={
            "description": (
                "Create a two-part rounded clamshell enclosure with a hollow base and lid, "
                "overall dimensions 78 mm x 56 mm x 32 mm, "
                "four corner magnet recesses, a front thumb notch, two shallow earphone cavities, "
                "and one side plug pocket."
            )
        },
        latest_validation={},
        latest_write_health={},
    )

    skill_ids = [item["skill_id"] for item in skills[:5]]

    assert skill_ids[:3] == [
        "execute_build123d_minimal_script_hygiene",
        "nested_hollow_section_builder_native_cavity",
        "enclosure_local_feature_placement_contract",
    ]
    assert "multi_part_assembled_pose_bbox_contract" in skill_ids
    assert "clamshell_split_axis_and_hinge_contract" in skill_ids


def test_build_runtime_skill_pack_frontloads_buildsketch_and_transform_hygiene() -> None:
    skills = build_runtime_skill_pack(
        requirements={
            "description": "Create a two-part rounded clamshell enclosure with a hollow base and lid."
        },
        latest_validation={},
        latest_write_health={},
    )

    hygiene_skill = next(
        item
        for item in skills
        if item["skill_id"] == "execute_build123d_minimal_script_hygiene"
    )
    first_guidance = hygiene_skill["guidance"][:7]
    joined = "\n".join(first_guidance)

    assert "Sketch primitives such as `Circle(...)`, `Ellipse(...)`, `Rectangle(...)`, and `RegularPolygon(...)` belong inside `BuildSketch`" in joined
    assert "Do not write `with Rot(...):` or `with Pos(...):`" in joined
    assert "do not invent `Loc(...)`" in joined
    assert "do not guess `Plane(...).moved(...)`" in joined
    assert "Do not import `ocp_vscode` or call `show(...)` / `show_object(...)`" in joined
    assert "Do not invent `Box(..., radius=...)`" in joined
    assert "rounded pillbox or rounded enclosure shells" in joined
    assert "lowercase `scale(shape, by=(sx, sy, sz))`" in joined


def test_build_runtime_skill_pack_frontloads_fresh_shell_fillet_caution_for_enclosures() -> None:
    skills = build_runtime_skill_pack(
        requirements={
            "description": (
                "Create a two-part rounded clamshell enclosure with a hollow base and lid, "
                "corner magnet recesses, a thumb notch, and smooth rounded shell edges."
            )
        },
        latest_validation={},
        latest_write_health={},
    )

    enclosure_skill = next(
        item
        for item in skills
        if item["skill_id"] == "enclosure_local_feature_placement_contract"
    )
    first_guidance = enclosure_skill["guidance"][:6]
    joined = "\n".join(first_guidance)

    assert "filter_by_position(Axis.Z, ...)" in joined
    assert "RectangleRounded(...)" in joined
    assert "max_fillet(...)" in joined


def test_build_runtime_skill_pack_frontloads_code_first_local_finish_tail_contract() -> None:
    skills = build_runtime_skill_pack(
        requirements={
            "description": (
                "Create a rectangular service bracket sized 66mm x 42mm x 16mm with a shallow top "
                "pocket and two mounting holes on the bottom face. Add a centered rounded-rectangle "
                "recess on the front face sized about 12mm x 6mm and 2mm deep, plus small fillets "
                "around the top opening and countersinks on the mounting holes, so that a "
                "topology-aware local finishing pass on the front face is useful."
            )
        },
        latest_validation={},
        latest_write_health={"tool": "execute_build123d"},
    )

    skill_ids = [item["skill_id"] for item in skills[:4]]

    assert skill_ids[:2] == [
        "execute_build123d_minimal_script_hygiene",
        "code_first_local_finish_tail_contract",
    ]
    tail_skill = next(
        item
        for item in skills
        if item["skill_id"] == "code_first_local_finish_tail_contract"
    )
    guidance = "\n".join(tail_skill["guidance"])

    assert "CounterSinkHole(...)" in guidance
    assert "do not approximate countersinks with manual `Cylinder(...)` cutters" in guidance
    assert "postpone it to a later topology-guided local finish" in guidance
    assert "do not call `shift_origin((0, 0, 0))`" in guidance


def test_build_runtime_skill_pack_clamshell_guidance_mentions_two_shell_envelope_stabilization() -> None:
    skills = build_runtime_skill_pack(
        requirements={
            "description": (
                "Create a two-part rounded clamshell enclosure with lid and base, "
                "overall dimensions 78 mm x 56 mm x 32 mm, a pin hinge, corner magnet recesses, "
                "a thumb notch, and one side plug pocket."
            )
        },
        latest_validation={},
        latest_write_health={},
    )

    split_skill = next(
        item
        for item in skills
        if item["skill_id"] == "clamshell_split_axis_and_hinge_contract"
    )
    enclosure_skill = next(
        item
        for item in skills
        if item["skill_id"] == "enclosure_local_feature_placement_contract"
    )
    split_guidance = "\n".join(split_skill["guidance"])
    split_visible_guidance = "\n".join(split_skill["guidance"][:6])
    enclosure_guidance = "\n".join(enclosure_skill["guidance"])

    assert "exactly two dominant shell solids" in split_guidance
    assert "do not keep adding hinge, magnet, notch, or pocket detail" in split_guidance
    assert "Compound([base.part, lid.part])" in split_visible_guidance
    assert "extra skinny solids or tiny fragments" in enclosure_guidance
    assert "do not accept a four-solid or fused one-solid stop state" in enclosure_guidance


def test_build_runtime_skill_pack_adds_compound_children_contract_guidance() -> None:
    skills = build_runtime_skill_pack(
        requirements={
            "description": (
                "Create a clamshell enclosure with separate lid, base, and hinge parts in one assembled pose."
            )
        },
        latest_validation={},
        latest_write_health={"tool": "execute_build123d"},
        previous_tool_failure_summary={
            "tool": "execute_build123d",
            "lint_hits": [
                {
                    "rule_id": "invalid_build123d_contract.compound_positional_children_contract",
                    "message": "Compound received multiple positional child shapes.",
                }
            ],
        },
    )

    compound_skill = next(
        item
        for item in skills
        if item["skill_id"] == "execute_build123d_compound_children_contract"
    )
    guidance = "\n".join(compound_skill["guidance"])

    assert "not a variadic assembly constructor" in guidance
    assert "Compound([base_solid, lid_solid, hinge_solid])" in guidance
    assert "Do not write `Compound(base_solid, lid_solid, hinge_solid)`" in guidance


def test_build_runtime_skill_pack_adds_rotated_detached_cutter_contract_guidance() -> None:
    skills = build_runtime_skill_pack(
        requirements={
            "description": (
                "Create a clamshell enclosure with a pin hinge, magnet recesses, and a thumb notch."
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
                    "rule_id": "invalid_build123d_context.transform_context_manager",
                    "message": "Rot(...) is a transform helper, not a context manager.",
                },
            ],
        },
    )

    rotated_skill = next(
        item
        for item in skills
        if item["skill_id"] == "execute_build123d_rotated_detached_cutter_contract"
    )
    guidance = "\n".join(rotated_skill["guidance"])

    assert "Do not combine two invalid lanes in one repair" in guidance
    assert "keep the subtractive primitive inside the authoritative host builder" in guidance
    assert "orient it with `Rot(...) * solid`" in guidance
    assert "choose one valid lane instead of mixing two invalid ones" in guidance


def test_build_runtime_skill_pack_adds_clamshell_host_local_cut_contract_guidance() -> None:
    skills = build_runtime_skill_pack(
        requirements={
            "description": (
                "Create a two-part rounded clamshell enclosure with a hollow lid and base, "
                "a pin hinge, corner magnet slots, and a front thumb notch."
            )
        },
        latest_validation={},
        latest_write_health={"tool": "execute_build123d"},
        previous_tool_failure_summary={
            "tool": "execute_build123d",
            "lint_hits": [
                {
                    "rule_id": "invalid_build123d_contract.active_builder_temporary_primitive_transform_rebind",
                    "message": "Active-builder primitive was rebound with Pos(...).",
                },
                {
                    "rule_id": "invalid_build123d_context.sketch_primitive_requires_buildsketch",
                    "message": "SlotOverall was used outside BuildSketch.",
                },
            ],
            "repair_recipe": {
                "recipe_id": "clamshell_host_local_cut_contract",
                "recipe_summary": (
                    "Keep each shell host authoritative, finish host-owned local cuts before "
                    "closing it, and keep hinge solids detached and positive."
                ),
            },
        },
    )

    clamshell_skill = next(
        item
        for item in skills
        if item["skill_id"] == "execute_build123d_clamshell_host_local_cut_contract"
    )
    guidance = "\n".join(clamshell_skill["guidance"])

    assert "one authoritative `BuildPart` per shell host" in guidance
    assert "before that shell builder closes" in guidance
    assert "hinge barrels, hinge pins, and other rotated hardware as detached positive solids" in guidance
    assert "Do not reopen `with BuildPart() as lid:`" in guidance


def test_build_runtime_skill_pack_frontloads_clamshell_host_local_cut_contract_on_first_turn() -> None:
    skills = build_runtime_skill_pack(
        requirements={
            "description": (
                "Create a two-part rounded clamshell enclosure with a hollow lid and base, "
                "a pin hinge, corner magnet recesses, a front thumb notch, two shallow cavities, "
                "and one side plug pocket."
            )
        },
        latest_validation={},
        latest_write_health={},
    )

    skill_ids = [item["skill_id"] for item in skills[:8]]
    host_local_cut_skill = next(
        item
        for item in skills
        if item["skill_id"] == "execute_build123d_clamshell_host_local_cut_contract"
    )
    visible_guidance = "\n".join(host_local_cut_skill["guidance"][:6])

    assert "execute_build123d_clamshell_host_local_cut_contract" in skill_ids
    assert "never write `with Rot(...): Cylinder(...)`" in visible_guidance
    assert "already-added host geometry" in visible_guidance
    assert "hinge_barrel = Rot(...)" in visible_guidance
    assert "without `with Rot(...):`" in visible_guidance
    assert "`Rot(...) * hinge_barrel.part`" in visible_guidance


def test_build_runtime_skill_pack_surfaces_previous_failure_repair_recipe_steps() -> None:
    skills = build_runtime_skill_pack(
        requirements={"description": "make a countersunk bracket"},
        latest_validation={},
        latest_write_health={"tool": "execute_build123d"},
        previous_tool_failure_summary={
            "tool": "execute_build123d",
            "repair_recipe": {
                "recipe_id": "explicit_anchor_hole_same_builder_subtract_recipe",
                "repair_family": "explicit_anchor_hole",
                "recipe_summary": (
                    "Keep the host body authoritative and repair the countersink array through one "
                    "supported host-face lane."
                ),
                "recipe_skeleton": {
                    "mode": "subtree_rebuild_via_execute_build123d",
                    "hole_call": "CounterSinkHole_or_Hole",
                    "steps": [
                        "with BuildPart() as part: build the host body first",
                        "compute the full hole center set in the host-face coordinate frame before cutting",
                        "prefer one CounterSinkHole helper-first pass on the actual host-face plane",
                    ],
                },
            },
        },
    )

    recipe_skill = next(
        item for item in skills if item["skill_id"] == "execute_build123d_failure_recipe_focus"
    )
    guidance = "\n".join(recipe_skill["guidance"])

    assert "recipe=explicit_anchor_hole_same_builder_subtract_recipe" in guidance
    assert "repair_family=explicit_anchor_hole" in guidance
    assert "hole_call=\"CounterSinkHole_or_Hole\"" in guidance
    assert "compute the full hole center set in the host-face coordinate frame before cutting" in guidance


def test_build_runtime_skill_pack_adds_active_builder_authority_repair_guidance() -> None:
    skills = build_runtime_skill_pack(
        requirements={
            "description": "Create a two-part enclosure with a hollow base, hinge barrel, and thumb notch."
        },
        latest_validation={},
        latest_write_health={"tool": "execute_build123d"},
        previous_tool_failure_summary={
            "tool": "execute_build123d",
            "lint_hits": [
                {
                    "rule_id": "invalid_build123d_contract.active_builder_temporary_primitive_arithmetic",
                    "message": "temporary primitive arithmetic",
                }
            ],
        },
    )

    active_builder_skill = next(
        item
        for item in skills
        if item["skill_id"] == "execute_build123d_active_builder_authority_repair"
    )
    guidance = "\n".join(active_builder_skill["guidance"])

    assert "Every primitive constructor inside the active builder mutates the host immediately" in guidance
    assert "`mode=Mode.PRIVATE`" in guidance
    assert "outer envelope and inner cavity" in guidance
    assert "one closed `BuildPart` per physical part" in guidance


def test_build_runtime_skill_pack_adds_local_finish_exact_face_ref_contract_guidance() -> None:
    skills = build_runtime_skill_pack(
        requirements={
            "description": (
                "Create a rounded clamshell enclosure and finish it with a topology-aware local "
                "edit on the mating face after query_topology identifies the target host."
            )
        },
        latest_validation={
            "blockers": ["feature_target_face_subtractive_merge"],
        },
        latest_write_health={"tool": "apply_cad_action"},
    )

    local_finish_skill = next(
        item for item in skills if item["skill_id"] == "local_finish_exact_face_ref_contract"
    )
    guidance = "\n".join(local_finish_skill["guidance"])

    assert "face_ref='face:...'" in guidance
    assert "create_sketch(face_ref=...)" in guidance
    assert "plane='XY'" in guidance


def test_build_runtime_skill_pack_adds_local_finish_preserved_center_guidance() -> None:
    skills = build_runtime_skill_pack(
        requirements={
            "description": (
                "Create a bracket and finish the countersink detail on the mounting face "
                "after query_topology identifies the host face."
            )
        },
        latest_validation={
            "blockers": ["feature_countersink"],
        },
        latest_write_health={"tool": "apply_cad_action"},
        domain_kernel_digest={
            "active_feature_instances": [
                {
                    "family_id": "explicit_anchor_hole",
                    "host_ids": ["body.primary"],
                    "parameter_bindings": {
                        "expected_local_center_count": 2,
                        "realized_centers": [[-23.0, -13.0], [-23.0, 13.0]],
                    },
                }
            ]
        },
    )

    preserved_center_skill = next(
        item
        for item in skills
        if item["skill_id"] == "local_finish_preserve_existing_local_centers"
    )
    guidance = "\n".join(preserved_center_skill["guidance"])

    assert "Current preserved local centers from semantic evidence" in guidance
    assert "[-23.0, -13.0]" in guidance
    assert "reuse these exact local centers" in guidance
    assert "query_kernel_state" in guidance


def test_build_runtime_skill_pack_adds_multi_part_assembled_pose_bbox_contract() -> None:
    skills = build_runtime_skill_pack(
        requirements={
            "description": (
                "Create a two-part rounded clamshell enclosure with lid and base, "
                "a rear hinge, and overall dimensions 78mm x 56mm x 32mm."
            )
        },
        latest_validation={},
        latest_write_health={"tool": "execute_build123d"},
    )

    assembled_skill = next(
        item
        for item in skills
        if item["skill_id"] == "multi_part_assembled_pose_bbox_contract"
    )
    guidance = "\n".join(assembled_skill["guidance"])

    assert "separate solids in one assembled coordinate frame" in guidance
    assert "overall dimensions or an outer bounding box" in guidance
    assert "Do not translate the lid above the base" in guidance
    assert "centered by default" in guidance


def test_build_runtime_skill_pack_enclosure_feature_guidance_mentions_detached_fragment_signal() -> None:
    skills = build_runtime_skill_pack(
        requirements={
            "description": (
                "Create a two-part rounded clamshell enclosure with lid and base, "
                "rear hinge, magnet recesses, thumb notch, and side pocket."
            )
        },
        latest_validation={
            "blockers": ["suspected_detached_feature_fragment"],
        },
        latest_write_health={"tool": "execute_build123d"},
    )

    enclosure_skill = next(
        item
        for item in skills
        if item["skill_id"] == "enclosure_local_feature_placement_contract"
    )
    guidance = "\n".join(enclosure_skill["guidance"])

    assert "one dominant enclosure solid plus a tiny extra solid" in guidance
    assert "detached feature fragment" in guidance
    assert "stay builder-native on the host" in guidance


def test_build_runtime_skill_pack_adds_detached_subtractive_builder_repair_guidance() -> None:
    skills = build_runtime_skill_pack(
        requirements={
            "description": "Create a two-part enclosure with magnet recesses and a thumb notch."
        },
        latest_validation={},
        latest_write_health={"tool": "execute_build123d"},
        previous_tool_failure_summary={
            "tool": "execute_build123d",
            "error": (
                "Exit code: 1 | Traceback (most recent call last): RuntimeError: Nothing to subtract from"
            ),
        },
    )

    repair_skill = next(
        item
        for item in skills
        if item["skill_id"] == "execute_build123d_detached_subtractive_builder_repair"
    )
    guidance = "\n".join(repair_skill["guidance"])

    assert "Nothing to subtract from" in guidance
    assert "standalone `with BuildPart() as cutter:`" in guidance
    assert "build the cutter as a positive solid first" in guidance
    assert "result = host.part - cutter.part" in guidance


def test_build_runtime_skill_pack_adds_local_finish_exact_face_ref_contract_from_domain_kernel_family() -> None:
    skills = build_runtime_skill_pack(
        requirements={
            "description": "Create a rounded enclosure with a local edit on a named host face."
        },
        latest_validation={},
        latest_write_health={"tool": "apply_cad_action"},
        domain_kernel_digest={
            "active_feature_instances": [
                {
                    "instance_id": "instance.named_face_local_edit.primary",
                    "family_id": "named_face_local_edit",
                    "status": "active",
                }
            ]
        },
    )

    skill_ids = {item["skill_id"] for item in skills}

    assert "local_finish_exact_face_ref_contract" in skill_ids


def test_build_runtime_skill_pack_adds_failure_driven_exact_face_ref_retry_guidance() -> None:
    skills = build_runtime_skill_pack(
        requirements={
            "description": (
                "Create a rounded enclosure and continue with a topology-aware local finish."
            )
        },
        latest_validation={},
        latest_write_health={"tool": "apply_cad_action"},
        previous_tool_failure_summary={
            "tool": "apply_cad_action",
            "failure_kind": "apply_cad_action_contract_failure",
            "error": (
                "apply_cad_action preflight failed: create_sketch must use face_ref "
                "from latest query_topology during local_finish"
            ),
        },
    )

    retry_skill = next(
        item for item in skills if item["skill_id"] == "local_finish_retry_bind_latest_face_ref"
    )
    guidance = "\n".join(retry_skill["guidance"])

    assert "plane='XY'" in guidance
    assert "face_ref='face:...'" in guidance
    assert "mating_face" in guidance
    assert "create_sketch(face_ref=...)" in guidance


def test_build_runtime_skill_pack_adds_candidate_set_label_retry_guidance() -> None:
    skills = build_runtime_skill_pack(
        requirements={
            "description": "Create a rounded enclosure and add local features on the mating faces."
        },
        latest_validation={},
        latest_write_health={"tool": "apply_cad_action"},
        previous_tool_failure_summary={
            "tool": "apply_cad_action",
            "failure_kind": "apply_cad_action_invalid_reference",
            "error": (
                "invalid_reference: malformed face_ref 'mating_faces'; face_ref must be one "
                "concrete `face:<step>:<entity_id>` ref from the latest query_topology, not a "
                "candidate-set label or host-role alias"
            ),
        },
    )

    retry_skill = next(
        item
        for item in skills
        if item["skill_id"] == "topology_candidate_set_label_is_not_exact_ref"
    )
    guidance = "\n".join(retry_skill["guidance"])

    assert "mating_faces" in guidance
    assert "face:..." in guidance
    assert "candidate-set id" in guidance or "candidate-set" in guidance


def test_build_runtime_skill_pack_adds_explicit_anchor_helper_first_repair_guidance() -> None:
    skills = build_runtime_skill_pack(
        requirements={
            "description": "Create a plate with explicit countersunk holes on the top face."
        },
        latest_validation={},
        latest_write_health={"tool": "execute_build123d"},
        previous_tool_failure_summary={
            "tool": "execute_build123d",
            "lint_hits": [
                {
                    "rule_id": "invalid_build123d_contract.explicit_anchor_manual_cutter_requires_subtract_mode",
                    "message": "manual cutter missing subtract mode",
                }
            ],
        },
    )

    explicit_anchor_skill = next(
        item
        for item in skills
        if item["skill_id"] == "execute_build123d_explicit_anchor_helper_first_repair"
    )
    guidance = "\n".join(explicit_anchor_skill["guidance"])

    assert "CounterSinkHole(radius=..., counter_sink_radius=..., depth=..., counter_sink_angle=...)" in guidance
    assert "manual `Cylinder(...)` / `Cone(...)` cutters" in guidance
    assert "mode=Mode.SUBTRACT" in guidance
    assert "outward normal" in guidance
    assert "2D center set" in guidance


def test_build_runtime_skill_pack_proactively_adds_explicit_anchor_helper_guidance_from_repair_packet() -> None:
    skills = build_runtime_skill_pack(
        requirements={
            "description": "Create a bracket with explicit countersunk mounting holes on the top face."
        },
        latest_validation={
            "blockers": [
                "feature_countersink",
                "feature_hole_position_alignment",
            ],
            "insufficient_evidence": True,
            "repair_hints": ["query_topology", "query_feature_probes"],
        },
        latest_write_health={"tool": "execute_build123d"},
        domain_kernel_digest={
            "latest_repair_packet_family_id": "explicit_anchor_hole",
            "latest_repair_packet_recipe_id": "explicit_anchor_hole_helper_contract_fallback",
            "latest_repair_packet_repair_mode": "subtree_rebuild",
        },
    )

    explicit_anchor_skill = next(
        item
        for item in skills
        if item["skill_id"] == "execute_build123d_explicit_anchor_helper_first_repair"
    )
    guidance = "\n".join(explicit_anchor_skill["guidance"])

    assert "CounterSinkHole(radius=..., counter_sink_radius=..., depth=..., counter_sink_angle=...)" in guidance
    assert "manual `Cylinder(...)` / `Cone(...)` cutters" in guidance
    assert "preserve that count" in guidance


def test_build_runtime_skill_pack_adds_nested_hollow_section_cavity_guidance() -> None:
    skills = build_runtime_skill_pack(
        requirements={
            "description": (
                "Create a two-part rounded clamshell enclosure with a hollow base and lid."
            )
        },
        latest_validation={},
        latest_write_health={"tool": "execute_build123d"},
    )

    enclosure_skill = next(
        item
        for item in skills
        if item["skill_id"] == "nested_hollow_section_builder_native_cavity"
    )
    guidance = "\n".join(enclosure_skill["guidance"])

    assert "then mutate `outer_box -= inner_box` inside the active `BuildPart`" in guidance
    assert "`mode=Mode.SUBTRACT`" in guidance
    assert "with BuildPart() as base: Box(...); with Locations((0, 0, wall)): Box(..., mode=Mode.SUBTRACT)" in guidance
    assert "`base_block = Box(...)`, `inner = Box(...)`, `base_block - inner`, or `base.part = ...`" in guidance
    assert "result = host.part - inner_cavity" in guidance
    assert "Do not open a nested `BuildPart(mode=Mode.SUBTRACT)`" in guidance
    assert "model the lid and base in separate closed builders first" in guidance
    assert "keep the lid in its mating/closed pose" in guidance
    assert "keep hinge geometry inside the same assembled outer envelope" in guidance
    assert "Do not default to `fillet(host.edges().filter_by(GeomType.LINE), ...)`" in guidance


def test_build_runtime_skill_pack_adds_nested_hollow_section_cavity_guidance_on_first_turn() -> None:
    skills = build_runtime_skill_pack(
        requirements={
            "description": (
                "Create a two-part rounded clamshell enclosure with a hollow base and lid."
            )
        },
        latest_validation={},
        latest_write_health={},
    )

    enclosure_skill = next(
        item
        for item in skills
        if item["skill_id"] == "nested_hollow_section_builder_native_cavity"
    )
    guidance = "\n".join(enclosure_skill["guidance"])

    assert "mode=Mode.SUBTRACT" in guidance
    assert "base.part = ..." in guidance
    assert "Do not default to `fillet(host.edges().filter_by(GeomType.LINE), ...)`" in guidance


def test_build_runtime_skill_pack_adds_enclosure_local_feature_placement_contract_on_first_turn() -> None:
    skills = build_runtime_skill_pack(
        requirements={
            "description": (
                "Create a two-part rounded clamshell enclosure with a hollow base and lid, "
                "four corner magnet recesses, a front thumb notch, two shallow earphone cavities, "
                "and one side plug pocket."
            )
        },
        latest_validation={},
        latest_write_health={},
    )

    placement_skill = next(
        item
        for item in skills
        if item["skill_id"] == "enclosure_local_feature_placement_contract"
    )
    guidance = "\n".join(placement_skill["guidance"])

    assert "Stabilize the lid/base shell first" in guidance
    assert "Do not immediately fillet every broad top/bottom shell edge set" in guidance
    assert "`edges().filter_by(Axis.Z)`" in guidance
    assert "Do not open a detached `BuildPart` whose first real operation is subtractive" in guidance
    assert "For repeated magnet recesses, thumb notches, plug pockets, posts" in guidance
    assert "`Locations(...)` plus `mode=Mode.SUBTRACT/ADD`" in guidance
    assert "Do not use `Loc(...)`" in guidance
    assert "do not open `with Rot(...):` or `with Pos(...):`" in guidance
    assert "`Pos(...) * Rot(...) * solid`" in guidance
    assert "lowercase `scale(shape, by=...)`" in guidance
    assert "`SlotOverall(...)`, `Rectangle(...)`, and `Circle(...)` belong inside `BuildSketch(...)`" in guidance
    assert "`with BuildSketch(target_plane): SlotOverall(...)` and then `extrude(..., mode=Mode.SUBTRACT)`" in guidance
    assert "`SlotOverall(..., mode=Mode.SUBTRACT)`" in guidance
    assert "keep local features attached to their real physical host part" in guidance


def test_build_runtime_skill_pack_discourages_nested_buildpart_cutters_for_hole_layouts() -> None:
    skills = build_runtime_skill_pack(
        requirements={
            "description": (
                "Select the top plane, draw a 100 x 60 rectangle, extrude it to make a plate, "
                "then place countersunk holes at four explicit point coordinates."
            )
        },
        latest_validation={
            "blockers": [
                "feature_hole_position_alignment",
                "feature_local_anchor_alignment",
            ],
        },
        latest_write_health={"tool": "execute_build123d"},
    )

    hygiene_guidance = "\n".join(skills[0]["guidance"])
    positioned_hole_skill = next(
        item for item in skills if item["skill_id"] == "positioned_holes_on_face_workplanes"
    )
    hole_guidance = "\n".join(positioned_hole_skill["guidance"])

    assert "nested `BuildPart()` cutter" in hygiene_guidance
    assert "`part.part -= cutter.part`" in hygiene_guidance
    assert "`CounterSinkHole(...)` belongs in `BuildPart`, not `BuildSketch`" in hygiene_guidance
    assert "There is no `Workplanes(...)` helper" in hygiene_guidance
    assert "Use capitalized `Hole(...)`, not lowercase `hole(...)`" in hygiene_guidance
    assert "there is no `filter_by_direction(...)` helper" in hygiene_guidance
    assert "do not call `edge.is_parallel(Axis.Y)`" in hygiene_guidance
    assert "use lowercase `make_face()`" in hygiene_guidance
    assert "Do not instantiate a detached `Cylinder(...)` cutter inside an active `BuildPart`" in hygiene_guidance
    assert "top-face plane is at `+height/2`, not `+height`" in hygiene_guidance
    assert "sketch on `Plane.XY` and extrude upward" in hygiene_guidance
    assert "Curve helpers such as `Polyline(...)`, `Line(...)`, `CenterArc(...)`, and `RadiusArc(...)` belong inside `BuildLine`" in hygiene_guidance
    assert "For non-XY planar polygons that keep failing inside `BuildSketch`, prefer `Wire.make_polygon(...)`" in hygiene_guidance
    assert "If a `BuildSketch` only contains wire geometry from `BuildLine`" in hygiene_guidance
    assert "do not call the enclosing `BuildPart` alias's `vertices()` / `edges()` / `faces()`" in hygiene_guidance
    assert "do not invent `angle=` inside `revolve(...)`" in hygiene_guidance
    assert "Do not import `ocp_vscode` or call `show(...)` / `show_object(...)`" in hygiene_guidance
    assert "do not mix a positional edge argument with `radius=`" in hygiene_guidance
    assert "do not expect `peg = Pos(...) * peg` or `peg = Rot(...) * peg`" in hygiene_guidance
    assert "Do not assign back into `part.part` while that `BuildPart` is still open" in hygiene_guidance
    assert "Do not write `Plane.XY * (x, y, z)`" in hygiene_guidance
    assert "do not invent `Loc(...)`" in hygiene_guidance
    assert "Do not move one part aside merely for visibility" in hygiene_guidance
    assert "For explicit countersink arrays on a planar host face, prefer one `CounterSinkHole(...)` pass on the first attempt" in hygiene_guidance
    assert "same active `BuildPart`" in hole_guidance
    assert "Locations((x, y, top_z), ...)" in hole_guidance
    assert "Only fall back to an explicit same-builder cylinder+cone or revolved countersink recipe" in hole_guidance
    assert "prefer a corner-anchored host sketch/extrude" in hole_guidance
    assert "declaring `top_face_plane` or `host_plane` is not enough by itself" in hole_guidance


def test_build_runtime_skill_pack_prioritizes_clean_cylindrical_slot_boolean() -> None:
    skills = build_runtime_skill_pack(
        requirements={
            "description": (
                "Create a block and subtract one cylinder to form a semicircular slot on the top surface."
            )
        },
        latest_validation={
            "blockers": ["feature_cylindrical_slot_alignment"],
        },
        latest_write_health={"tool": "execute_build123d"},
    )

    assert skills[1]["skill_id"] == "clean_cylindrical_slot_boolean"
    guidance = "\n".join(skills[1]["guidance"])
    assert "YZ plane" in guidance
    assert "result = host.part - cutter" in guidance
    assert "Do not write `Cylinder(..., axis=...)`" in guidance


def test_build_runtime_skill_pack_path_sweep_guidance_emphasizes_single_connected_wire() -> None:
    skills = build_runtime_skill_pack(
        requirements={
            "description": (
                "Use the Sweep feature to construct a hollow bent pipe by sweeping an annular profile "
                "along an L-shaped path with a tangent arc."
            )
        },
        latest_validation={
            "blockers": ["feature_path_sweep_rail"],
        },
        latest_write_health={"tool": "execute_build123d"},
    )

    path_sweep_skill = next(
        item for item in skills if item["skill_id"] == "path_sweep_wire_profile_frame_repair"
    )
    guidance = "\n".join(path_sweep_skill["guidance"])

    assert "must start from the previous segment endpoint such as `arc @ 1`" in guidance
    assert "repair the rail continuity first" in guidance
    assert "`sweep(profile.sketch, path=path_wire)`" in guidance
    assert "one face with inner wires" in guidance
    assert "one explicit solid boolean" in guidance
    assert "rebuild the rail/profile in a stable local frame" in guidance
    assert "named front/top/side view plane" in guidance
    assert "`CenterArc(...)` with `start_angle=` and `arc_size=`" in guidance
    assert "pass plain degree numbers directly" in guidance
    assert "`DEGREE` or `DEGREES`" in guidance
    assert "`TangentArc(...)` or `JernArc(...)`" in guidance


def test_build_runtime_skill_pack_prioritizes_builder_native_spherical_recess_recipe() -> None:
    skills = build_runtime_skill_pack(
        requirements={
            "description": (
                "Draw a 50.0x50.0mm square in the XY plane and extrude it by 15.0mm to create the base. "
                "Select the top face as the reference and create a sketch for positioning the center of the recess. "
                "Draw the center point and use it as a reference to create an auxiliary plane perpendicular to the top face. "
                "On the auxiliary plane, draw a semicircle with a radius of 5.0mm and use the revolve cut command "
                "to generate the first hemispherical recess. Then use the linear pattern command with 15.0mm spacing "
                "and quantity 3 in both X and Y, centered on the face."
            )
        },
        latest_validation={},
        latest_write_health=None,
    )

    assert skills[1]["skill_id"] == "spherical_recess_pattern_code_first"
    guidance = "\n".join(skills[1]["guidance"])
    assert "Locations((x, y, top_z)" in guidance
    assert "Sphere(radius=..., mode=Mode.SUBTRACT)" in guidance
    assert "Do not subtract by mutating `part.solid`" in guidance
    assert "sphere_center_z = top_face_z" in guidance


def test_build_runtime_skill_pack_infers_centered_linear_pattern_centers() -> None:
    skills = build_runtime_skill_pack(
        requirements={
            "description": (
                "Draw a 50.0x50.0mm square in the XY plane and extrude it by 15.0mm to create the base. "
                "Use the linear pattern command, with direction 1 along the X-axis, spacing 15.0mm, and quantity 3; "
                "direction 2 along the Y-axis, spacing 15.0mm, and quantity 3. "
                "Select center the pattern so the layout is symmetrically centered on the face."
            )
        },
        latest_validation={},
        latest_write_health=None,
    )

    explicit_centers_skill = next(
        item for item in skills if item["skill_id"] == "explicit_centered_face_array_centers"
    )
    guidance = "\n".join(explicit_centers_skill["guidance"])
    assert "[-15.0, -15.0]" in guidance
    assert "[0.0, 0.0]" in guidance
    assert "[15.0, 15.0]" in guidance


def test_build_runtime_skill_pack_describes_directional_drill_plane_mapping() -> None:
    skills = build_runtime_skill_pack(
        requirements={
            "description": (
                "Create a half shell with two through-holes through the lugs in the Y direction, "
                "centered at x = -22.25 and x = 22.25 millimeters, at z = 20.0 millimeters."
            )
        },
        latest_validation={
            "blockers": [
                "feature_hole_position_alignment",
                "feature_local_anchor_alignment",
            ],
        },
        latest_write_health={"tool": "execute_build123d"},
    )

    positioned_hole_skill = next(
        item for item in skills if item["skill_id"] == "positioned_holes_on_face_workplanes"
    )
    guidance = "\n".join(positioned_hole_skill["guidance"])

    assert "Choose the workplane whose normal matches the requested drill direction" in guidance
    assert "If the requirement says the holes run in the Y direction" in guidance
    assert "use the XZ workplane so the local coordinates are `(x, z)`" in guidance


def test_build_runtime_skill_pack_describes_plane_offset_and_rotation_contract_for_directional_drills() -> None:
    skills = build_runtime_skill_pack(
        requirements={
            "description": (
                "Drill two through-holes in the Y direction at explicit x and z coordinates "
                "through the side lugs of a half shell."
            )
        },
        latest_validation={
            "blockers": [
                "feature_hole_position_alignment",
                "feature_local_anchor_alignment",
            ],
        },
        latest_write_health={"tool": "execute_build123d"},
    )

    hygiene_guidance = "\n".join(skills[0]["guidance"])
    positioned_hole_skill = next(
        item for item in skills if item["skill_id"] == "positioned_holes_on_face_workplanes"
    )
    guidance = "\n".join(positioned_hole_skill["guidance"])

    assert "`Plane.rotated(rotation, ordering=...)` only changes orientation" in hygiene_guidance
    assert "The plane origin stays where it was" in hygiene_guidance
    assert "`Plane.XZ.offset(d)` shifts along Y, not Z" in guidance
    assert "do not encode a Z coordinate with `Plane.XZ.offset(z0)`" in guidance


def test_build_runtime_skill_pack_strengthens_half_shell_builder_native_subtraction_guidance() -> None:
    skills = build_runtime_skill_pack(
        requirements={
            "description": (
                "Create a half-cylindrical shell bearing housing with a flat split surface, "
                "merge a bottom pad with two lugs, cut the bore, and drill two through-holes "
                "through the lugs in the Y direction."
            )
        },
        latest_validation={},
        latest_write_health={"tool": "execute_build123d"},
    )

    half_shell_skill = next(
        item for item in skills if item["skill_id"] == "half_shell_profile_from_semicircle_section"
    )
    guidance = "\n".join(half_shell_skill["guidance"])

    assert "same active `BuildPart`" in guidance
    assert "Cylinder(radius, extent, rotation=(90, 0, 0), mode=Mode.SUBTRACT)" in guidance
    assert "do not start from a full cylinder and split it later" in guidance.lower()
    assert "Do not guess `Circle(..., arc_size=180)`" in guidance
    assert "`Semicircle(...)` is not a Build123d helper" in guidance
    assert "prefer the lower-risk same-builder cylinder-subtract-then-intersect recipe on the first pass" in guidance
    assert "outer cylinder -> subtract inner cylinder -> intersect/trim to the half-plane -> add pad/lugs -> cut the bore -> drill the lug holes" in guidance
    assert "merge the pad/lugs, then run the bore cut on that combined host" in guidance
    assert "Do not write `outer_cyl = Cylinder(...)`" in guidance
    assert "`Cylinder(outer_radius, length)` -> `Cylinder(inner_radius, length, mode=Mode.SUBTRACT)` -> `Box(..., mode=Mode.INTERSECT)`" in guidance


def test_build_runtime_skill_pack_discourages_nested_annular_groove_band_builders() -> None:
    skills = build_runtime_skill_pack(
        requirements={
            "description": _ANNULAR_GROOVE_REQUIREMENT
        },
        latest_validation={},
        latest_write_health={"tool": "execute_build123d"},
    )

    annular_skill = next(
        item for item in skills if item["skill_id"] == "code_first_annular_band_subtraction"
    )
    guidance = "\n".join(annular_skill["guidance"])

    assert "same active `BuildPart`" in guidance
    assert "close the host and subtract the annular groove band once" in guidance
    assert "There is no `Ring(...)` helper in Build123d" in guidance


def test_build_runtime_skill_pack_strengthens_explicit_revolve_profile_recipe() -> None:
    skills = build_runtime_skill_pack(
        requirements={
            "description": (
                "Select the front plane, draw a closed profile with a stepped outline and a centerline "
                "through the origin, and revolve it 360 degrees around the center axis."
            )
        },
        latest_validation={},
        latest_write_health={"tool": "execute_build123d"},
    )

    revolve_skill = next(
        item for item in skills if item["skill_id"] == "explicit_revolve_profile_recipe"
    )
    guidance = "\n".join(revolve_skill["guidance"])

    assert "inside `BuildLine`" in guidance
    assert "call `make_face()` before revolving" in guidance
    assert "do not invent `angle=`" in guidance


def test_build_runtime_skill_pack_preserves_named_plane_mixed_section_extrude_contract() -> None:
    skills = build_runtime_skill_pack(
        requirements={"description": _ANNULAR_GROOVE_REQUIREMENT},
        latest_validation={},
        latest_write_health={"tool": "execute_build123d"},
    )

    plane_skill = next(
        item
        for item in skills
        if item["skill_id"] == "positive_extrude_from_named_plane_is_not_centered"
    )
    guidance = "\n".join(plane_skill["guidance"])

    assert "draws multiple closed section elements" in guidance
    assert "outer-circle plus inner-square/rectangle families" in guidance
    assert "centered `Cylinder(...)`" in guidance


def test_build_runtime_skill_pack_preserves_named_feature_face_for_shelled_hosts() -> None:
    skills = build_runtime_skill_pack(
        requirements={
            "description": "Create a shelled block with a shallow top-face recess and a reference hole pattern."
        },
        latest_validation={},
        latest_write_health={"tool": "execute_build123d"},
    )

    shell_face_skill = next(
        item
        for item in skills
        if item["skill_id"] == "shelled_host_preserves_named_feature_face"
    )
    guidance = "\n".join(shell_face_skill["guidance"])

    assert "do not open or remove that same target face" in guidance
    assert "open the opposite face by default" in guidance
    assert "keep the recesses, holes, or reference pattern on surviving host material" in guidance


def test_build_runtime_skill_pack_warns_that_temporary_primitives_auto_mutate_active_buildpart() -> None:
    skills = build_runtime_skill_pack(
        requirements={
            "description": (
                "Create a half-cylindrical shell bearing housing with a flat split surface, "
                "merge a bottom pad with two lugs, cut the bore, and drill two through-holes "
                "through the lugs in the Y direction."
            )
        },
        latest_validation={},
        latest_write_health={"tool": "execute_build123d"},
    )

    hygiene_guidance = "\n".join(skills[0]["guidance"])

    assert "Every primitive constructor inside an active `BuildPart` mutates that host immediately" in hygiene_guidance
    assert "temporary solid arithmetic" in hygiene_guidance


def test_build_runtime_skill_pack_surfaces_mode_private_as_safe_staging_escape_hatch() -> None:
    skills = build_runtime_skill_pack(
        requirements={
            "description": "Create a shelled block with a shallow top-face recess and a reference hole pattern."
        },
        latest_validation={},
        latest_write_health={"tool": "execute_build123d"},
    )

    hygiene_guidance = "\n".join(skills[0]["guidance"])

    assert "`mode=Mode.PRIVATE`" in hygiene_guidance
    assert "temporary staging solid" in hygiene_guidance
