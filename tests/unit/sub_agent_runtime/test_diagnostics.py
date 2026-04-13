from sub_agent_runtime.diagnostics import (
    build_runtime_validation_payload,
    split_validation_feedback,
)


def test_split_validation_feedback_keeps_insufficient_evidence_incomplete() -> None:
    core, diagnostics = split_validation_feedback(
        {
            "success": True,
            "is_complete": False,
            "summary": "Requirement validation has insufficient evidence",
            "blockers": [],
            "core_checks": [
                {
                    "check_id": "solid_exists",
                    "status": "pass",
                    "evidence": "solids=1",
                }
            ],
            "diagnostic_checks": [],
            "insufficient_evidence": True,
            "observation_tags": ["insufficient_evidence"],
        }
    )

    assert core["is_complete"] is False
    assert core["insufficient_evidence"] is True
    assert core["summary"] == "Requirement validation has insufficient evidence"
    assert diagnostics["diagnostic_blockers"] == []


def test_build_runtime_validation_payload_preserves_insufficient_evidence_flag() -> None:
    normalized = build_runtime_validation_payload(
        {
            "success": True,
            "is_complete": False,
            "summary": "Requirement validation has insufficient evidence",
            "blockers": [],
            "core_checks": [
                {
                    "check_id": "solid_exists",
                    "status": "pass",
                    "evidence": "solids=1",
                }
            ],
            "diagnostic_checks": [],
            "insufficient_evidence": True,
            "observation_tags": ["insufficient_evidence"],
        }
    )

    assert normalized["is_complete"] is False
    assert normalized["insufficient_evidence"] is True
    assert normalized["summary"] == "Requirement validation has insufficient evidence"
