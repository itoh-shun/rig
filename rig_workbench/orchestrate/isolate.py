"""orchestrate isolate: worktree isolation (split from scripts/orchestrate.py)."""

import re
import pathlib

from ..ports import Clock, ProcessRunner
from ..ports.local import SUBPROCESS, SYSTEM_CLOCK
from . import config

# ── Isolated worktree runs (--isolate) ───────────────────────────────────────
# Isolate the run in a disposable git worktree: never dirty the working tree, and
# ff-merge only gate-green results into the original branch (unmet/dirty/non-ff
# runs keep the branch for a human). The spatial version of determinism-by-gate:
# non-deterministic generation never escapes the gate.

_ISO_SEQ = 0


def setup_isolation(recipe_name: str, *, clock: Clock = SYSTEM_CLOCK,
                    proc: ProcessRunner = SUBPROCESS) -> dict:
    r = proc.run(["git", "rev-parse", "--show-toplevel"], cwd=str(config.INVOCATION_CWD))
    if r.returncode != 0:
        raise SystemExit("[ERROR] --isolate can only be used inside a git repository")
    root = r.stdout.strip()
    ts = clock.now().strftime("%Y%m%d-%H%M%S")
    safe = re.sub(r"[^a-zA-Z0-9_-]", "-", recipe_name)
    # Add a sequence number so back-to-back runs within the same second do not collide (in-process counter + avoids existing branches)
    global _ISO_SEQ
    _ISO_SEQ += 1
    name = f"{safe}-{ts}-{_ISO_SEQ}"
    branch = f"rig/run-{name}"
    wdir = pathlib.Path(root) / ".rig" / "worktrees" / name
    wdir.parent.mkdir(parents=True, exist_ok=True)
    a = proc.run(["git", "-C", root, "worktree", "add", "-b", branch, str(wdir), "HEAD"])
    if a.returncode != 0:
        raise SystemExit(f"[ERROR] failed to create worktree: {a.stderr.strip()[:200]}")
    return {"root": root, "dir": str(wdir), "branch": branch}


def teardown_isolation(iso: dict, final: str, *, proc: ProcessRunner = SUBPROCESS) -> str:
    """Clean up the worktree according to the final state and return a result label (pure-function style; the only side effects are git).

    DONE and clean with commits    -> ff-merge into the original branch and remove (merged)
    DONE and clean with no commits -> remove only (clean-removed)
    Anything else (unmet / dirty / dirty root / non-ff) -> keep the worktree and branch (kept)
    """
    root, wdir, branch = iso["root"], iso["dir"], iso["branch"]
    dirty = proc.run(["git", "-C", wdir, "status", "--porcelain"]).stdout.strip()
    ahead = proc.run(["git", "-C", root, "rev-list", "--count",
                      f"HEAD..{branch}"]).stdout.strip() or "0"
    root_dirty = proc.run(["git", "-C", root, "status", "--porcelain",
                           "--untracked-files=no"]).stdout.strip()

    def _remove(delete_branch: bool) -> None:
        proc.run(["git", "-C", root, "worktree", "remove", "--force", wdir])
        if delete_branch:
            proc.run(["git", "-C", root, "branch", "-D", branch])

    if final == "DONE" and not dirty:
        if ahead == "0":
            _remove(delete_branch=True)
            return "clean-removed"
        if not root_dirty:
            m = proc.run(["git", "-C", root, "merge", "--ff-only", branch])
            if m.returncode == 0:
                _remove(delete_branch=True)
                return "merged"
    return "kept"

