"""The boundary between what rig decides and what a CLI happens to call it (#416 Phase 1).

`build_argv` grew one `if provider == ...` branch per vendor, each carrying that vendor's
flag spellings, its sandbox story, and — since #326 — its session-flag shape. Rig Core read
those branches to answer questions that are not really about argv at all: whether two
providers count as independent reviewers, whether a verifier is actually confined, whether
a session can be resumed. The answers were readable only by knowing which strings each
branch appends.

`agent_runtime.py` makes each vendor an adapter that declares what it can do, so Core asks the
capability model instead of pattern-matching a vendor name. Three things that were
previously implicit become checkable here:

- **independence is a backend property, not a label.** `rig` and `claude` are different
  providers that execute through the same binary, so accepting one as an independent review
  of the other would be alias laundering.
- **"read-only verifier" is not one guarantee.** claude enforces it with a tool allowlist,
  codex with an OS sandbox, and grok not at all — grok's verifier is held by the prompt
  contract alone (#328). A capability model states that gap; a branch full of flags hides it.
- **session reuse is a per-CLI capability**, declared by the adapter and probed by
  `sessions.py` against the installed binary; the fallback lands in the run history.

The argv matrix below is the safety net for the extraction itself: it is the output of the
pre-refactor `build_argv`, frozen. Every adapter change has to keep it byte for byte, or say
out loud that a provider's invocation changed.
"""

import pathlib
import re

import pytest

from rig_workbench.orchestrate import agent_runtime as runtime
from rig_workbench.orchestrate import providers, sessions

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent

# Captured from build_argv before the adapters existed. `mock` is asserted structurally
# instead (its argv embeds the whole mock program).
ARGV_GOLDEN = {
    ("rig", "generator", (), ""): [
        "claude", "-p", runtime.RIG_GEN_PREFIX + "P", "--output-format", "text",
        "--permission-mode", "acceptEdits"],
    ("rig", "verifier", (), ""): [
        "claude", "-p", runtime.RIG_VER_PREFIX + "P", "--output-format", "text",
        "--allowedTools", "Read,Grep,Glob"],
    ("claude", "generator", (), ""): [
        "claude", "-p", "P", "--output-format", "text", "--permission-mode", "acceptEdits"],
    ("claude", "verifier", (), ""): [
        "claude", "-p", "P", "--output-format", "text", "--allowedTools", "Read,Grep,Glob"],
    ("claude", "generator", (("model", "claude-opus-4-8"),), ""): [
        "claude", "-p", "P", "--output-format", "text", "--model", "claude-opus-4-8",
        "--permission-mode", "acceptEdits"],
    ("claude", "verifier", (("claude_no_session_persistence", True),), ""): [
        "claude", "-p", "P", "--output-format", "text", "--no-session-persistence",
        "--allowedTools", "Read,Grep,Glob"],
    ("codex", "generator", (), ""): [
        "codex", "exec", "--skip-git-repo-check", "--sandbox", "workspace-write", "P"],
    ("codex", "verifier", (), ""): [
        "codex", "exec", "--skip-git-repo-check", "--sandbox", "read-only", "P"],
    ("codex", "generator", (("model", "gpt-5"),), ""): [
        "codex", "exec", "--skip-git-repo-check", "--sandbox", "workspace-write", "-m", "gpt-5", "P"],
    ("grok", "generator", (), ""): ["grok", "-p", "P", "--output-format", "plain"],
    ("grok", "verifier", (("model", "grok-4"),), ""): [
        "grok", "-p", "P", "--output-format", "plain", "-m", "grok-4"],
    ("cmd", "generator", (("provider_cmd", "wrap --role {role} -- {prompt}"),), "px"): [
        "wrap", "--role", "generator", "--", "P"],
}


@pytest.mark.parametrize("key", list(ARGV_GOLDEN))
def test_extracting_the_adapters_did_not_change_a_single_invocation(key):
    provider, role, cfg_items, persona = key
    argv = providers.build_argv(provider, role, "P", dict(cfg_items), persona)
    assert argv == ARGV_GOLDEN[key]


def test_the_mock_runtime_still_runs_the_mock_program_in_its_own_interpreter():
    argv = providers.build_argv("mock", "verifier", "P", {}, "sec")
    assert argv[1:] == ["-c", runtime.MOCK_SRC, "verifier", "sec"]
    assert argv[0].endswith("python3") or "python" in argv[0]


def test_every_provider_build_argv_accepts_resolves_to_an_adapter():
    accepted = {"mock", "rig", "claude", "codex", "grok", "cmd"}
    assert set(runtime.REGISTRY) == accepted
    for label in accepted:
        assert runtime.runtime_for(label).capabilities.label == label


def test_an_unknown_provider_still_fails_the_way_it_always_did():
    with pytest.raises(SystemExit, match="unknown provider: nope"):
        providers.build_argv("nope", "generator", "P", {})


def test_independence_is_decided_by_execution_backend_not_by_provider_label():
    """`rig` is a prompt mode over the claude binary — accepting it as an independent
    review of `claude` would be alias laundering, so both declare one backend."""
    backend = {label: runtime.runtime_for(label).capabilities.backend
               for label in runtime.REGISTRY}
    assert backend["rig"] == backend["claude"] == "claude-cli"
    assert backend["codex"] == "codex"
    assert backend["grok"] == "grok"
    assert providers._effective_provider_backend("rig") == "claude-cli"


@pytest.mark.parametrize("label", ["ollama", "lmstudio", "anthropic", "same-provider"])
def test_a_provider_with_no_cli_adapter_is_still_its_own_backend(label):
    """Independence is asked about labels no adapter will ever build argv for: HTTP model
    providers reach a model directly, and callers name their own. Each has to stay distinct
    from every other label, and resolving one must not be fatal — which is exactly what
    `runtime_for` is required to be, so the two questions are separate."""
    assert runtime.backend_for(label) == label
    assert providers._effective_provider_backend(label) == label
    with pytest.raises(SystemExit):
        runtime.runtime_for(label)


def test_verifier_confinement_is_declared_per_runtime_including_where_it_is_missing():
    """The strength of "read-only verifier" differs per CLI, and one of them has none."""
    confinement = {label: runtime.runtime_for(label).capabilities.verifier_confinement
                   for label in runtime.REGISTRY}
    assert confinement["claude"] == "tool-allowlist"
    assert confinement["rig"] == "tool-allowlist"
    assert confinement["codex"] == "os-sandbox"
    # #328, stated rather than buried in a comment: grok headless documents no read-only
    # flag, so the verifier's stance rests on the prompt contract alone.
    assert confinement["grok"] == "prompt-only"


def test_session_reuse_is_a_declared_capability_and_codex_declines_it():
    assert runtime.runtime_for("claude").capabilities.session_flags == ("--session-id", "--resume")
    assert runtime.runtime_for("rig").capabilities.session_flags == ("--session-id", "--resume")
    assert runtime.runtime_for("grok").capabilities.session_flags == ("--session-id", "--resume")
    assert runtime.runtime_for("codex").capabilities.session_flags is None
    assert runtime.runtime_for("codex").capabilities.supports_session_reuse is False
    assert runtime.runtime_for("claude").capabilities.supports_session_reuse is True


def test_the_adapter_table_and_the_session_probe_table_agree():
    """One table, read from two places. An adapter that declared flags `sessions.py` never
    probes for would be claiming a capability nothing checks, and the reverse would probe a
    CLI the adapter never resumes."""
    for label, adapter in runtime.REGISTRY.items():
        flags = adapter.capabilities.session_flags
        probed = sessions.SESSION_FLAGS.get(adapter.capabilities.session_binary or label)
        if flags is None:
            assert probed is None or adapter.capabilities.session_binary is None, label
        else:
            assert probed == {"start": flags[0], "resume": flags[1]}, label


def test_a_declined_session_reuse_still_records_why_in_the_run_history():
    """The adapter asks `sessions` even when the answer is always `[]`, so a run that asked
    for reuse on codex learns from its history that it did not happen, and why."""
    state: dict = {"history": []}
    argv = providers.build_argv("codex", "generator", "P", {"reuse_session": True}, state=state)
    assert argv == ARGV_GOLDEN[("codex", "generator", (), "")]
    fallbacks = [h for h in state["history"] if h.get("action") == "SESSION_REUSE_FALLBACK"]
    assert [f["provider"] for f in fallbacks] == ["codex"]
    assert "no documented session flags" in fallbacks[0]["reason"]


def test_a_verifier_never_resumes_the_generator_s_session(monkeypatch):
    monkeypatch.setattr(sessions, "supports", lambda provider, cfg=None: (True, ""))
    cfg = {"reuse_session": True}
    first = providers.build_argv("claude", "generator", "P", cfg)
    assert "--session-id" in first
    verifier = providers.build_argv("claude", "verifier", "P", cfg)
    assert "--session-id" not in verifier and "--resume" not in verifier
    second = providers.build_argv("claude", "generator", "P", cfg)
    assert "--resume" in second and second[second.index("--resume") + 1] == first[first.index("--session-id") + 1]


def test_the_orchestrator_core_names_no_vendor_cli_flags():
    """Core decides; adapters speak vendor. A flag spelling leaking back into a decision
    module is the boundary #416 asks for quietly coming undone.

    Two things are deliberately outside this scan, because neither is argv construction.
    `cli.py` is the usage text — describing what `--isolate` pins for a verifier is its job.
    And `--no-session-persistence` is rig's *own* option, parsed by `commands.py` into cfg
    and spelled by the claude adapter; it is a vendor word in rig's public flag surface,
    which is Phase 2's problem, not this one's. `sessions.py` is the probe table for the
    adapters' declared flags, and is listed beside them, not beside Core.
    """
    vendor_flags = re.compile(r"--(allowedTools|permission-mode|sandbox|session-id|resume|"
                              r"skip-git-repo-check)")
    core = ["commands.py", "recipes.py", "runstate.py", "queueing.py"]
    offenders = []
    for name in core:
        path = REPO_ROOT / "rig_workbench" / "orchestrate" / name
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if vendor_flags.search(line):
                offenders.append(f"{name}:{number}: {line.strip()}")
    assert not offenders, (
        "vendor CLI flags appear outside the runtime adapters:\n" + "\n".join(offenders))


def test_providers_no_longer_spells_any_vendor_argv_itself():
    """The extraction's own claim: after Phase 1, `providers.py` holds no vendor branch."""
    source = (REPO_ROOT / "rig_workbench" / "orchestrate" / "providers.py").read_text()
    assert 'if provider == "codex"' not in source
    assert '"--skip-git-repo-check"' not in source
    assert "--permission-mode" not in source
