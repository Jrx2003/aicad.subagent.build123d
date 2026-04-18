from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from common.config import settings
from sub_agent_runtime.practice_runner import run_practice_suite


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run custom prompt practice cases under practice_runs/<timestamp>/."
    )
    parser.add_argument("--manifest", type=str, default=None)
    parser.add_argument("--runs-root", type=str, default="practice_runs")
    parser.add_argument("--run-id", type=str, default=None)
    parser.add_argument("--seed-ids", type=str, default="")
    parser.add_argument("--variants-per-seed", type=int, default=1)
    parser.add_argument("--max-rounds", type=int, default=8)
    parser.add_argument("--sandbox-timeout", type=int, default=180)
    action_mode = parser.add_mutually_exclusive_group()
    action_mode.add_argument("--one-action-per-round", dest="one_action_per_round", action="store_true")
    action_mode.add_argument("--batch-actions", dest="one_action_per_round", action="store_false")
    parser.set_defaults(one_action_per_round=True)
    parser.add_argument("--force-post-convergence-round", action="store_true")
    parser.add_argument("--provider", type=str, default=None)
    parser.add_argument("--model", type=str, default=None)
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    if args.provider:
        settings.llm_reasoning_provider = args.provider.strip()
    if args.model:
        settings.llm_reasoning_model = args.model.strip()

    payload = asyncio.run(
        run_practice_suite(
            manifest_path=Path(args.manifest).expanduser() if args.manifest else None,
            runs_root=Path(args.runs_root).expanduser(),
            run_id=args.run_id,
            seed_ids=[item.strip() for item in args.seed_ids.split(",") if item.strip()],
            variants_per_seed=max(1, int(args.variants_per_seed or 1)),
            max_rounds=int(args.max_rounds or 8),
            sandbox_timeout=int(args.sandbox_timeout or 180),
            one_action_per_round=bool(args.one_action_per_round),
            force_post_convergence_round=bool(args.force_post_convergence_round),
            dry_run=bool(args.dry_run),
        )
    )
    print(json.dumps(payload, ensure_ascii=True, sort_keys=True))


if __name__ == "__main__":
    main()
