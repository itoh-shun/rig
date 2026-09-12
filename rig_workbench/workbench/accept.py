"""workbench accept/discard: diff rendering, accept_requirements + squash apply, discard, gc
(split from scripts/workbench.py)."""

import argparse
import datetime
import json
import pathlib
import re
import shutil
import sys

# `ast_diff` is stdlib-only and also runnable directly as
# `python3 scripts/ast_diff.py <base.py> <new.py>`; reuse it here rather than
# duplicating its logic (#280). It has to live inside the package to be importable:
# reaching a sibling `scripts/` dir through sys.path works in a checkout but
# resolves to a non-existent `site-packages/scripts` once installed.
from .. import ast_diff
from ..govern import enforce as govern_enforce
from .config import CHECK_ICON, RECOMMENDATION
from .state import (_diff_lines, audit_append, build_acceptance,
                    current_identity, die, drift_lines, effective_base,
                    gate_status, git, load_access_control,
                    load_json, load_task, now_iso, parse_diff_md, reject, repo_root,
                    resolve_task_id, runs_dir, save_json, save_task, sign_provenance,
                    task_lock, verify_provenance, warn, worktree_dirty)
from . import runtime as runtime_mod
from .telemetry import record_task_run


# The identities git refuses to guess. Two spellings of one condition: "empty ident name"
# comes from a config with a blank `user.name`, the "Please tell me who you are" block from
# no config at all (its `fatal:` line names whichever of email/name it could not auto-detect,
# so match the prose above it rather than that line). Used to tell a squash that could not
# run from a squash that ran and hit a conflict — see `_cmd_accept_locked`.
# How much of a failed squash is worth printing. Both are "enough to act on, not a wall":
# the conflict listing is a cap with the remainder counted, and git's own output is kept
# from both ends — the first lines say what it was doing, the last say why it stopped.
_CONFLICT_LIST_MAX = 20
_STDERR_HEAD_LINES, _STDERR_TAIL_LINES = 4, 12

_MISSING_IDENTITY_RE = re.compile(
    r"empty ident name|Please tell me who you are|Committer identity unknown"
    r"|unable to auto-detect email address|no email was given",
    re.I)


#: rig's own state root, as `git status --porcelain` spells a path inside it.
STATE_DIR = ".rig/"


def _porcelain_path(entry: str) -> str:
    """The path out of one `git status --porcelain` line, unquoted.

    `XY <path>` is the shape of every line, and git C-quotes a path holding anything awkward.
    A rename or copy is `R  <from> -> <to>`, and the destination is the one that exists now —
    but only there: a file genuinely named `a -> .rig/b` is an ordinary path, and splitting
    every line on that separator would read it as a rename into `.rig/` and exempt it. The
    status code is what tells the two apart, so it is what is asked.
    """
    path = entry[3:].strip()
    if entry[:2].strip().startswith(("R", "C")) and " -> " in path:
        path = path.split(" -> ", 1)[1]
    if len(path) > 1 and path.startswith('"') and path.endswith('"'):
        path = path[1:-1]
    return path


def _names_state(entry: str) -> bool:
    """Is this status line about something under `.rig/`?"""
    path = _porcelain_path(entry)
    return path == STATE_DIR.rstrip("/") or path.startswith(STATE_DIR)


def _dirty_root_advice(blocking: list[str]) -> str:
    """What to do about every path that is blocking, not about the most interesting one.

    Two kinds can be in the list at once, and they need opposite things. Ordinary dirt is the
    operator's own work: commit or stash it. `.rig/` that somebody committed is rig's run state
    living in the repository by mistake: it has to be *untracked*, and the removal committed —
    `git rm -r --cached` stages a deletion, which is still an uncommitted change, so advice that
    stopped at the `rm` sent the operator back to this same refusal. Naming only one of the two
    kinds is the same failure at a larger scale: the operator follows the instruction, accept
    refuses again, and the second message is the one they were never shown.

    The command is one recovery and not a list of things to do, because it was measured being
    followed. `git rm -r --cached` stages a deletion — an uncommitted change — so advice that
    stopped there came straight back here. Adding the ignore entry leaves `?? .gitignore`, so
    advice that stopped *there* came back a second time, on the other branch of this same
    message. Three refusals to reach one accept is not guidance, it is a maze; the untrack, the
    entry, the staging and the commit are one line now, and the next accept succeeds.

    That line also carries `-m` for the reason it is a line at all: this message is read by
    runners as well as by people, and a bare `git commit` opens `$EDITOR`, which on a CI runner
    is a process waiting for a keystroke nobody will send. Advice only a human at a terminal
    can follow is advice half the readers cannot.
    """
    state = [entry for entry in blocking if _names_state(entry)]
    ordinary = [entry for entry in blocking if entry not in state]
    if not state:
        return "Commit or stash first (check with git status)"
    untrack = (f"{len(state)} of them {'is' if len(state) == 1 else 'are'} under `{STATE_DIR}` "
               f"and tracked by git — that is rig's run state, not repository content. Untrack "
               f"it, ignore it and commit, in one go: git rm -r --cached {STATE_DIR} && "
               f"printf '{STATE_DIR}\\n' >> .gitignore && git add .gitignore && "
               f'git commit -m "untrack {STATE_DIR}"')
    if not ordinary:
        return f"{untrack} (check with git status)"
    return (f"{untrack}. Commit or stash the other "
            f"{len(ordinary)} (check with git status)")


def _is_untracked_state(entry: str) -> bool:
    """True for an *untracked* `git status` line under `.rig/` — which must not block accept.

    The check below exists for exactly one reason, stated where it is raised: the rollback
    after a failed squash is a hard reset, and that would wipe uncommitted work the operator
    had in the tree. A hard reset does not touch untracked files, and accept never stages
    `.rig/`, so untracked run state cannot be lost by the rollback and has no business
    stopping it.

    It is not a hypothetical exemption. `wb new` writes `.rig/runs/<id>/`, the context meter
    appends `.rig/context.jsonl` and the lock files land in `.rig/locks/` — every task, before
    anyone can accept it. In a repository whose `.gitignore` has no `.rig/` entry (the default
    since task creation stopped adding one unasked) that is a permanently dirty tree, and the
    old message's advice — commit or stash — meant committing rig's execution history into the
    repository: the PR contamination the ignore entry exists to prevent, arrived by instruction.

    Tracked-and-modified `.rig/` is deliberately *not* covered. There a reset would move real
    index content, so the check still fires; the message says to untrack rather than to commit.
    """
    return entry.startswith("?? ") and _names_state(entry)


def _task_head(root: pathlib.Path, task: dict) -> str | None:
    """The task branch tip. Approvals are bound to it, so a branch that moves after
    an approval stops counting as approved (see govern.approval)."""
    wt = task.get("worktree_path")
    cwd = pathlib.Path(wt) if wt and pathlib.Path(wt).is_dir() else root
    proc = git(["rev-parse", "HEAD"], cwd=cwd, check=False)
    return proc.stdout.strip() or None if proc.returncode == 0 else None


def _semantic_diff_section(root: pathlib.Path, task: dict, names: list) -> list:
    """Summarize changed *.py files with an AST diff (#280). Augments the text diff, never replaces it.

    Non-Python / unparseable files simply get `supported: False` from `ast_diff` itself —
    this function only narrows down which files to call it on (Modified *.py only) and
    holds no judgment logic of its own.
    """
    wt = pathlib.Path(task["worktree_path"]) if task.get("worktree_path") else root
    base, _drift = effective_base(root, task)
    py_modified = []
    for line in names:
        parts = line.split("\t")
        if len(parts) >= 2 and parts[0] == "M" and parts[-1].endswith(".py"):
            py_modified.append(parts[-1])
    if not py_modified:
        return []
    out = ["", "Semantic diff (Python, #280):"]
    for path in py_modified:
        base_src = git(["show", f"{base}:{path}"], cwd=wt).stdout
        try:
            new_src = (wt / path).read_text(encoding="utf-8")
        except OSError:
            continue
        result = ast_diff.semantic_diff(base_src, new_src)
        out.append(ast_diff.format_summary(result, path))
    return out


def cmd_diff(args: argparse.Namespace) -> None:
    root = repo_root()
    task_id = resolve_task_id(root, args.task_id)
    d, task = load_task(root, task_id)
    acc = load_json(d / "acceptance.json", build_acceptance(task_id, task["task_type"], root))
    names, stat, dirty = _diff_lines(root, task)

    eff_base, drifted_from = effective_base(root, task)
    print(f"## rig diff: {task_id}")
    print(f"base: {task['base_branch']} @ {task['base_commit'][:12]}")
    for line in drift_lines(task, drifted_from, eff_base):
        print(line)
    if task.get("branch"):
        print(f"branch: {task['branch']}")
    print()
    print("Changed files:")
    if not names and not dirty:
        print("  (no changes)")
    for line in names:
        print(f"  {line}")
    if stat:
        print(f"  {stat}")
    if dirty:
        print(f"\n[WARN] worktree has {len(dirty)} uncommitted change(s) (must be committed before accept):")
        for line in dirty[:20]:
            print(f"  {line}")

    for line in _semantic_diff_section(root, task, names):
        print(line)

    diff_md = d / "diff.md"
    sections = parse_diff_md(diff_md.read_text(encoding="utf-8")) if diff_md.exists() else {}
    for label, key in (("Summary", "summary"), ("Risk", "risk"), ("Tests", "tests")):
        print(f"\n{label}:")
        print(f"  {sections[key]}" if sections.get(key) else "  (not written)")

    print("\nUnrelated diff:")
    unrelated = next((c for c in acc["checks"] if c["name"] == "no_unrelated_diff"), None)
    if "unrelated diff" in sections:
        print(f"  {sections['unrelated diff']}")
    elif unrelated:
        print(f"  {CHECK_ICON[unrelated['status']]} {unrelated['status']}"
              + (f" — {unrelated['detail']}" if unrelated.get("detail") else "")
              + (f"\n  note (operator): {unrelated['note']}"
                 if unrelated.get("note") and unrelated["note"] != unrelated.get("detail")
                 else ""))
    else:
        print("  (not checked)")

    if not diff_md.exists():
        print(f"\n[NOTE] {diff_md.relative_to(root)} has not been created. A diff summary is required before accept.")

    print(f"\nRecommended:\n  {RECOMMENDATION[gate_status(acc)]}")


def cmd_accept(args: argparse.Namespace) -> None:
    root = repo_root()
    task_id = resolve_task_id(root, args.task_id)
    with task_lock(root, task_id):
        _cmd_accept_locked(args, root, task_id)


def _cmd_accept_locked(args: argparse.Namespace, root: pathlib.Path, task_id: str) -> None:
    d, task = load_task(root, task_id)

    if task["status"] == "accepted":
        die(f"task '{task_id}' has already been accepted")
    if task["status"] == "discarded":
        die(f"task '{task_id}' has already been discarded")

    # ── RBAC (only takes effect if .rig/access.json exists; #282. Solo use stays unrestricted) ──
    # v1's allowlist. Still honoured verbatim: a team that tuned this file keeps
    # working after upgrading to v2, and `govern migrate` folds it into a policy
    # layer when they are ready. The policy layer below is checked in addition,
    # never instead — two restrictions both apply, which is the safe composition.
    access = load_access_control(root)
    if access:
        allowed = access.get(task["task_type"]) or access.get("default") or []
        who = current_identity(root)
        if allowed and who not in allowed:
            # A judgement: rig read the allowlist and refused this actor. Nothing here
            # failed to work, so a caller must be able to tell it from one that did.
            reject(f"'{who}' is not permitted to accept task_type '{task['task_type']}' "
                   f"(allowed: {', '.join(allowed)}). Check `.rig/access.json` or ask "
                   "someone with permission to accept this.")

    acc = load_json(d / "acceptance.json", build_acceptance(task_id, task["task_type"], root))
    status = gate_status(acc)
    diff_md = d / "diff.md"
    diff_summary_ok = diff_md.exists() and diff_md.read_text(encoding="utf-8").strip() != ""
    unrelated = next((c for c in acc["checks"] if c["name"] == "no_unrelated_diff"), None)
    unrelated_ok = (unrelated is None) or (unrelated["status"] in ("passed", "warning", "skipped"))
    gate_ok = status in ("passed", "passed_with_warnings", "skipped")

    # ── accept_requirements checklist (Phase 3: show all items first, then judge) ──
    hard = [
        ("worktree_exists", bool(task.get("worktree_path")) and pathlib.Path(task["worktree_path"]).is_dir()),
        ("base_branch_recorded", bool(task.get("base_branch")) and bool(task.get("base_commit"))),
        ("diff_summary_generated", diff_summary_ok),
    ]
    soft = [
        ("acceptance_gate_not_failed", gate_ok),
        ("no_unrelated_diff", unrelated_ok),
    ]
    print(f"## rig accept: {task_id} — accept_requirements")
    for name, ok in hard + soft:
        print(f"  {'✓' if ok else '✗'} {name}")

    hard_fail = [name for name, ok in hard if not ok]
    if hard_fail:
        hints = {
            "worktree_exists": "this task has no worktree (--no-worktree run, or already discarded)",
            "base_branch_recorded": "task.json has no base_branch/base_commit recorded (run-state may be corrupted)",
            "diff_summary_generated": f"{diff_md.relative_to(root)} has not been created. Write the `/rig diff` prose summary first",
        }
        # Not a verdict on the work: the worktree, the recorded base, or the diff summary
        # is missing, so there is nothing for the gate to judge. `--force` cannot override
        # it for the same reason.
        die("Cannot accept (structural preconditions unmet; not overridable even with --force):\n"
            + "\n".join(f"  - {n}: {hints[n]}" for n in hard_fail))

    # ── base drift (#312): rebasing a task branch is legitimate, so drift never blocks —
    # but it must never be silent either, because the range it changes is the range this
    # command applies. Quiet when the recorded base still is the merge base.
    eff_base, drifted_from = effective_base(root, task)
    for line in drift_lines(task, drifted_from, eff_base):
        print(line)
    if drifted_from is None and git(["rev-parse", "--verify", f"{task['base_branch']}^{{commit}}"],
                                    cwd=root, check=False).returncode != 0:
        warn(f"base branch '{task['base_branch']}' no longer resolves; the diff range falls back to "
             f"the recorded base_commit {task['base_commit'][:12]}, which may be stale")
    # What `accept` actually applies is decided by `git merge --squash` against the main
    # working tree's HEAD, not by base_branch. They coincide unless the main tree is on
    # some other branch — in which case the preview above and the applied range differ,
    # which is precisely the silent wrongness this is here to prevent.
    if git(["merge-base", "--is-ancestor", eff_base, "HEAD"], cwd=root, check=False).returncode != 0:
        warn(f"the diff range base {eff_base[:12]} is not an ancestor of the main working tree's HEAD "
             f"(HEAD is not on '{task['base_branch']}', or it diverged) — the squash merge below applies "
             f"a different range than the preview. Check out {task['base_branch']} before accepting")

    soft_fail = [name for name, ok in soft if not ok]

    # The gate speaks first. Someone whose gate is simply unmet needs to hear that,
    # not a governance message about an approval they do not yet need.
    if soft_fail and not args.force:
        failed_checks = [c["name"] for c in acc["checks"] if c["status"] in ("failed", "pending")]
        # The acceptance gate is the verdict, and this is rig delivering it.
        reject(
            f"Cannot accept because the acceptance-gate is {status} (unmet: {', '.join(failed_checks) or 'no_unrelated_diff'}).\n"
            f"  Record the criteria you have judged with `workbench.py gate {task_id} --set <criterion>=<status>`.\n"
            f"  A criterion a sensor backs is not one of them — `--set` is refused there: remove the\n"
            f"  finding from the diff, re-run `gate`, and the same `--set` is then accepted because it\n"
            f"  agrees with the measurement. The sensor records the finding or its absence, not the pass.\n"
            f"  Or pass --force if you understand the risk (recorded in .rig/audit.jsonl and provenance.json)"
        )

    # ── governance (v2; inert unless .rig/org.json + a policy layer exist) ──
    # Permission to accept, the approval quorum, and — when forcing — the right to
    # force plus a live waiver for every criterion being bypassed. Evaluated before
    # anything is written or merged, so a refusal leaves the tree and the run-state
    # exactly as they were.
    unmet_criteria = sorted({c["name"] for c in acc["checks"] if c["status"] in ("failed", "pending")}
                            | ({"no_unrelated_diff"} if "no_unrelated_diff" in soft_fail else set()))
    gov = govern_enforce.check_accept(root, task, bypassed=unmet_criteria,
                                      force=bool(soft_fail), head=_task_head(root, task))
    for line in gov.lines:
        print(line)
    if gov.blocked:
        # Permission, quorum, or a missing waiver: the policy layer looked and said no.
        reject(f"governance: {gov.blocked}")

    if soft_fail:
        warn(f"Accepting with unmet requirements overridden by --force ({', '.join(soft_fail)}). Recording forced: true in task.json")
        task["forced"] = True
        audit_append(root, {
            "ts": now_iso(),
            "action": "accept_force",
            "task_id": task_id,
            "task_type": task.get("task_type"),
            "recipe": task.get("recipe"),
            "bypassed": soft_fail,
            "gate_status": status,
            "failed_checks": [c["name"] for c in acc["checks"]
                              if c["status"] in ("failed", "pending")],
            "invoker": __import__("os").environ.get("RIG_INVOKER") or "direct",
        })
    if status == "passed_with_warnings":
        warns = [f"{c['name']} ({c.get('detail') or 'no detail'})" for c in acc["checks"] if c["status"] == "warning"]
        warn("Accepting with unresolved warnings: " + " / ".join(warns))

    if not task.get("worktree_path"):
        die("This task has no worktree (--no-worktree run). There is no diff to accept")

    # (2) Worktree consistency check
    wt = pathlib.Path(task["worktree_path"])
    if not wt.is_dir():
        die(f"worktree {wt} does not exist")
    dirty = worktree_dirty(wt)
    if dirty:
        die(
            f"worktree has {len(dirty)} uncommitted change(s). "
            f"Commit them in the worktree before accepting (git -C {wt} add -A && git -C {wt} commit)"
        )
    branch = task["branch"]
    ahead = git(["rev-list", "--count", f"{eff_base}..{branch}"], cwd=root).stdout.strip()
    if ahead == "0":
        die(f"branch {branch} has no commits on top of base (no diff to accept)")

    # (2)-b Main working tree consistency check (guarantees up front that a failed
    # squash merge can be safely rolled back with `git reset --hard HEAD`.
    # `git merge --squash` does not create MERGE_HEAD on failure, so
    # `git merge --abort` doesn't work — without this pre-check, reset --hard
    # would wipe out the user's existing uncommitted changes)
    root_dirty = git(["status", "--porcelain"], cwd=root).stdout.splitlines()
    blocking = [entry for entry in root_dirty if not _is_untracked_state(entry)]
    if blocking:
        die(
            f"The working tree has {len(blocking)} uncommitted change(s). "
            f"accept only runs on a clean working tree so that the squash merge can be safely rolled back. "
            + _dirty_root_advice(blocking)
        )

    # (3) Squash merge into the main working tree (no commit = the final decision is an explicit human/model action)
    proc = git(["merge", "--squash", branch], cwd=root, check=False)
    if proc.returncode != 0:
        # Why the squash failed decides what the operator should do next, and git answers
        # that on two separate channels. A content conflict prints "CONFLICT (content): …"
        # on *stdout*, leaves stderr empty and exits 1; a refusal to run at all — no
        # committer identity, an unwritable index — is a "fatal:"/"error:" on stderr,
        # usually exit 128. Reading stderr alone and calling every failure a conflict is
        # how a runner with no `user.name` was told "divergence from base, go rebase":
        # the advice was wrong, and the one line that said why was the empty string.
        #
        # The unmerged paths have to be read here, while the failed merge's index is still
        # in place — the rollback below is what erases the evidence.
        # `-z` for the paths: without it git C-quotes anything non-ASCII
        # ("unicode_\343\203\225…"), which is not a path the operator can copy into a
        # command. The stdout fallback below is the quoted form and is only reached when
        # the index could not be read at all.
        unmerged = [p for p in git(["diff", "--name-only", "-z", "--diff-filter=U"],
                                   cwd=root, check=False).stdout.split("\0") if p.strip()]
        conflicted = unmerged or re.findall(r"^CONFLICT \([^)]+\): Merge conflict in (.+)$",
                                            proc.stdout, re.M)
        # Squash merge doesn't create MERGE_HEAD so `merge --abort` doesn't work. Having
        # guaranteed above that the working tree was clean, roll back with reset --hard.
        # This runs on every branch below: whatever went wrong, the tree the operator
        # started with is the tree they get back.
        git(["reset", "--hard", "HEAD"], cwd=root, check=False)
        restored = "The working tree was restored to its pre-merge state."
        if conflicted:
            paths = sorted(set(conflicted))
            listed = "".join(f"    {p}\n" for p in paths[:_CONFLICT_LIST_MAX])
            if len(paths) > _CONFLICT_LIST_MAX:
                listed += f"    … and {len(paths) - _CONFLICT_LIST_MAX} more\n"
            die(
                f"squash merge conflicted in {len(paths)} file(s). {restored}\n"
                f"{listed}"
                f"  The base moved under this branch, which is legitimate (see `effective_base`).\n"
                f"  Merge the base into the task branch and resolve it there:\n"
                f"    git -C {wt} merge {task['base_branch']}\n"
                f"  then commit the resolution and retry accept. A rebase is allowed too — accept\n"
                f"  recomputes the range from the live merge base either way — and is advised\n"
                f"  against only because a merge keeps the commits the gate was evaluated over\n"
                f"  reachable instead of replacing them with new shas. Either move shifts the\n"
                f"  branch tip, so a governance approval bound to the old tip has to be given again."
            )
        detail = (proc.stderr.strip() or proc.stdout.strip())
        if _MISSING_IDENTITY_RE.search(detail):
            die(
                f"squash merge could not run: git has no committer identity in this repository "
                f'("empty ident name" / "Please tell me who you are"). {restored}\n'
                f"  This is not a conflict and there is nothing to resolve in the worktree. "
                f"Set an identity and retry:\n"
                f"    git -C {root} config user.name \"Your Name\"\n"
                f"    git -C {root} config user.email \"you@example.com\""
            )
        # The tail, not the head: git puts the decisive `fatal:`/`error:` line last and
        # can precede it with pages of per-file noise (a failing smudge filter), so a
        # head-first cut drops the one line this message exists to carry.
        lines = detail.splitlines()
        if len(lines) > _STDERR_HEAD_LINES + _STDERR_TAIL_LINES:
            omitted = len(lines) - _STDERR_HEAD_LINES - _STDERR_TAIL_LINES
            lines = [*lines[:_STDERR_HEAD_LINES], f"… {omitted} line(s) omitted …",
                     *lines[-_STDERR_TAIL_LINES:]]
        trimmed = "\n".join(f"    {line}" for line in lines) or "    (git said nothing)"
        die(
            f"squash merge failed (git exit {proc.returncode}), and not from a conflict or a "
            f"missing identity. {restored} git said:\n{trimmed}"
        )

    task["status"] = "accepted"
    task["accepted_at"] = now_iso()
    save_task(d, task)

    # The governed record of the decision. Written for every accept under a
    # policy, not only the forced ones: "who applied what, when, under which
    # policy, with whose approval" is what an audit asks, and a ledger holding
    # only the exceptions cannot answer it.
    govern_enforce.record_accept(root, task, gov, forced=bool(task.get("forced")),
                                 bypassed=unmet_criteria, gate_status=status)

    # ── signed provenance (#299) — a tamper-evident record of the accept decision and the
    # gate result it was based on. HMAC-SHA256 with a locally-held key: same-machine
    # tamper-evidence, not third-party public verification (see sign_provenance docstring).
    provenance_record = {
        "task_id": task_id,
        "task_type": task["task_type"],
        "recipe": task.get("recipe") or None,
        "base_branch": task["base_branch"],
        "base_commit": task["base_commit"],
        # The range this accept was actually computed and applied against (#312). Equal to
        # base_commit unless the branch was rebased after registration; `base_rebased` makes
        # that fact part of the tamper-evident record rather than a line of scrollback.
        "base_commit_effective": eff_base,
        "base_rebased": drifted_from is not None,
        "branch": branch,
        "accepted_at": task["accepted_at"],
        "gate_status": status,
        "forced": bool(task.get("forced")),
        "checks": sorted([{"name": c["name"], "status": c["status"]} for c in acc["checks"]],
                         key=lambda c: c["name"]),
    }
    signature = sign_provenance(root, provenance_record)
    save_json(d / "provenance.json", {"record": provenance_record, "signature": signature, "algo": "HMAC-SHA256"})

    # After the governance ledger and the signed provenance, never before them. Those
    # answer "who applied what, under which policy, and can it be shown untampered";
    # this one is a usage statistic. Ordering it first would let a telemetry problem
    # leave an accepted task with no audit record at all.
    record_task_run(root, task, "accepted")

    names, stat, _ = _diff_lines(root, task)
    print(f"\n## rig accept: {task_id} ✓")
    print(f"Applied the changes from branch {branch} ({ahead} commits) to the main working tree as **staged**.")
    if stat:
        print(f"  {stat}")
    print(f"Provenance: {(d / 'provenance.json').relative_to(root)} "
          f"(verify with `workbench.py verify-provenance {task_id}`)")
    print("Next actions:")
    print("  1) Review: git diff --staged")
    print("  2) Commit: git commit")
    print(f"  3) Clean up: workbench.py discard {task_id} --yes  (removes the worktree and branch; keeps the run log)")


def cmd_verify_provenance(args: argparse.Namespace) -> None:
    """Verify an accepted task's signed provenance record (#299). The key lives at
    `.rig/provenance.key` (local only, gitignored) — this checks that the record hasn't
    been edited after the fact on this same machine, not a third-party public signature
    (SLSA/Ed25519 public verification is a different, heavier guarantee)."""
    root = repo_root()
    task_id = resolve_task_id(root, args.task_id)
    d, _task = load_task(root, task_id)
    p = d / "provenance.json"
    if not p.is_file():
        die(f"task '{task_id}' has no provenance record (created at accept time; this task may not be accepted yet)")
    data = load_json(p)
    ok = verify_provenance(root, data["record"], data["signature"])
    print(f"## rig verify-provenance: {task_id}")
    print(f"signature: {'✓ valid (untampered)' if ok else '✗ INVALID (record or key may have changed)'}")
    print(json.dumps(data["record"], ensure_ascii=False, indent=2))
    if not ok:
        sys.exit(1)


def cmd_discard(args: argparse.Namespace) -> None:
    root = repo_root()
    if not args.task_id:
        die("discard requires an explicit task_id to prevent accidents (see `workbench.py log`)")
    with task_lock(root, args.task_id):
        _cmd_discard_locked(args, root)


def _cmd_discard_locked(args: argparse.Namespace, root: pathlib.Path) -> None:
    d, task = load_task(root, args.task_id)
    task_id = task["task_id"]

    names, stat, dirty = _diff_lines(root, task)
    print(f"## rig discard: {task_id}")
    print(f"input: {task['input']}")
    print("Changed files to be discarded:")
    if names or dirty:
        for line in names:
            print(f"  {line}")
        for line in dirty:
            print(f"  {line}  (uncommitted)")
    else:
        print("  (no changes)")

    if not args.yes:
        die("Re-run with --yes to confirm (the changes listed above will be lost)")

    # Disposal goes back to whoever created it (#461). Reading the path and calling
    # `git worktree remove` on it happens to work while native is the only backend, and
    # stops being true the moment another runtime owns the directory.
    #
    # Asked through `reconnect` rather than `for_task` (#463): `for_task` raises when the
    # owning runtime is not usable here, and a raise leaves the operator with a traceback,
    # advice naming a flag this command does not have, and a task that can never be cleaned
    # up. "The runtime is gone" is a state to report, not a crash.
    state = runtime_mod.reconnect(task, root)
    handle = state["handle"]
    cleanup_note = None
    if state["state"] == runtime_mod.RUNTIME_UNAVAILABLE:
        if not getattr(args, "local_cleanup", False):
            die(f"this task's worktree belongs to the {state['runtime']!r} runtime, which is "
                f"not usable here: {state['detail']}\n"
                f"  Rig will not quietly dispose of it with a different runtime — that would "
                f"delete a directory it no longer owns and report success.\n"
                f"  Restore {state['runtime']!r} and re-run, or pass --local-cleanup to remove "
                f"the checkout with git and record that its runtime never saw it.")
        # Explicit, and written down. The audit has to show that this worktree was disposed
        # of by something other than its owner, or a later reader cannot tell an orderly
        # teardown from one that left workspace state stranded in another tool.
        cleanup_note = (f"runtime {state['runtime']!r} was unavailable ({state['detail']}); "
                        f"removed locally with git at the operator's explicit request")
        print(f"◇ {cleanup_note}")
        runtime_mod.BACKENDS[runtime_mod.NATIVE].remove(root, handle, strict=False)
    elif state["state"] == runtime_mod.WORKTREE_MISSING:
        # Nothing to remove, and saying so beats a backend error about a path that is
        # already gone — the outcome the operator wanted is the one they already have.
        cleanup_note = f"worktree was already absent ({state['detail']})"
        print(f"◇ {cleanup_note}")
    elif handle:
        state["backend"].remove(root, handle)
    if task.get("branch"):
        proc = git(["rev-parse", "--verify", task["branch"]], cwd=root, check=False)
        if proc.returncode == 0:
            git(["branch", "-D", task["branch"]], cwd=root)
    discarded_now = task["status"] != "accepted"  # cleanup after accept keeps the accepted status
    if discarded_now:
        task["status"] = "discarded"
    task["cleaned_at"] = now_iso()
    if cleanup_note:
        task["cleanup_note"] = cleanup_note
    task["worktree_path"] = None
    task["worktree"] = None
    save_task(d, task)
    if discarded_now:
        # Only when this call is what ended the task. Cleaning up after an accept runs
        # through here too, and that task was already recorded at accept time.
        record_task_run(root, task, "discarded")

    # Temporary visual-verification artifacts (screenshots etc.) are a means, not
    # a decision record, so delete them immediately on discard
    # (the run log's JSON/MD is kept. See patterns/visual-artifacts).
    visual_dir = d / "visual"
    visual_removed = visual_dir.is_dir()
    if visual_removed:
        shutil.rmtree(visual_dir, ignore_errors=True)

    print(f"Removed the worktree and branch. The run log remains at {d.relative_to(root)}/.")
    if visual_removed:
        print(f"Also removed temporary visual-verification images ({visual_dir.relative_to(root)}/).")


def _dir_age_days(p: pathlib.Path) -> float:
    return (datetime.datetime.now().timestamp() - p.stat().st_mtime) / 86400.0


def cmd_gc(args: argparse.Namespace) -> None:
    """Age-based disposal of temporary visual-verification artifacts (see `patterns/visual-artifacts`).

    Task status (accepted/discarded/running) is irrelevant — the images are a
    regenerable verification means, not permanent records. Never touches
    sources, worktrees, or branches.
    """
    root = repo_root()
    threshold_days = 14
    if args.older_than:
        m = re.match(r"^(\d+)d$", args.older_than)
        if not m:
            die(f"--older-than must be given as '<N>d' (e.g. 14d; got: {args.older_than!r})")
        threshold_days = int(m.group(1))

    candidates: list[pathlib.Path] = []
    runs = runs_dir(root)
    if runs.is_dir():
        candidates.extend(p / "visual" for p in runs.iterdir() if (p / "visual").is_dir())
    adhoc = root / ".rig" / "visual" / "adhoc"
    if adhoc.is_dir():
        candidates.extend(p for p in adhoc.iterdir() if p.is_dir())

    to_remove = [p for p in candidates if _dir_age_days(p) >= threshold_days]

    print(f"## rig gc (threshold: {threshold_days} days{', dry-run' if args.dry_run else ''})")
    if not to_remove:
        print("Nothing to remove.")
        return
    for p in sorted(to_remove):
        rel = p.relative_to(root)
        age = _dir_age_days(p)
        prefix = "[dry-run] " if args.dry_run else ""
        print(f"  {prefix}remove: {rel}/ ({age:.1f} days old)")
        if not args.dry_run:
            shutil.rmtree(p, ignore_errors=True)
    verb = "candidate(s) (not removed due to --dry-run)" if args.dry_run else "removed"
    print(f"\n{len(to_remove)} {verb}.")
