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

def test_preflight_lint_rejects_unsupported_cylinder_axis_keyword() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "result = Cylinder(radius=2, height=12, axis=Axis.X)\n"
        ),
        session_id="test-session",
        requirement_text="make a rotated cylinder",
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_keyword.cylinder_axis" in rule_ids
    assert payload["repair_recipe"]["recipe_id"] == "build123d_cylinder_axis_transform_contract"
    repair_steps = "\n".join(payload["repair_recipe"]["recipe_skeleton"]["steps"])
    assert "do not create a cylinder inside an active `BuildPart`" in repair_steps

def test_preflight_lint_rejects_unsupported_cylinder_taper_keyword() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "result = Cylinder(radius=3, height=6, taper=45)\n"
        ),
        session_id="test-session",
        requirement_text="make a countersunk cutter",
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_keyword.cylinder_taper" in rule_ids

def test_preflight_lint_rejects_unsupported_cylinder_length_keyword() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "result = Cylinder(radius=3, length=25)\n"
        ),
        session_id="test-session",
        requirement_text="make a long cylindrical cutter",
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_keyword.cylinder_length_alias" in rule_ids

def test_preflight_lint_rejects_unsupported_box_depth_keyword_and_surfaces_contract() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildPart() as part:\n"
            "    Box(width=80, depth=60, height=40)\n"
            "result = part.part\n"
        ),
        session_id="test-session",
        requirement_text="model a compact enclosure body",
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_keyword.box_depth_alias" in rule_ids
    assert payload["repair_recipe"]["recipe_id"] == "build123d_box_keyword_contract"

def test_preflight_lint_rejects_unsupported_box_radius_keyword_and_surfaces_contract() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildPart() as part:\n"
            "    Box(80, 60, 20, radius=6)\n"
            "result = part.part\n"
        ),
        session_id="test-session",
        requirement_text="model a rounded enclosure shell",
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_keyword.box_radius_alias" in rule_ids
    assert payload["repair_recipe"]["recipe_id"] == "build123d_box_keyword_contract"

def test_preflight_lint_rejects_regular_polygon_sides_alias() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildPart() as part:\n"
            "    with BuildSketch(Plane.XY):\n"
            "        RegularPolygon(radius=20.0, sides=3, major_radius=True)\n"
            "    extrude(amount=10.0)\n"
            "result = part.part\n"
        ),
        session_id="test-session",
        requirement_text=(
            "Select the XY plane. Draw an equilateral triangle and extrude it by 10.0 millimeters."
        ),
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_keyword.regular_polygon_sides_alias" in rule_ids
    assert payload["repair_recipe"]["recipe_id"] == "build123d_regular_polygon_keyword_contract"

def test_preflight_lint_rejects_extrude_direction_keyword_alias() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildPart() as part:\n"
            "    with BuildSketch(Plane.XY):\n"
            "        Rectangle(100, 50)\n"
            "    extrude(amount=20, direction=(0, 0, 1))\n"
            "result = part.part\n"
        ),
        session_id="test-session",
        requirement_text="extrude a rectangle upward",
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_keyword.extrude_direction_alias" in rule_ids

def test_preflight_lint_rejects_rectangle_length_keyword_alias() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildPart() as part:\n"
            "    with BuildSketch(Plane.XY):\n"
            "        Rectangle(width=80, length=40)\n"
            "    extrude(amount=12)\n"
            "result = part.part\n"
        ),
        session_id="test-session",
        requirement_text="extrude a rectangular plate from a centered sketch",
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_keyword.rectangle_length_alias" in rule_ids

def test_preflight_lint_rejects_slot_center_point_radius_alias() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildPart() as part:\n"
            "    with BuildSketch(Plane.XY):\n"
            "        SlotCenterPoint(center=(0, 0), point=(12, 0), height=6, radius=3)\n"
            "    extrude(amount=4)\n"
            "result = part.part\n"
        ),
        session_id="test-session",
        requirement_text="extrude a slot-shaped profile",
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_keyword.slot_center_point_radius_alias" in rule_ids

def test_preflight_lint_does_not_misclassify_unrelated_radius_names_as_slot_center_point_radius_alias() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "hole_radius = 2.0\n"
            "with BuildPart() as part:\n"
            "    with BuildSketch(Plane.XY):\n"
            "        SlotCenterPoint((0, 0), (12, 0), 6)\n"
            "    extrude(amount=4)\n"
            "    with Locations((0, 0, 2)):\n"
            "        Hole(radius=hole_radius, depth=4)\n"
            "result = part.part\n"
        ),
        session_id="test-session",
        requirement_text="extrude a slot-shaped profile with a separate hole radius variable",
        run_state=None,
    )

    if payload is None:
        return
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_keyword.slot_center_point_radius_alias" not in rule_ids

def test_preflight_lint_rejects_slot_center_point_center_point_alias() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildPart() as part:\n"
            "    Box(54, 34, 12)\n"
            "    with BuildSketch(Plane.XY):\n"
            "        SlotCenterPoint(center_point=(0, 0), point=(12, 0), height=6)\n"
            "    extrude(amount=2)\n"
            "result = part.part\n"
        ),
        session_id="test-session",
        requirement_text=(
            "Create a bracket with a rounded slot profile centered on the top face."
        ),
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_keyword.slot_center_point_center_alias" in rule_ids

def test_preflight_lint_rejects_slot_center_to_center_center_to_center_alias() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildSketch():\n"
            "    SlotCenterToCenter(center_to_center=18, height=6)\n"
        ),
        session_id="test-session",
        requirement_text="Create a rounded slot cutout with a center-to-center span.",
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_keyword.slot_center_to_center_alias" in rule_ids

def test_preflight_lint_rejects_slot_center_to_center_width_alias() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildSketch():\n"
            "    SlotCenterToCenter(center_separation=18, width=6)\n"
        ),
        session_id="test-session",
        requirement_text="Create a rounded slot cutout with a 6mm slot width.",
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_keyword.slot_center_to_center_alias" in rule_ids
