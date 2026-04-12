import asyncio
import logging
import sys
from enum import Enum
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import ContentBlock, TextContent, Tool
import structlog

from common.config import settings
from common.logging import get_logger
from llm.factory import create_tiered_llm_client
from llm.interface import LLMTier
from sandbox import SandboxRunner, create_sandbox_runner
from sandbox_mcp_server.contracts import (
    CADActionInput,
    CADActionOutput,
    ExecuteBuild123dInput,
    ExecuteBuild123dProbeInput,
    ExecuteBuild123dProbeOutput,
    ExecuteBuild123dOutput,
    GetHistoryInput,
    QueryFeatureProbesInput,
    QueryFeatureProbesOutput,
    QueryGeometryInput,
    QueryGeometryOutput,
    QuerySketchInput,
    QuerySketchOutput,
    QuerySnapshotInput,
    QuerySnapshotOutput,
    QueryTopologyInput,
    QueryTopologyOutput,
    RenderViewInput,
    RenderViewOutput,
    SandboxErrorCode,
    ValidateRequirementInput,
    ValidateRequirementOutput,
)
from sandbox_mcp_server.evaluation_orchestrator import EvaluationOrchestrator
from sandbox_mcp_server.evidence_builder import EvidenceBuilder
from sandbox_mcp_server.llm_judge import LLMJudgeEvaluator
from sandbox_mcp_server.registry import get_supported_action_types, get_tool_definition
from sandbox_mcp_server.rubric_loader import RubricLoader
from sandbox_mcp_server.rule_evaluator import RuleEvaluator
from sandbox_mcp_server.score_merger import ScoreMerger
from sandbox_mcp_server.service import SandboxMCPService

logger = get_logger(__name__)


class SandboxTools(str, Enum):
    EXECUTE_BUILD123D = "execute_build123d"
    EXECUTE_BUILD123D_PROBE = "execute_build123d_probe"
    APPLY_CAD_ACTION = "apply_cad_action"
    GET_HISTORY = "get_history"
    QUERY_SNAPSHOT = "query_snapshot"
    QUERY_SKETCH = "query_sketch"
    QUERY_GEOMETRY = "query_geometry"
    QUERY_TOPOLOGY = "query_topology"
    QUERY_FEATURE_PROBES = "query_feature_probes"
    RENDER_VIEW = "render_view"
    VALIDATE_REQUIREMENT = "validate_requirement"


def configure_mcp_stdio_logging(log_level: str = "INFO") -> None:
    """Configure structlog to write to stderr to preserve stdout JSON-RPC channel."""
    normalized_level = getattr(logging, log_level.upper(), logging.INFO)

    logging.basicConfig(
        format="%(message)s",
        level=normalized_level,
        stream=sys.stderr,
    )

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(normalized_level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
        cache_logger_on_first_use=True,
    )


def build_execute_build123d_tool() -> Tool:
    definition = get_tool_definition(SandboxTools.EXECUTE_BUILD123D)
    return Tool(
        name=SandboxTools.EXECUTE_BUILD123D,
        description=definition.description if definition is not None else "",
        inputSchema=ExecuteBuild123dInput.model_json_schema(),
        outputSchema=ExecuteBuild123dOutput.model_json_schema(),
    )


def build_apply_cad_action_tool() -> Tool:
    definition = get_tool_definition(SandboxTools.APPLY_CAD_ACTION)
    return Tool(
        name=SandboxTools.APPLY_CAD_ACTION,
        description=(
            f"{definition.description if definition is not None else ''} "
            f"Supported actions: {', '.join(get_supported_action_types())}."
        ).strip(),
        inputSchema=CADActionInput.model_json_schema(),
        outputSchema=CADActionOutput.model_json_schema(),
    )


def build_get_history_tool() -> Tool:
    definition = get_tool_definition(SandboxTools.GET_HISTORY)
    return Tool(
        name=SandboxTools.GET_HISTORY,
        description=definition.description if definition is not None else "",
        inputSchema=GetHistoryInput.model_json_schema(),
        outputSchema=CADActionOutput.model_json_schema(),
    )


def build_query_snapshot_tool() -> Tool:
    definition = get_tool_definition(SandboxTools.QUERY_SNAPSHOT)
    return Tool(
        name=SandboxTools.QUERY_SNAPSHOT,
        description=definition.description if definition is not None else "",
        inputSchema=QuerySnapshotInput.model_json_schema(),
        outputSchema=QuerySnapshotOutput.model_json_schema(),
    )


def build_query_sketch_tool() -> Tool:
    definition = get_tool_definition(SandboxTools.QUERY_SKETCH)
    return Tool(
        name=SandboxTools.QUERY_SKETCH,
        description=definition.description if definition is not None else "",
        inputSchema=QuerySketchInput.model_json_schema(),
        outputSchema=QuerySketchOutput.model_json_schema(),
    )


def build_query_geometry_tool() -> Tool:
    definition = get_tool_definition(SandboxTools.QUERY_GEOMETRY)
    return Tool(
        name=SandboxTools.QUERY_GEOMETRY,
        description=definition.description if definition is not None else "",
        inputSchema=QueryGeometryInput.model_json_schema(),
        outputSchema=QueryGeometryOutput.model_json_schema(),
    )


def build_query_topology_tool() -> Tool:
    definition = get_tool_definition(SandboxTools.QUERY_TOPOLOGY)
    return Tool(
        name=SandboxTools.QUERY_TOPOLOGY,
        description=definition.description if definition is not None else "",
        inputSchema=QueryTopologyInput.model_json_schema(),
        outputSchema=QueryTopologyOutput.model_json_schema(),
    )


def build_query_feature_probes_tool() -> Tool:
    definition = get_tool_definition(SandboxTools.QUERY_FEATURE_PROBES)
    return Tool(
        name=SandboxTools.QUERY_FEATURE_PROBES,
        description=definition.description if definition is not None else "",
        inputSchema=QueryFeatureProbesInput.model_json_schema(),
        outputSchema=QueryFeatureProbesOutput.model_json_schema(),
    )


def build_validate_requirement_tool() -> Tool:
    definition = get_tool_definition(SandboxTools.VALIDATE_REQUIREMENT)
    return Tool(
        name=SandboxTools.VALIDATE_REQUIREMENT,
        description=definition.description if definition is not None else "",
        inputSchema=ValidateRequirementInput.model_json_schema(),
        outputSchema=ValidateRequirementOutput.model_json_schema(),
    )


def build_render_view_tool() -> Tool:
    definition = get_tool_definition(SandboxTools.RENDER_VIEW)
    return Tool(
        name=SandboxTools.RENDER_VIEW,
        description=definition.description if definition is not None else "",
        inputSchema=RenderViewInput.model_json_schema(),
        outputSchema=RenderViewOutput.model_json_schema(),
    )


def build_execute_build123d_probe_tool() -> Tool:
    definition = get_tool_definition(SandboxTools.EXECUTE_BUILD123D_PROBE)
    return Tool(
        name=SandboxTools.EXECUTE_BUILD123D_PROBE,
        description=definition.description if definition is not None else "",
        inputSchema=ExecuteBuild123dProbeInput.model_json_schema(),
        outputSchema=ExecuteBuild123dProbeOutput.model_json_schema(),
    )


async def execute_build123d_tool(
    arguments: dict[str, Any],
    service: SandboxMCPService,
) -> tuple[list[ContentBlock], dict[str, Any]]:
    request = ExecuteBuild123dInput.model_validate(arguments)
    output = await service.execute(request)
    return (
        service.build_unstructured_content(output),
        output.model_dump(mode="json", exclude_none=True),
    )


async def apply_cad_action_tool(
    arguments: dict[str, Any],
    service: SandboxMCPService,
) -> tuple[list[ContentBlock], dict[str, Any]]:
    request = CADActionInput.model_validate(arguments)
    output = await service.apply_cad_action(request)
    return (
        service.build_unstructured_content(output),
        output.model_dump(mode="json", exclude_none=True),
    )


async def get_history_tool(
    arguments: dict[str, Any],
    service: SandboxMCPService,
) -> tuple[list[ContentBlock], dict[str, Any]]:
    request = GetHistoryInput.model_validate(arguments)
    history = service._session_manager.get_session_history(request.session_id)

    if not history:
        empty_output = CADActionOutput(
            success=True,
            stdout="No action history found for this session",
            stderr="",
            error_code=SandboxErrorCode.NONE,
            error_message=None,
            snapshot=service._empty_snapshot(),
            executed_action={"type": "get_history", "params": {}},
            step_file=None,
            output_files=[],
            artifacts=[],
            action_history=[],
            suggestions=[],
            completeness=None,
        )
        return (
            [TextContent(type="text", text="No action history found for this session")],
            empty_output.model_dump(mode="json", exclude_none=True),
        )

    # Return the complete history in the last entry's snapshot format
    last_entry = history[-1]
    output = CADActionOutput(
        success=True,
        stdout=f"Retrieved {len(history)} action entries",
        stderr="",
        error_code=SandboxErrorCode.NONE,
        error_message=None,
        snapshot=last_entry.result_snapshot,
        executed_action={"type": "get_history", "params": {}},
        step_file=None,
        output_files=[],
        artifacts=[],
        action_history=history if request.include_history else [],
        suggestions=["Action history retrieved successfully"],
        completeness=None,
    )
    return (
        service.build_unstructured_content(output),
        output.model_dump(mode="json", exclude_none=True),
    )


async def query_snapshot_tool(
    arguments: dict[str, Any],
    service: SandboxMCPService,
) -> tuple[list[ContentBlock], dict[str, Any]]:
    request = QuerySnapshotInput.model_validate(arguments)
    output = await service.query_snapshot(request)
    return (
        service.build_unstructured_content(output),
        output.model_dump(mode="json", exclude_none=True),
    )


async def query_sketch_tool(
    arguments: dict[str, Any],
    service: SandboxMCPService,
) -> tuple[list[ContentBlock], dict[str, Any]]:
    request = QuerySketchInput.model_validate(arguments)
    output = await service.query_sketch(request)
    return (
        service.build_unstructured_content(output),
        output.model_dump(mode="json", exclude_none=True),
    )


async def query_geometry_tool(
    arguments: dict[str, Any],
    service: SandboxMCPService,
) -> tuple[list[ContentBlock], dict[str, Any]]:
    request = QueryGeometryInput.model_validate(arguments)
    output = await service.query_geometry(request)
    return (
        service.build_unstructured_content(output),
        output.model_dump(mode="json", exclude_none=True),
    )


async def query_topology_tool(
    arguments: dict[str, Any],
    service: SandboxMCPService,
) -> tuple[list[ContentBlock], dict[str, Any]]:
    request = QueryTopologyInput.model_validate(arguments)
    output = await service.query_topology(request)
    return (
        service.build_unstructured_content(output),
        output.model_dump(mode="json", exclude_none=True),
    )


async def query_feature_probes_tool(
    arguments: dict[str, Any],
    service: SandboxMCPService,
) -> tuple[list[ContentBlock], dict[str, Any]]:
    request = QueryFeatureProbesInput.model_validate(arguments)
    output = await service.query_feature_probes(request)
    return (
        service.build_unstructured_content(output),
        output.model_dump(mode="json", exclude_none=True),
    )


async def validate_requirement_tool(
    arguments: dict[str, Any],
    service: SandboxMCPService,
) -> tuple[list[ContentBlock], dict[str, Any]]:
    request = ValidateRequirementInput.model_validate(arguments)
    output = await service.validate_requirement(request)
    return (
        service.build_unstructured_content(output),
        output.model_dump(mode="json", exclude_none=True),
    )


async def execute_build123d_probe_tool(
    arguments: dict[str, Any],
    service: SandboxMCPService,
) -> tuple[list[ContentBlock], dict[str, Any]]:
    request = ExecuteBuild123dProbeInput.model_validate(arguments)
    output = await service.execute_build123d_probe(request)
    return (
        service.build_unstructured_content(output),
        output.model_dump(mode="json", exclude_none=True),
    )


async def render_view_tool(
    arguments: dict[str, Any],
    service: SandboxMCPService,
) -> tuple[list[ContentBlock], dict[str, Any]]:
    request = RenderViewInput.model_validate(arguments)
    output = await service.render_view(request)
    return (
        service.build_unstructured_content(output),
        output.model_dump(mode="json", exclude_none=True),
    )


def create_mcp_server(runner: SandboxRunner | None = None) -> Server:
    sandbox_runner = runner or create_sandbox_runner(settings)
    if settings.sandbox_mcp_benchmark_index_path:
        logger.info(
            "ground_truth_benchmark_evaluator_in_mcp_disabled",
            index_path=settings.sandbox_mcp_benchmark_index_path,
            reason=(
                "mcp_runtime_uses_llm_judge_or_none_only;"
                "use local_dataset_gt_eval_runner.py for GT benchmark runs"
            ),
        )
    else:
        logger.info("ground_truth_benchmark_evaluator_disabled")

    evaluation_orchestrator = EvaluationOrchestrator()
    if settings.sandbox_mcp_llm_judge_enabled:
        try:
            rubric_loader = RubricLoader(settings.sandbox_mcp_llm_judge_rubric_path)
            rubric = rubric_loader.load()
            configured_rubric_judge_model = rubric.judge_model
            llm_client = create_tiered_llm_client(LLMTier.STANDARD, settings)
            runtime_judge_provider = settings.llm_standard_provider
            runtime_judge_model = settings.llm_standard_model
            if configured_rubric_judge_model != runtime_judge_model:
                logger.info(
                    "llm_judge_model_overridden_by_runtime_config",
                    rubric_judge_model=configured_rubric_judge_model,
                    runtime_judge_provider=runtime_judge_provider,
                    runtime_judge_model=runtime_judge_model,
                )
            rubric = rubric.model_copy(update={"judge_model": runtime_judge_model})
            evidence_builder = EvidenceBuilder(
                max_prompt_chars=settings.sandbox_mcp_llm_judge_max_prompt_chars,
                max_code_chars=settings.sandbox_mcp_llm_judge_max_code_chars,
            )
            rule_evaluator = RuleEvaluator(rubric=rubric)
            llm_judge_evaluator = LLMJudgeEvaluator(
                llm_client=llm_client,
                rubric=rubric,
                max_prompt_chars=settings.sandbox_mcp_llm_judge_max_prompt_chars,
            )
            score_merger = ScoreMerger(rubric=rubric)
            evaluation_orchestrator = EvaluationOrchestrator(
                llm_judge_enabled=True,
                evidence_builder=evidence_builder,
                rule_evaluator=rule_evaluator,
                llm_judge_evaluator=llm_judge_evaluator,
                score_merger=score_merger,
            )
            logger.info(
                "llm_judge_evaluator_enabled",
                rubric_version=rubric.rubric_version,
                prompt_version=rubric.prompt_version,
                judge_model=rubric.judge_model,
                judge_provider=runtime_judge_provider,
            )
        except Exception as exc:
            logger.warning(
                "llm_judge_evaluator_disabled_due_to_init_error",
                reason=str(exc),
                exc_info=True,
            )
    else:
        logger.info("llm_judge_evaluator_disabled")

    service = SandboxMCPService(
        runner=sandbox_runner,
        evaluation_orchestrator=evaluation_orchestrator,
    )

    server = Server("aicad-sandbox")

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        return [
            build_execute_build123d_tool(),
            build_execute_build123d_probe_tool(),
            build_apply_cad_action_tool(),
            build_get_history_tool(),
            build_query_snapshot_tool(),
            build_query_sketch_tool(),
            build_query_geometry_tool(),
            build_query_topology_tool(),
            build_query_feature_probes_tool(),
            build_render_view_tool(),
            build_validate_requirement_tool(),
        ]

    @server.call_tool()
    async def call_tool(
        name: str, arguments: dict[str, Any]
    ) -> tuple[list[ContentBlock], dict[str, Any]]:
        if name == SandboxTools.EXECUTE_BUILD123D:
            return await execute_build123d_tool(
                arguments=arguments or {}, service=service
            )
        elif name == SandboxTools.EXECUTE_BUILD123D_PROBE:
            return await execute_build123d_probe_tool(
                arguments=arguments or {},
                service=service,
            )
        elif name == SandboxTools.APPLY_CAD_ACTION:
            return await apply_cad_action_tool(
                arguments=arguments or {}, service=service
            )
        elif name == SandboxTools.GET_HISTORY:
            return await get_history_tool(arguments=arguments or {}, service=service)
        elif name == SandboxTools.QUERY_SNAPSHOT:
            return await query_snapshot_tool(arguments=arguments or {}, service=service)
        elif name == SandboxTools.QUERY_SKETCH:
            return await query_sketch_tool(arguments=arguments or {}, service=service)
        elif name == SandboxTools.QUERY_GEOMETRY:
            return await query_geometry_tool(arguments=arguments or {}, service=service)
        elif name == SandboxTools.QUERY_TOPOLOGY:
            return await query_topology_tool(arguments=arguments or {}, service=service)
        elif name == SandboxTools.QUERY_FEATURE_PROBES:
            return await query_feature_probes_tool(
                arguments=arguments or {},
                service=service,
            )
        elif name == SandboxTools.RENDER_VIEW:
            return await render_view_tool(arguments=arguments or {}, service=service)
        elif name == SandboxTools.VALIDATE_REQUIREMENT:
            return await validate_requirement_tool(
                arguments=arguments or {},
                service=service,
            )
        else:
            raise ValueError(f"Unknown tool: {name}")

    return server


async def serve(runner: SandboxRunner | None = None) -> None:
    configure_mcp_stdio_logging(settings.log_level)
    logger.info("sandbox_mcp_server_starting")

    server = create_mcp_server(runner=runner)
    options = server.create_initialization_options()

    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, options, raise_exceptions=True)


def main() -> None:
    asyncio.run(serve())
