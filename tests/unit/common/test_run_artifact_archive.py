from __future__ import annotations

import importlib.util
from datetime import date
from pathlib import Path


def _load_archive_module():
    script_path = (
        Path(__file__).resolve().parents[3] / "scripts" / "archive_historical_runs.py"
    )
    spec = importlib.util.spec_from_file_location("archive_historical_runs", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_process_source_root_archives_old_runs_and_by_practice_links(tmp_path: Path) -> None:
    module = _load_archive_module()
    runs_root = tmp_path / "benchmark" / "runs"
    runs_root.mkdir(parents=True)

    archived_run = runs_root / "20260411_120000"
    archived_run.mkdir()
    (archived_run / "summary.json").write_text("{}\n", encoding="utf-8")

    kept_run = runs_root / "20260412_155352"
    kept_run.mkdir()
    (kept_run / "summary.json").write_text("{}\n", encoding="utf-8")

    latest = runs_root / "latest"
    latest.symlink_to(kept_run)

    by_practice_root = runs_root / "by_practice"
    by_practice_root.mkdir()
    canonical_old_link = by_practice_root / "20260411_120000__v2__l1__kimi__model__one_action"
    canonical_old_link.symlink_to(archived_run)
    noncanonical_old_link = by_practice_root / "20260411_l1_special__v2__l1__kimi__model__one_action"
    noncanonical_old_link.symlink_to(archived_run)
    kept_link = by_practice_root / "20260412_155352__v2__l1__kimi__model__one_action"
    kept_link.symlink_to(kept_run)

    manifest = module._process_source_root(
        source_root=runs_root,
        cutoff_date=date(2026, 4, 12),
        dry_run=False,
    )

    archive_root = runs_root / "archive" / "pre_20260412"
    archived_destination = archive_root / "20260411_120000"
    archived_canonical_link = archive_root / "by_practice" / canonical_old_link.name
    archived_noncanonical_link = archive_root / "by_practice" / noncanonical_old_link.name

    assert archived_destination.exists()
    assert not archived_run.exists()
    assert kept_run.exists()
    assert latest.is_symlink()
    assert latest.resolve() == kept_run.resolve()

    assert archived_canonical_link.is_symlink()
    assert archived_canonical_link.resolve() == archived_destination.resolve()
    assert archived_noncanonical_link.is_symlink()
    assert archived_noncanonical_link.resolve() == archived_destination.resolve()
    assert not canonical_old_link.exists()
    assert not noncanonical_old_link.exists()
    assert kept_link.is_symlink()
    assert kept_link.resolve() == kept_run.resolve()

    assert manifest["summary"]["selected_count"] == 1
    assert manifest["by_practice"]["selected_count"] == 2
    assert manifest["latest"]["status"] == "unchanged"


def test_process_source_root_repoints_latest_when_latest_target_is_archived(tmp_path: Path) -> None:
    module = _load_archive_module()
    runs_root = tmp_path / "test_runs"
    runs_root.mkdir(parents=True)

    archived_run = runs_root / "20260411_120000"
    archived_run.mkdir()
    kept_run = runs_root / "20260412_160110"
    kept_run.mkdir()

    latest = runs_root / "latest"
    latest.symlink_to(archived_run)

    manifest = module._process_source_root(
        source_root=runs_root,
        cutoff_date=date(2026, 4, 12),
        dry_run=False,
    )

    archived_destination = runs_root / "archive" / "pre_20260412" / "20260411_120000"
    assert archived_destination.exists()
    assert latest.is_symlink()
    assert latest.resolve() == kept_run.resolve()
    assert manifest["latest"]["status"] == "repointed"
    assert manifest["latest"]["target_run_id"] == "20260412_160110"


def test_process_source_root_archives_broken_by_practice_links_to_existing_archive_targets(
    tmp_path: Path,
) -> None:
    module = _load_archive_module()
    runs_root = tmp_path / "benchmark" / "runs"
    runs_root.mkdir(parents=True)

    kept_run = runs_root / "20260412_155352"
    kept_run.mkdir()
    existing_archive_target = runs_root / "archive" / "pre_20260406" / "20260405_130433"
    existing_archive_target.mkdir(parents=True)

    latest = runs_root / "latest"
    latest.symlink_to(kept_run)

    by_practice_root = runs_root / "by_practice"
    by_practice_root.mkdir()
    broken_old_link = by_practice_root / "20260405_130433__v2__l1__kimi__model__one_action"
    broken_old_link.symlink_to(
        Path("/Users/jerryx/code/aicad.subagent.iteration/benchmark/runs/20260405_130433")
    )

    manifest = module._process_source_root(
        source_root=runs_root,
        cutoff_date=date(2026, 4, 12),
        dry_run=False,
    )

    archived_link = (
        runs_root
        / "archive"
        / "pre_20260412"
        / "by_practice"
        / broken_old_link.name
    )
    assert archived_link.is_symlink()
    assert archived_link.resolve() == existing_archive_target.resolve()
    assert manifest["by_practice"]["selected_count"] == 1


def test_process_source_root_archives_noncanonical_target_links_to_existing_archive_targets(
    tmp_path: Path,
) -> None:
    module = _load_archive_module()
    runs_root = tmp_path / "benchmark" / "runs"
    runs_root.mkdir(parents=True)

    kept_run = runs_root / "20260412_155352"
    kept_run.mkdir()
    existing_archive_target = (
        runs_root / "archive" / "pre_20260406" / "20260408_144_l2_96_axis_consistency"
    )
    existing_archive_target.mkdir(parents=True)

    latest = runs_root / "latest"
    latest.symlink_to(kept_run)

    by_practice_root = runs_root / "by_practice"
    by_practice_root.mkdir()
    broken_old_link = (
        by_practice_root
        / "20260408_144_l2_96_axis_consistency__v2__l2__default__kimi_k2_5_thinking__one_action"
    )
    broken_old_link.symlink_to(
        Path(
            "/Users/jerryx/code/aicad.subagent.iteration/benchmark/runs/"
            "20260408_144_l2_96_axis_consistency"
        )
    )

    manifest = module._process_source_root(
        source_root=runs_root,
        cutoff_date=date(2026, 4, 12),
        dry_run=False,
    )

    archived_link = (
        runs_root
        / "archive"
        / "pre_20260412"
        / "by_practice"
        / broken_old_link.name
    )
    assert archived_link.is_symlink()
    assert archived_link.resolve() == existing_archive_target.resolve()
    assert manifest["by_practice"]["selected_count"] == 1


def test_process_source_root_repairs_existing_archived_by_practice_broken_links(
    tmp_path: Path,
) -> None:
    module = _load_archive_module()
    runs_root = tmp_path / "benchmark" / "runs"
    runs_root.mkdir(parents=True)

    kept_run = runs_root / "20260412_155352"
    kept_run.mkdir()
    latest = runs_root / "latest"
    latest.symlink_to(kept_run)

    existing_archive_target = (
        runs_root / "archive" / "pre_20260406" / "20260408_144_l2_96_axis_consistency"
    )
    existing_archive_target.mkdir(parents=True)
    current_archive_link = (
        runs_root
        / "archive"
        / "pre_20260412"
        / "by_practice"
        / "20260408_144_l2_96_axis_consistency__v2__l2__default__kimi_k2_5_thinking__one_action"
    )
    current_archive_link.parent.mkdir(parents=True, exist_ok=True)
    current_archive_link.symlink_to(
        Path(
            "/Users/jerryx/code/aicad.subagent.iteration/benchmark/runs/"
            "20260408_144_l2_96_axis_consistency"
        )
    )

    manifest = module._process_source_root(
        source_root=runs_root,
        cutoff_date=date(2026, 4, 12),
        dry_run=False,
    )

    assert current_archive_link.is_symlink()
    assert current_archive_link.resolve() == existing_archive_target.resolve()
    assert manifest["archive_repair"]["repaired_count"] == 1
