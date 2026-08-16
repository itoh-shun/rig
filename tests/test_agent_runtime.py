"""The boundary between what rig decides and what a CLI happens to call it (#416 Phase 1).

`build_argv` grew one `if provider == ...` branch per vendor, each carrying that
vendor's flag spellings, its sandbox story, and — since #326 — its session-flag
shape. Rig Core reads those branches to answer questions that are not really about
argv at all: whether two providers count as independent reviewers, whether a
verifier is actually confined, whether a session can be resumed. The answers were
readable only by knowing which strings each branch appends.

`runtime.py` makes each vendor an adapter that declares what it can do, so Core asks
the capability model instead of pattern-matching a vendor name. Three things that
were previously implicit become checkable here:

- **independence is a backend property, not a label.** `rig` and `claude` are
  different providers that execute through the same binary, so accepting one as an
  independent review of the other would be alias laundering.
- **"read-only verifier" is not one guarantee.** claude enforces it with a tool
  allowlist, codex with an OS sandbox, and grok not at all — grok's verifier is
  held by the prompt contract alone (#328). A capability model states that gap;
  a branch full of flags hides it.
- **session reuse is a per-CLI capability**, so codex declines it by declaring no
  flags rather than by being absent from a dict somewhere else in the file.

The argv matrix below is the safety net for the extraction itself: it is the output
of the pre-refactor `build_argv`, frozen. Every adapter change has to keep it byte
for byte, or say out loud that a provider's invocation changed.
"""

import pathlib
import re

import pytest

from rig_workbench.orchestrate import providers, runtime

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent

# Captured from build_argv before the adapters existed. `mock` is asserted
# structurally instead (its argv embeds the whole mock program).
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
    """Independence is asked about labels no adapter will ever build argv for: HTTP
    model providers reach a model directly, and callers name their own. Each has to
    stay distinct from every other label, and resolving one must not be fatal — which
    is exactly what `runtime_for` is required to be, so the two questions are separate.
    """
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
    # #328, stated rather than buried in a comment: grok headless documents no
    # read-only flag, so the verifier's stance rests on the prompt contract alone.
    assert confinement["grok"] == "prompt-only"


def test_session_reuse_is_a_declared_capability_and_codex_declines_it():
    assert runtime.runtime_for("claude").capabilities.session_flags == ("--session-id", "--resume")
    assert runtime.runtime_for("rig").capabilities.session_flags == ("--session-id", "--resume")
    assert runtime.runtime_for("grok").capabilities.session_flags == ("--session-id", "--resume")
    assert runtime.runtime_for("codex").capabilities.session_flags is None
    assert runtime.runtime_for("codex").capabilities.supports_session_reuse is False
    assert runtime.runtime_for("claude").capabilities.supports_session_reuse is True


def test_the_session_binary_is_the_one_the_adapter_actually_execs():
    """`rig` resumes through the claude binary, so that is the binary to probe."""
    assert runtime.runtime_for("rig").capabilities.session_binary == "claude"
    assert runtime.runtime_for("claude").capabilities.session_binary == "claude"
    assert runtime.runtime_for("grok").capabilities.session_binary == "grok"


def test_a_declined_session_reuse_still_records_why(monkeypatch):
    monkeypatch.setattr(runtime, "_cli_supports_session", lambda binary: True)
    cfg = {"reuse_session": True, "session_ids": {}}
    assert providers._session_reuse_argv("codex", "generator", cfg) == []
    reasons = [n["reason"] for n in cfg["session_reuse_notes"] if n["provider"] == "codex"]
    assert reasons and "session" in reasons[0]


def test_the_orchestrator_core_names_no_vendor_cli_flags():
    """Core decides; adapters speak vendor. A flag spelling leaking back into a
    decision module is the boundary #416 asks for quietly coming undone.

    Two things are deliberately outside this scan, because neither is argv
    construction. `cli.py` is the usage text — describing what `--isolate` pins for a
    verifier is its job. And `--no-session-persistence` is rig's *own* option, parsed
    by `commands.py` into cfg and spelled by the claude adapter; it is a vendor word
    in rig's public flag surface, which is Phase 2's problem, not this one's.
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
