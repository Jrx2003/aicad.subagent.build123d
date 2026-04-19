from __future__ import annotations

import asyncio
import ast
from dataclasses import asdict, dataclass, field
from functools import lru_cache
import io
import re
import tokenize
from typing import Any, Sequence

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
from sandbox_mcp_server.registry import infer_requirement_probe_families
from sub_agent_runtime.feature_graph import (
    PatchFeatureGraphInput,
    QueryGraphStateInput,
)
from sub_agent_runtime.hooks import RuntimeHookManager, ToolHookTrace
from sub_agent_runtime.tool_adapters import (
    KernelStateToolAdapter,
    compile_runtime_repair_packet_execution,
)
from sub_agent_runtime.turn_state import (
    RunState,
    ToolCallRecord,
    ToolCategory,
    ToolExecutionEvent,
    ToolResultRecord,
)


class FinishRunInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(
        default="The current geometry appears requirement-complete.",
        description="Short finish reason.",
    )
    summary: str | None = Field(
        default=None,
        description="Optional concise final summary for the run artifacts.",
    )


class ExecuteRepairPacketInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    packet_id: str | None = Field(
        default=None,
        description="Optional FamilyRepairPacket id. Omit to use the latest active packet.",
    )
    session_id: str | None = Field(default=None, description="Runtime-managed session id.")
    requirement_text: str | None = Field(
        default=None,
        description="Runtime-managed requirement text for recipe compilation.",
    )
    timeout_seconds: int | None = Field(
        default=None,
        description="Runtime-managed execution timeout.",
    )


@dataclass(slots=True)
class ToolSpec:
    name: str
    category: ToolCategory
    description: str
    input_model: type[BaseModel]
    concurrency_safe: bool = False
    follow_up_recommendation: str | None = None
    compatibility_alias_of: str | None = None
    runtime_managed_fields: set[str] | None = None


@dataclass(slots=True)
class ToolBatchResult:
    tool_calls: list[ToolCallRecord]
    tool_results: list[ToolResultRecord]
    execution_events: list[ToolExecutionEvent] = field(default_factory=list)
    error: str | None = None
    requested_finish: bool = False
    finish_reason: str | None = None


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
                input_schema=_strip_runtime_managed_fields(
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
            normalized_write_batch = _normalize_multi_write_batch(
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
                _clear_current_task_cancellation_state()
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
        results = await _gather_results(
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
            preflight_payload = _preflight_gate_apply_cad_action(
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
            merged.setdefault("include_artifact_content", True)
            merged.setdefault("timeout_seconds", min(120, sandbox_timeout))
            merged.setdefault("clear_session", False)
        if tool_name == "render_view":
            merged.setdefault("include_artifact_content", True)
        return merged


def build_default_tool_specs() -> dict[str, ToolSpec]:
    specs = [
        ToolSpec(
            name="query_kernel_state",
            category=ToolCategory.READ,
            description="Inspect the canonical domain-kernel state view that tracks bodies, features, blockers, bindings, and revision progress.",
            input_model=QueryGraphStateInput,
            concurrency_safe=True,
            follow_up_recommendation="Preferred semantic readback tool. Use when you need a compact semantic view of what remains to be built or repaired without replaying long planner history.",
        ),
        ToolSpec(
            name="query_snapshot",
            category=ToolCategory.READ,
            description="Inspect the latest session snapshot and optional action history.",
            input_model=QuerySnapshotInput,
            concurrency_safe=True,
            follow_up_recommendation="Use before acting when the current session state is uncertain.",
            runtime_managed_fields={"session_id"},
        ),
        ToolSpec(
            name="query_sketch",
            category=ToolCategory.READ,
            description="Inspect current pre-solid sketch, path, and profile state.",
            input_model=QuerySketchInput,
            concurrency_safe=True,
            runtime_managed_fields={"session_id"},
        ),
        ToolSpec(
            name="query_geometry",
            category=ToolCategory.READ,
            description="Inspect structured geometry facts for solids, faces, and edges.",
            input_model=QueryGeometryInput,
            concurrency_safe=True,
            runtime_managed_fields={"session_id"},
        ),
        ToolSpec(
            name="query_topology",
            category=ToolCategory.READ,
            description="Inspect face/edge refs and candidate sets for topology-aware edits.",
            input_model=QueryTopologyInput,
            concurrency_safe=True,
            runtime_managed_fields={"session_id"},
        ),
        ToolSpec(
            name="query_feature_probes",
            category=ToolCategory.READ,
            description="Inspect family-specific geometric probes for hollow sections, grooves, holes, unions, and axisymmetric profiles.",
            input_model=QueryFeatureProbesInput,
            concurrency_safe=True,
            follow_up_recommendation="Prefer after a successful write when the remaining uncertainty is geometric-family interpretation rather than raw topology targeting.",
            runtime_managed_fields={"session_id", "requirements", "requirement_text", "timeout_seconds"},
        ),
        ToolSpec(
            name="render_view",
            category=ToolCategory.READ,
            description="Render a focused visual preview for local confirmation.",
            input_model=RenderViewInput,
            concurrency_safe=True,
            runtime_managed_fields={"session_id", "include_artifact_content"},
        ),
        ToolSpec(
            name="get_history",
            category=ToolCategory.READ,
            description="Retrieve the current session action history when needed.",
            input_model=GetHistoryInput,
            concurrency_safe=True,
            runtime_managed_fields={"session_id"},
        ),
        ToolSpec(
            name="validate_requirement",
            category=ToolCategory.JUDGE,
            description="Judge whether the current model satisfies the requirement.",
            input_model=ValidateRequirementInput,
            concurrency_safe=True,
            follow_up_recommendation="Use near completion or after repeated non-progress, not every turn.",
            runtime_managed_fields={"session_id", "requirements", "requirement_text"},
        ),
        ToolSpec(
            name="patch_domain_kernel",
            category=ToolCategory.WRITE,
            description="Update the runtime domain-kernel state without mutating geometry.",
            input_model=PatchFeatureGraphInput,
            follow_up_recommendation="Preferred semantic patch tool. Use only to refine semantic decomposition, active nodes, blocked nodes, or completion tracking. Geometry still changes only through apply_cad_action or execute_build123d.",
        ),
        ToolSpec(
            name="execute_repair_packet",
            category=ToolCategory.WRITE,
            description=(
                "Execute the latest supported FamilyRepairPacket as a deterministic runtime-owned repair write."
            ),
            input_model=ExecuteRepairPacketInput,
            follow_up_recommendation=(
                "Prefer when domain_kernel_digest already exposes a latest_repair_packet_* surface for a supported family "
                "and you want a narrower repair lane than free-form execute_build123d."
            ),
            runtime_managed_fields={
                "session_id",
                "timeout_seconds",
                "requirement_text",
            },
        ),
        ToolSpec(
            name="execute_build123d",
            category=ToolCategory.WRITE,
            description=(
                "Execute a Build123d program for the default initial write in V2 and for later whole-part rebuilds or materially simpler code-driven modeling steps. "
                "A successful result is persisted back into the current session for later queries and follow-on local finishing edits."
            ),
            input_model=ExecuteBuild123dInput,
            follow_up_recommendation=(
                "Default first-write path for the initial write. "
                "Only deviate on the initial write when the user explicitly requested a local edit and a stable topology anchor already exists. "
                "Prefer a builder-first Build123d structure: BuildPart for host solids, BuildSketch for section profiles, and BuildLine for rails. "
                "Use Plane, Axis, Pos, Rot, and Locations to encode placement instead of Workplane-chain intuition or implicit origin guesses. "
                "For explicit cutter booleans, build the cutter as a literal solid, orient it with Rot/Pos, and subtract it with an explicit solid boolean instead of guessed top-level helpers or unsupported primitive keywords. "
                "Assign the final geometry explicitly with result = part.part or result = final_solid before the script ends. "
                "If the result has solids but zero volume, repair the code before more read-only inspection. "
                "Treat execute_build123d as a rebuild-oriented tool, not the default way to patch an existing session model edge-by-edge. "
                "Only after a successful code-first host build, use direct apply_cad_action for narrow final local finishing edits such as fillets or chamfers when selector arguments are already obvious; prefer query_topology first only when those selectors still need disambiguation. "
                "After a successful session-backed code write, keep local finishing bounded and do not reopen a new structured bootstrap chain. "
                "For axisymmetric stepped parts defined by radii over axial segments, prefer coaxial primitives and explicit unions when repeated revolve attempts stay flat or zero-volume. "
                "For cylindrical annular grooves, prefer subtracting an explicit annular band through the requested axial window over a raw sketch-plane revolve unless axis/workplane semantics are already explicit."
            ),
            runtime_managed_fields={
                "session_id",
                "timeout_seconds",
                "include_artifact_content",
                "requirement_text",
            },
        ),
        ToolSpec(
            name="apply_cad_action",
            category=ToolCategory.WRITE,
            description=(
                "Apply one structured CAD action inside the current session. "
                "Use this for local, inspectable edits after a code-backed model already exists; additive extrude does not support hollow or subtractive overload modes."
            ),
            input_model=CADActionInput,
            follow_up_recommendation=(
                "Not the default first-write path in V2. "
                "Prefer only for local, structured, inspectable edits once a stable host solid or topology anchor already exists. "
                "Use additive extrude only for additive growth; switch to cut_extrude or execute_build123d for hollow/subtractive section intent."
            ),
            runtime_managed_fields={
                "session_id",
                "timeout_seconds",
                "include_artifact_content",
                "clear_session",
            },
        ),
        ToolSpec(
            name="execute_build123d_probe",
            category=ToolCategory.READ,
            description="Run diagnostics-only Build123d/OCP probe code without mutating the authoritative session.",
            input_model=ExecuteBuild123dProbeInput,
            concurrency_safe=False,
            follow_up_recommendation="Use when you need a one-off geometric probe or custom Build123d/OCP measurement and the standard read tools are not enough.",
            runtime_managed_fields={
                "session_id",
                "timeout_seconds",
                "include_artifact_content",
                "requirement_text",
            },
        ),
        ToolSpec(
            name="finish_run",
            category=ToolCategory.VIRTUAL,
            description="Declare that the run should stop and request one final completion judgment.",
            input_model=FinishRunInput,
        ),
    ]
    return {spec.name: spec for spec in specs}


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


def _strip_python_comments_and_strings(code: str) -> str:
    output: list[str] = []
    last_line = 1
    last_col = 0
    try:
        tokens = tokenize.generate_tokens(io.StringIO(code).readline)
        for token in tokens:
            token_type = token.type
            token_text = token.string
            start_line, start_col = token.start
            end_line, end_col = token.end
            while last_line < start_line:
                output.append("\n")
                last_line += 1
                last_col = 0
            if start_col > last_col:
                output.append(" " * (start_col - last_col))
            if token_type in {tokenize.COMMENT, tokenize.STRING}:
                output.append(" " * len(token_text))
            else:
                output.append(token_text)
            last_line = end_line
            last_col = end_col
    except Exception:
        return code
    return "".join(output)


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
        for plane_located_hit in _find_plane_located_call_hits(parsed_tree):
            line_no = int(plane_located_hit.get("line_no") or 0)
            hits.append(
                {
                    "rule_id": "invalid_build123d_api.plane_located_shape_method_guess",
                    "message": (
                        "Build123d Plane objects do not support a shape-style `.located(...)` "
                        "method for repositioning workplanes."
                    ),
                    "repair_hint": (
                        "Translate or re-anchor the workplane with `Plane.offset(...)`, "
                        "`Plane.move(Location(...))`, or `Plane.shift_origin(...)`; only call "
                        "`.located(...)` on actual shapes when you need to reposition geometry. "
                        + (
                            f"Repair the Plane placement call at line {line_no}."
                            if line_no > 0
                            else "Repair the Plane placement call."
                        )
                    ),
                }
            )
        for plane_moved_hit in _find_plane_moved_call_hits(parsed_tree):
            line_no = int(plane_moved_hit.get("line_no") or 0)
            hits.append(
                {
                    "rule_id": "invalid_build123d_api.plane_moved_shape_method_guess",
                    "message": (
                        "Build123d Plane objects do not support a shape-style `.moved(...)` "
                        "method for repositioning workplanes."
                    ),
                    "repair_hint": (
                        "Translate or re-anchor the workplane with `Plane.offset(...)`, "
                        "`Plane.move(Location(...))`, or `Plane.shift_origin(...)`; only call "
                        "`.moved(...)` on actual shapes when you need to reposition geometry. "
                        + (
                            f"Repair the Plane placement call at line {line_no}."
                            if line_no > 0
                            else "Repair the Plane placement call."
                        )
                    ),
                }
            )
        for plane_rotate_hit in _find_plane_rotate_method_hits(parsed_tree):
            line_no = int(plane_rotate_hit.get("line_no") or 0)
            hits.append(
                {
                    "rule_id": "invalid_build123d_api.plane_rotate_shape_method_guess",
                    "message": (
                        "Build123d Plane objects do not support a shape-style `.rotate(...)` "
                        "method for orienting workplanes."
                    ),
                    "repair_hint": (
                        "Orient the workplane with `Plane.rotated((rx, ry, rz), ordering=...)` "
                        "or keep the named plane when it already matches the target normal; "
                        "use `Plane.offset(...)` only for plane-normal translation. "
                        + (
                            f"Repair the Plane rotation call at line {line_no}."
                            if line_no > 0
                            else "Repair the Plane rotation call."
                        )
                    ),
                }
            )
        for shift_origin_hit in _find_face_plane_shift_origin_global_coordinate_hits(
            parsed_tree
        ):
            line_no = int(shift_origin_hit.get("line_no") or 0)
            hits.append(
                {
                    "rule_id": "invalid_build123d_contract.face_plane_shift_origin_global_coordinate_guess",
                    "message": (
                        "When a workplane is derived from `Plane(face_like_expr)`, passing a "
                        "raw global `(x, y, z)` tuple into `.shift_origin(...)` often chooses "
                        "a point that is not on the host face plane and will fail before any "
                        "geometry is created."
                    ),
                    "repair_hint": (
                        "Keep the face-derived workplane on the host plane, then place the "
                        "sketch/profile with local 2D coordinates inside `BuildSketch(...)`; "
                        "if you truly need a re-anchored face plane, rebuild it from the host "
                        "face origin/normal instead of guessing a world-space XYZ tuple. "
                        + (
                            f"Repair the face-plane shift_origin call at line {line_no}."
                            if line_no > 0
                            else "Repair the face-plane shift_origin call."
                        )
                    ),
                }
            )
        for nested_hit in _find_nested_buildpart_part_arithmetic_hits(parsed_tree):
            line_no = int(nested_hit.get("line_no") or 0)
            hits.append(
                {
                    "rule_id": "invalid_build123d_api.nested_buildpart_cutter_part_arithmetic",
                    "message": (
                        "Do not open a nested BuildPart cutter inside an active BuildPart "
                        "and then mutate the host with `part.part -= cutter.part`; that "
                        "pattern does not reliably preserve the active placement context "
                        "for repeated subtractive features."
                    ),
                    "repair_hint": (
                        "Keep repeated cutters in the same active `BuildPart` with "
                        "`mode=Mode.SUBTRACT`, or close the host builder before doing an "
                        "explicit `result = host.part - cutter` boolean. "
                        + (
                            f"Repair the nested cutter arithmetic at line {line_no}."
                            if line_no > 0
                            else "Repair the nested cutter arithmetic."
                        )
                    ),
                }
            )
        for transform_hit in _find_nested_buildpart_part_transform_hits(parsed_tree):
            line_no = int(transform_hit.get("line_no") or 0)
            nested_alias = str(transform_hit.get("nested_alias") or "cutter").strip() or "cutter"
            method_name = (
                str(transform_hit.get("method_name") or "move").strip() or "move"
            )
            hits.append(
                {
                    "rule_id": "invalid_build123d_api.nested_buildpart_part_transform",
                    "message": (
                        "Do not treat a nested BuildPart alias like `"
                        f"{nested_alias}.part.{method_name}(...)` as a detached solid transform "
                        "surface while the outer host BuildPart is still active."
                    ),
                    "repair_hint": (
                        "If the nested builder is only a cutter or secondary local feature, keep "
                        "that geometry builder-native inside the active host with explicit "
                        "`Locations(...)` / `mode=Mode.SUBTRACT`. Otherwise close the host builder "
                        "first, then transform the detached solid outside it before one explicit "
                        "boolean. "
                        + (
                            f"Repair the nested BuildPart part transform at line {line_no}."
                            if line_no > 0
                            else "Repair the nested BuildPart part transform."
                        )
                    ),
                }
            )
        for display_hit in _find_display_only_helper_hits(parsed_tree):
            line_no = int(display_hit.get("line_no") or 0)
            helper_label = str(display_hit.get("helper_label") or "display helper").strip()
            hits.append(
                {
                    "rule_id": "invalid_build123d_runtime.display_only_helper_import",
                    "message": (
                        "Sandbox execution should return geometry through `result` only; "
                        "display/debug helpers such as ocp_vscode imports or `show(...)` "
                        "calls are not available inside the runtime container."
                    ),
                    "repair_hint": (
                        f"Remove the display/debug helper usage ({helper_label})"
                        + (f" at line {line_no}" if line_no > 0 else "")
                        + ", keep the Build123d geometry construction only, and expose the "
                        "final part/compound via `result = ...`."
                    ),
                }
            )
        for identifier_hit in _find_case_drift_local_symbol_hits(parsed_tree):
            line_no = int(identifier_hit.get("line_no") or 0)
            undefined_name = str(
                identifier_hit.get("undefined_name") or "local_symbol"
            ).strip()
            suggested_name = str(
                identifier_hit.get("suggested_name") or "local_symbol"
            ).strip()
            hits.append(
                {
                    "rule_id": "invalid_build123d_identifier.case_drift_local_symbol",
                    "message": (
                        f"`{undefined_name}` is not defined in this execute_build123d snippet, "
                        f"but `{suggested_name}` is already bound with different casing."
                    ),
                    "repair_hint": (
                        f"Rename `{undefined_name}` to the existing local symbol "
                        f"`{suggested_name}`, or define `{undefined_name}` explicitly before "
                        "it is used."
                        + (
                            f" Repair the local identifier casing at line {line_no}."
                            if line_no > 0
                            else ""
                        )
                    ),
                }
            )
        for topology_hit in _find_buildpart_topology_access_inside_buildsketch_hits(
            parsed_tree
        ):
            line_no = int(topology_hit.get("line_no") or 0)
            builder_alias = str(topology_hit.get("builder_alias") or "part").strip()
            accessor = str(topology_hit.get("accessor") or "topology accessor").strip()
            hits.append(
                {
                    "rule_id": "invalid_build123d_context.buildpart_topology_access_inside_buildsketch",
                    "message": (
                        "Do not query the enclosing `BuildPart` topology from inside "
                        "`BuildSketch`; the host solid may not exist yet, and sketch-time "
                        "shape edits should stay on sketch geometry instead of "
                        f"`{builder_alias}.{accessor}()`."
                    ),
                    "repair_hint": (
                        "Keep the `BuildSketch` self-contained: use sketch-native profile "
                        "construction for rounded corners, or extrude/revolve first and then "
                        "apply solid-edge edits on the finished part. "
                        + (
                            f"Repair the `{builder_alias}.{accessor}()` access at line {line_no}."
                            if line_no > 0
                            else f"Repair the `{builder_alias}.{accessor}()` access."
                        )
                    ),
                }
            )
        for nested_subtractive_hit in _find_nested_subtractive_buildpart_hits(parsed_tree):
            line_no = int(nested_subtractive_hit.get("line_no") or 0)
            inside_locations = bool(nested_subtractive_hit.get("inside_locations"))
            hits.append(
                {
                    "rule_id": "invalid_build123d_context.nested_subtractive_buildpart_inside_active_builder",
                    "message": (
                        "Do not open a nested `BuildPart(mode=Mode.SUBTRACT)` inside an "
                        "active `BuildPart`; that pattern does not reliably preserve the "
                        "host placement/workplane context for repeated local subtractive features."
                        + (
                            " This is especially brittle when the nested subtractive builder sits "
                            "inside an outer `Locations(...)` placement."
                            if inside_locations
                            else ""
                        )
                    ),
                    "repair_hint": (
                        "Keep repeated subtractive features in the same active `BuildPart` with "
                        "direct builder-native subtractive calls, or close the host builder before "
                        "doing an explicit `result = host.part - cutter` boolean. "
                        + (
                            f"Repair the nested subtractive BuildPart at line {line_no}."
                            if line_no > 0
                            else "Repair the nested subtractive BuildPart."
                        )
                    ),
                }
            )
        for detached_subtractive_hit in _find_detached_subtractive_builder_without_host_hits(
            parsed_tree
        ):
            line_no = int(detached_subtractive_hit.get("line_no") or 0)
            builder_alias = (
                str(detached_subtractive_hit.get("builder_alias") or "part").strip()
                or "part"
            )
            operation_name = (
                str(detached_subtractive_hit.get("operation_name") or "subtractive operation")
                .strip()
                or "subtractive operation"
            )
            hits.append(
                {
                    "rule_id": "invalid_build123d_contract.detached_subtractive_builder_without_host",
                    "message": (
                        "A detached `BuildPart` cannot start by removing material before any "
                        "additive host exists; the first materializing operation in that builder "
                        f"is subtractive (`{operation_name}`), so Build123d has nothing to subtract from."
                    ),
                    "repair_hint": (
                        "Do not open a standalone builder whose first real operation is "
                        "subtractive. If the cut belongs to an existing host, keep it inside the "
                        "authoritative host builder with explicit placement and "
                        "`mode=Mode.SUBTRACT`. If a detached cutter is required, build it as a "
                        "positive or private solid first and subtract it only after the host "
                        "builder closes. "
                        + (
                            f"Repair the subtract-without-host builder `{builder_alias}` at line {line_no}."
                            if line_no > 0
                            else f"Repair the subtract-without-host builder `{builder_alias}`."
                        )
                    ),
                }
            )
        if (
            ("pin hinge" in requirement_lower or "mechanical hinge" in requirement_lower)
            and (
                "clamshell" in requirement_lower
                or ("lid" in requirement_lower and "base" in requirement_lower)
                or "top lid" in requirement_lower
                or "bottom base" in requirement_lower
            )
        ):
            for hinge_axis_hit in _find_clamshell_unrotated_default_hinge_cylinder_hits(
                parsed_tree,
                code_for_lint=code,
            ):
                line_no = int(hinge_axis_hit.get("line_no") or 0)
                hits.append(
                    {
                        "rule_id": "invalid_build123d_contract.clamshell_hinge_unrotated_default_cylinder",
                        "message": (
                            "For a clamshell back-edge pin/mechanical hinge, dropping an "
                            "unrotated default `Cylinder(...)` onto the hinge seam leaves the "
                            "cylinder axis on Z instead of the requested hinge axis, usually X/width."
                        ),
                        "repair_hint": (
                            "Keep the seam Y coordinate literal, but choose a supported "
                            "orientation lane for the hinge cylinder. If the hinge geometry is "
                            "host-owned, build it with an axis-correct host-native pattern. If "
                            "detached hinge hardware is truly required, build the cylinder "
                            "positively first, close that builder, then orient the closed solid "
                            "with `Rot(...)` before assembly. "
                            + (
                                f"Repair the unrotated hinge cylinder at line {line_no}."
                                if line_no > 0
                                else "Repair the unrotated hinge cylinder."
                            )
                        ),
                    }
                )
        for host_part_hit in _find_active_buildpart_host_part_mutation_hits(parsed_tree):
            line_no = int(host_part_hit.get("line_no") or 0)
            host_alias = str(host_part_hit.get("host_alias") or "part").strip() or "part"
            primitive_names = ", ".join(
                str(item) for item in (host_part_hit.get("primitive_vars") or [])
            )
            hits.append(
                {
                    "rule_id": "invalid_build123d_contract.active_builder_part_mutation",
                    "message": (
                        "Do not reassign or mutate `"
                        f"{host_alias}.part` while its `BuildPart` context is still active; "
                        "the builder output is not a detached staging solid until the host "
                        "builder closes."
                    ),
                    "repair_hint": (
                        "Keep additive/subtractive edits builder-native inside the active "
                        "`BuildPart` with `mode=Mode.ADD` / `mode=Mode.SUBTRACT` and explicit "
                        "`Locations(...)` placement. If detached boolean arithmetic is truly "
                        "required, close the host builder first and only then compute "
                        f"`result = {host_alias}.part +/- cutter`. "
                        + (
                            f"Repair the active builder part mutation at line {line_no}"
                            f" for {primitive_names}."
                            if line_no > 0 and primitive_names
                            else (
                                f"Repair the active builder part mutation at line {line_no}."
                                if line_no > 0
                                else "Repair the active builder part mutation."
                            )
                        )
                    ),
                }
            )
        for temporary_hit in _find_active_buildpart_temporary_primitive_arithmetic_hits(
            parsed_tree
        ):
            line_no = int(temporary_hit.get("line_no") or 0)
            primitive_names = ", ".join(
                str(item) for item in (temporary_hit.get("primitive_vars") or [])
            )
            hits.append(
                {
                    "rule_id": "invalid_build123d_contract.active_builder_temporary_primitive_arithmetic",
                    "message": (
                        "Primitive constructors inside an active `BuildPart` mutate the "
                        "host immediately, so reusing those temporary solids in later "
                        "boolean/intersection arithmetic does not preserve an isolated "
                        "intermediate-solid contract."
                    ),
                    "repair_hint": (
                        "Keep the active builder authoritative: encode the shape with one "
                        "builder-native sketch/profile recipe, or close the host builder "
                        "before doing explicit solid arithmetic with temporary solids. "
                        + (
                            f"Repair the temporary solid arithmetic at line {line_no}"
                            f" for {primitive_names}."
                            if line_no > 0 and primitive_names
                            else (
                                f"Repair the temporary solid arithmetic at line {line_no}."
                                if line_no > 0
                                else "Repair the temporary solid arithmetic."
                            )
                        )
                    ),
                }
            )
        for transform_hit in _find_active_buildpart_temporary_primitive_transform_hits(
            parsed_tree
        ):
            line_no = int(transform_hit.get("line_no") or 0)
            primitive_names = ", ".join(
                str(item) for item in (transform_hit.get("primitive_vars") or [])
            )
            transform_kinds = ", ".join(
                str(item) for item in (transform_hit.get("transform_kinds") or [])
            )
            hits.append(
                {
                    "rule_id": "invalid_build123d_contract.active_builder_temporary_primitive_transform_rebind",
                    "message": (
                        "A primitive created inside an active `BuildPart` is already added to "
                        "that host immediately, so rebinding it with `Pos(...) * solid`, "
                        "`Rot(...) * solid`, or similar transform multiplication does not move "
                        "the already-added host geometry."
                    ),
                    "repair_hint": (
                        "Inside the active builder, place translated features with "
                        "`Locations(...)` / explicit local frames, or close the host builder "
                        "before transforming a detached solid. Do not expect "
                        "`solid = Pos(...) * solid` or `solid = Rot(...) * solid` to relocate "
                        "geometry that was already added to the active host. "
                        + (
                            f"Repair the temporary primitive transform at line {line_no}"
                            f" for {primitive_names} via {transform_kinds}."
                            if line_no > 0 and primitive_names and transform_kinds
                            else (
                                f"Repair the temporary primitive transform at line {line_no}."
                                if line_no > 0
                                else "Repair the temporary primitive transform."
                            )
                        )
                    ),
                }
            )
        for compound_hit in _find_compound_positional_children_contract_hits(parsed_tree):
            line_no = int(compound_hit.get("line_no") or 0)
            arg_count = int(compound_hit.get("arg_count") or 0)
            hits.append(
                {
                    "rule_id": "invalid_build123d_contract.compound_positional_children_contract",
                    "message": (
                        "Do not pass multiple detached parts/shapes to `Compound(...)` as "
                        "separate positional arguments; after the first positional `obj`, later "
                        "positional slots bind to `label`, `color`, or other metadata instead of "
                        "additional child shapes."
                    ),
                    "repair_hint": (
                        "Wrap detached solids in one iterable such as "
                        "`Compound([base_solid, lid_solid, hinge_solid])`, or use an explicit "
                        "`children=[...]` keyword payload. Do not write "
                        "`Compound(base_solid, lid_solid, hinge_solid)` expecting a variadic "
                        "assembly constructor. "
                        + (
                            f"Repair the Compound positional-child contract at line {line_no} "
                            f"with {arg_count} positional arguments."
                            if line_no > 0 and arg_count > 0
                            else (
                                f"Repair the Compound positional-child contract at line {line_no}."
                                if line_no > 0
                                else "Repair the Compound positional-child contract."
                            )
                        )
                    ),
                }
            )
        if "explicit_anchor_hole" in candidate_family_ids:
            for cutter_hit in _find_explicit_anchor_manual_cutter_missing_subtract_hits(
                parsed_tree
            ):
                line_no = int(cutter_hit.get("line_no") or 0)
                primitive_name = str(cutter_hit.get("primitive_name") or "primitive").strip()
                hits.append(
                    {
                        "rule_id": "invalid_build123d_contract.explicit_anchor_manual_cutter_requires_subtract_mode",
                        "message": (
                            "Manual countersink / through-hole cutters inside an active "
                            "`BuildPart` placement must use `mode=Mode.SUBTRACT` (or stay "
                            "`mode=Mode.PRIVATE` for a later boolean), otherwise they add "
                            "material instead of cutting it."
                        ),
                        "repair_hint": (
                            "When realizing explicit hole arrays with manual "
                            f"`{primitive_name}(...)` cutters inside `Locations(...)`, add "
                            "`mode=Mode.SUBTRACT` on the cutter itself, or build the cutter "
                            "privately and subtract it after the host builder closes. "
                            + (
                                f"Repair the non-subtractive manual cutter at line {line_no}."
                                if line_no > 0
                                else "Repair the non-subtractive manual cutter."
                            )
                        ),
                    }
                )
        for rotated_hit in _find_plane_rotated_origin_guess_hits(parsed_tree):
            line_no = int(rotated_hit.get("line_no") or 0)
            hits.append(
                {
                    "rule_id": "invalid_build123d_api.plane_rotated_origin_guess",
                    "message": (
                        "`Plane.rotated(rotation, ordering=...)` only changes plane "
                        "orientation; the second positional argument is ordering, not "
                        "an origin tuple."
                    ),
                    "repair_hint": (
                        "Keep the named workplane unrotated when it already matches the "
                        "required normal, or move it with `offset(...)` / feature placement "
                        "instead of guessing an origin argument to `Plane.rotated(...)`. "
                        + (
                            f"Repair the plane rotation call at line {line_no}."
                            if line_no > 0
                            else "Repair the plane rotation call."
                        )
                    ),
                }
            )
        for plane_offset_hit in _find_directional_drill_plane_offset_coordinate_hits(
            parsed_tree,
            requirement_lower=requirement_lower,
        ):
            line_no = int(plane_offset_hit.get("line_no") or 0)
            hits.append(
                {
                    "rule_id": "invalid_build123d_contract.directional_drill_plane_offset_coordinate_mixup",
                    "message": (
                        "For directional drilling on the XZ/YZ workplane, `Plane.offset(...)` "
                        "moves along the drill axis normal, not along the in-plane anchor "
                        "coordinate that the requirement usually gives."
                    ),
                    "repair_hint": (
                        "Keep the directional-drill workplane at the correct normal-axis "
                        "datum and place the named local coordinates inside that workplane "
                        "instead of encoding them with `Plane.offset(...)`. "
                        + (
                            f"Repair the workplane offset at line {line_no}."
                            if line_no > 0
                            else "Repair the workplane offset."
                        )
                    ),
                }
            )
        for plane_family_hit in _find_named_face_plane_family_mismatch_hits(
            parsed_tree,
            requirement_lower=requirement_lower,
        ):
            line_no = int(plane_family_hit.get("line_no") or 0)
            plane_name = str(plane_family_hit.get("plane_name") or "").strip() or "unknown"
            expected_planes = ", ".join(
                str(item).strip() for item in plane_family_hit.get("expected_planes", []) if str(item).strip()
            ) or "XY/XZ/YZ"
            hits.append(
                {
                    "rule_id": "invalid_build123d_contract.named_face_plane_family_mismatch",
                    "message": (
                        "Named-face local edits must use the plane family whose normal matches the "
                        f"requested host face. `Plane.{plane_name}` does not match the named-face "
                        f"orientation implied here; expected plane family/families: {expected_planes}."
                    ),
                    "repair_hint": (
                        "Map named host faces to plane families by normal axis before sketching or "
                        "placing cutters: `top/bottom -> Plane.XY`, `front/back -> Plane.XZ`, "
                        "`left/right -> Plane.YZ`. If the host face has already been selected from "
                        "topology, prefer `Plane(face)` or an explicit `Plane(origin=..., z_dir=...)` "
                        "built from that face instead of guessing a mismatched named plane. "
                        + (
                            f"Repair the named-face workplane at line {line_no}."
                            if line_no > 0
                            else "Repair the named-face workplane."
                        )
                    ),
                }
            )
        for plane_offset_hit in _find_centered_box_face_plane_offset_span_mismatch_hits(
            parsed_tree,
            requirement_lower=requirement_lower,
        ):
            line_no = int(plane_offset_hit.get("line_no") or 0)
            plane_name = str(plane_offset_hit.get("plane_name") or "").strip() or "XY"
            span_expr = str(plane_offset_hit.get("span_expr") or "").strip() or "dimension"
            hits.append(
                {
                    "rule_id": "invalid_build123d_contract.centered_box_face_plane_full_span_offset",
                    "message": (
                        "A default centered `Box(...)` spans equally about the origin, so "
                        f"`Plane.{plane_name}.offset({span_expr})` overshoots the actual host "
                        "face by using the full span instead of the half-span datum."
                    ),
                    "repair_hint": (
                        "When a feature belongs on a named face of a centered host, place the "
                        "workplane on the real face datum: use the half-span (`height/2`, "
                        "`width/2`, `length/2`) or bind to an actual face reference/topology "
                        "query instead of offsetting by the full box dimension. "
                        + (
                            f"Repair the centered-host face offset at line {line_no}."
                            if line_no > 0
                            else "Repair the centered-host face offset."
                        )
                    ),
                }
            )
        for context_hit in _find_buildsketch_countersink_context_hits(parsed_tree):
            line_no = int(context_hit.get("line_no") or 0)
            hits.append(
                {
                    "rule_id": "invalid_build123d_context.countersinkhole_requires_buildpart",
                    "message": (
                        "`CounterSinkHole(...)` is a BuildPart operation, not a "
                        "BuildSketch entity. Calling it inside `BuildSketch` will fail "
                        "before any geometry is created."
                    ),
                    "repair_hint": (
                        "Move `CounterSinkHole(...)` back into the active `BuildPart`, "
                        "and place it on the target host-face plane with an explicit "
                        "face-local placement such as `Locations((x, y, top_z))`. "
                        + (
                            f"Repair the BuildSketch countersink misuse at line {line_no}."
                            if line_no > 0
                            else "Repair the BuildSketch countersink misuse."
                        )
                    ),
                }
            )
        for alias_hit in _find_countersink_keyword_alias_hits(parsed_tree):
            line_no = int(alias_hit.get("line_no") or 0)
            alias_name = str(alias_hit.get("alias_name") or "").strip()
            if alias_name == "countersink_radius":
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
                            + (
                                f" Repair the countersink radius keyword at line {line_no}."
                                if line_no > 0
                                else ""
                            )
                        ),
                    }
                )
            if alias_name in {
                "head_diameter",
                "head_radius",
                "countersink_diameter",
                "counter_sink_diameter",
                "countersink_radius",
                "counter_sink_head_radius",
                "head_dia",
                "countersink_dia",
                "counter_sink_dia",
            }:
                hits.append(
                    {
                        "rule_id": "invalid_build123d_keyword.countersink_head_diameter_alias",
                        "message": (
                            "`CounterSinkHole(...)` uses `counter_sink_radius=...`, not "
                            f"`{alias_name}=`."
                        ),
                        "repair_hint": (
                            "Convert the requested countersink head diameter to a radius and pass "
                            "it as `counter_sink_radius=` when calling `CounterSinkHole(...)`."
                            + (
                                f" Repair the countersink head-diameter keyword at line {line_no}."
                                if line_no > 0
                                else ""
                            )
                        ),
                    }
                )
            if alias_name in {
                "thru_diameter",
                "through_diameter",
                "through_hole_diameter",
                "hole_diameter",
                "diameter",
                "thru_dia",
                "through_dia",
                "hole_dia",
            }:
                hits.append(
                    {
                        "rule_id": "invalid_build123d_keyword.countersink_through_diameter_alias",
                        "message": (
                            "`CounterSinkHole(...)` uses `radius=...` for the through-hole size, "
                            f"not `{alias_name}=`."
                        ),
                        "repair_hint": (
                            "Convert the requested through-hole diameter to a radius and pass it "
                            "as `radius=` when calling `CounterSinkHole(...)`."
                            + (
                                f" Repair the countersink through-hole keyword at line {line_no}."
                                if line_no > 0
                                else ""
                            )
                        ),
                    }
                )
            if alias_name in {"countersink_angle", "angle", "cone_angle"}:
                wrong_keyword = (
                    "`countersink_angle=`"
                    if alias_name == "countersink_angle"
                    else ("`cone_angle=`" if alias_name == "cone_angle" else "`angle=`")
                )
                hits.append(
                    {
                        "rule_id": "invalid_build123d_keyword.countersink_angle_alias",
                        "message": (
                            "`CounterSinkHole(...)` uses `counter_sink_angle=...`, not "
                            f"{wrong_keyword}."
                        ),
                        "repair_hint": (
                            "Rename the keyword to `counter_sink_angle=` when calling "
                            "`CounterSinkHole(...)`."
                            + (
                                f" Repair the countersink angle keyword at line {line_no}."
                                if line_no > 0
                                else ""
                            )
                        ),
                    }
                )
            if alias_name in {"countersink_depth", "counter_sink_depth"}:
                hits.append(
                    {
                        "rule_id": "invalid_build123d_keyword.countersink_depth_alias",
                        "message": (
                            "`CounterSinkHole(...)` does not accept "
                            f"`{alias_name}=`. Keep `depth=` for the through-hole depth and "
                            "describe the countersink with `counter_sink_radius=` plus "
                            "`counter_sink_angle=`."
                        ),
                        "repair_hint": (
                            "Remove the guessed countersink-depth keyword. Keep `depth=` for the "
                            "through-hole depth only, and express the countersink with "
                            "`counter_sink_radius=` plus `counter_sink_angle=` when calling "
                            "`CounterSinkHole(...)`."
                            + (
                                f" Repair the countersink depth keyword at line {line_no}."
                                if line_no > 0
                                else ""
                            )
                        ),
                    }
                )
        for alias_hit in _find_regular_polygon_keyword_alias_hits(parsed_tree):
            line_no = int(alias_hit.get("line_no") or 0)
            alias_name = str(alias_hit.get("alias_name") or "").strip()
            hits.append(
                {
                    "rule_id": "invalid_build123d_keyword.regular_polygon_sides_alias",
                    "message": (
                        "`RegularPolygon(...)` uses `side_count=...`, not "
                        f"`{alias_name}=`."
                    ),
                    "repair_hint": (
                        "Rename the keyword to `side_count=` when calling "
                        "`RegularPolygon(...)`."
                        + (
                            f" Repair the regular-polygon side-count keyword at line {line_no}."
                            if line_no > 0
                            else ""
                        )
                    ),
                }
            )
        for alias_hit in _find_plane_keyword_alias_hits(parsed_tree):
            line_no = int(alias_hit.get("line_no") or 0)
            alias_name = str(alias_hit.get("alias_name") or "").strip()
            hits.append(
                {
                    "rule_id": "invalid_build123d_keyword.plane_normal_alias",
                    "message": (
                        "`Plane(...)` uses `z_dir=...` for its normal direction, not "
                        f"`{alias_name}=`."
                    ),
                    "repair_hint": (
                        "Construct the plane with `Plane(origin=..., z_dir=...)`, and "
                        "only add `x_dir=` when you need to pin the in-plane rotation."
                        + (
                            f" Repair the plane normal keyword at line {line_no}."
                            if line_no > 0
                            else ""
                        )
                    ),
                }
            )
        for alias_hit in _find_filter_by_position_keyword_band_hits(parsed_tree):
            line_no = int(alias_hit.get("line_no") or 0)
            alias_name = str(alias_hit.get("alias_name") or "").strip()
            hits.append(
                {
                    "rule_id": "invalid_build123d_api.filter_by_position_keyword_band",
                    "message": (
                        "`ShapeList.filter_by_position(...)` uses positional `minimum, maximum` "
                        f"arguments (plus optional `inclusive=`), not axis-band alias keywords such as `{alias_name}=`."
                    ),
                    "repair_hint": (
                        "Keep the axis as the first argument and pass the numeric band as "
                        "positional `minimum, maximum`, for example "
                        "`edges.filter_by_position(Axis.Z, z_min, z_max)`."
                        + (
                            f" Repair the position-band keyword at line {line_no}."
                            if line_no > 0
                            else ""
                        )
                    ),
                }
            )
        for plane_hit in _find_filter_by_position_plane_axis_hits(parsed_tree):
            line_no = int(plane_hit.get("line_no") or 0)
            plane_name = str(plane_hit.get("plane_name") or "").strip()
            hits.append(
                {
                    "rule_id": "invalid_build123d_api.filter_by_position_plane_axis",
                    "message": (
                        "`ShapeList.filter_by_position(...)` expects an `Axis.*` selector as "
                        f"its first argument, not `Plane.{plane_name}`."
                    ),
                    "repair_hint": (
                        "Replace the first argument with the matching axis band such as "
                        "`Axis.Z`, and keep the numeric band as `minimum, maximum`, for "
                        "example `edges.filter_by_position(Axis.Z, z_min, z_max)`. When "
                        "the edit depends on a named host face or rim subset, prefer "
                        "`query_topology` over guessing a Plane-based band filter."
                        + (
                            f" Repair the Plane-based position filter at line {line_no}."
                            if line_no > 0
                            else ""
                        )
                    ),
                }
            )
        for alias_hit in _find_cone_keyword_alias_hits(parsed_tree):
            line_no = int(alias_hit.get("line_no") or 0)
            alias_name = str(alias_hit.get("alias_name") or "").strip()
            expected_keyword = "top_radius" if alias_name == "upper_radius" else "bottom_radius"
            hits.append(
                {
                    "rule_id": "invalid_build123d_keyword.cone_radius_alias",
                    "message": (
                        "`Cone(...)` uses `bottom_radius=` and `top_radius=...`, not "
                        f"`{alias_name}=`."
                    ),
                    "repair_hint": (
                        f"Rename `{alias_name}=` to `{expected_keyword}=` when calling `Cone(...)`."
                        + (
                            f" Repair the cone radius keyword at line {line_no}."
                            if line_no > 0
                            else ""
                        )
                    ),
                }
            )
        for alias_hit in _find_slot_center_point_radius_alias_hits(parsed_tree):
            line_no = int(alias_hit.get("line_no") or 0)
            hits.append(
                {
                    "rule_id": "invalid_build123d_keyword.slot_center_point_radius_alias",
                    "message": (
                        "SlotCenterPoint(...) does not accept `radius=...`; Build123d uses "
                        "`center=..., point=..., height=...` plus optional `rotation=`."
                    ),
                    "repair_hint": (
                        "Keep `SlotCenterPoint(center=..., point=..., height=...)` and express the "
                        "slot span with the center/point pair. If the slot orientation needs control, "
                        "use `rotation=` instead of inventing a rounded-slot `radius=` keyword."
                        + (
                            f" Repair the SlotCenterPoint keyword at line {line_no}."
                            if line_no > 0
                            else ""
                        )
                    ),
                }
            )
        for alias_hit in _find_slot_center_point_center_alias_hits(parsed_tree):
            line_no = int(alias_hit.get("line_no") or 0)
            hits.append(
                {
                    "rule_id": "invalid_build123d_keyword.slot_center_point_center_alias",
                    "message": (
                        "SlotCenterPoint(...) uses `center=...`, not `center_point=...`."
                    ),
                    "repair_hint": (
                        "Rename `center_point=` to `center=` when calling `SlotCenterPoint(...)`."
                        + (
                            f" Repair the SlotCenterPoint keyword at line {line_no}."
                            if line_no > 0
                            else ""
                        )
                    ),
                }
            )
        for alias_hit in _find_slot_center_to_center_keyword_alias_hits(parsed_tree):
            line_no = int(alias_hit.get("line_no") or 0)
            alias_name = str(alias_hit.get("alias_name") or "").strip()
            expected_keyword = (
                "center_separation" if alias_name == "center_to_center" else "height"
            )
            hits.append(
                {
                    "rule_id": "invalid_build123d_keyword.slot_center_to_center_alias",
                    "message": (
                        "SlotCenterToCenter(...) uses `center_separation=...` and "
                        f"`height=...`, not `{alias_name}=...`."
                    ),
                    "repair_hint": (
                        f"Rename `{alias_name}=` to `{expected_keyword}=` when calling "
                        "`SlotCenterToCenter(...)`."
                        + (
                            f" Repair the SlotCenterToCenter keyword at line {line_no}."
                            if line_no > 0
                            else ""
                        )
                    ),
                }
            )
        for alias_hit in _find_center_arc_keyword_alias_hits(parsed_tree):
            line_no = int(alias_hit.get("line_no") or 0)
            alias_name = str(alias_hit.get("alias_name") or "").strip()
            if alias_name == "end_angle":
                rule_id = "invalid_build123d_keyword.center_arc_end_angle_alias"
                message = (
                    "`CenterArc(...)` uses `arc_size=...` for the sweep span, not "
                    "`end_angle=...`."
                )
                repair_hint = (
                    "Keep `start_angle=...` for the start direction and replace "
                    "`end_angle=` with `arc_size=` when calling `CenterArc(...)`."
                )
            else:
                rule_id = "invalid_build123d_keyword.center_arc_arc_angle_alias"
                message = (
                    "`CenterArc(...)` uses `arc_size=...`, not `arc_angle=...`."
                )
                repair_hint = (
                    "Rename `arc_angle=` to `arc_size=` when calling `CenterArc(...)`."
                )
            hits.append(
                {
                    "rule_id": rule_id,
                    "message": message,
                    "repair_hint": repair_hint
                    + (
                        f" Repair the CenterArc keyword at line {line_no}."
                        if line_no > 0
                        else ""
                    ),
                }
            )
        for missing_hit in _find_center_arc_missing_start_angle_hits(parsed_tree):
            line_no = int(missing_hit.get("line_no") or 0)
            hits.append(
                {
                    "rule_id": "invalid_build123d_contract.center_arc_missing_start_angle",
                    "message": (
                        "`CenterArc(...)` requires an explicit `start_angle` before the arc "
                        "span. Omitting it leaves the arc under-specified."
                    ),
                    "repair_hint": (
                        "Provide `start_angle=...` (or the third positional argument) and keep "
                        "`arc_size=` for the sweep span when calling `CenterArc(...)`."
                        + (
                            f" Repair the CenterArc call at line {line_no}."
                            if line_no > 0
                            else ""
                        )
                    ),
                }
            )
        for symbolic_hit in _find_symbolic_degree_constant_hits(code_for_lint):
            line_no = int(symbolic_hit.get("line_no") or 0)
            symbol_name = str(symbolic_hit.get("symbol_name") or "DEGREES").strip()
            hits.append(
                {
                    "rule_id": "invalid_build123d_api.symbolic_degree_constant",
                    "message": (
                        "Build123d angle parameters already take plain degree-valued floats; "
                        f"`{symbol_name}` is not a supported symbolic angle constant."
                    ),
                    "repair_hint": (
                        "Pass literal degree numbers such as `start_angle=-90` and "
                        "`arc_size=90` directly instead of multiplying by "
                        f"`{symbol_name}`."
                        + (
                            f" Repair the symbolic angle constant at line {line_no}."
                            if line_no > 0
                            else ""
                        )
                    ),
                }
            )
        if _requirement_prefers_center_arc_for_explicit_radius_path(requirement_lower):
            for helper_hit in _find_explicit_radius_arc_helper_hits(parsed_tree):
                line_no = int(helper_hit.get("line_no") or 0)
                helper_name = str(helper_hit.get("helper_name") or "arc helper").strip()
                hits.append(
                    {
                        "rule_id": "invalid_build123d_contract.explicit_radius_arc_prefers_center_arc",
                        "message": (
                            "For a path-sweep rail with an explicit tangent-arc radius, "
                            f"`{helper_name}(...)` is a higher-risk construction lane than an "
                            "explicit `CenterArc(...)` definition and often fails after the "
                            "model guesses the elbow endpoint or tangent."
                        ),
                        "repair_hint": (
                            "Prefer `CenterArc(center=..., radius=..., start_angle=..., arc_size=...)` "
                            "for the explicit-radius elbow, and connect the downstream line from `arc @ 1`."
                            + (
                                f" Repair the arc helper at line {line_no}."
                                if line_no > 0
                                else ""
                            )
                        ),
                    }
                )
        for method_hit in _find_sweep_path_method_reference_hits(parsed_tree):
            line_no = int(method_hit.get("line_no") or 0)
            attribute_name = str(method_hit.get("attribute_name") or "").strip()
            if attribute_name == "line":
                hits.append(
                    {
                        "rule_id": "invalid_build123d_contract.sweep_path_line_alias",
                        "message": (
                            "`BuildLine.line` exposes only one curve member and can silently drop "
                            "the full multi-segment rail that a path sweep requires."
                        ),
                        "repair_hint": (
                            "Pass `path.wire()` or another real connected `Wire`/`Edge` rail into "
                            "`sweep(...)` instead of `path.line`."
                            + (
                                f" Repair the sweep path object at line {line_no}."
                                if line_no > 0
                                else ""
                            )
                        ),
                    }
                )
            else:
                hits.append(
                    {
                        "rule_id": "invalid_build123d_contract.sweep_path_wire_method_reference",
                        "message": (
                            "`BuildLine.wire` is a method. `sweep(..., path=path.wire)` passes "
                            "a bound method object instead of the path wire itself."
                        ),
                        "repair_hint": (
                            "Call `path.wire()` when passing the captured path into `sweep(...)`, "
                            "or pass another real `Wire`/`Edge` object as the path."
                            + (
                                f" Repair the sweep path object at line {line_no}."
                                if line_no > 0
                                else ""
                            )
                        ),
                    }
                )
        for alias_hit in _find_sweep_section_keyword_alias_hits(parsed_tree):
            line_no = int(alias_hit.get("line_no") or 0)
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
                        + (
                            f" Repair the sweep profile keyword at line {line_no}."
                            if line_no > 0
                            else ""
                        )
                    ),
                }
            )
        for keyword_hit in _find_solid_sweep_invalid_keyword_hits(parsed_tree):
            line_no = int(keyword_hit.get("line_no") or 0)
            alias_name = str(keyword_hit.get("alias_name") or "").strip()
            hits.append(
                {
                    "rule_id": "invalid_build123d_keyword.solid_sweep_unsupported_keyword",
                    "message": (
                        "`Solid.sweep(...)` only accepts the verified Build123d keywords "
                        "`section`, `path`, `inner_wires`, `make_solid`, `is_frenet`, "
                        "`mode`, and `transition`; "
                        f"`{alias_name}=` is not part of that contract."
                    ),
                    "repair_hint": (
                        "Repair the call to use the real `Solid.sweep(...)` signature, or "
                        "prefer `sweep(profile.sketch, path=path_wire)` when the section is "
                        "one annular sketch with inner wires."
                        + (
                            f" Repair the Solid.sweep keyword at line {line_no}."
                            if line_no > 0
                            else ""
                        )
                    ),
                }
            )
        for method_hit in _find_sweep_profile_face_method_reference_hits(parsed_tree):
            line_no = int(method_hit.get("line_no") or 0)
            hits.append(
                {
                    "rule_id": "invalid_build123d_contract.sweep_profile_face_method_reference",
                    "message": (
                        "`BuildSketch.face` is a method. Passing `profile.face` into "
                        "`sweep(...)` uses a bound method object instead of the actual profile face."
                    ),
                    "repair_hint": (
                        "Call `profile.face()` when extracting a face from the builder, or "
                        "pass `profile.sketch` / another real face object into `sweep(...)`."
                        + (
                            f" Repair the sweep profile object at line {line_no}."
                            if line_no > 0
                            else ""
                        )
                    ),
                }
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


def _find_nested_buildpart_part_arithmetic_hits(tree: ast.AST) -> list[dict[str, Any]]:
    if not isinstance(tree, ast.Module):
        return []
    hits: list[dict[str, Any]] = []
    seen: set[tuple[str, tuple[str, ...], int]] = set()
    for node in ast.walk(tree):
        host_alias = _buildpart_with_alias(node)
        if not host_alias:
            continue
        outer_body = ast.Module(body=list(getattr(node, "body", [])), type_ignores=[])
        nested_aliases = {
            alias
            for child in ast.walk(outer_body)
            if (alias := _buildpart_with_alias(child))
        }
        nested_aliases.discard(host_alias)
        if not nested_aliases:
            continue
        for child in ast.walk(outer_body):
            assignment = _host_part_arithmetic_assignment(
                node=child,
                host_alias=host_alias,
            )
            if assignment is None:
                continue
            value_expr, line_no = assignment
            referenced_aliases = tuple(
                sorted(
                    alias
                    for alias in nested_aliases
                    if _expression_references_part_attr(value_expr, alias)
                )
            )
            if not referenced_aliases:
                continue
            key = (host_alias, referenced_aliases, line_no)
            if key in seen:
                continue
            seen.add(key)
            hits.append(
                {
                    "host_alias": host_alias,
                    "nested_aliases": list(referenced_aliases),
                    "line_no": line_no,
                }
            )
    return hits


def _find_nested_buildpart_part_transform_hits(tree: ast.AST) -> list[dict[str, Any]]:
    if not isinstance(tree, ast.Module):
        return []
    hits: list[dict[str, Any]] = []
    seen: set[tuple[str, str, int]] = set()
    for node in ast.walk(tree):
        host_alias = _buildpart_with_alias(node)
        if not host_alias:
            continue
        outer_body = ast.Module(body=list(getattr(node, "body", [])), type_ignores=[])
        nested_aliases = {
            alias
            for child in ast.walk(outer_body)
            if (alias := _buildpart_with_alias(child))
        }
        nested_aliases.discard(host_alias)
        if not nested_aliases:
            continue
        for child in ast.walk(outer_body):
            if not isinstance(child, ast.Call):
                continue
            for nested_alias in nested_aliases:
                method_name = _call_targets_named_part_transform_method(child, nested_alias)
                if not method_name:
                    continue
                line_no = int(getattr(child, "lineno", 0) or 0)
                key = (nested_alias, method_name, line_no)
                if key in seen:
                    continue
                seen.add(key)
                hits.append(
                    {
                        "host_alias": host_alias,
                        "nested_alias": nested_alias,
                        "method_name": method_name,
                        "line_no": line_no,
                    }
                )
    return hits


def _find_nested_subtractive_buildpart_hits(tree: ast.AST) -> list[dict[str, Any]]:
    if not isinstance(tree, ast.Module):
        return []

    class _Visitor(ast.NodeVisitor):
        def __init__(self) -> None:
            self._builder_stack: list[str] = []
            self._locations_depth = 0
            self._hits: list[dict[str, Any]] = []
            self._seen: set[tuple[int, bool]] = set()

        @property
        def hits(self) -> list[dict[str, Any]]:
            return self._hits

        def visit_With(self, node: ast.With) -> None:  # noqa: N802
            self._visit_with_like(node.items, node.body)

        def visit_AsyncWith(self, node: ast.AsyncWith) -> None:  # noqa: N802
            self._visit_with_like(node.items, node.body)

        def _visit_with_like(
            self,
            items: list[ast.withitem],
            body: list[ast.stmt],
        ) -> None:
            pushed_builders = 0
            pushed_locations = 0
            try:
                for item in items:
                    context_expr = item.context_expr
                    if _with_context_builder_name(context_expr) == "BuildPart":
                        if _call_is_subtractive_buildpart(context_expr) and self._builder_stack:
                            line_no = int(
                                getattr(context_expr, "lineno", 0)
                                or getattr(item, "lineno", 0)
                                or 0
                            )
                            cache_key = (line_no, self._locations_depth > 0)
                            if cache_key not in self._seen:
                                self._seen.add(cache_key)
                                self._hits.append(
                                    {
                                        "line_no": line_no,
                                        "inside_locations": self._locations_depth > 0,
                                    }
                                )
                        self._builder_stack.append("BuildPart")
                        pushed_builders += 1
                        continue
                    if _with_context_is_locations(context_expr):
                        self._locations_depth += 1
                        pushed_locations += 1
                for statement in body:
                    self.visit(statement)
            finally:
                for _ in range(pushed_builders):
                    self._builder_stack.pop()
                self._locations_depth = max(0, self._locations_depth - pushed_locations)

    visitor = _Visitor()
    visitor.visit(tree)
    return visitor.hits


def _find_detached_subtractive_builder_without_host_hits(
    tree: ast.AST,
) -> list[dict[str, Any]]:
    if not isinstance(tree, ast.Module):
        return []

    class _Visitor(ast.NodeVisitor):
        def __init__(self, *, builder_alias: str) -> None:
            self._builder_alias = builder_alias
            self._host_materialized = False
            self._hits: list[dict[str, Any]] = []
            self._seen_lines: set[int] = set()

        @property
        def hits(self) -> list[dict[str, Any]]:
            return self._hits

        def visit_With(self, node: ast.With) -> None:  # noqa: N802
            if any(
                _with_context_builder_name(item.context_expr) == "BuildPart"
                for item in node.items
            ):
                return
            self.generic_visit(node)

        def visit_AsyncWith(self, node: ast.AsyncWith) -> None:  # noqa: N802
            if any(
                _with_context_builder_name(item.context_expr) == "BuildPart"
                for item in node.items
            ):
                return
            self.generic_visit(node)

        def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
            operation_name = _call_subtractive_without_host_operation_name(node)
            if operation_name is not None and not self._host_materialized:
                line_no = int(getattr(node, "lineno", 0) or 0)
                if line_no not in self._seen_lines:
                    self._seen_lines.add(line_no)
                    self._hits.append(
                        {
                            "line_no": line_no,
                            "builder_alias": self._builder_alias,
                            "operation_name": operation_name,
                        }
                    )
            if _call_materializes_additive_host(node):
                self._host_materialized = True
            self.generic_visit(node)

    hits: list[dict[str, Any]] = []
    seen: set[tuple[str, int]] = set()
    for node in ast.walk(tree):
        builder_alias = _buildpart_with_alias(node)
        if not builder_alias:
            continue
        visitor = _Visitor(builder_alias=builder_alias)
        for statement in list(getattr(node, "body", [])):
            visitor.visit(statement)
        for hit in visitor.hits:
            line_no = int(hit.get("line_no") or 0)
            cache_key = (builder_alias, line_no)
            if cache_key in seen:
                continue
            seen.add(cache_key)
            hits.append(hit)
    return hits


def _find_clamshell_unrotated_default_hinge_cylinder_hits(
    tree: ast.AST,
    *,
    code_for_lint: str,
) -> list[dict[str, Any]]:
    if not isinstance(tree, ast.Module):
        return []

    def _expr_text(node: ast.AST | None) -> str:
        if node is None:
            return ""
        try:
            return ast.unparse(node).replace(" ", "").lower()
        except Exception:  # pragma: no cover - defensive fallback
            return ""

    def _locations_target_back_hinge_seam(node: ast.With) -> bool:
        for item in node.items:
            context_expr = item.context_expr
            if not isinstance(context_expr, ast.Call):
                continue
            func = context_expr.func
            if not isinstance(func, ast.Name) or func.id != "Locations":
                continue
            for arg in context_expr.args:
                if not isinstance(arg, ast.Tuple) or len(arg.elts) < 2:
                    continue
                y_text = _expr_text(arg.elts[1])
                if "hinge_y" in y_text or "-depth/2" in y_text:
                    return True
        return False

    hits: list[dict[str, Any]] = []
    seen_lines: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.With) or not _locations_target_back_hinge_seam(node):
            continue
        for statement in node.body:
            statement_source = ast.get_source_segment(code_for_lint, statement) or ""
            normalized = statement_source.replace(" ", "")
            if "Cylinder(" not in statement_source:
                continue
            if any(token in statement_source for token in ("Rot(", ".rotate(", "rotation=")):
                continue
            if "mode=Mode.SUBTRACT" in normalized:
                continue
            line_no = int(getattr(statement, "lineno", 0) or 0)
            if line_no in seen_lines:
                continue
            seen_lines.add(line_no)
            hits.append({"line_no": line_no})
    return hits


def _find_active_buildpart_temporary_primitive_arithmetic_hits(
    tree: ast.AST,
) -> list[dict[str, Any]]:
    if not isinstance(tree, ast.Module):
        return []
    hits: list[dict[str, Any]] = []
    seen: set[tuple[str, tuple[str, ...], int]] = set()
    for node in ast.walk(tree):
        host_alias = _buildpart_with_alias(node)
        if not host_alias:
            continue
        outer_body = ast.Module(body=list(getattr(node, "body", [])), type_ignores=[])
        primitive_assignments: dict[str, dict[str, Any]] = {}
        for child in ast.walk(outer_body):
            if not isinstance(child, ast.Assign) or len(child.targets) != 1:
                continue
            target = child.targets[0]
            if not isinstance(target, ast.Name):
                continue
            primitive_name = _primitive_constructor_name(child.value)
            if primitive_name is None:
                continue
            primitive_assignments[target.id] = {
                "primitive_name": primitive_name,
                "line_no": int(getattr(child, "lineno", 0) or 0),
            }
        if not primitive_assignments:
            continue
        for child in ast.walk(outer_body):
            arithmetic_expr, line_no = _temporary_primitive_arithmetic_expr(child)
            if arithmetic_expr is None:
                continue
            referenced_vars = tuple(
                sorted(
                    variable_name
                    for variable_name in primitive_assignments
                    if _expression_references_name(arithmetic_expr, variable_name)
                )
            )
            if not referenced_vars:
                continue
            key = (host_alias, referenced_vars, line_no)
            if key in seen:
                continue
            seen.add(key)
            hits.append(
                {
                    "host_alias": host_alias,
                    "primitive_vars": list(referenced_vars),
                    "line_no": line_no,
                }
            )
    return hits


def _find_compound_positional_children_contract_hits(
    tree: ast.AST,
) -> list[dict[str, Any]]:
    if not isinstance(tree, ast.Module):
        return []

    hits: list[dict[str, Any]] = []
    seen_lines: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not _ast_name_matches(node.func, "Compound"):
            continue
        if len(node.args) <= 1:
            continue
        line_no = int(getattr(node, "lineno", 0) or 0)
        if line_no in seen_lines:
            continue
        seen_lines.add(line_no)
        hits.append(
            {
                "line_no": line_no,
                "arg_count": len(node.args),
            }
        )
    return hits


def _find_active_buildpart_host_part_mutation_hits(
    tree: ast.AST,
) -> list[dict[str, Any]]:
    if not isinstance(tree, ast.Module):
        return []
    hits: list[dict[str, Any]] = []
    seen: set[tuple[str, tuple[str, ...], int]] = set()
    for node in ast.walk(tree):
        host_alias = _buildpart_with_alias(node)
        if not host_alias:
            continue
        outer_body = ast.Module(body=list(getattr(node, "body", [])), type_ignores=[])
        primitive_assignments: dict[str, dict[str, Any]] = {}
        for child in ast.walk(outer_body):
            if not isinstance(child, ast.Assign) or len(child.targets) != 1:
                continue
            target = child.targets[0]
            if not isinstance(target, ast.Name):
                continue
            primitive_name = _primitive_constructor_name(child.value)
            if primitive_name is None:
                continue
            primitive_assignments[target.id] = {
                "primitive_name": primitive_name,
                "line_no": int(getattr(child, "lineno", 0) or 0),
            }
        if not primitive_assignments:
            continue
        for child in ast.walk(outer_body):
            assignment = _host_part_arithmetic_assignment(node=child, host_alias=host_alias)
            if assignment is None:
                continue
            value_expr, line_no = assignment
            referenced_vars = tuple(
                sorted(
                    variable_name
                    for variable_name in primitive_assignments
                    if _expression_references_name(value_expr, variable_name)
                )
            )
            if not referenced_vars:
                continue
            key = (host_alias, referenced_vars, line_no)
            if key in seen:
                continue
            seen.add(key)
            hits.append(
                {
                    "host_alias": host_alias,
                    "primitive_vars": list(referenced_vars),
                    "line_no": line_no,
                }
            )
    return hits


def _find_active_buildpart_temporary_primitive_transform_hits(
    tree: ast.AST,
) -> list[dict[str, Any]]:
    if not isinstance(tree, ast.Module):
        return []
    hits: list[dict[str, Any]] = []
    seen: set[tuple[str, tuple[str, ...], tuple[str, ...], int]] = set()
    for node in ast.walk(tree):
        host_alias = _buildpart_with_alias(node)
        if not host_alias:
            continue
        outer_body = ast.Module(body=list(getattr(node, "body", [])), type_ignores=[])
        primitive_assignments: dict[str, dict[str, Any]] = {}
        for child in ast.walk(outer_body):
            if not isinstance(child, ast.Assign) or len(child.targets) != 1:
                continue
            target = child.targets[0]
            if not isinstance(target, ast.Name):
                continue
            primitive_name = _primitive_constructor_name(child.value)
            if primitive_name is None:
                continue
            primitive_assignments[target.id] = {
                "primitive_name": primitive_name,
                "line_no": int(getattr(child, "lineno", 0) or 0),
            }
        if not primitive_assignments:
            continue
        for child in ast.walk(outer_body):
            transform_expr, line_no, transform_kinds = _temporary_primitive_transform_expr(child)
            if transform_expr is None:
                continue
            referenced_vars = tuple(
                sorted(
                    variable_name
                    for variable_name in primitive_assignments
                    if _expression_references_name(transform_expr, variable_name)
                )
            )
            if (
                not referenced_vars
                and isinstance(child, ast.Assign)
                and len(child.targets) == 1
                and isinstance(child.targets[0], ast.Name)
                and child.targets[0].id in primitive_assignments
            ):
                referenced_vars = (child.targets[0].id,)
            if not referenced_vars:
                continue
            key = (host_alias, referenced_vars, tuple(sorted(transform_kinds)), line_no)
            if key in seen:
                continue
            seen.add(key)
            hits.append(
                {
                    "host_alias": host_alias,
                    "primitive_vars": list(referenced_vars),
                    "transform_kinds": sorted(transform_kinds),
                    "line_no": line_no,
                }
            )
    return hits


def _find_explicit_anchor_manual_cutter_missing_subtract_hits(
    tree: ast.AST,
) -> list[dict[str, Any]]:
    if not isinstance(tree, ast.Module):
        return []

    class _Visitor(ast.NodeVisitor):
        _primitive_names = {"Cone", "Cylinder"}

        def __init__(self) -> None:
            self._context_stack: list[str] = []
            self._feature_locations_stack: list[bool] = []
            self._hits: list[dict[str, Any]] = []
            self._seen: set[tuple[str, int]] = set()

        @property
        def hits(self) -> list[dict[str, Any]]:
            return self._hits

        def visit_With(self, node: ast.With) -> None:  # noqa: N802
            self._visit_with_like(node.items, node.body)

        def visit_AsyncWith(self, node: ast.AsyncWith) -> None:  # noqa: N802
            self._visit_with_like(node.items, node.body)

        def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
            primitive_name = next(
                (
                    name
                    for name in self._primitive_names
                    if _ast_name_matches(node.func, name)
                ),
                None,
            )
            if (
                primitive_name is not None
                and "BuildPart" in self._context_stack
                and any(self._feature_locations_stack)
                and not _call_uses_mode_subtract(node)
                and not _call_uses_mode_private(node)
            ):
                line_no = int(getattr(node, "lineno", 0) or 0)
                cache_key = (primitive_name, line_no)
                if cache_key not in self._seen:
                    self._seen.add(cache_key)
                    self._hits.append(
                        {"line_no": line_no, "primitive_name": primitive_name}
                    )
            self.generic_visit(node)

        def _visit_with_like(
            self,
            items: list[ast.withitem],
            body: list[ast.stmt],
        ) -> None:
            pushed_contexts = 0
            pushed_feature_locations = 0
            try:
                for item in items:
                    context_expr = item.context_expr
                    builder_name = _with_context_builder_name(context_expr)
                    if builder_name is not None:
                        self._context_stack.append(builder_name)
                        pushed_contexts += 1
                        continue
                    if _with_context_is_locations(context_expr):
                        self._feature_locations_stack.append(
                            _locations_context_suggests_local_feature_placement(
                                context_expr
                            )
                        )
                        pushed_feature_locations += 1
                for statement in body:
                    self.visit(statement)
            finally:
                for _ in range(pushed_contexts):
                    self._context_stack.pop()
                for _ in range(pushed_feature_locations):
                    if self._feature_locations_stack:
                        self._feature_locations_stack.pop()

    visitor = _Visitor()
    visitor.visit(tree)
    return visitor.hits


def _find_display_only_helper_hits(tree: ast.AST) -> list[dict[str, Any]]:
    hits: list[dict[str, Any]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module_name = str(node.module or "").strip().lower()
            if module_name == "ocp_vscode":
                imported_names = [
                    alias.name
                    for alias in node.names
                    if isinstance(alias.name, str) and alias.name.strip()
                ]
                hits.append(
                    {
                        "line_no": int(getattr(node, "lineno", 0) or 0),
                        "helper_label": (
                            f"from ocp_vscode import {', '.join(imported_names)}"
                            if imported_names
                            else "from ocp_vscode import ..."
                        ),
                    }
                )
        elif isinstance(node, ast.Import):
            for alias in node.names:
                module_name = str(alias.name or "").strip().lower()
                if module_name == "ocp_vscode":
                    hits.append(
                        {
                            "line_no": int(getattr(node, "lineno", 0) or 0),
                            "helper_label": "import ocp_vscode",
                        }
                    )
        elif isinstance(node, ast.Call):
            callee = None
            if isinstance(node.func, ast.Name):
                callee = str(node.func.id or "").strip()
            elif isinstance(node.func, ast.Attribute):
                callee = str(node.func.attr or "").strip()
            if callee in {"show", "show_object"}:
                hits.append(
                    {
                        "line_no": int(getattr(node, "lineno", 0) or 0),
                        "helper_label": f"{callee}(...)",
                    }
                )
    return hits


def _find_case_drift_local_symbol_hits(tree: ast.AST) -> list[dict[str, Any]]:
    if not isinstance(tree, ast.Module):
        return []
    bound_names = _collect_module_local_binding_names(tree)
    if not bound_names:
        return []
    known_build123d_symbols = (
        _build123d_exported_symbol_names()
        if _module_imports_build123d_symbols(tree)
        else set()
    )
    lower_to_names: dict[str, set[str]] = {}
    for name in bound_names:
        lowered = name.lower()
        if not lowered:
            continue
        lower_to_names.setdefault(lowered, set()).add(name)

    class _Visitor(ast.NodeVisitor):
        def __init__(self) -> None:
            self.hits: list[dict[str, Any]] = []
            self._seen: set[tuple[int, str, str]] = set()

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
            return None

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:  # noqa: N802
            return None

        def visit_ClassDef(self, node: ast.ClassDef) -> None:  # noqa: N802
            return None

        def visit_Lambda(self, node: ast.Lambda) -> None:  # noqa: N802
            return None

        def visit_Name(self, node: ast.Name) -> None:  # noqa: N802
            if not isinstance(node.ctx, ast.Load):
                return
            name = str(node.id or "").strip()
            if not name or name in bound_names or name in known_build123d_symbols:
                return
            candidates = sorted(
                candidate
                for candidate in lower_to_names.get(name.lower(), set())
                if candidate != name
            )
            if not candidates:
                return
            suggested_name = candidates[0]
            cache_key = (int(getattr(node, "lineno", 0) or 0), name, suggested_name)
            if cache_key in self._seen:
                return
            self._seen.add(cache_key)
            self.hits.append(
                {
                    "line_no": int(getattr(node, "lineno", 0) or 0),
                    "undefined_name": name,
                    "suggested_name": suggested_name,
                }
            )

    visitor = _Visitor()
    visitor.visit(tree)
    return visitor.hits


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


def _find_plane_located_call_hits(tree: ast.AST) -> list[dict[str, Any]]:
    if not isinstance(tree, ast.Module):
        return []
    plane_binding_names = _collect_module_plane_binding_names(tree)
    hits: list[dict[str, Any]] = []
    seen_lines: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (
            isinstance(func, ast.Attribute)
            and str(func.attr or "").strip() == "located"
            and _ast_expr_is_plane_like(func.value, plane_binding_names)
        ):
            continue
        line_no = int(getattr(node, "lineno", 0) or 0)
        if line_no in seen_lines:
            continue
        seen_lines.add(line_no)
        hits.append({"line_no": line_no})
    return hits


def _find_plane_moved_call_hits(tree: ast.AST) -> list[dict[str, Any]]:
    if not isinstance(tree, ast.Module):
        return []
    plane_binding_names = _collect_module_plane_binding_names(tree)
    hits: list[dict[str, Any]] = []
    seen_lines: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (
            isinstance(func, ast.Attribute)
            and str(func.attr or "").strip() == "moved"
            and _ast_expr_is_plane_like(func.value, plane_binding_names)
        ):
            continue
        line_no = int(getattr(node, "lineno", 0) or 0)
        if line_no in seen_lines:
            continue
        seen_lines.add(line_no)
        hits.append({"line_no": line_no})
    return hits


def _find_face_plane_shift_origin_global_coordinate_hits(
    tree: ast.AST,
) -> list[dict[str, Any]]:
    if not isinstance(tree, ast.Module):
        return []
    hits: list[dict[str, Any]] = []
    seen_lines: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (
            isinstance(func, ast.Attribute)
            and str(func.attr or "").strip() == "shift_origin"
            and _ast_expr_is_face_plane_constructor(func.value)
        ):
            continue
        locator_expr: ast.AST | None = node.args[0] if node.args else None
        if locator_expr is None:
            for keyword in node.keywords:
                if str(getattr(keyword, "arg", "") or "").strip() == "locator":
                    locator_expr = keyword.value
                    break
        if locator_expr is None or not _looks_like_xyz_coordinate_tuple(locator_expr):
            continue
        line_no = int(getattr(node, "lineno", 0) or 0)
        if line_no in seen_lines:
            continue
        seen_lines.add(line_no)
        hits.append({"line_no": line_no})
    return hits


def _collect_module_local_binding_names(tree: ast.Module) -> set[str]:
    names: set[str] = set()

    class _Collector(ast.NodeVisitor):
        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
            if node.name:
                names.add(str(node.name))
            for decorator in node.decorator_list:
                self.visit(decorator)
            if node.returns is not None:
                self.visit(node.returns)

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:  # noqa: N802
            self.visit_FunctionDef(node)

        def visit_ClassDef(self, node: ast.ClassDef) -> None:  # noqa: N802
            if node.name:
                names.add(str(node.name))
            for decorator in node.decorator_list:
                self.visit(decorator)
            for base in node.bases:
                self.visit(base)
            for keyword in node.keywords:
                self.visit(keyword.value)

        def visit_Assign(self, node: ast.Assign) -> None:  # noqa: N802
            for target in node.targets:
                _collect_binding_names_from_target(target, names)
            self.visit(node.value)

        def visit_AnnAssign(self, node: ast.AnnAssign) -> None:  # noqa: N802
            _collect_binding_names_from_target(node.target, names)
            if node.value is not None:
                self.visit(node.value)
            self.visit(node.annotation)

        def visit_AugAssign(self, node: ast.AugAssign) -> None:  # noqa: N802
            _collect_binding_names_from_target(node.target, names)
            self.visit(node.target)
            self.visit(node.value)

        def visit_For(self, node: ast.For) -> None:  # noqa: N802
            _collect_binding_names_from_target(node.target, names)
            self.visit(node.iter)
            for statement in node.body:
                self.visit(statement)
            for statement in node.orelse:
                self.visit(statement)

        def visit_AsyncFor(self, node: ast.AsyncFor) -> None:  # noqa: N802
            self.visit_For(node)

        def visit_With(self, node: ast.With) -> None:  # noqa: N802
            for item in node.items:
                self.visit(item.context_expr)
                if item.optional_vars is not None:
                    _collect_binding_names_from_target(item.optional_vars, names)
            for statement in node.body:
                self.visit(statement)

        def visit_AsyncWith(self, node: ast.AsyncWith) -> None:  # noqa: N802
            self.visit_With(node)

        def visit_Import(self, node: ast.Import) -> None:  # noqa: N802
            for alias in node.names:
                alias_name = str(alias.asname or alias.name or "").strip()
                if alias_name:
                    names.add(alias_name.split(".")[0])

        def visit_ImportFrom(self, node: ast.ImportFrom) -> None:  # noqa: N802
            for alias in node.names:
                if alias.name == "*":
                    continue
                alias_name = str(alias.asname or alias.name or "").strip()
                if alias_name:
                    names.add(alias_name.split(".")[0])

        def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:  # noqa: N802
            if isinstance(node.name, str) and node.name.strip():
                names.add(node.name.strip())
            if node.type is not None:
                self.visit(node.type)
            for statement in node.body:
                self.visit(statement)

        def visit_NamedExpr(self, node: ast.NamedExpr) -> None:  # noqa: N802
            _collect_binding_names_from_target(node.target, names)
            self.visit(node.value)

    _Collector().visit(tree)
    return names


def _collect_module_plane_binding_names(tree: ast.Module) -> set[str]:
    plane_names: set[str] = set()
    for statement in tree.body:
        value: ast.AST | None = None
        targets: list[ast.AST] = []
        if isinstance(statement, ast.Assign):
            value = statement.value
            targets = list(statement.targets)
        elif isinstance(statement, ast.AnnAssign):
            value = statement.value
            targets = [statement.target]
        elif isinstance(statement, ast.AugAssign):
            value = statement.value
            targets = [statement.target]
        if value is None or not _ast_expr_is_plane_like(value, plane_names):
            continue
        for target in targets:
            _collect_binding_names_from_target(target, plane_names)
    return plane_names


def _collect_binding_names_from_target(target: ast.AST, names: set[str]) -> None:
    if isinstance(target, ast.Name) and target.id:
        names.add(str(target.id))
        return
    if isinstance(target, (ast.Tuple, ast.List)):
        for item in target.elts:
            _collect_binding_names_from_target(item, names)
        return
    if isinstance(target, ast.Starred):
        _collect_binding_names_from_target(target.value, names)


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


def _find_plane_rotated_origin_guess_hits(tree: ast.AST) -> list[dict[str, Any]]:
    if not isinstance(tree, ast.Module):
        return []
    hits: list[dict[str, Any]] = []
    seen: set[tuple[str, int]] = set()
    for node in ast.walk(tree):
        if not _is_plane_rotated_call(node):
            continue
        line_no = int(getattr(node, "lineno", 0) or 0)
        if len(node.args) >= 2 and _looks_like_vector_tuple(node.args[1]):
            key = ("tuple_ordering_guess", line_no)
            if key not in seen:
                seen.add(key)
                hits.append({"line_no": line_no})
        for keyword in node.keywords:
            if str(getattr(keyword, "arg", "") or "").strip() != "origin":
                continue
            key = ("origin_keyword_guess", line_no)
            if key in seen:
                continue
            seen.add(key)
            hits.append({"line_no": line_no})
    return hits


def _find_plane_rotate_method_hits(tree: ast.AST) -> list[dict[str, Any]]:
    if not isinstance(tree, ast.Module):
        return []
    hits: list[dict[str, Any]] = []
    seen_lines: set[int] = set()
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "rotate"
            and _looks_like_plane_expr(node.func.value)
        ):
            continue
        line_no = int(getattr(node, "lineno", 0) or 0)
        if line_no in seen_lines:
            continue
        seen_lines.add(line_no)
        hits.append({"line_no": line_no})
    return hits


def _find_directional_drill_plane_offset_coordinate_hits(
    tree: ast.AST,
    *,
    requirement_lower: str,
) -> list[dict[str, Any]]:
    if not isinstance(tree, ast.Module):
        return []
    target_plane_name = _directional_drill_workplane_with_in_plane_anchor(requirement_lower)
    if target_plane_name is None:
        return []
    hits: list[dict[str, Any]] = []
    seen_lines: set[int] = set()
    for node in ast.walk(tree):
        if not _is_buildsketch_with_plane_offset(node, plane_name=target_plane_name):
            continue
        plane_expr = node.args[0]
        offset_arg = _plane_offset_argument(plane_expr)
        if offset_arg is None or _is_zero_literal(offset_arg):
            continue
        line_no = int(getattr(node, "lineno", 0) or 0)
        if line_no in seen_lines:
            continue
        seen_lines.add(line_no)
        hits.append({"line_no": line_no})
    return hits


def _find_centered_box_face_plane_offset_span_mismatch_hits(
    tree: ast.AST,
    *,
    requirement_lower: str,
) -> list[dict[str, Any]]:
    if not isinstance(tree, ast.Module):
        return []
    if not any(
        token in requirement_lower
        for token in (
            "top face",
            "bottom face",
            "front face",
            "back face",
            "side face",
            "left face",
            "right face",
        )
    ):
        return []

    centered_box_spans: list[dict[str, str]] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and _ast_name_matches(node.func, "Box")):
            continue
        if any(str(getattr(keyword, "arg", "") or "").strip() == "align" for keyword in node.keywords):
            continue
        spans = _extract_centered_box_span_exprs(node)
        if spans:
            centered_box_spans.append(spans)
    if not centered_box_spans:
        return []

    hits: list[dict[str, Any]] = []
    seen_lines: set[int] = set()
    plane_to_span_key = {"XY": "z_span", "XZ": "y_span", "YZ": "x_span"}
    for node in ast.walk(tree):
        offset_arg = _plane_offset_argument(node)
        if offset_arg is None:
            continue
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "offset"
            and isinstance(node.func.value, ast.Attribute)
            and isinstance(node.func.value.value, ast.Name)
            and node.func.value.value.id == "Plane"
        ):
            continue
        plane_name = str(node.func.value.attr or "").strip()
        span_key = plane_to_span_key.get(plane_name)
        if span_key is None:
            continue
        offset_text = _ast_expr_text(offset_arg)
        if not offset_text:
            continue
        for spans in centered_box_spans:
            span_expr = str(spans.get(span_key) or "").strip()
            if not span_expr or span_expr != offset_text:
                continue
            line_no = int(getattr(node, "lineno", 0) or 0)
            if line_no in seen_lines:
                continue
            seen_lines.add(line_no)
            hits.append(
                {
                    "line_no": line_no,
                    "plane_name": plane_name,
                    "span_expr": span_expr,
                }
            )
    return hits


def _find_named_face_plane_family_mismatch_hits(
    tree: ast.AST,
    *,
    requirement_lower: str,
) -> list[dict[str, Any]]:
    if not isinstance(tree, ast.Module):
        return []
    mentioned_groups = _named_face_requirement_plane_groups(requirement_lower)
    if not mentioned_groups:
        return []
    allowed_planes = {_plane_name_for_named_face_group(group) for group in mentioned_groups}
    plane_aliases = _collect_named_plane_aliases(tree)
    parent_map = _build_parent_map(tree)
    buildpart_host_span_cache: dict[int, set[str]] = {}

    allowed_plane_seen = False
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Call)
            and (
                _ast_name_matches(node.func, "BuildSketch")
                or _ast_name_matches(node.func, "Locations")
            )
            and node.args
        ):
            continue
        plane_name = _named_plane_root_name(node.args[0], plane_aliases=plane_aliases)
        if plane_name in allowed_planes:
            allowed_plane_seen = True
            break

    hits: list[dict[str, Any]] = []
    seen_lines: set[int] = set()
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Call)
            and (
                _ast_name_matches(node.func, "BuildSketch")
                or _ast_name_matches(node.func, "Locations")
            )
            and node.args
        ):
            continue
        plane_name = _named_plane_root_name(node.args[0], plane_aliases=plane_aliases)
        if not plane_name or plane_name in allowed_planes:
            continue
        if allowed_plane_seen and _is_plain_named_plane_expr(
            node.args[0],
            plane_aliases=plane_aliases,
        ):
            continue
        if _buildsketch_candidate_is_host_profile(
            node,
            parent_map=parent_map,
            buildpart_host_span_cache=buildpart_host_span_cache,
        ):
            continue
        if _buildsketch_candidate_is_inert_placeholder_builder(
            node,
            parent_map=parent_map,
        ):
            continue
        if _buildsketch_candidate_is_detached_axisymmetric_positive_helper(
            node,
            parent_map=parent_map,
            buildpart_host_span_cache=buildpart_host_span_cache,
        ):
            continue
        line_no = int(getattr(node, "lineno", 0) or 0)
        if line_no in seen_lines:
            continue
        seen_lines.add(line_no)
        hits.append(
            {
                "line_no": line_no,
                "plane_name": plane_name,
                "expected_planes": sorted(allowed_planes),
            }
        )
    return hits


def _build_parent_map(tree: ast.AST) -> dict[ast.AST, ast.AST]:
    parent_map: dict[ast.AST, ast.AST] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parent_map[child] = parent
    return parent_map


def _buildsketch_candidate_is_host_profile(
    node: ast.AST,
    *,
    parent_map: dict[ast.AST, ast.AST],
    buildpart_host_span_cache: dict[int, set[str]],
) -> bool:
    if not (isinstance(node, ast.Call) and _ast_name_matches(node.func, "BuildSketch")):
        return False
    sketch_with = _enclosing_with_for_context_call(node, parent_map=parent_map)
    if sketch_with is None:
        return False
    buildpart_with = _enclosing_buildpart_with(sketch_with, parent_map=parent_map)
    if buildpart_with is None:
        return False
    cache_key = id(buildpart_with)
    host_span_ids = buildpart_host_span_cache.get(cache_key)
    if host_span_ids is None:
        host_span_ids = _collect_buildpart_host_span_ids(buildpart_with)
        buildpart_host_span_cache[cache_key] = host_span_ids
    if len(host_span_ids) < 2:
        return False
    host_profile_alias_ids = _collect_buildpart_host_profile_alias_ids(
        buildpart_with,
        seed_ids=host_span_ids,
    )
    sketch_ids = _strip_host_profile_modifier_ids(_collect_sketch_size_identifier_names(sketch_with.body))
    host_profile_ids = host_span_ids | host_profile_alias_ids
    return len(sketch_ids) >= 2 and sketch_ids.issubset(host_profile_ids)


def _buildsketch_candidate_is_inert_placeholder_builder(
    node: ast.AST,
    *,
    parent_map: dict[ast.AST, ast.AST],
) -> bool:
    if not (isinstance(node, ast.Call) and _ast_name_matches(node.func, "BuildSketch")):
        return False
    sketch_with = _enclosing_with_for_context_call(node, parent_map=parent_map)
    if sketch_with is None:
        return False
    buildpart_with = _enclosing_buildpart_with(sketch_with, parent_map=parent_map)
    if buildpart_with is None:
        return False
    return not _buildpart_contains_materializing_ops(buildpart_with.body)


_AXISYMMETRIC_SKETCH_PRIMITIVE_NAMES = {
    "Circle",
    "Ellipse",
}

_DETACHED_POSITIVE_BUILDER_SUBTRACTIVE_HELPERS = {
    "Hole",
    "CounterBoreHole",
    "CounterSinkHole",
}


def _buildsketch_candidate_is_detached_axisymmetric_positive_helper(
    node: ast.AST,
    *,
    parent_map: dict[ast.AST, ast.AST],
    buildpart_host_span_cache: dict[int, set[str]],
) -> bool:
    if not (isinstance(node, ast.Call) and _ast_name_matches(node.func, "BuildSketch")):
        return False
    sketch_with = _enclosing_with_for_context_call(node, parent_map=parent_map)
    if sketch_with is None:
        return False
    buildpart_with = _enclosing_buildpart_with(sketch_with, parent_map=parent_map)
    if buildpart_with is None:
        return False
    cache_key = id(buildpart_with)
    host_span_ids = buildpart_host_span_cache.get(cache_key)
    if host_span_ids is None:
        host_span_ids = _collect_buildpart_host_span_ids(buildpart_with)
        buildpart_host_span_cache[cache_key] = host_span_ids
    if len(host_span_ids) >= 2:
        return False
    primitive_names = _collect_sketch_primitive_helper_names(sketch_with.body)
    if not primitive_names or not primitive_names.issubset(_AXISYMMETRIC_SKETCH_PRIMITIVE_NAMES):
        return False
    return not _buildpart_contains_subtractive_ops(buildpart_with.body)


_HOST_PROFILE_MODIFIER_IDS = {
    "wall",
    "thickness",
    "offset",
    "clearance",
    "gap",
    "radius",
    "corner_radius",
    "fillet",
    "draft",
    "lip",
    "shell",
}

_HOST_PROFILE_MODIFIER_TOKENS = _HOST_PROFILE_MODIFIER_IDS | {
    "thick",
}

_HOST_PROFILE_IGNORED_TOKENS = {
    "mm",
}


def _identifier_tokens(name: str) -> list[str]:
    normalized = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", name or "").lower()
    return [token for token in re.split(r"[^a-z0-9]+", normalized) if token]


def _is_host_profile_modifier_id(name: str | None) -> bool:
    cleaned = str(name or "").strip()
    if not cleaned:
        return False
    lowered = cleaned.lower()
    if lowered in _HOST_PROFILE_MODIFIER_IDS:
        return True
    tokens = [
        token for token in _identifier_tokens(cleaned) if token not in _HOST_PROFILE_IGNORED_TOKENS
    ]
    return bool(tokens) and all(token in _HOST_PROFILE_MODIFIER_TOKENS for token in tokens)


def _strip_host_profile_modifier_ids(names: set[str]) -> set[str]:
    return {name for name in names if not _is_host_profile_modifier_id(name)}


def _enclosing_with_for_context_call(
    node: ast.AST,
    *,
    parent_map: dict[ast.AST, ast.AST],
) -> ast.With | ast.AsyncWith | None:
    current = parent_map.get(node)
    while current is not None:
        if isinstance(current, ast.withitem):
            parent = parent_map.get(current)
            if isinstance(parent, (ast.With, ast.AsyncWith)):
                return parent
        current = parent_map.get(current)
    return None


def _enclosing_buildpart_with(
    node: ast.AST,
    *,
    parent_map: dict[ast.AST, ast.AST],
) -> ast.With | ast.AsyncWith | None:
    current = node
    while current is not None:
        if isinstance(current, (ast.With, ast.AsyncWith)) and any(
            _with_context_builder_name(item.context_expr) == "BuildPart"
            for item in current.items
        ):
            return current
        current = parent_map.get(current)
    return None


def _collect_buildpart_host_span_ids(node: ast.With | ast.AsyncWith) -> set[str]:
    for stmt in node.body:
        ids = _extract_host_span_ids_from_stmt(stmt)
        if len(ids) >= 2:
            return ids
    return set()


def _collect_sketch_size_identifier_names(body: list[ast.stmt]) -> set[str]:
    ids: set[str] = set()
    for stmt in body:
        for node in ast.walk(stmt):
            if not isinstance(node, ast.Call):
                continue
            if not any(
                _ast_name_matches(node.func, shape_name)
                for shape_name in ("Rectangle", "RectangleRounded", "SlotOverall", "SlotCenterToCenter")
            ):
                continue
            ids |= _collect_identifier_names_from_exprs(node.args[:2])
    return ids


def _collect_sketch_primitive_helper_names(body: list[ast.stmt]) -> set[str]:
    names: set[str] = set()
    for stmt in body:
        for node in ast.walk(stmt):
            if not isinstance(node, ast.Call):
                continue
            for helper_name in _AXISYMMETRIC_SKETCH_PRIMITIVE_NAMES:
                if _ast_name_matches(node.func, helper_name):
                    names.add(helper_name)
    return names


def _buildpart_contains_subtractive_ops(body: list[ast.stmt]) -> bool:
    for stmt in body:
        for node in ast.walk(stmt):
            if not isinstance(node, ast.Call):
                continue
            if any(_ast_name_matches(node.func, helper) for helper in _DETACHED_POSITIVE_BUILDER_SUBTRACTIVE_HELPERS):
                return True
            for keyword in node.keywords or []:
                if str(keyword.arg or "").strip() != "mode":
                    continue
                if _ast_dotted_name(keyword.value) == "Mode.SUBTRACT":
                    return True
    return False


def _buildpart_contains_materializing_ops(body: list[ast.stmt]) -> bool:
    for stmt in body:
        for node in ast.walk(stmt):
            if not isinstance(node, ast.Call):
                continue
            if _call_materializes_additive_host(node):
                return True
            if _call_subtractive_without_host_operation_name(node) is not None:
                return True
    return False


def _ast_dotted_name(node: ast.AST | None) -> str | None:
    if node is None:
        return None
    if isinstance(node, ast.Name):
        return str(node.id or "").strip() or None
    if isinstance(node, ast.Attribute):
        parent = _ast_dotted_name(node.value)
        attr = str(node.attr or "").strip()
        if parent and attr:
            return f"{parent}.{attr}"
        if attr:
            return attr
    return None


def _collect_buildpart_host_profile_alias_ids(
    node: ast.With | ast.AsyncWith,
    *,
    seed_ids: set[str],
) -> set[str]:
    known_ids = set(seed_ids)
    alias_ids: set[str] = set()
    changed = True
    while changed:
        changed = False
        for stmt in node.body:
            for target_name, value_ids in _iter_assignment_name_dependencies(stmt):
                if not target_name:
                    continue
                if target_name in known_ids or _is_host_profile_modifier_id(target_name):
                    continue
                cleaned_value_ids = _strip_host_profile_modifier_ids({item for item in value_ids if item})
                if not cleaned_value_ids:
                    continue
                if not cleaned_value_ids.issubset(known_ids):
                    continue
                known_ids.add(target_name)
                alias_ids.add(target_name)
                changed = True
    return alias_ids


def _iter_assignment_name_dependencies(stmt: ast.stmt) -> Iterable[tuple[str, set[str]]]:
    for node in ast.walk(stmt):
        if isinstance(node, ast.Assign):
            value_ids = _collect_identifier_names_from_exprs([node.value])
            for target in node.targets:
                if isinstance(target, ast.Name):
                    target_name = str(target.id or "").strip()
                    if target_name:
                        yield target_name, value_ids
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            target_name = str(node.target.id or "").strip()
            if target_name:
                value_ids = _collect_identifier_names_from_exprs([node.value]) if node.value else set()
                yield target_name, value_ids


def _extract_host_span_ids_from_stmt(stmt: ast.stmt) -> set[str]:
    if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call) and _ast_name_matches(
        stmt.value.func, "Box"
    ):
        return _strip_host_profile_modifier_ids(_collect_identifier_names_from_exprs(stmt.value.args[:3]))
    if isinstance(stmt, (ast.With, ast.AsyncWith)):
        if any(_with_context_builder_name(item.context_expr) == "BuildSketch" for item in stmt.items):
            return _strip_host_profile_modifier_ids(_collect_sketch_size_identifier_names(stmt.body))
        if any(_with_context_is_locations(item.context_expr) for item in stmt.items):
            for inner_stmt in stmt.body:
                ids = _extract_host_span_ids_from_stmt(inner_stmt)
                if len(ids) >= 2:
                    return ids
    return set()


def _collect_identifier_names_from_exprs(exprs: Sequence[ast.AST]) -> set[str]:
    ids: set[str] = set()
    for expr in exprs:
        for node in ast.walk(expr):
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                name = str(node.id or "").strip()
                if name:
                    ids.add(name)
    return ids


def _find_buildsketch_countersink_context_hits(tree: ast.AST) -> list[dict[str, Any]]:
    if not isinstance(tree, ast.Module):
        return []

    class _Visitor(ast.NodeVisitor):
        def __init__(self) -> None:
            self._context_stack: list[str] = []
            self._hits: list[dict[str, Any]] = []
            self._seen_lines: set[int] = set()

        @property
        def hits(self) -> list[dict[str, Any]]:
            return self._hits

        def visit_With(self, node: ast.With) -> None:  # noqa: N802
            self._visit_with_like(node.items, node.body)

        def visit_AsyncWith(self, node: ast.AsyncWith) -> None:  # noqa: N802
            self._visit_with_like(node.items, node.body)

        def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
            if _ast_name_matches(node.func, "CounterSinkHole") and "BuildSketch" in self._context_stack:
                line_no = int(getattr(node, "lineno", 0) or 0)
                if line_no not in self._seen_lines:
                    self._seen_lines.add(line_no)
                    self._hits.append({"line_no": line_no})
            self.generic_visit(node)

        def _visit_with_like(
            self,
            items: list[ast.withitem],
            body: list[ast.stmt],
        ) -> None:
            added_contexts: list[str] = []
            for item in items:
                builder_name = _with_context_builder_name(item.context_expr)
                if builder_name is None:
                    continue
                added_contexts.append(builder_name)
                self._context_stack.append(builder_name)
            try:
                for statement in body:
                    self.visit(statement)
            finally:
                for _ in added_contexts:
                    self._context_stack.pop()

    visitor = _Visitor()
    visitor.visit(tree)
    return visitor.hits


def _find_buildsketch_curve_context_hits(tree: ast.AST) -> list[dict[str, Any]]:
    if not isinstance(tree, ast.Module):
        return []

    class _Visitor(ast.NodeVisitor):
        _curve_helper_names = {"Polyline", "Line", "CenterArc", "RadiusArc"}

        def __init__(self) -> None:
            self._context_stack: list[str] = []
            self._hits: list[dict[str, Any]] = []
            self._seen: set[tuple[int, str]] = set()

        @property
        def hits(self) -> list[dict[str, Any]]:
            return self._hits

        def visit_With(self, node: ast.With) -> None:  # noqa: N802
            self._visit_with_like(node.items, node.body)

        def visit_AsyncWith(self, node: ast.AsyncWith) -> None:  # noqa: N802
            self._visit_with_like(node.items, node.body)

        def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
            helper_name = next(
                (
                    name
                    for name in self._curve_helper_names
                    if _ast_name_matches(node.func, name)
                ),
                None,
            )
            if (
                helper_name is not None
                and "BuildSketch" in self._context_stack
                and "BuildLine" not in self._context_stack
            ):
                line_no = int(getattr(node, "lineno", 0) or 0)
                cache_key = (line_no, helper_name)
                if cache_key not in self._seen:
                    self._seen.add(cache_key)
                    self._hits.append(
                        {"line_no": line_no, "helper_name": helper_name}
                    )
            self.generic_visit(node)

        def _visit_with_like(
            self,
            items: list[ast.withitem],
            body: list[ast.stmt],
        ) -> None:
            added_contexts: list[str] = []
            for item in items:
                builder_name = _with_context_builder_name(item.context_expr)
                if builder_name is None:
                    continue
                added_contexts.append(builder_name)
                self._context_stack.append(builder_name)
            try:
                for statement in body:
                    self.visit(statement)
            finally:
                for _ in added_contexts:
                    self._context_stack.pop()

    visitor = _Visitor()
    visitor.visit(tree)
    return visitor.hits


def _find_buildpart_sketch_primitive_context_hits(tree: ast.AST) -> list[dict[str, Any]]:
    if not isinstance(tree, ast.Module):
        return []

    class _Visitor(ast.NodeVisitor):
        _sketch_helper_names = {
            "Circle",
            "Ellipse",
            "Rectangle",
            "RectangleRounded",
            "RegularPolygon",
            "Polygon",
            "SlotCenterToCenter",
            "SlotOverall",
            "Text",
            "Trapezoid",
        }

        def __init__(self) -> None:
            self._context_stack: list[str] = []
            self._hits: list[dict[str, Any]] = []
            self._seen: set[tuple[int, str]] = set()

        @property
        def hits(self) -> list[dict[str, Any]]:
            return self._hits

        def visit_With(self, node: ast.With) -> None:  # noqa: N802
            self._visit_with_like(node.items, node.body)

        def visit_AsyncWith(self, node: ast.AsyncWith) -> None:  # noqa: N802
            self._visit_with_like(node.items, node.body)

        def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
            helper_name = next(
                (
                    name
                    for name in self._sketch_helper_names
                    if _ast_name_matches(node.func, name)
                ),
                None,
            )
            if (
                helper_name is not None
                and "BuildPart" in self._context_stack
                and "BuildSketch" not in self._context_stack
            ):
                line_no = int(getattr(node, "lineno", 0) or 0)
                cache_key = (line_no, helper_name)
                if cache_key not in self._seen:
                    self._seen.add(cache_key)
                    self._hits.append(
                        {"line_no": line_no, "helper_name": helper_name}
                    )
            self.generic_visit(node)

        def _visit_with_like(
            self,
            items: list[ast.withitem],
            body: list[ast.stmt],
        ) -> None:
            added_contexts: list[str] = []
            for item in items:
                builder_name = _with_context_builder_name(item.context_expr)
                if builder_name is None:
                    continue
                added_contexts.append(builder_name)
                self._context_stack.append(builder_name)
            try:
                for statement in body:
                    self.visit(statement)
            finally:
                for _ in added_contexts:
                    self._context_stack.pop()

    visitor = _Visitor()
    visitor.visit(tree)
    return visitor.hits


def _find_rectanglerounded_radius_bounds_hits(tree: ast.AST) -> list[dict[str, Any]]:
    if not isinstance(tree, ast.Module):
        return []
    numeric_env = _collect_numeric_assignment_env(tree)
    hits: list[dict[str, Any]] = []
    seen_lines: set[int] = set()
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and _ast_name_matches(node.func, "RectangleRounded")):
            continue
        width_expr, height_expr, radius_expr = _rectanglerounded_dimension_args(node)
        width_value = _eval_numeric_expr(width_expr, numeric_env)
        height_value = _eval_numeric_expr(height_expr, numeric_env)
        radius_value = _eval_numeric_expr(radius_expr, numeric_env)
        if width_value is None or height_value is None or radius_value is None:
            continue
        min_span = min(float(width_value), float(height_value))
        if min_span <= 0:
            continue
        if 2.0 * float(radius_value) < min_span:
            continue
        line_no = int(getattr(node, "lineno", 0) or 0)
        if line_no in seen_lines:
            continue
        seen_lines.add(line_no)
        hits.append(
            {
                "line_no": line_no,
                "width": float(width_value),
                "height": float(height_value),
                "radius": float(radius_value),
            }
        )
    return hits


def _rectanglerounded_dimension_args(node: ast.Call) -> tuple[ast.AST | None, ast.AST | None, ast.AST | None]:
    positional = list(node.args[:3])
    keyword_map = {
        str(getattr(keyword, "arg", "") or "").strip(): keyword.value
        for keyword in node.keywords
        if str(getattr(keyword, "arg", "") or "").strip()
    }
    width_expr = positional[0] if len(positional) >= 1 else keyword_map.get("width")
    height_expr = positional[1] if len(positional) >= 2 else keyword_map.get("height")
    radius_expr = positional[2] if len(positional) >= 3 else keyword_map.get("radius")
    return width_expr, height_expr, radius_expr


def _collect_numeric_assignment_env(tree: ast.AST) -> dict[str, float]:
    ordered_bindings = _collect_ordered_numeric_assignment_bindings(tree)
    env: dict[str, float] = {}
    if not ordered_bindings:
        return env

    # Resolve simple numeric aliases with source-order precedence while keeping the
    # pass count bounded so repeated reassignments cannot oscillate forever.
    max_passes = max(len(ordered_bindings) + 1, 2)
    for _ in range(max_passes):
        next_env = dict(env)
        for name, value_expr in ordered_bindings:
            value = _eval_numeric_expr(value_expr, next_env)
            if value is None:
                continue
            next_env[name] = value
        if next_env == env:
            return next_env
        env = next_env
    return env


def _collect_ordered_numeric_assignment_bindings(
    tree: ast.AST,
) -> list[tuple[str, ast.AST]]:
    if not isinstance(tree, ast.Module):
        return []

    bindings: list[tuple[str, ast.AST]] = []

    class _Visitor(ast.NodeVisitor):
        def visit_Assign(self, node: ast.Assign) -> None:  # noqa: N802
            for target in node.targets:
                if not isinstance(target, ast.Name):
                    continue
                name = str(target.id or "").strip()
                if name:
                    bindings.append((name, node.value))
            self.generic_visit(node)

        def visit_AnnAssign(self, node: ast.AnnAssign) -> None:  # noqa: N802
            if isinstance(node.target, ast.Name) and node.value is not None:
                name = str(node.target.id or "").strip()
                if name:
                    bindings.append((name, node.value))
            self.generic_visit(node)

    _Visitor().visit(tree)
    return bindings


def _eval_numeric_expr(node: ast.AST | None, env: dict[str, float]) -> float | None:
    if node is None:
        return None
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return float(node.value)
    if isinstance(node, ast.Name):
        return env.get(str(node.id or "").strip())
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        operand = _eval_numeric_expr(node.operand, env)
        if operand is None:
            return None
        return operand if isinstance(node.op, ast.UAdd) else -operand
    if isinstance(node, ast.BinOp) and isinstance(
        node.op,
        (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod, ast.Pow),
    ):
        left = _eval_numeric_expr(node.left, env)
        right = _eval_numeric_expr(node.right, env)
        if left is None or right is None:
            return None
        if isinstance(node.op, ast.Add):
            return left + right
        if isinstance(node.op, ast.Sub):
            return left - right
        if isinstance(node.op, ast.Mult):
            return left * right
        if isinstance(node.op, ast.Div):
            return left / right if right != 0 else None
        if isinstance(node.op, ast.FloorDiv):
            return left // right if right != 0 else None
        if isinstance(node.op, ast.Mod):
            return left % right if right != 0 else None
        if isinstance(node.op, ast.Pow):
            return left**right
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        func_name = str(node.func.id or "").strip()
        arg_values = [_eval_numeric_expr(arg, env) for arg in node.args]
        if any(value is None for value in arg_values):
            return None
        numeric_args = [float(value) for value in arg_values if value is not None]
        if func_name == "max" and numeric_args:
            return max(numeric_args)
        if func_name == "min" and numeric_args:
            return min(numeric_args)
        if func_name == "abs" and len(numeric_args) == 1:
            return abs(numeric_args[0])
    return None


def _find_transform_context_manager_hits(tree: ast.AST) -> list[dict[str, Any]]:
    if not isinstance(tree, ast.Module):
        return []
    hits: list[dict[str, Any]] = []
    seen: set[tuple[int, str]] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.With, ast.AsyncWith)):
            continue
        for item in node.items:
            context_expr = item.context_expr
            if not isinstance(context_expr, ast.Call):
                continue
            helper_name = next(
                (
                    name
                    for name in ("Rot", "Pos")
                    if _ast_name_matches(context_expr.func, name)
                ),
                None,
            )
            if helper_name is None:
                continue
            line_no = int(getattr(context_expr, "lineno", 0) or getattr(node, "lineno", 0) or 0)
            cache_key = (line_no, helper_name)
            if cache_key in seen:
                continue
            seen.add(cache_key)
            hits.append({"line_no": line_no, "helper_name": helper_name})
    return hits


def _find_buildsketch_wire_profile_missing_make_face_hits(tree: ast.AST) -> list[dict[str, Any]]:
    if not isinstance(tree, ast.Module):
        return []
    hits: list[dict[str, Any]] = []
    seen_lines: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.With):
            continue
        if not any(
            _with_context_builder_name(item.context_expr) == "BuildSketch" for item in node.items
        ):
            continue
        body_module = ast.Module(body=node.body, type_ignores=[])
        has_make_face = any(
            isinstance(child, ast.Call) and _ast_name_matches(child.func, "make_face")
            for child in ast.walk(body_module)
        )
        if has_make_face:
            continue
        has_nested_buildline = any(
            isinstance(child, ast.With)
            and any(
                _with_context_builder_name(item.context_expr) == "BuildLine"
                for item in child.items
            )
            for child in ast.walk(body_module)
        )
        if not has_nested_buildline:
            continue
        line_no = int(getattr(node, "lineno", 0) or 0)
        if line_no in seen_lines:
            continue
        seen_lines.add(line_no)
        hits.append({"line_no": line_no})
    return hits


def _find_circle_make_face_trim_profile_hits(tree: ast.AST) -> list[dict[str, Any]]:
    if not isinstance(tree, ast.Module):
        return []
    hits: list[dict[str, Any]] = []
    seen_lines: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.With):
            continue
        if not any(
            _with_context_builder_name(item.context_expr) == "BuildSketch" for item in node.items
        ):
            continue
        body_module = ast.Module(body=node.body, type_ignores=[])
        has_circle = any(
            isinstance(child, ast.Call) and _ast_name_matches(child.func, "Circle")
            for child in ast.walk(body_module)
        )
        has_make_face = any(
            isinstance(child, ast.Call) and _ast_name_matches(child.func, "make_face")
            for child in ast.walk(body_module)
        )
        has_nested_buildline = any(
            isinstance(child, ast.With)
            and any(
                _with_context_builder_name(item.context_expr) == "BuildLine"
                for item in child.items
            )
            for child in ast.walk(body_module)
        )
        has_line = any(
            isinstance(child, ast.Call) and _ast_name_matches(child.func, "Line")
            for child in ast.walk(body_module)
        )
        if not (has_circle and has_make_face and has_nested_buildline and has_line):
            continue
        line_no = int(getattr(node, "lineno", 0) or 0)
        if line_no in seen_lines:
            continue
        seen_lines.add(line_no)
        hits.append({"line_no": line_no})
    return hits


def _find_buildpart_topology_access_inside_buildsketch_hits(
    tree: ast.AST,
) -> list[dict[str, Any]]:
    if not isinstance(tree, ast.Module):
        return []

    class _Visitor(ast.NodeVisitor):
        def __init__(self) -> None:
            self._buildpart_alias_stack: list[str] = []
            self._buildsketch_depth = 0
            self._hits: list[dict[str, Any]] = []
            self._seen: set[tuple[str, str, int]] = set()

        @property
        def hits(self) -> list[dict[str, Any]]:
            return self._hits

        def visit_With(self, node: ast.With) -> None:  # noqa: N802
            self._visit_with_like(node.items, node.body)

        def visit_AsyncWith(self, node: ast.AsyncWith) -> None:  # noqa: N802
            self._visit_with_like(node.items, node.body)

        def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
            if (
                self._buildsketch_depth > 0
                and self._buildpart_alias_stack
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in {"vertices", "edges", "faces"}
                and isinstance(node.func.value, ast.Name)
            ):
                builder_alias = str(node.func.value.id)
                if builder_alias in self._buildpart_alias_stack:
                    line_no = int(getattr(node, "lineno", 0) or 0)
                    cache_key = (builder_alias, str(node.func.attr), line_no)
                    if cache_key not in self._seen:
                        self._seen.add(cache_key)
                        self._hits.append(
                            {
                                "line_no": line_no,
                                "builder_alias": builder_alias,
                                "accessor": str(node.func.attr),
                            }
                        )
            self.generic_visit(node)

        def _visit_with_like(
            self,
            items: list[ast.withitem],
            body: list[ast.stmt],
        ) -> None:
            pushed_buildparts = 0
            pushed_buildsketches = 0
            try:
                for item in items:
                    builder_name = _with_context_builder_name(item.context_expr)
                    if builder_name == "BuildPart":
                        buildpart_alias = "__active_buildpart__"
                        if isinstance(item.optional_vars, ast.Name) and item.optional_vars.id:
                            buildpart_alias = str(item.optional_vars.id)
                        self._buildpart_alias_stack.append(buildpart_alias)
                        pushed_buildparts += 1
                    elif builder_name == "BuildSketch":
                        self._buildsketch_depth += 1
                        pushed_buildsketches += 1
                for statement in body:
                    self.visit(statement)
            finally:
                for _ in range(pushed_buildparts):
                    self._buildpart_alias_stack.pop()
                self._buildsketch_depth = max(
                    0, self._buildsketch_depth - pushed_buildsketches
                )

    visitor = _Visitor()
    visitor.visit(tree)
    return visitor.hits


def _find_countersink_keyword_alias_hits(tree: ast.AST) -> list[dict[str, Any]]:
    if not isinstance(tree, ast.Module):
        return []
    hits: list[dict[str, Any]] = []
    seen: set[tuple[int, str]] = set()
    valid_helper_names = {"CounterSinkHole", "CountersinkHole", "CounterSink"}
    alias_names = {
        "countersink_radius",
        "countersink_angle",
        "countersink_depth",
        "counter_sink_depth",
        "angle",
        "cone_angle",
        "head_diameter",
        "head_radius",
        "countersink_diameter",
        "counter_sink_head_radius",
        "counter_sink_diameter",
        "head_dia",
        "countersink_dia",
        "counter_sink_dia",
        "thru_diameter",
        "through_diameter",
        "through_hole_diameter",
        "hole_diameter",
        "diameter",
        "thru_dia",
        "through_dia",
        "hole_dia",
    }
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not any(_ast_name_matches(node.func, helper_name) for helper_name in valid_helper_names):
            continue
        line_no = int(getattr(node, "lineno", 0) or 0)
        for keyword in node.keywords:
            alias_name = str(getattr(keyword, "arg", "") or "").strip()
            if alias_name not in alias_names:
                continue
            cache_key = (line_no, alias_name)
            if cache_key in seen:
                continue
            seen.add(cache_key)
            hits.append({"line_no": line_no, "alias_name": alias_name})
    return hits


def _find_member_fillet_radius_keyword_conflict_hits(
    tree: ast.AST,
) -> list[dict[str, Any]]:
    if not isinstance(tree, ast.Module):
        return []
    hits: list[dict[str, Any]] = []
    seen_lines: set[int] = set()
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "fillet"
            and node.args
        ):
            continue
        if not any(
            str(getattr(keyword, "arg", "") or "").strip() == "radius"
            for keyword in node.keywords
        ):
            continue
        line_no = int(getattr(node, "lineno", 0) or 0)
        if line_no in seen_lines:
            continue
        seen_lines.add(line_no)
        target_label = "solid"
        if isinstance(node.func.value, ast.Name) and node.func.value.id:
            target_label = str(node.func.value.id)
        hits.append({"line_no": line_no, "target_label": target_label})
    return hits


def _find_global_fillet_helper_argument_contract_hits(
    tree: ast.AST,
) -> list[dict[str, Any]]:
    if not isinstance(tree, ast.Module):
        return []
    hits: list[dict[str, Any]] = []
    seen_lines: set[int] = set()
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Call)
            and _ast_name_matches(node.func, "fillet")
            and len(node.args) > 2
        ):
            continue
        line_no = int(getattr(node, "lineno", 0) or 0)
        if line_no in seen_lines:
            continue
        seen_lines.add(line_no)
        target_label = "shape"
        first_arg = node.args[0]
        if isinstance(first_arg, ast.Name) and first_arg.id:
            target_label = str(first_arg.id)
        elif isinstance(first_arg, ast.Attribute):
            target_label = _ast_expr_text(first_arg)
        hits.append({"line_no": line_no, "target_label": target_label})
    return hits


def _extract_broad_shell_axis_selector_builder(node: ast.AST) -> str | None:
    if not (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "filter_by"
        and len(node.args) == 1
        and _ast_expr_text(node.args[0]) == "Axis.Z"
    ):
        return None
    edges_call = node.func.value
    if not (
        isinstance(edges_call, ast.Call)
        and isinstance(edges_call.func, ast.Attribute)
        and edges_call.func.attr == "edges"
    ):
        return None
    builder_expr = edges_call.func.value
    builder_label = _ast_expr_text(builder_expr).strip()
    return builder_label or "part"


def _extract_broad_edge_selector_builder(node: ast.AST) -> str | None:
    current = node
    saw_selector = False
    while (
        isinstance(current, ast.Call)
        and isinstance(current.func, ast.Attribute)
        and current.func.attr in {"filter_by", "filter_by_position"}
    ):
        saw_selector = True
        current = current.func.value
    if not saw_selector:
        return None
    if not (
        isinstance(current, ast.Call)
        and isinstance(current.func, ast.Attribute)
        and current.func.attr == "edges"
    ):
        return None
    builder_expr = current.func.value
    builder_label = _ast_expr_text(builder_expr).strip()
    return builder_label or "part"


def _find_broad_shell_axis_fillet_hits(tree: ast.AST) -> list[dict[str, Any]]:
    if not isinstance(tree, ast.Module):
        return []
    selector_bindings: dict[str, str] = {}
    for node in ast.walk(tree):
        target_name: str | None = None
        value: ast.AST | None = None
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            target_name = node.targets[0].id
            value = node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.value is not None:
            target_name = node.target.id
            value = node.value
        if not target_name or value is None:
            continue
        builder_label = _extract_broad_shell_axis_selector_builder(value)
        if builder_label:
            selector_bindings[target_name] = builder_label

    hits: list[dict[str, Any]] = []
    seen_lines: set[int] = set()
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Call)
            and _ast_name_matches(node.func, "fillet")
            and node.args
        ):
            continue
        first_arg = node.args[0]
        builder_label: str | None = None
        if isinstance(first_arg, ast.Name):
            builder_label = selector_bindings.get(first_arg.id)
        if builder_label is None:
            builder_label = _extract_broad_shell_axis_selector_builder(first_arg)
        if builder_label is None:
            continue
        line_no = int(getattr(node, "lineno", 0) or 0)
        if line_no in seen_lines:
            continue
        seen_lines.add(line_no)
        hits.append({"line_no": line_no, "builder_label": builder_label})
    return hits


def _find_broad_local_finish_tail_fillet_hits(tree: ast.AST) -> list[dict[str, Any]]:
    if not isinstance(tree, ast.Module):
        return []
    selector_bindings: dict[str, str] = {}
    for node in ast.walk(tree):
        target_name: str | None = None
        value: ast.AST | None = None
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
        ):
            target_name = node.targets[0].id
            value = node.value
        elif (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.value is not None
        ):
            target_name = node.target.id
            value = node.value
        if not target_name or value is None:
            continue
        builder_label = _extract_broad_edge_selector_builder(value)
        if builder_label:
            selector_bindings[target_name] = builder_label

    hits: list[dict[str, Any]] = []
    seen_lines: set[int] = set()
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Call)
            and _ast_name_matches(node.func, "fillet")
            and node.args
        ):
            continue
        first_arg = node.args[0]
        builder_label: str | None = None
        if isinstance(first_arg, ast.Name):
            builder_label = selector_bindings.get(first_arg.id)
        if builder_label is None:
            builder_label = _extract_broad_edge_selector_builder(first_arg)
        if builder_label is None:
            continue
        line_no = int(getattr(node, "lineno", 0) or 0)
        if line_no in seen_lines:
            continue
        seen_lines.add(line_no)
        hits.append({"line_no": line_no, "builder_label": builder_label})
    return hits


def _requirement_mentions_local_finish_fillet_tail(requirement_lower: str) -> bool:
    lowered = str(requirement_lower or "").lower()
    local_finish_tokens = (
        "local finish",
        "local finishing",
        "topology-aware",
        "topology aware",
        "later local finish",
        "later topology-aware",
        "opening rim",
        "rim edges",
        "target edge",
    )
    feature_tokens = (
        "fillet",
        "edge fillet",
        "chamfer",
        "edge chamfer",
    )
    return any(token in lowered for token in local_finish_tokens) and any(
        token in lowered for token in feature_tokens
    )


def _find_regular_polygon_keyword_alias_hits(tree: ast.AST) -> list[dict[str, Any]]:
    if not isinstance(tree, ast.Module):
        return []
    hits: list[dict[str, Any]] = []
    seen: set[tuple[int, str]] = set()
    alias_names = {"sides", "n_sides", "num_sides", "regular_sides"}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not _ast_name_matches(node.func, "RegularPolygon"):
            continue
        line_no = int(getattr(node, "lineno", 0) or 0)
        for keyword in node.keywords:
            alias_name = str(getattr(keyword, "arg", "") or "").strip()
            if alias_name not in alias_names:
                continue
            cache_key = (line_no, alias_name)
            if cache_key in seen:
                continue
            seen.add(cache_key)
            hits.append({"line_no": line_no, "alias_name": alias_name})
    return hits


def _find_slot_center_point_radius_alias_hits(tree: ast.AST) -> list[dict[str, Any]]:
    if not isinstance(tree, ast.Module):
        return []
    hits: list[dict[str, Any]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not _ast_name_matches(node.func, "SlotCenterPoint"):
            continue
        for keyword in node.keywords:
            if str(keyword.arg or "").strip() != "radius":
                continue
            hits.append(
                {
                    "line_no": int(getattr(keyword, "lineno", getattr(node, "lineno", 0)) or 0),
                }
            )
            break
    return hits


def _find_slot_center_point_center_alias_hits(tree: ast.AST) -> list[dict[str, Any]]:
    if not isinstance(tree, ast.Module):
        return []
    hits: list[dict[str, Any]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not _ast_name_matches(node.func, "SlotCenterPoint"):
            continue
        for keyword in node.keywords:
            if str(keyword.arg or "").strip() != "center_point":
                continue
            hits.append(
                {
                    "line_no": int(getattr(keyword, "lineno", getattr(node, "lineno", 0)) or 0),
                }
            )
            break
    return hits


def _find_slot_center_to_center_keyword_alias_hits(tree: ast.AST) -> list[dict[str, Any]]:
    if not isinstance(tree, ast.Module):
        return []
    hits: list[dict[str, Any]] = []
    seen: set[tuple[int, str]] = set()
    alias_names = {"center_to_center", "width"}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not _ast_name_matches(node.func, "SlotCenterToCenter"):
            continue
        line_no = int(getattr(node, "lineno", 0) or 0)
        for keyword in node.keywords:
            alias_name = str(getattr(keyword, "arg", "") or "").strip()
            if alias_name not in alias_names:
                continue
            cache_key = (line_no, alias_name)
            if cache_key in seen:
                continue
            seen.add(cache_key)
            hits.append({"line_no": line_no, "alias_name": alias_name})
    return hits


def _find_plane_keyword_alias_hits(tree: ast.AST) -> list[dict[str, Any]]:
    if not isinstance(tree, ast.Module):
        return []
    hits: list[dict[str, Any]] = []
    seen: set[tuple[int, str]] = set()
    alias_names = {"normal"}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not _ast_name_matches(node.func, "Plane"):
            continue
        line_no = int(getattr(node, "lineno", 0) or 0)
        for keyword in node.keywords:
            alias_name = str(getattr(keyword, "arg", "") or "").strip()
            if alias_name not in alias_names:
                continue
            cache_key = (line_no, alias_name)
            if cache_key in seen:
                continue
            seen.add(cache_key)
            hits.append({"line_no": line_no, "alias_name": alias_name})
    return hits


def _find_cone_keyword_alias_hits(tree: ast.AST) -> list[dict[str, Any]]:
    if not isinstance(tree, ast.Module):
        return []
    hits: list[dict[str, Any]] = []
    seen: set[tuple[int, str]] = set()
    alias_names = {"upper_radius", "lower_radius"}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not _ast_name_matches(node.func, "Cone"):
            continue
        line_no = int(getattr(node, "lineno", 0) or 0)
        for keyword in node.keywords:
            alias_name = str(getattr(keyword, "arg", "") or "").strip()
            if alias_name not in alias_names:
                continue
            cache_key = (line_no, alias_name)
            if cache_key in seen:
                continue
            seen.add(cache_key)
            hits.append({"line_no": line_no, "alias_name": alias_name})
    return hits


def _find_filter_by_position_keyword_band_hits(tree: ast.AST) -> list[dict[str, Any]]:
    if not isinstance(tree, ast.Module):
        return []
    hits: list[dict[str, Any]] = []
    seen: set[tuple[int, str]] = set()
    alias_names = {
        "XMin",
        "XMax",
        "YMin",
        "YMax",
        "ZMin",
        "ZMax",
        "x_min",
        "x_max",
        "y_min",
        "y_max",
        "z_min",
        "z_max",
        "min_x",
        "max_x",
        "min_y",
        "max_y",
        "min_z",
        "max_z",
    }
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "filter_by_position"
        ):
            continue
        line_no = int(getattr(node, "lineno", 0) or 0)
        for keyword in node.keywords:
            alias_name = str(getattr(keyword, "arg", "") or "").strip()
            if alias_name not in alias_names:
                continue
            cache_key = (line_no, alias_name)
            if cache_key in seen:
                continue
            seen.add(cache_key)
            hits.append({"line_no": line_no, "alias_name": alias_name})
    return hits


def _find_filter_by_position_plane_axis_hits(tree: ast.AST) -> list[dict[str, Any]]:
    if not isinstance(tree, ast.Module):
        return []
    hits: list[dict[str, Any]] = []
    seen: set[tuple[int, str]] = set()
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "filter_by_position"
            and node.args
        ):
            continue
        first_arg = node.args[0]
        if not (
            isinstance(first_arg, ast.Attribute)
            and isinstance(first_arg.value, ast.Name)
            and first_arg.value.id == "Plane"
        ):
            continue
        plane_name = str(first_arg.attr or "").strip()
        line_no = int(getattr(node, "lineno", 0) or 0)
        cache_key = (line_no, plane_name)
        if cache_key in seen:
            continue
        seen.add(cache_key)
        hits.append({"line_no": line_no, "plane_name": plane_name})
    return hits


def _find_lowercase_vector_component_attribute_hits(
    tree: ast.AST,
) -> list[dict[str, Any]]:
    if not isinstance(tree, ast.Module):
        return []
    hits: list[dict[str, Any]] = []
    seen: set[tuple[int, str]] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Attribute) or node.attr not in {"x", "y", "z"}:
            continue
        value = node.value
        if not (
            isinstance(value, ast.Call)
            and isinstance(value.func, ast.Attribute)
            and value.func.attr == "center"
        ):
            continue
        line_no = int(getattr(node, "lineno", 0) or 0)
        cache_key = (line_no, node.attr)
        if cache_key in seen:
            continue
        seen.add(cache_key)
        hits.append({"line_no": line_no, "attr_name": node.attr})
    return hits


def _find_center_arc_keyword_alias_hits(tree: ast.AST) -> list[dict[str, Any]]:
    if not isinstance(tree, ast.Module):
        return []
    hits: list[dict[str, Any]] = []
    seen: set[tuple[int, str]] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not _ast_name_matches(node.func, "CenterArc"):
            continue
        line_no = int(getattr(node, "lineno", 0) or 0)
        for keyword in node.keywords:
            alias_name = str(getattr(keyword, "arg", "") or "").strip()
            if alias_name not in {"arc_angle", "end_angle"}:
                continue
            cache_key = (line_no, alias_name)
            if cache_key in seen:
                continue
            seen.add(cache_key)
            hits.append({"line_no": line_no, "alias_name": alias_name})
    return hits


def _find_explicit_radius_arc_helper_hits(tree: ast.AST) -> list[dict[str, Any]]:
    if not isinstance(tree, ast.Module):
        return []
    hits: list[dict[str, Any]] = []
    seen: set[tuple[int, str]] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        helper_name = None
        if _ast_name_matches(node.func, "TangentArc"):
            helper_name = "TangentArc"
        elif _ast_name_matches(node.func, "JernArc"):
            helper_name = "JernArc"
        if helper_name is None:
            continue
        line_no = int(getattr(node, "lineno", 0) or 0)
        cache_key = (line_no, helper_name)
        if cache_key in seen:
            continue
        seen.add(cache_key)
        hits.append({"line_no": line_no, "helper_name": helper_name})
    return hits


def _find_sweep_path_method_reference_hits(tree: ast.AST) -> list[dict[str, Any]]:
    if not isinstance(tree, ast.Module):
        return []
    hits: list[dict[str, Any]] = []
    seen: set[tuple[int, str]] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not _ast_name_matches(node.func, "sweep"):
            continue
        candidate_nodes: list[ast.AST] = []
        if len(node.args) >= 2:
            candidate_nodes.append(node.args[1])
        for keyword in node.keywords:
            if str(getattr(keyword, "arg", "") or "").strip() == "path":
                candidate_nodes.append(keyword.value)
        for candidate in candidate_nodes:
            if not isinstance(candidate, ast.Attribute) or candidate.attr not in {"wire", "line"}:
                continue
            line_no = int(getattr(candidate, "lineno", 0) or getattr(node, "lineno", 0) or 0)
            cache_key = (line_no, str(candidate.attr))
            if cache_key in seen:
                continue
            seen.add(cache_key)
            hits.append({"line_no": line_no, "attribute_name": str(candidate.attr)})
    return hits


def _find_topology_geometry_attribute_hits(tree: ast.AST) -> list[dict[str, Any]]:
    if not isinstance(tree, ast.Module):
        return []
    hits: list[dict[str, Any]] = []
    seen: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Attribute) or node.attr != "geometry":
            continue
        line_no = int(getattr(node, "lineno", 0) or 0)
        if line_no in seen:
            continue
        seen.add(line_no)
        hits.append({"line_no": line_no})
    return hits


def _find_center_arc_missing_start_angle_hits(tree: ast.AST) -> list[dict[str, Any]]:
    if not isinstance(tree, ast.Module):
        return []
    hits: list[dict[str, Any]] = []
    seen: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not _ast_name_matches(node.func, "CenterArc"):
            continue
        keyword_names = {
            str(getattr(keyword, "arg", "") or "").strip()
            for keyword in node.keywords
            if str(getattr(keyword, "arg", "") or "").strip()
        }
        if "start_angle" in keyword_names:
            continue
        if len(node.args) >= 3:
            continue
        line_no = int(getattr(node, "lineno", 0) or 0)
        if line_no in seen:
            continue
        seen.add(line_no)
        hits.append({"line_no": line_no})
    return hits


def _find_symbolic_degree_constant_hits(code: str) -> list[dict[str, Any]]:
    hits: list[dict[str, Any]] = []
    seen: set[tuple[int, str]] = set()
    try:
        token_stream = tokenize.generate_tokens(io.StringIO(code).readline)
    except (tokenize.TokenError, IndentationError):
        return hits

    pending_name: tokenize.TokenInfo | None = None
    last_significant: tokenize.TokenInfo | None = None
    for token in token_stream:
        if token.type in {
            tokenize.NL,
            tokenize.NEWLINE,
            tokenize.INDENT,
            tokenize.DEDENT,
            tokenize.COMMENT,
            tokenize.ENDMARKER,
        }:
            continue
        if token.type == tokenize.NAME and token.string in {"DEGREE", "DEGREES"}:
            line_no = int(token.start[0] or 0)
            cache_key = (line_no, token.string)
            if last_significant is not None and last_significant.string in {"*", "/"}:
                if cache_key not in seen:
                    seen.add(cache_key)
                    hits.append({"line_no": line_no, "symbol_name": token.string})
                pending_name = None
            else:
                pending_name = token
            last_significant = token
            continue
        if pending_name is not None and token.string in {"*", "/"}:
            line_no = int(pending_name.start[0] or 0)
            cache_key = (line_no, pending_name.string)
            if cache_key not in seen:
                seen.add(cache_key)
                hits.append({"line_no": line_no, "symbol_name": pending_name.string})
        pending_name = None
        last_significant = token
    return hits


def _requirement_prefers_center_arc_for_explicit_radius_path(requirement_lower: str) -> bool:
    lowered = str(requirement_lower or "").strip().lower()
    return (
        "sweep" in lowered
        and any(token in lowered for token in ("path", "rail"))
        and "arc" in lowered
        and "radius" in lowered
        and any(token in lowered for token in ("tangent arc", "90-degree", "90 degree"))
    )


def _find_sweep_section_keyword_alias_hits(tree: ast.AST) -> list[dict[str, Any]]:
    if not isinstance(tree, ast.Module):
        return []
    hits: list[dict[str, Any]] = []
    seen: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not _ast_name_matches(node.func, "sweep"):
            continue
        for keyword in node.keywords:
            if str(getattr(keyword, "arg", "") or "").strip() != "section":
                continue
            line_no = int(getattr(keyword, "lineno", 0) or getattr(node, "lineno", 0) or 0)
            if line_no in seen:
                continue
            seen.add(line_no)
            hits.append({"line_no": line_no})
    return hits


def _find_solid_sweep_invalid_keyword_hits(tree: ast.AST) -> list[dict[str, Any]]:
    if not isinstance(tree, ast.Module):
        return []
    hits: list[dict[str, Any]] = []
    seen: set[tuple[int, str]] = set()
    allowed_keywords = {
        "section",
        "path",
        "inner_wires",
        "make_solid",
        "is_frenet",
        "mode",
        "transition",
    }
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not (
            isinstance(node.func, ast.Attribute)
            and node.func.attr == "sweep"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "Solid"
        ):
            continue
        line_no = int(getattr(node, "lineno", 0) or 0)
        for keyword in node.keywords:
            alias_name = str(getattr(keyword, "arg", "") or "").strip()
            if not alias_name or alias_name in allowed_keywords:
                continue
            cache_key = (line_no, alias_name)
            if cache_key in seen:
                continue
            seen.add(cache_key)
            hits.append({"line_no": line_no, "alias_name": alias_name})
    return hits


def _find_sweep_profile_face_method_reference_hits(tree: ast.AST) -> list[dict[str, Any]]:
    if not isinstance(tree, ast.Module):
        return []
    hits: list[dict[str, Any]] = []
    seen: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not _ast_name_matches(node.func, "sweep"):
            continue
        candidate_nodes: list[ast.AST] = []
        if node.args:
            candidate_nodes.append(node.args[0])
        for keyword in node.keywords:
            if str(getattr(keyword, "arg", "") or "").strip() in {"sections", "section"}:
                candidate_nodes.append(keyword.value)
        for candidate in candidate_nodes:
            if not isinstance(candidate, ast.Attribute) or candidate.attr != "face":
                continue
            line_no = int(getattr(candidate, "lineno", 0) or getattr(node, "lineno", 0) or 0)
            if line_no in seen:
                continue
            seen.add(line_no)
            hits.append({"line_no": line_no})
    return hits


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


def _call_uses_mode_subtract(node: ast.Call) -> bool:
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


def _call_materializes_additive_host(node: ast.Call) -> bool:
    if _call_uses_mode_subtract(node) or _call_uses_mode_private(node):
        return False
    call_name = _call_name(node)
    if call_name in {"Box", "Cylinder", "Cone", "Sphere", "Torus"}:
        return True
    if call_name in {"extrude", "revolve", "loft", "sweep"}:
        return True
    if call_name == "add":
        return True
    return False


def _call_subtractive_without_host_operation_name(node: ast.Call) -> str | None:
    call_name = _call_name(node)
    if not call_name:
        return None
    if call_name in {"Box", "Cylinder", "Cone", "Sphere", "Torus", "extrude"}:
        return call_name if _call_uses_mode_subtract(node) else None
    if call_name in {"Hole", "CounterBoreHole", "CounterSinkHole"}:
        return call_name
    return None


def _buildsketch_aliases_with_subtractive_entities(tree: ast.AST) -> set[str]:
    if not isinstance(tree, ast.Module):
        return set()
    aliases: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.With):
            continue
        for item in node.items:
            if not (
                isinstance(item.context_expr, ast.Call)
                and _ast_name_matches(item.context_expr.func, "BuildSketch")
            ):
                continue
            if not isinstance(item.optional_vars, ast.Name):
                continue
            builder_alias = str(item.optional_vars.id)
            for child in ast.walk(ast.Module(body=node.body, type_ignores=[])):
                if not isinstance(child, ast.Call):
                    continue
                for keyword in child.keywords:
                    if str(getattr(keyword, "arg", "") or "").strip() != "mode":
                        continue
                    if _ast_is_mode_subtract(keyword.value):
                        aliases.add(builder_alias)
                        break
                if builder_alias in aliases:
                    break
    return aliases


def _expr_anchors_to_builder_faces(expr: ast.AST, *, builder_alias: str) -> bool:
    if isinstance(expr, ast.Call):
        func = expr.func
        if (
            isinstance(func, ast.Attribute)
            and func.attr == "faces"
            and isinstance(func.value, ast.Name)
            and func.value.id == builder_alias
        ):
            return True
        if isinstance(func, ast.Attribute):
            return _expr_anchors_to_builder_faces(func.value, builder_alias=builder_alias)
    if isinstance(expr, ast.Attribute):
        return _expr_anchors_to_builder_faces(expr.value, builder_alias=builder_alias)
    return False


def _subscript_index_value(node: ast.Subscript) -> int | None:
    slice_node = node.slice
    if isinstance(slice_node, ast.Constant) and isinstance(slice_node.value, int):
        return int(slice_node.value)
    if isinstance(slice_node, ast.UnaryOp) and isinstance(slice_node.op, ast.USub) and isinstance(
        slice_node.operand, ast.Constant
    ) and isinstance(slice_node.operand.value, int):
        return -int(slice_node.operand.value)
    return None


def _find_annular_profile_face_splitting_hits(tree: ast.AST) -> list[dict[str, Any]]:
    if not isinstance(tree, ast.Module):
        return []
    subtractive_aliases = _buildsketch_aliases_with_subtractive_entities(tree)
    if not subtractive_aliases:
        return []
    hits: list[dict[str, Any]] = []
    seen: set[tuple[str, int]] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Subscript):
            continue
        index_value = _subscript_index_value(node)
        if index_value is None or index_value < 1:
            continue
        line_no = int(getattr(node, "lineno", 0) or 0)
        for builder_alias in subtractive_aliases:
            if not _expr_anchors_to_builder_faces(node.value, builder_alias=builder_alias):
                continue
            cache_key = (builder_alias, line_no)
            if cache_key in seen:
                continue
            seen.add(cache_key)
            hits.append({"line_no": line_no, "builder_alias": builder_alias})
    return hits


def _find_annular_profile_face_extraction_sweep_hits(tree: ast.AST) -> list[dict[str, Any]]:
    if not isinstance(tree, ast.Module):
        return []
    subtractive_aliases = _buildsketch_aliases_with_subtractive_entities(tree)
    if not subtractive_aliases:
        return []

    extracted_face_aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        value = node.value
        if not (
            isinstance(value, ast.Call)
            and isinstance(value.func, ast.Attribute)
            and value.func.attr == "face"
            and isinstance(value.func.value, ast.Name)
            and value.func.value.id in subtractive_aliases
        ):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id:
                extracted_face_aliases[target.id] = value.func.value.id

    hits: list[dict[str, Any]] = []
    seen: set[tuple[str, int]] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not _ast_name_matches(node.func, "sweep"):
            continue
        candidate_nodes: list[ast.AST] = []
        if node.args:
            candidate_nodes.append(node.args[0])
        for keyword in node.keywords:
            if str(getattr(keyword, "arg", "") or "").strip() in {"section", "sections"}:
                candidate_nodes.append(keyword.value)
        for candidate in candidate_nodes:
            builder_alias = None
            if isinstance(candidate, ast.Name):
                builder_alias = extracted_face_aliases.get(candidate.id)
            elif (
                isinstance(candidate, ast.Call)
                and isinstance(candidate.func, ast.Attribute)
                and candidate.func.attr == "face"
                and isinstance(candidate.func.value, ast.Name)
                and candidate.func.value.id in subtractive_aliases
            ):
                builder_alias = candidate.func.value.id
            if not builder_alias:
                continue
            line_no = int(getattr(candidate, "lineno", 0) or getattr(node, "lineno", 0) or 0)
            cache_key = (builder_alias, line_no)
            if cache_key in seen:
                continue
            seen.add(cache_key)
            hits.append({"line_no": line_no, "builder_alias": builder_alias})
    return hits


def _expr_is_build123d_vector_like(node: ast.AST) -> bool:
    return isinstance(node, ast.BinOp) and isinstance(node.op, (ast.MatMult, ast.Mod))


def _find_vector_component_indexing_hits(tree: ast.AST) -> list[dict[str, Any]]:
    if not isinstance(tree, ast.Module):
        return []
    vector_aliases: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign) or not _expr_is_build123d_vector_like(node.value):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id:
                vector_aliases.add(target.id)

    hits: list[dict[str, Any]] = []
    seen: set[tuple[int, int]] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Subscript):
            continue
        index_value = _subscript_index_value(node)
        if index_value not in {0, 1, 2}:
            continue
        value = node.value
        if not (
            _expr_is_build123d_vector_like(value)
            or (isinstance(value, ast.Name) and value.id in vector_aliases)
        ):
            continue
        line_no = int(getattr(node, "lineno", 0) or 0)
        cache_key = (line_no, index_value)
        if cache_key in seen:
            continue
        seen.add(cache_key)
        hits.append({"line_no": line_no, "index_value": index_value})
    return hits


def _find_builder_method_reference_assignment_hits(tree: ast.AST) -> list[dict[str, Any]]:
    if not isinstance(tree, ast.Module):
        return []
    builder_aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.With, ast.AsyncWith)):
            continue
        for item in node.items:
            builder_name = _with_context_builder_name(item.context_expr)
            if builder_name not in {"BuildLine", "BuildSketch"}:
                continue
            optional_vars = getattr(item, "optional_vars", None)
            if isinstance(optional_vars, ast.Name) and optional_vars.id:
                builder_aliases[optional_vars.id] = builder_name
    if not builder_aliases:
        return []
    hits: list[dict[str, Any]] = []
    seen: set[tuple[int, str]] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        value = node.value
        if not (
            isinstance(value, ast.Attribute)
            and isinstance(value.value, ast.Name)
            and value.value.id in builder_aliases
            and value.attr in {"wire", "face"}
        ):
            continue
        line_no = int(getattr(value, "lineno", 0) or getattr(node, "lineno", 0) or 0)
        cache_key = (line_no, value.attr)
        if cache_key in seen:
            continue
        seen.add(cache_key)
        hits.append(
            {
                "line_no": line_no,
                "builder_alias": value.value.id,
                "builder_name": builder_aliases[value.value.id],
                "method_name": value.attr,
            }
        )
    return hits


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


def _with_context_builder_name(node: ast.AST) -> str | None:
    context_expr = node
    if isinstance(context_expr, ast.Call):
        context_expr = context_expr.func
    for builder_name in ("BuildPart", "BuildSketch", "BuildLine"):
        if _ast_name_matches(context_expr, builder_name):
            return builder_name
    return None


def _with_context_is_locations(node: ast.AST) -> bool:
    context_expr = node
    if isinstance(context_expr, ast.Call):
        context_expr = context_expr.func
    return _ast_name_matches(context_expr, "Locations")


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


def _is_plane_rotated_call(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "rotated"
        and _looks_like_plane_expr(node.func.value)
    )


def _looks_like_plane_expr(node: ast.AST) -> bool:
    if isinstance(node, ast.Attribute):
        return isinstance(node.value, ast.Name) and node.value.id == "Plane"
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        if node.func.attr not in {"offset", "rotated"}:
            return False
        return _looks_like_plane_expr(node.func.value)
    return False


def _is_buildsketch_with_plane_offset(node: ast.AST, *, plane_name: str) -> bool:
    if not isinstance(node, ast.Call) or not _ast_name_matches(node.func, "BuildSketch"):
        return False
    if not node.args:
        return False
    plane_expr = node.args[0]
    if not (
        isinstance(plane_expr, ast.Call)
        and isinstance(plane_expr.func, ast.Attribute)
        and plane_expr.func.attr == "offset"
    ):
        return False
    return _is_named_plane_expr(plane_expr.func.value, plane_name=plane_name)


def _is_named_plane_expr(node: ast.AST, *, plane_name: str) -> bool:
    return (
        isinstance(node, ast.Attribute)
        and node.attr == plane_name
        and isinstance(node.value, ast.Name)
        and node.value.id == "Plane"
    )


def _plane_offset_argument(node: ast.AST) -> ast.AST | None:
    if not (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "offset"
    ):
        return None
    if node.args:
        return node.args[0]
    for keyword in node.keywords:
        if str(getattr(keyword, "arg", "") or "").strip() in {"amount", "distance", "offset"}:
            return keyword.value
    return None


def _is_zero_literal(node: ast.AST) -> bool:
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return float(node.value) == 0.0
    if (
        isinstance(node, ast.UnaryOp)
        and isinstance(node.op, (ast.UAdd, ast.USub))
        and isinstance(node.operand, ast.Constant)
        and isinstance(node.operand.value, (int, float))
    ):
        value = float(node.operand.value)
        return (-value if isinstance(node.op, ast.USub) else value) == 0.0
    return False


def _extract_centered_box_span_exprs(node: ast.Call) -> dict[str, str]:
    if not (isinstance(node, ast.Call) and _ast_name_matches(node.func, "Box")):
        return {}
    positional = list(node.args[:3])
    keyword_map = {
        str(getattr(keyword, "arg", "") or "").strip(): keyword.value
        for keyword in node.keywords
        if str(getattr(keyword, "arg", "") or "").strip()
    }
    x_arg = positional[0] if len(positional) >= 1 else keyword_map.get("length")
    y_arg = positional[1] if len(positional) >= 2 else keyword_map.get("width")
    z_arg = positional[2] if len(positional) >= 3 else keyword_map.get("height")
    x_span = _ast_expr_text(x_arg) if x_arg is not None else ""
    y_span = _ast_expr_text(y_arg) if y_arg is not None else ""
    z_span = _ast_expr_text(z_arg) if z_arg is not None else ""
    if not (x_span and y_span and z_span):
        return {}
    return {"x_span": x_span, "y_span": y_span, "z_span": z_span}


def _named_face_requirement_plane_groups(requirement_lower: str) -> set[str]:
    lowered = str(requirement_lower or "").lower()
    groups: set[str] = set()
    if any(
        token in lowered
        for token in (
            "top face",
            "top-face",
            "bottom face",
            "bottom-face",
            "mating face",
            "mating faces",
            "mating surface",
            "mating surfaces",
        )
    ):
        groups.add("top_bottom")
    if any(token in lowered for token in ("front face", "front-face", "back face", "back-face")):
        groups.add("front_back")
    if any(token in lowered for token in ("left face", "left-face", "right face", "right-face")):
        groups.add("left_right")
    return groups


def _plane_name_for_named_face_group(group: str) -> str:
    return {
        "top_bottom": "XY",
        "front_back": "XZ",
        "left_right": "YZ",
    }.get(group, "")


def _collect_named_plane_aliases(tree: ast.AST) -> dict[str, str]:
    aliases: dict[str, str] = {}
    if not isinstance(tree, ast.Module):
        return aliases
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            plane_name = _named_plane_root_name(node.value, plane_aliases=aliases)
            if not plane_name:
                continue
            for target in node.targets:
                if isinstance(target, ast.Name):
                    aliases[str(target.id or "").strip()] = plane_name
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            plane_name = _named_plane_root_name(node.value, plane_aliases=aliases)
            if plane_name:
                aliases[str(node.target.id or "").strip()] = plane_name
    return aliases


def _named_plane_root_name(
    expr: ast.AST | None,
    *,
    plane_aliases: dict[str, str] | None = None,
) -> str | None:
    if expr is None:
        return None
    if isinstance(expr, ast.Name):
        key = str(expr.id or "").strip()
        if not key:
            return None
        return (plane_aliases or {}).get(key)
    if (
        isinstance(expr, ast.Attribute)
        and isinstance(expr.value, ast.Name)
        and expr.value.id == "Plane"
        and expr.attr in {"XY", "XZ", "YZ"}
    ):
        return str(expr.attr)
    if isinstance(expr, ast.Call) and isinstance(expr.func, ast.Attribute):
        method_name = str(expr.func.attr or "").strip()
        if method_name in {"offset", "move", "shift_origin", "rotated", "rotate", "moved", "located"}:
            return _named_plane_root_name(expr.func.value, plane_aliases=plane_aliases)
    return None


def _is_plain_named_plane_expr(
    expr: ast.AST | None,
    *,
    plane_aliases: dict[str, str] | None = None,
) -> bool:
    if expr is None:
        return False
    if isinstance(expr, ast.Name):
        key = str(expr.id or "").strip()
        return bool(key and key in (plane_aliases or {}))
    return (
        isinstance(expr, ast.Attribute)
        and isinstance(expr.value, ast.Name)
        and expr.value.id == "Plane"
        and expr.attr in {"XY", "XZ", "YZ"}
    )


def _directional_drill_workplane_with_in_plane_anchor(
    requirement_lower: str,
) -> str | None:
    if not requirement_lower:
        return None
    mentions_x = "x =" in requirement_lower or " at x" in requirement_lower
    mentions_y = "y =" in requirement_lower or " at y" in requirement_lower
    mentions_z = "z =" in requirement_lower or " at z" in requirement_lower
    if (
        any(
            phrase in requirement_lower
            for phrase in (
                "in the y direction",
                "along the y direction",
                "drill in the y direction",
                "drill through the lugs in the y direction",
            )
        )
        and mentions_x
        and mentions_z
    ):
        return "XZ"
    if (
        any(
            phrase in requirement_lower
            for phrase in (
                "in the x direction",
                "along the x direction",
                "drill in the x direction",
            )
        )
        and mentions_y
        and mentions_z
    ):
        return "YZ"
    return None


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


def _call_uses_mode_private(node: ast.Call) -> bool:
    for keyword in node.keywords:
        if str(getattr(keyword, "arg", "") or "").strip() != "mode":
            continue
        value = keyword.value
        if (
            isinstance(value, ast.Attribute)
            and value.attr == "PRIVATE"
            and isinstance(value.value, ast.Name)
            and value.value.id == "Mode"
        ):
            return True
    return False


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


def _looks_like_vector_tuple(node: ast.AST) -> bool:
    if not isinstance(node, (ast.Tuple, ast.List)):
        return False
    if len(node.elts) not in {2, 3}:
        return False
    for element in node.elts:
        if _looks_like_scalar_coordinate_expr(element):
            continue
        return False
    return True


def _looks_like_scalar_coordinate_expr(node: ast.AST) -> bool:
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return True
    if (
        isinstance(node, ast.UnaryOp)
        and isinstance(node.op, (ast.UAdd, ast.USub))
        and _looks_like_scalar_coordinate_expr(node.operand)
    ):
        return True
    if isinstance(node, ast.Name):
        return True
    if isinstance(node, ast.Attribute):
        return True
    if isinstance(node, ast.Subscript):
        return True
    if isinstance(node, ast.BinOp) and isinstance(
        node.op,
        (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod, ast.Pow),
    ):
        return _looks_like_scalar_coordinate_expr(
            node.left
        ) and _looks_like_scalar_coordinate_expr(node.right)
    return False


def _looks_like_xyz_coordinate_tuple(node: ast.AST) -> bool:
    return isinstance(node, (ast.Tuple, ast.List)) and len(node.elts) == 3


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


def _candidate_lint_family_ids(
    *,
    requirement_text: str,
    run_state: RunState | None,
) -> list[str]:
    families: list[str] = []
    lowered_requirement = requirement_text.lower()
    graph = getattr(run_state, "feature_graph", None)
    feature_instances = getattr(graph, "feature_instances", None)
    if isinstance(feature_instances, dict):
        for feature_instance in feature_instances.values():
            family_id = str(getattr(feature_instance, "family_id", "") or "").strip()
            if family_id and family_id not in families and family_id != "general_geometry":
                families.append(family_id)
    for inferred_family_id in infer_requirement_probe_families(
        requirement_text=requirement_text
    ):
        if inferred_family_id == "nested_hollow_section" and not any(
            token in lowered_requirement
            for token in (
                "shell",
                "shelled",
                "hollow enclosure",
                "enclosure",
                "housing",
                "casing",
                "clamshell",
                "lid",
                "base",
            )
        ):
            continue
        if inferred_family_id and inferred_family_id not in families and inferred_family_id != "general_geometry":
            families.append(inferred_family_id)
    if (
        "sweep" in lowered_requirement
        and any(
            token in lowered_requirement
            for token in (
                "path",
                "rail",
                "profile sketch",
                "concentric",
                "reference plane",
                "tangent arc",
            )
        )
        and "path_sweep" not in families
    ):
        families.append("path_sweep")
    if (
        any(token in lowered_requirement for token in ("countersink", "countersunk"))
        and "explicit_anchor_hole" not in families
    ):
        families.append("explicit_anchor_hole")
    if (
        "explicit_anchor_hole" not in families
        and _requirement_mentions_explicit_hole_anchors(lowered_requirement)
    ):
        families.append("explicit_anchor_hole")
    if (
        "four point" in lowered_requirement
        or "four points" in lowered_requirement
        or ("four" in lowered_requirement and "hole" in lowered_requirement)
    ):
        if "pattern_distribution" not in families:
            families.append("pattern_distribution")
    if _requirement_mentions_explicit_cylindrical_slot(lowered_requirement):
        if "slots" not in families:
            families.append("slots")
    if (
        "annular groove" in lowered_requirement
        or ("groove" in lowered_requirement and "revol" in lowered_requirement)
    ):
        if "annular_groove" not in families:
            families.append("annular_groove")
        if "axisymmetric_profile" not in families:
            families.append("axisymmetric_profile")
    if (
        any(
            token in lowered_requirement
            for token in (
                "hemisphere",
                "hemispherical",
                "spherical recess",
                "spherical cavity",
                "spherical depression",
            )
        )
        or ("sphere" in lowered_requirement and "recess" in lowered_requirement)
        and "spherical_recess" not in families
    ):
        families.append("spherical_recess")
    if (
        any(token in lowered_requirement for token in ("recess", "pocket", "groove"))
        and any(
            token in lowered_requirement
            for token in (
                "top face",
                "top-face",
                "bottom face",
                "bottom-face",
                "front face",
                "front-face",
                "back face",
                "back-face",
                "left face",
                "left-face",
                "right face",
                "right-face",
            )
        )
        and "named_face_local_edit" not in families
    ):
        families.append("named_face_local_edit")
    if (
        any(token in lowered_requirement for token in ("shell", "shelled", "hollow enclosure"))
        and "nested_hollow_section" not in families
    ):
        families.append("nested_hollow_section")
    if _requirement_mentions_half_shell_with_split_surface(lowered_requirement):
        if "axisymmetric_profile" not in families:
            families.append("axisymmetric_profile")
    if (
        any(token in lowered_requirement for token in ("pattern", "quantity", "spacing"))
        and "pattern_distribution" not in families
    ):
        if "pattern_distribution" not in families:
            families.append("pattern_distribution")
    return families


def _requirement_mentions_explicit_hole_anchors(requirement_lower: str) -> bool:
    if not requirement_lower:
        return False
    if "hole" not in requirement_lower:
        return False
    coordinate_tokens = (
        "coordinates (",
        "coordinate (",
        "centered at x",
        "centered at y",
        "centered at z",
        "at x =",
        "at y =",
        "at z =",
        "x =",
        "y =",
        "z =",
    )
    if any(token in requirement_lower for token in coordinate_tokens):
        return True
    return bool(
        re.search(
            r"\(\s*-?[0-9]+(?:\.[0-9]+)?\s*,\s*-?[0-9]+(?:\.[0-9]+)?(?:\s*,\s*-?[0-9]+(?:\.[0-9]+)?)?\s*\)",
            requirement_lower,
        )
    )


def _build_preflight_repair_recipe(
    *,
    family_ids: list[str],
    lint_hits: list[dict[str, Any]],
    requirement_text: str = "",
) -> dict[str, Any]:
    requirement_lower = requirement_text.lower()
    living_hinge_requested = (
        "living hinge" in requirement_lower or "living-hinge" in requirement_lower
    )
    plain_pin_or_mechanical_hinge_requested = any(
        token in requirement_lower
        for token in (
            "pin hinge",
            "mechanical hinge",
        )
    )
    detached_hinge_requested = any(
        token in requirement_lower
        for token in (
            "removable pin",
            "removable hinge pin",
            "detachable pin",
            "exposed hinge",
            "exposed hinge assembly",
            "external hinge assembly",
            "hinge assembly",
            "hinge barrel",
            "hinge barrels",
            "hinge pin",
            "hinge pins",
            "detached hinge",
            "detached hinge hardware",
            "separate hinge part",
            "separate hinge parts",
        )
    )

    def _explicit_cylindrical_slot_boolean_safe_recipe() -> dict[str, Any]:
        return {
            "recipe_id": "explicit_cylindrical_slot_boolean_safe_recipe",
            "recipe_summary": (
                "For an explicit cutting-cylinder slot, keep the host builder authoritative. "
                "Prefer one builder-native subtractive placement on the stable host; if a "
                "detached boolean is still required, close the host builder first, then build "
                "one literal Cylinder cutter, orient it with Rot(...), place it with Pos(...), "
                "and subtract it from the detached host solid."
            ),
            "recipe_skeleton": {
                "mode": "whole_part_rebuild_via_execute_build123d",
                "steps": [
                    "with BuildPart() as host: build the target body first and keep the active host authoritative",
                    "if the slot can stay builder-native, place the literal Cylinder cutter with `mode=Mode.SUBTRACT` and explicit `Locations(...)` on that host",
                    "if detached solid arithmetic is still required, close the host builder first before creating or positioning the cutter",
                    "create the cutter as `cutter = Cylinder(radius=..., height=..., align=(Align.CENTER, Align.CENTER, Align.CENTER))` without `axis=` or `length=`",
                    "orient the detached cutter with `Rot(...)` and place it with `Pos(...)` or `Locations(...)`",
                    "compute the final detached boolean only after the host builder closes, for example `result = host.part - cutter`",
                ],
            },
        }

    def _nested_hollow_section_same_builder_subtract_recipe() -> dict[str, Any]:
        return {
            "recipe_id": "nested_hollow_section_same_builder_subtract_contract",
            "recipe_summary": (
                "For hollow enclosures, shells, lids, and bases, keep the host builder "
                "authoritative. Realize cavities, slots, notches, and side pockets with "
                "same-builder subtractive geometry when possible, and only fall back to "
                "detached booleans after the host builder closes. Do not mutate `host.part` "
                "inside the active builder or reuse temporary primitive staging solids as CSG."
            ),
            "recipe_skeleton": {
                "mode": "whole_part_rebuild_via_execute_build123d",
                "steps": [
                    "open one active `BuildPart` for the host shell/body and build the outer envelope first",
                    "keep cavity, notch, slot, and pocket edits builder-native while that host builder is open; do not assign into `host.part`, `base.part`, or `lid.part` there",
                    "if a local cut uses sketch primitives such as Rectangle, SlotOverall, Circle, or Ellipse, open `BuildSketch(target_plane)` first, realize the 2D profile there, and only then extrude/cut against the host",
                    "if a cut truly needs detached solid arithmetic, close the host builder first, then create and place the detached cutter outside it before one explicit `result = host.part - cutter` boolean",
                    "if the requirement names separate physical parts such as lid/base or body/cover, realize each physical part in its own closed `BuildPart` before combining detached results",
                    "only after the shell/body/lid/base envelope is stable should magnets, thumb notches, hinge features, posts, or side pockets be added",
                ],
            },
        }

    def _clamshell_host_local_cut_recipe() -> dict[str, Any]:
        hinge_summary = (
            "keep the hinge host-owned as an integrated living hinge instead of detached hardware."
            if living_hinge_requested and not detached_hinge_requested
            else (
                "only realize detached hinge hardware when the requirement explicitly asks for a removable pin, separate hinge parts, or an exposed hinge assembly."
                if detached_hinge_requested
                else "treat a plain pin/mechanical hinge as host-owned lid/base geometry unless the prompt explicitly asks for detachable hinge hardware."
            )
        )
        return {
            "recipe_id": "clamshell_host_local_cut_contract",
            "recipe_summary": (
                "For clamshell lid/base shells, keep each shell host authoritative, "
                "finish host-owned local cuts before that shell closes, and "
                f"{hinge_summary}"
            ),
            "recipe_skeleton": {
                "mode": "whole_part_rebuild_via_execute_build123d",
                "steps": [
                    "realize the base and lid in one authoritative `BuildPart` per shell host, keeping both parts in the closed assembled envelope instead of reopening the same alias later",
                    "finish host-owned local cuts such as magnet recesses, thumb notches, slots, and pockets inside that shell builder before that shell builder closes",
                    (
                        "if the requirement says `living hinge`, keep that hinge as an integrated host-owned thin back-edge strip or flexure between lid and base and preserve the default two physical parts; do not create detached `hinge_barrel` or `hinge_pin` solids unless the prompt explicitly switches to pin/mechanical/removable hinge hardware"
                        if living_hinge_requested and not detached_hinge_requested
                        else (
                            "if the requirement explicitly requests detachable hinge hardware such as a removable pin, separate hinge parts, or an exposed hinge assembly, detached hinge barrels or hinge pins are allowed, but keep the default physical-part target at lid/base only and avoid inventing extra hinge solids beyond the requested hardware"
                            if detached_hinge_requested
                            else "a plain `pin hinge` or `mechanical hinge` still defaults to a two-part lid/base target: keep the hinge knuckles/barrels host-owned on lid/base and only detach the pin/hardware when the prompt explicitly asks for a removable pin, separate hinge parts, or an exposed hinge assembly"
                        )
                    ),
                    (
                        "plain `pin hinge` or `mechanical hinge` still defaults to a two-part lid/base target: keep the hinge knuckles/barrels host-owned on lid/base and only detach the pin/hardware when the prompt explicitly asks for a removable pin, separate hinge parts, or an exposed hinge assembly"
                        if plain_pin_or_mechanical_hinge_requested and not detached_hinge_requested
                        else "only use detached hinge hardware when the prompt explicitly names detachable hinge hardware such as a removable pin, separate hinge parts, or an exposed hinge assembly"
                    ),
                    "Build123d `extrude(amount=h)` grows one-sided from the active sketch plane; it does not automatically create a centered `[-h/2, +h/2]` shell interval around that plane.",
                    "For centered lid/base intervals, sketch on the real start face plane or translate the finished solid afterward; do not assume `Locations((0, 0, center_z))` plus `extrude(amount=h)` creates a centered shell interval by itself.",
                    "for a living hinge, the back-edge seam coordinate belongs to the hinge strip itself, not to the whole shell envelope; do not translate the whole lid or base to the back seam coordinate just to make the hinge touch",
                    "do not drop an unrotated default `Cylinder(...)` directly onto `(x, hinge_y, split_z)` or `(x, -depth/2, z)` inside lid/base builders and assume it became an X-axis hinge barrel or pin; without a supported rotation/orientation lane that cylinder still runs along Z",
                    "for front/back clamshell-local edits such as a thumb notch, front label recess, or mating-face pocket, treat the host as Y-normal and start from `Plane.XZ.offset(±depth/2)` or `Plane(face)` instead of `Plane.XY`/`Plane.YZ` plus guessed 3D placement",
                    "for top/bottom mating-face edits, keep the host plane in the XY family at the real face datum such as `Plane.XY.offset(z_face)` and place only in-plane `(x, y)` coordinates locally",
                    "if a local cut uses sketch primitives such as `SlotOverall(...)` or `Rectangle(...)`, open `BuildSketch(target_plane)` on the intended host plane first, then extrude/subtract from that same shell host",
                    "if a thumb notch or front label recess is externalized as a detached cutter such as `notch_cutter` or `label_recess`, that detached builder must stay positive or `mode=Mode.PRIVATE`; do not write `with BuildPart() as notch_cutter:` followed by `extrude(..., mode=Mode.SUBTRACT)` because a detached cutter builder has no host yet",
                    "do not try to rescue a wrong host plane by nesting `BuildSketch(Plane.XY)` or `BuildSketch(Plane.YZ)` inside `Locations((x, y, z))`, `shift_origin(...)`, or extra rotations when the requested host is front/back",
                    "do not reopen `with BuildPart() as lid:` or `with BuildPart() as base:` later just to start detached subtractive mini-builders for late local cuts",
                    "only when the prompt explicitly requests detached back-edge hinge barrels or pins should you separate the hinge seam location from the hinge axis direction: the seam still sits at `y = ±depth/2`, while the cylinder axis is chosen separately by transform; do not reinterpret the back-edge hinge seam as a `Plane.YZ` sketch family just because the hinge sits at the back edge",
                    "when detached hinge hardware is explicitly requested, keep hinge barrels, hinge pins, and other rotated hardware as detached separate positive solids after the shell hosts close, then assemble those detached solids in the shared closed pose",
                    "a safe detached hinge lane is `Pos(0, ±depth/2, split_z) * (Rot(Y=90) * hinge_barrel.part)` after the hinge builder closes, keeping the Y seam coordinate explicit instead of rebuilding the hinge on `Plane.YZ`",
                    "choose one axis-orientation lane for a detached hinge cylinder: either create it with one supported primitive rotation lane, or build it unrotated and orient the closed solid afterward, but do not stack `Cylinder(..., rotation=...)` and a second `Rot(...) * hinge_barrel.part` just to realize one hinge axis",
                    "if a detached boolean is still required for one local cutter, build that cutter as a positive/private solid after the shell hosts close and do one explicit final boolean outside the active host builders",
                ],
            },
        }

    lint_ids = {
        str(item.get("rule_id") or "").strip()
        for item in lint_hits
        if isinstance(item, dict)
    }
    family_id_set = {str(item).strip() for item in family_ids if str(item).strip()}
    clamshell_half_shell_context = (
        "half_shell" in family_id_set and "nested_hollow_section" in family_id_set
    )
    if not lint_ids:
        return {}
    if (
        "nested_hollow_section" in family_id_set
        and "slots" in family_id_set
        and lint_ids.intersection(
            {
                "invalid_build123d_contract.active_builder_part_mutation",
                "invalid_build123d_contract.active_builder_temporary_primitive_arithmetic",
            }
        )
    ):
        if clamshell_half_shell_context:
            return _clamshell_host_local_cut_recipe()
        return _nested_hollow_section_same_builder_subtract_recipe()
    if (
        "nested_hollow_section" in family_id_set
        and lint_ids.intersection(
            {
                "invalid_build123d_context.nested_subtractive_buildpart_inside_active_builder",
                "invalid_build123d_api.nested_buildpart_cutter_part_arithmetic",
                "invalid_build123d_api.nested_buildpart_part_transform",
            }
        )
    ):
        if clamshell_half_shell_context:
            return _clamshell_host_local_cut_recipe()
        return _nested_hollow_section_same_builder_subtract_recipe()
    if (
        clamshell_half_shell_context
        and "invalid_build123d_contract.named_face_plane_family_mismatch" in lint_ids
    ):
        return _clamshell_host_local_cut_recipe()
    if "slots" in family_ids and lint_ids.intersection(
        {
            "invalid_build123d_api.bare_subtract_helper",
            "invalid_build123d_api.bare_rotate_helper",
            "invalid_build123d_keyword.cylinder_axis",
            "invalid_build123d_keyword.cylinder_length_alias",
            "invalid_build123d_contract.active_builder_cutter_primitive_boolean",
        }
    ):
        return _explicit_cylindrical_slot_boolean_safe_recipe()
    if (
        "spherical_recess" in family_ids
        and "pattern_distribution" in family_ids
        and "invalid_build123d_api.buildpart_solid_method_arithmetic" in lint_ids
    ):
        return {
            "recipe_id": "spherical_recess_pattern_builder_subtract_recipe",
            "recipe_summary": (
                "For repeated hemispherical recesses, keep the host in one BuildPart, compute the "
                "centered pattern offsets, and subtract the recess bodies with builder-native "
                "`mode=Mode.SUBTRACT` placements instead of mutating `part.solid`."
            ),
            "recipe_skeleton": {
                "mode": "whole_part_rebuild_via_execute_build123d",
                "steps": [
                    "with BuildPart() as part: build the base body first",
                    "compute the centered pattern offsets explicitly from the requested spacing/count",
                    "with Locations((x, y, top_z), ...): Sphere(radius=..., mode=Mode.SUBTRACT)",
                    "result = part.part",
                ],
            },
        }
    if (
        "explicit_anchor_hole" in family_ids
        and "pattern_distribution" in family_ids
        and "invalid_build123d_contract.active_builder_temporary_primitive_arithmetic"
        in lint_ids
    ):
        return {
            "recipe_id": "explicit_anchor_hole_same_builder_subtract_recipe",
            "recipe_summary": (
                "For repeated countersunk hole layouts, keep the host in one BuildPart, "
                "convert explicit point coordinates into the correct host-face frame, and "
                "realize the hole/countersink cutters through one supported subtractive "
                "pattern instead of nesting BuildPart cutters or reusing temporary "
                "primitive staging solids for later `part.part` arithmetic."
            ),
            "recipe_skeleton": {
                "mode": "subtree_rebuild_via_execute_build123d",
                "steps": [
                    "with BuildPart() as part: build the host body first",
                    "compute the full hole center set in the host-face coordinate frame before cutting",
                    "for explicit planar countersink arrays where the requirement already gives the through-hole diameter, head diameter, and cone angle, prefer one `CounterSinkHole(...)` pass first with the exact helper contract and explicit host-face placement",
                    "Only fall back to an explicit same-builder cylinder+cone or revolved countersink recipe when the helper contract cannot express the host/placement semantics cleanly or when prior validation/evaluation evidence shows the helper result is dimensionally wrong for that family",
                    "either keep the cutters in the same active BuildPart with explicit subtractive placement, or close the host builder and subtract fully positioned cutters with `result = host.part - cutter`",
                    "do not use nested `with BuildPart() as cutter:` blocks followed by `part.part -= cutter.part` inside the host builder",
                    "do not create `cone = Cone(...)` or `cyl = Cylinder(...)` staging solids inside the active host and reuse them later in explicit boolean arithmetic unless they were created as `mode=Mode.PRIVATE`",
                ],
            },
        }
    if (
        "explicit_anchor_hole" in family_ids
        and lint_ids.intersection(
            {
                "invalid_build123d_api.nested_buildpart_cutter_part_arithmetic",
                "invalid_build123d_context.nested_subtractive_buildpart_inside_active_builder",
                "invalid_build123d_contract.explicit_anchor_manual_cutter_requires_subtract_mode",
            }
        )
    ):
        return {
            "recipe_id": "explicit_anchor_hole_same_builder_subtract_recipe",
            "recipe_summary": (
                "For repeated countersunk hole layouts, keep the host in one BuildPart, "
                "convert explicit point coordinates into the correct host-face frame, and "
                "realize the hole/countersink cutters through one supported subtractive "
                "pattern instead of nesting BuildPart cutters or reusing temporary "
                "primitive staging solids for later `part.part` arithmetic."
            ),
            "recipe_skeleton": {
                "mode": "subtree_rebuild_via_execute_build123d",
                "steps": [
                    "with BuildPart() as part: build the host body first",
                    "compute the full hole center set in the host-face coordinate frame before cutting",
                    "for explicit planar countersink arrays where the requirement already gives the through-hole diameter, head diameter, and cone angle, prefer one `CounterSinkHole(...)` pass first with the exact helper contract and explicit host-face placement",
                    "Only fall back to an explicit same-builder cylinder+cone or revolved countersink recipe when the helper contract cannot express the host/placement semantics cleanly or when prior validation/evaluation evidence shows the helper result is dimensionally wrong for that family",
                    "either keep the cutters in the same active BuildPart with explicit subtractive placement, or close the host builder and subtract fully positioned cutters with `result = host.part - cutter`",
                    "do not use nested `with BuildPart() as cutter:` blocks followed by `part.part -= cutter.part` inside the host builder",
                    "do not create `cone = Cone(...)` or `cyl = Cylinder(...)` staging solids inside the active host and reuse them later in explicit boolean arithmetic unless they were created as `mode=Mode.PRIVATE`",
                ],
            },
        }
    if (
        "explicit_anchor_hole" in family_ids
        and lint_ids.intersection(
            {
                "invalid_build123d_keyword.cylinder_axis",
                "invalid_build123d_keyword.cylinder_taper",
                "invalid_build123d_keyword.cylinder_length_alias",
            }
        )
    ):
        return {
            "recipe_id": "explicit_anchor_directional_hole_cylinder_contract",
            "recipe_summary": (
                "For explicit directional through-holes, keep the host body authoritative, "
                "place the hole centers with literal local anchors, build a plain Cylinder "
                "cutter without `axis=`, and orient it with `Rot(...)` plus explicit "
                "placement instead of guessing unsupported cylinder keywords."
            ),
            "recipe_skeleton": {
                "mode": "subtree_rebuild_via_execute_build123d",
                "steps": [
                    "with BuildPart() as part: build the host solid and any shell/pad geometry first",
                    "keep the requested hole centers literal in the target local frame, for example explicit `(x, y, z)` anchors or a face-local workplane placement",
                    "create a plain cutter such as `cutter = Cylinder(radius=..., height=..., align=(Align.CENTER, Align.CENTER, Align.CENTER))` without an `axis=` keyword",
                    "orient the cutter with `Rot(...)`, for example `Rot(Y=90) * cutter` for a Y-direction drill, then place it with `Pos(...)` or a non-origin `Locations(...)` anchor",
                    "either subtract inside the active builder with a verified subtractive path, or close the host builder and do one explicit boolean such as `result = part.part - cutter`",
                    "before finishing, verify that the realized centers still match the requested anchor coordinates on the actual host geometry",
                ],
            },
        }
    if (
        ("annular_groove" in family_ids or "axisymmetric_profile" in family_ids)
        and lint_ids.intersection(
            {
                "invalid_build123d_api.nested_buildpart_cutter_part_arithmetic",
                "invalid_build123d_api.ring_helper_name",
            }
        )
    ):
        return {
            "recipe_id": "annular_groove_same_builder_band_subtract_recipe",
            "recipe_summary": (
                "For annular grooves on a code-first Build123d path, keep the host geometry "
                "authoritative and realize the groove band through one same-builder subtractive "
                "pattern or one post-host boolean, not a nested BuildPart cutter inside the host."
            ),
            "recipe_skeleton": {
                "mode": "whole_part_rebuild_via_execute_build123d",
                "steps": [
                    "with BuildPart() as part: build the base solid first and keep its outer envelope authoritative",
                    "derive the groove outer_radius, inner_radius, and axial window directly from the requirement",
                    "either keep the annular groove subtraction in the same active `BuildPart` with builder-native subtractive geometry, or close the host and subtract the annular groove band once",
                    "do not guess `Ring(...)`; realize the band as an outer coaxial solid/profile minus the inner coaxial solid/profile",
                    "do not use `with BuildPart() as groove_band:` inside the host builder followed by `part.part -= groove_band.part`",
                ],
            },
        }
    if (
        "nested_hollow_section" in family_ids
        and "axisymmetric_profile" in family_ids
        and "invalid_build123d_contract.active_builder_temporary_primitive_arithmetic"
        in lint_ids
    ):
        return {
            "recipe_id": "half_shell_semi_profile_extrude_contract",
            "recipe_summary": (
                "For half-shell or split-shell hosts, do not stage full cylinders and trim "
                "them inside an active BuildPart. Build one closed semi-profile on the named "
                "plane, extrude it once for the host envelope, then add pads and explicit "
                "hole cutters after the host geometry is stable."
            ),
            "recipe_skeleton": {
                "mode": "whole_part_rebuild_via_execute_build123d",
                "steps": [
                    "open `with BuildSketch(named_plane):` on the requirement plane and build the shell cross-section there first",
                    "inside `BuildLine`, draw the outer semicircle and inner semicircle, then close the split side with explicit `Line(...)` segments so the semi-annulus becomes one closed face",
                    "call `make_face()` and `extrude(amount=...)` once to create the half-shell host, preserving the named-plane lower bound and the one-sided split envelope",
                    "realize any bottom pad or lug body as a separate additive host step after the shell profile is valid, not by trimming temporary cylinders inside the same active builder",
                    "if an inner clearance or drill cutter still needs explicit solid arithmetic, close the host builder first and subtract that external cutter from `host.part` afterward",
                    "only after the host shell and pad are stable should directional holes be placed with literal anchors and rotated cutters",
                ],
            },
        }
    if clamshell_half_shell_context and lint_ids.intersection(
        {
            "invalid_build123d_contract.clamshell_hinge_unrotated_default_cylinder",
            "invalid_build123d_contract.detached_subtractive_builder_without_host",
            "invalid_build123d_contract.active_builder_temporary_primitive_arithmetic",
            "invalid_build123d_contract.active_builder_temporary_primitive_transform_rebind",
            "invalid_build123d_contract.active_builder_part_mutation",
            "invalid_build123d_context.sketch_primitive_requires_buildsketch",
            "invalid_build123d_context.transform_context_manager",
        }
    ):
        return _clamshell_host_local_cut_recipe()
    if "invalid_build123d_contract.active_builder_part_mutation" in lint_ids:
        return {
            "recipe_id": "active_builder_part_mutation_contract",
            "recipe_summary": (
                "Do not mutate `host.part` while the host BuildPart is still open. Keep the "
                "active builder authoritative for adds/cuts, or close it first before detached "
                "boolean arithmetic."
            ),
            "recipe_skeleton": {
                "mode": "whole_part_rebuild_via_execute_build123d",
                "steps": [
                    "inside `with BuildPart() as host:`, do not write `host.part = ...`, `host.part += ...`, or `host.part -= ...`",
                    "express bosses, pockets, shells, notches, magnet recesses, and similar edits as builder-native primitives with `mode=Mode.ADD` / `mode=Mode.SUBTRACT` plus explicit `Locations(...)` placement",
                    "if a feature truly needs detached solid arithmetic, close the host builder first and then compute `result = host.part +/- detached_feature` outside the active builder",
                    "only after the detached boolean is complete should the final geometry be assigned to `result`",
                ],
            },
        }
    if "invalid_build123d_contract.detached_subtractive_builder_without_host" in lint_ids:
        return {
            "recipe_id": "detached_subtractive_builder_without_host_contract",
            "recipe_summary": (
                "Do not start a detached BuildPart with a subtractive operation when no host "
                "solid exists yet. Keep the cut inside the authoritative host builder, or build "
                "a positive/private cutter first and subtract it only after the host closes."
            ),
            "recipe_skeleton": {
                "mode": "whole_part_rebuild_via_execute_build123d",
                "steps": [
                    "do not open a standalone `BuildPart` whose first materializing operation is `mode=Mode.SUBTRACT`, `Hole(...)`, `CounterBoreHole(...)`, or `CounterSinkHole(...)`",
                    "if the feature belongs to an existing host body, keep that subtractive operation inside the authoritative host builder with explicit `Locations(...)`, target plane placement, or a topology-targeted local edit",
                    "if a detached cutter is truly required, create it as a positive or `mode=Mode.PRIVATE` solid first, close that builder, then do one explicit boolean such as `result = host.part - cutter.part` outside the active host builder",
                    "for repeated magnet recesses, thumb notches, pockets, and similar enclosure cuts, prefer repeated same-host subtractive placements instead of detached subtractive mini-builders",
                ],
            },
        }
    if "invalid_build123d_api.nested_buildpart_part_transform" in lint_ids:
        return {
            "recipe_id": "nested_buildpart_part_transform_contract",
            "recipe_summary": (
                "Do not transform `nested_builder.part` as though it were a stable detached "
                "solid while an outer BuildPart host is still active. Keep the host builder "
                "authoritative, or close the host first before transforming detached solids."
            ),
            "recipe_skeleton": {
                "mode": "whole_part_rebuild_via_execute_build123d",
                "steps": [
                    "if the nested builder is only a local cutter or pocket feature, do not open a second BuildPart just to move its `.part`; keep the cut builder-native on the authoritative host with `Locations(...)` and subtractive mode",
                    "if a truly detached local feature is required, close the outer host builder first and only then transform the detached solid with `Pos(...)`, `Rot(...)`, or `Location(...)` outside the active host",
                    "do not call `nested_builder.part.move(...)`, `.rotate(...)`, `.located(...)`, or similar transform methods while the outer host BuildPart is still open",
                    "after the detached transform is complete, do one explicit boolean such as `result = host.part - cutter` outside the active builder",
                ],
            },
        }
    if "invalid_build123d_contract.plane_tuple_multiplication" in lint_ids:
        return {
            "recipe_id": "build123d_plane_tuple_multiplication_contract",
            "recipe_summary": (
                "Do not treat `Plane.XY/XZ/YZ` like a tuple-transform surface. Use translated "
                "planes for sketch/workplane placement and `Locations((x, y, z))` for point "
                "placement."
            ),
            "recipe_skeleton": {
                "mode": "subtree_rebuild_via_execute_build123d",
                "steps": [
                    "when the feature is defined by a sketch/workplane, translate the named plane with its normal-aware offset, for example `Plane.XY.offset(z0)`, `Plane.XZ.offset(y0)`, or `Plane.YZ.offset(x0)`",
                    "when the feature is defined by an explicit point placement, keep the workplane unchanged and place the operation with `Locations((x, y, z))` instead of multiplying a plane by a raw tuple",
                    "if both a translated workplane and an explicit local point are needed, build the shifted plane first and then apply a local `Locations((u, v))` placement on that plane",
                    "do not write `Plane.XY * (x, y, z)`, `Plane.XZ * (x, z, y)`, or similar tuple multiplication forms",
                ],
            },
        }
    if lint_ids.intersection(
        {
            "invalid_build123d_api.loc_helper_name",
            "invalid_build123d_api.bare_move_helper",
        }
    ) and not lint_ids.intersection(
        {
            "invalid_build123d_contract.active_builder_temporary_primitive_arithmetic",
            "invalid_build123d_contract.active_builder_temporary_primitive_transform_rebind",
        }
    ):
        return {
            "recipe_id": "build123d_location_helper_contract",
            "recipe_summary": (
                "Use supported Build123d placement primitives: `Location(...)` for a location "
                "object, or `Pos(...)` / `Rot(...)` for pure transforms. Do not invent `Loc(...)`."
            ),
            "recipe_skeleton": {
                "mode": "subtree_rebuild_via_execute_build123d",
                "steps": [
                    "replace every `Loc(...)` call with the supported placement surface that matches the intent",
                    "for an explicit location object, use `Location(...)` with the supported constructor shape",
                    "for a pure translation, use `Pos(x, y, z)`; for a pure rotation, use `Rot(...)`",
                    "if you are moving a detached solid, apply the supported transform or location object directly and keep the final geometry assignment explicit",
                ],
            },
        }
    if "invalid_build123d_api.scale_helper_case" in lint_ids:
        return {
            "recipe_id": "build123d_scale_helper_contract",
            "recipe_summary": (
                "Use lowercase `scale(...)` on a detached shape with the explicit `by=` argument; "
                "do not invent a capitalized `Scale.by(...)` / `Scale(...)` helper."
            ),
            "recipe_skeleton": {
                "mode": "subtree_rebuild_via_execute_build123d",
                "steps": [
                    "keep the source solid detached and explicitly named before scaling it",
                    "replace `Scale.by((sx, sy, sz)) * shape` with `scale(shape, by=(sx, sy, sz))`",
                    "if the scaled shape still needs placement, apply `Pos(...)`, `Rot(...)`, or `Location(...)` after the supported lowercase `scale(...)` call",
                    "assign the scaled detached result explicitly instead of mutating an active builder host through an invented scaling helper",
                ],
            },
        }
    if "invalid_build123d_api.split_helper_case" in lint_ids:
        return {
            "recipe_id": "build123d_split_function_contract",
            "recipe_summary": (
                "For clamshell or split-body workflows, use lowercase `split(...)` on a finished "
                "host solid and keep lid/base extraction outside the active builder."
            ),
            "recipe_skeleton": {
                "mode": "whole_part_rebuild_via_execute_build123d",
                "steps": [
                    "finish the outer shell/body first and assign the authoritative host solid to a detached variable",
                    "do not call `Split(...)`; use lowercase `split(host_solid, ...)` on the verified Build123d contract",
                    "perform split/lid-base extraction only after the host builder closes, not while the host `BuildPart` is still active",
                    "after the split, derive lid/base solids explicitly and then continue with hinge, notch, magnet, or cavity features on the detached parts",
                ],
            },
        }
    if "invalid_build123d_contract.compound_positional_children_contract" in lint_ids:
        return {
            "recipe_id": "build123d_compound_children_contract",
            "recipe_summary": (
                "Build123d Compound is not a variadic part constructor. Keep detached child "
                "shapes in one iterable or an explicit `children=[...]` payload instead of "
                "passing each child as its own positional argument."
            ),
            "recipe_skeleton": {
                "mode": "subtree_rebuild_via_execute_build123d",
                "steps": [
                    "finish each physical part as its own detached `Part`/`Shape` first",
                    "when returning a multi-part assembly, combine those detached children with `Compound([part_a, part_b, part_c])` or another explicit iterable form",
                    "do not write `Compound(part_a, part_b, part_c)` because only the first positional argument is the shape payload and later positional slots map to metadata like label/color",
                    "if an explicit children-style assembly is clearer, pass `children=[...]` with the intended detached parts instead of overloading positional arguments",
                ],
            },
        }
    if (
        "nested_hollow_section" in family_ids
        and "invalid_build123d_contract.active_builder_temporary_primitive_arithmetic"
        in lint_ids
    ):
        if clamshell_half_shell_context:
            return _clamshell_host_local_cut_recipe()
        return _nested_hollow_section_same_builder_subtract_recipe()
    if "invalid_build123d_contract.active_builder_temporary_primitive_arithmetic" in lint_ids:
        return {
            "recipe_id": "active_builder_temporary_primitive_boolean_contract",
            "recipe_summary": (
                "Temporary Box/Cylinder/Cone/Sphere/Torus values created inside an active "
                "BuildPart are already part of that host, so later boolean/intersection "
                "arithmetic on those staging solids does not behave like isolated CSG."
            ),
            "recipe_skeleton": {
                "mode": "whole_part_rebuild_via_execute_build123d",
                "steps": [
                    "with BuildPart() as host: build only the intended host solids inside the active builder",
                    "do not create temporary primitive staging solids inside that active builder just for later boolean or trim arithmetic",
                    "if the prompt asks for multiple physical parts, do not keep them in one shared active host builder; close each part builder first and combine the detached results afterward",
                    "if the requirement is a split shell or half-profile body, prefer one closed semi-profile and extrude it for the base envelope",
                    "otherwise close the host builder first, then create any temporary solids outside it before doing explicit solid arithmetic such as `result = host.part - cutter` or `result = host.part & trim_box`",
                ],
            },
        }
    if lint_ids.intersection(
        {
            "invalid_build123d_keyword.cylinder_axis",
            "invalid_build123d_keyword.cylinder_length_alias",
        }
    ):
        return {
            "recipe_id": "build123d_cylinder_axis_transform_contract",
            "recipe_summary": (
                "Build123d Cylinder keeps a literal radius/height contract and does not "
                "accept `axis=` or `length=`. Create a plain detached cylinder first, then "
                "orient and place it with Rot/Pos/Locations instead of rebinding an already-"
                "consumed primitive inside an active builder."
            ),
            "recipe_skeleton": {
                "mode": "subtree_rebuild_via_execute_build123d",
                "steps": [
                    "create the primitive as `Cylinder(radius=..., height=...)` or `Cylinder(radius, height)`",
                    "do not pass `axis=` or `length=` into Cylinder",
                    "if the feature must point along X or Y, orient the detached cylinder with `Rot(...)` before the final add/subtract step",
                    "do not create a cylinder inside an active `BuildPart` and then try to relocate it with `solid = Rot(...) * solid` or `solid = Pos(...) * solid` after the builder already consumed that primitive",
                    "place the rotated cutter or feature with `Pos(...)` or `Locations(...)` on the intended host",
                ],
            },
        }
    if "invalid_build123d_contract.active_builder_temporary_primitive_transform_rebind" in lint_ids:
        return {
            "recipe_id": "active_builder_temporary_primitive_transform_contract",
            "recipe_summary": (
                "A primitive created inside an active BuildPart is already part of that host, "
                "so `solid = Pos(...) * solid` or `solid = Rot(...) * solid` only rebinds a "
                "temporary Python value instead of relocating the geometry already added to the "
                "builder."
            ),
            "recipe_skeleton": {
                "mode": "whole_part_rebuild_via_execute_build123d",
                "steps": [
                    "inside an active `BuildPart`, express placement with `Locations(...)`, an explicit sketch/workplane, or another builder-native local frame instead of transforming an already-added primitive variable",
                    "do not rely on `solid = Pos(...) * solid`, `solid = Rot(...) * solid`, or similar transform multiplication to mutate geometry that the active builder already consumed",
                    "if the feature truly needs detached transform-first solid composition, close the host builder first, then create and transform the detached solid outside the active builder before the final boolean/add step",
                ],
            },
        }
    if "invalid_build123d_api.vector_lowercase_component_attribute" in lint_ids:
        return {
            "recipe_id": "build123d_vector_component_attribute_contract",
            "recipe_summary": (
                "Build123d vector and point components use uppercase attributes such as "
                "`.X`, `.Y`, and `.Z`; do not guess lowercase `.x/.y/.z` accessors."
            ),
            "recipe_skeleton": {
                "mode": "local_edit_via_execute_build123d",
                "steps": [
                    "replace lowercase vector component access such as `.z` with the Build123d attribute `.Z`",
                    "if you need numeric indexing, explicitly convert the vector or point to a tuple before subscripting it",
                    "keep the surrounding edge/face filtering logic unchanged unless separate topology evidence says the selector itself is too broad",
                ],
            },
        }
    if "invalid_build123d_api.topology_geometry_attribute" in lint_ids:
        return {
            "recipe_id": "build123d_topology_geometry_attribute_contract",
            "recipe_summary": (
                "Build123d topology entities do not expose a generic `.geometry` attribute; "
                "selection logic must use explicit topology measurements or typed properties."
            ),
            "recipe_skeleton": {
                "mode": "local_edit_via_execute_build123d",
                "steps": [
                    "replace `.geometry` checks on edges/faces/solids with Build123d-native measurements such as `.geom_type`, `.length`, `.radius`, `.bounding_box()`, `.center()`, or another explicit query that matches the selector intent",
                    "keep the selection focused on the intended host subset instead of treating topology entities like generic CAD kernel wrappers",
                    "if the selector still feels broad after removing `.geometry`, narrow it with an explicit edge/face subset rather than retrying the same broad filter",
                ],
            },
        }
    if "invalid_build123d_context.transform_context_manager" in lint_ids:
        return {
            "recipe_id": "build123d_transform_placement_contract",
            "recipe_summary": (
                "Build123d transform helpers such as Rot/Pos/Location are not builder "
                "context managers. Keep placement in builder-native `Locations(...)` "
                "blocks, or build a detached solid first and then transform it with "
                "`Rot(...) * solid` / `Pos(...) * solid` before the final boolean step."
            ),
            "recipe_skeleton": {
                "mode": "whole_part_rebuild_via_execute_build123d",
                "steps": [
                    "inside an active BuildPart, express repeated feature placement with `Locations(...)` rather than `with Rot(...):` or `with Pos(...):` blocks",
                    "if the feature is a subtractive primitive on the current host, keep the cutter primitive directly inside the active builder and orient it with builder-native placement or a detached-transform-first pattern",
                    "if rotation truly needs to happen before the boolean, create the detached solid first, transform it with `Rot(...) * solid` or `Pos(...) * solid`, then add/subtract that transformed solid in an explicit final step",
                    "do not rely on transform helpers as if they opened a temporary local builder scope",
                ],
            },
        }
    if "invalid_build123d_api.bare_shell_helper" in lint_ids:
        preserve_target_face_material = bool(
            {"named_face_local_edit", "explicit_anchor_hole", "pattern_distribution"}
            .intersection(family_ids)
        )
        return {
            "recipe_id": "build123d_shell_offset_contract",
            "recipe_summary": (
                "For shelled bodies, keep the outer host build explicit and realize wall "
                "thickness with Build123d shell/offset semantics or an explicit inner-solid "
                "subtraction, not with a guessed bare `shell(...)` helper."
                + (
                    " When later local edits target a named face, preserve that face as "
                    "material and open the opposite face by default unless the requirement "
                    "explicitly says otherwise."
                    if preserve_target_face_material
                    else ""
                )
            ),
            "recipe_skeleton": {
                "mode": "whole_part_rebuild_via_execute_build123d",
                "steps": [
                    "build the outer host solid first inside BuildPart",
                    "for true shell semantics, use `offset(amount=-wall_thickness, openings=...)` on the host-facing opening set",
                    "if the body is a simple box-like enclosure, subtract an explicitly placed inner solid instead of calling `shell(...)`",
                    *(
                        [
                            "if a later recess, hole set, or reference pattern targets a named face, keep that target face on surviving host material and open the opposite face when the opening face is unspecified"
                        ]
                        if preserve_target_face_material
                        else []
                    ),
                ],
            },
        }
    if "invalid_build123d_keyword.offset_opening_singular" in lint_ids:
        preserve_target_face_material = bool(
            {"named_face_local_edit", "explicit_anchor_hole", "pattern_distribution"}
            .intersection(family_ids)
        )
        return {
            "recipe_id": "build123d_shell_offset_contract",
            "recipe_summary": (
                "Build123d shell offsets use `openings=...` for the opening-face set. "
                "Do not guess a singular `opening=` keyword."
                + (
                    " When later local edits target a named face, preserve that face as "
                    "material and open the opposite face by default unless the requirement "
                    "explicitly says otherwise."
                    if preserve_target_face_material
                    else ""
                )
            ),
            "recipe_skeleton": {
                "mode": "whole_part_rebuild_via_execute_build123d",
                "steps": [
                    "build the outer host solid first inside BuildPart",
                    "when using shell semantics, call `offset(amount=-wall_thickness, openings=...)` with the opening face set",
                    "if the body is simple and the opening face choice is still ambiguous, subtract an explicitly placed inner solid instead",
                    *(
                        [
                            "if a later recess, hole set, or reference pattern targets a named face, keep that target face on surviving host material and open the opposite face when the opening face is unspecified"
                        ]
                        if preserve_target_face_material
                        else []
                    ),
                ],
            },
        }
    if lint_ids.intersection(
        {
            "invalid_build123d_keyword.box_depth_alias",
            "invalid_build123d_keyword.box_radius_alias",
        }
    ):
        return {
            "recipe_id": "build123d_box_keyword_contract",
            "recipe_summary": (
                "When using Build123d Box primitives, stay on the native `length / width / "
                "height` contract and do not use guessed keyword aliases such as `depth=` "
                "or `radius=`."
            ),
            "recipe_skeleton": {
                "mode": "whole_part_rebuild_via_execute_build123d",
                "steps": [
                    "define the three host spans explicitly as length, width, and height",
                    "call `Box(length=..., width=..., height=...)` or `Box(length, width, height)`",
                    "if your variable name is `depth`, pass that variable as the second width dimension instead of `depth=...`",
                    "if the body needs rounded plan corners, use `RectangleRounded(...)` + `extrude(...)` or apply explicit edge fillets after the Box is created",
                ],
            },
        }
    if "invalid_build123d_keyword.regular_polygon_sides_alias" in lint_ids:
        return {
            "recipe_id": "build123d_regular_polygon_keyword_contract",
            "recipe_summary": (
                "When using Build123d `RegularPolygon(...)`, keep the side-count contract "
                "literal with `side_count=` instead of guessed aliases such as `sides=`."
            ),
            "recipe_skeleton": {
                "mode": "local_edit_via_execute_build123d",
                "steps": [
                    "rewrite the polygon call as `RegularPolygon(radius=..., side_count=..., major_radius=True)`",
                    "if the requirement gives side length instead of circumradius, derive the radius first and still pass the polygon count with `side_count=`",
                    "keep same-sketch nested polygon subtraction builder-native with `mode=Mode.SUBTRACT` instead of changing the overall recipe structure just to repair the keyword",
                ],
            },
        }
    if "invalid_build123d_keyword.pos_lowercase_axis_keyword" in lint_ids:
        return {
            "recipe_id": "build123d_pos_keyword_contract",
            "recipe_summary": (
                "When positioning solids in Build123d, use positional `Pos(x, y, z)` "
                "placement instead of lowercase axis keyword guesses such as `Pos(z=...)`."
            ),
            "recipe_skeleton": {
                "mode": "local_edit_via_execute_build123d",
                "steps": [
                    "rewrite the placement with positional arguments such as `Pos(0, 0, z_offset)`",
                    "compose that positional `Pos(...)` with `Rot(...)` or the solid on the correct side of the multiplication",
                    "rerun the same geometry recipe after only the placement expression is repaired",
                ],
            },
        }
    if (
        "invalid_build123d_api.plane_rotated_origin_guess" in lint_ids
        or "invalid_build123d_api.plane_rotate_shape_method_guess" in lint_ids
    ):
        return {
            "recipe_id": "build123d_plane_rotation_contract",
            "recipe_summary": (
                "When orienting Build123d workplanes, treat `Plane.rotated(rotation, "
                "ordering=...)` as an orientation-only operation; it does not accept or "
                "apply a guessed origin tuple."
            ),
            "recipe_skeleton": {
                "mode": "local_edit_via_execute_build123d",
                "steps": [
                    "keep the named workplane when it already has the requested normal, for example use `Plane.XZ` directly for Y-direction drilling",
                    "store in-plane coordinates in the sketch or `Locations(...)` data instead of trying to encode them with a rotated-plane origin guess",
                    "if translation is needed, use `Plane.offset(...)` only along the plane normal or place the cutter/feature with `Pos(...)`",
                    "do not call `.rotate(...)` on Plane objects; use `Plane.rotated((rx, ry, rz), ordering=...)` when orientation must change",
                    "only call `Plane.rotated((rx, ry, rz), ordering=...)` when you truly need a different orientation, and do not pass a second `(x, y, z)` tuple",
                ],
            },
        }
    if (
        "invalid_build123d_api.plane_located_shape_method_guess" in lint_ids
        or "invalid_build123d_api.plane_moved_shape_method_guess" in lint_ids
    ):
        return {
            "recipe_id": "build123d_plane_translation_contract",
            "recipe_summary": (
                "When repositioning a Build123d workplane, use the Plane translation "
                "APIs (`offset`, `move`, `shift_origin`) instead of guessing a "
                "shape-style `.located(...)` or `.moved(...)` method."
            ),
            "recipe_skeleton": {
                "mode": "local_edit_via_execute_build123d",
                "steps": [
                    "keep the named workplane when its orientation is already correct",
                    "translate the workplane with `Plane.offset(amount)` along the plane normal when only a datum shift is needed",
                    "use `Plane.move(Location(...))` or `Plane.shift_origin(...)` when the workplane origin itself must move in a more explicit way",
                    "only call `.located(...)` or `.moved(...)` on actual solids/shapes, not on Plane objects",
                ],
            },
        }
    if "invalid_build123d_contract.face_plane_shift_origin_global_coordinate_guess" in lint_ids:
        return {
            "recipe_id": "build123d_face_plane_shift_origin_contract",
            "recipe_summary": (
                "For face-derived workplanes, do not guess a world-space XYZ tuple inside "
                "`Plane(face).shift_origin(...)`; keep the host plane and place the profile "
                "with local sketch coordinates, or rebuild the plane from the host face's "
                "own origin/normal."
            ),
            "recipe_skeleton": {
                "mode": "local_edit_via_execute_build123d",
                "steps": [
                    "capture the host face once, for example `front_face = part.faces().sort_by(...)[0]`",
                    "either open `with BuildSketch(Plane(front_face)):` directly and draw the local notch/profile with 2D sketch coordinates",
                    "or rebuild the plane from the host face datum, such as `Plane(origin=front_face.center(), z_dir=front_face.normal_at())`, before any further in-plane placement",
                    "avoid passing a guessed global `(x, y, z)` tuple to `shift_origin(...)` unless that point is explicitly guaranteed to lie on the host face plane",
                ],
            },
        }
    if "invalid_build123d_contract.named_face_plane_family_mismatch" in lint_ids and "explicit_anchor_hole" not in family_id_set:
        return {
            "recipe_id": "build123d_named_face_plane_family_contract",
            "recipe_summary": (
                "Named-face local edits must start from the plane family whose normal matches the "
                "requested face instead of sketching on the wrong global plane and hoping later "
                "offsets or rotations recover the host."
            ),
            "recipe_skeleton": {
                "mode": "local_edit_via_execute_build123d",
                "steps": [
                    "map the named face to the matching plane family before any sketch or cutter placement: `top/bottom -> Plane.XY`, `front/back -> Plane.XZ`, `left/right -> Plane.YZ`",
                    "translate that plane only along its own normal axis, or bind directly to `Plane(face)` if topology already selected the host face",
                    "keep the host-face normal authoritative; do not compensate for a wrong plane family with extra `rotated(...)`, `shift_origin(...)`, or guessed world-space offsets",
                    "if the host solid is centered, combine the correct plane family with the correct face datum such as `depth/2`, `width/2`, or `height/2` rather than the full span",
                ],
            },
        }
    if "invalid_build123d_contract.directional_drill_plane_offset_coordinate_mixup" in lint_ids:
        return {
            "recipe_id": "directional_drill_workplane_coordinate_contract",
            "recipe_summary": (
                "For directional drilling, keep the XZ/YZ workplane on the correct "
                "normal-axis datum and put the named hole-center coordinates inside that "
                "workplane instead of encoding an in-plane anchor with `Plane.offset(...)`."
            ),
            "recipe_skeleton": {
                "mode": "local_edit_via_execute_build123d",
                "steps": [
                    "choose the workplane whose normal matches the drill direction, for example `Plane.XZ` for Y-direction holes",
                    "keep the named in-plane coordinates in `Locations((x, z), ...)` or an equivalent local sketch placement",
                    "only use `Plane.offset(...)` for a true translation along the workplane normal axis",
                    "if you need a 3D cutter instead of a sketch, place it explicitly at `(x, normal_axis_value, z)` with `Pos(...)` and orient it with `Rot(...)`",
                ],
            },
        }
    if lint_ids.intersection(
        {
            "invalid_build123d_api.shapelist_filter_by_direction",
            "invalid_build123d_api.edge_is_parallel_axis",
            "invalid_build123d_api.filter_by_position_keyword_band",
            "invalid_build123d_api.filter_by_position_plane_axis",
        }
    ):
        return {
            "recipe_id": "build123d_shapelist_axis_filter_contract",
            "recipe_summary": (
                "When selecting edges or faces by axis direction, use ShapeList "
                "`filter_by(Axis.X/Y/Z)` or an explicit predicate; do not rely on "
                "`filter_by_direction(...)` or `edge.is_parallel(Axis.*)` helpers that "
                "do not exist in Build123d."
            ),
            "recipe_skeleton": {
                "mode": "local_edit_via_execute_build123d",
                "steps": [
                    "extract the target ShapeList, for example `edges = part.edges()`",
                    "use `edges.filter_by(Axis.Y)` for linear edges parallel to the Y axis, or a Python predicate if you need a custom test",
                    "chain `filter_by_position(...)` separately when the selection also depends on a face/edge band such as the bottom Z range",
                    "apply fillet/chamfer/other local edits to the filtered ShapeList",
                ],
            },
        }
    if lint_ids.intersection(
        {
            "invalid_build123d_context.buildpart_topology_access_inside_buildsketch",
        }
    ):
        return {
            "recipe_id": "build123d_buildsketch_builder_boundary_contract",
            "recipe_summary": (
                "Keep `BuildSketch` profile construction separate from enclosing "
                "`BuildPart` topology access; do not use the host part's "
                "edges/faces/vertices as if they were sketch geometry before the solid exists."
            ),
            "recipe_skeleton": {
                "mode": "whole_part_rebuild_via_execute_build123d",
                "steps": [
                    "inside `with BuildSketch(...):`, construct the 2D profile directly with sketch-native geometry instead of calling the enclosing `BuildPart` alias",
                    "if the requirement needs rounded profile corners, encode them in the sketch recipe itself rather than querying `part.vertices()` / `part.edges()` inside the sketch",
                    "only after `extrude(...)`, `revolve(...)`, or another solid-forming step should you select solid edges/faces from the finished part for fillet/chamfer/local edits",
                    "keep the final host solid authoritative and assign it to `result`",
                ],
            },
        }
    if "invalid_build123d_context.sketch_primitive_requires_buildsketch" in lint_ids:
        return {
            "recipe_id": "build123d_sketch_primitive_builder_contract",
            "recipe_summary": (
                "Sketch primitives such as SlotOverall, Rectangle, Circle, Ellipse, and "
                "RegularPolygon belong inside BuildSketch on the intended plane; build the "
                "2D profile there first, then realize material from that sketch."
            ),
            "recipe_skeleton": {
                "mode": "whole_part_rebuild_via_execute_build123d",
                "steps": [
                    "choose the intended profile plane, then open `with BuildSketch(target_plane):`",
                    "move SlotOverall/Rectangle/Circle/Ellipse/RegularPolygon calls into that BuildSketch instead of dropping them directly into BuildPart",
                    "after the sketch profile is complete, use `extrude(...)`, `revolve(...)`, or another supported solid step to add or remove material",
                    "keep host edits builder-native with `mode=Mode.ADD/SUBTRACT`, or close the builder before detached booleans",
                ],
            },
        }
    if lint_ids.intersection(
        {
            "invalid_build123d_contract.member_fillet_radius_keyword_conflict",
            "invalid_build123d_contract.global_fillet_helper_argument_contract",
        }
    ):
        return {
            "recipe_id": "build123d_fillet_member_contract",
            "recipe_summary": (
                "Keep fillet calls on the verified Build123d signatures instead of "
                "mixing host-shape and edge-list arguments across the global helper "
                "and member-style contracts."
            ),
            "recipe_skeleton": {
                "mode": "local_edit_via_execute_build123d",
                "steps": [
                    "prefer the global `fillet(edge_list, radius=...)` helper when you already have a ShapeList selection from the active part",
                    "if you use member-style `solid.fillet(...)`, keep the radius in the method's expected position and pass the selected edge/edge-list on the verified contract instead of `solid.fillet(edge, radius=...)`",
                    "when a broad edge set still fails, retry with a smaller radius or a narrower edge subset rather than reusing the same invalid call shape",
                ],
            },
        }
    if "invalid_build123d_contract.broad_shell_axis_fillet_on_fresh_host" in lint_ids:
        return {
            "recipe_id": "shell_edge_fillet_postpone_contract",
            "recipe_summary": (
                "Delay broad shell-edge fillets until the enclosure/clamshell host is already "
                "valid, or narrow the fillet to a verified outer-edge subset instead of "
                "filleting every `Axis.Z` edge on the fresh host."
            ),
            "recipe_skeleton": {
                "mode": "whole_part_rebuild_via_execute_build123d",
                "steps": [
                    "build the enclosure/lid/base shell hosts and host-owned local cuts first, keeping the shell host authoritative",
                    "if the intended enclosure silhouette is rounded-rect or pillbox-like, build that rounded footprint directly with `RectangleRounded(...)` in `BuildSketch(...)` instead of using a fresh-host broad edge fillet to create the overall shape",
                    "validate that the part count, assembled pose, hinge/local cuts, and requested bbox are already stable before adding broad shell-edge fillets",
                    "if a fillet is still needed in the rebuild lane, fillet only a verified outer-edge subset instead of `builder.edges().filter_by(Axis.Z)` across the whole host",
                    "if only the finishing fillet remains after the host is valid, prefer query_topology plus a local finishing step over another broad whole-part fillet pass",
                ],
            },
        }
    if "invalid_build123d_contract.broad_local_finish_tail_fillet_on_first_write" in lint_ids:
        return {
            "recipe_id": "local_finish_fillet_postpone_contract",
            "recipe_summary": (
                "When the requirement already frames a fillet/chamfer as later local finishing, "
                "do not spend the first whole-part rebuild on a broad edge selector. Stabilize "
                "the host first, then finish that edge set from exact topology refs."
            ),
            "recipe_skeleton": {
                "mode": "whole_part_rebuild_via_execute_build123d",
                "steps": [
                    "build the primary host geometry and the directly expressible pockets, holes, and recesses first",
                    "if the fillet/chamfer is already described as a later local finish, leave it out of the first whole-part write instead of guessing a broad edge selector",
                    "do not fillet `builder.edges().filter_by(...)`, `filter_by_position(...)`, or stored broad edge sets before exact topology refs exist",
                    "once the host is stable, use query_topology to identify the exact target edges and finish that detail through the bounded local-finish lane",
                    "if a rebuild still needs a fillet, narrow the selector to a verified edge subset instead of broad whole-part edge bands",
                ],
            },
        }
    explicit_anchor_hole_countersink_recipe_lint_ids = {
        "legacy_api.countersink_workplane_method",
        "legacy_api.cut_extrude_helper",
        "invalid_build123d_api.countersink_helper_name",
        "invalid_build123d_api.workplanes_helper_name",
        "invalid_build123d_api.lowercase_hole_helper_name",
        "invalid_build123d_keyword.cone_radius_alias",
        "invalid_build123d_keyword.countersink_radius_alias",
        "invalid_build123d_keyword.countersink_head_diameter_alias",
        "invalid_build123d_keyword.countersink_through_diameter_alias",
        "invalid_build123d_keyword.countersink_angle_alias",
        "invalid_build123d_keyword.countersink_depth_alias",
        "invalid_build123d_context.countersinkhole_requires_buildpart",
        "invalid_build123d_contract.centered_box_face_plane_full_span_offset",
        "invalid_build123d_contract.named_face_plane_family_mismatch",
        "legacy_api.workplane_chain",
    }
    if lint_ids.intersection(
        {
            "invalid_build123d_api.makeface_helper_case",
            "invalid_build123d_contract.buildsketch_wire_requires_make_face",
        }
    ) and not (
        {"explicit_anchor_hole", "pattern_distribution"} & family_id_set
        and lint_ids.intersection(explicit_anchor_hole_countersink_recipe_lint_ids)
    ):
        return {
            "recipe_id": "build123d_make_face_helper_contract",
            "recipe_summary": (
                "When converting a closed `BuildLine` wire into a sketch face, use "
                "lowercase `make_face()` in the same builder context; `MakeFace()` is "
                "not a Build123d helper."
            ),
            "recipe_skeleton": {
                "mode": "local_edit_via_execute_build123d",
                "steps": [
                    "finish the closed wire inside `with BuildLine() as profile:`",
                    "call lowercase `make_face()` after the closed wire is complete",
                    "continue with `extrude(...)`, `mode=Mode.SUBTRACT`, or another builder-native solid operation from that resulting face",
                ],
            },
        }
    if lint_ids.intersection(
        {
            "invalid_build123d_context.curve_requires_buildline",
            "invalid_build123d_keyword.revolve_angle_alias",
        }
    ):
        return {
            "recipe_id": "build123d_revolve_profile_contract",
            "recipe_summary": (
                "For Build123d revolve profiles, keep curve construction inside "
                "`BuildLine`, convert the closed wire with `make_face()`, and call "
                "`revolve(...)` with the supported default or `revolution_arc=` keyword."
            ),
            "recipe_skeleton": {
                "mode": "whole_part_rebuild_via_execute_build123d",
                "steps": [
                    "open `with BuildSketch(target_plane):` on the plane containing the rotation axis",
                    "inside `BuildLine`, create the closed profile with `Polyline(...)`, `Line(...)`, and/or arc helpers",
                    "call lowercase `make_face()` after the wire closes",
                    "revolve that profile with `revolve(axis=Axis.Z)` or `revolve(axis=Axis.Z, revolution_arc=360)` instead of using `angle=`",
                ],
            },
        }
    path_sweep_signature_lint_ids = {
        "invalid_build123d_contract.explicit_radius_arc_prefers_center_arc",
        "invalid_build123d_keyword.center_arc_arc_angle_alias",
        "invalid_build123d_keyword.center_arc_end_angle_alias",
        "invalid_build123d_contract.center_arc_missing_start_angle",
    }
    path_sweep_specific_lint_ids = {
        "invalid_build123d_contract.sweep_path_wire_method_reference",
        "invalid_build123d_contract.sweep_path_line_alias",
        "invalid_build123d_keyword.sweep_section_alias",
        "invalid_build123d_keyword.solid_sweep_unsupported_keyword",
        "invalid_build123d_contract.sweep_profile_face_method_reference",
        "invalid_build123d_contract.annular_profile_face_splitting",
        "invalid_build123d_contract.annular_profile_face_extraction",
        "invalid_build123d_contract.vector_component_indexing",
        "invalid_build123d_keyword.plane_normal_alias",
    }
    if lint_ids.intersection(path_sweep_specific_lint_ids) or (
        "path_sweep" in family_ids
        and (
            lint_ids.intersection(path_sweep_signature_lint_ids)
            or "invalid_build123d_contract.builder_method_reference_assignment" in lint_ids
            or "invalid_build123d_api.symbolic_degree_constant" in lint_ids
        )
    ):
        return {
            "recipe_id": "build123d_path_sweep_contract",
            "recipe_summary": (
                "For Build123d path sweeps, keep the rail in `BuildLine`, keep the profile "
                "as a real closed section, and keep annular same-sketch sweeps on the verified "
                "Build123d API contract before escalating to more fragile split-profile lanes."
            ),
            "recipe_skeleton": {
                "mode": "whole_part_rebuild_via_execute_build123d",
                "steps": [
                    "open `with BuildLine() as path:` and build the full rail there",
                    "when the requirement gives an explicit elbow radius or quarter-turn, prefer a directly specified `CenterArc(...)` rail segment over guessed `TangentArc(...)` / `JernArc(...)` endpoint constructions",
                    "construct the profile plane with `Plane(origin=..., z_dir=path_tangent)` or an equivalent named plane; do not pass `normal=` to `Plane(...)`",
                    "if the section is one same-sketch annular profile built with an outer loop plus `mode=Mode.SUBTRACT`, treat it as one face with inner wires and prefer `sweep(profile.sketch, path=path_wire)`",
                    "do not split one subtractive annular sketch into guessed `outer_face` / `inner_face` objects by indexing `profile.faces()[1]` or similar sorted-face shortcuts",
                    "only if the annular sketch sweep already produced shell/null geometry should you rebuild truly separate outer/inner closed section faces and then compute one explicit solid boolean such as `result = outer_tube - inner_tube`",
                    "when using `Solid.sweep(...)`, stay on the verified signature such as `Solid.sweep(section_face, path_wire)` or `Solid.sweep(section=..., path=...)`; do not invent keywords like `path_wire=` or `profile_plane=`",
                    "if the requested world-space rail orientation keeps collapsing to zero-volume sweep output, rebuild the rail/profile in a stable local frame first and rotate/translate the finished tube into the target pose afterward",
                    "assign the final material solid back to `result` or `pipe.part` instead of leaving only builders in scope",
                ],
            },
        }
    if lint_ids.intersection(
        {
            "invalid_build123d_keyword.circle_arc_size",
            "invalid_build123d_keyword.center_arc_arc_angle_alias",
            "invalid_build123d_keyword.center_arc_end_angle_alias",
            "invalid_build123d_contract.center_arc_missing_start_angle",
            "invalid_build123d_contract.circle_make_face_trim_profile",
            "invalid_build123d_api.semicircle_helper_name",
            "invalid_build123d_api.symbolic_degree_constant",
        }
    ):
        return {
            "recipe_id": "build123d_arc_profile_contract",
            "recipe_summary": (
                "When a Build123d profile needs a semicircle or circular arc, use "
                "`CenterArc(...)` or `RadiusArc(...)` inside `BuildLine`; `Circle(...)` "
                "stays full-circle geometry and there is no `Semicircle(...)` helper."
            ),
            "recipe_skeleton": {
                "mode": "whole_part_rebuild_via_execute_build123d",
                "steps": [
                    "open `with BuildSketch(target_plane):` for the profile plane",
                    "inside `BuildLine`, draw the needed outer/inner `CenterArc(...)` or `RadiusArc(...)` segments",
                    "close the split edge explicitly with `Line(...)` segments and call `make_face()`",
                    "extrude the resulting closed profile instead of guessing `Circle(..., arc_size=...)` or `Semicircle(...)`",
                ],
            },
        }
    if "invalid_build123d_contract.builder_method_reference_assignment" in lint_ids:
        return {
            "recipe_id": "build123d_builder_method_reference_contract",
            "recipe_summary": (
                "When capturing geometry from a Build123d builder, call accessor methods "
                "such as `.wire()` / `.face()` instead of storing the bound method object."
            ),
            "recipe_skeleton": {
                "mode": "subtree_rebuild_via_execute_build123d",
                "steps": [
                    "identify builder-derived assignments such as `path_builder.wire` or `profile_builder.face`",
                    "call the accessor method when you need the actual geometry, for example `path_builder.wire()` or `profile_builder.face()`",
                    "if a builder-native object is already sufficient, prefer `profile_builder.sketch` or the direct builder output instead of intermediate method references",
                    "propagate the real geometry object into later sweep/revolve/boolean calls",
                ],
            },
        }
    if (
        "invalid_build123d_keyword.extrude_direction_alias" in lint_ids
        or "invalid_build123d_contract.centered_box_breaks_plane_anchored_positive_extrude"
        in lint_ids
    ):
        return {
            "recipe_id": "build123d_plane_anchored_extrude_contract",
            "recipe_summary": (
                "When the requirement explicitly says to sketch on a named plane and extrude "
                "upward/positively, preserve that plane-anchored span literally instead of "
                "switching to a default centered primitive or an unsupported extrude keyword."
            ),
            "recipe_skeleton": {
                "mode": "whole_part_rebuild_via_execute_build123d",
                "steps": [
                    "open `with BuildSketch(Plane.XY/XZ/YZ):` on the named requirement plane",
                    "draw the required square/rectangle/profile there instead of defaulting to a centered Box(...)",
                    "use `extrude(amount=...)` from that sketch, or only use a primitive-solid equivalent when its alignment/placement keeps the named plane as the lower bound",
                    "if you need a non-default extrusion direction, use the supported `dir=` keyword or change the sketch plane/orientation explicitly",
                ],
            },
        }
    if "explicit_anchor_hole" not in family_id_set and "pattern_distribution" not in family_id_set:
        return {}
    if not lint_ids.intersection(explicit_anchor_hole_countersink_recipe_lint_ids):
        return {}
    return {
        "recipe_id": "explicit_anchor_hole_countersink_array_safe_recipe",
        "recipe_summary": (
            "For countersunk hole arrays, map the point coordinates into the correct host-face "
            "frame and realize the through-hole plus countersink with an explicit same-builder "
            "subtractive recipe on the actual target face plane instead of relying on guessed "
            "helper names or a default mid-plane placement."
        ),
        "recipe_skeleton": {
            "mode": "subtree_rebuild_via_execute_build123d",
            "steps": [
                "with BuildPart() as part: ...",
                "compute the full local hole center set in the host-face frame, including any centered-host translation from corner-based sketch coordinates",
                "if the holes belong on a specific face such as the top face of a centered plate, include that face-plane translation in each placement, for example `Locations((x, y, top_z), ...)`",
                "for explicit planar countersink arrays where the requirement already gives the through-hole diameter, head diameter, and cone angle, prefer one `CounterSinkHole(...)` pass first with the exact helper contract and explicit host-face placement",
                "Only fall back to an explicit same-builder cylinder+cone or revolved countersink recipe when the helper contract cannot express the host/placement semantics cleanly or when prior validation/evaluation evidence shows the helper result is dimensionally wrong for that family",
                "if you use `CounterSinkHole(...)`, keep it in BuildPart, not BuildSketch, and keep the keyword names literal",
                "result = part.part",
            ],
        },
    }


def _preflight_lint_failure_kind(lint_hits: list[dict[str, Any]]) -> str:
    lint_ids = {
        str(item.get("rule_id") or "").strip()
        for item in lint_hits
        if isinstance(item, dict) and str(item.get("rule_id") or "").strip()
    }
    if "python_syntax.invalid_script" in lint_ids:
        return "execute_build123d_python_syntax_failure"
    return "execute_build123d_api_lint_failure"


def _preflight_gate_apply_cad_action(
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
                "Use face_ref or edge_refs from the latest query_topology result for the next local edit.",
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
                    "Choose one concrete face_ref from the latest query_topology candidate set instead of passing the candidate-set label directly.",
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
            alias_fragment = f" instead of face={broad_face_alias!r}" if broad_face_alias else ""
            suggestions = [
                "Use face_ref from the latest query_topology result for this local face edit instead of a broad face or plane alias.",
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
        "Use explicit edge_refs from the latest query_topology candidate_sets before retrying this local fillet/chamfer.",
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


def _requirement_mentions_explicit_cylindrical_slot(requirement_lower: str) -> bool:
    if not requirement_lower:
        return False
    slot_tokens = ("slot", "groove", "notch")
    if not any(token in requirement_lower for token in slot_tokens):
        return False
    cylindrical_tokens = (
        "cylinder",
        "cylindrical",
        "semicircular",
        "centerline",
        "axis along",
    )
    if not any(token in requirement_lower for token in cylindrical_tokens):
        return False
    return "boolean difference" in requirement_lower or "tool body" in requirement_lower


def _requirement_mentions_half_shell_with_split_surface(requirement_lower: str) -> bool:
    if not requirement_lower:
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
    if not any(token in requirement_lower for token in half_shell_tokens):
        return False
    return any(
        token in requirement_lower
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


def _requirement_mentions_plane_anchored_positive_extrude(requirement_lower: str) -> bool:
    if not requirement_lower:
        return False
    if not re.search(r"\b(?:xy|xz|yz)\s+plane\b", requirement_lower):
        return False
    if "extrude" not in requirement_lower:
        return False
    if not any(
        token in requirement_lower
        for token in ("rectangle", "square", "profile", "draw ")
    ):
        return False
    return any(
        token in requirement_lower
        for token in ("upward", "positive", "to form", "to create")
    )


def _record_from_result(
    *,
    name: str,
    category: ToolCategory,
    result: Any,
) -> ToolResultRecord:
    payload = _result_to_dict(result)
    success = bool(payload.get("success", False))
    artifact_files = [
        item for item in payload.get("output_files", []) if isinstance(item, str)
    ]
    artifact_contents = payload.get("output_file_contents")
    if not isinstance(artifact_contents, dict):
        artifact_contents = {}
    normalized_contents = {
        filename: content
        for filename, content in artifact_contents.items()
        if isinstance(filename, str) and isinstance(content, (bytes, bytearray))
    }
    error = payload.get("error_message")
    if not isinstance(error, str):
        error = None
    stderr_value = payload.get("stderr")
    if (
        isinstance(error, str)
        and error.strip().lower().startswith("exit code:")
        and isinstance(stderr_value, str)
        and stderr_value.strip()
    ):
        stderr_head = stderr_value.strip().splitlines()[0].strip()
        if stderr_head:
            error = f"{error.strip()} | {stderr_head[:180]}"
    step_file = payload.get("step_file")
    if not isinstance(step_file, str):
        step_file = None
    return ToolResultRecord(
        name=name,
        category=category,
        success=success,
        payload=payload,
        error=error,
        artifact_files=artifact_files,
        artifact_contents=normalized_contents,
        step_file=step_file,
    )


def _result_to_dict(result: Any) -> dict[str, Any]:
    if isinstance(result, dict):
        return dict(result)
    if hasattr(result, "model_dump"):
        return result.model_dump(mode="python", exclude_none=False)
    if hasattr(result, "__dataclass_fields__"):
        return asdict(result)
    if hasattr(result, "__dict__"):
        return {
            key: value
            for key, value in vars(result).items()
            if not key.startswith("_")
        }
    return {"success": False, "error_message": f"unserializable_result:{type(result)}"}


def _summarize_result_payload(result: ToolResultRecord) -> dict[str, Any]:
    payload = result.payload
    summary: dict[str, Any] = {
        "tool_name": result.name,
        "success": result.success,
    }
    for key in (
        "error_code",
        "error_message",
        "step",
        "session_id",
        "summary",
        "is_complete",
        "blockers",
        "features",
        "issues",
        "view_file",
        "step_file",
        "session_state_persisted",
    ):
        if key in payload:
            summary[key] = payload.get(key)
    if "snapshot" in payload and isinstance(payload["snapshot"], dict):
        summary["snapshot"] = {
            key: payload["snapshot"].get(key)
            for key in ("step", "issues", "geometry")
            if key in payload["snapshot"]
        }
    return summary


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
