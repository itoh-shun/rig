"""The externally visible CLI surface, pinned through the real `rig-wb` process.

rig's internals are about to be rewritten, so nothing here imports rig_workbench:
every assertion goes out through `python -m rig_workbench.cli` (the `rig_cli`
fixture), which is what a user's shell actually reaches. Argument parsing, exit
codes and stdout framing are the contract, and an in-process call would see none
of them.

Two things are frozen.

*The subcommand surface.* Every name a user can type is written out as a literal
below and compared for exact equality against what `--help` prints. Equality, not
containment: a rewrite that quietly drops `wb stale-refs` or grows a new verb
fails here first, and the fix is to edit the literal — a deliberate act, recorded
in the diff — rather than to discover the change from a user's bug report.

*The verbs nothing else invokes through the CLI.* `wb effectiveness`,
`wb compose-options`, `wb note`, most of `pack`, `eval reproduce` and friends have
in-process coverage at best; before this file no test ever spawned them as a
process, so an import error or a broken parser in any of them was invisible to
the suite. Each one is run once here and its behaviour *as it stands today* is
pinned — a usage error where the verb needs arguments, exit code and stdout shape
where it runs. These are smoke tests: they say "this path executes and answers
like this", not "this answer is correct".

That second freeze covers a named handful, not the surface: the verbs spawned
beyond `--help` are exactly the ones in VERBS_SMOKE_RUN — 21 of the 122 verbs
frozen above. Every other verb is reached here by `--help` alone, which proves
its module imports and its parser builds and nothing more. This file claims no
behavioural coverage of them.
"""

import json
import re

import pytest

# ── T4: the frozen subcommand surface ────────────────────────────────────────
# Every set below was read off the real `--help` output, not off the source, and
# the tests re-read it and demand exact equality.

# `rig-wb --help`. Hand-written usage text, not argparse (see _hand_written_verbs).
TOP_LEVEL_SUBCOMMANDS = frozenset({
    "asvs", "baseline", "bench", "coverage", "dashboard", "design-constraints",
    "eval", "gh-check", "githooks", "govern", "hostcheck", "ja-lint", "mutation",
    "pack", "plan", "queue", "run", "runs", "selftest", "sensor-bench", "usage",
    "validate", "version", "wb",
})

# `rig-wb wb --help` — the workbench: run-state, worktrees, sensors, the gate.
WB_SUBCOMMANDS = frozenset({
    "accept", "anomaly-trigger", "assurance-derive", "assurance-target", "audit",
    "board", "budget-plan", "change-graph", "cockpit", "compose-options",
    "confidence", "context", "contract", "dev-loop", "diff", "digest", "discard",
    "drill-corpus", "effectiveness", "expected-outcome", "gate", "gates", "gc",
    "import", "instincts", "intent", "intent-derive", "knowledge-candidate", "log",
    "new", "note", "provenance", "receipt", "record-commit", "record-outcome",
    "review", "route", "route-team", "scan-anchors", "scan-destructive",
    "scan-injection", "scan-ja-prose", "scan-secrets", "stale-refs", "stats",
    "status", "step", "stream-checks", "synthesise", "trace-commit",
    "verify-provenance",
})

# `rig-wb govern --help` — the org/team layer.
GOVERN_SUBCOMMANDS = frozenset({
    "approve", "audit", "can", "conformance", "init", "migrate", "policy",
    "rollup", "waiver", "whoami",
})

# `rig-wb pack --help` — prompt-pack lifecycle and publishing.
PACK_SUBCOMMANDS = frozenset({
    "bundle", "doctor", "explain", "export", "import-results", "info", "init",
    "install", "invoke", "keygen", "knowledge", "list", "outdated", "remove",
    "sign", "source", "sync", "test", "update", "validate", "verify-sources",
})

# `rig-wb eval --help` — versioned regression evaluation cases.
EVAL_SUBCOMMANDS = frozenset({
    "affected", "affected-run", "capture", "compare", "gate", "list", "promote",
    "reproduce", "run", "validate",
})

# `rig-wb baseline --help` — versioned benchmark baselines.
BASELINE_SUBCOMMANDS = frozenset({"capture", "compare", "show"})

# `rig-wb githooks --help`. Hand-written usage text like the top level, and the
# verbs are hand-parsed too (see SUBCOMMANDS_THAT_REJECT_A_HELP_FLAG).
GITHOOKS_SUBCOMMANDS = frozenset({"install", "status", "uninstall"})

GROUPED_SUBCOMMANDS = {
    "wb": WB_SUBCOMMANDS,
    "govern": GOVERN_SUBCOMMANDS,
    "pack": PACK_SUBCOMMANDS,
    "eval": EVAL_SUBCOMMANDS,
    "baseline": BASELINE_SUBCOMMANDS,
    "githooks": GITHOOKS_SUBCOMMANDS,
}

# The only subcommands whose own `--help` is *not* asserted to exit 0, and the
# reason, so the exclusion is a statement rather than a silence: `githooks` parses
# its verbs by hand instead of through argparse, so `rig-wb githooks install
# --help` is not a help request at all — it is an unknown argument, and the
# command exits 2. That behaviour is itself pinned, in
# test_the_githooks_verbs_reject_the_help_flag_they_never_learned below, so this
# list cannot hide a regression: it only records where the help contract stops.
SUBCOMMANDS_THAT_REJECT_A_HELP_FLAG = frozenset({
    ("githooks", "install"),
    ("githooks", "uninstall"),
    ("githooks", "status"),
})

HELP_ANSWERING_SUBCOMMANDS = tuple(sorted(TOP_LEVEL_SUBCOMMANDS)) + tuple(sorted(
    f"{group} {verb}"
    for group, verbs in GROUPED_SUBCOMMANDS.items()
    for verb in verbs
    if (group, verb) not in SUBCOMMANDS_THAT_REJECT_A_HELP_FLAG
))

# Subcommands this file deliberately never runs past `--help`, with the reason.
# Everything here would reach outside the throwaway repo the fixtures hand out —
# the network, the developer's install scopes, a real provider's bill — or would
# cost more than a smoke test is worth. Their `--help` is still exercised by
# test_every_subcommand_answers_its_own_help_with_exit_zero, so the module still
# imports and the parser is still built; only the side effect is skipped.
# Keyed by argv prefix; the reason is what a later reader needs, not decoration.
#
# This is *not* the complement of what this file runs. Only 21 of the 122 frozen
# verbs are spawned beyond `--help` (VERBS_SMOKE_RUN, below); the ~100 that are
# not spawned are, with these 11 exceptions, simply unexercised rather than ruled
# out. The name says "deliberately" for that reason: the old name claimed to
# enumerate every not-run verb and enumerated a tenth of them.
VERBS_DELIBERATELY_NOT_RUN_BEYOND_HELP = {
    "run": "spawns a real provider (claude/codex): billable and minutes long",
    "bench": "runs an A/B benchmark against providers",
    "selftest": "executes the golden verification suite; minutes, not seconds",
    "validate": "walks and validates the whole repo tree",
    "dashboard": "writes an HTML dashboard file",
    "wb new": "cuts a git worktree and a branch; owned by the workbench tests",
    "govern init": "scaffolds policy files and binds the repository",
    "githooks install": "writes rig's hooks into .git/hooks",
    "pack install": "resolves a source (git clone) and writes into an install scope",
    "pack sign": "needs a private key and writes signature material",
    "pack remove": "deletes an installed pack from a scope",
}


def _hand_written_verbs(help_text, heading):
    """Verb names out of rig's hand-written usage blocks (top level, githooks).

    Neither of these two commands is an argparse parser, so there is no `{a,b,c}`
    to read: the surface is a literal block of text under a heading, one verb per
    entry at two-space indent, continuation lines indented further. Parsed rather
    than eyeballed so that a verb added to the dispatcher but forgotten in the
    help text still shows up as a diff against the frozen set.
    """
    verbs, inside = [], False
    for line in help_text.splitlines():
        if line.startswith(heading):
            inside = True
            continue
        if not inside:
            continue
        if not line.strip():
            continue
        if not line.startswith("  "):  # a new section at column 0 ends the block
            break
        if line.startswith("   "):  # a continuation of the previous entry
            continue
        verbs.append(line.split()[0])
    return verbs


def _argparse_choices(help_text):
    """The `{new,import,contract,…}` choice list argparse prints in its usage line."""
    match = re.search(r"\{([^{}\s]+)\}", help_text)
    assert match, f"no argparse subcommand choices in:\n{help_text}"
    return match.group(1).split(",")


def test_the_top_level_help_lists_exactly_the_frozen_set_of_subcommands(rig_cli):
    result = rig_cli("--help")
    assert result.returncode == 0, result.stderr
    assert set(_hand_written_verbs(result.stdout, "Sub-commands:")) == TOP_LEVEL_SUBCOMMANDS


@pytest.mark.parametrize("group", sorted(set(GROUPED_SUBCOMMANDS) - {"githooks"}))
def test_each_command_group_help_lists_exactly_the_frozen_set_of_subcommands(rig_cli, group):
    result = rig_cli(group, "--help")
    assert result.returncode == 0, result.stderr
    assert set(_argparse_choices(result.stdout)) == GROUPED_SUBCOMMANDS[group]


def test_the_githooks_help_lists_exactly_the_frozen_set_of_hook_verbs(rig_cli):
    result = rig_cli("githooks", "--help")
    assert result.returncode == 0, result.stderr
    # Usage entries read `rig-wb githooks install   [--force] …`; the title line
    # above them (`rig-wb githooks — rig's …`) is not one, hence the word class.
    assert set(re.findall(r"^\s+rig-wb githooks ([a-z][a-z-]*)\s", result.stdout,
                          re.MULTILINE)) == GITHOOKS_SUBCOMMANDS


@pytest.mark.parametrize("argv", HELP_ANSWERING_SUBCOMMANDS)
def test_every_subcommand_answers_its_own_help_with_exit_zero(rig_cli, argv):
    """A subcommand that cannot print its own help is broken before it is wrong.

    Cheapest possible proof that each verb's module imports and its parser builds:
    `--help` touches the dispatcher and the parser and then exits, without doing
    the verb's work, so this needs no repo, no network and no provider.
    """
    result = rig_cli(*argv.split(), "--help")
    assert result.returncode == 0, (
        f"`rig-wb {argv} --help` exited {result.returncode}\n"
        f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}")
    assert result.stdout.strip(), f"`rig-wb {argv} --help` printed nothing"


@pytest.mark.parametrize("verb", sorted(GITHOOKS_SUBCOMMANDS))
def test_the_githooks_verbs_reject_the_help_flag_they_never_learned(rig_cli, verb):
    """The one hole in the help contract, pinned so it stays a known hole.

    `githooks` reads its own argv instead of handing it to argparse, so `--help`
    after the verb is an unrecognised argument rather than a help request. Not
    asserted to be *right* — asserted to be what happens, so that either fixing it
    or preserving it is a decision someone makes on purpose.
    """
    result = rig_cli("githooks", verb, "--help")
    assert result.returncode == 2
    assert "unknown argument" in result.stderr


def test_every_verb_excluded_from_the_smoke_runs_is_still_a_real_subcommand(rig_cli):
    """Keep the exclusions honest: a stale exclusion is a silent gap.

    Two ways an entry rots. It can name a verb that no longer exists, in which case
    the exclusion protects nothing and only reads as if it did; or the verb can have
    gained a smoke run anyway, in which case the recorded reason ("billable",
    "writes into .git/hooks") is a false warning about a test that already runs.
    """
    for argv in VERBS_DELIBERATELY_NOT_RUN_BEYOND_HELP:
        parts = argv.split()
        if len(parts) == 1:
            assert parts[0] in TOP_LEVEL_SUBCOMMANDS, argv
        else:
            group, verb = parts
            assert group in GROUPED_SUBCOMMANDS, argv
            assert verb in GROUPED_SUBCOMMANDS[group], argv
        assert argv not in VERBS_SMOKE_RUN, (
            f"{argv} is listed as deliberately not run, but is smoke-run: "
            f"{VERBS_DELIBERATELY_NOT_RUN_BEYOND_HELP[argv]}")


# ── T5/T6: the verbs no test ever spawned ────────────────────────────────────

# argv that today ends in an argparse usage error because the verb needs
# arguments. Pinned as a pair: exit 2, and usage text on stderr rather than a
# traceback — the difference between "you forgot an argument" and "rig crashed".
VERBS_THAT_REQUIRE_ARGUMENTS = (
    "wb effectiveness",
    "wb compose-options",
    "wb note",
    "pack bundle",
    "pack invoke",
    "pack info",
    "pack explain",
    "pack update",
    "pack export",
    "pack keygen",
    "pack import-results",
    "eval reproduce",
    "eval affected-run",
)

# argv that runs to completion in a repo rig has never touched and says so.
VERBS_THAT_REPORT_AN_EMPTY_PACK_SCOPE = ("pack list", "pack outdated", "pack verify-sources")

# Every verb this file actually spawns beyond `--help`, written down so the
# coverage level is a stated number rather than an impression. The two tuples
# above are parametrised; the rest each have a test of their own further down.
# 21 verbs, against the 122 frozen at the top of this file.
VERBS_SMOKE_RUN = frozenset(VERBS_THAT_REQUIRE_ARGUMENTS) | frozenset(
    VERBS_THAT_REPORT_AN_EMPTY_PACK_SCOPE) | frozenset({
        "wb contract", "wb digest", "wb gc", "govern whoami", "pack sync",
    })

@pytest.mark.parametrize("argv", VERBS_THAT_REQUIRE_ARGUMENTS)
def test_a_verb_missing_its_required_arguments_prints_usage_and_exits_two(
        rig_cli, rig_git_repo, argv):
    result = rig_cli(*argv.split(), cwd=rig_git_repo)
    assert result.returncode == 2, (
        f"`rig-wb {argv}` exited {result.returncode}\n"
        f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}")
    assert result.stdout == "", f"`rig-wb {argv}` put its usage error on stdout"
    assert result.stderr.startswith("usage:"), result.stderr
    # The usage belongs to the verb itself, not to its parent group's chooser.
    assert argv.split()[-1] in result.stderr.splitlines()[0]


@pytest.mark.parametrize("argv", VERBS_THAT_REPORT_AN_EMPTY_PACK_SCOPE)
def test_a_pack_query_in_a_repo_with_no_packs_reports_the_empty_scope(
        rig_cli, rig_git_repo, argv):
    result = rig_cli(*argv.split(), cwd=rig_git_repo)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().endswith("in this scope"), result.stdout


def test_wb_effectiveness_derives_every_metric_as_unobservable_when_no_runs_are_recorded(
        rig_cli_json, rig_git_repo):
    """The verb's whole point: absent telemetry stays absent, it never becomes zero."""
    query = rig_git_repo / "effectiveness-query.json"
    query.write_text(json.dumps({
        "schema": "rig.workflow-effectiveness-query/v1",
        "patterns": [{"kind": "late-stage-failure", "minimum_occurrences": 2,
                      "late_steps": ["review"]}],
    }), encoding="utf-8")

    payload = rig_cli_json("wb", "effectiveness", "--query", query.name, "--json",
                           cwd=rig_git_repo, expect_returncode=0)

    assert payload["schema"] == "rig.workflow-effectiveness/v1"
    # A floor, not an exact set. tests/test_schema_cli_contract.py pins this same
    # document as `required <= set(payload)`, and the exact-set form here
    # contradicted it: adding a key would have failed this file and passed that
    # one. The exact form was dropped deliberately — adding a key to this document
    # is backwards compatible for readers, while losing one is not, and losing one
    # is what the floor still catches.
    assert {"schema", "metrics", "patterns", "records",
            "unobservable_patterns", "does_not_guarantee"} <= set(payload)
    assert payload["metrics"], "the metric list itself must not be empty"
    assert {metric["status"] for metric in payload["metrics"].values()} == {"unobservable"}
    assert payload["does_not_guarantee"], "the caveats are part of the answer"


def test_wb_effectiveness_answers_an_unreadable_query_with_an_envelope_not_a_traceback(
        rig_cli, rig_git_repo):
    result = rig_cli("wb", "effectiveness", "--query", "absent-query.json", "--json",
                     cwd=rig_git_repo)
    assert result.returncode == 2
    payload = json.loads(result.stdout)
    assert payload["schema"] == "rig.workflow-effectiveness/v1"
    assert payload["status"] == "execution-error"
    assert "Traceback" not in result.stderr


def test_wb_compose_options_derives_the_five_deterministic_axes_for_a_bugfix(
        rig_cli_json, rig_git_repo):
    payload = rig_cli_json("wb", "compose-options", "--type", "bugfix", "--json",
                           cwd=rig_git_repo, expect_returncode=0)

    assert payload["schema"] == "rig.compose-options/v1"
    assert payload["task_type"] == "bugfix"
    assert [axis["id"] for axis in payload["axes"]] == \
        ["recipe", "step", "gate", "backend", "mode"]
    for axis in payload["axes"]:
        assert axis["candidates"], f"axis {axis['id']} offered no candidates"
        assert axis["recommended"], f"axis {axis['id']} recommended nothing"


def test_wb_contract_answers_execution_error_when_the_task_state_cannot_be_read(
        rig_cli, rig_git_repo):
    """The machine answer an external orchestrator acts on, in the unreadable case."""
    result = rig_cli("wb", "contract", cwd=rig_git_repo)
    assert result.returncode == 2
    assert result.stdout.startswith("## rig assurance contract:")
    assert "execution-error" in result.stdout
    assert "No run history" in result.stderr


def test_wb_digest_reports_an_empty_period_in_a_repo_with_no_runs(rig_cli, rig_git_repo):
    result = rig_cli("wb", "digest", cwd=rig_git_repo)
    assert result.returncode == 0, result.stderr
    # The heading carries the ISO week and the date range, so only the stem is pinned.
    assert result.stdout.startswith("# rig digest — ")
    assert "No runs in period" in result.stdout


def test_wb_gc_finds_nothing_to_dispose_of_in_a_repo_with_no_visual_artifacts(
        rig_cli, rig_git_repo):
    result = rig_cli("wb", "gc", cwd=rig_git_repo)
    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith("## rig gc (threshold:")
    assert "Nothing to remove." in result.stdout


def test_wb_note_refuses_to_attach_a_hand_off_note_when_there_is_no_run_to_attach_it_to(
        rig_cli, rig_git_repo):
    result = rig_cli("wb", "note", "what a later run should know", cwd=rig_git_repo)
    # 2, not 1: there is no run history to attach a note to, which is rig unable to do
    # the thing — not rig judging the note and refusing it. 1 is reserved for a verdict,
    # and `wb contract` reports the same "No run history" condition as 2 as well
    # (test_wb_contract_answers_execution_error_when_the_task_state_cannot_be_read).
    assert result.returncode == 2, (
        f"`wb note` with no run history exited {result.returncode}; a plumbing failure "
        f"is 2, and 1 would be indistinguishable from a judgement\n"
        f"--- stderr ---\n{result.stderr}")
    assert result.stdout == ""
    assert "No run history" in result.stderr


def test_govern_whoami_names_the_actor_and_says_no_policy_is_configured(
        rig_cli, rig_git_repo):
    """`govern` is inert until `govern init` binds a repo; whoami has to say so."""
    result = rig_cli("govern", "whoami", cwd=rig_git_repo)
    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert lines[0].startswith("actor: ") and lines[0] != "actor: "
    assert lines[1].startswith("policy: ")
    assert "none configured" in lines[1]


def test_pack_sync_reports_the_missing_manifest_rather_than_reaching_for_a_source(
        rig_cli, rig_git_repo):
    """No pack.yaml, no sync: the failure is local and named, and nothing is fetched."""
    result = rig_cli("pack", "sync", cwd=rig_git_repo)
    assert result.returncode == 2
    assert result.stdout == ""
    assert "pack.yaml" in result.stderr
    assert "Traceback" not in result.stderr
