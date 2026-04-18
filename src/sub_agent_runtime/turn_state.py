from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from sub_agent_runtime.feature_graph import DomainKernelState

_SEMANTIC_REFRESH_TOOL_NAMES = {
    "query_kernel_state",
    "query_feature_probes",
    "query_topology",
    "validate_requirement",
    "execute_build123d_probe",
}


class ToolCategory(str, Enum):
    READ = "read"
    WRITE = "write"
    JUDGE = "judge"
    VIRTUAL = "virtual"


@dataclass(slots=True)
class ToolCallRecord:
    name: str
    category: ToolCategory
    arguments: dict[str, Any] = field(default_factory=dict)
    call_id: str | None = None


@dataclass(slots=True)
class ToolResultRecord:
    name: str
    category: ToolCategory
    success: bool
    payload: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    artifact_files: list[str] = field(default_factory=list)
    artifact_contents: dict[str, bytes] = field(default_factory=dict)
    step_file: str | None = None


@dataclass(slots=True)
class VisibleDecisionLog:
    round_no: int
    summary: str
    why_next: str | None = None
    stop_reason: str | None = None
    tool_names: list[str] = field(default_factory=list)
    requested_finish: bool = False


@dataclass(slots=True)
class ToolExecutionEvent:
    round_no: int
    tool_name: str
    phase: str
    category: ToolCategory | None = None
    success: bool | None = None
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class AgentEvent:
    kind: str
    round_no: int | None = None
    role: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class CompactionBoundary:
    round_no: int
    raw_chars: int
    final_chars: int
    was_compacted: bool
    reason: str | None = None
    kept_sections: list[str] = field(default_factory=list)
    summarized_sections: list[str] = field(default_factory=list)
    post_compact_messages: list[str] = field(default_factory=list)


@dataclass(slots=True)
class TurnToolPolicy:
    round_no: int
    policy_id: str
    mode: str
    reason: str
    allowed_tool_names: list[str] = field(default_factory=list)
    blocked_tool_names: list[str] = field(default_factory=list)
    preferred_tool_names: list[str] = field(default_factory=list)
    preferred_probe_families: list[str] = field(default_factory=list)


@dataclass(slots=True)
class TurnEnvelope:
    round_no: int
    prompt_metrics: dict[str, Any] = field(default_factory=dict)
    decision_log: VisibleDecisionLog | None = None
    tool_calls: list[ToolCallRecord] = field(default_factory=list)
    tool_results: list[ToolResultRecord] = field(default_factory=list)
    compaction_boundary: CompactionBoundary | None = None
    turn_tool_policy: TurnToolPolicy | None = None
    stop_reason: str | None = None
    previous_error: str | None = None


@dataclass(slots=True)
class TurnRecord:
    round_no: int
    decision_summary: str
    tool_calls: list[ToolCallRecord] = field(default_factory=list)
    tool_results: list[ToolResultRecord] = field(default_factory=list)
    error: str | None = None
    requested_finish: bool = False
    validation_requested: bool = False

    @property
    def write_tool_name(self) -> str | None:
        for tool_call in self.tool_calls:
            if tool_call.category == ToolCategory.WRITE:
                return tool_call.name
        return None

    @property
    def read_only(self) -> bool:
        return bool(self.tool_calls) and all(
            tool_call.category != ToolCategory.WRITE for tool_call in self.tool_calls
        )


@dataclass(slots=True)
class EvidenceStore:
    latest_by_tool: dict[str, dict[str, Any]] = field(default_factory=dict)
    artifacts_by_tool: dict[str, list[str]] = field(default_factory=dict)
    rounds_by_tool: dict[str, int | None] = field(default_factory=dict)

    def update(
        self,
        *,
        tool_name: str,
        payload: dict[str, Any],
        artifact_files: list[str] | None = None,
        round_no: int | None = None,
    ) -> None:
        self.latest_by_tool[tool_name] = payload
        self.rounds_by_tool[tool_name] = round_no
        if artifact_files:
            self.artifacts_by_tool[tool_name] = list(artifact_files)

    def invalidate(self, *tool_names: str) -> None:
        for tool_name in tool_names:
            self.latest_by_tool.pop(tool_name, None)
            self.artifacts_by_tool.pop(tool_name, None)
            self.rounds_by_tool.pop(tool_name, None)


@dataclass(slots=True)
class RunState:
    session_id: str
    requirements: dict[str, Any]
    feature_graph: DomainKernelState | None = None
    turns: list[TurnRecord] = field(default_factory=list)
    turn_envelopes: list[TurnEnvelope] = field(default_factory=list)
    visible_decision_logs: list[VisibleDecisionLog] = field(default_factory=list)
    tool_execution_events: list[ToolExecutionEvent] = field(default_factory=list)
    agent_events: list[AgentEvent] = field(default_factory=list)
    compaction_boundaries: list[CompactionBoundary] = field(default_factory=list)
    turn_tool_policies: list[TurnToolPolicy] = field(default_factory=list)
    evidence: EvidenceStore = field(default_factory=EvidenceStore)
    action_history: list[dict[str, Any]] = field(default_factory=list)
    previous_error: str | None = None
    latest_validation: dict[str, Any] | None = None
    latest_render_view: dict[str, Any] | None = None
    latest_step_file: str | None = None
    latest_output_files: list[str] = field(default_factory=list)
    latest_write_payload: dict[str, Any] | None = None
    llm_error: str | None = None
    stale_probe_carry_count: int = 0
    evidence_conflict_count: int = 0
    stale_probe_carry_events: set[tuple[int, str]] = field(default_factory=set)
    evidence_conflict_rounds: set[int] = field(default_factory=set)
    token_usage: dict[str, int] = field(
        default_factory=lambda: {
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
            "rounds_with_usage": 0,
        }
    )

    def add_turn(self, turn: TurnRecord) -> None:
        self.turns.append(turn)

    def add_visible_decision_log(self, log: VisibleDecisionLog) -> None:
        self.visible_decision_logs.append(log)

    def add_turn_envelope(self, envelope: TurnEnvelope) -> None:
        self.turn_envelopes.append(envelope)

    def add_tool_execution_events(
        self,
        events: list[ToolExecutionEvent],
    ) -> None:
        self.tool_execution_events.extend(events)

    def add_agent_event(self, event: AgentEvent) -> None:
        self.agent_events.append(event)

    def add_compaction_boundary(self, boundary: CompactionBoundary) -> None:
        self.compaction_boundaries.append(boundary)

    def add_turn_tool_policy(self, policy: TurnToolPolicy) -> None:
        self.turn_tool_policies.append(policy)

    def note_stale_probe_carry(self, round_no: int, tool_name: str) -> None:
        self.stale_probe_carry_events.add((round_no, tool_name))
        self.stale_probe_carry_count = len(self.stale_probe_carry_events)

    def note_evidence_conflict(self, round_no: int) -> None:
        self.evidence_conflict_rounds.add(round_no)
        self.evidence_conflict_count = len(self.evidence_conflict_rounds)

    @property
    def recent_turns(self) -> list[TurnRecord]:
        return list(self.turns)

    @property
    def inspection_only_rounds(self) -> int:
        return sum(1 for turn in self.turns if turn.read_only)

    @property
    def consecutive_inspection_only_rounds(self) -> int:
        count = 0
        for turn in reversed(self.turns):
            if not turn.read_only:
                break
            count += 1
        return count

    @property
    def executed_action_count(self) -> int:
        return sum(1 for turn in self.turns if turn.write_tool_name is not None)

    @property
    def executed_action_types(self) -> list[str]:
        return [
            tool_name
            for turn in self.turns
            for tool_name in [turn.write_tool_name]
            if isinstance(tool_name, str)
        ]

    @property
    def first_write_tool(self) -> str | None:
        for turn in self.turns:
            if turn.write_tool_name is not None:
                return turn.write_tool_name
        return None

    @property
    def structured_bootstrap_rounds(self) -> int:
        count = 0
        for turn in self.turns:
            tool_name = turn.write_tool_name
            if tool_name != "apply_cad_action":
                break
            count += 1
        return count

    @property
    def forced_policy_chain(self) -> list[str]:
        return [policy.policy_id for policy in self.turn_tool_policies if policy.policy_id]

    @property
    def latest_write_turn(self) -> TurnRecord | None:
        for turn in reversed(self.turns):
            if turn.write_tool_name is not None:
                return turn
        return None

    @property
    def latest_successful_write_turn(self) -> TurnRecord | None:
        for turn in reversed(self.turns):
            if turn.write_tool_name is None:
                continue
            if any(
                result.category == ToolCategory.WRITE and result.success
                for result in turn.tool_results
            ):
                return turn
        return None

    @property
    def validation_call_count(self) -> int:
        return sum(
            1
            for turn in self.turns
            for result in turn.tool_results
            if result.name == "validate_requirement"
        ) + sum(
            1 for event in self.agent_events if event.kind == "validation_result"
        )

    @property
    def feature_probe_count(self) -> int:
        return sum(
            1
            for turn in self.turns
            for result in turn.tool_results
            if result.name == "query_feature_probes"
        )

    @property
    def probe_code_count(self) -> int:
        return sum(
            1
            for turn in self.turns
            for result in turn.tool_results
            if result.name == "execute_build123d_probe"
        )


def count_consecutive_write_turns(
    run_state: RunState,
    *,
    tool_name: str,
) -> int:
    count = 0
    for turn in reversed(run_state.turns):
        if turn.write_tool_name != tool_name:
            break
        count += 1
    return count


def count_consecutive_successful_write_turns(
    run_state: RunState,
    *,
    tool_name: str,
) -> int:
    count = 0
    for turn in reversed(run_state.turns):
        if turn.write_tool_name != tool_name:
            break
        if not any(
            result.category == ToolCategory.WRITE
            and result.name == tool_name
            and result.success
            for result in turn.tool_results
        ):
            break
        count += 1
    return count


def _extract_geometry_from_payload(payload: Any) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    snapshot = payload.get("snapshot")
    if isinstance(snapshot, dict):
        geometry = snapshot.get("geometry")
        if isinstance(geometry, dict):
            return geometry
    geometry = payload.get("geometry")
    if isinstance(geometry, dict):
        return geometry
    payload_summary = payload.get("payload_summary")
    if isinstance(payload_summary, dict):
        return _extract_geometry_from_payload(payload_summary)
    return None


def _geometry_has_stable_solid(geometry: dict[str, Any] | None) -> bool:
    if not isinstance(geometry, dict):
        return False
    solids = int(geometry.get("solids", 0) or 0)
    volume = abs(float(geometry.get("volume", 0.0) or 0.0))
    bbox = geometry.get("bbox")
    return solids > 0 and volume > 0.0 and isinstance(bbox, list) and len(bbox) >= 3


def _turn_has_stable_write_solid(turn: TurnRecord) -> bool:
    for result in turn.tool_results:
        if result.category != ToolCategory.WRITE or not result.success:
            continue
        if _geometry_has_stable_solid(_extract_geometry_from_payload(result.payload)):
            return True
    return False


def _has_any_tool_turn_since_round(
    run_state: RunState,
    *,
    after_round: int,
    tool_names: set[str],
) -> bool:
    for turn in run_state.turns:
        if turn.round_no <= after_round:
            continue
        for tool_call in turn.tool_calls:
            if tool_call.name in tool_names:
                return True
    return False


def _has_successful_tool_result_since_round(
    run_state: RunState,
    *,
    after_round: int,
    tool_names: set[str],
) -> bool:
    for turn in run_state.turns:
        if turn.round_no <= after_round:
            continue
        for result in turn.tool_results:
            if result.name in tool_names and result.success:
                return True
    return False


def _has_successful_semantic_refresh_since_round(
    run_state: RunState,
    *,
    after_round: int,
) -> bool:
    if _has_successful_tool_result_since_round(
        run_state,
        after_round=after_round,
        tool_names=_SEMANTIC_REFRESH_TOOL_NAMES,
    ):
        return True
    for event in run_state.agent_events:
        if event.round_no is None or event.round_no <= after_round:
            continue
        if event.kind == "validation_result":
            return True
    return False


def build_post_solid_semantic_admission_signal(
    run_state: RunState,
    *,
    round_budget: dict[str, Any] | None = None,
    max_rounds: int | None = None,
) -> dict[str, Any] | None:
    latest_write_turn = run_state.latest_write_turn
    if latest_write_turn is None or latest_write_turn.write_tool_name != "apply_cad_action":
        return None
    if run_state.feature_graph is None:
        return None

    latest_geometry = _extract_geometry_from_payload(run_state.latest_write_payload)
    if not _geometry_has_stable_solid(latest_geometry):
        latest_geometry = None
        if _turn_has_stable_write_solid(latest_write_turn):
            for result in latest_write_turn.tool_results:
                if result.category != ToolCategory.WRITE or not result.success:
                    continue
                candidate = _extract_geometry_from_payload(result.payload)
                if _geometry_has_stable_solid(candidate):
                    latest_geometry = candidate
                    break
    if not _geometry_has_stable_solid(latest_geometry):
        return None

    for turn in run_state.turns:
        if turn.round_no >= latest_write_turn.round_no:
            break
        if _turn_has_stable_write_solid(turn):
            return None

    unsatisfied_feature_ids = [
        node.node_id
        for node in run_state.feature_graph.nodes.values()
        if node.kind == "feature" and node.status not in {"satisfied", "resolved"}
    ]
    if not unsatisfied_feature_ids:
        return None

    if _has_successful_semantic_refresh_since_round(
        run_state,
        after_round=latest_write_turn.round_no,
    ):
        return None

    if isinstance(round_budget, dict) and round_budget:
        remaining_rounds = int(round_budget.get("remaining_rounds", 0) or 0)
    elif isinstance(max_rounds, int) and max_rounds > 0:
        remaining_rounds = max(max_rounds - len(run_state.turns), 0)
    else:
        return None

    direct_code_escape = remaining_rounds <= 1 or len(unsatisfied_feature_ids) >= remaining_rounds
    return {
        "remaining_rounds": remaining_rounds,
        "first_stable_solid_round": latest_write_turn.round_no,
        "unsatisfied_feature_count": len(unsatisfied_feature_ids),
        "unsatisfied_feature_ids": unsatisfied_feature_ids[:6],
        "direct_code_escape": direct_code_escape,
        "recommended_next_tools": (
            ["execute_build123d", "query_kernel_state"]
            if direct_code_escape
            else ["query_kernel_state", "query_feature_probes"]
        ),
        "reason": (
            "budget_too_tight_for_semantic_admission"
            if direct_code_escape
            else "first_stable_solid_requires_semantic_admission"
        ),
    }


def build_feature_chain_budget_risk(
    run_state: RunState,
    *,
    round_budget: dict[str, Any] | None = None,
    max_rounds: int | None = None,
) -> dict[str, Any] | None:
    latest_write_turn = run_state.latest_write_turn
    if latest_write_turn is None or latest_write_turn.write_tool_name != "apply_cad_action":
        return None
    if run_state.feature_graph is None:
        return None

    payload = run_state.latest_write_payload if isinstance(run_state.latest_write_payload, dict) else {}
    snapshot = payload.get("snapshot")
    geometry = snapshot.get("geometry") if isinstance(snapshot, dict) else {}
    solids = int(geometry.get("solids", 0) or 0) if isinstance(geometry, dict) else 0
    if solids <= 0:
        return None

    unsatisfied_feature_ids = [
        node.node_id
        for node in run_state.feature_graph.nodes.values()
        if node.kind == "feature" and node.status not in {"satisfied", "resolved"}
    ]
    if len(unsatisfied_feature_ids) < 2:
        return None

    consecutive_apply_action_writes = count_consecutive_successful_write_turns(
        run_state,
        tool_name="apply_cad_action",
    )
    if consecutive_apply_action_writes < 3:
        return None

    if isinstance(round_budget, dict) and round_budget:
        remaining_rounds = int(round_budget.get("remaining_rounds", 0) or 0)
    elif isinstance(max_rounds, int) and max_rounds > 0:
        remaining_rounds = max(max_rounds - len(run_state.turns), 0)
    else:
        return None

    min_write_steps_remaining = len(unsatisfied_feature_ids) + 1
    risk = "normal"
    if remaining_rounds < min_write_steps_remaining:
        risk = "critical"
    elif remaining_rounds == min_write_steps_remaining:
        risk = "high"
    else:
        return None

    return {
        "risk": risk,
        "remaining_rounds": remaining_rounds,
        "min_write_steps_remaining": min_write_steps_remaining,
        "unsatisfied_feature_count": len(unsatisfied_feature_ids),
        "unsatisfied_feature_ids": unsatisfied_feature_ids[:6],
        "consecutive_apply_action_writes": consecutive_apply_action_writes,
        "recommended_fallback": "prefer_execute_build123d",
    }
