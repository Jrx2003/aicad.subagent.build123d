import asyncio
import base64
import binascii
import os
from dataclasses import dataclass
from typing import Any

from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from common.logging import get_logger
from sandbox.interface import SandboxResult

DEFAULT_MCP_TOOL_NAME = "execute_build123d"
DEFAULT_APPLY_CAD_ACTION_TOOL_NAME = "apply_cad_action"
DEFAULT_QUERY_SNAPSHOT_TOOL_NAME = "query_snapshot"
DEFAULT_QUERY_SKETCH_TOOL_NAME = "query_sketch"
DEFAULT_QUERY_GEOMETRY_TOOL_NAME = "query_geometry"
DEFAULT_QUERY_TOPOLOGY_TOOL_NAME = "query_topology"
DEFAULT_QUERY_FEATURE_PROBES_TOOL_NAME = "query_feature_probes"
DEFAULT_RENDER_VIEW_TOOL_NAME = "render_view"
DEFAULT_VALIDATE_REQUIREMENT_TOOL_NAME = "validate_requirement"
DEFAULT_EXECUTE_BUILD123D_PROBE_TOOL_NAME = "execute_build123d_probe"
DEFAULT_GET_HISTORY_TOOL_NAME = "get_history"

logger = get_logger(__name__)


@dataclass
class CADActionResult:
    """Result payload for MCP apply_cad_action tool."""

    success: bool
    stdout: str
    stderr: str
    error_message: str | None
    output_files: list[str]
    output_file_contents: dict[str, bytes]
    snapshot: dict[str, Any] | None
    action_history: list[dict[str, Any]]
    suggestions: list[str]
    completeness: dict[str, Any] | None
    step_file: str | None


@dataclass
class SnapshotQueryResult:
    """Result payload for MCP query_snapshot tool."""

    success: bool
    error_code: str
    error_message: str | None
    session_id: str
    step: int | None
    snapshot: dict[str, Any] | None
    action_history: list[dict[str, Any]]


@dataclass
class GeometryQueryResult:
    """Result payload for MCP query_geometry tool."""

    success: bool
    error_code: str
    error_message: str | None
    session_id: str
    step: int | None
    geometry: dict[str, Any] | None
    features: list[str]
    issues: list[str]
    object_index: dict[str, Any] | None
    matched_entity_ids: list[str]
    next_solid_offset: int | None
    next_face_offset: int | None
    next_edge_offset: int | None


@dataclass
class SketchQueryResult:
    """Result payload for MCP query_sketch tool."""

    success: bool
    error_code: str
    error_message: str | None
    session_id: str
    step: int | None
    sketch_state: dict[str, Any] | None
    relation_index: dict[str, Any] | None


@dataclass
class TopologyQueryResult:
    """Result payload for MCP query_topology tool."""

    success: bool
    error_code: str
    error_message: str | None
    session_id: str
    step: int | None
    topology_index: dict[str, Any] | None
    matched_entity_ids: list[str]
    matched_ref_ids: list[str]
    candidate_sets: list[dict[str, Any]]
    applied_hints: list[str]
    relation_index: dict[str, Any] | None
    next_face_offset: int | None
    next_edge_offset: int | None


@dataclass
class FeatureProbeQueryResult:
    """Result payload for MCP query_feature_probes tool."""

    success: bool
    error_code: str
    error_message: str | None
    session_id: str
    step: int | None
    detected_families: list[str]
    probes: list[dict[str, Any]]
    summary: str


@dataclass
class RenderViewResult:
    """Result payload for MCP render_view tool."""

    success: bool
    error_code: str
    error_message: str | None
    session_id: str
    step: int | None
    view_file: str | None
    output_files: list[str]
    output_file_contents: dict[str, bytes]
    camera: dict[str, Any]
    focused_entity_ids: list[str]
    focus_bbox: dict[str, Any] | None


@dataclass
class RequirementValidationResult:
    """Result payload for MCP validate_requirement tool."""

    success: bool
    error_code: str
    error_message: str | None
    session_id: str
    step: int | None
    is_complete: bool
    blockers: list[str]
    checks: list[dict[str, Any]]
    core_checks: list[dict[str, Any]]
    diagnostic_checks: list[dict[str, Any]]
    relation_index: dict[str, Any] | None
    summary: str


@dataclass
class Build123dProbeResult:
    """Result payload for MCP execute_build123d_probe tool."""

    success: bool
    stdout: str
    stderr: str
    error_message: str | None
    output_files: list[str]
    output_file_contents: dict[str, bytes]
    session_id: str | None
    step: int | None
    step_file: str | None
    probe_summary: dict[str, Any]
    session_state_persisted: bool


class McpSandboxRunner:
    """Sandbox runner that executes Build123d code through MCP stdio."""

    def __init__(
        self,
        command: str = "uv",
        args: list[str] | None = None,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        tool_name: str = DEFAULT_MCP_TOOL_NAME,
        timeout_buffer_seconds: int = 30,
    ) -> None:
        self._command = command
        self._args = (
            list(args)
            if args is not None
            else ["run", "python", "-m", "sandbox_mcp_server"]
        )
        self._cwd = cwd.strip() if cwd and cwd.strip() else None
        self._env = dict(env) if env is not None else os.environ.copy()
        self._tool_name = tool_name
        self._timeout_buffer_seconds = timeout_buffer_seconds

    async def execute(
        self,
        code: str,
        timeout: int = 120,
        requirement_text: str | None = None,
        session_id: str | None = None,
    ) -> SandboxResult:
        arguments = {
            "code": code,
            "timeout_seconds": timeout,
            "include_artifact_content": True,
        }
        if requirement_text and requirement_text.strip():
            arguments["requirement_text"] = requirement_text.strip()
        if session_id is not None:
            arguments["session_id"] = session_id

        try:
            call_result = await asyncio.wait_for(
                self._call_tool(arguments=arguments),
                timeout=timeout + self._timeout_buffer_seconds,
            )
        except TimeoutError:
            logger.warning(
                "sandbox_mcp_request_timeout",
                timeout=timeout,
                timeout_buffer_seconds=self._timeout_buffer_seconds,
            )
            return SandboxResult(
                success=False,
                stdout="",
                stderr=f"Sandbox MCP request timed out after {timeout} seconds",
                output_files=[],
                output_file_contents={},
                error_message="Timeout",
            )
        except Exception as exc:
            logger.warning(
                "sandbox_mcp_request_failed",
                command=self._command,
                args=self._args,
                cwd=self._cwd,
                error=str(exc),
                exc_info=True,
            )
            return SandboxResult(
                success=False,
                stdout="",
                stderr=f"Sandbox MCP request failed: {exc}",
                output_files=[],
                output_file_contents={},
                error_message="MCP request failed",
            )

        return self._map_call_result(call_result)

    async def apply_cad_action(
        self,
        action_type: str,
        action_params: dict[str, Any] | None = None,
        session_id: str | None = None,
        timeout: int = 60,
        include_artifact_content: bool = True,
        clear_session: bool = False,
    ) -> CADActionResult:
        arguments: dict[str, Any] = {
            "action_type": action_type,
            "action_params": action_params or {},
            "timeout_seconds": timeout,
            "include_artifact_content": include_artifact_content,
            "clear_session": clear_session,
        }
        if session_id is not None:
            arguments["session_id"] = session_id

        try:
            call_result = await asyncio.wait_for(
                self._call_named_tool(
                    tool_name=DEFAULT_APPLY_CAD_ACTION_TOOL_NAME,
                    arguments=arguments,
                ),
                timeout=timeout + self._timeout_buffer_seconds,
            )
        except TimeoutError:
            logger.warning(
                "sandbox_mcp_apply_action_timeout",
                timeout=timeout,
                timeout_buffer_seconds=self._timeout_buffer_seconds,
                action_type=action_type,
            )
            return CADActionResult(
                success=False,
                stdout="",
                stderr=f"apply_cad_action timed out after {timeout} seconds",
                error_message="Timeout",
                output_files=[],
                output_file_contents={},
                snapshot=None,
                action_history=[],
                suggestions=[],
                completeness=None,
                step_file=None,
            )
        except Exception as exc:
            logger.warning(
                "sandbox_mcp_apply_action_failed",
                action_type=action_type,
                error=str(exc),
                exc_info=True,
            )
            return CADActionResult(
                success=False,
                stdout="",
                stderr=f"apply_cad_action failed: {exc}",
                error_message="MCP request failed",
                output_files=[],
                output_file_contents={},
                snapshot=None,
                action_history=[],
                suggestions=[],
                completeness=None,
                step_file=None,
            )

        return self._map_action_call_result(call_result)

    async def apply_action_sequence(
        self,
        actions: list[dict[str, Any]],
        session_id: str | None = None,
        timeout: int = 60,
        include_artifact_content: bool = True,
        clear_session: bool = False,
    ) -> list[CADActionResult]:
        if not actions:
            return []

        normalized_actions: list[dict[str, Any]] = []
        for action in actions:
            action_type = action.get("action_type")
            if not isinstance(action_type, str):
                continue
            action_params = action.get("action_params")
            normalized_actions.append(
                {
                    "action_type": action_type,
                    "action_params": (
                        action_params if isinstance(action_params, dict) else {}
                    ),
                }
            )

        if not normalized_actions:
            return []

        try:
            return await asyncio.wait_for(
                self._call_action_sequence(
                    actions=normalized_actions,
                    session_id=session_id,
                    timeout=timeout,
                    include_artifact_content=include_artifact_content,
                    clear_session=clear_session,
                ),
                timeout=(timeout + self._timeout_buffer_seconds)
                * max(1, len(normalized_actions)),
            )
        except TimeoutError:
            logger.warning(
                "sandbox_mcp_apply_action_sequence_timeout",
                timeout=timeout,
                actions_count=len(normalized_actions),
            )
            return [
                CADActionResult(
                    success=False,
                    stdout="",
                    stderr=f"apply_action_sequence timed out after {timeout} seconds",
                    error_message="Timeout",
                    output_files=[],
                    output_file_contents={},
                    snapshot=None,
                    action_history=[],
                    suggestions=[],
                    completeness=None,
                    step_file=None,
                )
            ]
        except Exception as exc:
            logger.warning(
                "sandbox_mcp_apply_action_sequence_failed",
                error=str(exc),
                actions_count=len(normalized_actions),
                exc_info=True,
            )
            return [
                CADActionResult(
                    success=False,
                    stdout="",
                    stderr=f"apply_action_sequence failed: {exc}",
                    error_message="MCP request failed",
                    output_files=[],
                    output_file_contents={},
                    snapshot=None,
                    action_history=[],
                    suggestions=[],
                    completeness=None,
                    step_file=None,
                )
            ]

    async def query_snapshot(
        self,
        session_id: str,
        step: int | None = None,
        include_history: bool = False,
        timeout: int = 30,
    ) -> SnapshotQueryResult:
        arguments: dict[str, Any] = {
            "session_id": session_id,
            "include_history": include_history,
        }
        if step is not None:
            arguments["step"] = step

        try:
            call_result = await asyncio.wait_for(
                self._call_named_tool(
                    tool_name=DEFAULT_QUERY_SNAPSHOT_TOOL_NAME,
                    arguments=arguments,
                ),
                timeout=timeout + self._timeout_buffer_seconds,
            )
        except TimeoutError:
            logger.warning(
                "sandbox_mcp_query_snapshot_timeout",
                timeout=timeout,
                session_id=session_id,
            )
            return SnapshotQueryResult(
                success=False,
                error_code="timeout",
                error_message=f"query_snapshot timed out after {timeout} seconds",
                session_id=session_id,
                step=step,
                snapshot=None,
                action_history=[],
            )
        except Exception as exc:
            logger.warning(
                "sandbox_mcp_query_snapshot_failed",
                session_id=session_id,
                error=str(exc),
                exc_info=True,
            )
            return SnapshotQueryResult(
                success=False,
                error_code="execution_error",
                error_message=f"query_snapshot failed: {exc}",
                session_id=session_id,
                step=step,
                snapshot=None,
                action_history=[],
            )

        return self._map_snapshot_query_result(call_result, session_id=session_id)

    async def query_geometry(
        self,
        session_id: str,
        step: int | None = None,
        include_solids: bool = True,
        include_faces: bool = False,
        include_edges: bool = False,
        max_items_per_type: int = 25,
        entity_ids: list[str] | None = None,
        solid_offset: int = 0,
        face_offset: int = 0,
        edge_offset: int = 0,
        timeout: int = 30,
    ) -> GeometryQueryResult:
        arguments: dict[str, Any] = {
            "session_id": session_id,
            "include_solids": include_solids,
            "include_faces": include_faces,
            "include_edges": include_edges,
            "max_items_per_type": max_items_per_type,
            "entity_ids": entity_ids or [],
            "solid_offset": solid_offset,
            "face_offset": face_offset,
            "edge_offset": edge_offset,
        }
        if step is not None:
            arguments["step"] = step

        try:
            call_result = await asyncio.wait_for(
                self._call_named_tool(
                    tool_name=DEFAULT_QUERY_GEOMETRY_TOOL_NAME,
                    arguments=arguments,
                ),
                timeout=timeout + self._timeout_buffer_seconds,
            )
        except TimeoutError:
            logger.warning(
                "sandbox_mcp_query_geometry_timeout",
                timeout=timeout,
                session_id=session_id,
            )
            return GeometryQueryResult(
                success=False,
                error_code="timeout",
                error_message=f"query_geometry timed out after {timeout} seconds",
                session_id=session_id,
                step=step,
                geometry=None,
                features=[],
                issues=[],
                object_index=None,
                matched_entity_ids=[],
                next_solid_offset=None,
                next_face_offset=None,
                next_edge_offset=None,
            )
        except Exception as exc:
            logger.warning(
                "sandbox_mcp_query_geometry_failed",
                session_id=session_id,
                error=str(exc),
                exc_info=True,
            )
            return GeometryQueryResult(
                success=False,
                error_code="execution_error",
                error_message=f"query_geometry failed: {exc}",
                session_id=session_id,
                step=step,
                geometry=None,
                features=[],
                issues=[],
                object_index=None,
                matched_entity_ids=[],
                next_solid_offset=None,
                next_face_offset=None,
                next_edge_offset=None,
            )

        return self._map_geometry_query_result(call_result, session_id=session_id)

    async def query_sketch(
        self,
        session_id: str,
        step: int | None = None,
        timeout: int = 30,
    ) -> SketchQueryResult:
        arguments: dict[str, Any] = {
            "session_id": session_id,
        }
        if step is not None:
            arguments["step"] = step

        try:
            call_result = await asyncio.wait_for(
                self._call_named_tool(
                    tool_name=DEFAULT_QUERY_SKETCH_TOOL_NAME,
                    arguments=arguments,
                ),
                timeout=timeout + self._timeout_buffer_seconds,
            )
        except TimeoutError:
            logger.warning(
                "sandbox_mcp_query_sketch_timeout",
                timeout=timeout,
                session_id=session_id,
            )
            return SketchQueryResult(
                success=False,
                error_code="timeout",
                error_message=f"query_sketch timed out after {timeout} seconds",
                session_id=session_id,
                step=step,
                sketch_state=None,
                relation_index=None,
            )
        except Exception as exc:
            logger.warning(
                "sandbox_mcp_query_sketch_failed",
                session_id=session_id,
                error=str(exc),
                exc_info=True,
            )
            return SketchQueryResult(
                success=False,
                error_code="execution_error",
                error_message=f"query_sketch failed: {exc}",
                session_id=session_id,
                step=step,
                sketch_state=None,
                relation_index=None,
            )

        return self._map_sketch_query_result(call_result, session_id=session_id)

    async def query_topology(
        self,
        session_id: str,
        step: int | None = None,
        include_faces: bool = True,
        include_edges: bool = True,
        max_items_per_type: int = 20,
        entity_ids: list[str] | None = None,
        ref_ids: list[str] | None = None,
        selection_hints: list[str] | None = None,
        requirement_text: str | None = None,
        face_offset: int = 0,
        edge_offset: int = 0,
        timeout: int = 30,
    ) -> TopologyQueryResult:
        arguments: dict[str, Any] = {
            "session_id": session_id,
            "include_faces": include_faces,
            "include_edges": include_edges,
            "max_items_per_type": max_items_per_type,
            "entity_ids": entity_ids or [],
            "ref_ids": ref_ids or [],
            "selection_hints": selection_hints or [],
            "face_offset": face_offset,
            "edge_offset": edge_offset,
        }
        if step is not None:
            arguments["step"] = step
        if requirement_text and requirement_text.strip():
            arguments["requirement_text"] = requirement_text.strip()

        try:
            call_result = await asyncio.wait_for(
                self._call_named_tool(
                    tool_name=DEFAULT_QUERY_TOPOLOGY_TOOL_NAME,
                    arguments=arguments,
                ),
                timeout=timeout + self._timeout_buffer_seconds,
            )
        except TimeoutError:
            logger.warning(
                "sandbox_mcp_query_topology_timeout",
                timeout=timeout,
                session_id=session_id,
            )
            return TopologyQueryResult(
                success=False,
                error_code="timeout",
                error_message=f"query_topology timed out after {timeout} seconds",
                session_id=session_id,
                step=step,
                topology_index=None,
                matched_entity_ids=[],
                matched_ref_ids=[],
                candidate_sets=[],
                applied_hints=[],
                relation_index=None,
                next_face_offset=None,
                next_edge_offset=None,
            )
        except Exception as exc:
            logger.warning(
                "sandbox_mcp_query_topology_failed",
                session_id=session_id,
                error=str(exc),
                exc_info=True,
            )
            return TopologyQueryResult(
                success=False,
                error_code="execution_error",
                error_message=f"query_topology failed: {exc}",
                session_id=session_id,
                step=step,
                topology_index=None,
                matched_entity_ids=[],
                matched_ref_ids=[],
                candidate_sets=[],
                applied_hints=[],
                relation_index=None,
                next_face_offset=None,
                next_edge_offset=None,
            )

        return self._map_topology_query_result(call_result, session_id=session_id)

    async def query_feature_probes(
        self,
        session_id: str,
        requirements: dict[str, Any] | None = None,
        requirement_text: str | None = None,
        step: int | None = None,
        families: list[str] | None = None,
        timeout: int = 30,
    ) -> FeatureProbeQueryResult:
        arguments: dict[str, Any] = {
            "session_id": session_id,
            "requirements": requirements or {},
            "families": families or [],
        }
        if requirement_text and requirement_text.strip():
            arguments["requirement_text"] = requirement_text.strip()
        if step is not None:
            arguments["step"] = step

        try:
            call_result = await asyncio.wait_for(
                self._call_named_tool(
                    tool_name=DEFAULT_QUERY_FEATURE_PROBES_TOOL_NAME,
                    arguments=arguments,
                ),
                timeout=timeout + self._timeout_buffer_seconds,
            )
        except TimeoutError:
            logger.warning(
                "sandbox_mcp_query_feature_probes_timeout",
                timeout=timeout,
                session_id=session_id,
            )
            return FeatureProbeQueryResult(
                success=False,
                error_code="timeout",
                error_message=f"query_feature_probes timed out after {timeout} seconds",
                session_id=session_id,
                step=step,
                detected_families=[],
                probes=[],
                summary="Feature probes timed out",
            )
        except Exception as exc:
            logger.warning(
                "sandbox_mcp_query_feature_probes_failed",
                session_id=session_id,
                error=str(exc),
                exc_info=True,
            )
            return FeatureProbeQueryResult(
                success=False,
                error_code="execution_error",
                error_message=f"query_feature_probes failed: {exc}",
                session_id=session_id,
                step=step,
                detected_families=[],
                probes=[],
                summary="Feature probes failed",
            )

        return self._map_feature_probe_query_result(call_result, session_id=session_id)

    async def execute_build123d_probe(
        self,
        code: str,
        *,
        session_id: str | None = None,
        requirement_text: str | None = None,
        timeout: int = 120,
        include_artifact_content: bool = True,
    ) -> Build123dProbeResult:
        arguments: dict[str, Any] = {
            "code": code,
            "timeout_seconds": timeout,
            "include_artifact_content": include_artifact_content,
        }
        if session_id is not None:
            arguments["session_id"] = session_id
        if requirement_text and requirement_text.strip():
            arguments["requirement_text"] = requirement_text.strip()

        try:
            call_result = await asyncio.wait_for(
                self._call_named_tool(
                    tool_name=DEFAULT_EXECUTE_BUILD123D_PROBE_TOOL_NAME,
                    arguments=arguments,
                ),
                timeout=timeout + self._timeout_buffer_seconds,
            )
        except TimeoutError:
            logger.warning(
                "sandbox_mcp_execute_build123d_probe_timeout",
                timeout=timeout,
                session_id=session_id,
            )
            return Build123dProbeResult(
                success=False,
                stdout="",
                stderr=f"execute_build123d_probe timed out after {timeout} seconds",
                error_message="Timeout",
                output_files=[],
                output_file_contents={},
                session_id=session_id,
                step=None,
                step_file=None,
                probe_summary={},
                session_state_persisted=False,
            )
        except Exception as exc:
            logger.warning(
                "sandbox_mcp_execute_build123d_probe_failed",
                session_id=session_id,
                error=str(exc),
                exc_info=True,
            )
            return Build123dProbeResult(
                success=False,
                stdout="",
                stderr=f"execute_build123d_probe failed: {exc}",
                error_message="MCP request failed",
                output_files=[],
                output_file_contents={},
                session_id=session_id,
                step=None,
                step_file=None,
                probe_summary={},
                session_state_persisted=False,
            )

        return self._map_execute_build123d_probe_result(
            call_result,
            session_id=session_id,
        )

    async def render_view(
        self,
        session_id: str,
        step: int | None = None,
        azimuth_deg: float = 35.0,
        elevation_deg: float = 25.0,
        zoom: float = 1.0,
        width_px: int = 960,
        height_px: int = 720,
        style: str = "shaded",
        target_entity_ids: list[str] | None = None,
        focus_center: list[float] | None = None,
        focus_span: float | None = None,
        focus_padding_ratio: float = 0.15,
        include_artifact_content: bool = True,
        timeout: int = 90,
    ) -> RenderViewResult:
        arguments: dict[str, Any] = {
            "session_id": session_id,
            "azimuth_deg": azimuth_deg,
            "elevation_deg": elevation_deg,
            "zoom": zoom,
            "width_px": width_px,
            "height_px": height_px,
            "style": style,
            "target_entity_ids": target_entity_ids or [],
            "focus_center": focus_center,
            "focus_span": focus_span,
            "focus_padding_ratio": focus_padding_ratio,
            "include_artifact_content": include_artifact_content,
            "timeout_seconds": timeout,
        }
        if step is not None:
            arguments["step"] = step

        try:
            call_result = await asyncio.wait_for(
                self._call_named_tool(
                    tool_name=DEFAULT_RENDER_VIEW_TOOL_NAME,
                    arguments=arguments,
                ),
                timeout=timeout + self._timeout_buffer_seconds,
            )
        except TimeoutError:
            logger.warning(
                "sandbox_mcp_render_view_timeout",
                timeout=timeout,
                session_id=session_id,
            )
            return RenderViewResult(
                success=False,
                error_code="timeout",
                error_message=f"render_view timed out after {timeout} seconds",
                session_id=session_id,
                step=step,
                view_file=None,
                output_files=[],
                output_file_contents={},
                camera={},
                focused_entity_ids=[],
                focus_bbox=None,
            )
        except Exception as exc:
            logger.warning(
                "sandbox_mcp_render_view_failed",
                session_id=session_id,
                error=str(exc),
                exc_info=True,
            )
            return RenderViewResult(
                success=False,
                error_code="execution_error",
                error_message=f"render_view failed: {exc}",
                session_id=session_id,
                step=step,
                view_file=None,
                output_files=[],
                output_file_contents={},
                camera={},
                focused_entity_ids=[],
                focus_bbox=None,
            )

        return self._map_render_view_result(call_result, session_id=session_id)

    async def validate_requirement(
        self,
        session_id: str,
        requirements: dict[str, Any] | None = None,
        requirement_text: str | None = None,
        step: int | None = None,
        timeout: int = 30,
    ) -> RequirementValidationResult:
        arguments: dict[str, Any] = {
            "session_id": session_id,
            "requirements": requirements or {},
        }
        if requirement_text is not None and requirement_text.strip():
            arguments["requirement_text"] = requirement_text.strip()
        if step is not None:
            arguments["step"] = step

        try:
            call_result = await asyncio.wait_for(
                self._call_named_tool(
                    tool_name=DEFAULT_VALIDATE_REQUIREMENT_TOOL_NAME,
                    arguments=arguments,
                ),
                timeout=timeout + self._timeout_buffer_seconds,
            )
        except TimeoutError:
            logger.warning(
                "sandbox_mcp_validate_requirement_timeout",
                timeout=timeout,
                session_id=session_id,
            )
            return RequirementValidationResult(
                success=False,
                error_code="timeout",
                error_message=f"validate_requirement timed out after {timeout} seconds",
                session_id=session_id,
                step=step,
                is_complete=False,
                blockers=["timeout"],
                checks=[],
                core_checks=[],
                diagnostic_checks=[],
                relation_index=None,
                summary="Validation timed out",
            )
        except Exception as exc:
            logger.warning(
                "sandbox_mcp_validate_requirement_failed",
                session_id=session_id,
                error=str(exc),
                exc_info=True,
            )
            return RequirementValidationResult(
                success=False,
                error_code="execution_error",
                error_message=f"validate_requirement failed: {exc}",
                session_id=session_id,
                step=step,
                is_complete=False,
                blockers=["execution_error"],
                checks=[],
                core_checks=[],
                diagnostic_checks=[],
                relation_index=None,
                summary="Validation failed",
            )

        return self._map_requirement_validation_result(
            call_result,
            session_id=session_id,
        )

    async def get_history(
        self,
        session_id: str,
        include_history: bool = True,
        timeout: int = 30,
    ) -> CADActionResult:
        arguments: dict[str, Any] = {
            "session_id": session_id,
            "include_history": include_history,
        }
        try:
            call_result = await asyncio.wait_for(
                self._call_named_tool(
                    tool_name=DEFAULT_GET_HISTORY_TOOL_NAME,
                    arguments=arguments,
                ),
                timeout=timeout + self._timeout_buffer_seconds,
            )
        except TimeoutError:
            logger.warning(
                "sandbox_mcp_get_history_timeout",
                timeout=timeout,
                session_id=session_id,
            )
            return CADActionResult(
                success=False,
                stdout="",
                stderr=f"get_history timed out after {timeout} seconds",
                error_message="Timeout",
                output_files=[],
                output_file_contents={},
                snapshot=None,
                action_history=[],
                suggestions=[],
                completeness=None,
                step_file=None,
            )
        except Exception as exc:
            logger.warning(
                "sandbox_mcp_get_history_failed",
                session_id=session_id,
                error=str(exc),
                exc_info=True,
            )
            return CADActionResult(
                success=False,
                stdout="",
                stderr=f"get_history failed: {exc}",
                error_message="MCP request failed",
                output_files=[],
                output_file_contents={},
                snapshot=None,
                action_history=[],
                suggestions=[],
                completeness=None,
                step_file=None,
            )
        return self._map_action_call_result(call_result)

    async def _call_action_sequence(
        self,
        actions: list[dict[str, Any]],
        session_id: str | None,
        timeout: int,
        include_artifact_content: bool,
        clear_session: bool,
    ) -> list[CADActionResult]:
        arguments: dict[str, Any] = {
            "timeout_seconds": timeout,
            "include_artifact_content": include_artifact_content,
        }
        if session_id is not None:
            arguments["session_id"] = session_id

        params = StdioServerParameters(
            command=self._command,
            args=self._args,
            cwd=self._cwd,
            env=self._env,
        )
        mapped_results: list[CADActionResult] = []
        should_clear_session = clear_session

        async with stdio_client(params) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                for action in actions:
                    action_arguments = dict(arguments)
                    action_arguments["action_type"] = action["action_type"]
                    action_arguments["action_params"] = action["action_params"]
                    action_arguments["clear_session"] = should_clear_session
                    should_clear_session = False

                    call_result = await session.call_tool(
                        DEFAULT_APPLY_CAD_ACTION_TOOL_NAME,
                        action_arguments,
                    )
                    mapped = self._map_action_call_result(call_result)
                    mapped_results.append(mapped)
                    if not mapped.success:
                        break

        return mapped_results

    async def _call_tool(self, arguments: dict[str, Any]) -> Any:
        return await self._call_named_tool(
            tool_name=self._tool_name, arguments=arguments
        )

    async def _call_named_tool(self, tool_name: str, arguments: dict[str, Any]) -> Any:
        params = StdioServerParameters(
            command=self._command,
            args=self._args,
            cwd=self._cwd,
            env=self._env,
        )

        async with stdio_client(params) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                return await session.call_tool(tool_name, arguments)

    def _map_call_result(self, call_result: Any) -> SandboxResult:
        structured = getattr(call_result, "structuredContent", None)
        if not isinstance(structured, dict):
            logger.warning("sandbox_mcp_invalid_payload", payload_type=type(structured))
            return SandboxResult(
                success=False,
                stdout="",
                stderr="Sandbox MCP returned invalid structured payload",
                output_files=[],
                output_file_contents={},
                error_message="Invalid payload",
            )

        success = bool(structured.get("success"))
        stdout = self._as_string(structured.get("stdout"))
        stderr = self._as_string(structured.get("stderr"))
        error_message = self._as_optional_string(structured.get("error_message"))
        evaluation = self._parse_evaluation(structured.get("evaluation"))
        session_id = self._as_optional_string(structured.get("session_id"))
        step = structured.get("step")
        if not isinstance(step, int):
            step = None
        step_file = self._as_optional_string(structured.get("step_file"))
        snapshot = structured.get("snapshot")
        if not isinstance(snapshot, dict):
            snapshot = None
        session_state_persisted = bool(structured.get("session_state_persisted", False))

        output_files = self._parse_output_files(structured.get("output_files"))
        artifact_files, output_file_contents, artifact_error = self._parse_artifacts(
            structured.get("artifacts")
        )

        if artifact_error is not None:
            logger.warning("sandbox_mcp_invalid_artifacts", error=artifact_error)
            return SandboxResult(
                success=False,
                stdout=stdout,
                stderr=stderr or artifact_error,
                output_files=output_files or artifact_files,
                output_file_contents={},
                error_message="Invalid output files",
            )

        merged_files = list(dict.fromkeys([*output_files, *artifact_files]))
        is_error = bool(getattr(call_result, "isError", False))

        if is_error:
            return SandboxResult(
                success=False,
                stdout=stdout,
                stderr=stderr or "Sandbox MCP tool returned an error",
                output_files=merged_files,
                output_file_contents=output_file_contents,
                error_message=error_message or "MCP tool returned an error",
                evaluation=evaluation,
                session_id=session_id,
                step=step,
                step_file=step_file,
                snapshot=snapshot,
                session_state_persisted=session_state_persisted,
            )

        return SandboxResult(
            success=success,
            stdout=stdout,
            stderr=stderr,
            output_files=merged_files,
            output_file_contents=output_file_contents,
            error_message=error_message,
            evaluation=evaluation,
            session_id=session_id,
            step=step,
            step_file=step_file,
            snapshot=snapshot,
            session_state_persisted=session_state_persisted,
        )

    def _map_action_call_result(self, call_result: Any) -> CADActionResult:
        structured = getattr(call_result, "structuredContent", None)
        if not isinstance(structured, dict):
            logger.warning(
                "sandbox_mcp_invalid_action_payload",
                payload_type=type(structured),
            )
            return CADActionResult(
                success=False,
                stdout="",
                stderr="Sandbox MCP returned invalid action payload",
                error_message="Invalid payload",
                output_files=[],
                output_file_contents={},
                snapshot=None,
                action_history=[],
                suggestions=[],
                completeness=None,
                step_file=None,
            )

        stdout = self._as_string(structured.get("stdout"))
        stderr = self._as_string(structured.get("stderr"))
        error_message = self._as_optional_string(structured.get("error_message"))
        output_files = self._parse_output_files(structured.get("output_files"))
        artifact_files, output_file_contents, artifact_error = self._parse_artifacts(
            structured.get("artifacts")
        )

        if artifact_error is not None:
            logger.warning("sandbox_mcp_invalid_action_artifacts", error=artifact_error)
            return CADActionResult(
                success=False,
                stdout=stdout,
                stderr=stderr or artifact_error,
                error_message="Invalid output files",
                output_files=output_files or artifact_files,
                output_file_contents={},
                snapshot=(
                    structured.get("snapshot")
                    if isinstance(structured.get("snapshot"), dict)
                    else None
                ),
                action_history=[],
                suggestions=[],
                completeness=None,
                step_file=None,
            )

        history = structured.get("action_history")
        action_history: list[dict[str, Any]]
        if isinstance(history, list):
            action_history = [item for item in history if isinstance(item, dict)]
        else:
            action_history = []

        suggestions_raw = structured.get("suggestions")
        suggestions: list[str]
        if isinstance(suggestions_raw, list):
            suggestions = [item for item in suggestions_raw if isinstance(item, str)]
        else:
            suggestions = []

        completeness_raw = structured.get("completeness")
        completeness = completeness_raw if isinstance(completeness_raw, dict) else None
        snapshot_raw = structured.get("snapshot")
        snapshot = snapshot_raw if isinstance(snapshot_raw, dict) else None
        step_file_raw = structured.get("step_file")
        step_file = step_file_raw if isinstance(step_file_raw, str) else None

        merged_files = list(dict.fromkeys([*output_files, *artifact_files]))
        is_error = bool(getattr(call_result, "isError", False))
        success = bool(structured.get("success")) and not is_error

        if is_error and not error_message:
            error_message = "MCP tool returned an error"
        if is_error and not stderr:
            stderr = "Sandbox MCP apply_cad_action returned an error"

        return CADActionResult(
            success=success,
            stdout=stdout,
            stderr=stderr,
            error_message=error_message,
            output_files=merged_files,
            output_file_contents=output_file_contents,
            snapshot=snapshot,
            action_history=action_history,
            suggestions=suggestions,
            completeness=completeness,
            step_file=step_file,
        )

    def _map_snapshot_query_result(
        self,
        call_result: Any,
        session_id: str,
    ) -> SnapshotQueryResult:
        structured = getattr(call_result, "structuredContent", None)
        if not isinstance(structured, dict):
            logger.warning(
                "sandbox_mcp_invalid_query_snapshot_payload",
                payload_type=type(structured),
            )
            return SnapshotQueryResult(
                success=False,
                error_code="invalid_payload",
                error_message="Sandbox MCP returned invalid query_snapshot payload",
                session_id=session_id,
                step=None,
                snapshot=None,
                action_history=[],
            )

        history_raw = structured.get("action_history")
        action_history: list[dict[str, Any]]
        if isinstance(history_raw, list):
            action_history = [item for item in history_raw if isinstance(item, dict)]
        else:
            action_history = []

        snapshot_raw = structured.get("snapshot")
        snapshot = snapshot_raw if isinstance(snapshot_raw, dict) else None
        step_raw = structured.get("step")
        resolved_step = step_raw if isinstance(step_raw, int) else None

        error_code = self._as_string(structured.get("error_code")) or "none"
        error_message = self._as_optional_string(structured.get("error_message"))
        is_error = bool(getattr(call_result, "isError", False))

        return SnapshotQueryResult(
            success=bool(structured.get("success")) and not is_error,
            error_code=error_code,
            error_message=error_message,
            session_id=self._as_string(structured.get("session_id")) or session_id,
            step=resolved_step,
            snapshot=snapshot,
            action_history=action_history,
        )

    def _map_geometry_query_result(
        self,
        call_result: Any,
        session_id: str,
    ) -> GeometryQueryResult:
        structured = getattr(call_result, "structuredContent", None)
        if not isinstance(structured, dict):
            logger.warning(
                "sandbox_mcp_invalid_query_geometry_payload",
                payload_type=type(structured),
            )
            return GeometryQueryResult(
                success=False,
                error_code="invalid_payload",
                error_message="Sandbox MCP returned invalid query_geometry payload",
                session_id=session_id,
                step=None,
                geometry=None,
                features=[],
                issues=[],
                object_index=None,
                matched_entity_ids=[],
                next_solid_offset=None,
                next_face_offset=None,
                next_edge_offset=None,
            )

        geometry_raw = structured.get("geometry")
        geometry = geometry_raw if isinstance(geometry_raw, dict) else None

        features_raw = structured.get("features")
        features = (
            [item for item in features_raw if isinstance(item, str)]
            if isinstance(features_raw, list)
            else []
        )
        issues_raw = structured.get("issues")
        issues = (
            [item for item in issues_raw if isinstance(item, str)]
            if isinstance(issues_raw, list)
            else []
        )
        object_index_raw = structured.get("object_index")
        object_index = object_index_raw if isinstance(object_index_raw, dict) else None
        matched_ids_raw = structured.get("matched_entity_ids")
        matched_entity_ids = (
            [item for item in matched_ids_raw if isinstance(item, str)]
            if isinstance(matched_ids_raw, list)
            else []
        )
        next_solid_offset_raw = structured.get("next_solid_offset")
        next_face_offset_raw = structured.get("next_face_offset")
        next_edge_offset_raw = structured.get("next_edge_offset")
        step_raw = structured.get("step")
        resolved_step = step_raw if isinstance(step_raw, int) else None
        error_code = self._as_string(structured.get("error_code")) or "none"
        error_message = self._as_optional_string(structured.get("error_message"))
        is_error = bool(getattr(call_result, "isError", False))

        return GeometryQueryResult(
            success=bool(structured.get("success")) and not is_error,
            error_code=error_code,
            error_message=error_message,
            session_id=self._as_string(structured.get("session_id")) or session_id,
            step=resolved_step,
            geometry=geometry,
            features=features,
            issues=issues,
            object_index=object_index,
            matched_entity_ids=matched_entity_ids,
            next_solid_offset=(
                int(next_solid_offset_raw)
                if isinstance(next_solid_offset_raw, int)
                else None
            ),
            next_face_offset=(
                int(next_face_offset_raw)
                if isinstance(next_face_offset_raw, int)
                else None
            ),
            next_edge_offset=(
                int(next_edge_offset_raw)
                if isinstance(next_edge_offset_raw, int)
                else None
            ),
        )

    def _map_sketch_query_result(
        self,
        call_result: Any,
        session_id: str,
    ) -> SketchQueryResult:
        structured = getattr(call_result, "structuredContent", None)
        if not isinstance(structured, dict):
            logger.warning(
                "sandbox_mcp_invalid_query_sketch_payload",
                payload_type=type(structured),
            )
            return SketchQueryResult(
                success=False,
                error_code="invalid_payload",
                error_message="Sandbox MCP returned invalid query_sketch payload",
                session_id=session_id,
                step=None,
                sketch_state=None,
                relation_index=None,
            )

        sketch_state_raw = structured.get("sketch_state")
        sketch_state = sketch_state_raw if isinstance(sketch_state_raw, dict) else None
        relation_index_raw = structured.get("relation_index")
        relation_index = (
            relation_index_raw if isinstance(relation_index_raw, dict) else None
        )
        step_raw = structured.get("step")
        resolved_step = step_raw if isinstance(step_raw, int) else None
        error_code = self._as_string(structured.get("error_code")) or "none"
        error_message = self._as_optional_string(structured.get("error_message"))
        is_error = bool(getattr(call_result, "isError", False))

        return SketchQueryResult(
            success=bool(structured.get("success")) and not is_error,
            error_code=error_code,
            error_message=error_message,
            session_id=self._as_string(structured.get("session_id")) or session_id,
            step=resolved_step,
            sketch_state=sketch_state,
            relation_index=relation_index,
        )

    def _map_topology_query_result(
        self,
        call_result: Any,
        session_id: str,
    ) -> TopologyQueryResult:
        structured = getattr(call_result, "structuredContent", None)
        if not isinstance(structured, dict):
            logger.warning(
                "sandbox_mcp_invalid_query_topology_payload",
                payload_type=type(structured),
            )
            return TopologyQueryResult(
                success=False,
                error_code="invalid_payload",
                error_message="Sandbox MCP returned invalid query_topology payload",
                session_id=session_id,
                step=None,
                topology_index=None,
                matched_entity_ids=[],
                matched_ref_ids=[],
                candidate_sets=[],
                applied_hints=[],
                relation_index=None,
                next_face_offset=None,
                next_edge_offset=None,
            )

        topology_raw = structured.get("topology_index")
        topology_index = topology_raw if isinstance(topology_raw, dict) else None
        matched_ids_raw = structured.get("matched_entity_ids")
        matched_entity_ids = (
            [item for item in matched_ids_raw if isinstance(item, str)]
            if isinstance(matched_ids_raw, list)
            else []
        )
        matched_refs_raw = structured.get("matched_ref_ids")
        matched_ref_ids = (
            [item for item in matched_refs_raw if isinstance(item, str)]
            if isinstance(matched_refs_raw, list)
            else []
        )
        candidate_sets_raw = structured.get("candidate_sets")
        candidate_sets = (
            [item for item in candidate_sets_raw if isinstance(item, dict)]
            if isinstance(candidate_sets_raw, list)
            else []
        )
        relation_index_raw = structured.get("relation_index")
        relation_index = (
            relation_index_raw if isinstance(relation_index_raw, dict) else None
        )
        applied_hints_raw = structured.get("applied_hints")
        applied_hints = (
            [item for item in applied_hints_raw if isinstance(item, str)]
            if isinstance(applied_hints_raw, list)
            else []
        )

        next_face_offset_raw = structured.get("next_face_offset")
        next_edge_offset_raw = structured.get("next_edge_offset")
        step_raw = structured.get("step")
        resolved_step = step_raw if isinstance(step_raw, int) else None
        error_code = self._as_string(structured.get("error_code")) or "none"
        error_message = self._as_optional_string(structured.get("error_message"))
        is_error = bool(getattr(call_result, "isError", False))

        return TopologyQueryResult(
            success=bool(structured.get("success")) and not is_error,
            error_code=error_code,
            error_message=error_message,
            session_id=self._as_string(structured.get("session_id")) or session_id,
            step=resolved_step,
            topology_index=topology_index,
            matched_entity_ids=matched_entity_ids,
            matched_ref_ids=matched_ref_ids,
            candidate_sets=candidate_sets,
            applied_hints=applied_hints,
            relation_index=relation_index,
            next_face_offset=(
                int(next_face_offset_raw)
                if isinstance(next_face_offset_raw, int)
                else None
            ),
            next_edge_offset=(
                int(next_edge_offset_raw)
                if isinstance(next_edge_offset_raw, int)
                else None
            ),
        )

    def _map_feature_probe_query_result(
        self,
        call_result: Any,
        session_id: str,
    ) -> FeatureProbeQueryResult:
        structured = getattr(call_result, "structuredContent", None)
        if not isinstance(structured, dict):
            logger.warning(
                "sandbox_mcp_invalid_query_feature_probes_payload",
                payload_type=type(structured),
            )
            return FeatureProbeQueryResult(
                success=False,
                error_code="invalid_payload",
                error_message="Sandbox MCP returned invalid query_feature_probes payload",
                session_id=session_id,
                step=None,
                detected_families=[],
                probes=[],
                summary="Feature probe payload is invalid",
            )

        detected_families_raw = structured.get("detected_families")
        detected_families = (
            [item for item in detected_families_raw if isinstance(item, str)]
            if isinstance(detected_families_raw, list)
            else []
        )
        probes_raw = structured.get("probes")
        probes = (
            [item for item in probes_raw if isinstance(item, dict)]
            if isinstance(probes_raw, list)
            else []
        )
        step_raw = structured.get("step")
        resolved_step = step_raw if isinstance(step_raw, int) else None
        error_code = self._as_string(structured.get("error_code")) or "none"
        error_message = self._as_optional_string(structured.get("error_message"))
        summary = self._as_string(structured.get("summary"))
        is_error = bool(getattr(call_result, "isError", False))

        return FeatureProbeQueryResult(
            success=bool(structured.get("success")) and not is_error,
            error_code=error_code,
            error_message=error_message,
            session_id=self._as_string(structured.get("session_id")) or session_id,
            step=resolved_step,
            detected_families=detected_families,
            probes=probes,
            summary=summary,
        )

    def _map_render_view_result(
        self,
        call_result: Any,
        session_id: str,
    ) -> RenderViewResult:
        structured = getattr(call_result, "structuredContent", None)
        if not isinstance(structured, dict):
            logger.warning(
                "sandbox_mcp_invalid_render_view_payload",
                payload_type=type(structured),
            )
            return RenderViewResult(
                success=False,
                error_code="invalid_payload",
                error_message="Sandbox MCP returned invalid render_view payload",
                session_id=session_id,
                step=None,
                view_file=None,
                output_files=[],
                output_file_contents={},
                camera={},
                focused_entity_ids=[],
                focus_bbox=None,
            )

        output_files = self._parse_output_files(structured.get("output_files"))
        artifact_files, output_file_contents, artifact_error = self._parse_artifacts(
            structured.get("artifacts")
        )
        if artifact_error is not None:
            logger.warning(
                "sandbox_mcp_invalid_render_view_artifacts", error=artifact_error
            )
            return RenderViewResult(
                success=False,
                error_code="invalid_payload",
                error_message=artifact_error,
                session_id=session_id,
                step=None,
                view_file=None,
                output_files=[],
                output_file_contents={},
                camera={},
                focused_entity_ids=[],
                focus_bbox=None,
            )

        merged_files = list(dict.fromkeys([*output_files, *artifact_files]))
        camera_raw = structured.get("camera")
        camera = camera_raw if isinstance(camera_raw, dict) else {}
        focused_ids_raw = structured.get("focused_entity_ids")
        focused_entity_ids = (
            [item for item in focused_ids_raw if isinstance(item, str)]
            if isinstance(focused_ids_raw, list)
            else []
        )
        focus_bbox_raw = structured.get("focus_bbox")
        focus_bbox = focus_bbox_raw if isinstance(focus_bbox_raw, dict) else None

        step_raw = structured.get("step")
        resolved_step = step_raw if isinstance(step_raw, int) else None
        error_code = self._as_string(structured.get("error_code")) or "none"
        error_message = self._as_optional_string(structured.get("error_message"))
        view_file_raw = structured.get("view_file")
        view_file = view_file_raw if isinstance(view_file_raw, str) else None
        is_error = bool(getattr(call_result, "isError", False))

        return RenderViewResult(
            success=bool(structured.get("success")) and not is_error,
            error_code=error_code,
            error_message=error_message,
            session_id=self._as_string(structured.get("session_id")) or session_id,
            step=resolved_step,
            view_file=view_file,
            output_files=merged_files,
            output_file_contents=output_file_contents,
            camera=camera,
            focused_entity_ids=focused_entity_ids,
            focus_bbox=focus_bbox,
        )

    def _map_requirement_validation_result(
        self,
        call_result: Any,
        session_id: str,
    ) -> RequirementValidationResult:
        structured = getattr(call_result, "structuredContent", None)
        if not isinstance(structured, dict):
            logger.warning(
                "sandbox_mcp_invalid_validate_requirement_payload",
                payload_type=type(structured),
            )
            return RequirementValidationResult(
                success=False,
                error_code="invalid_payload",
                error_message=(
                    "Sandbox MCP returned invalid validate_requirement payload"
                ),
                session_id=session_id,
                step=None,
                is_complete=False,
                blockers=["invalid_payload"],
                checks=[],
                core_checks=[],
                diagnostic_checks=[],
                relation_index=None,
                summary="Validation payload is invalid",
            )

        blockers_raw = structured.get("blockers")
        blockers = (
            [item for item in blockers_raw if isinstance(item, str)]
            if isinstance(blockers_raw, list)
            else []
        )

        checks_raw = structured.get("checks")
        checks = (
            [item for item in checks_raw if isinstance(item, dict)]
            if isinstance(checks_raw, list)
            else []
        )
        core_checks_raw = structured.get("core_checks")
        core_checks = (
            [item for item in core_checks_raw if isinstance(item, dict)]
            if isinstance(core_checks_raw, list)
            else []
        )
        diagnostic_checks_raw = structured.get("diagnostic_checks")
        diagnostic_checks = (
            [item for item in diagnostic_checks_raw if isinstance(item, dict)]
            if isinstance(diagnostic_checks_raw, list)
            else []
        )
        relation_index_raw = structured.get("relation_index")
        relation_index = (
            relation_index_raw if isinstance(relation_index_raw, dict) else None
        )

        step_raw = structured.get("step")
        resolved_step = step_raw if isinstance(step_raw, int) else None
        error_code = self._as_string(structured.get("error_code")) or "none"
        error_message = self._as_optional_string(structured.get("error_message"))
        summary = self._as_string(structured.get("summary"))
        is_error = bool(getattr(call_result, "isError", False))

        return RequirementValidationResult(
            success=bool(structured.get("success")) and not is_error,
            error_code=error_code,
            error_message=error_message,
            session_id=self._as_string(structured.get("session_id")) or session_id,
            step=resolved_step,
            is_complete=bool(structured.get("is_complete")),
            blockers=blockers,
            checks=checks,
            core_checks=core_checks,
            diagnostic_checks=diagnostic_checks,
            relation_index=relation_index,
            summary=summary,
        )

    def _map_execute_build123d_probe_result(
        self,
        call_result: Any,
        session_id: str | None,
    ) -> Build123dProbeResult:
        structured = getattr(call_result, "structuredContent", None)
        if not isinstance(structured, dict):
            logger.warning(
                "sandbox_mcp_invalid_execute_build123d_probe_payload",
                payload_type=type(structured),
            )
            return Build123dProbeResult(
                success=False,
                stdout="",
                stderr="Sandbox MCP returned invalid execute_build123d_probe payload",
                error_message="Invalid payload",
                output_files=[],
                output_file_contents={},
                session_id=session_id,
                step=None,
                step_file=None,
                probe_summary={},
                session_state_persisted=False,
            )

        stdout = self._as_string(structured.get("stdout"))
        stderr = self._as_string(structured.get("stderr"))
        error_message = self._as_optional_string(structured.get("error_message"))
        output_files = self._parse_output_files(structured.get("output_files"))
        artifact_files, output_file_contents, artifact_error = self._parse_artifacts(
            structured.get("artifacts")
        )
        if artifact_error is not None:
            logger.warning(
                "sandbox_mcp_invalid_execute_build123d_probe_artifacts",
                error=artifact_error,
            )
            return Build123dProbeResult(
                success=False,
                stdout=stdout,
                stderr=stderr or artifact_error,
                error_message="Invalid output files",
                output_files=output_files or artifact_files,
                output_file_contents={},
                session_id=session_id,
                step=None,
                step_file=None,
                probe_summary={},
                session_state_persisted=False,
            )

        merged_files = list(dict.fromkeys([*output_files, *artifact_files]))
        step_raw = structured.get("step")
        resolved_step = step_raw if isinstance(step_raw, int) else None
        step_file = self._as_optional_string(structured.get("step_file"))
        probe_summary_raw = structured.get("probe_summary")
        probe_summary = probe_summary_raw if isinstance(probe_summary_raw, dict) else {}
        is_error = bool(getattr(call_result, "isError", False))
        success = bool(structured.get("success")) and not is_error

        return Build123dProbeResult(
            success=success,
            stdout=stdout,
            stderr=stderr,
            error_message=error_message,
            output_files=merged_files,
            output_file_contents=output_file_contents,
            session_id=self._as_optional_string(structured.get("session_id")) or session_id,
            step=resolved_step,
            step_file=step_file,
            probe_summary=probe_summary,
            session_state_persisted=bool(structured.get("session_state_persisted", False)),
        )

    def _parse_output_files(self, raw_output_files: Any) -> list[str]:
        if not isinstance(raw_output_files, list):
            return []
        if any(not isinstance(item, str) for item in raw_output_files):
            return []
        return raw_output_files

    def _parse_artifacts(
        self,
        raw_artifacts: Any,
    ) -> tuple[list[str], dict[str, bytes], str | None]:
        if raw_artifacts is None:
            return [], {}, None

        if not isinstance(raw_artifacts, list):
            return [], {}, "Sandbox MCP artifacts must be a list"

        artifact_files: list[str] = []
        output_file_contents: dict[str, bytes] = {}

        for artifact in raw_artifacts:
            if not isinstance(artifact, dict):
                return [], {}, "Sandbox MCP artifact must be an object"

            filename = artifact.get("filename")
            if not isinstance(filename, str):
                return [], {}, "Sandbox MCP artifact filename must be a string"

            artifact_files.append(filename)

            content_base64 = artifact.get("content_base64")
            if content_base64 is None:
                continue

            if not isinstance(content_base64, str):
                return (
                    [],
                    {},
                    (
                        "Sandbox MCP artifact content_base64 must be a string when provided"
                    ),
                )

            try:
                output_file_contents[filename] = base64.b64decode(
                    content_base64,
                    validate=True,
                )
            except (binascii.Error, ValueError):
                return [], {}, f"Sandbox MCP artifact base64 is invalid for {filename}"

        return artifact_files, output_file_contents, None

    def _as_string(self, value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, str):
            return value
        return str(value)

    def _as_optional_string(self, value: Any) -> str | None:
        if value is None:
            return None
        if isinstance(value, str):
            return value
        return str(value)

    def _parse_evaluation(self, raw_evaluation: Any) -> dict[str, Any] | None:
        if raw_evaluation is None:
            return None
        if isinstance(raw_evaluation, dict):
            return raw_evaluation
        logger.warning(
            "sandbox_mcp_invalid_evaluation_payload",
            payload_type=str(type(raw_evaluation)),
        )
        return None
