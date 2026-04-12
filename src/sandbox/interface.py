from pydantic import BaseModel
from typing import Any, Protocol


class SandboxResult(BaseModel):
    """Result from sandbox execution."""

    model_config = {"arbitrary_types_allowed": True}

    success: bool
    stdout: str
    stderr: str
    output_files: list[str] = []
    output_file_contents: dict[str, bytes] = {}  # filename -> content mapping
    error_message: str | None = None
    evaluation: dict[str, Any] | None = None
    session_id: str | None = None
    step: int | None = None
    step_file: str | None = None
    snapshot: dict[str, Any] | None = None
    session_state_persisted: bool = False


class SandboxRunner(Protocol):
    """Protocol for sandbox execution."""

    async def execute(
        self,
        code: str,
        timeout: int = 120,
        requirement_text: str | None = None,
        session_id: str | None = None,
    ) -> SandboxResult:
        """Execute code in sandbox."""
        ...
