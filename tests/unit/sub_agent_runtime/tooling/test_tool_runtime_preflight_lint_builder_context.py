import ast

from sub_agent_runtime.tooling.lint.families.planes import (
    _named_face_requirement_plane_groups,
)
from sub_agent_runtime.tooling.lint.families.structural import (
    _collect_numeric_assignment_env,
    _find_rectanglerounded_radius_bounds_hits,
)
from sub_agent_runtime.tooling.lint.plane_rules import (
    _find_named_face_plane_family_mismatch_hits,
)
from sub_agent_runtime.tooling.lint.preflight import (
    _preflight_gate_apply_cad_action,
    _preflight_lint_execute_build123d,
)
from types import SimpleNamespace

from sub_agent_runtime.turn_state import RunState, TurnToolPolicy


_CYLINDRICAL_SLOT_REQUIREMENT = (
    "Create a new part with units in millimeters. Draw a center rectangle 100.0×50.0 "
    "in the XY plane and extrude it by 20.0 to form a block. Create a cutting cylinder: "
    "radius 12.0, axis along the X-axis, cylinder centerline placed at (0,0,8.0), "
    "length set to 110.0 to cover the entire length. Perform a Boolean difference: "
    "the block as the target body and the cylinder as the tool body, resulting in "
    "a semicircular slot on the top surface."
)

_SPHERICAL_RECESS_PATTERN_REQUIREMENT = (
    "Draw a 50.0x50.0mm square in the XY plane and extrude it by 15.0mm to create "
    "the base. Select the top face as the reference and create a sketch for "
    "positioning the center of the recess. Draw the center point and use it as a "
    "reference to create an auxiliary plane perpendicular to the top face. On the "
    "auxiliary plane, draw a semicircle with a radius of 5.0mm (the diameter edge "
    "coincides with the top face) and use the revolve cut command to generate the "
    "first hemispherical recess. Then use the linear pattern command, with "
    "direction 1 along the X-axis, spacing 15.0mm, and quantity 3; direction 2 "
    "along the Y-axis, spacing 15.0mm, and quantity 3. Select \"Center the pattern\" "
    "or pre-calculate the starting position to ensure that the nine holes are "
    "completely symmetrically centered on the 50x50 face, completing the "
    "construction of the shock absorber pad."
)

_COUNTERSUNK_HOLE_ARRAY_REQUIREMENT = (
    "Select the top plane, draw a 100.0x60.0 millimeter rectangle, and extrude it "
    "by 8.0 millimeters. Use the sketch to draw four points with coordinates "
    "(25,15), (25,45), (75,15), and (75,45). Create countersunk through-holes at "
    "those four points with a through-hole diameter of 6.0 millimeters and a 90 "
    "degree countersink."
)

_HALF_SHELL_DIRECTIONAL_HOLE_REQUIREMENT = (
    "Create a half-cylindrical shell by sketching an outer semicircle of radius "
    "25.0 millimeters and an inner semicircle of radius 17.5 millimeters on the XY "
    "plane, closing the profile along the split line, and extruding it 40.0 "
    "millimeters. Add a bottom rectangular pad spanning x = -27.0 to 27.0 "
    "millimeters with a height of 8.0 millimeters, remove the inner 35.0 "
    "millimeter diameter clearance so the shell remains open above the split line "
    "and two side lugs remain outside the bore, and union this pad with the shell. "
    "At z = 20.0 millimeters, drill two 6.0 millimeter through-holes through the "
    "lugs in the Y direction, centered at x = -22.25 and x = 22.25 millimeters."
)

_ANNULAR_GROOVE_REQUIREMENT = (
    "Select the XY plane, draw a circle with a diameter of 50.0 mm and a square with "
    "a side length of 25.0 mm centered. Extrude the section by 60.0 mm. Select the "
    "front view plane, at a height of 30.0 mm, draw a 5.0 mm x 2.0 mm rectangle "
    "aligned with the edge, and use a revolved cut to create an annular groove."
)

def test_preflight_lint_rejects_bare_subtract_and_surfaces_slot_recipe() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildPart() as part:\n"
            "    Box(10, 10, 10)\n"
            "    cutter = Cylinder(radius=2, height=12)\n"
            "    subtract(cutter)\n"
            "result = part.part\n"
        ),
        session_id="test-session",
        requirement_text=_CYLINDRICAL_SLOT_REQUIREMENT,
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_api.bare_subtract_helper" in rule_ids
    assert payload["repair_recipe"]["recipe_id"] == "explicit_cylindrical_slot_boolean_safe_recipe"

def test_preflight_lint_rejects_bare_rotate_helper() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "solid = Cylinder(radius=2, height=12)\n"
            "rotate(axis=Axis.Y, angle=90)\n"
            "result = solid\n"
        ),
        session_id="test-session",
        requirement_text="make a rotated cylinder",
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_api.bare_rotate_helper" in rule_ids

def test_preflight_lint_rejects_display_only_debug_helpers() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "from ocp_vscode import show\n"
            "with BuildPart() as part:\n"
            "    Box(10, 10, 10)\n"
            "result = part.part\n"
            "show(result)\n"
        ),
        session_id="test-session",
        requirement_text="create a simple block",
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_runtime.display_only_helper_import" in rule_ids

def test_preflight_lint_rejects_buildpart_topology_access_inside_buildsketch() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildPart() as part:\n"
            "    with BuildSketch(Plane.XY):\n"
            "        Rectangle(40, 20)\n"
            "        fillet(*part.vertices(), radius=4)\n"
            "    extrude(amount=10)\n"
            "result = part.part\n"
        ),
        session_id="test-session",
        requirement_text="Create a rounded rectangular enclosure body.",
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_context.buildpart_topology_access_inside_buildsketch" in rule_ids
    assert (
        payload["repair_recipe"]["recipe_id"]
        == "build123d_buildsketch_builder_boundary_contract"
    )

def test_preflight_lint_rejects_sketch_primitive_inside_buildpart_without_buildsketch() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildPart() as part:\n"
            "    Box(78, 56, 12)\n"
            "    with Locations((0, 0, 6)):\n"
            "        Ellipse(12, 18)\n"
            "result = part.part\n"
        ),
        session_id="test-session",
        requirement_text="Create a rounded enclosure with an organic top cavity.",
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_context.sketch_primitive_requires_buildsketch" in rule_ids
    assert payload["repair_recipe"]["recipe_id"] == "build123d_sketch_primitive_builder_contract"

def test_preflight_lint_rejects_transform_helper_as_context_manager() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildPart() as part:\n"
            "    Box(40, 20, 8)\n"
            "    with Rot(90, 0, 0):\n"
            "        Cylinder(2, 50, mode=Mode.SUBTRACT)\n"
            "result = part.part\n"
        ),
        session_id="test-session",
        requirement_text="Create a bracket and add a rotated cylindrical cut.",
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_context.transform_context_manager" in rule_ids
    assert payload["repair_recipe"]["recipe_id"] == "build123d_transform_placement_contract"

def test_preflight_lint_rejects_detached_subtractive_builder_without_host() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildPart() as magnet_slots:\n"
            "    with Locations((20, 12, 4), (-20, 12, 4)):\n"
            "        Cylinder(radius=3.0, height=2.5, mode=Mode.SUBTRACT)\n"
            "result = magnet_slots.part\n"
        ),
        session_id="test-session",
        requirement_text=(
            "Create a two-part clamshell enclosure with four corner magnet recesses and a "
            "front thumb notch."
        ),
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_contract.detached_subtractive_builder_without_host" in rule_ids
    assert (
        payload["repair_recipe"]["recipe_id"]
        == "detached_subtractive_builder_without_host_contract"
    )

def test_preflight_lint_marks_clamshell_hinge_requirements_as_half_shell_family() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildPart() as magnet_slots:\n"
            "    with Locations((20, 12, 4), (-20, 12, 4)):\n"
            "        Cylinder(radius=3.0, height=2.5, mode=Mode.SUBTRACT)\n"
            "result = magnet_slots.part\n"
        ),
        session_id="test-session",
        requirement_text=(
            "Create a two-part clamshell enclosure with a top lid, bottom base, pin hinge, "
            "corner magnet recesses, and a thumb notch."
        ),
        run_state=None,
    )

    assert payload is not None
    assert "half_shell" in payload["candidate_family_ids"]

def test_preflight_lint_allows_same_builder_subtract_when_host_exists_first() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildPart() as base:\n"
            "    Box(40, 30, 12)\n"
            "    with Locations((12, 8, 6), (-12, 8, 6)):\n"
            "        Cylinder(radius=3.0, height=2.5, mode=Mode.SUBTRACT)\n"
            "result = base.part\n"
        ),
        session_id="test-session",
        requirement_text="Create a base block with two magnet recesses near the top face.",
        run_state=None,
    )

    assert payload is None or (
        "invalid_build123d_contract.detached_subtractive_builder_without_host"
        not in {item["rule_id"] for item in payload["lint_hits"]}
    )

def test_preflight_lint_rejects_compound_positional_children_contract() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildPart() as base:\n"
            "    Box(40, 30, 10)\n"
            "with BuildPart() as lid:\n"
            "    Box(40, 30, 10)\n"
            "result = Compound(base.part, lid.part)\n"
        ),
        session_id="test-session",
        requirement_text=(
            "Create a two-part clamshell enclosure with separate lid and base parts in one assembled pose."
        ),
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_contract.compound_positional_children_contract" in rule_ids
    assert payload["repair_recipe"]["recipe_id"] == "build123d_compound_children_contract"

def test_preflight_lint_rejects_case_drift_local_symbol_before_container_runtime() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "mag_z = 4.0\n"
            "with BuildPart() as part:\n"
            "    Box(40, 20, 8)\n"
            "    with Locations((0, 0, mag_Z)):\n"
            "        Cylinder(2, 10, mode=Mode.SUBTRACT)\n"
            "result = part.part\n"
        ),
        session_id="test-session",
        requirement_text="Create a box with one cylindrical recess.",
        run_state=None,
    )

    assert payload is not None
    hits = payload["lint_hits"]
    rule_ids = {item["rule_id"] for item in hits}
    assert "invalid_build123d_identifier.case_drift_local_symbol" in rule_ids
    repair_hints = "\n".join(str(item.get("repair_hint") or "") for item in hits)
    assert "`mag_Z`" in repair_hints
    assert "`mag_z`" in repair_hints

def test_preflight_lint_does_not_mistake_build123d_pos_for_local_case_drift() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "pos = (0, 0, 0)\n"
            "with BuildPart() as part:\n"
            "    Box(20, 20, 8)\n"
            "result = part.part.moved(Pos(0, 0, 4))\n"
        ),
        session_id="test-session",
        requirement_text="Move the box upward after it is built.",
        run_state=None,
    )

    if payload is None:
        return
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_identifier.case_drift_local_symbol" not in rule_ids

def test_preflight_lint_rejects_ellipse_major_minor_radius_aliases() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildPart() as part:\n"
            "    Box(78, 56, 12)\n"
            "    with BuildSketch(Plane.XY.offset(9)):\n"
            "        Ellipse(major_radius=14, minor_radius=10)\n"
            "    extrude(amount=2, mode=Mode.SUBTRACT)\n"
            "result = part.part\n"
        ),
        session_id="test-session",
        requirement_text="Create a rounded enclosure and cut one shallow organic top cavity.",
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_keyword.ellipse_major_radius_alias" in rule_ids
    assert "invalid_build123d_keyword.ellipse_minor_radius_alias" in rule_ids

def test_preflight_lint_rejects_filter_by_direction_and_surfaces_axis_filter_contract() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildPart() as part:\n"
            "    Box(60, 40, 30)\n"
            "    bottom_edges = part.edges().filter_by_position(Axis.Z, -15.1, -14.9).filter_by_direction(Axis.Y)\n"
            "    fillet(bottom_edges, radius=1)\n"
            "result = part.part\n"
        ),
        session_id="test-session",
        requirement_text="fillet the bottom outer edges parallel to the Y axis",
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_api.shapelist_filter_by_direction" in rule_ids
    assert payload["repair_recipe"]["recipe_id"] == "build123d_shapelist_axis_filter_contract"

def test_preflight_lint_rejects_edge_is_parallel_axis_and_reuses_axis_filter_contract() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildPart() as part:\n"
            "    Box(60, 40, 30)\n"
            "    bottom_edges = part.edges().filter_by_position(Axis.Z, -15.1, -14.9)\n"
            "    y_parallel_edges = [edge for edge in bottom_edges if edge.is_parallel(Axis.Y)]\n"
            "    fillet(y_parallel_edges, radius=1)\n"
            "result = part.part\n"
        ),
        session_id="test-session",
        requirement_text="fillet the bottom outer edges parallel to the Y axis",
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_api.edge_is_parallel_axis" in rule_ids
    assert payload["repair_recipe"]["recipe_id"] == "build123d_shapelist_axis_filter_contract"

def test_preflight_lint_rejects_filter_by_position_keyword_band_aliases() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildPart() as part:\n"
            "    Box(60, 40, 30)\n"
            "    top_edges = part.edges().filter_by(Axis.Z).filter_by_position(Axis.Z, ZMin=10, ZMax=15)\n"
            "    fillet(top_edges, radius=1)\n"
            "result = part.part\n"
        ),
        session_id="test-session",
        requirement_text="fillet the top edges parallel to the Z direction band",
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_api.filter_by_position_keyword_band" in rule_ids
    assert payload["repair_recipe"]["recipe_id"] == "build123d_shapelist_axis_filter_contract"

def test_preflight_lint_rejects_filter_by_position_plane_axis_argument() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildPart() as part:\n"
            "    Box(60, 40, 30)\n"
            "    top_edges = part.edges().filter_by_position(Plane.XY, 14.9, 15.1)\n"
            "    fillet(top_edges, radius=1)\n"
            "result = part.part\n"
        ),
        session_id="test-session",
        requirement_text="fillet the top opening edges after selecting the top Z band",
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_api.filter_by_position_plane_axis" in rule_ids
    assert payload["repair_recipe"]["recipe_id"] == "build123d_shapelist_axis_filter_contract"

def test_preflight_lint_rejects_member_fillet_radius_keyword_conflict() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildPart() as part:\n"
            "    Box(60, 40, 30)\n"
            "solid = part.part\n"
            "first_edge = solid.edges()[0]\n"
            "solid = solid.fillet(first_edge, radius=2.4)\n"
            "result = solid\n"
        ),
        session_id="test-session",
        requirement_text="Create a rounded block with softened outer edges.",
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_contract.member_fillet_radius_keyword_conflict" in rule_ids
    assert payload["repair_recipe"]["recipe_id"] == "build123d_fillet_member_contract"

def test_preflight_lint_rejects_global_fillet_helper_with_host_shape_argument() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "base = Box(60, 40, 30)\n"
            "top_edges = base.edges()\n"
            "rounded = fillet(base, top_edges, 1.5)\n"
            "result = rounded\n"
        ),
        session_id="test-session",
        requirement_text="Create a rounded block with softened top edges.",
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_contract.global_fillet_helper_argument_contract" in rule_ids
    assert payload["repair_recipe"]["recipe_id"] == "build123d_fillet_member_contract"

def test_preflight_lint_rejects_active_builder_cutter_primitive_for_explicit_slot_boolean() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildPart() as part:\n"
            "    with BuildSketch(Plane.XY):\n"
            "        Rectangle(100.0, 50.0)\n"
            "    extrude(amount=20.0)\n"
            "    cutter = Cylinder(radius=12.0, height=110.0)\n"
            "    cutter = cutter.rotate(Axis.Y, 90).translate((0, 0, 8.0))\n"
            "    result = part.part - cutter\n"
            "result = result\n"
        ),
        session_id="test-session",
        requirement_text=_CYLINDRICAL_SLOT_REQUIREMENT,
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_contract.active_builder_cutter_primitive_boolean" in rule_ids
    assert payload["repair_recipe"]["recipe_id"] == "explicit_cylindrical_slot_boolean_safe_recipe"

def test_preflight_lint_rejects_makeface_helper_case_and_surfaces_make_face_contract() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildPart() as part:\n"
            "    with BuildSketch(Plane.XY):\n"
            "        with BuildLine():\n"
            "            Line((0, 0), (10, 0))\n"
            "            Line((10, 0), (0, 10))\n"
            "            Line((0, 10), (0, 0))\n"
            "        MakeFace()\n"
            "    extrude(amount=5)\n"
            "result = part.part\n"
        ),
        session_id="test-session",
        requirement_text="create a triangular prism",
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_api.makeface_helper_case" in rule_ids
    assert payload["repair_recipe"]["recipe_id"] == "build123d_make_face_helper_contract"

def test_preflight_lint_rejects_curve_helpers_directly_inside_buildsketch() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildPart() as part:\n"
            "    with BuildSketch(Plane.XZ):\n"
            "        Polyline([(10, 0), (25, 0), (25, 15)], close=True)\n"
            "    revolve(axis=Axis.Z)\n"
            "result = part.part\n"
        ),
        session_id="test-session",
        requirement_text=(
            "Select the front plane, draw a stepped closed profile, and revolve it 360 degrees."
        ),
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_context.curve_requires_buildline" in rule_ids
    assert payload["repair_recipe"]["recipe_id"] == "build123d_revolve_profile_contract"

def test_preflight_lint_rejects_buildsketch_wire_profile_without_make_face() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildPart() as part:\n"
            "    with BuildSketch(Plane.XZ):\n"
            "        with BuildLine() as profile:\n"
            "            Line((0, 0), (80, 0))\n"
            "            Line((80, 0), (80, 5))\n"
            "            Line((80, 5), (0, 30))\n"
            "            Line((0, 30), (0, 0))\n"
            "    extrude(amount=40)\n"
            "result = part.part\n"
        ),
        session_id="test-session",
        requirement_text="Draw a trapezoidal profile on the front plane and extrude it into a wedge.",
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_contract.buildsketch_wire_requires_make_face" in rule_ids
    assert payload["repair_recipe"]["recipe_id"] == "build123d_make_face_helper_contract"
