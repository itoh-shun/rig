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

from ..ports import Clock, FileStore
from ..ports.local import LOCAL_FILES, SYSTEM_CLOCK
from . import ledger
from .policy import EffectivePolicy
from .rbac import roles_of

VALID_DECISIONS = ("approve", "deny")


class _UnknownHead:
    """The type of `UNKNOWN_HEAD`. One instance, compared with `is`."""

    __slots__ = ()

    def __repr__(self) -> str:
        return "UNKNOWN_HEAD"


#: "There is a commit this accept is about, and I could not resolve it."
#:
#: `evaluate`'s `head=None` means *there is no such commit* — an orchestrator stage gate
#: has none, and neither does a `--no-worktree` run — and it correctly holds no approval to
#: one. A caller that cannot tell git what its task branch points at is in the opposite
#: situation, and passing `None` for it turns the freshness rule off by omission: a deleted
#: task branch made every approval count, which is the strongest a missing ref can possibly
#: be read as. This value says *unknown*, and unknown is not a match: every approval is
#: ignored with its own line, and the caller refuses for want of a quorum rather than
#: proceeding on approvals it could not check.
UNKNOWN_HEAD = _UnknownHead()


def approvals_path(root: pathlib.Path, task_id: str) -> pathlib.Path:
    return root / ".rig" / "runs" / task_id / "approvals.json"


def load_approvals(root: pathlib.Path, task_id: str, *,
                   files: FileStore = LOCAL_FILES) -> dict:
    p = approvals_path(root, task_id)
    if not files.is_file(p):
        return {"task_id": task_id, "decisions": []}
    try:
        data = json.loads(files.read_text(p))
    except json.JSONDecodeError:
        return {"task_id": task_id, "decisions": [], "error": f"{p} is not valid JSON"}
    if not isinstance(data, dict):
        return {"task_id": task_id, "decisions": [], "error": f"{p} must be a JSON object"}
    data.setdefault("decisions", [])
    return data


def save_approvals(root: pathlib.Path, task_id: str, data: dict, *,
                   files: FileStore = LOCAL_FILES) -> None:
    files.write_text(approvals_path(root, task_id),
                     json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def make_decision(*, actor: str, decision: str, roles: list[str],
                  head: str | None = None, branch_tip: str | None = None, note: str = "",
                  clock: Clock = SYSTEM_CLOCK) -> dict:
    """One decision record. Pure — the caller decides where it is stored, which is
    what lets a workbench task and an orchestrator stage share this arithmetic.

    **Two shas, and the ledger schema keeps both meanings.** `head` is what it has always
    been — the HEAD of the tree the approver was standing in — and it is left alone so that
    a decision written by an older rig still says what it said. `branch_tip` is the commit
    the task branch pointed at, resolved in the main tree, which is what `accept` squashes;
    it is `None` for a decision that has no branch to resolve (an orchestrator stage gate)
    and absent from every record written before this field existed. `_bound_to` below is
    the one place that decides which of the two an approval is held to.
    """
    if decision not in VALID_DECISIONS:
        raise ValueError(f"decision must be one of {', '.join(VALID_DECISIONS)}")
    return {
        "actor": actor,
        "decision": decision,
        "roles": list(roles),
        "head": head,
        "branch_tip": branch_tip,
        "note": note,
        "ts": clock.stamp(),
    }


def upsert(decisions: list[dict], entry: dict) -> list[dict]:
    """Add a decision, replacing any earlier one by the same actor. People change
    their minds, and two contradictory records from one person would make the
    quorum arithmetic meaningless."""
    return [d for d in decisions if d.get("actor") != entry.get("actor")] + [entry]


def record_decision(root: pathlib.Path, task_id: str, *, actor: str, decision: str,
                    roles: list[str], head: str | None = None, branch_tip: str | None = None,
                    note: str = "", clock: Clock = SYSTEM_CLOCK,
                    files: FileStore = LOCAL_FILES) -> dict:
    """Append one decision to a workbench task's approval file."""
    entry = make_decision(actor=actor, decision=decision, roles=roles, head=head,
                          branch_tip=branch_tip, note=note, clock=clock)
    data = load_approvals(root, task_id, files=files)
    data["decisions"] = upsert(data["decisions"], entry)
    save_approvals(root, task_id, data, files=files)
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


def _bound_to(decision: dict) -> str | None:
    """The commit one approval approved, in the terms `evaluate`'s `head` is given in.

    `branch_tip` wins where a decision has one: it is the commit `accept` squashes. A record
    written before that field existed carries only `head`, the HEAD of the tree the approver
    stood in, and it is compared against the branch tip too — the safer of the two readings,
    because the alternative compared a worktree HEAD against a worktree HEAD, so a detached
    worktree made the check vacuous. No sha at all is neither a match nor a mismatch:
    `evaluate` leaves such a decision counting, exactly as it always has.
    """
    return decision.get("branch_tip") or decision.get("head")


#: The ledger action that attests each decision word. `govern approve grant` writes
#: `approval.grant` and `govern approve deny` writes `approval.deny`; nothing else in the
#: chain claims to be an approval, and an entry under any other action never attests one.
_ATTESTING_ACTION = {"approval.grant": "approve", "approval.deny": "deny"}

#: The ignore reason a decision the chain does not attest is refused with. One sentence,
#: because it is the only one a reader needs: the file says a decision happened and the
#: tamper-evident record does not.
UNATTESTED = "no ledger entry attests this decision"


@dataclasses.dataclass(frozen=True)
class Attestations:
    """The `approval.grant` / `approval.deny` entries the chained ledger holds, and whether
    a decision without one is refused.

    **The hole this closes.** `approvals.json` is plain JSON in a tree the task's own author
    can write, so a decision that was never granted reads back as a real one. Measured
    before this existed: a hand-written `{"actor": "bob", "decision": "approve", ...}` with
    no `approval.grant` anywhere in the ledger printed `approvals: 1/1  ✓ satisfied` and let
    `accept` through; the ledger's only trace of the whole episode was the `accept.force`
    entry written afterwards. G2 put the verdict and both shas into the grant entry, which
    made the forgery findable by a person reading the chain; this is what stops it without
    one.

    **The match, and why it has two shapes.** Strictly: an entry whose `subject` is the task,
    whose `actor` is the approver, whose action names the same decision word, and whose
    `data.branch_tip`/`data.head` is the commit the decision claims. Loosely — task_id,
    actor and decision only — for an entry written **before G2** (b5016ed), which carried
    only the task type and the note and therefore cannot be held to a sha it never recorded.
    Holding such an entry to a sha it never recorded would refuse work the chain plainly
    attests, purely because the chain is older than the field — so an old entry keeps
    counting its decision under the looser match, and only entries that *do* carry the shas
    are held to them.

    **When it is enforced.** A ledger that is absent *and* unsignable cannot attest anything,
    and refusing every decision on that basis would break every repository that has not
    turned the chain on. So: `audit.chain_required` makes enforcement unconditional, and
    otherwise the chain enforces itself once it is signed or could be — a repository holding
    `.rig/provenance.key`, which is every repository `workbench` has ever written a
    provenance record in (`ledger.signs_here`). An empty ledger in a keyed repository is
    therefore not a way out: it refuses every decision instead of counting them all. Nothing
    at all happens where govern is inactive (no `.rig/org.json`), because no caller there
    builds one of these.

    **A signature on the attesting entry is not required, and requiring it was wrong.** The
    first shape of this refused an entry that carried no `sig` wherever the repository had a
    key. Measured: the key is created lazily by the first successful accept
    (`workbench.state.load_or_create_provenance_key`), so on a fresh governed repository bob
    granted A and B — both real, both through the CLI, both unsigned because no key existed
    yet — and accepting A created the key, after which accepting B was refused with "no
    ledger entry attests this decision" while the ledger plainly attested it. The line was
    also wrong about why. It buys nothing in exchange: the chain needs no secret, so an
    attacker who can write `approvals.json` can append an unsigned `approval.grant` beside
    it either way. Whether the entries are signed is `verify`'s question, and it reports an
    unsigned entry in a keyed repository as a problem of its own.

    **What it does not claim, stated as two measurements rather than as a promise.** This is
    existence, not cryptography: whether the attesting entry is *genuine* is `govern audit
    verify` and conformance's `audit_ledger` check, which walk the hash chain and the HMACs —
    and the HMACs are the half that needs the key.

    *Without a key the reconciliation buys ordering and visibility, not resistance.* Measured
    on a keyless repository with `chain_required` set, one honest `policy.init` entry in the
    file, and an `approval.grant` appended by hand with `entry_hash` recomputed over it and
    `prev` pointed at the entry before — neither step needs a secret: `attests` True,
    `evaluate` `1/1 satisfied`, `verify` `ledger intact — 2 entries, unsigned` (`ok=True`,
    `signed=0`). The forger has to write a second file, in order, where a reader can see it.
    That is all it costs them.

    *A repository that had a key can be talked out of having had one.* Measured on a keyed
    repository whose ledger verified `1 entries, 1 signed`: strip every `sig`, recompute each
    `hash` and `prev`, append the forged grant, delete `.rig/provenance.key` — `enforced`
    drops to False, the hand-written decision counts `1/1`, and `verify` answers `ledger
    intact — 2 entries, unsigned`, `ok=True`. **This is documented and not bounded, and the
    earlier claim that the degradation is visible is withdrawn.** Bounding it needs a marker
    an attacker with write access to `.rig/` cannot remove, and every candidate inside `.rig/`
    — the chain, the key, a flag file — is writable by exactly the actor this is defending
    against, so a marker there would only have to be deleted too. The one durable answer is
    outside that directory: `audit.chain_required` in a policy layer, which is a git-tracked
    file, tightening-only from org to project, and enforces regardless of what the chain
    looks like. An org that wants this closed sets it; nothing rig can write into `.rig/`
    closes it for them. Deleting `.rig/ledger.jsonl` outright is the same class of move and
    has the same answer.
    """

    entries: tuple[dict, ...] = ()
    enforced: bool = False

    def attests(self, task_id: str, decision: dict, *, org: str | None = None,
                team: str | None = None) -> bool:
        return any(self._matches(entry, task_id, decision, org, team)
                   for entry in self.entries)

    def _matches(self, entry: dict, task_id: str, decision: dict,
                 org: str | None, team: str | None) -> bool:
        word = _ATTESTING_ACTION.get(entry.get("action") or "")
        if word is None or entry.get("subject") != task_id:
            return False
        if _same_actor(entry.get("actor"), decision.get("actor")):
            pass
        else:
            return False
        # The org and team the task is stamped with, where it has them. A decision carries
        # neither, so this is the one part of the match that comes from the task: an entry
        # written under another org's name does not attest this org's approval, and unlike
        # the shas these fields have been on every entry since v2, so holding old entries to
        # them costs nothing.
        if org and entry.get("org") and entry.get("org") != org:
            return False
        if team and entry.get("team") and entry.get("team") != team:
            return False
        data = entry.get("data") or {}
        if (data.get("decision") or word) != decision.get("decision"):
            return False
        if "branch_tip" not in data and "head" not in data:
            return True                      # pre-G2 entry: the looser match, by design
        return _bound_to(data) == _bound_to(decision)


def _same_actor(left, right) -> bool:
    """Whether two recorded identities are the same person.

    `RIG_ACTOR`, `RIG_USER` and `git config user.name` are typed by people and by shells,
    and `Bob` recorded on the decision against `bob` recorded on the entry is one person
    twice, not a forgery. Compared with surrounding space removed and case folded, which is
    the same latitude the identity is granted everywhere it is entered — and a loosening
    only in the sense that it stops refusing a match nobody disputes.
    """
    return (left or "").strip().casefold() == (right or "").strip().casefold()


def ledger_attestations(root: pathlib.Path, *, chain_required: bool = False,
                        files: FileStore = LOCAL_FILES) -> Attestations:
    """Read the chain and decide whether it is in a state to attest decisions.

    Built by the callers that hold a policy — `enforce.check_accept`, `govern approve
    status` and conformance's `approvals` check — and handed to `evaluate` as a parameter,
    the way G2 handed it the branch tip: the sha and now the chain arrive from the caller
    rather than being fetched from inside the arithmetic.
    """
    entries = tuple(e for e in ledger.read_ledger(root, files=files) if "_malformed" not in e)
    available = ledger.signs_here(root, files=files) or any("sig" in e for e in entries)
    return Attestations(entries=entries, enforced=bool(chain_required or available))


def _age_hours(ts: str, *, clock: Clock = SYSTEM_CLOCK) -> float | None:
    try:
        then = datetime.datetime.fromisoformat(ts)
    except (TypeError, ValueError):
        return None
    return (clock.now() - then).total_seconds() / 3600.0


def evaluate(eff: EffectivePolicy, task: dict, approvals: dict,
             *, head: str | _UnknownHead | None = None, rule: dict | None = None,
             author: str | None = None, attested: Attestations | None = None,
             clock: Clock = SYSTEM_CLOCK) -> ApprovalStatus:
    """Decide whether this approval requirement is met right now.

    `head` is **the commit the caller is about to apply**, as the caller resolves it —
    for `accept` that is the tip of the task branch read in the main tree, because that is
    what `git merge --squash` is handed, and for an orchestrator stage gate it is the head
    that stage ran on. When a decision was bound to a different commit, the branch moved
    after the approval and that approval no longer applies to the code being accepted.
    `UNKNOWN_HEAD` is the third answer, and it is not `None`: see the constant.

    This module never resolves a ref itself, and that is a layering fact as much as a
    design one: govern's judgement layer may import the six ports and nothing else that
    touches the outside, and `workbench.state.task_branch_tip` — the one resolver that
    knows the branch tip is read in the MAIN tree — lives in the other pillar. So the sha
    arrives as this parameter, from the caller that already holds all three
    (`accept.py`: `evaluated_head`, `branch_tip`, `worktree_head`).

    `rule` / `author` override what would be read from the task, so an
    orchestrator stage gate can reuse the same arithmetic with the rule that
    governs that stage and the identity that ran it.

    `attested` is the chained ledger, read by the caller (`ledger_attestations`) and passed
    in for the same layering reason `head` is: an approval that no `approval.grant` entry
    attests is not counted, because `approvals.json` is an ordinary writable file and the
    chain is the half of the record that cannot be edited unseen. `None` — a caller that
    holds no chain, which is every orchestrator stage gate — leaves the arithmetic exactly
    as it was. **Denials are not reconciled**: reconciliation only ever removes reasons to
    proceed, never reasons to stop, and a deny nobody can attest is at worst a nuisance
    while an approve nobody can attest is the whole of this bug.
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

    task_id = task.get("task_id") or approvals.get("task_id") or ""

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
        approved_sha = _bound_to(d)
        if head is UNKNOWN_HEAD:
            ignored.append((d, "the commit this would be spent on could not be resolved "
                               "(the task's branch does not exist), so no approval can be "
                               "matched to it"))
            continue
        if head and approved_sha and approved_sha != head:
            ignored.append((d, f"approved {approved_sha[:12]}, the branch is now at {head[:12]} "
                               "(the branch moved after this approval); re-approve at "
                               f"{head[:12]}"))
            continue
        if expires:
            age = _age_hours(d.get("ts") or "", clock=clock)
            if age is not None and age > float(expires):
                ignored.append((d, f"expired ({age:.0f}h old, limit {expires}h)"))
                continue
        # Last, so that a decision with an ordinary reason not to count (wrong role, moved
        # branch, expired) still says so: this line is reserved for a decision that would
        # otherwise have counted, which is exactly the forgery it exists to name.
        if attested is not None and attested.enforced and not attested.attests(
                task_id, d, org=task.get("org"), team=task.get("team")):
            ignored.append((d, UNATTESTED))
            continue
        seen.add(actor)
        counting.append(d)

    satisfied = (not denials) and len(counting) >= required
    return ApprovalStatus(required=required, counted=len(counting), satisfied=satisfied,
                          denials=denials, counting=counting, ignored=ignored, rule=rule)
