"""Host adapter layer generalizing native-layer integration beyond Codex (#304).

Golden-fixture coverage: hook-event translation, capability lookups, degrade
declarations, and cross-host event-key consistency.
"""

import importlib.util
import pathlib

_SPEC = importlib.util.spec_from_file_location(
    "host_adapters", pathlib.Path(__file__).resolve().parent.parent / "scripts" / "host_adapters.py"
)
host_adapters = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(host_adapters)


def test_hook_event_precompact_is_unchanged_for_claude_code():
    assert host_adapters.translate_hook_event("PreCompact", "claude-code") == "PreCompact"


def test_hook_event_precompact_is_camel_case_for_cursor():
    assert host_adapters.translate_hook_event("PreCompact", "cursor") == "preCompact"


def test_hook_event_user_prompt_submit_is_before_submit_prompt_for_cursor():
    assert host_adapters.translate_hook_event("UserPromptSubmit", "cursor") == "beforeSubmitPrompt"


def test_hook_event_unknown_host_returns_none():
    assert host_adapters.translate_hook_event("PreCompact", "no-such-host") is None


def test_capability_cursor_precompact_injection_is_unsupported():
    assert host_adapters.capability("cursor", "precompact_context_injection") == "unsupported"


def test_capability_claude_code_precompact_injection_is_supported():
    assert host_adapters.capability("claude-code", "precompact_context_injection") == "supported"


def test_capability_unregistered_feature_is_unsupported_not_silently_supported():
    assert host_adapters.capability("claude-code", "no-such-feature") == "unsupported"


def test_degrade_cursor_precompact_is_declared():
    assert host_adapters.degrade_behavior("cursor", "precompact_context_injection") is not None


def test_degrade_claude_code_baseline_host_has_no_degrades():
    assert host_adapters.degrade_behavior("claude-code", "precompact_context_injection") is None


def test_hook_events_grok_build_pass_through_unchanged():
    # grok-build documents full Claude Code compatibility (#328): canonical
    # event names must survive translation untouched — any renaming here would
    # mean the entry stopped being a native passthrough.
    assert all(host_adapters.translate_hook_event(e, "grok-build") == e
               for e in host_adapters.CANONICAL_EVENTS)


def test_capability_grok_build_is_all_unverified_never_supported():
    # Nothing has been exercised against a live grok CLI, so no capability may
    # claim "supported" — the compat claim is theirs, not ours.
    assert set(host_adapters.HOSTS["grok-build"]["capabilities"].values()) == {"unverified"}


def test_degrade_grok_build_read_only_sandbox_gap_is_declared():
    assert host_adapters.degrade_behavior("grok-build", "read_only_sandbox") is not None


def test_grok_build_reuses_the_claude_code_hooks_config_verbatim():
    assert (host_adapters.HOSTS["grok-build"]["hooks_config_path"]
            == host_adapters.HOSTS["claude-code"]["hooks_config_path"])


def test_every_host_shares_the_same_canonical_event_key_set():
    assert all(set(host_adapters.HOSTS[h]["hook_events"]) == set(host_adapters.CANONICAL_EVENTS)
               for h in host_adapters.HOSTS)


def test_capability_matrix_table_includes_every_host():
    table = host_adapters.capability_matrix_table()
    for spec in host_adapters.HOSTS.values():
        assert spec["display_name"] in table
