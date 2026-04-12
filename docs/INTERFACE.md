# Upstream Interface

## Python contract

Use these classes for integration with upstream orchestration:

1. `sub_agent_runtime.contracts.IterationRequest`
2. `sub_agent_runtime.contracts.IterationRunResult`
3. `sub_agent_runtime.runner.IterativeSubAgentRunner`

Example:

```python
import asyncio
from pathlib import Path

from sub_agent_runtime.contracts import IterationRequest
from sub_agent_runtime.runner import IterativeSubAgentRunner


async def run_once() -> None:
    runner = IterativeSubAgentRunner()
    run_dir = runner.create_run_dir(Path("test_runs"))
    result = await runner.run(
        request=IterationRequest(
            requirements={
                "description": "Create a 40x20x10mm plate with 2mm fillet on outer edges"
            },
            max_rounds=4,
            one_action_per_round=True,
        ),
        run_dir=run_dir,
    )
    print(result.summary.model_dump())


asyncio.run(run_once())
```

## Request expectations from main_agent

`requirements` should already be normalized and coherent. Preferred fields:

1. `description` (required, concise but complete)
2. `dimensions` (optional object, numeric mm values)
3. `features` (optional list, e.g. `hole`, `fillet`, `chamfer`)
4. `constraints` (optional list/object for manufacturing rules)

This repo assumes requirement quality is at main-agent production standard.
