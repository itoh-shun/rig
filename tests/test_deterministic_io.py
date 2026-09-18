import os
import subprocess

import pytest

from rig_workbench.orchestrate.deterministic_io import StrictIO


@pytest.fixture
def io(tmp_path):
    workspace = tmp_path / "repo"
    workspace.mkdir()
    subprocess.run(["git", "init", str(workspace)], check=True, capture_output=True)
    (workspace / "source").write_text("original")
    subprocess.run(["git", "-C", str(workspace), "add", "source"], check=True)
    adapter = StrictIO(workspace, tmp_path / "private" / "state.json")
    adapter.preflight()
    return adapter


def test_real_sandbox_protects_state_and_git_but_allows_workspace(io):
    io.save({"trusted": True})
    result = io.run(["/usr/bin/python3", "-c", "import pathlib,sys; pathlib.Path('source').write_text('fixed');\nfor name in sys.argv[1:]:\n try: pathlib.Path(name).write_text('forged')\n except OSError: pass\n else: raise RuntimeError('write escaped')", str(io.state_path), str(io.workspace / ".git" / "config")])
    assert result.returncode == 0, result.stderr
    assert io.load() == {"trusted": True}
    assert (io.workspace / "source").read_text() == "fixed"


def test_readonly_checks_cannot_change_subject(io):
    result = io.run(["/usr/bin/python3", "-c", "from pathlib import Path; Path('source').write_text('bad')"], writable=False)
    assert result.returncode != 0
    assert (io.workspace / "source").read_text() == "original"


def test_snapshot_binds_untracked_contents_modes_and_deletions(io):
    before = io.snapshot()
    extra = io.workspace / "extra"
    extra.write_text("new")
    assert io.snapshot() != before
    extra.unlink()
    assert io.snapshot() == before
    source = io.workspace / "source"
    source.chmod(0o755)
    assert io.snapshot() != before
    source.unlink()
    assert io.snapshot() != before


def test_snapshot_rejects_symlink(io):
    (io.workspace / "link").symlink_to("source")
    with pytest.raises(ValueError, match="symlink"):
        io.snapshot()


def test_state_is_private_strict_and_locked(io):
    with io.locked():
        io.save({"n": 1})
        assert io.load() == {"n": 1}
        with pytest.raises(OSError, match="locked"):
            with io.locked():
                pass
    assert io.state_path.stat().st_mode & 0o777 == 0o600
    with pytest.raises(ValueError):
        io.save({"n": float("nan")})
    io.state_path.write_text('{"n":1,"n":2}')
    with pytest.raises(ValueError):
        io.load()


def test_workspace_cannot_contain_state(io):
    with pytest.raises(ValueError, match="outside"):
        StrictIO(io.workspace, io.workspace / "state.json")


def test_preflight_required(io):
    fresh = StrictIO(io.workspace, io.state_path)
    with pytest.raises(ValueError, match="preflight"):
        fresh.run(["/bin/true"])


def test_writable_alias_to_external_file_is_rejected(io, tmp_path):
    outside = tmp_path / "outside"
    outside.write_text("trusted")
    os.link(outside, io.workspace / "alias")
    with pytest.raises(ValueError, match="hardlink"):
        io.run(["/bin/true"])


def test_provider_subprocess_marker_is_set(io):
    result = io.run(["/usr/bin/python3", "-c", "import os; print(os.environ['RIG_PROVIDER_SUBPROCESS'])"])
    assert result.stdout.strip() == "1"


@pytest.mark.parametrize("writable", [False, True])
def test_protected_state_cannot_be_overwritten_in_either_role(io, writable):
    io.save({"trusted": True})
    result = io.run(["/usr/bin/python3", "-c", "import pathlib,sys; pathlib.Path(sys.argv[1]).write_text('forged')", str(io.state_path)], writable=writable)
    assert result.returncode != 0
    assert io.load() == {"trusted": True}


def test_checks_do_not_receive_provider_credentials(io, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-secret")
    result = io.run(["/usr/bin/python3", "-c", "import os; print('OPENAI_API_KEY' in os.environ)"], writable=False)
    assert result.stdout.strip() == "False"


def test_bootstrap_load_uses_same_strict_parser(io):
    from rig_workbench.orchestrate.deterministic_io import load_state
    io.save({"valid": True})
    assert load_state(io.state_path) == {"valid": True}
    io.state_path.write_text('{"v":1,"v":2}')
    with pytest.raises(ValueError):
        load_state(io.state_path)


def test_state_parent_ancestor_is_readonly_without_masking_workspace(io):
    adapter = StrictIO(io.workspace, io.workspace.parent / "ancestor-state.json")
    adapter.preflight()
    adapter.save({"protected": True})
    result = adapter.run(["/usr/bin/python3", "-c", "from pathlib import Path; Path('source').write_text('works')"])
    assert result.returncode == 0
    assert adapter.load() == {"protected": True}
