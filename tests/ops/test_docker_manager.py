"""Tests for DockerManager with mocked subprocess."""

import json
import subprocess
import pytest
from unittest.mock import patch, MagicMock

from ops.infra.docker_manager import DockerManager


@pytest.fixture
def no_docker():
    """DockerManager where docker is not installed."""
    with patch("shutil.which", return_value=None):
        yield DockerManager()


@pytest.fixture
def docker_mgr():
    """DockerManager where docker is 'installed' (mocked path)."""
    with patch("shutil.which", return_value="/usr/bin/docker"):
        yield DockerManager()


class TestDockerNotInstalled:
    def test_available_false(self, no_docker):
        assert no_docker.available is False

    def test_service_status_graceful(self, no_docker):
        result = no_docker.service_status()
        assert result["success"] is False
        assert "not found" in result["error"].lower()

    def test_restart_graceful(self, no_docker):
        result = no_docker.restart_service("myapp")
        assert result["success"] is False

    def test_prune_graceful(self, no_docker):
        result = no_docker.prune_networks()
        assert result["success"] is False


class TestServiceStatus:
    def test_empty(self, docker_mgr):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            result = docker_mgr.service_status()
        assert result["success"] is True
        assert result["containers"] == []

    def test_with_containers(self, docker_mgr):
        container_json = json.dumps({"Names": "myapp", "Status": "Up 2h", "Image": "myapp:latest"})
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=container_json + "\n", stderr="")
            result = docker_mgr.service_status()
        assert result["success"] is True
        assert len(result["containers"]) == 1
        assert result["containers"][0]["Names"] == "myapp"

    def test_filter_by_name(self, docker_mgr):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            docker_mgr.service_status(name="myapp")
            cmd = mock_run.call_args[0][0]
            assert "--filter" in cmd
            assert "name=myapp" in cmd


class TestRestart:
    def test_success(self, docker_mgr):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="myapp\n", stderr="")
            result = docker_mgr.restart_service("myapp")
        assert result["success"] is True

    def test_failure(self, docker_mgr):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="No such container")
            result = docker_mgr.restart_service("nonexistent")
        assert result["success"] is False
        assert "No such container" in result["error"]


class TestPruneNetworks:
    def test_success(self, docker_mgr):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="Deleted Networks:\nbridge1\n", stderr="")
            result = docker_mgr.prune_networks()
        assert result["success"] is True


class TestTimeout:
    def test_timeout_expired(self, docker_mgr):
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="docker", timeout=30)):
            result = docker_mgr.service_status()
        assert result["success"] is False
        assert "Timeout" in result["error"]
