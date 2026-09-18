"""Read-only lifecycle status projection and presentation."""


def _display_reason(reason):
    clean = ' '.join(''.join(char if char.isprintable() else ' ' for char in reason).split())
    return (clean[:317] + '...' if len(clean) > 320 else clean), len(clean) > 320


def lifecycle_snapshot(state):
    """Saved-plan obligations only; no raw requirements or provider output."""
    from .lifecycle_policy import plan_digest, decision_digest, readiness
    runtime = state['deterministic_runtime']
    saved = runtime['lifecycle']
    plan = saved['plan']
    completed = [unit['id'] for unit in plan['units']
                 if state['step_state'][unit['id']]['status'] == 'passed']
    stages = [{'scope': 'shared', 'stage': 'shared',
               'digest': decision_digest(plan, 'shared', 'shared')}]
    for unit in plan['units']:
        for stage in ('requirements', 'design'):
            stages.append({'scope': unit['id'], 'stage': stage,
                           'digest': decision_digest(plan, unit['id'], stage)})
    latest = {(record['scope'], record['stage']): record for record in saved['decisions']}
    for stage in stages:
        record = latest.get((stage['scope'], stage['stage']))
        reason, truncated = _display_reason(record['reason']) if record else (None, False)
        stage.update(latest_decision=record['decision'] if record else None,
                     decision_id=record['id'] if record else None,
                     digest_current=record['digest'] == stage['digest'] if record else None,
                     reason=reason, reason_truncated=truncated)
    units = [{'id': unit['id'], **readiness(plan, saved['decisions'], unit['id'], completed)}
             for unit in plan['units']]
    integration = readiness(plan, saved['decisions'], 'integration', completed)
    pending = [unit for unit in units if unit['id'] not in completed]
    ready = pending[0]['allowed'] if pending else integration['allowed']
    phase = runtime.get('phase', '')
    next_action = ('resolve_interrupted_operation' if phase.endswith('_INFLIGHT') else
                   'inspect_stop' if state.get('stopped') or phase in
                   ('BLOCKED', 'ESCALATE', 'AWAIT_DECISION', 'REQUIREMENTS', 'DESIGN') else
                   'inspect_results' if state.get('done') else
                   'resume' if ready else 'review_plan_and_record_decisions')
    return {'mode': plan['mode'], 'revision': saved['revision'], 'plan_digest': plan_digest(plan),
            'stages': stages, 'units': units, 'integration': integration,
            'decision_requires': ['scope', 'stage', 'revision', 'digest', 'actor', 'reason'],
            'next_action': next_action}


def render_lifecycle(snapshot, out):
    out.out(f"Lifecycle mode={snapshot['mode']} revision={snapshot['revision']} "
            f"plan_digest={snapshot['plan_digest']}")
    for stage in snapshot['stages']:
        out.out(f"  decision scope={stage['scope']} stage={stage['stage']} digest={stage['digest']} "
                f"latest={stage['latest_decision']} id={stage['decision_id']} "
                f"digest_current={stage['digest_current']} reason={stage['reason']}")
    for unit in [*snapshot['units'], {'id': 'integration', **snapshot['integration']}]:
        out.out(f"  {unit['id']}: ready={unit['allowed']} reasons={','.join(unit['reasons'])}")
    out.out('  next_action=' + snapshot['next_action'])
    out.out('  decisions require --scope --stage --revision --digest --actor --reason; '
            'revise requires the displayed plan_digest. Resume executes only ready work.')


