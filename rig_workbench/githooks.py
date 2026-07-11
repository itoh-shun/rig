"""rig-wb githooks — distribute rig's computational acceptance sensors as git hooks.

Implements itoh-shun/rig#298: the machine-checkable part of the acceptance gate
(build / lint / test / secret-pattern scan) shipped as native `pre-commit` /
`pre-push` hook scripts, so plain human `git commit` / `git push` get the same
computational quality gate as rig-driven changes. AI/LLM criteria (intent
confirmation etc.) are deliberately NOT part of these hooks.

Design:
  - Templates live in `<rig repo>/hooks/git/{pre-commit,pre-push}`.
  - `install` copies them into `.git/hooks/` of the *current* repo (opt-in,
    per issue #298), and refuses to overwrite existing non-rig hooks unless
    `--force` is given — no silent clobbering of a project's own hooks.
  - Installed hooks carry a signature line (`# rig-githooks v1`) near the top
    so `status` / `uninstall` can tell rig-managed hooks from foreign ones,
    and `uninstall` removes only rig-managed hooks.

Usage:
    rig-wb githooks install   [--force] [--repo <path>]
    rig-wb githooks uninstall [--repo <path>]
    rig-wb githooks status    [--repo <path>]
"""

from __future__ import annotations

import pathlib
import shutil
import subprocess
import sys

# Marker written by the templates; presence near the top of an installed hook
# identifies it as rig-managed. Bump the version when the contract changes.
SIGNATURE = "# rig-githooks v1"

# Hooks we ship. Keep in sync with hooks/git/ templates.
HOOK_NAMES = ("pre-commit", "pre-push")

# How many leading lines of an existing hook to inspect for SIGNATURE.
_SIGNATURE_SCAN_LINES = 5


def _templates_dir() -> pathlib.Path:
    """Locate the shipped hook templates (RIG_HOME-aware, same as other subcommands)."""
    from .cli import _rig_home

    d = _rig_home() / "hooks" / "git"
    if not d.is_dir():
        raise RuntimeError(f"hook templates not found: {d}")
    return d


def _hooks_dir(repo: pathlib.Path) -> pathlib.Path:
    """Resolve the target repo's hooks directory via git (worktree/core.hooksPath aware)."""
    proc = subprocess.run(
        ["git", "rev-parse", "--git-path", "hooks"],
        cwd=repo, capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"not a git repository: {repo}")
    p = pathlib.Path(proc.stdout.strip())
    if not p.is_absolute():
        p = (repo / p).resolve()
    return p


def is_rig_hook(path: pathlib.Path) -> bool:
    """True if `path` is a hook installed (and thus owned) by rig-wb githooks."""
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            for _ in range(_SIGNATURE_SCAN_LINES):
                line = fh.readline()
                if not line:
                    break
                if SIGNATURE in line:
                    return True
    except OSError:
        return False
    return False


def hook_state(path: pathlib.Path) -> str:
    """One of 'absent' | 'rig' | 'foreign' for the given hook path."""
    if not path.exists():
        return "absent"
    return "rig" if is_rig_hook(path) else "foreign"


def install(repo: pathlib.Path, force: bool = False) -> int:
    """Copy hook templates into the repo's hooks dir. Returns a process exit code.

    Existing rig-managed hooks are refreshed in place; existing foreign hooks
    are refused (exit 1) unless `force` is set.
    """
    templates = _templates_dir()
    hooks_dir = _hooks_dir(repo)
    hooks_dir.mkdir(parents=True, exist_ok=True)

    refused: list[str] = []
    for name in HOOK_NAMES:
        src = templates / name
        if not src.is_file():
            raise RuntimeError(f"missing hook template: {src}")
        dest = hooks_dir / name
        state = hook_state(dest)
        if state == "foreign" and not force:
            refused.append(name)
            print(f"[REFUSED] {dest} exists and is not a rig hook "
                  f"(use --force to overwrite)", file=sys.stderr)
            continue
        shutil.copyfile(src, dest)
        dest.chmod(dest.stat().st_mode | 0o755)
        verb = {"absent": "installed", "rig": "refreshed", "foreign": "overwrote (--force)"}[state]
        print(f"[OK] {verb} {dest}")

    if refused:
        print(f"[ERROR] {len(refused)} hook(s) not installed: {', '.join(refused)}",
              file=sys.stderr)
        return 1
    print("githooks: computational sensors only (build/lint/test/secret-scan); "
          "no AI criteria run in hooks. Skip with RIG_HOOK_SKIP=1.")
    return 0


def uninstall(repo: pathlib.Path) -> int:
    """Remove rig-managed hooks only; foreign hooks are left untouched."""
    hooks_dir = _hooks_dir(repo)
    for name in HOOK_NAMES:
        dest = hooks_dir / name
        state = hook_state(dest)
        if state == "rig":
            dest.unlink()
            print(f"[OK] removed {dest}")
        elif state == "foreign":
            print(f"[SKIP] {dest} is not a rig hook — left in place")
        else:
            print(f"[SKIP] {dest} absent")
    return 0


def status(repo: pathlib.Path) -> int:
    """Report per-hook state. Exit 0 if all rig hooks installed, 1 otherwise."""
    hooks_dir = _hooks_dir(repo)
    all_rig = True
    print(f"hooks dir: {hooks_dir}")
    for name in HOOK_NAMES:
        dest = hooks_dir / name
        state = hook_state(dest)
        label = {
            "rig": "rig-managed",
            "foreign": "foreign (not rig)",
            "absent": "absent",
        }[state]
        print(f"  {name:12s} {label}")
        if state != "rig":
            all_rig = False
    return 0 if all_rig else 1


def _print_help() -> None:
    print(
        """rig-wb githooks — rig's computational acceptance sensors as native git hooks

Usage:
  rig-wb githooks install   [--force] [--repo <path>]   copy pre-commit/pre-push into .git/hooks/
  rig-wb githooks uninstall [--repo <path>]             remove rig-managed hooks only
  rig-wb githooks status    [--repo <path>]             show per-hook state

Notes:
  - Opt-in per repository; never installed automatically (issue #298).
  - install refuses to overwrite existing non-rig hooks unless --force is given.
  - Hooks run computational checks only (build/lint/test/secret-scan) read from
    the project manifest .claude/rig.md; no AI/LLM criteria.
  - Skip at commit/push time: RIG_HOOK_SKIP=1 (per check: RIG_HOOK_SKIP_LINT,
    RIG_HOOK_SKIP_SECRETS, RIG_HOOK_SKIP_BUILD, RIG_HOOK_SKIP_TEST).
"""
    )


def cmd_githooks(argv: list[str]) -> int:
    """CLI dispatch for `rig-wb githooks <action> [--force] [--repo <path>]`."""
    if not argv or argv[0] in ("-h", "--help", "help"):
        _print_help()
        return 0

    action = argv[0]
    force = False
    repo = pathlib.Path.cwd()
    i = 1
    while i < len(argv):
        if argv[i] == "--force":
            force = True
            i += 1
        elif argv[i] == "--repo" and i + 1 < len(argv):
            repo = pathlib.Path(argv[i + 1]).resolve()
            i += 2
        else:
            print(f"[ERROR] unknown argument: {argv[i]!r}", file=sys.stderr)
            return 2

    try:
        if action == "install":
            return install(repo, force=force)
        if action == "uninstall":
            return uninstall(repo)
        if action == "status":
            return status(repo)
    except RuntimeError as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        return 1

    print(f"[ERROR] unknown githooks action: {action!r} "
          "(expected install|uninstall|status)", file=sys.stderr)
    return 2
