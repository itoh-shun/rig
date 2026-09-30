"""Japanese writing modes bind one effective style for generation and review (#641)."""

import copy
import io
import json
import pathlib
from types import SimpleNamespace

import pytest


ROOT = pathlib.Path(__file__).resolve().parents[1]
RECIPES = ("japanese-writing", "japanese-writing-revision")


class RecordingPresenter:
    def __init__(self):
        self.output = []
        self.errors = []

    def out(self, text=""):
        self.output.append(text)

    def err(self, text=""):
        self.errors.append(text)


class PrivateStdin(io.BytesIO):
    @property
    def buffer(self):
        return self


@pytest.fixture
def secure_writing_run(tmp_path, monkeypatch):
    """Use core recipes and the fake secure-provider boundary used by runtime tests.

    Capture the initial state before runtime transitions: plain must preserve the
    exact initial history that the workflow evaluator composes into its prompts.
    """
    from rig_workbench.orchestrate import commands

    tmp_path.chmod(0o700)
    for name in ("CLAUDECODE", "CLAUDE_CODE_SESSION_ID", "RIG_CALLER"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("RIG_USER_HOME", str(tmp_path / "user-home"))
    monkeypatch.setattr(commands, "resolve_recipe", lambda name: ROOT / "skills/engine/recipes" / f"{name}.md")
    monkeypatch.setattr(commands, "load_manifest", lambda: {})
    monkeypatch.setattr(commands, "preflight_secure_runtime", lambda *_args: {
        "generator": SimpleNamespace(provider="claude", launcher_hashes=["a" * 64]),
        "verifier": SimpleNamespace(provider="codex", launcher_hashes=["b" * 64]),
    })
    monkeypatch.setattr(commands, "close_secure_launchers", lambda _launchers: None)
    captured = []

    def fake_run_loop(state, *_args, **_kwargs):
        captured.append(copy.deepcopy(state))
        return "DONE"

    monkeypatch.setattr(commands, "run_loop", fake_run_loop)
    counter = 0

    def run(*mode_args, category="general", recipe="japanese-writing", expect_code=0):
        nonlocal counter
        counter += 1
        out_path = tmp_path / f"run-{counter}.json"
        presenter = RecordingPresenter()
        monkeypatch.setattr(commands.sys, "stdin", PrivateStdin("依頼本文".encode()))
        before = len(captured)
        with pytest.raises(SystemExit) as stopped:
            commands.cmd_run([
                recipe, "--provider", "claude", "--verifier-provider", "codex",
                "--goal-stdin", "--review-category", category, "--out", str(out_path),
                *mode_args,
            ], out=presenter)
        assert stopped.value.code == expect_code, presenter.output + presenter.errors
        if expect_code:
            assert len(captured) == before
            assert not out_path.exists()
            return presenter
        assert len(captured) == before + 1
        return captured[-1], presenter, out_path

    return run


@pytest.mark.parametrize("mode_args", [
    ["--mode"],
    ["--mode", ""],
    ["--mode", ","],
    ["--mode", ",talk"],
    ["--mode", "talk,"],
    ["--mode", "talk,,emoji"],
    ["--mode", "unknown"],
    ["--mode", "talk,unknown"],
    ["--mode", "talk,talk"],
    ["--mode", "plain,plain"],
    ["--mode", "plain,talk"],
    ["--mode", "emoji,plain"],
    ["--mode", "talk", "--mode", "emoji"],
    ["--mode", "plain", "--mode", "plain"],
])
def test_invalid_writing_mode_refused_before_execution(secure_writing_run, mode_args):
    presenter = secure_writing_run(*mode_args, expect_code=2)
    assert "--mode" in "\n".join(presenter.output + presenter.errors)


@pytest.mark.parametrize("recipe_name,policies", [
    ("japanese-writing", ""),
    ("feature", "    policies: [secure-provider-execution]\n"),
])
def test_mode_requires_secure_japanese_recipe(tmp_path, monkeypatch, recipe_name, policies):
    from rig_workbench.orchestrate import commands

    recipe = tmp_path / f"{recipe_name}.md"
    recipe.write_text(f"---\nname: {recipe_name}\nsteps:\n  - id: write\n    instruction: write\n{policies}---\n")
    monkeypatch.setattr(commands, "resolve_recipe", lambda _name: recipe)
    monkeypatch.setattr(commands, "run_loop", lambda *_a, **_kw: pytest.fail("nonsecure run executed"))
    presenter = RecordingPresenter()
    with pytest.raises(SystemExit) as stopped:
        commands.cmd_run([recipe_name, "--provider", "mock", "--mode", "talk"], out=presenter)
    assert stopped.value.code == 2
    assert "--mode requires a secure Japanese-writing recipe" in "\n".join(presenter.output + presenter.errors)


@pytest.mark.parametrize("entrypoint,args", [
    ("cmd_run", ["japanese-writing", "--provider", "mock", "--deterministic", "--isolate"]),
    ("cmd_ab", ["japanese-writing", "--provider", "mock"]),
])
def test_mode_is_refused_by_unsupported_execution_paths(entrypoint, args):
    from rig_workbench.orchestrate import commands

    presenter = RecordingPresenter()
    with pytest.raises(SystemExit) as stopped:
        getattr(commands, entrypoint)([*args, "--mode", "talk"], out=presenter)
    assert stopped.value.code == 2
    assert "--mode" in "\n".join(presenter.output + presenter.errors)


@pytest.mark.parametrize("recipe", RECIPES)
@pytest.mark.parametrize("requested,expected", [
    ("emoji,talk", ["talk", "emoji"]),
    ("talk,emoji", ["talk", "emoji"]),
    ("emoji,onomatopoeia,dialogue,talk", ["talk", "dialogue", "onomatopoeia", "emoji"]),
    ("dialogue", ["dialogue"]),
    ("onomatopoeia", ["onomatopoeia"]),
    ("emoji", ["emoji"]),
])
def test_writing_mode_is_canonical_and_bound_once(secure_writing_run, recipe, requested, expected):
    state, presenter, _path = secure_writing_run("--mode", requested, recipe=recipe)
    assert state["writing_mode"] == expected
    assert state["secure_runtime"]["writing_mode"] == expected
    assert [row for row in state["history"] if row["action"] == "BIND_WRITING_MODE"] == [
        {"action": "BIND_WRITING_MODE", "modes": expected},
    ]
    assert not any("not applied" in message or "[WARN]" in message for message in presenter.errors)


def _mode_policy_facets():
    """Supply the existing policy without repeatedly revalidating unrelated packs."""
    return {
        "persona": [], "knowledge": [], "instruction": [], "output_contract": [],
        "policy": [(ROOT / "skills/engine/facets/policies/japanese-writing-modes.md").read_text()],
    }


def _prompts(state):
    from rig_workbench.orchestrate import providers

    write, review = state["steps"]
    return (
        providers.compose_step_prompt(state, write, facets=_mode_policy_facets()),
        providers.compose_artifact_review_prompt(
            state, review, "japanese-writing-reviewer", "完成稿",
            facets=_mode_policy_facets(),
            source_draft=state["goal"] if state["recipe"] == "japanese-writing-revision" else None,
        ),
    )


def _assert_plain_binding_absent(state):
    assert "writing_mode" not in state
    assert "writing_mode" not in state["secure_runtime"]
    assert not any(row["action"] == "BIND_WRITING_MODE" for row in state["history"])
    assert state["history"] == [{"action": "BIND_REVIEW_CATEGORY", "category": state["review_category"]}]


@pytest.mark.parametrize("recipe", RECIPES)
def test_default_and_explicit_plain_keep_both_prompts_byte_identical_with_talk_control(
    secure_writing_run, recipe,
):
    default, _presenter, _path = secure_writing_run(recipe=recipe)
    plain, _presenter, _path = secure_writing_run("--mode", "plain", recipe=recipe)
    for state in (default, plain):
        _assert_plain_binding_absent(state)
        baseline = copy.deepcopy(state)
        baseline.pop("writing_mode", None)
        baseline["secure_runtime"].pop("writing_mode", None)
        baseline["history"] = [{"action": "BIND_REVIEW_CATEGORY", "category": "general"}]
        assert tuple(text.encode() for text in _prompts(state)) == tuple(text.encode() for text in _prompts(baseline))
    assert _prompts(default) == _prompts(plain)

    talk, _presenter, _path = secure_writing_run("--mode", "talk", recipe=recipe)
    for baseline_prompt, talk_prompt in zip(_prompts(default), _prompts(talk)):
        assert talk_prompt != baseline_prompt
        assert "writing_mode: talk" in talk_prompt.split("## Task Contract", 1)[1]
    from rig_workbench.orchestrate import providers
    repair = providers.compose_repair_prompt(
        talk, talk["steps"][0], "初稿", "修正条件", facets=_mode_policy_facets(),
    )
    assert "writing_mode: talk" in repair


def test_both_prompts_read_canonical_modes_only_from_state(secure_writing_run):
    state, _presenter, _path = secure_writing_run("--mode", "emoji,talk")
    for prompt in _prompts(state):
        assert prompt.count("writing_mode: talk,emoji") == 1
    baseline = copy.deepcopy(state)
    baseline.pop("writing_mode")
    for prompt in _prompts(baseline):
        assert "writing_mode:" not in prompt


def test_secure_run_delivers_talk_to_both_providers_and_omits_plain(tmp_path, monkeypatch):
    from rig_workbench.orchestrate import commands
    from test_runtime_security import _fake_provider, _sha256, _valid_japanese_review_output

    tmp_path.chmod(0o700)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("RIG_USER_HOME", str(tmp_path / "user-home"))
    monkeypatch.delenv("RIG_ORG_HOME", raising=False)
    for name in ("CLAUDECODE", "CLAUDE_CODE_SESSION_ID", "RIG_CALLER"):
        monkeypatch.delenv(name, raising=False)

    delivered = {}
    for name, mode_args in (("talk", ["--mode", "talk"]), ("plain", [])):
        run_dir = tmp_path / name
        run_dir.mkdir(mode=0o700)
        generator, verifier = run_dir / "claude", run_dir / "codex"
        generator_stdin, verifier_stdin = run_dir / "generator.stdin", run_dir / "verifier.stdin"
        _fake_provider(
            generator, run_dir / "generator.argv", "会議は明日の10時に始まります。",
            stdin_log=generator_stdin,
        )
        _fake_provider(
            verifier, run_dir / "verifier.argv", _valid_japanese_review_output(),
            stdin_log=verifier_stdin,
        )
        interpreter = pathlib.Path("/bin/sh")
        pin_config = run_dir / "provider-pins.json"
        pin_config.write_text(json.dumps({
            "schema_version": 1,
            **{
                role: {
                    "executable": str(executable), "sha256": _sha256(executable),
                    "interpreter": str(interpreter), "interpreter_sha256": _sha256(interpreter),
                }
                for role, executable in (("generator", generator), ("verifier", verifier))
            },
        }), encoding="utf-8")
        pin_config.chmod(0o600)
        monkeypatch.setattr(commands.sys, "stdin", PrivateStdin("明日の10時に会議を開く。".encode()))
        presenter = RecordingPresenter()
        state_path = run_dir / "run-state.json"
        with pytest.raises(SystemExit) as stopped:
            commands.cmd_run([
                "japanese-writing", "--provider", "claude", "--verifier-provider", "codex",
                "--secure-provider-config", str(pin_config), "--goal-stdin",
                "--review-category", "general", "--out", str(state_path), *mode_args,
            ], out=presenter)
        assert stopped.value.code == 0, presenter.output + presenter.errors
        state = json.loads(state_path.read_text(encoding="utf-8"))
        assert state["secure_runtime"]["prompt_transport"] == "stdin"
        assert all(state["step_state"][step]["status"] == "passed" for step in ("write", "review"))
        delivered[name] = {
            "generator": generator_stdin.read_text(encoding="utf-8"),
            "reviewer": verifier_stdin.read_text(encoding="utf-8"),
        }

    for role in ("generator", "reviewer"):
        assert "writing_mode: talk" in delivered["talk"][role].splitlines(), role
        assert "writing_mode:" not in delivered["plain"][role], role


@pytest.mark.parametrize("recipe", RECIPES)
@pytest.mark.parametrize("category", ["incident_report", "support_reply"])
@pytest.mark.parametrize("requested,expected", [("emoji,talk", ["talk"]), ("emoji", None)])
def test_sensitive_categories_suppress_emoji_with_presenter_warning(
    secure_writing_run, recipe, category, requested, expected,
):
    state, presenter, _path = secure_writing_run("--mode", requested, category=category, recipe=recipe)
    warnings = [line for line in presenter.errors if "[WARN]" in line]
    assert len(warnings) == 1
    assert "emoji" in warnings[0] and category in warnings[0]
    assert not any("[WARN]" in line for line in presenter.output)
    if expected is None:
        _assert_plain_binding_absent(state)
    else:
        assert state["writing_mode"] == state["secure_runtime"]["writing_mode"] == expected
        assert {"action": "BIND_WRITING_MODE", "modes": expected} in state["history"]
    for prompt in _prompts(state):
        assert "writing_mode: emoji" not in prompt
        assert "writing_mode: talk,emoji" not in prompt


def _load_saved_state(state, path):
    from rig_workbench.orchestrate.runstate import load_state, save_state

    save_state(state, path)
    return load_state(path)


def test_load_legacy_absent_mode_remains_plain(secure_writing_run):
    state, _presenter, path = secure_writing_run()
    loaded = _load_saved_state(state, path)
    _assert_plain_binding_absent(loaded)
    assert loaded["stopped"] is None


@pytest.mark.parametrize("modes", [["talk"], ["talk", "emoji"], ["dialogue", "onomatopoeia"]])
def test_load_valid_writing_modes(secure_writing_run, modes):
    state, _presenter, path = secure_writing_run("--mode", ",".join(modes))
    loaded = _load_saved_state(state, path)
    assert loaded["writing_mode"] == loaded["secure_runtime"]["writing_mode"] == modes
    assert loaded["stopped"] is None


@pytest.mark.parametrize("bindings", [
    [],
    [{"action": "BIND_WRITING_MODE", "modes": ["emoji"]}],
    [{"action": "BIND_WRITING_MODE"}],
    [{"action": "BIND_WRITING_MODE", "modes": "talk"}],
    [{"action": "BIND_WRITING_MODE", "modes": ["talk"]}] * 2,
])
def test_load_refuses_missing_changed_or_duplicate_mode_audit(secure_writing_run, bindings):
    state, _presenter, path = secure_writing_run("--mode", "talk")
    state["history"] = [
        row for row in state["history"] if row["action"] != "BIND_WRITING_MODE"
    ] + copy.deepcopy(bindings)
    with pytest.raises(OSError, match="writing mode binding is malformed or changed"):
        _load_saved_state(state, path)


@pytest.mark.parametrize("secure", [True, False])
@pytest.mark.parametrize("modes", [["talk"], []])
def test_load_refuses_mode_audit_without_state_key(secure_writing_run, secure, modes):
    state, _presenter, path = secure_writing_run()
    if not secure:
        state.pop("secure_runtime")
    state["history"].append({"action": "BIND_WRITING_MODE", "modes": modes})
    with pytest.raises(OSError, match="writing mode binding is missing or changed"):
        _load_saved_state(state, path)


@pytest.mark.parametrize("malformed", [
    [], None, "talk", 1, True, {"mode": "talk"},
    ["unknown"], ["talk", "unknown"], [""], ["talk", "talk"],
    ["plain"], ["plain", "talk"], ["emoji", "talk"],
    [None], [1], [True], [["talk"]], [{"mode": "talk"}],
])
def test_load_refuses_malformed_writing_mode(secure_writing_run, malformed):
    state, _presenter, path = secure_writing_run()
    state["writing_mode"] = malformed
    state["secure_runtime"]["writing_mode"] = copy.deepcopy(malformed)
    state["history"].append({"action": "BIND_WRITING_MODE", "modes": copy.deepcopy(malformed)})
    with pytest.raises(OSError, match="writing mode"):
        _load_saved_state(state, path)


@pytest.mark.parametrize("runtime_mode", [None, [], "talk", ["emoji"], ["talk", "emoji"]])
def test_load_refuses_runtime_writing_mode_mismatch(secure_writing_run, runtime_mode):
    state, _presenter, path = secure_writing_run("--mode", "talk")
    state["secure_runtime"]["writing_mode"] = runtime_mode
    with pytest.raises(OSError, match="writing mode"):
        _load_saved_state(state, path)


def test_load_refuses_missing_runtime_writing_mode(secure_writing_run):
    state, _presenter, path = secure_writing_run("--mode", "talk")
    state["secure_runtime"].pop("writing_mode")
    with pytest.raises(OSError, match="writing mode"):
        _load_saved_state(state, path)


def test_load_refuses_runtime_only_writing_mode(secure_writing_run):
    state, _presenter, path = secure_writing_run("--mode", "talk")
    state.pop("writing_mode")
    with pytest.raises(OSError, match="writing mode"):
        _load_saved_state(state, path)


@pytest.mark.parametrize("invalid_run", ["non_japanese", "nonsecure"])
def test_load_refuses_mode_outside_secure_japanese_run(secure_writing_run, invalid_run):
    state, _presenter, path = secure_writing_run("--mode", "talk")
    if invalid_run == "non_japanese":
        state["recipe"] = "feature"
    else:
        state.pop("secure_runtime")
    with pytest.raises(OSError, match="writing mode"):
        _load_saved_state(state, path)


@pytest.mark.parametrize("secure_runtime", [True, "secure", ["talk"]])
def test_load_refuses_mode_with_malformed_secure_runtime(secure_writing_run, secure_runtime):
    state, _presenter, path = secure_writing_run("--mode", "talk")
    state["goal"] = None  # preserve the malformed runtime marker through save_state
    state["secure_runtime"] = secure_runtime
    with pytest.raises(OSError, match="writing mode"):
        _load_saved_state(state, path)


@pytest.mark.parametrize("category", ["incident_report", "support_reply"])
def test_load_refuses_emoji_for_sensitive_category(secure_writing_run, category):
    state, _presenter, path = secure_writing_run(category=category)
    state["writing_mode"] = state["secure_runtime"]["writing_mode"] = ["emoji"]
    state["history"].append({"action": "BIND_WRITING_MODE", "modes": ["emoji"]})
    with pytest.raises(OSError, match="writing mode"):
        _load_saved_state(state, path)


@pytest.mark.parametrize("category", [[], {}, None, 1, True])
def test_load_refuses_nonstring_category_with_bound_mode(secure_writing_run, category):
    state, _presenter, path = secure_writing_run("--mode", "talk")
    state["review_category"] = category
    state["secure_runtime"]["review_category"] = copy.deepcopy(category)
    state["history"][0]["category"] = copy.deepcopy(category)
    with pytest.raises(OSError, match="writing mode binding is malformed or changed"):
        _load_saved_state(state, path)


def test_resume_refuses_invalid_writing_mode_before_checks(secure_writing_run, monkeypatch):
    from rig_workbench.orchestrate import commands
    from rig_workbench.orchestrate.runstate import save_state

    state, _presenter, path = secure_writing_run()
    state["writing_mode"] = state["secure_runtime"]["writing_mode"] = []
    save_state(state, path)
    monkeypatch.setattr(commands, "_run_checks", lambda *_a, **_kw: pytest.fail("resume ran checks"))
    presenter = RecordingPresenter()
    with pytest.raises(SystemExit) as stopped:
        commands.cmd_resume([str(path)], out=presenter)
    assert stopped.value.code == 2
    assert "writing mode" in "\n".join(presenter.output + presenter.errors)


@pytest.mark.parametrize("bound_mode", [None, "talk"])
@pytest.mark.parametrize("mode_args", [["--mode", "emoji"], ["--mode=talk"], ["--mode"]])
def test_resume_refuses_mode_override_without_mutating_state(
    secure_writing_run, bound_mode, mode_args,
):
    from rig_workbench.orchestrate import commands
    from rig_workbench.orchestrate.runstate import save_state

    state, _presenter, path = secure_writing_run(
        *(["--mode", bound_mode] if bound_mode else []),
    )
    save_state(state, path)
    before = path.read_bytes()
    presenter = RecordingPresenter()
    with pytest.raises(SystemExit) as stopped:
        commands.cmd_resume([str(path), *mode_args], out=presenter)
    assert stopped.value.code == 2
    message = "\n".join(presenter.output + presenter.errors)
    assert "--mode" in message
    assert "fixed at run time" in message
    assert "bound in the run state" in message
    assert path.read_bytes() == before


def test_run_usage_documents_writing_mode():
    from rig_workbench.orchestrate.commands import RUN_USAGE

    # The value list would push `run --help` past the usage-length cap in test_cli_smoke;
    # an invalid value's refusal lists the valid modes instead.
    assert "[--mode MODE[,MODE...]]" in RUN_USAGE
