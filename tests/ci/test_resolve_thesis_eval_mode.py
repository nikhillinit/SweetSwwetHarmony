# tests/ci/test_resolve_thesis_eval_mode.py
from scripts.ci.resolve_thesis_eval_mode import decide, route_executor


def test_gold_mode_when_google_key_present():
    d = decide({"GOOGLE_API_KEY": "x"}, plan={})
    assert d["mode"] == "gold"


def test_gold_mode_when_gemini_key_present():
    d = decide({"GEMINI_API_KEY": "x"}, plan={})
    assert d["mode"] == "gold"


def test_hermes_mode_when_executor_supports_execute():
    plan = {
        "recommendedExecutor": "codex",
        "phase": "production",
        "risk": "low",
        "executorMetadata": {"codex": {"enabled": True, "supportsExecute": True}},
    }
    d = decide({}, plan=plan)
    assert d["mode"] == "hermes"
    assert d["executor"] == "codex"


def test_structural_mode_when_executor_cannot_execute():
    plan = {
        "recommendedExecutor": "gemini",
        "executorMetadata": {"gemini": {"enabled": True, "supportsExecute": False}},
    }
    d = decide({}, plan=plan)
    assert d["mode"] == "structural"


def test_structural_mode_when_no_plan():
    assert decide({}, plan={})["mode"] == "structural"


def test_route_executor_returns_empty_on_nonzero_exit():
    class FakeProc:
        returncode = 1
        stdout = ""
        stderr = "boom"

    def runner(*_a, **_k):
        return FakeProc()

    assert route_executor(runner=runner) == {}


def test_route_executor_parses_json():
    class FakeProc:
        returncode = 0
        stdout = '{"recommendedExecutor": "codex"}'
        stderr = ""

    def runner(*_a, **_k):
        return FakeProc()

    assert route_executor(runner=runner)["recommendedExecutor"] == "codex"
