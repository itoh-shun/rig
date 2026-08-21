"""Register a change rig did not produce (#429, Bring Your Own Orchestrator).

Rig's assurance is worth more when it does not depend on rig having done the work.
An external orchestrator — another harness, a CI job, a person with a branch — can
produce a change, and rig can still be the boundary that decides whether it is
acceptable: its own isolation, its own deterministic sensors, its own gate, its own
governance, its own signed provenance.

The load-bearing decision here is that **nothing about that path is new**. `import`
creates the ordinary run directory and the ordinary worktree; the only difference is
that the task branch is created *at the imported commit* rather than at the base. From
that point `diff`, every sensor, `gate`, `govern`, `accept` and the assurance receipt
operate unchanged, because there is nothing for them to tell apart. "An imported task
cannot skip verification" is therefore true by construction rather than by policy —
there is no second accept path to keep honest.

Three things this refuses to do.

**A producer's own PASS is not a gate PASS.** `--producer-claim` is recorded, and it
is recorded as a claim: it lands in the task's import block and in the receipt with
`gate_effect: "none"`, and there is no code path from it into `acceptance.json`. An
external orchestrator reporting `tests=passed` has told rig something worth keeping
next to the verdict, not something that changes it.

**Rig does not branch its rules on who called.** No producer name is consulted
anywhere in the gate, accept or governance path. The contract is the same one for
every caller, which is the only way it can be reused by the next one.

**A name is not a commit.** `--head refs/heads/foo` names something that can move;
`--head <sha>` names an object that cannot. Both are accepted, both are recorded as
what they are, and the difference is carried through to the receipt's `immutable`
field and to the staleness check rather than being flattened into "the head".
"""

from __future__ import annotations

import argparse
import pathlib
import shutil

from rig_workbench import caller as caller_mod
from rig_workbench.govern import identity as govern_identity
from rig_workbench.packs.model import PackError

from .capabilities import resolve_task_route
from .config import TASK_TYPES
from .flow_view import render_flow
from .lifecycle import ensure_rig_gitignored, find_similar_tasks
from .progress import load_recipe_steps
from . import runtime as runtime_mod
from .state import (build_acceptance, current_branch, die, git, invocation_root,
                    make_slug, make_task_id, now_iso, repo_root, runs_dir,
                    save_json)

#: What the import block records under. Kept as a single nested key so that every
#: consumer can ask one question — "was this produced elsewhere?" — instead of
#: inferring it from the presence of scattered fields.
IMPORT_KEY = "import"


def _resolve_head(root: pathlib.Path, head: str) -> tuple[str, bool, str | None]:
    """Resolve `--head` to (commit sha, was it symbolic, the full ref name if so).

    Symbolic means the caller named something git can repoint: a branch or a tag.
    Rig accepts it — refusing would make the contract unusable from a CI job that only
    knows its branch — but it never calls the result immutable, and it keeps the ref
    so that a later check can notice the name now resolves somewhere else.
    """
    proc = git(["rev-parse", "--verify", "--quiet", f"{head}^{{commit}}"], cwd=root, check=False)
    sha = proc.stdout.strip()
    if proc.returncode != 0 or not sha:
        die(f"--head '{head}' does not resolve to a commit in this repository")
    ref = git(["rev-parse", "--symbolic-full-name", head], cwd=root, check=False).stdout.strip()
    return sha, bool(ref), ref or None


def _parse_claims(pairs: list[str] | None) -> list[dict]:
    """`--producer-claim name=value` → recorded claims.

    Every claim carries `gate_effect: "none"` in the record itself, rather than
    relying on a reader to know it. A field that has to be explained elsewhere to be
    read correctly will eventually be read without the explanation.
    """
    claims = []
    for pair in pairs or []:
        if "=" not in pair:
            die(f"--producer-claim must be given as <name>=<value> (got: {pair!r})")
        name, value = pair.split("=", 1)
        try:
            name = caller_mod.normalise_name(name, field="--producer-claim name")
            value = caller_mod.reject_deceptive(
                value, field="--producer-claim value", max_length=caller_mod.MAX_PROVENANCE)
        except (TypeError, ValueError) as exc:
            die(str(exc))
        claims.append({"name": name, "value": value, "gate_effect": "none"})
    return claims


def _derived_diff_md(root: pathlib.Path, base_sha: str, head_sha: str,
                     producer: str) -> str:
    """A diff summary built from the imported commits, labelled as what it is.

    `accept` requires a diff summary and treats a missing one as structural — not
    overridable even with `--force` — because a change nobody described is a change
    nobody read. An external orchestrator running headlessly writes no `diff.md`, so
    without this every imported task would be permanently unacceptable, and the flow
    this issue exists to enable would stop one step short of its point.

    What it must not do is *look* authored. The commit messages are the producer's own
    account of its work; deriving a summary from them is a convenience, not a review,
    and the heading says so in the file that `accept` reads and that the receipt
    digests.
    """
    log = git(["log", "--no-merges", "--format=- %h %s", f"{base_sha}..{head_sha}"],
              cwd=root, check=False).stdout.strip()
    stat = git(["diff", "--stat", f"{base_sha}..{head_sha}"], cwd=root, check=False).stdout.strip()
    return "\n".join([
        f"# imported change — produced by `{producer}`",
        "",
        "## summary",
        "",
        f"Derived at import from the commit messages of `{base_sha[:12]}..{head_sha[:12]}`. "
        "**No reviewer wrote this**: it is the producer's own account of its work, "
        "restated. Replace it with an authored summary (`--summary <file>`, or by "
        "editing this file) before treating it as one.",
        "",
        log or "(no commits in range)",
        "",
        "## tests",
        "",
        "Not stated by this import. Whatever the producer claimed is recorded under "
        "`producer.external.claims` in the assurance receipt, where it carries "
        "`gate_effect: none` — rig's own gate is the only thing that decides this task.",
        "",
        "## risk",
        "",
        "Not stated by this import.",
        "",
        "## changed files",
        "",
        "```",
        stat or "(no changes)",
        "```",
        "",
    ])


def cmd_import(args: argparse.Namespace) -> None:
    root = repo_root()
    if args.type not in TASK_TYPES:
        die(f"task_type '{args.type}' is invalid. Valid: {', '.join(TASK_TYPES)}")

    # Sanitise before anything is created. Every one of these strings is printed back
    # to the operator and rendered into the receipt, and they are the first values in
    # that record supplied from outside rig — the same rule `--caller` is held to
    # applies, from the same module, so a second and quietly diverging definition of
    # "characters that make printed text lie" cannot come into existence here.
    try:
        producer = caller_mod.normalise_name(args.producer, field="--producer")
        producer_runtime = (caller_mod.normalise_name(args.producer_runtime,
                                                      field="--producer-runtime")
                            if args.producer_runtime else None)
        producer_run_id = (caller_mod.reject_deceptive(
            args.producer_run_id, field="--producer-run-id",
            max_length=caller_mod.MAX_PROVENANCE) if args.producer_run_id else None)
        producer_url = (caller_mod.reject_deceptive(
            args.producer_url, field="--producer-url",
            max_length=caller_mod.MAX_PROVENANCE) if args.producer_url else None)
    except (TypeError, ValueError) as exc:
        die(str(exc))
    claims = _parse_claims(args.producer_claim)

    # Read before anything exists on disk. Every precondition in this half of the
    # command is here for one reason: a command that fails *after* creating a worktree
    # leaves a branch and a run directory nobody asked for, and cleaning that up means
    # knowing rig's own layout.
    summary_text = None
    if args.summary:
        try:
            summary_text = pathlib.Path(args.summary).read_text(encoding="utf-8")
        except OSError as exc:
            die(f"--summary {args.summary}: {exc}")

    # Both the head being imported and the base it is measured against are questions about
    # the working tree the caller is standing in, not about where rig keeps state (#471).
    here = invocation_root()
    head_sha, head_symbolic, head_ref = _resolve_head(here, args.head)

    base_branch = args.base or current_branch(here)
    proc = git(["rev-parse", "--verify", f"{base_branch}^{{commit}}"], cwd=here, check=False)
    base_sha = proc.stdout.strip()
    if proc.returncode != 0 or not base_sha:
        die(f"--base '{base_branch}' does not resolve to a commit")
    if base_sha == head_sha:
        die(f"--head resolves to the same commit as --base ({head_sha[:12]}); there is "
            f"nothing to verify")
    if git(["merge-base", "--is-ancestor", head_sha, base_sha],
           cwd=root, check=False).returncode == 0:
        die(f"--head {head_sha[:12]} is already contained in --base '{base_branch}'; "
            f"there is nothing to verify")

    context = {"recipe": getattr(args, "recipe", None), "remote_pr": False,
               "has_diff": True, "diff": None, "read_only": False,
               "implementation_type": None}
    try:
        # Branch content, so the caller's tree — see cmd_new (#471).
        route = resolve_task_route(args.type, context, invocation_root(), shared=root)
    except PackError as exc:
        die(str(exc))
    if route["status"] in {"stopped", "trust_required"}:
        suffix = f" Hint: {route['hint']}" if route["hint"] else ""
        die(f"route {route['status']}: {route['reason']}.{suffix}")
    if not route["worktree"]:
        die(f"task_type '{args.type}' routes to a worktree-less capability, and an "
            f"imported change has nowhere to live without one. Import it as a "
            f"change-producing type instead")

    subject = git(["log", "-1", "--format=%s", head_sha], cwd=root, check=False).stdout.strip()
    task_input = args.input or (f"imported from {producer}: {subject}" if subject
                                else f"imported from {producer}: {head_sha[:12]}")
    slug = args.slug or make_slug(task_input)
    task_id = make_task_id(slug)
    d = runs_dir(root) / task_id
    if d.exists():
        die(f"task '{task_id}' already exists")

    # Compose the gate before creating anything, same as `new`: a malformed
    # `.rig/gates.json` must abort with no partial state on disk.
    acc = build_acceptance(task_id, args.type, root)

    if ensure_rig_gitignored(root):
        print("◇ Appended .rig/ to .gitignore (prevents PR contamination)")

    # The one line that makes the rest of rig work unchanged: the task branch is
    # created *at the imported commit*. `base..branch` is then the external change,
    # and every sensor, the gate, governance and `accept` see an ordinary task.
    backend = runtime_mod.select(None, root)   # see cmd_new: the flag arrives with #462
    branch = f"rig/{task_id}"
    handle = backend.create(root, task_id, head_sha, branch)
    wt = pathlib.Path(handle.path)

    # Past this point the worktree and branch exist. Anything that fails now leaves
    # state only this command created — the run directory was refused above if it was
    # already there — so it is removed rather than left as a zombie import. `die` raises
    # SystemExit, which is why this catches BaseException rather than Exception.
    try:
        task = {
            "task_id": task_id,
            "input": task_input,
            "task_type": args.type,
            "recipe": route["recipe"] or "",
            "recipe_reason": args.reason or route["reason"],
            "route": route,
            "base_branch": base_branch,
            "base_commit": base_sha,
            "branch": branch,
            "worktree_path": str(wt),
            "worktree": handle.as_state(),
            "status": "running",
            "created_at": now_iso(),
            "updated_at": now_iso(),
            "budget_minutes": args.budget_minutes,
            IMPORT_KEY: {
                "schema": "rig.byoo-import/v1",
                "producer": producer,
                "producer_runtime": producer_runtime,
                "run_id": producer_run_id,
                "source_url": producer_url,
                # The identity rig verified, pinned. Everything downstream compares
                # against this value rather than against whatever the ref means later.
                "head_commit": head_sha,
                "head_requested": args.head,
                "head_ref": head_ref,
                "head_symbolic": head_symbolic,
                "claims": claims,
                "claims_gate_effect": "none",
                "diff_summary": "authored" if summary_text is not None else "derived",
                "imported_at": now_iso(),
            },
        }
        task["actor"] = govern_identity.current_actor(root)
        _caller = caller_mod.detect(getattr(args, "caller", None))
        task["caller"] = {"id": _caller.id, "source": _caller.source,
                          "declared": _caller.declared}
        _binding = govern_identity.load_org_binding(root)
        if _binding.bound:
            task["org"] = _binding.org
            task["team"] = _binding.team

        d.mkdir(parents=True, exist_ok=True)
        save_json(d / "task.json", task)
        seeded = load_recipe_steps(task["recipe"])
        save_json(d / "steps.json", {"steps": seeded, "seeded": bool(seeded)})
        save_json(d / "acceptance.json", acc)

        if summary_text is not None:
            (d / "diff.md").write_text(summary_text, encoding="utf-8")
        else:
            (d / "diff.md").write_text(
                _derived_diff_md(root, base_sha, head_sha, producer), encoding="utf-8")

        print("▸ rig import (Bring Your Own Orchestrator)")
        print(f"producer: {producer}"
              + (f" / runtime {producer_runtime}" if producer_runtime else "")
              + (f" / run {producer_run_id}" if producer_run_id else ""))
        if producer_url:
            print(f"source: {producer_url}")
        print(f"target: {head_sha} "
              + (f"(from ref {head_ref} — a name, which can move; rig pinned the commit)"
                 if head_symbolic else "(an immutable commit, as given)"))
        print(f"recipe: {route['recipe'] or '(stopped)'} — {args.reason or route['reason']}")
        print(f"gate: {' + '.join(acc['presets'])}")
        if claims:
            print(f"producer claims recorded ({len(claims)}), none of which affects the gate:")
            for c in claims:
                print(f"  · {c['name']}={c['value']} (gate_effect: none)")
        print()
        print(f"task_id: {task_id}")
        print(f"base_branch: {base_branch} @ {base_sha[:12]}")
        print(f"worktree: {wt} (branch: {branch} @ {head_sha[:12]})")
        print(f"state: {d.relative_to(root)}/")
        print("diff summary: " + ("authored (--summary)" if summary_text is not None
                                  else "derived from the imported commit messages — not a review"))

        for line in render_flow(seeded, acc):
            print(line)

        similar = find_similar_tasks(root, task_input, exclude_task_id=task_id)
        if similar:
            print("\nSimilar tasks (past runs, deja-vu detection #290):")
            for t in similar:
                label = t["input"][:50] + ("…" if len(t["input"]) > 50 else "")
                print(f"  - {t['task_id']} ({t['status']}): {label}")

    except BaseException:
        # Back through the backend that created it: whichever runtime owns the directory
        # is the one that knows how to take it away (#461).
        backend.remove(root, handle, strict=False)
        git(["branch", "-D", branch], cwd=root, check=False)
        shutil.rmtree(d, ignore_errors=True)
        raise
