from sub_agent_runtime.tool_runtime import _preflight_lint_execute_build123d


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
    recipe_steps = "\n".join(payload["repair_recipe"]["recipe_skeleton"]["steps"])
    assert "prefer one `CounterSinkHole(...)` pass first" in recipe_steps
    assert "Only fall back to an explicit same-builder cylinder+cone or revolved countersink recipe" in recipe_steps


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
