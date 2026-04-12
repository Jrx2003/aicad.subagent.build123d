from __future__ import annotations

import argparse
import asyncio
import csv
import json
import tempfile
from pathlib import Path
from typing import Any

from common.config import settings
from sandbox.docker_runner import DockerSandboxRunner
from step_similarity_eval import _evaluate_step_pair_async


CSV_ENCODINGS = ("utf-8", "utf-8-sig", "gb18030", "gbk", "latin1")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Execute sampled benchmark generated_code in the sandbox and compare it "
            "against the stored GT STEP for dataset consistency auditing."
        )
    )
    parser.add_argument(
        "--dataset-root",
        type=Path,
        default=Path("benchmark/sampled_10_per_L"),
        help="Root containing L1/L2/L3 sampled CSV + steps.",
    )
    parser.add_argument(
        "--cases",
        type=str,
        default="",
        help="Comma-separated case ids to audit, e.g. L1_148,L1_159.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Optional JSON output path.",
    )
    return parser.parse_args()


def _decode_csv(path: Path) -> tuple[list[dict[str, str]], str]:
    for encoding in CSV_ENCODINGS:
        try:
            with path.open("r", encoding=encoding, newline="") as handle:
                rows = list(csv.DictReader(handle))
            return rows, encoding
        except UnicodeDecodeError:
            continue
    raise RuntimeError(f"failed to decode CSV: {path}")


def _load_rows(dataset_root: Path) -> dict[str, dict[str, Any]]:
    case_rows: dict[str, dict[str, Any]] = {}
    for level in ("L1", "L2", "L3"):
        csv_path = dataset_root / level / f"{level}_sampled_rows.csv"
        if not csv_path.exists():
            continue
        rows, encoding = _decode_csv(csv_path)
        for row in rows:
            case_id = str(row.get("id", "")).strip()
            if not case_id:
                continue
            case_rows[case_id] = {
                "level": level,
                "csv_path": str(csv_path.resolve()),
                "csv_encoding": encoding,
                "generated_code": str(row.get("generated_code", "")),
                "geo_prompt_en": str(row.get("geo_prompt_en", "")),
                "pro_prompt_en": str(row.get("pro_prompt_en", "")),
                "gt_step_path": str((dataset_root / level / "steps" / f"{case_id}.step").resolve()),
            }
    return case_rows


async def _audit_case(
    case_id: str,
    payload: dict[str, Any],
    runner: DockerSandboxRunner,
) -> dict[str, Any]:
    generated_code = str(payload.get("generated_code", ""))
    sandbox_result = await runner.execute(generated_code, timeout=settings.sandbox_timeout)
    result: dict[str, Any] = {
        "case_id": case_id,
        "level": payload.get("level"),
        "csv_path": payload.get("csv_path"),
        "csv_encoding": payload.get("csv_encoding"),
        "gt_step_path": payload.get("gt_step_path"),
        "sandbox_success": sandbox_result.success,
        "sandbox_error": sandbox_result.error_message,
        "score": None,
        "passed": None,
        "difference_notes": [],
        "generated_stats": None,
        "ground_truth_stats": None,
    }
    if not sandbox_result.success or "model.step" not in sandbox_result.output_file_contents:
        return result

    with tempfile.TemporaryDirectory(prefix=f"aicad-audit-{case_id}-") as temp_dir:
        temp_root = Path(temp_dir)
        generated_step = temp_root / "generated_from_code.step"
        generated_step.write_bytes(sandbox_result.output_file_contents["model.step"])
        eval_dir = temp_root / "evaluation"
        evaluation = await _evaluate_step_pair_async(
            generated_step=generated_step,
            ground_truth_step=Path(str(payload["gt_step_path"])),
            output_dir=eval_dir,
            threshold=0.78,
            timeout_seconds=180,
        )

    result["score"] = (
        evaluation.get("scores", {}).get("final_score")
        if isinstance(evaluation.get("scores"), dict)
        else None
    )
    result["passed"] = evaluation.get("passed")
    result["difference_notes"] = evaluation.get("difference_notes", [])
    result["generated_stats"] = evaluation.get("generated_stats")
    result["ground_truth_stats"] = evaluation.get("ground_truth_stats")
    return result


async def _main_async(args: argparse.Namespace) -> int:
    repo_root = Path(__file__).resolve().parents[1]
    dataset_root = (
        args.dataset_root
        if args.dataset_root.is_absolute()
        else (repo_root / args.dataset_root)
    ).resolve()
    rows = _load_rows(dataset_root)

    requested_case_ids = [item.strip() for item in args.cases.split(",") if item.strip()]
    if requested_case_ids:
        case_ids = requested_case_ids
    else:
        case_ids = sorted(rows.keys())

    missing = [case_id for case_id in case_ids if case_id not in rows]
    if missing:
        raise KeyError(f"unknown case ids: {', '.join(missing)}")

    runner = DockerSandboxRunner(
        image=settings.sandbox_image,
        memory_limit=settings.sandbox_memory_limit,
        cpu_quota=settings.sandbox_cpu_quota,
        docker_socket=settings.sandbox_docker_socket,
    )
    reports: list[dict[str, Any]] = []
    for case_id in case_ids:
        reports.append(await _audit_case(case_id, rows[case_id], runner))

    text = json.dumps(
        {
            "dataset_root": str(dataset_root),
            "case_count": len(reports),
            "reports": reports,
        },
        ensure_ascii=True,
        indent=2,
        sort_keys=True,
    )
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


def main() -> int:
    args = _parse_args()
    return asyncio.run(_main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
