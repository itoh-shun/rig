"""Evidence-anchor extraction and resolution (`workbench/anchors.py`).

Covers: the whole-span backtick grammar (including the quotes and
compiler/URL lookalikes it must NOT extract), body positions, and the five
resolution outcomes the plan calls for — resolves in the worktree, missing
everywhere, line past the end of the file, reversed range, and a file deleted
in the worktree but alive at the base commit — plus the skip categories
(binary, symlink, generated) and the path/tree traps that would otherwise
resolve silently green.

Also the loudest of those silences: a body holding no anchor at all, which is
its own `no_anchors` finding rather than a clean scan, and the body fingerprint
that lets `stream-checks --watch` notice one being written.
"""

import os
import pathlib
import re
import subprocess
import sys

import pytest

from rig_workbench.workbench.anchors import (MAX_FILE_BYTES, NO_ANCHORS_KIND,
                                             RESOLVED, SKIPPED, UNRESOLVED,
                                             bodies_fingerprint,
                                             extract_anchors, resolve_anchor,
                                             scan_bodies, scan_body)


def _git(repo, *args):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
                   cwd=repo, check=True, capture_output=True, text=True)


def make_repo(tmp_path):
    """Scratch repo whose base commit holds a text file, a doc, a file the task
    will delete, a symlink and a binary — the surfaces resolution has to tell
    apart."""
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "dist").mkdir(parents=True)
    _git(repo, "init", "-q")
    (repo / "src" / "app.py").write_text("".join(f"line {i}\n" for i in range(1, 6)),
                                         encoding="utf-8")
    # A directory one level down, so the "anchor pointing at a directory" trap
    # can still be written path-shaped — a bare `src:1` is no longer an anchor.
    (repo / "src" / "sub").mkdir()
    (repo / "src" / "sub" / "inner.py").write_text("inner\n", encoding="utf-8")
    (repo / "legacy.py").write_text("".join(f"old {i}\n" for i in range(1, 21)),
                                    encoding="utf-8")
    (repo / "dist" / "bundle.js").write_text("var x = 1\n", encoding="utf-8")
    # Invalid UTF-8 *and* a NUL: `git show` on this either raises inside the
    # text-mode git() helper or hands back a NUL-bearing string, and both
    # paths must end in a skip rather than a traceback.
    (repo / "icon.png").write_bytes(b"\x89PNG\r\n\x1a\n\x00\xff\xfe" + b"\x00" * 32)
    (repo / "link.py").symlink_to("src/app.py")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, check=True,
                         capture_output=True, text=True).stdout.strip()
    return repo, sha


def commit(repo, msg="edit"):
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", msg)


def anchor(raw):
    """The single anchor in a backticked span — extraction is the only way to
    build one, so every resolution test exercises the grammar too."""
    (a,) = extract_anchors(f"`{raw}`")
    return a


@pytest.fixture
def repo(tmp_path):
    return make_repo(tmp_path)


# ── extraction ────────────────────────────────────────────────────────────────
def test_extracts_single_line_and_range_anchors_with_body_positions():
    body = (
        "根拠:\n"
        "1. 握りつぶしている（`src/app.py:42`）\n"
        "2. 同じ分岐が二重（`web/other.ts:10-18`）\n"
    )
    first, second = extract_anchors(body)
    assert (first.raw, first.path, first.start, first.end) == (
        "src/app.py:42", "src/app.py", 42, 42)
    assert (second.raw, second.path, second.start, second.end) == (
        "web/other.ts:10-18", "web/other.ts", 10, 18)
    assert (first.body_line, second.body_line) == (2, 3)
    assert body[first.body_offset:].startswith("`src/app.py:42`")
    assert body[second.body_offset:].startswith("`web/other.ts:10-18`")


def test_empty_body_and_body_without_anchors_yield_nothing():
    assert extract_anchors("") == []
    assert extract_anchors("判定: APPROVE\n確信度: 高\n") == []


@pytest.mark.parametrize("body", [
    # The contract's other evidence form for prose: a short quote. Adversarial
    # on purpose — a backticked quote that *contains* something anchor-shaped.
    '3. 該当箇所の短い引用（`raise RuntimeError(f"{path}:{lineno}")`）',
    "3. コンパイラ出力をそのまま引用（`main.go:42:5: undefined: x`）",
    "3. 参照先（`https://example.com:8080/docs`）",
    "3. 引用（`エラー時は握りつぶす — app.py の 42 行目`）",
    "3. 素の散文に埋もれた src/app.py:42 はアンカーとして数えない",
])
def test_quotes_and_lookalikes_are_not_anchors(body):
    assert extract_anchors(body) == []


def test_anchor_next_to_a_quote_is_still_extracted():
    body = '1. （`src/app.py:3`）\n2. 引用（`if err != nil { return }`）\n'
    (only,) = extract_anchors(body)
    assert only.raw == "src/app.py:3"


@pytest.mark.parametrize("body", [
    # A bare word plus a number is prose in the shape of an anchor. `docs` is a
    # real directory in many repos, so this used to resolve far enough to become
    # a fail-grade `not_a_file` finding against a reviewer who wrote no anchor.
    "3. `docs:12` を参照",
    "3. 終了コード（`exit:0`）",
])
def test_bare_tokens_are_not_anchors(body):
    assert extract_anchors(body) == []


@pytest.mark.parametrize("body", ["`Makefile:12`", "`Dockerfile:7`", "`LICENSE:1`"])
def test_known_cost_extensionless_filenames_at_the_root_are_lost(body):
    """Debt, not a decision to be proud of: the rule that keeps `docs:12` from
    becoming a finding cannot tell a bare directory name from a real
    extensionless file at the repo root. Those anchors are silently not
    anchors, which shows up as `no_anchors` rather than as an accusation —
    the safe direction, but a real miss. Lifted by basename resolution
    (phase B), which knows what actually exists in the tree."""
    assert extract_anchors(body) == []
    # …while the same file cited with a path still works
    assert extract_anchors("`build/Makefile:12`")[0].path == "build/Makefile"


@pytest.mark.parametrize("raw", ["README.md:12", ".gitignore:3", "src/app.py:1", "src/pkg/x:4"])
def test_path_shaped_anchors_are_still_extracted(raw):
    """The bare-token rule must not cost a legitimate anchor: an extension is
    enough on its own, and so is a `/`."""
    (a,) = extract_anchors(f"1. （`{raw}`）")
    assert a.raw == raw


def test_a_directory_name_alone_is_not_a_finding(repo):
    """The regression the rule exists for, end to end: a body citing a bare
    directory name produces no anchor — and therefore no accusation."""
    wt, sha = repo
    scan = scan_body("1. `src` 配下（`src:1`）を参照\n", "reviews/design.md", wt, sha)
    assert scan.n_anchors == 0
    assert [f["kind"] for f in scan.findings] == [NO_ANCHORS_KIND]


# ── resolution: the five cases from the plan ──────────────────────────────────
def test_resolves_against_the_worktree(repo):
    wt, sha = repo
    res = resolve_anchor(wt, sha, anchor("src/app.py:3"))
    assert (res.status, res.source, res.ok) == (RESOLVED, "worktree", True)


def test_range_within_the_file_resolves(repo):
    wt, sha = repo
    res = resolve_anchor(wt, sha, anchor("src/app.py:2-5"))
    assert (res.status, res.source) == (RESOLVED, "worktree")


def test_missing_everywhere_is_unresolved(repo):
    wt, sha = repo
    res = resolve_anchor(wt, sha, anchor("src/nope.py:1"))
    assert (res.status, res.reason) == (UNRESOLVED, "missing")
    assert "src/nope.py" in res.detail


def test_line_past_the_end_of_the_file_is_unresolved(repo):
    """Out of range in the worktree *and* at the base commit — the base pass
    rescues a truncated line, so a file that never had line 99 is the only
    honest way to assert this outcome."""
    wt, sha = repo
    res = resolve_anchor(wt, sha, anchor("src/app.py:99"))
    assert (res.status, res.reason) == (UNRESOLVED, "line_out_of_range")
    assert "5 line(s) in the worktree" in res.detail
    end_of_range = resolve_anchor(wt, sha, anchor("src/app.py:4-6"))
    assert end_of_range.reason == "line_out_of_range"


def test_reversed_range_is_unresolved_without_touching_the_disk(repo):
    wt, sha = repo
    res = resolve_anchor(wt, sha, anchor("src/app.py:20-10"))
    assert (res.status, res.reason, res.source) == (UNRESOLVED, "reversed_range", None)


def test_file_deleted_in_the_worktree_resolves_at_the_base_commit(repo):
    wt, sha = repo
    (wt / "legacy.py").unlink()
    commit(wt, "drop legacy")
    res = resolve_anchor(wt, sha, anchor("legacy.py:12"))
    assert (res.status, res.source) == (RESOLVED, "base")
    assert "base commit" in res.detail


def test_renamed_file_resolves_at_the_base_commit(repo):
    wt, sha = repo
    (wt / "legacy.py").rename(wt / "src" / "renamed.py")
    commit(wt, "rename legacy")
    assert resolve_anchor(wt, sha, anchor("legacy.py:20")).source == "base"


def test_line_truncated_away_by_the_diff_resolves_at_the_base_commit(repo):
    """The worktree copy is now shorter than the cited line. Same false-positive
    class as a deleted file, so the base pass rescues it too."""
    wt, sha = repo
    (wt / "legacy.py").write_text("old 1\n", encoding="utf-8")
    commit(wt, "truncate")
    res = resolve_anchor(wt, sha, anchor("legacy.py:15"))
    assert (res.status, res.source) == (RESOLVED, "base")


def test_without_a_base_commit_only_the_worktree_is_consulted(repo):
    wt, _sha = repo
    (wt / "legacy.py").unlink()
    res = resolve_anchor(wt, "", anchor("legacy.py:12"))
    assert (res.status, res.reason) == (UNRESOLVED, "missing")
    assert "base commit" not in res.detail


# ── skips: reported with a reason, never silently green ───────────────────────
def test_binary_in_the_worktree_is_skipped(repo):
    wt, sha = repo
    res = resolve_anchor(wt, sha, anchor("icon.png:1"))
    assert (res.status, res.reason) == (SKIPPED, "binary")


def test_binary_only_at_the_base_commit_is_skipped_not_crashed(repo):
    """`state.git()` decodes in text mode, so a binary blob at base raises
    UnicodeDecodeError right through `check=False`."""
    wt, sha = repo
    (wt / "icon.png").unlink()
    commit(wt, "drop icon")
    res = resolve_anchor(wt, sha, anchor("icon.png:1"))
    assert (res.status, res.reason, res.source) == (SKIPPED, "binary", "base")


def test_oversized_file_is_skipped_on_both_passes(repo):
    """The bound has to hold at base too, or a file too large to judge in the
    worktree resolves anyway once the task deletes it."""
    wt, sha = repo
    big = wt / "huge.txt"
    big.write_text("x\n" * (MAX_FILE_BYTES // 2 + 10), encoding="utf-8")
    commit(wt, "add huge")
    at_head = resolve_anchor(wt, sha, anchor("huge.txt:5"))
    assert (at_head.status, at_head.reason, at_head.source) == (SKIPPED, "too_large", "worktree")
    big.unlink()
    commit(wt, "drop huge")
    # HEAD~1 is the commit that still holds it — the base pass must bound too.
    at_base = resolve_anchor(wt, "HEAD~1", anchor("huge.txt:5"))
    assert (at_base.status, at_base.reason, at_base.source) == (SKIPPED, "too_large", "base")


def test_symlink_is_skipped_rather_than_followed(repo):
    wt, sha = repo
    res = resolve_anchor(wt, sha, anchor("link.py:3"))
    assert (res.status, res.reason) == (SKIPPED, "symlink")


def test_generated_artifacts_are_skipped_by_path(repo):
    wt, sha = repo
    (wt / "package-lock.json").write_text('{"x": 1}\n', encoding="utf-8")
    (wt / "src" / "app.min.js").write_text("var x=1\n", encoding="utf-8")
    for raw in ("dist/bundle.js:1", "package-lock.json:1", "src/app.min.js:1"):
        res = resolve_anchor(wt, sha, anchor(raw))
        assert (res.status, res.reason) == (SKIPPED, "generated"), raw


def test_a_cited_rig_file_is_not_treated_as_generated(repo):
    """`.rig/` is skipped by the secrets walker; anchors must still resolve
    there — reviewers cite gate config."""
    wt, sha = repo
    (wt / ".rig").mkdir()
    (wt / ".rig" / "gates.json").write_text("{\n}\n", encoding="utf-8")
    assert resolve_anchor(wt, sha, anchor(".rig/gates.json:2")).status == RESOLVED


# ── traps that would otherwise pass ───────────────────────────────────────────
def test_directory_does_not_resolve_in_worktree_or_at_base(repo):
    """`git show <base>:src/sub` exits 0 and prints a tree listing; line-counting
    that would pass an anchor at a directory."""
    wt, sha = repo
    assert resolve_anchor(wt, sha, anchor("src/sub:1")).reason == "not_a_file"
    (wt / "src" / "sub" / "inner.py").unlink()
    (wt / "src" / "sub").rmdir()
    commit(wt, "drop src/sub")
    res = resolve_anchor(wt, sha, anchor("src/sub:1"))
    assert (res.status, res.reason, res.source) == (UNRESOLVED, "not_a_file", "base")


@pytest.mark.parametrize("raw", ["/etc/passwd:1", "../outside.py:1", "~/secrets.txt:1"])
def test_paths_leaving_the_worktree_are_unresolved(repo, raw):
    wt, sha = repo
    res = resolve_anchor(wt, sha, anchor(raw))
    assert (res.status, res.reason) == (UNRESOLVED, "path_escapes_worktree")


def test_symlinked_directory_component_does_not_leave_the_worktree(repo, tmp_path):
    """`..` and absolute paths are refused lexically, but a symlink at an
    *intermediate* component leaves the worktree without either. The escape has
    to be refused, and refused identically whether the outside target exists or
    not — a verdict that varied would be an existence-and-size oracle for files
    outside the worktree."""
    wt, sha = repo
    outside = tmp_path / "outside"
    (outside / "sub").mkdir(parents=True)
    (outside / "sub" / "secret.txt").write_text("".join(f"x{i}\n" for i in range(200)),
                                                encoding="utf-8")
    (wt / "escape").symlink_to(outside)

    present = resolve_anchor(wt, sha, anchor("escape/sub/secret.txt:1"))
    absent = resolve_anchor(wt, sha, anchor("escape/sub/nothing-here.txt:1"))
    assert (present.status, present.reason) == (UNRESOLVED, "path_escapes_worktree")
    # byte-identical verdicts: no line count, no "exists" signal, nothing that
    # distinguishes a 200-line file outside the worktree from a missing one
    assert (present.status, present.reason, present.source) == \
           (absent.status, absent.reason, absent.source)
    assert present.detail.replace("secret.txt", "X") == absent.detail.replace(
        "nothing-here.txt", "X")
    assert "200" not in present.detail and "line" not in present.detail


def test_a_symlinked_component_inside_the_worktree_still_resolves(repo):
    """Containment, not symlink-phobia: a link that stays inside the tree is
    fine, and the final-component symlink keeps its own documented skip."""
    wt, sha = repo
    (wt / "alias").symlink_to("src")
    assert resolve_anchor(wt, sha, anchor("alias/app.py:3")).status == RESOLVED
    assert resolve_anchor(wt, sha, anchor("link.py:3")).reason == "symlink"


def test_line_zero_is_unresolved(repo):
    wt, sha = repo
    res = resolve_anchor(wt, sha, anchor("src/app.py:0"))
    assert (res.status, res.reason) == (UNRESOLVED, "bad_line_number")


def test_worktree_is_accepted_as_a_string_path(repo):
    wt, sha = repo
    assert resolve_anchor(str(wt), sha, anchor("src/app.py:1")).status == RESOLVED


def test_empty_file_has_no_lines_to_cite(repo):
    wt, sha = repo
    (wt / "src" / "empty.py").write_text("", encoding="utf-8")
    res = resolve_anchor(wt, sha, anchor("src/empty.py:1"))
    assert (res.status, res.reason) == (UNRESOLVED, "line_out_of_range")


def test_resolution_carries_the_anchor_back(repo):
    """A finding has to name the anchor and where in the body it came from."""
    wt, sha = repo
    a = anchor("src/app.py:1")
    assert resolve_anchor(wt, sha, a).anchor is a


# ── the `scan-anchors` subcommand (T3) ────────────────────────────────────────
REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
WORKBENCH = REPO_ROOT / "scripts" / "workbench.py"


def run_cli(args, cwd, wt_root=None):
    env = dict(os.environ)
    if wt_root is not None:
        env["RIG_WORKTREE_ROOT"] = str(wt_root)
    return subprocess.run([sys.executable, str(WORKBENCH), *args],
                          capture_output=True, text=True, cwd=cwd, timeout=60, env=env)


def write_body(repo, name, text):
    p = repo / name
    p.write_text(text, encoding="utf-8")
    return p


def test_scan_anchors_reports_unresolved_anchors_and_exits_1(repo):
    wt, _sha = repo
    write_body(wt, "review.md", "1. 握りつぶし（`src/app.py:99`）\n2. 別件（`src/nope.py:1`）\n")
    r = run_cli(["scan-anchors", "review.md"], cwd=wt)
    assert r.returncode == 1, r.stdout + r.stderr
    assert r.stdout.startswith("## scan-anchors: review.md")
    assert "2 anchor(s) in 1 body file(s): 0 resolved, 2 unresolved, 0 skipped" in r.stdout
    # grade split: a line past the end of a file we found is fail, a file we
    # could not find at all is only a warning.
    assert "review.md:1 [line_out_of_range/fail]" in r.stdout
    assert "review.md:2 [missing/warning]" in r.stdout


def test_scan_anchors_exits_0_when_every_anchor_resolves(repo):
    wt, _sha = repo
    write_body(wt, "review.md", "1. （`src/app.py:3`）\n2. （`legacy.py:2-4`）\n")
    r = run_cli(["scan-anchors", "review.md"], cwd=wt)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "2 anchor(s) in 1 body file(s): 2 resolved, 0 unresolved, 0 skipped" in r.stdout
    assert "No unresolved evidence anchors found." in r.stdout


def test_scan_anchors_prints_skips_without_failing(repo):
    """A citation into a lockfile is not a reviewer error, so it must not drive
    the exit code — but it must still be printed and counted."""
    wt, _sha = repo
    write_body(wt, "review.md", "1. （`dist/bundle.js:1`）\n2. （`link.py:3`）\n")
    r = run_cli(["scan-anchors", "review.md"], cwd=wt)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "0 resolved, 0 unresolved, 2 skipped" in r.stdout
    assert "[generated]" in r.stdout and "[symlink]" in r.stdout
    assert "/warning]" not in r.stdout and "/fail]" not in r.stdout  # skips carry no grade


def test_scan_anchors_walks_only_markdown_under_a_directory(repo):
    wt, _sha = repo
    (wt / "bodies").mkdir()
    write_body(wt, "bodies/security.md", "（`src/app.py:99`）\n")
    write_body(wt, "bodies/notes.txt", "（`src/app.py:99`）\n")
    r = run_cli(["scan-anchors", "bodies"], cwd=wt)
    assert r.returncode == 1
    assert "1 anchor(s) in 1 body file(s)" in r.stdout


def test_scan_anchors_without_a_scope_refuses_rather_than_guessing(repo):
    """No default scope on purpose: anchors resolve from a worktree root, and
    every plausible default would resolve prose against a tree it was not
    written for and report the mismatch as a reviewer error."""
    wt, _sha = repo
    r = run_cli(["scan-anchors"], cwd=wt)
    assert r.returncode != 0
    assert "--diff <task-id>" in r.stderr


def test_scan_anchors_refuses_paths_and_diff_together(repo):
    wt, _sha = repo
    r = run_cli(["scan-anchors", "review.md", "--diff", "some-task"], cwd=wt)
    assert r.returncode != 0
    assert "not both" in r.stderr


# ── --diff <task-id>: a task's recorded review bodies (the gate's scope) ──────
def test_scan_anchors_diff_scans_the_tasks_recorded_review_bodies(tmp_path):
    """End to end: `review --body` records the prose, `scan-anchors --diff`
    resolves its anchors against that task's own worktree."""
    repo, _sha = make_repo(tmp_path)
    wt_root = tmp_path / "wt"
    # `--type feature`, not `review`: review-type tasks are registered without a
    # worktree, and the reviews this sensor exists for are the fan-out recorded
    # against the implementation task whose worktree the anchors point into.
    r = run_cli(["new", "add a thing", "--type", "feature", "--slug", "thing"],
                cwd=repo, wt_root=wt_root)
    assert r.returncode == 0, r.stderr
    task_id = re.search(r"task_id: (\S+)", r.stdout).group(1)

    body = tmp_path / "security.md"
    body.write_text("判定: REJECT\n1. 実在（`src/app.py:3`）\n2. 行超過（`src/app.py:99`）\n",
                    encoding="utf-8")
    # A second verdict with no body at all: `--body` cannot name every persona
    # (its filename guard), so reviews/ is the population, not review.json.
    r = run_cli(["review", task_id, "--set", "security=REJECT", "--set", "design=APPROVE",
                 "--body", f"security=@{body}"], cwd=repo, wt_root=wt_root)
    assert r.returncode == 0, r.stderr

    r = run_cli(["scan-anchors", "--diff", task_id], cwd=repo, wt_root=wt_root)
    assert r.returncode == 1, r.stdout + r.stderr
    assert f"review bodies of task {task_id} (1 file(s), worktree vs " in r.stdout
    assert "2 anchor(s) in 1 body file(s): 1 resolved, 1 unresolved" in r.stdout
    assert "reviews/security.md:3 [line_out_of_range/fail]" in r.stdout


def test_scan_anchors_diff_with_no_recorded_bodies_is_quiet_and_green(tmp_path):
    repo, _sha = make_repo(tmp_path)
    wt_root = tmp_path / "wt"
    r = run_cli(["new", "add a thing", "--type", "feature", "--slug", "thing"],
                cwd=repo, wt_root=wt_root)
    task_id = re.search(r"task_id: (\S+)", r.stdout).group(1)
    r = run_cli(["scan-anchors", "--diff", task_id], cwd=repo, wt_root=wt_root)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "No review bodies recorded." in r.stdout


# ── a body with no anchors at all (T5a) ───────────────────────────────────────
# The silence this whole sensor exists to prevent: a reviewer body holding zero
# anchors used to scan clean, which is a sensor reporting green on something it
# never inspected — the `lint-skin.py` failure the design brief names.
ANCHORLESS = "判定: APPROVE\n確信度: 高\n根拠:\n1. 全体的に問題なさそう\n2. テストも通っている\n"


def test_a_body_without_anchors_is_exactly_one_no_anchors_finding(repo):
    wt, sha = repo
    scan = scan_body(ANCHORLESS, "reviews/design.md", wt, sha)
    assert len(scan.findings) == 1
    (f,) = scan.findings
    assert (f["kind"], f["grade"], f["path"]) == (NO_ANCHORS_KIND, "warning", "reviews/design.md")
    assert f["line"] == 0  # the finding is about the body, not a place in the code
    assert (scan.n_anchors, scan.n_resolved, scan.n_bodies_without_anchors) == (0, 0, 1)
    assert scan.skipped == []


def test_an_anchorless_body_is_not_counted_as_an_unresolved_anchor(repo):
    """`findings` carries two populations; the summary must not report the
    absence of an anchor as an anchor that failed to resolve."""
    wt, sha = repo
    scan = scan_body(ANCHORLESS, "reviews/design.md", wt, sha)
    assert scan.summary() == ("0 anchor(s) in 1 body file(s): 0 resolved, 0 unresolved, "
                              "0 skipped, 1 body file(s) with no anchors")


def test_a_body_whose_anchors_were_all_skipped_is_not_called_anchorless(repo):
    """It cited something; the citation was into a lockfile. That is the
    documented skip, reported with its own reason — not this finding."""
    wt, _sha = repo
    (wt / "package-lock.json").write_text('{"x": 1}\n', encoding="utf-8")
    scan = scan_body("1. （`package-lock.json:1`）\n", "reviews/security.md", wt, "")
    assert scan.findings == []
    assert [s["kind"] for s in scan.skipped] == ["generated"]
    assert scan.n_bodies_without_anchors == 0


def test_finding_excerpts_never_carry_invisible_or_control_characters(repo):
    """Reviewer bodies are LLM output, and a finding built from one is printed
    to a terminal and stored in acceptance.json. The anchor grammar admits
    anything that is not whitespace/backtick/colon, so both a zero-width space
    and an ESC can ride into an excerpt — the injection sensor's renderer is
    shared to escape them (`<U+XXXX>`)."""
    wt, sha = repo
    zwsp = "\u200b"  # escaped, never literal: this file is itself scanned
    body = "1. （`sr" + zwsp + "c/app.py:99`）\n2. （`src/\x1b[2Kapp.py:99`）\n"
    scan = scan_body(body, "reviews/security.md", wt, sha)
    excerpts = " ".join(f["excerpt"] for f in scan.findings)
    assert "<U+200B>" in excerpts and "<U+001B>" in excerpts
    assert zwsp not in excerpts and "\x1b" not in excerpts


def test_a_symlinked_review_body_is_skipped_not_followed(repo, tmp_path):
    """`review_bodies` finds bodies with is_file(), which follows links: a
    symlink dropped into reviews/ would otherwise have its target read as
    reviewer prose. Skipped with a reason, never silently dropped."""
    wt, sha = repo
    outside = tmp_path / "outside.md"
    outside.write_text("1. （`src/app.py:99`）\n", encoding="utf-8")
    (wt / "bodies").mkdir()
    (wt / "bodies" / "linked.md").symlink_to(outside)
    scan = scan_bodies([(wt / "bodies" / "linked.md", "reviews/linked.md")], wt, sha)
    assert scan.findings == []  # its contents were never read
    assert [s["kind"] for s in scan.skipped] == ["symlink_body"]
    assert scan.n_bodies == 1


def test_each_anchorless_body_is_its_own_finding(repo):
    wt, sha = repo
    (wt / "bodies").mkdir()
    for name in ("design.md", "security.md"):
        (wt / "bodies" / name).write_text(ANCHORLESS, encoding="utf-8")
    scan = scan_bodies([(wt / "bodies" / n, f"reviews/{n}") for n in ("design.md", "security.md")],
                       wt, sha)
    assert [f["path"] for f in scan.findings] == ["reviews/design.md", "reviews/security.md"]


def test_scan_anchors_does_not_report_an_anchorless_body_as_clean(repo):
    wt, _sha = repo
    write_body(wt, "review.md", ANCHORLESS)
    r = run_cli(["scan-anchors", "review.md"], cwd=wt)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "No unresolved evidence anchors found." not in r.stdout
    assert "review.md:0 [no_anchors/warning]" in r.stdout
    # ...and the report calls it what it is rather than counting a missing
    # anchor as an unresolved one
    assert "1 review body file(s) with no evidence anchor at all" in r.stdout
    assert "1 unresolved evidence anchor(s)" not in r.stdout


def test_scan_anchors_reports_both_populations_separately(repo):
    wt, _sha = repo
    (wt / "bodies").mkdir()
    write_body(wt, "bodies/security.md", "1. （`src/app.py:99`）\n")
    write_body(wt, "bodies/design.md", ANCHORLESS)
    r = run_cli(["scan-anchors", "bodies"], cwd=wt)
    assert r.returncode == 1
    assert ("1 unresolved evidence anchor(s) and 1 review body file(s) with no evidence "
            "anchor at all (1 fail-grade, 1 warning-grade):") in r.stdout


# ── bodies_fingerprint: the change signal --watch needs (T5b) ─────────────────
def test_bodies_fingerprint_moves_with_names_and_contents(tmp_path):
    run_d = tmp_path / "run"
    (run_d / "reviews").mkdir(parents=True)
    empty = bodies_fingerprint(run_d)
    assert bodies_fingerprint(tmp_path / "no-such-run") == empty  # absent dir == empty dir

    body = run_d / "reviews" / "security.md"
    body.write_text("1. （`src/app.py:3`）\n", encoding="utf-8")
    one = bodies_fingerprint(run_d)
    assert one != empty
    assert bodies_fingerprint(run_d) == one  # stable while nothing moves

    body.write_text("1. （`src/app.py:99`）\n", encoding="utf-8")
    edited = bodies_fingerprint(run_d)
    assert edited != one

    # A second body with prose identical to the first: contents alone would
    # collide, so the name is hashed too.
    (run_d / "reviews" / "design.md").write_text("1. （`src/app.py:99`）\n", encoding="utf-8")
    assert bodies_fingerprint(run_d) != edited
