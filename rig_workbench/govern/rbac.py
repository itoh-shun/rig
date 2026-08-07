"""govern.rbac — roles → permissions, resolved through the policy layers.

v1's `.rig/access.json` answered exactly one question: is this name on the list
of people allowed to `accept` this task_type? That is a permission system with a
single permission and no roles, which is the right size for one repository and
the wrong size for three teams.

v2 resolves an actor's roles from the effective policy's `members` map, unions
the permissions of those roles, and answers `can(actor, permission)`. The legacy
allowlist keeps working underneath: where no policy is configured, `accept`
still consults `.rig/access.json` exactly as before (see workbench.accept).

Denials are explained, never bare. "'bob' lacks 'accept.force'" is useless
without "roles: developer (from team:team-a); accept.force is held by
quality-owner" — a permission system nobody can read is a permission system
people route around.
"""

from __future__ import annotations

import dataclasses

from .policy import PERMISSIONS, EffectivePolicy


class PermissionDenied(Exception):
    """An actor attempted something their roles do not permit."""


@dataclasses.dataclass(frozen=True)
class Decision:
    allowed: bool
    actor: str
    permission: str
    roles: list[str]
    reason: str

    def __bool__(self) -> bool:  # `if can(...):` reads naturally
        return self.allowed


def roles_of(eff: EffectivePolicy, actor: str) -> list[str]:
    """Roles held by `actor`, including anything granted to the `*` wildcard.

    The wildcard is how an org gives every engineer the baseline role without
    listing the whole company in a JSON file.
    """
    out: list[str] = []
    for key in ("*", actor):
        for role in eff.members.get(key, []):
            if role not in out:
                out.append(role)
    return out


def permissions_of(eff: EffectivePolicy, actor: str) -> set[str]:
    granted: set[str] = set()
    for role in roles_of(eff, actor):
        granted |= set(eff.roles.get(role, []))
    return granted


def holders_of(eff: EffectivePolicy, permission: str) -> list[str]:
    """Which roles carry a permission — the actionable half of a denial message."""
    return sorted(role for role, perms in eff.roles.items() if permission in perms)


def can(eff: EffectivePolicy, actor: str, permission: str) -> Decision:
    """Decide whether `actor` may exercise `permission`.

    Governance off (no policy layers) → allowed, with "governance not
    configured" as the reason. That is the v1 behaviour and it is deliberate:
    installing rig must never lock anybody out of their own repository.
    """
    if permission not in PERMISSIONS:
        raise ValueError(f"unknown permission '{permission}' (known: {', '.join(PERMISSIONS)})")
    if not eff.active:
        return Decision(True, actor, permission, [], "governance not configured (unrestricted)")
    actor_roles = roles_of(eff, actor)
    if not eff.roles:
        return Decision(True, actor, permission, actor_roles,
                        "policy defines no roles (permissions unrestricted)")
    if permission in permissions_of(eff, actor):
        return Decision(True, actor, permission, actor_roles,
                        f"granted via {', '.join(actor_roles) or 'no role'}")
    holders = holders_of(eff, permission)
    detail = f"held by {', '.join(holders)}" if holders else "no role in this policy holds it"
    return Decision(False, actor, permission, actor_roles,
                    f"'{actor}' has role(s) {', '.join(actor_roles) or '(none)'}; "
                    f"'{permission}' is {detail}")


def require(eff: EffectivePolicy, actor: str, permission: str) -> Decision:
    """`can`, but raises PermissionDenied instead of returning a false Decision."""
    decision = can(eff, actor, permission)
    if not decision.allowed:
        raise PermissionDenied(decision.reason)
    return decision


def explain(eff: EffectivePolicy, actor: str) -> list[str]:
    """Full permission report for one actor — the body of `govern whoami`."""
    if not eff.active:
        return [f"actor: {actor}", "policy: (none configured — every action is unrestricted)"]
    actor_roles = roles_of(eff, actor)
    granted = sorted(permissions_of(eff, actor))
    lines = [
        f"actor: {actor}",
        f"org:   {eff.org or '(unset)'}" + (f"   team: {eff.team}" if eff.team else ""),
        f"roles: {', '.join(actor_roles) or '(none)'}",
    ]
    if not eff.roles:
        lines.append("permissions: (policy defines no roles — unrestricted)")
        return lines
    lines.append(f"permissions ({len(granted)}/{len(PERMISSIONS)}):")
    for perm in PERMISSIONS:
        mark = "✓" if perm in granted else "·"
        holders = holders_of(eff, perm)
        note = "" if perm in granted else f"  (held by {', '.join(holders)})" if holders else "  (unheld)"
        lines.append(f"  {mark} {perm}{note}")
    return lines
