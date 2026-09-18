"""Lifecycle decisions bind explicit human decisions to current plan content."""
from copy import deepcopy

import pytest

from rig_workbench.orchestrate.lifecycle_policy import (
    affected_units, compile_steps, decision_digest, plan_digest, readiness, validate_plan,
)


def plan(mode='iterative'):
    def unit(name, deps):
        return {'id': name, 'depends_on': deps,
                'requirements': [{'id': 'r', 'text': 'Requirement', 'acceptance': [
                    {'id': 'a', 'text': 'Behavior works', 'check_ids': ['c']}]}],
                'design': {'summary': 'Implementation design', 'requirement_ids': ['r']},
                'checks': [{'id': 'c', 'command': 'true'}], 'open_questions': []}
    return {'schema_version': 1, 'mode': mode, 'objective': 'Deliver behavior',
            'shared': {'constraints': [], 'interfaces': [], 'open_questions': []},
            'units': [unit('one', []), unit('two', ['one']), unit('three', [])],
            'integration_checks': [{'id': 'all', 'command': 'true'}]}


def approve(p, decisions, scope, stage, decision='approve'):
    decisions.append({'id': str(len(decisions)), 'scope': scope, 'stage': stage,
                      'digest': decision_digest(p, scope, stage), 'actor': 'operator',
                      'decision': decision, 'reason': 'Reviewed the current contract'})


def approvals(p):
    records = []
    approve(p, records, 'shared', 'shared')
    for unit in p['units']:
        for stage in ('requirements', 'design'):
            approve(p, records, unit['id'], stage)
    return records


def test_start_requires_decisions_and_completed_dependencies():
    p = plan()
    assert not readiness(p, [], 'one', [])['allowed']
    records = approvals(p)
    assert readiness(p, records, 'one', [])['allowed']
    assert not readiness(p, records, 'two', [])['allowed']
    assert readiness(p, records, 'two', ['one'])['allowed']


def test_iterative_allows_independent_draft_but_waterfall_does_not():
    p = plan()
    records = approvals(p)
    p['units'][2]['requirements'] = []
    p['units'][2]['design'] = {'summary': '', 'requirement_ids': []}
    assert readiness(p, records, 'one', [])['allowed']
    p['mode'] = 'waterfall'
    assert not readiness(p, approvals(p), 'one', [])['allowed']


@pytest.mark.parametrize('change', ['check', 'trace', 'question', 'integration'])
def test_readiness_refuses_incomplete_or_changed_contract(change):
    p = plan()
    records = approvals(p)
    if change == 'check':
        p['units'][0]['checks'][0]['command'] = 'false'
    elif change == 'trace':
        p['units'][0]['requirements'][0]['acceptance'][0]['check_ids'] = []
    elif change == 'question':
        p['shared']['open_questions'] = ['Undecided']
    else:
        p['integration_checks'] = []
    assert not readiness(p, records, 'one', [])['allowed']


def test_rejection_cannot_fall_back_and_requirements_reapproval_invalidates_design():
    p = plan()
    records = approvals(p)
    approve(p, records, 'one', 'design', 'reject')
    assert not readiness(p, records, 'one', [])['allowed']
    approve(p, records, 'one', 'design')
    approve(p, records, 'one', 'requirements')
    assert not readiness(p, records, 'one', [])['allowed']
    approve(p, records, 'one', 'design')
    assert readiness(p, records, 'one', [])['allowed']


@pytest.mark.parametrize('change', ['bool_schema', 'unknown', 'reserved', 'forward', 'duplicate', 'unknown_check'])
def test_malformed_plan_is_rejected(change):
    p = plan()
    if change == 'bool_schema':
        p['schema_version'] = True
    elif change == 'unknown':
        p['extra'] = True
    elif change == 'reserved':
        p['units'][0]['id'] = 'shared'
    elif change == 'forward':
        p['units'][0]['depends_on'] = ['two']
    elif change == 'duplicate':
        p['units'][0]['checks'] *= 2
    else:
        p['units'][0]['requirements'][0]['acceptance'][0]['check_ids'] = ['missing']
    with pytest.raises(ValueError):
        validate_plan(p)


def test_draft_validity_is_not_readiness():
    p = plan()
    p['units'][0].update(requirements=[], checks=[], design={'summary': '', 'requirement_ids': []})
    validate_plan(p)
    assert not readiness(p, approvals(p), 'one', [])['allowed']


def test_digest_and_invalidation_scope_are_deterministic():
    p = plan()
    new = deepcopy(p)
    new['units'][0]['checks'][0]['command'] = 'false'
    assert affected_units(p, new) == ['one', 'two']
    assert decision_digest(p, 'one', 'requirements') == decision_digest(new, 'one', 'requirements')
    assert decision_digest(p, 'one', 'design') != decision_digest(new, 'one', 'design')
    assert plan_digest(p) == plan_digest(deepcopy(p))
    new['shared']['interfaces'] = ['Changed API']
    assert affected_units(p, new) == ['one', 'two', 'three']


def test_decision_records_fail_closed_and_compilation_preserves_checks():
    p = plan()
    records = approvals(p)
    records.append(deepcopy(records[0]))
    with pytest.raises(ValueError):
        readiness(p, records, 'one', [])
    steps = compile_steps(p)
    assert [s['id'] for s in steps] == ['one', 'two', 'three']
    assert steps[0]['checks'] == ['true']
    assert steps[0]['acceptance'] == ['Behavior works']
    assert steps[-1]['checks'] == ['true']


def test_dependency_change_invalidates_downstream_approval_without_touching_independent_unit():
    p = plan()
    revised = deepcopy(p)
    revised['units'][0]['design']['summary'] = 'New design'
    assert decision_digest(p, 'two', 'requirements') != decision_digest(revised, 'two', 'requirements')
    assert decision_digest(p, 'three', 'design') == decision_digest(revised, 'three', 'design')


def test_integration_requires_all_current_upstream_approvals_and_completions():
    p = plan()
    records = approvals(p)
    assert not readiness(p, records, 'integration', ['one', 'two'])['allowed']
    assert readiness(p, records, 'integration', ['one', 'two', 'three'])['allowed']
    p['integration_checks'][0]['command'] = 'false'
    assert not readiness(p, records, 'integration', ['one', 'two', 'three'])['allowed']


def test_stale_latest_decision_does_not_restore_previous_approval():
    p = plan()
    records = approvals(p)
    approve(p, records, 'one', 'design', 'reject')
    records[-1]['digest'] = '0' * 64
    assert not readiness(p, records, 'one', [])['allowed']


def test_dependency_structure_or_removed_unit_invalidates_conservatively():
    p = plan()
    revised = deepcopy(p)
    revised['units'][1]['depends_on'] = []
    assert affected_units(p, revised) == ['one', 'two', 'three']
    revised['units'].pop(2)
    assert affected_units(p, revised) == ['one', 'two', 'three']


def test_unit_identifier_cannot_collide_with_integration_evidence_namespace():
    p = plan()
    p['units'][0]['id'] = 'integration:check'
    p['units'][1]['depends_on'] = ['integration:check']
    p['integration_checks'][0]['id'] = 'check:1'
    with pytest.raises(ValueError):
        validate_plan(p)
