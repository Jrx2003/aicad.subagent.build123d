# Validation Generalization Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Refactor validation, taxonomy, benchmark diagnostics, and run-archive hygiene so the system becomes evidence-first and coverage-aware instead of overfitting to known benchmark families.

**Architecture:** Add a generic validation evidence layer plus a coverage-aware interpretation layer inside `validate_requirement`, then project that interpretation back into the legacy `checks / core_checks / diagnostic_checks` contract during a compatibility window. Demote blocker taxonomy and runtime skill selection from family/lane binding toward observation tags and evidence-gap hints, extend benchmark diagnostics to measure premature narrowing and insufficient-evidence handling, and centralize timestamp-only run-id enforcement plus archival tooling.

**Tech Stack:** Python, Pydantic, pytest, existing sandbox MCP server/runtime modules, benchmark runner, shell probe script, JSON corpus files, Markdown/JSON docs.

---

## File Structure

Use this decomposition before touching code:

- `src/sandbox_mcp_server/contracts.py`
  Expose the new validation-facing public contract fields without breaking existing callers.
- `src/sandbox_mcp_server/validation_evidence.py`
  Build generic geometry/history/topology/process evidence bundles with no family conclusions.
- `src/sandbox_mcp_server/validation_interpretation.py`
  Convert requirement clauses plus evidence into `verified / contradicted / insufficient_evidence / not_applicable`, and project legacy checks for compatibility.
- `src/sandbox_mcp_server/service.py`
  Wire the new evidence and interpretation layers into `validate_requirement` and `query_feature_probes`.
- `src/common/blocker_taxonomy.py`
  Reframe taxonomy output around observation tags and decision hints rather than repair-lane authority.
- `src/sub_agent_runtime/skill_pack.py`
  Replace family-heavy guidance selection with evidence-gap guidance selection.
- `benchmark/run_prompt_benchmark.py`
  Emit new generalization diagnostics and keep old brief report data stable.
- `benchmark/corpus/external_cadquery_index.json`
  Index external CadQuery source material for broader geometry distributions.
- `benchmark/corpus/external_cadquery_stress_set.json`
  Curate a small stress set derived from the external corpus.
- `src/common/run_artifacts.py`
  Centralize timestamp-only run-id validation and historical archive classification.
- `scripts/archive_historical_runs.py`
  Archive old and noncanonical runs into date-partitioned archive roots without breaking `latest` or `by_practice`.
- `scripts/run_aci_live_probe.sh`
  Reject non-timestamp explicit run ids before creating new probe directories.
- `tests/unit/sandbox_mcp_server/test_validate_requirement_contract.py`
  Lock the new validation contract and interpretation behavior.
- `tests/unit/common/test_blocker_taxonomy.py`
  Lock the new taxonomy observation-tag semantics.
- `tests/unit/sub_agent_runtime/test_skill_pack_l2_guidance.py`
  Lock evidence-gap skill selection and the removal of family-first narrowing.
- `tests/unit/benchmark/test_run_prompt_benchmark.py`
  Lock benchmark aggregation of insufficient-evidence and premature-narrowing diagnostics.
- `tests/unit/common/test_run_artifacts.py`
  Lock timestamp-only run-id parsing and archive classification.
- `tests/unit/sub_agent_runtime/test_runner_contracts.py`
  Lock strict explicit run-id validation in `create_run_dir`.

### Task 1: Add validation evidence and interpretation contracts

**Files:**
- Create: `src/sandbox_mcp_server/validation_evidence.py`
- Create: `src/sandbox_mcp_server/validation_interpretation.py`
- Modify: `src/sandbox_mcp_server/contracts.py:1444-1580`
- Test: `tests/unit/sandbox_mcp_server/test_validate_requirement_contract.py`

- [ ] **Step 1: Write the failing contract test for clause interpretations and insufficient-evidence output**

Add these assertions to `tests/unit/sandbox_mcp_server/test_validate_requirement_contract.py`:

```python
from sandbox_mcp_server.contracts import (
    RequirementClauseInterpretation,
    RequirementClauseStatus,
    ValidateRequirementOutput,
)


def test_validate_requirement_output_accepts_clause_interpretations_and_insufficient_evidence() -> None:
    output = ValidateRequirementOutput(
        success=True,
        session_id="session-1",
        step=3,
        is_complete=False,
        summary="Need more evidence before completion",
        clause_interpretations=[
            RequirementClauseInterpretation(
                clause_id="clause.outer_bbox",
                clause_text="outer body matches requested plate envelope",
                status=RequirementClauseStatus.VERIFIED,
                evidence_keys=["geometry.solids", "geometry.bbox"],
                summary="bbox spans 60 x 40 x 8",
            ),
            RequirementClauseInterpretation(
                clause_id="clause.corner_treatment",
                clause_text="corners are rounded",
                status=RequirementClauseStatus.INSUFFICIENT_EVIDENCE,
                evidence_keys=["render.images_missing"],
                summary="no edge-treatment evidence yet",
            ),
        ],
        coverage_confidence=0.5,
        insufficient_evidence=True,
        observation_tags=["missing_edge_treatment_evidence"],
        decision_hints=["query_topology_before_repair"],
    )

    assert output.insufficient_evidence is True
    assert output.coverage_confidence == 0.5
    assert output.clause_interpretations[1].status is RequirementClauseStatus.INSUFFICIENT_EVIDENCE
    assert output.observation_tags == ["missing_edge_treatment_evidence"]
    assert output.decision_hints == ["query_topology_before_repair"]
```

- [ ] **Step 2: Run the focused contract test to confirm it fails before implementation**

Run:

```bash
PYTHONPATH=src pytest -q tests/unit/sandbox_mcp_server/test_validate_requirement_contract.py -k "clause_interpretations_and_insufficient_evidence"
```

Expected:

```text
FAIL because RequirementClauseInterpretation / RequirementClauseStatus / ValidateRequirementOutput fields do not exist yet
```

- [ ] **Step 3: Add the public contract types and the internal bundle/interpretation dataclasses**

Add these public contract types in `src/sandbox_mcp_server/contracts.py`:

```python
class RequirementClauseStatus(str, Enum):
    VERIFIED = "verified"
    CONTRADICTED = "contradicted"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    NOT_APPLICABLE = "not_applicable"


class RequirementClauseInterpretation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    clause_id: str
    clause_text: str
    status: RequirementClauseStatus
    evidence_keys: list[str] = Field(default_factory=list)
    summary: str = Field(default="")
```

Add these internal dataclasses in `src/sandbox_mcp_server/validation_evidence.py` and `src/sandbox_mcp_server/validation_interpretation.py`:

```python
@dataclass(slots=True)
class RequirementEvidenceBundle:
    geometry_facts: dict[str, Any]
    topology_facts: dict[str, Any]
    local_feature_facts: dict[str, Any]
    process_facts: dict[str, Any]
    source_keys: list[str]


@dataclass(slots=True)
class RequirementInterpretationSummary:
    clause_interpretations: list[RequirementClauseInterpretation]
    coverage_confidence: float
    insufficient_evidence: bool
    observation_tags: list[str]
    decision_hints: list[str]
    checks: list[RequirementCheck]
```

- [ ] **Step 4: Extend `ValidateRequirementOutput` with compatibility-safe optional fields**

Modify `src/sandbox_mcp_server/contracts.py` so `ValidateRequirementOutput` also includes:

```python
    clause_interpretations: list[RequirementClauseInterpretation] = Field(
        default_factory=list,
        description="Coverage-aware clause interpretations derived from evidence.",
    )
    coverage_confidence: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="How much of the requirement the current evidence can support.",
    )
    insufficient_evidence: bool = Field(
        default=False,
        description="Whether completion is blocked by missing evidence rather than contradiction alone.",
    )
    observation_tags: list[str] = Field(
        default_factory=list,
        description="Normalized evidence/coverage observations for runtime and diagnostics.",
    )
    decision_hints: list[str] = Field(
        default_factory=list,
        description="Non-binding next-step hints derived from the current evidence state.",
    )
```

- [ ] **Step 5: Re-run the focused contract test and commit**

Run:

```bash
PYTHONPATH=src pytest -q tests/unit/sandbox_mcp_server/test_validate_requirement_contract.py -k "clause_interpretations_and_insufficient_evidence"
```

Expected:

```text
1 passed
```

Commit:

```bash
git add src/sandbox_mcp_server/contracts.py src/sandbox_mcp_server/validation_evidence.py src/sandbox_mcp_server/validation_interpretation.py tests/unit/sandbox_mcp_server/test_validate_requirement_contract.py
git commit -m "feat: add coverage-aware validation contracts"
```

### Task 2: Integrate evidence extraction and coverage-aware interpretation into `validate_requirement`

**Files:**
- Modify: `src/sandbox_mcp_server/service.py:101-163`
- Modify: `src/sandbox_mcp_server/service.py:4819-4937`
- Modify: `src/sandbox_mcp_server/service.py:1388-1528`
- Modify: `src/sandbox_mcp_server/validation_evidence.py`
- Modify: `src/sandbox_mcp_server/validation_interpretation.py`
- Test: `tests/unit/sandbox_mcp_server/test_validate_requirement_contract.py`

- [ ] **Step 1: Write the failing behavior tests for unknown clauses and legacy projection**

Add these tests to `tests/unit/sandbox_mcp_server/test_validate_requirement_contract.py`:

```python
@pytest.mark.asyncio
async def test_validate_requirement_marks_unhandled_clause_as_insufficient_evidence() -> None:
    service = SandboxMCPService(runner=_DummyRunner())
    session_id = "session-unknown-clause"
    service._session_manager.update_session_state(session_id, _snapshot(1, 1), "solid")

    output = await service.validate_requirement(
        ValidateRequirementInput(
            session_id=session_id,
            requirements={"description": "Create a plate with a decorative scalloped perimeter and hidden underside ribs."},
        )
    )

    assert output.success is True
    assert output.insufficient_evidence is True
    assert any(
        item.status.value == "insufficient_evidence"
        for item in output.clause_interpretations
    )
    assert output.is_complete is False


@pytest.mark.asyncio
async def test_validate_requirement_projects_legacy_blockers_from_interpretation() -> None:
    service = SandboxMCPService(runner=_DummyRunner())
    session_id = "session-legacy-projection"
    service._session_manager.update_session_state(session_id, _snapshot(1, 0), "sketch only")

    output = await service.validate_requirement(
        ValidateRequirementInput(
            session_id=session_id,
            requirements={"description": "Create a 20 mm block."},
        )
    )

    assert output.blockers == ["solid_exists", "solid_positive_volume"]
    assert [item.check_id for item in output.core_checks[:2]] == [
        "solid_exists",
        "solid_positive_volume",
    ]
```

- [ ] **Step 2: Run the focused behavior tests and confirm the current implementation fails**

Run:

```bash
PYTHONPATH=src pytest -q tests/unit/sandbox_mcp_server/test_validate_requirement_contract.py -k "unknown_clause_as_insufficient_evidence or legacy_blockers_from_interpretation"
```

Expected:

```text
FAIL because validate_requirement still emits direct builder-chain checks and no insufficient_evidence state
```

- [ ] **Step 3: Implement generic evidence extraction with no family conclusions**

Put this in `src/sandbox_mcp_server/validation_evidence.py`:

```python
def build_requirement_evidence_bundle(
    *,
    snapshot: CADStateSnapshot,
    history: list[ActionHistoryEntry],
    requirement_text: str,
) -> RequirementEvidenceBundle:
    return RequirementEvidenceBundle(
        geometry_facts={
            "solids": int(snapshot.geometry.solids or 0),
            "volume": float(snapshot.geometry.volume or 0.0),
            "bbox": [float(x) for x in (snapshot.geometry.bbox or []) if isinstance(x, (int, float))],
            "issue_count": len(snapshot.issues),
        },
        topology_facts={
            "face_count": int(snapshot.geometry.faces or 0),
            "edge_count": int(snapshot.geometry.edges or 0),
            "has_topology_index": bool(snapshot.topology_index),
        },
        local_feature_facts={
            "feature_count": len(snapshot.features),
            "blockers": [str(item) for item in snapshot.blockers or []],
            "warnings": [str(item) for item in snapshot.warnings or []],
        },
        process_facts={
            "history_steps": len(history),
            "action_types": [entry.action_type.value for entry in history],
            "requirement_text": requirement_text,
        },
        source_keys=[
            "geometry.solids",
            "geometry.volume",
            "geometry.bbox",
            "topology.face_count",
            "topology.edge_count",
            "history.action_types",
        ],
    )
```

- [ ] **Step 4: Implement interpretation plus legacy-check projection and wire it into `service.py`**

Put this in `src/sandbox_mcp_server/validation_interpretation.py`:

```python
def interpret_requirement(
    *,
    requirements: dict[str, object],
    requirement_text: str,
    evidence_bundle: RequirementEvidenceBundle,
) -> RequirementInterpretationSummary:
    clauses = [str(requirement_text or "").strip()] if str(requirement_text or "").strip() else []
    clause_interpretations: list[RequirementClauseInterpretation] = []
    observation_tags: list[str] = []
    decision_hints: list[str] = []

    solids = int(evidence_bundle.geometry_facts.get("solids") or 0)
    volume = float(evidence_bundle.geometry_facts.get("volume") or 0.0)

    if solids <= 0 or volume <= 0.0:
        clause_interpretations.append(
            RequirementClauseInterpretation(
                clause_id="clause.basic_solid",
                clause_text="a valid solid exists",
                status=RequirementClauseStatus.CONTRADICTED,
                evidence_keys=["geometry.solids", "geometry.volume"],
                summary=f"solids={solids}, volume={volume}",
            )
        )
    elif clauses:
        clause_interpretations.append(
            RequirementClauseInterpretation(
                clause_id="clause.requirement_text",
                clause_text=clauses[0],
                status=RequirementClauseStatus.INSUFFICIENT_EVIDENCE,
                evidence_keys=["geometry.solids", "geometry.volume", "topology.face_count"],
                summary="generic geometry exists, but no clause-specific coverage adapter proved the remaining requirement text",
            )
        )
        observation_tags.append("requirement_clause_not_covered_by_generic_interpretation")
        decision_hints.append("query_more_evidence_before_repair")

    checks = project_legacy_checks_from_interpretation(clause_interpretations, evidence_bundle)
    return RequirementInterpretationSummary(
        clause_interpretations=clause_interpretations,
        coverage_confidence=0.75 if solids > 0 and volume > 0.0 and not observation_tags else 0.35,
        insufficient_evidence=any(
            item.status is RequirementClauseStatus.INSUFFICIENT_EVIDENCE
            for item in clause_interpretations
        ),
        observation_tags=observation_tags,
        decision_hints=decision_hints,
        checks=checks,
    )
```

Then change `src/sandbox_mcp_server/service.py` to replace the direct `_build_requirement_checks(...)` call with:

```python
        evidence_bundle = build_requirement_evidence_bundle(
            snapshot=snapshot,
            history=history,
            requirement_text=requirement_text,
        )
        interpretation = interpret_requirement(
            requirements=requirements,
            requirement_text=requirement_text,
            evidence_bundle=evidence_bundle,
        )
        checks = interpretation.checks
        core_checks, diagnostic_checks = partition_requirement_checks(checks)
```

- [ ] **Step 5: Re-run the focused behavior tests and commit**

Run:

```bash
PYTHONPATH=src pytest -q tests/unit/sandbox_mcp_server/test_validate_requirement_contract.py -k "unknown_clause_as_insufficient_evidence or legacy_blockers_from_interpretation"
```

Expected:

```text
2 passed
```

Commit:

```bash
git add src/sandbox_mcp_server/service.py src/sandbox_mcp_server/validation_evidence.py src/sandbox_mcp_server/validation_interpretation.py tests/unit/sandbox_mcp_server/test_validate_requirement_contract.py
git commit -m "feat: add evidence-first validation interpretation"
```

### Task 3: Demote blocker taxonomy and runtime skill guidance from family-first to evidence-gap-first

**Files:**
- Modify: `src/common/blocker_taxonomy.py:7-260`
- Modify: `src/sub_agent_runtime/skill_pack.py:1-220`
- Test: `tests/unit/common/test_blocker_taxonomy.py`
- Test: `tests/unit/sub_agent_runtime/test_skill_pack_l2_guidance.py`

- [ ] **Step 1: Write the failing taxonomy and skill-pack tests**

Add these tests:

```python
def test_blocker_taxonomy_exposes_observation_tags_and_non_binding_hints() -> None:
    taxonomy = classify_blocker_taxonomy("feature_path_sweep_frame")

    assert "missing_path_frame_evidence" in taxonomy.observation_tags
    assert "query_kernel_state_before_retry" in taxonomy.decision_hints


def test_build_runtime_skill_pack_prefers_evidence_gap_guidance_when_validation_is_insufficient() -> None:
    skills = build_runtime_skill_pack(
        requirements={"description": "Create a decorative scalloped plate with hidden ribs."},
        latest_validation={
            "insufficient_evidence": True,
            "observation_tags": ["requirement_clause_not_covered_by_generic_interpretation"],
            "decision_hints": ["query_more_evidence_before_repair"],
            "blocker_taxonomy": [],
        },
        latest_write_health={"tool": "execute_cadquery", "invalid_signals": []},
    )

    skill_ids = _runtime_skill_ids(skills)
    assert "insufficient_evidence_query_before_repair" in skill_ids
    assert "path_sweep_wire_profile_frame_repair" not in skill_ids
```

- [ ] **Step 2: Run the focused taxonomy and skill-pack tests and confirm failure**

Run:

```bash
PYTHONPATH=src pytest -q tests/unit/common/test_blocker_taxonomy.py tests/unit/sub_agent_runtime/test_skill_pack_l2_guidance.py -k "observation_tags_and_non_binding_hints or insufficient_evidence_query_before_repair"
```

Expected:

```text
FAIL because taxonomy has no observation_tags / decision_hints and skill_pack still keys off family-driven narrowing
```

- [ ] **Step 3: Add observation tags and decision hints to taxonomy while keeping compatibility**

Change `src/common/blocker_taxonomy.py` to expose:

```python
@dataclass(frozen=True, slots=True)
class BlockerTaxonomy:
    blocker_id: str
    normalized_blocker_id: str
    family_ids: list[str]
    feature_ids: list[str]
    primary_feature_id: str
    evidence_source: str
    completeness_relevance: str
    severity: str
    recommended_repair_lane: str
    observation_tags: list[str]
    decision_hints: list[str]
```

Use conservative mappings such as:

```python
_OBSERVATION_HINTS = {
    "feature_path_sweep_frame": (
        ["missing_path_frame_evidence"],
        ["query_kernel_state_before_retry", "query_feature_probes_before_rewrite"],
    ),
    "feature_local_anchor_alignment": (
        ["missing_local_anchor_alignment_evidence"],
        ["query_geometry_before_repair"],
    ),
}
```

Keep `recommended_repair_lane`, but collapse uncertain situations to a neutral value:

```python
recommended_repair_lane = "inspect_more_evidence" if observation_tags else _recommended_repair_lane(...)
```

- [ ] **Step 4: Replace family-heavy skill selection with evidence-gap guidance**

In `src/sub_agent_runtime/skill_pack.py`, add helpers like:

```python
def _validation_observation_tags(latest_validation: dict[str, Any] | None) -> set[str]:
    return {
        str(item).strip()
        for item in (latest_validation or {}).get("observation_tags", [])
        if isinstance(item, str) and str(item).strip()
    }


def _validation_decision_hints(latest_validation: dict[str, Any] | None) -> set[str]:
    return {
        str(item).strip()
        for item in (latest_validation or {}).get("decision_hints", [])
        if isinstance(item, str) and str(item).strip()
    }
```

Then insert the new guidance branch near the top of `build_runtime_skill_pack(...)`:

```python
    if bool((latest_validation or {}).get("insufficient_evidence")):
        skills.append(
            {
                "skill_id": "insufficient_evidence_query_before_repair",
                "when_relevant": "Use when validation cannot verify key requirement clauses from the current evidence.",
                "guidance": [
                    "Do not collapse the requirement into the closest known family when validation explicitly says evidence is insufficient.",
                    "Prefer the smallest read or probe that can prove or disprove the missing clause before another whole-part rewrite.",
                    "If the last write already produced geometry, ask for the missing evidence surface instead of repeating the same write with only wording changes.",
                ],
            }
        )
```

- [ ] **Step 5: Re-run the focused taxonomy and skill-pack tests and commit**

Run:

```bash
PYTHONPATH=src pytest -q tests/unit/common/test_blocker_taxonomy.py tests/unit/sub_agent_runtime/test_skill_pack_l2_guidance.py -k "observation_tags_and_non_binding_hints or insufficient_evidence_query_before_repair"
```

Expected:

```text
2 passed
```

Commit:

```bash
git add src/common/blocker_taxonomy.py src/sub_agent_runtime/skill_pack.py tests/unit/common/test_blocker_taxonomy.py tests/unit/sub_agent_runtime/test_skill_pack_l2_guidance.py
git commit -m "refactor: demote taxonomy and skill pack to evidence-gap hints"
```

### Task 4: Extend benchmark diagnostics and add the external CadQuery corpus artifacts

**Files:**
- Create: `benchmark/corpus/external_cadquery_index.json`
- Create: `benchmark/corpus/external_cadquery_stress_set.json`
- Modify: `benchmark/run_prompt_benchmark.py:926-1237`
- Modify: `benchmark/run_prompt_benchmark.py:1468-1605`
- Modify: `benchmark/run_prompt_benchmark.py:1829-1888`
- Test: `tests/unit/benchmark/test_run_prompt_benchmark.py`

- [ ] **Step 1: Write the failing benchmark tests for insufficient-evidence and premature-narrowing reporting**

Add these tests to `tests/unit/benchmark/test_run_prompt_benchmark.py`:

```python
def test_diagnose_case_marks_premature_narrowing_when_lane_is_non_neutral_under_insufficient_evidence() -> None:
    benchmark_module = _load_benchmark_module()
    case_dir = Path("/tmp/fake-case")

    diagnosis = benchmark_module._diagnose_case(
        case_dir=case_dir,
        return_code=0,
        timed_out=False,
        case_summary_payload={"summary": {"validation_complete": False, "planner_rounds": 3}},
        evaluation_payload={"passed": False, "summary": "shape mismatch"},
        generated_step_path=case_dir / "outputs" / "final_model.step",
        prompt_metrics={},
        trace_summary={"rounds": [], "failure_bundle": {"recent_validation": {"insufficient_evidence": True}}},
    )

    assert diagnosis["premature_narrowing"] is True


def test_build_brief_case_row_keeps_coverage_confidence_and_insufficient_evidence() -> None:
    row = _build_brief_case_row(
        {
            "case_id": "X1",
            "evaluation": {"passed": False},
            "runtime_summary": {"planner_rounds": 2, "validation_complete": False},
            "analysis": {
                "status": "INCOMPLETE",
                "likely_root_cause": "Need more evidence",
                "coverage_confidence": 0.4,
                "insufficient_evidence": True,
                "premature_narrowing": False,
            },
        }
    )

    assert row["coverage_confidence"] == 0.4
    assert row["insufficient_evidence"] is True
```

- [ ] **Step 2: Run the focused benchmark tests and confirm failure**

Run:

```bash
PYTHONPATH=src pytest -q tests/unit/benchmark/test_run_prompt_benchmark.py -k "premature_narrowing or coverage_confidence_and_insufficient_evidence"
```

Expected:

```text
FAIL because the benchmark diagnosis/row builders do not emit the new fields yet
```

- [ ] **Step 3: Implement the new diagnosis fields and brief-report columns**

Modify `benchmark/run_prompt_benchmark.py` so `_diagnose_case(...)` and `_build_brief_case_row(...)` emit:

```python
    insufficient_evidence = bool(runtime_validation_view.get("insufficient_evidence"))
    coverage_confidence = float(runtime_validation_view.get("coverage_confidence") or 0.0)
    validation_lanes = _summarize_validation_lanes(_load_latest_validation_payload(case_dir))
    has_non_neutral_lane = any(
        str(item.get("recommended_repair_lane") or "").strip()
        not in {"", "inspect_more_evidence"}
        for item in validation_lanes.get("blocker_taxonomy", [])
        if isinstance(item, dict)
    )
    premature_narrowing = insufficient_evidence and has_non_neutral_lane
```

Add these row fields:

```python
        "coverage_confidence": float(analysis.get("coverage_confidence") or 0.0),
        "insufficient_evidence": bool(analysis.get("insufficient_evidence")),
        "premature_narrowing": bool(analysis.get("premature_narrowing")),
```

- [ ] **Step 4: Add the initial external corpus files**

Create `benchmark/corpus/external_cadquery_index.json`:

```json
{
  "version": "2026-04-09",
  "sources": [
    {
      "repo": "CadQuery/cadquery-contrib",
      "focus": ["sweep", "loft", "selectors", "advanced_examples"],
      "entries": [
        {
          "path": "examples/Parametric_Enclosure.py",
          "themes": ["shell", "bosses", "lid", "fastener_layout"],
          "artifact_types": ["code", "image"]
        },
        {
          "path": "examples/Classic_OCC_Bottle.py",
          "themes": ["loft", "fillet", "organic_profile"],
          "artifact_types": ["code", "image"]
        }
      ]
    }
  ]
}
```

Create `benchmark/corpus/external_cadquery_stress_set.json`:

```json
{
  "version": "2026-04-09",
  "cases": [
    {
      "case_id": "external_enclosure_shell",
      "source_repo": "CadQuery/cadquery-contrib",
      "source_path": "examples/Parametric_Enclosure.py",
      "why_selected": "Combines shell, lid, bosses, and distributed holes without matching one narrow benchmark family."
    },
    {
      "case_id": "external_gridfinity_rugged_box",
      "source_repo": "michaelgale/cq-gridfinity",
      "source_path": "README.md#gridfinityruggedbox",
      "why_selected": "High-option product-like shell and accessory structure useful for coverage-aware interpretation stress."
    }
  ]
}
```

- [ ] **Step 5: Re-run the focused benchmark tests and commit**

Run:

```bash
PYTHONPATH=src pytest -q tests/unit/benchmark/test_run_prompt_benchmark.py -k "premature_narrowing or coverage_confidence_and_insufficient_evidence"
```

Expected:

```text
2 passed
```

Commit:

```bash
git add benchmark/run_prompt_benchmark.py benchmark/corpus/external_cadquery_index.json benchmark/corpus/external_cadquery_stress_set.json tests/unit/benchmark/test_run_prompt_benchmark.py
git commit -m "feat: add generalization benchmark diagnostics"
```

### Task 5: Enforce timestamp-only run ids and add the historical archive utility

**Files:**
- Create: `src/common/run_artifacts.py`
- Create: `scripts/archive_historical_runs.py`
- Modify: `src/sub_agent_runtime/runner.py:26-32`
- Modify: `benchmark/run_prompt_benchmark.py:47-53`
- Modify: `scripts/run_aci_live_probe.sh`
- Test: `tests/unit/common/test_run_artifacts.py`
- Modify: `tests/unit/sub_agent_runtime/test_runner_contracts.py`

- [ ] **Step 1: Write the failing run-id and archive-classification tests**

Create `tests/unit/common/test_run_artifacts.py` with:

```python
from datetime import datetime

import pytest

from common.run_artifacts import classify_run_directory, ensure_timestamp_run_id


def test_ensure_timestamp_run_id_rejects_noncanonical_suffixes() -> None:
    with pytest.raises(ValueError, match="timestamp-only"):
        ensure_timestamp_run_id("20260408_130_half_shell_guidance_v2")


def test_classify_run_directory_marks_old_and_noncanonical_paths_for_archive() -> None:
    cutoff = datetime(2026, 4, 6, 0, 0, 0)

    assert classify_run_directory("20260405_171520", cutoff=cutoff) == "archive"
    assert classify_run_directory("20260408_130_half_shell_guidance_v2", cutoff=cutoff) == "archive"
    assert classify_run_directory("latest", cutoff=cutoff) == "keep_special"
```

Extend `tests/unit/sub_agent_runtime/test_runner_contracts.py`:

```python
def test_create_run_dir_rejects_non_timestamp_run_id(tmp_path: Path) -> None:
    runner = IterativeSubAgentRunner()

    with pytest.raises(ValueError, match="timestamp-only"):
        runner.create_run_dir(tmp_path, run_id="20260408_bad_suffix")
```

- [ ] **Step 2: Run the focused run-id tests and confirm failure**

Run:

```bash
PYTHONPATH=src pytest -q tests/unit/common/test_run_artifacts.py tests/unit/sub_agent_runtime/test_runner_contracts.py -k "timestamp_run_id or classify_run_directory"
```

Expected:

```text
FAIL because there is no shared run-id helper and create_run_dir still accepts arbitrary explicit names
```

- [ ] **Step 3: Implement the shared helper and wire it into runner and benchmark**

Create `src/common/run_artifacts.py`:

```python
from __future__ import annotations

from datetime import datetime
import re

_TIMESTAMP_RUN_ID_RE = re.compile(r"^\d{8}_\d{6}$")
_TIMESTAMP_PREFIX_RE = re.compile(r"^(\d{8})_(\d{6})(?:$|_)")


def ensure_timestamp_run_id(run_id: str) -> str:
    normalized = str(run_id or "").strip()
    if not _TIMESTAMP_RUN_ID_RE.fullmatch(normalized):
        raise ValueError("run_id must stay timestamp-only: YYYYMMDD_HHMMSS")
    return normalized


def classify_run_directory(name: str, *, cutoff: datetime) -> str:
    normalized = str(name or "").strip()
    if normalized in {"latest", "by_practice"}:
        return "keep_special"
    match = _TIMESTAMP_PREFIX_RE.match(normalized)
    if match is None:
        return "archive"
    seen_at = datetime.strptime("".join(match.groups()), "%Y%m%d%H%M%S")
    return "archive" if seen_at < cutoff or normalized != f"{match.group(1)}_{match.group(2)}" else "keep"
```

Then use it in `src/sub_agent_runtime/runner.py`:

```python
        resolved_run_id = (
            ensure_timestamp_run_id(run_id)
            if run_id is not None
            else datetime.now().strftime("%Y%m%d_%H%M%S")
        )
```

And in `benchmark/run_prompt_benchmark.py`:

```python
def _parse_benchmark_run_id(value: str) -> str:
    return ensure_timestamp_run_id(value)
```

- [ ] **Step 4: Add the archive script and explicit probe-run-id validation**

Create `scripts/archive_historical_runs.py`:

```python
#!/usr/bin/env python3
from __future__ import annotations

from datetime import datetime
from pathlib import Path
import argparse
import json
import shutil

from common.run_artifacts import classify_run_directory


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cutoff", required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    cutoff = datetime.strptime(args.cutoff, "%Y-%m-%d")
    for root_name in ("benchmark/runs", "test_runs"):
        root = Path(root_name)
        archive_root = root / "archive" / f"pre_{cutoff.strftime('%Y%m%d')}"
        archive_root.mkdir(parents=True, exist_ok=True)
        manifest: list[dict[str, str]] = []
        for child in sorted(path for path in root.iterdir() if path.is_dir()):
            verdict = classify_run_directory(child.name, cutoff=cutoff)
            if verdict != "archive":
                continue
            target = archive_root / child.name
            manifest.append({"source": str(child), "target": str(target)})
            if not args.dry_run:
                shutil.move(str(child), str(target))
        (archive_root / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
```

Modify `scripts/run_aci_live_probe.sh` near `RUN_ID` resolution:

```bash
if [[ -n "${1:-}" && ! "$1" =~ ^[0-9]{8}_[0-9]{6}$ ]]; then
  echo "[aci-live] explicit run id must stay timestamp-only: YYYYMMDD_HHMMSS" >&2
  exit 2
fi
```

- [ ] **Step 5: Re-run the focused tests, dry-run the archive script, and commit**

Run:

```bash
PYTHONPATH=src pytest -q tests/unit/common/test_run_artifacts.py tests/unit/sub_agent_runtime/test_runner_contracts.py -k "timestamp_run_id or classify_run_directory"
PYTHONPATH=src python scripts/archive_historical_runs.py --cutoff 2026-04-06 --dry-run
```

Expected:

```text
all selected tests pass
archive script writes manifest.json files under benchmark/runs/archive/pre_20260406 and test_runs/archive/pre_20260406 without moving directories in dry-run mode
```

Commit:

```bash
git add src/common/run_artifacts.py scripts/archive_historical_runs.py scripts/run_aci_live_probe.sh src/sub_agent_runtime/runner.py benchmark/run_prompt_benchmark.py tests/unit/common/test_run_artifacts.py tests/unit/sub_agent_runtime/test_runner_contracts.py
git commit -m "feat: enforce timestamp run ids and add archival utility"
```

### Task 6: Sync canonical docs, update tracking docs, then verify with tests, probe, benchmark, and archive execution

**Files:**
- Modify: `CODEX.md`
- Modify: `docs/cad_iteration/SYSTEM_RECORD.json`
- Modify: `docs/cad_iteration/DESIGN_INTENT.md`
- Modify: `docs/cad_iteration/ITERATION_PROTOCOL.md`
- Modify: `docs/cad_iteration/TOOL_SURFACE.md`
- Modify: `docs/work_logs/2026-04-09_validation-generalization-refactor.md`
- Modify: `docs/work_logs/2026-04-09_validation-generalization-checklist.json`

- [ ] **Step 1: Update the canonical docs to the evidence-first direction**

Patch `CODEX.md` and the CAD-iteration docs so they say:

```md
1. Prefer evidence-first validation over family-first validator branching.
2. Treat insufficient_evidence as a first-class validation outcome.
3. Demote blocker taxonomy and runtime skills from repair-lane authority to observation and hint surfaces.
4. Use benchmark as a generalization monitor, not as a family-rule accumulation loop.
```

- [ ] **Step 2: Update the work log and checklist to reflect approved spec plus written plan**

Update `docs/work_logs/2026-04-09_validation-generalization-refactor.md` to append:

```md
## Implementation Plan

Plan written to:

1. `docs/superpowers/plans/2026-04-09-validation-generalization-phase1.md`

User approved the design spec and moved the work into implementation-planning state.
```

Update `docs/work_logs/2026-04-09_validation-generalization-checklist.json` statuses:

```json
{
  "id": "user_spec_review",
  "status": "done",
  "notes": "User approved the written design spec and authorized implementation planning."
},
{
  "id": "plan_phase1_impl",
  "status": "done",
  "notes": "Implementation plan saved under docs/superpowers/plans/2026-04-09-validation-generalization-phase1.md."
}
```

- [ ] **Step 3: Run the focused unit-test suite for the touched modules**

Run:

```bash
PYTHONPATH=src pytest -q \
  tests/unit/sandbox_mcp_server/test_validate_requirement_contract.py \
  tests/unit/common/test_blocker_taxonomy.py \
  tests/unit/sub_agent_runtime/test_skill_pack_l2_guidance.py \
  tests/unit/benchmark/test_run_prompt_benchmark.py \
  tests/unit/common/test_run_artifacts.py \
  tests/unit/sub_agent_runtime/test_runner_contracts.py
```

Expected:

```text
all selected tests pass
```

- [ ] **Step 4: Run one real probe, one targeted benchmark, and the real archive move**

Run the real probe:

```bash
AICAD_PROBE_REQUIREMENT='Create a 60 mm by 40 mm by 8 mm plate, add four corner holes, and cut a centered U-slot on the front edge.' \
scripts/run_aci_live_probe.sh
```

Expected:

```text
a new test_runs/YYYYMMDD_HHMMSS directory is created
test_runs/latest points to that directory
summary.json and trace/conversation.jsonl exist under the new run
```

Run the targeted benchmark:

```bash
PYTHONPATH=src python benchmark/run_prompt_benchmark.py --run-id 20260409_220000 --cases L2_63,L2_149
```

Expected:

```text
benchmark/runs/20260409_220000 is created
run_diagnostics.json includes coverage_confidence / insufficient_evidence / premature_narrowing fields
brief_report.tsv includes the new columns
```

Run the real archive move:

```bash
PYTHONPATH=src python scripts/archive_historical_runs.py --cutoff 2026-04-06
```

Expected:

```text
benchmark/runs/archive/pre_20260406/manifest.json exists
test_runs/archive/pre_20260406/manifest.json exists
directories older than 2026-04-06 and noncanonical names move under the archive roots
```

- [ ] **Step 5: Commit the docs sync and final verification evidence**

Commit:

```bash
git add CODEX.md docs/cad_iteration/SYSTEM_RECORD.json docs/cad_iteration/DESIGN_INTENT.md docs/cad_iteration/ITERATION_PROTOCOL.md docs/cad_iteration/TOOL_SURFACE.md docs/work_logs/2026-04-09_validation-generalization-refactor.md docs/work_logs/2026-04-09_validation-generalization-checklist.json docs/superpowers/plans/2026-04-09-validation-generalization-phase1.md
git commit -m "docs: sync validation generalization phase 1 plan and contracts"
```
