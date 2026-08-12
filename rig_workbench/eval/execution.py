"""Deterministic Git execution-state identity."""

from __future__ import annotations

import hashlib
import pathlib
import subprocess

from .cases import EvalCaseError

# Pinned rather than inherited from whoever invokes us.
#
# The measurement now happens on a maintainer's machine and the recomputation on
# a runner, so `git diff` has become a cross-machine function and every knob that
# changes its bytes has become a way for the gate to fail for a reason it cannot
# report. Measured on this repository: `diff.noprefix=true` and
# `diff.renames=false` each produce a different `execution_diff_sha256` for the
# identical tree pair. A maintainer with either in `~/.gitconfig` would sign
# evidence CI can only answer with `execution_diff_mismatch` — permanently, and
# with the cause named nowhere. That is the same "nobody can pass this" defect
# this change exists to remove, arriving by another door.
#
# `--no-ext-diff` on the command line already covers `diff.external` and
# `--no-textconv` covers a textconv driver declared in `.gitattributes`.
# `core.quotePath` is here because `--name-only` escapes non-ASCII paths under
# the default, which no surface prefix then matches. Not covered:
# `diff.orderFile`, which git offers no way to unset from the command line — an
# empty value is read as a missing file and fails outright.
GIT_DETERMINISTIC = (
    "-c", "core.quotePath=false",
    "-c", "color.diff=false",
    "-c", "diff.noprefix=false",
    "-c", "diff.mnemonicPrefix=false",
    "-c", "diff.renames=false",
    "-c", "diff.algorithm=myers",
    "-c", "diff.context=3",
    "-c", "diff.indentHeuristic=true",
)


def _run(repo: pathlib.Path, argv: list[str]) -> bytes:
    try:
        completed = subprocess.run(
            argv, cwd=repo, capture_output=True, timeout=15, shell=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise EvalCaseError("cannot compute execution diff identity") from exc
    if completed.returncode != 0:
        raise EvalCaseError("cannot compute execution diff identity")
    output = completed.stdout
    return output.encode("utf-8") if isinstance(output, str) else (output or b"")


def execution_diff_sha256(
    repo: pathlib.Path, *, base: str, head: str = "working",
    ignored_untracked_prefixes: tuple[str, ...] = (),
) -> str:
    """Hash base→head/working tracked diff plus untracked path/content framing."""
    diff_argv = [
        "git", *GIT_DETERMINISTIC,
        "diff", "--binary", "--full-index", "--no-ext-diff", "--no-textconv", base,
    ]
    if head != "working":
        diff_argv.append(head)
    diff_argv.append("--")
    tracked = _run(repo, diff_argv)
    digest = hashlib.sha256()
    digest.update(b"rig-eval-execution-diff-v1\0")
    digest.update(len(tracked).to_bytes(8, "big"))
    digest.update(tracked)
    if head == "working":
        raw_paths = _run(
            repo, ["git", *GIT_DETERMINISTIC,
                   "ls-files", "--others", "--exclude-standard", "-z"],
        )
        paths = sorted(
            path for path in raw_paths.split(b"\0") if path
            and not path.startswith(b".rig/evals/results/")
            and not any(
                path == prefix.encode("utf-8")
                or path.startswith(prefix.encode("utf-8") + b"/")
                for prefix in ignored_untracked_prefixes
            )
        )
        for encoded in paths:
            try:
                relative = encoded.decode("utf-8")
                candidate = (repo / relative).resolve()
                candidate.relative_to(repo.resolve())
                content = candidate.read_bytes()
            except (UnicodeError, OSError, ValueError) as exc:
                raise EvalCaseError("cannot hash untracked execution input") from exc
            digest.update(b"U")
            digest.update(len(encoded).to_bytes(8, "big"))
            digest.update(encoded)
            digest.update(len(content).to_bytes(8, "big"))
            digest.update(content)
    return digest.hexdigest()
