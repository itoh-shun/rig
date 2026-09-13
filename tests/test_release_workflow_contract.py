"""Guards on `.github/workflows/release.yml` — the workflow that did not publish v3.0.0.

Run 34736999578 failed on the v3 merge with

    HTTP 422: Validation Failed
    body is too long (maximum is 125000 characters)

because the notes step handed the whole `## [3.0.0]` CHANGELOG section (129,059
bytes) to `gh release create --notes-file`. Two failures, not one: the oversized
body, and the state it left behind — `gh release create` creates the tag before
the release, so `v3.0.0` exists at `ebe2621` with no release, and the workflow's
idempotence check keys on the *release*.

These run the workflow's own `run:` bodies rather than restating them, in the
manner of tests/test_eval_workflow_contract.py: the steps are read out of the YAML
and executed by bash against real git repositories and a stub `gh`, so a step that
stops saying what it says here fails, and so does one that says it in a way that
cannot run. Tags are real tags — lightweight and annotated — because the step reads
them with `git rev-parse ...^{commit}` and the dereference that peel performs is
the whole reason an annotated tag does not take the "different commit" branch.

What is NOT covered here, named rather than implied: how GitHub's own API answers
a `POST /repos/{o}/{r}/releases` whose `tag_name` is already a tag with no release
attached. That needs a real repository to create a real orphan tag in, and there
is none available to this suite. What is pinned instead is everything around it —
that the workflow reaches that call having recognised the state, that it sends no
`target_commitish` when the tag is already placed, and that it verifies afterwards
that the tag still names the same commit.
"""
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
WORKFLOW = ROOT / ".github" / "workflows" / "release.yml"

#: The ceiling the 422 named. Mirrored from rig_workbench.release_notes so a change
#: to one of them has to be a deliberate change to both.
BODY_LIMIT = 125_000


def _step(name: str) -> dict:
    import yaml

    document = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    steps = document["jobs"]["release"]["steps"]
    try:
        return next(item for item in steps if item.get("name") == name)
    except StopIteration:  # pragma: no cover - only on a renamed/removed step
        raise AssertionError(
            f"release.yml has no step named {name!r}; it has "
            f"{[item.get('name') for item in steps]}") from None


def _body(name: str, subs: dict[str, str] | None = None) -> str:
    """The step's shell, ready to run.

    With no `subs`, this also asserts the step *is* runnable shell as written: a
    `${{ }}` expression inside a `run:` body cannot be executed outside Actions,
    and — the reason that matters more — it splices a value into shell source.
    Every value these steps need arrives through `env:`, which is both testable
    and not an injection site.

    `subs` renders the expressions instead of refusing them, which is what lets the
    notes test run the *old* step as well as the new one and show the difference in
    the bytes it produces rather than in a formatting rule.
    """
    body = _step(name)["run"]
    if subs is None:
        assert "${{" not in body, (
            f"{name}: values reach the shell through env:, not by interpolation — {body}")
        return body
    for expression in set(re.findall(r"\$\{\{(.*?)\}\}", body)):
        key = expression.strip()
        assert key in subs, f"{name}: no test value for ${{{{ {key} }}}}"
        body = body.replace("${{" + expression + "}}", subs[key])
    return body




GH_STUB = '''
import json, os, pathlib, sys

argv = sys.argv[1:]
log = pathlib.Path(os.environ["GH_STUB_LOG"])
with log.open("a", encoding="utf-8") as handle:
    handle.write(json.dumps(argv) + "\\n")
state = json.loads(os.environ["GH_STUB_STATE"])

if argv[:2] == ["release", "view"]:
    sys.exit(0 if state["release"] else 1)
if argv[:2] == ["release", "create"]:
    sys.exit(state.get("create_exit", 0))
sys.stderr.write("gh stub: unhandled " + " ".join(argv) + "\\n")
sys.exit(9)
'''


def _gh_stub(directory: pathlib.Path) -> pathlib.Path:
    """A `gh` on PATH that answers about the release and records what it was asked.

    It answers about the release and nothing else, because after #624 that is all the
    workflow asks `gh`: the tag questions go to git, where an absent ref and a failed
    call are different answers.
    """
    directory.mkdir(parents=True, exist_ok=True)
    stub = directory / "gh"
    stub.write_text("#!/usr/bin/env python3\n" + GH_STUB, encoding="utf-8")
    stub.chmod(0o755)
    return directory


def _git(repo: pathlib.Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, check=True,
                          capture_output=True, text=True).stdout.strip()


def _checkout(tmp_path: pathlib.Path, tags: dict[str, tuple[str, str]]) -> tuple:
    """A bare origin carrying `tags`, and a runner clone as actions/checkout leaves it.

    `tags` maps tag name to (which commit, "lightweight" or "annotated"). Two commits
    exist: "target" is the one a run would release and "other" is a later one, so a
    tag can be put somewhere this workflow must refuse to release over.

    The clone reproduces what `fetch-depth: 0` fetches and then detaches at the target,
    which is the state every one of these steps starts from.
    """
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", str(origin)], check=True)
    seed = tmp_path / "seed"
    seed.mkdir()
    _git(seed, "init", "-q", "-b", "master")
    _git(seed, "config", "user.email", "sim@test.invalid")
    _git(seed, "config", "user.name", "sim")
    (seed / "f").write_text("one\n", encoding="utf-8")
    _git(seed, "add", ".")
    _git(seed, "commit", "-qm", "the commit this run releases")
    commits = {"target": _git(seed, "rev-parse", "HEAD")}
    (seed / "g").write_text("two\n", encoding="utf-8")
    _git(seed, "add", ".")
    _git(seed, "commit", "-qm", "a later commit")
    commits["other"] = _git(seed, "rev-parse", "HEAD")
    objects = {}
    for name, (where, kind) in tags.items():
        if kind == "annotated":
            _git(seed, "tag", "-a", name, "-m", f"release {name}", commits[where])
        else:
            _git(seed, "tag", name, commits[where])
        # What `refs/tags/<name>` resolves to *without* peeling: for an annotated tag
        # this is the tag object, and it is what a comparison that forgets `^{commit}`
        # would put against the target sha.
        objects[name] = _git(seed, "rev-parse", name)
    _git(seed, "remote", "add", "origin", str(origin))
    _git(seed, "push", "-q", "origin", "master", *(["--tags"] if tags else []))

    runner = tmp_path / "runner"
    runner.mkdir()
    _git(runner, "init", "-q")
    _git(runner, "remote", "add", "origin", str(origin))
    _git(runner, "fetch", "-q", "--no-recurse-submodules", "origin",
         "+refs/heads/*:refs/remotes/origin/*", "+refs/tags/*:refs/tags/*")
    _git(runner, "checkout", "-q", "--force", commits["target"])
    return runner, commits, objects


def _run(body: str, cwd: pathlib.Path, env: dict, stub_dir: pathlib.Path | None = None):
    script = cwd / "step.sh"
    script.write_text(body, encoding="utf-8")
    path = os.environ["PATH"]
    if stub_dir is not None:
        path = f"{stub_dir}{os.pathsep}{path}"
    # GitHub runs a `run:` block as `bash -e {0}`; `-e` is what decides whether a
    # failing command aborts the step or is handled.
    return subprocess.run(["bash", "-e", str(script)], cwd=cwd, capture_output=True,
                          text=True, env={"PATH": path, "HOME": str(cwd), **env})


def _outputs(path: pathlib.Path) -> dict:
    return dict(line.split("=", 1) for line in
                path.read_text(encoding="utf-8").splitlines() if "=" in line)


def _changelog(version: str, body: str) -> str:
    return (f"# Changelog\n\n## [{version}] - 2026-01-01\n\n{body}\n\n"
            "## [0.0.1] - 2020-01-01\n\nthe entry before it\n")


@pytest.mark.skipif(shutil.which("bash") is None, reason="the step is a bash script")
def test_the_notes_step_keeps_the_release_body_under_the_api_limit(tmp_path):
    """The 422, reproduced: the step's own shell, over this repository's own CHANGELOG.

    Not a unit test of the cutting — the step as the runner executes it, at the
    version plugin.json declares, over the file that actually broke it. On the
    commit this branch starts from, the same step (its `${{ }}` expressions rendered)
    writes 129,058 bytes and this fails naming the overage — the 422 itself, not a
    formatting rule standing in for it.
    """
    version = json.loads(
        (ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))["version"]
    (tmp_path / "scripts").symlink_to(ROOT / "scripts")
    (tmp_path / "CHANGELOG.md").symlink_to(ROOT / "CHANGELOG.md")
    output = tmp_path / "github_output"
    output.write_text("", encoding="utf-8")

    body = _body("Extract release notes from CHANGELOG", subs={
        "steps.target.outputs.version": version,
        "steps.target.outputs.tag": f"v{version}"})
    result = _run(body, tmp_path, {
        "VERSION": version, "TAG": f"v{version}",
        "GITHUB_REPOSITORY": "itoh-shun/rig", "GITHUB_OUTPUT": str(output)})
    assert result.returncode == 0, result.stdout + result.stderr

    notes = (tmp_path / "notes.md").read_bytes()
    assert notes, "the step produced no notes for the version plugin.json declares"
    assert len(notes) <= BODY_LIMIT, (
        f"the {version} notes are {len(notes)} bytes, {len(notes) - BODY_LIMIT} over "
        f"GitHub's {BODY_LIMIT}-character release body limit — this is the 422 that "
        f"stopped run 34736999578")
    # …and in characters too, since which of the two GitHub counts is not stated.
    text = notes.decode("utf-8")
    assert len(text) <= BODY_LIMIT
    # The section heading belongs to `--title`, not to the body. A body that repeats
    # it renders the version twice on the release page.
    assert not text.lstrip().startswith("## ["), text[:80]

    outputs = _outputs(output)
    assert outputs["source"] == "changelog", outputs
    if outputs["shortened"] == "true":
        # A shortened body has to say so and say where the rest is, or the release
        # page reads as an entry that simply stops.
        assert "shortened" in text
        assert f"CHANGELOG.md at v{version}" in text
        assert f"/blob/v{version}/CHANGELOG.md" in text
        assert int(outputs["entry_bytes"]) > int(outputs["notes_bytes"])
        # Both units, in the footer as well as in the status dict. They really do
        # differ on this entry, so a footer that names only the character count puts
        # a number *under* the limit next to the claim that the entry was over it,
        # and leaves the reader no way to tell which half is wrong.
        assert int(outputs["entry_bytes"]) > int(outputs["entry_chars"])
        assert f"{int(outputs['entry_chars']):,} characters" in text, text[-400:]
        assert f"{int(outputs['entry_bytes']):,} bytes in UTF-8" in text, text[-400:]


@pytest.mark.skipif(shutil.which("bash") is None, reason="the step is a bash script")
@pytest.mark.parametrize("entry,shortened", [
    (BODY_LIMIT - 2, "false"),   # comfortably under
    (BODY_LIMIT - 1, "false"),   # the largest entry that fits whole, newline included
    (BODY_LIMIT, "true"),        # one over once the file has its newline
    (BODY_LIMIT + 1, "true"),    # one over on its own
])
def test_the_notes_file_that_is_handed_to_gh_never_exceeds_the_limit(
    tmp_path, entry, shortened,
):
    """The bytes of the *file*, at the boundary, through the real script.

    The budget bounds what `--notes-file` reads, and for a while it bounded the
    string one step earlier: the script wrote `notes + "\\n"`, so an entry of exactly
    the limit produced a 125,001-byte file with `shortened=false`, and — worse — an
    entry one over ran the cut, reported `shortened=true`, and still wrote 125,001.
    A mechanism that exists to prevent a 422 producing one is the failure this whole
    change is about, so these rows measure the file and nothing else.
    """
    (tmp_path / "CHANGELOG.md").write_text(_changelog("9.9.9", "x" * entry),
                                           encoding="utf-8")
    output, notes = tmp_path / "github_output", tmp_path / "notes.md"
    output.write_text("", encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "release_notes.py"),
         "--changelog", str(tmp_path / "CHANGELOG.md"), "--version", "9.9.9",
         "--repo", "itoh-shun/rig", "--tag", "v9.9.9", "--output", str(notes),
         "--status-file", str(output)], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr

    written = notes.read_bytes()
    assert len(written) <= BODY_LIMIT, (
        f"entry of {entry} produced a {len(written)}-byte notes file, "
        f"{len(written) - BODY_LIMIT} over the {BODY_LIMIT} GitHub accepts")
    assert _outputs(output)["shortened"] == shortened, _outputs(output)
    assert _outputs(output)["entry_chars"] == str(entry)


@pytest.mark.skipif(shutil.which("bash") is None, reason="the step is a bash script")
def test_a_version_with_no_changelog_section_falls_back_to_generated_notes(tmp_path):
    """The `--generate-notes` path, which had no row at all.

    `source=auto` is not an error here — a version with no entry is caught by
    rig_workbench/validation/release.py, not by the release job — but it does decide
    which flags the create step passes, and nothing checked either half.
    """
    (tmp_path / "CHANGELOG.md").write_text(_changelog("1.0.0", "an entry"),
                                           encoding="utf-8")
    (tmp_path / "scripts").symlink_to(ROOT / "scripts")
    output = tmp_path / "github_output"
    output.write_text("", encoding="utf-8")
    result = _run(_body("Extract release notes from CHANGELOG"), tmp_path, {
        "VERSION": "9.9.9", "TAG": "v9.9.9", "GITHUB_REPOSITORY": "itoh-shun/rig",
        "GITHUB_OUTPUT": str(output)})
    assert result.returncode == 0, result.stdout + result.stderr
    assert _outputs(output)["source"] == "auto", _outputs(output)
    assert (tmp_path / "notes.md").read_bytes() == b""

    report = _run(_body("Report what the notes carry"), tmp_path, {
        "VERSION": "9.9.9", "SOURCE": "auto", "SHORTENED": "false",
        "ENTRY_CHARS": "0", "ENTRY_BYTES": "0", "NOTES_CHARS": "0", "NOTES_BYTES": "0",
        "GITHUB_STEP_SUMMARY": str(tmp_path / "summary")})
    assert "::notice::No 9.9.9 section in CHANGELOG" in report.stdout, report.stdout


def test_the_cut_lands_on_a_block_boundary_and_never_inside_a_fence_or_a_table():
    """Where the cut goes, which is the difference between shortened and broken.

    A body sliced at byte N ends mid-sentence about as often as not, and inside a
    fence it takes the rest of the page with it — the closing ``` is gone, so the
    pointer to the full entry renders as code. Driven at every limit across the body
    so the boundary is hit many times over every block shape the CHANGELOG uses.
    """
    from rig_workbench.release_notes import shorten, split_blocks

    body = "\n\n".join([
        "### Breaking",
        "A paragraph that runs on for a while so the budget lands inside it, "
        "and would cut mid-sentence if the cut were taken at a byte offset.",
        "| step | before | after |\n|---|---|---|\n| create | tag | release |",
        "```console\n$ rig-wb release\n\nstill inside the fence\n```",
        "- a list item\n- another list item",
        "### A heading with nothing under it yet",
        "The last paragraph.",
    ])
    footer = "\n\n---\n\nshortened; the rest is in CHANGELOG.md at v9.9.9."
    # Whole blocks, so the pieces the cut chooses between rebuild the body exactly.
    assert "\n".join(split_blocks(body)) == body

    seen_shorter = False
    for limit in range(len(footer.encode()) + 20, len(body.encode()) + len(footer.encode())):
        notes = shorten(body, footer, limit)
        assert notes.endswith(footer), notes
        assert len(notes.encode("utf-8")) <= limit, (limit, len(notes.encode("utf-8")))
        kept = notes[: -len(footer)]
        seen_shorter = seen_shorter or kept != body
        assert kept.count("```") % 2 == 0, f"cut left a fence open at limit {limit}:\n{kept}"
        rows = [line for line in kept.splitlines() if line.startswith("|")]
        assert len(rows) in (0, 3), f"cut through a table at limit {limit}: {rows}"
        assert not kept.rstrip().splitlines()[-1].lstrip().startswith("#"), (
            f"cut left a heading with no body at limit {limit}: {kept!r}")
    assert seen_shorter

    # A fence that opens anywhere but the first line still needs its closing line
    # reserved. Reserving only for a first-line fence appended the close out of a
    # budget that was already spent, and the notes came back over the limit.
    late = "intro line\n```console\n" + "".join(f"$ line {n}\n" for n in range(50)) + "```"
    for limit in range(len(footer.encode()) + 20, len(late.encode())):
        cut = shorten(late, footer, limit)
        assert len(cut.encode("utf-8")) <= limit, (limit, len(cut.encode("utf-8")))
        assert cut[: -len(footer)].count("```") % 2 == 0, cut

    # The last resort inside the last resort: not one whole line fits, so the cut
    # lands on a word — and a fence open at that point must still be closed, or the
    # pointer below renders as code. Unreachable at 125,000 (it needs a single line
    # of 124KB) and live under a smaller --limit, which is where a stated rule that
    # holds only where convenient goes unnoticed.
    one_line = "```console\n$ rig-wb " + "release --dry-run --and-more " * 40
    for limit in range(len(footer.encode()) + 10, 400):
        cut = shorten(one_line, footer, limit)
        assert len(cut.encode("utf-8")) <= limit, (limit, len(cut.encode("utf-8")))
        assert cut[: -len(footer)].count("```") % 2 == 0, (limit, cut)

    # Bytes, not code points, and the difference is three to one here. A budget
    # counted in characters keeps three times too much of this and hands GitHub a
    # body over the limit the budget exists to respect — which is the one decision
    # the module docstring argues hardest for and nothing else pins. Repeated
    # ideographs rather than sentences: what is under test is the width of the
    # encoding, and no prose belongs in a fixture that only needs three-byte
    # characters.
    wide = "\n\n".join("\u6f22" * 60 for _ in range(40))
    assert len(wide.encode("utf-8")) > 2.9 * len(wide)
    for limit in (700, 1500, 4000):
        cut = shorten(wide, footer, limit)
        assert len(cut.encode("utf-8")) <= limit, (limit, len(cut.encode("utf-8")))
        assert len(cut) > len(footer), "the cut kept nothing at all"


def test_the_cut_never_publishes_a_heading_with_nothing_under_it():
    """The pop that prevents a dangling heading used to put one back.

    When every block that fits is a heading, the fallback hard-cut ran on
    `blocks[0]` — which is that heading — so the branch written to avoid a lone
    `###` produced exactly that. The budget goes to the first block with a body
    instead: a paragraph cut at a line says something, a bare heading says nothing,
    and the footer under it explains either.
    """
    from rig_workbench.release_notes import shorten

    body = "### Breaking\n\n" + "A paragraph of real content. " * 40
    footer = "\n\n---\n\nshortened."
    for limit in range(len(footer.encode()) + 30, len(body.encode())):
        kept = shorten(body, footer, limit)[: -len(footer)]
        assert len(kept.encode("utf-8")) + len(footer.encode("utf-8")) <= limit
        assert "A paragraph" in kept, f"only the heading survived at limit {limit}: {kept!r}"


@pytest.mark.skipif(shutil.which("bash") is None, reason="the step is a bash script")
@pytest.mark.parametrize("ref_type,annotated", [
    ("branch", False),
    ("tag", False),
    ("tag", True),
])
def test_the_target_step_resolves_a_commit_whatever_the_ref_points_at(
    tmp_path, ref_type, annotated,
):
    """`target` must be a commit, because the state step compares it against one.

    The tag path used to publish `GITHUB_SHA` unpeeled and compare it against a sha
    the next step reaches by dereferencing an annotated tag — one side peeled, the
    other assumed. If `GITHUB_SHA` on an annotated `v*` push is the tag object, that
    comparison fails and the job refuses a release the old workflow published
    without trouble: a new failure introduced by the fix. Peeling both sides with
    `^{commit}` retires the question instead of answering it, and the third row is
    the one that would notice if the peel went away — `GITHUB_SHA` is handed the tag
    object there, exactly what that push would set if it sets it.
    """
    runner, commits, objects = _checkout(
        tmp_path, {"v9.9.9": ("target", "annotated" if annotated else "lightweight")})
    (runner / ".claude-plugin").mkdir()
    (runner / ".claude-plugin" / "plugin.json").write_text(
        '{"version": "9.9.9"}', encoding="utf-8")
    output = runner / "github_output"
    output.write_text("", encoding="utf-8")

    sha = objects["v9.9.9"] if ref_type == "tag" else commits["target"]
    if annotated:
        assert sha != commits["target"], "the fixture must hand over a tag object"
    result = _run(_body("Resolve target version and ref"), runner, {
        "GITHUB_REF_TYPE": ref_type, "GITHUB_REF_NAME": "v9.9.9" if ref_type == "tag"
        else "master", "GITHUB_SHA": sha, "GITHUB_OUTPUT": str(output)})
    assert result.returncode == 0, result.stdout + result.stderr
    outputs = _outputs(output)
    assert outputs == {"version": "9.9.9", "tag": "v9.9.9",
                       "target": commits["target"]}, outputs


@pytest.mark.skipif(shutil.which("bash") is None, reason="the step is a bash script")
@pytest.mark.parametrize("version,ok", [
    ("9.9.9", True),
    ("9.9.9-rc.1", True),
    ("9.9.9\nevil=1", False),
    ("9.9.9; id", False),
    ("not-a-version", False),
])
def test_the_target_step_refuses_a_version_that_is_not_a_version(tmp_path, version, ok):
    """`plugin.json` is repository content, and this value reaches `$GITHUB_OUTPUT`.

    A newline inside the version string writes a second output line of the author's
    choosing, which every later step then reads as the workflow's own. Refusing the
    value is one check; escaping it would be three, at three call sites, one of which
    is a git refspec.
    """
    runner, commits, _objects = _checkout(tmp_path, {})
    (runner / ".claude-plugin").mkdir()
    (runner / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"version": version}), encoding="utf-8")
    output = runner / "github_output"
    output.write_text("", encoding="utf-8")

    result = _run(_body("Resolve target version and ref"), runner, {
        "GITHUB_REF_TYPE": "branch", "GITHUB_REF_NAME": "master",
        "GITHUB_SHA": commits["target"], "GITHUB_OUTPUT": str(output)})
    if ok:
        assert result.returncode == 0, result.stdout + result.stderr
        assert _outputs(output)["version"] == version
    else:
        assert result.returncode == 1, result.stdout + result.stderr
        assert "::error::" in result.stdout and "not a version number" in result.stdout
        assert output.read_text(encoding="utf-8") == "", "nothing may be written first"


@pytest.mark.skipif(shutil.which("bash") is None, reason="the step is a bash script")
@pytest.mark.parametrize("ref_type,release,tag,expected", [
    # the ordinary settled state, and the ordinary first release
    ("branch", True, ("target", "lightweight"), {"code": 0, "skip": "true", "warns": False}),
    ("branch", False, None, {"code": 0, "skip": "false", "tag_state": "absent",
                             "warns": False}),
    # the v3.0.0 state: the tag exists, the release does not
    ("branch", False, ("target", "lightweight"),
     {"code": 0, "skip": "false", "tag_state": "present", "warns": True}),
    # the same, annotated. Without `^{commit}` the ref resolves to the tag object and
    # this row takes the refusal branch below instead — a release the base published
    # without trouble, refused by its own fix.
    ("branch", False, ("target", "annotated"),
     {"code": 0, "skip": "false", "tag_state": "present", "warns": True}),
    # a tag naming some other commit: not a half-finished run, and not ours to move
    ("branch", False, ("other", "lightweight"), {"code": 1}),
    ("branch", False, ("other", "annotated"), {"code": 1}),
    # the `tags: ['v*']` path shares every one of these steps, and there the tag always
    # exists before the job starts. Same branch, deliberately quieter report: calling a
    # hand-pushed tag a failed run teaches a maintainer to ignore the warning, and then
    # it is not there when it means something.
    ("tag", False, ("target", "annotated"),
     {"code": 0, "skip": "false", "tag_state": "present", "warns": False}),
    # a release whose tag has gone. Nothing else would ever mention it: this run skips.
    ("branch", True, None, {"code": 0, "skip": "true", "warns": True,
                            "says": "the tag does not"}),
])
def test_the_state_step_tells_no_tag_from_an_orphan_tag_from_a_tag_somewhere_else(
    tmp_path, ref_type, release, tag, expected,
):
    """The path nobody had run: the tag exists, the release does not.

    `gh release create` creates the tag first, so the failed v3.0.0 run left
    `v3.0.0` at `ebe2621` with no release — and the old idempotence check asked only
    `gh release view`, so a re-run went straight back to `gh release create` with the
    tag already there. These rows are the states that check has to tell apart,
    driven against real tags in a real repository because the difference between two
    of them is a `^{commit}` peel that a stub would have to be told to model.
    """
    runner, commits, _objects = _checkout(tmp_path, {"v9.9.9": tag} if tag else {})
    output, summary = runner / "out", runner / "summary"
    output.write_text("", encoding="utf-8")
    summary.write_text("", encoding="utf-8")

    result = _run(_body("Resolve release and tag state"), runner, {
        "TAG": "v9.9.9", "TARGET": commits["target"], "GITHUB_REF_TYPE": ref_type,
        "GITHUB_OUTPUT": str(output), "GITHUB_STEP_SUMMARY": str(summary),
        "GH_TOKEN": "stub", "GH_STUB_LOG": str(runner / "gh.log"),
        "GH_STUB_STATE": json.dumps({"release": release}),
    }, _gh_stub(runner / "bin"))

    assert result.returncode == expected["code"], result.stdout + result.stderr
    outputs = _outputs(output)
    for key in ("skip", "tag_state"):
        if key in expected:
            assert outputs.get(key) == expected[key], (outputs, result.stdout)
    if expected["code"] == 1:
        # Refused, and legibly: both commits named, so a maintainer can see which is
        # which without going to look them up.
        assert "::error::" in result.stdout, result.stdout
        assert commits["other"] in result.stdout and commits["target"] in result.stdout
    assert ("::warning::" in result.stdout) is expected.get("warns", False), result.stdout
    if expected.get("says"):
        assert expected["says"] in result.stdout, result.stdout
    if expected.get("tag_state") == "present":
        # Healed, but on the master path never silently: nothing but this workflow
        # creates tags there, so an orphan one is the trace of a run that died, and it
        # is worth reporting even though this run goes on to fix it.
        assert ("v9.9.9" in summary.read_text(encoding="utf-8")) is expected["warns"]


@pytest.mark.skipif(shutil.which("bash") is None, reason="the step is a bash script")
@pytest.mark.parametrize("tag_state,source,targeted,flag", [
    ("absent", "changelog", True, "--notes-file"),
    ("present", "changelog", False, "--notes-file"),
    ("present", "auto", False, "--generate-notes"),
])
def test_the_create_step_hands_no_target_to_a_tag_that_is_already_placed(
    tmp_path, tag_state, source, targeted, flag,
):
    """`--target` is `target_commitish`, and it has no business in the orphan case.

    Measured, not assumed: `gh release create` issues exactly one request, a
    `POST /repos/{o}/{r}/releases`, and it never looks the tag up first — so
    whatever GitHub does with a `tag_name` that is already a tag, it does on the
    strength of that one body. `--target` is copied into it verbatim and omitting
    the flag omits the field. With the tag already placed there is nothing for it to
    decide, so it is left off rather than passed and ignored: the request then
    carries nothing that could be read as an instruction about where the tag goes.
    """
    # The fixture carries the tag on both rows because this step reads the tag only
    # *after* the call: on the `absent` row `gh release create` is what puts it there,
    # and the verification at the end of the step is the only read.
    runner, commits, _objects = _checkout(tmp_path, {"v9.9.9": ("target", "lightweight")})
    log = runner / "gh.log"
    result = _run(_body("Create tag + release"), runner, {
        "TAG": "v9.9.9", "TARGET": commits["target"], "TAG_STATE": tag_state,
        "SOURCE": source, "GH_TOKEN": "stub", "GH_STUB_LOG": str(log),
        "GH_STUB_STATE": json.dumps({"release": False}),
    }, _gh_stub(runner / "bin"))
    assert result.returncode == 0, result.stdout + result.stderr

    calls = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    create = next(c for c in calls if c[:2] == ["release", "create"])
    assert ("--target" in create) is targeted, create
    assert flag in create, create
    assert [f for f in ("--notes-file", "--generate-notes") if f in create] == [flag]
    if targeted:
        assert create[create.index("--target") + 1] == commits["target"]
    assert "::notice::Created" in result.stdout


@pytest.mark.skipif(shutil.which("bash") is None, reason="the step is a bash script")
@pytest.mark.parametrize("after,says", [
    (("other", "lightweight"), "moved the tag"),
    (("other", "annotated"), "moved the tag"),
    (None, "does not exist after"),
])
def test_the_create_step_fails_the_job_if_the_tag_is_not_where_it_should_be(
    tmp_path, after, says,
):
    """The other half of that verification: it has to actually fail when it is wrong.

    A check that cannot fail is a comment. The tag in the fixture ends up somewhere
    other than the target — which is the one behaviour of the API this suite cannot
    reach a real repository to rule out — and the step must report it as a job
    failure with the release left in place, rather than printing "Created". The
    annotated row is here for the same reason as in the state step: the peel is what
    makes the two shas comparable, and without it every annotated tag reads as moved.
    """
    runner, commits, _objects = _checkout(tmp_path, {"v9.9.9": after} if after else {})
    log = runner / "gh.log"
    result = _run(_body("Create tag + release"), runner, {
        "TAG": "v9.9.9", "TARGET": commits["target"], "TAG_STATE": "present",
        "SOURCE": "changelog", "GH_TOKEN": "stub", "GH_STUB_LOG": str(log),
        "GH_STUB_STATE": json.dumps({"release": False}),
    }, _gh_stub(runner / "bin"))
    assert result.returncode == 1, result.stdout + result.stderr
    assert "::error::" in result.stdout and says in result.stdout, result.stdout
    assert "::notice::Created" not in result.stdout


@pytest.mark.skipif(shutil.which("bash") is None, reason="the step is a bash script")
def test_the_state_step_reads_the_remote_not_the_checkouts_copy_of_it(tmp_path):
    """A tag deleted after checkout, which is where the two readings disagree.

    Every other fixture here has a clone that agrees with its origin, so the
    `--prune --prune-tags` on the step's fetch is a rule stated in a comment that
    nothing holds: delete the flags and the suite stays green. It is not cosmetic.
    Without pruning the deleted tag is still sitting in the checkout, the step reports
    `tag_state=present`, and — worse than the wrong state — it prints "a previous run
    created the tag and then failed", which did not happen. A wrong diagnosis sends a
    maintainer to look in the wrong place, the same way conflating a 403 with a 404
    would have.

    `fetch-depth: 0` is asserted here rather than anywhere else because it is the same
    contract from the other end: `_checkout` fetches every tag because the workflow
    asks the action to, and both this step and the post-create check say in their
    comments that the checkout holds every tag. tests/test_eval_workflow_contract.py
    pins the same line on validate.yml for the same reason — but as a substring of the
    file, and that is not enough here: two of those comments contain the words
    `fetch-depth: 0`, so a grep passes with the setting itself changed to 1. The value
    is read off the parsed step instead.
    """
    import yaml

    document = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    checkout = next(step for step in document["jobs"]["release"]["steps"]
                    if str(step.get("uses", "")).startswith("actions/checkout"))
    assert checkout["with"]["fetch-depth"] == 0, checkout

    runner, commits, _objects = _checkout(tmp_path, {"v9.9.9": ("target", "lightweight")})
    # The tag was there at checkout and the clone still has it...
    assert _git(runner, "rev-parse", "--verify", "--quiet", "refs/tags/v9.9.9^{commit}")
    # ...and then it went from origin, which is the only copy that counts.
    _git(tmp_path / "origin.git", "update-ref", "-d", "refs/tags/v9.9.9")

    output, summary = runner / "out", runner / "summary"
    output.write_text("", encoding="utf-8")
    summary.write_text("", encoding="utf-8")
    result = _run(_body("Resolve release and tag state"), runner, {
        "TAG": "v9.9.9", "TARGET": commits["target"], "GITHUB_REF_TYPE": "branch",
        "GITHUB_OUTPUT": str(output), "GITHUB_STEP_SUMMARY": str(summary),
        "GH_TOKEN": "stub", "GH_STUB_LOG": str(runner / "gh.log"),
        "GH_STUB_STATE": json.dumps({"release": False}),
    }, _gh_stub(runner / "bin"))

    assert result.returncode == 0, result.stdout + result.stderr
    assert _outputs(output)["tag_state"] == "absent", (_outputs(output), result.stdout)
    assert "::warning::" not in result.stdout, result.stdout
    assert "previous run created the tag" not in result.stdout, result.stdout
    assert summary.read_text(encoding="utf-8") == ""


@pytest.mark.skipif(shutil.which("bash") is None, reason="the step is a bash script")
def test_the_shortened_warning_names_the_unit_of_every_number_it_prints(tmp_path):
    """Both units, in the two places a person actually reads them.

    The rule is argued at length in `build_notes` and pinned only in the status dict,
    so reverting either rendering to characters alone survives. Both renderings are
    the sentence a maintainer sees: the workflow warning, and the footer on the
    release page. Either one saying "77038 characters, over the 125000-character
    limit" of a CJK entry is a claim the reader can see is false, and a reader who has
    caught the tool being wrong once reads the rest of it differently.
    """
    summary = tmp_path / "summary"
    summary.write_text("", encoding="utf-8")
    result = _run(_body("Report what the notes carry"), tmp_path, {
        "VERSION": "9.9.9", "SOURCE": "changelog", "SHORTENED": "true",
        "ENTRY_CHARS": "77038", "ENTRY_BYTES": "230638",
        "NOTES_CHARS": "41000", "NOTES_BYTES": "124990",
        "GITHUB_STEP_SUMMARY": str(summary)})
    assert result.returncode == 0, result.stdout + result.stderr
    warning = next(line for line in result.stdout.splitlines()
                   if line.startswith("::warning::"))
    # The number the budget is counted in leads, and it is the one over the limit.
    assert "230638 UTF-8 bytes" in warning, warning
    assert "77038 characters" in warning, warning
    assert "124990" in warning and "41000" in warning, warning
    assert warning.index("230638") < warning.index("77038"), warning
    written = summary.read_text(encoding="utf-8")
    assert "230638 -> 124990 bytes" in written and "77038 -> 41000 characters" in written
