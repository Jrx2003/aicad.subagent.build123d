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

def test_preflight_lint_rejects_pos_lowercase_axis_keyword() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "solid = Box(10, 10, 10)\n"
            "result = solid.located(Pos(z=30))\n"
        ),
        session_id="test-session",
        requirement_text="move the block upward",
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_keyword.pos_lowercase_axis_keyword" in rule_ids
    assert payload["repair_recipe"]["recipe_id"] == "build123d_pos_keyword_contract"

def test_preflight_lint_rejects_plane_rotated_origin_guess() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildPart() as part:\n"
            "    Box(40, 30, 20)\n"
            "    with BuildSketch(Plane.XZ.offset(0).rotated((90, 0, 0), (0, 0, 0))):\n"
            "        Circle(2)\n"
            "    extrude(amount=50, both=True, mode=Mode.SUBTRACT)\n"
            "result = part.part\n"
        ),
        session_id="test-session",
        requirement_text=(
            "Drill two through-holes in the Y direction at explicit x and z coordinates."
        ),
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_api.plane_rotated_origin_guess" in rule_ids
    assert payload["repair_recipe"]["recipe_id"] == "build123d_plane_rotation_contract"

def test_preflight_lint_rejects_plane_rotated_origin_guess_with_coordinate_expressions() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "width = 40\n"
            "with BuildPart() as part:\n"
            "    Box(40, width, 20)\n"
            "    with Locations(Plane.XY.offset(0).rotated((90, 0, 0), (0, width/2, 0))):\n"
            "        Cylinder(radius=3, height=6, mode=Mode.SUBTRACT)\n"
            "result = part.part\n"
        ),
        session_id="test-session",
        requirement_text=(
            "Cut a front thumb notch using a translated placement, not a guessed plane-rotation origin."
        ),
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_api.plane_rotated_origin_guess" in rule_ids
    assert payload["repair_recipe"]["recipe_id"] == "build123d_plane_rotation_contract"

def test_preflight_lint_rejects_plane_rotate_shape_method_guess() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "width = 66.0\n"
            "with BuildPart() as part:\n"
            "    Box(66, 42, 16)\n"
            "    with BuildSketch(Plane.YZ.offset(width/2).rotate((0, 0, 0), (1, 0, 0), 90)):\n"
            "        RectangleRounded(12, 6, radius=1.5)\n"
            "    extrude(amount=-2, mode=Mode.SUBTRACT)\n"
            "result = part.part\n"
        ),
        session_id="test-session",
        requirement_text="Create a front-face rounded recess on a service bracket.",
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_api.plane_rotate_shape_method_guess" in rule_ids
    assert payload["repair_recipe"]["recipe_id"] == "build123d_plane_rotation_contract"

def test_preflight_lint_rejects_plane_located_shape_method_guess() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "wall = 2.4\n"
            "with BuildPart() as part:\n"
            "    Box(40, 30, 20)\n"
            "    with BuildSketch(Plane.XY.located((0, 0, wall))):\n"
            "        Rectangle(20, 10)\n"
            "    extrude(amount=5, mode=Mode.SUBTRACT)\n"
            "result = part.part\n"
        ),
        session_id="test-session",
        requirement_text="Create a hollowed host with an inner sketch on an offset XY workplane.",
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_api.plane_located_shape_method_guess" in rule_ids
    assert payload["repair_recipe"]["recipe_id"] == "build123d_plane_translation_contract"

def test_preflight_lint_rejects_plane_moved_shape_method_guess() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "hinge_y = -20\n"
            "hinge_z = 8\n"
            "with BuildPart() as part:\n"
            "    Box(40, 30, 20)\n"
            "with BuildPart() as hinge:\n"
            "    with BuildSketch(Plane.YZ.moved(Location((0, hinge_y, hinge_z)))):\n"
            "        Circle(3)\n"
            "    extrude(amount=10, both=True)\n"
            "result = part.part + hinge.part\n"
        ),
        session_id="test-session",
        requirement_text="Create a rounded shell with one detached hinge barrel at the back.",
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_api.plane_moved_shape_method_guess" in rule_ids
    assert payload["repair_recipe"]["recipe_id"] == "build123d_plane_translation_contract"

def test_preflight_lint_rejects_face_plane_shift_origin_global_coordinate_guess() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildPart() as part:\n"
            "    Box(40, 30, 20)\n"
            "    front_face = part.faces().sort_by(Axis.Y)[-1]\n"
            "    with BuildSketch(Plane(front_face).shift_origin((0, 0, 7))):\n"
            "        Rectangle(12, 4)\n"
            "    extrude(amount=4, mode=Mode.SUBTRACT)\n"
            "result = part.part\n"
        ),
        session_id="test-session",
        requirement_text=(
            "Create a front thumb notch on the host face with a local sketch anchored to the face."
        ),
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_contract.face_plane_shift_origin_global_coordinate_guess" in rule_ids
    assert payload["repair_recipe"]["recipe_id"] == "build123d_face_plane_shift_origin_contract"

def test_preflight_lint_rejects_directional_drill_plane_offset_coordinate_mixup() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "HOLE_Z = 15\n"
            "with BuildPart() as part:\n"
            "    Box(40, 20, 20)\n"
            "    with BuildSketch(Plane.XZ.offset(HOLE_Z)):\n"
            "        with Locations((-10, 0), (10, 0)):\n"
            "            Circle(2)\n"
            "    extrude(amount=30, both=True, mode=Mode.SUBTRACT)\n"
            "result = part.part\n"
        ),
        session_id="test-session",
        requirement_text=(
            "Drill two through-holes in the Y direction at x = -10 mm and x = 10 mm, both at z = 15 mm."
        ),
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_contract.directional_drill_plane_offset_coordinate_mixup" in rule_ids
    assert payload["repair_recipe"]["recipe_id"] == "directional_drill_workplane_coordinate_contract"

def test_preflight_lint_allows_zero_offset_xz_workplane_for_y_direction_drill() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildPart() as part:\n"
            "    Box(40, 20, 20)\n"
            "    with BuildSketch(Plane.XZ.offset(0)):\n"
            "        with Locations((-10, 15), (10, 15)):\n"
            "            Circle(2)\n"
            "    extrude(amount=30, both=True, mode=Mode.SUBTRACT)\n"
            "result = part.part\n"
        ),
        session_id="test-session",
        requirement_text=(
            "Drill two through-holes in the Y direction at x = -10 mm and x = 10 mm, both at z = 15 mm."
        ),
        run_state=None,
    )

    assert payload is None

def test_preflight_lint_rejects_centered_box_for_plane_anchored_positive_extrude_requirement() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildPart() as part:\n"
            "    Box(length=100.0, width=50.0, height=20.0)\n"
            "result = part.part\n"
        ),
        session_id="test-session",
        requirement_text=(
            "Draw a center rectangle 100.0x50.0 in the XY plane and extrude it by 20.0 "
            "to form a block."
        ),
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert (
        "invalid_build123d_contract.centered_box_breaks_plane_anchored_positive_extrude"
        in rule_ids
    )
    assert payload["repair_recipe"]["recipe_id"] == "build123d_plane_anchored_extrude_contract"

def test_preflight_lint_rejects_full_span_face_plane_offset_on_centered_box() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "width = 66.0\n"
            "depth = 42.0\n"
            "height = 16.0\n"
            "with BuildPart() as part:\n"
            "    Box(width, depth, height)\n"
            "    with BuildSketch(Plane.XY.offset(height)):\n"
            "        Rectangle(width - 6.0, depth - 6.0)\n"
            "    extrude(amount=-3.0, mode=Mode.SUBTRACT)\n"
            "    with Locations(Plane.YZ.offset(width)):\n"
            "        with Locations((0, height / 2)):\n"
            "            CounterSinkHole(radius=2.5, counter_sink_radius=5.0, depth=height, counter_sink_angle=82)\n"
            "result = part.part\n"
        ),
        session_id="test-session",
        requirement_text=(
            "Create a rectangular bracket body, cut a shallow top pocket from the top face, "
            "and add countersunk holes on the front face of the centered block."
        ),
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_contract.centered_box_face_plane_full_span_offset" in rule_ids
    assert payload["repair_recipe"]["recipe_id"] == "explicit_anchor_hole_countersink_array_safe_recipe"

def test_preflight_lint_allows_half_span_face_plane_offset_on_centered_box() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "width = 66.0\n"
            "depth = 42.0\n"
            "height = 16.0\n"
            "with BuildPart() as part:\n"
            "    Box(width, depth, height)\n"
            "    with BuildSketch(Plane.XY.offset(height / 2)):\n"
            "        Rectangle(width - 6.0, depth - 6.0)\n"
            "    extrude(amount=-3.0, mode=Mode.SUBTRACT)\n"
            "    with Locations(Plane.YZ.offset(width / 2)):\n"
            "        pass\n"
            "result = part.part\n"
        ),
        session_id="test-session",
        requirement_text=(
            "Create a rectangular bracket body, cut a shallow top pocket from the top face, "
            "and add a front-face feature on the centered block."
        ),
        run_state=None,
    )

    rule_ids = {item["rule_id"] for item in (payload or {}).get("lint_hits", [])}
    assert "invalid_build123d_contract.centered_box_face_plane_full_span_offset" not in rule_ids

def test_preflight_lint_rejects_named_front_face_plane_family_mismatch() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "width = 66.0\n"
            "depth = 42.0\n"
            "height = 16.0\n"
            "with BuildPart() as part:\n"
            "    Box(width, depth, height)\n"
            "    with BuildSketch(Plane.XY.offset(height / 2)):\n"
            "        Rectangle(width - 6.0, depth - 6.0)\n"
            "    extrude(amount=-3.0, mode=Mode.SUBTRACT)\n"
            "    with BuildSketch(Plane.YZ.offset(-width / 2).shift_origin((0, 0, 0))):\n"
            "        RectangleRounded(12.0, 6.0, radius=1.0)\n"
            "    extrude(amount=2.0, mode=Mode.SUBTRACT)\n"
            "result = part.part\n"
        ),
        session_id="test-session",
        requirement_text=(
            "Create a rectangular bracket body, cut a shallow top pocket from the top face, "
            "and add a centered rounded-rectangle recess on the front face."
        ),
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_contract.named_face_plane_family_mismatch" in rule_ids
    assert payload["repair_recipe"]["recipe_id"] == "build123d_named_face_plane_family_contract"

def test_preflight_lint_allows_named_front_face_xz_plane_family() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "width = 66.0\n"
            "depth = 42.0\n"
            "height = 16.0\n"
            "with BuildPart() as part:\n"
            "    Box(width, depth, height)\n"
            "    with BuildSketch(Plane.XY.offset(height / 2)):\n"
            "        Rectangle(width - 6.0, depth - 6.0)\n"
            "    extrude(amount=-3.0, mode=Mode.SUBTRACT)\n"
            "    with BuildSketch(Plane.XZ.offset(-depth / 2)):\n"
            "        RectangleRounded(12.0, 6.0, radius=1.0)\n"
            "    extrude(amount=2.0, mode=Mode.SUBTRACT)\n"
            "result = part.part\n"
        ),
        session_id="test-session",
        requirement_text=(
            "Create a rectangular bracket body, cut a shallow top pocket from the top face, "
            "and add a centered rounded-rectangle recess on the front face."
        ),
        run_state=None,
    )

    rule_ids = {item["rule_id"] for item in (payload or {}).get("lint_hits", [])}
    assert "invalid_build123d_contract.named_face_plane_family_mismatch" not in rule_ids

def test_preflight_lint_ignores_bare_xy_host_profiles_when_front_face_local_edit_uses_xz() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "width = 64.0\n"
            "depth = 48.0\n"
            "base_height = 12.0\n"
            "lid_height = 10.0\n"
            "with BuildPart() as base:\n"
            "    with BuildSketch(Plane.XY):\n"
            "        RectangleRounded(width, depth, radius=8.0)\n"
            "    extrude(amount=base_height)\n"
            "with BuildPart() as lid:\n"
            "    with BuildSketch(Plane.XY):\n"
            "        RectangleRounded(width, depth, radius=8.0)\n"
            "    extrude(amount=lid_height)\n"
            "with BuildPart() as label:\n"
            "    with BuildSketch(Plane.XZ.offset(-depth / 2)):\n"
            "        RectangleRounded(40.0, 12.0, radius=2.0)\n"
            "    extrude(amount=1.0)\n"
            "result = Compound(children=[base.part, lid.part, label.part])\n"
        ),
        session_id="test-session",
        requirement_text=(
            "Create a two-part rounded pillbox enclosure and add a shallow rounded label window on the front face."
        ),
        run_state=None,
    )

    rule_ids = {item["rule_id"] for item in (payload or {}).get("lint_hits", [])}
    assert "invalid_build123d_contract.named_face_plane_family_mismatch" not in rule_ids

def test_named_face_plane_family_mismatch_only_hits_local_front_face_sketches_not_shell_profiles() -> None:
    code = (
        "from build123d import *\n"
        "width = 64.0\n"
        "depth = 48.0\n"
        "height_per_part = 12.0\n"
        "wall = 2.2\n"
        "with BuildPart() as base:\n"
        "    with BuildSketch(Plane.XY):\n"
        "        RectangleRounded(width, depth, 8.0)\n"
        "    extrude(amount=height_per_part)\n"
        "    with Locations((0, 0, wall)):\n"
        "        with BuildSketch(Plane.XY):\n"
        "            RectangleRounded(width - 2*wall, depth - 2*wall, 6.0)\n"
        "        extrude(amount=height_per_part - wall, mode=Mode.SUBTRACT)\n"
        "    with BuildSketch(Plane.YZ.offset(depth / 2)):\n"
        "        RectangleRounded(36.0, 14.0, 3.0)\n"
        "    extrude(amount=1.2, mode=Mode.SUBTRACT)\n"
        "with BuildPart() as lid:\n"
        "    with BuildSketch(Plane.XY):\n"
        "        RectangleRounded(width, depth, 8.0)\n"
        "    extrude(amount=height_per_part)\n"
        "    with Locations((0, 0, wall)):\n"
        "        with BuildSketch(Plane.XY):\n"
        "            RectangleRounded(width - 2*wall, depth - 2*wall, 6.0)\n"
        "        extrude(amount=height_per_part - wall, mode=Mode.SUBTRACT)\n"
        "    with BuildSketch(Plane.YZ.offset(depth / 2)):\n"
        "        SlotOverall(7.0, height_per_part * 0.6, rotation=90)\n"
        "    extrude(amount=3.0, mode=Mode.SUBTRACT)\n"
        "result = base.part + lid.part\n"
    )

    hits = _find_named_face_plane_family_mismatch_hits(
        ast.parse(code),
        requirement_lower=(
            "create a two-part rounded pillbox enclosure, add a shallow label window on the front face, "
            "and add a thumb notch on the front face."
        ),
    )

    assert len(hits) == 2
    assert {item["plane_name"] for item in hits} == {"YZ"}

def test_named_face_plane_family_mismatch_ignores_alias_derived_xy_host_profiles() -> None:
    code = (
        "from build123d import *\n"
        "width = 64.0\n"
        "depth = 48.0\n"
        "base_height = 14.0\n"
        "wall = 2.2\n"
        "with BuildPart() as base:\n"
        "    with BuildSketch(Plane.XY):\n"
        "        RectangleRounded(width, depth, radius=8.0)\n"
        "    extrude(amount=base_height)\n"
        "    inner_w = width - 2 * wall\n"
        "    inner_d = depth - 2 * wall\n"
        "    inner_r = max(8.0 - wall, 2.0)\n"
        "    with BuildSketch(Plane.XY.offset(wall)):\n"
        "        RectangleRounded(inner_w, inner_d, radius=inner_r)\n"
        "    extrude(amount=base_height - wall, mode=Mode.SUBTRACT)\n"
        "    with BuildSketch(Plane.YZ.offset(-depth / 2)):\n"
        "        RectangleRounded(40.0, 12.0, radius=2.0)\n"
        "    extrude(amount=1.0, mode=Mode.SUBTRACT)\n"
        "result = base.part\n"
    )

    hits = _find_named_face_plane_family_mismatch_hits(
        ast.parse(code),
        requirement_lower=(
            "create a rounded clamshell base and add a shallow label recess on the front face."
        ),
    )

    assert len(hits) == 1
    assert hits[0]["plane_name"] == "YZ"

def test_named_face_plane_family_mismatch_ignores_host_profile_aliases_derived_via_wall_thick_names() -> None:
    code = (
        "from build123d import *\n"
        "width = 64.0\n"
        "depth = 48.0\n"
        "base_height = 14.0\n"
        "lid_height = 10.0\n"
        "wall_thick = 2.2\n"
        "with BuildPart() as lid:\n"
        "    with Locations((0, 0, base_height)):\n"
        "        with BuildSketch(Plane.XY):\n"
        "            RectangleRounded(width, depth, radius=8.0)\n"
        "        extrude(amount=lid_height)\n"
        "        inner_w = width - 2 * wall_thick\n"
        "        inner_d = depth - 2 * wall_thick\n"
        "        inner_r = max(8.0 - wall_thick, 2.0)\n"
        "        with BuildSketch(Plane.XY.offset(base_height + lid_height - wall_thick)):\n"
        "            RectangleRounded(inner_w, inner_d, radius=inner_r)\n"
        "        extrude(amount=lid_height, mode=Mode.SUBTRACT)\n"
        "    with BuildSketch(Plane.YZ.offset(-depth / 2)):\n"
        "        RectangleRounded(12.0, 40.0, radius=2.0)\n"
        "    extrude(amount=1.0, mode=Mode.SUBTRACT)\n"
        "result = lid.part\n"
    )

    hits = _find_named_face_plane_family_mismatch_hits(
        ast.parse(code),
        requirement_lower=(
            "create a two-part rounded pillbox enclosure with a front face label window recess, "
            "a front thumb notch, and smooth lid/base shells."
        ),
    )

    assert len(hits) == 1
    assert hits[0]["line_no"] == 18
    assert hits[0]["plane_name"] == "YZ"

def test_named_face_requirement_plane_groups_include_mating_faces_as_top_bottom() -> None:
    groups = _named_face_requirement_plane_groups(
        "create a pillbox enclosure, add magnet recesses on the mating faces near the front corners, "
        "and add a front face label recess."
    )

    assert groups == {"front_back", "top_bottom"}

def test_named_face_plane_family_mismatch_allows_xy_mating_face_edits_when_requirement_mentions_front_face_and_mating_faces() -> None:
    code = (
        "from build123d import *\n"
        "width = 64.0\n"
        "depth = 48.0\n"
        "base_height = 12.0\n"
        "magnet_d = 1.5\n"
        "with BuildPart() as base:\n"
        "    with BuildSketch(Plane.XY):\n"
        "        RectangleRounded(width, depth, radius=6.0)\n"
        "    extrude(amount=base_height)\n"
        "    with BuildSketch(Plane.XY.offset(base_height - magnet_d)):\n"
        "        with Locations((20, depth/2 - 8), (-20, depth/2 - 8)):\n"
        "            Circle(3.0)\n"
        "    extrude(amount=magnet_d, mode=Mode.SUBTRACT)\n"
        "    with BuildSketch(Plane.XZ.offset(depth / 2)):\n"
        "        Circle(3.5)\n"
        "    extrude(amount=3.0, mode=Mode.SUBTRACT)\n"
        "result = base.part\n"
    )

    hits = _find_named_face_plane_family_mismatch_hits(
        ast.parse(code),
        requirement_lower=(
            "create a pillbox enclosure, add magnet recesses on the mating faces near the front corners, "
            "and add a front face label recess."
        ),
    )

    assert hits == []

def test_preflight_lint_rejects_rectanglerounded_radius_that_exceeds_half_of_height() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "label_w = 30.0\n"
            "label_h = 12.0\n"
            "label_r = 8.0\n"
            "with BuildPart() as part:\n"
            "    with BuildSketch(Plane.XY):\n"
            "        RectangleRounded(label_w, label_h, label_r)\n"
            "    extrude(amount=2.0)\n"
            "result = part.part\n"
        ),
        session_id="test-session",
        requirement_text="Create a small rounded label plaque.",
        run_state=None,
    )

    rule_ids = {hit["rule_id"] for hit in payload["lint_hits"]}

    assert "invalid_build123d_contract.rectanglerounded_radius_bounds" in rule_ids

def test_numeric_assignment_env_converges_when_same_name_is_reassigned_in_multiple_loops() -> None:
    code = (
        "from build123d import *\n"
        "width = 64.0\n"
        "depth = 48.0\n"
        "wall = 2.2\n"
        "for x_sign in [-1, 1]:\n"
        "    magnet_z = depth / 2 - wall\n"
        "for x_sign in [-1, 1]:\n"
        "    magnet_z = wall / 2\n"
        "with BuildPart() as part:\n"
        "    with BuildSketch(Plane.XY):\n"
        "        RectangleRounded(width, depth, radius=8.0)\n"
        "    extrude(amount=4.0)\n"
        "result = part.part\n"
    )

    tree = ast.parse(code)
    env = _collect_numeric_assignment_env(tree)
    hits = _find_rectanglerounded_radius_bounds_hits(tree)

    assert env["width"] == 64.0
    assert env["depth"] == 48.0
    assert env["magnet_z"] == 1.1
    assert hits == []

def test_preflight_lint_ignores_locations_wrapped_xy_host_profiles_when_front_face_local_edit_uses_xz() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "width = 64.0\n"
            "depth = 48.0\n"
            "base_height = 12.0\n"
            "lid_height = 10.0\n"
            "wall = 2.2\n"
            "with BuildPart() as lid:\n"
            "    with Locations((0, 0, base_height)):\n"
            "        with BuildSketch(Plane.XY):\n"
            "            RectangleRounded(width, depth, radius=8.0)\n"
            "        extrude(amount=lid_height)\n"
            "        with BuildSketch(Plane.XY.offset(wall)):\n"
            "            RectangleRounded(width - 2*wall, depth - 2*wall, radius=6.0)\n"
            "        extrude(amount=lid_height - wall, mode=Mode.SUBTRACT)\n"
            "        with BuildSketch(Plane.XZ.offset(-depth / 2)):\n"
            "            RectangleRounded(30.0, 12.0, radius=2.0)\n"
            "        extrude(amount=1.0, mode=Mode.SUBTRACT)\n"
            "result = lid.part\n"
        ),
        session_id="test-session",
        requirement_text=(
            "Create a two-part rounded pillbox enclosure and add a shallow rounded label window on the front face."
        ),
        run_state=None,
    )

    rule_ids = {item["rule_id"] for item in (payload or {}).get("lint_hits", [])}
    assert "invalid_build123d_contract.named_face_plane_family_mismatch" not in rule_ids

def test_named_face_plane_family_mismatch_ignores_detached_positive_axisymmetric_hinge_builder() -> None:
    code = (
        "from build123d import *\n"
        "width = 64.0\n"
        "depth = 48.0\n"
        "height = 24.0\n"
        "with BuildPart() as base:\n"
        "    with BuildSketch(Plane.XY):\n"
        "        RectangleRounded(width, depth, radius=8.0)\n"
        "    extrude(amount=height/2)\n"
        "with BuildPart() as hinge:\n"
        "    with BuildSketch(Plane.YZ.offset(-20.0)):\n"
        "        Circle(2.5)\n"
        "    extrude(amount=40.0)\n"
        "result = base.part + hinge.part\n"
    )

    hits = _find_named_face_plane_family_mismatch_hits(
        ast.parse(code),
        requirement_lower=(
            "create a two-part rounded pillbox enclosure with a living hinge at the back and "
            "a shallow rounded recess on the front face."
        ),
    )

    assert hits == []

def test_named_face_plane_family_mismatch_still_hits_detached_front_face_label_builder() -> None:
    code = (
        "from build123d import *\n"
        "width = 64.0\n"
        "depth = 48.0\n"
        "height = 24.0\n"
        "with BuildPart() as base:\n"
        "    with BuildSketch(Plane.XY):\n"
        "        RectangleRounded(width, depth, radius=8.0)\n"
        "    extrude(amount=height/2)\n"
        "with BuildPart() as label:\n"
        "    with BuildSketch(Plane.YZ.offset(-depth/2)):\n"
        "        RectangleRounded(18.0, 8.0, radius=2.0)\n"
        "    extrude(amount=1.2)\n"
        "result = base.part + label.part\n"
    )

    hits = _find_named_face_plane_family_mismatch_hits(
        ast.parse(code),
        requirement_lower=(
            "create a two-part rounded pillbox enclosure with a living hinge at the back and "
            "a shallow rounded recess on the front face."
        ),
    )

    assert len(hits) == 1
    assert hits[0]["plane_name"] == "YZ"

def test_named_face_plane_family_mismatch_ignores_placeholder_builder_without_materializing_ops() -> None:
    code = (
        "from build123d import *\n"
        "width = 64.0\n"
        "depth = 48.0\n"
        "base_height = 13.0\n"
        "label_depth = 1.0\n"
        "with BuildPart() as base:\n"
        "    with BuildSketch(Plane.XY):\n"
        "        RectangleRounded(width, depth, radius=8.0)\n"
        "    extrude(amount=base_height)\n"
        "with BuildPart() as label_cut:\n"
        "    with BuildSketch(Plane.YZ.offset(width/2)) as sk:\n"
        "        pass\n"
        "with BuildPart() as label_recess:\n"
        "    with BuildSketch(Plane.XZ.offset(depth/2)):\n"
        "        RectangleRounded(30.0, 12.0, radius=3.0)\n"
        "    extrude(amount=label_depth)\n"
        "result = base.part - label_recess.part\n"
    )

    hits = _find_named_face_plane_family_mismatch_hits(
        ast.parse(code),
        requirement_lower=(
            "create a two-part rounded pillbox enclosure with a living hinge at the back and "
            "a shallow rounded recess on the front face."
        ),
    )

    assert hits == []

def test_preflight_lint_routes_bottom_face_plane_family_mismatch_to_explicit_anchor_recipe() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "width = 66.0\n"
            "depth = 42.0\n"
            "height = 16.0\n"
            "with BuildPart() as part:\n"
            "    Box(width, depth, height)\n"
            "    with Locations(Plane.YZ.offset(width / 2)):\n"
            "        with GridLocations(x_spacing=50.0, y_spacing=0, x_count=2, y_count=1):\n"
            "            CounterSinkHole(radius=2.5, counter_sink_radius=4.5, depth=8.0, counter_sink_angle=90.0)\n"
            "result = part.part\n"
        ),
        session_id="test-session",
        requirement_text=(
            "Create a rectangular bracket body with two countersunk mounting holes on the bottom face."
        ),
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_contract.named_face_plane_family_mismatch" in rule_ids
    assert payload["repair_recipe"]["recipe_id"] == "explicit_anchor_hole_countersink_array_safe_recipe"
