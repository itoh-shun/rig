"""Pure projections for lifecycle-backed execution; effects stay in the runner.

Protected state and caller identity remain the adapter's trust boundary. Digests
detect stale edits and contract drift, not a malicious writer of the state file.
"""
import copy

from .lifecycle_policy import compile_steps, readiness, validate_plan, plan_digest, affected_units


def immutable_definition(state, rt):
    return {key: value for key, value in {
        'run_id': state['run_id'], 'binding': state.get('deterministic_binding'),
        'workspace': rt['workspace'], 'state_path': rt['state_path'],
        'provider_config': rt['provider_config'], 'resolved_executables': rt['resolved_executables'],
        'limits': rt['limits'], 'unit_ids': [s['id'] for s in state['steps']],
        'lifecycle_schema_version': state.get('lifecycle_schema_version'),
    }.items()}


def validate_lifecycle(state, rt):
    lc = rt.get('lifecycle')
    if type(lc) is not dict or set(lc) != {'schema_version', 'revision', 'plan', 'decisions', 'changes'}:
        raise ValueError('missing or malformed lifecycle state')
    if type(lc['schema_version']) is not int or lc['schema_version'] != 1:
        raise ValueError('unsupported lifecycle state schema')
    if type(lc['revision']) is not int or lc['revision'] < 1:
        raise ValueError('invalid lifecycle revision')
    validate_plan(lc['plan'])
    if state['steps'] != compile_steps(lc['plan']) or state.get('goal') != lc['plan']['objective']:
        raise ValueError('compiled lifecycle contract changed')
    if type(lc['changes']) is not list or len(lc['changes']) != lc['revision'] - 1:
        raise ValueError('lifecycle revision history disagrees')
    for index, change in enumerate(lc['changes'], 2):
        if (type(change) is not dict or set(change) != {'revision', 'previous_plan', 'previous_digest', 'actor', 'reason', 'affected_units'}
                or type(change.get('revision')) is not int or change['revision'] != index
                or not isinstance(change.get('actor'), str) or not change['actor'].strip()
                or not isinstance(change.get('reason'), str) or not change['reason'].strip()):
            raise ValueError('invalid lifecycle change record')
        previous = change['previous_plan']
        following = lc['changes'][index - 1]['previous_plan'] if index <= len(lc['changes']) else lc['plan']
        if (change['previous_digest'] != plan_digest(previous)
                or change['affected_units'] != affected_units(previous, following)
                or [u['id'] for u in previous['units']] != [u['id'] for u in lc['plan']['units']]):
            raise ValueError('inconsistent lifecycle revision history')
    readiness(lc['plan'], lc['decisions'], lc['plan']['units'][0]['id'], [])
    if rt['phase'] == 'UPSTREAM' and rt.get('upstream_resume_phase') not in (
            'GENERATE', 'CHECK', 'VERIFY', 'DIAGNOSE', 'REPLAN', 'FINAL_CHECK', 'FINAL_VERIFY'):
        raise ValueError('invalid upstream continuation')


def ready(state, final=False):
    rt = state['deterministic_runtime']
    lc = rt['lifecycle']
    completed = [sid for sid, item in state['step_state'].items() if item['status'] == 'passed']
    unit = 'integration' if final else state['steps'][rt['step_index']]['id']
    return readiness(lc['plan'], lc['decisions'], unit, completed)


def final_checks(state):
    lc = state['deterministic_runtime'].get('lifecycle')
    return [(f"integration:{c['id']}", c['command']) for c in lc['plan']['integration_checks']] if lc else []


def context(state):
    """Give the provider the actual approved requirements and shared obligations."""
    lc = state['deterministic_runtime'].get('lifecycle')
    return {'lifecycle_plan': copy.deepcopy(lc['plan']), 'lifecycle_revision': lc['revision']} if lc else {}
