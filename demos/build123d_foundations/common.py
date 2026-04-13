from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

from build123d import Part, Shape, export_step

ARTIFACT_DIR = Path(__file__).resolve().parent / "artifacts"


def ensure_artifact_dir() -> Path:
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    return ARTIFACT_DIR


def as_part(shape: Part | Shape) -> Part:
    return shape if isinstance(shape, Part) else Part(shape)


def describe_part(part: Part) -> dict[str, object]:
    bbox = part.bounding_box()
    return {
        "volume": round(part.volume, 3),
        "bbox": {
            "x": round(bbox.size.X, 3),
            "y": round(bbox.size.Y, 3),
            "z": round(bbox.size.Z, 3),
        },
    }


def export_artifact(
    stem: str,
    shape: Part | Shape,
    *,
    title: str,
    narrative: str,
    talking_points: Iterable[str],
) -> dict[str, object]:
    part = as_part(shape)
    artifact_dir = ensure_artifact_dir()
    step_path = artifact_dir / f"{stem}.step"
    export_step(part, step_path)
    return {
        "stem": stem,
        "title": title,
        "narrative": narrative,
        "talking_points": list(talking_points),
        "step_path": str(step_path.relative_to(Path(__file__).resolve().parent)),
        **describe_part(part),
    }


def write_suite_summary(entries: list[dict[str, object]]) -> Path:
    artifact_dir = ensure_artifact_dir()
    summary_path = artifact_dir / "summary.json"
    summary_path.write_text(
        json.dumps({"entries": entries}, indent=2, ensure_ascii=False) + "\n"
    )
    return summary_path
