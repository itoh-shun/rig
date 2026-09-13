#!/usr/bin/env python3
"""release_notes.py — the notes `.github/workflows/release.yml` hands to `gh`.

Usage, as release.yml calls it:

    python3 scripts/release_notes.py --changelog CHANGELOG.md --version 3.0.0 \
        --repo owner/name --tag v3.0.0 --output notes.md --status-file "$GITHUB_OUTPUT"

Writes the notes to `--output`, and `source` / `shortened` / `entry_chars` /
`notes_chars` as `key=value` lines to `--status-file`, which release.yml points at
`$GITHUB_OUTPUT` so the next step can report what the notes carry. An oversized
entry is cut to fit GitHub's 125,000-character limit on a release body, with a
pointer to CHANGELOG.md at the tag; the CHANGELOG entry itself is never touched.

The cutting is `rig_workbench/release_notes.py` — the arithmetic, with no paths in
it, so tests can drive it directly. This file is the process around it: the
arguments, the two files, and the one line of diagnostics. It used to be an `awk`
program inside the workflow's `run:` block, where nothing could reach it, and it was
there that the 3.0.0 release failed with `HTTP 422: body is too long`.
"""
import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from rig_workbench.console import harden_streams  # noqa: E402
from rig_workbench.release_notes import GITHUB_BODY_LIMIT, build_notes  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--changelog", default="CHANGELOG.md")
    parser.add_argument("--version", required=True)
    parser.add_argument("--repo", required=True, help="owner/name, for the pointer link")
    parser.add_argument("--tag", required=True, help="the tag the pointer link reads at")
    parser.add_argument("--output", required=True, help="where the notes are written")
    parser.add_argument("--status-file",
                        help="key=value lines, appended; release.yml points this at "
                             "$GITHUB_OUTPUT, which earlier steps have already written to")
    parser.add_argument("--limit", type=int, default=GITHUB_BODY_LIMIT,
                        help="budget in UTF-8 bytes (default: %(default)s)")
    args = parser.parse_args(argv)

    changelog = pathlib.Path(args.changelog).read_text(encoding="utf-8")
    notes, status = build_notes(changelog, args.version, args.repo, args.tag, args.limit)
    # Written verbatim: `notes` already carries its trailing newline, and the budget
    # was computed on these exact bytes. Adding one here is what put the file one byte
    # over the limit the cut had just fitted it to.
    pathlib.Path(args.output).write_text(notes, encoding="utf-8")

    if status["source"] == "auto":
        sys.stderr.write(
            f"release_notes: no '## [{args.version}]' section in {args.changelog}\n")
    elif status["shortened"] == "true":
        sys.stderr.write(
            f"release_notes: the {args.version} entry is {status['entry_bytes']} bytes "
            f"({status['entry_chars']} characters); cut to {status['notes_bytes']} bytes "
            f"for the {args.limit}-byte budget\n")
    if args.status_file:
        with pathlib.Path(args.status_file).open("a", encoding="utf-8") as handle:
            for key, value in status.items():
                handle.write(f"{key}={value}\n")
    return 0


if __name__ == "__main__":
    # Every process entry under `scripts/` hardens its output streams here — see
    # `rig_workbench/console.py`, and `scripts/workbench.py` for the reasoning in full.
    harden_streams()
    sys.exit(main())
