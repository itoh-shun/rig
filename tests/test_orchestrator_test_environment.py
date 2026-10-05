"""CLI test modules stay independent of a developer's configured T3 server."""
import os
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.parametrize("module", [
    "test_orchestrator_run_cli.py", "test_orchestrator_resume_cli.py",
    "test_doctor.py", "test_cli_smoke.py",
])
def test_cli_module_fixtures_clear_inherited_t3_settings(module):
    root = Path(__file__).resolve().parents[1]
    env = dict(os.environ, RIG_T3_MCP_URL="https://developer.example/mcp",
               RIG_T3_MCP_TOKEN="developer-secret", RIG_T3_PROJECT_ID="developer-project",
               RIG_T3_FUTURE_SETTING="also-isolated")
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q",
         f"tests/{module}::test_t3_environment_is_isolated"],
        cwd=root, env=env, capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("module", [
    "test_orchestrator_run_cli.py", "test_orchestrator_resume_cli.py",
    "test_doctor.py", "test_cli_smoke.py",
])
def test_cli_module_fixtures_clear_the_captured_t3_token(module):
    # Importing the orchestrator moves the token out of the environment into a holder, so
    # the environment check above cannot see it; the holder must be reset as well.
    root = Path(__file__).resolve().parents[1]
    env = dict(os.environ, RIG_T3_MCP_TOKEN="developer-secret")
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q",
         f"tests/{module}::test_t3_holder_is_isolated"],
        cwd=root, env=env, capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
