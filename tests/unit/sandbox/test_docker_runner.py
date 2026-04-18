from sandbox.docker_runner import _describe_docker_connection_error


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
