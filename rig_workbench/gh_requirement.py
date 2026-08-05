"""rig's `gh` + `github/gh-stack` probe — advisory, never blocking.

The `gh` binary and the `github/gh-stack` extension are **nice to have, not
required**. This module is the single place that decides whether they are
present, so the CLI wiring, `/rig:setup`, and the tests all agree on one answer.

**Why this is advisory and not a requirement.** rig once made both mandatory on
the theory that it would drive stacked branches through `gh stack`. That was
measured and does not hold: `gh stack` switches branches by checking them out,
and git refuses to check out a branch that another worktree already holds —
which in rig is always, since every task gets its own worktree:

    $ gh stack rebase --no-trunk
    ✗ could not start rebase of task2 onto task1: failed to run git:
      fatal: 'task2' is already used by worktree at '.../wt2'

Worktree isolation is rig's core safety property and is not negotiable, so the
tool cannot perform the operation it was made mandatory for. Plain git does the
same job from inside the child's own worktree. What `gh stack` still buys is the
*publishing* side (declaring a stack, `submit` / `push` for review on GitHub),
which is worth mentioning to someone who does not have it and worth nothing to
someone who never opens a PR — hence: one advisory line, no gate.

**Authentication is not part of the picture either.** `gh stack`'s local
operations work unauthenticated and with no remote at all — only `push` /
`submit` / `sync` touch GitHub. Auth is *reported* by `gh-check` and never
probed on the advisory path, which has no use for the answer.

Exactly one state is reported:

  ok                 the gh binary and gh-stack are both present
  gh-missing         `gh` is not on PATH (or is on PATH but unusable)
  extension-missing  gh is present, but `github/gh-stack` is not installed

Exit-code contract — `rig-wb gh-check` is its only consumer, and it is a
question ("is this environment set up?"), not a gate. Nothing in rig exits with
these codes any more:

  0  ok
  2  CLI usage error (matches the rig-wb convention; never produced by check_gh)
  3  gh-missing
  4  retired — used to mean "not authenticated", which is no longer a failure.
     Left unused rather than reassigned, so a caller that once branched on 4
     simply never sees it instead of silently getting a different meaning.
  5  extension-missing

`RIG_SKIP_GH_CHECK=1` silences the advisory (and skips the probe entirely). It
gates nothing, because nothing is gated. It has no effect on `gh-check` or on
`/rig:setup`: those are explicit requests to be told about the environment, and
silencing an answer someone asked for would be wrong.

Nothing here mutates anything: only `gh --version`, `gh extension list` and
`gh auth status` are ever run, all read-only.
"""

from __future__ import annotations

import dataclasses
import os
import re
import shutil
import subprocess
import sys
from typing import TextIO

# State constants (kept as plain strings: they show up in JSON output and tests).
STATE_OK = "ok"
STATE_GH_MISSING = "gh-missing"
STATE_EXTENSION_MISSING = "extension-missing"

EXIT_CODES = {
    STATE_OK: 0,
    STATE_GH_MISSING: 3,
    STATE_EXTENSION_MISSING: 5,
}

SKIP_ENV = "RIG_SKIP_GH_CHECK"
EXTENSION = "github/gh-stack"

# What authentication is actually for. Repeated wherever auth is reported, so a
# "not authenticated" line can never read as a problem that needs fixing now.
AUTH_NOTE = "only needed for push/submit/sync"

# A hung network must not wedge a rig run, so every probe is bounded; a timeout
# is reported as the failure it is, never as success.
PROBE_TIMEOUT_SECONDS = 20.0

# `gh auth status` is the only probe that talks to github.com. It is skipped
# entirely on the advisory path (which never reports auth, so the answer would
# have no consumer) and gets a much shorter leash than the local probes where it
# does run: an offline `gh-check` gets "not authenticated", the correct answer.
AUTH_PROBE_TIMEOUT_SECONDS = 5.0

_REMEDIES = {
    STATE_GH_MISSING: (
        "Install the GitHub CLI, then add the extension:\n"
        "    macOS:          brew install gh\n"
        "    Debian/Ubuntu:  sudo apt install gh\n"
        "    other:          https://github.com/cli/cli#installation\n"
        "  then:\n"
        "    gh extension install github/gh-stack\n"
        f"  (`gh auth login` is {AUTH_NOTE}, not for this check.)"
    ),
    STATE_EXTENSION_MISSING: (
        "Install the stacked-branch extension:\n"
        "    gh extension install github/gh-stack"
    ),
}

# The two halves of the one-line advisory: what an unmet state costs, and the
# shortest command that ends it. `_REMEDIES` above stays for `gh-check`, which is
# a report and can afford to spell out every platform on its own lines.
_ADVISORY_COST = {
    STATE_GH_MISSING: "the GitHub CLI (`gh`) is not installed",
    STATE_EXTENSION_MISSING: f"`{EXTENSION}` is not installed",
}
_ADVISORY_FIX = {
    STATE_GH_MISSING: ("install gh (https://github.com/cli/cli#installation), "
                       "then `gh extension install github/gh-stack`"),
    STATE_EXTENSION_MISSING: "gh extension install github/gh-stack",
}


@dataclasses.dataclass(frozen=True)
class GhStatus:
    """The outcome of one requirement check.

    `authenticated` / `account` are informational only: they never affect `ok`
    or `exit_code`.
    """

    state: str
    gh_version: str | None = None
    stack_version: str | None = None
    authenticated: bool | None = None
    account: str | None = None
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.state == STATE_OK

    @property
    def exit_code(self) -> int:
        return EXIT_CODES[self.state]

    @property
    def remedy(self) -> str:
        """Exactly what to run to fix this state ('' when there is nothing to fix)."""
        return _REMEDIES.get(self.state, "")

    def auth_summary(self) -> str:
        """How the auth state is reported. Never phrased as an error."""
        if self.authenticated is None:
            return "auth not checked"
        if self.authenticated:
            return f"authenticated as {self.account}" if self.account else "authenticated"
        return f"not authenticated ({AUTH_NOTE})"

    def as_dict(self) -> dict:
        return {
            "state": self.state,
            "ok": self.ok,
            "exit_code": self.exit_code,
            "gh_version": self.gh_version,
            "stack_version": self.stack_version,
            "authenticated": self.authenticated,
            "account": self.account,
            "detail": self.detail,
            "remedy": self.remedy,
        }

    def summary(self) -> str:
        """One-line human summary (the headline of every report)."""
        if self.state == STATE_OK:
            return (f"gh {self.gh_version or '?'} / gh-stack {self.stack_version or '?'} "
                    f"— {self.auth_summary()}")
        if self.state == STATE_GH_MISSING:
            return "the GitHub CLI (`gh`) is not installed"
        return f"gh {self.gh_version or '?'} is installed but `{EXTENSION}` is not"


def _run(argv: list[str], timeout: float = PROBE_TIMEOUT_SECONDS) -> tuple[int, str, str]:
    """Run a read-only gh probe. A missing binary / timeout is a failure, not a crash."""
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        return 127, "", f"{argv[0]} not found"
    except subprocess.TimeoutExpired:
        return 124, "", f"{' '.join(argv)} timed out after {timeout:g}s"
    except OSError as error:  # not executable, permission denied, ...
        return 126, "", f"{argv[0]} could not be executed: {error}"
    return proc.returncode, proc.stdout or "", proc.stderr or ""


def _parse_gh_version(stdout: str) -> str | None:
    """`gh version 2.88.0 (2026-03-10)` -> `2.88.0`."""
    first = stdout.strip().splitlines()[0] if stdout.strip() else ""
    parts = first.split()
    if len(parts) >= 3 and parts[0] == "gh" and parts[1] == "version":
        return parts[2]
    return first or None


def _find_stack_extension(stdout: str) -> str | None:
    """Return gh-stack's version from `gh extension list` output, or None if absent.

    The listing is tab-separated (`gh stack\tgithub/gh-stack\tv0.1.0`), but the
    column layout is gh's to change, so match on the repo slug anywhere in the
    line and take the last version-shaped field.
    """
    for line in stdout.splitlines():
        if "gh-stack" not in line.lower():
            continue
        fields = [f.strip() for f in line.replace("\t", " ").split() if f.strip()]
        for field in reversed(fields):
            if field.startswith("v") or field[:1].isdigit():
                return field
        return "?"
    return None


def _parse_account(text: str) -> str | None:
    """Pull the account name out of `gh auth status` ('... account tester (keyring)')."""
    match = re.search(r"\baccount\s+(\S+)", text)
    if match:
        return match.group(1)
    match = re.search(r"\blogged in to \S+ as (\S+)", text, re.IGNORECASE)
    return match.group(1) if match else None


def _probe_auth() -> tuple[bool, str | None]:
    """Informational auth probe. Never decides pass/fail — see the module docstring."""
    rc, out, err = _run(["gh", "auth", "status"], timeout=AUTH_PROBE_TIMEOUT_SECONDS)
    if rc != 0:
        return False, None
    return True, _parse_account(out or err)


def check_gh(*, probe_auth: bool = True) -> GhStatus:
    """Probe the environment and report exactly one state. Never raises, never mutates.

    `probe_auth=False` drops `gh auth status` — the only probe that talks to
    github.com. The advisory path passes it because it never reports auth: the
    call would have no consumer, and on an offline machine it is a network wait
    for an answer nobody reads. `authenticated` is then None ("not checked").
    """
    if shutil.which("gh") is None:
        return GhStatus(STATE_GH_MISSING, detail="`gh` was not found on PATH")

    rc, out, err = _run(["gh", "--version"])
    if rc != 0:
        # On PATH but unusable (broken install, wrong arch, ...). Treat it as
        # not-installed: the remedy is the same.
        return GhStatus(STATE_GH_MISSING,
                        detail=f"`gh --version` failed (exit {rc}): {(err or out).strip()}")
    gh_version = _parse_gh_version(out)

    # `gh extension list` reads the local extension dir — no auth, no remote.
    # It exits non-zero when nothing is installed, which is a legitimate
    # "extension missing" answer, so only the stdout listing matters.
    _rc, out, _err = _run(["gh", "extension", "list"])
    stack_version = _find_stack_extension(out)

    authenticated, account = _probe_auth() if probe_auth else (None, None)
    if stack_version is None:
        return GhStatus(STATE_EXTENSION_MISSING, gh_version=gh_version,
                        authenticated=authenticated, account=account,
                        detail=f"`{EXTENSION}` was not in `gh extension list`")
    return GhStatus(STATE_OK, gh_version=gh_version, stack_version=stack_version,
                    authenticated=authenticated, account=account)


def silence_requested() -> bool:
    """Whether the advisory is silenced. Any value except an empty string / 0 / false counts."""
    raw = os.environ.get(SKIP_ENV, "").strip().lower()
    return raw not in ("", "0", "false", "no", "off")


def format_report(status: GhStatus) -> str:
    """The multi-line `gh-check` report: what is missing, and exactly what to run.

    This is a report, not a refusal: `gh-check` was asked the question, so it
    answers in full. The one-line version an unasked-for advisory is allowed to
    print is `format_advisory`.
    """
    lines = [f"[INFO] {status.summary()}."]
    if status.detail:
        lines.append(f"       detail: {status.detail}")
    remedy = status.remedy
    if remedy:
        lines.append("       fix:")
        lines.extend(f"         {ln}" if ln.strip() else ln for ln in remedy.splitlines())
    lines.append("       Optional: rig runs without it — `gh stack` only adds "
                 "stacked-PR publishing.")
    return "\n".join(lines)


def format_advisory(status: GhStatus, context: str) -> str:
    """The one line an entry point prints when gh / gh-stack is absent.

    One line, on purpose: it is unsolicited, it names the entry point that
    produced it (it lands in the middle of a run's output), it says plainly that
    nothing is broken, and it names its own off switch so nobody has to search
    for one.
    """
    return (f"[NOTE] {context}: {_ADVISORY_COST[status.state]} — stacked-PR helpers "
            f"(`gh stack`) are unavailable; rig does not need them. "
            f"fix: {_ADVISORY_FIX[status.state]} (silence: {SKIP_ENV}=1)")


def advise_gh(context: str, stream: TextIO | None = None) -> GhStatus | None:
    """Mention the missing GitHub CLI once, for one entry point. Never blocks.

    Returns the status, or None when the advisory is silenced — in which case no
    probe runs at all, so an air-gapped machine pays nothing. A satisfied
    environment stays silent: there is nothing to say.
    """
    if silence_requested():
        return None
    out = stream if stream is not None else sys.stderr
    status = check_gh(probe_auth=False)
    if not status.ok:
        print(format_advisory(status, context), file=out, flush=True)
    return status


def cmd_gh_check(argv: list[str]) -> int:
    """`rig-wb gh-check [--json]` — report the gh / gh-stack state and exit with its code.

    The exit codes are unchanged and stay scriptable; they answer "is this
    environment set up?", and no rig command exits with them.
    """
    as_json = False
    for arg in argv:
        if arg == "--json":
            as_json = True
        elif arg in ("-h", "--help"):
            print("usage: rig-wb gh-check [--json]\n"
                  "  exit 0=ok / 3=gh missing / 5=gh-stack missing / 2=usage error\n"
                  "  gh + gh-stack are optional: rig runs without them (see --json)\n"
                  f"  authentication is reported, never required ({AUTH_NOTE})")
            return 0
        else:
            print(f"[ERROR] gh-check: unknown flag {arg!r}", file=sys.stderr)
            return 2

    status = check_gh()
    if as_json:
        import json

        print(json.dumps(status.as_dict(), ensure_ascii=False, indent=2))
        return status.exit_code

    if status.ok:
        print(f"✓ {status.summary()}")
        return 0
    print(format_report(status), file=sys.stderr)
    if silence_requested():
        print(f"[NOTE] {SKIP_ENV} is set: rig runs stay silent about this.",
              file=sys.stderr)
    return status.exit_code
