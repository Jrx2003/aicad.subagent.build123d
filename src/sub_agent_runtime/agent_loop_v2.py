from __future__ import annotations

import json
import datetime as dt
from pathlib import Path
from typing import Any

from common.config import Settings, settings
from common.blocker_taxonomy import (
    classify_blocker_taxonomy_many,
    taxonomy_family_ids_from_validation_payload,
    taxonomy_repair_lanes_from_validation_payload,
)
from common.logging import get_logger
from llm.factory import create_provider_client
from llm.interface import LLMMessage, LLMToolCall, LLMToolResponse
from sub_agent_runtime.context_manager import V2ContextManager
from sub_agent_runtime.contracts import (
    IterationRequest,
    IterationRunResult,
    IterationRunSummary,
)
from sub_agent_runtime.diagnostics import (
    build_runtime_validation_payload,
    build_v2_diagnostics,
    split_validation_feedback,
)
from sub_agent_runtime.feature_graph import (
    build_domain_kernel_digest,
    initialize_domain_kernel_state,
    sync_domain_kernel_state_from_tool_result,
)
from sub_agent_runtime.hallucination import build_run_hallucination_summary
from sub_agent_runtime.hooks import RuntimeHookManager
from sub_agent_runtime.skill_pack import (
    recommended_feature_probe_families,
    requirement_prefers_code_first_family,
)
from sub_agent_runtime.tool_adapters import supports_runtime_repair_packet
from sub_agent_runtime.tool_runtime import ToolRuntime
from sub_agent_runtime.turn_state import (
    AgentEvent,
    CompactionBoundary,
    RunState,
    ToolCategory,
    ToolExecutionEvent,
    TurnToolPolicy,
    TurnEnvelope,
    TurnRecord,
    VisibleDecisionLog,
    _has_successful_tool_result_since_round,
    build_feature_chain_budget_risk,
    build_post_solid_semantic_admission_signal,
    count_consecutive_write_turns,
    _has_successful_semantic_refresh_since_round,
)

logger = get_logger(__name__)

_SUCCESSFUL_WRITE_INVALIDATED_EVIDENCE = [
    "query_kernel_state",
    "query_feature_probes",
    "execute_build123d_probe",
    "validate_requirement",
    "query_geometry",
    "query_topology",
    "query_sketch",
    "render_view",
    "get_history",
]

_TRANSIENT_MODEL_TIMEOUT_RETRY_COUNT = 1
_RUNTIME_VALIDATION_TIMEOUT_SECONDS = 120
_AUTHORITATIVE_KERNEL_QUERY_TOOL_NAMES = {"query_kernel_state"}
_CANONICAL_OUTPUT_ARTIFACT_TOOL_NAMES = {
    "apply_cad_action",
    "execute_build123d",
    "execute_repair_packet",
    "render_view",
}
_SKETCH_WINDOW_CONTINUATION_ACTIONS = {
    "create_sketch",
    "add_circle",
    "add_rectangle",
    "add_polygon",
    "add_slot",
    "add_ellipse",
    "add_path",
}


class IterativeAgentLoopV2:
    """Model-driven tool loop that preserves the stable external contracts."""

    def __init__(
        self,
        *,
        app_settings: Settings | None = None,
        sandbox: Any,
        hook_manager: RuntimeHookManager | None = None,
    ) -> None:
        self._settings = app_settings or settings
        self._sandbox = sandbox
        self._hook_manager = hook_manager
        self._tool_runtime = ToolRuntime(sandbox=self._sandbox, hook_manager=self._hook_manager)
        self._context_manager = V2ContextManager()

    async def run(
        self,
        *,
        request: IterationRequest,
        run_dir: Path,
    ) -> IterationRunResult:
        run_dir = run_dir.resolve()
        prompts_dir = run_dir / "prompts"
        plans_dir = run_dir / "plans"
        actions_dir = run_dir / "actions"
        queries_dir = run_dir / "queries"
        outputs_dir = run_dir / "outputs"
        trace_dir = run_dir / "trace"
        for path in (prompts_dir, plans_dir, actions_dir, queries_dir, outputs_dir, trace_dir):
            path.mkdir(parents=True, exist_ok=True)
        conversation_trace = trace_dir / "conversation.jsonl"
        tool_timeline_trace = trace_dir / "tool_timeline.jsonl"
        stop_reason_path = trace_dir / "stop_reason.json"
        failure_bundle_path = trace_dir / "failure_bundle.json"

        requirements = request.requirements or {
            "description": "Create a simple CAD model and produce a valid STEP file."
        }
        session_id = request.session_id or f"iter-v2-{__import__('uuid').uuid4()}"
        requirement_text = _stringify_requirements(requirements)
        run_state = RunState(
            session_id=session_id,
            requirements=requirements,
            feature_graph=initialize_domain_kernel_state(requirements),
            previous_error=request.previous_error,
        )
        trace_file = trace_dir / "events.jsonl"
        self._append_trace(
            trace_file,
            "run_started",
            {
                "runtime_mode": "v2",
                "session_id": session_id,
                "max_rounds": request.max_rounds,
                "provider": self._settings.llm_reasoning_provider,
                "model": self._settings.llm_reasoning_model,
            },
        )
        self._write_json(
            trace_dir / "domain_kernel_round_00.json",
            build_domain_kernel_digest(
                run_state.feature_graph,
                include_edges=True,
                include_bindings=True,
                include_revision_history=True,
                max_nodes=40,
                max_edges=40,
                max_bindings=40,
                max_revisions=40,
            )
            if run_state.feature_graph is not None
            else {},
        )

        llm_client = create_provider_client(
            provider=self._settings.llm_reasoning_provider,
            model=self._settings.llm_reasoning_model,
            settings=self._settings,
        )
        converged = False
        stop_reason: dict[str, Any] = {
            "code": "max_rounds_reached",
            "detail": "The run exhausted the configured round budget.",
            "round": None,
        }
        self._append_conversation(
            conversation_trace,
            round_no=0,
            role="system",
            kind="run_started",
            payload={
                "runtime_mode": "v2",
                "session_id": session_id,
                "provider": self._settings.llm_reasoning_provider,
                "model": self._settings.llm_reasoning_model,
            },
        )

        for round_no in range(1, request.max_rounds + 1):
            turn_tool_policy = _determine_turn_tool_policy(
                run_state=run_state,
                round_no=round_no,
                max_rounds=request.max_rounds,
                all_tool_names=self._tool_runtime.list_tool_names(),
                previous_tool_failure_summary=self._context_manager.build_previous_tool_failure_summary(
                    run_state
                ),
            )
            if turn_tool_policy is not None:
                run_state.add_turn_tool_policy(turn_tool_policy)
            allowed_tool_names = _filter_supported_round_tool_names(
                run_state=run_state,
                tool_names=(
                    set(turn_tool_policy.allowed_tool_names)
                    if turn_tool_policy is not None
                    else set(self._tool_runtime.list_tool_names())
                ),
            )
            llm_tools = self._tool_runtime.build_llm_tools(
                allowed_tool_names=allowed_tool_names
            )
            tool_partitions = self._tool_runtime.build_tool_partitions(
                allowed_tool_names=allowed_tool_names
            )
            diagnostics = build_v2_diagnostics(run_state)
            prompt_bundle = self._context_manager.build_prompt_bundle(
                run_state=run_state,
                diagnostics=diagnostics,
                tool_partitions=tool_partitions,
                turn_tool_policy=turn_tool_policy,
                max_rounds=request.max_rounds,
            )
            prompt_payload = prompt_bundle.payload
            messages = prompt_bundle.messages
            compaction_boundary = CompactionBoundary(
                round_no=round_no,
                raw_chars=int(prompt_bundle.compaction_report.get("raw_chars", 0) or 0),
                final_chars=int(prompt_bundle.compaction_report.get("final_chars", 0) or 0),
                was_compacted=bool(prompt_bundle.compaction_report.get("was_compacted")),
                reason=prompt_bundle.compaction_report.get("reason"),
                kept_sections=[
                    str(item)
                    for item in prompt_bundle.compaction_report.get("what_was_kept", [])
                    if isinstance(item, str)
                ],
                summarized_sections=[
                    str(item)
                    for item in prompt_bundle.compaction_report.get("what_was_summarized", [])
                    if isinstance(item, str)
                ],
                post_compact_messages=[
                    str(item)
                    for item in prompt_bundle.compaction_report.get("post_compact_messages", [])
                    if isinstance(item, str)
                ],
            )
            run_state.add_compaction_boundary(compaction_boundary)
            self._append_trace(
                trace_file,
                "round_started",
                {
                    "round": round_no,
                    "prompt_metrics": prompt_bundle.metrics,
                    "turn_status": prompt_payload.get("turn_status"),
                    "evidence_status": prompt_payload.get("evidence_status"),
                    "diagnostics_included": "diagnostics" in prompt_payload,
                },
            )
            if turn_tool_policy is not None:
                self._append_trace(
                    trace_file,
                    "turn_tool_policy",
                    {
                        "round": round_no,
                        "policy_id": turn_tool_policy.policy_id,
                        "mode": turn_tool_policy.mode,
                        "reason": turn_tool_policy.reason,
                        "allowed_tool_names": turn_tool_policy.allowed_tool_names,
                        "blocked_tool_names": turn_tool_policy.blocked_tool_names,
                        "preferred_tool_names": turn_tool_policy.preferred_tool_names,
                        "preferred_probe_families": turn_tool_policy.preferred_probe_families,
                    },
                )
            self._append_trace(
                trace_file,
                "compaction_boundary",
                {
                    "round": round_no,
                    "raw_chars": compaction_boundary.raw_chars,
                    "final_chars": compaction_boundary.final_chars,
                    "was_compacted": compaction_boundary.was_compacted,
                    "reason": compaction_boundary.reason,
                    "kept_sections": compaction_boundary.kept_sections,
                    "summarized_sections": compaction_boundary.summarized_sections,
                    "post_compact_messages": compaction_boundary.post_compact_messages,
                },
            )
            self._append_conversation(
                conversation_trace,
                round_no=round_no,
                role="user",
                kind="context_bundle",
                payload={
                    "turn_status": prompt_payload.get("turn_status"),
                    "evidence_status": prompt_payload.get("evidence_status"),
                    "recent_public_transcript": prompt_payload.get("recent_public_transcript"),
                    "latest_write_health": prompt_payload.get("latest_write_health"),
                    "stall_summary": prompt_payload.get("stall_summary"),
                    "turn_tool_policy": prompt_payload.get("turn_tool_policy"),
                    "compaction_boundary": prompt_bundle.compaction_report,
                },
            )
            self._write_json(
                prompts_dir / f"round_{round_no:02d}_request.json",
                {
                    "round": round_no,
                    "runtime_mode": "v2",
                    "session_id": session_id,
                    "provider": self._settings.llm_reasoning_provider,
                    "model": self._settings.llm_reasoning_model,
                    "context": prompt_payload,
                    "prompt_metrics": prompt_bundle.metrics,
                    "messages": [message.model_dump(mode="json") for message in messages],
                    "compaction_report": prompt_bundle.compaction_report,
                    "tools": [tool.model_dump(mode="json") for tool in llm_tools],
                },
            )
            (prompts_dir / f"round_{round_no:02d}_user_prompt.txt").write_text(
                messages[-1].content if isinstance(messages[-1].content, str) else json.dumps(prompt_payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

            llm_response = None
            llm_error: Exception | None = None
            for attempt in range(_TRANSIENT_MODEL_TIMEOUT_RETRY_COUNT + 1):
                try:
                    llm_response = await self._call_model(
                        client=llm_client,
                        messages=messages,
                        tools=llm_tools,
                    )
                    llm_error = None
                    break
                except TimeoutError as exc:
                    llm_error = exc
                    if attempt >= _TRANSIENT_MODEL_TIMEOUT_RETRY_COUNT:
                        break
                    retry_detail = {
                        "round": round_no,
                        "attempt": attempt + 1,
                        "next_attempt": attempt + 2,
                        "error": f"{exc.__class__.__name__}: {exc}",
                    }
                    self._append_trace(
                        trace_file,
                        "llm_timeout_retry",
                        retry_detail,
                    )
                    run_state.add_agent_event(
                        AgentEvent(
                            kind="llm_timeout_retry",
                            round_no=round_no,
                            role="runtime",
                            payload=retry_detail,
                        )
                    )
                    continue
                except Exception as exc:  # noqa: BLE001
                    llm_error = exc
                    break
            if llm_error is not None or llm_response is None:
                exc = llm_error or RuntimeError("model_call_failed_without_response")
                run_state.llm_error = f"{exc.__class__.__name__}: {exc}"
                stop_reason = {
                    "code": "llm_error",
                    "detail": run_state.llm_error,
                    "round": round_no,
                }
                self._write_json(
                    plans_dir / f"round_{round_no:02d}_response.json",
                    {
                        "round": round_no,
                        "runtime_mode": "v2",
                        "failed": True,
                        "error": run_state.llm_error,
                    },
                )
                break

            _accumulate_usage(run_state, llm_response.usage)
            decision_log = _build_visible_decision_log(round_no=round_no, response=llm_response)
            run_state.add_visible_decision_log(decision_log)
            run_state.add_agent_event(
                AgentEvent(
                    kind="assistant_decision",
                    round_no=round_no,
                    role="assistant",
                    payload={
                        "summary": decision_log.summary,
                        "why_next": decision_log.why_next,
                        "tool_names": decision_log.tool_names,
                        "requested_finish": decision_log.requested_finish,
                    },
                )
            )
            turn = TurnRecord(
                round_no=round_no,
                decision_summary=llm_response.content or "",
            )
            tool_calls = llm_response.tool_calls
            self._append_trace(
                trace_file,
                "model_response_received",
                {
                    "round": round_no,
                    "finish_reason": llm_response.finish_reason,
                    "usage": llm_response.usage,
                    "tool_call_names": [tool_call.name for tool_call in tool_calls],
                    "decision_summary": (llm_response.content or "")[:400],
                    "why_next": decision_log.why_next,
                },
            )
            self._append_conversation(
                conversation_trace,
                round_no=round_no,
                role="assistant",
                kind="decision",
                payload={
                    "decision_summary": decision_log.summary,
                    "why_next": decision_log.why_next,
                    "tool_names": decision_log.tool_names,
                    "requested_finish": decision_log.requested_finish,
                },
            )
            self._write_json(
                plans_dir / f"round_{round_no:02d}_response.json",
                {
                    "round": round_no,
                    "runtime_mode": "v2",
                    "decision_summary": llm_response.content,
                    "decision_log": {
                        "summary": decision_log.summary,
                        "why_next": decision_log.why_next,
                        "tool_names": decision_log.tool_names,
                        "requested_finish": decision_log.requested_finish,
                    },
                    "tool_calls": [tool_call.model_dump(mode="json") for tool_call in tool_calls],
                    "usage": llm_response.usage,
                    "finish_reason": llm_response.finish_reason,
                },
            )

            if not tool_calls:
                turn.error = "model_returned_no_tool_calls"
                run_state.previous_error = turn.error
                run_state.add_turn(turn)
                run_state.add_turn_envelope(
                    TurnEnvelope(
                        round_no=round_no,
                        prompt_metrics=prompt_bundle.metrics,
                        decision_log=decision_log,
                        compaction_boundary=compaction_boundary,
                        turn_tool_policy=turn_tool_policy,
                        stop_reason=turn.error,
                        previous_error=turn.error,
                    )
                )
                self._append_trace(
                    trace_file,
                    "round_error",
                    {"round": round_no, "error": turn.error},
                )
                continue

            self._append_trace(
                trace_file,
                "tool_batch_started",
                {
                    "round": round_no,
                    "tool_calls": [
                        {
                            "name": tool_call.name,
                            "arguments": tool_call.arguments,
                        }
                        for tool_call in tool_calls
                    ],
                },
            )
            batch_result = await self._tool_runtime.execute_tool_calls(
                tool_calls=tool_calls,
                session_id=session_id,
                requirements=requirements,
                requirement_text=requirement_text,
                sandbox_timeout=request.sandbox_timeout,
                round_no=round_no,
                run_state=run_state,
                allowed_tool_names=allowed_tool_names,
            )
            turn.tool_calls = list(batch_result.tool_calls)
            turn.tool_results = list(batch_result.tool_results)
            turn.requested_finish = batch_result.requested_finish
            turn.error = batch_result.error
            run_state.add_tool_execution_events(list(batch_result.execution_events))
            for tool_event in batch_result.execution_events:
                self._append_tool_timeline(tool_timeline_trace, tool_event)

            for result in turn.tool_results:
                context_events = self._persist_tool_result(
                    result=result,
                    round_no=round_no,
                    actions_dir=actions_dir,
                    queries_dir=queries_dir,
                    outputs_dir=outputs_dir,
                    run_state=run_state,
                )
                run_state.add_tool_execution_events(context_events)
                for tool_event in context_events:
                    self._append_tool_timeline(tool_timeline_trace, tool_event)
                self._append_trace(
                    trace_file,
                    "tool_result",
                    {
                        "round": round_no,
                        "tool_name": result.name,
                        "category": result.category.value,
                        "success": result.success,
                        "error": result.error,
                        "artifact_files": result.artifact_files,
                        "step_file": result.step_file,
                        "payload_summary": _trace_payload_summary(result.payload),
                    },
                )
                self._append_conversation(
                    conversation_trace,
                    round_no=round_no,
                    role="user",
                    kind="tool_result",
                    payload={
                        "tool_name": result.name,
                        "category": result.category.value,
                        "success": result.success,
                        "error": result.error,
                        "payload_summary": _trace_payload_summary(result.payload),
                    },
                )
                self._write_feature_graph_trace(
                    trace_dir=trace_dir,
                    run_state=run_state,
                    round_no=round_no,
                )

            if batch_result.error:
                run_state.previous_error = batch_result.error
                run_state.add_turn(turn)
                run_state.add_turn_envelope(
                    TurnEnvelope(
                        round_no=round_no,
                        prompt_metrics=prompt_bundle.metrics,
                        decision_log=decision_log,
                        tool_calls=turn.tool_calls,
                        tool_results=turn.tool_results,
                        compaction_boundary=compaction_boundary,
                        turn_tool_policy=turn_tool_policy,
                        stop_reason=batch_result.error,
                        previous_error=batch_result.error,
                    )
                )
                self._append_trace(
                    trace_file,
                    "tool_batch_error",
                    {"round": round_no, "error": batch_result.error},
                )
                continue
            if _should_stop_after_terminal_code_path(turn):
                run_state.previous_error = (
                    "execute_build123d_terminal_without_session_validation"
                )
                run_state.add_turn(turn)
                run_state.add_turn_envelope(
                    TurnEnvelope(
                        round_no=round_no,
                        prompt_metrics=prompt_bundle.metrics,
                        decision_log=decision_log,
                        tool_calls=turn.tool_calls,
                        tool_results=turn.tool_results,
                        compaction_boundary=compaction_boundary,
                        turn_tool_policy=turn_tool_policy,
                        stop_reason=run_state.previous_error,
                        previous_error=run_state.previous_error,
                    )
                )
                stop_reason = {
                    "code": "terminal_code_path_without_session_validation",
                    "detail": run_state.previous_error,
                    "round": round_no,
                }
                self._append_trace(
                    trace_file,
                    "round_completed",
                    {
                        "round": round_no,
                        "requested_finish": batch_result.requested_finish,
                        "write_tool_names": [
                            result.name
                            for result in turn.tool_results
                            if result.category.value == "write"
                        ],
                        "inspection_only": turn.read_only,
                        "previous_error": run_state.previous_error,
                    },
                )
                break
            if any(_is_environment_blocker(result.error) for result in turn.tool_results):
                run_state.previous_error = next(
                    (
                        result.error
                        for result in turn.tool_results
                        if _is_environment_blocker(result.error)
                    ),
                    "environment_blocker",
                )
                run_state.add_turn(turn)
                run_state.add_turn_envelope(
                    TurnEnvelope(
                        round_no=round_no,
                        prompt_metrics=prompt_bundle.metrics,
                        decision_log=decision_log,
                        tool_calls=turn.tool_calls,
                        tool_results=turn.tool_results,
                        compaction_boundary=compaction_boundary,
                        turn_tool_policy=turn_tool_policy,
                        stop_reason=run_state.previous_error,
                        previous_error=run_state.previous_error,
                    )
                )
                stop_reason = {
                    "code": "environment_blocker",
                    "detail": run_state.previous_error,
                    "round": round_no,
                }
                self._append_trace(
                    trace_file,
                    "round_completed",
                    {
                        "round": round_no,
                        "requested_finish": batch_result.requested_finish,
                        "write_tool_names": [
                            result.name
                            for result in turn.tool_results
                            if result.category.value == "write"
                        ],
                        "inspection_only": turn.read_only,
                        "previous_error": run_state.previous_error,
                    },
                )
                break

            run_state.add_turn(turn)
            if (
                not batch_result.requested_finish
                and _should_auto_validate_after_post_write(
                    run_state=run_state,
                    turn=turn,
                    round_no=round_no,
                    max_rounds=request.max_rounds,
                )
            ):
                validation_core = await self._execute_runtime_validation(
                    run_state=run_state,
                    session_id=session_id,
                    requirements=requirements,
                    requirement_text=requirement_text,
                    timeout=min(_RUNTIME_VALIDATION_TIMEOUT_SECONDS, request.sandbox_timeout),
                    round_no=round_no,
                    trigger="post_write_probe",
                    queries_dir=queries_dir,
                    trace_dir=trace_dir,
                    trace_file=trace_file,
                    conversation_trace=conversation_trace,
                    query_filename=f"round_{round_no:02d}_validate_requirement_post_write.json",
                )
                if _is_successful_validation(validation_core):
                    converged = True
                    run_state.previous_error = None
                    run_state.add_turn_envelope(
                        TurnEnvelope(
                            round_no=round_no,
                            prompt_metrics=prompt_bundle.metrics,
                            decision_log=decision_log,
                            tool_calls=turn.tool_calls,
                            tool_results=turn.tool_results,
                            compaction_boundary=compaction_boundary,
                            turn_tool_policy=turn_tool_policy,
                            stop_reason="post_write_validated_complete",
                            previous_error=None,
                        )
                    )
                    stop_reason = {
                        "code": "post_write_validated_complete",
                        "detail": str(
                            validation_core.get("summary") or "validated complete"
                        ),
                        "round": round_no,
                    }
                    self._append_round_completed_trace(
                        trace_file=trace_file,
                        round_no=round_no,
                        requested_finish=batch_result.requested_finish,
                        turn=turn,
                        previous_error=run_state.previous_error,
                    )
                    break
            if batch_result.requested_finish:
                validation_core = await self._execute_runtime_validation(
                    run_state=run_state,
                    session_id=session_id,
                    requirements=requirements,
                    requirement_text=requirement_text,
                    timeout=min(_RUNTIME_VALIDATION_TIMEOUT_SECONDS, request.sandbox_timeout),
                    round_no=round_no,
                    trigger="finish_run",
                    queries_dir=queries_dir,
                    trace_dir=trace_dir,
                    trace_file=trace_file,
                    conversation_trace=conversation_trace,
                    query_filename=f"round_{round_no:02d}_validate_requirement_final.json",
                )
                if _is_successful_validation(validation_core):
                    converged = True
                    run_state.previous_error = None
                    run_state.add_turn_envelope(
                        TurnEnvelope(
                            round_no=round_no,
                            prompt_metrics=prompt_bundle.metrics,
                            decision_log=decision_log,
                            tool_calls=turn.tool_calls,
                            tool_results=turn.tool_results,
                            compaction_boundary=compaction_boundary,
                            turn_tool_policy=turn_tool_policy,
                            stop_reason="finish_validated_complete",
                            previous_error=None,
                        )
                    )
                    stop_reason = {
                        "code": "finish_validated_complete",
                        "detail": str(validation_core.get("summary") or "validated complete"),
                        "round": round_no,
                    }
                    self._append_round_completed_trace(
                        trace_file=trace_file,
                        round_no=round_no,
                        requested_finish=True,
                        turn=turn,
                        previous_error=run_state.previous_error,
                    )
                    break
                run_state.previous_error = (
                    validation_core.get("summary")
                    if isinstance(validation_core.get("summary"), str)
                    else "finish_requested_but_validation_incomplete"
                )
                run_state.add_turn_envelope(
                    TurnEnvelope(
                        round_no=round_no,
                        prompt_metrics=prompt_bundle.metrics,
                        decision_log=decision_log,
                        tool_calls=turn.tool_calls,
                        tool_results=turn.tool_results,
                        compaction_boundary=compaction_boundary,
                        turn_tool_policy=turn_tool_policy,
                        stop_reason="finish_requested_but_validation_incomplete",
                        previous_error=run_state.previous_error,
                    )
                )
                self._append_round_completed_trace(
                    trace_file=trace_file,
                    round_no=round_no,
                    requested_finish=True,
                    turn=turn,
                    previous_error=run_state.previous_error,
                )
                continue

            if _should_auto_validate_after_non_progress(run_state):
                validation_core = await self._execute_runtime_validation(
                    run_state=run_state,
                    session_id=session_id,
                    requirements=requirements,
                    requirement_text=requirement_text,
                    timeout=min(_RUNTIME_VALIDATION_TIMEOUT_SECONDS, request.sandbox_timeout),
                    round_no=round_no,
                    trigger="non_progress",
                    queries_dir=queries_dir,
                    trace_dir=trace_dir,
                    trace_file=trace_file,
                    conversation_trace=conversation_trace,
                    query_filename=f"round_{round_no:02d}_validate_requirement_auto.json",
                )
                if _is_successful_validation(validation_core):
                    converged = True
                    run_state.previous_error = None
                    run_state.add_turn_envelope(
                        TurnEnvelope(
                            round_no=round_no,
                            prompt_metrics=prompt_bundle.metrics,
                            decision_log=decision_log,
                            tool_calls=turn.tool_calls,
                            tool_results=turn.tool_results,
                            compaction_boundary=compaction_boundary,
                            turn_tool_policy=turn_tool_policy,
                            stop_reason="auto_validated_complete",
                            previous_error=None,
                        )
                    )
                    stop_reason = {
                        "code": "auto_validated_complete",
                        "detail": str(
                            validation_core.get("summary") or "validated complete"
                        ),
                        "round": round_no,
                    }
                    self._append_round_completed_trace(
                        trace_file=trace_file,
                        round_no=round_no,
                        requested_finish=batch_result.requested_finish,
                        turn=turn,
                        previous_error=run_state.previous_error,
                    )
                    break

            run_state.add_turn_envelope(
                TurnEnvelope(
                    round_no=round_no,
                    prompt_metrics=prompt_bundle.metrics,
                    decision_log=decision_log,
                    tool_calls=turn.tool_calls,
                    tool_results=turn.tool_results,
                    compaction_boundary=compaction_boundary,
                    turn_tool_policy=turn_tool_policy,
                    previous_error=run_state.previous_error,
                )
            )
            self._append_round_completed_trace(
                trace_file=trace_file,
                round_no=round_no,
                requested_finish=batch_result.requested_finish,
                turn=turn,
                previous_error=run_state.previous_error,
            )

        if run_state.latest_validation is None and run_state.latest_step_file:
            validation_core = await self._execute_runtime_validation(
                run_state=run_state,
                session_id=session_id,
                requirements=requirements,
                requirement_text=requirement_text,
                timeout=min(_RUNTIME_VALIDATION_TIMEOUT_SECONDS, request.sandbox_timeout),
                round_no=None,
                trigger="final_missing_validation",
                queries_dir=queries_dir,
                trace_dir=trace_dir,
                trace_file=trace_file,
                conversation_trace=conversation_trace,
                query_filename="final_validate_requirement.json",
                evidence_round_no=0,
            )
            if _is_successful_validation(validation_core):
                converged = True
                run_state.previous_error = None
                stop_reason = {
                    "code": "final_missing_validation_completed",
                    "detail": str(validation_core.get("summary") or "validated complete"),
                    "round": None,
                }
            elif stop_reason.get("code") == "max_rounds_reached":
                stop_reason = {
                    "code": "final_missing_validation_incomplete",
                    "detail": str(validation_core.get("summary") or "validation incomplete"),
                    "round": None,
                }

        step_file_exists = bool(run_state.latest_step_file) and (
            outputs_dir / run_state.latest_step_file
        ).exists()
        render_file = _pick_render_file(run_state.latest_output_files)
        render_file_exists = bool(render_file) and (outputs_dir / str(render_file)).exists()
        summary = IterationRunSummary(
            session_id=session_id,
            provider=self._settings.llm_reasoning_provider,
            model=self._settings.llm_reasoning_model,
            planner_rounds=len(run_state.turns),
            executed_action_count=run_state.executed_action_count,
            executed_action_types=run_state.executed_action_types,
            converged=converged,
            validation_complete=_is_successful_validation(run_state.latest_validation),
            step_file_exists=step_file_exists,
            render_file_exists=render_file_exists,
            render_image_attached_for_prompt=False,
            inspection_only_rounds=run_state.inspection_only_rounds,
            inspection_requested_rounds=run_state.inspection_only_rounds,
            no_op_action_count=0,
            token_usage=run_state.token_usage,
            reasoning_log_available=bool(run_state.visible_decision_logs),
            tool_event_count=len(run_state.tool_execution_events),
            compaction_count=sum(1 for item in run_state.compaction_boundaries if item.was_compacted),
            validation_call_count=run_state.validation_call_count,
            read_only_turn_count=run_state.inspection_only_rounds,
            primary_write_mode="code_first",
            first_write_tool=run_state.first_write_tool,
            structured_bootstrap_rounds=run_state.structured_bootstrap_rounds,
            stale_probe_carry_count=run_state.stale_probe_carry_count,
            evidence_conflict_count=run_state.evidence_conflict_count,
            freshness_conflict_count=run_state.evidence_conflict_count,
            forced_policy_chain=run_state.forced_policy_chain,
            feature_probe_count=run_state.feature_probe_count,
            probe_code_count=run_state.probe_code_count,
            build123d_hallucination=build_run_hallucination_summary(run_state),
            baseline_comparison={},
            failure_cluster=_infer_runtime_failure_cluster(run_state),
            llm_error=run_state.llm_error,
            last_error=run_state.previous_error,
            runtime_mode_effective="v2",
        )
        result = IterationRunResult(
            run_dir=str(run_dir),
            summary=summary,
            request=request,
        )
        self._write_json(run_dir / "summary.json", result.model_dump(mode="json"))
        self._write_json(
            stop_reason_path,
            {
                "session_id": session_id,
                "stop_reason": stop_reason,
                "converged": converged,
                "validation_complete": summary.validation_complete,
                "last_error": run_state.previous_error,
                "planner_rounds": len(run_state.turns),
            },
        )
        self._write_json(
            failure_bundle_path,
            _build_failure_bundle(
                run_state=run_state,
                run_dir=run_dir,
                stop_reason=stop_reason,
            ),
        )
        self._append_trace(
            trace_file,
            "run_finished",
            {
                "converged": converged,
                "planner_rounds": len(run_state.turns),
                "step_file_exists": step_file_exists,
                "validation_complete": summary.validation_complete,
            },
        )
        self._append_conversation(
            conversation_trace,
            round_no=stop_reason.get("round"),
            role="runtime",
            kind="run_finished",
            payload={
                "stop_reason": stop_reason,
                "converged": converged,
                "validation_complete": summary.validation_complete,
                "planner_rounds": len(run_state.turns),
            },
        )
        return result

    async def _call_model(
        self,
        *,
        client: Any,
        messages: list[LLMMessage],
        tools: list[Any],
    ) -> LLMToolResponse:
        if getattr(client, "supports_tool_calling", False) and hasattr(
            client, "complete_with_tools"
        ):
            return await client.complete_with_tools(
                messages=messages,
                tools=tools,
                tool_choice="auto",
                temperature=0.2,
                max_tokens=None,
            )
        fallback_messages = list(messages)
        fallback_messages.append(
            LLMMessage(
                role="user",
                content=(
                    "Native tool calling is unavailable. "
                    "Respond with strict JSON: "
                    '{"decision_summary": "...", "tool_calls": [{"name": "...", "arguments": {...}}]}'
                ),
            )
        )
        response = await client.complete(
            fallback_messages,
            temperature=0.2,
            max_tokens=None,
        )
        return _parse_tool_envelope(response)

    def _write_feature_graph_trace(
        self,
        *,
        trace_dir: Path,
        run_state: RunState,
        round_no: int | None,
    ) -> None:
        if run_state.feature_graph is None:
            return
        target = (
            trace_dir / "domain_kernel_final.json"
            if round_no is None
            else trace_dir / f"domain_kernel_round_{round_no:02d}.json"
        )
        self._write_json(
            target,
            build_domain_kernel_digest(
                run_state.feature_graph,
                include_edges=True,
                include_bindings=True,
                include_revision_history=True,
                max_nodes=60,
                max_edges=60,
                max_bindings=60,
                max_revisions=60,
            ),
        )

    async def _execute_runtime_validation(
        self,
        *,
        run_state: RunState,
        session_id: str,
        requirements: dict[str, Any],
        requirement_text: str,
        timeout: int,
        round_no: int | None,
        trigger: str,
        queries_dir: Path,
        trace_dir: Path,
        trace_file: Path,
        conversation_trace: Path,
        query_filename: str,
        evidence_round_no: int | None = None,
    ) -> dict[str, Any]:
        self._append_trace(
            trace_file,
            "validation_requested",
            {"round": round_no, "trigger": trigger},
        )
        validation = await self._sandbox.validate_requirement(
            session_id=session_id,
            requirements=requirements,
            requirement_text=requirement_text,
            timeout=timeout,
        )
        validation_payload = _to_jsonable(validation)
        validation_core, _validation_diagnostics = split_validation_feedback(
            validation_payload
        )
        validation_runtime_payload = build_runtime_validation_payload(validation_payload)
        run_state.add_agent_event(
            AgentEvent(
                kind="validation_result",
                round_no=round_no,
                role="runtime",
                payload={
                    "trigger": trigger,
                    "summary": validation_core.get("summary"),
                    "is_complete": validation_core.get("is_complete"),
                    "blockers": validation_core.get("blockers"),
                },
            )
        )
        run_state.latest_validation = validation_runtime_payload
        run_state.evidence.update(
            tool_name="validate_requirement",
            payload=validation_runtime_payload,
            round_no=evidence_round_no if evidence_round_no is not None else round_no,
        )
        self._sync_feature_graph_from_runtime_payload(
            run_state=run_state,
            tool_name="validate_requirement",
            payload=validation_runtime_payload,
            round_no=evidence_round_no if evidence_round_no is not None else (round_no or 0),
        )
        self._write_json(queries_dir / query_filename, validation_payload)
        self._write_feature_graph_trace(
            trace_dir=trace_dir,
            run_state=run_state,
            round_no=round_no,
        )
        self._append_trace(
            trace_file,
            "validation_result",
            {
                "round": round_no,
                "trigger": trigger,
                "summary": validation_core.get("summary"),
                "is_complete": validation_core.get("is_complete"),
                "blockers": validation_core.get("blockers"),
            },
        )
        self._append_conversation(
            conversation_trace,
            round_no=round_no,
            role="runtime",
            kind="validation_result",
            payload={
                "trigger": trigger,
                "summary": validation_core.get("summary"),
                "is_complete": validation_core.get("is_complete"),
                "blockers": validation_core.get("blockers"),
            },
        )
        return validation_core

    def _append_round_completed_trace(
        self,
        *,
        trace_file: Path,
        round_no: int,
        requested_finish: bool,
        turn: TurnRecord,
        previous_error: str | None,
    ) -> None:
        self._append_trace(
            trace_file,
            "round_completed",
            {
                "round": round_no,
                "requested_finish": requested_finish,
                "write_tool_names": [
                    result.name
                    for result in turn.tool_results
                    if result.category.value == "write"
                ],
                "inspection_only": turn.read_only,
                "previous_error": previous_error,
            },
        )

    def _persist_tool_result(
        self,
        *,
        result: Any,
        round_no: int,
        actions_dir: Path,
        queries_dir: Path,
        outputs_dir: Path,
        run_state: RunState,
    ) -> list[ToolExecutionEvent]:
        context_events: list[ToolExecutionEvent] = []
        target_dir = actions_dir if result.category.value == "write" else queries_dir
        self._write_json(
            target_dir / f"round_{round_no:02d}_{result.name}.json",
            result.payload,
        )
        artifact_dir = (
            outputs_dir
            if result.name in _CANONICAL_OUTPUT_ARTIFACT_TOOL_NAMES
            else target_dir / f"round_{round_no:02d}_{result.name}_artifacts"
        )
        for filename, content in result.artifact_contents.items():
            output_path = artifact_dir / filename
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(content)
        if (
            result.success
            and result.artifact_files
            and result.name in _CANONICAL_OUTPUT_ARTIFACT_TOOL_NAMES
        ):
            run_state.latest_output_files = list(
                dict.fromkeys([*run_state.latest_output_files, *result.artifact_files])
            )
        if result.success and result.step_file and result.name in {
            "apply_cad_action",
            "execute_build123d",
            "execute_repair_packet",
        }:
            run_state.latest_step_file = result.step_file
        if result.name == "validate_requirement":
            _validation_core, _validation_diagnostics = split_validation_feedback(
                result.payload
            )
            run_state.latest_validation = build_runtime_validation_payload(result.payload)
        if result.name == "render_view":
            run_state.latest_render_view = result.payload
        if result.name in {
            "query_kernel_state",
            "query_snapshot",
            "query_sketch",
            "query_geometry",
            "query_topology",
            "query_feature_probes",
            "validate_requirement",
            "render_view",
            "get_history",
            "execute_build123d_probe",
        }:
            payload_for_loop = result.payload
            if result.name == "validate_requirement":
                payload_for_loop = build_runtime_validation_payload(result.payload)
            run_state.evidence.update(
                tool_name=result.name,
                payload=payload_for_loop,
                artifact_files=result.artifact_files,
                round_no=round_no,
            )
        if result.name == "apply_cad_action":
            run_state.latest_write_payload = result.payload
            if result.success:
                run_state.latest_validation = None
                run_state.latest_render_view = None
                invalidated_tools = list(_SUCCESSFUL_WRITE_INVALIDATED_EVIDENCE)
                run_state.evidence.invalidate(
                    *invalidated_tools,
                )
                context_events.append(
                    ToolExecutionEvent(
                        round_no=round_no,
                        tool_name=result.name,
                        phase="context_mutation",
                        category=result.category,
                        success=result.success,
                        detail={"invalidated_evidence": invalidated_tools},
                    )
                )
                if isinstance(result.payload.get("action_history"), list):
                    run_state.action_history = [
                        item
                        for item in result.payload["action_history"]
                        if isinstance(item, dict)
                    ]
                snapshot = result.payload.get("snapshot")
                if isinstance(snapshot, dict):
                    run_state.evidence.update(
                        tool_name="query_snapshot",
                        payload={"success": True, "snapshot": snapshot},
                        round_no=round_no,
                    )
            else:
                context_events.append(
                    ToolExecutionEvent(
                        round_no=round_no,
                        tool_name=result.name,
                        phase="context_mutation",
                        category=result.category,
                        success=result.success,
                        detail={
                            "retained_evidence": [
                                "validate_requirement",
                                "query_geometry",
                                "query_topology",
                                "query_sketch",
                                "render_view",
                                "get_history",
                            ],
                            "reason": "write_failed",
                        },
                    )
                )
        if result.name in {"execute_build123d", "execute_repair_packet"}:
            run_state.latest_write_payload = result.payload
            if result.success:
                run_state.latest_validation = None
                run_state.latest_render_view = None
                invalidated_tools = list(_SUCCESSFUL_WRITE_INVALIDATED_EVIDENCE)
                run_state.evidence.invalidate(
                    *invalidated_tools,
                )
                context_events.append(
                    ToolExecutionEvent(
                        round_no=round_no,
                        tool_name=result.name,
                        phase="context_mutation",
                        category=result.category,
                        success=result.success,
                        detail={"invalidated_evidence": invalidated_tools},
                    )
                )
                if not run_state.latest_step_file:
                    run_state.latest_step_file = _pick_step_file(result.artifact_files)
                snapshot = result.payload.get("snapshot")
                if isinstance(snapshot, dict):
                    run_state.evidence.update(
                        tool_name="query_snapshot",
                        payload={
                            "success": True,
                            "session_id": result.payload.get("session_id"),
                            "step": result.payload.get("step"),
                            "snapshot": snapshot,
                            "action_history": [],
                        },
                        round_no=round_no,
                    )
            else:
                context_events.append(
                    ToolExecutionEvent(
                        round_no=round_no,
                        tool_name=result.name,
                        phase="context_mutation",
                        category=result.category,
                        success=result.success,
                        detail={
                            "retained_evidence": [
                                "validate_requirement",
                                "query_geometry",
                                "query_topology",
                                "query_sketch",
                                "render_view",
                                "get_history",
                            ],
                            "reason": "write_failed",
                        },
                    )
                )
        run_state.previous_error = result.error
        payload_for_feature_graph_sync = result.payload
        if result.name == "validate_requirement":
            payload_for_feature_graph_sync = build_runtime_validation_payload(result.payload)
        self._sync_feature_graph_from_runtime_payload(
            run_state=run_state,
            tool_name=result.name,
            payload=payload_for_feature_graph_sync,
            round_no=round_no,
        )
        return context_events

    def _sync_feature_graph_from_runtime_payload(
        self,
        *,
        run_state: RunState,
        tool_name: str,
        payload: dict[str, Any],
        round_no: int,
    ) -> None:
        if run_state.feature_graph is None or not isinstance(payload, dict):
            return
        feature_graph, validation = sync_domain_kernel_state_from_tool_result(
            run_state.feature_graph,
            tool_name=tool_name,
            payload=payload,
            round_no=round_no,
            fallback_latest_validation=run_state.latest_validation,
        )
        run_state.feature_graph = feature_graph
        if tool_name == "query_kernel_state":
            return
        synthetic_kernel_digest = {
            **build_domain_kernel_digest(feature_graph),
            "success": bool(validation.get("ok")),
            "_synthetic_kernel_sync": True,
            "source_tool": tool_name,
            "freshness_source_round": round_no,
        }
        run_state.evidence.update(
            tool_name="query_kernel_state",
            payload=synthetic_kernel_digest,
            round_no=round_no,
        )

    def _write_json(self, path: Path, payload: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, default=_json_default),
            encoding="utf-8",
        )

    def _append_trace(self, trace_file: Path, event_type: str, payload: dict[str, Any]) -> None:
        trace_file.parent.mkdir(parents=True, exist_ok=True)
        with trace_file.open("a", encoding="utf-8") as handle:
            handle.write(
                json.dumps(
                    {
                        "timestamp": dt.datetime.now(dt.UTC).isoformat(),
                        "event_type": event_type,
                        "payload": payload,
                    },
                    ensure_ascii=False,
                    default=_json_default,
                )
            )
            handle.write("\n")

    def _append_conversation(
        self,
        path: Path,
        *,
        round_no: int | None,
        role: str,
        kind: str,
        payload: dict[str, Any],
    ) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(
                json.dumps(
                    {
                        "timestamp": dt.datetime.now(dt.UTC).isoformat(),
                        "round": round_no,
                        "role": role,
                        "kind": kind,
                        "payload": payload,
                    },
                    ensure_ascii=False,
                    default=_json_default,
                )
            )
            handle.write("\n")

    def _append_tool_timeline(self, path: Path, event: ToolExecutionEvent) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(
                json.dumps(
                    {
                        "timestamp": dt.datetime.now(dt.UTC).isoformat(),
                        "round": event.round_no,
                        "tool_name": event.tool_name,
                        "phase": event.phase,
                        "category": event.category.value if event.category else None,
                        "success": event.success,
                        "detail": event.detail,
                    },
                    ensure_ascii=False,
                    default=_json_default,
                )
            )
            handle.write("\n")


def _accumulate_usage(run_state: RunState, usage: dict[str, Any] | None) -> None:
    if not isinstance(usage, dict):
        return
    input_tokens = int(usage.get("input_tokens", 0) or 0)
    output_tokens = int(usage.get("output_tokens", 0) or 0)
    total_tokens = int(usage.get("total_tokens", input_tokens + output_tokens) or 0)
    run_state.token_usage["input_tokens"] += input_tokens
    run_state.token_usage["output_tokens"] += output_tokens
    run_state.token_usage["total_tokens"] += total_tokens
    run_state.token_usage["rounds_with_usage"] += 1


def _stringify_requirements(requirements: dict[str, Any]) -> str:
    description = requirements.get("description")
    if isinstance(description, str) and description.strip():
        return description.strip()
    return json.dumps(requirements, ensure_ascii=False, indent=2)


def _build_visible_decision_log(
    *,
    round_no: int,
    response: LLMToolResponse,
) -> VisibleDecisionLog:
    content = (response.content or "").strip()
    summary = content.splitlines()[0].strip() if content else "tool-driven round"
    summary = summary[:240]
    tool_names = [tool_call.name for tool_call in response.tool_calls]
    why_next = None
    if tool_names:
        why_next = f"Selected tools: {', '.join(tool_names)}"
    return VisibleDecisionLog(
        round_no=round_no,
        summary=summary or "tool-driven round",
        why_next=why_next,
        tool_names=tool_names,
        requested_finish=any(tool_name == "finish_run" for tool_name in tool_names),
    )


def _build_failure_bundle(
    *,
    run_state: RunState,
    run_dir: Path,
    stop_reason: dict[str, Any],
) -> dict[str, Any]:
    last_good_write = None
    if run_state.latest_write_turn is not None:
        last_good_write = {
            "round": run_state.latest_write_turn.round_no,
            "tool": run_state.latest_write_turn.write_tool_name,
            "decision_summary": run_state.latest_write_turn.decision_summary,
        }
    last_turn = run_state.turns[-1] if run_state.turns else None
    return {
        "session_id": run_state.session_id,
        "stop_reason": stop_reason,
        "last_error": run_state.previous_error,
        "last_good_write": last_good_write,
        "last_bad_turn": {
            "round": last_turn.round_no if last_turn else None,
            "decision_summary": last_turn.decision_summary if last_turn else None,
            "error": last_turn.error if last_turn else None,
            "tool_calls": [
                {"name": tool.name, "category": tool.category.value}
                for tool in (last_turn.tool_calls if last_turn else [])
            ],
        },
        "recent_decision_logs": [
            {
                "round": log.round_no,
                "summary": log.summary,
                "why_next": log.why_next,
                "tool_names": log.tool_names,
                "requested_finish": log.requested_finish,
            }
            for log in run_state.visible_decision_logs[-3:]
        ],
        "recent_validation": run_state.latest_validation,
        "recent_turn_policies": [
            {
                "round": policy.round_no,
                "policy_id": policy.policy_id,
                "mode": policy.mode,
                "reason": policy.reason,
                "allowed_tool_names": policy.allowed_tool_names,
                "blocked_tool_names": policy.blocked_tool_names,
                "preferred_tool_names": policy.preferred_tool_names,
                "preferred_probe_families": policy.preferred_probe_families,
            }
            for policy in run_state.turn_tool_policies[-3:]
        ],
        "trace_files": {
            "events": str((run_dir / "trace" / "events.jsonl").resolve()),
            "conversation": str((run_dir / "trace" / "conversation.jsonl").resolve()),
            "tool_timeline": str((run_dir / "trace" / "tool_timeline.jsonl").resolve()),
        },
    }


_PROBE_FIRST_TOOL_SET = {
    "query_kernel_state",
    "query_feature_probes",
    "execute_build123d_probe",
}

_ARTIFACTLESS_PROBE_FIRST_TOOL_SET = {
    "query_kernel_state",
    "execute_build123d_probe",
}

_FAMILY_PROBE_FIRST_AFTER_CODE_WRITE_TOOL_SET = {
    "query_kernel_state",
    "query_feature_probes",
    "execute_build123d_probe",
}

_GRAPH_REFRESH_TOOL_SET = {
    "query_kernel_state",
    "query_feature_probes",
    "execute_build123d_probe",
    "validate_requirement",
    "finish_run",
}

_CODE_FIRST_ESCAPE_TOOL_SET = {
    "execute_build123d",
    "query_kernel_state",
    "query_feature_probes",
    "execute_build123d_probe",
}

_CODE_CLOSURE_AFTER_ADMISSION_TOOL_SET = {
    "execute_build123d",
    "query_feature_probes",
    "execute_build123d_probe",
}

_SEMANTIC_ADMISSION_TOOL_SET = {
    "query_kernel_state",
    "query_feature_probes",
    "execute_build123d_probe",
    "validate_requirement",
}

_SEMANTIC_REFRESH_QUERY_TOOL_SET = {"query_kernel_state"}
_SEMANTIC_REFRESH_COMPLETION_TOOL_SET = {
    "query_kernel_state",
    "query_feature_probes",
}
_SEMANTIC_REFRESH_REPAIR_TOOL_SET = {
    "query_kernel_state",
    "query_feature_probes",
    "execute_build123d_probe",
}


def _determine_turn_tool_policy(
    *,
    run_state: RunState,
    round_no: int,
    max_rounds: int,
    all_tool_names: list[str],
    previous_tool_failure_summary: dict[str, Any] | None,
) -> TurnToolPolicy | None:
    if not run_state.turns:
        allowed_tool_names = [
            name for name in all_tool_names if name == "execute_build123d"
        ]
        blocked_tool_names = [
            name for name in all_tool_names if name not in set(allowed_tool_names)
        ]
        return TurnToolPolicy(
            round_no=round_no,
            policy_id="code_first_build",
            mode="code_first",
            reason=(
                "V2 defaults to a code-first initial write. Start with execute_build123d "
                "instead of opening a structured bootstrap chain."
            ),
            allowed_tool_names=allowed_tool_names,
            blocked_tool_names=blocked_tool_names,
            preferred_tool_names=["execute_build123d"],
            preferred_probe_families=[],
        )

    preferred_probe_families = _preferred_probe_families_for_turn(run_state)

    incomplete_finish_round = _latest_incomplete_finish_round(run_state)
    if incomplete_finish_round is not None and not _has_tool_turn_since_round(
        run_state,
        after_round=incomplete_finish_round,
        tool_names=_SEMANTIC_REFRESH_QUERY_TOOL_SET,
    ):
        allowed_tool_names = [
            name for name in all_tool_names if name in _GRAPH_REFRESH_TOOL_SET
        ]
        blocked_tool_names = [
            name for name in all_tool_names if name not in _GRAPH_REFRESH_TOOL_SET
        ]
        return TurnToolPolicy(
            round_no=round_no,
            policy_id="graph_refresh_after_incomplete_finish",
            mode="graph_refresh",
            reason=(
                "A finish attempt ended with incomplete validation, so refresh semantic state "
                "with query_kernel_state before more reads or another finish."
            ),
            allowed_tool_names=allowed_tool_names,
            blocked_tool_names=blocked_tool_names,
            preferred_tool_names=[
                "query_kernel_state",
                "query_feature_probes",
            ],
            preferred_probe_families=preferred_probe_families,
        )

    sketch_window_action_type = _latest_successful_apply_action_type_with_open_sketch_window(
        run_state
    )
    if isinstance(sketch_window_action_type, str) and sketch_window_action_type.strip():
        if _open_sketch_window_requires_code_escape(
            run_state=run_state,
            max_rounds=max_rounds,
        ):
            allowed_tool_names = [
                name for name in all_tool_names if name in _CODE_FIRST_ESCAPE_TOOL_SET
            ]
            blocked_tool_names = [
                name for name in all_tool_names if name not in _CODE_FIRST_ESCAPE_TOOL_SET
            ]
            return TurnToolPolicy(
                round_no=round_no,
                policy_id="code_escape_after_open_sketch_window_under_budget",
                mode="code_first",
                reason=(
                    "The bounded sketch window cannot be completed within the remaining round "
                    "budget, so switch to a whole-part code repair instead of extending the "
                    "local-finish tail."
                ),
                allowed_tool_names=allowed_tool_names,
                blocked_tool_names=blocked_tool_names,
                preferred_tool_names=[
                    name
                    for name in (
                        "execute_build123d",
                        "query_feature_probes",
                        "query_kernel_state",
                        "execute_build123d_probe",
                    )
                    if name in allowed_tool_names
                ],
                preferred_probe_families=preferred_probe_families,
            )
        allowed_tool_names = [
            name for name in all_tool_names if name in {"apply_cad_action", "query_sketch"}
        ]
        blocked_tool_names = [
            name for name in all_tool_names if name not in set(allowed_tool_names)
        ]
        return TurnToolPolicy(
            round_no=round_no,
            policy_id="continue_open_sketch_window_after_apply_action",
            mode="local_finish",
            reason=(
                "The latest successful apply_cad_action opened or extended a sketch window, "
                "so continue that bounded sketch lane before validation or broader semantic reads."
            ),
            allowed_tool_names=allowed_tool_names,
            blocked_tool_names=blocked_tool_names,
            preferred_tool_names=_preferred_sketch_window_tools(
                sketch_window_action_type,
                all_tool_names=allowed_tool_names,
            ),
            preferred_probe_families=preferred_probe_families,
        )

    post_solid_semantic_admission = build_post_solid_semantic_admission_signal(
        run_state,
        max_rounds=max_rounds,
    )
    if isinstance(post_solid_semantic_admission, dict):
        remaining_rounds = int(post_solid_semantic_admission.get("remaining_rounds", 0) or 0)
        unsatisfied_feature_count = int(
            post_solid_semantic_admission.get("unsatisfied_feature_count", 0) or 0
        )
        direct_code_escape = bool(post_solid_semantic_admission.get("direct_code_escape"))
        if direct_code_escape:
            allowed_tool_names = [
                name for name in all_tool_names if name in _CODE_FIRST_ESCAPE_TOOL_SET
            ]
            blocked_tool_names = [
                name for name in all_tool_names if name not in _CODE_FIRST_ESCAPE_TOOL_SET
            ]
            return TurnToolPolicy(
                round_no=round_no,
                policy_id="code_first_after_feature_budget_risk",
                mode="code_first_escape",
                reason=(
                    "A structured chain already produced a stable solid, but only "
                    f"{remaining_rounds} rounds remain for {unsatisfied_feature_count} unsatisfied "
                    "features. Prefer execute_build123d over another local-feature continuation."
                ),
                allowed_tool_names=allowed_tool_names,
                blocked_tool_names=blocked_tool_names,
                preferred_tool_names=[
                    "execute_build123d",
                    "query_kernel_state",
                ],
                preferred_probe_families=preferred_probe_families,
            )

        allowed_tool_names = [
            name for name in all_tool_names if name in _SEMANTIC_REFRESH_REPAIR_TOOL_SET
        ]
        blocked_tool_names = [
            name for name in all_tool_names if name not in set(allowed_tool_names)
        ]
        return TurnToolPolicy(
            round_no=round_no,
            policy_id="semantic_admission_after_first_stable_solid",
            mode="graph_refresh",
            reason=(
                "A structured chain already produced the first stable solid, and the remaining "
                f"{unsatisfied_feature_count} unsatisfied features still have {remaining_rounds} "
                "rounds of budget. Refresh semantic evidence before reopening whole-part code."
            ),
            allowed_tool_names=allowed_tool_names,
            blocked_tool_names=blocked_tool_names,
            preferred_tool_names=[
                "query_kernel_state",
                "query_feature_probes",
            ],
            preferred_probe_families=preferred_probe_families,
        )

    latest_successful_structured_write_turn = run_state.latest_successful_write_turn
    if (
        latest_successful_structured_write_turn is not None
        and latest_successful_structured_write_turn.write_tool_name == "apply_cad_action"
        and max(max_rounds - len(run_state.turns), 0) <= 1
        and not _turn_has_open_sketch_window_after_successful_apply(
            latest_successful_structured_write_turn
        )
        and not _latest_validation_is_fresh_for_write(
            run_state,
            write_round=latest_successful_structured_write_turn.round_no,
        )
        and not _has_tool_turn_since_round(
            run_state,
            after_round=latest_successful_structured_write_turn.round_no,
            tool_names={"validate_requirement"},
        )
        and _has_successful_semantic_refresh_since_round(
            run_state,
            after_round=latest_successful_structured_write_turn.round_no,
        )
    ):
        actionable_kernel_patch = _latest_actionable_kernel_patch(run_state)
        latest_validation = (
            run_state.latest_validation if isinstance(run_state.latest_validation, dict) else {}
        )
        if bool(latest_validation.get("blockers")):
            if actionable_kernel_patch is not None:
                return _turn_policy_from_actionable_kernel_patch(
                    round_no=round_no,
                    all_tool_names=all_tool_names,
                    policy_id="repair_after_local_finish_semantic_refresh_under_budget",
                    reason=(
                        "A successful topology-aware local finish already happened, subsequent "
                        "semantic reads refreshed the geometry/topology evidence, and only one "
                        "round remains. Reopen the actionable repair lane now instead of spending "
                        "the final turn on validation-only closure."
                    ),
                    patch=actionable_kernel_patch,
                )
            allowed_tool_names = [
                name for name in all_tool_names if name == "execute_build123d"
            ]
            blocked_tool_names = [
                name for name in all_tool_names if name not in set(allowed_tool_names)
            ]
            return TurnToolPolicy(
                round_no=round_no,
                policy_id="code_escape_after_local_finish_semantic_refresh_under_budget",
                mode="code_repair",
                reason=(
                    "A successful topology-aware local finish already happened, the latest "
                    "semantic refresh still leaves concrete blockers, and only one round remains. "
                    "No actionable kernel patch is available, so reopen execute_build123d for a "
                    "whole-part escape instead of spending the last turn on validation."
                ),
                allowed_tool_names=allowed_tool_names,
                blocked_tool_names=blocked_tool_names,
                preferred_tool_names=["execute_build123d"],
                preferred_probe_families=preferred_probe_families,
            )

    if (
        latest_successful_structured_write_turn is not None
        and latest_successful_structured_write_turn.write_tool_name == "apply_cad_action"
        and _successful_local_finish_semantic_refresh_needs_validation(
            run_state,
            write_round=latest_successful_structured_write_turn.round_no,
            all_tool_names=all_tool_names,
        )
    ):
        allowed_tool_names = [
            name for name in all_tool_names if name in {"validate_requirement"}
        ]
        blocked_tool_names = [
            name for name in all_tool_names if name not in set(allowed_tool_names)
        ]
        return TurnToolPolicy(
            round_no=round_no,
            policy_id="validate_after_local_finish_semantic_refresh",
            mode="validation_check",
            reason=(
                "A successful topology-aware local finish already happened, and subsequent semantic "
                "reads refreshed the geometry/topology evidence. Re-run validate_requirement once "
                "before spending another local write on potentially stale blocker assumptions."
            ),
            allowed_tool_names=allowed_tool_names,
            blocked_tool_names=blocked_tool_names,
            preferred_tool_names=["validate_requirement"],
            preferred_probe_families=preferred_probe_families,
        )

    if (
        latest_successful_structured_write_turn is not None
        and latest_successful_structured_write_turn.write_tool_name == "apply_cad_action"
        and max(max_rounds - len(run_state.turns), 0) <= 1
        and any(
            name in all_tool_names for name in {"validate_requirement", "finish_run"}
        )
        and _local_finish_validation_evidence_gap_needs_read_refresh(
            run_state,
            write_round=latest_successful_structured_write_turn.round_no,
            all_tool_names=all_tool_names,
        )
    ):
        allowed_tool_names, preferred_tool_names = (
            _local_finish_validation_evidence_gap_closure_tools_for_turn(
                run_state,
                all_tool_names=all_tool_names,
            )
        )
        blocked_tool_names = [
            name for name in all_tool_names if name not in set(allowed_tool_names)
        ]
        return TurnToolPolicy(
            round_no=round_no,
            policy_id="closure_refresh_after_local_finish_validation_evidence_gap_under_budget",
            mode="completion_judge",
            reason=(
                "The latest post-local-finish validation cleared blockers but still needs "
                "targeted geometry/topology evidence, and only one round remains. Keep one "
                "focused refresh open, but also allow validate_requirement or finish_run in "
                "the same turn so the final round can consume that evidence instead of ending "
                "on a read-only stall."
            ),
            allowed_tool_names=allowed_tool_names,
            blocked_tool_names=blocked_tool_names,
            preferred_tool_names=preferred_tool_names,
            preferred_probe_families=preferred_probe_families,
        )

    if (
        latest_successful_structured_write_turn is not None
        and latest_successful_structured_write_turn.write_tool_name == "apply_cad_action"
        and _local_finish_validation_evidence_gap_needs_read_refresh(
            run_state,
            write_round=latest_successful_structured_write_turn.round_no,
            all_tool_names=all_tool_names,
        )
    ):
        allowed_tool_names = _local_finish_validation_evidence_refresh_tools_for_turn(
            run_state,
            all_tool_names=all_tool_names,
        )
        blocked_tool_names = [
            name for name in all_tool_names if name not in set(allowed_tool_names)
        ]
        return TurnToolPolicy(
            round_no=round_no,
            policy_id="read_refresh_after_local_finish_validation_evidence_gap",
            mode="graph_refresh",
            reason=(
                "The latest post-local-finish validation cleared blockers but still requests "
                "more geometry/topology evidence. Refresh that evidence once before reopening "
                "another local write or code-first escape."
            ),
            allowed_tool_names=allowed_tool_names,
            blocked_tool_names=blocked_tool_names,
            preferred_tool_names=allowed_tool_names,
            preferred_probe_families=preferred_probe_families,
        )

    latest_structured_write_turn = run_state.latest_write_turn
    if (
        latest_structured_write_turn is not None
        and latest_structured_write_turn.write_tool_name == "apply_cad_action"
        and _local_finish_contract_failure_should_retry_with_existing_topology_refs(
            run_state,
            previous_tool_failure_summary=previous_tool_failure_summary,
            all_tool_names=all_tool_names,
        )
    ):
        allowed_tool_names = [
            name for name in all_tool_names if name in {"apply_cad_action"}
        ]
        blocked_tool_names = [
            name for name in all_tool_names if name not in set(allowed_tool_names)
        ]
        return TurnToolPolicy(
            round_no=round_no,
            policy_id="retry_local_finish_with_existing_topology_refs",
            mode="local_finish",
            reason=(
                "The latest apply_cad_action failed on a local action contract, but the same "
                "geometry state still has actionable query_topology refs in evidence. Retry one "
                "bounded apply_cad_action with those exact refs before opening broader reads."
            ),
            allowed_tool_names=allowed_tool_names,
            blocked_tool_names=blocked_tool_names,
            preferred_tool_names=["apply_cad_action"],
            preferred_probe_families=preferred_probe_families,
        )

    if (
        latest_structured_write_turn is not None
        and latest_structured_write_turn.write_tool_name == "apply_cad_action"
        and _local_finish_contract_failure_should_retry_after_topology_refresh(
            run_state,
            previous_tool_failure_summary=previous_tool_failure_summary,
            all_tool_names=all_tool_names,
        )
    ):
        allowed_tool_names = [
            name for name in all_tool_names if name in {"apply_cad_action"}
        ]
        blocked_tool_names = [
            name for name in all_tool_names if name not in set(allowed_tool_names)
        ]
        return TurnToolPolicy(
            round_no=round_no,
            policy_id="retry_local_finish_after_topology_contract_repair",
            mode="local_finish",
            reason=(
                "The latest apply_cad_action failed on a local action contract, and a subsequent "
                "query_topology already returned actionable refs. Consume those refs with one "
                "explicit apply_cad_action retry before abandoning the bounded local-finishing lane."
            ),
            allowed_tool_names=allowed_tool_names,
            blocked_tool_names=blocked_tool_names,
            preferred_tool_names=["apply_cad_action"],
            preferred_probe_families=preferred_probe_families,
        )

    if (
        latest_structured_write_turn is not None
        and latest_structured_write_turn.write_tool_name == "apply_cad_action"
        and run_state.feature_graph is not None
        and _has_successful_semantic_refresh_since_round(
            run_state,
            after_round=latest_structured_write_turn.round_no,
        )
    ):
        unsatisfied_feature_ids = [
            node.node_id
            for node in run_state.feature_graph.nodes.values()
            if node.kind == "feature" and node.status not in {"satisfied", "resolved"}
        ]
        if unsatisfied_feature_ids and (
            _latest_feature_probes_recommend_apply_local_finish(run_state)
            or _latest_feature_probes_recommend_local_finish(run_state)
        ):
            if _latest_topology_evidence_is_actionable(run_state):
                allowed_tool_names = [
                    name for name in all_tool_names if name in {"apply_cad_action"}
                ]
                blocked_tool_names = [
                    name for name in all_tool_names if name not in set(allowed_tool_names)
                ]
                return TurnToolPolicy(
                    round_no=round_no,
                    policy_id="continue_local_finish_after_semantic_refresh",
                    mode="local_finish",
                    reason=(
                        "A successful local-finish write already happened, and the latest semantic "
                        "refresh still narrows the remaining work to topology-anchored local edits. "
                        "Consume the freshest local refs before reopening whole-part code."
                    ),
                    allowed_tool_names=allowed_tool_names,
                    blocked_tool_names=blocked_tool_names,
                    preferred_tool_names=["apply_cad_action"],
                    preferred_probe_families=preferred_probe_families,
                )
            if "query_topology" in all_tool_names:
                allowed_tool_names = [
                    name
                    for name in all_tool_names
                    if name in {"apply_cad_action", "query_topology", "query_kernel_state"}
                ]
                blocked_tool_names = [
                    name for name in all_tool_names if name not in set(allowed_tool_names)
                ]
                return TurnToolPolicy(
                    round_no=round_no,
                    policy_id="refresh_topology_for_continued_local_finish_after_semantic_refresh",
                    mode="local_finish",
                    reason=(
                        "A successful local-finish write already happened, but the following semantic "
                        "refresh still leaves topology-sensitive local work unresolved and the latest "
                        "topology refs are stale or missing. Refresh query_topology once and stay on "
                        "the local-finish lane instead of reopening whole-part code."
                    ),
                    allowed_tool_names=allowed_tool_names,
                    blocked_tool_names=blocked_tool_names,
                    preferred_tool_names=["query_topology", "apply_cad_action"],
                    preferred_probe_families=preferred_probe_families,
                )
        if unsatisfied_feature_ids:
            allowed_tool_names = [
                name for name in all_tool_names if name in _CODE_FIRST_ESCAPE_TOOL_SET
            ]
            blocked_tool_names = [
                name for name in all_tool_names if name not in _CODE_FIRST_ESCAPE_TOOL_SET
            ]
            return TurnToolPolicy(
                round_no=round_no,
                policy_id="code_first_after_semantic_refresh",
                mode="code_first_escape",
                reason=(
                    "A compatibility semantic refresh already ran after a structured solid, but "
                    f"{len(unsatisfied_feature_ids)} semantic features still remain. Do not reopen "
                    "the structured chain; switch to execute_build123d now."
                ),
                allowed_tool_names=allowed_tool_names,
                blocked_tool_names=blocked_tool_names,
                preferred_tool_names=[
                    "execute_build123d",
                    "query_kernel_state",
                ],
                preferred_probe_families=preferred_probe_families,
            )

    latest_validation = (
        run_state.latest_validation if isinstance(run_state.latest_validation, dict) else {}
    )
    latest_validation_blockers = (
        latest_validation.get("blockers")
        if isinstance(latest_validation.get("blockers"), list)
        else []
    )

    feature_chain_budget_risk = build_feature_chain_budget_risk(
        run_state,
        max_rounds=max_rounds,
    )
    if isinstance(feature_chain_budget_risk, dict):
        allowed_tool_names = [
            name for name in all_tool_names if name in _CODE_FIRST_ESCAPE_TOOL_SET
        ]
        blocked_tool_names = [
            name for name in all_tool_names if name not in _CODE_FIRST_ESCAPE_TOOL_SET
        ]
        unsatisfied_feature_count = int(
            feature_chain_budget_risk.get("unsatisfied_feature_count", 0) or 0
        )
        remaining_rounds = int(feature_chain_budget_risk.get("remaining_rounds", 0) or 0)
        consecutive_apply_action_writes = int(
            feature_chain_budget_risk.get("consecutive_apply_action_writes", 0) or 0
        )
        return TurnToolPolicy(
            round_no=round_no,
            policy_id="code_first_after_feature_budget_risk",
            mode="code_first_escape",
            reason=(
                "A structured apply_cad_action chain already produced a solid, but "
                f"{unsatisfied_feature_count} semantic features remain with only "
                f"{remaining_rounds} rounds left after {consecutive_apply_action_writes} "
                "consecutive structured writes. Prefer execute_build123d over another "
                "partial local step."
            ),
            allowed_tool_names=allowed_tool_names,
            blocked_tool_names=blocked_tool_names,
            preferred_tool_names=[
                "execute_build123d",
                "query_kernel_state",
            ],
            preferred_probe_families=preferred_probe_families,
        )

    latest_code_write_turn = run_state.latest_write_turn
    if (
        latest_code_write_turn is None
        or latest_code_write_turn.write_tool_name != "execute_build123d"
    ):
        latest_code_write_turn = None

    latest_successful_code_write_turn = run_state.latest_successful_write_turn
    if (
        latest_successful_code_write_turn is None
        or latest_successful_code_write_turn.write_tool_name != "execute_build123d"
    ):
        latest_successful_code_write_turn = None

    if (
        latest_successful_code_write_turn is not None
        and run_state.consecutive_inspection_only_rounds < 2
        and not _is_successful_validation(latest_validation)
        and _local_finish_should_force_apply_after_topology_targeting(
            run_state,
            write_round=latest_successful_code_write_turn.round_no,
            all_tool_names=all_tool_names,
        )
        and not _has_tool_turn_since_round(
            run_state,
            after_round=latest_successful_code_write_turn.round_no,
            tool_names={"apply_cad_action"},
        )
    ):
        allowed_tool_names = [
            name for name in all_tool_names if name in {"apply_cad_action"}
        ]
        blocked_tool_names = [
            name for name in all_tool_names if name not in set(allowed_tool_names)
        ]
        return TurnToolPolicy(
            round_no=round_no,
            policy_id="apply_local_finish_after_topology_targeting",
            mode="local_finish",
            reason=(
                "The latest semantic refresh already resolved concrete topology refs for the local "
                "edit target, so the next turn should consume those refs with apply_cad_action "
                "instead of reopening another topology read."
            ),
            allowed_tool_names=allowed_tool_names,
            blocked_tool_names=blocked_tool_names,
            preferred_tool_names=["apply_cad_action"],
            preferred_probe_families=preferred_probe_families,
        )

    if (
        latest_successful_code_write_turn is not None
        and run_state.consecutive_inspection_only_rounds < 2
        and not _is_successful_validation(latest_validation)
        and _local_finish_is_actionable_after_semantic_refresh(
            run_state,
            write_round=latest_successful_code_write_turn.round_no,
            all_tool_names=all_tool_names,
        )
        and not _has_tool_turn_since_round(
            run_state,
            after_round=latest_successful_code_write_turn.round_no,
            tool_names={"apply_cad_action"},
        )
    ):
        allowed_tool_names = [
            name
            for name in all_tool_names
            if name
            in {
                "apply_cad_action",
                "query_topology",
                "query_kernel_state",
                "validate_requirement",
                "finish_run",
            }
        ]
        blocked_tool_names = [
            name for name in all_tool_names if name not in set(allowed_tool_names)
        ]
        return TurnToolPolicy(
            round_no=round_no,
            policy_id="local_finish_after_semantic_refresh_from_code_write",
            mode="local_finish",
            reason=(
                "The latest semantic refresh already produced fresh topology refs, and the feature "
                "probes explicitly recommend topology-anchored local finishing. Do the targeted "
                "apply_cad_action step now instead of burning another validation-only closure turn."
            ),
            allowed_tool_names=allowed_tool_names,
            blocked_tool_names=blocked_tool_names,
            preferred_tool_names=[
                name
                for name in (
                    "apply_cad_action",
                    "query_topology",
                    "validate_requirement",
                    "query_kernel_state",
                    "finish_run",
                )
                if name in allowed_tool_names
            ],
            preferred_probe_families=preferred_probe_families,
        )

    if (
        latest_code_write_turn is not None
        and not latest_validation_blockers
        and not _is_successful_validation(latest_validation)
        and _latest_validation_is_fresh_for_write(
            run_state,
            write_round=latest_code_write_turn.round_no,
        )
        and _latest_validation_prefers_semantic_refresh(latest_validation)
        and _has_repeated_validation_without_new_evidence_after_write(
            run_state,
            write_round=latest_code_write_turn.round_no,
            min_validations=2,
        )
    ):
        allowed_tool_names = _semantic_refresh_allowed_tool_names_for_turn(
            run_state,
            all_tool_names=all_tool_names,
        )
        blocked_tool_names = [
            name
            for name in all_tool_names
            if name not in set(allowed_tool_names)
        ]
        preferred_tools = _preferred_validation_assessment_tools_for_turn(
            run_state,
            all_tool_names=allowed_tool_names,
        )
        return TurnToolPolicy(
            round_no=round_no,
            policy_id="semantic_refresh_after_repeated_validation_without_new_evidence",
            mode="graph_refresh",
            reason=(
                "The last successful execute_build123d write already triggered repeated "
                "incomplete validation checks without any new kernel or probe evidence. "
                "Force a semantic refresh instead of allowing validation ping-pong."
            ),
            allowed_tool_names=allowed_tool_names,
            blocked_tool_names=blocked_tool_names,
            preferred_tool_names=preferred_tools,
            preferred_probe_families=preferred_probe_families,
        )

    if (
        latest_code_write_turn is not None
        and not latest_validation_blockers
        and not _is_successful_validation(latest_validation)
        and _latest_validation_is_fresh_for_write(
            run_state,
            write_round=latest_code_write_turn.round_no,
        )
        and _latest_validation_prefers_semantic_refresh(latest_validation)
        and not _has_tool_turn_since_round(
            run_state,
            after_round=latest_code_write_turn.round_no,
            tool_names={
                "query_kernel_state",
                "query_feature_probes",
                "execute_build123d_probe",
                "query_topology",
            },
        )
    ):
        actionable_kernel_patch = _latest_actionable_kernel_patch(run_state)
        if actionable_kernel_patch is not None:
            if _kernel_patch_should_yield_semantic_refresh(
                actionable_kernel_patch,
                latest_validation,
            ):
                if _short_budget_after_topology_refresh_requires_actionable_repair(
                    run_state=run_state,
                    write_round=latest_code_write_turn.round_no,
                    max_rounds=max_rounds,
                ):
                    return _turn_policy_from_actionable_kernel_patch(
                        round_no=round_no,
                        all_tool_names=all_tool_names,
                        policy_id="repair_after_topology_refresh_under_budget",
                        reason=(
                            "A topology refresh already ran after the fresh validation assessment gap, "
                            "and only a short round budget remains. Exit graph-refresh mode now and "
                            "reopen the actionable repair lane."
                        ),
                        patch=actionable_kernel_patch,
                    )
                allowed_tool_names = _semantic_refresh_allowed_tool_names_for_turn(
                    run_state,
                    all_tool_names=all_tool_names,
                )
                blocked_tool_names = [
                    name
                    for name in all_tool_names
                    if name not in set(allowed_tool_names)
                ]
                return TurnToolPolicy(
                    round_no=round_no,
                    policy_id="semantic_refresh_before_under_grounded_kernel_patch_for_local_feature_gap",
                    mode="graph_refresh",
                    reason=(
                        "The domain kernel synthesized a repair patch, but the current packet is still "
                        "under-grounded for a localized feature family. Refresh topology/feature evidence "
                        "before reopening a whole-part repair lane."
                    ),
                    allowed_tool_names=allowed_tool_names,
                    blocked_tool_names=blocked_tool_names,
                    preferred_tool_names=_preferred_validation_assessment_tools_for_turn(
                        run_state,
                        all_tool_names=allowed_tool_names,
                    ),
                    preferred_probe_families=preferred_probe_families,
                )
            return _turn_policy_from_actionable_kernel_patch(
                round_no=round_no,
                all_tool_names=all_tool_names,
                policy_id="repair_from_actionable_kernel_patch_after_validation_assessment_gap",
                reason=(
                    "The fresh post-write validation surface is still incomplete without explicit "
                    "family blockers, but the domain kernel already synthesized a concrete repair "
                    "patch from the unresolved clause assessment. Repair directly instead of "
                    "opening another semantic-refresh lane."
                ),
                patch=actionable_kernel_patch,
            )
        allowed_tool_names = _semantic_refresh_allowed_tool_names_for_turn(
            run_state,
            all_tool_names=all_tool_names,
        )
        blocked_tool_names = [
            name
            for name in all_tool_names
            if name not in set(allowed_tool_names)
        ]
        preferred_tools = _preferred_validation_assessment_tools_for_turn(
            run_state,
            all_tool_names=allowed_tool_names,
        )
        return TurnToolPolicy(
            round_no=round_no,
            policy_id="semantic_refresh_after_validation_assessment_gap_from_code_write",
            mode="graph_refresh",
            reason=(
                "The fresh post-write validation surface is still incomplete even though it has no "
                "family blockers. Refresh evidence directly from the validation assessment instead "
                "of drifting into generic geometry reads."
            ),
            allowed_tool_names=allowed_tool_names,
            blocked_tool_names=blocked_tool_names,
            preferred_tool_names=preferred_tools,
            preferred_probe_families=preferred_probe_families,
        )

    if (
        latest_code_write_turn is not None
        and not latest_validation_blockers
        and not _is_successful_validation(latest_validation)
        and _latest_validation_is_fresh_for_write(
            run_state,
            write_round=latest_code_write_turn.round_no,
        )
        and _latest_validation_prefers_semantic_refresh(latest_validation)
        and _has_successful_semantic_refresh_since_round(
            run_state,
            after_round=latest_code_write_turn.round_no,
        )
        and not _has_tool_turn_since_round(
            run_state,
            after_round=latest_code_write_turn.round_no,
            tool_names={"validate_requirement", "finish_run"},
        )
        and _semantic_refresh_followup_should_preempt_closure_validation(
            run_state,
            write_round=latest_code_write_turn.round_no,
            all_tool_names=all_tool_names,
        )
    ):
        allowed_tool_names = _post_semantic_refresh_followup_tools_for_turn(
            run_state,
            write_round=latest_code_write_turn.round_no,
            all_tool_names=all_tool_names,
        )
        blocked_tool_names = [
            name for name in all_tool_names if name not in set(allowed_tool_names)
        ]
        return TurnToolPolicy(
            round_no=round_no,
            policy_id="followup_after_semantic_refresh_before_closure_validation_from_code_write",
            mode=(
                "local_finish"
                if "apply_cad_action" in allowed_tool_names
                else "graph_refresh"
            ),
            reason=(
                "The latest validation still has an evidence gap, and the fresh semantic refresh "
                "already recommends concrete follow-up tools. Consume that grounded follow-up lane "
                "before reopening closure validation."
            ),
            allowed_tool_names=allowed_tool_names,
            blocked_tool_names=blocked_tool_names,
            preferred_tool_names=allowed_tool_names,
            preferred_probe_families=preferred_probe_families,
        )

    if (
        latest_code_write_turn is not None
        and not latest_validation_blockers
        and not _is_successful_validation(latest_validation)
        and _latest_validation_is_fresh_for_write(
            run_state,
            write_round=latest_code_write_turn.round_no,
        )
        and _latest_validation_prefers_semantic_refresh(latest_validation)
        and _has_successful_semantic_refresh_since_round(
            run_state,
            after_round=latest_code_write_turn.round_no,
        )
        and not _has_tool_turn_since_round(
            run_state,
            after_round=latest_code_write_turn.round_no,
            tool_names={"validate_requirement", "finish_run"},
        )
    ):
        allowed_tool_names = [
            name
            for name in all_tool_names
            if name in {"validate_requirement", "finish_run", "query_kernel_state"}
        ]
        if (
            _latest_validation_prefers_topology_refresh(latest_validation)
            and "query_topology" in all_tool_names
            and not _has_tool_turn_since_round(
                run_state,
                after_round=latest_code_write_turn.round_no,
                tool_names={"query_topology"},
            )
        ):
            allowed_tool_names.append("query_topology")
        blocked_tool_names = [
            name for name in all_tool_names if name not in set(allowed_tool_names)
        ]
        preferred_tool_names = [
            name
            for name in (
                "query_topology",
                "validate_requirement",
                "finish_run",
                "query_kernel_state",
            )
            if name in allowed_tool_names
        ]
        return TurnToolPolicy(
            round_no=round_no,
            policy_id="closure_validation_after_semantic_refresh_from_code_write",
            mode="completion_judge",
            reason=(
                "A successful execute_build123d write already produced a solid, and the "
                "post-write semantic refresh completed without introducing new blockers. "
                "Close the evidence gap with validation instead of reopening generic reads."
            ),
            allowed_tool_names=allowed_tool_names,
            blocked_tool_names=blocked_tool_names,
            preferred_tool_names=preferred_tool_names,
            preferred_probe_families=preferred_probe_families,
        )

    if (
        latest_code_write_turn is not None
        and latest_validation_blockers
        and not _is_successful_validation(latest_validation)
        and _latest_validation_is_fresh_for_write(
            run_state,
            write_round=latest_code_write_turn.round_no,
        )
        and not _blockers_are_local_structured_tail(latest_validation_blockers)
        and _latest_validation_prefers_semantic_refresh(latest_validation)
        and not _latest_validation_has_actionable_single_blocker(latest_validation)
        and not _has_tool_turn_since_round(
            run_state,
            after_round=latest_code_write_turn.round_no,
            tool_names={
                "query_kernel_state",
                "query_feature_probes",
                "execute_build123d_probe",
                "query_topology",
            },
        )
        and max(max_rounds - len(run_state.turns), 0) <= 1
    ):
        allowed_tool_names = [
            name
            for name in all_tool_names
            if name in {"execute_build123d", "query_kernel_state"}
        ]
        blocked_tool_names = [
            name for name in all_tool_names if name not in set(allowed_tool_names)
        ]
        return TurnToolPolicy(
            round_no=round_no,
            policy_id="code_repair_last_round_after_validation_evidence_gap",
            mode="code_first_repair",
            reason=(
                "A fresh execute_build123d write already reduced the blocker set, but only "
                "one round remains and the latest validation still mixes concrete blockers "
                "with evidence gaps. Spend the final turn on a bounded code repair instead "
                "of a read-only semantic refresh."
            ),
            allowed_tool_names=allowed_tool_names,
            blocked_tool_names=blocked_tool_names,
            preferred_tool_names=["execute_build123d", "query_kernel_state"],
            preferred_probe_families=preferred_probe_families,
        )

    if (
        latest_code_write_turn is not None
        and latest_validation_blockers
        and not _is_successful_validation(latest_validation)
        and _latest_validation_is_fresh_for_write(
            run_state,
            write_round=latest_code_write_turn.round_no,
        )
        and not _blockers_are_local_structured_tail(latest_validation_blockers)
        and _latest_validation_prefers_semantic_refresh(latest_validation)
        and not _latest_validation_has_actionable_single_blocker(latest_validation)
        and not _has_tool_turn_since_round(
            run_state,
            after_round=latest_code_write_turn.round_no,
            tool_names={
                "query_kernel_state",
                "query_feature_probes",
                "execute_build123d_probe",
                "query_topology",
            },
        )
    ):
        allowed_tool_names = _semantic_refresh_allowed_tool_names_for_turn(
            run_state,
            all_tool_names=all_tool_names,
        )
        blocked_tool_names = [
            name
            for name in all_tool_names
            if name not in set(allowed_tool_names)
        ]
        return TurnToolPolicy(
            round_no=round_no,
            policy_id="semantic_refresh_after_validation_evidence_gap_from_code_write",
            mode="graph_refresh",
            reason=(
                "The fresh validation result still has blockers, but it also explicitly says "
                "the current evidence is insufficient. Refresh semantic evidence before "
                "another whole-part rewrite."
            ),
            allowed_tool_names=allowed_tool_names,
            blocked_tool_names=blocked_tool_names,
            preferred_tool_names=_preferred_validation_assessment_tools_for_turn(
                run_state,
                all_tool_names=allowed_tool_names,
            ),
            preferred_probe_families=preferred_probe_families,
        )

    if (
        latest_code_write_turn is not None
        and latest_validation_blockers
        and not bool(latest_validation.get("is_complete"))
        and _latest_validation_is_fresh_for_write(
            run_state,
            write_round=latest_code_write_turn.round_no,
        )
        and not _blockers_are_local_structured_tail(latest_validation_blockers)
        and _has_successful_tool_result_since_round(
            run_state,
            after_round=latest_code_write_turn.round_no,
            tool_names={"query_feature_probes"},
        )
        and _latest_feature_probes_have_general_geometry_grounding_gap(
            run_state,
            after_round=latest_code_write_turn.round_no,
        )
        and not _latest_feature_probes_allow_topology_refresh_despite_general_geometry_gap(
            run_state,
            after_round=latest_code_write_turn.round_no,
        )
        and not _has_tool_turn_since_round(
            run_state,
            after_round=latest_code_write_turn.round_no,
            tool_names={"query_topology", "apply_cad_action", "execute_build123d"},
        )
    ):
        actionable_kernel_patch = _latest_actionable_kernel_patch(run_state)
        if actionable_kernel_patch is not None:
            return _turn_policy_from_actionable_kernel_patch(
                round_no=round_no,
                all_tool_names=all_tool_names,
                policy_id="code_repair_after_feature_probe_detected_whole_part_geometry_gap",
                reason=(
                    "The latest feature probe evidence shows that the overall part count or bounding "
                    "box is still wrong, so stay on the whole-part repair lane instead of escalating "
                    "to topology targeting."
                ),
                patch=actionable_kernel_patch,
            )
        allowed_tool_names = [
            name for name in all_tool_names if name == "execute_build123d"
        ]
        blocked_tool_names = [
            name for name in all_tool_names if name not in set(allowed_tool_names)
        ]
        return TurnToolPolicy(
            round_no=round_no,
            policy_id="code_repair_after_feature_probe_detected_whole_part_geometry_gap",
            mode="code_repair",
            reason=(
                "The latest feature probe evidence shows that the overall part count or bounding box "
                "is still wrong, so stay on the whole-part repair lane instead of escalating to "
                "topology targeting."
            ),
            allowed_tool_names=allowed_tool_names,
            blocked_tool_names=blocked_tool_names,
            preferred_tool_names=["execute_build123d"],
            preferred_probe_families=preferred_probe_families,
        )

    if (
        latest_code_write_turn is not None
        and latest_validation_blockers
        and not bool(latest_validation.get("is_complete"))
        and _latest_validation_is_fresh_for_write(
            run_state,
            write_round=latest_code_write_turn.round_no,
        )
        and not _blockers_are_local_structured_tail(latest_validation_blockers)
        and _latest_feature_probes_prefer_topology_refresh(run_state)
        and _has_successful_tool_result_since_round(
            run_state,
            after_round=latest_code_write_turn.round_no,
            tool_names={"query_feature_probes"},
        )
        and _has_successful_tool_result_since_round(
            run_state,
            after_round=latest_code_write_turn.round_no,
            tool_names={"query_kernel_state"},
        )
        and not _has_tool_turn_since_round(
            run_state,
            after_round=latest_code_write_turn.round_no,
            tool_names={"query_topology", "apply_cad_action"},
        )
        and "query_topology" in all_tool_names
    ):
        allowed_tool_names = [
            name for name in all_tool_names if name == "query_topology"
        ]
        blocked_tool_names = [
            name for name in all_tool_names if name not in set(allowed_tool_names)
        ]
        return TurnToolPolicy(
            round_no=round_no,
            policy_id="force_query_topology_after_feature_probe_kernel_stall",
            mode="graph_refresh",
            reason=(
                "Feature probes already established that the blocked family needs topology host "
                "selection, and a post-probe query_kernel_state refresh has already been spent "
                "without producing topology refs. Exit the semantic stall now and force one "
                "query_topology turn before reopening more kernel refresh or whole-part repair."
            ),
            allowed_tool_names=allowed_tool_names,
            blocked_tool_names=blocked_tool_names,
            preferred_tool_names=["query_topology"],
            preferred_probe_families=preferred_probe_families,
        )

    if (
        latest_code_write_turn is not None
        and latest_validation_blockers
        and not bool(latest_validation.get("is_complete"))
        and _latest_validation_is_fresh_for_write(
            run_state,
            write_round=latest_code_write_turn.round_no,
        )
        and not _blockers_are_local_structured_tail(latest_validation_blockers)
        and _latest_feature_probes_prefer_topology_refresh(run_state)
        and _has_successful_tool_result_since_round(
            run_state,
            after_round=latest_code_write_turn.round_no,
            tool_names={"query_feature_probes"},
        )
        and not _has_tool_turn_since_round(
            run_state,
            after_round=latest_code_write_turn.round_no,
            tool_names={"query_topology", "apply_cad_action"},
        )
    ):
        remaining_rounds = max(max_rounds - len(run_state.turns), 0)
        actionable_kernel_patch = _latest_actionable_kernel_patch(run_state)
        if remaining_rounds <= 1 and actionable_kernel_patch is not None:
            return _turn_policy_from_actionable_kernel_patch(
                round_no=round_no,
                all_tool_names=all_tool_names,
                policy_id="repair_last_round_after_feature_probe_assessment",
                reason=(
                    "A successful feature-probe assessment already narrowed the repair family, "
                    "and only one round remains. Skip another topology refresh and spend the "
                    "final turn on the actionable repair lane."
                ),
                patch=actionable_kernel_patch,
            )
        allowed_tool_names = [
            name
            for name in all_tool_names
            if name in {"query_kernel_state", "query_topology"}
        ]
        blocked_tool_names = [
            name
            for name in all_tool_names
            if name not in set(allowed_tool_names)
        ]
        return TurnToolPolicy(
            round_no=round_no,
            policy_id="topology_refresh_after_feature_probe_assessment_from_code_write",
            mode="graph_refresh",
            reason=(
                "A successful feature-probe assessment already narrowed the next step to topology "
                "host selection. Refresh query_topology now instead of spending another turn "
                "repeating feature probes."
            ),
            allowed_tool_names=allowed_tool_names,
            blocked_tool_names=blocked_tool_names,
            preferred_tool_names=[
                "query_topology",
                "query_kernel_state",
            ],
            preferred_probe_families=preferred_probe_families,
        )

    if (
        latest_code_write_turn is not None
        and latest_validation_blockers
        and not bool(latest_validation.get("is_complete"))
        and _latest_validation_is_fresh_for_write(
            run_state,
            write_round=latest_code_write_turn.round_no,
        )
        and _blockers_are_local_structured_tail(latest_validation_blockers)
        and not _has_tool_turn_since_round(
            run_state,
            after_round=latest_code_write_turn.round_no,
            tool_names={"apply_cad_action", "query_topology"},
        )
    ):
        allowed_tool_names = [
            name
            for name in all_tool_names
            if name in {"apply_cad_action", "query_topology", "query_kernel_state"}
        ]
        blocked_tool_names = [
            name for name in all_tool_names if name not in set(allowed_tool_names)
        ]
        return TurnToolPolicy(
            round_no=round_no,
            policy_id="local_finish_after_code_write",
            mode="local_finish",
            reason=(
                "A successful execute_build123d write already produced the correct whole-part body, "
                "and only a local fillet/chamfer tail remains. Prefer one targeted structured finish "
                "over another whole-part rewrite."
            ),
            allowed_tool_names=allowed_tool_names,
            blocked_tool_names=blocked_tool_names,
            preferred_tool_names=["apply_cad_action", "query_topology"],
            preferred_probe_families=[],
        )

    if (
        latest_code_write_turn is not None
        and latest_validation_blockers
        and not bool(latest_validation.get("is_complete"))
        and _latest_validation_is_fresh_for_write(
            run_state,
            write_round=latest_code_write_turn.round_no,
        )
        and max(max_rounds - len(run_state.turns), 0) <= 1
        and _has_recent_semantic_refresh_before_round(
            run_state,
            before_round=latest_code_write_turn.round_no,
        )
        and not _blockers_are_local_structured_tail(latest_validation_blockers)
    ):
        allowed_tool_names = [
            name for name in all_tool_names if name == "execute_build123d"
        ]
        blocked_tool_names = [
            name for name in all_tool_names if name not in set(allowed_tool_names)
        ]
        return TurnToolPolicy(
            round_no=round_no,
            policy_id="code_repair_under_budget_after_repeated_validation_blockers",
            mode="code_repair",
            reason=(
                "The latest execute_build123d write already has fresh core blockers, and an earlier "
                "semantic refresh exists for this repair chain. Spend the final round on a targeted "
                "code repair instead of burning it on another refresh read."
            ),
            allowed_tool_names=allowed_tool_names,
            blocked_tool_names=blocked_tool_names,
            preferred_tool_names=["execute_build123d"],
            preferred_probe_families=preferred_probe_families,
        )

    if (
        latest_code_write_turn is not None
        and latest_validation_blockers
        and not bool(latest_validation.get("is_complete"))
        and _latest_validation_is_fresh_for_write(
            run_state,
            write_round=latest_code_write_turn.round_no,
        )
        and count_consecutive_write_turns(run_state, tool_name="execute_build123d") >= 2
        and _has_repeated_validation_blockers_without_semantic_refresh(
            run_state,
            blockers=latest_validation_blockers,
            min_repeats=2,
        )
        and not _blockers_are_local_structured_tail(latest_validation_blockers)
        and not _has_tool_turn_since_round(
            run_state,
            after_round=latest_code_write_turn.round_no,
            tool_names={
                "query_kernel_state",
                "query_feature_probes",
                "execute_build123d_probe",
            },
        )
    ):
        actionable_kernel_patch = _latest_actionable_kernel_patch(run_state)
        if actionable_kernel_patch is not None:
            if _kernel_patch_should_yield_semantic_refresh(
                actionable_kernel_patch,
                latest_validation,
            ):
                if _short_budget_after_topology_refresh_requires_actionable_repair(
                    run_state=run_state,
                    write_round=latest_code_write_turn.round_no,
                    max_rounds=max_rounds,
                ):
                    return _turn_policy_from_actionable_kernel_patch(
                        round_no=round_no,
                        all_tool_names=all_tool_names,
                        policy_id="repair_after_topology_refresh_under_budget",
                        reason=(
                            "A topology refresh already ran after the repeated validation blocker, "
                            "and only a short round budget remains. Exit graph-refresh mode now and "
                            "spend the next turn on the actionable repair lane."
                        ),
                        patch=actionable_kernel_patch,
                    )
                allowed_tool_names = _semantic_refresh_allowed_tool_names_for_turn(
                    run_state,
                    all_tool_names=all_tool_names,
                )
                blocked_tool_names = [
                    name
                    for name in all_tool_names
                    if name not in set(allowed_tool_names)
                ]
                return TurnToolPolicy(
                    round_no=round_no,
                    policy_id="semantic_refresh_before_under_grounded_kernel_patch_for_local_feature_gap",
                    mode="graph_refresh",
                    reason=(
                        "The latest kernel patch is still under-grounded for a localized feature family, "
                        "so repeated validation blockers should trigger a semantic refresh instead of "
                        "another broad rewrite."
                    ),
                    allowed_tool_names=allowed_tool_names,
                    blocked_tool_names=blocked_tool_names,
                    preferred_tool_names=_preferred_validation_assessment_tools_for_turn(
                        run_state,
                        all_tool_names=allowed_tool_names,
                    ),
                    preferred_probe_families=preferred_probe_families,
                )
            if _kernel_patch_should_yield_feature_probe_assessment(
                actionable_kernel_patch,
                latest_validation,
                run_state=run_state,
            ):
                allowed_tool_names = _semantic_refresh_allowed_tool_names_for_turn(
                    run_state,
                    all_tool_names=all_tool_names,
                )
                blocked_tool_names = [
                    name
                    for name in all_tool_names
                    if name not in set(allowed_tool_names)
                ]
                preferred_tool_names = _preferred_validation_assessment_tools_for_turn(
                    run_state,
                    all_tool_names=allowed_tool_names,
                )
                if (
                    "query_feature_probes" in allowed_tool_names
                    and "query_feature_probes" not in preferred_tool_names
                ):
                    preferred_tool_names = [
                        "query_feature_probes",
                        *preferred_tool_names,
                    ]
                return TurnToolPolicy(
                    round_no=round_no,
                    policy_id="feature_probe_assessment_after_repeated_validation_blocker_from_code_write",
                    mode="graph_refresh",
                    reason=(
                        "Repeated successful whole-part rebuilds are still blocked on topology-sensitive "
                        "local families, but the current actionable kernel patch is still broad whole-part "
                        "repair guidance. Refresh family-specific probe evidence before repeating another rebuild."
                    ),
                    allowed_tool_names=allowed_tool_names,
                    blocked_tool_names=blocked_tool_names,
                    preferred_tool_names=preferred_tool_names,
                    preferred_probe_families=preferred_probe_families,
                )
            return _turn_policy_from_actionable_kernel_patch(
                round_no=round_no,
                all_tool_names=all_tool_names,
                policy_id="repair_from_actionable_kernel_patch_after_repeated_validation_blocker",
                reason=(
                    "Repeated execute_build123d repairs are still hitting the same blocked feature "
                    "instances, but the domain kernel already has a concrete repair patch. Stay on "
                    "the repair lane instead of reopening semantic-refresh reads."
                ),
                patch=actionable_kernel_patch,
            )
        allowed_tool_names = [
            name for name in all_tool_names if name in _SEMANTIC_REFRESH_REPAIR_TOOL_SET
        ]
        blocked_tool_names = [
            name
            for name in all_tool_names
            if name not in _SEMANTIC_REFRESH_REPAIR_TOOL_SET
        ]
        return TurnToolPolicy(
            round_no=round_no,
            policy_id="semantic_refresh_after_repeated_validation_blocker_from_code_write",
            mode="graph_refresh",
            reason=(
                "Repeated execute_build123d repairs are still landing on the same fresh core "
                "validation blockers. Force a semantic refresh or probe turn before another "
                "whole-part rewrite."
            ),
            allowed_tool_names=allowed_tool_names,
            blocked_tool_names=blocked_tool_names,
            preferred_tool_names=[
                "query_kernel_state",
                "query_feature_probes",
            ],
            preferred_probe_families=preferred_probe_families,
        )

    if (
        latest_code_write_turn is not None
        and latest_validation_blockers
        and not bool(latest_validation.get("is_complete"))
        and _latest_validation_is_fresh_for_write(
            run_state,
            write_round=latest_code_write_turn.round_no,
        )
        and not _blockers_are_local_structured_tail(latest_validation_blockers)
        and not _has_tool_turn_since_round(
            run_state,
            after_round=latest_code_write_turn.round_no,
            tool_names={
                "query_kernel_state",
                "query_feature_probes",
                "execute_build123d_probe",
                "execute_build123d",
            },
        )
    ):
        actionable_kernel_patch = _latest_actionable_kernel_patch(run_state)
        if actionable_kernel_patch is not None:
            if _kernel_patch_should_yield_semantic_refresh(
                actionable_kernel_patch,
                latest_validation,
            ):
                if _short_budget_after_topology_refresh_requires_actionable_repair(
                    run_state=run_state,
                    write_round=latest_code_write_turn.round_no,
                    max_rounds=max_rounds,
                ):
                    return _turn_policy_from_actionable_kernel_patch(
                        round_no=round_no,
                        all_tool_names=all_tool_names,
                        policy_id="repair_after_topology_refresh_under_budget",
                        reason=(
                            "A topology refresh already ran after the fresh validation blocker, "
                            "and only a short round budget remains. Exit graph-refresh mode now "
                            "and spend the next turn on the actionable repair lane."
                        ),
                        patch=actionable_kernel_patch,
                    )
                allowed_tool_names = _semantic_refresh_allowed_tool_names_for_turn(
                    run_state,
                    all_tool_names=all_tool_names,
                )
                blocked_tool_names = [
                    name
                    for name in all_tool_names
                    if name not in set(allowed_tool_names)
                ]
                return TurnToolPolicy(
                    round_no=round_no,
                    policy_id="semantic_refresh_before_under_grounded_kernel_patch_for_local_feature_gap",
                    mode="graph_refresh",
                    reason=(
                        "Validation still asks for localized geometry/topology evidence, and the latest "
                        "kernel patch does not yet carry executable anchors. Refresh semantic evidence "
                        "before applying another whole-part repair."
                    ),
                    allowed_tool_names=allowed_tool_names,
                    blocked_tool_names=blocked_tool_names,
                    preferred_tool_names=_preferred_validation_assessment_tools_for_turn(
                        run_state,
                        all_tool_names=allowed_tool_names,
                    ),
                    preferred_probe_families=preferred_probe_families,
                )
            if _kernel_patch_should_yield_feature_probe_assessment(
                actionable_kernel_patch,
                latest_validation,
                run_state=run_state,
            ):
                allowed_tool_names = _semantic_refresh_allowed_tool_names_for_turn(
                    run_state,
                    all_tool_names=all_tool_names,
                )
                blocked_tool_names = [
                    name
                    for name in all_tool_names
                    if name not in set(allowed_tool_names)
                ]
                preferred_tool_names = _preferred_validation_assessment_tools_for_turn(
                    run_state,
                    all_tool_names=allowed_tool_names,
                )
                if (
                    "query_feature_probes" in allowed_tool_names
                    and "query_feature_probes" not in preferred_tool_names
                ):
                    preferred_tool_names = [
                        "query_feature_probes",
                        *preferred_tool_names,
                    ]
                return TurnToolPolicy(
                    round_no=round_no,
                    policy_id="feature_probe_assessment_before_actionable_kernel_patch_repair",
                    mode="graph_refresh",
                    reason=(
                        "The latest successful whole-part write is blocked on topology-sensitive local "
                        "feature families, so refresh family-specific probe evidence before committing "
                        "to another whole-part repair."
                    ),
                    allowed_tool_names=allowed_tool_names,
                    blocked_tool_names=blocked_tool_names,
                    preferred_tool_names=preferred_tool_names,
                    preferred_probe_families=preferred_probe_families,
                )
            return _turn_policy_from_actionable_kernel_patch(
                round_no=round_no,
                all_tool_names=all_tool_names,
                policy_id="repair_from_actionable_kernel_patch_after_validation_blocker",
                reason=(
                    "A successful execute_build123d write already has explicit core blockers, and the "
                    "domain kernel patch is specific enough to drive the next repair directly."
                ),
                patch=actionable_kernel_patch,
            )
        allowed_tool_names = [
            name for name in all_tool_names if name in _CODE_FIRST_ESCAPE_TOOL_SET
        ]
        blocked_tool_names = [
            name for name in all_tool_names if name not in _CODE_FIRST_ESCAPE_TOOL_SET
        ]
        return TurnToolPolicy(
            round_no=round_no,
            policy_id="code_repair_after_validation_blocker_from_code_write",
            mode="code_repair",
            reason=(
                "A successful execute_build123d write already has explicit core validation blockers. "
                "Prefer direct code repair or semantic refresh over generic geometry/topology inspection."
            ),
            allowed_tool_names=allowed_tool_names,
            blocked_tool_names=blocked_tool_names,
            preferred_tool_names=[
                "execute_build123d",
                "query_kernel_state",
            ],
            preferred_probe_families=preferred_probe_families,
        )

    if latest_code_write_turn is not None and isinstance(previous_tool_failure_summary, dict):
        if str(previous_tool_failure_summary.get("tool") or "").strip() == "execute_build123d":
            same_tool_failure_count = int(
                previous_tool_failure_summary.get("same_tool_failure_count") or 0
            )
            effective_failure_kind = str(
                previous_tool_failure_summary.get("effective_failure_kind")
                or previous_tool_failure_summary.get("failure_kind")
                or ""
            ).strip()
            concrete_code_failure_kinds = {
                "execute_build123d_api_lint_failure",
                "execute_build123d_python_syntax_failure",
                "execute_build123d_curve_api_failure",
                "execute_build123d_sweep_profile_recipe_failure",
                "execute_build123d_boolean_shape_api_failure",
                "execute_build123d_loft_wire_recipe_failure",
                "execute_build123d_selector_api_failure",
                "execute_build123d_selector_failure",
            }
            if same_tool_failure_count == 1 and effective_failure_kind in concrete_code_failure_kinds:
                if (
                    "query_topology" in all_tool_names
                    and not _latest_failed_code_sequence_is_artifactless(run_state)
                    and _latest_feature_probes_prefer_topology_refresh(run_state)
                ):
                    allowed_tool_names = [
                        name
                        for name in all_tool_names
                        if name in {"query_topology", "query_kernel_state"}
                    ]
                    blocked_tool_names = [
                        name for name in all_tool_names if name not in set(allowed_tool_names)
                    ]
                    return TurnToolPolicy(
                        round_no=round_no,
                        policy_id="topology_refresh_after_first_concrete_code_failure",
                        mode="graph_refresh",
                        reason=(
                            "The latest execute_build123d failure is a concrete Build123d contract "
                            "problem, but the freshest feature-probe evidence also says the blocked "
                            "family needs topology host selection. Refresh query_topology once before "
                            "reopening another whole-part rewrite so the next repair lane is narrowed "
                            "against real host-face or edge evidence first."
                        ),
                        allowed_tool_names=allowed_tool_names,
                        blocked_tool_names=blocked_tool_names,
                        preferred_tool_names=[
                            "query_topology",
                            "query_kernel_state",
                        ],
                        preferred_probe_families=preferred_probe_families,
                    )
                allowed_tool_names = [
                    name
                    for name in all_tool_names
                    if name in {"execute_build123d", "query_kernel_state"}
                ]
                blocked_tool_names = [
                    name for name in all_tool_names if name not in {"execute_build123d", "query_kernel_state"}
                ]
                return TurnToolPolicy(
                    round_no=round_no,
                    policy_id="code_repair_after_first_concrete_code_failure",
                    mode="code_first_repair",
                    reason=(
                        "The latest execute_build123d failure already names a concrete Build123d "
                        "builder/API contract problem. Repair the code path directly, or use "
                        "query_kernel_state to refresh the repair lane, instead of opening a "
                        "probe-first detour."
                    ),
                    allowed_tool_names=allowed_tool_names,
                    blocked_tool_names=blocked_tool_names,
                    preferred_tool_names=[
                        "execute_build123d",
                        "query_kernel_state",
                    ],
                    preferred_probe_families=preferred_probe_families,
                )
            if same_tool_failure_count >= 2:
                actionable_refresh = _latest_actionable_semantic_refresh_since_failed_write(
                    run_state,
                    failed_write_round=latest_code_write_turn.round_no,
                )
                if actionable_refresh is not None and actionable_refresh["repair_lane"] == "local_finish":
                    if _local_finish_should_force_apply_after_topology_targeting(
                        run_state,
                        write_round=latest_code_write_turn.round_no,
                        all_tool_names=all_tool_names,
                    ):
                        allowed_tool_names = [
                            name for name in all_tool_names if name in {"apply_cad_action"}
                        ]
                        blocked_tool_names = [
                            name for name in all_tool_names if name not in set(allowed_tool_names)
                        ]
                        return TurnToolPolicy(
                            round_no=round_no,
                            policy_id="apply_local_finish_after_actionable_feature_probe_refresh",
                            mode="local_finish",
                            reason=(
                                "Repeated code failures already triggered a successful semantic refresh, "
                                "and that refresh plus the latest topology read now provide actionable "
                                "local refs. Consume those refs with apply_cad_action instead of "
                                "reopening another topology read."
                            ),
                            allowed_tool_names=allowed_tool_names,
                            blocked_tool_names=blocked_tool_names,
                            preferred_tool_names=["apply_cad_action"],
                            preferred_probe_families=actionable_refresh["families"],
                        )
                    allowed_tool_names = [
                        name
                        for name in all_tool_names
                        if name in {"apply_cad_action", "query_topology", "query_kernel_state"}
                    ]
                    blocked_tool_names = [
                        name for name in all_tool_names if name not in set(allowed_tool_names)
                    ]
                    return TurnToolPolicy(
                        round_no=round_no,
                        policy_id="local_finish_after_actionable_feature_probe_refresh",
                        mode="local_finish",
                        reason=(
                            "Repeated execute_build123d repairs already triggered a successful semantic "
                            "refresh, and that refresh explicitly narrowed the next step to topology-"
                            "anchored local finishing. Promote the local-finish lane directly instead "
                            "of reopening another whole-part rewrite."
                        ),
                        allowed_tool_names=allowed_tool_names,
                        blocked_tool_names=blocked_tool_names,
                        preferred_tool_names=["query_topology", "apply_cad_action"],
                        preferred_probe_families=actionable_refresh["families"],
                    )
            repeated_code_failure_requires_probe = requirement_prefers_code_first_family(
                requirements=run_state.requirements,
                latest_validation=run_state.latest_validation,
            ) or _latest_failed_code_sequence_is_artifactless(run_state)
            if (
                same_tool_failure_count >= 2
                and repeated_code_failure_requires_probe
            ):
                actionable_refresh = _latest_actionable_semantic_refresh_since_failed_write(
                    run_state,
                    failed_write_round=latest_code_write_turn.round_no,
                )
                artifactless_failure = _latest_failed_code_sequence_is_artifactless(run_state)
                if artifactless_failure and effective_failure_kind in concrete_code_failure_kinds:
                    allowed_tool_names = [
                        name for name in all_tool_names if name in _CODE_FIRST_ESCAPE_TOOL_SET
                    ]
                    allowed_tool_names = [
                        name
                        for name in allowed_tool_names
                        if name != "query_feature_probes"
                    ]
                    blocked_tool_names = [
                        name
                        for name in all_tool_names
                        if name not in set(allowed_tool_names)
                    ]
                    return TurnToolPolicy(
                        round_no=round_no,
                        policy_id="code_repair_after_repeated_concrete_code_failure",
                        mode="code_first_repair",
                        reason=(
                            "The repeated execute_build123d failure is already classified as a concrete "
                            "Build123d builder/API problem, so do not spend another round on semantic-refresh "
                            "reads before repairing the code path."
                        ),
                        allowed_tool_names=allowed_tool_names,
                        blocked_tool_names=blocked_tool_names,
                        preferred_tool_names=[
                            "execute_build123d",
                            "query_kernel_state",
                            "execute_build123d_probe",
                        ],
                        preferred_probe_families=preferred_probe_families,
                    )
                if actionable_refresh is not None:
                    if actionable_refresh["repair_lane"] == "local_finish":
                        allowed_tool_names = [
                            name
                            for name in all_tool_names
                            if name in {"apply_cad_action", "query_topology", "query_kernel_state"}
                        ]
                        blocked_tool_names = [
                            name for name in all_tool_names if name not in set(allowed_tool_names)
                        ]
                    else:
                        allowed_tool_names = [
                            name for name in all_tool_names if name in _CODE_FIRST_ESCAPE_TOOL_SET
                        ]
                        blocked_tool_names = [
                            name for name in all_tool_names if name not in _CODE_FIRST_ESCAPE_TOOL_SET
                        ]
                        allowed_tool_names = [
                            name
                            for name in allowed_tool_names
                            if name not in {"query_feature_probes", "execute_build123d_probe"}
                        ]
                        blocked_tool_names = [
                            *blocked_tool_names,
                            *[
                                name
                                for name in ("query_feature_probes", "execute_build123d_probe")
                                if name not in blocked_tool_names
                            ],
                        ]
                    preferred_tool_names = ["execute_build123d", "query_kernel_state"]
                    if actionable_refresh["repair_lane"] == "local_finish":
                        preferred_tool_names = ["apply_cad_action", "query_topology"]
                    return TurnToolPolicy(
                        round_no=round_no,
                        policy_id="code_repair_after_actionable_semantic_refresh",
                        mode=(
                            "local_finish"
                            if actionable_refresh["repair_lane"] == "local_finish"
                            else "code_repair"
                        ),
                        reason=(
                            "A recent semantic refresh already narrowed the failure to a concrete repair "
                            "lane. Exit the probe chain now and spend the next turn on the targeted repair."
                        ),
                        allowed_tool_names=allowed_tool_names,
                        blocked_tool_names=blocked_tool_names,
                        preferred_tool_names=preferred_tool_names,
                        preferred_probe_families=actionable_refresh["families"],
                    )
                if (
                    artifactless_failure
                    and "path_sweep" in preferred_probe_families
                    and _has_actionable_probe_turn_since_failed_write(
                        run_state,
                        failed_write_round=latest_code_write_turn.round_no,
                    )
                ):
                    allowed_tool_names = [
                        name for name in all_tool_names if name in _CODE_FIRST_ESCAPE_TOOL_SET
                    ]
                    blocked_tool_names = [
                        name for name in all_tool_names if name not in _CODE_FIRST_ESCAPE_TOOL_SET
                    ]
                    return TurnToolPolicy(
                        round_no=round_no,
                        policy_id="code_repair_after_actionable_artifactless_probe",
                        mode="code_first_repair",
                        reason=(
                            "A successful execute_build123d_probe already produced actionable "
                            "rail/profile/frame diagnostics for an artifactless path-sweep failure. "
                            "Reopen execute_build123d now instead of inserting another kernel refresh read."
                        ),
                        allowed_tool_names=allowed_tool_names,
                        blocked_tool_names=blocked_tool_names,
                        preferred_tool_names=[
                            "execute_build123d",
                            "execute_build123d_probe",
                            "query_kernel_state",
                        ],
                        preferred_probe_families=preferred_probe_families,
                    )
                remaining_rounds = max(max_rounds - len(run_state.turns), 0)
                if (
                    artifactless_failure
                    and remaining_rounds <= 1
                    and _has_successful_probe_turn_since_failed_write(
                        run_state,
                        failed_write_round=latest_code_write_turn.round_no,
                    )
                ):
                    allowed_tool_names = [
                        name for name in all_tool_names if name in _CODE_FIRST_ESCAPE_TOOL_SET
                    ]
                    blocked_tool_names = [
                        name for name in all_tool_names if name not in _CODE_FIRST_ESCAPE_TOOL_SET
                    ]
                    return TurnToolPolicy(
                        round_no=round_no,
                        policy_id="code_repair_last_round_after_successful_probe",
                        mode="code_first_repair",
                        reason=(
                            "A successful diagnostic probe already ran after the artifactless code "
                            "failure, and only one round remains. Spend the final turn on targeted "
                            "code repair instead of another read-only refresh."
                        ),
                        allowed_tool_names=allowed_tool_names,
                        blocked_tool_names=blocked_tool_names,
                        preferred_tool_names=[
                            "execute_build123d",
                            "query_kernel_state",
                            "execute_build123d_probe",
                        ],
                        preferred_probe_families=preferred_probe_families,
                    )
                if (
                    artifactless_failure
                    and _has_successful_non_persisted_probe_turn_since_failed_write(
                        run_state,
                        failed_write_round=latest_code_write_turn.round_no,
                    )
                    and not _has_semantic_refresh_turn_since_failed_write(
                        run_state,
                        failed_write_round=latest_code_write_turn.round_no,
                    )
                ):
                    allowed_tool_names = [
                        name for name in all_tool_names if name == "query_kernel_state"
                    ]
                    blocked_tool_names = [
                        name for name in all_tool_names if name not in {"query_kernel_state"}
                    ]
                    return TurnToolPolicy(
                        round_no=round_no,
                        policy_id="kernel_refresh_after_successful_artifactless_probe",
                        mode="semantic_refresh",
                        reason=(
                            "A successful execute_build123d_probe already captured the artifactless "
                            "failure surface. Close the probe chain now with query_kernel_state so "
                            "the next turn can reopen a targeted repair lane instead of spending "
                            "another round on probe-only diagnostics."
                        ),
                        allowed_tool_names=allowed_tool_names,
                        blocked_tool_names=blocked_tool_names,
                        preferred_tool_names=["query_kernel_state"],
                        preferred_probe_families=preferred_probe_families,
                    )
                if not _has_semantic_refresh_turn_since_failed_write(
                    run_state,
                    failed_write_round=latest_code_write_turn.round_no,
                ):
                    probe_first_tool_set = (
                        _ARTIFACTLESS_PROBE_FIRST_TOOL_SET
                        if artifactless_failure
                        else _PROBE_FIRST_TOOL_SET
                    )
                    allowed_tool_names = [
                        name for name in all_tool_names if name in probe_first_tool_set
                    ]
                    blocked_tool_names = [
                        name for name in all_tool_names if name not in probe_first_tool_set
                    ]
                    reason = (
                        "Repeated execute_build123d failures without usable post-write evidence require a "
                        "semantic refresh before another broad rewrite. execute_build123d_probe can help "
                        "diagnose the code path, but it does not satisfy the refresh requirement alone."
                        if artifactless_failure
                        else (
                            "Repeated execute_build123d failure on a family-driven geometry problem requires "
                            "a semantic refresh before another broad rewrite. execute_build123d_probe can "
                            "help diagnose the code path, but it does not satisfy the refresh requirement alone."
                        )
                    )
                    preferred_tool_names = [
                        name
                        for name in [
                            "query_kernel_state",
                            "query_feature_probes",
                            "execute_build123d_probe",
                        ]
                        if name in allowed_tool_names
                    ]
                    return TurnToolPolicy(
                        round_no=round_no,
                        policy_id="probe_first_after_repeated_code_failure",
                        mode="probe_first",
                        reason=reason,
                        allowed_tool_names=allowed_tool_names,
                        blocked_tool_names=blocked_tool_names,
                        preferred_tool_names=preferred_tool_names,
                        preferred_probe_families=preferred_probe_families,
                    )
                if artifactless_failure and not _has_actionable_probe_turn_since_failed_write(
                    run_state,
                    failed_write_round=latest_code_write_turn.round_no,
                ):
                    allowed_tool_names = [
                        name
                        for name in all_tool_names
                        if name in {"execute_build123d", "query_kernel_state"}
                    ]
                    blocked_tool_names = [
                        name for name in all_tool_names if name not in set(allowed_tool_names)
                    ]
                    return TurnToolPolicy(
                        round_no=round_no,
                        policy_id="code_repair_after_semantic_refresh_without_actionable_probe",
                        mode="code_first_repair",
                        reason=(
                            "A semantic refresh already happened after the artifactless code failure, "
                            "but it still did not produce actionable probe evidence. Stay on a narrowed "
                            "code-repair surface instead of reopening broad reads."
                        ),
                        allowed_tool_names=allowed_tool_names,
                        blocked_tool_names=blocked_tool_names,
                        preferred_tool_names=[
                            "execute_build123d",
                            "query_kernel_state",
                            "execute_build123d_probe",
                        ],
                        preferred_probe_families=recommended_feature_probe_families(
                            requirements=run_state.requirements,
                            latest_validation=run_state.latest_validation,
                        ),
                    )

    if latest_code_write_turn is not None and isinstance(previous_tool_failure_summary, dict):
        failure_tool = str(previous_tool_failure_summary.get("tool") or "").strip()
        failure_kind = str(previous_tool_failure_summary.get("failure_kind") or "").strip()
        remaining_rounds = max(max_rounds - len(run_state.turns), 0)
        if (
            failure_tool == "execute_build123d"
            and _latest_turn_tool_policy_id(run_state)
            in {
                "code_first_after_feature_budget_risk",
                "code_first_after_semantic_admission_budget_risk",
                "code_first_after_semantic_refresh",
                "code_repair_after_validation_blocker_from_code_write",
            }
            and remaining_rounds <= 2
            and not _has_probe_turn_since_failed_write(
                run_state,
                failed_write_round=latest_code_write_turn.round_no,
            )
        ):
            allowed_tool_names = [
                name for name in all_tool_names if name in _CODE_FIRST_ESCAPE_TOOL_SET
            ]
            blocked_tool_names = [
                name for name in all_tool_names if name not in _CODE_FIRST_ESCAPE_TOOL_SET
            ]
            preferred_tool_names = ["execute_build123d", "execute_build123d_probe"]
            if failure_kind == "execute_build123d_timeout":
                preferred_tool_names = ["execute_build123d_probe", "query_feature_probes"]
            elif failure_kind == "execute_build123d_chain_context_failure":
                preferred_tool_names = ["execute_build123d", "query_kernel_state"]
            elif failure_kind == "execute_build123d_selector_failure":
                preferred_tool_names = ["execute_build123d", "query_feature_probes"]
            return TurnToolPolicy(
                round_no=round_no,
                policy_id="code_recovery_after_failed_code_escape",
                mode="code_recovery",
                reason=(
                    "A short-tail code-first escape already failed on execute_build123d, so do not "
                    "burn the remaining rounds on broad generic reads or reopening apply_cad_action "
                    "bootstrap steps. Repair the concrete code-path failure directly."
                ),
                allowed_tool_names=allowed_tool_names,
                blocked_tool_names=blocked_tool_names,
                preferred_tool_names=preferred_tool_names,
                preferred_probe_families=preferred_probe_families,
            )

    latest_successful_write_turn = run_state.latest_successful_write_turn
    if (
        latest_successful_write_turn is not None
        and run_state.consecutive_inspection_only_rounds >= 2
        and _local_finish_should_force_apply_after_topology_targeting(
            run_state,
            write_round=latest_successful_write_turn.round_no,
            all_tool_names=all_tool_names,
        )
        and not _has_tool_turn_since_round(
            run_state,
            after_round=latest_successful_write_turn.round_no,
            tool_names={"apply_cad_action"},
        )
    ):
        allowed_tool_names = [
            name for name in all_tool_names if name in {"apply_cad_action"}
        ]
        blocked_tool_names = [
            name for name in all_tool_names if name not in set(allowed_tool_names)
        ]
        return TurnToolPolicy(
            round_no=round_no,
            policy_id="apply_local_finish_after_topology_targeting_from_read_stall",
            mode="local_finish",
            reason=(
                "Repeated read-only turns already produced actionable topology refs for the local "
                "edit target, so exit the read stall by consuming those refs with apply_cad_action "
                "instead of reopening more semantic reads."
            ),
            allowed_tool_names=allowed_tool_names,
            blocked_tool_names=blocked_tool_names,
            preferred_tool_names=["apply_cad_action"],
            preferred_probe_families=preferred_probe_families,
        )

    if (
        latest_successful_write_turn is not None
        and run_state.consecutive_inspection_only_rounds >= 2
        and _local_finish_is_actionable_after_semantic_refresh(
            run_state,
            write_round=latest_successful_write_turn.round_no,
            all_tool_names=all_tool_names,
        )
        and not _has_tool_turn_since_round(
            run_state,
            after_round=latest_successful_write_turn.round_no,
            tool_names={"apply_cad_action"},
        )
    ):
        allowed_tool_names = [
            name
            for name in all_tool_names
            if name in {"apply_cad_action", "query_topology", "query_kernel_state"}
        ]
        blocked_tool_names = [
            name for name in all_tool_names if name not in set(allowed_tool_names)
        ]
        return TurnToolPolicy(
            round_no=round_no,
            policy_id="local_finish_after_read_stall_topology_refresh",
            mode="local_finish",
            reason=(
                "Repeated read-only turns already produced fresh topology evidence, and the latest "
                "feature probe points to a topology-anchored local finishing step. Exit the read stall "
                "and spend the next turn on apply_cad_action."
            ),
            allowed_tool_names=allowed_tool_names,
            blocked_tool_names=blocked_tool_names,
            preferred_tool_names=["apply_cad_action", "query_topology", "query_kernel_state"],
            preferred_probe_families=preferred_probe_families,
        )

    if (
        latest_successful_write_turn is not None
        and run_state.consecutive_inspection_only_rounds >= 2
        and not _has_successful_tool_result_since_round(
            run_state,
            after_round=latest_successful_write_turn.round_no,
            tool_names=_SEMANTIC_REFRESH_QUERY_TOOL_SET,
        )
        and _local_finish_escape_is_available_after_topology_targeting(
            run_state,
            write_round=latest_successful_write_turn.round_no,
            all_tool_names=all_tool_names,
        )
        and not _has_tool_turn_since_round(
            run_state,
            after_round=latest_successful_write_turn.round_no,
            tool_names={"apply_cad_action"},
        )
    ):
        allowed_tool_names = [
            name
            for name in ["query_kernel_state", "apply_cad_action"]
            if name in all_tool_names
        ]
        blocked_tool_names = [
            name for name in all_tool_names if name not in set(allowed_tool_names)
        ]
        return TurnToolPolicy(
            round_no=round_no,
            policy_id="graph_refresh_with_local_finish_escape_after_read_stall",
            mode="graph_refresh",
            reason=(
                "Repeated inspection-only turns still require a semantic refresh, but the latest "
                "feature probe and topology read already produced actionable local targeting. "
                "Keep query_kernel_state preferred while preserving apply_cad_action as a bounded "
                "escape instead of collapsing the lane to rebuild-only reads."
            ),
            allowed_tool_names=allowed_tool_names,
            blocked_tool_names=blocked_tool_names,
            preferred_tool_names=["query_kernel_state", "apply_cad_action"],
            preferred_probe_families=preferred_probe_families,
        )

    if (
        latest_successful_write_turn is not None
        and run_state.consecutive_inspection_only_rounds >= 2
        and not _has_successful_tool_result_since_round(
            run_state,
            after_round=latest_successful_write_turn.round_no,
            tool_names=_SEMANTIC_REFRESH_QUERY_TOOL_SET,
        )
    ):
        allowed_tool_names = [
            name for name in all_tool_names if name in _GRAPH_REFRESH_TOOL_SET
        ]
        blocked_tool_names = [
            name for name in all_tool_names if name not in _GRAPH_REFRESH_TOOL_SET
        ]
        return TurnToolPolicy(
            round_no=round_no,
            policy_id="graph_refresh_after_read_stall",
            mode="graph_refresh",
            reason=(
                "Repeated inspection-only turns after the latest write require a semantic refresh "
                "via query_kernel_state before more generic reads."
            ),
            allowed_tool_names=allowed_tool_names,
            blocked_tool_names=blocked_tool_names,
            preferred_tool_names=[
                "query_kernel_state",
                "query_feature_probes",
            ],
            preferred_probe_families=preferred_probe_families,
        )
    return None


def _has_semantic_refresh_turn_since_failed_write(
    run_state: RunState,
    *,
    failed_write_round: int,
) -> bool:
    return _has_tool_turn_since_round(
        run_state,
        after_round=failed_write_round,
        tool_names=_SEMANTIC_REFRESH_COMPLETION_TOOL_SET,
    )


def _has_probe_turn_since_failed_write(
    run_state: RunState,
    *,
    failed_write_round: int,
) -> bool:
    return _has_tool_turn_since_round(
        run_state,
        after_round=failed_write_round,
        tool_names={"execute_build123d_probe"},
    )


def _has_successful_probe_turn_since_failed_write(
    run_state: RunState,
    *,
    failed_write_round: int,
) -> bool:
    for turn in run_state.turns:
        if turn.round_no <= failed_write_round:
            continue
        for result in turn.tool_results:
            if result.name == "execute_build123d_probe" and result.success:
                return True
    probe_round = run_state.evidence.rounds_by_tool.get("execute_build123d_probe")
    probe_payload = run_state.evidence.latest_by_tool.get("execute_build123d_probe")
    if (
        isinstance(probe_round, int)
        and probe_round > failed_write_round
        and isinstance(probe_payload, dict)
        and bool(probe_payload.get("success"))
    ):
        return True
    return False


def _has_successful_non_persisted_probe_turn_since_failed_write(
    run_state: RunState,
    *,
    failed_write_round: int,
) -> bool:
    for turn in run_state.turns:
        if turn.round_no <= failed_write_round:
            continue
        for result in turn.tool_results:
            if result.name != "execute_build123d_probe" or not result.success:
                continue
            payload = result.payload if isinstance(result.payload, dict) else {}
            if not bool(payload.get("session_state_persisted", False)):
                return True
    probe_round = run_state.evidence.rounds_by_tool.get("execute_build123d_probe")
    probe_payload = run_state.evidence.latest_by_tool.get("execute_build123d_probe")
    return (
        isinstance(probe_round, int)
        and probe_round > failed_write_round
        and isinstance(probe_payload, dict)
        and bool(probe_payload.get("success"))
        and not bool(probe_payload.get("session_state_persisted", False))
    )


def _has_actionable_probe_turn_since_failed_write(
    run_state: RunState,
    *,
    failed_write_round: int,
) -> bool:
    for turn in run_state.turns:
        if turn.round_no <= failed_write_round:
            continue
        for result in turn.tool_results:
            if result.name != "execute_build123d_probe" or not result.success:
                continue
            payload = result.payload if isinstance(result.payload, dict) else {}
            probe_summary = (
                payload.get("probe_summary")
                if isinstance(payload.get("probe_summary"), dict)
                else {}
            )
            if bool(probe_summary.get("actionable")):
                return True
    return False


def _feature_probe_recommends_local_finish(probe: dict[str, Any]) -> bool:
    if not isinstance(probe, dict):
        return False
    family_id = str(probe.get("family") or "").strip()
    recommended_next_tools = {
        str(item or "").strip().lower()
        for item in (probe.get("recommended_next_tools") or [])
        if str(item or "").strip()
    }
    if {"query_topology", "apply_cad_action"}.issubset(recommended_next_tools):
        return True
    return family_id == "named_face_local_edit" and "query_topology" in recommended_next_tools


def _latest_feature_probes_recommend_local_finish(run_state: RunState) -> bool:
    for turn in reversed(run_state.turns):
        for result in reversed(turn.tool_results):
            if result.name != "query_feature_probes" or not result.success:
                continue
            payload = result.payload if isinstance(result.payload, dict) else {}
            for probe in payload.get("probes") or []:
                if _feature_probe_recommends_local_finish(
                    probe if isinstance(probe, dict) else {}
                ):
                    return True
            return False
    return False


def _local_finish_is_actionable_after_semantic_refresh(
    run_state: RunState,
    *,
    write_round: int,
    all_tool_names: list[str],
) -> bool:
    if "apply_cad_action" not in all_tool_names or "query_topology" not in all_tool_names:
        return False
    if _local_finish_should_defer_to_actionable_rebuild_patch(run_state):
        return False
    if not _latest_feature_probes_recommend_local_finish(run_state):
        return False
    return _has_successful_tool_result_since_round(
        run_state,
        after_round=write_round,
        tool_names={"query_topology"},
    )


def _latest_feature_probes_recommend_apply_local_finish(run_state: RunState) -> bool:
    return bool(_latest_feature_probe_apply_local_finish_families(run_state))


def _latest_feature_probe_apply_local_finish_families(run_state: RunState) -> set[str]:
    families: set[str] = set()
    for turn in reversed(run_state.turns):
        for result in reversed(turn.tool_results):
            if result.name != "query_feature_probes" or not result.success:
                continue
            payload = result.payload if isinstance(result.payload, dict) else {}
            for probe in payload.get("probes") or []:
                if not isinstance(probe, dict):
                    continue
                family_id = str(
                    probe.get("family")
                    or probe.get("family_id")
                    or ""
                ).strip()
                recommended_next_tools = {
                    str(item or "").strip().lower()
                    for item in (probe.get("recommended_next_tools") or [])
                    if str(item or "").strip()
                }
                if "apply_cad_action" in recommended_next_tools and family_id:
                    families.add(family_id)
            return families
    return families


def _actionable_patch_family_ids(
    run_state: RunState,
    patch: dict[str, Any],
) -> set[str]:
    families: set[str] = set()
    feature_graph = run_state.feature_graph
    feature_instances = (
        getattr(feature_graph, "feature_instances", {})
        if feature_graph is not None
        else {}
    )

    direct_family_id = str(patch.get("family_id") or "").strip()
    if direct_family_id:
        families.add(direct_family_id)

    feature_instance_id = str(patch.get("feature_instance_id") or "").strip()
    if feature_instance_id:
        instance = feature_instances.get(feature_instance_id)
        family_id = str(getattr(instance, "family_id", "") or "").strip()
        if family_id:
            families.add(family_id)

    for instance_id in patch.get("feature_instance_ids") or []:
        normalized_instance_id = str(instance_id or "").strip()
        if not normalized_instance_id:
            continue
        instance = feature_instances.get(normalized_instance_id)
        family_id = str(getattr(instance, "family_id", "") or "").strip()
        if family_id:
            families.add(family_id)
    return families


def _has_actionable_topology_targeting_since_round(
    run_state: RunState,
    *,
    after_round: int,
) -> bool:
    for turn in reversed(run_state.turns):
        if turn.round_no <= after_round:
            continue
        for result in reversed(turn.tool_results):
            if result.name != "query_topology" or not result.success:
                continue
            payload = result.payload if isinstance(result.payload, dict) else {}
            matched_ref_ids = [
                str(ref_id).strip()
                for ref_id in (payload.get("matched_ref_ids") or [])
                if str(ref_id).strip()
            ]
            if matched_ref_ids:
                return True
            matched_ref_id_count = payload.get("matched_ref_id_count")
            if isinstance(matched_ref_id_count, int) and matched_ref_id_count > 0:
                return True
            for candidate_set in payload.get("candidate_sets") or []:
                if not isinstance(candidate_set, dict):
                    continue
                candidate_ref_ids = [
                    str(ref_id).strip()
                    for ref_id in (candidate_set.get("ref_ids") or [])
                    if str(ref_id).strip()
                ]
                if candidate_ref_ids:
                    return True
            return False
    return False


def _local_finish_should_force_apply_after_topology_targeting(
    run_state: RunState,
    *,
    write_round: int,
    all_tool_names: list[str],
) -> bool:
    if "apply_cad_action" not in all_tool_names:
        return False
    if _local_finish_should_defer_to_actionable_rebuild_patch(run_state):
        return False
    if not _latest_feature_probes_recommend_apply_local_finish(run_state):
        return False
    return _has_actionable_topology_targeting_since_round(
        run_state,
        after_round=write_round,
    )


def _local_finish_escape_is_available_after_topology_targeting(
    run_state: RunState,
    *,
    write_round: int,
    all_tool_names: list[str],
) -> bool:
    if "apply_cad_action" not in all_tool_names:
        return False
    if not _latest_feature_probes_recommend_apply_local_finish(run_state):
        return False
    return _has_actionable_topology_targeting_since_round(
        run_state,
        after_round=write_round,
    )


def _local_finish_should_defer_to_actionable_rebuild_patch(
    run_state: RunState,
) -> bool:
    patch = _latest_actionable_kernel_patch(run_state)
    if not isinstance(patch, dict):
        return False
    repair_mode = str(patch.get("repair_mode") or "").strip()
    if repair_mode not in {"whole_part_rebuild", "subtree_rebuild"}:
        return False
    apply_local_finish_families = _latest_feature_probe_apply_local_finish_families(
        run_state
    )
    if apply_local_finish_families:
        patch_family_ids = _actionable_patch_family_ids(run_state, patch)
        if patch_family_ids and apply_local_finish_families.isdisjoint(patch_family_ids):
            return False
    return True


def _local_finish_contract_failure_should_retry_after_topology_refresh(
    run_state: RunState,
    *,
    previous_tool_failure_summary: dict[str, Any] | None,
    all_tool_names: list[str],
) -> bool:
    if "apply_cad_action" not in all_tool_names:
        return False
    if not isinstance(previous_tool_failure_summary, dict):
        return False
    latest_write_turn = run_state.latest_write_turn
    if latest_write_turn is None or latest_write_turn.write_tool_name != "apply_cad_action":
        return False
    failure_tool = str(previous_tool_failure_summary.get("tool") or "").strip()
    failure_kind = str(
        previous_tool_failure_summary.get("effective_failure_kind")
        or previous_tool_failure_summary.get("failure_kind")
        or ""
    ).strip()
    if failure_tool != "apply_cad_action" or failure_kind != "apply_cad_action_contract_failure":
        return False
    if not (
        _latest_feature_probes_recommend_apply_local_finish(run_state)
        or _latest_feature_probes_recommend_local_finish(run_state)
    ):
        return False
    if not _has_successful_tool_result_since_round(
        run_state,
        after_round=latest_write_turn.round_no,
        tool_names={"query_topology"},
    ):
        return False
    if not _has_actionable_topology_targeting_since_round(
        run_state,
        after_round=latest_write_turn.round_no,
    ):
        return False
    return not _has_tool_turn_since_round(
        run_state,
        after_round=latest_write_turn.round_no,
        tool_names={"apply_cad_action"},
    )


def _latest_topology_evidence_is_actionable(run_state: RunState) -> bool:
    topology_payload = run_state.evidence.latest_by_tool.get("query_topology")
    if not isinstance(topology_payload, dict):
        return False
    matched_ref_ids = [
        str(ref_id).strip()
        for ref_id in (topology_payload.get("matched_ref_ids") or [])
        if str(ref_id).strip()
    ]
    if matched_ref_ids:
        return True
    matched_ref_id_count = topology_payload.get("matched_ref_id_count")
    if isinstance(matched_ref_id_count, int) and matched_ref_id_count > 0:
        return True
    for candidate_set in topology_payload.get("candidate_sets") or []:
        if not isinstance(candidate_set, dict):
            continue
        candidate_ref_ids = [
            str(ref_id).strip()
            for ref_id in (candidate_set.get("ref_ids") or [])
            if str(ref_id).strip()
        ]
        if candidate_ref_ids:
            return True
    return False


def _successful_local_finish_semantic_refresh_needs_validation(
    run_state: RunState,
    *,
    write_round: int,
    all_tool_names: list[str],
) -> bool:
    if "validate_requirement" not in all_tool_names:
        return False
    if _latest_validation_is_fresh_for_write(run_state, write_round=write_round):
        return False
    if _has_tool_turn_since_round(
        run_state,
        after_round=write_round,
        tool_names={"validate_requirement"},
    ):
        return False
    if not _has_successful_semantic_refresh_since_round(
        run_state,
        after_round=write_round,
    ):
        return False
    return _latest_feature_probes_recommend_apply_local_finish(
        run_state
    ) or _latest_feature_probes_recommend_local_finish(run_state)


def _local_finish_validation_evidence_gap_needs_read_refresh(
    run_state: RunState,
    *,
    write_round: int,
    all_tool_names: list[str],
) -> bool:
    if not any(
        name in all_tool_names
        for name in {
            "query_topology",
            "query_feature_probes",
            "query_geometry",
            "query_kernel_state",
            "execute_build123d_probe",
        }
    ):
        return False
    latest_validation = (
        run_state.latest_validation if isinstance(run_state.latest_validation, dict) else {}
    )
    if not _latest_validation_is_fresh_for_write(run_state, write_round=write_round):
        return False
    blockers = latest_validation.get("blockers")
    if isinstance(blockers, list) and blockers:
        return False
    if _is_successful_validation(latest_validation):
        return False
    if not _validation_has_evidence_gap(latest_validation):
        return False
    if not (
        _validation_requests_localized_evidence_refresh(latest_validation)
        or _latest_validation_has_budget_skipped_hint(latest_validation)
    ):
        return False
    latest_validation_round = _latest_validation_round(run_state)
    if latest_validation_round <= write_round:
        return False
    return not _has_tool_turn_since_round(
        run_state,
        after_round=latest_validation_round,
        tool_names={
            "query_topology",
            "query_feature_probes",
            "query_geometry",
            "query_kernel_state",
            "execute_build123d_probe",
        },
    )


def _local_finish_validation_evidence_refresh_tools_for_turn(
    run_state: RunState,
    *,
    all_tool_names: list[str],
) -> list[str]:
    read_refresh_tool_set = {
        "query_topology",
        "query_feature_probes",
        "query_geometry",
        "query_kernel_state",
        "execute_build123d_probe",
    }
    allowed_tool_names: list[str] = []

    def _append(raw_tool_name: Any) -> None:
        tool_name = str(raw_tool_name or "").strip()
        if (
            not tool_name
            or tool_name not in all_tool_names
            or tool_name not in read_refresh_tool_set
            or tool_name in allowed_tool_names
        ):
            return
        allowed_tool_names.append(tool_name)

    for tool_name in _preferred_validation_assessment_tools_for_turn(
        run_state,
        all_tool_names=all_tool_names,
    ):
        _append(tool_name)

    latest_validation = (
        run_state.latest_validation if isinstance(run_state.latest_validation, dict) else {}
    )
    normalized_hints = {
        str(item or "").strip().lower()
        for field_name in ("repair_hints", "decision_hints")
        for item in (latest_validation.get(field_name) or [])
        if isinstance(item, str) and str(item).strip()
    }
    if any(
        token in hint
        for hint in normalized_hints
        for token in (
            "geometry/topology evidence",
            "inspect count or placement",
            "inspect_more_evidence",
        )
    ):
        _append("query_geometry")
        _append("query_topology")
        _append("query_feature_probes")
    if not allowed_tool_names and _latest_validation_prefers_topology_refresh(latest_validation):
        _append("query_topology")
        _append("query_feature_probes")
    _append("query_kernel_state")
    return allowed_tool_names


def _local_finish_validation_evidence_gap_closure_tools_for_turn(
    run_state: RunState,
    *,
    all_tool_names: list[str],
) -> tuple[list[str], list[str]]:
    refresh_tools = _local_finish_validation_evidence_refresh_tools_for_turn(
        run_state,
        all_tool_names=all_tool_names,
    )
    allowed_tool_names = list(refresh_tools)

    for tool_name in ("validate_requirement", "finish_run"):
        if tool_name in all_tool_names and tool_name not in allowed_tool_names:
            allowed_tool_names.append(tool_name)

    preferred_tool_names: list[str] = []
    if refresh_tools:
        preferred_tool_names.append(refresh_tools[0])
    for tool_name in ("validate_requirement", "finish_run"):
        if tool_name in allowed_tool_names and tool_name not in preferred_tool_names:
            preferred_tool_names.append(tool_name)
    for tool_name in refresh_tools[1:]:
        if tool_name not in preferred_tool_names:
            preferred_tool_names.append(tool_name)
    return allowed_tool_names, preferred_tool_names


def _local_finish_contract_failure_should_retry_with_existing_topology_refs(
    run_state: RunState,
    *,
    previous_tool_failure_summary: dict[str, Any] | None,
    all_tool_names: list[str],
) -> bool:
    if "apply_cad_action" not in all_tool_names:
        return False
    if not isinstance(previous_tool_failure_summary, dict):
        return False
    latest_write_turn = run_state.latest_write_turn
    if latest_write_turn is None or latest_write_turn.write_tool_name != "apply_cad_action":
        return False
    failure_tool = str(previous_tool_failure_summary.get("tool") or "").strip()
    failure_kind = str(
        previous_tool_failure_summary.get("effective_failure_kind")
        or previous_tool_failure_summary.get("failure_kind")
        or ""
    ).strip()
    if failure_tool != "apply_cad_action" or failure_kind != "apply_cad_action_contract_failure":
        return False
    if not (
        _latest_feature_probes_recommend_apply_local_finish(run_state)
        or _latest_feature_probes_recommend_local_finish(run_state)
    ):
        return False
    topology_round = run_state.evidence.rounds_by_tool.get("query_topology")
    if not isinstance(topology_round, int) or topology_round <= 0:
        return False
    if topology_round >= latest_write_turn.round_no:
        return False
    if not _latest_topology_evidence_is_actionable(run_state):
        return False
    return not _has_tool_turn_since_round(
        run_state,
        after_round=latest_write_turn.round_no,
        tool_names={"apply_cad_action", "query_topology"},
    )


def _latest_actionable_semantic_refresh_since_failed_write(
    run_state: RunState,
    *,
    failed_write_round: int,
) -> dict[str, Any] | None:
    for turn in reversed(run_state.turns):
        if turn.round_no <= failed_write_round:
            continue
        for result in reversed(turn.tool_results):
            if result.name != "query_feature_probes" or not result.success:
                continue
            payload = result.payload if isinstance(result.payload, dict) else {}
            families = [
                str(family_id).strip()
                for family_id in (payload.get("detected_families") or [])
                if isinstance(family_id, str) and str(family_id).strip()
            ]
            local_finish_signaled = False
            for probe in payload.get("probes") or []:
                if not isinstance(probe, dict):
                    continue
                probe_family = str(probe.get("family") or "").strip()
                if probe_family and probe_family not in families:
                    families.append(probe_family)
                if _feature_probe_recommends_local_finish(probe):
                    local_finish_signaled = True
            if local_finish_signaled:
                return {
                    "repair_lane": "local_finish",
                    "families": families,
                    "round_no": turn.round_no,
                }
            probe_blockers = [
                str(blocker_id).strip()
                for probe in (payload.get("probes") or [])
                if isinstance(probe, dict)
                for blocker_id in (probe.get("blockers") or [])
                if isinstance(blocker_id, str) and str(blocker_id).strip()
            ]
            taxonomy = classify_blocker_taxonomy_many(
                probe_blockers,
                evidence_source="probe",
                completeness_relevance="diagnostic",
            )
            repair_lanes = {
                str(item.recommended_repair_lane or "").strip()
                for item in taxonomy
                if str(item.recommended_repair_lane or "").strip()
            }
            if not repair_lanes or repair_lanes == {"probe_first"}:
                continue
            for item in taxonomy:
                for family_id in item.family_ids:
                    if family_id and family_id not in families:
                        families.append(family_id)
            repair_lane = "code_repair"
            if repair_lanes == {"local_finish"}:
                repair_lane = "local_finish"
            return {
                "repair_lane": repair_lane,
                "families": families,
                "round_no": turn.round_no,
            }
    return None


def _latest_actionable_kernel_patch(
    run_state: RunState,
) -> dict[str, Any] | None:
    graph = run_state.feature_graph
    if graph is None:
        return None
    raw_patches = getattr(graph, "repair_patches", None)
    if not isinstance(raw_patches, dict) or not raw_patches:
        raw_patches = {}
    feature_instances = getattr(graph, "feature_instances", {})
    if not isinstance(feature_instances, dict):
        feature_instances = {}

    blocked_family_ids = {
        str(getattr(feature_instance, "family_id", "") or "").strip()
        for feature_instance in feature_instances.values()
        if str(getattr(feature_instance, "status", "") or "").strip() == "blocked"
        and str(getattr(feature_instance, "family_id", "") or "").strip()
    }

    best_patch: dict[str, Any] | None = None
    for patch in reversed(list(raw_patches.values())):
        if bool(getattr(patch, "stale", False)):
            continue
        repair_mode = str(getattr(patch, "repair_mode", "") or "").strip()
        feature_instance_ids = [
            str(item).strip()
            for item in (getattr(patch, "feature_instance_ids", None) or [])
            if isinstance(item, str) and str(item).strip()
        ]
        anchor_keys = [
            str(item).strip()
            for item in (getattr(patch, "anchor_keys", None) or [])
            if isinstance(item, str) and str(item).strip()
        ]
        parameter_keys = [
            str(item).strip()
            for item in (getattr(patch, "parameter_keys", None) or [])
            if isinstance(item, str) and str(item).strip()
        ]
        repair_intent = str(getattr(patch, "repair_intent", "") or "").strip()
        if not repair_mode or not feature_instance_ids:
            continue
        if not (anchor_keys or parameter_keys):
            continue
        families: list[str] = []
        for instance_id in feature_instance_ids:
            feature_instance = feature_instances.get(instance_id)
            family_id = str(getattr(feature_instance, "family_id", "") or "").strip()
            if family_id and family_id not in families:
                families.append(family_id)
        best_patch = {
            "repair_mode": repair_mode,
            "feature_instance_ids": feature_instance_ids,
            "anchor_keys": anchor_keys,
            "parameter_keys": parameter_keys,
            "repair_intent": repair_intent,
            "families": families,
        }
        break

    best_packet: dict[str, Any] | None = None
    raw_packets = getattr(graph, "repair_packets", None)
    if isinstance(raw_packets, dict) and raw_packets:
        for packet in reversed(list(raw_packets.values())):
            if bool(getattr(packet, "stale", False)):
                continue
            repair_mode = str(getattr(packet, "repair_mode", "") or "").strip()
            feature_instance_id = str(getattr(packet, "feature_instance_id", "") or "").strip()
            family_id = str(getattr(packet, "family_id", "") or "").strip()
            anchor_keys = [
                str(item).strip()
                for item in (getattr(packet, "anchor_keys", None) or [])
                if isinstance(item, str) and str(item).strip()
            ]
            parameter_keys = [
                str(item).strip()
                for item in (getattr(packet, "parameter_keys", None) or [])
                if isinstance(item, str) and str(item).strip()
            ]
            repair_intent = str(getattr(packet, "repair_intent", "") or "").strip()
            if not repair_mode or not feature_instance_id:
                continue
            if not (anchor_keys or parameter_keys):
                continue
            families = [family_id] if family_id else []
            packet_dict = (
                packet.to_dict()
                if hasattr(packet, "to_dict")
                else {
                    "family_id": family_id,
                    "feature_instance_id": feature_instance_id,
                    "repair_mode": repair_mode,
                }
            )
            best_packet = {
                "repair_mode": repair_mode,
                "feature_instance_ids": [feature_instance_id],
                "anchor_keys": anchor_keys,
                "parameter_keys": parameter_keys,
                "repair_intent": repair_intent,
                "families": families,
                "repair_packet": packet_dict,
            }
            break

    if best_packet is None:
        return best_patch
    if best_patch is None:
        return best_packet

    packet_mode = str(best_packet.get("repair_mode") or "").strip()
    patch_mode = str(best_patch.get("repair_mode") or "").strip()
    if (
        packet_mode == "local_edit"
        and patch_mode in {"whole_part_rebuild", "subtree_rebuild"}
        and (len(blocked_family_ids) > 1 or "general_geometry" in blocked_family_ids)
    ):
        return best_patch
    return best_packet


_COARSE_KERNEL_PATCH_PARAMETER_KEYS = {
    "bbox",
    "bbox_min",
    "bbox_max",
    "bbox_min_span",
    "bbox_max_span",
    "anchor_summary",
}

_LOCAL_TOPOLOGY_SENSITIVE_FAMILIES = {
    "named_face_local_edit",
    "slots",
    "explicit_anchor_hole",
    "half_shell",
    "nested_hollow_section",
}

_LOCAL_TOPOLOGY_SENSITIVE_BLOCKER_IDS = {
    "feature_target_face_edit",
    "feature_target_face_subtractive_merge",
    "feature_notch_or_profile_cut",
    "feature_hole",
    "feature_counterbore",
    "feature_countersink",
}


def _validation_has_evidence_gap(latest_validation: dict[str, Any] | None) -> bool:
    if not isinstance(latest_validation, dict):
        return False
    if bool(latest_validation.get("insufficient_evidence")):
        return True
    observation_tags = {
        str(item).strip().lower()
        for item in (latest_validation.get("observation_tags") or [])
        if isinstance(item, str) and str(item).strip()
    }
    return "insufficient_evidence" in observation_tags


def _validation_requests_localized_evidence_refresh(
    latest_validation: dict[str, Any] | None,
) -> bool:
    if not _validation_has_evidence_gap(latest_validation):
        return False
    if not isinstance(latest_validation, dict):
        return False
    normalized_hints = {
        str(item).strip().lower()
        for field_name in ("repair_hints", "decision_hints")
        for item in (latest_validation.get(field_name) or [])
        if isinstance(item, str) and str(item).strip()
    }
    if "query_topology" in normalized_hints or "query_feature_probes" in normalized_hints:
        return True
    if any(
        token in hint
        for hint in normalized_hints
        for token in (
            "geometry/topology evidence",
            "inspect count or placement",
            "inspect_more_evidence",
        )
    ):
        return True
    for item in (latest_validation.get("blocker_taxonomy") or []):
        if not isinstance(item, dict):
            continue
        if str(item.get("recommended_repair_lane") or "").strip() == "local_finish":
            return True
        decision_hints = {
            str(hint).strip().lower()
            for hint in (item.get("decision_hints") or [])
            if isinstance(hint, str) and str(hint).strip()
        }
        if "query_topology" in decision_hints or "query_feature_probes" in decision_hints:
            return True
    return False


def _kernel_patch_is_under_grounded_for_local_feature_gap(
    patch: dict[str, Any] | None,
) -> bool:
    if not isinstance(patch, dict):
        return False
    anchor_keys = {
        str(item).strip()
        for item in (patch.get("anchor_keys") or [])
        if isinstance(item, str) and str(item).strip()
    }
    parameter_keys = {
        str(item).strip()
        for item in (patch.get("parameter_keys") or [])
        if isinstance(item, str) and str(item).strip()
    }
    repair_packet = (
        patch.get("repair_packet")
        if isinstance(patch.get("repair_packet"), dict)
        else {}
    )
    target_anchor_summary = (
        repair_packet.get("target_anchor_summary")
        if isinstance(repair_packet.get("target_anchor_summary"), dict)
        else {}
    )
    realized_anchor_summary = (
        repair_packet.get("realized_anchor_summary")
        if isinstance(repair_packet.get("realized_anchor_summary"), dict)
        else {}
    )
    recipe_skeleton = (
        repair_packet.get("recipe_skeleton")
        if isinstance(repair_packet.get("recipe_skeleton"), dict)
        else {}
    )
    grounding_blockers = {
        str(item).strip()
        for item in (
            repair_packet.get("grounding_blockers") or []
            if isinstance(repair_packet, dict)
            else []
        )
        if isinstance(item, str) and str(item).strip()
    }
    center_source_key = str(recipe_skeleton.get("center_source_key") or "").strip().lower()
    needs_external_anchor_grounding = (
        center_source_key.startswith("derive_from_requirement")
        or "validation" in center_source_key
        or bool(target_anchor_summary.get("requires_topology_host_ranking"))
        or "need_topology_host_selection" in grounding_blockers
    )
    coarse_only_parameters = bool(parameter_keys) and parameter_keys.issubset(
        _COARSE_KERNEL_PATCH_PARAMETER_KEYS
    )
    has_anchor_grounding = bool(anchor_keys) or bool(target_anchor_summary) or bool(realized_anchor_summary)
    return not has_anchor_grounding and (
        needs_external_anchor_grounding or coarse_only_parameters
    )


def _kernel_patch_should_yield_semantic_refresh(
    patch: dict[str, Any] | None,
    latest_validation: dict[str, Any] | None,
) -> bool:
    if not _validation_requests_localized_evidence_refresh(latest_validation):
        return False
    return _kernel_patch_is_under_grounded_for_local_feature_gap(patch)


def _kernel_patch_should_yield_feature_probe_assessment(
    patch: dict[str, Any] | None,
    latest_validation: dict[str, Any] | None,
    *,
    run_state: RunState | None = None,
) -> bool:
    if not isinstance(patch, dict) or not isinstance(latest_validation, dict):
        return False
    repair_mode = str(patch.get("repair_mode") or "").strip()
    if repair_mode not in {"whole_part_rebuild", "subtree_rebuild"}:
        return False
    families = {
        str(item).strip()
        for item in (patch.get("families") or [])
        if isinstance(item, str) and str(item).strip()
    }
    if run_state is not None:
        families.update(_blocked_feature_instance_family_ids(run_state))
    if not families.intersection(_LOCAL_TOPOLOGY_SENSITIVE_FAMILIES):
        return False
    blocker_ids = {
        str(item).strip()
        for item in (latest_validation.get("blockers") or [])
        if isinstance(item, str) and str(item).strip()
    }
    if blocker_ids.intersection(_LOCAL_TOPOLOGY_SENSITIVE_BLOCKER_IDS):
        return True
    for item in (latest_validation.get("blocker_taxonomy") or []):
        if not isinstance(item, dict):
            continue
        taxonomy_families = {
            str(family_id).strip()
            for family_id in (item.get("family_ids") or [])
            if isinstance(family_id, str) and str(family_id).strip()
        }
        if taxonomy_families.intersection(_LOCAL_TOPOLOGY_SENSITIVE_FAMILIES):
            return True
        decision_hints = {
            str(hint).strip().lower()
            for hint in (item.get("decision_hints") or [])
            if isinstance(hint, str) and str(hint).strip()
        }
        if decision_hints.intersection({"query_feature_probes", "query_topology"}):
            return True
    return False


def _blocked_feature_instance_family_ids(run_state: RunState | None) -> set[str]:
    if run_state is None or run_state.feature_graph is None:
        return set()
    feature_instances = getattr(run_state.feature_graph, "feature_instances", None)
    if not isinstance(feature_instances, dict):
        return set()
    families: set[str] = set()
    for feature_instance in feature_instances.values():
        status = str(getattr(feature_instance, "status", "") or "").strip()
        if status != "blocked":
            continue
        family_id = str(getattr(feature_instance, "family_id", "") or "").strip()
        if family_id:
            families.add(family_id)
    return families


def _preferred_probe_families_for_turn(run_state: RunState) -> list[str]:
    families: list[str] = []

    def _append(raw_family_id: Any) -> None:
        family_id = str(raw_family_id or "").strip()
        if not family_id or family_id in families:
            return
        families.append(family_id)

    graph = run_state.feature_graph
    if graph is not None:
        feature_probe_node = graph.nodes.get("evidence.feature_probes")
        if feature_probe_node is not None:
            detected_families = (
                feature_probe_node.attributes.get("detected_families")
                if isinstance(feature_probe_node.attributes, dict)
                else None
            )
            if isinstance(detected_families, list):
                for family_id in detected_families:
                    _append(family_id)
            if families:
                return families
        raw_packets = getattr(graph, "repair_packets", None)
        if isinstance(raw_packets, dict):
            for packet in reversed(list(raw_packets.values())):
                if bool(getattr(packet, "stale", False)):
                    continue
                _append(getattr(packet, "family_id", ""))
        raw_patches = getattr(graph, "repair_patches", None)
        feature_instances = (
            getattr(graph, "feature_instances", {})
            if isinstance(getattr(graph, "feature_instances", {}), dict)
            else {}
        )
        if isinstance(raw_patches, dict):
            for patch in reversed(list(raw_patches.values())):
                if bool(getattr(patch, "stale", False)):
                    continue
                for instance_id in getattr(patch, "feature_instance_ids", []) or []:
                    feature_instance = feature_instances.get(instance_id)
                    if feature_instance is None:
                        continue
                    _append(getattr(feature_instance, "family_id", ""))
        for feature_instance in feature_instances.values():
            status = str(getattr(feature_instance, "status", "") or "").strip()
            if status not in {"active", "blocked", "observed"}:
                continue
            _append(getattr(feature_instance, "family_id", ""))
        bindings = getattr(graph, "bindings", None)
        if isinstance(bindings, dict):
            for binding in reversed(list(bindings.values())):
                if bool(getattr(binding, "stale", False)):
                    continue
                for family_id in getattr(binding, "family_ids", []) or []:
                    _append(family_id)
                if families:
                    break
        for node_id in getattr(graph, "active_node_ids", []) or []:
            if not isinstance(node_id, str):
                continue
            if node_id.startswith("feature."):
                _append(node_id.split(".", 1)[1])
            elif node_id.startswith("feature:"):
                _append(node_id.split(":", 1)[1])

    latest_validation = run_state.latest_validation
    for family_id in taxonomy_family_ids_from_validation_payload(latest_validation):
        _append(family_id)

    for family_id in recommended_feature_probe_families(
        requirements=run_state.requirements,
        latest_validation=run_state.latest_validation,
    ):
        _append(family_id)
    if not families:
        families.append("general_geometry")
    return families


def _latest_validation_prefers_topology_refresh(
    latest_validation: dict[str, Any] | None,
) -> bool:
    if not isinstance(latest_validation, dict):
        return False
    if "local_finish" in taxonomy_repair_lanes_from_validation_payload(latest_validation):
        return True
    for field_name in ("repair_hints", "decision_hints"):
        hints = latest_validation.get(field_name)
        if not isinstance(hints, list):
            continue
        for hint in hints:
            if str(hint or "").strip().lower() == "query_topology":
                return True
    return False


def _latest_feature_probes_prefer_topology_refresh(run_state: RunState) -> bool:
    for turn in reversed(run_state.turns):
        for result in reversed(turn.tool_results):
            if result.name != "query_feature_probes" or not result.success:
                continue
            payload = result.payload if isinstance(result.payload, dict) else {}
            if _feature_probe_payload_has_general_geometry_grounding_gap(
                payload
            ) and not _feature_probe_payload_allows_hybrid_topology_refresh(
                run_state,
                payload,
            ):
                return False
            for probe in payload.get("probes") or []:
                if not isinstance(probe, dict):
                    continue
                recommended_next_tools = {
                    str(item or "").strip().lower()
                    for item in (probe.get("recommended_next_tools") or [])
                    if str(item or "").strip()
                }
                if "query_topology" in recommended_next_tools:
                    return True
                anchor_summary = (
                    probe.get("anchor_summary")
                    if isinstance(probe.get("anchor_summary"), dict)
                    else {}
                )
                if bool(anchor_summary.get("requires_topology_host_ranking")):
                    return True
                grounding_blockers = {
                    str(item or "").strip()
                    for item in (probe.get("grounding_blockers") or [])
                    if str(item or "").strip()
                }
                if "need_topology_host_selection" in grounding_blockers:
                    return True
            return False
    return False


def _latest_feature_probes_allow_topology_refresh_despite_general_geometry_gap(
    run_state: RunState,
    *,
    after_round: int | None = None,
) -> bool:
    for turn in reversed(run_state.turns):
        if after_round is not None and turn.round_no <= after_round:
            continue
        for result in reversed(turn.tool_results):
            if result.name != "query_feature_probes" or not result.success:
                continue
            payload = result.payload if isinstance(result.payload, dict) else {}
            return _feature_probe_payload_allows_hybrid_topology_refresh(
                run_state,
                payload,
            )
    return False


def _latest_feature_probes_have_general_geometry_grounding_gap(
    run_state: RunState,
    *,
    after_round: int | None = None,
) -> bool:
    for turn in reversed(run_state.turns):
        if after_round is not None and turn.round_no <= after_round:
            continue
        for result in reversed(turn.tool_results):
            if result.name != "query_feature_probes" or not result.success:
                continue
            payload = result.payload if isinstance(result.payload, dict) else {}
            return _feature_probe_payload_has_general_geometry_grounding_gap(payload)
    return False


def _feature_probe_payload_has_general_geometry_grounding_gap(
    payload: dict[str, Any] | None,
) -> bool:
    if not isinstance(payload, dict):
        return False
    for probe in payload.get("probes") or []:
        if not isinstance(probe, dict):
            continue
        if str(probe.get("family") or "").strip() != "general_geometry":
            continue
        grounding_blockers = {
            str(item or "").strip()
            for item in (probe.get("grounding_blockers") or [])
            if str(item or "").strip()
        }
        if grounding_blockers and not bool(probe.get("success")):
            return True
        recommended_next_tools = {
            str(item or "").strip().lower()
            for item in (probe.get("recommended_next_tools") or [])
            if str(item or "").strip()
        }
        if {"query_geometry", "query_snapshot"}.intersection(recommended_next_tools) and not bool(
            probe.get("success")
        ):
            return True
    return False


def _feature_probe_payload_allows_hybrid_topology_refresh(
    run_state: RunState,
    payload: dict[str, Any] | None,
) -> bool:
    if not _feature_probe_payload_has_general_geometry_grounding_gap(payload):
        return False
    if not _feature_probe_payload_has_topology_refresh_signal(payload):
        return False
    return _latest_write_geometry_is_close_enough_for_topology_refresh(
        run_state,
        payload,
    )


def _feature_probe_payload_has_topology_refresh_signal(
    payload: dict[str, Any] | None,
) -> bool:
    if not isinstance(payload, dict):
        return False
    for probe in payload.get("probes") or []:
        if not isinstance(probe, dict):
            continue
        recommended_next_tools = {
            str(item or "").strip().lower()
            for item in (probe.get("recommended_next_tools") or [])
            if str(item or "").strip()
        }
        if "query_topology" in recommended_next_tools:
            return True
        anchor_summary = (
            probe.get("anchor_summary")
            if isinstance(probe.get("anchor_summary"), dict)
            else {}
        )
        if bool(anchor_summary.get("requires_topology_host_ranking")):
            return True
        grounding_blockers = {
            str(item or "").strip()
            for item in (probe.get("grounding_blockers") or [])
            if str(item or "").strip()
        }
        if "need_topology_host_selection" in grounding_blockers:
            return True
    return False


def _feature_probe_payload_has_non_general_topology_refresh_signal(
    payload: dict[str, Any] | None,
) -> bool:
    if not isinstance(payload, dict):
        return False
    for probe in payload.get("probes") or []:
        if not isinstance(probe, dict):
            continue
        family_id = str(
            probe.get("family")
            or probe.get("family_id")
            or ""
        ).strip()
        if not family_id or family_id == "general_geometry":
            continue
        recommended_next_tools = {
            str(item or "").strip().lower()
            for item in (probe.get("recommended_next_tools") or [])
            if str(item or "").strip()
        }
        if "query_topology" in recommended_next_tools:
            return True
        anchor_summary = (
            probe.get("anchor_summary")
            if isinstance(probe.get("anchor_summary"), dict)
            else {}
        )
        if bool(anchor_summary.get("requires_topology_host_ranking")):
            return True
        grounding_blockers = {
            str(item or "").strip()
            for item in (probe.get("grounding_blockers") or [])
            if str(item or "").strip()
        }
        if "need_topology_host_selection" in grounding_blockers:
            return True
    return False


def _latest_write_geometry_is_close_enough_for_topology_refresh(
    run_state: RunState,
    payload: dict[str, Any] | None,
) -> bool:
    latest_write_payload = (
        run_state.latest_write_payload if isinstance(run_state.latest_write_payload, dict) else {}
    )
    if not _payload_has_positive_session_backed_solid(latest_write_payload):
        return False
    snapshot = (
        latest_write_payload.get("snapshot")
        if isinstance(latest_write_payload.get("snapshot"), dict)
        else {}
    )
    geometry = snapshot.get("geometry") if isinstance(snapshot.get("geometry"), dict) else {}
    actual_solids = int(geometry.get("solids", 0) or 0)
    actual_bbox_raw = geometry.get("bbox")
    actual_bbox = (
        [float(item or 0.0) for item in actual_bbox_raw[:3]]
        if isinstance(actual_bbox_raw, list) and len(actual_bbox_raw) >= 3
        else []
    )
    if not actual_bbox or any(value <= 0.0 for value in actual_bbox):
        return False
    expected_bbox: list[float] = []
    expected_part_count: int | None = None
    if isinstance(payload, dict):
        for probe in payload.get("probes") or []:
            if not isinstance(probe, dict):
                continue
            anchor_summary = (
                probe.get("anchor_summary")
                if isinstance(probe.get("anchor_summary"), dict)
                else {}
            )
            if not expected_bbox:
                expected_bbox_raw = anchor_summary.get("expected_bbox")
                if isinstance(expected_bbox_raw, list) and len(expected_bbox_raw) >= 3:
                    expected_bbox = [float(item or 0.0) for item in expected_bbox_raw[:3]]
            if expected_part_count is None:
                raw_expected_part_count = anchor_summary.get("expected_part_count")
                if isinstance(raw_expected_part_count, int):
                    expected_part_count = raw_expected_part_count
    if not expected_bbox and expected_part_count is None:
        return False
    max_bbox_rel_diff = 0.0
    if expected_bbox:
        max_bbox_rel_diff = max(
            abs(actual - expected) / max(abs(expected), 1.0)
            for actual, expected in zip(actual_bbox, expected_bbox, strict=False)
        )
        if max_bbox_rel_diff > 0.35:
            return False
    if (
        expected_part_count is not None
        and expected_part_count > 0
        and abs(actual_solids - expected_part_count) > 1
    ):
        solid_gap = abs(actual_solids - expected_part_count)
        if solid_gap > 2:
            return False
        if not expected_bbox or max_bbox_rel_diff > 0.12:
            return False
        if not _feature_probe_payload_has_non_general_topology_refresh_signal(payload):
            return False
    return True


def _latest_feature_probe_preferred_tools_for_turn(
    run_state: RunState,
    *,
    all_tool_names: list[str],
    after_round: int | None = None,
) -> list[str]:
    preferred_tools: list[str] = []

    def _append(raw_tool_name: Any) -> None:
        tool_name = str(raw_tool_name or "").strip()
        if (
            not tool_name
            or tool_name in preferred_tools
            or tool_name not in all_tool_names
        ):
            return
        preferred_tools.append(tool_name)

    for turn in reversed(run_state.turns):
        if after_round is not None and turn.round_no <= after_round:
            continue
        for result in reversed(turn.tool_results):
            if result.name != "query_feature_probes" or not result.success:
                continue
            payload = result.payload if isinstance(result.payload, dict) else {}
            for probe in payload.get("probes") or []:
                if not isinstance(probe, dict):
                    continue
                anchor_summary = (
                    probe.get("anchor_summary")
                    if isinstance(probe.get("anchor_summary"), dict)
                    else {}
                )
                grounding_blockers = {
                    str(item or "").strip()
                    for item in (probe.get("grounding_blockers") or [])
                    if str(item or "").strip()
                }
                if bool(anchor_summary.get("requires_topology_host_ranking")) or (
                    "need_topology_host_selection" in grounding_blockers
                ):
                    _append("query_topology")
                for tool_name in probe.get("recommended_next_tools") or []:
                    _append(tool_name)
            return preferred_tools
    return preferred_tools


def _post_semantic_refresh_followup_tools_for_turn(
    run_state: RunState,
    *,
    write_round: int,
    all_tool_names: list[str],
) -> list[str]:
    focused_followup_set = {
        "apply_cad_action",
        "query_topology",
        "query_feature_probes",
        "query_geometry",
        "query_kernel_state",
        "execute_build123d_probe",
    }
    preferred_tools = _latest_feature_probe_preferred_tools_for_turn(
        run_state,
        all_tool_names=all_tool_names,
        after_round=write_round,
    )
    followup_tools: list[str] = []
    for tool_name in preferred_tools:
        if tool_name not in focused_followup_set or tool_name in followup_tools:
            continue
        followup_tools.append(tool_name)
    if (
        "apply_cad_action" in followup_tools
        and "query_topology" in followup_tools
        and not _has_actionable_topology_targeting_since_round(
            run_state,
            after_round=write_round,
        )
    ):
        reordered = ["query_topology", "apply_cad_action"]
        followup_tools = reordered + [
            tool_name
            for tool_name in followup_tools
            if tool_name not in {"query_topology", "apply_cad_action"}
        ]
    if "query_kernel_state" in all_tool_names and "query_kernel_state" not in followup_tools:
        followup_tools.append("query_kernel_state")
    return followup_tools


def _semantic_refresh_allowed_tool_names_for_turn(
    run_state: RunState,
    *,
    all_tool_names: list[str],
) -> list[str]:
    allowed = [
        name for name in all_tool_names if name in _SEMANTIC_REFRESH_REPAIR_TOOL_SET
    ]
    if (
        (
            _latest_validation_prefers_topology_refresh(run_state.latest_validation)
            or _latest_feature_probes_prefer_topology_refresh(run_state)
        )
        and "query_topology" in all_tool_names
        and "query_topology" not in allowed
    ):
        allowed.append("query_topology")
    return allowed


def _preferred_validation_assessment_tools_for_turn(
    run_state: RunState,
    *,
    all_tool_names: list[str],
) -> list[str]:
    preferred_tools: list[str] = []

    def _append(raw_tool_name: Any) -> None:
        tool_name = str(raw_tool_name or "").strip()
        if (
            not tool_name
            or tool_name in preferred_tools
            or tool_name not in all_tool_names
        ):
            return
        preferred_tools.append(tool_name)

    graph = run_state.feature_graph
    assessment = (
        getattr(graph, "latest_validation_assessment", None)
        if graph is not None
        else None
    )
    feature_probe_tools = _latest_feature_probe_preferred_tools_for_turn(
        run_state,
        all_tool_names=all_tool_names,
    )
    for tool_name in feature_probe_tools:
        _append(tool_name)

    if assessment is not None:
        for tool_name in getattr(assessment, "recommended_next_tools", []) or []:
            _append(tool_name)

    if not preferred_tools:
        latest_validation = run_state.latest_validation or {}
        for field_name in ("repair_hints", "decision_hints"):
            hints = latest_validation.get(field_name)
            if not isinstance(hints, list):
                continue
            for hint in hints:
                normalized = str(hint or "").strip().lower()
                if normalized == "inspect_more_evidence":
                    _append("query_feature_probes")
                    _append("query_kernel_state")
                elif normalized == "inspect count or placement with geometry/topology evidence":
                    _append("query_topology")
                    _append("query_feature_probes")
                else:
                    _append(normalized)

    if not preferred_tools and _latest_validation_prefers_topology_refresh(run_state.latest_validation):
        _append("query_topology")
        _append("query_feature_probes")
        _append("query_kernel_state")

    if not preferred_tools:
        _append("query_feature_probes")
        _append("query_kernel_state")
    return preferred_tools


def _turn_policy_from_actionable_kernel_patch(
    *,
    round_no: int,
    all_tool_names: list[str],
    policy_id: str,
    reason: str,
    patch: dict[str, Any],
) -> TurnToolPolicy:
    repair_mode = str(patch.get("repair_mode") or "").strip() or "subtree_rebuild"
    families = [
        str(item).strip()
        for item in (patch.get("families") or [])
        if isinstance(item, str) and str(item).strip()
    ]
    repair_packet = patch.get("repair_packet")
    if repair_mode == "local_edit":
        allowed_tool_names = [
            name
            for name in all_tool_names
            if name in {"apply_cad_action", "query_topology"}
        ]
        blocked_tool_names = [
            name for name in all_tool_names if name not in set(allowed_tool_names)
        ]
        return TurnToolPolicy(
            round_no=round_no,
            policy_id=policy_id,
            mode="local_finish",
            reason=reason,
            allowed_tool_names=allowed_tool_names,
            blocked_tool_names=blocked_tool_names,
            preferred_tool_names=["apply_cad_action", "query_topology"],
            preferred_probe_families=families,
        )

    if (
        "execute_repair_packet" in all_tool_names
        and supports_runtime_repair_packet(repair_packet if isinstance(repair_packet, dict) else None)
    ):
        allowed_tool_names = ["execute_repair_packet"]
        blocked_tool_names = [
            name for name in all_tool_names if name not in set(allowed_tool_names)
        ]
        return TurnToolPolicy(
            round_no=round_no,
            policy_id=policy_id,
            mode="code_repair",
            reason=reason,
            allowed_tool_names=allowed_tool_names,
            blocked_tool_names=blocked_tool_names,
            preferred_tool_names=["execute_repair_packet"],
            preferred_probe_families=families,
        )

    allowed_tool_names = [
        name for name in all_tool_names if name == "execute_build123d"
    ]
    blocked_tool_names = [
        name for name in all_tool_names if name not in set(allowed_tool_names)
    ]
    return TurnToolPolicy(
        round_no=round_no,
        policy_id=policy_id,
        mode="code_repair",
        reason=reason,
        allowed_tool_names=allowed_tool_names,
        blocked_tool_names=blocked_tool_names,
        preferred_tool_names=["execute_build123d"],
        preferred_probe_families=families,
    )


def _short_budget_after_topology_refresh_requires_actionable_repair(
    *,
    run_state: RunState,
    write_round: int,
    max_rounds: int,
) -> bool:
    remaining_rounds = max(max_rounds - len(run_state.turns), 0)
    if remaining_rounds > 2:
        return False
    return _has_tool_turn_since_round(
        run_state,
        after_round=write_round,
        tool_names={"query_topology"},
    )


def _has_recent_semantic_refresh_before_round(
    run_state: RunState,
    *,
    before_round: int,
    lookback_rounds: int = 4,
) -> bool:
    threshold = max(before_round - lookback_rounds, 0)
    for turn in reversed(run_state.turns):
        if turn.round_no >= before_round:
            continue
        if turn.round_no <= threshold:
            break
        used_tool_names = {
            tool.name for tool in turn.tool_calls
        } | {
            result.name for result in turn.tool_results
        }
        if used_tool_names.intersection(_SEMANTIC_REFRESH_COMPLETION_TOOL_SET):
            return True
    return False


def _filter_supported_round_tool_names(
    *,
    run_state: RunState,
    tool_names: set[str],
) -> set[str]:
    filtered = set(tool_names)
    if "execute_repair_packet" not in filtered:
        return filtered
    graph = run_state.feature_graph
    if graph is None:
        filtered.discard("execute_repair_packet")
        return filtered
    raw_packets = getattr(graph, "repair_packets", None)
    if not isinstance(raw_packets, dict) or not raw_packets:
        filtered.discard("execute_repair_packet")
        return filtered
    latest_packet: dict[str, Any] | None = None
    for packet in reversed(list(raw_packets.values())):
        if bool(getattr(packet, "stale", False)):
            continue
        latest_packet = packet.to_dict() if hasattr(packet, "to_dict") else None
        break
    if not supports_runtime_repair_packet(latest_packet):
        filtered.discard("execute_repair_packet")
    return filtered


def _has_tool_turn_since_round(
    run_state: RunState,
    *,
    after_round: int,
    tool_names: set[str],
) -> bool:
    for turn in run_state.turns:
        if turn.round_no <= after_round:
            continue
        used_tool_names = {
            tool.name for tool in turn.tool_calls
        } | {
            result.name for result in turn.tool_results
        }
        if used_tool_names.intersection(tool_names):
            return True
    return False


def _latest_validation_is_fresh_for_write(
    run_state: RunState,
    *,
    write_round: int,
) -> bool:
    latest_validation = (
        run_state.latest_validation if isinstance(run_state.latest_validation, dict) else {}
    )
    latest_validation_round = max(
        (
            int(event.round_no)
            for event in run_state.agent_events
            if event.kind == "validation_result"
            and isinstance(event.round_no, int)
        ),
        default=-1,
    )
    if latest_validation_round < write_round:
        return False
    blockers = latest_validation.get("blockers")
    if isinstance(blockers, list) and blockers:
        return True
    if latest_validation.get("is_complete") is False:
        return True
    if bool(latest_validation.get("insufficient_evidence")):
        return True
    return bool(latest_validation)


def _latest_validation_round(run_state: RunState) -> int:
    return max(
        (
            int(event.round_no)
            for event in run_state.agent_events
            if event.kind == "validation_result"
            and isinstance(event.round_no, int)
        ),
        default=-1,
    )


def _blockers_are_local_structured_tail(blockers: list[str]) -> bool:
    blocker_set = {item for item in blockers if isinstance(item, str)}
    if not blocker_set:
        return False
    return blocker_set.issubset({"feature_fillet", "feature_chamfer"})


def _latest_validation_prefers_semantic_refresh(
    latest_validation: dict[str, Any] | None,
) -> bool:
    if not isinstance(latest_validation, dict):
        return False
    if _latest_validation_has_actionable_geometry_contradiction(
        latest_validation,
        min_coverage=0.4,
    ):
        return False
    if bool(latest_validation.get("insufficient_evidence")):
        return True
    observation_tags = {
        str(item).strip().lower()
        for item in (latest_validation.get("observation_tags") or [])
        if isinstance(item, str) and str(item).strip()
    }
    if "insufficient_evidence" in observation_tags:
        return True
    decision_hints = {
        str(item).strip().lower()
        for item in (latest_validation.get("decision_hints") or [])
        if isinstance(item, str) and str(item).strip()
    }
    if "inspect_more_evidence" in decision_hints:
        return True
    if any(
        "inspect more" in hint and any(token in hint for token in ("evidence", "geometry", "topology"))
        for hint in decision_hints
    ):
        return True
    if any(hint.startswith("no explicit evidence for clause:") for hint in decision_hints):
        return True
    coverage_confidence = latest_validation.get("coverage_confidence")
    return isinstance(coverage_confidence, (int, float)) and float(coverage_confidence) <= 0.25


def _latest_validation_has_budget_skipped_hint(
    latest_validation: dict[str, Any] | None,
) -> bool:
    if not isinstance(latest_validation, dict):
        return False
    for item in (latest_validation.get("decision_hints") or []):
        normalized = str(item or "").strip().lower()
        if normalized.startswith("validation_llm_skipped:"):
            return True
    return False


def _semantic_refresh_followup_should_preempt_closure_validation(
    run_state: RunState,
    *,
    write_round: int,
    all_tool_names: list[str],
) -> bool:
    latest_validation = run_state.latest_validation
    if not _validation_has_evidence_gap(latest_validation):
        return False
    if not (
        _validation_requests_localized_evidence_refresh(latest_validation)
        or _latest_validation_has_budget_skipped_hint(latest_validation)
    ):
        return False
    followup_tools = _post_semantic_refresh_followup_tools_for_turn(
        run_state,
        write_round=write_round,
        all_tool_names=all_tool_names,
    )
    return any(tool_name != "query_kernel_state" for tool_name in followup_tools)


def _latest_validation_has_actionable_geometry_contradiction(
    latest_validation: dict[str, Any] | None,
    *,
    min_coverage: float,
    require_nonempty_evidence: bool = True,
) -> bool:
    if not isinstance(latest_validation, dict):
        return False
    coverage_confidence = latest_validation.get("coverage_confidence")
    if not isinstance(coverage_confidence, (int, float)):
        return False
    if float(coverage_confidence) < float(min_coverage):
        return False
    clause_interpretations = latest_validation.get("clause_interpretations")
    if not isinstance(clause_interpretations, list):
        return False
    process_only_evidence_markers = (
        "no explicit evidence for clause:",
        "no cutting action observed",
        "no sweep action observed",
        "no revolve action observed",
        "no fillet action observed",
        "no chamfer action observed",
        "sketch-related evidence exists in the process history",
        "setup/process clause is not directly verifiable",
        "ui/navigation clause is not directly verifiable",
        "construction-constraint clause is not directly verifiable",
        "construction-method clause is not directly verifiable",
    )
    for clause in clause_interpretations:
        if not isinstance(clause, dict):
            continue
        status = str(clause.get("status") or "").strip().lower()
        if status != "contradicted":
            continue
        observation_tags = {
            str(item).strip().lower()
            for item in (clause.get("observation_tags") or [])
            if isinstance(item, str) and str(item).strip()
        }
        if "clause:process_setup" in observation_tags:
            continue
        evidence = str(clause.get("evidence") or "").strip().lower()
        if require_nonempty_evidence and not evidence:
            continue
        if evidence and any(marker in evidence for marker in process_only_evidence_markers):
            continue
        return True
    return False


def _latest_validation_has_actionable_single_blocker(
    latest_validation: dict[str, Any] | None,
) -> bool:
    if not isinstance(latest_validation, dict):
        return False
    blockers = [
        str(item).strip()
        for item in (latest_validation.get("blockers") or [])
        if isinstance(item, str) and str(item).strip()
    ]
    blocker_set = set(blockers)
    if len(blocker_set) != 1:
        return False
    if blocker_set.isdisjoint(
        {
            "feature_countersink",
            "feature_counterbore",
            "feature_hole",
            "feature_hole_exact_center_set",
            "feature_hole_position_alignment",
            "feature_local_anchor_alignment",
            "feature_target_face_additive_merge",
            "feature_target_face_subtractive_merge",
        }
    ):
        return False
    coverage_confidence = latest_validation.get("coverage_confidence")
    if not isinstance(coverage_confidence, (int, float)):
        return False
    if float(coverage_confidence) < 0.5:
        return False
    decision_hints = {
        str(item).strip().lower()
        for item in (latest_validation.get("decision_hints") or [])
        if isinstance(item, str) and str(item).strip()
    }
    if "inspect_more_evidence" in decision_hints:
        return False
    if any(hint.startswith("no explicit evidence for clause:") for hint in decision_hints):
        return False
    return True


def _blockers_prefer_probe_first_after_code_write(blockers: list[str]) -> bool:
    blocker_set = {item for item in blockers if isinstance(item, str)}
    if not blocker_set:
        return False
    return bool(
        blocker_set.intersection(
            {
                "feature_annular_groove",
                "feature_revolved_groove_setup",
                "feature_revolved_groove_alignment",
                "feature_revolved_groove_result",
                "feature_profile_shape_alignment",
                "feature_pattern",
                "feature_pattern_seed_alignment",
                "feature_hole_position_alignment",
                "feature_local_anchor_alignment",
            }
        )
    )


def _has_repeated_validation_blockers_without_semantic_refresh(
    run_state: RunState,
    *,
    blockers: list[str],
    min_repeats: int = 2,
) -> bool:
    normalized_target = tuple(
        sorted(item for item in blockers if isinstance(item, str) and item.strip())
    )
    if not normalized_target:
        return False
    repeat_count = 0
    for event in reversed(run_state.agent_events):
        if event.kind != "validation_result":
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        event_blockers = payload.get("blockers")
        normalized_event: tuple[str, ...] = ()
        if isinstance(event_blockers, list):
            normalized_event = tuple(
                sorted(
                    item
                    for item in event_blockers
                    if isinstance(item, str) and item.strip()
                )
            )
        else:
            # Older traces and a few synthetic tests only recorded the validation
            # summary/is_complete flags. Treat those incomplete validation events as
            # matching the current blocker set so probe-only turns cannot silently
            # bypass the semantic-refresh guard.
            summary_text = str(payload.get("summary") or "").strip().lower()
            is_incomplete = payload.get("is_complete") is False or "blocker" in summary_text
            if is_incomplete:
                normalized_event = normalized_target
        if not normalized_event:
            continue
        if normalized_event == normalized_target:
            repeat_count += 1
            if repeat_count >= min_repeats:
                return True
            continue
        break
    return False


def _has_repeated_validation_without_new_evidence_after_write(
    run_state: RunState,
    *,
    write_round: int,
    min_validations: int = 2,
) -> bool:
    if _has_tool_turn_since_round(
        run_state,
        after_round=write_round,
        tool_names={
            "query_kernel_state",
            "query_feature_probes",
            "execute_build123d_probe",
        },
    ):
        return False
    validation_rounds = {
        int(event.round_no)
        for event in run_state.agent_events
        if event.kind == "validation_result"
        and isinstance(event.round_no, int)
        and int(event.round_no) >= write_round
    }
    return len(validation_rounds) >= min_validations


def _latest_incomplete_finish_round(run_state: RunState) -> int | None:
    latest_validation = (
        run_state.latest_validation if isinstance(run_state.latest_validation, dict) else {}
    )
    if bool(latest_validation.get("is_complete")):
        return None
    for envelope in reversed(run_state.turn_envelopes):
        if envelope.stop_reason == "finish_requested_but_validation_incomplete":
            return envelope.round_no
    for turn in reversed(run_state.turns):
        if turn.requested_finish:
            return turn.round_no
    return None


def _latest_turn_tool_policy_id(run_state: RunState) -> str | None:
    if not run_state.turn_tool_policies:
        return None
    return str(run_state.turn_tool_policies[-1].policy_id or "").strip() or None


def _has_turn_tool_policy_since_round(
    run_state: RunState,
    *,
    after_round: int,
    policy_ids: set[str],
) -> bool:
    for policy in run_state.turn_tool_policies:
        if policy.round_no <= after_round:
            continue
        if policy.policy_id in policy_ids:
            return True
    return False


def _infer_runtime_failure_cluster(run_state: RunState) -> str | None:
    last_error = str(run_state.previous_error or "").strip().lower()
    latest_validation = (
        run_state.latest_validation if isinstance(run_state.latest_validation, dict) else {}
    )
    if _is_successful_validation(latest_validation):
        return None
    blockers = latest_validation.get("blockers")
    blocker_list = [item for item in blockers if isinstance(item, str)] if isinstance(blockers, list) else []
    taxonomy_families = taxonomy_family_ids_from_validation_payload(latest_validation)
    taxonomy_repair_lanes = taxonomy_repair_lanes_from_validation_payload(
        latest_validation
    )
    if any(
        token in last_error
        for token in ("no step", "model.step", "step file", "step export")
    ):
        return "missing_step_gap"
    if run_state.feature_probe_count or run_state.probe_code_count:
        if taxonomy_families:
            return "code_path_family_gap"
        if any("annular" in item or "revolve" in item for item in blocker_list):
            return "code_path_family_gap"
    if run_state.inspection_only_rounds >= max(2, len(run_state.turns) // 2):
        return "read_stall_gap"
    if taxonomy_repair_lanes and set(taxonomy_repair_lanes) == {"probe_first"}:
        return "tool_gap"
    if blocker_list:
        return "tool_gap"
    if last_error:
        return "runtime_gap"
    return None


def _parse_tool_envelope(response: Any) -> LLMToolResponse:
    raw_content = getattr(response, "content", "")
    usage = getattr(response, "usage", None)
    try:
        payload = json.loads(raw_content)
    except Exception:
        return LLMToolResponse(
            content=str(raw_content),
            tool_calls=[],
            usage=usage,
            finish_reason="fallback_plain_text",
        )
    if not isinstance(payload, dict):
        return LLMToolResponse(
            content=str(raw_content),
            tool_calls=[],
            usage=usage,
            finish_reason="fallback_non_dict",
        )
    tool_calls_raw = payload.get("tool_calls")
    tool_calls: list[LLMToolCall] = []
    if isinstance(tool_calls_raw, list):
        for item in tool_calls_raw:
            if not isinstance(item, dict):
                continue
            name = item.get("name")
            arguments = item.get("arguments")
            if not isinstance(name, str) or not isinstance(arguments, dict):
                continue
            tool_calls.append(LLMToolCall(name=name, arguments=arguments))
    return LLMToolResponse(
        content=str(payload.get("decision_summary") or raw_content),
        tool_calls=tool_calls,
        usage=usage,
        finish_reason="fallback_tool_envelope",
    )


def _should_auto_validate_after_non_progress(run_state: RunState) -> bool:
    if len(run_state.turns) < 2:
        return False
    recent = run_state.turns[-2:]
    if not all(turn.write_tool_name is None for turn in recent):
        return False
    latest_turn = recent[-1]
    if any(tool_call.name == "validate_requirement" for tool_call in latest_turn.tool_calls):
        return False
    if any(
        result.name == "validate_requirement" and result.success
        for result in latest_turn.tool_results
    ):
        return False
    return True


def _should_auto_validate_after_post_write(
    *,
    run_state: RunState,
    turn: TurnRecord,
    round_no: int,
    max_rounds: int,
) -> bool:
    write_results = [
        result
        for result in turn.tool_results
        if result.category == ToolCategory.WRITE and result.success
    ]
    if len(write_results) != 1:
        return False
    write_result = write_results[0]
    if write_result.name not in {
        "execute_build123d",
        "execute_repair_packet",
        "apply_cad_action",
    }:
        return False
    if (
        write_result.name == "apply_cad_action"
        and _turn_has_open_sketch_window_after_successful_apply(turn)
    ):
        return False
    latest_validation = run_state.latest_validation or {}
    prior_blockers = latest_validation.get("blockers")
    if not isinstance(prior_blockers, list) or not prior_blockers:
        for event in reversed(run_state.agent_events):
            if event.kind != "validation_result":
                continue
            if not isinstance(event.round_no, int) or event.round_no >= round_no:
                continue
            payload = event.payload if isinstance(event.payload, dict) else {}
            event_blockers = payload.get("blockers")
            if isinstance(event_blockers, list):
                prior_blockers = event_blockers
                break
    remaining_rounds = max(max_rounds - round_no, 0)
    prior_successful_positive_writes = 0
    for previous_turn in run_state.turns:
        if previous_turn is turn:
            continue
        for previous_result in previous_turn.tool_results:
            if (
                previous_result.category == ToolCategory.WRITE
                and previous_result.success
                and _result_has_positive_session_backed_solid(previous_result)
            ):
                prior_successful_positive_writes += 1
    has_positive_solid = _result_has_positive_session_backed_solid(write_result)
    no_prior_validation = not isinstance(run_state.latest_validation, dict)
    should_probe_first_code_write = (
        no_prior_validation
        and prior_successful_positive_writes == 0
        and has_positive_solid
    )
    should_close_existing_blockers = bool(prior_blockers) and has_positive_solid
    return (
        should_close_existing_blockers
        or remaining_rounds <= 1
        or should_probe_first_code_write
    )


def _tool_call_apply_action_type(tool_call: ToolCallRecord) -> str | None:
    if tool_call.category != ToolCategory.WRITE or tool_call.name != "apply_cad_action":
        return None
    action_type = tool_call.arguments.get("action_type")
    if isinstance(action_type, str) and action_type.strip():
        return action_type.strip().lower()
    return None


def _latest_apply_action_type_from_turn(turn: TurnRecord | None) -> str | None:
    if turn is None:
        return None
    for tool_call in reversed(turn.tool_calls):
        action_type = _tool_call_apply_action_type(tool_call)
        if action_type:
            return action_type
    return None


def _turn_has_open_sketch_window_after_successful_apply(turn: TurnRecord | None) -> bool:
    action_type = _latest_apply_action_type_from_turn(turn)
    return bool(action_type and action_type in _SKETCH_WINDOW_CONTINUATION_ACTIONS)


def _latest_successful_apply_action_type_with_open_sketch_window(
    run_state: RunState,
) -> str | None:
    latest_successful_write_turn = run_state.latest_successful_write_turn
    if (
        latest_successful_write_turn is None
        or latest_successful_write_turn.write_tool_name != "apply_cad_action"
    ):
        return None
    action_type = _latest_apply_action_type_from_turn(latest_successful_write_turn)
    if action_type not in _SKETCH_WINDOW_CONTINUATION_ACTIONS:
        return None
    if _has_tool_turn_since_round(
        run_state,
        after_round=latest_successful_write_turn.round_no,
        tool_names={"validate_requirement"},
    ):
        return None
    return action_type


def _open_sketch_window_requires_code_escape(
    *,
    run_state: RunState,
    max_rounds: int,
) -> bool:
    latest_action_type = _latest_successful_apply_action_type_with_open_sketch_window(run_state)
    if latest_action_type is None:
        return False
    remaining_rounds = max(max_rounds - len(run_state.turns), 0)
    if remaining_rounds <= 0:
        return True
    query_sketch_payload = run_state.evidence.latest_by_tool.get("query_sketch")
    sketch_state = (
        query_sketch_payload.get("sketch_state")
        if isinstance(query_sketch_payload, dict)
        else None
    )
    profile_refs = (
        sketch_state.get("profile_refs")
        if isinstance(sketch_state, dict)
        and isinstance(sketch_state.get("profile_refs"), list)
        else []
    )
    path_refs = (
        sketch_state.get("path_refs")
        if isinstance(sketch_state, dict)
        and isinstance(sketch_state.get("path_refs"), list)
        else []
    )
    min_write_steps_remaining = 0
    if latest_action_type == "create_sketch":
        min_write_steps_remaining = 2
    elif profile_refs:
        min_write_steps_remaining = 1
    elif path_refs:
        min_write_steps_remaining = 2
    if min_write_steps_remaining <= 0:
        return False
    return remaining_rounds < min_write_steps_remaining


def _preferred_sketch_window_tools(
    action_type: str,
    *,
    all_tool_names: list[str],
) -> list[str]:
    preferred_order = (
        ["apply_cad_action", "query_sketch"]
        if action_type == "create_sketch"
        else ["query_sketch", "apply_cad_action"]
    )
    return [name for name in preferred_order if name in all_tool_names]


def _result_has_positive_session_backed_solid(result: ToolResultRecord) -> bool:
    payload = result.payload if isinstance(result.payload, dict) else {}
    snapshot = payload.get("snapshot") if isinstance(payload.get("snapshot"), dict) else {}
    geometry = snapshot.get("geometry") if isinstance(snapshot.get("geometry"), dict) else {}
    return (
        int(geometry.get("solids", 0) or 0) > 0
        and abs(float(geometry.get("volume", 0.0) or 0.0)) > 1e-6
        and bool(payload.get("session_state_persisted", False))
    )


def _latest_failed_code_sequence_is_artifactless(run_state: RunState) -> bool:
    payload = run_state.latest_write_payload if isinstance(run_state.latest_write_payload, dict) else {}
    if bool(run_state.latest_step_file):
        return False
    if _payload_has_step_artifact(payload):
        return False
    return not _payload_has_positive_session_backed_solid(payload)


def _payload_has_step_artifact(payload: dict[str, Any]) -> bool:
    step_file = str(payload.get("step_file") or "").strip()
    if step_file:
        return True
    output_files = payload.get("output_files")
    if isinstance(output_files, list):
        return any(
            isinstance(item, str) and item.lower().endswith(".step")
            for item in output_files
        )
    return False


def _payload_has_positive_session_backed_solid(payload: dict[str, Any]) -> bool:
    snapshot = payload.get("snapshot") if isinstance(payload.get("snapshot"), dict) else {}
    geometry = snapshot.get("geometry") if isinstance(snapshot.get("geometry"), dict) else {}
    return (
        int(geometry.get("solids", 0) or 0) > 0
        and abs(float(geometry.get("volume", 0.0) or 0.0)) > 1e-6
        and bool(payload.get("session_state_persisted", False))
    )


def _is_successful_validation(validation_core: dict[str, Any] | None) -> bool:
    if not isinstance(validation_core, dict):
        return False
    return bool(validation_core.get("success")) and bool(
        validation_core.get("is_complete")
    )


def _pick_step_file(output_files: list[str]) -> str | None:
    for filename in output_files:
        if filename.lower().endswith(".step"):
            return filename
    return None


def _pick_render_file(output_files: list[str]) -> str | None:
    for filename in output_files:
        lowered = filename.lower()
        if lowered.endswith(".png") or lowered.endswith(".jpg") or lowered.endswith(".jpeg"):
            return filename
    return None


def _is_environment_blocker(error: str | None) -> bool:
    if not isinstance(error, str):
        return False
    lowered = error.lower()
    return any(
        token in lowered
        for token in (
            "docker",
            "connection closed",
            "mcp request failed",
            "sandbox mcp request failed",
            "no such file or directory",
            "unhandled errors in a taskgroup",
        )
    )


def _should_stop_after_terminal_code_path(turn: TurnRecord) -> bool:
    for result in turn.tool_results:
        if result.name != "execute_build123d" or not result.success:
            continue
        payload = result.payload if isinstance(result.payload, dict) else {}
        if bool(payload.get("session_state_persisted", False)):
            return False
        return True
    return False


def _to_jsonable(value: Any) -> dict[str, Any]:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json", exclude_none=False)
    if hasattr(value, "__dataclass_fields__"):
        return __import__("dataclasses").asdict(value)
    if isinstance(value, dict):
        return value
    if hasattr(value, "__dict__"):
        return {
            key: item
            for key, item in vars(value).items()
            if not key.startswith("_")
        }
    return {"success": False, "error_message": f"unsupported_payload:{type(value)}"}


def _json_default(value: Any) -> Any:
    if isinstance(value, bytes):
        return f"<{len(value)} bytes>"
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json", exclude_none=False)
    if hasattr(value, "__dict__"):
        return {
            key: item
            for key, item in vars(value).items()
            if not key.startswith("_")
        }
    return str(value)


def _trace_payload_summary(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    summary: dict[str, Any] = {}
    for key in (
        "success",
        "summary",
        "is_complete",
        "error_code",
        "session_id",
        "step",
        "step_file",
        "session_state_persisted",
    ):
        value = payload.get(key)
        if value is not None:
            summary[key] = value
    blockers = payload.get("blockers")
    if isinstance(blockers, list):
        summary["blockers"] = blockers[:6]
    output_files = payload.get("output_files")
    if isinstance(output_files, list):
        summary["output_files"] = output_files[:6]
    snapshot = payload.get("snapshot")
    if isinstance(snapshot, dict):
        geometry = snapshot.get("geometry")
        summary["snapshot"] = {
            key: snapshot.get(key)
            for key in ("step", "issues")
            if key in snapshot
        }
        if isinstance(geometry, dict):
            summary["snapshot"]["geometry"] = {
                key: geometry.get(key)
                for key in ("solids", "faces", "edges", "volume", "bbox")
                if key in geometry
            }
    return summary
