from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class IterationRequest(BaseModel):
    """Stable upstream-facing request contract for iterative sub-agent runs."""

    model_config = ConfigDict(extra="forbid")

    requirements: dict[str, Any] = Field(
        default_factory=dict,
        description="Structured requirement payload from main-agent or external caller.",
    )
    max_rounds: int = Field(default=8, ge=1, le=30)
    sandbox_timeout: int = Field(default=180, ge=30, le=600)
    one_action_per_round: bool = Field(default=True)
    force_post_convergence_round: bool = Field(default=False)
    session_id: str | None = Field(
        default=None,
        description="Optional stable session ID. If omitted, runtime generates one.",
    )
    previous_error: str | None = Field(
        default=None,
        description="Optional previous error to seed first planning round.",
    )


class IterationRunSummary(BaseModel):
    """Compact run summary for API callers and CI traces."""

    model_config = ConfigDict(extra="forbid")

    session_id: str
    provider: str
    model: str
    planner_rounds: int
    executed_action_count: int
    executed_action_types: list[str] = Field(default_factory=list)
    converged: bool
    validation_complete: bool
    step_file_exists: bool
    render_file_exists: bool
    render_image_attached_for_prompt: bool
    render_image_size_bytes: int | None = None
    inspection_only_rounds: int = 0
    inspection_requested_rounds: int = 0
    no_op_action_count: int = 0
    token_usage: dict[str, int] = Field(default_factory=dict)
    reasoning_log_available: bool = False
    tool_event_count: int = 0
    compaction_count: int = 0
    validation_call_count: int = 0
    read_only_turn_count: int = 0
    primary_write_mode: str | None = None
    first_write_tool: str | None = None
    structured_bootstrap_rounds: int = 0
    stale_probe_carry_count: int = 0
    evidence_conflict_count: int = 0
    freshness_conflict_count: int = 0
    forced_policy_chain: list[str] = Field(default_factory=list)
    feature_probe_count: int = 0
    probe_code_count: int = 0
    build123d_hallucination: dict[str, Any] = Field(default_factory=dict)
    baseline_comparison: dict[str, Any] = Field(default_factory=dict)
    failure_cluster: str | None = None
    llm_error: str | None = None
    last_error: str | None = None
    runtime_mode_effective: str | None = None


class IterationRunResult(BaseModel):
    """Full run contract with artifact location and summary."""

    model_config = ConfigDict(extra="forbid")

    run_dir: str
    summary: IterationRunSummary
    request: IterationRequest
