"""Deterministic Git execution-state identity."""

from __future__ import annotations

import hashlib
import pathlib
import subprocess

from .cases import EvalCaseError


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
) -> str:
    """Hash base→head/working tracked diff plus untracked path/content framing."""
    diff_argv = [
        "git", "diff", "--binary", "--full-index", "--no-ext-diff", base,
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
            repo, ["git", "ls-files", "--others", "--exclude-standard", "-z"],
        )
        paths = sorted(
            path for path in raw_paths.split(b"\0") if path
            and not path.startswith(b".rig/evals/results/")
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
