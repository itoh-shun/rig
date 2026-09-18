import copy

import pytest

from rig_workbench.orchestrate import deterministic_runtime as runtime
from rig_workbench.orchestrate.lifecycle_policy import compile_steps, decision_digest, plan_digest
from test_deterministic_runtime import FakeIO
from test_lifecycle_policy import plan


@pytest.fixture
def lifecycle(monkeypatch, tmp_path):
    monkeypatch.setattr(runtime, 'StrictIO', FakeIO)
    monkeypatch.setattr(runtime, '_load_state', lambda path: copy.deepcopy(FakeIO.saved[str(path)]))
    FakeIO.saved, FakeIO.calls, FakeIO.content = {}, [], 'initial'
    FakeIO.failing, FakeIO.bad_diagnosis, FakeIO.interrupt = 0, False, None
    p = plan()
    steps = compile_steps(p)
    state = {'run_id': 'lifecycle-test', 'goal': p['objective'], 'steps': steps,
             'step_state': {s['id']: {'status': 'pending', 'retries': 0} for s in steps},
             'done': False, 'stopped': None, 'cursor': 0, 'history': []}
    workspace = tmp_path / 'repo'
    workspace.mkdir()
    path = tmp_path / 'private' / 'run.json'
    runtime.initialize(state, workspace, path, {'generator': 'mock', 'verifier': 'mock'}, lifecycle_plan=p)
    return state, path, workspace


def decide(path, scope, stage, decision='approve'):
    lc = FakeIO.saved[str(path)]['deterministic_runtime']['lifecycle']
    runtime.lifecycle_decide(path, scope=scope, stage=stage, digest=decision_digest(lc['plan'], scope, stage),
                             revision=lc['revision'], decision=decision, actor='operator', reason='Reviewed')


def approve_all(path):
    decide(path, 'shared', 'shared')
    for unit in FakeIO.saved[str(path)]['deterministic_runtime']['lifecycle']['plan']['units']:
        decide(path, unit['id'], 'requirements')
        decide(path, unit['id'], 'design')


def test_initialization_and_resume_do_not_call_provider_before_approval(lifecycle):
    state, path, _ = lifecycle
    assert state['deterministic_runtime']['phase'] == 'UPSTREAM'
    assert runtime.resume_strict(path) == 'UPSTREAM'
    assert FakeIO.calls == []


def test_iterative_stops_at_next_unapproved_unit_then_finishes_integration(lifecycle):
    _, path, workspace = lifecycle
    decide(path, 'shared', 'shared')
    decide(path, 'one', 'requirements')
    decide(path, 'one', 'design')
    assert runtime.resume_strict(path) == 'UPSTREAM'
    assert [x[0] for x in FakeIO.calls] == ['GENERATE', 'CHECK', 'VERIFY']
    for sid in ('two', 'three'):
        decide(path, sid, 'requirements')
        decide(path, sid, 'design')
    assert runtime.resume_strict(path) == 'DONE'
    saved = FakeIO.saved[str(path)]
    assert len([x for x in FakeIO.calls if x[0] == 'GENERATE']) == 3
    assert len(saved['deterministic_runtime']['final_evidence']['evidence']) == 4
    runtime.validate_acceptance(path, workspace)


def test_revision_invalidates_done_and_affected_dependents(lifecycle):
    _, path, workspace = lifecycle
    approve_all(path)
    assert runtime.resume_strict(path) == 'DONE'
    before = copy.deepcopy(FakeIO.saved[str(path)])
    lc = before['deterministic_runtime']['lifecycle']
    changed = copy.deepcopy(lc['plan'])
    changed['units'][0]['design']['summary'] += ' revised'
    runtime.lifecycle_revise(path, changed, revision=1, digest=plan_digest(lc['plan']), actor='operator', reason='Correct design')
    after = FakeIO.saved[str(path)]
    assert after['step_state']['one']['status'] == 'pending'
    assert after['step_state']['two']['status'] == 'pending'
    assert after['step_state']['three']['status'] == 'passed'
    assert after['deterministic_runtime']['operations'] == before['deterministic_runtime']['operations']
    with pytest.raises(ValueError):
        runtime.validate_acceptance(path, workspace)
    assert runtime.resume_strict(path) == 'UPSTREAM'


@pytest.mark.parametrize('kind', ['remove', 'downgrade', 'steps'])
def test_schema_and_compiled_contract_tampering_rejected(lifecycle, kind):
    state, _, _ = lifecycle
    if kind == 'remove':
        del state['deterministic_runtime']['lifecycle']
    elif kind == 'downgrade':
        state['deterministic_runtime']['schema_version'] = 1
    else:
        state['steps'][0]['checks'] = ['false']
    with pytest.raises(ValueError):
        runtime.validate_state(state)


def test_done_rejection_blocks_acceptance_and_resume(lifecycle):
    _, path, workspace = lifecycle
    approve_all(path)
    assert runtime.resume_strict(path) == 'DONE'
    decide(path, 'shared', 'shared', 'reject')
    count = len(FakeIO.calls)
    assert runtime.resume_strict(path) == 'UPSTREAM'
    assert len(FakeIO.calls) == count
    with pytest.raises(ValueError):
        runtime.validate_acceptance(path, workspace)
    assert FakeIO.saved[str(path)]['deterministic_runtime']['superseded_final_evidence']


@pytest.mark.parametrize('field,value', [('revision', True), ('revision', 0), ('digest', '0' * 64)])
def test_stale_decision_cannot_modify_state(lifecycle, field, value):
    _, path, _ = lifecycle
    before = copy.deepcopy(FakeIO.saved[str(path)])
    lc = before['deterministic_runtime']['lifecycle']
    args = dict(scope='shared', stage='shared', digest=decision_digest(lc['plan'], 'shared', 'shared'),
                revision=1, decision='approve', actor='operator', reason='Reviewed')
    args[field] = value
    with pytest.raises(ValueError):
        runtime.lifecycle_decide(path, **args)
    assert FakeIO.saved[str(path)] == before


def test_waterfall_blocks_first_unit_when_later_unit_is_unapproved(lifecycle):
    _, path, _ = lifecycle
    lc = FakeIO.saved[str(path)]['deterministic_runtime']['lifecycle']
    changed = copy.deepcopy(lc['plan'])
    changed['mode'] = 'waterfall'
    runtime.lifecycle_revise(path, changed, 1, plan_digest(lc['plan']), 'Choose waterfall', 'operator')
    decide(path, 'shared', 'shared')
    decide(path, 'one', 'requirements')
    decide(path, 'one', 'design')
    assert runtime.resume_strict(path) == 'UPSTREAM'
    assert not FakeIO.calls


def test_revision_rebuilds_only_affected_units_and_rechecks_all(lifecycle):
    _, path, workspace = lifecycle
    approve_all(path)
    assert runtime.resume_strict(path) == 'DONE'
    lc = FakeIO.saved[str(path)]['deterministic_runtime']['lifecycle']
    changed = copy.deepcopy(lc['plan'])
    changed['units'][0]['design']['summary'] = 'Corrected first design'
    runtime.lifecycle_revise(path, changed, 1, plan_digest(lc['plan']), 'Correct', 'operator')
    for sid in ('one', 'two'):
        decide(path, sid, 'requirements')
        decide(path, sid, 'design')
    before = len(FakeIO.calls)
    FakeIO.content += ' external edit'
    assert runtime.resume_strict(path) == 'DONE'
    new = FakeIO.calls[before:]
    assert len([c for c in new if c[0] == 'GENERATE']) == 2
    assert len([c for c in new if c[0] == 'CHECK']) == 6
    runtime.validate_acceptance(path, workspace)


@pytest.mark.parametrize('prior_unit_pass', [False, True])
def test_revision_preserves_waiting_replan_without_fabricating_completion(lifecycle, prior_unit_pass):
    _, path, _ = lifecycle
    approve_all(path)
    FakeIO.failing = 2
    assert runtime.resume_strict(path, max_steps=5) == 'PAUSED'
    saved = FakeIO.saved[str(path)]
    assert saved['deterministic_runtime']['phase'] == 'REPLAN'
    if prior_unit_pass:
        # A final integration failure can follow an earlier successful unit gate.
        saved['step_state']['one']['status'] = 'passed'
    lc = saved['deterministic_runtime']['lifecycle']
    changed = copy.deepcopy(lc['plan'])
    changed['units'][0]['design']['summary'] += ' reconstruct'
    runtime.lifecycle_revise(path, changed, 1, plan_digest(lc['plan']), 'Reconstruct', 'operator')
    after = FakeIO.saved[str(path)]
    events = after['deterministic_runtime']['units']['one']['events']
    assert [e['kind'] for e in events] == ['failure', 'failure']
    assert after['step_state']['one']['retries'] == 2
    assert after['deterministic_runtime']['upstream_resume_phase'] == 'REPLAN'
    for sid in ('one', 'two'):
        decide(path, sid, 'requirements')
        decide(path, sid, 'design')
    count = len(FakeIO.calls)
    assert runtime.resume_strict(path) == 'DONE'
    assert FakeIO.calls[count][0] == 'REPLAN'
    events = FakeIO.saved[str(path)]['deterministic_runtime']['units']['one']['events']
    assert [e['kind'] for e in events] == ['failure', 'failure', 'replan']


def test_provider_receives_current_requirements_and_revision(lifecycle, monkeypatch):
    _, path, _ = lifecycle
    approve_all(path)
    observed = []
    original = FakeIO.run
    def run(self, argv, **kwargs):
        if kwargs.get('input'):
            import json
            observed.append(json.loads(kwargs['input']))
        return original(self, argv, **kwargs)
    monkeypatch.setattr(FakeIO, 'run', run)
    assert runtime.resume_strict(path) == 'DONE'
    assert all(p['lifecycle_revision'] == 1 for p in observed)
    assert all(p['lifecycle_plan']['units'][0]['requirements'][0]['text'] == 'Requirement' for p in observed)
    assert all('decisions' not in p['lifecycle_plan'] for p in observed)


def test_real_isolation_integration_failure_requires_revision_and_fresh_gate(tmp_path):
    import json
    import subprocess
    workspace = tmp_path / 'repo'
    workspace.mkdir()
    subprocess.run(['git', 'init', str(workspace)], check=True, capture_output=True)
    p = plan()
    p['integration_checks'][0]['command'] = 'false'
    steps = compile_steps(p)
    state = {'run_id': 'real-lifecycle', 'goal': p['objective'], 'steps': steps,
             'step_state': {s['id']: {'status': 'pending', 'retries': 0} for s in steps},
             'done': False, 'stopped': None, 'cursor': 0, 'history': []}
    path = tmp_path / 'private' / 'run.json'
    runtime.initialize(state, workspace, path, {'generator': 'mock', 'verifier': 'mock'}, lifecycle_plan=p)
    def approve_current():
        lc = json.loads(path.read_text())['deterministic_runtime']['lifecycle']
        for scope, stage in [('shared', 'shared')] + [(u['id'], stage) for u in p['units'] for stage in ('requirements', 'design')]:
            runtime.lifecycle_decide(path, scope, stage, decision_digest(lc['plan'], scope, stage), lc['revision'], 'approve', 'operator', 'Reviewed')
    assert runtime.resume_strict(path) == 'UPSTREAM'
    approve_current()
    assert runtime.resume_strict(path) == 'AWAIT_DECISION'
    with pytest.raises(ValueError):
        runtime.validate_acceptance(path, workspace)
    before = json.loads(path.read_text())
    assert len([op for op in before['deterministic_runtime']['operations'] if op['operation'] == 'GENERATE']) == 3
    changed = copy.deepcopy(p)
    changed['integration_checks'][0]['command'] = 'true'
    runtime.lifecycle_revise(path, changed, 1, plan_digest(p), 'Correct integrated assertion', 'operator')
    approve_current()
    assert runtime.resume_strict(path) == 'DONE'
    runtime.validate_acceptance(path, workspace)
    after = json.loads(path.read_text())
    assert after['deterministic_runtime']['superseded_final_evidence'][0]['final_evidence']['evidence'][-1]['status'] == 'FAIL'
    (workspace / 'external-change').write_text('stale')
    with pytest.raises(ValueError):
        runtime.validate_acceptance(path, workspace)


def test_revision_refuses_inflight_without_changing_state(lifecycle):
    _, path, _ = lifecycle
    saved = FakeIO.saved[str(path)]
    saved['deterministic_runtime']['phase'] = 'GENERATE_INFLIGHT'
    lc = saved['deterministic_runtime']['lifecycle']
    changed = copy.deepcopy(lc['plan'])
    changed['objective'] += ' revised'
    before = copy.deepcopy(saved)
    with pytest.raises(ValueError, match='interrupted'):
        runtime.lifecycle_revise(path, changed, 1, plan_digest(lc['plan']), 'Revise', 'operator')
    assert FakeIO.saved[str(path)] == before


def test_failure_and_replan_budgets_remain_cumulative_across_revision(lifecycle):
    _, path, _ = lifecycle
    approve_all(path)
    FakeIO.failing = 20
    assert runtime.resume_strict(path, max_steps=5) == 'PAUSED'
    lc = FakeIO.saved[str(path)]['deterministic_runtime']['lifecycle']
    changed = copy.deepcopy(lc['plan'])
    changed['units'][0]['design']['summary'] += ' revised'
    runtime.lifecycle_revise(path, changed, 1, plan_digest(lc['plan']), 'Reconstruct', 'operator')
    for sid in ('one', 'two'):
        decide(path, sid, 'requirements')
        decide(path, sid, 'design')
    assert runtime.resume_strict(path) == 'ESCALATE'
    saved = FakeIO.saved[str(path)]
    events = saved['deterministic_runtime']['units']['one']['events']
    assert len([e for e in events if e['kind'] == 'failure']) == 5
    assert len([e for e in events if e['kind'] == 'replan']) == 2
    assert saved['step_state']['one']['retries'] == 5
    changed['objective'] += ' again'
    with pytest.raises(ValueError, match='terminal'):
        runtime.lifecycle_revise(path, changed, 2, plan_digest(lc['plan']), 'Retry', 'operator')


@pytest.mark.parametrize('mutation', ['missing', 'digest', 'affected', 'extra', 'boolean'])
def test_revision_history_corruption_is_rejected(lifecycle, mutation):
    _, path, _ = lifecycle
    lc = FakeIO.saved[str(path)]['deterministic_runtime']['lifecycle']
    changed = copy.deepcopy(lc['plan'])
    changed['objective'] += ' updated'
    runtime.lifecycle_revise(path, changed, 1, plan_digest(lc['plan']), 'Update', 'operator')
    saved = copy.deepcopy(FakeIO.saved[str(path)])
    change = saved['deterministic_runtime']['lifecycle']['changes'][0]
    if mutation == 'missing':
        del change['previous_plan']
    elif mutation == 'digest':
        change['previous_digest'] = '0' * 64
    elif mutation == 'affected':
        change['affected_units'] = []
    elif mutation == 'boolean':
        change['revision'] = True
    else:
        change['unknown'] = 'x'
    with pytest.raises(ValueError):
        runtime.validate_state(saved)


def test_unrelated_revision_preserves_pending_diagnosis(lifecycle):
    _, path, _ = lifecycle
    approve_all(path)
    FakeIO.failing = 1
    assert runtime.resume_strict(path, max_steps=2) == 'PAUSED'
    lc = FakeIO.saved[str(path)]['deterministic_runtime']['lifecycle']
    changed = copy.deepcopy(lc['plan'])
    changed['units'][2]['design']['summary'] += ' revised later'
    runtime.lifecycle_revise(path, changed, 1, plan_digest(lc['plan']), 'Update later unit', 'operator')
    assert FakeIO.saved[str(path)]['deterministic_runtime']['upstream_resume_phase'] == 'DIAGNOSE'
    count = len(FakeIO.calls)
    assert runtime.resume_strict(path) == 'UPSTREAM'
    assert FakeIO.calls[count][0] == 'DIAGNOSE'
