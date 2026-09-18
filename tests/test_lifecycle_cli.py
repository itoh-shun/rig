"""Lifecycle entry points preserve explicit operator decisions and draft semantics."""
import json
import os
from pathlib import Path
import re
import subprocess
import sys

import pytest


class Output:
    def __init__(self):
        self.lines = []

    def out(self, text):
        self.lines.append(text)

    def err(self, text):
        self.lines.append(text)


def test_template_is_valid_draft_and_never_silently_approves():
    from rig_workbench.orchestrate.lifecycle_commands import cmd_lifecycle
    from rig_workbench.orchestrate.lifecycle_policy import validate_plan, readiness
    out = Output()
    cmd_lifecycle(['template', '--mode', 'iterative', '--unit', 'login', '--unit', 'search'], out=out)
    plan = json.loads(out.lines[0])
    validate_plan(plan)
    assert plan['mode'] == 'iterative'
    assert [u['id'] for u in plan['units']] == ['login', 'search']
    assert not readiness(plan, [], 'login', [])['allowed']


@pytest.mark.parametrize('args', [
    ['template', '--unit', 'x'],
    ['template', '--mode', 'scrum', '--unit', 'x'],
    ['template', '--mode', 'waterfall', '--unit', 'x', '--unit', 'x'],
    ['init', 'plan.json', '--out', 'state.json'],
    ['decide', 'state.json', '--decision', 'approve'],
])
def test_incomplete_or_unknown_lifecycle_input_is_refused(args):
    from rig_workbench.orchestrate.lifecycle_commands import cmd_lifecycle
    with pytest.raises(SystemExit) as stopped:
        cmd_lifecycle(args, out=Output())
    assert stopped.value.code == 2


def test_status_exposes_review_binding_without_plan_prose():
    from rig_workbench.orchestrate.lifecycle_commands import _template
    from rig_workbench.orchestrate.lifecycle_status import lifecycle_snapshot
    plan = _template('iterative', ['first', 'later'])
    plan['objective'] = 'PRIVATE REQUIREMENT'
    state = {'step_state': {'first': {'status': 'passed'}, 'later': {'status': 'pending'}},
             'deterministic_runtime': {'lifecycle': {'revision': 4, 'plan': plan, 'decisions': []}}}
    view = lifecycle_snapshot(state)
    assert view['revision'] == 4
    assert len(view['plan_digest']) == 64
    assert len(view['stages']) == 5
    assert view['next_action'] == 'review_plan_and_record_decisions'
    assert 'PRIVATE REQUIREMENT' not in json.dumps(view)
    assert view['units'][1]['reasons']


def test_duplicate_json_plan_key_is_rejected(tmp_path):
    from rig_workbench.orchestrate.lifecycle_commands import _read_plan
    path = tmp_path / 'plan.json'
    path.write_text('{"mode":"waterfall","mode":"iterative"}', encoding='utf-8')
    with pytest.raises(ValueError, match='duplicate'):
        _read_plan(path)


def test_status_latest_rejection_reason_is_bounded_safe_and_freshness_explicit():
    from rig_workbench.orchestrate.lifecycle_commands import _template
    from rig_workbench.orchestrate.lifecycle_policy import decision_digest
    from rig_workbench.orchestrate.lifecycle_status import lifecycle_snapshot, render_lifecycle
    plan = _template('waterfall', ['one'])
    digest = decision_digest(plan, 'shared', 'shared')
    decisions = [
        {'id': '1', 'scope': 'shared', 'stage': 'shared', 'digest': digest,
         'actor': 'operator', 'decision': 'approve', 'reason': 'Old approval'},
        {'id': '2', 'scope': 'shared', 'stage': 'shared', 'digest': digest,
         'actor': 'operator', 'decision': 'reject', 'reason': 'Fix\ninterface\t\u202e ' + 'x' * 600},
    ]
    state = {'step_state': {'one': {'status': 'pending'}}, 'deterministic_runtime': {
        'phase': 'UPSTREAM', 'lifecycle': {'revision': 1, 'plan': plan, 'decisions': decisions}}}
    view = lifecycle_snapshot(state)
    stage = view['stages'][0]
    assert stage['latest_decision'] == 'reject'
    assert stage['decision_id'] == '2'
    assert stage['digest_current'] is True
    assert stage['reason'].startswith('Fix interface ')
    assert len(stage['reason']) <= 320
    assert stage['reason_truncated'] is True
    assert view['stages'][1]['latest_decision'] is None
    out = Output()
    render_lifecycle(view, out)
    assert 'latest=reject' in out.lines[1]
    assert all('\n' not in line and '\x1b' not in line and '\u202e' not in line for line in out.lines)
    plan['objective'] = 'Changed objective'
    stale = lifecycle_snapshot(state)['stages'][0]
    assert stale['digest'] != digest
    assert stale['digest_current'] is False
    assert stale['latest_decision'] == 'reject'


@pytest.mark.parametrize(('phase', 'stopped', 'expected'), [
    ('BLOCKED', {'kind': 'BLOCKED'}, 'inspect_stop'),
    ('ESCALATE', {'kind': 'ESCALATE'}, 'inspect_stop'),
    ('GENERATE_INFLIGHT', None, 'resolve_interrupted_operation'),
])
def test_saved_stop_or_inflight_never_suggests_blind_resume(phase, stopped, expected):
    from rig_workbench.orchestrate.lifecycle_commands import _template
    from rig_workbench.orchestrate.lifecycle_status import lifecycle_snapshot
    state = {'stopped': stopped, 'step_state': {'one': {'status': 'pending'}},
             'deterministic_runtime': {'phase': phase, 'lifecycle': {
                 'revision': 1, 'plan': _template('waterfall', ['one']), 'decisions': []}}}
    assert lifecycle_snapshot(state)['next_action'] == expected


@pytest.mark.parametrize('marker', [1, 2, None, False])
def test_lifecycle_marker_without_runtime_never_loads_as_legacy(tmp_path, marker):
    from rig_workbench.orchestrate.runstate import load_state, new_state
    state = new_state('legacy-shaped', [{'id': 'one', 'instruction': 'work', 'checks': ['true']}], None)
    state['lifecycle_schema_version'] = marker
    path = tmp_path / 'state.json'
    path.write_text(json.dumps(state), encoding='utf-8')
    with pytest.raises(ValueError, match='runtime marker'):
        load_state(path)


@pytest.mark.parametrize('bound', [False, True])
def test_real_lifecycle_cli_draft_decisions_revision_and_execution(tmp_path, bound):
    from rig_workbench.orchestrate.lifecycle_commands import _template
    root = Path(__file__).resolve().parents[1]
    workspace = tmp_path / 'repo'
    workspace.mkdir()
    subprocess.run(['git', 'init', '-q', str(workspace)], check=True)
    subprocess.run(['git', '-C', str(workspace), '-c', 'user.name=Fixture',
                    '-c', 'user.email=fixture@example.invalid', 'commit', '--allow-empty', '-qm', 'fixture'], check=True)
    env = dict(os.environ, PYTHONPATH=str(root), RIG_HOME=str(root),
               RIG_WORKTREE_ROOT=str(tmp_path / 'worktrees'))

    def wb(*args):
        return subprocess.run([sys.executable, str(root / 'scripts/workbench.py'), *args],
                              cwd=workspace, env=env, capture_output=True, text=True, timeout=40)

    isolation = ['--isolate']
    if bound:
        made = wb('new', 'lifecycle fixture', '--type', 'documentation', '--slug', 'lifecycle')
        assert made.returncode == 0, made.stdout + made.stderr
        task_id = re.search(r'task_id: (\S+)', made.stdout).group(1)
        isolation = ['--deterministic-task', task_id]

    def refuse_force():
        if bound:
            before = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=workspace)
            accepted = wb('accept', task_id, '--force')
            assert accepted.returncode != 0
            assert 'deterministic' in accepted.stdout + accepted.stderr
            assert subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=workspace) == before

    def cli(*args):
        return subprocess.run([sys.executable, str(root / 'scripts/orchestrate.py'), *args],
                              cwd=workspace, env=env, capture_output=True, text=True, timeout=40)

    proposal = _template('waterfall', ['one'])
    plan_file, state_file = tmp_path / 'plan.json', tmp_path / 'run.json'
    plan_file.write_text(json.dumps(proposal), encoding='utf-8')
    created = cli('lifecycle', 'init', str(plan_file), '--out', str(state_file), *isolation,
                  '--provider', 'mock', '--verifier-provider', 'mock')
    assert created.returncode == 0, created.stdout + created.stderr
    state = json.loads(state_file.read_text())
    assert state['deterministic_runtime']['phase'] == 'UPSTREAM'
    assert state['deterministic_runtime']['operations'] == []
    refuse_force()
    snapshot = json.loads(cli('status', str(state_file), '--json').stdout)['lifecycle']
    blocked = cli('resume', str(state_file), '--progress')
    assert blocked.returncode != 0
    assert 'review_plan_and_record_decisions' in blocked.stderr
    assert json.loads(state_file.read_text())['deterministic_runtime']['operations'] == []

    unit = proposal['units'][0]
    unit['requirements'] = [{'id': 'r', 'text': 'Behavior', 'acceptance': [
        {'id': 'a', 'text': 'Correct behavior', 'check_ids': ['c']}]}]
    unit['design'] = {'summary': 'Implement behavior', 'requirement_ids': ['r']}
    unit['checks'] = [{'id': 'c', 'command': 'true'}]
    proposal['integration_checks'] = [{'id': 'all', 'command': 'true'}]
    plan_file.write_text(json.dumps(proposal), encoding='utf-8')
    revised = cli('lifecycle', 'revise', str(state_file), '--plan', str(plan_file),
                  '--revision', '1', '--digest', snapshot['plan_digest'], '--actor', 'operator', '--reason', 'Resolve draft')
    assert revised.returncode == 0, revised.stdout + revised.stderr
    snapshot = json.loads(cli('lifecycle', 'status', str(state_file), '--json').stdout)['lifecycle']
    assert snapshot['revision'] == 2
    refuse_force()
    before = state_file.read_bytes()
    stale = cli('lifecycle', 'decide', str(state_file), '--scope', 'shared', '--stage', 'shared',
                '--revision', '1', '--digest', snapshot['stages'][0]['digest'], '--decision', 'approve',
                '--actor', 'operator', '--reason', 'Old displayed version')
    assert stale.returncode == 2
    assert state_file.read_bytes() == before
    for stage in snapshot['stages']:
        decided = cli('lifecycle', 'decide', str(state_file), '--scope', stage['scope'], '--stage', stage['stage'],
                      '--revision', '2', '--digest', stage['digest'], '--decision', 'approve',
                      '--actor', 'operator', '--reason', 'Reviewed current proposal')
        assert decided.returncode == 0, decided.stdout + decided.stderr
    completed = cli('resume', str(state_file), '--progress')
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert 'outcome=DONE' in completed.stderr
    assert json.loads(state_file.read_text())['deterministic_runtime']['phase'] == 'DONE'
    snapshot = json.loads(cli('status', str(state_file), '--json').stdout)['lifecycle']
    proposal['objective'] = 'Revised release objective'
    plan_file.write_text(json.dumps(proposal), encoding='utf-8')
    changed = cli('lifecycle', 'revise', str(state_file), '--plan', str(plan_file),
                  '--revision', '2', '--digest', snapshot['plan_digest'], '--actor', 'operator', '--reason', 'Changed after DONE')
    assert changed.returncode == 0, changed.stdout + changed.stderr
    assert json.loads(state_file.read_text())['done'] is False
    refuse_force()
