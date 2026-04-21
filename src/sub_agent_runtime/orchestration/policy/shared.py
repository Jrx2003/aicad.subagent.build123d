from __future__ import annotations

import json
import datetime as dt
from pathlib import Path
from typing import Any

from common.config import Settings, settings
from common.blocker_taxonomy import (
    classify_blocker_taxonomy_many,
)
from common.logging import get_logger
from llm.factory import create_provider_client
from llm.interface import LLMMessage, LLMToolCall, LLMToolResponse
from sub_agent_runtime.prompting.context_builder import V2ContextManager
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
from sub_agent_runtime.semantic_kernel.digest import build_domain_kernel_digest
from sub_agent_runtime.semantic_kernel.sync import (
    initialize_domain_kernel_state,
    sync_domain_kernel_state_from_tool_result,
)
from sub_agent_runtime.semantic_kernel.repair_packets import (
    supports_runtime_repair_packet,
)
from sub_agent_runtime.prompting.skill_assembly import (
    recommended_feature_probe_families,
    requirement_prefers_code_first_family,
)
from sub_agent_runtime.tooling.execution import ToolRuntime
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
from sub_agent_runtime.hallucination import build_run_hallucination_summary
from sub_agent_runtime.hooks import RuntimeHookManager

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
            packet_observability_events = _build_repair_packet_round_observability_events(
                run_state=run_state,
                round_no=round_no,
                allowed_tool_names=allowed_tool_names,
            )
            if packet_observability_events:
                run_state.add_tool_execution_events(packet_observability_events)
                for tool_event in packet_observability_events:
                    self._append_tool_timeline(tool_timeline_trace, tool_event)
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
            if _turn_has_successful_validation_completion(turn):
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
                        stop_reason="validated_complete",
                        previous_error=None,
                    )
                )
                stop_reason = {
                    "code": "validated_complete",
                    "detail": "validate_requirement returned complete",
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
        repair_packet_observability = _runtime_repair_packet_observability_summary(run_state)
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
            no_op_action_count=run_state.no_op_action_count,
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
            repair_packet_exposed_count=int(
                repair_packet_observability.get("repair_packet_exposed_count", 0) or 0
            ),
            repair_packet_supported_count=int(
                repair_packet_observability.get("repair_packet_supported_count", 0) or 0
            ),
            repair_packet_compile_success_count=int(
                repair_packet_observability.get("repair_packet_compile_success_count", 0) or 0
            ),
            repair_packet_compile_failure_count=int(
                repair_packet_observability.get("repair_packet_compile_failure_count", 0) or 0
            ),
            repair_packet_fallback_count=int(
                repair_packet_observability.get("repair_packet_fallback_count", 0) or 0
            ),
            repair_packet_fallback_reasons=dict(
                repair_packet_observability.get("repair_packet_fallback_reasons") or {}
            ),
            execute_build123d_preflight_fail_count=int(
                repair_packet_observability.get("execute_build123d_preflight_fail_count", 0)
                or 0
            ),
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
            if result.name == "execute_build123d" and not result.success:
                payload = result.payload if isinstance(result.payload, dict) else {}
                error_text = " ".join(
                    item
                    for item in [
                        str(result.error or "").strip(),
                        str(payload.get("error_message") or "").strip(),
                    ]
                    if item
                ).lower()
                if "preflight lint failed" in error_text:
                    context_events.append(
                        ToolExecutionEvent(
                            round_no=round_no,
                            tool_name=result.name,
                            phase="build123d_preflight_failed",
                            category=result.category,
                            success=False,
                            detail={
                                "failure_kind": str(payload.get("failure_kind") or "").strip()
                                or "preflight_lint_failed",
                                "lint_hit_count": len(payload.get("lint_hits") or []),
                            },
                        )
                    )
            if result.name == "execute_repair_packet":
                payload = result.payload if isinstance(result.payload, dict) else {}
                packet_payload = (
                    payload.get("repair_packet")
                    if isinstance(payload.get("repair_packet"), dict)
                    else payload.get("packet")
                    if isinstance(payload.get("packet"), dict)
                    else {}
                )
                event_detail = {
                    "packet_id": str(payload.get("packet_id") or packet_payload.get("packet_id") or "").strip(),
                    "family_id": str(payload.get("family_id") or packet_payload.get("family_id") or "").strip(),
                    "recipe_id": str(payload.get("recipe_id") or packet_payload.get("recipe_id") or "").strip(),
                }
                if bool(payload.get("repair_packet_compile_success")):
                    context_events.append(
                        ToolExecutionEvent(
                            round_no=round_no,
                            tool_name=result.name,
                            phase="repair_packet_compile_succeeded",
                            category=result.category,
                            success=True,
                            detail=event_detail,
                        )
                    )
                elif not result.success:
                    context_events.append(
                        ToolExecutionEvent(
                            round_no=round_no,
                            tool_name=result.name,
                            phase="repair_packet_compile_failed",
                            category=result.category,
                            success=False,
                            detail={
                                **event_detail,
                                "reason": str(
                                    payload.get("repair_packet_compile_failure_reason")
                                    or payload.get("error")
                                    or result.error
                                    or "repair_packet_compile_failed"
                                ).strip(),
                            },
                        )
                    )
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

    if (
        _is_successful_validation(run_state.latest_validation)
        and "finish_run" in all_tool_names
    ):
        allowed_tool_names = [
            name for name in all_tool_names if name == "finish_run"
        ]
        blocked_tool_names = [
            name for name in all_tool_names if name not in set(allowed_tool_names)
        ]
        return TurnToolPolicy(
            round_no=round_no,
            policy_id="finish_after_successful_validation",
            mode="completion_judge",
            reason=(
                "The latest successful validate_requirement already marked the requirement "
                "complete, so close the run instead of reopening read or local-finish lanes."
            ),
            allowed_tool_names=allowed_tool_names,
            blocked_tool_names=blocked_tool_names,
            preferred_tool_names=["finish_run"],
            preferred_probe_families=preferred_probe_families,
        )

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
        sketch_window_requires_apply_write_first = (
            _open_sketch_window_requires_apply_write_first(run_state)
        )
        sketch_window_allowed_tools = (
            {"apply_cad_action"}
            if sketch_window_requires_apply_write_first
            else {"apply_cad_action", "query_sketch"}
        )
        allowed_tool_names = [
            name for name in all_tool_names if name in sketch_window_allowed_tools
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
                if not sketch_window_requires_apply_write_first
                else "The latest successful create_sketch opened a fresh empty sketch window, "
                "so spend the next turn on apply_cad_action to add the first sketch geometry "
                "instead of re-querying the same empty window."
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
        and not _turn_has_open_sketch_window_after_successful_apply(
            latest_successful_structured_write_turn
        )
        and not _latest_validation_is_fresh_for_write(
            run_state,
            write_round=latest_successful_structured_write_turn.round_no,
        )
        and not _has_successful_semantic_refresh_since_round(
            run_state,
            after_round=latest_successful_structured_write_turn.round_no,
        )
        and not _has_tool_turn_since_round(
            run_state,
            after_round=latest_successful_structured_write_turn.round_no,
            tool_names={"validate_requirement"},
        )
    ):
        allowed_tool_names = _semantic_refresh_allowed_tool_names_for_turn(
            run_state,
            all_tool_names=all_tool_names,
        )
        if allowed_tool_names:
            blocked_tool_names = [
                name for name in all_tool_names if name not in set(allowed_tool_names)
            ]
            preferred_tool_names = [
                name
                for name in _preferred_validation_assessment_tools_for_turn(
                    run_state,
                    all_tool_names=all_tool_names,
                )
                if name in allowed_tool_names
            ]
            if not preferred_tool_names:
                preferred_tool_names = list(allowed_tool_names)
            return TurnToolPolicy(
                round_no=round_no,
                policy_id="semantic_refresh_after_successful_local_finish",
                mode="graph_refresh",
                reason=(
                    "A successful local-finish write already changed geometry, but the semantic "
                    "state has not been refreshed since that write. Refresh feature/kernel evidence "
                    "once before reopening whole-part code or budgeting against stale blockers."
                ),
                allowed_tool_names=allowed_tool_names,
                blocked_tool_names=blocked_tool_names,
                preferred_tool_names=preferred_tool_names,
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
                    actionable_kernel_patch = _latest_actionable_kernel_patch(run_state)
                    if (
                        actionable_kernel_patch is not None
                        and isinstance(actionable_kernel_patch.get("repair_packet"), dict)
                    ):
                        return _turn_policy_from_actionable_kernel_patch(
                            round_no=round_no,
                            all_tool_names=all_tool_names,
                            policy_id="repair_packet_after_repeated_concrete_code_failure",
                            reason=(
                                "Repeated concrete execute_build123d failures exposed an actionable "
                                "kernel repair packet, so switch from broad code repair to the "
                                "narrow packet-backed repair lane."
                            ),
                            patch=actionable_kernel_patch,
                        )
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


from sub_agent_runtime.orchestration.policy import (
    code_repair as _code_repair_policy,
    local_finish as _local_finish_policy,
    semantic_refresh as _semantic_refresh_policy,
    validation as _validation_policy,
)

_has_semantic_refresh_turn_since_failed_write = (
    _code_repair_policy._has_semantic_refresh_turn_since_failed_write
)
_has_probe_turn_since_failed_write = (
    _code_repair_policy._has_probe_turn_since_failed_write
)
_has_successful_probe_turn_since_failed_write = (
    _code_repair_policy._has_successful_probe_turn_since_failed_write
)
_has_successful_non_persisted_probe_turn_since_failed_write = (
    _code_repair_policy._has_successful_non_persisted_probe_turn_since_failed_write
)
_has_actionable_probe_turn_since_failed_write = (
    _code_repair_policy._has_actionable_probe_turn_since_failed_write
)
_runtime_repair_packet_observability_summary = (
    _code_repair_policy._runtime_repair_packet_observability_summary
)
_infer_runtime_failure_cluster = _code_repair_policy._infer_runtime_failure_cluster
_latest_failed_code_sequence_is_artifactless = (
    _code_repair_policy._latest_failed_code_sequence_is_artifactless
)
_payload_has_step_artifact = _code_repair_policy._payload_has_step_artifact
_latest_actionable_kernel_patch = _code_repair_policy._latest_actionable_kernel_patch
_filter_supported_round_tool_names = _code_repair_policy._filter_supported_round_tool_names
_build_repair_packet_round_observability_events = (
    _code_repair_policy._build_repair_packet_round_observability_events
)
_blockers_prefer_probe_first_after_code_write = (
    _code_repair_policy._blockers_prefer_probe_first_after_code_write
)
_preferred_probe_families_for_turn = _code_repair_policy._preferred_probe_families_for_turn
_kernel_patch_should_yield_semantic_refresh = (
    _code_repair_policy._kernel_patch_should_yield_semantic_refresh
)
_kernel_patch_should_yield_feature_probe_assessment = (
    _code_repair_policy._kernel_patch_should_yield_feature_probe_assessment
)
_turn_policy_from_actionable_kernel_patch = (
    _code_repair_policy._turn_policy_from_actionable_kernel_patch
)
_short_budget_after_topology_refresh_requires_actionable_repair = (
    _code_repair_policy._short_budget_after_topology_refresh_requires_actionable_repair
)
_feature_probe_recommends_local_finish = (
    _local_finish_policy._feature_probe_recommends_local_finish
)
_latest_feature_probes_recommend_local_finish = (
    _local_finish_policy._latest_feature_probes_recommend_local_finish
)
_latest_feature_probes_recommend_apply_local_finish = (
    _local_finish_policy._latest_feature_probes_recommend_apply_local_finish
)
_latest_feature_probe_apply_local_finish_families = (
    _local_finish_policy._latest_feature_probe_apply_local_finish_families
)
_latest_apply_action_type_from_turn = (
    _local_finish_policy._latest_apply_action_type_from_turn
)
_turn_has_open_sketch_window_after_successful_apply = (
    _local_finish_policy._turn_has_open_sketch_window_after_successful_apply
)
_latest_successful_apply_action_type_with_open_sketch_window = (
    _local_finish_policy._latest_successful_apply_action_type_with_open_sketch_window
)
_latest_successful_tool_payload = _local_finish_policy._latest_successful_tool_payload
_open_sketch_window_requires_code_escape = (
    _local_finish_policy._open_sketch_window_requires_code_escape
)
_preferred_sketch_window_tools = _local_finish_policy._preferred_sketch_window_tools
_open_sketch_window_requires_apply_write_first = (
    _local_finish_policy._open_sketch_window_requires_apply_write_first
)
_successful_local_finish_semantic_refresh_needs_validation = (
    _local_finish_policy._successful_local_finish_semantic_refresh_needs_validation
)
_local_finish_validation_evidence_refresh_tools_for_turn = (
    _local_finish_policy._local_finish_validation_evidence_refresh_tools_for_turn
)
_local_finish_is_actionable_after_semantic_refresh = (
    _local_finish_policy._local_finish_is_actionable_after_semantic_refresh
)
_has_actionable_topology_targeting_since_round = (
    _local_finish_policy._has_actionable_topology_targeting_since_round
)
_local_finish_should_force_apply_after_topology_targeting = (
    _local_finish_policy._local_finish_should_force_apply_after_topology_targeting
)
_local_finish_escape_is_available_after_topology_targeting = (
    _local_finish_policy._local_finish_escape_is_available_after_topology_targeting
)
_local_finish_should_defer_to_actionable_rebuild_patch = (
    _local_finish_policy._local_finish_should_defer_to_actionable_rebuild_patch
)
_local_finish_contract_failure_should_retry_after_topology_refresh = (
    _local_finish_policy._local_finish_contract_failure_should_retry_after_topology_refresh
)
_latest_topology_evidence_is_actionable = (
    _local_finish_policy._latest_topology_evidence_is_actionable
)
_local_finish_validation_evidence_gap_needs_read_refresh = (
    _local_finish_policy._local_finish_validation_evidence_gap_needs_read_refresh
)
_local_finish_validation_evidence_gap_closure_tools_for_turn = (
    _local_finish_policy._local_finish_validation_evidence_gap_closure_tools_for_turn
)
_local_finish_contract_failure_should_retry_with_existing_topology_refs = (
    _local_finish_policy._local_finish_contract_failure_should_retry_with_existing_topology_refs
)
_latest_actionable_semantic_refresh_since_failed_write = (
    _semantic_refresh_policy._latest_actionable_semantic_refresh_since_failed_write
)
_has_recent_semantic_refresh_before_round = (
    _semantic_refresh_policy._has_recent_semantic_refresh_before_round
)
_semantic_refresh_allowed_tool_names_for_turn = (
    _semantic_refresh_policy._semantic_refresh_allowed_tool_names_for_turn
)
_latest_feature_probes_prefer_topology_refresh = (
    _semantic_refresh_policy._latest_feature_probes_prefer_topology_refresh
)
_latest_feature_probes_allow_topology_refresh_despite_general_geometry_gap = (
    _semantic_refresh_policy._latest_feature_probes_allow_topology_refresh_despite_general_geometry_gap
)
_latest_feature_probes_have_general_geometry_grounding_gap = (
    _semantic_refresh_policy._latest_feature_probes_have_general_geometry_grounding_gap
)
_feature_probe_payload_has_general_geometry_grounding_gap = (
    _semantic_refresh_policy._feature_probe_payload_has_general_geometry_grounding_gap
)
_feature_probe_payload_allows_hybrid_topology_refresh = (
    _semantic_refresh_policy._feature_probe_payload_allows_hybrid_topology_refresh
)
_feature_probe_payload_has_topology_refresh_signal = (
    _semantic_refresh_policy._feature_probe_payload_has_topology_refresh_signal
)
_feature_probe_payload_has_non_general_topology_refresh_signal = (
    _semantic_refresh_policy._feature_probe_payload_has_non_general_topology_refresh_signal
)
_latest_write_geometry_is_close_enough_for_topology_refresh = (
    _semantic_refresh_policy._latest_write_geometry_is_close_enough_for_topology_refresh
)
_latest_feature_probe_preferred_tools_for_turn = (
    _semantic_refresh_policy._latest_feature_probe_preferred_tools_for_turn
)
_post_semantic_refresh_followup_tools_for_turn = (
    _semantic_refresh_policy._post_semantic_refresh_followup_tools_for_turn
)
_preferred_validation_assessment_tools_for_turn = (
    _semantic_refresh_policy._preferred_validation_assessment_tools_for_turn
)
_latest_validation_has_budget_skipped_hint = (
    _semantic_refresh_policy._latest_validation_has_budget_skipped_hint
)
_semantic_refresh_followup_should_preempt_closure_validation = (
    _semantic_refresh_policy._semantic_refresh_followup_should_preempt_closure_validation
)
_validation_has_evidence_gap = _validation_policy._validation_has_evidence_gap
_validation_requests_localized_evidence_refresh = (
    _validation_policy._validation_requests_localized_evidence_refresh
)
_is_successful_validation = _validation_policy._is_successful_validation
_pick_step_file = _validation_policy._pick_step_file
_pick_render_file = _validation_policy._pick_render_file
_should_auto_validate_after_non_progress = (
    _validation_policy._should_auto_validate_after_non_progress
)
_should_auto_validate_after_post_write = (
    _validation_policy._should_auto_validate_after_post_write
)
_result_has_positive_session_backed_solid = (
    _validation_policy._result_has_positive_session_backed_solid
)
_payload_has_positive_session_backed_solid = (
    _validation_policy._payload_has_positive_session_backed_solid
)
_latest_validation_prefers_topology_refresh = (
    _validation_policy._latest_validation_prefers_topology_refresh
)
_latest_validation_is_fresh_for_write = (
    _validation_policy._latest_validation_is_fresh_for_write
)
_latest_validation_round = _validation_policy._latest_validation_round
_blockers_are_local_structured_tail = _validation_policy._blockers_are_local_structured_tail
_latest_validation_prefers_semantic_refresh = (
    _validation_policy._latest_validation_prefers_semantic_refresh
)
_latest_validation_has_actionable_geometry_contradiction = (
    _validation_policy._latest_validation_has_actionable_geometry_contradiction
)
_latest_validation_has_actionable_single_blocker = (
    _validation_policy._latest_validation_has_actionable_single_blocker
)
_has_repeated_validation_blockers_without_semantic_refresh = (
    _validation_policy._has_repeated_validation_blockers_without_semantic_refresh
)
_has_repeated_validation_without_new_evidence_after_write = (
    _validation_policy._has_repeated_validation_without_new_evidence_after_write
)
_turn_has_successful_validation_completion = (
    _validation_policy._turn_has_successful_validation_completion
)
