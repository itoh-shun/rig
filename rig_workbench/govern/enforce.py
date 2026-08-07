"""govern.enforce — the one place the governance layer meets the existing flow.

`workbench accept` was already the choke point of rig: nothing enters the main
working tree without passing through it. v2 does not add a second choke point,
it makes that one governed. Four questions, in the order that produces the most
useful error message:

  1. may this actor accept at all?              (rbac)
  2. is the approval requirement satisfied?     (approval)
  3. if forcing: may this actor force?          (rbac)
  4. if forcing: is every bypassed criterion    (waiver)
     covered by a live waiver?

Everything here is inert when no policy is configured — `check_accept` returns
"no findings" and `accept` behaves exactly as it did in v1. That is the whole
compatibility contract: the governance layer is something a team turns on.
"""

from __future__ import annotations

import dataclasses
import pathlib

from . import ledger, waiver
from .approval import evaluate, load_approvals
from .identity import current_actor, load_org_binding
from .policy import EffectivePolicy, PolicyError, effective_policy
from .rbac import can, roles_of


@dataclasses.dataclass
class Verdict:
    """The governance layer's answer for one accept attempt."""
    active: bool
    actor: str
    org: str | None = None
    team: str | None = None
    lines: list[str] = dataclasses.field(default_factory=list)
    blocked: str | None = None
    waivers_used: list[dict] = dataclasses.field(default_factory=list)
    approvals_counted: int = 0
    approvals_required: int = 0


def load(root: pathlib.Path) -> tuple[EffectivePolicy, str | None]:
    """The effective policy plus a load error, if any.

    A broken policy document is *not* swallowed: unlike the legacy
    `.rig/access.json`, whose malformed-file fallback is "unrestricted", a policy
    layer that fails to parse must stop the flow. The whole point of the layer is
    that a downstream repository cannot quietly lose the org's rules — and
    "somebody put a comma in the wrong place" is exactly how that would happen.
    """
    try:
        return effective_policy(root), None
    except PolicyError as e:
        return EffectivePolicy(), str(e)


def check_accept(root: pathlib.Path, task: dict, *, bypassed: list[str],
                 force: bool, head: str | None = None) -> Verdict:
    """Decide whether this accept may proceed. Never writes; `record_accept` does that."""
    actor = current_actor(root)
    eff, error = load(root)
    if error:
        return Verdict(active=True, actor=actor, blocked=f"policy layer does not load: {error}")
    if not eff.active:
        return Verdict(active=False, actor=actor)

    binding = load_org_binding(root)
    verdict = Verdict(active=True, actor=actor, org=eff.org or binding.org,
                      team=eff.team or binding.team)
    roles = roles_of(eff, actor)
    verdict.lines.append(
        f"governance: {eff.org}{'/' + eff.team if eff.team else ''}  ·  actor {actor}"
        f" ({', '.join(roles) or 'no role'})  ·  policy {', '.join(layer.label() for layer in eff.layers)}")

    decision = can(eff, actor, "accept")
    if not decision.allowed:
        verdict.blocked = f"not permitted to accept: {decision.reason}"
        return verdict

    status = evaluate(eff, task, load_approvals(root, task.get("task_id", "")), head=head)
    verdict.approvals_counted = status.counted
    verdict.approvals_required = status.required
    if status.required or status.counting or status.denials:
        verdict.lines.extend("  " + line for line in status.lines())
    if not status.satisfied:
        if status.denials:
            who = ", ".join(d.get("actor", "?") for d in status.denials)
            verdict.blocked = (f"approval denied by {who}. Resolve the objection, then ask for a fresh "
                               "approval (`rig-wb govern approve grant`)")
        else:
            verdict.blocked = (
                f"approval requirement not met ({status.counted}/{status.required}). "
                f"Ask a qualified approver to run `rig-wb govern approve grant {task.get('task_id')}`")
        return verdict

    if not force:
        return verdict

    force_decision = can(eff, actor, "accept.force")
    if not force_decision.allowed:
        verdict.blocked = f"not permitted to use --force: {force_decision.reason}"
        return verdict

    rule = eff.waivers or {}
    non_waivable = set(rule.get("non_waivable") or [])
    blocked_criteria = sorted(set(bypassed) & non_waivable)
    if blocked_criteria:
        verdict.blocked = (f"criteria {', '.join(blocked_criteria)} are non-waivable under the org policy — "
                           "--force cannot override them")
        return verdict

    if rule.get("required_for_force"):
        cover = waiver.coverage(root, bypassed, task_type=task.get("task_type") or "",
                                task_id=task.get("task_id") or "")
        verdict.waivers_used = cover.used
        for w in cover.used:
            verdict.lines.append(f"  waiver {w['id']} covers {', '.join(w.get('criteria') or [])} "
                                 f"until {w.get('expires')} (granted by {w.get('granted_by')})")
        for w in cover.expired:
            verdict.lines.append(f"  waiver {w['id']} has lapsed ({w.get('expires')}) — it does not cover this")
        if cover.uncovered:
            verdict.blocked = (
                f"--force requires a live waiver for {', '.join(cover.uncovered)} under this policy. "
                f"Ask someone with `waiver.grant` to run `rig-wb govern waiver grant`")
            return verdict

    return verdict


def record_accept(root: pathlib.Path, task: dict, verdict: Verdict, *,
                  forced: bool, bypassed: list[str], gate_status: str) -> None:
    """Write the accept into the tamper-evident ledger.

    Recorded for every governed accept, not just forced ones: "who applied what,
    when, under which policy" is the question an audit actually asks, and a
    ledger that only holds the exceptions cannot answer it.
    """
    if not verdict.active:
        return
    ledger.append(
        root, "accept.force" if forced else "accept",
        actor=verdict.actor, subject=task.get("task_id", ""),
        org=verdict.org, team=verdict.team,
        data={
            "task_type": task.get("task_type"),
            "recipe": task.get("recipe"),
            "gate_status": gate_status,
            "bypassed": sorted(bypassed),
            "approvals": f"{verdict.approvals_counted}/{verdict.approvals_required}",
            "waivers": [w.get("id") for w in verdict.waivers_used],
        },
    )
