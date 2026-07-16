"""Deadline-enforcement contract for the Codex reviewer lane.

Regression cover for the 2026-07-15 Q10 Track B incident: a codex reviewer
lane spawned under the Hermes panel hung for ~11h with the 300s deadline
never taking effect. Root cause: on Windows the wrapper launches via
``create_subprocess_shell`` (cmd.exe -> codex.CMD -> node -> codex -> N node
grandchildren). Every grandchild inherits the stdout/stderr PIPE handles, so
``communicate()`` never reaches EOF while any grandchild survives, and the
timeout handler's ``process.kill()`` only kills cmd.exe -- the tree, and the
pipes it holds open, live on. The wrapper must instead terminate the whole
child process tree when the deadline expires.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import time

import integrations.codex_wrapper as codex_wrapper
from integrations.codex_wrapper import CodexCLI, _terminate_process_tree


def _pid_alive(pid: int) -> bool:
    if sys.platform == "win32":
        out = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
            capture_output=True,
            text=True,
            check=False,
        )
        return str(pid) in out.stdout
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _wait_until(predicate, timeout: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.1)
    return predicate()


class _HangingProcess:
    """A spawned process whose output pipes never reach EOF on their own.

    Mirrors the real failure: ``communicate()`` only returns once the process
    TREE is torn down (which closes the inherited pipe handles). ``kill()``
    represents killing only the immediate child (cmd.exe) and does NOT unblock
    the pipes, so a wrapper that relies on it alone deadlocks.
    """

    def __init__(self, pid: int = 4242) -> None:
        self.pid = pid
        self.returncode = None
        self.parent_only_killed = False
        self._tree_down = asyncio.Event()

    async def communicate(self, input=None):  # noqa: A002 - match asyncio API
        await self._tree_down.wait()
        return (b"", b"")

    def kill(self) -> None:
        # Killing only the immediate child does not close the inherited pipes.
        self.parent_only_killed = True

    async def wait(self) -> int:
        await self._tree_down.wait()
        return self.returncode or 0

    def signal_tree_terminated(self) -> None:
        self.returncode = -1
        self._tree_down.set()


async def test_timeout_terminates_whole_process_tree(monkeypatch, tmp_path) -> None:
    """On deadline expiry the wrapper must terminate the entire child tree
    (via the module ``_terminate_process_tree`` helper), not merely
    ``process.kill()`` the immediate child. Otherwise inherited pipe handles
    stay open and the lane hangs indefinitely."""
    monkeypatch.delenv("CODEX_MODEL", raising=False)
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))

    proc = _HangingProcess(pid=4242)

    async def fake_spawn(*args, **kwargs):
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_shell", fake_spawn)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_spawn)

    tree_killed: list[int] = []

    def fake_tree_kill(pid: int) -> None:
        tree_killed.append(pid)
        proc.signal_tree_terminated()  # tearing down the tree closes the pipes

    # raising=True (default): the helper MUST exist -- absence is itself a RED.
    monkeypatch.setattr(codex_wrapper, "_terminate_process_tree", fake_tree_kill)

    codex = CodexCLI(timeout_seconds=1)
    codex._codex_path = "codex"

    # Outer guard so a regression can never hang the test suite itself.
    response = await asyncio.wait_for(codex.exec("short prompt"), timeout=15)

    assert response.exit_code == -1
    assert response.error and "timed out" in response.error.lower()
    # The core contract: the CHILD pid's whole tree was terminated.
    assert tree_killed == [4242]


async def test_completed_run_does_not_terminate_tree(monkeypatch, tmp_path) -> None:
    """A lane that finishes before the deadline must NOT trigger a tree kill --
    the enforcement path is timeout-only and must not disturb healthy runs."""
    monkeypatch.delenv("CODEX_MODEL", raising=False)
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))

    class _FastProcess:
        returncode = 0

        async def communicate(self, input=None):  # noqa: A002
            return (b"codex says hi", b"")

    async def fake_spawn(*args, **kwargs):
        return _FastProcess()

    monkeypatch.setattr(asyncio, "create_subprocess_shell", fake_spawn)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_spawn)

    tree_killed: list[int] = []
    monkeypatch.setattr(
        codex_wrapper,
        "_terminate_process_tree",
        lambda pid: tree_killed.append(pid),
    )

    codex = CodexCLI(timeout_seconds=30)
    codex._codex_path = "codex"

    response = await codex.exec("short prompt")

    assert response.exit_code == 0
    assert response.content == "codex says hi"
    assert tree_killed == []


def test_terminate_process_tree_reaps_real_descendants(tmp_path) -> None:
    """Integration: the helper must kill a real parent AND its grandchild.

    This is the assertion the unit tests cannot make (they stub the helper):
    it exercises the actual ``taskkill /T`` (Windows) / SIGKILL (POSIX) path
    against real OS processes. A regression here is precisely the class of bug
    that let a codex subprocess tree survive its parent and hang a lane ~11h.
    """
    pid_file = tmp_path / "child.pid"
    spawn_script = tmp_path / "spawn_tree.py"
    spawn_script.write_text(
        "import subprocess, sys, time, pathlib\n"
        "child = subprocess.Popen([sys.executable, '-c', "
        "'import time; time.sleep(120)'])\n"
        "pathlib.Path(sys.argv[1]).write_text(str(child.pid))\n"
        "time.sleep(120)\n",
        encoding="utf-8",
    )

    parent = subprocess.Popen([sys.executable, str(spawn_script), str(pid_file)])
    try:
        assert _wait_until(
            lambda: pid_file.exists() and pid_file.read_text().strip().isdigit()
        ), "grandchild pid never reported"
        child_pid = int(pid_file.read_text().strip())

        assert _pid_alive(parent.pid)
        assert _pid_alive(child_pid)

        _terminate_process_tree(parent.pid)

        assert _wait_until(lambda: not _pid_alive(parent.pid)), "parent survived"
        assert _wait_until(
            lambda: not _pid_alive(child_pid)
        ), "grandchild survived tree kill"
    finally:
        if parent.poll() is None:
            parent.kill()
        parent.wait(timeout=5)
