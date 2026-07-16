"""Token/cost usage metering for HTTP-based providers (#271, #296).

_record_token_usage rolls up an OpenAI-compatible `usage` payload into a per-run
accumulator; telemetry_append threads it into .rig/runs.jsonl as `token_usage`.
"""

import json

from rig_workbench.orchestrate.providers import _record_token_usage
from rig_workbench.orchestrate.runstate import new_state, telemetry_append


def test_record_token_usage_accumulates_across_calls():
    cfg = {"_token_usage": {}}
    _record_token_usage(cfg, "ollama", {"prompt_tokens": 10, "completion_tokens": 5})
    _record_token_usage(cfg, "ollama", {"prompt_tokens": 7, "completion_tokens": 3})
    assert cfg["_token_usage"]["ollama"] == {"prompt_tokens": 17, "completion_tokens": 8, "calls": 2}


def test_record_token_usage_keeps_providers_separate():
    cfg = {"_token_usage": {}}
    _record_token_usage(cfg, "ollama", {"prompt_tokens": 10, "completion_tokens": 5})
    _record_token_usage(cfg, "lmstudio", {"prompt_tokens": 1, "completion_tokens": 1})
    assert set(cfg["_token_usage"]) == {"ollama", "lmstudio"}


def test_record_token_usage_noop_without_accumulator():
    cfg = {}  # no "_token_usage" key: CLI providers / callers that opted out
    _record_token_usage(cfg, "ollama", {"prompt_tokens": 10, "completion_tokens": 5})
    assert "_token_usage" not in cfg


def test_telemetry_append_writes_token_usage(tmp_path, monkeypatch, step_factory):
    from rig_workbench.orchestrate import config

    monkeypatch.setattr(config, "RUNS_PATH", tmp_path / "runs.jsonl")
    monkeypatch.setattr(config, "GLOBAL_RUNS_PATH", tmp_path / "global-runs.jsonl", raising=False)
    step = step_factory(id="a")
    state = new_state("demo", [step], goal=None)
    state["step_state"]["a"]["status"] = "passed"
    state["token_usage"] = {"ollama": {"prompt_tokens": 3, "completion_tokens": 2, "calls": 1}}
    telemetry_append(state, "DONE")
    rec = json.loads(config.RUNS_PATH.read_text(encoding="utf-8").splitlines()[-1])
    assert rec["token_usage"] == {"ollama": {"prompt_tokens": 3, "completion_tokens": 2, "calls": 1}}


def test_telemetry_append_defaults_token_usage_to_empty(tmp_path, monkeypatch, step_factory):
    from rig_workbench.orchestrate import config

    monkeypatch.setattr(config, "RUNS_PATH", tmp_path / "runs.jsonl")
    monkeypatch.setattr(config, "GLOBAL_RUNS_PATH", tmp_path / "global-runs.jsonl", raising=False)
    step = step_factory(id="a")
    state = new_state("demo", [step], goal=None)
    state["step_state"]["a"]["status"] = "passed"
    telemetry_append(state, "DONE")
    rec = json.loads(config.RUNS_PATH.read_text(encoding="utf-8").splitlines()[-1])
    assert rec["token_usage"] == {}
