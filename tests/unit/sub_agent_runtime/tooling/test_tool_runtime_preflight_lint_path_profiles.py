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

def test_preflight_lint_rejects_revolve_angle_keyword_alias() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildPart() as part:\n"
            "    with BuildSketch(Plane.XZ):\n"
            "        with BuildLine():\n"
            "            Line((10, 0), (25, 0))\n"
            "            Line((25, 0), (10, 20))\n"
            "            Line((10, 20), (10, 0))\n"
            "        make_face()\n"
            "    revolve(axis=Axis.Z, angle=360)\n"
            "result = part.part\n"
        ),
        session_id="test-session",
        requirement_text=(
            "Select the front plane, draw a closed profile, and revolve it 360 degrees."
        ),
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_keyword.revolve_angle_alias" in rule_ids
    assert payload["repair_recipe"]["recipe_id"] == "build123d_revolve_profile_contract"

def test_preflight_lint_rejects_circle_arc_size_and_surfaces_arc_profile_contract() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildPart() as part:\n"
            "    with BuildSketch(Plane.XZ):\n"
            "        Circle(20, arc_size=180)\n"
            "    extrude(amount=30)\n"
            "result = part.part\n"
        ),
        session_id="test-session",
        requirement_text=(
            "Create a half-cylindrical shell with a flat split line and extrude it along the length."
        ),
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_keyword.circle_arc_size" in rule_ids
    assert payload["repair_recipe"]["recipe_id"] == "build123d_arc_profile_contract"

def test_preflight_lint_rejects_circle_plus_make_face_trim_pattern_and_surfaces_arc_profile_contract() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildPart() as part:\n"
            "    with BuildSketch(Plane.YZ):\n"
            "        Circle(radius=3)\n"
            "        with BuildLine():\n"
            "            Line((-3, 0), (3, 0))\n"
            "        make_face()\n"
            "    extrude(amount=20, both=True)\n"
            "result = part.part\n"
        ),
        session_id="test-session",
        requirement_text=(
            "Create a rounded thumb notch with a semicircular profile on the front face."
        ),
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_contract.circle_make_face_trim_profile" in rule_ids
    assert payload["repair_recipe"]["recipe_id"] == "build123d_arc_profile_contract"

def test_preflight_lint_rejects_center_arc_arc_angle_alias_and_surfaces_arc_profile_contract() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildPart() as part:\n"
            "    with BuildLine() as path:\n"
            "        Line((0, 0), (50, 0))\n"
            "        CenterArc((50, 30), radius=30, start_angle=-90, arc_angle=90)\n"
            "        Line((80, 30), (80, 80))\n"
            "result = None\n"
        ),
        session_id="test-session",
        requirement_text=(
            "Use the Sweep feature to construct a hollow bent pipe from an L-shaped path with a tangent arc."
        ),
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_keyword.center_arc_arc_angle_alias" in rule_ids
    assert payload["repair_recipe"]["recipe_id"] == "build123d_path_sweep_contract"

def test_preflight_lint_rejects_center_arc_end_angle_alias_and_surfaces_arc_profile_contract() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildPart() as part:\n"
            "    with BuildLine() as profile:\n"
            "        CenterArc((0, 0), 20, start_angle=-90, end_angle=180)\n"
            "    result = None\n"
        ),
        session_id="test-session",
        requirement_text=(
            "Create a half-cylindrical shell with a semicircular arc profile and a flat split edge."
        ),
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_keyword.center_arc_end_angle_alias" in rule_ids
    assert payload["repair_recipe"]["recipe_id"] == "build123d_arc_profile_contract"

def test_preflight_lint_rejects_symbolic_degree_constants_and_surfaces_arc_profile_contract() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildLine() as profile:\n"
            "    CenterArc((0, 0), 20, -90 * DEGREES, 180 * DEGREE)\n"
            "result = None\n"
        ),
        session_id="test-session",
        requirement_text=(
            "Create a half-cylindrical shell with a semicircular arc profile and a flat split edge."
        ),
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_api.symbolic_degree_constant" in rule_ids
    assert payload["repair_recipe"]["recipe_id"] == "build123d_arc_profile_contract"

def test_preflight_lint_prefers_path_sweep_recipe_when_arc_keyword_error_appears_inside_path_sweep() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildLine() as path_builder:\n"
            "    Line((0, 0, 0), (50, 0, 0))\n"
            "    CenterArc((50, 30, 0), 30, start_angle=-90, arc_angle=90)\n"
            "    Line((80, 30, 0), (80, 80, 0))\n"
            "path_wire = path_builder.wire\n"
            "with BuildSketch(Plane.YZ) as profile:\n"
            "    Circle(10)\n"
            "    Circle(8, mode=Mode.SUBTRACT)\n"
            "with BuildPart() as pipe:\n"
            "    sweep(profile.sketch, path=path_wire)\n"
            "result = pipe.part\n"
        ),
        session_id="test-session",
        requirement_text=(
            "Use the Sweep feature to construct a hollow bent pipe by sweeping an annular profile along an L-shaped path with a tangent arc."
        ),
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_keyword.center_arc_arc_angle_alias" in rule_ids
    assert "invalid_build123d_contract.builder_method_reference_assignment" in rule_ids
    assert payload["repair_recipe"]["recipe_id"] == "build123d_path_sweep_contract"

def test_preflight_lint_rejects_symbolic_degree_constants_and_prefers_path_sweep_contract() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildLine() as path:\n"
            "    Line((0, 0, 0), (50, 0, 0))\n"
            "    CenterArc((50, 30, 0), 30, -90 * DEGREES, 90 * DEGREE)\n"
            "    Line((80, 30, 0), (80, 80, 0))\n"
            "path_wire = path.wire()\n"
            "with BuildSketch(Plane.YZ) as profile:\n"
            "    Circle(10)\n"
            "    Circle(8, mode=Mode.SUBTRACT)\n"
            "with BuildPart() as pipe:\n"
            "    sweep(profile.sketch, path=path_wire)\n"
            "result = pipe.part\n"
        ),
        session_id="test-session",
        requirement_text=(
            "Use the Sweep feature to construct a hollow bent pipe by sweeping an annular profile along an L-shaped path with a 90-degree tangent arc of radius 30mm."
        ),
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_api.symbolic_degree_constant" in rule_ids
    assert payload["repair_recipe"]["recipe_id"] == "build123d_path_sweep_contract"

def test_preflight_lint_rejects_tangent_arc_helpers_for_explicit_radius_path_sweep() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildLine() as path:\n"
            "    l1 = Line((0, 0, 0), (50, 0, 0))\n"
            "    arc = TangentArc(l1 @ 1, (50, 80, 0), tangent=(0, 1, 0))\n"
            "    l2 = Line(arc @ 1, (0, 80, 0))\n"
            "path_wire = path.wire()\n"
            "with BuildSketch(Plane.YZ) as profile:\n"
            "    Circle(10)\n"
            "    Circle(8, mode=Mode.SUBTRACT)\n"
            "with BuildPart() as pipe:\n"
            "    sweep(profile.sketch, path=path_wire)\n"
            "result = pipe.part\n"
        ),
        session_id="test-session",
        requirement_text=(
            "Use the Sweep feature to construct a hollow bent pipe. First draw a path with a "
            "50mm straight line, a 90-degree tangent arc with a radius of 30mm, and another "
            "50mm straight line, then sweep an annular profile along that path."
        ),
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_contract.explicit_radius_arc_prefers_center_arc" in rule_ids
    assert payload["repair_recipe"]["recipe_id"] == "build123d_path_sweep_contract"

def test_preflight_lint_rejects_sweep_path_wire_method_reference_and_surfaces_path_sweep_contract() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildLine() as path:\n"
            "    Line((0, 0), (50, 0))\n"
            "with BuildSketch(Plane.YZ) as profile:\n"
            "    Circle(10)\n"
            "    Circle(8, mode=Mode.SUBTRACT)\n"
            "with BuildPart() as pipe:\n"
            "    sweep(profile.sketch, path=path.wire)\n"
            "result = pipe.part\n"
        ),
        session_id="test-session",
        requirement_text=(
            "Use the Sweep feature to construct a hollow bent pipe by sweeping an annular profile along a path."
        ),
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_contract.sweep_path_wire_method_reference" in rule_ids
    assert payload["repair_recipe"]["recipe_id"] == "build123d_path_sweep_contract"

def test_preflight_lint_rejects_sweep_path_line_alias_and_surfaces_path_sweep_contract() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildLine() as path:\n"
            "    Line((0, 0), (50, 0))\n"
            "    Line((50, 0), (80, 30))\n"
            "with BuildSketch(Plane.YZ) as profile:\n"
            "    Circle(10)\n"
            "with BuildPart() as pipe:\n"
            "    sweep(profile.sketch, path=path.line)\n"
            "result = pipe.part\n"
        ),
        session_id="test-session",
        requirement_text=(
            "Use the Sweep feature to construct a hollow bent pipe by sweeping a profile along an L-shaped path."
        ),
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_contract.sweep_path_line_alias" in rule_ids
    assert payload["repair_recipe"]["recipe_id"] == "build123d_path_sweep_contract"

def test_preflight_lint_rejects_center_arc_without_start_angle_and_keeps_path_sweep_contract() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildLine() as path:\n"
            "    Line((0, 0), (50, 0))\n"
            "    CenterArc((50, 30), 30, arc_size=90)\n"
            "result = None\n"
        ),
        session_id="test-session",
        requirement_text=(
            "Use the Sweep feature to construct a bent pipe with a tangent arc path."
        ),
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_contract.center_arc_missing_start_angle" in rule_ids
    assert payload["repair_recipe"]["recipe_id"] == "build123d_path_sweep_contract"

def test_preflight_lint_rejects_sweep_section_keyword_alias_and_surfaces_path_sweep_contract() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildLine() as path:\n"
            "    Line((0, 0), (50, 0))\n"
            "with BuildSketch(Plane.YZ) as profile:\n"
            "    Circle(10)\n"
            "    Circle(8, mode=Mode.SUBTRACT)\n"
            "with BuildPart() as pipe:\n"
            "    sweep(section=profile.sketch, path=path.line)\n"
            "result = pipe.part\n"
        ),
        session_id="test-session",
        requirement_text=(
            "Use the Sweep feature to construct a hollow bent pipe by sweeping an annular profile along a path."
        ),
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_keyword.sweep_section_alias" in rule_ids
    assert payload["repair_recipe"]["recipe_id"] == "build123d_path_sweep_contract"

def test_preflight_lint_rejects_sweep_profile_face_method_reference_and_surfaces_path_sweep_contract() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildLine() as path:\n"
            "    Line((0, 0), (50, 0))\n"
            "with BuildSketch(Plane.YZ) as profile:\n"
            "    Circle(10)\n"
            "    Circle(8, mode=Mode.SUBTRACT)\n"
            "with BuildPart() as pipe:\n"
            "    sweep(profile.face, path=path.wire())\n"
            "result = pipe.part\n"
        ),
        session_id="test-session",
        requirement_text=(
            "Use the Sweep feature to construct a hollow bent pipe by sweeping an annular profile along a path."
        ),
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_contract.sweep_profile_face_method_reference" in rule_ids
    assert payload["repair_recipe"]["recipe_id"] == "build123d_path_sweep_contract"

def test_preflight_lint_rejects_solid_sweep_invalid_keywords_and_surfaces_path_sweep_contract() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildLine() as path:\n"
            "    Line((0, 0, 0), (50, 0, 0))\n"
            "    CenterArc((50, 30, 0), 30, start_angle=-90, arc_size=90)\n"
            "    Line((80, 30, 0), (80, 80, 0))\n"
            "profile_plane = Plane(origin=(80, 80, 0), z_dir=(0, 1, 0))\n"
            "with BuildSketch(profile_plane) as profile:\n"
            "    Circle(10)\n"
            "outer_pipe = Solid.sweep(profile.face(), path_wire=path.wire(), profile_plane=profile_plane)\n"
            "result = outer_pipe\n"
        ),
        session_id="test-session",
        requirement_text=(
            "Use the Sweep feature to construct a hollow bent pipe by sweeping a profile along an L-shaped path."
        ),
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_keyword.solid_sweep_unsupported_keyword" in rule_ids
    assert payload["repair_recipe"]["recipe_id"] == "build123d_path_sweep_contract"

def test_preflight_lint_rejects_annular_profile_face_splitting_and_surfaces_path_sweep_contract() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildLine() as path:\n"
            "    Line((0, 0, 0), (50, 0, 0))\n"
            "    CenterArc((50, 30, 0), 30, start_angle=-90, arc_size=90)\n"
            "    Line((80, 30, 0), (80, 80, 0))\n"
            "profile_plane = Plane(origin=(80, 80, 0), z_dir=(0, 1, 0))\n"
            "with BuildSketch(profile_plane) as profile:\n"
            "    Circle(10)\n"
            "    Circle(8, mode=Mode.SUBTRACT)\n"
            "outer_face = profile.faces().sort_by(Axis.Z, reverse=True)[0]\n"
            "inner_face = profile.faces().sort_by(Axis.Z, reverse=True)[1]\n"
            "result = None\n"
        ),
        session_id="test-session",
        requirement_text=(
            "Use the Sweep feature to construct a hollow bent pipe by sweeping an annular profile along a path."
        ),
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_contract.annular_profile_face_splitting" in rule_ids
    assert payload["repair_recipe"]["recipe_id"] == "build123d_path_sweep_contract"

def test_preflight_lint_rejects_annular_profile_face_extraction_before_sweep_and_surfaces_path_sweep_contract() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildLine() as path:\n"
            "    Line((0, 0, 0), (50, 0, 0))\n"
            "    CenterArc((50, 30, 0), 30, start_angle=-90, arc_size=90)\n"
            "    Line((80, 30, 0), (80, 80, 0))\n"
            "profile_plane = Plane(origin=(80, 80, 0), z_dir=(0, 1, 0))\n"
            "with BuildSketch(profile_plane) as profile_builder:\n"
            "    Circle(10)\n"
            "    Circle(8, mode=Mode.SUBTRACT)\n"
            "profile_face = profile_builder.face()\n"
            "with BuildPart() as pipe:\n"
            "    sweep(profile_face, path=path.wire())\n"
            "result = pipe.part\n"
        ),
        session_id="test-session",
        requirement_text=(
            "Use the Sweep feature to construct a hollow bent pipe by sweeping an annular profile along a path."
        ),
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_contract.annular_profile_face_extraction" in rule_ids
    assert payload["repair_recipe"]["recipe_id"] == "build123d_path_sweep_contract"

def test_preflight_lint_rejects_vector_component_indexing_in_path_sweep_and_surfaces_path_sweep_contract() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildLine() as path:\n"
            "    l1 = Line((0, 0), (50, 0))\n"
            "    arc = CenterArc((50, 30), 30, start_angle=-90, arc_size=90)\n"
            "    arc_end = arc @ 1\n"
            "    l2 = Line(arc_end, (arc_end[0], arc_end[1] + 50))\n"
            "profile_plane = Plane.YZ.offset(0)\n"
            "with BuildSketch(profile_plane) as profile:\n"
            "    Circle(10)\n"
            "    Circle(8, mode=Mode.SUBTRACT)\n"
            "with BuildPart() as pipe:\n"
            "    sweep(profile.sketch, path=path.wire())\n"
            "result = pipe.part\n"
        ),
        session_id="test-session",
        requirement_text=(
            "Use the Sweep feature to construct a hollow bent pipe by sweeping an annular profile along a path."
        ),
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_contract.vector_component_indexing" in rule_ids
    assert payload["repair_recipe"]["recipe_id"] == "build123d_path_sweep_contract"

def test_preflight_lint_rejects_lowercase_vector_component_attribute_access() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildPart() as base_part:\n"
            "    Box(78, 56, 16)\n"
            "base_outer_edges = [e for e in base_part.edges() if abs(e.center().z - 8) < 0.1]\n"
            "fillet(base_outer_edges, radius=2)\n"
            "result = base_part.part\n"
        ),
        session_id="test-session",
        requirement_text="Create a rounded enclosure body and fillet the top outer edges.",
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_api.vector_lowercase_component_attribute" in rule_ids
    assert (
        payload["repair_recipe"]["recipe_id"]
        == "build123d_vector_component_attribute_contract"
    )

def test_preflight_lint_rejects_topology_geometry_attribute_access() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildPart() as lid:\n"
            "    Box(78, 56, 14)\n"
            "edges_to_fillet = [e for e in lid.part.edges() if e.geometry is not None]\n"
            "fillet(edges_to_fillet, radius=3.0)\n"
            "result = lid.part\n"
        ),
        session_id="test-session",
        requirement_text="Create a rounded enclosure lid and fillet the top outer edges.",
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_api.topology_geometry_attribute" in rule_ids
    assert (
        payload["repair_recipe"]["recipe_id"]
        == "build123d_topology_geometry_attribute_contract"
    )

def test_preflight_lint_rejects_broad_shell_axis_fillet_on_fresh_enclosure_host() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildPart() as base_part:\n"
            "    with BuildSketch():\n"
            "        RectangleRounded(78, 56, 8)\n"
            "    extrude(amount=20)\n"
            "    with BuildSketch(Plane.XY.offset(2.4)):\n"
            "        RectangleRounded(73.2, 51.2, 5.6)\n"
            "    extrude(amount=17.6, mode=Mode.SUBTRACT)\n"
            "    fillet(base_part.edges().filter_by(Axis.Z), 2.0)\n"
            "result = base_part.part\n"
        ),
        session_id="test-session",
        requirement_text=(
            "Create a two-part rounded clamshell storage enclosure with lid and base, "
            "corner magnet recesses, and a front thumb notch."
        ),
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert (
        "invalid_build123d_contract.broad_shell_axis_fillet_on_fresh_host"
        in rule_ids
    )
    assert payload["repair_recipe"]["recipe_id"] == "shell_edge_fillet_postpone_contract"

def test_preflight_lint_rejects_broad_shell_axis_fillet_when_selector_is_stored_first() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildPart() as shell:\n"
            "    with BuildSketch():\n"
            "        RectangleRounded(78, 56, 8)\n"
            "    extrude(amount=14)\n"
            "    with BuildSketch(Plane.XY.offset(2.4)):\n"
            "        RectangleRounded(73.2, 51.2, 5.6)\n"
            "    extrude(amount=11.6, mode=Mode.SUBTRACT)\n"
            "    edges_to_fillet = shell.edges().filter_by(Axis.Z)\n"
            "    fillet(edges_to_fillet, 3.0)\n"
            "result = shell.part\n"
        ),
        session_id="test-session",
        requirement_text=(
            "Create a rounded enclosure shell for a clamshell lid with a thumb notch and "
            "magnet recesses."
        ),
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert (
        "invalid_build123d_contract.broad_shell_axis_fillet_on_fresh_host"
        in rule_ids
    )
    assert payload["repair_recipe"]["recipe_id"] == "shell_edge_fillet_postpone_contract"

def test_preflight_lint_rejects_broad_fillet_when_requirement_marks_it_as_local_finish_tail() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildPart() as bracket:\n"
            "    Box(66, 42, 16)\n"
            "    with BuildSketch(Plane.XY.offset(8)):\n"
            "        Rectangle(60, 36)\n"
            "    extrude(amount=-2.5, mode=Mode.SUBTRACT)\n"
            "    top_edges = bracket.part.edges().filter_by(Axis.Z).filter_by(lambda e: e.center().Z > 7.9)\n"
            "    fillet(top_edges, radius=1.0)\n"
            "result = bracket.part\n"
        ),
        session_id="test-session",
        requirement_text=(
            "Create a rectangular service bracket sized 66mm x 42mm x 16mm with a shallow top "
            "pocket and two mounting holes on the bottom face. Add a centered rounded-rectangle "
            "recess on the front face and leave the small edge fillet for a later topology-aware "
            "local finish."
        ),
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert (
        "invalid_build123d_contract.broad_local_finish_tail_fillet_on_first_write"
        in rule_ids
    )
    assert payload["repair_recipe"]["recipe_id"] == "local_finish_fillet_postpone_contract"

def test_preflight_lint_rejects_plane_normal_keyword_alias_and_surfaces_path_sweep_contract() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildLine() as path:\n"
            "    l1 = Line((0, 0, 0), (50, 0, 0))\n"
            "profile_plane = Plane(path.line @ 0, normal=l1 % 0)\n"
            "with BuildSketch(profile_plane) as profile:\n"
            "    Circle(10)\n"
            "    Circle(8, mode=Mode.SUBTRACT)\n"
            "with BuildPart() as pipe:\n"
            "    sweep(profile.sketch, path=path.wire())\n"
            "result = pipe.part\n"
        ),
        session_id="test-session",
        requirement_text=(
            "Use the Sweep feature to construct a hollow bent pipe by sweeping an annular profile along a path."
        ),
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_keyword.plane_normal_alias" in rule_ids
    assert payload["repair_recipe"]["recipe_id"] == "build123d_path_sweep_contract"

def test_preflight_lint_rejects_builder_method_reference_assignment_and_surfaces_generic_builder_contract() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildLine() as path_builder:\n"
            "    Line((0, 0), (50, 0))\n"
            "path_wire = path_builder.wire\n"
            "with BuildSketch(Plane.YZ) as profile_builder:\n"
            "    Circle(10)\n"
            "profile_face = profile_builder.face\n"
            "result = None\n"
        ),
        session_id="test-session",
        requirement_text=(
            "Build a wire and a circular profile, then continue modeling from those builders."
        ),
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_contract.builder_method_reference_assignment" in rule_ids
    assert payload["repair_recipe"]["recipe_id"] == "build123d_builder_method_reference_contract"

def test_preflight_lint_rejects_semicircle_helper_and_surfaces_arc_profile_contract() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildPart() as part:\n"
            "    with BuildSketch(Plane.XZ):\n"
            "        Semicircle(radius=20)\n"
            "    extrude(amount=30)\n"
            "result = part.part\n"
        ),
        session_id="test-session",
        requirement_text=(
            "Create a half-cylindrical shell with a flat split line and extrude it along the length."
        ),
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_api.semicircle_helper_name" in rule_ids
    assert payload["repair_recipe"]["recipe_id"] == "build123d_arc_profile_contract"

def test_preflight_lint_rejects_ring_helper_and_surfaces_annular_band_contract() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildPart() as part:\n"
            "    with BuildSketch(Plane.XY):\n"
            "        Circle(25)\n"
            "    extrude(amount=60)\n"
            "    with BuildSketch(Plane.XY.offset(29)):\n"
            "        Ring(25, 20)\n"
            "    extrude(amount=2, mode=Mode.SUBTRACT)\n"
            "result = part.part\n"
        ),
        session_id="test-session",
        requirement_text=_ANNULAR_GROOVE_REQUIREMENT,
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_api.ring_helper_name" in rule_ids
    assert payload["repair_recipe"]["recipe_id"] == "annular_groove_same_builder_band_subtract_recipe"

def test_preflight_lint_rejects_bare_shell_helper_and_surfaces_shell_contract() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildPart() as part:\n"
            "    Box(80, 60, 40)\n"
            "shell(part.faces(), 3)\n"
            "result = part.part\n"
        ),
        session_id="test-session",
        requirement_text="model a shelled enclosure body",
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_api.bare_shell_helper" in rule_ids
    assert payload["repair_recipe"]["recipe_id"] == "build123d_shell_offset_contract"

def test_preflight_lint_rejects_offset_opening_keyword_and_surfaces_shell_contract() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildPart() as part:\n"
            "    Box(80, 60, 40)\n"
            "    offset(amount=-2.4, opening=part.faces().sort_by(Axis.Z)[-1])\n"
            "result = part.part\n"
        ),
        session_id="test-session",
        requirement_text="model a shelled enclosure body",
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_keyword.offset_opening_singular" in rule_ids
    assert payload["repair_recipe"]["recipe_id"] == "build123d_shell_offset_contract"

def test_preflight_lint_ignores_shell_like_comment_text() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildPart() as part:\n"
            "    Box(80, 60, 40)\n"
            "    # Create a hollow shell (open top) by subtracting an inner box later\n"
            "result = part.part\n"
        ),
        session_id="test-session",
        requirement_text="model a shelled enclosure body",
        run_state=None,
    )

    assert payload is None

def test_preflight_lint_shell_recess_requirement_does_not_misclassify_as_spherical_recess() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildPart() as part:\n"
            "    Box(80, 60, 30)\n"
            "shell(part.faces(), 3)\n"
            "result = part.part\n"
        ),
        session_id="test-session",
        requirement_text="Create a shelled block with a shallow top-face recess and a reference hole pattern.",
        run_state=None,
    )

    assert payload is not None
    assert "spherical_recess" not in payload["candidate_family_ids"]
    assert "pattern_distribution" in payload["candidate_family_ids"]
