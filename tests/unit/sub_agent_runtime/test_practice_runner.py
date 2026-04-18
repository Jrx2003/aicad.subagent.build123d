from __future__ import annotations

import json

from sub_agent_runtime.practice_runner import (
    PracticeSeed,
    _summarize_read_model_usage,
    _write_practice_run_diagnostics,
    expand_practice_seed_variant,
)


def test_expand_practice_seed_variant_is_deterministic_and_records_expansion_parameters() -> None:
    seed = PracticeSeed(
        seed_id="clamshell_enclosure",
        title="Clamshell enclosure",
        prompt_template=(
            "Create a {closure_style} enclosure with overall size "
            "{width_mm}mm x {depth_mm}mm x {height_mm}mm and a {hinge_style}."
        ),
        difficulty_band="high",
        expected_part_count=2,
        target_feature_families=["half_shell", "directional_hole"],
        local_topology_targeting_expected=False,
        variation_knobs={
            "closure_style": ["magnetic", "snap"],
            "width_mm": [72, 80],
            "depth_mm": [58, 64],
            "height_mm": [24, 32],
            "hinge_style": ["living hinge", "pin hinge"],
        },
    )

    first = expand_practice_seed_variant(seed, variant_index=3)
    second = expand_practice_seed_variant(seed, variant_index=3)

    assert first.variant_id == second.variant_id
    assert first.prompt == second.prompt
    assert first.expansion_parameters == second.expansion_parameters
    assert first.expected_part_count == 2
    assert first.target_feature_families == ["half_shell", "directional_hole"]
    assert first.expansion_parameters["closure_style"] in {"magnetic", "snap"}
    assert "enclosure" in first.prompt


def test_summarize_read_model_usage_captures_host_roles_and_local_targeting(
    tmp_path,
) -> None:
    case_dir = tmp_path / "practice_case"
    queries_dir = case_dir / "queries"
    actions_dir = case_dir / "actions"
    queries_dir.mkdir(parents=True)
    actions_dir.mkdir(parents=True)

    (queries_dir / "round_01_query_topology.json").write_text(
        json.dumps(
            {
                "matched_ref_ids": ["face:1:F_TOP", "edge:1:E_RIM"],
                "candidate_sets": [
                    {
                        "candidate_id": "mating_faces",
                        "label": "Mating Faces",
                        "entity_type": "face",
                        "family_id": "explicit_anchor_hole",
                        "family_ids": ["explicit_anchor_hole", "named_face_local_edit"],
                        "preferred_ref_id": "face:1:F_TOP",
                        "preferred_entity_id": "F_TOP",
                        "ref_ids": ["face:1:F_TOP"],
                        "metadata": {
                            "host_role": "mating_face",
                            "semantic_host_roles": ["mating_face", "split_plane"],
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (queries_dir / "round_02_query_kernel_state.json").write_text("{}", encoding="utf-8")
    (queries_dir / "round_03_validate_requirement.json").write_text("{}", encoding="utf-8")
    (actions_dir / "round_04_apply_cad_action_create_sketch.json").write_text(
        json.dumps(
            {
                "action_history": [
                    {
                        "action_type": "create_sketch",
                        "action_params": {"face_ref": "face:1:F_TOP"},
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (actions_dir / "round_05_apply_cad_action_chamfer.json").write_text(
        json.dumps(
            {
                "action_history": [
                    {
                        "action_type": "chamfer",
                        "action_params": {"edge_refs": ["edge:2:E_A", "edge:2:E_B"]},
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    summary = _summarize_read_model_usage(
        case_dir=case_dir,
        round_digest={"domain_kernel_summary": {"graph_query_count": 1, "graph_patch_count": 2}},
    )

    assert summary["query_counts"]["query_topology"] == 1
    assert summary["query_counts"]["query_kernel_state"] == 1
    assert summary["query_counts"]["validate_requirement"] == 1
    assert summary["matched_ref_id_count"] == 2
    assert summary["candidate_set_count"] == 1
    assert summary["candidate_family_ids"] == [
        "explicit_anchor_hole",
        "named_face_local_edit",
    ]
    assert summary["candidate_host_roles"] == ["mating_face", "split_plane"]
    assert summary["local_targeting_action_count"] == 2
    assert summary["face_ref_action_count"] == 1
    assert summary["edge_ref_action_count"] == 1
    assert summary["host_role_targeting_observed"] is True
    assert summary["topology_targeting_observed"] is True
    assert summary["topology_examples"][0]["candidate_sets"][0]["family_id"] == (
        "explicit_anchor_hole"
    )
    assert summary["topology_examples"][0]["candidate_sets"][0]["preferred_ref_id"] == (
        "face:1:F_TOP"
    )


def test_write_practice_run_diagnostics_only_counts_real_query_topology_usage(tmp_path) -> None:
    run_root = tmp_path / "practice_run"
    run_root.mkdir()

    case_payloads = [
        {
            "practice_analysis": {
                "case_id": "kernel_only_case",
                "status": "incomplete",
                "hallucination": {"weighted_score": 0.2, "primary_layer": "write_surface"},
                "issue": "semantic refresh only",
                "topology_read_model_usage": {
                    "query_counts": {
                        "query_kernel_state": 1,
                        "validate_requirement": 2,
                    },
                    "local_targeting_action_count": 0,
                    "host_role_targeting_observed": False,
                },
            }
        },
        {
            "practice_analysis": {
                "case_id": "topology_case",
                "status": "incomplete",
                "hallucination": {"weighted_score": 0.1, "primary_layer": "read_surface"},
                "issue": "topology read happened",
                "topology_read_model_usage": {
                    "query_counts": {
                        "query_topology": 1,
                    },
                    "local_targeting_action_count": 1,
                    "host_role_targeting_observed": True,
                },
            }
        },
    ]

    _write_practice_run_diagnostics(run_root=run_root, case_payloads=case_payloads)

    payload = json.loads((run_root / "run_diagnostics.json").read_text(encoding="utf-8"))
    assert payload["topology_query_cases"] == ["topology_case"]
