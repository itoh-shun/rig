"""Real CLI children must never inherit the MCP bearer credential."""
import json
import os
import pathlib
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("entry", ["orchestrate", "public", "doctor"])
def test_cli_entry_removes_token_before_running_checks_shell(tmp_path, entry):
    # A fresh parent avoids a process-local holder persisting between pytest cases.
    script = '''
import json, pathlib, shlex, sys
from rig_workbench.orchestrate import commands, config
from rig_workbench.orchestrate.orchestrators.config import OrchestratorConfig
from rig_workbench.orchestrate.orchestrators.selection import resolve_settings
config.INVOCATION_CWD = pathlib.Path(sys.argv[2])
child_code = 'import json, os, pathlib; pathlib.Path("child-env.json").write_text(json.dumps(dict(os.environ)))'
check = shlex.quote(sys.executable) + ' -c ' + shlex.quote(child_code)
def inspect(*args, **kwargs):
    assert commands._run_checks([check])[0]['ok']
    assert resolve_settings(OrchestratorConfig()).token == 'private-cli-token'
    from rig_workbench.orchestrate.orchestrators.bridge import safe_external_ref
    assert safe_external_ref({'run_id': 'private-cli-token'}) == {}
if sys.argv[1] == 'doctor':
    from rig_workbench import doctor
    doctor.load_manifest = lambda **kw: {}
    def report(env):
        inspect()
        return {'session': {'observed': False, 'present': None}, 'cli': {'observed': False}}
    doctor.orca_report = report
    assert doctor.main(['--json', '--orchestrator', 'native']) == 0
else:
    from rig_workbench.orchestrate import cli
    cli.COMMANDS['check'] = inspect
    if sys.argv[1] == 'public':
        from rig_workbench import cli as public
        sys.argv = ['rig-wb', 'check']
        public.main()
    else:
        sys.argv = ['orchestrate.py', 'check']
        cli.main()
'''
    env = {key: value for key, value in os.environ.items() if not key.startswith("RIG_T3_")}
    env["RIG_T3_MCP_TOKEN"] = "private-cli-token"
    result = subprocess.run([sys.executable, "-c", script, entry, str(tmp_path)],
                            cwd=ROOT, env=env, text=True, capture_output=True)
    assert result.returncode == 0, result.stdout + result.stderr
    inherited = json.loads((tmp_path / "child-env.json").read_text())
    assert "RIG_T3_MCP_TOKEN" not in inherited
    assert "private-cli-token" not in result.stdout + result.stderr
    assert "PATH" in inherited


def test_direct_cmd_run_removes_token_before_checks_shell(tmp_path):
    # cmd_run is called without main() by pack invoke and selftest; it must capture itself.
    script = '''
import json, pathlib, shlex, sys
from rig_workbench.orchestrate import commands, config
from rig_workbench.orchestrate.orchestrators import credentials
config.INVOCATION_CWD = pathlib.Path(sys.argv[1])
child_code = 'import json, os, pathlib; pathlib.Path("child-env.json").write_text(json.dumps(dict(os.environ)))'
check = shlex.quote(sys.executable) + ' -c ' + shlex.quote(child_code)
def stop(*args, **kwargs):
    assert commands._run_checks([check])[0]['ok']
    raise SystemExit(0)
commands._validate_run_args = stop
try:
    commands.cmd_run(['--goal', 'x'])
except SystemExit:
    pass
credentials.capture_t3_token()
assert credentials.t3_token() == 'direct-entry-token'
'''
    env = {key: value for key, value in os.environ.items() if not key.startswith("RIG_T3_")}
    env["RIG_T3_MCP_TOKEN"] = "direct-entry-token"
    result = subprocess.run([sys.executable, "-c", script, str(tmp_path)],
                            cwd=ROOT, env=env, text=True, capture_output=True)
    assert result.returncode == 0, result.stdout + result.stderr
    inherited = json.loads((tmp_path / "child-env.json").read_text())
    assert "RIG_T3_MCP_TOKEN" not in inherited
    assert "direct-entry-token" not in result.stdout + result.stderr


def _fresh(script, env_token="import-token", *args):
    env = {key: value for key, value in os.environ.items() if not key.startswith("RIG_T3_")}
    if env_token is not None:
        env["RIG_T3_MCP_TOKEN"] = env_token
    return subprocess.run([sys.executable, "-c", script, *args], cwd=ROOT, env=env,
                          text=True, capture_output=True)


@pytest.mark.parametrize("module", ["commands", "providers"])
def test_importing_removes_token_before_any_function_runs(module):
    # Library callers that reach cmd_resume (or a provider) through a decorator that reads
    # state and launches git first must already be clean at import time.
    script = f'''
import os
from rig_workbench.orchestrate import {module}
assert "RIG_T3_MCP_TOKEN" not in os.environ
from rig_workbench.orchestrate.orchestrators import credentials
assert credentials.t3_token() == "import-token"
'''
    result = _fresh(script)
    assert result.returncode == 0, result.stdout + result.stderr


def test_empty_env_value_keeps_holder_and_is_removed(monkeypatch):
    from rig_workbench.orchestrate.orchestrators import credentials
    monkeypatch.setattr(credentials, "_token", "A")
    for blank in ("", "   "):
        monkeypatch.setenv("RIG_T3_MCP_TOKEN", blank)
        assert credentials.t3_token() == "A"
        assert "RIG_T3_MCP_TOKEN" not in os.environ
    monkeypatch.setattr(credentials, "_token", None)
    monkeypatch.setenv("RIG_T3_MCP_TOKEN", "")
    assert credentials.t3_token() is None
    assert "RIG_T3_MCP_TOKEN" not in os.environ


def test_capture_and_read_share_one_lock(monkeypatch):
    # Deterministic stand-in for a race: if either path runs without the shared lock,
    # the recorded lock state is False.
    from rig_workbench.orchestrate.orchestrators import credentials
    held = []
    real_pop = os.environ.pop

    def pop(key, *default):
        held.append(credentials._LOCK.locked())
        return real_pop(key, *default)

    monkeypatch.setattr(credentials, "_token", None)
    monkeypatch.setattr(os.environ, "pop", pop)
    monkeypatch.setenv("RIG_T3_MCP_TOKEN", "x")
    credentials.t3_token()
    monkeypatch.setenv("RIG_T3_MCP_TOKEN", "y")
    credentials.capture_t3_token()
    assert held == [True, True]


def test_concurrent_reads_never_return_a_stale_token(monkeypatch):
    import threading
    from rig_workbench.orchestrate.orchestrators import credentials
    monkeypatch.setattr(credentials, "_token", "A")
    monkeypatch.delenv("RIG_T3_MCP_TOKEN", raising=False)
    real_pop = os.environ.pop
    results = {}

    def slow_pop(key, *default):
        value = real_pop(key, *default)
        if value == "B":
            # Thread 1 has taken B but not yet published it; thread 2 runs meanwhile.
            threading.Event().wait(0.1)
        return value

    monkeypatch.setattr(os.environ, "pop", slow_pop)
    os.environ["RIG_T3_MCP_TOKEN"] = "B"
    first = threading.Thread(target=lambda: results.setdefault("first", credentials.t3_token()))
    second = threading.Thread(target=lambda: results.setdefault("second", credentials.t3_token()))
    first.start()
    threading.Event().wait(0.03)
    second.start()
    first.join()
    second.join()
    assert results == {"first": "B", "second": "B"}
