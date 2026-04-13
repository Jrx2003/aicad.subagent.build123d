import pytest

from sandbox_mcp_server.contracts import (
    ActionHistoryEntry,
    CADActionType,
    CADStateSnapshot,
    GeometryInfo,
    RequirementClauseInterpretation,
    RequirementClauseStatus,
    RequirementCheck,
    RequirementCheckStatus,
    ValidateRequirementInput,
    ValidateRequirementOutput,
)
from sandbox_mcp_server.service import SandboxMCPService
from sandbox_mcp_server.validation_evidence import RequirementEvidenceBuilder
from sandbox_mcp_server.validation_interpretation import interpret_requirement_clauses
from sandbox_mcp_server.validation_llm import (
    ValidationLLMClauseDecision,
    ValidationLLMOutput,
)


class _DummyRunner:
    async def execute(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        raise RuntimeError("not used in this test")


class _ValidationAdjudicatorStub:
    async def adjudicate(self, **kwargs):  # type: ignore[no-untyped-def]
        clauses = kwargs.get("clauses") or []
        if not clauses:
            return None
        first_clause = clauses[0]
        return ValidationLLMOutput(
            summary="llm resolved the generic clause",
            clauses=[
                ValidationLLMClauseDecision(
                    clause_id=first_clause.clause_id,
                    status=RequirementClauseStatus.VERIFIED,
                    evidence="llm adjudicator resolved the unresolved clause from broader evidence",
                    decision_hints=[],
                    confidence=0.95,
                )
            ],
        )


def _snapshot(
    *,
    step: int,
    solids: int = 1,
    faces: int = 6,
    edges: int = 12,
    volume: float = 100.0,
    bbox: list[float] | None = None,
) -> CADStateSnapshot:
    return CADStateSnapshot(
        step=step,
        features=[],
        geometry=GeometryInfo(
            solids=solids,
            faces=faces,
            edges=edges,
            volume=volume,
            bbox=bbox or [10.0, 10.0, 10.0],
            center_of_mass=[0.0, 0.0, 0.0],
            surface_area=50.0,
            bbox_min=[0.0, 0.0, 0.0],
            bbox_max=bbox or [10.0, 10.0, 10.0],
        ),
        issues=[],
        warnings=[],
        blockers=[],
        images=[],
        sketch_state=None,
        geometry_objects=None,
        topology_index=None,
        success=True,
        error=None,
    )


def test_validate_requirement_output_accepts_clause_interpretation_fields() -> None:
    output = ValidateRequirementOutput(
        success=True,
        session_id="session-1",
        clause_interpretations=[
            RequirementClauseInterpretation(
                clause_id="clause-1",
                clause_text="Add a centered hole",
                status=RequirementClauseStatus.VERIFIED,
                evidence="hole center found in topology",
                observation_tags=["topology"],
                decision_hints=["keep"],
            )
        ],
        coverage_confidence=0.75,
        insufficient_evidence=True,
        observation_tags=["geometry", "process"],
        decision_hints=["inspect history"],
    )

    assert output.clause_interpretations[0].status == RequirementClauseStatus.VERIFIED
    assert output.coverage_confidence == 0.75
    assert output.insufficient_evidence is True
    assert output.observation_tags == ["geometry", "process"]
    assert output.decision_hints == ["inspect history"]


def test_body_dimension_clause_verifies_from_bbox_geometry() -> None:
    bundle = RequirementEvidenceBuilder.build(
        snapshot=_snapshot(step=1, solids=1, bbox=[60.0, 40.0, 8.0]),
        history=[],
        requirements={"description": "Create a 60 mm by 40 mm by 8 mm plate."},
        requirement_text="Create a 60 mm by 40 mm by 8 mm plate.",
    )

    summary = interpret_requirement_clauses(
        bundle=bundle,
        requirements={"description": "Create a 60 mm by 40 mm by 8 mm plate."},
        requirement_text="Create a 60 mm by 40 mm by 8 mm plate.",
    )

    assert summary.clause_interpretations[0].status == RequirementClauseStatus.VERIFIED
    assert summary.coverage_confidence == 1.0


@pytest.mark.asyncio
async def test_validate_requirement_applies_validation_llm_adjudication_to_unresolved_clause() -> None:
    service = SandboxMCPService(
        runner=_DummyRunner(),
        validation_adjudicator=_ValidationAdjudicatorStub(),
    )
    session_id = "session-validation-llm-adjudication"
    service._session_manager.clear_session(session_id)
    service._session_manager.append_action(
        session_id,
        ActionHistoryEntry(
            step=1,
            action_type=CADActionType.SNAPSHOT,
            action_params={"source": "execute_build123d"},
            result_snapshot=_snapshot(step=1, solids=1),
            success=True,
            error=None,
        ),
    )

    result = await service.validate_requirement(
        ValidateRequirementInput(
            session_id=session_id,
            requirement_text="Make it elegant.",
            requirements={"description": "Make it elegant."},
        )
    )

    assert result.success is True
    assert result.clause_interpretations[0].status == RequirementClauseStatus.VERIFIED
    assert "llm:validation_adjudicated" in result.clause_interpretations[0].observation_tags
    assert result.insufficient_evidence is False


@pytest.mark.asyncio
async def test_validate_requirement_keeps_coordinate_clause_as_insufficient_evidence() -> None:
    service = SandboxMCPService(
        runner=_DummyRunner(),
        validation_adjudicator=_ValidationAdjudicatorStub(),
    )
    session_id = "session-validation-llm-localized-clause-guard"
    service._session_manager.clear_session(session_id)
    service._session_manager.append_action(
        session_id,
        ActionHistoryEntry(
            step=1,
            action_type=CADActionType.SNAPSHOT,
            action_params={"source": "execute_build123d"},
            result_snapshot=_snapshot(step=1, solids=1, bbox=[40.0, 20.0, 10.0]),
            success=True,
            error=None,
        ),
    )

    result = await service.validate_requirement(
        ValidateRequirementInput(
            session_id=session_id,
            requirement_text="Add a hole centered at x = 10.0 mm.",
            requirements={"description": "Add a hole centered at x = 10.0 mm."},
        )
    )

    assert result.success is True
    assert result.clause_interpretations[0].status != RequirementClauseStatus.VERIFIED
    assert "llm:validation_adjudicated" not in result.clause_interpretations[0].observation_tags
    assert result.is_complete is False


def test_interpretation_projects_unknown_high_level_clause_without_marking_complete() -> None:
    bundle = RequirementEvidenceBuilder.build(
        snapshot=_snapshot(step=1, solids=1),
        history=[],
        requirements={"description": "Make it elegant."},
        requirement_text="Make it elegant.",
    )

    summary = interpret_requirement_clauses(
        bundle=bundle,
        requirements={"description": "Make it elegant."},
        requirement_text="Make it elegant.",
    )

    assert summary.clause_interpretations[0].status == RequirementClauseStatus.INSUFFICIENT_EVIDENCE
    assert summary.insufficient_evidence == ["make_it_elegant"]
    assert summary.legacy_checks[0].status == RequirementCheckStatus.UNKNOWN


def test_single_dimension_clauses_reuse_passed_dimension_checks() -> None:
    requirement_text = "width 40, height 20, thickness 10"
    bundle = RequirementEvidenceBuilder.build(
        snapshot=_snapshot(step=1, solids=1, bbox=[40.0, 20.0, 10.0]),
        history=[],
        requirements={"description": requirement_text},
        requirement_text=requirement_text,
    )

    summary = interpret_requirement_clauses(
        bundle=bundle,
        requirements={"description": requirement_text},
        requirement_text=requirement_text,
        supplemental_checks=[
            RequirementCheck(
                check_id="dimension_width",
                label="Dimension 'width' is satisfied",
                status=RequirementCheckStatus.PASS,
                blocking=True,
                evidence="target=40.0, matched=40.0, bbox=[40.0, 20.0, 10.0]",
            ),
            RequirementCheck(
                check_id="dimension_height",
                label="Dimension 'height' is satisfied",
                status=RequirementCheckStatus.PASS,
                blocking=True,
                evidence="target=20.0, matched=20.0, bbox=[40.0, 20.0, 10.0]",
            ),
            RequirementCheck(
                check_id="dimension_thickness",
                label="Dimension 'thickness' is satisfied",
                status=RequirementCheckStatus.PASS,
                blocking=True,
                evidence="target=10.0, matched=10.0, bbox=[40.0, 20.0, 10.0]",
            ),
        ],
    )

    clause_status = {clause.clause_id: clause.status for clause in summary.clause_interpretations}
    assert clause_status["width_40"] == RequirementClauseStatus.VERIFIED
    assert clause_status["height_20"] == RequirementClauseStatus.VERIFIED
    assert clause_status["thickness_10"] == RequirementClauseStatus.VERIFIED
    assert summary.insufficient_evidence == []


def test_precise_pattern_dimension_clause_reuses_local_alignment_instead_of_bbox() -> None:
    requirement_text = (
        "Constrain the centers of these four circles to form a square array with a side length of 8.0mm."
    )
    bundle = RequirementEvidenceBuilder.build(
        snapshot=_snapshot(step=1, solids=1, bbox=[16.0, 16.0, 12.0]),
        history=[],
        requirements={"description": requirement_text},
        requirement_text=requirement_text,
    )

    summary = interpret_requirement_clauses(
        bundle=bundle,
        requirements={"description": requirement_text},
        requirement_text=requirement_text,
        supplemental_checks=[
            RequirementCheck(
                check_id="feature_local_anchor_alignment",
                label="Direct feature centers stay aligned with the requested local anchor layout",
                status=RequirementCheckStatus.PASS,
                blocking=True,
                evidence=(
                    "required_centers=[[4.0, 4.0], [4.0, -4.0], [-4.0, 4.0], [-4.0, -4.0]], "
                    "realized_centers=[[-4.0, -4.0], [4.0, -4.0], [-4.0, 4.0], [4.0, 4.0]]"
                ),
            ),
            RequirementCheck(
                check_id="feature_pattern",
                label="Contains required pattern feature",
                status=RequirementCheckStatus.PASS,
                blocking=True,
                evidence="execute_build123d_geometry_fallback=true, found repeated direct feature pattern in final geometry",
            ),
        ],
    )

    assert summary.clause_interpretations[0].status == RequirementClauseStatus.VERIFIED
    assert "requested_dimensions=[8.0]" not in summary.clause_interpretations[0].evidence


def test_pattern_layout_dimension_without_local_checks_stays_unresolved_instead_of_bbox_contradicted() -> None:
    requirement_text = (
        "Arrange the feature centers into a square array with a side length of 8.0mm."
    )
    bundle = RequirementEvidenceBuilder.build(
        snapshot=_snapshot(step=1, solids=1, bbox=[16.0, 16.0, 12.0]),
        history=[],
        requirements={"description": requirement_text},
        requirement_text=requirement_text,
    )

    summary = interpret_requirement_clauses(
        bundle=bundle,
        requirements={"description": requirement_text},
        requirement_text=requirement_text,
        supplemental_checks=[],
    )

    clause = summary.clause_interpretations[0]
    assert clause.status == RequirementClauseStatus.INSUFFICIENT_EVIDENCE
    assert "requested_dimensions=[8.0]" not in clause.evidence
