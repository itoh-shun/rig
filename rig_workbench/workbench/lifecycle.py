"""workbench lifecycle: task registration and progress recording — new/step/gate/review
(split from scripts/workbench.py)."""

import argparse
import pathlib
import re
import shlex
import sys

from rig_workbench import caller
from rig_workbench.govern import identity as govern_identity
from rig_workbench.packs.model import PackError
from rig_workbench.ports import Env, Presenter
from rig_workbench.ports.local import CONSOLE, OS_ENV

from .anchors import SENSOR_CRITERION as ANCHOR_CRITERION
from .anchors import apply_anchor_sensor
from .config import (CHECK_ICON, TASK_TYPES, VALID_CRITERION_STATUS,
                     VALID_STEP_STATUS, VALID_VERDICT, WRITER_OPERATOR)
from .capabilities import resolve_task_route
from .destructive import SENSOR_CRITERION as DESTRUCTIVE_CRITERION
from .destructive import apply_destructive_sensor
from .hardening import SENSOR_CRITERION as TAMPER_CRITERION
from .hardening import apply_tamper_sensor
from .injection import SENSOR_CRITERION as INJECTION_CRITERION
from .injection import apply_injection_sensor
from .issue_link import IssueRefError
from . import issue_link
from .flow_view import render_flow, render_transition
from .progress import from_state as progress_from_state
from .progress import load_recipe_steps
from .runtime import WorktreeHandle
from . import runtime as runtime_mod
from . import task_package
from .ja_prose import (apply_ja_lint_sensor, apply_ja_smell_sensor,
                       ensure_ja_prose_criteria)
from .prompt_regression import (CRITERION as PROMPT_REGRESSION_CRITERION,
                                apply_prompt_regression_sensor,
                                ensure_prompt_criterion)
from .schema_diff import apply_schema_sensor
from .secrets import SENSOR_CRITERION as SECRET_CRITERION
from .secrets import apply_secret_sensor, shared_diff_cache
from .state import (build_acceptance, current_branch, die, gate_status, git, invocation_root,
                    load_json, load_task, make_slug,
                    make_task_id, now_iso, repo_root, resolve_task_id, run_dir,
                    runs_dir, save_json, save_task, task_lock)


#: How to start a session, per harness rig can actually name one for. `caller.detect` only
#: recognises a harness from markers rig has measured, and declares the rest through
#: `--caller`; a harness it cannot name gets the `cd` alone rather than a command guessed on
#: its behalf, which would send the operator somewhere with an instruction that does not run.
_SESSION_LAUNCHER = {"claude-code": "claude", "codex": "codex"}


#: The entry rig wants ignored, and the comment it writes above it.
GITIGNORE_ENTRY = ".rig/"
_GITIGNORE_COMMENT = "# rig workbench state (task worktrees, telemetry, audit, locks)"

#: Why, in the one sentence the question, the refusal and the advisory line all reuse — so the
#: operator reads the same reason whichever of the three they meet.
_GITIGNORE_REASON = ("worktree state, run telemetry, the audit log and the locks live there, "
                     "and an unignored .rig/ rides along in the next commit")

#: Standing consent, spelled the way every other consent rig records is: an environment
#: variable set to "1" (`RIG_ALLOW_PROJECT_PACKS`, `RIG_ALLOW_PROJECT_MANIFEST`,
#: `RIG_ALLOW_PROJECT_RECIPES` — `rig_workbench/packs/trust.py`). Deliberately not a new config
#: file and not a manifest key: a decision about whether rig may edit a tracked file has to be
#: answerable without first trusting a file in the repository that is making the request.
GITIGNORE_CONSENT_ENV = "RIG_ALLOW_GITIGNORE"


def rig_gitignore_status(root: pathlib.Path) -> str:
    """`"ignored"`, `"missing"`, or `"not-git"` — what `.gitignore` says about `.rig/`.

    Already ignored as `.rig/` / `.rig` / `/.rig/` (any variant) counts as ignored: rig never
    clobbers the operator's own entries on a false positive. A tree that is not git-managed has
    no `.gitignore` to have an opinion, which is not the same as one that is missing the line.
    """
    if not (root / ".git").exists():
        return "not-git"
    gi = root / ".gitignore"
    if gi.exists():
        try:
            lines = gi.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeError):
            # Unreadable is not "missing": appending to a file rig could not read would be a
            # write made blind. Treat it as ignored — the advisory costs nothing, the write could.
            return "ignored"
        for ln in lines:
            if ln.strip() in (".rig/", ".rig", "/.rig/", "/.rig"):
                return "ignored"
    return "missing"


def append_rig_gitignore(root: pathlib.Path) -> None:
    """Append the `.rig/` entry to the repo's `.gitignore`, creating the file if absent."""
    with (root / ".gitignore").open("a", encoding="utf-8") as f:
        # The existing file may not end with a newline, so lead with one
        f.write(f"\n{_GITIGNORE_COMMENT}\n{GITIGNORE_ENTRY}\n")


def _answer_from_a_terminal(question: str) -> bool | None:
    """Ask once, y/N. `True` for a yes, `False` for anything else, `None` when the ask itself
    was cut short (EOF, Ctrl-C) and the caller owes the terminal a newline before it speaks."""
    try:
        answer = input(question)
    except (EOFError, KeyboardInterrupt):
        return None
    return answer.strip().lower() in {"y", "yes"}


def ensure_rig_gitignored(root: pathlib.Path, *, env: Env = OS_ENV,
                          out: Presenter = CONSOLE) -> bool:
    """Offer to ignore `.rig/`, and never write `.gitignore` without being told to.

    `.gitignore` is the one file outside `.rig/` that registering a task touches, and it is
    *tracked content*: appending to it silently puts a line in somebody's next commit that they
    never typed, in a repository rig was only asked to start a task in. rig is the thing that
    refuses to let an agent land work nobody looked at; writing to the operator's file on its
    own authority is the same move with the roles swapped. So there are three ways the answer
    can arrive, and no fourth:

    * `RIG_ALLOW_GITIGNORE=1` — standing consent, given in advance. Write, and say so.
    * stdin is a terminal — ask once (y/N, default no). A bare Enter is a no.
    * anything else — CI, ``claude -p``, a subagent, a piped run — **do not write**. Say which
      line to add and why, in one line. The person who could decide is not here, and a question
      nobody can answer is either a process that hangs or a yes that nobody gave.

    The environment and the console arrive on the `Env` and `Presenter` ports (§3) rather than
    as `os.environ` and `print`, which is what lets a test drive all three branches without a
    subprocess — and what keeps the consent from costing the effect-site ratchet anything.

    Returns whether `.gitignore` was appended.
    """
    if rig_gitignore_status(root) != "missing":
        return False
    wrote, lead = False, ""
    if env.get(GITIGNORE_CONSENT_ENV) == "1":
        wrote, message = True, f"◇ Appended {GITIGNORE_ENTRY} to .gitignore ({GITIGNORE_CONSENT_ENV}=1)"
    elif _stdin_is_a_terminal():
        answered = _answer_from_a_terminal(
            f"◇ Add {GITIGNORE_ENTRY} to .gitignore? {_GITIGNORE_REASON}. [y/N] ")
        lead = "\n" if answered is None else ""
        wrote = answered is True
        message = (f"◇ Appended {GITIGNORE_ENTRY} to .gitignore" if wrote else
                   f"◇ .gitignore left as it is — add {GITIGNORE_ENTRY} yourself whenever you "
                   f"want it ignored, or set {GITIGNORE_CONSENT_ENV}=1 to answer this for good.")
    else:
        message = (f"◇ .gitignore has no {GITIGNORE_ENTRY} entry. Not written: no terminal to "
                   f"ask. Add the line yourself, or set {GITIGNORE_CONSENT_ENV}=1.")
    if wrote:
        append_rig_gitignore(root)
    out.out(lead + message)
    return wrote


def _stdin_is_a_terminal() -> bool:
    """Is there somebody at the other end of stdin to answer a question?

    Every way this can fail is the same answer: a closed, detached or replaced stdin has no
    human behind it, and guessing yes would hang the run on a prompt nobody sees.
    """
    try:
        return sys.stdin is not None and sys.stdin.isatty()
    except (AttributeError, OSError, ValueError):
        return False


_STOPWORDS = {"の", "を", "に", "は", "が", "で", "と", "も", "て", "た", "する", "して", "ください",
              "the", "a", "an", "to", "for", "of", "in", "on", "and", "or", "is", "are"}


def _tokenize(text: str) -> set[str]:
    """Rough tokenization (contiguous alphanumerics, everything else char-by-char). A lightweight
    heuristic that skips bringing in a real morphological analyzer — a hint of overlap is enough,
    exact matching isn't the goal."""
    words = re.findall(r"[A-Za-z0-9_]+|[^\sA-Za-z0-9_]", text.lower())
    return {w for w in words if w not in _STOPWORDS and len(w) > 1}


def find_similar_tasks(root: pathlib.Path, text: str, exclude_task_id: str | None = None,
                       limit: int = 3, threshold: float = 0.25) -> list[dict]:
    """Return past tasks whose `input` is most similar (Jaccard coefficient over rough
    tokenization) to `text`, highest first (#290, deja-vu detection). No dedicated
    embeddings/search engine is brought in — this is just a lightweight scan of task.json."""
    base = runs_dir(root)
    if not base.is_dir():
        return []
    query = _tokenize(text)
    if not query:
        return []
    scored: list[tuple[float, dict]] = []
    for p in base.iterdir():
        tj = p / "task.json"
        if not tj.exists():
            continue
        t = load_json(tj)
        if t["task_id"] == exclude_task_id:
            continue
        candidate = _tokenize(t.get("input", ""))
        if not candidate:
            continue
        overlap = query & candidate
        union = query | candidate
        score = len(overlap) / len(union) if union else 0.0
        if score >= threshold:
            scored.append((score, t))
    scored.sort(key=lambda x: -x[0])
    return [t for _, t in scored[:limit]]


def cmd_new(args: argparse.Namespace) -> None:
    root = repo_root()
    if args.type not in TASK_TYPES:
        die(f"task_type '{args.type}' is invalid. Valid: {', '.join(TASK_TYPES)}")
    context = {
        "recipe": getattr(args, "recipe", None),
        "remote_pr": getattr(args, "remote_pr", False),
        "has_diff": getattr(args, "has_diff", False),
        "diff": getattr(args, "diff", None),
        "read_only": getattr(args, "read_only", False),
        "implementation_type": getattr(args, "implementation_type", None),
    }
    try:
        # Assets are resolved against the tree the caller is standing in, not against the
        # checkout rig keeps state in. Three of the four legacy asset directories
        # (`packs/resolver.py::_legacy_assets`) live under `.claude/`, which is tracked —
        # branch content, so a worktree carrying its own recipe overrides must be the tree
        # that is read. `.rig/gates.json` and `.rig/packs` are the other kind, gitignored
        # install state, and they stay on the state root (#471).
        route = resolve_task_route(args.type, context, invocation_root(), shared=root)
    except PackError as exc:
        die(str(exc))
    if route["status"] in {"stopped", "trust_required"}:
        suffix = f" Hint: {route['hint']}" if route["hint"] else ""
        die(f"route {route['status']}: {route['reason']}.{suffix}")

    slug = args.slug or make_slug(args.input)
    task_id = make_task_id(slug)
    d = runs_dir(root) / task_id
    if d.exists():
        die(f"task '{task_id}' already exists")

    # Compose the gate first: a malformed `.rig/gates.json` must abort here,
    # before any run dir / worktree is created (no partial state on error).
    acc = build_acceptance(task_id, args.type, root)

    # The base is a question about the working tree the operator is standing in, not about
    # where rig keeps its state (#471). Taking it from `root` would fork a task started
    # inside another task's worktree from the main line silently, and — worse — record a
    # branch the operator never mentioned as the base every gate range is measured against.
    here = invocation_root()
    base_branch = args.base or current_branch(here)
    # `--base <branch>` has to mean it. Recording HEAD here while naming another
    # branch as the base made every later range wrong by construction: the diff
    # and the gate sensors are taken against `base_commit`, so a task started
    # from `feature` with `--base master` counted `feature`'s own commits as the
    # task's work. Resolve the requested branch and fork the worktree from that
    # same commit, so the recorded value and the real fork point cannot diverge —
    # which is exactly the invariant `effective_base` (base drift, #312) assumes.
    # Resolved before anything is written, same reason as the gate above.
    if args.base:
        proc = git(["rev-parse", "--verify", f"{args.base}^{{commit}}"], cwd=here, check=False)
        base_commit = proc.stdout.strip()
        if proc.returncode != 0 or not base_commit:
            die(f"--base '{args.base}' does not resolve to a commit")
    else:
        base_commit = git(["rev-parse", "HEAD"], cwd=here).stdout.strip()

    # What this run is against (#548). Resolved here for the same reason `--base` is: a
    # reference rig cannot resolve must fail before a worktree exists, not after. Absent
    # unless declared — see `issue_link` for why one is never read out of the task text.
    try:
        _issue = issue_link.declared(getattr(args, "issue", None))
    except IssueRefError as exc:
        die(str(exc))

    # Insurance against accidental PR contamination — offered, not taken. `ensure_rig_gitignored`
    # prints its own outcome, whichever of the three it is, so nothing is decided here.
    ensure_rig_gitignored(root)

    worktree_path: str | None = None
    branch: str | None = None
    handle: WorktreeHandle | None = None
    package: str | None = None
    create_worktree = route["worktree"] and not args.no_worktree
    if create_worktree:
        # Where the work lives is chosen here and nowhere else; which model does the work
        # is chosen by the provider layer, which this does not consult (#461). The name is
        # passed explicitly as `None` rather than read off `args`: no flag sets it yet, and
        # #462 adds the flag together with the refusal message a bad value deserves.
        backend = runtime_mod.select(getattr(args, "runtime", runtime_mod.NATIVE), root)
        branch = f"rig/{task_id}"
        agent = getattr(args, "agent", None)
        if agent and backend.name != runtime_mod.ORCA:
            # Not ignored: a flag that silently did nothing would leave the operator
            # believing a session exists. Native creates a directory and starts nothing.
            die(f"--agent starts an agent session inside an Orca worktree; the {backend.name} "
                f"runtime starts none. Use --runtime orca, or drop --agent")
        if agent:
            # The package the fresh session receives instead of this session's transcript
            # (#460): composed from the task as registered, fenced like every other task
            # text, and kept with the run so what the agent was told is on the record.
            package = task_package.compose(
                {"task_id": task_id, "input": args.input, "task_type": args.type,
                 "recipe": route["recipe"] or "", "route": route,
                 "base_branch": base_branch, "base_commit": base_commit, "branch": branch},
                criteria=list(route.get("criteria") or []) if isinstance(route.get("criteria"), list) else None)
            handle = backend.create(root, task_id, base_commit, branch, agent=agent,
                                    prompt=package)
        else:
            handle = backend.create(root, task_id, base_commit, branch)
        branch = handle.branch
        worktree_path = handle.path

    task = {
        "task_id": task_id,
        "input": args.input,
        "task_type": args.type,
        "recipe": route["recipe"] or "",
        "recipe_reason": args.reason or route["reason"],
        "route": route,
        "base_branch": base_branch,
        "base_commit": base_commit,
        "branch": branch,
        "worktree_path": worktree_path,
        # The handle beside the path, not instead of it: `worktree_path` is read by
        # accept, the sensors, the receipt and the board, and this change is not the place
        # to move all of them.
        "worktree": handle.as_state() if handle else None,
        "status": "running",
        "created_at": now_iso(),
        "updated_at": now_iso(),
        "budget_minutes": args.budget_minutes,   # optional (#281); None = no warning
    }
    # Attribution (v2). `actor` is what separation of duties compares against at
    # approval time — without it recorded here, "the author's own approval does
    # not count" has nothing to compare. org/team make a run attributable to a
    # team rather than to a directory; all three are absent for unbound repos.
    task["actor"] = govern_identity.current_actor(root)
    # Which harness started this, for the assurance receipt (#428). `actor` is who
    # rig runs as; this is what invoked rig, and the two answer different questions
    # once another agent is the one typing. Recorded with `declared`/`source` intact
    # so the receipt can keep an operator's statement apart from rig's own guess —
    # flattening them is how a heuristic becomes a fact (`rig_workbench/caller.py`).
    _caller = caller.detect(getattr(args, "caller", None))
    task["caller"] = _caller.as_record()
    if getattr(args, "agent", None) and handle is not None:
        task["session"] = {
            "runtime": handle.runtime, "agent": args.agent,
            # Present only when Orca reported one; a missing handle is "not reported",
            # and the way back is `orca terminal list`, which `reconnect` runs.
            **({"terminal": handle.ref["orca_terminal"]} if handle.ref.get("orca_terminal") else {}),
            "package": "handoff-package.md",
        }
    if _issue is not None:
        task["issue"] = _issue
    _binding = govern_identity.load_org_binding(root)
    if _binding.bound:
        task["org"] = _binding.org
        task["team"] = _binding.team
    d.mkdir(parents=True, exist_ok=True)
    save_json(d / "task.json", task)
    if package is not None:
        # What the fresh session was told, kept with the run. A later reader can compare it
        # with what the session did; a package that lived only in Orca's terminal could not.
        (d / "handoff-package.md").write_text(package, encoding="utf-8")
    # Seed the recipe's declared steps so every later view has a denominator. An
    # unreadable recipe seeds nothing and the run behaves exactly as before — the
    # step list is display metadata, never an input to the accept decision.
    seeded = load_recipe_steps(task["recipe"])
    save_json(d / "steps.json", {"steps": seeded, "seeded": bool(seeded)})
    save_json(d / "acceptance.json", acc)

    # ── Selection-rationale banner (Phase 1 §3: code prints this deterministically instead of leaving it to prose) ──
    print("▸ rig")
    print(f"task: {args.input}")
    print(f"detected: {args.type}")
    print(f"recipe: {route['recipe'] or '(stopped)'} — {args.reason or route['reason']}")
    print(f"routing: {route['status']} / capability={route['capability']} / "
          f"tier={route['tier'] or '-'} / pack={route['pack'] or '-'}")
    if route["hint"]:
        print(f"hint: {route['hint']}")
    mode = (
        "isolated worktree" if worktree_path
        else "not isolated (--no-worktree)" if args.no_worktree
        else "not isolated (route policy)"
    )
    print(f"mode: {mode}")
    print(f"gate: {' + '.join(acc['presets'])}")
    print()
    print(f"task_id: {task_id}")
    print(f"base_branch: {base_branch} @ {base_commit[:12]}")
    if worktree_path:
        print(f"worktree: {worktree_path} (branch: {branch})")
    else:
        reason = "--no-worktree specified" if args.no_worktree else "route policy"
        print(f"worktree: none ({reason})")
    print(f"state: {d.relative_to(root)}/")

    for line in render_flow(seeded, acc):
        print(line)

    similar = find_similar_tasks(root, args.input, exclude_task_id=task_id)
    if similar:
        print("\nSimilar tasks (past runs, deja-vu detection #290):")
        for t in similar:
            label = t["input"][:50] + ("…" if len(t["input"]) > 50 else "")
            print(f"  - {t['task_id']} ({t['status']}): {label}")

    if worktree_path:
        # These lines used to open with 「この worktree の中でセッションを開き直す」, which
        # told the operator to do something README §1/§2 promise they do not have to: saying
        # `/rig:go "<task>"` is enough, and the session that said it keeps driving. That is not
        # a promise made only in prose — `facets/instructions/workbench` §③-2 and
        # `patterns/isolated-worktree` §2 are the mechanism: the parent dispatches, and every
        # subagent's working directory is pinned to this path. So the command was contradicting
        # both the documentation and its own harness, and the documentation was the side that
        # was right.
        #
        # What is true about cwd is narrower (#471), and it is about a *second* session, not
        # this run: an agent session is filed under the directory it is started in, so one
        # started at the repository root is filed with every other task's and `--continue`
        # inside the worktree finds nothing. Worth saying while the path is on screen — as the
        # condition it is, not as the next step.
        launcher = _SESSION_LAUNCHER.get(caller.detect(getattr(args, "caller", None)).id)
        target = shlex.quote(worktree_path)
        CONSOLE.out("\nNext: このセッションのまま進む。開き直さなくていい")
        CONSOLE.out("  コードの変更は accept までこの worktree の中だけ。rig の状態は `.rig/` へ")
        CONSOLE.out("  別セッションで開くときは worktree の中で（セッションは cwd に紐づく）:")
        CONSOLE.out(f"    cd {target} && {launcher}" if launcher else f"    cd {target}")


def cmd_step(args: argparse.Namespace) -> None:
    root = repo_root()
    task_id = resolve_task_id(root, args.task_id)
    with task_lock(root, task_id):
        d = run_dir(root, task_id)
        data = load_json(d / "steps.json", {"steps": []})
        for pair in args.set:
            if "=" not in pair:
                die(f"--set must be given as <step>=<status> (got: {pair!r})")
            name, status = pair.split("=", 1)
            if status not in VALID_STEP_STATUS:
                die(f"step status '{status}' is invalid. Valid: {', '.join(VALID_STEP_STATUS)}")
            for step in data["steps"]:
                if step["name"] == name:
                    step["status"] = status
                    step["updated_at"] = now_iso()
                    break
            else:
                data["steps"].append({"name": name, "status": status, "updated_at": now_iso()})
        save_json(d / "steps.json", data)
        progress = progress_from_state(data)
        if progress.known:
            for line in render_transition(progress):
                print(line)
        else:
            # Runs registered before the recipe was seeded have no denominator, so
            # they keep the flat listing rather than getting a fabricated one.
            print(f"{task_id} steps: "
                  + " ".join(f"{s['name']}={s['status']}" for s in data["steps"]))


#: The criteria whose sensor is fail-grade: a finding there is a reason the gate must not
#: pass, so a `--set` that says otherwise is refused loudly instead of quietly overwritten.
#: Warning-grade sensors are deliberately absent. `apply_schema_sensor` downgrades
#: `public_api_changes_documented` to `warning` whenever the API moved and diff.md does not
#: say so (README.md:234) — an annotation, not a verdict — and refusing a whole `gate`
#: invocation over it would turn a note into a blocker. `ja_lint_clean` and
#: `ja_prose_ai_smell_reviewed` are absent for the narrower reason that they are wholly
#: machine-owned: the sensor rewrites them on every evaluation whatever anyone declares.
#:
#: Nothing here is what stops a declaration from beating a sensor — the sensors run after
#: the `--set` loop and write over it, so every sensor-backed criterion carries its own
#: answer whether or not it is in this set. This set only decides which disagreements are
#: worth stopping the operator for.
REFUSABLE_CRITERIA = frozenset({SECRET_CRITERION, TAMPER_CRITERION, INJECTION_CRITERION,
                                DESTRUCTIVE_CRITERION, ANCHOR_CRITERION})


def sensor_contradictions(known: dict[str, dict],
                          declared: dict[str, str]) -> list[tuple[str, str, dict]]:
    """The `REFUSABLE_CRITERIA` this `gate` invocation set by hand that its sensor then
    disagreed with.

    Returns (name, declared status, the check as the sensor left it) for every such
    criterion whose status after the sensor pass is not the one the `--set` pair asked
    for. A criterion no sensor touches keeps exactly what was declared, so it never
    appears here — the declaration-only criteria are untouched by this rule.

    WHY A CONTRADICTED `--set` IS REFUSED RATHER THAN ACCEPTED OR SILENTLY OVERWRITTEN
    (the choice between "agree with the sensor or be refused" and "let --set only record
    a warning-grade entry", made by reading what `gate` and `accept` each own):

    `cmd_gate` is the measurement. It runs the sensors, and the check it writes into
    acceptance.json is what `accept` and the signed provenance record later read back as
    the gate's verdict. `_cmd_accept_locked` is the decision, and it is where a bypass
    already lives and is accountable: `--force` appends an `accept_force` entry naming
    the bypassed criteria to `.rig/audit.jsonl`, needs a live waiver per bypassed
    criterion under a governance policy (`govern_enforce.check_accept(..., force=True)`),
    sets `forced: true` in task.json, and is signed into provenance.json.

    A sensor override at gate time defeats every one of those. Writing `passed` over a
    sensor's `failed` makes `gate_status` "passed", so `accept` sees nothing to force:
    no audit entry, no waiver, `forced: false`, and a provenance record that says the
    criterion passed. The bypass becomes invisible exactly where an audit looks. So the
    escape hatch does not belong here, and the weaker option — letting `--set` record a
    `warning:未確認`-grade entry over a finding — has the same defect in smaller print,
    since `passed_with_warnings` is also a status `accept` lets through unforced.

    Refusing (rather than silently overwriting the declaration with the sensor's answer)
    is what makes the disagreement visible: the operator asked for something the machine
    contradicts, and the gate says so instead of quietly recording one of the two. The
    refusal is a `die`, not a `reject`: `reject`'s exit 1 is reserved for a verdict on
    the work (state.py:28-42), and a script branching on it must not read "you wrote a
    status the sensor contradicts" as "this task's gate failed".
    """
    out: list[tuple[str, str, dict]] = []
    for name in sorted(declared):
        if name not in REFUSABLE_CRITERIA:
            continue
        check = known[name]
        if check["status"] != declared[name]:
            out.append((name, declared[name], check))
    return out


def render_contradictions(task_id: str, contradicted: list[tuple[str, str, dict]]) -> str:
    """The refusal in full: the difference per criterion — what was declared, what the
    sensor measured, and the findings it measured it from (any `*_findings` list a sensor
    recorded) — and where the decision to accept a finding anyway does belong.

    One string rather than printed lines, because the caller hands it to `die`: the whole
    refusal is one message on stderr with one exit code, and the workbench package gains
    no print site of its own for it.
    """
    lines = [f"{task_id}: {len(contradicted)} criterion(s) were set by hand to something the "
             f"sensor backing them contradicts. The sensor's answer is what was recorded."]
    for name, want, check in contradicted:
        lines.append(f"  ✗ {name}: you set '{want}' — the sensor measured '{check['status']}'")
        if check.get("detail"):
            # The detail as the check now holds it — the sensor's, since a sensor that
            # overwrites the status writes its own detail with it.
            lines.append(f"      detail: {check['detail']}")
        for key, value in sorted(check.items()):
            if key.endswith("_findings") and isinstance(value, list):
                lines.extend(f"      {item}" for item in value)
    lines.append("  Remove the finding from the diff, re-run `gate`, and this same --set is then "
                 "accepted because it agrees with the measurement — what the sensor records is the "
                 "finding or its absence, never the pass. If the finding is reviewed and accepted "
                 "anyway, that decision belongs to `accept --force`, which records it in "
                 ".rig/audit.jsonl, requires a waiver under a governance policy, and marks the "
                 "task forced in provenance.json.")
    return "\n".join(lines)


def cmd_gate(args: argparse.Namespace) -> None:
    root = repo_root()
    task_id = resolve_task_id(root, args.task_id)
    with task_lock(root, task_id):
        d, task = load_task(root, task_id)
        acc = load_json(d / "acceptance.json", build_acceptance(task_id, task["task_type"], root))

        ensure_prompt_criterion(root, task, acc)
        # Japanese-prose gate (diff-conditional, like the prompt criterion): a lint
        # criterion the machine owns and a reviewer-verdict criterion it only transcribes.
        with shared_diff_cache():
            ensure_ja_prose_criteria(root, task, acc)

        known = {c["name"]: c for c in acc["checks"]}
        if not known:
            # An empty gate must not read as a gate that was cleared. `gate_status`
            # answers "skipped" for an empty check list, and the transition below then
            # moves the task to `gate_passed` — a pass that nothing was measured for,
            # which `accept` goes on to treat as met (accept.py's `gate_ok`).
            die(f"{task_id}: this task's acceptance gate has no criteria, so there is nothing "
                f"to judge and nothing that can pass. Its acceptance.json holds an empty check "
                f"list; delete that file to rebuild the gate from the presets for task_type "
                f"'{task['task_type']}', or restore the one it was written from.")
        declared: dict[str, str] = {}
        for pair in args.set or []:
            if "=" not in pair:
                die(f"--set must be given as <criterion>=<status>[:detail] (got: {pair!r})")
            name, status = pair.split("=", 1)
            detail = ""
            if ":" in status:
                status, detail = status.split(":", 1)
            if status not in VALID_CRITERION_STATUS:
                die(f"criterion status '{status}' is invalid. Valid: {', '.join(VALID_CRITERION_STATUS)}")
            if name not in known:
                die(f"criterion '{name}' does not exist in this task's gate. Valid: {', '.join(known)}")
            if name == PROMPT_REGRESSION_CRITERION:
                die("prompt_regression_passed is machine-controlled and cannot be set manually")
            known[name]["status"] = status
            # `note` is the operator's own sentence and nothing else writes it: a `--set`
            # that carries a `:DETAIL` records one, and a `--set` that does not removes
            # the one that was there — a fresh declaration with no reason is not still
            # explained by the reason given for the last one. `detail` keeps its old
            # shape for the criteria no sensor touches, where the two say the same thing.
            if detail:
                known[name]["detail"] = detail
                known[name]["note"] = detail
            else:
                # A declaration with no reason leaves none behind. The words underneath
                # explain the status they were written for, and under a new one they
                # explain it wrongly — whether they came from a sensor (`--set
                # no_secret_leak=passed` once read "1 potential secret(s) detected" under a
                # pass) or from this same operator an hour ago on a criterion no sensor
                # touches. "No explanation" is the honest state to leave a status in; a
                # stale one is not. The sensors follow the same rule from their side
                # (state.record_sensor_status).
                known[name].pop("note", None)
                known[name]["detail"] = ""
            # Whose status this now is, recorded rather than left to be guessed from the
            # detail: `--set <criterion>=<status>` with no `:detail` leaves a detail behind
            # that its writer never revised (config.WRITER_OPERATOR).
            known[name]["by"] = WRITER_OPERATOR
            declared[name] = status

        # The sensors below all scan the same worktree diff; shared_diff_cache
        # dedupes their identical git diff / ls-files calls for the duration of
        # this one evaluation (#321 — measured 8 redundant subprocesses without it).
        #
        # None of them is told what was just set by hand: a sensor-backed criterion is
        # the sensor's to answer, and each writes its own verdict over whatever the
        # `--set` loop above put there. What the operator declared is kept in `declared`
        # and compared with the result afterwards — see sensor_contradictions().
        with shared_diff_cache():
            # Machine sensor (issue #288): verify public_api_changes_documented
            # against the actual base↔worktree OpenAPI diff before evaluating.
            sensor_notes = apply_schema_sensor(root, d, task, acc)
            # Machine sensor (issue #273): diff-scoped secret scan backing
            # no_secret_leak. Fail-grade: findings block accept.
            sensor_notes += apply_secret_sensor(root, d, task, acc)
            # Anti-tamper sensor: gate/CI-config edits in the diff are fail-grade,
            # test-weakening patterns warning-grade.
            sensor_notes += apply_tamper_sensor(root, d, task, acc)
            # Injection-marker sensor: invisible Unicode is fail-grade,
            # instruction-override phrases warning-grade.
            sensor_notes += apply_injection_sensor(root, d, task, acc)
            # Destructive-command sensor (#315): unambiguous destroyers (rm -rf /,
            # mkfs, dd of=/dev, DROP DATABASE) are fail-grade, context-dependent
            # patterns and mass deletions warning-grade.
            sensor_notes += apply_destructive_sensor(root, d, task, acc)
            # Evidence-anchor sensor: do the `file.py:42` anchors in this task's
            # recorded reviewer bodies point at lines that exist? Opt-in — the
            # criterion is in no preset, only in `.rig/gates.json` extra_criteria —
            # so this is a no-op on a default gate.
            sensor_notes += apply_anchor_sensor(root, d, task, acc)
            sensor_notes += apply_prompt_regression_sensor(root, task, acc)
            # Japanese-prose sensors: errors on added Japanese lines are fail-grade;
            # the AI-smell criterion is the ai-smell-reviewer's recorded verdict,
            # never a rhythm score.
            sensor_notes += apply_ja_lint_sensor(root, d, task, acc)
            sensor_notes += apply_ja_smell_sensor(root, d, task, acc)

        acc["status"] = gate_status(acc)
        acc["checked_at"] = now_iso()
        save_json(d / "acceptance.json", acc)

        if task["status"] == "running" and acc["status"] in ("passed", "passed_with_warnings", "failed", "skipped"):
            task["status"] = "gate_failed" if acc["status"] == "failed" else "gate_passed"
            save_task(d, task)

        print(f"## acceptance-gate: {task_id}  [{acc['status'].upper()}]")
        print(f"presets: {' + '.join(acc['presets'])}")
        for c in acc["checks"]:
            origin = " [project]" if c.get("origin") == "project" else ""
            detail = f" — {c['detail']}" if c.get("detail") else ""
            # The operator's own sentence rides the same line, when it is not
            # already the detail
            note = (f"\n      note (operator): {c['note']}"
                    if c.get("note") and c["note"] != c.get("detail") else "")
            print(f"  {CHECK_ICON[c['status']]} {c['name']}{origin}{detail}{note}")
        for note in sensor_notes:
            print(note)
        # After the listing, so the operator sees the gate as recorded before being told
        # which of their declarations did not survive it. Everything else in this
        # invocation is applied and saved above — a refused declaration costs the operator
        # the one criterion they were wrong about, not the batch it travelled in, and the
        # sensor's finding is on the record either way.
        contradicted = sensor_contradictions(known, declared)
        if contradicted:
            die(render_contradictions(task_id, contradicted))

        # #497: a reader who has just run a recipe sees a shorter `acceptance:` list in the
        # recipe than the list above and concludes one of the two is wrong. Neither is. The
        # list above is what `accept` requires; a recipe's list is what that flow's own steps
        # produce evidence for, and it is expected to be a subset.
        pending = [c["name"] for c in acc["checks"] if c["status"] == "pending"]
        if pending:
            print(f"\n{len(pending)} criteria still pending: {', '.join(pending)}")
            print("  This list — built by build_acceptance() from the presets, never from a "
                  "recipe — is what `wb accept` requires.")
            print("  A recipe's `acceptance:` is that flow's WORK LIST (the criteria its own "
                  "steps produce evidence for), not the condition for acceptance,")
            print("  so answering it exactly is expected to leave the rest pending. Record "
                  "them with `--set`, or `warning:未確認` when you cannot judge.")
        if acc["status"] == "failed":
            sys.exit(1)


# A --body persona becomes a filename, so it may not carry a path separator or
# start with a dot/dash. --set stays unrestricted (it only ever becomes a JSON key).
REVIEW_BODY_PERSONA_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")


def read_review_body(pair: str) -> tuple[str, str]:
    """Parse one `--body <persona>=@<path>` pair into (persona, text)."""
    if "=" not in pair:
        die(f"--body must be given as <persona>=@<path> (got: {pair!r})")
    persona, ref = pair.split("=", 1)
    if not ref.startswith("@"):
        die(f"--body takes @<path> to a file holding the reviewer text, not inline text (got: {pair!r})")
    if not REVIEW_BODY_PERSONA_RE.match(persona):
        die(f"persona '{persona}' cannot be used as a --body filename "
            "(must start with a letter/digit, then letters/digits/'.'/'_'/':'/'-')")
    try:
        return persona, pathlib.Path(ref[1:]).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        die(f"--body file for persona '{persona}' cannot be read ({ref[1:]}): {exc}")


#: What a hand-off note may name as its subject: a path inside the task's worktree or run
#: directory, relative, with no way to climb out. The subject is an artifact, so an absolute
#: path or `..` is refused rather than stored — a note is rendered on a board and joined on.
_ABOUT_MAX = 200


def _clean_about(values: list[str] | None) -> list[str]:
    cleaned: list[str] = []
    for raw in values or []:
        value = raw.strip()
        if not value or len(value) > _ABOUT_MAX:
            die(f"--about must be a non-empty relative path of at most {_ABOUT_MAX} characters")
        path = pathlib.PurePosixPath(value.replace("\\", "/"))
        if path.is_absolute() or ".." in path.parts or any(c in value for c in "\n\r"):
            die(f"--about must be a relative path inside the task (got {value!r})")
        if value not in cleaned:
            cleaned.append(value)
    return cleaned


def cmd_note(args: argparse.Namespace) -> None:
    """Attach a hand-off note to a run (#548, slice 5).

    Not a chat. A free-form message panel would be a place for unmeasured claims, and it
    would lower the board's information density rather than raise it. What survives a
    session usefully is a note whose *author is a run* and whose *subject is an artifact*:
    "this run changed the pack lock format; a later run touching `pack.lock.json` should read
    `diff.md`". So a note names what it is about, in paths a later run can open, and it is
    filed with the run that wrote it rather than in a stream.

    Append-only. A note is a record of what somebody knew when they wrote it; editing one
    later would let the board show a claim under a timestamp it did not have.
    """
    root = repo_root()
    task_id = resolve_task_id(root, args.task_id)
    text = (args.text or "").strip()
    if not text:
        die("a hand-off note needs text")
    if len(text) > 2000:
        die("a hand-off note is at most 2000 characters; put the rest in a file and --about it")
    about = _clean_about(args.about)
    with task_lock(root, task_id):
        d = run_dir(root, task_id)
        task = load_json(d / "task.json", {})
        data = load_json(d / "handoff.json", {"task_id": task_id, "notes": []})
        entry = {
            "recorded_at": now_iso(),
            "text": text,
            "about": about,
            # Who wrote it, as recorded on the task: a declaration when one was made, an
            # inference otherwise, and absent when rig knows nothing — never a guess here.
            **({"caller": task["caller"]} if isinstance(task.get("caller"), dict) else {}),
        }
        data["notes"].append(entry)
        save_json(d / "handoff.json", data)
    print(f"{task_id} hand-off note #{len(data['notes'])} recorded"
          + (f" (about: {', '.join(about)})" if about else ""))


def cmd_review(args: argparse.Namespace) -> None:
    """Record per-persona verdicts for review tasks (used by stats for rubber-stamp detection).

    The optional `--body <persona>=@<path>` additionally persists the reviewer's full
    text to `.rig/runs/<task_id>/reviews/<persona>.md` — that prose carries the
    `file:line` evidence anchors, which the verdict label alone throws away.
    review.json stays labels-only so none of its readers have to change.
    """
    root = repo_root()
    task_id = resolve_task_id(root, args.task_id)
    # Read every body before taking the lock: an unreadable path must not leave
    # verdicts recorded with their bodies missing.
    bodies = dict(read_review_body(pair) for pair in (args.body or []))
    with task_lock(root, task_id):
        d = run_dir(root, task_id)
        data = load_json(d / "review.json", {"task_id": task_id, "verdicts": []})
        by_persona = {v["persona"]: v for v in data["verdicts"]}
        for pair in args.set:
            if "=" not in pair:
                die(f"--set must be given as <persona>=<APPROVE|REJECT|APPROVE_WITH_CONDITIONS> (got: {pair!r})")
            persona, verdict = pair.split("=", 1)
            if verdict not in VALID_VERDICT:
                die(f"verdict '{verdict}' is invalid. Valid: {', '.join(VALID_VERDICT)}")
            by_persona[persona] = {"persona": persona, "verdict": verdict, "recorded_at": now_iso()}
        # A body without a verdict (here or recorded earlier) is a typo, not a review:
        # keeping it would file evidence under a reviewer who never rendered a judgement.
        for persona in bodies:
            if persona not in by_persona:
                die(f"--body persona '{persona}' has no verdict for this task. "
                    f"Record one with --set {persona}=<{'|'.join(VALID_VERDICT)}>. "
                    f"Known: {', '.join(sorted(by_persona)) or '(none)'}")
        data["verdicts"] = list(by_persona.values())
        save_json(d / "review.json", data)
        for persona, text in bodies.items():
            body_path = d / "reviews" / f"{persona}.md"
            body_path.parent.mkdir(parents=True, exist_ok=True)
            body_path.write_text(text, encoding="utf-8")  # upsert: the body tracks the current verdict
        print(f"{task_id} review verdicts: " + " ".join(f"{v['persona']}={v['verdict']}" for v in data["verdicts"]))
        if bodies:
            print(f"{task_id} review bodies: " + " ".join(f"reviews/{p}.md" for p in sorted(bodies)))
