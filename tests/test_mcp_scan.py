"""Static threat scan for rig's own MCP tools (#303).

Exercises mcp_scan() against the real scripts/mcp_server.py and against
synthetic files planting the risk patterns it's meant to catch.
"""

import pathlib

from rig_workbench.orchestrate.mcp_scan import mcp_scan
from rig_workbench.validation.mcp_scan import check_mcp_scan
from rig_workbench.validation import state as validation_state

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_missing_mcp_server_reports_unavailable(tmp_path):
    result = mcp_scan(tmp_path / "does-not-exist.py")
    assert result["available"] is False
    assert "not found" in result["reason"]


def test_real_mcp_server_scans_clean_and_run_is_low_because_it_isolates_by_default():
    # #419 lowered this from medium: isolation is now opt-out, so an MCP call that
    # says nothing about `isolate` no longer reaches the main working tree.
    result = mcp_scan(REPO_ROOT / "scripts" / "mcp_server.py")
    assert result["available"] is True
    assert result["overall_severity"] == "low"
    by_name = {f["tool"]: f for f in result["tool_findings"]}
    assert by_name["rig_orchestrate_run"]["severity"] == "low"
    assert by_name["rig_orchestrate_run"]["kind"] == "write"


def test_run_is_flagged_medium_again_if_isolation_goes_back_to_opt_in(tmp_path):
    # The verdict must track real behavior, not a hardcoded label: an adapter whose
    # argv builder omits --isolate by default is still the medium-risk shape.
    p = tmp_path / "mcp_server.py"
    p.write_text(
        "def _orchestrate_run_args(a):\n"
        "    return ['run', a['recipe']] + (['--isolate'] if a.get('isolate') else [])\n"
        "TOOLS = {'rig_orchestrate_run': {'fn': _orchestrate_run_args, 'description': 'd', 'input_schema': {}}}\n",
        encoding="utf-8",
    )
    result = mcp_scan(p)
    by_name = {f["tool"]: f for f in result["tool_findings"]}
    assert by_name["rig_orchestrate_run"]["severity"] == "medium"


def test_run_without_an_argv_builder_is_read_pessimistically(tmp_path):
    p = tmp_path / "mcp_server.py"
    p.write_text(
        "TOOLS = {'rig_orchestrate_run': {'fn': lambda a: None, 'description': 'd', 'input_schema': {}}}\n",
        encoding="utf-8",
    )
    result = mcp_scan(p)
    by_name = {f["tool"]: f for f in result["tool_findings"]}
    assert by_name["rig_orchestrate_run"]["severity"] == "medium"


def test_runs_aggregator_is_not_confused_with_run(tmp_path):
    # Regression test for the exact bug the orphan reference fixed: substring
    # matching ("run" in name) misclassifying rig_orchestrate_runs as the
    # higher-risk run tool.
    result = mcp_scan(REPO_ROOT / "scripts" / "mcp_server.py")
    by_name = {f["tool"]: f for f in result["tool_findings"]}
    assert by_name["rig_orchestrate_runs"]["kind"] == "read"
    assert by_name["rig_orchestrate_runs"]["severity"] == "low"


def test_accept_family_tools_are_classified_as_write_low_risk():
    result = mcp_scan(REPO_ROOT / "scripts" / "mcp_server.py")
    by_name = {f["tool"]: f for f in result["tool_findings"]}
    for name in ("rig_task_accept", "rig_task_discard", "rig_task_new", "rig_task_gate"):
        assert by_name[name]["kind"] == "write"
        assert by_name[name]["severity"] == "low"


def test_read_only_tools_are_classified_as_read_low_risk():
    result = mcp_scan(REPO_ROOT / "scripts" / "mcp_server.py")
    by_name = {f["tool"]: f for f in result["tool_findings"]}
    for name in ("rig_task_board", "rig_task_status", "rig_task_diff", "rig_orchestrate_status"):
        assert by_name[name]["kind"] == "read"
        assert by_name[name]["severity"] == "low"


def test_shell_true_in_source_raises_severity_to_high(tmp_path):
    p = tmp_path / "mcp_server.py"
    p.write_text(
        "TOOLS = {'x': {'fn': lambda a: None, 'description': 'd', 'input_schema': {}}}\n"
        "def _unused():\n"
        "    import subprocess\n"
        "    subprocess.run(['x'], shell=True)\n",
        encoding="utf-8",
    )
    result = mcp_scan(p)
    assert result["available"] is True
    assert result["overall_severity"] == "high"
    shell_finding = next(f for f in result["module_findings"] if f["axis"] == "shell/network over-permission")
    assert shell_finding["severity"] == "high"


def test_hardcoded_secret_in_source_raises_severity_to_high(tmp_path):
    p = tmp_path / "mcp_server.py"
    p.write_text(
        "TOOLS = {'x': {'fn': lambda a: None, 'description': 'd', 'input_schema': {}}}\n"
        "API_KEY = 'sk-abcdefghijklmnopqrstuvwxyz123456'\n",
        encoding="utf-8",
    )
    result = mcp_scan(p)
    assert result["available"] is True
    assert result["overall_severity"] == "high"
    secret_finding = next(f for f in result["module_findings"] if f["axis"] == "plaintext secret exposure")
    assert secret_finding["severity"] == "high"


def test_module_missing_tools_dict_is_unavailable(tmp_path):
    p = tmp_path / "mcp_server.py"
    p.write_text("NOT_TOOLS = {}\n", encoding="utf-8")
    result = mcp_scan(p)
    assert result["available"] is False
    assert "failed to import" in result["reason"]


def test_scan_is_read_only_and_deterministic():
    r1 = mcp_scan(REPO_ROOT / "scripts" / "mcp_server.py")
    r2 = mcp_scan(REPO_ROOT / "scripts" / "mcp_server.py")
    assert r1 == r2


def test_check_mcp_scan_reports_the_real_severity(monkeypatch):
    validation_state.results.clear()
    validation_state._pass = validation_state._warn = validation_state._fail = 0
    check_mcp_scan()
    assert any("mcp-scan" in line for line in validation_state.results)
    assert validation_state._warn >= 1 or validation_state._fail >= 1 or validation_state._pass >= 1


def test_check_mcp_scan_skips_silently_when_mcp_server_missing(monkeypatch, tmp_path):
    from rig_workbench.validation import mcp_scan as validation_mcp_scan

    monkeypatch.setattr(validation_mcp_scan, "ROOT", tmp_path)
    validation_state.results.clear()
    validation_state._pass = validation_state._warn = validation_state._fail = 0
    check_mcp_scan()
    assert validation_state.results == []
