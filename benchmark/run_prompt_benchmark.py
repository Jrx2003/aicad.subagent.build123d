from __future__ import annotations

import argparse
from collections import Counter
import csv
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SRC_ROOT = _REPO_ROOT / "src"
for _path in (_SRC_ROOT, _REPO_ROOT):
    _path_str = str(_path)
    if _path_str not in sys.path:
        sys.path.insert(0, _path_str)

from common.config import settings
from sub_agent_runtime.diagnostics import build_runtime_validation_payload
from sub_agent_runtime.hallucination import normalize_hallucination_summary
from step_similarity_eval import evaluate_step_pair_sync


CSV_ENCODINGS = ("utf-8", "utf-8-sig", "gb18030", "gbk", "latin1")
PROMPT_FIELD_FALLBACK = ("pro_prompt_en", "geo_prompt_en", "prompt", "requirement")
_TIMESTAMP_RUN_ID_RE = re.compile(r"^\d{8}_\d{6}$")
_CASE_SET_MANIFEST_PATH = _REPO_ROOT / "benchmark" / "canary_case_sets.json"


@dataclass
class BenchmarkCase:
    case_id: str
    level: str
    prompt: str
    prompt_field: str
    csv_path: str
    gt_step_path: str
    canonical_reference: str | None = None
    reference_notes: str | None = None


def _parse_benchmark_run_id(value: str) -> str:
    run_id = str(value or "").strip()
    if not _TIMESTAMP_RUN_ID_RE.fullmatch(run_id):
        raise argparse.ArgumentTypeError(
            "benchmark --run-id must stay timestamp-only: YYYYMMDD_HHMMSS"
        )
    return run_id


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run iterative CAD loop on benchmark prompts and persist outputs under "
            "benchmark/runs/<timestamp>/<case_id>/"
        )
    )
    parser.add_argument(
        "--dataset-root",
        type=Path,
        default=Path("benchmark/sampled_10_per_L"),
        help="Root containing L1/L2/L3 CSV + steps.",
    )
    parser.add_argument(
        "--runs-root",
        type=Path,
        default=Path("benchmark/runs"),
        help="Run artifact root for benchmark outputs.",
    )
    parser.add_argument(
        "--run-id",
        type=_parse_benchmark_run_id,
        default=dt.datetime.now().strftime("%Y%m%d_%H%M%S"),
        help="Benchmark run id (timestamp by default).",
    )
    parser.add_argument(
        "--cases",
        type=str,
        default="",
        help="Comma-separated case ids, e.g. L1_20,L2_88.",
    )
    parser.add_argument(
        "--case-set",
        type=str,
        default="",
        help="Named case set from benchmark/canary_case_sets.json, e.g. canary.",
    )
    parser.add_argument(
        "--levels",
        type=str,
        default="",
        help="Comma-separated levels, e.g. L1,L2,L3.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Maximum selected case count after filters. 0 means no limit.",
    )
    parser.add_argument(
        "--prompt-field",
        type=str,
        default="",
        help="Preferred prompt field name in CSV row.",
    )
    parser.add_argument(
        "--reasoning-provider",
        type=str,
        default="",
        help="Optional override for LLM_REASONING_PROVIDER (e.g. kimi).",
    )
    parser.add_argument(
        "--reasoning-model",
        type=str,
        default="",
        help="Optional override for LLM_REASONING_MODEL (e.g. kimi-k2-thinking).",
    )
    parser.add_argument(
        "--max-rounds",
        type=int,
        default=0,
        help="Optional override for AICAD_PROBE_MAX_ROUNDS.",
    )
    parser.add_argument(
        "--sandbox-timeout",
        type=int,
        default=0,
        help="Optional override for AICAD_PROBE_SANDBOX_TIMEOUT.",
    )
    parser.add_argument(
        "--case-timeout",
        type=int,
        default=0,
        help=(
            "Hard timeout seconds for one benchmark case runner subprocess. "
            "0 means auto-derived timeout."
        ),
    )
    action_mode = parser.add_mutually_exclusive_group()
    action_mode.add_argument(
        "--one-action-per-round",
        dest="one_action_per_round",
        action="store_true",
        help="Dynamic loop: re-plan after each action (default).",
    )
    action_mode.add_argument(
        "--batch-actions",
        dest="one_action_per_round",
        action="store_false",
        help="Allow executing all planner-returned actions in same round.",
    )
    parser.set_defaults(one_action_per_round=True)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Resolve and print selected cases without running.",
    )
    parser.add_argument(
        "--skip-eval",
        action="store_true",
        help="Skip automatic STEP similarity evaluation.",
    )
    parser.add_argument(
        "--eval-threshold",
        type=float,
        default=0.78,
        help="Pass threshold for rendered similarity evaluation (0..1).",
    )
    parser.add_argument(
        "--eval-timeout",
        type=int,
        default=180,
        help="Timeout seconds for per-case evaluation job.",
    )
    return parser.parse_args()


def _resolve_runtime_mode(args: argparse.Namespace) -> str:
    _ = args
    return "v2"


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except Exception:
                continue
            if isinstance(payload, dict):
                records.append(payload)
    return records


def _slugify(value: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9]+", "_", value.strip().lower()).strip("_")
    return normalized or "unknown"


def _derive_practice_identity(
    *,
    args: argparse.Namespace,
    selected: list[BenchmarkCase],
) -> dict[str, Any]:
    runtime_mode = _resolve_runtime_mode(args)
    provider = (
        args.reasoning_provider
        or os.environ.get("LLM_REASONING_PROVIDER")
        or settings.llm_reasoning_provider
        or ""
    ).strip() or "default"
    model = (
        args.reasoning_model
        or os.environ.get("LLM_REASONING_MODEL")
        or settings.llm_reasoning_model
        or ""
    ).strip() or "default"
    levels = sorted({case.level for case in selected})
    case_scope = levels if levels else ["mixed"]
    action_mode = "one_action" if args.one_action_per_round else "batch_actions"
    practice_slug = "__".join(
        [
            _slugify(runtime_mode),
            _slugify("-".join(case_scope)),
            _slugify(provider),
            _slugify(model),
            _slugify(action_mode),
        ]
    )
    practice_label = (
        f"runtime={runtime_mode}, levels={','.join(case_scope)}, "
        f"provider={provider}, model={model}, action_mode={action_mode}"
    )
    return {
        "runtime_mode": runtime_mode,
        "provider": provider,
        "model": model,
        "levels": case_scope,
        "action_mode": action_mode,
        "practice_slug": practice_slug,
        "practice_label": practice_label,
    }


def _extract_runtime_summary(case_summary_payload: dict[str, Any]) -> dict[str, Any]:
    summary = case_summary_payload.get("summary")
    return summary if isinstance(summary, dict) else {}


def _extract_request_payload(case_summary_payload: dict[str, Any]) -> dict[str, Any]:
    request = case_summary_payload.get("request")
    return request if isinstance(request, dict) else {}


def _resolve_generated_step_path(
    *,
    case_dir: Path,
    case_summary_payload: dict[str, Any],
) -> Path | None:
    outputs_dir = case_dir / "outputs"
    runtime_summary = _extract_runtime_summary(case_summary_payload)
    if runtime_summary.get("step_file_exists") is False:
        return None
    candidate_names: list[str] = []
    step_file_name = runtime_summary.get("step_file_name")
    if isinstance(step_file_name, str) and step_file_name.strip():
        candidate_names.append(step_file_name.strip())
    candidate_names.extend(["final_model.step", "model.step"])
    for name in candidate_names:
        candidate = outputs_dir / name
        if candidate.exists():
            return candidate.resolve()
    step_candidates = sorted(outputs_dir.glob("*.step"))
    if step_candidates:
        return step_candidates[0].resolve()
    return None


def _resolve_render_path(case_dir: Path) -> Path | None:
    outputs_dir = case_dir / "outputs"
    preferred_names = [
        "render_view.png",
        "preview_iso.png",
        "preview_front.png",
        "preview_right.png",
        "preview_top.png",
    ]
    for name in preferred_names:
        candidate = outputs_dir / name
        if candidate.exists():
            return candidate.resolve()
    image_candidates = sorted(
        path for path in outputs_dir.iterdir()
        if path.is_file() and path.suffix.lower() in {".png", ".jpg", ".jpeg"}
    ) if outputs_dir.exists() else []
    if image_candidates:
        return image_candidates[0].resolve()
    return None


def _load_prompt_metrics_summary(case_dir: Path) -> dict[str, Any]:
    prompt_files = sorted((case_dir / "prompts").glob("round_*_request.json"))
    rounds: list[dict[str, Any]] = []
    raw_total = 0
    final_total = 0
    max_raw = 0
    max_final = 0
    diagnostics_rounds = 0
    for path in prompt_files:
        payload = _read_json(path)
        metrics = payload.get("prompt_metrics")
        if not isinstance(metrics, dict):
            continue
        round_no = payload.get("round")
        raw_chars = int(metrics.get("raw_chars", 0) or 0)
        final_chars = int(metrics.get("final_chars", 0) or 0)
        used_diagnostics = bool(metrics.get("used_diagnostics"))
        if used_diagnostics:
            diagnostics_rounds += 1
        raw_total += raw_chars
        final_total += final_chars
        max_raw = max(max_raw, raw_chars)
        max_final = max(max_final, final_chars)
        rounds.append(
            {
                "round": round_no if isinstance(round_no, int) else None,
                "raw_chars": raw_chars,
                "final_chars": final_chars,
                "used_diagnostics": used_diagnostics,
            }
        )
    return {
        "round_count": len(rounds),
        "raw_chars_total": raw_total,
        "final_chars_total": final_total,
        "max_raw_chars": max_raw,
        "max_final_chars": max_final,
        "diagnostics_round_count": diagnostics_rounds,
        "rounds": rounds,
    }


def _load_trace_summary(case_dir: Path) -> dict[str, Any]:
    trace_path = case_dir / "trace" / "events.jsonl"
    if not trace_path.exists():
        return {
            "available": False,
            "event_count": 0,
            "event_type_counts": {},
            "last_event_type": None,
            "timeline_tail": [],
        }
    event_type_counts: Counter[str] = Counter()
    timeline_tail: list[dict[str, Any]] = []
    last_event_type: str | None = None
    with trace_path.open("r", encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except Exception:
                continue
            event_type = event.get("event_type")
            if isinstance(event_type, str):
                event_type_counts[event_type] += 1
                last_event_type = event_type
            timeline_tail.append(
                {
                    "event_type": event_type,
                    "timestamp": event.get("timestamp"),
                    "payload": event.get("payload"),
                }
            )
            if len(timeline_tail) > 8:
                timeline_tail.pop(0)
    return {
        "available": True,
        "event_count": sum(event_type_counts.values()),
        "event_type_counts": dict(event_type_counts),
        "last_event_type": last_event_type,
        "timeline_tail": timeline_tail,
    }


def _summarize_tool_call(call: dict[str, Any]) -> dict[str, Any]:
    name = call.get("name")
    arguments = call.get("arguments")
    summary: dict[str, Any] = {"name": name}
    if not isinstance(arguments, dict):
        return summary
    if name == "apply_cad_action":
        action_type = arguments.get("action_type")
        action_params = arguments.get("action_params")
        summary["action_type"] = action_type
        if isinstance(action_params, dict):
            summary["action_param_keys"] = sorted(action_params.keys())
    elif name == "execute_build123d":
        code = arguments.get("code")
        if isinstance(code, str):
            preview = next((line.strip() for line in code.splitlines() if line.strip()), "")
            summary["code_chars"] = len(code)
            summary["code_preview"] = preview[:120]
    else:
        summary["argument_keys"] = sorted(arguments.keys())
    return summary


def _load_round_digest(case_dir: Path) -> dict[str, Any]:
    trace_path = case_dir / "trace" / "events.jsonl"
    conversation_path = case_dir / "trace" / "conversation.jsonl"
    tool_timeline_path = case_dir / "trace" / "tool_timeline.jsonl"
    stop_reason_path = case_dir / "trace" / "stop_reason.json"
    failure_bundle_path = case_dir / "trace" / "failure_bundle.json"
    if not trace_path.exists() and not conversation_path.exists():
        return {
            "available": False,
            "round_count": 0,
            "rounds": [],
            "final_validation": [],
            "run_finished": None,
            "stop_reason": {},
            "failure_bundle": {},
            "conversation_count": 0,
            "tool_event_count": 0,
            "domain_kernel_summary": {"available": False, "snapshot_count": 0},
        }

    rounds: dict[int, dict[str, Any]] = {}
    final_validation: list[dict[str, Any]] = []
    run_finished: dict[str, Any] | None = None
    conversation_records = _read_jsonl(conversation_path)
    tool_timeline_records = _read_jsonl(tool_timeline_path)
    stop_reason = _read_json(stop_reason_path)
    failure_bundle = _read_json(failure_bundle_path)

    def _round_entry(round_no: int) -> dict[str, Any]:
        entry = rounds.get(round_no)
        if entry is None:
            entry = {
                "round": round_no,
                "prompt_metrics": None,
                "turn_status": None,
                "decision_summary": None,
                "why_next": None,
                "finish_reason": None,
                "usage": None,
                "tool_calls": [],
                "tool_results": [],
                "conversation": [],
                "tool_timeline": [],
                "context_mutations": [],
                "compaction_boundary": None,
                "tool_batch_error": None,
                "round_completion": None,
                "validation": [],
                "feature_graph": None,
            }
            rounds[round_no] = entry
        return entry

    for event in _read_jsonl(trace_path):
            event_type = event.get("event_type")
            payload = event.get("payload")
            if not isinstance(payload, dict):
                payload = {}
            round_no = payload.get("round")
            entry = _round_entry(round_no) if isinstance(round_no, int) else None

            if event_type == "round_started" and entry is not None:
                entry["prompt_metrics"] = payload.get("prompt_metrics")
                entry["turn_status"] = payload.get("turn_status")
            elif event_type == "model_response_received" and entry is not None:
                entry["decision_summary"] = payload.get("decision_summary")
                entry["why_next"] = payload.get("why_next")
                entry["finish_reason"] = payload.get("finish_reason")
                entry["usage"] = payload.get("usage")
                entry["tool_call_names"] = payload.get("tool_call_names")
            elif event_type == "compaction_boundary" and entry is not None:
                entry["compaction_boundary"] = {
                    "raw_chars": payload.get("raw_chars"),
                    "final_chars": payload.get("final_chars"),
                    "was_compacted": payload.get("was_compacted"),
                    "reason": payload.get("reason"),
                    "kept_sections": payload.get("kept_sections"),
                    "summarized_sections": payload.get("summarized_sections"),
                    "post_compact_messages": payload.get("post_compact_messages"),
                }
            elif event_type == "tool_batch_started" and entry is not None:
                tool_calls = payload.get("tool_calls")
                if isinstance(tool_calls, list):
                    entry["tool_calls"] = [
                        _summarize_tool_call(call)
                        for call in tool_calls
                        if isinstance(call, dict)
                    ]
            elif event_type == "tool_result" and entry is not None:
                entry["tool_results"].append(
                    {
                        "tool_name": payload.get("tool_name"),
                        "category": payload.get("category"),
                        "success": payload.get("success"),
                        "error": payload.get("error"),
                        "artifact_files": payload.get("artifact_files"),
                        "payload_summary": payload.get("payload_summary"),
                    }
                )
            elif event_type == "tool_batch_error" and entry is not None:
                entry["tool_batch_error"] = payload.get("error")
            elif event_type in {"validation_requested", "validation_result"}:
                target = entry["validation"] if entry is not None else final_validation
                target.append(
                    {
                        "event_type": event_type,
                        "trigger": payload.get("trigger"),
                        "summary": payload.get("summary"),
                        "is_complete": payload.get("is_complete"),
                        "blockers": payload.get("blockers"),
                    }
                )
            elif event_type == "round_completed" and entry is not None:
                entry["round_completion"] = {
                    "requested_finish": payload.get("requested_finish"),
                    "write_tool_names": payload.get("write_tool_names"),
                    "inspection_only": payload.get("inspection_only"),
                    "previous_error": payload.get("previous_error"),
                }
            elif event_type == "run_finished":
                run_finished = payload

    for record in conversation_records:
        round_no = record.get("round")
        if not isinstance(round_no, int):
            continue
        entry = _round_entry(round_no)
        payload = record.get("payload")
        if (
            entry.get("why_next") is None
            and record.get("kind") == "decision"
            and isinstance(payload, dict)
            and isinstance(payload.get("why_next"), str)
        ):
            entry["why_next"] = payload.get("why_next")
        entry["conversation"].append(
            {
                "role": record.get("role"),
                "kind": record.get("kind"),
                "payload": payload,
            }
        )

    for record in tool_timeline_records:
        round_no = record.get("round")
        if not isinstance(round_no, int):
            continue
        entry = _round_entry(round_no)
        payload = {
            "tool_name": record.get("tool_name"),
            "phase": record.get("phase"),
            "category": record.get("category"),
            "success": record.get("success"),
            "detail": record.get("detail"),
        }
        entry["tool_timeline"].append(payload)
        if record.get("phase") == "context_mutation":
            entry["context_mutations"].append(payload)

    ordered_rounds = [rounds[key] for key in sorted(rounds)]
    feature_graph_index = _load_feature_graph_index(case_dir)
    for entry in ordered_rounds:
        round_no = entry.get("round")
        if not isinstance(round_no, int):
            continue
        feature_graph_entry = feature_graph_index.get(round_no)
        if feature_graph_entry is None:
            continue
        entry["domain_kernel"] = feature_graph_entry
    feature_graph_summary = _summarize_feature_graph_index(
        feature_graph_index=feature_graph_index,
        round_entries=ordered_rounds,
    )
    return {
        "available": True,
        "round_count": len(ordered_rounds),
        "rounds": ordered_rounds,
        "final_validation": final_validation,
        "run_finished": run_finished,
        "stop_reason": stop_reason,
        "failure_bundle": failure_bundle,
        "conversation_count": len(conversation_records),
        "tool_event_count": len(tool_timeline_records),
        "domain_kernel_summary": feature_graph_summary,
    }


def _write_round_digest(case_dir: Path, round_digest: dict[str, Any]) -> None:
    trace_dir = case_dir / "trace"
    trace_dir.mkdir(parents=True, exist_ok=True)
    _write_json(trace_dir / "round_digest.json", round_digest)

    lines = [
        f"# Round Digest: {case_dir.name}",
        "",
        "## Summary",
        "",
        f"- available: {round_digest.get('available')}",
        f"- round_count: {round_digest.get('round_count')}",
        f"- final_validation_events: {len(round_digest.get('final_validation') or [])}",
        f"- conversation_count: {round_digest.get('conversation_count')}",
        f"- tool_event_count: {round_digest.get('tool_event_count')}",
        f"- stop_reason: {round_digest.get('stop_reason')}",
        f"- run_finished: {round_digest.get('run_finished')}",
        "",
    ]
    feature_graph_summary = round_digest.get("domain_kernel_summary")
    if not isinstance(feature_graph_summary, dict):
        feature_graph_summary = round_digest.get("feature_graph_summary")
    if isinstance(feature_graph_summary, dict) and feature_graph_summary.get("available"):
        lines.extend(
            [
                "## Domain Kernel",
                "",
                f"- snapshot_count: {feature_graph_summary.get('snapshot_count')}",
                f"- kernel_query_count: {feature_graph_summary.get('graph_query_count')}",
                f"- kernel_state_patch_count: {feature_graph_summary.get('graph_patch_count')}",
                f"- kernel_binding_count: {feature_graph_summary.get('kernel_binding_count')}",
                f"- kernel_stale_binding_count: {feature_graph_summary.get('kernel_stale_binding_count')}",
                f"- kernel_binding_kinds: {feature_graph_summary.get('kernel_binding_kinds')}",
                f"- latest_binding_families: {feature_graph_summary.get('latest_binding_families')}",
                f"- latest_binding_blocker_ids: {feature_graph_summary.get('latest_binding_blocker_ids')}",
                f"- latest_binding_primary_feature_ids: {feature_graph_summary.get('latest_binding_primary_feature_ids')}",
                f"- latest_binding_evidence_source: {feature_graph_summary.get('latest_binding_evidence_source')}",
                f"- latest_binding_completeness_relevance: {feature_graph_summary.get('latest_binding_completeness_relevance')}",
                f"- latest_binding_severity: {feature_graph_summary.get('latest_binding_severity')}",
                f"- latest_binding_repair_lane: {feature_graph_summary.get('latest_binding_repair_lane')}",
                f"- latest_binding_geometry_summary: {feature_graph_summary.get('latest_binding_geometry_summary')}",
                f"- latest_binding_feature_anchor_summary: {feature_graph_summary.get('latest_binding_feature_anchor_summary')}",
                f"- final_revision: {feature_graph_summary.get('final_revision')}",
                f"- latest_sync_reason: {feature_graph_summary.get('latest_sync_reason')}",
                f"- blocked_node_ids: {feature_graph_summary.get('blocked_node_ids')}",
                f"- unsatisfied_feature_ids: {feature_graph_summary.get('unsatisfied_feature_ids')}",
                f"- latest_domain_kernel_path: {feature_graph_summary.get('latest_graph_path')}",
                "",
            ]
        )
    for round_entry in round_digest.get("rounds", []):
        if not isinstance(round_entry, dict):
            continue
        lines.append(f"## Round {round_entry.get('round')}")
        lines.append("")
        lines.append(f"- prompt_metrics: {round_entry.get('prompt_metrics')}")
        lines.append(f"- decision_summary: {round_entry.get('decision_summary')}")
        lines.append(f"- why_next: {round_entry.get('why_next')}")
        lines.append(f"- compaction_boundary: {round_entry.get('compaction_boundary')}")
        lines.append(f"- tool_calls: {round_entry.get('tool_calls')}")
        lines.append(f"- tool_results: {round_entry.get('tool_results')}")
        lines.append(f"- tool_timeline: {round_entry.get('tool_timeline')}")
        lines.append(f"- context_mutations: {round_entry.get('context_mutations')}")
        lines.append(f"- conversation: {round_entry.get('conversation')}")
        lines.append(f"- tool_batch_error: {round_entry.get('tool_batch_error')}")
        lines.append(f"- round_completion: {round_entry.get('round_completion')}")
        lines.append(f"- domain_kernel: {round_entry.get('domain_kernel')}")
        validation = round_entry.get("validation")
        if validation:
            lines.append(f"- validation: {validation}")
        lines.append("")
    final_validation = round_digest.get("final_validation")
    if final_validation:
        lines.extend(
            [
                "## Final Validation",
                "",
                f"- events: {final_validation}",
                "",
            ]
        )
    failure_bundle = round_digest.get("failure_bundle")
    if failure_bundle:
        lines.extend(
            [
                "## Failure Bundle",
                "",
                f"- bundle: {failure_bundle}",
                "",
            ]
        )
    (trace_dir / "round_digest.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _find_last_artifact_path(case_dir: Path, subdir: str) -> str | None:
    target_dir = case_dir / subdir
    if not target_dir.exists():
        return None
    files = sorted(path for path in target_dir.iterdir() if path.is_file())
    if not files:
        return None
    return str(files[-1].resolve())


def _extract_round_from_feature_graph_path(path: Path) -> int | None:
    match = re.search(r"(?:domain_kernel|feature_graph)_round_(\d+)\.json$", path.name)
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def _load_feature_graph_index(case_dir: Path) -> dict[int, dict[str, Any]]:
    trace_dir = case_dir / "trace"
    if not trace_dir.exists():
        return {}
    index: dict[int, dict[str, Any]] = {}
    paths = list(sorted(trace_dir.glob("domain_kernel_round_*.json")))
    if not paths:
        paths = list(sorted(trace_dir.glob("feature_graph_round_*.json")))
    for path in paths:
        round_no = _extract_round_from_feature_graph_path(path)
        if round_no is None:
            continue
        payload = _read_json(path)
        if not isinstance(payload, dict):
            payload = {}
        index[round_no] = {
            "round": round_no,
            "path": str(path.resolve()),
            "revision": payload.get("revision"),
            "latest_sync_reason": payload.get("latest_sync_reason"),
            "active_node_ids": payload.get("active_node_ids"),
            "blocked_node_ids": payload.get("blocked_node_ids"),
            "completed_node_ids": payload.get("completed_node_ids"),
            "unsatisfied_feature_ids": payload.get("unsatisfied_feature_ids"),
            "requirement_tags": payload.get("requirement_tags"),
            "evidence_summary": payload.get("evidence_summary"),
            "kernel_binding_count": payload.get("kernel_binding_count"),
            "kernel_stale_binding_count": payload.get("kernel_stale_binding_count"),
            "kernel_binding_kinds": payload.get("kernel_binding_kinds"),
            "latest_binding_families": payload.get("latest_binding_families"),
            "latest_binding_blocker_ids": payload.get("latest_binding_blocker_ids"),
            "latest_binding_primary_feature_ids": payload.get(
                "latest_binding_primary_feature_ids"
            ),
            "latest_binding_evidence_source": payload.get("latest_binding_evidence_source"),
            "latest_binding_completeness_relevance": payload.get(
                "latest_binding_completeness_relevance"
            ),
            "latest_binding_severity": payload.get("latest_binding_severity"),
            "latest_binding_repair_lane": payload.get("latest_binding_repair_lane"),
            "latest_binding_geometry_summary": payload.get(
                "latest_binding_geometry_summary"
            ),
            "latest_binding_feature_anchor_summary": payload.get(
                "latest_binding_feature_anchor_summary"
            ),
            "feature_instance_count": payload.get("feature_instance_count"),
            "active_feature_instance_ids": payload.get("active_feature_instance_ids"),
            "active_feature_instances": payload.get("active_feature_instances"),
            "kernel_patch_count": payload.get("kernel_patch_count"),
            "kernel_patch_kinds": payload.get("kernel_patch_kinds"),
            "repair_packet_count": payload.get("repair_packet_count"),
            "repair_packet_kinds": payload.get("repair_packet_kinds"),
            "latest_patch_repair_mode": payload.get("latest_patch_repair_mode"),
            "latest_patch_feature_instance_ids": payload.get(
                "latest_patch_feature_instance_ids"
            ),
            "latest_patch_affected_hosts": payload.get("latest_patch_affected_hosts"),
            "latest_patch_anchor_keys": payload.get("latest_patch_anchor_keys"),
            "latest_patch_parameter_keys": payload.get("latest_patch_parameter_keys"),
            "latest_patch_repair_intent": payload.get("latest_patch_repair_intent"),
            "latest_repair_packet_family_id": payload.get("latest_repair_packet_family_id"),
            "latest_repair_packet_feature_instance_id": payload.get(
                "latest_repair_packet_feature_instance_id"
            ),
            "latest_repair_packet_repair_mode": payload.get(
                "latest_repair_packet_repair_mode"
            ),
            "latest_repair_packet_recipe_id": payload.get("latest_repair_packet_recipe_id"),
            "latest_repair_packet_recipe_summary": payload.get(
                "latest_repair_packet_recipe_summary"
            ),
        }
    return index


def _count_tool_mentions(round_entries: list[dict[str, Any]], tool_name: str) -> int:
    tool_call_count = 0
    tool_result_count = 0
    for entry in round_entries:
        if not isinstance(entry, dict):
            continue
        tool_calls = entry.get("tool_calls")
        if isinstance(tool_calls, list):
            tool_call_count += sum(
                1
                for item in tool_calls
                if isinstance(item, dict) and item.get("name") == tool_name
            )
        tool_results = entry.get("tool_results")
        if isinstance(tool_results, list):
            tool_result_count += sum(
                1
                for item in tool_results
                if isinstance(item, dict) and item.get("tool_name") == tool_name
            )
    return tool_call_count or tool_result_count


def _feature_graph_patch_entries(
    feature_graph_index: dict[int, dict[str, Any]],
) -> list[dict[str, Any]]:
    ordered_entries = [feature_graph_index[round_no] for round_no in sorted(feature_graph_index)]
    return [
        entry
        for entry in ordered_entries
        if int(entry.get("kernel_patch_count", 0) or 0) > 0
        or bool(entry.get("latest_patch_repair_mode"))
        or bool(entry.get("latest_patch_feature_instance_ids"))
    ]


def _summarize_feature_graph_index(
    *,
    feature_graph_index: dict[int, dict[str, Any]],
    round_entries: list[dict[str, Any]],
) -> dict[str, Any]:
    if not feature_graph_index:
        return {"available": False, "snapshot_count": 0}
    ordered_rounds = sorted(feature_graph_index)
    initial = feature_graph_index[ordered_rounds[0]]
    latest = feature_graph_index[ordered_rounds[-1]]
    ordered_entries = [feature_graph_index[round_no] for round_no in ordered_rounds]
    patch_entries = _feature_graph_patch_entries(feature_graph_index)
    latest_patch_entry = patch_entries[-1] if patch_entries else latest
    first_patch_entry = patch_entries[0] if patch_entries else None
    feature_instance_peak = max(
        int(entry.get("feature_instance_count", 0) or 0) for entry in ordered_entries
    )
    kernel_patch_peak = max(
        int(entry.get("kernel_patch_count", 0) or 0) for entry in ordered_entries
    )
    repair_packet_peak = max(
        int(entry.get("repair_packet_count", 0) or 0) for entry in ordered_entries
    )
    kernel_patch_rounds = [
        int(entry.get("round"))
        for entry in patch_entries
        if isinstance(entry.get("round"), int)
    ]
    kernel_patch_kind_counts = Counter()
    repair_mode_counts = Counter()
    repair_packet_kind_counts = Counter()
    for entry in patch_entries:
        for patch_kind in entry.get("kernel_patch_kinds") or []:
            if isinstance(patch_kind, str) and patch_kind.strip():
                kernel_patch_kind_counts[patch_kind.strip()] += 1
        repair_mode = str(entry.get("latest_patch_repair_mode") or "").strip()
        if repair_mode:
            repair_mode_counts[repair_mode] += 1
    for entry in ordered_entries:
        for packet_kind in entry.get("repair_packet_kinds") or []:
            if isinstance(packet_kind, str) and packet_kind.strip():
                repair_packet_kind_counts[packet_kind.strip()] += 1
    return {
        "available": True,
        "snapshot_count": len(feature_graph_index),
        "initial_graph_path": initial.get("path"),
        "latest_graph_path": latest.get("path"),
        "initial_revision": initial.get("revision"),
        "final_revision": latest.get("revision"),
        "latest_sync_reason": latest.get("latest_sync_reason"),
        "active_node_ids": latest.get("active_node_ids") or [],
        "blocked_node_ids": latest.get("blocked_node_ids") or [],
        "completed_node_ids": latest.get("completed_node_ids") or [],
        "unsatisfied_feature_ids": latest.get("unsatisfied_feature_ids") or [],
        "requirement_tags": latest.get("requirement_tags") or [],
        "evidence_summary": latest.get("evidence_summary") or [],
        "kernel_binding_count": latest.get("kernel_binding_count"),
        "kernel_stale_binding_count": latest.get("kernel_stale_binding_count"),
        "kernel_binding_kinds": latest.get("kernel_binding_kinds") or [],
        "latest_binding_families": latest.get("latest_binding_families") or [],
        "latest_binding_blocker_ids": latest.get("latest_binding_blocker_ids") or [],
        "latest_binding_primary_feature_ids": (
            latest.get("latest_binding_primary_feature_ids") or []
        ),
        "latest_binding_evidence_source": latest.get("latest_binding_evidence_source"),
        "latest_binding_completeness_relevance": latest.get(
            "latest_binding_completeness_relevance"
        ),
        "latest_binding_severity": latest.get("latest_binding_severity"),
        "latest_binding_repair_lane": latest.get("latest_binding_repair_lane"),
        "latest_binding_geometry_summary": (
            latest.get("latest_binding_geometry_summary") or {}
        ),
        "latest_binding_feature_anchor_summary": (
            latest.get("latest_binding_feature_anchor_summary") or {}
        ),
        "graph_query_count": _count_tool_mentions(round_entries, "query_kernel_state"),
        "graph_patch_count": _count_tool_mentions(round_entries, "patch_domain_kernel"),
        "feature_instance_count": feature_instance_peak,
        "final_feature_instance_count": latest.get("feature_instance_count"),
        "active_feature_instance_ids": latest.get("active_feature_instance_ids") or [],
        "active_feature_instances": latest.get("active_feature_instances") or [],
        "kernel_patch_count": kernel_patch_peak,
        "final_kernel_patch_count": latest.get("kernel_patch_count"),
        "kernel_patch_kinds": sorted(kernel_patch_kind_counts),
        "repair_packet_count": repair_packet_peak,
        "final_repair_packet_count": latest.get("repair_packet_count"),
        "repair_packet_kinds": sorted(repair_packet_kind_counts),
        "kernel_patch_rounds": kernel_patch_rounds,
        "repair_mode_counts": dict(repair_mode_counts),
        "latest_patch_repair_mode": latest_patch_entry.get("latest_patch_repair_mode"),
        "latest_patch_feature_instance_ids": latest_patch_entry.get("latest_patch_feature_instance_ids") or [],
        "latest_patch_affected_hosts": latest_patch_entry.get("latest_patch_affected_hosts") or [],
        "latest_patch_anchor_keys": latest_patch_entry.get("latest_patch_anchor_keys") or [],
        "latest_patch_parameter_keys": latest_patch_entry.get("latest_patch_parameter_keys") or [],
        "latest_patch_repair_intent": latest_patch_entry.get("latest_patch_repair_intent"),
        "latest_repair_packet_family_id": latest.get("latest_repair_packet_family_id"),
        "latest_repair_packet_feature_instance_id": latest.get(
            "latest_repair_packet_feature_instance_id"
        ),
        "latest_repair_packet_repair_mode": latest.get(
            "latest_repair_packet_repair_mode"
        ),
        "latest_repair_packet_recipe_id": latest.get("latest_repair_packet_recipe_id"),
        "latest_repair_packet_recipe_summary": latest.get(
            "latest_repair_packet_recipe_summary"
        ),
        "first_patch_round": first_patch_entry.get("round") if first_patch_entry else None,
        "first_patch_feature_instance_ids": (
            first_patch_entry.get("latest_patch_feature_instance_ids") or []
            if first_patch_entry
            else []
        ),
    }


def _load_latest_validation_payload(case_dir: Path) -> dict[str, Any]:
    queries_dir = case_dir / "queries"
    if not queries_dir.exists():
        return {}
    candidates = sorted(queries_dir.glob("*validate_requirement*.json"))
    if not candidates:
        return {}
    return _read_json(candidates[-1])


def _summarize_validation_lanes(payload: dict[str, Any]) -> dict[str, Any]:
    runtime_payload = build_runtime_validation_payload(payload)
    core_blockers = [
        str(item).strip()
        for item in (runtime_payload.get("blockers") or [])
        if isinstance(item, str) and str(item).strip()
    ]
    diagnostic_blockers = [
        str(item).strip()
        for item in (runtime_payload.get("diagnostic_blockers") or [])
        if isinstance(item, str) and str(item).strip()
    ]
    blocker_taxonomy: list[dict[str, Any]] = []
    for key in ("blocker_taxonomy", "diagnostic_blocker_taxonomy"):
        raw_taxonomy = runtime_payload.get(key)
        if not isinstance(raw_taxonomy, list):
            continue
        for item in raw_taxonomy:
            if not isinstance(item, dict):
                continue
            blocker_id = str(item.get("blocker_id") or "").strip()
            if not blocker_id:
                continue
            blocker_taxonomy.append(
                {
                    "blocker_id": blocker_id,
                    "family_ids": [
                        str(family_id).strip()
                        for family_id in (item.get("family_ids") or [])
                        if isinstance(family_id, str) and str(family_id).strip()
                    ],
                    "primary_feature_id": str(
                        item.get("primary_feature_id") or "feature.core_geometry"
                    ).strip()
                    or "feature.core_geometry",
                    "recommended_repair_lane": str(
                        item.get("recommended_repair_lane") or "code_repair"
                    ).strip()
                    or "code_repair",
                    "completeness_relevance": str(
                        item.get("completeness_relevance") or "core"
                    ).strip()
                    or "core",
                    "severity": str(item.get("severity") or "").strip()
                    or (
                        "diagnostic"
                        if str(item.get("completeness_relevance") or "").strip().lower()
                        == "diagnostic"
                        else "blocking"
                    ),
                }
            )
    blocker_taxonomy_counts = Counter(
        family_id
        for item in blocker_taxonomy
        for family_id in item.get("family_ids", [])
        if isinstance(family_id, str) and family_id.strip()
    )
    core_blocker_taxonomy_counts = Counter(
        family_id
        for item in blocker_taxonomy
        if item.get("completeness_relevance") == "core"
        for family_id in item.get("family_ids", [])
        if isinstance(family_id, str) and family_id.strip()
    )
    diagnostic_blocker_taxonomy_counts = Counter(
        family_id
        for item in blocker_taxonomy
        if item.get("completeness_relevance") == "diagnostic"
        for family_id in item.get("family_ids", [])
        if isinstance(family_id, str) and family_id.strip()
    )
    return {
        "core_blockers": core_blockers,
        "diagnostic_blockers": diagnostic_blockers,
        "core_blocker_count": len(core_blockers),
        "diagnostic_blocker_count": len(diagnostic_blockers),
        "blocker_taxonomy": blocker_taxonomy,
        "blocker_taxonomy_counts": dict(blocker_taxonomy_counts),
        "core_blocker_taxonomy_counts": dict(core_blocker_taxonomy_counts),
        "diagnostic_blocker_taxonomy_counts": dict(diagnostic_blocker_taxonomy_counts),
        "raw_summary": runtime_payload.get("summary"),
        "raw_is_complete": runtime_payload.get("is_complete"),
    }


def _diagnose_case(
    *,
    case_dir: Path,
    return_code: int,
    timed_out: bool,
    case_summary_payload: dict[str, Any],
    evaluation_payload: dict[str, Any],
    generated_step_path: Path | None,
    prompt_metrics: dict[str, Any],
    trace_summary: dict[str, Any],
) -> dict[str, Any]:
    runtime_summary = _extract_runtime_summary(case_summary_payload)
    llm_error = runtime_summary.get("llm_error")
    last_error = runtime_summary.get("last_error")
    planner_rounds = int(runtime_summary.get("planner_rounds", 0) or 0)
    inspection_only_rounds = int(runtime_summary.get("inspection_only_rounds", 0) or 0)
    validation_complete = bool(runtime_summary.get("validation_complete"))
    converged = bool(runtime_summary.get("converged"))
    evaluation_passed = evaluation_payload.get("passed") is True
    evaluation_failed = evaluation_payload.get("passed") is False
    evaluation_summary = str(evaluation_payload.get("summary") or "").strip()
    terminal_validation_gap = (
        last_error == "execute_build123d_terminal_without_session_validation"
    )
    validator_evaluator_disagreement = evaluation_passed and not validation_complete
    round_entries = trace_summary.get("rounds") if isinstance(trace_summary, dict) else None
    if not isinstance(round_entries, list):
        round_entries = []
    stop_reason = trace_summary.get("stop_reason") if isinstance(trace_summary, dict) else {}
    if not isinstance(stop_reason, dict):
        stop_reason = {}
    failure_bundle = trace_summary.get("failure_bundle") if isinstance(trace_summary, dict) else {}
    if not isinstance(failure_bundle, dict):
        failure_bundle = {}
    feature_graph_summary = (
        trace_summary.get("domain_kernel_summary")
        if isinstance(trace_summary.get("domain_kernel_summary"), dict)
        else trace_summary.get("feature_graph_summary")
        if isinstance(trace_summary.get("feature_graph_summary"), dict)
        else {}
    )
    runtime_validation_view = (
        failure_bundle.get("recent_validation")
        if isinstance(failure_bundle.get("recent_validation"), dict)
        else {}
    )
    validation_lanes = _summarize_validation_lanes(_load_latest_validation_payload(case_dir))
    core_blockers = validation_lanes.get("core_blockers")
    if not isinstance(core_blockers, list):
        core_blockers = []
    blocked_node_ids = feature_graph_summary.get("blocked_node_ids")
    if not isinstance(blocked_node_ids, list):
        blocked_node_ids = []
    graph_validation_mismatch = bool(feature_graph_summary.get("available")) and (
        bool(core_blockers) != bool(blocked_node_ids)
    )
    diagnostic_only_validation_gap = bool(
        validator_evaluator_disagreement
        and not validation_lanes.get("core_blockers")
        and validation_lanes.get("diagnostic_blockers")
    )

    category = "ok"
    diagnosis = "run completed without an obvious aggregation-level failure"
    status = "PASS"
    end_to_end_status = "pass"

    if timed_out:
        category = "runner_timeout"
        diagnosis = "Case runner timed out before finishing."
        status = "TIMEOUT"
        end_to_end_status = "timeout"
    elif return_code != 0:
        category = "runner_exception"
        diagnosis = "Case runner exited non-zero; inspect benchmark_runner.stderr.log."
        status = "RUN_ERROR"
        end_to_end_status = "runtime_failure"
    elif generated_step_path is None:
        category = "missing_step_artifact"
        diagnosis = "No STEP artifact was found under outputs/."
        status = "NO_STEP"
        end_to_end_status = "runtime_failure"
    elif isinstance(llm_error, str) and llm_error.strip():
        category = "llm_error"
        diagnosis = llm_error.strip()
        status = "LLM_ERROR"
        end_to_end_status = "runtime_failure"
    elif evaluation_payload.get("status") == "error":
        category = "evaluation_error"
        diagnosis = str(evaluation_payload.get("summary") or "STEP evaluation failed")
        status = "EVAL_ERROR"
        end_to_end_status = "evaluation_error"
    elif validator_evaluator_disagreement:
        category = "validator_evaluator_disagreement"
        if terminal_validation_gap:
            diagnosis = (
                "Generated STEP passed evaluator, but runtime stopped on the "
                "terminal execute_build123d path without validator confirmation."
            )
        elif diagnostic_only_validation_gap:
            diagnosis = (
                "Generated STEP passed evaluator, and the remaining validator gap is in "
                "diagnostic/provenance checks rather than loop-safe core completion checks."
            )
        else:
            diagnosis = (
                "Generated STEP passed evaluator, but runtime validation remained incomplete."
            )
        status = "VALIDATOR_MISMATCH"
        end_to_end_status = "validator_disagreement"
    elif evaluation_failed:
        category = "geometry_mismatch"
        diagnosis = evaluation_summary or "Generated STEP failed similarity evaluation"
        status = "EVAL_FAIL"
        end_to_end_status = "geometry_mismatch"
    elif isinstance(last_error, str) and last_error.strip():
        category = "runtime_error"
        diagnosis = last_error.strip()
        status = "RUNTIME_ERROR"
        end_to_end_status = "runtime_failure"
    elif not converged or not validation_complete:
        if inspection_only_rounds >= max(2, planner_rounds // 2):
            category = "excessive_reinspection"
            diagnosis = "Run consumed many inspection-only rounds without reaching completion."
        else:
            category = "incomplete_run"
            diagnosis = "Run ended with a STEP artifact but without convergence/validation completion."
        status = "INCOMPLETE"
        end_to_end_status = "incomplete"

    diagnosis_supports_context_pressure_note = category not in {
        "runner_timeout",
        "runner_exception",
        "runtime_error",
        "llm_error",
    }
    diagnosis_supports_kernel_query_note = category not in {
        "runner_exception",
        "runtime_error",
    }

    if (
        diagnosis_supports_context_pressure_note
        and prompt_metrics.get("max_final_chars", 0)
        and prompt_metrics.get("max_final_chars", 0) > 20000
    ):
        diagnosis = f"{diagnosis} Prompt context grew large during the run."
    if trace_summary.get("available") and trace_summary.get("event_count", 0) <= 3:
        diagnosis = f"{diagnosis} Trace is too sparse to explain the failure timeline."
    if (
        diagnosis_supports_kernel_query_note
        and isinstance(feature_graph_summary, dict)
        and feature_graph_summary.get("available")
        and not int(feature_graph_summary.get("graph_query_count") or 0)
        and (
            feature_graph_summary.get("blocked_node_ids")
            or feature_graph_summary.get("unsatisfied_feature_ids")
        )
    ):
        diagnosis = (
            f"{diagnosis} Domain-kernel snapshots existed, but the loop never issued "
            "query_kernel_state before stopping."
        )
    if diagnosis_supports_kernel_query_note and graph_validation_mismatch:
        diagnosis = (
            f"{diagnosis} Current validation blockers and domain-kernel blocked nodes diverged; "
            "inspect freshness/binding sync rather than treating this as a pure modeling miss."
        )

    recommended_fix_layer = _recommended_fix_layer(category, validation_lanes)
    if graph_validation_mismatch:
        recommended_fix_layer = "kernel_binding_gap"
    first_bad_turn = _infer_first_bad_turn(round_entries)
    last_good_write = failure_bundle.get("last_good_write")
    repeated_useless_reads = _infer_repeated_useless_reads(round_entries)
    failure_cluster = str(runtime_summary.get("failure_cluster") or "").strip() or None
    first_bad_feature_instance = None
    latest_patch_feature_instance_ids = feature_graph_summary.get("latest_patch_feature_instance_ids")
    if isinstance(latest_patch_feature_instance_ids, list) and latest_patch_feature_instance_ids:
        first_bad_feature_instance = latest_patch_feature_instance_ids[0]
    first_patch_feature_instance_ids = feature_graph_summary.get(
        "first_patch_feature_instance_ids"
    )
    if (
        first_bad_feature_instance is None
        and isinstance(first_patch_feature_instance_ids, list)
        and first_patch_feature_instance_ids
    ):
        first_bad_feature_instance = first_patch_feature_instance_ids[0]
    repair_mode_counts = Counter(feature_graph_summary.get("repair_mode_counts") or {})
    if not repair_mode_counts:
        latest_patch_repair_mode = str(
            feature_graph_summary.get("latest_patch_repair_mode") or ""
        ).strip()
        if latest_patch_repair_mode:
            repair_mode_counts[latest_patch_repair_mode] += 1

    return {
        "status": status,
        "end_to_end_status": end_to_end_status,
        "evaluation_passed": evaluation_passed,
        "validation_complete": validation_complete,
        "validator_evaluator_disagreement": validator_evaluator_disagreement,
        "failure_category": category,
        "likely_root_cause": diagnosis,
        "recommended_fix_layer": recommended_fix_layer,
        "graph_validation_mismatch": graph_validation_mismatch,
        "first_bad_turn": first_bad_turn,
        "last_good_write": last_good_write,
        "repeated_useless_reads": repeated_useless_reads,
        "first_bad_feature_instance": first_bad_feature_instance,
        "repair_mode_counts": dict(repair_mode_counts),
        "validation_lanes": validation_lanes,
        "runtime_validation_view": runtime_validation_view,
        "stop_reason": stop_reason,
        "failure_cluster": failure_cluster,
        "domain_kernel_summary": feature_graph_summary,
        "key_artifacts": {
            "summary_json": str((case_dir / "summary.json").resolve()),
            "trace_events": str((case_dir / "trace" / "events.jsonl").resolve()),
            "conversation_trace": str((case_dir / "trace" / "conversation.jsonl").resolve()),
            "tool_timeline": str((case_dir / "trace" / "tool_timeline.jsonl").resolve()),
            "round_digest": str((case_dir / "trace" / "round_digest.md").resolve()),
            "stop_reason": str((case_dir / "trace" / "stop_reason.json").resolve()),
            "failure_bundle": str((case_dir / "trace" / "failure_bundle.json").resolve()),
            "initial_domain_kernel": feature_graph_summary.get("initial_graph_path"),
            "latest_domain_kernel": feature_graph_summary.get("latest_graph_path"),
            "last_prompt_request": _find_last_artifact_path(case_dir, "prompts"),
            "last_plan_response": _find_last_artifact_path(case_dir, "plans"),
            "last_action_result": _find_last_artifact_path(case_dir, "actions"),
            "last_query_result": _find_last_artifact_path(case_dir, "queries"),
            "runner_stderr_log": str((case_dir / "benchmark_runner.stderr.log").resolve()),
        },
    }


def _write_case_analysis(case_dir: Path, case_record: dict[str, Any]) -> None:
    analysis = case_record.get("analysis")
    if not isinstance(analysis, dict):
        return
    _write_json(case_dir / "benchmark_analysis.json", analysis)
    runtime_summary = case_record.get("runtime_summary")
    if not isinstance(runtime_summary, dict):
        runtime_summary = {}
    evaluation = case_record.get("evaluation")
    if not isinstance(evaluation, dict):
        evaluation = {}
    round_digest = case_record.get("round_digest")
    if not isinstance(round_digest, dict):
        round_digest = {}
    baseline_metrics = (
        case_record.get("baseline_metrics")
        if isinstance(case_record.get("baseline_metrics"), dict)
        else _build_case_baseline_metrics(case_record)
    )
    validation_lanes = analysis.get("validation_lanes")
    if not isinstance(validation_lanes, dict):
        validation_lanes = {}
    runtime_validation_view = analysis.get("runtime_validation_view")
    if not isinstance(runtime_validation_view, dict):
        runtime_validation_view = {}
    key_artifacts = analysis.get("key_artifacts")
    if not isinstance(key_artifacts, dict):
        key_artifacts = {}
    feature_graph_summary = analysis.get("domain_kernel_summary")
    if not isinstance(feature_graph_summary, dict):
        feature_graph_summary = analysis.get("feature_graph_summary")
    if not isinstance(feature_graph_summary, dict):
        feature_graph_summary = {}
    lines = [
        f"# Benchmark Analysis: {case_record.get('case_id', '')}",
        "",
        "## Status",
        "",
        f"- status: {analysis.get('status')}",
        f"- end_to_end_status: {analysis.get('end_to_end_status')}",
        f"- evaluation_passed: {analysis.get('evaluation_passed')}",
        f"- validation_complete: {analysis.get('validation_complete')}",
        f"- validator_evaluator_disagreement: {analysis.get('validator_evaluator_disagreement')}",
        f"- failure_category: {analysis.get('failure_category')}",
        f"- likely_root_cause: {analysis.get('likely_root_cause')}",
        f"- recommended_fix_layer: {analysis.get('recommended_fix_layer')}",
        f"- graph_validation_mismatch: {analysis.get('graph_validation_mismatch')}",
        f"- first_bad_turn: {analysis.get('first_bad_turn')}",
        f"- last_good_write: {analysis.get('last_good_write')}",
        f"- repeated_useless_reads: {analysis.get('repeated_useless_reads')}",
        f"- first_bad_feature_instance: {analysis.get('first_bad_feature_instance')}",
        f"- repair_mode_counts: {analysis.get('repair_mode_counts')}",
        f"- stop_reason: {analysis.get('stop_reason')}",
        "",
        "## Validation Lanes",
        "",
        f"- core_blockers: {validation_lanes.get('core_blockers')}",
        f"- diagnostic_blockers: {validation_lanes.get('diagnostic_blockers')}",
        f"- core_blocker_count: {validation_lanes.get('core_blocker_count')}",
        f"- diagnostic_blocker_count: {validation_lanes.get('diagnostic_blocker_count')}",
        f"- blocker_taxonomy_counts: {validation_lanes.get('blocker_taxonomy_counts')}",
        f"- core_blocker_taxonomy_counts: {validation_lanes.get('core_blocker_taxonomy_counts')}",
        f"- diagnostic_blocker_taxonomy_counts: {validation_lanes.get('diagnostic_blocker_taxonomy_counts')}",
        "",
        "## Runtime Validation View",
        "",
        f"- summary: {runtime_validation_view.get('summary')}",
        f"- is_complete: {runtime_validation_view.get('is_complete')}",
        f"- blockers: {runtime_validation_view.get('blockers')}",
        f"- failed_checks: {runtime_validation_view.get('failed_checks')}",
        "",
        "## Baseline Metrics",
        "",
        f"- first_solid_success: {baseline_metrics.get('first_solid_success')}",
        f"- first_solid_round: {baseline_metrics.get('first_solid_round')}",
        f"- first_solid_tool: {baseline_metrics.get('first_solid_tool')}",
        f"- requirement_complete: {baseline_metrics.get('requirement_complete')}",
        f"- runtime_rewrite_turn_count: {baseline_metrics.get('rewrite_turn_count')}",
        f"- repair_turns_after_first_write: {baseline_metrics.get('repair_turns_after_first_write')}",
        f"- stale_evidence_incidents: {baseline_metrics.get('stale_evidence_incidents')}",
        f"- tokens: {baseline_metrics.get('tokens')}",
        f"- family_repair_packet_available: {baseline_metrics.get('family_repair_packet_available')}",
        f"- family_repair_packet_hit: {baseline_metrics.get('family_repair_packet_hit')}",
        f"- latest_repair_packet_family_id: {baseline_metrics.get('latest_repair_packet_family_id')}",
        "",
        "## Runtime",
        "",
        f"- planner_rounds: {runtime_summary.get('planner_rounds')}",
        f"- executed_action_count: {runtime_summary.get('executed_action_count')}",
        f"- executed_action_types: {runtime_summary.get('executed_action_types')}",
        f"- inspection_only_rounds: {runtime_summary.get('inspection_only_rounds')}",
        f"- validation_complete: {runtime_summary.get('validation_complete')}",
        f"- primary_write_mode: {runtime_summary.get('primary_write_mode')}",
        f"- first_write_tool: {runtime_summary.get('first_write_tool')}",
        f"- structured_bootstrap_rounds: {runtime_summary.get('structured_bootstrap_rounds')}",
        f"- stale_probe_carry_count: {runtime_summary.get('stale_probe_carry_count')}",
        f"- evidence_conflict_count: {runtime_summary.get('evidence_conflict_count')}",
        f"- forced_policy_chain: {runtime_summary.get('forced_policy_chain')}",
        f"- feature_probe_count: {runtime_summary.get('feature_probe_count')}",
        f"- probe_code_count: {runtime_summary.get('probe_code_count')}",
        f"- failure_cluster: {analysis.get('failure_cluster')}",
        f"- last_error: {runtime_summary.get('last_error')}",
        f"- runtime_mode_effective: {runtime_summary.get('runtime_mode_effective')}",
        "",
        "## Evaluation",
        "",
        f"- status: {evaluation.get('status')}",
        f"- passed: {evaluation.get('passed')}",
        f"- score: {evaluation.get('score')}",
        f"- summary: {evaluation.get('summary')}",
        "",
        "## Round Digest",
        "",
        f"- round_count: {round_digest.get('round_count')}",
        f"- final_validation: {round_digest.get('final_validation')}",
        f"- conversation_count: {round_digest.get('conversation_count')}",
        f"- tool_event_count: {round_digest.get('tool_event_count')}",
        f"- stop_reason: {round_digest.get('stop_reason')}",
        "",
        "## Domain Kernel",
        "",
        f"- available: {feature_graph_summary.get('available')}",
        f"- snapshot_count: {feature_graph_summary.get('snapshot_count')}",
        f"- kernel_query_count: {feature_graph_summary.get('graph_query_count')}",
        f"- kernel_state_patch_count: {feature_graph_summary.get('graph_patch_count')}",
        f"- initial_revision: {feature_graph_summary.get('initial_revision')}",
        f"- final_revision: {feature_graph_summary.get('final_revision')}",
        f"- latest_sync_reason: {feature_graph_summary.get('latest_sync_reason')}",
        f"- active_node_ids: {feature_graph_summary.get('active_node_ids')}",
        f"- blocked_node_ids: {feature_graph_summary.get('blocked_node_ids')}",
        f"- completed_node_ids: {feature_graph_summary.get('completed_node_ids')}",
        f"- unsatisfied_feature_ids: {feature_graph_summary.get('unsatisfied_feature_ids')}",
        f"- requirement_tags: {feature_graph_summary.get('requirement_tags')}",
        f"- evidence_summary: {feature_graph_summary.get('evidence_summary')}",
        f"- kernel_binding_count: {feature_graph_summary.get('kernel_binding_count')}",
        f"- kernel_binding_kinds: {feature_graph_summary.get('kernel_binding_kinds')}",
        f"- feature_instance_count: {feature_graph_summary.get('feature_instance_count')}",
        f"- final_feature_instance_count: {feature_graph_summary.get('final_feature_instance_count')}",
        f"- active_feature_instance_ids: {feature_graph_summary.get('active_feature_instance_ids')}",
        f"- kernel_patch_count: {feature_graph_summary.get('kernel_patch_count')}",
        f"- final_kernel_patch_count: {feature_graph_summary.get('final_kernel_patch_count')}",
        f"- kernel_patch_kinds: {feature_graph_summary.get('kernel_patch_kinds')}",
        f"- repair_packet_count: {feature_graph_summary.get('repair_packet_count')}",
        f"- final_repair_packet_count: {feature_graph_summary.get('final_repair_packet_count')}",
        f"- repair_packet_kinds: {feature_graph_summary.get('repair_packet_kinds')}",
        f"- kernel_patch_rounds: {feature_graph_summary.get('kernel_patch_rounds')}",
        f"- first_patch_round: {feature_graph_summary.get('first_patch_round')}",
        f"- latest_binding_families: {feature_graph_summary.get('latest_binding_families')}",
        f"- latest_binding_blocker_ids: {feature_graph_summary.get('latest_binding_blocker_ids')}",
        f"- latest_binding_primary_feature_ids: {feature_graph_summary.get('latest_binding_primary_feature_ids')}",
        f"- latest_binding_evidence_source: {feature_graph_summary.get('latest_binding_evidence_source')}",
        f"- latest_binding_completeness_relevance: {feature_graph_summary.get('latest_binding_completeness_relevance')}",
        f"- latest_binding_severity: {feature_graph_summary.get('latest_binding_severity')}",
        f"- latest_binding_repair_lane: {feature_graph_summary.get('latest_binding_repair_lane')}",
        f"- latest_binding_geometry_summary: {feature_graph_summary.get('latest_binding_geometry_summary')}",
        f"- latest_binding_feature_anchor_summary: {feature_graph_summary.get('latest_binding_feature_anchor_summary')}",
        f"- latest_patch_repair_mode: {feature_graph_summary.get('latest_patch_repair_mode')}",
        f"- latest_repair_packet_family_id: {feature_graph_summary.get('latest_repair_packet_family_id')}",
        f"- latest_repair_packet_feature_instance_id: {feature_graph_summary.get('latest_repair_packet_feature_instance_id')}",
        f"- latest_repair_packet_repair_mode: {feature_graph_summary.get('latest_repair_packet_repair_mode')}",
        f"- latest_repair_packet_recipe_id: {feature_graph_summary.get('latest_repair_packet_recipe_id')}",
        f"- latest_repair_packet_recipe_summary: {feature_graph_summary.get('latest_repair_packet_recipe_summary')}",
        f"- first_patch_feature_instance_ids: {feature_graph_summary.get('first_patch_feature_instance_ids')}",
        f"- latest_patch_feature_instance_ids: {feature_graph_summary.get('latest_patch_feature_instance_ids')}",
        f"- latest_patch_affected_hosts: {feature_graph_summary.get('latest_patch_affected_hosts')}",
        f"- latest_patch_anchor_keys: {feature_graph_summary.get('latest_patch_anchor_keys')}",
        "",
        "## Key Files",
        "",
    ]
    for name, path in key_artifacts.items():
        lines.append(f"- {name}: {path}")
    (case_dir / "benchmark_analysis.md").write_text(
        "\n".join(lines) + "\n",
        encoding="utf-8",
    )


def _recommended_fix_layer(
    category: str,
    validation_lanes: dict[str, Any] | None = None,
) -> str:
    validation_lanes = validation_lanes if isinstance(validation_lanes, dict) else {}
    if category in {"validator_evaluator_disagreement", "incomplete_run"}:
        if (
            category == "validator_evaluator_disagreement"
            and not validation_lanes.get("core_blockers")
            and validation_lanes.get("diagnostic_blockers")
        ):
            return "validator_lane_policy"
        return "validator_or_skill"
    if category in {"geometry_mismatch", "excessive_reinspection"}:
        return "tool_or_context"
    if category in {"runner_timeout", "runner_exception", "runtime_error"}:
        return "runtime_or_sandbox"
    if category in {"llm_error"}:
        return "llm_or_compaction"
    return "inspect_case_artifacts"


def _infer_first_bad_turn(round_entries: list[dict[str, Any]]) -> dict[str, Any] | None:
    for entry in round_entries:
        if not isinstance(entry, dict):
            continue
        if entry.get("tool_batch_error"):
            return {
                "round": entry.get("round"),
                "reason": entry.get("tool_batch_error"),
            }
        conversation = entry.get("conversation")
        if isinstance(conversation, list):
            for item in conversation:
                if not isinstance(item, dict):
                    continue
                payload = item.get("payload")
                if isinstance(payload, dict) and payload.get("is_complete") is False:
                    return {
                        "round": entry.get("round"),
                        "reason": payload.get("summary") or "validation_incomplete",
                    }
    return None


def _infer_repeated_useless_reads(round_entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    repeated: list[dict[str, Any]] = []
    for entry in round_entries:
        if not isinstance(entry, dict):
            continue
        completion = entry.get("round_completion")
        if not isinstance(completion, dict) or completion.get("inspection_only") is not True:
            continue
        timeline = entry.get("tool_timeline")
        if not isinstance(timeline, list):
            continue
        phases = [
            item
            for item in timeline
            if isinstance(item, dict)
            and item.get("phase") == "finished"
            and item.get("category") == "read"
            and isinstance(item.get("tool_name"), str)
            and str(item.get("tool_name")).strip()
        ]
        if not phases:
            continue
        repeated.append(
            {
                "round": entry.get("round"),
                "tools": [item.get("tool_name") for item in phases],
                "decision_summary": entry.get("decision_summary"),
            }
        )
    return repeated[-3:]


def _extract_first_positive_write(round_digest: dict[str, Any]) -> dict[str, Any] | None:
    round_entries = round_digest.get("rounds") if isinstance(round_digest, dict) else None
    if not isinstance(round_entries, list):
        return None
    for entry in round_entries:
        if not isinstance(entry, dict):
            continue
        round_no = entry.get("round")
        if not isinstance(round_no, int):
            continue
        tool_results = entry.get("tool_results")
        if not isinstance(tool_results, list):
            continue
        for result in tool_results:
            if not isinstance(result, dict):
                continue
            if result.get("category") != "write" or result.get("success") is not True:
                continue
            payload_summary = result.get("payload_summary")
            if not isinstance(payload_summary, dict):
                continue
            snapshot = payload_summary.get("snapshot")
            if not isinstance(snapshot, dict):
                continue
            geometry = snapshot.get("geometry")
            if not isinstance(geometry, dict):
                continue
            solids = geometry.get("solids")
            volume = geometry.get("volume")
            solid_count = int(solids or 0) if isinstance(solids, (int, float)) else 0
            solid_volume = float(volume or 0.0) if isinstance(volume, (int, float)) else 0.0
            if solid_count <= 0 and solid_volume <= 0.0:
                continue
            return {
                "round": round_no,
                "tool_name": str(result.get("tool_name") or "").strip() or None,
                "solids": solid_count,
                "volume": solid_volume,
            }
    return None


def _build_case_baseline_metrics(case_payload: dict[str, Any]) -> dict[str, Any]:
    runtime_summary = (
        case_payload.get("runtime_summary")
        if isinstance(case_payload.get("runtime_summary"), dict)
        else {}
    )
    round_digest = (
        case_payload.get("round_digest")
        if isinstance(case_payload.get("round_digest"), dict)
        else {}
    )
    feature_graph_summary = {}
    if isinstance(round_digest.get("domain_kernel_summary"), dict):
        feature_graph_summary = round_digest.get("domain_kernel_summary") or {}
    elif isinstance(round_digest.get("feature_graph_summary"), dict):
        feature_graph_summary = round_digest.get("feature_graph_summary") or {}
    analysis = (
        case_payload.get("analysis") if isinstance(case_payload.get("analysis"), dict) else {}
    )
    token_usage = (
        case_payload.get("token_usage")
        if isinstance(case_payload.get("token_usage"), dict)
        else {}
    )
    hallucination = normalize_hallucination_summary(
        runtime_summary.get("build123d_hallucination")
    )
    planner_rounds = int(runtime_summary.get("planner_rounds", 0) or 0)
    executed_action_count = int(runtime_summary.get("executed_action_count", 0) or 0)
    validation_complete = bool(runtime_summary.get("validation_complete"))
    first_positive_write = _extract_first_positive_write(round_digest)
    stale_probe_carry_count = int(runtime_summary.get("stale_probe_carry_count", 0) or 0)
    evidence_conflict_count = int(
        runtime_summary.get("evidence_conflict_count")
        or runtime_summary.get("freshness_conflict_count", 0)
        or 0
    )
    stale_evidence_incidents = stale_probe_carry_count + evidence_conflict_count
    repair_packet_count = int(feature_graph_summary.get("repair_packet_count", 0) or 0)
    latest_repair_packet_family_id = str(
        feature_graph_summary.get("latest_repair_packet_family_id") or ""
    ).strip()
    repair_packet_available = repair_packet_count > 0 or bool(latest_repair_packet_family_id)
    token_total = (
        int(token_usage.get("total_tokens"))
        if isinstance(token_usage.get("total_tokens"), (int, float))
        else None
    )
    first_solid_round = first_positive_write.get("round") if isinstance(first_positive_write, dict) else None
    repair_turns_after_first_write = None
    if isinstance(first_solid_round, int):
        repair_turns_after_first_write = max(planner_rounds - first_solid_round, 0)
    status = str(analysis.get("status") or "").strip()
    return {
        "case_id": str(case_payload.get("case_id") or ""),
        "status": status,
        "first_solid_success": first_positive_write is not None,
        "first_solid_round": first_solid_round,
        "first_solid_tool": (
            first_positive_write.get("tool_name")
            if isinstance(first_positive_write, dict)
            else None
        ),
        "requirement_complete": validation_complete,
        "rewrite_turn_count": max(executed_action_count - 1, 0),
        "write_turn_count": max(executed_action_count, 0),
        "repair_turns_after_first_write": repair_turns_after_first_write,
        "stale_evidence_incidents": stale_evidence_incidents,
        "tokens": token_total,
        "family_repair_packet_available": repair_packet_available,
        "family_repair_packet_hit": repair_packet_available and status == "PASS",
        "repair_packet_count": repair_packet_count,
        "latest_repair_packet_family_id": latest_repair_packet_family_id or None,
        "hallucination_event_count": int(hallucination.get("event_count", 0) or 0),
        "hallucination_weighted_score": float(
            hallucination.get("weighted_score", 0.0) or 0.0
        ),
        "hallucination_primary_layer": hallucination.get("primary_layer"),
    }


def _safe_ratio(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return float(numerator) / float(denominator)


def _summarize_baseline_metrics(case_payloads: list[dict[str, Any]]) -> dict[str, Any]:
    case_metrics = [_build_case_baseline_metrics(item) for item in case_payloads]
    total_cases = len(case_metrics)
    first_solid_success_case_count = sum(
        1 for item in case_metrics if item.get("first_solid_success") is True
    )
    requirement_complete_case_count = sum(
        1 for item in case_metrics if item.get("requirement_complete") is True
    )
    rewrite_turn_total = sum(int(item.get("rewrite_turn_count", 0) or 0) for item in case_metrics)
    write_turn_total = sum(int(item.get("write_turn_count", 0) or 0) for item in case_metrics)
    repair_turn_values = [
        float(item["repair_turns_after_first_write"])
        for item in case_metrics
        if isinstance(item.get("repair_turns_after_first_write"), (int, float))
    ]
    stale_evidence_incidents = sum(
        int(item.get("stale_evidence_incidents", 0) or 0) for item in case_metrics
    )
    successful_case_tokens = [
        int(item["tokens"])
        for item in case_metrics
        if item.get("status") == "PASS" and isinstance(item.get("tokens"), int)
    ]
    family_repair_packet_cases = [
        item for item in case_metrics if item.get("family_repair_packet_available") is True
    ]
    family_repair_packet_hit_case_count = sum(
        1 for item in family_repair_packet_cases if item.get("family_repair_packet_hit") is True
    )
    hallucination_event_count = sum(
        int(item.get("hallucination_event_count", 0) or 0) for item in case_metrics
    )
    hallucination_weighted_scores = [
        float(item.get("hallucination_weighted_score", 0.0) or 0.0) for item in case_metrics
    ]
    hallucination_primary_layer_counts = Counter(
        str(item.get("hallucination_primary_layer") or "").strip()
        for item in case_metrics
        if str(item.get("hallucination_primary_layer") or "").strip()
    )
    return {
        "total_cases": total_cases,
        "case_metrics": case_metrics,
        "first_solid_success_case_count": first_solid_success_case_count,
        "first_solid_success_rate": _safe_ratio(first_solid_success_case_count, total_cases),
        "requirement_complete_case_count": requirement_complete_case_count,
        "requirement_complete_rate": _safe_ratio(requirement_complete_case_count, total_cases),
        "runtime_rewrite_turn_count": rewrite_turn_total,
        "runtime_write_turn_count": write_turn_total,
        "runtime_rewrite_rate": _safe_ratio(rewrite_turn_total, write_turn_total),
        "mean_repair_turns_after_first_write": (
            sum(repair_turn_values) / len(repair_turn_values) if repair_turn_values else 0.0
        ),
        "stale_evidence_incidents": stale_evidence_incidents,
        "stale_evidence_case_count": sum(
            1 for item in case_metrics if int(item.get("stale_evidence_incidents", 0) or 0) > 0
        ),
        "tokens_per_successful_case": (
            float(sum(successful_case_tokens)) / float(len(successful_case_tokens))
            if successful_case_tokens
            else 0.0
        ),
        "family_repair_packet_case_count": len(family_repair_packet_cases),
        "family_repair_packet_hit_case_count": family_repair_packet_hit_case_count,
        "family_repair_packet_hit_rate": _safe_ratio(
            family_repair_packet_hit_case_count,
            len(family_repair_packet_cases),
        ),
        "hallucination_event_count": hallucination_event_count,
        "hallucination_weighted_score_mean": round(
            (sum(hallucination_weighted_scores) / len(hallucination_weighted_scores))
            if hallucination_weighted_scores
            else 0.0,
            4,
        ),
        "hallucination_primary_layer_counts": dict(hallucination_primary_layer_counts),
    }


def _build_brief_case_row(case_payload: dict[str, Any]) -> dict[str, Any]:
    evaluation = case_payload.get("evaluation") if isinstance(case_payload.get("evaluation"), dict) else {}
    runtime_summary = (
        case_payload.get("runtime_summary")
        if isinstance(case_payload.get("runtime_summary"), dict)
        else {}
    )
    analysis = case_payload.get("analysis") if isinstance(case_payload.get("analysis"), dict) else {}
    prompt_metrics = (
        case_payload.get("prompt_metrics")
        if isinstance(case_payload.get("prompt_metrics"), dict)
        else {}
    )
    token_usage = case_payload.get("token_usage") if isinstance(case_payload.get("token_usage"), dict) else {}
    hallucination = normalize_hallucination_summary(
        runtime_summary.get("build123d_hallucination")
    )
    feature_graph_summary = {}
    round_digest = case_payload.get("round_digest")
    if isinstance(round_digest, dict) and isinstance(round_digest.get("domain_kernel_summary"), dict):
        feature_graph_summary = round_digest.get("domain_kernel_summary") or {}
    elif isinstance(round_digest, dict) and isinstance(round_digest.get("feature_graph_summary"), dict):
        feature_graph_summary = round_digest.get("feature_graph_summary") or {}
    first_bad_turn = analysis.get("first_bad_turn") if isinstance(analysis.get("first_bad_turn"), dict) else {}
    last_good_write = analysis.get("last_good_write") if isinstance(analysis.get("last_good_write"), dict) else {}
    repeated_useless_reads = (
        analysis.get("repeated_useless_reads")
        if isinstance(analysis.get("repeated_useless_reads"), list)
        else []
    )
    baseline_metrics = (
        case_payload.get("baseline_metrics")
        if isinstance(case_payload.get("baseline_metrics"), dict)
        else _build_case_baseline_metrics(case_payload)
    )
    validation_call_count = int(runtime_summary.get("validation_call_count", 0) or 0)
    read_only_turn_count = int(
        runtime_summary.get("read_only_turn_count", runtime_summary.get("inspection_only_rounds", 0))
        or 0
    )

    score_value = evaluation.get("score")
    score = float(score_value) if isinstance(score_value, (int, float)) else None
    token_total = (
        int(token_usage["total_tokens"])
        if isinstance(token_usage.get("total_tokens"), (int, float))
        else None
    )

    return {
        "case_id": str(case_payload.get("case_id", "")),
        "status": str(analysis.get("status") or "UNKNOWN"),
        "end_to_end_status": str(analysis.get("end_to_end_status") or "unknown"),
        "eval_passed": evaluation.get("passed"),
        "validation_complete": bool(runtime_summary.get("validation_complete")),
        "score": score,
        "issue": str(analysis.get("likely_root_cause") or "unknown"),
        "tokens": token_total,
        "rounds": int(runtime_summary.get("planner_rounds", 0) or 0),
        "writes": int(runtime_summary.get("executed_action_count", 0) or 0),
        "inspections": int(runtime_summary.get("inspection_only_rounds", 0) or 0),
        "validation_call_count": validation_call_count,
        "repeated_validation_count": max(0, validation_call_count - 1),
        "read_only_turn_count": read_only_turn_count,
        "repeated_read_only_turn_count": len(repeated_useless_reads),
        "prompt_chars": int(prompt_metrics.get("max_final_chars", 0) or 0),
        "primary_write_mode": str(runtime_summary.get("primary_write_mode") or ""),
        "first_write_tool": str(runtime_summary.get("first_write_tool") or ""),
        "structured_bootstrap_rounds": int(
            runtime_summary.get("structured_bootstrap_rounds", 0) or 0
        ),
        "stale_probe_carry_count": int(
            runtime_summary.get("stale_probe_carry_count", 0) or 0
        ),
        "evidence_conflict_count": int(
            runtime_summary.get("evidence_conflict_count")
            or runtime_summary.get("freshness_conflict_count", 0)
            or 0
        ),
        "freshness_conflict_count": int(
            runtime_summary.get("freshness_conflict_count")
            or runtime_summary.get("evidence_conflict_count", 0)
            or 0
        ),
        "forced_policy_chain": [
            str(item)
            for item in (runtime_summary.get("forced_policy_chain") or [])
            if isinstance(item, str) and item.strip()
        ],
        "feature_probe_count": int(runtime_summary.get("feature_probe_count", 0) or 0),
        "probe_code_count": int(runtime_summary.get("probe_code_count", 0) or 0),
        "hallucination_events": int(hallucination.get("event_count", 0) or 0),
        "hallucination_weighted_score": float(
            hallucination.get("weighted_score", 0.0) or 0.0
        ),
        "hallucination_primary_layer": str(hallucination.get("primary_layer") or ""),
        "failure_cluster": str(
            runtime_summary.get("failure_cluster") or analysis.get("failure_cluster") or ""
        ),
        "recommended_fix_layer": str(analysis.get("recommended_fix_layer") or ""),
        "first_bad_turn_round": first_bad_turn.get("round"),
        "first_bad_turn_reason": first_bad_turn.get("reason"),
        "last_good_write_round": last_good_write.get("round"),
        "last_good_write_tool": last_good_write.get("tool"),
        "last_error": runtime_summary.get("last_error"),
        "domain_kernel_available": bool(feature_graph_summary.get("available")),
        "kernel_query_count": int(feature_graph_summary.get("graph_query_count", 0) or 0),
        "kernel_state_patch_count": int(feature_graph_summary.get("graph_patch_count", 0) or 0),
        "kernel_final_revision": feature_graph_summary.get("final_revision"),
        "kernel_binding_count": int(feature_graph_summary.get("kernel_binding_count", 0) or 0),
        "kernel_stale_binding_count": int(
            feature_graph_summary.get("kernel_stale_binding_count", 0) or 0
        ),
        "kernel_binding_kinds": list(feature_graph_summary.get("kernel_binding_kinds") or []),
        "feature_instance_count": int(feature_graph_summary.get("feature_instance_count", 0) or 0),
        "kernel_patch_count": int(feature_graph_summary.get("kernel_patch_count", 0) or 0),
        "kernel_patch_kinds": list(feature_graph_summary.get("kernel_patch_kinds") or []),
        "latest_patch_repair_mode": str(feature_graph_summary.get("latest_patch_repair_mode") or ""),
        "repair_packet_count": int(feature_graph_summary.get("repair_packet_count", 0) or 0),
        "repair_packet_kinds": list(feature_graph_summary.get("repair_packet_kinds") or []),
        "latest_repair_packet_family_id": str(
            feature_graph_summary.get("latest_repair_packet_family_id") or ""
        ),
        "latest_repair_packet_recipe_id": str(
            feature_graph_summary.get("latest_repair_packet_recipe_id") or ""
        ),
        "first_bad_feature_instance": str(analysis.get("first_bad_feature_instance") or ""),
        "repair_mode_counts": dict(analysis.get("repair_mode_counts") or {}),
        "kernel_blocked_count": len(feature_graph_summary.get("blocked_node_ids") or []),
        "kernel_unsatisfied_count": len(feature_graph_summary.get("unsatisfied_feature_ids") or []),
        "runtime_mode_effective": str(runtime_summary.get("runtime_mode_effective") or ""),
        "first_solid_success": bool(baseline_metrics.get("first_solid_success")),
        "first_solid_round": baseline_metrics.get("first_solid_round"),
        "repair_turns_after_first_write": baseline_metrics.get(
            "repair_turns_after_first_write"
        ),
        "runtime_rewrite_turn_count": int(
            baseline_metrics.get("rewrite_turn_count", 0) or 0
        ),
        "stale_evidence_incidents": int(
            baseline_metrics.get("stale_evidence_incidents", 0) or 0
        ),
        "family_repair_packet_hit": bool(
            baseline_metrics.get("family_repair_packet_hit")
        ),
    }


def _write_run_diagnostics(
    *,
    run_root: Path,
    case_payloads: list[dict[str, Any]],
    practice_identity: dict[str, Any],
) -> None:
    validator_disagreement_cases: list[dict[str, Any]] = []
    diagnostic_only_validator_cases: list[str] = []
    geometry_mismatch_cases: list[dict[str, Any]] = []
    runtime_error_cases: list[dict[str, Any]] = []
    end_to_end_pass_cases: list[dict[str, Any]] = []
    rows = [_build_brief_case_row(item) for item in case_payloads]
    baseline_metrics = _summarize_baseline_metrics(case_payloads)
    recommended_fix_layers = Counter(
        str(
            (
                item.get("analysis")
                if isinstance(item.get("analysis"), dict)
                else {}
            ).get("recommended_fix_layer")
            or "unknown"
        )
        for item in case_payloads
        if (
            isinstance(item.get("analysis"), dict)
            and str(item["analysis"].get("status") or "").strip() != "PASS"
        )
    )
    dominant_failure_clusters = Counter(
        row["failure_cluster"]
        for row in rows
        if (
            row.get("status") != "PASS"
            and isinstance(row.get("failure_cluster"), str)
            and row["failure_cluster"]
        )
    )
    repeated_validation_cases = [
        row["case_id"] for row in rows if int(row.get("repeated_validation_count", 0) or 0) > 0
    ]
    read_only_outlier_cases = [
        row["case_id"] for row in rows if int(row.get("read_only_turn_count", 0) or 0) >= 2
    ]
    structured_bootstrap_cases = [
        row["case_id"]
        for row in rows
        if int(row.get("structured_bootstrap_rounds", 0) or 0) > 0
    ]
    stale_probe_carry_cases = [
        row["case_id"]
        for row in rows
        if int(row.get("stale_probe_carry_count", 0) or 0) > 0
    ]
    evidence_conflict_cases = [
        row["case_id"]
        for row in rows
        if int(row.get("evidence_conflict_count", 0) or 0) > 0
    ]
    freshness_conflict_cases = [
        row["case_id"]
        for row in rows
        if int(row.get("freshness_conflict_count", 0) or 0) > 0
    ]
    read_stall_cases = [
        item.get("case_id")
        for item in case_payloads
        if isinstance(item.get("analysis"), dict)
        and isinstance(item["analysis"].get("repeated_useless_reads"), list)
        and item["analysis"]["repeated_useless_reads"]
    ]
    kernel_trace_available_cases = [
        row["case_id"] for row in rows if row.get("domain_kernel_available") is True
    ]
    kernel_query_cases = [
        row["case_id"] for row in rows if int(row.get("kernel_query_count", 0) or 0) > 0
    ]
    kernel_state_patch_cases = [
        row["case_id"] for row in rows if int(row.get("kernel_state_patch_count", 0) or 0) > 0
    ]
    kernel_patch_cases = [
        row["case_id"] for row in rows if int(row.get("kernel_patch_count", 0) or 0) > 0
    ]
    kernel_blocked_cases = [
        row["case_id"] for row in rows if int(row.get("kernel_blocked_count", 0) or 0) > 0
    ]
    kernel_unsatisfied_cases = [
        row["case_id"] for row in rows if int(row.get("kernel_unsatisfied_count", 0) or 0) > 0
    ]
    final_kernel_sync_reasons = Counter()
    requirement_tag_counter = Counter()
    blocker_taxonomy_counts = Counter()
    repair_mode_counts = Counter()
    kernel_patch_kind_counts = Counter()
    hallucination_primary_layer_counts = Counter()
    hallucination_category_counts = Counter()

    for row in rows:
        status = row["status"]
        if status == "PASS":
            end_to_end_pass_cases.append(row)
        elif status == "VALIDATOR_MISMATCH":
            validator_disagreement_cases.append(row)
        elif status == "EVAL_FAIL":
            geometry_mismatch_cases.append(row)
        else:
            runtime_error_cases.append(row)

    for item in case_payloads:
        analysis = item.get("analysis") if isinstance(item.get("analysis"), dict) else {}
        validation_lanes = analysis.get("validation_lanes") if isinstance(analysis.get("validation_lanes"), dict) else {}
        if (
            analysis.get("status") == "VALIDATOR_MISMATCH"
            and not validation_lanes.get("core_blockers")
            and validation_lanes.get("diagnostic_blockers")
        ):
            diagnostic_only_validator_cases.append(str(item.get("case_id") or ""))
        round_digest = item.get("round_digest")
        feature_graph_summary = (
            round_digest.get("domain_kernel_summary")
            if isinstance(round_digest, dict) and isinstance(round_digest.get("domain_kernel_summary"), dict)
            else round_digest.get("feature_graph_summary")
            if isinstance(round_digest, dict) and isinstance(round_digest.get("feature_graph_summary"), dict)
            else {}
        )
        sync_reason = str(feature_graph_summary.get("latest_sync_reason") or "").strip()
        if sync_reason:
            final_kernel_sync_reasons[sync_reason] += 1
        for tag in feature_graph_summary.get("requirement_tags") or []:
            if isinstance(tag, str) and tag.strip():
                requirement_tag_counter[tag.strip()] += 1
        analysis = item.get("analysis") if isinstance(item.get("analysis"), dict) else {}
        validation_lanes = (
            analysis.get("validation_lanes")
            if isinstance(analysis.get("validation_lanes"), dict)
            else {}
        )
        for family_id, count in (validation_lanes.get("blocker_taxonomy_counts") or {}).items():
            if isinstance(family_id, str) and family_id.strip():
                blocker_taxonomy_counts[family_id.strip()] += int(count or 0)
        for repair_mode, count in (analysis.get("repair_mode_counts") or {}).items():
            if isinstance(repair_mode, str) and repair_mode.strip():
                repair_mode_counts[repair_mode.strip()] += int(count or 0)
        for patch_kind in feature_graph_summary.get("kernel_patch_kinds") or []:
            if isinstance(patch_kind, str) and patch_kind.strip():
                kernel_patch_kind_counts[patch_kind.strip()] += 1
        runtime_summary = (
            item.get("runtime_summary")
            if isinstance(item.get("runtime_summary"), dict)
            else {}
        )
        hallucination = normalize_hallucination_summary(
            runtime_summary.get("build123d_hallucination")
        )
        primary_layer = str(hallucination.get("primary_layer") or "").strip()
        if primary_layer:
            hallucination_primary_layer_counts[primary_layer] += 1
        for category, count in (hallucination.get("categories") or {}).items():
            if isinstance(category, str) and category.strip():
                hallucination_category_counts[category.strip()] += int(count or 0)

    top_token_cases = sorted(
        (row for row in rows if isinstance(row.get("tokens"), int)),
        key=lambda item: int(item["tokens"]),
        reverse=True,
    )[:5]
    top_prompt_cases = sorted(rows, key=lambda item: int(item["prompt_chars"]), reverse=True)[:5]
    top_hallucination_cases = sorted(
        rows,
        key=lambda item: (
            float(item.get("hallucination_weighted_score", 0.0) or 0.0),
            int(item.get("hallucination_events", 0) or 0),
        ),
        reverse=True,
    )[:5]

    diagnostics_payload = {
        "practice_identity": practice_identity,
        "total_cases": len(rows),
        "baseline_metrics": baseline_metrics,
        "end_to_end_pass_cases": [row["case_id"] for row in end_to_end_pass_cases],
        "validator_disagreement_cases": [row["case_id"] for row in validator_disagreement_cases],
        "diagnostic_only_validator_cases": diagnostic_only_validator_cases,
        "geometry_mismatch_cases": [row["case_id"] for row in geometry_mismatch_cases],
        "runtime_error_cases": [row["case_id"] for row in runtime_error_cases],
        "top_token_cases": top_token_cases,
        "top_prompt_cases": top_prompt_cases,
        "recommended_fix_layers": dict(recommended_fix_layers),
        "recommended_fix_layer_counts": dict(recommended_fix_layers),
        "dominant_failure_clusters": dict(dominant_failure_clusters),
        "failure_cluster_counts": dict(dominant_failure_clusters),
        "repeated_validation_cases": repeated_validation_cases,
        "read_only_outlier_cases": read_only_outlier_cases,
        "structured_bootstrap_cases": structured_bootstrap_cases,
        "stale_probe_carry_cases": stale_probe_carry_cases,
        "evidence_conflict_cases": evidence_conflict_cases,
        "freshness_conflict_cases": freshness_conflict_cases,
        "read_stall_cases": read_stall_cases,
        "kernel_trace_available_cases": kernel_trace_available_cases,
        "kernel_query_cases": kernel_query_cases,
        "kernel_state_patch_cases": kernel_state_patch_cases,
        "kernel_patch_cases": kernel_patch_cases,
        "kernel_blocked_cases": kernel_blocked_cases,
        "kernel_unsatisfied_cases": kernel_unsatisfied_cases,
        "final_kernel_sync_reasons": dict(final_kernel_sync_reasons),
        "requirement_tag_counter": dict(requirement_tag_counter),
        "blocker_taxonomy_counts": dict(blocker_taxonomy_counts),
        "repair_mode_counts": dict(repair_mode_counts),
        "kernel_patch_kind_counts": dict(kernel_patch_kind_counts),
        "hallucination_primary_layer_counts": dict(hallucination_primary_layer_counts),
        "hallucination_category_counts": dict(hallucination_category_counts),
        "top_hallucination_cases": top_hallucination_cases,
    }
    _write_json(run_root / "run_diagnostics.json", diagnostics_payload)

    lines = [
        "# Benchmark Diagnostics",
        "",
        "## Practice",
        "",
        f"- {practice_identity.get('practice_label')}",
        "",
        "## Overview",
        "",
        f"- total_cases: {len(rows)}",
        f"- baseline_metrics: {baseline_metrics}",
        f"- end_to_end_pass_cases: {[row['case_id'] for row in end_to_end_pass_cases]}",
        f"- validator_disagreement_cases: {[row['case_id'] for row in validator_disagreement_cases]}",
        f"- diagnostic_only_validator_cases: {diagnostic_only_validator_cases}",
        f"- geometry_mismatch_cases: {[row['case_id'] for row in geometry_mismatch_cases]}",
        f"- runtime_error_cases: {[row['case_id'] for row in runtime_error_cases]}",
        f"- recommended_fix_layers: {dict(recommended_fix_layers)}",
        f"- dominant_failure_clusters: {dict(dominant_failure_clusters)}",
        f"- repeated_validation_cases: {repeated_validation_cases}",
        f"- read_only_outlier_cases: {read_only_outlier_cases}",
        f"- structured_bootstrap_cases: {structured_bootstrap_cases}",
        f"- stale_probe_carry_cases: {stale_probe_carry_cases}",
        f"- evidence_conflict_cases: {evidence_conflict_cases}",
        f"- freshness_conflict_cases: {freshness_conflict_cases}",
        f"- read_stall_cases: {read_stall_cases}",
        "",
        "## Domain Kernel Coverage",
        "",
        f"- kernel_trace_available_cases: {kernel_trace_available_cases}",
        f"- kernel_query_cases: {kernel_query_cases}",
        f"- kernel_state_patch_cases: {kernel_state_patch_cases}",
        f"- kernel_patch_cases: {kernel_patch_cases}",
        f"- kernel_blocked_cases: {kernel_blocked_cases}",
        f"- kernel_unsatisfied_cases: {kernel_unsatisfied_cases}",
        f"- final_kernel_sync_reasons: {dict(final_kernel_sync_reasons)}",
        f"- requirement_tag_counter: {dict(requirement_tag_counter)}",
        f"- blocker_taxonomy_counts: {dict(blocker_taxonomy_counts)}",
        f"- repair_mode_counts: {dict(repair_mode_counts)}",
        f"- kernel_patch_kind_counts: {dict(kernel_patch_kind_counts)}",
        f"- hallucination_primary_layer_counts: {dict(hallucination_primary_layer_counts)}",
        f"- hallucination_category_counts: {dict(hallucination_category_counts)}",
        "",
        "## Token Outliers",
        "",
    ]
    for row in top_token_cases:
        lines.append(
            f"- {row['case_id']}: tokens={row['tokens']}, status={row['status']}, rounds={row['rounds']}, first_write_tool={row.get('first_write_tool') or '-'}, structured_bootstrap_rounds={row.get('structured_bootstrap_rounds') or 0}, stale_probe_carry_count={row.get('stale_probe_carry_count') or 0}, freshness_conflict_count={row.get('freshness_conflict_count') or 0}, evidence_conflict_count={row.get('evidence_conflict_count') or 0}, validation_calls={row.get('validation_call_count') or 0}, read_only_turns={row.get('read_only_turn_count') or 0}, issue={row['issue']}"
        )
    lines.extend(["", "## Prompt Size Outliers", ""])
    for row in top_prompt_cases:
        lines.append(
            f"- {row['case_id']}: prompt_chars={row['prompt_chars']}, status={row['status']}, rounds={row['rounds']}, first_write_tool={row.get('first_write_tool') or '-'}, structured_bootstrap_rounds={row.get('structured_bootstrap_rounds') or 0}, stale_probe_carry_count={row.get('stale_probe_carry_count') or 0}, freshness_conflict_count={row.get('freshness_conflict_count') or 0}, evidence_conflict_count={row.get('evidence_conflict_count') or 0}, validation_calls={row.get('validation_call_count') or 0}, read_only_turns={row.get('read_only_turn_count') or 0}, issue={row['issue']}"
        )
    lines.extend(["", "## Probe Activity", ""])
    for row in sorted(
        rows,
        key=lambda item: (int(item.get("feature_probe_count", 0)), int(item.get("probe_code_count", 0))),
        reverse=True,
    )[:5]:
        lines.append(
            f"- {row['case_id']}: primary_write_mode={row.get('primary_write_mode') or '-'}, feature_probe_count={row.get('feature_probe_count')}, probe_code_count={row.get('probe_code_count')}, failure_cluster={row.get('failure_cluster') or '-'}"
        )
    lines.extend(["", "## Hallucination Outliers", ""])
    for row in top_hallucination_cases:
        lines.append(
            f"- {row['case_id']}: hallucination_events={row.get('hallucination_events') or 0}, hallucination_weighted_score={row.get('hallucination_weighted_score') or 0.0}, hallucination_primary_layer={row.get('hallucination_primary_layer') or '-'}, issue={row['issue']}"
        )
    (run_root / "run_diagnostics.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_brief_report(
    *,
    run_root: Path,
    case_payloads: list[dict[str, Any]],
    practice_identity: dict[str, Any],
) -> None:
    rows = [_build_brief_case_row(item) for item in case_payloads]
    baseline_metrics = _summarize_baseline_metrics(case_payloads)
    failure_rows = [row for row in rows if row["status"] != "PASS"]
    tsv_lines = [
        "case_id\tstatus\teval_passed\tvalidation_complete\tscore\ttokens\trounds\twrites\tinspections\tvalidation_calls\trepeated_validation\tread_only_turns\tprompt_chars\truntime_mode_effective\tprimary_write_mode\tfirst_write_tool\tfirst_solid_success\tfirst_solid_round\trepair_turns_after_first_write\truntime_rewrite_turn_count\tstructured_bootstrap_rounds\tstale_probe_carry_count\tstale_evidence_incidents\tfreshness_conflict_count\tevidence_conflict_count\tforced_policy_chain\tfeature_probe_count\tprobe_code_count\thallucination_events\thallucination_weighted_score\thallucination_primary_layer\tfailure_cluster\trecommended_fix_layer\tfirst_bad_turn_round\tfirst_bad_feature_instance\tlast_good_write_round\tlast_good_write_tool\tkernel_binding_count\tkernel_binding_kinds\tfeature_instance_count\tkernel_patch_count\tkernel_patch_kinds\trepair_packet_count\tlatest_repair_packet_family_id\tfamily_repair_packet_hit\tlatest_patch_repair_mode\tissue"
    ]
    for row in rows:
        score_text = "" if row["score"] is None else f"{row['score']:.4f}"
        token_text = "" if row["tokens"] is None else str(row["tokens"])
        eval_text = (
            "pass" if row["eval_passed"] is True else
            "fail" if row["eval_passed"] is False else
            ""
        )
        validation_text = "done" if row["validation_complete"] else "open"
        forced_policy_chain_text = ",".join(row.get("forced_policy_chain") or [])
        tsv_lines.append(
            f"{row['case_id']}\t{row['status']}\t{eval_text}\t{validation_text}\t{score_text}\t{token_text}\t{row['rounds']}\t{row['writes']}\t{row['inspections']}\t{row.get('validation_call_count') or 0}\t{row.get('repeated_validation_count') or 0}\t{row.get('read_only_turn_count') or 0}\t{row['prompt_chars']}\t{row.get('runtime_mode_effective') or ''}\t{row.get('primary_write_mode') or ''}\t{row.get('first_write_tool') or ''}\t{int(row.get('first_solid_success') is True)}\t{row.get('first_solid_round') or ''}\t{row.get('repair_turns_after_first_write') if row.get('repair_turns_after_first_write') is not None else ''}\t{row.get('runtime_rewrite_turn_count') or 0}\t{row.get('structured_bootstrap_rounds') or 0}\t{row.get('stale_probe_carry_count') or 0}\t{row.get('stale_evidence_incidents') or 0}\t{row.get('freshness_conflict_count') or 0}\t{row.get('evidence_conflict_count') or 0}\t{forced_policy_chain_text}\t{row.get('feature_probe_count') or 0}\t{row.get('probe_code_count') or 0}\t{row.get('hallucination_events') or 0}\t{row.get('hallucination_weighted_score') or 0.0}\t{row.get('hallucination_primary_layer') or ''}\t{row.get('failure_cluster') or ''}\t{row.get('recommended_fix_layer') or ''}\t{row.get('first_bad_turn_round') or ''}\t{row.get('first_bad_feature_instance') or ''}\t{row.get('last_good_write_round') or ''}\t{row.get('last_good_write_tool') or ''}\t{row.get('kernel_binding_count') or 0}\t{','.join(row.get('kernel_binding_kinds') or [])}\t{row.get('feature_instance_count') or 0}\t{row.get('kernel_patch_count') or 0}\t{','.join(row.get('kernel_patch_kinds') or [])}\t{row.get('repair_packet_count') or 0}\t{row.get('latest_repair_packet_family_id') or ''}\t{int(row.get('family_repair_packet_hit') is True)}\t{row.get('latest_patch_repair_mode') or ''}\t{row['issue']}"
        )
    (run_root / "brief_report.tsv").write_text(
        "\n".join(tsv_lines) + "\n",
        encoding="utf-8",
    )

    md_lines = [
        "# Brief Benchmark Report",
        "",
        "## Practice",
        "",
        f"- {practice_identity.get('practice_label')}",
        "",
        "## Overview",
        "",
        f"- total_cases: {len(rows)}",
        f"- baseline_metrics: {baseline_metrics}",
        f"- end_to_end_pass_cases: {sum(1 for row in rows if row['status'] == 'PASS')}",
        f"- evaluator_pass_cases: {sum(1 for row in rows if row['eval_passed'] is True)}",
        f"- validation_complete_cases: {sum(1 for row in rows if row['validation_complete'])}",
        f"- validator_disagreement_cases: {sum(1 for row in rows if row['status'] == 'VALIDATOR_MISMATCH')}",
        f"- runtime_mode_effective_counts: {dict(Counter(row.get('runtime_mode_effective') or 'unknown' for row in rows))}",
        f"- primary_write_modes: {dict(Counter(row.get('primary_write_mode') or 'unknown' for row in rows))}",
        f"- first_write_tools: {dict(Counter(row.get('first_write_tool') or 'unknown' for row in rows))}",
        f"- dominant_failure_clusters: {dict(Counter(row.get('failure_cluster') or 'none' for row in rows if row['status'] != 'PASS'))}",
        f"- recommended_fix_layer_counts: {dict(Counter(row.get('recommended_fix_layer') or 'unknown' for row in failure_rows))}",
        f"- repeated_validation_cases: {sum(1 for row in rows if int(row.get('repeated_validation_count', 0) or 0) > 0)}",
        f"- read_only_outlier_cases: {sum(1 for row in rows if int(row.get('read_only_turn_count', 0) or 0) >= 2)}",
        f"- structured_bootstrap_cases: {sum(1 for row in rows if int(row.get('structured_bootstrap_rounds', 0) or 0) > 0)}",
        f"- stale_probe_carry_cases: {sum(1 for row in rows if int(row.get('stale_probe_carry_count', 0) or 0) > 0)}",
        f"- freshness_conflict_cases: {sum(1 for row in rows if int(row.get('freshness_conflict_count', 0) or 0) > 0)}",
        f"- evidence_conflict_cases: {sum(1 for row in rows if int(row.get('evidence_conflict_count', 0) or 0) > 0)}",
        f"- hallucination_event_count: {sum(int(row.get('hallucination_events', 0) or 0) for row in rows)}",
        f"- hallucination_primary_layers: {dict(Counter(row.get('hallucination_primary_layer') or 'none' for row in rows if row.get('hallucination_primary_layer')))}",
        f"- kernel_trace_available_cases: {sum(1 for row in rows if row.get('domain_kernel_available') is True)}",
        f"- kernel_query_cases: {sum(1 for row in rows if int(row.get('kernel_query_count', 0) or 0) > 0)}",
        f"- kernel_blocked_cases: {sum(1 for row in rows if int(row.get('kernel_blocked_count', 0) or 0) > 0)}",
        f"- kernel_patch_cases: {sum(1 for row in rows if int(row.get('kernel_patch_count', 0) or 0) > 0)}",
        f"- repair_mode_counts: {dict(Counter(row.get('latest_patch_repair_mode') or 'none' for row in rows if row.get('latest_patch_repair_mode')))}",
        "",
        "| case_id | status | eval | validation | score | tokens | rounds | writes | inspections | validation_calls | repeated_validation | read_only_turns | prompt_chars | write_mode | probes | probe_code | cluster | fix_layer | first_bad_turn | last_good_write | issue |",
        "| --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | ---: | ---: | --- | --- | ---: | --- | --- |",
    ]
    for row in rows:
        score_text = "-" if row["score"] is None else f"{row['score']:.4f}"
        token_text = "-" if row["tokens"] is None else str(row["tokens"])
        issue_text = row["issue"].replace("\n", " ").replace("|", "/")
        eval_text = (
            "pass" if row["eval_passed"] is True else
            "fail" if row["eval_passed"] is False else
            "-"
        )
        validation_text = "done" if row["validation_complete"] else "open"
        first_bad_turn_text = (
            "-" if row.get("first_bad_turn_round") in {None, ""} else str(row.get("first_bad_turn_round"))
        )
        last_good_write_text = (
            "-"
            if row.get("last_good_write_round") in {None, ""} and not row.get("last_good_write_tool")
            else f"{row.get('last_good_write_round') or '-'}:{row.get('last_good_write_tool') or '-'}"
        )
        md_lines.append(
            f"| {row['case_id']} | {row['status']} | {eval_text} | {validation_text} | {score_text} | {token_text} | {row['rounds']} | {row['writes']} | {row['inspections']} | {row.get('validation_call_count') or 0} | {row.get('repeated_validation_count') or 0} | {row.get('read_only_turn_count') or 0} | {row['prompt_chars']} | {row.get('primary_write_mode') or '-'} | {row.get('feature_probe_count') or 0} | {row.get('probe_code_count') or 0} | {row.get('failure_cluster') or '-'} | {row.get('recommended_fix_layer') or '-'} | {first_bad_turn_text} | {last_good_write_text} | {issue_text} |"
        )
    if failure_rows:
        md_lines.extend(["", "## Failure Details", ""])
        for row in failure_rows:
            case_payload = next(
                (item for item in case_payloads if item.get("case_id") == row["case_id"]),
                {},
            )
            analysis = case_payload.get("analysis") if isinstance(case_payload.get("analysis"), dict) else {}
            key_artifacts = analysis.get("key_artifacts") if isinstance(analysis.get("key_artifacts"), dict) else {}
            feature_graph_summary = analysis.get("domain_kernel_summary") if isinstance(analysis.get("domain_kernel_summary"), dict) else {}
            if not feature_graph_summary and isinstance(analysis.get("feature_graph_summary"), dict):
                feature_graph_summary = analysis.get("feature_graph_summary")
            md_lines.append(f"### {row['case_id']}")
            md_lines.append("")
            md_lines.append(f"- status: {row['status']}")
            md_lines.append(f"- failure_category: {analysis.get('failure_category')}")
            md_lines.append(f"- failure_cluster: {analysis.get('failure_cluster')}")
            md_lines.append(f"- recommended_fix_layer: {analysis.get('recommended_fix_layer')}")
            md_lines.append(f"- first_bad_turn: {analysis.get('first_bad_turn')}")
            md_lines.append(f"- last_good_write: {analysis.get('last_good_write')}")
            md_lines.append(f"- likely_root_cause: {row['issue']}")
            md_lines.append(f"- primary_write_mode: {row.get('primary_write_mode') or '-'}")
            md_lines.append(f"- first_write_tool: {row.get('first_write_tool') or '-'}")
            md_lines.append(f"- structured_bootstrap_rounds: {row.get('structured_bootstrap_rounds') or 0}")
            md_lines.append(f"- stale_probe_carry_count: {row.get('stale_probe_carry_count') or 0}")
            md_lines.append(f"- evidence_conflict_count: {row.get('evidence_conflict_count') or 0}")
            md_lines.append(f"- forced_policy_chain: {row.get('forced_policy_chain') or []}")
            md_lines.append(f"- validation_call_count: {row.get('validation_call_count') or 0}")
            md_lines.append(f"- repeated_validation_count: {row.get('repeated_validation_count') or 0}")
            md_lines.append(f"- read_only_turn_count: {row.get('read_only_turn_count') or 0}")
            md_lines.append(f"- feature_probe_count: {row.get('feature_probe_count') or 0}")
            md_lines.append(f"- probe_code_count: {row.get('probe_code_count') or 0}")
            md_lines.append(f"- kernel_query_count: {feature_graph_summary.get('graph_query_count')}")
            md_lines.append(f"- kernel_final_revision: {feature_graph_summary.get('final_revision')}")
            md_lines.append(f"- kernel_blocked_node_ids: {feature_graph_summary.get('blocked_node_ids')}")
            md_lines.append(f"- kernel_unsatisfied_feature_ids: {feature_graph_summary.get('unsatisfied_feature_ids')}")
            md_lines.append(f"- kernel_latest_sync_reason: {feature_graph_summary.get('latest_sync_reason')}")
            for name, path in key_artifacts.items():
                md_lines.append(f"- {name}: {path}")
            md_lines.append("")
    (run_root / "brief_report.md").write_text(
        "\n".join(md_lines) + "\n",
        encoding="utf-8",
    )


def _decode_csv(path: Path) -> tuple[list[dict[str, str]], str]:
    for encoding in CSV_ENCODINGS:
        try:
            with path.open("r", encoding=encoding, newline="") as handle:
                rows = list(csv.DictReader(handle))
            return rows, encoding
        except UnicodeDecodeError:
            continue
    raise RuntimeError(f"failed to decode CSV: {path}")


def _load_case_overrides(dataset_root: Path) -> dict[str, dict[str, Any]]:
    override_path = dataset_root / "canonical_case_overrides.json"
    if not override_path.exists():
        return {}
    try:
        payload = json.loads(override_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise RuntimeError(
            f"failed to decode canonical override manifest: {override_path}"
        ) from exc

    raw_cases = payload.get("cases")
    if not isinstance(raw_cases, dict):
        return {}

    overrides: dict[str, dict[str, Any]] = {}
    for case_id, value in raw_cases.items():
        if not isinstance(case_id, str) or not isinstance(value, dict):
            continue
        overrides[case_id.strip()] = value
    return overrides


def _load_case_sets(manifest_path: Path) -> dict[str, list[str]]:
    if not manifest_path.exists():
        return {}
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise RuntimeError(f"failed to decode case-set manifest: {manifest_path}") from exc
    raw_case_sets = payload.get("case_sets")
    if not isinstance(raw_case_sets, dict):
        return {}
    case_sets: dict[str, list[str]] = {}
    for name, raw_ids in raw_case_sets.items():
        if not isinstance(name, str) or not isinstance(raw_ids, list):
            continue
        case_ids = [
            str(case_id).strip()
            for case_id in raw_ids
            if isinstance(case_id, str) and str(case_id).strip()
        ]
        if case_ids:
            case_sets[name.strip()] = case_ids
    return case_sets


def _pick_prompt(
    row: dict[str, str],
    preferred_field: str,
) -> tuple[str, str]:
    if preferred_field:
        text = str(row.get(preferred_field, "")).strip()
        if text:
            return text, preferred_field
    for field in PROMPT_FIELD_FALLBACK:
        text = str(row.get(field, "")).strip()
        if text:
            return text, field
    return "", ""


def _sort_case_key(case_id: str) -> tuple[int, int]:
    left, _, right = case_id.partition("_")
    try:
        level_num = int(left.removeprefix("L"))
    except ValueError:
        level_num = 999
    try:
        sample_num = int(right)
    except ValueError:
        sample_num = 999999
    return level_num, sample_num


def _load_case_map(dataset_root: Path, preferred_field: str) -> dict[str, BenchmarkCase]:
    case_map: dict[str, BenchmarkCase] = {}
    case_overrides = _load_case_overrides(dataset_root)
    for level in ("L1", "L2", "L3"):
        level_dir = dataset_root / level
        csv_path = level_dir / f"{level}_sampled_rows.csv"
        if not csv_path.exists():
            continue
        rows, encoding = _decode_csv(csv_path)
        for row in rows:
            case_id = str(row.get("id", "")).strip()
            if not case_id:
                continue
            prompt, prompt_field = _pick_prompt(row=row, preferred_field=preferred_field)
            gt_path = level_dir / "steps" / f"{case_id}.step"
            override = case_overrides.get(case_id, {})
            override_prompt = str(override.get("prompt", "")).strip()
            if override_prompt:
                prompt = override_prompt
                prompt_field = str(override.get("prompt_field", "canonical_override")).strip() or "canonical_override"
            override_gt_path = str(override.get("gt_step_path", "")).strip()
            if override_gt_path:
                override_gt_candidate = Path(override_gt_path)
                if not override_gt_candidate.is_absolute():
                    override_gt_candidate = (dataset_root / override_gt_candidate).resolve()
                gt_path = override_gt_candidate
            case_map[case_id] = BenchmarkCase(
                case_id=case_id,
                level=level,
                prompt=prompt,
                prompt_field=prompt_field,
                csv_path=f"{csv_path} (encoding={encoding})",
                gt_step_path=str(gt_path.resolve()),
                canonical_reference=str(override.get("canonical_reference", "")).strip() or None,
                reference_notes=str(override.get("notes", "")).strip() or None,
            )
    return case_map


def _select_cases(
    case_map: dict[str, BenchmarkCase],
    case_ids_raw: str,
    levels_raw: str,
    limit: int,
    case_set_name: str = "",
    case_sets: dict[str, list[str]] | None = None,
) -> list[BenchmarkCase]:
    if case_ids_raw.strip():
        case_ids = [item.strip() for item in case_ids_raw.split(",") if item.strip()]
    elif case_set_name.strip():
        selected_case_ids = (case_sets or {}).get(case_set_name.strip())
        if not isinstance(selected_case_ids, list) or not selected_case_ids:
            raise KeyError(f"unknown case set: {case_set_name.strip()}")
        case_ids = selected_case_ids
    else:
        case_ids = []

    if case_ids:
        missing: list[str] = []
        selected: list[BenchmarkCase] = []
        for case_id in case_ids:
            case = case_map.get(case_id)
            if case is None:
                missing.append(case_id)
                continue
            selected.append(case)
        if missing:
            raise KeyError(f"unknown case ids: {', '.join(missing)}")
    else:
        selected = sorted(case_map.values(), key=lambda item: _sort_case_key(item.case_id))

    if levels_raw.strip():
        allowed = {item.strip().upper() for item in levels_raw.split(",") if item.strip()}
        selected = [item for item in selected if item.level.upper() in allowed]

    if limit > 0:
        selected = selected[:limit]
    return selected


def _build_case_meta(case: BenchmarkCase, case_dir: Path) -> dict[str, Any]:
    return {
        "case_id": case.case_id,
        "level": case.level,
        "prompt_field": case.prompt_field,
        "used_canonical_override": case.prompt_field == "canonical_override",
        "prompt": case.prompt,
        "csv_path": case.csv_path,
        "ground_truth_step": case.gt_step_path,
        "canonical_reference": case.canonical_reference,
        "reference_notes": case.reference_notes,
        "expected_generated_step": str((case_dir / "outputs" / "model.step").resolve()),
        "generated_step_candidates": [
            str((case_dir / "outputs" / "model.step").resolve()),
            str((case_dir / "outputs" / "final_model.step").resolve()),
        ],
        "llm_judge_extension": {
            "enabled": False,
            "status": "not_requested",
            "score": None,
            "notes": "reserved_for_future_manual_or_llm_judge_extension",
        },
    }


def _resolve_case_timeout_seconds(args: argparse.Namespace) -> int:
    explicit_timeout = int(getattr(args, "case_timeout", 0) or 0)
    if explicit_timeout > 0:
        return explicit_timeout
    max_rounds = int(args.max_rounds) if int(args.max_rounds) > 0 else 6
    sandbox_timeout = int(args.sandbox_timeout) if int(args.sandbox_timeout) > 0 else 180
    per_round_budget = max(180, sandbox_timeout) * 4
    return max(900, max_rounds * per_round_budget)


def main() -> int:
    args = _parse_args()
    repo_root = Path(__file__).resolve().parents[1]
    dataset_root = (
        args.dataset_root
        if args.dataset_root.is_absolute()
        else (repo_root / args.dataset_root)
    ).resolve()
    runs_root = (
        args.runs_root if args.runs_root.is_absolute() else (repo_root / args.runs_root)
    ).resolve()
    runner_script = (repo_root / "scripts" / "run_aci_live_probe.sh").resolve()

    if not dataset_root.exists():
        raise FileNotFoundError(f"dataset_root does not exist: {dataset_root}")
    if not runner_script.exists():
        raise FileNotFoundError(f"runner script missing: {runner_script}")

    case_map = _load_case_map(dataset_root=dataset_root, preferred_field=args.prompt_field.strip())
    case_sets = _load_case_sets(_CASE_SET_MANIFEST_PATH)
    selected = _select_cases(
        case_map=case_map,
        case_ids_raw=args.cases,
        levels_raw=args.levels,
        limit=max(0, args.limit),
        case_set_name=args.case_set,
        case_sets=case_sets,
    )
    if not selected:
        raise ValueError("no benchmark case selected")

    runtime_mode = _resolve_runtime_mode(args)
    practice_identity = _derive_practice_identity(args=args, selected=selected)
    run_root = runs_root / args.run_id
    run_root.mkdir(parents=True, exist_ok=True)
    case_timeout_seconds = _resolve_case_timeout_seconds(args)
    _write_json(
        run_root / "run_manifest.json",
        {
            "run_id": args.run_id,
            "run_root": str(run_root),
            "dataset_root": str(dataset_root),
            "selected_case_count": len(selected),
            "selected_cases": [asdict(item) for item in selected],
            "case_set_name": args.case_set.strip() or None,
            "case_set_manifest": (
                str(_CASE_SET_MANIFEST_PATH.resolve())
                if _CASE_SET_MANIFEST_PATH.exists()
                else None
            ),
            "provider_override": args.reasoning_provider or None,
            "model_override": args.reasoning_model or None,
            "practice_identity": practice_identity,
            "max_rounds_override": args.max_rounds if args.max_rounds > 0 else None,
            "sandbox_timeout_override": (
                args.sandbox_timeout if args.sandbox_timeout > 0 else None
            ),
            "case_timeout_seconds": case_timeout_seconds,
            "one_action_per_round": bool(args.one_action_per_round),
            "evaluation_enabled": not args.skip_eval,
            "evaluation_threshold": args.eval_threshold,
            "evaluation_timeout_seconds": args.eval_timeout,
            "llm_judge_extension": {
                "enabled": False,
                "status": "not_requested",
                "notes": "reserved_for_future_manual_or_llm_judge_extension",
            },
        },
    )

    print(f"[benchmark] dataset_root={dataset_root}")
    print(f"[benchmark] run_root={run_root}")
    print(f"[benchmark] practice={practice_identity['practice_label']}")
    print(f"[benchmark] selected_cases={len(selected)}")

    if args.dry_run:
        return 0

    aggregate_cases: list[dict[str, Any]] = []
    for idx, case in enumerate(selected, start=1):
        print(f"[benchmark] ({idx}/{len(selected)}) running {case.case_id}")
        case_dir = run_root / case.case_id
        case_dir.mkdir(parents=True, exist_ok=True)

        (case_dir / "prompt.txt").write_text(case.prompt + "\n", encoding="utf-8")
        gt_path = Path(case.gt_step_path)
        if gt_path.exists():
            shutil.copy2(gt_path, case_dir / "ground_truth.step")
        _write_json(case_dir / "benchmark_case.json", _build_case_meta(case=case, case_dir=case_dir))

        env = os.environ.copy()
        env["AICAD_TEST_RUNS_ROOT"] = str(run_root)
        env["AICAD_TEST_RUN_DIR"] = str(case_dir)
        env["AICAD_PROBE_REQUIREMENT"] = case.prompt
        env["AICAD_PROBE_SESSION_ID"] = f"bench-{case.case_id}-{uuid4().hex[:8]}"
        if args.reasoning_provider.strip():
            env["LLM_REASONING_PROVIDER"] = args.reasoning_provider.strip()
        if args.reasoning_model.strip():
            env["LLM_REASONING_MODEL"] = args.reasoning_model.strip()
        if args.max_rounds > 0:
            env["AICAD_PROBE_MAX_ROUNDS"] = str(args.max_rounds)
        if args.sandbox_timeout > 0:
            env["AICAD_PROBE_SANDBOX_TIMEOUT"] = str(args.sandbox_timeout)
        env["AICAD_PROBE_ONE_ACTION_PER_ROUND"] = (
            "1" if args.one_action_per_round else "0"
        )

        timed_out = False
        timeout_message = ""
        try:
            result = subprocess.run(
                [str(runner_script), args.run_id],
                cwd=repo_root,
                env=env,
                capture_output=True,
                text=True,
                check=False,
                timeout=case_timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            timeout_message = (
                f"[benchmark] case runner timed out after {case_timeout_seconds}s\n"
            )
            stdout_value = exc.stdout if isinstance(exc.stdout, str) else (exc.stdout or "")
            stderr_value = exc.stderr if isinstance(exc.stderr, str) else (exc.stderr or "")
            result = subprocess.CompletedProcess(
                args=[str(runner_script), args.run_id],
                returncode=124,
                stdout=stdout_value,
                stderr=f"{stderr_value}{timeout_message}",
            )
            print(f"[benchmark] timeout {case.case_id} after {case_timeout_seconds}s")

        stdout_log = result.stdout if isinstance(result.stdout, str) else str(result.stdout or "")
        stderr_log = result.stderr if isinstance(result.stderr, str) else str(result.stderr or "")
        if timed_out and timeout_message and timeout_message not in stderr_log:
            stderr_log = f"{stderr_log}{timeout_message}"

        (case_dir / "benchmark_runner.stdout.log").write_text(
            stdout_log,
            encoding="utf-8",
        )
        (case_dir / "benchmark_runner.stderr.log").write_text(
            stderr_log,
            encoding="utf-8",
        )

        case_summary_payload = _read_json(case_dir / "summary.json")
        runtime_summary = _extract_runtime_summary(case_summary_payload)
        request_payload = _extract_request_payload(case_summary_payload)
        generated_step = _resolve_generated_step_path(
            case_dir=case_dir,
            case_summary_payload=case_summary_payload,
        )
        render_path = _resolve_render_path(case_dir)
        prompt_metrics = _load_prompt_metrics_summary(case_dir)
        trace_summary = _load_trace_summary(case_dir)
        round_digest = _load_round_digest(case_dir)
        _write_round_digest(case_dir, round_digest)
        evaluation_payload: dict[str, Any] = {
            "status": "not_requested" if args.skip_eval else "pending",
            "passed": None,
            "score": None,
            "threshold": args.eval_threshold,
            "summary": None,
        }
        if (
            not args.skip_eval
            and generated_step is not None
            and generated_step.exists()
            and (case_dir / "ground_truth.step").exists()
        ):
            evaluation_dir = case_dir / "evaluation"
            evaluation_payload = evaluate_step_pair_sync(
                generated_step=generated_step,
                ground_truth_step=case_dir / "ground_truth.step",
                output_dir=evaluation_dir,
                threshold=args.eval_threshold,
                timeout_seconds=args.eval_timeout,
            )
            evaluation_payload = {
                **evaluation_payload,
                "threshold": args.eval_threshold,
                "score": (
                    evaluation_payload.get("scores", {}).get("final_score")
                    if isinstance(evaluation_payload.get("scores"), dict)
                    else None
                ),
                "summary": (
                    "; ".join(evaluation_payload.get("difference_notes", [])[:3])
                    if isinstance(evaluation_payload.get("difference_notes"), list)
                    else evaluation_payload.get("error_message")
                ),
            }
        analysis = _diagnose_case(
            case_dir=case_dir,
            return_code=result.returncode,
            timed_out=timed_out,
            case_summary_payload=case_summary_payload,
            evaluation_payload=evaluation_payload,
            generated_step_path=generated_step,
            prompt_metrics=prompt_metrics,
            trace_summary={**trace_summary, **round_digest},
        )

        case_record = {
            "case_id": case.case_id,
            "level": case.level,
            "prompt_field": case.prompt_field,
            "used_canonical_override": case.prompt_field == "canonical_override",
            "canonical_reference": case.canonical_reference,
            "reference_notes": case.reference_notes,
            "practice_label": practice_identity["practice_label"],
            "return_code": result.returncode,
            "timed_out": timed_out,
            "case_timeout_seconds": case_timeout_seconds,
            "generated_step_exists": generated_step is not None and generated_step.exists(),
            "generated_step_path": str(generated_step) if generated_step is not None else None,
            "render_path": str(render_path) if render_path is not None else None,
            "ground_truth_step_path": str((case_dir / "ground_truth.step").resolve()),
            "run_result": case_summary_payload,
            "runtime_summary": runtime_summary,
            "request_payload": request_payload,
            "token_usage": (
                runtime_summary.get("token_usage")
                if isinstance(runtime_summary.get("token_usage"), dict)
                else None
            ),
            "prompt_metrics": prompt_metrics,
            "trace_summary": trace_summary,
            "round_digest": round_digest,
            "evaluation": evaluation_payload,
            "analysis": analysis,
            "evaluation_passed": evaluation_payload.get("passed") is True,
            "validation_complete": bool(runtime_summary.get("validation_complete")),
            "validator_evaluator_disagreement": bool(
                analysis.get("validator_evaluator_disagreement")
            ),
            "baseline_metrics": _build_case_baseline_metrics(
                {
                    "case_id": case.case_id,
                    "analysis": analysis,
                    "runtime_summary": runtime_summary,
                    "token_usage": (
                        runtime_summary.get("token_usage")
                        if isinstance(runtime_summary.get("token_usage"), dict)
                        else None
                    ),
                    "round_digest": round_digest,
                }
            ),
            "llm_judge_extension": {
                "enabled": False,
                "status": "not_requested",
                "score": None,
            },
        }
        _write_case_analysis(case_dir, case_record)
        aggregate_cases.append(case_record)

    success_count = sum(
        1
        for item in aggregate_cases
        if isinstance(item.get("analysis"), dict)
        and item["analysis"].get("status") == "PASS"
    )
    evaluated_cases = [
        item for item in aggregate_cases if isinstance(item.get("evaluation"), dict)
    ]
    eval_attempt_count = sum(
        1
        for item in evaluated_cases
        if item["evaluation"].get("status") not in {"pending", "not_requested", None}
    )
    eval_success_count = sum(
        1
        for item in evaluated_cases
        if item["evaluation"].get("status") == "success"
    )
    eval_error_count = sum(
        1
        for item in evaluated_cases
        if item["evaluation"].get("status") == "error"
    )
    eval_pass_count = sum(
        1
        for item in evaluated_cases
        if item["evaluation"].get("passed") is True
    )
    status_counts = Counter()
    failure_category_counts = Counter()
    end_to_end_status_counts = Counter()
    failure_cluster_counts = Counter()
    recommended_fix_layer_counts = Counter()
    for item in aggregate_cases:
        analysis = item.get("analysis")
        if not isinstance(analysis, dict):
            continue
        status = analysis.get("status")
        failure_category = analysis.get("failure_category")
        end_to_end_status = analysis.get("end_to_end_status")
        failure_cluster = analysis.get("failure_cluster")
        recommended_fix_layer = analysis.get("recommended_fix_layer")
        if isinstance(status, str) and status:
            status_counts[status] += 1
        if isinstance(failure_category, str) and failure_category:
            failure_category_counts[failure_category] += 1
        if isinstance(end_to_end_status, str) and end_to_end_status:
            end_to_end_status_counts[end_to_end_status] += 1
        if isinstance(status, str) and status == "PASS":
            continue
        if isinstance(failure_cluster, str) and failure_cluster:
            failure_cluster_counts[failure_cluster] += 1
        if isinstance(recommended_fix_layer, str) and recommended_fix_layer:
            recommended_fix_layer_counts[recommended_fix_layer] += 1
    token_input_total = 0
    token_output_total = 0
    token_total_total = 0
    token_case_count = 0
    canonical_override_case_count = 0
    for item in aggregate_cases:
        token_usage = item.get("token_usage")
        if not isinstance(token_usage, dict):
            if item.get("used_canonical_override") is True:
                canonical_override_case_count += 1
            continue
        input_tokens = token_usage.get("input_tokens")
        output_tokens = token_usage.get("output_tokens")
        total_tokens = token_usage.get("total_tokens")
        if isinstance(input_tokens, (int, float)):
            token_input_total += int(input_tokens)
        if isinstance(output_tokens, (int, float)):
            token_output_total += int(output_tokens)
        if isinstance(total_tokens, (int, float)):
            token_total_total += int(total_tokens)
            token_case_count += 1
        if item.get("used_canonical_override") is True:
            canonical_override_case_count += 1
    baseline_metrics = _summarize_baseline_metrics(aggregate_cases)
    aggregate_summary = {
        "run_id": args.run_id,
        "run_root": str(run_root),
        "practice_identity": practice_identity,
        "dataset_root": str(dataset_root),
        "case_set_name": args.case_set.strip() or None,
        "total_cases": len(aggregate_cases),
        "successful_case_count": success_count,
        "failed_case_count": len(aggregate_cases) - success_count,
        "generated_step_case_count": sum(1 for item in aggregate_cases if item.get("generated_step_exists")),
        "evaluation_pass_case_count": sum(1 for item in aggregate_cases if item.get("evaluation_passed") is True),
        "runtime_validated_case_count": sum(1 for item in aggregate_cases if item.get("validation_complete") is True),
        "validator_evaluator_disagreement_case_count": sum(
            1 for item in aggregate_cases if item.get("validator_evaluator_disagreement") is True
        ),
        "canonical_override_case_count": canonical_override_case_count,
        "evaluation": {
            "enabled": not args.skip_eval,
            "threshold": args.eval_threshold,
            "attempted_case_count": eval_attempt_count,
            "evaluated_case_count": eval_success_count,
            "error_case_count": eval_error_count,
            "pass_case_count": eval_pass_count,
            "fail_case_count": max(0, eval_success_count - eval_pass_count),
        },
        "status_counts": dict(status_counts),
        "end_to_end_status_counts": dict(end_to_end_status_counts),
        "failure_category_counts": dict(failure_category_counts),
        "failure_cluster_counts": dict(failure_cluster_counts),
        "recommended_fix_layer_counts": dict(recommended_fix_layer_counts),
        "validation_call_total": sum(
            int(
                (
                    item.get("runtime_summary")
                    if isinstance(item.get("runtime_summary"), dict)
                    else {}
                ).get("validation_call_count", 0)
                or 0
            )
            for item in aggregate_cases
        ),
        "read_only_turn_total": sum(
            int(
                (
                    item.get("runtime_summary")
                    if isinstance(item.get("runtime_summary"), dict)
                    else {}
                ).get("read_only_turn_count", 0)
                or 0
            )
            for item in aggregate_cases
        ),
        "token_usage": {
            "input_tokens": token_input_total,
            "output_tokens": token_output_total,
            "total_tokens": token_total_total,
            "cases_with_usage": token_case_count,
        },
        "baseline_metrics": baseline_metrics,
        "cases": aggregate_cases,
        "llm_judge_extension": {
            "enabled": False,
            "status": "not_requested",
            "notes": "reserved_for_future_manual_or_llm_judge_extension",
        },
    }
    _write_json(run_root / "summary.json", aggregate_summary)
    _write_brief_report(
        run_root=run_root,
        case_payloads=aggregate_cases,
        practice_identity=practice_identity,
    )
    _write_run_diagnostics(
        run_root=run_root,
        case_payloads=aggregate_cases,
        practice_identity=practice_identity,
    )

    latest = runs_root / "latest"
    if latest.exists() or latest.is_symlink():
        if latest.is_symlink() or latest.is_file():
            latest.unlink()
        else:
            shutil.rmtree(latest)
    latest.symlink_to(run_root)
    by_practice_root = runs_root / "by_practice"
    by_practice_root.mkdir(parents=True, exist_ok=True)
    practice_link = by_practice_root / f"{args.run_id}__{practice_identity['practice_slug']}"
    if practice_link.exists() or practice_link.is_symlink():
        if practice_link.is_symlink() or practice_link.is_file():
            practice_link.unlink()
        else:
            shutil.rmtree(practice_link)
    practice_link.symlink_to(run_root)
    print(f"[benchmark] latest={latest}")
    print(f"[benchmark] practice_link={practice_link}")
    print(json.dumps(aggregate_summary, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
