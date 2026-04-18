from __future__ import annotations

import pytest

from sub_agent_runtime.hallucination import build_run_hallucination_summary
from sub_agent_runtime.turn_state import (
    RunState,
    ToolCallRecord,
    ToolCategory,
    ToolResultRecord,
    TurnRecord,
)


def test_build_run_hallucination_summary_tracks_write_and_validation_layers() -> None:
    run_state = RunState(session_id="session-1", requirements={"description": "demo"})
    run_state.add_turn(
        TurnRecord(
            round_no=1,
            decision_summary="initial write",
            tool_calls=[
                ToolCallRecord(
                    name="execute_build123d",
                    category=ToolCategory.WRITE,
                    arguments={"code": "import cadquery as cq"},
                )
            ],
            tool_results=[
                ToolResultRecord(
                    name="execute_build123d",
                    category=ToolCategory.WRITE,
                    success=False,
                    payload={
                        "failure_kind": "execute_build123d_api_lint_failure",
                        "lint_hits": [
                            {
                                "lint_id": "legacy_kernel.cadquery_import",
                                "message": "CadQuery import is not allowed in Build123d code.",
                                "family_ids": ["enclosure"],
                            }
                        ],
                    },
                    error="execute_build123d preflight lint failed",
                )
            ],
        )
    )
    run_state.add_turn(
        TurnRecord(
            round_no=2,
            decision_summary="finish with validation",
            tool_calls=[
                ToolCallRecord(
                    name="validate_requirement",
                    category=ToolCategory.JUDGE,
                    arguments={},
                )
            ],
            tool_results=[
                ToolResultRecord(
                    name="validate_requirement",
                    category=ToolCategory.JUDGE,
                    success=True,
                    payload={
                        "is_complete": True,
                        "summary": "Looks complete, but local anchors are not fully grounded.",
                        "coverage_confidence": 0.2,
                        "insufficient_evidence": True,
                    },
                )
            ],
        )
    )

    summary = build_run_hallucination_summary(run_state)

    assert summary["event_count"] == 2
    assert summary["primary_layer"] == "write_surface"
    assert summary["first_write_event_count"] == 1
    assert summary["layers"]["write_surface"] == 1
    assert summary["layers"]["validation_surface"] == 1
    assert summary["categories"]["invalid_api_contract"] == 1
    assert summary["categories"]["validation_overclaim"] == 1
    assert summary["weighted_score"] == pytest.approx(1.35)
    assert summary["events_per_write"] == pytest.approx(2.0)


def test_build_run_hallucination_summary_detects_topology_targeting_without_readback() -> None:
    run_state = RunState(session_id="session-2", requirements={"description": "demo"})
    run_state.add_turn(
        TurnRecord(
            round_no=1,
            decision_summary="host build",
            tool_calls=[
                ToolCallRecord(
                    name="execute_build123d",
                    category=ToolCategory.WRITE,
                    arguments={"code": "result = Box(10, 10, 4)"},
                )
            ],
            tool_results=[
                ToolResultRecord(
                    name="execute_build123d",
                    category=ToolCategory.WRITE,
                    success=True,
                    payload={
                        "snapshot": {
                            "geometry": {
                                "solids": 1,
                                "volume": 400.0,
                                "bbox": [10.0, 10.0, 4.0],
                            }
                        }
                    },
                    step_file="model.step",
                )
            ],
        )
    )
    run_state.add_turn(
        TurnRecord(
            round_no=2,
            decision_summary="blind local fillet",
            tool_calls=[
                ToolCallRecord(
                    name="apply_cad_action",
                    category=ToolCategory.WRITE,
                    arguments={
                        "action_type": "fillet",
                        "action_params": {"edge_refs": ["edge:outer-top-1"], "radius": 1.0},
                    },
                )
            ],
            tool_results=[
                ToolResultRecord(
                    name="apply_cad_action",
                    category=ToolCategory.WRITE,
                    success=False,
                    payload={"error_message": "invalid_edge_ref:edge:outer-top-1"},
                    error="invalid_edge_ref:edge:outer-top-1",
                )
            ],
        )
    )

    summary = build_run_hallucination_summary(run_state)

    assert summary["event_count"] >= 1
    assert summary["layers"]["read_surface"] >= 1
    assert summary["categories"]["targeting_without_readback"] >= 1
    assert summary["weighted_score"] >= 0.6


def test_build_run_hallucination_summary_classifies_apply_action_contract_failure_as_targeting_gap() -> None:
    run_state = RunState(session_id="session-apply-contract", requirements={"description": "demo"})
    run_state.add_turn(
        TurnRecord(
            round_no=1,
            decision_summary="host build",
            tool_calls=[
                ToolCallRecord(
                    name="execute_build123d",
                    category=ToolCategory.WRITE,
                    arguments={"code": "result = Box(10, 10, 4)"},
                )
            ],
            tool_results=[
                ToolResultRecord(
                    name="execute_build123d",
                    category=ToolCategory.WRITE,
                    success=True,
                    payload={
                        "snapshot": {
                            "geometry": {
                                "solids": 1,
                                "volume": 400.0,
                                "bbox": [10.0, 10.0, 4.0],
                            }
                        }
                    },
                    step_file="model.step",
                )
            ],
        )
    )
    run_state.add_turn(
        TurnRecord(
            round_no=2,
            decision_summary="get topology for local finish",
            tool_calls=[
                ToolCallRecord(
                    name="query_topology",
                    category=ToolCategory.READ,
                    arguments={},
                )
            ],
            tool_results=[
                ToolResultRecord(
                    name="query_topology",
                    category=ToolCategory.READ,
                    success=True,
                    payload={
                        "matched_ref_ids": ["edge:1:E_outer_1"],
                        "candidate_sets": [
                            {
                                "candidate_id": "opening_rim_edges",
                                "label": "Opening Rim Edges",
                                "entity_type": "edge",
                                "ref_ids": ["edge:1:E_outer_1"],
                            }
                        ],
                    },
                )
            ],
        )
    )
    run_state.add_turn(
        TurnRecord(
            round_no=3,
            decision_summary="local fillet still missing refs",
            tool_calls=[
                ToolCallRecord(
                    name="apply_cad_action",
                    category=ToolCategory.WRITE,
                    arguments={
                        "action_type": "fillet",
                        "action_params": {"edge_selector": "top_face_edges", "radius": 1.0},
                    },
                )
            ],
            tool_results=[
                ToolResultRecord(
                    name="apply_cad_action",
                    category=ToolCategory.WRITE,
                    success=False,
                    payload={
                        "failure_kind": "apply_cad_action_contract_failure",
                        "error_message": "apply_cad_action preflight failed: missing edge_refs for local fillet/chamfer",
                    },
                    error="apply_cad_action preflight failed: missing edge_refs for local fillet/chamfer",
                )
            ],
        )
    )

    summary = build_run_hallucination_summary(run_state)

    assert summary["layers"]["read_surface"] >= 1
    assert summary["categories"]["local_action_contract_missing_target_refs"] == 1
    assert summary["weighted_score"] == pytest.approx(0.6)


def test_build_run_hallucination_summary_tracks_malformed_face_ref_as_invalid_target_reference() -> None:
    run_state = RunState(session_id="session-invalid-ref", requirements={"description": "demo"})
    run_state.add_turn(
        TurnRecord(
            round_no=1,
            decision_summary="get topology for local finish",
            tool_calls=[
                ToolCallRecord(
                    name="query_topology",
                    category=ToolCategory.READ,
                    arguments={},
                )
            ],
            tool_results=[
                ToolResultRecord(
                    name="query_topology",
                    category=ToolCategory.READ,
                    success=True,
                    payload={
                        "matched_ref_ids": ["face:1:F_top"],
                        "candidate_sets": [
                            {
                                "candidate_id": "mating_faces",
                                "label": "Mating Faces",
                                "entity_type": "face",
                                "ref_ids": ["face:1:F_top"],
                            }
                        ],
                    },
                )
            ],
        )
    )
    run_state.add_turn(
        TurnRecord(
            round_no=2,
            decision_summary="local hole used candidate set label as face_ref",
            tool_calls=[
                ToolCallRecord(
                    name="apply_cad_action",
                    category=ToolCategory.WRITE,
                    arguments={
                        "action_type": "hole",
                        "action_params": {
                            "face_ref": "mating_faces",
                            "diameter": 6.0,
                            "depth": 2.0,
                        },
                    },
                )
            ],
            tool_results=[
                ToolResultRecord(
                    name="apply_cad_action",
                    category=ToolCategory.WRITE,
                    success=False,
                    payload={},
                    error=(
                        "invalid_reference: malformed face_ref 'mating_faces'; face_ref must "
                        "be one concrete `face:<step>:<entity_id>` ref from the latest "
                        "query_topology, not a candidate-set label or host-role alias"
                    ),
                )
            ],
        )
    )

    summary = build_run_hallucination_summary(run_state)

    assert summary["layers"]["read_surface"] >= 1
    assert summary["categories"]["invalid_target_reference"] == 1
    assert summary["weighted_score"] == pytest.approx(0.6)


def test_build_run_hallucination_summary_tracks_validation_provider_error() -> None:
    run_state = RunState(session_id="session-3", requirements={"description": "demo"})
    run_state.add_turn(
        TurnRecord(
            round_no=1,
            decision_summary="validation with provider timeout",
            tool_calls=[
                ToolCallRecord(
                    name="validate_requirement",
                    category=ToolCategory.JUDGE,
                    arguments={},
                )
            ],
            tool_results=[
                ToolResultRecord(
                    name="validate_requirement",
                    category=ToolCategory.JUDGE,
                    success=True,
                    payload={
                        "is_complete": False,
                        "summary": "Validation stayed conservative due to unresolved evidence.",
                        "observation_tags": ["validation:llm_provider_error"],
                        "decision_hints": [
                            "fallback_to_evidence_first_clause_interpretation",
                            "validation_llm_provider_error:TimeoutError:validation timed out",
                        ],
                    },
                )
            ],
        )
    )

    summary = build_run_hallucination_summary(run_state)

    assert summary["event_count"] == 1
    assert summary["layers"]["validation_surface"] == 1
    assert summary["categories"]["validation_provider_error"] == 1
    assert summary["weighted_score"] == pytest.approx(0.35)


def test_build_run_hallucination_summary_classifies_plane_location_runtime_error() -> None:
    run_state = RunState(session_id="session-4", requirements={"description": "demo"})
    run_state.add_turn(
        TurnRecord(
            round_no=1,
            decision_summary="bad plane placement",
            tool_calls=[
                ToolCallRecord(
                    name="execute_build123d",
                    category=ToolCategory.WRITE,
                    arguments={"code": "with Locations(Plane.XY * (0, 0, 5)): pass"},
                )
            ],
            tool_results=[
                ToolResultRecord(
                    name="execute_build123d",
                    category=ToolCategory.WRITE,
                    success=False,
                    payload={},
                    error=(
                        "Exit code: 1 | TypeError: Planes can only be multiplied with Locations "
                        "or Shapes to relocate them"
                    ),
                )
            ],
        )
    )

    summary = build_run_hallucination_summary(run_state)

    assert summary["event_count"] == 1
    assert summary["layers"]["write_surface"] == 1
    assert summary["categories"]["invalid_plane_location_contract"] == 1


def test_build_run_hallucination_summary_classifies_unexpected_constructor_keyword_error() -> None:
    run_state = RunState(session_id="session-4b", requirements={"description": "demo"})
    run_state.add_turn(
        TurnRecord(
            round_no=1,
            decision_summary="box constructor guessed unsupported radius keyword",
            tool_calls=[
                ToolCallRecord(
                    name="execute_build123d",
                    category=ToolCategory.WRITE,
                    arguments={"code": "result = Box(80, 60, 20, radius=6)"},
                )
            ],
            tool_results=[
                ToolResultRecord(
                    name="execute_build123d",
                    category=ToolCategory.WRITE,
                    success=False,
                    payload={},
                    error=(
                        "Exit code: 1 | TypeError: Box.__init__() got an unexpected keyword "
                        "argument 'radius'"
                    ),
                )
            ],
        )
    )

    summary = build_run_hallucination_summary(run_state)

    assert summary["event_count"] == 1
    assert summary["layers"]["write_surface"] == 1
    assert summary["categories"]["invalid_constructor_keyword_contract"] == 1
    assert summary["weighted_score"] == pytest.approx(1.0)


def test_build_run_hallucination_summary_separates_runtime_name_error_from_build123d_contract_error() -> None:
    run_state = RunState(session_id="session-5", requirements={"description": "demo"})
    run_state.add_turn(
        TurnRecord(
            round_no=1,
            decision_summary="undefined final symbol",
            tool_calls=[
                ToolCallRecord(
                    name="execute_build123d",
                    category=ToolCategory.WRITE,
                    arguments={"code": "result = part"},
                )
            ],
            tool_results=[
                ToolResultRecord(
                    name="execute_build123d",
                    category=ToolCategory.WRITE,
                    success=False,
                    payload={},
                    error="Exit code: 1 | NameError: name 'part' is not defined. Did you mean: 'Part'?",
                )
            ],
        )
    )

    summary = build_run_hallucination_summary(run_state)

    assert summary["event_count"] == 1
    assert summary["layers"]["write_surface"] == 1
    assert summary["categories"]["write_runtime_symbol_error.part"] == 1
    assert summary["weighted_score"] == pytest.approx(0.5)


def test_build_run_hallucination_summary_classifies_buildpart_sketch_primitive_context_error() -> None:
    run_state = RunState(session_id="session-6", requirements={"description": "demo"})
    run_state.add_turn(
        TurnRecord(
            round_no=1,
            decision_summary="ellipse in wrong builder",
            tool_calls=[
                ToolCallRecord(
                    name="execute_build123d",
                    category=ToolCategory.WRITE,
                    arguments={"code": "with BuildPart():\n    Ellipse(12, 18)\n"},
                )
            ],
            tool_results=[
                ToolResultRecord(
                    name="execute_build123d",
                    category=ToolCategory.WRITE,
                    success=False,
                    payload={},
                    error=(
                        "Exit code: 1 | RuntimeError: BuildPart doesn't have a Ellipse "
                        "object or operation (Ellipse applies to ['BuildSketch'])"
                    ),
                )
            ],
        )
    )

    summary = build_run_hallucination_summary(run_state)

    assert summary["event_count"] == 1
    assert summary["categories"]["invalid_builder_context.buildsketch_only_primitive.ellipse"] == 1
    assert summary["weighted_score"] == pytest.approx(1.0)


def test_build_run_hallucination_summary_classifies_transform_context_manager_error() -> None:
    run_state = RunState(session_id="session-7", requirements={"description": "demo"})
    run_state.add_turn(
        TurnRecord(
            round_no=1,
            decision_summary="rotation used as context manager",
            tool_calls=[
                ToolCallRecord(
                    name="execute_build123d",
                    category=ToolCategory.WRITE,
                    arguments={"code": "with Rot(90, 0, 0):\n    pass\n"},
                )
            ],
            tool_results=[
                ToolResultRecord(
                    name="execute_build123d",
                    category=ToolCategory.WRITE,
                    success=False,
                    payload={},
                    error=(
                        "Exit code: 1 | TypeError: 'Rotation' object does not support the context manager protocol"
                    ),
                )
            ],
        )
    )

    summary = build_run_hallucination_summary(run_state)

    assert summary["event_count"] == 1
    assert summary["categories"]["invalid_builder_context.transform_context_manager.rotation"] == 1
    assert summary["weighted_score"] == pytest.approx(1.0)


def test_build_run_hallucination_summary_ignores_infrastructure_write_errors() -> None:
    run_state = RunState(session_id="session-8", requirements={"description": "demo"})
    run_state.add_turn(
        TurnRecord(
            round_no=1,
            decision_summary="write failed because docker daemon is unavailable",
            tool_calls=[
                ToolCallRecord(
                    name="execute_build123d",
                    category=ToolCategory.WRITE,
                    arguments={"code": "result = Box(10, 10, 4)"},
                )
            ],
            tool_results=[
                ToolResultRecord(
                    name="execute_build123d",
                    category=ToolCategory.WRITE,
                    success=False,
                    payload={},
                    error=(
                        "Docker API error | docker.errors.DockerException: Error while fetching "
                        "server API version: 503 Server Error for http+docker://localhost/version: "
                        "Service Unavailable (\"Docker Desktop is unable to start\")"
                    ),
                )
            ],
        )
    )

    summary = build_run_hallucination_summary(run_state)

    assert summary["event_count"] == 0
    assert summary["weighted_score"] == pytest.approx(0.0)
    assert summary["layers"] == {}
    assert summary["categories"] == {}


def test_build_run_hallucination_summary_ignores_missing_lib3mf_import_error() -> None:
    run_state = RunState(session_id="session-lib3mf", requirements={"description": "demo"})
    run_state.add_turn(
        TurnRecord(
            round_no=1,
            decision_summary="write failed because sandbox image misses lib3mf",
            tool_calls=[
                ToolCallRecord(
                    name="execute_build123d",
                    category=ToolCategory.WRITE,
                    arguments={"code": "result = Box(10, 10, 4)"},
                )
            ],
            tool_results=[
                ToolResultRecord(
                    name="execute_build123d",
                    category=ToolCategory.WRITE,
                    success=False,
                    payload={},
                    error=(
                        "Exit code: 1 | Traceback (most recent call last):\n"
                        "  File \"/app/aicad_runtime_main.py\", line 1, in <module>\n"
                        "    from build123d import *\n"
                        "  File \"/usr/local/lib/python3.11/site-packages/build123d/mesher.py\", line 112, in <module>\n"
                        "    from lib3mf import Lib3MF\n"
                        "ModuleNotFoundError: No module named 'lib3mf'\n"
                    ),
                )
            ],
        )
    )

    summary = build_run_hallucination_summary(run_state)

    assert summary["event_count"] == 0
    assert summary["weighted_score"] == pytest.approx(0.0)
    assert summary["layers"] == {}
    assert summary["categories"] == {}
