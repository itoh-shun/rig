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
skipped entirely when the repo has no GitHub remote. `--bench` never runs it: the
corpus injects the probe output, so measurement stays offline.

**A check that cannot verify its axis reports MISS, not OK.** An unreadable token
scope list, an unresolvable interpreter — these are "not verified", and hostcheck
is advisory, so a false MISS costs a line of output while a false OK costs the
run it was supposed to protect. The one exception is *not applicable*: a check
whose subject does not exist here reports `applicable: false` and says so on its
own line, which is not the same as claiming to have looked.

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
                patterns.append(line)
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
_GH_ACCOUNT_RE = re.compile(r"\baccount\s+(\S+)")

PROBE_TIMEOUT_SECONDS = 10.0


def _run(argv: list[str], *, cwd: str | None = None, env: dict | None = None,
         timeout: float = PROBE_TIMEOUT_SECONDS) -> tuple[int, str, str]:
    """Run a read-only probe. A missing binary or a timeout is a failure, never a crash."""
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout,
                              cwd=cwd, env=env)
    except FileNotFoundError:
        return 127, "", f"{argv[0]} not found"
    except subprocess.TimeoutExpired:
        return 124, "", f"{' '.join(argv)} timed out after {timeout:g}s"
    except OSError as error:
        return 126, "", f"{argv[0]} could not be executed: {error}"
    return proc.returncode, proc.stdout or "", proc.stderr or ""


def git_remotes(root: pathlib.Path) -> str:
    """`git remote -v` for this repo, or '' when git/the repo is unavailable."""
    _rc, out, _err = _run(["git", "-C", str(root), "remote", "-v"])
    return out


_REMOTE_URL_RE = re.compile(r"^\S+\s+(\S+)", re.MULTILINE)


def _remote_host(url: str) -> str:
    """The host out of a git remote URL, in either of the two spellings git accepts."""
    if "://" in url:
        url = url.split("://", 1)[1]
    authority = url.split("@")[-1]
    # scp-style `git@host:owner/repo`, URL-style `host:443/owner/repo` and `host/owner/repo`
    return authority.split("/")[0].split(":")[0].lower()


def has_github_remote(remote_text: str) -> bool:
    """Does any remote point at github.com (or a GHE host named github.*)?

    Matched on the host, not on the whole line: a GitLab repo called `github-tools`
    is not a reason to start reporting `gh` token scopes at someone who never uses
    the GitHub CLI.
    """
    for url in _REMOTE_URL_RE.findall(remote_text):
        host = _remote_host(url)
        if host == "github.com" or host.endswith(".github.com") or host.startswith("github."):
            return True
    return False


def gh_auth_probe() -> dict:
    """`gh auth status`, delegated to the module that owns gh probing."""
    return gh_requirement.probe_auth_status()


def parse_token_scopes(output: str) -> list[str] | None:
    """Token scopes from `gh auth status` output — None when it never said.

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
    for match in _GH_SCOPES_RE.finditer(output):
        found_line = True
        raw = match.group("scopes").strip()
        if raw.lower() in ("", "none"):
            continue
        for field in raw.split(","):
            scope = field.strip().strip("'\"")
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
    output instead of against whichever token happens to be on the host.
    """
    remote_text = git_remotes(root) if remotes is None else remotes
    if not has_github_remote(remote_text):
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

    result = gh_auth_probe() if probe is None else probe
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
    if not result.get("installed", False):
        return {**base, "ok": False, "state": "gh-missing",
                "detail": ["`gh` is not on PATH"],
                "remedy": "Install the GitHub CLI (https://github.com/cli/cli#installation), "
                          "or ignore this if nothing here pushes to GitHub."}
    if result.get("returncode", 1) != 0:
        return {**base, "ok": False, "state": "not-authenticated",
                "detail": [(result.get("output") or "").strip().splitlines()[0]
                           if (result.get("output") or "").strip() else "`gh auth status` failed"],
                "remedy": "gh auth login"}

    output = result.get("output") or ""
    account_match = _GH_ACCOUNT_RE.search(output)
    account = account_match.group(1) if account_match else None
    scopes = parse_token_scopes(output)
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
                "detail": [f"has: {', '.join(scopes) or 'none'}"],
                "remedy": f"gh auth refresh -h github.com -s {','.join(missing)}"}
    return {**base, "ok": True, "state": "ok", "account": account, "scopes": scopes,
            "detail": [f"{account or 'authenticated'}: {', '.join(scopes)}"],
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
    if not path or not os.path.exists(path):
        return None, f"{script} points at {path or '<empty>'}, which does not exist"
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
    base = {
        "id": "installed_import",
        "applicable": True,
        "interpreter": result.get("interpreter"),
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
    if result.get("interpreter") is None:
        return {**base, "ok": False, "state": "interpreter-unknown",
                "detail": [result.get("interpreter_source", "interpreter not resolved")],
                "remedy": "Resolve it by hand: run `<python> -c 'import rig_workbench.workbench'` "
                          "from a directory outside this repo with PYTHONPATH unset."}
    payload = result.get("payload")
    if payload is None:
        return {**base, "ok": False, "state": "probe-failed",
                "detail": [f"the probe returned {result.get('returncode')} with no JSON",
                           result.get("stderr", "")[:200] or "(no stderr)"],
                "remedy": "Run the probe by hand from outside the repo to see what the "
                          "interpreter printed."}
    errors = payload.get("errors") or {}
    package = payload.get("package")
    detail = [f"interpreter: {result['interpreter']}"]
    if package:
        detail.append(f"resolved: {package}")
    if errors:
        return {**base, "ok": False, "state": "import-failed", "package": package,
                "failed_modules": sorted(errors),
                "detail": detail + [f"{name}: {message}" for name, message in sorted(errors.items())],
                "remedy": "Reinstall the CLI so the wheel matches the source: "
                          "`uv tool install --force rig-workbench` / `pipx reinstall rig-workbench` "
                          "(`/rig:setup` does the same)."}
    editable = bool(package) and _is_inside(pathlib.Path(package), root)
    if editable:
        # An editable install points at the source tree, so every subpackage is
        # present by construction — this green says the import works, not that a
        # built wheel would. Saying so is the difference between a sensor and a
        # rubber stamp.
        detail.append("editable install pointing at this checkout: a packaging omission "
                      "cannot show up here — verify a built wheel separately")
    return {**base, "ok": True, "state": "ok", "package": package, "detail": detail,
            "remedy": ""}


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
        kwargs = {"probe": spec.get("gh"), "remotes": spec.get("remotes", GH_REMOTE_SAMPLE)}
        if "required" in spec:
            kwargs["required"] = spec["required"]
    elif check is check_installed_import:
        kwargs = {"probe": spec.get("probe")}
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
          "scopes cannot be read, an installed package that imports only inside the checkout.\n")
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
