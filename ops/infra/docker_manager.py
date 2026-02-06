"""Subprocess-based Docker management with graceful degradation.

Provides service status, restart, stop, and network pruning via the
docker CLI.  When Docker is not installed, ``available`` returns False
and every method returns a graceful error dict.
"""

import json
import logging
import shutil
import subprocess
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

_TIMEOUT = 30  # seconds


class DockerManager:
    """Thin wrapper around the docker CLI."""

    def __init__(self, timeout: int = _TIMEOUT):
        self.timeout = timeout
        self._docker_path = shutil.which("docker")

    @property
    def available(self) -> bool:
        return self._docker_path is not None

    def _run(self, args: list, timeout: Optional[int] = None) -> Dict[str, Any]:
        if not self.available:
            return {
                "success": False,
                "output": "",
                "error": "Docker CLI not found on PATH",
            }

        cmd = [self._docker_path] + args
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout or self.timeout,
            )
            if result.returncode != 0:
                return {
                    "success": False,
                    "output": result.stdout,
                    "error": result.stderr.strip() or f"Exit code {result.returncode}",
                }
            return {"success": True, "output": result.stdout}
        except subprocess.TimeoutExpired:
            return {
                "success": False,
                "output": "",
                "error": f"Timeout after {timeout or self.timeout}s",
            }
        except Exception as e:
            return {"success": False, "output": "", "error": str(e)}

    def service_status(self, name: Optional[str] = None) -> Dict[str, Any]:
        """List running containers, optionally filtered by *name*."""
        args = ["ps", "--format", "{{json .}}"]
        if name:
            args.extend(["--filter", f"name={name}"])
        result = self._run(args)
        if result["success"]:
            containers = []
            for line in result["output"].strip().splitlines():
                if line.strip():
                    try:
                        containers.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
            result["containers"] = containers
        return result

    def restart_service(self, name: str) -> Dict[str, Any]:
        """Restart a container by *name*."""
        return self._run(["restart", name])

    def stop_service(self, name: str) -> Dict[str, Any]:
        """Stop a container by *name*."""
        return self._run(["stop", name])

    def prune_networks(self) -> Dict[str, Any]:
        """Remove unused Docker networks."""
        return self._run(["network", "prune", "-f"])
