"""Signed provenance via HMAC-SHA256 on accept (#299).

accept() writes .rig/runs/<task_id>/provenance.json (a signed record of what was
accepted and the gate result it was based on); `workbench.py verify-provenance
<task_id>` checks the signature and exits 1 on mismatch or tamper.
"""

import ast
import json
import pathlib
import subprocess
import sys

import pytest

from rig_workbench.workbench.state import sign_provenance, verify_provenance

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
WORKBENCH = REPO_ROOT / "scripts" / "workbench.py"


def _scoped_nodes(tree):
    """Every node in a module, paired with the name of the function it sits in.

    `<module>` covers module level, a class body, and anything else outside a `def`. Both
    `FunctionDef` and `AsyncFunctionDef` open a scope, which is the hole the first version
    of this scan had: it walked `ast.FunctionDef` only, so a reference at module level or
    inside an `async def` was invisible to it.
    """
    stack = [("<module>", tree)]
    while stack:
        scope, node = stack.pop()
        for child in ast.iter_child_nodes(node):
            yield scope, child
            inner = (child.name if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
                     else scope)
            stack.append((inner, child))


def run_cli(args, cwd):
    return subprocess.run([sys.executable, str(WORKBENCH), *args],
                          capture_output=True, text=True, cwd=cwd, timeout=60)


@pytest.fixture
def git_repo(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)
    (tmp_path / "f.txt").write_text("hello\n", encoding="utf-8")
    subprocess.run(["git", "add", "f.txt"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=tmp_path, check=True)
    return tmp_path


# ---- sign_provenance / verify_provenance (pure functions) --------------------

def test_valid_signature_verifies(tmp_path):
    record = {"task_id": "rig-1", "gate_status": "passed"}
    sig = sign_provenance(tmp_path, record)
    assert verify_provenance(tmp_path, record, sig) is True


def test_tampered_record_fails_verification(tmp_path):
    record = {"task_id": "rig-1", "gate_status": "passed"}
    sig = sign_provenance(tmp_path, record)
    tampered = {"task_id": "rig-1", "gate_status": "failed"}
    assert verify_provenance(tmp_path, tampered, sig) is False


def test_key_is_persisted_and_reused(tmp_path):
    record = {"task_id": "rig-1"}
    sig = sign_provenance(tmp_path, record)
    assert (tmp_path / ".rig" / "provenance.key").is_file()
    # A fresh call against the same root must reuse the persisted key, not mint a new one.
    assert verify_provenance(tmp_path, record, sig) is True


# ---- end-to-end via workbench.py accept / verify-provenance ------------------

def _make_acceptable_task(git_repo, task_id):
    """Everything `accept` requires except the squash itself. Call it AFTER the task's
    own commit: `evaluated_head` is the head the gate is claimed to have judged, and
    `accept`'s `gate_judged_this_head` compares it with the worktree's HEAD."""
    d = git_repo / ".rig" / "runs" / task_id
    acc = json.loads((d / "acceptance.json").read_text(encoding="utf-8"))
    for c in acc["checks"]:
        c["status"] = "passed" if c["name"] in ("no_unrelated_diff",) else "skipped"
    task = json.loads((d / "task.json").read_text(encoding="utf-8"))
    acc["evaluated_head"] = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=task["worktree_path"], check=True,
        capture_output=True, text=True).stdout.strip()
    (d / "acceptance.json").write_text(json.dumps(acc), encoding="utf-8")
    (d / "diff.md").write_text("## Summary\nx\n", encoding="utf-8")


def _commit_gitignore(git_repo):
    """Ignore `.rig/` and commit it, because `accept` requires a clean root working tree.

    Written here rather than left to `new`: `new` only *offers* to add the entry now, and
    off a terminal it declines and prints the line instead, so a test that relied on the
    side effect was committing a file that no longer appears.
    """
    (git_repo / ".gitignore").write_text(".rig/\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A", "--", ".gitignore"], cwd=git_repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "gitignore .rig/"], cwd=git_repo, check=True)


def test_accept_writes_provenance_and_verify_passes(git_repo):
    run_cli(["new", "test task", "--type", "feature"], git_repo)
    _commit_gitignore(git_repo)
    task_id = next((git_repo / ".rig" / "runs").iterdir()).name

    task = json.loads((git_repo / ".rig" / "runs" / task_id / "task.json").read_text(encoding="utf-8"))
    wt = pathlib.Path(task["worktree_path"])
    (wt / "g.txt").write_text("change\n", encoding="utf-8")
    subprocess.run(["git", "add", "g.txt"], cwd=wt, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "work"], cwd=wt, check=True)
    _make_acceptable_task(git_repo, task_id)

    r = run_cli(["accept", task_id], git_repo)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "Provenance:" in r.stdout

    prov = json.loads((git_repo / ".rig" / "runs" / task_id / "provenance.json").read_text(encoding="utf-8"))
    assert prov["algo"] == "HMAC-SHA256"
    assert prov["record"]["task_id"] == task_id
    assert prov["record"]["gate_status"] in ("passed", "passed_with_warnings", "skipped")

    r = run_cli(["verify-provenance", task_id], git_repo)
    assert r.returncode == 0
    assert "✓ valid" in r.stdout


def test_verify_provenance_detects_tampering(git_repo):
    run_cli(["new", "test task", "--type", "feature"], git_repo)
    _commit_gitignore(git_repo)
    task_id = next((git_repo / ".rig" / "runs").iterdir()).name

    task = json.loads((git_repo / ".rig" / "runs" / task_id / "task.json").read_text(encoding="utf-8"))
    wt = pathlib.Path(task["worktree_path"])
    (wt / "g.txt").write_text("change\n", encoding="utf-8")
    subprocess.run(["git", "add", "g.txt"], cwd=wt, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "work"], cwd=wt, check=True)
    _make_acceptable_task(git_repo, task_id)
    run_cli(["accept", task_id], git_repo)

    prov_path = git_repo / ".rig" / "runs" / task_id / "provenance.json"
    prov = json.loads(prov_path.read_text(encoding="utf-8"))
    prov["record"]["gate_status"] = "failed"  # tamper after the fact
    prov_path.write_text(json.dumps(prov), encoding="utf-8")

    r = run_cli(["verify-provenance", task_id], git_repo)
    assert r.returncode != 0
    assert "INVALID" in r.stdout


def test_verify_provenance_before_accept_errors(git_repo):
    run_cli(["new", "test task", "--type", "feature", "--no-worktree"], git_repo)
    task_id = next((git_repo / ".rig" / "runs").iterdir()).name
    r = run_cli(["verify-provenance", task_id], git_repo)
    assert r.returncode != 0
    assert "no provenance record" in (r.stdout + r.stderr)


def test_verify_provenance_leaves_the_repository_exactly_as_it_found_it(git_repo):
    """The registry declares `wb.verify-provenance` `effect_class="read-only"`, with an
    effect line that ends "何も書き換えません" (it rewrites nothing). This is that claim,
    checked through the command rather than assumed: run it where the key is below the floor
    — the one state where the command used to replace that key — and the key file, its bytes
    and the whole `.rig/` listing come back unchanged.
    """
    run_cli(["new", "test task", "--type", "feature"], git_repo)
    _commit_gitignore(git_repo)
    task_id = next((git_repo / ".rig" / "runs").iterdir()).name
    task = json.loads((git_repo / ".rig" / "runs" / task_id / "task.json").read_text(encoding="utf-8"))
    wt = pathlib.Path(task["worktree_path"])
    (wt / "g.txt").write_text("change\n", encoding="utf-8")
    subprocess.run(["git", "add", "g.txt"], cwd=wt, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "work"], cwd=wt, check=True)
    _make_acceptable_task(git_repo, task_id)
    assert run_cli(["accept", task_id], git_repo).returncode == 0

    key = git_repo / ".rig" / "provenance.key"
    key.write_bytes(b"12345678")

    def state(root):
        # Everything under `.rig/` except the usage log. `.rig/context.jsonl` gains a line
        # on *every* invocation, read-only ones included — that is `wb context`'s telemetry
        # and it predates all of this; it is excluded by name rather than by ignoring
        # unexpected differences, so anything else that appears fails here.
        return {p.name: (p.read_bytes() if p.is_file() else None)
                for p in sorted(root.iterdir()) if p.name != "context.jsonl"}

    before = state(git_repo / ".rig")
    r = run_cli(["verify-provenance", task_id], git_repo)
    assert r.returncode != 0                      # unverifiable is not verified
    assert state(git_repo / ".rig") == before
    assert key.read_bytes() == b"12345678"


def test_signing_with_a_key_in_hand_touches_no_filesystem(tmp_path):
    """The mechanism of the ordering fix, which nothing else pins.

    `accept` acquires the key before the squash and hands the bytes to `sign_provenance`
    precisely so that the call made *after* the squash cannot fail on the filesystem.
    Measured by review: dropping `key=` at the call site, and separately making
    `sign_provenance` ignore the argument, each passed the whole suite — the refusal test
    pins the preflight, not the parameter. A root with no `.rig/` at all is the assertion
    that cannot pass unless the bytes given are the bytes used: any path that reads or
    creates the key would have to make that directory.
    """
    from rig_workbench.workbench.state import sign_provenance

    root = tmp_path / "no-rig-directory-here"
    root.mkdir()
    digest = sign_provenance(root, {"task_id": "rig-1"}, key=b"k" * 32)
    assert len(digest) == 64 and int(digest, 16) >= 0
    assert not (root / ".rig").exists()

    # …and it is the key that was handed in, not some other one.
    import hashlib
    import hmac

    from rig_workbench.workbench.state import _provenance_payload

    assert digest == hmac.new(b"k" * 32, _provenance_payload({"task_id": "rig-1"}),
                              hashlib.sha256).hexdigest()


def test_the_record_is_signed_with_the_key_the_preflight_held(git_repo, monkeypatch):
    """The other half of the mechanism: the bytes acquired before the squash are the bytes
    that sign, so the signing step reads nothing.

    Measured by review: dropping `key=` at the call site passed the whole suite, because in
    the ordinary case the file still holds the same key by then and the two spellings agree.
    They disagree exactly when something changes the file in between — a sibling accept
    rotating it, which is the case the ordering fix exists for — so that is what this does,
    from the last step that runs before the signing.
    """
    import argparse
    import hashlib
    import hmac

    from rig_workbench.workbench import accept as accept_mod
    from rig_workbench.workbench.accept import cmd_accept
    from rig_workbench.workbench.state import _provenance_payload

    run_cli(["new", "test task", "--type", "feature"], git_repo)
    _commit_gitignore(git_repo)
    task_id = next((git_repo / ".rig" / "runs").iterdir()).name
    task = json.loads((git_repo / ".rig" / "runs" / task_id / "task.json").read_text(encoding="utf-8"))
    wt = pathlib.Path(task["worktree_path"])
    (wt / "g.txt").write_text("change\n", encoding="utf-8")
    subprocess.run(["git", "add", "g.txt"], cwd=wt, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "work"], cwd=wt, check=True)
    _make_acceptable_task(git_repo, task_id)

    key_path = git_repo / ".rig" / "provenance.key"
    held = bytes(range(32))
    key_path.parent.mkdir(parents=True, exist_ok=True)
    key_path.write_bytes(held)
    rotated = bytes(range(32, 64))
    real_record_accept = accept_mod.govern_enforce.record_accept

    def rotate_then_record(*a, **kw):
        key_path.write_bytes(rotated)          # a sibling rotates the key post-squash
        return real_record_accept(*a, **kw)

    monkeypatch.setattr(accept_mod.govern_enforce, "record_accept", rotate_then_record)
    monkeypatch.chdir(git_repo)
    cmd_accept(argparse.Namespace(task_id=task_id, force=False))

    prov = json.loads((git_repo / ".rig" / "runs" / task_id / "provenance.json")
                      .read_text(encoding="utf-8"))
    assert key_path.read_bytes() == rotated
    assert prov["signature"] == hmac.new(held, _provenance_payload(prov["record"]),
                                         hashlib.sha256).hexdigest()


def test_a_forced_accept_records_the_refusal_when_the_key_cannot_be_prepared(git_repo, monkeypatch):
    """The eighth `accept_refused` reason, exercised. Measured by review: deleting the
    branch that writes it survived the suite, because the refusal test above runs with
    `force=False` and `_audit_force_refused` only fires under a force.

    Reaching for the override has to stay visible whether or not it worked — that is what
    the other seven reasons are for, and a key that cannot be prepared is now one of them.
    """
    import argparse
    import errno

    from rig_workbench.workbench.accept import cmd_accept

    run_cli(["new", "test task", "--type", "feature"], git_repo)
    _commit_gitignore(git_repo)
    task_id = next((git_repo / ".rig" / "runs").iterdir()).name
    task = json.loads((git_repo / ".rig" / "runs" / task_id / "task.json").read_text(encoding="utf-8"))
    wt = pathlib.Path(task["worktree_path"])
    (wt / "g.txt").write_text("change\n", encoding="utf-8")
    subprocess.run(["git", "add", "g.txt"], cwd=wt, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "work"], cwd=wt, check=True)
    _make_acceptable_task(git_repo, task_id)
    # One criterion left failing, so `--force` is a real force: `_audit_force_refused` fires
    # on `soft_fail`, the requirements the flag is reaching past, and not on the flag.
    acceptance = git_repo / ".rig" / "runs" / task_id / "acceptance.json"
    acc = json.loads(acceptance.read_text(encoding="utf-8"))
    acc["checks"][0]["status"] = "failed"
    acceptance.write_text(json.dumps(acc), encoding="utf-8")

    key = git_repo / ".rig" / "provenance.key"
    key.write_bytes(b"12345678")
    real_rename = pathlib.Path.rename

    def refusing_rename(self, target):
        if "provenance.key" in self.name:
            raise OSError(errno.EACCES, "Permission denied")
        return real_rename(self, target)

    monkeypatch.setattr(pathlib.Path, "rename", refusing_rename)
    monkeypatch.chdir(git_repo)

    with pytest.raises(SystemExit) as exited:
        cmd_accept(argparse.Namespace(task_id=task_id, force=True))
    assert exited.value.code == 2

    entries = [json.loads(line) for line
               in (git_repo / ".rig" / "audit.jsonl").read_text(encoding="utf-8").splitlines()
               if line.strip()]
    assert [e["action"] for e in entries] == ["accept_refused"]
    assert entries[0]["reason"] == "provenance_key_unavailable"
    assert "will not be overwritten" in entries[0]["detail"]
    # Refused means refused: the force did not apply, either.
    assert subprocess.run(["git", "status", "--porcelain"], cwd=git_repo, check=True,
                          capture_output=True, text=True).stdout.strip() == ""
    assert not (git_repo / ".rig" / "runs" / task_id / "provenance.json").exists()
    assert key.read_bytes() == b"12345678"


def test_a_key_that_cannot_be_set_aside_refuses_the_accept_before_it_lands(git_repo, monkeypatch):
    """An error path that fires after the point of no return is the defect this whole run is
    about, and setting the key aside was one.

    `sign_provenance` is called after the squash has been applied and the ledger written, so
    a rename that fails there — measured by review as `PermissionError` on an immutable
    `.rig/`, and as `FileNotFoundError` in one of twenty-five trials with four concurrent
    accepts renaming the key from under each other — left a landed accept with no provenance
    record and no explanation. `accept` now acquires the key before the squash, where
    failing costs nothing: the refusal is exit 2, the working tree is untouched, and the key
    is still there.

    The rename is made to fail for the key path only, which is the same fault the immutable
    directory produces without needing a filesystem a test can't have. `pathlib.Path.rename`
    has exactly one call site in the package (`_set_unusable_key_aside`), so nothing else in
    the accept is disturbed by it.
    """
    import argparse
    import errno

    from rig_workbench.workbench.accept import cmd_accept

    run_cli(["new", "test task", "--type", "feature"], git_repo)
    _commit_gitignore(git_repo)
    task_id = next((git_repo / ".rig" / "runs").iterdir()).name
    task = json.loads((git_repo / ".rig" / "runs" / task_id / "task.json").read_text(encoding="utf-8"))
    wt = pathlib.Path(task["worktree_path"])
    (wt / "g.txt").write_text("change\n", encoding="utf-8")
    subprocess.run(["git", "add", "g.txt"], cwd=wt, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "work"], cwd=wt, check=True)
    _make_acceptable_task(git_repo, task_id)

    key = git_repo / ".rig" / "provenance.key"
    key.write_bytes(b"12345678")                      # present, unusable, and about to be immovable
    real_rename = pathlib.Path.rename

    def refusing_rename(self, target):
        if "provenance.key" in self.name:
            raise OSError(errno.EACCES, "Permission denied")
        return real_rename(self, target)

    monkeypatch.setattr(pathlib.Path, "rename", refusing_rename)
    monkeypatch.chdir(git_repo)

    with pytest.raises(SystemExit) as exited:
        cmd_accept(argparse.Namespace(task_id=task_id, force=False))
    assert exited.value.code == 2

    # Nothing landed, nothing was written, and the key the command refused to move is intact.
    assert subprocess.run(["git", "status", "--porcelain"], cwd=git_repo, check=True,
                          capture_output=True, text=True).stdout.strip() == ""
    assert not (git_repo / ".rig" / "runs" / task_id / "provenance.json").exists()
    assert json.loads((git_repo / ".rig" / "runs" / task_id / "task.json")
                      .read_text(encoding="utf-8"))["status"] != "accepted"
    assert key.read_bytes() == b"12345678"
    assert not list((git_repo / ".rig").glob("provenance.key.unusable*"))


def test_setting_a_key_aside_never_falls_through_to_overwriting_it(tmp_path, monkeypatch):
    """The rule the refusal rests on: if the file cannot be moved, it is not written over."""
    import errno

    from rig_workbench.workbench.state import load_or_create_provenance_key

    (tmp_path / ".rig").mkdir(parents=True)
    key = tmp_path / ".rig" / "provenance.key"
    key.write_bytes(b"12345678")
    monkeypatch.setattr(pathlib.Path, "rename",
                        lambda self, target: (_ for _ in ()).throw(OSError(errno.EACCES, "nope")))

    with pytest.raises(OSError, match="could not be moved aside"):
        load_or_create_provenance_key(tmp_path)
    assert key.read_bytes() == b"12345678"


def test_a_source_that_has_already_vanished_is_not_an_error(tmp_path, capsys):
    """A sibling got there first, which is not a failure and must not be refused as one.

    `_set_unusable_key_aside` answers `None` when the rename finds nothing to move, and the
    settle loop re-reads instead of raising — the pair this round added, and mutation showed
    nothing reached either half: turning the `FileNotFoundError` into a re-raise survived the
    suite. Here the first rename raises it *and* a sibling's key lands at the path, which is
    the real shape of the race; the second pass reads that key and returns it.
    """
    from rig_workbench.workbench.state import load_or_create_provenance_key

    (tmp_path / ".rig").mkdir(parents=True)
    key_path = tmp_path / ".rig" / "provenance.key"
    key_path.write_bytes(b"12345678")
    siblings_key = bytes(range(32))
    real_rename = pathlib.Path.rename
    raised = []

    def vanishing_rename(self, target):
        if self.name == "provenance.key" and not raised:
            raised.append(True)
            self.write_bytes(siblings_key)          # the sibling's key is what is there now
            raise FileNotFoundError(2, "No such file or directory")
        return real_rename(self, target)

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(pathlib.Path, "rename", vanishing_rename)
    try:
        resolved = load_or_create_provenance_key(tmp_path)
    finally:
        monkeypatch.undo()

    assert raised == [True]
    assert resolved == siblings_key                 # the sibling's key, not a new one
    assert key_path.read_bytes() == siblings_key
    assert not list((tmp_path / ".rig").glob("provenance.key.unusable*"))
    assert capsys.readouterr().out == ""            # nothing happened worth warning about


def test_an_aside_suffix_somebody_else_chose_does_not_raise(tmp_path):
    """`isdecimal` and not `isdigit`, with the case that separates them.

    `"²".isdigit()` is True and `int("²")` raises, so a sibling file named
    `provenance.key.unusable-²` — a name this code did not choose and cannot stop somebody
    writing — turned suffix parsing into a `ValueError` escaping the OSError handling around
    it. The guard was changed when review demonstrated it; this is the case behind the claim.
    """
    from rig_workbench.workbench.state import load_or_create_provenance_key

    (tmp_path / ".rig").mkdir(parents=True)
    (tmp_path / ".rig" / "provenance.key.unusable-\u00b2").write_bytes(b"not a key")
    (tmp_path / ".rig" / "provenance.key").write_bytes(b"12345678")

    assert len(load_or_create_provenance_key(tmp_path)) == 32
    names = sorted(p.name for p in (tmp_path / ".rig").glob("provenance.key.unusable*"))
    assert names == ["provenance.key.unusable-2", "provenance.key.unusable-\u00b2"]


def test_an_aside_name_is_never_reused_after_one_is_deleted(tmp_path):
    """Suffixes are allocated after the highest present, not at the first gap: reusing
    `.unusable` once an operator deletes it gives the newest file the oldest name, and then
    only mtime says which is which."""
    from rig_workbench.workbench.state import load_or_create_provenance_key

    (tmp_path / ".rig").mkdir(parents=True)
    key = tmp_path / ".rig" / "provenance.key"
    for i in range(3):
        key.write_bytes(bytes([i]) * 8)
        load_or_create_provenance_key(tmp_path)
    assert sorted(p.name for p in (tmp_path / ".rig").glob("provenance.key.unusable*")) == [
        "provenance.key.unusable", "provenance.key.unusable-2", "provenance.key.unusable-3"]

    (tmp_path / ".rig" / "provenance.key.unusable").unlink()
    key.write_bytes(b"zzzzzzzz")
    load_or_create_provenance_key(tmp_path)
    assert sorted(p.name for p in (tmp_path / ".rig").glob("provenance.key.unusable*")) == [
        "provenance.key.unusable-2", "provenance.key.unusable-3", "provenance.key.unusable-4"]


# ---- the key the signer will not use (the ledger's rule, asked once) ---------

def test_an_unusable_key_file_is_set_aside_rather_than_signed_with(tmp_path, capsys):
    """The second reader of `.rig/provenance.key`, closed with the first.

    Measured before this: a zero-byte key file made `load_or_create_provenance_key` return
    `b""`, `sign_provenance` signed with it, and a record rewritten to a different
    `accepted_by` and signed under `hmac.new(b"", …)` — computable by anyone, without the
    repository — verified True, so `workbench.py verify-provenance` printed valid and
    untampered over it. `govern.ledger.usable_key` is the one rule for what counts as a key
    and both readers ask it now.
    """
    import hashlib
    import hmac

    from rig_workbench.govern.ledger import MIN_KEY_BYTES
    from rig_workbench.workbench.state import (_provenance_payload,
                                               load_or_create_provenance_key)

    (tmp_path / ".rig").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".rig" / "provenance.key").write_bytes(b"")

    key = load_or_create_provenance_key(tmp_path)
    assert len(key) >= MIN_KEY_BYTES
    # The warning says what this process read, not what the file is: see
    # `test_a_key_a_sibling_wrote_is_never_called_unusable` for why that distinction is the
    # whole point.
    assert "held 0 byte(s) when this process read it" in capsys.readouterr().out

    record = {"task_id": "rig-1", "accepted_by": "alice"}
    assert verify_provenance(tmp_path, record, sign_provenance(tmp_path, record)) is True
    forged = {"task_id": "rig-1", "accepted_by": "Chief Security Officer"}
    offline = hmac.new(b"", _provenance_payload(forged), hashlib.sha256).hexdigest()
    assert verify_provenance(tmp_path, forged, offline) is False


def test_a_one_byte_key_file_is_refused_too(tmp_path):
    """`echo > .rig/provenance.key`. Guessable, and it used to sign."""
    (tmp_path / ".rig").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".rig" / "provenance.key").write_bytes(b"\n")
    from rig_workbench.workbench.state import load_or_create_provenance_key

    assert load_or_create_provenance_key(tmp_path) != b"\n"
    assert (tmp_path / ".rig" / "provenance.key").read_bytes() != b"\n"


def test_a_key_a_sibling_wrote_is_never_called_unusable(tmp_path, capsys):
    """The claim this run is about, applied to its own warning.

    Two accepts in one repository share no lock, so between one process reading the key
    file and renaming it aside, another can replace it with a perfectly good key. Measured
    on the previous shape with four concurrent creators: 7 usable keys across 60 races were
    renamed to `provenance.key.unusable` under a warning that said they were not usable
    signing keys — a false statement about a good key, produced by the aside path itself.

    The rename is made to race here deterministically: a sibling's 32-byte key lands at the
    path in the instant before the rename. What comes out must not call that key unusable —
    it is put back into service, and the warning says what actually happened.
    """
    from rig_workbench.workbench.state import load_or_create_provenance_key

    (tmp_path / ".rig").mkdir(parents=True)
    key_path = tmp_path / ".rig" / "provenance.key"
    key_path.write_bytes(b"12345678")
    siblings_key = bytes(range(32))
    real_rename = pathlib.Path.rename

    def racing_rename(self, target):
        if self.name == "provenance.key":
            self.write_bytes(siblings_key)      # the sibling wins the instant before us
        return real_rename(self, target)

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(pathlib.Path, "rename", racing_rename)
    try:
        resolved = load_or_create_provenance_key(tmp_path)
    finally:
        monkeypatch.undo()

    printed = capsys.readouterr().out
    assert resolved == siblings_key                      # the good key is what signs
    assert key_path.read_bytes() == siblings_key         # …and what the repository keeps
    assert "was replaced with a usable key" in printed
    assert "No key has been discarded" in printed
    assert "byte(s) when this process read it" not in printed   # the false claim, absent
    # …and no copy of the live secret is left behind under a name saying it is dead: the
    # rescue used to leave `provenance.key.unusable` byte-identical to the key in use.
    assert not list((tmp_path / ".rig").glob("provenance.key.unusable*"))
    assert "has been removed" in printed


def test_a_key_this_process_could_not_read_is_not_reported_as_zero_bytes(tmp_path, capsys):
    """"held 0 byte(s)" is a measurement, and for a file nobody could read it is a false one.

    A FIFO at the key path is the reproducible case (a mode this process may not open is the
    same branch, and a sandbox running as root cannot demonstrate it). The file is still set
    aside and a key is still created; what changes is that the warning says the file could
    not be read rather than stating a length no one here measured.
    """
    import os

    from rig_workbench.workbench.state import load_or_create_provenance_key

    (tmp_path / ".rig").mkdir(parents=True)
    os.mkfifo(tmp_path / ".rig" / "provenance.key")

    assert len(load_or_create_provenance_key(tmp_path)) == 32
    printed = capsys.readouterr().out
    assert "could not be read as a key by this process" in printed
    assert "held 0 byte(s)" not in printed
    moved = list((tmp_path / ".rig").glob("provenance.key.unusable*"))
    assert len(moved) == 1


def test_a_key_is_created_without_clobbering_one_that_appeared_first(tmp_path):
    """The creation is a link from a complete temporary file, not a write to the path.

    `O_EXCL` alone would not do: the file exists from the moment it is created and is empty
    until the write lands, so a sibling reading in that window sees zero bytes and — by this
    module's own floor — would call it unusable and move it aside. The bytes are linked into
    place complete, and a creator that loses the race takes the winner's key.
    """
    from rig_workbench.workbench.state import _create_key_if_absent

    (tmp_path / ".rig").mkdir(parents=True)
    p = tmp_path / ".rig" / "provenance.key"
    winner = bytes(range(32))
    _create_key_if_absent(p, winner)
    _create_key_if_absent(p, b"z" * 32)                  # the loser
    assert p.read_bytes() == winner
    assert not list((tmp_path / ".rig").glob(".provenance-key.*"))   # no temp left behind


def test_concurrent_creators_settle_on_one_key(tmp_path):
    """Four threads, one repository, no key: every one of them must come away with the key
    the repository actually holds. Measured on the previous shape, four concurrent
    processes: 3 of 40 races ended with siblings holding different keys, so a record signed
    by one verified against nothing."""
    import threading

    from rig_workbench.workbench.state import load_or_create_provenance_key

    (tmp_path / ".rig").mkdir(parents=True)
    start = threading.Barrier(4)
    got: list[bytes] = []
    lock = threading.Lock()

    def create():
        start.wait()
        key = load_or_create_provenance_key(tmp_path)
        with lock:
            got.append(key)

    threads = [threading.Thread(target=create) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    on_disk = (tmp_path / ".rig" / "provenance.key").read_bytes()
    assert got and set(got) == {on_disk}


# ---- the read path must stay a read path ------------------------------------

def test_verifying_never_creates_or_replaces_the_key(tmp_path):
    """The defect the split exists to fix, and it was mine.

    `verify_provenance` called the *creating* loader, so checking a record on a repository
    whose key was below the floor replaced that key: measured, an 8-byte key — a 64-bit
    secret nobody brute-forces — became 32 fresh bytes on a verify that returned False, and
    every record it had signed was unverifiable for good, with tamper and rotation
    indistinguishable. A key that cannot be checked is `False`; nothing is written.
    """
    from rig_workbench.workbench.state import provenance_key

    (tmp_path / ".rig").mkdir(parents=True, exist_ok=True)
    key = tmp_path / ".rig" / "provenance.key"
    key.write_bytes(b"12345678")
    before = sorted(p.name for p in (tmp_path / ".rig").iterdir())

    assert verify_provenance(tmp_path, {"task_id": "rig-1"}, "deadbeef") is False
    assert key.read_bytes() == b"12345678"
    assert sorted(p.name for p in (tmp_path / ".rig").iterdir()) == before
    assert provenance_key(tmp_path) is None          # unusable is not usable, and stays put

    # An absent key is the same answer, and still writes nothing.
    other = tmp_path / "elsewhere"
    other.mkdir()
    assert verify_provenance(other, {"task_id": "rig-1"}, "deadbeef") is False
    assert not (other / ".rig").exists()


def test_a_symlinked_key_is_moved_aside_rather_than_written_through(tmp_path):
    """`Path.is_file()` follows symlinks, so writing the new key through the path wrote it
    into whatever the link pointed at — measured, an 8-byte file *outside the repository*
    came back 32 random bytes. `rename` acts on the link, not on its target."""
    from rig_workbench.workbench.state import load_or_create_provenance_key

    outside = tmp_path / "outside"
    outside.mkdir()
    target = outside / "secret.bin"
    target.write_bytes(b"87654321")

    repo = tmp_path / "repo"
    (repo / ".rig").mkdir(parents=True)
    (repo / ".rig" / "provenance.key").symlink_to(target)

    key = load_or_create_provenance_key(repo)
    assert len(key) == 32
    assert target.read_bytes() == b"87654321"
    assert (repo / ".rig" / "provenance.key").read_bytes() == key
    assert not (repo / ".rig" / "provenance.key").is_symlink()
    aside = [p for p in (repo / ".rig").iterdir() if ".unusable" in p.name]
    assert len(aside) == 1 and aside[0].is_symlink()


def test_an_unusable_key_is_kept_where_an_operator_can_find_it(tmp_path):
    """Replacing it in place destroyed the old bytes; this keeps them beside the new key."""
    from rig_workbench.workbench.state import load_or_create_provenance_key

    (tmp_path / ".rig").mkdir(parents=True)
    (tmp_path / ".rig" / "provenance.key").write_bytes(b"12345678")
    key = load_or_create_provenance_key(tmp_path)
    kept = [p for p in (tmp_path / ".rig").iterdir() if ".unusable" in p.name]
    assert len(kept) == 1 and kept[0].read_bytes() == b"12345678"
    assert (tmp_path / ".rig" / "provenance.key").read_bytes() == key


def test_only_the_signer_and_accepts_own_preflight_reach_the_creating_loader():
    """`verify-provenance` is declared `effect_class="read-only"` in the capability
    registry, and keeping the creating loader out of every verify-shaped path is what makes
    that true.

    Two places name it, both deliberate: `sign_provenance`'s fallback for a caller that
    holds no key, and the preflight in `accept` that acquires one *before* the squash.

    **What this reaches, stated exactly, because a guard whose docstring overstates it is
    worse than no guard.** Every *mention* of the name is counted, per file and enclosing
    scope: a call, a bare reference, an import alias, the definition, and a `"..."` string
    holding the name (which is how `getattr` would reach it). Counting rather than
    collecting means a mention added inside a scope already listed here fails too — review
    measured both earlier shapes of this scan being slipped past, first by an `ast.Call`
    filter that missed module level and `async def`, then by set-collection that hid a
    second mention in a listed scope.

    What it cannot reach is a name assembled at runtime — `getattr(state, "load_or" +
    "_create_provenance_key")` — which no static scan resolves. That is the honest limit of
    the guard, and it is why it is not the only thing holding this: the read path
    (`provenance_key`) exists so no caller *needs* the creating one, and the behaviour is
    pinned directly by `test_verify_provenance_leaves_the_repository_exactly_as_it_found_it`.
    """
    package = REPO_ROOT / "rig_workbench"
    symbol = "load_or_create_provenance_key"
    found: dict[str, int] = {}
    for path in sorted(package.rglob("*.py")):
        rel = path.relative_to(REPO_ROOT)
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for scope, node in _scoped_nodes(tree):
            if ((isinstance(node, ast.Name) and node.id == symbol)
                    or (isinstance(node, ast.Attribute) and node.attr == symbol)
                    or (isinstance(node, ast.alias) and node.name == symbol)
                    or (isinstance(node, ast.Constant) and node.value == symbol)
                    or (isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                        and node.name == symbol)):
                found[f"{rel}:{scope}"] = found.get(f"{rel}:{scope}", 0) + 1
    # COUNTED, not just named. Keyed by scope alone, a mention *added inside a scope that is
    # already expected* was invisible: review copied the package, put a module-level alias in
    # `accept.py` and called it from `cmd_verify_provenance`, and the set came back identical
    # to this one. A count changes when a mention is added anywhere, including there.
    assert found == {
        "rig_workbench/workbench/state.py:<module>": 1,                   # the definition
        "rig_workbench/workbench/state.py:sign_provenance": 1,
        "rig_workbench/workbench/accept.py:<module>": 1,                  # the import
        "rig_workbench/workbench/accept.py:_cmd_accept_locked": 1,
    }

    from rig_workbench.registry import CAPABILITIES

    verify = next(c for c in CAPABILITIES if c.id == "wb.verify-provenance")
    assert verify.effect_class == "read-only"
