import pytest
from docker.errors import DockerException

from sandbox.docker_runner import (
    DockerSandboxRunner,
    _describe_docker_connection_error,
)


def test_describe_docker_connection_error_reports_missing_socket_candidates():
    error = FileNotFoundError(2, "No such file or directory")

    message = _describe_docker_connection_error(error)

    assert "Docker daemon appears unavailable" in message
    assert "/var/run/docker.sock" in message
    assert ".docker/run/docker.sock" in message


def test_describe_docker_connection_error_prefers_configured_socket_path():
    error = FileNotFoundError(2, "No such file or directory")

    message = _describe_docker_connection_error(
        error,
        docker_socket="/tmp/custom-docker.sock",
    )

    assert "Docker daemon appears unavailable" in message
    assert "/tmp/custom-docker.sock" in message


def test_docker_runner_init_is_lazy_when_docker_is_unavailable(monkeypatch):
    def _raise_from_env():
        raise DockerException(
            "Error while fetching server API version: "
            "('Connection aborted.', FileNotFoundError(2, 'No such file or directory'))"
        )

    monkeypatch.setattr("sandbox.docker_runner.docker.from_env", _raise_from_env)

    runner = DockerSandboxRunner(image="build123d-runtime:latest")

    assert runner is not None


@pytest.mark.asyncio
async def test_docker_runner_execute_returns_structured_unavailable_error(monkeypatch):
    def _raise_from_env():
        raise DockerException(
            "Error while fetching server API version: "
            "('Connection aborted.', FileNotFoundError(2, 'No such file or directory'))"
        )

    monkeypatch.setattr("sandbox.docker_runner.docker.from_env", _raise_from_env)

    runner = DockerSandboxRunner(image="build123d-runtime:latest")
    result = await runner.execute("result = Box(10, 10, 4)")

    assert result.success is False
    assert "Docker daemon appears unavailable" in result.stderr
    assert "server API version" in result.stderr
    assert result.error_message == result.stderr
