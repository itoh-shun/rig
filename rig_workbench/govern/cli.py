"""govern.cli — `rig-wb govern …`, the operator surface of the governance layer.

Verb groups map one-to-one onto the concepts: `policy`, `whoami`/`can`,
`approve`, `waiver`, `audit`, `conformance`, `rollup`. Read-only commands print
and exit 0; a failed conformance run exits 3 so CI can gate on it without
parsing output; a refusal exits 1.
"""

from __future__ import annotations

import argparse
import datetime
import json
import pathlib
import subprocess
import sys

from . import conformance as conf
from . import ledger, waiver
from .approval import evaluate, load_approvals, record_decision
from .identity import ORG_SCHEMA, current_actor, load_org_binding, org_binding_path
from .policy import (PERMISSIONS, SCHEMA, EffectivePolicy, PolicyError,
                     describe_layers, effective_policy, load_policy_document,
                     resolve_layer_paths)
from .rbac import can, explain, roles_of

EXIT_OK, EXIT_ERROR, EXIT_NONCONFORMANT = 0, 1, 3


def _err(msg: str) -> int:
    print(f"[ERROR] {msg}", file=sys.stderr)
    return EXIT_ERROR


def _repo_root() -> pathlib.Path:
    proc = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True)
    if proc.returncode == 0 and proc.stdout.strip():
        return pathlib.Path(proc.stdout.strip())
    return pathlib.Path.cwd()


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


def _effective(root: pathlib.Path) -> EffectivePolicy | int:
    try:
        return effective_policy(root)
    except PolicyError as e:
        return _err(str(e))


# ── init / migrate ───────────────────────────────────────────────────────────
_STARTER_ROLES = {
    "developer": ["task.new", "gate.set", "accept", "discard"],
    "reviewer": ["task.new", "gate.set", "accept", "approve", "discard"],
    "quality-owner": ["task.new", "gate.set", "accept", "accept.force", "approve",
                      "waiver.grant", "waiver.revoke", "audit.export", "discard"],
    "policy-admin": ["policy.publish", "audit.export", "pack.install"],
}


def cmd_init(args: argparse.Namespace) -> int:
    root = _repo_root()
    binding_path = org_binding_path(root)
    if binding_path.is_file() and not args.force:
        return _err(f"{binding_path} already exists (pass --force to overwrite)")

    layers = list(args.layer or [])
    policy_written: pathlib.Path | None = None
    if not layers:
        policy_written = root / ".rig" / "policy" / "org.json"
        if policy_written.is_file() and not args.force:
            return _err(f"{policy_written} already exists (pass --force to overwrite)")
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

    print(f"## rig govern init: {args.org}" + (f"/{args.team}" if args.team else ""))
    print(f"  wrote {binding_path.relative_to(root)}")
    if policy_written:
        print(f"  wrote {policy_written.relative_to(root)} (starter org policy — edit it, it is the floor)")
    print("\nNext:")
    print("  rig-wb govern policy show      # what is in effect here")
    print("  rig-wb govern whoami           # your roles and permissions")
    print("  rig-wb govern conformance      # does this repo clear the policy")
    ledger.append(root, "policy.init", actor=current_actor(root), subject=args.org,
                  org=args.org, team=args.team, data={"layers": layers})
    return EXIT_OK


def cmd_migrate(args: argparse.Namespace) -> int:
    """Fold v1's `.rig/access.json` and `.rig/gates.json` into a policy layer.

    The two files keep working either way; this exists so a team that already
    tuned them does not start the org policy from a blank page.
    """
    root = _repo_root()
    access_p = root / ".rig" / "access.json"
    gates_p = root / ".rig" / "gates.json"
    if not access_p.is_file() and not gates_p.is_file():
        return _err("nothing to migrate (neither .rig/access.json nor .rig/gates.json exists)")

    roles: dict[str, list[str]] = {}
    members: dict[str, list[str]] = {}
    require: dict[str, list[str]] = {}
    descriptions: dict[str, str] = {}

    if access_p.is_file():
        try:
            access = json.loads(access_p.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            return _err(f"{access_p}: not valid JSON: {e}")
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
            return _err(f"{gates_p}: not valid JSON: {e}")
        if isinstance(gates, dict):
            for target, crits in (gates.get("extra_criteria") or {}).items():
                if isinstance(crits, list):
                    require[target] = [c for c in crits if isinstance(c, str)]
            for key, value in (gates.get("descriptions") or {}).items():
                if isinstance(value, str):
                    descriptions[key] = value

    org = args.org or load_org_binding(root).org
    if not org:
        return _err("no org known — pass --org, or run `rig-wb govern init` first")
    doc = {"schema": SCHEMA, "id": args.id, "scope": args.scope, "org": org,
           "version": "1.0.0",
           "description": "migrated from .rig/access.json / .rig/gates.json"}
    if args.scope == "team":
        if not args.team:
            return _err("--scope team requires --team")
        doc["team"] = args.team
    if require:
        doc["require_criteria"] = require
    if descriptions:
        doc["descriptions"] = descriptions
    if roles:
        doc["roles"] = roles
        doc["members"] = members

    out = pathlib.Path(args.out) if args.out else root / ".rig" / "policy" / f"{args.id}.json"
    if out.is_file() and not args.force:
        return _err(f"{out} already exists (pass --force to overwrite)")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"## rig govern migrate\n  wrote {out}")
    print(f"  {len(require)} criteria target(s), {len(members)} member(s) carried over")
    print("\nReview it, then add it to policy_layers in .rig/org.json (or leave it in .rig/policy/).")
    print("The original files keep working until you delete them.")
    return EXIT_OK


# ── policy ───────────────────────────────────────────────────────────────────
def cmd_policy(args: argparse.Namespace) -> int:
    root = _repo_root()
    if args.action == "lint":
        paths = [pathlib.Path(p) for p in args.paths] if args.paths else resolve_layer_paths(
            root, load_org_binding(root).raw)
        if not paths:
            print("## rig govern policy lint\n\nNo policy layer found (nothing to lint).")
            return EXIT_OK
        failures = 0
        for p in paths:
            try:
                doc = load_policy_document(p)
                print(f"  ✓ {p}  [{doc['scope']}:{doc['id']}]")
            except PolicyError as e:
                print(f"  ✗ {e}")
                failures += 1
        if failures:
            return EXIT_NONCONFORMANT
        # Folding is where cross-layer tightening violations surface.
        try:
            effective_policy(root)
        except PolicyError as e:
            print(f"  ✗ {e}")
            return EXIT_NONCONFORMANT
        print(f"\n{len(paths)} layer(s) valid, and they stack without loosening anything.")
        return EXIT_OK

    eff = _effective(root)
    if isinstance(eff, int):
        return eff
    if args.json:
        print(json.dumps(_policy_dict(eff), ensure_ascii=False, indent=2))
        return EXIT_OK
    if not eff.active:
        print("## rig govern policy\n\nNo policy in effect — this repository is ungoverned "
              "(rig behaves exactly as it does for solo use).\n"
              "Start one with `rig-wb govern init --org <org> --team <team>`.")
        return EXIT_OK

    print(f"## rig govern policy: {eff.org}{'/' + eff.team if eff.team else ''}\n")
    print("layers (applied in order; each may only tighten the one before it):")
    for line in describe_layers(eff):
        print(f"  {line}")
    if eff.require_criteria:
        print("\nrequired criteria (added to every gate that applies):")
        for target, crits in sorted(eff.require_criteria.items()):
            for crit in crits:
                desc = eff.descriptions.get(crit)
                print(f"  {target} + {crit}" + (f" — {desc}" if desc else ""))
    if eff.roles:
        print("\nroles:")
        for role, perms in sorted(eff.roles.items()):
            seal = " [sealed]" if role in eff.sealed_roles else ""
            print(f"  {role}{seal}: {', '.join(perms) or '(none)'}")
        print("\nmembers:")
        for actor, assigned in sorted(eff.members.items()):
            print(f"  {actor}: {', '.join(assigned)}")
    if eff.approvals:
        print("\napprovals:")
        for target, rule in sorted(eff.approvals.items()):
            bits = [f"quorum {rule['quorum']}"]
            if rule.get("roles"):
                bits.append(f"roles {', '.join(rule['roles'])}")
            if rule.get("separation_of_duties"):
                bits.append("separation of duties")
            if rule.get("expires_hours"):
                bits.append(f"expires {rule['expires_hours']}h")
            print(f"  {target}: {' · '.join(bits)}")
    if eff.waivers:
        w = eff.waivers
        print("\nwaivers:")
        print(f"  max lifetime: {w.get('max_days') or 'unbounded'} day(s)"
              f"   required for --force: {'yes' if w.get('required_for_force') else 'no'}")
        if w.get("grant_roles"):
            print(f"  may be granted by: {', '.join(w['grant_roles'])}")
        if w.get("non_waivable"):
            print(f"  non-waivable: {', '.join(w['non_waivable'])}")
    print(f"\naudit: chained ledger {'required' if eff.audit_chain_required else 'optional'}")
    return EXIT_OK


def _policy_dict(eff: EffectivePolicy) -> dict:
    return {
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
def cmd_whoami(args: argparse.Namespace) -> int:
    root = _repo_root()
    eff = _effective(root)
    if isinstance(eff, int):
        return eff
    actor = args.actor or current_actor(root)
    binding = load_org_binding(root)
    if binding.error:
        print(f"[WARN] {binding.error}")
    for line in explain(eff, actor):
        print(line)
    return EXIT_OK


def cmd_can(args: argparse.Namespace) -> int:
    root = _repo_root()
    eff = _effective(root)
    if isinstance(eff, int):
        return eff
    actor = args.actor or current_actor(root)
    try:
        decision = can(eff, actor, args.permission)
    except ValueError as e:
        return _err(str(e))
    print(f"{'✓ allowed' if decision.allowed else '✗ denied'}: {actor} → {args.permission}")
    print(f"  {decision.reason}")
    return EXIT_OK if decision.allowed else EXIT_NONCONFORMANT


# ── approvals ────────────────────────────────────────────────────────────────
def cmd_approve(args: argparse.Namespace) -> int:
    root = _repo_root()
    eff = _effective(root)
    if isinstance(eff, int):
        return eff
    loaded = _load_task(root, getattr(args, "task_id", None))
    if not loaded:
        return _err("no such task (looked in .rig/runs/). Run `rig-wb wb log` to list tasks")
    task_id, task = loaded

    if args.action in ("grant", "deny"):
        actor = args.actor or current_actor(root)
        if eff.active:
            decision = can(eff, actor, "approve")
            if not decision.allowed:
                return _err(f"not permitted to approve: {decision.reason}")
        if eff.active and (task.get("actor") == actor):
            rule = eff.approval_rule(task.get("task_type") or "")
            if rule.get("separation_of_duties", True):
                print(f"[WARN] {actor} authored this task; separation of duties means this "
                      "decision will not count toward the quorum")
        record_decision(root, task_id, actor=actor,
                        decision="approve" if args.action == "grant" else "deny",
                        roles=roles_of(eff, actor), head=_head(root, task), note=args.note or "")
        ledger.append(root, f"approval.{args.action}", actor=actor, subject=task_id,
                      org=eff.org, team=eff.team,
                      data={"task_type": task.get("task_type"), "note": args.note or ""})

    status = evaluate(eff, task, load_approvals(root, task_id), head=_head(root, task))
    print(f"## rig govern approve: {task_id} ({task.get('task_type')})\n")
    if not eff.active:
        print("(no policy in effect — decisions are recorded but nothing is required)")
    for line in status.lines():
        print(line)
    return EXIT_OK if status.satisfied or not status.required else EXIT_NONCONFORMANT


# ── waivers ──────────────────────────────────────────────────────────────────
def cmd_waiver(args: argparse.Namespace) -> int:
    root = _repo_root()
    eff = _effective(root)
    if isinstance(eff, int):
        return eff
    actor = args.actor or current_actor(root)

    if args.action == "list":
        waivers = waiver.load_waivers(root)
        if not waivers:
            print("## rig govern waiver\n\nNo waivers on record.")
            return EXIT_OK
        print(f"## rig govern waiver ({len(waivers)} on record)\n")
        for w in waivers:
            state = ("revoked" if w.get("revoked")
                     else "live" if waiver.is_active(w) else "lapsed")
            print(f"  [{state}] {w.get('id')}  {', '.join(w.get('criteria') or [])}")
            print(f"      scope {w.get('scope')}  until {w.get('expires')}  by {w.get('granted_by')}")
            print(f"      reason: {w.get('reason')}")
        return EXIT_OK

    if args.action == "revoke":
        if eff.active:
            decision = can(eff, actor, "waiver.revoke")
            if not decision.allowed:
                return _err(f"not permitted to revoke waivers: {decision.reason}")
        try:
            record = waiver.revoke(root, args.id, actor=actor, reason=args.reason or "")
        except waiver.WaiverError as e:
            return _err(str(e))
        ledger.append(root, "waiver.revoke", actor=actor, subject=args.id,
                      org=eff.org, team=eff.team, data={"reason": args.reason or ""})
        print(f"revoked waiver {record['id']}")
        return EXIT_OK

    # grant
    if eff.active:
        decision = can(eff, actor, "waiver.grant")
        if not decision.allowed:
            return _err(f"not permitted to grant waivers: {decision.reason}")
        allowed_roles = set((eff.waivers or {}).get("grant_roles") or [])
        if allowed_roles and not (set(roles_of(eff, actor)) & allowed_roles):
            return _err(f"the policy restricts granting waivers to {', '.join(sorted(allowed_roles))}; "
                        f"{actor} holds {', '.join(roles_of(eff, actor)) or 'no role'}")
    if not args.criteria:
        return _err("--criterion is required (a waiver has to name what it excuses)")
    expires = args.expires
    if not expires:
        days = (eff.waivers or {}).get("max_days") or 7
        expires = (datetime.date.today() + datetime.timedelta(days=float(days))).isoformat()
    try:
        record = waiver.grant(root, eff, waiver_id=args.id, actor=actor, criteria=args.criteria,
                              reason=args.reason or "", expires=expires, scope=args.scope)
    except waiver.WaiverError as e:
        return _err(str(e))
    ledger.append(root, "waiver.grant", actor=actor, subject=record["id"], org=eff.org, team=eff.team,
                  data={"criteria": record["criteria"], "expires": record["expires"],
                        "scope": record["scope"], "reason": record["reason"]})
    print(f"granted waiver {record['id']}: {', '.join(record['criteria'])} "
          f"(scope {record['scope']}) until {record['expires']}")
    return EXIT_OK


# ── audit ────────────────────────────────────────────────────────────────────
def cmd_audit(args: argparse.Namespace) -> int:
    root = _repo_root()
    if args.action == "verify":
        result = ledger.verify(root)
        print(f"## rig govern audit verify\n\n{result.summary()}")
        for problem in result.problems:
            print(f"  ✗ {problem}")
        return EXIT_OK if result.ok else EXIT_NONCONFORMANT

    if args.action == "export":
        eff = _effective(root)
        if isinstance(eff, int):
            return eff
        actor = current_actor(root)
        if eff.active:
            decision = can(eff, actor, "audit.export")
            if not decision.allowed:
                return _err(f"not permitted to export the audit trail: {decision.reason}")
        try:
            text = ledger.export(root, fmt=args.format, since=args.since, action=args.filter_action)
        except ValueError as e:
            return _err(str(e))
        if args.out:
            pathlib.Path(args.out).write_text(text + "\n", encoding="utf-8")
            print(f"wrote {args.out}")
        else:
            print(text)
        ledger.append(root, "audit.export", actor=actor, subject=args.format,
                      org=eff.org, team=eff.team, data={"out": args.out or "(stdout)"})
        return EXIT_OK

    entries = [e for e in ledger.read_ledger(root) if "_malformed" not in e]
    if args.filter_action:
        entries = [e for e in entries if e.get("action") == args.filter_action]
    if args.since:
        entries = [e for e in entries if (e.get("ts") or "")[:10] >= args.since]
    if not entries:
        print("## rig govern audit\n\nNo ledger entries.")
        return EXIT_OK
    shown = entries[-args.limit:] if args.limit else entries
    print(f"## rig govern audit (latest {len(shown)} / {len(entries)})\n")
    for e in shown:
        print(f"  #{e.get('seq'):<4} {e.get('ts')}  {e.get('action'):<16} "
              f"{e.get('actor')}  {e.get('subject')}")
        data = e.get("data") or {}
        if data:
            print(f"        {json.dumps(data, ensure_ascii=False, sort_keys=True)}")
    return EXIT_OK


# ── conformance / rollup ─────────────────────────────────────────────────────
def cmd_conformance(args: argparse.Namespace) -> int:
    root = pathlib.Path(args.path).resolve() if args.path else _repo_root()
    report = conf.evaluate_project(root, since_days=args.since_days)
    if args.json:
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
        return EXIT_OK if report.verdict != conf.FAIL else EXIT_NONCONFORMANT

    header = f"{report.org}/{report.team}" if report.team else (report.org or "(unbound)")
    print(f"## rig govern conformance: {report.project} [{header}]\n")
    if report.error:
        print(f"  ✗ {report.error}")
        return EXIT_NONCONFORMANT
    print(f"verdict: {conf.ICON[report.verdict]} {report.verdict}   "
          f"score: {report.score:.0%} ({report.passed}/{len(report.applicable)} applicable checks)")
    if report.policy_layers:
        print(f"policy: {', '.join(report.policy_layers)}")
    print()
    for check in report.checks:
        print(f"  {conf.ICON[check.verdict]} {check.id}: {check.detail}")
        for line in check.evidence:
            print(f"      {line}")
    return EXIT_OK if report.verdict != conf.FAIL else EXIT_NONCONFORMANT


def cmd_rollup(args: argparse.Namespace) -> int:
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
        return _err("no projects to roll up (pass repository paths, or --scan a directory of them)")
    result = conf.rollup(roots, since_days=args.since_days)
    if args.json:
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    else:
        print(result.markdown())
    worst = min((conf._RANK[r.verdict] for r in result.reports), default=3)
    return EXIT_NONCONFORMANT if worst == conf._RANK[conf.FAIL] else EXIT_OK


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


def cmd_govern(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


def main() -> None:
    sys.exit(cmd_govern(sys.argv[1:]))


if __name__ == "__main__":
    main()
