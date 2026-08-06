import json
import pathlib

from rig_workbench import hostcheck


def _repo(tmp_path: pathlib.Path) -> pathlib.Path:
    (tmp_path / ".claude").mkdir()
    return tmp_path


def test_deny_rules_missing_when_settings_absent(tmp_path):
    result = hostcheck.check_deny_rules(_repo(tmp_path))
    assert result["ok"] is False
    assert result["sources"] == []
    assert "permissions.deny" in result["remedy"]


def test_deny_rules_found_and_counted(tmp_path):
    root = _repo(tmp_path)
    (root / ".claude" / "settings.json").write_text(
        json.dumps({"permissions": {"deny": ["Bash(rm -rf:*)", "Bash(git push --force:*)"]}}),
        encoding="utf-8",
    )
    result = hostcheck.check_deny_rules(root)
    assert result["ok"] is True
    assert result["sources"] == [{"path": ".claude/settings.json", "rules": 2}]


def test_deny_rules_reject_empty_and_malformed(tmp_path):
    root = _repo(tmp_path)
    (root / ".claude" / "settings.json").write_text(
        json.dumps({"permissions": {"deny": []}}), encoding="utf-8"
    )
    assert hostcheck.check_deny_rules(root)["ok"] is False

    (root / ".claude" / "settings.local.json").write_text("{not json", encoding="utf-8")
    assert hostcheck.check_deny_rules(root)["ok"] is False


def test_state_ignored_accepts_the_usual_spellings(tmp_path):
    root = _repo(tmp_path)
    for pattern in (".rig/", "/.rig", ".rig", ".rig/runs/"):
        (root / ".gitignore").write_text(f"node_modules/\n{pattern}\n", encoding="utf-8")
        assert hostcheck.check_state_ignored(root)["ok"] is True, pattern


def test_state_ignored_is_not_fooled_by_a_similar_prefix(tmp_path):
    root = _repo(tmp_path)
    (root / ".gitignore").write_text(".rigging/\n", encoding="utf-8")
    assert hostcheck.check_state_ignored(root)["ok"] is False


def test_isolation_reports_declared_devcontainer_config(tmp_path):
    root = _repo(tmp_path)
    (root / ".devcontainer").mkdir()
    (root / ".devcontainer" / "devcontainer.json").write_text("{}", encoding="utf-8")
    result = hostcheck.check_isolation(root)
    assert result["declared_config"] == [".devcontainer/devcontainer.json"]


def test_isolation_signal_comes_from_the_environment_not_the_config(tmp_path, monkeypatch):
    root = _repo(tmp_path)
    for var in hostcheck.CONTAINER_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(hostcheck.pathlib.Path, "exists", lambda self: False)
    assert hostcheck.check_isolation(root)["ok"] is False

    monkeypatch.setenv("REMOTE_CONTAINERS", "true")
    result = hostcheck.check_isolation(root)
    assert result["ok"] is True
    assert "env:REMOTE_CONTAINERS" in result["signals"]


def test_run_all_collects_missing_ids(tmp_path):
    result = hostcheck.run_all(_repo(tmp_path))
    assert set(result["missing"]) >= {"deny_rules", "state_ignored"}
    assert result["ok"] is False
    assert [check["id"] for check in result["checks"]] == [
        "process_isolation",
        "deny_rules",
        "state_ignored",
    ]


def test_exit_code_is_advisory_unless_strict(tmp_path, capsys):
    assert hostcheck.cmd_hostcheck(["--repo", str(tmp_path)]) == 3
    assert hostcheck.cmd_hostcheck(["--repo", str(tmp_path), "--strict"]) == 1
    capsys.readouterr()


def test_json_output_is_parseable(tmp_path, capsys):
    hostcheck.cmd_hostcheck(["--repo", str(tmp_path), "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert payload["root"] == str(tmp_path.resolve())
    assert len(payload["checks"]) == len(hostcheck.CHECKS)
