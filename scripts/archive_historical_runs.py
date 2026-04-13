#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any

ROOT_DIR = Path(os.environ.get("AICAD_REPO_ROOT", Path(__file__).resolve().parents[1]))
SRC_DIR = ROOT_DIR / "src"
for _path in (SRC_DIR, ROOT_DIR):
    _path_str = str(_path)
    if _path_str not in sys.path:
        sys.path.insert(0, _path_str)

from common.run_artifacts import TIMESTAMP_RUN_ID_RE, classify_run_directory


DEFAULT_CUTOFF_DATE = date(2026, 4, 6)
SOURCE_ROOTS = (Path("benchmark") / "runs", Path("test_runs"))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Archive historical run directories under per-root pre-cutoff manifests."
    )
    parser.add_argument(
        "--cutoff",
        type=_parse_cutoff_date,
        default=DEFAULT_CUTOFF_DATE,
        help="Archive timestamp runs older than this YYYY-MM-DD cutoff date.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report archive candidates without moving files.",
    )
    return parser.parse_args()


def _parse_cutoff_date(value: str) -> date:
    text = str(value or "").strip()
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "cutoff must be an ISO date like 2026-04-06"
        ) from exc


def _resolve_root(path: Path) -> Path:
    return path if path.is_absolute() else (ROOT_DIR / path).resolve()


def _safe_destination(archive_root: Path, source_name: str) -> Path:
    candidate = archive_root / source_name
    if not candidate.exists():
        return candidate
    suffix = datetime.now().strftime("%Y%m%d_%H%M%S")
    index = 1
    while True:
        fallback = archive_root / f"{source_name}__{suffix}_{index}"
        if not fallback.exists():
            return fallback
        index += 1


def _archive_candidate(
    *,
    source_dir: Path,
    archive_root: Path,
    cutoff_date: date,
    dry_run: bool,
) -> dict[str, Any]:
    classification = classify_run_directory(source_dir, cutoff_date=cutoff_date)
    destination = _safe_destination(archive_root, classification.name)
    if not dry_run:
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source_dir), str(destination))
    return {
        "name": classification.name,
        "source": str(source_dir),
        "destination": str(destination),
        "run_date": classification.run_date.isoformat() if classification.run_date else None,
        "reason": classification.archive_reason,
        "classification": "timestamp_run",
        "archived": not dry_run,
    }


def _keep_entry(
    *,
    source_dir: Path,
    cutoff_date: date,
) -> dict[str, Any]:
    classification = classify_run_directory(source_dir, cutoff_date=cutoff_date)
    return {
        "name": classification.name,
        "source": str(source_dir),
        "run_date": classification.run_date.isoformat() if classification.run_date else None,
        "reason": classification.keep_reason,
        "classification": (
            "timestamp_run"
            if classification.is_timestamp_run_id
            else "special_keep"
            if classification.is_special_keep_name
            else "noncanonical"
        ),
        "archived": False,
    }


def _archive_root_for(source_root: Path, cutoff_date: date) -> Path:
    return source_root / "archive" / f"pre_{cutoff_date:%Y%m%d}"


def _remove_path(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
        return
    if path.exists():
        shutil.rmtree(path)


def _symlink_target_basename(path: Path) -> str | None:
    raw_target = _raw_symlink_target(path)
    if raw_target is None:
        return None
    target_name = Path(raw_target).name
    if not target_name:
        return None
    return target_name


def _timestamp_run_id_from_symlink_target(path: Path) -> str | None:
    target_name = _symlink_target_basename(path)
    if target_name is None:
        return None
    if TIMESTAMP_RUN_ID_RE.fullmatch(target_name):
        return target_name
    return None


def _raw_symlink_target(path: Path) -> str | None:
    if not path.is_symlink():
        return None
    try:
        return os.readlink(path)
    except OSError:
        return None


def _parse_run_date_from_run_id(run_id: str | None) -> date | None:
    if not isinstance(run_id, str) or TIMESTAMP_RUN_ID_RE.fullmatch(run_id) is None:
        return None
    try:
        return date(
            year=int(run_id[0:4]),
            month=int(run_id[4:6]),
            day=int(run_id[6:8]),
        )
    except Exception:
        return None


def _parse_leading_date_from_name(name: str | None) -> date | None:
    text = str(name or "").strip()
    if len(text) < 8 or not text[:8].isdigit():
        return None
    try:
        return date(
            year=int(text[0:4]),
            month=int(text[4:6]),
            day=int(text[6:8]),
        )
    except Exception:
        return None


def _find_existing_archived_run(source_root: Path, run_id: str) -> Path | None:
    archive_parent = source_root / "archive"
    if not archive_parent.exists():
        return None
    for candidate in sorted(archive_parent.glob(f"*/{run_id}")):
        if candidate.is_dir():
            return candidate
    return None


def _find_existing_archived_entry(source_root: Path, entry_name: str | None) -> Path | None:
    text = str(entry_name or "").strip()
    if not text:
        return None
    archive_parent = source_root / "archive"
    if not archive_parent.exists():
        return None
    for candidate in sorted(archive_parent.glob(f"*/{text}")):
        if candidate.exists():
            return candidate
    return None


def _resolve_raw_symlink_target_path(path: Path, raw_target: str | None) -> Path | None:
    text = str(raw_target or "").strip()
    if not text:
        return None
    candidate = Path(text)
    if candidate.is_absolute():
        return candidate
    return (path.parent / candidate).resolve()


def _archive_by_practice_links(
    *,
    source_root: Path,
    archive_root: Path,
    archived_run_destinations: dict[str, Path],
    cutoff_date: date,
    dry_run: bool,
) -> dict[str, Any]:
    by_practice_root = source_root / "by_practice"
    selected: list[dict[str, Any]] = []
    kept: list[dict[str, Any]] = []
    if not by_practice_root.exists():
        return {
            "source_root": str(by_practice_root),
            "archive_root": str(archive_root / "by_practice"),
            "selected": selected,
            "kept": kept,
            "selected_count": 0,
            "kept_count": 0,
        }

    archived_link_root = archive_root / "by_practice"
    for candidate in sorted(by_practice_root.iterdir()):
        target_basename = _symlink_target_basename(candidate)
        linked_run_id = _timestamp_run_id_from_symlink_target(candidate)
        linked_run_date = _parse_run_date_from_run_id(linked_run_id)
        name_date = _parse_leading_date_from_name(candidate.name)
        should_archive = (
            linked_run_date is not None and linked_run_date < cutoff_date
        ) or (linked_run_date is None and name_date is not None and name_date < cutoff_date)
        archived_target = None
        lookup_name = linked_run_id if isinstance(linked_run_id, str) else target_basename
        if isinstance(lookup_name, str):
            archived_target = archived_run_destinations.get(lookup_name)
            if archived_target is None and should_archive:
                archived_target = _find_existing_archived_entry(source_root, lookup_name)
        if not should_archive:
            kept.append(
                {
                    "name": candidate.name,
                    "source": str(candidate),
                    "target_run_id": linked_run_id,
                    "archived": False,
                    "reason": "linked_run_kept_or_unknown",
                }
            )
            continue
        raw_target = _raw_symlink_target(candidate)
        raw_target_path = _resolve_raw_symlink_target_path(candidate, raw_target)
        if archived_target is None and isinstance(lookup_name, str):
            source_target = source_root / lookup_name
            if source_target.exists():
                archived_target = source_target
        if archived_target is None and raw_target_path is not None and raw_target_path.exists():
            archived_target = raw_target_path
        if archived_target is None:
            destination = _safe_destination(archived_link_root, f"{candidate.name}.json")
            record = {
                "name": candidate.name,
                "source": str(candidate),
                "raw_target": raw_target,
                "target_basename": target_basename,
                "target_run_id": linked_run_id,
                "reason": "archive_target_unresolved",
            }
            if not dry_run:
                destination.parent.mkdir(parents=True, exist_ok=True)
                _remove_path(candidate)
                destination.write_text(
                    json.dumps(record, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8",
                )
            selected.append(
                {
                    "name": candidate.name,
                    "source": str(candidate),
                    "destination": str(destination),
                    "target_run_id": linked_run_id,
                    "target_destination": raw_target,
                    "archived": not dry_run,
                    "reason": "archived_as_unresolved_record",
                }
            )
            continue
        destination = _safe_destination(archived_link_root, candidate.name)
        if not dry_run:
            destination.parent.mkdir(parents=True, exist_ok=True)
            _remove_path(candidate)
            if archived_target is not None:
                destination.symlink_to(archived_target)
            else:
                destination.symlink_to(raw_target)
        selected.append(
            {
                "name": candidate.name,
                "source": str(candidate),
                "destination": str(destination),
                "target_run_id": linked_run_id,
                "target_destination": (
                    str(archived_target) if archived_target is not None else raw_target
                ),
                "archived": not dry_run,
                "reason": "linked_run_archived",
            }
        )

    return {
        "source_root": str(by_practice_root),
        "archive_root": str(archived_link_root),
        "selected": selected,
        "kept": kept,
        "selected_count": len(selected),
        "kept_count": len(kept),
    }


def _repair_existing_archived_by_practice_links(
    *,
    source_root: Path,
    dry_run: bool,
) -> dict[str, Any]:
    archive_parent = source_root / "archive"
    repaired: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []
    if not archive_parent.exists():
        return {
            "archive_root": str(archive_parent),
            "repaired": repaired,
            "unresolved": unresolved,
            "repaired_count": 0,
            "unresolved_count": 0,
        }

    for candidate in sorted(archive_parent.glob("*/by_practice/*")):
        if not candidate.is_symlink() or candidate.exists():
            continue
        target_basename = _symlink_target_basename(candidate)
        repaired_target = _find_existing_archived_entry(source_root, target_basename)
        if repaired_target is None:
            unresolved.append(
                {
                    "path": str(candidate),
                    "raw_target": _raw_symlink_target(candidate),
                    "target_basename": target_basename,
                    "reason": "archive_target_unresolved",
                }
            )
            continue
        if not dry_run:
            candidate.unlink()
            candidate.symlink_to(repaired_target)
        repaired.append(
            {
                "path": str(candidate),
                "target_basename": target_basename,
                "target": str(repaired_target),
                "archived": not dry_run,
                "reason": "relinked_to_existing_archive_entry",
            }
        )

    return {
        "archive_root": str(archive_parent),
        "repaired": repaired,
        "unresolved": unresolved,
        "repaired_count": len(repaired),
        "unresolved_count": len(unresolved),
    }


def _refresh_latest_link(
    *,
    source_root: Path,
    archived_run_destinations: dict[str, Path],
    kept_entries: list[dict[str, Any]],
    cutoff_date: date,
    dry_run: bool,
) -> dict[str, Any]:
    latest = source_root / "latest"
    if not latest.exists() and not latest.is_symlink():
        return {"path": str(latest), "status": "missing"}

    current_run_id = _timestamp_run_id_from_symlink_target(latest)
    current_run_date = _parse_run_date_from_run_id(current_run_id)
    should_repoint = (
        current_run_id in archived_run_destinations
        or (current_run_date is not None and current_run_date < cutoff_date)
    )
    if not should_repoint:
        return {
            "path": str(latest),
            "status": "unchanged",
            "target_run_id": current_run_id,
        }

    kept_run_ids = sorted(
        entry["name"]
        for entry in kept_entries
        if entry.get("classification") == "timestamp_run"
    )
    replacement_target: Path
    replacement_run_id: str | None
    if kept_run_ids:
        replacement_run_id = kept_run_ids[-1]
        replacement_target = source_root / replacement_run_id
    else:
        replacement_run_id = current_run_id
        replacement_target = archived_run_destinations[current_run_id]

    if not dry_run:
        _remove_path(latest)
        latest.symlink_to(replacement_target)
    return {
        "path": str(latest),
        "status": "repointed",
        "previous_run_id": current_run_id,
        "target_run_id": replacement_run_id,
        "target": str(replacement_target),
    }


def _process_source_root(
    *,
    source_root: Path,
    cutoff_date: date,
    dry_run: bool,
) -> dict[str, Any]:
    archive_root = _archive_root_for(source_root, cutoff_date)
    archive_root.mkdir(parents=True, exist_ok=True)
    selected: list[dict[str, Any]] = []
    kept: list[dict[str, Any]] = []
    archived_run_destinations: dict[str, Path] = {}

    if source_root.exists():
        for candidate in sorted(source_root.iterdir()):
            if not candidate.is_dir():
                continue
            classification = classify_run_directory(candidate, cutoff_date=cutoff_date)
            if classification.should_archive:
                archived_entry = _archive_candidate(
                    source_dir=candidate,
                    archive_root=archive_root,
                    cutoff_date=cutoff_date,
                    dry_run=dry_run,
                )
                selected.append(archived_entry)
                archived_run_destinations[classification.name] = Path(
                    archived_entry["destination"]
                )
            else:
                kept.append(_keep_entry(source_dir=candidate, cutoff_date=cutoff_date))

    by_practice_manifest = (
        _archive_by_practice_links(
            source_root=source_root,
            archive_root=archive_root,
            archived_run_destinations=archived_run_destinations,
            cutoff_date=cutoff_date,
            dry_run=dry_run,
        )
        if source_root.name == "runs"
        else {
            "source_root": str(source_root / "by_practice"),
            "archive_root": str(archive_root / "by_practice"),
            "selected": [],
            "kept": [],
            "selected_count": 0,
            "kept_count": 0,
        }
    )
    latest_manifest = _refresh_latest_link(
        source_root=source_root,
        archived_run_destinations=archived_run_destinations,
        kept_entries=kept,
        cutoff_date=cutoff_date,
        dry_run=dry_run,
    )
    archive_repair_manifest = _repair_existing_archived_by_practice_links(
        source_root=source_root,
        dry_run=dry_run,
    )

    manifest = {
        "source_root": str(source_root),
        "archive_root": str(archive_root),
        "cutoff": cutoff_date.isoformat(),
        "dry_run": bool(dry_run),
        "selected": selected,
        "kept": kept,
        "by_practice": by_practice_manifest,
        "latest": latest_manifest,
        "archive_repair": archive_repair_manifest,
        "summary": {
            "selected_count": len(selected),
            "kept_count": len(kept),
            "by_practice_selected_count": by_practice_manifest["selected_count"],
            "by_practice_kept_count": by_practice_manifest["kept_count"],
        },
    }
    manifest_path = archive_root / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest


def _main() -> int:
    args = _parse_args()
    manifests = []
    for source_root in SOURCE_ROOTS:
        manifests.append(
            _process_source_root(
                source_root=_resolve_root(source_root),
                cutoff_date=args.cutoff,
                dry_run=args.dry_run,
            )
        )
    print(json.dumps({"roots": manifests}, ensure_ascii=True, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
