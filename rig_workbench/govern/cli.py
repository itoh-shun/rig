"""govern.cli — `rig-wb govern …`, the operator surface of the governance layer.

Verb groups map one-to-one onto the concepts: `policy`, `whoami`/`can`,
`approve`, `waiver`, `audit`, `conformance`, `rollup`. Read-only commands print
and exit 0; a failed conformance run exits 3 so CI can gate on it without
parsing output; a refusal exits 1.

This module is `govern`'s **shell** (`tests/test_layering_contract.py`'s `SHELL_MODULES`
names it and says why), so it is allowed to wire and to present. Stage 3 of
`docs/v3-architecture-design-brief.ja.md` §3 asks two things of it, and both are visible
in the shapes below.

**Words leave through the `Presenter` port.** No command calls `print`. Each takes an
`out: Presenter` and the adapter is built once, at the process boundary in `main()`, then
handed down — a module-level instance reached for from inside each command would be the
same global under a different name, and the point of the port is that a caller (a test, an
embedding harness, the day rig grows a `--quiet`) can hand in a different one. Which
stream a line goes to is unchanged and stays a property of the call: `out.out` is stdout,
`out.err` is stderr, and `[WARN]` keeps going to stdout because that is where it went
(the `Presenter` docstring explains why the port has no `warn()`).

**Judging and reporting the judgement are separate.** A command returns a `Verdict` —
what it decided — and `cmd_govern` turns that into an exit status through `_STATUS`,
which is the one place in this file that knows a number. The two used to be the same
statement, so the mapping was restated at fifteen `return` sites and could only be read by
reading all of them.
"""

from __future__ import annotations

import argparse
import datetime
import enum
import json
import pathlib
import subprocess
import sys

from rig_workbench import gitroot
from rig_workbench.ports import Presenter
from rig_workbench.ports.local import ConsolePresenter

from . import conformance as conf
from . import ledger, waiver
from .approval import evaluate, load_approvals, record_decision
from .identity import ORG_SCHEMA, current_actor, load_org_binding, org_binding_path
from .policy import (EFFECTIVE_SCHEMA, PERMISSIONS, SCHEMA, EffectivePolicy,
                     PolicyError, describe_layers, effective_policy,
                     load_policy_document, resolve_layer_paths)
from .rbac import can, explain, roles_of

EXIT_OK, EXIT_ERROR, EXIT_NONCONFORMANT = 0, 1, 3


class Verdict(enum.Enum):
    """What a govern command decided, before anyone turns it into a number.

    Three answers, and they are the three `govern` has always had: it ran and the answer
    is yes, it could not produce an answer at all, or it judged and the answer is no.
    Separating them from the exit codes is what lets a command say what it found without
    also deciding how a shell hears it — the mapping lives once, in `_STATUS`.
    """

    OK = "ok"
    ERROR = "error"
    NONCONFORMANT = "nonconformant"


#: Verdict → exit status. The whole of govern's contract with a caller that cannot read
#: prose, in one table.
#:
#: **These numbers are not `rig_workbench.exitcodes`', and the difference is deliberate.**
#: There, `1` is `REJECTED` — "rig judged and the answer is no" — and `2` is `ERROR`.
#: Here `1` is the error and `3` is the judgement. `govern can` has returned 0 for allowed
#: and 3 for denied since it existed, `tests/test_exit_code_surface.py` freezes both through
#: a real process, and CI steps are written against them, so the divergence is a
#: caller-visible contract rather than a slip. It is recorded as a known divergence and left
#: alone here; the restructuring only makes it legible, in one table instead of fifteen
#: `return` statements. Reconciling it is its own decision, with its own deprecation.
_STATUS: dict[Verdict, int] = {
    Verdict.OK: EXIT_OK,
    Verdict.ERROR: EXIT_ERROR,
    Verdict.NONCONFORMANT: EXIT_NONCONFORMANT,
}


def status_for(verdict: Verdict) -> int:
    """The exit status a verdict is reported as."""
    return _STATUS[verdict]


def _err(out: Presenter, msg: str) -> Verdict:
    out.err(f"[ERROR] {msg}")
    return Verdict.ERROR


def _repo_root() -> pathlib.Path:
    """Where governance state lives: the repository, not the working tree in front of you.

    The org binding, the effective policy, recorded approvals and the audit ledger are all
    under `.rig/`, which is gitignored — one set per repository, not one per branch. Asked
    with `--show-toplevel` this returned the caller's worktree, so `govern` run from a task
    worktree read an empty policy and appended to a ledger nobody else would ever read. It
    took #471 to make that reachable: before it, nothing ran from a worktree.

    Delegated rather than duplicated. This function had its own copy of the porcelain query
    and therefore its own copy of every property that query needs to have — including the
    removal of `GIT_DIR` and `GIT_WORK_TREE`, which it did not have, so an inherited routing
    variable sent governance's ledger and approvals to another repository entirely. One
    definition was the point of `gitroot`, and a second one that merely looks the same is
    how the first one stops being true.
    """
    return gitroot.main_worktree() or pathlib.Path.cwd()


def _head(root: pathlib.Path, task: dict) -> str | None:
    """The task branch tip, used to bind approvals to the code they approved."""
    wt = task.get("worktree_path")
    cwd = wt if wt and pathlib.Path(wt).is_dir() else str(root)
    proc = subprocess.run(["git", "rev-parse", "HEAD"], cwd=cwd, capture_output=True, text=True)
    return proc.stdout.strip() or None if proc.returncode == 0 else None


def _load_task(root: pathlib.Path, task_id: str | None) -> tuple[str, dict] | None:
    base = root / ".rig" / "runs"
    if not task_id:
        if not base.is_dir():
            return None
        ids = sorted(p.name for p in base.iterdir() if (p / "task.json").is_file())
        if not ids:
            return None
        task_id = ids[-1]
    p = base / task_id / "task.json"
    if not p.is_file():
        return None
    try:
        return task_id, json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def _effective(root: pathlib.Path, out: Presenter) -> EffectivePolicy | Verdict:
    try:
        return effective_policy(root)
    except PolicyError as e:
        return _err(out, str(e))


# ── init / migrate ───────────────────────────────────────────────────────────
_STARTER_ROLES = {
    "developer": ["task.new", "gate.set", "accept", "discard"],
    "reviewer": ["task.new", "gate.set", "accept", "approve", "discard"],
    "quality-owner": ["task.new", "gate.set", "accept", "accept.force", "approve",
                      "waiver.grant", "waiver.revoke", "audit.export", "discard"],
    "policy-admin": ["policy.publish", "audit.export", "pack.install"],
}


def cmd_init(args: argparse.Namespace, out: Presenter) -> Verdict:
    root = _repo_root()
    binding_path = org_binding_path(root)
    if binding_path.is_file() and not args.force:
        return _err(out, f"{binding_path} already exists (pass --force to overwrite)")

    layers = list(args.layer or [])
    policy_written: pathlib.Path | None = None
    if not layers:
        policy_written = root / ".rig" / "policy" / "org.json"
        if policy_written.is_file() and not args.force:
            return _err(out, f"{policy_written} already exists (pass --force to overwrite)")
        starter = {
            "schema": SCHEMA,
            "id": args.org,
            "scope": "org",
            "org": args.org,
            "version": "1.0.0",
            "description": f"{args.org} common quality policy — the floor every team builds on.",
            "roles": _STARTER_ROLES,
            "members": {current_actor(root): ["quality-owner"], "*": ["developer"]},
            "sealed_roles": ["quality-owner", "policy-admin"],
            "delegatable_permissions": ["task.new", "gate.set", "accept", "discard", "approve"],
            "approvals": {"default": {"quorum": 0},
                          "feature": {"quorum": 1, "roles": ["reviewer", "quality-owner"],
                                      "separation_of_duties": True, "expires_hours": 168}},
            "waivers": {"max_days": 14, "grant_roles": ["quality-owner"],
                        "non_waivable": ["no_secret_leak", "no_gate_tampering",
                                         "no_destructive_operation"],
                        "required_for_force": True},
            "audit": {"chain_required": True},
        }
        policy_written.parent.mkdir(parents=True, exist_ok=True)
        policy_written.write_text(json.dumps(starter, ensure_ascii=False, indent=2) + "\n",
                                  encoding="utf-8")
        layers = [".rig/policy/org.json"]

    binding = {"schema": ORG_SCHEMA, "org": args.org, "policy_layers": layers}
    if args.team:
        binding["team"] = args.team
    binding_path.parent.mkdir(parents=True, exist_ok=True)
    binding_path.write_text(json.dumps(binding, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    out.out(f"## rig govern init: {args.org}" + (f"/{args.team}" if args.team else ""))
    out.out(f"  wrote {binding_path.relative_to(root)}")
    if policy_written:
        out.out(f"  wrote {policy_written.relative_to(root)} (starter org policy — edit it, it is the floor)")
    out.out("\nNext:")
    out.out("  rig-wb govern policy show      # what is in effect here")
    out.out("  rig-wb govern whoami           # your roles and permissions")
    out.out("  rig-wb govern conformance      # does this repo clear the policy")
    ledger.append(root, "policy.init", actor=current_actor(root), subject=args.org,
                  org=args.org, team=args.team, data={"layers": layers})
    return Verdict.OK


def cmd_migrate(args: argparse.Namespace, out: Presenter) -> Verdict:
    """Fold v1's `.rig/access.json` and `.rig/gates.json` into a policy layer.

    The two files keep working either way; this exists so a team that already
    tuned them does not start the org policy from a blank page.
    """
    root = _repo_root()
    access_p = root / ".rig" / "access.json"
    gates_p = root / ".rig" / "gates.json"
    if not access_p.is_file() and not gates_p.is_file():
        return _err(out, "nothing to migrate (neither .rig/access.json nor .rig/gates.json exists)")

    roles: dict[str, list[str]] = {}
    members: dict[str, list[str]] = {}
    require: dict[str, list[str]] = {}
    descriptions: dict[str, str] = {}

    if access_p.is_file():
        try:
            access = json.loads(access_p.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            return _err(out, f"{access_p}: not valid JSON: {e}")
        if isinstance(access, dict):
            roles["accepter"] = ["task.new", "gate.set", "accept", "discard"]
            roles["developer"] = ["task.new", "gate.set", "discard"]
            for group, names in access.items():
                if not isinstance(names, list):
                    continue
                for name in names:
                    if isinstance(name, str):
                        members.setdefault(name, [])
                        if "accepter" not in members[name]:
                            members[name].append("accepter")
                del group

    if gates_p.is_file():
        try:
            gates = json.loads(gates_p.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            return _err(out, f"{gates_p}: not valid JSON: {e}")
        if isinstance(gates, dict):
            for target, crits in (gates.get("extra_criteria") or {}).items():
                if isinstance(crits, list):
                    require[target] = [c for c in crits if isinstance(c, str)]
            for key, value in (gates.get("descriptions") or {}).items():
                if isinstance(value, str):
                    descriptions[key] = value

    org = args.org or load_org_binding(root).org
    if not org:
        return _err(out, "no org known — pass --org, or run `rig-wb govern init` first")
    doc = {"schema": SCHEMA, "id": args.id, "scope": args.scope, "org": org,
           "version": "1.0.0",
           "description": "migrated from .rig/access.json / .rig/gates.json"}
    if args.scope == "team":
        if not args.team:
            return _err(out, "--scope team requires --team")
        doc["team"] = args.team
    if require:
        doc["require_criteria"] = require
    if descriptions:
        doc["descriptions"] = descriptions
    if roles:
        doc["roles"] = roles
        doc["members"] = members

    # `out_path`, not `out`: `out` is the presenter now, and the file this writes is a
    # different thing that happened to share the name.
    out_path = pathlib.Path(args.out) if args.out else root / ".rig" / "policy" / f"{args.id}.json"
    if out_path.is_file() and not args.force:
        return _err(out, f"{out_path} already exists (pass --force to overwrite)")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    out.out(f"## rig govern migrate\n  wrote {out_path}")
    out.out(f"  {len(require)} criteria target(s), {len(members)} member(s) carried over")
    out.out("\nReview it, then add it to policy_layers in .rig/org.json (or leave it in .rig/policy/).")
    out.out("The original files keep working until you delete them.")
    return Verdict.OK


# ── policy ───────────────────────────────────────────────────────────────────
def cmd_policy(args: argparse.Namespace, out: Presenter) -> Verdict:
    root = _repo_root()
    if args.action == "lint":
        paths = [pathlib.Path(p) for p in args.paths] if args.paths else resolve_layer_paths(
            root, load_org_binding(root).raw)
        if not paths:
            out.out("## rig govern policy lint\n\nNo policy layer found (nothing to lint).")
            return Verdict.OK
        failures = 0
        for p in paths:
            try:
                doc = load_policy_document(p)
                out.out(f"  ✓ {p}  [{doc['scope']}:{doc['id']}]")
            except PolicyError as e:
                out.out(f"  ✗ {e}")
                failures += 1
        if failures:
            return Verdict.NONCONFORMANT
        # Folding is where cross-layer tightening violations surface.
        try:
            effective_policy(root)
        except PolicyError as e:
            out.out(f"  ✗ {e}")
            return Verdict.NONCONFORMANT
        out.out(f"\n{len(paths)} layer(s) valid, and they stack without loosening anything.")
        return Verdict.OK

    eff = _effective(root, out)
    if isinstance(eff, Verdict):
        return eff
    if args.json:
        out.out(json.dumps(_policy_dict(eff), ensure_ascii=False, indent=2))
        return Verdict.OK
    if not eff.active:
        out.out("## rig govern policy\n\nNo policy in effect — this repository is ungoverned "
                "(rig behaves exactly as it does for solo use).\n"
                "Start one with `rig-wb govern init --org <org> --team <team>`.")
        return Verdict.OK

    out.out(f"## rig govern policy: {eff.org}{'/' + eff.team if eff.team else ''}\n")
    out.out("layers (applied in order; each may only tighten the one before it):")
    for line in describe_layers(eff):
        out.out(f"  {line}")
    if eff.require_criteria:
        out.out("\nrequired criteria (added to every gate that applies):")
        for target, crits in sorted(eff.require_criteria.items()):
            for crit in crits:
                desc = eff.descriptions.get(crit)
                out.out(f"  {target} + {crit}" + (f" — {desc}" if desc else ""))
    if eff.roles:
        out.out("\nroles:")
        for role, perms in sorted(eff.roles.items()):
            seal = " [sealed]" if role in eff.sealed_roles else ""
            out.out(f"  {role}{seal}: {', '.join(perms) or '(none)'}")
        out.out("\nmembers:")
        for actor, assigned in sorted(eff.members.items()):
            out.out(f"  {actor}: {', '.join(assigned)}")
    if eff.approvals:
        out.out("\napprovals:")
        for target, rule in sorted(eff.approvals.items()):
            bits = [f"quorum {rule['quorum']}"]
            if rule.get("roles"):
                bits.append(f"roles {', '.join(rule['roles'])}")
            if rule.get("separation_of_duties"):
                bits.append("separation of duties")
            if rule.get("expires_hours"):
                bits.append(f"expires {rule['expires_hours']}h")
            out.out(f"  {target}: {' · '.join(bits)}")
    if eff.waivers:
        w = eff.waivers
        out.out("\nwaivers:")
        out.out(f"  max lifetime: {w.get('max_days') or 'unbounded'} day(s)"
                f"   required for --force: {'yes' if w.get('required_for_force') else 'no'}")
        if w.get("grant_roles"):
            out.out(f"  may be granted by: {', '.join(w['grant_roles'])}")
        if w.get("non_waivable"):
            out.out(f"  non-waivable: {', '.join(w['non_waivable'])}")
    out.out(f"\naudit: chained ledger {'required' if eff.audit_chain_required else 'optional'}")
    return Verdict.OK


def _policy_dict(eff: EffectivePolicy) -> dict:
    """The effective policy as `govern policy show --json` prints it.

    `schema` is first and is the only field a consumer is asked to branch on. It is
    `rig.effective-policy/v1`, not the `rig.policy/v2` of the layers this was folded
    from: the layers are on `layers[].path` for a reader that wants to follow them
    back, and calling the fold by the layer's name would invite a reader to validate
    it as one. Everything below `schema` is exactly what this printed before it had a
    name — the id was the only thing missing.
    """
    return {
        "schema": EFFECTIVE_SCHEMA,
        "active": eff.active,
        "org": eff.org,
        "team": eff.team,
        "layers": [{"scope": layer.scope, "id": layer.id, "version": layer.version,
                    "path": str(layer.path) if layer.path else None} for layer in eff.layers],
        "require_criteria": eff.require_criteria,
        "descriptions": eff.descriptions,
        "roles": eff.roles,
        "members": eff.members,
        "sealed_roles": sorted(eff.sealed_roles),
        "approvals": eff.approvals,
        "waivers": eff.waivers,
        "audit_chain_required": eff.audit_chain_required,
    }


# ── identity / permissions ───────────────────────────────────────────────────
def cmd_whoami(args: argparse.Namespace, out: Presenter) -> Verdict:
    root = _repo_root()
    eff = _effective(root, out)
    if isinstance(eff, Verdict):
        return eff
    actor = args.actor or current_actor(root)
    binding = load_org_binding(root)
    if binding.error:
        out.out(f"[WARN] {binding.error}")
    for line in explain(eff, actor):
        out.out(line)
    return Verdict.OK


def cmd_can(args: argparse.Namespace, out: Presenter) -> Verdict:
    root = _repo_root()
    eff = _effective(root, out)
    if isinstance(eff, Verdict):
        return eff
    actor = args.actor or current_actor(root)
    try:
        decision = can(eff, actor, args.permission)
    except ValueError as e:
        return _err(out, str(e))
    out.out(f"{'✓ allowed' if decision.allowed else '✗ denied'}: {actor} → {args.permission}")
    out.out(f"  {decision.reason}")
    return Verdict.OK if decision.allowed else Verdict.NONCONFORMANT


# ── approvals ────────────────────────────────────────────────────────────────
def cmd_approve(args: argparse.Namespace, out: Presenter) -> Verdict:
    root = _repo_root()
    eff = _effective(root, out)
    if isinstance(eff, Verdict):
        return eff
    loaded = _load_task(root, getattr(args, "task_id", None))
    if not loaded:
        return _err(out, "no such task (looked in .rig/runs/). Run `rig-wb wb log` to list tasks")
    task_id, task = loaded

    if args.action in ("grant", "deny"):
        actor = args.actor or current_actor(root)
        if eff.active:
            decision = can(eff, actor, "approve")
            if not decision.allowed:
                return _err(out, f"not permitted to approve: {decision.reason}")
        if eff.active and (task.get("actor") == actor):
            rule = eff.approval_rule(task.get("task_type") or "")
            if rule.get("separation_of_duties", True):
                out.out(f"[WARN] {actor} authored this task; separation of duties means this "
                        "decision will not count toward the quorum")
        record_decision(root, task_id, actor=actor,
                        decision="approve" if args.action == "grant" else "deny",
                        roles=roles_of(eff, actor), head=_head(root, task), note=args.note or "")
        ledger.append(root, f"approval.{args.action}", actor=actor, subject=task_id,
                      org=eff.org, team=eff.team,
                      data={"task_type": task.get("task_type"), "note": args.note or ""})

    status = evaluate(eff, task, load_approvals(root, task_id), head=_head(root, task))
    out.out(f"## rig govern approve: {task_id} ({task.get('task_type')})\n")
    if not eff.active:
        out.out("(no policy in effect — decisions are recorded but nothing is required)")
    for line in status.lines():
        out.out(line)
    return Verdict.OK if status.satisfied or not status.required else Verdict.NONCONFORMANT


# ── waivers ──────────────────────────────────────────────────────────────────
def cmd_waiver(args: argparse.Namespace, out: Presenter) -> Verdict:
    root = _repo_root()
    eff = _effective(root, out)
    if isinstance(eff, Verdict):
        return eff
    actor = args.actor or current_actor(root)

    if args.action == "list":
        waivers = waiver.load_waivers(root)
        if not waivers:
            out.out("## rig govern waiver\n\nNo waivers on record.")
            return Verdict.OK
        out.out(f"## rig govern waiver ({len(waivers)} on record)\n")
        for w in waivers:
            state = ("revoked" if w.get("revoked")
                     else "live" if waiver.is_active(w) else "lapsed")
            out.out(f"  [{state}] {w.get('id')}  {', '.join(w.get('criteria') or [])}")
            out.out(f"      scope {w.get('scope')}  until {w.get('expires')}  by {w.get('granted_by')}")
            out.out(f"      reason: {w.get('reason')}")
        return Verdict.OK

    if args.action == "revoke":
        if eff.active:
            decision = can(eff, actor, "waiver.revoke")
            if not decision.allowed:
                return _err(out, f"not permitted to revoke waivers: {decision.reason}")
        try:
            record = waiver.revoke(root, args.id, actor=actor, reason=args.reason or "")
        except waiver.WaiverError as e:
            return _err(out, str(e))
        ledger.append(root, "waiver.revoke", actor=actor, subject=args.id,
                      org=eff.org, team=eff.team, data={"reason": args.reason or ""})
        out.out(f"revoked waiver {record['id']}")
        return Verdict.OK

    # grant
    if eff.active:
        decision = can(eff, actor, "waiver.grant")
        if not decision.allowed:
            return _err(out, f"not permitted to grant waivers: {decision.reason}")
        allowed_roles = set((eff.waivers or {}).get("grant_roles") or [])
        if allowed_roles and not (set(roles_of(eff, actor)) & allowed_roles):
            return _err(out, f"the policy restricts granting waivers to {', '.join(sorted(allowed_roles))}; "
                             f"{actor} holds {', '.join(roles_of(eff, actor)) or 'no role'}")
    if not args.criteria:
        return _err(out, "--criterion is required (a waiver has to name what it excuses)")
    expires = args.expires
    if not expires:
        days = (eff.waivers or {}).get("max_days") or 7
        expires = (datetime.date.today() + datetime.timedelta(days=float(days))).isoformat()
    try:
        record = waiver.grant(root, eff, waiver_id=args.id, actor=actor, criteria=args.criteria,
                              reason=args.reason or "", expires=expires, scope=args.scope)
    except waiver.WaiverError as e:
        return _err(out, str(e))
    ledger.append(root, "waiver.grant", actor=actor, subject=record["id"], org=eff.org, team=eff.team,
                  data={"criteria": record["criteria"], "expires": record["expires"],
                        "scope": record["scope"], "reason": record["reason"]})
    out.out(f"granted waiver {record['id']}: {', '.join(record['criteria'])} "
            f"(scope {record['scope']}) until {record['expires']}")
    return Verdict.OK


# ── audit ────────────────────────────────────────────────────────────────────
def cmd_audit(args: argparse.Namespace, out: Presenter) -> Verdict:
    root = _repo_root()
    if args.action == "verify":
        result = ledger.verify(root)
        out.out(f"## rig govern audit verify\n\n{result.summary()}")
        for problem in result.problems:
            out.out(f"  ✗ {problem}")
        return Verdict.OK if result.ok else Verdict.NONCONFORMANT

    if args.action == "export":
        eff = _effective(root, out)
        if isinstance(eff, Verdict):
            return eff
        actor = current_actor(root)
        if eff.active:
            decision = can(eff, actor, "audit.export")
            if not decision.allowed:
                return _err(out, f"not permitted to export the audit trail: {decision.reason}")
        try:
            text = ledger.export(root, fmt=args.format, since=args.since, action=args.filter_action)
        except ValueError as e:
            return _err(out, str(e))
        if args.out:
            pathlib.Path(args.out).write_text(text + "\n", encoding="utf-8")
            out.out(f"wrote {args.out}")
        else:
            out.out(text)
        ledger.append(root, "audit.export", actor=actor, subject=args.format,
                      org=eff.org, team=eff.team, data={"out": args.out or "(stdout)"})
        return Verdict.OK

    entries = [e for e in ledger.read_ledger(root) if "_malformed" not in e]
    if args.filter_action:
        entries = [e for e in entries if e.get("action") == args.filter_action]
    if args.since:
        entries = [e for e in entries if (e.get("ts") or "")[:10] >= args.since]
    if not entries:
        out.out("## rig govern audit\n\nNo ledger entries.")
        return Verdict.OK
    shown = entries[-args.limit:] if args.limit else entries
    out.out(f"## rig govern audit (latest {len(shown)} / {len(entries)})\n")
    for e in shown:
        out.out(f"  #{e.get('seq'):<4} {e.get('ts')}  {e.get('action'):<16} "
                f"{e.get('actor')}  {e.get('subject')}")
        data = e.get("data") or {}
        if data:
            out.out(f"        {json.dumps(data, ensure_ascii=False, sort_keys=True)}")
    return Verdict.OK


# ── conformance / rollup ─────────────────────────────────────────────────────
def cmd_conformance(args: argparse.Namespace, out: Presenter) -> Verdict:
    root = pathlib.Path(args.path).resolve() if args.path else _repo_root()
    report = conf.evaluate_project(root, since_days=args.since_days)
    if args.json:
        out.out(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
        return Verdict.OK if report.verdict != conf.FAIL else Verdict.NONCONFORMANT

    header = f"{report.org}/{report.team}" if report.team else (report.org or "(unbound)")
    out.out(f"## rig govern conformance: {report.project} [{header}]\n")
    if report.error:
        out.out(f"  ✗ {report.error}")
        return Verdict.NONCONFORMANT
    # The shortfall renders on the same line as the score, not under it: this is the number
    # that gets quoted upward, and a rate computed from fewer records than the runs directory
    # holds has to say so where it is read.
    out.out(f"verdict: {conf.ICON[report.verdict]} {report.verdict}   "
            f"score: {report.score:.0%} ({report.passed}/{len(report.applicable)} applicable "
            f"checks){report.unreadable_note}")
    if report.policy_layers:
        out.out(f"policy: {', '.join(report.policy_layers)}")
    out.out()
    for check in report.checks:
        out.out(f"  {conf.ICON[check.verdict]} {check.id}: {check.detail}")
        for line in check.evidence:
            out.out(f"      {line}")
    return Verdict.OK if report.verdict != conf.FAIL else Verdict.NONCONFORMANT


def cmd_rollup(args: argparse.Namespace, out: Presenter) -> Verdict:
    roots: list[pathlib.Path] = []
    for entry in args.paths:
        p = pathlib.Path(entry).resolve()
        if args.scan and p.is_dir():
            for child in sorted(p.iterdir()):
                if (child / ".rig" / "org.json").is_file():
                    roots.append(child)
            continue
        roots.append(p)
    if not roots:
        return _err(out, "no projects to roll up (pass repository paths, or --scan a directory of them)")
    result = conf.rollup(roots, since_days=args.since_days)
    if args.json:
        out.out(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    else:
        out.out(result.markdown())
    worst = min((conf._RANK[r.verdict] for r in result.reports), default=3)
    return Verdict.NONCONFORMANT if worst == conf._RANK[conf.FAIL] else Verdict.OK


# ── parser ───────────────────────────────────────────────────────────────────
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="rig-wb govern",
        description="rig govern — org/team policy, permissions, approvals, waivers, audit")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("init", help="bind this repository to an org/team and scaffold a starter policy")
    p.add_argument("--org", required=True, help="org identifier (e.g. acme)")
    p.add_argument("--team", help="team identifier (e.g. team-a)")
    p.add_argument("--layer", action="append",
                   help="path to an existing policy layer, repeatable and applied in order "
                        "(relative paths also resolve against $RIG_POLICY_HOME)")
    p.add_argument("--force", action="store_true", help="overwrite existing files")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("migrate", help="fold v1 .rig/access.json / .rig/gates.json into a policy layer")
    p.add_argument("--org", help="org identifier (defaults to the one in .rig/org.json)")
    p.add_argument("--scope", choices=("org", "team", "project"), default="project")
    p.add_argument("--team", help="team identifier (required with --scope team)")
    p.add_argument("--id", default="migrated", help="policy document id (default: migrated)")
    p.add_argument("--out", help="write here instead of .rig/policy/<id>.json")
    p.add_argument("--force", action="store_true", help="overwrite an existing file")
    p.set_defaults(func=cmd_migrate)

    p = sub.add_parser("policy", help="show or lint the policy in effect")
    p.add_argument("action", nargs="?", choices=("show", "lint"), default="show")
    p.add_argument("paths", nargs="*", help="with lint: specific documents (default: the resolved layers)")
    p.add_argument("--json", action="store_true", help="with show: machine-readable output")
    p.set_defaults(func=cmd_policy)

    p = sub.add_parser("whoami", help="the roles and permissions of the current actor")
    p.add_argument("--actor", help="ask about somebody else")
    p.set_defaults(func=cmd_whoami)

    p = sub.add_parser("can", help="check a single permission (exit 0 allowed / 3 denied)")
    p.add_argument("permission", help=f"one of: {', '.join(PERMISSIONS)}")
    p.add_argument("--actor", help="ask about somebody else")
    p.set_defaults(func=cmd_can)

    p = sub.add_parser("approve", help="grant/deny an approval, or show a task's approval status")
    p.add_argument("action", nargs="?", choices=("status", "grant", "deny"), default="status")
    p.add_argument("task_id", nargs="?", help="defaults to the most recent task")
    p.add_argument("--note", help="why (recorded with the decision; required in practice for deny)")
    p.add_argument("--actor", help="record the decision under this identity")
    p.set_defaults(func=cmd_approve)

    p = sub.add_parser("waiver", help="grant, list or revoke time-boxed exceptions")
    p.add_argument("action", nargs="?", choices=("list", "grant", "revoke"), default="list")
    p.add_argument("id", nargs="?", help="waiver id (with grant/revoke)")
    p.add_argument("--criterion", dest="criteria", action="append",
                   help="gate criterion this waiver excuses (repeatable)")
    p.add_argument("--reason", help="why this exception exists")
    p.add_argument("--expires", help="YYYY-MM-DD (defaults to the policy's maximum)")
    p.add_argument("--scope", default="*",
                   help="fnmatch pattern over task_type or task_id (default: *)")
    p.add_argument("--actor", help="act as this identity")
    p.set_defaults(func=cmd_waiver)

    p = sub.add_parser("audit", help="read, verify or export the tamper-evident ledger")
    p.add_argument("action", nargs="?", choices=("log", "verify", "export"), default="log")
    p.add_argument("--limit", type=int, help="with log: show only the latest N entries")
    p.add_argument("--action", dest="filter_action", help="filter by action name")
    p.add_argument("--since", help="only entries since YYYY-MM-DD")
    p.add_argument("--format", choices=("jsonl", "csv", "markdown"), default="jsonl",
                   help="with export: output format")
    p.add_argument("--out", help="with export: write to this file")
    p.set_defaults(func=cmd_audit)

    p = sub.add_parser("conformance", help="measure this repository against its effective policy")
    p.add_argument("path", nargs="?", help="repository to measure (default: the current one)")
    p.add_argument("--since-days", type=int, default=90, help="run window for the measured checks")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    p.set_defaults(func=cmd_conformance)

    p = sub.add_parser("rollup", help="aggregate several projects into the org/team view")
    p.add_argument("paths", nargs="+", help="repository paths (or directories with --scan)")
    p.add_argument("--scan", action="store_true",
                   help="treat each path as a directory whose immediate children are repositories")
    p.add_argument("--since-days", type=int, default=90)
    p.add_argument("--json", action="store_true", help="machine-readable output")
    p.set_defaults(func=cmd_rollup)

    return parser


def cmd_govern(argv: list[str], *, out: Presenter | None = None) -> int:
    """Parse, run the command, and report its verdict as a status.

    The presenter is a parameter with a default rather than a module-level instance the
    commands reach for: `main()` builds the console adapter at the process boundary and
    passes it in, and an in-process caller (`rig_workbench/cli.py` dispatches here, and a
    test can too) may hand in its own. The default exists so those callers keep working
    unchanged — it constructs an adapter, it does not share one.

    This is also the only place a verdict becomes a number. Everything above returns a
    `Verdict`; `status_for` is the single statement that reads `_STATUS`.
    """
    args = build_parser().parse_args(argv)
    return status_for(args.func(args, ConsolePresenter() if out is None else out))


def main() -> None:
    sys.exit(cmd_govern(sys.argv[1:], out=ConsolePresenter()))


if __name__ == "__main__":
    main()
