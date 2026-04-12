# Build123d Runtime Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the repository's CadQuery-centered code-first contract with a strict Build123d-centered runtime, then verify the result with focused tests and a full `L1` benchmark run.

**Architecture:** Keep the current runtime structure, but rewire its code-first path end-to-end: the sandbox executes Build123d scripts, MCP/runtime tool names become `execute_build123d*`, and planner guidance plus preflight lint move from chain-heavy CadQuery advice to builder-first Build123d recipes. Benchmark/reporting schemas stay structurally stable, but all tool identity values, diagnostics, and payload names become Build123d-native.

**Tech Stack:** Python 3.12+, `build123d`, Pydantic v2, MCP stdio server/client, pytest, `uv`, benchmark shell runner, LLM credentials from `.env`

---

### Task 1: Move the sandbox runtime from CadQuery to Build123d

**Files:**
- Modify: `pyproject.toml`
- Modify: `src/common/config.py`
- Modify: `src/sandbox/docker_runner.py`
- Test: `tests/unit/sandbox/test_docker_runner.py`

- [ ] **Step 1: Rewrite the sandbox tests so they fail against the current CadQuery prelude**

```python
def test_build_runtime_code_bootstraps_build123d_and_exports_part() -> None:
    code = _build_runtime_code(
        "with BuildPart() as bp:\n"
        "    Box(1, 1, 1)\n"
        "result = bp.part"
    )

    assert "from build123d import *" in code
    assert "def __aicad_resolve_export_part():" in code
    assert "export_step(__aicad_export_part, '/output/model.step')" in code


def test_execute_sync_uses_build123d_runtime_image_name(monkeypatch) -> None:
    runner = DockerSandboxRunner.__new__(DockerSandboxRunner)
    runner._image = "build123d-runtime:latest"
    runner._memory_limit = "512m"
    runner._cpu_quota = 100000
    runner._client = _FakeClient()
    monkeypatch.setattr(runner, "_copy_to_container", lambda container, src_path, dest_path: None)
    monkeypatch.setattr(
        runner,
        "_copy_from_container",
        lambda container, src_path: (_ for _ in ()).throw(RuntimeError("missing")),
    )

    result = runner._execute_sync(
        "with BuildPart() as bp:\n"
        "    Box(1, 1, 1)\n"
        "result = bp.part",
        "build123d-test-entrypoint",
        timeout=5,
    )

    created_kwargs = runner._client.containers.created_kwargs
    assert created_kwargs is not None
    assert created_kwargs["image"] == "build123d-runtime:latest"
    assert result.success is True
```

- [ ] **Step 2: Run the sandbox test file and confirm it fails for the expected reasons**

Run: `pytest tests/unit/sandbox/test_docker_runner.py -q`

Expected: FAIL because the runtime prelude still imports CadQuery, the image default is still `cadquery-runtime:latest`, and the export epilogue still expects CadQuery solids.

- [ ] **Step 3: Add the Build123d dependency and switch the default sandbox image name**

```toml
[project]
dependencies = [
  "pydantic>=2.0",
  "pydantic-settings>=2.0",
  "python-dotenv>=1.0.0",
  "structlog>=24.0.0",
  "mcp>=1.0.0",
  "docker>=7.0.0",
  "httpx>=0.27.0",
  "langchain>=0.3.0",
  "langchain-core>=0.3.0",
  "langchain-openai>=0.2.0",
  "langchain-anthropic>=0.3.0",
  "langchain-google-genai>=2.0.0",
  "openai>=1.0.0",
  "anthropic>=0.18.0",
  "build123d>=0.8.0",
]
```

```python
# src/common/config.py
sandbox_image: str = "build123d-runtime:latest"
```

- [ ] **Step 4: Replace the CadQuery prelude/epilogue with a Build123d runtime prelude**

```python
def _build_runtime_code(user_code: str) -> str:
    prelude = (
        "from build123d import *\n"
        "from pathlib import Path\n"
        "__aicad_last_result = None\n"
        "def show_object(obj, *args, **kwargs):\n"
        "    global __aicad_last_result\n"
        "    __aicad_last_result = obj\n"
        "def debug(*args, **kwargs):\n"
        "    return None\n"
    )
    epilogue = (
        "\n"
        "def __aicad_resolve_export_part():\n"
        "    candidates = []\n"
        "    if 'result' in globals():\n"
        "        candidates.append(result)\n"
        "    for name in ('part', 'model', '__aicad_last_result'):\n"
        "        if name in globals():\n"
        "            candidates.append(globals()[name])\n"
        "    for candidate in candidates:\n"
        "        if hasattr(candidate, 'part') and isinstance(candidate.part, Part):\n"
        "            return candidate.part\n"
        "        if isinstance(candidate, Part):\n"
        "            return candidate\n"
        "    return None\n"
        "__aicad_export_part = __aicad_resolve_export_part()\n"
        "if __aicad_export_part is not None and len(__aicad_export_part.solids()) > 0:\n"
        "    Path('/output').mkdir(parents=True, exist_ok=True)\n"
        "    export_step(__aicad_export_part, '/output/model.step')\n"
    )
    return f\"{prelude}\\n{user_code.rstrip()}\\n{epilogue}\"
```

- [ ] **Step 5: Run the sandbox tests again**

Run: `pytest tests/unit/sandbox/test_docker_runner.py -q`

Expected: PASS with Build123d imports, `export_step`, and the new image default.

- [ ] **Step 6: Commit the sandbox slice**

```bash
git add pyproject.toml src/common/config.py src/sandbox/docker_runner.py tests/unit/sandbox/test_docker_runner.py
git commit -m "feat: switch sandbox runtime to build123d"
```

### Task 2: Rename the MCP code-execution contract to `execute_build123d`

**Files:**
- Modify: `src/sandbox/mcp_runner.py`
- Modify: `src/sandbox_mcp_server/contracts.py`
- Modify: `src/sandbox_mcp_server/server.py`
- Modify: `src/sandbox_mcp_server/registry.py`
- Modify: `src/sandbox_mcp_server/evidence_builder.py`
- Test: `tests/unit/sandbox/test_mcp_runner.py`
- Test: `tests/unit/sandbox_mcp_server/test_execute_cadquery_session_bridge.py`

- [ ] **Step 1: Rewrite MCP and session-bridge tests to the Build123d names**

```python
async def test_mcp_sandbox_runner_execute_build123d_probe_maps_artifacts() -> None:
    async def _fake_call_named_tool(*, tool_name: str, arguments: dict[str, object]) -> dict[str, object]:
        assert tool_name == "execute_build123d_probe"
        assert arguments["session_id"] == "session-1"
        return {
            "success": True,
            "stdout": "",
            "stderr": "",
            "output_files": ["model.step"],
            "artifacts": [],
            "session_state_persisted": False,
            "probe_summary": {"families": ["axisymmetric_profile"]},
        }

    runner = McpSandboxRunner()
    runner._call_named_tool = _fake_call_named_tool  # type: ignore[method-assign]

    result = await runner.execute_build123d_probe(
        code="with BuildPart() as bp:\n"
        "    Box(1, 1, 1)\n"
        "result = bp.part",
        session_id="session-1",
    )

    assert result.success is True


def test_server_builds_build123d_tools() -> None:
    assert build_execute_build123d_tool().name == "execute_build123d"
    assert build_execute_build123d_probe_tool().name == "execute_build123d_probe"
```

- [ ] **Step 2: Run the MCP and session-bridge suites to confirm the old tool names break the tests**

Run: `pytest tests/unit/sandbox/test_mcp_runner.py tests/unit/sandbox_mcp_server/test_execute_cadquery_session_bridge.py -q`

Expected: FAIL because `execute_cadquery*`, `ExecuteCadQuery*`, and CadQuery wording still dominate the contract.

- [ ] **Step 3: Rename the MCP runner defaults, result dataclasses, and probe method**

```python
DEFAULT_MCP_TOOL_NAME = "execute_build123d"
DEFAULT_EXECUTE_BUILD123D_PROBE_TOOL_NAME = "execute_build123d_probe"


@dataclass
class Build123dProbeResult:
    success: bool
    stdout: str
    stderr: str
    error_message: str | None
    output_files: list[str]
    output_file_contents: dict[str, bytes]
    session_id: str | None
    step: int | None
    step_file: str | None
    probe_summary: dict[str, Any]
    session_state_persisted: bool


async def execute_build123d_probe(
    self,
    code: str,
    session_id: str | None = None,
    timeout: int = 60,
    include_artifact_content: bool = True,
) -> Build123dProbeResult:
    arguments = {
        "code": code,
        "timeout_seconds": timeout,
        "include_artifact_content": include_artifact_content,
    }
    if session_id is not None:
        arguments["session_id"] = session_id
    call_result = await self._call_named_tool(
        tool_name=DEFAULT_EXECUTE_BUILD123D_PROBE_TOOL_NAME,
        arguments=arguments,
    )
    return self._map_execute_build123d_probe_result(call_result)
```

- [ ] **Step 4: Rename the Pydantic contract models and descriptions**

```python
class ExecuteBuild123dInput(BaseModel):
    """Input contract for execute_build123d tool."""

    model_config = ConfigDict(extra="forbid")

    code: str = Field(
        min_length=1,
        description="Build123d Python code. Must assign the final solid-bearing Part to `result`.",
    )
    timeout_seconds: int = Field(default=120, ge=1, le=600)
    include_artifact_content: bool = Field(default=True)
    requirement_text: str | None = Field(default=None)
    session_id: str | None = Field(default=None)


class ExecuteBuild123dOutput(BaseModel):
    """Structured output contract for execute_build123d tool."""

    model_config = ConfigDict(extra="forbid")

    success: bool = Field(description="Whether Build123d sandbox execution succeeded.")
    stdout: str = Field(default="", description="Captured standard output.")
    stderr: str = Field(default="", description="Captured standard error.")
    session_state_persisted: bool = Field(default=False)
```

- [ ] **Step 5: Rewire the server and tool registry to only expose Build123d code tools**

```python
class SandboxTools(str, Enum):
    EXECUTE_BUILD123D = "execute_build123d"
    EXECUTE_BUILD123D_PROBE = "execute_build123d_probe"
    APPLY_CAD_ACTION = "apply_cad_action"
    GET_HISTORY = "get_history"
    QUERY_SNAPSHOT = "query_snapshot"
    QUERY_SKETCH = "query_sketch"
    QUERY_GEOMETRY = "query_geometry"
    QUERY_TOPOLOGY = "query_topology"
    QUERY_FEATURE_PROBES = "query_feature_probes"
    RENDER_VIEW = "render_view"
    VALIDATE_REQUIREMENT = "validate_requirement"


def build_execute_build123d_tool() -> Tool:
    definition = get_tool_definition(SandboxTools.EXECUTE_BUILD123D)
    return Tool(
        name=SandboxTools.EXECUTE_BUILD123D,
        description=definition.description if definition is not None else "",
        inputSchema=ExecuteBuild123dInput.model_json_schema(),
        outputSchema=ExecuteBuild123dOutput.model_json_schema(),
    )
```

```python
ToolDefinition(
    name="execute_build123d",
    description="Execute Build123d code in the sandbox and return execution logs plus generated artifacts.",
    input_model=ExecuteBuild123dInput,
    output_model=ExecuteBuild123dOutput,
    prompt_schema_lines=(
        "- code: Build123d Python script that assigns the final Part to `result`",
        "- timeout_seconds/include_artifact_content",
    ),
    exposure_bundles=("code_execution",),
)
```

- [ ] **Step 6: Run the MCP and session-bridge tests again**

Run: `pytest tests/unit/sandbox/test_mcp_runner.py tests/unit/sandbox_mcp_server/test_execute_cadquery_session_bridge.py -q`

Expected: PASS with `execute_build123d` and `execute_build123d_probe` exposed end-to-end. Keeping an older test filename is acceptable for this slice; the imported types and assertions must still be Build123d-native.

- [ ] **Step 7: Commit the MCP contract slice**

```bash
git add src/sandbox/mcp_runner.py src/sandbox_mcp_server/contracts.py src/sandbox_mcp_server/server.py src/sandbox_mcp_server/registry.py src/sandbox_mcp_server/evidence_builder.py tests/unit/sandbox/test_mcp_runner.py tests/unit/sandbox_mcp_server/test_execute_cadquery_session_bridge.py
git commit -m "feat: rename mcp code tools to build123d"
```

### Task 3: Rebuild runtime guidance, failure taxonomy, and preflight lint around Build123d

**Files:**
- Modify: `src/sub_agent_runtime/tool_runtime.py`
- Modify: `src/sub_agent_runtime/skill_pack.py`
- Modify: `src/sub_agent_runtime/context_manager.py`
- Modify: `src/sub_agent_runtime/feature_graph.py`
- Modify: `src/common/blocker_taxonomy.py`
- Modify: `src/sub_agent_runtime/agent_loop_v2.py`
- Test: `tests/unit/sub_agent_runtime/test_v2_runtime.py`
- Test: `tests/unit/sub_agent_runtime/test_probe_first_semantic_refresh.py`
- Test: `tests/unit/common/test_blocker_taxonomy.py`

- [ ] **Step 1: Rewrite runtime tests to assert Build123d-first guidance**

```python
def test_execute_build123d_tool_guidance_keeps_builder_first_default() -> None:
    specs = build_default_tool_specs()
    guidance = specs["execute_build123d"].follow_up_recommendation or ""

    assert "BuildPart" in guidance
    assert "BuildSketch" in guidance
    assert "BuildLine" in guidance
    assert "Plane, Axis, Pos, Rot, and Locations" in guidance
    assert "execute_cadquery" not in guidance


def test_tool_runtime_exposes_execute_build123d_before_apply_cad_action() -> None:
    runtime = ToolRuntime(sandbox=_FakeSandbox())
    tool_names = [tool.name for tool in runtime.build_llm_tools()]
    assert tool_names.index("execute_build123d") < tool_names.index("apply_cad_action")
```

- [ ] **Step 2: Add failing lint and failure-summary tests for the new Build123d names**

```python
def test_execute_build123d_preflight_lint_blocks_legacy_cadquery_api() -> None:
    lint = _preflight_lint_execute_build123d(
        "import cadquery as cq\n"
        "result = cq.Workplane('XY').box(1, 1, 1)"
    )

    assert lint is not None
    assert lint["failure_kind"] == "execute_build123d_api_lint_failure"
    assert lint["lint_hits"][0]["rule_id"] == "legacy_cadquery_api"


def test_previous_tool_failure_summary_normalizes_execute_build123d_selector_failure() -> None:
    run_state = _make_run_state_for_failed_code_write(
        tool_name="execute_build123d",
        stderr="Selector string failed to parse",
        failure_kind="execute_build123d_selector_failure",
    )
    failure_summary = V2ContextManager().build_previous_tool_failure_summary(run_state)
    assert failure_summary["failure_kind"] == "execute_build123d_selector_failure"
```

- [ ] **Step 3: Run the runtime regression suites and confirm the current CadQuery assumptions fail**

Run: `pytest tests/unit/sub_agent_runtime/test_v2_runtime.py tests/unit/sub_agent_runtime/test_probe_first_semantic_refresh.py tests/unit/common/test_blocker_taxonomy.py -q`

Expected: FAIL because tool names, failure kinds, guidance strings, and recommended tool lists still point at `execute_cadquery*`.

- [ ] **Step 4: Rename the code-first tool specs and dispatch paths**

```python
from sandbox_mcp_server.contracts import (
    CADActionInput,
    ExecuteBuild123dInput,
    ExecuteBuild123dProbeInput,
    GetHistoryInput,
    QueryFeatureProbesInput,
    QueryGeometryInput,
    QuerySketchInput,
    QuerySnapshotInput,
    QueryTopologyInput,
    RenderViewInput,
    ValidateRequirementInput,
)
```

```python
if name == "execute_build123d":
    lint_payload = _preflight_lint_execute_build123d(tool_call.arguments.get("code", ""))
    if lint_payload is not None:
        return ToolBatchResult(
            tool_calls=[normalized_call],
            tool_results=[
                ToolResultRecord(
                    tool_name="execute_build123d",
                    success=False,
                    payload=lint_payload,
                )
            ],
            execution_events=[],
        )

if name == "execute_build123d_probe":
    payload = await self._sandbox.execute_build123d_probe(
        code=tool_call.arguments.get("code", ""),
        timeout=tool_call.arguments.get("timeout_seconds", sandbox_timeout),
        session_id=session_id,
        include_artifact_content=tool_call.arguments.get("include_artifact_content", True),
    )
```

- [ ] **Step 5: Rewrite the follow-up guidance to a builder-first Build123d model**

```python
ToolSpec(
    name="execute_build123d",
    category=ToolCategory.WRITE,
    description=(
        "Default code-first whole-part and subtree rebuild tool. "
        "Use BuildPart for host solids, BuildSketch for section profiles, "
        "BuildLine for rails, and explicit Plane/Axis/Locations placement."
    ),
    input_model=ExecuteBuild123dInput,
    runtime_managed_fields={"session_id", "requirement_text", "timeout_seconds"},
    follow_up_recommendation=(
        "Default first-write path. Prefer explicit BuildPart / BuildSketch / BuildLine staging. "
        "Use Plane, Axis, Pos, Rot, and Locations instead of chained workplane intuition. "
        "After a successful session-backed code write, inspect geometry or validate before another broad rewrite."
    ),
)
```

```python
{
    "skill_id": "execute_build123d_builder_hygiene",
    "when_relevant": "Use whenever you write or repair execute_build123d code.",
    "guidance": [
        "Prefer BuildPart for host solids, BuildSketch for section profiles, and BuildLine for rails.",
        "Use explicit Plane, Axis, Pos, Rot, and Locations placement rather than chained workplane state.",
        "When the requirement is whole-part or subtree shaped, prefer execute_build123d as the first write."
    ],
}
```

- [ ] **Step 6: Replace the CadQuery preflight lint with deterministic Build123d lint**

```python
def _preflight_lint_execute_build123d(code: str) -> dict[str, Any] | None:
    lowered = code.lower()
    lint_hits: list[dict[str, str]] = []

    if "import cadquery" in lowered or "cq.workplane" in lowered:
        lint_hits.append(
            {
                "rule_id": "legacy_cadquery_api",
                "message": "execute_build123d only accepts Build123d code; CadQuery APIs are not supported.",
                "suggestion": "Rewrite the model with BuildPart, BuildSketch, BuildLine, Plane, Axis, and a final `result = bp.part` assignment.",
            }
        )

    if "result =" not in code:
        lint_hits.append(
            {
                "rule_id": "missing_explicit_part_result",
                "message": "Build123d code must assign a final solid-bearing Part to `result`.",
                "suggestion": "Finish the script with `result = bp.part` or another explicit Part value.",
            }
        )

    if not lint_hits:
        return None

    return {
        "error_message": "execute_build123d preflight lint failed",
        "failure_kind": "execute_build123d_api_lint_failure",
        "lint_hits": lint_hits,
        "recommended_next_tools": ["execute_build123d", "query_kernel_state"],
    }
```

- [ ] **Step 7: Rename blocker-taxonomy and agent-loop references to the new tool names**

```python
BLOCKER_TAXONOMY = {
    "feature_unknown_profile_gap": {
        "recommended_tools": ["query_geometry", "execute_build123d_probe"],
    },
    "general_geometry_no_history": {
        "recommended_tools": ["query_geometry", "execute_build123d"],
    },
}
```

```python
if latest_code_write_turn is not None and isinstance(previous_tool_failure_summary, dict):
    if str(previous_tool_failure_summary.get("tool") or "").strip() == "execute_build123d":
        same_tool_failures = int(previous_tool_failure_summary.get("same_tool_failure_count") or 0)
```

- [ ] **Step 8: Run the runtime suites again**

Run: `pytest tests/unit/sub_agent_runtime/test_v2_runtime.py tests/unit/sub_agent_runtime/test_probe_first_semantic_refresh.py tests/unit/common/test_blocker_taxonomy.py -q`

Expected: PASS with Build123d tool names, builder-first guidance, renamed failure kinds, and updated recovery policy.

- [ ] **Step 9: Commit the runtime/lint slice**

```bash
git add src/sub_agent_runtime/tool_runtime.py src/sub_agent_runtime/skill_pack.py src/sub_agent_runtime/context_manager.py src/sub_agent_runtime/feature_graph.py src/common/blocker_taxonomy.py src/sub_agent_runtime/agent_loop_v2.py tests/unit/sub_agent_runtime/test_v2_runtime.py tests/unit/sub_agent_runtime/test_probe_first_semantic_refresh.py tests/unit/common/test_blocker_taxonomy.py
git commit -m "feat: make code-first runtime build123d-native"
```

### Task 4: Update benchmark identity, reports, and codegen payload naming

**Files:**
- Modify: `benchmark/run_prompt_benchmark.py`
- Modify: `benchmark/README.md`
- Modify: `src/sub_agent/codegen.py`
- Test: `tests/unit/benchmark/test_run_prompt_benchmark.py`
- Test: `tests/unit/sub_agent/test_codegen_aci.py`

- [ ] **Step 1: Rewrite benchmark and codegen tests to assert Build123d names**

```python
def test_brief_report_counts_execute_build123d_first_write_tools(tmp_path: Path) -> None:
    payload = {
        "case_id": "L1_1",
        "status": "PASS",
        "analysis": {},
        "runtime_summary": {
            "first_write_tool": "execute_build123d",
            "last_good_write": {"round": 1, "tool": "execute_build123d"},
            "executed_action_types": ["execute_build123d"],
        },
    }
    _write_brief_report(
        run_root=tmp_path,
        case_payloads=[payload],
        practice_identity={"practice_label": "runtime=v2"},
    )
    report = (tmp_path / "brief_report.md").read_text()
    assert "execute_build123d" in report
    assert "execute_cadquery" not in report


def test_codegen_payload_uses_reconstructed_build123d_code() -> None:
    payload = _normalize_codegen_payload(
        {
            "actions": [],
            "reconstructed_build123d_code": "with BuildPart() as bp:\n    Box(1, 1, 1)\nresult = bp.part",
        }
    )
    assert "reconstructed_build123d_code" in payload
    assert "reconstructed_cadquery_code" not in payload
```

- [ ] **Step 2: Run the benchmark and codegen unit tests and confirm they fail under the old naming**

Run: `pytest tests/unit/benchmark/test_run_prompt_benchmark.py tests/unit/sub_agent/test_codegen_aci.py -q`

Expected: FAIL because the report text, terminal diagnostics, and payload keys still use CadQuery names.

- [ ] **Step 3: Rename benchmark diagnostics and value normalization**

```python
if name == "execute_build123d":
    primary_write_mode = "code"

terminal_validation_gap = (
    last_error == "execute_build123d_terminal_without_session_validation"
)

if terminal_validation_gap:
    diagnosis = "terminal execute_build123d path without validator confirmation."
elif isinstance(last_error, str) and last_error.strip():
    diagnosis = last_error.strip()
else:
    diagnosis = ""
```

- [ ] **Step 4: Rename codegen payload fields and model-facing reminders**

```python
reconstructed_code = self._build_reconstructed_build123d_code(normalized_actions)
payload = {
    "normalized_actions": normalized_actions,
    "reconstructed_build123d_code": reconstructed_code,
}
```

```python
SYSTEM_REMINDERS = [
    "If reconstructed_build123d_code is present, treat it only as a convenience sketch, not ground truth.",
]
```

- [ ] **Step 5: Update benchmark README wording to the Build123d contract**

```markdown
By default the benchmark runs with:

1. `runtime=v2`
2. dynamic loop mode (`one-action-per-round`)
3. automatic deterministic STEP evaluation
4. `execute_build123d` as the default code-first whole-part write tool
```

- [ ] **Step 6: Run the benchmark and codegen tests again**

Run: `pytest tests/unit/benchmark/test_run_prompt_benchmark.py tests/unit/sub_agent/test_codegen_aci.py -q`

Expected: PASS with Build123d-only naming in reports, diagnostics, and codegen payloads.

- [ ] **Step 7: Commit the benchmark/reporting slice**

```bash
git add benchmark/run_prompt_benchmark.py benchmark/README.md src/sub_agent/codegen.py tests/unit/benchmark/test_run_prompt_benchmark.py tests/unit/sub_agent/test_codegen_aci.py
git commit -m "feat: rename benchmark and codegen build123d surfaces"
```

### Task 5: Run verification and the full L1 benchmark

**Files:**
- Modify if needed: any touched source/test files from verification fixes
- Output: `benchmark/runs/<timestamp>/`

- [ ] **Step 1: Run the focused regression suites together**

Run:

```bash
pytest \
  tests/unit/sandbox/test_docker_runner.py \
  tests/unit/sandbox/test_mcp_runner.py \
  tests/unit/sandbox_mcp_server/test_execute_cadquery_session_bridge.py \
  tests/unit/sub_agent_runtime/test_v2_runtime.py \
  tests/unit/sub_agent_runtime/test_probe_first_semantic_refresh.py \
  tests/unit/common/test_blocker_taxonomy.py \
  tests/unit/benchmark/test_run_prompt_benchmark.py \
  tests/unit/sub_agent/test_codegen_aci.py -q
```

Expected: PASS. Any failure must be fixed before moving to the benchmark run.

- [ ] **Step 2: Audit for remaining active CadQuery contract strings**

Run:

```bash
rg -n "execute_cadquery|cadquery-runtime|reconstructed_cadquery_code|ExecuteCadQuery|CadQuery" src tests benchmark
```

Expected: no matches in active source/tests/benchmark logic, except temporary legacy test filenames or migration comments that do not affect runtime behavior.

- [ ] **Step 3: Sync dependencies so the environment includes Build123d**

Run: `uv sync`

Expected: success, with `build123d` installed in the active environment.

- [ ] **Step 4: Run the full L1 benchmark using the repository `.env`**

Run:

```bash
set -a
source .env
set +a
./benchmark/run_prompt_benchmark.sh --levels L1
```

Expected: a new run directory under `benchmark/runs/` with `summary.json`, `brief_report.md`, `run_diagnostics.md`, per-case subdirectories, and Build123d-only tool identities in the summaries.

- [ ] **Step 5: Inspect the latest benchmark summary and record the run directory**

Run:

```bash
python - <<'PY'
import json
from pathlib import Path

runs = sorted(Path("benchmark/runs").iterdir())
run_dir = runs[-1]
summary = json.loads((run_dir / "summary.json").read_text())
print(run_dir)
print(summary.get("aggregate", {}))
PY
```

Expected: prints the latest benchmark run directory and aggregate summary so the final handoff can cite them exactly.

- [ ] **Step 6: Commit any verification-driven fixes**

```bash
git add pyproject.toml src benchmark tests
git commit -m "fix: stabilize build123d runtime migration"
```

- [ ] **Step 7: Prepare the final delivery notes**

```text
Report:
- exact focused pytest command and result
- exact benchmark run directory
- whether any residual active CadQuery references remain
- the top L1 failure clusters, if any
```
