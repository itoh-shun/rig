"""Read-only public diagnosis, including unavailable integrations and secret handling."""
import hashlib
import json
import os

import pytest

from rig_workbench import doctor
from rig_workbench.orchestrate import config
from rig_workbench.orchestrate.orchestrators.base import Availability
from rig_workbench.orchestrate.orchestrators.selection import select_orchestrator


from rig_workbench.orchestrate.orchestrators import credentials  # noqa: E402


@pytest.fixture(autouse=True)
def clear_t3_environment(monkeypatch):
    """Tests opt in to T3 settings instead of inheriting the developer's credentials."""
    for key in tuple(os.environ):
        if key.startswith("RIG_T3_"):
            monkeypatch.delenv(key, raising=False)
    credentials._reset_for_tests()


def test_t3_environment_is_isolated():
    assert not any(key.startswith("RIG_T3_") for key in os.environ)


def test_t3_holder_is_isolated():
    # The module imports captured any token the developer exported before pytest started.
    assert credentials.t3_token() is None


class Environment:
    def __init__(self, **values):
        self.values = values

    def get(self, key, default=None):
        return self.values.get(key, default)

    def snapshot(self):
        return dict(self.values)


class Output:
    def __init__(self):
        self.stdout, self.stderr = [], []

    def out(self, value=""):
        self.stdout.append(value)

    def err(self, value=""):
        self.stderr.append(value)


class Backend:
    name = "t3"
    project_id = "project"

    def __init__(self, ok=True, detail="compatible"):
        self.ok, self.detail = ok, detail
        self.client = self
        self.closed = 0

    def available(self):
        return Availability(self.ok, "compatible" if self.ok else "auth_failed", self.detail)

    def capabilities(self):
        return frozenset({"agent.run", "agent.cancel", "thread.resume", "thread.durable"})

    def close(self):
        self.closed += 1


@pytest.fixture
def manifest(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "INVOCATION_CWD", tmp_path)
    store = tmp_path / "trust.json"
    monkeypatch.setenv("RIG_TRUST_STORE", str(store))
    path = tmp_path / ".claude" / "rig.md"
    path.parent.mkdir()
    path.write_text("---\norchestrator:\n  preferred: auto\n---\n")
    return path, store


def test_doctor_reports_native_t3_and_both_orca_axes_without_writes(manifest, monkeypatch):
    path, store = manifest
    monkeypatch.setenv("RIG_ALLOW_PROJECT_MANIFEST", "1")
    before = path.read_bytes()
    output = Output()
    env = Environment(ORCA_WORKTREE_ID="worktree::/workspace", RIG_ALLOW_PROJECT_MANIFEST="1")
    assert doctor.main([], out=output, env=env) == 0
    text = "\n".join(output.stdout)
    assert "Rig Native Runtime" in text and "T3 Code" in text
    assert "session detected; CLI not probed" in text
    assert "untrusted project manifest ignored" in text
    assert path.read_bytes() == before and not store.exists()
    assert not (path.parent.parent / ".rig").exists()


def test_doctor_reads_only_a_previously_trusted_manifest(manifest):
    path, store = manifest
    store.write_text(json.dumps({str(path.resolve()): hashlib.sha256(path.read_bytes()).hexdigest()}))
    before = store.read_bytes()
    output = Output()
    assert doctor.main(["--json"], out=output, env=Environment()) == 0
    data = json.loads(output.stdout[0])
    assert data["selection"]["selected_by"] == "manifest"
    assert data["reasons"] == []
    assert store.read_bytes() == before


def test_doctor_json_matches_the_shared_selection_without_stdout_noise(manifest):
    env = Environment(RIG_T3_MCP_URL="http://localhost/mcp", RIG_T3_MCP_TOKEN="secret")
    backend = Backend()
    output = Output()
    assert doctor.main(["--json"], out=output, env=env, factory=lambda _: backend) == 0
    data = json.loads(output.stdout[0])
    selected = select_orchestrator(env=env, factory=lambda _: Backend(), emit=False)
    assert len(output.stdout) == 1 and output.stderr == []
    assert data["schema_version"] == 1
    assert data["selection"]["active"] == selected.name == "t3"
    assert data["execution_backends"]["t3"]["available"] is True
    assert "MCP connected" in data["execution_backends"]["t3"]["detail"]
    assert data["workspace_runtimes"]["orca"]["session"]["observed"] is True
    assert data["workspace_runtimes"]["orca"]["cli"]["observed"] is False
    assert backend.closed == 1


@pytest.mark.parametrize("args,manifest_value,expected", [
    (["--orchestrator", "native"], {}, "native"),
    (["--orchestrator", "t3"], {}, None),
    ([], {}, "native"),
    ([], {"fallback": "none"}, None),
    (["--orchestrator", "native"], {"t3": {"token": "secret"}}, None),
    (["--orchestrator", "wrong"], {}, None),
    (["--orchestrator"], {}, None),
    (["--unknown"], {}, None),
])
def test_doctor_exits_zero_for_unavailable_invalid_and_unconfigured_backends(monkeypatch, args, manifest_value, expected):
    monkeypatch.setattr(doctor, "load_manifest", lambda **_: {"orchestrator": manifest_value})
    output = Output()
    assert doctor.main(["--json", *args], out=output, env=Environment()) == 0
    data = json.loads(output.stdout[0])
    assert data["selection"]["active"] == expected
    assert len(output.stdout) == 1 and output.stderr == []
    if expected is None:
        assert data["errors"]


@pytest.mark.parametrize("json_output", [False, True])
def test_doctor_never_discloses_tokens_or_probes_t3_for_explicit_native(monkeypatch, json_output):
    monkeypatch.setattr(doctor, "load_manifest", lambda **_: {})
    output = Output()
    env = Environment(RIG_T3_MCP_URL="https://example.test/mcp", RIG_T3_MCP_TOKEN="top-secret",
                      ORCA_WORKTREE_ID="top-secret::/top-secret")
    args = ["--orchestrator", "native", *(["--json"] if json_output else [])]
    assert doctor.main(args, out=output, env=env,
                       factory=lambda _: pytest.fail("Native diagnosis probed T3")) == 0
    text = "\n".join(output.stdout + output.stderr)
    assert "top-secret" not in text
    assert "not probed (native requested)" in text


def test_doctor_redacts_failing_probe_and_orca_diagnostic_errors(monkeypatch):
    monkeypatch.setattr(doctor, "load_manifest", lambda **_: {})
    output = Output()
    env = Environment(RIG_T3_MCP_URL="https://example.test/mcp", RIG_T3_MCP_TOKEN="secret")
    assert doctor.main(["--json"], out=output, env=env,
                       factory=lambda _: Backend(False, "secret authentication failed")) == 0
    assert "secret" not in output.stdout[0]
    assert json.loads(output.stdout[0])["execution_backends"]["t3"]["capabilities"] == []
    monkeypatch.setattr(doctor, "orca_report", lambda _: (_ for _ in ()).throw(RuntimeError("secret")))
    output = Output()
    assert doctor.main(["--json"], out=output, env=env) == 0
    assert "workspace_diagnosis_failed" in json.loads(output.stdout[0])["errors"]
    assert "secret" not in output.stdout[0]


def test_doctor_help_is_zero_and_json_help_is_one_document(monkeypatch):
    monkeypatch.setattr(doctor, "load_manifest", lambda **_: {})
    output = Output()
    assert doctor.main(["--help"], out=output) == 0
    assert "usage: rig-wb doctor" in output.stdout[0]
    output = Output()
    assert doctor.main(["--json", "--help"], out=output, env=Environment()) == 0
    assert len(output.stdout) == 1 and json.loads(output.stdout[0])["schema_version"] == 1
