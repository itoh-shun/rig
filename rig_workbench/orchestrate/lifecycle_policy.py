"""Pure lifecycle policy; callers authenticate decisions and protect stored history.

These functions neither authenticate actors nor prove requirement correctness.
Completion IDs must come from current runner-owned evidence, never AI assertions.
Compilation is not authorization: the execution adapter must enforce readiness.
Malformed inputs raise ValueError; well-formed incomplete drafts are denied.
"""
import hashlib
import json


def _object(value, keys):
    if type(value) is not dict or set(value) != set(keys.split()):
        raise ValueError('invalid lifecycle object fields')


def _text(value, blank=False):
    if type(value) is not str or (not blank and not value.strip()) or any(
            ord(c) < 32 and c not in '\n\t' for c in value):
        raise ValueError('invalid lifecycle text')


def _id(value):
    _text(value)
    if value != value.strip() or any(c.isspace() for c in value):
        raise ValueError('invalid lifecycle identifier')


def _list(value):
    if type(value) is not list:
        raise ValueError('expected lifecycle list')
    return value


def _strings(value, identifiers=False):
    for item in _list(value):
        (_id if identifiers else _text)(item)
    if identifiers and len(set(value)) != len(value):
        raise ValueError('duplicate lifecycle reference')


def _unique(items):
    ids = []
    for item in _list(items):
        if type(item) is not dict or 'id' not in item:
            raise ValueError('missing lifecycle identity')
        _id(item['id'])
        ids.append(item['id'])
    if len(ids) != len(set(ids)):
        raise ValueError('duplicate lifecycle identity')
    return set(ids)


def _checks(checks):
    ids = _unique(checks)
    for check in checks:
        _object(check, 'id command')
        _text(check['command'])
    return ids


def validate_plan(plan):
    """Validate exact schema and references, while allowing incomplete drafts."""
    _object(plan, 'schema_version mode objective shared units integration_checks')
    if type(plan['schema_version']) is not int or plan['schema_version'] != 1:
        raise ValueError('unsupported lifecycle schema')
    if type(plan['mode']) is not str or plan['mode'] not in ('waterfall', 'iterative'):
        raise ValueError('invalid lifecycle mode')
    _text(plan['objective'])
    _object(plan['shared'], 'constraints interfaces open_questions')
    for values in plan['shared'].values():
        _strings(values)
    ids = _unique(plan['units'])
    if not ids or ids & {'shared', 'integration'}:
        raise ValueError('missing or reserved lifecycle unit')
    if any(':' in unit_id for unit_id in ids):
        raise ValueError('unit IDs must not contain the evidence namespace separator :')
    seen = set()
    for unit in plan['units']:
        _object(unit, 'id depends_on requirements design checks open_questions')
        _strings(unit['depends_on'], True)
        if not set(unit['depends_on']) <= seen:
            raise ValueError('dependencies must identify preceding units')
        seen.add(unit['id'])
        _strings(unit['open_questions'])
        checks = _checks(unit['checks'])
        requirements = _unique(unit['requirements'])
        acceptance_ids = set()
        for requirement in unit['requirements']:
            _object(requirement, 'id text acceptance')
            _text(requirement['text'])
            local = _unique(requirement['acceptance'])
            if acceptance_ids & local:
                raise ValueError('duplicate acceptance identity within unit')
            acceptance_ids |= local
            for acceptance in requirement['acceptance']:
                _object(acceptance, 'id text check_ids')
                _text(acceptance['text'])
                _strings(acceptance['check_ids'], True)
                if not set(acceptance['check_ids']) <= checks:
                    raise ValueError('unknown acceptance check')
        _object(unit['design'], 'summary requirement_ids')
        _text(unit['design']['summary'], blank=True)
        _strings(unit['design']['requirement_ids'], True)
        if not set(unit['design']['requirement_ids']) <= requirements:
            raise ValueError('unknown design requirement')
    _checks(plan['integration_checks'])


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=True,
                                    separators=(',', ':')).encode()).hexdigest()


def plan_digest(plan):
    validate_plan(plan)
    return _digest(['rig-lifecycle-plan-v1', plan])


def decision_digest(plan, scope, stage):
    validate_plan(plan)
    shared = _digest(['rig-lifecycle-shared-v1', plan['mode'], plan['objective'],
                      plan['shared'], plan['integration_checks']])
    if scope == 'shared' and stage == 'shared':
        return shared
    _id(scope)
    _id(stage)
    units = {u['id']: u for u in plan['units']}
    unit = units.get(scope)
    if unit is None or stage not in ('requirements', 'design'):
        raise ValueError('invalid lifecycle decision scope or stage')
    dependencies = set()
    pending = list(unit['depends_on'])
    while pending:
        dependency = pending.pop()
        if dependency not in dependencies:
            dependencies.add(dependency)
            pending.extend(units[dependency]['depends_on'])
    contracts = [u for key, u in units.items() if key in dependencies]
    req = _digest(['rig-lifecycle-requirements-v1', shared, scope, contracts,
                   unit['depends_on'], unit['requirements'], unit['open_questions']])
    return req if stage == 'requirements' else _digest(['rig-lifecycle-design-v1', req, unit])


def _decisions(plan, decisions):
    _unique(decisions)
    latest = {}
    for index, decision in enumerate(decisions):
        _object(decision, 'id scope stage digest actor decision reason')
        for key in ('actor', 'reason'):
            _text(decision[key])
        if decision['decision'] not in ('approve', 'reject'):
            raise ValueError('invalid lifecycle decision')
        digest = decision['digest']
        if type(digest) is not str or len(digest) != 64 or any(c not in '0123456789abcdef' for c in digest):
            raise ValueError('invalid lifecycle decision digest')
        decision_digest(plan, decision['scope'], decision['stage'])
        latest[(decision['scope'], decision['stage'])] = (index, decision)
    return latest


def readiness(plan, decisions, unit_id, completed_units):
    """Project authorization from current contract and ordered trusted decisions."""
    validate_plan(plan)
    latest = _decisions(plan, decisions)
    _strings(completed_units, True)
    units = {u['id']: u for u in plan['units']}
    if not set(completed_units) <= units.keys() or unit_id not in (*units, 'integration'):
        raise ValueError('unknown lifecycle unit')
    reasons = []

    def approved(scope, stage):
        item = latest.get((scope, stage))
        if (item is None or item[1]['decision'] != 'approve'
                or item[1]['digest'] != decision_digest(plan, scope, stage)):
            reasons.append(f'{scope}:{stage}:approval_required')
            return -1
        return item[0]

    approved('shared', 'shared')
    if plan['shared']['open_questions']:
        reasons.append('shared:unresolved_questions')
    if not plan['integration_checks']:
        reasons.append('integration:checks_required')
    required = set(units) if plan['mode'] == 'waterfall' or unit_id == 'integration' else {unit_id}
    if unit_id != 'integration':
        dependencies = set()
        pending = list(units[unit_id]['depends_on'])
        while pending:
            dependency = pending.pop()
            if dependency not in dependencies:
                dependencies.add(dependency)
                required.add(dependency)
                pending.extend(units[dependency]['depends_on'])
        for dependency in units:
            if dependency in dependencies and dependency not in completed_units:
                reasons.append(f'{dependency}:completion_required')
    elif set(completed_units) != set(units):
        reasons.append('integration:all_units_required')
    for key, unit in units.items():
        if key not in required:
            continue
        if unit['open_questions']:
            reasons.append(f'{key}:unresolved_questions')
        if not unit['requirements'] or not unit['checks'] or not unit['design']['summary'].strip():
            reasons.append(f'{key}:upstream_incomplete')
        if set(unit['design']['requirement_ids']) != {r['id'] for r in unit['requirements']}:
            reasons.append(f'{key}:design_coverage_required')
        if any(not r['acceptance'] or any(not a['check_ids'] for a in r['acceptance'])
               for r in unit['requirements']):
            reasons.append(f'{key}:acceptance_coverage_required')
        req_index = approved(key, 'requirements')
        design_index = approved(key, 'design')
        if design_index >= 0 and (req_index < 0 or design_index <= req_index):
            reasons.append(f'{key}:design_requires_current_requirements_approval')
    return {'allowed': not reasons, 'reasons': reasons}


def affected_units(old, new):
    """Return affected IDs, including removed IDs, in old then new unit order."""
    validate_plan(old)
    validate_plan(new)
    before = {u['id']: u for u in old['units']}
    after = {u['id']: u for u in new['units']}
    ordered = list(dict.fromkeys([*before, *after]))
    global_keys = ('mode', 'objective', 'shared', 'integration_checks')
    if (any(old[k] != new[k] for k in global_keys) or list(before) != list(after)
            or any(before[k]['depends_on'] != after[k]['depends_on'] for k in before.keys() & after.keys())):
        return ordered
    changed = {k for k in ordered if before.get(k) != after.get(k)}
    while True:
        expanded = changed | {k for mapping in (before, after) for k, u in mapping.items()
                              if set(u['depends_on']) & changed}
        if expanded == changed:
            return [k for k in ordered if k in changed]
        changed = expanded


def compile_steps(plan):
    """Compile commands exactly; this does not grant permission to execute."""
    validate_plan(plan)
    steps = []
    for unit in plan['units']:
        steps.append({'id': unit['id'], 'instruction': unit['design']['summary'],
                      'gate': 'acceptance-gate', 'acceptance': [a['text']
                          for r in unit['requirements'] for a in r['acceptance']],
                      'checks': [c['command'] for c in unit['checks']], 'needs': []})
    return steps
