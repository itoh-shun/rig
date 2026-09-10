"""govern.identity — who is acting, and which org/team this repository belongs to.

v1 had one identity question ("may this person accept?") and answered it from
`RIG_USER` or `git config user.name`. v2 keeps exactly that resolution — the
same name works unchanged — and adds the binding that says which org and team
the repository sits in, so a run can be attributed to team A rather than to a
directory on somebody's laptop.

`.rig/org.json`:

    {
      "schema": "rig.org/v2",
      "org": "acme",
      "team": "team-a",
      "policy_layers": ["policy/acme-baseline.json", ".rig/policy/team-a.json"]
    }

Absent file → unbound repository → governance inert.
"""

from __future__ import annotations

import dataclasses
import json
import pathlib
import re

from ..ports import Env, FileStore, ProcessRunner
from ..ports.local import LOCAL_FILES, OS_ENV, SUBPROCESS

ORG_SCHEMA = "rig.org/v2"
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


@dataclasses.dataclass(frozen=True)
class OrgBinding:
    """The repository's place in the org. `bound` is False when `.rig/org.json` is absent."""
    bound: bool
    org: str | None
    team: str | None
    raw: dict
    path: pathlib.Path | None = None
    error: str | None = None

    def label(self) -> str:
        if not self.bound:
            return "(unbound)"
        return f"{self.org}/{self.team}" if self.team else str(self.org)


def org_binding_path(root: pathlib.Path) -> pathlib.Path:
    return root / ".rig" / "org.json"


def load_org_binding(root: pathlib.Path, *, files: FileStore = LOCAL_FILES) -> OrgBinding:
    """Read `.rig/org.json`.

    A malformed binding is reported through `error` rather than raised: the
    governance CLI surfaces it, and the enforcement path treats an unreadable
    binding as unbound so a broken file can never *silently* disable a gate
    while also never bricking someone's checkout.
    """
    p = org_binding_path(root)
    if not files.is_file(p):
        return OrgBinding(bound=False, org=None, team=None, raw={}, path=None)
    try:
        data = json.loads(files.read_text(p))
    except json.JSONDecodeError as e:
        return OrgBinding(False, None, None, {}, p, f"{p}: not valid JSON: {e}")
    if not isinstance(data, dict):
        return OrgBinding(False, None, None, {}, p, f"{p}: must be a JSON object")
    if data.get("schema") != ORG_SCHEMA:
        return OrgBinding(False, None, None, {}, p,
                          f"{p}: schema must be '{ORG_SCHEMA}' (got {data.get('schema')!r})")
    org = data.get("org")
    if not isinstance(org, str) or not _ID_RE.match(org):
        return OrgBinding(False, None, None, {}, p, f"{p}: 'org' is required")
    team = data.get("team")
    if team is not None and (not isinstance(team, str) or not _ID_RE.match(team)):
        return OrgBinding(False, None, None, {}, p, f"{p}: 'team' must be an identifier")
    layers = data.get("policy_layers", [])
    if not isinstance(layers, list) or not all(isinstance(s, str) and s for s in layers):
        return OrgBinding(False, None, None, {}, p, f"{p}: 'policy_layers' must be a list of paths")
    return OrgBinding(True, org, team, data, p)


def current_actor(root: pathlib.Path | None = None, *, env: Env = OS_ENV,
                  runner: ProcessRunner = SUBPROCESS) -> str:
    """The identity performing the action.

    `RIG_ACTOR` first (v2 name), then `RIG_USER` (the v1 name, still honoured so
    existing `.rig/access.json` setups keep working), then `git config
    user.name`, then "unknown".

    Through `ProcessRunner` and deliberately **not** `GitRepo.config_value`, which is
    the second decision the ports left open. `GitRepo` goes via `gitroot._git`, which
    strips `GIT_DIR` / `GIT_WORK_TREE` / `GIT_COMMON_DIR` first — so moving this call
    there would stop an inherited `GIT_DIR` deciding *whose name* the governance layer
    writes into the ledger. That is a real fix (#471's class of bug, applied to the
    actor rather than to where state lives), and `ports/local.GitCli` says in its own
    docstring that it belongs in the commit that moves the call site, **with its own
    test**. This change may not add one, so taking the fix here would leave a
    behaviour change unpinned inside what is otherwise an exact swap — the thing that
    docstring warns against. `ProcessRunner` is the port this exact call site shaped
    (`argv`, `cwd`, captured text, no `check=`) and it inherits the environment exactly
    as `subprocess.run` does, so today's answer is today's answer. The follow-up is one
    line — `runner.run([...])` becomes `git.config_value("user.name", cwd=root)` — plus
    the test that exports `GIT_DIR` at a second repository with a different `user.name`
    and asserts the actor comes from the tree the caller is standing in.
    """
    for var in ("RIG_ACTOR", "RIG_USER"):
        value = env.get(var)
        if value:
            return value
    try:
        proc = runner.run(["git", "config", "user.name"], cwd=root if root else None)
        name = proc.stdout.strip()
        if name:
            return name
    except Exception:
        pass
    return "unknown"
