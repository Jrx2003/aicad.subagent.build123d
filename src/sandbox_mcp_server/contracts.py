from enum import Enum
from typing import Any, TypeAlias

from pydantic import BaseModel, ConfigDict, Field


class SandboxErrorCode(str, Enum):
    NONE = "none"
    INVALID_REQUEST = "invalid_request"
    INVALID_REFERENCE = "invalid_reference"
    TIMEOUT = "timeout"
    IMAGE_NOT_FOUND = "image_not_found"
    DOCKER_API_ERROR = "docker_api_error"
    EXECUTION_ERROR = "execution_error"


class EvaluationMode(str, Enum):
    NONE = "none"
    GROUND_TRUTH = "ground_truth"
    LLM_JUDGE = "llm_judge"


class EvaluationStatus(str, Enum):
    NOT_REQUESTED = "not_requested"
    SKIPPED = "skipped"
    SUCCESS = "success"
    ERROR = "error"


class ExecuteBuild123dInput(BaseModel):
    """Input contract for execute_build123d tool."""

    model_config = ConfigDict(extra="forbid")

    code: str = Field(
        ...,
        min_length=1,
        description="Build123d Python code. Must assign final geometry to `result`.",
    )
    timeout_seconds: int = Field(
        default=120,
        ge=1,
        le=600,
        description="Maximum sandbox runtime in seconds.",
    )
    include_artifact_content: bool = Field(
        default=True,
        description="Include generated artifact bytes (base64) in the response.",
    )
    benchmark_sample_id: str | None = Field(
        default=None,
        description=(
            "Optional benchmark sample identifier reserved for external benchmark "
            "scripts (for example local_dataset_gt_eval_runner.py). "
            "MCP runtime evaluation no longer performs ground-truth scoring."
        ),
    )
    requirement_text: str | None = Field(
        default=None,
        description=(
            "Optional user requirement text used by llm_judge for requirement fidelity "
            "assessment when benchmark_sample_id is absent."
        ),
    )
    session_id: str | None = Field(
        default=None,
        description=(
            "Optional runtime-managed session id. When present, successful execution "
            "must persist a replayable base state for later queries, validation, "
            "and follow-on edits."
        ),
    )


class SandboxArtifact(BaseModel):
    """Artifact metadata returned by sandbox execution."""

    model_config = ConfigDict(extra="forbid")

    filename: str = Field(..., description="Artifact filename.")
    uri: str = Field(..., description="Stable artifact URI in this tool response.")
    mime_type: str = Field(..., description="Artifact MIME type.")
    size_bytes: int = Field(..., ge=0, description="Artifact size in bytes.")
    content_base64: str | None = Field(
        default=None,
        description="Base64-encoded artifact bytes when include_artifact_content=true.",
    )


class ExecutionEvaluation(BaseModel):
    """Evaluation metadata for this sandbox execution."""

    model_config = ConfigDict(extra="forbid")

    mode: EvaluationMode = Field(
        default=EvaluationMode.NONE,
        description="Evaluation mode used for this response.",
    )
    status: EvaluationStatus = Field(
        default=EvaluationStatus.NOT_REQUESTED,
        description="Evaluation status.",
    )
    benchmark_name: str | None = Field(
        default=None,
        description="Benchmark name for ground-truth evaluation.",
    )
    sample_id: str | None = Field(
        default=None,
        description="Sample id used in benchmark evaluation.",
    )
    metric_name: str | None = Field(
        default=None,
        description="Metric used to calculate score.",
    )
    score: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Normalized score in [0, 1].",
    )
    threshold: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Pass threshold used in evaluation.",
    )
    passed: bool | None = Field(
        default=None,
        description="Whether score meets threshold.",
    )
    summary: str = Field(
        default="Evaluation not requested",
        description="Human-readable evaluation summary.",
    )
    details: dict[str, str | int | float | bool | None] = Field(
        default_factory=dict,
        description="Additional evaluation details.",
    )


class ExecuteBuild123dOutput(BaseModel):
    """Structured output contract for execute_build123d tool."""

    model_config = ConfigDict(extra="forbid")

    success: bool = Field(..., description="Whether sandbox execution succeeded.")
    stdout: str = Field(default="", description="Captured standard output.")
    stderr: str = Field(default="", description="Captured standard error.")
    error_code: SandboxErrorCode = Field(
        default=SandboxErrorCode.NONE,
        description="Normalized error category.",
    )
    error_message: str | None = Field(
        default=None,
        description="Human-readable error detail.",
    )
    output_files: list[str] = Field(
        default_factory=list,
        description="List of generated artifact filenames.",
    )
    artifacts: list[SandboxArtifact] = Field(
        default_factory=list,
        description="Artifact metadata and optional content.",
    )
    session_id: str | None = Field(
        default=None,
        description="Session id that received the persisted code-execution state.",
    )
    step: int | None = Field(
        default=None,
        description="Persisted session step after successful code execution.",
    )
    step_file: str | None = Field(
        default=None,
        description="Primary STEP artifact filename when available.",
    )
    snapshot: "CADStateSnapshot | None" = Field(
        default=None,
        description="Parsed snapshot persisted into session state when available.",
    )
    session_state_persisted: bool = Field(
        default=False,
        description="Whether the successful code execution was written back into session state.",
    )
    evaluation: ExecutionEvaluation = Field(
        default_factory=ExecutionEvaluation,
        description="Optional benchmark/quality evaluation for this execution.",
    )


# ==================== CAD Action Interface Models ====================


class CADActionType(str, Enum):
    """CAD action types for action-based CAD generation."""

    # Sketch operations
    CREATE_SKETCH = "create_sketch"
    ADD_RECTANGLE = "add_rectangle"
    ADD_CIRCLE = "add_circle"
    ADD_POLYGON = "add_polygon"
    ADD_PATH = "add_path"

    # Solid operations
    EXTRUDE = "extrude"
    CUT_EXTRUDE = "cut_extrude"
    TRIM_SOLID = "trim_solid"
    REVOLVE = "revolve"
    LOFT = "loft"
    SWEEP = "sweep"

    # Feature operations
    FILLET = "fillet"
    CHAMFER = "chamfer"
    HOLE = "hole"
    SPHERE_RECESS = "sphere_recess"
    PATTERN_LINEAR = "pattern_linear"
    PATTERN_CIRCULAR = "pattern_circular"

    # State operations
    ROLLBACK = "rollback"
    SNAPSHOT = "snapshot"

    # ACI Enhancement - New action types for self-adjustment
    MODIFY_ACTION = "modify_action"
    GET_HISTORY = "get_history"
    CLEAR_SESSION = "clear_session"


# Keep action params JSON-like and permissive to avoid rejecting valid nested
# planner payloads (e.g., polygon points list[list[float]]) at tool boundary.
CADParamValue: TypeAlias = Any


class ActionHistoryEntry(BaseModel):
    """Single action entry in the history."""

    model_config = ConfigDict(extra="forbid")

    step: int = Field(..., description="Action step number.")
    action_type: CADActionType = Field(..., description="Type of action executed.")
    action_params: dict[str, CADParamValue] = Field(
        ..., description="Parameters used for this action."
    )
    result_snapshot: "CADStateSnapshot" = Field(
        ..., description="Snapshot after this action."
    )
    success: bool = Field(..., description="Whether the action succeeded.")
    error: str | None = Field(
        default=None, description="Error message if action failed."
    )
    warnings: list[str] = Field(
        default_factory=list,
        description="Structured warning codes emitted by this action.",
    )
    blockers: list[str] = Field(
        default_factory=list,
        description="Structured blocker codes still unresolved after this action.",
    )


class CompletenessInfo(BaseModel):
    """Model completeness and guidance information."""

    model_config = ConfigDict(extra="forbid")

    expected_steps: int = Field(..., description="Total expected steps for completion.")
    current_step: int = Field(..., description="Current step number.")
    missing_features: list[str] = Field(
        default_factory=list, description="Features that are still missing."
    )
    can_continue: bool = Field(
        ..., description="Whether the model is ready to continue."
    )
    confidence: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        description="Confidence level in completion status.",
    )


class BoundingBox3D(BaseModel):
    """Axis-aligned bounding box data."""

    model_config = ConfigDict(extra="forbid")

    xlen: float = Field(..., description="Box size along X axis.")
    ylen: float = Field(..., description="Box size along Y axis.")
    zlen: float = Field(..., description="Box size along Z axis.")
    xmin: float = Field(..., description="Minimum X coordinate.")
    xmax: float = Field(..., description="Maximum X coordinate.")
    ymin: float = Field(..., description="Minimum Y coordinate.")
    ymax: float = Field(..., description="Maximum Y coordinate.")
    zmin: float = Field(..., description="Minimum Z coordinate.")
    zmax: float = Field(..., description="Maximum Z coordinate.")


class SolidEntity(BaseModel):
    """Queryable solid-level geometry facts."""

    model_config = ConfigDict(extra="forbid")

    solid_id: str = Field(..., description="Stable solid identifier in this snapshot.")
    volume: float = Field(..., description="Solid volume.")
    surface_area: float = Field(..., description="Solid surface area.")
    center_of_mass: list[float] = Field(
        ..., description="Solid center of mass [x, y, z]."
    )
    bbox: BoundingBox3D = Field(..., description="Solid bounding box.")


class FaceEntity(BaseModel):
    """Queryable face-level geometry facts."""

    model_config = ConfigDict(extra="forbid")

    face_id: str = Field(..., description="Stable face identifier in this snapshot.")
    area: float = Field(..., description="Face area.")
    center: list[float] = Field(..., description="Face center [x, y, z].")
    normal: list[float] | None = Field(
        default=None,
        description="Face normal vector [nx, ny, nz] when available.",
    )
    axis_origin: list[float] | None = Field(
        default=None,
        description="Axis origin [x, y, z] for axis-bearing geometry such as cylinders.",
    )
    axis_direction: list[float] | None = Field(
        default=None,
        description="Axis direction [dx, dy, dz] for axis-bearing geometry such as cylinders.",
    )
    radius: float | None = Field(
        default=None,
        description="Radius for circular/cylindrical geometry when available.",
    )
    geom_type: str = Field(
        default="unknown", description="Build123d geometry type."
    )
    bbox: BoundingBox3D = Field(..., description="Face bounding box.")


class EdgeEntity(BaseModel):
    """Queryable edge-level geometry facts."""

    model_config = ConfigDict(extra="forbid")

    edge_id: str = Field(..., description="Stable edge identifier in this snapshot.")
    length: float = Field(..., description="Edge length.")
    geom_type: str = Field(
        default="unknown",
        description="Build123d geometry type.",
    )
    center: list[float] | None = Field(
        default=None,
        description="Edge center [x, y, z] when available.",
    )
    axis_origin: list[float] | None = Field(
        default=None,
        description="Axis origin [x, y, z] for circular edges when available.",
    )
    axis_direction: list[float] | None = Field(
        default=None,
        description="Axis direction [dx, dy, dz] for circular edges when available.",
    )
    radius: float | None = Field(
        default=None,
        description="Radius for circular edges when available.",
    )
    bbox: BoundingBox3D = Field(..., description="Edge bounding box.")


class GeometryObjectIndex(BaseModel):
    """Structured queryable geometry object index."""

    model_config = ConfigDict(extra="forbid")

    solids: list[SolidEntity] = Field(
        default_factory=list, description="Queryable solid objects."
    )
    faces: list[FaceEntity] = Field(
        default_factory=list, description="Queryable face objects."
    )
    edges: list[EdgeEntity] = Field(
        default_factory=list, description="Queryable edge objects."
    )
    solids_truncated: bool = Field(
        default=False,
        description="Whether returned solids are truncated.",
    )
    faces_truncated: bool = Field(
        default=False,
        description="Whether returned faces are truncated.",
    )
    edges_truncated: bool = Field(
        default=False,
        description="Whether returned edges are truncated.",
    )
    max_items_per_type: int = Field(
        default=0,
        ge=0,
        description="Per-type cap used when building this index.",
    )
    solids_total: int = Field(
        default=0,
        ge=0,
        description="Total solids count before offset/limit windowing.",
    )
    faces_total: int = Field(
        default=0,
        ge=0,
        description="Total faces count before offset/limit windowing.",
    )
    edges_total: int = Field(
        default=0,
        ge=0,
        description="Total edges count before offset/limit windowing.",
    )
    solid_offset: int = Field(
        default=0,
        ge=0,
        description="Applied solid offset for this window.",
    )
    face_offset: int = Field(
        default=0,
        ge=0,
        description="Applied face offset for this window.",
    )
    edge_offset: int = Field(
        default=0,
        ge=0,
        description="Applied edge offset for this window.",
    )
    next_solid_offset: int | None = Field(
        default=None,
        ge=0,
        description="Next solid offset if more solids exist.",
    )
    next_face_offset: int | None = Field(
        default=None,
        ge=0,
        description="Next face offset if more faces exist.",
    )
    next_edge_offset: int | None = Field(
        default=None,
        ge=0,
        description="Next edge offset if more edges exist.",
    )


class TopologyFaceEntity(BaseModel):
    """Face entity with step-local topology reference and adjacency context."""

    model_config = ConfigDict(extra="forbid")

    face_ref: str = Field(..., description="Step-local face reference.")
    face_id: str = Field(..., description="Stable face identifier in this snapshot.")
    step: int = Field(..., ge=1, description="Snapshot step used for this ref.")
    area: float = Field(..., description="Face area.")
    center: list[float] = Field(..., description="Face center [x, y, z].")
    normal: list[float] | None = Field(
        default=None,
        description="Face normal vector [nx, ny, nz] when available.",
    )
    axis_origin: list[float] | None = Field(
        default=None,
        description="Axis origin [x, y, z] for axis-bearing geometry such as cylinders.",
    )
    axis_direction: list[float] | None = Field(
        default=None,
        description="Axis direction [dx, dy, dz] for axis-bearing geometry such as cylinders.",
    )
    radius: float | None = Field(
        default=None,
        description="Radius for circular/cylindrical geometry when available.",
    )
    geom_type: str = Field(
        default="unknown",
        description="Build123d geometry type.",
    )
    bbox: BoundingBox3D = Field(..., description="Face bounding box.")
    parent_solid_id: str | None = Field(
        default=None,
        description="Owning solid ID when known.",
    )
    edge_refs: list[str] = Field(
        default_factory=list,
        description="Step-local edge refs bounding this face.",
    )
    adjacent_face_refs: list[str] = Field(
        default_factory=list,
        description="Adjacent faces sharing an edge with this face.",
    )
    candidate_rank: int | None = Field(
        default=None,
        ge=1,
        description="Optional rank when the result set is filter-ordered.",
    )


class TopologyEdgeEntity(BaseModel):
    """Edge entity with step-local topology reference and adjacency context."""

    model_config = ConfigDict(extra="forbid")

    edge_ref: str = Field(..., description="Step-local edge reference.")
    edge_id: str = Field(..., description="Stable edge identifier in this snapshot.")
    step: int = Field(..., ge=1, description="Snapshot step used for this ref.")
    length: float = Field(..., description="Edge length.")
    geom_type: str = Field(
        default="unknown",
        description="Build123d geometry type.",
    )
    center: list[float] | None = Field(
        default=None,
        description="Edge center [x, y, z] when available.",
    )
    axis_origin: list[float] | None = Field(
        default=None,
        description="Axis origin [x, y, z] for circular edges when available.",
    )
    axis_direction: list[float] | None = Field(
        default=None,
        description="Axis direction [dx, dy, dz] for circular edges when available.",
    )
    radius: float | None = Field(
        default=None,
        description="Radius for circular edges when available.",
    )
    bbox: BoundingBox3D = Field(..., description="Edge bounding box.")
    parent_solid_id: str | None = Field(
        default=None,
        description="Owning solid ID when known.",
    )
    adjacent_face_refs: list[str] = Field(
        default_factory=list,
        description="Faces incident to this edge.",
    )
    candidate_rank: int | None = Field(
        default=None,
        ge=1,
        description="Optional rank when the result set is filter-ordered.",
    )


class TopologyObjectIndex(BaseModel):
    """Structured queryable topology index."""

    model_config = ConfigDict(extra="forbid")

    faces: list[TopologyFaceEntity] = Field(
        default_factory=list, description="Queryable topology faces."
    )
    edges: list[TopologyEdgeEntity] = Field(
        default_factory=list, description="Queryable topology edges."
    )
    faces_truncated: bool = Field(
        default=False,
        description="Whether returned faces are truncated.",
    )
    edges_truncated: bool = Field(
        default=False,
        description="Whether returned edges are truncated.",
    )
    max_items_per_type: int = Field(
        default=0,
        ge=0,
        description="Per-type cap used when building this index.",
    )
    faces_total: int = Field(
        default=0,
        ge=0,
        description="Total face count before offset/limit windowing.",
    )
    edges_total: int = Field(
        default=0,
        ge=0,
        description="Total edge count before offset/limit windowing.",
    )
    face_offset: int = Field(
        default=0,
        ge=0,
        description="Applied face offset for this window.",
    )
    edge_offset: int = Field(
        default=0,
        ge=0,
        description="Applied edge offset for this window.",
    )
    next_face_offset: int | None = Field(
        default=None,
        ge=0,
        description="Next face offset if more faces exist.",
    )
    next_edge_offset: int | None = Field(
        default=None,
        ge=0,
        description="Next edge offset if more edges exist.",
    )


class TopologyCandidateSet(BaseModel):
    """Requirement-aware candidate subset for common topology targeting intents."""

    model_config = ConfigDict(extra="forbid")

    candidate_id: str = Field(..., description="Stable candidate-set identifier.")
    label: str = Field(..., description="Human-readable candidate-set label.")
    entity_type: str = Field(
        ...,
        description="Candidate entity type, typically 'face' or 'edge'.",
    )
    ref_ids: list[str] = Field(
        default_factory=list,
        description="Step-local refs contained in this candidate set.",
    )
    entity_ids: list[str] = Field(
        default_factory=list,
        description="Snapshot entity IDs contained in this candidate set.",
    )
    rationale: str = Field(
        default="",
        description="Compact explanation of why these candidates were selected.",
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Compact extra anchors for planning, such as primary axis alias, "
            "axis midpoint, outer span, or suggested sketch planes."
        ),
    )


class RelationEntity(BaseModel):
    """Structured entity node used by relation-base outputs."""

    model_config = ConfigDict(extra="forbid")

    entity_id: str = Field(..., description="Stable entity identifier in the relation view.")
    entity_type: str = Field(..., description="Entity kind, e.g. sketch_path, profile_loop, topology_face.")
    ref: str | None = Field(
        default=None,
        description="Step-local ref when this relation entity maps directly to a tool ref.",
    )
    label: str = Field(
        default="",
        description="Human-readable compact label for this entity.",
    )
    attributes: dict[str, Any] = Field(
        default_factory=dict,
        description="Compact descriptive attributes for this entity.",
    )


class RelationFact(BaseModel):
    """Observed objective relation between one or more entities."""

    model_config = ConfigDict(extra="forbid")

    relation_id: str = Field(..., description="Stable relation identifier.")
    relation_type: str = Field(..., description="Relation family/type name.")
    lhs: str | None = Field(default=None, description="Primary relation entity ID.")
    rhs: str | None = Field(default=None, description="Secondary relation entity ID.")
    members: list[str] = Field(
        default_factory=list,
        description="Member entity IDs for n-ary relations when lhs/rhs is insufficient.",
    )
    metrics: dict[str, Any] = Field(
        default_factory=dict,
        description="Raw measured values that describe this observed relation.",
    )
    evidence: str = Field(
        default="",
        description="Short evidence string explaining why this relation is present.",
    )


class RelationGroup(BaseModel):
    """Higher-level grouped pattern derived from multiple entities/relations."""

    model_config = ConfigDict(extra="forbid")

    group_id: str = Field(..., description="Stable relation-group identifier.")
    group_type: str = Field(..., description="Group family/type name.")
    members: list[str] = Field(
        default_factory=list,
        description="Entity IDs participating in this group.",
    )
    derived: dict[str, Any] = Field(
        default_factory=dict,
        description="Compact derived facts for this relation group.",
    )
    evidence: str = Field(
        default="",
        description="Short evidence string summarizing the grouped pattern.",
    )


class RelationStatus(str, Enum):
    """Legacy relation status enum kept for backward-compatible internal helpers."""

    PASS = "pass"
    FAIL = "fail"
    INFO = "info"
    UNKNOWN = "unknown"


class RelationSignal(BaseModel):
    """Legacy relation signal kept for backward-compatible internal helpers."""

    model_config = ConfigDict(extra="forbid")

    relation_id: str = Field(..., description="Stable relation identifier.")
    relation_type: str = Field(..., description="Relation family/type name.")
    status: RelationStatus = Field(..., description="Relation status.")
    score: float = Field(default=0.0, ge=0.0, le=1.0)
    entities: list[str] = Field(default_factory=list)
    blocking: bool = Field(default=False)
    measured: dict[str, Any] = Field(default_factory=dict)
    why: str = Field(default="")


class RelationIndex(BaseModel):
    """Objective relation-base summary derived from sketch/topology evidence."""

    model_config = ConfigDict(extra="forbid")

    version: str = Field(
        default="v2",
        description="Relation-index schema version.",
    )
    source_tool: str = Field(
        ...,
        description="Tool surface that produced this relation summary.",
    )
    step: int | None = Field(
        default=None,
        description="Snapshot step this relation summary is based on.",
    )
    focus_entity_ids: list[str] = Field(
        default_factory=list,
        description="Primary entity IDs this relation summary focuses on.",
    )
    entities: list[RelationEntity] = Field(
        default_factory=list,
        description="Relation-graph entities exposed to the planner/user.",
    )
    relations: list[RelationFact] = Field(
        default_factory=list,
        description="Observed objective relations among these entities.",
    )
    relation_groups: list[RelationGroup] = Field(
        default_factory=list,
        description="Higher-level grouped patterns derived from these entities/relations.",
    )
    summary: str = Field(
        default="",
        description="One-line summary of the current relation base.",
    )


class GeometryInfo(BaseModel):
    """Geometry summary for CAD state snapshot."""

    model_config = ConfigDict(extra="forbid")

    solids: int = Field(..., description="Number of solids in the model.")
    faces: int = Field(..., description="Number of faces.")
    edges: int = Field(..., description="Number of edges.")
    volume: float = Field(..., description="Model volume.")
    bbox: list[float] = Field(..., description="Bounding box dimensions [x, y, z].")
    center_of_mass: list[float] = Field(..., description="Center of mass [x, y, z].")
    surface_area: float = Field(default=0.0, description="Model surface area.")
    bbox_min: list[float] = Field(
        default_factory=lambda: [0.0, 0.0, 0.0],
        description="Bounding box min corner [x, y, z].",
    )
    bbox_max: list[float] = Field(
        default_factory=lambda: [0.0, 0.0, 0.0],
        description="Bounding box max corner [x, y, z].",
    )


class CADStateSnapshot(BaseModel):
    """CAD state snapshot after action execution."""

    model_config = ConfigDict(extra="forbid")

    step: int = Field(..., description="Current step number in action history.")
    features: list[str] = Field(..., description="List of feature names applied.")
    geometry: GeometryInfo = Field(..., description="Geometry analysis summary.")
    issues: list[str] = Field(
        default_factory=list, description="List of geometry/validation issues."
    )
    warnings: list[str] = Field(
        default_factory=list,
        description="Structured warning codes for this snapshot.",
    )
    blockers: list[str] = Field(
        default_factory=list,
        description="Structured blocker codes for this snapshot.",
    )
    images: list[str] = Field(
        default_factory=list, description="Generated preview image filenames."
    )
    sketch_state: "SketchState | None" = Field(
        default=None,
        description="Structured pre-solid sketch/path/profile state when available.",
    )
    geometry_objects: GeometryObjectIndex | None = Field(
        default=None,
        description="Queryable object index extracted from geometry analysis.",
    )
    topology_index: TopologyObjectIndex | None = Field(
        default=None,
        description="Queryable topology index extracted from geometry analysis.",
    )
    success: bool = Field(..., description="Whether the action executed successfully.")
    error: str | None = Field(
        default=None, description="Error message if action failed."
    )


class CADActionInput(BaseModel):
    """Input for applying a CAD action."""

    model_config = ConfigDict(extra="forbid")

    action_type: CADActionType = Field(
        ..., description="Type of CAD action to execute."
    )
    action_params: dict[str, CADParamValue] = Field(
        default_factory=dict,
        description="Parameters for the action (varies by action_type).",
    )
    session_id: str | None = Field(
        default=None,
        description=(
            "Optional session ID for state persistence. If provided, maintains "
            "state across multiple action calls."
        ),
    )
    timeout_seconds: int = Field(
        default=120,
        ge=1,
        le=600,
        description="Maximum sandbox runtime in seconds.",
    )
    include_artifact_content: bool = Field(
        default=True,
        description="Include generated artifact bytes (base64) in response.",
    )
    clear_session: bool = Field(
        default=False,
        description="Clear all session state and start fresh. Only works when session_id is provided.",
    )


class GetHistoryInput(BaseModel):
    """Input for getting action history."""

    model_config = ConfigDict(extra="forbid")

    session_id: str = Field(..., description="Session ID to retrieve history for.")
    include_history: bool = Field(
        default=True,
        description="Whether to include full action history in response.",
    )


class CADActionOutput(BaseModel):
    """Output from applying a CAD action."""

    model_config = ConfigDict(extra="forbid")

    success: bool = Field(..., description="Whether the action executed successfully.")
    stdout: str = Field(default="", description="Captured standard output.")
    stderr: str = Field(default="", description="Captured standard error.")
    error_code: SandboxErrorCode = Field(
        default=SandboxErrorCode.NONE,
        description="Normalized error category.",
    )
    error_message: str | None = Field(
        default=None,
        description="Human-readable error detail.",
    )
    snapshot: "CADStateSnapshot" = Field(
        ..., description="CAD state after action execution."
    )
    executed_action: dict[str, str | dict] = Field(
        ...,
        description="The action that was executed (for confirmation).",
    )
    step_file: str | None = Field(
        default=None,
        description="Path to generated STEP file if model was created.",
    )
    output_files: list[str] = Field(
        default_factory=list,
        description="List of generated artifact filenames.",
    )
    artifacts: list[SandboxArtifact] = Field(
        default_factory=list,
        description="Artifact metadata and optional content.",
    )
    action_history: list["ActionHistoryEntry"] = Field(
        default_factory=list,
        description="Complete action history for this session.",
    )
    suggestions: list[str] = Field(
        default_factory=list,
        description="AI-generated suggestions for next actions.",
    )
    completeness: "CompletenessInfo | None" = Field(
        default=None,
        description="Model completeness and continuation guidance.",
    )


class QuerySnapshotInput(BaseModel):
    """Input for querying a session snapshot."""

    model_config = ConfigDict(extra="forbid")

    session_id: str = Field(..., description="Session ID to query.")
    step: int | None = Field(
        default=None,
        ge=1,
        description="Optional step index (1-based). Defaults to latest step.",
    )
    include_history: bool = Field(
        default=False,
        description="Include full action history in the response.",
    )


class QuerySnapshotOutput(BaseModel):
    """Snapshot query response."""

    model_config = ConfigDict(extra="forbid")

    success: bool = Field(..., description="Whether query completed successfully.")
    error_code: SandboxErrorCode = Field(
        default=SandboxErrorCode.NONE,
        description="Normalized error category.",
    )
    error_message: str | None = Field(
        default=None,
        description="Human-readable error detail.",
    )
    session_id: str = Field(..., description="Queried session ID.")
    step: int | None = Field(
        default=None,
        description="Resolved step index for returned snapshot.",
    )
    snapshot: CADStateSnapshot | None = Field(
        default=None,
        description="Snapshot payload for requested step.",
    )
    action_history: list[ActionHistoryEntry] = Field(
        default_factory=list,
        description="Optional action history for the session.",
    )


class SketchSegmentEntity(BaseModel):
    """Structured path segment facts for pre-solid sketch inspection."""

    model_config = ConfigDict(extra="forbid")

    segment_index: int = Field(..., ge=1, description="1-based segment index.")
    segment_type: str = Field(..., description="Normalized segment type.")
    start_point: list[float] = Field(..., description="Segment start point [x, y].")
    end_point: list[float] = Field(..., description="Segment end point [x, y].")
    connected_to_previous: bool = Field(
        default=True,
        description="Whether this segment starts where the previous segment ended.",
    )
    length: float = Field(default=0.0, description="Approximate segment length.")
    radius: float | None = Field(
        default=None,
        description="Arc radius when applicable.",
    )
    angle_degrees: float | None = Field(
        default=None,
        description="Arc angle in degrees when applicable.",
    )
    start_tangent: list[float] | None = Field(
        default=None,
        description="Approximate tangent [dx, dy] at the segment start when available.",
    )
    end_tangent: list[float] | None = Field(
        default=None,
        description="Approximate tangent [dx, dy] at the segment end when available.",
    )


class SketchPathEntity(BaseModel):
    """Structured path/rail facts from the current pre-solid sketch state."""

    model_config = ConfigDict(extra="forbid")

    path_ref: str = Field(..., description="Step-local path reference.")
    step: int = Field(..., ge=1, description="Snapshot step used for this path ref.")
    plane: str = Field(default="XY", description="Active sketch plane for this path.")
    origin: list[float] = Field(..., description="Sketch origin [x, y, z].")
    segment_types: list[str] = Field(
        default_factory=list,
        description="Normalized ordered segment type sequence.",
    )
    segments: list[SketchSegmentEntity] = Field(
        default_factory=list,
        description="Detailed path segment facts.",
    )
    start_point: list[float] = Field(..., description="Path start point [x, y].")
    end_point: list[float] = Field(..., description="Path end point [x, y].")
    connected: bool = Field(..., description="Whether the path is connected.")
    closed: bool = Field(default=False, description="Whether the path is closed.")
    start_tangent: list[float] | None = Field(
        default=None,
        description="Approximate start tangent [dx, dy] when available.",
    )
    terminal_tangent: list[float] | None = Field(
        default=None,
        description="Approximate terminal tangent [dx, dy] when available.",
    )
    total_length: float = Field(default=0.0, description="Approximate total path length.")
    bbox: BoundingBox3D = Field(..., description="2D path bbox lifted into 3D fields.")


class SketchLoopEntity(BaseModel):
    """Structured loop facts extracted from a sketch profile window."""

    model_config = ConfigDict(extra="forbid")

    loop_id: str = Field(..., description="Stable loop identifier inside the profile window.")
    loop_type: str = Field(..., description="Loop primitive type, e.g. circle.")
    role: str | None = Field(
        default=None,
        description="Loop role when known, e.g. outer or inner.",
    )
    center: list[float] | None = Field(
        default=None,
        description="Loop-local center [x, y] when available.",
    )
    radius: float | None = Field(
        default=None,
        description="Loop radius when the loop is circular.",
    )


class SketchProfileEntity(BaseModel):
    """Structured closed-profile facts from the current pre-solid sketch state."""

    model_config = ConfigDict(extra="forbid")

    profile_ref: str = Field(..., description="Step-local profile reference.")
    step: int = Field(..., ge=1, description="Snapshot step used for this profile ref.")
    window_index: int = Field(
        default=1,
        ge=1,
        description="Ordered sketch-window index for this profile within the current stack.",
    )
    source_sketch_step: int | None = Field(
        default=None,
        ge=1,
        description="Create-sketch step that opened this profile window when known.",
    )
    plane: str = Field(default="XY", description="Active sketch plane for this profile.")
    origin: list[float] = Field(..., description="Sketch origin [x, y, z].")
    outer_loop_count: int = Field(default=0, ge=0, description="Outer loop count.")
    inner_loop_count: int = Field(default=0, ge=0, description="Inner loop count.")
    closed: bool = Field(default=False, description="Whether the profile is closed.")
    nested_relationship: str | None = Field(
        default=None,
        description="Nested/concentric relationship summary when available.",
    )
    has_sloped_segment: bool = Field(
        default=False,
        description="Whether the profile window contains at least one non-orthogonal segment.",
    )
    primitive_types: list[str] = Field(
        default_factory=list,
        description="Primitive/profile shape hints observed in this sketch window, e.g. circle, rectangle, polygon, triangle, hexagon.",
    )
    point_count: int | None = Field(
        default=None,
        description="Observed explicit point count when the profile was authored from point vertices.",
    )
    regular_sides: int | None = Field(
        default=None,
        description="Observed regular polygon side count when available.",
    )
    regular_polygon_size_mode: str | None = Field(
        default=None,
        description="Observed regular polygon size mode, e.g. circumradius or apothem/distance_to_side.",
    )
    regular_polygon_circumradius: float | None = Field(
        default=None,
        description="Observed regular polygon circumradius when available.",
    )
    regular_polygon_apothem: float | None = Field(
        default=None,
        description="Observed regular polygon apothem / center-to-side distance when available.",
    )
    rotation_degrees: float | None = Field(
        default=None,
        description="Observed regular-polygon phase/orientation in sketch-local degrees when available.",
    )
    centers: list[list[float]] = Field(
        default_factory=list,
        description="Local profile centers when explicitly represented.",
    )
    loops: list[SketchLoopEntity] = Field(
        default_factory=list,
        description="Explicit loop entities extracted from this profile window when available.",
    )
    loop_radii: list[float] = Field(
        default_factory=list,
        description="Distinct circular loop radii when the profile window exposes circle-based loops.",
    )
    estimated_area: float | None = Field(
        default=None,
        description="Approximate closed profile area in sketch-local units squared when inferable.",
    )
    attached_path_ref: str | None = Field(
        default=None,
        description="Path ref used to resolve this profile frame when attached to a rail endpoint.",
    )
    frame_mode: str | None = Field(
        default=None,
        description="Resolved frame mode used when this profile was attached to a path endpoint.",
    )
    loftable: bool = Field(
        default=False,
        description="Whether this profile window is closed and suitable for loft stacking.",
    )
    bbox: BoundingBox3D = Field(..., description="2D profile bbox lifted into 3D fields.")


class SketchState(BaseModel):
    """Structured pre-solid sketch/path/profile state."""

    model_config = ConfigDict(extra="forbid")

    plane: str = Field(default="XY", description="Current active sketch plane.")
    origin: list[float] = Field(
        default_factory=lambda: [0.0, 0.0, 0.0],
        description="Current active sketch origin [x, y, z].",
    )
    path_refs: list[str] = Field(default_factory=list, description="Available path refs.")
    profile_refs: list[str] = Field(
        default_factory=list,
        description="Available profile refs.",
    )
    profile_stack_order: list[str] = Field(
        default_factory=list,
        description="Ordered loft/profile-stack profile refs in planner-visible stack order.",
    )
    sweep_ready_profile_refs: list[str] = Field(
        default_factory=list,
        description="Profile refs currently suitable for sweep execution.",
    )
    loft_ready_profile_refs: list[str] = Field(
        default_factory=list,
        description="Closed profile refs currently suitable for loft/profile-stack use.",
    )
    paths: list[SketchPathEntity] = Field(
        default_factory=list,
        description="Current path/rail entities.",
    )
    profiles: list[SketchProfileEntity] = Field(
        default_factory=list,
        description="Current closed profile entities.",
    )
    issues_by_path_ref: dict[str, list[str]] = Field(
        default_factory=dict,
        description="Structured issues scoped to individual path refs.",
    )
    issues_by_profile_ref: dict[str, list[str]] = Field(
        default_factory=dict,
        description="Structured issues scoped to individual profile refs.",
    )
    issues: list[str] = Field(
        default_factory=list,
        description="Structured sketch/path/profile issues.",
    )


class QuerySketchInput(BaseModel):
    """Input for querying structured pre-solid sketch/path/profile state."""

    model_config = ConfigDict(extra="forbid")

    session_id: str = Field(..., description="Session ID to query.")
    step: int | None = Field(
        default=None,
        ge=1,
        description="Optional step index (1-based). Defaults to latest step.",
    )


class QuerySketchOutput(BaseModel):
    """Structured pre-solid sketch/path/profile query response."""

    model_config = ConfigDict(extra="forbid")

    success: bool = Field(..., description="Whether query completed successfully.")
    error_code: SandboxErrorCode = Field(
        default=SandboxErrorCode.NONE,
        description="Normalized error category.",
    )
    error_message: str | None = Field(
        default=None,
        description="Human-readable error detail.",
    )
    session_id: str = Field(..., description="Queried session ID.")
    step: int | None = Field(
        default=None,
        description="Resolved step index for returned sketch state.",
    )
    sketch_state: SketchState | None = Field(
        default=None,
        description="Structured sketch/path/profile state for the requested step.",
    )
    relation_index: RelationIndex | None = Field(
        default=None,
        description="Compact relation-state summary derived from sketch evidence.",
    )


class QueryGeometryInput(BaseModel):
    """Input for querying geometry summary from a session snapshot."""

    model_config = ConfigDict(extra="forbid")

    session_id: str = Field(..., description="Session ID to query.")
    step: int | None = Field(
        default=None,
        ge=1,
        description="Optional step index (1-based). Defaults to latest step.",
    )
    include_solids: bool = Field(
        default=True,
        description="Include solid objects in the returned object index.",
    )
    include_faces: bool = Field(
        default=False,
        description="Include face objects in the returned object index.",
    )
    include_edges: bool = Field(
        default=False,
        description="Include edge objects in the returned object index.",
    )
    max_items_per_type: int = Field(
        default=25,
        ge=1,
        le=200,
        description="Maximum number of objects returned per type.",
    )
    entity_ids: list[str] = Field(
        default_factory=list,
        description=(
            "Optional entity IDs to focus on (for example S_xxx/F_xxx/E_xxx). "
            "When provided, only matching objects are returned."
        ),
    )
    solid_offset: int = Field(
        default=0,
        ge=0,
        description="Offset for solids windowing.",
    )
    face_offset: int = Field(
        default=0,
        ge=0,
        description="Offset for faces windowing.",
    )
    edge_offset: int = Field(
        default=0,
        ge=0,
        description="Offset for edges windowing.",
    )


class QueryGeometryOutput(BaseModel):
    """Geometry query response."""

    model_config = ConfigDict(extra="forbid")

    success: bool = Field(..., description="Whether query completed successfully.")
    error_code: SandboxErrorCode = Field(
        default=SandboxErrorCode.NONE,
        description="Normalized error category.",
    )
    error_message: str | None = Field(
        default=None,
        description="Human-readable error detail.",
    )
    session_id: str = Field(..., description="Queried session ID.")
    step: int | None = Field(
        default=None,
        description="Resolved step index for returned geometry.",
    )
    geometry: GeometryInfo | None = Field(
        default=None,
        description="Geometry summary if available.",
    )
    features: list[str] = Field(
        default_factory=list,
        description="Detected feature summary at this step.",
    )
    issues: list[str] = Field(
        default_factory=list,
        description="Known issues at this step.",
    )
    object_index: GeometryObjectIndex | None = Field(
        default=None,
        description="Queryable object index for high-precision inspection.",
    )
    matched_entity_ids: list[str] = Field(
        default_factory=list,
        description="Entity IDs that matched the query filter.",
    )
    next_solid_offset: int | None = Field(
        default=None,
        ge=0,
        description="Next solid offset if more solids exist.",
    )
    next_face_offset: int | None = Field(
        default=None,
        ge=0,
        description="Next face offset if more faces exist.",
    )
    next_edge_offset: int | None = Field(
        default=None,
        ge=0,
        description="Next edge offset if more edges exist.",
    )


class QueryTopologyInput(BaseModel):
    """Input for querying topology details from a session snapshot."""

    model_config = ConfigDict(extra="forbid")

    session_id: str = Field(..., description="Session ID to query.")
    step: int | None = Field(
        default=None,
        ge=1,
        description="Optional step index (1-based). Defaults to latest step.",
    )
    include_faces: bool = Field(
        default=True,
        description="Include face topology entries in the returned index.",
    )
    include_edges: bool = Field(
        default=True,
        description="Include edge topology entries in the returned index.",
    )
    max_items_per_type: int = Field(
        default=20,
        ge=1,
        le=200,
        description="Maximum number of topology objects returned per type.",
    )
    entity_ids: list[str] = Field(
        default_factory=list,
        description="Optional face_id/edge_id filter for focused retrieval.",
    )
    ref_ids: list[str] = Field(
        default_factory=list,
        description="Optional face_ref/edge_ref filter for focused retrieval.",
    )
    selection_hints: list[str] = Field(
        default_factory=list,
        description=(
            "Optional semantic hints such as top/outer/top_outer_edges/"
            "primary_outer_faces to rank and summarize candidate subsets."
        ),
    )
    requirement_text: str | None = Field(
        default=None,
        description=(
            "Optional requirement text used to derive requirement-aware topology "
            "candidate sets."
        ),
    )
    face_offset: int = Field(
        default=0,
        ge=0,
        description="Offset for face topology windowing.",
    )
    edge_offset: int = Field(
        default=0,
        ge=0,
        description="Offset for edge topology windowing.",
    )


class QueryTopologyOutput(BaseModel):
    """Topology query response."""

    model_config = ConfigDict(extra="forbid")

    success: bool = Field(..., description="Whether query completed successfully.")
    error_code: SandboxErrorCode = Field(
        default=SandboxErrorCode.NONE,
        description="Normalized error category.",
    )
    error_message: str | None = Field(
        default=None,
        description="Human-readable error detail.",
    )
    session_id: str = Field(..., description="Queried session ID.")
    step: int | None = Field(
        default=None,
        description="Resolved step index for returned topology.",
    )
    topology_index: TopologyObjectIndex | None = Field(
        default=None,
        description="Queryable topology index for high-precision inspection.",
    )
    matched_entity_ids: list[str] = Field(
        default_factory=list,
        description="Entity IDs that matched the query filter.",
    )
    matched_ref_ids: list[str] = Field(
        default_factory=list,
        description="Topology refs that matched the query filter.",
    )
    candidate_sets: list[TopologyCandidateSet] = Field(
        default_factory=list,
        description="Requirement-aware face/edge candidate subsets inferred from hints.",
    )
    applied_hints: list[str] = Field(
        default_factory=list,
        description="Normalized semantic hints applied during candidate generation.",
    )
    relation_index: RelationIndex | None = Field(
        default=None,
        description="Compact relation-state summary derived from topology evidence.",
    )
    next_face_offset: int | None = Field(
        default=None,
        ge=0,
        description="Next face offset if more faces exist.",
    )
    next_edge_offset: int | None = Field(
        default=None,
        ge=0,
        description="Next edge offset if more edges exist.",
    )


class RequirementCheckStatus(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    UNKNOWN = "unknown"


class RequirementClauseStatus(str, Enum):
    VERIFIED = "verified"
    CONTRADICTED = "contradicted"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    NOT_APPLICABLE = "not_applicable"


class RequirementClauseInterpretation(BaseModel):
    """Evidence-first interpretation of one requirement clause."""

    model_config = ConfigDict(extra="forbid")

    clause_id: str = Field(..., description="Stable clause identifier.")
    clause_text: str = Field(..., description="Original requirement clause text.")
    status: RequirementClauseStatus = Field(
        ..., description="Interpretation outcome for the clause."
    )
    evidence: str = Field(
        default="",
        description="Compact evidence summary supporting the interpretation.",
    )
    observation_tags: list[str] = Field(
        default_factory=list,
        description="Normalized observation tags used during interpretation.",
    )
    decision_hints: list[str] = Field(
        default_factory=list,
        description="Short hints that explain the next validation step.",
    )


class RequirementCheck(BaseModel):
    """Single requirement check result."""

    model_config = ConfigDict(extra="forbid")

    check_id: str = Field(..., description="Stable check identifier.")
    label: str = Field(..., description="Human-readable check label.")
    status: RequirementCheckStatus = Field(..., description="Check outcome.")
    blocking: bool = Field(
        default=True,
        description="Whether failure should block completion.",
    )
    evidence: str = Field(
        default="",
        description="Compact evidence summary for this check.",
    )


class ValidateRequirementInput(BaseModel):
    """Input for requirement validation against current CAD state."""

    model_config = ConfigDict(extra="forbid")

    session_id: str = Field(..., description="Session ID to validate.")
    requirements: dict[str, Any] = Field(
        default_factory=dict,
        description="Structured requirement payload.",
    )
    requirement_text: str | None = Field(
        default=None,
        description="Optional raw requirement text.",
    )
    step: int | None = Field(
        default=None,
        ge=1,
        description="Optional step index (1-based). Defaults to latest step.",
    )


class BlockerTaxonomyRecord(BaseModel):
    """Structured blocker-family mapping shared across runtime surfaces."""

    model_config = ConfigDict(extra="forbid")

    blocker_id: str = Field(..., description="Original blocker/check identifier.")
    normalized_blocker_id: str = Field(
        default="",
        description="Normalized blocker identifier used by taxonomy classification.",
    )
    family_ids: list[str] = Field(
        default_factory=list,
        description="Related semantic feature-family identifiers.",
    )
    feature_ids: list[str] = Field(
        default_factory=list,
        description="Related domain-kernel feature node identifiers.",
    )
    primary_feature_id: str = Field(
        default="feature.core_geometry",
        description="Primary feature node most directly associated with the blocker.",
    )
    evidence_source: str = Field(
        default="validation",
        description="Evidence source that produced this blocker mapping.",
    )
    completeness_relevance: str = Field(
        default="core",
        description="Whether this blocker came from core or diagnostic validation lanes.",
    )
    severity: str = Field(
        default="blocking",
        description="Blocking severity for this blocker mapping.",
    )
    recommended_repair_lane: str = Field(
        default="code_repair",
        description="Suggested runtime repair lane for this blocker family.",
    )
    observation_tags: list[str] = Field(
        default_factory=list,
        description="Normalized observation tags derived from blocker classification.",
    )
    decision_hints: list[str] = Field(
        default_factory=list,
        description="Short follow-up hints for the runtime and diagnostics surfaces.",
    )


class ValidateRequirementOutput(BaseModel):
    """Requirement validation response."""

    model_config = ConfigDict(extra="forbid")

    success: bool = Field(..., description="Whether validation completed successfully.")
    error_code: SandboxErrorCode = Field(
        default=SandboxErrorCode.NONE,
        description="Normalized error category.",
    )
    error_message: str | None = Field(
        default=None,
        description="Human-readable error detail.",
    )
    session_id: str = Field(..., description="Validated session ID.")
    step: int | None = Field(
        default=None,
        description="Resolved step index used for validation.",
    )
    is_complete: bool = Field(
        default=False,
        description="Whether model appears complete for provided requirements.",
    )
    blockers: list[str] = Field(
        default_factory=list,
        description="Blocking check IDs currently failing.",
    )
    checks: list[RequirementCheck] = Field(
        default_factory=list,
        description="Detailed requirement checks.",
    )
    core_checks: list[RequirementCheck] = Field(
        default_factory=list,
        description="Loop-safe core completion checks.",
    )
    diagnostic_checks: list[RequirementCheck] = Field(
        default_factory=list,
        description="Diagnostics-only validation checks kept for artifacts and debugging.",
    )
    clause_interpretations: list[RequirementClauseInterpretation] = Field(
        default_factory=list,
        description="Evidence-first clause interpretations used to derive legacy checks.",
    )
    coverage_confidence: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Confidence that the available evidence covers the requested clauses.",
    )
    insufficient_evidence: bool = Field(
        default=False,
        description="Whether any key requirement clause remained unresolved due to insufficient evidence.",
    )
    observation_tags: list[str] = Field(
        default_factory=list,
        description="Normalized observation tags extracted from the evidence bundle.",
    )
    decision_hints: list[str] = Field(
        default_factory=list,
        description="High-level follow-up hints derived from interpretation.",
    )
    blocker_taxonomy: list[BlockerTaxonomyRecord] = Field(
        default_factory=list,
        description="Structured blocker-family mapping shared across runtime, kernel sync, and benchmark diagnostics.",
    )
    relation_index: RelationIndex | None = Field(
        default=None,
        description="Compact relation-state summary derived from validation evidence.",
    )
    summary: str = Field(
        default="Validation not available",
        description="One-line validation summary.",
    )


class QueryFeatureProbesInput(BaseModel):
    """Input for family-specific geometric probe queries."""

    model_config = ConfigDict(extra="forbid")

    session_id: str = Field(..., description="Session ID to inspect.")
    requirements: dict[str, Any] = Field(
        default_factory=dict,
        description="Structured requirement payload.",
    )
    requirement_text: str | None = Field(
        default=None,
        description="Optional raw requirement text used for probe-family detection.",
    )
    step: int | None = Field(
        default=None,
        ge=1,
        description="Optional step index (1-based). Defaults to latest step.",
    )
    families: list[str] = Field(
        default_factory=list,
        description=(
            "Optional feature families to probe. If omitted, service infers a small "
            "family set from the current requirement."
        ),
    )
    timeout_seconds: int = Field(
        default=30,
        ge=1,
        le=600,
        description="Maximum service-side probe runtime budget in seconds.",
    )


class FeatureProbeRecord(BaseModel):
    """One family-level probe result."""

    model_config = ConfigDict(extra="forbid")

    family: str = Field(..., description="Probe family identifier.")
    summary: str = Field(default="", description="One-line probe summary.")
    success: bool = Field(
        default=False,
        description="Whether the current geometry appears to satisfy this family probe.",
    )
    confidence: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Heuristic confidence for the probe conclusion.",
    )
    signals: dict[str, Any] = Field(
        default_factory=dict,
        description="Compact structured evidence used by this probe.",
    )
    blockers: list[str] = Field(
        default_factory=list,
        description="Probe-local blockers or missing signals.",
    )
    recommended_next_tools: list[str] = Field(
        default_factory=list,
        description="Suggested next tools when the probe remains inconclusive.",
    )


class QueryFeatureProbesOutput(BaseModel):
    """Family-specific geometric probe response."""

    model_config = ConfigDict(extra="forbid")

    success: bool = Field(..., description="Whether probe query completed successfully.")
    error_code: SandboxErrorCode = Field(
        default=SandboxErrorCode.NONE,
        description="Normalized error category.",
    )
    error_message: str | None = Field(
        default=None,
        description="Human-readable error detail.",
    )
    session_id: str = Field(..., description="Queried session ID.")
    step: int | None = Field(
        default=None,
        description="Resolved step index for returned probe results.",
    )
    detected_families: list[str] = Field(
        default_factory=list,
        description="Feature families that were probed.",
    )
    probes: list[FeatureProbeRecord] = Field(
        default_factory=list,
        description="Family-specific probe results.",
    )
    summary: str = Field(
        default="No probe summary available",
        description="One-line summary for the overall probe pass.",
    )


class ExecuteBuild123dProbeInput(BaseModel):
    """Input for one-off diagnostic Build123d probe execution."""

    model_config = ConfigDict(extra="forbid")

    code: str = Field(
        ...,
        min_length=1,
        description=(
            "Diagnostic Build123d Python code. This probe must not persist any "
            "session state."
        ),
    )
    timeout_seconds: int = Field(
        default=120,
        ge=1,
        le=600,
        description="Maximum sandbox runtime in seconds.",
    )
    include_artifact_content: bool = Field(
        default=True,
        description="Include generated artifact bytes (base64) in the response.",
    )
    requirement_text: str | None = Field(
        default=None,
        description="Optional raw requirement text for diagnostic context.",
    )
    session_id: str | None = Field(
        default=None,
        description="Runtime-managed session ID for locating current context only.",
    )


class ExecuteBuild123dProbeOutput(BaseModel):
    """Structured output for diagnostics-only Build123d probe execution."""

    model_config = ConfigDict(extra="forbid")

    success: bool = Field(..., description="Whether sandbox execution succeeded.")
    stdout: str = Field(default="", description="Captured standard output.")
    stderr: str = Field(default="", description="Captured standard error.")
    error_code: SandboxErrorCode = Field(
        default=SandboxErrorCode.NONE,
        description="Normalized error category.",
    )
    error_message: str | None = Field(
        default=None,
        description="Human-readable error detail.",
    )
    output_files: list[str] = Field(
        default_factory=list,
        description="List of generated artifact filenames.",
    )
    artifacts: list[SandboxArtifact] = Field(
        default_factory=list,
        description="Artifact metadata and optional content.",
    )
    session_id: str | None = Field(
        default=None,
        description="Session context used for the probe, if any.",
    )
    step: int | None = Field(
        default=None,
        description="Resolved session step used for the probe context, if any.",
    )
    step_file: str | None = Field(
        default=None,
        description="Diagnostic STEP artifact filename when available.",
    )
    probe_summary: dict[str, Any] = Field(
        default_factory=dict,
        description="Compact diagnostic summary extracted from probe outputs.",
    )
    session_state_persisted: bool = Field(
        default=False,
        description="Always false; diagnostic probes must not mutate session state.",
    )


class RenderStyle(str, Enum):
    SHADED = "shaded"
    WIREFRAME = "wireframe"


class RenderViewInput(BaseModel):
    """Input for rendering a custom camera view from a CAD session state."""

    model_config = ConfigDict(extra="forbid")

    session_id: str = Field(..., description="Session ID to render.")
    step: int | None = Field(
        default=None,
        ge=1,
        description="Optional step index (1-based). Defaults to latest step.",
    )
    azimuth_deg: float = Field(
        default=35.0,
        ge=-360.0,
        le=360.0,
        description="Camera azimuth angle in degrees.",
    )
    elevation_deg: float = Field(
        default=25.0,
        ge=-180.0,
        le=180.0,
        description="Camera elevation angle in degrees.",
    )
    zoom: float = Field(
        default=1.0,
        ge=0.2,
        le=4.0,
        description="Zoom multiplier. >1 zooms in, <1 zooms out.",
    )
    width_px: int = Field(
        default=960,
        ge=320,
        le=2048,
        description="Output image width in pixels.",
    )
    height_px: int = Field(
        default=720,
        ge=240,
        le=2048,
        description="Output image height in pixels.",
    )
    style: RenderStyle = Field(
        default=RenderStyle.SHADED,
        description="Rendering style.",
    )
    target_entity_ids: list[str] = Field(
        default_factory=list,
        description=(
            "Optional entity IDs to focus on when framing the render view "
            "(for example F_xxx/E_xxx/S_xxx)."
        ),
    )
    focus_center: list[float] | None = Field(
        default=None,
        description="Optional explicit focus center [x, y, z] in model coordinates.",
    )
    focus_span: float | None = Field(
        default=None,
        gt=0.0,
        description="Optional focus span size around focus_center.",
    )
    focus_padding_ratio: float = Field(
        default=0.15,
        ge=0.0,
        le=3.0,
        description="Extra framing padding ratio when focus is applied.",
    )
    include_artifact_content: bool = Field(
        default=True,
        description="Include rendered image bytes (base64) in response.",
    )
    timeout_seconds: int = Field(
        default=90,
        ge=1,
        le=600,
        description="Maximum sandbox runtime in seconds for rendering.",
    )


class RenderViewOutput(BaseModel):
    """Custom view render response."""

    model_config = ConfigDict(extra="forbid")

    success: bool = Field(..., description="Whether rendering completed successfully.")
    error_code: SandboxErrorCode = Field(
        default=SandboxErrorCode.NONE,
        description="Normalized error category.",
    )
    error_message: str | None = Field(
        default=None,
        description="Human-readable error detail.",
    )
    session_id: str = Field(..., description="Rendered session ID.")
    step: int | None = Field(
        default=None,
        description="Resolved step index used for rendering.",
    )
    view_file: str | None = Field(
        default=None,
        description="Rendered image filename when successful.",
    )
    output_files: list[str] = Field(
        default_factory=list,
        description="List of available render output filenames.",
    )
    artifacts: list[SandboxArtifact] = Field(
        default_factory=list,
        description="Rendered artifact metadata and optional content.",
    )
    camera: dict[str, int | float | str | bool] = Field(
        default_factory=dict,
        description=(
            "Resolved camera parameters used for rendering, including fallback "
            "metadata when custom render output is unavailable."
        ),
    )
    focused_entity_ids: list[str] = Field(
        default_factory=list,
        description="Entity IDs used to build focused framing.",
    )
    focus_bbox: BoundingBox3D | None = Field(
        default=None,
        description="Applied focus bounding box for rendering.",
    )


ActionHistoryEntry.model_rebuild()
ExecuteBuild123dOutput.model_rebuild()
