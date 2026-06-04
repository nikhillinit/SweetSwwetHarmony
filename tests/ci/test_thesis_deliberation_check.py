from scripts.ci.thesis_deliberation_check import build_deliberation_argv, summarize


def test_build_argv_uses_panel_and_synthesizer():
    argv = build_deliberation_argv(panel="codex,kimi", rounds=2, synthesizer="codex",
                                   task_text="cross-check borderline thesis rows")
    assert argv[:4] == ["-m", "ops.cli", "hermes", "task"]
    assert "deliberate" in argv
    assert "--panel" in argv and "codex,kimi" in argv
    assert "--synthesizer" in argv and "codex" in argv


def test_summarize_reports_advisory_and_never_raises():
    s = summarize(returncode=1, stdout="", stderr="panel unavailable")
    assert s["advisory"] is True
    assert s["ran"] is False


def test_summarize_marks_ran_on_success():
    s = summarize(returncode=0, stdout="consensus reached", stderr="")
    assert s["ran"] is True
    assert s["advisory"] is True
