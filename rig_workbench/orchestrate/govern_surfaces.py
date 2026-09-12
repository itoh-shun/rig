"""The governance this pillar asks, in the one module allowed to know where it lives.

Eleven of this pillar's cross-pillar edges point at `govern`, and they are all the same
request in different words: the orchestrator has reached a step, and it needs to know
whether a person has to sign off, whether this identity may, who this identity is, and
where the decision gets written down. None of that is the runner's rule. Quorum, qualifying
roles, separation of duties, freshness, the org→team→project tightening and the hash chain
that makes the record tamper-evident are all `govern`'s arithmetic, and a runner that
re-derived any of it would be a second governance implementation nobody audits.

They are not moved here; they are inverted. `commands.py` states what it needs of
governance as `StageGovernance` and `runstate.py` states what it needs as `StepGovernance`,
and this module satisfies those shapes without either of them naming another pillar. It is
declared a shell in `tests/test_layering_contract.py` for the reason `PORT_ADAPTERS` gives
for `ports/local.py`: an adapter exists precisely to hold what the protocol may not.

**`PolicyError` never crosses.** `cmd_approve` used to write `except PolicyError`, and a
type in an `except` clause is an edge exactly as much as a function in a call — the reason
`rig_surfaces._parse_human_gate` keeps `StageConfigError` on its side of the boundary. So
`policy()` below answers `(policy, refusal)` and the refusal crosses as the message the
command was going to print anyway, with its wording and its exit status unchanged. That is
the opposite of what `pack_surfaces.py` does with `PackError`, and the difference is which
side raises: this pillar only ever *caught* `PolicyError`, so the class can stop here,
while it *raises* `PackError` into a `packs/cli.py` handler, where two classes would be two
rules.

**The policy itself crosses as a token, not as a shape.** `commands.py` used to read
`eff.active` and pass `eff` to five `govern` functions; now it holds the value this module
gives it and hands it back, and every question about it — is there a stage rule, may this
actor approve, what roles do they hold — is a method here. So the judgement layer writes no
signature in another pillar's vocabulary, which is the half of the rule
`conformance.RunRecords` exists to make explicit: a type name is a design dependency
whether or not it costs an import.

**The imports stay inside the methods, and the `ImportError` guards come with them.**
`runstate` carried `except ImportError` around each of these imports — a packaging guard for
an install without the governance pillar — and answered "no gate" and "no identity"
respectively. The guard belongs with the import, so it moved here with it, and the two
methods that had it return the same sentinel the two call sites used to compute for
themselves.

The arrow stays one-way: this module imports no `orchestrate` judgement module.
"""

from __future__ import annotations

import pathlib
from typing import Any


class _GovernSurfaces:
    """The govern pillar, under the names this pillar's two protocols ask for."""

    # ── the policy layer ─────────────────────────────────────────────────────
    @staticmethod
    def policy(root: pathlib.Path) -> tuple[Any, str | None]:
        """The effective policy at `root`, or `(None, why it does not load)`."""
        from ..govern.policy import PolicyError, effective_policy

        try:
            return effective_policy(root), None
        except PolicyError as refused:
            return None, str(refused)

    @staticmethod
    def has_stage_rule(policy: Any, step: dict) -> bool:
        """Whether any approval rule governs this step."""
        from ..govern.stage import stage_rule

        return stage_rule(policy, step) is not None

    @staticmethod
    def stage_status(root: pathlib.Path, step: dict, decisions: list[dict], *,
                     author: str = "") -> Any:
        """This step's human-gate status, or None when it has no human gate.

        Resolved live against the effective policy, and a policy that fails to load raises
        rather than answering "no gate" — a broken document must not silently open a stage.
        None is also the answer when the governance pillar is not installed at all.
        """
        try:
            from ..govern.policy import effective_policy
            from ..govern.stage import evaluate_stage
        except ImportError:                            # pragma: no cover - packaging guard
            return None
        return evaluate_stage(effective_policy(root), step, decisions, author=author)

    # ── identity and permission ──────────────────────────────────────────────
    @staticmethod
    def current_actor(root: pathlib.Path) -> str:
        """The identity performing this action."""
        from ..govern.identity import current_actor

        return current_actor(root)

    @staticmethod
    def refusal(policy: Any, actor: str, permission: str) -> str | None:
        """Why `actor` may not exercise `permission`, or None when they may.

        None when governance is off, too: `govern.rbac.can` allows everything under an
        inactive policy, and `policy.active` was the only attribute of another pillar's
        object this pillar read. Folding the two together is what lets the policy cross as
        a token rather than as a shape.
        """
        from ..govern.rbac import can

        if not getattr(policy, "active", False):
            return None
        allowed = can(policy, actor, permission)
        return None if allowed.allowed else allowed.reason

    @staticmethod
    def actor_for(root: pathlib.Path, step: dict) -> tuple[str, str | None] | None:
        """`(actor, advisory note)` for a step that needs an identity, or None.

        None both when the governance pillar is not installed and when nothing about this
        step or this repository asks for an identity. That second exit is the cheap one and
        it stays first: resolving an identity shells out to `git config`, and doing that at
        every step START of every run — in repositories with no policy and no step that
        declares an owner — would be a subprocess per step to record a value nothing reads.
        """
        try:
            from ..govern.identity import current_actor, org_binding_path
            from ..govern.policy import effective_policy
            from ..govern.stage import actor_mismatch
        except ImportError:                            # pragma: no cover - packaging guard
            return None
        if not (step.get("actor") or step.get("human_gate")
                or org_binding_path(root).is_file()):
            return None
        actor = current_actor(root)
        return actor, actor_mismatch(effective_policy(root), step, actor)

    # ── recording a decision ─────────────────────────────────────────────────
    @staticmethod
    def decision(policy: Any, *, actor: str, decision: str, head: str | None,
                 note: str) -> dict:
        """One decision record, carrying the roles this actor holds under `policy`."""
        from ..govern.approval import make_decision
        from ..govern.rbac import roles_of

        return make_decision(actor=actor, decision=decision, roles=roles_of(policy, actor),
                             head=head, note=note)

    @staticmethod
    def upsert(decisions: list[dict], entry: dict) -> list[dict]:
        """`entry` added, replacing any earlier decision by the same actor."""
        from ..govern.approval import upsert

        return upsert(decisions, entry)

    @staticmethod
    def record(root: pathlib.Path, action: str, *, actor: str, subject: str,
               data: dict) -> None:
        """Mirror one governance event into the tamper-evident ledger.

        The org binding is read here rather than handed in: `org` and `team` are the only
        two things the caller took from it, and reading it at the point of the write is what
        keeps `govern.identity.OrgBinding` from appearing in a signature this pillar writes.
        """
        from ..govern import ledger
        from ..govern.identity import load_org_binding

        binding = load_org_binding(root)
        ledger.append(root, action, actor=actor, subject=subject,
                      org=binding.org, team=binding.team, data=data)


#: The shipped implementation, named as a value so a caller passes one rather than a module
#: whose address it would have to know — as `ports.local`'s instances are.
GOVERN_SURFACES = _GovernSurfaces()
