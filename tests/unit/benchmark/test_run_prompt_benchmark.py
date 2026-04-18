from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys


def _load_benchmark_module():
    script_path = Path(__file__).resolve().parents[3] / "benchmark" / "run_prompt_benchmark.py"
    benchmark_dir = script_path.parent
    benchmark_dir_str = str(benchmark_dir)
    if benchmark_dir_str not in sys.path:
        sys.path.insert(0, benchmark_dir_str)
    spec = importlib.util.spec_from_file_location("run_prompt_benchmark", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_select_cases_supports_named_case_set() -> None:
    module = _load_benchmark_module()
    case_map = {
        "L1_122": module.BenchmarkCase(
            case_id="L1_122",
            level="L1",
            prompt="a",
            prompt_field="prompt",
            csv_path="l1.csv",
            gt_step_path="/tmp/L1_122.step",
        ),
        "L1_148": module.BenchmarkCase(
            case_id="L1_148",
            level="L1",
            prompt="b",
            prompt_field="prompt",
            csv_path="l1.csv",
            gt_step_path="/tmp/L1_148.step",
        ),
        "L2_130": module.BenchmarkCase(
            case_id="L2_130",
            level="L2",
            prompt="c",
            prompt_field="prompt",
            csv_path="l2.csv",
            gt_step_path="/tmp/L2_130.step",
        ),
    }

    selected = module._select_cases(
        case_map=case_map,
        case_ids_raw="",
        levels_raw="",
        limit=0,
        case_set_name="canary",
        case_sets={
            "canary": ["L2_130", "L1_148"],
        },
    )

    assert [case.case_id for case in selected] == ["L2_130", "L1_148"]


def test_summarize_baseline_metrics_uses_existing_case_artifacts() -> None:
    module = _load_benchmark_module()
    case_payloads = [
        {
            "case_id": "L2_130",
            "analysis": {"status": "PASS"},
            "runtime_summary": {
                "planner_rounds": 3,
                "executed_action_count": 2,
                "validation_complete": True,
                "stale_probe_carry_count": 1,
                "evidence_conflict_count": 1,
                "build123d_hallucination": {
                    "event_count": 2,
                    "weighted_score": 1.6,
                    "primary_layer": "write_surface",
                    "layers": {"write_surface": 2},
                    "categories": {"invalid_api_contract": 2},
                },
            },
            "token_usage": {"total_tokens": 100},
            "round_digest": {
                "rounds": [
                    {
                        "round": 1,
                        "tool_results": [
                            {
                                "tool_name": "execute_build123d",
                                "category": "write",
                                "success": True,
                                "payload_summary": {
                                    "snapshot": {
                                        "geometry": {
                                            "solids": 1,
                                            "volume": 42.0,
                                        }
                                    }
                                },
                            }
                        ],
                    }
                ],
                "domain_kernel_summary": {
                    "repair_packet_count": 1,
                    "latest_repair_packet_family_id": "half_shell_profile",
                },
            },
        },
        {
            "case_id": "L1_148",
            "analysis": {"status": "RUN_ERROR"},
            "runtime_summary": {
                "planner_rounds": 2,
                "executed_action_count": 1,
                "validation_complete": False,
                "stale_probe_carry_count": 0,
                "evidence_conflict_count": 0,
                "build123d_hallucination": {
                    "event_count": 1,
                    "weighted_score": 0.6,
                    "primary_layer": "read_surface",
                    "layers": {"read_surface": 1},
                    "categories": {"targeting_without_readback": 1},
                },
            },
            "token_usage": {"total_tokens": 50},
            "round_digest": {
                "rounds": [
                    {
                        "round": 1,
                        "tool_results": [
                            {
                                "tool_name": "execute_build123d",
                                "category": "write",
                                "success": False,
                                "payload_summary": {},
                            }
                        ],
                    }
                ],
                "domain_kernel_summary": {
                    "repair_packet_count": 0,
                    "latest_repair_packet_family_id": None,
                },
            },
        },
    ]

    summary = module._summarize_baseline_metrics(case_payloads)

    assert summary["total_cases"] == 2
    assert summary["first_solid_success_case_count"] == 1
    assert summary["first_solid_success_rate"] == 0.5
    assert summary["requirement_complete_rate"] == 0.5
    assert summary["runtime_rewrite_rate"] == 1 / 3
    assert summary["mean_repair_turns_after_first_write"] == 2.0
    assert summary["stale_evidence_incidents"] == 2
    assert summary["tokens_per_successful_case"] == 100.0
    assert summary["family_repair_packet_case_count"] == 1
    assert summary["family_repair_packet_hit_case_count"] == 1
    assert summary["family_repair_packet_hit_rate"] == 1.0
    assert summary["hallucination_event_count"] == 3
    assert summary["hallucination_weighted_score_mean"] == 1.1
    assert summary["hallucination_primary_layer_counts"] == {
        "write_surface": 1,
        "read_surface": 1,
    }


def test_build_brief_case_row_includes_hallucination_columns() -> None:
    module = _load_benchmark_module()
    row = module._build_brief_case_row(
        {
            "case_id": "L2_172",
            "analysis": {"status": "VALIDATOR_MISMATCH", "likely_root_cause": "demo"},
            "evaluation": {"passed": True, "score": 0.91},
            "runtime_summary": {
                "planner_rounds": 5,
                "executed_action_count": 2,
                "validation_complete": False,
                "build123d_hallucination": {
                    "event_count": 4,
                    "weighted_score": 2.1,
                    "primary_layer": "write_surface",
                    "layers": {"write_surface": 3, "validation_surface": 1},
                    "categories": {"invalid_api_contract": 3, "validation_overclaim": 1},
                },
            },
            "prompt_metrics": {"max_final_chars": 14000},
            "token_usage": {"total_tokens": 222},
            "round_digest": {"domain_kernel_summary": {"available": True}},
        }
    )

    assert row["hallucination_events"] == 4
    assert row["hallucination_weighted_score"] == 2.1
    assert row["hallucination_primary_layer"] == "write_surface"


def test_diagnose_case_keeps_concrete_runtime_error_primary(tmp_path: Path) -> None:
    module = _load_benchmark_module()
    case_dir = tmp_path / "L2_149"
    (case_dir / "queries").mkdir(parents=True)

    diagnosis = module._diagnose_case(
        case_dir=case_dir,
        return_code=0,
        timed_out=False,
        case_summary_payload={
            "summary": {
                "last_error": "Exit code: 139",
                "planner_rounds": 4,
                "inspection_only_rounds": 0,
                "validation_complete": False,
                "converged": False,
            }
        },
        evaluation_payload={},
        generated_step_path=case_dir / "outputs" / "model.step",
        prompt_metrics={"max_final_chars": 25001},
        trace_summary={
            "feature_graph_summary": {
                "available": True,
                "graph_query_count": 0,
                "blocked_node_ids": ["blocker.feature_path_sweep_result"],
            }
        },
    )

    assert diagnosis["failure_category"] == "runtime_error"
    assert diagnosis["likely_root_cause"] == "Exit code: 139"


def test_diagnose_case_keeps_context_pressure_note_for_validation_gap(tmp_path: Path) -> None:
    module = _load_benchmark_module()
    case_dir = tmp_path / "L2_172"
    (case_dir / "queries").mkdir(parents=True)
    (case_dir / "queries" / "round_03_validate_requirement_post_write.json").write_text(
        json.dumps({"is_complete": False, "blockers": ["feature_countersink"]}),
        encoding="utf-8",
    )

    diagnosis = module._diagnose_case(
        case_dir=case_dir,
        return_code=0,
        timed_out=False,
        case_summary_payload={
            "summary": {
                "last_error": None,
                "planner_rounds": 8,
                "inspection_only_rounds": 3,
                "validation_complete": False,
                "converged": False,
            }
        },
        evaluation_payload={"passed": True},
        generated_step_path=case_dir / "outputs" / "model.step",
        prompt_metrics={"max_final_chars": 26000},
        trace_summary={
            "feature_graph_summary": {
                "available": True,
                "graph_query_count": 0,
                "blocked_node_ids": ["blocker.feature_countersink"],
            }
        },
    )

    assert diagnosis["failure_category"] == "validator_evaluator_disagreement"
    assert "Prompt context grew large during the run." in diagnosis["likely_root_cause"]
    assert "query_kernel_state before stopping" in diagnosis["likely_root_cause"]
