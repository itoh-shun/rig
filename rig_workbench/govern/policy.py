"""govern.policy — the common policy, and the rule that keeps it common.

A policy document is JSON (never YAML: gate and permission configuration has to
parse with the standard library alone, the same reasoning that made
`.rig/gates.json` JSON). Documents stack in three scopes:

    org  →  team  →  project

and the stacking rule is **monotonic tightening**: a downstream layer may only
make the bar harder to clear. It can add required criteria, raise an approval
quorum, shorten a waiver's lifetime, narrow a role. It can never drop a
criterion the org requires, lower a quorum, extend a waiver, or hand a role a
permission the org never delegated. Any attempt is a hard error naming the layer
and the field — silently ignoring it would be the one failure mode that makes
the whole layer worthless.

That single invariant is what makes "common policy" mean something once team A,
team B and team C each keep their own repository: the org sets a floor, the
teams build on top of it, and nobody can dig.
"""

from __future__ import annotations

import dataclasses
import json
import os
import pathlib
import re

# ── permission vocabulary ────────────────────────────────────────────────────
# Fixed and closed: a typo in a role definition must fail loudly, not silently
# grant nothing (or, worse, look like it granted something).
PERMISSIONS: tuple[str, ...] = (
    "task.new",        # register a task / create a worktree
    "gate.set",        # record acceptance-gate criterion verdicts
    "accept",          # apply an accepted task into the main working tree
    "accept.force",    # accept over an unmet gate (--force)
    "approve",         # cast an approval on someone else's task
    "waiver.grant",    # issue a time-boxed exception
    "waiver.revoke",   # revoke one before it expires
    "policy.publish",  # author/replace a policy layer
    "audit.export",    # export the audit ledger out of the repository
    "pack.install",    # install a pack/extension into the project
    "discard",         # drop a task and its worktree
)

SCOPES: tuple[str, ...] = ("org", "team", "project")
SCHEMA = "rig.policy/v2"

_SLUG_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_ROLE_RE = re.compile(r"^[a-z][a-z0-9-]{0,63}$")
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")

_TOP_LEVEL_KEYS = (
    "schema", "id", "scope", "org", "team", "version", "description",
    "require_criteria", "descriptions",
    "roles", "members", "sealed_roles", "delegatable_permissions",
    "approvals", "waivers", "audit",
)

# Keys that only make sense on the layer that opens the policy, never on a child.
_ORG_ONLY_KEYS = ("delegatable_permissions",)

_APPROVAL_KEYS = ("quorum", "roles", "separation_of_duties", "expires_hours")
_WAIVER_KEYS = ("max_days", "grant_roles", "non_waivable", "required_for_force")
_AUDIT_KEYS = ("chain_required",)


STAGE_PREFIX = "stage:"
_STEP_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")


def stage_key(step_id: str) -> str:
    """The `approvals` key that governs one recipe step (v2.1 human gate)."""
    return f"{STAGE_PREFIX}{step_id}"


def _is_approval_target(target: str) -> bool:
    """`default`, a task_type slug, or `stage:<step-id>`.

    Stage keys are what let an org say "the architecture_review step of any recipe
    needs an architect's sign-off" — a requirement that belongs to the process,
    not to the kind of task being run.
    """
    if target == "default":
        return True
    if target.startswith(STAGE_PREFIX):
        return bool(_STEP_ID_RE.match(target[len(STAGE_PREFIX):]))
    return bool(_SLUG_RE.match(target))


class PolicyError(Exception):
    """A policy document is malformed, or a child layer tried to loosen a parent."""


# ── single-document validation ───────────────────────────────────────────────
def _need_dict(value, where: str) -> dict:
    if not isinstance(value, dict):
        raise PolicyError(f"{where} must be an object, got {type(value).__name__}")
    return value


def _need_str_list(value, where: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(v, str) and v for v in value):
        raise PolicyError(f"{where} must be a list of non-empty strings")
    return value


def _validate_approval_rule(rule: dict, where: str) -> dict:
    _need_dict(rule, where)
    for key in rule:
        if key not in _APPROVAL_KEYS:
            raise PolicyError(f"{where}: unknown key '{key}' (allowed: {', '.join(_APPROVAL_KEYS)})")
    quorum = rule.get("quorum", 0)
    if not isinstance(quorum, int) or isinstance(quorum, bool) or quorum < 0:
        raise PolicyError(f"{where}.quorum must be a non-negative integer")
    for role in _need_str_list(rule.get("roles", []), f"{where}.roles"):
        if not _ROLE_RE.match(role):
            raise PolicyError(f"{where}.roles: '{role}' is not a role name (^[a-z][a-z0-9-]*$)")
    sod = rule.get("separation_of_duties", True)
    if not isinstance(sod, bool):
        raise PolicyError(f"{where}.separation_of_duties must be a boolean")
    expires = rule.get("expires_hours")
    if expires is not None and (not isinstance(expires, (int, float))
                                or isinstance(expires, bool) or expires <= 0):
        raise PolicyError(f"{where}.expires_hours must be a positive number of hours")
    return {
        "quorum": quorum,
        "roles": sorted(set(rule.get("roles", []))),
        "separation_of_duties": sod,
        "expires_hours": expires,
    }


def _validate_waiver_rule(rule: dict, where: str) -> dict:
    _need_dict(rule, where)
    for key in rule:
        if key not in _WAIVER_KEYS:
            raise PolicyError(f"{where}: unknown key '{key}' (allowed: {', '.join(_WAIVER_KEYS)})")
    max_days = rule.get("max_days")
    if max_days is not None and (not isinstance(max_days, (int, float))
                                 or isinstance(max_days, bool) or max_days <= 0):
        raise PolicyError(f"{where}.max_days must be a positive number of days")
    for role in _need_str_list(rule.get("grant_roles", []), f"{where}.grant_roles"):
        if not _ROLE_RE.match(role):
            raise PolicyError(f"{where}.grant_roles: '{role}' is not a role name")
    for crit in _need_str_list(rule.get("non_waivable", []), f"{where}.non_waivable"):
        if not _SLUG_RE.match(crit):
            raise PolicyError(f"{where}.non_waivable: '{crit}' is not a criterion slug")
    required = rule.get("required_for_force", False)
    if not isinstance(required, bool):
        raise PolicyError(f"{where}.required_for_force must be a boolean")
    return {
        "max_days": max_days,
        "grant_roles": sorted(set(rule.get("grant_roles", []))),
        "non_waivable": sorted(set(rule.get("non_waivable", []))),
        "required_for_force": required,
    }


def load_policy_document(path: pathlib.Path) -> dict:
    """Parse and validate one policy document. Raises PolicyError on any defect."""
    rel = str(path)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise PolicyError(f"{rel}: policy layer not found") from None
    except json.JSONDecodeError as e:
        raise PolicyError(f"{rel}: not valid JSON: {e}") from None
    doc = _need_dict(raw, rel)

    if doc.get("schema") != SCHEMA:
        raise PolicyError(f"{rel}: schema must be '{SCHEMA}' (got {doc.get('schema')!r})")
    for key in doc:
        if key not in _TOP_LEVEL_KEYS:
            raise PolicyError(f"{rel}: unknown key '{key}' (allowed: {', '.join(_TOP_LEVEL_KEYS)})")

    scope = doc.get("scope")
    if scope not in SCOPES:
        raise PolicyError(f"{rel}: scope must be one of {', '.join(SCOPES)} (got {scope!r})")
    if scope != "org":
        for key in _ORG_ONLY_KEYS:
            if key in doc:
                raise PolicyError(
                    f"{rel}: '{key}' may only be set on the org layer — a team or project "
                    "cannot widen what the org delegated")
    for field in ("id", "org"):
        value = doc.get(field)
        if not isinstance(value, str) or not _ID_RE.match(value):
            raise PolicyError(f"{rel}: '{field}' is required and must match ^[A-Za-z0-9][A-Za-z0-9._-]*$")
    if scope == "team":
        team = doc.get("team")
        if not isinstance(team, str) or not _ID_RE.match(team):
            raise PolicyError(f"{rel}: scope 'team' requires a 'team' identifier")
    version = doc.get("version")
    if version is not None and not isinstance(version, str):
        raise PolicyError(f"{rel}: 'version' must be a string")

    require = _need_dict(doc.get("require_criteria", {}), f"{rel}.require_criteria")
    for target, crits in require.items():
        if not _SLUG_RE.match(target):
            raise PolicyError(f"{rel}.require_criteria: target '{target}' is not a preset/task_type slug")
        for crit in _need_str_list(crits, f"{rel}.require_criteria['{target}']"):
            if not _SLUG_RE.match(crit):
                raise PolicyError(f"{rel}.require_criteria['{target}']: '{crit}' is not a criterion slug")

    descs = _need_dict(doc.get("descriptions", {}), f"{rel}.descriptions")
    for key, value in descs.items():
        if not isinstance(value, str):
            raise PolicyError(f"{rel}.descriptions['{key}'] must be a string")

    roles = _need_dict(doc.get("roles", {}), f"{rel}.roles")
    for role, perms in roles.items():
        if not _ROLE_RE.match(role):
            raise PolicyError(f"{rel}.roles: '{role}' is not a role name (^[a-z][a-z0-9-]*$)")
        for perm in _need_str_list(perms, f"{rel}.roles['{role}']"):
            if perm not in PERMISSIONS:
                raise PolicyError(
                    f"{rel}.roles['{role}']: unknown permission '{perm}' "
                    f"(known: {', '.join(PERMISSIONS)})")

    members = _need_dict(doc.get("members", {}), f"{rel}.members")
    for actor, assigned in members.items():
        if not isinstance(actor, str) or not actor:
            raise PolicyError(f"{rel}.members: actor keys must be non-empty strings")
        for role in _need_str_list(assigned, f"{rel}.members['{actor}']"):
            if not _ROLE_RE.match(role):
                raise PolicyError(f"{rel}.members['{actor}']: '{role}' is not a role name")

    for role in _need_str_list(doc.get("sealed_roles", []), f"{rel}.sealed_roles"):
        if not _ROLE_RE.match(role):
            raise PolicyError(f"{rel}.sealed_roles: '{role}' is not a role name")

    for perm in _need_str_list(doc.get("delegatable_permissions", []), f"{rel}.delegatable_permissions"):
        if perm not in PERMISSIONS:
            raise PolicyError(f"{rel}.delegatable_permissions: unknown permission '{perm}'")

    approvals = _need_dict(doc.get("approvals", {}), f"{rel}.approvals")
    for target, rule in approvals.items():
        if not _is_approval_target(target):
            raise PolicyError(
                f"{rel}.approvals: target '{target}' must be 'default', a task_type slug, "
                "or 'stage:<step-id>'")
        _validate_approval_rule(rule, f"{rel}.approvals['{target}']")

    _validate_waiver_rule(doc.get("waivers", {}), f"{rel}.waivers")

    audit = _need_dict(doc.get("audit", {}), f"{rel}.audit")
    for key in audit:
        if key not in _AUDIT_KEYS:
            raise PolicyError(f"{rel}.audit: unknown key '{key}' (allowed: {', '.join(_AUDIT_KEYS)})")
    if "chain_required" in audit and not isinstance(audit["chain_required"], bool):
        raise PolicyError(f"{rel}.audit.chain_required must be a boolean")

    return doc


# ── effective policy ─────────────────────────────────────────────────────────
@dataclasses.dataclass(frozen=True)
class Layer:
    """One resolved policy document and where it came from."""
    scope: str
    id: str
    org: str
    team: str | None
    version: str | None
    path: pathlib.Path | None

    def label(self) -> str:
        suffix = f"@{self.version}" if self.version else ""
        return f"{self.scope}:{self.id}{suffix}"


@dataclasses.dataclass
class EffectivePolicy:
    """The folded result of every layer, plus enough provenance to explain it."""
    active: bool = False
    org: str | None = None
    team: str | None = None
    layers: list[Layer] = dataclasses.field(default_factory=list)
    require_criteria: dict[str, list[str]] = dataclasses.field(default_factory=dict)
    descriptions: dict[str, str] = dataclasses.field(default_factory=dict)
    roles: dict[str, list[str]] = dataclasses.field(default_factory=dict)
    members: dict[str, list[str]] = dataclasses.field(default_factory=dict)
    sealed_roles: set[str] = dataclasses.field(default_factory=set)
    delegatable_permissions: set[str] = dataclasses.field(default_factory=set)
    approvals: dict[str, dict] = dataclasses.field(default_factory=dict)
    waivers: dict = dataclasses.field(default_factory=dict)
    audit_chain_required: bool = False
    # Which layer last set a given role / member assignment (for `govern policy explain`).
    role_origin: dict[str, str] = dataclasses.field(default_factory=dict)

    def approval_rule(self, task_type: str) -> dict:
        """The approval rule for a task_type, falling back to `default`."""
        rule = self.approvals.get(task_type) or self.approvals.get("default")
        return rule or {"quorum": 0, "roles": [], "separation_of_duties": True, "expires_hours": None}

    def stage_approval_rule(self, step_id: str) -> dict | None:
        """The rule the org attaches to a named recipe step, or None if it names none.

        Deliberately *not* falling back to `default`: `default` exists so that every
        accept is covered, and applying it to every step of every recipe would turn
        one approval into a dozen. A stage is governed only when the policy says so
        by name, or when the recipe itself asks for a human gate."""
        return self.approvals.get(stage_key(step_id))

    def required_criteria_for(self, task_type: str, presets: list[str]) -> list[str]:
        """Criteria the policy adds for a task_type, given the gate presets it applies."""
        out: list[str] = []
        for target in [*presets, task_type]:
            for crit in self.require_criteria.get(target, []):
                if crit not in out:
                    out.append(crit)
        return out


def _tighten_int(parent, child, field: str, layer: str, direction: str) -> None:
    """Raise unless `child` moves `field` in the tightening direction."""
    if parent is None or child is None:
        return
    if direction == "up" and child < parent:
        raise PolicyError(
            f"{layer}: {field} may only be raised (parent requires {parent}, layer sets {child}). "
            "A downstream policy layer can tighten the common policy, never loosen it")
    if direction == "down" and child > parent:
        raise PolicyError(
            f"{layer}: {field} may only be shortened (parent allows {parent}, layer sets {child}). "
            "A downstream policy layer can tighten the common policy, never loosen it")


def _fold(eff: EffectivePolicy, doc: dict, path: pathlib.Path | None) -> None:
    """Fold one document into the accumulator, enforcing monotonic tightening."""
    scope = doc["scope"]
    layer = Layer(scope=scope, id=doc["id"], org=doc["org"], team=doc.get("team"),
                  version=doc.get("version"), path=path)
    label = layer.label()

    if eff.org and doc["org"] != eff.org:
        raise PolicyError(
            f"{label}: org '{doc['org']}' does not match the org already in effect "
            f"('{eff.org}') — policy layers from two different orgs cannot be stacked")
    eff.org = doc["org"]
    if doc.get("team"):
        eff.team = doc["team"]

    # require_criteria: additive only. Removal is not expressible, by construction —
    # there is no "remove" key, and a child that simply omits a criterion still
    # inherits it, because we union rather than replace.
    for target, crits in doc.get("require_criteria", {}).items():
        bucket = eff.require_criteria.setdefault(target, [])
        for crit in crits:
            if crit not in bucket:
                bucket.append(crit)
    eff.descriptions.update(doc.get("descriptions", {}))

    # delegatable_permissions is org-only (validated above): it is the set of
    # powers a downstream layer is allowed to invent a new role around.
    if scope == "org":
        eff.delegatable_permissions |= set(doc.get("delegatable_permissions", []))

    # roles: an existing role may only be narrowed; a *new* role may only carry
    # permissions the org already grants somewhere or explicitly delegated.
    granted_by_parents = {p for perms in eff.roles.values() for p in perms}
    allowed_for_new = granted_by_parents | eff.delegatable_permissions
    for role, perms in doc.get("roles", {}).items():
        want = set(perms)
        if role in eff.roles:
            widened = want - set(eff.roles[role])
            if widened:
                raise PolicyError(
                    f"{label}: role '{role}' adds permission(s) {', '.join(sorted(widened))} that the "
                    "upstream layer did not grant it. A downstream layer may narrow a role, never widen it")
        elif eff.layers:
            # Only meaningful relative to a parent. A lone project-scope document
            # (no org layer above it) is the root of its own stack and may define
            # what it likes — conformance is what flags "this policy is local, so
            # there is no common bar", which is a different finding entirely.
            invented = want - allowed_for_new
            if invented:
                raise PolicyError(
                    f"{label}: new role '{role}' claims permission(s) {', '.join(sorted(invented))} that "
                    "the org policy neither grants to any role nor lists in delegatable_permissions. "
                    "A team or project cannot invent power the org never handed out")
        eff.roles[role] = sorted(want)
        eff.role_origin[role] = label

    for role in doc.get("sealed_roles", []):
        eff.sealed_roles.add(role)

    # members: membership of a sealed role is fixed at the layer that sealed it —
    # otherwise a project could simply write itself into `quality-owner`.
    for actor, assigned in doc.get("members", {}).items():
        sealed = [r for r in assigned if r in eff.sealed_roles and eff.role_origin.get(r, label) != label]
        if sealed and scope != "org":
            raise PolicyError(
                f"{label}: cannot assign sealed role(s) {', '.join(sorted(sealed))} to '{actor}'. "
                "Sealed roles are granted only by the layer that defines them")
        existing = eff.members.setdefault(actor, [])
        for role in assigned:
            if role not in existing:
                existing.append(role)

    # approvals: quorum only rises, required roles only accumulate, SoD only turns
    # on, expiry only shortens.
    for target, raw in doc.get("approvals", {}).items():
        rule = _validate_approval_rule(raw, f"{label}.approvals['{target}']")
        current = eff.approvals.get(target)
        if current is None:
            eff.approvals[target] = dict(rule)
            continue
        where = f"{label}.approvals['{target}']"
        _tighten_int(current["quorum"], rule["quorum"], "quorum", where, "up")
        if current["separation_of_duties"] and not rule["separation_of_duties"]:
            raise PolicyError(
                f"{where}: separation_of_duties cannot be turned off once an upstream layer requires it")
        _tighten_int(current["expires_hours"], rule["expires_hours"], "expires_hours", where, "down")
        merged = dict(current)
        merged["quorum"] = max(current["quorum"], rule["quorum"])
        merged["roles"] = sorted(set(current["roles"]) | set(rule["roles"]))
        merged["separation_of_duties"] = current["separation_of_duties"] or rule["separation_of_duties"]
        if rule["expires_hours"] is not None:
            merged["expires_hours"] = (rule["expires_hours"] if current["expires_hours"] is None
                                       else min(current["expires_hours"], rule["expires_hours"]))
        eff.approvals[target] = merged

    # waivers: shorter lifetime, more non-waivable criteria, fewer granting roles.
    if "waivers" in doc:
        rule = _validate_waiver_rule(doc["waivers"], f"{label}.waivers")
        current = eff.waivers or {"max_days": None, "grant_roles": [], "non_waivable": [],
                                  "required_for_force": False}
        where = f"{label}.waivers"
        _tighten_int(current["max_days"], rule["max_days"], "max_days", where, "down")
        if current["required_for_force"] and not rule["required_for_force"] and "required_for_force" in doc["waivers"]:
            raise PolicyError(
                f"{where}: required_for_force cannot be turned off once an upstream layer requires it")
        if current["grant_roles"] and rule["grant_roles"]:
            widened = set(rule["grant_roles"]) - set(current["grant_roles"])
            if widened:
                raise PolicyError(
                    f"{where}: grant_roles adds {', '.join(sorted(widened))}, which the upstream layer "
                    "does not allow to grant waivers. A downstream layer may only narrow this list")
        eff.waivers = {
            "max_days": (rule["max_days"] if current["max_days"] is None
                         else min(current["max_days"], rule["max_days"] or current["max_days"])),
            "grant_roles": sorted(set(rule["grant_roles"]) & set(current["grant_roles"])) if
                           (current["grant_roles"] and rule["grant_roles"]) else
                           sorted(set(rule["grant_roles"]) | set(current["grant_roles"])),
            "non_waivable": sorted(set(current["non_waivable"]) | set(rule["non_waivable"])),
            "required_for_force": current["required_for_force"] or rule["required_for_force"],
        }

    audit = doc.get("audit", {})
    if audit.get("chain_required") is False and eff.audit_chain_required:
        raise PolicyError(
            f"{label}.audit.chain_required cannot be turned off once an upstream layer requires it")
    eff.audit_chain_required = eff.audit_chain_required or bool(audit.get("chain_required"))

    eff.layers.append(layer)
    eff.active = True


_SCOPE_RANK = {"org": 0, "team": 1, "project": 2}


def resolve_layer_paths(root: pathlib.Path, binding: dict | None = None) -> list[pathlib.Path]:
    """Where the policy layers live, in application order.

    Explicit `policy_layers` in `.rig/org.json` wins and is used verbatim (that is
    how a team points at the org's shared policy checkout). Otherwise every
    `.rig/policy/*.json` is picked up and ordered org → team → project, so the
    zero-config case still stacks correctly.

    Relative entries resolve against `$RIG_POLICY_HOME` first — one shared
    clone of the org policy, referenced identically from every team repository —
    and against the repository root second.
    """
    home = os.environ.get("RIG_POLICY_HOME")
    listed = (binding or {}).get("policy_layers")
    if listed:
        out: list[pathlib.Path] = []
        for entry in listed:
            p = pathlib.Path(os.path.expanduser(entry))
            if p.is_absolute():
                out.append(p)
                continue
            if home:
                candidate = pathlib.Path(os.path.expanduser(home)) / p
                if candidate.is_file():
                    out.append(candidate)
                    continue
            out.append(root / p)
        return out

    policy_dir = root / ".rig" / "policy"
    if not policy_dir.is_dir():
        return []
    found: list[tuple[int, str, pathlib.Path]] = []
    for p in sorted(policy_dir.glob("*.json")):
        try:
            scope = json.loads(p.read_text(encoding="utf-8")).get("scope")
        except Exception:
            scope = None
        found.append((_SCOPE_RANK.get(scope, 99), p.name, p))
    return [p for _rank, _name, p in sorted(found, key=lambda t: (t[0], t[1]))]


def effective_policy(root: pathlib.Path, binding: dict | None = None) -> EffectivePolicy:
    """Load, order and fold every policy layer for this repository.

    Returns an inactive EffectivePolicy when nothing is configured — callers
    treat that as "governance is off" and behave exactly as v1 did.
    """
    if binding is None:
        from .identity import load_org_binding
        binding = load_org_binding(root).raw
    eff = EffectivePolicy()
    for path in resolve_layer_paths(root, binding):
        _fold(eff, load_policy_document(path), path)
    if eff.active and binding.get("team") and not eff.team:
        eff.team = binding["team"]
    return eff


def describe_layers(eff: EffectivePolicy) -> list[str]:
    """Human-readable one-liners for `govern policy show` and conformance reports."""
    return [f"{layer.label()}  ({layer.path})" if layer.path else layer.label()
            for layer in eff.layers]
