from sub_agent.prompts import load_prompt


def test_build123d_codegen_prompt_discourages_part_solid_mutation() -> None:
    prompt = load_prompt("codegen")

    assert "mode=Mode.SUBTRACT" in prompt
    assert "Do not assign back into `part.solid`" in prompt
    assert "Locations((x, y, top_z))" in prompt


def test_build123d_codegen_prompt_describes_origin_centered_host_defaults() -> None:
    prompt = load_prompt("codegen")

    assert "`Box(length, width, height)` is centered at the origin by default" in prompt
    assert "Rectangle(width, height) is centered on the sketch origin by default" in prompt
    assert "do not shift the pattern by `(+width/2, +height/2)`" in prompt
    assert "top face is at `z = +5`, not `z = +10`" in prompt
    assert "Do not silently replace it with a centered `Box(...)`" in prompt


def test_build123d_codegen_prompt_describes_corner_sketch_coordinate_translation() -> None:
    prompt = load_prompt("codegen")

    assert "draw points with coordinates on a rectangular host face or plate surface" in prompt
    assert "corner-based sketch coordinates like `(25, 15)` on a `100 x 60` face" in prompt
    assert "translate corner-based sketch coordinates into the centered host frame" in prompt


def test_build123d_codegen_prompt_uses_host_plane_center_for_hemisphere_recesses() -> None:
    prompt = load_prompt("codegen")

    assert "set `sphere_center_z = top_face_z`, not `top_face_z - radius`" in prompt


def test_build123d_codegen_prompt_discourages_box_depth_keyword_alias() -> None:
    prompt = load_prompt("codegen")

    assert "Do not invent `Box(..., depth=...)`" in prompt


def test_build123d_codegen_prompt_discourages_bare_shell_helper() -> None:
    prompt = load_prompt("codegen")

    assert "do not invent a bare `shell(...)` helper" in prompt


def test_build123d_codegen_prompt_discourages_bare_subtract_helper() -> None:
    prompt = load_prompt("codegen")

    assert "Do not invent a top-level `subtract(...)` helper" in prompt


def test_build123d_codegen_prompt_describes_valid_axis_filter_selection() -> None:
    prompt = load_prompt("codegen")

    assert "`filter_by(Axis.X)`, `filter_by(Axis.Y)`, or `filter_by(Axis.Z)`" in prompt
    assert "do not invent `filter_by_direction(...)`" in prompt
    assert "Do not call `edge.is_parallel(Axis.Y)`" in prompt


def test_build123d_codegen_prompt_describes_make_face_helper_case() -> None:
    prompt = load_prompt("codegen")

    assert "use lowercase `make_face()`" in prompt
    assert "Do not invent `MakeFace()`" in prompt
    assert "Curve helpers such as `Polyline(...)`, `Line(...)`, `CenterArc(...)`, and `RadiusArc(...)` belong inside `BuildLine`" in prompt


def test_build123d_codegen_prompt_describes_revolve_keyword_contract() -> None:
    prompt = load_prompt("codegen")

    assert "`revolve(...)` uses `revolution_arc=`" in prompt
    assert "do not invent `angle=...`" in prompt


def test_build123d_codegen_prompt_describes_semicircle_arc_contract() -> None:
    prompt = load_prompt("codegen")

    assert "`Circle(radius)` always creates a full circle" in prompt
    assert "Do not invent `Circle(..., arc_size=...)`" in prompt
    assert "there is no `Semicircle(...)` helper" in prompt
    assert "use `CenterArc(...)` or `RadiusArc(...)` inside `BuildLine`" in prompt
    assert "Do not mix a full `Circle(...)` with helper trim lines plus `make_face()`" in prompt


def test_build123d_codegen_prompt_describes_plain_degree_angle_contract() -> None:
    prompt = load_prompt("codegen")

    assert "pass plain degree numbers such as `start_angle=-90` and `arc_size=90` directly" in prompt
    assert "do not multiply them by `DEGREE` or `DEGREES`" in prompt


def test_build123d_codegen_prompt_prefers_center_arc_for_explicit_radius_path_sweeps() -> None:
    prompt = load_prompt("codegen")

    assert "For explicit circular elbows with a named radius or quarter-turn" in prompt
    assert "prefer `CenterArc(...)` with `start_angle=` and `arc_size=`" in prompt
    assert "only reach for `TangentArc(...)` or `JernArc(...)`" in prompt


def test_build123d_codegen_prompt_discourages_detached_cutter_primitives_inside_active_buildpart() -> None:
    prompt = load_prompt("codegen")

    assert "Do not instantiate a detached `Cylinder(...)` cutter inside an active `BuildPart`" in prompt
    assert "build the host in one `BuildPart`, close it, then create the cutter outside" in prompt


def test_build123d_codegen_prompt_discourages_nested_buildpart_cutter_arithmetic() -> None:
    prompt = load_prompt("codegen")

    assert "Do not open a nested `BuildPart()` cutter inside an active `BuildPart`" in prompt
    assert "`part.part -= cutter.part`" in prompt
    assert "do not open a nested `BuildPart()` cutter inside an active host and then mutate `host.part -= cutter.part`" in prompt


def test_build123d_codegen_prompt_describes_valid_countersink_helper_contract() -> None:
    prompt = load_prompt("codegen")

    assert "`CounterSinkHole(radius=..., counter_sink_radius=..., depth=..., counter_sink_angle=...)`" in prompt
    assert "Do not invent `CountersinkHole(...)`" in prompt
    assert "There is no `Workplanes(...)` helper" in prompt
    assert "Use capitalized `Hole(...)`, not lowercase `hole(...)`" in prompt
    assert "`CounterSinkHole(...)` is a `BuildPart` operation, not a `BuildSketch` entity" in prompt
    assert "with Locations((x, y, top_z), ...): CounterSinkHole(...)" in prompt


def test_build123d_codegen_prompt_prefers_countersink_helper_for_explicit_plate_arrays() -> None:
    prompt = load_prompt("codegen")

    assert "For explicit countersink arrays on a planar host face" in prompt
    assert "prefer one `CounterSinkHole(...)` pass on the first attempt" in prompt
    assert "Only fall back to manual cylinder+cone or revolved cutters" in prompt


def test_build123d_codegen_prompt_describes_directional_drill_coordinate_remapping() -> None:
    prompt = load_prompt("codegen")

    assert "XY drills along Z, XZ drills along Y, and YZ drills along X" in prompt
    assert "If the prompt says to drill in the Y direction at `z = 20` and `x = ±22.25`" in prompt
    assert "the hole centers live in the XZ workplane as `(x, z)`" in prompt


def test_build123d_codegen_prompt_describes_plane_offset_normal_semantics() -> None:
    prompt = load_prompt("codegen")

    assert "`Plane.XY.offset(d)` shifts along Z, `Plane.XZ.offset(d)` shifts along Y, and `Plane.YZ.offset(d)` shifts along X" in prompt
    assert "Do not use `Plane.XZ.offset(z0)` to encode a Z coordinate" in prompt


def test_build123d_codegen_prompt_discourages_plane_rotated_origin_guess() -> None:
    prompt = load_prompt("codegen")

    assert "`Plane.rotated(rotation, ordering=...)` only changes orientation" in prompt
    assert "The origin is unchanged" in prompt
    assert "Do not pass a second `(x, y, z)` tuple to `Plane.rotated(...)` as an origin guess" in prompt


def test_build123d_codegen_prompt_discourages_pos_lowercase_axis_keywords() -> None:
    prompt = load_prompt("codegen")

    assert "Use positional `Pos(x, y, z)` placement" in prompt
    assert "Do not guess lowercase keyword forms such as `Pos(z=30)`" in prompt


def test_build123d_codegen_prompt_describes_half_shell_same_builder_subtraction_discipline() -> None:
    prompt = load_prompt("codegen")

    assert "split bearing housings or half-shell bodies" in prompt
    assert "do not start from a full cylinder and split it later" in prompt
    assert "keep the Y-axis hole cutters in the same active `BuildPart`" in prompt
    assert "prefer the lower-risk same-builder `Cylinder(...)` + `mode=Mode.SUBTRACT` + `mode=Mode.INTERSECT` path on the first pass" in prompt
    assert "merge the pad/lugs, then run the bore cut on that combined host" in prompt
    assert "outer cylinder -> subtract inner cylinder -> intersect/trim to the half-plane -> add pad/lugs -> cut the bore -> drill the lug holes" in prompt
    assert "Do not write `outer_cyl = Cylinder(...)`" in prompt
    assert "`Cylinder(outer_radius, length)` -> `Cylinder(inner_radius, length, mode=Mode.SUBTRACT)` -> `Box(..., mode=Mode.INTERSECT)`" in prompt


def test_build123d_codegen_prompt_prefers_explicit_inner_solid_for_simple_shelled_boxes() -> None:
    prompt = load_prompt("codegen")

    assert "simple shelled boxes or enclosures" in prompt
    assert "default to explicit inner-solid subtraction on the first pass" in prompt


def test_build123d_codegen_prompt_discourages_nested_annular_groove_band_builders() -> None:
    prompt = load_prompt("codegen")

    assert "Do not open a nested `BuildPart()` just to create an annular groove band cutter" in prompt
    assert "close the host and subtract the groove band once" in prompt
    assert "There is no `Ring(...)` helper in Build123d" in prompt


def test_build123d_codegen_prompt_preserves_named_plane_mixed_section_extrude_contract() -> None:
    prompt = load_prompt("codegen")

    assert "draw multiple closed section elements on a named plane" in prompt
    assert "outer-circle plus inner-square/rectangle families" in prompt
    assert "default centered `Cylinder(...)`" in prompt


def test_build123d_codegen_prompt_discourages_temporary_primitives_inside_active_buildpart() -> None:
    prompt = load_prompt("codegen")

    assert "Every primitive constructor inside an active `BuildPart` mutates that host immediately" in prompt
    assert "temporary `outer_cyl = Cylinder(...)`" in prompt
    assert "close the host builder before doing explicit solid arithmetic" in prompt


def test_build123d_codegen_prompt_discourages_active_builder_transform_rebind() -> None:
    prompt = load_prompt("codegen")

    assert "Inside an active `BuildPart`, do not create `solid = Box(...)`" in prompt
    assert "`solid = Pos(...) * solid` or `solid = Rot(...) * solid`" in prompt
    assert "use `Locations(...)` at creation time" in prompt


def test_build123d_codegen_prompt_describes_detached_hinge_rotation_pattern() -> None:
    prompt = load_prompt("codegen")

    assert "do not write `with Rot(...): Cylinder(...)` inside `BuildPart`" in prompt
    assert "`Rot(...) * hinge_barrel.part`" in prompt
    assert "`Pos(...) * Rot(...) * hinge_barrel.part`" in prompt


def test_build123d_codegen_prompt_describes_centered_clamshell_split_pose_and_boundaries() -> None:
    prompt = load_prompt("codegen")

    assert "place the base shell center at `split_z - base_h/2`" in prompt
    assert "lid shell center at `split_z + lid_h/2`" in prompt
    assert "back seam normally sits at `y = -depth/2`" in prompt
    assert "front opening boundary at `y = +depth/2`" in prompt
    assert "hinge belongs on the back seam `y = -depth/2`" in prompt
    assert "A default `Cylinder(...)` still runs along Z" in prompt
    assert "do not drop an unrotated default `Cylinder(...)` directly onto `(x, hinge_y, split_z)`" in prompt
    assert "without a supported rotation/orientation lane that cylinder still runs along Z" in prompt
    assert "do not reuse the seam Y coordinate as an X offset" in prompt
    assert "A plain `pin hinge` or `mechanical hinge` on a two-part lid/base enclosure does not by itself authorize extra detached hinge solids or a third physical part" in prompt
    assert "`extrude(amount=h)` grows one-sided from the active sketch plane" in prompt
    assert "do not assume `Locations((0, 0, center_z))` plus `extrude(amount=h)` creates a centered shell interval" in prompt


def test_build123d_codegen_prompt_preserves_rectangle_rounded_outer_envelope() -> None:
    prompt = load_prompt("codegen")

    assert "`RectangleRounded(width, depth, radius=...)` already uses the outer footprint spans" in prompt
    assert "do not shrink the requested overall width/depth to `width - 2*radius`" in prompt


def test_build123d_codegen_prompt_preserves_named_feature_face_on_shelled_hosts() -> None:
    prompt = load_prompt("codegen")

    assert "If a shelled body will later receive a top-face/side-face/front-face local edit" in prompt
    assert "do not remove that same target face as the shell opening" in prompt
    assert "open the opposite face by default" in prompt


def test_build123d_codegen_prompt_allows_mode_private_for_safe_staging_solids() -> None:
    prompt = load_prompt("codegen")

    assert "`mode=Mode.PRIVATE`" in prompt
    assert "temporary staging solid inside an active `BuildPart`" in prompt
