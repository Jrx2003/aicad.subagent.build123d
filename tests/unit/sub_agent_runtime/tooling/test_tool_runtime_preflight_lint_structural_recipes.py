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

def test_preflight_lint_rejects_python_indentation_error() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildPart() as host:\n"
            "    Box(10, 10, 10)\n"
            " cutter = Cylinder(radius=2, height=12)\n"
            "result = host.part - cutter\n"
        ),
        session_id="test-session",
        requirement_text="make a simple boolean cut",
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "python_syntax.invalid_script" in rule_ids
    assert payload["failure_kind"] == "execute_build123d_python_syntax_failure"

def test_preflight_lint_rejects_buildpart_solid_method_arithmetic_and_surfaces_recess_recipe() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildPart() as part:\n"
            "    with BuildSketch(Plane.XY):\n"
            "        Rectangle(50, 50)\n"
            "    extrude(amount=15)\n"
            "    sphere = Sphere(radius=5)\n"
            "    part.solid = part.solid - sphere\n"
            "result = part.part\n"
        ),
        session_id="test-session",
        requirement_text=_SPHERICAL_RECESS_PATTERN_REQUIREMENT,
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_api.buildpart_solid_method_arithmetic" in rule_ids
    assert payload["repair_recipe"]["recipe_id"] == "spherical_recess_pattern_builder_subtract_recipe"

def test_preflight_lint_rejects_nested_buildpart_cutter_arithmetic_for_hole_arrays() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "plate_length = 100.0\n"
            "plate_width = 60.0\n"
            "plate_height = 8.0\n"
            "through_hole_dia = 6.0\n"
            "countersink_dia = 12.0\n"
            "cone_depth = countersink_dia / 2\n"
            "hole_positions = [(25, 15), (25, 45), (75, 15), (75, 45)]\n"
            "with BuildPart() as part:\n"
            "    Box(plate_length, plate_width, plate_height)\n"
            "    for pos in hole_positions:\n"
            "        with Locations((pos[0] - plate_length/2, pos[1] - plate_width/2, plate_height/2)):\n"
            "            with BuildPart() as countersink:\n"
            "                Cone(bottom_radius=countersink_dia/2, top_radius=through_hole_dia/2, height=cone_depth)\n"
            "            part.part -= countersink.part\n"
            "            with BuildPart() as through_hole:\n"
            "                Cylinder(radius=through_hole_dia/2, height=plate_height + 0.2)\n"
            "            part.part -= through_hole.part\n"
            "result = part.part\n"
        ),
        session_id="test-session",
        requirement_text=_COUNTERSUNK_HOLE_ARRAY_REQUIREMENT,
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_api.nested_buildpart_cutter_part_arithmetic" in rule_ids
    assert (
        payload["repair_recipe"]["recipe_id"]
        == "explicit_anchor_hole_same_builder_subtract_recipe"
    )
    recipe_steps = "\n".join(payload["repair_recipe"]["recipe_skeleton"]["steps"])
    assert "prefer one `CounterSinkHole(...)` pass first" in recipe_steps
    assert "Only fall back to an explicit same-builder cylinder+cone or revolved countersink recipe" in recipe_steps

def test_preflight_lint_surfaces_annular_groove_same_builder_recipe_for_nested_groove_band() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildPart() as part:\n"
            "    Cylinder(radius=25, height=60)\n"
            "    with BuildPart() as groove_band:\n"
            "        Cylinder(radius=25, height=2)\n"
            "    part.part -= groove_band.part\n"
            "result = part.part\n"
        ),
        session_id="test-session",
        requirement_text=_ANNULAR_GROOVE_REQUIREMENT,
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_api.nested_buildpart_cutter_part_arithmetic" in rule_ids
    assert (
        payload["repair_recipe"]["recipe_id"]
        == "annular_groove_same_builder_band_subtract_recipe"
    )

def test_preflight_lint_rejects_nested_buildpart_part_transform_inside_active_builder() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildPart() as lid:\n"
            "    Box(78, 56, 16)\n"
            "    with BuildPart() as notch_cut:\n"
            "        Box(10, 4, 4)\n"
            "    moved_notch = notch_cut.part.move(Location((0, 24, 6)))\n"
            "    lid.part = lid.part - moved_notch\n"
            "result = lid.part\n"
        ),
        session_id="test-session",
        requirement_text=(
            "Create a two-part rounded clamshell enclosure with a hollow lid, a front "
            "thumb notch, and smooth printable outer walls."
        ),
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_api.nested_buildpart_part_transform" in rule_ids
    assert (
        payload["repair_recipe"]["recipe_id"]
        == "nested_hollow_section_same_builder_subtract_contract"
    )

def test_preflight_lint_rejects_temporary_primitive_arithmetic_inside_active_buildpart() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildPart() as part:\n"
            "    outer_cyl = Cylinder(radius=25, height=40)\n"
            "    inner_cyl = Cylinder(radius=17.5, height=40)\n"
            "    full_shell = outer_cyl - inner_cyl\n"
            "    half_space_box = Box(200, 100, 200)\n"
            "    half_space_box = Pos(0, 50, 0) * half_space_box\n"
            "    half_shell = full_shell & half_space_box\n"
            "    add(half_shell)\n"
            "result = part.part\n"
        ),
        session_id="test-session",
        requirement_text=(
            "Create a half-cylindrical shell bearing housing with a flat split surface, "
            "merge a bottom pad with two lugs, cut the bore, and drill two through-holes "
            "through the lugs in the Y direction."
        ),
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_contract.active_builder_temporary_primitive_arithmetic" in rule_ids
    assert payload["repair_recipe"]["recipe_id"] == "half_shell_semi_profile_extrude_contract"

def test_preflight_lint_prefers_nested_hollow_section_same_builder_subtract_recipe() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildPart() as body:\n"
            "    outer_box = Box(78, 56, 18)\n"
            "    inner_box = Box(73.2, 51.2, 15.8)\n"
            "    inner_box = Pos(0, 0, 1.2) * inner_box\n"
            "    outer_box -= inner_box\n"
            "result = body.part\n"
        ),
        session_id="test-session",
        requirement_text="Create a hollow enclosure base body with a front notch and hinge features.",
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_contract.active_builder_temporary_primitive_arithmetic" in rule_ids
    assert (
        payload["repair_recipe"]["recipe_id"]
        == "nested_hollow_section_same_builder_subtract_contract"
    )

def test_preflight_lint_surfaces_active_builder_transform_rebind_for_temporary_primitives() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildPart() as hinge:\n"
            "    hinge_cyl = Cylinder(radius=4, height=20)\n"
            "    hinge_cyl = Rot(0, 90, 0) * hinge_cyl\n"
            "result = hinge.part\n"
        ),
        session_id="test-session",
        requirement_text="Create a hinge barrel rotated onto the X axis.",
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert (
        "invalid_build123d_contract.active_builder_temporary_primitive_transform_rebind"
        in rule_ids
    )
    assert (
        payload["repair_recipe"]["recipe_id"]
        == "active_builder_temporary_primitive_transform_contract"
    )

def test_preflight_lint_rejects_active_builder_part_mutation_inside_buildpart() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildPart() as base:\n"
            "    outer_base = Box(78, 56, 18)\n"
            "    with Locations((0, 0, 2.4)):\n"
            "        inner_base = Box(73.2, 51.2, 15.61)\n"
            "    base.part = outer_base.part - inner_base.part\n"
            "result = base.part\n"
        ),
        session_id="test-session",
        requirement_text="Create a hollow enclosure shell with wall thickness 2.4 mm.",
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_contract.active_builder_part_mutation" in rule_ids
    assert payload["repair_recipe"]["recipe_id"] == "active_builder_part_mutation_contract"

def test_preflight_lint_rejects_active_builder_part_mutation_for_direct_transformed_primitive_assignment() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildPart() as base:\n"
            "    Box(72, 64, 13)\n"
            "    cutter = Pos(24, 24, 1.0) * Cylinder(radius=2.0, height=2.0)\n"
            "    base.part = base.part - cutter\n"
            "result = base.part\n"
        ),
        session_id="test-session",
        requirement_text=(
            "Create a two-part rounded clamshell enclosure with a hollow lid and base, "
            "a pin hinge, corner magnet slots, and a front thumb notch."
        ),
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_contract.active_builder_part_mutation" in rule_ids
    assert "invalid_build123d_contract.active_builder_temporary_primitive_arithmetic" in rule_ids
    assert (
        "invalid_build123d_contract.active_builder_temporary_primitive_transform_rebind"
        in rule_ids
    )
    assert payload["repair_recipe"]["recipe_id"] == "clamshell_host_local_cut_contract"

def test_preflight_lint_rejects_plane_tuple_multiplication_for_locations() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildPart() as base:\n"
            "    Box(78, 56, 18)\n"
            "    with Locations(Plane.XY * (0, 0, 18)):\n"
            "        with BuildSketch():\n"
            "            Circle(3)\n"
            "        extrude(amount=-2, mode=Mode.SUBTRACT)\n"
            "result = base.part\n"
        ),
        session_id="test-session",
        requirement_text="Create a hollow enclosure shell with one top-face magnet recess.",
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_contract.plane_tuple_multiplication" in rule_ids
    assert (
        payload["repair_recipe"]["recipe_id"]
        == "build123d_plane_tuple_multiplication_contract"
    )

def test_preflight_lint_rejects_loc_helper_alias() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildPart() as base:\n"
            "    Box(20, 20, 8)\n"
            "moved = base.part.move(Loc((0, 0, 4)))\n"
            "result = moved\n"
        ),
        session_id="test-session",
        requirement_text="Create a box and move it upward.",
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_api.loc_helper_name" in rule_ids
    assert payload["repair_recipe"]["recipe_id"] == "build123d_location_helper_contract"

def test_preflight_lint_rejects_capitalized_scale_helper_guess() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "earphone = Sphere(10)\n"
            "earphone = Scale.by((1.2, 0.8, 0.4)) * earphone\n"
            "result = earphone\n"
        ),
        session_id="test-session",
        requirement_text="Create a softly flattened organic earphone cavity proxy by scaling a detached solid.",
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_api.scale_helper_case" in rule_ids
    assert payload["repair_recipe"]["recipe_id"] == "build123d_scale_helper_contract"

def test_preflight_lint_rejects_bare_move_helper_guess() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildPart() as lid:\n"
            "    Box(20, 20, 4)\n"
            "move(lid.part, (0, 0, 6))\n"
            "result = lid.part\n"
        ),
        session_id="test-session",
        requirement_text="Create a lid and move it upward.",
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_api.bare_move_helper" in rule_ids
    assert payload["repair_recipe"]["recipe_id"] == "build123d_location_helper_contract"

def test_preflight_lint_prefers_shell_recipe_over_loc_helper_when_builder_arithmetic_is_also_present() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildPart() as body:\n"
            "    outer_box = Box(78, 56, 18)\n"
            "    inner_box = Box(73.2, 51.2, 15.6)\n"
            "    inner_box = Pos(0, 0, 1.2) * inner_box\n"
            "    outer_box -= inner_box\n"
            "moved = body.part.move(Loc((0, 0, 2)))\n"
            "result = moved\n"
        ),
        session_id="test-session",
        requirement_text="Create a hollow enclosure base body with a front notch and hinge features.",
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_contract.active_builder_temporary_primitive_arithmetic" in rule_ids
    assert "invalid_build123d_api.loc_helper_name" in rule_ids
    assert (
        payload["repair_recipe"]["recipe_id"]
        == "nested_hollow_section_same_builder_subtract_contract"
    )

def test_preflight_lint_rejects_capitalized_split_helper_guess_for_clamshell_split() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildPart() as shell:\n"
            "    Box(40, 30, 20)\n"
            "result = Split(shell.part, Plane.XY)\n"
        ),
        session_id="test-session",
        requirement_text="Create a two-part clamshell enclosure with a lid and base.",
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_api.split_helper_case" in rule_ids
    assert payload["repair_recipe"]["recipe_id"] == "build123d_split_function_contract"

def test_preflight_lint_surfaces_explicit_anchor_hole_recipe_for_temporary_countersink_primitive_arithmetic() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildPart() as part:\n"
            "    Box(100, 60, 8, align=(Align.MIN, Align.MIN, Align.MIN))\n"
            "    cone = Cone(bottom_radius=3, top_radius=6, height=3)\n"
            "    cone = Pos(25, 15, 8) * cone\n"
            "    part.part = part.part - cone\n"
            "result = part.part\n"
        ),
        session_id="test-session",
        requirement_text=_COUNTERSUNK_HOLE_ARRAY_REQUIREMENT,
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_contract.active_builder_temporary_primitive_arithmetic" in rule_ids
    assert (
        payload["repair_recipe"]["recipe_id"]
        == "explicit_anchor_hole_same_builder_subtract_recipe"
    )

def test_preflight_lint_rejects_nested_subtractive_buildpart_inside_location_array() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "plate_length = 100.0\n"
            "plate_width = 60.0\n"
            "plate_height = 8.0\n"
            "hole_diameter = 6.0\n"
            "countersink_diameter = 12.0\n"
            "countersink_depth = (countersink_diameter - hole_diameter) / 2.0\n"
            "hole_positions = [(-25.0, -15.0), (-25.0, 15.0), (25.0, -15.0), (25.0, 15.0)]\n"
            "with BuildPart() as part:\n"
            "    Box(length=plate_length, width=plate_width, height=plate_height)\n"
            "    for pos in hole_positions:\n"
            "        with Locations((pos[0], pos[1], plate_height / 2)):\n"
            "            with BuildPart(mode=Mode.SUBTRACT) as countersink:\n"
            "                Cone(bottom_radius=countersink_diameter / 2, top_radius=hole_diameter / 2, height=countersink_depth, align=(Align.CENTER, Align.CENTER, Align.MIN))\n"
            "            with BuildPart(mode=Mode.SUBTRACT) as through_hole:\n"
            "                Cylinder(radius=hole_diameter / 2, height=plate_height, align=(Align.CENTER, Align.CENTER, Align.CENTER))\n"
            "result = part.part\n"
        ),
        session_id="test-session",
        requirement_text=_COUNTERSUNK_HOLE_ARRAY_REQUIREMENT,
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert (
        "invalid_build123d_context.nested_subtractive_buildpart_inside_active_builder"
        in rule_ids
    )
    assert (
        payload["repair_recipe"]["recipe_id"]
        == "explicit_anchor_hole_same_builder_subtract_recipe"
    )
    repair_steps = "\n".join(payload["repair_recipe"]["recipe_skeleton"]["steps"])
    assert "prefer one `CounterSinkHole(...)` pass first" in repair_steps

def test_preflight_lint_rejects_non_subtractive_manual_countersink_cutters_inside_locations() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildPart() as plate:\n"
            "    Box(100, 60, 8)\n"
            "    hole_positions = [(-25, -15), (-25, 15), (25, -15), (25, 15)]\n"
            "    for pos in hole_positions:\n"
            "        with Locations((pos[0], pos[1], 4)):\n"
            "            Cone(3, 6, 3)\n"
            "            Cylinder(3, 10)\n"
            "result = plate.part\n"
        ),
        session_id="test-session",
        requirement_text=_COUNTERSUNK_HOLE_ARRAY_REQUIREMENT,
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert (
        "invalid_build123d_contract.explicit_anchor_manual_cutter_requires_subtract_mode"
        in rule_ids
    )
    assert (
        payload["repair_recipe"]["recipe_id"]
        == "explicit_anchor_hole_same_builder_subtract_recipe"
    )

def test_preflight_lint_does_not_treat_origin_host_cylinders_as_manual_hole_cutters() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildPart() as part:\n"
            "    with Locations((0, 0, 0)):\n"
            "        Cylinder(radius=25.0, height=40.0)\n"
            "    with Locations((0, 0, 0)):\n"
            "        Cylinder(radius=17.5, height=40.0, mode=Mode.SUBTRACT)\n"
            "    with Locations((22.25, -50.0, 20.0)):\n"
            "        Cylinder(radius=3.0, height=100.0, axis=Axis.Y, mode=Mode.SUBTRACT)\n"
            "result = part.part\n"
        ),
        session_id="test-session",
        requirement_text=_HALF_SHELL_DIRECTIONAL_HOLE_REQUIREMENT,
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_keyword.cylinder_axis" in rule_ids
    assert (
        "invalid_build123d_contract.explicit_anchor_manual_cutter_requires_subtract_mode"
        not in rule_ids
    )

def test_preflight_lint_prefers_half_shell_profile_recipe_for_half_shell_temporary_primitive_arithmetic() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildPart() as part:\n"
            "    outer_cyl = Cylinder(radius=25.0, height=40.0)\n"
            "    inner_cyl = Cylinder(radius=17.5, height=40.0)\n"
            "    result = outer_cyl - inner_cyl\n"
            "result = part.part\n"
        ),
        session_id="test-session",
        requirement_text=_HALF_SHELL_DIRECTIONAL_HOLE_REQUIREMENT,
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_contract.active_builder_temporary_primitive_arithmetic" in rule_ids
    assert payload["repair_recipe"]["recipe_id"] == "half_shell_semi_profile_extrude_contract"

def test_preflight_lint_prefers_directional_hole_recipe_for_explicit_anchor_cylinder_axis() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildPart() as part:\n"
            "    Box(54.0, 25.0, 40.0)\n"
            "    with Locations((-22.25, -50.0, 20.0), (22.25, -50.0, 20.0)):\n"
            "        Cylinder(radius=3.0, height=100.0, axis=Axis.Y, mode=Mode.SUBTRACT)\n"
            "result = part.part\n"
        ),
        session_id="test-session",
        requirement_text=_HALF_SHELL_DIRECTIONAL_HOLE_REQUIREMENT,
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_keyword.cylinder_axis" in rule_ids
    assert (
        payload["repair_recipe"]["recipe_id"]
        == "explicit_anchor_directional_hole_cylinder_contract"
    )

def test_preflight_lint_prefers_structural_builder_contract_over_cylinder_axis_fix_when_lints_are_mixed() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildPart() as base:\n"
            "    Box(78, 56, 18)\n"
            "    slot_cutter = Cylinder(radius=3.0, height=20.0, axis=Axis.X)\n"
            "    base.part = base.part - slot_cutter\n"
            "result = base.part\n"
        ),
        session_id="test-session",
        requirement_text=(
            "Create a two-part rounded clamshell enclosure with a hollow base and lid, "
            "a front thumb notch, one cylindrical side slot, and a side plug pocket."
        ),
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_keyword.cylinder_axis" in rule_ids
    assert "invalid_build123d_contract.active_builder_part_mutation" in rule_ids
    assert (
        "invalid_build123d_contract.active_builder_temporary_primitive_arithmetic"
        in rule_ids
    )
    assert payload["repair_recipe"]["recipe_id"] == "active_builder_part_mutation_contract"
    repair_steps = "\n".join(payload["repair_recipe"]["recipe_skeleton"]["steps"])
    assert "close the host builder first" in repair_steps

def test_preflight_lint_prefers_clamshell_host_local_cut_recipe_for_half_shell_shells() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildPart() as lid:\n"
            "    lid_outer = Box(72, 64, 13)\n"
            "    lid_outer = Pos(0, 0, 6.5) * lid_outer\n"
            "    with Locations((0, 28, 0)):\n"
            "        SlotOverall(10, 4, mode=Mode.SUBTRACT)\n"
            "result = lid.part\n"
        ),
        session_id="test-session",
        requirement_text=(
            "Create a two-part rounded clamshell enclosure with a hollow lid and base, "
            "a pin hinge, corner magnet slots, and a front thumb notch."
        ),
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_contract.active_builder_temporary_primitive_transform_rebind" in rule_ids
    assert "invalid_build123d_context.sketch_primitive_requires_buildsketch" in rule_ids
    assert payload["repair_recipe"]["recipe_id"] == "clamshell_host_local_cut_contract"
    repair_steps = "\n".join(payload["repair_recipe"]["recipe_skeleton"]["steps"])
    assert "separate positive solids after the shell hosts close" in repair_steps

def test_preflight_lint_keeps_clamshell_contract_priority_over_nested_hollow_slot_recipe() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildPart() as base:\n"
            "    base_outer = Box(72, 64, 13)\n"
            "    base_outer = Pos(0, 0, 6.5) * base_outer\n"
            "    base.part = base_outer\n"
            "    mag_cyl = Cylinder(radius=2.0, height=2.0)\n"
            "    mag_cyl = Pos(24, 24, 1.0) * mag_cyl\n"
            "    base.part = base.part - mag_cyl\n"
            "result = base.part\n"
        ),
        session_id="test-session",
        requirement_text=(
            "Create a two-part rounded clamshell enclosure with a hollow lid and base, "
            "a pin hinge, corner magnet slots, and a front thumb notch."
        ),
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_contract.active_builder_part_mutation" in rule_ids
    assert "invalid_build123d_contract.active_builder_temporary_primitive_arithmetic" in rule_ids
    assert "invalid_build123d_contract.active_builder_temporary_primitive_transform_rebind" in rule_ids
    assert payload["repair_recipe"]["recipe_id"] == "clamshell_host_local_cut_contract"

def test_preflight_lint_keeps_clamshell_contract_priority_when_slots_family_comes_from_kernel() -> None:
    run_state = RunState(
        session_id="test-session",
        requirements={},
        feature_graph=SimpleNamespace(
            feature_instances={
                "instance.slots.feature_notch_or_profile_cut": SimpleNamespace(
                    family_id="slots"
                )
            }
        ),
    )
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildPart() as enclosure:\n"
            "    with BuildPart() as base:\n"
            "        base_outer = Box(72, 64, 13)\n"
            "        base_outer = Pos(0, 0, 6.5) * base_outer\n"
            "        base.part = base_outer\n"
            "        mag_cyl = Cylinder(radius=2.0, height=2.0)\n"
            "        mag_cyl = Pos(24, 24, 1.0) * mag_cyl\n"
            "        base.part = base.part - mag_cyl\n"
            "result = enclosure.part\n"
        ),
        session_id="test-session",
        requirement_text=(
            "Create a snap clamshell enclosure with overall dimensions 72mm x 64mm x 26mm. "
            "Use a pin hinge, keep wall thickness near 2.0mm, include two-part lid/base "
            "separation, corner magnet slots, and a thumb notch. The outer form should remain "
            "smooth and printable."
        ),
        run_state=run_state,
    )

    assert payload is not None
    assert "slots" in payload["candidate_family_ids"]
    assert "half_shell" in payload["candidate_family_ids"]
    assert payload["repair_recipe"]["recipe_id"] == "clamshell_host_local_cut_contract"
    repair_steps = "\n".join(payload["repair_recipe"]["recipe_skeleton"]["steps"])
    assert "plain `pin hinge` or `mechanical hinge` still defaults to a two-part lid/base target" in repair_steps
    assert "only detach the pin/hardware when the prompt explicitly asks for a removable pin, separate hinge parts, or an exposed hinge assembly" in repair_steps
    assert "`extrude(amount=h)` grows one-sided from the active sketch plane" in repair_steps
    assert "do not assume `Locations((0, 0, center_z))` plus `extrude(amount=h)` creates a centered shell interval" in repair_steps

def test_preflight_lint_prefers_clamshell_host_local_cut_recipe_when_nested_subtractive_builder_is_present() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildPart() as base:\n"
            "    Box(78, 56, 18)\n"
            "    with Locations((30, 0, 9)):\n"
            "        with BuildPart(mode=Mode.SUBTRACT) as pocket:\n"
            "            Box(12, 8, 4)\n"
            "moved = base.part.move(Loc((0, 0, 0)))\n"
            "result = moved\n"
        ),
        session_id="test-session",
        requirement_text=(
            "Create a two-part rounded clamshell enclosure with a hollow base and lid, "
            "one side plug pocket, a front thumb notch, and a pin hinge."
        ),
        run_state=None,
    )

    assert payload is not None
    assert "half_shell" in payload["candidate_family_ids"]
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert (
        "invalid_build123d_context.nested_subtractive_buildpart_inside_active_builder"
        in rule_ids
    )
    assert "invalid_build123d_api.loc_helper_name" in rule_ids
    assert (
        payload["repair_recipe"]["recipe_id"]
        == "clamshell_host_local_cut_contract"
    )

def test_preflight_lint_prefers_clamshell_host_local_cut_recipe_for_named_face_plane_family_mismatch() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "width = 64.0\n"
            "depth = 48.0\n"
            "height = 24.0\n"
            "wall = 2.2\n"
            "with BuildPart() as base:\n"
            "    with BuildSketch(Plane.XY):\n"
            "        RectangleRounded(width, depth, radius=8.0)\n"
            "    extrude(amount=height / 2)\n"
            "    with BuildSketch(Plane.XY.offset(wall)):\n"
            "        RectangleRounded(width - 2 * wall, depth - 2 * wall, radius=6.0)\n"
            "    extrude(amount=height / 2 - wall, mode=Mode.SUBTRACT)\n"
            "    with BuildSketch(Plane.YZ.offset(-width / 2).shift_origin((0, 0, 0))):\n"
            "        RectangleRounded(18.0, 8.0, radius=2.0)\n"
            "    extrude(amount=1.2, mode=Mode.SUBTRACT)\n"
            "result = base.part\n"
        ),
        session_id="test-session",
        requirement_text=(
            "Create a two-part rounded clamshell enclosure with a hollow lid and base, "
            "a living hinge, corner magnet recesses, and a rounded label recess on the front face."
        ),
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_contract.named_face_plane_family_mismatch" in rule_ids
    assert payload["repair_recipe"]["recipe_id"] == "clamshell_host_local_cut_contract"
    repair_steps = "\n".join(payload["repair_recipe"]["recipe_skeleton"]["steps"])
    assert "`Plane.XZ.offset(±depth/2)`" in repair_steps
    assert "wrong host plane" in repair_steps
    assert "if the requirement says `living hinge`" in repair_steps
    assert "do not create detached `hinge_barrel` or `hinge_pin` solids" in repair_steps
    assert "do not translate the whole lid or base to the back seam coordinate" in repair_steps
    assert "hinge seam location from the hinge axis direction" in repair_steps
    assert "do not reinterpret the back-edge hinge seam as a `Plane.YZ` sketch family" in repair_steps
    assert "choose one axis-orientation lane for a detached hinge cylinder" in repair_steps

def test_preflight_lint_rejects_unrotated_clamshell_hinge_cylinder_axis_mismatch() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "width = 72.0\n"
            "depth = 64.0\n"
            "split_z = 0.0\n"
            "hinge_y = -depth / 2\n"
            "with BuildPart() as base:\n"
            "    Box(width, depth, 13)\n"
            "    with Locations((0, hinge_y, split_z)):\n"
            "        Cylinder(radius=2.0, height=12.0)\n"
            "with BuildPart() as lid:\n"
            "    Box(width, depth, 13)\n"
            "    with Locations((12, hinge_y, split_z)):\n"
            "        Cylinder(radius=2.0, height=12.0)\n"
            "result = Compound([base.part, lid.part])\n"
        ),
        session_id="test-session",
        requirement_text=(
            "Create a snap clamshell enclosure with overall dimensions 72mm x 64mm x 26mm. "
            "Use a pin hinge, keep wall thickness near 2.0mm, include two-part lid/base "
            "separation, corner magnet slots, and a thumb notch. The outer form should remain "
            "smooth and printable."
        ),
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_contract.clamshell_hinge_unrotated_default_cylinder" in rule_ids
    assert payload["repair_recipe"]["recipe_id"] == "clamshell_host_local_cut_contract"
    repair_steps = "\n".join(payload["repair_recipe"]["recipe_skeleton"]["steps"])
    assert "do not drop an unrotated default `Cylinder(...)` directly onto `(x, hinge_y, split_z)`" in repair_steps
    assert "without a supported rotation/orientation lane that cylinder still runs along Z" in repair_steps

def test_preflight_lint_allows_mode_private_temporary_primitive_boolean_inside_active_buildpart() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildPart() as part:\n"
            "    Box(80, 60, 40)\n"
            "    inner_box = Box(74, 54, 37, mode=Mode.PRIVATE)\n"
            "    part.part = part.part - inner_box\n"
            "result = part.part\n"
        ),
        session_id="test-session",
        requirement_text="Create a shelled block with a shallow top-face recess and a reference hole pattern.",
        run_state=None,
    )

    assert payload is None
