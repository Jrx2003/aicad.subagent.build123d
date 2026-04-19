import base64
import datetime
import hashlib
import json
import math
import mimetypes
from pathlib import Path
import re
from typing import Any

from mcp.types import BlobResourceContents, ContentBlock, EmbeddedResource, TextContent

from sandbox.interface import SandboxRunner, SandboxResult
from sandbox_mcp_server.contracts import (
    ActionHistoryEntry,
    BlockerTaxonomyRecord,
    BoundingBox3D,
    CADActionInput,
    CADActionOutput,
    CADActionType,
    CADParamValue,
    CADStateSnapshot,
    CompletenessInfo,
    EdgeEntity,
    EvaluationMode,
    EvaluationStatus,
    ExecuteBuild123dInput,
    ExecuteBuild123dProbeInput,
    ExecuteBuild123dProbeOutput,
    ExecuteBuild123dOutput,
    FeatureProbeRecord,
    ExecutionEvaluation,
    FaceEntity,
    GeometryInfo,
    GeometryObjectIndex,
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
    RelationEntity,
    RelationFact,
    RelationGroup,
    RelationIndex,
    RelationSignal,
    RelationStatus,
    RenderStyle,
    RenderViewInput,
    RenderViewOutput,
    RequirementClauseInterpretation,
    RequirementClauseStatus,
    RequirementCheck,
    RequirementCheckStatus,
    SandboxArtifact,
    SandboxErrorCode,
    SketchLoopEntity,
    SketchPathEntity,
    SketchProfileEntity,
    SketchSegmentEntity,
    SketchState,
    SolidEntity,
    TopologyEdgeEntity,
    TopologyCandidateSet,
    TopologyFaceEntity,
    TopologyObjectIndex,
    ValidateRequirementInput,
    ValidateRequirementOutput,
)
from sandbox_mcp_server.evaluation_orchestrator import EvaluationOrchestrator
from sandbox_mcp_server.feature_probe_grounding import (
    build_feature_probe_grounding,
    recommended_next_tools_for_feature_probe_grounding,
)
from sandbox_mcp_server.registry import (
    RequirementSemantics,
    analyze_requirement_semantics,
    collect_requirement_topology_hints,
    extract_rectangular_notch_profile_spec,
    get_action_definition,
    infer_requirement_probe_families,
    normalize_action_params,
    requirement_requests_path_sweep,
    requirement_suggests_axisymmetric_profile,
    requirement_uses_operation_as_optional_method,
    parse_topology_ref,
)
from sandbox_mcp_server.validation_evidence import RequirementEvidenceBuilder
from sandbox_mcp_server.validation_grounding import (
    attach_clause_grounding_surface,
)
from sandbox_mcp_server.validation_interpretation import (
    RequirementInterpretationSummary,
    build_interpretation_summary_from_clauses,
    interpret_requirement_clauses,
)
from sandbox_mcp_server.validation_llm import (
    ValidationLLMAdjudicator,
    _INVALID_OUTPUT_SENTINEL,
    _PROVIDER_ERROR_PREFIX,
)
from common.blocker_taxonomy import (
    classify_blocker_taxonomy_many,
    normalize_probe_family_ids,
    probe_check_ids_for_family,
    recommended_probe_tools_for_family,
)

DEFAULT_STEP_MIME_TYPE = "application/step"
DEFAULT_RENDER_VIEW_FILENAME = "render_view.png"
GEOMETRY_OBJECT_CAPTURE_LIMIT = 120
_VALIDATION_LLM_MAX_ELIGIBLE_UNRESOLVED_CLAUSES = 3
_VALIDATION_LLM_MAX_ESTIMATED_PROMPT_CHARS = 7000
SKETCH_REF_PATTERN = re.compile(
    r"^(?P<kind>path|profile):(?P<step>[0-9]+):(?P<entity_id>[A-Z]_[A-Za-z0-9_]+)$"
)
BUILD123D_REPLAY_HELPER_CODE = (
    Path(__file__)
    .with_name("build123d_replay_helpers.py")
    .read_text(encoding="utf-8")
)


def partition_requirement_checks(
    checks: list[RequirementCheck],
) -> tuple[list[RequirementCheck], list[RequirementCheck]]:
    """Split validation checks into loop-safe core checks and diagnostics.

    Current policy is intentionally conservative:
    - blocking checks stay in the core lane because they participate in completion
    - non-blocking checks remain diagnostics for artifacts and debugging

    Runtime-side loop-safe demotion may further refine these lanes for the V2
    model loop without changing the raw tool contract here.
    """
    core_checks: list[RequirementCheck] = []
    diagnostic_checks: list[RequirementCheck] = []
    for check in checks:
        if bool(check.blocking):
            core_checks.append(check)
        else:
            diagnostic_checks.append(check)
    return core_checks, diagnostic_checks


def build_validation_blocker_taxonomy(
    *,
    core_checks: list[RequirementCheck],
    diagnostic_checks: list[RequirementCheck],
    clause_interpretations: list[RequirementClauseInterpretation] | None = None,
) -> list[BlockerTaxonomyRecord]:
    core_blockers = [
        check.check_id
        for check in core_checks
        if check.blocking and check.status == RequirementCheckStatus.FAIL
    ]
    diagnostic_blockers = [
        check.check_id
        for check in diagnostic_checks
        if check.status == RequirementCheckStatus.FAIL
    ]
    records = [
        *classify_blocker_taxonomy_many(
            core_blockers,
            evidence_source="validation",
            completeness_relevance="core",
        ),
        *classify_blocker_taxonomy_many(
            diagnostic_blockers,
            evidence_source="validation",
            completeness_relevance="diagnostic",
        ),
    ]
    return [
        _apply_clause_grounding_to_blocker_taxonomy_record(
            BlockerTaxonomyRecord(
                blocker_id=item.blocker_id,
                normalized_blocker_id=item.normalized_blocker_id,
                family_ids=list(item.family_ids),
                feature_ids=list(item.feature_ids),
                primary_feature_id=item.primary_feature_id,
                evidence_source=item.evidence_source,
                completeness_relevance=item.completeness_relevance,
                severity=item.severity,
                recommended_repair_lane=item.recommended_repair_lane,
                observation_tags=list(item.observation_tags),
                decision_hints=list(item.decision_hints),
            ),
            clause_interpretations=clause_interpretations or [],
        )
        for item in records
    ]


_LOCAL_FINISH_GROUNDING_HINTS = {"query_topology", "apply_cad_action"}
_SEMANTIC_REFRESH_GROUNDING_HINTS = {"query_feature_probes", "query_kernel_state", "query_geometry"}
_LOCAL_FINISH_FAMILY_BINDINGS = {"explicit_anchor_hole", "named_face_local_edit"}
_LOCAL_FINISH_BLOCKER_IDS = {
    "feature_hole",
    "feature_countersink",
    "feature_hole_position_alignment",
    "feature_local_anchor_alignment",
}


def _apply_clause_grounding_to_blocker_taxonomy_record(
    record: BlockerTaxonomyRecord,
    *,
    clause_interpretations: list[RequirementClauseInterpretation],
) -> BlockerTaxonomyRecord:
    relevant_clauses = _matching_clause_grounding_for_blocker(
        blocker_id=str(record.blocker_id or "").strip(),
        family_ids=list(record.family_ids or []),
        clause_interpretations=clause_interpretations,
    )
    if not relevant_clauses:
        return record
    repair_lane_override = _repair_lane_override_from_clause_grounding(
        blocker_id=str(record.blocker_id or "").strip(),
        relevant_clauses=relevant_clauses,
    )
    observation_tags = list(record.observation_tags or [])
    decision_hints = list(record.decision_hints or [])
    for clause in relevant_clauses:
        for tag in clause.observation_tags or []:
            normalized = str(tag or "").strip()
            if normalized and normalized not in observation_tags:
                observation_tags.append(normalized)
        family_binding = str(clause.family_binding or "").strip()
        if family_binding:
            binding_tag = f"family:{family_binding}"
            if binding_tag not in observation_tags:
                observation_tags.append(binding_tag)
        for hint in [*(clause.decision_hints or []), *(clause.repair_hints or [])]:
            normalized = str(hint or "").strip()
            if normalized and normalized not in decision_hints:
                decision_hints.append(normalized)
    return record.model_copy(
        update={
            "recommended_repair_lane": repair_lane_override
            or str(record.recommended_repair_lane or "").strip()
            or "code_repair",
            "observation_tags": observation_tags,
            "decision_hints": decision_hints,
        }
    )


def _matching_clause_grounding_for_blocker(
    *,
    blocker_id: str,
    family_ids: list[str],
    clause_interpretations: list[RequirementClauseInterpretation],
) -> list[RequirementClauseInterpretation]:
    if not blocker_id:
        return []
    normalized_family_ids = {
        str(item).strip()
        for item in family_ids
        if isinstance(item, str) and str(item).strip()
    }
    relevant: list[RequirementClauseInterpretation] = []
    for clause in clause_interpretations:
        if not isinstance(clause, RequirementClauseInterpretation):
            continue
        if clause.status == RequirementClauseStatus.VERIFIED:
            continue
        clause_id = str(clause.clause_id or "").strip()
        family_binding = str(clause.family_binding or "").strip()
        repair_hints = {
            str(item).strip()
            for item in (clause.repair_hints or [])
            if isinstance(item, str) and str(item).strip()
        }
        required_evidence_kinds = {
            str(item).strip()
            for item in (clause.required_evidence_kinds or [])
            if isinstance(item, str) and str(item).strip()
        }
        if clause_id == blocker_id:
            relevant.append(clause)
            continue
        if family_binding and family_binding in normalized_family_ids:
            if _LOCAL_FINISH_GROUNDING_HINTS & repair_hints:
                relevant.append(clause)
                continue
            if blocker_id in _LOCAL_FINISH_BLOCKER_IDS and family_binding in _LOCAL_FINISH_FAMILY_BINDINGS:
                relevant.append(clause)
                continue
            if "topology" in required_evidence_kinds and family_binding in _LOCAL_FINISH_FAMILY_BINDINGS:
                relevant.append(clause)
                continue
    return relevant


def _repair_lane_override_from_clause_grounding(
    *,
    blocker_id: str,
    relevant_clauses: list[RequirementClauseInterpretation],
) -> str | None:
    if not relevant_clauses:
        return None
    saw_probe_first = False
    for clause in relevant_clauses:
        repair_hints = {
            str(item).strip()
            for item in (clause.repair_hints or [])
            if isinstance(item, str) and str(item).strip()
        }
        required_evidence_kinds = {
            str(item).strip()
            for item in (clause.required_evidence_kinds or [])
            if isinstance(item, str) and str(item).strip()
        }
        family_binding = str(clause.family_binding or "").strip()
        if _LOCAL_FINISH_GROUNDING_HINTS & repair_hints:
            return "local_finish"
        if (
            blocker_id in _LOCAL_FINISH_BLOCKER_IDS
            and family_binding in _LOCAL_FINISH_FAMILY_BINDINGS
            and "topology" in required_evidence_kinds
        ):
            return "local_finish"
        if _SEMANTIC_REFRESH_GROUNDING_HINTS & repair_hints:
            saw_probe_first = True
    if saw_probe_first:
        return "probe_first"
    return None


class SessionState:
    """CAD session state for ACI-enhanced action tracking."""

    def __init__(
        self,
        history: list[ActionHistoryEntry] | None = None,
        current_step: int = 0,
        created_at: datetime.datetime | None = None,
    ) -> None:
        self.history = history or []
        self.current_step = current_step
        self.created_at = created_at or datetime.datetime.now()


class SessionManager:
    """Manager for CAD session state persistence."""

    def __init__(
        self,
        max_history_length: int = 50,
        storage_dir: Path | None = None,
    ) -> None:
        self._sessions: dict[str, SessionState] = {}
        self._max_history_length = max_history_length
        self._storage_dir = storage_dir or Path("/tmp/aicad_mcp_sessions")
        self._storage_dir.mkdir(parents=True, exist_ok=True)

    def get_or_create_session(self, session_id: str) -> SessionState:
        """Get existing session or create new one."""
        if session_id in self._sessions:
            return self._sessions[session_id]

        restored = self._load_session_from_disk(session_id)
        if restored is not None:
            self._sessions[session_id] = restored
            return restored

        # Create new session
        state = SessionState(
            history=[],
            current_step=0,
            created_at=datetime.datetime.now(),
        )
        self._sessions[session_id] = state
        self._save_session_to_disk(session_id, state)
        return state

    def clear_session(self, session_id: str) -> SessionState:
        """Clear session state and start fresh."""
        state = SessionState(
            history=[],
            current_step=0,
            created_at=datetime.datetime.now(),
        )
        self._sessions[session_id] = state
        self._save_session_to_disk(session_id, state)
        return state

    def append_action(
        self,
        session_id: str,
        entry: ActionHistoryEntry,
    ) -> SessionState | None:
        """Append action to session history."""
        if session_id not in self._sessions:
            return None  # Session not found

        session = self._sessions[session_id]
        session.history.append(entry)
        session.current_step += 1

        # Trim history if exceeds max length
        if len(session.history) > self._max_history_length:
            session.history = session.history[-self._max_history_length :]

        self._save_session_to_disk(session_id, session)
        return session

    def replace_history(
        self,
        session_id: str,
        history: list[ActionHistoryEntry],
    ) -> SessionState:
        """Replace a session history with a new authoritative replay base."""
        session = self.get_or_create_session(session_id)
        session.history = list(history)
        session.current_step = len(session.history)
        self._save_session_to_disk(session_id, session)
        return session

    def get_session_history(
        self,
        session_id: str,
    ) -> list[ActionHistoryEntry] | None:
        """Get complete action history for a session."""
        if session_id not in self._sessions:
            restored = self._load_session_from_disk(session_id)
            if restored is None:
                return None
            self._sessions[session_id] = restored

        return self._sessions[session_id].history.copy()

    def has_session(self, session_id: str) -> bool:
        """Check if session exists."""
        if session_id in self._sessions:
            return True
        return self._session_file_path(session_id).exists()

    def _session_file_path(self, session_id: str) -> Path:
        digest = hashlib.sha1(session_id.encode("utf-8")).hexdigest()
        return self._storage_dir / f"{digest}.json"

    def _save_session_to_disk(self, session_id: str, state: SessionState) -> None:
        payload = {
            "session_id": session_id,
            "current_step": state.current_step,
            "created_at": state.created_at.isoformat(),
            "history": [entry.model_dump(mode="json") for entry in state.history],
        }
        try:
            self._session_file_path(session_id).write_text(
                json.dumps(payload, ensure_ascii=True, sort_keys=True),
                encoding="utf-8",
            )
        except Exception:
            return

    def _load_session_from_disk(self, session_id: str) -> SessionState | None:
        path = self._session_file_path(session_id)
        if not path.exists():
            return None
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            history_raw = raw.get("history", [])
            history = [
                ActionHistoryEntry.model_validate(item)
                for item in history_raw
                if isinstance(item, dict)
            ]
            created_at_raw = raw.get("created_at")
            created_at = (
                datetime.datetime.fromisoformat(created_at_raw)
                if isinstance(created_at_raw, str)
                else datetime.datetime.now()
            )
            current_step_raw = raw.get("current_step", len(history))
            current_step = (
                int(current_step_raw)
                if isinstance(current_step_raw, (int, float, str))
                else len(history)
            )
            return SessionState(
                history=history,
                current_step=current_step,
                created_at=created_at,
            )
        except Exception:
            return None


class SandboxMCPService:
    """Execution service isolated from MCP transport details."""

    def __init__(
        self,
        runner: SandboxRunner,
        evaluation_orchestrator: EvaluationOrchestrator | None = None,
        validation_adjudicator: ValidationLLMAdjudicator | None = None,
        max_history_length: int = 50,
    ) -> None:
        self._runner = runner
        self._evaluation_orchestrator = (
            evaluation_orchestrator or EvaluationOrchestrator()
        )
        self._validation_adjudicator = validation_adjudicator
        self._session_manager = SessionManager(max_history_length=max_history_length)

    async def execute(self, request: ExecuteBuild123dInput) -> ExecuteBuild123dOutput:
        """Execute Build123d code through injected sandbox runner."""
        code = request.code.strip()
        if not code:
            return ExecuteBuild123dOutput(
                success=False,
                stdout="",
                stderr="Code is empty",
                error_code=SandboxErrorCode.INVALID_REQUEST,
                error_message="Code is empty",
                output_files=[],
                artifacts=[],
                session_id=request.session_id,
                step=None,
                step_file=None,
                snapshot=None,
                session_state_persisted=False,
                evaluation=self._not_requested_evaluation(),
            )

        result = await self._runner.execute(
            code=code,
            timeout=request.timeout_seconds,
            requirement_text=request.requirement_text,
            session_id=request.session_id,
        )
        snapshot_result = await self._hydrate_execute_snapshot_result(
            request=request,
            build123d_code=code,
            primary_result=result,
        )
        effective_result = self._merge_execute_results(
            primary=result,
            supplemental=snapshot_result,
        )

        filenames = list(
            dict.fromkeys(
                [
                    *effective_result.output_files,
                    *effective_result.output_file_contents.keys(),
                ]
            )
        )
        artifacts: list[SandboxArtifact] = []

        for filename in filenames:
            content = effective_result.output_file_contents.get(filename)
            artifact = SandboxArtifact(
                filename=filename,
                uri=f"sandbox://artifacts/{filename}",
                mime_type=self._resolve_mime_type(filename),
                size_bytes=len(content) if content is not None else 0,
                content_base64=(
                    base64.b64encode(content).decode("ascii")
                    if content is not None and request.include_artifact_content
                    else None
                ),
            )
            artifacts.append(artifact)

        error_code = (
            SandboxErrorCode.NONE
            if result.success
            else self._map_error_code(result.error_message, result.stderr)
        )
        step_file = self._pick_primary_step_file(filenames)
        persisted_snapshot: CADStateSnapshot | None = None
        persisted_step: int | None = None
        session_state_persisted = False
        if result.success and request.session_id:
            persisted_snapshot, persisted_step = self._persist_execute_build123d_result(
                session_id=request.session_id,
                build123d_code=code,
                result=effective_result,
                output_files=filenames,
                step_file=step_file,
            )
            session_state_persisted = (
                persisted_snapshot is not None and persisted_step is not None
            )
        evaluation = await self._evaluate(
            request=request,
            sandbox_result=result,
            error_code=error_code,
        )

        return ExecuteBuild123dOutput(
            success=result.success,
            stdout=result.stdout,
            stderr=result.stderr,
            error_code=error_code,
            error_message=result.error_message,
            output_files=filenames,
            artifacts=artifacts,
            session_id=request.session_id,
            step=persisted_step,
            step_file=step_file,
            snapshot=persisted_snapshot,
            session_state_persisted=session_state_persisted,
            evaluation=evaluation,
        )

    async def _hydrate_execute_snapshot_result(
        self,
        *,
        request: ExecuteBuild123dInput,
        build123d_code: str,
        primary_result: SandboxResult,
    ) -> SandboxResult | None:
        if not primary_result.success:
            return None
        if "geometry_info.json" in primary_result.output_file_contents:
            return None

        analysis_code: str | None = None
        step_file = primary_result.step_file or self._pick_primary_step_file(
            list(
                dict.fromkeys(
                    [
                        *primary_result.output_files,
                        *primary_result.output_file_contents.keys(),
                    ]
                )
            )
        )
        if step_file is not None:
            step_bytes = primary_result.output_file_contents.get(step_file)
            if isinstance(step_bytes, bytes) and step_bytes:
                analysis_code = self._build_step_snapshot_analysis_code(step_bytes)
        if analysis_code is None:
            analysis_code = self._build_execute_snapshot_analysis_code(build123d_code)

        try:
            analysis_result = await self._runner.execute(
                code=analysis_code,
                timeout=request.timeout_seconds,
                requirement_text=request.requirement_text,
                session_id=None,
            )
        except Exception:
            return None
        if not analysis_result.success:
            return None
        if "geometry_info.json" not in analysis_result.output_file_contents:
            return None
        return analysis_result

    def _merge_execute_results(
        self,
        *,
        primary: SandboxResult,
        supplemental: SandboxResult | None,
    ) -> SandboxResult:
        if supplemental is None:
            return primary
        merged_output_files = list(
            dict.fromkeys([*primary.output_files, *supplemental.output_files])
        )
        merged_output_contents = dict(primary.output_file_contents)
        for filename, content in supplemental.output_file_contents.items():
            merged_output_contents.setdefault(filename, content)
        return SandboxResult(
            success=primary.success,
            stdout=primary.stdout,
            stderr=primary.stderr,
            output_files=merged_output_files,
            output_file_contents=merged_output_contents,
            error_message=primary.error_message,
            evaluation=primary.evaluation,
            session_id=primary.session_id,
            step=primary.step,
            step_file=primary.step_file or supplemental.step_file,
            snapshot=primary.snapshot or supplemental.snapshot,
            session_state_persisted=primary.session_state_persisted,
        )

    def _persist_execute_build123d_result(
        self,
        *,
        session_id: str,
        build123d_code: str,
        result: SandboxResult,
        output_files: list[str],
        step_file: str | None,
    ) -> tuple[CADStateSnapshot | None, int | None]:
        if not result.success:
            return None, None

        snapshot = self._parse_snapshot(result)
        step = 1
        snapshot.step = step
        snapshot.topology_index = self._retag_topology_index_step(
            snapshot.topology_index,
            step=step,
        )
        history_entry = ActionHistoryEntry(
            step=step,
            action_type=CADActionType.SNAPSHOT,
            action_params={
                "source": "execute_build123d",
                "build123d_code": build123d_code,
                "output_files": output_files,
                "step_file": step_file,
            },
            result_snapshot=snapshot,
            success=True,
            error=None,
            warnings=list(snapshot.warnings),
            blockers=list(snapshot.blockers),
        )
        self._session_manager.replace_history(session_id, [history_entry])
        return snapshot, step

    def _pick_primary_step_file(self, filenames: list[str]) -> str | None:
        for filename in filenames:
            lowered = filename.lower()
            if lowered.endswith(".step") or lowered.endswith(".stp"):
                return filename
        return None

    def _runtime_wrap_build123d_code(self, build123d_code: str) -> str:
        wrapped = build123d_code.rstrip()
        if not wrapped:
            return "result = result"
        prelude = (
            "from build123d import *\n"
            "__aicad_last_show_object = None\n"
            "__aicad_last_result = None\n"
            "def show_object(obj, *args, **kwargs):\n"
            "    global __aicad_last_show_object\n"
            "    __aicad_last_show_object = obj\n"
            "def debug(*args, **kwargs):\n"
            "    return None\n"
            "def __aicad_as_exportable(candidate):\n"
            "    try:\n"
            "        if hasattr(candidate, 'part'):\n"
            "            part = candidate.part\n"
            "            if hasattr(part, 'solids') and len(list(part.solids())) > 0:\n"
            "                return part\n"
            "        if hasattr(candidate, 'wrapped') and hasattr(candidate, 'solids') and len(list(candidate.solids())) > 0:\n"
            "            return candidate\n"
            "        if hasattr(candidate, 'solids'):\n"
            "            solids = [solid for solid in list(candidate.solids()) if hasattr(solid, 'wrapped')]\n"
            "            if len(solids) == 1:\n"
            "                return solids[0]\n"
            "            if len(solids) > 1:\n"
            "                try:\n"
            "                    return Compound(children=solids)\n"
            "                except Exception:\n"
            "                    return Compound(solids)\n"
            "    except Exception:\n"
            "        return None\n"
            "    return None\n"
            "def __aicad_resolve_export_part():\n"
            "    if 'result' in globals():\n"
            "        result_part = __aicad_as_exportable(result)\n"
            "        if result_part is not None:\n"
            "            return result_part\n"
            "    if 'part' in globals():\n"
            "        part_part = __aicad_as_exportable(part)\n"
            "        if part_part is not None:\n"
            "            return part_part\n"
            "    if 'model' in globals():\n"
            "        model_part = __aicad_as_exportable(model)\n"
            "        if model_part is not None:\n"
            "            return model_part\n"
            "    if '__aicad_last_result' in globals():\n"
            "        last_result_part = __aicad_as_exportable(__aicad_last_result)\n"
            "        if last_result_part is not None:\n"
            "            return last_result_part\n"
            "    if __aicad_last_show_object is not None:\n"
            "        show_object_part = __aicad_as_exportable(__aicad_last_show_object)\n"
            "        if show_object_part is not None:\n"
            "            return show_object_part\n"
            "    return None\n"
        )
        epilogue = (
            "\nif 'result' in globals():\n"
            "    result = __aicad_as_exportable(result) or result\n"
            "    __aicad_last_result = result\n"
            "elif __aicad_last_show_object is not None:\n"
            "    result = __aicad_as_exportable(__aicad_last_show_object) or __aicad_last_show_object\n"
            "    __aicad_last_result = result\n"
            "else:\n"
            "    result = Part()\n"
            "    __aicad_last_result = result\n"
        )
        return f"{prelude}\n{wrapped}\n{epilogue}"

    def _build_execute_snapshot_analysis_code(self, build123d_code: str) -> str:
        code_lines = [
            "import json",
            "from pathlib import Path",
            "import hashlib",
            self._runtime_wrap_build123d_code(build123d_code),
            *self._geometry_analysis_code_lines(),
        ]
        return "\n".join(line for line in code_lines if line is not None)

    def _build_step_snapshot_analysis_code(self, step_bytes: bytes) -> str:
        step_b64 = base64.b64encode(step_bytes).decode("ascii")
        code_lines = [
            "import base64",
            "import hashlib",
            "import json",
            "from pathlib import Path",
            "from build123d import import_step",
            "",
            "output_dir = Path('/output')",
            "output_dir.mkdir(parents=True, exist_ok=True)",
            "step_path = output_dir / 'hydrated_model.step'",
            f"step_path.write_bytes(base64.b64decode({step_b64!r}))",
            "result = import_step(step_path)",
            "def _aicad_solid_entity_id(solid):",
            "    solid_bbox = _aicad_bbox(solid)",
            "    solid_center = _aicad_shape_center(solid)",
            "    solid_volume = _aicad_shape_volume(solid)",
            "    solid_area = _aicad_shape_area(solid)",
            "    return _aicad_entity_id('S', [",
            "        solid_volume, solid_area,",
            "        solid_center[0], solid_center[1], solid_center[2],",
            "        solid_bbox['xlen'], solid_bbox['ylen'], solid_bbox['zlen'],",
            "    ])",
            *self._geometry_analysis_code_lines(),
        ]
        return "\n".join(code_lines)

    async def apply_cad_action(self, request: CADActionInput) -> CADActionOutput:
        """Apply a CAD action and return updated state snapshot with ACI enhancements."""
        # Handle CLEAR_SESSION action
        if request.action_type == CADActionType.CLEAR_SESSION:
            if not request.session_id:
                return CADActionOutput(
                    success=False,
                    stdout="",
                    stderr="CLEAR_SESSION requires session_id",
                    error_code=SandboxErrorCode.INVALID_REQUEST,
                    error_message="CLEAR_SESSION requires session_id",
                    snapshot=self._empty_snapshot(),
                    executed_action={"type": "clear_session", "params": {}},
                    step_file=None,
                    output_files=[],
                    artifacts=[],
                    action_history=[],
                    suggestions=[],
                    completeness=None,
                )
            self._session_manager.clear_session(request.session_id)
            return CADActionOutput(
                success=True,
                stdout="Session cleared",
                stderr="",
                error_code=SandboxErrorCode.NONE,
                error_message=None,
                snapshot=self._empty_snapshot(),
                executed_action={"type": "clear_session", "params": {}},
                step_file=None,
                output_files=[],
                artifacts=[],
                action_history=[],
                suggestions=["Session cleared, ready for new modeling sequence"],
                completeness=None,
            )

        # Handle GET_HISTORY action (should be routed to get_history_tool)
        if request.action_type == CADActionType.GET_HISTORY:
            return CADActionOutput(
                success=False,
                stdout="",
                stderr="Use get_history tool instead",
                error_code=SandboxErrorCode.INVALID_REQUEST,
                error_message="Use get_history tool instead",
                snapshot=self._empty_snapshot(),
                executed_action={"type": "get_history", "params": {}},
                step_file=None,
                output_files=[],
                artifacts=[],
                action_history=[],
                suggestions=[],
                completeness=None,
            )

        # Handle MODIFY_ACTION action
        if request.action_type == CADActionType.MODIFY_ACTION:
            return await self._handle_modify_action(request)

        # Get or create session
        session_id = request.session_id or "default"
        if request.clear_session:
            self._session_manager.clear_session(session_id)

        session = self._session_manager.get_or_create_session(session_id)

        # Rebuild entire model from action history
        # This ensures state is properly constructed across action calls
        action_history = self._session_manager.get_session_history(session_id) or []
        normalized_action_params = normalize_action_params(
            request.action_type, request.action_params
        )
        normalized_action_params = self._resolve_action_params_against_history(
            action_type=request.action_type,
            action_params=normalized_action_params,
            action_history=action_history,
        )
        reference_error = self._validate_action_references(
            action_type=request.action_type,
            action_params=normalized_action_params,
            action_history=action_history,
        )
        if reference_error is not None:
            return CADActionOutput(
                success=False,
                stdout="",
                stderr=reference_error,
                error_code=SandboxErrorCode.INVALID_REFERENCE,
                error_message=reference_error,
                snapshot=self._empty_snapshot(),
                executed_action={
                    "type": request.action_type.value,
                    "params": normalized_action_params,
                },
                step_file=None,
                output_files=[],
                artifacts=[],
                action_history=action_history,
                suggestions=[
                    "Query topology again and use fresh face_ref/edge_refs before retrying."
                ],
                completeness=None,
            )
        (
            normalized_action_params,
            contract_error,
            contract_suggestions,
        ) = self._validate_action_contract(
            action_type=request.action_type,
            action_params=normalized_action_params,
            action_history=action_history,
        )
        if contract_error is not None:
            return CADActionOutput(
                success=False,
                stdout="",
                stderr=contract_error,
                error_code=SandboxErrorCode.INVALID_REQUEST,
                error_message=contract_error,
                snapshot=self._empty_snapshot(),
                executed_action={
                    "type": request.action_type.value,
                    "params": normalized_action_params,
                },
                step_file=None,
                output_files=[],
                artifacts=[],
                action_history=action_history,
                suggestions=contract_suggestions,
                completeness=None,
            )

        normalized_request = request.model_copy(
            update={"action_params": normalized_action_params}
        )
        code = self._rebuild_model_code(action_history, normalized_request)
        if not code:
            return CADActionOutput(
                success=False,
                stdout="",
                stderr=f"Unsupported action type: {request.action_type}",
                error_code=SandboxErrorCode.INVALID_REQUEST,
                error_message=f"Unsupported action type: {request.action_type}",
                snapshot=self._empty_snapshot(),
                executed_action={
                    "type": request.action_type.value,
                    "params": normalized_action_params,
                },
                step_file=None,
                output_files=[],
                artifacts=[],
                action_history=[],
                suggestions=[],
                completeness=None,
            )

        # Execute the complete code (rebuilds entire model)
        result = await self._runner.execute(
            code=code,
            timeout=request.timeout_seconds,
            requirement_text=None,
        )

        # Parse the snapshot from the result
        snapshot = self._parse_snapshot(result)

        # Update snapshot step to reflect actual session step
        current_step = session.current_step + 1
        snapshot.step = current_step
        snapshot.topology_index = self._retag_topology_index_step(
            snapshot.topology_index,
            current_step,
        )

        # Build action history entry
        history_entry = ActionHistoryEntry(
            step=current_step,
            action_type=request.action_type,
            action_params=normalized_action_params,
            result_snapshot=snapshot,
            success=result.success,
            error=result.error_message if not result.success else None,
            warnings=list(snapshot.warnings),
            blockers=list(snapshot.blockers),
        )

        # Append to session history
        self._session_manager.append_action(session_id, history_entry)

        # Get complete history for response
        action_history = self._session_manager.get_session_history(session_id) or []

        # Generate suggestions based on geometry analysis
        suggestions = self._generate_suggestions(snapshot, request.action_type)

        # Generate completeness diagnostics
        completeness = self._generate_completeness(snapshot, action_history)

        # Build artifacts
        filenames = list(
            dict.fromkeys([*result.output_files, *result.output_file_contents.keys()])
        )
        artifacts: list[SandboxArtifact] = []

        for filename in filenames:
            content = result.output_file_contents.get(filename)
            artifact = SandboxArtifact(
                filename=filename,
                uri=f"sandbox://artifacts/{filename}",
                mime_type=self._resolve_mime_type(filename),
                size_bytes=len(content) if content is not None else 0,
                content_base64=(
                    base64.b64encode(content).decode("ascii")
                    if content is not None and request.include_artifact_content
                    else None
                ),
            )
            artifacts.append(artifact)

        error_code = (
            SandboxErrorCode.NONE
            if result.success
            else self._map_error_code(result.error_message, result.stderr)
        )

        return CADActionOutput(
            success=result.success,
            stdout=result.stdout,
            stderr=result.stderr,
            error_code=error_code,
            error_message=result.error_message,
            snapshot=snapshot,
            executed_action={
                "type": request.action_type.value,
                "params": normalized_action_params,
            },
            step_file=next((f for f in filenames if f.endswith(".step")), None),
            output_files=filenames,
            artifacts=artifacts,
            action_history=action_history,
            suggestions=suggestions,
            completeness=completeness,
        )

    async def query_snapshot(self, request: QuerySnapshotInput) -> QuerySnapshotOutput:
        """Query a snapshot by session and optional step."""
        entry, history, error_message = self._resolve_history_entry(
            session_id=request.session_id,
            step=request.step,
        )
        if error_message is not None:
            return QuerySnapshotOutput(
                success=False,
                error_code=SandboxErrorCode.INVALID_REQUEST,
                error_message=error_message,
                session_id=request.session_id,
                step=request.step,
                snapshot=None,
                action_history=history if request.include_history else [],
            )

        assert entry is not None
        return QuerySnapshotOutput(
            success=True,
            error_code=SandboxErrorCode.NONE,
            error_message=None,
            session_id=request.session_id,
            step=entry.step,
            snapshot=entry.result_snapshot,
            action_history=history if request.include_history else [],
        )

    async def query_sketch(self, request: QuerySketchInput) -> QuerySketchOutput:
        """Query structured pre-solid sketch/path/profile state."""
        entry, history, error_message = self._resolve_history_entry(
            session_id=request.session_id,
            step=request.step,
        )
        if error_message is not None:
            return QuerySketchOutput(
                success=False,
                error_code=SandboxErrorCode.INVALID_REQUEST,
                error_message=error_message,
                session_id=request.session_id,
                step=request.step,
                sketch_state=None,
            )

        assert entry is not None
        sketch_state = self._build_sketch_state(
            history=history[: entry.step],
            snapshot=entry.result_snapshot,
            step=entry.step,
        )
        relation_index = self._build_sketch_relation_index(
            sketch_state,
            step=entry.step,
        )
        return QuerySketchOutput(
            success=True,
            error_code=SandboxErrorCode.NONE,
            error_message=None,
            session_id=request.session_id,
            step=entry.step,
            sketch_state=sketch_state,
            relation_index=relation_index,
        )

    async def query_geometry(self, request: QueryGeometryInput) -> QueryGeometryOutput:
        """Query geometry facts by session and optional step."""
        entry, _, error_message = self._resolve_history_entry(
            session_id=request.session_id,
            step=request.step,
        )
        if error_message is not None:
            return QueryGeometryOutput(
                success=False,
                error_code=SandboxErrorCode.INVALID_REQUEST,
                error_message=error_message,
                session_id=request.session_id,
                step=request.step,
                geometry=None,
                features=[],
                issues=[],
                object_index=None,
                matched_entity_ids=[],
                next_solid_offset=None,
                next_face_offset=None,
                next_edge_offset=None,
            )

        assert entry is not None
        snapshot = entry.result_snapshot
        (
            object_index,
            matched_entity_ids,
            next_solid_offset,
            next_face_offset,
            next_edge_offset,
        ) = self._slice_geometry_object_index(
            source=snapshot.geometry_objects,
            include_solids=request.include_solids,
            include_faces=request.include_faces,
            include_edges=request.include_edges,
            max_items_per_type=request.max_items_per_type,
            entity_ids=request.entity_ids,
            solid_offset=request.solid_offset,
            face_offset=request.face_offset,
            edge_offset=request.edge_offset,
        )
        return QueryGeometryOutput(
            success=True,
            error_code=SandboxErrorCode.NONE,
            error_message=None,
            session_id=request.session_id,
            step=entry.step,
            geometry=snapshot.geometry,
            features=snapshot.features,
            issues=snapshot.issues,
            object_index=object_index,
            matched_entity_ids=matched_entity_ids,
            next_solid_offset=next_solid_offset,
            next_face_offset=next_face_offset,
            next_edge_offset=next_edge_offset,
        )

    async def query_topology(self, request: QueryTopologyInput) -> QueryTopologyOutput:
        """Query step-local topology refs and adjacency facts by session/step."""
        entry, _, error_message = self._resolve_history_entry(
            session_id=request.session_id,
            step=request.step,
        )
        if error_message is not None:
            return QueryTopologyOutput(
                success=False,
                error_code=SandboxErrorCode.INVALID_REQUEST,
                error_message=error_message,
                session_id=request.session_id,
                step=request.step,
                topology_index=None,
                matched_entity_ids=[],
                matched_ref_ids=[],
                candidate_sets=[],
                applied_hints=[],
                next_face_offset=None,
                next_edge_offset=None,
            )

        assert entry is not None
        applied_hints = self._normalize_topology_selection_hints(
            selection_hints=request.selection_hints,
            requirement_text=request.requirement_text,
        )
        (
            topology_index,
            matched_entity_ids,
            matched_ref_ids,
            candidate_sets,
            applied_hints,
            next_face_offset,
            next_edge_offset,
        ) = self._slice_topology_object_index(
            source=entry.result_snapshot.topology_index,
            include_faces=request.include_faces,
            include_edges=request.include_edges,
            max_items_per_type=request.max_items_per_type,
            entity_ids=request.entity_ids,
            ref_ids=request.ref_ids,
            selection_hints=applied_hints,
            family_ids=request.family_ids,
            face_offset=request.face_offset,
            edge_offset=request.edge_offset,
        )
        relation_index = self._build_topology_relation_index(
            topology_index,
            candidate_sets,
            step=entry.step,
        )
        return QueryTopologyOutput(
            success=True,
            error_code=SandboxErrorCode.NONE,
            error_message=None,
            session_id=request.session_id,
            step=entry.step,
            topology_index=topology_index,
            matched_entity_ids=matched_entity_ids,
            matched_ref_ids=matched_ref_ids,
            candidate_sets=candidate_sets,
            applied_hints=applied_hints,
            relation_index=relation_index,
            next_face_offset=next_face_offset,
            next_edge_offset=next_edge_offset,
        )

    async def query_feature_probes(
        self,
        request: QueryFeatureProbesInput,
    ) -> QueryFeatureProbesOutput:
        """Query family-specific probe summaries against the current session snapshot."""
        entry, history, error_message = self._resolve_history_entry(
            session_id=request.session_id,
            step=request.step,
        )
        if error_message is not None:
            return QueryFeatureProbesOutput(
                success=False,
                error_code=SandboxErrorCode.INVALID_REQUEST,
                error_message=error_message,
                session_id=request.session_id,
                step=request.step,
                detected_families=[],
                probes=[],
                summary="Feature probes failed: no usable snapshot",
            )

        assert entry is not None
        scoped_history = history[: entry.step]
        requirement_text = request.requirement_text or self._requirement_text_from_payload(
            request.requirements
        )
        families = self._detect_feature_probe_families(
            requirement_text=requirement_text,
            requirements=request.requirements,
            requested_families=request.families,
        )
        checks = self._build_requirement_checks(
            snapshot=entry.result_snapshot,
            history=scoped_history,
            requirements=request.requirements,
            requirement_text=requirement_text,
        )
        probe_records = self._build_feature_probe_records(
            families=families,
            snapshot=entry.result_snapshot,
            history=scoped_history,
            checks=checks,
            requirements=request.requirements,
            requirement_text=requirement_text,
        )
        success_count = sum(1 for record in probe_records if record.success)
        summary = (
            f"Feature probes: {success_count}/{len(probe_records)} families currently look satisfied"
            if probe_records
            else "Feature probes found no relevant family heuristics"
        )
        return QueryFeatureProbesOutput(
            success=True,
            error_code=SandboxErrorCode.NONE,
            error_message=None,
            session_id=request.session_id,
            step=entry.step,
            detected_families=families,
            probes=probe_records,
            summary=summary,
        )

    async def execute_build123d_probe(
        self,
        request: ExecuteBuild123dProbeInput,
    ) -> ExecuteBuild123dProbeOutput:
        """Execute diagnostics-only Build123d probe code without mutating session state."""
        probe_code = request.code
        if request.session_id:
            history = self._session_manager.get_session_history(request.session_id) or []
            if history:
                probe_code = self._build_execute_probe_code(
                    action_history=history,
                    probe_code=request.code,
                )
        result = await self._runner.execute(
            code=probe_code,
            timeout=request.timeout_seconds,
            requirement_text=request.requirement_text,
            session_id=None,
        )
        hydrate_request = ExecuteBuild123dInput(
            code=probe_code,
            timeout_seconds=request.timeout_seconds,
            include_artifact_content=request.include_artifact_content,
            requirement_text=request.requirement_text,
            session_id=None,
        )
        snapshot_result = await self._hydrate_execute_snapshot_result(
            request=hydrate_request,
            build123d_code=probe_code,
            primary_result=result,
        )
        effective_result = self._merge_execute_results(
            primary=result,
            supplemental=snapshot_result,
        )
        filenames = list(
            dict.fromkeys(
                [
                    *effective_result.output_files,
                    *effective_result.output_file_contents.keys(),
                ]
            )
        )
        artifacts = self._build_artifacts(
            filenames=filenames,
            output_file_contents=effective_result.output_file_contents,
            include_artifact_content=request.include_artifact_content,
        )
        error_code = (
            SandboxErrorCode.NONE
            if result.success
            else self._map_error_code(result.error_message, result.stderr)
        )
        resolved_step = None
        if request.session_id:
            history = self._session_manager.get_session_history(request.session_id) or []
            if history:
                resolved_step = len(history)
        probe_summary = self._build_execute_probe_summary(
            result=effective_result,
            filenames=filenames,
            requirement_text=request.requirement_text,
        )

        return ExecuteBuild123dProbeOutput(
            success=result.success,
            stdout=result.stdout,
            stderr=result.stderr,
            error_code=error_code,
            error_message=result.error_message,
            output_files=filenames,
            artifacts=artifacts,
            session_id=request.session_id,
            step=resolved_step,
            step_file=self._pick_primary_step_file(filenames),
            probe_summary=probe_summary,
            session_state_persisted=False,
        )

    async def render_view(self, request: RenderViewInput) -> RenderViewOutput:
        """Render a custom camera view for a session snapshot."""
        entry, history, error_message = self._resolve_history_entry(
            session_id=request.session_id,
            step=request.step,
        )
        if error_message is not None:
            return RenderViewOutput(
                success=False,
                error_code=SandboxErrorCode.INVALID_REQUEST,
                error_message=error_message,
                session_id=request.session_id,
                step=request.step,
                view_file=None,
                output_files=[],
                artifacts=[],
                camera=self._build_camera_payload(request),
                focused_entity_ids=[],
                focus_bbox=None,
            )

        assert entry is not None
        focus_bbox, focused_entity_ids = self._resolve_render_focus(
            snapshot=entry.result_snapshot,
            request=request,
        )
        replay_history = history[: entry.step]
        render_code = self._build_render_view_code(
            action_history=replay_history,
            request=request,
            focus_bbox=focus_bbox,
        )
        result = await self._runner.execute(
            code=render_code,
            timeout=request.timeout_seconds,
            requirement_text=None,
        )
        filenames = list(
            dict.fromkeys([*result.output_files, *result.output_file_contents.keys()])
        )
        artifacts = self._build_artifacts(
            filenames=filenames,
            output_file_contents=result.output_file_contents,
            include_artifact_content=request.include_artifact_content,
        )
        camera_payload = self._build_camera_payload(request)
        view_file = (
            DEFAULT_RENDER_VIEW_FILENAME
            if DEFAULT_RENDER_VIEW_FILENAME in filenames
            else None
        )
        error_code = (
            SandboxErrorCode.NONE
            if result.success
            else self._map_error_code(result.error_message, result.stderr)
        )
        error_message = result.error_message
        render_warning = self._extract_render_warning(
            stdout=result.stdout,
            stderr=result.stderr,
        )
        if result.success and view_file is None:
            fallback_view_file = self._select_render_fallback_file(
                filenames=filenames,
                request=request,
            )
            if fallback_view_file is not None:
                view_file = fallback_view_file
                camera_payload["render_source"] = "preview_fallback"
                camera_payload["render_fallback_used"] = True
                camera_payload["fallback_view_file"] = fallback_view_file
                if render_warning:
                    camera_payload["render_warning"] = render_warning
                error_code = SandboxErrorCode.NONE
                error_message = None
            else:
                camera_payload["render_source"] = "none"
                camera_payload["render_fallback_used"] = False
                camera_payload["fallback_view_file"] = ""
                if render_warning:
                    camera_payload["render_warning"] = render_warning
                error_code = SandboxErrorCode.EXECUTION_ERROR
                error_message = self._build_render_missing_error_message(
                    filenames=filenames,
                    stdout=result.stdout,
                    stderr=result.stderr,
                )
        elif view_file is not None:
            if render_warning:
                camera_payload["render_source"] = "custom_render_with_warning"
                camera_payload["render_warning"] = render_warning
            else:
                camera_payload["render_source"] = "custom_render"
            camera_payload["render_fallback_used"] = False
            camera_payload["fallback_view_file"] = ""
        else:
            camera_payload["render_source"] = "none"
            camera_payload["render_fallback_used"] = False
            camera_payload["fallback_view_file"] = ""
            if render_warning:
                camera_payload["render_warning"] = render_warning

        return RenderViewOutput(
            success=result.success and view_file is not None,
            error_code=error_code,
            error_message=error_message,
            session_id=request.session_id,
            step=entry.step,
            view_file=view_file,
            output_files=filenames,
            artifacts=artifacts,
            camera=camera_payload,
            focused_entity_ids=focused_entity_ids,
            focus_bbox=focus_bbox,
        )

    async def validate_requirement(
        self, request: ValidateRequirementInput
    ) -> ValidateRequirementOutput:
        """Validate requirement coverage against current session snapshot."""
        entry, history, error_message = self._resolve_history_entry(
            session_id=request.session_id,
            step=request.step,
        )
        if error_message is not None:
            return ValidateRequirementOutput(
                success=False,
                error_code=SandboxErrorCode.INVALID_REQUEST,
                error_message=error_message,
                session_id=request.session_id,
                step=request.step,
                is_complete=False,
                blockers=["no_history"],
                checks=[],
                core_checks=[],
                diagnostic_checks=[],
                clause_interpretations=[],
                coverage_confidence=0.0,
                insufficient_evidence=False,
                observation_tags=[],
                decision_hints=[],
                blocker_taxonomy=[],
                summary="Validation failed: no usable snapshot",
            )

        assert entry is not None
        requirement_text = request.requirement_text or self._requirement_text_from_payload(
            request.requirements
        )
        legacy_checks = self._build_requirement_checks(
            snapshot=entry.result_snapshot,
            history=history,
            requirements=request.requirements,
            requirement_text=requirement_text,
        )
        evidence_bundle = RequirementEvidenceBuilder.build(
            snapshot=entry.result_snapshot,
            history=history,
            requirements=request.requirements,
            requirement_text=requirement_text,
        )
        interpretation = interpret_requirement_clauses(
            bundle=evidence_bundle,
            requirements=request.requirements,
            requirement_text=requirement_text,
            supplemental_checks=legacy_checks,
        )
        interpretation = await self._maybe_apply_validation_llm_adjudication(
            requirement_text=requirement_text,
            bundle=evidence_bundle,
            interpretation=interpretation,
            history=history,
        )
        interpretation, grounding_surface = attach_clause_grounding_surface(
            interpretation
        )

        has_unresolved_clause_coverage = bool(interpretation.insufficient_evidence)
        has_contradicted_clause_coverage = any(
            clause.status == RequirementClauseStatus.CONTRADICTED
            for clause in interpretation.clause_interpretations
        )
        use_legacy_family_checks = self._has_family_specific_requirement_checks(
            legacy_checks
        )
        can_demote_family_checks = (
            use_legacy_family_checks
            and not has_unresolved_clause_coverage
            and not has_contradicted_clause_coverage
            and interpretation.coverage_confidence >= 0.75
        )

        if can_demote_family_checks:
            checks = [
                check
                for check in legacy_checks
                if not self._is_family_specific_requirement_check(check)
            ]
            checks = self._merge_projected_clause_checks(
                base_checks=checks,
                projected_checks=interpretation.legacy_checks,
            )
            has_insufficient_evidence = False
        else:
            checks = self._merge_projected_clause_checks(
                base_checks=list(legacy_checks),
                projected_checks=interpretation.legacy_checks,
            )
            has_insufficient_evidence = has_unresolved_clause_coverage

        core_checks, diagnostic_checks = partition_requirement_checks(checks)
        blocker_taxonomy = build_validation_blocker_taxonomy(
            core_checks=core_checks,
            diagnostic_checks=diagnostic_checks,
            clause_interpretations=interpretation.clause_interpretations,
        )
        blockers = [
            check.check_id
            for check in core_checks
            if check.blocking and check.status == RequirementCheckStatus.FAIL
        ]
        is_complete = len(blockers) == 0 and not has_insufficient_evidence
        if is_complete:
            summary = "Requirement validation passed"
        elif blockers:
            summary = f"Requirement validation has {len(blockers)} blocker(s)"
        else:
            summary = "Requirement validation has insufficient evidence"
        relation_index = None

        return ValidateRequirementOutput(
            success=True,
            error_code=SandboxErrorCode.NONE,
            error_message=None,
            session_id=request.session_id,
            step=entry.step,
            is_complete=is_complete,
            blockers=blockers,
            checks=checks,
            core_checks=core_checks,
            diagnostic_checks=diagnostic_checks,
            clause_interpretations=interpretation.clause_interpretations,
            coverage_confidence=interpretation.coverage_confidence,
            insufficient_evidence=has_insufficient_evidence,
            observation_tags=interpretation.observation_tags,
            decision_hints=interpretation.decision_hints,
            grounding_sources=grounding_surface.get("grounding_sources") or [],
            grounding_strength=str(grounding_surface.get("grounding_strength") or "none"),
            required_evidence_kinds=grounding_surface.get("required_evidence_kinds") or [],
            grounding_gap_reasons=grounding_surface.get("grounding_gap_reasons") or [],
            overclaim_guard=grounding_surface.get("overclaim_guard"),
            repair_hints=grounding_surface.get("repair_hints") or [],
            family_bindings=grounding_surface.get("family_bindings") or [],
            blocker_taxonomy=blocker_taxonomy,
            relation_index=relation_index,
            summary=summary,
        )

    async def _maybe_apply_validation_llm_adjudication(
        self,
        *,
        requirement_text: str,
        bundle: Any,
        interpretation: RequirementInterpretationSummary,
        history: list[ActionHistoryEntry],
    ) -> RequirementInterpretationSummary:
        if self._validation_adjudicator is None:
            return interpretation
        if not interpretation.insufficient_evidence:
            return interpretation
        adjudication_clauses = self._validation_llm_adjudication_clauses(interpretation)
        eligible_unresolved = [
            clause
            for clause in adjudication_clauses
            if clause.status == RequirementClauseStatus.INSUFFICIENT_EVIDENCE
        ]
        if not eligible_unresolved:
            return self._validation_llm_skip_update(
                interpretation,
                reason="no_eligible_clause",
                detail=None,
            )
        if len(eligible_unresolved) > _VALIDATION_LLM_MAX_ELIGIBLE_UNRESOLVED_CLAUSES:
            return self._validation_llm_skip_update(
                interpretation,
                reason="eligible_clause_budget_exceeded",
                detail=str(len(eligible_unresolved)),
            )
        estimated_prompt_chars = self._estimate_validation_llm_prompt_chars(
            requirement_text=requirement_text,
            bundle=bundle,
            clauses=adjudication_clauses,
            history=history,
        )
        if estimated_prompt_chars > _VALIDATION_LLM_MAX_ESTIMATED_PROMPT_CHARS:
            return self._validation_llm_skip_update(
                interpretation,
                reason="estimated_prompt_budget_exceeded",
                detail=(
                    f"{estimated_prompt_chars}/"
                    f"{_VALIDATION_LLM_MAX_ESTIMATED_PROMPT_CHARS}"
                ),
            )
        adjudicated = await self._validation_adjudicator.adjudicate(
            requirement_text=requirement_text,
            bundle=bundle,
            clauses=adjudication_clauses,
            history=history,
        )
        if adjudicated is None:
            return interpretation
        if not adjudicated.clauses:
            diagnostic_update = self._validation_llm_diagnostic_update(
                interpretation,
                summary=str(adjudicated.summary or ""),
            )
            return diagnostic_update or interpretation
        decisions_by_clause_id = {
            decision.clause_id: decision
            for decision in adjudicated.clauses
            if isinstance(decision.clause_id, str)
            and decision.clause_id.strip()
            and float(decision.confidence) >= 0.7
        }
        if not decisions_by_clause_id:
            return interpretation
        updated_clauses: list[RequirementClauseInterpretation] = []
        changed = False
        for clause in interpretation.clause_interpretations:
            decision = decisions_by_clause_id.get(clause.clause_id)
            if (
                decision is None
                or clause.status != RequirementClauseStatus.INSUFFICIENT_EVIDENCE
                or decision.status == RequirementClauseStatus.INSUFFICIENT_EVIDENCE
            ):
                updated_clauses.append(clause)
                continue
            if not self._clause_is_eligible_for_llm_adjudication(clause):
                updated_clauses.append(
                    clause.model_copy(
                        update={
                            "observation_tags": list(
                                dict.fromkeys(
                                    [
                                        *clause.observation_tags,
                                        "validation:geometry_grounding_required",
                                    ]
                                )
                            ),
                            "decision_hints": list(
                                dict.fromkeys(
                                    [
                                        *clause.decision_hints,
                                        "inspect_more_evidence",
                                        "require geometry-grounded verification before completion",
                                    ]
                                )
                            ),
                        }
                    )
                )
                changed = True
                continue
            changed = True
            updated_clauses.append(
                clause.model_copy(
                    update={
                        "status": decision.status,
                        "evidence": str(decision.evidence or clause.evidence or "").strip(),
                        "observation_tags": list(
                            dict.fromkeys([*clause.observation_tags, "llm:validation_adjudicated"])
                        ),
                        "decision_hints": list(
                            dict.fromkeys(
                                [*(decision.decision_hints or []), *clause.decision_hints]
                            )
                        ),
                    }
                )
            )
        if not changed:
            return interpretation
        return build_interpretation_summary_from_clauses(updated_clauses, bundle=bundle)

    def _validation_llm_adjudication_clauses(
        self,
        interpretation: RequirementInterpretationSummary,
    ) -> list[RequirementClauseInterpretation]:
        clauses: list[RequirementClauseInterpretation] = []
        for clause in interpretation.clause_interpretations:
            if clause.status != RequirementClauseStatus.INSUFFICIENT_EVIDENCE:
                clauses.append(clause)
                continue
            if self._clause_is_eligible_for_llm_adjudication(clause):
                clauses.append(clause)
        return clauses

    def _validation_llm_skip_update(
        self,
        interpretation: RequirementInterpretationSummary,
        *,
        reason: str,
        detail: str | None,
    ) -> RequirementInterpretationSummary:
        hint = f"validation_llm_skipped:{reason}" + (f":{detail}" if detail else "")
        return interpretation.model_copy(
            update={
                "observation_tags": list(
                    dict.fromkeys([*interpretation.observation_tags, "validation:llm_skipped"])
                ),
                "decision_hints": list(
                    dict.fromkeys(
                        [
                            *interpretation.decision_hints,
                            "fallback_to_evidence_first_clause_interpretation",
                            hint,
                        ]
                    )
                ),
            }
        )

    def _estimate_validation_llm_prompt_chars(
        self,
        *,
        requirement_text: str,
        bundle: Any,
        clauses: list[RequirementClauseInterpretation],
        history: list[ActionHistoryEntry],
    ) -> int:
        unresolved_clauses = [
            clause
            for clause in clauses
            if clause.status == RequirementClauseStatus.INSUFFICIENT_EVIDENCE
        ]
        payload = {
            "requirement_text": requirement_text[:6000],
            "unresolved_clauses": [
                {
                    "clause_id": clause.clause_id,
                    "clause_text": clause.clause_text[:800],
                    "current_status": clause.status.value,
                    "current_evidence": clause.evidence[:600],
                    "observation_tags": list(clause.observation_tags[:8]),
                    "decision_hints": list(clause.decision_hints[:8]),
                }
                for clause in unresolved_clauses
            ],
            "all_clause_summaries": [
                {
                    "clause_id": clause.clause_id,
                    "clause_text": clause.clause_text[:400],
                    "status": clause.status.value,
                    "evidence": clause.evidence[:300],
                }
                for clause in clauses
            ],
            "geometry_facts": self._validation_llm_budget_surface(
                getattr(bundle, "geometry_facts", {}),
                max_depth=3,
                max_list_items=10,
            ),
            "topology_facts": self._validation_llm_budget_surface(
                getattr(bundle, "topology_facts", {}),
                max_depth=3,
                max_list_items=8,
            ),
            "process_facts": self._validation_llm_budget_surface(
                getattr(bundle, "process_facts", {}),
                max_depth=3,
                max_list_items=8,
            ),
            "latest_code_excerpt": self._validation_llm_latest_code_excerpt(history),
        }
        try:
            return len(json.dumps(payload, ensure_ascii=True, separators=(",", ":")))
        except Exception:
            return len(requirement_text)

    def _validation_llm_budget_surface(
        self,
        value: Any,
        *,
        max_depth: int,
        max_list_items: int,
    ) -> Any:
        if max_depth <= 0:
            return "<truncated>"
        if isinstance(value, dict):
            compact: dict[str, Any] = {}
            for index, (key, item) in enumerate(value.items()):
                if index >= max_list_items:
                    compact["__truncated__"] = True
                    break
                compact[str(key)] = self._validation_llm_budget_surface(
                    item,
                    max_depth=max_depth - 1,
                    max_list_items=max_list_items,
                )
            return compact
        if isinstance(value, list):
            return [
                self._validation_llm_budget_surface(
                    item,
                    max_depth=max_depth - 1,
                    max_list_items=max_list_items,
                )
                for item in value[:max_list_items]
            ]
        if isinstance(value, str):
            return value[:800]
        return value

    def _validation_llm_latest_code_excerpt(
        self,
        history: list[ActionHistoryEntry],
    ) -> str:
        for entry in reversed(history):
            action_params = entry.action_params if isinstance(entry.action_params, dict) else {}
            for key in ("build123d_code", "cad_code", "code"):
                value = action_params.get(key)
                if isinstance(value, str) and value.strip():
                    return value[:1800]
        return ""

    def _validation_llm_diagnostic_update(
        self,
        interpretation: RequirementInterpretationSummary,
        *,
        summary: str,
    ) -> RequirementInterpretationSummary | None:
        normalized = summary.strip()
        if not normalized:
            return None
        if normalized.startswith(_PROVIDER_ERROR_PREFIX):
            detail = normalized[len(_PROVIDER_ERROR_PREFIX) :].strip() or "provider_error"
            return interpretation.model_copy(
                update={
                    "observation_tags": list(
                        dict.fromkeys([*interpretation.observation_tags, "validation:llm_provider_error"])
                    ),
                    "decision_hints": list(
                        dict.fromkeys(
                            [
                                *interpretation.decision_hints,
                                "fallback_to_evidence_first_clause_interpretation",
                                f"validation_llm_provider_error:{detail}",
                            ]
                        )
                    ),
                }
            )
        if normalized == _INVALID_OUTPUT_SENTINEL:
            return interpretation.model_copy(
                update={
                    "observation_tags": list(
                        dict.fromkeys([*interpretation.observation_tags, "validation:llm_invalid_output"])
                    ),
                    "decision_hints": list(
                        dict.fromkeys(
                            [
                                *interpretation.decision_hints,
                                "fallback_to_evidence_first_clause_interpretation",
                                "validation_llm_invalid_output",
                            ]
                        )
                    ),
                }
            )
        return None

    def _clause_is_eligible_for_llm_adjudication(
        self,
        clause: RequirementClauseInterpretation,
    ) -> bool:
        text = str(getattr(clause, "clause_text", "") or "").strip().lower()
        if not text:
            return False
        observation_tags = {
            str(tag).strip().lower()
            for tag in getattr(clause, "observation_tags", []) or []
            if isinstance(tag, str) and str(tag).strip()
        }
        if observation_tags.intersection(
            {
                "clause:coordinate",
                "clause:local_feature",
                "clause:notch_like",
                "clause:pattern",
            }
        ):
            return False
        if re.search(r"\b[xyz]\s*=\s*-?\d", text):
            return False
        unsafe_text_markers = (
            "centered at",
            "centred at",
            "top face",
            "bottom face",
            "left",
            "right",
            "through the",
            "direction",
            "union this",
        )
        return not any(marker in text for marker in unsafe_text_markers)

    def _has_family_specific_requirement_checks(
        self,
        checks: list[RequirementCheck],
    ) -> bool:
        return any(self._is_family_specific_requirement_check(check) for check in checks)

    def _is_family_specific_requirement_check(self, check: RequirementCheck) -> bool:
        check_id = str(getattr(check, "check_id", "") or "").strip()
        if check_id == "feature_spherical_recess_host_plane_opening":
            return False
        return check_id.startswith("feature_") or check_id in {
            "path_disconnected",
            "missing_profile",
        }

    def _merge_projected_clause_checks(
        self,
        *,
        base_checks: list[RequirementCheck],
        projected_checks: list[RequirementCheck],
    ) -> list[RequirementCheck]:
        merged = list(base_checks)
        seen = {
            check.check_id
            for check in merged
            if isinstance(check.check_id, str) and check.check_id.strip()
        }
        for check in projected_checks:
            if not isinstance(check.check_id, str) or not check.check_id.strip():
                continue
            if check.check_id in seen:
                continue
            seen.add(check.check_id)
            merged.append(check)
        return merged

    def _resolve_history_entry(
        self,
        session_id: str,
        step: int | None,
    ) -> tuple[ActionHistoryEntry | None, list[ActionHistoryEntry], str | None]:
        history = self._session_manager.get_session_history(session_id) or []
        if not history:
            return None, [], "No action history found for this session"

        if step is None:
            for entry in reversed(history):
                snapshot = getattr(entry, "result_snapshot", None)
                if (
                    getattr(entry, "success", False)
                    and snapshot is not None
                    and bool(getattr(snapshot, "success", True))
                ):
                    return entry, history, None
            return history[-1], history, None

        resolved_step = step
        if resolved_step < 1 or resolved_step > len(history):
            return (
                None,
                history,
                f"Invalid step {resolved_step}; valid range is 1..{len(history)}",
            )

        return history[resolved_step - 1], history, None

    def _requirement_text_from_payload(self, requirements: dict[str, Any]) -> str:
        description = requirements.get("description")
        if isinstance(description, str) and description.strip():
            return description.strip()
        return json.dumps(requirements, ensure_ascii=False, sort_keys=True)

    def _detect_feature_probe_families(
        self,
        *,
        requirement_text: str,
        requirements: dict[str, Any],
        requested_families: list[str],
    ) -> list[str]:
        if requested_families:
            return normalize_probe_family_ids(
                [
                    item.strip()
                    for item in requested_families
                    if isinstance(item, str) and item.strip()
                ]
            )
        semantics = analyze_requirement_semantics(requirements, requirement_text)
        families = infer_requirement_probe_families(
            requirements=requirements,
            requirement_text=requirement_text,
            semantics=semantics,
        )
        if not families:
            families.append("general_geometry")
        return normalize_probe_family_ids(families)

    def _build_feature_probe_records(
        self,
        *,
        families: list[str],
        snapshot: CADStateSnapshot,
        history: list[ActionHistoryEntry],
        checks: list[RequirementCheck],
        requirements: dict[str, Any],
        requirement_text: str,
    ) -> list[FeatureProbeRecord]:
        check_index = {check.check_id: check for check in checks}
        semantics = analyze_requirement_semantics(requirements, requirement_text)
        records: list[FeatureProbeRecord] = []
        for family in families:
            records.append(
                self._build_feature_probe_record(
                    family=family,
                    snapshot=snapshot,
                    history=history,
                    check_index=check_index,
                    semantics=semantics,
                    requirement_text=requirement_text,
                )
            )
        return records

    def _build_feature_probe_record(
        self,
        *,
        family: str,
        snapshot: CADStateSnapshot,
        history: list[ActionHistoryEntry],
        check_index: dict[str, RequirementCheck],
        semantics: RequirementSemantics,
        requirement_text: str,
    ) -> FeatureProbeRecord:
        normalized_families = normalize_probe_family_ids([family]) if family else []
        family = normalized_families[0] if normalized_families else "general_geometry"
        selected_check_ids = probe_check_ids_for_family(family)
        selected_checks = [
            check_index[check_id]
            for check_id in selected_check_ids
            if check_id in check_index
        ]
        passed_count = sum(
            1
            for check in selected_checks
            if check.status == RequirementCheckStatus.PASS
        )
        failed_checks = [
            check for check in selected_checks if check.status == RequirementCheckStatus.FAIL
        ]
        blockers = [check.check_id for check in failed_checks]
        solids = int(snapshot.geometry.solids or 0)
        volume = float(snapshot.geometry.volume or 0.0)
        bbox = [
            float(item)
            for item in (snapshot.geometry.bbox or [])
            if isinstance(item, (int, float))
        ]
        bbox_min = [
            float(item)
            for item in (snapshot.geometry.bbox_min or [])
            if isinstance(item, (int, float))
        ]
        bbox_max = [
            float(item)
            for item in (snapshot.geometry.bbox_max or [])
            if isinstance(item, (int, float))
        ]
        signals: dict[str, Any] = {
            "solids": solids,
            "volume": volume,
            "bbox": bbox,
            "bbox_min": bbox_min,
            "bbox_max": bbox_max,
            "bbox_min_span": min(bbox) if bbox else 0.0,
            "bbox_max_span": max(bbox) if bbox else 0.0,
            "feature_count": len(snapshot.features),
            "history_steps": len(history),
        }
        signals.update(self._snapshot_detached_fragment_signals(snapshot))
        expected_bbox = self._extract_overall_bbox_dimensions(requirement_text)
        if expected_bbox:
            signals["expected_bbox"] = expected_bbox
        expected_part_count = self._extract_expected_part_count(requirement_text)
        if expected_part_count is not None:
            signals["expected_part_count"] = expected_part_count
        if family == "half_shell":
            signals.update(
                self._half_shell_probe_signals(
                    snapshot=snapshot,
                    requirement_text=requirement_text,
                )
            )
        if family == "nested_hollow_section":
            signals["prefers_explicit_inner_void_cut"] = bool(
                semantics.prefers_explicit_inner_void_cut
            )
        elif family == "annular_groove":
            signals["mentions_revolved_groove_cut"] = bool(
                semantics.mentions_revolved_groove_cut
            )
        elif family == "explicit_anchor_hole":
            expected_centers = self._infer_expected_local_feature_centers(
                requirement_text
            )
            expected_center_count = (
                len(expected_centers)
                if expected_centers
                else self._infer_expected_local_feature_count(
                    requirement_text,
                    family="explicit_anchor_hole",
                )
            )
            realized_centers = self._snapshot_collect_subtractive_feature_centers(
                snapshot,
                face_targets=semantics.face_targets,
            )
            signals["expected_local_centers"] = expected_centers
            if expected_center_count is not None:
                signals["expected_local_center_count"] = expected_center_count
            signals["realized_centers"] = realized_centers
            layout_hints = self._explicit_anchor_probe_layout_hints(
                requirement_text=requirement_text,
                expected_centers=expected_centers,
            )
            if layout_hints:
                signals.update(layout_hints)
        elif family == "spherical_recess":
            signals["mentions_spherical_recess"] = bool(
                getattr(semantics, "mentions_spherical_recess", False)
            )
            signals["expected_local_centers"] = self._infer_expected_local_feature_centers(
                requirement_text
            )
            signals["realized_centers"] = self._snapshot_collect_subtractive_feature_centers(
                snapshot,
                face_targets=semantics.face_targets,
            )
        elif family == "pattern_distribution":
            signals["mentions_pattern"] = bool(getattr(semantics, "mentions_pattern", False))
            signals["expected_local_centers"] = self._infer_expected_local_feature_centers(
                requirement_text
            )
            signals["realized_centers"] = self._snapshot_collect_subtractive_feature_centers(
                snapshot,
                face_targets=semantics.face_targets,
            )
        elif family == "named_face_local_edit":
            requested_face_targets = [
                target
                for target in semantics.face_targets
                if target in {"top", "bottom", "front", "back", "left", "right", "side"}
            ]
            requested_side_face_targets = [
                target
                for target in semantics.face_targets
                if target in {"front", "back", "left", "right"}
            ]
            expects_subtractive = semantics.mentions_subtractive_edit and not (
                semantics.mentions_additive_face_feature
            )
            expects_additive = semantics.mentions_additive_face_feature and not (
                semantics.mentions_subtractive_edit
            )
            side_target_grounded = False
            if requested_side_face_targets:
                first_solid_index = self._find_first_solid_index(history)
                side_target_grounded = self._history_has_post_solid_face_feature(
                    history=history,
                    first_solid_index=first_solid_index,
                    face_targets=tuple(requested_side_face_targets),
                    expect_subtractive=expects_subtractive,
                    expect_additive=expects_additive,
                )
                if not side_target_grounded:
                    side_target_grounded = self._snapshot_has_target_face_feature(
                        snapshot=snapshot,
                        face_targets=tuple(requested_side_face_targets),
                        expect_subtractive=expects_subtractive,
                        expect_additive=expects_additive,
                        include_aligned_host_faces=expects_additive,
                    )
            signals["requested_face_targets"] = requested_face_targets
            signals["requested_side_face_targets"] = requested_side_face_targets
            signals["specific_side_target_grounded"] = side_target_grounded
        elif family == "orthogonal_union":
            signals["mentions_multi_plane_additive_union"] = bool(
                semantics.mentions_multi_plane_additive_union
            )
        elif family == "axisymmetric_profile":
            signals["mentions_named_axis_pose"] = any(
                check_id in check_index
                for check_id in ("feature_named_axis_axisymmetric_pose",)
            )

        if selected_checks:
            confidence = max(0.2, min(1.0, passed_count / len(selected_checks)))
            success = not failed_checks
            summary = (
                f"{family}: {passed_count}/{len(selected_checks)} relevant checks currently pass"
            )
            if family == "explicit_anchor_hole":
                normalized_centers = signals.get("normalized_local_centers")
                if isinstance(normalized_centers, list) and normalized_centers:
                    summary = (
                        f"{summary}; centered host frame suggests normalized centers "
                        f"{normalized_centers}"
                    )
        else:
            material_volume = abs(float(volume)) if isinstance(volume, (int, float)) else 0.0
            confidence = 0.35 if solids > 0 and material_volume > 0.0 else 0.15
            if family == "general_geometry":
                geometry_blockers = self._general_geometry_probe_blockers(
                    solids=solids,
                    bbox=bbox,
                    expected_bbox=expected_bbox,
                    expected_part_count=expected_part_count,
                    suspected_detached_fragment_count=int(
                        signals.get("suspected_detached_fragment_count") or 0
                    ),
                )
                blockers.extend(
                    blocker_id
                    for blocker_id in geometry_blockers
                    if blocker_id not in blockers
                )
                success = solids > 0 and material_volume > 0.0 and not blockers
                if blockers:
                    summary = (
                        f"{family}: generic geometry signals expose {len(blockers)} "
                        "grounding blocker(s)"
                    )
                else:
                    summary = (
                        f"{family}: generic solid/volume/part-count/bbox signals currently look stable"
                    )
            else:
                if family == "half_shell":
                    half_shell_blockers = self._half_shell_probe_blockers(
                        solids=solids,
                        bbox=bbox,
                        expected_bbox=expected_bbox,
                        expected_part_count=expected_part_count,
                        suspected_detached_fragment_count=int(
                            signals.get("suspected_detached_fragment_count") or 0
                        ),
                        hinge_requested=bool(signals.get("hinge_requested")),
                        hinge_like_cylinder_count=int(
                            signals.get("hinge_like_cylinder_count") or 0
                        ),
                    )
                    blockers.extend(
                        blocker_id
                        for blocker_id in half_shell_blockers
                        if blocker_id not in blockers
                    )
                    success = solids > 0 and material_volume > 0.0 and not blockers
                    if blockers:
                        summary = (
                            f"{family}: clamshell hinge/split signals expose {len(blockers)} "
                            "grounding blocker(s)"
                        )
                    else:
                        summary = (
                            f"{family}: clamshell hinge/split signals currently look grounded"
                        )
                    confidence = 0.55 if success else 0.3
                else:
                    success = (
                        solids > 0 and material_volume > 0.0 and family == "general_geometry"
                    )
                    summary = (
                        f"{family}: no dedicated validator checks mapped yet; using generic solid/volume signals"
                    )

        probe_grounding = build_feature_probe_grounding(
            family=family,
            signals=signals,
            blockers=blockers,
        )
        if (
            family == "named_face_local_edit"
            and success
            and "local_host_target_not_grounded" in probe_grounding["grounding_blockers"]
        ):
            success = False
            confidence = min(confidence, 0.45)
            summary = (
                f"{family}: local edit checks pass, but the requested side-face host is "
                "not yet grounded by topology-aware evidence"
            )
        recommended_next_tools = recommended_next_tools_for_feature_probe_grounding(
            base_tools=recommended_probe_tools_for_family(family),
            required_evidence_kinds=probe_grounding["required_evidence_kinds"],
            anchor_summary=probe_grounding["anchor_summary"],
            grounding_blockers=probe_grounding["grounding_blockers"],
        )

        return FeatureProbeRecord(
            family=family,
            summary=summary,
            success=success,
            confidence=confidence,
            signals=signals,
            blockers=blockers,
            recommended_next_tools=recommended_next_tools,
            family_binding=probe_grounding["family_binding"],
            required_evidence_kinds=probe_grounding["required_evidence_kinds"],
            anchor_summary=probe_grounding["anchor_summary"],
            grounding_blockers=probe_grounding["grounding_blockers"],
        )

    def _explicit_anchor_probe_layout_hints(
        self,
        *,
        requirement_text: str | None,
        expected_centers: list[list[float]],
    ) -> dict[str, Any]:
        if not expected_centers:
            return {}
        host_dims = self._extract_rectangular_host_face_dimensions(requirement_text)
        normalized_centers = self._translate_rectangular_host_face_local_centers(
            requirement_text,
            expected_centers,
        )
        if (
            host_dims is None
            or not normalized_centers
            or self._center_sets_match_2d_direct(
                expected_centers,
                normalized_centers,
                tolerance=0.01,
            )
        ):
            return {}
        width, height = host_dims
        return {
            "host_frame_dimensions": [round(width, 6), round(height, 6)],
            "host_frame_translation_from_corner": [
                round(-width / 2.0, 6),
                round(-height / 2.0, 6),
            ],
            "normalized_local_centers": normalized_centers,
            "coordinate_frame_hint": (
                "The point coordinates appear to come from a rectangular host-face sketch. "
                "If the host solid is centered about the origin, translate the sketch "
                "coordinates into the centered host frame before placing the hole centers."
            ),
        }

    def _build_execute_probe_summary(
        self,
        *,
        result: SandboxResult,
        filenames: list[str],
        requirement_text: str | None,
    ) -> dict[str, Any]:
        probe_family_ids = infer_requirement_probe_families(
            {"description": requirement_text} if requirement_text else None,
            requirement_text,
        )
        snapshot = self._parse_snapshot(result)
        geometry = snapshot.geometry if snapshot is not None else None
        summary = {
            "success": bool(result.success),
            "step_file_exists": self._pick_primary_step_file(filenames) is not None,
            "output_file_count": len(filenames),
            "solids": int(geometry.solids or 0) if geometry is not None else 0,
            "faces": int(geometry.faces or 0) if geometry is not None else 0,
            "edges": int(geometry.edges or 0) if geometry is not None else 0,
            "volume": float(geometry.volume or 0.0) if geometry is not None else 0.0,
            "bbox": list(geometry.bbox or []) if geometry is not None else [],
            "bbox_min": list(geometry.bbox_min or []) if geometry is not None else [],
            "bbox_max": list(geometry.bbox_max or []) if geometry is not None else [],
            "issues": list(snapshot.issues) if snapshot is not None else [],
            "actionable_family_ids": [],
            # Runtime treats execute_build123d_probe as actionable only when a future
            # family-specific probe explicitly marks it so. Generic geometry artifacts
            # alone should not reopen blind code repair.
            "actionable": False,
            "actionable_reason": (
                "Generic execute_build123d_probe results are diagnostic-only unless a "
                "family-specific probe summary explicitly marks them as repair-ready."
            ),
        }
        if snapshot is None or not result.success:
            if "path_sweep" in probe_family_ids or requirement_requests_path_sweep(
                None, requirement_text
            ):
                self._apply_path_sweep_probe_diagnostics_to_summary(
                    summary=summary,
                    stdout=result.stdout,
                    stderr=result.stderr,
                )
            if "explicit_anchor_hole" in probe_family_ids:
                self._apply_explicit_anchor_hole_probe_diagnostics_to_summary(
                    summary=summary,
                    stdout=result.stdout,
                    stderr=result.stderr,
                )
            return summary
        if "path_sweep" in probe_family_ids or requirement_requests_path_sweep(
            None, requirement_text
        ):
            path_sweep_ok, path_sweep_evidence = (
                self._snapshot_has_execute_build123d_path_sweep_fallback(
                    snapshot=snapshot,
                    hollow_profile_required=self._requirement_requires_hollow_sweep_profile(
                        requirement_text
                    ),
                    bend_required=self._requirement_requires_bent_sweep_result(
                        requirement_text
                    ),
                )
            )
            if path_sweep_ok:
                summary["actionable"] = True
                summary["actionable_family_ids"] = ["path_sweep"]
                summary["actionable_reason"] = (
                    "path_sweep probe produced repair-ready geometry evidence; "
                    f"{path_sweep_evidence}"
                )
            elif path_sweep_evidence:
                summary["actionable_reason"] = (
                    "path_sweep probe remains diagnostic-only because the geometry "
                    f"fallback is incomplete; {path_sweep_evidence}"
                )
            else:
                self._apply_path_sweep_probe_diagnostics_to_summary(
                    summary=summary,
                    stdout=result.stdout,
                    stderr=result.stderr,
                )
        if "explicit_anchor_hole" in probe_family_ids:
            self._apply_explicit_anchor_hole_probe_diagnostics_to_summary(
                summary=summary,
                stdout=result.stdout,
                stderr=result.stderr,
            )
        return summary

    def _apply_path_sweep_probe_diagnostics_to_summary(
        self,
        *,
        summary: dict[str, Any],
        stdout: str | None,
        stderr: str | None,
    ) -> None:
        signal_values = self._extract_path_sweep_probe_signal_values(
            stdout=stdout,
            stderr=stderr,
        )
        if not signal_values:
            return
        anchor_keys = sorted(signal_values.keys())
        summary["signal_values_by_family"] = {"path_sweep": signal_values}
        summary["anchor_signal_keys_by_family"] = {"path_sweep": anchor_keys}
        if not summary.get("recommended_repair_lane"):
            summary["recommended_repair_lane"] = "subtree_rebuild"
        has_valid_profile = bool(signal_values.get("profile_face_valid"))
        has_connected_path = bool(signal_values.get("path_chain_connected")) or bool(
            signal_values.get("workplane_path_wire_valid")
        )
        has_repair_ready_rail = has_connected_path and int(
            signal_values.get("path_segment_count", 0) or 0
        ) >= 2
        if has_valid_profile and has_connected_path:
            summary["actionable"] = True
            summary["actionable_family_ids"] = ["path_sweep"]
            summary["actionable_reason"] = (
                "path_sweep probe produced repair-ready wire/profile diagnostics; "
                f"signals={signal_values}"
            )
        elif has_repair_ready_rail:
            summary["actionable"] = True
            summary["actionable_family_ids"] = ["path_sweep"]
            summary["actionable_reason"] = (
                "path_sweep probe produced repair-ready rail diagnostics; "
                f"signals={signal_values}"
            )

    def _extract_path_sweep_probe_signal_values(
        self,
        *,
        stdout: str | None,
        stderr: str | None,
    ) -> dict[str, Any]:
        combined = "\n".join(
            part for part in (stdout or "", stderr or "") if isinstance(part, str) and part.strip()
        )
        if not combined:
            return {}

        def _extract_bool(label: str) -> bool | None:
            match = re.search(
                rf"{re.escape(label)}\s*:\s*(True|False)",
                combined,
                flags=re.IGNORECASE,
            )
            if match is None:
                return None
            return match.group(1).strip().lower() == "true"

        def _extract_float(label: str) -> float | None:
            match = re.search(
                rf"{re.escape(label)}\s*:\s*([-+]?[0-9]+(?:\.[0-9]+)?)",
                combined,
                flags=re.IGNORECASE,
            )
            if match is None:
                return None
            return float(match.group(1))

        signals: dict[str, Any] = {}
        profile_face_valid = _extract_bool("Profile face valid")
        if profile_face_valid is not None:
            signals["profile_face_valid"] = profile_face_valid
        profile_face_area = _extract_float("Profile face area")
        if profile_face_area is not None:
            signals["profile_face_area"] = profile_face_area
        profile_face_count = _extract_float("Profile face count")
        if profile_face_count is not None:
            signals["profile_face_count"] = int(profile_face_count)
            if int(profile_face_count) >= 1:
                signals["profile_face_valid"] = True
        edge_gap_1 = _extract_float("Edge1 end to Edge2 start distance")
        edge_gap_2 = _extract_float("Edge2 end to Edge3 start distance")
        if edge_gap_1 is not None or edge_gap_2 is not None:
            gap_values = [
                float(value)
                for value in (edge_gap_1, edge_gap_2)
                if isinstance(value, float)
            ]
            signals["path_endpoint_gap_distances"] = gap_values
            if len(gap_values) == 2:
                signals["path_chain_connected"] = all(abs(value) <= 1e-4 for value in gap_values)
        full_wire_valid = _extract_bool("Full wire valid")
        if full_wire_valid is not None:
            signals["full_wire_valid"] = full_wire_valid
        full_wire_closed = _extract_bool("Full wire closed")
        if full_wire_closed is not None:
            signals["full_wire_closed"] = full_wire_closed
        workplane_path_wire_valid = _extract_bool("Path wire via Workplane")
        if workplane_path_wire_valid is not None:
            signals["workplane_path_wire_valid"] = workplane_path_wire_valid
        if "workplane_path_wire_valid" not in signals and re.search(
            r"Path wire:\s*<build123d\.topology\.Wire\b",
            combined,
            flags=re.IGNORECASE,
        ):
            signals["workplane_path_wire_valid"] = True
        segment_count = len(
            re.findall(
                r"\b(?:Line\s*\d+|Arc)\s*:\s*start=.*?end=",
                combined,
                flags=re.IGNORECASE,
            )
        )
        if segment_count == 0:
            path_edges_match = re.search(r"Path edges:\s*\[(.*?)\]", combined, flags=re.DOTALL)
            if path_edges_match is not None:
                segment_count = len(
                    re.findall(
                        r"<build123d\.topology\.Edge\b",
                        str(path_edges_match.group(1)),
                        flags=re.IGNORECASE,
                    )
                )
        if segment_count:
            signals["path_segment_count"] = segment_count
        if (
            "path_chain_connected" not in signals
            and segment_count >= 3
            and re.search(r"Path endpoint:\s*Vector\(", combined, flags=re.IGNORECASE)
        ):
            signals["path_chain_connected"] = True
        if (
            "path_chain_connected" not in signals
            and bool(signals.get("workplane_path_wire_valid"))
            and segment_count >= 2
        ):
            signals["path_chain_connected"] = True
        if (
            "workplane_path_wire_valid" not in signals
            and segment_count >= 3
            and re.search(
                r"Positional args work:\s*<build123d\.objects_curve\.CenterArc\b",
                combined,
                flags=re.IGNORECASE,
            )
        ):
            signals["workplane_path_wire_valid"] = True
        return signals

    def _apply_explicit_anchor_hole_probe_diagnostics_to_summary(
        self,
        *,
        summary: dict[str, Any],
        stdout: str | None,
        stderr: str | None,
    ) -> None:
        signal_values = self._extract_explicit_anchor_hole_probe_signal_values(
            stdout=stdout,
            stderr=stderr,
        )
        if not signal_values:
            return
        signal_map = dict(summary.get("signal_values_by_family") or {})
        signal_map["explicit_anchor_hole"] = signal_values
        summary["signal_values_by_family"] = signal_map
        anchor_keys = sorted(signal_values.keys())
        anchor_map = dict(summary.get("anchor_signal_keys_by_family") or {})
        anchor_map["explicit_anchor_hole"] = anchor_keys
        summary["anchor_signal_keys_by_family"] = anchor_map
        if not summary.get("recommended_repair_lane"):
            summary["recommended_repair_lane"] = "subtree_rebuild"
        if bool(signal_values.get("countersink_helper_signature_valid")):
            summary["actionable"] = True
            summary["actionable_family_ids"] = ["explicit_anchor_hole"]
            summary["actionable_reason"] = (
                "explicit_anchor_hole probe produced repair-ready CounterSinkHole "
                f"contract diagnostics; signals={signal_values}"
            )

    def _extract_explicit_anchor_hole_probe_signal_values(
        self,
        *,
        stdout: str | None,
        stderr: str | None,
    ) -> dict[str, Any]:
        combined = "\n".join(
            part for part in (stdout or "", stderr or "") if isinstance(part, str) and part.strip()
        )
        if not combined:
            return {}
        lowered = combined.lower()
        signature_present = "countersinkhole signature" in lowered
        helper_contract_present = all(
            token in lowered
            for token in ("radius", "counter_sink_radius", "depth", "counter_sink_angle")
        )
        if not signature_present and not helper_contract_present:
            return {}
        return {
            "countersink_helper_signature_present": signature_present,
            "countersink_helper_signature_valid": signature_present and helper_contract_present,
            "counter_sink_radius_keyword_valid": "counter_sink_radius" in lowered,
            "counter_sink_angle_keyword_valid": "counter_sink_angle" in lowered,
            "counter_sink_radius_contract_hint": (
                "CounterSinkHole(radius=..., counter_sink_radius=..., depth=..., "
                "counter_sink_angle=...)"
            ),
        }

    def _build_sketch_state(
        self,
        history: list[ActionHistoryEntry],
        snapshot: CADStateSnapshot,
        step: int,
    ) -> SketchState:
        plane, origin = self._latest_sketch_frame(history)
        relevant_history = self._relevant_pre_solid_history(history)
        paths: list[SketchPathEntity] = []
        for entry in relevant_history:
            if entry.action_type != CADActionType.ADD_PATH:
                continue
            frame_plane, frame_origin = self._latest_sketch_frame(
                relevant_history[: entry.step]
            )
            path_entity = self._build_sketch_path_entity(
                entry=entry,
                step=step,
                ordinal=len(paths) + 1,
                plane=frame_plane,
                origin=frame_origin,
            )
            if path_entity is not None:
                paths.append(path_entity)

        profiles = self._build_profile_entities(
            history=relevant_history,
            step=step,
        )
        path_ref_set = {path.path_ref for path in paths}
        current_path_ref_by_suffix: dict[str, str] = {}
        for path in paths:
            path_ref = str(path.path_ref).strip()
            if ":P_" in path_ref:
                current_path_ref_by_suffix[path_ref.split(":")[-1]] = path_ref
        for profile in profiles:
            attached_path_ref = (
                str(profile.attached_path_ref).strip()
                if isinstance(profile.attached_path_ref, str)
                and str(profile.attached_path_ref).strip()
                else None
            )
            if not attached_path_ref or attached_path_ref in path_ref_set:
                continue
            suffix = attached_path_ref.split(":")[-1] if ":P_" in attached_path_ref else ""
            rebound_path_ref = current_path_ref_by_suffix.get(suffix)
            if rebound_path_ref:
                profile.attached_path_ref = rebound_path_ref

        path_issues_by_ref: dict[str, list[str]] = {}
        for path in paths:
            scoped_issues: list[str] = []
            if not path.connected:
                scoped_issues.append("path_disconnected")
            path_issues_by_ref[path.path_ref] = self._normalize_string_list(
                scoped_issues,
                limit=16,
            )

        profile_issues_by_ref: dict[str, list[str]] = {}
        sweep_ready_profile_refs: list[str] = []
        loft_ready_profile_refs: list[str] = []
        for profile in profiles:
            scoped_issues: list[str] = []
            if not profile.closed:
                scoped_issues.append("profile_not_closed")
            if profile.attached_path_ref and profile.attached_path_ref not in path_ref_set:
                scoped_issues.append("feature_path_sweep_frame")
            normalized_profile_issues = self._normalize_string_list(
                scoped_issues,
                limit=16,
            )
            profile_issues_by_ref[profile.profile_ref] = normalized_profile_issues
            if profile.closed and int(profile.outer_loop_count or 0) >= 1:
                loft_ready_profile_refs.append(profile.profile_ref)
                if (
                    isinstance(profile.attached_path_ref, str)
                    and profile.attached_path_ref in path_ref_set
                    and not path_issues_by_ref.get(profile.attached_path_ref)
                ):
                    sweep_ready_profile_refs.append(profile.profile_ref)

        issues = self._normalize_string_list(
            [
                *snapshot.issues,
                *snapshot.warnings,
                *snapshot.blockers,
            ],
            limit=64,
        )
        if any(not path.connected for path in paths):
            issues.append("path_disconnected")
        if any(not profile.closed for profile in profiles):
            issues.append("profile_not_closed")

        issues = self._normalize_string_list(issues, limit=64)
        return SketchState(
            plane=plane,
            origin=origin,
            path_refs=[item.path_ref for item in paths],
            profile_refs=[item.profile_ref for item in profiles],
            profile_stack_order=[
                item.profile_ref
                for item in profiles
                if bool(getattr(item, "loftable", False))
            ],
            sweep_ready_profile_refs=sweep_ready_profile_refs,
            loft_ready_profile_refs=loft_ready_profile_refs,
            paths=paths,
            profiles=profiles,
            issues_by_path_ref=path_issues_by_ref,
            issues_by_profile_ref=profile_issues_by_ref,
            issues=issues,
        )

    def _relevant_pre_solid_history(
        self,
        history: list[ActionHistoryEntry],
    ) -> list[ActionHistoryEntry]:
        latest_create_sketch_index: int | None = None
        latest_create_sketch_entry: ActionHistoryEntry | None = None
        for index, entry in enumerate(history):
            if entry.action_type == CADActionType.CREATE_SKETCH:
                latest_create_sketch_index = index
                latest_create_sketch_entry = entry
        if latest_create_sketch_index is not None:
            has_positive_solid_before_latest_sketch = False
            for entry in history[:latest_create_sketch_index]:
                try:
                    if entry.result_snapshot.geometry.solids > 0:
                        has_positive_solid_before_latest_sketch = True
                        break
                except Exception:
                    continue
            start_index = latest_create_sketch_index
            sketch_params = (
                latest_create_sketch_entry.action_params
                if latest_create_sketch_entry is not None
                else {}
            )
            parsed_path_ref = self._parse_sketch_ref(sketch_params.get("path_ref"))
            if (
                parsed_path_ref is not None
                and parsed_path_ref.get("kind") == "path"
                and isinstance(parsed_path_ref.get("step"), int)
            ):
                path_step = int(parsed_path_ref["step"])
                path_index = max(min(path_step - 1, len(history) - 1), 0)
                for index in range(path_index, -1, -1):
                    if history[index].action_type == CADActionType.CREATE_SKETCH:
                        start_index = min(start_index, index)
                        break
            # query_sketch should describe the current sketch window first.
            # When a new sketch is opened on top of an existing solid, the action
            # history after that create_sketch may still report solids>0, so a
            # "latest solid" cut would incorrectly leak older profile windows.
            # If the current sketch is attached to an earlier path, keep the
            # minimal path ancestry needed to preserve that attachment context.
            trailing_from_latest_sketch = history[start_index:]
            if has_positive_solid_before_latest_sketch and trailing_from_latest_sketch:
                return trailing_from_latest_sketch
        latest_solid_index = -1
        for index, entry in enumerate(history):
            try:
                if entry.result_snapshot.geometry.solids > 0:
                    latest_solid_index = index
            except Exception:
                continue
        if latest_solid_index < 0:
            return history
        trailing = history[latest_solid_index + 1 :]
        return trailing or history

    def _latest_sketch_frame(
        self,
        history: list[ActionHistoryEntry],
    ) -> tuple[str, list[float]]:
        for entry in reversed(history):
            if entry.action_type != CADActionType.CREATE_SKETCH:
                continue
            params = normalize_action_params(entry.action_type, entry.action_params)
            plane_raw = params.get("plane", "XY")
            plane_token = (
                str(plane_raw).strip().upper()
                if isinstance(plane_raw, str)
                else "XY"
            )
            plane_alias_map = {
                "XY": "XY",
                "TOP": "XY",
                "BOTTOM": "XY",
                "XZ": "XZ",
                "FRONT": "XZ",
                "BACK": "XZ",
                "YZ": "YZ",
                "RIGHT": "YZ",
                "LEFT": "YZ",
            }
            plane = plane_alias_map.get(plane_token, "XY")
            origin = self._resolve_create_sketch_origin_3d(
                params=params,
                plane=plane,
            )
            return plane, origin
        return "XY", [0.0, 0.0, 0.0]

    def _build_sketch_path_entity(
        self,
        entry: ActionHistoryEntry,
        step: int,
        ordinal: int,
        plane: str,
        origin: list[float],
    ) -> SketchPathEntity | None:
        params = normalize_action_params(entry.action_type, entry.action_params)
        segments_raw = params.get("segments")
        if not isinstance(segments_raw, list):
            return None
        start_raw = params.get("start", [0.0, 0.0])
        start = self._project_path_point_2d(start_raw, plane=plane) or [0.0, 0.0]
        (
            segment_entities,
            end_point,
            connected,
            start_tangent,
            terminal_tangent,
        ) = self._resolve_path_segments(segments_raw, start, plane=plane)
        if not segment_entities:
            return None
        points = [start, *[segment.end_point for segment in segment_entities]]
        bbox = self._bbox_from_local_points(points)
        total_length = sum(segment.length for segment in segment_entities)
        explicit_closed = self._path_closed_flag(params)
        inferred_closed = math.dist(start, end_point) <= 1e-4
        return SketchPathEntity(
            path_ref=self._format_sketch_ref("path", step, f"P_{ordinal}"),
            step=step,
            plane=plane,
            origin=origin,
            segment_types=[segment.segment_type for segment in segment_entities],
            segments=segment_entities,
            start_point=start,
            end_point=end_point,
            connected=connected,
            closed=bool(explicit_closed or inferred_closed),
            start_tangent=start_tangent,
            terminal_tangent=terminal_tangent,
            total_length=total_length,
            bbox=bbox,
        )

    def _resolve_path_segments(
        self,
        segments_raw: list[Any],
        start: list[float],
        *,
        plane: str = "XY",
    ) -> tuple[
        list[SketchSegmentEntity],
        list[float],
        bool,
        list[float] | None,
        list[float] | None,
    ]:
        current = [float(start[0]), float(start[1])]
        previous_tangent: list[float] | None = None
        start_tangent: list[float] | None = None
        connected = True
        entities: list[SketchSegmentEntity] = []
        for index, raw_segment in enumerate(segments_raw, start=1):
            if not isinstance(raw_segment, dict):
                continue
            segment_type = str(raw_segment.get("type", "line")).strip().lower()
            if segment_type in {"tangent_line", "add_line"}:
                segment_type = "line"
            start_point = [current[0], current[1]]
            end_point = [current[0], current[1]]
            radius = None
            angle_degrees = None
            segment_start_tangent = previous_tangent
            segment_end_tangent = previous_tangent
            if segment_type == "line":
                end_point, segment_end_tangent = self._resolve_line_segment(
                    raw_segment,
                    start_point,
                    previous_tangent,
                    plane=plane,
                )
                segment_start_tangent = segment_end_tangent
            elif segment_type in {"tangent_arc", "arc"}:
                (
                    end_point,
                    segment_start_tangent,
                    segment_end_tangent,
                    radius,
                    angle_degrees,
                ) = self._resolve_arc_segment(
                    raw_segment,
                    start_point,
                    previous_tangent,
                    plane=plane,
                )
            else:
                continue
            length = math.dist(start_point, end_point)
            if radius is not None and angle_degrees is not None:
                length = abs(math.radians(angle_degrees) * radius)
            if start_tangent is None and segment_start_tangent is not None:
                start_tangent = segment_start_tangent
            entities.append(
                SketchSegmentEntity(
                    segment_index=index,
                    segment_type=segment_type,
                    start_point=start_point,
                    end_point=end_point,
                    connected_to_previous=True,
                    length=length,
                    radius=radius,
                    angle_degrees=angle_degrees,
                    start_tangent=segment_start_tangent,
                    end_tangent=segment_end_tangent,
                )
            )
            current = [end_point[0], end_point[1]]
            previous_tangent = segment_end_tangent
        return entities, current, connected, start_tangent, previous_tangent

    def _resolve_line_segment(
        self,
        raw_segment: dict[str, Any],
        start_point: list[float],
        previous_tangent: list[float] | None,
        *,
        plane: str = "XY",
    ) -> tuple[list[float], list[float] | None]:
        to_raw = raw_segment.get("to", raw_segment.get("end"))
        end = self._project_path_point_2d(to_raw, plane=plane)
        if end is not None:
            tangent = self._normalize_vec2([end[0] - start_point[0], end[1] - start_point[1]])
            return end, tangent
        length = self._as_float(raw_segment.get("length"), fallback=0.0)
        direction = self._normalize_path_direction_2d(
            raw_segment.get("direction"),
            plane=plane,
        )
        if direction is None:
            direction = previous_tangent or [1.0, 0.0]
        tangent = self._normalize_vec2(direction)
        end = [
            start_point[0] + tangent[0] * length,
            start_point[1] + tangent[1] * length,
        ]
        return end, tangent

    def _resolve_arc_segment(
        self,
        raw_segment: dict[str, Any],
        start_point: list[float],
        previous_tangent: list[float] | None,
        *,
        plane: str = "XY",
    ) -> tuple[
        list[float],
        list[float] | None,
        list[float] | None,
        float | None,
        float | None,
    ]:
        radius = self._as_float(raw_segment.get("radius"), fallback=0.0)
        angle_degrees = self._as_float(
            raw_segment.get(
                "angle_degrees",
                raw_segment.get("arc_degrees", raw_segment.get("angle")),
            ),
            fallback=0.0,
        )
        to_raw = raw_segment.get("to", raw_segment.get("end"))
        end = self._project_path_point_2d(to_raw, plane=plane)
        if end is not None:
            tangent = self._normalize_vec2([end[0] - start_point[0], end[1] - start_point[1]])
            return end, tangent, tangent, radius or None, angle_degrees or None
        tangent = previous_tangent or self._normalize_path_direction_2d(
            raw_segment.get("direction"),
            plane=plane,
        ) or [1.0, 0.0]
        if radius <= 1e-6 or abs(angle_degrees) <= 1e-6:
            return start_point, tangent, tangent, None, None
        signed_angle = math.radians(angle_degrees)
        turn = str(raw_segment.get("turn", raw_segment.get("turn_direction", "left"))).strip().lower()
        if turn == "right":
            signed_angle = -abs(signed_angle)
        else:
            signed_angle = abs(signed_angle)
        tangent = self._normalize_vec2(tangent)
        normal = [-tangent[1], tangent[0]]
        if signed_angle < 0:
            normal = [-normal[0], -normal[1]]
        center = [
            start_point[0] + normal[0] * radius,
            start_point[1] + normal[1] * radius,
        ]
        rel_start = [start_point[0] - center[0], start_point[1] - center[1]]
        rel_end = [
            rel_start[0] * math.cos(signed_angle) - rel_start[1] * math.sin(signed_angle),
            rel_start[0] * math.sin(signed_angle) + rel_start[1] * math.cos(signed_angle),
        ]
        end = [center[0] + rel_end[0], center[1] + rel_end[1]]
        end_tangent = [
            tangent[0] * math.cos(signed_angle) - tangent[1] * math.sin(signed_angle),
            tangent[0] * math.sin(signed_angle) + tangent[1] * math.cos(signed_angle),
        ]
        return (
            end,
            self._normalize_vec2(tangent),
            self._normalize_vec2(end_tangent),
            radius,
            abs(math.degrees(signed_angle)),
        )

    def _project_path_point_2d(
        self,
        value: object,
        *,
        plane: str = "XY",
    ) -> list[float] | None:
        if not isinstance(value, (list, tuple)) or len(value) < 2:
            return None
        normalized_plane = self._normalize_sketch_plane_name(plane)
        if len(value) >= 3 and all(isinstance(item, (int, float)) for item in value[:3]):
            x_value = float(value[0])
            y_value = float(value[1])
            z_value = float(value[2])
            if normalized_plane == "XZ":
                return [x_value, z_value]
            if normalized_plane == "YZ":
                return [y_value, z_value]
            return [x_value, y_value]
        if not isinstance(value[0], (int, float)) or not isinstance(value[1], (int, float)):
            return None
        return [float(value[0]), float(value[1])]

    def _normalize_path_direction_2d(
        self,
        value: object,
        *,
        plane: str = "XY",
    ) -> list[float] | None:
        if isinstance(value, (list, tuple)):
            projected = self._project_path_point_2d(value, plane=plane)
            if projected is None:
                return None
            return self._normalize_vec2(projected)
        if not isinstance(value, str) or not value.strip():
            return None
        token = value.strip().lower().replace(" ", "_")
        local_aliases = {
            "x": [1.0, 0.0],
            "+x": [1.0, 0.0],
            "x+": [1.0, 0.0],
            "right": [1.0, 0.0],
            "horizontal": [1.0, 0.0],
            "-x": [-1.0, 0.0],
            "x-": [-1.0, 0.0],
            "left": [-1.0, 0.0],
            "y": [0.0, 1.0],
            "+y": [0.0, 1.0],
            "y+": [0.0, 1.0],
            "up": [0.0, 1.0],
            "vertical": [0.0, 1.0],
            "-y": [0.0, -1.0],
            "y-": [0.0, -1.0],
            "down": [0.0, -1.0],
        }
        if token in local_aliases:
            return list(local_aliases[token])
        axis_aliases = {
            "x": "x",
            "+x": "x",
            "x+": "x",
            "-x": "-x",
            "x-": "-x",
            "y": "y",
            "+y": "y",
            "y+": "y",
            "-y": "-y",
            "y-": "-y",
            "z": "z",
            "+z": "z",
            "z+": "z",
            "-z": "-z",
            "z-": "-z",
        }
        axis_token = axis_aliases.get(token)
        if axis_token is None:
            return None
        normalized_plane = self._normalize_sketch_plane_name(plane)
        if normalized_plane == "XZ":
            mapping = {
                "x": [1.0, 0.0],
                "-x": [-1.0, 0.0],
                "z": [0.0, 1.0],
                "-z": [0.0, -1.0],
            }
            mapped = mapping.get(axis_token)
            return list(mapped) if mapped is not None else None
        if normalized_plane == "YZ":
            mapping = {
                "y": [1.0, 0.0],
                "-y": [-1.0, 0.0],
                "z": [0.0, 1.0],
                "-z": [0.0, -1.0],
            }
            mapped = mapping.get(axis_token)
            return list(mapped) if mapped is not None else None
        mapping = {
            "x": [1.0, 0.0],
            "-x": [-1.0, 0.0],
            "y": [0.0, 1.0],
            "-y": [0.0, -1.0],
        }
        mapped = mapping.get(axis_token)
        return list(mapped) if mapped is not None else None

    def _build_profile_entities(
        self,
        history: list[ActionHistoryEntry],
        step: int,
    ) -> list[SketchProfileEntity]:
        create_indices = [
            index
            for index, entry in enumerate(history)
            if entry.action_type == CADActionType.CREATE_SKETCH
        ]
        if not create_indices:
            return []
        profiles: list[SketchProfileEntity] = []
        for ordinal, create_index in enumerate(create_indices, start=1):
            next_create_index = next(
                (index for index in create_indices if index > create_index),
                len(history),
            )
            sketch_entry = history[create_index]
            sketch_window = history[create_index + 1 : next_create_index]
            profile_entry = self._build_profile_entity_for_window(
                sketch_entry=sketch_entry,
                sketch_history=history[: create_index + 1],
                sketch_window=sketch_window,
                step=step,
                ordinal=ordinal,
            )
            if profile_entry is not None:
                profiles.append(profile_entry)
        return profiles

    def _build_profile_entity_for_window(
        self,
        sketch_entry: ActionHistoryEntry,
        sketch_history: list[ActionHistoryEntry],
        sketch_window: list[ActionHistoryEntry],
        step: int,
        ordinal: int,
    ) -> SketchProfileEntity | None:
        sketch_params = normalize_action_params(
            CADActionType.CREATE_SKETCH,
            sketch_entry.action_params,
        )
        attached_path_ref = (
            str(sketch_params.get("path_ref")).strip()
            if isinstance(sketch_params.get("path_ref"), str)
            and str(sketch_params.get("path_ref")).strip()
            else None
        )
        profile_actions = [
            entry
            for entry in sketch_window
            if entry.action_type
            in {
                CADActionType.ADD_CIRCLE,
                CADActionType.ADD_RECTANGLE,
                CADActionType.ADD_POLYGON,
                CADActionType.ADD_PATH,
            }
        ]
        if not profile_actions:
            return None
        plane, origin = self._latest_sketch_frame(sketch_history)
        outer_loop_count = 0
        inner_loop_count = 0
        has_sloped_segment = False
        primitive_types: list[str] = []
        point_count: int | None = None
        regular_sides: int | None = None
        regular_polygon_size_mode: str | None = None
        regular_polygon_circumradius: float | None = None
        regular_polygon_apothem: float | None = None
        rotation_degrees: float | None = None
        centers: list[list[float]] = []
        bbox_points: list[list[float]] = []
        nested_relationship: str | None = None
        circle_radii_by_center: dict[tuple[float, float], list[float]] = {}
        loops: list[SketchLoopEntity] = []
        loop_radii: list[float] = []
        estimated_area = 0.0
        for entry in profile_actions:
            params = normalize_action_params(entry.action_type, entry.action_params)
            if entry.action_type == CADActionType.ADD_CIRCLE:
                primitive_types.append("circle")
                radius = self._to_positive_float(params.get("radius"), default=0.0)
                if radius <= 0.0:
                    radius = (
                        self._to_positive_float(params.get("diameter"), default=0.0)
                        / 2.0
                    )
                radius_inner = self._to_positive_float(
                    params.get("radius_inner"),
                    default=0.0,
                )
                raw_centers = params.get("centers", params.get("positions"))
                normalized_centers = self._normalize_local_centers(raw_centers)
                if not normalized_centers:
                    normalized_centers = [
                        self._normalize_local_center(
                            params.get("position", params.get("center"))
                        )
                    ]
                for center in normalized_centers:
                    centers.append(center)
                    bbox_points.extend(
                        [
                            [center[0] - radius, center[1] - radius],
                            [center[0] + radius, center[1] + radius],
                        ]
                    )
                    center_key = (round(center[0], 6), round(center[1], 6))
                    radii = circle_radii_by_center.setdefault(center_key, [])
                    if radius > 0.0:
                        radii.append(radius)
                    if radius_inner > 0.0:
                        radii.append(radius_inner)
                continue
            if entry.action_type == CADActionType.ADD_RECTANGLE:
                primitive_types.append("rectangle")
            profile_bbox_points, profile_centers, outer_loops, inner_loops = self._profile_metrics_from_action(
                entry.action_type,
                params,
            )
            if entry.action_type == CADActionType.ADD_POLYGON:
                primitive_types.append("polygon")
                raw_points = params.get("points", params.get("vertices"))
                normalized_points = [
                    raw_point
                    for raw_point in raw_points
                    if isinstance(raw_points, list)
                    and isinstance(raw_point, (list, tuple))
                    and len(raw_point) >= 2
                    and isinstance(raw_point[0], (int, float))
                    and isinstance(raw_point[1], (int, float))
                ] if isinstance(raw_points, list) else []
                if normalized_points:
                    point_count = max(point_count or 0, len(normalized_points))
                    if len(normalized_points) == 3:
                        primitive_types.append("triangle")
                    elif len(normalized_points) == 6:
                        primitive_types.append("hexagon")
                side_count_raw = params.get(
                    "sides",
                    params.get(
                        "n_sides",
                        params.get(
                            "num_sides",
                            params.get("side_count", params.get("regular_sides")),
                        ),
                    ),
                )
                if isinstance(side_count_raw, (int, float)) and int(side_count_raw) >= 3:
                    regular_sides = max(regular_sides or 0, int(side_count_raw))
                    (
                        derived_size_mode,
                        derived_circumradius,
                        derived_apothem,
                    ) = self._resolve_regular_polygon_size_evidence(
                        int(side_count_raw),
                        params,
                    )
                    if derived_size_mode is not None:
                        regular_polygon_size_mode = derived_size_mode
                    if derived_circumradius is not None:
                        regular_polygon_circumradius = derived_circumradius
                    if derived_apothem is not None:
                        regular_polygon_apothem = derived_apothem
                    rotation_value = self._as_float(
                        params.get(
                            "rotation_degrees",
                            params.get("rotation", params.get("phase_degrees")),
                        ),
                        fallback=0.0,
                    )
                    if abs(rotation_value) > 1e-6:
                        rotation_degrees = float(rotation_value)
                    if int(side_count_raw) == 3:
                        primitive_types.append("triangle")
                    elif int(side_count_raw) == 6:
                        primitive_types.append("hexagon")
            action_area = self._estimate_profile_action_area(
                entry.action_type,
                params,
            )
            if isinstance(action_area, (int, float)) and float(action_area) > 1e-9:
                estimated_area += float(action_area)
            bbox_points.extend(profile_bbox_points)
            centers.extend(profile_centers)
            outer_loop_count += outer_loops
            inner_loop_count += inner_loops
            if self._profile_action_has_sloped_segment(entry.action_type, params):
                has_sloped_segment = True
        for center_key, radii in circle_radii_by_center.items():
            distinct_radii = sorted({round(radius, 6) for radius in radii if radius > 0.0})
            if not distinct_radii:
                continue
            loop_radii.extend(float(radius) for radius in distinct_radii)
            outer_loop_count += 1
            estimated_area += math.pi * float(distinct_radii[-1]) ** 2
            ordered_radii = sorted((float(radius) for radius in distinct_radii), reverse=True)
            for radius_index, radius_value in enumerate(ordered_radii, start=1):
                loops.append(
                    SketchLoopEntity(
                        loop_id=(
                            f"profile:{step}:PR_{ordinal}:loop:{len(loops) + 1}"
                        ),
                        loop_type="circle",
                        role="outer" if radius_index == 1 else "inner",
                        center=[float(center_key[0]), float(center_key[1])],
                        radius=radius_value,
                    )
                )
            if len(distinct_radii) > 1:
                inner_loop_count += len(distinct_radii) - 1
                estimated_area -= math.pi * sum(
                    float(radius) ** 2 for radius in distinct_radii[:-1]
                )
        if inner_loop_count > 0:
            nested_relationship = "concentric_frame"
        if (
            outer_loop_count <= 0
            and inner_loop_count <= 0
            and attached_path_ref is None
            and profile_actions
            and all(entry.action_type == CADActionType.ADD_PATH for entry in profile_actions)
        ):
            # An open path-only sketch window is a rail candidate, not a profile candidate.
            return None
        bbox = self._bbox_from_local_points(bbox_points or [[0.0, 0.0], [0.0, 0.0]])
        frame_mode = (
            str(sketch_params.get("frame_mode")).strip().lower()
            if isinstance(sketch_params.get("frame_mode"), str)
            and str(sketch_params.get("frame_mode")).strip()
            else None
        )
        return SketchProfileEntity(
            profile_ref=self._format_sketch_ref("profile", step, f"PR_{ordinal}"),
            step=step,
            window_index=ordinal,
            source_sketch_step=sketch_entry.step,
            plane=plane,
            origin=origin,
            outer_loop_count=outer_loop_count,
            inner_loop_count=inner_loop_count,
            closed=(outer_loop_count + inner_loop_count) > 0,
            nested_relationship=nested_relationship,
            has_sloped_segment=has_sloped_segment,
            primitive_types=self._normalize_string_list(primitive_types, limit=8),
            point_count=point_count,
            regular_sides=regular_sides,
            regular_polygon_size_mode=regular_polygon_size_mode,
            regular_polygon_circumradius=regular_polygon_circumradius,
            regular_polygon_apothem=regular_polygon_apothem,
            rotation_degrees=rotation_degrees,
            centers=centers or [[0.0, 0.0]],
            loops=loops,
            attached_path_ref=attached_path_ref,
            frame_mode=frame_mode,
            loop_radii=sorted(loop_radii),
            estimated_area=(float(estimated_area) if estimated_area > 1e-9 else None),
            loftable=(outer_loop_count >= 1),
            bbox=bbox,
        )

    def _profile_action_has_sloped_segment(
        self,
        action_type: CADActionType,
        params: dict[str, Any],
    ) -> bool:
        if action_type == CADActionType.ADD_PATH:
            start_raw = params.get("start", [0.0, 0.0])
            start = [0.0, 0.0]
            if (
                isinstance(start_raw, (list, tuple))
                and len(start_raw) >= 2
                and isinstance(start_raw[0], (int, float))
                and isinstance(start_raw[1], (int, float))
            ):
                start = [float(start_raw[0]), float(start_raw[1])]
            segments_raw = params.get("segments")
            if not isinstance(segments_raw, list):
                return False
            segment_entities, _end_point, _connected, _start_tangent, _terminal_tangent = (
                self._resolve_path_segments(segments_raw, start)
            )
            for segment in segment_entities:
                if str(segment.segment_type).strip().lower() != "line":
                    return True
                dx = abs(float(segment.end_point[0]) - float(segment.start_point[0]))
                dy = abs(float(segment.end_point[1]) - float(segment.start_point[1]))
                if dx > 1e-6 and dy > 1e-6:
                    return True
            return False
        if action_type != CADActionType.ADD_POLYGON:
            return False
        points = params.get("points", params.get("vertices"))
        if not isinstance(points, list) or len(points) < 3:
            return False
        normalized_points: list[tuple[float, float]] = []
        for raw_point in points:
            if (
                isinstance(raw_point, (list, tuple))
                and len(raw_point) >= 2
                and isinstance(raw_point[0], (int, float))
                and isinstance(raw_point[1], (int, float))
            ):
                normalized_points.append((float(raw_point[0]), float(raw_point[1])))
        if len(normalized_points) < 3:
            return False
        closed_points = normalized_points + [normalized_points[0]]
        for start, end in zip(closed_points, closed_points[1:]):
            dx = abs(float(end[0]) - float(start[0]))
            dy = abs(float(end[1]) - float(start[1]))
            if dx > 1e-6 and dy > 1e-6:
                return True
        return False

    def _profile_metrics_from_action(
        self,
        action_type: CADActionType,
        params: dict[str, Any],
    ) -> tuple[list[list[float]], list[list[float]], int, int]:
        bbox_points: list[list[float]] = []
        centers: list[list[float]] = []
        outer_loops = 0
        inner_loops = 0
        if action_type == CADActionType.ADD_CIRCLE:
            radius = self._to_positive_float(params.get("radius"), default=0.0)
            if radius <= 0.0:
                radius = self._to_positive_float(params.get("diameter"), default=0.0) / 2.0
            radius_inner = self._to_positive_float(params.get("radius_inner"), default=0.0)
            raw_centers = params.get("centers", params.get("positions"))
            normalized_centers = self._normalize_local_centers(raw_centers)
            if not normalized_centers:
                normalized_centers = [
                    self._normalize_local_center(params.get("position", params.get("center")))
                ]
            for center in normalized_centers:
                centers.append(center)
                bbox_points.extend(
                    [
                        [center[0] - radius, center[1] - radius],
                        [center[0] + radius, center[1] + radius],
                    ]
                )
            outer_loops += len(normalized_centers)
            if radius_inner > 0.0:
                inner_loops += len(normalized_centers)
            return bbox_points, centers, outer_loops, inner_loops
        if action_type == CADActionType.ADD_RECTANGLE:
            width = self._as_float(params.get("width"), fallback=0.0)
            height = self._as_float(params.get("height"), fallback=0.0)
            inner_width = self._to_positive_float(params.get("inner_width"), default=0.0)
            inner_height = self._to_positive_float(params.get("inner_height"), default=0.0)
            center = self._resolve_rectangle_center_from_params(params)
            centers.append(center)
            bbox_points.extend(
                [
                    [center[0] - width / 2.0, center[1] - height / 2.0],
                    [center[0] + width / 2.0, center[1] + height / 2.0],
                ]
            )
            outer_loops += 1
            if inner_width > 0.0 and inner_height > 0.0:
                inner_loops += 1
            return bbox_points, centers, outer_loops, inner_loops
        if action_type == CADActionType.ADD_POLYGON:
            points = params.get("points", params.get("vertices"))
            if isinstance(points, list):
                normalized_points = []
                for raw_point in points:
                    if (
                        isinstance(raw_point, (list, tuple))
                        and len(raw_point) >= 2
                        and isinstance(raw_point[0], (int, float))
                        and isinstance(raw_point[1], (int, float))
                    ):
                        normalized_points.append([float(raw_point[0]), float(raw_point[1])])
                if normalized_points:
                    bbox_points.extend(normalized_points)
                    outer_loops += 1
                    centers.append(self._bbox_center_2d(normalized_points))
                    if self._to_positive_float(params.get("radius_inner"), default=0.0) > 0.0:
                        inner_loops += 1
                    return bbox_points, centers, outer_loops, inner_loops
            side_count_raw = params.get(
                "sides",
                params.get(
                    "n_sides",
                    params.get(
                        "num_sides",
                        params.get("side_count", params.get("regular_sides")),
                    ),
                ),
            )
            side_count = (
                int(side_count_raw)
                if isinstance(side_count_raw, (int, float))
                else 0
            )
            radius_outer = self._to_positive_float(
                params.get("radius_outer", params.get("radius")),
                default=0.0,
            )
            side_length = self._to_positive_float(
                params.get("side_length"),
                default=0.0,
            )
            if radius_outer <= 0.0 and side_count >= 3 and side_length > 0.0:
                radius_outer = side_length / (
                    2.0 * math.sin(math.pi / float(side_count))
                )
            size_mode = self._normalize_regular_polygon_size_mode(
                params.get("size_mode", params.get("radius_mode"))
            )
            if side_count >= 3 and radius_outer > 0.0:
                if size_mode == "apothem":
                    radius_outer = radius_outer / max(
                        math.cos(math.pi / float(side_count)),
                        1e-6,
                    )
                center = self._normalize_local_center(
                    params.get("position", params.get("center"))
                )
                centers.append(center)
                bbox_points.extend(
                    [
                        [center[0] - radius_outer, center[1] - radius_outer],
                        [center[0] + radius_outer, center[1] + radius_outer],
                    ]
                )
                outer_loops += 1
                if self._to_positive_float(
                    params.get("radius_inner"),
                    default=0.0,
                ) > 0.0:
                    inner_loops += 1
                return bbox_points, centers, outer_loops, inner_loops
        if action_type == CADActionType.ADD_PATH:
            start_raw = params.get("start", [0.0, 0.0])
            start = [0.0, 0.0]
            if (
                isinstance(start_raw, (list, tuple))
                and len(start_raw) >= 2
                and isinstance(start_raw[0], (int, float))
                and isinstance(start_raw[1], (int, float))
            ):
                start = [float(start_raw[0]), float(start_raw[1])]
            segments_raw = params.get("segments")
            if not isinstance(segments_raw, list):
                return bbox_points, centers, outer_loops, inner_loops
            segment_entities, end_point, _connected, _start_tangent, _terminal_tangent = (
                self._resolve_path_segments(segments_raw, start)
            )
            if not segment_entities:
                return bbox_points, centers, outer_loops, inner_loops
            explicit_closed = self._path_closed_flag(params)
            inferred_closed = math.dist(start, end_point) <= 1e-4
            if not explicit_closed and not inferred_closed:
                return bbox_points, centers, outer_loops, inner_loops
            points = [start, *[segment.end_point for segment in segment_entities]]
            bbox_points.extend(points)
            centers.append(self._bbox_center_2d(points))
            outer_loops += 1
            return bbox_points, centers, outer_loops, inner_loops
        return bbox_points, centers, outer_loops, inner_loops

    def _normalize_regular_polygon_size_mode(self, value: Any) -> str | None:
        if not isinstance(value, str):
            return None
        normalized = (
            value.strip()
            .lower()
            .replace("-", "_")
            .replace(" ", "_")
        )
        if normalized in {
            "circumradius",
            "vertex_radius",
            "circumcircle",
            "inscribed_circle",
            "inscribed_in_circle",
            "radius",
        }:
            return "circumradius"
        if normalized in {
            "apothem",
            "distance_to_side",
            "distance_to_sides",
            "distance_to_flat",
            "distance_to_flats",
            "center_to_side",
            "center_to_sides",
            "center_to_flat",
            "center_to_flats",
        }:
            return "apothem"
        return None

    def _resolve_regular_polygon_size_evidence(
        self,
        side_count: int,
        params: dict[str, Any],
    ) -> tuple[str | None, float | None, float | None]:
        if side_count < 3:
            return None, None, None
        radius_value = self._to_positive_float(
            params.get("radius_outer", params.get("radius")),
            default=0.0,
        )
        side_length = self._to_positive_float(
            params.get("side_length"),
            default=0.0,
        )
        size_mode = self._normalize_regular_polygon_size_mode(
            params.get("size_mode", params.get("radius_mode"))
        )
        if radius_value <= 0.0 and side_length > 0.0:
            radius_value = side_length / (
                2.0 * math.sin(math.pi / float(side_count))
            )
            size_mode = size_mode or "circumradius"
        if radius_value <= 0.0:
            return size_mode, None, None
        if size_mode == "apothem":
            apothem = radius_value
            circumradius = apothem / max(
                math.cos(math.pi / float(side_count)),
                1e-6,
            )
            return size_mode, circumradius, apothem
        circumradius = radius_value
        apothem = circumradius * math.cos(math.pi / float(side_count))
        return size_mode or "circumradius", circumradius, apothem

    def _polygon_signed_area(self, points: list[list[float]]) -> float:
        if len(points) < 3:
            return 0.0
        area = 0.0
        closed_points = list(points) + [points[0]]
        for start, end in zip(closed_points, closed_points[1:]):
            if len(start) < 2 or len(end) < 2:
                continue
            area += float(start[0]) * float(end[1]) - float(end[0]) * float(start[1])
        return area / 2.0

    def _regular_polygon_area(
        self,
        side_count: int,
        circumradius: float,
    ) -> float:
        if side_count < 3 or circumradius <= 1e-9:
            return 0.0
        return 0.5 * float(side_count) * float(circumradius) ** 2 * math.sin(
            (2.0 * math.pi) / float(side_count)
        )

    def _estimate_profile_action_area(
        self,
        action_type: CADActionType,
        params: dict[str, Any],
    ) -> float | None:
        if action_type == CADActionType.ADD_RECTANGLE:
            width = max(0.0, self._as_float(params.get("width"), fallback=0.0))
            height = max(0.0, self._as_float(params.get("height"), fallback=0.0))
            inner_width = self._to_positive_float(params.get("inner_width"), default=0.0)
            inner_height = self._to_positive_float(params.get("inner_height"), default=0.0)
            return max(0.0, width * height - inner_width * inner_height)
        if action_type == CADActionType.ADD_POLYGON:
            points = params.get("points", params.get("vertices"))
            if isinstance(points, list):
                normalized_points: list[list[float]] = []
                for raw_point in points:
                    if (
                        isinstance(raw_point, (list, tuple))
                        and len(raw_point) >= 2
                        and isinstance(raw_point[0], (int, float))
                        and isinstance(raw_point[1], (int, float))
                    ):
                        normalized_points.append([float(raw_point[0]), float(raw_point[1])])
                if len(normalized_points) >= 3:
                    area = abs(self._polygon_signed_area(normalized_points))
                    inner_radius = self._to_positive_float(
                        params.get("radius_inner"),
                        default=0.0,
                    )
                    if inner_radius > 0.0:
                        side_count_raw = params.get(
                            "sides",
                            params.get(
                                "n_sides",
                                params.get(
                                    "num_sides",
                                    params.get("side_count", params.get("regular_sides")),
                                ),
                            ),
                        )
                        side_count = (
                            int(side_count_raw)
                            if isinstance(side_count_raw, (int, float))
                            else len(normalized_points)
                        )
                        area -= self._regular_polygon_area(side_count, inner_radius)
                    return max(0.0, area)
            side_count_raw = params.get(
                "sides",
                params.get(
                    "n_sides",
                    params.get(
                        "num_sides",
                        params.get("side_count", params.get("regular_sides")),
                    ),
                ),
            )
            side_count = int(side_count_raw) if isinstance(side_count_raw, (int, float)) else 0
            size_mode, circumradius, _apothem = self._resolve_regular_polygon_size_evidence(
                side_count,
                params,
            )
            _ = size_mode
            if circumradius is None:
                return None
            area = self._regular_polygon_area(side_count, circumradius)
            inner_radius = self._to_positive_float(params.get("radius_inner"), default=0.0)
            if inner_radius > 0.0:
                area -= self._regular_polygon_area(side_count, inner_radius)
            return max(0.0, area)
        if action_type == CADActionType.ADD_PATH:
            start_raw = params.get("start", [0.0, 0.0])
            start = [0.0, 0.0]
            if (
                isinstance(start_raw, (list, tuple))
                and len(start_raw) >= 2
                and isinstance(start_raw[0], (int, float))
                and isinstance(start_raw[1], (int, float))
            ):
                start = [float(start_raw[0]), float(start_raw[1])]
            segments_raw = params.get("segments")
            if not isinstance(segments_raw, list):
                return None
            segment_entities, end_point, _connected, _start_tangent, _terminal_tangent = (
                self._resolve_path_segments(segments_raw, start)
            )
            if not segment_entities:
                return None
            explicit_closed = self._path_closed_flag(params)
            inferred_closed = math.dist(start, end_point) <= 1e-4
            if not explicit_closed and not inferred_closed:
                return None
            points = [start, *[segment.end_point for segment in segment_entities]]
            return abs(self._polygon_signed_area(points))
        return None

    def _normalize_local_centers(self, value: Any) -> list[list[float]]:
        if not isinstance(value, list):
            return []
        centers: list[list[float]] = []
        for item in value:
            center = self._normalize_local_center(item)
            centers.append(center)
        return centers

    def _path_closed_flag(self, params: dict[str, Any]) -> bool:
        if not isinstance(params, dict):
            return False
        return bool(params.get("closed", params.get("close", False)))

    def _normalize_local_center(self, value: Any) -> list[float]:
        if isinstance(value, dict):
            x_val = value.get("x", value.get("u", 0.0))
            y_val = value.get("y", value.get("v", 0.0))
            if isinstance(x_val, (int, float)) and isinstance(y_val, (int, float)):
                return [float(x_val), float(y_val)]
        if (
            isinstance(value, (list, tuple))
            and len(value) >= 2
            and isinstance(value[0], (int, float))
            and isinstance(value[1], (int, float))
        ):
            return [float(value[0]), float(value[1])]
        return [0.0, 0.0]

    def _normalize_rectangle_anchor_token(self, value: Any) -> str:
        token = (
            str(value).strip().lower().replace("-", "_").replace(" ", "_")
            if isinstance(value, str)
            else "center"
        )
        anchor_aliases = {
            "center": "center",
            "centre": "center",
            "lower_left": "lower_left",
            "bottom_left": "lower_left",
            "lower_right": "lower_right",
            "bottom_right": "lower_right",
            "upper_left": "top_left",
            "top_left": "top_left",
            "upper_right": "top_right",
            "top_right": "top_right",
        }
        return anchor_aliases.get(token, "center")

    def _resolve_rectangle_center_from_params(self, params: dict[str, Any]) -> list[float]:
        center = self._normalize_local_center(
            params.get("position", params.get("center"))
        )
        width = self._as_float(params.get("width"), fallback=0.0)
        height = self._as_float(params.get("height"), fallback=0.0)
        anchor = self._normalize_rectangle_anchor_token(params.get("anchor", "center"))
        if bool(params.get("centered", False)):
            anchor = "center"
        if width <= 0.0 or height <= 0.0 or anchor == "center":
            return center
        if anchor == "lower_left":
            return [center[0] + width / 2.0, center[1] + height / 2.0]
        if anchor == "lower_right":
            return [center[0] - width / 2.0, center[1] + height / 2.0]
        if anchor == "top_left":
            return [center[0] + width / 2.0, center[1] - height / 2.0]
        if anchor == "top_right":
            return [center[0] - width / 2.0, center[1] - height / 2.0]
        return center

    def _bbox_center_2d(self, points: list[list[float]]) -> list[float]:
        bbox = self._bbox_from_local_points(points)
        return [
            (bbox.xmin + bbox.xmax) / 2.0,
            (bbox.ymin + bbox.ymax) / 2.0,
        ]

    def _bbox_from_local_points(self, points: list[list[float]]) -> BoundingBox3D:
        xs = [float(point[0]) for point in points if len(point) >= 2]
        ys = [float(point[1]) for point in points if len(point) >= 2]
        if not xs or not ys:
            xs = [0.0]
            ys = [0.0]
        xmin = min(xs)
        xmax = max(xs)
        ymin = min(ys)
        ymax = max(ys)
        return BoundingBox3D(
            xlen=max(0.0, xmax - xmin),
            ylen=max(0.0, ymax - ymin),
            zlen=0.0,
            xmin=xmin,
            xmax=xmax,
            ymin=ymin,
            ymax=ymax,
            zmin=0.0,
            zmax=0.0,
        )

    def _format_sketch_ref(self, kind: str, step: int, entity_id: str) -> str:
        normalized_kind = "path" if kind == "path" else "profile"
        return f"{normalized_kind}:{int(step)}:{entity_id}"

    def _parse_sketch_ref(self, value: Any) -> dict[str, Any] | None:
        if not isinstance(value, str):
            return None
        match = SKETCH_REF_PATTERN.fullmatch(value.strip())
        if match is None:
            return None
        return {
            "kind": match.group("kind"),
            "step": int(match.group("step")),
            "entity_id": match.group("entity_id"),
            "ref": value.strip(),
        }

    def _normalize_vec2(self, value: list[float] | tuple[float, float] | None) -> list[float] | None:
        if not value or len(value) < 2:
            return None
        x = float(value[0])
        y = float(value[1])
        magnitude = math.sqrt(x * x + y * y)
        if magnitude <= 1e-9:
            return None
        return [x / magnitude, y / magnitude]

    def _clamp_relation_score(self, value: float) -> float:
        return max(0.0, min(1.0, float(value)))

    def _make_relation_signal(
        self,
        *,
        relation_id: str,
        relation_type: str,
        score: float,
        status: RelationStatus,
        entities: list[str] | None = None,
        blocking: bool = False,
        measured: dict[str, Any] | None = None,
        why: str = "",
    ) -> RelationSignal:
        return RelationSignal(
            relation_id=relation_id,
            relation_type=relation_type,
            score=self._clamp_relation_score(score),
            status=status,
            entities=self._normalize_string_list(entities or [], limit=12),
            blocking=blocking,
            measured=measured or {},
            why=why,
        )

    def _recommended_actions_from_relations(
        self,
        relations: list[RelationSignal],
    ) -> list[str]:
        recommendations: list[str] = []
        relation_to_action = {
            "candidate_set_available": "requery_topology_with_specific_hints",
            "face_role_match": "inspect_target_face_role",
            "edge_role_match": "inspect_target_edge_role",
            "circular_boundary_evidence": "inspect_circular_boundary_candidates",
            "path_connected": "repair_path_connectivity",
            "path_tangent_continuity": "repair_path_tangent_continuity",
            "path_geometry_matches_requirement": "repair_path_geometry_to_requirement",
            "profile_ring_concentric": "rebuild_concentric_ring_profile",
            "profile_area_positive": "rebuild_profile_with_positive_area",
            "profile_path_frame_attachable": "reattach_profile_to_path_endpoint",
            "profile_size_matches_requirement": "rebuild_profile_with_requirement_radii",
            "bend_realized": "inspect_or_rebuild_sweep_bend",
            "wall_thickness_consistency": "inspect_wall_thickness_and_profile_radii",
            "sweep_result_volume_consistency": "repair_sweep_result_or_upstream_geometry",
        }
        for relation in relations:
            if relation.status == RelationStatus.PASS and relation.score >= 0.85:
                continue
            recommended = relation_to_action.get(relation.relation_type)
            if not recommended or recommended in recommendations:
                continue
            recommendations.append(recommended)
        return recommendations[:8]

    def _build_relation_index(
        self,
        *,
        source_tool: str,
        step: int | None,
        focus_refs: list[str] | None,
        relations: list[RelationSignal],
        summary: str | None = None,
    ) -> RelationIndex | None:
        if not relations:
            return None
        pass_count = sum(1 for relation in relations if relation.status == RelationStatus.PASS)
        fail_count = sum(1 for relation in relations if relation.status == RelationStatus.FAIL)
        blocking_relation_ids = [
            relation.relation_id
            for relation in relations
            if relation.blocking and relation.status == RelationStatus.FAIL
        ]
        resolved_summary = summary or (
            f"{pass_count}/{len(relations)} relations pass; "
            f"{len(blocking_relation_ids)} blocking relation failure(s)."
        )
        return RelationIndex(
            source_tool=source_tool,
            step=step,
            focus_refs=self._normalize_string_list(focus_refs or [], limit=12),
            relations=relations,
            blocking_relation_ids=blocking_relation_ids,
            recommended_actions=self._recommended_actions_from_relations(relations),
            summary=resolved_summary,
        )

    def _relation_candidate_set_available(
        self,
        candidate_set: TopologyCandidateSet,
    ) -> RelationSignal:
        ref_count = len(candidate_set.ref_ids or [])
        score = 1.0 if ref_count > 0 else 0.0
        return self._make_relation_signal(
            relation_id=f"candidate_set_available:{candidate_set.candidate_id}",
            relation_type="candidate_set_available",
            score=score,
            status=RelationStatus.PASS if ref_count > 0 else RelationStatus.FAIL,
            entities=list(candidate_set.ref_ids or []),
            blocking=False,
            measured={
                "candidate_id": candidate_set.candidate_id,
                "entity_type": candidate_set.entity_type,
                "ref_count": ref_count,
            },
            why=(
                f"{candidate_set.candidate_id} exposes {ref_count} current-step candidate ref(s)."
                if ref_count > 0
                else f"{candidate_set.candidate_id} has no candidate refs in the current topology window."
            ),
        )

    def _relation_candidate_role_match(
        self,
        candidate_set: TopologyCandidateSet,
    ) -> RelationSignal:
        ref_count = len(candidate_set.ref_ids or [])
        relation_type = (
            "face_role_match"
            if str(candidate_set.entity_type).strip().lower() == "face"
            else "edge_role_match"
        )
        return self._make_relation_signal(
            relation_id=f"{relation_type}:{candidate_set.candidate_id}",
            relation_type=relation_type,
            score=1.0 if ref_count > 0 else 0.0,
            status=RelationStatus.PASS if ref_count > 0 else RelationStatus.FAIL,
            entities=list(candidate_set.ref_ids or []),
            blocking=False,
            measured={
                "candidate_id": candidate_set.candidate_id,
                "role_label": candidate_set.label,
                "ref_count": ref_count,
            },
            why=(
                f"{candidate_set.label} is currently grounded by explicit refs."
                if ref_count > 0
                else f"{candidate_set.label} is requested semantically but not grounded by current refs."
            ),
        )

    def _relation_circular_boundary_evidence(
        self,
        topology_index: TopologyObjectIndex | None,
    ) -> RelationSignal | None:
        if topology_index is None:
            return None
        circle_edges = sum(
            1
            for edge in topology_index.edges
            if str(edge.geom_type).strip().upper() == "CIRCLE"
        )
        curved_faces = sum(
            1
            for face in topology_index.faces
            if str(face.geom_type).strip().upper() in {"CYLINDER", "TORUS", "CONE"}
        )
        if circle_edges == 0 and curved_faces == 0:
            return self._make_relation_signal(
                relation_id="circular_boundary_evidence",
                relation_type="circular_boundary_evidence",
                score=0.0,
                status=RelationStatus.INFO,
                entities=[],
                blocking=False,
                measured={"circle_edges": 0, "curved_faces": 0},
                why="Current topology window does not expose circular or curved boundary evidence.",
            )
        return self._make_relation_signal(
            relation_id="circular_boundary_evidence",
            relation_type="circular_boundary_evidence",
            score=1.0,
            status=RelationStatus.PASS,
            entities=[],
            blocking=False,
            measured={
                "circle_edges": circle_edges,
                "curved_faces": curved_faces,
            },
            why=(
                f"Current topology window exposes {circle_edges} circular edge(s) and "
                f"{curved_faces} curved face(s)."
            ),
        )

    def _relation_path_connected(
        self,
        path: SketchPathEntity | None,
        *,
        blocking: bool,
    ) -> RelationSignal:
        if path is None:
            return self._make_relation_signal(
                relation_id="path_connected",
                relation_type="path_connected",
                score=0.0,
                status=RelationStatus.FAIL,
                entities=[],
                blocking=blocking,
                measured={"path_count": 0},
                why="No path evidence is currently available.",
            )
        is_connected = bool(path.connected) and float(path.total_length) > 1e-6
        return self._make_relation_signal(
            relation_id=f"path_connected:{path.path_ref}",
            relation_type="path_connected",
            score=1.0 if is_connected else 0.0,
            status=RelationStatus.PASS if is_connected else RelationStatus.FAIL,
            entities=[path.path_ref],
            blocking=blocking,
            measured={
                "segment_count": len(path.segment_types),
                "total_length": float(path.total_length),
            },
            why=(
                f"{path.path_ref} is connected and has positive length."
                if is_connected
                else f"{path.path_ref} is disconnected or degenerate."
            ),
        )

    def _relation_path_tangent_continuity(
        self,
        path: SketchPathEntity | None,
        *,
        blocking: bool,
    ) -> RelationSignal:
        if path is None:
            return self._make_relation_signal(
                relation_id="path_tangent_continuity",
                relation_type="path_tangent_continuity",
                score=0.0,
                status=RelationStatus.FAIL if blocking else RelationStatus.INFO,
                entities=[],
                blocking=blocking,
                measured={},
                why="No path evidence is currently available.",
            )
        segment_types = [str(item).strip().lower() for item in (path.segment_types or [])]
        if len(segment_types) <= 1:
            return self._make_relation_signal(
                relation_id=f"path_tangent_continuity:{path.path_ref}",
                relation_type="path_tangent_continuity",
                score=1.0,
                status=RelationStatus.INFO,
                entities=[path.path_ref],
                blocking=blocking,
                measured={"segment_types": segment_types},
                why="Single-segment path has no internal turn that needs tangency validation.",
            )
        if "tangent_arc" in segment_types:
            return self._make_relation_signal(
                relation_id=f"path_tangent_continuity:{path.path_ref}",
                relation_type="path_tangent_continuity",
                score=1.0,
                status=RelationStatus.PASS,
                entities=[path.path_ref],
                blocking=blocking,
                measured={"segment_types": segment_types},
                why="Path includes an explicit tangent arc segment, so tangent continuity is directly evidenced.",
            )
        score = 0.8 if "arc" in segment_types else 0.5
        return self._make_relation_signal(
            relation_id=f"path_tangent_continuity:{path.path_ref}",
            relation_type="path_tangent_continuity",
            score=score,
            status=RelationStatus.INFO if not blocking or score >= 0.5 else RelationStatus.FAIL,
            entities=[path.path_ref],
            blocking=blocking,
            measured={"segment_types": segment_types},
            why="Path does not expose explicit tangent-arc evidence; continuity is only weakly inferred from segment ordering.",
        )

    def _max_center_offset(self, centers: list[list[float]]) -> float:
        if not centers:
            return 0.0
        anchor = centers[0]
        if len(anchor) < 2:
            return 0.0
        offsets = [
            math.dist([float(anchor[0]), float(anchor[1])], [float(center[0]), float(center[1])])
            for center in centers[1:]
            if isinstance(center, list) and len(center) >= 2
        ]
        return max(offsets) if offsets else 0.0

    def _relation_profile_ring_concentric(
        self,
        profile: SketchProfileEntity | None,
        *,
        blocking: bool,
    ) -> RelationSignal:
        if profile is None:
            return self._make_relation_signal(
                relation_id="profile_ring_concentric",
                relation_type="profile_ring_concentric",
                score=0.0,
                status=RelationStatus.FAIL if blocking else RelationStatus.INFO,
                entities=[],
                blocking=blocking,
                measured={"profile_count": 0},
                why="No profile evidence is currently available.",
            )
        center_offset = self._max_center_offset(profile.centers or [])
        bbox_span = max(float(profile.bbox.xlen), float(profile.bbox.ylen), 1.0)
        if (
            profile.closed
            and int(profile.outer_loop_count or 0) >= 1
            and int(profile.inner_loop_count or 0) >= 1
            and str(profile.nested_relationship or "").strip().lower() == "concentric_frame"
        ):
            score = 1.0 - min(1.0, center_offset / bbox_span)
            return self._make_relation_signal(
                relation_id=f"profile_ring_concentric:{profile.profile_ref}",
                relation_type="profile_ring_concentric",
                score=score,
                status=RelationStatus.PASS,
                entities=[profile.profile_ref],
                blocking=blocking,
                measured={
                    "outer_loop_count": int(profile.outer_loop_count or 0),
                    "inner_loop_count": int(profile.inner_loop_count or 0),
                    "center_offset": center_offset,
                },
                why="Profile exposes a closed concentric ring/frame relationship.",
            )
        score = 0.0 if blocking else 0.35
        return self._make_relation_signal(
            relation_id=f"profile_ring_concentric:{profile.profile_ref}",
            relation_type="profile_ring_concentric",
            score=score,
            status=RelationStatus.FAIL if blocking else RelationStatus.INFO,
            entities=[profile.profile_ref],
            blocking=blocking,
            measured={
                "outer_loop_count": int(profile.outer_loop_count or 0),
                "inner_loop_count": int(profile.inner_loop_count or 0),
                "nested_relationship": profile.nested_relationship,
            },
            why="Profile does not yet expose strong concentric ring evidence.",
        )

    def _relation_profile_area_positive(
        self,
        profile: SketchProfileEntity | None,
        *,
        blocking: bool,
    ) -> RelationSignal:
        if profile is None:
            return self._make_relation_signal(
                relation_id="profile_area_positive",
                relation_type="profile_area_positive",
                score=0.0,
                status=RelationStatus.FAIL if blocking else RelationStatus.INFO,
                entities=[],
                blocking=blocking,
                measured={"profile_count": 0},
                why="No closed profile evidence is currently available.",
            )
        loop_radii = sorted(
            float(radius)
            for radius in (profile.loop_radii or [])
            if isinstance(radius, (int, float)) and float(radius) > 1e-6
        )
        outer_radius = loop_radii[-1] if loop_radii else None
        inner_radius = loop_radii[-2] if len(loop_radii) >= 2 else None
        observed_area = (
            float(profile.estimated_area)
            if isinstance(profile.estimated_area, (int, float))
            else None
        )
        ring_gap = (
            max(0.0, float(outer_radius) - float(inner_radius))
            if outer_radius is not None and inner_radius is not None
            else None
        )
        has_positive_area = (
            bool(profile.closed)
            and observed_area is not None
            and observed_area > 1e-6
            and (ring_gap is None or ring_gap > 1e-6)
        )
        if has_positive_area:
            reference_span = max(float(profile.bbox.xlen), float(profile.bbox.ylen), 1.0)
            normalized_gap = (
                min(1.0, float(ring_gap) / reference_span)
                if ring_gap is not None
                else 1.0
            )
            score = 0.85 + (0.15 * normalized_gap)
            status = RelationStatus.PASS
            why = "Profile exposes a positive enclosed area, so the section is materially sweepable."
        else:
            score = 0.0 if blocking else 0.35
            status = RelationStatus.FAIL if blocking else RelationStatus.INFO
            why = (
                "Profile area is zero or radii collapse together, so the section is not yet materially sweepable."
                if observed_area is not None
                else "Profile area could not be inferred from the current sketch evidence."
            )
        return self._make_relation_signal(
            relation_id=f"profile_area_positive:{profile.profile_ref}",
            relation_type="profile_area_positive",
            score=score,
            status=status,
            entities=[profile.profile_ref],
            blocking=blocking,
            measured={
                "outer_radius": outer_radius,
                "inner_radius": inner_radius,
                "ring_gap": ring_gap,
                "estimated_area": observed_area,
            },
            why=why,
        )

    def _relation_profile_path_frame_attachable(
        self,
        profile: SketchProfileEntity | None,
        path: SketchPathEntity | None,
        *,
        blocking: bool,
    ) -> RelationSignal:
        if profile is None or path is None:
            return self._make_relation_signal(
                relation_id="profile_path_frame_attachable",
                relation_type="profile_path_frame_attachable",
                score=0.0,
                status=RelationStatus.FAIL if blocking else RelationStatus.INFO,
                entities=[],
                blocking=blocking,
                measured={},
                why="Path/profile pair is incomplete, so frame compatibility cannot be confirmed.",
            )
        has_explicit_path_ref = (
            isinstance(profile.attached_path_ref, str)
            and profile.attached_path_ref.strip() == path.path_ref
        )
        has_frame_hint = has_explicit_path_ref or profile.frame_mode is not None or path.terminal_tangent is not None
        if profile.closed and path.connected and has_explicit_path_ref:
            score = 1.0
            status = RelationStatus.PASS
            why = "Profile is explicitly attached to the current path ref."
        elif profile.closed and path.connected and has_frame_hint:
            score = 0.9
            status = RelationStatus.PASS
            why = "Profile is closed and the path endpoint frame is inferable from current path evidence."
        else:
            score = 0.0 if blocking else 0.4
            status = RelationStatus.FAIL if blocking else RelationStatus.INFO
            why = "Current evidence does not strongly prove profile-to-path frame compatibility."
        return self._make_relation_signal(
            relation_id=f"profile_path_frame_attachable:{profile.profile_ref}",
            relation_type="profile_path_frame_attachable",
            score=score,
            status=status,
            entities=[path.path_ref, profile.profile_ref],
            blocking=blocking,
            measured={
                "profile_plane": profile.plane,
                "path_plane": path.plane,
                "has_explicit_path_ref": has_explicit_path_ref,
                "has_frame_mode": profile.frame_mode is not None,
                "path_terminal_tangent_known": path.terminal_tangent is not None,
            },
            why=why,
        )

    def _numeric_match_score(
        self,
        observed: float | None,
        target: float | None,
        *,
        absolute_tolerance: float,
        relative_tolerance: float,
    ) -> float | None:
        if observed is None or target is None:
            return None
        tolerance = max(float(absolute_tolerance), abs(float(target)) * float(relative_tolerance))
        if tolerance <= 1e-9:
            return 1.0 if abs(float(observed) - float(target)) <= 1e-9 else 0.0
        error = abs(float(observed) - float(target))
        if error <= tolerance:
            return max(0.85, 1.0 - (error / max(tolerance * 4.0, 1e-9)))
        return max(0.0, 0.85 - ((error - tolerance) / max(tolerance * 2.0, 1e-9)))

    def _numeric_match_passes(
        self,
        observed: float | None,
        target: float | None,
        *,
        absolute_tolerance: float,
        relative_tolerance: float,
    ) -> bool:
        if observed is None or target is None:
            return False
        tolerance = max(float(absolute_tolerance), abs(float(target)) * float(relative_tolerance))
        return abs(float(observed) - float(target)) <= tolerance

    def _extract_sweep_path_requirement_spec(
        self,
        requirement_text: str | None,
    ) -> dict[str, Any]:
        text = str(requirement_text or "").strip().lower()
        if not text:
            return {}
        line_lengths = [
            float(match.group("value"))
            for match in re.finditer(
                r"(?P<value>\d+(?:\.\d+)?)\s*mm\s+(?:horizontal|vertical|tangent\s+straight|straight|tangent)?\s*line\b",
                text,
            )
        ]
        angle_match = re.search(
            r"(?P<value>\d+(?:\.\d+)?)\s*(?:-| )?degree\s+tangent\s+arc\b",
            text,
        )
        radius_match = re.search(
            r"radius(?:\s+of)?\s+(?P<value>\d+(?:\.\d+)?)\s*mm",
            text,
        )
        spec: dict[str, Any] = {
            "segment_types": self._expected_path_segment_types(requirement_text),
            "line_lengths": line_lengths,
        }
        if angle_match is not None:
            spec["arc_angle_degrees"] = float(angle_match.group("value"))
        if radius_match is not None:
            spec["arc_radius"] = float(radius_match.group("value"))
        return spec

    def _extract_sweep_profile_requirement_spec(
        self,
        requirement_text: str | None,
    ) -> dict[str, Any]:
        text = str(requirement_text or "").strip().lower()
        if not text:
            return {}

        def _capture(pattern: str) -> float | None:
            match = re.search(pattern, text)
            if match is None:
                return None
            return float(match.group("value"))

        outer_diameter = _capture(
            r"outer\s+diameter(?:\s+of)?\s+(?P<value>\d+(?:\.\d+)?)\s*mm"
        )
        inner_diameter = _capture(
            r"inner\s+diameter(?:\s+of)?\s+(?P<value>\d+(?:\.\d+)?)\s*mm"
        )
        outer_radius = _capture(
            r"outer\s+radius(?:\s+of)?\s+(?P<value>\d+(?:\.\d+)?)\s*mm"
        )
        inner_radius = _capture(
            r"inner\s+radius(?:\s+of)?\s+(?P<value>\d+(?:\.\d+)?)\s*mm"
        )
        wall_thickness = _capture(
            r"wall\s+thickness(?:\s+of)?\s+(?P<value>\d+(?:\.\d+)?)\s*mm"
        )
        if outer_radius is None and outer_diameter is not None:
            outer_radius = outer_diameter / 2.0
        if inner_radius is None and inner_diameter is not None:
            inner_radius = inner_diameter / 2.0
        if (
            inner_radius is None
            and outer_radius is not None
            and wall_thickness is not None
            and wall_thickness > 0.0
        ):
            inner_radius = max(0.0, outer_radius - wall_thickness)
        spec: dict[str, Any] = {}
        if outer_radius is not None:
            spec["outer_radius"] = float(outer_radius)
        if inner_radius is not None:
            spec["inner_radius"] = float(inner_radius)
        if wall_thickness is not None:
            spec["wall_thickness"] = float(wall_thickness)
        if outer_radius is not None:
            area = math.pi * float(outer_radius) ** 2
            if inner_radius is not None and inner_radius > 0.0:
                area -= math.pi * float(inner_radius) ** 2
            spec["estimated_area"] = max(0.0, area)
        return spec

    def _relation_path_geometry_matches_requirement(
        self,
        path: SketchPathEntity | None,
        requirement_text: str | None,
        *,
        blocking: bool,
    ) -> RelationSignal:
        if path is None:
            return self._make_relation_signal(
                relation_id="path_geometry_matches_requirement",
                relation_type="path_geometry_matches_requirement",
                score=0.0,
                status=RelationStatus.FAIL if blocking else RelationStatus.INFO,
                entities=[],
                blocking=blocking,
                measured={"path_count": 0},
                why="No path evidence is currently available, so rail geometry cannot be compared against the requirement.",
            )
        spec = self._extract_sweep_path_requirement_spec(requirement_text)
        expected_types = [
            str(item).strip().lower()
            for item in (spec.get("segment_types") or [])
            if isinstance(item, str) and item.strip()
        ]
        expected_line_lengths = [
            float(item)
            for item in (spec.get("line_lengths") or [])
            if isinstance(item, (int, float))
        ]
        expected_arc_radius = spec.get("arc_radius")
        expected_arc_angle = spec.get("arc_angle_degrees")
        if not expected_types and not expected_line_lengths and not isinstance(
            expected_arc_radius,
            (int, float),
        ):
            return self._make_relation_signal(
                relation_id=f"path_geometry_matches_requirement:{path.path_ref}",
                relation_type="path_geometry_matches_requirement",
                score=0.5,
                status=RelationStatus.INFO,
                entities=[path.path_ref],
                blocking=blocking,
                measured={"segment_types": path.segment_types},
                why="No explicit rail geometry dimensions were parsed from the current requirement text.",
            )
        observed_types = [
            str(item).strip().lower()
            for item in (path.segment_types or [])
            if isinstance(item, str) and item.strip()
        ]
        observed_line_lengths = [
            float(segment.length)
            for segment in (path.segments or [])
            if str(segment.segment_type).strip().lower() == "line"
        ]
        arc_segment = next(
            (
                segment
                for segment in (path.segments or [])
                if str(segment.segment_type).strip().lower() in {"arc", "tangent_arc"}
            ),
            None,
        )
        observed_arc_radius = (
            float(arc_segment.radius)
            if arc_segment is not None and isinstance(arc_segment.radius, (int, float))
            else None
        )
        observed_arc_angle = (
            float(arc_segment.angle_degrees)
            if arc_segment is not None and isinstance(arc_segment.angle_degrees, (int, float))
            else None
        )
        component_scores: list[float] = []
        component_matches: list[bool] = []
        if expected_types:
            type_match = observed_types[: len(expected_types)] == expected_types
            component_matches.append(type_match)
            component_scores.append(1.0 if type_match else 0.0)
        if expected_line_lengths:
            enough_lines = len(observed_line_lengths) >= len(expected_line_lengths)
            component_matches.append(enough_lines)
            component_scores.append(1.0 if enough_lines else 0.0)
            if enough_lines:
                for observed_length, expected_length in zip(
                    observed_line_lengths,
                    expected_line_lengths,
                ):
                    component_scores.append(
                        self._numeric_match_score(
                            observed_length,
                            expected_length,
                            absolute_tolerance=1.0,
                            relative_tolerance=0.08,
                        )
                        or 0.0
                    )
                    component_matches.append(
                        self._numeric_match_passes(
                            observed_length,
                            expected_length,
                            absolute_tolerance=1.0,
                            relative_tolerance=0.08,
                        )
                    )
        if isinstance(expected_arc_radius, (int, float)):
            component_scores.append(
                self._numeric_match_score(
                    observed_arc_radius,
                    float(expected_arc_radius),
                    absolute_tolerance=1.0,
                    relative_tolerance=0.08,
                )
                or 0.0
            )
            component_matches.append(
                self._numeric_match_passes(
                    observed_arc_radius,
                    float(expected_arc_radius),
                    absolute_tolerance=1.0,
                    relative_tolerance=0.08,
                )
            )
        if isinstance(expected_arc_angle, (int, float)):
            component_scores.append(
                self._numeric_match_score(
                    observed_arc_angle,
                    float(expected_arc_angle),
                    absolute_tolerance=2.0,
                    relative_tolerance=0.03,
                )
                or 0.0
            )
            component_matches.append(
                self._numeric_match_passes(
                    observed_arc_angle,
                    float(expected_arc_angle),
                    absolute_tolerance=2.0,
                    relative_tolerance=0.03,
                )
            )
        score = (
            sum(component_scores) / len(component_scores)
            if component_scores
            else 0.5
        )
        passes = bool(component_matches) and all(component_matches)
        return self._make_relation_signal(
            relation_id=f"path_geometry_matches_requirement:{path.path_ref}",
            relation_type="path_geometry_matches_requirement",
            score=score,
            status=RelationStatus.PASS if passes else (RelationStatus.FAIL if blocking else RelationStatus.INFO),
            entities=[path.path_ref],
            blocking=blocking,
            measured={
                "expected_segment_types": expected_types,
                "observed_segment_types": observed_types,
                "expected_line_lengths": expected_line_lengths,
                "observed_line_lengths": observed_line_lengths,
                "expected_arc_radius": expected_arc_radius,
                "observed_arc_radius": observed_arc_radius,
                "expected_arc_angle_degrees": expected_arc_angle,
                "observed_arc_angle_degrees": observed_arc_angle,
            },
            why=(
                "Path segment sequence and dimensions match the parsed sweep-rail requirement."
                if passes
                else "Path segment sequence or dimensions drift from the parsed sweep-rail requirement."
            ),
        )

    def _relation_profile_size_matches_requirement(
        self,
        profile: SketchProfileEntity | None,
        requirement_text: str | None,
        *,
        blocking: bool,
    ) -> RelationSignal:
        if profile is None:
            return self._make_relation_signal(
                relation_id="profile_size_matches_requirement",
                relation_type="profile_size_matches_requirement",
                score=0.0,
                status=RelationStatus.FAIL if blocking else RelationStatus.INFO,
                entities=[],
                blocking=blocking,
                measured={"profile_count": 0},
                why="No profile evidence is currently available, so section size cannot be compared against the requirement.",
            )
        spec = self._extract_sweep_profile_requirement_spec(requirement_text)
        expected_outer_radius = spec.get("outer_radius")
        expected_inner_radius = spec.get("inner_radius")
        expected_area = spec.get("estimated_area")
        if not any(
            isinstance(value, (int, float))
            for value in (expected_outer_radius, expected_inner_radius, expected_area)
        ):
            return self._make_relation_signal(
                relation_id=f"profile_size_matches_requirement:{profile.profile_ref}",
                relation_type="profile_size_matches_requirement",
                score=0.5,
                status=RelationStatus.INFO,
                entities=[profile.profile_ref],
                blocking=blocking,
                measured={
                    "observed_loop_radii": profile.loop_radii,
                    "observed_estimated_area": profile.estimated_area,
                },
                why="No explicit annular/profile size numbers were parsed from the current requirement text.",
            )
        observed_radii = sorted(
            float(radius)
            for radius in (profile.loop_radii or [])
            if isinstance(radius, (int, float)) and float(radius) > 1e-6
        )
        observed_outer_radius = observed_radii[-1] if observed_radii else None
        observed_inner_radius = observed_radii[-2] if len(observed_radii) >= 2 else None
        observed_area = (
            float(profile.estimated_area)
            if isinstance(profile.estimated_area, (int, float))
            else None
        )
        component_scores: list[float] = []
        component_matches: list[bool] = []
        if isinstance(expected_outer_radius, (int, float)):
            component_scores.append(
                self._numeric_match_score(
                    observed_outer_radius,
                    float(expected_outer_radius),
                    absolute_tolerance=0.5,
                    relative_tolerance=0.06,
                )
                or 0.0
            )
            component_matches.append(
                self._numeric_match_passes(
                    observed_outer_radius,
                    float(expected_outer_radius),
                    absolute_tolerance=0.5,
                    relative_tolerance=0.06,
                )
            )
        if isinstance(expected_inner_radius, (int, float)):
            component_scores.append(
                self._numeric_match_score(
                    observed_inner_radius,
                    float(expected_inner_radius),
                    absolute_tolerance=0.5,
                    relative_tolerance=0.06,
                )
                or 0.0
            )
            component_matches.append(
                self._numeric_match_passes(
                    observed_inner_radius,
                    float(expected_inner_radius),
                    absolute_tolerance=0.5,
                    relative_tolerance=0.06,
                )
            )
        if isinstance(expected_area, (int, float)):
            component_scores.append(
                self._numeric_match_score(
                    observed_area,
                    float(expected_area),
                    absolute_tolerance=2.0,
                    relative_tolerance=0.08,
                )
                or 0.0
            )
            component_matches.append(
                self._numeric_match_passes(
                    observed_area,
                    float(expected_area),
                    absolute_tolerance=2.0,
                    relative_tolerance=0.08,
                )
            )
        score = (
            sum(component_scores) / len(component_scores)
            if component_scores
            else 0.5
        )
        passes = bool(component_matches) and all(component_matches)
        return self._make_relation_signal(
            relation_id=f"profile_size_matches_requirement:{profile.profile_ref}",
            relation_type="profile_size_matches_requirement",
            score=score,
            status=RelationStatus.PASS if passes else (RelationStatus.FAIL if blocking else RelationStatus.INFO),
            entities=[profile.profile_ref],
            blocking=blocking,
            measured={
                "expected_outer_radius": expected_outer_radius,
                "observed_outer_radius": observed_outer_radius,
                "expected_inner_radius": expected_inner_radius,
                "observed_inner_radius": observed_inner_radius,
                "expected_area": expected_area,
                "observed_area": observed_area,
            },
            why=(
                "Profile loop radii and enclosed area match the parsed sweep-profile requirement."
                if passes
                else "Profile loop radii or enclosed area drift from the parsed sweep-profile requirement."
            ),
        )

    def _estimate_sweep_expected_volume(
        self,
        path: SketchPathEntity | None,
        profile: SketchProfileEntity | None,
        requirement_text: str | None,
    ) -> dict[str, Any]:
        path_spec = self._extract_sweep_path_requirement_spec(requirement_text)
        profile_spec = self._extract_sweep_profile_requirement_spec(requirement_text)
        expected_path_length = None
        line_lengths = [
            float(item)
            for item in (path_spec.get("line_lengths") or [])
            if isinstance(item, (int, float))
        ]
        arc_radius = path_spec.get("arc_radius")
        arc_angle = path_spec.get("arc_angle_degrees")
        if line_lengths:
            expected_path_length = float(sum(line_lengths))
            if isinstance(arc_radius, (int, float)) and isinstance(arc_angle, (int, float)):
                expected_path_length += abs(
                    math.radians(float(arc_angle)) * float(arc_radius)
                )
        expected_profile_area = (
            float(profile_spec["estimated_area"])
            if isinstance(profile_spec.get("estimated_area"), (int, float))
            else None
        )
        source = "requirement"
        if expected_path_length is None and path is not None:
            expected_path_length = float(path.total_length)
            source = "observed"
        if expected_profile_area is None and profile is not None and isinstance(
            profile.estimated_area,
            (int, float),
        ):
            expected_profile_area = float(profile.estimated_area)
            source = "observed" if source != "requirement" else "hybrid"
        expected_volume = None
        if (
            isinstance(expected_path_length, (int, float))
            and expected_path_length > 1e-6
            and isinstance(expected_profile_area, (int, float))
            and expected_profile_area > 1e-6
        ):
            expected_volume = float(expected_path_length) * float(expected_profile_area)
        return {
            "expected_path_length": expected_path_length,
            "expected_profile_area": expected_profile_area,
            "expected_volume": expected_volume,
            "source": source,
        }

    def _face_section_radius(self, face: Any) -> float | None:
        geom_type = str(getattr(face, "geom_type", "") or "").strip().upper()
        if geom_type not in {"CYLINDER", "TORUS"}:
            return None
        bbox = getattr(face, "bbox", None)
        if bbox is None:
            return None
        dims = [
            float(getattr(bbox, "xlen", 0.0)),
            float(getattr(bbox, "ylen", 0.0)),
            float(getattr(bbox, "zlen", 0.0)),
        ]
        positive_dims = sorted(dimension for dimension in dims if dimension > 1e-6)
        if not positive_dims:
            return None
        return positive_dims[0] / 2.0

    def _cluster_numeric_values(
        self,
        values: list[float],
        *,
        tolerance: float = 0.05,
    ) -> list[list[float]]:
        if not values:
            return []
        sorted_values = sorted(float(value) for value in values)
        clusters: list[list[float]] = [[sorted_values[0]]]
        for value in sorted_values[1:]:
            current = clusters[-1]
            anchor = sum(current) / len(current)
            if abs(value - anchor) <= tolerance:
                current.append(value)
            else:
                clusters.append([value])
        return clusters

    def _relation_bend_realized(
        self,
        snapshot: CADStateSnapshot,
        *,
        blocking: bool,
    ) -> RelationSignal:
        faces = []
        if snapshot.geometry_objects is not None:
            faces.extend(snapshot.geometry_objects.faces)
        if snapshot.topology_index is not None:
            faces.extend(snapshot.topology_index.faces)
        torus_count = sum(
            1 for face in faces if str(getattr(face, "geom_type", "")).strip().upper() == "TORUS"
        )
        revolution_count = sum(
            1
            for face in faces
            if str(getattr(face, "geom_type", "")).strip().upper() == "REVOLUTION"
        )
        cylinder_count = sum(
            1 for face in faces if str(getattr(face, "geom_type", "")).strip().upper() == "CYLINDER"
        )
        curved_transition_count = torus_count + revolution_count
        realized = curved_transition_count >= 1 and cylinder_count >= 2
        return self._make_relation_signal(
            relation_id="bend_realized",
            relation_type="bend_realized",
            score=1.0 if realized else 0.0,
            status=RelationStatus.PASS if realized else (RelationStatus.FAIL if blocking else RelationStatus.INFO),
            entities=[],
            blocking=blocking,
            measured={
                "torus_faces": torus_count,
                "revolution_faces": revolution_count,
                "cylinder_faces": cylinder_count,
            },
            why=(
                "Curved transition plus multiple cylindrical branches indicate a realized bend."
                if realized
                else "Current geometry does not yet expose enough curved-branch evidence to confirm the bend."
            ),
        )

    def _relation_wall_thickness_consistency(
        self,
        snapshot: CADStateSnapshot,
        *,
        blocking: bool,
    ) -> RelationSignal:
        faces = []
        if snapshot.geometry_objects is not None:
            faces.extend(snapshot.geometry_objects.faces)
        if snapshot.topology_index is not None:
            faces.extend(snapshot.topology_index.faces)
        radii = [
            radius
            for radius in (self._face_section_radius(face) for face in faces)
            if isinstance(radius, (int, float)) and float(radius) > 1e-6
        ]
        clusters = self._cluster_numeric_values(radii)
        radius_families = [round(sum(cluster) / len(cluster), 4) for cluster in clusters]
        max_cluster_span = max(
            (max(cluster) - min(cluster) for cluster in clusters),
            default=0.0,
        )
        if len(radius_families) < 2:
            return self._make_relation_signal(
                relation_id="wall_thickness_consistency",
                relation_type="wall_thickness_consistency",
                score=0.0,
                status=RelationStatus.FAIL if blocking else RelationStatus.INFO,
                entities=[],
                blocking=blocking,
                measured={"radius_families": radius_families},
                why="Current geometry does not expose both inner and outer radius families.",
            )
        reference_radius = max(radius_families)
        score = 1.0 - min(1.0, max_cluster_span / max(reference_radius, 1.0))
        if len(radius_families) > 2:
            score = min(score, 0.7)
        observed_thickness = round(max(radius_families) - min(radius_families), 4)
        return self._make_relation_signal(
            relation_id="wall_thickness_consistency",
            relation_type="wall_thickness_consistency",
            score=score,
            status=RelationStatus.PASS if score >= 0.9 else (RelationStatus.FAIL if blocking else RelationStatus.INFO),
            entities=[],
            blocking=blocking,
            measured={
                "radius_families": radius_families,
                "observed_thickness": observed_thickness,
                "max_family_span": round(max_cluster_span, 6),
            },
            why=(
                "Inner and outer curved-face radius families are internally consistent."
                if score >= 0.9
                else "Curved-face radius families are noisy or incomplete, so wall thickness consistency is weak."
            ),
        )

    def _relation_sweep_result_volume_consistency(
        self,
        snapshot: CADStateSnapshot,
        path: SketchPathEntity | None,
        profile: SketchProfileEntity | None,
        requirement_text: str | None,
        *,
        has_material_sweep: bool,
        blocking: bool,
    ) -> RelationSignal:
        expected_meta = self._estimate_sweep_expected_volume(
            path,
            profile,
            requirement_text,
        )
        expected_volume = expected_meta.get("expected_volume")
        actual_volume = (
            float(snapshot.geometry.volume)
            if isinstance(snapshot.geometry.volume, (int, float))
            else 0.0
        )
        material_volume = abs(actual_volume)
        entities = self._normalize_string_list(
            [
                path.path_ref if path is not None else "",
                profile.profile_ref if profile is not None else "",
            ],
            limit=2,
        )
        if not has_material_sweep:
            return self._make_relation_signal(
                relation_id="sweep_result_volume_consistency",
                relation_type="sweep_result_volume_consistency",
                score=0.0,
                status=RelationStatus.INFO,
                entities=entities,
                blocking=False,
                measured={
                    "expected_volume": expected_volume,
                    "actual_volume": actual_volume,
                    "actual_volume_magnitude": material_volume,
                    "source": expected_meta.get("source"),
                },
                why="No material sweep result exists yet, so volume consistency is not ready for scoring.",
            )
        if not isinstance(expected_volume, (int, float)) or float(expected_volume) <= 1e-6:
            return self._make_relation_signal(
                relation_id="sweep_result_volume_consistency",
                relation_type="sweep_result_volume_consistency",
                score=0.5,
                status=RelationStatus.INFO,
                entities=entities,
                blocking=blocking,
                measured={
                    "expected_volume": expected_volume,
                    "actual_volume": actual_volume,
                    "actual_volume_magnitude": material_volume,
                    "source": expected_meta.get("source"),
                },
                why="Expected sweep volume could not be estimated robustly from the current requirement/profile/path evidence.",
            )
        volume_ratio = material_volume / float(expected_volume)
        passes = (
            snapshot.geometry.solids > 0
            and material_volume > 1e-6
            and 0.35 <= volume_ratio <= 1.65
        )
        score = max(0.0, 1.0 - (abs(volume_ratio - 1.0) / 0.8))
        return self._make_relation_signal(
            relation_id="sweep_result_volume_consistency",
            relation_type="sweep_result_volume_consistency",
            score=score,
            status=RelationStatus.PASS if passes else (RelationStatus.FAIL if blocking else RelationStatus.INFO),
            entities=entities,
            blocking=blocking,
            measured={
                "expected_volume": round(float(expected_volume), 6),
                "actual_volume": round(actual_volume, 6),
                "actual_volume_magnitude": round(material_volume, 6),
                "volume_ratio": round(volume_ratio, 6),
                "source": expected_meta.get("source"),
            },
            why=(
                "Sweep result volume is consistent with the inferred path length and profile area."
                if passes
                else "Sweep result volume is inconsistent with the inferred path length/profile area, which usually means the result is degenerate or upstream sweep geometry drifted."
            ),
        )

    def _relation_base_summary(
        self,
        entities: list[RelationEntity],
        relations: list[RelationFact],
        relation_groups: list[RelationGroup],
    ) -> str:
        return (
            f"{len(entities)} entities, {len(relations)} relations, "
            f"{len(relation_groups)} groups"
        )

    def _relation_base_vector_angle_degrees(
        self,
        lhs: list[float] | tuple[float, ...] | None,
        rhs: list[float] | tuple[float, ...] | None,
    ) -> float | None:
        if lhs is None or rhs is None:
            return None
        if len(lhs) < 2 or len(rhs) < 2:
            return None
        lhs_values = [float(value) for value in lhs]
        rhs_values = [float(value) for value in rhs]
        lhs_norm = math.sqrt(sum(value * value for value in lhs_values))
        rhs_norm = math.sqrt(sum(value * value for value in rhs_values))
        if lhs_norm <= 1e-9 or rhs_norm <= 1e-9:
            return None
        dot = sum(
            lhs_values[index] * rhs_values[index]
            for index in range(min(len(lhs_values), len(rhs_values)))
        )
        cosine = max(-1.0, min(1.0, dot / (lhs_norm * rhs_norm)))
        return math.degrees(math.acos(cosine))

    def _relation_base_distance_2d(
        self,
        lhs: list[float] | None,
        rhs: list[float] | None,
    ) -> float | None:
        if lhs is None or rhs is None or len(lhs) < 2 or len(rhs) < 2:
            return None
        return math.dist([float(lhs[0]), float(lhs[1])], [float(rhs[0]), float(rhs[1])])

    def _relation_base_normalize_vec3(
        self,
        value: list[float] | None,
    ) -> list[float] | None:
        if value is None or len(value) < 3:
            return None
        x = float(value[0])
        y = float(value[1])
        z = float(value[2])
        magnitude = math.sqrt((x * x) + (y * y) + (z * z))
        if magnitude <= 1e-9:
            return None
        return [x / magnitude, y / magnitude, z / magnitude]

    def _relation_base_axis_metrics(
        self,
        origin_a: list[float] | None,
        direction_a: list[float] | None,
        origin_b: list[float] | None,
        direction_b: list[float] | None,
    ) -> dict[str, float] | None:
        norm_a = self._relation_base_normalize_vec3(direction_a)
        norm_b = self._relation_base_normalize_vec3(direction_b)
        if (
            norm_a is None
            or norm_b is None
            or origin_a is None
            or origin_b is None
            or len(origin_a) < 3
            or len(origin_b) < 3
        ):
            return None
        angle_deg = self._relation_base_vector_angle_degrees(norm_a, norm_b)
        if angle_deg is None:
            return None
        delta = [
            float(origin_b[0]) - float(origin_a[0]),
            float(origin_b[1]) - float(origin_a[1]),
            float(origin_b[2]) - float(origin_a[2]),
        ]
        cross = [
            (norm_a[1] * norm_b[2]) - (norm_a[2] * norm_b[1]),
            (norm_a[2] * norm_b[0]) - (norm_a[0] * norm_b[2]),
            (norm_a[0] * norm_b[1]) - (norm_a[1] * norm_b[0]),
        ]
        cross_norm = math.sqrt(sum(value * value for value in cross))
        if cross_norm <= 1e-9:
            cross_delta = [
                (delta[1] * norm_a[2]) - (delta[2] * norm_a[1]),
                (delta[2] * norm_a[0]) - (delta[0] * norm_a[2]),
                (delta[0] * norm_a[1]) - (delta[1] * norm_a[0]),
            ]
            axis_distance = math.sqrt(sum(value * value for value in cross_delta))
        else:
            axis_distance = abs(
                (delta[0] * cross[0]) + (delta[1] * cross[1]) + (delta[2] * cross[2])
            ) / cross_norm
        return {
            "axis_angle_deg": round(float(angle_deg), 6),
            "axis_distance": round(float(axis_distance), 6),
        }

    def _relation_base_topology_distance_tolerance(
        self,
        topology_index: TopologyObjectIndex,
    ) -> float:
        spans: list[float] = []
        for face in topology_index.faces:
            spans.extend([float(face.bbox.xlen), float(face.bbox.ylen), float(face.bbox.zlen)])
        for edge in topology_index.edges:
            spans.extend([float(edge.bbox.xlen), float(edge.bbox.ylen), float(edge.bbox.zlen)])
        scale = max((value for value in spans if value > 1e-6), default=1.0)
        return max(1e-3, scale * 0.01)

    def _relation_base_plane_offset(self, face: TopologyFaceEntity) -> float | None:
        normal = self._relation_base_normalize_vec3(face.normal)
        if normal is None or len(face.center) < 3:
            return None
        return (
            (normal[0] * float(face.center[0]))
            + (normal[1] * float(face.center[1]))
            + (normal[2] * float(face.center[2]))
        )

    def _build_sketch_relation_index(
        self,
        sketch_state: SketchState | None,
        *,
        step: int | None,
    ) -> RelationIndex | None:
        if sketch_state is None:
            return None
        entities: list[RelationEntity] = []
        relations: list[RelationFact] = []
        relation_groups: list[RelationGroup] = []
        focus_entity_ids: list[str] = []
        seen_entity_ids: set[str] = set()
        path_endpoint_ids: dict[str, str] = {}

        def _add_entity(entity: RelationEntity) -> None:
            if entity.entity_id in seen_entity_ids:
                return
            seen_entity_ids.add(entity.entity_id)
            entities.append(entity)

        for path in sketch_state.paths:
            path_id = path.path_ref
            focus_entity_ids.append(path_id)
            _add_entity(
                RelationEntity(
                    entity_id=path_id,
                    entity_type="sketch_path",
                    ref=path.path_ref,
                    label=path.path_ref,
                    attributes={
                        "plane": path.plane,
                        "segment_types": list(path.segment_types),
                        "connected": bool(path.connected),
                        "closed": bool(path.closed),
                        "total_length": round(float(path.total_length), 6),
                    },
                )
            )
            endpoint_id = f"{path.path_ref}:endpoint:end"
            path_endpoint_ids[path.path_ref] = endpoint_id
            _add_entity(
                RelationEntity(
                    entity_id=endpoint_id,
                    entity_type="path_endpoint",
                    ref=None,
                    label=f"{path.path_ref} end",
                    attributes={
                        "point": list(path.end_point),
                        "terminal_tangent": path.terminal_tangent,
                        "plane": path.plane,
                    },
                )
            )
            previous_segment: SketchSegmentEntity | None = None
            previous_segment_id: str | None = None
            for segment in path.segments:
                segment_id = f"{path.path_ref}:segment:{segment.segment_index}"
                _add_entity(
                    RelationEntity(
                        entity_id=segment_id,
                        entity_type="path_segment",
                        ref=None,
                        label=f"{path.path_ref} segment {segment.segment_index}",
                        attributes={
                            "segment_type": segment.segment_type,
                            "start_point": list(segment.start_point),
                            "end_point": list(segment.end_point),
                            "length": round(float(segment.length), 6),
                            "radius": segment.radius,
                            "angle_degrees": segment.angle_degrees,
                        },
                    )
                )
                if previous_segment is not None and previous_segment_id is not None:
                    joint_gap = self._relation_base_distance_2d(
                        previous_segment.end_point,
                        segment.start_point,
                    )
                    if joint_gap is not None and joint_gap <= 1e-4:
                        relations.append(
                            RelationFact(
                                relation_id=f"connected:{previous_segment_id}:{segment_id}",
                                relation_type="connected",
                                lhs=previous_segment_id,
                                rhs=segment_id,
                                metrics={"joint_gap": round(float(joint_gap), 6)},
                                evidence="Adjacent path segments share the same endpoint.",
                            )
                        )
                    tangent_angle = self._relation_base_vector_angle_degrees(
                        previous_segment.end_tangent,
                        segment.start_tangent,
                    )
                    if tangent_angle is not None and tangent_angle <= 5.0:
                        relations.append(
                            RelationFact(
                                relation_id=f"tangent:{previous_segment_id}:{segment_id}",
                                relation_type="tangent",
                                lhs=previous_segment_id,
                                rhs=segment_id,
                                metrics={
                                    "junction_angle_deg": round(
                                        float(tangent_angle),
                                        6,
                                    )
                                },
                                evidence="Adjacent path segments meet with tangent continuity.",
                            )
                        )
                previous_segment = segment
                previous_segment_id = segment_id

        for profile in sketch_state.profiles:
            profile_id = profile.profile_ref
            focus_entity_ids.append(profile_id)
            _add_entity(
                RelationEntity(
                    entity_id=profile_id,
                    entity_type="sketch_profile",
                    ref=profile.profile_ref,
                    label=profile.profile_ref,
                    attributes={
                        "plane": profile.plane,
                        "closed": bool(profile.closed),
                        "outer_loop_count": int(profile.outer_loop_count),
                        "inner_loop_count": int(profile.inner_loop_count),
                        "attached_path_ref": profile.attached_path_ref,
                        "frame_mode": profile.frame_mode,
                    },
                )
            )
            clusters: dict[tuple[float, float], list[SketchLoopEntity]] = {}
            for loop in profile.loops:
                _add_entity(
                    RelationEntity(
                        entity_id=loop.loop_id,
                        entity_type="profile_loop",
                        ref=None,
                        label=loop.loop_id,
                        attributes={
                            "loop_type": loop.loop_type,
                            "role": loop.role,
                            "center": loop.center,
                            "radius": loop.radius,
                        },
                    )
                )
                if (
                    str(loop.loop_type).strip().lower() == "circle"
                    and isinstance(loop.center, list)
                    and len(loop.center) >= 2
                ):
                    key = (
                        round(float(loop.center[0]), 6),
                        round(float(loop.center[1]), 6),
                    )
                    clusters.setdefault(key, []).append(loop)
            cluster_index = 0
            for center_key, loops in clusters.items():
                if len(loops) < 2:
                    continue
                cluster_index += 1
                for lhs_index in range(len(loops)):
                    for rhs_index in range(lhs_index + 1, len(loops)):
                        lhs_loop = loops[lhs_index]
                        rhs_loop = loops[rhs_index]
                        center_offset = self._relation_base_distance_2d(
                            lhs_loop.center,
                            rhs_loop.center,
                        )
                        relations.append(
                            RelationFact(
                                relation_id=f"concentric:{lhs_loop.loop_id}:{rhs_loop.loop_id}",
                                relation_type="concentric",
                                lhs=lhs_loop.loop_id,
                                rhs=rhs_loop.loop_id,
                                metrics={
                                    "center_offset": round(
                                        float(center_offset or 0.0),
                                        6,
                                    )
                                },
                                evidence="These circular loops share the same sketch-local center.",
                            )
                        )
                ordered_loops = sorted(
                    (
                        loop
                        for loop in loops
                        if isinstance(loop.radius, (int, float))
                    ),
                    key=lambda item: float(item.radius or 0.0),
                    reverse=True,
                )
                if len(ordered_loops) >= 2:
                    outer_radius = float(ordered_loops[0].radius or 0.0)
                    inner_radius = float(ordered_loops[-1].radius or 0.0)
                    relation_groups.append(
                        RelationGroup(
                            group_id=f"annular_profile:{profile_id}:{cluster_index}",
                            group_type="annular_profile",
                            members=[profile_id, *[loop.loop_id for loop in ordered_loops]],
                            derived={
                                "center": [float(center_key[0]), float(center_key[1])],
                                "outer_radius": outer_radius,
                                "inner_radius": inner_radius,
                                "wall_thickness": round(
                                    max(0.0, outer_radius - inner_radius),
                                    6,
                                ),
                            },
                            evidence="This profile window contains concentric circular loops that form an annular section.",
                        )
                    )
            if (
                isinstance(profile.attached_path_ref, str)
                and profile.attached_path_ref in path_endpoint_ids
            ):
                endpoint_id = path_endpoint_ids[profile.attached_path_ref]
                relations.append(
                    RelationFact(
                        relation_id=f"attached_to_path_endpoint:{profile_id}:{endpoint_id}",
                        relation_type="attached_to_path_endpoint",
                        lhs=profile_id,
                        rhs=endpoint_id,
                        metrics={
                            "frame_mode": profile.frame_mode,
                            "profile_plane": profile.plane,
                        },
                        evidence="This profile sketch was opened from a path endpoint frame.",
                    )
                )
                relation_groups.append(
                    RelationGroup(
                        group_id=f"sweep_profile_pair:{profile.attached_path_ref}:{profile_id}",
                        group_type="sweep_profile_pair",
                        members=[profile.attached_path_ref, endpoint_id, profile_id],
                        derived={
                            "frame_mode": profile.frame_mode,
                            "profile_plane": profile.plane,
                        },
                        evidence="Current sketch evidence exposes a path/profile pair for a sweep-like feature.",
                    )
                )

        return RelationIndex(
            source_tool="query_sketch",
            step=step,
            focus_entity_ids=list(dict.fromkeys(focus_entity_ids))[:16],
            entities=entities,
            relations=relations,
            relation_groups=relation_groups,
            summary=self._relation_base_summary(entities, relations, relation_groups),
        )

    def _build_topology_relation_index(
        self,
        topology_index: TopologyObjectIndex | None,
        candidate_sets: list[TopologyCandidateSet],
        *,
        step: int | None,
    ) -> RelationIndex | None:
        _ = candidate_sets
        if topology_index is None:
            return None
        entities: list[RelationEntity] = []
        relations: list[RelationFact] = []
        relation_groups: list[RelationGroup] = []
        focus_entity_ids: list[str] = []
        seen_entity_ids: set[str] = set()
        seen_relation_ids: set[str] = set()
        seen_group_ids: set[str] = set()
        distance_tol = self._relation_base_topology_distance_tolerance(topology_index)
        angle_tol = 2.0
        radius_tol = max(1e-3, distance_tol * 0.5)

        def _add_entity(entity: RelationEntity) -> None:
            if entity.entity_id in seen_entity_ids:
                return
            seen_entity_ids.add(entity.entity_id)
            entities.append(entity)

        def _add_relation(relation: RelationFact) -> None:
            if relation.relation_id in seen_relation_ids:
                return
            seen_relation_ids.add(relation.relation_id)
            relations.append(relation)

        def _add_group(group: RelationGroup) -> None:
            if group.group_id in seen_group_ids:
                return
            seen_group_ids.add(group.group_id)
            relation_groups.append(group)

        for face in topology_index.faces:
            focus_entity_ids.append(face.face_ref)
            _add_entity(
                RelationEntity(
                    entity_id=face.face_ref,
                    entity_type="topology_face",
                    ref=face.face_ref,
                    label=face.face_ref,
                    attributes={
                        "geom_type": face.geom_type,
                        "center": face.center,
                        "normal": face.normal,
                        "axis_origin": face.axis_origin,
                        "axis_direction": face.axis_direction,
                        "radius": face.radius,
                        "parent_solid_id": face.parent_solid_id,
                    },
                )
            )
        for edge in topology_index.edges:
            focus_entity_ids.append(edge.edge_ref)
            _add_entity(
                RelationEntity(
                    entity_id=edge.edge_ref,
                    entity_type="topology_edge",
                    ref=edge.edge_ref,
                    label=edge.edge_ref,
                    attributes={
                        "geom_type": edge.geom_type,
                        "center": edge.center,
                        "axis_origin": edge.axis_origin,
                        "axis_direction": edge.axis_direction,
                        "radius": edge.radius,
                        "parent_solid_id": edge.parent_solid_id,
                    },
                )
            )

        circular_edges = [
            edge
            for edge in topology_index.edges[:16]
            if str(edge.geom_type).strip().upper() == "CIRCLE"
        ]
        for lhs_index in range(len(circular_edges)):
            for rhs_index in range(lhs_index + 1, len(circular_edges)):
                lhs_edge = circular_edges[lhs_index]
                rhs_edge = circular_edges[rhs_index]
                center_distance = self._relation_base_distance_2d(
                    lhs_edge.center[:2] if lhs_edge.center else None,
                    rhs_edge.center[:2] if rhs_edge.center else None,
                )
                if center_distance is not None and center_distance <= distance_tol:
                    _add_relation(
                        RelationFact(
                            relation_id=f"concentric:{lhs_edge.edge_ref}:{rhs_edge.edge_ref}",
                            relation_type="concentric",
                            lhs=lhs_edge.edge_ref,
                            rhs=rhs_edge.edge_ref,
                            metrics={
                                "center_distance": round(float(center_distance), 6)
                            },
                            evidence="These circular topology edges share the same center.",
                        )
                    )
                axis_metrics = self._relation_base_axis_metrics(
                    lhs_edge.axis_origin,
                    lhs_edge.axis_direction,
                    rhs_edge.axis_origin,
                    rhs_edge.axis_direction,
                )
                if (
                    axis_metrics is not None
                    and float(axis_metrics["axis_angle_deg"]) <= angle_tol
                    and float(axis_metrics["axis_distance"]) <= distance_tol
                ):
                    _add_relation(
                        RelationFact(
                            relation_id=f"coaxial:{lhs_edge.edge_ref}:{rhs_edge.edge_ref}",
                            relation_type="coaxial",
                            lhs=lhs_edge.edge_ref,
                            rhs=rhs_edge.edge_ref,
                            metrics=axis_metrics,
                            evidence="These circular topology edges lie on the same axis.",
                        )
                    )
                if (
                    isinstance(lhs_edge.radius, (int, float))
                    and isinstance(rhs_edge.radius, (int, float))
                    and abs(float(lhs_edge.radius) - float(rhs_edge.radius)) <= radius_tol
                ):
                    _add_relation(
                        RelationFact(
                            relation_id=f"equal_radius:{lhs_edge.edge_ref}:{rhs_edge.edge_ref}",
                            relation_type="equal_radius",
                            lhs=lhs_edge.edge_ref,
                            rhs=rhs_edge.edge_ref,
                            metrics={
                                "radius_delta": round(
                                    abs(float(lhs_edge.radius) - float(rhs_edge.radius)),
                                    6,
                                )
                            },
                            evidence="These circular topology edges have the same radius within tolerance.",
                        )
                    )
                if (
                    axis_metrics is not None
                    and isinstance(lhs_edge.radius, (int, float))
                    and isinstance(rhs_edge.radius, (int, float))
                    and float(axis_metrics["axis_angle_deg"]) <= angle_tol
                    and float(axis_metrics["axis_distance"]) <= distance_tol
                    and abs(float(lhs_edge.radius) - float(rhs_edge.radius)) > radius_tol
                ):
                    outer_radius = max(float(lhs_edge.radius), float(rhs_edge.radius))
                    inner_radius = min(float(lhs_edge.radius), float(rhs_edge.radius))
                    _add_group(
                        RelationGroup(
                            group_id=f"annular_edge_pair:{lhs_edge.edge_ref}:{rhs_edge.edge_ref}",
                            group_type="annular_edge_pair",
                            members=[lhs_edge.edge_ref, rhs_edge.edge_ref],
                            derived={
                                "outer_radius": round(outer_radius, 6),
                                "inner_radius": round(inner_radius, 6),
                                "wall_thickness": round(outer_radius - inner_radius, 6),
                            },
                            evidence="These circular topology edges define an annular section on the same axis family.",
                        )
                    )

        cylindrical_faces = [
            face
            for face in topology_index.faces[:16]
            if str(face.geom_type).strip().upper() == "CYLINDER"
        ]
        for lhs_index in range(len(cylindrical_faces)):
            for rhs_index in range(lhs_index + 1, len(cylindrical_faces)):
                lhs_face = cylindrical_faces[lhs_index]
                rhs_face = cylindrical_faces[rhs_index]
                axis_metrics = self._relation_base_axis_metrics(
                    lhs_face.axis_origin,
                    lhs_face.axis_direction,
                    rhs_face.axis_origin,
                    rhs_face.axis_direction,
                )
                if (
                    axis_metrics is None
                    or float(axis_metrics["axis_angle_deg"]) > angle_tol
                    or float(axis_metrics["axis_distance"]) > distance_tol
                ):
                    continue
                _add_relation(
                    RelationFact(
                        relation_id=f"coaxial:{lhs_face.face_ref}:{rhs_face.face_ref}",
                        relation_type="coaxial",
                        lhs=lhs_face.face_ref,
                        rhs=rhs_face.face_ref,
                        metrics=axis_metrics,
                        evidence="These cylindrical faces share the same axis.",
                    )
                )
                if (
                    isinstance(lhs_face.radius, (int, float))
                    and isinstance(rhs_face.radius, (int, float))
                ):
                    radius_delta = abs(float(lhs_face.radius) - float(rhs_face.radius))
                    if radius_delta <= radius_tol:
                        _add_relation(
                            RelationFact(
                                relation_id=f"equal_radius:{lhs_face.face_ref}:{rhs_face.face_ref}",
                                relation_type="equal_radius",
                                lhs=lhs_face.face_ref,
                                rhs=rhs_face.face_ref,
                                metrics={"radius_delta": round(radius_delta, 6)},
                                evidence="These cylindrical faces have matching radii within tolerance.",
                            )
                        )
                    else:
                        outer_radius = max(float(lhs_face.radius), float(rhs_face.radius))
                        inner_radius = min(float(lhs_face.radius), float(rhs_face.radius))
                        _add_group(
                            RelationGroup(
                                group_id=f"annular_cylindrical_pair:{lhs_face.face_ref}:{rhs_face.face_ref}",
                                group_type="annular_cylindrical_pair",
                                members=[lhs_face.face_ref, rhs_face.face_ref],
                                derived={
                                    "outer_radius": round(outer_radius, 6),
                                    "inner_radius": round(inner_radius, 6),
                                    "wall_thickness": round(outer_radius - inner_radius, 6),
                                },
                                evidence="These coaxial cylindrical faces define an inner/outer wall pair.",
                            )
                        )

        planar_faces = [
            face
            for face in topology_index.faces[:12]
            if str(face.geom_type).strip().upper() == "PLANE"
            and isinstance(face.normal, list)
            and len(face.normal) >= 3
        ]
        for lhs_index in range(len(planar_faces)):
            for rhs_index in range(lhs_index + 1, len(planar_faces)):
                lhs_face = planar_faces[lhs_index]
                rhs_face = planar_faces[rhs_index]
                angle_deg = self._relation_base_vector_angle_degrees(lhs_face.normal, rhs_face.normal)
                lhs_offset = self._relation_base_plane_offset(lhs_face)
                rhs_offset = self._relation_base_plane_offset(rhs_face)
                if (
                    angle_deg is None
                    or lhs_offset is None
                    or rhs_offset is None
                    or float(angle_deg) > angle_tol
                    or abs(float(lhs_offset) - float(rhs_offset)) > distance_tol
                ):
                    continue
                _add_relation(
                    RelationFact(
                        relation_id=f"coplanar:{lhs_face.face_ref}:{rhs_face.face_ref}",
                        relation_type="coplanar",
                        lhs=lhs_face.face_ref,
                        rhs=rhs_face.face_ref,
                        metrics={
                            "normal_angle_deg": round(float(angle_deg), 6),
                            "plane_distance": round(
                                abs(float(lhs_offset) - float(rhs_offset)),
                                6,
                            ),
                        },
                        evidence="These planar faces lie on the same plane within tolerance.",
                    )
                )

        return RelationIndex(
            source_tool="query_topology",
            step=step,
            focus_entity_ids=list(dict.fromkeys(focus_entity_ids))[:16],
            entities=entities,
            relations=relations,
            relation_groups=relation_groups,
            summary=self._relation_base_summary(entities, relations, relation_groups),
        )

    def _build_validation_relation_index(
        self,
        snapshot: CADStateSnapshot,
        history: list[ActionHistoryEntry],
        requirements: dict[str, object],
        requirement_text: str | None,
    ) -> RelationIndex | None:
        _ = (snapshot, history, requirements, requirement_text)
        return None

    def _build_requirement_checks(
        self,
        snapshot: CADStateSnapshot,
        history: list[ActionHistoryEntry],
        requirements: dict[str, object],
        requirement_text: str | None,
    ) -> list[RequirementCheck]:
        checks: list[RequirementCheck] = []

        has_solid = snapshot.geometry.solids > 0
        has_positive_volume = (
            has_solid
            and isinstance(snapshot.geometry.volume, (int, float))
            and abs(float(snapshot.geometry.volume)) > 1e-6
        )
        checks.append(
            RequirementCheck(
                check_id="solid_exists",
                label="Model contains at least one solid",
                status=(
                    RequirementCheckStatus.PASS
                    if has_solid
                    else RequirementCheckStatus.FAIL
                ),
                blocking=True,
                evidence=f"solids={snapshot.geometry.solids}",
            )
        )
        checks.append(
            RequirementCheck(
                check_id="solid_positive_volume",
                label="Model solid has positive volume",
                status=(
                    RequirementCheckStatus.PASS
                    if has_positive_volume
                    else RequirementCheckStatus.FAIL
                ),
                blocking=True,
                evidence=(
                    f"volume={snapshot.geometry.volume}"
                    if has_positive_volume
                    else f"solids={snapshot.geometry.solids}, volume={snapshot.geometry.volume}"
                ),
            )
        )

        has_no_issues = len(snapshot.issues) == 0
        checks.append(
            RequirementCheck(
                check_id="no_geometry_issues",
                label="No geometry issues reported",
                status=(
                    RequirementCheckStatus.PASS
                    if has_no_issues
                    else RequirementCheckStatus.FAIL
                ),
                blocking=True,
                evidence=(
                    "no issues"
                    if has_no_issues
                    else f"issues={len(snapshot.issues)}:{'; '.join(snapshot.issues[:3])}"
                ),
            )
        )
        checks.extend(self._build_structured_issue_checks(snapshot))

        checks.extend(
            self._build_dimension_checks(
                bbox=snapshot.geometry.bbox,
                requirements=requirements,
            )
        )
        checks.extend(
            self._build_named_plane_positive_extrude_checks(
                snapshot=snapshot,
                requirement_text=requirement_text,
            )
        )
        checks.extend(
            self._build_named_axis_axisymmetric_pose_checks(
                snapshot=snapshot,
                requirement_text=requirement_text,
            )
        )
        checks.extend(
            self._build_cylindrical_slot_alignment_checks(
                snapshot=snapshot,
                requirement_text=requirement_text,
            )
        )
        checks.extend(
            self._build_path_sweep_checks(
                snapshot=snapshot,
                history=history,
                requirements=requirements,
                requirement_text=requirement_text,
            )
        )
        checks.extend(
            self._build_loft_checks(
                snapshot=snapshot,
                history=history,
                requirement_text=requirement_text,
            )
        )
        checks.extend(
            self._build_revolve_profile_checks(
                snapshot=snapshot,
                history=history,
                requirement_text=requirement_text,
            )
        )
        checks.extend(
            self._build_feature_checks(
                snapshot=snapshot,
                history=history,
                requirements=requirements,
                requirement_text=requirement_text,
            )
        )
        return checks

    def _build_structured_issue_checks(
        self,
        snapshot: CADStateSnapshot,
    ) -> list[RequirementCheck]:
        blockers = self._normalize_string_list(snapshot.blockers, limit=24)
        warnings = self._normalize_string_list(snapshot.warnings, limit=24)
        checks: list[RequirementCheck] = [
            RequirementCheck(
                check_id="no_structured_blockers",
                label="No unresolved structured execution blockers remain",
                status=(
                    RequirementCheckStatus.PASS
                    if not blockers
                    else RequirementCheckStatus.FAIL
                ),
                blocking=True,
                evidence=(
                    "no structured blockers"
                    if not blockers
                    else f"blockers={blockers}"
                ),
            )
        ]
        checks.append(
            RequirementCheck(
                check_id="no_structured_warnings",
                label="Execution warnings are either absent or already resolved",
                status=(
                    RequirementCheckStatus.PASS
                    if not warnings
                    else RequirementCheckStatus.FAIL
                ),
                blocking=False,
                evidence=(
                    "no structured warnings"
                    if not warnings
                    else f"warnings={warnings}"
                ),
            )
        )
        return checks

    def _build_path_sweep_checks(
        self,
        snapshot: CADStateSnapshot,
        history: list[ActionHistoryEntry],
        requirements: dict[str, object],
        requirement_text: str | None,
    ) -> list[RequirementCheck]:
        semantics = analyze_requirement_semantics(
            requirements=(
                requirements if isinstance(requirements, dict) else None
            ),
            requirement_text=requirement_text,
        )
        sweep_family_requested = requirement_requests_path_sweep(
            requirements if isinstance(requirements, dict) else None,
            requirement_text=requirement_text,
            semantics=semantics,
        )
        has_material_sweep = any(
            entry.action_type == CADActionType.SWEEP
            and self._history_action_materially_changes_geometry(history, index)
            for index, entry in enumerate(history)
        )
        if not sweep_family_requested and not has_material_sweep:
            return []

        sketch_state = snapshot.sketch_state or self._build_sketch_state(
            history=history,
            snapshot=snapshot,
            step=max(1, snapshot.step),
        )
        blockers = set(self._normalize_string_list(snapshot.blockers, limit=32))
        paths = list(sketch_state.paths)
        profiles = list(sketch_state.profiles)
        primary_path = next((item for item in paths if item.connected), None)
        if primary_path is None and paths:
            primary_path = paths[0]
        expected_segment_types = self._expected_path_segment_types(requirement_text)
        hollow_profile_required = self._requirement_requires_hollow_sweep_profile(
            requirement_text
        )
        bend_required = self._requirement_requires_bent_sweep_result(requirement_text)
        execute_build123d_path_sweep_recipe = (
            self._history_starts_from_execute_build123d_snapshot(history)
            and self._history_has_execute_build123d_path_sweep_recipe(history)
        )
        snapshot_path_sweep_fallback = False
        snapshot_path_sweep_evidence = ""
        if execute_build123d_path_sweep_recipe:
            snapshot_path_sweep_fallback, snapshot_path_sweep_evidence = (
                self._snapshot_has_execute_build123d_path_sweep_fallback(
                    snapshot=snapshot,
                    hollow_profile_required=hollow_profile_required,
                    bend_required=bend_required,
                )
            )
        rail_ok = (
            primary_path is not None
            and bool(primary_path.segment_types)
            and bool(primary_path.connected)
            and primary_path.total_length > 1e-6
            and "path_disconnected" not in blockers
            and "feature_path_sweep_rail" not in blockers
        )
        rail_relation = None
        if rail_ok and primary_path is not None:
            rail_relation = self._relation_path_geometry_matches_requirement(
                primary_path,
                requirement_text,
                blocking=True,
            )
            rail_ok = rail_relation.status != RelationStatus.FAIL
        if snapshot_path_sweep_fallback:
            rail_ok = True
        profile_ok = any(
            profile.closed
            and profile.outer_loop_count >= 1
            and (
                not hollow_profile_required
                or profile.inner_loop_count >= 1
            )
            for profile in profiles
        )
        if "missing_profile" in blockers or "feature_path_sweep_profile" in blockers:
            profile_ok = False
        if snapshot_path_sweep_fallback:
            profile_ok = True

        frame_ok = self._history_has_path_attached_profile_frame(history)
        if not frame_ok:
            frame_ok = (
                primary_path is not None
                and primary_path.terminal_tangent is not None
                and self._history_has_secondary_profile_sketch_window(history)
            )
        if "feature_path_sweep_frame" in blockers:
            frame_ok = False
        if snapshot_path_sweep_fallback:
            frame_ok = True

        realized_bend = (
            primary_path is not None
            and any(
                segment_type in {"arc", "tangent_arc"}
                for segment_type in primary_path.segment_types
            )
        )
        result_ok = (
            has_material_sweep
            and snapshot.geometry.solids > 0
            and isinstance(snapshot.geometry.volume, (int, float))
            and abs(float(snapshot.geometry.volume)) > 1e-6
            and "feature_path_sweep_result" not in blockers
            and (not bend_required or realized_bend)
        )
        if snapshot_path_sweep_fallback:
            result_ok = True

        fallback_evidence = ""
        if snapshot_path_sweep_fallback:
            fallback_evidence = (
                "execute_build123d_path_sweep_recipe=true, "
                f"{snapshot_path_sweep_evidence}"
            )

        return [
            RequirementCheck(
                check_id="feature_path_sweep_rail",
                label="Path-sweep rail is connected and matches the intended segment sequence",
                status=(
                    RequirementCheckStatus.PASS
                    if rail_ok
                    else RequirementCheckStatus.FAIL
                ),
                blocking=True,
                evidence=(
                    fallback_evidence
                    if snapshot_path_sweep_fallback and primary_path is None
                    else (
                        f"path_ref={primary_path.path_ref}, segment_types={primary_path.segment_types}, "
                        f"path_geometry={rail_relation.measured}"
                    )
                    if rail_ok and primary_path is not None and rail_relation is not None
                    else (
                        f"path_ref={primary_path.path_ref}, segment_types={primary_path.segment_types}, "
                        f"path_geometry={rail_relation.measured}, why={rail_relation.why}"
                    )
                    if primary_path is not None and rail_relation is not None
                    else (
                        f"expected_segment_types={expected_segment_types}, blockers={sorted(blockers)}, path_count={len(paths)}"
                    )
                ),
            ),
            RequirementCheck(
                check_id="feature_path_sweep_profile",
                label="Path-sweep profile is closed and matches the required ring/loop structure",
                status=(
                    RequirementCheckStatus.PASS
                    if profile_ok
                    else RequirementCheckStatus.FAIL
                ),
                blocking=True,
                evidence=(
                    fallback_evidence
                    if snapshot_path_sweep_fallback and not profiles
                    else "closed profile available"
                    if profile_ok
                    else (
                        f"hollow_profile_required={hollow_profile_required}, profile_count={len(profiles)}, blockers={sorted(blockers)}"
                    )
                ),
            ),
            RequirementCheck(
                check_id="feature_path_sweep_frame",
                label="Sweep profile is attached using an explicit or inferable path-end frame",
                status=(
                    RequirementCheckStatus.PASS
                    if frame_ok
                    else RequirementCheckStatus.FAIL
                ),
                blocking=True,
                evidence=(
                    fallback_evidence
                    if snapshot_path_sweep_fallback
                    else "path-end profile frame found"
                    if frame_ok
                    else "missing path-attached profile sketch window or terminal tangent frame"
                ),
            ),
            RequirementCheck(
                check_id="feature_path_sweep_result",
                label="Sweep result produces the intended solid and preserves required bend semantics",
                status=(
                    RequirementCheckStatus.PASS
                    if result_ok
                    else RequirementCheckStatus.FAIL
                ),
                blocking=True,
                evidence=(
                    fallback_evidence
                    if snapshot_path_sweep_fallback and not has_material_sweep
                    else f"material_sweep={has_material_sweep}, bend_required={bend_required}, realized_bend={realized_bend}, volume={snapshot.geometry.volume}"
                ),
            ),
        ]

    def _build_loft_checks(
        self,
        snapshot: CADStateSnapshot,
        history: list[ActionHistoryEntry],
        requirement_text: str | None,
    ) -> list[RequirementCheck]:
        if not self._requirement_suggests_loft(requirement_text, history):
            return []

        sketch_state = snapshot.sketch_state or self._build_sketch_state(
            history=history,
            snapshot=snapshot,
            step=max(1, snapshot.step),
        )
        blockers = set(self._normalize_string_list(snapshot.blockers, limit=32))
        loft_profiles = [
            profile
            for profile in sketch_state.profiles
            if profile.closed and profile.outer_loop_count >= 1 and bool(profile.loftable)
        ]
        needs_apex_only = self._history_has_point_loft(history)
        snapshot_loft_fallback = False
        snapshot_loft_evidence = ""
        if self._history_starts_from_execute_build123d_snapshot(history):
            snapshot_loft_fallback, snapshot_loft_evidence = (
                self._snapshot_has_requirement_aligned_loft_transition(
                    snapshot=history[-1].result_snapshot,
                    requirement_text=requirement_text,
                )
            )
        profile_stack_ok = len(loft_profiles) >= (1 if needs_apex_only else 2)
        if "feature_loft_profile_stack" in blockers and not snapshot_loft_fallback:
            profile_stack_ok = False
        if snapshot_loft_fallback:
            profile_stack_ok = True
        distinct_profile_planes = sorted(
            {
                str(profile.plane).strip().upper()
                for profile in loft_profiles
                if isinstance(profile.plane, str) and profile.plane.strip()
            }
        )
        stack_order = [profile.profile_ref for profile in loft_profiles]
        source_sketch_steps = [
            profile.source_sketch_step
            for profile in loft_profiles
            if isinstance(profile.source_sketch_step, int)
        ]
        has_material_loft = any(
            entry.action_type == CADActionType.LOFT
            and self._history_action_materially_changes_geometry(history, index)
            for index, entry in enumerate(history)
        )
        result_ok = (
            (has_material_loft or snapshot_loft_fallback)
            and snapshot.geometry.solids > 0
            and isinstance(snapshot.geometry.volume, (int, float))
            and abs(float(snapshot.geometry.volume)) > 1e-6
            and (
                "feature_loft_result" not in blockers
                or snapshot_loft_fallback
            )
        )
        return [
            RequirementCheck(
                check_id="feature_loft_profile_stack",
                label="Loft has a sufficient closed profile stack before execution",
                status=(
                    RequirementCheckStatus.PASS
                    if profile_stack_ok
                    else RequirementCheckStatus.FAIL
                ),
                blocking=True,
                evidence=(
                    snapshot_loft_evidence
                    if snapshot_loft_fallback and not loft_profiles
                    else (
                        f"profile_count={len(loft_profiles)}, planes={distinct_profile_planes}, stack_order={stack_order}, source_sketch_steps={source_sketch_steps}, needs_apex_only={needs_apex_only}"
                    )
                ),
            ),
            RequirementCheck(
                check_id="feature_loft_result",
                label="Loft result materially realizes a positive-volume stacked-profile transition",
                status=(
                    RequirementCheckStatus.PASS
                    if result_ok
                    else RequirementCheckStatus.FAIL
                ),
                blocking=True,
                evidence=(
                    snapshot_loft_evidence
                    if snapshot_loft_fallback and not has_material_loft
                    else (
                        f"material_loft={has_material_loft}, profile_count={len(loft_profiles)}, volume={snapshot.geometry.volume}"
                    )
                ),
            ),
        ]

    def _build_revolve_profile_checks(
        self,
        snapshot: CADStateSnapshot,
        history: list[ActionHistoryEntry],
        requirement_text: str | None,
    ) -> list[RequirementCheck]:
        if not self._requirement_suggests_tapered_revolve(requirement_text, history):
            return []

        sketch_state = snapshot.sketch_state or self._build_sketch_state(
            history=history,
            snapshot=snapshot,
            step=max(1, snapshot.step),
        )
        blockers = set(self._normalize_string_list(snapshot.blockers, limit=32))
        profiles = [profile for profile in sketch_state.profiles if profile.closed]
        execute_build123d_face_revolve = (
            self._history_starts_from_execute_build123d_snapshot(history)
            and self._history_has_execute_build123d_face_revolve_recipe(history)
        )
        profile_shape_ok = any(profile.has_sloped_segment for profile in profiles)
        if not profile_shape_ok and execute_build123d_face_revolve:
            profile_shape_ok = True
        if "feature_revolve_profile_setup" in blockers:
            profile_shape_ok = False

        has_material_revolve = any(
            entry.action_type == CADActionType.REVOLVE
            and self._history_action_materially_changes_geometry(history, index)
            for index, entry in enumerate(history)
        )
        if execute_build123d_face_revolve:
            has_material_revolve = True
        cone_like_face_present = any(
            str(getattr(face, "geom_type", "")).strip().upper() == "CONE"
            for face in self._snapshot_feature_faces(snapshot)
        )
        result_shape_ok = (
            has_material_revolve
            and cone_like_face_present
            and "feature_revolve_profile_shape" not in blockers
        )
        return [
            RequirementCheck(
                check_id="feature_revolve_profile_setup",
                label="Revolve profile preserves the required sloped/tapered sketch geometry before execution",
                status=(
                    RequirementCheckStatus.PASS
                    if profile_shape_ok
                    else RequirementCheckStatus.FAIL
                ),
                blocking=True,
                evidence=(
                    f"profile_count={len(profiles)}, has_sloped_segment={profile_shape_ok}"
                    if profiles
                    else (
                        "execute_build123d_face_revolve_recipe=true"
                        if execute_build123d_face_revolve
                        else "no closed revolve profile window found"
                    )
                ),
            ),
            RequirementCheck(
                check_id="feature_revolve_profile_shape",
                label="Revolve result preserves the requested tapered/conical shape semantics",
                status=(
                    RequirementCheckStatus.PASS
                    if result_shape_ok
                    else RequirementCheckStatus.FAIL
                ),
                blocking=True,
                evidence=(
                    f"material_revolve={has_material_revolve}, cone_like_face_present={cone_like_face_present}"
                    + (
                        ", execute_build123d_face_revolve_recipe=true"
                        if execute_build123d_face_revolve
                        else ""
                    )
                ),
            ),
        ]

    def _expected_path_segment_types(
        self,
        requirement_text: str | None,
    ) -> list[str]:
        text = str(requirement_text or "").strip().lower()
        if not text:
            return []
        if "line-arc-line" in text or "line arc line" in text:
            return ["line", "tangent_arc", "line"]
        if "l-shaped" in text or "l shaped" in text or "elbow" in text or "bent pipe" in text:
            return ["line", "tangent_arc", "line"]
        if "arc" in text and "line" in text:
            return ["line", "tangent_arc", "line"]
        return []

    def _requirement_suggests_tapered_revolve(
        self,
        requirement_text: str | None,
        history: list[ActionHistoryEntry],
    ) -> bool:
        text = str(requirement_text or "").strip().lower()
        if not text:
            return False
        has_material_revolve = any(
            entry.action_type == CADActionType.REVOLVE
            and self._history_action_materially_changes_geometry(history, index)
            for index, entry in enumerate(history)
        )
        if not has_material_revolve:
            if "revolve" not in text and "rotational" not in text:
                return False
            if "countersink" in text or "hole wizard" in text:
                return False
        return any(
            token in text
            for token in (
                "taper",
                "tapered",
                "frustum",
                "conical",
                "cone",
                "inclined line",
                "small end diameter",
            )
        ) or (
            "angle between" in text and "vertical" in text
        )

    def _requirement_requires_hollow_sweep_profile(
        self,
        requirement_text: str | None,
    ) -> bool:
        text = str(requirement_text or "").strip().lower()
        return any(
            token in text
            for token in (
                "pipe",
                "tube",
                "hollow",
                "annular",
                "concentric",
                "ring profile",
            )
        )

    def _requirement_requires_bent_sweep_result(
        self,
        requirement_text: str | None,
    ) -> bool:
        text = str(requirement_text or "").strip().lower()
        return any(
            token in text
            for token in (
                "bent",
                "bend",
                "elbow",
                "l-shaped",
                "l shaped",
                "arc",
            )
        )

    def _segment_sequence_matches(
        self,
        observed: list[str],
        expected: list[str],
    ) -> bool:
        observed_tokens = [
            str(item).strip().lower()
            for item in observed
            if isinstance(item, str) and item.strip()
        ]
        expected_tokens = [
            str(item).strip().lower()
            for item in expected
            if isinstance(item, str) and item.strip()
        ]
        if not expected_tokens:
            return True
        if len(observed_tokens) < len(expected_tokens):
            return False
        return observed_tokens[: len(expected_tokens)] == expected_tokens

    def _history_has_path_attached_profile_frame(
        self,
        history: list[ActionHistoryEntry],
    ) -> bool:
        latest_path_index = max(
            (
                index
                for index, entry in enumerate(history)
                if entry.action_type == CADActionType.ADD_PATH
            ),
            default=-1,
        )
        if latest_path_index < 0:
            return False
        for entry in history[latest_path_index + 1 :]:
            if entry.action_type == CADActionType.SWEEP:
                break
            if entry.action_type != CADActionType.CREATE_SKETCH:
                continue
            params = entry.action_params if isinstance(entry.action_params, dict) else {}
            if isinstance(params.get("path_ref"), str) and params.get("path_ref").strip():
                return True
            if params.get("resolved_from_path_ref") is True:
                return True
            if str(params.get("frame_mode", "")).strip().lower() == "normal_to_path_tangent":
                return True
        return False

    def _history_has_secondary_profile_sketch_window(
        self,
        history: list[ActionHistoryEntry],
    ) -> bool:
        latest_path_index = max(
            (
                index
                for index, entry in enumerate(history)
                if entry.action_type == CADActionType.ADD_PATH
            ),
            default=-1,
        )
        if latest_path_index < 0:
            return False
        create_sketch_count = 0
        for entry in history[latest_path_index + 1 :]:
            if entry.action_type == CADActionType.SWEEP:
                break
            if entry.action_type == CADActionType.CREATE_SKETCH:
                create_sketch_count += 1
        return create_sketch_count >= 1

    def _build_dimension_checks(
        self,
        bbox: list[float],
        requirements: dict[str, object],
    ) -> list[RequirementCheck]:
        checks: list[RequirementCheck] = []
        dimensions = requirements.get("dimensions")
        if not isinstance(dimensions, dict):
            return checks

        observed_dims = [value for value in bbox if isinstance(value, (int, float))]
        if len(observed_dims) != 3:
            return checks

        for name, raw_target in dimensions.items():
            if not isinstance(name, str):
                continue
            if not isinstance(raw_target, (int, float)):
                continue
            target = float(raw_target)
            if target <= 0:
                continue

            tolerance = max(1.0, target * 0.1)
            matched_dim = next(
                (
                    value
                    for value in observed_dims
                    if abs(float(value) - target) <= tolerance
                ),
                None,
            )
            passed = matched_dim is not None
            evidence = (
                f"target={target}, matched={matched_dim}, bbox={observed_dims}"
                if passed
                else f"target={target}, bbox={observed_dims}, tolerance={tolerance:.2f}"
            )
            checks.append(
                RequirementCheck(
                    check_id=f"dimension_{name.lower()}",
                    label=f"Dimension '{name}' is satisfied",
                    status=(
                        RequirementCheckStatus.PASS
                        if passed
                        else RequirementCheckStatus.FAIL
                    ),
                    blocking=True,
                    evidence=evidence,
                )
            )
        return checks

    def _build_feature_checks(
        self,
        snapshot: CADStateSnapshot,
        history: list[ActionHistoryEntry],
        requirements: dict[str, object],
        requirement_text: str | None,
    ) -> list[RequirementCheck]:
        checks: list[RequirementCheck] = []
        semantics = analyze_requirement_semantics(
            requirements=(
                requirements if isinstance(requirements, dict) else None
            ),
            requirement_text=requirement_text,
        )
        action_types = {entry.action_type.value for entry in history}
        first_solid_index = self._find_first_solid_index(history)

        material_cut_revolve_indices = [
            index
            for index, entry in enumerate(history)
            if entry.action_type == CADActionType.REVOLVE
            and self._action_is_subtractive_edit(entry)
            and self._history_action_materially_changes_geometry(history, index)
        ]

        if (
            semantics.mentions_nested_profile_cutout
            or semantics.mentions_profile_region_frame
        ):
            has_explicit_inner_cut = self._history_has_post_solid_axial_inner_cut(
                history=history,
                first_solid_index=first_solid_index,
            )
            has_multi_profile_bootstrap = (
                first_solid_index is not None
                and self._count_profile_actions_between(
                    history, start_index=0, end_index=first_solid_index
                )
                >= 2
                and self._history_action_materially_changes_geometry(
                    history, first_solid_index
                )
            )
            prefer_explicit_inner_cut = (
                semantics.prefers_explicit_inner_void_cut
                and not semantics.mentions_profile_region_frame
            )
            snapshot_frame_fallback = False
            snapshot_frame_evidence = ""
            if (
                not has_explicit_inner_cut
                and not has_multi_profile_bootstrap
                and history
                and self._history_starts_from_execute_build123d_snapshot(history)
            ):
                (
                    snapshot_frame_fallback,
                    snapshot_frame_evidence,
                ) = self._snapshot_has_frame_like_inner_void(
                    snapshot=history[-1].result_snapshot,
                    requirement_text=requirement_text,
                )
                if not snapshot_frame_fallback:
                    (
                        snapshot_frame_fallback,
                        snapshot_frame_evidence,
                    ) = self._snapshot_has_mixed_nested_inner_void(
                        snapshot=history[-1].result_snapshot,
                        requirement_text=requirement_text,
                    )
            inner_void_cutout_passed = has_explicit_inner_cut or (
                has_multi_profile_bootstrap and not prefer_explicit_inner_cut
            ) or snapshot_frame_fallback
            checks.append(
                RequirementCheck(
                    check_id="feature_inner_void_cutout",
                    label="Nested/frame profile intent is realized by an explicit inner cut stage or a reliable same-sketch frame bootstrap",
                    status=(
                        RequirementCheckStatus.PASS
                        if inner_void_cutout_passed
                        else RequirementCheckStatus.FAIL
                    ),
                    blocking=True,
                    evidence=(
                        "axial_end_face_inner_cut"
                        if has_explicit_inner_cut
                        else (
                            "multi_profile_first_solid"
                            if has_multi_profile_bootstrap and not prefer_explicit_inner_cut
                            else (
                                snapshot_frame_evidence
                                if snapshot_frame_fallback
                                else (
                                    "mixed_shape_nested_void_requires_explicit_inner_cut_stage"
                                    if prefer_explicit_inner_cut and has_multi_profile_bootstrap
                                    else "no axial end-face inner cut stage or reliable multi-profile bootstrap found"
                                )
                            )
                        )
                    ),
                )
            )

        triangle_frame_spec = self._extract_equilateral_triangle_frame_side_lengths(
            requirement_text
        )
        if triangle_frame_spec is not None and history:
            triangle_scale_ok, triangle_scale_evidence = (
                self._snapshot_has_equilateral_triangle_frame_scale(
                    snapshot=history[-1].result_snapshot,
                    outer_side=triangle_frame_spec[0],
                    inner_side=triangle_frame_spec[1],
                )
            )
            checks.append(
                RequirementCheck(
                    check_id="feature_regular_polygon_scale_alignment",
                    label="Explicit regular-polygon side lengths remain scale-correct in the final geometry",
                    status=(
                        RequirementCheckStatus.PASS
                        if triangle_scale_ok
                        else RequirementCheckStatus.FAIL
                    ),
                    blocking=True,
                    evidence=(
                        triangle_scale_evidence
                        or (
                            "triangle_frame_area_match=false, "
                            f"outer_side={triangle_frame_spec[0]:.4f}, "
                            f"inner_side={triangle_frame_spec[1]:.4f}"
                        )
                    ),
                )
            )

        if semantics.mentions_multi_plane_additive_union:
            additive_planes = self._collect_additive_feature_planes(history)
            additive_signatures = self._collect_additive_feature_span_signatures(
                history
            )
            explicit_plane_specs = self._extract_multi_plane_additive_specs(
                requirement_text
            )
            snapshot_multi_plane_match = False
            if (
                explicit_plane_specs
                and history
                and self._history_starts_from_execute_build123d_snapshot(history)
            ):
                snapshot_multi_plane_match = (
                    self._snapshot_matches_multi_plane_additive_specs(
                        snapshot=history[-1].result_snapshot,
                        plane_specs=explicit_plane_specs,
                    )
                )
                if snapshot_multi_plane_match:
                    additive_planes = {
                        self._normalize_sketch_plane_name(str(spec.get("plane")))
                        for spec in explicit_plane_specs
                        if self._normalize_sketch_plane_name(str(spec.get("plane")))
                        in {"XY", "XZ", "YZ"}
                    }
                    additive_signatures = {
                        self._build_plane_extrude_signature(
                            plane=str(spec["plane"]),
                            width=float(spec["width"]),
                            height=float(spec["height"]),
                            extrusion_span=float(spec["distance"]),
                        )
                        for spec in explicit_plane_specs
                        if all(
                            isinstance(spec.get(key), (int, float))
                            for key in ("width", "height", "distance")
                        )
                        and isinstance(spec.get("plane"), str)
                    }
            required_planes = {
                self._normalize_sketch_plane_name(item)
                for item in semantics.datum_planes
                if self._normalize_sketch_plane_name(item) in {"XY", "XZ", "YZ"}
            }
            required_signature_options = (
                semantics.multi_plane_additive_signature_options
            )
            covered_signature_segments = sum(
                1
                for options in required_signature_options
                if any(option in additive_signatures for option in options)
            )
            required_plane_count = self._required_multi_plane_union_plane_count(
                semantics
            )
            final_solids = history[-1].result_snapshot.geometry.solids if history else 0
            has_required_plane_coverage = (
                required_planes.issubset(additive_planes)
                if required_planes
                else len(additive_planes) >= required_plane_count
            )
            has_required_signature_coverage = (
                covered_signature_segments >= len(required_signature_options)
                if required_signature_options and not required_planes
                else True
            )
            has_multi_plane_union = (
                final_solids == 1
                and has_required_plane_coverage
                and has_required_signature_coverage
            )
            has_explicit_plane_specs = self._history_matches_multi_plane_additive_specs(
                history=history,
                plane_specs=explicit_plane_specs,
                semantics=semantics,
            )
            if snapshot_multi_plane_match:
                has_multi_plane_union = final_solids == 1
                has_explicit_plane_specs = True
            checks.append(
                RequirementCheck(
                    check_id="feature_multi_plane_additive_union",
                    label="Orthogonal additive union contributes the required unique additive span signatures and remains one merged solid",
                    status=(
                        RequirementCheckStatus.PASS
                        if has_multi_plane_union
                        else RequirementCheckStatus.FAIL
                    ),
                    blocking=True,
                    evidence=(
                        "additive_planes="
                        f"{sorted(additive_planes)}, "
                        f"required_planes_explicit={sorted(required_planes)}, "
                        f"signatures={sorted(additive_signatures)}, "
                        f"required_signature_segments={len(required_signature_options)}, "
                        f"covered_signature_segments={covered_signature_segments}, "
                        f"required_planes={required_plane_count}, "
                        f"final_solids={final_solids}"
                        + (
                            ", execute_build123d_geometry_fallback=true"
                            if snapshot_multi_plane_match
                            else ""
                        )
                    ),
                )
            )
            if explicit_plane_specs:
                checks.append(
                    RequirementCheck(
                        check_id="feature_multi_plane_additive_specs",
                        label="Each named datum-plane additive window preserves the requested rectangle dimension order and extrusion distance",
                        status=(
                            RequirementCheckStatus.PASS
                            if has_explicit_plane_specs
                            else RequirementCheckStatus.FAIL
                        ),
                        blocking=True,
                        evidence=(
                            (
                                f"matched_plane_specs={explicit_plane_specs}, "
                                "execute_build123d_geometry_fallback=true"
                            )
                            if has_explicit_plane_specs
                            else f"missing or swapped plane-specific additive specs: expected={explicit_plane_specs}"
                        ),
                    )
                )

        if semantics.mentions_revolved_groove_cut:
            has_local_groove_setup = any(
                self._history_has_local_revolve_cut_setup(
                    history=history,
                    revolve_index=index,
                    first_solid_index=first_solid_index,
                )
                for index in material_cut_revolve_indices
            )
            has_requirement_aligned_groove_window = (
                self._history_has_requirement_aligned_revolved_groove_window(
                    history=history,
                    revolve_indices=material_cut_revolve_indices,
                    first_solid_index=first_solid_index,
                    requirement_text=requirement_text,
                )
            )
            has_semantic_groove_result, groove_result_evidence = (
                self._history_has_semantically_valid_revolved_groove_result(
                    history=history,
                    revolve_indices=material_cut_revolve_indices,
                )
            )
            snapshot_groove_fallback = False
            snapshot_groove_evidence = ""
            if (
                not material_cut_revolve_indices
                and history
                and self._history_starts_from_execute_build123d_snapshot(history)
            ):
                snapshot_groove_fallback, snapshot_groove_evidence = (
                    self._snapshot_has_requirement_aligned_axisymmetric_groove(
                        snapshot=history[-1].result_snapshot,
                        requirement_text=requirement_text,
                    )
                )
            checks.append(
                RequirementCheck(
                    check_id="feature_annular_groove",
                    label="Annular groove is created with a subtractive revolve that materially changes geometry",
                    status=(
                        RequirementCheckStatus.PASS
                        if material_cut_revolve_indices or snapshot_groove_fallback
                        else RequirementCheckStatus.FAIL
                    ),
                    blocking=True,
                    evidence=(
                        f"material_cut_revolve_steps={[history[index].step for index in material_cut_revolve_indices]}"
                        if material_cut_revolve_indices
                        else (
                            snapshot_groove_evidence
                            if snapshot_groove_fallback
                            else "requirement mentions annular groove / revolved cut but no material subtractive revolve action was found"
                        )
                    ),
                )
            )
            checks.append(
                RequirementCheck(
                    check_id="feature_revolved_groove_setup",
                    label="Groove revolve uses a post-solid local sketch window on an axis-containing plane",
                    status=(
                        RequirementCheckStatus.PASS
                        if has_local_groove_setup or snapshot_groove_fallback
                        else RequirementCheckStatus.FAIL
                    ),
                    blocking=True,
                    evidence=(
                        "found axis-plane groove sketch window before subtractive revolve"
                        if has_local_groove_setup
                        else (
                            snapshot_groove_evidence
                            if snapshot_groove_fallback
                            else "missing post-solid local groove sketch window with profile actions before revolve cut"
                        )
                    ),
                )
            )
            checks.append(
                RequirementCheck(
                    check_id="feature_revolved_groove_alignment",
                    label="Groove revolve window matches the requested profile dimensions and axial placement",
                    status=(
                        RequirementCheckStatus.PASS
                        if has_requirement_aligned_groove_window or snapshot_groove_fallback
                        else RequirementCheckStatus.FAIL
                    ),
                    blocking=True,
                    evidence=(
                        "found groove sketch window with requirement-aligned rectangle dims and axial placement"
                        if has_requirement_aligned_groove_window
                        else (
                            snapshot_groove_evidence
                            if snapshot_groove_fallback
                            else "missing requirement-aligned groove rectangle dims or axial placement before revolve cut"
                        )
                    ),
                )
            )
            checks.append(
                RequirementCheck(
                    check_id="feature_revolved_groove_result",
                    label="Groove revolve preserves the outer envelope while creating a local rotational cut result",
                    status=(
                        RequirementCheckStatus.PASS
                        if has_semantic_groove_result or snapshot_groove_fallback
                        else RequirementCheckStatus.FAIL
                    ),
                    blocking=True,
                    evidence=(
                        groove_result_evidence
                        if has_semantic_groove_result
                        else (
                            snapshot_groove_evidence
                            if snapshot_groove_fallback
                            else groove_result_evidence
                        )
                    ),
                )
            )

        if first_solid_index is not None:
            plane_trim_ok, plane_trim_evidence = (
                self._history_has_requirement_plane_trim(
                    history=history,
                    first_solid_index=first_solid_index,
                    requirement_text=requirement_text,
                )
            )
            if plane_trim_evidence:
                checks.append(
                    RequirementCheck(
                        check_id="feature_plane_trim",
                        label="Datum-plane frustum / truncate intent is realized by a requirement-aligned plane trim stage",
                        status=(
                            RequirementCheckStatus.PASS
                            if plane_trim_ok
                            else RequirementCheckStatus.FAIL
                        ),
                        blocking=True,
                        evidence=plane_trim_evidence,
                    )
                )

        if semantics.mentions_face_edit:
            expects_subtractive = semantics.mentions_subtractive_edit and not (
                semantics.mentions_additive_face_feature
            )
            expects_additive = semantics.mentions_additive_face_feature and not (
                semantics.mentions_subtractive_edit
            )
            snapshot_target_face_edit = False
            equivalent_full_span_channel_profile = False
            equivalent_full_span_channel_evidence = ""
            has_target_face_edit = self._history_has_post_solid_face_feature(
                history=history,
                first_solid_index=first_solid_index,
                face_targets=semantics.face_targets,
                expect_subtractive=expects_subtractive,
                expect_additive=expects_additive,
            )
            if (
                not has_target_face_edit
                and self._history_starts_from_execute_build123d_snapshot(history)
            ):
                snapshot_target_face_edit = self._snapshot_has_target_face_feature(
                    snapshot=history[-1].result_snapshot,
                    face_targets=semantics.face_targets,
                    expect_subtractive=expects_subtractive,
                    expect_additive=expects_additive,
                    include_aligned_host_faces=expects_additive,
                )
                has_target_face_edit = snapshot_target_face_edit
            if (
                not has_target_face_edit
                and expects_subtractive
                and semantics.mentions_notch_like
                and self._requirement_allows_full_span_channel_profile_equivalence(
                    requirement_text
                )
                and self._history_starts_from_execute_build123d_snapshot(history)
            ):
                equivalent_full_span_channel_profile, equivalent_full_span_channel_evidence = (
                    self._snapshot_has_requirement_aligned_notch_geometry(
                        snapshot=history[-1].result_snapshot,
                        requirement_text=requirement_text,
                    )
                )
                has_target_face_edit = equivalent_full_span_channel_profile
            checks.append(
                RequirementCheck(
                    check_id="feature_target_face_edit",
                    label="Target-face edit uses either a post-solid sketch window or a direct face-targeted feature action on the requested face",
                    status=(
                        RequirementCheckStatus.PASS
                        if has_target_face_edit
                        else RequirementCheckStatus.FAIL
                    ),
                    blocking=True,
                    evidence=(
                        (
                            f"face_targets={list(semantics.face_targets)}, execute_build123d_geometry_fallback=true"
                            if snapshot_target_face_edit
                            else (
                                "equivalent_full_span_channel_profile "
                                f"execute_build123d_geometry_fallback=true, {equivalent_full_span_channel_evidence}"
                                if equivalent_full_span_channel_profile
                                else f"face_targets={list(semantics.face_targets)}"
                            )
                        )
                        if has_target_face_edit
                        else f"no face-targeted edit window or direct face-targeted feature matched face_targets={list(semantics.face_targets)}"
                    ),
                )
            )
            requires_subtractive_merge = expects_subtractive or bool(
                getattr(semantics, "mentions_hole", False)
                or getattr(semantics, "mentions_spherical_recess", False)
            )
            if requires_subtractive_merge:
                snapshot_target_face_subtractive = False
                equivalent_full_span_channel_merge = False
                has_merged_target_face_subtractive = (
                    self._history_has_merged_subtractive_face_feature(
                        history=history,
                        first_solid_index=first_solid_index,
                        face_targets=semantics.face_targets,
                    )
                )
                if (
                    not has_merged_target_face_subtractive
                    and self._history_starts_from_execute_build123d_snapshot(history)
                ):
                    snapshot_target_face_subtractive = (
                        self._snapshot_has_merged_subtractive_face_feature(
                            snapshot=history[-1].result_snapshot,
                            face_targets=semantics.face_targets,
                        )
                    )
                    has_merged_target_face_subtractive = (
                        snapshot_target_face_subtractive
                    )
                if (
                    not has_merged_target_face_subtractive
                    and equivalent_full_span_channel_profile
                    and self._history_starts_from_execute_build123d_snapshot(history)
                    and int(history[-1].result_snapshot.geometry.solids) == 1
                ):
                    equivalent_full_span_channel_merge = True
                    has_merged_target_face_subtractive = True
                checks.append(
                    RequirementCheck(
                        check_id="feature_target_face_subtractive_merge",
                        label="Target-face subtractive features remain merged into the host solid instead of becoming detached fragments",
                        status=(
                            RequirementCheckStatus.PASS
                            if has_merged_target_face_subtractive
                            else RequirementCheckStatus.FAIL
                        ),
                        blocking=True,
                        evidence=(
                            (
                                f"face_targets={list(semantics.face_targets)} merged_subtractive_feature execute_build123d_geometry_fallback=true"
                                if snapshot_target_face_subtractive
                                else (
                                    "equivalent_full_span_channel_profile merged_subtractive_feature "
                                    f"execute_build123d_geometry_fallback=true, {equivalent_full_span_channel_evidence}"
                                    if equivalent_full_span_channel_merge
                                    else f"face_targets={list(semantics.face_targets)} merged_subtractive_feature"
                                )
                            )
                            if has_merged_target_face_subtractive
                            else "post-solid subtractive feature did not stay merged with the target solid"
                        ),
                    )
                )
            if semantics.mentions_additive_face_feature:
                snapshot_target_face_additive = False
                has_merged_target_face_feature = (
                    self._history_has_merged_additive_face_feature(
                        history=history,
                        first_solid_index=first_solid_index,
                        face_targets=semantics.face_targets,
                    )
                )
                if (
                    not has_merged_target_face_feature
                    and self._history_starts_from_execute_build123d_snapshot(history)
                ):
                    snapshot_target_face_additive = (
                        self._snapshot_has_target_face_feature(
                            snapshot=history[-1].result_snapshot,
                            face_targets=semantics.face_targets,
                            expect_subtractive=False,
                            expect_additive=True,
                            include_aligned_host_faces=True,
                        )
                        and int(history[-1].result_snapshot.geometry.solids) == 1
                    )
                    has_merged_target_face_feature = snapshot_target_face_additive
                checks.append(
                    RequirementCheck(
                        check_id="feature_target_face_additive_merge",
                        label="Target-face additive features remain merged into the existing solid",
                        status=(
                            RequirementCheckStatus.PASS
                            if has_merged_target_face_feature
                            else RequirementCheckStatus.FAIL
                        ),
                        blocking=True,
                        evidence=(
                            (
                                f"face_targets={list(semantics.face_targets)} merged_additive_feature execute_build123d_geometry_fallback=true"
                                if snapshot_target_face_additive
                                else f"face_targets={list(semantics.face_targets)} merged_additive_feature"
                            )
                            if has_merged_target_face_feature
                            else "post-solid additive feature did not stay merged with the target solid"
                        ),
                    )
                )

        if semantics.mentions_notch_like:
            has_profile_notch_bootstrap = (
                first_solid_index is not None
                and (
                    self._count_profile_actions_between(
                        history, start_index=0, end_index=first_solid_index
                    )
                    >= 2
                    or self._history_has_complex_profile_bootstrap(
                        history,
                        start_index=0,
                        end_index=first_solid_index,
                    )
                )
                and self._history_action_materially_changes_geometry(
                    history, first_solid_index
                )
            )
            has_post_solid_notch_cut = any(
                self._history_has_local_subtractive_window(
                    history=history,
                    action_index=index,
                    first_solid_index=first_solid_index,
                )
                for index, entry in enumerate(history)
                if self._action_is_subtractive_edit(entry)
                and self._history_action_materially_changes_geometry(history, index)
            )
            notch_alignment_ok, notch_alignment_evidence = (
                self._history_has_requirement_aligned_notch_window(
                    history=history,
                    first_solid_index=first_solid_index,
                    requirement_text=requirement_text,
                    has_profile_notch_bootstrap=has_profile_notch_bootstrap,
                )
            )
            snapshot_notch_feature = False
            snapshot_notch_evidence = ""
            snapshot_notch_alignment = False
            snapshot_notch_alignment_evidence = ""
            if (
                not has_profile_notch_bootstrap
                and not has_post_solid_notch_cut
                and self._history_starts_from_execute_build123d_snapshot(history)
            ):
                snapshot_notch_feature, snapshot_notch_evidence = (
                    self._snapshot_has_requirement_aligned_notch_feature(
                        snapshot=history[-1].result_snapshot,
                        requirement_text=requirement_text,
                        face_targets=semantics.face_targets,
                    )
                )
            if (
                not notch_alignment_ok
                and self._history_starts_from_execute_build123d_snapshot(history)
            ):
                snapshot_notch_alignment, snapshot_notch_alignment_evidence = (
                    self._snapshot_has_requirement_aligned_notch_geometry(
                        snapshot=history[-1].result_snapshot,
                        requirement_text=requirement_text,
                    )
                )
                if snapshot_notch_alignment:
                    notch_alignment_ok = True
                    notch_alignment_evidence = snapshot_notch_alignment_evidence
            checks.append(
                RequirementCheck(
                    check_id="feature_notch_or_profile_cut",
                    label="Notch/U-shape/slot intent is realized by either a complex base profile or a local subtractive edit",
                    status=(
                        RequirementCheckStatus.PASS
                        if (
                            has_profile_notch_bootstrap
                            or has_post_solid_notch_cut
                            or snapshot_notch_feature
                        )
                        else RequirementCheckStatus.FAIL
                    ),
                    blocking=True,
                    evidence=(
                        "complex_profile_bootstrap"
                        if has_profile_notch_bootstrap
                        else (
                            "post_solid_local_subtractive_edit"
                            if has_post_solid_notch_cut
                            else (
                                snapshot_notch_evidence
                                if snapshot_notch_feature and snapshot_notch_evidence
                                else "no complex base profile or local subtractive notch window found"
                            )
                        )
                    ),
                )
            )
            if notch_alignment_evidence:
                checks.append(
                    RequirementCheck(
                        check_id="feature_notch_profile_alignment",
                        label="Notch/U-shape profile stays aligned with the requirement's cross-section plane and profile dimensions",
                        status=(
                            RequirementCheckStatus.PASS
                            if notch_alignment_ok
                            else RequirementCheckStatus.FAIL
                        ),
                        blocking=True,
                        evidence=notch_alignment_evidence,
                    )
                )

        if semantics.mentions_hole:
            allow_spherical_recess_as_hole = bool(
                getattr(semantics, "mentions_spherical_recess", False)
            )
            snapshot_hole_feature = False
            has_hole_feature = any(
                (
                    entry.action_type == CADActionType.HOLE
                    or (
                        allow_spherical_recess_as_hole
                        and entry.action_type == CADActionType.SPHERE_RECESS
                    )
                    or (
                        entry.action_type == CADActionType.CUT_EXTRUDE
                        and self._history_window_has_profile_actions(
                            history,
                            sketch_index=self._find_preceding_sketch_index(
                                history, index
                            ),
                            action_index=index,
                            allowed_action_types={CADActionType.ADD_CIRCLE},
                        )
                    )
                )
                and self._history_action_materially_changes_geometry(history, index)
                for index, entry in enumerate(history)
            )
            if (
                not has_hole_feature
                and self._history_starts_from_execute_build123d_snapshot(history)
            ):
                snapshot_hole_feature = self._snapshot_has_hole_like_feature(
                    snapshot=history[-1].result_snapshot,
                    face_targets=semantics.face_targets,
                )
                has_hole_feature = snapshot_hole_feature
            checks.append(
                RequirementCheck(
                    check_id="feature_hole",
                    label="Contains required hole feature",
                    status=(
                        RequirementCheckStatus.PASS
                        if has_hole_feature
                        else RequirementCheckStatus.FAIL
                    ),
                    blocking=True,
                    evidence=(
                        (
                            "found hole/recess-like subtractive geometry in final snapshot execute_build123d_geometry_fallback=true"
                            if snapshot_hole_feature
                            else "found material hole/recess action"
                        )
                        if has_hole_feature
                        else "no material hole/recess action or circle-based cut window found"
                    ),
                )
            )
            if getattr(semantics, "mentions_countersink", False):
                has_countersink_feature = any(
                    entry.action_type == CADActionType.HOLE
                    and isinstance(entry.action_params, dict)
                    and isinstance(
                        (
                            normalized_hole_params := self._normalize_hole_action_params(
                                entry.action_params
                            )
                        ).get("countersink_diameter"),
                        (int, float),
                    )
                    and float(normalized_hole_params.get("countersink_diameter")) > 0.0
                    and self._history_action_materially_changes_geometry(history, index)
                    for index, entry in enumerate(history)
                )
                snapshot_countersink_feature = False
                if (
                    not has_countersink_feature
                    and self._history_starts_from_execute_build123d_snapshot(history)
                ):
                    snapshot_countersink_feature = (
                        self._snapshot_has_countersink_cone_feature(
                            snapshot=history[-1].result_snapshot,
                            face_targets=semantics.face_targets,
                        )
                    )
                    has_countersink_feature = snapshot_countersink_feature
                cone_like_face_present = any(
                    str(getattr(face, "geom_type", "")).strip().upper() == "CONE"
                    for face in self._snapshot_feature_faces(snapshot)
                )
                checks.append(
                    RequirementCheck(
                        check_id="feature_countersink",
                        label="Hole feature preserves the required countersink/conical head geometry",
                        status=(
                            RequirementCheckStatus.PASS
                            if (
                                has_hole_feature
                                and has_countersink_feature
                                and cone_like_face_present
                            )
                            else RequirementCheckStatus.FAIL
                        ),
                        blocking=True,
                        evidence=(
                            "countersink_action="
                            f"{has_countersink_feature and not snapshot_countersink_feature}, "
                            f"snapshot_countersink_geometry={snapshot_countersink_feature}, "
                            f"hole_feature={has_hole_feature}, "
                            f"cone_like_face_present={cone_like_face_present}"
                        ),
                    )
                )
            explicit_hole_centers = self._extract_explicit_hole_centers_from_requirement(
                requirement_text
            )
            if explicit_hole_centers:
                realized_hole_centers = self._collect_history_hole_centers(history)
                if (
                    not realized_hole_centers
                    and self._history_starts_from_execute_build123d_snapshot(history)
                ):
                    realized_hole_centers = self._snapshot_collect_subtractive_feature_centers(
                        snapshot=history[-1].result_snapshot,
                        face_targets=semantics.face_targets,
                    )
                realized_hole_centers = self._filter_extra_center_feature_for_requirement(
                    requirement_text,
                    realized_hole_centers,
                    explicit_hole_centers,
                )
                hole_position_alignment = self._center_sets_match_with_requirement_coordinate_modes(
                    realized_hole_centers,
                    explicit_hole_centers,
                    requirement_text=requirement_text,
                )
                checks.append(
                    RequirementCheck(
                        check_id="feature_hole_position_alignment",
                        label="Explicit hole centers stay aligned with the requested local coordinates",
                        status=(
                            RequirementCheckStatus.PASS
                            if hole_position_alignment
                            else RequirementCheckStatus.FAIL
                        ),
                        blocking=True,
                        evidence=(
                            f"required_centers={explicit_hole_centers}, realized_centers={realized_hole_centers}"
                        ),
                    )
                )
                if (
                    not semantics.mentions_pattern
                    and history
                    and history[-1].result_snapshot is not None
                ):
                    actual_snapshot_hole_centers = self._snapshot_collect_subtractive_feature_centers(
                        snapshot=history[-1].result_snapshot,
                        face_targets=semantics.face_targets,
                    )
                    actual_snapshot_hole_centers = (
                        self._filter_extra_center_feature_for_requirement(
                            requirement_text,
                            actual_snapshot_hole_centers,
                            explicit_hole_centers,
                        )
                    )
                    if actual_snapshot_hole_centers:
                        checks.append(
                            RequirementCheck(
                                check_id="feature_hole_exact_center_set",
                                label="Explicit hole-center requirements do not leave extra subtractive hole centers on the target face",
                                status=(
                                    RequirementCheckStatus.PASS
                                    if self._center_sets_match_with_requirement_coordinate_modes(
                                        actual_snapshot_hole_centers,
                                        explicit_hole_centers,
                                        requirement_text=requirement_text,
                                    )
                                    else RequirementCheckStatus.FAIL
                                ),
                                blocking=True,
                                evidence=(
                                    "required_centers="
                                    f"{explicit_hole_centers}, actual_snapshot_centers={actual_snapshot_hole_centers}"
                                ),
                            )
                        )

        expected_local_feature_centers = self._infer_expected_local_feature_centers(
            requirement_text
        )
        expected_local_feature_count = (
            len(expected_local_feature_centers)
            if expected_local_feature_centers
            else self._infer_expected_local_feature_count(
                requirement_text,
                family="explicit_anchor_hole"
                if (
                    semantics.mentions_hole
                    or semantics.mentions_pattern
                    or getattr(semantics, "mentions_spherical_recess", False)
                )
                else None,
            )
        )
        if (expected_local_feature_centers or expected_local_feature_count is not None) and (
            semantics.mentions_hole
            or semantics.mentions_pattern
            or getattr(semantics, "mentions_spherical_recess", False)
        ):
            realized_feature_centers = self._collect_history_hole_centers(history)
            if (
                not realized_feature_centers
                and self._history_starts_from_execute_build123d_snapshot(history)
            ):
                realized_feature_centers = self._snapshot_collect_subtractive_feature_centers(
                    snapshot=history[-1].result_snapshot,
                    face_targets=semantics.face_targets,
                )
            if (
                not realized_feature_centers
                and semantics.mentions_pattern
                and self._history_starts_from_execute_build123d_snapshot(history)
            ):
                realized_feature_centers = [
                    [float(center[0]), float(center[1])]
                    for center in self._snapshot_collect_round_feature_centers(
                        history[-1].result_snapshot
                    )
                    if len(center) >= 2
                ]
            if expected_local_feature_centers:
                realized_feature_centers = self._filter_extra_center_feature_for_requirement(
                    requirement_text,
                    realized_feature_centers,
                    expected_local_feature_centers,
                )
                allow_centered_translation = (
                    self._requirement_uses_centered_pattern_local_coordinates(requirement_text)
                )
                local_anchor_alignment = self._center_sets_match_with_requirement_coordinate_modes(
                    realized_feature_centers,
                    expected_local_feature_centers,
                    requirement_text=requirement_text,
                    allow_translation=allow_centered_translation,
                )
                checks.append(
                    RequirementCheck(
                        check_id="feature_local_anchor_alignment",
                        label="Direct feature centers stay aligned with the requested local anchor layout",
                        status=(
                            RequirementCheckStatus.PASS
                            if local_anchor_alignment
                            else RequirementCheckStatus.FAIL
                        ),
                        blocking=True,
                        evidence=(
                            f"required_centers={expected_local_feature_centers}, realized_centers={realized_feature_centers}"
                        ),
                    )
                )
            elif expected_local_feature_count is not None:
                checks.append(
                    RequirementCheck(
                        check_id="feature_local_anchor_count_alignment",
                        label="Direct feature count stays aligned with the requested local anchor count",
                        status=(
                            RequirementCheckStatus.PASS
                            if len(realized_feature_centers) == expected_local_feature_count
                            else RequirementCheckStatus.FAIL
                        ),
                        blocking=True,
                        evidence=(
                            f"required_center_count={expected_local_feature_count}, "
                            f"realized_center_count={len(realized_feature_centers)}, "
                            f"realized_centers={realized_feature_centers}"
                        ),
                    )
                )

        if (
            history
            and self._history_starts_from_execute_build123d_snapshot(history)
            and getattr(semantics, "mentions_spherical_recess", False)
            and self._requirement_requires_host_plane_open_spherical_recess(
                requirement_text
            )
        ):
            opening_centers = self._snapshot_collect_host_plane_circle_edge_centers(
                snapshot=history[-1].result_snapshot,
                face_targets=semantics.face_targets,
            )
            allow_centered_translation = (
                self._requirement_uses_centered_pattern_local_coordinates(
                    requirement_text
                )
            )
            openings_match = bool(opening_centers)
            if expected_local_feature_centers:
                openings_match = self._center_sets_match_with_requirement_coordinate_modes(
                    opening_centers,
                    expected_local_feature_centers,
                    requirement_text=requirement_text,
                    allow_translation=allow_centered_translation,
                )
            checks.append(
                RequirementCheck(
                    check_id="feature_spherical_recess_host_plane_opening",
                    label="Spherical recesses whose diameter edge lies on the host face expose matching circular openings on that host plane",
                    status=(
                        RequirementCheckStatus.PASS
                        if openings_match
                        else RequirementCheckStatus.FAIL
                    ),
                    blocking=True,
                    evidence=(
                        f"required_opening_centers={expected_local_feature_centers or []}, "
                        f"realized_host_plane_circle_centers={opening_centers}"
                    ),
                )
            )

        explicit_profile_shapes = self._extract_required_profile_shape_tokens(
            requirement_text,
            post_solid_only=True,
        )
        explicit_pre_solid_shapes = ()
        if not (
            semantics.mentions_nested_profile_cutout
            or semantics.mentions_profile_region_frame
        ):
            explicit_pre_solid_shapes = tuple(
                shape
                for shape in self._extract_required_profile_shape_tokens(
                    requirement_text,
                    pre_solid_only=True,
                )
                if shape in {"rectangle", "square", "triangle", "hexagon", "polygon"}
            )
        if explicit_pre_solid_shapes:
            pre_solid_shape_ok, observed_pre_solid_shapes = (
                self._history_has_pre_solid_matching_profile_shape(
                    history=history,
                    first_solid_index=first_solid_index,
                    targets=explicit_pre_solid_shapes,
                )
            )
            if (
                not pre_solid_shape_ok
                and self._history_starts_from_execute_build123d_snapshot(history)
            ):
                pre_solid_shape_ok, observed_pre_solid_shapes = (
                    self._snapshot_has_matching_profile_shape(
                        snapshot=(
                            history[first_solid_index].result_snapshot
                            if isinstance(first_solid_index, int)
                            and 0 <= first_solid_index < len(history)
                            else history[-1].result_snapshot
                        ),
                        targets=explicit_pre_solid_shapes,
                    )
                )
            observed_pre_solid_summary = (
                observed_pre_solid_shapes
                if observed_pre_solid_shapes
                else ["<none>"]
            )
            checks.append(
                RequirementCheck(
                    check_id="pre_solid_profile_shape_alignment",
                    label="Explicit primitive sketch shapes named before the first solid are present in the pre-solid sketch window",
                    status=(
                        RequirementCheckStatus.PASS
                        if pre_solid_shape_ok
                        else RequirementCheckStatus.FAIL
                    ),
                    blocking=True,
                    evidence=(
                        f"required_pre_solid_shapes={list(explicit_pre_solid_shapes)}, "
                        f"observed_pre_solid_shapes={observed_pre_solid_summary}"
                    ),
                )
            )
        if explicit_profile_shapes and first_solid_index is not None:
            profile_shape_ok, observed_profile_shapes = (
                self._history_has_post_solid_matching_profile_shape(
                    history=history,
                    first_solid_index=first_solid_index,
                    targets=explicit_profile_shapes,
                )
            )
            sweep_equivalent_shape_ok = False
            sweep_equivalent_shapes: list[str] = []
            if not profile_shape_ok:
                sweep_equivalent_shape_ok, sweep_equivalent_shapes = (
                    self._history_has_sweep_equivalent_profile_shape(
                        history=history,
                        first_solid_index=first_solid_index,
                        targets=explicit_profile_shapes,
                    )
                )
            direct_feature_shape_ok = False
            direct_feature_shapes: list[str] = []
            if not profile_shape_ok and not sweep_equivalent_shape_ok:
                direct_feature_shape_ok, direct_feature_shapes = (
                    self._history_has_direct_feature_matching_profile_shape(
                        history=history,
                        first_solid_index=first_solid_index,
                        targets=explicit_profile_shapes,
                    )
                )
            spherical_recess_shape_ok = False
            spherical_recess_shapes: list[str] = []
            if (
                not profile_shape_ok
                and not sweep_equivalent_shape_ok
                and not direct_feature_shape_ok
                and self._history_starts_from_execute_build123d_snapshot(history)
                and getattr(semantics, "mentions_spherical_recess", False)
            ):
                spherical_recess_shape_ok = (
                    self._snapshot_has_spherical_recess_circle_equivalent(
                        snapshot=history[-1].result_snapshot,
                        face_targets=semantics.face_targets,
                    )
                )
                if spherical_recess_shape_ok:
                    spherical_recess_shapes = ["circle"]
            snapshot_shape_ok = False
            snapshot_shapes: list[str] = []
            if (
                not profile_shape_ok
                and not sweep_equivalent_shape_ok
                and not direct_feature_shape_ok
                and not spherical_recess_shape_ok
                and self._history_starts_from_execute_build123d_snapshot(history)
            ):
                snapshot_shape_ok, snapshot_shapes = (
                    self._snapshot_has_matching_profile_shape(
                        snapshot=history[-1].result_snapshot,
                        targets=explicit_profile_shapes,
                    )
                )
            snapshot_requirement_shape_ok = False
            snapshot_requirement_shapes: list[str] = []
            snapshot_requirement_shape_evidence = ""
            if (
                not profile_shape_ok
                and not sweep_equivalent_shape_ok
                and not direct_feature_shape_ok
                and not spherical_recess_shape_ok
                and not snapshot_shape_ok
                and self._history_starts_from_execute_build123d_snapshot(history)
            ):
                (
                    snapshot_requirement_shape_ok,
                    snapshot_requirement_shapes,
                    snapshot_requirement_shape_evidence,
                ) = self._snapshot_has_requirement_aligned_profile_shape_fallback(
                    snapshot=history[-1].result_snapshot,
                    requirement_text=requirement_text,
                    targets=explicit_profile_shapes,
                )
            observed_summary = (
                observed_profile_shapes if observed_profile_shapes else ["<none>"]
            )
            evidence = (
                f"required_shapes={list(explicit_profile_shapes)}, "
                f"observed_post_solid_shapes={observed_summary}"
            )
            if not observed_profile_shapes and not sweep_equivalent_shapes:
                evidence += ", missing_post_solid_profile_window=true"
            if sweep_equivalent_shapes:
                evidence += (
                    f", observed_sweep_profile_shapes={sweep_equivalent_shapes}"
                )
            if direct_feature_shapes:
                evidence += f", observed_direct_feature_shapes={direct_feature_shapes}"
            if spherical_recess_shapes:
                evidence += (
                    f", observed_spherical_recess_equivalent_shapes={spherical_recess_shapes}, "
                    "execute_build123d_geometry_fallback=true"
                )
            if snapshot_shapes:
                evidence += (
                    f", observed_snapshot_profile_shapes={snapshot_shapes}, "
                    "execute_build123d_geometry_fallback=true"
                )
            if snapshot_requirement_shapes:
                evidence += (
                    f", observed_requirement_aligned_snapshot_shapes={snapshot_requirement_shapes}, "
                    "execute_build123d_geometry_fallback=true"
                )
            if snapshot_requirement_shape_evidence:
                evidence += f", {snapshot_requirement_shape_evidence}"
            checks.append(
                RequirementCheck(
                    check_id="feature_profile_shape_alignment",
                    label="Explicit profile shapes named in the requirement are preserved either in post-solid sketch windows or in semantically equivalent direct features",
                    status=(
                        RequirementCheckStatus.PASS
                        if (
                            profile_shape_ok
                            or sweep_equivalent_shape_ok
                            or direct_feature_shape_ok
                            or spherical_recess_shape_ok
                            or snapshot_shape_ok
                            or snapshot_requirement_shape_ok
                        )
                        else RequirementCheckStatus.FAIL
                    ),
                    blocking=True,
                    evidence=evidence,
                )
            )

        half_shell_requirement = self._extract_half_shell_profile_requirement(
            requirement_text
        )
        if (
            half_shell_requirement is not None
            and history
            and int(history[-1].result_snapshot.geometry.solids or 0) > 0
        ):
            half_shell_ok, half_shell_evidence = (
                self._snapshot_has_requirement_aligned_half_shell_profile_result(
                    snapshot=history[-1].result_snapshot,
                    requirement_text=requirement_text,
                )
            )
            checks.append(
                RequirementCheck(
                    check_id="feature_half_shell_profile_envelope",
                    label="Half-shell profiles preserve a one-sided radial envelope instead of silently keeping a full-diameter body",
                    status=(
                        RequirementCheckStatus.PASS
                        if half_shell_ok
                        else RequirementCheckStatus.FAIL
                    ),
                    blocking=True,
                    evidence=half_shell_evidence,
                )
            )

        if (
            history
            and int(history[-1].result_snapshot.geometry.solids or 0) > 0
            and self._requirement_requires_merged_body_result(
                requirement_text,
                semantics=semantics,
            )
        ):
            merged_body_ok = int(history[-1].result_snapshot.geometry.solids or 0) == 1
            checks.append(
                RequirementCheck(
                    check_id="feature_merged_body_result",
                    label="Union / merged-body intent resolves to one connected solid instead of detached bodies",
                    status=(
                        RequirementCheckStatus.PASS
                        if merged_body_ok
                        else RequirementCheckStatus.FAIL
                    ),
                    blocking=True,
                    evidence=(
                        "final_solids="
                        f"{int(history[-1].result_snapshot.geometry.solids or 0)}, "
                        f"requires_merged_body={self._requirement_requires_merged_body_result(requirement_text, semantics=semantics)}"
                    ),
                )
            )

        if first_solid_index is not None:
            annular_seed_ok, annular_seed_evidence = (
                self._history_has_requirement_aligned_annular_pattern_seed(
                    history=history,
                    first_solid_index=first_solid_index,
                    requirement_text=requirement_text,
                )
            )
            if annular_seed_evidence:
                checks.append(
                    RequirementCheck(
                        check_id="feature_pattern_seed_alignment",
                        label="Pattern seed profile aligns with the required annular/radial repeated-feature geometry",
                        status=(
                            RequirementCheckStatus.PASS
                            if annular_seed_ok
                            else RequirementCheckStatus.FAIL
                        ),
                        blocking=True,
                        evidence=annular_seed_evidence,
                    )
                )

        edge_feature_rules = [
            (
                semantics.mentions_fillet,
                CADActionType.FILLET,
                "feature_fillet",
                "Contains required fillet feature",
            ),
            (
                semantics.mentions_chamfer,
                CADActionType.CHAMFER,
                "feature_chamfer",
                "Contains required chamfer feature",
            ),
        ]
        for enabled, action_type, check_id, label in edge_feature_rules:
            if not enabled:
                continue
            passed = any(
                entry.action_type == action_type
                and self._history_action_materially_changes_geometry(history, index)
                and (
                    not semantics.mentions_targeted_edge_feature
                    or self._action_uses_matching_edge_targets(
                        history,
                        index,
                        entry,
                        semantics.edge_targets,
                        semantics.face_targets,
                    )
                )
                for index, entry in enumerate(history)
            )
            snapshot_edge_feature = False
            snapshot_edge_feature_evidence = ""
            if (
                not passed
                and action_type == CADActionType.FILLET
                and history
                and self._history_starts_from_execute_build123d_snapshot(history)
            ):
                (
                    snapshot_edge_feature,
                    snapshot_edge_feature_evidence,
                ) = self._snapshot_has_targeted_fillet_face(
                    snapshot=history[-1].result_snapshot,
                    edge_targets=semantics.edge_targets,
                    requirement_text=requirement_text,
                )
                passed = snapshot_edge_feature
            checks.append(
                RequirementCheck(
                    check_id=check_id,
                    label=label,
                    status=(
                        RequirementCheckStatus.PASS
                        if passed
                        else RequirementCheckStatus.FAIL
                    ),
                    blocking=True,
                    evidence=(
                        snapshot_edge_feature_evidence
                        if snapshot_edge_feature
                        else (
                            "found targeted material edge feature"
                            if passed
                            else (
                                f"required targeted {action_type.value} but no matching explicit edge targeting was found"
                                if semantics.edge_targets
                                else f"required {action_type.value} but no material feature action was found"
                            )
                        )
                    ),
                )
            )

        if semantics.mentions_pattern:
            passed = any(
                entry.action_type in {
                    CADActionType.PATTERN_LINEAR,
                    CADActionType.PATTERN_CIRCULAR,
                }
                and self._history_action_materially_changes_geometry(history, index)
                for index, entry in enumerate(history)
            )
            if not passed:
                passed = self._history_has_explicit_repeated_profile_window(history)
            if not passed:
                passed = self._history_has_direct_repeated_feature_pattern(history)
            snapshot_pattern = False
            snapshot_spherical_recess_pattern = False
            if (
                not passed
                and self._history_starts_from_execute_build123d_snapshot(history)
            ):
                snapshot_pattern = self._snapshot_has_direct_repeated_feature_pattern(
                    history[-1].result_snapshot
                )
                passed = snapshot_pattern
            if (
                not passed
                and self._history_starts_from_execute_build123d_snapshot(history)
                and getattr(semantics, "mentions_spherical_recess", False)
            ):
                snapshot_spherical_recess_pattern = (
                    self._snapshot_has_direct_repeated_spherical_recess_pattern(
                        snapshot=history[-1].result_snapshot,
                        face_targets=semantics.face_targets,
                        expected_centers=expected_local_feature_centers,
                        allow_translation=self._requirement_uses_centered_pattern_local_coordinates(
                            requirement_text
                        ),
                    )
                )
                passed = snapshot_spherical_recess_pattern
            checks.append(
                RequirementCheck(
                    check_id="feature_pattern",
                    label="Contains required pattern feature",
                    status=(
                        RequirementCheckStatus.PASS
                        if passed
                        else RequirementCheckStatus.FAIL
                    ),
                    blocking=True,
                    evidence=(
                        (
                            "execute_build123d_geometry_fallback=true, found repeated direct feature pattern in final geometry"
                            if snapshot_pattern
                            else (
                                "execute_build123d_geometry_fallback=true, found repeated spherical recess pattern in final geometry"
                                if snapshot_spherical_recess_pattern
                                else "found material pattern action"
                            )
                        )
                        if passed
                        else f"required pattern intent, observed actions={sorted(action_types)}"
                    ),
                )
            )
        return checks

    def _snapshot_has_spherical_recess_circle_equivalent(
        self,
        *,
        snapshot: CADStateSnapshot,
        face_targets: tuple[str, ...],
    ) -> bool:
        realized_centers = self._snapshot_collect_subtractive_feature_centers(
            snapshot=snapshot,
            face_targets=face_targets,
        )
        if not realized_centers:
            return False
        return any(
            str(getattr(face, "geom_type", "")).strip().upper() == "SPHERE"
            for face in self._snapshot_feature_faces(snapshot)
        )

    def _snapshot_has_direct_repeated_spherical_recess_pattern(
        self,
        *,
        snapshot: CADStateSnapshot,
        face_targets: tuple[str, ...],
        expected_centers: list[list[float]] | None = None,
        allow_translation: bool = False,
    ) -> bool:
        if not self._snapshot_has_spherical_recess_circle_equivalent(
            snapshot=snapshot,
            face_targets=face_targets,
        ):
            return False
        realized_centers = self._snapshot_collect_subtractive_feature_centers(
            snapshot=snapshot,
            face_targets=face_targets,
        )
        if len(realized_centers) < 2:
            return False
        if expected_centers:
            return self._center_sets_match_2d(
                realized_centers,
                expected_centers,
                allow_translation=allow_translation,
            )
        return True

    def _find_first_solid_index(
        self, history: list[ActionHistoryEntry]
    ) -> int | None:
        for index, entry in enumerate(history):
            if entry.result_snapshot.geometry.solids > 0:
                return index
        return None

    def _history_action_materially_changes_geometry(
        self,
        history: list[ActionHistoryEntry],
        index: int,
    ) -> bool:
        current = history[index].result_snapshot.geometry
        previous = history[index - 1].result_snapshot.geometry if index > 0 else None
        if previous is None:
            return (
                current.solids > 0
                or current.faces > 0
                or current.edges > 0
                or abs(float(current.volume)) > 1e-6
            )
        if (
            current.solids != previous.solids
            or current.faces != previous.faces
            or current.edges != previous.edges
        ):
            return True
        if abs(float(current.volume) - float(previous.volume)) > max(
            1e-6, abs(float(previous.volume)) * 1e-4
        ):
            return True
        current_bbox = current.bbox if isinstance(current.bbox, list) else []
        previous_bbox = previous.bbox if isinstance(previous.bbox, list) else []
        if len(current_bbox) == 3 and len(previous_bbox) == 3:
            for current_value, previous_value in zip(current_bbox, previous_bbox):
                if abs(float(current_value) - float(previous_value)) > max(
                    1e-6,
                    abs(float(previous_value)) * 1e-4,
                ):
                    return True
        return False

    def _count_profile_actions_between(
        self,
        history: list[ActionHistoryEntry],
        start_index: int,
        end_index: int | None,
    ) -> int:
        if end_index is None or end_index <= start_index:
            return 0
        return sum(
            self._profile_instance_count(entry)
            for entry in history[start_index:end_index]
            if entry.action_type
            in {
                CADActionType.ADD_RECTANGLE,
                CADActionType.ADD_CIRCLE,
                CADActionType.ADD_POLYGON,
            }
        )

    def _find_preceding_sketch_index(
        self,
        history: list[ActionHistoryEntry],
        action_index: int,
    ) -> int | None:
        for index in range(action_index - 1, -1, -1):
            action_type = history[index].action_type
            if action_type == CADActionType.CREATE_SKETCH:
                return index
            if action_type in {
                CADActionType.EXTRUDE,
                CADActionType.CUT_EXTRUDE,
                CADActionType.TRIM_SOLID,
                CADActionType.REVOLVE,
                CADActionType.LOFT,
                CADActionType.HOLE,
            }:
                return None
        return None

    def _history_window_has_profile_actions(
        self,
        history: list[ActionHistoryEntry],
        sketch_index: int | None,
        action_index: int,
        allowed_action_types: set[CADActionType] | None = None,
    ) -> bool:
        if sketch_index is None or sketch_index >= action_index:
            return False
        profile_action_types = allowed_action_types or {
            CADActionType.ADD_RECTANGLE,
            CADActionType.ADD_CIRCLE,
            CADActionType.ADD_POLYGON,
        }
        return any(
            entry.action_type in profile_action_types
            for entry in history[sketch_index + 1 : action_index]
        )

    def _action_is_subtractive_edit(self, entry: ActionHistoryEntry) -> bool:
        if entry.action_type in {
            CADActionType.CUT_EXTRUDE,
            CADActionType.TRIM_SOLID,
            CADActionType.HOLE,
            CADActionType.SPHERE_RECESS,
        }:
            return True
        if entry.action_type != CADActionType.REVOLVE:
            return False
        operation = str(
            entry.action_params.get(
                "operation",
                entry.action_params.get(
                    "mode",
                    entry.action_params.get(
                        "boolean", entry.action_params.get("cut", "")
                    ),
                ),
            )
        ).strip().lower()
        return operation in {
            "cut",
            "subtract",
            "subtractive",
            "difference",
            "remove",
            "true",
        }

    def _action_is_additive_feature(self, entry: ActionHistoryEntry) -> bool:
        if entry.action_type not in {
            CADActionType.EXTRUDE,
            CADActionType.REVOLVE,
            CADActionType.LOFT,
        }:
            return False
        return not self._action_is_subtractive_edit(entry)

    def _resolve_snapshot_for_topology_ref(
        self,
        history: list[ActionHistoryEntry],
        ref_value: Any,
    ) -> CADStateSnapshot | None:
        parsed = parse_topology_ref(ref_value)
        if parsed is None:
            alias_token = self._normalize_face_ref_alias_token(ref_value)
            if alias_token is None:
                return None
            latest_entry = history[-1] if history else None
            if latest_entry is None or latest_entry.result_snapshot.topology_index is None:
                return None
            if self._resolve_face_for_alias(
                latest_entry.result_snapshot.topology_index,
                alias_token,
            ) is None:
                return None
            return latest_entry.result_snapshot
        target_step = int(parsed.get("step", 0) or 0)
        if target_step <= 0:
            return None
        for item in history:
            if int(item.result_snapshot.step) == target_step:
                return item.result_snapshot
        return None

    def _resolve_topology_face_for_ref(
        self,
        history: list[ActionHistoryEntry],
        ref_value: Any,
    ) -> TopologyFaceEntity | None:
        parsed = parse_topology_ref(ref_value)
        if parsed is None:
            alias_token = self._normalize_face_ref_alias_token(ref_value)
            if alias_token is None:
                return None
            latest_entry = history[-1] if history else None
            topology_index = (
                latest_entry.result_snapshot.topology_index
                if latest_entry is not None
                else None
            )
            return self._resolve_face_for_alias(topology_index, alias_token)
        if parsed.get("kind") != "face":
            return None
        snapshot = self._resolve_snapshot_for_topology_ref(history, ref_value)
        if snapshot is None or snapshot.topology_index is None:
            return None
        entity_id = str(parsed.get("entity_id", "")).strip()
        if not entity_id:
            return None
        for face in snapshot.topology_index.faces:
            if face.face_id == entity_id or face.face_ref == str(ref_value).strip():
                return face
        return None

    def _normalize_face_ref_alias_token(self, ref_value: Any) -> str | None:
        if not isinstance(ref_value, str):
            return None
        token = ref_value.strip().lower().replace("-", "_").replace(" ", "_")
        if not token or parse_topology_ref(token) is not None:
            return None
        alias_map = {
            "top": "top",
            "top_face": "top",
            "top_faces": "top",
            "bottom": "bottom",
            "bottom_face": "bottom",
            "bottom_faces": "bottom",
            "front": "front",
            "front_face": "front",
            "front_faces": "front",
            "back": "back",
            "back_face": "back",
            "back_faces": "back",
            "left": "left",
            "left_face": "left",
            "left_faces": "left",
            "right": "right",
            "right_face": "right",
            "right_faces": "right",
        }
        return alias_map.get(token)

    def _face_alias_hint(self, alias_token: str | None) -> str | None:
        if alias_token is None:
            return None
        return {
            "top": "top_faces",
            "bottom": "bottom_faces",
            "front": "front_faces",
            "back": "back_faces",
            "left": "left_faces",
            "right": "right_faces",
        }.get(alias_token)

    def _resolve_face_for_alias(
        self,
        topology_index: TopologyObjectIndex | None,
        alias_token: str | None,
    ) -> TopologyFaceEntity | None:
        if topology_index is None or alias_token is None:
            return None
        candidates: list[tuple[int, float, str, TopologyFaceEntity]] = []
        for face in topology_index.faces:
            labels = self._topology_face_labels(topology_index, face)
            if alias_token not in labels:
                continue
            planar_rank = 0 if str(face.geom_type).strip().upper() == "PLANE" else 1
            area = float(face.area) if isinstance(face.area, (int, float)) else 0.0
            candidates.append((planar_rank, -area, str(face.face_ref), face))
        if not candidates:
            return None
        candidates.sort(key=lambda item: (item[0], item[1], item[2]))
        return candidates[0][3]

    def _normalize_face_ref_alias_in_params(
        self,
        action_params: dict[str, Any],
        action_history: list[ActionHistoryEntry],
    ) -> dict[str, Any]:
        resolved = dict(action_params)
        face_ref = resolved.get("face_ref")
        alias_token = self._normalize_face_ref_alias_token(face_ref)
        if alias_token is None:
            return resolved
        latest_entry = action_history[-1] if action_history else None
        topology_index = (
            latest_entry.result_snapshot.topology_index
            if latest_entry is not None
            else None
        )
        matched_face = self._resolve_face_for_alias(topology_index, alias_token)
        if matched_face is None or not isinstance(matched_face.face_ref, str):
            return resolved
        resolved["face_ref"] = matched_face.face_ref
        resolved.setdefault("_face_candidate_hint", self._face_alias_hint(alias_token))
        resolved.setdefault("_face_alias_input", str(face_ref).strip())
        return resolved

    def _resolve_topology_edge_for_ref(
        self,
        history: list[ActionHistoryEntry],
        ref_value: Any,
    ) -> TopologyEdgeEntity | None:
        parsed = parse_topology_ref(ref_value)
        if parsed is None or parsed.get("kind") != "edge":
            return None
        snapshot = self._resolve_snapshot_for_topology_ref(history, ref_value)
        if snapshot is None or snapshot.topology_index is None:
            return None
        entity_id = str(parsed.get("entity_id", "")).strip()
        if not entity_id:
            return None
        for edge in snapshot.topology_index.edges:
            if edge.edge_id == entity_id or edge.edge_ref == str(ref_value).strip():
                return edge
        return None

    def _topology_face_labels(
        self,
        topology_index: TopologyObjectIndex | None,
        face: TopologyFaceEntity | None,
    ) -> set[str]:
        if topology_index is None or face is None:
            return set()
        scope_faces = self._faces_for_parent_solid_scope(
            topology_index.faces,
            parent_solid_id=getattr(face, "parent_solid_id", None),
        )
        extents = self._topology_extents(
            faces=scope_faces or topology_index.faces,
            edges=topology_index.edges,
        )
        if extents is None:
            return set()
        min_x, max_x, min_y, max_y, min_z, max_z = extents
        x_tol = self._extent_tolerance(min_x, max_x)
        y_tol = self._extent_tolerance(min_y, max_y)
        z_tol = self._extent_tolerance(min_z, max_z)
        labels: set[str] = set()
        if isinstance(face.normal, list) and len(face.normal) >= 3:
            nz = face.normal[2]
            if isinstance(nz, (int, float)):
                if float(nz) >= 0.7 and self._near_max(face.bbox.zmax, max_z, z_tol):
                    labels.add("top")
                if float(nz) <= -0.7 and self._near_min(face.bbox.zmin, min_z, z_tol):
                    labels.add("bottom")
        if self._near_max(face.center[2], max_z, z_tol):
            labels.add("top")
        if self._near_min(face.center[2], min_z, z_tol):
            labels.add("bottom")

        if isinstance(face.normal, list) and len(face.normal) >= 2:
            ny = face.normal[1]
            if isinstance(ny, (int, float)):
                if float(ny) >= 0.7 and self._near_max(face.bbox.ymax, max_y, y_tol):
                    labels.add("front")
                if float(ny) <= -0.7 and self._near_min(face.bbox.ymin, min_y, y_tol):
                    labels.add("back")
        if self._near_max(face.center[1], max_y, y_tol):
            labels.add("front")
        if self._near_min(face.center[1], min_y, y_tol):
            labels.add("back")

        if isinstance(face.normal, list) and len(face.normal) >= 1:
            nx = face.normal[0]
            if isinstance(nx, (int, float)):
                if float(nx) >= 0.7 and self._near_max(face.bbox.xmax, max_x, x_tol):
                    labels.add("right")
                if float(nx) <= -0.7 and self._near_min(face.bbox.xmin, min_x, x_tol):
                    labels.add("left")
        if self._near_max(face.center[0], max_x, x_tol):
            labels.add("right")
        if self._near_min(face.center[0], min_x, x_tol):
            labels.add("left")
        touches_outer_xy = (
            self._near_min(face.bbox.xmin, min_x, x_tol)
            or self._near_max(face.bbox.xmax, max_x, x_tol)
            or self._near_min(face.bbox.ymin, min_y, y_tol)
            or self._near_max(face.bbox.ymax, max_y, y_tol)
        )
        if labels.intersection({"top", "bottom", "front", "back", "left", "right"}) or (
            str(face.geom_type).strip().upper() != "PLANE" and touches_outer_xy
        ):
            labels.add("outer")
        return labels

    def _topology_edge_labels(
        self,
        topology_index: TopologyObjectIndex | None,
        edge: TopologyEdgeEntity | None,
    ) -> set[str]:
        if topology_index is None or edge is None:
            return set()
        extents = self._topology_extents(
            faces=topology_index.faces,
            edges=topology_index.edges,
        )
        if extents is None:
            return set()
        min_x, max_x, min_y, max_y, min_z, max_z = extents
        x_tol = self._extent_tolerance(min_x, max_x)
        y_tol = self._extent_tolerance(min_y, max_y)
        z_tol = self._extent_tolerance(min_z, max_z)
        labels: set[str] = set()
        if isinstance(edge.center, list) and len(edge.center) >= 3:
            if self._near_max(float(edge.center[2]), max_z, z_tol):
                labels.add("top")
            if self._near_min(float(edge.center[2]), min_z, z_tol):
                labels.add("bottom")
        if self._near_max(edge.bbox.zmax, max_z, z_tol) and edge.bbox.zlen <= z_tol * 4.0:
            labels.add("top")
        if self._near_min(edge.bbox.zmin, min_z, z_tol) and edge.bbox.zlen <= z_tol * 4.0:
            labels.add("bottom")
        face_lookup = {
            face.face_ref: face for face in topology_index.faces if isinstance(face.face_ref, str)
        }
        adjacent_refs = [
            face_ref for face_ref in edge.adjacent_face_refs if isinstance(face_ref, str)
        ]
        adjacent_outer_count = 0
        for face_ref in adjacent_refs:
            face_labels = self._topology_face_labels(topology_index, face_lookup.get(face_ref))
            labels.update(face_labels)
            if "outer" in face_labels:
                adjacent_outer_count += 1
        touches_outer_xy = (
            self._near_min(edge.bbox.xmin, min_x, x_tol)
            or self._near_max(edge.bbox.xmax, max_x, x_tol)
            or self._near_min(edge.bbox.ymin, min_y, y_tol)
            or self._near_max(edge.bbox.ymax, max_y, y_tol)
        )
        if (
            adjacent_outer_count >= 2
            or (adjacent_refs and adjacent_outer_count == len(adjacent_refs))
            or (adjacent_outer_count == 0 and touches_outer_xy)
        ):
            labels.add("outer")
        else:
            labels.add("inner")
        alignment_axis = self._topology_edge_alignment_axis(edge)
        if alignment_axis == "X":
            labels.add("x_parallel")
        elif alignment_axis == "Y":
            labels.add("y_parallel")
        elif alignment_axis == "Z":
            labels.add("z_parallel")
        return labels

    def _topology_edge_center_z(self, edge: TopologyEdgeEntity | None) -> float | None:
        if edge is None:
            return None
        if isinstance(edge.center, list) and len(edge.center) >= 3:
            try:
                return float(edge.center[2])
            except Exception:
                return float(edge.bbox.zmin)
        return float(edge.bbox.zmin)

    def _topology_edge_alignment_axis(
        self,
        edge: TopologyEdgeEntity | None,
    ) -> str | None:
        if edge is None:
            return None
        if str(edge.geom_type).strip().upper() != "LINE":
            return None
        lengths = {
            "X": abs(float(edge.bbox.xlen)),
            "Y": abs(float(edge.bbox.ylen)),
            "Z": abs(float(edge.bbox.zlen)),
        }
        axis = max(lengths, key=lengths.get)
        axis_length = lengths[axis]
        other_lengths = [value for key, value in lengths.items() if key != axis]
        max_other = max(other_lengths) if other_lengths else 0.0
        if axis_length <= 1e-6:
            return None
        if max_other > max(axis_length * 0.15, 1e-6):
            return None
        return axis

    def _labels_match_face_targets(
        self,
        labels: set[str],
        face_targets: tuple[str, ...],
    ) -> bool:
        if not face_targets:
            return True
        if "existing" in face_targets and labels:
            return True
        if "side" in face_targets and labels.intersection({"front", "back", "left", "right"}):
            return True
        return bool(labels.intersection(face_targets))

    def _labels_match_edge_targets(
        self,
        labels: set[str],
        edge_targets: tuple[str, ...],
    ) -> bool:
        normalized_targets = self._normalize_edge_targets_for_matching(edge_targets)
        if not normalized_targets:
            return True
        for target in normalized_targets:
            if target == "x_parallel_outer_edges" and {"outer", "x_parallel"}.issubset(labels):
                return True
            if target == "y_parallel_outer_edges" and {"outer", "y_parallel"}.issubset(labels):
                return True
            if target == "z_parallel_outer_edges" and {"outer", "z_parallel"}.issubset(labels):
                return True
            if target == "x_parallel_top_outer_edges" and {"top", "outer", "x_parallel"}.issubset(labels):
                return True
            if target == "y_parallel_top_outer_edges" and {"top", "outer", "y_parallel"}.issubset(labels):
                return True
            if target == "z_parallel_top_outer_edges" and {"top", "outer", "z_parallel"}.issubset(labels):
                return True
            if target == "x_parallel_bottom_outer_edges" and {"bottom", "outer", "x_parallel"}.issubset(labels):
                return True
            if target == "y_parallel_bottom_outer_edges" and {"bottom", "outer", "y_parallel"}.issubset(labels):
                return True
            if target == "z_parallel_bottom_outer_edges" and {"bottom", "outer", "z_parallel"}.issubset(labels):
                return True
            if target == "top_outer_edges" and {"top", "outer"}.issubset(labels):
                return True
            if target == "bottom_outer_edges" and {"bottom", "outer"}.issubset(labels):
                return True
            if target == "outer_edges" and "outer" in labels:
                return True
            if target == "inner_edges" and "inner" in labels:
                return True
            if target == "top_edges" and "top" in labels:
                return True
            if target == "bottom_edges" and "bottom" in labels:
                return True
        return False

    def _normalize_edge_targets_for_matching(
        self,
        edge_targets: tuple[str, ...],
    ) -> tuple[str, ...]:
        ordered = [item for item in edge_targets if isinstance(item, str) and item.strip()]
        if not ordered:
            return ()
        normalized = list(ordered)
        if "top_outer_edges" in normalized:
            normalized = [
                item for item in normalized if item not in {"top_edges", "outer_edges"}
            ]
        if "bottom_outer_edges" in normalized:
            normalized = [
                item
                for item in normalized
                if item not in {"bottom_edges", "outer_edges"}
            ]
        for axis in ("x", "y", "z"):
            specific_outer = f"{axis}_parallel_outer_edges"
            specific_top_outer = f"{axis}_parallel_top_outer_edges"
            specific_bottom_outer = f"{axis}_parallel_bottom_outer_edges"
            if specific_outer in normalized:
                normalized = [
                    item
                    for item in normalized
                    if item not in {"outer_edges"}
                ]
            if specific_top_outer in normalized:
                normalized = [
                    item
                    for item in normalized
                    if item
                    not in {"top_outer_edges", "top_edges", "outer_edges", specific_outer}
                ]
            if specific_bottom_outer in normalized:
                normalized = [
                    item
                    for item in normalized
                    if item
                    not in {
                        "bottom_outer_edges",
                        "bottom_edges",
                        "outer_edges",
                        specific_outer,
                    }
                ]
        if "inner_bottom_edges" in normalized:
            normalized = [
                item
                for item in normalized
                if item not in {"bottom_edges", "inner_edges"}
            ]
        if "inner_top_edges" in normalized:
            normalized = [
                item
                for item in normalized
                if item not in {"top_edges", "inner_edges"}
            ]
        deduped: list[str] = []
        for item in normalized:
            if item not in deduped:
                deduped.append(item)
        return tuple(deduped)

    def _sketch_matches_face_targets(
        self,
        history: list[ActionHistoryEntry],
        entry: ActionHistoryEntry,
        face_targets: tuple[str, ...],
    ) -> bool:
        if entry.action_type != CADActionType.CREATE_SKETCH:
            return False
        params = entry.action_params
        face_ref = params.get("face_ref")
        if isinstance(face_ref, str) and face_ref.strip():
            resolved_face = self._resolve_topology_face_for_ref(history, face_ref)
            resolved_snapshot = self._resolve_snapshot_for_topology_ref(history, face_ref)
            if resolved_face is not None and resolved_snapshot is not None:
                return self._labels_match_face_targets(
                    self._topology_face_labels(
                        resolved_snapshot.topology_index,
                        resolved_face,
                    ),
                    face_targets,
                )
            return not face_targets

        plane_raw = params.get("plane", "XY")
        plane_token = str(plane_raw).strip().upper() if isinstance(plane_raw, str) else "XY"
        attach_to_solid = bool(params.get("attach_to_solid", False))
        mapped_face = {
            "TOP": "top",
            "BOTTOM": "bottom",
            "FRONT": "front",
            "BACK": "back",
            "RIGHT": "right",
            "LEFT": "left",
        }.get(plane_token)
        if mapped_face is None and attach_to_solid:
            mapped_face = {"XY": "top", "XZ": "front", "YZ": "right"}.get(
                plane_token
            )

        if "existing" in face_targets and mapped_face is not None:
            return True
        if "side" in face_targets and mapped_face in {"front", "back", "left", "right"}:
            return True
        return mapped_face in face_targets

    def _history_has_post_solid_face_feature(
        self,
        history: list[ActionHistoryEntry],
        first_solid_index: int | None,
        face_targets: tuple[str, ...],
        expect_subtractive: bool,
        expect_additive: bool,
    ) -> bool:
        if first_solid_index is None:
            return False
        for action_index in range(first_solid_index + 1, len(history)):
            candidate = history[action_index]
            if not self._history_action_materially_changes_geometry(history, action_index):
                continue
            if expect_subtractive and not self._action_is_subtractive_edit(candidate):
                continue
            if expect_additive and not self._action_is_additive_feature(candidate):
                continue
            if (
                not expect_subtractive
                and not expect_additive
                and candidate.action_type
                not in {
                    CADActionType.EXTRUDE,
                    CADActionType.CUT_EXTRUDE,
                    CADActionType.HOLE,
                    CADActionType.SPHERE_RECESS,
                    CADActionType.REVOLVE,
                    CADActionType.LOFT,
                }
            ):
                continue
            if self._action_matches_face_targets(history, candidate, face_targets):
                return True
        for sketch_index in range(first_solid_index + 1, len(history)):
            sketch_entry = history[sketch_index]
            if not self._sketch_matches_face_targets(
                history, sketch_entry, face_targets
            ):
                continue
            for action_index in range(sketch_index + 1, len(history)):
                candidate = history[action_index]
                if candidate.action_type == CADActionType.CREATE_SKETCH:
                    break
                if not self._history_action_materially_changes_geometry(
                    history, action_index
                ):
                    continue
                if expect_subtractive and not self._action_is_subtractive_edit(candidate):
                    continue
                if expect_additive and not self._action_is_additive_feature(candidate):
                    continue
                if not expect_subtractive and not expect_additive and candidate.action_type not in {
                    CADActionType.EXTRUDE,
                    CADActionType.CUT_EXTRUDE,
                    CADActionType.HOLE,
                    CADActionType.SPHERE_RECESS,
                    CADActionType.REVOLVE,
                    CADActionType.LOFT,
                }:
                    continue
                return True
        return False

    def _action_matches_face_targets(
        self,
        history: list[ActionHistoryEntry],
        entry: ActionHistoryEntry,
        face_targets: tuple[str, ...],
    ) -> bool:
        params = entry.action_params
        face_ref = params.get("face_ref")
        if not isinstance(face_ref, str) or not face_ref.strip():
            return False
        resolved_face = self._resolve_topology_face_for_ref(history, face_ref)
        resolved_snapshot = self._resolve_snapshot_for_topology_ref(history, face_ref)
        if resolved_face is not None and resolved_snapshot is not None:
            return self._labels_match_face_targets(
                self._topology_face_labels(
                    resolved_snapshot.topology_index,
                    resolved_face,
                ),
                face_targets,
            )
        return not face_targets

    def _history_has_local_subtractive_window(
        self,
        history: list[ActionHistoryEntry],
        action_index: int,
        first_solid_index: int | None,
    ) -> bool:
        if first_solid_index is None or action_index <= first_solid_index:
            return False
        sketch_index = self._find_preceding_sketch_index(history, action_index)
        return (
            sketch_index is not None
            and sketch_index > first_solid_index
            and self._history_window_has_profile_actions(
                history, sketch_index=sketch_index, action_index=action_index
            )
        )

    def _history_has_merged_additive_face_feature(
        self,
        history: list[ActionHistoryEntry],
        first_solid_index: int | None,
        face_targets: tuple[str, ...],
    ) -> bool:
        if first_solid_index is None:
            return False
        for action_index, entry in enumerate(history):
            if entry.action_type != CADActionType.EXTRUDE:
                continue
            if action_index <= first_solid_index:
                continue
            if not self._history_action_materially_changes_geometry(history, action_index):
                continue
            sketch_index = self._find_preceding_sketch_index(history, action_index)
            if sketch_index is None or sketch_index <= first_solid_index:
                continue
            sketch_entry = history[sketch_index]
            if face_targets and not self._sketch_matches_face_targets(
                history, sketch_entry, face_targets
            ):
                continue
            if not self._history_window_has_profile_actions(
                history,
                sketch_index=sketch_index,
                action_index=action_index,
            ):
                continue
            baseline_solids = history[sketch_index - 1].result_snapshot.geometry.solids
            current_solids = entry.result_snapshot.geometry.solids
            if baseline_solids > 0 and current_solids <= baseline_solids:
                return True
        return False

    def _history_has_merged_subtractive_face_feature(
        self,
        history: list[ActionHistoryEntry],
        first_solid_index: int | None,
        face_targets: tuple[str, ...],
    ) -> bool:
        if first_solid_index is None:
            return False
        for action_index, entry in enumerate(history):
            if action_index <= first_solid_index:
                continue
            if not self._action_is_subtractive_edit(entry):
                continue
            if not self._history_action_materially_changes_geometry(history, action_index):
                continue
            direct_face_match = self._action_matches_face_targets(
                history, entry, face_targets
            )
            sketch_index = self._find_preceding_sketch_index(history, action_index)
            sketch_face_match = False
            if sketch_index is not None and sketch_index > first_solid_index:
                sketch_face_match = self._sketch_matches_face_targets(
                    history, history[sketch_index], face_targets
                ) and self._history_window_has_profile_actions(
                    history,
                    sketch_index=sketch_index,
                    action_index=action_index,
                )
            if face_targets and not direct_face_match and not sketch_face_match:
                continue
            baseline_snapshot = history[action_index - 1].result_snapshot
            current_snapshot = entry.result_snapshot
            baseline_solids = baseline_snapshot.geometry.solids
            current_solids = current_snapshot.geometry.solids
            baseline_volume = baseline_snapshot.geometry.volume
            current_volume = current_snapshot.geometry.volume
            if baseline_solids <= 0 or current_solids <= 0:
                continue
            if current_solids > baseline_solids:
                continue
            if (
                isinstance(baseline_volume, (int, float))
                and isinstance(current_volume, (int, float))
                and current_volume > baseline_volume + max(1e-6, abs(float(baseline_volume)) * 0.01)
            ):
                continue
            return True
        return False

    def _infer_revolve_axis_token(self, entry: ActionHistoryEntry) -> str:
        axis_raw = entry.action_params.get("axis")
        if isinstance(axis_raw, str):
            axis_token = axis_raw.strip().upper()
            if axis_token in {"X", "Y", "Z"}:
                return axis_token
        axis_start = entry.action_params.get("axis_start")
        axis_end = entry.action_params.get("axis_end")
        if (
            isinstance(axis_start, list)
            and isinstance(axis_end, list)
            and len(axis_start) >= 3
            and len(axis_end) >= 3
        ):
            deltas = [
                abs(float(axis_end[0]) - float(axis_start[0])),
                abs(float(axis_end[1]) - float(axis_start[1])),
                abs(float(axis_end[2]) - float(axis_start[2])),
            ]
            axis_index = max(range(3), key=lambda idx: deltas[idx])
            return ("X", "Y", "Z")[axis_index]
        return "Z"

    def _sketch_plane_contains_revolve_axis(
        self,
        sketch_entry: ActionHistoryEntry,
        revolve_entry: ActionHistoryEntry,
    ) -> bool:
        if sketch_entry.action_type != CADActionType.CREATE_SKETCH:
            return False
        plane_raw = sketch_entry.action_params.get("plane", "XY")
        plane_token = str(plane_raw).strip().upper() if isinstance(plane_raw, str) else "XY"
        plane = {
            "TOP": "XY",
            "BOTTOM": "XY",
            "FRONT": "XZ",
            "BACK": "XZ",
            "RIGHT": "YZ",
            "LEFT": "YZ",
        }.get(plane_token, plane_token)
        axis_token = self._infer_revolve_axis_token(revolve_entry)
        plane_axes = {
            "XY": {"X", "Y"},
            "XZ": {"X", "Z"},
            "YZ": {"Y", "Z"},
        }.get(plane, set())
        return axis_token in plane_axes

    def _history_has_local_revolve_cut_setup(
        self,
        history: list[ActionHistoryEntry],
        revolve_index: int,
        first_solid_index: int | None,
    ) -> bool:
        if first_solid_index is None or revolve_index <= first_solid_index:
            return False
        sketch_index = self._find_preceding_sketch_index(history, revolve_index)
        if sketch_index is None or sketch_index <= first_solid_index:
            return False
        if not self._history_window_has_profile_actions(
            history, sketch_index=sketch_index, action_index=revolve_index
        ):
            return False
        return self._sketch_plane_contains_revolve_axis(
            history[sketch_index], history[revolve_index]
        )

    def _extract_revolved_groove_height_anchor_mode(
        self,
        requirement_text: str | None,
    ) -> str:
        return self._extract_revolved_groove_height_anchor_modes(requirement_text)[0]

    def _extract_revolved_groove_height_anchor_modes(
        self,
        requirement_text: str | None,
    ) -> list[str]:
        text = (requirement_text or "").lower()
        if not text:
            return ["center"]

        explicit_patterns = (
            (
                r"(?:top|upper)(?: edge)?(?: of (?:the )?(?:groove|rectangle|profile))?[^0-9]{0,32}(?:height|z)[^0-9]{0,12}\d+(?:\.\d+)?",
                "top_edge",
            ),
            (
                r"(?:bottom|lower)(?: edge)?(?: of (?:the )?(?:groove|rectangle|profile))?[^0-9]{0,32}(?:height|z)[^0-9]{0,12}\d+(?:\.\d+)?",
                "bottom_edge",
            ),
            (
                r"center(?:ed)?[^0-9]{0,24}(?:at|on)?[^0-9]{0,12}(?:height|z)[^0-9]{0,12}\d+(?:\.\d+)?",
                "center",
            ),
        )
        for pattern, mode in explicit_patterns:
            if re.search(pattern, text) is not None:
                return [mode]
        if re.search(r"at(?: a)? height of\s*\d+(?:\.\d+)?", text) is not None:
            return ["top_edge", "center"]
        return ["center"]

    def _extract_revolved_groove_targets(
        self,
        requirement_text: str | None,
    ) -> tuple[tuple[float, float] | None, float | None, str]:
        text = (requirement_text or "").lower()
        if not text:
            return None, None, "center"
        anchor_mode = self._extract_revolved_groove_height_anchor_mode(text)
        rectangle_match = re.search(
            r"height of\s+(?P<height>\d+(?:\.\d+)?)\s*mm.*?draw a\s+"
            r"(?P<a>\d+(?:\.\d+)?)\s*mm\s*x\s*(?P<b>\d+(?:\.\d+)?)\s*mm\s+rectangle",
            text,
        )
        if rectangle_match is not None:
            return (
                (
                    float(rectangle_match.group("a")),
                    float(rectangle_match.group("b")),
                ),
                float(rectangle_match.group("height")),
                anchor_mode,
            )
        rectangle_only = re.search(
            r"draw a\s+(?P<a>\d+(?:\.\d+)?)\s*mm\s*x\s*(?P<b>\d+(?:\.\d+)?)\s*mm\s+rectangle",
            text,
        )
        height_only = re.search(
            r"height of\s+(?P<height>\d+(?:\.\d+)?)\s*mm",
            text,
        )
        dims = (
            (
                float(rectangle_only.group("a")),
                float(rectangle_only.group("b")),
            )
            if rectangle_only is not None
            else None
        )
        height = (
            float(height_only.group("height")) if height_only is not None else None
        )
        return dims, height, anchor_mode

    def _history_has_requirement_aligned_revolved_groove_window(
        self,
        history: list[ActionHistoryEntry],
        revolve_indices: list[int],
        first_solid_index: int | None,
        requirement_text: str | None,
    ) -> bool:
        groove_dims, groove_height, _groove_anchor_mode = self._extract_revolved_groove_targets(
            requirement_text
        )
        groove_anchor_modes = self._extract_revolved_groove_height_anchor_modes(
            requirement_text
        )
        for revolve_index in revolve_indices:
            if not self._history_has_local_revolve_cut_setup(
                history=history,
                revolve_index=revolve_index,
                first_solid_index=first_solid_index,
            ):
                continue
            sketch_index = self._find_preceding_sketch_index(history, revolve_index)
            if sketch_index is None:
                continue
            sketch_entry = history[sketch_index]
            revolve_entry = history[revolve_index]
            rectangle_entries = [
                item
                for item in history[sketch_index + 1 : revolve_index]
                if item.action_type == CADActionType.ADD_RECTANGLE
            ]
            if groove_dims is not None:
                if not rectangle_entries:
                    continue
                expected_dims = sorted(round(float(value), 4) for value in groove_dims)
                matched_dims = False
                for rectangle_entry in rectangle_entries:
                    width = rectangle_entry.action_params.get("width")
                    height = rectangle_entry.action_params.get("height")
                    if not isinstance(width, (int, float)) or not isinstance(
                        height, (int, float)
                    ):
                        continue
                    observed_dims = sorted(
                        [round(abs(float(width)), 4), round(abs(float(height)), 4)]
                    )
                    if all(
                        abs(observed - expected)
                        <= max(1.0, abs(expected) * 0.15)
                        for observed, expected in zip(observed_dims, expected_dims)
                    ):
                        matched_dims = True
                        break
                if not matched_dims:
                    continue
            if groove_height is not None:
                axis_token = self._infer_revolve_axis_token(revolve_entry)
                axis_position_matches = False
                for groove_anchor_mode in groove_anchor_modes:
                    axis_position = self._extract_axis_coordinate_from_sketch_window(
                        sketch_entry=sketch_entry,
                        profile_entries=rectangle_entries,
                        axis_token=axis_token,
                        anchor_mode=groove_anchor_mode,
                    )
                    if axis_position is None:
                        continue
                    if abs(axis_position - groove_height) <= max(
                        1.0, abs(groove_height) * 0.12
                    ):
                        axis_position_matches = True
                        break
                if not axis_position_matches:
                    continue
            return True
        return False

    def _history_has_semantically_valid_revolved_groove_result(
        self,
        history: list[ActionHistoryEntry],
        revolve_indices: list[int],
    ) -> tuple[bool, str]:
        if not revolve_indices:
            return (
                False,
                "no subtractive revolve result available for groove result validation",
            )

        for revolve_index in revolve_indices:
            if revolve_index <= 0 or revolve_index >= len(history):
                continue
            before_snapshot = history[revolve_index - 1].result_snapshot
            after_snapshot = history[revolve_index].result_snapshot
            if before_snapshot is None or after_snapshot is None:
                continue

            before_bbox = list(before_snapshot.geometry.bbox)
            after_bbox = list(after_snapshot.geometry.bbox)
            if len(before_bbox) != 3 or len(after_bbox) != 3:
                continue

            envelope_preserved = all(
                abs(float(after_dim) - float(before_dim))
                <= max(1.0, abs(float(before_dim)) * 0.08)
                for before_dim, after_dim in zip(before_bbox, after_bbox)
            )

            before_volume = (
                float(before_snapshot.geometry.volume)
                if isinstance(before_snapshot.geometry.volume, (int, float))
                else 0.0
            )
            after_volume = (
                float(after_snapshot.geometry.volume)
                if isinstance(after_snapshot.geometry.volume, (int, float))
                else 0.0
            )
            material_removed = after_volume < (before_volume - max(1.0, before_volume * 0.01))

            before_faces = int(before_snapshot.geometry.faces or 0)
            after_faces = int(after_snapshot.geometry.faces or 0)
            face_growth = after_faces >= before_faces + 1

            before_cyl_faces = self._count_topology_faces_by_geom_type(
                before_snapshot.topology_index,
                "CYLINDER",
            )
            after_cyl_faces = self._count_topology_faces_by_geom_type(
                after_snapshot.topology_index,
                "CYLINDER",
            )
            rotational_face_growth = (
                after_cyl_faces >= before_cyl_faces + 1
                if before_cyl_faces or after_cyl_faces
                else face_growth
            )

            if envelope_preserved and material_removed and rotational_face_growth:
                return (
                    True,
                    "groove result preserved bbox, removed material, and introduced additional rotational faces",
                )

            return (
                False,
                "groove result semantic mismatch: "
                f"before_bbox={before_bbox}, after_bbox={after_bbox}, "
                f"before_volume={before_volume:.4f}, after_volume={after_volume:.4f}, "
                f"before_cyl_faces={before_cyl_faces}, after_cyl_faces={after_cyl_faces}, "
                f"before_faces={before_faces}, after_faces={after_faces}",
            )

        return (
            False,
            "could not resolve a usable pre/post snapshot pair for groove result validation",
        )

    def _count_topology_faces_by_geom_type(
        self,
        topology_index: TopologyObjectIndex | None,
        geom_type: str,
    ) -> int:
        if topology_index is None:
            return 0
        target = str(geom_type).strip().upper()
        return sum(
            1
            for face in topology_index.faces
            if str(face.geom_type).strip().upper() == target
        )

    def _extract_rectangular_notch_targets(
        self,
        requirement_text: str | None,
    ) -> tuple[str | None, tuple[float, float] | None]:
        spec = extract_rectangular_notch_profile_spec(
            requirements=None,
            requirement_text=requirement_text,
        )
        if spec is None:
            return None, None
        return spec.preferred_plane, (float(spec.inner_width), float(spec.inner_height))

    def _history_has_requirement_aligned_notch_window(
        self,
        history: list[ActionHistoryEntry],
        first_solid_index: int | None,
        requirement_text: str | None,
        has_profile_notch_bootstrap: bool,
    ) -> tuple[bool, str]:
        preferred_plane, inner_dims = self._extract_rectangular_notch_targets(
            requirement_text
        )
        if inner_dims is None or preferred_plane not in {"XY", "XZ", "YZ"}:
            return True, ""
        if has_profile_notch_bootstrap:
            return True, "complex_profile_bootstrap_or_multi_profile_section"

        allowed_face_labels = {
            "XY": {"top", "bottom"},
            "XZ": {"front", "back"},
            "YZ": {"left", "right"},
        }.get(preferred_plane, set())
        expected_dims = sorted(round(float(value), 4) for value in inner_dims)

        for index, entry in enumerate(history):
            if not self._action_is_subtractive_edit(entry):
                continue
            if not self._history_action_materially_changes_geometry(history, index):
                continue
            if first_solid_index is None or index <= first_solid_index:
                continue
            sketch_index = self._find_preceding_sketch_index(history, index)
            if sketch_index is None:
                continue
            sketch_entry = history[sketch_index]
            sketch_plane = self._sketch_plane_name_from_entry(sketch_entry)
            if sketch_plane != preferred_plane:
                continue

            face_ref = sketch_entry.action_params.get("face_ref")
            if isinstance(face_ref, str) and face_ref.strip():
                resolved_face = self._resolve_topology_face_for_ref(history, face_ref)
                snapshot = self._resolve_snapshot_for_topology_ref(history, face_ref)
                face_labels = self._topology_face_labels(
                    snapshot.topology_index if snapshot is not None else None,
                    resolved_face,
                )
                if not face_labels.intersection(allowed_face_labels):
                    continue

            rectangle_entries = [
                item
                for item in history[sketch_index + 1 : index]
                if item.action_type == CADActionType.ADD_RECTANGLE
            ]
            for rectangle_entry in rectangle_entries:
                width = rectangle_entry.action_params.get("width")
                height = rectangle_entry.action_params.get("height")
                if not isinstance(width, (int, float)) or not isinstance(
                    height, (int, float)
                ):
                    continue
                observed_dims = sorted(
                    [round(abs(float(width)), 4), round(abs(float(height)), 4)]
                )
                if all(
                    abs(observed - expected)
                    <= max(1.0, abs(expected) * 0.15)
                    for observed, expected in zip(observed_dims, expected_dims)
                ):
                    return (
                        True,
                        f"preferred_plane={preferred_plane}, notch_dims={list(inner_dims)}",
                    )

        return (
            False,
            f"missing requirement-aligned notch window on plane {preferred_plane} with dims={list(inner_dims)}",
        )

    def _extract_axis_coordinate_from_sketch_window(
        self,
        sketch_entry: ActionHistoryEntry,
        profile_entries: list[ActionHistoryEntry],
        axis_token: str,
        anchor_mode: str = "center",
    ) -> float | None:
        plane_raw = sketch_entry.action_params.get("plane", "XY")
        plane_token = str(plane_raw).strip().upper() if isinstance(plane_raw, str) else "XY"
        plane = {
            "TOP": "XY",
            "BOTTOM": "XY",
            "FRONT": "XZ",
            "BACK": "XZ",
            "RIGHT": "YZ",
            "LEFT": "YZ",
        }.get(plane_token, plane_token)

        local_axis_index = {
            "XY": {"X": 0, "Y": 1},
            "XZ": {"X": 0, "Z": 1},
            "YZ": {"Y": 0, "Z": 1},
        }.get(plane, {}).get(axis_token)

        def _candidate_values(raw_value: Any) -> list[float]:
            if (
                isinstance(raw_value, list)
                and len(raw_value) >= 3
                and all(isinstance(item, (int, float)) for item in raw_value[:3])
            ):
                mapped = {
                    "X": float(raw_value[0]),
                    "Y": float(raw_value[1]),
                    "Z": float(raw_value[2]),
                }.get(axis_token)
                return [mapped] if isinstance(mapped, float) else []
            if (
                isinstance(raw_value, list)
                and len(raw_value) >= 2
                and all(isinstance(item, (int, float)) for item in raw_value[:2])
            ):
                if isinstance(local_axis_index, int):
                    return [float(raw_value[local_axis_index])]
            return []

        sketch_anchor: float | None = None
        for source in (
            sketch_entry.action_params.get("position"),
            sketch_entry.action_params.get("origin"),
            sketch_entry.action_params.get("center"),
        ):
            values = _candidate_values(source)
            if values:
                sketch_anchor = values[0]
                break

        candidate_centers: list[tuple[float, float | None]] = []
        for profile_entry in profile_entries:
            profile_axis_offset: float | None = None
            for source in (
                profile_entry.action_params.get("position"),
                profile_entry.action_params.get("center"),
            ):
                values = _candidate_values(source)
                if values:
                    profile_axis_offset = values[0]
                    break

            width = profile_entry.action_params.get("width")
            height = profile_entry.action_params.get("height")
            axial_size: float | None = None
            if isinstance(width, (int, float)) and isinstance(height, (int, float)):
                if plane == "XY":
                    axial_size = (
                        abs(float(width))
                        if axis_token == "X"
                        else abs(float(height))
                    )
                elif plane == "XZ":
                    axial_size = (
                        abs(float(width))
                        if axis_token == "X"
                        else abs(float(height))
                    )
                elif plane == "YZ":
                    axial_size = (
                        abs(float(width))
                        if axis_token == "Y"
                        else abs(float(height))
                    )

            if sketch_anchor is not None and profile_axis_offset is not None:
                candidate_centers.append((sketch_anchor + profile_axis_offset, axial_size))
                continue
            if sketch_anchor is not None:
                candidate_centers.append((sketch_anchor, axial_size))
                continue
            if profile_axis_offset is not None:
                candidate_centers.append((profile_axis_offset, axial_size))

        if not candidate_centers and sketch_anchor is not None:
            candidate_centers.append((sketch_anchor, None))

        for center_value, axial_size in candidate_centers:
            if anchor_mode == "top_edge" and isinstance(axial_size, (int, float)):
                return float(center_value) + (float(axial_size) / 2.0)
            if anchor_mode == "bottom_edge" and isinstance(axial_size, (int, float)):
                return float(center_value) - (float(axial_size) / 2.0)
            return float(center_value)
        return None

    def _history_has_explicit_repeated_profile_window(
        self,
        history: list[ActionHistoryEntry],
    ) -> bool:
        sketch_index: int | None = None
        profile_count = 0
        profile_centers: list[list[float]] = []
        for index, entry in enumerate(history):
            if entry.action_type == CADActionType.CREATE_SKETCH:
                sketch_index = index
                profile_count = 0
                profile_centers = []
                continue
            if sketch_index is None:
                continue
            if entry.action_type in {
                CADActionType.ADD_RECTANGLE,
                CADActionType.ADD_CIRCLE,
                CADActionType.ADD_POLYGON,
            }:
                profile_count += self._profile_instance_count(entry)
                for center in self._collect_profile_entry_centers(entry):
                    if center not in profile_centers:
                        profile_centers.append(center)
                continue
            if entry.action_type in {
                CADActionType.EXTRUDE,
                CADActionType.CUT_EXTRUDE,
                CADActionType.TRIM_SOLID,
                CADActionType.HOLE,
                CADActionType.REVOLVE,
                CADActionType.LOFT,
            }:
                if (
                    profile_count >= 2
                    and len(profile_centers) >= 2
                    and self._history_action_materially_changes_geometry(history, index)
                ):
                    return True
                sketch_index = None
                profile_count = 0
                profile_centers = []
        return False

    def _collect_profile_entry_centers(
        self,
        entry: ActionHistoryEntry,
    ) -> list[list[float]]:
        params = entry.action_params if isinstance(entry.action_params, dict) else {}

        def _normalize_point(raw_value: object) -> list[float] | None:
            if isinstance(raw_value, dict):
                x_value = raw_value.get("x")
                y_value = raw_value.get("y")
                if isinstance(x_value, (int, float)) and isinstance(y_value, (int, float)):
                    return [float(x_value), float(y_value)]
                return None
            if (
                isinstance(raw_value, (list, tuple))
                and len(raw_value) >= 2
                and isinstance(raw_value[0], (int, float))
                and isinstance(raw_value[1], (int, float))
            ):
                return [float(raw_value[0]), float(raw_value[1])]
            return None

        centers_raw = params.get("centers", params.get("positions"))
        centers: list[list[float]] = []
        if isinstance(centers_raw, list):
            for raw_center in centers_raw:
                center = _normalize_point(raw_center)
                if center is not None and center not in centers:
                    centers.append(center)
            if centers:
                return centers

        for key in ("position", "center", "origin"):
            center = _normalize_point(params.get(key))
            if center is not None:
                return [center]

        if entry.action_type == CADActionType.ADD_POLYGON:
            points = params.get("points", params.get("vertices"))
            normalized_points: list[list[float]] = []
            if isinstance(points, list):
                for raw_point in points:
                    center = _normalize_point(raw_point)
                    if center is not None:
                        normalized_points.append(center)
            if normalized_points:
                xs = [item[0] for item in normalized_points]
                ys = [item[1] for item in normalized_points]
                return [[(min(xs) + max(xs)) / 2.0, (min(ys) + max(ys)) / 2.0]]

        return [[0.0, 0.0]]

    def _history_has_direct_repeated_feature_pattern(
        self,
        history: list[ActionHistoryEntry],
    ) -> bool:
        repeated_centers: list[list[float]] = []
        for index, entry in enumerate(history):
            if not self._history_action_materially_changes_geometry(history, index):
                continue
            if entry.action_type not in {
                CADActionType.HOLE,
                CADActionType.SPHERE_RECESS,
            }:
                continue
            for center in self._collect_entry_hole_centers(entry):
                if center not in repeated_centers:
                    repeated_centers.append(center)
            if len(repeated_centers) >= 2:
                return True
        return False

    def _entry_is_complex_profile_builder(self, entry: ActionHistoryEntry) -> bool:
        if entry.action_type != CADActionType.ADD_POLYGON:
            return False
        points = entry.action_params.get("points", entry.action_params.get("vertices"))
        if isinstance(points, list):
            valid_points = 0
            for raw_point in points:
                if isinstance(raw_point, dict):
                    if isinstance(raw_point.get("x"), (int, float)) and isinstance(
                        raw_point.get("y"), (int, float)
                    ):
                        valid_points += 1
                elif isinstance(raw_point, (list, tuple)) and len(raw_point) >= 2:
                    if isinstance(raw_point[0], (int, float)) and isinstance(
                        raw_point[1], (int, float)
                    ):
                        valid_points += 1
            return valid_points >= 6
        return False

    def _history_has_complex_profile_bootstrap(
        self,
        history: list[ActionHistoryEntry],
        start_index: int,
        end_index: int | None,
    ) -> bool:
        if end_index is None or end_index <= start_index:
            return False
        return any(
            self._entry_is_complex_profile_builder(entry)
            for entry in history[start_index:end_index]
        )

    def _profile_instance_count(self, entry: ActionHistoryEntry) -> int:
        params = entry.action_params if isinstance(entry.action_params, dict) else {}
        if entry.action_type == CADActionType.ADD_CIRCLE:
            centers = params.get("centers", params.get("positions"))
            if not isinstance(centers, list):
                radius_inner = params.get("radius_inner")
                if isinstance(radius_inner, (int, float)) and float(radius_inner) > 0.0:
                    return 2
                return 1
            valid_count = 0
            for raw_center in centers:
                if isinstance(raw_center, dict):
                    values = [
                        raw_center.get(axis)
                        for axis in ("x", "y", "z")
                        if isinstance(raw_center.get(axis), (int, float))
                    ]
                elif isinstance(raw_center, (list, tuple)):
                    values = [
                        value for value in raw_center if isinstance(value, (int, float))
                    ]
                else:
                    values = []
                if len(values) >= 2:
                    valid_count += 1
            count = max(valid_count, 1)
            radius_inner = params.get("radius_inner")
            if isinstance(radius_inner, (int, float)) and float(radius_inner) > 0.0:
                count = max(count, 2)
            return count
        if entry.action_type == CADActionType.ADD_RECTANGLE:
            inner_width = params.get("inner_width")
            inner_height = params.get("inner_height")
            if (
                isinstance(inner_width, (int, float))
                and isinstance(inner_height, (int, float))
                and float(inner_width) > 0.0
                and float(inner_height) > 0.0
            ):
                return 2
            return 1
        if entry.action_type == CADActionType.ADD_POLYGON:
            radius_inner = params.get("radius_inner")
            if isinstance(radius_inner, (int, float)) and float(radius_inner) > 0.0:
                return 2
            return 1
        return 1

    def _extract_required_profile_shape_tokens(
        self,
        requirement_text: str | None,
        *,
        post_solid_only: bool = False,
        pre_solid_only: bool = False,
    ) -> tuple[str, ...]:
        text = (requirement_text or "").strip().lower()
        if not text:
            return ()
        scan_text = text
        if post_solid_only and pre_solid_only:
            return ()
        if post_solid_only:
            segments = re.split(r"(?<=[.!?])\s+", text)
            post_solid_segments: list[str] = []
            saw_primary_solid = False
            primary_solid_tokens = (
                "extrude",
                "loft",
                "revolve",
                "sweep",
                "create a base",
                "create the base",
                "create a washer",
                "create the washer",
                "create a pyramid",
                "create the pyramid",
                "frustum",
                "generate a regular triangular pyramid",
                "form a frustum",
            )
            for segment in segments:
                normalized = str(segment).strip().lower()
                if not normalized:
                    continue
                if saw_primary_solid:
                    post_solid_segments.append(normalized)
                    continue
                if any(token in normalized for token in primary_solid_tokens):
                    saw_primary_solid = True
            if post_solid_segments:
                scan_text = " ".join(post_solid_segments)
            else:
                first_match_end = None
                for token in primary_solid_tokens:
                    match_index = text.find(token)
                    if match_index < 0:
                        continue
                    match_end = match_index + len(token)
                    if first_match_end is None or match_end < first_match_end:
                        first_match_end = match_end
                if first_match_end is not None:
                    scan_text = text[first_match_end:].strip()
                else:
                    scan_text = ""
        elif pre_solid_only:
            segments = re.split(r"(?<=[.!?])\s+", text)
            pre_solid_segments: list[str] = []
            primary_solid_tokens = (
                "extrude",
                "loft",
                "revolve",
                "sweep",
                "create a base",
                "create the base",
                "create a washer",
                "create the washer",
                "create a pyramid",
                "create the pyramid",
                "frustum",
                "generate a regular triangular pyramid",
                "form a frustum",
            )
            for segment in segments:
                normalized = str(segment).strip().lower()
                if not normalized:
                    continue
                if any(token in normalized for token in primary_solid_tokens):
                    break
                pre_solid_segments.append(normalized)
            if pre_solid_segments:
                scan_text = " ".join(pre_solid_segments)
        ignore_circle = (
            "inscribed in a circle" in scan_text
            or "circumscribed by a circle" in scan_text
        )
        targets: list[str] = []
        token_map = (
            ("hexagonal", "hexagon"),
            ("hexagon", "hexagon"),
            ("equilateral triangle", "triangle"),
            ("triangular", "triangle"),
            ("triangle", "triangle"),
            ("square", "square"),
            ("rectangular", "rectangle"),
            ("rectangle", "rectangle"),
            ("polygon", "polygon"),
            ("circular", "circle"),
            ("circle", "circle"),
        )
        for token, label in token_map:
            if label == "circle" and ignore_circle:
                continue
            if token in scan_text and label not in targets:
                targets.append(label)
        return tuple(targets)

    def _entry_matches_profile_shape_targets(
        self,
        entry: ActionHistoryEntry,
        targets: tuple[str, ...],
    ) -> bool:
        if not targets:
            return False
        params = entry.action_params if isinstance(entry.action_params, dict) else {}
        if entry.action_type == CADActionType.ADD_CIRCLE:
            return "circle" in targets
        if entry.action_type == CADActionType.ADD_RECTANGLE:
            if "rectangle" in targets:
                return True
            if "square" in targets:
                width = self._to_positive_float(params.get("width"), default=0.0)
                height = self._to_positive_float(params.get("height"), default=0.0)
                if width > 0.0 and height > 0.0 and abs(width - height) <= max(1e-3, width * 0.05):
                    return True
            return False
        if entry.action_type != CADActionType.ADD_POLYGON:
            return False
        if "polygon" in targets:
            return True
        points = params.get("points", params.get("vertices"))
        point_count = 0
        if isinstance(points, list):
            for raw_point in points:
                if (
                    isinstance(raw_point, (list, tuple))
                    and len(raw_point) >= 2
                    and isinstance(raw_point[0], (int, float))
                    and isinstance(raw_point[1], (int, float))
                ):
                    point_count += 1
                elif isinstance(raw_point, dict) and isinstance(raw_point.get("x"), (int, float)) and isinstance(raw_point.get("y"), (int, float)):
                    point_count += 1
        side_count_raw = params.get(
            "sides",
            params.get(
                "n_sides",
                params.get(
                    "num_sides",
                    params.get("side_count", params.get("regular_sides")),
                ),
            ),
        )
        side_count = int(side_count_raw) if isinstance(side_count_raw, (int, float)) else 0
        effective_count = side_count or point_count
        if "triangle" in targets and effective_count == 3:
            return True
        if "hexagon" in targets and effective_count == 6:
            return True
        return False

    def _history_has_post_solid_matching_profile_shape(
        self,
        history: list[ActionHistoryEntry],
        first_solid_index: int | None,
        targets: tuple[str, ...],
    ) -> tuple[bool, list[str]]:
        if first_solid_index is None or not targets:
            return False, []
        observed: list[str] = []
        relevant_actions = {
            CADActionType.EXTRUDE,
            CADActionType.CUT_EXTRUDE,
            CADActionType.REVOLVE,
            CADActionType.LOFT,
            CADActionType.SWEEP,
        }
        for action_index, entry in enumerate(history):
            if action_index <= first_solid_index or entry.action_type not in relevant_actions:
                continue
            if not self._history_action_materially_changes_geometry(history, action_index):
                continue
            sketch_index = self._find_preceding_sketch_index(history, action_index)
            if sketch_index is None or sketch_index <= first_solid_index:
                continue
            for window_entry in history[sketch_index + 1 : action_index]:
                if window_entry.action_type not in {
                    CADActionType.ADD_CIRCLE,
                    CADActionType.ADD_RECTANGLE,
                    CADActionType.ADD_POLYGON,
                }:
                    continue
                if window_entry.action_type == CADActionType.ADD_CIRCLE:
                    observed.append("circle")
                elif window_entry.action_type == CADActionType.ADD_RECTANGLE:
                    observed.append("rectangle")
                elif window_entry.action_type == CADActionType.ADD_POLYGON:
                    observed.append("polygon")
                if self._entry_matches_profile_shape_targets(window_entry, targets):
                    return True, self._normalize_string_list(observed, limit=12)
        return False, self._normalize_string_list(observed, limit=12)

    def _history_has_pre_solid_matching_profile_shape(
        self,
        history: list[ActionHistoryEntry],
        first_solid_index: int | None,
        targets: tuple[str, ...],
    ) -> tuple[bool, list[str]]:
        if not history or not targets:
            return False, []
        upper_bound = (
            first_solid_index
            if isinstance(first_solid_index, int) and first_solid_index >= 0
            else len(history)
        )
        observed: list[str] = []
        for entry in history[:upper_bound]:
            if entry.action_type not in {
                CADActionType.ADD_RECTANGLE,
                CADActionType.ADD_POLYGON,
            }:
                continue
            if entry.action_type == CADActionType.ADD_RECTANGLE:
                observed.append("rectangle")
            elif entry.action_type == CADActionType.ADD_POLYGON:
                observed.append("polygon")
            if self._entry_matches_profile_shape_targets(entry, targets):
                return True, self._normalize_string_list(observed, limit=12)
        return False, self._normalize_string_list(observed, limit=12)

    def _history_starts_from_execute_build123d_snapshot(
        self,
        history: list[ActionHistoryEntry],
    ) -> bool:
        snapshot_entry = next(
            (entry for entry in reversed(history) if entry.action_type == CADActionType.SNAPSHOT),
            None,
        )
        if snapshot_entry is None:
            return False
        params = (
            snapshot_entry.action_params
            if isinstance(snapshot_entry.action_params, dict)
            else {}
        )
        return str(params.get("source") or "").strip().lower() == "execute_build123d"

    def _history_execute_build123d_code(
        self,
        history: list[ActionHistoryEntry],
    ) -> str:
        snapshot_entry = next(
            (
                entry
                for entry in reversed(history)
                if entry.action_type == CADActionType.SNAPSHOT
                and str(
                    (
                        entry.action_params.get("source")
                        if isinstance(entry.action_params, dict)
                        else ""
                    )
                    or ""
                ).strip().lower()
                == "execute_build123d"
            ),
            None,
        )
        if snapshot_entry is None:
            return ""
        params = (
            snapshot_entry.action_params
            if isinstance(snapshot_entry.action_params, dict)
            else {}
        )
        build123d_code = params.get("build123d_code")
        if not isinstance(build123d_code, str):
            return ""
        return build123d_code

    def _history_has_execute_build123d_face_revolve_recipe(
        self,
        history: list[ActionHistoryEntry],
    ) -> bool:
        code = self._history_execute_build123d_code(history)
        if not code.strip():
            return False
        normalized = re.sub(r"\s+", "", code.lower())
        has_profile = any(
            token in normalized
            for token in (
                "buildsketch(",
                "make_face(",
                "polyline(",
                "line(",
                "radiusarc(",
                "threepointarc(",
                "centerarc(",
                "rectangle(",
                "regularpolygon(",
                "circle(",
            )
        )
        has_revolve = "revolve(" in normalized
        return has_profile and has_revolve

    def _history_has_execute_build123d_path_sweep_recipe(
        self,
        history: list[ActionHistoryEntry],
    ) -> bool:
        code = self._history_execute_build123d_code(history)
        if not code.strip():
            return False
        normalized = re.sub(r"\s+", "", code.lower())
        has_sweep = "sweep(" in normalized
        has_wire = any(
            token in normalized
            for token in (
                "buildline(",
                "polyline(",
                "line(",
                "radiusarc(",
                "threepointarc(",
                "centerarc(",
                "spline(",
                "helix(",
            )
        )
        has_profile = (
            "buildsketch(" in normalized
            or "make_face(" in normalized
            or normalized.count("circle(") >= 2
        )
        has_named_plane = any(
            token in normalized
            for token in (
                "plane.xy",
                "plane.xz",
                "plane.yz",
            )
        )
        has_frame = (
            "plane(" in normalized
            or has_named_plane
            or "location(" in normalized
            or "locations(" in normalized
            or "isfrenet=" in normalized
            or "is_frenet=" in normalized
        )
        return has_sweep and has_wire and has_profile and has_frame

    def _snapshot_has_target_face_feature(
        self,
        snapshot: CADStateSnapshot,
        face_targets: tuple[str, ...],
        *,
        expect_subtractive: bool,
        expect_additive: bool,
        allow_candidate_hosts: bool = False,
        include_aligned_host_faces: bool = False,
    ) -> bool:
        if int(snapshot.geometry.solids) <= 0:
            return False
        host_faces = self._snapshot_target_host_faces(snapshot, face_targets)
        aligned_host_face_keys: set[tuple[str, str]] = set()
        if not host_faces and allow_candidate_hosts:
            host_faces = self._snapshot_candidate_host_faces(snapshot, face_targets)
        if include_aligned_host_faces and expect_additive and face_targets:
            aligned_hosts = self._snapshot_directionally_aligned_host_faces(
                snapshot,
                face_targets,
            )
            if aligned_hosts:
                aligned_host_face_keys = {
                    (self._snapshot_host_face_key(face), target_label)
                    for face, target_label in aligned_hosts
                }
                host_faces = self._merge_snapshot_host_faces(host_faces, aligned_hosts)
        if not host_faces:
            return False
        feature_faces = self._snapshot_feature_faces(snapshot)
        if not feature_faces:
            return False
        for host_face, target_label in host_faces:
            axis_index, outward_sign, plane_value = self._target_label_axis_spec(
                host_face, target_label
            )
            if axis_index is None or outward_sign == 0 or plane_value is None:
                continue
            host_is_aligned_fallback = (
                self._snapshot_host_face_key(host_face),
                target_label,
            ) in aligned_host_face_keys
            host_bbox = getattr(host_face, "bbox", None)
            if host_bbox is None:
                continue
            axis_span = float(snapshot.geometry.bbox[axis_index]) if len(snapshot.geometry.bbox) >= 3 else 0.0
            axis_tolerance = max(1e-4, abs(axis_span) * 0.03)
            for candidate in feature_faces:
                if candidate is host_face:
                    continue
                candidate_bbox = getattr(candidate, "bbox", None)
                if candidate_bbox is None:
                    continue
                if not self._snapshot_face_projects_inside_host(
                    candidate_bbox=candidate_bbox,
                    host_bbox=host_bbox,
                    axis_index=axis_index,
                    tolerance=axis_tolerance,
                ):
                    continue
                candidate_min, candidate_max = self._bbox_axis_bounds(
                    candidate_bbox,
                    axis_index,
                )
                additive_hit = False
                subtractive_hit = False
                if outward_sign > 0:
                    additive_hit = self._near_min(
                        candidate_min,
                        plane_value,
                        axis_tolerance,
                    ) and candidate_max > plane_value + axis_tolerance
                    subtractive_hit = self._near_max(
                        candidate_max,
                        plane_value,
                        axis_tolerance,
                    ) and candidate_min < plane_value - axis_tolerance
                else:
                    additive_hit = self._near_max(
                        candidate_max,
                        plane_value,
                        axis_tolerance,
                    ) and candidate_min < plane_value - axis_tolerance
                    subtractive_hit = self._near_min(
                        candidate_min,
                        plane_value,
                        axis_tolerance,
                    ) and candidate_max > plane_value + axis_tolerance
                if (
                    expect_additive
                    and additive_hit
                    and host_is_aligned_fallback
                    and not self._snapshot_feature_reaches_target_boundary(
                        snapshot=snapshot,
                        candidate_bbox=candidate_bbox,
                        axis_index=axis_index,
                        outward_sign=outward_sign,
                        tolerance=axis_tolerance,
                    )
                ):
                    additive_hit = False
                if expect_additive and additive_hit:
                    return True
                if expect_subtractive and subtractive_hit:
                    return True
                if not expect_additive and not expect_subtractive and (
                    additive_hit or subtractive_hit
                ):
                    return True
        return False

    def _snapshot_feature_reaches_target_boundary(
        self,
        *,
        snapshot: CADStateSnapshot,
        candidate_bbox: BoundingBox3D,
        axis_index: int,
        outward_sign: int,
        tolerance: float,
    ) -> bool:
        bbox_min = snapshot.geometry.bbox_min if isinstance(snapshot.geometry.bbox_min, list) else None
        bbox_max = snapshot.geometry.bbox_max if isinstance(snapshot.geometry.bbox_max, list) else None
        if not (
            isinstance(bbox_min, list)
            and isinstance(bbox_max, list)
            and len(bbox_min) > axis_index
            and len(bbox_max) > axis_index
        ):
            return True
        candidate_min, candidate_max = self._bbox_axis_bounds(
            candidate_bbox,
            axis_index,
        )
        if outward_sign > 0:
            return self._near_max(candidate_max, float(bbox_max[axis_index]), tolerance)
        return self._near_min(candidate_min, float(bbox_min[axis_index]), tolerance)

    def _merge_snapshot_host_faces(
        self,
        host_faces: list[tuple[Any, str]],
        extra_host_faces: list[tuple[Any, str]],
    ) -> list[tuple[Any, str]]:
        merged = list(host_faces)
        seen: set[tuple[str, str]] = set()
        for face, target_label in host_faces:
            seen.add((self._snapshot_host_face_key(face), target_label))
        for face, target_label in extra_host_faces:
            key = (self._snapshot_host_face_key(face), target_label)
            if key in seen:
                continue
            seen.add(key)
            merged.append((face, target_label))
        return merged

    def _snapshot_host_face_key(self, face: Any) -> str:
        for attr in ("face_ref", "face_id"):
            value = getattr(face, attr, None)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return f"face:{id(face)}"

    def _snapshot_directionally_aligned_host_faces(
        self,
        snapshot: CADStateSnapshot,
        face_targets: tuple[str, ...],
    ) -> list[tuple[Any, str]]:
        aligned_targets: list[str] = []
        for target in face_targets:
            if target in {"top", "bottom", "front", "back", "left", "right"}:
                aligned_targets.append(target)
            elif target == "side":
                aligned_targets.extend(["front", "back", "left", "right"])
        if not aligned_targets:
            return []
        topology_index = snapshot.topology_index
        if topology_index is not None and topology_index.faces:
            faces = list(topology_index.faces)
        else:
            geometry_objects = snapshot.geometry_objects
            faces = list(geometry_objects.faces) if geometry_objects is not None else []
        matches: list[tuple[Any, str]] = []
        for face in faces:
            if str(getattr(face, "geom_type", "")).strip().upper() != "PLANE":
                continue
            for target in aligned_targets:
                if self._snapshot_face_matches_target_direction(face, target):
                    matches.append((face, target))
                    break
        return matches

    def _snapshot_face_matches_target_direction(
        self,
        face: Any,
        target_label: str,
    ) -> bool:
        direction = getattr(face, "normal", None)
        if (
            not isinstance(direction, list)
            or len(direction) < 3
            or not all(isinstance(item, (int, float)) for item in direction[:3])
        ):
            direction = getattr(face, "axis_direction", None)
        if (
            not isinstance(direction, list)
            or len(direction) < 3
            or not all(isinstance(item, (int, float)) for item in direction[:3])
        ):
            return False
        axis_specs = {
            "top": (2, 1.0),
            "bottom": (2, -1.0),
            "front": (1, 1.0),
            "back": (1, -1.0),
            "right": (0, 1.0),
            "left": (0, -1.0),
        }
        axis_spec = axis_specs.get(target_label)
        if axis_spec is None:
            return False
        axis_index, expected_sign = axis_spec
        component = float(direction[axis_index])
        return component * expected_sign >= 0.7

    def _snapshot_has_merged_subtractive_face_feature(
        self,
        snapshot: CADStateSnapshot,
        face_targets: tuple[str, ...],
    ) -> bool:
        if int(snapshot.geometry.solids) != 1:
            return False
        volume = snapshot.geometry.volume
        if not isinstance(volume, (int, float)) or float(volume) <= 1e-6:
            return False
        return self._snapshot_has_target_face_feature(
            snapshot=snapshot,
            face_targets=face_targets,
            expect_subtractive=True,
            expect_additive=False,
        )

    def _snapshot_has_hole_like_feature(
        self,
        snapshot: CADStateSnapshot,
        face_targets: tuple[str, ...],
    ) -> bool:
        return bool(
            self._snapshot_collect_subtractive_feature_centers(
                snapshot=snapshot,
                face_targets=face_targets,
            )
        )

    def _snapshot_has_countersink_cone_feature(
        self,
        snapshot: CADStateSnapshot,
        face_targets: tuple[str, ...],
    ) -> bool:
        return bool(
            self._snapshot_collect_subtractive_feature_centers(
                snapshot=snapshot,
                face_targets=face_targets,
                geom_types={"CONE"},
            )
        )

    def _snapshot_collect_subtractive_feature_centers(
        self,
        snapshot: CADStateSnapshot,
        face_targets: tuple[str, ...],
        geom_types: set[str] | None = None,
    ) -> list[list[float]]:
        if int(snapshot.geometry.solids) <= 0:
            return []
        host_faces = self._snapshot_candidate_host_faces(snapshot, face_targets)
        if not host_faces:
            return []
        feature_faces = self._snapshot_feature_faces(snapshot)
        if not feature_faces:
            return []
        allowed_geom_types = {
            str(item).strip().upper()
            for item in (geom_types or {"CYLINDER", "CONE", "SPHERE"})
            if str(item).strip()
        }
        centers: list[list[float]] = []
        for host_face, target_label in host_faces:
            axis_index, outward_sign, plane_value = self._target_label_axis_spec(
                host_face, target_label
            )
            if axis_index is None or outward_sign == 0 or plane_value is None:
                continue
            host_bbox = getattr(host_face, "bbox", None)
            if host_bbox is None:
                continue
            host_parent_solid_id = str(getattr(host_face, "parent_solid_id", "") or "").strip()
            axis_span = (
                float(snapshot.geometry.bbox[axis_index])
                if len(snapshot.geometry.bbox) >= 3
                else 0.0
            )
            axis_tolerance = max(1e-4, abs(axis_span) * 0.03)
            host_in_plane_spans = self._bbox_in_plane_spans(host_bbox, axis_index)
            radial_limit = (
                max(1e-4, min(host_in_plane_spans) * 0.49)
                if host_in_plane_spans
                else None
            )
            for candidate in feature_faces:
                if candidate is host_face:
                    continue
                candidate_bbox = getattr(candidate, "bbox", None)
                if candidate_bbox is None:
                    continue
                candidate_parent_solid_id = str(
                    getattr(candidate, "parent_solid_id", "") or ""
                ).strip()
                if (
                    host_parent_solid_id
                    and candidate_parent_solid_id
                    and candidate_parent_solid_id != host_parent_solid_id
                ):
                    continue
                if not self._snapshot_face_projects_inside_host(
                    candidate_bbox=candidate_bbox,
                    host_bbox=host_bbox,
                    axis_index=axis_index,
                    tolerance=axis_tolerance,
                ):
                    continue
                if not self._snapshot_face_is_inward_from_host_plane(
                    candidate_bbox=candidate_bbox,
                    axis_index=axis_index,
                    outward_sign=outward_sign,
                    plane_value=plane_value,
                    tolerance=axis_tolerance,
                ):
                    continue
                geom_type = str(getattr(candidate, "geom_type", "")).strip().upper()
                if geom_type not in allowed_geom_types:
                    continue
                if geom_type in {"CYLINDER", "CONE"} and not self._face_axis_matches_index(
                    candidate,
                    axis_index,
                ):
                    continue
                radius_value = getattr(candidate, "radius", None)
                if (
                    radial_limit is not None
                    and isinstance(radius_value, (int, float))
                    and float(radius_value) > radial_limit
                ):
                    continue
                center_point = self._snapshot_feature_center_point(candidate)
                if geom_type == "SPHERE":
                    center_point = self._snapshot_infer_spherical_feature_center_on_host_plane(
                        face=candidate,
                        axis_index=axis_index,
                        plane_value=plane_value,
                    )
                    inferred_radius = self._snapshot_infer_spherical_feature_radius(
                        candidate,
                    )
                    if (
                        radial_limit is not None
                        and isinstance(inferred_radius, (int, float))
                        and float(inferred_radius) > radial_limit
                    ):
                        continue
                    if not self._snapshot_sphere_opens_on_host_plane(
                        center_point=center_point,
                        radius_value=inferred_radius,
                        axis_index=axis_index,
                        plane_value=plane_value,
                        tolerance=axis_tolerance,
                    ):
                        continue
                projected = self._project_point_to_target_face_2d(
                    center_point,
                    target_label,
                )
                if projected is None or projected in centers:
                    continue
                centers.append(projected)
        if not centers and "SPHERE" in allowed_geom_types:
            for point in self._snapshot_collect_host_plane_circle_edge_centers(
                snapshot=snapshot,
                face_targets=face_targets,
            ):
                if point not in centers:
                    centers.append(point)
        return centers

    def _snapshot_candidate_host_faces(
        self,
        snapshot: CADStateSnapshot,
        face_targets: tuple[str, ...],
    ) -> list[tuple[Any, str]]:
        if face_targets:
            return self._snapshot_target_host_faces(snapshot, face_targets)
        topology_index = snapshot.topology_index
        matches: list[tuple[Any, str]] = []
        if topology_index is not None and topology_index.faces:
            for face in topology_index.faces:
                if str(getattr(face, "geom_type", "")).strip().upper() != "PLANE":
                    continue
                labels = self._topology_face_labels(topology_index, face)
                target_label = self._select_snapshot_face_target_label(labels, ())
                if target_label is None:
                    continue
                matches.append((face, target_label))
            if matches:
                return self._prefer_largest_host_faces(matches)
        geometry_objects = snapshot.geometry_objects
        if geometry_objects is None or not geometry_objects.faces:
            return []
        for face in geometry_objects.faces:
            if str(getattr(face, "geom_type", "")).strip().upper() != "PLANE":
                continue
            labels = self._geometry_face_labels(geometry_objects, face)
            target_label = self._select_snapshot_face_target_label(labels, ())
            if target_label is None:
                continue
            matches.append((face, target_label))
        return self._prefer_largest_host_faces(matches)

    def _bbox_in_plane_spans(
        self,
        bbox: BoundingBox3D,
        axis_index: int,
    ) -> list[float]:
        spans = [
            abs(float(bbox.xlen)),
            abs(float(bbox.ylen)),
            abs(float(bbox.zlen)),
        ]
        return [
            span for index, span in enumerate(spans) if index != axis_index and span > 1e-6
        ]

    def _snapshot_face_is_inward_from_host_plane(
        self,
        *,
        candidate_bbox: BoundingBox3D,
        axis_index: int,
        outward_sign: int,
        plane_value: float,
        tolerance: float,
    ) -> bool:
        candidate_min, candidate_max = self._bbox_axis_bounds(
            candidate_bbox,
            axis_index,
        )
        if outward_sign > 0:
            return self._near_max(
                candidate_max,
                plane_value,
                tolerance,
            ) and candidate_min < plane_value - tolerance
        return self._near_min(
            candidate_min,
            plane_value,
            tolerance,
        ) and candidate_max > plane_value + tolerance

    def _face_axis_matches_index(
        self,
        face: Any,
        axis_index: int,
    ) -> bool:
        axis_direction = getattr(face, "axis_direction", None)
        if (
            not isinstance(axis_direction, list)
            or len(axis_direction) < 3
            or not all(isinstance(item, (int, float)) for item in axis_direction[:3])
        ):
            return True
        dominant = max(
            range(3),
            key=lambda idx: abs(float(axis_direction[idx])),
        )
        return dominant == axis_index and abs(float(axis_direction[dominant])) >= 0.7

    def _snapshot_feature_center_point(
        self,
        face: Any,
    ) -> list[float] | None:
        axis_origin = getattr(face, "axis_origin", None)
        if (
            isinstance(axis_origin, list)
            and len(axis_origin) >= 3
            and all(isinstance(item, (int, float)) for item in axis_origin[:3])
        ):
            return [float(axis_origin[0]), float(axis_origin[1]), float(axis_origin[2])]

        geom_type = str(getattr(face, "geom_type", "")).strip().upper()
        bbox = getattr(face, "bbox", None)
        if geom_type in {"CYLINDER", "CONE"} and bbox is not None:
            return [
                round((float(bbox.xmin) + float(bbox.xmax)) / 2.0, 6),
                round((float(bbox.ymin) + float(bbox.ymax)) / 2.0, 6),
                round((float(bbox.zmin) + float(bbox.zmax)) / 2.0, 6),
            ]

        for raw_point in (getattr(face, "center", None),):
            if (
                isinstance(raw_point, list)
                and len(raw_point) >= 3
                and all(isinstance(item, (int, float)) for item in raw_point[:3])
            ):
                return [float(raw_point[0]), float(raw_point[1]), float(raw_point[2])]
        return None

    def _extract_requirement_outer_face_hint(
        self,
        requirement_text: str | None,
    ) -> tuple[str, int, int] | None:
        text = str(requirement_text or "").strip().lower()
        if not text:
            return None
        face_map = {
            "top": (2, 1),
            "bottom": (2, -1),
            "front": (1, 1),
            "back": (1, -1),
            "right": (0, 1),
            "left": (0, -1),
        }
        matches = list(
            re.finditer(
                r"\b(top|bottom|front|back|left|right)\s+(?:surface|face)\b",
                text,
                re.IGNORECASE,
            )
        )
        if not matches:
            return None
        last_match = matches[-1]
        label = str(last_match.group(1)).lower()
        axis_index, outward_sign = face_map[label]
        return label, axis_index, outward_sign

    def _snapshot_infer_cylindrical_slot_reference_point(
        self,
        *,
        face: Any,
        axis_index: int,
        solid_bbox: BoundingBox3D | None,
        radius_value: float | None,
        requirement_text: str | None,
    ) -> list[float] | None:
        bbox = getattr(face, "bbox", None)
        if bbox is None:
            return self._snapshot_feature_center_point(face)

        reference_point = [
            round((float(bbox.xmin) + float(bbox.xmax)) / 2.0, 6),
            round((float(bbox.ymin) + float(bbox.ymax)) / 2.0, 6),
            round((float(bbox.zmin) + float(bbox.zmax)) / 2.0, 6),
        ]

        axis_origin = getattr(face, "axis_origin", None)
        if (
            isinstance(axis_origin, list)
            and len(axis_origin) >= 3
            and all(isinstance(item, (int, float)) for item in axis_origin[:3])
        ):
            for idx in range(3):
                if idx == axis_index:
                    continue
                axis_min, axis_max = self._bbox_axis_bounds(bbox, idx)
                axis_tolerance = self._extent_tolerance(axis_min, axis_max)
                origin_value = float(axis_origin[idx])
                if axis_min - axis_tolerance <= origin_value <= axis_max + axis_tolerance:
                    reference_point[idx] = round(origin_value, 6)

        outer_face_hint = self._extract_requirement_outer_face_hint(requirement_text)
        if (
            outer_face_hint is not None
            and solid_bbox is not None
            and isinstance(radius_value, (int, float))
            and float(radius_value) > 0.0
        ):
            _label, host_axis_index, outward_sign = outer_face_hint
            if host_axis_index != axis_index:
                solid_axis_min, solid_axis_max = self._bbox_axis_bounds(solid_bbox, host_axis_index)
                face_axis_min, face_axis_max = self._bbox_axis_bounds(bbox, host_axis_index)
                host_tolerance = self._extent_tolerance(solid_axis_min, solid_axis_max)
                radius = float(radius_value)
                if outward_sign > 0 and self._near_max(face_axis_max, solid_axis_max, host_tolerance):
                    reference_point[host_axis_index] = round(solid_axis_max - radius, 6)
                elif outward_sign < 0 and self._near_min(face_axis_min, solid_axis_min, host_tolerance):
                    reference_point[host_axis_index] = round(solid_axis_min + radius, 6)

        return reference_point

    def _snapshot_feature_surface_center_point(
        self,
        face: Any,
    ) -> list[float] | None:
        raw_point = getattr(face, "center", None)
        if (
            isinstance(raw_point, list)
            and len(raw_point) >= 3
            and all(isinstance(item, (int, float)) for item in raw_point[:3])
        ):
            return [float(raw_point[0]), float(raw_point[1]), float(raw_point[2])]

        bbox = getattr(face, "bbox", None)
        if bbox is None:
            return None
        return [
            round((float(bbox.xmin) + float(bbox.xmax)) / 2.0, 6),
            round((float(bbox.ymin) + float(bbox.ymax)) / 2.0, 6),
            round((float(bbox.zmin) + float(bbox.zmax)) / 2.0, 6),
        ]

    def _snapshot_sphere_opens_on_host_plane(
        self,
        *,
        center_point: list[float] | None,
        radius_value: float | None,
        axis_index: int,
        plane_value: float,
        tolerance: float,
    ) -> bool:
        if (
            center_point is None
            or len(center_point) <= axis_index
            or not isinstance(radius_value, (int, float))
        ):
            return False
        radius = float(radius_value)
        if radius <= tolerance:
            return False
        plane_offset = abs(float(center_point[axis_index]) - plane_value)
        return plane_offset < radius - tolerance

    def _snapshot_infer_spherical_feature_center_on_host_plane(
        self,
        *,
        face: Any,
        axis_index: int,
        plane_value: float,
    ) -> list[float] | None:
        bbox = getattr(face, "bbox", None)
        if bbox is None:
            return self._snapshot_feature_center_point(face)
        inferred = [
            round((float(bbox.xmin) + float(bbox.xmax)) / 2.0, 6),
            round((float(bbox.ymin) + float(bbox.ymax)) / 2.0, 6),
            round((float(bbox.zmin) + float(bbox.zmax)) / 2.0, 6),
        ]
        if 0 <= axis_index < len(inferred):
            inferred[axis_index] = round(float(plane_value), 6)
        return inferred

    def _project_point_to_target_face_2d(
        self,
        point: list[float] | None,
        target_label: str,
    ) -> list[float] | None:
        if point is None or len(point) < 3:
            return None
        if target_label in {"top", "bottom"}:
            return [round(float(point[0]), 6), round(float(point[1]), 6)]
        if target_label in {"front", "back"}:
            return [round(float(point[0]), 6), round(float(point[2]), 6)]
        if target_label in {"left", "right"}:
            return [round(float(point[1]), 6), round(float(point[2]), 6)]
        return None

    def _snapshot_target_host_faces(
        self,
        snapshot: CADStateSnapshot,
        face_targets: tuple[str, ...],
    ) -> list[tuple[Any, str]]:
        topology_index = snapshot.topology_index
        if topology_index is not None and topology_index.faces:
            matches: list[tuple[Any, str]] = []
            for face in topology_index.faces:
                if str(getattr(face, "geom_type", "")).strip().upper() != "PLANE":
                    continue
                labels = self._topology_face_labels(topology_index, face)
                if not self._labels_match_face_targets(labels, face_targets):
                    continue
                target_label = self._select_snapshot_face_target_label(
                    labels,
                    face_targets,
                )
                if target_label is not None:
                    matches.append((face, target_label))
            if matches:
                return self._prefer_largest_host_faces(matches)
        geometry_objects = snapshot.geometry_objects
        if geometry_objects is not None and geometry_objects.faces:
            matches = []
            for face in geometry_objects.faces:
                if str(getattr(face, "geom_type", "")).strip().upper() != "PLANE":
                    continue
                labels = self._geometry_face_labels(geometry_objects, face)
                if not self._labels_match_face_targets(labels, face_targets):
                    continue
                target_label = self._select_snapshot_face_target_label(
                    labels,
                    face_targets,
                )
                if target_label is not None:
                    matches.append((face, target_label))
            if matches:
                return self._prefer_largest_host_faces(matches)
        return []

    def _snapshot_feature_faces(
        self,
        snapshot: CADStateSnapshot,
    ) -> list[Any]:
        topology_index = snapshot.topology_index
        if topology_index is not None and topology_index.faces:
            return list(topology_index.faces)
        geometry_objects = snapshot.geometry_objects
        if geometry_objects is not None and geometry_objects.faces:
            return list(geometry_objects.faces)
        return []

    def _snapshot_feature_edges(
        self,
        snapshot: CADStateSnapshot,
    ) -> list[Any]:
        topology_index = snapshot.topology_index
        if topology_index is not None and topology_index.edges:
            return list(topology_index.edges)
        geometry_objects = snapshot.geometry_objects
        if geometry_objects is not None and geometry_objects.edges:
            return list(geometry_objects.edges)
        return []

    def _snapshot_infer_spherical_feature_radius(
        self,
        face: Any,
    ) -> float | None:
        radius_value = getattr(face, "radius", None)
        if isinstance(radius_value, (int, float)) and float(radius_value) > 0.0:
            return float(radius_value)
        bbox = getattr(face, "bbox", None)
        if bbox is None:
            return None
        spans = [
            abs(float(getattr(bbox, "xlen", 0.0))),
            abs(float(getattr(bbox, "ylen", 0.0))),
            abs(float(getattr(bbox, "zlen", 0.0))),
        ]
        positive_spans = [span for span in spans if span > 1e-6]
        if not positive_spans:
            return None
        return max(positive_spans) / 2.0

    def _snapshot_edge_center_point(
        self,
        edge: Any,
    ) -> list[float] | None:
        for raw_point in (
            getattr(edge, "axis_origin", None),
            getattr(edge, "center", None),
        ):
            if (
                isinstance(raw_point, list)
                and len(raw_point) >= 3
                and all(isinstance(item, (int, float)) for item in raw_point[:3])
            ):
                return [float(raw_point[0]), float(raw_point[1]), float(raw_point[2])]
        return None

    def _snapshot_edge_lies_on_host_plane(
        self,
        *,
        edge: Any,
        axis_index: int,
        plane_value: float,
        tolerance: float,
    ) -> bool:
        center_point = self._snapshot_edge_center_point(edge)
        if center_point is not None and len(center_point) > axis_index:
            if abs(float(center_point[axis_index]) - plane_value) <= tolerance:
                return True
        bbox = getattr(edge, "bbox", None)
        if bbox is None:
            return False
        edge_min, edge_max = self._bbox_axis_bounds(bbox, axis_index)
        return abs(edge_min - plane_value) <= tolerance and abs(edge_max - plane_value) <= tolerance

    def _snapshot_collect_host_plane_circle_edge_centers(
        self,
        *,
        snapshot: CADStateSnapshot,
        face_targets: tuple[str, ...],
    ) -> list[list[float]]:
        if int(snapshot.geometry.solids) <= 0:
            return []
        host_faces = self._snapshot_candidate_host_faces(snapshot, face_targets)
        if not host_faces:
            return []
        feature_edges = self._snapshot_feature_edges(snapshot)
        if not feature_edges:
            return []
        centers: list[list[float]] = []
        for host_face, target_label in host_faces:
            axis_index, _outward_sign, plane_value = self._target_label_axis_spec(
                host_face,
                target_label,
            )
            if axis_index is None or plane_value is None:
                continue
            host_bbox = getattr(host_face, "bbox", None)
            if host_bbox is None:
                continue
            host_parent_solid_id = str(getattr(host_face, "parent_solid_id", "") or "").strip()
            axis_span = (
                float(snapshot.geometry.bbox[axis_index])
                if len(snapshot.geometry.bbox) >= 3
                else 0.0
            )
            axis_tolerance = max(1e-4, abs(axis_span) * 0.03)
            for candidate in feature_edges:
                if str(getattr(candidate, "geom_type", "")).strip().upper() != "CIRCLE":
                    continue
                candidate_parent_solid_id = str(
                    getattr(candidate, "parent_solid_id", "") or ""
                ).strip()
                if (
                    host_parent_solid_id
                    and candidate_parent_solid_id
                    and candidate_parent_solid_id != host_parent_solid_id
                ):
                    continue
                candidate_bbox = getattr(candidate, "bbox", None)
                if candidate_bbox is None:
                    continue
                if not self._snapshot_face_projects_inside_host(
                    candidate_bbox=candidate_bbox,
                    host_bbox=host_bbox,
                    axis_index=axis_index,
                    tolerance=axis_tolerance,
                ):
                    continue
                if not self._snapshot_edge_lies_on_host_plane(
                    edge=candidate,
                    axis_index=axis_index,
                    plane_value=plane_value,
                    tolerance=axis_tolerance,
                ):
                    continue
                projected = self._project_point_to_target_face_2d(
                    self._snapshot_edge_center_point(candidate),
                    target_label,
                )
                if projected is None or projected in centers:
                    continue
                centers.append(projected)
        return centers

    def _select_snapshot_face_target_label(
        self,
        labels: set[str],
        face_targets: tuple[str, ...],
    ) -> str | None:
        ordered_targets = ("top", "bottom", "front", "back", "left", "right")
        for target in ordered_targets:
            if target in face_targets and target in labels:
                return target
        if "side" in face_targets:
            for target in ("front", "back", "left", "right"):
                if target in labels:
                    return target
        if "existing" in face_targets:
            for target in ordered_targets:
                if target in labels:
                    return target
        for target in ordered_targets:
            if target in labels:
                return target
        return None

    def _target_label_axis_spec(
        self,
        face: Any,
        target_label: str,
    ) -> tuple[int | None, int, float | None]:
        bbox = getattr(face, "bbox", None)
        if bbox is None:
            return None, 0, None
        if target_label == "top":
            return 2, 1, float(bbox.zmax)
        if target_label == "bottom":
            return 2, -1, float(bbox.zmin)
        if target_label == "front":
            return 1, 1, float(bbox.ymax)
        if target_label == "back":
            return 1, -1, float(bbox.ymin)
        if target_label == "right":
            return 0, 1, float(bbox.xmax)
        if target_label == "left":
            return 0, -1, float(bbox.xmin)
        return None, 0, None

    def _snapshot_face_projects_inside_host(
        self,
        *,
        candidate_bbox: BoundingBox3D,
        host_bbox: BoundingBox3D,
        axis_index: int,
        tolerance: float,
    ) -> bool:
        if axis_index != 0:
            if candidate_bbox.xmax < host_bbox.xmin - tolerance or candidate_bbox.xmin > host_bbox.xmax + tolerance:
                return False
        if axis_index != 1:
            if candidate_bbox.ymax < host_bbox.ymin - tolerance or candidate_bbox.ymin > host_bbox.ymax + tolerance:
                return False
        if axis_index != 2:
            if candidate_bbox.zmax < host_bbox.zmin - tolerance or candidate_bbox.zmin > host_bbox.zmax + tolerance:
                return False
        return True

    def _bbox_axis_bounds(
        self,
        bbox: BoundingBox3D,
        axis_index: int,
    ) -> tuple[float, float]:
        if axis_index == 0:
            return float(bbox.xmin), float(bbox.xmax)
        if axis_index == 1:
            return float(bbox.ymin), float(bbox.ymax)
        return float(bbox.zmin), float(bbox.zmax)

    def _geometry_face_labels(
        self,
        geometry_objects: GeometryObjectIndex,
        face: FaceEntity,
    ) -> set[str]:
        if not geometry_objects.faces:
            return set()
        scope_faces = self._faces_for_parent_solid_scope(
            geometry_objects.faces,
            parent_solid_id=getattr(face, "parent_solid_id", None),
        )
        boxes = [
            item.bbox
            for item in (scope_faces or geometry_objects.faces)
            if item.bbox is not None
        ]
        if not boxes:
            return set()
        min_x = min(item.xmin for item in boxes)
        max_x = max(item.xmax for item in boxes)
        min_y = min(item.ymin for item in boxes)
        max_y = max(item.ymax for item in boxes)
        min_z = min(item.zmin for item in boxes)
        max_z = max(item.zmax for item in boxes)
        x_tol = self._extent_tolerance(min_x, max_x)
        y_tol = self._extent_tolerance(min_y, max_y)
        z_tol = self._extent_tolerance(min_z, max_z)
        labels: set[str] = set()
        if isinstance(face.normal, list) and len(face.normal) >= 3:
            nz = face.normal[2]
            if isinstance(nz, (int, float)):
                if float(nz) >= 0.7 and self._near_max(face.bbox.zmax, max_z, z_tol):
                    labels.add("top")
                if float(nz) <= -0.7 and self._near_min(face.bbox.zmin, min_z, z_tol):
                    labels.add("bottom")
            ny = face.normal[1]
            if isinstance(ny, (int, float)):
                if float(ny) >= 0.7 and self._near_max(face.bbox.ymax, max_y, y_tol):
                    labels.add("front")
                if float(ny) <= -0.7 and self._near_min(face.bbox.ymin, min_y, y_tol):
                    labels.add("back")
            nx = face.normal[0]
            if isinstance(nx, (int, float)):
                if float(nx) >= 0.7 and self._near_max(face.bbox.xmax, max_x, x_tol):
                    labels.add("right")
                if float(nx) <= -0.7 and self._near_min(face.bbox.xmin, min_x, x_tol):
                    labels.add("left")
        if self._near_max(face.center[2], max_z, z_tol):
            labels.add("top")
        if self._near_min(face.center[2], min_z, z_tol):
            labels.add("bottom")
        if self._near_max(face.center[1], max_y, y_tol):
            labels.add("front")
        if self._near_min(face.center[1], min_y, y_tol):
            labels.add("back")
        if self._near_max(face.center[0], max_x, x_tol):
            labels.add("right")
        if self._near_min(face.center[0], min_x, x_tol):
            labels.add("left")
        return labels

    def _faces_for_parent_solid_scope(
        self,
        faces: list[Any] | tuple[Any, ...],
        *,
        parent_solid_id: Any,
    ) -> list[Any]:
        solid_id = str(parent_solid_id or "").strip()
        if not solid_id:
            return []
        return [
            face
            for face in faces
            if str(getattr(face, "parent_solid_id", "") or "").strip() == solid_id
        ]

    def _prefer_largest_host_faces(
        self,
        matches: list[tuple[Any, str]],
    ) -> list[tuple[Any, str]]:
        if len(matches) <= 1:
            return matches
        best_area_by_target: dict[str, float] = {}
        for face, target_label in matches:
            area = float(getattr(face, "area", 0.0) or 0.0)
            current = best_area_by_target.get(target_label)
            if current is None or area > current:
                best_area_by_target[target_label] = area
        filtered: list[tuple[Any, str]] = []
        for face, target_label in matches:
            area = float(getattr(face, "area", 0.0) or 0.0)
            best_area = float(best_area_by_target.get(target_label, area))
            if area >= max(1e-6, best_area * 0.98):
                filtered.append((face, target_label))
        return filtered or matches

    def _snapshot_has_direct_repeated_feature_pattern(
        self,
        snapshot: CADStateSnapshot,
    ) -> bool:
        return len(self._snapshot_collect_round_feature_centers(snapshot)) >= 2

    def _snapshot_collect_round_feature_centers(
        self,
        snapshot: CADStateSnapshot,
    ) -> list[tuple[float, float, float]]:
        faces = self._snapshot_feature_faces(snapshot)
        centers: list[tuple[float, float, float]] = []
        for face in faces:
            if str(getattr(face, "geom_type", "")).strip().upper() != "CYLINDER":
                continue
            center_value = getattr(face, "axis_origin", None) or getattr(face, "center", None)
            if (
                not isinstance(center_value, list)
                or len(center_value) < 3
                or not all(isinstance(item, (int, float)) for item in center_value[:3])
            ):
                continue
            rounded_center = (
                round(float(center_value[0]), 4),
                round(float(center_value[1]), 4),
                round(float(center_value[2]), 4),
            )
            if rounded_center not in centers:
                centers.append(rounded_center)
        return centers

    def _snapshot_has_matching_profile_shape(
        self,
        snapshot: CADStateSnapshot,
        targets: tuple[str, ...],
    ) -> tuple[bool, list[str]]:
        if not targets:
            return False, []
        observed: list[str] = []
        topology_index = snapshot.topology_index
        if topology_index is not None and topology_index.faces:
            candidate_faces = sorted(
                (
                    face
                    for face in topology_index.faces
                    if str(face.geom_type or "").upper() in {"PLANE", "CYLINDER"}
                ),
                key=lambda item: float(item.area),
                reverse=True,
            )
            for face in candidate_faces[:16]:
                inferred_shapes = self._infer_shapes_from_topology_face(face)
                for shape in inferred_shapes:
                    if shape not in observed:
                        observed.append(shape)
                if any(shape in targets for shape in inferred_shapes):
                    return True, self._normalize_string_list(observed, limit=12)
        geometry_objects = snapshot.geometry_objects
        if geometry_objects is not None and geometry_objects.faces:
            candidate_faces = sorted(
                (
                    face
                    for face in geometry_objects.faces
                    if str(face.geom_type or "").upper() in {"PLANE", "CYLINDER"}
                ),
                key=lambda item: float(item.area),
                reverse=True,
            )
            for face in candidate_faces[:16]:
                inferred_shapes = self._infer_shapes_from_geometry_face(face)
                for shape in inferred_shapes:
                    if shape not in observed:
                        observed.append(shape)
                if any(shape in targets for shape in inferred_shapes):
                    return True, self._normalize_string_list(observed, limit=12)
        inferred_shapes = self._infer_shapes_from_geometry_summary(snapshot)
        for shape in inferred_shapes:
            if shape not in observed:
                observed.append(shape)
        if any(shape in targets for shape in inferred_shapes):
            return True, self._normalize_string_list(observed, limit=12)
        return False, self._normalize_string_list(observed, limit=12)

    def _face_normal_axis_index(
        self,
        face: Any,
    ) -> int | None:
        normal = getattr(face, "normal", None)
        if (
            isinstance(normal, list)
            and len(normal) >= 3
            and all(isinstance(item, (int, float)) for item in normal[:3])
        ):
            dominant = max(
                range(3),
                key=lambda idx: abs(float(normal[idx])),
            )
            if abs(float(normal[dominant])) >= 0.7:
                return dominant
        return None

    def _infer_shapes_from_snapshot_face(
        self,
        face: Any,
    ) -> list[str]:
        if isinstance(face, TopologyFaceEntity):
            return self._infer_shapes_from_topology_face(face)
        if isinstance(face, FaceEntity):
            return self._infer_shapes_from_geometry_face(face)
        return []

    def _snapshot_loft_transition_signature(
        self,
        snapshot: CADStateSnapshot,
    ) -> dict[str, Any] | None:
        faces = self._snapshot_feature_faces(snapshot)
        if not faces:
            return None
        global_bounds = self._snapshot_global_bbox_bounds(snapshot)
        axis_candidates: list[tuple[int, float, int, float, int]] = []
        for axis_index in range(3):
            min_bound = global_bounds[axis_index * 2]
            max_bound = global_bounds[axis_index * 2 + 1]
            tolerance = self._extent_tolerance(min_bound, max_bound)
            min_caps = 0
            max_caps = 0
            min_cap_area = 0.0
            max_cap_area = 0.0
            for face in faces:
                if str(getattr(face, "geom_type", "")).strip().upper() != "PLANE":
                    continue
                bbox = getattr(face, "bbox", None)
                if bbox is None:
                    continue
                if self._face_normal_axis_index(face) != axis_index:
                    continue
                face_min, face_max = self._bbox_axis_bounds(bbox, axis_index)
                face_area = float(getattr(face, "area", 0.0) or 0.0)
                if self._near_min(face_min, min_bound, tolerance):
                    min_caps += 1
                    min_cap_area += face_area
                if self._near_max(face_max, max_bound, tolerance):
                    max_caps += 1
                    max_cap_area += face_area
            axis_candidates.append(
                (
                    min(1, min_caps) + min(1, max_caps),
                    min_cap_area + max_cap_area,
                    min_caps + max_caps,
                    abs(max_bound - min_bound),
                    axis_index,
                )
            )
        axis_candidates.sort(reverse=True)
        if not axis_candidates or axis_candidates[0][0] <= 0:
            return None
        axis_index = axis_candidates[0][4]
        min_bound = global_bounds[axis_index * 2]
        max_bound = global_bounds[axis_index * 2 + 1]
        axis_span = abs(max_bound - min_bound)
        axis_tolerance = self._extent_tolerance(min_bound, max_bound)
        lower_transition_limit = min_bound + axis_span * 0.6
        min_cap_faces = 0
        max_cap_faces = 0
        cap_shapes: list[str] = []
        bspline_transition_faces = 0
        cone_transition_faces = 0
        lower_planar_facets = 0
        for face in faces:
            bbox = getattr(face, "bbox", None)
            if bbox is None:
                continue
            geom_type = str(getattr(face, "geom_type", "")).strip().upper()
            spans = [
                abs(float(bbox.xlen)),
                abs(float(bbox.ylen)),
                abs(float(bbox.zlen)),
            ]
            face_axis_span = spans[axis_index]
            center = getattr(face, "center", None)
            center_axis = (
                float(center[axis_index])
                if isinstance(center, list)
                and len(center) > axis_index
                and isinstance(center[axis_index], (int, float))
                else (min_bound + max_bound) / 2.0
            )
            normal_axis = self._face_normal_axis_index(face)
            is_cap_face = (
                geom_type == "PLANE"
                and normal_axis == axis_index
                and face_axis_span <= axis_tolerance
            )
            if is_cap_face:
                face_min, face_max = self._bbox_axis_bounds(bbox, axis_index)
                if self._near_min(face_min, min_bound, axis_tolerance):
                    min_cap_faces += 1
                if self._near_max(face_max, max_bound, axis_tolerance):
                    max_cap_faces += 1
                for shape in self._infer_shapes_from_snapshot_face(face):
                    if shape not in cap_shapes:
                        cap_shapes.append(shape)
                continue
            if face_axis_span < max(axis_span * 0.25, 1.0):
                continue
            if geom_type == "BSPLINE":
                bspline_transition_faces += 1
                continue
            if geom_type == "CONE":
                cone_transition_faces += 1
                continue
            if (
                geom_type == "PLANE"
                and normal_axis is not None
                and normal_axis != axis_index
                and center_axis <= lower_transition_limit
            ):
                lower_planar_facets += 1
        return {
            "axis_index": axis_index,
            "axis_span": axis_span,
            "min_cap_faces": min_cap_faces,
            "max_cap_faces": max_cap_faces,
            "cap_shapes": self._normalize_string_list(cap_shapes, limit=12),
            "bspline_transition_faces": bspline_transition_faces,
            "cone_transition_faces": cone_transition_faces,
            "lower_planar_facets": lower_planar_facets,
        }

    def _snapshot_has_requirement_aligned_loft_transition(
        self,
        *,
        snapshot: CADStateSnapshot,
        requirement_text: str | None,
    ) -> tuple[bool, str]:
        signature = self._snapshot_loft_transition_signature(snapshot)
        if signature is None:
            return False, ""
        cap_shapes = set(signature["cap_shapes"])
        triangle_ok = "triangle" in cap_shapes
        if not triangle_ok:
            triangle_ok, _observed_shapes = self._snapshot_has_matching_profile_shape(
                snapshot,
                ("triangle",),
            )
        transition_ok = (
            int(signature["cone_transition_faces"]) >= 1
            or int(signature["bspline_transition_faces"]) >= 2
        )
        cap_ok = (
            int(signature["min_cap_faces"]) >= 1
            and int(signature["max_cap_faces"]) >= 1
        )
        if not (cap_ok and triangle_ok and transition_ok):
            return (
                False,
                "execute_build123d_geometry_fallback=false, "
                f"cap_faces={[signature['min_cap_faces'], signature['max_cap_faces']]}, "
                f"cap_shapes={signature['cap_shapes']}, "
                f"bspline_transition_faces={signature['bspline_transition_faces']}, "
                f"cone_transition_faces={signature['cone_transition_faces']}",
            )
        return (
            True,
            "execute_build123d_geometry_fallback=true, "
            f"cap_faces={[signature['min_cap_faces'], signature['max_cap_faces']]}, "
            f"cap_shapes={signature['cap_shapes']}, "
            f"bspline_transition_faces={signature['bspline_transition_faces']}, "
            f"cone_transition_faces={signature['cone_transition_faces']}",
        )

    def _snapshot_has_execute_build123d_path_sweep_fallback(
        self,
        *,
        snapshot: CADStateSnapshot,
        hollow_profile_required: bool,
        bend_required: bool,
    ) -> tuple[bool, str]:
        if int(snapshot.geometry.solids) <= 0 or abs(float(snapshot.geometry.volume)) <= 1e-6:
            return False, ""
        faces = list((snapshot.geometry_objects.faces if snapshot.geometry_objects else []) or [])
        if not faces:
            return False, ""
        torus_faces = sum(
            1 for face in faces if str(face.geom_type).strip().upper() == "TORUS"
        )
        revolution_faces = sum(
            1 for face in faces if str(face.geom_type).strip().upper() == "REVOLUTION"
        )
        cylinder_faces = sum(
            1 for face in faces if str(face.geom_type).strip().upper() == "CYLINDER"
        )
        plane_faces = sum(
            1 for face in faces if str(face.geom_type).strip().upper() == "PLANE"
        )
        bbox = [
            float(value or 0.0)
            for value in (snapshot.geometry.bbox or [])
            if isinstance(value, (int, float))
        ]
        bbox_ok = len(bbox) >= 3 and min(bbox[:3]) > 1e-6
        hollow_ok = not hollow_profile_required or cylinder_faces >= 2
        bend_ok = not bend_required or (torus_faces + revolution_faces) >= 1
        cap_ok = plane_faces >= 2
        straight_leg_ok = not (
            hollow_profile_required and bend_required
        ) or cylinder_faces >= 4
        geometry_ok = (
            bbox_ok
            and bend_ok
            and hollow_ok
            and cap_ok
            and cylinder_faces >= 2
            and straight_leg_ok
        )
        return (
            geometry_ok,
            "execute_build123d_geometry_fallback="
            f"{str(geometry_ok).lower()}, torus_faces={torus_faces}, "
            f"revolution_faces={revolution_faces}, "
            f"cylinder_faces={cylinder_faces}, plane_faces={plane_faces}, "
            f"straight_leg_ok={str(straight_leg_ok).lower()}, "
            f"bbox={list(snapshot.geometry.bbox or [])}, volume={snapshot.geometry.volume}",
        )

    def _snapshot_has_requirement_aligned_profile_shape_fallback(
        self,
        *,
        snapshot: CADStateSnapshot,
        requirement_text: str | None,
        targets: tuple[str, ...],
    ) -> tuple[bool, list[str], str]:
        if not targets:
            return False, [], ""
        signature = self._snapshot_loft_transition_signature(snapshot)
        if signature is None:
            return False, [], ""
        observed_shapes: list[str] = []
        evidence_parts: list[str] = []
        if "hexagon" in targets:
            if "hexagon" in signature["cap_shapes"] or int(signature["lower_planar_facets"]) >= 6:
                observed_shapes.append("hexagon")
                evidence_parts.append(
                    f"lower_planar_facets={signature['lower_planar_facets']}"
                )
        if "triangle" in targets and "triangle" in signature["cap_shapes"]:
            observed_shapes.append("triangle")
        if not observed_shapes:
            return False, [], ""
        return (
            True,
            self._normalize_string_list(observed_shapes, limit=12),
            "requirement_aligned_loft_signature=true, "
            + ", ".join(evidence_parts)
            + (
                f", cap_shapes={signature['cap_shapes']}"
                if signature["cap_shapes"]
                else ""
            ),
        )

    def _snapshot_has_requirement_aligned_notch_feature(
        self,
        *,
        snapshot: CADStateSnapshot,
        requirement_text: str | None,
        face_targets: tuple[str, ...],
    ) -> tuple[bool, str]:
        targets = self._extract_required_profile_shape_tokens(
            requirement_text
        ) or self._extract_required_profile_shape_tokens(
            requirement_text,
            pre_solid_only=True,
        )
        if not targets:
            return False, ""
        shape_ok, observed_shapes = self._snapshot_has_matching_profile_shape(
            snapshot=snapshot,
            targets=targets,
        )
        if not shape_ok:
            return False, ""
        has_subtractive_feature = self._snapshot_has_target_face_feature(
            snapshot=snapshot,
            face_targets=face_targets,
            expect_subtractive=True,
            expect_additive=False,
            allow_candidate_hosts=not bool(face_targets),
        )
        if not has_subtractive_feature:
            return False, ""
        return (
            True,
            "execute_build123d_geometry_fallback=true, "
            f"observed_snapshot_profile_shapes={observed_shapes}, "
            "feature=subtractive_notch_or_slot",
        )

    def _snapshot_has_requirement_aligned_notch_geometry(
        self,
        *,
        snapshot: CADStateSnapshot,
        requirement_text: str | None,
    ) -> tuple[bool, str]:
        spec = extract_rectangular_notch_profile_spec(
            requirements=None,
            requirement_text=requirement_text,
        )
        if spec is None or spec.preferred_plane not in {"XY", "XZ", "YZ"}:
            return False, ""

        axis_map = {
            "XY": (0, 1, 2),
            "XZ": (0, 2, 1),
            "YZ": (1, 2, 0),
        }
        width_axis, height_axis, extrude_axis = axis_map[spec.preferred_plane]
        solid_bbox = self._snapshot_primary_solid_bbox(snapshot)
        if solid_bbox is None:
            return False, ""

        extrude_min, extrude_max = self._bbox_axis_bounds(solid_bbox, extrude_axis)
        extrude_span = extrude_max - extrude_min
        if extrude_span <= 1e-6:
            return False, ""
        width_min, width_max = self._bbox_axis_bounds(solid_bbox, width_axis)
        _, height_max = self._bbox_axis_bounds(solid_bbox, height_axis)
        height_min, _ = self._bbox_axis_bounds(solid_bbox, height_axis)

        expected_width = float(spec.inner_width)
        expected_height = float(spec.inner_height)
        expected_floor = (
            float(spec.bottom_offset) + height_min
            if spec.bottom_offset is not None
            else height_max - expected_height
        )
        expected_width_center = (width_min + width_max) / 2.0

        zero_tolerance = max(1e-3, extrude_span * 0.02)
        extrude_tolerance = max(1e-3, extrude_span * 0.03)
        width_tolerance = max(1.0, abs(expected_width) * 0.15)
        height_tolerance = max(1.0, abs(expected_height) * 0.15)
        floor_tolerance = max(1.0, abs(expected_height) * 0.12)

        candidate_floor: tuple[float, float, float] | None = None
        side_face_count = 0
        for face in self._snapshot_feature_faces(snapshot):
            bbox = getattr(face, "bbox", None)
            geom_type = str(getattr(face, "geom_type", "")).strip().upper()
            if bbox is None or geom_type != "PLANE":
                continue

            spans = [
                abs(float(bbox.xlen)),
                abs(float(bbox.ylen)),
                abs(float(bbox.zlen)),
            ]
            if spans[extrude_axis] < extrude_span - extrude_tolerance:
                continue

            width_span = spans[width_axis]
            height_span = spans[height_axis]
            width_bounds = self._bbox_axis_bounds(bbox, width_axis)
            height_bounds = self._bbox_axis_bounds(bbox, height_axis)
            width_center = (width_bounds[0] + width_bounds[1]) / 2.0

            if (
                height_span <= zero_tolerance
                and abs(width_span - expected_width) <= width_tolerance
                and abs(height_bounds[0] - expected_floor) <= floor_tolerance
                and abs(height_bounds[1] - expected_floor) <= floor_tolerance
                and abs(width_center - expected_width_center) <= width_tolerance
            ):
                candidate_floor = (
                    round(width_span, 4),
                    round(expected_floor, 4),
                    round(width_center, 4),
                )
                continue

            if (
                width_span <= zero_tolerance
                and abs(height_span - expected_height) <= height_tolerance
                and abs(height_bounds[0] - expected_floor) <= floor_tolerance
                and abs(height_bounds[1] - height_max) <= floor_tolerance
            ):
                side_face_count += 1

        if candidate_floor is None or side_face_count < 2:
            return False, ""
        return (
            True,
            "execute_build123d_geometry_fallback=true, "
            f"preferred_plane={spec.preferred_plane}, notch_dims={[expected_width, expected_height]}, "
            f"floor_axis_value={candidate_floor[1]}, floor_center={candidate_floor[2]}, "
            f"matched_side_faces={side_face_count}",
        )

    def _requirement_allows_full_span_channel_profile_equivalence(
        self,
        requirement_text: str | None,
    ) -> bool:
        text = str(requirement_text or "").strip().lower()
        if not text:
            return False
        return (
            "top face" in text
            and any(token in text for token in ("slot", "notch", "channel section"))
            and any(
                token in text
                for token in ("spans the full", "spans full", "full length")
            )
            and any(token in text for token in ("u-shaped", "u shape", "channel section"))
        )

    def _snapshot_primary_solid_bbox(
        self,
        snapshot: CADStateSnapshot,
    ) -> BoundingBox3D | None:
        geometry_objects = snapshot.geometry_objects
        if geometry_objects is not None and geometry_objects.solids:
            bbox = getattr(geometry_objects.solids[0], "bbox", None)
            if bbox is not None:
                return bbox
        geometry = snapshot.geometry
        bbox_min = geometry.bbox_min if isinstance(geometry.bbox_min, list) else None
        bbox_max = geometry.bbox_max if isinstance(geometry.bbox_max, list) else None
        if (
            isinstance(bbox_min, list)
            and isinstance(bbox_max, list)
            and len(bbox_min) >= 3
            and len(bbox_max) >= 3
        ):
            return BoundingBox3D(
                xmin=float(bbox_min[0]),
                xmax=float(bbox_max[0]),
                ymin=float(bbox_min[1]),
                ymax=float(bbox_max[1]),
                zmin=float(bbox_min[2]),
                zmax=float(bbox_max[2]),
                xlen=abs(float(bbox_max[0]) - float(bbox_min[0])),
                ylen=abs(float(bbox_max[1]) - float(bbox_min[1])),
                zlen=abs(float(bbox_max[2]) - float(bbox_min[2])),
            )
        return None

    def _extract_equilateral_triangle_frame_side_lengths(
        self,
        requirement_text: str | None,
    ) -> tuple[float, float] | None:
        text = (requirement_text or "").strip().lower()
        if not text or "equilateral triangle" not in text:
            return None
        if not any(
            token in text
            for token in (
                "frame-shaped",
                "frame shaped",
                "frame region",
                "region between",
                "section between",
                "between the two",
                "between them",
            )
        ):
            return None

        outer_match = re.search(
            r"outer triangle[^.]{0,160}?side length of\s*([0-9]+(?:\.[0-9]+)?)",
            text,
        )
        inner_match = re.search(
            r"inner triangle[^.]{0,160}?side length of\s*([0-9]+(?:\.[0-9]+)?)",
            text,
        )
        if outer_match is not None and inner_match is not None:
            outer_side = self._as_float(outer_match.group(1))
            inner_side = self._as_float(inner_match.group(1))
            if outer_side > 0.0 and inner_side > 0.0 and outer_side > inner_side:
                return outer_side, inner_side

        side_lengths = [
            self._as_float(match)
            for match in re.findall(
                r"side length of\s*([0-9]+(?:\.[0-9]+)?)",
                text,
            )
        ]
        positive_lengths = [value for value in side_lengths if value > 0.0]
        if len(positive_lengths) >= 2:
            outer_side, inner_side = positive_lengths[:2]
            if outer_side > inner_side:
                return outer_side, inner_side
        return None

    def _snapshot_has_equilateral_triangle_frame_scale(
        self,
        *,
        snapshot: CADStateSnapshot,
        outer_side: float,
        inner_side: float,
    ) -> tuple[bool, str]:
        bbox = snapshot.geometry.bbox if isinstance(snapshot.geometry.bbox, list) else []
        if len(bbox) < 3:
            return False, ""
        axis_index = max(range(3), key=lambda idx: abs(float(bbox[idx])))
        axis_span = abs(float(bbox[axis_index]))
        if axis_span <= 1e-6:
            return False, ""
        axis_tolerance = max(1e-3, axis_span * 0.03)
        expected_area = (math.sqrt(3.0) / 4.0) * (
            float(outer_side) ** 2 - float(inner_side) ** 2
        )
        observed_cap_areas: list[float] = []
        for face in self._snapshot_feature_faces(snapshot):
            bbox_obj = getattr(face, "bbox", None)
            geom_type = str(getattr(face, "geom_type", "")).strip().upper()
            area = getattr(face, "area", None)
            if bbox_obj is None or geom_type != "PLANE" or not isinstance(area, (int, float)):
                continue
            spans = [
                abs(float(bbox_obj.xlen)),
                abs(float(bbox_obj.ylen)),
                abs(float(bbox_obj.zlen)),
            ]
            positive_non_axis = [
                span for idx, span in enumerate(spans) if idx != axis_index and span > 1e-6
            ]
            if spans[axis_index] <= axis_tolerance and len(positive_non_axis) >= 2:
                observed_cap_areas.append(float(area))
        if not observed_cap_areas:
            return False, ""
        observed_area = max(observed_cap_areas)
        tolerance = max(1.0, abs(expected_area) * 0.12)
        if abs(observed_area - expected_area) <= tolerance:
            return (
                True,
                f"triangle_frame_area_match=true, expected_cap_area={expected_area:.4f}, "
                f"observed_cap_area={observed_area:.4f}",
            )
        return (
            False,
            f"triangle_frame_area_match=false, expected_cap_area={expected_area:.4f}, "
            f"observed_cap_area={observed_area:.4f}",
        )

    def _infer_shapes_from_topology_face(
        self,
        face: TopologyFaceEntity,
    ) -> list[str]:
        inferred: list[str] = []
        if str(face.geom_type or "").strip().upper() == "CYLINDER":
            inferred.append("circle")
        edge_count = len(face.edge_refs) if isinstance(face.edge_refs, list) else 0
        if edge_count >= 3:
            inferred.append("polygon")
        if edge_count == 4:
            inferred.append("rectangle")
            spans = sorted(
                [
                    abs(float(face.bbox.xlen)),
                    abs(float(face.bbox.ylen)),
                    abs(float(face.bbox.zlen)),
                ],
                reverse=True,
            )
            positive_spans = [span for span in spans if span > 1e-6]
            if len(positive_spans) >= 2:
                tolerance = max(1e-3, positive_spans[0] * 0.05)
                if abs(positive_spans[0] - positive_spans[1]) <= tolerance:
                    inferred.append("square")
        if edge_count == 3:
            inferred.append("triangle")
        if edge_count == 6:
            inferred.append("hexagon")
        return inferred

    def _infer_shapes_from_geometry_face(
        self,
        face: FaceEntity,
    ) -> list[str]:
        inferred: list[str] = []
        if str(face.geom_type or "").strip().upper() == "CYLINDER":
            inferred.append("circle")
        spans = sorted(
            [
                abs(float(face.bbox.xlen)),
                abs(float(face.bbox.ylen)),
                abs(float(face.bbox.zlen)),
            ],
            reverse=True,
        )
        positive_spans = [span for span in spans if span > 1e-6]
        if len(positive_spans) < 2:
            return inferred
        width = positive_spans[0]
        height = positive_spans[1]
        expected_area = width * height
        tolerance = max(1e-3, expected_area * 0.08)
        if abs(float(face.area) - expected_area) <= tolerance:
            inferred.append("rectangle")
            side_tolerance = max(1e-3, width * 0.05)
            if abs(width - height) <= side_tolerance:
                inferred.append("square")
        triangle_area = 0.5 * width * height
        triangle_tolerance = max(1e-3, triangle_area * 0.15)
        if abs(float(face.area) - triangle_area) <= triangle_tolerance:
            inferred.append("triangle")
        return inferred

    def _infer_shapes_from_geometry_summary(
        self,
        snapshot: CADStateSnapshot,
    ) -> list[str]:
        geometry = snapshot.geometry
        if int(geometry.solids) != 1:
            return []
        if int(geometry.faces) != 6 or int(geometry.edges) != 12:
            return []
        bbox = geometry.bbox if isinstance(geometry.bbox, list) else []
        if len(bbox) != 3:
            return []
        positive_spans = sorted(
            [abs(float(span)) for span in bbox if abs(float(span)) > 1e-6],
            reverse=True,
        )
        if len(positive_spans) < 2:
            return []
        inferred = ["rectangle"]
        side_tolerance = max(1e-3, positive_spans[0] * 0.05)
        if abs(positive_spans[0] - positive_spans[1]) <= side_tolerance:
            inferred.append("square")
        return inferred

    def _snapshot_has_frame_like_inner_void(
        self,
        *,
        snapshot: CADStateSnapshot,
        requirement_text: str | None,
    ) -> tuple[bool, str]:
        targets = self._extract_required_profile_shape_tokens(
            requirement_text,
            pre_solid_only=True,
        ) or self._extract_required_profile_shape_tokens(requirement_text)
        if not targets:
            return False, ""
        bbox = snapshot.geometry.bbox if isinstance(snapshot.geometry.bbox, list) else []
        if len(bbox) < 3:
            return False, ""
        axis_index = max(range(3), key=lambda idx: abs(float(bbox[idx])))
        axis_span = abs(float(bbox[axis_index]))
        if axis_span <= 1e-6:
            return False, ""
        axis_tolerance = max(1e-3, axis_span * 0.03)
        faces = self._snapshot_feature_faces(snapshot)
        if not faces:
            return False, ""
        cap_faces = 0
        lateral_planar_faces = 0
        lateral_cylindrical_faces = 0
        for face in faces:
            face_bbox = getattr(face, "bbox", None)
            if face_bbox is None:
                continue
            spans = [
                abs(float(face_bbox.xlen)),
                abs(float(face_bbox.ylen)),
                abs(float(face_bbox.zlen)),
            ]
            face_axis_span = spans[axis_index]
            positive_non_axis = [
                span
                for idx, span in enumerate(spans)
                if idx != axis_index and span > 1e-6
            ]
            geom_type = str(getattr(face, "geom_type", "")).strip().upper()
            if geom_type == "PLANE" and face_axis_span <= axis_tolerance and len(positive_non_axis) >= 2:
                cap_faces += 1
            if face_axis_span >= axis_span - axis_tolerance and positive_non_axis:
                if geom_type == "CYLINDER":
                    lateral_cylindrical_faces += 1
                elif geom_type == "PLANE":
                    lateral_planar_faces += 1
        if cap_faces < 2:
            return False, ""
        triangle_frame_spec = self._extract_equilateral_triangle_frame_side_lengths(
            requirement_text
        )
        if "triangle" in targets and lateral_planar_faces >= 6:
            if triangle_frame_spec is not None:
                area_ok, area_evidence = self._snapshot_has_equilateral_triangle_frame_scale(
                    snapshot=snapshot,
                    outer_side=triangle_frame_spec[0],
                    inner_side=triangle_frame_spec[1],
                )
                if not area_ok:
                    return False, area_evidence
                return True, (
                    "same_shape_frame_snapshot_geometry=true, "
                    f"shape=triangle, cap_faces={cap_faces}, lateral_faces={lateral_planar_faces}, "
                    f"{area_evidence}, execute_build123d_geometry_fallback=true"
                )
            return True, (
                "same_shape_frame_snapshot_geometry=true, "
                f"shape=triangle, cap_faces={cap_faces}, lateral_faces={lateral_planar_faces}, "
                "execute_build123d_geometry_fallback=true"
            )
        if ("rectangle" in targets or "square" in targets) and lateral_planar_faces >= 8:
            return True, (
                "same_shape_frame_snapshot_geometry=true, "
                f"shape=rectangle, cap_faces={cap_faces}, lateral_faces={lateral_planar_faces}, "
                "execute_build123d_geometry_fallback=true"
            )
        if "hexagon" in targets and lateral_planar_faces >= 12:
            return True, (
                "same_shape_frame_snapshot_geometry=true, "
                f"shape=hexagon, cap_faces={cap_faces}, lateral_faces={lateral_planar_faces}, "
                "execute_build123d_geometry_fallback=true"
            )
        if "circle" in targets and lateral_cylindrical_faces >= 2:
            return True, (
                "same_shape_frame_snapshot_geometry=true, "
                f"shape=circle, cap_faces={cap_faces}, lateral_faces={lateral_cylindrical_faces}, "
                "execute_build123d_geometry_fallback=true"
            )
        return False, ""

    def _snapshot_has_mixed_nested_inner_void(
        self,
        *,
        snapshot: CADStateSnapshot,
        requirement_text: str | None,
    ) -> tuple[bool, str]:
        outer_diameter, inner_dims = self._extract_mixed_nested_section_targets(
            requirement_text
        )
        if outer_diameter is None or inner_dims is None:
            return False, ""
        solid_bbox = self._snapshot_primary_solid_bbox(snapshot)
        if solid_bbox is None:
            return False, ""

        bbox_spans = [
            abs(float(solid_bbox.xlen)),
            abs(float(solid_bbox.ylen)),
            abs(float(solid_bbox.zlen)),
        ]
        axis_index = max(range(3), key=lambda idx: bbox_spans[idx])
        axis_span = bbox_spans[axis_index]
        if axis_span <= 1e-6:
            return False, ""
        radial_axes = [idx for idx in range(3) if idx != axis_index]
        outer_tolerance = max(1.0, abs(outer_diameter) * 0.1)
        if any(
            abs(bbox_spans[idx] - outer_diameter) > outer_tolerance
            for idx in radial_axes
        ):
            return False, ""

        inner_expected = sorted(float(value) for value in inner_dims)
        axis_tolerance = max(1e-3, axis_span * 0.03)
        dim_tolerance = max(1.0, max(inner_expected) * 0.12)

        outer_cylindrical_faces = 0
        outer_axis_intervals: list[tuple[float, float]] = []
        inner_planar_faces = 0
        for face in self._snapshot_feature_faces(snapshot):
            bbox = getattr(face, "bbox", None)
            geom_type = str(getattr(face, "geom_type", "")).strip().upper()
            if bbox is None:
                continue
            spans = [
                abs(float(bbox.xlen)),
                abs(float(bbox.ylen)),
                abs(float(bbox.zlen)),
            ]
            radial_spans = sorted(spans[idx] for idx in radial_axes if spans[idx] > 1e-6)
            if geom_type == "CYLINDER" and radial_spans:
                candidate_diameter = max(radial_spans)
                if abs(candidate_diameter - outer_diameter) <= outer_tolerance:
                    outer_cylindrical_faces += 1
                    outer_axis_intervals.append(
                        self._bbox_axis_bounds(bbox, axis_index)
                    )
                continue
            if spans[axis_index] < axis_span - axis_tolerance:
                continue
            if geom_type != "PLANE":
                continue
            zero_radial_axes = [idx for idx in radial_axes if spans[idx] <= axis_tolerance]
            if len(zero_radial_axes) != 1:
                continue
            nonzero_radial_spans = sorted(
                spans[idx] for idx in radial_axes if spans[idx] > axis_tolerance
            )
            if not nonzero_radial_spans:
                continue
            candidate_dim = nonzero_radial_spans[-1]
            if any(abs(candidate_dim - expected) <= dim_tolerance for expected in inner_expected):
                inner_planar_faces += 1

        outer_interval_coverage = self._merged_axis_interval_coverage(
            outer_axis_intervals,
            merge_gap=max(1e-3, axis_tolerance),
        )
        coverage_tolerance = max(
            1.0,
            axis_span * 0.15,
            max(inner_expected) * 0.2,
        )
        if (
            outer_cylindrical_faces < 1
            or inner_planar_faces < 4
            or outer_interval_coverage < axis_span - coverage_tolerance
        ):
            return False, ""
        return (
            True,
            "execute_build123d_geometry_fallback=true, "
            f"outer_diameter={outer_diameter}, inner_dims={list(inner_dims)}, "
            f"outer_cylindrical_faces={outer_cylindrical_faces}, inner_planar_faces={inner_planar_faces}, "
            f"outer_axis_coverage={round(outer_interval_coverage, 4)}",
        )

    def _merged_axis_interval_coverage(
        self,
        intervals: list[tuple[float, float]],
        *,
        merge_gap: float,
    ) -> float:
        if not intervals:
            return 0.0
        normalized = sorted(
            (
                (min(float(start), float(end)), max(float(start), float(end)))
                for start, end in intervals
            ),
            key=lambda item: item[0],
        )
        merged: list[list[float]] = []
        for start, end in normalized:
            if not merged:
                merged.append([start, end])
                continue
            last_start, last_end = merged[-1]
            if start <= last_end + merge_gap:
                merged[-1][1] = max(last_end, end)
                continue
            merged.append([start, end])
        return sum(max(0.0, end - start) for start, end in merged)

    def _extract_mixed_nested_section_targets(
        self,
        requirement_text: str | None,
    ) -> tuple[float | None, tuple[float, float] | None]:
        text = (requirement_text or "").lower()
        if "circle" not in text or ("square" not in text and "rectangle" not in text):
            return None, None

        outer_diameter_match = re.search(
            r"circle with a diameter of\s*([0-9]+(?:\.[0-9]+)?)",
            text,
        )
        outer_diameter = (
            float(outer_diameter_match.group(1))
            if outer_diameter_match is not None
            else None
        )

        square_match = re.search(
            r"square with a side length of\s*([0-9]+(?:\.[0-9]+)?)",
            text,
        )
        if square_match is not None:
            value = float(square_match.group(1))
            return outer_diameter, (value, value)

        rectangle_match = re.search(
            r"rectangle(?: with)?(?: width)?\s*([0-9]+(?:\.[0-9]+)?)\s*(?:mm)?\s*(?:x|×)\s*([0-9]+(?:\.[0-9]+)?)",
            text,
        )
        if rectangle_match is not None:
            return outer_diameter, (
                float(rectangle_match.group(1)),
                float(rectangle_match.group(2)),
            )
        return outer_diameter, None

    def _extract_half_shell_profile_requirement(
        self,
        requirement_text: str | None,
    ) -> dict[str, Any] | None:
        text = (requirement_text or "").strip().lower()
        if not text:
            return None
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
        if not any(token in text for token in half_shell_tokens):
            return None
        if not any(
            token in text
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
        ):
            return None

        outer_diameter: float | None = None
        for pattern in (
            r"outer diameter of\s*([0-9]+(?:\.[0-9]+)?)",
            r"outer diameter\s*[:=]?\s*([0-9]+(?:\.[0-9]+)?)",
            r"\bod\b\s*[:=]?\s*([0-9]+(?:\.[0-9]+)?)",
        ):
            match = re.search(pattern, text)
            if match is not None:
                outer_diameter = float(match.group(1))
                break
        if outer_diameter is None:
            for pattern in (
                r"outer semicircle[^.]{0,80}?radius(?: of)?\s*([0-9]+(?:\.[0-9]+)?)",
                r"outer radius(?: of)?\s*([0-9]+(?:\.[0-9]+)?)",
                r"outer radius\s*[:=]?\s*([0-9]+(?:\.[0-9]+)?)",
            ):
                match = re.search(pattern, text)
                if match is not None:
                    outer_diameter = float(match.group(1)) * 2.0
                    break
        if outer_diameter is None or outer_diameter <= 0.0:
            return None

        length: float | None = None
        for pattern in (
            r"length of\s*([0-9]+(?:\.[0-9]+)?)",
            r"extrud(?:e|ed|ing)(?: it)?(?: [^.,;]{0,32})? by\s*([0-9]+(?:\.[0-9]+)?)",
            r"extrud(?:e|ed|ing)(?: it)?\s*([0-9]+(?:\.[0-9]+)?)\s*(?:millimeters?|mm)",
        ):
            match = re.search(pattern, text)
            if match is not None:
                length = float(match.group(1))
                break

        return {
            "outer_diameter": float(outer_diameter),
            "outer_radius": float(outer_diameter) / 2.0,
            "length": float(length) if isinstance(length, (int, float)) else None,
            "require_split_plane_anchor": any(
                token in text
                for token in (
                    "split surface is flat",
                    "split line",
                    "open above the split line",
                    "open below the split line",
                )
            ),
        }

    def _requirement_requires_merged_body_result(
        self,
        requirement_text: str | None,
        *,
        semantics: RequirementSemantics | None = None,
    ) -> bool:
        text = str(requirement_text or "").strip().lower()
        if not text:
            return False
        if bool(getattr(semantics, "mentions_multi_plane_additive_union", False)):
            return False
        if any(
            token in text
            for token in (
                "separate parts",
                "two-part",
                "two part",
                "lid and base",
                "top lid",
                "bottom base",
                "assembly",
            )
        ):
            return False
        if any(
            token in text
            for token in (
                "boolean difference",
                "cut-extrude",
                "cut extrude",
                "slot",
                "notch",
                "pocket",
                "recess",
                "hole",
                "bore",
            )
        ):
            return True
        if not any(
            token in text
            for token in (
                "union",
                "merged solid",
                "one merged solid",
                "single solid",
                "one connected solid",
            )
        ):
            if not requirement_suggests_axisymmetric_profile(
                {"description": requirement_text or ""},
                requirement_text,
            ):
                return False
            if not any(
                token in text
                for token in ("boss", "flange", "disk", "end cap", "concentric")
            ):
                return False
        return True

    def _snapshot_has_requirement_aligned_half_shell_profile_result(
        self,
        *,
        snapshot: CADStateSnapshot,
        requirement_text: str | None,
    ) -> tuple[bool, str]:
        spec = self._extract_half_shell_profile_requirement(requirement_text)
        if spec is None:
            return False, ""
        solid_bbox = self._snapshot_primary_solid_bbox(snapshot)
        if solid_bbox is not None:
            spans = [
                abs(float(solid_bbox.xlen)),
                abs(float(solid_bbox.ylen)),
                abs(float(solid_bbox.zlen)),
            ]
            bounds = [
                self._bbox_axis_bounds(solid_bbox, axis_index)
                for axis_index in range(3)
            ]
        else:
            bbox = snapshot.geometry.bbox if isinstance(snapshot.geometry.bbox, list) else []
            if len(bbox) < 3:
                return False, ""
            spans = [abs(float(bbox[0])), abs(float(bbox[1])), abs(float(bbox[2]))]
            bounds = [(0.0, span) for span in spans]

        outer_radius = float(spec["outer_radius"])
        outer_diameter = float(spec["outer_diameter"])
        expected_length = spec.get("length")
        require_split_plane_anchor = bool(spec.get("require_split_plane_anchor"))
        half_tolerance = max(1.0, abs(outer_radius) * 0.12)
        diameter_tolerance = max(1.0, abs(outer_diameter) * 0.12)
        length_tolerance = (
            max(1.0, abs(float(expected_length)) * 0.12)
            if isinstance(expected_length, (int, float))
            else None
        )
        anchor_tolerance = max(1.0, abs(outer_radius) * 0.12)

        candidates: list[dict[str, Any]] = []
        for axis_index, span in enumerate(spans):
            if abs(span - outer_radius) > half_tolerance:
                continue
            axis_bounds = bounds[axis_index]
            anchor_ok = True
            if require_split_plane_anchor:
                anchor_ok = (
                    abs(float(axis_bounds[0])) <= anchor_tolerance
                    or abs(float(axis_bounds[1])) <= anchor_tolerance
                )
            candidates.append(
                {
                    "axis_index": axis_index,
                    "span": span,
                    "bounds": axis_bounds,
                    "anchor_ok": anchor_ok,
                }
            )

        selected: dict[str, Any] | None = None
        for candidate in candidates:
            axis_index = int(candidate["axis_index"])
            other_axes = [idx for idx in range(3) if idx != axis_index]
            length_ok = (
                True
                if length_tolerance is None
                else any(
                    abs(spans[idx] - float(expected_length)) <= length_tolerance
                    for idx in other_axes
                )
            )
            envelope_ok = any(
                spans[idx] >= outer_diameter - diameter_tolerance for idx in other_axes
            )
            if bool(candidate["anchor_ok"]) and length_ok and envelope_ok:
                selected = candidate
                break

        observed_spans = [round(float(span), 4) for span in spans]
        likely_split_axis = min(
            range(3),
            key=lambda axis_index: abs(spans[axis_index] - outer_radius),
        )
        likely_split_bounds = bounds[likely_split_axis]
        if selected is None:
            candidate_axes = [
                {
                    "axis": "XYZ"[int(item["axis_index"])],
                    "span": round(float(item["span"]), 4),
                    "bounds": [
                        round(float(item["bounds"][0]), 4),
                        round(float(item["bounds"][1]), 4),
                    ],
                    "anchor_ok": bool(item["anchor_ok"]),
                }
                for item in candidates
            ]
            return (
                False,
                f"expected_half_profile_span={round(outer_radius, 4)}, "
                f"expected_outer_diameter={round(outer_diameter, 4)}, "
                f"expected_length={round(float(expected_length), 4) if isinstance(expected_length, (int, float)) else '<unspecified>'}, "
                f"observed_spans={observed_spans}, "
                f"likely_split_axis={'XYZ'[likely_split_axis]}, "
                f"likely_split_bounds={[round(float(likely_split_bounds[0]), 4), round(float(likely_split_bounds[1]), 4)]}, "
                f"candidate_half_profile_axes={candidate_axes}, "
                f"require_split_plane_anchor={require_split_plane_anchor}",
            )

        return (
            True,
            "execute_build123d_geometry_fallback=true, "
            f"expected_half_profile_span={round(outer_radius, 4)}, "
            f"observed_half_profile_span={round(float(selected['span']), 4)}, "
            f"half_profile_axis={'XYZ'[int(selected['axis_index'])]}, "
            f"observed_bounds={[round(float(selected['bounds'][0]), 4), round(float(selected['bounds'][1]), 4)]}, "
            f"observed_spans={observed_spans}, "
            f"expected_length={round(float(expected_length), 4) if isinstance(expected_length, (int, float)) else '<unspecified>'}",
        )

    def _snapshot_has_requirement_aligned_axisymmetric_groove(
        self,
        *,
        snapshot: CADStateSnapshot,
        requirement_text: str | None,
    ) -> tuple[bool, str]:
        groove_dims, groove_height, _anchor_mode = self._extract_revolved_groove_targets(
            requirement_text
        )
        anchor_modes = self._extract_revolved_groove_height_anchor_modes(
            requirement_text
        )
        if groove_dims is None or groove_height is None:
            return False, ""
        solid_bbox = self._snapshot_primary_solid_bbox(snapshot)
        if solid_bbox is None:
            return False, ""
        bbox_spans = [
            abs(float(solid_bbox.xlen)),
            abs(float(solid_bbox.ylen)),
            abs(float(solid_bbox.zlen)),
        ]
        axis_index = max(range(3), key=lambda idx: bbox_spans[idx])
        axis_span = bbox_spans[axis_index]
        if axis_span <= 1e-6:
            return False, ""
        solid_axis_min, _solid_axis_max = self._bbox_axis_bounds(solid_bbox, axis_index)
        radial_axes = [idx for idx in range(3) if idx != axis_index]
        radial_spans = sorted(bbox_spans[idx] for idx in radial_axes)
        if len(radial_spans) != 2 or abs(radial_spans[0] - radial_spans[1]) > max(1.0, radial_spans[-1] * 0.08):
            return False, ""
        outer_radius = radial_spans[-1] / 2.0
        axis_tolerance = max(1e-3, axis_span * 0.03)
        outer_tolerance = max(1.0, outer_radius * 0.1)
        dim_candidates = [float(groove_dims[0]), float(groove_dims[1])]
        dim_tolerance = max(1.0, max(dim_candidates) * 0.15)

        for face in self._snapshot_feature_faces(snapshot):
            bbox = getattr(face, "bbox", None)
            geom_type = str(getattr(face, "geom_type", "")).strip().upper()
            if bbox is None or geom_type not in {"CYLINDER", "REVOLUTION"}:
                continue
            spans = [
                abs(float(bbox.xlen)),
                abs(float(bbox.ylen)),
                abs(float(bbox.zlen)),
            ]
            face_axis_span = spans[axis_index]
            if face_axis_span >= axis_span - axis_tolerance:
                continue
            candidate_diameter = max(spans[idx] for idx in radial_axes)
            candidate_radius = candidate_diameter / 2.0
            radial_depth = outer_radius - candidate_radius
            if radial_depth <= 0.0:
                continue
            for axial_size in dim_candidates:
                radial_size = next(
                    (value for value in dim_candidates if value != axial_size),
                    axial_size,
                )
                if abs(face_axis_span - axial_size) > dim_tolerance:
                    continue
                if abs(radial_depth - radial_size) > dim_tolerance:
                    continue
                axis_min, axis_max = self._bbox_axis_bounds(bbox, axis_index)
                matched_window_mode = self._match_snapshot_axis_window_to_requirement_height(
                    axis_min=axis_min,
                    axis_max=axis_max,
                    solid_axis_min=solid_axis_min,
                    target_height=groove_height,
                    anchor_modes=anchor_modes,
                    axial_size=axial_size,
                    tolerance=max(1.0, axial_size * 0.2),
                )
                if matched_window_mode is None:
                    continue
                if candidate_radius > outer_radius + outer_tolerance:
                    continue
                return (
                    True,
                    "execute_build123d_geometry_fallback=true, "
                    f"outer_radius={round(outer_radius, 4)}, groove_dims={list(groove_dims)}, "
                    f"candidate_radius={round(candidate_radius, 4)}, axial_window={[round(axis_min, 4), round(axis_max, 4)]}, "
                    f"height_match_mode={matched_window_mode}",
                )
        return False, ""

    def _match_snapshot_axis_window_to_requirement_height(
        self,
        *,
        axis_min: float,
        axis_max: float,
        solid_axis_min: float,
        target_height: float,
        anchor_modes: list[str],
        axial_size: float,
        tolerance: float,
    ) -> str | None:
        normalized_anchor_modes = [
            mode
            for mode in anchor_modes
            if mode in {"top_edge", "bottom_edge", "center"}
        ] or ["center"]
        for anchor_mode in normalized_anchor_modes:
            if self._axis_window_matches_requirement_height(
                axis_min=axis_min,
                axis_max=axis_max,
                target_height=target_height,
                anchor_mode=anchor_mode,
                axial_size=axial_size,
                tolerance=tolerance,
            ):
                return f"world_space:{anchor_mode}"

        normalized_min = axis_min - solid_axis_min
        normalized_max = axis_max - solid_axis_min
        for anchor_mode in normalized_anchor_modes:
            if self._axis_window_matches_requirement_height(
                axis_min=normalized_min,
                axis_max=normalized_max,
                target_height=target_height,
                anchor_mode=anchor_mode,
                axial_size=axial_size,
                tolerance=tolerance,
            ):
                return f"bbox_min_normalized:{anchor_mode}"

        if abs(solid_axis_min) <= 1e-6 or "center" in normalized_anchor_modes:
            return None

        for anchor_mode in normalized_anchor_modes:
            if self._axis_window_matches_requirement_height(
                axis_min=normalized_min,
                axis_max=normalized_max,
                target_height=target_height,
                anchor_mode="center",
                axial_size=axial_size,
                tolerance=tolerance,
            ):
                return f"bbox_min_normalized_center_fallback:{anchor_mode}"
        return None

    def _axis_window_matches_requirement_height(
        self,
        *,
        axis_min: float,
        axis_max: float,
        target_height: float,
        anchor_mode: str,
        axial_size: float,
        tolerance: float,
    ) -> bool:
        if anchor_mode == "top_edge":
            expected_min = target_height - axial_size
            expected_max = target_height
        elif anchor_mode == "bottom_edge":
            expected_min = target_height
            expected_max = target_height + axial_size
        else:
            expected_min = target_height - axial_size / 2.0
            expected_max = target_height + axial_size / 2.0
        return abs(axis_min - expected_min) <= tolerance and abs(axis_max - expected_max) <= tolerance

    def _extract_required_edge_feature_radius(
        self,
        requirement_text: str | None,
        *,
        feature_keyword: str,
    ) -> float | None:
        text = str(requirement_text or "").strip().lower()
        if not text or feature_keyword not in text:
            return None
        patterns = (
            rf"{feature_keyword}[^.]{0,80}?radius(?: of)?\s*([0-9]+(?:\.[0-9]+)?)",
            rf"radius(?: of)?\s*([0-9]+(?:\.[0-9]+)?)[^.]{0,80}?{feature_keyword}",
        )
        for pattern in patterns:
            match = re.search(pattern, text)
            if match is None:
                continue
            try:
                value = float(match.group(1))
            except Exception:
                continue
            if value > 0.0:
                return value
        return None

    def _snapshot_has_targeted_fillet_face(
        self,
        *,
        snapshot: CADStateSnapshot,
        edge_targets: tuple[str, ...],
        requirement_text: str | None,
    ) -> tuple[bool, str]:
        required_radius = self._extract_required_edge_feature_radius(
            requirement_text,
            feature_keyword="fillet",
        )
        normalized_targets = self._normalize_edge_targets_for_matching(edge_targets)
        for face in self._snapshot_feature_faces(snapshot):
            if str(getattr(face, "geom_type", "")).strip().upper() != "CYLINDER":
                continue
            radius_value = getattr(face, "radius", None)
            if required_radius is not None and isinstance(radius_value, (int, float)):
                if abs(float(radius_value) - required_radius) > max(1e-3, required_radius * 0.15):
                    continue
            labels = self._snapshot_edge_feature_labels(snapshot, face)
            if normalized_targets and not self._labels_match_edge_targets(labels, normalized_targets):
                continue
            return True, (
                "execute_build123d_geometry_fallback=true, "
                f"feature=fillet, labels={sorted(labels)}, radius={radius_value}"
            )
        return False, ""

    def _snapshot_edge_feature_labels(
        self,
        snapshot: CADStateSnapshot,
        face: Any,
    ) -> set[str]:
        bbox = getattr(face, "bbox", None)
        if bbox is None:
            return set()
        labels: set[str] = set()
        dominant_axis = self._face_parallel_axis_index(face)
        if dominant_axis == 0:
            labels.add("x_parallel")
        elif dominant_axis == 1:
            labels.add("y_parallel")
        elif dominant_axis == 2:
            labels.add("z_parallel")
        global_bounds = self._snapshot_global_bbox_bounds(snapshot)
        axis_tolerances = [
            self._extent_tolerance(global_bounds[0], global_bounds[1]),
            self._extent_tolerance(global_bounds[2], global_bounds[3]),
            self._extent_tolerance(global_bounds[4], global_bounds[5]),
        ]
        axis_bounds = (
            (float(bbox.xmin), float(bbox.xmax)),
            (float(bbox.ymin), float(bbox.ymax)),
            (float(bbox.zmin), float(bbox.zmax)),
        )
        axis_labels = ("left", "right"), ("back", "front"), ("bottom", "top")
        touches_outer = False
        for axis_index, (low_label, high_label) in enumerate(axis_labels):
            lower_bound, upper_bound = axis_bounds[axis_index]
            min_global = global_bounds[axis_index * 2]
            max_global = global_bounds[axis_index * 2 + 1]
            tolerance = axis_tolerances[axis_index]
            if self._near_min(lower_bound, min_global, tolerance):
                labels.add(low_label)
                touches_outer = True
            if self._near_max(upper_bound, max_global, tolerance):
                labels.add(high_label)
                touches_outer = True
        if touches_outer:
            labels.add("outer")
        return labels

    def _snapshot_global_bbox_bounds(
        self,
        snapshot: CADStateSnapshot,
    ) -> tuple[float, float, float, float, float, float]:
        geometry = snapshot.geometry
        if len(geometry.bbox_min) >= 3 and len(geometry.bbox_max) >= 3:
            return (
                float(geometry.bbox_min[0]),
                float(geometry.bbox_max[0]),
                float(geometry.bbox_min[1]),
                float(geometry.bbox_max[1]),
                float(geometry.bbox_min[2]),
                float(geometry.bbox_max[2]),
            )
        faces = self._snapshot_feature_faces(snapshot)
        if not faces:
            return (0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
        min_x = min(float(face.bbox.xmin) for face in faces if getattr(face, "bbox", None) is not None)
        max_x = max(float(face.bbox.xmax) for face in faces if getattr(face, "bbox", None) is not None)
        min_y = min(float(face.bbox.ymin) for face in faces if getattr(face, "bbox", None) is not None)
        max_y = max(float(face.bbox.ymax) for face in faces if getattr(face, "bbox", None) is not None)
        min_z = min(float(face.bbox.zmin) for face in faces if getattr(face, "bbox", None) is not None)
        max_z = max(float(face.bbox.zmax) for face in faces if getattr(face, "bbox", None) is not None)
        return (min_x, max_x, min_y, max_y, min_z, max_z)

    def _face_parallel_axis_index(
        self,
        face: Any,
    ) -> int | None:
        axis_direction = getattr(face, "axis_direction", None)
        if (
            isinstance(axis_direction, list)
            and len(axis_direction) >= 3
            and all(isinstance(item, (int, float)) for item in axis_direction[:3])
        ):
            dominant = max(
                range(3),
                key=lambda idx: abs(float(axis_direction[idx])),
            )
            if abs(float(axis_direction[dominant])) >= 0.7:
                return dominant
        bbox = getattr(face, "bbox", None)
        if bbox is None:
            return None
        spans = [abs(float(bbox.xlen)), abs(float(bbox.ylen)), abs(float(bbox.zlen))]
        dominant = max(range(3), key=lambda idx: spans[idx])
        return dominant if spans[dominant] > 1e-6 else None

    def _history_has_direct_feature_matching_profile_shape(
        self,
        history: list[ActionHistoryEntry],
        first_solid_index: int | None,
        targets: tuple[str, ...],
    ) -> tuple[bool, list[str]]:
        if first_solid_index is None or not targets:
            return False, []
        observed: list[str] = []
        for action_index, entry in enumerate(history):
            if action_index <= first_solid_index:
                continue
            if not self._history_action_materially_changes_geometry(history, action_index):
                continue
            if entry.action_type == CADActionType.HOLE:
                observed.append("circle")
                if "circle" in targets:
                    return True, self._normalize_string_list(observed, limit=12)
                continue
            if entry.action_type == CADActionType.SPHERE_RECESS:
                observed.extend(["circle", "sphere_recess"])
                if "circle" in targets:
                    return True, self._normalize_string_list(observed, limit=12)
                continue
        return False, self._normalize_string_list(observed, limit=12)

    def _history_has_sweep_equivalent_profile_shape(
        self,
        history: list[ActionHistoryEntry],
        first_solid_index: int | None,
        targets: tuple[str, ...],
    ) -> tuple[bool, list[str]]:
        if first_solid_index is None or not targets:
            return False, []
        observed: list[str] = []
        for action_index, entry in enumerate(history):
            if entry.action_type != CADActionType.SWEEP:
                continue
            if action_index < first_solid_index:
                continue
            if not self._history_action_materially_changes_geometry(history, action_index):
                continue
            sketch_index = self._find_preceding_sketch_index(history, action_index)
            if sketch_index is None:
                continue
            for window_entry in history[sketch_index + 1 : action_index]:
                if window_entry.action_type not in {
                    CADActionType.ADD_CIRCLE,
                    CADActionType.ADD_RECTANGLE,
                    CADActionType.ADD_POLYGON,
                }:
                    continue
                if window_entry.action_type == CADActionType.ADD_CIRCLE:
                    observed.append("circle")
                elif window_entry.action_type == CADActionType.ADD_RECTANGLE:
                    observed.append("rectangle")
                elif window_entry.action_type == CADActionType.ADD_POLYGON:
                    observed.append("polygon")
                if self._entry_matches_profile_shape_targets(window_entry, targets):
                    return True, self._normalize_string_list(observed, limit=12)
        return False, self._normalize_string_list(observed, limit=12)

    def _requirement_suggests_annular_radial_pattern_seed(
        self,
        requirement_text: str | None,
    ) -> bool:
        text = str(requirement_text or "").strip().lower()
        if not text:
            return False
        has_pattern = any(
            token in text
            for token in ("pattern", "evenly distributed", "pitch circle", "circular pattern")
        )
        has_seed_feature = any(
            token in text
            for token in ("tooth", "teeth", "serrated", "serration", "rib", "boss")
        )
        has_annular_context = any(
            token in text for token in ("annular", "washer", "radial", "12 o'clock")
        )
        return has_pattern and has_seed_feature and has_annular_context

    def _infer_annular_band_width_from_snapshot(
        self,
        snapshot: CADStateSnapshot | None,
    ) -> float | None:
        if snapshot is None or snapshot.geometry_objects is None:
            return None
        radii: list[float] = []
        for face in snapshot.geometry_objects.faces:
            if str(face.geom_type).strip().upper() != "CYLINDER":
                continue
            dims = [
                abs(float(face.bbox.xlen)),
                abs(float(face.bbox.ylen)),
                abs(float(face.bbox.zlen)),
            ]
            dims.sort(reverse=True)
            diameter: float | None = None
            if abs(dims[0] - dims[1]) <= max(1e-3, dims[0] * 0.15):
                diameter = (dims[0] + dims[1]) / 2.0
            elif abs(dims[1] - dims[2]) <= max(1e-3, dims[1] * 0.15):
                diameter = (dims[1] + dims[2]) / 2.0
            if diameter is None or diameter <= 0.0:
                continue
            radius = diameter / 2.0
            if not any(
                abs(radius - existing) <= max(1e-3, existing * 0.05)
                for existing in radii
            ):
                radii.append(radius)
        if len(radii) < 2:
            return None
        radii.sort()
        return radii[-1] - radii[0]

    def _profile_entry_max_in_plane_span(
        self,
        entry: ActionHistoryEntry,
    ) -> float:
        params = entry.action_params if isinstance(entry.action_params, dict) else {}
        if entry.action_type == CADActionType.ADD_CIRCLE:
            radius = self._to_positive_float(
                params.get("radius", params.get("radius_outer")),
                default=0.0,
            )
            if radius > 0.0:
                return radius * 2.0
            diameter = self._to_positive_float(
                params.get("diameter", params.get("diameter_outer")),
                default=0.0,
            )
            return diameter
        if entry.action_type == CADActionType.ADD_RECTANGLE:
            width = self._to_positive_float(params.get("width"), default=0.0)
            height = self._to_positive_float(params.get("height"), default=0.0)
            return max(width, height)
        if entry.action_type != CADActionType.ADD_POLYGON:
            return 0.0
        points = params.get("points", params.get("vertices"))
        xs: list[float] = []
        ys: list[float] = []
        if isinstance(points, list):
            for raw_point in points:
                if (
                    isinstance(raw_point, (list, tuple))
                    and len(raw_point) >= 2
                    and isinstance(raw_point[0], (int, float))
                    and isinstance(raw_point[1], (int, float))
                ):
                    xs.append(float(raw_point[0]))
                    ys.append(float(raw_point[1]))
                elif (
                    isinstance(raw_point, dict)
                    and isinstance(raw_point.get("x"), (int, float))
                    and isinstance(raw_point.get("y"), (int, float))
                ):
                    xs.append(float(raw_point["x"]))
                    ys.append(float(raw_point["y"]))
        if xs and ys:
            return max(max(xs) - min(xs), max(ys) - min(ys))
        radius = self._to_positive_float(
            params.get("radius", params.get("radius_outer")),
            default=0.0,
        )
        if radius > 0.0:
            return radius * 2.0
        return 0.0

    def _history_has_requirement_aligned_annular_pattern_seed(
        self,
        history: list[ActionHistoryEntry],
        first_solid_index: int,
        requirement_text: str | None,
    ) -> tuple[bool, str]:
        if not self._requirement_suggests_annular_radial_pattern_seed(requirement_text):
            return True, ""
        pattern_indices = [
            index
            for index, entry in enumerate(history)
            if entry.action_type == CADActionType.PATTERN_CIRCULAR
        ]
        seed_end_index = pattern_indices[0] if pattern_indices else len(history)
        seed_index: int | None = None
        for index in range(seed_end_index - 1, first_solid_index, -1):
            entry = history[index]
            if entry.action_type not in {
                CADActionType.EXTRUDE,
                CADActionType.LOFT,
                CADActionType.SWEEP,
                CADActionType.REVOLVE,
            }:
                continue
            if not self._history_action_materially_changes_geometry(history, index):
                continue
            if entry.action_type == CADActionType.REVOLVE and not bool(
                entry.action_params.get("cut")
            ):
                seed_index = index
                break
            if entry.action_type != CADActionType.REVOLVE:
                seed_index = index
                break
        if seed_index is None:
            return False, "missing additive seed feature before circular pattern"
        sketch_index = self._find_preceding_sketch_index(history, seed_index)
        if sketch_index is None or sketch_index <= first_solid_index:
            return False, "missing face-attached seed sketch before repeated annular pattern"
        shape_targets = self._extract_required_profile_shape_tokens(
            requirement_text,
            post_solid_only=True,
        )
        window_entries = [
            item
            for item in history[sketch_index + 1 : seed_index]
            if item.action_type
            in {
                CADActionType.ADD_CIRCLE,
                CADActionType.ADD_RECTANGLE,
                CADActionType.ADD_POLYGON,
            }
        ]
        if not window_entries:
            return False, "seed sketch window is empty before repeated annular pattern"
        shape_ok = True
        if shape_targets:
            shape_ok = any(
                self._entry_matches_profile_shape_targets(window_entry, shape_targets)
                for window_entry in window_entries
            )
        max_span = max(
            self._profile_entry_max_in_plane_span(window_entry)
            for window_entry in window_entries
        )
        band_width = self._infer_annular_band_width_from_snapshot(
            history[seed_index - 1].result_snapshot if seed_index > 0 else None
        )
        if band_width is None or band_width <= 1e-6:
            return shape_ok, (
                f"required_shapes={list(shape_targets)}, max_seed_span={round(max_span, 6)}, "
                "annular_band_width=unknown"
            )
        span_ok = max_span >= band_width * 0.45
        return (
            shape_ok and span_ok,
            f"required_shapes={list(shape_targets)}, max_seed_span={round(max_span, 6)}, annular_band_width={round(band_width, 6)}, span_ok={span_ok}, shape_ok={shape_ok}",
        )

    def _extract_explicit_hole_centers_from_requirement(
        self,
        requirement_text: str,
    ) -> list[list[float]]:
        text = str(requirement_text or "").strip().lower()
        if not text or "hole" not in text:
            return []
        matches = re.findall(
            r"\(\s*([-+]?[0-9]+(?:\.[0-9]+)?)\s*,\s*([-+]?[0-9]+(?:\.[0-9]+)?)\s*\)",
            text,
        )
        centers: list[list[float]] = []
        for x_raw, y_raw in matches:
            center = [float(x_raw), float(y_raw)]
            if center not in centers:
                centers.append(center)
        sentence_candidates = []
        if "hole" in text and "center" in text:
            sentence_candidates.append(text)
        sentence_candidates.extend(
            sentence
            for sentence in re.split(r"[\n;]", text)
            if "hole" in sentence and "center" in sentence
        )
        for sentence in sentence_candidates:
            center_clause_match = re.search(
                r"\bcenter(?:ed)?\b.*$",
                sentence,
                re.IGNORECASE,
            )
            center_clause = (
                center_clause_match.group(0) if center_clause_match is not None else ""
            )
            center_axis_order, center_axis_values = self._extract_requirement_axis_values(
                center_clause
            )
            axis_order, axis_values = self._extract_requirement_axis_values(sentence)
            if center_axis_order:
                axis_order = [
                    axis_name
                    for axis_name in center_axis_order
                    if axis_name in center_axis_values
                ] + [
                    axis_name
                    for axis_name in axis_order
                    if axis_name not in center_axis_values
                ]
                axis_values = {
                    axis_name: list(values)
                    for axis_name, values in axis_values.items()
                }
                for axis_name, values in center_axis_values.items():
                    axis_values[axis_name] = list(values)
            if len(axis_order) < 2:
                continue
            ordered_axes = sorted(
                axis_order[:3],
                key=lambda axis_name: {"X": 0, "Y": 1, "Z": 2}.get(axis_name, 99),
            )
            primary_axis, secondary_axis = ordered_axes[:2]
            for primary_value in axis_values.get(primary_axis, []):
                for secondary_value in axis_values.get(secondary_axis, []):
                    center = [float(primary_value), float(secondary_value)]
                    if center not in centers:
                        centers.append(center)
        return centers

    def _extract_requirement_axis_values(
        self,
        text: str | None,
    ) -> tuple[list[str], dict[str, list[float]]]:
        axis_order: list[str] = []
        axis_values: dict[str, list[float]] = {}
        for match in re.finditer(
            r"\b(?P<axis>[xyz])\s*=\s*(?P<pm>±\s*)?(?P<value>[-+]?[0-9]+(?:\.[0-9]+)?)",
            str(text or ""),
            re.IGNORECASE,
        ):
            normalized_axis = str(match.group("axis")).upper()
            values = axis_values.setdefault(normalized_axis, [])
            if normalized_axis not in axis_order:
                axis_order.append(normalized_axis)
            value = float(str(match.group("value")))
            if match.group("pm"):
                for signed_value in (-abs(value), abs(value)):
                    if signed_value not in values:
                        values.append(signed_value)
            elif value not in values:
                values.append(value)
        return axis_order, axis_values

    def _infer_expected_local_feature_centers(
        self,
        requirement_text: str | None,
    ) -> list[list[float]]:
        explicit_hole_centers = self._extract_explicit_hole_centers_from_requirement(
            requirement_text
        )
        if explicit_hole_centers:
            return explicit_hole_centers
        explicit_points = self._extract_explicit_point_centers_from_requirement(
            requirement_text
        )
        if len(explicit_points) >= 2:
            return explicit_points
        centered_square_or_rectangular_array = (
            self._infer_centered_square_or_rectangular_array_centers_from_requirement(
                requirement_text
            )
        )
        if len(centered_square_or_rectangular_array) >= 2:
            return centered_square_or_rectangular_array
        centered_pattern = self._infer_centered_linear_pattern_centers_from_requirement(
            requirement_text
        )
        if len(centered_pattern) >= 2:
            return centered_pattern
        circular_pattern = self._infer_circular_pattern_centers_from_requirement(
            requirement_text
        )
        if len(circular_pattern) >= 2:
            return circular_pattern
        return []

    def _infer_expected_local_feature_count(
        self,
        requirement_text: str | None,
        *,
        family: str | None = None,
    ) -> int | None:
        text = str(requirement_text or "").strip().lower()
        if not text:
            return None
        family_name = str(family or "").strip().lower()
        word_to_count = {
            "a": 1,
            "an": 1,
            "single": 1,
            "one": 1,
            "two": 2,
            "pair": 2,
            "three": 3,
            "four": 4,
            "five": 5,
            "six": 6,
            "seven": 7,
            "eight": 8,
            "nine": 9,
            "ten": 10,
            "eleven": 11,
            "twelve": 12,
        }
        if family_name == "explicit_anchor_hole":
            noun_patterns = (
                r"(?:mounting|countersunk?|counterbored|clearance|pilot|fastener|anchor|through|threaded)\s+holes?",
                r"holes?",
                r"countersinks?",
                r"counterbores?",
                r"(?:magnet\s+)?recess(?:es)?",
            )
        else:
            noun_patterns = (r"features?",)
        counts: list[int] = []
        for noun_pattern in noun_patterns:
            for match in re.finditer(
                rf"\b(?P<count>\d+|a|an|single|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)\b"
                rf"[^.;,\n]{{0,40}}\b(?:{noun_pattern})\b",
                text,
                re.IGNORECASE,
            ):
                raw = str(match.group("count")).lower()
                count = word_to_count.get(raw)
                if count is None:
                    try:
                        count = int(raw)
                    except Exception:
                        count = None
                if isinstance(count, int) and count > 0:
                    counts.append(count)
            if re.search(
                rf"\bpair\s+of\b[^.;,\n]{{0,24}}\b(?:{noun_pattern})\b",
                text,
                re.IGNORECASE,
            ):
                counts.append(2)
        return max(counts) if counts else None

    def _extract_explicit_point_centers_from_requirement(
        self,
        requirement_text: str | None,
    ) -> list[list[float]]:
        text = str(requirement_text or "").strip().lower()
        if not text:
            return []
        matches = re.findall(
            r"\(\s*([-+]?[0-9]+(?:\.[0-9]+)?)\s*,\s*([-+]?[0-9]+(?:\.[0-9]+)?)\s*\)",
            text,
        )
        centers: list[list[float]] = []
        for x_raw, y_raw in matches:
            center = [float(x_raw), float(y_raw)]
            if center not in centers:
                centers.append(center)
        return centers

    def _infer_centered_linear_pattern_centers_from_requirement(
        self,
        requirement_text: str | None,
    ) -> list[list[float]]:
        text = str(requirement_text or "").strip().lower()
        if "pattern" not in text and "array" not in text:
            return []
        if "center" not in text:
            return []
        direction_pattern = re.compile(
            r"direction\s*(?P<idx>[12])[^.]{0,120}?along(?: the)?\s+(?P<axis>[xy])(?:[\s-]?axis)?"
            r"[^0-9]{0,40}spacing[^0-9]{0,16}(?P<spacing>[0-9]+(?:\.[0-9]+)?)"
            r"[^0-9]{0,40}(?:quantity|count)[^0-9]{0,16}(?P<count>[0-9]+)",
            re.IGNORECASE,
        )
        direction_specs: dict[str, tuple[float, int]] = {}
        for match in direction_pattern.finditer(text):
            axis = str(match.group("axis")).upper()
            try:
                spacing = float(match.group("spacing"))
                count = int(match.group("count"))
            except Exception:
                continue
            if spacing <= 0.0 or count <= 1:
                continue
            direction_specs[axis] = (spacing, count)
        if not direction_specs:
            return []

        def _axis_positions(spec: tuple[float, int] | None) -> list[float]:
            if spec is None:
                return [0.0]
            spacing, count = spec
            return [
                (index - (count - 1) / 2.0) * spacing
                for index in range(count)
            ]

        x_positions = _axis_positions(direction_specs.get("X"))
        y_positions = _axis_positions(direction_specs.get("Y"))
        centers = [[float(x), float(y)] for x in x_positions for y in y_positions]
        return centers if len(centers) >= 2 else []

    def _infer_centered_square_or_rectangular_array_centers_from_requirement(
        self,
        requirement_text: str | None,
    ) -> list[list[float]]:
        text = str(requirement_text or "").strip().lower()
        if not text or ("array" not in text and "pattern" not in text):
            return []
        if "center" not in text:
            return []

        explicit_xy_offset = re.search(
            r"each\s+\w+\s*'?s?\s+center\s+is\s+([0-9]+(?:\.[0-9]+)?)\s*mm?\s+from\s+the\s+center\s+in\s+the\s+x\s*/\s*y\s+direction",
            text,
            re.IGNORECASE,
        )
        if explicit_xy_offset is not None:
            try:
                offset = float(explicit_xy_offset.group(1))
            except Exception:
                offset = 0.0
            if offset > 0.0:
                return [
                    [offset, offset],
                    [offset, -offset],
                    [-offset, offset],
                    [-offset, -offset],
                ]

        square_side_match = re.search(
            r"square\s+array[^.]{0,80}?side\s+length(?:\s+of)?\s+([0-9]+(?:\.[0-9]+)?)",
            text,
            re.IGNORECASE,
        )
        if square_side_match is not None:
            try:
                side_length = float(square_side_match.group(1))
            except Exception:
                side_length = 0.0
            if side_length > 0.0:
                half = side_length / 2.0
                return [
                    [half, half],
                    [half, -half],
                    [-half, half],
                    [-half, -half],
                ]

        return []

    def _infer_circular_pattern_centers_from_requirement(
        self,
        requirement_text: str | None,
    ) -> list[list[float]]:
        text = str(requirement_text or "").strip().lower()
        if not any(
            token in text
            for token in (
                "circular pattern",
                "pitch circle",
                "construction circle",
                "distributed circle",
                "evenly distributed",
            )
        ):
            return []

        diameter_patterns = (
            r"(?:pitch|construction|distributed)\s+circle[^0-9]{0,40}diameter(?: of)?\s*([0-9]+(?:\.[0-9]+)?)",
            r"diameter(?: of)?\s*([0-9]+(?:\.[0-9]+)?)[^.]{0,40}(?:pitch|construction|distributed)\s+circle",
            r"([0-9]+(?:\.[0-9]+)?)\s*(?:millimeters?|mm)?[^.]{0,20}(?:pitch|construction|distributed)\s+circle",
        )
        pitch_diameter: float | None = None
        for pattern in diameter_patterns:
            match = re.search(pattern, text)
            if match is None:
                continue
            try:
                value = float(match.group(1))
            except Exception:
                continue
            if value > 0.0:
                pitch_diameter = value
                break
        if pitch_diameter is None:
            return []

        quantity_match = re.search(
            r"(?:quantity|count|set(?:ting)?(?: the)? quantity to)\D{0,16}([0-9]+)",
            text,
        )
        word_quantity_matches = list(
            re.finditer(
                r"\b(one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)\b"
                r"[^,;\n]{0,64}\b(?:bolt[\s-]*)?holes?\b"
                r"(?:[^,;\n]{0,96}\b(?:pitch|construction|distributed)\s+circle\b)?",
                text,
                re.IGNORECASE,
            )
        )
        remaining_match = re.search(
            r"(?:generate(?:ing)?(?: the)? remaining|remaining)\D{0,16}([0-9]+)",
            text,
        )
        word_to_count = {
            "one": 1,
            "two": 2,
            "three": 3,
            "four": 4,
            "five": 5,
            "six": 6,
            "seven": 7,
            "eight": 8,
            "nine": 9,
            "ten": 10,
            "eleven": 11,
            "twelve": 12,
        }
        count = 0
        try:
            quantity_count = (
                int(quantity_match.group(1)) if quantity_match is not None else 0
            )
        except Exception:
            quantity_count = 0
        word_quantity = max(
            (
                word_to_count.get(str(match.group(1)).lower(), 0)
                for match in word_quantity_matches
            ),
            default=0,
        )
        try:
            remaining_count = (
                int(remaining_match.group(1)) if remaining_match is not None else 0
            )
        except Exception:
            remaining_count = 0
        if quantity_count > 1:
            count = quantity_count
        elif word_quantity > 1:
            count = word_quantity
        elif remaining_count > 1:
            count = remaining_count + 1 if "single hole" in text else remaining_count
        if count <= 1:
            return []

        start_angle_deg = 0.0
        if (
            "positive x axis" in text
            or "3 o'clock" in text
            or "right quadrant" in text
            or "right point" in text
        ):
            start_angle_deg = 0.0
        elif "top quadrant" in text or "top point" in text or "upper" in text or "positive y axis" in text or "12 o'clock" in text:
            start_angle_deg = 90.0
        elif "left quadrant" in text or "left point" in text or "negative x axis" in text or "9 o'clock" in text:
            start_angle_deg = 180.0
        elif "bottom quadrant" in text or "bottom point" in text or "lower" in text or "negative y axis" in text or "6 o'clock" in text:
            start_angle_deg = 270.0

        radius = float(pitch_diameter) / 2.0
        centers: list[list[float]] = []
        for index in range(count):
            angle = math.radians(start_angle_deg + (360.0 * index / count))
            center = [round(radius * math.cos(angle), 6), round(radius * math.sin(angle), 6)]
            if center not in centers:
                centers.append(center)
        return centers if len(centers) >= 2 else []

    def _collect_entry_hole_centers(self, entry: ActionHistoryEntry) -> list[list[float]]:
        params = entry.action_params if isinstance(entry.action_params, dict) else {}
        centers_raw = params.get("centers", params.get("positions"))
        centers: list[list[float]] = []
        if isinstance(centers_raw, list):
            for item in centers_raw:
                if isinstance(item, dict):
                    values = [
                        item.get(axis)
                        for axis in ("x", "y", "z")
                        if isinstance(item.get(axis), (int, float))
                    ]
                elif isinstance(item, (list, tuple)):
                    values = [
                        value for value in item if isinstance(value, (int, float))
                    ]
                else:
                    values = []
                if len(values) >= 2:
                    center = [float(values[0]), float(values[1])]
                    if center not in centers:
                        centers.append(center)
        if centers:
            return centers

        position_raw = params.get("position", params.get("center"))
        if isinstance(position_raw, list) and len(position_raw) >= 2:
            if isinstance(position_raw[0], (int, float)) and isinstance(
                position_raw[1], (int, float)
            ):
                return [[float(position_raw[0]), float(position_raw[1])]]
        return []

    def _collect_history_hole_centers(
        self,
        history: list[ActionHistoryEntry],
    ) -> list[list[float]]:
        centers: list[list[float]] = []
        for index, entry in enumerate(history):
            if not self._history_action_materially_changes_geometry(history, index):
                continue
            if entry.action_type in {
                CADActionType.HOLE,
                CADActionType.SPHERE_RECESS,
            }:
                for center in self._collect_entry_hole_centers(entry):
                    if center not in centers:
                        centers.append(center)
                continue
            if entry.action_type != CADActionType.CUT_EXTRUDE:
                continue
            sketch_index = self._find_preceding_sketch_index(history, index)
            if sketch_index is None:
                continue
            if not self._history_window_has_profile_actions(
                history,
                sketch_index=sketch_index,
                action_index=index,
                allowed_action_types={CADActionType.ADD_CIRCLE},
            ):
                continue
            for candidate in history[sketch_index + 1 : index]:
                if candidate.action_type != CADActionType.ADD_CIRCLE:
                    continue
                for center in self._collect_entry_hole_centers(candidate):
                    if center not in centers:
                        centers.append(center)
        return centers

    def _center_sets_match_2d(
        self,
        actual: list[list[float]],
        expected: list[list[float]],
        tolerance: float = 0.75,
        allow_translation: bool = False,
    ) -> bool:
        if not expected:
            return True
        if len(actual) != len(expected):
            return False
        if self._center_sets_match_2d_direct(
            actual,
            expected,
            tolerance=tolerance,
        ):
            return True
        if not allow_translation:
            return False
        normalized_actual = self._center_points_about_centroid(actual)
        normalized_expected = self._center_points_about_centroid(expected)
        if not normalized_actual or not normalized_expected:
            return False
        return self._center_sets_match_2d_direct(
            normalized_actual,
            normalized_expected,
            tolerance=tolerance,
        )

    def _center_sets_match_with_requirement_coordinate_modes(
        self,
        actual: list[list[float]],
        expected: list[list[float]],
        *,
        requirement_text: str | None,
        tolerance: float = 0.75,
        allow_translation: bool = False,
    ) -> bool:
        if not expected:
            return True
        if len(actual) != len(expected):
            return False
        if self._center_sets_match_2d_direct(
            actual,
            expected,
            tolerance=tolerance,
        ):
            return True
        translated_expected = self._translate_rectangular_host_face_local_centers(
            requirement_text,
            expected,
            tolerance=tolerance,
        )
        if translated_expected and self._center_sets_match_2d_direct(
            actual,
            translated_expected,
            tolerance=tolerance,
        ):
            return True
        if allow_translation:
            return self._center_sets_match_2d(
                actual,
                expected,
                tolerance=tolerance,
                allow_translation=True,
            )
        return False

    def _center_sets_match_2d_direct(
        self,
        actual: list[list[float]],
        expected: list[list[float]],
        *,
        tolerance: float,
    ) -> bool:
        remaining = [list(item) for item in actual]
        for expected_center in expected:
            match_index: int | None = None
            for index, actual_center in enumerate(remaining):
                if len(actual_center) < 2 or len(expected_center) < 2:
                    continue
                if (
                    abs(float(actual_center[0]) - float(expected_center[0])) <= tolerance
                    and abs(float(actual_center[1]) - float(expected_center[1])) <= tolerance
                ):
                    match_index = index
                    break
            if match_index is None:
                return False
            remaining.pop(match_index)
        return True

    def _center_points_about_centroid(
        self,
        centers: list[list[float]],
    ) -> list[list[float]]:
        points = [
            [float(item[0]), float(item[1])]
            for item in centers
            if len(item) >= 2
        ]
        if len(points) != len(centers) or not points:
            return []
        centroid_x = sum(point[0] for point in points) / len(points)
        centroid_y = sum(point[1] for point in points) / len(points)
        return [
            [point[0] - centroid_x, point[1] - centroid_y]
            for point in points
        ]

    def _translate_rectangular_host_face_local_centers(
        self,
        requirement_text: str | None,
        expected_centers: list[list[float]],
        *,
        tolerance: float = 0.75,
    ) -> list[list[float]] | None:
        host_dims = self._infer_rectangular_host_face_local_coordinate_frame(
            requirement_text,
            expected_centers,
            tolerance=tolerance,
        )
        if host_dims is None:
            return None
        width, height = host_dims
        translated: list[list[float]] = []
        for center in expected_centers:
            if len(center) < 2:
                return None
            translated.append(
                [
                    round(float(center[0]) - width / 2.0, 6),
                    round(float(center[1]) - height / 2.0, 6),
                ]
            )
        return translated

    def _infer_rectangular_host_face_local_coordinate_frame(
        self,
        requirement_text: str | None,
        expected_centers: list[list[float]],
        *,
        tolerance: float = 0.75,
    ) -> tuple[float, float] | None:
        if len(expected_centers) < 2:
            return None
        text = str(requirement_text or "").strip().lower()
        if not text:
            return None
        if not any(
            token in text
            for token in (
                "points with coordinates",
                "point with coordinates",
                "draw four points",
                "draw points",
                "position tab",
                "sketch coordinates",
                "sketch coordinate",
                "face-sketch coordinates",
                "face sketch coordinates",
                "local face sketch coordinates",
                "already-centered offsets",
            )
        ):
            return None
        if not any(
            token in text
            for token in (
                "plate surface",
                "top surface",
                "top face",
                "surface of the plate",
                "select the surface",
            )
        ):
            return None
        host_dims = self._extract_rectangular_host_face_dimensions(requirement_text)
        if host_dims is None:
            return None
        width, height = host_dims
        xs: list[float] = []
        ys: list[float] = []
        for center in expected_centers:
            if len(center) < 2:
                return None
            xs.append(float(center[0]))
            ys.append(float(center[1]))
        if not xs or not ys:
            return None
        if min(xs) < -tolerance or min(ys) < -tolerance:
            return None
        if max(xs) > width + tolerance or max(ys) > height + tolerance:
            return None
        if max(xs) <= width / 2.0 + tolerance and max(ys) <= height / 2.0 + tolerance:
            return None
        return width, height

    def _filter_pattern_local_anchor_centers_for_requirement(
        self,
        requirement_text: str | None,
        realized_centers: list[list[float]],
        expected_centers: list[list[float]],
        *,
        tolerance: float = 0.75,
    ) -> list[list[float]]:
        text = str(requirement_text or "").strip().lower()
        if "pitch circle" not in text and "circular pattern" not in text:
            return realized_centers
        return self._filter_extra_center_feature_for_requirement(
            requirement_text,
            realized_centers,
            expected_centers,
            tolerance=tolerance,
        )

    def _filter_extra_center_feature_for_requirement(
        self,
        requirement_text: str | None,
        realized_centers: list[list[float]],
        expected_centers: list[list[float]],
        *,
        tolerance: float = 0.75,
    ) -> list[list[float]]:
        if len(realized_centers) != len(expected_centers) + 1:
            return realized_centers
        text = str(requirement_text or "").strip().lower()
        if not any(
            token in text
            for token in ("center hole", "concentric", "clearance", "bore", "inner diameter")
        ):
            return realized_centers
        expected_points = [
            [float(item[0]), float(item[1])]
            for item in expected_centers
            if len(item) >= 2
        ]
        realized_points = [
            [float(item[0]), float(item[1])]
            for item in realized_centers
            if len(item) >= 2
        ]
        if len(expected_points) != len(expected_centers) or len(realized_points) != len(realized_centers):
            return realized_centers
        centroid_x = sum(point[0] for point in expected_points) / len(expected_points)
        centroid_y = sum(point[1] for point in expected_points) / len(expected_points)
        removable_index: int | None = None
        removable_distance: float | None = None
        for index, point in enumerate(realized_points):
            distance = math.hypot(point[0] - centroid_x, point[1] - centroid_y)
            if removable_distance is None or distance < removable_distance:
                removable_index = index
                removable_distance = distance
        if removable_index is None or removable_distance is None:
            return realized_centers
        if removable_distance > max(1.5, tolerance * 2.0):
            return realized_centers
        filtered_points = [
            point for index, point in enumerate(realized_points) if index != removable_index
        ]
        if self._center_sets_match_with_requirement_coordinate_modes(
            filtered_points,
            expected_points,
            requirement_text=requirement_text,
            tolerance=tolerance,
            allow_translation=False,
        ):
            return filtered_points
        return realized_centers

    def _extract_rectangular_host_face_dimensions(
        self,
        requirement_text: str | None,
    ) -> tuple[float, float] | None:
        text = str(requirement_text or "").strip().lower()
        if not text:
            return None
        patterns = (
            r"([0-9]+(?:\.[0-9]+)?)\s*(?:x|×)\s*([0-9]+(?:\.[0-9]+)?)\s*(?:x|×)\s*([0-9]+(?:\.[0-9]+)?)\s*(?:millimeter|millimeters|mm)\s+(?:plate|block|box|slab|base)",
            r"([0-9]+(?:\.[0-9]+)?)\s*(?:x|×)\s*([0-9]+(?:\.[0-9]+)?)\s*(?:millimeter|millimeters|mm)\s+(?:plate|block|box|slab|base)",
            r"draw\s+(?:a\s+)?([0-9]+(?:\.[0-9]+)?)\s*(?:x|×)\s*([0-9]+(?:\.[0-9]+)?)\s*(?:millimeter|millimeters|mm)\s+rectangle",
            r"draw\s+(?:a\s+)?rectangle[^0-9]{0,32}([0-9]+(?:\.[0-9]+)?)\s*(?:x|×)\s*([0-9]+(?:\.[0-9]+)?)",
            r"rectangle[^0-9]{0,24}([0-9]+(?:\.[0-9]+)?)\s*(?:x|×)\s*([0-9]+(?:\.[0-9]+)?)",
        )
        for pattern in patterns:
            match = re.search(pattern, text)
            if match is None:
                continue
            try:
                width = float(match.group(1))
                height = float(match.group(2))
            except Exception:
                continue
            if width > 0.0 and height > 0.0:
                return width, height
        return None

    def _extract_overall_bbox_dimensions(
        self,
        requirement_text: str | None,
    ) -> list[float]:
        text = str(requirement_text or "").strip().lower()
        if not text:
            return []
        patterns = (
            r"overall dimensions[^0-9]{0,32}([0-9]+(?:\.[0-9]+)?)\s*(?:mm)?\s*(?:x|×)\s*([0-9]+(?:\.[0-9]+)?)\s*(?:mm)?\s*(?:x|×)\s*([0-9]+(?:\.[0-9]+)?)\s*(?:mm)?",
            r"bounding box[^0-9]{0,32}([0-9]+(?:\.[0-9]+)?)\s*(?:mm)?\s*(?:x|×)\s*([0-9]+(?:\.[0-9]+)?)\s*(?:mm)?\s*(?:x|×)\s*([0-9]+(?:\.[0-9]+)?)\s*(?:mm)?",
        )
        for pattern in patterns:
            match = re.search(pattern, text)
            if match is None:
                continue
            try:
                dims = [float(match.group(1)), float(match.group(2)), float(match.group(3))]
            except Exception:
                continue
            if all(value > 0.0 for value in dims):
                return dims
        return []

    def _extract_expected_part_count(
        self,
        requirement_text: str | None,
    ) -> int | None:
        text = str(requirement_text or "").strip().lower()
        if not text:
            return None
        if any(
            token in text
            for token in (
                "two-part",
                "two part",
                "separate parts",
                "lid and base",
                "top lid",
                "bottom base",
            )
        ):
            return 2
        return None

    def _general_geometry_probe_blockers(
        self,
        *,
        solids: int,
        bbox: list[float],
        expected_bbox: list[float],
        expected_part_count: int | None,
        suspected_detached_fragment_count: int = 0,
    ) -> list[str]:
        blockers: list[str] = []
        if expected_part_count is not None and solids != expected_part_count:
            blockers.append("unexpected_part_count_for_requirement")
        if (
            expected_part_count is not None
            and expected_part_count >= 2
            and suspected_detached_fragment_count > 0
        ):
            blockers.append("suspected_detached_feature_fragment")
        if expected_bbox and len(bbox) >= 3:
            observed_dims = [abs(float(value)) for value in bbox[:3]]
            unmatched_targets = 0
            for target in expected_bbox[:3]:
                tolerance = max(1.0, abs(float(target)) * 0.1)
                if not any(abs(observed - float(target)) <= tolerance for observed in observed_dims):
                    unmatched_targets += 1
            if unmatched_targets > 0:
                blockers.append("bbox_dimension_mismatch")
        return blockers

    def _half_shell_probe_signals(
        self,
        *,
        snapshot: CADStateSnapshot,
        requirement_text: str | None,
    ) -> dict[str, Any]:
        text = str(requirement_text or "").strip().lower()
        hinge_requested = "hinge" in text
        global_bounds = self._snapshot_global_bbox_bounds(snapshot)
        x_min, x_max, y_min, y_max, z_min, z_max = global_bounds
        axis_tolerances = (
            self._extent_tolerance(x_min, x_max),
            self._extent_tolerance(y_min, y_max),
            self._extent_tolerance(z_min, z_max),
        )
        horizontal_spans = (
            max(0.0, x_max - x_min),
            max(0.0, y_max - y_min),
        )
        horizontal_reference = max(horizontal_spans) if any(horizontal_spans) else 0.0
        hinge_faces: list[dict[str, Any]] = []
        boundary_cylindrical_face_count = 0

        for face in self._snapshot_feature_faces(snapshot):
            if str(getattr(face, "geom_type", "")).strip().upper() != "CYLINDER":
                continue
            bbox = getattr(face, "bbox", None)
            if bbox is None:
                continue
            boundary_cylindrical_face_count += 1
            axis_index = self._face_parallel_axis_index(face)
            if axis_index is None or axis_index == 2:
                continue
            span = abs(
                float((bbox.xlen, bbox.ylen, bbox.zlen)[axis_index])
            )
            if span < max(4.0, horizontal_reference * 0.18):
                continue
            surface_center = self._snapshot_feature_surface_center_point(face)
            if not (
                isinstance(surface_center, list)
                and len(surface_center) >= 3
                and all(isinstance(item, (int, float)) for item in surface_center[:3])
            ):
                continue
            boundary_axis: int | None = None
            boundary_side: str | None = None
            for candidate_axis in range(3):
                if candidate_axis == axis_index:
                    continue
                center_value = float(surface_center[candidate_axis])
                lower_bound = global_bounds[candidate_axis * 2]
                upper_bound = global_bounds[candidate_axis * 2 + 1]
                tolerance = max(
                    axis_tolerances[candidate_axis],
                    abs(float(getattr(face, "radius", 0.0) or 0.0)) * 1.5,
                )
                if self._near_min(center_value, lower_bound, tolerance):
                    boundary_axis = candidate_axis
                    boundary_side = "min"
                    break
                if self._near_max(center_value, upper_bound, tolerance):
                    boundary_axis = candidate_axis
                    boundary_side = "max"
                    break
            if boundary_axis is None:
                continue
            hinge_faces.append(
                {
                    "face_id": str(getattr(face, "face_id", "") or "").strip(),
                    "axis": "XYZ"[axis_index],
                    "axis_index": axis_index,
                    "span": round(span, 6),
                    "radius": round(float(getattr(face, "radius", 0.0) or 0.0), 6),
                    "boundary_axis": "XYZ"[boundary_axis],
                    "boundary_side": boundary_side,
                }
            )

        dominant_hinge_face = max(
            hinge_faces,
            key=lambda item: float(item.get("span") or 0.0),
            default=None,
        )
        signals: dict[str, Any] = {
            "hinge_requested": hinge_requested,
            "boundary_cylindrical_face_count": boundary_cylindrical_face_count,
            "hinge_like_cylinder_count": len(hinge_faces),
            "hinge_like_face_ids": [
                item["face_id"] for item in hinge_faces if str(item.get("face_id") or "").strip()
            ][:4],
        }
        if dominant_hinge_face is not None:
            signals["hinge_like_axis"] = dominant_hinge_face["axis"]
            signals["hinge_like_span"] = dominant_hinge_face["span"]
            signals["hinge_like_radius"] = dominant_hinge_face["radius"]
            signals["hinge_boundary_axis"] = dominant_hinge_face["boundary_axis"]
            signals["hinge_boundary_side"] = dominant_hinge_face["boundary_side"]
        return signals

    def _half_shell_probe_blockers(
        self,
        *,
        solids: int,
        bbox: list[float],
        expected_bbox: list[float],
        expected_part_count: int | None,
        suspected_detached_fragment_count: int,
        hinge_requested: bool,
        hinge_like_cylinder_count: int,
    ) -> list[str]:
        blockers = self._general_geometry_probe_blockers(
            solids=solids,
            bbox=bbox,
            expected_bbox=expected_bbox,
            expected_part_count=expected_part_count,
            suspected_detached_fragment_count=suspected_detached_fragment_count,
        )
        if hinge_requested and hinge_like_cylinder_count <= 0:
            blockers.append("missing_hinge_like_cylindrical_evidence")
        return blockers

    def _snapshot_detached_fragment_signals(
        self,
        snapshot: CADStateSnapshot,
    ) -> dict[str, Any]:
        geometry_objects = snapshot.geometry_objects
        if geometry_objects is None or len(geometry_objects.solids) < 2:
            return {}

        ranked_solids = [
            solid
            for solid in geometry_objects.solids
            if abs(float(getattr(solid, "volume", 0.0) or 0.0)) > 1e-6
        ]
        if len(ranked_solids) < 2:
            return {}

        ranked_solids.sort(
            key=lambda solid: abs(float(getattr(solid, "volume", 0.0) or 0.0)),
            reverse=True,
        )
        total_volume = sum(
            abs(float(getattr(solid, "volume", 0.0) or 0.0)) for solid in ranked_solids
        )
        if total_volume <= 1e-6:
            return {}

        dominant = ranked_solids[0]
        dominant_volume = abs(float(getattr(dominant, "volume", 0.0) or 0.0))
        dominant_fraction = dominant_volume / total_volume
        dominant_bbox = getattr(dominant, "bbox", None)
        dominant_max_span = max(
            float(getattr(dominant_bbox, "xlen", 0.0) or 0.0),
            float(getattr(dominant_bbox, "ylen", 0.0) or 0.0),
            float(getattr(dominant_bbox, "zlen", 0.0) or 0.0),
        )
        fragment_ids: list[str] = []
        fragment_volume_fractions: list[float] = []
        fragment_bbox_max_spans: list[float] = []
        fragment_span_limit = max(12.0, dominant_max_span * 0.25)

        for solid in ranked_solids[1:]:
            volume = abs(float(getattr(solid, "volume", 0.0) or 0.0))
            fraction = volume / total_volume
            bbox = getattr(solid, "bbox", None)
            bbox_max_span = max(
                float(getattr(bbox, "xlen", 0.0) or 0.0),
                float(getattr(bbox, "ylen", 0.0) or 0.0),
                float(getattr(bbox, "zlen", 0.0) or 0.0),
            )
            if (
                dominant_fraction >= 0.92
                and fraction <= 0.08
                and bbox_max_span <= fragment_span_limit
            ):
                solid_id = str(getattr(solid, "solid_id", "") or "").strip()
                if solid_id:
                    fragment_ids.append(solid_id)
                fragment_volume_fractions.append(round(fraction, 6))
                fragment_bbox_max_spans.append(round(bbox_max_span, 6))

        signals: dict[str, Any] = {
            "dominant_solid_volume_fraction": round(dominant_fraction, 6),
            "secondary_solid_count": max(0, len(ranked_solids) - 1),
        }
        if fragment_ids:
            signals["suspected_detached_fragment_count"] = len(fragment_ids)
            signals["suspected_detached_fragment_solid_ids"] = fragment_ids[:4]
            signals["suspected_detached_fragment_volume_fractions"] = (
                fragment_volume_fractions[:4]
            )
            signals["suspected_detached_fragment_bbox_max_spans"] = (
                fragment_bbox_max_spans[:4]
            )
        return signals

    def _requirement_uses_centered_pattern_local_coordinates(
        self,
        requirement_text: str | None,
    ) -> bool:
        if self._extract_explicit_hole_centers_from_requirement(requirement_text):
            return False
        if len(self._extract_explicit_point_centers_from_requirement(requirement_text)) >= 2:
            return False
        return (
            len(self._infer_centered_linear_pattern_centers_from_requirement(requirement_text))
            >= 2
        )

    def _requirement_requires_host_plane_open_spherical_recess(
        self,
        requirement_text: str | None,
    ) -> bool:
        text = str(requirement_text or "").strip().lower()
        if not text or "diameter edge" not in text:
            return False
        return (
            "coincides with the top face" in text
            or "coincides with the face" in text
            or "diameter edge lies on the top face" in text
            or "diameter edge lies on the face" in text
        )

    def _action_uses_matching_edge_targets(
        self,
        history: list[ActionHistoryEntry],
        action_index: int,
        entry: ActionHistoryEntry,
        edge_targets: tuple[str, ...],
        face_targets: tuple[str, ...] = (),
    ) -> bool:
        edge_refs = entry.action_params.get("edge_refs")
        if isinstance(edge_refs, list) and any(
            isinstance(item, str) and item.strip() for item in edge_refs
        ):
            if not edge_targets:
                return True
            resolved_edges: list[tuple[TopologyEdgeEntity, CADStateSnapshot, set[str]]] = []
            for item in edge_refs:
                if not isinstance(item, str) or not item.strip():
                    continue
                edge = self._resolve_topology_edge_for_ref(history, item)
                snapshot = self._resolve_snapshot_for_topology_ref(history, item)
                if edge is None or snapshot is None:
                    return False
                resolved_edges.append(
                    (
                        edge,
                        snapshot,
                        self._topology_edge_labels(snapshot.topology_index, edge),
                    )
                )
            return bool(resolved_edges) and all(
                self._edge_matches_semantic_targets(
                    topology_index=snapshot.topology_index,
                    edge=edge,
                    labels=labels,
                    edge_targets=edge_targets,
                    face_targets=face_targets,
                )
                for edge, snapshot, labels in resolved_edges
            )

        selector_text = " ".join(
            str(item).strip().lower()
            for item in (
                entry.action_params.get("edges_selector", ""),
                entry.action_params.get("edge_scope", ""),
            )
            if isinstance(item, str) and item.strip()
        )
        if not selector_text:
            return False
        if not edge_targets:
            return True

        target_tokens = {
            "top_outer_edges": (">z", "top_outer", "top outer"),
            "bottom_outer_edges": ("<z", "bottom_outer", "bottom outer"),
            "outer_edges": ("|z", "outer"),
            "top_edges": (">z", "top"),
            "bottom_edges": ("<z", "bottom"),
        }
        return any(
            any(token in selector_text for token in target_tokens.get(target, ()))
            for target in edge_targets
        )

    def _edge_matches_semantic_targets(
        self,
        topology_index: TopologyObjectIndex | None,
        edge: TopologyEdgeEntity | None,
        labels: set[str],
        edge_targets: tuple[str, ...],
        face_targets: tuple[str, ...],
    ) -> bool:
        normalized_targets = self._normalize_edge_targets_for_matching(edge_targets)
        if not normalized_targets:
            return True
        if topology_index is None or edge is None:
            return False
        if self._labels_match_edge_targets(labels, normalized_targets):
            return True
        if not any(
            target in {"inner_edges", "inner_top_edges", "inner_bottom_edges"}
            for target in normalized_targets
        ):
            return False
        if "inner" not in labels:
            return False

        relevant_edges = [
            item
            for item in topology_index.edges
            if "inner" in self._topology_edge_labels(topology_index, item)
        ]
        target_face_labels = {
            item for item in face_targets if item in {"front", "back", "left", "right"}
        }
        if target_face_labels:
            relevant_edges = [
                item
                for item in relevant_edges
                if self._topology_edge_labels(topology_index, item).intersection(target_face_labels)
            ]
            if not labels.intersection(target_face_labels):
                return False
        if not relevant_edges:
            return False
        edge_center_z = self._topology_edge_center_z(edge)
        if edge_center_z is None:
            return False
        relevant_z = [
            z_value
            for z_value in (
                self._topology_edge_center_z(item) for item in relevant_edges
            )
            if isinstance(z_value, (int, float))
        ]
        if not relevant_z:
            return False
        tolerance = max(1.0, (max(relevant_z) - min(relevant_z)) * 0.08)
        for target in normalized_targets:
            if target == "inner_edges":
                return True
            if target == "inner_bottom_edges" and abs(edge_center_z - min(relevant_z)) <= tolerance:
                return True
            if target == "inner_top_edges" and abs(edge_center_z - max(relevant_z)) <= tolerance:
                return True
        return False

    def _dominant_bbox_axis(self, snapshot: CADStateSnapshot | None) -> str | None:
        if snapshot is None:
            return None
        bbox = snapshot.geometry.bbox
        if not isinstance(bbox, list) or len(bbox) < 3:
            return None
        try:
            dims = [abs(float(bbox[0])), abs(float(bbox[1])), abs(float(bbox[2]))]
        except Exception:
            return None
        axis_index = max(range(3), key=lambda idx: dims[idx])
        return ("X", "Y", "Z")[axis_index]

    def _axis_end_face_targets(self, axis: str | None) -> tuple[str, ...]:
        if axis == "X":
            return ("left", "right")
        if axis == "Y":
            return ("front", "back")
        return ("top", "bottom")

    def _history_has_post_solid_axial_inner_cut(
        self,
        history: list[ActionHistoryEntry],
        first_solid_index: int | None,
    ) -> bool:
        if first_solid_index is None or first_solid_index >= len(history):
            return False
        primary_axis = self._dominant_bbox_axis(history[first_solid_index].result_snapshot)
        face_targets = self._axis_end_face_targets(primary_axis)
        for action_index, entry in enumerate(history):
            if action_index <= first_solid_index:
                continue
            if entry.action_type not in {CADActionType.CUT_EXTRUDE, CADActionType.HOLE}:
                continue
            if not self._history_action_materially_changes_geometry(history, action_index):
                continue
            sketch_index = self._find_preceding_sketch_index(history, action_index)
            if sketch_index is None or sketch_index <= first_solid_index:
                continue
            sketch_entry = history[sketch_index]
            if not self._sketch_matches_face_targets(history, sketch_entry, face_targets):
                continue
            if not self._history_window_has_profile_actions(
                history,
                sketch_index=sketch_index,
                action_index=action_index,
            ):
                continue
            return True
        return False

    def build_unstructured_content(
        self,
        output: (
            ExecuteBuild123dOutput
            | ExecuteBuild123dProbeOutput
            | CADActionOutput
            | QuerySnapshotOutput
            | QuerySketchOutput
            | QueryGeometryOutput
            | QueryTopologyOutput
            | QueryFeatureProbesOutput
            | RenderViewOutput
            | ValidateRequirementOutput
        ),
    ) -> list[ContentBlock]:
        """Build MCP content blocks from structured output."""
        # Handle CADActionOutput
        if isinstance(output, CADActionOutput):
            return self._build_action_content(output)
        if isinstance(output, QuerySnapshotOutput):
            return [
                TextContent(
                    type="text",
                    text=(
                        "Snapshot query "
                        f"{'success' if output.success else 'failure'}; "
                        f"session_id={output.session_id}; step={output.step}"
                    ),
                )
            ]
        if isinstance(output, QuerySketchOutput):
            sketch_state = output.sketch_state
            sketch_summary = (
                f"paths={len(sketch_state.paths)}; profiles={len(sketch_state.profiles)}; "
                f"issues={len(sketch_state.issues)}"
                if sketch_state is not None
                else "sketch=none"
            )
            return [
                TextContent(
                    type="text",
                    text=(
                        "Sketch query "
                        f"{'success' if output.success else 'failure'}; "
                        f"session_id={output.session_id}; step={output.step}; "
                        f"{sketch_summary}"
                    ),
                )
            ]
        if isinstance(output, QueryGeometryOutput):
            geometry = output.geometry
            geometry_summary = (
                f"solids={geometry.solids}; faces={geometry.faces}; edges={geometry.edges}; "
                f"volume={geometry.volume:.2f}"
                if geometry is not None
                else "geometry=none"
            )
            match_summary = f"matched_entity_ids={len(output.matched_entity_ids)}"
            window_summary = (
                f"next_offsets=({output.next_solid_offset},"
                f"{output.next_face_offset},{output.next_edge_offset})"
            )
            return [
                TextContent(
                    type="text",
                    text=(
                        "Geometry query "
                        f"{'success' if output.success else 'failure'}; "
                        f"session_id={output.session_id}; step={output.step}; "
                        f"{geometry_summary}; {match_summary}; {window_summary}"
                    ),
                )
            ]
        if isinstance(output, QueryTopologyOutput):
            topology = output.topology_index
            topology_summary = (
                f"faces={len(topology.faces)}; edges={len(topology.edges)}"
                if topology is not None
                else "topology=none"
            )
            match_summary = (
                f"matched_ref_ids={len(output.matched_ref_ids)}; "
                f"matched_entity_ids={len(output.matched_entity_ids)}"
            )
            candidate_summary = (
                f"candidate_sets={len(output.candidate_sets)}; "
                f"applied_hints={','.join(output.applied_hints) or 'none'}"
            )
            window_summary = (
                f"next_offsets=({output.next_face_offset},{output.next_edge_offset})"
            )
            return [
                TextContent(
                    type="text",
                    text=(
                        "Topology query "
                        f"{'success' if output.success else 'failure'}; "
                        f"session_id={output.session_id}; step={output.step}; "
                        f"{topology_summary}; {match_summary}; "
                        f"{candidate_summary}; {window_summary}"
                    ),
                )
            ]
        if isinstance(output, QueryFeatureProbesOutput):
            return [
                TextContent(
                    type="text",
                    text=(
                        "Feature probes "
                        f"{'success' if output.success else 'failure'}; "
                        f"session_id={output.session_id}; step={output.step}; "
                        f"families={len(output.detected_families)}; probes={len(output.probes)}; "
                        f"summary={output.summary}"
                    ),
                )
            ]
        if isinstance(output, RenderViewOutput):
            content_blocks: list[ContentBlock] = [
                TextContent(
                    type="text",
                    text=(
                        "Render view "
                        f"{'success' if output.success else 'failure'}; "
                        f"session_id={output.session_id}; step={output.step}; "
                        f"view_file={output.view_file or 'none'}; "
                        f"focused_entity_ids={len(output.focused_entity_ids)}"
                    ),
                )
            ]
            for artifact in output.artifacts:
                if artifact.content_base64 is None:
                    continue
                content_blocks.append(
                    EmbeddedResource(
                        type="resource",
                        resource=BlobResourceContents(
                            uri=artifact.uri,
                            mimeType=artifact.mime_type,
                            blob=artifact.content_base64,
                        ),
                    )
                )
            return content_blocks
        if isinstance(output, ValidateRequirementOutput):
            return [
                TextContent(
                    type="text",
                    text=(
                        "Requirement validation "
                        f"{'success' if output.success else 'failure'}; "
                        f"session_id={output.session_id}; step={output.step}; "
                        f"is_complete={output.is_complete}; blockers={len(output.blockers)}; "
                        f"core_checks={len(output.core_checks)}; diagnostics={len(output.diagnostic_checks)}"
                    ),
                )
            ]
        if isinstance(output, ExecuteBuild123dProbeOutput):
            return [
                TextContent(
                    type="text",
                    text=(
                        "Build123d probe "
                        f"{'success' if output.success else 'failure'}; "
                        f"session_id={output.session_id or 'none'}; step={output.step}; "
                        f"output_files={len(output.output_files)}; "
                        f"summary_keys={len(output.probe_summary)}"
                    ),
                )
            ]

        # Handle ExecuteBuild123dOutput (original behavior)
        content_blocks: list[ContentBlock] = [
            TextContent(
                type="text",
                text=self._build_summary(output),
            )
        ]

        for artifact in output.artifacts:
            if artifact.content_base64 is None:
                continue
            content_blocks.append(
                EmbeddedResource(
                    type="resource",
                    resource=BlobResourceContents(
                        uri=artifact.uri,
                        mimeType=artifact.mime_type,
                        blob=artifact.content_base64,
                    ),
                )
            )

        return content_blocks

    def _build_action_content(
        self,
        output: CADActionOutput,
    ) -> list[ContentBlock]:
        """Build MCP content blocks for CAD action output."""
        status = "success" if output.success else "failure"
        summary = (
            f"CAD action {status}; "
            f"action={output.executed_action.get('type')}; "
            f"step={output.snapshot.step}; "
            f"features={len(output.snapshot.features)}; "
            f"solids={output.snapshot.geometry.solids}; "
            f"faces={output.snapshot.geometry.faces}; "
            f"volume={output.snapshot.geometry.volume:.2f}; "
            f"error_code={output.error_code.value}"
        )

        if output.snapshot.issues:
            summary += f"; issues={len(output.snapshot.issues)}"

        content_blocks: list[ContentBlock] = [
            TextContent(
                type="text",
                text=summary,
            )
        ]

        # Add preview images as resources
        for filename in output.snapshot.images:
            if filename in output.output_files:
                artifact = next(
                    (a for a in output.artifacts if a.filename == filename), None
                )
                if artifact and artifact.content_base64:
                    content_blocks.append(
                        EmbeddedResource(
                            type="resource",
                            resource=BlobResourceContents(
                                uri=artifact.uri,
                                mimeType=artifact.mime_type,
                                blob=artifact.content_base64,
                            ),
                        )
                    )

        # Add STEP file as resource
        if output.step_file:
            artifact = next(
                (a for a in output.artifacts if a.filename == output.step_file), None
            )
            if artifact and artifact.content_base64:
                content_blocks.append(
                    EmbeddedResource(
                        type="resource",
                        resource=BlobResourceContents(
                            uri=artifact.uri,
                            mimeType=artifact.mime_type,
                            blob=artifact.content_base64,
                        ),
                    )
                )

        return content_blocks

    def _build_summary(self, output: ExecuteBuild123dOutput) -> str:
        status = "success" if output.success else "failure"
        summary = (
            f"Sandbox execution {status}; "
            f"artifacts={len(output.output_files)}; "
            f"error_code={output.error_code.value}"
        )
        if output.session_id:
            summary += (
                f"; session_id={output.session_id}; "
                f"step={output.step}; "
                f"session_state_persisted={str(output.session_state_persisted).lower()}"
            )
        evaluation = output.evaluation

        if (
            evaluation.status == EvaluationStatus.SUCCESS
            and evaluation.score is not None
        ):
            summary += f"; eval={evaluation.mode.value}:{evaluation.score:.4f}"
        elif evaluation.status != EvaluationStatus.NOT_REQUESTED:
            summary += f"; eval={evaluation.mode.value}:{evaluation.status.value}"

        return summary

    async def _evaluate(
        self,
        request: ExecuteBuild123dInput,
        sandbox_result: SandboxResult,
        error_code: SandboxErrorCode,
    ) -> ExecutionEvaluation:
        return await self._evaluation_orchestrator.evaluate(
            request=request,
            sandbox_result=sandbox_result,
            error_code=error_code,
        )

    def _not_requested_evaluation(self) -> ExecutionEvaluation:
        return ExecutionEvaluation(
            mode=EvaluationMode.NONE,
            status=EvaluationStatus.NOT_REQUESTED,
            summary="Evaluation not requested",
        )

    def _resolve_mime_type(self, filename: str) -> str:
        if filename.endswith(".step") or filename.endswith(".stp"):
            return DEFAULT_STEP_MIME_TYPE

        guessed, _ = mimetypes.guess_type(filename)
        if guessed:
            return guessed

        return "application/octet-stream"

    def _map_error_code(
        self,
        error_message: str | None,
        stderr: str | None,
    ) -> SandboxErrorCode:
        combined = f"{error_message or ''}\n{stderr or ''}".lower()

        if "invalid_reference" in combined or "topology reference" in combined:
            return SandboxErrorCode.INVALID_REFERENCE

        if "timeout" in combined or "timed out" in combined:
            return SandboxErrorCode.TIMEOUT

        if "image not found" in combined:
            return SandboxErrorCode.IMAGE_NOT_FOUND

        if (
            "docker api error" in combined
            or "docker daemon appears unavailable" in combined
            or "error while fetching server api version" in combined
        ):
            return SandboxErrorCode.DOCKER_API_ERROR

        return SandboxErrorCode.EXECUTION_ERROR

    def _validate_action_references(
        self,
        action_type: CADActionType,
        action_params: dict[str, Any],
        action_history: list[ActionHistoryEntry],
    ) -> str | None:
        definition = get_action_definition(action_type)
        if definition is None or not definition.topology_fields:
            return None

        latest_entry = action_history[-1] if action_history else None
        expected_step = latest_entry.step if latest_entry is not None else 0
        topology_index = (
            latest_entry.result_snapshot.topology_index
            if latest_entry is not None
            else None
        )
        actions_requiring_planar_face_ref = {
            CADActionType.CREATE_SKETCH,
            CADActionType.HOLE,
            CADActionType.SPHERE_RECESS,
        }

        for field_name in definition.topology_fields:
            if field_name == "face_ref":
                normalized_params = self._normalize_face_ref_alias_in_params(
                    action_params,
                    action_history,
                )
                action_params.update(normalized_params)
                face_ref = action_params.get("face_ref")
                if face_ref in (None, ""):
                    continue
                parsed = parse_topology_ref(face_ref)
                if parsed is None or parsed.get("kind") != "face":
                    if isinstance(face_ref, str) and face_ref.strip() and not face_ref.strip().startswith("face:"):
                        return (
                            "invalid_reference: malformed face_ref "
                            f"{face_ref!r}; face_ref must be one concrete "
                            "`face:<step>:<entity_id>` ref from the latest query_topology, "
                            "not a candidate-set label or host-role alias"
                        )
                    return f"invalid_reference: malformed face_ref={face_ref!r}"
                if parsed["step"] != expected_step:
                    return (
                        "invalid_reference: stale face_ref "
                        f"{face_ref!r}; expected step {expected_step}"
                    )
                if topology_index is None:
                    return (
                        "invalid_reference: no topology snapshot available; "
                        "run query_topology before using face_ref"
                    )
                matched_face = next(
                    (
                        face
                        for face in topology_index.faces
                        if face.face_ref == parsed["ref"]
                    ),
                    None,
                )
                if matched_face is None:
                    return (
                        "invalid_reference: face_ref not found in latest topology "
                        f"{face_ref!r}"
                    )
                if action_type in actions_requiring_planar_face_ref:
                    geom_type = str(getattr(matched_face, "geom_type", "") or "").upper()
                    if geom_type and geom_type != "PLANE":
                        return (
                            "invalid_reference: face_ref "
                            f"{face_ref!r} is not planar (geom_type={geom_type}); "
                            "use a planar face candidate from the latest query_topology "
                            "before creating a face-attached local frame"
                        )

            if field_name == "edge_refs":
                edge_refs = action_params.get("edge_refs")
                if not isinstance(edge_refs, list) or not edge_refs:
                    continue
                if topology_index is None:
                    return (
                        "invalid_reference: no topology snapshot available; "
                        "run query_topology before using edge_refs"
                    )
                available_refs = {edge.edge_ref for edge in topology_index.edges}
                for edge_ref in edge_refs:
                    parsed = parse_topology_ref(edge_ref)
                    if parsed is None or parsed.get("kind") != "edge":
                        if isinstance(edge_ref, str) and edge_ref.strip() and not edge_ref.strip().startswith("edge:"):
                            return (
                                "invalid_reference: malformed edge_ref "
                                f"{edge_ref!r}; edge_refs must contain concrete "
                                "`edge:<step>:<entity_id>` refs from the latest query_topology, "
                                "not candidate-set labels or host-role aliases"
                            )
                        return f"invalid_reference: malformed edge_ref={edge_ref!r}"
                    if parsed["step"] != expected_step:
                        return (
                            "invalid_reference: stale edge_ref "
                            f"{edge_ref!r}; expected step {expected_step}"
                        )
                    if parsed["ref"] not in available_refs:
                        return (
                            "invalid_reference: edge_ref not found in latest topology "
                            f"{edge_ref!r}"
                        )

        return None

    def _resolve_action_params_against_history(
        self,
        action_type: CADActionType,
        action_params: dict[str, Any],
        action_history: list[ActionHistoryEntry],
    ) -> dict[str, Any]:
        resolved = normalize_action_params(action_type, action_params)
        if action_type == CADActionType.HOLE:
            resolved = self._normalize_hole_action_params(resolved)
        resolved = self._normalize_face_ref_alias_in_params(
            resolved,
            action_history,
        )
        if action_type != CADActionType.CREATE_SKETCH:
            return resolved
        path_ref = resolved.get("path_ref")
        if not isinstance(path_ref, str) or not path_ref.strip():
            return resolved
        resolved["frame_mode"] = "normal_to_path_tangent"
        latest_entry = action_history[-1] if action_history else None
        if latest_entry is None:
            return resolved
        sketch_state = self._build_sketch_state(
            history=action_history,
            snapshot=latest_entry.result_snapshot,
            step=latest_entry.step,
        )
        matched_path = next(
            (item for item in sketch_state.paths if item.path_ref == path_ref.strip()),
            None,
        )
        if matched_path is None:
            parsed_ref = self._parse_sketch_ref(path_ref)
            if parsed_ref is None or parsed_ref.get("kind") != "path":
                return resolved
            matched_path = next(
                (
                    item
                    for item in sketch_state.paths
                    if item.path_ref.endswith(str(parsed_ref["entity_id"]))
                ),
                None,
            )
        if matched_path is None:
            return resolved
        endpoint_value = resolved.get("path_endpoint", "end")
        endpoint = str(endpoint_value or "end").strip().lower()
        if endpoint in {"0", "start", "first", "begin"}:
            endpoint = "start"
        elif endpoint in {"1", "end", "last", "finish"}:
            endpoint = "end"
        resolved["path_endpoint"] = endpoint
        endpoint_point = (
            matched_path.start_point if endpoint == "start" else matched_path.end_point
        )
        tangent = (
            matched_path.start_tangent
            if endpoint == "start"
            else matched_path.terminal_tangent
        )
        target_plane = self._path_tangent_to_profile_plane(
            path_plane=matched_path.plane,
            tangent=tangent,
        )
        world_origin = self._local_point_to_world_3d(
            plane=matched_path.plane,
            origin=matched_path.origin,
            point=endpoint_point,
        )
        resolved["plane"] = target_plane
        resolved["origin"] = world_origin
        if "position" not in resolved and "center" not in resolved:
            resolved["position"] = [0.0, 0.0]
        resolved["resolved_from_path_ref"] = True
        return resolved

    def _validate_action_contract(
        self,
        action_type: CADActionType,
        action_params: dict[str, Any],
        action_history: list[ActionHistoryEntry] | None = None,
    ) -> tuple[dict[str, Any], str | None, list[str]]:
        resolved = dict(action_params)
        history = action_history or []
        if action_type == CADActionType.HOLE:
            resolved = self._normalize_hole_action_params(resolved)
            face_ref = str(resolved.get("face_ref") or "").strip()
            face_hint = str(resolved.get("_face_candidate_hint") or "").strip()
            sketch_index = self._find_preceding_sketch_index(history, len(history))
            has_face_attached_sketch = False
            if sketch_index is not None:
                sketch_entry = history[sketch_index]
                sketch_params = normalize_action_params(
                    CADActionType.CREATE_SKETCH,
                    sketch_entry.action_params,
                )
                has_face_attached_sketch = bool(
                    str(sketch_params.get("face_ref") or "").strip()
                    or str(sketch_params.get("_face_candidate_hint") or "").strip()
                )
            if not face_ref and not face_hint and not has_face_attached_sketch:
                return (
                    resolved,
                    "invalid_request: hole on an existing solid needs a face-attached local frame; "
                    "provide face_ref from query_topology or open create_sketch on the target face first.",
                    [
                        "Run query_topology and pass a fresh face_ref to hole so the center stays in the target face's local frame.",
                        "Or open create_sketch(face_ref=...) on the target face first, then use hole with 2D local centers instead of world XYZ coordinates.",
                    ],
                )
        if action_type != CADActionType.EXTRUDE:
            return resolved, None, []

        additive_mode_tokens = {
            "add",
            "additive",
            "union",
            "join",
            "boss",
            "new",
            "feature",
            "solid",
        }
        for field_name in ("mode", "operation", "combine_mode"):
            raw_value = resolved.get(field_name)
            if not isinstance(raw_value, str):
                continue
            token = raw_value.strip().lower()
            if not token:
                resolved.pop(field_name, None)
                continue
            if token in additive_mode_tokens:
                resolved.pop(field_name, None)
                continue
            return (
                resolved,
                "invalid_request: extrude is additive-only; "
                f"unsupported {field_name}={raw_value!r}. "
                "Use cut_extrude or execute_build123d for subtractive or hollow intent.",
                [
                    "Use cut_extrude for a blind/through subtractive cut on an existing solid.",
                    "For hollow or mixed nested-section intent, build the outer solid first and remove the inner profile, or switch to execute_build123d for an explicit whole-part construction.",
                ],
            )
        return resolved, None, []

    def _path_tangent_to_profile_plane(
        self,
        path_plane: str,
        tangent: list[float] | None,
    ) -> str:
        dominant_axis = "X"
        if tangent is not None and len(tangent) >= 2:
            tx = abs(float(tangent[0]))
            ty = abs(float(tangent[1]))
            if path_plane == "XY":
                dominant_axis = "X" if tx >= ty else "Y"
            elif path_plane == "XZ":
                dominant_axis = "X" if tx >= ty else "Z"
            elif path_plane == "YZ":
                dominant_axis = "Y" if tx >= ty else "Z"
        if dominant_axis == "X":
            return "YZ"
        if dominant_axis == "Y":
            return "XZ"
        return "XY"

    def _local_point_to_world_3d(
        self,
        plane: str,
        origin: list[float],
        point: list[float],
    ) -> list[float]:
        ox = self._as_float(origin[0] if len(origin) >= 1 else 0.0)
        oy = self._as_float(origin[1] if len(origin) >= 2 else 0.0)
        oz = self._as_float(origin[2] if len(origin) >= 3 else 0.0)
        px = self._as_float(point[0] if len(point) >= 1 else 0.0)
        py = self._as_float(point[1] if len(point) >= 2 else 0.0)
        if plane == "YZ":
            return [ox, oy + px, oz + py]
        if plane == "XZ":
            return [ox + px, oy, oz + py]
        return [ox + px, oy + py, oz]

    def _rebuild_model_code(
        self,
        action_history: list[ActionHistoryEntry],
        current_request: CADActionInput,
    ) -> str:
        """Rebuild entire model code by replaying all actions."""
        action_pairs = [
            (entry.action_type, entry.action_params) for entry in action_history
        ]
        action_pairs.append(
            (current_request.action_type, current_request.action_params)
        )
        code_lines = self._compose_model_replay_lines(action_pairs)
        analysis_mode = (
            "pre_solid_lightweight"
            if self._should_use_pre_solid_lightweight_analysis(
                action_history=action_history,
                current_request=current_request,
            )
            else "full"
        )
        code_lines.extend(self._geometry_analysis_code_lines(mode=analysis_mode))
        return "\n".join(code_lines)

    def _should_use_pre_solid_lightweight_analysis(
        self,
        *,
        action_history: list[ActionHistoryEntry],
        current_request: CADActionInput,
    ) -> bool:
        pre_solid_action_types = {
            CADActionType.CREATE_SKETCH,
            CADActionType.ADD_RECTANGLE,
            CADActionType.ADD_CIRCLE,
            CADActionType.ADD_POLYGON,
            CADActionType.ADD_PATH,
        }
        if current_request.action_type not in pre_solid_action_types:
            return False
        for entry in action_history:
            snapshot = entry.result_snapshot
            if snapshot.geometry.solids > 0:
                return False
            if snapshot.geometry.volume > 1e-6:
                return False
        return True

    def _build_render_view_code(
        self,
        action_history: list[ActionHistoryEntry],
        request: RenderViewInput,
        focus_bbox: BoundingBox3D | None,
    ) -> str:
        action_pairs = [
            (entry.action_type, entry.action_params) for entry in action_history
        ]
        code_lines = self._compose_model_replay_lines(action_pairs)
        code_lines.extend(self._geometry_analysis_code_lines())
        code_lines.extend(self._render_view_code_lines(request, focus_bbox))
        return "\n".join(code_lines)

    def _build_execute_probe_code(
        self,
        *,
        action_history: list[ActionHistoryEntry],
        probe_code: str,
    ) -> str:
        action_pairs = [
            (entry.action_type, entry.action_params) for entry in action_history
        ]
        code_lines = self._compose_model_replay_lines(action_pairs)
        code_lines.extend(
            [
                "try:",
                "    __aicad_probe_export_part = globals().get('__aicad_resolve_export_part', lambda: None)()",
                "except Exception:",
                "    __aicad_probe_export_part = None",
                "if __aicad_probe_export_part is not None:",
                "    export_step(__aicad_probe_export_part, 'model.step')",
                probe_code,
            ]
        )
        return "\n".join(line for line in code_lines if line is not None)

    def _compose_model_replay_lines(
        self,
        action_pairs: list[
            tuple[CADActionType, dict[str, CADParamValue]]
        ],
    ) -> list[str]:
        code_lines = self._topology_helper_code_lines()
        for action_type, action_params in action_pairs:
            action_code = self._action_to_code(action_type, action_params)
            if not action_code:
                continue
            for import_line in [
                "from build123d import *",
                "from pathlib import Path",
                "import hashlib",
                "import json",
            ]:
                action_code = action_code.replace(import_line, "")
            stripped = action_code.strip()
            if stripped:
                code_lines.append(stripped)
        return code_lines

    def _topology_helper_code_lines(self) -> list[str]:
        return BUILD123D_REPLAY_HELPER_CODE.splitlines()

    def _geometry_analysis_code_lines(self, mode: str = "full") -> list[str]:
        if mode == "pre_solid_lightweight":
            return self._pre_solid_geometry_analysis_code_lines()
        limit = GEOMETRY_OBJECT_CAPTURE_LIMIT
        return [
            "",
            "# Analyze geometry and build queryable object index",
            "geometry_info = {}",
            "def _aicad_to_float(value, default=0.0):",
            "    try:",
            "        return float(value)",
            "    except Exception:",
            "        return float(default)",
            "",
            "def _aicad_vec3(vec):",
            "    try:",
            "        return [_aicad_to_float(vec.x), _aicad_to_float(vec.y), _aicad_to_float(vec.z)]",
            "    except Exception:",
            "        try:",
            "            return [_aicad_to_float(vec.X), _aicad_to_float(vec.Y), _aicad_to_float(vec.Z)]",
            "        except Exception:",
            "            try:",
            "                return [_aicad_to_float(vec.X()), _aicad_to_float(vec.Y()), _aicad_to_float(vec.Z())]",
            "            except Exception:",
            "                return [0.0, 0.0, 0.0]",
            "",
            "def _aicad_bound_box(shape):",
            "    try:",
            "        if hasattr(shape, 'bounding_box'):",
            "            return shape.bounding_box()",
            "        return shape.BoundingBox()",
            "    except Exception:",
            "        return None",
            "",
            "def _aicad_bbox(shape):",
            "    try:",
            "        bbox = _aicad_bound_box(shape)",
            "        if bbox is None:",
            "            raise RuntimeError('bbox unavailable')",
            "        return {",
            "            'xlen': _aicad_to_float(getattr(getattr(bbox, 'size', None), 'X', getattr(bbox, 'xlen', 0.0))),",
            "            'ylen': _aicad_to_float(getattr(getattr(bbox, 'size', None), 'Y', getattr(bbox, 'ylen', 0.0))),",
            "            'zlen': _aicad_to_float(getattr(getattr(bbox, 'size', None), 'Z', getattr(bbox, 'zlen', 0.0))),",
            "            'xmin': _aicad_to_float(getattr(getattr(bbox, 'min', None), 'X', getattr(bbox, 'xmin', 0.0))),",
            "            'xmax': _aicad_to_float(getattr(getattr(bbox, 'max', None), 'X', getattr(bbox, 'xmax', 0.0))),",
            "            'ymin': _aicad_to_float(getattr(getattr(bbox, 'min', None), 'Y', getattr(bbox, 'ymin', 0.0))),",
            "            'ymax': _aicad_to_float(getattr(getattr(bbox, 'max', None), 'Y', getattr(bbox, 'ymax', 0.0))),",
            "            'zmin': _aicad_to_float(getattr(getattr(bbox, 'min', None), 'Z', getattr(bbox, 'zmin', 0.0))),",
            "            'zmax': _aicad_to_float(getattr(getattr(bbox, 'max', None), 'Z', getattr(bbox, 'zmax', 0.0))),",
            "        }",
            "    except Exception:",
            "        return {",
            "            'xlen': 0.0, 'ylen': 0.0, 'zlen': 0.0,",
            "            'xmin': 0.0, 'xmax': 0.0, 'ymin': 0.0, 'ymax': 0.0,",
            "            'zmin': 0.0, 'zmax': 0.0,",
            "        }",
            "",
            "def _aicad_geom_type(entity):",
            "    try:",
            "        geom_type = getattr(entity, 'geom_type')",
            "        geom_name = str(geom_type)",
            "        return geom_name.split('.')[-1].upper()",
            "    except Exception:",
            "        try:",
            "            return str(entity.geomType())",
            "        except Exception:",
            "            return 'unknown'",
            "",
            "def _aicad_shape_items(source, method_name, legacy_method_name):",
            "    try:",
            "        if hasattr(source, method_name):",
            "            items = getattr(source, method_name)()",
            "            if hasattr(items, 'vals'):",
            "                return list(items.vals())",
            "            return list(items)",
            "    except Exception:",
            "        pass",
            "    try:",
            "        if hasattr(source, legacy_method_name):",
            "            return list(getattr(source, legacy_method_name)())",
            "    except Exception:",
            "        pass",
            "    wrapped = getattr(source, 'wrapped', None)",
            "    if wrapped is not None:",
            "        try:",
            "            return list(getattr(wrapped, legacy_method_name)())",
            "        except Exception:",
            "            pass",
            "    return []",
            "",
            "def _aicad_shape_volume(shape):",
            "    try:",
            "        return _aicad_to_float(shape.volume)",
            "    except Exception:",
            "        try:",
            "            return _aicad_to_float(shape.Volume())",
            "        except Exception:",
            "            return 0.0",
            "",
            "def _aicad_shape_area(shape):",
            "    try:",
            "        return _aicad_to_float(shape.area)",
            "    except Exception:",
            "        try:",
            "            return _aicad_to_float(shape.Area())",
            "        except Exception:",
            "            return 0.0",
            "",
            "def _aicad_shape_center(shape):",
            "    try:",
            "        return _aicad_vec3(shape.center())",
            "    except Exception:",
            "        try:",
            "            return _aicad_vec3(shape.Center())",
            "        except Exception:",
            "            return [0.0, 0.0, 0.0]",
            "",
            "def _aicad_shape_length(shape):",
            "    try:",
            "        return _aicad_to_float(shape.length)",
            "    except Exception:",
            "        try:",
            "            return _aicad_to_float(shape.Length())",
            "        except Exception:",
            "            return 0.0",
            "",
            "def _aicad_face_normal(face):",
            "    try:",
            "        return _aicad_vec3(face.normal_at())",
            "    except Exception:",
            "        try:",
            "            return _aicad_vec3(face.normalAt())",
            "        except Exception:",
            "            return None",
            "",
            "def _aicad_entity_id(prefix, parts):",
            "    normalized_parts = []",
            "    for part in parts:",
            "        value = _aicad_to_float(part)",
            "        if abs(value) < 1e-6:",
            "            value = 0.0",
            "        normalized_parts.append(f'{value:.6f}')",
            "    normalized = '|'.join(normalized_parts)",
            "    digest = hashlib.sha1(normalized.encode('utf-8')).hexdigest()[:12]",
            "    return f'{prefix}_{digest}'",
            "",
            "def _aicad_face_entity_id(face):",
            "    face_bbox = _aicad_bbox(face)",
            "    face_center = _aicad_shape_center(face)",
            "    face_area = _aicad_shape_area(face)",
            "    face_normal = _aicad_face_normal(face)",
            "    return _aicad_entity_id('F', [",
            "        face_area,",
            "        face_center[0], face_center[1], face_center[2],",
            "        (face_normal[0] if face_normal else 0.0),",
            "        (face_normal[1] if face_normal else 0.0),",
            "        (face_normal[2] if face_normal else 0.0),",
            "        face_bbox['xlen'], face_bbox['ylen'], face_bbox['zlen'],",
            "    ])",
            "",
            "def _aicad_edge_entity_id(edge):",
            "    edge_bbox = _aicad_bbox(edge)",
            "    edge_center = None",
            "    try:",
            "        edge_center = _aicad_shape_center(edge)",
            "    except Exception:",
            "        pass",
            "    edge_length = _aicad_shape_length(edge)",
            "    return _aicad_entity_id('E', [",
            "        edge_length,",
            "        (edge_center[0] if edge_center else 0.0),",
            "        (edge_center[1] if edge_center else 0.0),",
            "        (edge_center[2] if edge_center else 0.0),",
            "        edge_bbox['xlen'], edge_bbox['ylen'], edge_bbox['zlen'],",
            "    ])",
            "",
            "def _aicad_xyz_like(value):",
            "    try:",
            "        return [_aicad_to_float(value.x), _aicad_to_float(value.y), _aicad_to_float(value.z)]",
            "    except Exception:",
            "        try:",
            "            return [_aicad_to_float(value.X()), _aicad_to_float(value.Y()), _aicad_to_float(value.Z())]",
            "        except Exception:",
            "            return None",
            "",
            "def _aicad_axis_payload(entity):",
            "    _aicad_geom = _aicad_geom_type(entity)",
            "    if _aicad_geom not in {'CIRCLE', 'CYLINDER'}:",
            "        return (None, None, None)",
            "    _aicad_adaptor = None",
            "    try:",
            "        if hasattr(entity, 'geom_adaptor'):",
            "            _aicad_adaptor = entity.geom_adaptor()",
            "        elif hasattr(entity, '_geom_adaptor'):",
            "            _aicad_adaptor = entity._geom_adaptor()",
            "        elif hasattr(entity, '_geomAdaptor'):",
            "            _aicad_adaptor = entity._geomAdaptor()",
            "    except Exception:",
            "        _aicad_adaptor = None",
            "    _aicad_raw = _aicad_adaptor",
            "    if _aicad_adaptor is not None:",
            "        try:",
            "            if _aicad_geom == 'CIRCLE' and hasattr(_aicad_adaptor, 'Circle'):",
            "                _aicad_raw = _aicad_adaptor.Circle()",
            "            elif _aicad_geom == 'CYLINDER' and hasattr(_aicad_adaptor, 'Cylinder'):",
            "                _aicad_raw = _aicad_adaptor.Cylinder()",
            "        except Exception:",
            "            _aicad_raw = _aicad_adaptor",
            "    if _aicad_raw is None:",
            "        return (None, None, None)",
            "    _aicad_radius = None",
            "    try:",
            "        _aicad_radius = _aicad_to_float(_aicad_raw.Radius())",
            "    except Exception:",
            "        try:",
            "            _aicad_radius = _aicad_to_float(entity.radius)",
            "        except Exception:",
            "            _aicad_radius = None",
            "    _aicad_axis = None",
            "    try:",
            "        _aicad_axis = _aicad_raw.Axis()",
            "    except Exception:",
            "        try:",
            "            _aicad_position = _aicad_raw.Position()",
            "            if hasattr(_aicad_position, 'Axis'):",
            "                _aicad_axis = _aicad_position.Axis()",
            "            else:",
            "                _aicad_axis = _aicad_position",
            "        except Exception:",
            "            _aicad_axis = None",
            "    if _aicad_axis is None:",
            "        return (None, None, _aicad_radius)",
            "    _aicad_origin = None",
            "    _aicad_direction = None",
            "    try:",
            "        _aicad_origin = _aicad_xyz_like(_aicad_axis.Location())",
            "    except Exception:",
            "        _aicad_origin = None",
            "    try:",
            "        _aicad_direction = _aicad_xyz_like(_aicad_axis.Direction())",
            "    except Exception:",
            "        _aicad_direction = None",
            "    return (_aicad_origin, _aicad_direction, _aicad_radius)",
            "",
            f"geometry_info['entities_limit'] = {limit}",
            "shape = result.val() if hasattr(result, 'val') else result",
            "",
            "solids = _aicad_shape_items(result, 'solids', 'Solids')",
            "faces = _aicad_shape_items(result, 'faces', 'Faces')",
            "edges = _aicad_shape_items(result, 'edges', 'Edges')",
            "pending_wires = []",
            "if not solids:",
            "    solids = _aicad_shape_items(shape, 'solids', 'Solids')",
            "if not faces:",
            "    faces = _aicad_shape_items(shape, 'faces', 'Faces')",
            "if not edges:",
            "    edges = _aicad_shape_items(shape, 'edges', 'Edges')",
            "if not edges:",
            "    try:",
            "        pending_wires = list(getattr(getattr(result, 'ctx', None), 'pendingWires', []) or [])",
            "    except Exception:",
                "        pending_wires = []",
            "    if pending_wires:",
            "        for _aicad_wire in pending_wires:",
            "            try:",
            "                edges.extend(_aicad_shape_items(_aicad_wire, 'edges', 'Edges'))",
            "            except Exception:",
            "                continue",
            "",
            "geometry_info['solids'] = len(solids)",
            "geometry_info['faces'] = len(faces)",
            "geometry_info['edges'] = len(edges)",
            "geometry_info['pending_wires'] = len(pending_wires)",
            "",
            "geometry_info['volume'] = _aicad_shape_volume(shape)",
            "geometry_info['surface_area'] = _aicad_shape_area(shape)",
            "",
            "shape_bbox = _aicad_bbox(shape)",
            "geometry_info['bbox'] = [shape_bbox['xlen'], shape_bbox['ylen'], shape_bbox['zlen']]",
            "geometry_info['bbox_min'] = [shape_bbox['xmin'], shape_bbox['ymin'], shape_bbox['zmin']]",
            "geometry_info['bbox_max'] = [shape_bbox['xmax'], shape_bbox['ymax'], shape_bbox['zmax']]",
            "geometry_info['center'] = _aicad_shape_center(shape)",
            "if geometry_info['center'] == [0.0, 0.0, 0.0] and any(shape_bbox.values()):",
            "    geometry_info['center'] = [",
            "        (shape_bbox['xmin'] + shape_bbox['xmax']) / 2.0,",
            "        (shape_bbox['ymin'] + shape_bbox['ymax']) / 2.0,",
            "        (shape_bbox['zmin'] + shape_bbox['zmax']) / 2.0,",
            "    ]",
            "",
            "solids_detail = []",
            "for idx, solid in enumerate(solids[:geometry_info['entities_limit']], start=1):",
            "    solid_bbox = _aicad_bbox(solid)",
            "    solid_center = _aicad_shape_center(solid)",
            "    solid_volume = _aicad_shape_volume(solid)",
            "    solid_area = _aicad_shape_area(solid)",
            "    solid_id = _aicad_entity_id(",
            "        'S',",
            "        [",
            "            solid_volume, solid_area,",
            "            solid_center[0], solid_center[1], solid_center[2],",
            "            solid_bbox['xlen'], solid_bbox['ylen'], solid_bbox['zlen'],",
            "        ],",
            "    )",
            "    solids_detail.append({",
            "        'solid_id': solid_id,",
            "        'volume': solid_volume,",
            "        'surface_area': solid_area,",
            "        'center_of_mass': solid_center,",
            "        'bbox': solid_bbox,",
            "    })",
            "",
            "faces_detail = []",
            "for idx, face in enumerate(faces[:geometry_info['entities_limit']], start=1):",
            "    face_bbox = _aicad_bbox(face)",
            "    face_center = _aicad_shape_center(face)",
            "    face_area = _aicad_shape_area(face)",
            "    face_normal = _aicad_face_normal(face)",
            "    face_axis_origin, face_axis_direction, face_radius = _aicad_axis_payload(face)",
            "    face_id = _aicad_entity_id(",
            "        'F',",
            "        [",
            "            face_area,",
            "            face_center[0], face_center[1], face_center[2],",
            "            (face_normal[0] if face_normal else 0.0),",
            "            (face_normal[1] if face_normal else 0.0),",
            "            (face_normal[2] if face_normal else 0.0),",
            "            face_bbox['xlen'], face_bbox['ylen'], face_bbox['zlen'],",
            "        ],",
            "    )",
            "    faces_detail.append({",
            "        'face_id': face_id,",
            "        'area': face_area,",
            "        'center': face_center,",
            "        'normal': face_normal,",
            "        'axis_origin': face_axis_origin,",
            "        'axis_direction': face_axis_direction,",
            "        'radius': face_radius,",
            "        'geom_type': _aicad_geom_type(face),",
            "        'bbox': face_bbox,",
            "    })",
            "",
            "edges_detail = []",
            "for idx, edge in enumerate(edges[:geometry_info['entities_limit']], start=1):",
            "    edge_bbox = _aicad_bbox(edge)",
            "    edge_center = None",
            "    try:",
            "        edge_center = _aicad_shape_center(edge)",
            "    except Exception:",
            "        pass",
            "    edge_length = _aicad_shape_length(edge)",
            "    edge_axis_origin, edge_axis_direction, edge_radius = _aicad_axis_payload(edge)",
            "    edge_id = _aicad_entity_id(",
            "        'E',",
            "        [",
            "            edge_length,",
            "            (edge_center[0] if edge_center else 0.0),",
            "            (edge_center[1] if edge_center else 0.0),",
            "            (edge_center[2] if edge_center else 0.0),",
            "            edge_bbox['xlen'], edge_bbox['ylen'], edge_bbox['zlen'],",
            "        ],",
            "    )",
            "    edges_detail.append({",
            "        'edge_id': edge_id,",
            "        'length': edge_length,",
            "        'geom_type': _aicad_geom_type(edge),",
            "        'center': edge_center,",
            "        'axis_origin': edge_axis_origin,",
            "        'axis_direction': edge_axis_direction,",
            "        'radius': edge_radius,",
            "        'bbox': edge_bbox,",
            "    })",
            "",
            "solid_face_map = {}",
            "solid_edge_map = {}",
            "for solid in solids[:geometry_info['entities_limit']]:",
            "    try:",
            "        solid_id = _aicad_solid_entity_id(solid)",
            "    except Exception:",
            "        solid_id = None",
            "    if solid_id is None:",
            "        continue",
            "    try:",
            "        for solid_face in _aicad_shape_items(solid, 'faces', 'Faces'):",
            "            try:",
            "                solid_face_map[_aicad_face_entity_id(solid_face)] = solid_id",
            "            except Exception:",
            "                continue",
            "    except Exception:",
            "        pass",
            "    try:",
            "        for solid_edge in _aicad_shape_items(solid, 'edges', 'Edges'):",
            "            try:",
            "                solid_edge_map[_aicad_edge_entity_id(solid_edge)] = solid_id",
            "            except Exception:",
            "                continue",
            "    except Exception:",
            "        pass",
            "",
            "edge_face_map = {}",
            "topology_faces_detail = []",
            "for face, face_detail in zip(faces[:geometry_info['entities_limit']], faces_detail):",
            "    face_edge_ids = []",
            "    try:",
            "        face_edges = _aicad_shape_items(face, 'edges', 'Edges')",
            "    except Exception:",
            "        face_edges = []",
            "    for face_edge in face_edges:",
            "        try:",
            "            face_edge_id = _aicad_edge_entity_id(face_edge)",
            "        except Exception:",
            "            continue",
            "        face_edge_ids.append(face_edge_id)",
            "        edge_face_map.setdefault(face_edge_id, []).append(face_detail['face_id'])",
            "    topology_faces_detail.append({",
            "        'face_id': face_detail['face_id'],",
            "        'area': face_detail['area'],",
            "        'center': face_detail['center'],",
            "        'normal': face_detail['normal'],",
            "        'axis_origin': face_detail.get('axis_origin'),",
            "        'axis_direction': face_detail.get('axis_direction'),",
            "        'radius': face_detail.get('radius'),",
            "        'geom_type': face_detail['geom_type'],",
            "        'bbox': face_detail['bbox'],",
            "        'parent_solid_id': solid_face_map.get(face_detail['face_id']),",
            "        'edge_ids': sorted(set(face_edge_ids)),",
            "        'adjacent_face_ids': [],",
            "    })",
            "",
            "for face_item in topology_faces_detail:",
            "    adjacent_face_ids = set()",
            "    for edge_id in face_item['edge_ids']:",
            "        for adjacent_face_id in edge_face_map.get(edge_id, []):",
            "            if adjacent_face_id != face_item['face_id']:",
            "                adjacent_face_ids.add(adjacent_face_id)",
            "    face_item['adjacent_face_ids'] = sorted(adjacent_face_ids)",
            "",
            "edge_detail_map = {item['edge_id']: item for item in edges_detail}",
            "topology_edges_detail = []",
            "for edge in edges[:geometry_info['entities_limit']]:",
            "    try:",
            "        edge_id = _aicad_edge_entity_id(edge)",
            "    except Exception:",
            "        continue",
            "    edge_detail = edge_detail_map.get(edge_id, {})",
            "    topology_edges_detail.append({",
            "        'edge_id': edge_id,",
            "        'length': edge_detail.get('length', 0.0),",
            "        'geom_type': edge_detail.get('geom_type', 'unknown'),",
            "        'center': edge_detail.get('center'),",
            "        'axis_origin': edge_detail.get('axis_origin'),",
            "        'axis_direction': edge_detail.get('axis_direction'),",
            "        'radius': edge_detail.get('radius'),",
            "        'bbox': edge_detail.get('bbox', {",
            "            'xlen': 0.0, 'ylen': 0.0, 'zlen': 0.0,",
            "            'xmin': 0.0, 'xmax': 0.0, 'ymin': 0.0, 'ymax': 0.0,",
            "            'zmin': 0.0, 'zmax': 0.0,",
            "        }),",
            "        'parent_solid_id': solid_edge_map.get(edge_id),",
            "        'adjacent_face_ids': sorted(set(edge_face_map.get(edge_id, []))),",
            "    })",
            "",
            "geometry_info['solids_detail'] = solids_detail",
            "geometry_info['faces_detail'] = faces_detail",
            "geometry_info['edges_detail'] = edges_detail",
            "geometry_info['topology_faces_detail'] = topology_faces_detail",
            "geometry_info['topology_edges_detail'] = topology_edges_detail",
            "geometry_info['solids_truncated'] = len(solids) > len(solids_detail)",
            "geometry_info['faces_truncated'] = len(faces) > len(faces_detail)",
            "geometry_info['edges_truncated'] = len(edges) > len(edges_detail)",
            "",
            "geometry_path = Path('/output/geometry_info.json')",
            "with open(geometry_path, 'w', encoding='utf-8') as f:",
            "    json.dump(geometry_info, f, ensure_ascii=True)",
        ]

    def _pre_solid_geometry_analysis_code_lines(self) -> list[str]:
        limit = GEOMETRY_OBJECT_CAPTURE_LIMIT
        return [
            "",
            "# Lightweight geometry analysis for sketch-only / pre-solid replay",
            "geometry_info = {}",
            "def _aicad_to_float(value, default=0.0):",
            "    try:",
            "        return float(value)",
            "    except Exception:",
            "        return float(default)",
            "",
            "def _aicad_vec3(vec):",
            "    try:",
            "        return [_aicad_to_float(vec.x), _aicad_to_float(vec.y), _aicad_to_float(vec.z)]",
            "    except Exception:",
            "        return [0.0, 0.0, 0.0]",
            "",
            "def _aicad_bbox(shape):",
            "    try:",
            "        bbox = shape.BoundingBox()",
            "        return {",
            "            'xlen': _aicad_to_float(bbox.xlen),",
            "            'ylen': _aicad_to_float(bbox.ylen),",
            "            'zlen': _aicad_to_float(bbox.zlen),",
            "            'xmin': _aicad_to_float(bbox.xmin),",
            "            'xmax': _aicad_to_float(bbox.xmax),",
            "            'ymin': _aicad_to_float(bbox.ymin),",
            "            'ymax': _aicad_to_float(bbox.ymax),",
            "            'zmin': _aicad_to_float(bbox.zmin),",
            "            'zmax': _aicad_to_float(bbox.zmax),",
            "        }",
            "    except Exception:",
            "        return {",
            "            'xlen': 0.0, 'ylen': 0.0, 'zlen': 0.0,",
            "            'xmin': 0.0, 'xmax': 0.0, 'ymin': 0.0, 'ymax': 0.0,",
            "            'zmin': 0.0, 'zmax': 0.0,",
            "        }",
            "",
            "def _aicad_geom_type(entity):",
            "    try:",
            "        return str(entity.geomType())",
            "    except Exception:",
            "        return 'unknown'",
            "",
            "def _aicad_entity_id(prefix, parts):",
            "    normalized_parts = []",
            "    for part in parts:",
            "        value = _aicad_to_float(part)",
            "        if abs(value) < 1e-6:",
            "            value = 0.0",
            "        normalized_parts.append(f'{value:.6f}')",
            "    normalized = '|'.join(normalized_parts)",
            "    digest = hashlib.sha1(normalized.encode('utf-8')).hexdigest()[:12]",
            "    return f'{prefix}_{digest}'",
            "",
            "def _aicad_merge_bbox(acc, item):",
            "    if item is None:",
            "        return acc",
            "    bbox = _aicad_bbox(item)",
            "    if acc is None:",
            "        return dict(bbox)",
            "    return {",
            "        'xlen': 0.0,",
            "        'ylen': 0.0,",
            "        'zlen': 0.0,",
            "        'xmin': min(float(acc.get('xmin', 0.0)), float(bbox.get('xmin', 0.0))),",
            "        'xmax': max(float(acc.get('xmax', 0.0)), float(bbox.get('xmax', 0.0))),",
            "        'ymin': min(float(acc.get('ymin', 0.0)), float(bbox.get('ymin', 0.0))),",
            "        'ymax': max(float(acc.get('ymax', 0.0)), float(bbox.get('ymax', 0.0))),",
            "        'zmin': min(float(acc.get('zmin', 0.0)), float(bbox.get('zmin', 0.0))),",
            "        'zmax': max(float(acc.get('zmax', 0.0)), float(bbox.get('zmax', 0.0))),",
            "    }",
            "",
            f"geometry_info['entities_limit'] = {limit}",
            "geometry_info['analysis_mode'] = 'pre_solid_lightweight'",
            "geometry_info['pre_solid_only'] = True",
            "shape = result.val() if hasattr(result, 'val') else result",
            "solids = []",
            "faces = []",
            "edges = []",
            "pending_wires = []",
            "pending_edges = []",
            "try:",
            "    solids = list(result.solids().vals())",
            "except Exception:",
            "    solids = []",
            "try:",
            "    pending_wires = list(getattr(getattr(result, 'ctx', None), 'pendingWires', []) or [])",
            "except Exception:",
            "    pending_wires = []",
            "try:",
            "    pending_edges = list(getattr(getattr(result, 'ctx', None), 'pendingEdges', []) or [])",
            "except Exception:",
            "    pending_edges = []",
            "for _aicad_wire in pending_wires:",
            "    try:",
            "        edges.extend(list(_aicad_wire.Edges()))",
            "    except Exception:",
            "        continue",
            "if not edges:",
            "    edges = list(pending_edges)",
            "if not edges:",
            "    try:",
            "        edges = list(result.edges().vals())",
            "    except Exception:",
            "        edges = []",
            "bbox_acc = None",
            "bbox_acc = _aicad_merge_bbox(bbox_acc, shape)",
            "for _aicad_item in pending_wires[:geometry_info['entities_limit']]:",
            "    bbox_acc = _aicad_merge_bbox(bbox_acc, _aicad_item)",
            "for _aicad_item in edges[:geometry_info['entities_limit']]:",
            "    bbox_acc = _aicad_merge_bbox(bbox_acc, _aicad_item)",
            "if bbox_acc is None:",
            "    bbox_acc = {",
            "        'xlen': 0.0, 'ylen': 0.0, 'zlen': 0.0,",
            "        'xmin': 0.0, 'xmax': 0.0, 'ymin': 0.0, 'ymax': 0.0,",
            "        'zmin': 0.0, 'zmax': 0.0,",
            "    }",
            "bbox_acc['xlen'] = max(0.0, float(bbox_acc.get('xmax', 0.0)) - float(bbox_acc.get('xmin', 0.0)))",
            "bbox_acc['ylen'] = max(0.0, float(bbox_acc.get('ymax', 0.0)) - float(bbox_acc.get('ymin', 0.0)))",
            "bbox_acc['zlen'] = max(0.0, float(bbox_acc.get('zmax', 0.0)) - float(bbox_acc.get('zmin', 0.0)))",
            "geometry_info['solids'] = len(solids)",
            "geometry_info['faces'] = len(faces)",
            "geometry_info['edges'] = len(edges)",
            "geometry_info['pending_wires'] = len(pending_wires)",
            "geometry_info['volume'] = 0.0",
            "geometry_info['surface_area'] = 0.0",
            "geometry_info['bbox'] = [bbox_acc['xlen'], bbox_acc['ylen'], bbox_acc['zlen']]",
            "geometry_info['bbox_min'] = [bbox_acc['xmin'], bbox_acc['ymin'], bbox_acc['zmin']]",
            "geometry_info['bbox_max'] = [bbox_acc['xmax'], bbox_acc['ymax'], bbox_acc['zmax']]",
            "geometry_info['center'] = [",
            "    (bbox_acc['xmin'] + bbox_acc['xmax']) / 2.0,",
            "    (bbox_acc['ymin'] + bbox_acc['ymax']) / 2.0,",
            "    (bbox_acc['zmin'] + bbox_acc['zmax']) / 2.0,",
            "]",
            "edges_detail = []",
            "for edge in edges[:geometry_info['entities_limit']]:",
            "    edge_bbox = _aicad_bbox(edge)",
            "    edge_center = None",
            "    try:",
            "        edge_center = _aicad_vec3(edge.Center())",
            "    except Exception:",
            "        edge_center = None",
            "    try:",
            "        edge_length = _aicad_to_float(edge.Length())",
            "    except Exception:",
            "        edge_length = 0.0",
            "    edge_id = _aicad_entity_id('E', [",
            "        edge_length,",
            "        (edge_center[0] if edge_center else 0.0),",
            "        (edge_center[1] if edge_center else 0.0),",
            "        (edge_center[2] if edge_center else 0.0),",
            "        edge_bbox['xlen'], edge_bbox['ylen'], edge_bbox['zlen'],",
            "    ])",
            "    edges_detail.append({",
            "        'edge_id': edge_id,",
            "        'length': edge_length,",
            "        'geom_type': _aicad_geom_type(edge),",
            "        'center': edge_center,",
            "        'bbox': edge_bbox,",
            "    })",
            "geometry_info['solids_detail'] = []",
            "geometry_info['faces_detail'] = []",
            "geometry_info['edges_detail'] = edges_detail",
            "geometry_info['topology_faces_detail'] = []",
            "geometry_info['topology_edges_detail'] = []",
            "geometry_info['solids_truncated'] = False",
            "geometry_info['faces_truncated'] = False",
            "geometry_info['edges_truncated'] = len(edges) > len(edges_detail)",
            "geometry_path = Path('/output/geometry_info.json')",
            "with open(geometry_path, 'w', encoding='utf-8') as f:",
            "    json.dump(geometry_info, f, ensure_ascii=True)",
        ]

    def _render_view_code_lines(
        self,
        request: RenderViewInput,
        focus_bbox: BoundingBox3D | None,
    ) -> list[str]:
        width_inches = request.width_px / 100.0
        height_inches = request.height_px / 100.0
        focus_dict = self._bbox_to_dict(focus_bbox) if focus_bbox is not None else None
        return [
            "",
            "# Render custom camera view",
            "import matplotlib",
            "matplotlib.use('Agg')",
            "from matplotlib import pyplot as plt",
            "try:",
            "    render_shape = result.val() if hasattr(result, 'val') else result",
            "    if not hasattr(render_shape, 'tessellate'):",
            "        try:",
            "            rv_solids = list(result.solids().vals())",
            "            if rv_solids:",
            "                render_shape = rv_solids[0]",
            "        except Exception:",
            "            pass",
            "    if not hasattr(render_shape, 'tessellate'):",
            "        try:",
            "            rv_faces = list(result.faces().vals())",
            "            if rv_faces:",
            "                render_shape = rv_faces[0]",
            "        except Exception:",
            "            pass",
            "",
            "    def _rv_xyz(point):",
            "        try:",
            "            return (float(point.x), float(point.y), float(point.z))",
            "        except Exception:",
            "            try:",
            "                return (float(point.X), float(point.Y), float(point.Z))",
            "            except Exception:",
            "                try:",
            "                    return (float(point.X()), float(point.Y()), float(point.Z()))",
            "                except Exception:",
            "                    try:",
            "                        return (float(point[0]), float(point[1]), float(point[2]))",
            "                    except Exception:",
            "                        return None",
            "",
            "    rv_points = []",
            "    rv_triangles = []",
            "    rv_lines = []",
            "",
            "    if hasattr(render_shape, 'tessellate'):",
            "        try:",
            "            rv_vertices_raw, rv_triangles_raw = render_shape.tessellate(0.2, 0.2)",
            "        except TypeError:",
            "            rv_vertices_raw, rv_triangles_raw = render_shape.tessellate(0.2)",
            "        except Exception:",
            "            rv_vertices_raw, rv_triangles_raw = [], []",
            "        for rv_v in rv_vertices_raw:",
            "            rv_xyz = _rv_xyz(rv_v)",
            "            if rv_xyz is not None:",
            "                rv_points.append(rv_xyz)",
            "        for tri in rv_triangles_raw:",
            "            try:",
            "                a, b, c = tri",
            "                rv_triangles.append((int(a), int(b), int(c)))",
            "            except Exception:",
            "                continue",
            "",
            "    if not rv_triangles:",
            "        rv_edges = []",
            "        try:",
            "            rv_edges = list(result.edges().vals())",
            "        except Exception:",
            "            pass",
            "        if not rv_edges:",
            "            try:",
            "                rv_wires = list(result.wires().vals())",
            "                for rv_wire in rv_wires:",
            "                    try:",
            "                        rv_edges.extend(list(rv_wire.Edges()))",
            "                    except Exception:",
            "                        continue",
            "            except Exception:",
            "                pass",
            "        if not rv_edges:",
            "            try:",
            "                rv_pending_wires = list(getattr(getattr(result, 'ctx', None), 'pendingWires', []) or [])",
            "                for rv_wire in rv_pending_wires:",
            "                    try:",
            "                        rv_edges.extend(list(rv_wire.Edges()))",
            "                    except Exception:",
            "                        continue",
            "            except Exception:",
            "                pass",
            "        if not rv_edges and hasattr(render_shape, 'Edges'):",
            "            try:",
            "                rv_edges = list(render_shape.Edges())",
            "            except Exception:",
            "                rv_edges = []",
            "",
            "        for rv_edge in rv_edges:",
            "            rv_line = []",
            "            try:",
            "                rv_samples = rv_edge.discretize(48)",
            "            except Exception:",
            "                rv_samples = []",
            "            if not rv_samples:",
            "                try:",
            "                    rv_samples = [rv_edge.startPoint(), rv_edge.endPoint()]",
            "                except Exception:",
            "                    rv_samples = []",
            "            for rv_sample in rv_samples:",
            "                rv_xyz = _rv_xyz(rv_sample)",
            "                if rv_xyz is None:",
            "                    continue",
            "                rv_line.append(rv_xyz)",
            "                rv_points.append(rv_xyz)",
            "            if len(rv_line) >= 2:",
            "                rv_lines.append(rv_line)",
            "",
            "    if not rv_points:",
            "        raise RuntimeError('no renderable geometry for custom render')",
            "",
            "    rv_x = [p[0] for p in rv_points]",
            "    rv_y = [p[1] for p in rv_points]",
            "    rv_z = [p[2] for p in rv_points]",
            "    rv_xmin, rv_xmax = min(rv_x), max(rv_x)",
            "    rv_ymin, rv_ymax = min(rv_y), max(rv_y)",
            "    rv_zmin, rv_zmax = min(rv_z), max(rv_z)",
            "    rv_xmid = (rv_xmin + rv_xmax) / 2.0",
            "    rv_ymid = (rv_ymin + rv_ymax) / 2.0",
            "    rv_zmid = (rv_zmin + rv_zmax) / 2.0",
            "    rv_max_span = max(rv_xmax - rv_xmin, rv_ymax - rv_ymin, rv_zmax - rv_zmin)",
            "    if rv_max_span <= 0.0:",
            "        rv_max_span = 1.0",
            f"    rv_focus_bbox = {focus_dict!r}",
            "    if isinstance(rv_focus_bbox, dict):",
            "        rv_xmid = (float(rv_focus_bbox['xmin']) + float(rv_focus_bbox['xmax'])) / 2.0",
            "        rv_ymid = (float(rv_focus_bbox['ymin']) + float(rv_focus_bbox['ymax'])) / 2.0",
            "        rv_zmid = (float(rv_focus_bbox['zmin']) + float(rv_focus_bbox['zmax'])) / 2.0",
            "        rv_max_span = max(",
            "            float(rv_focus_bbox['xlen']),",
            "            float(rv_focus_bbox['ylen']),",
            "            float(rv_focus_bbox['zlen']),",
            "        )",
            "        if rv_max_span <= 0.0:",
            "            rv_max_span = 1.0",
            f"        rv_half_span = (rv_max_span * (0.5 + float({request.focus_padding_ratio}))) / max(0.1, float({request.zoom}))",
            "    else:",
            f"        rv_half_span = (rv_max_span * 0.55) / max(0.1, float({request.zoom}))",
            f"    rv_fig = plt.figure(figsize=({width_inches:.4f}, {height_inches:.4f}), dpi=100)",
            "    rv_axis = rv_fig.add_subplot(111, projection='3d')",
            "    rv_axis.set_box_aspect((1.0, 1.0, 1.0))",
            f"    rv_style = {request.style.value!r}",
            "    if rv_triangles and rv_style == 'wireframe':",
            "        rv_axis.plot_trisurf(",
            "            rv_x, rv_y, rv_z, triangles=rv_triangles,",
            "            linewidth=0.30, antialiased=True,",
            "            color='#ffffff', edgecolor='#5c6778', shade=False, alpha=0.95",
            "        )",
            "    elif rv_triangles:",
            "        rv_axis.plot_trisurf(",
            "            rv_x, rv_y, rv_z, triangles=rv_triangles,",
            "            linewidth=0.14, antialiased=True,",
            "            color='#e6ebf2', edgecolor='#6b7586', shade=True, alpha=1.0",
            "        )",
            "    else:",
            "        rv_line_color = '#5a6577' if rv_style == 'wireframe' else '#3f4a5a'",
            "        for rv_line in rv_lines:",
            "            rv_lx = [p[0] for p in rv_line]",
            "            rv_ly = [p[1] for p in rv_line]",
            "            rv_lz = [p[2] for p in rv_line]",
            "            rv_axis.plot(rv_lx, rv_ly, rv_lz, color=rv_line_color, linewidth=2.0, alpha=0.98)",
            "        if rv_points:",
            "            rv_axis.scatter(rv_x, rv_y, rv_z, s=4, c='#334155', alpha=0.18, depthshade=False)",
            "    rv_axis.set_xlim(rv_xmid - rv_half_span, rv_xmid + rv_half_span)",
            "    rv_axis.set_ylim(rv_ymid - rv_half_span, rv_ymid + rv_half_span)",
            "    rv_axis.set_zlim(rv_zmid - rv_half_span, rv_zmid + rv_half_span)",
            f"    rv_axis.view_init(elev={request.elevation_deg}, azim={request.azimuth_deg})",
            "    rv_axis.set_axis_off()",
            "    rv_axis.set_facecolor('#ffffff')",
            "    rv_fig.patch.set_facecolor('#ffffff')",
            "    rv_fig.tight_layout(pad=0.02)",
            f"    rv_output_path = Path('/output/{DEFAULT_RENDER_VIEW_FILENAME}')",
            "    rv_fig.savefig(",
            "        rv_output_path,",
            "        dpi=100,",
            "        bbox_inches='tight',",
            "        pad_inches=0.02,",
            "        facecolor='#ffffff',",
            "        edgecolor='none',",
            "    )",
            "    plt.close(rv_fig)",
            "except Exception as render_exc:",
            "    print(f'RENDER_VIEW_WARNING: {render_exc}')",
        ]

    def _build_camera_payload(
        self, request: RenderViewInput
    ) -> dict[str, int | float | str | bool]:
        return {
            "azimuth_deg": request.azimuth_deg,
            "elevation_deg": request.elevation_deg,
            "zoom": request.zoom,
            "width_px": request.width_px,
            "height_px": request.height_px,
            "style": request.style.value,
            "focus_padding_ratio": request.focus_padding_ratio,
            "focus_span": self._as_float(request.focus_span, fallback=0.0),
            "target_entity_count": len(request.target_entity_ids),
            "has_target_entity_ids": bool(request.target_entity_ids),
            "has_focus_center": bool(request.focus_center),
            "has_step_override": request.step is not None,
            "render_source": "custom_render",
            "render_fallback_used": False,
            "fallback_view_file": "",
        }

    def _select_render_fallback_file(
        self,
        filenames: list[str],
        request: RenderViewInput,
    ) -> str | None:
        png_files = [name for name in filenames if name.lower().endswith(".png")]
        if not png_files:
            return None

        by_lower = {name.lower(): name for name in png_files}

        if abs(request.elevation_deg) >= 65.0 and "preview_top.png" in by_lower:
            return by_lower["preview_top.png"]

        normalized_azimuth = request.azimuth_deg % 360.0
        if normalized_azimuth < 0:
            normalized_azimuth += 360.0

        if (
            normalized_azimuth <= 45.0
            or normalized_azimuth >= 315.0
            or 135.0 <= normalized_azimuth <= 225.0
        ) and "preview_front.png" in by_lower:
            return by_lower["preview_front.png"]

        if (
            45.0 < normalized_azimuth < 135.0
            or 225.0 < normalized_azimuth < 315.0
        ) and "preview_right.png" in by_lower:
            return by_lower["preview_right.png"]

        for preferred in (
            "preview_iso.png",
            "preview_front.png",
            "preview_right.png",
            "preview_top.png",
        ):
            if preferred in by_lower:
                return by_lower[preferred]

        return png_files[0]

    def _extract_render_warning(self, stdout: str, stderr: str) -> str:
        for stream in (stdout, stderr):
            if not stream:
                continue
            for line in stream.splitlines():
                marker = "RENDER_VIEW_WARNING:"
                if marker in line:
                    warning = line.split(marker, 1)[1].strip()
                    return warning or line.strip()
        return ""

    def _build_render_missing_error_message(
        self,
        filenames: list[str],
        stdout: str,
        stderr: str,
    ) -> str:
        warning = self._extract_render_warning(stdout=stdout, stderr=stderr)
        png_candidates = [name for name in filenames if name.lower().endswith(".png")]

        if warning and png_candidates:
            candidate_preview = ",".join(png_candidates[:4])
            return (
                "render_view output not found"
                f"; warning={warning}; png_candidates={candidate_preview}"
            )
        if warning:
            return f"render_view output not found; warning={warning}"
        if png_candidates:
            candidate_preview = ",".join(png_candidates[:4])
            return f"render_view output not found; png_candidates={candidate_preview}"
        return "render_view output not found"

    def _build_artifacts(
        self,
        filenames: list[str],
        output_file_contents: dict[str, bytes],
        include_artifact_content: bool,
    ) -> list[SandboxArtifact]:
        artifacts: list[SandboxArtifact] = []
        for filename in filenames:
            content = output_file_contents.get(filename)
            artifacts.append(
                SandboxArtifact(
                    filename=filename,
                    uri=f"sandbox://artifacts/{filename}",
                    mime_type=self._resolve_mime_type(filename),
                    size_bytes=len(content) if content is not None else 0,
                    content_base64=(
                        base64.b64encode(content).decode("ascii")
                        if content is not None and include_artifact_content
                        else None
                    ),
                )
            )
        return artifacts

    def _action_to_code(
        self,
        action_type: CADActionType,
        params: dict[str, CADParamValue],
    ) -> str:
        """Convert CAD action to Build123d-compatible Python code."""
        definition = get_action_definition(action_type)
        if definition is None:
            return ""
        params = normalize_action_params(action_type, params)
        code_lines = []

        if action_type == CADActionType.CREATE_SKETCH:
            plane_raw = params.get("plane", "XY")
            plane_token = (
                str(plane_raw).strip().upper()
                if isinstance(plane_raw, str)
                else "XY"
            )
            plane_alias_map = {
                "XY": "XY",
                "TOP": "XY",
                "BOTTOM": "XY",
                "XZ": "XZ",
                "FRONT": "XZ",
                "BACK": "XZ",
                "YZ": "YZ",
                "RIGHT": "YZ",
                "LEFT": "YZ",
            }
            plane = plane_alias_map.get(plane_token, "XY")
            sketch_origin_3d = self._resolve_create_sketch_origin_3d(
                params=params,
                plane=plane,
            )
            attach_face_selector: str | None = {
                "TOP": ">Z",
                "BOTTOM": "<Z",
                "FRONT": ">Y",
                "BACK": "<Y",
                "RIGHT": ">X",
                "LEFT": "<X",
            }.get(plane_token)
            if attach_face_selector is None and bool(params.get("attach_to_solid", False)):
                attach_face_selector = {
                    "XY": ">Z",
                    "XZ": ">Y",
                    "YZ": ">X",
                }.get(plane)
            sketch_position = params.get("position", params.get("center"))
            position_u, position_v = self._normalize_workplane_center(
                value=sketch_position,
                plane=plane,
            )
            use_world_origin = bool(
                params.get("resolved_from_path_ref", False)
                or "origin" in params
                or isinstance(params.get("offset"), (int, float))
            )
            face_ref = params.get("face_ref")
            face_candidate_hint = params.get("_face_candidate_hint")
            parsed_face_ref = parse_topology_ref(face_ref)
            face_candidate_hint = params.get("_face_candidate_hint")
            if parsed_face_ref is not None and parsed_face_ref.get("kind") == "face":
                code_lines = [
                    "_aicad_capture_pending_loft_profile()",
                    "_aicad_capture_pending_sweep_path()",
                    f"_aicad_plane = {plane!r}",
                    f"_aicad_sketch_origin_3d = {sketch_origin_3d!r}",
                    f"_aicad_sketch_face_id = {parsed_face_ref['entity_id']!r}",
                    f"_aicad_sketch_face_hint = {face_candidate_hint!r}",
                    "_aicad_sketch_from_face_ref = True",
                    "_aicad_stepped_profile = None",
                    (
                        "_aicad_sketch = _aicad_create_sketch_from_face("
                        "result, _aicad_sketch_face_id, _aicad_sketch_face_hint, "
                        f"_aicad_plane, _aicad_sketch_origin_3d, [{position_u}, {position_v}]"
                        ")"
                    ),
                ]
            else:
                code_lines = [
                    "_aicad_capture_pending_loft_profile()",
                    "_aicad_capture_pending_sweep_path()",
                    f"_aicad_plane = {plane!r}",
                    f"_aicad_sketch_origin_3d = {sketch_origin_3d!r}",
                    "_aicad_sketch_face_id = None",
                    f"_aicad_sketch_face_hint = {face_candidate_hint!r}",
                    "_aicad_sketch_from_face_ref = False",
                    "_aicad_stepped_profile = None",
                    (
                        "_aicad_sketch = _aicad_create_sketch("
                        "result, "
                        f"{plane!r}, "
                        f"{sketch_origin_3d!r}, "
                        f"attach_selector={attach_face_selector!r}, "
                        f"position_u={position_u}, "
                        f"position_v={position_v}, "
                        f"use_world_origin={use_world_origin!r}"
                        ")"
                    ),
                ]
            bootstrap_width = self._to_positive_float(params.get("width"), default=0.0)
            bootstrap_height = self._to_positive_float(params.get("height"), default=0.0)
            bootstrap_inner_width = self._to_positive_float(
                params.get("inner_width"),
                default=0.0,
            )
            bootstrap_inner_height = self._to_positive_float(
                params.get("inner_height"),
                default=0.0,
            )
            bootstrap_position = params.get("position", params.get("center"))
            bootstrap_x = 0.0
            bootstrap_y = 0.0
            if (
                isinstance(bootstrap_position, list)
                and len(bootstrap_position) >= 2
                and isinstance(bootstrap_position[0], (int, float))
                and isinstance(bootstrap_position[1], (int, float))
            ):
                bootstrap_x = float(bootstrap_position[0])
                bootstrap_y = float(bootstrap_position[1])
            if bootstrap_width > 0.0 and bootstrap_height > 0.0:
                inner_size = (
                    repr([bootstrap_inner_width, bootstrap_inner_height])
                    if (
                        bootstrap_inner_width > 0.0
                        and bootstrap_inner_height > 0.0
                        and bootstrap_inner_width < bootstrap_width
                        and bootstrap_inner_height < bootstrap_height
                    )
                    else "None"
                )
                code_lines.extend(
                    [
                        "_aicad_stepped_profile = None",
                        (
                            "_aicad_sketch = _aicad_add_rectangle_to_sketch("
                            "_aicad_sketch, "
                            f"{bootstrap_width}, {bootstrap_height}, ({bootstrap_x}, {bootstrap_y}), "
                            f"inner_size={inner_size}"
                            ")"
                        ),
                    ]
                )
            else:
                bootstrap_radius = self._to_positive_float(params.get("radius"), default=0.0)
                if bootstrap_radius <= 0.0:
                    bootstrap_diameter = self._to_positive_float(
                        params.get("diameter"),
                        default=0.0,
                    )
                    if bootstrap_diameter > 0.0:
                        bootstrap_radius = bootstrap_diameter / 2.0
                if bootstrap_radius > 0.0:
                    code_lines.extend(
                        [
                            "_aicad_stepped_profile = None",
                            f"_aicad_circle_points_raw = {[[bootstrap_x, bootstrap_y]]!r}",
                            "_aicad_circle_points = _aicad_localize_points_for_plane(_aicad_state_plane(_aicad_sketch), _aicad_circle_points_raw)",
                            (
                                "_aicad_sketch = _aicad_add_circles_to_sketch("
                                "_aicad_sketch, "
                                f"{bootstrap_radius}, _aicad_circle_points, radius_inner=0.0"
                                ")"
                            ),
                        ]
                    )
            code_lines.append("result = _aicad_result_or_preview(result, _aicad_sketch)")

        elif action_type == CADActionType.ADD_RECTANGLE:
            width = params.get("width", 50)
            height = params.get("height", 50)
            inner_width = self._to_positive_float(
                params.get("inner_width"),
                default=0.0,
            )
            inner_height = self._to_positive_float(
                params.get("inner_height"),
                default=0.0,
            )
            centered = bool(params.get("centered", False))
            anchor = self._normalize_rectangle_anchor_token(params.get("anchor", "center"))
            position = params.get("position", params.get("center"))
            position_x = 0.0
            position_y = 0.0
            has_explicit_position = False
            if (
                isinstance(position, list)
                and len(position) >= 2
                and isinstance(position[0], (int, float))
                and isinstance(position[1], (int, float))
            ):
                position_x = float(position[0])
                position_y = float(position[1])
                has_explicit_position = True
            if centered:
                anchor = "center"
            offset_x = position_x
            offset_y = position_y
            if has_explicit_position:
                if anchor == "lower_left":
                    offset_x += float(width) / 2.0
                    offset_y += float(height) / 2.0
                elif anchor == "lower_right":
                    offset_x -= float(width) / 2.0
                    offset_y += float(height) / 2.0
                elif anchor == "top_left":
                    offset_x += float(width) / 2.0
                    offset_y -= float(height) / 2.0
                elif anchor == "top_right":
                    offset_x -= float(width) / 2.0
                    offset_y -= float(height) / 2.0
            code_lines = [
                "_aicad_stepped_profile = None",
                (
                    "_aicad_sketch = _aicad_add_rectangle_to_sketch("
                    "_aicad_sketch, "
                    f"{width}, {height}, ({offset_x}, {offset_y}), "
                    f"inner_size={repr([inner_width, inner_height]) if (inner_width > 0.0 and inner_height > 0.0 and inner_width < float(width) and inner_height < float(height)) else 'None'}"
                    ")"
                ),
            ]
            code_lines.extend(
                [
                    "result = _aicad_result_or_preview(result, _aicad_sketch)",
                ]
            )

        elif action_type == CADActionType.ADD_CIRCLE:
            radius = self._to_positive_float(params.get("radius"), default=0.0)
            if radius <= 0.0:
                diameter = self._to_positive_float(params.get("diameter"), default=0.0)
                if diameter > 0.0:
                    radius = diameter / 2.0
            if radius <= 0.0:
                radius = 5.0
            radius_inner = self._to_positive_float(
                params.get("radius_inner"),
                default=0.0,
            )
            circle_centers_raw = params.get("centers", params.get("positions"))
            circle_centers: list[list[float]] = []
            if isinstance(circle_centers_raw, list):
                for raw_center in circle_centers_raw:
                    if isinstance(raw_center, dict):
                        values = [
                            float(raw_center[axis])
                            for axis in ("x", "y", "z")
                            if isinstance(raw_center.get(axis), (int, float))
                        ]
                    elif isinstance(raw_center, (list, tuple)):
                        values = [
                            float(value)
                            for value in raw_center
                            if isinstance(value, (int, float))
                        ]
                    else:
                        values = []
                    if len(values) >= 2:
                        circle_centers.append(values[:3])
            circle_position = params.get("position", params.get("center"))
            circle_values = self._extract_numeric_sequence(circle_position)
            circle_points_raw: list[list[float]] = []
            if circle_centers:
                circle_points_raw = circle_centers
            elif len(circle_values) >= 3:
                circle_points_raw = [[
                    float(circle_values[0]),
                    float(circle_values[1]),
                    float(circle_values[2]),
                ]]
            elif len(circle_values) >= 2:
                circle_points_raw = [[float(circle_values[0]), float(circle_values[1])]]
            else:
                circle_points_raw = [[0.0, 0.0]]
            code_lines = [
                "_aicad_stepped_profile = None",
                f"_aicad_circle_points_raw = {circle_points_raw!r}",
                "_aicad_circle_points = _aicad_localize_points_for_plane(_aicad_state_plane(_aicad_sketch), _aicad_circle_points_raw)",
                (
                    "_aicad_sketch = _aicad_add_circles_to_sketch("
                    "_aicad_sketch, "
                    f"{radius}, _aicad_circle_points, radius_inner={radius_inner}"
                    ")"
                ),
            ]
            code_lines.extend(
                [
                    "result = _aicad_result_or_preview(result, _aicad_sketch)",
                ]
            )

        elif action_type == CADActionType.ADD_POLYGON:
            points = params.get("points", params.get("vertices", []))
            normalized_points: list[tuple[float, float]] = []
            code_lines = []
            stepped_profile_data: dict[str, list[float]] | None = None
            if isinstance(points, list):
                for raw_point in points:
                    if (
                        isinstance(raw_point, list)
                        and len(raw_point) >= 2
                        and isinstance(raw_point[0], (int, float))
                        and isinstance(raw_point[1], (int, float))
                    ):
                        normalized_points.append(
                            (float(raw_point[0]), float(raw_point[1]))
                        )
                    elif isinstance(raw_point, dict):
                        x_value = raw_point.get("x")
                        y_value = raw_point.get("y")
                        if isinstance(x_value, (int, float)) and isinstance(
                            y_value, (int, float)
                        ):
                            normalized_points.append(
                                (float(x_value), float(y_value))
                            )
                    elif (
                        isinstance(raw_point, tuple)
                        and len(raw_point) >= 2
                        and isinstance(raw_point[0], (int, float))
                        and isinstance(raw_point[1], (int, float))
                    ):
                        normalized_points.append(
                            (float(raw_point[0]), float(raw_point[1]))
                            )
            if len(normalized_points) < 3:
                # Regular polygon form
                side_count_raw = params.get(
                    "sides",
                    params.get(
                        "n_sides",
                        params.get(
                            "num_sides",
                            params.get("side_count", params.get("regular_sides")),
                        ),
                    ),
                )
                side_count = (
                    int(side_count_raw)
                    if isinstance(side_count_raw, (int, float))
                    else 0
                )
                radius_outer = self._to_positive_float(
                    params.get("radius_outer", params.get("radius")),
                    default=0.0,
                )
                side_length = self._to_positive_float(
                    params.get("side_length"),
                    default=0.0,
                )
                if radius_outer <= 0.0 and side_count >= 3 and side_length > 0.0:
                    radius_outer = side_length / (
                        2.0 * math.sin(math.pi / float(side_count))
                    )
                radius_inner = self._to_positive_float(
                    params.get("radius_inner"),
                    default=0.0,
                )
                size_mode = self._normalize_regular_polygon_size_mode(
                    params.get("size_mode", params.get("radius_mode"))
                )
                rotation_degrees = self._as_float(
                    params.get(
                        "rotation_degrees",
                        params.get("rotation", params.get("phase_degrees")),
                    ),
                    fallback=0.0,
                )
                center_raw = params.get("center", [0.0, 0.0])
                center_x = 0.0
                center_y = 0.0
                if (
                    isinstance(center_raw, list)
                    and len(center_raw) >= 2
                    and isinstance(center_raw[0], (int, float))
                    and isinstance(center_raw[1], (int, float))
                ):
                    center_x = float(center_raw[0])
                    center_y = float(center_raw[1])
                if side_count >= 3 and radius_outer > 0.0 and size_mode == "apothem":
                    radius_outer = radius_outer / max(
                        math.cos(math.pi / float(side_count)),
                        1e-6,
                    )
                    if radius_inner > 0.0:
                        radius_inner = radius_inner / max(
                            math.cos(math.pi / float(side_count)),
                            1e-6,
                        )

                # Stepped shaft profile form: lengths + radiuses/radii
                lengths_raw = params.get("lengths", params.get("length_list"))
                radiuses_raw = params.get(
                    "radiuses",
                    params.get("radii", params.get("radius_list")),
                )
                if (
                    isinstance(lengths_raw, list)
                    and isinstance(radiuses_raw, list)
                    and len(lengths_raw) == len(radiuses_raw)
                    and len(lengths_raw) >= 2
                    and all(isinstance(item, (int, float)) for item in lengths_raw)
                    and all(isinstance(item, (int, float)) for item in radiuses_raw)
                ):
                    stepped_lengths = [
                        max(0.0, float(item)) for item in lengths_raw
                    ]
                    stepped_radii = [
                        max(0.0, float(item)) for item in radiuses_raw
                    ]
                    axial_position = 0.0
                    profile_points: list[tuple[float, float]] = []
                    current_radius = stepped_radii[0]
                    profile_points.append((current_radius, 0.0))
                    for length_value, radius_value in zip(
                        stepped_lengths, stepped_radii
                    ):
                        axial_position += length_value
                        profile_points.append((current_radius, axial_position))
                        target_radius = radius_value
                        if abs(target_radius - current_radius) > 1e-6:
                            profile_points.append((target_radius, axial_position))
                        current_radius = target_radius
                    profile_points.append((0.0, axial_position))
                    profile_points.append((0.0, 0.0))
                    normalized_points = profile_points
                    stepped_profile_data = {
                        "lengths": stepped_lengths,
                        "radii": stepped_radii,
                    }
                elif side_count >= 3 and radius_outer > 0.0:
                    code_lines = [
                        "_aicad_stepped_profile = None",
                        (
                            "_aicad_sketch = _aicad_add_regular_polygon_to_sketch("
                            "_aicad_sketch, "
                            f"{side_count}, {radius_outer}, ({center_x}, {center_y}), "
                            f"rotation_degrees={rotation_degrees}, radius_inner={radius_inner}"
                            ")"
                        ),
                        "result = _aicad_result_or_preview(result, _aicad_sketch)",
                    ]
                else:
                    return ""
            else:
                inferred_stepped_profile = self._infer_stepped_profile_from_points(
                    normalized_points
                )
                if inferred_stepped_profile is not None:
                    stepped_lengths, stepped_radii = inferred_stepped_profile
                    stepped_profile_data = {
                        "lengths": stepped_lengths,
                        "radii": stepped_radii,
                    }
                else:
                    inferred_revolve_bands = (
                        self._infer_revolve_profile_bands_from_points(
                            normalized_points
                        )
                    )
                    if inferred_revolve_bands is not None:
                        stepped_profile_data = {
                            "bands": inferred_revolve_bands,
                        }

            if len(normalized_points) >= 3 and not code_lines:
                points_str = repr(normalized_points)
                stepped_profile_literal = (
                    repr(stepped_profile_data)
                    if stepped_profile_data is not None
                    else "None"
                )
                code_lines = [
                    f"_aicad_stepped_profile = {stepped_profile_literal}",
                    f"_aicad_polygon_points = {points_str}",
                    "_aicad_sketch = _aicad_add_polygon_to_sketch(_aicad_sketch, _aicad_polygon_points)",
                    "result = _aicad_result_or_preview(result, _aicad_sketch)",
                ]
            elif not code_lines:
                return ""

        elif action_type == CADActionType.ADD_PATH:
            start_raw = params.get("start", [0.0, 0.0])
            start_point = [0.0, 0.0]
            if (
                isinstance(start_raw, list)
                and len(start_raw) >= 2
                and isinstance(start_raw[0], (int, float))
                and isinstance(start_raw[1], (int, float))
            ):
                start_point = [float(start_raw[0]), float(start_raw[1])]
            segments_raw = params.get("segments", [])
            segments_literal = segments_raw if isinstance(segments_raw, list) else []
            closed = self._path_closed_flag(params)
            code_lines = [
                f"_aicad_path_start = {start_point!r}",
                f"_aicad_path_segments = {segments_literal!r}",
                f"_aicad_path_closed = {closed!r}",
                "_aicad_stepped_profile = None",
                "_aicad_sketch = _aicad_add_path_to_sketch(_aicad_sketch, _aicad_path_start, _aicad_path_segments, closed=_aicad_path_closed)",
                "result = _aicad_result_or_preview(result, _aicad_sketch)",
            ]

        elif action_type == CADActionType.EXTRUDE:
            distance = self._resolve_linear_span_param(
                params,
                canonical_key="distance",
                aliases=("height", "length"),
                default=20.0,
            )
            direction_raw = params.get("direction", "up")
            direction = (
                str(direction_raw).strip().lower()
                if isinstance(direction_raw, str)
                else "up"
            )
            both_sides = bool(
                params.get(
                    "both_sides",
                    params.get(
                        "symmetric",
                        params.get("symmetrical", params.get("centered", False)),
                    ),
                )
            )
            positive_directions = {
                "up",
                "+z",
                "+y",
                "+x",
                "z",
                "y",
                "x",
                "positive",
                "forward",
            }
            reverse = not both_sides and direction not in positive_directions
            code_lines = [
                f"_aicad_extrude_distance = {distance}",
                f"_aicad_extrude_both = {both_sides!r}",
                f"_aicad_extrude_reverse = {reverse!r}",
                "result, _aicad_last_additive_feature = _aicad_apply_extrude(",
                "    result,",
                "    _aicad_sketch,",
                "    _aicad_extrude_distance,",
                "    both=_aicad_extrude_both,",
                "    reverse=_aicad_extrude_reverse,",
                ")",
                "_aicad_sketch = None",
                "_aicad_sketch_face_id = None",
                "_aicad_sketch_face_hint = None",
                "_aicad_sketch_from_face_ref = False",
                "_aicad_stepped_profile = None",
                "_aicad_loft_profiles = []",
            ]

        elif action_type == CADActionType.CUT_EXTRUDE:
            distance = self._resolve_linear_span_param(
                params,
                canonical_key="distance",
                aliases=("depth", "height", "length"),
                default=5.0,
            )
            both_sides = bool(params.get("both_sides", False))
            through_all_raw = params.get("through_all", params.get("condition", False))
            through_all = bool(through_all_raw)
            if isinstance(through_all_raw, str):
                through_all = through_all_raw.strip().lower() in {
                    "through",
                    "through_all",
                    "all",
                }
            outside_cut = bool(
                params.get(
                    "flip_side",
                    params.get(
                        "outside_cut",
                        params.get("flip_side_to_cut", False),
                    ),
                )
            )
            code_lines = [
                f"_aicad_cut_depth_raw = {repr(distance)}",
                f"_aicad_cut_through_all = {through_all!r}",
                f"_aicad_cut_outside = {outside_cut!r}",
                f"_aicad_cut_both_sides = {both_sides!r}",
                "result = _aicad_apply_cut_extrude(",
                "    result,",
                "    _aicad_sketch,",
                "    _aicad_cut_depth_raw,",
                "    through_all=_aicad_cut_through_all,",
                "    outside_cut=_aicad_cut_outside,",
                "    both_sides=_aicad_cut_both_sides,",
                ")",
                "_aicad_last_additive_feature = None",
                "_aicad_sketch = None",
                "_aicad_sketch_face_id = None",
                "_aicad_sketch_face_hint = None",
                "_aicad_sketch_from_face_ref = False",
                "_aicad_stepped_profile = None",
                "_aicad_loft_profiles = []",
            ]

        elif action_type == CADActionType.TRIM_SOLID:
            trim_plane = self._normalize_sketch_plane_name(params.get("plane"))
            trim_keep = self._normalize_trim_keep_side(
                plane=trim_plane,
                keep=params.get("keep", params.get("keep_side")),
            )
            trim_offset = self._resolve_trim_plane_offset(
                plane=trim_plane,
                offset=params.get("offset"),
                origin=params.get(
                    "origin",
                    params.get("position", params.get("center")),
                ),
            )
            code_lines = [
                f"_aicad_trim_plane = {trim_plane!r}",
                f"_aicad_trim_keep = {trim_keep!r}",
                f"_aicad_trim_offset = float({trim_offset})",
                "result = _aicad_trim_solid(result, _aicad_trim_plane, _aicad_trim_keep, _aicad_trim_offset)",
                "_aicad_last_additive_feature = None",
                "_aicad_sketch = None",
                "_aicad_sketch_face_id = None",
                "_aicad_sketch_face_hint = None",
                "_aicad_sketch_from_face_ref = False",
                "_aicad_stepped_profile = None",
                "_aicad_loft_profiles = []",
            ]

        elif action_type == CADActionType.FILLET:
            radius = self._to_positive_float(params.get("radius"), default=2.0)
            edge_refs = params.get("edge_refs")
            if isinstance(edge_refs, list) and edge_refs:
                edge_ids = [
                    parsed_ref["entity_id"]
                    for edge_ref in edge_refs
                    for parsed_ref in [parse_topology_ref(edge_ref)]
                    if parsed_ref is not None and parsed_ref.get("kind") == "edge"
                ]
                code_lines = [
                    f"_aicad_edge_ids = {edge_ids!r}",
                    f"result = _aicad_apply_fillet(result, {radius}, edge_ids=_aicad_edge_ids)",
                ]
            else:
                code_lines = [
                    (
                        "result = _aicad_apply_fillet("
                        f"result, {radius}, edge_scope={params.get('edge_scope')!r}, "
                        f"edges_selector={params.get('edges_selector')!r}"
                        ")"
                    ),
                ]

        elif action_type == CADActionType.CHAMFER:
            distance = self._to_positive_float(params.get("distance"), default=1.0)
            edge_refs = params.get("edge_refs")
            if isinstance(edge_refs, list) and edge_refs:
                edge_ids = [
                    parsed_ref["entity_id"]
                    for edge_ref in edge_refs
                    for parsed_ref in [parse_topology_ref(edge_ref)]
                    if parsed_ref is not None and parsed_ref.get("kind") == "edge"
                ]
                code_lines = [
                    f"_aicad_edge_ids = {edge_ids!r}",
                    f"result = _aicad_apply_chamfer(result, {distance}, edge_ids=_aicad_edge_ids)",
                ]
            else:
                code_lines = [
                    (
                        "result = _aicad_apply_chamfer("
                        f"result, {distance}, edge_scope={params.get('edge_scope')!r}, "
                        f"edges_selector={params.get('edges_selector')!r}"
                        ")"
                    ),
                ]

        elif action_type == CADActionType.HOLE:
            params = self._normalize_hole_action_params(params)
            diameter = params.get("diameter", 5)
            depth = params.get("depth", None)
            countersink_diameter = params.get("countersink_diameter")
            countersink_angle = params.get("countersink_angle", 90.0)
            position = params.get("position", params.get("center", [0, 0]))
            centers_raw = params.get("centers", params.get("positions"))
            face_ref = params.get("face_ref")
            face_candidate_hint = params.get("_face_candidate_hint")
            parsed_face_ref = parse_topology_ref(face_ref)
            explicit_face_id = (
                parsed_face_ref["entity_id"]
                if parsed_face_ref is not None and parsed_face_ref.get("kind") == "face"
                else None
            )
            centers: list[list[float]] = []
            if isinstance(centers_raw, list):
                for item in centers_raw:
                    if not isinstance(item, list) or len(item) < 2:
                        continue
                    if not isinstance(item[0], (int, float)) or not isinstance(item[1], (int, float)):
                        continue
                    point = [float(item[0]), float(item[1])]
                    if len(item) >= 3 and isinstance(item[2], (int, float)):
                        point.append(float(item[2]))
                    centers.append(point)
            default_center: list[float] = [0.0, 0.0]
            if isinstance(position, list) and len(position) >= 2:
                if isinstance(position[0], (int, float)) and isinstance(position[1], (int, float)):
                    default_center = [float(position[0]), float(position[1])]
                    if len(position) >= 3 and isinstance(position[2], (int, float)):
                        default_center.append(float(position[2]))
            raw_centers_literal = centers if centers else [default_center]
            depth_literal = repr(depth)
            code_lines = [
                f"_aicad_hole_points_raw = {raw_centers_literal!r}",
                f"_aicad_hole_depth_raw = {depth_literal}",
                f"_aicad_explicit_hole_face_id = {explicit_face_id!r}",
                f"_aicad_hole_face_hint = {face_candidate_hint!r}",
                f"_aicad_countersink_diameter = {repr(countersink_diameter)}",
                f"_aicad_countersink_angle = {repr(countersink_angle)}",
                "_aicad_has_countersink = isinstance(_aicad_countersink_diameter, (int, float)) and float(_aicad_countersink_diameter) > float("
                + str(diameter)
                + ")",
                "result = _aicad_apply_holes(",
                "    result,",
                "    _aicad_sketch,",
                f"    {diameter},",
                "    _aicad_hole_depth_raw,",
                "    _aicad_hole_points_raw,",
                "    explicit_face_id=_aicad_explicit_hole_face_id,",
                "    face_hint=_aicad_hole_face_hint,",
                "    countersink_diameter=_aicad_countersink_diameter,",
                "    countersink_angle=_aicad_countersink_angle,",
                ")",
                "_aicad_last_additive_feature = None",
                "_aicad_sketch = None",
                "_aicad_sketch_face_id = None",
                "_aicad_sketch_face_hint = None",
                "_aicad_sketch_from_face_ref = False",
                "_aicad_stepped_profile = None",
                "_aicad_loft_profiles = []",
            ]

        elif action_type == CADActionType.SPHERE_RECESS:
            radius_value = params.get("radius")
            if not isinstance(radius_value, (int, float)) or float(radius_value) <= 0.0:
                diameter_value = params.get("diameter")
                if isinstance(diameter_value, (int, float)) and float(diameter_value) > 0.0:
                    radius_value = float(diameter_value) / 2.0
                else:
                    radius_value = 2.5
            radius = float(radius_value)
            position = params.get("position", params.get("center", [0, 0]))
            centers_raw = params.get("centers", params.get("positions"))
            centers: list[list[float]] = []
            if isinstance(centers_raw, list):
                for item in centers_raw:
                    if not isinstance(item, list) or len(item) < 2:
                        continue
                    if not isinstance(item[0], (int, float)) or not isinstance(
                        item[1], (int, float)
                    ):
                        continue
                    centers.append([float(item[0]), float(item[1])])
            x = 0.0
            y = 0.0
            if isinstance(position, list) and len(position) >= 2:
                if isinstance(position[0], (int, float)) and isinstance(
                    position[1], (int, float)
                ):
                    x = float(position[0])
                    y = float(position[1])
            centers_literal = centers if centers else [[x, y]]
            face_ref = params.get("face_ref")
            face_candidate_hint = params.get("_face_candidate_hint")
            parsed_face_ref = parse_topology_ref(face_ref)
            explicit_face_id = (
                parsed_face_ref["entity_id"]
                if parsed_face_ref is not None and parsed_face_ref.get("kind") == "face"
                else None
            )
            code_lines = [
                f"_aicad_recess_radius = {radius}",
                f"_aicad_recess_points = {centers_literal!r}",
                f"_aicad_explicit_recess_face_id = {explicit_face_id!r}",
                f"_aicad_recess_face_hint = {face_candidate_hint!r}",
                "result = _aicad_apply_sphere_recesses(",
                "    result,",
                "    _aicad_sketch,",
                "    _aicad_recess_radius,",
                "    _aicad_recess_points,",
                "    explicit_face_id=_aicad_explicit_recess_face_id,",
                "    face_hint=_aicad_recess_face_hint,",
                ")",
                "_aicad_last_additive_feature = None",
                "_aicad_sketch = None",
                "_aicad_sketch_face_id = None",
                "_aicad_sketch_face_hint = None",
                "_aicad_sketch_from_face_ref = False",
                "_aicad_stepped_profile = None",
            ]

        elif action_type == CADActionType.PATTERN_LINEAR:
            count = params.get("count", 2)
            spacing = params.get("spacing", 10)
            direction = params.get("direction", "X")
            code_lines = [
                f"_aicad_pattern_count = max(2, int({count}))",
                f"_aicad_pattern_spacing = float({spacing})",
                f"_aicad_pattern_direction_raw = {direction!r}",
                "_aicad_pattern_source = globals().get('_aicad_last_additive_feature')",
                "result, _aicad_last_additive_feature = _aicad_pattern_linear(",
                "    result,",
                "    _aicad_pattern_source,",
                "    _aicad_pattern_count,",
                "    _aicad_pattern_spacing,",
                "    _aicad_pattern_direction_raw,",
                ")",
            ]

        elif action_type == CADActionType.PATTERN_CIRCULAR:
            count = params.get("count", 4)
            axis = params.get("axis", "Z")
            center = params.get("center", [0.0, 0.0, 0.0])
            total_angle = params.get("total_angle", params.get("angle", 360.0))
            code_lines = [
                f"_aicad_pattern_count = max(2, int({count}))",
                f"_aicad_pattern_axis_raw = {axis!r}",
                f"_aicad_pattern_center_raw = {center!r}",
                f"_aicad_pattern_total_angle = float({total_angle})",
                "_aicad_pattern_source = globals().get('_aicad_last_additive_feature')",
                "result, _aicad_last_additive_feature = _aicad_pattern_circular(",
                "    result,",
                "    _aicad_pattern_source,",
                "    _aicad_pattern_count,",
                "    _aicad_pattern_axis_raw,",
                "    _aicad_pattern_center_raw,",
                "    _aicad_pattern_total_angle,",
                ")",
            ]

        elif action_type == CADActionType.REVOLVE:
            angle_value = self._to_positive_float(
                params.get("angle_degrees", params.get("angle", 360.0)),
                default=360.0,
            )
            axis_start_raw = params.get("axis_start", [0.0, 0.0, 0.0])
            axis_end_raw = params.get("axis_end")
            axis_raw = params.get("axis", "Z")
            axis = str(axis_raw).strip().upper() if isinstance(axis_raw, str) else "Z"
            if axis not in {"X", "Y", "Z"}:
                axis = "Z"
            operation_raw = params.get(
                "operation",
                params.get("mode", params.get("boolean", "add")),
            )
            operation = (
                str(operation_raw).strip().lower()
                if isinstance(operation_raw, str)
                else "add"
            )
            is_cut = operation in {
                "cut",
                "subtract",
                "subtractive",
                "difference",
                "remove",
            }

            axis_start = self._normalize_axis_point(axis_start_raw, [0.0, 0.0, 0.0])
            if isinstance(axis_end_raw, list) and len(axis_end_raw) >= 3:
                axis_end = self._normalize_axis_point(axis_end_raw, [0.0, 0.0, 1.0])
            else:
                axis_end = {
                    "X": [1.0, 0.0, 0.0],
                    "Y": [0.0, 1.0, 0.0],
                    "Z": [0.0, 0.0, 1.0],
                }[axis]
            use_cross_section = bool(params.get("use_cross_section", False))
            code_lines = [
                f"_aicad_revolve_axis = {axis!r}",
                f"_aicad_revolve_axis_start_raw = {axis_start!r}",
                f"_aicad_revolve_axis_end_raw = {axis_end!r}",
                f"_aicad_revolve_angle = {angle_value}",
                f"_aicad_revolve_is_cut = {is_cut!r}",
                f"_aicad_revolve_use_cross_section = {use_cross_section!r}",
                "result, _aicad_revolve_feature = _aicad_apply_revolve(",
                "    result,",
                "    _aicad_sketch,",
                "    _aicad_revolve_angle,",
                "    _aicad_revolve_axis,",
                "    _aicad_revolve_axis_start_raw,",
                "    _aicad_revolve_axis_end_raw,",
                "    is_cut=_aicad_revolve_is_cut,",
                "    use_cross_section=_aicad_revolve_use_cross_section,",
                "    stepped_profile=_aicad_stepped_profile,",
                "    sketch_origin_3d=globals().get('_aicad_sketch_origin_3d', [0.0, 0.0, 0.0]),",
                ")",
                "_aicad_last_additive_feature = None if _aicad_revolve_is_cut else _aicad_revolve_feature",
                "_aicad_sketch = None",
                "_aicad_sketch_face_id = None",
                "_aicad_sketch_face_hint = None",
                "_aicad_sketch_from_face_ref = False",
                "_aicad_stepped_profile = None",
                "_aicad_loft_profiles = []",
            ]

        elif action_type == CADActionType.LOFT:
            ruled = bool(params.get("ruled", True))
            to_point = params.get("to_point")
            height = params.get("height")
            code_lines = [
                "_aicad_capture_pending_loft_profile()",
                "_aicad_capture_pending_sweep_path()",
                "_aicad_loft_shapes = list(globals().get('_aicad_loft_profiles', []) or [])",
                f"_aicad_explicit_loft_point = {to_point!r}",
                f"_aicad_explicit_loft_height = {height!r}",
                f"_aicad_loft_ruled = {ruled!r}",
                "result, _aicad_last_additive_feature = _aicad_apply_loft(",
                "    result,",
                "    _aicad_loft_shapes,",
                "    _aicad_sketch,",
                "    explicit_loft_point=_aicad_explicit_loft_point,",
                "    explicit_loft_height=_aicad_explicit_loft_height,",
                "    ruled=_aicad_loft_ruled,",
                ")",
                "_aicad_sketch = None",
                "_aicad_sketch_face_id = None",
                "_aicad_sketch_face_hint = None",
                "_aicad_sketch_from_face_ref = False",
                "_aicad_stepped_profile = None",
                "_aicad_loft_profiles = []",
                "_aicad_sweep_path = None",
            ]

        elif action_type == CADActionType.SWEEP:
            transition_raw = params.get("transition", "round")
            transition = (
                str(transition_raw).strip().lower()
                if isinstance(transition_raw, str)
                else "round"
            )
            if transition not in {"right", "round", "transformed"}:
                transition = "round"
            is_frenet = bool(params.get("is_frenet", False))
            code_lines = [
                "_aicad_capture_pending_loft_profile()",
                "_aicad_capture_pending_sweep_path()",
                f"_aicad_transition = {transition!r}",
                f"_aicad_is_frenet = {is_frenet!r}",
                "result, _aicad_last_additive_feature = _aicad_apply_sweep(",
                "    result,",
                "    _aicad_sketch,",
                "    globals().get('_aicad_sweep_path'),",
                "    _aicad_transition,",
                "    is_frenet=_aicad_is_frenet,",
                ")",
                "_aicad_sketch = None",
                "_aicad_sketch_face_id = None",
                "_aicad_sketch_face_hint = None",
                "_aicad_sketch_from_face_ref = False",
                "_aicad_stepped_profile = None",
                "_aicad_loft_profiles = []",
                "_aicad_sweep_path = None",
            ]

        elif action_type in (
            CADActionType.PATTERN_LINEAR,
            CADActionType.PATTERN_CIRCULAR,
        ):
            # For now, return empty for explicit pattern actions; runtime prefers
            # lowering requirement-specified layouts into direct repeated features.
            code_lines = []

        elif action_type == CADActionType.SNAPSHOT:
            build123d_code = params.get("build123d_code")
            if isinstance(build123d_code, str) and build123d_code.strip():
                code_lines = [
                    self._runtime_wrap_build123d_code(build123d_code),
                ]
            else:
                code_lines = [
                    "result = result",
                ]

        elif action_type == CADActionType.ROLLBACK:
            # These are state operations; keep current model unchanged.
            code_lines = [
                "result = result",
            ]

        else:
            return ""

        # Add imports at the start
        if code_lines:
            code_lines.insert(0, "from pathlib import Path")
            code_lines.insert(0, "import json")

        return "\n".join(code_lines)

    def _to_positive_float(self, value: object, default: float) -> float:
        if isinstance(value, (int, float)) and value > 0:
            return float(value)
        return default

    def _normalize_hole_action_params(
        self,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        normalized = normalize_action_params(CADActionType.HOLE, params)
        countersink_diameter = normalized.get("countersink_diameter")
        if isinstance(countersink_diameter, (int, float)) and float(countersink_diameter) > 0.0:
            normalized["countersink_diameter"] = float(countersink_diameter)
        return normalized

    def _resolve_linear_span_param(
        self,
        params: dict[str, object],
        *,
        canonical_key: str,
        aliases: tuple[str, ...],
        default: float,
    ) -> float:
        candidate_keys = (canonical_key, *aliases)
        for key in candidate_keys:
            value = params.get(key)
            if isinstance(value, (int, float)):
                return float(value)
        return float(default)

    def _normalize_axis_point(
        self,
        value: object,
        default: list[float],
    ) -> list[float]:
        if (
            isinstance(value, list)
            and len(value) >= 3
            and all(isinstance(item, (int, float)) for item in value[:3])
        ):
            return [float(value[0]), float(value[1]), float(value[2])]
        return list(default)

    def _extract_numeric_sequence(self, value: object) -> list[float]:
        if isinstance(value, list):
            return [float(item) for item in value if isinstance(item, (int, float))]
        if isinstance(value, tuple):
            return [float(item) for item in value if isinstance(item, (int, float))]
        if isinstance(value, str) and value.strip():
            tokens = re.findall(r"[-+]?[0-9]*\.?[0-9]+", value)
            parsed: list[float] = []
            for token in tokens:
                try:
                    parsed.append(float(token))
                except Exception:
                    continue
            return parsed
        return []

    def _normalize_workplane_center(
        self,
        value: object,
        plane: str,
    ) -> tuple[float, float]:
        numeric = self._extract_numeric_sequence(value)
        if len(numeric) >= 3:
            x, y, z = numeric[0], numeric[1], numeric[2]
            if plane == "XY":
                return float(x), float(y)
            if plane == "XZ":
                return float(x), float(z)
            if plane == "YZ":
                return float(y), float(z)
        if len(numeric) >= 2:
            return float(numeric[0]), float(numeric[1])
        return 0.0, 0.0

    def _normalize_sketch_origin_3d(
        self,
        value: object,
        plane: str,
    ) -> list[float]:
        numeric = self._extract_numeric_sequence(value)
        if len(numeric) >= 3:
            return [float(numeric[0]), float(numeric[1]), float(numeric[2])]
        if len(numeric) >= 2:
            u = float(numeric[0])
            v = float(numeric[1])
            if plane == "XZ":
                return [u, 0.0, v]
            if plane == "YZ":
                return [0.0, u, v]
            return [u, v, 0.0]
        return [0.0, 0.0, 0.0]

    def _resolve_create_sketch_origin_3d(
        self,
        params: dict[str, CADParamValue],
        plane: str,
    ) -> list[float]:
        if "origin" in params:
            return self._normalize_sketch_origin_3d(
                params.get("origin"),
                plane=plane,
            )
        offset = params.get("offset")
        if isinstance(offset, (int, float)):
            offset_value = float(offset)
            if plane == "XZ":
                return [0.0, offset_value, 0.0]
            if plane == "YZ":
                return [offset_value, 0.0, 0.0]
            return [0.0, 0.0, offset_value]
        return self._normalize_sketch_origin_3d(
            params.get("position", params.get("center")),
            plane=plane,
        )

    def _normalize_sketch_plane_name(self, plane: object) -> str:
        plane_token = str(plane).strip().upper() if isinstance(plane, str) else "XY"
        return {
            "TOP": "XY",
            "BOTTOM": "XY",
            "FRONT": "XZ",
            "BACK": "XZ",
            "RIGHT": "YZ",
            "LEFT": "YZ",
        }.get(plane_token, plane_token if plane_token in {"XY", "XZ", "YZ"} else "XY")

    def _normalize_trim_keep_side(
        self,
        plane: str,
        keep: object,
    ) -> str:
        token = str(keep).strip().lower() if isinstance(keep, str) else ""
        if plane == "XZ":
            if token in {"front", "positive", "+", "+y", "pos", "keep_front"}:
                return "front"
            if token in {"back", "negative", "-", "-y", "neg", "keep_back"}:
                return "back"
            return "front"
        if plane == "YZ":
            if token in {"right", "positive", "+", "+x", "pos", "keep_right"}:
                return "right"
            if token in {"left", "negative", "-", "-x", "neg", "keep_left"}:
                return "left"
            return "right"
        if token in {"above", "top", "positive", "+", "+z", "up", "keep_above"}:
            return "above"
        if token in {"below", "bottom", "negative", "-", "-z", "down", "keep_below"}:
            return "below"
        return "below"

    def _resolve_trim_plane_offset(
        self,
        plane: str,
        offset: object,
        origin: object,
    ) -> float:
        if isinstance(offset, (int, float)):
            return float(offset)
        origin3d = self._normalize_sketch_origin_3d(origin, plane=plane)
        if plane == "XZ":
            return float(origin3d[1] if len(origin3d) >= 2 else 0.0)
        if plane == "YZ":
            return float(origin3d[0] if len(origin3d) >= 1 else 0.0)
        return float(origin3d[2] if len(origin3d) >= 3 else 0.0)

    def _infer_stepped_profile_from_points(
        self,
        points: list[tuple[float, float]],
    ) -> tuple[list[float], list[float]] | None:
        if len(points) < 4:
            return None
        normalized_points: list[tuple[float, float]] = []
        for point in points:
            if not normalized_points or point != normalized_points[-1]:
                normalized_points.append((float(point[0]), float(point[1])))
        if len(normalized_points) < 4:
            return None
        first_point = normalized_points[0]
        last_point = normalized_points[-1]
        if abs(first_point[0]) > 1e-6 or abs(last_point[0]) > 1e-6:
            return None
        if any(point[0] < -1e-6 for point in normalized_points):
            return None
        if any(
            normalized_points[index + 1][1] < normalized_points[index][1] - 1e-6
            for index in range(len(normalized_points) - 1)
        ):
            return None

        lengths: list[float] = []
        radii: list[float] = []
        for start, end in zip(normalized_points, normalized_points[1:]):
            if start[0] <= 1e-6 and end[0] <= 1e-6:
                continue
            if abs(start[0] - end[0]) <= 1e-6 and end[1] > start[1] + 1e-6:
                lengths.append(float(end[1] - start[1]))
                radii.append(float(start[0]))
        if len(lengths) < 2 or len(lengths) != len(radii):
            return None
        return lengths, radii

    def _infer_revolve_profile_bands_from_points(
        self,
        points: list[tuple[float, float]],
    ) -> list[dict[str, float]] | None:
        if len(points) < 4:
            return None
        normalized_points: list[tuple[float, float]] = []
        for point in points:
            px = float(point[0])
            py = float(point[1])
            if not normalized_points or (
                abs(px - normalized_points[-1][0]) > 1e-6
                or abs(py - normalized_points[-1][1]) > 1e-6
            ):
                normalized_points.append((px, py))
        if len(normalized_points) < 4:
            return None
        if any(point[0] < -1e-6 for point in normalized_points):
            return None
        closed_points = normalized_points + [normalized_points[0]]
        for start, end in zip(closed_points, closed_points[1:]):
            dx = abs(float(end[0]) - float(start[0]))
            dy = abs(float(end[1]) - float(start[1]))
            if dx > 1e-6 and dy > 1e-6:
                # Do not flatten tapered/conical profiles into orthogonal stepped bands.
                return None
        unique_y = sorted({round(point[1], 6) for point in normalized_points})
        if len(unique_y) < 2:
            return None

        def _x_hits_for_span(y_mid: float) -> list[float]:
            hits: list[float] = []
            for start, end in zip(closed_points, closed_points[1:]):
                x1, y1 = start
                x2, y2 = end
                if abs(y1 - y2) <= 1e-6:
                    continue
                ymin = min(y1, y2)
                ymax = max(y1, y2)
                if y_mid < ymin - 1e-6 or y_mid > ymax + 1e-6:
                    continue
                if abs(x1 - x2) <= 1e-6:
                    hits.append(float(x1))
                    continue
                ratio = (y_mid - y1) / (y2 - y1)
                hits.append(float(x1 + ((x2 - x1) * ratio)))
            filtered_hits = sorted(
                {
                    round(hit, 6)
                    for hit in hits
                    if hit >= -1e-6
                }
            )
            return [float(hit) for hit in filtered_hits]

        bands: list[dict[str, float]] = []
        for start_y, end_y in zip(unique_y, unique_y[1:]):
            span = float(end_y - start_y)
            if span <= 1e-6:
                continue
            y_mid = float(start_y + (span / 2.0))
            hits = _x_hits_for_span(y_mid)
            if len(hits) < 2:
                return None
            inner_radius = max(0.0, float(hits[0]))
            outer_radius = max(0.0, float(hits[-1]))
            if outer_radius <= inner_radius + 1e-6:
                return None
            band = {
                "length": span,
                "inner_radius": inner_radius,
                "outer_radius": outer_radius,
            }
            if bands:
                previous = bands[-1]
                if (
                    abs(previous["inner_radius"] - band["inner_radius"]) <= 1e-6
                    and abs(previous["outer_radius"] - band["outer_radius"]) <= 1e-6
                ):
                    previous["length"] += span
                    continue
            bands.append(band)
        return bands or None

    def _sketch_plane_name_from_entry(
        self,
        entry: ActionHistoryEntry,
    ) -> str | None:
        if entry.action_type != CADActionType.CREATE_SKETCH:
            return None
        if isinstance(entry.action_params.get("face_ref"), str) and str(
            entry.action_params.get("face_ref")
        ).strip():
            return None
        return self._normalize_sketch_plane_name(entry.action_params.get("plane", "XY"))

    def _collect_additive_feature_planes(
        self,
        history: list[ActionHistoryEntry],
    ) -> set[str]:
        planes: set[str] = set()
        for index, entry in enumerate(history):
            if not self._action_is_additive_feature(entry):
                continue
            if not self._history_action_materially_changes_geometry(history, index):
                continue
            sketch_index = self._find_preceding_sketch_index(history, index)
            if sketch_index is None:
                continue
            plane = self._sketch_plane_name_from_entry(history[sketch_index])
            if plane:
                planes.add(plane)
        return planes

    def _collect_additive_feature_span_signatures(
        self,
        history: list[ActionHistoryEntry],
    ) -> set[tuple[float, float, float]]:
        signatures: set[tuple[float, float, float]] = set()
        for index, entry in enumerate(history):
            if entry.action_type != CADActionType.EXTRUDE:
                continue
            if not self._history_action_materially_changes_geometry(history, index):
                continue
            sketch_index = self._find_preceding_sketch_index(history, index)
            if sketch_index is None:
                continue
            plane = self._sketch_plane_name_from_entry(history[sketch_index])
            if plane not in {"XY", "XZ", "YZ"}:
                continue
            profile_entry = None
            for candidate in history[sketch_index + 1 : index]:
                if candidate.action_type == CADActionType.ADD_RECTANGLE:
                    profile_entry = candidate
                    break
            if profile_entry is None:
                continue
            width = profile_entry.action_params.get("width")
            height = profile_entry.action_params.get("height")
            distance = entry.action_params.get("distance")
            if not all(isinstance(value, (int, float)) for value in (width, height, distance)):
                continue
            extrusion_span = float(distance)
            if bool(entry.action_params.get("both_sides")):
                extrusion_span *= 2.0
            if plane == "XY":
                signature = (float(width), float(height), extrusion_span)
            elif plane == "XZ":
                signature = (float(width), extrusion_span, float(height))
            else:
                signature = (extrusion_span, float(width), float(height))
            signatures.add(signature)
        return signatures

    def _build_plane_extrude_signature(
        self,
        plane: str,
        width: float,
        height: float,
        extrusion_span: float,
    ) -> tuple[float, float, float]:
        normalized_plane = self._normalize_sketch_plane_name(plane)
        if normalized_plane == "XY":
            return (float(width), float(height), float(extrusion_span))
        if normalized_plane == "XZ":
            return (float(width), float(extrusion_span), float(height))
        if normalized_plane == "YZ":
            return (float(extrusion_span), float(width), float(height))
        return (float(width), float(height), float(extrusion_span))

    def _required_multi_plane_union_plane_count(
        self,
        semantics: RequirementSemantics,
    ) -> int:
        explicit_planes = {
            self._normalize_sketch_plane_name(item)
            for item in semantics.datum_planes
        }
        if len(explicit_planes) >= 2:
            return len(explicit_planes)
        if any(token in semantics.normalized_text for token in ("orthogonal", "perpendicular")):
            return 2
        return 2

    def _extract_multi_plane_additive_specs(
        self,
        requirement_text: str | None,
    ) -> list[dict[str, float | str]]:
        if not isinstance(requirement_text, str) or not requirement_text.strip():
            return []
        normalized = " ".join(requirement_text.strip().lower().split())
        plane_pattern = re.compile(r"\bselect the (?P<plane>xy|xz|yz) plane\b", re.IGNORECASE)
        segment_patterns = (
            re.compile(
                r"\brectangle\b"
                r"[^0-9]{0,24}(?P<width>[0-9]+(?:\.[0-9]+)?)"
                r"\s*(?:x|×)\s*"
                r"(?P<height>[0-9]+(?:\.[0-9]+)?)"
                r".{0,200}?\bextrud(?:e|ed|ing)?\b"
                r"[^0-9]{0,24}(?P<distance>[0-9]+(?:\.[0-9]+)?)",
                re.IGNORECASE | re.DOTALL,
            ),
            re.compile(
                r"(?P<width>[0-9]+(?:\.[0-9]+)?)"
                r"\s*(?:x|×)\s*"
                r"(?P<height>[0-9]+(?:\.[0-9]+)?)"
                r"[^.]{0,48}?\brectangle\b"
                r".{0,200}?\bextrud(?:e|ed|ing)?\b"
                r"[^0-9]{0,24}(?P<distance>[0-9]+(?:\.[0-9]+)?)",
                re.IGNORECASE | re.DOTALL,
            ),
        )
        specs: list[dict[str, float | str]] = []
        seen: set[tuple[str, float, float, float]] = set()
        plane_matches = list(plane_pattern.finditer(normalized))
        for index, plane_match in enumerate(plane_matches):
            plane = str(plane_match.group("plane")).upper()
            start = plane_match.end()
            end = (
                plane_matches[index + 1].start()
                if index + 1 < len(plane_matches)
                else len(normalized)
            )
            segment = normalized[start:end]
            segment_match = None
            for pattern in segment_patterns:
                segment_match = pattern.search(segment)
                if segment_match is not None:
                    break
            if segment_match is None:
                continue
            try:
                width = float(segment_match.group("width"))
                height = float(segment_match.group("height"))
                distance = float(segment_match.group("distance"))
            except Exception:
                continue
            if min(width, height, distance) <= 0.0:
                continue
            key = (plane, width, height, distance)
            if key in seen:
                continue
            seen.add(key)
            specs.append(
                {
                    "plane": plane,
                    "width": width,
                    "height": height,
                    "distance": distance,
                }
            )
        return specs

    def _build_named_plane_positive_extrude_checks(
        self,
        *,
        snapshot: CADStateSnapshot,
        requirement_text: str | None,
    ) -> list[RequirementCheck]:
        specs = self._extract_named_plane_positive_extrude_specs(requirement_text)
        if not specs:
            return []
        geometry = snapshot.geometry
        bbox_min = geometry.bbox_min if isinstance(geometry.bbox_min, list) else None
        bbox_max = geometry.bbox_max if isinstance(geometry.bbox_max, list) else None
        if not (
            isinstance(bbox_min, list)
            and isinstance(bbox_max, list)
            and len(bbox_min) >= 3
            and len(bbox_max) >= 3
        ):
            return []
        axis_index_map = {"XY": 2, "XZ": 1, "YZ": 0}
        axis_name_map = {"XY": "Z", "XZ": "Y", "YZ": "X"}
        checks: list[RequirementCheck] = []
        for spec in specs:
            plane = str(spec["plane"]).upper()
            distance = float(spec["distance"])
            require_positive_direction = bool(spec.get("require_positive_direction", True))
            axis_index = axis_index_map[plane]
            axis_name = axis_name_map[plane]
            observed_min = float(bbox_min[axis_index])
            observed_max = float(bbox_max[axis_index])
            tolerance = max(1.0, abs(distance) * 0.08)
            matches_positive_coverage = (
                abs(observed_min) <= tolerance
                and observed_max >= distance - tolerance
                and observed_max >= observed_min
            )
            matches_negative_coverage = (
                abs(observed_max) <= tolerance
                and abs(observed_min + distance) <= tolerance
                and observed_max >= observed_min
            )
            matches_plane_anchored_coverage = (
                matches_positive_coverage
                if require_positive_direction
                else (matches_positive_coverage or matches_negative_coverage)
            )
            checks.append(
                RequirementCheck(
                    check_id="feature_named_plane_positive_extrude_span",
                    label="Named-plane positive extrude preserves a plane-anchored span instead of silently centering the solid about the plane normal",
                    status=(
                        RequirementCheckStatus.PASS
                        if matches_plane_anchored_coverage
                        else RequirementCheckStatus.FAIL
                    ),
                    blocking=True,
                    evidence=(
                        f"plane={plane}, axis={axis_name}, required_lower_bound=0.0, required_minimum_extent={distance}, "
                        f"require_positive_direction={require_positive_direction}, "
                        f"observed_range=[{observed_min}, {observed_max}]"
                    ),
                )
            )
        return checks

    def _build_named_axis_axisymmetric_pose_checks(
        self,
        *,
        snapshot: CADStateSnapshot,
        requirement_text: str | None,
    ) -> list[RequirementCheck]:
        axis_spec = self._extract_named_axis_axisymmetric_pose_spec(requirement_text)
        if axis_spec is None:
            return []
        axis_name, axis_index = axis_spec
        geometry = snapshot.geometry
        bbox_min = geometry.bbox_min if isinstance(geometry.bbox_min, list) else None
        bbox_max = geometry.bbox_max if isinstance(geometry.bbox_max, list) else None
        center_of_mass = (
            geometry.center_of_mass if isinstance(geometry.center_of_mass, list) else None
        )
        if not (
            isinstance(bbox_min, list)
            and isinstance(bbox_max, list)
            and len(bbox_min) >= 3
            and len(bbox_max) >= 3
        ):
            return []

        perpendicular_indices = [idx for idx in range(3) if idx != axis_index]
        radial_span = max(
            float(bbox_max[idx]) - float(bbox_min[idx]) for idx in perpendicular_indices
        )
        tolerance = max(1.0, abs(radial_span) * 0.08)
        bbox_center_offsets = [
            abs((float(bbox_min[idx]) + float(bbox_max[idx])) / 2.0)
            for idx in perpendicular_indices
        ]
        bbox_axis_centered = all(offset <= tolerance for offset in bbox_center_offsets)

        center_of_mass_offsets: list[float] = []
        if isinstance(center_of_mass, list) and len(center_of_mass) >= 3:
            center_of_mass_offsets = [
                abs(float(center_of_mass[idx])) for idx in perpendicular_indices
            ]
        center_of_mass_axis_centered = (
            True
            if not center_of_mass_offsets
            else all(offset <= tolerance for offset in center_of_mass_offsets)
        )

        rotational_faces = []
        if snapshot.geometry_objects is not None:
            rotational_faces = [
                face
                for face in (snapshot.geometry_objects.faces or [])
                if str(getattr(face, "geom_type", "")).strip().upper()
                in {"CYLINDER", "CONE", "TORUS"}
                and self._face_axis_matches_index(face, axis_index)
            ]
        axis_origin_offsets: list[float] = []
        for face in rotational_faces:
            axis_origin = getattr(face, "axis_origin", None)
            if (
                isinstance(axis_origin, list)
                and len(axis_origin) >= 3
                and all(isinstance(item, (int, float)) for item in axis_origin[:3])
            ):
                axis_origin_offsets.append(
                    max(abs(float(axis_origin[idx])) for idx in perpendicular_indices)
                )
        rotational_axis_centered = (
            True
            if not axis_origin_offsets
            else all(offset <= tolerance for offset in axis_origin_offsets)
        )

        matches_axis_pose = (
            bbox_axis_centered
            and center_of_mass_axis_centered
            and rotational_axis_centered
        )
        return [
            RequirementCheck(
                check_id="feature_named_axis_axisymmetric_pose",
                label="Explicit named-axis revolve / axisymmetric parts stay centered on the declared global axis",
                status=(
                    RequirementCheckStatus.PASS
                    if matches_axis_pose
                    else RequirementCheckStatus.FAIL
                ),
                blocking=True,
                evidence=(
                    f"axis={axis_name}, tolerance={tolerance}, bbox_center_offsets={bbox_center_offsets}, "
                    f"center_of_mass_offsets={center_of_mass_offsets or ['unavailable']}, "
                    f"rotational_face_count={len(rotational_faces)}, axis_origin_offsets={axis_origin_offsets or ['unavailable']}"
                ),
            )
        ]

    def _build_cylindrical_slot_alignment_checks(
        self,
        *,
        snapshot: CADStateSnapshot,
        requirement_text: str | None,
    ) -> list[RequirementCheck]:
        specs = self._extract_cylindrical_slot_specs(requirement_text)
        if not specs:
            return []
        solid_bbox = self._snapshot_primary_solid_bbox(snapshot)
        if solid_bbox is None:
            return []
        cylindrical_faces = [
            face
            for face in self._snapshot_feature_faces(snapshot)
            if str(getattr(face, "geom_type", "")).strip().upper() == "CYLINDER"
        ]
        if not cylindrical_faces:
            return [
                RequirementCheck(
                    check_id="feature_cylindrical_slot_alignment",
                    label="Explicit cylindrical slot cuts stay centered on the requested centerline and span the host length",
                    status=RequirementCheckStatus.FAIL,
                    blocking=True,
                    evidence="no cylindrical faces available for slot-span validation",
                )
            ]

        checks: list[RequirementCheck] = []
        for spec in specs:
            axis_name = str(spec["axis"])
            axis_index = int(spec["axis_index"])
            expected_radius = float(spec["radius"])
            centerline = list(spec["centerline"])
            solid_axis_min, solid_axis_max = self._bbox_axis_bounds(solid_bbox, axis_index)
            solid_axis_span = solid_axis_max - solid_axis_min
            if solid_axis_span <= 1e-6:
                continue

            radius_tolerance = max(1.0, abs(expected_radius) * 0.12)
            centerline_tolerance = max(1.0, abs(expected_radius) * 0.2)
            span_tolerance = max(1.0, abs(solid_axis_span) * 0.08)
            midpoint_tolerance = max(1.0, abs(solid_axis_span) * 0.08)

            matched_faces: list[dict[str, Any]] = []
            best_evidence = "no cylindrical face matched the requested slot axis/span"
            best_score = -1
            for face in cylindrical_faces:
                if not self._face_axis_matches_index(face, axis_index):
                    continue
                bbox = getattr(face, "bbox", None)
                if bbox is None:
                    continue
                face_axis_min, face_axis_max = self._bbox_axis_bounds(bbox, axis_index)
                face_axis_span = face_axis_max - face_axis_min
                face_axis_midpoint = (face_axis_min + face_axis_max) / 2.0
                face_radius = getattr(face, "radius", None)
                radius_ok = not isinstance(face_radius, (int, float)) or (
                    abs(float(face_radius) - expected_radius) <= radius_tolerance
                )
                reference_point = self._snapshot_infer_cylindrical_slot_reference_point(
                    face=face,
                    axis_index=axis_index,
                    solid_bbox=solid_bbox,
                    radius_value=face_radius if isinstance(face_radius, (int, float)) else expected_radius,
                    requirement_text=requirement_text,
                )
                cross_offsets: list[float] = []
                if reference_point is not None:
                    cross_offsets = [
                        abs(float(reference_point[idx]) - float(centerline[idx]))
                        for idx in range(3)
                        if idx != axis_index
                    ]
                cross_ok = (
                    True
                    if not cross_offsets
                    else all(offset <= centerline_tolerance for offset in cross_offsets)
                )
                span_ok = face_axis_span >= solid_axis_span - span_tolerance
                midpoint_ok = (
                    abs(face_axis_midpoint - float(centerline[axis_index]))
                    <= midpoint_tolerance
                )
                score = sum(
                    1
                    for item in (radius_ok, cross_ok, span_ok, midpoint_ok)
                    if item
                )
                surface_center = self._snapshot_feature_surface_center_point(face)
                evidence = (
                    f"axis={axis_name}, expected_radius={expected_radius}, "
                    f"observed_radius={face_radius if isinstance(face_radius, (int, float)) else 'unavailable'}, "
                    f"expected_centerline={centerline}, observed_reference_point={reference_point or 'unavailable'}, "
                    f"solid_axis_range=[{solid_axis_min}, {solid_axis_max}], "
                    f"observed_axis_range=[{face_axis_min}, {face_axis_max}], "
                    f"observed_axis_midpoint={round(face_axis_midpoint, 6)}, "
                    f"cross_offsets={cross_offsets or ['unavailable']}, "
                    f"surface_center={surface_center or 'unavailable'}"
                )
                if score > best_score:
                    best_score = score
                    best_evidence = evidence
                if radius_ok and cross_ok and span_ok and midpoint_ok:
                    matched_faces.append(
                        {
                            "evidence": evidence,
                            "surface_center": surface_center,
                        }
                    )

            matched = bool(matched_faces)
            if matched:
                if len(matched_faces) > 2:
                    perpendicular_indices = [
                        idx for idx in range(3) if idx != axis_index
                    ]
                    usable_surface_centers = [
                        item["surface_center"]
                        for item in matched_faces
                        if isinstance(item.get("surface_center"), list)
                        and len(item["surface_center"]) >= 3
                    ]
                    side_axis_name = "unavailable"
                    side_bucket_counts = {
                        "negative": 0,
                        "center": 0,
                        "positive": 0,
                    }
                    if usable_surface_centers:
                        side_axis_index = max(
                            perpendicular_indices,
                            key=lambda idx: max(
                                abs(float(center[idx]) - float(centerline[idx]))
                                for center in usable_surface_centers
                            ),
                        )
                        side_axis_name = "XYZ"[side_axis_index]
                        for center in usable_surface_centers:
                            delta = float(center[side_axis_index]) - float(
                                centerline[side_axis_index]
                            )
                            if delta < -centerline_tolerance:
                                side_bucket_counts["negative"] += 1
                            elif delta > centerline_tolerance:
                                side_bucket_counts["positive"] += 1
                            else:
                                side_bucket_counts["center"] += 1
                    best_evidence = (
                        f"axis={axis_name}, expected_radius={expected_radius}, "
                        f"expected_centerline={centerline}, matched_face_count={len(matched_faces)}, "
                        f"side_axis={side_axis_name}, side_bucket_counts={side_bucket_counts}, "
                        f"surface_centers={usable_surface_centers or ['unavailable']}, "
                        "fragmented_cylindrical_wall_faces=true"
                    )
                    matched = False
                else:
                    best_evidence = str(matched_faces[0]["evidence"])
                    if len(matched_faces) > 1:
                        best_evidence += f", matched_face_count={len(matched_faces)}"

            checks.append(
                RequirementCheck(
                    check_id="feature_cylindrical_slot_alignment",
                    label="Explicit cylindrical slot cuts stay centered on the requested centerline and span the host length",
                    status=(
                        RequirementCheckStatus.PASS
                        if matched
                        else RequirementCheckStatus.FAIL
                    ),
                    blocking=True,
                    evidence=best_evidence,
                )
            )
        return checks

    def _extract_named_plane_positive_extrude_specs(
        self,
        requirement_text: str | None,
    ) -> list[dict[str, float | str | bool]]:
        if not isinstance(requirement_text, str) or not requirement_text.strip():
            return []
        normalized = " ".join(requirement_text.strip().lower().split())
        plane_pattern = re.compile(
            r"\b(?:select|draw(?:ing)?|on|in)\s+the\s+(?P<plane>xy|xz|yz)\s+plane\b",
            re.IGNORECASE,
        )
        distance_pattern = re.compile(
            r"\bextrud(?:e|ed|ing)?\b[^0-9]{0,24}(?P<distance>[0-9]+(?:\.[0-9]+)?)",
            re.IGNORECASE | re.DOTALL,
        )
        axis_pattern = re.compile(
            r"\balong\s+(?:the\s+)?(?:(?P<sign>positive|negative)\s+)?(?P<axis>[xyz])(?:\s*[- ]?\s*axis)\b",
            re.IGNORECASE,
        )
        plane_matches = list(plane_pattern.finditer(normalized))
        specs: list[dict[str, float | str | bool]] = []
        seen: set[tuple[str, float, bool]] = set()
        expected_axis_map = {"XY": "Z", "XZ": "Y", "YZ": "X"}
        for index, plane_match in enumerate(plane_matches):
            plane = str(plane_match.group("plane")).upper()
            start = plane_match.end()
            end = (
                plane_matches[index + 1].start()
                if index + 1 < len(plane_matches)
                else len(normalized)
            )
            segment = normalized[start:end]
            if any(
                token in segment
                for token in (
                    "symmetr",
                    "midplane",
                    "about the plane",
                    "about the xy plane",
                    "about the xz plane",
                    "about the yz plane",
                )
            ):
                continue
            distance_match = distance_pattern.search(segment)
            if distance_match is None:
                continue
            try:
                distance = float(distance_match.group("distance"))
            except Exception:
                continue
            if distance <= 0.0:
                continue
            sentence_end_candidates = [
                index
                for index in (
                    segment.find(".", distance_match.end()),
                    segment.find(";", distance_match.end()),
                )
                if index != -1
            ]
            axis_window_end = min(sentence_end_candidates) if sentence_end_candidates else len(segment)
            axis_match = axis_pattern.search(segment[distance_match.start():axis_window_end])
            require_positive_direction = True
            if axis_match is not None:
                sign = str(axis_match.group("sign") or "").strip().lower()
                axis_name = str(axis_match.group("axis") or "").strip().upper()
                if axis_name and axis_name != expected_axis_map.get(plane):
                    continue
                if sign == "negative":
                    continue
                if sign != "positive":
                    require_positive_direction = False
            key = (plane, distance, require_positive_direction)
            if key in seen:
                continue
            seen.add(key)
            specs.append(
                {
                    "plane": plane,
                    "distance": distance,
                    "require_positive_direction": require_positive_direction,
                }
            )
        return specs

    def _extract_named_axis_axisymmetric_pose_spec(
        self,
        requirement_text: str | None,
    ) -> tuple[str, int] | None:
        if not isinstance(requirement_text, str) or not requirement_text.strip():
            return None
        normalized = " ".join(requirement_text.strip().lower().split())
        if not any(
            token in normalized
            for token in ("revolve", "revolution", "rotational", "axisymmetric")
        ):
            return None
        match = re.search(
            r"(?:around|about)\s+(?:the\s+)?(?P<axis>[xyz])(?:\s*[- ]?\s*axis)\b",
            normalized,
            re.IGNORECASE,
        )
        if match is None:
            return None
        axis_name = str(match.group("axis")).upper()
        axis_index_map = {"X": 0, "Y": 1, "Z": 2}
        return axis_name, axis_index_map[axis_name]

    def _extract_cylindrical_slot_specs(
        self,
        requirement_text: str | None,
    ) -> list[dict[str, Any]]:
        if not isinstance(requirement_text, str) or not requirement_text.strip():
            return []
        normalized = " ".join(requirement_text.strip().lower().split())
        if not any(
            token in normalized
            for token in ("semicircular slot", "cylindrical slot")
        ):
            return []
        if "cutting cylinder" not in normalized and "cylinder centerline" not in normalized:
            return []
        axis_match = re.search(
            r"axis along\s+(?:the\s+)?(?P<axis>[xyz])(?:\s*[- ]?\s*axis)?\b",
            normalized,
            re.IGNORECASE,
        )
        centerline_match = re.search(
            r"centerline placed at\s*\(\s*(?P<x>-?[0-9]+(?:\.[0-9]+)?)\s*,\s*(?P<y>-?[0-9]+(?:\.[0-9]+)?)\s*,\s*(?P<z>-?[0-9]+(?:\.[0-9]+)?)\s*\)",
            normalized,
            re.IGNORECASE,
        )
        radius_match = re.search(
            r"radius(?:\s*(?:of|=|:))?\s*(?P<radius>[0-9]+(?:\.[0-9]+)?)",
            normalized,
            re.IGNORECASE,
        )
        length_match = re.search(
            r"length(?:\s*(?:set to|of|=|:))?\s*(?P<length>[0-9]+(?:\.[0-9]+)?)",
            normalized,
            re.IGNORECASE,
        )
        if (
            axis_match is None
            or centerline_match is None
            or radius_match is None
            or length_match is None
        ):
            return []
        axis_name = str(axis_match.group("axis")).upper()
        axis_index_map = {"X": 0, "Y": 1, "Z": 2}
        axis_index = axis_index_map[axis_name]
        centerline = [
            self._as_float(centerline_match.group("x")),
            self._as_float(centerline_match.group("y")),
            self._as_float(centerline_match.group("z")),
        ]
        radius = self._as_float(radius_match.group("radius"))
        length = self._as_float(length_match.group("length"))
        if radius <= 0.0 or length <= 0.0:
            return []
        return [
            {
                "axis": axis_name,
                "axis_index": axis_index,
                "centerline": centerline,
                "radius": radius,
                "length": length,
            }
        ]

    def _history_matches_multi_plane_additive_specs(
        self,
        history: list[ActionHistoryEntry],
        plane_specs: list[dict[str, float | str]],
        semantics: RequirementSemantics | None = None,
    ) -> bool:
        if not plane_specs:
            return True
        observed: list[dict[str, float | str]] = []
        for index, entry in enumerate(history):
            if entry.action_type != CADActionType.EXTRUDE:
                continue
            if not self._history_action_materially_changes_geometry(history, index):
                continue
            sketch_index = self._find_preceding_sketch_index(history, index)
            if sketch_index is None:
                continue
            plane = self._sketch_plane_name_from_entry(history[sketch_index])
            if plane not in {"XY", "XZ", "YZ"}:
                continue
            rectangle_entry = None
            for candidate in history[sketch_index + 1 : index]:
                if candidate.action_type == CADActionType.ADD_RECTANGLE:
                    rectangle_entry = candidate
                    break
            if rectangle_entry is None:
                continue
            width = rectangle_entry.action_params.get("width")
            height = rectangle_entry.action_params.get("height")
            distance = entry.action_params.get("distance")
            if not all(
                isinstance(value, (int, float))
                for value in (width, height, distance)
            ):
                continue
            observed.append(
                {
                    "plane": plane,
                    "width": float(width),
                    "height": float(height),
                    "distance": float(distance)
                    * (2.0 if bool(entry.action_params.get("both_sides")) else 1.0),
                    "signature": self._build_plane_extrude_signature(
                        plane=plane,
                        width=float(width),
                        height=float(height),
                        extrusion_span=float(distance)
                        * (2.0 if bool(entry.action_params.get("both_sides")) else 1.0),
                    ),
                }
            )
        if not observed:
            return False

        def _matches(
            expected: dict[str, float | str],
            candidate: dict[str, float | str],
        ) -> bool:
            if expected.get("plane") != candidate.get("plane"):
                return False
            plane = str(expected.get("plane", "")).upper()
            for key in ("width", "height", "distance"):
                expected_value = expected.get(key)
                candidate_value = candidate.get(key)
                if not isinstance(expected_value, (int, float)) or not isinstance(
                    candidate_value, (int, float)
                ):
                    return False
                tolerance = max(1.0, abs(float(expected_value)) * 0.08)
                if abs(float(expected_value) - float(candidate_value)) > tolerance:
                    break
            else:
                return True

            target_options = self._target_signature_options_for_multi_plane_validator(
                plane=plane,
                semantics=semantics,
            )
            candidate_signature = candidate.get("signature")
            if (
                target_options
                and isinstance(candidate_signature, tuple)
                and candidate_signature in target_options
            ):
                expected_distance = expected.get("distance")
                candidate_distance = candidate.get("distance")
                if isinstance(expected_distance, (int, float)) and isinstance(
                    candidate_distance, (int, float)
                ):
                    tolerance = max(1.0, abs(float(expected_distance)) * 0.08)
                    return (
                        abs(float(expected_distance) - float(candidate_distance))
                        <= tolerance
                    )
            return False

        return all(any(_matches(spec, item) for item in observed) for spec in plane_specs)

    def _snapshot_matches_multi_plane_additive_specs(
        self,
        snapshot: CADStateSnapshot,
        plane_specs: list[dict[str, float | str]],
    ) -> bool:
        if int(snapshot.geometry.solids) != 1 or not plane_specs:
            return False
        bbox = snapshot.geometry.bbox
        volume = snapshot.geometry.volume
        if (
            not isinstance(bbox, list)
            or len(bbox) != 3
            or not all(isinstance(value, (int, float)) for value in bbox)
            or not isinstance(volume, (int, float))
            or float(volume) <= 1e-6
        ):
            return False
        signatures: list[tuple[float, float, float]] = []
        for spec in plane_specs:
            plane = spec.get("plane")
            width = spec.get("width")
            height = spec.get("height")
            distance = spec.get("distance")
            if not isinstance(plane, str) or not all(
                isinstance(value, (int, float))
                for value in (width, height, distance)
            ):
                return False
            normalized_plane = self._normalize_sketch_plane_name(plane)
            if normalized_plane not in {"XY", "XZ", "YZ"}:
                return False
            signatures.append(
                self._build_plane_extrude_signature(
                    plane=normalized_plane,
                    width=float(width),
                    height=float(height),
                    extrusion_span=float(distance),
                )
            )
        if len(signatures) < 2:
            return False
        expected_bbox = [
            max(signature[index] for signature in signatures)
            for index in range(3)
        ]
        for expected, observed in zip(expected_bbox, bbox):
            tolerance = max(1.0, abs(float(expected)) * 0.08)
            if abs(float(expected) - float(observed)) > tolerance:
                return False
        expected_volume = self._axis_aligned_centered_union_volume(signatures)
        tolerance = max(1.0, abs(float(expected_volume)) * 0.08)
        if abs(float(expected_volume) - float(volume)) > tolerance:
            return False
        face_count = int(snapshot.geometry.faces or 0)
        return face_count >= 8

    def _axis_aligned_centered_union_volume(
        self,
        signatures: list[tuple[float, float, float]],
    ) -> float:
        from itertools import combinations

        total = 0.0
        for subset_size in range(1, len(signatures) + 1):
            sign = 1.0 if subset_size % 2 == 1 else -1.0
            for subset in combinations(signatures, subset_size):
                intersection = [
                    min(float(signature[index]) for signature in subset)
                    for index in range(3)
                ]
                total += sign * intersection[0] * intersection[1] * intersection[2]
        return total

    def _target_signature_options_for_multi_plane_validator(
        self,
        plane: str,
        semantics: RequirementSemantics | None,
    ) -> tuple[tuple[float, float, float], ...]:
        if semantics is None:
            return ()
        normalized_plane = self._normalize_sketch_plane_name(plane)
        datum_planes = getattr(semantics, "datum_planes", ())
        signature_groups = getattr(
            semantics, "multi_plane_additive_signature_options", ()
        )
        if not isinstance(datum_planes, tuple) or not isinstance(signature_groups, tuple):
            return ()
        for datum_plane, options in zip(datum_planes, signature_groups):
            if not isinstance(datum_plane, str) or not isinstance(options, tuple):
                continue
            if self._normalize_sketch_plane_name(datum_plane) != normalized_plane:
                continue
            return tuple(
                option
                for option in options
                if isinstance(option, tuple) and len(option) == 3
            )
        return ()

    def _edges_expression(
        self,
        params: dict[str, CADParamValue],
    ) -> str:
        edge_scope = params.get("edge_scope")
        if isinstance(edge_scope, str):
            normalized_scope = edge_scope.strip().lower()
            if normalized_scope in {"all", "all_outer", "outer", "all_edges"}:
                return "result.edges()"
            if normalized_scope in {"top", "top_edges"}:
                return "result.edges('>Z')"
            if normalized_scope in {"bottom", "bottom_edges"}:
                return "result.edges('<Z')"
            if normalized_scope in {"vertical", "vertical_edges"}:
                return "result.edges('|Z')"

        edges_selector = params.get("edges_selector")
        if isinstance(edges_selector, str):
            normalized_selector = edges_selector.strip()
            if normalized_selector.lower() in {"all", "all_outer", "outer"}:
                return "result.edges()"
            if self._is_safe_cq_selector(normalized_selector):
                return f"result.edges({normalized_selector!r})"

        return "result.edges()"

    def _is_safe_cq_selector(self, selector: str) -> bool:
        return (
            len(selector) <= 32
            and re.fullmatch(r"[<>|XYZxyz+\-#%&*()]+", selector) is not None
        )

    def _parse_snapshot(self, result: SandboxResult) -> CADStateSnapshot:
        """Parse CAD state snapshot from sandbox result."""
        # Default snapshot values
        step = 1
        features = []
        geometry = GeometryInfo(
            solids=0,
            faces=0,
            edges=0,
            volume=0.0,
            bbox=[0.0, 0.0, 0.0],
            center_of_mass=[0.0, 0.0, 0.0],
            surface_area=0.0,
            bbox_min=[0.0, 0.0, 0.0],
            bbox_max=[0.0, 0.0, 0.0],
        )
        geometry_objects: GeometryObjectIndex | None = None
        topology_index: TopologyObjectIndex | None = None
        issues = []
        warnings: list[str] = []
        blockers: list[str] = []
        images = []
        success = result.success
        error = result.error_message if not success else None

        # Try to parse geometry info from JSON file
        if result.success and "geometry_info.json" in result.output_file_contents:
            try:
                geo_json = json.loads(
                    result.output_file_contents["geometry_info.json"].decode("utf-8")
                )
                geometry = GeometryInfo(
                    solids=geo_json.get("solids", 0),
                    faces=geo_json.get("faces", 0),
                    edges=geo_json.get("edges", 0),
                    volume=geo_json.get("volume", 0.0),
                    bbox=geo_json.get("bbox", [0.0, 0.0, 0.0]),
                    center_of_mass=geo_json.get("center", [0.0, 0.0, 0.0]),
                    surface_area=geo_json.get("surface_area", 0.0),
                    bbox_min=geo_json.get("bbox_min", [0.0, 0.0, 0.0]),
                    bbox_max=geo_json.get("bbox_max", [0.0, 0.0, 0.0]),
                )
                geometry_objects = self._parse_geometry_object_index(geo_json)
                topology_index = self._parse_topology_object_index(
                    geo_json, step=step
                )
            except Exception as e:
                # If parsing fails, use defaults
                issues.append(f"Failed to parse geometry info: {e}")

        warnings, blockers, diagnostic_issues = self._classify_execution_diagnostics(
            result=result,
            geometry=geometry,
        )
        issues.extend(diagnostic_issues)

        # Count preview images
        if result.success:
            images = [f for f in result.output_files if f.endswith(".png")]
            # Feature detection based on geometry
            if geometry.solids > 0:
                features = ["Extruded solid"]
            else:
                features = ["Sketch only"]

        return CADStateSnapshot(
            step=step,
            features=features,
            geometry=geometry,
            issues=issues,
            warnings=warnings,
            blockers=blockers,
            images=images,
            sketch_state=None,
            geometry_objects=geometry_objects,
            topology_index=topology_index,
            success=success,
            error=error,
        )

    def _classify_execution_diagnostics(
        self,
        result: SandboxResult,
        geometry: GeometryInfo,
    ) -> tuple[list[str], list[str], list[str]]:
        warnings: list[str] = []
        blockers: list[str] = []
        issues: list[str] = []
        combined = f"{result.stderr}\n{result.stdout}\n{result.error_message or ''}".lower()

        if "disconnectedwire" in combined or "brepbuilderapi_disconnectedwire" in combined:
            warnings.append("execution_warning_disconnected_wire")
            blockers.extend(["path_disconnected", "feature_path_sweep_rail"])
            issues.append("DisconnectedWire detected during execution")
        if "closed profile sketch" in combined or "closed face profile" in combined:
            warnings.append("execution_warning_missing_closed_profile")
            blockers.extend(["missing_profile", "feature_path_sweep_profile"])
            issues.append("Closed sweep profile was not available")
        if "captured path sketch" in combined or "requires a captured path sketch" in combined:
            warnings.append("execution_warning_missing_path")
            blockers.append("feature_path_sweep_rail")
            issues.append("Sweep path was not available")
        if (
            "pattern_circular requires a preceding additive feature" in combined
            or "pattern_linear requires a preceding additive feature" in combined
        ):
            warnings.append("execution_warning_missing_pattern_seed")
            blockers.append("feature_pattern_seed")
            issues.append("Pattern action requires a preceding additive seed feature")
        if result.success and geometry.solids <= 0 and geometry.volume <= 1e-6:
            warnings.append("execution_warning_no_positive_solid")
        if "no renderable geometry" in combined:
            warnings.append("execution_warning_no_renderable_geometry")

        return (
            self._normalize_string_list(warnings, limit=32),
            self._normalize_string_list(blockers, limit=32),
            self._normalize_string_list(issues, limit=32),
        )

    def _parse_geometry_object_index(
        self,
        geo_json: dict[str, Any],
    ) -> GeometryObjectIndex | None:
        solids_raw = geo_json.get("solids_detail")
        faces_raw = geo_json.get("faces_detail")
        edges_raw = geo_json.get("edges_detail")

        if not any(isinstance(raw, list) for raw in (solids_raw, faces_raw, edges_raw)):
            return None

        solids = self._parse_solid_entities(solids_raw)
        faces = self._parse_face_entities(faces_raw)
        edges = self._parse_edge_entities(edges_raw)
        try:
            return GeometryObjectIndex(
                solids=solids,
                faces=faces,
                edges=edges,
                solids_truncated=bool(geo_json.get("solids_truncated", False)),
                faces_truncated=bool(geo_json.get("faces_truncated", False)),
                edges_truncated=bool(geo_json.get("edges_truncated", False)),
                max_items_per_type=int(geo_json.get("entities_limit", 0) or 0),
            )
        except Exception:
            return None

    def _parse_topology_object_index(
        self,
        geo_json: dict[str, Any],
        step: int,
    ) -> TopologyObjectIndex | None:
        faces_raw = geo_json.get("topology_faces_detail")
        edges_raw = geo_json.get("topology_edges_detail")
        if not any(isinstance(raw, list) for raw in (faces_raw, edges_raw)):
            return None

        faces = self._parse_topology_face_entities(raw_items=faces_raw, step=step)
        edges = self._parse_topology_edge_entities(raw_items=edges_raw, step=step)
        try:
            return TopologyObjectIndex(
                faces=faces,
                edges=edges,
                faces_truncated=bool(geo_json.get("faces_truncated", False)),
                edges_truncated=bool(geo_json.get("edges_truncated", False)),
                max_items_per_type=int(geo_json.get("entities_limit", 0) or 0),
                faces_total=len(faces),
                edges_total=len(edges),
            )
        except Exception:
            return None

    def _parse_solid_entities(self, raw_items: Any) -> list[SolidEntity]:
        if not isinstance(raw_items, list):
            return []
        parsed: list[SolidEntity] = []
        for item in raw_items:
            if not isinstance(item, dict):
                continue
            bbox = self._parse_bbox(item.get("bbox"))
            if bbox is None:
                continue
            parsed.append(
                SolidEntity(
                    solid_id=str(item.get("solid_id", f"S{len(parsed) + 1}")),
                    volume=self._as_float(item.get("volume")),
                    surface_area=self._as_float(item.get("surface_area")),
                    center_of_mass=self._parse_vector3(
                        item.get("center_of_mass"),
                        fallback=[0.0, 0.0, 0.0],
                    ),
                    bbox=bbox,
                )
            )
        return parsed

    def _parse_face_entities(self, raw_items: Any) -> list[FaceEntity]:
        if not isinstance(raw_items, list):
            return []
        parsed: list[FaceEntity] = []
        for item in raw_items:
            if not isinstance(item, dict):
                continue
            bbox = self._parse_bbox(item.get("bbox"))
            if bbox is None:
                continue
            normal_value = item.get("normal")
            normal = (
                self._parse_vector3(normal_value, fallback=[0.0, 0.0, 0.0])
                if isinstance(normal_value, list)
                else None
            )
            parsed.append(
                FaceEntity(
                    face_id=str(item.get("face_id", f"F{len(parsed) + 1}")),
                    area=self._as_float(item.get("area")),
                    center=self._parse_vector3(
                        item.get("center"), fallback=[0.0, 0.0, 0.0]
                    ),
                    normal=normal,
                    axis_origin=(
                        self._parse_vector3(item.get("axis_origin"), fallback=[0.0, 0.0, 0.0])
                        if isinstance(item.get("axis_origin"), list)
                        else None
                    ),
                    axis_direction=(
                        self._parse_vector3(item.get("axis_direction"), fallback=[0.0, 0.0, 1.0])
                        if isinstance(item.get("axis_direction"), list)
                        else None
                    ),
                    radius=(
                        self._as_float(item.get("radius"))
                        if isinstance(item.get("radius"), (int, float))
                        else None
                    ),
                    geom_type=str(item.get("geom_type", "unknown")),
                    bbox=bbox,
                )
            )
        return parsed

    def _parse_edge_entities(self, raw_items: Any) -> list[EdgeEntity]:
        if not isinstance(raw_items, list):
            return []
        parsed: list[EdgeEntity] = []
        for item in raw_items:
            if not isinstance(item, dict):
                continue
            bbox = self._parse_bbox(item.get("bbox"))
            if bbox is None:
                continue
            center_value = item.get("center")
            center = (
                self._parse_vector3(center_value, fallback=[0.0, 0.0, 0.0])
                if isinstance(center_value, list)
                else None
            )
            parsed.append(
                EdgeEntity(
                    edge_id=str(item.get("edge_id", f"E{len(parsed) + 1}")),
                    length=self._as_float(item.get("length")),
                    geom_type=str(item.get("geom_type", "unknown")),
                    center=center,
                    axis_origin=(
                        self._parse_vector3(item.get("axis_origin"), fallback=[0.0, 0.0, 0.0])
                        if isinstance(item.get("axis_origin"), list)
                        else None
                    ),
                    axis_direction=(
                        self._parse_vector3(item.get("axis_direction"), fallback=[0.0, 0.0, 1.0])
                        if isinstance(item.get("axis_direction"), list)
                        else None
                    ),
                    radius=(
                        self._as_float(item.get("radius"))
                        if isinstance(item.get("radius"), (int, float))
                        else None
                    ),
                    bbox=bbox,
                )
            )
        return parsed

    def _parse_topology_face_entities(
        self,
        raw_items: Any,
        step: int,
    ) -> list[TopologyFaceEntity]:
        if not isinstance(raw_items, list):
            return []
        parsed: list[TopologyFaceEntity] = []
        for item in raw_items:
            if not isinstance(item, dict):
                continue
            bbox = self._parse_bbox(item.get("bbox"))
            if bbox is None:
                continue
            normal_value = item.get("normal")
            normal = (
                self._parse_vector3(normal_value, fallback=[0.0, 0.0, 0.0])
                if isinstance(normal_value, list)
                else None
            )
            face_id = str(item.get("face_id", f"F_{len(parsed) + 1}"))
            parsed.append(
                TopologyFaceEntity(
                    face_ref=self._format_topology_ref("face", step, face_id),
                    face_id=face_id,
                    step=step,
                    area=self._as_float(item.get("area")),
                    center=self._parse_vector3(
                        item.get("center"), fallback=[0.0, 0.0, 0.0]
                    ),
                    normal=normal,
                    axis_origin=(
                        self._parse_vector3(item.get("axis_origin"), fallback=[0.0, 0.0, 0.0])
                        if isinstance(item.get("axis_origin"), list)
                        else None
                    ),
                    axis_direction=(
                        self._parse_vector3(item.get("axis_direction"), fallback=[0.0, 0.0, 1.0])
                        if isinstance(item.get("axis_direction"), list)
                        else None
                    ),
                    radius=(
                        self._as_float(item.get("radius"))
                        if isinstance(item.get("radius"), (int, float))
                        else None
                    ),
                    geom_type=str(item.get("geom_type", "unknown")),
                    bbox=bbox,
                    parent_solid_id=(
                        str(item.get("parent_solid_id"))
                        if item.get("parent_solid_id") is not None
                        else None
                    ),
                    edge_refs=self._normalize_string_list(
                        [
                            self._format_topology_ref("edge", step, edge_id)
                            for edge_id in item.get("edge_ids", [])
                            if isinstance(edge_id, str)
                        ],
                        limit=256,
                    ),
                    adjacent_face_refs=self._normalize_string_list(
                        [
                            self._format_topology_ref("face", step, face_id_ref)
                            for face_id_ref in item.get("adjacent_face_ids", [])
                            if isinstance(face_id_ref, str)
                        ],
                        limit=256,
                    ),
                )
            )
        return parsed

    def _parse_topology_edge_entities(
        self,
        raw_items: Any,
        step: int,
    ) -> list[TopologyEdgeEntity]:
        if not isinstance(raw_items, list):
            return []
        parsed: list[TopologyEdgeEntity] = []
        for item in raw_items:
            if not isinstance(item, dict):
                continue
            bbox = self._parse_bbox(item.get("bbox"))
            if bbox is None:
                continue
            center_value = item.get("center")
            center = (
                self._parse_vector3(center_value, fallback=[0.0, 0.0, 0.0])
                if isinstance(center_value, list)
                else None
            )
            edge_id = str(item.get("edge_id", f"E_{len(parsed) + 1}"))
            parsed.append(
                TopologyEdgeEntity(
                    edge_ref=self._format_topology_ref("edge", step, edge_id),
                    edge_id=edge_id,
                    step=step,
                    length=self._as_float(item.get("length")),
                    geom_type=str(item.get("geom_type", "unknown")),
                    center=center,
                    axis_origin=(
                        self._parse_vector3(item.get("axis_origin"), fallback=[0.0, 0.0, 0.0])
                        if isinstance(item.get("axis_origin"), list)
                        else None
                    ),
                    axis_direction=(
                        self._parse_vector3(item.get("axis_direction"), fallback=[0.0, 0.0, 1.0])
                        if isinstance(item.get("axis_direction"), list)
                        else None
                    ),
                    radius=(
                        self._as_float(item.get("radius"))
                        if isinstance(item.get("radius"), (int, float))
                        else None
                    ),
                    bbox=bbox,
                    parent_solid_id=(
                        str(item.get("parent_solid_id"))
                        if item.get("parent_solid_id") is not None
                        else None
                    ),
                    adjacent_face_refs=self._normalize_string_list(
                        [
                            self._format_topology_ref("face", step, face_id)
                            for face_id in item.get("adjacent_face_ids", [])
                            if isinstance(face_id, str)
                        ],
                        limit=256,
                    ),
                )
            )
        return parsed

    def _parse_bbox(self, raw_bbox: Any) -> BoundingBox3D | None:
        if not isinstance(raw_bbox, dict):
            return None
        try:
            return BoundingBox3D(
                xlen=self._as_float(raw_bbox.get("xlen")),
                ylen=self._as_float(raw_bbox.get("ylen")),
                zlen=self._as_float(raw_bbox.get("zlen")),
                xmin=self._as_float(raw_bbox.get("xmin")),
                xmax=self._as_float(raw_bbox.get("xmax")),
                ymin=self._as_float(raw_bbox.get("ymin")),
                ymax=self._as_float(raw_bbox.get("ymax")),
                zmin=self._as_float(raw_bbox.get("zmin")),
                zmax=self._as_float(raw_bbox.get("zmax")),
            )
        except Exception:
            return None

    def _parse_vector3(self, value: Any, fallback: list[float]) -> list[float]:
        if not isinstance(value, list) or len(value) < 3:
            return fallback
        return [
            self._as_float(value[0], fallback=fallback[0]),
            self._as_float(value[1], fallback=fallback[1]),
            self._as_float(value[2], fallback=fallback[2]),
        ]

    def _as_float(self, value: Any, fallback: float = 0.0) -> float:
        try:
            return float(value)
        except Exception:
            return fallback

    def _normalize_string_list(self, value: Any, limit: int) -> list[str]:
        if not isinstance(value, list):
            return []
        normalized = [
            item.strip() for item in value if isinstance(item, str) and item.strip()
        ]
        return normalized[:limit]

    def _format_topology_ref(self, kind: str, step: int, entity_id: str) -> str:
        normalized_kind = "face" if kind == "face" else "edge"
        return f"{normalized_kind}:{int(step)}:{entity_id}"

    def _retag_topology_index_step(
        self,
        topology_index: TopologyObjectIndex | None,
        step: int,
    ) -> TopologyObjectIndex | None:
        if topology_index is None:
            return None
        faces = [
            face.model_copy(
                update={
                    "step": step,
                    "face_ref": self._format_topology_ref("face", step, face.face_id),
                    "edge_refs": [
                        self._format_topology_ref(
                            "edge",
                            step,
                            parse_topology_ref(edge_ref).get("entity_id", edge_ref)
                            if parse_topology_ref(edge_ref) is not None
                            else edge_ref.split(":")[-1],
                        )
                        for edge_ref in face.edge_refs
                    ],
                    "adjacent_face_refs": [
                        self._format_topology_ref(
                            "face",
                            step,
                            parse_topology_ref(face_ref).get("entity_id", face_ref)
                            if parse_topology_ref(face_ref) is not None
                            else face_ref.split(":")[-1],
                        )
                        for face_ref in face.adjacent_face_refs
                    ],
                }
            )
            for face in topology_index.faces
        ]
        edges = [
            edge.model_copy(
                update={
                    "step": step,
                    "edge_ref": self._format_topology_ref("edge", step, edge.edge_id),
                    "adjacent_face_refs": [
                        self._format_topology_ref(
                            "face",
                            step,
                            parse_topology_ref(face_ref).get("entity_id", face_ref)
                            if parse_topology_ref(face_ref) is not None
                            else face_ref.split(":")[-1],
                        )
                        for face_ref in edge.adjacent_face_refs
                    ],
                }
            )
            for edge in topology_index.edges
        ]
        return topology_index.model_copy(update={"faces": faces, "edges": edges})

    def _bbox_to_dict(self, bbox: BoundingBox3D) -> dict[str, float]:
        return {
            "xlen": bbox.xlen,
            "ylen": bbox.ylen,
            "zlen": bbox.zlen,
            "xmin": bbox.xmin,
            "xmax": bbox.xmax,
            "ymin": bbox.ymin,
            "ymax": bbox.ymax,
            "zmin": bbox.zmin,
            "zmax": bbox.zmax,
        }

    def _resolve_render_focus(
        self,
        snapshot: CADStateSnapshot,
        request: RenderViewInput,
    ) -> tuple[BoundingBox3D | None, list[str]]:
        focused_entity_ids: list[str] = []
        if request.target_entity_ids:
            focus_bbox, focused_entity_ids = self._resolve_focus_bbox_from_entity_ids(
                geometry_objects=snapshot.geometry_objects,
                target_entity_ids=request.target_entity_ids,
            )
            if focus_bbox is not None:
                return focus_bbox, focused_entity_ids

        if (
            isinstance(request.focus_center, list)
            and len(request.focus_center) >= 3
            and request.focus_span is not None
            and request.focus_span > 0
        ):
            cx = self._as_float(request.focus_center[0])
            cy = self._as_float(request.focus_center[1])
            cz = self._as_float(request.focus_center[2])
            half = self._as_float(request.focus_span, fallback=1.0) / 2.0
            return (
                BoundingBox3D(
                    xlen=half * 2.0,
                    ylen=half * 2.0,
                    zlen=half * 2.0,
                    xmin=cx - half,
                    xmax=cx + half,
                    ymin=cy - half,
                    ymax=cy + half,
                    zmin=cz - half,
                    zmax=cz + half,
                ),
                [],
            )

        return None, []

    def _resolve_focus_bbox_from_entity_ids(
        self,
        geometry_objects: GeometryObjectIndex | None,
        target_entity_ids: list[str],
    ) -> tuple[BoundingBox3D | None, list[str]]:
        if geometry_objects is None:
            return None, []
        id_filter = {
            entity_id.strip() for entity_id in target_entity_ids if entity_id.strip()
        }
        if not id_filter:
            return None, []

        xmin = float("inf")
        xmax = float("-inf")
        ymin = float("inf")
        ymax = float("-inf")
        zmin = float("inf")
        zmax = float("-inf")
        focused_entity_ids: list[str] = []

        for solid in geometry_objects.solids:
            if solid.solid_id not in id_filter:
                continue
            focused_entity_ids.append(solid.solid_id)
            xmin = min(xmin, solid.bbox.xmin)
            xmax = max(xmax, solid.bbox.xmax)
            ymin = min(ymin, solid.bbox.ymin)
            ymax = max(ymax, solid.bbox.ymax)
            zmin = min(zmin, solid.bbox.zmin)
            zmax = max(zmax, solid.bbox.zmax)

        for face in geometry_objects.faces:
            if face.face_id not in id_filter:
                continue
            focused_entity_ids.append(face.face_id)
            xmin = min(xmin, face.bbox.xmin)
            xmax = max(xmax, face.bbox.xmax)
            ymin = min(ymin, face.bbox.ymin)
            ymax = max(ymax, face.bbox.ymax)
            zmin = min(zmin, face.bbox.zmin)
            zmax = max(zmax, face.bbox.zmax)

        for edge in geometry_objects.edges:
            if edge.edge_id not in id_filter:
                continue
            focused_entity_ids.append(edge.edge_id)
            xmin = min(xmin, edge.bbox.xmin)
            xmax = max(xmax, edge.bbox.xmax)
            ymin = min(ymin, edge.bbox.ymin)
            ymax = max(ymax, edge.bbox.ymax)
            zmin = min(zmin, edge.bbox.zmin)
            zmax = max(zmax, edge.bbox.zmax)

        if not focused_entity_ids:
            return None, []

        xlen = max(0.0, xmax - xmin)
        ylen = max(0.0, ymax - ymin)
        zlen = max(0.0, zmax - zmin)
        if xlen == 0.0:
            xlen = 1e-3
        if ylen == 0.0:
            ylen = 1e-3
        if zlen == 0.0:
            zlen = 1e-3

        return (
            BoundingBox3D(
                xlen=xlen,
                ylen=ylen,
                zlen=zlen,
                xmin=xmin,
                xmax=xmax,
                ymin=ymin,
                ymax=ymax,
                zmin=zmin,
                zmax=zmax,
            ),
            focused_entity_ids,
        )

    def _slice_geometry_object_index(
        self,
        source: GeometryObjectIndex | None,
        include_solids: bool,
        include_faces: bool,
        include_edges: bool,
        max_items_per_type: int,
        entity_ids: list[str],
        solid_offset: int,
        face_offset: int,
        edge_offset: int,
    ) -> tuple[
        GeometryObjectIndex | None, list[str], int | None, int | None, int | None
    ]:
        if source is None:
            return None, [], None, None, None

        id_filter = {entity_id.strip() for entity_id in entity_ids if entity_id.strip()}

        solids = source.solids if include_solids else []
        faces = source.faces if include_faces else []
        edges = source.edges if include_edges else []

        if id_filter:
            solids = [item for item in solids if item.solid_id in id_filter]
            faces = [item for item in faces if item.face_id in id_filter]
            edges = [item for item in edges if item.edge_id in id_filter]

        bounded_solid_offset = max(0, min(solid_offset, len(solids)))
        bounded_face_offset = max(0, min(face_offset, len(faces)))
        bounded_edge_offset = max(0, min(edge_offset, len(edges)))

        sliced_solids = solids[
            bounded_solid_offset : bounded_solid_offset + max_items_per_type
        ]
        sliced_faces = faces[
            bounded_face_offset : bounded_face_offset + max_items_per_type
        ]
        sliced_edges = edges[
            bounded_edge_offset : bounded_edge_offset + max_items_per_type
        ]

        matched_entity_ids = [
            *[item.solid_id for item in sliced_solids],
            *[item.face_id for item in sliced_faces],
            *[item.edge_id for item in sliced_edges],
        ]

        next_solid_offset = (
            bounded_solid_offset + len(sliced_solids)
            if bounded_solid_offset + len(sliced_solids) < len(solids)
            else None
        )
        next_face_offset = (
            bounded_face_offset + len(sliced_faces)
            if bounded_face_offset + len(sliced_faces) < len(faces)
            else None
        )
        next_edge_offset = (
            bounded_edge_offset + len(sliced_edges)
            if bounded_edge_offset + len(sliced_edges) < len(edges)
            else None
        )

        return (
            GeometryObjectIndex(
                solids=sliced_solids,
                faces=sliced_faces,
                edges=sliced_edges,
                solids_truncated=include_solids
                and (
                    source.solids_truncated
                    or bounded_solid_offset > 0
                    or len(solids) > bounded_solid_offset + len(sliced_solids)
                ),
                faces_truncated=include_faces
                and (
                    source.faces_truncated
                    or bounded_face_offset > 0
                    or len(faces) > bounded_face_offset + len(sliced_faces)
                ),
                edges_truncated=include_edges
                and (
                    source.edges_truncated
                    or bounded_edge_offset > 0
                    or len(edges) > bounded_edge_offset + len(sliced_edges)
                ),
                max_items_per_type=max_items_per_type,
                solids_total=len(solids),
                faces_total=len(faces),
                edges_total=len(edges),
                solid_offset=bounded_solid_offset,
                face_offset=bounded_face_offset,
                edge_offset=bounded_edge_offset,
                next_solid_offset=next_solid_offset,
                next_face_offset=next_face_offset,
                next_edge_offset=next_edge_offset,
            ),
            matched_entity_ids,
            next_solid_offset,
            next_face_offset,
            next_edge_offset,
        )

    def _slice_topology_object_index(
        self,
        source: TopologyObjectIndex | None,
        include_faces: bool,
        include_edges: bool,
        max_items_per_type: int,
        entity_ids: list[str],
        ref_ids: list[str],
        selection_hints: list[str],
        family_ids: list[str],
        face_offset: int,
        edge_offset: int,
    ) -> tuple[
        TopologyObjectIndex | None,
        list[str],
        list[str],
        list[TopologyCandidateSet],
        list[str],
        int | None,
        int | None,
    ]:
        if source is None:
            return None, [], [], [], [], None, None

        entity_filter = {
            entity_id.strip() for entity_id in entity_ids if entity_id.strip()
        }
        ref_filter = {ref_id.strip() for ref_id in ref_ids if ref_id.strip()}

        faces = source.faces if include_faces else []
        edges = source.edges if include_edges else []

        if entity_filter:
            faces = [item for item in faces if item.face_id in entity_filter]
            edges = [item for item in edges if item.edge_id in entity_filter]
        if ref_filter:
            faces = [item for item in faces if item.face_ref in ref_filter]
            edges = [item for item in edges if item.edge_ref in ref_filter]

        candidate_sets = self._build_requirement_topology_candidate_sets(
            faces=faces,
            edges=edges,
            selection_hints=selection_hints,
            family_ids=family_ids,
        )
        face_priority_ids = [
            candidate.candidate_id
            for candidate in candidate_sets
            if candidate.entity_type == "face"
        ]
        edge_priority_ids = [
            candidate.candidate_id
            for candidate in candidate_sets
            if candidate.entity_type == "edge"
        ]

        if face_priority_ids:
            faces = self._sort_topology_faces_by_candidate_priority(
                faces=faces,
                candidate_sets=candidate_sets,
                priority_ids=face_priority_ids,
            )
        if edge_priority_ids:
            edges = self._sort_topology_edges_by_candidate_priority(
                edges=edges,
                candidate_sets=candidate_sets,
                priority_ids=edge_priority_ids,
            )

        bounded_face_offset = max(0, min(face_offset, len(faces)))
        bounded_edge_offset = max(0, min(edge_offset, len(edges)))

        sliced_faces = faces[
            bounded_face_offset : bounded_face_offset + max_items_per_type
        ]
        sliced_edges = edges[
            bounded_edge_offset : bounded_edge_offset + max_items_per_type
        ]

        rank_results = bool(entity_filter or ref_filter or selection_hints)
        if rank_results:
            sliced_faces = [
                item.model_copy(update={"candidate_rank": idx})
                for idx, item in enumerate(sliced_faces, start=1)
            ]
            sliced_edges = [
                item.model_copy(update={"candidate_rank": idx})
                for idx, item in enumerate(sliced_edges, start=1)
            ]

        matched_entity_ids = [
            *[item.face_id for item in sliced_faces],
            *[item.edge_id for item in sliced_edges],
        ]
        matched_ref_ids = [
            *[item.face_ref for item in sliced_faces],
            *[item.edge_ref for item in sliced_edges],
        ]

        next_face_offset = (
            bounded_face_offset + len(sliced_faces)
            if bounded_face_offset + len(sliced_faces) < len(faces)
            else None
        )
        next_edge_offset = (
            bounded_edge_offset + len(sliced_edges)
            if bounded_edge_offset + len(sliced_edges) < len(edges)
            else None
        )

        return (
            TopologyObjectIndex(
                faces=sliced_faces,
                edges=sliced_edges,
                faces_truncated=include_faces
                and (
                    source.faces_truncated
                    or bounded_face_offset > 0
                    or len(faces) > bounded_face_offset + len(sliced_faces)
                ),
                edges_truncated=include_edges
                and (
                    source.edges_truncated
                    or bounded_edge_offset > 0
                    or len(edges) > bounded_edge_offset + len(sliced_edges)
                ),
                max_items_per_type=max_items_per_type,
                faces_total=len(faces),
                edges_total=len(edges),
                face_offset=bounded_face_offset,
                edge_offset=bounded_edge_offset,
                next_face_offset=next_face_offset,
                next_edge_offset=next_edge_offset,
            ),
            matched_entity_ids,
            matched_ref_ids,
            candidate_sets,
            selection_hints,
            next_face_offset,
            next_edge_offset,
        )

    def _normalize_topology_selection_hints(
        self,
        selection_hints: list[str],
        requirement_text: str | None,
    ) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()

        def _add_hint(raw_hint: str) -> None:
            hint = raw_hint.strip().lower().replace("-", "_").replace(" ", "_")
            alias_map: dict[str, tuple[str, ...]] = {
                "top": ("top_faces",),
                "top_face": ("top_faces",),
                "bottom": ("bottom_faces",),
                "bottom_face": ("bottom_faces",),
                "front": ("front_faces",),
                "front_face": ("front_faces",),
                "back": ("back_faces",),
                "back_face": ("back_faces",),
                "left": ("left_faces",),
                "left_face": ("left_faces",),
                "right": ("right_faces",),
                "right_face": ("right_faces",),
                "mounting": ("mating_faces",),
                "mounting_face": ("mating_faces",),
                "mounting_faces": ("mating_faces",),
                "mating": ("mating_faces",),
                "mating_face": ("mating_faces",),
                "mating_faces": ("mating_faces",),
                "planar": ("upward_planar_faces", "downward_planar_faces"),
                "planar_face": ("upward_planar_faces", "downward_planar_faces"),
                "planar_faces": ("upward_planar_faces", "downward_planar_faces"),
                "flat": ("upward_planar_faces", "downward_planar_faces"),
                "flat_faces": ("upward_planar_faces", "downward_planar_faces"),
                "rim": ("opening_rim_edges",),
                "opening_rim": ("opening_rim_edges",),
                "notch_rim": ("opening_rim_edges",),
                "split": ("split_plane_faces",),
                "split_plane": ("split_plane_faces",),
                "seam": ("split_plane_faces",),
            }
            expanded_hints = alias_map.get(hint, (hint,))
            for expanded_hint in expanded_hints:
                if not expanded_hint or expanded_hint in seen:
                    continue
                seen.add(expanded_hint)
                normalized.append(expanded_hint)

        for item in selection_hints:
            if isinstance(item, str):
                _add_hint(item)
        for item in collect_requirement_topology_hints(
            {"description": requirement_text} if requirement_text else None
        ):
            _add_hint(item)

        return normalized

    def _requirement_suggests_loft(
        self,
        requirement_text: str | None,
        history: list[ActionHistoryEntry],
    ) -> bool:
        if any(entry.action_type == CADActionType.LOFT for entry in history):
            return True
        text = (requirement_text or "").strip().lower()
        if not text:
            return False
        if any(
            token in text
            for token in (
                "frustum",
                "taper between",
                "transition between",
                "apex",
                "connect the two profiles",
                "connect two profiles",
            )
        ):
            return True
        if "loft" not in text:
            return False
        return not self._requirement_uses_operation_as_optional_method(
            requirement_text,
            operation_terms=("loft", "lofting"),
        )

    def _extract_plane_trim_requirement(
        self,
        requirement_text: str | None,
    ) -> dict[str, Any] | None:
        text = (requirement_text or "").strip().lower()
        if not text:
            return None
        segments = re.split(r"(?<=[.!?])\s+", text)
        trim_patterns = (
            r"\btrim(?:med|ming)?\b",
            r"\btruncate(?:d|s|ing)?\b",
            r"\bremove (?:the )?solid above\b",
            r"\bremove solid above\b",
            r"\bremove (?:the )?solid below\b",
            r"\bremove solid below\b",
            r"\bform a frustum\b",
            r"\bforming a frustum\b",
            r"\bsplit (?:the )?(?:solid|body|part)\b",
            r"\bsplit by (?:a |the )?(?:datum )?plane\b",
            r"\bsplit with (?:a |the )?(?:datum )?plane\b",
        )
        trim_segments = [
            segment
            for segment in segments
            if any(re.search(pattern, segment) for pattern in trim_patterns)
        ]
        if not trim_segments:
            return None
        scan_text = " ".join(trim_segments)
        plane = "XY"
        if any(token in scan_text for token in ("front datum", "front view datum plane")):
            plane = "XZ"
        elif any(
            token in scan_text
            for token in ("right datum", "side datum", "right view datum plane", "side view datum plane")
        ):
            plane = "YZ"

        if plane == "XZ":
            keep = "back" if any(
                token in scan_text
                for token in ("remove the solid in front", "remove solid in front", "keep back")
            ) else "front"
        elif plane == "YZ":
            keep = "left" if any(
                token in scan_text
                for token in ("remove the solid on the right", "remove solid on the right", "keep left")
            ) else "right"
        else:
            keep = "above" if any(
                token in scan_text
                for token in ("remove the solid below", "remove solid below", "keep above")
            ) else "below"

        offset: float | None = None
        patterns = [
            r"([0-9]+(?:\.[0-9]+)?)\s*mm\s+above the base",
            r"([0-9]+(?:\.[0-9]+)?)\s*mm\s+below the top",
            r"distance of\s*([0-9]+(?:\.[0-9]+)?)\s*mm",
            r"at a distance of\s*([0-9]+(?:\.[0-9]+)?)\s*mm",
        ]
        for pattern in patterns:
            match = re.search(pattern, scan_text)
            if match:
                try:
                    offset = float(match.group(1))
                except Exception:
                    offset = None
                break

        return {"plane": plane, "keep": keep, "offset": offset, "text": scan_text}

    def _history_has_requirement_plane_trim(
        self,
        history: list[ActionHistoryEntry],
        first_solid_index: int | None,
        requirement_text: str | None,
    ) -> tuple[bool, str]:
        trim_spec = self._extract_plane_trim_requirement(requirement_text)
        if trim_spec is None or first_solid_index is None or not history:
            return False, ""
        axis_index = {"XY": 2, "XZ": 1, "YZ": 0}.get(trim_spec["plane"], 2)
        first_geometry = history[first_solid_index].result_snapshot.geometry
        final_geometry = history[-1].result_snapshot.geometry
        first_min = (
            float(first_geometry.bbox_min[axis_index])
            if len(first_geometry.bbox_min) > axis_index
            else None
        )
        first_max = (
            float(first_geometry.bbox_max[axis_index])
            if len(first_geometry.bbox_max) > axis_index
            else None
        )
        final_min = (
            float(final_geometry.bbox_min[axis_index])
            if len(final_geometry.bbox_min) > axis_index
            else None
        )
        final_max = (
            float(final_geometry.bbox_max[axis_index])
            if len(final_geometry.bbox_max) > axis_index
            else None
        )
        first_span = (
            float(first_geometry.bbox[axis_index])
            if len(first_geometry.bbox) > axis_index
            else None
        )
        final_span = (
            float(final_geometry.bbox[axis_index])
            if len(final_geometry.bbox) > axis_index
            else None
        )

        trim_indices = [
            index
            for index, entry in enumerate(history)
            if index > first_solid_index
            and entry.action_type == CADActionType.TRIM_SOLID
            and self._history_action_materially_changes_geometry(history, index)
        ]
        if trim_indices:
            return (
                True,
                f"trim_solid_steps={[history[index].step for index in trim_indices]}, plane={trim_spec['plane']}, keep={trim_spec['keep']}, offset={trim_spec['offset']}",
            )

        subtractive_indices = [
            index
            for index, entry in enumerate(history)
            if index > first_solid_index
            and self._action_is_subtractive_edit(entry)
            and self._history_action_materially_changes_geometry(history, index)
        ]
        span_reduced = (
            isinstance(first_span, (int, float))
            and isinstance(final_span, (int, float))
            and float(final_span) < float(first_span) - 1e-3
        )
        offset_ok = True
        offset_value = trim_spec.get("offset")
        if isinstance(offset_value, (int, float)):
            tolerance = max(1.0, abs(float(offset_value)) * 0.05)
            if trim_spec["keep"] in {"below", "back", "left"}:
                offset_ok = isinstance(final_max, (int, float)) and float(final_max) <= float(offset_value) + tolerance
            else:
                offset_ok = isinstance(final_min, (int, float)) and float(final_min) >= float(offset_value) - tolerance
        if subtractive_indices and span_reduced and offset_ok:
            return (
                True,
                "post-solid subtractive stage reduced the trimmed axis span consistently with the requirement "
                f"(axis_index={axis_index}, plane={trim_spec['plane']}, keep={trim_spec['keep']}, offset={trim_spec['offset']})",
            )
        return (
            False,
            "missing requirement-aligned plane trim stage "
            f"(plane={trim_spec['plane']}, keep={trim_spec['keep']}, offset={trim_spec['offset']}, "
            f"first_span={first_span}, final_span={final_span}, first_bounds={[first_min, first_max]}, final_bounds={[final_min, final_max]})",
        )

    def _requirement_uses_operation_as_optional_method(
        self,
        requirement_text: str | None,
        operation_terms: tuple[str, ...],
    ) -> bool:
        return requirement_uses_operation_as_optional_method(
            None,
            requirement_text,
            operation_terms=operation_terms,
        )

    def _history_has_point_loft(
        self,
        history: list[ActionHistoryEntry],
    ) -> bool:
        for entry in history:
            if entry.action_type != CADActionType.LOFT:
                continue
            params = normalize_action_params(entry.action_type, entry.action_params)
            if params.get("to_point") is not None:
                return True
            if isinstance(params.get("height"), (int, float)):
                return True
        return False

    def _build_requirement_topology_candidate_sets(
        self,
        faces: list[TopologyFaceEntity],
        edges: list[TopologyEdgeEntity],
        selection_hints: list[str],
        family_ids: list[str],
    ) -> list[TopologyCandidateSet]:
        if not selection_hints:
            return []

        extents = self._topology_extents(faces=faces, edges=edges)
        if extents is None:
            return []

        min_x, max_x, min_y, max_y, min_z, max_z = extents
        x_tol = self._extent_tolerance(min_x, max_x)
        y_tol = self._extent_tolerance(min_y, max_y)
        z_tol = self._extent_tolerance(min_z, max_z)

        def _face_is_top(face: TopologyFaceEntity) -> bool:
            if isinstance(face.normal, list) and len(face.normal) >= 3:
                nz = face.normal[2]
                if (
                    isinstance(nz, (int, float))
                    and float(nz) >= 0.7
                    and self._near_max(face.bbox.zmax, max_z, z_tol)
                ):
                    return True
            return self._near_max(face.center[2], max_z, z_tol)

        def _face_is_bottom(face: TopologyFaceEntity) -> bool:
            if isinstance(face.normal, list) and len(face.normal) >= 3:
                nz = face.normal[2]
                if (
                    isinstance(nz, (int, float))
                    and float(nz) <= -0.7
                    and self._near_min(face.bbox.zmin, min_z, z_tol)
                ):
                    return True
            return self._near_min(face.center[2], min_z, z_tol)

        def _face_is_front(face: TopologyFaceEntity) -> bool:
            if isinstance(face.normal, list) and len(face.normal) >= 2:
                ny = face.normal[1]
                if (
                    isinstance(ny, (int, float))
                    and float(ny) >= 0.7
                    and self._near_max(face.bbox.ymax, max_y, y_tol)
                ):
                    return True
            return self._near_max(face.center[1], max_y, y_tol)

        def _face_is_back(face: TopologyFaceEntity) -> bool:
            if isinstance(face.normal, list) and len(face.normal) >= 2:
                ny = face.normal[1]
                if (
                    isinstance(ny, (int, float))
                    and float(ny) <= -0.7
                    and self._near_min(face.bbox.ymin, min_y, y_tol)
                ):
                    return True
            return self._near_min(face.center[1], min_y, y_tol)

        def _face_is_left(face: TopologyFaceEntity) -> bool:
            if isinstance(face.normal, list) and len(face.normal) >= 1:
                nx = face.normal[0]
                if (
                    isinstance(nx, (int, float))
                    and float(nx) <= -0.7
                    and self._near_min(face.bbox.xmin, min_x, x_tol)
                ):
                    return True
            return self._near_min(face.center[0], min_x, x_tol)

        def _face_is_right(face: TopologyFaceEntity) -> bool:
            if isinstance(face.normal, list) and len(face.normal) >= 1:
                nx = face.normal[0]
                if (
                    isinstance(nx, (int, float))
                    and float(nx) >= 0.7
                    and self._near_max(face.bbox.xmax, max_x, x_tol)
                ):
                    return True
            return self._near_max(face.center[0], max_x, x_tol)

        def _touches_outer_xy(bbox: BoundingBox3D) -> bool:
            return (
                self._near_min(bbox.xmin, min_x, x_tol)
                or self._near_max(bbox.xmax, max_x, x_tol)
                or self._near_min(bbox.ymin, min_y, y_tol)
                or self._near_max(bbox.ymax, max_y, y_tol)
            )

        def _edge_is_top(edge: TopologyEdgeEntity) -> bool:
            if isinstance(edge.center, list) and len(edge.center) >= 3:
                z_value = edge.center[2]
                if isinstance(z_value, (int, float)) and self._near_max(
                    float(z_value), max_z, z_tol
                ):
                    return True
            return self._near_max(edge.bbox.zmax, max_z, z_tol) and edge.bbox.zlen <= (
                z_tol * 4.0
            )

        def _edge_is_bottom(edge: TopologyEdgeEntity) -> bool:
            if isinstance(edge.center, list) and len(edge.center) >= 3:
                z_value = edge.center[2]
                if isinstance(z_value, (int, float)) and self._near_min(
                    float(z_value), min_z, z_tol
                ):
                    return True
            return self._near_min(edge.bbox.zmin, min_z, z_tol) and edge.bbox.zlen <= (
                z_tol * 4.0
            )

        axis_spans = {
            "X": max_x - min_x,
            "Y": max_y - min_y,
            "Z": max_z - min_z,
        }
        primary_axis = max(axis_spans, key=axis_spans.get)
        axis_bounds = {
            "X": (min_x, max_x),
            "Y": (min_y, max_y),
            "Z": (min_z, max_z),
        }
        primary_axis_min, primary_axis_max = axis_bounds[primary_axis]
        primary_axis_midpoint = (primary_axis_min + primary_axis_max) / 2.0
        orthogonal_axes = [axis for axis in ("X", "Y", "Z") if axis != primary_axis]
        orthogonal_spans = [axis_spans[axis] for axis in orthogonal_axes]
        outer_half_span_estimate = (
            min(orthogonal_spans) / 2.0 if orthogonal_spans else 0.0
        )
        suggested_sketch_planes = {
            "X": ["XY", "XZ"],
            "Y": ["XY", "YZ"],
            "Z": ["XZ", "YZ"],
        }.get(primary_axis, [])

        common_metadata = {
            "primary_axis": primary_axis,
            "axis_span": round(axis_spans.get(primary_axis, 0.0), 6),
            "axis_min": round(primary_axis_min, 6),
            "axis_max": round(primary_axis_max, 6),
            "axis_midpoint": round(primary_axis_midpoint, 6),
            "outer_half_span_estimate": round(outer_half_span_estimate, 6),
            "suggested_sketch_planes": suggested_sketch_planes,
        }

        def _sketch_frame_metadata(face_label: str) -> dict[str, Any]:
            if face_label == "top":
                return {
                    "sketch_plane": "XY",
                    "sketch_u_axis": "+X",
                    "sketch_v_axis": "+Y",
                }
            if face_label == "bottom":
                return {
                    "sketch_plane": "XY",
                    "sketch_u_axis": "-X",
                    "sketch_v_axis": "+Y",
                }
            if face_label == "front":
                return {
                    "sketch_plane": "XZ",
                    "sketch_u_axis": "-X",
                    "sketch_v_axis": "+Z",
                }
            if face_label == "back":
                return {
                    "sketch_plane": "XZ",
                    "sketch_u_axis": "+X",
                    "sketch_v_axis": "+Z",
                }
            if face_label == "right":
                return {
                    "sketch_plane": "YZ",
                    "sketch_u_axis": "+Y",
                    "sketch_v_axis": "+Z",
                }
            if face_label == "left":
                return {
                    "sketch_plane": "YZ",
                    "sketch_u_axis": "-Y",
                    "sketch_v_axis": "+Z",
                }
            return {}

        def _combined_edge_set(
            primary_faces: list[TopologyFaceEntity],
            secondary_edges: list[TopologyEdgeEntity],
        ) -> list[TopologyEdgeEntity]:
            primary_edge_refs = {
                edge_ref
                for face in primary_faces
                for edge_ref in face.edge_refs
                if isinstance(edge_ref, str)
            }
            if not primary_edge_refs:
                return []
            return [
                edge
                for edge in secondary_edges
                if edge.edge_ref in primary_edge_refs
            ]

        def _edge_anchor_metadata(
            edge_list: list[TopologyEdgeEntity],
            *,
            anchor_role: str,
            face_label: str,
        ) -> dict[str, Any]:
            metadata = {
                **common_metadata,
                **_sketch_frame_metadata(face_label),
                "anchor_role": anchor_role,
            }
            if edge_list:
                metadata["anchor_ref_id"] = edge_list[0].edge_ref
                if isinstance(edge_list[0].center, list) and len(edge_list[0].center) >= 3:
                    metadata["anchor_point"] = [
                        round(float(edge_list[0].center[0]), 6),
                        round(float(edge_list[0].center[1]), 6),
                        round(float(edge_list[0].center[2]), 6),
                    ]
            return metadata

        def _bbox_axis_length(bbox: BoundingBox3D, axis: str) -> float:
            if axis == "X":
                return float(bbox.xlen)
            if axis == "Y":
                return float(bbox.ylen)
            return float(bbox.zlen)

        def _edge_is_primary_axis_aligned(edge: TopologyEdgeEntity) -> bool:
            axis_length = _bbox_axis_length(edge.bbox, primary_axis)
            other_lengths = [
                _bbox_axis_length(edge.bbox, axis)
                for axis in orthogonal_axes
            ]
            max_other = max(other_lengths) if other_lengths else 0.0
            return axis_length > 0.0 and axis_length >= max(max_other * 1.5, 1e-6)

        def _edge_is_axis_aligned(
            edge: TopologyEdgeEntity,
            axis: str,
        ) -> bool:
            return self._topology_edge_alignment_axis(edge) == axis

        def _edge_center_z(edge: TopologyEdgeEntity) -> float:
            if isinstance(edge.center, list) and len(edge.center) >= 3:
                try:
                    return float(edge.center[2])
                except Exception:
                    return float(edge.bbox.zmin)
            return float(edge.bbox.zmin)

        def _edge_subset_by_z(
            edge_list: list[TopologyEdgeEntity],
            *,
            pick: str,
        ) -> list[TopologyEdgeEntity]:
            if not edge_list:
                return []
            z_values = [_edge_center_z(edge) for edge in edge_list]
            target_value = min(z_values) if pick == "min" else max(z_values)
            tolerance = max(1.0, (max(z_values) - min(z_values)) * 0.08)
            return [
                edge
                for edge in edge_list
                if abs(_edge_center_z(edge) - target_value) <= tolerance
            ]

        def _dedupe_faces(face_list: list[TopologyFaceEntity]) -> list[TopologyFaceEntity]:
            ordered: list[TopologyFaceEntity] = []
            seen: set[str] = set()
            for face in face_list:
                if face.face_ref in seen:
                    continue
                seen.add(face.face_ref)
                ordered.append(face)
            return ordered

        def _dedupe_edges(edge_list: list[TopologyEdgeEntity]) -> list[TopologyEdgeEntity]:
            ordered: list[TopologyEdgeEntity] = []
            seen: set[str] = set()
            for edge in edge_list:
                if edge.edge_ref in seen:
                    continue
                seen.add(edge.edge_ref)
                ordered.append(edge)
            return ordered

        def _host_role_metadata(
            *,
            base_metadata: dict[str, Any],
            primary_role: str,
            role_confidence: str,
            role_candidates: list[str],
        ) -> dict[str, Any]:
            metadata = dict(base_metadata)
            metadata["host_role"] = primary_role
            metadata["host_role_confidence"] = role_confidence
            metadata["semantic_host_roles"] = role_candidates
            metadata["semantic_host_digest"] = {
                "primary_role": primary_role,
                "role_confidence": role_confidence,
                "role_candidates": role_candidates,
            }
            return metadata

        def _face_axis_center(face: TopologyFaceEntity, axis: str) -> float:
            axis_index = {"X": 0, "Y": 1, "Z": 2}.get(axis, 2)
            return float(face.center[axis_index])

        def _matching_mid_axes(face: TopologyFaceEntity) -> list[str]:
            if str(face.geom_type).strip().upper() != "PLANE":
                return []
            matched_axes: list[str] = []
            for axis in ("X", "Y", "Z"):
                axis_min, axis_max = axis_bounds[axis]
                axis_midpoint = (axis_min + axis_max) / 2.0
                axis_span = axis_spans.get(axis, 0.0)
                if abs(_face_axis_center(face, axis) - axis_midpoint) > max(1.0, axis_span * 0.12):
                    continue
                if _bbox_axis_length(face.bbox, axis) > max(1.0, axis_span * 0.18):
                    continue
                matched_axes.append(axis)
            return matched_axes

        candidate_builders: dict[
            str,
            tuple[str, str, list[str], list[str], str, dict[str, Any]],
        ] = {}

        def _sort_directional_faces(
            face_items: list[TopologyFaceEntity],
        ) -> list[TopologyFaceEntity]:
            return sorted(
                face_items,
                key=lambda face: (
                    0 if str(face.geom_type).strip().upper() == "PLANE" else 1,
                    -float(face.area),
                    face.face_ref,
                ),
            )

        top_faces = _sort_directional_faces([face for face in faces if _face_is_top(face)])
        bottom_faces = _sort_directional_faces([face for face in faces if _face_is_bottom(face)])
        front_faces = _sort_directional_faces([face for face in faces if _face_is_front(face)])
        back_faces = _sort_directional_faces([face for face in faces if _face_is_back(face)])
        left_faces = _sort_directional_faces([face for face in faces if _face_is_left(face)])
        right_faces = _sort_directional_faces([face for face in faces if _face_is_right(face)])
        upward_planar_faces = [
            face
            for face in faces
            if str(face.geom_type).strip().upper() == "PLANE"
            and isinstance(face.normal, list)
            and len(face.normal) >= 3
            and isinstance(face.normal[2], (int, float))
            and float(face.normal[2]) >= 0.7
        ]
        downward_planar_faces = [
            face
            for face in faces
            if str(face.geom_type).strip().upper() == "PLANE"
            and isinstance(face.normal, list)
            and len(face.normal) >= 3
            and isinstance(face.normal[2], (int, float))
            and float(face.normal[2]) <= -0.7
        ]
        outer_faces = [
            face
            for face in faces
            if (
                _face_is_top(face)
                or _face_is_bottom(face)
                or _face_is_front(face)
                or _face_is_back(face)
                or _face_is_left(face)
                or _face_is_right(face)
                or (
                    str(face.geom_type).strip().upper() != "PLANE"
                    and _touches_outer_xy(face.bbox)
                )
            )
        ]
        outer_face_ref_set = {face.face_ref for face in outer_faces}
        interior_faces = [face for face in faces if face.face_ref not in outer_face_ref_set]
        top_inner_planar_faces = [
            face
            for face in upward_planar_faces
            if face.face_ref not in outer_face_ref_set or not _face_is_top(face)
        ]
        bottom_inner_planar_faces = [
            face
            for face in downward_planar_faces
            if face.face_ref not in outer_face_ref_set or not _face_is_bottom(face)
        ]
        outer_face_refs = {face.face_ref for face in outer_faces}
        top_edges = [edge for edge in edges if _edge_is_top(edge)]
        bottom_edges = [edge for edge in edges if _edge_is_bottom(edge)]
        def _edge_is_outer(edge: TopologyEdgeEntity) -> bool:
            adjacent_refs = [
                face_ref
                for face_ref in edge.adjacent_face_refs
                if isinstance(face_ref, str)
            ]
            adjacent_outer_count = sum(
                1
                for face_ref in adjacent_refs
                if face_ref in outer_face_refs
            )
            if adjacent_outer_count >= 2:
                return True
            if adjacent_refs and adjacent_outer_count == len(adjacent_refs):
                return True
            if adjacent_outer_count == 0:
                return _touches_outer_xy(edge.bbox)
            return False

        outer_edges = [edge for edge in edges if _edge_is_outer(edge)]
        outer_edge_refs = {edge.edge_ref for edge in outer_edges}
        inner_edges = [edge for edge in edges if edge.edge_ref not in outer_edge_refs]
        primary_outer_faces = sorted(
            outer_faces,
            key=lambda face: (-float(face.area), face.face_ref),
        )
        shell_exterior_faces = primary_outer_faces
        shell_interior_faces = sorted(
            interior_faces,
            key=lambda face: (-float(face.area), face.face_ref),
        )
        mating_faces = sorted(
            _dedupe_faces([*top_inner_planar_faces, *bottom_inner_planar_faces]),
            key=lambda face: (-float(face.area), face.face_ref),
        )
        split_plane_faces = sorted(
            [
                face
                for face in faces
                if _matching_mid_axes(face)
            ],
            key=lambda face: (-float(face.area), face.face_ref),
        )
        primary_axis_outer_edges = sorted(
            [
                edge
                for edge in outer_edges
                if _bbox_axis_length(edge.bbox, primary_axis)
                >= max(
                    _bbox_axis_length(edge.bbox, orthogonal_axes[0])
                    if len(orthogonal_axes) >= 1
                    else 0.0,
                    _bbox_axis_length(edge.bbox, orthogonal_axes[1])
                    if len(orthogonal_axes) >= 2
                    else 0.0,
                )
            ],
            key=lambda edge: (
                -_bbox_axis_length(edge.bbox, primary_axis),
                edge.edge_ref,
            ),
        )
        top_outer_edges = [edge for edge in top_edges if _touches_outer_xy(edge.bbox)]
        bottom_outer_edges = [
            edge for edge in bottom_edges if _touches_outer_xy(edge.bbox)
        ]
        x_parallel_outer_edges = [
            edge for edge in outer_edges if _edge_is_axis_aligned(edge, "X")
        ]
        y_parallel_outer_edges = [
            edge for edge in outer_edges if _edge_is_axis_aligned(edge, "Y")
        ]
        z_parallel_outer_edges = [
            edge for edge in outer_edges if _edge_is_axis_aligned(edge, "Z")
        ]
        x_parallel_top_outer_edges = [
            edge for edge in top_outer_edges if _edge_is_axis_aligned(edge, "X")
        ]
        y_parallel_top_outer_edges = [
            edge for edge in top_outer_edges if _edge_is_axis_aligned(edge, "Y")
        ]
        z_parallel_top_outer_edges = [
            edge for edge in top_outer_edges if _edge_is_axis_aligned(edge, "Z")
        ]
        x_parallel_bottom_outer_edges = [
            edge for edge in bottom_outer_edges if _edge_is_axis_aligned(edge, "X")
        ]
        y_parallel_bottom_outer_edges = [
            edge for edge in bottom_outer_edges if _edge_is_axis_aligned(edge, "Y")
        ]
        z_parallel_bottom_outer_edges = [
            edge for edge in bottom_outer_edges if _edge_is_axis_aligned(edge, "Z")
        ]
        front_top_edges = _combined_edge_set(front_faces, top_edges)
        back_top_edges = _combined_edge_set(back_faces, top_edges)
        left_top_edges = _combined_edge_set(left_faces, top_edges)
        right_top_edges = _combined_edge_set(right_faces, top_edges)
        front_bottom_edges = _combined_edge_set(front_faces, bottom_edges)
        back_bottom_edges = _combined_edge_set(back_faces, bottom_edges)
        left_bottom_edges = _combined_edge_set(left_faces, bottom_edges)
        right_bottom_edges = _combined_edge_set(right_faces, bottom_edges)
        front_inner_edges = _combined_edge_set(front_faces, inner_edges)
        back_inner_edges = _combined_edge_set(back_faces, inner_edges)
        left_inner_edges = _combined_edge_set(left_faces, inner_edges)
        right_inner_edges = _combined_edge_set(right_faces, inner_edges)
        inner_bottom_edges = _edge_subset_by_z(inner_edges, pick="min")
        inner_bottom_side_edges = [
            edge for edge in inner_bottom_edges if not _edge_is_primary_axis_aligned(edge)
        ]
        inner_top_edges = _edge_subset_by_z(inner_edges, pick="max")
        front_inner_bottom_edges = _edge_subset_by_z(front_inner_edges, pick="min")
        back_inner_bottom_edges = _edge_subset_by_z(back_inner_edges, pick="min")
        left_inner_bottom_edges = _edge_subset_by_z(left_inner_edges, pick="min")
        right_inner_bottom_edges = _edge_subset_by_z(right_inner_edges, pick="min")
        front_inner_top_edges = _edge_subset_by_z(front_inner_edges, pick="max")
        back_inner_top_edges = _edge_subset_by_z(back_inner_edges, pick="max")
        left_inner_top_edges = _edge_subset_by_z(left_inner_edges, pick="max")
        right_inner_top_edges = _edge_subset_by_z(right_inner_edges, pick="max")
        opening_rim_edges = sorted(
            _dedupe_edges([*inner_top_edges, *front_inner_top_edges, *back_inner_top_edges, *left_inner_top_edges, *right_inner_top_edges]),
            key=lambda edge: (-float(edge.length), edge.edge_ref),
        )

        candidate_builders["top_faces"] = (
            "Top Faces",
            "face",
            [face.face_ref for face in top_faces],
            [face.face_id for face in top_faces],
            "Faces near the global +Z extent, prioritized for top-side edits.",
            {
                **common_metadata,
                **_sketch_frame_metadata("top"),
                "target_axis_side": "max",
                "target_coordinate": round(max_z, 6),
            },
        )
        candidate_builders["bottom_faces"] = (
            "Bottom Faces",
            "face",
            [face.face_ref for face in bottom_faces],
            [face.face_id for face in bottom_faces],
            "Faces near the global -Z extent, prioritized for bottom-side edits.",
            {
                **common_metadata,
                **_sketch_frame_metadata("bottom"),
                "target_axis_side": "min",
                "target_coordinate": round(min_z, 6),
            },
        )
        candidate_builders["upward_planar_faces"] = (
            "Upward Planar Faces",
            "face",
            [face.face_ref for face in upward_planar_faces],
            [face.face_id for face in upward_planar_faces],
            "Planar faces whose normals point generally upward (+Z), including interior shelves and annular landing faces.",
            {
                **common_metadata,
                **_sketch_frame_metadata("top"),
                "target_axis": "Z",
                "target_axis_side": "positive",
            },
        )
        candidate_builders["downward_planar_faces"] = (
            "Downward Planar Faces",
            "face",
            [face.face_ref for face in downward_planar_faces],
            [face.face_id for face in downward_planar_faces],
            "Planar faces whose normals point generally downward (-Z), including interior floors and underside shelves.",
            {
                **common_metadata,
                **_sketch_frame_metadata("bottom"),
                "target_axis": "Z",
                "target_axis_side": "negative",
            },
        )
        candidate_builders["top_inner_planar_faces"] = (
            "Top Inner Planar Faces",
            "face",
            [face.face_ref for face in top_inner_planar_faces],
            [face.face_id for face in top_inner_planar_faces],
            "Upward planar faces that are not limited to the outermost top cap, useful for annular flanges, shelves, and interior landing pads.",
            {
                **common_metadata,
                **_sketch_frame_metadata("top"),
                "target_axis": "Z",
                "target_axis_side": "interior_positive",
            },
        )
        candidate_builders["bottom_inner_planar_faces"] = (
            "Bottom Inner Planar Faces",
            "face",
            [face.face_ref for face in bottom_inner_planar_faces],
            [face.face_id for face in bottom_inner_planar_faces],
            "Downward planar faces that are not limited to the outermost bottom cap, useful for interior floors and underside landing pads.",
            {
                **common_metadata,
                **_sketch_frame_metadata("bottom"),
                "target_axis": "Z",
                "target_axis_side": "interior_negative",
            },
        )
        candidate_builders["front_faces"] = (
            "Front Faces",
            "face",
            [face.face_ref for face in front_faces],
            [face.face_id for face in front_faces],
            "Faces near the global +Y extent, prioritized for front-side edits.",
            {
                **common_metadata,
                **_sketch_frame_metadata("front"),
                "target_axis": "Y",
                "target_axis_side": "max",
                "target_coordinate": round(max_y, 6),
            },
        )
        candidate_builders["back_faces"] = (
            "Back Faces",
            "face",
            [face.face_ref for face in back_faces],
            [face.face_id for face in back_faces],
            "Faces near the global -Y extent, prioritized for back-side edits.",
            {
                **common_metadata,
                **_sketch_frame_metadata("back"),
                "target_axis": "Y",
                "target_axis_side": "min",
                "target_coordinate": round(min_y, 6),
            },
        )
        candidate_builders["left_faces"] = (
            "Left Faces",
            "face",
            [face.face_ref for face in left_faces],
            [face.face_id for face in left_faces],
            "Faces near the global -X extent, prioritized for left-side edits.",
            {
                **common_metadata,
                **_sketch_frame_metadata("left"),
                "target_axis": "X",
                "target_axis_side": "min",
                "target_coordinate": round(min_x, 6),
            },
        )
        candidate_builders["right_faces"] = (
            "Right Faces",
            "face",
            [face.face_ref for face in right_faces],
            [face.face_id for face in right_faces],
            "Faces near the global +X extent, prioritized for right-side edits.",
            {
                **common_metadata,
                **_sketch_frame_metadata("right"),
                "target_axis": "X",
                "target_axis_side": "max",
                "target_coordinate": round(max_x, 6),
            },
        )
        candidate_builders["outer_faces"] = (
            "Outer Faces",
            "face",
            [face.face_ref for face in outer_faces],
            [face.face_id for face in outer_faces],
            "Faces touching the outer XY boundary of the current solid.",
            common_metadata,
        )
        candidate_builders["primary_outer_faces"] = (
            "Primary Outer Faces",
            "face",
            [face.face_ref for face in primary_outer_faces],
            [face.face_id for face in primary_outer_faces],
            "Largest outer faces, prioritized for axis-aligned side edits such as grooves and side sketches.",
            {
                **common_metadata,
                "dominant_ref_id": (
                    primary_outer_faces[0].face_ref if primary_outer_faces else None
                ),
            },
        )
        candidate_builders["shell_exterior_faces"] = (
            "Shell Exterior Faces",
            "face",
            [face.face_ref for face in shell_exterior_faces],
            [face.face_id for face in shell_exterior_faces],
            "Outer shell host faces ranked for enclosure, housing, lid, and base edits.",
            _host_role_metadata(
                base_metadata=common_metadata,
                primary_role="shell_exterior",
                role_confidence="high",
                role_candidates=["shell_exterior", "outer_host", "enclosure_wall"],
            ),
        )
        candidate_builders["shell_interior_faces"] = (
            "Shell Interior Faces",
            "face",
            [face.face_ref for face in shell_interior_faces],
            [face.face_id for face in shell_interior_faces],
            "Interior shell host faces ranked for cavity, pocket, and enclosure-inner edits.",
            _host_role_metadata(
                base_metadata=common_metadata,
                primary_role="shell_interior",
                role_confidence="medium",
                role_candidates=["shell_interior", "cavity_host", "inner_wall"],
            ),
        )
        candidate_builders["mating_faces"] = (
            "Mating Faces",
            "face",
            [face.face_ref for face in mating_faces],
            [face.face_id for face in mating_faces],
            "Large inner planar faces that can act as lid/base mating surfaces or closure landing faces.",
            _host_role_metadata(
                base_metadata=common_metadata,
                primary_role="mating_face",
                role_confidence="medium",
                role_candidates=["mating_face", "closure_landing", "inner_planar_host"],
            ),
        )
        candidate_builders["split_plane_faces"] = (
            "Split-Plane Faces",
            "face",
            [face.face_ref for face in split_plane_faces],
            [face.face_id for face in split_plane_faces],
            "Planar faces near the dominant-axis mid-band, useful for half-shell split, seam, and mating diagnostics.",
            _host_role_metadata(
                base_metadata={
                    **common_metadata,
                    "target_axis": "midband",
                    "target_axis_side": "mid",
                    "candidate_split_axes": sorted(
                        {
                            axis
                            for face in split_plane_faces
                            for axis in _matching_mid_axes(face)
                        }
                    ),
                },
                primary_role="split_plane",
                role_confidence="medium",
                role_candidates=["split_plane", "seam_host", "mating_face"],
            ),
        )
        candidate_builders["top_edges"] = (
            "Top Edges",
            "edge",
            [edge.edge_ref for edge in top_edges],
            [edge.edge_id for edge in top_edges],
            "Edges near the global +Z extent.",
            {
                **common_metadata,
                "target_axis_side": "max",
                "target_coordinate": round(max_z, 6),
            },
        )
        candidate_builders["bottom_edges"] = (
            "Bottom Edges",
            "edge",
            [edge.edge_ref for edge in bottom_edges],
            [edge.edge_id for edge in bottom_edges],
            "Edges near the global -Z extent.",
            {
                **common_metadata,
                "target_axis_side": "min",
                "target_coordinate": round(min_z, 6),
            },
        )
        candidate_builders["outer_edges"] = (
            "Outer Edges",
            "edge",
            [edge.edge_ref for edge in outer_edges],
            [edge.edge_id for edge in outer_edges],
            "Edges touching the outer XY boundary of the current solid.",
            common_metadata,
        )
        candidate_builders["inner_edges"] = (
            "Inner Edges",
            "edge",
            [edge.edge_ref for edge in inner_edges],
            [edge.edge_id for edge in inner_edges],
            "Edges away from the outer XY boundary, often produced by interior cuts, grooves, or notches.",
            common_metadata,
        )
        candidate_builders["inner_bottom_edges"] = (
            "Inner Bottom Edges",
            "edge",
            [edge.edge_ref for edge in inner_bottom_edges],
            [edge.edge_id for edge in inner_bottom_edges],
            "Lowest interior edges across the current solid, typically the bottom edge(s) of a recess, groove, or notch.",
            {
                **common_metadata,
                "anchor_role": "inner_bottom_edge_midpoint",
                "dominant_ref_id": (
                    inner_bottom_edges[0].edge_ref if inner_bottom_edges else None
                ),
            },
        )
        candidate_builders["inner_bottom_side_edges"] = (
            "Inner Bottom Side Edges",
            "edge",
            [edge.edge_ref for edge in inner_bottom_side_edges],
            [edge.edge_id for edge in inner_bottom_side_edges],
            "Lowest interior edges that are not primarily aligned with the dominant body axis, useful for triangular or V-shaped recess fillets.",
            {
                **common_metadata,
                "anchor_role": "inner_bottom_side_edge_midpoint",
                "excluded_alignment_axis": primary_axis,
                "dominant_ref_id": (
                    inner_bottom_side_edges[0].edge_ref
                    if inner_bottom_side_edges
                    else None
                ),
            },
        )
        candidate_builders["inner_top_edges"] = (
            "Inner Top Edges",
            "edge",
            [edge.edge_ref for edge in inner_top_edges],
            [edge.edge_id for edge in inner_top_edges],
            "Highest interior edges across the current solid, typically the top rim of a recess, groove, or notch.",
            {
                **common_metadata,
                "anchor_role": "inner_top_edge_midpoint",
                "dominant_ref_id": (
                    inner_top_edges[0].edge_ref if inner_top_edges else None
                ),
            },
        )
        candidate_builders["opening_rim_edges"] = (
            "Opening Rim Edges",
            "edge",
            [edge.edge_ref for edge in opening_rim_edges],
            [edge.edge_id for edge in opening_rim_edges],
            "High-confidence interior rim edges around openings, recess mouths, and notch lips.",
            _host_role_metadata(
                base_metadata={
                    **common_metadata,
                    "anchor_role": "opening_rim_edge",
                    "dominant_ref_id": (
                        opening_rim_edges[0].edge_ref if opening_rim_edges else None
                    ),
                },
                primary_role="opening_rim",
                role_confidence="medium",
                role_candidates=["opening_rim", "notch_rim", "cut_lip"],
            ),
        )
        candidate_builders["primary_axis_outer_edges"] = (
            "Primary-Axis Outer Edges",
            "edge",
            [edge.edge_ref for edge in primary_axis_outer_edges],
            [edge.edge_id for edge in primary_axis_outer_edges],
            "Outer edges whose span aligns with the dominant body axis, useful for edge-aligned side edits.",
            {
                **common_metadata,
                "alignment_axis": primary_axis,
                "dominant_ref_id": (
                    primary_axis_outer_edges[0].edge_ref
                    if primary_axis_outer_edges
                    else None
                ),
            },
        )
        for axis_label, axis_name, axis_outer, axis_top_outer, axis_bottom_outer in (
            ("x", "X", x_parallel_outer_edges, x_parallel_top_outer_edges, x_parallel_bottom_outer_edges),
            ("y", "Y", y_parallel_outer_edges, y_parallel_top_outer_edges, y_parallel_bottom_outer_edges),
            ("z", "Z", z_parallel_outer_edges, z_parallel_top_outer_edges, z_parallel_bottom_outer_edges),
        ):
            candidate_builders[f"{axis_label}_parallel_outer_edges"] = (
                f"{axis_name}-Parallel Outer Edges",
                "edge",
                [edge.edge_ref for edge in axis_outer],
                [edge.edge_id for edge in axis_outer],
                f"Outer straight edges whose dominant span runs parallel to the global {axis_name} axis.",
                {
                    **common_metadata,
                    "alignment_axis": axis_name,
                    "dominant_ref_id": axis_outer[0].edge_ref if axis_outer else None,
                },
            )
            candidate_builders[f"{axis_label}_parallel_top_outer_edges"] = (
                f"{axis_name}-Parallel Top Outer Edges",
                "edge",
                [edge.edge_ref for edge in axis_top_outer],
                [edge.edge_id for edge in axis_top_outer],
                f"Top outer straight edges whose dominant span runs parallel to the global {axis_name} axis.",
                {
                    **common_metadata,
                    "alignment_axis": axis_name,
                    "target_axis_side": "max",
                    "target_coordinate": round(max_z, 6),
                    "dominant_ref_id": (
                        axis_top_outer[0].edge_ref if axis_top_outer else None
                    ),
                },
            )
            candidate_builders[f"{axis_label}_parallel_bottom_outer_edges"] = (
                f"{axis_name}-Parallel Bottom Outer Edges",
                "edge",
                [edge.edge_ref for edge in axis_bottom_outer],
                [edge.edge_id for edge in axis_bottom_outer],
                f"Bottom outer straight edges whose dominant span runs parallel to the global {axis_name} axis.",
                {
                    **common_metadata,
                    "alignment_axis": axis_name,
                    "target_axis_side": "min",
                    "target_coordinate": round(min_z, 6),
                    "dominant_ref_id": (
                        axis_bottom_outer[0].edge_ref if axis_bottom_outer else None
                    ),
                },
            )
        candidate_builders["top_outer_edges"] = (
            "Top Outer Edges",
            "edge",
            [edge.edge_ref for edge in top_outer_edges],
            [edge.edge_id for edge in top_outer_edges],
            "Edges near +Z that also lie on the outer XY boundary.",
            {
                **common_metadata,
                "target_axis_side": "max",
                "target_coordinate": round(max_z, 6),
            },
        )
        candidate_builders["front_top_edges"] = (
            "Front Top Edges",
            "edge",
            [edge.edge_ref for edge in front_top_edges],
            [edge.edge_id for edge in front_top_edges],
            "Edges shared by the front face and the top boundary, useful for front-face sketches anchored at the top edge.",
            _edge_anchor_metadata(
                front_top_edges,
                anchor_role="top_edge_midpoint",
                face_label="front",
            ),
        )
        candidate_builders["back_top_edges"] = (
            "Back Top Edges",
            "edge",
            [edge.edge_ref for edge in back_top_edges],
            [edge.edge_id for edge in back_top_edges],
            "Edges shared by the back face and the top boundary, useful for back-face sketches anchored at the top edge.",
            _edge_anchor_metadata(
                back_top_edges,
                anchor_role="top_edge_midpoint",
                face_label="back",
            ),
        )
        candidate_builders["left_top_edges"] = (
            "Left Top Edges",
            "edge",
            [edge.edge_ref for edge in left_top_edges],
            [edge.edge_id for edge in left_top_edges],
            "Edges shared by the left face and the top boundary, useful for left-face sketches anchored at the top edge.",
            _edge_anchor_metadata(
                left_top_edges,
                anchor_role="top_edge_midpoint",
                face_label="left",
            ),
        )
        candidate_builders["right_top_edges"] = (
            "Right Top Edges",
            "edge",
            [edge.edge_ref for edge in right_top_edges],
            [edge.edge_id for edge in right_top_edges],
            "Edges shared by the right face and the top boundary, useful for right-face sketches anchored at the top edge.",
            _edge_anchor_metadata(
                right_top_edges,
                anchor_role="top_edge_midpoint",
                face_label="right",
            ),
        )
        candidate_builders["bottom_outer_edges"] = (
            "Bottom Outer Edges",
            "edge",
            [edge.edge_ref for edge in bottom_outer_edges],
            [edge.edge_id for edge in bottom_outer_edges],
            "Edges near -Z that also lie on the outer XY boundary.",
            {
                **common_metadata,
                "target_axis_side": "min",
                "target_coordinate": round(min_z, 6),
            },
        )
        candidate_builders["front_bottom_edges"] = (
            "Front Bottom Edges",
            "edge",
            [edge.edge_ref for edge in front_bottom_edges],
            [edge.edge_id for edge in front_bottom_edges],
            "Edges shared by the front face and the bottom boundary.",
            _edge_anchor_metadata(
                front_bottom_edges,
                anchor_role="bottom_edge_midpoint",
                face_label="front",
            ),
        )
        candidate_builders["back_bottom_edges"] = (
            "Back Bottom Edges",
            "edge",
            [edge.edge_ref for edge in back_bottom_edges],
            [edge.edge_id for edge in back_bottom_edges],
            "Edges shared by the back face and the bottom boundary.",
            _edge_anchor_metadata(
                back_bottom_edges,
                anchor_role="bottom_edge_midpoint",
                face_label="back",
            ),
        )
        candidate_builders["left_bottom_edges"] = (
            "Left Bottom Edges",
            "edge",
            [edge.edge_ref for edge in left_bottom_edges],
            [edge.edge_id for edge in left_bottom_edges],
            "Edges shared by the left face and the bottom boundary.",
            _edge_anchor_metadata(
                left_bottom_edges,
                anchor_role="bottom_edge_midpoint",
                face_label="left",
            ),
        )
        candidate_builders["right_bottom_edges"] = (
            "Right Bottom Edges",
            "edge",
            [edge.edge_ref for edge in right_bottom_edges],
            [edge.edge_id for edge in right_bottom_edges],
            "Edges shared by the right face and the bottom boundary.",
            _edge_anchor_metadata(
                right_bottom_edges,
                anchor_role="bottom_edge_midpoint",
                face_label="right",
            ),
        )
        candidate_builders["front_inner_edges"] = (
            "Front Inner Edges",
            "edge",
            [edge.edge_ref for edge in front_inner_edges],
            [edge.edge_id for edge in front_inner_edges],
            "Interior edges adjacent to the front face, useful after front-face cuts and grooves.",
            {**common_metadata, **_sketch_frame_metadata("front")},
        )
        candidate_builders["back_inner_edges"] = (
            "Back Inner Edges",
            "edge",
            [edge.edge_ref for edge in back_inner_edges],
            [edge.edge_id for edge in back_inner_edges],
            "Interior edges adjacent to the back face, useful after back-face cuts and grooves.",
            {**common_metadata, **_sketch_frame_metadata("back")},
        )
        candidate_builders["left_inner_edges"] = (
            "Left Inner Edges",
            "edge",
            [edge.edge_ref for edge in left_inner_edges],
            [edge.edge_id for edge in left_inner_edges],
            "Interior edges adjacent to the left face, useful after left-face cuts and grooves.",
            {**common_metadata, **_sketch_frame_metadata("left")},
        )
        candidate_builders["right_inner_edges"] = (
            "Right Inner Edges",
            "edge",
            [edge.edge_ref for edge in right_inner_edges],
            [edge.edge_id for edge in right_inner_edges],
            "Interior edges adjacent to the right face, useful after right-face cuts and grooves.",
            {**common_metadata, **_sketch_frame_metadata("right")},
        )
        candidate_builders["front_inner_bottom_edges"] = (
            "Front Inner Bottom Edges",
            "edge",
            [edge.edge_ref for edge in front_inner_bottom_edges],
            [edge.edge_id for edge in front_inner_bottom_edges],
            "Lowest interior front-face edges, typically the bottom edge of a front-side groove or notch.",
            _edge_anchor_metadata(
                front_inner_bottom_edges,
                anchor_role="inner_bottom_edge_midpoint",
                face_label="front",
            ),
        )
        candidate_builders["back_inner_bottom_edges"] = (
            "Back Inner Bottom Edges",
            "edge",
            [edge.edge_ref for edge in back_inner_bottom_edges],
            [edge.edge_id for edge in back_inner_bottom_edges],
            "Lowest interior back-face edges, typically the bottom edge of a back-side groove or notch.",
            _edge_anchor_metadata(
                back_inner_bottom_edges,
                anchor_role="inner_bottom_edge_midpoint",
                face_label="back",
            ),
        )
        candidate_builders["left_inner_bottom_edges"] = (
            "Left Inner Bottom Edges",
            "edge",
            [edge.edge_ref for edge in left_inner_bottom_edges],
            [edge.edge_id for edge in left_inner_bottom_edges],
            "Lowest interior left-face edges, typically the bottom edge of a left-side groove or notch.",
            _edge_anchor_metadata(
                left_inner_bottom_edges,
                anchor_role="inner_bottom_edge_midpoint",
                face_label="left",
            ),
        )
        candidate_builders["right_inner_bottom_edges"] = (
            "Right Inner Bottom Edges",
            "edge",
            [edge.edge_ref for edge in right_inner_bottom_edges],
            [edge.edge_id for edge in right_inner_bottom_edges],
            "Lowest interior right-face edges, typically the bottom edge of a right-side groove or notch.",
            _edge_anchor_metadata(
                right_inner_bottom_edges,
                anchor_role="inner_bottom_edge_midpoint",
                face_label="right",
            ),
        )
        candidate_builders["front_inner_top_edges"] = (
            "Front Inner Top Edges",
            "edge",
            [edge.edge_ref for edge in front_inner_top_edges],
            [edge.edge_id for edge in front_inner_top_edges],
            "Highest interior front-face edges after a front-side cut or groove.",
            _edge_anchor_metadata(
                front_inner_top_edges,
                anchor_role="inner_top_edge_midpoint",
                face_label="front",
            ),
        )
        candidate_builders["back_inner_top_edges"] = (
            "Back Inner Top Edges",
            "edge",
            [edge.edge_ref for edge in back_inner_top_edges],
            [edge.edge_id for edge in back_inner_top_edges],
            "Highest interior back-face edges after a back-side cut or groove.",
            _edge_anchor_metadata(
                back_inner_top_edges,
                anchor_role="inner_top_edge_midpoint",
                face_label="back",
            ),
        )
        candidate_builders["left_inner_top_edges"] = (
            "Left Inner Top Edges",
            "edge",
            [edge.edge_ref for edge in left_inner_top_edges],
            [edge.edge_id for edge in left_inner_top_edges],
            "Highest interior left-face edges after a left-side cut or groove.",
            _edge_anchor_metadata(
                left_inner_top_edges,
                anchor_role="inner_top_edge_midpoint",
                face_label="left",
            ),
        )
        candidate_builders["right_inner_top_edges"] = (
            "Right Inner Top Edges",
            "edge",
            [edge.edge_ref for edge in right_inner_top_edges],
            [edge.edge_id for edge in right_inner_top_edges],
            "Highest interior right-face edges after a right-side cut or groove.",
            _edge_anchor_metadata(
                right_inner_top_edges,
                anchor_role="inner_top_edge_midpoint",
                face_label="right",
            ),
        )

        candidate_sets: list[TopologyCandidateSet] = []
        for hint in selection_hints:
            candidate = candidate_builders.get(hint)
            if candidate is None:
                continue
            label, entity_type, ref_ids, entity_ids, rationale, metadata = candidate
            if not ref_ids:
                continue
            candidate_sets.append(
                TopologyCandidateSet(
                    candidate_id=hint,
                    label=label,
                    entity_type=entity_type,
                    ref_ids=ref_ids,
                    entity_ids=entity_ids,
                    rationale=rationale,
                    metadata=metadata,
                )
            )
        return self._annotate_family_priority_on_topology_candidate_sets(
            candidate_sets,
            family_ids=family_ids,
            selection_hints=selection_hints,
        )

    def _annotate_family_priority_on_topology_candidate_sets(
        self,
        candidate_sets: list[TopologyCandidateSet],
        *,
        family_ids: list[str],
        selection_hints: list[str],
    ) -> list[TopologyCandidateSet]:
        normalized_family_ids: list[str] = []
        seen_family_ids: set[str] = set()
        for family_id in family_ids:
            normalized = str(family_id or "").strip()
            if not normalized or normalized in seen_family_ids:
                continue
            seen_family_ids.add(normalized)
            normalized_family_ids.append(normalized)
        if not candidate_sets or not normalized_family_ids:
            return candidate_sets

        family_preferences: dict[str, dict[str, tuple[str, ...]]] = {
            "explicit_anchor_hole": {
                "candidate_ids": (
                    "mating_faces",
                    "top_inner_planar_faces",
                    "bottom_inner_planar_faces",
                    "upward_planar_faces",
                    "downward_planar_faces",
                    "top_faces",
                    "bottom_faces",
                    "opening_rim_edges",
                ),
                "host_roles": (
                    "mating_face",
                    "closure_landing",
                    "inner_planar_host",
                    "opening_rim",
                    "shell_interior",
                ),
            },
            "named_face_local_edit": {
                "candidate_ids": (
                    "front_faces",
                    "back_faces",
                    "left_faces",
                    "right_faces",
                    "top_faces",
                    "bottom_faces",
                    "opening_rim_edges",
                    "front_inner_edges",
                    "back_inner_edges",
                    "left_inner_edges",
                    "right_inner_edges",
                ),
                "host_roles": (
                    "opening_rim",
                    "notch_rim",
                    "cut_lip",
                    "shell_exterior",
                    "shell_interior",
                    "mating_face",
                ),
            },
            "slots": {
                "candidate_ids": (
                    "opening_rim_edges",
                    "front_inner_edges",
                    "back_inner_edges",
                    "left_inner_edges",
                    "right_inner_edges",
                    "front_faces",
                    "back_faces",
                    "left_faces",
                    "right_faces",
                ),
                "host_roles": (
                    "opening_rim",
                    "cut_lip",
                    "shell_interior",
                    "shell_exterior",
                ),
            },
            "nested_hollow_section": {
                "candidate_ids": (
                    "shell_interior_faces",
                    "mating_faces",
                    "opening_rim_edges",
                    "top_inner_planar_faces",
                    "bottom_inner_planar_faces",
                ),
                "host_roles": (
                    "shell_interior",
                    "cavity_host",
                    "mating_face",
                    "opening_rim",
                ),
            },
            "half_shell": {
                "candidate_ids": (
                    "split_plane_faces",
                    "mating_faces",
                    "shell_exterior_faces",
                    "shell_interior_faces",
                    "opening_rim_edges",
                ),
                "host_roles": (
                    "split_plane",
                    "mating_face",
                    "shell_exterior",
                    "shell_interior",
                ),
            },
            "general_geometry": {
                "candidate_ids": (
                    "primary_outer_faces",
                    "shell_exterior_faces",
                    "shell_interior_faces",
                    "split_plane_faces",
                ),
                "host_roles": (
                    "shell_exterior",
                    "shell_interior",
                    "split_plane",
                ),
            },
            "spherical_recess": {
                "candidate_ids": (
                    "shell_exterior_faces",
                    "shell_interior_faces",
                    "mating_faces",
                    "top_faces",
                    "bottom_faces",
                ),
                "host_roles": (
                    "shell_exterior",
                    "shell_interior",
                    "mating_face",
                ),
            },
        }
        hint_rank = {
            str(hint).strip(): idx for idx, hint in enumerate(selection_hints)
        }
        ranked_candidates: list[tuple[int, int, TopologyCandidateSet]] = []

        for candidate in candidate_sets:
            metadata = candidate.metadata if isinstance(candidate.metadata, dict) else {}
            host_role = str(metadata.get("host_role") or "").strip()
            semantic_host_roles = [
                str(item).strip()
                for item in (metadata.get("semantic_host_roles") or [])
                if str(item).strip()
            ]
            candidate_family_scores: list[tuple[str, int]] = []
            for family_id in normalized_family_ids:
                preference = family_preferences.get(family_id)
                if preference is None:
                    continue
                candidate_score = 0
                candidate_ids = preference.get("candidate_ids", ())
                if candidate.candidate_id in candidate_ids:
                    candidate_score = max(
                        candidate_score,
                        100 - candidate_ids.index(candidate.candidate_id),
                    )
                host_roles = preference.get("host_roles", ())
                if host_role in host_roles:
                    candidate_score = max(
                        candidate_score,
                        60 - host_roles.index(host_role),
                    )
                if any(role in host_roles for role in semantic_host_roles):
                    candidate_score = max(candidate_score, 45)
                hint_boost = self._family_specific_topology_hint_boost(
                    family_id=family_id,
                    candidate_id=candidate.candidate_id,
                    selection_hints=selection_hints,
                )
                if hint_boost > 0:
                    candidate_score = max(candidate_score, 200 + hint_boost)
                if candidate_score > 0:
                    candidate_family_scores.append((family_id, candidate_score))
            candidate_family_scores.sort(key=lambda item: (-item[1], item[0]))
            update_payload: dict[str, Any] = {}
            best_score = 0
            if candidate_family_scores:
                best_score = candidate_family_scores[0][1]
                update_payload["family_id"] = candidate_family_scores[0][0]
                update_payload["family_ids"] = [
                    family_id for family_id, _ in candidate_family_scores
                ]
                if candidate.ref_ids:
                    update_payload["preferred_ref_id"] = candidate.ref_ids[0]
                if candidate.entity_ids:
                    update_payload["preferred_entity_id"] = candidate.entity_ids[0]
            ranked_candidates.append(
                (
                    -best_score,
                    hint_rank.get(candidate.candidate_id, len(selection_hints)),
                    candidate.model_copy(update=update_payload),
                )
            )

        ranked_candidates.sort(key=lambda item: (item[0], item[1], item[2].candidate_id))
        return [candidate for _, _, candidate in ranked_candidates]

    def _family_specific_topology_hint_boost(
        self,
        *,
        family_id: str,
        candidate_id: str,
        selection_hints: list[str],
    ) -> int:
        normalized_hints = [
            str(hint).strip()
            for hint in selection_hints
            if str(hint).strip()
        ]
        if not normalized_hints:
            return 0

        boosted_candidate_ids: list[str] = []

        def _append_unique(candidate_name: str) -> None:
            if candidate_name not in boosted_candidate_ids:
                boosted_candidate_ids.append(candidate_name)

        if family_id == "explicit_anchor_hole":
            directional_hint_present = any(
                hint in {"bottom_faces", "downward_planar_faces", "top_faces", "upward_planar_faces"}
                for hint in normalized_hints
            )
            expansion_map: dict[str, tuple[str, ...]] = {
                "bottom_faces": (
                    "bottom_faces",
                    "downward_planar_faces",
                    "bottom_inner_planar_faces",
                ),
                "downward_planar_faces": (
                    "downward_planar_faces",
                    "bottom_faces",
                    "bottom_inner_planar_faces",
                ),
                "top_faces": (
                    "top_faces",
                    "upward_planar_faces",
                    "top_inner_planar_faces",
                ),
                "upward_planar_faces": (
                    "upward_planar_faces",
                    "top_faces",
                    "top_inner_planar_faces",
                ),
                "mating_faces": ("mating_faces",),
            }
            for hint in normalized_hints:
                for expanded_candidate_id in expansion_map.get(hint, ()):
                    if expanded_candidate_id == "mating_faces" and directional_hint_present:
                        continue
                    _append_unique(expanded_candidate_id)
        elif family_id == "named_face_local_edit":
            expansion_map = {
                "front_faces": ("front_faces", "front_inner_edges"),
                "back_faces": ("back_faces", "back_inner_edges"),
                "left_faces": ("left_faces", "left_inner_edges"),
                "right_faces": ("right_faces", "right_inner_edges"),
                "top_faces": ("top_faces",),
                "bottom_faces": ("bottom_faces",),
                "opening_rim_edges": ("opening_rim_edges",),
            }
            for hint in normalized_hints:
                for expanded_candidate_id in expansion_map.get(hint, ()):
                    _append_unique(expanded_candidate_id)

        if candidate_id not in boosted_candidate_ids:
            return 0
        return max(1, 40 - boosted_candidate_ids.index(candidate_id))

    def _sort_topology_faces_by_candidate_priority(
        self,
        faces: list[TopologyFaceEntity],
        candidate_sets: list[TopologyCandidateSet],
        priority_ids: list[str],
    ) -> list[TopologyFaceEntity]:
        if not priority_ids:
            return faces
        priority_map = self._candidate_ref_priority_map(
            candidate_sets=candidate_sets,
            priority_ids=priority_ids,
        )
        return sorted(
            faces,
            key=lambda item: (
                priority_map.get(item.face_ref, len(priority_ids)),
                item.face_ref,
            ),
        )

    def _sort_topology_edges_by_candidate_priority(
        self,
        edges: list[TopologyEdgeEntity],
        candidate_sets: list[TopologyCandidateSet],
        priority_ids: list[str],
    ) -> list[TopologyEdgeEntity]:
        if not priority_ids:
            return edges
        priority_map = self._candidate_ref_priority_map(
            candidate_sets=candidate_sets,
            priority_ids=priority_ids,
        )
        return sorted(
            edges,
            key=lambda item: (
                priority_map.get(item.edge_ref, len(priority_ids)),
                item.edge_ref,
            ),
        )

    def _candidate_ref_priority_map(
        self,
        candidate_sets: list[TopologyCandidateSet],
        priority_ids: list[str],
    ) -> dict[str, int]:
        priority_map: dict[str, int] = {}
        sets_by_id = {item.candidate_id: item for item in candidate_sets}
        for rank, candidate_id in enumerate(priority_ids):
            candidate = sets_by_id.get(candidate_id)
            if candidate is None:
                continue
            for ref_id in candidate.ref_ids:
                priority_map.setdefault(ref_id, rank)
        return priority_map

    def _topology_extents(
        self,
        faces: list[TopologyFaceEntity],
        edges: list[TopologyEdgeEntity],
    ) -> tuple[float, float, float, float, float, float] | None:
        bbox_items = [item.bbox for item in [*faces, *edges] if item.bbox is not None]
        if not bbox_items:
            return None
        return (
            min(item.xmin for item in bbox_items),
            max(item.xmax for item in bbox_items),
            min(item.ymin for item in bbox_items),
            max(item.ymax for item in bbox_items),
            min(item.zmin for item in bbox_items),
            max(item.zmax for item in bbox_items),
        )

    def _extent_tolerance(self, min_value: float, max_value: float) -> float:
        span = max(0.0, max_value - min_value)
        return max(1e-4, span * 0.03)

    def _near_max(self, value: float, max_value: float, tolerance: float) -> bool:
        return float(value) >= float(max_value) - float(tolerance)

    def _near_min(self, value: float, min_value: float, tolerance: float) -> bool:
        return float(value) <= float(min_value) + float(tolerance)

    def _empty_snapshot(self) -> CADStateSnapshot:
        """Return empty snapshot for error cases."""
        return CADStateSnapshot(
            step=0,
            features=[],
            geometry=GeometryInfo(
                solids=0,
                faces=0,
                edges=0,
                volume=0.0,
                bbox=[0.0, 0.0, 0.0],
                center_of_mass=[0.0, 0.0, 0.0],
                surface_area=0.0,
                bbox_min=[0.0, 0.0, 0.0],
                bbox_max=[0.0, 0.0, 0.0],
            ),
            issues=[],
            warnings=[],
            blockers=[],
            images=[],
            geometry_objects=None,
            sketch_state=None,
            success=False,
            error=None,
        )

    def _generate_suggestions(
        self,
        snapshot: CADStateSnapshot,
        action_type: CADActionType,
    ) -> list[str]:
        """Generate AI suggestions based on geometry analysis."""
        suggestions: list[str] = []

        # Geometry-based suggestions
        if snapshot.geometry.volume > 0:
            volume = snapshot.geometry.volume
            if volume < 1000:
                suggestions.append(
                    "Volume is quite small (<1000mm³), consider checking extrude dimensions",
                )
            elif volume > 10000000:
                suggestions.append(
                    "Large volume detected, consider adding draft angles for manufacturability",
                )

        # Issue-based suggestions
        if snapshot.issues:
            suggestions.append(
                f"Geometry has {len(snapshot.issues)} issues that should be addressed",
            )

        if action_type == CADActionType.HOLE:
            suggestions.append(
                "Consider adding counterbore or countersink if using flat-head screws",
            )

        if action_type == CADActionType.FILLET:
            suggestions.append(
                "Verify fillet radius meets design specifications and manufacturing constraints",
            )

        return suggestions

    def _generate_completeness(
        self,
        snapshot: CADStateSnapshot,
        action_history: list[ActionHistoryEntry],
    ) -> CompletenessInfo:
        """Generate requirement-agnostic completeness diagnostics for planner context."""
        current_step = snapshot.step
        has_solids = snapshot.geometry.solids > 0
        has_profile = snapshot.geometry.edges > 0

        missing_features: list[str] = []
        if not has_solids:
            missing_features.append("solid body" if has_profile else "base sketch")
        if snapshot.issues:
            missing_features.extend(snapshot.issues)

        expected_steps = max(
            current_step,
            3 if has_solids else 2,
        )
        can_continue = True
        confidence = 1.0 if has_solids and not snapshot.issues else 0.8 if has_solids else 0.5

        return CompletenessInfo(
            expected_steps=expected_steps,
            current_step=current_step,
            missing_features=missing_features,
            can_continue=can_continue,
            confidence=confidence,
        )

    async def _handle_modify_action(self, request: CADActionInput) -> CADActionOutput:
        """Handle MODIFY_ACTION to adjust previous action parameters."""
        if not request.session_id:
            return CADActionOutput(
                success=False,
                stdout="",
                stderr="MODIFY_ACTION requires session_id",
                error_code=SandboxErrorCode.INVALID_REQUEST,
                error_message="MODIFY_ACTION requires session_id",
                snapshot=self._empty_snapshot(),
                executed_action={"type": "modify_action", "params": {}},
                step_file=None,
                output_files=[],
                artifacts=[],
                action_history=[],
                suggestions=[],
                completeness=None,
            )

        target_step = request.action_params.get("target_step")
        modification = request.action_params.get("modification")
        new_params = request.action_params.get("new_params", {})

        if target_step is None or not isinstance(target_step, int):
            return CADActionOutput(
                success=False,
                stdout="",
                stderr="MODIFY_ACTION requires target_step parameter",
                error_code=SandboxErrorCode.INVALID_REQUEST,
                error_message="MODIFY_ACTION requires target_step parameter",
                snapshot=self._empty_snapshot(),
                executed_action={
                    "type": "modify_action",
                    "params": request.action_params,
                },
                step_file=None,
                output_files=[],
                artifacts=[],
                action_history=[],
                suggestions=[],
                completeness=None,
            )

        # Get session history
        history = self._session_manager.get_session_history(request.session_id)
        if not history or target_step > len(history) or target_step < 1:
            return CADActionOutput(
                success=False,
                stdout="",
                stderr=f"Invalid target_step: {target_step}",
                error_code=SandboxErrorCode.INVALID_REQUEST,
                error_message=f"Invalid target_step: {target_step}",
                snapshot=self._empty_snapshot(),
                executed_action={
                    "type": "modify_action",
                    "params": request.action_params,
                },
                step_file=None,
                output_files=[],
                artifacts=[],
                action_history=history or [],
                suggestions=[],
                completeness=None,
            )

        # For this implementation, we'll replay actions with the modification
        # In a full implementation, this would rebuild from step 1 to target_step-1
        # with the modified parameters
        original_entry = history[target_step - 1]

        if modification == "adjust":
            # Merge new params with original params
            merged_params = {**original_entry.action_params, **new_params}

            # Rebuild model by executing all actions up to target_step
            # Note: This is a simplified implementation
            code = self._action_to_code(original_entry.action_type, merged_params)
            if not code:
                return CADActionOutput(
                    success=False,
                    stdout="",
                    stderr="Failed to generate code for modified action",
                    error_code=SandboxErrorCode.INVALID_REQUEST,
                    error_message="Failed to generate code for modified action",
                    snapshot=self._empty_snapshot(),
                    executed_action={
                        "type": "modify_action",
                        "params": request.action_params,
                    },
                    step_file=None,
                    output_files=[],
                    artifacts=[],
                    action_history=history or [],
                    suggestions=[],
                    completeness=None,
                )

            result = await self._runner.execute(
                code=code,
                timeout=request.timeout_seconds,
                requirement_text=None,
            )

            # Update history entry
            updated_snapshot = self._parse_snapshot(result)
            updated_entry = ActionHistoryEntry(
                step=original_entry.step,
                action_type=original_entry.action_type,
                action_params=merged_params,
                result_snapshot=updated_snapshot,
                success=result.success,
                error=result.error_message if not result.success else None,
                warnings=list(updated_snapshot.warnings),
                blockers=list(updated_snapshot.blockers),
            )

            # Update session history
            session = self._session_manager.get_or_create_session(request.session_id)
            session.history[target_step - 1] = updated_entry

            # Build response
            filenames = list(
                dict.fromkeys(
                    [*result.output_files, *result.output_file_contents.keys()]
                )
            )
            artifacts: list[SandboxArtifact] = []

            for filename in filenames:
                content = result.output_file_contents.get(filename)
                artifact = SandboxArtifact(
                    filename=filename,
                    uri=f"sandbox://artifacts/{filename}",
                    mime_type=self._resolve_mime_type(filename),
                    size_bytes=len(content) if content is not None else 0,
                    content_base64=(
                        base64.b64encode(content).decode("ascii")
                        if content is not None and request.include_artifact_content
                        else None
                    ),
                )
                artifacts.append(artifact)

            return CADActionOutput(
                success=result.success,
                stdout=result.stdout,
                stderr=result.stderr,
                error_code=(
                    SandboxErrorCode.NONE
                    if result.success
                    else self._map_error_code(result.error_message, result.stderr)
                ),
                error_message=result.error_message,
                snapshot=self._parse_snapshot(result),
                executed_action={
                    "type": "modify_action",
                    "params": request.action_params,
                },
                step_file=next((f for f in filenames if f.endswith(".step")), None),
                output_files=filenames,
                artifacts=artifacts,
                action_history=history or [],
                suggestions=[f"Action step {target_step} has been modified"],
                completeness=self._generate_completeness(
                    self._parse_snapshot(result), history or []
                ),
            )

        else:
            return CADActionOutput(
                success=False,
                stdout="",
                stderr=f"Unsupported modification type: {modification}",
                error_code=SandboxErrorCode.INVALID_REQUEST,
                error_message=f"Unsupported modification type: {modification}",
                snapshot=self._empty_snapshot(),
                executed_action={
                    "type": "modify_action",
                    "params": request.action_params,
                },
                step_file=None,
                output_files=[],
                artifacts=[],
                action_history=history or [],
                suggestions=[],
                completeness=None,
            )
