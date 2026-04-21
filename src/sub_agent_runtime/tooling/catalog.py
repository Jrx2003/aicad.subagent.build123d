from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

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
from sub_agent_runtime.turn_state import ToolCategory


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
                "Use this for local, inspectable edits after a code-backed model already exists; additive extrude does not support hollow or subtractive overload modes. "
                "Canonical input shape: action_type plus a nested action_params object."
            ),
            input_model=CADActionInput,
            follow_up_recommendation=(
                "Not the default first-write path in V2. "
                "Prefer only for local, structured, inspectable edits once a stable host solid or topology anchor already exists. "
                "Put geometry keys such as face_ref, edge_refs, diameter, centers/positions, depth, and hole_type inside action_params, not at the top level. "
                "For example: {\"action_type\": \"fillet\", \"action_params\": {\"edge_refs\": [\"edge:...\"], \"radius\": 1.0}}. "
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


__all__ = [
    "ExecuteRepairPacketInput",
    "FinishRunInput",
    "ToolSpec",
    "build_default_tool_specs",
]
