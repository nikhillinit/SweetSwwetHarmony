"""Unit tests for utils.git_utils — mock _git_cmd to test parsing logic."""

from unittest.mock import patch

from utils.git_utils import DETACHED, get_git_info


def _make_git_cmd_side_effect(sha_return, branch_return):
    """Return a side_effect function that maps git commands to canned responses."""
    def side_effect(cmd, cwd):
        if "--short" in cmd:
            return sha_return
        if "--abbrev-ref" in cmd:
            return branch_return
        return None
    return side_effect


@patch("utils.git_utils._git_cmd")
def test_normal_branch(mock_cmd):
    mock_cmd.side_effect = _make_git_cmd_side_effect("abc1234", "feature/x")
    branch, sha = get_git_info()
    assert branch == "feature/x"
    assert sha == "abc1234"


@patch("utils.git_utils._git_cmd")
def test_main_branch(mock_cmd):
    mock_cmd.side_effect = _make_git_cmd_side_effect("def5678", "main")
    branch, sha = get_git_info()
    assert branch == "main"
    assert sha == "def5678"


@patch("utils.git_utils._git_cmd")
def test_detached_head(mock_cmd):
    mock_cmd.side_effect = _make_git_cmd_side_effect("abc1234", "HEAD")
    branch, sha = get_git_info()
    assert branch == DETACHED
    assert sha == "abc1234"


@patch("utils.git_utils._git_cmd")
def test_no_git(mock_cmd):
    mock_cmd.side_effect = _make_git_cmd_side_effect(None, None)
    branch, sha = get_git_info()
    assert branch is None
    assert sha is None


@patch("utils.git_utils._git_cmd")
def test_sha_ok_branch_fails(mock_cmd):
    mock_cmd.side_effect = _make_git_cmd_side_effect("abc1234", None)
    branch, sha = get_git_info()
    assert branch == DETACHED
    assert sha == "abc1234"
