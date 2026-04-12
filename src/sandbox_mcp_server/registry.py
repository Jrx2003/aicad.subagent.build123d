from __future__ import annotations

from dataclasses import dataclass
import math
import re
from typing import Any

from sandbox_mcp_server.contracts import (
    CADActionType,
    CADParamValue,
    CADActionInput,
    CADActionOutput,
    ExecuteBuild123dInput,
    ExecuteBuild123dProbeInput,
    ExecuteBuild123dProbeOutput,
    ExecuteBuild123dOutput,
    GetHistoryInput,
    QueryFeatureProbesInput,
    QueryFeatureProbesOutput,
    QueryGeometryInput,
    QueryGeometryOutput,
    QuerySketchInput,
    QuerySketchOutput,
    QuerySnapshotInput,
    QuerySnapshotOutput,
    QueryTopologyInput,
    QueryTopologyOutput,
    RenderViewInput,
    RenderViewOutput,
    ValidateRequirementInput,
    ValidateRequirementOutput,
)

TOPOLOGY_REF_PATTERN = re.compile(
    r"^(?P<kind>face|edge):(?P<step>[0-9]+):(?P<entity_id>[A-Z]_[A-Za-z0-9_]+)$"
)


@dataclass(frozen=True)
class ActionParamDefinition:
    name: str
    description: str
    required: bool = False
    default: Any | None = None
    aliases: tuple[str, ...] = ()


@dataclass(frozen=True)
class ActionDefinition:
    action_type: CADActionType
    summary: str
    translator: str
    params: tuple[ActionParamDefinition, ...]
    exposure_bundles: tuple[str, ...]
    topology_fields: tuple[str, ...] = ()


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    input_model: type
    output_model: type
    prompt_schema_lines: tuple[str, ...]
    exposure_bundles: tuple[str, ...]


@dataclass(frozen=True)
class ExposureBundleDefinition:
    bundle_id: str
    title: str
    summary: str
    action_types: tuple[CADActionType, ...]
    query_tools: tuple[str, ...]
    topology_hints: tuple[str, ...]
    decision_rules: tuple[str, ...]
    library_patterns: tuple[str, ...]


@dataclass(frozen=True)
class RequirementSemantics:
    normalized_text: str
    face_targets: tuple[str, ...]
    datum_planes: tuple[str, ...]
    multi_plane_additive_signature_options: tuple[
        tuple[tuple[float, float, float], ...], ...
    ]
    edge_targets: tuple[str, ...]
    mentions_subtractive_edit: bool
    mentions_additive_face_feature: bool
    mentions_hole: bool
    mentions_countersink: bool
    mentions_pattern: bool
    mentions_spherical_recess: bool
    mentions_fillet: bool
    mentions_chamfer: bool
    mentions_targeted_edge_feature: bool
    mentions_face_edit: bool
    mentions_nested_profile_cutout: bool
    mentions_profile_region_frame: bool
    mentions_revolved_groove_cut: bool
    mentions_sweep: bool
    mentions_notch_like: bool
    mentions_multi_plane_additive_union: bool
    prefers_explicit_inner_void_cut: bool


def _requirement_suggests_pattern_distribution(text: str) -> bool:
    return any(
        token in text
        for token in (
            "pattern",
            "array",
            "pitch circle",
            "bolt circle",
            "circular pattern",
            "evenly distributed",
        )
    )


def _requirement_mentions_half_shell_with_split_surface(text: str) -> bool:
    if not text:
        return False
    half_shell_tokens = (
        "half-cylindrical",
        "half cylindrical",
        "half cylinder",
        "half a cylinder",
        "semi-cylindrical",
        "semi cylindrical",
        "semicylindrical",
        "half-shell",
        "half shell",
    )
    if not any(token in text for token in half_shell_tokens):
        return False
    return any(
        token in text
        for token in (
            "split surface",
            "split line",
            "semicircle",
            "semi-circle",
            "bearing housing",
            "bore",
            "lug",
            "flange",
        )
    )


def requirement_suggests_axisymmetric_profile(
    requirements: dict[str, Any] | None = None,
    requirement_text: str | None = None,
) -> bool:
    text = normalize_requirement_text(requirements, requirement_text)
    if not text:
        return False
    if any(
        token in text
        for token in ("axisymmetric", "revolve", "revolution", "rotational", "shaft")
    ):
        return True
    diameter_mentions = len(
        re.findall(r"(?:diameter|⌀|ø|\bphi\b)", text, re.IGNORECASE)
    )
    if diameter_mentions < 2:
        return False
    circular_stack_terms = (
        "concentric",
        "flange",
        "boss",
        "disk",
        "end cap",
        "center hole",
        "pitch circle",
    )
    return any(token in text for token in circular_stack_terms)


def requirement_has_explicit_anchor_coordinates(
    requirements: dict[str, Any] | None = None,
    requirement_text: str | None = None,
) -> bool:
    text = normalize_requirement_text(requirements, requirement_text)
    if not text:
        return False
    if re.search(
        r"\(\s*[-+]?[0-9]+(?:\.[0-9]+)?\s*,\s*[-+]?[0-9]+(?:\.[0-9]+)?\s*\)",
        text,
    ):
        return True
    if "center" not in text:
        return False
    axes = {
        str(match.group("axis")).upper()
        for match in re.finditer(
            r"\b(?P<axis>[xyz])\s*=\s*(?:±\s*)?[-+]?[0-9]+(?:\.[0-9]+)?",
            text,
            re.IGNORECASE,
        )
    }
    return len(axes) >= 2


def requirement_suggests_explicit_anchor_hole(
    requirements: dict[str, Any] | None = None,
    requirement_text: str | None = None,
    *,
    semantics: RequirementSemantics | None = None,
) -> bool:
    text = normalize_requirement_text(requirements, requirement_text)
    current_semantics = (
        semantics
        if semantics is not None
        else analyze_requirement_semantics(requirements, requirement_text)
    )
    if not text or not current_semantics.mentions_hole:
        return False
    return requirement_has_explicit_anchor_coordinates(
        requirements,
        requirement_text or text,
    )


def infer_requirement_probe_families(
    requirements: dict[str, Any] | None = None,
    requirement_text: str | None = None,
    *,
    semantics: RequirementSemantics | None = None,
) -> list[str]:
    text = normalize_requirement_text(requirements, requirement_text)
    if not text:
        return []
    current_semantics = (
        semantics
        if semantics is not None
        else analyze_requirement_semantics(requirements, requirement_text)
    )
    families: list[str] = []
    if (
        current_semantics.mentions_nested_profile_cutout
        or current_semantics.mentions_profile_region_frame
        or "hollow section" in text
        or "inner void" in text
        or "hollow" in text
        or "frame" in text
    ):
        families.append("nested_hollow_section")
    if (
        current_semantics.mentions_revolved_groove_cut
        or "annular groove" in text
        or ("groove" in text and "revolve" in text)
    ):
        families.append("annular_groove")
    if current_semantics.mentions_spherical_recess:
        families.append("spherical_recess")
    if current_semantics.mentions_pattern:
        families.append("pattern_distribution")
    if requirement_suggests_explicit_anchor_hole(
        requirements,
        requirement_text or text,
        semantics=current_semantics,
    ):
        families.append("explicit_anchor_hole")
    if current_semantics.mentions_multi_plane_additive_union or "union" in text or "orthogonal" in text:
        families.append("orthogonal_union")
    if requirement_suggests_axisymmetric_profile(
        requirements,
        requirement_text or text,
    ) or _requirement_mentions_half_shell_with_split_surface(text):
        families.append("axisymmetric_profile")
    if requirement_requests_path_sweep(
        requirements,
        requirement_text or text,
        semantics=current_semantics,
    ):
        families.append("path_sweep")
    deduped: list[str] = []
    seen: set[str] = set()
    for family in families:
        normalized = str(family or "").strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(normalized)
    return deduped


@dataclass(frozen=True)
class RectangularNotchProfileSpec:
    preferred_plane: str | None
    outer_width: float
    outer_height: float
    inner_width: float
    inner_height: float
    bottom_offset: float | None = None


ACTION_DEFINITIONS: tuple[ActionDefinition, ...] = (
    ActionDefinition(
        action_type=CADActionType.CREATE_SKETCH,
        summary="Open a sketch on a plane or on a queried face reference.",
        translator="_build_create_sketch_code_lines",
        params=(
            ActionParamDefinition("plane", "Sketch plane or face alias.", default="XY"),
            ActionParamDefinition(
                "position",
                "Optional local sketch center as [x,y] or [x,y,z].",
                default=[0.0, 0.0],
                aliases=("center", "origin"),
            ),
            ActionParamDefinition(
                "face_ref",
                "Preferred step-local face reference from query_topology.",
                aliases=("target_face_ref",),
            ),
            ActionParamDefinition(
                "path_ref",
                "Preferred step-local path reference from query_sketch.",
            ),
            ActionParamDefinition(
                "path_endpoint",
                "Path endpoint to attach to: start or end.",
                default="end",
            ),
            ActionParamDefinition(
                "frame_mode",
                "How to resolve the profile frame from the path endpoint.",
                default="normal_to_path_tangent",
            ),
            ActionParamDefinition(
                "attach_to_solid",
                "Attach to a canonical solid face when using plane aliases.",
                default=False,
            ),
        ),
        exposure_bundles=("bootstrap_sketch", "face_attached_sketch"),
        topology_fields=("face_ref", "path_ref"),
    ),
    ActionDefinition(
        action_type=CADActionType.ADD_RECTANGLE,
        summary="Add a rectangle to the active sketch/workplane.",
        translator="_build_add_rectangle_code_lines",
        params=(
            ActionParamDefinition("width", "Rectangle width.", required=True),
            ActionParamDefinition("height", "Rectangle height.", required=True),
            ActionParamDefinition(
                "inner_width",
                "Optional concentric inner rectangle width for same-sketch frame/section profiles.",
            ),
            ActionParamDefinition(
                "inner_height",
                "Optional concentric inner rectangle height for same-sketch frame/section profiles.",
            ),
            ActionParamDefinition(
                "position",
                "Optional local rectangle placement in sketch coordinates.",
                default=[0.0, 0.0],
            ),
            ActionParamDefinition(
                "centered",
                "Backward-compatible flag; when true, interpret position as the rectangle center.",
                default=False,
            ),
            ActionParamDefinition(
                "anchor",
                "Explicit rectangle anchor: center, lower_left, lower_right, top_left, or top_right.",
                default="center",
            ),
        ),
        exposure_bundles=("bootstrap_sketch", "face_attached_sketch"),
    ),
    ActionDefinition(
        action_type=CADActionType.ADD_CIRCLE,
        summary="Add a circle to the active sketch/workplane.",
        translator="_build_add_circle_code_lines",
        params=(
            ActionParamDefinition("radius", "Circle radius.", aliases=("diameter",)),
            ActionParamDefinition(
                "radius_inner",
                "Optional concentric inner radius for same-sketch frame/section profiles.",
            ),
            ActionParamDefinition(
                "position",
                "Optional local circle center as [x,y] or [x,y,z].",
                default=[0.0, 0.0],
                aliases=("center",),
            ),
            ActionParamDefinition(
                "centers",
                "Optional list of local/world circle centers for repeated circles on the same sketch.",
                aliases=("positions",),
            ),
        ),
        exposure_bundles=("bootstrap_sketch", "face_attached_sketch"),
    ),
    ActionDefinition(
        action_type=CADActionType.ADD_POLYGON,
        summary="Add a polygon/polyline profile to the active sketch/workplane.",
        translator="_build_add_polygon_code_lines",
        params=(
            ActionParamDefinition("points", "Polyline points.", aliases=("vertices",)),
            ActionParamDefinition(
                "sides",
                "Regular polygon side count.",
                aliases=("n_sides", "num_sides", "side_count", "regular_sides"),
            ),
            ActionParamDefinition("radius_outer", "Outer radius.", aliases=("radius",)),
            ActionParamDefinition(
                "size_mode",
                "Regular polygon radius semantics: circumradius or apothem/distance_to_side.",
                aliases=("radius_mode", "polygon_size_mode"),
            ),
            ActionParamDefinition("side_length", "Regular polygon side length."),
            ActionParamDefinition("radius_inner", "Optional inner radius."),
            ActionParamDefinition(
                "rotation_degrees",
                "Optional regular-polygon phase/orientation in sketch-local degrees.",
                aliases=("rotation", "phase_degrees"),
            ),
            ActionParamDefinition("length_list", "Stepped profile lengths."),
            ActionParamDefinition("radius_list", "Stepped profile radii."),
        ),
        exposure_bundles=("bootstrap_sketch", "face_attached_sketch"),
    ),
    ActionDefinition(
        action_type=CADActionType.ADD_PATH,
        summary="Add an open or closed path sketch made of line and tangent-arc segments.",
        translator="_build_add_path_code_lines",
        params=(
            ActionParamDefinition(
                "points",
                "Optional open polyline points; simple point rails are normalized into path segments.",
                aliases=("vertices",),
            ),
            ActionParamDefinition("start", "Path start point as [x, y].", default=[0.0, 0.0]),
            ActionParamDefinition("segments", "Ordered path segment list.", required=True),
            ActionParamDefinition(
                "closed",
                "Whether to close the path.",
                default=False,
                aliases=("close",),
            ),
        ),
        exposure_bundles=("bootstrap_sketch", "path_sweep"),
    ),
    ActionDefinition(
        action_type=CADActionType.EXTRUDE,
        summary="Additively extrude the current sketch/profile into a solid feature.",
        translator="_build_extrude_code_lines",
        params=(
            ActionParamDefinition(
                "distance",
                "Extrusion distance.",
                required=True,
                aliases=("height", "length"),
            ),
            ActionParamDefinition("direction", "Extrusion direction.", default="up"),
            ActionParamDefinition(
                "both_sides",
                "Extrude symmetrically along both normal directions.",
                default=False,
                aliases=("symmetric", "symmetrical", "centered"),
            ),
        ),
        exposure_bundles=("additive_extrusion", "face_attached_sketch"),
    ),
    ActionDefinition(
        action_type=CADActionType.CUT_EXTRUDE,
        summary="Extrude-cut the current sketch/profile from the active solid.",
        translator="_build_cut_extrude_code_lines",
        params=(
            ActionParamDefinition(
                "distance",
                "Cut depth.",
                required=True,
                aliases=("depth", "height", "length"),
            ),
            ActionParamDefinition(
                "through_all",
                "Cut through the full host solid thickness instead of a fixed blind depth.",
                default=False,
                aliases=("condition",),
            ),
            ActionParamDefinition(
                "flip_side",
                "Remove material outside the profile when the CAD tool describes flip-side/outside cut behavior.",
                default=False,
                aliases=("outside_cut", "flip_side_to_cut"),
            ),
            ActionParamDefinition(
                "both_sides",
                "Cut symmetrically along both normal directions.",
                default=False,
            ),
        ),
        exposure_bundles=("subtractive_edit", "face_attached_sketch"),
    ),
    ActionDefinition(
        action_type=CADActionType.TRIM_SOLID,
        summary="Trim an existing solid against an axis-aligned datum plane and keep one side.",
        translator="_build_trim_solid_code_lines",
        params=(
            ActionParamDefinition("plane", "Datum plane alias XY/XZ/YZ or Top/Front/Right.", required=True),
            ActionParamDefinition(
                "offset",
                "Signed plane offset from the global origin along the plane normal.",
            ),
            ActionParamDefinition(
                "origin",
                "Optional plane origin [x, y, z]; the plane offset is derived from this point.",
                aliases=("position", "center"),
            ),
            ActionParamDefinition(
                "keep",
                "Which side to keep: below/above, front/back, or left/right depending on plane.",
                default="below",
                aliases=("keep_side",),
            ),
        ),
        exposure_bundles=("subtractive_edit",),
    ),
    ActionDefinition(
        action_type=CADActionType.REVOLVE,
        summary="Revolve the current sketch/profile, optionally as a cut.",
        translator="_build_revolve_code_lines",
        params=(
            ActionParamDefinition("angle", "Revolve angle in degrees.", default=360.0),
            ActionParamDefinition("axis", "Axis alias X/Y/Z.", default="Z"),
            ActionParamDefinition("axis_start", "Axis start point."),
            ActionParamDefinition("axis_end", "Axis end point."),
            ActionParamDefinition(
                "operation",
                "Boolean mode: add or cut.",
                default="add",
                aliases=("mode", "boolean"),
            ),
        ),
        exposure_bundles=("additive_extrusion", "subtractive_edit"),
    ),
    ActionDefinition(
        action_type=CADActionType.LOFT,
        summary="Loft between available profiles.",
        translator="_build_loft_code_lines",
        params=(
            ActionParamDefinition("to_point", "Optional apex point [x, y, z] for point lofts."),
            ActionParamDefinition("height", "Optional apex height above the current sketch origin."),
        ),
        exposure_bundles=("additive_extrusion",),
    ),
    ActionDefinition(
        action_type=CADActionType.SWEEP,
        summary="Sweep the current closed profile along the previously captured path sketch.",
        translator="_build_sweep_code_lines",
        params=(
            ActionParamDefinition("transition", "Sweep transition mode.", default="transformed"),
            ActionParamDefinition("is_frenet", "Use Frenet frame along the path.", default=False),
        ),
        exposure_bundles=("path_sweep",),
    ),
    ActionDefinition(
        action_type=CADActionType.FILLET,
        summary="Apply a fillet to selected edges; prefer edge_refs over global selectors.",
        translator="_build_fillet_code_lines",
        params=(
            ActionParamDefinition("radius", "Fillet radius.", required=True),
            ActionParamDefinition(
                "edge_refs",
                "Preferred step-local edge references from query_topology.",
                aliases=("edge_ref",),
            ),
            ActionParamDefinition(
                "edges_selector",
                "Legacy selector-string fallback (for example >Z or |Z).",
            ),
            ActionParamDefinition(
                "edge_scope",
                "Legacy coarse edge scope fallback.",
            ),
        ),
        exposure_bundles=("edge_ref_features",),
        topology_fields=("edge_refs",),
    ),
    ActionDefinition(
        action_type=CADActionType.CHAMFER,
        summary="Apply a chamfer to selected edges; prefer edge_refs over global selectors.",
        translator="_build_chamfer_code_lines",
        params=(
            ActionParamDefinition("distance", "Chamfer distance.", required=True),
            ActionParamDefinition(
                "edge_refs",
                "Preferred step-local edge references from query_topology.",
                aliases=("edge_ref",),
            ),
            ActionParamDefinition(
                "edges_selector",
                "Legacy selector-string fallback (for example >Z or |Z).",
            ),
            ActionParamDefinition(
                "edge_scope",
                "Legacy coarse edge scope fallback.",
            ),
        ),
        exposure_bundles=("edge_ref_features",),
        topology_fields=("edge_refs",),
    ),
    ActionDefinition(
        action_type=CADActionType.HOLE,
        summary="Create one or more circular holes on the current face-attached workplane.",
        translator="_build_hole_code_lines",
        params=(
            ActionParamDefinition("diameter", "Hole diameter.", required=True),
            ActionParamDefinition("depth", "Optional hole depth."),
            ActionParamDefinition(
                "face_ref",
                "Preferred step-local face reference from query_topology.",
            ),
            ActionParamDefinition(
                "position",
                "Hole center on local workplane.",
                aliases=("center",),
            ),
            ActionParamDefinition(
                "centers",
                "Optional list of local workplane centers for repeated holes.",
                aliases=("positions",),
            ),
            ActionParamDefinition(
                "countersink_diameter",
                "Optional countersink head diameter.",
                aliases=("countersink_head_diameter", "head_diameter"),
            ),
            ActionParamDefinition(
                "countersink_angle",
                "Optional countersink included angle in degrees.",
                default=90.0,
            ),
        ),
        exposure_bundles=("subtractive_edit",),
        topology_fields=("face_ref",),
    ),
    ActionDefinition(
        action_type=CADActionType.SPHERE_RECESS,
        summary="Cut one or more hemispherical recesses on a target face.",
        translator="_build_sphere_recess_code_lines",
        params=(
            ActionParamDefinition("radius", "Hemisphere radius."),
            ActionParamDefinition("diameter", "Hemisphere diameter."),
            ActionParamDefinition(
                "position",
                "Single local face-workplane center for the recess.",
                aliases=("center",),
            ),
            ActionParamDefinition(
                "centers",
                "Optional list of repeated local face-workplane centers.",
                aliases=("positions",),
            ),
            ActionParamDefinition(
                "face_ref",
                "Preferred step-local face reference from query_topology.",
            ),
        ),
        exposure_bundles=("subtractive_edit", "spherical_face_edit", "feature_patterns"),
        topology_fields=("face_ref",),
    ),
    ActionDefinition(
        action_type=CADActionType.PATTERN_LINEAR,
        summary="Apply a linear feature pattern.",
        translator="_build_pattern_linear_code_lines",
        params=(
            ActionParamDefinition("count", "Feature count.", default=2),
            ActionParamDefinition("spacing", "Feature spacing.", default=10),
            ActionParamDefinition(
                "direction",
                "Pattern direction axis token or vector ('X', 'Y', 'Z', or [x, y, z]).",
                default="X",
            ),
        ),
        exposure_bundles=("feature_patterns",),
    ),
    ActionDefinition(
        action_type=CADActionType.PATTERN_CIRCULAR,
        summary="Apply a circular feature pattern.",
        translator="_build_pattern_circular_code_lines",
        params=(
            ActionParamDefinition("count", "Feature count.", default=4),
            ActionParamDefinition(
                "center",
                "Pattern center in world coordinates [x, y, z]. Defaults to the global origin.",
                default=[0.0, 0.0, 0.0],
            ),
            ActionParamDefinition(
                "axis",
                "Pattern axis token or vector ('X', 'Y', 'Z', or [x, y, z]).",
                default="Z",
            ),
            ActionParamDefinition(
                "total_angle",
                "Total sweep angle in degrees. Use 360 for a full circular pattern.",
                default=360.0,
                aliases=("angle",),
            ),
        ),
        exposure_bundles=("feature_patterns",),
    ),
    ActionDefinition(
        action_type=CADActionType.ROLLBACK,
        summary="Rewind the replay prefix to a known-good step before continuing.",
        translator="_build_state_passthrough_code_lines",
        params=(
            ActionParamDefinition(
                "target_step",
                "Keep action history only up to this step before inserting the rollback marker.",
                default=None,
            ),
            ActionParamDefinition(
                "steps_back",
                "Shorthand for removing this many most-recent steps when target_step is not provided.",
                default=1,
            ),
        ),
        exposure_bundles=("repair_state",),
    ),
    ActionDefinition(
        action_type=CADActionType.SNAPSHOT,
        summary="Keep state unchanged while capturing a snapshot.",
        translator="_build_state_passthrough_code_lines",
        params=(),
        exposure_bundles=("repair_state",),
    ),
    ActionDefinition(
        action_type=CADActionType.MODIFY_ACTION,
        summary="Modify a previous action through the dedicated service path.",
        translator="_build_noop_code_lines",
        params=(),
        exposure_bundles=("repair_state",),
    ),
    ActionDefinition(
        action_type=CADActionType.CLEAR_SESSION,
        summary="Reset session state through the dedicated service path.",
        translator="_build_noop_code_lines",
        params=(),
        exposure_bundles=("repair_state",),
    ),
    ActionDefinition(
        action_type=CADActionType.GET_HISTORY,
        summary="Retrieve action history through the dedicated tool path.",
        translator="_build_noop_code_lines",
        params=(),
        exposure_bundles=("repair_state",),
    ),
)

ACTION_DEFINITIONS_BY_NAME = {
    definition.action_type.value: definition for definition in ACTION_DEFINITIONS
}

ACTION_DEFINITIONS_BY_ENUM = {
    definition.action_type: definition for definition in ACTION_DEFINITIONS
}

TOOL_DEFINITIONS: tuple[ToolDefinition, ...] = (
    ToolDefinition(
        name="execute_build123d",
        description=(
            "Execute Build123d code in the Docker sandbox and return execution logs plus generated artifacts."
        ),
        input_model=ExecuteBuild123dInput,
        output_model=ExecuteBuild123dOutput,
        prompt_schema_lines=(),
        exposure_bundles=("inspection_tools",),
    ),
    ToolDefinition(
        name="apply_cad_action",
        description=(
            "Apply a single typed CAD action, replaying prior successful actions on a cleared session."
        ),
        input_model=CADActionInput,
        output_model=CADActionOutput,
        prompt_schema_lines=(),
        exposure_bundles=("inspection_tools",),
    ),
    ToolDefinition(
        name="get_history",
        description="Return action history for a CAD modeling session.",
        input_model=GetHistoryInput,
        output_model=CADActionOutput,
        prompt_schema_lines=(),
        exposure_bundles=("inspection_tools",),
    ),
    ToolDefinition(
        name="query_snapshot",
        description="Inspect latest or specified session snapshot without mutating CAD state.",
        input_model=QuerySnapshotInput,
        output_model=QuerySnapshotOutput,
        prompt_schema_lines=(
            "- inspection.query_snapshot: step/include_history",
        ),
        exposure_bundles=("inspection_tools",),
    ),
    ToolDefinition(
        name="query_sketch",
        description="Inspect latest or specified pre-solid sketch/path/profile state, including compact relation-base summaries when available, without mutating CAD state.",
        input_model=QuerySketchInput,
        output_model=QuerySketchOutput,
        prompt_schema_lines=(
            "- inspection.query_sketch: step(optional)",
        ),
        exposure_bundles=("inspection_tools",),
    ),
    ToolDefinition(
        name="query_geometry",
        description="Query compact geometry facts and object windows by session and step.",
        input_model=QueryGeometryInput,
        output_model=QueryGeometryOutput,
        prompt_schema_lines=(
            "- inspection.query_geometry: include_solids/include_faces/include_edges/max_items_per_type/entity_ids/solid_offset/face_offset/edge_offset/step",
        ),
        exposure_bundles=("inspection_tools",),
    ),
    ToolDefinition(
        name="render_view",
        description="Render a custom camera view for local or global geometric inspection.",
        input_model=RenderViewInput,
        output_model=RenderViewOutput,
        prompt_schema_lines=(
            "- inspection.render_view: intent/global_overview|detail_check, step, azimuth_deg, elevation_deg, zoom, width_px, height_px, style, target_entity_ids, focus_center, focus_span, focus_padding_ratio",
        ),
        exposure_bundles=("inspection_tools",),
    ),
    ToolDefinition(
        name="query_topology",
        description="Query step-local face/edge refs plus adjacency, parent-solid topology facts, and compact relation-base summaries when available.",
        input_model=QueryTopologyInput,
        output_model=QueryTopologyOutput,
        prompt_schema_lines=(
            "- inspection.query_topology: include_faces/include_edges/max_items_per_type/entity_ids/ref_ids/selection_hints/face_offset/edge_offset/step",
        ),
        exposure_bundles=("inspection_tools",),
    ),
    ToolDefinition(
        name="query_feature_probes",
        description="Run family-specific geometric probes so the model can diagnose likely hollow-section, annular-groove, anchor-hole, union, or axisymmetric mismatches without a broad topology dump.",
        input_model=QueryFeatureProbesInput,
        output_model=QueryFeatureProbesOutput,
        prompt_schema_lines=(
            "- inspection.query_feature_probes: requirement_text/requirements/families/step",
        ),
        exposure_bundles=("inspection_tools",),
    ),
    ToolDefinition(
        name="validate_requirement",
        description="Evaluate current CAD state against requirements and report semantic blockers/checks.",
        input_model=ValidateRequirementInput,
        output_model=ValidateRequirementOutput,
        prompt_schema_lines=(
            "- inspection.validate_requirement: step(optional)",
        ),
        exposure_bundles=("inspection_tools",),
    ),
    ToolDefinition(
        name="execute_build123d_probe",
        description="Execute diagnostics-only Build123d probe code without mutating the authoritative modeling session.",
        input_model=ExecuteBuild123dProbeInput,
        output_model=ExecuteBuild123dProbeOutput,
        prompt_schema_lines=(
            "- diagnostics.execute_build123d_probe: code/timeout_seconds/include_artifact_content",
        ),
        exposure_bundles=("inspection_tools",),
    ),
)

TOOL_DEFINITIONS_BY_NAME = {
    definition.name: definition for definition in TOOL_DEFINITIONS
}

EXPOSURE_BUNDLES: tuple[ExposureBundleDefinition, ...] = (
    ExposureBundleDefinition(
        bundle_id="bootstrap_sketch",
        title="Base Sketch",
        summary="Use these actions to establish the first valid 2D profile quickly.",
        action_types=(
            CADActionType.CREATE_SKETCH,
            CADActionType.ADD_RECTANGLE,
            CADActionType.ADD_CIRCLE,
            CADActionType.ADD_POLYGON,
        ),
        query_tools=("query_geometry", "validate_requirement"),
        topology_hints=(),
        decision_rules=(
            "If no solid exists, prioritize a simple valid profile first.",
            "Avoid decorative actions before the first solid exists.",
        ),
        library_patterns=(
            "Open one sketch, add the profile, then close it with extrude or revolve.",
            "Keep units in millimeters and dimensions explicit.",
            "For stepped shafts or double-ended studs defined by axial lengths and radii, prefer add_polygon(length_list=[...], radius_list=[...]) over hand-authored point lists.",
        ),
    ),
    ExposureBundleDefinition(
        bundle_id="additive_extrusion",
        title="Additive Solid",
        summary="Use these actions to turn valid profiles into solids.",
        action_types=(
            CADActionType.EXTRUDE,
            CADActionType.REVOLVE,
            CADActionType.LOFT,
        ),
        query_tools=("query_geometry", "validate_requirement"),
        topology_hints=(),
        decision_rules=(
            "Complete the base solid before local detail edits when possible.",
            "Use both_sides=true for explicitly symmetric extrusion requirements.",
        ),
        library_patterns=(
            "Extrude current sketch/profile directly and reset sketch state.",
            "Prefer the simplest valid solid-building operation that satisfies the requirement.",
            "For revolve-based shaft/stud requirements, keep the half-profile closed back to the axis so the result is a solid with positive volume.",
            "For revolved hollow parts with explicit inner radius/diameter and outer radius/diameter, keep the profile closed and off-axis; a nonzero inner radius is valid and should not be forced back to the axis.",
        ),
    ),
    ExposureBundleDefinition(
        bundle_id="path_sweep",
        title="Path Sweep",
        summary="Use an explicit path sketch plus a closed profile sketch, then sweep the profile along the path.",
        action_types=(
            CADActionType.ADD_PATH,
            CADActionType.SWEEP,
        ),
        query_tools=("query_sketch", "query_geometry", "validate_requirement"),
        topology_hints=(),
        decision_rules=(
            "Build the path sketch first, then open a second sketch for the closed profile before calling sweep.",
            "Do not continue to the profile sketch or sweep if query_sketch reports path_disconnected or another sketch blocker.",
            "Use open paths for rails and closed concentric profiles for hollow pipe-like sweeps.",
            "Map explicit view wording to planes before drawing the rail: front view -> XZ, top view -> XY, right/side view -> YZ.",
        ),
        library_patterns=(
            "Represent sweep rails as ordered line and tangent-arc segments instead of approximating them with polygon solids.",
            "Keep the rail open; keep the swept profile closed.",
            "For hollow bent tubes or pipes, use one annular profile and sweep it once along the full path.",
            "For pre-solid sweeps, keep the rail sketch and the profile sketch as two distinct sketch windows; do not collapse them into one plane.",
        ),
    ),
    ExposureBundleDefinition(
        bundle_id="loft_profile_stack",
        title="Loft Profile Stack",
        summary="Use ordered closed profile windows and query_sketch evidence before lofting.",
        action_types=(
            CADActionType.CREATE_SKETCH,
            CADActionType.ADD_RECTANGLE,
            CADActionType.ADD_CIRCLE,
            CADActionType.ADD_POLYGON,
            CADActionType.LOFT,
        ),
        query_tools=("query_sketch", "query_geometry", "validate_requirement"),
        topology_hints=(),
        decision_rules=(
            "Build one closed profile window at a time and confirm the ordered profile stack with query_sketch before loft.",
            "Do not jump straight to loft if query_sketch does not prove the intended number of loftable profile windows.",
            "When taper/frustum semantics are explicit, preserve sloped profile evidence instead of approximating the stack with unrelated orthogonal sections.",
        ),
        library_patterns=(
            "Keep loft profiles as separate pre-solid sketch windows; do not collapse them into one window.",
            "Use loft only after the profile stack order, plane coverage, and closure are confirmed.",
        ),
    ),
    ExposureBundleDefinition(
        bundle_id="subtractive_edit",
        title="Subtractive Edit",
        summary="Use these actions for holes, cuts, and subtractive revolve operations.",
        action_types=(
            CADActionType.TRIM_SOLID,
            CADActionType.CUT_EXTRUDE,
            CADActionType.HOLE,
            CADActionType.SPHERE_RECESS,
            CADActionType.REVOLVE,
        ),
        query_tools=("query_geometry", "validate_requirement", "render_view"),
        topology_hints=(),
        decision_rules=(
            "Do not use subtractive edits as the first material operation when no solid exists.",
            "For notch/groove/cutout requirements, finish the core subtractive edit before decorative edges.",
            "For face-attached circular blind/through holes, prefer hole over add_circle + cut_extrude when the intent is drilling rather than an arbitrary profile cut.",
            "If the same face needs mixed subtractive circles with different diameters, especially a central cut plus a bolt-circle pattern, keep them in one face-attached sketch and finish with cut_extrude instead of splitting the work into sequential hole actions.",
            "For hemispherical or spherical pits/recesses on an existing face, prefer sphere_recess over add_circle plus a generic revolve workaround.",
            "When the requirement asks to split or trim a body above/below a datum plane to form a frustum or truncated solid, prefer trim_solid over inventing an unrelated sketch cut.",
        ),
        library_patterns=(
            "Use local sketch windows for cuts when editing an existing solid.",
            "After subtractive edits, confirm geometry delta before declaring complete.",
            "For repeated circular holes on one face, use one hole action with centers=[...] when the drill pattern is explicit.",
            "If the requirement explicitly says extrude cut / cut through the flange or names a construction circle plus a later circular array, preserve that sketch-and-cut workflow instead of collapsing it into direct hole calls.",
            "For repeated hemispherical pits on one face, use one sphere_recess action with centers=[...] rather than a seed revolve plus a fragile follow-up pattern.",
            "For axis-aligned body truncation, intersect the current solid with the kept half-space instead of approximating the trim with a small profile cut.",
        ),
    ),
    ExposureBundleDefinition(
        bundle_id="spherical_face_edit",
        title="Spherical Recess",
        summary="Use this for hemispherical or spherical recesses cut into an existing face.",
        action_types=(
            CADActionType.CREATE_SKETCH,
            CADActionType.SPHERE_RECESS,
        ),
        query_tools=("query_topology", "validate_requirement", "render_view"),
        topology_hints=("top_faces", "bottom_faces"),
        decision_rules=(
            "Attach to the target face first, then place the recess center in local face coordinates.",
            "Prefer sphere_recess for hemispherical pits instead of approximating them with add_circle plus revolve.",
        ),
        library_patterns=(
            "Use create_sketch.face_ref or sphere_recess.face_ref when the pit must land on a specific face.",
            "For a repeated pit layout on one face, keep the seed feature implicit and pass all local centers in sphere_recess.centers.",
        ),
    ),
    ExposureBundleDefinition(
        bundle_id="feature_patterns",
        title="Feature Pattern",
        summary="Use this when one seed feature must be repeated as a direct repeated face feature or an additive pattern.",
        action_types=(
            CADActionType.HOLE,
            CADActionType.SPHERE_RECESS,
            CADActionType.PATTERN_LINEAR,
            CADActionType.PATTERN_CIRCULAR,
        ),
        query_tools=("validate_requirement", "query_geometry", "render_view"),
        topology_hints=("top_faces", "bottom_faces"),
        decision_rules=(
            "When pattern intent is explicit and the repeated centers are known or derivable, prefer one direct feature action with centers=[...] over multiple seed-and-pattern rounds.",
            "Use centered-grid or pitch-circle layouts only when the requirement explicitly specifies spacing/count or pitch-circle/count anchors.",
            "If the existing solid already exposes the target face and the repeated centers are derivable, prefer one direct hole/sphere_recess action over reopening a sketch only to recreate a construction circle.",
            "If the requirement mixes a central cut with a secondary hole pattern of another diameter on the same face, do not switch to direct hole actions mid-flow; keep the face sketch coherent and remove all required circles with cut_extrude.",
            "Use pattern_linear/pattern_circular only after a seed additive feature already exists at the correct location and orientation.",
            "For repeated circumferential teeth/ribs/bosses on an existing host face, prefer one additive seed feature plus pattern_circular over inventing a path_sweep unless the requirement explicitly defines a true rail.",
        ),
        library_patterns=(
            "For a centered MxN grid, precompute the local centers around the face origin and cut all features in one action.",
            "For a circular pattern on one face, derive the pitch-circle radius and count, then express the full center list directly when possible.",
            "For repeated additive teeth/ribs/bosses where direct centers are not enough, build one seed additive feature first and then apply pattern_circular around the model axis.",
            "For annular serrations or repeated radial teeth, make one face-attached additive tooth seed and then pattern it; do not replace that with an unrelated sweep rail.",
        ),
    ),
    ExposureBundleDefinition(
        bundle_id="inner_void_cutout",
        title="Inner Void Cutout",
        summary="Use either a same-sketch nested-profile section or an explicit subtractive inner-cut stage when the requirement defines an inner void/frame region.",
        action_types=(
            CADActionType.CREATE_SKETCH,
            CADActionType.ADD_RECTANGLE,
            CADActionType.ADD_CIRCLE,
            CADActionType.ADD_POLYGON,
            CADActionType.CUT_EXTRUDE,
            CADActionType.HOLE,
        ),
        query_tools=("query_geometry", "validate_requirement"),
        topology_hints=("top_faces",),
        decision_rules=(
            "If the requirement explicitly says to extrude the section/frame region between nested profiles of the same shape family, a same-sketch multi-profile extrusion is valid.",
            "If the inner void uses a different shape family from the outer profile (for example circle outside plus square/rectangle inside), do not rely on implicit section selection in one sketch window.",
            "Otherwise build the outer solid first, then attach a top/target-face sketch to the existing solid and remove the inner profile with cut_extrude or hole.",
        ),
        library_patterns=(
            "For explicit same-shape frame-region requirements, keep the nested closed profiles in one sketch window and extrude the selected section.",
            "When the outer and inner profiles are the same shape family and share a center, prefer one profile action with inner dimensions/radius instead of rebuilding separate sketch windows.",
            "For mixed-shape nested voids, prefer outer-solid-first and then an explicit top/target-face cut for the inner void.",
            "Outer profile first, extrude to first solid, then sketch the inner cutout on the target face when a second subtractive stage is clearer.",
            "On an existing solid, prefer create_sketch.face_ref or create_sketch.attach_to_solid=true before the inner cut stage.",
            "Use cut_extrude for square/rectangular/polygonal internal voids when the planner already established the outer solid and needs a precise post-solid cut.",
        ),
    ),
    ExposureBundleDefinition(
        bundle_id="orthogonal_additive_union",
        title="Orthogonal Union",
        summary="Use multiple orthogonal sketch windows and additive features when the requirement composes one solid from separate plane-based extrusions.",
        action_types=(
            CADActionType.CREATE_SKETCH,
            CADActionType.ADD_RECTANGLE,
            CADActionType.ADD_POLYGON,
            CADActionType.EXTRUDE,
        ),
        query_tools=("query_geometry", "validate_requirement"),
        topology_hints=(),
        decision_rules=(
            "When the requirement names multiple datum planes with separate extrusions and a union, keep each additive sketch window explicit instead of collapsing to one base prism.",
            "Use one additive material action per required plane-aligned feature, and let later additive features merge into the existing solid.",
            "Keep rectangle width/height order faithful to the requirement text on every named plane; do not swap the two values just because the plane orientation changes.",
            "For explicit multi-plane unions, each later rectangle must add the required plane-aligned span instead of recreating an existing bar with swapped width/height.",
        ),
        library_patterns=(
            "Finish one valid additive sketch window, extrude it, then open the next orthogonal sketch on its named datum plane.",
            "Use both_sides=true when the requirement explicitly requests symmetric extrusion about a datum plane.",
            "On XZ/YZ sketches, width is still the first number and height is still the second number; keep the textual dimension order stable.",
        ),
    ),
    ExposureBundleDefinition(
        bundle_id="revolved_groove_cut",
        title="Revolved Groove Cut",
        summary="Use a local orthogonal sketch profile plus subtractive revolve for annular grooves and similar rotational cuts.",
        action_types=(
            CADActionType.CREATE_SKETCH,
            CADActionType.ADD_RECTANGLE,
            CADActionType.ADD_POLYGON,
            CADActionType.REVOLVE,
        ),
        query_tools=("query_geometry", "validate_requirement", "render_view"),
        topology_hints=(
            "outer_faces",
            "outer_edges",
            "primary_outer_faces",
            "primary_axis_outer_edges",
        ),
        decision_rules=(
            "Place the sketch on a plane that contains the revolve axis and align the local profile to the solid outer edge before revolving.",
            "For cylindrical groove cuts, keep the profile local to the sketch origin; do not mix a shifted sketch origin with an extra rectangle position unless inspection evidence requires it.",
            "When the requirement says 'at a height of H' without saying centered, treat H as the groove profile edge/shoulder coordinate on the axial direction, not automatically as the rectangle center.",
        ),
        library_patterns=(
            "Use create_sketch.position/origin to establish the groove centerline, then keep add_rectangle local when possible.",
            "For revolve cut, radial depth should be the dimension normal to the axis and axial width should be the dimension parallel to the axis.",
            "If the sketch origin is anchored at the requested groove height, shift the rectangle locally by half its axial size when the wording implies an edge-aligned height rather than a centered height.",
        ),
    ),
    ExposureBundleDefinition(
        bundle_id="face_attached_sketch",
        title="Face-Attached Sketch",
        summary="Use queried face refs to attach a new sketch to an exact existing face.",
        action_types=(
            CADActionType.CREATE_SKETCH,
            CADActionType.ADD_RECTANGLE,
            CADActionType.ADD_CIRCLE,
            CADActionType.ADD_POLYGON,
            CADActionType.EXTRUDE,
            CADActionType.CUT_EXTRUDE,
        ),
        query_tools=("query_topology", "render_view"),
        topology_hints=("top_faces", "bottom_faces", "outer_faces"),
        decision_rules=(
            "Query topology before attaching a sketch to a specific existing face.",
            "Treat refs as stale after topology-changing edits; re-query instead of guessing.",
            "For repeated same-profile circles on one face, prefer one add_circle action with centers=[...] over multiple single-circle actions.",
            "If those repeated circles are meant to become drilled holes, hole is the preferred material-removal primitive once the face attachment is clear.",
            "If the same face carries a central cut plus additional patterned holes of another diameter, keep one sketch window with all circles and finish with cut_extrude so the target face does not drift after the first subtraction.",
        ),
        library_patterns=(
            "Prefer face_ref over broad plane aliases when editing an existing solid.",
            "Keep one local edit window coherent: attach sketch, add profile, then extrude or cut.",
            "When a face sketch contains a repeated circle array, encode the circle centers explicitly in add_circle.centers so the full sketch window fits in one round.",
            "For explicit blind/through holes on an attached face, keep the centers on the local face frame and remove material with hole instead of a generic cut_extrude when possible.",
        ),
    ),
    ExposureBundleDefinition(
        bundle_id="edge_ref_features",
        title="Edge-Ref Features",
        summary="Use queried edge refs for fillet and chamfer when exact edge selection matters.",
        action_types=(
            CADActionType.FILLET,
            CADActionType.CHAMFER,
        ),
        query_tools=("query_topology", "render_view", "validate_requirement"),
        topology_hints=("top_outer_edges", "bottom_outer_edges", "outer_edges"),
        decision_rules=(
            "Prefer edge_refs over edges_selector/edge_scope when the requirement targets a specific subset of edges.",
            "If refs fail to resolve, request query_topology again instead of switching to a broad global selector.",
        ),
        library_patterns=(
            "Apply one radius/distance uniformly across the selected explicit edge set.",
            "Use selector fallback only for coarse cases like all outer edges or all top edges.",
        ),
    ),
    ExposureBundleDefinition(
        bundle_id="repair_state",
        title="Repair State",
        summary="Use these actions only when the current action history or session state must be corrected.",
        action_types=(
            CADActionType.ROLLBACK,
            CADActionType.SNAPSHOT,
            CADActionType.MODIFY_ACTION,
            CADActionType.CLEAR_SESSION,
        ),
        query_tools=(
            "query_snapshot",
            "query_sketch",
            "query_geometry",
            "query_topology",
            "validate_requirement",
        ),
        topology_hints=(),
        decision_rules=(
            "Prefer direct forward edits over rollback unless the previous step is clearly invalid.",
            "If relation_eval reports a blocking drift immediately after the latest topology-changing step, repair or rollback instead of declaring completion or requesting validate-only inspection.",
            "Use clear_session only for explicit reset intent or unrecoverable session drift.",
        ),
        library_patterns=(),
    ),
    ExposureBundleDefinition(
        bundle_id="inspection_tools",
        title="Inspection Tools",
        summary="Query and render only the evidence needed for the next decision.",
        action_types=(),
        query_tools=(
            "query_snapshot",
            "query_sketch",
            "query_geometry",
            "query_topology",
            "query_feature_probes",
            "render_view",
            "execute_build123d_probe",
            "validate_requirement",
        ),
        topology_hints=(),
        decision_rules=(
            "Search first with compact windows, then paginate, then render locally, then act.",
            "Do not request every inspection tool every round.",
        ),
        library_patterns=(),
    ),
)

EXPOSURE_BUNDLES_BY_ID = {bundle.bundle_id: bundle for bundle in EXPOSURE_BUNDLES}


def get_action_definition(action_type: CADActionType | str) -> ActionDefinition | None:
    if isinstance(action_type, CADActionType):
        return ACTION_DEFINITIONS_BY_ENUM.get(action_type)
    return ACTION_DEFINITIONS_BY_NAME.get(str(action_type).strip())


def get_supported_action_types() -> list[str]:
    return [definition.action_type.value for definition in ACTION_DEFINITIONS]


def get_tool_definition(name: str) -> ToolDefinition | None:
    return TOOL_DEFINITIONS_BY_NAME.get(name)


def parse_topology_ref(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, str):
        return None
    match = TOPOLOGY_REF_PATTERN.fullmatch(value.strip())
    if match is None:
        return None
    return {
        "kind": match.group("kind"),
        "step": int(match.group("step")),
        "entity_id": match.group("entity_id"),
        "ref": value.strip(),
    }


def normalize_action_params(
    action_type: CADActionType | str,
    params: dict[str, CADParamValue] | None,
) -> dict[str, CADParamValue]:
    normalized = dict(params or {})
    definition = get_action_definition(action_type)
    if definition is None:
        return normalized

    for field in definition.params:
        if (
            definition.action_type
            in {CADActionType.ADD_CIRCLE, CADActionType.SPHERE_RECESS}
            and field.name == "radius"
            and "radius" not in normalized
            and "diameter" in normalized
        ):
            continue
        if field.name in normalized:
            continue
        for alias in field.aliases:
            if alias in normalized:
                normalized[field.name] = normalized[alias]
                break

    if "face_ref" in normalized and isinstance(normalized["face_ref"], str):
        face_ref = normalized["face_ref"].strip()
        if face_ref:
            normalized["face_ref"] = face_ref
        else:
            normalized.pop("face_ref", None)

    if "edge_refs" in normalized:
        normalized["edge_refs"] = _normalize_ref_list(normalized.get("edge_refs"))

    if "edge_ref" in normalized and "edge_refs" not in normalized:
        normalized["edge_refs"] = _normalize_ref_list(normalized.get("edge_ref"))

    if "position" not in normalized:
        position = _normalize_xyz_position_alias(normalized)
        if position is not None:
            normalized["position"] = position

    if definition.action_type == CADActionType.CREATE_SKETCH:
        normalized = _normalize_create_sketch_params(normalized)

    if definition.action_type == CADActionType.ADD_RECTANGLE:
        normalized = _normalize_add_rectangle_params(normalized)

    if definition.action_type == CADActionType.ADD_POLYGON:
        normalized = _normalize_add_polygon_params(normalized)

    if definition.action_type == CADActionType.ADD_PATH:
        normalized = _normalize_add_path_params(normalized)

    return normalized


def _normalize_create_sketch_params(
    params: dict[str, CADParamValue],
) -> dict[str, CADParamValue]:
    normalized = dict(params)
    plane_raw = normalized.get("plane", "XY")
    plane_token = str(plane_raw).strip().upper() if isinstance(plane_raw, str) else "XY"
    plane = {
        "TOP": "XY",
        "BOTTOM": "XY",
        "FRONT": "XZ",
        "BACK": "XZ",
        "RIGHT": "YZ",
        "LEFT": "YZ",
    }.get(plane_token, plane_token if plane_token in {"XY", "XZ", "YZ"} else "XY")
    if "offset" not in normalized:
        offset_aliases = ["plane_offset"]
        offset_aliases.extend(
            {
                "XY": ["offset_z", "z_offset"],
                "XZ": ["offset_y", "y_offset"],
                "YZ": ["offset_x", "x_offset"],
            }.get(plane, [])
        )
        for alias in offset_aliases:
            if isinstance(normalized.get(alias), (int, float)):
                normalized["offset"] = float(normalized[alias])
                break
    for alias in (
        "plane_offset",
        "offset_x",
        "x_offset",
        "offset_y",
        "y_offset",
        "offset_z",
        "z_offset",
    ):
        normalized.pop(alias, None)
    path_ref_raw = normalized.get("path_ref")
    has_path_ref = isinstance(path_ref_raw, str) and path_ref_raw.strip() != ""

    frame_mode_raw = normalized.get("frame_mode")
    if isinstance(frame_mode_raw, str):
        frame_mode = frame_mode_raw.strip().lower()
        frame_alias_map = {
            "normal": "normal_to_path_tangent",
            "normal_to_path_tangent": "normal_to_path_tangent",
            "frenet": "normal_to_path_tangent",
        }
        if frame_mode in frame_alias_map:
            normalized["frame_mode"] = frame_alias_map[frame_mode]
    elif isinstance(frame_mode_raw, bool) and frame_mode_raw:
        normalized["frame_mode"] = "normal_to_path_tangent"
    elif has_path_ref:
        normalized["frame_mode"] = "normal_to_path_tangent"

    if has_path_ref and str(normalized.get("frame_mode", "")).strip():
        normalized["frame_mode"] = "normal_to_path_tangent"

    endpoint_raw = normalized.get("path_endpoint")
    if isinstance(endpoint_raw, str):
        endpoint = endpoint_raw.strip().lower()
        endpoint_alias_map = {
            "0": "start",
            "start": "start",
            "first": "start",
            "begin": "start",
            "1": "end",
            "end": "end",
            "last": "end",
            "finish": "end",
        }
        if endpoint in endpoint_alias_map:
            normalized["path_endpoint"] = endpoint_alias_map[endpoint]
    elif isinstance(endpoint_raw, (int, float)) and not isinstance(endpoint_raw, bool):
        normalized["path_endpoint"] = "start" if int(endpoint_raw) == 0 else "end"

    return normalized


def _normalize_add_rectangle_params(
    params: dict[str, CADParamValue],
) -> dict[str, CADParamValue]:
    normalized = dict(params)
    if "position" not in normalized and "center" not in normalized:
        for field_name, anchor_name in (
            ("corner_xy", "lower_left"),
            ("corner", "lower_left"),
            ("lower_left", "lower_left"),
            ("bottom_left", "lower_left"),
            ("lower_right", "lower_right"),
            ("bottom_right", "lower_right"),
            ("top_left", "top_left"),
            ("upper_left", "top_left"),
            ("top_right", "top_right"),
            ("upper_right", "top_right"),
        ):
            point = _normalize_single_point_2d(normalized.get(field_name))
            if point is None:
                continue
            normalized["position"] = point
            anchor_raw_existing = normalized.get("anchor")
            if not isinstance(anchor_raw_existing, str) or str(anchor_raw_existing).strip().lower() in {
                "",
                "center",
                "centre",
            }:
                normalized["anchor"] = anchor_name
            break
    anchor_raw = normalized.get("anchor", "center")
    anchor = (
        str(anchor_raw).strip().lower().replace("-", "_").replace(" ", "_")
        if isinstance(anchor_raw, str)
        else "center"
    )
    anchor_aliases = {
        "center": "center",
        "centre": "center",
        "lower_left": "lower_left",
        "bottom_left": "lower_left",
        "lower_right": "lower_right",
        "bottom_right": "lower_right",
        "upper_left": "top_left",
        "top_left": "top_left",
        "upper_right": "top_right",
        "top_right": "top_right",
    }
    normalized_anchor = anchor_aliases.get(anchor, "center")
    normalized["anchor"] = normalized_anchor

    width = float(normalized["width"]) if isinstance(normalized.get("width"), (int, float)) else 0.0
    height = float(normalized["height"]) if isinstance(normalized.get("height"), (int, float)) else 0.0
    position = _normalize_single_point_2d(
        normalized.get("position", normalized.get("center"))
    )
    if normalized_anchor == "center" or position is None or width <= 0.0 or height <= 0.0:
        return normalized

    offset_x = 0.0
    offset_y = 0.0
    if normalized_anchor == "lower_left":
        offset_x = width / 2.0
        offset_y = height / 2.0
    elif normalized_anchor == "lower_right":
        offset_x = -width / 2.0
        offset_y = height / 2.0
    elif normalized_anchor == "top_left":
        offset_x = width / 2.0
        offset_y = -height / 2.0
    elif normalized_anchor == "top_right":
        offset_x = -width / 2.0
        offset_y = -height / 2.0

    normalized["position"] = [
        float(position[0] + offset_x),
        float(position[1] + offset_y),
    ]
    normalized["anchor"] = "center"
    normalized["centered"] = True
    return normalized


def _normalize_add_polygon_params(
    params: dict[str, CADParamValue],
) -> dict[str, CADParamValue]:
    normalized = dict(params)
    apothem_raw = normalized.get("apothem", normalized.get("distance_to_side"))
    apothem_value = (
        float(apothem_raw) if isinstance(apothem_raw, (int, float)) else None
    )
    if apothem_value is not None and apothem_value > 0.0:
        normalized.setdefault("radius_outer", apothem_value)
        normalized.setdefault("size_mode", "apothem")
    size_raw = normalized.get("size")
    size_value = float(size_raw) if isinstance(size_raw, (int, float)) else None
    if size_value is None or size_value <= 0.0:
        return normalized

    size_mode_raw = normalized.get(
        "size_mode",
        normalized.get("radius_mode", normalized.get("polygon_size_mode")),
    )
    size_mode = (
        str(size_mode_raw).strip().lower().replace("-", "_").replace(" ", "_")
        if isinstance(size_mode_raw, str) and str(size_mode_raw).strip()
        else ""
    )
    if size_mode in {"side_length", "edge_length", "side"}:
        normalized.setdefault("side_length", size_value)
    else:
        normalized.setdefault("radius_outer", size_value)
    return normalized


def _normalize_add_path_params(
    params: dict[str, CADParamValue],
) -> dict[str, CADParamValue]:
    normalized = dict(params)
    plane = _normalize_sketch_plane_name(normalized.get("plane"))
    segments_raw = normalized.get("segments")
    segments = _normalize_path_segments(segments_raw, plane=plane)
    if segments:
        normalized["segments"] = segments
    start_point = _normalize_single_point_2d(normalized.get("start"), plane=plane)
    if start_point is not None:
        normalized["start"] = start_point
    elif segments:
        extracted_start = _extract_path_start_from_segments(segments_raw, plane=plane)
        if extracted_start is not None:
            normalized["start"] = extracted_start
    points_raw = normalized.get("points", normalized.get("vertices"))
    points = _normalize_point_list_2d(points_raw, plane=plane)
    if points and "start" not in normalized:
        normalized["start"] = list(points[0])
    if points and "segments" not in normalized:
        normalized["segments"] = _segments_from_path_points(points)
    return normalized


def _normalize_sketch_plane_name(value: Any) -> str:
    plane_token = str(value).strip().upper() if isinstance(value, str) else "XY"
    return {
        "TOP": "XY",
        "BOTTOM": "XY",
        "FRONT": "XZ",
        "BACK": "XZ",
        "RIGHT": "YZ",
        "LEFT": "YZ",
    }.get(plane_token, plane_token if plane_token in {"XY", "XZ", "YZ"} else "XY")


def _normalize_point_list_2d(
    value: Any,
    *,
    plane: str = "XY",
) -> list[list[float]]:
    if not isinstance(value, list):
        return []
    normalized: list[list[float]] = []
    for item in value:
        point = _normalize_single_point_2d(item, plane=plane)
        if point is not None:
            normalized.append(point)
    return normalized


def _segments_from_path_points(points: list[list[float]]) -> list[dict[str, Any]]:
    if len(points) < 2:
        return []

    segments: list[dict[str, Any]] = []
    index = 1
    while index < len(points):
        if index + 2 < len(points):
            arc_segment = _infer_quarter_tangent_arc_segment(
                prev_point=points[index - 1],
                arc_start=points[index],
                arc_end=points[index + 1],
                next_point=points[index + 2],
            )
            if arc_segment is not None:
                segments.append({"type": "line", "to": list(points[index])})
                segments.append(arc_segment)
                index += 2
                continue
        segments.append({"type": "line", "to": list(points[index])})
        index += 1
    return segments


def _normalize_path_segments(
    value: Any,
    *,
    plane: str = "XY",
) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    normalized_segments: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        segment_type_raw = item.get("type", "line")
        segment_type = (
            str(segment_type_raw).strip().lower()
            if isinstance(segment_type_raw, str)
            else "line"
        )
        if segment_type in {"arc", "add_arc"}:
            normalized_type = "arc"
        elif segment_type == "add_tangent_arc":
            normalized_type = "tangent_arc"
        elif segment_type in {"three_point_arc", "add_three_point_arc"}:
            normalized_type = "three_point_arc"
        elif segment_type in {"tangent_line", "add_line"}:
            normalized_type = "line"
        else:
            normalized_type = segment_type
        if normalized_type not in {"line", "tangent_arc", "arc", "three_point_arc"}:
            continue
        normalized_item: dict[str, Any] = {"type": normalized_type}
        to_point = _normalize_single_point_2d(
            item.get("to", item.get("end", item.get("end_point"))),
            plane=plane,
        )
        if to_point is not None:
            normalized_item["to"] = to_point
        mid_point = _normalize_single_point_2d(
            item.get("mid", item.get("mid_point")),
            plane=plane,
        )
        if mid_point is not None:
            normalized_item["mid"] = mid_point
        center_point = _normalize_single_point_2d(
            item.get("center", item.get("origin")),
            plane=plane,
        )
        if center_point is not None:
            normalized_item["center"] = center_point
        for numeric_key in (
            "length",
            "radius",
            "angle_degrees",
            "arc_degrees",
            "angle",
            "dx",
            "dy",
            "start_angle",
            "end_angle",
        ):
            value_raw = item.get(numeric_key)
            if isinstance(value_raw, (int, float)):
                canonical_key = (
                    "angle_degrees" if numeric_key == "arc_degrees" else numeric_key
                )
                normalized_item[canonical_key] = float(value_raw)
        direction_raw = item.get("direction")
        direction_token = (
            direction_raw.strip().lower().replace(" ", "_")
            if isinstance(direction_raw, str)
            else None
        )
        if (
            normalized_type in {"arc", "tangent_arc"}
            and direction_token
            in {"cw", "clockwise", "ccw", "counterclockwise", "counter_clockwise"}
        ):
            normalized_item["direction"] = direction_token
            normalized_item["turn"] = (
                "right"
                if direction_token in {"cw", "clockwise"}
                else "left"
            )
        else:
            direction = _normalize_path_direction(direction_raw, plane=plane)
            if direction is not None:
                normalized_item["direction"] = direction
        turn_raw = item.get("turn", item.get("turn_direction"))
        if isinstance(turn_raw, str) and turn_raw.strip():
            normalized_item["turn"] = turn_raw.strip().lower()
        if isinstance(item.get("clockwise"), bool):
            normalized_item["clockwise"] = bool(item["clockwise"])
        if (
            normalized_type in {"arc", "tangent_arc"}
            and "to" not in normalized_item
            and center_point is not None
            and isinstance(normalized_item.get("radius"), (int, float))
            and isinstance(normalized_item.get("end_angle"), (int, float))
        ):
            normalized_item["to"] = _point_from_center_radius_angle_2d(
                center=center_point,
                radius=float(normalized_item["radius"]),
                angle_degrees=float(normalized_item["end_angle"]),
            )
        if (
            normalized_type in {"arc", "tangent_arc"}
            and "angle_degrees" not in normalized_item
            and isinstance(normalized_item.get("start_angle"), (int, float))
            and isinstance(normalized_item.get("end_angle"), (int, float))
        ):
            normalized_item["angle_degrees"] = abs(
                float(normalized_item["end_angle"]) - float(normalized_item["start_angle"])
            )
        normalized_segments.append(normalized_item)
    return normalized_segments


def _extract_path_start_from_segments(
    value: Any,
    *,
    plane: str = "XY",
) -> list[float] | None:
    if not isinstance(value, list) or not value:
        return None
    first = value[0]
    if not isinstance(first, dict):
        return None
    explicit_start = _normalize_single_point_2d(
        first.get("start", first.get("start_point")),
        plane=plane,
    )
    if explicit_start is not None:
        return explicit_start
    center_point = _normalize_single_point_2d(
        first.get("center", first.get("origin")),
        plane=plane,
    )
    radius_raw = first.get("radius")
    start_angle_raw = first.get("start_angle")
    if (
        center_point is not None
        and isinstance(radius_raw, (int, float))
        and isinstance(start_angle_raw, (int, float))
    ):
        return _point_from_center_radius_angle_2d(
            center=center_point,
            radius=float(radius_raw),
            angle_degrees=float(start_angle_raw),
        )
    return None


def _normalize_single_point_2d(
    value: Any,
    *,
    plane: str = "XY",
) -> list[float] | None:
    if not isinstance(value, (list, tuple)) or len(value) < 2:
        return None
    if len(value) >= 3 and all(isinstance(item, (int, float)) for item in value[:3]):
        x_value = float(value[0])
        y_value = float(value[1])
        z_value = float(value[2])
        normalized_plane = _normalize_sketch_plane_name(plane)
        if normalized_plane == "XZ":
            return [x_value, z_value]
        if normalized_plane == "YZ":
            return [y_value, z_value]
        return [x_value, y_value]
    if not isinstance(value[0], (int, float)) or not isinstance(value[1], (int, float)):
        return None
    return [float(value[0]), float(value[1])]


def _normalize_path_direction(
    value: Any,
    *,
    plane: str = "XY",
) -> list[float] | None:
    if isinstance(value, (list, tuple)):
        direction = _normalize_single_point_2d(value, plane=plane)
        if direction is None:
            return None
        magnitude = math.hypot(direction[0], direction[1])
        if magnitude <= 1e-6:
            return None
        return [float(direction[0] / magnitude), float(direction[1] / magnitude)]
    if not isinstance(value, str) or not value.strip():
        return None
    token = value.strip().lower().replace(" ", "_")
    local_aliases = {
        "x": [1.0, 0.0],
        "+x": [1.0, 0.0],
        "x+": [1.0, 0.0],
        "right": [1.0, 0.0],
        "horizontal": [1.0, 0.0],
        "-x": [-1.0, 0.0],
        "x-": [-1.0, 0.0],
        "left": [-1.0, 0.0],
        "y": [0.0, 1.0],
        "+y": [0.0, 1.0],
        "y+": [0.0, 1.0],
        "up": [0.0, 1.0],
        "vertical": [0.0, 1.0],
        "-y": [0.0, -1.0],
        "y-": [0.0, -1.0],
        "down": [0.0, -1.0],
    }
    if token in local_aliases:
        return list(local_aliases[token])
    axis_aliases = {
        "x": "x",
        "+x": "x",
        "x+": "x",
        "-x": "-x",
        "x-": "-x",
        "y": "y",
        "+y": "y",
        "y+": "y",
        "-y": "-y",
        "y-": "-y",
        "z": "z",
        "+z": "z",
        "z+": "z",
        "-z": "-z",
        "z-": "-z",
    }
    axis_token = axis_aliases.get(token)
    if axis_token is None:
        return None
    normalized_plane = _normalize_sketch_plane_name(plane)
    if normalized_plane == "XZ":
        mapping = {
            "x": [1.0, 0.0],
            "-x": [-1.0, 0.0],
            "z": [0.0, 1.0],
            "-z": [0.0, -1.0],
        }
        return list(mapping.get(axis_token, [])) or None
    if normalized_plane == "YZ":
        mapping = {
            "y": [1.0, 0.0],
            "-y": [-1.0, 0.0],
            "z": [0.0, 1.0],
            "-z": [0.0, -1.0],
        }
        return list(mapping.get(axis_token, [])) or None
    mapping = {
        "x": [1.0, 0.0],
        "-x": [-1.0, 0.0],
        "y": [0.0, 1.0],
        "-y": [0.0, -1.0],
    }
    return list(mapping.get(axis_token, [])) or None


def _point_from_center_radius_angle_2d(
    center: list[float],
    radius: float,
    angle_degrees: float,
) -> list[float]:
    radians = math.radians(float(angle_degrees))
    return [
        float(center[0] + (float(radius) * math.cos(radians))),
        float(center[1] + (float(radius) * math.sin(radians))),
    ]


def _infer_quarter_tangent_arc_segment(
    prev_point: list[float],
    arc_start: list[float],
    arc_end: list[float],
    next_point: list[float],
) -> dict[str, Any] | None:
    incoming = _axis_unit_vector(
        arc_start[0] - prev_point[0],
        arc_start[1] - prev_point[1],
    )
    outgoing = _axis_unit_vector(
        next_point[0] - arc_end[0],
        next_point[1] - arc_end[1],
    )
    if incoming is None or outgoing is None:
        return None
    if abs((incoming[0] * outgoing[0]) + (incoming[1] * outgoing[1])) > 1e-6:
        return None

    delta_x = arc_end[0] - arc_start[0]
    delta_y = arc_end[1] - arc_start[1]
    candidate_radii: list[float] = []
    sum_x = incoming[0] + outgoing[0]
    sum_y = incoming[1] + outgoing[1]
    if abs(sum_x) > 1e-6:
        candidate_radii.append(delta_x / sum_x)
    if abs(sum_y) > 1e-6:
        candidate_radii.append(delta_y / sum_y)
    if not candidate_radii:
        return None

    radius = candidate_radii[0]
    if any(abs(item - radius) > 1e-4 for item in candidate_radii[1:]):
        return None
    radius = abs(float(radius))
    if radius <= 1e-6:
        return None

    turn = "left" if ((incoming[0] * outgoing[1]) - (incoming[1] * outgoing[0])) > 0 else "right"
    return {
        "type": "tangent_arc",
        "to": list(arc_end),
        "radius": radius,
        "angle_degrees": 90.0,
        "turn": turn,
    }


def _axis_unit_vector(dx: float, dy: float) -> tuple[float, float] | None:
    abs_dx = abs(dx)
    abs_dy = abs(dy)
    if abs_dx <= 1e-6 and abs_dy <= 1e-6:
        return None
    if abs_dx > 1e-6 and abs_dy > 1e-6:
        return None
    if abs_dx > 1e-6:
        return (1.0 if dx > 0.0 else -1.0, 0.0)
    return (0.0, 1.0 if dy > 0.0 else -1.0)


def _normalize_ref_list(value: Any) -> list[str]:
    raw_items: list[Any]
    if isinstance(value, list):
        raw_items = value
    elif isinstance(value, str):
        raw_items = [value]
    else:
        return []

    normalized: list[str] = []
    seen: set[str] = set()
    for item in raw_items:
        if not isinstance(item, str):
            continue
        ref = item.strip()
        if not ref or ref in seen:
            continue
        normalized.append(ref)
        seen.add(ref)
    return normalized


def _normalize_xyz_position_alias(
    params: dict[str, CADParamValue],
) -> list[float] | None:
    x_value = params.get("x", params.get("center_x", params.get("position_x")))
    y_value = params.get("y", params.get("center_y", params.get("position_y")))
    z_value = params.get("z", params.get("center_z", params.get("position_z")))
    if not isinstance(x_value, (int, float)) or not isinstance(y_value, (int, float)):
        return None
    position = [float(x_value), float(y_value)]
    if isinstance(z_value, (int, float)):
        position.append(float(z_value))
    return position


def select_exposure_bundle_ids(
    requirements: dict[str, Any] | None,
    action_history: list[dict[str, Any]] | None,
    completeness: dict[str, Any] | None,
    query_geometry: dict[str, Any] | None,
    query_topology: dict[str, Any] | None,
    requirement_validation: dict[str, Any] | None = None,
    latest_unresolved_blockers: list[str] | None = None,
    previous_error: str | None = None,
) -> list[str]:
    semantics = analyze_requirement_semantics(requirements)
    requirement_text = semantics.normalized_text
    sweep_family_requested = requirement_requests_path_sweep(
        requirements,
        requirement_text=requirement_text,
        semantics=semantics,
    )
    loft_family_requested = requirement_requests_loft(
        requirements,
        requirement_text=requirement_text,
    )
    history = action_history or []
    completeness = completeness or {}
    geometry = query_geometry or {}
    topology = query_topology or {}
    validation = requirement_validation or {}
    error_text = (previous_error or "").strip().lower()
    blockers = {
        str(item).strip().lower()
        for item in validation.get("blockers", [])
        if isinstance(item, str)
    }
    unresolved_blockers = {
        str(item).strip().lower()
        for item in (latest_unresolved_blockers or [])
        if isinstance(item, str)
    }

    selected: list[str] = ["inspection_tools"]

    has_solid = False
    geometry_payload = geometry.get("geometry")
    if isinstance(geometry_payload, dict):
        try:
            has_solid = int(geometry_payload.get("solids", 0) or 0) > 0
        except Exception:
            has_solid = False
    if not has_solid:
        if semantics.mentions_nested_profile_cutout or semantics.mentions_profile_region_frame:
            selected.extend(["inner_void_cutout", "bootstrap_sketch", "additive_extrusion"])
        elif semantics.mentions_multi_plane_additive_union:
            selected.extend(["orthogonal_additive_union", "bootstrap_sketch", "additive_extrusion"])
        elif sweep_family_requested or any(
            isinstance(item, dict)
            and str(item.get("action_type", "")).strip().lower() == "sweep"
            for item in history
        ):
            selected.extend(["path_sweep", "bootstrap_sketch", "additive_extrusion"])
        else:
            selected.extend(["bootstrap_sketch", "additive_extrusion"])

    missing_features = {
        str(item).strip().lower()
        for item in completeness.get("missing_features", [])
        if isinstance(item, str)
    }
    if semantics.mentions_subtractive_edit:
        selected.append("subtractive_edit")
    if semantics.mentions_spherical_recess:
        selected.extend(["subtractive_edit", "spherical_face_edit", "face_attached_sketch"])
    if semantics.mentions_pattern:
        selected.append("feature_patterns")
    if missing_features.intersection({"cut", "hole", "cut_extrude"}):
        selected.append("subtractive_edit")
    if semantics.mentions_nested_profile_cutout:
        selected.append("inner_void_cutout")
        selected.append("face_attached_sketch")
    if semantics.prefers_explicit_inner_void_cut:
        selected.append("face_attached_sketch")
        selected.append("subtractive_edit")
    if semantics.mentions_profile_region_frame:
        selected.append("inner_void_cutout")
    if semantics.mentions_multi_plane_additive_union:
        selected.append("orthogonal_additive_union")
        selected.append("additive_extrusion")
    if sweep_family_requested or any(
        isinstance(item, dict)
        and str(item.get("action_type", "")).strip().lower() == "sweep"
        for item in history
    ):
        selected.extend(["path_sweep", "additive_extrusion"])
    if loft_family_requested:
        selected.extend(["loft_profile_stack", "additive_extrusion"])
    if semantics.mentions_revolved_groove_cut:
        selected.append("revolved_groove_cut")
    if semantics.mentions_fillet or semantics.mentions_chamfer:
        selected.append("edge_ref_features")
    if missing_features.intersection({"fillet", "chamfer"}):
        selected.append("edge_ref_features")
    if semantics.mentions_face_edit or "attach" in requirement_text:
        selected.append("face_attached_sketch")
    if isinstance(topology.get("topology_index"), dict):
        faces = topology["topology_index"].get("faces")
        edges = topology["topology_index"].get("edges")
        if isinstance(faces, list) and faces:
            selected.append("face_attached_sketch")
        if isinstance(edges, list) and edges:
            selected.append("edge_ref_features")
    if "invalid_reference" in error_text or "stale" in error_text:
        selected.extend(["face_attached_sketch", "edge_ref_features"])
    if any(
        "annular_groove" in blocker or "revolved_groove" in blocker
        for blocker in blockers
    ):
        selected.extend(["subtractive_edit", "revolved_groove_cut"])
    if any(
        "target_face" in blocker or "face_edit" in blocker for blocker in blockers
    ):
        selected.append("face_attached_sketch")
    if any(
        "edge_target" in blocker
        or "feature_fillet" in blocker
        or "feature_chamfer" in blocker
        for blocker in blockers
    ):
        selected.append("edge_ref_features")
    if any("notch" in blocker or "profile_cut" in blocker for blocker in blockers):
        selected.extend(["subtractive_edit", "face_attached_sketch"])
    if any("feature_pattern" in blocker for blocker in blockers):
        selected.append("feature_patterns")
    if any("solid_positive_volume" in blocker for blocker in blockers):
        selected.extend(["bootstrap_sketch", "additive_extrusion"])
    if any("multi_plane_additive_union" in blocker for blocker in blockers):
        selected.extend(["orthogonal_additive_union", "additive_extrusion"])
    if any(blocker.startswith("eval:") for blocker in unresolved_blockers):
        selected.append("repair_state")
    if any(
        action.get("action_type") in {"rollback", "modify_action"}
        for action in history[-2:]
        if isinstance(action, dict)
    ):
        selected.append("repair_state")

    deduped: list[str] = []
    for bundle_id in selected:
        if bundle_id in EXPOSURE_BUNDLES_BY_ID and bundle_id not in deduped:
            deduped.append(bundle_id)
    return deduped


def render_capability_cards(bundle_ids: list[str]) -> list[str]:
    cards: list[str] = []
    for bundle_id in bundle_ids:
        bundle = EXPOSURE_BUNDLES_BY_ID.get(bundle_id)
        if bundle is None or bundle_id == "inspection_tools":
            continue
        actions = [
            definition.action_type.value
            for definition in ACTION_DEFINITIONS
            if definition.action_type in bundle.action_types
        ]
        cards.extend(
            [
                f"### {bundle.title}",
                f"- use_when: {bundle.summary}",
                f"- actions: {', '.join(actions)}",
                f"- rules: {'; '.join(bundle.decision_rules)}",
            ]
        )
        if bundle.library_patterns:
            cards.append(f"- patterns: {'; '.join(bundle.library_patterns)}")
        cards.append("")
    return cards


def render_inspection_cards(bundle_ids: list[str]) -> list[str]:
    if "inspection_tools" not in bundle_ids:
        return []
    lines = [
        "## Inspection Cards",
        "Ask only for the evidence needed for the next decision.",
    ]
    for tool_name in (
        "query_snapshot",
        "query_sketch",
        "query_geometry",
        "query_topology",
        "render_view",
        "validate_requirement",
    ):
        definition = TOOL_DEFINITIONS_BY_NAME.get(tool_name)
        if definition is None:
            continue
        schema = " ".join(line.strip() for line in definition.prompt_schema_lines)
        lines.append(f"- {tool_name}: {schema}")
    lines.append("")
    return lines


def collect_bundle_topology_hints(bundle_ids: list[str]) -> list[str]:
    hints: list[str] = []
    seen: set[str] = set()
    for bundle_id in bundle_ids:
        bundle = EXPOSURE_BUNDLES_BY_ID.get(bundle_id)
        if bundle is None:
            continue
        for hint in bundle.topology_hints:
            normalized = hint.strip().lower()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            hints.append(normalized)
    return hints


def collect_requirement_topology_hints(
    requirements: dict[str, Any] | None,
) -> list[str]:
    semantics = analyze_requirement_semantics(requirements)
    text = semantics.normalized_text
    sweep_family_requested = requirement_requests_path_sweep(
        requirements,
        requirement_text=text,
        semantics=semantics,
    )
    if not text:
        return []

    hints: list[str] = []
    seen: set[str] = set()

    def _add_hint(raw_hint: str) -> None:
        hint = raw_hint.strip().lower().replace("-", "_").replace(" ", "_")
        if not hint or hint in seen:
            return
        seen.add(hint)
        hints.append(hint)

    face_target_map = {
        "top": "top_faces",
        "bottom": "bottom_faces",
        "front": "front_faces",
        "back": "back_faces",
        "left": "left_faces",
        "right": "right_faces",
    }
    for face_target in semantics.face_targets:
        if face_target == "side":
            for hint in ("front_faces", "back_faces", "left_faces", "right_faces"):
                _add_hint(hint)
            continue
        if face_target == "existing":
            continue
        mapped = face_target_map.get(face_target)
        if mapped:
            _add_hint(mapped)

    has_top = "top" in text or "upper" in text
    has_bottom = "bottom" in text or "lower" in text
    has_outer = "outer" in text or "outside" in text or "external" in text
    has_inner = (
        "inner" in text
        or "inside" in text
        or "internal" in text
        or "hollow" in text
        or "annular" in text
        or "wall thickness" in text
        or "pipe" in text
        or "tube" in text
    )
    has_edge = "edge" in text or "edges" in text
    aligned_with_edge = "aligned with the edge" in text or (
        "align" in text and "edge" in text
    )

    if "front" in semantics.face_targets and has_top and has_edge:
        _add_hint("front_top_edges")
    if "back" in semantics.face_targets and has_top and has_edge:
        _add_hint("back_top_edges")
    if "left" in semantics.face_targets and has_top and has_edge:
        _add_hint("left_top_edges")
    if "right" in semantics.face_targets and has_top and has_edge:
        _add_hint("right_top_edges")
    if "front" in semantics.face_targets and has_bottom and has_edge:
        _add_hint("front_bottom_edges")
    if "back" in semantics.face_targets and has_bottom and has_edge:
        _add_hint("back_bottom_edges")
    if "left" in semantics.face_targets and has_bottom and has_edge:
        _add_hint("left_bottom_edges")
    if "right" in semantics.face_targets and has_bottom and has_edge:
        _add_hint("right_bottom_edges")

    if has_top:
        _add_hint("top_faces")
        _add_hint("top_edges")
        _add_hint("upward_planar_faces")
    if has_bottom:
        _add_hint("bottom_faces")
        _add_hint("bottom_edges")
        _add_hint("downward_planar_faces")
    if has_outer or aligned_with_edge:
        _add_hint("outer_faces")
        _add_hint("outer_edges")
    if has_inner:
        _add_hint("inner_edges")
    if semantics.mentions_nested_profile_cutout:
        _add_hint("top_faces")
        _add_hint("top_edges")
    if sweep_family_requested and has_inner:
        _add_hint("outer_edges")
        _add_hint("inner_edges")
    if semantics.mentions_spherical_recess:
        _add_hint("top_faces")
        _add_hint("bottom_faces")
        _add_hint("upward_planar_faces")
        _add_hint("downward_planar_faces")
    if semantics.mentions_hole and semantics.mentions_pattern:
        if has_top:
            _add_hint("top_inner_planar_faces")
        if has_bottom:
            _add_hint("bottom_inner_planar_faces")
    if semantics.mentions_revolved_groove_cut:
        _add_hint("primary_outer_faces")
        _add_hint("primary_axis_outer_edges")
        _add_hint("outer_faces")
        _add_hint("outer_edges")
    if has_top and has_outer and has_edge:
        _add_hint("top_outer_edges")
    if has_bottom and has_outer and has_edge:
        _add_hint("bottom_outer_edges")
    if any(edge_target == "top_outer_edges" for edge_target in semantics.edge_targets):
        _add_hint("top_outer_edges")
    if any(
        edge_target == "bottom_outer_edges" for edge_target in semantics.edge_targets
    ):
        _add_hint("bottom_outer_edges")
    if any(edge_target == "outer_edges" for edge_target in semantics.edge_targets):
        _add_hint("outer_edges")
    for axis in ("x", "y", "z"):
        if any(
            edge_target == f"{axis}_parallel_outer_edges"
            for edge_target in semantics.edge_targets
        ):
            _add_hint(f"{axis}_parallel_outer_edges")
            _add_hint("outer_edges")
        if any(
            edge_target == f"{axis}_parallel_top_outer_edges"
            for edge_target in semantics.edge_targets
        ):
            _add_hint(f"{axis}_parallel_top_outer_edges")
            _add_hint("top_outer_edges")
            _add_hint("top_edges")
            _add_hint("outer_edges")
        if any(
            edge_target == f"{axis}_parallel_bottom_outer_edges"
            for edge_target in semantics.edge_targets
        ):
            _add_hint(f"{axis}_parallel_bottom_outer_edges")
            _add_hint("bottom_outer_edges")
            _add_hint("bottom_edges")
            _add_hint("outer_edges")
    if any(edge_target == "inner_edges" for edge_target in semantics.edge_targets):
        _add_hint("inner_edges")
    if any(
        edge_target == "inner_bottom_edges" for edge_target in semantics.edge_targets
    ):
        _add_hint("inner_bottom_side_edges")
        _add_hint("inner_bottom_edges")
    if any(edge_target == "inner_top_edges" for edge_target in semantics.edge_targets):
        _add_hint("inner_top_edges")
    if any(edge_target == "top_edges" for edge_target in semantics.edge_targets):
        _add_hint("top_edges")
    if any(edge_target == "bottom_edges" for edge_target in semantics.edge_targets):
        _add_hint("bottom_edges")
    if "front" in semantics.face_targets and any(
        edge_target in {"inner_edges", "inner_top_edges", "inner_bottom_edges"}
        for edge_target in semantics.edge_targets
    ):
        _add_hint("front_inner_edges")
    if "back" in semantics.face_targets and any(
        edge_target in {"inner_edges", "inner_top_edges", "inner_bottom_edges"}
        for edge_target in semantics.edge_targets
    ):
        _add_hint("back_inner_edges")
    if "left" in semantics.face_targets and any(
        edge_target in {"inner_edges", "inner_top_edges", "inner_bottom_edges"}
        for edge_target in semantics.edge_targets
    ):
        _add_hint("left_inner_edges")
    if "right" in semantics.face_targets and any(
        edge_target in {"inner_edges", "inner_top_edges", "inner_bottom_edges"}
        for edge_target in semantics.edge_targets
    ):
        _add_hint("right_inner_edges")
    if "front" in semantics.face_targets and "inner_bottom_edges" in semantics.edge_targets:
        _add_hint("front_inner_bottom_edges")
    if "back" in semantics.face_targets and "inner_bottom_edges" in semantics.edge_targets:
        _add_hint("back_inner_bottom_edges")
    if "left" in semantics.face_targets and "inner_bottom_edges" in semantics.edge_targets:
        _add_hint("left_inner_bottom_edges")
    if "right" in semantics.face_targets and "inner_bottom_edges" in semantics.edge_targets:
        _add_hint("right_inner_bottom_edges")
    if "front" in semantics.face_targets and "inner_top_edges" in semantics.edge_targets:
        _add_hint("front_inner_top_edges")
    if "back" in semantics.face_targets and "inner_top_edges" in semantics.edge_targets:
        _add_hint("back_inner_top_edges")
    if "left" in semantics.face_targets and "inner_top_edges" in semantics.edge_targets:
        _add_hint("left_inner_top_edges")
    if "right" in semantics.face_targets and "inner_top_edges" in semantics.edge_targets:
        _add_hint("right_inner_top_edges")

    return hints


def render_topology_card(bundle_ids: list[str]) -> list[str]:
    topology_hints = collect_bundle_topology_hints(bundle_ids)
    if not topology_hints:
        return []
    return [
        "## Topology Card",
        "- Use query_topology before face/edge-specific edits.",
        "- Refs are step-local and stale after topology changes.",
        "- Prefer candidate_sets/ref_ids over broad selectors when they match the requirement.",
        "- If current-step query_topology evidence is already present in this prompt, do not request it again unless refs are stale or the needed candidate set is missing.",
        f"- Candidate-set index for this round: {', '.join(topology_hints)}.",
        "- Candidate metadata may include primary axis, axis midpoint, outer span, stable sketch frame hints, and edge-anchor points.",
        "- When present, relation_index is an objective relation-base view: entities, observed relations, and grouped patterns such as coaxial cylindrical pairs or concentric circular edges.",
        "- Use center/normal/bbox plus adjacency to separate top/bottom/outer entities.",
        "- For repeated face features, use the candidate face together with direct centers=[...] instead of broad pattern guesses when the layout is explicit.",
        "",
    ]


def render_sketch_card(bundle_ids: list[str]) -> list[str]:
    if "path_sweep" not in bundle_ids and "loft_profile_stack" not in bundle_ids:
        return []
    lines = [
        "## Sketch Card",
    ]
    if "path_sweep" in bundle_ids:
        lines.extend(
            [
                "- Use query_sketch after building a rail and before building the sweep profile.",
                "- If query_sketch reports path_disconnected, path_segment_sequence_mismatch, missing_profile, or profile_not_closed, repair that state before sweep.",
                "- Prefer relation_index for compact path/profile relations such as connected segments, tangent joints, concentric loops, and profile/path attachment, but keep the raw sketch fields available for exact repair.",
                "- When available, attach the profile sketch with create_sketch.path_ref + path_endpoint + frame_mode instead of guessing the profile plane/origin.",
            ]
        )
    if "loft_profile_stack" in bundle_ids:
        lines.extend(
            [
                "- Use query_sketch between loft profile windows and before loft so the stack order and closure stay explicit.",
                "- If query_sketch does not yet prove enough loftable profile windows, request inspection instead of guessing the loft result.",
            ]
        )
    lines.append("")
    return lines


def render_library_card(bundle_ids: list[str]) -> list[str]:
    pattern_lines: list[str] = []
    for bundle_id in bundle_ids:
        bundle = EXPOSURE_BUNDLES_BY_ID.get(bundle_id)
        if bundle is None:
            continue
        for line in bundle.library_patterns:
            if line not in pattern_lines:
                pattern_lines.append(line)
    if not pattern_lines:
        return []
    return [
        "## Library Card",
        f"- patterns: {'; '.join(pattern_lines)}",
        "",
    ]


def _requirement_suggests_nested_profile_cutout(requirement_text: str) -> bool:
    if not requirement_text:
        return False
    if any(token in requirement_text for token in ("array", "pattern")):
        return False
    if re.search(
        r"\b(one|two|three|four|five|six|[0-9]+)\s+(circles?|squares?|rectangles?|polygons?)\b",
        requirement_text,
    ):
        return False
    centered = any(
        token in requirement_text
        for token in (
            "centered",
            "concentric",
            "coaxial",
            "center axis",
            "through the center axis",
            "coincides with the center",
            "coincides with center",
        )
    )
    has_outer_shape = any(
        token in requirement_text
        for token in ("circle", "cylinder", "round", "outer round", "cylindrical")
    )
    has_inner_shape = any(
        token in requirement_text for token in ("square", "rectangle", "polygon")
    )
    shape_tokens = sum(
        1
        for token in ("circle", "cylinder", "round", "rectangle", "square", "polygon")
        if token in requirement_text
    )
    profile_language = any(
        token in requirement_text
        for token in ("extrude", "section", "profile", "passage", "through", "hollow")
    ) or ("length" in requirement_text and has_outer_shape)
    return centered and profile_language and (
        shape_tokens >= 2 or (has_outer_shape and has_inner_shape)
    )


def _requirement_suggests_profile_region_frame(requirement_text: str) -> bool:
    if not requirement_text:
        return False
    frame_language = any(
        token in requirement_text
        for token in (
            "frame-shaped",
            "frame shaped",
            "frame region",
            "region between",
            "section between",
            "area between",
            "between the two",
            "between two",
            "between them",
        )
    )
    if not frame_language:
        return False
    same_shape_tokens = ("triangles", "triangle", "squares", "square", "rectangles", "rectangle", "circles", "circle", "polygons", "polygon")
    repeated_same_shape = (
        (
            "concentric" in requirement_text
            or "inner and outer" in requirement_text
            or re.search(r"\b(two|2)\b", requirement_text) is not None
        )
        and any(token in requirement_text for token in same_shape_tokens)
    )
    profile_language = any(
        token in requirement_text
        for token in ("extrude", "section", "profile", "frame", "region")
    )
    return repeated_same_shape and profile_language


def _requirement_prefers_explicit_inner_void_cut(requirement_text: str) -> bool:
    if not requirement_text:
        return False
    if not _requirement_suggests_nested_profile_cutout(requirement_text):
        return False
    if _requirement_suggests_profile_region_frame(requirement_text):
        return False
    has_round_outer = any(
        token in requirement_text
        for token in ("circle", "cylinder", "round", "outer round", "cylindrical")
    )
    has_rectilinear_inner = any(
        token in requirement_text
        for token in ("square", "rectangle", "polygon")
    )
    if has_round_outer and has_rectilinear_inner:
        return True
    outer_inner_pairs = (
        ("circle", "square"),
        ("circle", "rectangle"),
        ("circle", "polygon"),
        ("cylinder", "square"),
        ("cylinder", "rectangle"),
    )
    return any(
        outer in requirement_text and inner in requirement_text
        for outer, inner in outer_inner_pairs
    )


def _requirement_suggests_multi_plane_additive_union(requirement_text: str) -> bool:
    if not requirement_text:
        return False
    plane_labels = set()
    plane_patterns = (
        (r"\bxy plane\b|\bxy\b", "xy"),
        (r"\bxz plane\b|\bxz\b", "xz"),
        (r"\byz plane\b|\byz\b", "yz"),
        (r"\bfront view plane\b|\bfront plane\b", "front"),
        (r"\bside view plane\b|\bright view plane\b|\bleft view plane\b", "side"),
    )
    for pattern, label in plane_patterns:
        if re.search(pattern, requirement_text):
            plane_labels.add(label)
    union_language = any(
        token in requirement_text
        for token in (
            "union",
            "merge",
            "combined",
            "combine",
            "orthogonal",
            "perpendicular",
        )
    )
    if len(plane_labels) >= 3:
        return True
    return union_language and len(plane_labels) >= 2


def _requirement_suggests_revolved_groove_cut(requirement_text: str) -> bool:
    if not requirement_text:
        return False
    normalized = str(requirement_text).lower()
    if any(
        token in normalized
        for token in (
            "countersink",
            "countersunk",
            "counterbore",
            "hole wizard",
            "flat-head screw",
            "flat head screw",
        )
    ):
        return False
    if "annular groove" in normalized:
        return True
    if "revolved cut" in normalized and "groove" in normalized:
        return True
    return "groove" in normalized and "revolve" in normalized


def _requirement_suggests_additive_face_feature(requirement_text: str) -> bool:
    if not requirement_text:
        return False
    normalized = str(requirement_text).lower()
    for phrase in (
        "extrude boss command",
        "extrude boss",
        "boss extrude command",
        "boss extrude",
        "boss-extrude command",
        "boss-extrude",
    ):
        normalized = normalized.replace(phrase, " ")
    if any(token in normalized for token in ("stud", "protrusion")):
        return True
    if any(
        token in normalized
        for token in (
            "extrude them upward",
            "extrude it upward",
            "extrude upward",
            "extrude upward from",
            "boss on the",
            "bosses on the",
        )
    ):
        return True
    return "boss" in normalized


def _requirement_suggests_sweep(requirement_text: str) -> bool:
    if not requirement_text:
        return False
    text = str(requirement_text).lower()
    if "sweep" in text:
        return True
    return "path sketch" in text and "profile sketch" in text


def normalize_requirement_text(
    requirements: dict[str, Any] | None,
    requirement_text: str | None = None,
) -> str:
    chunks: list[str] = []
    if isinstance(requirement_text, str) and requirement_text.strip():
        chunks.append(requirement_text.lower())
    if isinstance(requirements, dict):
        for key in ("description", "features", "constraints"):
            value = requirements.get(key)
            if isinstance(value, str) and value.strip():
                chunks.append(value.lower())
            elif isinstance(value, list):
                chunks.extend(str(item).lower() for item in value if str(item).strip())
    return " ".join(chunks)


def requirement_uses_operation_as_optional_method(
    requirements: dict[str, Any] | None,
    requirement_text: str | None = None,
    *,
    operation_terms: tuple[str, ...],
) -> bool:
    text = normalize_requirement_text(requirements, requirement_text)
    if not text or not operation_terms:
        return False
    if not any(term in text for term in operation_terms):
        return False
    if any(
        marker in text
        for marker in (
            "better approach",
            "alternatively",
            "alternative approach",
            "either ",
            "or directly extrude",
            "or extrude",
        )
    ):
        return True
    for term in operation_terms:
        start = 0
        while True:
            index = text.find(term, start)
            if index < 0:
                break
            window = text[max(0, index - 40) : index + len(term) + 40]
            if " or " in window:
                return True
            start = index + len(term)
    return False


def requirement_requests_path_sweep(
    requirements: dict[str, Any] | None,
    requirement_text: str | None = None,
    *,
    semantics: RequirementSemantics | None = None,
) -> bool:
    normalized_text = (
        semantics.normalized_text
        if semantics is not None
        else normalize_requirement_text(requirements, requirement_text)
    )
    current_semantics = (
        semantics
        if semantics is not None
        else analyze_requirement_semantics(requirements, normalized_text)
    )
    if not current_semantics.mentions_sweep:
        return False
    return not requirement_uses_operation_as_optional_method(
        None,
        normalized_text,
        operation_terms=("sweep", "sweeping"),
    )


def requirement_requests_loft(
    requirements: dict[str, Any] | None,
    requirement_text: str | None = None,
) -> bool:
    text = normalize_requirement_text(requirements, requirement_text)
    if not text:
        return False
    if any(
        token in text
        for token in (
            "frustum",
            "taper between",
            "transition between",
            "connect two profiles",
            "connect the two profiles",
            "apex",
        )
    ):
        return True
    if "loft" not in text:
        return False
    return not requirement_uses_operation_as_optional_method(
        None,
        text,
        operation_terms=("loft", "lofting"),
    )


def analyze_requirement_semantics(
    requirements: dict[str, Any] | None,
    requirement_text: str | None = None,
) -> RequirementSemantics:
    text = normalize_requirement_text(requirements, requirement_text)

    def _ordered_hits(patterns: tuple[tuple[str, str], ...]) -> tuple[str, ...]:
        hits: list[str] = []
        seen: set[str] = set()
        for pattern, label in patterns:
            if re.search(pattern, text) and label not in seen:
                seen.add(label)
                hits.append(label)
        return tuple(hits)

    face_targets = _ordered_hits(
        (
            (r"\bselect(?: the)? top (?:face|surface)\b", "top"),
            (r"\bselect(?: the)? bottom (?:face|surface)\b", "bottom"),
            (r"\bselect(?: the)? front face\b", "front"),
            (r"\bselect(?: the)? back face\b", "back"),
            (r"\bselect(?: the)? right face\b", "right"),
            (r"\bselect(?: the)? left face\b", "left"),
            (r"\bselect(?: the)? side face\b", "side"),
            (r"\btop (?:face|surface)\b[^.]{0,32}\breference\b", "top"),
            (r"\bbottom (?:face|surface)\b[^.]{0,32}\breference\b", "bottom"),
            (r"\bfront face\b[^.]{0,32}\breference\b", "front"),
            (r"\bback face\b[^.]{0,32}\breference\b", "back"),
            (r"\bright face\b[^.]{0,32}\breference\b", "right"),
            (r"\bleft face\b[^.]{0,32}\breference\b", "left"),
            (r"\btop (?:face|surface)\b[^.]{0,48}\bsketch plane\b", "top"),
            (r"\bbottom (?:face|surface)\b[^.]{0,48}\bsketch plane\b", "bottom"),
            (r"\bfront face\b[^.]{0,48}\bsketch plane\b", "front"),
            (r"\bback face\b[^.]{0,48}\bsketch plane\b", "back"),
            (r"\bright face\b[^.]{0,48}\bsketch plane\b", "right"),
            (r"\bleft face\b[^.]{0,48}\bsketch plane\b", "left"),
            (r"\bexisting face\b[^.]{0,48}\bsketch plane\b", "existing"),
        )
    )
    datum_planes = _ordered_hits(
        (
            (r"\bxy plane\b", "xy"),
            (r"\bxz plane\b", "xz"),
            (r"\byz plane\b", "yz"),
            (r"\bfront view plane\b", "front"),
            (r"\bback view plane\b", "back"),
            (r"\bright view plane\b", "right"),
            (r"\bleft view plane\b", "left"),
        )
    )
    multi_plane_additive_signature_options = (
        _extract_multi_plane_additive_signature_options(text)
    )
    edge_targets = _ordered_hits(
        (
            (r"\bbottom outer edges?\b[^.]{0,64}\bparallel to (?:the )?x(?:[\s-]?axis)?\b", "x_parallel_bottom_outer_edges"),
            (r"\bbottom outer edges?\b[^.]{0,64}\bparallel to (?:the )?y(?:[\s-]?axis)?\b", "y_parallel_bottom_outer_edges"),
            (r"\bbottom outer edges?\b[^.]{0,64}\bparallel to (?:the )?z(?:[\s-]?axis)?\b", "z_parallel_bottom_outer_edges"),
            (r"\bparallel to (?:the )?x(?:[\s-]?axis)?\b[^.]{0,64}\bbottom outer edges?\b", "x_parallel_bottom_outer_edges"),
            (r"\bparallel to (?:the )?y(?:[\s-]?axis)?\b[^.]{0,64}\bbottom outer edges?\b", "y_parallel_bottom_outer_edges"),
            (r"\bparallel to (?:the )?z(?:[\s-]?axis)?\b[^.]{0,64}\bbottom outer edges?\b", "z_parallel_bottom_outer_edges"),
            (r"\btop outer edges?\b[^.]{0,64}\bparallel to (?:the )?x(?:[\s-]?axis)?\b", "x_parallel_top_outer_edges"),
            (r"\btop outer edges?\b[^.]{0,64}\bparallel to (?:the )?y(?:[\s-]?axis)?\b", "y_parallel_top_outer_edges"),
            (r"\btop outer edges?\b[^.]{0,64}\bparallel to (?:the )?z(?:[\s-]?axis)?\b", "z_parallel_top_outer_edges"),
            (r"\bparallel to (?:the )?x(?:[\s-]?axis)?\b[^.]{0,64}\btop outer edges?\b", "x_parallel_top_outer_edges"),
            (r"\bparallel to (?:the )?y(?:[\s-]?axis)?\b[^.]{0,64}\btop outer edges?\b", "y_parallel_top_outer_edges"),
            (r"\bparallel to (?:the )?z(?:[\s-]?axis)?\b[^.]{0,64}\btop outer edges?\b", "z_parallel_top_outer_edges"),
            (r"\bouter edges?\b[^.]{0,64}\bparallel to (?:the )?x(?:[\s-]?axis)?\b", "x_parallel_outer_edges"),
            (r"\bouter edges?\b[^.]{0,64}\bparallel to (?:the )?y(?:[\s-]?axis)?\b", "y_parallel_outer_edges"),
            (r"\bouter edges?\b[^.]{0,64}\bparallel to (?:the )?z(?:[\s-]?axis)?\b", "z_parallel_outer_edges"),
            (r"\bparallel to (?:the )?x(?:[\s-]?axis)?\b[^.]{0,64}\bouter edges?\b", "x_parallel_outer_edges"),
            (r"\bparallel to (?:the )?y(?:[\s-]?axis)?\b[^.]{0,64}\bouter edges?\b", "y_parallel_outer_edges"),
            (r"\bparallel to (?:the )?z(?:[\s-]?axis)?\b[^.]{0,64}\bouter edges?\b", "z_parallel_outer_edges"),
            (r"\bsharp edge at the bottom of (?:the )?(?:v[- ]shaped |u[- ]shaped )?(?:groove|notch|slot)\b", "inner_bottom_edges"),
            (r"\bbottom of (?:the )?(?:v[- ]shaped |u[- ]shaped )?(?:groove|notch|slot)\b", "inner_bottom_edges"),
            (r"\bbottom internal edges?\b", "inner_bottom_edges"),
            (r"\binternal bottom edges?\b", "inner_bottom_edges"),
            (r"\bbottom internal edge(?:\(s\)|s)?\b", "inner_bottom_edges"),
            (r"\bbottom of (?:the )?(?:triangular |rectangular |circular )?(?:recess|pocket)\b", "inner_bottom_edges"),
            (r"\binner bottom edges?\b", "inner_bottom_edges"),
            (r"\binternal edges?\b", "inner_edges"),
            (r"\binner top edges?\b", "inner_top_edges"),
            (r"\binner edges?\b", "inner_edges"),
            (r"\btop outer edges?\b", "top_outer_edges"),
            (r"\bbottom outer edges?\b", "bottom_outer_edges"),
            (r"\bouter edges?\b", "outer_edges"),
            (r"\btop edges?\b", "top_edges"),
            (r"\bbottom edges?\b", "bottom_edges"),
        )
    )
    mentions_revolved_groove_cut = _requirement_suggests_revolved_groove_cut(text)
    mentions_sweep = _requirement_suggests_sweep(text)
    mentions_countersink = any(
        token in text
        for token in (
            "countersink",
            "countersunk",
            "counterbore",
            "flat-head screw",
            "flat head screw",
        )
    )
    mentions_notch_like = any(
        token in text
        for token in (
            "notch",
            "u-shape",
            "u shaped",
            "u-shaped",
            "slot",
            "v-shaped",
            "v-groove",
            "middle groove",
        )
    ) or ("groove" in text and not mentions_revolved_groove_cut)
    mentions_spherical_recess = any(
        token in text
        for token in (
            "hemisphere",
            "hemispherical",
            "spherical recess",
            "spherical cavity",
            "spherical depression",
            "hemispherical recess",
            "hemispherical cavity",
            "hemispherical depression",
        )
    ) or (
        "sphere" in text
        and any(
            token in text
            for token in ("recess", "pit", "cavity", "depression", "hollow")
        )
    )

    return RequirementSemantics(
        normalized_text=text,
        face_targets=face_targets,
        datum_planes=datum_planes,
        multi_plane_additive_signature_options=multi_plane_additive_signature_options,
        edge_targets=edge_targets,
        mentions_subtractive_edit=any(
            token in text
            for token in (
                "cut",
                "subtract",
                "subtractive",
                "difference",
                "remove",
                "hole",
                "slot",
                "groove",
                "notch",
            )
        ),
        mentions_additive_face_feature=_requirement_suggests_additive_face_feature(
            text
        ),
        mentions_hole="hole" in text,
        mentions_countersink=mentions_countersink,
        mentions_pattern=_requirement_suggests_pattern_distribution(text),
        mentions_spherical_recess=mentions_spherical_recess,
        mentions_fillet="fillet" in text or "rounded" in text,
        mentions_chamfer="chamfer" in text or "bevel" in text,
        mentions_targeted_edge_feature=bool(edge_targets) or "edge" in text,
        mentions_face_edit=bool(face_targets),
        mentions_nested_profile_cutout=_requirement_suggests_nested_profile_cutout(
            text
        ),
        mentions_profile_region_frame=_requirement_suggests_profile_region_frame(text),
        mentions_revolved_groove_cut=mentions_revolved_groove_cut,
        mentions_sweep=mentions_sweep,
        mentions_notch_like=mentions_notch_like,
        mentions_multi_plane_additive_union=(
            _requirement_suggests_multi_plane_additive_union(text)
        ),
        prefers_explicit_inner_void_cut=_requirement_prefers_explicit_inner_void_cut(
            text
        ),
    )


def _normalize_text(requirements: dict[str, Any] | None) -> str:
    return normalize_requirement_text(requirements)


def extract_rectangular_notch_profile_spec(
    requirements: dict[str, Any] | None,
    requirement_text: str | None = None,
) -> RectangularNotchProfileSpec | None:
    text = normalize_requirement_text(requirements, requirement_text)
    if not text:
        return None

    preferred_plane: str | None = None
    if "select the xz plane" in text or "front view" in text or "front plane" in text:
        preferred_plane = "XZ"
    elif "select the yz plane" in text or "side view" in text or "side plane" in text:
        preferred_plane = "YZ"
    elif "select the xy plane" in text or "top view" in text or "top plane" in text:
        preferred_plane = "XY"
    top_face_full_span_channel = (
        "top face" in text
        and any(token in text for token in ("slot", "notch", "channel section"))
        and any(
            token in text
            for token in ("spans the full", "spans full", "full length", "full 80.0")
        )
        and any(token in text for token in ("u-shaped", "u shape", "channel section"))
    )
    if top_face_full_span_channel:
        preferred_plane = "YZ"

    explicit_pairs: list[tuple[float, float]] = []
    for match in _DIMENSION_PAIR_PATTERN.finditer(text):
        try:
            dim_a = float(match.group("a"))
            dim_b = float(match.group("b"))
        except Exception:
            continue
        if dim_a <= 0.0 or dim_b <= 0.0 or dim_a == dim_b:
            continue
        explicit_pairs.append((max(dim_a, dim_b), min(dim_a, dim_b)))

    if len(explicit_pairs) >= 2:
        explicit_pairs.sort(key=lambda item: item[0] * item[1], reverse=True)
        outer = explicit_pairs[0]
        inner = next(
            (
                item
                for item in reversed(explicit_pairs)
                if item[0] * item[1] < outer[0] * outer[1]
            ),
            explicit_pairs[-1],
        )
        if inner[0] < outer[0] and inner[1] < outer[1]:
            return RectangularNotchProfileSpec(
                preferred_plane=preferred_plane,
                outer_width=outer[0],
                outer_height=outer[1],
                inner_width=inner[0],
                inner_height=inner[1],
                bottom_offset=outer[1] - inner[1],
            )

    def _match_float(pattern: str) -> float | None:
        match = re.search(pattern, text)
        if match is None:
            return None
        try:
            value = float(match.group(1))
        except Exception:
            return None
        return value if value > 0.0 else None

    def _match_all_floats(pattern: str) -> list[float]:
        values: list[float] = []
        for match in re.finditer(pattern, text):
            try:
                value = float(match.group(1))
            except Exception:
                continue
            if value > 0.0:
                values.append(value)
        return values

    outer_width = _match_float(
        r"(?:bottom width|overall width|outer width|base width)\s*(?:is|of)?\s*([0-9]+(?:\.[0-9]+)?)"
    )
    outer_height = _match_float(
        r"(?:side walls? (?:are|is)|overall height|outer height|height)\s*([0-9]+(?:\.[0-9]+)?)"
    )
    if outer_height is None:
        outer_height = _match_float(
            r"(?:side walls? are|side walls? is)\s*([0-9]+(?:\.[0-9]+)?)\s*(?:millimeters?|mm)?\s*high"
        )
    inner_width = _match_float(
        r"(?:middle groove|middle slot|middle notch|groove|slot|notch)\s*(?:is|width is|width of)?\s*([0-9]+(?:\.[0-9]+)?)\s*(?:millimeters?|mm)?\s*wide"
    )
    bottom_offset = _match_float(
        r"distance from the (?:groove|slot|notch) bottom to the bottom edge\s*(?:is|of)?\s*([0-9]+(?:\.[0-9]+)?)"
    )
    inner_height = _match_float(
        r"(?:groove|slot|notch)\s*(?:is|depth is|depth of)?\s*([0-9]+(?:\.[0-9]+)?)\s*(?:millimeters?|mm)?\s*(?:deep|high)"
    )
    if inner_height is None:
        inner_height = _match_float(
            r"(?:middle groove|middle slot|middle notch)\s*(?:depth|height)\s*(?:is|of)?\s*([0-9]+(?:\.[0-9]+)?)"
        )
    if (
        inner_height is None
        and outer_height is not None
        and bottom_offset is not None
        and outer_height > bottom_offset
    ):
        inner_height = outer_height - bottom_offset
    if top_face_full_span_channel:
        wide_values = _match_all_floats(
            r"([0-9]+(?:\.[0-9]+)?)\s*(?:millimeters?|mm)?\s*wide(?:\s+in\s+[xyz])?"
        )
        high_values = _match_all_floats(
            r"([0-9]+(?:\.[0-9]+)?)\s*(?:millimeters?|mm)?\s*high"
        )
        deep_values = _match_all_floats(
            r"([0-9]+(?:\.[0-9]+)?)\s*(?:millimeters?|mm)?\s*deep"
        )
        if outer_width is None and wide_values:
            outer_width = wide_values[0]
        if inner_width is None and len(wide_values) >= 2:
            inner_width = wide_values[-1]
        if outer_height is None and high_values:
            outer_height = high_values[0]
        if inner_height is None and deep_values:
            inner_height = deep_values[0]
        if (
            bottom_offset is None
            and outer_height is not None
            and inner_height is not None
            and outer_height > inner_height
        ):
            bottom_offset = outer_height - inner_height

    if (
        outer_width is None
        or outer_height is None
        or inner_width is None
        or inner_height is None
    ):
        return None
    if (
        inner_width >= outer_width
        or inner_height >= outer_height
        or outer_width <= 0.0
        or outer_height <= 0.0
        or inner_width <= 0.0
        or inner_height <= 0.0
    ):
        return None

    return RectangularNotchProfileSpec(
        preferred_plane=preferred_plane,
        outer_width=outer_width,
        outer_height=outer_height,
        inner_width=inner_width,
        inner_height=inner_height,
        bottom_offset=bottom_offset,
    )


_DIMENSION_PAIR_PATTERN = re.compile(
    r"(?P<a>\d+(?:\.\d+)?)\s*(?:x|×)\s*(?P<b>\d+(?:\.\d+)?)"
)
_EXTRUDE_DISTANCE_PATTERN = re.compile(
    r"\bextrud(?:e|ed)(?: it| them)?(?: by| to)?\s+(?P<distance>\d+(?:\.\d+)?)"
)
_MULTI_PLANE_SEGMENT_PATTERN = re.compile(
    r"(?P<plane>xy|xz|yz)\s+plane(?P<body>.*?)(?=(?:\b(?:xy|xz|yz)\s+plane\b)|$)",
    re.IGNORECASE,
)


def _extract_multi_plane_additive_signature_options(
    text: str,
) -> tuple[tuple[tuple[float, float, float], ...], ...]:
    signature_groups: list[tuple[tuple[float, float, float], ...]] = []
    for match in _MULTI_PLANE_SEGMENT_PATTERN.finditer(text):
        plane = match.group("plane").lower()
        body = match.group("body").lower()
        if "rectangle" not in body or "extrud" not in body:
            continue
        dims_match = _DIMENSION_PAIR_PATTERN.search(body)
        distance_match = _EXTRUDE_DISTANCE_PATTERN.search(body)
        if dims_match is None or distance_match is None:
            continue
        in_plane_a = float(dims_match.group("a"))
        in_plane_b = float(dims_match.group("b"))
        extrusion_span = float(distance_match.group("distance"))
        options: list[tuple[float, float, float]] = []
        if plane == "xy":
            candidates = (
                (in_plane_a, in_plane_b, extrusion_span),
                (in_plane_b, in_plane_a, extrusion_span),
            )
        elif plane == "xz":
            candidates = (
                (in_plane_a, extrusion_span, in_plane_b),
                (in_plane_b, extrusion_span, in_plane_a),
            )
        else:
            candidates = (
                (extrusion_span, in_plane_a, in_plane_b),
                (extrusion_span, in_plane_b, in_plane_a),
            )
        seen_options: set[tuple[float, float, float]] = set()
        for candidate in candidates:
            if candidate in seen_options:
                continue
            seen_options.add(candidate)
            options.append(candidate)
        signature_groups.append(tuple(options))
    return tuple(signature_groups)
