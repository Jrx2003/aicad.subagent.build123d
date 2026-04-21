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

def test_preflight_lint_allows_valid_build123d_countersink_helper() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildPart() as part:\n"
            "    Box(20, 20, 8)\n"
            "    with Locations((0, 0, 4)):\n"
            "        CounterSinkHole(radius=3, counter_sink_radius=5, depth=8)\n"
            "result = part.part\n"
        ),
        session_id="test-session",
        requirement_text=_COUNTERSUNK_HOLE_ARRAY_REQUIREMENT,
        run_state=None,
    )

    assert payload is None

def test_preflight_lint_rejects_countersinkhole_inside_buildsketch_context() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildPart() as part:\n"
            "    Box(20, 20, 8)\n"
            "    with BuildSketch(Plane.XY.offset(4)):\n"
            "        CounterSinkHole(radius=3, counter_sink_radius=5, depth=8)\n"
            "result = part.part\n"
        ),
        session_id="test-session",
        requirement_text=_COUNTERSUNK_HOLE_ARRAY_REQUIREMENT,
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_context.countersinkhole_requires_buildpart" in rule_ids
    assert payload["repair_recipe"]["recipe_id"] == "explicit_anchor_hole_countersink_array_safe_recipe"

def test_preflight_lint_rejects_invalid_countersink_helper_name_and_keyword_alias() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildPart() as part:\n"
            "    Box(20, 20, 8)\n"
            "    with Locations((0, 0, 4)):\n"
            "        CountersinkHole(radius=3, countersink_radius=5, depth=8)\n"
            "result = part.part\n"
        ),
        session_id="test-session",
        requirement_text=_COUNTERSUNK_HOLE_ARRAY_REQUIREMENT,
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_api.countersink_helper_name" in rule_ids
    assert "invalid_build123d_keyword.countersink_radius_alias" in rule_ids
    assert payload["repair_recipe"]["recipe_id"] == "explicit_anchor_hole_countersink_array_safe_recipe"
    helper_hint = next(
        item["repair_hint"]
        for item in payload["lint_hits"]
        if item["rule_id"] == "invalid_build123d_api.countersink_helper_name"
    )
    assert "the exact name is `CounterSinkHole(...)`" in helper_hint
    assert "prefer one `CounterSinkHole(...)` pass first" in helper_hint
    assert "Only fall back to an explicit same-builder cone/cylinder or revolved countersink recipe" in helper_hint

def test_preflight_lint_rejects_lowercase_countersink_hole_helper_guess() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildPart() as part:\n"
            "    Box(20, 20, 8)\n"
            "    with Locations((0, 0, 4)):\n"
            "        countersink_hole(diameter=6, countersink_diameter=12)\n"
            "result = part.part\n"
        ),
        session_id="test-session",
        requirement_text=_COUNTERSUNK_HOLE_ARRAY_REQUIREMENT,
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_api.countersink_helper_name" in rule_ids
    assert payload["repair_recipe"]["recipe_id"] == "explicit_anchor_hole_countersink_array_safe_recipe"

def test_preflight_lint_rejects_bare_countersink_helper_guess() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildPart() as part:\n"
            "    Box(20, 20, 8)\n"
            "    with Locations((0, 0, 4)):\n"
            "        Hole(radius=3, depth=8)\n"
            "        Countersink(radius=6, angle=90, depth=3)\n"
            "result = part.part\n"
        ),
        session_id="test-session",
        requirement_text=_COUNTERSUNK_HOLE_ARRAY_REQUIREMENT,
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_api.countersink_helper_name" in rule_ids
    helper_hint = next(
        item["repair_hint"]
        for item in payload["lint_hits"]
        if item["rule_id"] == "invalid_build123d_api.countersink_helper_name"
    )
    assert "CounterSinkHole(...)" in helper_hint
    assert "Countersink(...)" in helper_hint

def test_preflight_lint_does_not_flag_plain_countersink_angle_variable_assignment() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "countersink_angle = 90.0\n"
            "with BuildPart() as part:\n"
            "    Box(20, 20, 8)\n"
            "result = part.part\n"
        ),
        session_id="test-session",
        requirement_text=_COUNTERSUNK_HOLE_ARRAY_REQUIREMENT,
        run_state=None,
    )

    assert payload is None

def test_preflight_lint_rejects_angle_keyword_alias_inside_countersink_helper() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildPart() as part:\n"
            "    Box(20, 20, 8)\n"
            "    with Locations((0, 0, 4)):\n"
            "        CounterSinkHole(radius=3, counter_sink_radius=5, angle=90)\n"
            "result = part.part\n"
        ),
        session_id="test-session",
        requirement_text=_COUNTERSUNK_HOLE_ARRAY_REQUIREMENT,
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_keyword.countersink_angle_alias" in rule_ids
    assert payload["repair_recipe"]["recipe_id"] == "explicit_anchor_hole_countersink_array_safe_recipe"

def test_preflight_lint_rejects_depth_keyword_alias_inside_countersink_helper() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildPart() as part:\n"
            "    Box(20, 20, 8)\n"
            "    with Locations((0, 0, 4)):\n"
            "        CounterSinkHole(radius=3, counter_sink_radius=5, counter_sink_angle=90, counter_sink_depth=2, depth=8)\n"
            "result = part.part\n"
        ),
        session_id="test-session",
        requirement_text=_COUNTERSUNK_HOLE_ARRAY_REQUIREMENT,
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_keyword.countersink_depth_alias" in rule_ids
    assert payload["repair_recipe"]["recipe_id"] == "explicit_anchor_hole_countersink_array_safe_recipe"

def test_preflight_lint_rejects_diameter_style_countersink_helper_aliases() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildPart() as part:\n"
            "    Box(100, 60, 8)\n"
            "    with Locations((-25, -15, 4), (-25, 15, 4), (25, -15, 4), (25, 15, 4)):\n"
            "        CounterSinkHole(head_diameter=12, thru_diameter=6, cone_angle=90, depth=8)\n"
            "result = part.part\n"
        ),
        session_id="test-session",
        requirement_text=_COUNTERSUNK_HOLE_ARRAY_REQUIREMENT,
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_keyword.countersink_head_diameter_alias" in rule_ids
    assert "invalid_build123d_keyword.countersink_through_diameter_alias" in rule_ids
    assert "invalid_build123d_keyword.countersink_angle_alias" in rule_ids
    assert payload["repair_recipe"]["recipe_id"] == "explicit_anchor_hole_countersink_array_safe_recipe"

def test_preflight_lint_rejects_radius_style_countersink_helper_aliases() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildPart() as part:\n"
            "    Box(100, 60, 8)\n"
            "    with Locations((25, 15, 4)):\n"
            "        CounterSinkHole(radius=3, head_radius=6, angle=90)\n"
            "result = part.part\n"
        ),
        session_id="test-session",
        requirement_text=_COUNTERSUNK_HOLE_ARRAY_REQUIREMENT,
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_keyword.countersink_head_diameter_alias" in rule_ids
    assert "invalid_build123d_keyword.countersink_angle_alias" in rule_ids
    assert payload["repair_recipe"]["recipe_id"] == "explicit_anchor_hole_countersink_array_safe_recipe"

def test_preflight_lint_prefers_explicit_anchor_hole_recipe_over_generic_make_face_contract() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildPart() as part:\n"
            "    Box(100, 60, 8)\n"
            "    with BuildSketch(Plane.XY.offset(4)):\n"
            "        with BuildLine():\n"
            "            Line((0, 0), (3, 0))\n"
            "            Line((3, 0), (3, 3))\n"
            "            Line((3, 3), (0, 3))\n"
            "            Line((0, 3), (0, 0))\n"
            "    with Locations((25, 15, 4)):\n"
            "        CounterSinkHole(radius=3, counter_sink_radius=6, angle=90, depth=8)\n"
            "result = part.part\n"
        ),
        session_id="test-session",
        requirement_text=_COUNTERSUNK_HOLE_ARRAY_REQUIREMENT,
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_keyword.countersink_angle_alias" in rule_ids
    assert "invalid_build123d_contract.buildsketch_wire_requires_make_face" in rule_ids
    assert payload["repair_recipe"]["recipe_id"] == "explicit_anchor_hole_countersink_array_safe_recipe"

def test_preflight_lint_rejects_cone_radius_aliases_for_manual_countersink_cutters() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildPart() as part:\n"
            "    Box(20, 20, 8)\n"
            "    with Locations((0, 0, 4)):\n"
            "        Cone(lower_radius=6, upper_radius=3, height=3, mode=Mode.SUBTRACT)\n"
            "result = part.part\n"
        ),
        session_id="test-session",
        requirement_text=_COUNTERSUNK_HOLE_ARRAY_REQUIREMENT,
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_keyword.cone_radius_alias" in rule_ids
    assert payload["repair_recipe"]["recipe_id"] == "explicit_anchor_hole_countersink_array_safe_recipe"

def test_preflight_lint_rejects_countersink_short_helper_name() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildPart() as part:\n"
            "    Box(20, 20, 8)\n"
            "    with Locations((0, 0, 4)):\n"
            "        CounterSink(radius=3, counter_sink_radius=5, depth=8)\n"
            "result = part.part\n"
        ),
        session_id="test-session",
        requirement_text=_COUNTERSUNK_HOLE_ARRAY_REQUIREMENT,
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_api.countersink_helper_name" in rule_ids

def test_preflight_lint_rejects_workplanes_helper_guess_for_face_local_holes() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildPart() as part:\n"
            "    Box(20, 20, 8)\n"
            "    with Workplanes(Plane.XY.offset(4)):\n"
            "        Hole(radius=3, depth=8)\n"
            "result = part.part\n"
        ),
        session_id="test-session",
        requirement_text=_COUNTERSUNK_HOLE_ARRAY_REQUIREMENT,
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_api.workplanes_helper_name" in rule_ids
    assert payload["repair_recipe"]["recipe_id"] == "explicit_anchor_hole_countersink_array_safe_recipe"

def test_preflight_lint_rejects_lowercase_hole_helper_guess() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildPart() as part:\n"
            "    Box(20, 20, 8)\n"
            "    with Locations((0, 0, 4)):\n"
            "        hole(radius=3, depth=8)\n"
            "result = part.part\n"
        ),
        session_id="test-session",
        requirement_text=_COUNTERSUNK_HOLE_ARRAY_REQUIREMENT,
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_api.lowercase_hole_helper_name" in rule_ids
    assert payload["repair_recipe"]["recipe_id"] == "explicit_anchor_hole_countersink_array_safe_recipe"

def test_preflight_lint_rejects_execute_build123d_cut_extrude_helper_guess() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildPart() as part:\n"
            "    Box(40, 30, 10)\n"
            "cut_extrude(amount=6)\n"
            "result = part.part\n"
        ),
        session_id="test-session",
        requirement_text=_CYLINDRICAL_SLOT_REQUIREMENT,
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "legacy_api.cut_extrude_helper" in rule_ids

def test_preflight_lint_routes_cut_extrude_helper_guess_for_explicit_anchor_holes_to_hole_recipe() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildPart() as part:\n"
            "    Box(100, 60, 8)\n"
            "    with BuildSketch(Plane.XY.offset(4)):\n"
            "        Circle(3)\n"
            "cut_extrude(amount=8)\n"
            "result = part.part\n"
        ),
        session_id="test-session",
        requirement_text=_COUNTERSUNK_HOLE_ARRAY_REQUIREMENT,
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "legacy_api.cut_extrude_helper" in rule_ids
    assert payload["repair_recipe"]["recipe_id"] == "explicit_anchor_hole_countersink_array_safe_recipe"
