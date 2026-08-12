"""rig-wb hostcheck — report the host-side prerequisites rig cannot enforce itself.

Some of rig's safety properties do not live inside rig and never will:

* **Process isolation.** rig's isolated worktree separates *file work* — a failed
  attempt never lands in your tree. It is not a boundary against code execution.
  A container (DevContainer) or VM is what bounds that, and rig runs *inside* it.
* **Runtime command blocking.** rig's `no_destructive_operation` sensor reads the
  commands a diff *writes*. Intercepting the commands a session *runs* is the
  host permission layer's job (`permissions.deny` / `PreToolUse`).

And some are not safety properties at all but *survival* ones: a long autonomous
run dies halfway through for reasons that were already true before it started —
a `gh` token without the scope the run will need, an installed `rig-wb` whose
wheel is missing a subpackage. Both were observed; neither is visible from inside
the run until the run needs them.

Documenting that split is not the same as noticing when it is missing. This
command performs the noticing: it inspects the environment deterministically —
no LLM, no writes — and reports which prerequisites are in place, so "we meant to
containerise it" cannot quietly persist as "we never did".

**Network:** `gh_auth_scopes` runs `gh auth status`, which contacts github.com
(bounded, read-only). It is the only check that leaves the machine, and it is
skipped entirely when the repo has no GitHub remote — or when `git remote -v`
could not be read at all, which is reported rather than assumed. `--bench` never
runs it: the corpus injects the probe output, so measurement stays offline.

**A check that cannot verify its axis reports MISS, not OK.** An unreadable token
scope list, an unresolvable interpreter, an editable install that cannot show a
packaging omission, a `git remote -v` that failed — these are "not verified", and
hostcheck is advisory, so a false MISS costs a line of output while a false OK
costs the run it was supposed to protect. The one exception is *not applicable*:
a check whose subject does not exist here reports `applicable: false` and says so
on its own line, which is not the same as claiming to have looked. "We could not
look" is never spelled `applicable: false` — that would be a claim about the
subject, made by code that never reached it.

**Scopes belong to one token, not to the machine.** `gh auth status` reports every
host it holds a token for, and every account within a host. The run pushes with
exactly one of them — the active account on the host in this repo's remote — so
that is the only stanza `check_gh_auth_scopes` reads. Unioning them all was the
original shape of this code, and it reported a green whenever *some* token on the
machine had `repo`.

Exit codes: 0 = every prerequisite present, 3 = at least one missing (advisory),
and with `--strict`, a missing prerequisite exits 1 so CI can block on it.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import shutil
import subprocess
import tempfile

from . import gh_requirement
from .workbench.injection import bounded_excerpt

# Bounds for the untrusted strings below. `gh auth status` output, an import
# probe's stderr and a console script's shebang are all *outside* text: none of
# it is authored by this repo, and all of it is rendered both to a terminal and
# into `--json`. Every one of them goes through `bounded_excerpt` — the same
# neutralisation the injection scanner applies when it quotes untrusted text back
# (control characters and zero-width/bidi code points become <U+XXXX>, whitespace
# collapses, length is capped). Not a second escaper: a hole closed with the tool
# that already exists for it.
ACCOUNT_MAX = 64      # matches the capture bound in _GH_ACCOUNT_RE
SCOPE_MAX = 40        # the longest real scope, `security_events`, is 15
HOST_MAX = 100        # a host read out of a git remote URL, echoed into a remedy command
MESSAGE_MAX = 200     # probe stderr, import errors, `gh auth status` failure lines
PATH_MAX = 200        # interpreter paths, resolved package files

CONTAINER_ENV_VARS = (
    "REMOTE_CONTAINERS",
    "CODESPACES",
    "DEVCONTAINER",
    "container",
)
CGROUP_MARKERS = ("docker", "containerd", "kubepods", "lxc", "podman")
SETTINGS_CANDIDATES = (
    ".claude/settings.json",
    ".claude/settings.local.json",
)


def _read_json(path: pathlib.Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def host_signals() -> list[str]:
    """Signals that this process is running inside a container, read from the host."""
    signals: list[str] = []
    if pathlib.Path("/.dockerenv").exists():
        signals.append("/.dockerenv")
    if pathlib.Path("/run/.containerenv").exists():
        signals.append("/run/.containerenv")
    try:
        text = pathlib.Path("/proc/1/cgroup").read_text(encoding="utf-8", errors="replace")
    except OSError:
        text = ""
    for marker in CGROUP_MARKERS:
        if marker in text:
            signals.append(f"cgroup:{marker}")
            break
    return signals


def check_isolation(
    root: pathlib.Path, *, env: dict | None = None, signals: list[str] | None = None,
) -> dict:
    """Is this session bounded by something stronger than the file system?

    `env` and `signals` are injectable so the check can be measured against a fixed
    corpus (`--bench`) instead of only against whatever host happens to run the tests.
    """
    environ = os.environ if env is None else env
    signals = list(host_signals() if signals is None else signals)
    for var in CONTAINER_ENV_VARS:
        if environ.get(var):
            signals.append(f"env:{var}")
    declared = [
        str(candidate.relative_to(root))
        for candidate in (
            root / ".devcontainer" / "devcontainer.json",
            root / ".devcontainer.json",
        )
        if candidate.exists()
    ]
    return {
        "id": "process_isolation",
        "ok": bool(signals),
        "signals": signals,
        "declared_config": declared,
        "requirement": "Run rig inside a container/VM. The isolated worktree separates file work, not execution.",
        "remedy": "Add a .devcontainer/devcontainer.json and start the session inside it.",
    }


def check_deny_rules(root: pathlib.Path) -> dict:
    """Does the host permission layer deny anything at all?"""
    found: list[dict] = []
    for rel in SETTINGS_CANDIDATES:
        path = root / rel
        if not path.exists():
            continue
        permissions = _read_json(path).get("permissions")
        deny = permissions.get("deny") if isinstance(permissions, dict) else None
        if isinstance(deny, list) and deny:
            found.append({"path": rel, "rules": len(deny)})
    return {
        "id": "deny_rules",
        "ok": bool(found),
        "sources": found,
        "requirement": "Deletion, production writes and secret output belong in permissions.deny, not in prose.",
        "remedy": 'Add a permissions.deny list to .claude/settings.json (rig\'s diff sensors are the second net, not the first).',
    }


def check_state_ignored(root: pathlib.Path) -> dict:
    """Is rig's run state kept out of version control?"""
    patterns: list[str] = []
    gitignore = root / ".gitignore"
    if gitignore.exists():
        for raw in gitignore.read_text(encoding="utf-8", errors="replace").splitlines():
            line = raw.strip()
            if line.rstrip("/") in {".rig", "/.rig"} or line.startswith(".rig/"):
                # File content, echoed to a terminal: escaped like every other
                # string here that this repo's code did not write.
                patterns.append(bounded_excerpt(line, PATH_MAX))
    return {
        "id": "state_ignored",
        "ok": bool(patterns),
        "patterns": patterns,
        "requirement": "Run state under .rig/ is local execution history, not repository content.",
        "remedy": "Add `.rig/` to .gitignore (`/rig:init` proposes this).",
    }


# ── gh authentication and token scopes ──────────────────────────────────
# Derived from what rig actually shells out to `gh` for, not from what a GitHub
# workflow might plausibly want:
#
#   scripts/rig-action-entrypoint.sh  `gh pr create`      -> repo
#   .github/workflows/release.yml     `gh release create` -> repo
#   scripts/install.sh / action.yml   `gh extension install` -> no auth at all
#
# Interactive sessions read Issues/PRs/CI through the GitHub MCP server
# (`facets/instructions/gh-flow`), not through `gh`, so nothing here needs a
# scope on their behalf. `read:project` is deliberately NOT required: `gh project`
# appears nowhere in this repo, and requiring a scope no rig code path uses would
# make the check fire on setups that are complete. If you drive Projects from
# your own tooling, that requirement is yours to add, not rig's to assume.
GH_REQUIRED_SCOPES = ("repo",)

# A classic token's `repo` implies its narrower siblings; the reverse is false, so
# `public_repo` alone does not satisfy `repo` (it fails on the first private repo,
# which is exactly the mid-run death this check exists to prevent).
GH_SCOPE_GRANTS = {
    "repo": ("public_repo", "repo:status", "repo_deployment", "repo:invite", "security_events"),
}

_GH_SCOPES_RE = re.compile(r"Token scopes:\s*(?P<scopes>.*)")
# Bounded capture: an unbounded `\S+` lets one hostile run of non-whitespace
# become the whole "account name". (The bound is not the escaping — `\S` matches
# ESC and U+200B happily — so the capture also goes through `bounded_excerpt`.)
_GH_ACCOUNT_RE = re.compile(r"\baccount\s+(\S{1,%d})" % ACCOUNT_MAX)
# A host block header: an unindented, bare host name on a line of its own.
# Bounded like every other capture that meets outside text, and anchored so an
# indented `- Token: ...` line can never be mistaken for the start of a new host.
_GH_HOST_HEADER_RE = re.compile(r"^(?P<host>[A-Za-z0-9][A-Za-z0-9.\-]{0,%d})\s*:?\s*$" % HOST_MAX)
# Inside a host block, one stanza per account: `✓ Logged in to github.com account
# tester (keyring)`, followed by `- Active account: true` for the one gh will use.
_GH_LOGGED_IN_RE = re.compile(r"\bLogged in to\b", re.IGNORECASE)
_GH_ACTIVE_ACCOUNT_RE = re.compile(r"Active account:\s*true", re.IGNORECASE)

PROBE_TIMEOUT_SECONDS = 10.0


def _run(argv: list[str], *, cwd: str | None = None, env: dict | None = None,
         timeout: float = PROBE_TIMEOUT_SECONDS) -> tuple[int, str, str]:
    """Run a read-only probe. A missing binary or a timeout is a failure, never a crash.

    `stdin` is closed: a probe that decides to prompt would otherwise inherit the
    session's terminal and hang until the timeout, with the prompt printed into
    whatever was on screen.
    """
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout,
                              cwd=cwd, env=env, stdin=subprocess.DEVNULL)
    except FileNotFoundError:
        return 127, "", f"{argv[0]} not found"
    except subprocess.TimeoutExpired:
        return 124, "", f"{' '.join(argv)} timed out after {timeout:g}s"
    except OSError as error:
        return 126, "", f"{argv[0]} could not be executed: {error}"
    return proc.returncode, proc.stdout or "", proc.stderr or ""


def git_remotes(root: pathlib.Path) -> tuple[int, str, str]:
    """`git remote -v` for this repo, as (returncode, stdout, stderr).

    The return code is part of the answer, not plumbing to be dropped. `git remote
    -v` exits 128 with empty stdout when this is not a git repository (or git is
    unusable), and empty stdout is indistinguishable from "a repository with no
    remotes" — so a caller that reads only stdout turns *we could not look* into
    the positive claim *there is no GitHub remote here*.
    """
    return _run(["git", "-C", str(root), "remote", "-v"])


_REMOTE_URL_RE = re.compile(r"^\S+\s+(\S+)", re.MULTILINE)


def _remote_host(url: str) -> str:
    """The host out of a git remote URL, in either of the two spellings git accepts."""
    if "://" in url:
        url = url.split("://", 1)[1]
    authority = url.split("@")[-1]
    # scp-style `git@host:owner/repo`, URL-style `host:443/owner/repo` and `host/owner/repo`
    return authority.split("/")[0].split(":")[0].lower()


def github_remote_host(remote_text: str) -> str | None:
    """The GitHub host this repo's remotes point at, or None when there is none.

    Matched on the host, not on the whole line: a GitLab repo called `github-tools`
    is not a reason to start reporting `gh` token scopes at someone who never uses
    the GitHub CLI.

    The *name* matters as much as the boolean, because `gh auth status` reports one
    block per host and only the block for this host says anything about the token
    the run will push with. The first match wins when several remotes name
    different GitHub hosts — `git remote -v` lists `origin` first in the common
    case, and a repo pushing to two different GitHub installations has no single
    answer to give here anyway.
    """
    for url in _REMOTE_URL_RE.findall(remote_text):
        host = _remote_host(url)
        if host == "github.com" or host.endswith(".github.com") or host.startswith("github."):
            return host
    return None


def has_github_remote(remote_text: str) -> bool:
    """Does any remote point at github.com (or a GHE host named github.*)?"""
    return github_remote_host(remote_text) is not None


def gh_auth_probe() -> dict:
    """`gh auth status`, delegated to the module that owns gh probing."""
    return gh_requirement.probe_auth_status()


def gh_hosts(output: str) -> list[str]:
    """The hosts `gh auth status` printed a block for, in order."""
    return [host for host, _text in _host_blocks(output) if host is not None]


def _host_header(line: str) -> str | None:
    """`github.com` out of a host block header line, or None if this is not one.

    A dot is required: without it `error:` on a line of its own reads as a host
    called `error`, and one unexpected unindented word would silently start a new
    block and hide the real one. Every host `gh` reports is a domain name.
    """
    match = _GH_HOST_HEADER_RE.match(line)
    if match is None:
        return None
    host = match.group("host").lower()
    return host if "." in host.strip(".") else None


def _host_blocks(output: str) -> list[tuple[str | None, str]]:
    """Split `gh auth status` output into (host, block) pairs.

    `gh` prints one block per host it holds a token for: an unindented line naming
    the host, then indented lines about that host's token. Anything before the
    first such header belongs to no host and comes back under `None`.
    """
    blocks: list[tuple[str | None, list[str]]] = [(None, [])]
    for line in output.splitlines():
        host = _host_header(line)
        if host is not None:
            blocks.append((host, []))
        else:
            blocks[-1][1].append(line)
    return [(host, "\n".join(lines)) for host, lines in blocks]


def _text_for_host(output: str, host: str | None) -> str:
    """The part of `gh auth status` output that describes `host`.

    Three cases, and the middle one is the reason this function exists:

    * `host is None` — the caller has no host in mind (a direct parse of a
      captured snippet). Everything is in scope.
    * the output names hosts — only the matching block is in scope. Scopes granted
      to a *different* host say nothing about the token this run will push with,
      and unioning them is how a github.com token short of `repo` reads as fine
      because some GitHub Enterprise token on the same machine has it.
    * the output names no host at all — one token is described and there is
      nothing to attribute it to but the host we asked about, so it is in scope.
      Real `gh` always prints the header; this keeps short captured fragments
      (and any future format that drops it) meaning what they look like they mean.
    """
    blocks = _host_blocks(output)
    if host is None:
        return "\n".join(text for _host, text in blocks)
    matched = [text for candidate, text in blocks if candidate == host.lower()]
    if matched:
        return "\n".join(matched)
    if all(candidate is None for candidate, _text in blocks):
        return blocks[0][1]
    return ""


def _active_account_text(host_text: str) -> str:
    """The part of a host block describing the account `gh` will actually use.

    One host can hold several accounts, and `gh` prints a stanza per account
    inside the host's block. Only the active one signs the requests, so an
    inactive account's scopes are exactly as irrelevant as another host's — the
    same mistake one level down, and it was live: an active token short of `repo`
    read as fine because a `GITHUB_TOKEN` stanza further down the same block had
    it.

    `gh` marks the active one with `Active account: true`. When it marks exactly
    one, that stanza is the answer. When it marks none, the block describes a
    single account (older `gh`, or a captured fragment) and all of it is. When it
    marks several — which `gh` does not do — the union of the *active* ones is
    still narrower than the whole block, and there is nothing better to pick.
    """
    stanzas: list[list[str]] = []
    for line in host_text.splitlines():
        if _GH_LOGGED_IN_RE.search(line):
            stanzas.append([])
        if stanzas:
            stanzas[-1].append(line)
    if not stanzas:
        return host_text
    active = ["\n".join(lines) for lines in stanzas
              if _GH_ACTIVE_ACCOUNT_RE.search("\n".join(lines))]
    return "\n".join(active) if active else host_text


def parse_token_scopes(output: str, host: str | None = None) -> list[str] | None:
    """Token scopes for `host` from `gh auth status` output — None when it never said.

    None and `[]` are different answers and must stay different: `[]` is a token
    that reported "none" (a classic token with nothing granted), while None is a
    token whose scopes the CLI does not print at all — fine-grained PATs, GitHub
    App installation tokens, `GH_TOKEN` in Actions. Collapsing None into `[]`
    would report "missing repo scope" at a token that may well have the permission
    under a different model; collapsing it into "fine" would green-light a token
    nobody looked at. It gets its own state instead.
    """
    seen: list[str] = []
    found_line = False
    for match in _GH_SCOPES_RE.finditer(_active_account_text(_text_for_host(output, host))):
        found_line = True
        raw = match.group("scopes").strip()
        if raw.lower() in ("", "none"):
            continue
        for field in raw.split(","):
            # Escaped here, at the point the scope list stops being `gh` output
            # and becomes a rig value: real scope names are short ASCII, so this
            # is a no-op on every honest token, and a hostile "scope" that no
            # longer matches `repo` after escaping was never going to satisfy it.
            scope = bounded_excerpt(field.strip().strip("'\""), SCOPE_MAX)
            if scope and scope not in seen:
                seen.append(scope)
    return seen if found_line else None


def _scopes_satisfied(granted: list[str], required: tuple[str, ...]) -> list[str]:
    """Which required scopes the token does not carry (directly or by implication)."""
    effective = set(granted)
    for scope in granted:
        effective.update(GH_SCOPE_GRANTS.get(scope, ()))
    return [scope for scope in required if scope not in effective]


def check_gh_auth_scopes(
    root: pathlib.Path, *, probe: dict | None = None, remotes: str | None = None,
    required: tuple[str, ...] = GH_REQUIRED_SCOPES,
) -> dict:
    """Will `gh` still work when this run reaches the step that needs it?

    A run that opens a PR at the end discovers a missing scope at the end. The
    probe is one bounded read-only call up front. `probe` / `remotes` are
    injectable so `--bench` can measure the parsing against fixed `gh auth status`
    output instead of against whichever token happens to be on the host. An
    injected `remotes` stands for a *successful* `git remote -v`; only the live
    path can fail to read the remotes at all.
    """
    base = {
        "id": "gh_auth_scopes",
        "applicable": True,
        "scopes": [],
        "missing_scopes": [],
        "required_scopes": list(required),
        "account": None,
        "requirement": (
            "`gh` must be authenticated with the scopes rig's GitHub writes need "
            f"({', '.join(required)}): `gh pr create` (the Action entrypoint) and "
            "`gh release create` (the release workflow) fail at the end of a run without them."
        ),
    }
    if remotes is None:
        remote_rc, remote_text, remote_error = git_remotes(root)
    else:
        remote_rc, remote_text, remote_error = 0, remotes, ""
    if remote_rc != 0:
        # Not `no-github-remote`: that is a claim about the repository, and we did
        # not get to look at the repository. An axis we could not read is MISS.
        first_line = (remote_error or "").strip().splitlines()
        return {**base, "ok": False, "state": "remotes-unknown",
                "detail": [f"`git remote -v` exited {remote_rc} — cannot tell whether "
                           "this repo pushes to GitHub",
                           bounded_excerpt(first_line[0], MESSAGE_MAX) if first_line
                           else "(no stderr)"],
                "remedy": "Run hostcheck from inside the git repository rig operates on "
                          "(`--repo <path>`), or fix the git invocation the message above names."}

    host = github_remote_host(remote_text)
    if host is None:
        return {
            "id": "gh_auth_scopes",
            "ok": True,
            "applicable": False,
            "state": "no-github-remote",
            "scopes": [],
            "missing_scopes": [],
            "required_scopes": list(required),
            "account": None,
            "detail": ["no GitHub remote — rig's `gh` writes cannot apply here"],
            "requirement": "`gh` needs the scopes rig's GitHub writes use, but only where GitHub is the remote.",
            "remedy": "",
        }
    # The host comes out of a remote URL in this repo's git config: outside text,
    # rendered to a terminal and pasted into a suggested command.
    shown_host = bounded_excerpt(host, HOST_MAX)

    result = gh_auth_probe() if probe is None else probe
    if not result.get("installed", False):
        return {**base, "ok": False, "state": "gh-missing",
                "detail": ["`gh` is not on PATH"],
                "remedy": "Install the GitHub CLI (https://github.com/cli/cli#installation), "
                          "or ignore this if nothing here pushes to GitHub."}
    if result.get("returncode") == 124:
        # A timeout is not a logout. `gh auth login` is the wrong instruction and
        # the wrong diagnosis: nothing here says anything about the token.
        return {**base, "ok": False, "state": "probe-timed-out",
                "detail": [f"`gh auth status` did not answer within "
                           f"{gh_requirement.AUTH_PROBE_TIMEOUT_SECONDS:g}s — the token was "
                           "not read, which is not the same as it being wrong"],
                "remedy": "Re-run when github.com is reachable, or check scopes by hand "
                          "(`gh auth status`) if this machine is offline on purpose."}
    if result.get("returncode", 1) != 0:
        failure = (result.get("output") or "").strip()
        return {**base, "ok": False, "state": "not-authenticated",
                "detail": [bounded_excerpt(failure.splitlines()[0], MESSAGE_MAX)
                           if failure else "`gh auth status` failed"],
                "remedy": "gh auth login"}

    output = result.get("output") or ""
    hosts = gh_hosts(output)
    if hosts and host not in hosts:
        # `gh` is logged in — somewhere else. The run pushes to `host`, so the
        # tokens it does hold are not the ones that have to carry the scope.
        return {**base, "ok": False, "state": "not-authenticated",
                "detail": [f"`gh` holds no token for {shown_host} (the host this repo's "
                           "remote points at)",
                           "authenticated hosts: " + ", ".join(
                               bounded_excerpt(name, HOST_MAX) for name in hosts)],
                "remedy": f"gh auth login -h {shown_host}"}
    # Everything below reads only this host's block: scopes granted on another
    # host are another token's, and this run will not be using it.
    host_text = _active_account_text(_text_for_host(output, host))
    account_match = _GH_ACCOUNT_RE.search(host_text)
    account = bounded_excerpt(account_match.group(1), ACCOUNT_MAX) if account_match else None
    scopes = parse_token_scopes(output, host)
    if scopes is None:
        return {**base, "ok": False, "state": "scopes-unknown", "account": account,
                "detail": ["authenticated, but `gh auth status` printed no token-scope line "
                           "(fine-grained PAT / GitHub App / GH_TOKEN)"],
                "remedy": "hostcheck cannot read scopes off this token — confirm it grants "
                          "pull-request write on the target repo yourself, or use a classic "
                          "token (`gh auth login`) whose scopes are reportable."}
    missing = _scopes_satisfied(scopes, required)
    if missing:
        return {**base, "ok": False, "state": "scopes-missing", "account": account,
                "scopes": scopes, "missing_scopes": missing,
                "detail": [f"{shown_host} has: {', '.join(scopes) or 'none'}"],
                # `-h <host>`, not a hardcoded github.com: refreshing the wrong
                # host's token is a remedy that changes nothing and reports success.
                "remedy": f"gh auth refresh -h {shown_host} -s {','.join(missing)}"}
    return {**base, "ok": True, "state": "ok", "account": account, "scopes": scopes,
            "detail": [f"{account or 'authenticated'}@{shown_host}: {', '.join(scopes)}"],
            "remedy": ""}


# ── the installed rig_workbench, imported from outside the checkout ─────
# The failure this reproduces: `rig-wb` installed by pipx/uv raised
# `ModuleNotFoundError: No module named 'rig_workbench.workbench'` because the
# wheel shipped the top-level package without its subpackages. Inside the
# checkout it was invisible — the current directory is on `sys.path`, so the
# import resolves against the source tree and every subpackage is there. It only
# appears from a cwd outside the repo with no PYTHONPATH, which is how anyone
# outside this repo runs it. Testing it any other way is why it shipped.
INSTALLED_IMPORT_MODULES = (
    "rig_workbench.workbench",
    "rig_workbench.orchestrate",
    "rig_workbench.govern",
    "rig_workbench.packs",
    "rig_workbench.validation",
    "rig_workbench.eval",
)

_IMPORT_PROBE_CODE = """
import json, sys
out = {"package": None, "errors": {}}
try:
    import rig_workbench
    out["package"] = getattr(rig_workbench, "__file__", None)
except BaseException as exc:
    out["errors"]["rig_workbench"] = "%s: %s" % (type(exc).__name__, exc)
for name in sys.argv[1:]:
    try:
        __import__(name)
    except BaseException as exc:
        out["errors"][name] = "%s: %s" % (type(exc).__name__, exc)
print(json.dumps(out))
"""


def _installed_interpreter() -> tuple[str | None, str]:
    """The python behind the installed `rig-wb`, read from its shebang.

    Not `sys.executable`: hostcheck may well be running from the checkout under
    the system python, which is not the pipx/uv venv the console script uses —
    and the venv is the thing that broke. Naming the interpreter in the result is
    the point; a green whose interpreter nobody can name proves nothing.
    """
    script = shutil.which("rig-wb")
    if script is None:
        return None, "rig-wb not on PATH"
    try:
        with open(script, "r", encoding="utf-8", errors="replace") as handle:
            first = handle.readline().strip()
    except OSError as error:
        return None, f"could not read {script}: {error}"
    if not first.startswith("#!"):
        return None, f"{script} has no shebang (not a python console script?)"
    candidate = first[2:].strip().split()
    # `#!/usr/bin/env python3` -> take the argument, not `env`.
    path = candidate[-1] if candidate and candidate[0].endswith("env") else (
        candidate[0] if candidate else "")
    if not path:
        return None, f"{script} has an empty shebang"
    if not os.path.isabs(path):
        # `#!/usr/bin/env python3` names an interpreter, not a location — which is
        # how uv writes console scripts on some platforms. Treating the bare name
        # as a path made every such install a permanent `interpreter-unknown`
        # MISS: a check that can never pass is not a check. Resolve it the way the
        # shebang itself would, and say in `interpreter_source` that we did, so
        # the answer names the python it actually ran.
        resolved = shutil.which(path)
        if resolved is None:
            return None, f"{script} points at {path}, which is not on PATH"
        return resolved, f"shebang of {script} (`{path}` resolved on PATH)"
    if not os.path.exists(path):
        return None, f"{script} points at {path}, which does not exist"
    return path, f"shebang of {script}"


def installed_import_probe(modules: tuple[str, ...] = INSTALLED_IMPORT_MODULES) -> dict:
    """Import the installed rig_workbench from a cwd outside any checkout, no PYTHONPATH."""
    interpreter, source = _installed_interpreter()
    if interpreter is None:
        return {"installed": shutil.which("rig-wb") is not None,
                "interpreter": None, "interpreter_source": source,
                "returncode": None, "payload": None, "stderr": ""}
    env = {key: value for key, value in os.environ.items()
           if key not in ("PYTHONPATH", "PYTHONHOME")}
    with tempfile.TemporaryDirectory() as tmp:
        # cwd is the whole point: `-c` puts the working directory on sys.path, so
        # running this from the repo would import the checkout and prove nothing.
        rc, out, err = _run([interpreter, "-c", _IMPORT_PROBE_CODE, *modules],
                            cwd=tmp, env=env)
    try:
        payload = json.loads(out)
    except ValueError:
        payload = None
    return {"installed": True, "interpreter": interpreter, "interpreter_source": source,
            "returncode": rc, "payload": payload, "stderr": err.strip()}


def check_installed_import(
    root: pathlib.Path, *, probe: dict | None = None,
    modules: tuple[str, ...] = INSTALLED_IMPORT_MODULES,
) -> dict:
    """Does the *installed* rig-wb import its subpackages away from the source tree?

    `probe` is injectable so `--bench` can measure the verdict against fixed probe
    payloads — including the exact ModuleNotFoundError that shipped — instead of
    against whatever is installed on the host.
    """
    result = installed_import_probe(modules) if probe is None else probe
    # Everything the probe hands back is outside text: the interpreter path comes
    # from a console script's shebang, the payload and stderr from whatever that
    # interpreter chose to print. `interpreter` here is the *rendered* copy — the
    # raw one stays in `result` for the code that has to compare paths.
    interpreter = result.get("interpreter")
    shown_interpreter = (bounded_excerpt(interpreter, PATH_MAX)
                         if isinstance(interpreter, str) else interpreter)
    base = {
        "id": "installed_import",
        "applicable": True,
        "interpreter": shown_interpreter,
        "failed_modules": [],
        "package": None,
        "requirement": (
            "The installed `rig-wb` must import its subpackages from outside a checkout — "
            "a wheel that omits them works in the repo and fails everywhere else."
        ),
    }
    if not result.get("installed", False):
        return {**base, "ok": True, "applicable": False, "state": "not-installed",
                "detail": ["`rig-wb` is not on PATH — this repo runs scripts/*.py directly, "
                           "so there is no installed copy to verify"],
                "remedy": ""}
    if interpreter is None:
        return {**base, "ok": False, "state": "interpreter-unknown",
                "detail": [bounded_excerpt(
                    result.get("interpreter_source") or "interpreter not resolved", MESSAGE_MAX)],
                "remedy": "Resolve it by hand: run `<python> -c 'import rig_workbench.workbench'` "
                          "from a directory outside this repo with PYTHONPATH unset."}
    payload = result.get("payload")
    if payload is None:
        return {**base, "ok": False, "state": "probe-failed",
                "detail": [f"the probe returned {result.get('returncode')} with no JSON",
                           bounded_excerpt(result.get("stderr") or "", MESSAGE_MAX) or "(no stderr)"],
                "remedy": "Run the probe by hand from outside the repo to see what the "
                          "interpreter printed."}
    errors = payload.get("errors") or {}
    raw_package = payload.get("package")
    package = (bounded_excerpt(raw_package, PATH_MAX)
               if isinstance(raw_package, str) else raw_package)
    detail = [f"interpreter: {shown_interpreter}"]
    if package:
        detail.append(f"resolved: {package}")
    if errors:
        # Both halves are the probe's own words — the module names come back from
        # its JSON, not from INSTALLED_IMPORT_MODULES, and the messages are
        # arbitrary exception text.
        shown = sorted((bounded_excerpt(str(name), MESSAGE_MAX),
                        bounded_excerpt(str(message), MESSAGE_MAX))
                       for name, message in errors.items())
        return {**base, "ok": False, "state": "import-failed", "package": package,
                "failed_modules": [name for name, _ in shown],
                "detail": detail + [f"{name}: {message}" for name, message in shown],
                "remedy": "Reinstall the CLI so the wheel matches the source: "
                          "`uv tool install --force rig-workbench` / `pipx reinstall rig-workbench` "
                          "(`/rig:setup` does the same)."}
    if not isinstance(raw_package, str) or not raw_package:
        # The import worked but the probe could not say what file it came from, so
        # there is no way to tell an installed copy from a source tree. Same rule
        # as everywhere else here: not verified is MISS.
        return {**base, "ok": False, "state": "package-path-unknown", "detail": detail + [
            "`rig_workbench` imported but reported no __file__ — hostcheck cannot tell "
            "whether that was a built install or a source tree"],
            "remedy": "Run `<python> -c 'import rig_workbench; print(rig_workbench.__file__)'` "
                      "with the interpreter above and check where it resolves."}
    # The raw path, not the rendered one: this is a filesystem question, and an
    # escaped path would resolve to somewhere that does not exist.
    if not _is_installed_copy(pathlib.Path(raw_package)):
        # An editable install imports from a source tree, where every subpackage
        # is present by construction — the packaging omission this check exists
        # for cannot appear, so the axis has not been verified. That is MISS, the
        # same answer `scopes-unknown` gives a token whose scopes cannot be read;
        # `ok: True` with a caveat in the prose was a green nobody had earned.
        #
        # The old test was `is this path inside the repo root`, which is narrower
        # than the thing it was standing in for: an editable install of a *different*
        # checkout is just as unable to reproduce a packaging bug, and used to pass
        # without even the caveat. "Not under site-packages" is the actual question.
        where = " (this checkout)" if _is_inside(pathlib.Path(raw_package), root) else ""
        return {**base, "ok": False, "state": "editable-install", "package": package,
                "detail": detail + [
                    f"imported from a source tree{where}, not from an install directory: "
                    "a wheel that omits a subpackage still imports fine here, so this "
                    "check has not been run against anything"],
                "remedy": "Verify a built wheel: `uv tool install --force rig-workbench` "
                          "(or `pipx install .` from a build) and re-run hostcheck. "
                          "In a source checkout this axis is expected to be MISS."}
    return {**base, "ok": True, "state": "ok", "package": package, "detail": detail,
            "remedy": ""}


_INSTALL_DIR_NAMES = frozenset({"site-packages", "dist-packages"})


def _is_installed_copy(package: pathlib.Path) -> bool:
    """Does this package path live in an interpreter's install directory?

    Both the literal path and its symlink-resolved form count. Resolving alone
    would call a perfectly ordinary wheel a source tree whenever some parent of
    site-packages happens to be a symlink; not resolving at all would miss a
    package reached through one. The cost of the union is the reverse-symlink
    case — a `site-packages/rig_workbench` symlinked *to* a source tree reads as
    installed — which `pip install -e` has not produced since egg-links, and which
    no bounded check can distinguish from a real install from the outside.
    """
    candidates = [package]
    try:
        candidates.append(package.resolve())
    except OSError:
        pass
    return any(part in _INSTALL_DIR_NAMES
               for candidate in candidates for part in candidate.parts)


def _is_inside(path: pathlib.Path, root: pathlib.Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except (ValueError, OSError):
        return False
    return True


CHECKS = (check_isolation, check_deny_rules, check_state_ignored,
          check_gh_auth_scopes, check_installed_import)


def run_all(root: pathlib.Path) -> dict:
    results = [check(root) for check in CHECKS]
    missing = [r["id"] for r in results if not r["ok"]]
    # Not-applicable is tracked separately from "present": a check whose subject
    # does not exist here has not been satisfied, it has been skipped, and the
    # difference is the whole reason this field exists.
    skipped = [r["id"] for r in results if r.get("applicable") is False]
    return {"root": str(root), "checks": results, "missing": missing,
            "skipped": skipped, "ok": not missing}


def _check_detail(check: dict) -> list | None:
    return (
        check.get("signals")
        or check.get("sources")
        or check.get("patterns")
        or check.get("detail")
    )


def _print_report(result: dict) -> None:
    print("## rig-wb hostcheck — prerequisites rig cannot enforce itself\n")
    for check in result["checks"]:
        mark = "OK  " if check["ok"] else "MISS"
        if check.get("applicable") is False:
            mark = "N/A "
        state = check.get("state")
        print(f"[{mark}] {check['id']}" + (f" ({state})" if state and state != "ok" else ""))
        print(f"       {check['requirement']}")
        detail = _check_detail(check)
        if detail:
            label = "why" if check.get("applicable") is False else (
                "found" if check["ok"] else "detail")
            lines = detail if isinstance(detail, list) else [detail]
            print(f"       {label}: {lines[0]}")
            for line in lines[1:]:
                print(f"       {' ' * len(label)}  {line}")
        if not check["ok"] and check.get("remedy"):
            print(f"       remedy: {check['remedy']}")
        print()
    if result["ok"]:
        print("All host-side prerequisites present.")
    else:
        print(f"Missing: {', '.join(result['missing'])}")
        print("rig still runs — these are the operator's side of the split, and rig only reports them.")
    if result.get("skipped"):
        print(f"Not applicable here (not checked, not satisfied): {', '.join(result['skipped'])}")


# ── fixed corpus ────────────────────────────────────────────────────────
# `--bench` measures the checks the way `sensor-bench` measures the scanners:
# against a fixed set of cases, with no LLM, no billing and no dependence on
# whatever host happens to run it. Positive cases are prerequisites that ARE in
# place and must be reported present; negative cases are absent or *look* present
# without being it — those are where a check earns its keep. A container config
# committed to the repo is the sharpest of them: it says the team intended
# isolation, which is not the same as this session having it.

BenchCase = tuple[str, dict, bool]

ISOLATION_CORPUS: tuple[BenchCase, ...] = (
    ("remote_containers_env", {"env": {"REMOTE_CONTAINERS": "true"}}, True),
    ("devcontainer_env", {"env": {"DEVCONTAINER": "true"}}, True),
    ("docker_marker_file", {"signals": ["/.dockerenv"]}, True),
    ("podman_marker_file", {"signals": ["/run/.containerenv"]}, True),
    ("cgroup_marker", {"signals": ["cgroup:kubepods"]}, True),
    ("declared_but_not_running",
     {"files": {".devcontainer/devcontainer.json": "{}"}}, False),
    ("empty_env_var", {"env": {"CODESPACES": ""}}, False),
    ("bare_host", {}, False),
)

DENY_CORPUS: tuple[BenchCase, ...] = (
    ("deny_rules_present",
     {"files": {".claude/settings.json":
                '{"permissions": {"deny": ["Bash(rm -rf:*)", "Bash(git push --force:*)"]}}'}}, True),
    ("deny_in_local_settings",
     {"files": {".claude/settings.local.json": '{"permissions": {"deny": ["Read(./.env)"]}}'}}, True),
    ("deny_list_empty", {"files": {".claude/settings.json": '{"permissions": {"deny": []}}'}}, False),
    ("allow_only_looks_configured",
     {"files": {".claude/settings.json": '{"permissions": {"allow": ["Bash(npm test:*)"]}}'}}, False),
    ("deny_is_not_a_list",
     {"files": {".claude/settings.json": '{"permissions": {"deny": "Bash(rm -rf:*)"}}'}}, False),
    ("settings_malformed", {"files": {".claude/settings.json": "{not json"}}, False),
    ("no_settings_file", {}, False),
)

IGNORE_CORPUS: tuple[BenchCase, ...] = (
    ("trailing_slash", {"files": {".gitignore": "node_modules/\n.rig/\n"}}, True),
    ("rooted", {"files": {".gitignore": "/.rig\n"}}, True),
    ("bare_name", {"files": {".gitignore": ".rig\n"}}, True),
    ("subdirectory", {"files": {".gitignore": ".rig/runs/\n"}}, True),
    ("similar_prefix", {"files": {".gitignore": ".rigging/\n"}}, False),
    ("different_name", {"files": {".gitignore": "rig/\n"}}, False),
    ("commented_out", {"files": {".gitignore": "# .rig/\n"}}, False),
    ("no_gitignore", {}, False),
)

# `gh auth status` output, captured from the real CLI rather than recalled, so the
# parser is measured against the format it will actually meet. Every case supplies
# a GitHub remote: the corpus measures *scope reading*, and a not-applicable case
# has no ground truth for "detected" — it would inflate recall with a skip. The
# no-remote path is pinned by a unit test on `state` instead.
GH_REMOTE_SAMPLE = ("origin\tgit@github.com:example/repo.git (fetch)\n"
                    "origin\tgit@github.com:example/repo.git (push)\n")

_GH_STATUS_FULL = """github.com
  ✓ Logged in to github.com account tester (/home/tester/.config/gh/hosts.yml)
  - Active account: true
  - Git operations protocol: https
  - Token: gho_************************************
  - Token scopes: 'gist', 'project', 'read:org', 'repo', 'workflow'"""

_GH_STATUS_NO_REPO_SCOPE = """github.com
  ✓ Logged in to github.com account tester (keyring)
  - Active account: true
  - Token: gho_************************************
  - Token scopes: 'gist', 'read:org'"""

_GH_STATUS_PUBLIC_ONLY = """github.com
  ✓ Logged in to github.com account tester (keyring)
  - Token: gho_************************************
  - Token scopes: 'public_repo', 'read:org'"""

_GH_STATUS_FINE_GRAINED = """github.com
  ✓ Logged in to github.com account tester (GH_TOKEN)
  - Active account: true
  - Git operations protocol: https
  - Token: github_pat_***"""

_GH_STATUS_LOGGED_OUT = ("You are not logged into any GitHub hosts. "
                         "To log in, run: gh auth login")

# Multi-host output. `gh auth status` reports *every* host it holds a token for,
# and the scopes of one host say nothing about another: the run pushes to the
# host in the remote, and only that token has to carry `repo`. The corpus needs
# these because every single-host case above passes whether the parser reads one
# host block or blindly unions all of them — the axis was untested, and an
# untested axis is where a check quietly turns green.
_GH_STATUS_ENTERPRISE_HAS_REPO = """github.com
  ✓ Logged in to github.com account tester (keyring)
  - Active account: true
  - Token: gho_************************************
  - Token scopes: 'gist', 'read:org'

ghe.example.com
  ✓ Logged in to ghe.example.com account admin (keyring)
  - Active account: false
  - Token: gho_************************************
  - Token scopes: 'repo', 'workflow'"""

_GH_STATUS_TARGET_HOST_HAS_REPO = """github.com
  ✓ Logged in to github.com account tester (keyring)
  - Active account: true
  - Token: gho_************************************
  - Token scopes: 'read:org', 'repo'

ghe.example.com
  ✓ Logged in to ghe.example.com account admin (keyring)
  - Token: gho_************************************
  - Token scopes: 'gist'"""

# Two accounts on one host — the same mistake one level down. Only the active
# account signs the run's requests; the `GITHUB_TOKEN` stanza below it is not the
# token `gh pr create` will use, so its `repo` is not this run's `repo`.
_GH_STATUS_INACTIVE_ACCOUNT_HAS_REPO = """github.com
  ✓ Logged in to github.com account tester (keyring)
  - Active account: true
  - Git operations protocol: https
  - Token: gho_************************************
  - Token scopes: 'gist', 'read:org'

  ✓ Logged in to github.com account robot (GITHUB_TOKEN)
  - Active account: false
  - Token: ghp_************************************
  - Token scopes: 'repo', 'workflow'"""

_GH_STATUS_ACTIVE_ACCOUNT_HAS_REPO = """github.com
  ✓ Logged in to github.com account robot (GITHUB_TOKEN)
  - Active account: false
  - Token: ghp_************************************
  - Token scopes: 'gist'

  ✓ Logged in to github.com account tester (keyring)
  - Active account: true
  - Token: gho_************************************
  - Token scopes: 'read:org', 'repo'"""

_GH_STATUS_OTHER_HOST_ONLY = """ghe.example.com
  ✓ Logged in to ghe.example.com account admin (keyring)
  - Active account: true
  - Token: gho_************************************
  - Token scopes: 'repo', 'workflow'"""

GH_AUTH_CORPUS: tuple[BenchCase, ...] = (
    ("full_classic_token",
     {"gh": {"installed": True, "returncode": 0, "output": _GH_STATUS_FULL}}, True),
    ("repo_scope_only",
     {"gh": {"installed": True, "returncode": 0,
             "output": "  - Token scopes: 'repo'"}}, True),
    ("repo_grants_public_repo",
     {"gh": {"installed": True, "returncode": 0,
             "output": "  - Token scopes: 'repo'"},
      "required": ("repo", "public_repo")}, True),
    ("missing_repo_scope",
     {"gh": {"installed": True, "returncode": 0, "output": _GH_STATUS_NO_REPO_SCOPE}}, False),
    ("public_repo_is_not_repo",
     {"gh": {"installed": True, "returncode": 0, "output": _GH_STATUS_PUBLIC_ONLY}}, False),
    ("scopes_reported_as_none",
     {"gh": {"installed": True, "returncode": 0,
             "output": "  - Token scopes: none"}}, False),
    # Authenticated but unreadable: the case that decides whether this is a sensor
    # or a rubber stamp. `ok` must be False — "cannot verify" is not "verified".
    ("fine_grained_token_hides_scopes",
     {"gh": {"installed": True, "returncode": 0, "output": _GH_STATUS_FINE_GRAINED}}, False),
    # The three multi-host cases. The first is the one that matters: the remote
    # host is short a scope and a *different* host has it, which a parser that
    # unions every block reports as fine.
    ("another_host_has_the_scope_this_one_lacks",
     {"gh": {"installed": True, "returncode": 0,
             "output": _GH_STATUS_ENTERPRISE_HAS_REPO}}, False),
    ("target_host_has_the_scope_others_do_not",
     {"gh": {"installed": True, "returncode": 0,
             "output": _GH_STATUS_TARGET_HOST_HAS_REPO}}, True),
    ("inactive_account_has_the_scope_the_active_one_lacks",
     {"gh": {"installed": True, "returncode": 0,
             "output": _GH_STATUS_INACTIVE_ACCOUNT_HAS_REPO}}, False),
    ("active_account_has_the_scope_and_is_not_listed_first",
     {"gh": {"installed": True, "returncode": 0,
             "output": _GH_STATUS_ACTIVE_ACCOUNT_HAS_REPO}}, True),
    ("logged_in_elsewhere_but_not_to_the_remote_host",
     {"gh": {"installed": True, "returncode": 0,
             "output": _GH_STATUS_OTHER_HOST_ONLY}}, False),
    ("logged_out",
     {"gh": {"installed": True, "returncode": 1, "output": _GH_STATUS_LOGGED_OUT}}, False),
    ("probe_timed_out",
     {"gh": {"installed": True, "returncode": 124, "output": "gh auth status timed out"}}, False),
    ("gh_not_installed",
     {"gh": {"installed": False, "returncode": 127, "output": ""}}, False),
)

# The negative cases here are the shipped bug (a wheel without the subpackage) and
# every way the probe can fail to answer. `not-installed` is not in the corpus for
# the same reason the no-remote case is not: it is a skip, not a detection.
_IMPORT_PROBE_OK = {
    "installed": True, "returncode": 0, "stderr": "",
    "interpreter": "/home/tester/.local/share/uv/tools/rig-workbench/bin/python",
    "interpreter_source": "shebang of /home/tester/.local/bin/rig-wb",
    "payload": {"package": "/home/tester/.local/share/uv/tools/rig-workbench/lib/"
                           "python3.13/site-packages/rig_workbench/__init__.py",
                "errors": {}},
}

INSTALLED_IMPORT_CORPUS: tuple[BenchCase, ...] = (
    ("installed_and_importable", {"probe": _IMPORT_PROBE_OK}, True),
    # The actual incident: top-level package present, subpackage absent from the wheel.
    ("wheel_missing_subpackage",
     {"probe": {**_IMPORT_PROBE_OK,
                "payload": {"package": _IMPORT_PROBE_OK["payload"]["package"],
                            "errors": {"rig_workbench.workbench":
                                       "ModuleNotFoundError: No module named "
                                       "'rig_workbench.workbench'"}}}}, False),
    ("package_itself_missing",
     {"probe": {**_IMPORT_PROBE_OK,
                "payload": {"package": None,
                            "errors": {"rig_workbench":
                                       "ModuleNotFoundError: No module named 'rig_workbench'"}}}},
     False),
    ("subpackage_imports_but_raises",
     {"probe": {**_IMPORT_PROBE_OK,
                "payload": {"package": _IMPORT_PROBE_OK["payload"]["package"],
                            "errors": {"rig_workbench.govern":
                                       "ImportError: cannot import name 'Policy'"}}}}, False),
    ("interpreter_not_resolvable",
     {"probe": {"installed": True, "interpreter": None,
                "interpreter_source": "/usr/local/bin/rig-wb has no shebang",
                "returncode": None, "payload": None, "stderr": ""}}, False),
    ("probe_printed_nothing_parseable",
     {"probe": {**_IMPORT_PROBE_OK, "returncode": 1, "payload": None,
                "stderr": "Segmentation fault"}}, False),
    # An editable install imports from a source tree, so every subpackage is
    # present by construction and the packaging omission this check exists for
    # cannot appear. That is "not verified", which is MISS — the same answer the
    # gh check gives a token whose scopes it cannot read. Both spellings are
    # here, because the earlier version only noticed the first one: an editable
    # install of *this* checkout, and an editable install of some other one.
    ("editable_install_of_this_checkout",
     {"probe": {**_IMPORT_PROBE_OK,
                "payload": {"package": "{root}/rig_workbench/__init__.py", "errors": {}}}},
     False),
    ("editable_install_of_another_checkout",
     {"probe": {**_IMPORT_PROBE_OK,
                "payload": {"package": "/home/tester/src/rig/rig_workbench/__init__.py",
                            "errors": {}}}}, False),
    ("import_worked_but_named_no_file",
     {"probe": {**_IMPORT_PROBE_OK,
                "payload": {"package": None, "errors": {}}}}, False),
)

BENCH_CORPORA = {
    "process_isolation": (check_isolation, ISOLATION_CORPUS),
    "deny_rules": (check_deny_rules, DENY_CORPUS),
    "state_ignored": (check_state_ignored, IGNORE_CORPUS),
    "gh_auth_scopes": (check_gh_auth_scopes, GH_AUTH_CORPUS),
    "installed_import": (check_installed_import, INSTALLED_IMPORT_CORPUS),
}


def _materialise(root: pathlib.Path, files: dict) -> None:
    for rel, content in files.items():
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")


def _required_probe(spec: dict, key: str, label: str) -> dict:
    """The case's own probe output, or a refusal to run the case at all.

    `check_*(probe=None)` means "go ask the host", which is precisely what
    `--bench` promises it never does — a case missing its key would silently start
    reading the operator's real `gh` token while the header still says "no
    network". A corpus case with no recorded output has no ground truth either, so
    there is nothing to measure: fail loudly instead of measuring the host.
    """
    probe = spec.get(key)
    if not isinstance(probe, dict):
        raise ValueError(
            f"bench case {label!r}: no {key!r} probe output. --bench cases must carry their "
            "own probe result; passing None would fall back to probing the live host.")
    return probe


def _bind_probe_root(probe: dict, workdir: pathlib.Path) -> dict:
    """Expand `{root}` in a corpus probe's package path to the case's own workdir.

    One case needs a package path that really is inside the repo root the check is
    given (an editable install of *this* checkout), and the root is a temporary
    directory that does not exist when the corpus is written. Nothing else in the
    corpus is templated: this is a path, not a general substitution mechanism.
    """
    payload = probe.get("payload")
    if not isinstance(payload, dict) or not isinstance(payload.get("package"), str):
        return probe
    package = payload["package"]
    if "{root}" not in package:
        return probe
    return {**probe, "payload": {**payload,
                                 "package": package.replace("{root}", str(workdir))}}


def run_case(check, case: BenchCase, workdir: pathlib.Path) -> dict:
    label, spec, expect_ok = case
    _materialise(workdir, spec.get("files", {}))
    kwargs = {}
    if check is check_isolation:
        # Isolation reads the host; the corpus supplies both inputs explicitly so a
        # case means the same thing inside a container and on a laptop.
        kwargs = {"env": spec.get("env", {}), "signals": spec.get("signals", [])}
    elif check is check_gh_auth_scopes:
        # Same principle for the two probes: the case carries its own `gh auth
        # status` output and its own remote, so no case reaches the network and
        # none of them means something different on a machine with a better token.
        # `remotes` defaults to a GitHub remote so a case that forgets it measures
        # scope parsing rather than silently becoming a not-applicable skip.
        kwargs = {"probe": _required_probe(spec, "gh", label),
                  "remotes": spec.get("remotes", GH_REMOTE_SAMPLE)}
        if "required" in spec:
            kwargs["required"] = spec["required"]
    elif check is check_installed_import:
        kwargs = {"probe": _bind_probe_root(_required_probe(spec, "probe", label), workdir)}
    result = check(workdir, **kwargs)
    return {"label": label, "expect_ok": expect_ok, "ok": result["ok"],
            "correct": result["ok"] == expect_ok}


def run_bench() -> dict:
    checks: dict[str, dict] = {}
    with tempfile.TemporaryDirectory() as tmp:
        base = pathlib.Path(tmp)
        for name, (check, corpus) in BENCH_CORPORA.items():
            cases = []
            for index, case in enumerate(corpus):
                workdir = base / f"{name}-{index}"
                workdir.mkdir(parents=True, exist_ok=True)
                cases.append(run_case(check, case, workdir))
            positives = [c for c in cases if c["expect_ok"]]
            negatives = [c for c in cases if not c["expect_ok"]]
            detected = sum(1 for c in positives if c["correct"])
            false_positives = sum(1 for c in negatives if not c["correct"])
            checks[name] = {
                "cases": cases,
                "positives": len(positives), "detected": detected,
                "recall": round(detected / len(positives), 3) if positives else None,
                "negatives": len(negatives), "false_positives": false_positives,
                "false_positive_rate": (round(false_positives / len(negatives), 3)
                                        if negatives else None),
            }
    total_pos = sum(c["positives"] for c in checks.values())
    total_det = sum(c["detected"] for c in checks.values())
    total_neg = sum(c["negatives"] for c in checks.values())
    total_fp = sum(c["false_positives"] for c in checks.values())
    return {
        "checks": checks,
        "overall": {
            "positives": total_pos, "detected": total_det,
            "recall": round(total_det / total_pos, 3) if total_pos else None,
            "negatives": total_neg, "false_positives": total_fp,
            "false_positive_rate": round(total_fp / total_neg, 3) if total_neg else None,
        },
        "ok": total_det == total_pos and total_fp == 0,
    }


def _print_bench(result: dict) -> None:
    print("## rig-wb hostcheck --bench — detection rate on a fixed corpus\n")
    print("No LLM, no billing, no network, no dependence on the host running it: every case\n"
          "supplies its own environment, including the `gh auth status` output and the import\n"
          "probe result. Negative cases include configurations that look like the prerequisite\n"
          "without being it — a committed devcontainer.json with no container around the\n"
          "session, an allow-list with no deny rules, a commented-out ignore, a token whose\n"
          "scopes cannot be read, a token whose `repo` scope belongs to a different host than\n"
          "the remote, an install that imports from a source tree instead of a built wheel.\n")
    for name, data in result["checks"].items():
        recall = f"{data['detected']}/{data['positives']}"
        print(f"### {name}")
        print(f"  detected: {recall}" + (f" ({data['recall'] * 100:.0f}%)" if data["recall"] is not None else ""))
        print(f"  false positives: {data['false_positives']}/{data['negatives']}")
        for case in data["cases"]:
            mark = "OK" if case["correct"] else "MISS"
            print(f"    [{mark}] {case['label']:<28} expect_ok={case['expect_ok']!s:<5} ok={case['ok']}")
        print()
    o = result["overall"]
    print(f"## overall: detected {o['detected']}/{o['positives']}, "
          f"false positives {o['false_positives']}/{o['negatives']}")


def cmd_hostcheck(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="rig-wb hostcheck",
        description=("Report the host-side prerequisites rig cannot enforce (container isolation, "
                     "deny rules, ignored run state, gh auth scopes, installed-package imports)."),
    )
    parser.add_argument("--repo", default=".", help="repository root to inspect (default: cwd)")
    parser.add_argument("--json", action="store_true", help="emit the full result as JSON")
    parser.add_argument("--strict", action="store_true", help="exit 1 instead of 3 when a prerequisite is missing")
    parser.add_argument("--bench", action="store_true",
                        help="measure the checks against a fixed corpus instead of inspecting this repo")
    args = parser.parse_args(argv)

    if args.bench:
        bench = run_bench()
        if args.json:
            print(json.dumps(bench, ensure_ascii=False, indent=2, sort_keys=True))
        else:
            _print_bench(bench)
        return 0 if bench["ok"] else 1

    root = pathlib.Path(args.repo).resolve()
    result = run_all(root)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        _print_report(result)
    if result["ok"]:
        return 0
    return 1 if args.strict else 3
