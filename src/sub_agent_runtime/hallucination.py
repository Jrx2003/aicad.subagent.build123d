from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import re
from typing import Any

from sub_agent_runtime.turn_state import RunState


_SEVERITY_WEIGHTS = {
    "fatal": 1.0,
    "strong": 0.6,
    "repair": 0.5,
    "validation": 0.35,
    "weak": 0.2,
}

_TARGETING_ERROR_RE = re.compile(
    r"(?:invalid|missing|unknown)_(?:face|edge)_ref|invalid_reference:\s+malformed\s+(?:face|edge)_ref|topology|selector|candidate_set",
    flags=re.IGNORECASE,
)
_TARGETING_CONTRACT_FAILURE_RE = re.compile(
    r"missing\s+(?:explicit\s+)?(?:face|edge)_?refs?|candidate[_ ]sets?|topology",
    flags=re.IGNORECASE,
)
_UNDEFINED_SYMBOL_RE = re.compile(
    r"(?:name|variable)\s+['`\"]?(?P<name>[A-Za-z_][A-Za-z0-9_]*)['`\"]?\s+is\s+not\s+defined",
    flags=re.IGNORECASE,
)
_BUILDSKETCH_CONTEXT_RUNTIME_RE = re.compile(
    r"buildpart doesn't have a (?P<helper>[a-z_][a-z0-9_]*) object or operation .*applies to \['buildsketch'\]",
    flags=re.IGNORECASE | re.DOTALL,
)
_TRANSFORM_CONTEXT_MANAGER_RUNTIME_RE = re.compile(
    r"'(?P<helper>rotation|position|location)' object does not support the context manager protocol",
    flags=re.IGNORECASE,
)
_UNEXPECTED_KEYWORD_ARGUMENT_RE = re.compile(
    r"(?P<constructor>[A-Za-z_][A-Za-z0-9_]*)\.__init__\(\)\s+got\s+an\s+unexpected\s+keyword\s+argument\s+['\"](?P<keyword>[A-Za-z_][A-Za-z0-9_]*)['\"]",
    flags=re.IGNORECASE,
)
_INFRASTRUCTURE_WRITE_ERROR_RE = re.compile(
    r"docker api error|docker desktop is unable to start|container wait failed|container_wait_failed|"
    r"error while fetching server api version|unixhttpconnectionpool|503 server error.*docker|"
    r"docker daemon appears unavailable|checked candidate sockets|"
    r"modulenotfounderror:\s+no module named 'lib3mf'",
    flags=re.IGNORECASE,
)


@dataclass(slots=True)
class HallucinationEvent:
    event_id: str
    round: int
    tool_name: str
    layer: str
    category: str
    severity: str
    family_id: str | None = None
    rule_id: str | None = None
    source: str | None = None
    message: str = ""
    artifact_path: str | None = None
    is_primary_write_related: bool = False

    @property
    def weight(self) -> float:
        return float(_SEVERITY_WEIGHTS.get(self.severity, 0.2))

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "round": self.round,
            "tool_name": self.tool_name,
            "layer": self.layer,
            "category": self.category,
            "severity": self.severity,
            "family_id": self.family_id,
            "rule_id": self.rule_id,
            "source": self.source,
            "message": self.message,
            "artifact_path": self.artifact_path,
            "is_primary_write_related": self.is_primary_write_related,
            "weight": self.weight,
        }


def normalize_hallucination_summary(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        payload = {}
    layers = payload.get("layers") if isinstance(payload.get("layers"), dict) else {}
    categories = (
        payload.get("categories") if isinstance(payload.get("categories"), dict) else {}
    )
    top_examples = (
        payload.get("top_examples") if isinstance(payload.get("top_examples"), list) else []
    )
    events = payload.get("events") if isinstance(payload.get("events"), list) else []
    return {
        "event_count": int(payload.get("event_count", 0) or 0),
        "weighted_score": float(payload.get("weighted_score", 0.0) or 0.0),
        "events_per_write": float(payload.get("events_per_write", 0.0) or 0.0),
        "first_write_event_count": int(payload.get("first_write_event_count", 0) or 0),
        "primary_layer": str(payload.get("primary_layer") or "").strip() or None,
        "layers": {
            str(key): int(value or 0)
            for key, value in layers.items()
            if isinstance(key, str) and key.strip()
        },
        "categories": {
            str(key): int(value or 0)
            for key, value in categories.items()
            if isinstance(key, str) and key.strip()
        },
        "top_examples": [item for item in top_examples if isinstance(item, dict)],
        "events": [item for item in events if isinstance(item, dict)],
    }


def summarize_hallucination_events(
    events: list[HallucinationEvent],
    *,
    write_turn_count: int,
) -> dict[str, Any]:
    layer_counts = Counter(event.layer for event in events if event.layer)
    layer_weights = Counter()
    for event in events:
        if event.layer:
            layer_weights[event.layer] += event.weight
    category_counts = Counter(event.category for event in events if event.category)
    ordered_events = sorted(
        events,
        key=lambda item: (-item.weight, item.round, item.tool_name, item.event_id),
    )
    primary_layer = None
    if layer_counts:
        primary_layer = sorted(
            layer_counts.items(),
            key=lambda item: (-item[1], -float(layer_weights.get(item[0], 0.0)), item[0]),
        )[0][0]
    return {
        "event_count": len(events),
        "weighted_score": round(sum(event.weight for event in events), 4),
        "events_per_write": round(float(len(events)) / float(write_turn_count), 4)
        if write_turn_count > 0
        else 0.0,
        "first_write_event_count": sum(1 for event in events if event.is_primary_write_related),
        "primary_layer": primary_layer,
        "layers": dict(layer_counts),
        "categories": dict(category_counts),
        "top_examples": [event.to_dict() for event in ordered_events[:5]],
        "events": [event.to_dict() for event in ordered_events],
    }


def build_run_hallucination_summary(run_state: RunState) -> dict[str, Any]:
    first_write_round = next(
        (turn.round_no for turn in run_state.turns if turn.write_tool_name is not None),
        None,
    )
    events: list[HallucinationEvent] = []
    for turn in run_state.turns:
        for tool_index, result in enumerate(turn.tool_results):
            tool_call = turn.tool_calls[tool_index] if tool_index < len(turn.tool_calls) else None
            is_primary = (
                first_write_round is not None
                and turn.round_no == first_write_round
                and result.name in {"execute_build123d", "apply_cad_action", "execute_repair_packet"}
            )
            events.extend(
                _events_from_tool_result(
                    run_state=run_state,
                    round_no=turn.round_no,
                    tool_call=tool_call.arguments if tool_call is not None else {},
                    tool_name=result.name,
                    payload=result.payload if isinstance(result.payload, dict) else {},
                    error=result.error,
                    artifact_files=result.artifact_files,
                    is_primary_write_related=is_primary,
                )
            )
    return summarize_hallucination_events(
        events,
        write_turn_count=run_state.executed_action_count,
    )


def _events_from_tool_result(
    *,
    run_state: RunState,
    round_no: int,
    tool_call: dict[str, Any],
    tool_name: str,
    payload: dict[str, Any],
    error: str | None,
    artifact_files: list[str],
    is_primary_write_related: bool,
) -> list[HallucinationEvent]:
    events: list[HallucinationEvent] = []
    artifact_path = artifact_files[0] if artifact_files else _synthetic_artifact_path(round_no, tool_name)

    if tool_name == "execute_build123d":
        lint_hits = payload.get("lint_hits") if isinstance(payload.get("lint_hits"), list) else []
        if lint_hits:
            for index, hit in enumerate(lint_hits, start=1):
                if not isinstance(hit, dict):
                    continue
                family_ids = hit.get("family_ids") if isinstance(hit.get("family_ids"), list) else []
                family_id = next(
                    (
                        str(item).strip()
                        for item in family_ids
                        if isinstance(item, str) and str(item).strip()
                    ),
                    None,
                )
                rule_id = str(hit.get("lint_id") or hit.get("rule_id") or "").strip() or None
                message = str(hit.get("repair_hint") or hit.get("message") or error or "").strip()
                events.append(
                    HallucinationEvent(
                        event_id=f"hallucination.r{round_no}.{tool_name}.{index}",
                        round=round_no,
                        tool_name=tool_name,
                        layer="write_surface",
                        category="invalid_api_contract",
                        severity="fatal",
                        family_id=family_id,
                        rule_id=rule_id,
                        source="preflight_lint",
                        message=message,
                        artifact_path=artifact_path,
                        is_primary_write_related=is_primary_write_related,
                    )
                )
            return events
        if error:
            if _is_infrastructure_write_error(error):
                return events
            runtime_category, runtime_severity, runtime_source = (
                _classify_build123d_write_runtime_error(error)
            )
            events.append(
                HallucinationEvent(
                    event_id=f"hallucination.r{round_no}.{tool_name}.runtime",
                    round=round_no,
                    tool_name=tool_name,
                    layer="write_surface",
                    category=runtime_category,
                    severity=runtime_severity,
                    source=runtime_source,
                    message=str(error),
                    artifact_path=artifact_path,
                    is_primary_write_related=is_primary_write_related,
                )
            )
            return events

    if tool_name == "execute_repair_packet" and error:
        packet = payload.get("packet") if isinstance(payload.get("packet"), dict) else {}
        events.append(
            HallucinationEvent(
                event_id=f"hallucination.r{round_no}.{tool_name}.compile",
                round=round_no,
                tool_name=tool_name,
                layer="repair_surface",
                category="repair_packet_compile_miss",
                severity="repair",
                family_id=str(payload.get("family_id") or packet.get("family_id") or "").strip() or None,
                rule_id=str(payload.get("recipe_id") or packet.get("recipe_id") or "").strip() or None,
                source="repair_packet_compiler",
                message=str(error),
                artifact_path=artifact_path,
                is_primary_write_related=is_primary_write_related,
            )
        )
        return events

    if tool_name == "validate_requirement":
        is_complete = bool(payload.get("is_complete"))
        coverage_confidence = float(payload.get("coverage_confidence", 0.0) or 0.0)
        insufficient_evidence = payload.get("insufficient_evidence") is True
        observation_tags = {
            str(tag).strip()
            for tag in (payload.get("observation_tags") or [])
            if isinstance(tag, str) and str(tag).strip()
        }
        decision_hints = [
            str(item).strip()
            for item in (payload.get("decision_hints") or [])
            if isinstance(item, str) and str(item).strip()
        ]
        if "validation:llm_provider_error" in observation_tags:
            provider_hint = next(
                (
                    item
                    for item in decision_hints
                    if item.startswith("validation_llm_provider_error:")
                ),
                "",
            )
            events.append(
                HallucinationEvent(
                    event_id=f"hallucination.r{round_no}.{tool_name}.provider_error",
                    round=round_no,
                    tool_name=tool_name,
                    layer="validation_surface",
                    category="validation_provider_error",
                    severity="validation",
                    source="validation_llm",
                    message=provider_hint or str(payload.get("summary") or "validation llm provider error"),
                    artifact_path=artifact_path,
                    is_primary_write_related=is_primary_write_related,
                )
            )
        elif "validation:llm_invalid_output" in observation_tags:
            events.append(
                HallucinationEvent(
                    event_id=f"hallucination.r{round_no}.{tool_name}.invalid_output",
                    round=round_no,
                    tool_name=tool_name,
                    layer="validation_surface",
                    category="validation_invalid_output",
                    severity="validation",
                    source="validation_llm",
                    message=str(payload.get("summary") or "validation llm returned invalid output"),
                    artifact_path=artifact_path,
                    is_primary_write_related=is_primary_write_related,
                )
            )
        if is_complete and (insufficient_evidence or coverage_confidence < 0.5):
            events.append(
                HallucinationEvent(
                    event_id=f"hallucination.r{round_no}.{tool_name}.overclaim",
                    round=round_no,
                    tool_name=tool_name,
                    layer="validation_surface",
                    category="validation_overclaim",
                    severity="validation",
                    source="validator",
                    message=str(payload.get("summary") or "validation completed without enough grounding"),
                    artifact_path=artifact_path,
                    is_primary_write_related=is_primary_write_related,
                )
            )
        return events

    if tool_name == "apply_cad_action":
        action_params = (
            tool_call.get("action_params") if isinstance(tool_call.get("action_params"), dict) else {}
        )
        failure_kind = str(payload.get("failure_kind") or "").strip()
        face_ref = str(action_params.get("face_ref") or "").strip()
        edge_refs = [
            str(item).strip()
            for item in (action_params.get("edge_refs") or [])
            if isinstance(item, str) and str(item).strip()
        ]
        if failure_kind == "apply_cad_action_contract_failure":
            contract_message = str(
                payload.get("error_message")
                or payload.get("summary")
                or error
                or "apply_cad_action contract failure"
            )
            layer = "write_surface"
            category = "local_action_contract_failure"
            if _TARGETING_CONTRACT_FAILURE_RE.search(contract_message):
                layer = "read_surface"
                category = "local_action_contract_missing_target_refs"
            events.append(
                HallucinationEvent(
                    event_id=f"hallucination.r{round_no}.{tool_name}.contract",
                    round=round_no,
                    tool_name=tool_name,
                    layer=layer,
                    category=category,
                    severity="strong",
                    source="tool_runtime",
                    message=contract_message,
                    artifact_path=artifact_path,
                    is_primary_write_related=is_primary_write_related,
                )
            )
            return events
        if face_ref or edge_refs:
            if not _has_successful_targeting_read_before_round(run_state, round_no):
                events.append(
                    HallucinationEvent(
                        event_id=f"hallucination.r{round_no}.{tool_name}.blind_target",
                        round=round_no,
                        tool_name=tool_name,
                        layer="read_surface",
                        category="targeting_without_readback",
                        severity="strong",
                        source="runtime_policy",
                        message="Local face/edge targeting was attempted without a prior successful query_topology/query_geometry read.",
                        artifact_path=artifact_path,
                        is_primary_write_related=is_primary_write_related,
                    )
                )
            if error and _TARGETING_ERROR_RE.search(error):
                events.append(
                    HallucinationEvent(
                        event_id=f"hallucination.r{round_no}.{tool_name}.invalid_ref",
                        round=round_no,
                        tool_name=tool_name,
                        layer="read_surface",
                        category="invalid_target_reference",
                        severity="strong",
                        source="runtime_policy",
                        message=str(error),
                        artifact_path=artifact_path,
                        is_primary_write_related=is_primary_write_related,
                    )
                )
        elif error:
            events.append(
                HallucinationEvent(
                    event_id=f"hallucination.r{round_no}.{tool_name}.runtime",
                    round=round_no,
                    tool_name=tool_name,
                    layer="write_surface",
                    category="write_runtime_exception",
                    severity="fatal",
                    source="tool_runtime",
                    message=str(error),
                    artifact_path=artifact_path,
                    is_primary_write_related=is_primary_write_related,
                )
            )
        return events

    return events


def _has_successful_targeting_read_before_round(run_state: RunState, round_no: int) -> bool:
    for turn in run_state.turns:
        if turn.round_no >= round_no:
            continue
        for result in turn.tool_results:
            if result.success and result.name in {"query_topology", "query_geometry"}:
                return True
    return False


def _synthetic_artifact_path(round_no: int, tool_name: str) -> str:
    return f"trace/round_digest.json#round={round_no}:{tool_name}"


def _classify_build123d_write_runtime_error(
    error: str | None,
) -> tuple[str, str, str]:
    raw_error = str(error or "")
    lowered = raw_error.lower()
    if "planes can only be multiplied with locations or shapes" in lowered:
        return ("invalid_plane_location_contract", "fatal", "tool_runtime")
    if "nothing to subtract from" in lowered:
        return ("builder_subtract_without_host", "fatal", "tool_runtime")
    buildsketch_context_match = _BUILDSKETCH_CONTEXT_RUNTIME_RE.search(lowered)
    if buildsketch_context_match is not None:
        helper = str(buildsketch_context_match.group("helper") or "").strip().lower()
        category = "invalid_builder_context.buildsketch_only_primitive"
        if helper:
            category = f"{category}.{helper}"
        return (category, "fatal", "tool_runtime")
    transform_context_match = _TRANSFORM_CONTEXT_MANAGER_RUNTIME_RE.search(lowered)
    if transform_context_match is not None:
        helper = str(transform_context_match.group("helper") or "").strip().lower()
        category = "invalid_builder_context.transform_context_manager"
        if helper:
            category = f"{category}.{helper}"
        return (category, "fatal", "tool_runtime")
    unexpected_keyword_match = _UNEXPECTED_KEYWORD_ARGUMENT_RE.search(raw_error)
    if unexpected_keyword_match is not None:
        return (
            "invalid_constructor_keyword_contract",
            "fatal",
            "runtime_typeerror",
        )
    if "nameerror" in lowered or "unboundlocalerror" in lowered:
        match = _UNDEFINED_SYMBOL_RE.search(str(error or ""))
        symbol = (
            str(match.group("name")).strip().lower()
            if match is not None
            else ""
        )
        if symbol:
            return (
                f"write_runtime_symbol_error.{symbol}",
                "repair",
                "runtime_symbol_resolution",
            )
        return (
            "write_runtime_symbol_error",
            "repair",
            "runtime_symbol_resolution",
        )
    return ("write_runtime_exception", "fatal", "tool_runtime")


def _is_infrastructure_write_error(error: str | None) -> bool:
    raw_error = str(error or "")
    return bool(_INFRASTRUCTURE_WRITE_ERROR_RE.search(raw_error))
