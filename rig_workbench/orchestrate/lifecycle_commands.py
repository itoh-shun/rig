"""Explicit lifecycle CLI adapter; proposals never imply operator approval."""
import argparse
import json
from pathlib import Path

from ..ports import Presenter
from ..ports.local import CONSOLE, LOCAL_FILES
from . import config
from .deterministic_binding import bound_task
from .isolate import setup_isolation
from .runstate import new_state


def _parser():
    parser = argparse.ArgumentParser(prog='orchestrate lifecycle', allow_abbrev=False)
    actions = parser.add_subparsers(dest='action', required=True)
    template = actions.add_parser('template', allow_abbrev=False)
    template.add_argument('--mode', choices=['waterfall', 'iterative'], required=True)
    template.add_argument('--unit', action='append', required=True)
    init = actions.add_parser('init', allow_abbrev=False)
    init.add_argument('plan')
    init.add_argument('--out', required=True)
    isolation = init.add_mutually_exclusive_group(required=True)
    isolation.add_argument('--isolate', action='store_true')
    isolation.add_argument('--deterministic-task')
    init.add_argument('--provider', choices=['cmd', 'mock', 'codex', 'claude'], required=True)
    init.add_argument('--verifier-provider', choices=['cmd', 'mock', 'codex', 'claude'], required=True)
    init.add_argument('--provider-cmd')
    init.add_argument('--timeout', type=int, default=600)
    init.add_argument('--model')
    for action in ('decide', 'revise'):
        command = actions.add_parser(action, allow_abbrev=False)
        command.add_argument('state')
        command.add_argument('--revision', type=int, required=True)
        command.add_argument('--digest', required=True)
        command.add_argument('--actor', required=True)
        command.add_argument('--reason', required=True)
        if action == 'decide':
            command.add_argument('--scope', required=True, help='shared or the exact unit ID')
            command.add_argument('--stage', choices=['shared', 'requirements', 'design'], required=True)
            command.add_argument('--decision', choices=['approve', 'reject'], required=True)
        else:
            command.add_argument('--plan', required=True)
    status = actions.add_parser('status', allow_abbrev=False)
    status.add_argument('state')
    status.add_argument('--json', action='store_true')
    return parser


def _read_plan(path):
    def unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError('duplicate plan key: ' + key)
            value[key] = item
        return value
    def invalid(value):
        raise ValueError('non-finite JSON number: ' + value)
    text = LOCAL_FILES.read_text(Path(path))
    return json.loads(text, object_pairs_hook=unique, parse_constant=invalid)


def _template(mode, units):
    return {'schema_version': 1, 'mode': mode, 'objective': 'Describe the release objective',
            'shared': {'constraints': [], 'interfaces': [], 'open_questions': []},
            'units': [{'id': unit, 'depends_on': [], 'requirements': [],
                       'design': {'summary': '', 'requirement_ids': []},
                       'checks': [], 'open_questions': []} for unit in units],
            'integration_checks': []}


def cmd_lifecycle(args, *, out: Presenter = CONSOLE):
    """Parse explicit arguments, then delegate all lifecycle decisions to runtime."""
    options = _parser().parse_args(args)
    from .lifecycle_policy import validate_plan, compile_steps
    from .commands import cmd_status
    try:
        if options.action == 'template':
            plan = _template(options.mode, options.unit)
            validate_plan(plan)
            out.out(json.dumps(plan, ensure_ascii=False, indent=2))
            return
        if options.action == 'status':
            cmd_status([options.state] + (['--json'] if options.json else []), out=out)
            return
        if options.action == 'init':
            from .deterministic_runtime import initialize
            plan = _read_plan(options.plan)
            validate_plan(plan)
            state = new_state('lifecycle-' + plan['mode'], compile_steps(plan), plan['objective'])
            cfg = {'generator': options.provider, 'verifier': options.verifier_provider,
                   'timeout': options.timeout}
            if options.provider_cmd is not None:
                cfg['provider_cmd'] = options.provider_cmd
            if options.model is not None:
                cfg['model'] = options.model
            state['providers'] = {'generator': options.provider, 'verifier': options.verifier_provider}
            path = Path(options.out).absolute()
            if options.isolate:
                state['isolation'] = setup_isolation(state['recipe'])
            with bound_task(options.deterministic_task, config.INVOCATION_CWD, state, path) as workspace:
                if workspace is None:
                    workspace = Path(state['isolation']['dir'])
                initialize(state, workspace, path, cfg, lifecycle_plan=plan)
        else:
            from .deterministic_runtime import lifecycle_decide, lifecycle_revise
            path = Path(options.state).absolute()
            guard = {'revision': options.revision, 'digest': options.digest,
                     'actor': options.actor, 'reason': options.reason}
            if options.action == 'decide':
                lifecycle_decide(path, scope=options.scope, stage=options.stage,
                                 decision=options.decision, **guard)
            else:
                plan = _read_plan(options.plan)
                validate_plan(plan)
                lifecycle_revise(path, plan, **guard)
        cmd_status([str(path)], out=out)
    except (ValueError, OSError, KeyError, TypeError) as error:
        out.err('[BLOCKED] lifecycle: ' + str(error))
        raise SystemExit(2) from error
