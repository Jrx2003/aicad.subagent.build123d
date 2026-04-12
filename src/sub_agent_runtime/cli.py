import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

from common.config import settings
from sub_agent_runtime.contracts import IterationRequest
from sub_agent_runtime.runner import run_from_env


def _load_requirements(args: argparse.Namespace) -> dict[str, Any]:
    if args.requirements_file:
        payload = json.loads(Path(args.requirements_file).read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            return payload
        raise ValueError("requirements file must be a JSON object")

    if args.requirement_text and args.requirement_text.strip():
        return {"description": args.requirement_text.strip()}

    return {
        "description": "Create a 40x20x10mm plate and add 2mm fillets on all outer edges."
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="aicad-iter-run",
        description="Run decoupled iterative CAD sub-agent with MCP sandbox tools.",
    )
    parser.add_argument("--requirements-file", type=str, default=None)
    parser.add_argument("--requirement-text", type=str, default=None)
    parser.add_argument("--max-rounds", type=int, default=8)
    parser.add_argument("--sandbox-timeout", type=int, default=180)
    action_mode = parser.add_mutually_exclusive_group()
    action_mode.add_argument(
        "--one-action-per-round",
        dest="one_action_per_round",
        action="store_true",
        help="Dynamic loop: re-plan after each applied action (default).",
    )
    action_mode.add_argument(
        "--batch-actions",
        dest="one_action_per_round",
        action="store_false",
        help="Allow executing all planner-returned actions in the same round.",
    )
    parser.set_defaults(one_action_per_round=True)
    parser.add_argument("--force-post-convergence-round", action="store_true")
    parser.add_argument("--session-id", type=str, default=None)
    parser.add_argument("--run-id", type=str, default=None)
    parser.add_argument("--runs-root", type=str, default="test_runs")
    parser.add_argument("--provider", type=str, default=None)
    parser.add_argument("--model", type=str, default=None)
    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    if args.provider:
        settings.llm_reasoning_provider = args.provider.strip()
    if args.model:
        settings.llm_reasoning_model = args.model.strip()

    request = IterationRequest(
        requirements=_load_requirements(args),
        max_rounds=args.max_rounds,
        sandbox_timeout=args.sandbox_timeout,
        one_action_per_round=args.one_action_per_round,
        force_post_convergence_round=args.force_post_convergence_round,
        session_id=args.session_id,
    )

    runs_root = Path(args.runs_root)
    if not runs_root.is_absolute():
        runs_root = Path.cwd() / runs_root

    result = asyncio.run(
        run_from_env(
            request=request,
            runs_root=runs_root,
            run_id=args.run_id,
        )
    )
    payload = {
        "run_dir": result.run_dir,
        "summary": result.summary.model_dump(mode="json"),
    }
    print(json.dumps(payload, ensure_ascii=True, sort_keys=True))


if __name__ == "__main__":
    main()
