"""Progress is observable on pipes and never becomes gate evidence."""
import json
import os
from pathlib import Path
import selectors
import shlex
import subprocess
import sys
import time

import pytest

from rig_workbench.orchestrate.commands import _status_snapshot
from rig_workbench.orchestrate.runstate import new_state, save_state

ROOT = Path(__file__).resolve().parents[1]
SECRET = 'RAW_PROVIDER_SECRET_DO_NOT_STREAM'


def test_status_json_is_read_only_saved_snapshot_without_payloads(tmp_path, step_factory):
    state = new_state('snapshot', [step_factory(id='work', checks=['echo SECRET_COMMAND'])], 'SECRET_GOAL')
    state['providers'] = {'provider_cmd': 'SECRET_PROVIDER'}
    state['step_state']['work']['checks'] = [{'cmd': 'SECRET_COMMAND', 'ok': True}]
    state['step_state']['work']['verdicts'] = [{'by': 'judge', 'ok': False, 'note': 'SECRET_NOTE'}]
    path = tmp_path / 'state.json'
    save_state(state, path)
    before = path.read_bytes()
    result = subprocess.run([sys.executable, str(ROOT / 'scripts/orchestrate.py'), 'status', str(path), '--json'], text=True, capture_output=True, cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    snapshot = json.loads(result.stdout)
    assert snapshot['snapshot'] == 'last_saved'
    assert snapshot['process_liveness'] == 'unknown'
    assert snapshot['steps'][0]['checks'] == {'passed': 1, 'total': 1}
    assert 'SECRET_' not in result.stdout
    assert path.read_bytes() == before


def test_strict_status_reports_saved_phase_attempt_and_evidence_only(tmp_path, step_factory):
    state = new_state('snapshot', [step_factory(id='work')], 'SECRET_GOAL')
    state['deterministic_runtime'] = {'schema_version': 1, 'phase': 'CHECK_INFLIGHT', 'attempt': 3,
        'units': {'work': {'events': [{'kind': 'failure'}], 'evidence': {'evidence': [{'check_id': 'work:1', 'status': 'FAIL', 'exit_code': 1}], 'outputs': [SECRET]}}},
        'provider_config': {'provider_cmd': SECRET}}
    snapshot = _status_snapshot(state, tmp_path / 'state.json')
    assert snapshot['strict']['phase'] == 'CHECK_INFLIGHT'
    assert snapshot['strict']['attempt'] == 3
    assert snapshot['strict']['units'][0]['evidence'] == [{'check_id': 'work:1', 'status': 'FAIL', 'exit_code': 1}]
    assert SECRET not in json.dumps(snapshot)


def test_progress_on_non_tty_arrives_before_provider_finishes_without_raw_output(tmp_path):
    recipe = tmp_path / 'probe.md'
    recipe.write_text('---\nname: progress-probe\nsteps:\n  - id: work\n    instruction: implement\n    checks: []\n---\n')
    marker = tmp_path / 'provider-started'
    state = tmp_path / 'state.json'
    source = f"import pathlib,time; pathlib.Path({str(marker)!r}).write_text('started'); print({SECRET!r},flush=True); time.sleep(2); print('done')"
    env = dict(os.environ, RIG_HOME=str(ROOT), PYTHONPATH=str(ROOT))
    env.pop('PYTHONUNBUFFERED', None)
    process = subprocess.Popen([sys.executable, str(ROOT / 'scripts/orchestrate.py'), 'run', str(recipe), '--provider', 'cmd', '--provider-cmd', 'python3 -c ' + shlex.quote(source), '--out', str(state), '--progress', '--max-steps', '1'], cwd=tmp_path, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ, 'out')
    selector.register(process.stderr, selectors.EVENT_READ, 'err')
    captured = {'out': '', 'err': ''}
    during = None
    deadline = time.monotonic() + 60
    try:
        while selector.get_map():
            for key, _ in selector.select(.05):
                chunk = os.read(key.fileobj.fileno(), 65536)
                if chunk:
                    captured[key.data] += chunk.decode()
                else:
                    selector.unregister(key.fileobj)
            if marker.exists() and during is None:
                during = (captured['err'], state.exists())
            assert time.monotonic() < deadline, 'progress fixture timed out'
        assert process.wait() == 0  # legacy max-step exit contract is preserved
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
        selector.close()
    assert during is not None
    assert 'operation_started' in during[0] and 'GENERATE' in during[0]
    assert not during[1]  # observation must not change legacy checkpoint timing
    assert SECRET not in captured['err']
    assert 'run_finished' in captured['err'] and 'INCOMPLETE' in captured['err']
    assert 'DONE' not in captured['err']
    assert 'run_finished' not in captured['out']
    summary = next(line for line in captured['err'].splitlines() if 'run_finished' in line)
    command = summary.split(' next_command=', 1)[1]
    status = subprocess.run(shlex.split(command), cwd=tmp_path, text=True, capture_output=True)
    assert status.returncode == 0, status.stderr
    assert json.loads(status.stdout)['process_liveness'] == 'unknown'


def test_legacy_resume_progress_describes_one_transition_not_complete_run(tmp_path, step_factory, capsys):
    from rig_workbench.orchestrate.commands import cmd_resume
    state = new_state('resume-test', [step_factory(id='work')], 'SECRET_GOAL')
    path = tmp_path / 'state.json'
    save_state(state, path)
    cmd_resume(['--progress', str(path)])
    captured = capsys.readouterr()
    assert 'outcome=INCOMPLETE' in captured.err
    assert 'next_action=continue_legacy_step' in captured.err
    assert 'GENERATE' not in captured.err
    assert json.loads(path.read_text())['step_state']['work']['status'] == 'running'


def test_text_status_uses_real_strict_evidence_and_does_not_claim_live(tmp_path, step_factory, capsys):
    from rig_workbench.orchestrate.commands import cmd_status
    from rig_workbench.orchestrate.deterministic_runtime import initialize, run_strict
    workspace = tmp_path / 'repo'
    workspace.mkdir()
    subprocess.run(['git', 'init', '-q', str(workspace)], check=True)
    state = new_state('strict-status', [step_factory(id='work', checks=['true'])], None)
    path = tmp_path / 'state.json'
    initialize(state, workspace, path, {'generator': 'mock', 'verifier': 'mock'})
    run_strict(state, path, max_steps=2)
    before = path.read_bytes()
    cmd_status([str(path)])
    output = capsys.readouterr().out
    assert 'phase=FINAL_CHECK' in output
    assert 'work: recorded evidence PASS 1/1' in output
    assert 'checks=0/0' not in output
    assert 'liveness unknown' in output and 'not revalidated' in output
    assert path.read_bytes() == before

    # Continuing through the CLI reports the newly loaded strict terminal state.
    from rig_workbench.orchestrate.commands import cmd_resume
    with pytest.raises(SystemExit) as exited:
        cmd_resume([str(path), '--progress'])
    assert exited.value.code == 0
    resumed = capsys.readouterr()
    assert 'run_finished' in resumed.err and 'outcome=DONE' in resumed.err
    assert 'next_action=inspect_results' in resumed.err


def test_progress_status_command_falls_back_to_executable_module(tmp_path, step_factory, monkeypatch):
    from rig_workbench.orchestrate import commands
    state = new_state('fallback', [step_factory(id='work')], None)
    path = tmp_path / 'state with spaces.json'
    save_state(state, path)
    monkeypatch.setattr(commands.SCRIPT_LOCATOR, 'find', lambda name: None)
    events = []
    commands._progress_summary(events.append, state, path, 'START')
    env = dict(os.environ, PYTHONPATH=str(ROOT))
    result = subprocess.run(shlex.split(events[-1]['next_command']), cwd=tmp_path,
                            env=env, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)['snapshot'] == 'last_saved'
