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
    assert "place the base outer-envelope center at `split_z - base_height/2`" in guidance
    assert "lid outer-envelope center at `split_z + lid_height/2`" in guidance
    assert "back-edge seam normally sits at `y = -depth/2`" in guidance
    assert "`Cylinder(...)` points along +Z by default" in guidance
    assert "unrotated default `Cylinder(...)` is not yet a valid hinge barrel or pin" in guidance
    assert "do not drop an unrotated default `Cylinder(...)` directly onto `(x, hinge_y, split_z)`" in guidance
    assert "without a supported rotation/orientation lane that cylinder still runs along Z" in guidance
    assert "do not reuse the seam Y coordinate as an X offset" in guidance
    assert "hinge seam location from the hinge axis direction" in guidance
    assert "`Cylinder(...)` is centered along its own axis by default" in guidance
    assert "do not let the hinge barrel or pin protrude outside the declared bounding box" in guidance
    assert "`extrude(amount=h)` grows one-sided from the active sketch plane" in guidance
    assert "do not assume `Locations((0, 0, center_z))` plus `extrude(amount=h)` creates a centered shell interval" in guidance
    assert "`RectangleRounded(width, depth, radius=...)` already uses the outer footprint spans" in guidance
    assert "do not rewrite the requested outer envelope as `width - 2*radius` / `depth - 2*radius`" in guidance
    assert "treat that as a two-part target by default" in guidance
    assert "A plain `pin hinge` or `mechanical hinge` still defaults to a two-part lid/base target" in guidance
    assert "only detach the pin/hardware when the prompt explicitly asks for a removable pin, separate hinge parts, or an exposed hinge assembly" in guidance
    assert "Only keep hinge pins, hinge barrels, or other hinge hardware as detached shapes" in guidance
    assert "prefer `Compound([base.part, lid.part])`" in guidance

def test_build_runtime_skill_pack_frontloads_living_hinge_as_host_owned_two_part_contract() -> None:
    skills = build_runtime_skill_pack(
        requirements={
            "description": (
                "Create a two-part rounded pillbox enclosure with a living hinge at the back, "
                "overall dimensions 64 mm x 48 mm x 24 mm, front magnet recesses, and a front "
                "thumb notch."
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
    visible_guidance = "\n".join(split_skill["guidance"][:6])
    all_guidance = "\n".join(split_skill["guidance"])

    assert "living hinge" in visible_guidance
    assert "integrated host-owned thin back-edge strip or flexure bridge" in visible_guidance
    assert "do not introduce detached hinge barrels, hinge pins, or extra hinge solids" in all_guidance
    assert "pin/mechanical/removable hinge" in all_guidance
    assert "do not translate the whole lid or base to the back seam coordinate" in all_guidance

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

    assert skill_ids[:4] == [
        "execute_build123d_minimal_script_hygiene",
        "execute_build123d_clamshell_host_local_cut_contract",
        "nested_hollow_section_builder_native_cavity",
        "enclosure_local_feature_placement_contract",
    ]
    assert "multi_part_assembled_pose_bbox_contract" in skill_ids
    assert "clamshell_split_axis_and_hinge_contract" in [item["skill_id"] for item in skills]

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
    assert "counter_sink_angle=..." in guidance
    assert "do not approximate countersinks with manual `Cylinder(...)` cutters" in guidance
    assert "does not provide a `Workplanes(...)` helper" in guidance
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
    assert "place the base shell center at `split_z - base_height/2`" in guidance
    assert "lid shell center at `split_z + lid_height/2`" in guidance
    assert "front opening/notch boundary at `y = +depth/2`" in guidance
    assert "before that shell builder closes" in guidance
    assert "`Plane.XZ.offset(±depth/2)`" in guidance
    assert "hinge barrels, hinge pins, and other rotated hardware as detached positive solids" in guidance
    assert "do not reinterpret the back-edge hinge seam as a `Plane.YZ` sketch family" in guidance
    assert "`Pos(0, ±depth/2, split_z) * (Rot(Y=90) * hinge_barrel.part)`" in guidance
    assert "A default `Cylinder(...)` still runs along Z" in guidance
    assert "do not plug `hinge_y` into the X position" in guidance
    assert "choose one axis-orientation lane for a detached hinge cylinder" in guidance
    assert "Do not reopen `with BuildPart() as lid:`" in guidance
    assert "notch_cutter" in guidance
    assert "mode=Mode.SUBTRACT" in guidance

def test_build_runtime_skill_pack_clamshell_guidance_forbids_detached_subtractive_notch_builders() -> None:
    skills = build_runtime_skill_pack(
        requirements={
            "description": (
                "Create a rounded clamshell pillbox enclosure with a living hinge, front thumb "
                "notch, and front label recess."
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
                }
            ],
            "repair_recipe": {
                "recipe_id": "clamshell_host_local_cut_contract",
                "recipe_summary": "Keep shell hosts authoritative and late local cuts host-owned.",
            },
        },
    )

    clamshell_skill = next(
        item
        for item in skills
        if item["skill_id"] == "execute_build123d_clamshell_host_local_cut_contract"
    )
    guidance = "\n".join(clamshell_skill["guidance"])

    assert "do not write `with BuildPart() as notch_cutter:`" in guidance
    assert "positive/private solid" in guidance

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
    all_guidance = "\n".join(host_local_cut_skill["guidance"])

    assert "execute_build123d_clamshell_host_local_cut_contract" in skill_ids
    assert "`Plane.XZ.offset(±depth/2)`" in visible_guidance
    assert "never write `with Rot(...): Cylinder(...)`" in visible_guidance
    assert "notch_cutter" in visible_guidance
    assert "already-added host geometry" in all_guidance
    assert "hinge_barrel = Rot(...)" in all_guidance
    assert "without `with Rot(...):`" in all_guidance
    assert "`Rot(...) * hinge_barrel.part`" in all_guidance
    assert "seam location from the hinge axis direction" in all_guidance
    assert "back-edge hinge seam as a `Plane.YZ` sketch family" in all_guidance
    assert "do not stack `Cylinder(..., rotation=...)` and a second `Rot(...) * hinge_barrel.part`" in all_guidance

def test_build_runtime_skill_pack_prioritizes_clamshell_host_local_cut_guidance_ahead_of_generic_enclosure_placement() -> None:
    skills = build_runtime_skill_pack(
        requirements={
            "description": (
                "Create a rounded clamshell pillbox enclosure with a living hinge, front thumb "
                "notch, front label recess, and corner magnet recesses."
            )
        },
        latest_validation={},
        latest_write_health={},
    )

    skill_ids = [item["skill_id"] for item in skills]

    assert "execute_build123d_clamshell_host_local_cut_contract" in skill_ids
    assert "enclosure_local_feature_placement_contract" in skill_ids
    assert skill_ids.index("execute_build123d_clamshell_host_local_cut_contract") < skill_ids.index(
        "enclosure_local_feature_placement_contract"
    )

def test_build_runtime_skill_pack_generic_local_finish_tail_maps_named_faces_to_plane_families() -> None:
    skills = build_runtime_skill_pack(
        requirements={
            "description": (
                "Create a centered service bracket with a top pocket, a front face recess, and "
                "countersunk mounting holes on the bottom face, while leaving any small edge "
                "fillet for a later topology-aware local finish."
            )
        },
        latest_validation={},
        latest_write_health={},
    )

    local_finish_skill = next(
        item
        for item in skills
        if item["skill_id"] == "code_first_local_finish_tail_contract"
    )
    guidance = "\n".join(local_finish_skill["guidance"])

    assert "`front/back -> Plane.XZ`" in guidance
    assert "`left/right -> Plane.YZ`" in guidance
    assert (
        "use the actual side-face workplane directly with the correct `Plane.YZ.offset(...)` "
        "or `Plane.XZ.offset(...)` translation"
    ) not in guidance

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

def test_build_runtime_skill_pack_adds_clamshell_transform_lane_guidance_after_rot_context_failure() -> None:
    skills = build_runtime_skill_pack(
        requirements={
            "description": (
                "Create a two-part rounded clamshell enclosure with a hollow lid and base, "
                "a living hinge, front thumb notch, front label recess, and corner magnet recesses."
            )
        },
        latest_validation={},
        latest_write_health={"tool": "execute_build123d"},
        previous_tool_failure_summary={
            "tool": "execute_build123d",
            "lint_hits": [
                {
                    "rule_id": "invalid_build123d_context.transform_context_manager",
                    "message": "Rot(...) is a transform helper, not a context manager.",
                }
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

    transform_skill = next(
        item
        for item in skills
        if item["skill_id"] == "execute_build123d_clamshell_transform_lane_contract"
    )
    guidance = "\n".join(transform_skill["guidance"])

    assert "`BuildSketch(Plane.XZ.offset(±depth/2))`" in guidance
    assert "do not wrap `Cylinder(...)` or `Box(...)` in `with Rot(...):`" in guidance
    assert "`Rot(...) * part`" in guidance
    assert "back-edge seam coordinate stays on Y" in guidance
    assert "do not switch to `Plane.YZ` just because the hinge sits at the back edge" in guidance
    assert "pick one axis lane for the detached hinge helper" in guidance

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

    assert "action_params.face_ref='face:...'" in guidance
    assert "do not spend `apply_cad_action` on `get_history`" in guidance
    assert (
        "prefer a direct `apply_cad_action` hole/countersink step with that exact `action_params.face_ref` before opening `create_sketch(face_ref=...)`"
        in guidance
    )
    assert "create_sketch(face_ref=...)" in guidance
    assert "After a successful `create_sketch(face_ref=...)`" in guidance
    assert "do not burn the next turn on `query_sketch`" in guidance
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
