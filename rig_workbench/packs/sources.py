"""Named pack sources: where `product:joypla@1.4.0` comes from, and how it is pinned.

Three rules shape this module, and each rules out a design that would otherwise be simpler.

**A manifest never names a URL.** Sources are declared once per project in
`.rig/sources.json` as a name and a URL template; a spec names the source, not the address.
Putting the address in the pack would weld its contents to its distribution path — a fork, a
mirror, or a move from public to private would each be a content change.

**Rig never holds a credential.** It runs `git`, and git answers for authentication out of
whatever the person already set up: an SSH agent, a credential helper, `gh auth`, the OS
keychain, a CI secret. Rig does not read tokens, does not prompt for them, and stores a
source's *name* rather than its URL, so nothing it writes can carry an embedded credential.

**A version resolves to bytes, once.** `@1.4.0` resolves tag → commit → tree digest, and the
lock keeps all three. Re-installing checks the digest, so a tag that is moved later is a
refusal rather than a silent substitution. That pins content, not provenance: a digest says
the bytes are the same ones, never who put them there.
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import shutil
import subprocess
import tempfile

from .model import AuthFailed, PackError, RevisionNotFound, SourceUnreachable

SOURCES_NAME = "sources.json"
SOURCES_SCHEMA_VERSION = 1
#: `git+file` is for a local or mounted repository — development, and the offline/air-gapped
#: case where nothing can reach a forge. It is a real scheme rather than a test affordance:
#: the same pin and the same refusals apply to it.
SOURCE_SCHEMES = ("git+ssh", "git+https", "git+file")
SOURCE_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
#: `<source>:<pack>@<version>` — the shape a person types and the shape the lock records.
SPEC = re.compile(
    r"^(?P<source>[a-z0-9][a-z0-9-]{0,63}):(?P<pack>[a-z0-9][a-z0-9-]{0,79})"
    r"@(?P<version>[0-9]+\.[0-9]+\.[0-9]+(?:-[a-z0-9.-]+)?)$"
)
#: Source names the builtin alias namespaces already own. A project that redefined one would
#: silently change what `domain:sales` installs, so the collision is refused instead.
RESERVED_SOURCE_NAMES = frozenset({"domain", "official"})
_GIT_TIMEOUT_SECONDS = 120


def sources_path(project: pathlib.Path) -> pathlib.Path:
    return project / ".rig" / SOURCES_NAME


def read_sources(project: pathlib.Path) -> dict[str, dict]:
    """The project's declared sources, or an empty mapping when it declares none."""
    path = sources_path(project)
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PackError(f"{SOURCES_NAME} is not readable JSON: {exc}") from exc
    if (not isinstance(value, dict)
            or value.get("sources_schema_version") != SOURCES_SCHEMA_VERSION
            or set(value) != {"sources_schema_version", "sources"}
            or not isinstance(value.get("sources"), dict)):
        raise PackError(f"{SOURCES_NAME} schema is invalid")
    for name, source in value["sources"].items():
        validate_source(name, source)
    return value["sources"]


def validate_source(name: object, source: object) -> None:
    if not isinstance(name, str) or not SOURCE_NAME.fullmatch(name):
        raise PackError(f"source name is invalid: {name!r}")
    if name in RESERVED_SOURCE_NAMES:
        raise PackError(f"source name is reserved: {name}")
    if not isinstance(source, dict) or set(source) != {"scheme", "url"}:
        raise PackError(f"source {name} must declare exactly scheme and url")
    if source["scheme"] not in SOURCE_SCHEMES:
        raise PackError(
            f"source {name} scheme must be one of {', '.join(SOURCE_SCHEMES)}")
    url = source["url"]
    if not isinstance(url, str) or not url or "{pack}" not in url:
        raise PackError(f"source {name} url must be a template containing {{pack}}")
    if "@" in url.split("//", 1)[-1].split("/", 1)[0] and source["scheme"] == "git+https":
        # `https://user:token@host/...` is exactly the shape this design exists to avoid;
        # accepting it would put a credential in a file rig reads and copies around.
        raise PackError(f"source {name} url must not embed credentials")


def parse_spec(spec: str) -> tuple[str, str, str] | None:
    """`(source, pack, version)` for a named-source spec, or None when it is not one."""
    match = SPEC.match(spec)
    if match is None:
        return None
    if match.group("source") in RESERVED_SOURCE_NAMES:
        return None
    return match.group("source"), match.group("pack"), match.group("version")


def resolve_url(source: dict, pack: str) -> str:
    return source["url"].replace("{pack}", pack)


def _git(args: list[str], *, cwd: pathlib.Path | None = None) -> subprocess.CompletedProcess:
    # The environment is inherited, not rebuilt. git finds credentials through SSH_AUTH_SOCK,
    # HOME/.gitconfig, and the helpers configured there; stripping the environment to look
    # careful would disable the very machinery this design delegates authentication to, and
    # rig would end up needing credentials of its own.
    env = dict(os.environ)
    # Never stop on a terminal prompt: a fetch blocked forever on a password is
    # indistinguishable from a hang, and the honest answer is that the credentials on this
    # machine do not open this source.
    env["GIT_TERMINAL_PROMPT"] = "0"
    try:
        return subprocess.run(
            ["git", *args], cwd=cwd, capture_output=True, text=True,
            timeout=_GIT_TIMEOUT_SECONDS, env=env,
        )
    except FileNotFoundError as exc:
        raise PackError("git is required to install from a named source") from exc
    except subprocess.TimeoutExpired as exc:
        raise SourceUnreachable(
            f"source did not answer within {_GIT_TIMEOUT_SECONDS}s") from exc


#: git says why it failed in prose on stderr. These are matched conservatively — anything
#: unrecognised stays `source-unreachable`, which is the answer that does not claim to know.
_AUTH_MARKERS = (
    "authentication failed", "permission denied", "could not read username",
    "could not read password", "access denied", "invalid username or password",
    "terminal prompts disabled", "publickey",
)
_MISSING_MARKERS = (
    "repository not found", "not found", "does not exist", "no such",
)


def _classify(stderr: str, *, url: str) -> PackError:
    lowered = stderr.casefold()
    if any(marker in lowered for marker in _AUTH_MARKERS):
        return AuthFailed(
            "source refused the credentials on this machine; authenticate with the tool that "
            "owns them (ssh-agent, git credential helper, `gh auth login`) and retry")
    if any(marker in lowered for marker in _MISSING_MARKERS):
        # A private repository answers "not found" to someone who cannot see it, so this is
        # reported as reachable-but-absent rather than as proof the pack does not exist.
        return SourceUnreachable(
            "source did not resolve; it may not exist, or this machine may not be permitted "
            "to see it")
    return SourceUnreachable(f"could not read source {_redact(url)}")


def _redact(url: str) -> str:
    """A URL as it may be printed: scheme and host, never userinfo."""
    return re.sub(r"//[^/@]*@", "//", url)


def resolve_revision(source: dict, pack: str, version: str) -> str:
    """The commit a version tag points at, refusing anything that is not exactly one commit."""
    url = resolve_url(source, pack)
    tag = f"v{version}"
    result = _git(["ls-remote", "--tags", "--refs", url, f"refs/tags/{tag}"])
    if result.returncode != 0:
        raise _classify(result.stderr, url=url)
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    if not lines:
        raise RevisionNotFound(f"source has no tag {tag} for {pack}")
    if len(lines) > 1:
        raise RevisionNotFound(f"source resolves {tag} to more than one commit for {pack}")
    revision = lines[0].split("\t", 1)[0].strip()
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise RevisionNotFound(f"source returned an unusable revision for {tag}")
    return revision


def fetch_revision(source: dict, pack: str, version: str, revision: str,
                   destination: pathlib.Path) -> None:
    """Materialise the tag for `version` into `destination`, refusing it unless it is
    still `revision`.

    The tag is fetched rather than the bare commit because a server may refuse to serve an
    arbitrary SHA, and then re-checked against the revision `resolve_revision` read. That
    re-check is the point: a tag moved in between is a refusal here rather than a different
    pack installed under the version somebody pinned.
    """
    url = resolve_url(source, pack)
    tag = f"v{version}"
    staging = pathlib.Path(tempfile.mkdtemp(prefix=".pack-git-"))
    work = staging / "work"
    try:
        for args in (["init", "--quiet", str(work)],
                     ["-C", str(work), "remote", "add", "origin", url]):
            result = _git(args)
            if result.returncode != 0:
                raise _classify(result.stderr, url=url)
        result = _git(["-C", str(work), "fetch", "--quiet", "--depth", "1",
                       "origin", f"refs/tags/{tag}"])
        if result.returncode != 0:
            raise _classify(result.stderr, url=url)
        head = _git(["-C", str(work), "rev-parse", "FETCH_HEAD"])
        fetched = head.stdout.strip()
        if head.returncode != 0 or fetched != revision:
            raise RevisionNotFound(
                f"{tag} moved while installing: expected {revision}, source served "
                f"{fetched or 'nothing'}")
        result = _git(["-C", str(work), "checkout", "--quiet", "--detach", "FETCH_HEAD"])
        if result.returncode != 0:
            raise RevisionNotFound(f"source could not check out {revision}")
        shutil.rmtree(work / ".git", ignore_errors=True)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(work), str(destination))
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def write_sources(project: pathlib.Path, sources: dict[str, dict]) -> None:
    """Persist the project's source declarations, canonically and without credentials."""
    from .lock import refuse_credentials

    for name, source in sources.items():
        validate_source(name, source)
    payload = (json.dumps(
        {"sources_schema_version": SOURCES_SCHEMA_VERSION,
         "sources": {name: sources[name] for name in sorted(sources)}},
        ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
    refuse_credentials(payload, where=SOURCES_NAME)
    path = sources_path(project)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(payload.decode("utf-8"), encoding="utf-8")


def verify_pin(source: dict, entry: dict) -> str:
    """Re-check one locked git pack against its source. Returns the reason, `ok` when it holds.

    This is where a moved tag stops being invisible. It costs a network round trip per pack,
    so it is a command a person runs rather than something install-time work does behind
    their back — and it reports the reasons apart, because `auth-failed` is fixed by logging
    in and `digest-mismatch` never is.
    """
    locked = entry["source"]
    pack = locked["path"].split(":", 1)[1].split("@", 1)[0]
    version = locked["path"].rsplit("@", 1)[1]
    try:
        revision = resolve_revision(source, pack, version)
    except PackError as error:
        return getattr(error, "reason", "invalid-pack")
    if revision != locked["revision"]:
        # The tag now points somewhere else. That is the case `@1.4.0` is supposed to make
        # impossible to install silently, so it is named as a mismatch rather than as an
        # update available.
        return "digest-mismatch"
    return "ok"
