from __future__ import annotations

from typing import Any

from sub_agent_runtime.compact import compact_jsonish
from sub_agent_runtime.turn_state import RunState, ToolCategory


def classify_write_failure(
    *,
    tool_name: str,
    error_text: str | None,
    stderr_text: str | None,
) -> str | None:
    lowered = "\n".join(
        part.strip().lower()
        for part in (tool_name, error_text or "", stderr_text or "")
        if isinstance(part, str) and part.strip()
    )
    if not lowered:
        return None
    normalized_tool = tool_name.strip().lower()
    if normalized_tool == "execute_build123d":
        if (
            "execute_build123d_python_syntax_failure" in lowered
            or "syntaxerror" in lowered
            or "indentationerror" in lowered
            or "unterminated string literal" in lowered
        ):
            return "execute_build123d_python_syntax_failure"
        if (
            "execute_build123d preflight lint failed" in lowered
            or "execute_build123d_api_lint_failure" in lowered
        ):
            return "execute_build123d_api_lint_failure"
        if (
            "typeerror" in lowered
            and "unexpected keyword argument" in lowered
            and any(
                token in lowered
                for token in (
                    "rectangle.__init__",
                    "circle.__init__",
                    "box.__init__",
                    "cylinder.__init__",
                    "extrude(",
                )
            )
        ):
            return "execute_build123d_api_lint_failure"
        if "timeout" in lowered:
            return "execute_build123d_timeout"
        if "cannot find a solid on the stack or in the parent chain" in lowered:
            return "execute_build123d_chain_context_failure"
        if (
            "solid.sweep() missing 1 required positional argument: 'path'" in lowered
            or ("dispatcherror" in lowered and "sweep" in lowered and "0 methods found" in lowered)
        ):
            return "execute_build123d_sweep_profile_recipe_failure"
        if (
            "attributeerror" in lowered
            and "face" in lowered
            and "has no attribute 'sweep'" in lowered
        ):
            return "execute_build123d_sweep_profile_recipe_failure"
        if (
            "unexpected keyword argument 'startangle'" in lowered
            and "makecircle" in lowered
        ):
            return "execute_build123d_curve_api_failure"
        if (
            "gc_makearcofcircle::value() - no result" in lowered
            and "makethreepointarc" in lowered
        ):
            return "execute_build123d_curve_api_failure"
        if "gp_vec::normalized() - vector has zero norm" in lowered and "plane" in lowered:
            return "execute_build123d_sweep_profile_recipe_failure"
        if (
            "disconnectedwire" in lowered
            or "brepbuilderapi_disconnectedwire" in lowered
            or ("stdfail_notdone" in lowered and "assembleedges" in lowered and "wire" in lowered)
        ):
            return "execute_build123d_sweep_profile_recipe_failure"
        if "no pending wires present" in lowered:
            return "execute_build123d_sweep_profile_recipe_failure"
        if (
            "attributeerror" in lowered
            and "workplane" in lowered
            and "has no attribute 'wrapped'" in lowered
        ):
            return "execute_build123d_boolean_shape_api_failure"
        if (
            "typeerror" in lowered
            and "unsupported operand type" in lowered
            and any(
                token in lowered
                for token in ("'method' and 'cylinder'", "'method' and 'part'", "'method' and 'solid'")
            )
        ):
            return "execute_build123d_boolean_shape_api_failure"
        if (
            "attributeerror" in lowered
            and "workplane" in lowered
            and "has no attribute" in lowered
            and any(token in lowered for token in ("selectnth", ".first()", ".last()", " end()"))
        ):
            return "execute_build123d_selector_api_failure"
        if (
            "parseexception" in lowered
            and "found 'and'" in lowered
            and ".edges(" in lowered
        ):
            return "execute_build123d_selector_api_failure"
        if "more than one wire is required" in lowered:
            return "execute_build123d_loft_wire_recipe_failure"
        if "fillets requires that edges be selected" in lowered:
            return "execute_build123d_selector_failure"
        if "chamfer" in lowered and "edges be selected" in lowered:
            return "execute_build123d_selector_failure"
        if "no suitable edges for chamfer or fillet" in lowered:
            return "execute_build123d_selector_failure"
        if (
            ("fillet" in lowered or "chamfer" in lowered)
            and "stdfail_notdone" in lowered
            and "command not done" in lowered
        ):
            return "execute_build123d_selector_failure"
        if "nothing to subtract from" in lowered:
            return "execute_build123d_detached_subtractive_builder_failure"
    return None


RETAINABLE_EXECUTE_BUILD123D_FAILURE_KINDS = {
    "execute_build123d_detached_subtractive_builder_failure",
    "execute_build123d_python_syntax_failure",
    "execute_build123d_chain_context_failure",
    "execute_build123d_curve_api_failure",
    "execute_build123d_sweep_profile_recipe_failure",
}


def failure_recovery_bias(failure_kind: str) -> str:
    recovery_bias_map = {
        "execute_build123d_api_lint_failure": "repair_api_usage_before_retry",
        "execute_build123d_python_syntax_failure": "repair_python_syntax_before_retry",
        "execute_build123d_timeout": "avoid_repeating_large_whole_part_code_retry",
        "execute_build123d_chain_context_failure": "repair_or_simplify_modeling_chain_before_retry",
        "execute_build123d_curve_api_failure": "repair_curve_api_usage_before_retry",
        "execute_build123d_sweep_profile_recipe_failure": "repair_sweep_profile_recipe_before_retry",
        "execute_build123d_boolean_shape_api_failure": "unwrap_workplane_shapes_before_boolean_retry",
        "execute_build123d_loft_wire_recipe_failure": "repair_loft_wire_recipe_before_retry",
        "execute_build123d_selector_api_failure": "repair_selector_api_usage_before_retry",
        "execute_build123d_selector_failure": "separate_local_edge_finish_from_whole_part_code_retry",
        "execute_build123d_detached_subtractive_builder_failure": "repair_detached_subtractive_builder_before_retry",
    }
    return recovery_bias_map.get(failure_kind, "repair_before_retry")


def failure_recommended_next_steps(failure_kind: str) -> list[str]:
    if failure_kind == "execute_build123d_python_syntax_failure":
        return [
            "Repair the Python syntax or indentation exactly before changing the modeling recipe again.",
            "Keep the next execute_build123d retry materially identical in geometry intent; this failure is about script validity, not CAD semantics.",
            "Prefer a shorter script with stable indentation and fewer comments/diagnostic strings until the script executes cleanly.",
        ]
    if failure_kind == "execute_build123d_api_lint_failure":
        return [
            "Use the lint hits directly; do not retry the same unsupported legacy API surface inside execute_build123d.",
            "For Build123d primitives and sketches, stay literal about supported constructor signatures: Rectangle is centered by default, and Cylinder does not accept axis=.",
            "For explicit boolean cuts, orient and place the cutter with Rot(...) and Pos(...), then use an explicit solid boolean such as `result = host.part - cutter` instead of bare subtract()/rotate() helper guesses.",
            "For countersunk hole arrays, prefer BuildSketch plus Locations on the target Plane and subtract explicit hole/countersink cutters from the host part.",
        ]
    if failure_kind == "execute_build123d_timeout":
        return [
            "Do not immediately retry another large end-to-end code block.",
            "Prefer a smaller subtree or staged code rebuild before another giant whole-part retry.",
        ]
    if failure_kind == "execute_build123d_chain_context_failure":
        return [
            "Repair the solid/workplane chain first instead of repeating the same whole-part script shape.",
            "If the requirement is mostly base-solid plus local face edits, rebuild the host solid cleanly in code first and only use local finishing once stable anchors exist.",
        ]
    if failure_kind == "execute_build123d_curve_api_failure":
        return [
            "Repair the concrete curve-construction API usage before another broad retry.",
            "For path sweeps, replace unsupported arc helpers or keyword arguments with a supported BuildLine/Edge/Wire recipe and keep the next turn on execute_build123d repair.",
        ]
    if failure_kind == "execute_build123d_sweep_profile_recipe_failure":
        return [
            "Repair the sweep profile recipe itself before another broad retry; do not keep bouncing into probe-only turns when the failure already points to an invalid rail/profile/frame recipe.",
            "Build the rail with BuildLine, the annular section with BuildSketch on the endpoint Plane, and sweep the resulting face/section along the connected path.",
        ]
    if failure_kind == "execute_build123d_boolean_shape_api_failure":
        return [
            "Unwrap builder/context-backed solids before boolean operations; cut/fuse/intersect actual solids rather than passing a builder context or legacy Workplane wrapper.",
            "After the boolean, return to BuildPart or explicit solid variables only if the next operation still needs more modeled geometry.",
        ]
    if failure_kind == "execute_build123d_loft_wire_recipe_failure":
        return [
            "Repair the loft recipe itself before another broad retry; keep the section wires explicit on parallel planes before lofting the solid.",
            "If the requirement is regular-polygon frustum plus boolean, keep the loft inputs as explicit profile wires on parallel planes and only then intersect the resulting solid with the other shape.",
        ]
    if failure_kind == "execute_build123d_selector_api_failure":
        return [
            "Do not retry the same unsupported legacy selector API call.",
            "Repair the code-path edge selection with supported Build123d/OCP operations, or stop at the pre-fillet solid and finish locally once authoritative refs exist.",
        ]
    if failure_kind == "execute_build123d_selector_failure":
        return [
            "Do not keep retrying selector-based whole-part fillet/chamfer code blindly.",
            "Either rebuild only up to the pre-fillet solid and finish locally with query_topology plus apply_cad_action, or use a more reliable explicit edge-targeting strategy once authoritative refs exist.",
        ]
    if failure_kind == "execute_build123d_detached_subtractive_builder_failure":
        return [
            "Treat `Nothing to subtract from` as a detached subtractive builder error: a subtractive primitive or subtractive extrude was opened before an additive host existed in that builder.",
            "If the cut belongs to the current host, keep the subtraction inside the authoritative host builder after the host solid already exists.",
            "If the cut needs a detached cutter, build the cutter as a positive solid first, close that builder, and only then subtract it explicitly from the host solid.",
        ]
    return ["Repair the concrete failure before another broad retry."]


def failure_recommended_next_tools(failure_kind: str) -> list[str]:
    if failure_kind == "execute_build123d_python_syntax_failure":
        return ["execute_build123d", "query_kernel_state"]
    if failure_kind == "execute_build123d_api_lint_failure":
        return ["execute_build123d", "query_kernel_state"]
    if failure_kind == "execute_build123d_timeout":
        return ["query_feature_probes", "execute_build123d_probe"]
    if failure_kind == "execute_build123d_chain_context_failure":
        return ["execute_build123d", "query_kernel_state"]
    if failure_kind == "execute_build123d_curve_api_failure":
        return ["execute_build123d", "query_kernel_state"]
    if failure_kind == "execute_build123d_sweep_profile_recipe_failure":
        return ["execute_build123d", "query_kernel_state"]
    if failure_kind == "execute_build123d_boolean_shape_api_failure":
        return ["execute_build123d", "execute_build123d_probe"]
    if failure_kind == "execute_build123d_loft_wire_recipe_failure":
        return ["execute_build123d", "execute_build123d_probe"]
    if failure_kind == "execute_build123d_selector_api_failure":
        return ["execute_build123d", "execute_build123d_probe"]
    if failure_kind == "execute_build123d_selector_failure":
        return ["execute_build123d", "query_feature_probes"]
    if failure_kind == "execute_build123d_detached_subtractive_builder_failure":
        return ["execute_build123d", "query_kernel_state"]
    return []


def summarize_failure_lint_hits(lint_hits: Any) -> list[dict[str, Any]] | None:
    if not isinstance(lint_hits, list) or not lint_hits:
        return None
    summarized: list[dict[str, Any]] = []
    occurrence_counts: dict[str, int] = {}
    for item in lint_hits:
        if not isinstance(item, dict):
            continue
        payload: dict[str, Any] = {}
        for key in (
            "rule_id",
            "message",
            "repair_hint",
            "layer",
            "category",
            "severity",
            "recommended_recipe_id",
        ):
            value = item.get(key)
            if isinstance(value, str) and value.strip():
                text = value.strip()
                payload[key] = text if len(text) <= 240 else text[:240] + "..."
        if not payload:
            continue
        dedupe_key = payload.get("rule_id")
        if isinstance(dedupe_key, str) and dedupe_key in occurrence_counts:
            occurrence_counts[dedupe_key] += 1
            continue
        if len(summarized) >= 4:
            continue
        if isinstance(dedupe_key, str):
            occurrence_counts[dedupe_key] = 1
        summarized.append(payload)
    for payload in summarized:
        rule_id = payload.get("rule_id")
        if not isinstance(rule_id, str):
            continue
        count = occurrence_counts.get(rule_id, 0)
        if count > 1:
            payload["occurrence_count"] = count
    return summarized or None


def summarize_failure_repair_recipe(repair_recipe: Any) -> dict[str, Any] | None:
    if not isinstance(repair_recipe, dict) or not repair_recipe:
        return None
    summary: dict[str, Any] = {}
    for key in ("recipe_id", "repair_family", "recipe_summary"):
        value = repair_recipe.get(key)
        if isinstance(value, str) and value.strip():
            text = value.strip()
            summary[key] = text if len(text) <= 480 else text[:480] + "..."
    recipe_skeleton = repair_recipe.get("recipe_skeleton")
    if isinstance(recipe_skeleton, dict) and recipe_skeleton:
        skeleton_summary = compact_jsonish(
            recipe_skeleton,
            max_depth=4,
            max_items=12,
            max_string_chars=320,
        )
        if isinstance(skeleton_summary, dict):
            steps = recipe_skeleton.get("steps")
            if isinstance(steps, list) and steps:
                skeleton_summary["steps"] = [
                    item if not isinstance(item, str) or len(item) <= 320 else item[:320] + "..."
                    for item in steps[:8]
                ]
            summary["recipe_skeleton"] = skeleton_summary
    return summary or None


def build_previous_tool_failure_summary(run_state: RunState) -> dict[str, Any] | None:
    latest_write_turn = run_state.latest_write_turn
    if latest_write_turn is None:
        return None
    failed_result = next(
        (
            result
            for result in latest_write_turn.tool_results
            if result.category == ToolCategory.WRITE
            and (not result.success or bool(result.error))
        ),
        None,
    )
    if failed_result is None and not latest_write_turn.error:
        return None
    payload = (
        run_state.latest_write_payload
        if isinstance(run_state.latest_write_payload, dict)
        else {}
    )
    stderr_text = ""
    stderr_value = payload.get("stderr")
    if isinstance(stderr_value, str) and stderr_value.strip():
        stderr_text = stderr_value.strip()
    error_text = None
    if failed_result is not None and isinstance(failed_result.error, str) and failed_result.error.strip():
        error_text = failed_result.error.strip()
    elif isinstance(latest_write_turn.error, str) and latest_write_turn.error.strip():
        error_text = latest_write_turn.error.strip()
    if not error_text and isinstance(payload.get("error_message"), str):
        error_text = str(payload.get("error_message")).strip() or None
    summary = {
        "round": latest_write_turn.round_no,
        "tool": latest_write_turn.write_tool_name,
        "error": error_text,
        "consecutive_write_failure_count": _count_recent_write_failures(run_state),
        "same_tool_failure_count": _count_recent_write_failures(
            run_state,
            tool_name=str(latest_write_turn.write_tool_name or "").strip() or None,
        ),
    }
    recent_failure_kinds = _recent_write_failure_kinds(
        run_state,
        tool_name=str(latest_write_turn.write_tool_name or "").strip() or None,
    )
    if recent_failure_kinds:
        summary["recent_failure_kinds"] = recent_failure_kinds
    payload_failure_kind = (
        str(payload.get("failure_kind")).strip()
        if isinstance(payload.get("failure_kind"), str)
        and str(payload.get("failure_kind")).strip()
        else None
    )
    failure_kind = payload_failure_kind or classify_write_failure(
        tool_name=str(latest_write_turn.write_tool_name or "").strip(),
        error_text=error_text,
        stderr_text=stderr_text,
    )
    if failure_kind is not None:
        summary["failure_kind"] = failure_kind
    effective_failure_kind = failure_kind
    if failure_kind == "execute_build123d_timeout" and recent_failure_kinds:
        retained_actionable_failure_kind = next(
            (
                kind
                for kind in recent_failure_kinds
                if kind in RETAINABLE_EXECUTE_BUILD123D_FAILURE_KINDS
            ),
            None,
        )
        if retained_actionable_failure_kind is not None:
            summary["retained_actionable_failure_kind"] = retained_actionable_failure_kind
            effective_failure_kind = retained_actionable_failure_kind
    if effective_failure_kind is not None:
        summary["effective_failure_kind"] = effective_failure_kind
        summary["recovery_bias"] = failure_recovery_bias(effective_failure_kind)
        summary["recommended_next_steps"] = failure_recommended_next_steps(
            effective_failure_kind
        )
        summary["recommended_next_tools"] = failure_recommended_next_tools(
            effective_failure_kind
        )
    lint_hits = summarize_failure_lint_hits(payload.get("lint_hits"))
    if lint_hits:
        summary["lint_hits"] = lint_hits
    repair_recipe = summarize_failure_repair_recipe(payload.get("repair_recipe"))
    if repair_recipe:
        summary["repair_recipe"] = repair_recipe
    if stderr_text:
        summary["stderr_excerpt"] = stderr_text[:400]
    artifact_files = payload.get("output_files")
    if isinstance(artifact_files, list) and artifact_files:
        summary["output_files"] = [
            item for item in artifact_files[:6] if isinstance(item, str)
        ]
    return summary


def _count_recent_write_failures(
    run_state: RunState,
    *,
    tool_name: str | None = None,
) -> int:
    count = 0
    normalized_tool = str(tool_name or "").strip().lower()
    for turn in reversed(run_state.turns):
        if turn.write_tool_name is None:
            continue
        failed_result = next(
            (
                result
                for result in turn.tool_results
                if result.category == ToolCategory.WRITE
                and (not result.success or bool(result.error))
            ),
            None,
        )
        if failed_result is None and not turn.error:
            break
        turn_tool = str(turn.write_tool_name or "").strip().lower()
        if normalized_tool and turn_tool != normalized_tool:
            break
        count += 1
    return count


def _recent_write_failure_kinds(
    run_state: RunState,
    *,
    tool_name: str | None = None,
    max_items: int = 4,
) -> list[str]:
    kinds: list[str] = []
    normalized_tool = str(tool_name or "").strip().lower()
    for turn in reversed(run_state.turns):
        if turn.write_tool_name is None:
            continue
        failed_result = next(
            (
                result
                for result in turn.tool_results
                if result.category == ToolCategory.WRITE
                and (not result.success or bool(result.error))
            ),
            None,
        )
        if failed_result is None and not turn.error:
            break
        turn_tool = str(turn.write_tool_name or "").strip().lower()
        if normalized_tool and turn_tool != normalized_tool:
            break
        payload = failed_result.payload if failed_result is not None else {}
        stderr_text = (
            str(payload.get("stderr")).strip()
            if isinstance(payload, dict)
            and isinstance(payload.get("stderr"), str)
            and str(payload.get("stderr")).strip()
            else None
        )
        error_text = None
        if failed_result is not None and isinstance(failed_result.error, str) and failed_result.error.strip():
            error_text = failed_result.error.strip()
        elif isinstance(turn.error, str) and turn.error.strip():
            error_text = turn.error.strip()
        elif isinstance(payload, dict) and isinstance(payload.get("error_message"), str):
            error_text = str(payload.get("error_message")).strip() or None
        payload_failure_kind = (
            str(payload.get("failure_kind")).strip()
            if isinstance(payload, dict)
            and isinstance(payload.get("failure_kind"), str)
            and str(payload.get("failure_kind")).strip()
            else None
        )
        failure_kind = payload_failure_kind or classify_write_failure(
            tool_name=str(turn.write_tool_name or "").strip(),
            error_text=error_text,
            stderr_text=stderr_text,
        )
        if failure_kind:
            kinds.append(failure_kind)
            if len(kinds) >= max_items:
                break
    return kinds


def is_pre_solid_action_type(action_type: str | None) -> bool:
    if not isinstance(action_type, str):
        return False
    return action_type.strip().lower() in {
        "create_sketch",
        "add_circle",
        "add_rectangle",
        "add_polygon",
        "add_slot",
        "add_ellipse",
        "add_path",
    }
