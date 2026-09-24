"""Every copy of the run-status header rule carries the no-information wake-up exemption.

The rule "restate the run-status header at the top of every turn" is repeated in many
command bodies and in the reminder hook. SKILL.md §6 ① exempts one kind of turn: a
`completed` task-notification whose result is the harness's "already delivered as a
message, not repeated" sentence. A copy that states the rule without the exemption tells
the model to narrate exactly the turns the exemption exists to silence, so each copy must
carry the same sentence, word for word.
"""

import pathlib
import re

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent

#: The directive, as the command bodies and the hook phrase it.
DIRECTIVE = re.compile(r"restate th(?:is|e) run-status header")

#: Written out rather than read from a file, so a copy cannot drift by editing the source.
EXEMPTION = ("Exception (SKILL.md §6 ①): a turn woken only by a `<task-notification>` whose "
             "status is `completed` and whose result says the report was already delivered "
             "to you as a message and is not repeated gets no header and no narration; a "
             "`failed`, `killed` or `stopped` notification is never exempt.")

SURFACES = ("commands", "hooks", "skills")


def files_with_the_directive():
    found = []
    for surface in SURFACES:
        for path in sorted((REPO_ROOT / surface).rglob("*")):
            if path.is_file() and path.suffix in (".md", ".sh"):
                if DIRECTIVE.search(path.read_text(encoding="utf-8")):
                    found.append(path)
    return found


def test_the_directive_is_found_where_it_is_known_to_live():
    """Guards the scan itself: a glob or regex that finds nothing would pass vacuously."""
    names = {p.relative_to(REPO_ROOT).as_posix() for p in files_with_the_directive()}
    assert {"commands/go.md", "commands/dev.md", "commands/loop.md",
            "hooks/remind-rig-header.sh"} <= names
    assert len(names) >= 18


def test_every_copy_of_the_directive_carries_the_exemption():
    missing = [p.relative_to(REPO_ROOT).as_posix() for p in files_with_the_directive()
               if EXEMPTION not in p.read_text(encoding="utf-8")]
    assert missing == []


def test_skill_md_states_the_exemption_it_is_cited_for():
    skill = (REPO_ROOT / "skills" / "engine" / "SKILL.md").read_text(encoding="utf-8")
    assert "情報のない起床は例外" in skill
