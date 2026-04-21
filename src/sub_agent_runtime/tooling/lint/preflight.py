from __future__ import annotations

import ast
import re
from typing import Any

from sub_agent_runtime.tooling.cad_actions import (
    preflight_gate_apply_cad_action as _preflight_gate_apply_cad_action,
)
from sub_agent_runtime.tooling.lint.ast_utils import (
    _strip_python_comments_and_strings,
)
from sub_agent_runtime.tooling.lint.routing import (
    _candidate_lint_family_ids,
    _requirement_mentions_local_finish_fillet_tail,
)
from sub_agent_runtime.tooling.lint.recipes import (
    _build_preflight_repair_recipe,
    _preflight_lint_failure_kind,
    _requirement_mentions_plane_anchored_positive_extrude,
)
from sub_agent_runtime.tooling.lint.families.builders import (
    _find_active_buildpart_host_part_mutation_hits,
    _find_active_buildpart_temporary_primitive_arithmetic_hits,
    _find_active_buildpart_temporary_primitive_transform_hits,
    _find_broad_local_finish_tail_fillet_hits,
    _find_broad_shell_axis_fillet_hits,
    _find_builder_method_reference_assignment_hits,
    _find_buildpart_topology_access_inside_buildsketch_hits,
    _find_case_drift_local_symbol_hits,
    _find_clamshell_unrotated_default_hinge_cylinder_hits,
    _find_compound_positional_children_contract_hits,
    _find_detached_subtractive_builder_without_host_hits,
    _find_display_only_helper_hits,
    _find_explicit_anchor_manual_cutter_missing_subtract_hits,
    _find_nested_buildpart_part_arithmetic_hits,
    _find_nested_buildpart_part_transform_hits,
    _find_nested_subtractive_buildpart_hits,
    collect_builder_context_hits,
)
from sub_agent_runtime.tooling.lint.families.countersinks import (
    _find_buildsketch_countersink_context_hits,
    _find_countersink_keyword_alias_hits,
    collect_countersink_contract_hits,
)
from sub_agent_runtime.tooling.lint.families.keywords import (
    _find_cone_keyword_alias_hits,
    _find_filter_by_position_keyword_band_hits,
    _find_filter_by_position_plane_axis_hits,
    _find_lowercase_vector_component_attribute_hits,
    _find_plane_keyword_alias_hits,
    _find_regular_polygon_keyword_alias_hits,
    _find_slot_center_point_center_alias_hits,
    _find_slot_center_point_radius_alias_hits,
    _find_slot_center_to_center_keyword_alias_hits,
    collect_keyword_contract_hits,
)
from sub_agent_runtime.tooling.lint.families.path_profiles import (
    _find_annular_profile_face_extraction_sweep_hits,
    _find_annular_profile_face_splitting_hits,
    _find_buildsketch_curve_context_hits,
    _find_buildsketch_wire_profile_missing_make_face_hits,
    _find_center_arc_keyword_alias_hits,
    _find_center_arc_missing_start_angle_hits,
    _find_circle_make_face_trim_profile_hits,
    _find_explicit_radius_arc_helper_hits,
    _find_solid_sweep_invalid_keyword_hits,
    _find_sweep_path_method_reference_hits,
    _find_sweep_profile_face_method_reference_hits,
    _find_sweep_section_keyword_alias_hits,
    _find_symbolic_degree_constant_hits,
    _requirement_prefers_center_arc_for_explicit_radius_path,
    collect_path_profile_contract_hits,
)
from sub_agent_runtime.tooling.lint.families.planes import (
    _find_centered_box_face_plane_offset_span_mismatch_hits,
    _find_directional_drill_plane_offset_coordinate_hits,
    _find_face_plane_shift_origin_global_coordinate_hits,
    _find_named_face_plane_family_mismatch_hits,
    _find_plane_located_call_hits,
    _find_plane_moved_call_hits,
    _find_plane_rotate_method_hits,
    _find_plane_rotated_origin_guess_hits,
    collect_plane_contract_hits,
    collect_plane_transform_hits,
)
from sub_agent_runtime.tooling.lint.families.structural import (
    _find_buildpart_sketch_primitive_context_hits,
    _find_global_fillet_helper_argument_contract_hits,
    _find_member_fillet_radius_keyword_conflict_hits,
    _find_rectanglerounded_radius_bounds_hits,
    _find_topology_geometry_attribute_hits,
    _find_transform_context_manager_hits,
    _find_vector_component_indexing_hits,
)
from sub_agent_runtime.turn_state import RunState


def _preflight_lint_execute_build123d(
    *,
    code: str,
    session_id: str,
    requirement_text: str,
    run_state: RunState | None,
) -> dict[str, Any] | None:
    code_for_lint = _strip_python_comments_and_strings(code)
    compact_lowered = re.sub(r"\s+", "", code_for_lint.lower())
    requirement_lower = str(requirement_text or "").strip().lower()
    hits: list[dict[str, Any]] = []
    parsed_tree: ast.AST | None = None

    try:
        parsed_tree = ast.parse(code)
    except SyntaxError as exc:
        line_no = int(getattr(exc, "lineno", 0) or 0)
        message = str(getattr(exc, "msg", "") or "invalid Python syntax").strip()
        hits.append(
            {
                "rule_id": "python_syntax.invalid_script",
                "message": (
                    "execute_build123d code must be valid Python before sandbox execution."
                ),
                "repair_hint": (
                    f"Repair the Python syntax/indentation at line {line_no}: {message}."
                    if line_no > 0
                    else f"Repair the Python syntax/indentation: {message}."
                ),
            }
        )
    if parsed_tree is not None:
        candidate_family_ids = _candidate_lint_family_ids(
            requirement_text=requirement_text,
            run_state=run_state,
        )
        candidate_family_id_set = {
            str(family_id).strip()
            for family_id in candidate_family_ids
            if str(family_id).strip()
        }
        hits.extend(collect_plane_transform_hits(parsed_tree=parsed_tree))
        hits.extend(
            collect_builder_context_hits(
                parsed_tree=parsed_tree,
                requirement_lower=requirement_lower,
                code=code,
                candidate_family_id_set=candidate_family_id_set,
            )
        )
        hits.extend(
            collect_plane_contract_hits(
                parsed_tree=parsed_tree,
                requirement_lower=requirement_lower,
            )
        )
        hits.extend(collect_countersink_contract_hits(parsed_tree))
        hits.extend(collect_keyword_contract_hits(parsed_tree))
        hits.extend(
            collect_path_profile_contract_hits(
                parsed_tree,
                code_for_lint=code_for_lint,
                requirement_lower=requirement_lower,
            )
        )
        for split_hit in _find_annular_profile_face_splitting_hits(parsed_tree):
            line_no = int(split_hit.get("line_no") or 0)
            builder_alias = str(split_hit.get("builder_alias") or "profile").strip()
            hits.append(
                {
                    "rule_id": "invalid_build123d_contract.annular_profile_face_splitting",
                    "message": (
                        "A single subtractive annular `BuildSketch` yields one face with "
                        "inner wires, not a stable pair of separate outer/inner faces."
                    ),
                    "repair_hint": (
                        f"Do not index `{builder_alias}.faces()[1]` or sorted-face variants "
                        "after one annular sketch. Sweep the annular sketch directly with "
                        "`sweep(profile.sketch, path=path_wire)`, or rebuild truly separate "
                        "outer/inner section faces before doing a solid boolean."
                        + (
                            f" Repair the annular face extraction at line {line_no}."
                            if line_no > 0
                            else ""
                        )
                    ),
                }
            )
        for extraction_hit in _find_annular_profile_face_extraction_sweep_hits(parsed_tree):
            line_no = int(extraction_hit.get("line_no") or 0)
            builder_alias = str(extraction_hit.get("builder_alias") or "profile").strip()
            hits.append(
                {
                    "rule_id": "invalid_build123d_contract.annular_profile_face_extraction",
                    "message": (
                        "Extracting `BuildSketch.face()` from one subtractive annular sketch "
                        "and sweeping that face often collapses the inner-wire boolean lane "
                        "and can fail with a null sweep result."
                    ),
                    "repair_hint": (
                        f"Do not sweep `{builder_alias}.face()` or a face variable captured "
                        f"from `{builder_alias}` when the section is one annular sketch. "
                        "Prefer `sweep(profile.sketch, path=path_wire)` for the same-sketch "
                        "annular section, or rebuild truly separate outer/inner section faces "
                        "before doing one explicit solid boolean."
                        + (
                            f" Repair the annular sweep section at line {line_no}."
                            if line_no > 0
                            else ""
                        )
                    ),
                }
            )
        for vector_hit in _find_vector_component_indexing_hits(parsed_tree):
            line_no = int(vector_hit.get("line_no") or 0)
            index_value = int(vector_hit.get("index_value") or 0)
            hits.append(
                {
                    "rule_id": "invalid_build123d_contract.vector_component_indexing",
                    "message": (
                        "Build123d points/vectors returned by curve endpoint or tangent "
                        "expressions are not subscriptable sequence objects."
                    ),
                    "repair_hint": (
                        "Use `.X`, `.Y`, and `.Z` (or explicitly convert to a tuple) "
                        f"instead of `[{index_value}]` when reading Build123d vector components."
                        + (
                            f" Repair the vector component access at line {line_no}."
                            if line_no > 0
                            else ""
                        )
                    ),
                }
            )
        for vector_attr_hit in _find_lowercase_vector_component_attribute_hits(parsed_tree):
            line_no = int(vector_attr_hit.get("line_no") or 0)
            attr_name = str(vector_attr_hit.get("attr_name") or "").strip()
            hits.append(
                {
                    "rule_id": "invalid_build123d_api.vector_lowercase_component_attribute",
                    "message": (
                        "Build123d vectors/points use uppercase component attributes "
                        f"such as `.X`, `.Y`, and `.Z`, not lowercase `.{attr_name}`."
                    ),
                    "repair_hint": (
                        "Rename lowercase vector component access such as `.z` to the "
                        "Build123d attribute form `.Z` (or explicitly convert the vector "
                        "to a tuple first)."
                        + (
                            f" Repair the vector component attribute at line {line_no}."
                            if line_no > 0
                            else ""
                        )
                    ),
                }
            )
        for geometry_attr_hit in _find_topology_geometry_attribute_hits(parsed_tree):
            line_no = int(geometry_attr_hit.get("line_no") or 0)
            hits.append(
                {
                    "rule_id": "invalid_build123d_api.topology_geometry_attribute",
                    "message": (
                        "Build123d topology entities such as Edge/Face/Wire/Solid do not expose "
                        "a generic `.geometry` attribute for downstream filtering."
                    ),
                    "repair_hint": (
                        "Replace `.geometry` checks with Build123d-native topology evidence such "
                        "as `.geom_type`, `.length`, `.radius`, `.bounding_box()`, `.center()`, "
                        "or another explicit measurement helper that matches the intended selector."
                        + (
                            f" Repair the `.geometry` attribute access at line {line_no}."
                            if line_no > 0
                            else ""
                        )
                    ),
                }
            )
        for assignment_hit in _find_builder_method_reference_assignment_hits(parsed_tree):
            line_no = int(assignment_hit.get("line_no") or 0)
            builder_name = str(assignment_hit.get("builder_name") or "Builder").strip()
            method_name = str(assignment_hit.get("method_name") or "method").strip()
            builder_alias = str(assignment_hit.get("builder_alias") or "builder").strip()
            hits.append(
                {
                    "rule_id": "invalid_build123d_contract.builder_method_reference_assignment",
                    "message": (
                        f"`{builder_name}.{method_name}` is a method. Assigning "
                        f"`{builder_alias}.{method_name}` stores a bound method object instead "
                        "of the actual geometry."
                    ),
                    "repair_hint": (
                        f"Call `{builder_alias}.{method_name}()` when capturing that geometry, "
                        "or keep the builder-native sketch/wire object instead of storing the method reference."
                        + (
                            f" Repair the builder method assignment at line {line_no}."
                            if line_no > 0
                            else ""
                        )
                    ),
                }
            )
        for fillet_hit in _find_member_fillet_radius_keyword_conflict_hits(parsed_tree):
            line_no = int(fillet_hit.get("line_no") or 0)
            target_label = str(fillet_hit.get("target_label") or "solid").strip()
            hits.append(
                {
                    "rule_id": "invalid_build123d_contract.member_fillet_radius_keyword_conflict",
                    "message": (
                        "Member-style `.fillet(...)` already uses the radius as part of the "
                        "method signature. Passing a positional edge/edge-list together with "
                        "`radius=` creates a conflicting Build123d fillet contract."
                    ),
                    "repair_hint": (
                        f"Do not mix `{target_label}.fillet(<edge>, radius=...)`. "
                        "Either use the verified member-call contract with the radius first, "
                        "or prefer the global `fillet(edge_list, radius=...)` helper on a "
                        "selected ShapeList. "
                        + (
                            f"Repair the member fillet call at line {line_no}."
                            if line_no > 0
                            else "Repair the member fillet call."
                        )
                    ),
                }
            )
        for fillet_hit in _find_global_fillet_helper_argument_contract_hits(parsed_tree):
            line_no = int(fillet_hit.get("line_no") or 0)
            target_label = str(fillet_hit.get("target_label") or "shape").strip()
            hits.append(
                {
                    "rule_id": "invalid_build123d_contract.global_fillet_helper_argument_contract",
                    "message": (
                        "Global `fillet(...)` follows the `(objects, radius)` contract. "
                        "Passing the host shape as a separate positional argument before the "
                        "selected edges/vertices creates an invalid Build123d helper call."
                    ),
                    "repair_hint": (
                        f"Do not call `fillet({target_label}, edge_list, radius)`. "
                        "Use the global helper as `fillet(edge_list, radius=...)`, or use the "
                        "member form `shape.fillet(radius, edge_list)` on the verified host shape. "
                        + (
                            f"Repair the global fillet helper call at line {line_no}."
                            if line_no > 0
                            else "Repair the global fillet helper call."
                        )
                    ),
                }
            )
        if _requirement_mentions_local_finish_fillet_tail(requirement_lower):
            for fillet_hit in _find_broad_local_finish_tail_fillet_hits(parsed_tree):
                line_no = int(fillet_hit.get("line_no") or 0)
                builder_label = str(fillet_hit.get("builder_label") or "part").strip()
                hits.append(
                    {
                        "rule_id": "invalid_build123d_contract.broad_local_finish_tail_fillet_on_first_write",
                        "message": (
                            "Do not spend the first whole-part write on a broad fillet selector "
                            "when the requirement already frames that fillet as a later local-"
                            "finish detail. Broad `edges().filter_by(...)` or "
                            "`filter_by_position(...)` selectors are too unstable before exact "
                            "topology refs exist."
                        ),
                        "repair_hint": (
                            f"Postpone the broad fillet on `{builder_label}` until the host "
                            "geometry is stable and query_topology can provide exact edge refs, "
                            "or narrow the selector to a verified edge subset before filleting."
                            + (
                                f" Repair the broad local-finish tail fillet at line {line_no}."
                                if line_no > 0
                                else " Repair the broad local-finish tail fillet."
                            )
                        ),
                    }
                )
        if (
            {"half_shell", "nested_hollow_section"} & candidate_family_id_set
            or any(
                token in requirement_lower
                for token in ("enclosure", "clamshell", "lid", "base", "shell", "body")
            )
        ):
            for fillet_hit in _find_broad_shell_axis_fillet_hits(parsed_tree):
                line_no = int(fillet_hit.get("line_no") or 0)
                builder_label = str(fillet_hit.get("builder_label") or "part").strip()
                hits.append(
                    {
                        "rule_id": "invalid_build123d_contract.broad_shell_axis_fillet_on_fresh_host",
                        "message": (
                            "Do not immediately fillet a fresh enclosure/shell host with a broad "
                            "`edges().filter_by(Axis.Z)` selection. That selector usually mixes "
                            "outer shell edges with seam, notch, hinge, or interior edges and is "
                            "too unstable for an early whole-part rebuild."
                        ),
                        "repair_hint": (
                            f"Postpone the broad shell-edge fillet on `{builder_label}` until the "
                            "host geometry and local cuts are already valid, or narrow the fillet "
                            "to a verified outer-edge subset before applying the radius."
                            + (
                                f" Repair the broad shell Axis.Z fillet at line {line_no}."
                                if line_no > 0
                                else " Repair the broad shell Axis.Z fillet."
                            )
                        ),
                    }
                )
        for curve_hit in _find_buildsketch_curve_context_hits(parsed_tree):
            line_no = int(curve_hit.get("line_no") or 0)
            helper_name = str(curve_hit.get("helper_name") or "CurveHelper").strip()
            hits.append(
                {
                    "rule_id": "invalid_build123d_context.curve_requires_buildline",
                    "message": (
                        f"`{helper_name}(...)` is a Build123d curve helper that belongs "
                        "inside `BuildLine`, not directly inside `BuildSketch`."
                    ),
                    "repair_hint": (
                        "Move the curve construction into `with BuildLine():`, close the "
                        "wire explicitly when needed, then call `make_face()` before "
                        "extruding or revolving."
                        + (
                            f" Repair the `{helper_name}` builder context at line {line_no}."
                            if line_no > 0
                            else ""
                        )
                    ),
                }
            )
        for sketch_hit in _find_buildpart_sketch_primitive_context_hits(parsed_tree):
            line_no = int(sketch_hit.get("line_no") or 0)
            helper_name = str(sketch_hit.get("helper_name") or "SketchPrimitive").strip()
            hits.append(
                {
                    "rule_id": "invalid_build123d_context.sketch_primitive_requires_buildsketch",
                    "message": (
                        f"`{helper_name}(...)` is a sketch primitive that belongs inside "
                        "`BuildSketch`, not directly inside an active `BuildPart`."
                    ),
                    "repair_hint": (
                        "Open `with BuildSketch(target_plane):`, build the 2D profile there, "
                        "then `extrude(...)` / subtract it from the host after the sketch is complete."
                        + (
                            f" Repair the `{helper_name}` builder context at line {line_no}."
                            if line_no > 0
                            else ""
                        )
                    ),
                }
            )
        for radius_hit in _find_rectanglerounded_radius_bounds_hits(parsed_tree):
            line_no = int(radius_hit.get("line_no") or 0)
            width_value = radius_hit.get("width")
            height_value = radius_hit.get("height")
            radius_value = radius_hit.get("radius")
            hits.append(
                {
                    "rule_id": "invalid_build123d_contract.rectanglerounded_radius_bounds",
                    "message": (
                        "`RectangleRounded(width, height, radius)` requires both profile spans "
                        "to stay strictly greater than `2 * radius`. The current rounded "
                        "rectangle will fail at runtime before a solid is created."
                    ),
                    "repair_hint": (
                        "Reduce the rounded-rectangle radius so it is smaller than half of the "
                        "smaller profile span, or enlarge the sketch width/height before calling "
                        "`RectangleRounded(...)`."
                        + (
                            f" Current values evaluate to width={width_value}, height={height_value}, "
                            f"radius={radius_value}. Repair the RectangleRounded radius contract "
                            f"at line {line_no}."
                            if line_no > 0
                            else ""
                        )
                    ),
                }
            )
        for transform_hit in _find_transform_context_manager_hits(parsed_tree):
            line_no = int(transform_hit.get("line_no") or 0)
            helper_name = str(transform_hit.get("helper_name") or "Transform").strip()
            hits.append(
                {
                    "rule_id": "invalid_build123d_context.transform_context_manager",
                    "message": (
                        f"`{helper_name}(...)` is a transform helper, not a context manager."
                    ),
                    "repair_hint": (
                        "Use `Locations(...)` for scoped placement, or apply the transform with "
                        f"`{helper_name}(...) * solid` on a detached solid instead of `with {helper_name}(...):`."
                        + (
                            f" Repair the transform context-manager misuse at line {line_no}."
                            if line_no > 0
                            else ""
                        )
                    ),
                }
            )
        for missing_face_hit in _find_buildsketch_wire_profile_missing_make_face_hits(parsed_tree):
            line_no = int(missing_face_hit.get("line_no") or 0)
            hits.append(
                {
                    "rule_id": "invalid_build123d_contract.buildsketch_wire_requires_make_face",
                    "message": (
                        "A `BuildSketch` that only contains wire geometry from `BuildLine` "
                        "must call lowercase `make_face()` before downstream extrude/revolve "
                        "operations; otherwise the sketch can stay empty."
                    ),
                    "repair_hint": (
                        "After the closed `BuildLine` wire is complete, call lowercase "
                        "`make_face()` in the same `BuildSketch` before extruding or revolving."
                        + (
                            f" Repair the missing face conversion at line {line_no}."
                            if line_no > 0
                            else ""
                        )
                    ),
                }
            )
        for mixed_profile_hit in _find_circle_make_face_trim_profile_hits(parsed_tree):
            line_no = int(mixed_profile_hit.get("line_no") or 0)
            hits.append(
                {
                    "rule_id": "invalid_build123d_contract.circle_make_face_trim_profile",
                    "message": (
                        "`Circle(...)` already creates a full circular sketch region in "
                        "Build123d. Mixing it with `BuildLine` plus `make_face()` in the "
                        "same `BuildSketch` to fake a semicircle or rounded notch/profile "
                        "usually produces the wrong face or a non-planar profile."
                    ),
                    "repair_hint": (
                        "Do not trim a full `Circle(...)` with helper lines and then call "
                        "`make_face()` in the same sketch. Build the half-round or arc "
                        "profile entirely inside `BuildLine` with `CenterArc(...)` or "
                        "`RadiusArc(...)`, close it with explicit `Line(...)` segments, "
                        "then call `make_face()` before extruding."
                        + (
                            f" Repair the mixed circle/trim profile at line {line_no}."
                            if line_no > 0
                            else ""
                        )
                    ),
                }
            )

    if re.search(r"^\s*(import|from)\s+cadquery\b", code_for_lint, flags=re.MULTILINE):
        hits.append(
            {
                "rule_id": "legacy_import.unsupported_modeling_module",
                "message": "Non-Build123d modeling-kernel imports are not allowed in execute_build123d.",
                "repair_hint": (
                    "Rewrite the script with BuildPart, BuildSketch, BuildLine, Plane, Axis, "
                    "Pos, Rot, and Locations instead of importing legacy modeling kernels."
                ),
            }
        )
    if re.search(r"\bcq\.", code_for_lint) or "workplane(" in compact_lowered:
        hits.append(
            {
                "rule_id": "legacy_api.workplane_chain",
                "message": "Legacy Workplane-chain code is not allowed in execute_build123d.",
                "repair_hint": (
                    "Use BuildPart for solids, BuildSketch for profiles, BuildLine for rails, "
                    "and Plane/Axis/Pos/Rot/Locations for placement."
                ),
            }
        )
    if re.search(r"\.\s*countersinkhole\s*\(", code_for_lint, flags=re.IGNORECASE):
        hits.append(
            {
                "rule_id": "legacy_api.countersink_workplane_method",
                "message": "Legacy countersink-hole helpers are not valid Build123d code.",
                "repair_hint": (
                    "Model countersinks with BuildSketch/Locations plus explicit subtractive "
                    "cutters or a supported Build123d hole recipe."
                ),
            }
        )
    if re.search(r"\b(?:CountersinkHole|CounterSink|Countersink|countersink_hole)\s*\(", code_for_lint):
        hits.append(
            {
                "rule_id": "invalid_build123d_api.countersink_helper_name",
                "message": (
                    "Build123d uses `CounterSinkHole(...)`, not helper-name guesses such "
                    "as `CountersinkHole(...)`, `CounterSink(...)`, `Countersink(...)`, "
                    "or `countersink_hole(...)`."
                ),
                "repair_hint": (
                    "Do not guess countersink helper names. If you truly use the helper, "
                    "the exact name is `CounterSinkHole(...)` rather than `Countersink(...)`; "
                    "for explicit planar countersink "
                    "arrays, prefer one `CounterSinkHole(...)` pass first with explicit host-face "
                    "placement. Only fall back to an explicit same-builder cone/cylinder or "
                    "revolved countersink recipe when the helper contract cannot express the "
                    "host/placement semantics cleanly or prior evidence shows the helper result "
                    "is dimensionally wrong for that family."
                ),
            }
        )
    if re.search(r"\bWorkplanes\s*\(", code_for_lint):
        hits.append(
            {
                "rule_id": "invalid_build123d_api.workplanes_helper_name",
                "message": (
                    "Build123d does not provide a `Workplanes(...)` helper or context "
                    "manager."
                ),
                "repair_hint": (
                    "Use the target plane directly with `BuildSketch(plane)` or place the "
                    "feature on that face/workplane with `Locations(...)` instead of "
                    "guessing `Workplanes(...)`."
                ),
            }
        )
    if re.search(r"\bSplit\s*\(", code_for_lint):
        hits.append(
            {
                "rule_id": "invalid_build123d_api.split_helper_case",
                "message": (
                    "Build123d uses lowercase `split(...)`; `Split(...)` is a helper-name guess."
                ),
                "repair_hint": (
                    "Use the verified lowercase `split(...)` function after the host solid is fully "
                    "built, and keep split/half extraction outside the active builder lifecycle."
                ),
            }
        )
    if re.search(r"(?<![\w.])hole\s*\(", code_for_lint):
        hits.append(
            {
                "rule_id": "invalid_build123d_api.lowercase_hole_helper_name",
                "message": "Build123d uses capitalized `Hole(...)`, not lowercase `hole(...)`.",
                "repair_hint": (
                    "Rename the helper to `Hole(...)` and keep it on the intended "
                    "face/workplane placement instead of calling lowercase `hole(...)`."
                ),
            }
        )
    if re.search(r"(?<![\w.])cut_extrude\s*\(", code_for_lint):
        hits.append(
            {
                "rule_id": "legacy_api.cut_extrude_helper",
                "message": (
                    "Standalone `cut_extrude(...)` is not a Build123d execute_build123d API surface."
                ),
                "repair_hint": (
                    "Keep the subtractive profile in `BuildSketch(...)` and remove material "
                    "with `extrude(amount=..., mode=Mode.SUBTRACT)`, `Hole(...)`, or an explicit "
                    "solid cutter/boolean on the authoritative host. Do not call a legacy "
                    "`cut_extrude(...)` helper inside execute_build123d."
                ),
            }
        )
    if parsed_tree is None and re.search(r"\bcountersink_radius\s*=", code_for_lint):
        hits.append(
            {
                "rule_id": "invalid_build123d_keyword.countersink_radius_alias",
                "message": (
                    "`CounterSinkHole(...)` uses `counter_sink_radius=...`, not "
                    "`countersink_radius=...`."
                ),
                "repair_hint": (
                    "Rename the keyword to `counter_sink_radius=` when calling "
                    "`CounterSinkHole(...)`."
                ),
            }
        )
    if parsed_tree is None and re.search(r"\bcountersink_angle\s*=", code_for_lint):
        hits.append(
            {
                "rule_id": "invalid_build123d_keyword.countersink_angle_alias",
                "message": (
                    "`CounterSinkHole(...)` uses `counter_sink_angle=...`, not "
                    "`countersink_angle=...`."
                ),
                "repair_hint": (
                    "Rename the keyword to `counter_sink_angle=` when calling "
                    "`CounterSinkHole(...)`."
                ),
            }
        )
    if parsed_tree is None and re.search(
        r"\b(?:countersink_depth|counter_sink_depth)\s*=",
        code_for_lint,
    ):
        hits.append(
            {
                "rule_id": "invalid_build123d_keyword.countersink_depth_alias",
                "message": (
                    "`CounterSinkHole(...)` does not accept a countersink-depth keyword. Keep "
                    "`depth=` for the through-hole depth and describe the countersink with "
                    "`counter_sink_radius=` plus `counter_sink_angle=`."
                ),
                "repair_hint": (
                    "Remove the guessed countersink-depth keyword and keep `depth=` only for the "
                    "through-hole depth when calling `CounterSinkHole(...)`."
                ),
            }
        )
    if parsed_tree is None and re.search(
        r"\bRegularPolygon\s*\([^)]*\b(?:sides|n_sides|num_sides|regular_sides)\s*=",
        code_for_lint,
        flags=re.DOTALL,
    ):
        hits.append(
            {
                "rule_id": "invalid_build123d_keyword.regular_polygon_sides_alias",
                "message": (
                    "`RegularPolygon(...)` uses `side_count=...`, not guessed side-count "
                    "keyword aliases such as `sides=`."
                ),
                "repair_hint": (
                    "Rename the keyword to `side_count=` when calling `RegularPolygon(...)`."
                ),
            }
        )
    if parsed_tree is None and re.search(
        r"\bCone\s*\([^)]*\b(?:upper_radius|lower_radius)\s*=",
        code_for_lint,
        flags=re.DOTALL,
    ):
        hits.append(
            {
                "rule_id": "invalid_build123d_keyword.cone_radius_alias",
                "message": (
                    "`Cone(...)` uses `bottom_radius=` and `top_radius=...`, not "
                    "legacy aliases such as `upper_radius=` or `lower_radius=`."
                ),
                "repair_hint": (
                    "Rename the keywords to `bottom_radius=` / `top_radius=` when calling `Cone(...)`."
                ),
            }
        )
    if re.search(r"(?<![\w.])subtract\s*\(", code_for_lint):
        hits.append(
            {
                "rule_id": "invalid_build123d_api.bare_subtract_helper",
                "message": "Bare subtract(...) is not a supported Build123d API surface.",
                "repair_hint": (
                    "Use an explicit solid boolean such as `result = host.part - cutter` "
                    "or a supported builder-first subtractive mode instead of guessing a "
                    "top-level subtract helper."
                ),
            }
        )
    if re.search(r"(?<![\w.])rotate\s*\(", code_for_lint):
        hits.append(
            {
                "rule_id": "invalid_build123d_api.bare_rotate_helper",
                "message": "Bare rotate(...) is not a supported Build123d API surface.",
                "repair_hint": (
                    "Use Build123d transforms on the shape itself, for example "
                    "`Rot(Y=90) * solid` or `solid.rotate(Axis.Y, 90)`, instead of "
                    "calling a guessed top-level rotate helper."
                ),
            }
        )
    if re.search(r"(?<![\w.])move\s*\(", code_for_lint):
        hits.append(
            {
                "rule_id": "invalid_build123d_api.bare_move_helper",
                "message": "Bare move(...) is not a supported Build123d API surface.",
                "repair_hint": (
                    "Move detached solids with supported transforms such as `Pos(...) * solid`, "
                    "`Location(...)`, or member methods on the shape itself. Do not call a "
                    "guessed top-level `move(...)` helper."
                ),
            }
        )
    if re.search(r"\brevolve\s*\([^)]*\bangle\s*=", code_for_lint, flags=re.DOTALL):
        hits.append(
            {
                "rule_id": "invalid_build123d_keyword.revolve_angle_alias",
                "message": "Build123d `revolve(...)` does not accept an `angle=` keyword.",
                "repair_hint": (
                    "Use the default 360-degree revolve, or pass the supported "
                    "`revolution_arc=` keyword when you need an explicit revolve span."
                ),
            }
        )
    active_builder_match = re.search(
        r"with\s+BuildPart\(\)\s+as\s+(?P<builder>\w+)\s*:",
        code_for_lint,
    )
    if active_builder_match is not None:
        builder_name = str(active_builder_match.group("builder"))
        cutter_boolean_pattern = re.compile(
            rf"""
            ^[ \t]+(?P<cutter>\w+)\s*=\s*(?:Box|Cylinder|Cone|Sphere|Torus)\s*\(
            [\s\S]*?
            ^[ \t]*(?:result\s*=\s*)?{re.escape(builder_name)}\.part\s*-\s*(?P=cutter)\b
            """,
            flags=re.MULTILINE | re.VERBOSE,
        )
        if cutter_boolean_pattern.search(code_for_lint):
            hits.append(
                {
                    "rule_id": "invalid_build123d_contract.active_builder_cutter_primitive_boolean",
                    "message": (
                        "A detached primitive cutter created inside an active `BuildPart` "
                        "is added to the builder immediately, so `builder.part - cutter` "
                        "does not express an isolated host-minus-tool boolean safely."
                    ),
                    "repair_hint": (
                        "Build the host in one `BuildPart`, close it, then create the cutter "
                        "outside the active builder before doing `result = host.part - cutter`, "
                        "or keep the cutter fully builder-native with `mode=Mode.SUBTRACT`."
                    ),
                }
            )
    if re.search(r"\.\s*filter_by_direction\s*\(", code_for_lint):
        hits.append(
            {
                "rule_id": "invalid_build123d_api.shapelist_filter_by_direction",
                "message": (
                    "`ShapeList.filter_by_direction(...)` is not a Build123d API. "
                    "Axis-parallel edge or face selection should use `filter_by(Axis.X/Y/Z)` "
                    "or an explicit Python predicate."
                ),
                "repair_hint": (
                    "Replace `.filter_by_direction(Axis.Y)`-style calls with "
                    "`.filter_by(Axis.Y)` on the relevant ShapeList, and keep any "
                    "position filtering separate with `filter_by_position(...)` when needed."
                ),
            }
        )
    if re.search(r"\.\s*is_parallel\s*\(\s*Axis\.[XYZ]\s*\)", code_for_lint):
        hits.append(
            {
                "rule_id": "invalid_build123d_api.edge_is_parallel_axis",
                "message": (
                    "`Edge.is_parallel(Axis.*)` is not a supported Build123d API surface. "
                    "Axis-parallel selection should use ShapeList `filter_by(Axis.X/Y/Z)` "
                    "or an explicit geometric predicate."
                ),
                "repair_hint": (
                    "Replace list-comprehension tests such as `edge.is_parallel(Axis.Y)` "
                    "with `edges.filter_by(Axis.Y)` on the source ShapeList, or compute an "
                    "explicit vector predicate when you truly need per-edge logic."
                ),
            }
        )
    if parsed_tree is None and re.search(
        r"\.\s*filter_by_position\s*\([^)]*\b(?:[XYZ]Min|[XYZ]Max|[xyz]_[Mm]in|[xyz]_[Mm]ax|[Mm]in_[XYZxyz]|[Mm]ax_[XYZxyz])\s*=",
        code_for_lint,
    ):
        hits.append(
            {
                "rule_id": "invalid_build123d_api.filter_by_position_keyword_band",
                "message": (
                    "`ShapeList.filter_by_position(...)` uses positional `minimum, maximum` "
                    "arguments (plus optional `inclusive=`), not axis-band alias keywords."
                ),
                "repair_hint": (
                    "Keep the axis as the first argument and pass the numeric band as "
                    "positional `minimum, maximum`, for example "
                    "`edges.filter_by_position(Axis.Z, z_min, z_max)`."
                ),
            }
        )
    if parsed_tree is None and re.search(r"\.\s*center\s*\(\s*\)\s*\.\s*[xyz]\b", code_for_lint):
        hits.append(
            {
                "rule_id": "invalid_build123d_api.vector_lowercase_component_attribute",
                "message": (
                    "Build123d vectors/points use uppercase component attributes such as "
                    "`.X`, `.Y`, and `.Z`, not lowercase `.x/.y/.z`."
                ),
                "repair_hint": (
                    "Rename lowercase vector component access such as `.z` to `.Z` "
                    "(or explicitly convert the vector to a tuple first)."
                ),
            }
        )
    if re.search(r"(?<![\w.])MakeFace\s*\(", code_for_lint):
        hits.append(
            {
                "rule_id": "invalid_build123d_api.makeface_helper_case",
                "message": (
                    "`MakeFace()` is not a Build123d helper. Use lowercase `make_face()` "
                    "after closing a `BuildLine` profile, or stay on builder-native sketch primitives."
                ),
                "repair_hint": (
                    "Replace `MakeFace()` with lowercase `make_face()`, keeping it in the "
                    "same sketch/profile context that owns the closed wire."
                ),
            }
        )
    if re.search(r"\bCircle\s*\([^)]*\barc_size\s*=", code_for_lint, flags=re.DOTALL):
        hits.append(
            {
                "rule_id": "invalid_build123d_keyword.circle_arc_size",
                "message": (
                    "`Circle(...)` always creates a full circle in Build123d and does not "
                    "accept an `arc_size=` keyword."
                ),
                "repair_hint": (
                    "Use `CenterArc(...)` or `RadiusArc(...)` inside `BuildLine` when you "
                    "need a semicircle/arc profile, then close the profile and call "
                    "`make_face()` before extruding."
                ),
            }
        )
    if re.search(r"\bCenterArc\s*\([^)]*\barc_angle\s*=", code_for_lint, flags=re.DOTALL):
        hits.append(
            {
                "rule_id": "invalid_build123d_keyword.center_arc_arc_angle_alias",
                "message": (
                    "`CenterArc(...)` uses `arc_size=...`, not `arc_angle=...`."
                ),
                "repair_hint": (
                    "Rename the keyword to `arc_size=` when calling `CenterArc(...)`."
                ),
            }
        )
    if re.search(r"\bCenterArc\s*\([^)]*\bend_angle\s*=", code_for_lint, flags=re.DOTALL):
        hits.append(
            {
                "rule_id": "invalid_build123d_keyword.center_arc_end_angle_alias",
                "message": (
                    "`CenterArc(...)` uses `arc_size=...` for the sweep span, not "
                    "`end_angle=...`."
                ),
                "repair_hint": (
                    "Keep `start_angle=...` for the start direction and replace "
                    "`end_angle=` with `arc_size=` when calling `CenterArc(...)`."
                ),
            }
        )
    if re.search(
        r"\bCenterArc\s*\(",
        code_for_lint,
        flags=re.DOTALL,
    ) and not re.search(
        r"\bCenterArc\s*\([^)]*\bstart_angle\s*=",
        code_for_lint,
        flags=re.DOTALL,
    ) and re.search(
        r"\bCenterArc\s*\([^)]*\barc_size\s*=",
        code_for_lint,
        flags=re.DOTALL,
    ):
        hits.append(
            {
                "rule_id": "invalid_build123d_contract.center_arc_missing_start_angle",
                "message": (
                    "`CenterArc(...)` requires an explicit `start_angle` before the arc "
                    "span and cannot infer it from `arc_size=` alone."
                ),
                "repair_hint": (
                    "Provide `start_angle=...` (or the third positional argument) before "
                    "`arc_size=` when calling `CenterArc(...)`."
                ),
            }
        )
    if re.search(
        r"\bsweep\s*\([^)]*\bpath\s*=\s*[A-Za-z_][A-Za-z0-9_]*\.wire\b(?!\s*\()",
        code_for_lint,
        flags=re.DOTALL,
    ) or re.search(
        r"\bsweep\s*\([^)]*,\s*[A-Za-z_][A-Za-z0-9_]*\.wire\b(?!\s*\()",
        code_for_lint,
        flags=re.DOTALL,
    ):
        hits.append(
            {
                "rule_id": "invalid_build123d_contract.sweep_path_wire_method_reference",
                "message": (
                    "`BuildLine.wire` is a method. Passing `path.wire` into `sweep(...)` "
                    "uses a bound method object instead of the actual wire."
                ),
                "repair_hint": (
                    "Call `path.wire()` when passing the path into `sweep(...)`, or pass "
                    "another real `Wire`/`Edge` object."
                ),
            }
        )
    if re.search(
        r"\bsweep\s*\([^)]*\bpath\s*=\s*[A-Za-z_][A-Za-z0-9_]*\.line\b(?!\s*\()",
        code_for_lint,
        flags=re.DOTALL,
    ) or re.search(
        r"\bsweep\s*\([^)]*,\s*[A-Za-z_][A-Za-z0-9_]*\.line\b(?!\s*\()",
        code_for_lint,
        flags=re.DOTALL,
    ):
        hits.append(
            {
                "rule_id": "invalid_build123d_contract.sweep_path_line_alias",
                "message": (
                    "`BuildLine.line` exposes only one curve member and can silently "
                    "drop the full multi-segment rail that a path sweep requires."
                ),
                "repair_hint": (
                    "Pass `path.wire()` or another real connected `Wire`/`Edge` rail "
                    "into `sweep(...)` instead of `path.line`."
                ),
            }
        )
    if re.search(r"\bsweep\s*\([^)]*\bsection\s*=", code_for_lint, flags=re.DOTALL):
        hits.append(
            {
                "rule_id": "invalid_build123d_keyword.sweep_section_alias",
                "message": (
                    "`sweep(...)` uses `sections=` (plural) or a positional first "
                    "argument, not `section=`."
                ),
                "repair_hint": (
                    "Pass the profile as the first positional argument to `sweep(...)`, "
                    "or rename `section=` to `sections=`."
                ),
            }
        )
    if re.search(
        r"\bsweep\s*\(\s*[A-Za-z_][A-Za-z0-9_]*\.face\b(?!\s*\()",
        code_for_lint,
        flags=re.DOTALL,
    ) or re.search(
        r"\bsweep\s*\([^)]*\b(?:sections|section)\s*=\s*[A-Za-z_][A-Za-z0-9_]*\.face\b(?!\s*\()",
        code_for_lint,
        flags=re.DOTALL,
    ):
        hits.append(
            {
                "rule_id": "invalid_build123d_contract.sweep_profile_face_method_reference",
                "message": (
                    "`BuildSketch.face` is a method. Passing `profile.face` into "
                    "`sweep(...)` uses a bound method object instead of the actual face."
                ),
                "repair_hint": (
                    "Call `profile.face()` when extracting the face, or pass `profile.sketch` "
                    "/ another real face object into `sweep(...)`."
                ),
            }
        )
    if re.search(
        r"^\s*[A-Za-z_][A-Za-z0-9_]*\s*=\s*[A-Za-z_][A-Za-z0-9_]*\.(?:wire|face)\b(?!\s*\()",
        code_for_lint,
        flags=re.MULTILINE,
    ):
        hits.append(
            {
                "rule_id": "invalid_build123d_contract.builder_method_reference_assignment",
                "message": (
                    "Build123d builder accessors such as `.wire` and `.face` are methods. "
                    "Assigning them without `()` stores a bound method object instead of geometry."
                ),
                "repair_hint": (
                    "Call the builder method, for example `path_builder.wire()` or "
                    "`profile_builder.face()`, when capturing that geometry."
                ),
            }
        )
    if re.search(r"\bSemicircle\s*\(", code_for_lint):
        hits.append(
            {
                "rule_id": "invalid_build123d_api.semicircle_helper_name",
                "message": (
                    "`Semicircle(...)` is not a Build123d helper."
                ),
                "repair_hint": (
                    "Use `CenterArc(...)` or `RadiusArc(...)` inside `BuildLine`, close "
                    "the split edge explicitly, and turn the closed wire into a face with "
                    "`make_face()`."
                ),
            }
        )
    if re.search(r"\bRing\s*\(", code_for_lint):
        hits.append(
            {
                "rule_id": "invalid_build123d_api.ring_helper_name",
                "message": "`Ring(...)` is not a Build123d helper.",
                "repair_hint": (
                    "For annular bands or grooves, build the outer coaxial solid/profile "
                    "and subtract the inner coaxial solid/profile instead of guessing "
                    "a `Ring(...)` primitive."
                ),
            }
        )
    if re.search(r"(?<![\w.])shell\s*\(", code_for_lint):
        hits.append(
            {
                "rule_id": "invalid_build123d_api.bare_shell_helper",
                "message": "Bare shell(...) is not a supported Build123d API surface.",
                "repair_hint": (
                    "Use Build123d shell-style operations such as `offset(amount=..., "
                    "openings=...)` on the built host, or subtract an explicit inner "
                    "solid when that is clearer, instead of guessing a top-level "
                    "shell helper."
                ),
            }
        )
    if re.search(r"\boffset\s*\([^)]*\bopening\s*=", code_for_lint, flags=re.DOTALL):
        hits.append(
            {
                "rule_id": "invalid_build123d_keyword.offset_opening_singular",
                "message": "`offset(...)` uses `openings=...`, not a singular `opening=` keyword.",
                "repair_hint": (
                    "Use `offset(amount=..., openings=...)` with the opening face set, or "
                    "subtract an explicit inner solid when that reads more clearly."
                ),
            }
        )
    if re.search(r"\bCylinder\s*\([^)]*\baxis\s*=", code_for_lint, flags=re.DOTALL):
        hits.append(
            {
                "rule_id": "invalid_build123d_keyword.cylinder_axis",
                "message": "Cylinder(...) does not accept an axis= keyword in Build123d.",
                "repair_hint": (
                    "Create the cylinder with `Cylinder(radius=..., height=...)` first. If it "
                    "must point along X or Y, keep that cylinder detached, orient it with "
                    "`Rot(...)`, and only then place/add/subtract it with `Pos(...)` or "
                    "`Locations(...)`; do not create a cylinder inside an active `BuildPart` "
                    "and then try `solid = Rot(...) * solid` or `solid = Pos(...) * solid`."
                ),
            }
        )
    if re.search(r"\bCylinder\s*\([^)]*\btaper\s*=", code_for_lint, flags=re.DOTALL):
        hits.append(
            {
                "rule_id": "invalid_build123d_keyword.cylinder_taper",
                "message": "Cylinder(...) does not accept a taper= keyword in Build123d.",
                "repair_hint": (
                    "Use `Cone(bottom_radius=..., top_radius=..., height=...)` for a tapered "
                    "countersink/cone, or a plain `Cylinder(radius=..., height=...)` when the "
                    "sidewall should stay parallel."
                ),
            }
        )
    if re.search(r"\bCylinder\s*\([^)]*\blength\s*=", code_for_lint, flags=re.DOTALL):
        hits.append(
            {
                "rule_id": "invalid_build123d_keyword.cylinder_length_alias",
                "message": "Cylinder(...) does not accept a length= keyword in Build123d.",
                "repair_hint": (
                    "Use `Cylinder(radius=..., height=...)` or the positional "
                    "`Cylinder(radius, height)` signature, keep the cylinder detached, and then "
                    "orient it with `Rot(...)` when the cylinder axis is not the default Z axis "
                    "instead of creating it inside an active builder and rebinding the temporary "
                    "primitive value."
                ),
            }
        )
    if re.search(r"\bBox\s*\([^)]*\bdepth\s*=", code_for_lint, flags=re.DOTALL):
        hits.append(
            {
                "rule_id": "invalid_build123d_keyword.box_depth_alias",
                "message": "Box(...) does not accept a depth= keyword in Build123d.",
                "repair_hint": (
                    "Use `Box(length=..., width=..., height=...)` or the positional "
                    "`Box(length, width, height)` signature. If your variable is named "
                    "`depth`, pass it as the second width argument instead of using "
                    "a `depth=` keyword."
                ),
            }
        )
    if re.search(r"\bBox\s*\([^)]*\bradius\s*=", code_for_lint, flags=re.DOTALL):
        hits.append(
            {
                "rule_id": "invalid_build123d_keyword.box_radius_alias",
                "message": "Box(...) does not accept a radius= keyword in Build123d.",
                "repair_hint": (
                    "Call `Box(length=..., width=..., height=...)` for the host prism, then "
                    "round it explicitly with edge fillets, or sketch a "
                    "`RectangleRounded(...)` profile and `extrude(...)` it when the rounded "
                    "corner radius is part of the primary section definition."
                ),
            }
        )
    if re.search(r"\bextrude\s*\([^)]*\bdirection\s*=", code_for_lint, flags=re.DOTALL):
        hits.append(
            {
                "rule_id": "invalid_build123d_keyword.extrude_direction_alias",
                "message": "extrude(...) does not accept a direction= keyword in Build123d.",
                "repair_hint": (
                    "Use `extrude(amount=...)` from the correct sketch plane, or the supported "
                    "`dir=` keyword when you truly need a non-default direction."
                ),
            }
        )
    if re.search(r"\bPos\s*\([^)]*\b(?:x|y|z)\s*=", code_for_lint, flags=re.DOTALL):
        hits.append(
            {
                "rule_id": "invalid_build123d_keyword.pos_lowercase_axis_keyword",
                "message": (
                    "Pos(...) does not accept lowercase axis keywords such as `x=` / `y=` / `z=`."
                ),
                "repair_hint": (
                    "Use positional placement such as `Pos(x, y, z)` or another supported "
                    "Build123d transform form instead of lowercase keyword arguments."
                ),
            }
        )
    if re.search(r"\bLoc\s*\(", code_for_lint):
        hits.append(
            {
                "rule_id": "invalid_build123d_api.loc_helper_name",
                "message": "Build123d does not expose a `Loc(...)` helper alias.",
                "repair_hint": (
                    "Use `Location(...)` for an explicit location object, or use `Pos(...)` / "
                    "`Rot(...)` when you only need a translation or rotation transform."
                ),
            }
        )
    if re.search(r"\bScale\b\s*(?:\.by\s*\(|\()", code_for_lint):
        hits.append(
            {
                "rule_id": "invalid_build123d_api.scale_helper_case",
                "message": "Build123d exposes lowercase `scale(...)`, not a capitalized `Scale(...)` helper.",
                "repair_hint": (
                    "Use lowercase `scale(detached_shape, by=(sx, sy, sz))` or another supported "
                    "transform flow instead of inventing `Scale.by(...)` / `Scale(...)`."
                ),
            }
        )
    if re.search(
        r"\bPlane\.(?:XY|XZ|YZ)\s*\*\s*\([^)]*,[^)]*\)",
        code_for_lint,
        flags=re.DOTALL,
    ):
        hits.append(
            {
                "rule_id": "invalid_build123d_contract.plane_tuple_multiplication",
                "message": (
                    "A Build123d Plane cannot be relocated by multiplying it with a raw coordinate "
                    "tuple such as `Plane.XY * (x, y, z)`."
                ),
                "repair_hint": (
                    "Use `Locations((x, y, z))` when you need a point placement, or build an "
                    "actual translated plane with `Plane.XY.offset(z)` / `Plane.XZ.offset(y)` / "
                    "`Plane.YZ.offset(x)` when the feature should be sketched on a shifted workplane."
                ),
            }
        )
    if (
        _requirement_mentions_plane_anchored_positive_extrude(requirement_lower)
        and re.search(r"\bBox\s*\(", code_for_lint)
        and not re.search(
            r"\bBuildSketch\s*\(\s*Plane\.(?:XY|XZ|YZ)\b",
            code_for_lint,
        )
        and not re.search(r"\bBox\s*\([^)]*\balign\s*=", code_for_lint, flags=re.DOTALL)
    ):
        hits.append(
            {
                "rule_id": "invalid_build123d_contract.centered_box_breaks_plane_anchored_positive_extrude",
                "message": (
                    "This requirement explicitly says to sketch on a named plane and extrude "
                    "positively, so a default centered Box(...) silently breaks the plane-anchored "
                    "span contract."
                ),
                "repair_hint": (
                    "Use `with BuildSketch(Plane.XY/XZ/YZ): ...` plus `extrude(amount=...)`, or "
                    "make any primitive-solid equivalent explicit with non-centered alignment/placement "
                    "that preserves the named plane as the lower bound."
                ),
            }
        )
    if re.search(r"\bRectangle\s*\([^)]*\bcentered\s*=", code_for_lint, flags=re.DOTALL):
        hits.append(
            {
                "rule_id": "invalid_build123d_keyword.rectangle_centered",
                "message": "Rectangle(...) does not accept centered= in Build123d.",
                "repair_hint": (
                    "Rectangle is centered by default. Use `align=...` only when you need a "
                    "non-default placement contract."
                ),
            }
        )
    if re.search(
        r"\bEllipse\s*\([^)]*\bmajor_radius\s*=",
        code_for_lint,
        flags=re.DOTALL,
    ):
        hits.append(
            {
                "rule_id": "invalid_build123d_keyword.ellipse_major_radius_alias",
                "message": (
                    "Ellipse(...) uses `x_radius=...`, not `major_radius=...`, in Build123d."
                ),
                "repair_hint": (
                    "Use `Ellipse(x_radius=..., y_radius=...)`, or pass the two ellipse "
                    "radii positionally as `Ellipse(x_radius, y_radius)`."
                ),
            }
        )
    if re.search(
        r"\bEllipse\s*\([^)]*\bminor_radius\s*=",
        code_for_lint,
        flags=re.DOTALL,
    ):
        hits.append(
            {
                "rule_id": "invalid_build123d_keyword.ellipse_minor_radius_alias",
                "message": (
                    "Ellipse(...) uses `y_radius=...`, not `minor_radius=...`, in Build123d."
                ),
                "repair_hint": (
                    "Use `Ellipse(x_radius=..., y_radius=...)`, or pass the two ellipse "
                    "radii positionally as `Ellipse(x_radius, y_radius)`."
                ),
            }
        )
    if re.search(r"\bRectangle\s*\([^)]*\blength\s*=", code_for_lint, flags=re.DOTALL):
        hits.append(
            {
                "rule_id": "invalid_build123d_keyword.rectangle_length_alias",
                "message": "Rectangle(...) uses `height=...`, not `length=...`, in Build123d.",
                "repair_hint": (
                    "Use `Rectangle(width=..., height=...)`, or pass the two centered sketch spans "
                    "positionally as `Rectangle(width, height)`."
                ),
            }
        )
    if re.search(
        r"\b(?P<builder>\w+)\.solid\s*=\s*(?P=builder)\.solid\s*[-+*/]",
        code_for_lint,
    ):
        hits.append(
            {
                "rule_id": "invalid_build123d_api.buildpart_solid_method_arithmetic",
                "message": (
                    "BuildPart.solid is not a mutable arithmetic surface; using it like "
                    "`part.solid = part.solid - cutter` usually treats `solid` as a method object."
                ),
                "repair_hint": (
                    "Keep the host in `with BuildPart() as part:` and use builder subtractive "
                    "modes such as `Sphere(..., mode=Mode.SUBTRACT)` / `Cylinder(..., mode=Mode.SUBTRACT)` "
                    "inside `Locations(...)`, or subtract an explicit cutter from `part.part` after the builder."
                ),
            }
        )
    if not re.search(r"(?m)^\s*(result|part)\s*=", code_for_lint):
        hits.append(
            {
                "rule_id": "missing_final_assignment.result_or_part",
                "message": "execute_build123d requires an explicit final part/result assignment.",
                "repair_hint": (
                    "Assign the final geometry explicitly, for example `result = part.part` "
                    "for BuildPart or `result = final_solid` for a direct solid result."
                ),
            }
        )

    if not hits:
        return None

    family_ids = _candidate_lint_family_ids(
        requirement_text=requirement_text,
        run_state=run_state,
    )
    repair_recipe = _build_preflight_repair_recipe(
        family_ids=family_ids,
        lint_hits=hits,
        requirement_text=requirement_text,
    )
    hits = [
        _attach_api_governance_metadata(
            hit,
            repair_recipe=repair_recipe,
        )
        for hit in hits
        if isinstance(hit, dict)
    ]
    failure_kind = _preflight_lint_failure_kind(hits)
    summary = (
        "Preflight lint rejected unsupported legacy modeling-kernel usage, known-invalid "
        "Build123d helper/keyword/context surfaces, risky nested BuildPart cutter "
        "arithmetic, or a missing final result assignment before sandbox execution."
    )
    stderr_lines = [summary]
    stderr_lines.extend(
        f"- {item['rule_id']}: {item['message']}"
        for item in hits
        if isinstance(item, dict)
    )
    if repair_recipe:
        recipe_summary = str(repair_recipe.get("recipe_summary") or "").strip()
        if recipe_summary:
            stderr_lines.append(f"- repair_recipe: {recipe_summary}")

    return {
        "success": False,
        "stdout": "",
        "stderr": "\n".join(stderr_lines),
        "output_files": [],
        "output_file_contents": {},
        "error_message": "execute_build123d preflight lint failed",
        "evaluation": {
            "mode": "none",
            "status": "not_requested",
            "summary": "Evaluation not requested",
            "details": {},
        },
        "session_id": session_id,
        "step": None,
        "step_file": None,
        "snapshot": None,
        "session_state_persisted": False,
        "failure_kind": failure_kind,
        "summary": summary,
        "lint_hits": hits,
        "candidate_family_ids": family_ids,
        "repair_recipe": repair_recipe,
    }


def _attach_api_governance_metadata(
    hit: dict[str, Any],
    *,
    repair_recipe: dict[str, Any] | None,
) -> dict[str, Any]:
    enriched = dict(hit)
    rule_id = str(enriched.get("rule_id") or "").strip()
    recipe_id = (
        str(repair_recipe.get("recipe_id") or "").strip()
        if isinstance(repair_recipe, dict)
        else ""
    )
    repair_family = (
        str(repair_recipe.get("repair_family") or "").strip()
        if isinstance(repair_recipe, dict)
        else ""
    )
    category = "invalid_api_contract"
    if rule_id.startswith("python_syntax."):
        category = "python_syntax"
    elif rule_id.startswith("legacy_kernel."):
        category = "legacy_kernel_surface"
    elif ".keyword." in rule_id:
        category = "invalid_keyword"
    elif ".contract." in rule_id:
        category = "builder_contract"
    elif ".api." in rule_id:
        category = "invalid_helper"
    enriched.update(
        {
            "lint_id": rule_id,
            "layer": "write_surface",
            "category": category,
            "severity": "fatal",
            "matcher": rule_id,
            "repair_family": repair_family or None,
            "recommended_recipe_id": recipe_id or None,
            "hallucination_weight": 1.0,
            "example_artifact_kind": "preflight_lint",
        }
    )
    return enriched


__all__ = [
    "_preflight_gate_apply_cad_action",
    "_preflight_lint_execute_build123d",
]
