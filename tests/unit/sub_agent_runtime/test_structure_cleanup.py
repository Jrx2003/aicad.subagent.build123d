from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
RUNTIME_ROOT = REPO_ROOT / "src" / "sub_agent_runtime"
TEST_ROOT = REPO_ROOT / "tests" / "unit" / "sub_agent_runtime"
DOC_ROOT = REPO_ROOT / "docs"

LEGACY_RUNTIME_MODULES = [
    "agent_loop_v2.py",
    "tool_runtime.py",
    "feature_graph.py",
    "context_manager.py",
    "skill_pack.py",
    "tool_adapters.py",
    "skill_runtime_guidance.py",
    "cad_action_interface.py",
    "repair_packet_support.py",
]

LEGACY_IMPORT_MARKERS = [
    "sub_agent_runtime.agent_loop_v2",
    "sub_agent_runtime.tool_runtime",
    "sub_agent_runtime.feature_graph",
    "sub_agent_runtime.context_manager",
    "sub_agent_runtime.skill_pack",
    "sub_agent_runtime.tool_adapters",
    "sub_agent_runtime.skill_runtime_guidance",
    "sub_agent_runtime.cad_action_interface",
    "sub_agent_runtime.repair_packet_support",
]


def test_legacy_runtime_facades_are_removed() -> None:
    remaining = [
        path.name for path in (RUNTIME_ROOT / name for name in LEGACY_RUNTIME_MODULES) if path.exists()
    ]

    assert remaining == []


def test_no_live_code_tests_or_docs_reference_removed_legacy_facades() -> None:
    search_roots = [
        REPO_ROOT / "src",
        TEST_ROOT,
        DOC_ROOT / "CAD_ACTION_ITERATION.md",
        DOC_ROOT / "cad_iteration",
    ]
    offenders: list[str] = []
    for root in search_roots:
        paths = [root] if root.is_file() else sorted(root.rglob("*.py")) + sorted(root.rglob("*.md")) + sorted(root.rglob("*.json"))
        for path in paths:
            if "docs/archive/" in str(path):
                continue
            if path.resolve() == Path(__file__).resolve():
                continue
            text = path.read_text()
            for marker in LEGACY_IMPORT_MARKERS:
                if marker in text:
                    offenders.append(f"{path.relative_to(REPO_ROOT)}::{marker}")

    assert offenders == []


def test_tooling_lint_modules_no_longer_depend_on_execution_batch_for_rules() -> None:
    lint_root = RUNTIME_ROOT / "tooling" / "lint"
    offenders = []
    for path in sorted(lint_root.rglob("*.py")):
        text = path.read_text()
        if "execution.batch" in text or "tooling import execution as _legacy" in text:
            offenders.append(str(path.relative_to(REPO_ROOT)))

    assert offenders == []


def test_requirements_module_no_longer_depends_on_skill_assembly_legacy_proxy() -> None:
    requirements_path = RUNTIME_ROOT / "prompting" / "requirements.py"

    assert "skill_assembly as _legacy" not in requirements_path.read_text()


def test_shared_and_batch_no_longer_define_moved_owner_helpers() -> None:
    shared_text = (RUNTIME_ROOT / "orchestration" / "policy" / "shared.py").read_text()
    batch_text = (RUNTIME_ROOT / "tooling" / "execution" / "batch.py").read_text()

    assert "def _preferred_probe_families_for_turn(" not in shared_text
    assert "def _turn_policy_from_actionable_kernel_patch(" not in shared_text
    assert "def _latest_apply_action_type_from_turn(" not in shared_text
    assert "def _turn_has_open_sketch_window_after_successful_apply(" not in shared_text
    assert "def _latest_successful_apply_action_type_with_open_sketch_window(" not in shared_text
    assert "def _latest_successful_tool_payload(" not in shared_text
    assert "def _open_sketch_window_requires_code_escape(" not in shared_text
    assert "def _preferred_sketch_window_tools(" not in shared_text
    assert "def _open_sketch_window_requires_apply_write_first(" not in shared_text
    assert "def _has_recent_semantic_refresh_before_round(" not in shared_text
    assert "def _filter_supported_round_tool_names(" not in shared_text
    assert "def _build_repair_packet_round_observability_events(" not in shared_text
    assert "def _blockers_prefer_probe_first_after_code_write(" not in shared_text
    assert "def _runtime_repair_packet_observability_summary(" not in shared_text
    assert "def _infer_runtime_failure_cluster(" not in shared_text
    assert "def _latest_failed_code_sequence_is_artifactless(" not in shared_text
    assert "def _payload_has_step_artifact(" not in shared_text
    assert "def _is_successful_validation(" not in shared_text
    assert "def _pick_step_file(" not in shared_text
    assert "def _pick_render_file(" not in shared_text
    assert "def _should_auto_validate_after_non_progress(" not in shared_text
    assert "def _should_auto_validate_after_post_write(" not in shared_text
    assert "def _result_has_positive_session_backed_solid(" not in shared_text
    assert "def _payload_has_positive_session_backed_solid(" not in shared_text
    assert "def _buildsketch_candidate_is_host_profile(" not in batch_text
    assert "def _collect_named_plane_aliases(" not in batch_text
    assert "def _find_buildsketch_curve_context_hits(" not in batch_text
    assert "def _find_annular_profile_face_extraction_sweep_hits(" not in batch_text
    assert "def _find_builder_method_reference_assignment_hits(" not in batch_text
    assert "def _candidate_lint_family_ids(" not in batch_text
    assert "def _named_face_requirement_plane_groups(" not in batch_text
    assert "def _with_context_builder_name(" not in batch_text
