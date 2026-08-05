"""rig's `gh` + `github/gh-stack` requirement check.

rig's work-producing flows drive stacked branches through the GitHub CLI, so the
`gh` binary and the `github/gh-stack` extension are **requirements, not extras**.
This module is the single place that decides whether the requirement is met, so
the CLI wiring, `/rig:setup`, and the tests all agree on one answer.

**Authentication is deliberately not part of the requirement.** `gh stack`'s
local operations (`init`, `add`, `rebase --no-trunk`) work unauthenticated and
with no remote at all — only `push` / `submit` / `sync` touch GitHub. rig
supports fully local, offline, no-remote use where work lands in the working
tree via `accept` and no PR ever exists, so auth is *reported* here and never
enforced: it becomes a requirement at the point a remote operation is attempted.

Exactly one state is reported:

  ok                 the gh binary and gh-stack are both present
  gh-missing         `gh` is not on PATH (or is on PATH but unusable)
  extension-missing  gh is present, but `github/gh-stack` is not installed

Exit-code contract (0 = fine, anything else = not fine; callers may branch on
the specific code, but must treat "not 0" as blocking):

  0  ok
  2  CLI usage error (matches the rig-wb convention; never produced by check_gh)
  3  gh-missing
  4  retired — used to mean "not authenticated", which is no longer a failure.
     Left unused rather than reassigned, so a caller that once branched on 4
     simply never sees it instead of silently getting a different meaning.
  5  extension-missing

Escape hatch: `RIG_SKIP_GH_CHECK=1` turns the block into a warning. It is loud
every single time (a stderr banner naming the state it bypassed), never silent.

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

# `gh auth status` is the only probe that talks to github.com, and it now runs on
# every gated command for a field that cannot change the verdict. It gets a much
# shorter leash than the local probes: an offline machine pays this once per
# gated command and gets "not authenticated", which is the correct answer anyway.
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


def check_gh() -> GhStatus:
    """Probe the environment and report exactly one state. Never raises, never mutates."""
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

    authenticated, account = _probe_auth()
    if stack_version is None:
        return GhStatus(STATE_EXTENSION_MISSING, gh_version=gh_version,
                        authenticated=authenticated, account=account,
                        detail=f"`{EXTENSION}` was not in `gh extension list`")
    return GhStatus(STATE_OK, gh_version=gh_version, stack_version=stack_version,
                    authenticated=authenticated, account=account)


def skip_requested() -> bool:
    """Whether the escape hatch is set. Any value except an empty string / 0 / false counts."""
    raw = os.environ.get(SKIP_ENV, "").strip().lower()
    return raw not in ("", "0", "false", "no", "off")


def format_failure(status: GhStatus, context: str) -> str:
    """The blocking message: what is wrong, where it blocked, and exactly what to run."""
    lines = [
        f"[ERROR] rig requires the GitHub CLI and the `{EXTENSION}` extension: {status.summary()}.",
        f"        blocked: {context}",
    ]
    if status.detail:
        lines.append(f"        detail:  {status.detail}")
    remedy = status.remedy
    if remedy:
        lines.append("        fix:")
        lines.extend(f"          {ln}" if ln.strip() else ln for ln in remedy.splitlines())
    lines.append(
        f"        If this environment genuinely cannot have it (air-gapped CI), set "
        f"{SKIP_ENV}=1 to proceed at your own risk."
    )
    return "\n".join(lines)


def format_skip_warning(status: GhStatus, context: str) -> str:
    """The escape-hatch banner. Printed on every single bypass — never once-per-session."""
    lines = [
        f"[WARN] {SKIP_ENV} is set: proceeding without the GitHub CLI requirement.",
        f"       state:   {status.state} — {status.summary()}",
        f"       context: {context}",
        "       rig's stacked-branch flow will not work here; anything that stacks,",
        "       cascades, or pushes will fail later instead of now.",
    ]
    remedy = status.remedy
    if remedy:
        lines.append("       fix:")
        lines.extend(f"         {ln}" if ln.strip() else ln for ln in remedy.splitlines())
    return "\n".join(lines)


def require_gh(context: str, stream: TextIO | None = None) -> GhStatus:
    """Enforce the requirement for a work-producing entry point.

    Returns the status when the requirement is met — including when gh is not
    authenticated, which is reported but never blocks. Otherwise it either exits
    with the state's code (see the module docstring) or — when
    `RIG_SKIP_GH_CHECK` is set — prints a loud warning and returns the failing
    status so the caller can proceed.

    The escape hatch covers both failing states (`gh-missing`,
    `extension-missing`); there is nothing auth-related for it to bypass, since
    auth is not part of the requirement.
    """
    out = stream if stream is not None else sys.stderr
    status = check_gh()
    if status.ok:
        return status
    if skip_requested():
        print(format_skip_warning(status, context), file=out, flush=True)
        return status
    print(format_failure(status, context), file=out, flush=True)
    raise SystemExit(status.exit_code)


def cmd_gh_check(argv: list[str]) -> int:
    """`rig-wb gh-check [--json]` — report the requirement state and exit with its code."""
    as_json = False
    for arg in argv:
        if arg == "--json":
            as_json = True
        elif arg in ("-h", "--help"):
            print("usage: rig-wb gh-check [--json]\n"
                  "  exit 0=ok / 3=gh missing / 5=gh-stack missing / 2=usage error\n"
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
    print(format_failure(status, "rig-wb gh-check"), file=sys.stderr)
    if skip_requested():
        print(f"[WARN] {SKIP_ENV} is set: rig runs would proceed anyway (loudly).",
              file=sys.stderr)
    return status.exit_code
