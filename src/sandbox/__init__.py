"""Sandbox execution and MCP client package."""

from common.config import SandboxType
from sandbox.interface import SandboxResult, SandboxRunner
from sandbox.docker_runner import DockerSandboxRunner
from sandbox.local_process_runner import LocalProcessSandboxRunner
from sandbox.mcp_runner import McpSandboxRunner


def create_sandbox_runner(settings):
    if settings.sandbox_type == SandboxType.LOCAL_PROCESS:
        return LocalProcessSandboxRunner()
    if settings.sandbox_type == SandboxType.DOCKER_LOCAL:
        return DockerSandboxRunner(
            image=settings.sandbox_image,
            memory_limit=settings.sandbox_memory_limit,
            cpu_quota=settings.sandbox_cpu_quota,
            docker_socket=settings.sandbox_docker_socket,
        )
    raise ValueError(
        "sandbox_mcp_server only supports SANDBOX_TYPE=docker-local or "
        "SANDBOX_TYPE=local-process for internal execution"
    )

__all__ = [
    "SandboxResult",
    "SandboxRunner",
    "DockerSandboxRunner",
    "LocalProcessSandboxRunner",
    "McpSandboxRunner",
    "create_sandbox_runner",
]
