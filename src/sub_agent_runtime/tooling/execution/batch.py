from __future__ import annotations

import asyncio
import ast
from dataclasses import asdict, dataclass, field
from functools import lru_cache
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from llm.interface import LLMToolCall, LLMToolDefinition
from sandbox.mcp_runner import McpSandboxRunner
from sandbox_mcp_server.contracts import (
    CADActionInput,
    ExecuteBuild123dInput,
    ExecuteBuild123dProbeInput,
    GetHistoryInput,
    QueryFeatureProbesInput,
    QueryGeometryInput,
    QuerySketchInput,
    QuerySnapshotInput,
    QueryTopologyInput,
    RenderViewInput,
    ValidateRequirementInput,
)
from sub_agent_runtime.semantic_kernel.models import (
    PatchFeatureGraphInput,
    QueryGraphStateInput,
)
from sub_agent_runtime.tooling.cad_actions import (
    normalize_model_facing_apply_cad_action_arguments as _normalize_apply_cad_action_arguments,
    preflight_gate_apply_cad_action as _preflight_gate_apply_cad_action_impl,
)
from sub_agent_runtime.hooks import RuntimeHookManager, ToolHookTrace
from sub_agent_runtime.tooling.adapters import (
    KernelStateToolAdapter,
    compile_runtime_repair_packet_execution,
)
from sub_agent_runtime.tooling.execution.cancellation import (
    _clear_current_task_cancellation_state as _clear_current_task_cancellation_state_impl,
)
from sub_agent_runtime.tooling.execution.dispatch import (
    _gather_results as _gather_results_impl,
)
from sub_agent_runtime.tooling.execution.writes import (
    _normalize_multi_write_batch as _normalize_multi_write_batch_impl,
    _strip_runtime_managed_fields as _strip_runtime_managed_fields_impl,
)
from sub_agent_runtime.tooling.lint import (
    _preflight_lint_execute_build123d,
)
from sub_agent_runtime.tooling.lint.ast_utils import (
    _call_materializes_additive_host,
    _call_subtractive_without_host_operation_name,
    _call_uses_mode_private,
    _call_uses_mode_subtract,
    _collect_module_plane_binding_names,
    _looks_like_plane_expr,
    _with_context_builder_name,
    _with_context_is_locations,
)
from sub_agent_runtime.turn_state import (
    RunState,
    ToolCallRecord,
    ToolCategory,
    ToolExecutionEvent,
    ToolResultRecord,
)


from sub_agent_runtime.tooling.catalog import (
    ExecuteRepairPacketInput,
    FinishRunInput,
    ToolSpec,
    build_default_tool_specs,
)
from sub_agent_runtime.tooling.results import (
    ToolBatchResult,
    record_from_result as _record_from_result,
    summarize_result_payload as _summarize_result_payload,
    trace_to_dict as _trace_to_dict,
)


@dataclass(slots=True)
class _NormalizedWriteBatch:
    tool_calls: list[ToolCallRecord]
    execution_events: list[ToolExecutionEvent] = field(default_factory=list)


class ToolRuntime:
    """Typed orchestration layer for V2 tool execution."""

    def __init__(
        self,
        sandbox: McpSandboxRunner,
        hook_manager: RuntimeHookManager | None = None,
    ) -> None:
        self._sandbox = sandbox
        self._hook_manager = hook_manager
        self._specs = build_default_tool_specs()
        self._kernel_state_adapter = KernelStateToolAdapter()

    def list_tool_names(self) -> list[str]:
        return list(self._specs.keys())

    def build_llm_tools(
        self,
        *,
        allowed_tool_names: set[str] | None = None,
    ) -> list[LLMToolDefinition]:
        return [
            LLMToolDefinition(
                name=spec.name,
                description=_join_description(
                    spec.description,
                    spec.follow_up_recommendation,
                ),
                input_schema=_strip_runtime_managed_fields_impl(
                    spec.input_model.model_json_schema(),
                    spec.runtime_managed_fields or set(),
                ),
            )
            for spec in self._specs.values()
            if allowed_tool_names is None or spec.name in allowed_tool_names
        ]

    def build_tool_partitions(
        self,
        *,
        allowed_tool_names: set[str] | None = None,
    ) -> dict[str, Any]:
        partitions: dict[str, list[dict[str, Any]]] = {
            "read_tools": [],
            "write_tools": [],
            "judge_tools": [],
            "virtual_tools": [],
        }
        category_key_map = {
            ToolCategory.READ: "read_tools",
            ToolCategory.WRITE: "write_tools",
            ToolCategory.JUDGE: "judge_tools",
            ToolCategory.VIRTUAL: "virtual_tools",
        }
        for spec in self._specs.values():
            if allowed_tool_names is not None and spec.name not in allowed_tool_names:
                continue
            entry: dict[str, Any] = {
                "name": spec.name,
                "what_it_reads_or_writes": spec.category.value,
                "is_concurrency_safe": bool(spec.concurrency_safe),
            }
            if spec.follow_up_recommendation:
                entry["when_to_use"] = spec.follow_up_recommendation
                entry["common_follow_up"] = spec.follow_up_recommendation
            if spec.compatibility_alias_of:
                entry["compatibility_alias_of"] = spec.compatibility_alias_of
            if spec.category == ToolCategory.READ:
                entry["parallel_safe"] = bool(spec.concurrency_safe)
            partitions[category_key_map[spec.category]].append(entry)
        return {
            "parallel_read_tools_allowed": True,
            "max_write_tools_per_turn": 1,
            "partitions": partitions,
        }

    async def execute_tool_calls(
        self,
        *,
        tool_calls: list[LLMToolCall],
        session_id: str,
        requirements: dict[str, Any],
        requirement_text: str,
        sandbox_timeout: int,
        round_no: int,
        run_state: RunState | None = None,
        allowed_tool_names: set[str] | None = None,
    ) -> ToolBatchResult:
        normalized_calls: list[ToolCallRecord] = []
        for tool_call in tool_calls:
            if allowed_tool_names is not None and tool_call.name not in allowed_tool_names:
                return ToolBatchResult(
                    tool_calls=[],
                    tool_results=[],
                    execution_events=[],
                    error=f"tool_not_exposed_this_turn:{tool_call.name}",
                )
            spec = self._specs.get(tool_call.name)
            if spec is None:
                return ToolBatchResult(
                    tool_calls=[],
                    tool_results=[],
                    execution_events=[],
                    error=f"unknown_tool:{tool_call.name}",
                )
            try:
                raw_arguments = self._inject_runtime_managed_fields(
                    tool_name=tool_call.name,
                    arguments=tool_call.arguments,
                    session_id=session_id,
                    requirements=requirements,
                    requirement_text=requirement_text,
                    sandbox_timeout=sandbox_timeout,
                    run_state=run_state,
                )
                arguments = spec.input_model.model_validate(
                    raw_arguments
                ).model_dump(mode="json", exclude_none=True)
            except Exception as exc:  # noqa: BLE001
                return ToolBatchResult(
                    tool_calls=[],
                    tool_results=[],
                    execution_events=[],
                    error=f"invalid_tool_arguments:{tool_call.name}:{exc}",
                )
            normalized_calls.append(
                ToolCallRecord(
                    name=tool_call.name,
                    category=spec.category,
                    arguments=arguments,
                    call_id=tool_call.id,
                )
            )

        if not normalized_calls:
            return ToolBatchResult(
                tool_calls=[],
                tool_results=[],
                execution_events=[],
                error="no_tool_calls",
            )

        execution_events = [
            ToolExecutionEvent(
                round_no=round_no,
                tool_name=tool_call.name,
                phase="queued",
                category=tool_call.category,
                detail={
                    "arguments": tool_call.arguments,
                    "call_id": tool_call.call_id,
                },
            )
            for tool_call in normalized_calls
        ]

        write_calls = [
            tool_call
            for tool_call in normalized_calls
            if tool_call.category == ToolCategory.WRITE
        ]
        finish_calls = [
            tool_call
            for tool_call in normalized_calls
            if tool_call.name == "finish_run"
        ]

        if finish_calls and len(normalized_calls) > 1:
            return ToolBatchResult(
                tool_calls=normalized_calls,
                tool_results=[],
                execution_events=execution_events,
                error="finish_run_must_be_called_alone",
            )
        if len(write_calls) > 1:
            normalized_write_batch = _normalize_multi_write_batch_impl(
                normalized_calls=normalized_calls,
                write_calls=write_calls,
                round_no=round_no,
            )
            if normalized_write_batch is not None:
                normalized_calls = list(normalized_write_batch.tool_calls)
                execution_events.extend(normalized_write_batch.execution_events)
                write_calls = [
                    tool_call
                    for tool_call in normalized_calls
                    if tool_call.category == ToolCategory.WRITE
                ]
            else:
                return ToolBatchResult(
                    tool_calls=normalized_calls,
                    tool_results=[],
                    execution_events=execution_events,
                    error="at_most_one_write_tool_per_turn",
                )
        if write_calls and len(normalized_calls) > 1:
            return ToolBatchResult(
                tool_calls=normalized_calls,
                tool_results=[],
                execution_events=execution_events,
                error="do_not_mix_read_tools_with_write_tool_in_same_turn",
            )
        if finish_calls:
            reason = str(finish_calls[0].arguments.get("reason") or "").strip()
            if self._hook_manager is not None:
                self._hook_manager.emit_pre_finish(
                    reason=reason,
                    round_no=round_no,
                    session_id=session_id,
                )
            return ToolBatchResult(
                tool_calls=normalized_calls,
                tool_results=[],
                execution_events=execution_events
                + [
                    ToolExecutionEvent(
                        round_no=round_no,
                        tool_name="finish_run",
                        phase="requested_finish",
                        category=ToolCategory.VIRTUAL,
                        success=True,
                        detail={"reason": reason or "finish_requested"},
                    )
                ],
                requested_finish=True,
                finish_reason=reason or "finish_requested",
            )

        if write_calls:
            execution_events.append(
                ToolExecutionEvent(
                    round_no=round_no,
                    tool_name=write_calls[0].name,
                    phase="started",
                    category=write_calls[0].category,
                    detail={"arguments": write_calls[0].arguments},
                )
            )
            result = await self._execute_single(
                tool_call=write_calls[0],
                session_id=session_id,
                requirements=requirements,
                requirement_text=requirement_text,
                sandbox_timeout=sandbox_timeout,
                round_no=round_no,
                run_state=run_state,
            )
            return ToolBatchResult(
                tool_calls=normalized_calls,
                tool_results=[result],
                execution_events=execution_events
                + [
                    ToolExecutionEvent(
                        round_no=round_no,
                        tool_name=result.name,
                        phase="finished",
                        category=result.category,
                        success=result.success,
                        detail={
                            "error": result.error,
                            "artifact_files": result.artifact_files,
                        },
                    )
                ],
                error=result.error,
            )

        if len(normalized_calls) == 1 and normalized_calls[0].name == "validate_requirement":
            judge_call = normalized_calls[0]
            execution_events.append(
                ToolExecutionEvent(
                    round_no=round_no,
                    tool_name=judge_call.name,
                    phase="started",
                    category=judge_call.category,
                    detail={"arguments": judge_call.arguments},
                )
            )
            try:
                result = await self._execute_single_guarded(
                    tool_call=judge_call,
                    session_id=session_id,
                    requirements=requirements,
                    requirement_text=requirement_text,
                    sandbox_timeout=sandbox_timeout,
                    round_no=round_no,
                    run_state=run_state,
                )
            except asyncio.CancelledError:
                _clear_current_task_cancellation_state_impl()
                result = ToolResultRecord(
                    name=judge_call.name,
                    category=judge_call.category,
                    success=False,
                    payload={},
                    error="CancelledError: tool execution cancelled",
                )
            return ToolBatchResult(
                tool_calls=normalized_calls,
                tool_results=[result],
                execution_events=execution_events
                + [
                    ToolExecutionEvent(
                        round_no=round_no,
                        tool_name=result.name,
                        phase="finished",
                        category=result.category,
                        success=result.success,
                        detail={
                            "error": result.error,
                            "artifact_files": result.artifact_files,
                        },
                    )
                ],
                error=result.error,
            )

        execution_events.extend(
            ToolExecutionEvent(
                round_no=round_no,
                tool_name=tool_call.name,
                phase="started",
                category=tool_call.category,
                detail={"arguments": tool_call.arguments},
            )
            for tool_call in normalized_calls
        )
        results = await _gather_results_impl(
            [
                self._execute_single_guarded(
                    tool_call=tool_call,
                    session_id=session_id,
                    requirements=requirements,
                    requirement_text=requirement_text,
                    sandbox_timeout=sandbox_timeout,
                    round_no=round_no,
                    run_state=run_state,
                )
                for tool_call in normalized_calls
            ],
            fallback_tool_calls=normalized_calls,
        )
        return ToolBatchResult(
            tool_calls=normalized_calls,
            tool_results=results,
            execution_events=execution_events
            + [
                ToolExecutionEvent(
                    round_no=round_no,
                    tool_name=result.name,
                    phase="finished",
                    category=result.category,
                    success=result.success,
                    detail={
                        "error": result.error,
                        "artifact_files": result.artifact_files,
                    },
                )
                for result in results
            ],
            error=next((result.error for result in results if result.error), None),
        )

    async def _execute_single_guarded(
        self,
        *,
        tool_call: ToolCallRecord,
        session_id: str,
        requirements: dict[str, Any],
        requirement_text: str,
        sandbox_timeout: int,
        round_no: int,
        run_state: RunState | None = None,
    ) -> ToolResultRecord:
        try:
            return await self._execute_single(
                tool_call=tool_call,
                session_id=session_id,
                requirements=requirements,
                requirement_text=requirement_text,
                sandbox_timeout=sandbox_timeout,
                round_no=round_no,
                run_state=run_state,
            )
        except asyncio.CancelledError:
            return ToolResultRecord(
                name=tool_call.name,
                category=tool_call.category,
                success=False,
                payload={},
                error="CancelledError: tool execution cancelled",
            )
        except BaseException as exc:  # noqa: BLE001
            return ToolResultRecord(
                name=tool_call.name,
                category=tool_call.category,
                success=False,
                payload={},
                error=f"{exc.__class__.__name__}: {exc}",
            )

    async def _execute_single(
        self,
        *,
        tool_call: ToolCallRecord,
        session_id: str,
        requirements: dict[str, Any],
        requirement_text: str,
        sandbox_timeout: int,
        round_no: int,
        run_state: RunState | None = None,
    ) -> ToolResultRecord:
        hook_trace = ToolHookTrace()
        if self._hook_manager is not None:
            hook_trace.pre = self._hook_manager.emit_pre_tool(
                tool_name=tool_call.name,
                arguments=tool_call.arguments,
                round_no=round_no,
                session_id=session_id,
            )

        try:
            result = await self._dispatch_tool(
                tool_call=tool_call,
                session_id=session_id,
                requirements=requirements,
                requirement_text=requirement_text,
                sandbox_timeout=sandbox_timeout,
                run_state=run_state,
            )
            summary = _summarize_result_payload(result)
            if self._hook_manager is not None:
                hook_trace.post_success = self._hook_manager.emit_post_tool_success(
                    tool_name=tool_call.name,
                    arguments=tool_call.arguments,
                    result_summary=summary,
                    round_no=round_no,
                    session_id=session_id,
                )
            result.payload["hook_trace"] = _trace_to_dict(hook_trace)
            return result
        except asyncio.CancelledError:
            error_message = "CancelledError: tool execution cancelled"
            if self._hook_manager is not None:
                hook_trace.post_failure = self._hook_manager.emit_post_tool_failure(
                    tool_name=tool_call.name,
                    arguments=tool_call.arguments,
                    error=error_message,
                    round_no=round_no,
                    session_id=session_id,
                    recommend_execute_build123d=(tool_call.name == "apply_cad_action"),
                )
            return ToolResultRecord(
                name=tool_call.name,
                category=tool_call.category,
                success=False,
                payload={"hook_trace": _trace_to_dict(hook_trace)},
                error=error_message,
            )
        except Exception as exc:  # noqa: BLE001
            error_message = f"{exc.__class__.__name__}: {exc}"
            if self._hook_manager is not None:
                hook_trace.post_failure = self._hook_manager.emit_post_tool_failure(
                    tool_name=tool_call.name,
                    arguments=tool_call.arguments,
                    error=error_message,
                    round_no=round_no,
                    session_id=session_id,
                    recommend_execute_build123d=(tool_call.name == "apply_cad_action"),
                )
            return ToolResultRecord(
                name=tool_call.name,
                category=tool_call.category,
                success=False,
                payload={"hook_trace": _trace_to_dict(hook_trace)},
                error=error_message,
            )

    async def _dispatch_tool(
        self,
        *,
        tool_call: ToolCallRecord,
        session_id: str,
        requirements: dict[str, Any],
        requirement_text: str,
        sandbox_timeout: int,
        run_state: RunState | None = None,
    ) -> ToolResultRecord:
        args = dict(tool_call.arguments)
        name = tool_call.name
        if self._kernel_state_adapter.handles(name):
            return await self._kernel_state_adapter.dispatch(
                tool_call=tool_call,
                run_state=run_state,
            )
        if name == "query_snapshot":
            payload = await self._sandbox.query_snapshot(
                session_id=args.get("session_id", session_id),
                step=args.get("step"),
                include_history=bool(args.get("include_history", False)),
                timeout=min(int(args.get("timeout_seconds", 30) or 30), sandbox_timeout),
            )
            return _record_from_result(
                name=name,
                category=tool_call.category,
                result=payload,
            )
        if name == "query_sketch":
            payload = await self._sandbox.query_sketch(
                session_id=args.get("session_id", session_id),
                step=args.get("step"),
                timeout=min(int(args.get("timeout_seconds", 30) or 30), sandbox_timeout),
            )
            return _record_from_result(
                name=name,
                category=tool_call.category,
                result=payload,
            )
        if name == "query_geometry":
            payload = await self._sandbox.query_geometry(
                session_id=args.get("session_id", session_id),
                step=args.get("step"),
                include_solids=bool(args.get("include_solids", True)),
                include_faces=bool(args.get("include_faces", False)),
                include_edges=bool(args.get("include_edges", False)),
                max_items_per_type=int(args.get("max_items_per_type", 25) or 25),
                entity_ids=args.get("entity_ids") or [],
                solid_offset=int(args.get("solid_offset", 0) or 0),
                face_offset=int(args.get("face_offset", 0) or 0),
                edge_offset=int(args.get("edge_offset", 0) or 0),
                timeout=min(int(args.get("timeout_seconds", 30) or 30), sandbox_timeout),
            )
            return _record_from_result(name=name, category=tool_call.category, result=payload)
        if name == "query_topology":
            payload = await self._sandbox.query_topology(
                session_id=args.get("session_id", session_id),
                step=args.get("step"),
                include_faces=bool(args.get("include_faces", True)),
                include_edges=bool(args.get("include_edges", True)),
                max_items_per_type=int(args.get("max_items_per_type", 20) or 20),
                entity_ids=args.get("entity_ids") or [],
                ref_ids=args.get("ref_ids") or [],
                selection_hints=args.get("selection_hints") or [],
                family_ids=args.get("family_ids") or [],
                requirement_text=args.get("requirement_text") or requirement_text,
                face_offset=int(args.get("face_offset", 0) or 0),
                edge_offset=int(args.get("edge_offset", 0) or 0),
                timeout=min(int(args.get("timeout_seconds", 30) or 30), sandbox_timeout),
            )
            return _record_from_result(name=name, category=tool_call.category, result=payload)
        if name == "query_feature_probes":
            payload = await self._sandbox.query_feature_probes(
                session_id=args.get("session_id", session_id),
                requirements=args.get("requirements") or requirements,
                requirement_text=args.get("requirement_text") or requirement_text,
                step=args.get("step"),
                families=args.get("families") or [],
                timeout=min(int(args.get("timeout_seconds", 30) or 30), sandbox_timeout),
            )
            return _record_from_result(name=name, category=tool_call.category, result=payload)
        if name == "render_view":
            payload = await self._sandbox.render_view(
                session_id=args.get("session_id", session_id),
                step=args.get("step"),
                azimuth_deg=float(args.get("azimuth_deg", 35.0) or 35.0),
                elevation_deg=float(args.get("elevation_deg", 25.0) or 25.0),
                zoom=float(args.get("zoom", 1.0) or 1.0),
                width_px=int(args.get("width_px", 960) or 960),
                height_px=int(args.get("height_px", 720) or 720),
                style=str(args.get("style", "shaded") or "shaded"),
                target_entity_ids=args.get("target_entity_ids") or [],
                focus_center=args.get("focus_center"),
                focus_span=args.get("focus_span"),
                focus_padding_ratio=float(args.get("focus_padding_ratio", 0.15) or 0.15),
                include_artifact_content=bool(args.get("include_artifact_content", True)),
                timeout=min(int(args.get("timeout_seconds", 90) or 90), sandbox_timeout),
            )
            return _record_from_result(name=name, category=tool_call.category, result=payload)
        if name == "validate_requirement":
            payload = await self._sandbox.validate_requirement(
                session_id=args.get("session_id", session_id),
                requirements=args.get("requirements") or requirements,
                requirement_text=args.get("requirement_text") or requirement_text,
                step=args.get("step"),
                timeout=min(int(args.get("timeout_seconds", 30) or 30), sandbox_timeout),
            )
            return _record_from_result(name=name, category=tool_call.category, result=payload)
        if name == "get_history":
            payload = await self._sandbox.get_history(
                session_id=args.get("session_id", session_id),
                include_history=bool(args.get("include_history", True)),
                timeout=min(int(args.get("timeout_seconds", 30) or 30), sandbox_timeout),
            )
            return _record_from_result(name=name, category=tool_call.category, result=payload)
        if name == "apply_cad_action":
            action_type = str(args.get("action_type") or "").strip()
            preflight_payload = _preflight_gate_apply_cad_action_impl(
                action_type=action_type,
                action_params=args.get("action_params") or {},
                run_state=run_state,
            )
            if preflight_payload is not None:
                error_message = str(preflight_payload.get("error_message") or "apply_cad_action preflight failed")
                return ToolResultRecord(
                    name=name,
                    category=tool_call.category,
                    success=False,
                    payload=preflight_payload,
                    error=error_message,
                )
            payload = await self._sandbox.apply_cad_action(
                action_type=action_type,
                action_params=args.get("action_params") or {},
                session_id=args.get("session_id", session_id),
                timeout=min(int(args.get("timeout_seconds", 120) or 120), sandbox_timeout),
                include_artifact_content=bool(args.get("include_artifact_content", True)),
                clear_session=bool(args.get("clear_session", False)),
            )
            return _record_from_result(name=name, category=tool_call.category, result=payload)
        if name == "execute_build123d":
            code = str(args.get("code") or "")
            lint_payload = _preflight_lint_execute_build123d(
                code=code,
                session_id=args.get("session_id", session_id),
                requirement_text=args.get("requirement_text") or requirement_text,
                run_state=run_state,
            )
            if lint_payload is not None:
                lint_hits = lint_payload.get("lint_hits") or []
                first_message = ""
                if isinstance(lint_hits, list) and lint_hits:
                    first_hit = lint_hits[0]
                    if isinstance(first_hit, dict):
                        first_message = str(first_hit.get("repair_hint") or first_hit.get("message") or "").strip()
                error_message = "execute_build123d preflight lint failed"
                if first_message:
                    error_message = f"{error_message} | {first_message}"
                return ToolResultRecord(
                    name=name,
                    category=tool_call.category,
                    success=False,
                    payload=lint_payload,
                    error=error_message,
                )
            payload = await self._sandbox.execute(
                code=code,
                timeout=min(int(args.get("timeout_seconds", 120) or 120), sandbox_timeout),
                requirement_text=args.get("requirement_text") or requirement_text,
                session_id=args.get("session_id", session_id),
            )
            return _record_from_result(name=name, category=tool_call.category, result=payload)
        if name == "execute_repair_packet":
            compiled = compile_runtime_repair_packet_execution(
                run_state=run_state,
                packet_id=str(args.get("packet_id") or "").strip() or None,
                requirement_text=args.get("requirement_text") or requirement_text,
            )
            if not bool(compiled.get("ok")):
                error_message = str(compiled.get("error") or "repair_packet_compile_failed")
                compiled["repair_packet_compile_success"] = False
                compiled["repair_packet_compile_failure_reason"] = error_message
                return ToolResultRecord(
                    name=name,
                    category=tool_call.category,
                    success=False,
                    payload=compiled,
                    error=error_message,
                )
            payload = await self._sandbox.execute(
                code=str(compiled.get("code") or ""),
                timeout=min(int(args.get("timeout_seconds", 120) or 120), sandbox_timeout),
                requirement_text=args.get("requirement_text") or requirement_text,
                session_id=args.get("session_id", session_id),
            )
            result = _record_from_result(name=name, category=tool_call.category, result=payload)
            result.payload.update(
                {
                    "compiled_from_repair_packet": True,
                    "repair_packet_compile_success": True,
                    "repair_packet_compile_failure_reason": None,
                    "repair_packet": compiled.get("packet"),
                    "recipe_id": compiled.get("recipe_id"),
                    "repair_mode": compiled.get("repair_mode"),
                    "family_id": compiled.get("family_id"),
                    "compiler_summary": compiled.get("compiler_summary"),
                    "compiled_parameters": compiled.get("compiled_parameters"),
                    "generated_code": compiled.get("code"),
                }
            )
            return result
        if name == "execute_build123d_probe":
            payload = await self._sandbox.execute_build123d_probe(
                code=str(args.get("code") or ""),
                session_id=args.get("session_id", session_id),
                requirement_text=args.get("requirement_text") or requirement_text,
                timeout=min(int(args.get("timeout_seconds", 120) or 120), sandbox_timeout),
                include_artifact_content=bool(args.get("include_artifact_content", True)),
            )
            return _record_from_result(name=name, category=tool_call.category, result=payload)
        raise ValueError(f"unsupported_tool:{name}")

    def _inject_runtime_managed_fields(
        self,
        *,
        tool_name: str,
        arguments: dict[str, Any],
        session_id: str,
        requirements: dict[str, Any],
        requirement_text: str,
        sandbox_timeout: int,
        run_state: RunState | None = None,
    ) -> dict[str, Any]:
        merged = dict(arguments)

        preferred_probe_families: list[str] = []
        if run_state is not None and run_state.turn_tool_policies:
            latest_policy = run_state.turn_tool_policies[-1]
            preferred_probe_families = [
                str(family_id).strip()
                for family_id in latest_policy.preferred_probe_families
                if str(family_id).strip()
            ]

        def _merge_family_ids(existing: Any, injected: list[str]) -> list[str]:
            normalized_families: list[str] = []
            seen_families: set[str] = set()
            for family_id in existing or []:
                normalized = str(family_id).strip()
                if not normalized or normalized in seen_families:
                    continue
                seen_families.add(normalized)
                normalized_families.append(normalized)
            for family_id in injected:
                normalized = str(family_id).strip()
                if not normalized or normalized in seen_families:
                    continue
                seen_families.add(normalized)
                normalized_families.append(normalized)
            return normalized_families

        if tool_name in {
            "query_snapshot",
            "query_sketch",
            "query_geometry",
            "query_topology",
            "query_feature_probes",
            "render_view",
            "validate_requirement",
            "get_history",
            "apply_cad_action",
            "execute_build123d_probe",
        }:
            merged.setdefault("session_id", session_id)
        if tool_name == "validate_requirement":
            merged.setdefault("requirements", requirements)
            merged.setdefault("requirement_text", requirement_text)
        if tool_name == "query_feature_probes":
            merged.setdefault("requirements", requirements)
            merged.setdefault("requirement_text", requirement_text)
            merged.setdefault("families", [])
            merged.setdefault("timeout_seconds", min(30, sandbox_timeout))
            if preferred_probe_families:
                merged["families"] = _merge_family_ids(
                    merged.get("families"),
                    preferred_probe_families,
                )
        if tool_name == "query_topology":
            merged.setdefault("requirement_text", requirement_text)
            merged.setdefault("family_ids", [])
            if preferred_probe_families:
                merged["family_ids"] = _merge_family_ids(
                    merged.get("family_ids"),
                    preferred_probe_families,
                )
            normalized_selection_hints = {
                str(item).strip().lower().replace("-", "_").replace(" ", "_")
                for item in (merged.get("selection_hints") or [])
                if str(item).strip()
            }
            topology_edge_families = {
                "named_face_local_edit",
                "slots",
                "nested_hollow_section",
            }
            if (
                not bool(merged.get("include_edges", True))
                and (
                    any(hint.endswith("_edges") for hint in normalized_selection_hints)
                    or any(
                        str(family_id).strip() in topology_edge_families
                        for family_id in (merged.get("family_ids") or [])
                    )
                )
            ):
                merged["include_edges"] = True
        if tool_name == "execute_build123d":
            merged.setdefault("session_id", session_id)
            merged.setdefault("requirement_text", requirement_text)
            merged.setdefault("include_artifact_content", True)
            merged.setdefault("timeout_seconds", min(120, sandbox_timeout))
        if tool_name == "execute_repair_packet":
            merged.setdefault("session_id", session_id)
            merged.setdefault("requirement_text", requirement_text)
            merged.setdefault("timeout_seconds", min(120, sandbox_timeout))
        if tool_name == "execute_build123d_probe":
            merged.setdefault("session_id", session_id)
            merged.setdefault("requirement_text", requirement_text)
            merged.setdefault("include_artifact_content", True)
            merged.setdefault("timeout_seconds", min(120, sandbox_timeout))
        if tool_name == "apply_cad_action":
            merged = _normalize_apply_cad_action_arguments(merged)
            merged.setdefault("include_artifact_content", True)
            merged.setdefault("timeout_seconds", min(120, sandbox_timeout))
            merged.setdefault("clear_session", False)
        if tool_name == "render_view":
            merged.setdefault("include_artifact_content", True)
        return merged


async def _gather_results(
    tasks: list[Any],
    *,
    fallback_tool_calls: list[ToolCallRecord],
) -> list[ToolResultRecord]:
    raw_results = await __import__("asyncio").gather(*tasks, return_exceptions=True)
    results: list[ToolResultRecord] = []
    saw_cancellation = False
    for tool_call, raw_result in zip(fallback_tool_calls, raw_results):
        if isinstance(raw_result, ToolResultRecord):
            results.append(raw_result)
            continue
        if isinstance(raw_result, asyncio.CancelledError):
            saw_cancellation = True
            results.append(
                ToolResultRecord(
                    name=tool_call.name,
                    category=tool_call.category,
                    success=False,
                    payload={},
                    error="CancelledError: tool batch cancelled before completion",
                )
            )
            continue
        if isinstance(raw_result, BaseException):
            results.append(
                ToolResultRecord(
                    name=tool_call.name,
                    category=tool_call.category,
                    success=False,
                    payload={},
                    error=f"{raw_result.__class__.__name__}: {raw_result}",
                )
            )
    if saw_cancellation:
        _clear_current_task_cancellation_state()
    return results


def _clear_current_task_cancellation_state() -> None:
    task = asyncio.current_task()
    if task is None:
        return
    uncancel = getattr(task, "uncancel", None)
    cancelling = getattr(task, "cancelling", None)
    if not callable(uncancel) or not callable(cancelling):
        return
    while cancelling():
        uncancel()

@lru_cache(maxsize=1)
def _build123d_exported_symbol_names() -> set[str]:
    try:
        import build123d  # type: ignore
    except Exception:
        return set()
    explicit_exports = getattr(build123d, "__all__", None)
    if isinstance(explicit_exports, (list, tuple, set)):
        return {
            str(item).strip()
            for item in explicit_exports
            if isinstance(item, str) and str(item).strip()
        }
    return {
        str(item).strip()
        for item in dir(build123d)
        if isinstance(item, str) and str(item).strip()
    }


def _module_imports_build123d_symbols(tree: ast.Module) -> bool:
    for statement in tree.body:
        if isinstance(statement, ast.Import):
            for alias in statement.names:
                if str(alias.name or "").strip() == "build123d":
                    return True
        if isinstance(statement, ast.ImportFrom):
            if str(statement.module or "").strip() == "build123d":
                return True
    return False


def _locations_context_suggests_local_feature_placement(node: ast.AST) -> bool:
    if not (isinstance(node, ast.Call) and _ast_name_matches(node.func, "Locations")):
        return False
    if not node.args and not node.keywords:
        return False

    location_exprs = list(node.args)
    for keyword in node.keywords:
        if str(getattr(keyword, "arg", "") or "").strip() in {"locs", "locations"}:
            location_exprs.append(keyword.value)

    return any(
        _location_expression_has_non_origin_anchor(expr) for expr in location_exprs
    )


def _location_expression_has_non_origin_anchor(expr: ast.AST) -> bool:
    if isinstance(expr, (ast.Tuple, ast.List)):
        elements = list(expr.elts)
        if not elements:
            return False
        return any(not _ast_expr_is_zero_like(item) for item in elements)
    return not _ast_expr_is_zero_like(expr)


def _ast_expr_is_zero_like(expr: ast.AST) -> bool:
    if isinstance(expr, ast.Constant):
        value = expr.value
        return isinstance(value, (int, float)) and float(value) == 0.0
    if isinstance(expr, ast.UnaryOp) and isinstance(expr.op, (ast.UAdd, ast.USub)):
        return _ast_expr_is_zero_like(expr.operand)
    return False


def _ast_expr_is_plane_like(expr: ast.AST, plane_names: set[str]) -> bool:
    if isinstance(expr, ast.Name):
        return str(expr.id or "").strip() in plane_names
    if isinstance(expr, ast.Attribute):
        return (
            isinstance(expr.value, ast.Name)
            and str(expr.value.id or "").strip() == "Plane"
            and str(expr.attr or "").strip() in {"XY", "XZ", "YZ"}
        )
    if isinstance(expr, ast.Call):
        func = expr.func
        if isinstance(func, ast.Name):
            return str(func.id or "").strip() == "Plane"
        if isinstance(func, ast.Attribute):
            method_name = str(func.attr or "").strip()
            if method_name in {"offset", "move", "shift_origin", "rotated"}:
                return _ast_expr_is_plane_like(func.value, plane_names)
    return False


def _ast_expr_is_face_plane_constructor(expr: ast.AST) -> bool:
    if not isinstance(expr, ast.Call):
        return False
    func = expr.func
    if not (isinstance(func, ast.Name) and str(func.id or "").strip() == "Plane"):
        return False
    if expr.keywords or len(expr.args) != 1:
        return False
    return isinstance(expr.args[0], (ast.Name, ast.Attribute, ast.Call, ast.Subscript))


def _build_parent_map(tree: ast.AST) -> dict[ast.AST, ast.AST]:
    parent_map: dict[ast.AST, ast.AST] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parent_map[child] = parent
    return parent_map


def _ast_is_mode_subtract(node: ast.AST) -> bool:
    return isinstance(node, ast.Attribute) and node.attr == "SUBTRACT" and _ast_name_matches(
        node.value, "Mode"
    )


def _call_is_subtractive_buildpart(node: ast.AST) -> bool:
    if not (isinstance(node, ast.Call) and _ast_name_matches(node.func, "BuildPart")):
        return False
    for keyword in node.keywords:
        if str(getattr(keyword, "arg", "") or "").strip() != "mode":
            continue
        if _ast_is_mode_subtract(keyword.value):
            return True
    return False


def _call_name(node: ast.Call) -> str | None:
    func = node.func
    if isinstance(func, ast.Name) and func.id:
        return str(func.id)
    if isinstance(func, ast.Attribute) and func.attr:
        return str(func.attr)
    return None


def _buildpart_with_alias(node: ast.AST) -> str | None:
    if not isinstance(node, (ast.With, ast.AsyncWith)):
        return None
    for item in node.items:
        context_expr = item.context_expr
        if isinstance(context_expr, ast.Call):
            context_expr = context_expr.func
        if _ast_name_matches(context_expr, "BuildPart") and isinstance(
            item.optional_vars, ast.Name
        ):
            return item.optional_vars.id
    return None


def _ast_name_matches(node: ast.AST, expected: str) -> bool:
    if isinstance(node, ast.Name):
        return node.id == expected
    if isinstance(node, ast.Attribute):
        return node.attr == expected
    return False


def _ast_expr_text(node: ast.AST) -> str:
    try:
        return ast.unparse(node).strip()
    except Exception:
        return ""


def _primitive_constructor_name(node: ast.AST) -> str | None:
    if (
        isinstance(node, ast.BinOp)
        and isinstance(node.op, ast.Mult)
        and _transform_like_binop_kinds(node)
    ):
        return _primitive_constructor_name(node.left) or _primitive_constructor_name(node.right)
    if not isinstance(node, ast.Call):
        return None
    if _call_uses_mode_private(node):
        return None
    for primitive_name in ("Box", "Cylinder", "Cone", "Sphere", "Torus"):
        if _ast_name_matches(node.func, primitive_name):
            return primitive_name
    return None


def _host_part_arithmetic_assignment(
    *,
    node: ast.AST,
    host_alias: str,
) -> tuple[ast.AST, int] | None:
    if isinstance(node, ast.AugAssign) and _is_named_part_attr(node.target, host_alias):
        if isinstance(node.op, (ast.Add, ast.Sub, ast.Mult, ast.Div)):
            return node.value, int(getattr(node, "lineno", 0) or 0)
        return None
    if isinstance(node, ast.Assign) and any(
        _is_named_part_attr(target, host_alias) for target in node.targets
    ):
        if isinstance(node.value, ast.BinOp) and isinstance(
            node.value.op, (ast.Add, ast.Sub, ast.Mult, ast.Div)
        ):
            return node.value, int(getattr(node, "lineno", 0) or 0)
    return None


def _is_named_part_attr(node: ast.AST, alias: str) -> bool:
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "part"
        and isinstance(node.value, ast.Name)
        and node.value.id == alias
    )


def _expression_references_part_attr(expr: ast.AST, alias: str) -> bool:
    return any(_is_named_part_attr(node, alias) for node in ast.walk(expr))


def _call_targets_named_part_transform_method(call: ast.Call, alias: str) -> str | None:
    if not isinstance(call.func, ast.Attribute):
        return None
    method_name = str(call.func.attr or "").strip()
    if method_name not in {
        "move",
        "moved",
        "translate",
        "translated",
        "rotate",
        "rotated",
        "locate",
        "located",
    }:
        return None
    owner = call.func.value
    if not _is_named_part_attr(owner, alias):
        return None
    return method_name


def _expression_references_name(expr: ast.AST, name: str) -> bool:
    return any(
        isinstance(node, ast.Name) and node.id == name
        for node in ast.walk(expr)
    )


def _temporary_primitive_arithmetic_expr(node: ast.AST) -> tuple[ast.AST | None, int]:
    arithmetic_ops = (ast.Add, ast.Sub, ast.BitAnd, ast.BitOr)
    if isinstance(node, ast.Assign) and isinstance(node.value, ast.BinOp) and isinstance(
        node.value.op, arithmetic_ops
    ):
        return node.value, int(getattr(node, "lineno", 0) or 0)
    if isinstance(node, ast.AugAssign) and isinstance(node.op, arithmetic_ops):
        return node.value, int(getattr(node, "lineno", 0) or 0)
    return None, 0


def _temporary_primitive_transform_expr(
    node: ast.AST,
) -> tuple[ast.AST | None, int, list[str]]:
    if not (
        isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
        and isinstance(node.value, ast.BinOp)
        and isinstance(node.value.op, ast.Mult)
    ):
        return None, 0, []
    transform_kinds = _transform_like_binop_kinds(node.value)
    if not transform_kinds:
        return None, 0, []
    return node.value, int(getattr(node, "lineno", 0) or 0), transform_kinds


def _transform_like_binop_kinds(node: ast.BinOp) -> list[str]:
    transform_kinds: list[str] = []
    for side in (node.left, node.right):
        kind = _transform_like_expr_kind(side)
        if kind and kind not in transform_kinds:
            transform_kinds.append(kind)
    return transform_kinds


def _transform_like_expr_kind(node: ast.AST) -> str | None:
    if isinstance(node, ast.Call):
        for name in ("Pos", "Rot", "Location", "Mirror", "Plane"):
            if _ast_name_matches(node.func, name):
                return name
        if isinstance(node.func, ast.Attribute) and node.func.attr in {"offset", "rotated"}:
            if _looks_like_plane_expr(node.func.value):
                return "Plane"
    if isinstance(node, ast.Attribute) and node.attr in {"location", "position"}:
        return node.attr
    return None


def _join_description(description: str, recommendation: str | None) -> str:
    if recommendation and recommendation.strip():
        return f"{description.strip()} Follow-up recommendation: {recommendation.strip()}"
    return description.strip()


def _strip_runtime_managed_fields(
    schema: dict[str, Any],
    managed_fields: set[str],
) -> dict[str, Any]:
    if not managed_fields:
        return schema
    normalized = dict(schema)
    properties = normalized.get("properties")
    if isinstance(properties, dict):
        normalized["properties"] = {
            key: value for key, value in properties.items() if key not in managed_fields
        }
    required = normalized.get("required")
    if isinstance(required, list):
        normalized["required"] = [item for item in required if item not in managed_fields]
    return normalized


def _normalize_multi_write_batch(
    *,
    normalized_calls: list[ToolCallRecord],
    write_calls: list[ToolCallRecord],
    round_no: int,
) -> _NormalizedWriteBatch | None:
    if len(normalized_calls) != len(write_calls):
        return None
    if not write_calls:
        return None
    if any(tool_call.name != "apply_cad_action" for tool_call in write_calls):
        return None

    kept_call = write_calls[0]
    dropped_calls = write_calls[1:]
    if not dropped_calls:
        return None

    return _NormalizedWriteBatch(
        tool_calls=[kept_call],
        execution_events=[
            ToolExecutionEvent(
                round_no=round_no,
                tool_name=kept_call.name,
                phase="normalized",
                category=ToolCategory.WRITE,
                detail={
                    "reason": "truncated_multi_apply_cad_action_batch",
                    "kept_call_id": kept_call.call_id,
                    "kept_action_type": kept_call.arguments.get("action_type"),
                    "dropped_call_ids": [
                        dropped_call.call_id
                        for dropped_call in dropped_calls
                        if dropped_call.call_id is not None
                    ],
                    "dropped_action_types": [
                        str(dropped_call.arguments.get("action_type") or "").strip()
                        for dropped_call in dropped_calls
                    ],
                    "original_write_count": len(write_calls),
                },
            )
        ],
    )


def _trace_to_dict(trace: ToolHookTrace) -> dict[str, Any]:
    return {
        "pre": trace.pre,
        "post_success": trace.post_success,
        "post_failure": trace.post_failure,
        "pre_finish": trace.pre_finish,
        "notes": list(trace.notes),
    }
