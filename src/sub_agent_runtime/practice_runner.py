from __future__ import annotations

from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass, field
import asyncio
import datetime as dt
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import sys
from typing import Any

from common.run_artifacts import ensure_timestamp_run_id
from sub_agent_runtime.contracts import IterationRequest
from sub_agent_runtime.hallucination import normalize_hallucination_summary
from sub_agent_runtime.orchestration.runner import run_from_env


_DEFAULT_MANIFEST_PATH = Path("practice") / "seed_manifest.json"
_DEFAULT_RUNS_ROOT = Path("practice_runs")
_BENCHMARK_HELPERS = None
_STEP_SIMILARITY = None
_ROUND_FILE_RE = re.compile(r"round_(?P<round>[0-9]+)_")
_TOPOLOGY_REF_RE = re.compile(r"^(?:face|edge):(?P<step>[0-9]+):[A-Z]_[A-Za-z0-9_]+$")
_NON_ACTION_HISTORY_TYPES = {"snapshot", "get_history", "clear_session"}
_SKETCH_LANE_CONTEXT_ACTIONS = {
    "add_circle",
    "add_rectangle",
    "add_polygon",
    "add_path",
    "cut_extrude",
    "extrude",
    "trim_solid",
}


@contextmanager
def _temporary_env_overrides(overrides: dict[str, str]) -> Any:
    previous_values: dict[str, str | None] = {}
    for key, value in overrides.items():
        previous_values[key] = os.environ.get(key)
        os.environ[key] = value
    try:
        yield
    finally:
        for key, previous_value in previous_values.items():
            if previous_value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = previous_value


@dataclass(slots=True)
class PracticeSeed:
    seed_id: str
    title: str
    prompt_template: str
    difficulty_band: str
    expected_part_count: int
    target_feature_families: list[str] = field(default_factory=list)
    local_topology_targeting_expected: bool = False
    variation_knobs: dict[str, list[Any]] = field(default_factory=dict)
    notes: str | None = None


@dataclass(slots=True)
class PracticeVariant:
    seed_id: str
    variant_id: str
    title: str
    prompt: str
    difficulty_band: str
    expected_part_count: int
    target_feature_families: list[str] = field(default_factory=list)
    local_topology_targeting_expected: bool = False
    expansion_parameters: dict[str, Any] = field(default_factory=dict)


def default_practice_manifest_path(repo_root: Path | None = None) -> Path:
    base = repo_root or Path.cwd()
    return (base / _DEFAULT_MANIFEST_PATH).resolve()


def load_practice_seed_manifest(path: Path) -> list[PracticeSeed]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError("practice seed manifest must be a JSON array")
    seeds: list[PracticeSeed] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        seeds.append(
            PracticeSeed(
                seed_id=str(item.get("seed_id") or "").strip(),
                title=str(item.get("title") or "").strip(),
                prompt_template=str(item.get("prompt_template") or "").strip(),
                difficulty_band=str(item.get("difficulty_band") or "medium").strip() or "medium",
                expected_part_count=int(item.get("expected_part_count", 1) or 1),
                target_feature_families=[
                    str(family).strip()
                    for family in (item.get("target_feature_families") or [])
                    if isinstance(family, str) and str(family).strip()
                ],
                local_topology_targeting_expected=bool(
                    item.get("local_topology_targeting_expected", False)
                ),
                variation_knobs={
                    str(key): list(value)
                    for key, value in (item.get("variation_knobs") or {}).items()
                    if isinstance(key, str) and key.strip() and isinstance(value, list) and value
                },
                notes=str(item.get("notes") or "").strip() or None,
            )
        )
    return [seed for seed in seeds if seed.seed_id and seed.prompt_template]


def expand_practice_seed_variant(seed: PracticeSeed, *, variant_index: int) -> PracticeVariant:
    expansion_parameters: dict[str, Any] = {}
    for knob_name, values in sorted(seed.variation_knobs.items()):
        if not values:
            continue
        expansion_parameters[knob_name] = _select_variant_value(
            seed_id=seed.seed_id,
            knob_name=knob_name,
            values=values,
            variant_index=variant_index,
        )
    prompt = seed.prompt_template.format(**expansion_parameters)
    variant_slug = _stable_variant_slug(seed.seed_id, variant_index, expansion_parameters)
    return PracticeVariant(
        seed_id=seed.seed_id,
        variant_id=f"{seed.seed_id}_{variant_slug}",
        title=seed.title,
        prompt=prompt,
        difficulty_band=seed.difficulty_band,
        expected_part_count=seed.expected_part_count,
        target_feature_families=list(seed.target_feature_families),
        local_topology_targeting_expected=seed.local_topology_targeting_expected,
        expansion_parameters=expansion_parameters,
    )


def _select_variant_value(
    *,
    seed_id: str,
    knob_name: str,
    values: list[Any],
    variant_index: int,
) -> Any:
    digest = hashlib.sha256(f"{seed_id}:{knob_name}:{variant_index}".encode("utf-8")).hexdigest()
    slot = int(digest[:8], 16) % len(values)
    return values[slot]


def _stable_variant_slug(
    seed_id: str,
    variant_index: int,
    expansion_parameters: dict[str, Any],
) -> str:
    digest = hashlib.sha256(
        json.dumps(
            {
                "seed_id": seed_id,
                "variant_index": variant_index,
                "expansion_parameters": expansion_parameters,
            },
            ensure_ascii=True,
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    return f"v{variant_index:02d}_{digest[:8]}"


def build_practice_variants(
    *,
    manifest_path: Path,
    seed_ids: list[str] | None = None,
    variants_per_seed: int = 1,
) -> list[PracticeVariant]:
    seeds = load_practice_seed_manifest(manifest_path)
    selected_seed_ids = {item.strip() for item in (seed_ids or []) if str(item).strip()}
    selected_seeds = [
        seed for seed in seeds if not selected_seed_ids or seed.seed_id in selected_seed_ids
    ]
    variants: list[PracticeVariant] = []
    for seed in selected_seeds:
        for variant_index in range(variants_per_seed):
            variants.append(expand_practice_seed_variant(seed, variant_index=variant_index))
    return variants


async def run_practice_suite(
    *,
    manifest_path: Path | None = None,
    runs_root: Path | None = None,
    run_id: str | None = None,
    seed_ids: list[str] | None = None,
    variants_per_seed: int = 1,
    max_rounds: int = 8,
    sandbox_timeout: int = 180,
    one_action_per_round: bool = True,
    force_post_convergence_round: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    repo_root = _repo_root()
    resolved_manifest_path = (manifest_path or default_practice_manifest_path(repo_root)).resolve()
    resolved_runs_root = (runs_root or (_repo_root() / _DEFAULT_RUNS_ROOT)).resolve()
    resolved_run_id = ensure_timestamp_run_id(
        run_id or dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    )
    run_root = resolved_runs_root / resolved_run_id
    run_root.mkdir(parents=True, exist_ok=True)

    practice_identity = {
        "run_id": resolved_run_id,
        "runs_root": str(resolved_runs_root),
        "manifest_path": str(resolved_manifest_path),
        "seed_ids": list(seed_ids or []),
        "variants_per_seed": variants_per_seed,
        "practice_label": (
            f"practice:{resolved_run_id} | manifest={resolved_manifest_path.name} "
            f"| variants_per_seed={variants_per_seed}"
        ),
    }
    _write_json(run_root / "practice_manifest.json", practice_identity)

    variants = build_practice_variants(
        manifest_path=resolved_manifest_path,
        seed_ids=seed_ids,
        variants_per_seed=variants_per_seed,
    )
    case_payloads: list[dict[str, Any]] = []
    for variant in variants:
        case_dir = run_root / variant.variant_id
        case_dir.mkdir(parents=True, exist_ok=True)
        _write_practice_case_files(case_dir=case_dir, variant=variant)
        if dry_run:
            practice_analysis = _build_dry_run_analysis(variant)
            _write_json(case_dir / "practice_analysis.json", practice_analysis)
            (case_dir / "practice_analysis.md").write_text(
                _practice_analysis_markdown(practice_analysis),
                encoding="utf-8",
            )
            case_payloads.append(
                {
                    "case_id": variant.variant_id,
                    "variant": _variant_to_dict(variant),
                    "runtime_summary": {},
                    "round_digest": {"available": False, "rounds": [], "domain_kernel_summary": {}},
                    "practice_analysis": practice_analysis,
                }
            )
            continue

        request = IterationRequest(
            requirements={"description": variant.prompt},
            max_rounds=max_rounds,
            sandbox_timeout=sandbox_timeout,
            one_action_per_round=one_action_per_round,
            force_post_convergence_round=force_post_convergence_round,
        )
        with _temporary_env_overrides({"SANDBOX_TYPE": "local-process"}):
            await run_from_env(
                request=request,
                runs_root=run_root,
                run_dir=case_dir,
            )
        summary_payload = _read_json(case_dir / "summary.json")
        runtime_summary = (
            summary_payload.get("summary") if isinstance(summary_payload.get("summary"), dict) else {}
        )
        round_digest = _load_round_digest(case_dir)
        _write_round_digest(case_dir, round_digest)
        latest_validation = _load_latest_validation_payload(case_dir)
        preview_payload = {}
        step_path = _resolve_generated_step_path(case_dir, runtime_summary)
        if step_path is not None:
            preview_payload = await render_generated_previews_async(
                step_path,
                case_dir / "evaluation",
            )
        practice_analysis = _build_practice_analysis(
            case_dir=case_dir,
            variant=variant,
            runtime_summary=runtime_summary,
            round_digest=round_digest,
            latest_validation=latest_validation,
            preview_payload=preview_payload,
        )
        _write_json(case_dir / "practice_analysis.json", practice_analysis)
        (case_dir / "practice_analysis.md").write_text(
            _practice_analysis_markdown(practice_analysis),
            encoding="utf-8",
        )
        case_payloads.append(
            {
                "case_id": variant.variant_id,
                "variant": _variant_to_dict(variant),
                "runtime_summary": runtime_summary,
                "round_digest": round_digest,
                "practice_analysis": practice_analysis,
            }
        )

    run_summary = _build_practice_run_summary(
        practice_identity=practice_identity,
        case_payloads=case_payloads,
    )
    _write_json(run_root / "summary.json", run_summary)
    _write_practice_brief_report(run_root=run_root, case_payloads=case_payloads)
    _write_practice_run_diagnostics(run_root=run_root, case_payloads=case_payloads)
    latest_link = resolved_runs_root / "latest"
    if latest_link.exists() or latest_link.is_symlink():
        latest_link.unlink()
    latest_link.symlink_to(run_root, target_is_directory=True)
    return run_summary


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _variant_to_dict(variant: PracticeVariant) -> dict[str, Any]:
    return {
        "seed_id": variant.seed_id,
        "variant_id": variant.variant_id,
        "title": variant.title,
        "prompt": variant.prompt,
        "difficulty_band": variant.difficulty_band,
        "expected_part_count": variant.expected_part_count,
        "target_feature_families": list(variant.target_feature_families),
        "local_topology_targeting_expected": variant.local_topology_targeting_expected,
        "expansion_parameters": dict(variant.expansion_parameters),
    }


def _write_practice_case_files(*, case_dir: Path, variant: PracticeVariant) -> None:
    (case_dir / "prompt.txt").write_text(variant.prompt + "\n", encoding="utf-8")
    _write_json(case_dir / "practice_case.json", _variant_to_dict(variant))


def _build_dry_run_analysis(variant: PracticeVariant) -> dict[str, Any]:
    return {
        "case_id": variant.variant_id,
        "seed_id": variant.seed_id,
        "status": "dry_run",
        "summary": "Prompt variant materialized without executing the runtime.",
        "variant": _variant_to_dict(variant),
        "hallucination": normalize_hallucination_summary({}),
        "prompt_coverage_evidence": {
            "expected_part_count": variant.expected_part_count,
            "target_feature_families": list(variant.target_feature_families),
        },
        "topology_read_model_usage": {
            "query_counts": {},
            "topology_examples": [],
            "topology_targeting_observed": False,
            "fresh_targeting_action_count": 0,
            "stale_ref_action_count": 0,
            "nonconcrete_ref_action_count": 0,
            "candidate_label_ref_action_count": 0,
            "exact_ref_consumption_rate": 0.0,
        },
    }


def _build_practice_analysis(
    *,
    case_dir: Path,
    variant: PracticeVariant,
    runtime_summary: dict[str, Any],
    round_digest: dict[str, Any],
    latest_validation: dict[str, Any],
    preview_payload: dict[str, Any],
) -> dict[str, Any]:
    hallucination = normalize_hallucination_summary(
        runtime_summary.get("build123d_hallucination")
    )
    read_usage = _summarize_read_model_usage(case_dir=case_dir, round_digest=round_digest)
    prompt_coverage_evidence = {
        "validation_complete": bool(runtime_summary.get("validation_complete")),
        "coverage_confidence": float(latest_validation.get("coverage_confidence", 0.0) or 0.0),
        "insufficient_evidence": bool(latest_validation.get("insufficient_evidence")),
        "validation_summary": str(latest_validation.get("summary") or "").strip() or None,
        "blockers": list(latest_validation.get("blockers") or []),
        "decision_hints": list(latest_validation.get("decision_hints") or []),
        "step_file_exists": bool(runtime_summary.get("step_file_exists")),
        "render_file_exists": bool(runtime_summary.get("render_file_exists")),
        "preview_files": list(preview_payload.get("output_files") or []),
    }
    status = _practice_case_status(runtime_summary)
    issue = (
        str(runtime_summary.get("last_error") or "").strip()
        or str(prompt_coverage_evidence.get("validation_summary") or "").strip()
        or "practice_case_completed_without_explicit_issue"
    )
    return {
        "case_id": variant.variant_id,
        "seed_id": variant.seed_id,
        "status": status,
        "issue": issue,
        "variant": _variant_to_dict(variant),
        "runtime_summary": runtime_summary,
        "hallucination": hallucination,
        "prompt_coverage_evidence": prompt_coverage_evidence,
        "topology_read_model_usage": read_usage,
        "preview_payload": preview_payload,
        "domain_kernel_summary": (
            round_digest.get("domain_kernel_summary")
            if isinstance(round_digest.get("domain_kernel_summary"), dict)
            else {}
        ),
    }


def _summarize_read_model_usage(
    *,
    case_dir: Path,
    round_digest: dict[str, Any],
) -> dict[str, Any]:
    query_counts = Counter()
    topology_examples: list[dict[str, Any]] = []
    candidate_labels: set[str] = set()
    candidate_host_roles: set[str] = set()
    candidate_family_ids: set[str] = set()
    matched_ref_id_count = 0
    candidate_set_count = 0
    topology_windows: list[dict[str, Any]] = []
    queries_dir = case_dir / "queries"
    if queries_dir.exists():
        for path in sorted(queries_dir.glob("*.json")):
            name = path.name
            if "_query_topology" in name:
                query_counts["query_topology"] += 1
                payload = _read_json(path)
                matched_ref_ids = [
                    str(item).strip()
                    for item in (payload.get("matched_ref_ids") or [])
                    if isinstance(item, str) and str(item).strip()
                ]
                matched_ref_id_count += len(matched_ref_ids)
                candidate_sets_raw = (
                    payload.get("candidate_sets")
                    if isinstance(payload.get("candidate_sets"), list)
                    else []
                )
                candidate_set_count += len(candidate_sets_raw)
                example_candidate_sets: list[dict[str, Any]] = []
                for item in candidate_sets_raw[:3]:
                    if not isinstance(item, dict):
                        continue
                    label = str(item.get("label") or "").strip()
                    if label:
                        candidate_labels.add(label)
                    metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
                    host_role = str(metadata.get("host_role") or "").strip()
                    if host_role:
                        candidate_host_roles.add(host_role)
                    semantic_host_roles = [
                        str(role).strip()
                        for role in (metadata.get("semantic_host_roles") or [])
                        if isinstance(role, str) and str(role).strip()
                    ]
                    candidate_host_roles.update(semantic_host_roles)
                    family_ids = [
                        str(family_id).strip()
                        for family_id in (item.get("family_ids") or [])
                        if isinstance(family_id, str) and str(family_id).strip()
                    ]
                    family_id = str(item.get("family_id") or "").strip()
                    if family_id and family_id not in family_ids:
                        family_ids.insert(0, family_id)
                    candidate_family_ids.update(family_ids)
                    example_candidate_sets.append(
                        {
                            "candidate_id": str(item.get("candidate_id") or ""),
                            "label": label,
                            "entity_type": str(item.get("entity_type") or ""),
                            "host_role": host_role or None,
                            "semantic_host_roles": semantic_host_roles[:4],
                            "family_id": family_id or None,
                            "family_ids": family_ids[:4],
                            "preferred_ref_id": str(item.get("preferred_ref_id") or "").strip()
                            or None,
                            "preferred_entity_id": str(
                                item.get("preferred_entity_id") or ""
                            ).strip()
                            or None,
                            "ref_ids": list(item.get("ref_ids") or [])[:8],
                        }
                    )
                topology_ref_ids: list[str] = list(matched_ref_ids)
                for item in candidate_sets_raw:
                    if not isinstance(item, dict):
                        continue
                    for ref_id in item.get("ref_ids") or []:
                        if isinstance(ref_id, str) and ref_id.strip():
                            topology_ref_ids.append(ref_id.strip())
                topology_windows.append(
                    {
                        "round_no": _extract_round_no_from_artifact_name(name),
                        "ref_ids": set(topology_ref_ids),
                        "ref_steps": {
                            parsed_step
                            for ref_id in topology_ref_ids
                            for parsed_step in [_parse_topology_ref_step(ref_id)]
                            if parsed_step is not None
                        },
                    }
                )
                topology_examples.append(
                    {
                        "file": str(path.relative_to(case_dir)),
                        "matched_ref_ids": matched_ref_ids[:8],
                        "candidate_sets": example_candidate_sets,
                    }
                )
            elif "_query_geometry" in name:
                query_counts["query_geometry"] += 1
            elif "_query_feature_probes" in name:
                query_counts["query_feature_probes"] += 1
            elif "_query_kernel_state" in name:
                query_counts["query_kernel_state"] += 1
            elif "_validate_requirement" in name:
                query_counts["validate_requirement"] += 1
            elif "_execute_build123d_probe" in name:
                query_counts["execute_build123d_probe"] += 1
            elif "_render_view" in name:
                query_counts["render_view"] += 1
    local_targeting_action_count = 0
    face_ref_action_count = 0
    edge_ref_action_count = 0
    fresh_targeting_action_count = 0
    stale_ref_action_count = 0
    nonconcrete_ref_action_count = 0
    candidate_label_ref_action_count = 0
    targeting_without_topology_query_count = 0
    local_targeting_examples: list[dict[str, Any]] = []
    actions_dir = case_dir / "actions"
    if actions_dir.exists():
        for path in sorted(actions_dir.glob("*.json")):
            payload = _read_json(path)
            resolved_action = _resolve_effective_local_targeting_action(payload)
            if resolved_action is None:
                continue
            action_type = resolved_action["action_type"]
            face_ref = resolved_action["face_ref"]
            edge_refs = resolved_action["edge_refs"]
            local_targeting_action_count += 1
            if face_ref:
                face_ref_action_count += 1
            if edge_refs:
                edge_ref_action_count += 1
            action_round = _extract_round_no_from_artifact_name(path.name)
            latest_topology_window = _latest_topology_window_before_round(
                topology_windows=topology_windows,
                round_no=action_round,
            )
            latest_ref_ids = (
                latest_topology_window.get("ref_ids")
                if isinstance(latest_topology_window, dict)
                else set()
            )
            latest_ref_steps = (
                latest_topology_window.get("ref_steps")
                if isinstance(latest_topology_window, dict)
                else set()
            )
            latest_ref_step = max(latest_ref_steps) if latest_ref_steps else None
            referenced_items = [face_ref] if face_ref else []
            referenced_items.extend(edge_refs)
            parsed_ref_steps = [_parse_topology_ref_step(ref_id) for ref_id in referenced_items]
            has_candidate_label = any(
                isinstance(ref_id, str) and ref_id.strip().startswith("candidate:")
                for ref_id in referenced_items
            )
            has_nonconcrete_ref = any(step is None for step in parsed_ref_steps)
            has_stale_ref = bool(
                latest_ref_step is not None
                and any(step is not None and step < latest_ref_step for step in parsed_ref_steps)
            )
            is_fresh_targeting = bool(
                latest_ref_ids
                and not has_nonconcrete_ref
                and all(
                    isinstance(ref_id, str) and ref_id in latest_ref_ids
                    for ref_id in referenced_items
                )
            )
            if latest_topology_window is None:
                targeting_without_topology_query_count += 1
            if has_candidate_label:
                candidate_label_ref_action_count += 1
            if has_nonconcrete_ref:
                nonconcrete_ref_action_count += 1
            if has_stale_ref:
                stale_ref_action_count += 1
            if is_fresh_targeting:
                fresh_targeting_action_count += 1
            local_targeting_examples.append(
                {
                    "file": str(path.relative_to(case_dir)),
                    "action_type": action_type,
                    "face_ref": face_ref or None,
                    "edge_refs": edge_refs[:8],
                    "latest_topology_query_round": (
                        latest_topology_window.get("round_no")
                        if isinstance(latest_topology_window, dict)
                        else None
                    ),
                    "fresh_targeting": is_fresh_targeting,
                    "stale_ref_detected": has_stale_ref,
                    "nonconcrete_ref_detected": has_nonconcrete_ref,
                    "candidate_label_ref_detected": has_candidate_label,
                }
            )
    kernel_summary = (
        round_digest.get("domain_kernel_summary")
        if isinstance(round_digest.get("domain_kernel_summary"), dict)
        else {}
    )
    exact_ref_consumption_rate = (
        round(float(fresh_targeting_action_count) / float(local_targeting_action_count), 4)
        if local_targeting_action_count > 0
        else 0.0
    )
    return {
        "query_counts": dict(query_counts),
        "kernel_query_count": int(kernel_summary.get("graph_query_count", 0) or 0),
        "kernel_patch_count": int(kernel_summary.get("graph_patch_count", 0) or 0),
        "matched_ref_id_count": matched_ref_id_count,
        "candidate_set_count": candidate_set_count,
        "candidate_labels": sorted(candidate_labels),
        "candidate_host_roles": sorted(candidate_host_roles),
        "candidate_family_ids": sorted(candidate_family_ids),
        "local_targeting_action_count": local_targeting_action_count,
        "fresh_targeting_action_count": fresh_targeting_action_count,
        "stale_ref_action_count": stale_ref_action_count,
        "nonconcrete_ref_action_count": nonconcrete_ref_action_count,
        "candidate_label_ref_action_count": candidate_label_ref_action_count,
        "targeting_without_topology_query_count": targeting_without_topology_query_count,
        "exact_ref_consumption_rate": exact_ref_consumption_rate,
        "face_ref_action_count": face_ref_action_count,
        "edge_ref_action_count": edge_ref_action_count,
        "local_targeting_examples": local_targeting_examples[:4],
        "topology_examples": topology_examples[:2],
        "topology_targeting_observed": bool(local_targeting_action_count)
        or any(example.get("matched_ref_ids") or example.get("candidate_sets") for example in topology_examples),
        "host_role_targeting_observed": bool(candidate_host_roles),
    }


def _resolve_effective_local_targeting_action(
    payload: dict[str, Any],
) -> dict[str, Any] | None:
    action_history = payload.get("action_history") if isinstance(payload.get("action_history"), list) else []
    history_entries: list[dict[str, Any]] = []
    for item in action_history:
        if not isinstance(item, dict):
            continue
        action_type = str(item.get("action_type") or "").strip().lower()
        if not action_type or action_type in _NON_ACTION_HISTORY_TYPES:
            continue
        action_params = item.get("action_params") if isinstance(item.get("action_params"), dict) else {}
        face_ref = str(action_params.get("face_ref") or "").strip()
        edge_refs = [
            str(ref).strip()
            for ref in (action_params.get("edge_refs") or [])
            if isinstance(ref, str) and str(ref).strip()
        ]
        history_entries.append(
            {
                "action_type": action_type,
                "face_ref": face_ref,
                "edge_refs": edge_refs,
            }
        )
    if not history_entries:
        return None
    current_entry = dict(history_entries[-1])
    if current_entry["face_ref"] or current_entry["edge_refs"]:
        return current_entry
    if current_entry["action_type"] not in _SKETCH_LANE_CONTEXT_ACTIONS:
        return None
    for previous_entry in reversed(history_entries[:-1]):
        if previous_entry["face_ref"] or previous_entry["edge_refs"]:
            current_entry["face_ref"] = previous_entry["face_ref"]
            current_entry["edge_refs"] = list(previous_entry["edge_refs"])
            return current_entry
    return None


def _practice_case_status(runtime_summary: dict[str, Any]) -> str:
    if not runtime_summary:
        return "dry_run"
    if bool(runtime_summary.get("validation_complete")):
        return "complete"
    if str(runtime_summary.get("last_error") or "").strip():
        return "open_with_error"
    if bool(runtime_summary.get("step_file_exists")):
        return "partial_geometry"
    return "no_geometry"


def _resolve_generated_step_path(case_dir: Path, runtime_summary: dict[str, Any]) -> Path | None:
    outputs_dir = case_dir / "outputs"
    step_candidates = []
    step_file = str(runtime_summary.get("step_file") or "").strip()
    if step_file:
        step_candidates.append(outputs_dir / step_file)
    step_candidates.extend(sorted(outputs_dir.glob("*.step")))
    for path in step_candidates:
        if path.exists():
            return path.resolve()
    return None


def _build_practice_run_summary(
    *,
    practice_identity: dict[str, Any],
    case_payloads: list[dict[str, Any]],
) -> dict[str, Any]:
    analyses = [
        item.get("practice_analysis")
        for item in case_payloads
        if isinstance(item.get("practice_analysis"), dict)
    ]
    hallucination_layers = Counter()
    repair_packet_fallback_reason_counts = Counter()
    repair_packet_exposed_count = 0
    repair_packet_supported_count = 0
    repair_packet_compile_success_count = 0
    repair_packet_compile_failure_count = 0
    repair_packet_fallback_count = 0
    execute_build123d_preflight_fail_count = 0
    for analysis in analyses:
        hallucination = (
            analysis.get("hallucination")
            if isinstance(analysis.get("hallucination"), dict)
            else {}
        )
        primary_layer = str(hallucination.get("primary_layer") or "").strip()
        if primary_layer:
            hallucination_layers[primary_layer] += 1
        runtime_summary = (
            analysis.get("runtime_summary")
            if isinstance(analysis.get("runtime_summary"), dict)
            else {}
        )
        repair_packet_exposed_count += int(runtime_summary.get("repair_packet_exposed_count", 0) or 0)
        repair_packet_supported_count += int(
            runtime_summary.get("repair_packet_supported_count", 0) or 0
        )
        repair_packet_compile_success_count += int(
            runtime_summary.get("repair_packet_compile_success_count", 0) or 0
        )
        repair_packet_compile_failure_count += int(
            runtime_summary.get("repair_packet_compile_failure_count", 0) or 0
        )
        repair_packet_fallback_count += int(
            runtime_summary.get("repair_packet_fallback_count", 0) or 0
        )
        execute_build123d_preflight_fail_count += int(
            runtime_summary.get("execute_build123d_preflight_fail_count", 0) or 0
        )
        for reason, count in (runtime_summary.get("repair_packet_fallback_reasons") or {}).items():
            if isinstance(reason, str) and reason.strip():
                repair_packet_fallback_reason_counts[reason.strip()] += int(count or 0)
    return {
        "practice_identity": practice_identity,
        "total_cases": len(case_payloads),
        "complete_case_count": sum(
            1 for analysis in analyses if analysis.get("status") == "complete"
        ),
        "step_case_count": sum(
            1
            for analysis in analyses
            if bool(
                (
                    analysis.get("runtime_summary")
                    if isinstance(analysis.get("runtime_summary"), dict)
                    else {}
                ).get("step_file_exists")
            )
        ),
        "hallucination_event_count": sum(
            int(
                (
                    analysis.get("hallucination")
                    if isinstance(analysis.get("hallucination"), dict)
                    else {}
                ).get("event_count", 0)
                or 0
            )
            for analysis in analyses
        ),
        "hallucination_primary_layer_counts": dict(hallucination_layers),
        "repair_packet_exposed_count": repair_packet_exposed_count,
        "repair_packet_supported_count": repair_packet_supported_count,
        "repair_packet_compile_success_count": repair_packet_compile_success_count,
        "repair_packet_compile_failure_count": repair_packet_compile_failure_count,
        "repair_packet_fallback_count": repair_packet_fallback_count,
        "repair_packet_fallback_reason_counts": dict(repair_packet_fallback_reason_counts),
        "execute_build123d_preflight_fail_count": execute_build123d_preflight_fail_count,
        "fresh_targeting_action_count": sum(
            int(
                (
                    analysis.get("topology_read_model_usage")
                    if isinstance(analysis.get("topology_read_model_usage"), dict)
                    else {}
                ).get("fresh_targeting_action_count", 0)
                or 0
            )
            for analysis in analyses
        ),
        "cases": case_payloads,
    }


def _write_practice_brief_report(*, run_root: Path, case_payloads: list[dict[str, Any]]) -> None:
    rows = []
    for item in case_payloads:
        analysis = (
            item.get("practice_analysis")
            if isinstance(item.get("practice_analysis"), dict)
            else {}
        )
        runtime_summary = (
            analysis.get("runtime_summary")
            if isinstance(analysis.get("runtime_summary"), dict)
            else {}
        )
        read_usage = (
            analysis.get("topology_read_model_usage")
            if isinstance(analysis.get("topology_read_model_usage"), dict)
            else {}
        )
        hallucination = (
            analysis.get("hallucination")
            if isinstance(analysis.get("hallucination"), dict)
            else {}
        )
        query_counts = read_usage.get("query_counts") if isinstance(read_usage.get("query_counts"), dict) else {}
        rows.append(
            {
                "case_id": str(item.get("case_id") or ""),
                "status": str(analysis.get("status") or "unknown"),
                "rounds": int(runtime_summary.get("planner_rounds", 0) or 0),
                "writes": int(runtime_summary.get("executed_action_count", 0) or 0),
                "validation_complete": bool(runtime_summary.get("validation_complete")),
                "first_write_tool": str(runtime_summary.get("first_write_tool") or ""),
                "feature_probe_count": int(runtime_summary.get("feature_probe_count", 0) or 0),
                "query_topology_count": int(query_counts.get("query_topology", 0) or 0),
                "query_geometry_count": int(query_counts.get("query_geometry", 0) or 0),
                "query_kernel_state_count": int(query_counts.get("query_kernel_state", 0) or 0),
                "validate_requirement_count": int(query_counts.get("validate_requirement", 0) or 0),
                "local_targeting_action_count": int(
                    read_usage.get("local_targeting_action_count", 0) or 0
                ),
                "fresh_targeting_action_count": int(
                    read_usage.get("fresh_targeting_action_count", 0) or 0
                ),
                "stale_ref_action_count": int(
                    read_usage.get("stale_ref_action_count", 0) or 0
                ),
                "nonconcrete_ref_action_count": int(
                    read_usage.get("nonconcrete_ref_action_count", 0) or 0
                ),
                "host_role_targeting_observed": bool(
                    read_usage.get("host_role_targeting_observed")
                ),
                "hallucination_events": int(hallucination.get("event_count", 0) or 0),
                "hallucination_weighted_score": float(
                    hallucination.get("weighted_score", 0.0) or 0.0
                ),
                "hallucination_primary_layer": str(hallucination.get("primary_layer") or ""),
                "repair_packet_exposed_count": int(
                    runtime_summary.get("repair_packet_exposed_count", 0) or 0
                ),
                "repair_packet_supported_count": int(
                    runtime_summary.get("repair_packet_supported_count", 0) or 0
                ),
                "repair_packet_compile_success_count": int(
                    runtime_summary.get("repair_packet_compile_success_count", 0) or 0
                ),
                "repair_packet_compile_failure_count": int(
                    runtime_summary.get("repair_packet_compile_failure_count", 0) or 0
                ),
                "repair_packet_fallback_count": int(
                    runtime_summary.get("repair_packet_fallback_count", 0) or 0
                ),
                "execute_build123d_preflight_fail_count": int(
                    runtime_summary.get("execute_build123d_preflight_fail_count", 0) or 0
                ),
                "issue": str(analysis.get("issue") or ""),
            }
        )
    tsv_lines = [
        "case_id\tstatus\tvalidation_complete\trounds\twrites\tfirst_write_tool\tfeature_probe_count\tquery_topology_count\tquery_geometry_count\tquery_kernel_state_count\tvalidate_requirement_count\tlocal_targeting_action_count\tfresh_targeting_action_count\tstale_ref_action_count\tnonconcrete_ref_action_count\thost_role_targeting_observed\trepair_packet_exposed_count\trepair_packet_supported_count\trepair_packet_compile_success_count\trepair_packet_compile_failure_count\trepair_packet_fallback_count\texecute_build123d_preflight_fail_count\thallucination_events\thallucination_weighted_score\thallucination_primary_layer\tissue"
    ]
    for row in rows:
        tsv_lines.append(
            f"{row['case_id']}\t{row['status']}\t{int(row['validation_complete'])}\t{row['rounds']}\t{row['writes']}\t{row['first_write_tool']}\t{row['feature_probe_count']}\t{row['query_topology_count']}\t{row['query_geometry_count']}\t{row['query_kernel_state_count']}\t{row['validate_requirement_count']}\t{row['local_targeting_action_count']}\t{row['fresh_targeting_action_count']}\t{row['stale_ref_action_count']}\t{row['nonconcrete_ref_action_count']}\t{int(row['host_role_targeting_observed'])}\t{row['repair_packet_exposed_count']}\t{row['repair_packet_supported_count']}\t{row['repair_packet_compile_success_count']}\t{row['repair_packet_compile_failure_count']}\t{row['repair_packet_fallback_count']}\t{row['execute_build123d_preflight_fail_count']}\t{row['hallucination_events']}\t{row['hallucination_weighted_score']}\t{row['hallucination_primary_layer']}\t{row['issue']}"
        )
    (run_root / "brief_report.tsv").write_text("\n".join(tsv_lines) + "\n", encoding="utf-8")

    repair_packet_fallback_reason_counts = Counter()
    for item in case_payloads:
        analysis = (
            item.get("practice_analysis")
            if isinstance(item.get("practice_analysis"), dict)
            else {}
        )
        runtime_summary = (
            analysis.get("runtime_summary")
            if isinstance(analysis.get("runtime_summary"), dict)
            else {}
        )
        for reason, count in (runtime_summary.get("repair_packet_fallback_reasons") or {}).items():
            if isinstance(reason, str) and reason.strip():
                repair_packet_fallback_reason_counts[reason.strip()] += int(count or 0)

    md_lines = [
        "# Practice Brief Report",
        "",
        f"- total_cases: {len(rows)}",
        f"- complete_cases: {sum(1 for row in rows if row['validation_complete'])}",
        f"- topology_query_cases: {sum(1 for row in rows if row['query_topology_count'] > 0)}",
        f"- repair_packet_exposed_count: {sum(row['repair_packet_exposed_count'] for row in rows)}",
        f"- repair_packet_supported_count: {sum(row['repair_packet_supported_count'] for row in rows)}",
        f"- repair_packet_compile_success_count: {sum(row['repair_packet_compile_success_count'] for row in rows)}",
        f"- repair_packet_compile_failure_count: {sum(row['repair_packet_compile_failure_count'] for row in rows)}",
        f"- repair_packet_fallback_count: {sum(row['repair_packet_fallback_count'] for row in rows)}",
        f"- repair_packet_fallback_reason_counts: {dict(repair_packet_fallback_reason_counts)}",
        f"- execute_build123d_preflight_fail_count: {sum(row['execute_build123d_preflight_fail_count'] for row in rows)}",
        f"- hallucination_events: {sum(row['hallucination_events'] for row in rows)}",
        "",
        "| case_id | status | validation | rounds | writes | first_write_tool | query_topology | query_geometry | query_kernel | validate | local_targeting | fresh_targeting | stale_ref | nonconcrete_ref | host_role | packet_exposed | packet_supported | packet_compile_ok | packet_compile_fail | packet_fallback | preflight_fail | hallucination_events | hallucination_layer | issue |",
        "| --- | --- | --- | ---: | ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- |",
    ]
    for row in rows:
        md_lines.append(
            f"| {row['case_id']} | {row['status']} | {int(row['validation_complete'])} | {row['rounds']} | {row['writes']} | {row['first_write_tool'] or '-'} | {row['query_topology_count']} | {row['query_geometry_count']} | {row['query_kernel_state_count']} | {row['validate_requirement_count']} | {row['local_targeting_action_count']} | {row['fresh_targeting_action_count']} | {row['stale_ref_action_count']} | {row['nonconcrete_ref_action_count']} | {int(row['host_role_targeting_observed'])} | {row['repair_packet_exposed_count']} | {row['repair_packet_supported_count']} | {row['repair_packet_compile_success_count']} | {row['repair_packet_compile_failure_count']} | {row['repair_packet_fallback_count']} | {row['execute_build123d_preflight_fail_count']} | {row['hallucination_events']} | {row['hallucination_primary_layer'] or '-'} | {row['issue'].replace('|', '/')} |"
        )
    (run_root / "brief_report.md").write_text("\n".join(md_lines) + "\n", encoding="utf-8")


def _write_practice_run_diagnostics(*, run_root: Path, case_payloads: list[dict[str, Any]]) -> None:
    analyses = [
        item.get("practice_analysis")
        for item in case_payloads
        if isinstance(item.get("practice_analysis"), dict)
    ]
    repair_packet_fallback_reason_counts = Counter()
    repair_packet_exposed_count = 0
    repair_packet_supported_count = 0
    repair_packet_compile_success_count = 0
    repair_packet_compile_failure_count = 0
    repair_packet_fallback_count = 0
    execute_build123d_preflight_fail_count = 0
    for analysis in analyses:
        runtime_summary = (
            analysis.get("runtime_summary")
            if isinstance(analysis.get("runtime_summary"), dict)
            else {}
        )
        repair_packet_exposed_count += int(runtime_summary.get("repair_packet_exposed_count", 0) or 0)
        repair_packet_supported_count += int(
            runtime_summary.get("repair_packet_supported_count", 0) or 0
        )
        repair_packet_compile_success_count += int(
            runtime_summary.get("repair_packet_compile_success_count", 0) or 0
        )
        repair_packet_compile_failure_count += int(
            runtime_summary.get("repair_packet_compile_failure_count", 0) or 0
        )
        repair_packet_fallback_count += int(
            runtime_summary.get("repair_packet_fallback_count", 0) or 0
        )
        execute_build123d_preflight_fail_count += int(
            runtime_summary.get("execute_build123d_preflight_fail_count", 0) or 0
        )
        for reason, count in (runtime_summary.get("repair_packet_fallback_reasons") or {}).items():
            if isinstance(reason, str) and reason.strip():
                repair_packet_fallback_reason_counts[reason.strip()] += int(count or 0)
    top_hallucination_cases = sorted(
        analyses,
        key=lambda item: float(
            (
                item.get("hallucination")
                if isinstance(item.get("hallucination"), dict)
                else {}
            ).get("weighted_score", 0.0)
            or 0.0
        ),
        reverse=True,
    )[:5]
    payload = {
        "total_cases": len(analyses),
        "complete_cases": [
            analysis.get("case_id") for analysis in analyses if analysis.get("status") == "complete"
        ],
        "topology_query_cases": [
            analysis.get("case_id")
            for analysis in analyses
            if int(
                (
                    analysis.get("topology_read_model_usage")
                    if isinstance(analysis.get("topology_read_model_usage"), dict)
                    else {}
                )
                .get("query_counts", {})
                .get("query_topology", 0)
                or 0
            )
            > 0
        ],
        "local_targeting_cases": [
            analysis.get("case_id")
            for analysis in analyses
            if int(
                (
                    analysis.get("topology_read_model_usage")
                    if isinstance(analysis.get("topology_read_model_usage"), dict)
                    else {}
                ).get("local_targeting_action_count", 0)
                or 0
            )
            > 0
        ],
        "stale_targeting_cases": [
            analysis.get("case_id")
            for analysis in analyses
            if int(
                (
                    analysis.get("topology_read_model_usage")
                    if isinstance(analysis.get("topology_read_model_usage"), dict)
                    else {}
                ).get("stale_ref_action_count", 0)
                or 0
            )
            > 0
        ],
        "host_role_targeting_cases": [
            analysis.get("case_id")
            for analysis in analyses
            if bool(
                (
                    analysis.get("topology_read_model_usage")
                    if isinstance(analysis.get("topology_read_model_usage"), dict)
                    else {}
                ).get("host_role_targeting_observed")
            )
        ],
        "repair_packet_exposed_count": repair_packet_exposed_count,
        "repair_packet_supported_count": repair_packet_supported_count,
        "repair_packet_compile_success_count": repair_packet_compile_success_count,
        "repair_packet_compile_failure_count": repair_packet_compile_failure_count,
        "repair_packet_fallback_count": repair_packet_fallback_count,
        "repair_packet_fallback_reason_counts": dict(repair_packet_fallback_reason_counts),
        "execute_build123d_preflight_fail_count": execute_build123d_preflight_fail_count,
        "top_hallucination_cases": top_hallucination_cases,
    }
    _write_json(run_root / "run_diagnostics.json", payload)
    md_lines = [
        "# Practice Diagnostics",
        "",
        f"- total_cases: {payload['total_cases']}",
        f"- complete_cases: {payload['complete_cases']}",
        f"- topology_query_cases: {payload['topology_query_cases']}",
        f"- local_targeting_cases: {payload['local_targeting_cases']}",
        f"- stale_targeting_cases: {payload['stale_targeting_cases']}",
        f"- host_role_targeting_cases: {payload['host_role_targeting_cases']}",
        f"- repair_packet_exposed_count: {payload['repair_packet_exposed_count']}",
        f"- repair_packet_supported_count: {payload['repair_packet_supported_count']}",
        f"- repair_packet_compile_success_count: {payload['repair_packet_compile_success_count']}",
        f"- repair_packet_compile_failure_count: {payload['repair_packet_compile_failure_count']}",
        f"- repair_packet_fallback_count: {payload['repair_packet_fallback_count']}",
        f"- repair_packet_fallback_reason_counts: {payload['repair_packet_fallback_reason_counts']}",
        f"- execute_build123d_preflight_fail_count: {payload['execute_build123d_preflight_fail_count']}",
        "",
        "## Top Hallucination Cases",
        "",
    ]
    for analysis in top_hallucination_cases:
        hallucination = (
            analysis.get("hallucination")
            if isinstance(analysis.get("hallucination"), dict)
            else {}
        )
        md_lines.append(
            f"- {analysis.get('case_id')}: weighted_score={hallucination.get('weighted_score')}, primary_layer={hallucination.get('primary_layer')}, issue={analysis.get('issue')}"
        )
    (run_root / "run_diagnostics.md").write_text("\n".join(md_lines) + "\n", encoding="utf-8")


def _practice_analysis_markdown(payload: dict[str, Any]) -> str:
    hallucination = payload.get("hallucination") if isinstance(payload.get("hallucination"), dict) else {}
    coverage = (
        payload.get("prompt_coverage_evidence")
        if isinstance(payload.get("prompt_coverage_evidence"), dict)
        else {}
    )
    read_usage = (
        payload.get("topology_read_model_usage")
        if isinstance(payload.get("topology_read_model_usage"), dict)
        else {}
    )
    lines = [
        f"# Practice Analysis: {payload.get('case_id')}",
        "",
        f"- status: {payload.get('status')}",
        f"- issue: {payload.get('issue')}",
        f"- hallucination: events={hallucination.get('event_count')}, weighted_score={hallucination.get('weighted_score')}, primary_layer={hallucination.get('primary_layer')}",
        f"- validation_complete: {coverage.get('validation_complete')}",
        f"- coverage_confidence: {coverage.get('coverage_confidence')}",
        f"- insufficient_evidence: {coverage.get('insufficient_evidence')}",
        f"- blockers: {coverage.get('blockers')}",
        f"- preview_files: {coverage.get('preview_files')}",
        f"- query_counts: {read_usage.get('query_counts')}",
        f"- topology_targeting_observed: {read_usage.get('topology_targeting_observed')}",
        f"- host_role_targeting_observed: {read_usage.get('host_role_targeting_observed')}",
        f"- candidate_host_roles: {read_usage.get('candidate_host_roles')}",
        f"- local_targeting_action_count: {read_usage.get('local_targeting_action_count')}",
        f"- fresh_targeting_action_count: {read_usage.get('fresh_targeting_action_count')}",
        f"- stale_ref_action_count: {read_usage.get('stale_ref_action_count')}",
        f"- nonconcrete_ref_action_count: {read_usage.get('nonconcrete_ref_action_count')}",
        f"- exact_ref_consumption_rate: {read_usage.get('exact_ref_consumption_rate')}",
        f"- local_targeting_examples: {read_usage.get('local_targeting_examples')}",
        f"- topology_examples: {read_usage.get('topology_examples')}",
        "",
    ]
    return "\n".join(lines)


def _extract_round_no_from_artifact_name(name: str) -> int | None:
    match = _ROUND_FILE_RE.search(str(name))
    if match is None:
        return None
    try:
        return int(match.group("round"))
    except (TypeError, ValueError):
        return None


def _parse_topology_ref_step(ref_id: str) -> int | None:
    if not isinstance(ref_id, str):
        return None
    match = _TOPOLOGY_REF_RE.fullmatch(ref_id.strip())
    if match is None:
        return None
    try:
        return int(match.group("step"))
    except (TypeError, ValueError):
        return None


def _latest_topology_window_before_round(
    *,
    topology_windows: list[dict[str, Any]],
    round_no: int | None,
) -> dict[str, Any] | None:
    eligible = [
        item
        for item in topology_windows
        if isinstance(item, dict)
        and (
            round_no is None
            or item.get("round_no") is None
            or int(item.get("round_no")) <= round_no
        )
    ]
    if not eligible:
        return None
    return eligible[-1]


def _load_round_digest(case_dir: Path) -> dict[str, Any]:
    module = _load_benchmark_helpers()
    return module._load_round_digest(case_dir)


def _write_round_digest(case_dir: Path, round_digest: dict[str, Any]) -> None:
    module = _load_benchmark_helpers()
    module._write_round_digest(case_dir, round_digest)


def _load_latest_validation_payload(case_dir: Path) -> dict[str, Any]:
    queries_dir = case_dir / "queries"
    if not queries_dir.exists():
        return {}
    candidates = sorted(queries_dir.glob("*validate_requirement*.json"))
    if not candidates:
        return {}
    return _read_json(candidates[-1])


async def render_generated_previews_async(
    step_path: Path,
    evaluation_dir: Path,
) -> dict[str, Any]:
    evaluation_dir.mkdir(parents=True, exist_ok=True)
    module = _load_step_similarity_module()
    payload = await module._evaluate_step_pair_async(
        generated_step=step_path,
        ground_truth_step=step_path,
        output_dir=evaluation_dir,
        threshold=1.0,
        timeout_seconds=180,
    )
    for path in evaluation_dir.glob("ground_truth_preview_*.png"):
        path.unlink(missing_ok=True)
    for filename in ("benchmark_eval.json", "benchmark_eval_summary.txt"):
        (evaluation_dir / filename).unlink(missing_ok=True)
    output_files = [
        filename
        for filename in (payload.get("output_files") or [])
        if isinstance(filename, str) and filename.startswith("generated_preview_")
    ]
    preview_payload = {
        "status": str(payload.get("status") or "unknown"),
        "preview_views": list(payload.get("preview_views") or []),
        "generated_stats": payload.get("generated_stats") or {},
        "output_files": output_files,
    }
    _write_json(evaluation_dir / "practice_preview.json", preview_payload)
    return preview_payload
def _load_benchmark_helpers():
    global _BENCHMARK_HELPERS
    if _BENCHMARK_HELPERS is not None:
        return _BENCHMARK_HELPERS
    script_path = _repo_root() / "benchmark" / "run_prompt_benchmark.py"
    benchmark_dir = script_path.parent
    benchmark_dir_str = str(benchmark_dir)
    if benchmark_dir_str not in sys.path:
        sys.path.insert(0, benchmark_dir_str)
    spec = importlib.util.spec_from_file_location("practice_benchmark_helpers", script_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("failed to load benchmark helpers")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    _BENCHMARK_HELPERS = module
    return module


def _load_step_similarity_module():
    global _STEP_SIMILARITY
    if _STEP_SIMILARITY is not None:
        return _STEP_SIMILARITY
    script_path = _repo_root() / "benchmark" / "step_similarity_eval.py"
    benchmark_dir = script_path.parent
    benchmark_dir_str = str(benchmark_dir)
    if benchmark_dir_str not in sys.path:
        sys.path.insert(0, benchmark_dir_str)
    spec = importlib.util.spec_from_file_location("practice_step_similarity_eval", script_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("failed to load step similarity module")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    _STEP_SIMILARITY = module
    return module


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=True, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
