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
    assert "action_params.face_ref='face:...'" in guidance
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
    assert "Map named enclosure faces to plane families by host normal before any local sketch or recess" in guidance
    assert "`front/back -> Plane.XZ`" in guidance
    assert "`Plane.YZ` is a side-face family" in guidance

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
    assert "`front/back -> Plane.XZ`" in hole_guidance
    assert "Only fall back to an explicit same-builder cylinder+cone or revolved countersink recipe" in hole_guidance
    assert "prefer a corner-anchored host sketch/extrude" in hole_guidance
    assert "declaring `top_face_plane` or `host_plane` is not enough by itself" in hole_guidance
