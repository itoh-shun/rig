"""Real CLI boundary tests for the explicitly selected strict runner."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]


def cli(cwd, *args):
    env = dict(os.environ, PYTHONPATH=str(ROOT), RIG_HOME=str(ROOT))
    return subprocess.run([sys.executable, str(ROOT / 'scripts/orchestrate.py'), *args], cwd=cwd, env=env, text=True, capture_output=True, timeout=30)


@pytest.fixture
def recipe(tmp_path):
    path = tmp_path / 'strict.md'
    path.write_text('''---
name: strict-fixture
steps:
  - id: implement
    instruction: implement
    gate: acceptance-gate
    acceptance: ["requested behavior verified"]
    checks: []
---
''')
    return path


def test_strict_requires_isolation_before_provider(recipe, tmp_path):
    result = cli(tmp_path, 'run', str(recipe), '--deterministic', '--provider', 'mock', '--check', 'true')
    assert result.returncode != 0
    assert '--isolate' in result.stdout + result.stderr
    assert not (tmp_path / 'run-state.json').exists()


def test_strict_unknown_options_are_not_silently_ignored(recipe, tmp_path):
    result = cli(tmp_path, 'run', str(recipe), '--deterministic', '--isolate', '--provider', 'mock', '--typo-option')
    assert result.returncode != 0
    assert '--typo-option' in result.stdout + result.stderr


@pytest.mark.parametrize('command,extra', [('next', []), ('check', []), ('verdict', ['--by', 'ai', '--pass']), ('approve', [])])
def test_manual_commands_cannot_mutate_strict_state(tmp_path, command, extra):
    path = tmp_path / 'state.json'
    data = {'deterministic_runtime': {'schema_version': 999}}
    path.write_text(json.dumps(data))
    args = [command, str(path), *extra] if command != 'approve' else [command, 'implement', str(path)]
    result = cli(tmp_path, *args)
    assert result.returncode != 0
    assert json.loads(path.read_text()) == data
    assert 'deterministic' in result.stdout + result.stderr

@pytest.fixture
def task(tmp_path):
    import re
    repo = tmp_path / 'repo'
    repo.mkdir()
    for args in [('init', '-q'), ('config', 'user.name', 'test'), ('config', 'user.email', 'test@example.com')]:
        subprocess.run(['git', *args], cwd=repo, check=True)
    (repo / 'app.py').write_text('value = 1\n')
    subprocess.run(['git', 'add', '.'], cwd=repo, check=True)
    subprocess.run(['git', 'commit', '-qm', 'base'], cwd=repo, check=True)
    env = dict(os.environ, PYTHONPATH=str(ROOT), RIG_HOME=str(ROOT), RIG_WORKTREE_ROOT=str(tmp_path / 'worktrees'))

    def wb(*args):
        return subprocess.run([sys.executable, str(ROOT / 'scripts/workbench.py'), *args], cwd=repo, env=env, text=True, capture_output=True, timeout=60)

    made = wb('new', 'strict fixture', '--type', 'documentation', '--slug', 'strict')
    assert made.returncode == 0, made.stdout + made.stderr
    task_id = re.search(r'task_id: (\S+)', made.stdout).group(1)
    directory = repo / '.rig' / 'runs' / task_id
    return repo, task_id, directory, wb


@pytest.mark.parametrize('missing', ['marker', 'sidecar', 'state'])
def test_force_cannot_bypass_missing_strict_binding(task, missing, tmp_path):
    repo, task_id, directory, wb = task
    path = directory / 'task.json'
    record = json.loads(path.read_text())
    marker = {'schema_version': 1, 'run_id': 'strict-run', 'state_path': str(tmp_path / 'absent-state.json')}
    if missing != 'marker':
        record['deterministic_binding'] = marker
        path.write_text(json.dumps(record))
    if missing != 'sidecar':
        (directory / 'deterministic-binding.json').write_text(json.dumps(marker))
    before = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=repo)
    result = wb('accept', task_id, '--force')
    assert result.returncode != 0
    assert 'deterministic' in result.stdout + result.stderr
    assert subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=repo) == before
    assert json.loads(path.read_text())['status'] != 'accepted'


def provider_command():
    import shlex
    code = '''import json,sys,pathlib
r=json.load(sys.stdin)
if r['operation']=='GENERATE': pathlib.Path('app.py').write_text('value = 2\\n')
print(json.dumps({'status':'PASS','criteria':[{'id':i,'status':'PASS'} for i in r.get('criteria_ids',[])]}))
'''
    return 'python3 -c ' + shlex.quote(code)


def test_real_cli_strict_check_resume_and_stale_acceptance(recipe, task, tmp_path):
    repo, task_id, directory, wb = task
    state_path = tmp_path / 'strict-state.json'
    result = cli(repo, 'run', str(recipe), '--deterministic-task', task_id,
                 '--provider', 'cmd', '--provider-cmd', provider_command(),
                 '--check', "python3 -c \"assert open('app.py').read() == 'value = 2\\n'\"",
                 '--out', str(state_path))
    assert result.returncode == 0, result.stdout + result.stderr
    state = json.loads(state_path.read_text())
    assert state['deterministic_runtime']['phase'] == 'DONE'
    assert state['steps'][-1]['checks']
    worktree = Path(state['deterministic_runtime']['workspace'])
    assert worktree.exists()
    assert (repo / 'app.py').read_text() == 'value = 1\n'
    assert (worktree / 'app.py').read_text() == 'value = 2\n'
    marker = json.loads((directory / 'task.json').read_text())['deterministic_binding']
    assert marker == json.loads((directory / 'deterministic-binding.json').read_text())
    for command, extra in [('next', []), ('check', []), ('verdict', ['--by', 'ai', '--pass'])]:
        before = state_path.read_bytes()
        refused = cli(repo, command, str(state_path), *extra)
        assert refused.returncode != 0
        assert state_path.read_bytes() == before
    resumed = cli(repo, 'resume', str(state_path))
    assert resumed.returncode == 0, resumed.stdout + resumed.stderr
    (worktree / 'app.py').write_text('value = 999\n')
    accepted = wb('accept', task_id, '--force')
    assert accepted.returncode != 0
    assert 'deterministic' in accepted.stdout + accepted.stderr
    assert (repo / 'app.py').read_text() == 'value = 1\n'


def test_isolated_strict_run_never_auto_merges_or_removes_worktree(recipe, task, tmp_path):
    repo, _, _, _ = task
    output = tmp_path / 'isolated-state.json'
    result = cli(repo, 'run', str(recipe), '--deterministic', '--isolate',
                 '--provider', 'cmd', '--provider-cmd', provider_command(),
                 '--check', 'true', '--out', str(output))
    assert result.returncode == 0, result.stdout + result.stderr
    state = json.loads(output.read_text())
    workspace = Path(state['isolation']['dir'])
    assert workspace.exists()
    assert (repo / 'app.py').read_text() == 'value = 1\n'
    assert (workspace / 'app.py').read_text() == 'value = 2\n'


def test_failed_initialization_still_binds_task_and_never_runs_provider(recipe, task, tmp_path):
    repo, task_id, directory, wb = task
    output = tmp_path / 'failed-state.json'
    provider_marker = tmp_path / 'provider-ran'
    result = cli(repo, 'run', str(recipe), '--deterministic-task', task_id,
                 '--provider', 'cmd', '--provider-cmd', f'touch {provider_marker}',
                 '--out', str(output))
    assert result.returncode != 0
    assert 'nonempty machine checks' in result.stdout + result.stderr
    assert not provider_marker.exists()
    assert 'deterministic_binding' in json.loads((directory / 'task.json').read_text())
    assert (directory / 'deterministic-binding.json').exists()
    refused = wb('accept', task_id, '--force')
    assert refused.returncode != 0
    assert 'deterministic' in refused.stdout + refused.stderr


def test_dangling_sidecar_cannot_downgrade_to_legacy_acceptance(task, tmp_path):
    _, task_id, directory, wb = task
    (directory / 'deterministic-binding.json').symlink_to(tmp_path / 'nonexistent')
    result = wb('accept', task_id, '--force')
    assert result.returncode != 0
    assert 'deterministic' in result.stdout + result.stderr


def test_real_cli_reject_diagnose_replan_resume_preserves_failures(recipe, task, tmp_path):
    import shlex
    repo, task_id, _, _ = task
    code = '''import json,sys,pathlib
r=json.load(sys.stdin)
op=r['operation']
if op=='GENERATE': pathlib.Path('app.py').write_text('value = '+str(2 if r['attempt']>=3 else 1)+'\\n')
out={'status':'PASS','criteria':[{'id':i,'status':'PASS'} for i in r.get('criteria_ids',[])]}
if op in ('DIAGNOSE','REPLAN'):
 out={'failure_id':r['failure_id'],'failed_check_ids':r['failed_check_ids'],'hypothesis':'Value is wrong','changes':['Set value to 2'],'verification_checks':r['failed_check_ids']}
 if op=='REPLAN': out['plan']='Set the requested value and run exact value assertion'
print(json.dumps(out))
'''
    output = tmp_path / 'recovery-state.json'
    result = cli(repo, 'run', str(recipe), '--deterministic-task', task_id,
                 '--provider', 'cmd', '--provider-cmd', 'python3 -c ' + shlex.quote(code),
                 '--check', "python3 -c \"assert open('app.py').read() == 'value = 2\\n'\"",
                 '--out', str(output), '--max-steps', '3')
    assert result.returncode != 0
    first = json.loads(output.read_text())
    events = first['deterministic_runtime']['units']['implement']['events']
    assert [e['kind'] for e in events] == ['failure']
    resumed = cli(repo, 'resume', str(output))
    assert resumed.returncode == 0, resumed.stdout + resumed.stderr
    final = json.loads(output.read_text())['deterministic_runtime']
    assert final['phase'] == 'DONE'
    assert [e['kind'] for e in final['units']['implement']['events']] == ['failure', 'failure', 'replan']
    assert final['attempt'] == 3
