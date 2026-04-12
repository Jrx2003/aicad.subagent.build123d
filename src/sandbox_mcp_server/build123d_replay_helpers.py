from build123d import *
from pathlib import Path
import hashlib
import json
import math

result = globals().get("result", Part())
_aicad_sketch = None
_aicad_plane = "XY"
_aicad_sketch_origin_3d = [0.0, 0.0, 0.0]
_aicad_sketch_face_id = None
_aicad_sketch_face_hint = None
_aicad_sketch_from_face_ref = False
_aicad_stepped_profile = None
_aicad_loft_profiles = []
_aicad_sweep_path = None
_aicad_last_additive_feature = None


def _aicad_to_float(value, default=0.0):
    try:
        return float(value)
    except Exception:
        return float(default)


def _aicad_vec3(vec):
    try:
        return [_aicad_to_float(vec.x), _aicad_to_float(vec.y), _aicad_to_float(vec.z)]
    except Exception:
        try:
            return [_aicad_to_float(vec.X), _aicad_to_float(vec.Y), _aicad_to_float(vec.Z)]
        except Exception:
            try:
                return [_aicad_to_float(vec.X()), _aicad_to_float(vec.Y()), _aicad_to_float(vec.Z())]
            except Exception:
                return [0.0, 0.0, 0.0]


def _aicad_bound_box(shape):
    try:
        if hasattr(shape, "bounding_box"):
            return shape.bounding_box()
        return shape.BoundingBox()
    except Exception:
        return None


def _aicad_bbox(shape):
    bbox = _aicad_bound_box(shape)
    if bbox is None:
        return {
            "xlen": 0.0,
            "ylen": 0.0,
            "zlen": 0.0,
            "xmin": 0.0,
            "xmax": 0.0,
            "ymin": 0.0,
            "ymax": 0.0,
            "zmin": 0.0,
            "zmax": 0.0,
        }
    return {
        "xlen": _aicad_to_float(getattr(getattr(bbox, "size", None), "X", getattr(bbox, "xlen", 0.0))),
        "ylen": _aicad_to_float(getattr(getattr(bbox, "size", None), "Y", getattr(bbox, "ylen", 0.0))),
        "zlen": _aicad_to_float(getattr(getattr(bbox, "size", None), "Z", getattr(bbox, "zlen", 0.0))),
        "xmin": _aicad_to_float(getattr(getattr(bbox, "min", None), "X", getattr(bbox, "xmin", 0.0))),
        "xmax": _aicad_to_float(getattr(getattr(bbox, "max", None), "X", getattr(bbox, "xmax", 0.0))),
        "ymin": _aicad_to_float(getattr(getattr(bbox, "min", None), "Y", getattr(bbox, "ymin", 0.0))),
        "ymax": _aicad_to_float(getattr(getattr(bbox, "max", None), "Y", getattr(bbox, "ymax", 0.0))),
        "zmin": _aicad_to_float(getattr(getattr(bbox, "min", None), "Z", getattr(bbox, "zmin", 0.0))),
        "zmax": _aicad_to_float(getattr(getattr(bbox, "max", None), "Z", getattr(bbox, "zmax", 0.0))),
    }


def _aicad_shape_center(shape):
    try:
        return _aicad_vec3(shape.center())
    except Exception:
        try:
            return _aicad_vec3(shape.Center())
        except Exception:
            return [0.0, 0.0, 0.0]


def _aicad_shape_area(shape):
    try:
        return _aicad_to_float(shape.area)
    except Exception:
        try:
            return _aicad_to_float(shape.Area())
        except Exception:
            return 0.0


def _aicad_shape_length(shape):
    try:
        return _aicad_to_float(shape.length)
    except Exception:
        try:
            return _aicad_to_float(shape.Length())
        except Exception:
            return 0.0


def _aicad_entity_id(prefix, parts):
    payload = [prefix]
    for part in list(parts or []):
        if isinstance(part, float):
            payload.append(f"{part:.5f}")
        else:
            payload.append(str(part))
    digest = hashlib.sha1("|".join(payload).encode("utf-8")).hexdigest()[:12].upper()
    return f"{prefix}_{digest}"


def _aicad_face_entity_id(face):
    bbox = _aicad_bbox(face)
    center = _aicad_shape_center(face)
    return _aicad_entity_id(
        "F",
        [
            _aicad_shape_area(face),
            center[0],
            center[1],
            center[2],
            bbox["xlen"],
            bbox["ylen"],
            bbox["zlen"],
        ],
    )


def _aicad_edge_entity_id(edge):
    bbox = _aicad_bbox(edge)
    center = _aicad_shape_center(edge)
    return _aicad_entity_id(
        "E",
        [
            _aicad_shape_length(edge),
            center[0],
            center[1],
            center[2],
            bbox["xlen"],
            bbox["ylen"],
            bbox["zlen"],
        ],
    )


def _aicad_faces(source):
    try:
        return list(source.faces())
    except Exception:
        try:
            return list(source.faces().vals())
        except Exception:
            return []


def _aicad_edges(source):
    try:
        return list(source.edges())
    except Exception:
        try:
            return list(source.edges().vals())
        except Exception:
            return []


def _aicad_solids(source):
    try:
        return list(source.solids())
    except Exception:
        try:
            return list(source.solids().vals())
        except Exception:
            return []


def _aicad_result_has_positive_solid(target):
    solids = _aicad_solids(target)
    total = 0.0
    for solid in solids:
        try:
            total += abs(_aicad_to_float(getattr(solid, "volume", solid.Volume())))
        except Exception:
            continue
    return total > 1e-6


def _aicad_as_part(target):
    if target is None:
        return Part()
    if isinstance(target, Part):
        return target
    if hasattr(target, "part") and getattr(target, "part") is not None:
        try:
            return Part(getattr(target, "part"))
        except Exception:
            return getattr(target, "part")
    try:
        if _aicad_result_has_positive_solid(target):
            return Part(target)
    except Exception:
        pass
    return Part()


def _aicad_has_nonempty_sketch(sketch):
    if sketch is None:
        return False
    try:
        if len(sketch.faces()) > 0:
            return True
    except Exception:
        pass
    try:
        if len(sketch.wires()) > 0:
            return True
    except Exception:
        pass
    try:
        if len(sketch.edges()) > 0:
            return True
    except Exception:
        pass
    return False


def _aicad_preview_from_sketch_state(state):
    if not isinstance(state, dict):
        return Part()
    profile = state.get("profile")
    if _aicad_has_nonempty_sketch(profile):
        return profile
    path = state.get("path")
    if path is not None:
        return path
    return Part()


def _aicad_result_or_preview(current_result, sketch_state):
    if _aicad_result_has_positive_solid(current_result):
        return current_result
    return _aicad_preview_from_sketch_state(sketch_state)


def _aicad_plane_from_token(token):
    token = str(token or "XY").strip().upper()
    plane_alias_map = {
        "XY": Plane.XY,
        "TOP": Plane.XY,
        "BOTTOM": Plane.XY,
        "XZ": Plane.XZ,
        "FRONT": Plane.XZ,
        "BACK": Plane.XZ,
        "YZ": Plane.YZ,
        "RIGHT": Plane.YZ,
        "LEFT": Plane.YZ,
    }
    return plane_alias_map.get(token, Plane.XY)


def _aicad_copy_plane(plane):
    return Plane(origin=plane.origin, x_dir=plane.x_dir, z_dir=plane.z_dir)


def _aicad_plane_with_origin(plane, origin_3d):
    plane = _aicad_copy_plane(plane)
    if not isinstance(origin_3d, (list, tuple)) or len(origin_3d) < 3:
        return plane
    return plane.move(Location((_aicad_to_float(origin_3d[0]), _aicad_to_float(origin_3d[1]), _aicad_to_float(origin_3d[2]))))


def _aicad_plane_shift_local(plane, u=0.0, v=0.0):
    return Plane(
        origin=plane.from_local_coords((_aicad_to_float(u), _aicad_to_float(v), 0.0)),
        x_dir=plane.x_dir,
        z_dir=plane.z_dir,
    )


def _aicad_make_sketch_state(plane, plane_name="XY"):
    return {
        "plane": plane,
        "plane_name": str(plane_name or "XY").upper(),
        "profile": Sketch(),
        "path": None,
    }


def _aicad_state_plane(state):
    if isinstance(state, dict) and isinstance(state.get("plane"), Plane):
        return state["plane"]
    plane_name = globals().get("_aicad_plane", "XY")
    origin_3d = globals().get("_aicad_sketch_origin_3d", [0.0, 0.0, 0.0])
    return _aicad_plane_with_origin(_aicad_plane_from_token(plane_name), origin_3d)


def _aicad_state_plane_name(state):
    if isinstance(state, dict):
        plane_name = state.get("plane_name")
        if isinstance(plane_name, str) and plane_name.strip():
            return plane_name.strip().upper()
    return str(globals().get("_aicad_plane", "XY")).upper()


def _aicad_create_sketch(current_result, plane_name, origin_3d, attach_selector=None, position_u=0.0, position_v=0.0, use_world_origin=False):
    plane = _aicad_plane_from_token(plane_name)
    if use_world_origin:
        plane = _aicad_plane_with_origin(plane, origin_3d)
    if _aicad_result_has_positive_solid(current_result) and isinstance(attach_selector, str) and attach_selector.strip():
        face = _aicad_find_face_by_hint(_aicad_as_part(current_result), attach_selector.strip())
        if face is not None:
            plane = Plane(face)
    plane = _aicad_plane_shift_local(plane, position_u, position_v)
    return _aicad_make_sketch_state(plane, plane_name=plane_name)


def _aicad_create_sketch_from_face(current_result, face_id, face_hint, plane_name, origin_3d, local_center):
    part = _aicad_as_part(current_result)
    face = _aicad_find_face_by_id(part, face_id)
    if face is None:
        face = _aicad_find_face_by_hint(part, face_hint, local_points=[local_center])
    if face is None:
        raise RuntimeError(f"invalid_reference: face_ref {face_id!r} not found")
    plane = Plane(face)
    if isinstance(local_center, (list, tuple)) and len(local_center) >= 2:
        plane = _aicad_plane_shift_local(plane, local_center[0], local_center[1])
    state = _aicad_make_sketch_state(plane, plane_name=plane_name)
    state["face_id"] = face_id
    state["face_hint"] = face_hint
    return state


def _aicad_rebuild_profile_sketch(state, builder_fn):
    plane = _aicad_state_plane(state)
    existing = state.get("profile") if isinstance(state, dict) else None
    with BuildSketch(plane) as sketch_builder:
        if _aicad_has_nonempty_sketch(existing):
            add(existing)
        builder_fn()
    new_state = state if isinstance(state, dict) else _aicad_make_sketch_state(plane, _aicad_state_plane_name(state))
    new_state["plane"] = plane
    new_state["profile"] = sketch_builder.sketch
    return new_state


def _aicad_add_rectangle_to_sketch(state, width, height, center, inner_size=None):
    center = center if isinstance(center, (list, tuple)) else (0.0, 0.0)
    inner_size = inner_size if isinstance(inner_size, (list, tuple)) else None
    def _builder():
        with Locations((_aicad_to_float(center[0]), _aicad_to_float(center[1]))):
            Rectangle(_aicad_to_float(width, 1.0), _aicad_to_float(height, 1.0))
            if inner_size is not None and len(inner_size) >= 2:
                inner_width = _aicad_to_float(inner_size[0], 0.0)
                inner_height = _aicad_to_float(inner_size[1], 0.0)
                if inner_width > 1e-6 and inner_height > 1e-6:
                    Rectangle(inner_width, inner_height, mode=Mode.SUBTRACT)
    return _aicad_rebuild_profile_sketch(state, _builder)


def _aicad_add_circles_to_sketch(state, radius, centers, radius_inner=None):
    centers = list(centers or [(0.0, 0.0)])
    radius_inner = _aicad_to_float(radius_inner, 0.0)
    def _builder():
        for center in centers:
            with Locations((_aicad_to_float(center[0]), _aicad_to_float(center[1]))):
                Circle(_aicad_to_float(radius, 1.0))
                if radius_inner > 1e-6 and radius_inner < _aicad_to_float(radius, 1.0):
                    Circle(radius_inner, mode=Mode.SUBTRACT)
    return _aicad_rebuild_profile_sketch(state, _builder)


def _aicad_add_regular_polygon_to_sketch(state, side_count, radius_outer, center, rotation_degrees=0.0, radius_inner=None):
    center = center if isinstance(center, (list, tuple)) else (0.0, 0.0)
    radius_inner = _aicad_to_float(radius_inner, 0.0)
    def _builder():
        with Locations((_aicad_to_float(center[0]), _aicad_to_float(center[1]))):
            RegularPolygon(
                radius=_aicad_to_float(radius_outer, 1.0),
                side_count=max(3, int(side_count)),
                major_radius=True,
                rotation=_aicad_to_float(rotation_degrees, 0.0),
            )
            if radius_inner > 1e-6 and radius_inner < _aicad_to_float(radius_outer, 1.0):
                RegularPolygon(
                    radius=radius_inner,
                    side_count=max(3, int(side_count)),
                    major_radius=True,
                    rotation=_aicad_to_float(rotation_degrees, 0.0),
                    mode=Mode.SUBTRACT,
                )
    return _aicad_rebuild_profile_sketch(state, _builder)


def _aicad_add_polygon_to_sketch(state, points):
    normalized_points = [tuple((_aicad_to_float(point[0]), _aicad_to_float(point[1]))) for point in list(points or []) if isinstance(point, (list, tuple)) and len(point) >= 2]
    def _builder():
        Polygon(*normalized_points)
    return _aicad_rebuild_profile_sketch(state, _builder)


def _aicad_direction_vector(token):
    if isinstance(token, (list, tuple)) and len(token) >= 2:
        dx = _aicad_to_float(token[0])
        dy = _aicad_to_float(token[1])
        length = math.hypot(dx, dy)
        if length > 1e-6:
            return [dx / length, dy / length]
        return []
    direction = str(token or "").strip().lower()
    mapping = {
        "x": [1.0, 0.0],
        "x+": [1.0, 0.0],
        "+x": [1.0, 0.0],
        "right": [1.0, 0.0],
        "horizontal": [1.0, 0.0],
        "-x": [-1.0, 0.0],
        "x-": [-1.0, 0.0],
        "left": [-1.0, 0.0],
        "y": [0.0, 1.0],
        "y+": [0.0, 1.0],
        "+y": [0.0, 1.0],
        "up": [0.0, 1.0],
        "vertical": [0.0, 1.0],
        "-y": [0.0, -1.0],
        "y-": [0.0, -1.0],
        "down": [0.0, -1.0],
    }
    return list(mapping.get(direction, []))


def _aicad_rotate_heading(vec, angle_degrees):
    radians = math.radians(_aicad_to_float(angle_degrees))
    cos_value = math.cos(radians)
    sin_value = math.sin(radians)
    return [
        (_aicad_to_float(vec[0]) * cos_value) - (_aicad_to_float(vec[1]) * sin_value),
        (_aicad_to_float(vec[0]) * sin_value) + (_aicad_to_float(vec[1]) * cos_value),
    ]


def _aicad_arc_midpoint_from_center(start_x, start_y, end_x, end_y, center_x, center_y, clockwise=False, turn=None):
    start_angle = math.atan2(start_y - center_y, start_x - center_x)
    end_angle = math.atan2(end_y - center_y, end_x - center_x)
    if isinstance(turn, str):
        turn_token = turn.strip().lower()
        if turn_token in {"right", "cw", "clockwise"}:
            clockwise = True
        elif turn_token in {"left", "ccw", "counterclockwise", "counter_clockwise"}:
            clockwise = False
    sweep = end_angle - start_angle
    if clockwise:
        if sweep >= 0.0:
            sweep -= 2.0 * math.pi
    else:
        if sweep <= 0.0:
            sweep += 2.0 * math.pi
    radius = math.hypot(start_x - center_x, start_y - center_y)
    if radius <= 1e-6:
        return None
    mid_angle = start_angle + (sweep * 0.5)
    return [center_x + (radius * math.cos(mid_angle)), center_y + (radius * math.sin(mid_angle))]


def _aicad_signed_arc_sweep_degrees(angle_degrees, turn=None, clockwise=False):
    raw_angle = _aicad_to_float(angle_degrees, 0.0)
    if abs(raw_angle) <= 1e-6:
        return 0.0
    if isinstance(turn, str):
        turn_token = turn.strip().lower()
        if turn_token in {"right", "cw", "clockwise"}:
            clockwise = True
        elif turn_token in {"left", "ccw", "counterclockwise", "counter_clockwise"}:
            clockwise = False
    preferred_sign = -1.0 if clockwise else 1.0
    if raw_angle < 0.0:
        return raw_angle
    return preferred_sign * abs(raw_angle)


def _aicad_arc_endpoint_from_center(current_x, current_y, center_x, center_y, angle_degrees=None, turn=None, clockwise=False, start_angle=None, end_angle=None):
    radius = math.hypot(current_x - center_x, current_y - center_y)
    if radius <= 1e-6:
        return None
    if isinstance(end_angle, (int, float)):
        next_angle = math.radians(_aicad_to_float(end_angle))
    else:
        start_angle_value = math.atan2(current_y - center_y, current_x - center_x)
        if isinstance(start_angle, (int, float)):
            start_angle_value = math.radians(_aicad_to_float(start_angle))
        signed_sweep = _aicad_signed_arc_sweep_degrees(angle_degrees, turn=turn, clockwise=clockwise)
        if abs(signed_sweep) <= 1e-6:
            return None
        next_angle = start_angle_value + math.radians(signed_sweep)
    return [center_x + (radius * math.cos(next_angle)), center_y + (radius * math.sin(next_angle))]


def _aicad_apply_path_segments(plane, start_point, segments, closed=False, existing_path=None):
    current_x = _aicad_to_float(start_point[0] if len(start_point) > 0 else 0.0)
    current_y = _aicad_to_float(start_point[1] if len(start_point) > 1 else 0.0)
    start_x = current_x
    start_y = current_y
    heading = None
    with BuildLine(plane) as line_builder:
        if existing_path is not None:
            add(existing_path)
        for segment in list(segments or []):
            if not isinstance(segment, dict):
                continue
            segment_type = str(segment.get("type", "line")).strip().lower()
            if segment_type in {"line", "tangent_line"}:
                to_point = segment.get("to")
                if isinstance(to_point, (list, tuple)) and len(to_point) >= 2:
                    next_x = _aicad_to_float(to_point[0])
                    next_y = _aicad_to_float(to_point[1])
                else:
                    dx = segment.get("dx")
                    dy = segment.get("dy")
                    if isinstance(dx, (int, float)) or isinstance(dy, (int, float)):
                        next_x = current_x + _aicad_to_float(dx)
                        next_y = current_y + _aicad_to_float(dy)
                    else:
                        length = segment.get("length")
                        direction = _aicad_direction_vector(segment.get("direction")) or heading or [1.0, 0.0]
                        next_x = current_x + (_aicad_to_float(length) * _aicad_to_float(direction[0]))
                        next_y = current_y + (_aicad_to_float(length) * _aicad_to_float(direction[1]))
                Line((current_x, current_y), (next_x, next_y))
                delta_x = next_x - current_x
                delta_y = next_y - current_y
                length = math.hypot(delta_x, delta_y)
                if length > 1e-6:
                    heading = [delta_x / length, delta_y / length]
                current_x = next_x
                current_y = next_y
                continue
            if segment_type == "three_point_arc":
                mid_point = segment.get("mid")
                end_point = segment.get("to")
                if isinstance(mid_point, (list, tuple)) and len(mid_point) >= 2 and isinstance(end_point, (list, tuple)) and len(end_point) >= 2:
                    mid_x = _aicad_to_float(mid_point[0])
                    mid_y = _aicad_to_float(mid_point[1])
                    next_x = _aicad_to_float(end_point[0])
                    next_y = _aicad_to_float(end_point[1])
                    ThreePointArc((current_x, current_y), (mid_x, mid_y), (next_x, next_y))
                    current_x = next_x
                    current_y = next_y
                    continue
            center_point = segment.get("center")
            if isinstance(center_point, (list, tuple)) and len(center_point) >= 2:
                center_x = _aicad_to_float(center_point[0])
                center_y = _aicad_to_float(center_point[1])
                end_point = segment.get("to")
                if isinstance(end_point, (list, tuple)) and len(end_point) >= 2:
                    next_x = _aicad_to_float(end_point[0])
                    next_y = _aicad_to_float(end_point[1])
                else:
                    endpoint = _aicad_arc_endpoint_from_center(
                        current_x,
                        current_y,
                        center_x,
                        center_y,
                        angle_degrees=segment.get("angle_degrees", segment.get("arc_degrees")),
                        turn=segment.get("turn", segment.get("turn_direction")),
                        clockwise=bool(segment.get("clockwise", False)),
                        start_angle=segment.get("start_angle"),
                        end_angle=segment.get("end_angle"),
                    )
                    if endpoint is None:
                        continue
                    next_x, next_y = endpoint
                midpoint = _aicad_arc_midpoint_from_center(
                    current_x,
                    current_y,
                    next_x,
                    next_y,
                    center_x,
                    center_y,
                    clockwise=bool(segment.get("clockwise", False)),
                    turn=segment.get("turn", segment.get("turn_direction")),
                )
                if midpoint is None:
                    continue
                ThreePointArc((current_x, current_y), tuple(midpoint), (next_x, next_y))
                current_x = next_x
                current_y = next_y
                continue
            radius = _aicad_to_float(segment.get("radius"), 0.0)
            angle_degrees = _aicad_to_float(segment.get("angle_degrees", segment.get("arc_degrees")), 0.0)
            if radius <= 1e-6 or abs(angle_degrees) <= 1e-6:
                continue
            direction = _aicad_direction_vector(segment.get("direction")) or heading or [1.0, 0.0]
            turn = segment.get("turn", segment.get("turn_direction"))
            signed_sweep = _aicad_signed_arc_sweep_degrees(angle_degrees, turn=turn, clockwise=bool(segment.get("clockwise", False)))
            next_heading = _aicad_rotate_heading(direction, signed_sweep)
            chord = 2.0 * radius * math.sin(math.radians(abs(signed_sweep)) * 0.5)
            midpoint_heading = _aicad_rotate_heading(direction, signed_sweep * 0.5)
            next_x = current_x + (chord * _aicad_to_float(midpoint_heading[0]))
            next_y = current_y + (chord * _aicad_to_float(midpoint_heading[1]))
            if turn in {"right", "cw", "clockwise"}:
                midpoint = [current_x + (radius * direction[1]), current_y - (radius * direction[0])]
            else:
                midpoint = [current_x - (radius * direction[1]), current_y + (radius * direction[0])]
            arc_mid = _aicad_arc_midpoint_from_center(
                current_x,
                current_y,
                next_x,
                next_y,
                midpoint[0],
                midpoint[1],
                clockwise=bool(segment.get("clockwise", False)),
                turn=turn,
            )
            if arc_mid is None:
                continue
            ThreePointArc((current_x, current_y), tuple(arc_mid), (next_x, next_y))
            heading = next_heading
            current_x = next_x
            current_y = next_y
        if closed and (abs(current_x - start_x) > 1e-6 or abs(current_y - start_y) > 1e-6):
            Line((current_x, current_y), (start_x, start_y))
    return line_builder.wire()


def _aicad_add_path_to_sketch(state, start_point, segments, closed=False):
    plane = _aicad_state_plane(state)
    existing_path = state.get("path") if isinstance(state, dict) else None
    new_state = state if isinstance(state, dict) else _aicad_make_sketch_state(plane, _aicad_state_plane_name(state))
    new_state["plane"] = plane
    new_state["path"] = _aicad_apply_path_segments(plane, list(start_point or [0.0, 0.0]), list(segments or []), bool(closed), existing_path=existing_path)
    return new_state


def _aicad_resolve_profile_shape(target):
    if isinstance(target, dict):
        profile = target.get("profile")
        if _aicad_has_nonempty_sketch(profile):
            return profile
        return None
    if target is None:
        return None
    try:
        if isinstance(target, Face):
            return target
    except Exception:
        pass
    try:
        if isinstance(target, Sketch) and _aicad_has_nonempty_sketch(target):
            return target
    except Exception:
        pass
    try:
        shape_faces = list(target.faces())
    except Exception:
        shape_faces = []
    if shape_faces:
        return shape_faces[0]
    try:
        shape_wires = list(target.wires())
    except Exception:
        shape_wires = []
    if shape_wires:
        return shape_wires[0]
    return None


def _aicad_resolve_path_shape(target):
    if isinstance(target, dict):
        return target.get("path")
    if target is None:
        return None
    try:
        if isinstance(target, Wire):
            return target
    except Exception:
        pass
    try:
        wires = list(target.wires())
    except Exception:
        wires = []
    if wires:
        return wires[0]
    try:
        edges = list(target.edges())
    except Exception:
        edges = []
    if len(edges) == 1:
        return edges[0]
    if edges:
        try:
            assembled = list(Wire.combine(edges))
            if assembled:
                return assembled[0]
        except Exception:
            pass
        return edges[0]
    return None


def _aicad_make_face_from_shape(shape):
    if shape is None:
        return None
    try:
        if isinstance(shape, Face):
            return shape
    except Exception:
        pass
    try:
        if isinstance(shape, Sketch) and len(shape.faces()) > 0:
            return shape.faces()[0]
    except Exception:
        pass
    try:
        if isinstance(shape, Wire):
            return Face(shape)
    except Exception:
        pass
    try:
        shape_faces = list(shape.faces())
    except Exception:
        shape_faces = []
    if shape_faces:
        return shape_faces[0]
    try:
        shape_wires = list(shape.wires())
    except Exception:
        shape_wires = []
    if not shape_wires:
        return None
    outer_wire = shape_wires[0]
    inner_wires = list(shape_wires[1:])
    try:
        return Face(outer_wire, inner_wires)
    except Exception:
        try:
            return Face(outer_wire)
        except Exception:
            return None


def _aicad_capture_pending_sweep_path():
    global _aicad_sweep_path
    candidate = _aicad_resolve_path_shape(globals().get("_aicad_sketch"))
    if candidate is None:
        return
    try:
        if bool(candidate.is_closed):
            return
    except Exception:
        pass
    _aicad_sweep_path = candidate


def _aicad_capture_pending_loft_profile():
    global _aicad_loft_profiles
    if _aicad_result_has_positive_solid(globals().get("result")):
        return
    candidate = _aicad_resolve_profile_shape(globals().get("_aicad_sketch"))
    if candidate is None:
        return
    candidate_id = None
    if isinstance(candidate, Sketch):
        try:
            candidate_id = _aicad_face_entity_id(candidate.faces()[0])
        except Exception:
            candidate_id = None
    else:
        try:
            candidate_id = _aicad_face_entity_id(_aicad_make_face_from_shape(candidate))
        except Exception:
            candidate_id = None
    for existing in list(_aicad_loft_profiles or []):
        try:
            if _aicad_face_entity_id(_aicad_make_face_from_shape(existing)) == candidate_id:
                return
        except Exception:
            continue
    _aicad_loft_profiles = list(_aicad_loft_profiles or []) + [candidate]


def _aicad_make_axis_cylinder(axis, radius, length, start_offset, origin):
    radius = max(0.0, _aicad_to_float(radius))
    length = max(0.0, _aicad_to_float(length))
    if radius <= 1e-6 or length <= 1e-6:
        return None
    origin = origin if isinstance(origin, (list, tuple)) and len(origin) >= 3 else [0.0, 0.0, 0.0]
    ox = _aicad_to_float(origin[0])
    oy = _aicad_to_float(origin[1])
    oz = _aicad_to_float(origin[2])
    axis = str(axis or "Z").upper()
    if axis == "X":
        plane = Plane.YZ.move(Location((ox + _aicad_to_float(start_offset), oy, oz)))
    elif axis == "Y":
        plane = Plane.XZ.move(Location((ox, oy + _aicad_to_float(start_offset), oz)))
    else:
        plane = Plane.XY.move(Location((ox, oy, oz + _aicad_to_float(start_offset))))
    with BuildSketch(plane) as sketch_builder:
        Circle(radius)
    return extrude(sketch_builder.sketch, amount=length)


def _aicad_make_axis_annulus(axis, inner_radius, outer_radius, length, start_offset, origin):
    outer_radius = max(0.0, _aicad_to_float(outer_radius))
    inner_radius = max(0.0, _aicad_to_float(inner_radius))
    if outer_radius <= 1e-6 or outer_radius <= inner_radius + 1e-6:
        return None
    origin = origin if isinstance(origin, (list, tuple)) and len(origin) >= 3 else [0.0, 0.0, 0.0]
    ox = _aicad_to_float(origin[0])
    oy = _aicad_to_float(origin[1])
    oz = _aicad_to_float(origin[2])
    axis = str(axis or "Z").upper()
    if axis == "X":
        plane = Plane.YZ.move(Location((ox + _aicad_to_float(start_offset), oy, oz)))
    elif axis == "Y":
        plane = Plane.XZ.move(Location((ox, oy + _aicad_to_float(start_offset), oz)))
    else:
        plane = Plane.XY.move(Location((ox, oy, oz + _aicad_to_float(start_offset))))
    with BuildSketch(plane) as sketch_builder:
        Circle(outer_radius)
        if inner_radius > 1e-6:
            Circle(inner_radius, mode=Mode.SUBTRACT)
    return extrude(sketch_builder.sketch, amount=max(0.0, _aicad_to_float(length)))


def _aicad_build_stepped_solid(axis, lengths, radii, origin, bands=None):
    if isinstance(bands, list) and bands:
        built = None
        offset = 0.0
        for band in bands:
            if not isinstance(band, dict):
                continue
            length = _aicad_to_float(band.get("length"), 0.0)
            inner_radius = _aicad_to_float(band.get("inner_radius", 0.0), 0.0)
            outer_radius = _aicad_to_float(band.get("outer_radius", 0.0), 0.0)
            segment = _aicad_make_axis_annulus(axis, inner_radius, outer_radius, length, offset, origin)
            offset += max(0.0, length)
            if segment is None:
                continue
            built = segment if built is None else built.fuse(segment)
        return built
    if not isinstance(lengths, list) or not isinstance(radii, list) or len(lengths) != len(radii) or not lengths:
        return None
    built = None
    offset = 0.0
    for length_value, radius_value in zip(lengths, radii):
        segment = _aicad_make_axis_cylinder(axis, radius_value, length_value, offset, origin)
        offset += max(0.0, _aicad_to_float(length_value))
        if segment is None:
            continue
        built = segment if built is None else built.fuse(segment)
    return built


def _aicad_face_axis_signature(face):
    normal = [0.0, 0.0, 1.0]
    try:
        normal = _aicad_vec3(face.normal_at())
    except Exception:
        try:
            normal = _aicad_vec3(face.normalAt())
        except Exception:
            normal = [0.0, 0.0, 1.0]
    axis_names = ["X", "Y", "Z"]
    magnitudes = [abs(_aicad_to_float(item)) for item in normal]
    axis_index = magnitudes.index(max(magnitudes))
    sign = "positive" if _aicad_to_float(normal[axis_index]) >= 0.0 else "negative"
    return axis_names[axis_index], sign


def _aicad_face_half_spans(face):
    bbox = _aicad_bbox(face)
    axis, _ = _aicad_face_axis_signature(face)
    if axis == "Z":
        return max(1e-6, bbox["xlen"] / 2.0), max(1e-6, bbox["ylen"] / 2.0)
    if axis == "Y":
        return max(1e-6, bbox["xlen"] / 2.0), max(1e-6, bbox["zlen"] / 2.0)
    return max(1e-6, bbox["ylen"] / 2.0), max(1e-6, bbox["zlen"] / 2.0)


def _aicad_local_point_extents(local_points):
    numeric_points = []
    for point in list(local_points or []):
        if not isinstance(point, (list, tuple)) or len(point) < 2:
            continue
        numeric_points.append((_aicad_to_float(point[0]), _aicad_to_float(point[1])))
    if not numeric_points:
        return 0.0, 0.0
    us = [item[0] for item in numeric_points]
    vs = [item[1] for item in numeric_points]
    return (max(us) - min(us)) / 2.0, (max(vs) - min(vs)) / 2.0


def _aicad_face_supports_local_points(face, local_points):
    u_extent, v_extent = _aicad_local_point_extents(local_points)
    if u_extent <= 1e-9 and v_extent <= 1e-9:
        return True
    half_a, half_b = _aicad_face_half_spans(face)
    face_spans = sorted([half_a, half_b])
    point_spans = sorted([u_extent, v_extent])
    tol = max(1e-3, max(face_spans[1], 1.0) * 0.05)
    return point_spans[0] <= face_spans[0] + tol and point_spans[1] <= face_spans[1] + tol


def _aicad_face_local_bounds(face, plane):
    if face is None or plane is None:
        return None
    local_samples = []
    face_half_a, face_half_b = _aicad_face_half_spans(face)
    for edge in _aicad_edges(face):
        world_samples = []
        try:
            world_samples = list(edge.discretize(24))
        except Exception:
            world_samples = []
        try:
            start_point = edge.start_point()
            end_point = edge.end_point()
            world_samples.extend([start_point, end_point])
        except Exception:
            pass
        for world in world_samples:
            try:
                local = plane.to_local_coords(world)
                local_samples.append((float(local.x), float(local.y)))
            except Exception:
                continue
    if not local_samples:
        if face_half_a > 1e-6 and face_half_b > 1e-6:
            return (-float(face_half_a), float(face_half_a), -float(face_half_b), float(face_half_b))
        return None
    us = [item[0] for item in local_samples]
    vs = [item[1] for item in local_samples]
    span_u = max(us) - min(us)
    span_v = max(vs) - min(vs)
    if (span_u <= 1e-6 or span_v <= 1e-6) and face_half_a > 1e-6 and face_half_b > 1e-6:
        return (-float(face_half_a), float(face_half_a), -float(face_half_b), float(face_half_b))
    return (min(us), max(us), min(vs), max(vs))


def _aicad_translate_positive_local_points(face, plane, local_points):
    if face is None or plane is None or not isinstance(local_points, list) or not local_points:
        return local_points
    bounds = _aicad_face_local_bounds(face, plane)
    if bounds is None:
        return local_points
    min_u, max_u, min_v, max_v = bounds
    span_u = max(0.0, float(max_u) - float(min_u))
    span_v = max(0.0, float(max_v) - float(min_v))
    tol = max(1e-3, max(span_u, span_v, 1.0) * 0.05)
    numeric_points = []
    for point in local_points:
        if not isinstance(point, (list, tuple)) or len(point) < 2:
            return local_points
        if not isinstance(point[0], (int, float)) or not isinstance(point[1], (int, float)):
            return local_points
        numeric_points.append([float(point[0]), float(point[1])])
    centered_fit = True
    for u_value, v_value in numeric_points:
        if u_value < (min_u - tol) or u_value > (max_u + tol) or v_value < (min_v - tol) or v_value > (max_v + tol):
            centered_fit = False
            break
    if centered_fit:
        return numeric_points
    positive_fit = True
    for u_value, v_value in numeric_points:
        if u_value < -tol or v_value < -tol or u_value > (span_u + tol) or v_value > (span_v + tol):
            positive_fit = False
            break
    if not positive_fit:
        return numeric_points
    return [[float(u_value + min_u), float(v_value + min_v)] for u_value, v_value in numeric_points]


def _aicad_find_face_by_id(workplane_obj, face_id):
    for face in _aicad_faces(workplane_obj):
        try:
            if _aicad_face_entity_id(face) == face_id:
                return face
        except Exception:
            continue
    return None


def _aicad_find_face_by_hint(workplane_obj, face_hint, local_points=None):
    if not isinstance(face_hint, str) or not face_hint.strip():
        return None
    faces = _aicad_faces(workplane_obj)
    if not faces:
        return None
    hint = face_hint.strip().lower()
    bbox_items = [_aicad_bbox(face) for face in faces]
    max_z = max(float(item.get("zmax", 0.0)) for item in bbox_items)
    min_z = min(float(item.get("zmin", 0.0)) for item in bbox_items)
    max_y = max(float(item.get("ymax", 0.0)) for item in bbox_items)
    min_y = min(float(item.get("ymin", 0.0)) for item in bbox_items)
    max_x = max(float(item.get("xmax", 0.0)) for item in bbox_items)
    min_x = min(float(item.get("xmin", 0.0)) for item in bbox_items)
    tol_z = max(1e-4, abs(max_z - min_z) * 0.03)
    tol_y = max(1e-4, abs(max_y - min_y) * 0.03)
    tol_x = max(1e-4, abs(max_x - min_x) * 0.03)
    candidates = []
    for face in faces:
        axis, sign = _aicad_face_axis_signature(face)
        face_bbox = _aicad_bbox(face)
        center = _aicad_shape_center(face)
        supports = _aicad_face_supports_local_points(face, local_points)
        match = False
        rank = 1000.0
        if hint in {"top_faces", "upward_planar_faces", ">z"} and axis == "Z" and sign == "positive":
            match = True
            rank = abs(float(face_bbox.get("zmax", 0.0)) - max_z)
        elif hint in {"bottom_faces", "downward_planar_faces", "<z"} and axis == "Z" and sign == "negative":
            match = True
            rank = abs(float(face_bbox.get("zmin", 0.0)) - min_z)
        elif hint in {"front_faces", ">y"} and axis == "Y" and sign == "positive":
            match = True
            rank = abs(float(face_bbox.get("ymax", 0.0)) - max_y)
        elif hint in {"back_faces", "<y"} and axis == "Y" and sign == "negative":
            match = True
            rank = abs(float(face_bbox.get("ymin", 0.0)) - min_y)
        elif hint in {"right_faces", ">x"} and axis == "X" and sign == "positive":
            match = True
            rank = abs(float(face_bbox.get("xmax", 0.0)) - max_x)
        elif hint in {"left_faces", "<x"} and axis == "X" and sign == "negative":
            match = True
            rank = abs(float(face_bbox.get("xmin", 0.0)) - min_x)
        elif hint in {"interior_upward_planar_faces", "top_inner_planar_faces"} and axis == "Z" and sign == "positive" and float(face_bbox.get("zmax", 0.0)) < (max_z - tol_z):
            match = True
            rank = abs(float(center[2]))
        elif hint in {"interior_downward_planar_faces", "bottom_inner_planar_faces"} and axis == "Z" and sign == "negative" and float(face_bbox.get("zmin", 0.0)) > (min_z + tol_z):
            match = True
            rank = abs(float(center[2]))
        elif hint in {"interior_right_faces"} and axis == "X" and sign == "positive" and float(face_bbox.get("xmax", 0.0)) < (max_x - tol_x):
            match = True
            rank = abs(float(center[0]))
        elif hint in {"interior_left_faces"} and axis == "X" and sign == "negative" and float(face_bbox.get("xmin", 0.0)) > (min_x + tol_x):
            match = True
            rank = abs(float(center[0]))
        elif hint in {"interior_front_faces"} and axis == "Y" and sign == "positive" and float(face_bbox.get("ymax", 0.0)) < (max_y - tol_y):
            match = True
            rank = abs(float(center[1]))
        elif hint in {"interior_back_faces"} and axis == "Y" and sign == "negative" and float(face_bbox.get("ymin", 0.0)) > (min_y + tol_y):
            match = True
            rank = abs(float(center[1]))
        if not match:
            continue
        candidates.append((0 if supports else 1, rank, _aicad_shape_area(face), face))
    if not candidates:
        return None
    candidates.sort(key=lambda item: (item[0], item[1], item[2]))
    return candidates[0][3]


def _aicad_find_edges_by_ids(workplane_obj, edge_ids):
    selected = []
    lookup = set(edge_ids or [])
    for edge in _aicad_edges(workplane_obj):
        try:
            edge_id = _aicad_edge_entity_id(edge)
        except Exception:
            continue
        if edge_id in lookup:
            selected.append(edge)
    return selected


def _aicad_select_edges_by_scope(workplane_obj, edge_scope=None, edges_selector=None):
    edges = _aicad_edges(workplane_obj)
    if not edges:
        return []
    token = str(edge_scope or edges_selector or "all").strip().lower()
    selector_alias = {
        ">z": "top",
        "<z": "bottom",
        ">x": "right",
        "<x": "left",
        ">y": "front",
        "<y": "back",
        "|z": "vertical",
    }
    token = selector_alias.get(token, token)
    if token in {"all", "all_outer", "outer", "all_edges"}:
        return edges
    bbox = _aicad_bbox(workplane_obj)
    tol = max(bbox["xlen"], bbox["ylen"], bbox["zlen"], 1.0) * 0.03
    selected = []
    for edge in edges:
        edge_bbox = _aicad_bbox(edge)
        if token in {"top", "top_edges"} and abs(edge_bbox["zmax"] - bbox["zmax"]) <= tol:
            selected.append(edge)
        elif token in {"bottom", "bottom_edges"} and abs(edge_bbox["zmin"] - bbox["zmin"]) <= tol:
            selected.append(edge)
        elif token in {"right", "right_edges"} and abs(edge_bbox["xmax"] - bbox["xmax"]) <= tol:
            selected.append(edge)
        elif token in {"left", "left_edges"} and abs(edge_bbox["xmin"] - bbox["xmin"]) <= tol:
            selected.append(edge)
        elif token in {"front", "front_edges"} and abs(edge_bbox["ymax"] - bbox["ymax"]) <= tol:
            selected.append(edge)
        elif token in {"back", "back_edges"} and abs(edge_bbox["ymin"] - bbox["ymin"]) <= tol:
            selected.append(edge)
        elif token in {"vertical", "vertical_edges"}:
            spans = [edge_bbox["xlen"], edge_bbox["ylen"], edge_bbox["zlen"]]
            if spans[2] >= max(spans[0], spans[1]) - tol:
                selected.append(edge)
    return selected or edges


def _aicad_direction_vector3(token):
    if isinstance(token, (list, tuple)) and len(token) >= 3:
        dx = _aicad_to_float(token[0])
        dy = _aicad_to_float(token[1])
        dz = _aicad_to_float(token[2])
    else:
        mapping = {
            "X": (1.0, 0.0, 0.0),
            "Y": (0.0, 1.0, 0.0),
            "Z": (0.0, 0.0, 1.0),
        }
        dx, dy, dz = mapping.get(str(token or "X").strip().upper(), (1.0, 0.0, 0.0))
    norm = max((dx * dx + dy * dy + dz * dz) ** 0.5, 1e-9)
    return (dx / norm, dy / norm, dz / norm)


def _aicad_part_union(base, feature):
    if feature is None:
        return _aicad_as_part(base)
    if not _aicad_result_has_positive_solid(base):
        return _aicad_as_part(feature)
    return _aicad_as_part(base).fuse(_aicad_as_part(feature))


def _aicad_apply_extrude(current_result, sketch_state, distance, both=False, reverse=False):
    amount = abs(_aicad_to_float(distance, 1.0))
    if amount <= 1e-6:
        amount = 1.0
    profile = _aicad_resolve_profile_shape(sketch_state)
    if profile is None and _aicad_has_nonempty_sketch(current_result):
        profile = current_result
    if profile is None:
        raise RuntimeError("extrude requires an active profile sketch")
    plane = _aicad_state_plane(sketch_state)
    if both:
        feature = extrude(profile, amount=amount, both=True)
    else:
        direction = plane.z_dir
        sign = -1.0 if reverse else 1.0
        direction = Vector(sign * _aicad_to_float(direction.x), sign * _aicad_to_float(direction.y), sign * _aicad_to_float(direction.z))
        feature = extrude(profile, amount=amount, dir=direction)
    new_result = feature if not _aicad_result_has_positive_solid(current_result) else _aicad_part_union(current_result, feature)
    return new_result, feature


def _aicad_apply_cut_extrude(current_result, sketch_state, distance, through_all=False, outside_cut=False, both_sides=False):
    part = _aicad_as_part(current_result)
    if not _aicad_result_has_positive_solid(part):
        raise RuntimeError("cut_extrude requires an existing solid result")
    amount = abs(_aicad_to_float(distance, 0.0))
    bbox = _aicad_bbox(part)
    through_depth = max(bbox["xlen"], bbox["ylen"], bbox["zlen"], 1.0) * 4.0
    if through_all or amount <= 1e-6:
        amount = through_depth
    profile = _aicad_resolve_profile_shape(sketch_state)
    if profile is None:
        raise RuntimeError("cut_extrude requires an active profile sketch")
    plane = _aicad_state_plane(sketch_state)
    if outside_cut:
        outer_span = max(amount, through_depth, 1.0) * 2.0
        with BuildSketch(plane) as outer_builder:
            Rectangle(outer_span, outer_span)
        outer_solid = extrude(outer_builder.sketch, amount=amount, both=True)
        inner_solid = extrude(profile, amount=amount, both=True)
        cutter = outer_solid.cut(inner_solid)
    elif both_sides or through_all or bool(globals().get("_aicad_sketch_from_face_ref", False)):
        cutter = extrude(profile, amount=amount, both=True)
    else:
        cutter = extrude(profile, amount=amount, dir=plane.z_dir)
    return part.cut(cutter)


def _aicad_trim_solid(current_result, plane_name, keep_side, offset):
    part = _aicad_as_part(current_result)
    if not _aicad_result_has_positive_solid(part):
        raise RuntimeError("trim_solid requires an existing solid result")
    trim_bb = _aicad_bbox(part)
    trim_pad = max(trim_bb["xlen"], trim_bb["ylen"], trim_bb["zlen"], abs(_aicad_to_float(offset)), 1.0) + 10.0
    xmin = trim_bb["xmin"] - trim_pad
    xmax = trim_bb["xmax"] + trim_pad
    ymin = trim_bb["ymin"] - trim_pad
    ymax = trim_bb["ymax"] + trim_pad
    zmin = trim_bb["zmin"] - trim_pad
    zmax = trim_bb["zmax"] + trim_pad
    plane_name = str(plane_name or "XY").upper()
    keep_side = str(keep_side or "above").lower()
    offset = _aicad_to_float(offset)
    if plane_name == "XY":
        if keep_side == "below":
            zmax = min(zmax, offset)
        else:
            zmin = max(zmin, offset)
    elif plane_name == "XZ":
        if keep_side == "back":
            ymax = min(ymax, offset)
        else:
            ymin = max(ymin, offset)
    else:
        if keep_side == "left":
            xmax = min(xmax, offset)
        else:
            xmin = max(xmin, offset)
    if (xmax - xmin) <= 1e-6 or (ymax - ymin) <= 1e-6 or (zmax - zmin) <= 1e-6:
        raise RuntimeError("trim_solid keep-side box is empty; check plane/offset/keep")
    trim_box = Box(xmax - xmin, ymax - ymin, zmax - zmin).move(Location(((xmin + xmax) / 2.0, (ymin + ymax) / 2.0, (zmin + zmax) / 2.0)))
    return part.intersect(trim_box)


def _aicad_apply_fillet(current_result, radius, edge_ids=None, edge_scope=None, edges_selector=None):
    part = _aicad_as_part(current_result)
    edges = _aicad_find_edges_by_ids(part, edge_ids) if edge_ids else _aicad_select_edges_by_scope(part, edge_scope=edge_scope, edges_selector=edges_selector)
    return fillet(edges, _aicad_to_float(radius, 1.0))


def _aicad_apply_chamfer(current_result, distance, edge_ids=None, edge_scope=None, edges_selector=None):
    part = _aicad_as_part(current_result)
    edges = _aicad_find_edges_by_ids(part, edge_ids) if edge_ids else _aicad_select_edges_by_scope(part, edge_scope=edge_scope, edges_selector=edges_selector)
    return chamfer(edges, _aicad_to_float(distance, 1.0))


def _aicad_localize_points_for_plane(plane, points_raw):
    local_points = []
    for point in list(points_raw or []):
        if not isinstance(point, (list, tuple)) or len(point) < 2:
            continue
        if len(point) >= 3 and all(isinstance(item, (int, float)) for item in point[:3]):
            try:
                local = plane.to_local_coords(Vector(float(point[0]), float(point[1]), float(point[2])))
                local_points.append([float(local.x), float(local.y)])
                continue
            except Exception:
                pass
        if all(isinstance(item, (int, float)) for item in point[:2]):
            local_points.append([float(point[0]), float(point[1])])
    return local_points or [[0.0, 0.0]]


def _aicad_resolve_face_and_plane(current_result, sketch_state, explicit_face_id=None, face_hint=None, local_points=None):
    part = _aicad_as_part(current_result)
    face = None
    if explicit_face_id:
        face = _aicad_find_face_by_id(part, explicit_face_id)
    if face is None and explicit_face_id:
        face = _aicad_find_face_by_hint(part, face_hint, local_points=local_points)
    if face is None and bool(globals().get("_aicad_sketch_from_face_ref", False)) and globals().get("_aicad_sketch_face_id"):
        face = _aicad_find_face_by_id(part, globals().get("_aicad_sketch_face_id"))
    if face is None and bool(globals().get("_aicad_sketch_from_face_ref", False)):
        face = _aicad_find_face_by_hint(part, globals().get("_aicad_sketch_face_hint"), local_points=local_points)
    if face is None and _aicad_result_has_positive_solid(part):
        face = _aicad_find_face_by_hint(part, face_hint or "top_faces", local_points=local_points)
    if face is None:
        return None, _aicad_state_plane(sketch_state)
    return face, Plane(face)


def _aicad_apply_holes(current_result, sketch_state, diameter, depth, points_raw, explicit_face_id=None, face_hint=None, countersink_diameter=None, countersink_angle=90.0):
    part = _aicad_as_part(current_result)
    if not _aicad_result_has_positive_solid(part):
        raise RuntimeError("hole requires an existing solid result")
    face, plane = _aicad_resolve_face_and_plane(part, sketch_state, explicit_face_id=explicit_face_id, face_hint=face_hint, local_points=points_raw)
    local_points = _aicad_localize_points_for_plane(plane, points_raw)
    local_points = _aicad_translate_positive_local_points(face, plane, local_points)
    bbox = _aicad_bbox(part)
    through_depth = max(bbox["xlen"], bbox["ylen"], bbox["zlen"], _aicad_to_float(diameter)) * 4.0
    depth_value = depth
    if isinstance(depth_value, str) and depth_value.strip().lower() in {"through", "through_all", "all"}:
        depth_value = through_depth
    elif not isinstance(depth_value, (int, float)) or float(depth_value) <= 0.0:
        depth_value = through_depth
    has_countersink = isinstance(countersink_diameter, (int, float)) and float(countersink_diameter) > float(diameter)
    with BuildPart(plane) as part_builder:
        add(part)
        for point in local_points:
            with Locations((_aicad_to_float(point[0]), _aicad_to_float(point[1]))):
                if has_countersink:
                    CounterSinkHole(
                        radius=float(diameter) / 2.0,
                        counter_sink_radius=float(countersink_diameter) / 2.0,
                        depth=float(depth_value),
                        counter_sink_angle=_aicad_to_float(countersink_angle, 90.0),
                    )
                else:
                    Hole(radius=float(diameter) / 2.0, depth=float(depth_value))
    return part_builder.part


def _aicad_apply_sphere_recesses(current_result, sketch_state, radius, points_raw, explicit_face_id=None, face_hint=None):
    part = _aicad_as_part(current_result)
    if not _aicad_result_has_positive_solid(part):
        raise RuntimeError("sphere_recess requires an existing solid result")
    face, plane = _aicad_resolve_face_and_plane(part, sketch_state, explicit_face_id=explicit_face_id, face_hint=face_hint, local_points=points_raw)
    local_points = _aicad_localize_points_for_plane(plane, points_raw)
    local_points = _aicad_translate_positive_local_points(face, plane, local_points)
    with BuildPart() as part_builder:
        add(part)
        for point in local_points:
            world_center = plane.from_local_coords((_aicad_to_float(point[0]), _aicad_to_float(point[1]), 0.0))
            add(Sphere(_aicad_to_float(radius, 1.0)).move(Location((_aicad_to_float(world_center.x), _aicad_to_float(world_center.y), _aicad_to_float(world_center.z)))), mode=Mode.SUBTRACT)
    return part_builder.part


def _aicad_pattern_linear(current_result, pattern_source, count, spacing, direction):
    part = _aicad_as_part(current_result)
    if pattern_source is None:
        raise RuntimeError("pattern_linear requires a preceding additive feature")
    source = _aicad_as_part(pattern_source)
    direction = _aicad_direction_vector3(direction)
    spacing = _aicad_to_float(spacing, 0.0)
    pattern_union = source
    with BuildPart() as part_builder:
        add(part)
        for index in range(1, max(2, int(count))):
            shift = float(index) * spacing
            copy_shape = source.moved(Location((direction[0] * shift, direction[1] * shift, direction[2] * shift)))
            add(copy_shape)
            pattern_union = pattern_union.fuse(copy_shape)
    return part_builder.part, pattern_union


def _aicad_pattern_circular(current_result, pattern_source, count, axis, center, total_angle):
    part = _aicad_as_part(current_result)
    if pattern_source is None:
        raise RuntimeError("pattern_circular requires a preceding additive feature")
    source = _aicad_as_part(pattern_source)
    axis_dir = _aicad_direction_vector3(axis)
    if not isinstance(center, (list, tuple)) or len(center) < 3:
        center = [0.0, 0.0, 0.0]
    axis_obj = Axis(
        (_aicad_to_float(center[0]), _aicad_to_float(center[1]), _aicad_to_float(center[2])),
        axis_dir,
    )
    pattern_union = source
    with BuildPart() as part_builder:
        add(part)
        for index in range(1, max(2, int(count))):
            angle = (_aicad_to_float(total_angle, 360.0) * float(index)) / float(max(2, int(count)))
            copy_shape = source.rotate(axis_obj, angle)
            add(copy_shape)
            pattern_union = pattern_union.fuse(copy_shape)
    return part_builder.part, pattern_union


def _aicad_transition_enum(token):
    mapping = {
        "right": Transition.RIGHT,
        "round": Transition.ROUND,
        "transformed": Transition.TRANSFORMED,
    }
    return mapping.get(str(token or "transformed").strip().lower(), Transition.TRANSFORMED)


def _aicad_resolve_revolve_axis_points(axis, axis_start, axis_end, plane, use_sketch):
    axis_token = str(axis or "Z").upper()
    plane_token = str(plane or "XY").upper()
    if use_sketch and isinstance(axis_start, (list, tuple)) and isinstance(axis_end, (list, tuple)) and len(axis_start) >= 2 and len(axis_end) >= 2 and len(axis_start) < 3 and len(axis_end) < 3:
        return (
            (float(axis_start[0]), float(axis_start[1])),
            (float(axis_end[0]), float(axis_end[1])),
        )
    local_axis_map = {
        "XY": {"X": ((0.0, 0.0), (1.0, 0.0)), "Y": ((0.0, 0.0), (0.0, 1.0))},
        "XZ": {"X": ((0.0, 0.0), (1.0, 0.0)), "Z": ((0.0, 0.0), (0.0, 1.0))},
        "YZ": {"Y": ((0.0, 0.0), (1.0, 0.0)), "Z": ((0.0, 0.0), (0.0, 1.0))},
    }
    if use_sketch:
        mapped = local_axis_map.get(plane_token, {}).get(axis_token)
        if mapped is not None:
            return mapped
    start = (0.0, 0.0, 0.0)
    end = {"X": (1.0, 0.0, 0.0), "Y": (0.0, 1.0, 0.0), "Z": (0.0, 0.0, 1.0)}.get(axis_token, (0.0, 0.0, 1.0))
    if isinstance(axis_start, (list, tuple)) and len(axis_start) >= 3:
        start = (float(axis_start[0]), float(axis_start[1]), float(axis_start[2]))
    if isinstance(axis_end, (list, tuple)) and len(axis_end) >= 3:
        end = (float(axis_end[0]), float(axis_end[1]), float(axis_end[2]))
    return start, end


def _aicad_axis_world_points(axis, axis_start, axis_end, sketch_state):
    use_sketch = isinstance(sketch_state, dict)
    plane_name = _aicad_state_plane_name(sketch_state)
    resolved = _aicad_resolve_revolve_axis_points(axis, axis_start, axis_end, plane_name, use_sketch)
    if isinstance(resolved[0], tuple) and len(resolved[0]) == 2:
        plane = _aicad_state_plane(sketch_state)
        start_world = plane.from_local_coords((resolved[0][0], resolved[0][1], 0.0))
        end_world = plane.from_local_coords((resolved[1][0], resolved[1][1], 0.0))
        return (
            (_aicad_to_float(start_world.x), _aicad_to_float(start_world.y), _aicad_to_float(start_world.z)),
            (_aicad_to_float(end_world.x), _aicad_to_float(end_world.y), _aicad_to_float(end_world.z)),
        )
    return resolved


def _aicad_apply_revolve(current_result, sketch_state, angle_degrees, axis, axis_start, axis_end, is_cut=False, use_cross_section=False, stepped_profile=None, sketch_origin_3d=None):
    part = _aicad_as_part(current_result)
    angle_degrees = _aicad_to_float(angle_degrees, 360.0)
    if stepped_profile and not is_cut and abs(float(angle_degrees) - 360.0) <= 1e-6:
        constructed = _aicad_build_stepped_solid(
            axis,
            stepped_profile.get("lengths", []),
            stepped_profile.get("radii", []),
            sketch_origin_3d or [0.0, 0.0, 0.0],
            stepped_profile.get("bands"),
        )
        if constructed is not None:
            if _aicad_result_has_positive_solid(part):
                return part.fuse(constructed), constructed
            return constructed, constructed
    profile_shape = _aicad_resolve_profile_shape(sketch_state if sketch_state is not None else current_result)
    profile_face = _aicad_make_face_from_shape(profile_shape)
    if profile_face is None:
        raise RuntimeError("revolve requires a closed profile sketch")
    axis_world_start, axis_world_end = _aicad_axis_world_points(axis, axis_start, axis_end, sketch_state)
    axis_obj = Axis(axis_world_start, axis_world_end)
    revolved_shape = revolve(profile_face, axis=axis_obj, revolution_arc=angle_degrees)
    if is_cut and use_cross_section and str(axis or "Z").upper() == "Z":
        solid_bbox = _aicad_bbox(part)
        sketch_shape = _aicad_resolve_profile_shape(sketch_state)
        sketch_bbox = _aicad_bbox(sketch_shape)
        outer_radius = max(solid_bbox["xlen"], solid_bbox["ylen"]) / 2.0
        dims = [abs(sketch_bbox["xlen"]), abs(sketch_bbox["ylen"]), abs(sketch_bbox["zlen"])]
        pos_dims = sorted([item for item in dims if item > 1e-6])
        if len(pos_dims) >= 2:
            depth = pos_dims[0]
            width = pos_dims[-1]
        elif len(pos_dims) == 1:
            depth = max(0.5, pos_dims[0] * 0.4)
            width = pos_dims[0]
        else:
            depth = 2.0
            width = 5.0
        inner_radius = max(0.1, outer_radius - max(depth, 0.1))
        axial_center = (sketch_bbox["zmin"] + sketch_bbox["zmax"]) / 2.0 if abs(sketch_bbox["zlen"]) > 1e-6 else (solid_bbox["zmin"] + solid_bbox["zmax"]) / 2.0
        cutter = _aicad_make_axis_annulus("Z", inner_radius, outer_radius, max(0.2, width), axial_center - (max(0.2, width) / 2.0), [0.0, 0.0, 0.0])
        return part.cut(cutter), None
    if is_cut:
        return part.cut(revolved_shape), None
    if _aicad_result_has_positive_solid(part):
        return part.fuse(revolved_shape), revolved_shape
    return revolved_shape, revolved_shape


def _aicad_apply_loft(current_result, loft_profiles, sketch_state, explicit_loft_point=None, explicit_loft_height=None, ruled=True):
    shapes = list(loft_profiles or [])
    current_profile = _aicad_resolve_profile_shape(sketch_state)
    if current_profile is not None and not shapes:
        shapes.append(current_profile)
    apex_point = None
    if isinstance(explicit_loft_point, (list, tuple)) and len(explicit_loft_point) >= 3:
        apex_point = [_aicad_to_float(explicit_loft_point[0]), _aicad_to_float(explicit_loft_point[1]), _aicad_to_float(explicit_loft_point[2])]
    elif isinstance(explicit_loft_height, (int, float)) and shapes:
        origin = list(globals().get("_aicad_sketch_origin_3d", [0.0, 0.0, 0.0]))
        while len(origin) < 3:
            origin.append(0.0)
        apex_point = [_aicad_to_float(origin[0]), _aicad_to_float(origin[1]), _aicad_to_float(origin[2]) + abs(float(explicit_loft_height))]
    if apex_point is not None:
        if not shapes:
            raise RuntimeError("loft requires at least one profile when using an apex point")
        sections = [_aicad_make_face_from_shape(shapes[0]), Vertex(tuple(apex_point))]
    else:
        if len(shapes) < 2:
            raise RuntimeError("loft requires at least two captured profiles or one profile plus an apex point")
        sections = []
        for shape in shapes:
            face = _aicad_make_face_from_shape(shape)
            if face is not None:
                sections.append(face)
    loft_solid = loft(sections, ruled=bool(ruled))
    if _aicad_result_has_positive_solid(current_result):
        return _aicad_as_part(current_result).fuse(loft_solid), loft_solid
    return loft_solid, loft_solid


def _aicad_apply_sweep(current_result, sketch_state, sweep_path, transition, is_frenet=False):
    if sweep_path is None:
        raise RuntimeError("sweep requires a captured path sketch before the profile sketch")
    profile_shape = _aicad_resolve_profile_shape(sketch_state if sketch_state is not None else current_result)
    profile_face = _aicad_make_face_from_shape(profile_shape)
    if profile_face is None:
        raise RuntimeError("sweep requires a closed face profile")
    swept = sweep(profile_face, path=sweep_path, is_frenet=bool(is_frenet), transition=_aicad_transition_enum(transition))
    if _aicad_result_has_positive_solid(current_result):
        return _aicad_as_part(current_result).fuse(swept), swept
    return swept, swept
