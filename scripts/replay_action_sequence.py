from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

from common.config import settings
from sandbox.mcp_runner import McpSandboxRunner


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("request file must contain a JSON object")
    return payload


def _normalize_actions(payload: dict[str, Any]) -> list[dict[str, Any]]:
    raw = payload.get("actions")
    if not isinstance(raw, list):
        return []
    normalized: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        action_type = item.get("action_type")
        action_params = item.get("action_params", {})
        if not isinstance(action_type, str):
            continue
        if not isinstance(action_params, dict):
            action_params = {}
        normalized.append(
            {
                "action_type": action_type,
                "action_params": action_params,
            }
        )
    return normalized


async def _run_async(args: argparse.Namespace) -> int:
    payload = _read_json(args.request_file)
    actions = _normalize_actions(payload)
    if not actions:
        raise ValueError("request contains no valid actions")

    timeout_seconds = payload.get("timeout_seconds", 120)
    if not isinstance(timeout_seconds, (int, float)):
        timeout_seconds = 120

    include_artifact_content = bool(payload.get("include_artifact_content", False))
    clear_session = bool(payload.get("clear_session", True))

    session_id = payload.get("session_id")
    if not isinstance(session_id, str) or not session_id.strip():
        session_id = None

    runner = McpSandboxRunner(
        command=settings.sandbox_mcp_server_command,
        args=settings.sandbox_mcp_server_args_list,
        cwd=settings.sandbox_mcp_server_cwd_effective,
        timeout_buffer_seconds=settings.sandbox_mcp_timeout_buffer_seconds,
    )
    results = await runner.apply_action_sequence(
        actions=actions,
        session_id=session_id,
        timeout=int(timeout_seconds),
        include_artifact_content=include_artifact_content,
        clear_session=clear_session,
    )

    output = {
        "request_file": str(args.request_file.resolve()),
        "action_count": len(actions),
        "session_id": session_id,
        "results": [
            {
                "index": idx,
                "success": item.success,
                "error_message": item.error_message,
                "stderr": item.stderr,
                "step_file": item.step_file,
                "output_files": item.output_files,
                "snapshot_step": (
                    item.snapshot.get("step") if isinstance(item.snapshot, dict) else None
                ),
            }
            for idx, item in enumerate(results, start=1)
        ],
    }
    text = json.dumps(output, ensure_ascii=True, sort_keys=True, indent=2)
    if args.output_file:
        args.output_file.parent.mkdir(parents=True, exist_ok=True)
        args.output_file.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Replay apply_action_sequence request from artifact JSON.",
    )
    parser.add_argument(
        "--request-file",
        type=Path,
        required=True,
        help="Path to actions/round_XX_action_YY_request.json",
    )
    parser.add_argument(
        "--output-file",
        type=Path,
        default=None,
        help="Optional JSON output path for replay result summary.",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    return asyncio.run(_run_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
