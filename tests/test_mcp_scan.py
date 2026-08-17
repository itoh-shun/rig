"""Static threat scan for rig's own MCP tools (#303).

Exercises mcp_scan() against the real scripts/mcp_server.py and against
synthetic files planting the risk patterns it's meant to catch.
"""

import pathlib

import pytest

from rig_workbench.orchestrate.mcp_scan import mcp_scan
from rig_workbench.validation.mcp_scan import check_mcp_scan
from rig_workbench.validation import state as validation_state

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_missing_mcp_server_reports_unavailable(tmp_path):
    result = mcp_scan(tmp_path / "does-not-exist.py")
    assert result["available"] is False
    assert "not found" in result["reason"]


def test_real_mcp_server_scans_clean_and_run_is_low_because_it_isolates_by_default():
    result = mcp_scan(REPO_ROOT / "scripts" / "mcp_server.py")
    assert result["available"] is True
    assert result["overall_severity"] == "low"
    by_name = {f["tool"]: f for f in result["tool_findings"]}
    assert by_name["rig_orchestrate_run"]["kind"] == "write"
    # LOW here is a reading of the adapter, not a standing claim about it (#419): the
    # shipped default isolates, and the verdict has to say what still gets you out of it.
    assert by_name["rig_orchestrate_run"]["severity"] == "low"
    assert "isolate: false" in by_name["rig_orchestrate_run"]["auditor_verdict"]


def test_run_is_medium_again_when_the_adapter_stops_isolating_by_default(tmp_path):
    # The sensor has to be a measurement, not a rubber stamp: put the pre-#419 default
    # back and the verdict must return to MEDIUM on its own. Without this, changing the
    # adapter back would leave mcp-scan reporting an isolation it never checked.
    p = tmp_path / "mcp_server.py"
    p.write_text(
        "TOOLS = {'rig_orchestrate_run': {'fn': lambda a: None, 'description': 'd', 'input_schema': {}}}\n"
        "def t_orchestrate_run(a):\n"
        "    return _opt([], '--isolate', a.get('isolate'))\n",
        encoding="utf-8",
    )
    result = mcp_scan(p)
    by_name = {f["tool"]: f for f in result["tool_findings"]}
    assert by_name["rig_orchestrate_run"]["severity"] == "medium"
    assert result["overall_severity"] == "medium"


@pytest.mark.parametrize("near_miss", [
    "a.get('isolate') is not None",   # isolates when absent, but an explicit null gets through
    "a.get('isolate') is not True",   # inverted: isolates only when the caller opted out
    "a.get('isolate') == False",      # 0 and 0.0 now opt out too, which the schema never promised
])
def test_a_near_miss_default_is_not_vouched_for(tmp_path, near_miss):
    # Each of these reads like the safe default at a glance and isn't one. The scan
    # clears a tool, so "close enough" has to come out as MEDIUM: an unrecognized
    # spelling costs a WARN, and that is the direction to be wrong in.
    p = tmp_path / "mcp_server.py"
    p.write_text(
        "TOOLS = {'rig_orchestrate_run': {'fn': lambda a: None, 'description': 'd', 'input_schema': {}}}\n"
        "def t_orchestrate_run(a):\n"
        f"    return _opt([], '--isolate', {near_miss})\n",
        encoding="utf-8",
    )
    result = mcp_scan(p)
    by_name = {f["tool"]: f for f in result["tool_findings"]}
    assert by_name["rig_orchestrate_run"]["severity"] == "medium"


def test_a_matching_line_outside_the_run_adapter_does_not_buy_a_low(tmp_path):
    # The check clears a tool, so it has to be hard to satisfy by accident. A comment
    # and an unrelated helper both spell the safe default here, while the tool itself
    # still has the pre-#419 one — reading this file as LOW is the failure that matters.
    p = tmp_path / "mcp_server.py"
    p.write_text(
        "TOOLS = {'rig_orchestrate_run': {'fn': lambda a: None, 'description': 'd', 'input_schema': {}}}\n"
        "def t_task_new(a):\n"
        "    return _opt([], '--isolate', a.get('isolate', True))\n"
        "def t_orchestrate_run(a):\n"
        "    # isolation: a.get('isolate') is not False\n"
        "    return _opt([], '--isolate', a.get('isolate'))\n",
        encoding="utf-8",
    )
    result = mcp_scan(p)
    by_name = {f["tool"]: f for f in result["tool_findings"]}
    assert by_name["rig_orchestrate_run"]["severity"] == "medium"


@pytest.mark.parametrize("body", [
    # A safe definition shadowed by an unsafe one: the second is what import leaves behind.
    "def t_orchestrate_run(a):\n"
    "    return _opt([], '--isolate', a.get('isolate') is not False)\n"
    "def t_orchestrate_run(a):\n"
    "    return _opt([], '--isolate', a.get('isolate'))\n",
    # One safe branch does not make the function safe.
    "def t_orchestrate_run(a):\n"
    "    if a.get('recipe'):\n"
    "        return _opt([], '--isolate', a.get('isolate') is not False)\n"
    "    return _opt([], '--isolate', a.get('isolate'))\n",
    # A same-named function nested somewhere else is not the one MCP calls.
    "def _helper():\n"
    "    def t_orchestrate_run(a):\n"
    "        return _opt([], '--isolate', a.get('isolate') is not False)\n"
    "def t_orchestrate_run(a):\n"
    "    return _opt([], '--isolate', a.get('isolate'))\n",
])
def test_a_safe_looking_path_next_to_an_unsafe_one_does_not_buy_a_low(tmp_path, body):
    p = tmp_path / "mcp_server.py"
    p.write_text(
        "TOOLS = {'rig_orchestrate_run': {'fn': lambda a: None, 'description': 'd', 'input_schema': {}}}\n" + body,
        encoding="utf-8",
    )
    result = mcp_scan(p)
    by_name = {f["tool"]: f for f in result["tool_findings"]}
    assert by_name["rig_orchestrate_run"]["severity"] == "medium"


def test_the_other_accepted_default_spelling_also_reads_as_isolating(tmp_path):
    # `a.get("isolate", True)` — the spelling #419 proposed — means the same thing as the
    # one that shipped. The check keys on intent, not on one line of source.
    p = tmp_path / "mcp_server.py"
    p.write_text(
        "TOOLS = {'rig_orchestrate_run': {'fn': lambda a: None, 'description': 'd', 'input_schema': {}}}\n"
        "def t_orchestrate_run(a):\n"
        "    return _opt([], '--isolate', a.get('isolate', True))\n",
        encoding="utf-8",
    )
    result = mcp_scan(p)
    by_name = {f["tool"]: f for f in result["tool_findings"]}
    assert by_name["rig_orchestrate_run"]["severity"] == "low"


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


def test_check_mcp_scan_emits_a_validate_line_for_the_real_scan(monkeypatch):
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
