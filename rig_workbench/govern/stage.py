"""govern.stage — governance at a recipe step, not only at accept (v2.1).

v2 governed the one place changes enter the working tree. That is the right
first place, and it is not the only place a team needs a person in the loop:
an architecture decision, a release sign-off or a data-migration review has to
happen *at that stage*, not retroactively at the end.

Two step-level declarations make that possible, and neither invents a new
mechanism — both reuse what v2 already built:

    steps:
      - id: architecture_review
        actor: architect            # an org ROLE (not an LLM persona) owns this stage
        human_gate: true            # the run halts here until a person signs off

`actor` answers "whose stage is this" using the policy's roles. `human_gate`
suspends the run in `awaiting_approval` until the approval requirement is met —
quorum, qualifying roles, separation of duties and freshness all inherited from
govern.approval, unchanged.

**Tightening only, again.** A stage's effective rule is the recipe's request and
the org's `stage:<id>` rule merged the same way policy layers merge: the higher
quorum, the union of roles, the shorter expiry, separation of duties if either
asks for it. A recipe cannot talk the org down, and an org that says nothing
about a stage leaves the recipe's own request standing.
"""

from __future__ import annotations

import pathlib

from .approval import ApprovalStatus, evaluate
from .identity import current_actor
from .policy import EffectivePolicy
from .rbac import roles_of

_DEFAULT_STAGE_RULE = {"quorum": 1, "roles": [], "separation_of_duties": True,
                       "expires_hours": None}


class StageConfigError(Exception):
    """A step's `actor` / `human_gate` declaration is not usable."""


def parse_human_gate(value: object, *, where: str) -> dict | None:
    """Normalise a step's `human_gate:` into a rule, or None when it asks for none.

    Accepted: `true` (one approval from anyone qualified), or an object with
    `quorum` / `roles` / `separation_of_duties` / `expires_hours`. `false` and
    absence both mean no human gate — a step should not become blocking by
    accident.
    """
    if value in (None, False):
        return None
    if value is True:
        return dict(_DEFAULT_STAGE_RULE)
    if not isinstance(value, dict):
        raise StageConfigError(
            f"{where}: human_gate must be true or an object with quorum/roles/"
            f"separation_of_duties/expires_hours (got {type(value).__name__})")
    allowed = ("quorum", "roles", "separation_of_duties", "expires_hours")
    unknown = [k for k in value if k not in allowed]
    if unknown:
        raise StageConfigError(f"{where}: human_gate has unknown key(s) "
                               f"{', '.join(sorted(unknown))} (allowed: {', '.join(allowed)})")
    quorum = value.get("quorum", 1)
    if not isinstance(quorum, int) or isinstance(quorum, bool) or quorum < 1:
        raise StageConfigError(f"{where}: human_gate.quorum must be an integer >= 1 "
                               "(a gate that needs nobody is not a gate)")
    roles = value.get("roles", [])
    if not isinstance(roles, list) or not all(isinstance(r, str) and r for r in roles):
        raise StageConfigError(f"{where}: human_gate.roles must be a list of role names")
    sod = value.get("separation_of_duties", True)
    if not isinstance(sod, bool):
        raise StageConfigError(f"{where}: human_gate.separation_of_duties must be a boolean")
    expires = value.get("expires_hours")
    if expires is not None and (not isinstance(expires, (int, float))
                                or isinstance(expires, bool) or expires <= 0):
        raise StageConfigError(f"{where}: human_gate.expires_hours must be a positive number")
    return {"quorum": quorum, "roles": sorted(set(roles)),
            "separation_of_duties": sod, "expires_hours": expires}


def _merge_tighter(recipe_rule: dict | None, policy_rule: dict | None) -> dict | None:
    """The stricter of the two, field by field. Neither side can relax the other."""
    if recipe_rule is None and policy_rule is None:
        return None
    if recipe_rule is None:
        return dict(policy_rule)
    if policy_rule is None:
        return dict(recipe_rule)
    expiries = [e for e in (recipe_rule.get("expires_hours"), policy_rule.get("expires_hours"))
                if e is not None]
    return {
        "quorum": max(int(recipe_rule.get("quorum") or 0), int(policy_rule.get("quorum") or 0)),
        "roles": sorted(set(recipe_rule.get("roles") or []) | set(policy_rule.get("roles") or [])),
        "separation_of_duties": bool(recipe_rule.get("separation_of_duties", True))
                                or bool(policy_rule.get("separation_of_duties", True)),
        "expires_hours": min(expiries) if expiries else None,
    }


def stage_rule(eff: EffectivePolicy, step: dict) -> dict | None:
    """The approval rule governing this step, or None when nobody has to sign off.

    A step declaring `actor:` but no `human_gate` is *not* gated — `actor` states
    ownership, which is useful on its own (it is what `whoami`-style reporting and
    the plan view read). Requiring a person is what `human_gate` is for, and
    conflating the two would make every annotated step blocking.
    """
    recipe_rule = parse_human_gate(step.get("human_gate"),
                                   where=f"step `{step.get('id')}`")
    if recipe_rule is not None and not recipe_rule["roles"] and step.get("actor"):
        # `actor` names who owns the stage; with no explicit roles, they are who signs.
        recipe_rule = {**recipe_rule, "roles": [step["actor"]]}
    policy_rule = eff.stage_approval_rule(step.get("id") or "") if eff.active else None
    return _merge_tighter(recipe_rule, policy_rule)


def actor_mismatch(eff: EffectivePolicy, step: dict, actor: str) -> str | None:
    """A note when the identity running this step does not hold its owning role.

    **Advisory, never blocking.** Enforcing "only an architect may execute this
    step" would break every CI-driven run for no safety gain — the process that
    runs a stage is whoever launched the pipeline, and rig cannot verify that a
    human architect typed anything. What it *can* enforce is that an architect
    signed the result, and that is what the human gate does. This note exists so
    an unowned execution is visible rather than invisible.

    Inert without governance, and inert for a step that names no `actor`.
    """
    role = step.get("actor")
    if not role or not eff.active or not eff.roles:
        return None
    if role not in eff.roles:
        return (f"step `{step.get('id')}` declares actor `{role}`, which the policy does not define "
                f"(known roles: {', '.join(sorted(eff.roles)) or 'none'})")
    if role in roles_of(eff, actor):
        return None
    holders = sorted(a for a in eff.members if role in roles_of(eff, a))
    who = f" (held by {', '.join(holders)})" if holders else " (nobody holds it)"
    return (f"step `{step.get('id')}` is owned by role `{role}`, and it is running as {actor}, "
            f"who holds {', '.join(roles_of(eff, actor)) or 'no role'}{who}")


def evaluate_stage(eff: EffectivePolicy, step: dict, decisions: list[dict], *,
                   author: str = "", head: str | None = None) -> ApprovalStatus | None:
    """Approval status for one step, or None when the step has no human gate."""
    rule = stage_rule(eff, step)
    if rule is None:
        return None
    return evaluate(eff, {}, {"decisions": list(decisions)},
                    head=head, rule=rule, author=author)


def describe(eff: EffectivePolicy, step: dict) -> str:
    """One line for `plan` / `status`, or "" when the step is not governed."""
    bits = []
    if step.get("actor"):
        bits.append(f"actor {step['actor']}")
    rule = stage_rule(eff, step)
    if rule:
        detail = [f"human gate: {rule['quorum']} approval(s)"]
        if rule["roles"]:
            detail.append(f"from {', '.join(rule['roles'])}")
        if rule["separation_of_duties"]:
            detail.append("author excluded")
        if rule["expires_hours"]:
            detail.append(f"expires {rule['expires_hours']}h")
        bits.append(" · ".join(detail))
    return "  ".join(bits)


def run_actor(root: pathlib.Path | None = None) -> str:
    """The identity the orchestrator is running as (same resolution as everywhere else)."""
    return current_actor(root)
