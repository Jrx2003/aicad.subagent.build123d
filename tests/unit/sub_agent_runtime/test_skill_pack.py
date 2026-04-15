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
    assert "do not invent `angle=` inside `revolve(...)`" in hygiene_guidance
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
