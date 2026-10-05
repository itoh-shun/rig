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
