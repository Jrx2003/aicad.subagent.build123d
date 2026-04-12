"""Docker sandbox runner for Build123d code execution."""

import asyncio
import io
import shutil
import tarfile
import tempfile
import time
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

import docker
from docker.errors import APIError, ImageNotFound

from sandbox.interface import SandboxResult
from common.logging import get_logger

if TYPE_CHECKING:
    from common.config import Settings

logger = get_logger(__name__)

_OUTPUT_ARTIFACT_FILENAMES: tuple[str, ...] = (
    "model.step",
    "preview_iso.png",
    "preview_front.png",
    "preview_right.png",
    "preview_top.png",
    "render_view.png",
    "generated_preview_iso.png",
    "generated_preview_front.png",
    "generated_preview_right.png",
    "generated_preview_top.png",
    "ground_truth_preview_iso.png",
    "ground_truth_preview_front.png",
    "ground_truth_preview_right.png",
    "ground_truth_preview_top.png",
    "geometry_info.json",
)


def _build_runtime_code(user_code: str) -> str:
    """Wrap user code with Build123d compatibility helpers."""
    prelude = (
        "from build123d import *\n"
        "from pathlib import Path\n"
        "__aicad_last_show_object = None\n"
        "__aicad_last_result = None\n"
        "def show_object(obj, *args, **kwargs):\n"
        "    global __aicad_last_show_object\n"
        "    __aicad_last_show_object = obj\n"
        "def debug(*args, **kwargs):\n"
        "    return None\n"
        "def __aicad_as_exportable(candidate):\n"
        "    try:\n"
        "        if hasattr(candidate, 'part'):\n"
        "            part = candidate.part\n"
        "            if __aicad_has_exportable_solids(part):\n"
        "                return part\n"
        "        if __aicad_has_exportable_solids(candidate):\n"
        "            return candidate\n"
        "    except Exception:\n"
        "        return None\n"
        "    return None\n"
        "def __aicad_resolve_export_part():\n"
        "    if 'result' in globals():\n"
        "        result_part = __aicad_as_exportable(result)\n"
        "        if result_part is not None:\n"
        "            return result_part\n"
        "    if 'part' in globals():\n"
        "        part_part = __aicad_as_exportable(part)\n"
        "        if part_part is not None:\n"
        "            return part_part\n"
        "    if 'model' in globals():\n"
        "        model_part = __aicad_as_exportable(model)\n"
        "        if model_part is not None:\n"
        "            return model_part\n"
        "    if '__aicad_last_result' in globals():\n"
        "        last_result_part = __aicad_as_exportable(__aicad_last_result)\n"
        "        if last_result_part is not None:\n"
        "            return last_result_part\n"
        "    if __aicad_last_show_object is not None:\n"
        "        show_object_part = __aicad_as_exportable(__aicad_last_show_object)\n"
        "        if show_object_part is not None:\n"
        "            return show_object_part\n"
        "    return None\n"
        "def __aicad_has_exportable_solids(obj):\n"
        "    try:\n"
        "        return hasattr(obj, 'solids') and len(list(obj.solids())) > 0\n"
        "    except Exception:\n"
        "        return False\n"
    )
    epilogue = (
        "\n"
        "if 'result' in globals():\n"
        "    __aicad_last_result = result\n"
        "elif __aicad_last_show_object is not None:\n"
        "    __aicad_last_result = __aicad_last_show_object\n"
        "__aicad_export_part = __aicad_resolve_export_part()\n"
        "if __aicad_export_part is not None and __aicad_has_exportable_solids(__aicad_export_part):\n"
        "    Path('/output').mkdir(parents=True, exist_ok=True)\n"
        "    export_step(__aicad_export_part, '/output/model.step')\n"
    )
    return f"{prelude}\n{user_code.rstrip()}\n{epilogue}"


class DockerSandboxRunner:
    """Execute Build123d code in isolated Docker containers."""

    def __init__(
        self,
        image: str,
        memory_limit: str = "512m",
        cpu_quota: int = 100000,
        docker_socket: str | None = None,
    ):
        """Initialize Docker sandbox runner.

        Args:
            image: Docker image name (e.g., "build123d-runtime:latest")
            memory_limit: Memory limit (e.g., "512m", "1g")
            cpu_quota: CPU quota (100000 = 1 CPU)
            docker_socket: Docker socket path (default: auto-detect)
        """
        self._image = image
        self._memory_limit = memory_limit
        self._cpu_quota = cpu_quota

        # Initialize Docker client
        if docker_socket:
            self._client = docker.DockerClient(base_url=f"unix://{docker_socket}")
        else:
            self._client = docker.from_env()

    async def execute(
        self,
        code: str,
        timeout: int = 120,
        requirement_text: str | None = None,
        session_id: str | None = None,
    ) -> SandboxResult:
        """Execute Build123d code in Docker container.

        Args:
            code: Python code to execute
            timeout: Execution timeout in seconds

        Returns:
            SandboxResult with stdout/stderr and output files
        """
        _ = requirement_text
        _ = session_id
        container_name = f"build123d-{uuid.uuid4().hex[:12]}"

        logger.info(
            "sandbox_execution_starting",
            container_name=container_name,
            code_length=len(code),
            timeout=timeout,
        )

        try:
            # Run in thread pool to avoid blocking
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                None,
                lambda: self._execute_sync(code, container_name, timeout),
            )
            return result

        except Exception as e:
            logger.exception("sandbox_execution_error", container_name=container_name)
            return SandboxResult(
                success=False,
                stdout="",
                stderr=str(e),
                output_files=[],
                output_file_contents={},
                error_message=f"Container execution failed: {e}",
            )

    def _execute_sync(
        self,
        code: str,
        container_name: str,
        timeout: int,
    ) -> SandboxResult:
        """Synchronous container execution (runs in thread pool).

        Args:
            code: Python code to execute
            container_name: Unique container name
            timeout: Timeout in seconds

        Returns:
            SandboxResult
        """
        container = None
        temp_dir = None

        try:
            # Create temp directory for file exchange
            temp_dir = tempfile.mkdtemp(prefix="build123d-sandbox-")
            temp_path = Path(temp_dir)

            # Avoid naming the runtime file code.py because build123d imports IPython,
            # which imports the stdlib code module during startup.
            code_file = temp_path / "aicad_runtime_main.py"
            runtime_code = _build_runtime_code(code)
            code_file.write_text(runtime_code, encoding="utf-8")

            # Create container (not started yet)
            # Note: read_only=False because we need to copy the runtime script into /app
            # Security is maintained via network_mode="none" and no-new-privileges
            container = self._client.containers.create(
                image=self._image,
                name=container_name,
                detach=True,
                entrypoint=[
                    "/usr/local/bin/_entrypoint.sh",
                    "python",
                    "/app/aicad_runtime_main.py",
                ],
                mem_limit=self._memory_limit,
                cpu_quota=self._cpu_quota,
                network_mode="none",  # No network access
                security_opt=["no-new-privileges:true"],
            )

            # Copy code into container
            self._copy_to_container(container, code_file, "/app/aicad_runtime_main.py")

            # Start container
            container.start()

            # Wait for completion with timeout
            try:
                exit_code = self._wait_for_exit(container, timeout=timeout)
            except TimeoutError as wait_error:
                # Timeout or other error
                logger.warning(
                    "container_wait_timeout",
                    container_name=container_name,
                    error=str(wait_error),
                )
                try:
                    container.kill()
                except Exception:
                    pass
                return SandboxResult(
                    success=False,
                    stdout="",
                    stderr=f"Execution timed out after {timeout} seconds",
                    output_files=[],
                    output_file_contents={},
                    error_message="Timeout",
                )
            except Exception as wait_error:
                logger.warning(
                    "container_wait_failed",
                    container_name=container_name,
                    error=str(wait_error),
                )
                return SandboxResult(
                    success=False,
                    stdout="",
                    stderr=str(wait_error),
                    output_files=[],
                    output_file_contents={},
                    error_message="Container wait failed",
                )

            # Get logs
            stdout = container.logs(stdout=True, stderr=False).decode("utf-8")
            stderr = container.logs(stdout=False, stderr=True).decode("utf-8")

            # Check exit code
            if exit_code != 0:
                logger.warning(
                    "container_execution_failed",
                    container_name=container_name,
                    exit_code=exit_code,
                    stderr=stderr[:500],
                )
                return SandboxResult(
                    success=False,
                    stdout=stdout,
                    stderr=stderr,
                    output_files=[],
                    output_file_contents={},
                    error_message=f"Exit code: {exit_code}",
                )

            # Extract output files
            output_files = []
            output_file_contents = {}

            for filename in _OUTPUT_ARTIFACT_FILENAMES:
                try:
                    content = self._copy_from_container(
                        container, f"/output/{filename}"
                    )
                except Exception:
                    continue
                output_files.append(filename)
                output_file_contents[filename] = content

            logger.info(
                "sandbox_execution_complete",
                container_name=container_name,
                success=True,
                output_files=output_files,
            )

            return SandboxResult(
                success=True,
                stdout=stdout,
                stderr=stderr,
                output_files=output_files,
                output_file_contents=output_file_contents,
                error_message=None,
            )

        except ImageNotFound:
            logger.error("sandbox_image_not_found", image=self._image)
            return SandboxResult(
                success=False,
                stdout="",
                stderr=f"Docker image not found: {self._image}",
                output_files=[],
                output_file_contents={},
                error_message="Image not found",
            )

        except APIError as e:
            logger.error("docker_api_error", error=str(e))
            return SandboxResult(
                success=False,
                stdout="",
                stderr=str(e),
                output_files=[],
                output_file_contents={},
                error_message="Docker API error",
            )

        finally:
            # Always clean up container
            if container:
                try:
                    container.remove(force=True)
                    logger.debug("container_removed", container_name=container_name)
                except Exception as cleanup_error:
                    logger.warning(
                        "container_cleanup_failed",
                        container_name=container_name,
                        error=str(cleanup_error),
                    )

            # Clean up temp directory
            if temp_dir:
                try:
                    shutil.rmtree(temp_dir)
                except Exception:
                    pass

    def _copy_to_container(self, container, src_path: Path, dest_path: str) -> None:
        """Copy file into container using tar archive.

        Args:
            container: Docker container object
            src_path: Local source file path
            dest_path: Destination path inside container
        """
        # Create tar archive in memory
        tar_stream = io.BytesIO()
        with tarfile.open(fileobj=tar_stream, mode="w") as tar:
            tar.add(str(src_path), arcname=Path(dest_path).name)
        tar_stream.seek(0)

        # Copy to container
        dest_dir = str(Path(dest_path).parent)
        container.put_archive(dest_dir, tar_stream.getvalue())

    def _copy_from_container(self, container, src_path: str) -> bytes:
        """Copy file from container using tar archive.

        Args:
            container: Docker container object
            src_path: Source path inside container

        Returns:
            File content as bytes
        """
        # Get tar archive from container
        bits, stat = container.get_archive(src_path)

        # Extract from tar
        tar_stream = io.BytesIO()
        for chunk in bits:
            tar_stream.write(chunk)
        tar_stream.seek(0)

        with tarfile.open(fileobj=tar_stream, mode="r") as tar:
            # Extract the file
            member = tar.getmembers()[0]
            extracted = tar.extractfile(member)
            if extracted:
                return extracted.read()

        raise RuntimeError(f"Failed to extract {src_path} from container")

    def _wait_for_exit(
        self,
        container,
        timeout: int,
        poll_interval: float = 0.25,
    ) -> int:
        """Wait for container exit using local polling.

        Docker SDK's `wait(timeout=...)` uses HTTP read timeouts, which can
        stretch far beyond the requested execution budget when the daemon is
        slow to respond. Polling `container.reload()` keeps timeout semantics
        under our control.
        """
        deadline = time.monotonic() + timeout
        last_status: str | None = None

        while time.monotonic() < deadline:
            container.reload()
            state = container.attrs.get("State", {})
            status = str(state.get("Status", "unknown"))

            if status != last_status:
                logger.debug(
                    "container_status_update",
                    container_name=getattr(container, "name", "<unknown>"),
                    status=status,
                )
                last_status = status

            if status in {"exited", "dead"}:
                return int(state.get("ExitCode", 1))

            time.sleep(poll_interval)

        raise TimeoutError(f"Execution timed out after {timeout} seconds")


def create_sandbox_runner(settings: "Settings") -> DockerSandboxRunner:
    """Factory function to create sandbox runner from settings.

    Args:
        settings: Application settings

    Returns:
        Configured DockerSandboxRunner
    """
    return DockerSandboxRunner(
        image=settings.sandbox_image,
        memory_limit=settings.sandbox_memory_limit,
        cpu_quota=settings.sandbox_cpu_quota,
        docker_socket=settings.sandbox_docker_socket,
    )
