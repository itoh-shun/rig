"""The non-interactive guard clause has to survive prose edits (#587).

The clause tells a dispatched agent — `codex exec`, `claude -p`, CI, an
orchestrator provider call, a Task/Agent subagent — that talk's conversational
manner and its confirm-before-acting rule do not apply to it. #587 put the
clause in the four files such an agent actually reads and said so in the
CHANGELOG, but pinned it nowhere a test could see.

It was then dropped from `talk-assistant` by a persona rewrite and only caught
in review, because a prose rewrite that reproduces a file from an older copy
loses whatever was added in between and nothing fails. That is the failure this
test exists to make loud: the clause is an invariant across four files, not a
paragraph any one of them owns.
"""

import pathlib

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent

# Every file a dispatched agent reads before it decides whether talk's rules
# bind it. Adding a fifth entry point means adding it here too.
GUARDED_FILES = [
    "skills/engine/SKILL.md",
    "codex/skills/rig/SKILL.md",
    "skills/engine/facets/instructions/talk-loop.md",
    "skills/engine/facets/personas/talk-assistant.md",
]

OPEN_TAG = "<NON-INTERACTIVE-STOP>"
CLOSE_TAG = "</NON-INTERACTIVE-STOP>"

# What the clause has to still mean, not how it is worded. A rewrite may change
# the prose; it may not quietly drop one of the execution modes it exempts.
REQUIRED_MENTIONS = ["codex exec", "claude -p", "CI"]


@pytest.mark.parametrize("rel", GUARDED_FILES)
def test_guard_clause_is_present_and_closed(rel):
    path = REPO_ROOT / rel
    assert path.is_file(), f"{rel} is missing; update GUARDED_FILES if it moved"
    text = path.read_text(encoding="utf-8")

    assert text.count(OPEN_TAG) == 1, (
        f"{rel} must carry exactly one {OPEN_TAG} block — a dispatched agent "
        f"reads this file and needs the clause to decide talk's rules do not bind it"
    )
    assert text.count(CLOSE_TAG) == 1, f"{rel} has an unclosed {OPEN_TAG} block"

    body = text.split(OPEN_TAG, 1)[1].split(CLOSE_TAG, 1)[0]
    missing = [m for m in REQUIRED_MENTIONS if m not in body]
    assert not missing, (
        f"{rel}: the guard clause no longer names {missing}. The wording is free; "
        f"the set of exempted execution modes is not"
    )
