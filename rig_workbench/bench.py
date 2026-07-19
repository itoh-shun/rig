"""rig-wb bench — bare vs rig A/B benchmark runner (MVP).

With the same LLM:
  - bare mode: solve the task in one shot via `claude -p` / `codex exec` / ollama HTTP, etc.
  - rig mode:  run the recipe via `rig-wb run <recipe> --provider <same>`

Each task is placed in a scratch worktree under /tmp/, solved in both modes,
then the same tests are run and metrics are collected into JSON.
**Default provider = `mock`** (for framework smoke-testing; no billing);
pass `--provider claude` etc. explicitly for real measurements.

MVP limitations:
  - external YAML specs come later
  - --runs N is the number of repetitions per task (to inspect variance)

Usage:
    rig-wb bench --provider mock --out /tmp/bench.json --html /tmp/bench.html
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import pathlib
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request

from . import __version__


# ── built-in task definitions ──────────────────────────────────────────


BUILTIN_TASKS: dict[str, dict] = {
    "divide-by-zero": {
        "difficulty": "simple",
        "files": {
            "buggy.py": (
                "def divide_all(numbers, divisor):\n"
                "    \"\"\"\n"
                "    Return each element of the list divided by divisor.\n"
                "    However, if divisor is 0, return the original elements unchanged.\n"
                "    \"\"\"\n"
                "    result = []\n"
                "    for n in numbers:\n"
                "        result.append(n / divisor)   # BUG: ZeroDivisionError when divisor==0\n"
                "    return result\n"
            ),
            "test_divide.py": (
                "from buggy import divide_all\n\n"
                "def test_normal():\n"
                "    assert divide_all([10, 20, 30], 2) == [5.0, 10.0, 15.0]\n\n"
                "def test_zero_divisor():\n"
                "    # Spec: if divisor is 0, return the original elements unchanged\n"
                "    assert divide_all([1, 2, 3], 0) == [1, 2, 3]\n\n"
                "def test_empty():\n"
                "    assert divide_all([], 5) == []\n"
            ),
        },
        "target_file": "buggy.py",
        "test_cmd": ["python3", "-m", "pytest", "test_divide.py", "--tb=no", "-q"],
        "goal": "Fix the bug in buggy.py. Do not modify the tests.",
        "spec_check_code": (
            "from buggy import divide_all\n"
            "assert divide_all([1, 2, 3], 0) == [1, 2, 3], 'spec violation'"
        ),
        "mock_fix": (
            "def divide_all(numbers, divisor):\n"
            "    if divisor == 0:\n"
            "        return list(numbers)\n"
            "    return [n / divisor for n in numbers]\n"
        ),
    },
    "order-dedup": {
        "difficulty": "medium",
        "files": {
            "order_dedup.py": (
                "def dedup(items):\n"
                "    \"\"\"\n"
                "    Return a list with duplicates removed, preserving first-seen order.\n"
                "      dedup([3, 1, 2, 1, 3]) == [3, 1, 2]\n"
                "      dedup(['a', 'b', 'a', 'c']) == ['a', 'b', 'c']\n"
                "    \"\"\"\n"
                "    return list(set(items))   # BUG: set() does not preserve order\n"
            ),
            "test_order_dedup.py": (
                "from order_dedup import dedup\n\n"
                "def test_unique():\n"
                "    # Weak test: only checks equality as a set\n"
                "    assert set(dedup([3, 1, 2, 1, 3])) == {1, 2, 3}\n\n"
                "def test_length():\n"
                "    assert len(dedup([3, 1, 2, 1, 3])) == 3\n"
            ),
        },
        "target_file": "order_dedup.py",
        "test_cmd": ["python3", "-m", "pytest", "test_order_dedup.py", "--tb=no", "-q"],
        "goal": "order_dedup.py may contain a bug. Fix it if a fix is needed. Do not modify the tests.",
        "spec_check_code": (
            "from order_dedup import dedup\n"
            "assert dedup([3, 1, 2, 1, 3]) == [3, 1, 2], 'spec violation (order preservation)'"
        ),
        "mock_fix": (
            "def dedup(items):\n"
            "    return list(dict.fromkeys(items))\n"
        ),
    },
    "sql-inject": {
        "difficulty": "security",
        "files": {
            "userdb.py": (
                "import sqlite3\n\n"
                "def get_user_by_name(conn: sqlite3.Connection, name: str) -> tuple | None:\n"
                "    \"\"\"Return one row from the users table matched by name.\n"
                "    Hint: name is user input (untrusted).\n"
                "    \"\"\"\n"
                "    # BUG: string concatenation invites SQL injection\n"
                "    cur = conn.cursor()\n"
                "    cur.execute(f\"SELECT id, name, role FROM users WHERE name = '{name}'\")\n"
                "    return cur.fetchone()\n"
            ),
            "test_userdb.py": (
                "import sqlite3, pytest\n"
                "from userdb import get_user_by_name\n\n"
                "@pytest.fixture\n"
                "def conn():\n"
                "    c = sqlite3.connect(':memory:')\n"
                "    c.execute(\"CREATE TABLE users(id INTEGER, name TEXT, role TEXT)\")\n"
                "    c.execute(\"INSERT INTO users VALUES(1, 'alice', 'admin')\")\n"
                "    c.execute(\"INSERT INTO users VALUES(2, 'bob', 'user')\")\n"
                "    return c\n\n"
                "def test_normal(conn):\n"
                "    row = get_user_by_name(conn, 'alice')\n"
                "    assert row == (1, 'alice', 'admin')\n\n"
                "def test_not_found(conn):\n"
                "    row = get_user_by_name(conn, 'zoe')\n"
                "    assert row is None\n"
            ),
        },
        "target_file": "userdb.py",
        "test_cmd": ["python3", "-m", "pytest", "test_userdb.py", "--tb=no", "-q"],
        "goal": (
            "userdb.py has a security issue. Fix it. "
            "Do not modify the tests. Return only the full fixed userdb.py in a ```python ... ``` block."
        ),
        "spec_check_code": (
            # Does malicious input execute DROP TABLE? Does quote escaping grant admin?
            "import sqlite3\n"
            "from userdb import get_user_by_name\n"
            "c = sqlite3.connect(':memory:')\n"
            "c.execute(\"CREATE TABLE users(id INTEGER, name TEXT, role TEXT)\")\n"
            "c.execute(\"INSERT INTO users VALUES(1, 'alice', 'admin')\")\n"
            "# SQL injection attempt 1: try to dump the entire users table\n"
            "r1 = get_user_by_name(c, \"' OR '1'='1\")\n"
            "assert r1 is None, f'SQL injection got through: {r1}'\n"
            "# SQL injection attempt 2: try to drop another table\n"
            "try:\n"
            "    get_user_by_name(c, \"'; DROP TABLE users; --\")\n"
            "except Exception:\n"
            "    pass\n"
            "assert c.execute(\"SELECT COUNT(*) FROM users\").fetchone()[0] == 1, "
            "'DROP TABLE got through'\n"
        ),
        "mock_fix": (
            "import sqlite3\n\n"
            "def get_user_by_name(conn: sqlite3.Connection, name: str) -> tuple | None:\n"
            "    \"\"\"Return one row from the users table matched by name. Uses a parametrized query.\"\"\"\n"
            "    cur = conn.cursor()\n"
            "    cur.execute(\"SELECT id, name, role FROM users WHERE name = ?\", (name,))\n"
            "    return cur.fetchone()\n"
        ),
    },
    "dry-refactor": {
        "difficulty": "refactor",
        "files": {
            "shipping.py": (
                "def price_domestic(weight_kg: float) -> int:\n"
                "    \"\"\"Domestic shipping fee. Round up in 0.5kg increments, 200 yen per unit, 500 yen minimum.\"\"\"\n"
                "    import math\n"
                "    units = math.ceil(weight_kg / 0.5)\n"
                "    return max(500, units * 200)\n\n"
                "def price_domestic_cool(weight_kg: float) -> int:\n"
                "    \"\"\"Domestic refrigerated shipping. Same logic as price_domestic but 300 yen per unit, 800 yen minimum.\"\"\"\n"
                "    # BUG: hand-written duplicate of price_domestic's logic, and the ceiling is missing\n"
                "    units = int(weight_kg / 0.5)   # <- truncates here, violating the spec\n"
                "    return max(800, units * 300)\n"
            ),
            "test_shipping.py": (
                "from shipping import price_domestic, price_domestic_cool\n\n"
                "def test_domestic_min():\n"
                "    assert price_domestic(0.1) == 500\n\n"
                "def test_domestic_border():\n"
                "    assert price_domestic(1.0) == 500\n"
                "    assert price_domestic(1.5) == 600\n\n"
                "def test_cool_min():\n"
                "    assert price_domestic_cool(0.1) == 800\n"
                "    # Weak test: no intermediate values covered\n"
            ),
        },
        "target_file": "shipping.py",
        "test_cmd": ["python3", "-m", "pytest", "test_shipping.py", "--tb=no", "-q"],
        "goal": (
            "shipping.py has a bug and duplicated code. Fix them. "
            "Do not modify the tests. Return only the full fixed shipping.py in a ```python ... ``` block."
        ),
        "spec_check_code": (
            "from shipping import price_domestic_cool\n"
            "# Spec: round up in 0.5kg increments, 300 per unit, 800 minimum\n"
            "assert price_domestic_cool(1.1) == 900, "
            "f'missing round-up bug still present: got {price_domestic_cool(1.1)} expected 900'\n"
            "assert price_domestic_cool(2.0) == 1200, "
            "f'{price_domestic_cool(2.0)} != 1200'\n"
        ),
        "mock_fix": (
            "import math\n\n"
            "def _price(weight_kg: float, unit_price: int, floor: int) -> int:\n"
            "    units = math.ceil(weight_kg / 0.5)\n"
            "    return max(floor, units * unit_price)\n\n"
            "def price_domestic(weight_kg: float) -> int:\n"
            "    \"\"\"Domestic shipping fee. Round up in 0.5kg increments, 200 yen per unit, 500 yen minimum.\"\"\"\n"
            "    return _price(weight_kg, 200, 500)\n\n"
            "def price_domestic_cool(weight_kg: float) -> int:\n"
            "    \"\"\"Domestic refrigerated shipping. 300 yen per unit, 800 yen minimum.\"\"\"\n"
            "    return _price(weight_kg, 300, 800)\n"
        ),
    },
    "auth-bypass-sibling": {
        # Designed for #330's Claim B: does a review pass catch what a narrow
        # one-shot fix of the LITERALLY reported symptom misses? The bug
        # report names get_profile only; update_profile has the identical
        # missing-ownership-check pattern one method down, mentioned nowhere
        # in the goal or the (deliberately weak) visible tests. A bare answer
        # that patches exactly what was asked plausibly leaves it open; a
        # security-reviewer persona's job is to notice the sibling.
        "difficulty": "security",
        "files": {
            "profile_service.py": (
                "class ProfileService:\n"
                "    \"\"\"In-memory profile store. current_user_id is who is making the "
                "call; requested_user_id is whose profile is being accessed.\"\"\"\n"
                "    def __init__(self):\n"
                "        self._profiles = {}\n\n"
                "    def create_profile(self, user_id, data):\n"
                "        self._profiles[user_id] = dict(data)\n\n"
                "    def get_profile(self, current_user_id, requested_user_id):\n"
                "        \"\"\"Return the requested profile.\n"
                "        BUG (reported): any logged-in user can read any other user's\n"
                "        profile by passing a different requested_user_id — no ownership\n"
                "        check.\n"
                "        \"\"\"\n"
                "        return self._profiles.get(requested_user_id)\n\n"
                "    def update_profile(self, current_user_id, requested_user_id, data):\n"
                "        \"\"\"Update the requested profile. Has the exact same "
                "missing-ownership-check pattern as get_profile, but this is not "
                "mentioned in the bug report.\"\"\"\n"
                "        if requested_user_id not in self._profiles:\n"
                "            return False\n"
                "        self._profiles[requested_user_id].update(data)\n"
                "        return True\n"
            ),
            "test_profile_service.py": (
                "from profile_service import ProfileService\n\n"
                "def _svc():\n"
                "    s = ProfileService()\n"
                "    s.create_profile('alice', {'bio': 'hi'})\n"
                "    s.create_profile('bob', {'bio': 'yo'})\n"
                "    return s\n\n"
                "def test_owner_can_read_own_profile():\n"
                "    s = _svc()\n"
                "    assert s.get_profile('alice', 'alice') == {'bio': 'hi'}\n\n"
                "def test_stranger_cannot_read_other_profile():\n"
                "    # This is the ONLY reported bug: fix so this passes.\n"
                "    s = _svc()\n"
                "    assert s.get_profile('bob', 'alice') is None\n"
                "    # Weak test: update_profile's cross-user case is never exercised.\n"
            ),
        },
        "target_file": "profile_service.py",
        "test_cmd": ["python3", "-m", "pytest", "test_profile_service.py", "--tb=no", "-q"],
        "goal": (
            "Users report they can view other users' profiles: calling get_profile "
            "with someone else's user id returns that person's data, even though "
            "they aren't that user. Fix this. Do not modify the existing tests "
            "(adding a new test file is allowed). Return only "
            "the full fixed profile_service.py in a ```python ... ``` block."
        ),
        "spec_check_code": (
            "from profile_service import ProfileService\n"
            "s = ProfileService()\n"
            "s.create_profile('alice', {'bio': 'hi'})\n"
            "s.create_profile('bob', {'bio': 'yo'})\n"
            "# The reported bug, re-verified\n"
            "assert s.get_profile('bob', 'alice') is None, 'reported bug not fixed'\n"
            "assert s.get_profile('alice', 'alice') == {'bio': 'hi'}, "
            "'owner access broken by the fix'\n"
            "# The same missing-ownership-check pattern exists in update_profile, one\n"
            "# method down, never mentioned in the bug report or the tests. A narrow\n"
            "# patch of only get_profile leaves this open.\n"
            "s.update_profile('bob', 'alice', {'bio': 'HACKED'})\n"
            "assert s._profiles['alice']['bio'] == 'hi', "
            "f'update_profile cross-user write not blocked: {s._profiles[\"alice\"]}'\n"
        ),
        "mock_fix": (
            "class ProfileService:\n"
            "    def __init__(self):\n"
            "        self._profiles = {}\n\n"
            "    def create_profile(self, user_id, data):\n"
            "        self._profiles[user_id] = dict(data)\n\n"
            "    def get_profile(self, current_user_id, requested_user_id):\n"
            "        if current_user_id != requested_user_id:\n"
            "            return None\n"
            "        return self._profiles.get(requested_user_id)\n\n"
            "    def update_profile(self, current_user_id, requested_user_id, data):\n"
            "        if current_user_id != requested_user_id:\n"
            "            return False\n"
            "        if requested_user_id not in self._profiles:\n"
            "            return False\n"
            "        self._profiles[requested_user_id].update(data)\n"
            "        return True\n"
        ),
    },
}


_MOCK_FIX_FOR: dict[str, str] = {}   # deprecated (use task["mock_fix"])


# ── task setup ─────────────────────────────────────────────────────────


def _setup_task_dir(task: dict) -> pathlib.Path:
    """Expand the task's files into a fresh tmp dir, init git, and make the initial commit."""
    d = pathlib.Path(tempfile.mkdtemp(prefix="rig-bench-"))
    for name, content in task["files"].items():
        (d / name).write_text(content, encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=d, check=True)
    subprocess.run(["git", "config", "user.email", "bench@rig.local"], cwd=d, check=True)
    subprocess.run(["git", "config", "user.name", "rig-bench"], cwd=d, check=True)
    subprocess.run(["git", "add", "."], cwd=d, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "bench setup"], cwd=d, check=True)
    return d


def _reset_task_dir(d: pathlib.Path) -> None:
    subprocess.run(["git", "checkout", "-q", "."], cwd=d, check=True)
    # ignore __pycache__
    for pc in d.rglob("__pycache__"):
        shutil.rmtree(pc, ignore_errors=True)


def _run_tests(task: dict, d: pathlib.Path) -> dict:
    r = subprocess.run(task["test_cmd"], cwd=d, capture_output=True, text=True, timeout=60)
    passed = int((re.search(r"(\d+) passed", r.stdout) or ["0", "0"])[1] if hasattr(re.search(r"(\d+) passed", r.stdout), "group") else 0)
    # the one-liner above is hard to read, so do it plainly
    m_p = re.search(r"(\d+) passed", r.stdout)
    m_f = re.search(r"(\d+) failed", r.stdout)
    passed = int(m_p.group(1)) if m_p else 0
    failed = int(m_f.group(1)) if m_f else 0
    return {"passed": passed, "failed": failed, "exit": r.returncode}


def _spec_check(task: dict, d: pathlib.Path) -> str:
    """Run spec_check_code. Returns PASS or FAIL: <detail>."""
    try:
        subprocess.run(
            ["python3", "-c", task["spec_check_code"]],
            cwd=d, capture_output=True, text=True, timeout=15, check=True,
        )
        return "PASS"
    except subprocess.CalledProcessError as e:
        return f"FAIL: {e.stderr.strip().splitlines()[-1] if e.stderr.strip() else 'assertion'}"


def _unrelated_diff(d: pathlib.Path, target: str) -> list[str]:
    r = subprocess.run(["git", "diff", "--name-only"], cwd=d, capture_output=True, text=True)
    files = [f for f in r.stdout.strip().splitlines() if f and not f.endswith(".pyc") and "__pycache__" not in f]
    return [f for f in files if f != target]


def _git_status_lines(root: pathlib.Path) -> set[str]:
    """Return the calling repo's dirty state as a set. Treated as empty if not a git repo."""
    r = subprocess.run(
        ["git", "-C", str(root), "status", "--porcelain"],
        capture_output=True,
        text=True,
    )
    if r.returncode != 0:
        return set()
    return {line for line in r.stdout.splitlines() if line.strip()}


def _workspace_leaks(root: pathlib.Path, before: set[str]) -> list[str]:
    """Detect git status lines that newly appeared outside the bench scratch dir."""
    after = _git_status_lines(root)
    return sorted(after - before)


# ── mode: bare ─────────────────────────────────────────────────────────


def _build_bare_prompt(task: dict) -> str:
    files_text = "\n\n".join(
        f"# {name}\n{content}" for name, content in task["files"].items()
    )
    return (
        f"{task['goal']}\n\n"
        f"Return only the full fixed {task['target_file']} in a ```python ... ``` block. No other explanation.\n\n"
        f"{files_text}"
    )


def _extract_code(text: str) -> str:
    m = re.search(r"```(?:python)?\s*(.*?)```", text, re.DOTALL)
    return (m.group(1) if m else text).strip()


def _call_provider(provider: str, prompt: str, model: str | None, allow_headless_in_cc: bool,
                    cwd: pathlib.Path,
                    mock_fix: str = "") -> tuple[str, float]:
    """Send the prompt to the provider and return (response_text, elapsed_s).

    With provider="mock", returns `mock_fix` as the code (framework smoke-testing;
    no LLM call).
    """
    t0 = time.time()
    if provider == "claude":
        if not allow_headless_in_cc and (os.environ.get("CLAUDECODE") or os.environ.get("CLAUDE_CODE_SESSION_ID")):
            raise SystemExit(
                "[bench] --provider claude from inside Claude Code is BLOCKED by default (billing safety). "
                "Pass --allow-headless-in-cc explicitly for real measurements."
            )
        argv = ["claude", "-p", prompt, "--output-format", "text"]
        if model:
            argv += ["--model", model]
        r = subprocess.run(argv, cwd=cwd, capture_output=True, text=True, timeout=300)
        return r.stdout, time.time() - t0
    if provider == "codex":
        # bare mode is the "answer in one shot" baseline, so restrict it to reading
        # the scratch repo only. This prevents measurement contamination where Codex
        # writes helper files into the calling repo root.
        argv = [
            "codex", "exec", "--skip-git-repo-check",
            "--cd", str(cwd), "--sandbox", "read-only", "--ephemeral",
        ]
        if model:
            argv += ["-m", model]
        argv += [prompt]
        r = subprocess.run(argv, cwd=cwd, capture_output=True, text=True, timeout=300)
        return r.stdout, time.time() - t0
    if provider in ("ollama", "lmstudio"):
        base = "http://localhost:11434/v1" if provider == "ollama" else "http://localhost:1234/v1"
        req = urllib.request.Request(
            f"{base}/chat/completions",
            data=json.dumps({
                "model": model or "local-model",
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.2,
                "max_tokens": 1200,
            }).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=300) as resp:
            data = json.loads(resp.read())
        return data["choices"][0]["message"]["content"], time.time() - t0
    if provider == "mock":
        return f"```python\n{mock_fix}\n```", 0.01
    raise SystemExit(f"[bench] Unknown provider: {provider}")


# ── mode: rig ──────────────────────────────────────────────────────────


def _rig_wb_argv() -> list[str]:
    """The rig-wb invocation used by bench.

    By default, calls the currently loaded package via `python -m rig_workbench.cli`,
    so bench measures the same version of the runner even when an older rig-wb is
    on PATH. Only set `RIG_BENCH_RIG_WB` to a shell-style argv when you want to
    measure an external command instead.
    """
    override = os.environ.get("RIG_BENCH_RIG_WB")
    if override:
        return shlex.split(override)
    return [sys.executable, "-m", "rig_workbench.cli"]


def _rig_run(task: dict, workdir: pathlib.Path, provider: str, model: str | None,
             allow_headless_in_cc: bool, max_steps: int, recipe: str) -> tuple[str, float, int]:
    """Run rig-wb run in workdir and return (stdout, elapsed_s, returncode)."""
    # The rig side gets an "edit and make the tests pass" contract. Do not mix in
    # the bare-mode "return the full file" instruction.
    files_text = "\n\n".join(f"# {name}\n{content}" for name, content in task["files"].items())
    goal = (
        f"{task['goal']}\n\n"
        f"Target file: {task['target_file']}\n"
        f"Tests: {' '.join(shlex.quote(x) for x in task['test_cmd'])}\n\n"
        f"Reference files:\n{files_text}"
    )
    env = dict(os.environ)
    candidate = pathlib.Path(__file__).resolve().parent.parent
    # Make the dev package importable even when `python -m rig_workbench.cli` is called from the scratch cwd.
    env["PYTHONPATH"] = str(candidate) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    # If RIG_HOME is unset, infer it from the package location
    if not env.get("RIG_HOME"):
        # Assume rig_workbench/'s parent is the repo root (only works for the dev version; pip installs need an explicit export)
        if (candidate / "scripts" / "orchestrate.py").exists():
            env["RIG_HOME"] = str(candidate)
    t0 = time.time()
    argv = _rig_wb_argv() + [
        "run", recipe, "--provider", provider,
        "--max-steps", str(max_steps), "--out", str(workdir / "run-state.json"),
        "--goal", goal,
    ]
    if allow_headless_in_cc:
        argv += ["--allow-headless-in-cc"]
    if model:
        argv += ["--model", model]
    r = subprocess.run(argv, cwd=workdir, capture_output=True, text=True, timeout=1800, env=env)
    return r.stdout + r.stderr, time.time() - t0, r.returncode


def _rig_read_state(workdir: pathlib.Path) -> dict:
    sp = workdir / "run-state.json"
    if not sp.exists():
        return {}
    return json.loads(sp.read_text(encoding="utf-8"))


# The mock provider's answer is defined in task["mock_fix"] (duplicate block removed)


# ── outcome classification ───────────────────────────────────────────


def classify_outcome(m: dict, mode: str) -> str:
    """Classify a single mode's result dict into one of four outcomes.

    "failed" means opposite things in the two modes, so a symmetric pass/fail
    label would hide the thing that actually matters here: whether a defect
    ships silently or is caught before it claims to be done.

      - clean_pass:    claimed done, hidden spec_check agrees. The good case.
      - silent_defect: claimed done, but spec_check disagrees — a bug ships
                        under the appearance of success. THE WORST OUTCOME:
                        strictly worse than safe_stop, because nothing about
                        the run signals that a human should look closer.
      - safe_stop:     (rig only) did NOT claim done (runner escalated /
                        stopped), yet the code was actually right per
                        spec_check. Over-conservative but honest — it costs
                        human attention it didn't strictly need, but it never
                        pretended to be finished.
      - stopped_wrong: (rig only) did NOT claim done, and the code was in
                        fact still wrong. Costs human attention same as
                        safe_stop, but at least it didn't ship the defect.

    bare mode always "completes" (there is no escalation/stop mechanism), so
    bare can only ever land on clean_pass or silent_defect.
    """
    spec = m.get("spec_check") == "PASS"
    completed = (m.get("runner_exit", 0) == 0) if mode == "rig" else True
    if completed and spec:
        return "clean_pass"
    if completed and not spec:
        return "silent_defect"
    if not completed and spec:
        return "safe_stop"
    return "stopped_wrong"


# ── bench execution for a single task ──────────────────────────────────


def _bench_task(task_id: str, task: dict, args: argparse.Namespace) -> dict:
    results = {"task_id": task_id, "difficulty": task["difficulty"], "runs": []}
    leak_root = pathlib.Path(args.leak_check_root).resolve() if args.leak_check_root else None
    for run_idx in range(args.runs):
        run_result: dict = {"run": run_idx + 1, "modes": {}}

        # ── BARE ──
        if args.mode in ("both", "bare"):
            d = _setup_task_dir(task)
            try:
                before = _git_status_lines(leak_root) if leak_root else set()
                prompt = _build_bare_prompt(task)
                resp, elapsed = _call_provider(args.provider, prompt, args.model,
                                                args.allow_headless_in_cc,
                                                cwd=d,
                                                mock_fix=task.get("mock_fix", ""))
                code = _extract_code(resp)
                (d / task["target_file"]).write_text(code, encoding="utf-8")
                t = _run_tests(task, d)
                sc = _spec_check(task, d)
                ud = _unrelated_diff(d, task["target_file"])
                leaks = _workspace_leaks(leak_root, before) if leak_root else []
                run_result["modes"]["bare"] = {
                    "elapsed_s": round(elapsed, 1),
                    "calls": 1,
                    "test_pass": t["failed"] == 0 and t["passed"] > 0 and t["exit"] == 0,
                    "test_stats": t,
                    "spec_check": sc,
                    "unrelated_files": ud,
                    "workspace_leaks": leaks,
                    "gate_verdict": None,
                }
                run_result["modes"]["bare"]["outcome"] = classify_outcome(run_result["modes"]["bare"], "bare")
            finally:
                shutil.rmtree(d, ignore_errors=True)

        # ── RIG ──
        if args.mode in ("both", "rig"):
            d = _setup_task_dir(task)
            try:
                before = _git_status_lines(leak_root) if leak_root else set()
                out, elapsed, runner_exit = _rig_run(task, d, args.provider, args.model,
                                                     args.allow_headless_in_cc, args.max_steps,
                                                     args.rig_recipe)
                state = _rig_read_state(d)
                # use the step count as a proxy for calls
                calls = sum(1 for s in state.get("step_state", {}).values()
                            if s.get("status") in ("passed", "running"))
                t = _run_tests(task, d)
                sc = _spec_check(task, d)
                ud = _unrelated_diff(d, task["target_file"])
                leaks = _workspace_leaks(leak_root, before) if leak_root else []
                # gate_verdict aggregates the review-diff / acceptance verdicts
                gate = None
                for sid in ("review-diff", "acceptance"):
                    st = state.get("step_state", {}).get(sid, {})
                    if st.get("status") == "passed":
                        gate = "passed"
                    elif st.get("status") == "failed":
                        gate = "failed"
                        break
                    elif st.get("verdicts"):
                        gate = "pending (has verdicts)"
                run_result["modes"]["rig"] = {
                    "elapsed_s": round(elapsed, 1),
                    "calls": calls,
                    "test_pass": t["failed"] == 0 and t["passed"] > 0 and t["exit"] == 0,
                    "test_stats": t,
                    "spec_check": sc,
                    "unrelated_files": ud,
                    "workspace_leaks": leaks,
                    "runner_exit": runner_exit,
                    "runner_output_tail": out[-1200:],
                    "gate_verdict": gate,
                    "reached_steps": [sid for sid, st in state.get("step_state", {}).items()
                                      if st.get("status") == "passed"],
                }
                run_result["modes"]["rig"]["outcome"] = classify_outcome(run_result["modes"]["rig"], "rig")
            finally:
                shutil.rmtree(d, ignore_errors=True)

        results["runs"].append(run_result)
    return results


# ── entry ──────────────────────────────────────────────────────────────


def cmd_bench(argv: list[str]) -> None:
    p = argparse.ArgumentParser(prog="rig-wb bench",
                                description="rig-wb bench — bare vs rig A/B benchmark (MVP)")
    p.add_argument("--tasks", nargs="+", choices=list(BUILTIN_TASKS.keys()) + ["all"],
                   default=["all"], help="tasks to run (default: all)")
    p.add_argument("--mode", choices=["bare", "rig", "both"], default="both")
    p.add_argument("--provider", default="mock",
                   help="claude / codex / ollama / lmstudio / mock (default: mock, no billing)")
    p.add_argument("--model", help="model name (for --provider claude|codex|ollama|lmstudio)")
    p.add_argument("--runs", type=int, default=1, help="repetitions per task (default: 1)")
    p.add_argument("--max-steps", type=int, default=14,
                   help="max-steps for rig mode (default: 14 — aims to reach every bugfix step)")
    p.add_argument("--rig-recipe", default="bugfix",
                   help="recipe used in rig mode (default: bugfix; use fast-bugfix for a lightweight comparison)")
    p.add_argument("--html", help="output path for the HTML dashboard (visualizes bench results)")
    p.add_argument("--leak-check-root", default=os.getcwd(),
                   help="root where writes leaking outside the scratch dir are detected via git status diff (default: cwd)")
    p.add_argument("--allow-headless-in-cc", action="store_true",
                   help="opt-in for using the claude/rig provider from inside Claude Code")
    p.add_argument("--out", help="JSON output file (stdout if omitted)")
    args = p.parse_args(argv)

    task_ids = list(BUILTIN_TASKS.keys()) if args.tasks == ["all"] or "all" in args.tasks else args.tasks

    summary: dict = {
        "generated": datetime.datetime.now().isoformat(timespec="seconds"),
        "rig_wb_version": __version__,
        "provider": args.provider,
        "model": args.model,
        "runs_per_task": args.runs,
        "rig_recipe": args.rig_recipe,
        "tasks": [],
    }
    for tid in task_ids:
        print(f"\n=== task: {tid} ({BUILTIN_TASKS[tid]['difficulty']}) ===", flush=True)
        r = _bench_task(tid, BUILTIN_TASKS[tid], args)
        summary["tasks"].append(r)
        # print just the highlights immediately
        for run in r["runs"]:
            for mode, m in run["modes"].items():
                print(f"  run={run['run']} mode={mode:5s}  "
                      f"elapsed={m['elapsed_s']}s  calls={m['calls']}  "
                      f"test_pass={m['test_pass']}  spec={m['spec_check']}  "
                      f"unrelated={len(m['unrelated_files'])}  "
                      f"leaks={len(m.get('workspace_leaks', []))}  "
                      f"gate={m.get('gate_verdict', '-')} "
                      f"outcome={m.get('outcome', '-')}", flush=True)

    out_text = json.dumps(summary, ensure_ascii=False, indent=2)
    if args.out:
        pathlib.Path(args.out).write_text(out_text, encoding="utf-8")
        print(f"\nWrote: {args.out}")
    else:
        print("\n" + out_text)

    if args.html:
        html = _render_html(summary)
        pathlib.Path(args.html).write_text(html, encoding="utf-8")
        print(f"HTML: {args.html}")


# ── HTML dashboard ─────────────────────────────────────────────────────


def _render_html(summary: dict) -> str:
    """Visualize bench results as a single HTML page. stdlib only, no external deps."""
    import html as _html

    def esc(s) -> str:
        return _html.escape(str(s), quote=True)

    tasks = summary.get("tasks", [])

    # Aggregate: bare vs rig average elapsed / calls / test_pass rate / spec_pass rate
    def aggregate(mode: str) -> dict:
        elapsed, calls, tests, specs, n = [], [], 0, 0, 0
        outcomes = {"clean_pass": 0, "silent_defect": 0, "safe_stop": 0, "stopped_wrong": 0}
        for t in tasks:
            for run in t["runs"]:
                m = run["modes"].get(mode)
                if not m:
                    continue
                elapsed.append(m["elapsed_s"])
                calls.append(m["calls"])
                if m["test_pass"]:
                    tests += 1
                if m["spec_check"] == "PASS":
                    specs += 1
                outcome = m.get("outcome") or classify_outcome(m, mode)
                outcomes[outcome] = outcomes.get(outcome, 0) + 1
                n += 1
        if n == 0:
            return {"n": 0}
        return {
            "n": n,
            "elapsed_avg": round(sum(elapsed) / n, 1),
            "calls_avg": round(sum(calls) / n, 1),
            "test_pass_rate": round(tests / n * 100, 0),
            "spec_pass_rate": round(specs / n * 100, 0),
            "outcomes": outcomes,
        }

    a_bare = aggregate("bare")
    a_rig = aggregate("rig")

    def kpi(label: str, bare_v, rig_v, unit: str = "") -> str:
        return (f'<div class="kpi"><div class="label">{esc(label)}</div>'
                f'<div class="row"><div class="v bare">{esc(bare_v)}{esc(unit)}</div>'
                f'<div class="v rig">{esc(rig_v)}{esc(unit)}</div></div>'
                f'<div class="sub">bare / rig</div></div>')

    kpi_bare_avg_e = f"{a_bare.get('elapsed_avg', '-')}"
    kpi_rig_avg_e = f"{a_rig.get('elapsed_avg', '-')}"
    kpi_bare_avg_c = f"{a_bare.get('calls_avg', '-')}"
    kpi_rig_avg_c = f"{a_rig.get('calls_avg', '-')}"
    kpi_bare_tp = f"{a_bare.get('test_pass_rate', '-')}"
    kpi_rig_tp = f"{a_rig.get('test_pass_rate', '-')}"
    kpi_bare_sp = f"{a_bare.get('spec_pass_rate', '-')}"
    kpi_rig_sp = f"{a_rig.get('spec_pass_rate', '-')}"
    kpi_bare_silent = f"{a_bare.get('outcomes', {}).get('silent_defect', '-')}"
    kpi_rig_silent = f"{a_rig.get('outcomes', {}).get('silent_defect', '-')}"
    kpi_bare_safe_stop = "-"  # bare mode never escalates, so it can never land on safe_stop
    kpi_rig_safe_stop = f"{a_rig.get('outcomes', {}).get('safe_stop', '-')}"

    # per-task table
    rows = []
    for t in tasks:
        for run in t["runs"]:
            bare = run["modes"].get("bare", {})
            rig = run["modes"].get("rig", {})
            def cell(m: dict, key: str, default="-") -> str:
                v = m.get(key, default) if m else default
                if isinstance(v, list):
                    v = ", ".join(map(str, v)) or "-"
                return esc(v)
            def testcell(m: dict) -> str:
                if not m:
                    return "-"
                cls = "ok" if m.get("test_pass") else "bad"
                return f'<span class="pill {cls}">{"pass" if m.get("test_pass") else "fail"}</span>'
            def speccell(m: dict) -> str:
                if not m:
                    return "-"
                v = m.get("spec_check", "?")
                cls = "ok" if v == "PASS" else "bad"
                short = v.split(":")[0] if isinstance(v, str) else str(v)
                return f'<span class="pill {cls}" title="{esc(v)}">{esc(short)}</span>'
            def outcomecell(m: dict, mode: str) -> str:
                if not m:
                    return "-"
                outcome = m.get("outcome") or classify_outcome(m, mode)
                cls = {
                    "clean_pass": "ok",
                    "silent_defect": "bad",
                    "safe_stop": "warn",
                    "stopped_wrong": "warn",
                }.get(outcome, "warn")
                return f'<span class="pill {cls}">{esc(outcome)}</span>'
            rows.append(
                f'<tr>'
                f'<td>{esc(t["task_id"])}<div class="sub">{esc(t["difficulty"])}</div></td>'
                f'<td>{cell(bare, "elapsed_s")}s</td><td>{cell(rig, "elapsed_s")}s</td>'
                f'<td>{cell(bare, "calls")}</td><td>{cell(rig, "calls")}</td>'
                f'<td>{testcell(bare)}</td><td>{testcell(rig)}</td>'
                f'<td>{speccell(bare)}</td><td>{speccell(rig)}</td>'
                f'<td>{outcomecell(bare, "bare")}</td><td>{outcomecell(rig, "rig")}</td>'
                f'<td>{cell(bare, "unrelated_files")}</td><td>{cell(rig, "unrelated_files")}</td>'
                f'<td>{cell(bare, "workspace_leaks")}</td><td>{cell(rig, "workspace_leaks")}</td>'
                f'<td>{cell(rig, "reached_steps")}</td>'
                f'</tr>'
            )
    table = (
        '<table><thead><tr>'
        '<th>task</th>'
        '<th>bare elapsed</th><th>rig elapsed</th>'
        '<th>bare calls</th><th>rig calls</th>'
        '<th>bare test</th><th>rig test</th>'
        '<th>bare spec</th><th>rig spec</th>'
        '<th>bare outcome</th><th>rig outcome</th>'
        '<th>bare unrel</th><th>rig unrel</th>'
        '<th>bare leaks</th><th>rig leaks</th>'
        '<th>rig reached_steps</th>'
        f'</tr></thead><tbody>{"".join(rows)}</tbody></table>'
    )

    css = (
        ":root{--bg:#f8fafc;--card:#fff;--ink:#0f172a;--dim:#64748b;"
        "--accent:#0d9488;--bad:#dc2626;--border:#e2e8f0;"
        "--bare:#3b82f6;--rig:#0d9488;}"
        "@media (prefers-color-scheme:dark){:root{--bg:#0b1220;--card:#111827;"
        "--ink:#f1f5f9;--dim:#94a3b8;--border:#1f2937;}}"
        "*{box-sizing:border-box}"
        "body{font-family:'Zen Kaku Gothic New',-apple-system,'Segoe UI',Roboto,sans-serif;"
        "background:var(--bg);color:var(--ink);margin:0;padding:2rem;line-height:1.6}"
        "h1{margin:0 0 .25rem;font-size:1.75rem}"
        ".sub{color:var(--dim);font-size:.75rem}"
        ".kpi-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));"
        "gap:.75rem;margin:1rem 0 2rem}"
        ".kpi{background:var(--card);border:1px solid var(--border);"
        "border-radius:12px;padding:1rem 1.25rem}"
        ".kpi .label{color:var(--dim);font-size:.75rem;text-transform:uppercase;"
        "letter-spacing:.05em}"
        ".kpi .row{display:flex;gap:.5rem;margin-top:.25rem}"
        ".kpi .v{font-size:1.5rem;font-weight:700;font-variant-numeric:tabular-nums;"
        "padding:.1rem .5rem;border-radius:6px}"
        ".kpi .v.bare{background:var(--bare);color:#fff}"
        ".kpi .v.rig{background:var(--rig);color:#fff}"
        "table{width:100%;border-collapse:collapse;font-size:.85rem;"
        "background:var(--card);border:1px solid var(--border);border-radius:12px;"
        "overflow:hidden}"
        "th,td{padding:.5rem .75rem;border-bottom:1px solid var(--border);"
        "text-align:left;font-variant-numeric:tabular-nums}"
        "th{color:var(--dim);font-weight:500;font-size:.75rem;text-transform:uppercase}"
        ".pill{display:inline-block;padding:1px 8px;border-radius:999px;"
        "font-size:.72rem;font-weight:600}"
        ".pill.ok{background:#059669;color:#fff}"
        ".pill.bad{background:var(--bad);color:#fff}"
        ".pill.warn{background:#d97706;color:#fff}"
        "footer{margin-top:2rem;color:var(--dim);font-size:.75rem;text-align:center}"
    )

    meta = (
        f"<p class='sub'>generated={esc(summary.get('generated'))} · "
        f"rig-wb {esc(summary.get('rig_wb_version'))} · "
        f"provider={esc(summary.get('provider'))} "
        f"{('model=' + esc(summary.get('model'))) if summary.get('model') else ''} · "
        f"runs_per_task={esc(summary.get('runs_per_task'))} · "
        f"tasks={len(tasks)}</p>"
    )

    return (
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<title>rig-wb bench dashboard</title>"
        f"<style>{css}</style></head><body>"
        "<h1>rig-wb bench dashboard</h1>"
        f"{meta}"
        "<div class='kpi-grid'>"
        f"{kpi('avg elapsed (s)', kpi_bare_avg_e, kpi_rig_avg_e)}"
        f"{kpi('avg calls', kpi_bare_avg_c, kpi_rig_avg_c)}"
        f"{kpi('test pass rate (%)', kpi_bare_tp, kpi_rig_tp)}"
        f"{kpi('spec pass rate (%)', kpi_bare_sp, kpi_rig_sp)}"
        f"{kpi('silent defects', kpi_bare_silent, kpi_rig_silent)}"
        f"{kpi('safe stops', kpi_bare_safe_stop, kpi_rig_safe_stop)}"
        "</div>"
        f"{table}"
        "<footer>rig-wb bench · <code>rig-wb bench --html &lt;path&gt;</code></footer>"
        "</body></html>"
    )
