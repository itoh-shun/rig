"""govern.approval — the approval flow, as a record rather than a conversation.

"Someone reviewed it" is the weakest sentence in software governance, because
nothing about it is checkable after the fact. An approval here is a stored
decision with four properties the acceptance path can actually verify:

  quorum                  how many distinct approvals are required
  role qualification      which roles count toward that quorum
  separation of duties    the author's own approval never counts
  freshness               an approval is bound to the commit it approved and to
                          a wall-clock expiry; rewrite the branch or let it go
                          stale and the approval stops counting

The last one is the difference between an approval flow and a rubber stamp. An
approval that survives a force-push approves code nobody read.

State lives beside the run it belongs to, in
`.rig/runs/<task-id>/approvals.json`, so it travels with the task and is
discarded with it.
"""

from __future__ import annotations

import dataclasses
import datetime
import json
import pathlib

from .policy import EffectivePolicy
from .rbac import roles_of

VALID_DECISIONS = ("approve", "deny")


def approvals_path(root: pathlib.Path, task_id: str) -> pathlib.Path:
    return root / ".rig" / "runs" / task_id / "approvals.json"


def load_approvals(root: pathlib.Path, task_id: str) -> dict:
    p = approvals_path(root, task_id)
    if not p.is_file():
        return {"task_id": task_id, "decisions": []}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"task_id": task_id, "decisions": [], "error": f"{p} is not valid JSON"}
    if not isinstance(data, dict):
        return {"task_id": task_id, "decisions": [], "error": f"{p} must be a JSON object"}
    data.setdefault("decisions", [])
    return data


def save_approvals(root: pathlib.Path, task_id: str, data: dict) -> None:
    p = approvals_path(root, task_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def make_decision(*, actor: str, decision: str, roles: list[str],
                  head: str | None = None, note: str = "") -> dict:
    """One decision record. Pure — the caller decides where it is stored, which is
    what lets a workbench task and an orchestrator stage share this arithmetic."""
    if decision not in VALID_DECISIONS:
        raise ValueError(f"decision must be one of {', '.join(VALID_DECISIONS)}")
    return {
        "actor": actor,
        "decision": decision,
        "roles": list(roles),
        "head": head,
        "note": note,
        "ts": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
    }


def upsert(decisions: list[dict], entry: dict) -> list[dict]:
    """Add a decision, replacing any earlier one by the same actor. People change
    their minds, and two contradictory records from one person would make the
    quorum arithmetic meaningless."""
    return [d for d in decisions if d.get("actor") != entry.get("actor")] + [entry]


def record_decision(root: pathlib.Path, task_id: str, *, actor: str, decision: str,
                    roles: list[str], head: str | None = None, note: str = "") -> dict:
    """Append one decision to a workbench task's approval file."""
    entry = make_decision(actor=actor, decision=decision, roles=roles, head=head, note=note)
    data = load_approvals(root, task_id)
    data["decisions"] = upsert(data["decisions"], entry)
    save_approvals(root, task_id, data)
    return entry


@dataclasses.dataclass
class ApprovalStatus:
    required: int
    counted: int
    satisfied: bool
    denials: list[dict]
    counting: list[dict]
    ignored: list[tuple[dict, str]]
    rule: dict

    def lines(self) -> list[str]:
        """Report block shared by `govern approve status` and the accept preview."""
        out = [f"approvals: {self.counted}/{self.required}"
               + ("  ✓ satisfied" if self.satisfied else "  … not yet satisfied")]
        rule_bits = []
        if self.rule.get("roles"):
            rule_bits.append(f"roles: {', '.join(self.rule['roles'])}")
        if self.rule.get("separation_of_duties"):
            rule_bits.append("separation of duties")
        if self.rule.get("expires_hours"):
            rule_bits.append(f"expires after {self.rule['expires_hours']}h")
        if rule_bits:
            out.append(f"  rule: {' · '.join(rule_bits)}")
        for d in self.counting:
            out.append(f"  ✓ {d['actor']} ({', '.join(d.get('roles') or []) or 'no role'}) {d['ts']}")
        for d, why in self.ignored:
            out.append(f"  · {d['actor']} — not counted: {why}")
        for d in self.denials:
            out.append(f"  ✗ {d['actor']} denied: {d.get('note') or '(no note)'}")
        return out


def _age_hours(ts: str) -> float | None:
    try:
        then = datetime.datetime.fromisoformat(ts)
    except (TypeError, ValueError):
        return None
    return (datetime.datetime.now().astimezone() - then).total_seconds() / 3600.0


def evaluate(eff: EffectivePolicy, task: dict, approvals: dict,
             *, head: str | None = None, rule: dict | None = None,
             author: str | None = None) -> ApprovalStatus:
    """Decide whether this approval requirement is met right now.

    `head` is the tip as it stands at evaluation time. When a decision recorded a
    different head, the branch moved after the approval and that approval no
    longer applies to the code being accepted.

    `rule` / `author` override what would be read from the task, so an
    orchestrator stage gate can reuse the same arithmetic with the rule that
    governs that stage and the identity that ran it.
    """
    rule = rule if rule is not None else eff.approval_rule(task.get("task_type") or "")
    required = int(rule.get("quorum") or 0)
    author = author if author is not None else (task.get("actor") or task.get("created_by") or "")
    # Role qualification needs a role system to check against. A recipe's own
    # `human_gate` runs in repositories with no policy at all, and holding its
    # approvals to roles nobody can hold would deadlock the stage forever.
    needed_roles = set(rule.get("roles") or []) if eff.active else set()
    sod = bool(rule.get("separation_of_duties", True))
    expires = rule.get("expires_hours")

    denials = [d for d in approvals.get("decisions", []) if d.get("decision") == "deny"]
    counting: list[dict] = []
    ignored: list[tuple[dict, str]] = []
    seen: set[str] = set()
    for d in approvals.get("decisions", []):
        if d.get("decision") != "approve":
            continue
        actor = d.get("actor") or ""
        if sod and author and actor == author:
            ignored.append((d, "the author's own approval never counts (separation of duties)"))
            continue
        if actor in seen:
            ignored.append((d, "duplicate approval from the same actor"))
            continue
        held = set(d.get("roles") or []) or set(roles_of(eff, actor))
        if needed_roles and not (held & needed_roles):
            ignored.append((d, f"role(s) {', '.join(sorted(held)) or '(none)'} do not include "
                               f"{' or '.join(sorted(needed_roles))}"))
            continue
        if head and d.get("head") and d["head"] != head:
            ignored.append((d, f"approved {d['head'][:12]}, the branch is now at {head[:12]} "
                               "(the branch moved after this approval)"))
            continue
        if expires:
            age = _age_hours(d.get("ts") or "")
            if age is not None and age > float(expires):
                ignored.append((d, f"expired ({age:.0f}h old, limit {expires}h)"))
                continue
        seen.add(actor)
        counting.append(d)

    satisfied = (not denials) and len(counting) >= required
    return ApprovalStatus(required=required, counted=len(counting), satisfied=satisfied,
                          denials=denials, counting=counting, ignored=ignored, rule=rule)
