"""Keeping `/rig:go`'s documented surface in step with the CLI it routes to.

`commands/go.md` branches on the first word of `$ARGUMENTS`: a word listed in its
routing table is a subcommand, anything else is a natural-language task. So a
subcommand the table forgets does not merely go undocumented — it falls through to
task classification and gets misrouted, which is what #412 reported for `context`
and #417 for `confidence`.

Nothing checked the two against each other. `--validate`'s catalog drift checks cover
brick references, not this table, so the same gap had already been repaired by hand in
#221 and #327 before recurring twice more. The repair kept working; the absence of a
sensor is what recurred.

What is pinned here is the alignment itself, in the four places a subcommand has to
appear to be reachable and findable — the routing table, the frontmatter `description`
and `argument-hint`, and the opening list of the instruction the table delegates to.
`INTERNAL` is the deliberate exit: plumbing invoked by instructions rather than typed
by a user is named there, so a new CLI subcommand fails this test until somebody
either documents it or declares it internal. That declaration is itself checked, so
the list cannot rot into an excuse for commands that no longer exist.
"""

import pathlib
import re

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
CLI_PY = REPO_ROOT / "rig_workbench" / "workbench" / "cli.py"
GO_MD = REPO_ROOT / "commands" / "go.md"
OPS_MD = REPO_ROOT / "skills" / "engine" / "facets" / "instructions" / "workbench-ops.md"

# Subcommands that are plumbing: instructions, recipes and the orchestrator call them,
# users do not type them after `/rig:go`. Keeping them out of the routing table is a
# decision, not an oversight, so it is recorded rather than inferred.
INTERNAL = frozenset({
    "new",               # worktree registration — patterns/isolated-worktree drives it
    "route",             # capability resolution consumed by facets/instructions/workbench
    "step",              # step progress recording, written by the RUN
    "gate",              # acceptance-gate evaluation, written by the RUN
    "gates",             # preset definitions, read by instructions as the criteria authority
    "drill-corpus",      # /rig:drill fixture corpus
    "record-commit",     # provenance linkage, written at accept time
    "record-outcome",    # production outcome recording
    "trace-commit",      # reverse lookup over the provenance records
    "verify-provenance",  # signature verification over an accepted task
})


def cli_subcommands():
    source = CLI_PY.read_text(encoding="utf-8")
    return frozenset(re.findall(r'sub\.add_parser\(\s*"([a-z0-9-]+)"', source))


def routing_table_commands():
    """First word of each routing-table row in commands/go.md."""
    rows = re.findall(r"^\|\s*`([^`]+)`\s*\|", GO_MD.read_text(encoding="utf-8"), re.M)
    return frozenset(row.split()[0] for row in rows)


def frontmatter_field(name):
    match = re.search(rf'^{name}:\s*"(.*)"\s*$', GO_MD.read_text(encoding="utf-8"), re.M)
    assert match, f"commands/go.md has no {name} frontmatter field"
    return match.group(1)


def description_keywords():
    """The `status/diff/accept/...` run the description uses to advertise subcommands."""
    runs = re.findall(r"(?:[a-z][a-z-]*/){3,}[a-z][a-z-]*", frontmatter_field("description"))
    assert runs, "commands/go.md description no longer lists subcommands as a slash-run"
    return frozenset(word for run in runs for word in run.split("/"))


def argument_hint_commands():
    """First word of each `|`-separated alternative in the argument-hint."""
    words = set()
    for alternative in frontmatter_field("argument-hint").split("|"):
        alternative = alternative.strip().lstrip("\\").strip()
        match = re.match(r"([a-z][a-z0-9-]*)", alternative)
        if match:
            words.add(match.group(1))
    return frozenset(words)


def workbench_ops_commands():
    """Subcommands named in the opening list of facets/instructions/workbench-ops."""
    opening = OPS_MD.read_text(encoding="utf-8").split("\n\n")[1]
    return frozenset(re.findall(r"`/rig ([a-z][a-z0-9-]*)", opening))


def user_facing():
    return cli_subcommands() - INTERNAL


def test_every_user_facing_subcommand_is_reachable_from_the_routing_table():
    missing = sorted(user_facing() - routing_table_commands())
    assert not missing, (
        "commands/go.md routing table is missing "
        f"{missing} — without a row these fall through to natural-language task "
        "classification and get misrouted. Add a row, or add the name to INTERNAL "
        "if users are not meant to type it."
    )


def test_the_routing_table_does_not_advertise_commands_the_cli_lacks():
    known = cli_subcommands() | {"gh"}  # `gh …` routes to facets/instructions/gh-flow
    stale = sorted(routing_table_commands() - known)
    assert not stale, f"commands/go.md routes {stale}, which the CLI no longer defines"


def test_the_frontmatter_advertises_every_user_facing_subcommand():
    described = description_keywords()
    hinted = argument_hint_commands()
    missing = sorted(cmd for cmd in user_facing() if cmd not in described or cmd not in hinted)
    assert not missing, (
        f"commands/go.md frontmatter omits {missing} — the description and "
        "argument-hint are what a user sees before typing, so a subcommand absent "
        "from them is undiscoverable even once the routing table works."
    )


def test_workbench_ops_opens_with_every_subcommand_the_table_sends_it():
    delegated = {
        row.split()[0]
        for row in re.findall(r"^\|\s*`([^`]+)`\s*\|\s*`facets/instructions/workbench-ops`",
                              GO_MD.read_text(encoding="utf-8"), re.M)
    }
    missing = sorted(delegated - workbench_ops_commands())
    assert not missing, (
        f"facets/instructions/workbench-ops does not open by naming {missing}, "
        "which commands/go.md delegates to it — the opening list is how the "
        "instruction declares its own scope."
    )


def test_the_internal_list_names_only_subcommands_that_still_exist():
    stale = sorted(INTERNAL - cli_subcommands())
    assert not stale, (
        f"INTERNAL still excuses {stale}, which the CLI no longer defines — a stale "
        "exemption silently widens into cover for a future subcommand of the same name."
    )
