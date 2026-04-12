from __future__ import annotations

from dataclasses import asdict, dataclass, field
import re
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


def _preflight_lint_execute_build123d(
    *,
    code: str,
    session_id: str,
    requirement_text: str,
    run_state: RunState | None,
) -> dict[str, Any] | None:
    compact_lowered = re.sub(r"\s+", "", code.lower())
    hits: list[dict[str, Any]] = []

    if re.search(r"^\s*(import|from)\s+cadquery\b", code, flags=re.MULTILINE):
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
    if re.search(r"\bcq\.", code) or "workplane(" in compact_lowered:
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
    if "countersinkhole(" in compact_lowered or "countersinksink(" in compact_lowered:
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
    if not re.search(r"(?m)^\s*(result|part)\s*=", code):
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
    summary = (
        "Preflight lint rejected unsupported legacy modeling-kernel usage or missing "
        "final Build123d result assignment before sandbox execution."
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
        "failure_kind": "execute_build123d_api_lint_failure",
        "summary": summary,
        "lint_hits": hits,
        "candidate_family_ids": family_ids,
        "repair_recipe": repair_recipe,
    }


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
    if "explicit_anchor_hole" not in family_ids and "pattern_distribution" not in family_ids:
        return {}
    if not (
        "legacy_api.countersink_workplane_method" in lint_ids
        or "legacy_api.workplane_chain" in lint_ids
    ):
        return {}
    return {
        "recipe_id": "explicit_anchor_hole_countersink_array_safe_recipe",
        "recipe_summary": (
            "For countersunk hole arrays, build the host with BuildPart, sketch the local hole "
            "centers with BuildSketch/Locations on the target Plane, and cut explicit hole plus "
            "countersink cutters from that anchored frame."
        ),
        "recipe_skeleton": {
            "mode": "subtree_rebuild_via_execute_build123d",
            "steps": [
                "with BuildPart() as part: ...",
                "target_plane = Plane(origin=..., z_dir=Axis.Z.direction)",
                "with Locations(*hole_centers): build explicit cylindrical and countersink cutters",
                "result = part.part",
            ],
        },
    }


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
