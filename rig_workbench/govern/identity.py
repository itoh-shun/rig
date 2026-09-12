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

#: What every actor in this repository is, and the word the record uses for it.
#:
#: There is no identity provider here and this change does not invent one. What the four
#: resolutions below actually read is: a flag typed on the command line, two environment
#: variables set by whoever ran the command, and `git config user.name`, which is a config
#: file that same person writes (`git config user.name "anyone"` and the next approval is
#: theirs). None of them is checked against anything, so none of them is an identity —
#: every one is a claim, and telling them apart by *trust* would be a distinction this
#: repository cannot back. `ActorClaim.source` says which mechanism carried the name
#: because that is a fact worth recording; `authenticated` is False on all four because
#: that is the other fact, and it is the one a reader of an approval needs.
SELF_ASSERTED = "self-asserted"


@dataclasses.dataclass(frozen=True)
class ActorClaim:
    """A name that arrived, and how much is known about it.

    `name` is exactly what `current_actor` has always returned, so nothing that stores or
    compares an actor sees any change. `source` and `assertion` are what the record gains:
    a decision can now say that the name on it was never authenticated, instead of leaving
    a reader to assume it was.
    """

    name: str
    source: str
    authenticated: bool = False

    @property
    def assertion(self) -> str:
        """The word written into the record. `SELF_ASSERTED` for everything today."""
        return "authenticated" if self.authenticated else SELF_ASSERTED


def resolve_actor(root: pathlib.Path | None = None, override: str | None = None, *,
                  env: Env = OS_ENV, runner: ProcessRunner = SUBPROCESS) -> ActorClaim:
    """`current_actor`'s resolution, with the provenance of the answer kept.

    `override` is the caller's own `--actor`, which every govern command accepts and which
    used to be applied at the call site as `args.actor or current_actor(root)`. It is
    resolved here so that the one place that knows a name came from a flag is the place
    that can say so.
    """
    if override:
        return ActorClaim(override, "--actor")
    for var in ("RIG_ACTOR", "RIG_USER"):
        value = env.get(var)
        if value:
            return ActorClaim(value, f"${var}")
    try:
        proc = runner.run(["git", "config", "user.name"], cwd=root if root else None)
        name = proc.stdout.strip()
        if name:
            return ActorClaim(name, "git config user.name")
    except Exception:
        pass
    return ActorClaim("unknown", "nothing supplied a name")


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

    The name and nothing else, which is all any caller that stores or compares an actor
    wants. A caller that *records* one wants `resolve_actor`, which returns the same name
    with the two facts this function drops: which of the four supplied it, and that none of
    them authenticates anybody (`SELF_ASSERTED`).

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
    return resolve_actor(root, env=env, runner=runner).name
