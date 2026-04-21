from __future__ import annotations

from typing import Any

from sub_agent_runtime.turn_state import RunState

_APPLY_CAD_ACTION_TOP_LEVEL_FIELDS = {
    "action_type",
    "action_params",
    "params",
    "session_id",
    "timeout_seconds",
    "include_artifact_content",
    "clear_session",
}


def normalize_model_facing_apply_cad_action_arguments(
    arguments: dict[str, Any] | None,
) -> dict[str, Any]:
    """Normalize model-facing apply_cad_action calls to the canonical nested shape.

    The sandbox contract remains strict (`action_type` + `action_params`), but live
    model traces still occasionally flatten geometry fields such as `face_ref`,
    `edge_refs`, or `diameter` at the top level. Accept that legacy/model-drift
    surface here and fold it back into `action_params` before schema validation.
    """

    merged = dict(arguments or {})
    nested_params = (
        dict(merged.get("action_params"))
        if isinstance(merged.get("action_params"), dict)
        else {}
    )

    alias_params = merged.pop("params", None)
    if isinstance(alias_params, dict):
        for key, value in alias_params.items():
            nested_params.setdefault(str(key), value)

    for key in list(merged.keys()):
        if key in _APPLY_CAD_ACTION_TOP_LEVEL_FIELDS:
            continue
        nested_params.setdefault(key, merged.pop(key))

    if nested_params or "action_params" in merged or isinstance(alias_params, dict):
        merged["action_params"] = nested_params

    return merged


def preflight_gate_apply_cad_action(
    *,
    action_type: str,
    action_params: dict[str, Any],
    run_state: RunState | None,
) -> dict[str, Any] | None:
    def _normalize_topology_candidate_token(value: Any) -> str | None:
        if not isinstance(value, str):
            return None
        token = value.strip()
        if not token or token.startswith(("face:", "edge:")):
            return None
        lowered = token.lower()
        if lowered.startswith("candidate:"):
            lowered = lowered.split(":", 1)[1].strip()
        normalized = lowered.replace("-", "_").replace(" ", "_")
        return normalized or None

    normalized_action = str(action_type or "").strip().lower()
    latest_turn_policy = (
        run_state.turn_tool_policies[-1]
        if isinstance(run_state, RunState) and run_state.turn_tool_policies
        else None
    )
    topology_payload = (
        run_state.evidence.latest_by_tool.get("query_topology")
        if isinstance(run_state, RunState)
        else None
    )
    query_sketch_payload = (
        run_state.evidence.latest_by_tool.get("query_sketch")
        if isinstance(run_state, RunState)
        else None
    )
    sketch_state = (
        query_sketch_payload.get("sketch_state")
        if isinstance(query_sketch_payload, dict)
        else None
    )
    latest_action_type = None
    if isinstance(run_state, RunState) and isinstance(run_state.action_history, list):
        for item in reversed(run_state.action_history):
            if not isinstance(item, dict):
                continue
            candidate = str(item.get("action_type") or "").strip().lower()
            if candidate:
                latest_action_type = candidate
                break
    if (
        latest_turn_policy is not None
        and latest_turn_policy.mode == "local_finish"
        and normalized_action
        in {"rollback", "clear_session", "get_history", "modify_action", "snapshot"}
    ):
        return {
            "success": False,
            "failure_kind": "apply_cad_action_contract_failure",
            "summary": (
                "A local_finish turn must spend apply_cad_action on a topology-anchored local edit, "
                "not on a session-control escape action."
            ),
            "error_message": (
                "apply_cad_action preflight failed: "
                f"{normalized_action} is not allowed while the turn_tool_policy is in local_finish mode"
            ),
            "suggestions": [
                "Use action_params.face_ref or action_params.edge_refs from the latest query_topology result for the next local edit.",
                "Keep session-control actions such as rollback or clear_session out of local_finish turns.",
            ],
        }
    if latest_turn_policy is not None and latest_turn_policy.mode == "local_finish":
        preferred_face_refs: list[str] = []
        candidate_face_labels: list[str] = []
        candidate_face_ref_map: dict[str, list[str]] = {}
        candidate_set_face_refs: list[str] = []

        def _append_unique_face_ref(ref_id: str) -> None:
            if ref_id and ref_id not in preferred_face_refs:
                preferred_face_refs.append(ref_id)

        if isinstance(topology_payload, dict):
            for item in topology_payload.get("candidate_sets") or []:
                if not isinstance(item, dict):
                    continue
                if str(item.get("entity_type") or "").strip().lower() != "face":
                    continue
                label = str(item.get("label") or "").strip()
                ref_ids = [
                    str(ref_id).strip()
                    for ref_id in (item.get("ref_ids") or [])
                    if isinstance(ref_id, str) and str(ref_id).strip().startswith("face:")
                ]
                if not ref_ids:
                    continue
                candidate_id = str(item.get("candidate_id") or "").strip().lower()
                if candidate_id:
                    candidate_face_ref_map[candidate_id] = list(ref_ids)
                candidate_token = _normalize_topology_candidate_token(item.get("candidate_id"))
                if candidate_token:
                    candidate_face_ref_map[candidate_token] = list(ref_ids)
                candidate_label_token = _normalize_topology_candidate_token(item.get("label"))
                if candidate_label_token:
                    candidate_face_ref_map[candidate_label_token] = list(ref_ids)
                if label and label not in candidate_face_labels:
                    candidate_face_labels.append(label)
                preferred_ref_id = str(item.get("preferred_ref_id") or "").strip()
                if preferred_ref_id.startswith("face:"):
                    _append_unique_face_ref(preferred_ref_id)
                for ref_id in ref_ids:
                    candidate_set_face_refs.append(ref_id)
            for ref_id in candidate_set_face_refs:
                if isinstance(ref_id, str) and ref_id.startswith("face:"):
                    _append_unique_face_ref(ref_id)
            for ref_id in topology_payload.get("matched_ref_ids") or []:
                if isinstance(ref_id, str) and ref_id.startswith("face:"):
                    _append_unique_face_ref(ref_id)
        face_ref = str(action_params.get("face_ref") or "").strip()
        if not face_ref:
            face_reference = str(action_params.get("face_reference") or "").strip()
            if face_reference.startswith("face:"):
                action_params["face_ref"] = face_reference
                action_params.pop("face_reference", None)
                face_ref = face_reference
        broad_face_alias = str(action_params.get("face") or "").strip()
        path_ref = str(action_params.get("path_ref") or "").strip()
        needs_exact_face_ref = normalized_action in {"hole", "sphere_recess"} or (
            normalized_action == "create_sketch" and not path_ref
        )
        if needs_exact_face_ref and face_ref and not face_ref.startswith("face:"):
            candidate_key = _normalize_topology_candidate_token(face_ref)
            candidate_refs = candidate_face_ref_map.get(candidate_key) or []
            if len(candidate_refs) == 1:
                action_params["face_ref"] = candidate_refs[0]
                face_ref = candidate_refs[0]
            elif candidate_refs:
                suggestions = [
                    "Choose one concrete action_params.face_ref from the latest query_topology candidate set instead of passing the candidate-set label directly.",
                    "Preferred face_ref candidates: " + ", ".join(candidate_refs[:3]) + ".",
                ]
                if candidate_face_labels:
                    suggestions.append(
                        "Recent face candidate sets already available: "
                        + ", ".join(candidate_face_labels[:3])
                        + "."
                    )
                return {
                    "success": False,
                    "failure_kind": "apply_cad_action_contract_failure",
                    "summary": (
                        "A local face edit cannot consume an ambiguous candidate face set directly; "
                        "pick one exact face_ref from the latest topology evidence first."
                    ),
                    "error_message": (
                        "apply_cad_action preflight failed: candidate face set "
                        f"{face_ref!r} resolved to multiple faces; choose one exact face_ref "
                        "from the latest query_topology result"
                    ),
                    "preferred_face_refs": candidate_refs[:4],
                    "candidate_face_set_labels": candidate_face_labels[:4],
                    "suggestions": suggestions,
                }
        if needs_exact_face_ref and not face_ref and broad_face_alias.startswith("face:"):
            action_params["face_ref"] = broad_face_alias
            action_params.pop("face", None)
            face_ref = broad_face_alias
            broad_face_alias = ""
        if needs_exact_face_ref and not face_ref and broad_face_alias:
            alias_key = broad_face_alias.strip().lower().replace("-", "_").replace(" ", "_")
            alias_candidate_ids = [alias_key]
            singular_face_aliases = {
                "top": "top_faces",
                "top_face": "top_faces",
                "bottom": "bottom_faces",
                "bottom_face": "bottom_faces",
                "front": "front_faces",
                "front_face": "front_faces",
                "back": "back_faces",
                "back_face": "back_faces",
                "left": "left_faces",
                "left_face": "left_faces",
                "right": "right_faces",
                "right_face": "right_faces",
            }
            mapped_candidate_id = singular_face_aliases.get(alias_key)
            if mapped_candidate_id and mapped_candidate_id not in alias_candidate_ids:
                alias_candidate_ids.append(mapped_candidate_id)
            resolved_face_refs: list[str] = []
            for candidate_id in alias_candidate_ids:
                candidate_refs = candidate_face_ref_map.get(candidate_id) or []
                if len(candidate_refs) == 1:
                    resolved_face_refs = candidate_refs
                    break
            if resolved_face_refs:
                action_params["face_ref"] = resolved_face_refs[0]
                action_params.pop("face", None)
                face_ref = resolved_face_refs[0]
                broad_face_alias = ""
        if needs_exact_face_ref and not face_ref and preferred_face_refs:
            alias_fragment = f" instead of action_params.face={broad_face_alias!r}" if broad_face_alias else ""
            suggestions = [
                "Use action_params.face_ref from the latest query_topology result for this local face edit instead of a broad face or plane alias.",
                "Preferred face_ref candidates: " + ", ".join(preferred_face_refs[:3]) + ".",
            ]
            if candidate_face_labels:
                suggestions.append(
                    "Recent face candidate sets already available: "
                    + ", ".join(candidate_face_labels[:3])
                    + "."
                )
            return {
                "success": False,
                "failure_kind": "apply_cad_action_contract_failure",
                "summary": (
                    "A topology-targeted local face edit should consume exact face_ref once "
                    "query_topology has already returned actionable face candidates."
                ),
                "error_message": (
                    "apply_cad_action preflight failed: "
                    f"{normalized_action} must use face_ref from latest query_topology{alias_fragment} "
                    "during local_finish"
                ),
                "preferred_face_refs": preferred_face_refs[:4],
                "candidate_face_set_labels": candidate_face_labels[:4],
                "suggestions": suggestions,
            }
    if normalized_action == "cut_extrude":
        profile_refs = (
            [
                str(item).strip()
                for item in (sketch_state.get("profile_refs") or [])
                if isinstance(item, str) and str(item).strip()
            ]
            if isinstance(sketch_state, dict)
            else []
        )
        path_refs = (
            [
                str(item).strip()
                for item in (sketch_state.get("path_refs") or [])
                if isinstance(item, str) and str(item).strip()
            ]
            if isinstance(sketch_state, dict)
            else []
        )
        if profile_refs:
            return None
        if isinstance(sketch_state, dict) or latest_action_type in {None, "create_sketch"}:
            suggestions = [
                "Open a target sketch and add a closed profile before cut_extrude.",
                "If the host face is already known, use create_sketch(face_ref=...) followed by add_circle/add_rectangle/add_polygon.",
                "Use query_sketch to confirm profile_refs is non-empty before retrying cut_extrude.",
            ]
            if path_refs:
                suggestions.append(
                    "Current sketch only exposes path_refs; add a closed profile or switch to sweep/revolve if the intent is path-driven."
                )
            return {
                "success": False,
                "failure_kind": "apply_cad_action_contract_failure",
                "summary": (
                    "cut_extrude needs an active closed profile sketch; do not spend a local write on a subtractive "
                    "terminal action before the sketch window has produced profile_refs."
                ),
                "error_message": (
                    "apply_cad_action preflight failed: cut_extrude requires an active profile sketch "
                    "with non-empty profile_refs"
                ),
                "latest_action_type": latest_action_type,
                "sketch_has_path_refs": bool(path_refs),
                "suggestions": suggestions,
            }
    if normalized_action not in {"fillet", "chamfer"}:
        return None
    if "edge_refs" not in action_params:
        for alias_key in ("edges", "target_edges"):
            if not isinstance(action_params.get(alias_key), list):
                continue
            normalized_edge_refs = [
                str(item).strip()
                for item in (action_params.get(alias_key) or [])
                if isinstance(item, str) and str(item).strip().startswith("edge:")
            ]
            if normalized_edge_refs:
                action_params["edge_refs"] = normalized_edge_refs
                action_params.pop(alias_key, None)
                break
    edge_refs = [
        str(item).strip()
        for item in (action_params.get("edge_refs") or [])
        if isinstance(item, str) and str(item).strip()
    ]
    if edge_refs:
        return None
    if not isinstance(topology_payload, dict):
        return None
    candidate_sets = topology_payload.get("candidate_sets")
    if not isinstance(candidate_sets, list):
        return None
    edge_candidates = [
        item
        for item in candidate_sets
        if isinstance(item, dict)
        and str(item.get("entity_type") or "").strip().lower() == "edge"
        and any(isinstance(ref, str) and ref.strip() for ref in (item.get("ref_ids") or []))
    ]
    if not edge_candidates:
        return None
    candidate_labels = [
        str(item.get("label") or "").strip()
        for item in edge_candidates[:3]
        if str(item.get("label") or "").strip()
    ]
    suggestions = [
        "Use explicit action_params.edge_refs from the latest query_topology candidate_sets before retrying this local fillet/chamfer.",
    ]
    if candidate_labels:
        suggestions.append(
            "Recent edge candidate sets already available: " + ", ".join(candidate_labels) + "."
        )
    return {
        "success": False,
        "failure_kind": "apply_cad_action_contract_failure",
        "summary": (
            "Local fillet/chamfer should consume explicit edge_refs once query_topology has already "
            "returned targetable edge candidate sets."
        ),
        "error_message": "apply_cad_action preflight failed: missing edge_refs for local fillet/chamfer",
        "candidate_edge_set_labels": candidate_labels,
        "suggestions": suggestions,
    }
