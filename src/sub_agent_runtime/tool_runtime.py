from __future__ import annotations

import ast
from dataclasses import asdict, dataclass, field
import io
import re
import tokenize
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
                self._execute_single(
                    tool_call=tool_call,
                    session_id=session_id,
                    requirements=requirements,
                    requirement_text=requirement_text,
                    sandbox_timeout=sandbox_timeout,
                    round_no=round_no,
                    run_state=run_state,
                )
                for tool_call in normalized_calls
            ]
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
    ) -> dict[str, Any]:
        merged = dict(arguments)
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


async def _gather_results(tasks: list[Any]) -> list[ToolResultRecord]:
    raw_results = await __import__("asyncio").gather(*tasks)
    return [result for result in raw_results if isinstance(result, ToolResultRecord)]


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
    if re.search(r"\b(?:CountersinkHole|CounterSink)\s*\(", code_for_lint):
        hits.append(
            {
                "rule_id": "invalid_build123d_api.countersink_helper_name",
                "message": (
                    "Build123d uses `CounterSinkHole(...)`, not helper-name guesses such "
                    "as `CountersinkHole(...)` or `CounterSink(...)`."
                ),
                "repair_hint": (
                    "Use the exact Build123d helper name `CounterSinkHole(...)` and keep "
                    "its keyword names literal."
                ),
            }
        )
    if re.search(r"\bcountersink_radius\s*=", code_for_lint):
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
    if re.search(r"\bcountersink_angle\s*=", code_for_lint):
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
    if re.search(r"\bCylinder\s*\([^)]*\baxis\s*=", code_for_lint, flags=re.DOTALL):
        hits.append(
            {
                "rule_id": "invalid_build123d_keyword.cylinder_axis",
                "message": "Cylinder(...) does not accept an axis= keyword in Build123d.",
                "repair_hint": (
                    "Create the cutter with `Cylinder(radius=..., height=...)`, then orient "
                    "it with `Rot(...)` and place it with `Pos(...)` or `Locations(...)`."
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
    )
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


def _ast_name_matches(node: ast.AST, expected: str) -> bool:
    if isinstance(node, ast.Name):
        return node.id == expected
    if isinstance(node, ast.Attribute):
        return node.attr == expected
    return False


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
        if isinstance(element, ast.Constant) and isinstance(element.value, (int, float)):
            continue
        if (
            isinstance(element, ast.UnaryOp)
            and isinstance(element.op, (ast.UAdd, ast.USub))
            and isinstance(element.operand, ast.Constant)
            and isinstance(element.operand.value, (int, float))
        ):
            continue
        return False
    return True


def _temporary_primitive_arithmetic_expr(node: ast.AST) -> tuple[ast.AST | None, int]:
    arithmetic_ops = (ast.Add, ast.Sub, ast.Mult, ast.BitAnd, ast.BitOr)
    if isinstance(node, ast.Assign) and isinstance(node.value, ast.BinOp) and isinstance(
        node.value.op, arithmetic_ops
    ):
        return node.value, int(getattr(node, "lineno", 0) or 0)
    if isinstance(node, ast.AugAssign) and isinstance(node.op, arithmetic_ops):
        return node.value, int(getattr(node, "lineno", 0) or 0)
    return None, 0


def _candidate_lint_family_ids(
    *,
    requirement_text: str,
    run_state: RunState | None,
) -> list[str]:
    families: list[str] = []
    graph = getattr(run_state, "feature_graph", None)
    feature_instances = getattr(graph, "feature_instances", None)
    if isinstance(feature_instances, dict):
        for feature_instance in feature_instances.values():
            family_id = str(getattr(feature_instance, "family_id", "") or "").strip()
            if family_id and family_id not in families and family_id != "general_geometry":
                families.append(family_id)
    lowered_requirement = requirement_text.lower()
    if (
        any(token in lowered_requirement for token in ("countersink", "countersunk"))
        and "explicit_anchor_hole" not in families
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


def _build_preflight_repair_recipe(
    *,
    family_ids: list[str],
    lint_hits: list[dict[str, Any]],
) -> dict[str, Any]:
    lint_ids = {
        str(item.get("rule_id") or "").strip()
        for item in lint_hits
        if isinstance(item, dict)
    }
    if not lint_ids:
        return {}
    if "slots" in family_ids and lint_ids.intersection(
        {
            "invalid_build123d_api.bare_subtract_helper",
            "invalid_build123d_api.bare_rotate_helper",
            "invalid_build123d_keyword.cylinder_axis",
            "invalid_build123d_contract.active_builder_cutter_primitive_boolean",
        }
    ):
        return {
            "recipe_id": "explicit_cylindrical_slot_boolean_safe_recipe",
            "recipe_summary": (
                "For an explicit cutting-cylinder slot, build the host solid first, build one "
                "literal Cylinder cutter, orient it with Rot(...), place it with Pos(...), and "
                "subtract it with an explicit solid boolean such as `result = host.part - cutter`."
            ),
            "recipe_skeleton": {
                "mode": "whole_part_rebuild_via_execute_build123d",
                "steps": [
                    "with BuildPart() as host: build the target body and assign host.part",
                    "cutter = Cylinder(radius=..., height=..., align=(Align.CENTER, Align.CENTER, Align.CENTER))",
                    "cutter = Pos(...) * (Rot(Y=90) * cutter)",
                    "result = host.part - cutter",
                ],
            },
        }
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
        and "invalid_build123d_api.nested_buildpart_cutter_part_arithmetic" in lint_ids
    ):
        return {
            "recipe_id": "explicit_anchor_hole_same_builder_subtract_recipe",
            "recipe_summary": (
                "For repeated countersunk hole layouts, keep the host in one BuildPart, "
                "convert explicit point coordinates into the correct host-face frame, and "
                "realize the hole/countersink cutters through one supported subtractive "
                "pattern instead of nesting BuildPart cutters and mutating `part.part` in-place."
            ),
            "recipe_skeleton": {
                "mode": "subtree_rebuild_via_execute_build123d",
                "steps": [
                    "with BuildPart() as part: build the host body first",
                    "compute the full hole center set in the host-face coordinate frame before cutting",
                    "either keep the cutters in the same active BuildPart with explicit subtractive placement, or close the host builder and subtract fully positioned cutters with `result = host.part - cutter`",
                    "do not use nested `with BuildPart() as cutter:` blocks followed by `part.part -= cutter.part` inside the host builder",
                ],
            },
        }
    if (
        ("annular_groove" in family_ids or "axisymmetric_profile" in family_ids)
        and "invalid_build123d_api.nested_buildpart_cutter_part_arithmetic" in lint_ids
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
                    "do not use `with BuildPart() as groove_band:` inside the host builder followed by `part.part -= groove_band.part`",
                ],
            },
        }
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
                    "if the requirement is a split shell or half-profile body, prefer one closed semi-profile and extrude it for the base envelope",
                    "otherwise close the host builder first, then create any temporary solids outside it before doing explicit solid arithmetic such as `result = host.part - cutter` or `result = host.part & trim_box`",
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
    if "invalid_build123d_keyword.box_depth_alias" in lint_ids:
        return {
            "recipe_id": "build123d_box_keyword_contract",
            "recipe_summary": (
                "When using Build123d Box primitives, stay on the native `length / width / "
                "height` contract and do not use a guessed `depth=` keyword alias."
            ),
            "recipe_skeleton": {
                "mode": "whole_part_rebuild_via_execute_build123d",
                "steps": [
                    "define the three host spans explicitly as length, width, and height",
                    "call `Box(length=..., width=..., height=...)` or `Box(length, width, height)`",
                    "if your variable name is `depth`, pass that variable as the second width dimension instead of `depth=...`",
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
    if "invalid_build123d_api.plane_rotated_origin_guess" in lint_ids:
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
                    "only call `Plane.rotated((rx, ry, rz), ordering=...)` when you truly need a different orientation, and do not pass a second `(x, y, z)` tuple",
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
    if "invalid_build123d_api.makeface_helper_case" in lint_ids:
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
            "invalid_build123d_keyword.circle_arc_size",
            "invalid_build123d_api.semicircle_helper_name",
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
    if "explicit_anchor_hole" not in family_ids and "pattern_distribution" not in family_ids:
        return {}
    if not (
        "legacy_api.countersink_workplane_method" in lint_ids
        or "invalid_build123d_api.countersink_helper_name" in lint_ids
        or "invalid_build123d_keyword.countersink_radius_alias" in lint_ids
        or "invalid_build123d_keyword.countersink_angle_alias" in lint_ids
        or "invalid_build123d_context.countersinkhole_requires_buildpart" in lint_ids
        or "legacy_api.workplane_chain" in lint_ids
    ):
        return {}
    return {
        "recipe_id": "explicit_anchor_hole_countersink_array_safe_recipe",
        "recipe_summary": (
            "For countersunk hole arrays, keep `CounterSinkHole(...)` inside BuildPart, map the "
            "point coordinates into the correct host-face frame, and place each hole on the "
            "actual target face plane instead of leaving it on the default mid-plane."
        ),
        "recipe_skeleton": {
            "mode": "subtree_rebuild_via_execute_build123d",
            "steps": [
                "with BuildPart() as part: ...",
                "compute the full local hole center set in the host-face frame, including any centered-host translation from corner-based sketch coordinates",
                "if the holes belong on a specific face such as the top face of a centered plate, include that face-plane translation in each placement, for example `Locations((x, y, top_z), ...)`",
                "`CounterSinkHole(...)` belongs in BuildPart, not BuildSketch",
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


def _trace_to_dict(trace: ToolHookTrace) -> dict[str, Any]:
    return {
        "pre": trace.pre,
        "post_success": trace.post_success,
        "post_failure": trace.post_failure,
        "pre_finish": trace.pre_finish,
        "notes": list(trace.notes),
    }
