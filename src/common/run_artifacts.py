from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path


TIMESTAMP_RUN_ID_RE = re.compile(r"^\d{8}_\d{6}$")
DEFAULT_SPECIAL_KEEP_NAMES = ("latest", "by_practice", "archive")


def ensure_timestamp_run_id(value: str | None) -> str:
    run_id = str(value or "").strip()
    if not TIMESTAMP_RUN_ID_RE.fullmatch(run_id):
        raise ValueError("run id must stay timestamp-only: YYYYMMDD_HHMMSS")
    return run_id


@dataclass(frozen=True, slots=True)
class RunDirectoryClassification:
    path: Path
    name: str
    run_id: str | None
    run_date: date | None
    is_timestamp_run_id: bool
    is_canonical: bool
    is_special_keep_name: bool
    should_archive: bool
    keep_reason: str | None
    archive_reason: str | None


def _parse_run_date(run_id: str) -> date | None:
    try:
        return date(
            year=int(run_id[0:4]),
            month=int(run_id[4:6]),
            day=int(run_id[6:8]),
        )
    except Exception:
        return None


def classify_run_directory(
    path: Path,
    *,
    cutoff_date: date,
    special_keep_names: tuple[str, ...] = DEFAULT_SPECIAL_KEEP_NAMES,
) -> RunDirectoryClassification:
    name = path.name
    is_timestamp_run_id = TIMESTAMP_RUN_ID_RE.fullmatch(name) is not None
    is_special_keep_name = name in set(special_keep_names)
    run_date = _parse_run_date(name) if is_timestamp_run_id else None
    should_archive = False
    keep_reason: str | None = None
    archive_reason: str | None = None
    if name == "archive":
        keep_reason = "archive_root"
        is_special_keep_name = True
    elif is_special_keep_name:
        keep_reason = "special_keep_entry"
    elif is_timestamp_run_id and run_date is not None:
        should_archive = run_date < cutoff_date
        if should_archive:
            archive_reason = "before_cutoff_date"
        else:
            keep_reason = "on_or_after_cutoff_date"
    else:
        should_archive = True
        archive_reason = "noncanonical_name"
    return RunDirectoryClassification(
        path=path,
        name=name,
        run_id=name if is_timestamp_run_id else None,
        run_date=run_date,
        is_timestamp_run_id=is_timestamp_run_id,
        is_canonical=is_timestamp_run_id,
        is_special_keep_name=is_special_keep_name,
        should_archive=should_archive,
        keep_reason=keep_reason,
        archive_reason=archive_reason,
    )
