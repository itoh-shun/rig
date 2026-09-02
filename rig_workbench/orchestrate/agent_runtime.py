"""Agent runtimes: what each CLI can do, and how rig asks it to do it (#416 Phase 1).

Rig decides *what* has to happen — which recipe, which steps, who verifies whom, what passes
the gate. A runtime decides *how* a single step is spoken to a particular vendor's CLI. The
two were interleaved: one `if provider == ...` branch per vendor inside `build_argv`, each
carrying that vendor's flag spellings, its sandbox story and its session-flag shape, with
Core reading those branches to answer questions that were never about argv.

Each vendor is an adapter here, and each adapter *declares* what it can do rather than
leaving Core to infer it from the strings it appends:

- **backend identity** — `rig` is a prompt mode executed through the claude binary, so it
  declares the same backend as `claude`. Separation-of-duty compares backends, not labels,
  because accepting one as an independent review of the other would be alias laundering.
- **verifier confinement** — "read-only verifier" is not one guarantee. claude enforces it
  with an in-process tool allowlist, codex with an OS sandbox, and grok headless documents
  no read-only flag at all, so its verifier rests on the prompt contract alone (#328).
  Declaring the mechanism keeps that gap legible instead of leaving it to a comment beside
  a flag list.
- **session reuse** — a per-CLI capability (#326). The flags an adapter declares are the
  ones `sessions.py` probes against the installed binary before use; codex declines by
  declaring none, and the fallback is recorded in the run history rather than swallowed.

What this module deliberately does *not* do yet: run anything. `AgentRuntime.run`,
streaming and abort are Phase 3 — the contract is shaped for them, but claiming them here
would be a promise with no implementation behind it. Today every adapter answers one
question, `build_argv`, and `providers.run_provider` still owns process execution.
"""

from __future__ import annotations

import shlex
import sys
from dataclasses import dataclass

from . import sessions

#: The one landmark a prompt uses to introduce its numbered acceptance criteria. Two
#: composers spell their own heading — `_build_verify_prompt` and `_adaptive_review_prompt`
#: — and a reader that hard-codes either one sees the other's list as no list at all. That
#: is what happened: the mock provider counted only the first spelling, so an
#: `adaptive-bugfix` run answered none of `targeted-review`'s declared criteria and the
#: gate escalated. Declared once here; both composers build their heading from it and
#: `test_every_criteria_heading_is_built_from_the_shared_landmark` refuses a third spelling.
CRITERIA_HEADING = "Acceptance criteria"

MOCK_SRC = (
    "import sys\n"
    "import os\n"
    "import re\n"
    "import shutil\n"
    "from pathlib import Path\n"
    "prompt = sys.stdin.read()\n"
    "role = sys.argv[1] if len(sys.argv) > 1 else 'generator'\n"
    "persona = sys.argv[2] if len(sys.argv) > 2 else ''\n"
    "step = re.search(r'step: ([^\\s]+)', prompt)\n"
    "step_id = step.group(1) if step else ''\n"
    "target = re.search(r'Target file: ([^\\s]+)', prompt)\n"
    "target_file = target.group(1) if target else ''\n"
    "def write(path, text):\n"
    "    if path:\n"
    "        Path(path).write_text(text, encoding='utf-8')\n"
    "def fix_for(text):\n"
    "    if 'divide-by-zero' in text or 'ZeroDivisionError' in text or 'divide_all' in text:\n"
    "        return (\n"
    "            'def divide_all(numbers, divisor):\\n'\n"
    "            '    if divisor == 0:\\n'\n"
    "            '        return list(numbers)\\n'\n"
    "            '    return [n / divisor for n in numbers]\\n'\n"
    "        )\n"
    "    if 'order-dedup' in text or 'dedup(' in text or 'order-preserving' in text:\n"
    "        return 'def dedup(items):\\n    return list(dict.fromkeys(items))\\n'\n"
    "    if 'sql-inject' in text or 'SQL injection' in text or 'get_user_by_name' in text:\n"
    "        return (\n"
    "            'import sqlite3\\n\\n'\n"
    "            'def get_user_by_name(conn: sqlite3.Connection, name: str) -> tuple | None:\\n'\n"
    "            '    cur = conn.cursor()\\n'\n"
    "            '    cur.execute(\"SELECT id, name, role FROM users WHERE name = ?\", (name,))\\n'\n"
    "            '    return cur.fetchone()\\n'\n"
    "        )\n"
    "    if 'dry-refactor' in text or 'missing round-up' in text or 'price_domestic_cool' in text:\n"
    "        return (\n"
    "            'import math\\n\\n'\n"
    "            'def _price(weight_kg: float, unit_price: int, floor: int) -> int:\\n'\n"
    "            '    units = math.ceil(weight_kg / 0.5)\\n'\n"
    "            '    return max(floor, units * unit_price)\\n\\n'\n"
    "            'def price_domestic(weight_kg: float) -> int:\\n'\n"
    "            '    return _price(weight_kg, 200, 500)\\n\\n'\n"
    "            'def price_domestic_cool(weight_kg: float) -> int:\\n'\n"
    "            '    return _price(weight_kg, 300, 800)\\n'\n"
    "        )\n"
    "    if 'auth-bypass-sibling' in text or 'ProfileService' in text or 'get_profile' in text:\n"
    "        return (\n"
    "            'class ProfileService:\\n'\n"
    "            '    def __init__(self):\\n'\n"
    "            '        self._profiles = {}\\n\\n'\n"
    "            '    def create_profile(self, user_id, data):\\n'\n"
    "            '        self._profiles[user_id] = dict(data)\\n\\n'\n"
    "            '    def get_profile(self, current_user_id, requested_user_id):\\n'\n"
    "            '        if current_user_id != requested_user_id:\\n'\n"
    "            '            return None\\n'\n"
    "            '        return self._profiles.get(requested_user_id)\\n\\n'\n"
    "            '    def update_profile(self, current_user_id, requested_user_id, data):\\n'\n"
    "            '        if current_user_id != requested_user_id:\\n'\n"
    "            '            return False\\n'\n"
    "            '        if requested_user_id not in self._profiles:\\n'\n"
    "            '            return False\\n'\n"
    "            '        self._profiles[requested_user_id].update(data)\\n'\n"
    "            '        return True\\n'\n"
    "        )\n"
    "    return ''\n"
    "def apply_benchmark_canonical():\n"
    "    canonical = os.environ.get('RIG_BENCH_MOCK_CANONICAL')\n"
    "    if not canonical:\n"
    "        return False\n"
    "    root = Path(canonical)\n"
    "    for source in root.rglob('*'):\n"
    "        if source.is_file():\n"
    "            destination = Path.cwd() / source.relative_to(root)\n"
    "            destination.parent.mkdir(parents=True, exist_ok=True)\n"
    "            shutil.copy2(source, destination)\n"
    "    return True\n"
    "def acceptance_count(text):\n"
    # Anchored on the shared landmark and on the shape of the list itself: the items are
    # the run of `  <n>. ` lines directly under the heading. Splitting on a terminator
    # phrase read only one composer's prompt; the other's list ended at a different
    # sentence and counted as zero.
    "    lines = text.splitlines()\n"
    "    heading = None\n"
    "    for index, line in enumerate(lines):\n"
    "        if line.startswith(" + repr(CRITERIA_HEADING) + ") and line.endswith(':'):\n"
    "            heading = index\n"
    "            break\n"
    "    if heading is None:\n"
    "        return 0\n"
    "    numbers = []\n"
    "    for line in lines[heading + 1:]:\n"
    "        match = re.match(r'^  (\\d+)\\. ', line)\n"
    "        if match is None:\n"
    "            break\n"
    "        numbers.append(int(match.group(1)))\n"
    "    if not numbers or numbers != list(range(1, len(numbers) + 1)):\n"
    "        return None\n"
    "    return len(numbers)\n"
    "if role == 'verifier':\n"
    "    count = acceptance_count(prompt)\n"
    "    failed = 'fail' in persona or count is None\n"
    "    print('independent verification (mock): ' + persona)\n"
    "    if count is None:\n"
    "        print('evidence: malformed acceptance criteria list - mock.py:1')\n"
    "    else:\n"
    "        print('evidence: mock inspection of the product - mock.py:1')\n"
    "        for number in range(1, count + 1):\n"
    "            print('CRITERION ' + str(number) + ': ' + ('FAIL' if failed else 'PASS') + ' - mock.py:1')\n"
    "    print('VERDICT: ' + ('FAIL' if failed else 'PASS'))\n"
    "else:\n"
    "    if step_id == 'implement' and not apply_benchmark_canonical() and target_file:\n"
    "        fix = fix_for(prompt)\n"
    "        if fix:\n"
    "            write(target_file, fix)\n"
    "    print('## step result (mock)')\n"
    "    print('STATUS: done')\n"
)

RIG_GEN_PREFIX = ("Invoke the `rig` skill via the Skill tool and execute the following step per its "
                  "engine (PARSE→RESOLVE→COMPOSE→RUN, context-minimal).\n")
RIG_VER_PREFIX = ("Invoke the `rig` skill via the Skill tool and, as an independent verifier (a separate "
                  "process from the agent that generated this step), judge the acceptance criteria; "
                  "end with exactly 'VERDICT: PASS' or 'VERDICT: FAIL'.\n")

# Enforce grader != generator one level beyond "separate process": verifier-role CLIs get
# **read-only permission flags pinned via argv** (a mechanism, not a polite prompt request).
# The two flag sets are not equally strong, and the eval harness names that difference
# (`ISOLATION_RANK` in `rig_workbench/eval/cases.py`): codex's `--sandbox read-only` is
# `os-enforced` — the operating system refuses the write, whatever the agent decides —
# while a Claude tool allowlist is `agent-policy`: the agent refuses the write in-process,
# and nothing outside that process stops one that gets past it. Both deny writes; only one
# is enforced by something other than the program under review. (These are the mechanism
# classes. The levels in `eval/cases.py` were measured against the eval adapter's argv,
# which is stricter than this one — it also passes `--disallowedTools` and `--safe-mode` —
# so nothing here claims this particular flag set was verified the same way.)
_READONLY_ENFCE = {
    "claude": ["--allowedTools", "Read,Grep,Glob"],   # agent-policy: in-process allowlist
    "codex":  ["--sandbox", "read-only"],              # os-enforced: codex exec sandbox
}

# The generator's counterpart problem (#331, discovered by a live #330 bench run):
# headless `claude -p` has no one to approve Edit/Write tool calls, so without an
# explicit permission mode the generator asks for approval it can never receive and
# silently writes nothing — confirmed live in this environment (`claude -p "edit
# x.py..." ` left the file untouched; the identical call with `--permission-mode
# acceptEdits` applied the edit). `acceptEdits` is the minimum-privilege fix: file
# edits are allowed, nothing else is blanket-bypassed (not `--dangerously-skip-
# permissions`). `codex`'s generator branch already gets `--sandbox workspace-write`
# from codex's own mechanism, so it isn't affected by this.
_GENERATOR_EDIT_ENFCE = {
    "claude": ["--permission-mode", "acceptEdits"],
}


# ── The contract ─────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class RuntimeCapabilities:
    """What Core is allowed to assume about a runtime, stated by the runtime.

    `verifier_confinement` and `generator_write` name the *mechanism*, not a strength score,
    because the mechanisms are not comparable on one axis: an OS sandbox and an in-process
    allowlist both deny a write, but only one is enforced by something other than the
    program under review. Values in use:

    - `tool-allowlist` — the agent refuses the call in-process (claude)
    - `os-sandbox`     — the operating system refuses the write (codex)
    - `prompt-only`    — nothing mechanical; the prompt contract alone (grok, #328)
    - `caller-defined` — the operator supplied the whole command line (`cmd`)
    - `none`           — no confinement claimed (the `mock` stub)

    `session_flags` is the pair `sessions.SESSION_FLAGS` probes for; declaring it here and
    reading it there keeps one table, so an adapter cannot claim a capability the probe
    would never check.
    """

    label: str
    backend: str
    model_flag: str | None
    verifier_confinement: str
    generator_write: str
    session_flags: tuple[str, str] | None = None
    session_binary: str | None = None

    @property
    def supports_session_reuse(self) -> bool:
        """Whether this CLI documents session flags at all — not whether the binary installed
        here has them. That second question is `sessions.supports`, and it is asked of the
        binary rather than of this table."""
        return self.session_flags is not None


@dataclass(frozen=True)
class AgentTask:
    """One step handed to a runtime. `role` is the whole of rig's authority here:
    `generator` writes, `verifier` judges and must not."""

    role: str
    prompt: str
    persona: str = ""


@dataclass
class AgentContext:
    """The run's mutable state as a runtime is allowed to see it.

    `cfg` carries the session container, so it is shared rather than copied — a container
    copied per step would give every step a fresh session while reporting reuse (#326).
    `state` is where a session fallback is recorded, and may be absent for callers that
    build argv outside a run.
    """

    cfg: dict
    state: dict | None = None


class AgentRuntime:
    """A way to execute one step.

    Today this is argv construction only. `run`/`stream`/`abort` belong to the native
    runtime (#416 Phase 3); they are not declared here because an interface method nothing
    implements reads as a capability rig does not have.
    """

    capabilities: RuntimeCapabilities

    def build_argv(self, task: AgentTask, context: AgentContext) -> list[str]:
        raise NotImplementedError

    def _session(self, task: AgentTask, context: AgentContext) -> list[str]:
        """The flags that continue this run's generator conversation, or `[]`.

        Asked of `sessions` for every adapter, including the ones that declare no flags:
        the answer there is always `[]`, but a caller who asked for reuse has to learn that
        from the run history rather than from measuring no improvement and guessing why.
        """
        return sessions.session_argv(self.capabilities.label, task.role, context.cfg,
                                     context.state)


# ── Adapters ─────────────────────────────────────────────────────────────────

class ClaudeCliRuntime(AgentRuntime):
    """Headless `claude -p`. Serves both the `claude` provider and `rig`, which is the same
    binary carrying a prompt prefix that invokes the rig skill by name.

    In production a user can tune permission modes etc. via `--provider-cmd`.
    """

    def __init__(self, label: str, *, gen_prefix: str = "", ver_prefix: str = ""):
        self._gen_prefix = gen_prefix
        self._ver_prefix = ver_prefix
        self.capabilities = RuntimeCapabilities(
            label=label,
            # `rig` and `claude` execute through one binary, so they are one backend:
            # treating the labels as independent would be alias laundering rather than
            # an independent review.
            backend="claude-cli",
            model_flag="--model",
            verifier_confinement="tool-allowlist",
            generator_write="permission-flag",
            session_flags=("--session-id", "--resume"),
            session_binary="claude",
        )

    def build_argv(self, task: AgentTask, context: AgentContext) -> list[str]:
        cfg = context.cfg
        prefix = self._ver_prefix if task.role == "verifier" else self._gen_prefix
        argv = ["claude", "-p", prefix + task.prompt, "--output-format", "text"]
        if cfg.get("model"):
            argv += [self.capabilities.model_flag, cfg["model"]]   # per-step model support
        if cfg.get("claude_no_session_persistence"):
            argv.append("--no-session-persistence")
        argv += self._session(task, context)
        return argv + (_READONLY_ENFCE["claude"] if task.role == "verifier"
                       else _GENERATOR_EDIT_ENFCE["claude"])


class CodexRuntime(AgentRuntime):
    """`codex exec`, confined by codex's own sandbox rather than by a tool allowlist."""

    capabilities = RuntimeCapabilities(
        label="codex",
        backend="codex",
        model_flag="-m",
        verifier_confinement="os-sandbox",
        generator_write="os-sandbox",
        session_flags=None,        # no documented equivalent; reuse falls back and says so
        session_binary=None,
    )

    def build_argv(self, task: AgentTask, context: AgentContext) -> list[str]:
        cfg = context.cfg
        # --skip-git-repo-check: keep codex from refusing to start in non-git directories
        # (e.g. overlay targets in cross-project use). The sandbox stays enabled, so this is safe.
        argv = ["codex", "exec", "--skip-git-repo-check"]
        argv += ["--sandbox", "workspace-write" if task.role == "generator" else "read-only"]
        if cfg.get("model"):
            argv += [self.capabilities.model_flag, cfg["model"]]   # per-step model support
        argv += self._session(task, context)                        # always [] — records the fallback
        return argv + [task.prompt]


class GrokRuntime(AgentRuntime):
    """grok-build headless (`grok -p`, claude-CLI-shaped syntax;
    docs.x.ai/build/cli/headless-scripting).

    Honest gap (#328): no read-only/sandbox flag is documented for grok headless, so the
    verifier role's read-only stance rests on the prompt contract alone — one enforcement
    layer thinner than claude (`--allowedTools`) or codex (`--sandbox read-only`), which is
    why `verifier_confinement` reads `prompt-only`. Deliberately NOT passing
    `--always-approve` (it auto-approves tool executions; a verifier must never get it, and
    a generator that needs it can opt in via `--provider-cmd "grok -p {prompt}
    --always-approve"`).
    """

    capabilities = RuntimeCapabilities(
        label="grok",
        backend="grok",
        model_flag="-m",
        verifier_confinement="prompt-only",
        generator_write="prompt-only",
        session_flags=("--session-id", "--resume"),
        session_binary="grok",
    )

    def build_argv(self, task: AgentTask, context: AgentContext) -> list[str]:
        cfg = context.cfg
        argv = ["grok", "-p", task.prompt, "--output-format", "plain"]
        if cfg.get("model"):
            argv += [self.capabilities.model_flag, cfg["model"]]   # per-step model support
        return argv + self._session(task, context)


class CmdRuntime(AgentRuntime):
    """An operator-supplied command line. Rig makes no claim about what it enforces."""

    capabilities = RuntimeCapabilities(
        label="cmd",
        backend="cmd",
        model_flag=None,
        verifier_confinement="caller-defined",
        generator_write="caller-defined",
    )

    def build_argv(self, task: AgentTask, context: AgentContext) -> list[str]:
        tmpl = context.cfg.get("provider_cmd") or ""
        if not tmpl:
            raise SystemExit("[ERROR] --provider cmd requires --provider-cmd \"... {prompt} ...\"")
        # Reuse cannot be added to somebody's own command template — rig does not know where a
        # session flag would go in it, and appending one could change what the template means.
        # Recorded as a fallback rather than attempted.
        self._session(task, context)
        # shlex respects quoting and whitespace (wrappers for real codex etc. pass through safely)
        return [a.replace("{prompt}", task.prompt)
                 .replace("{role}", task.role)
                 .replace("{persona}", task.persona)
                for a in shlex.split(tmpl)]


class MockRuntime(AgentRuntime):
    """The test double: a separate interpreter running `MOCK_SRC`. It confines nothing
    because it can do nothing — it prints a fixed verdict."""

    capabilities = RuntimeCapabilities(
        label="mock",
        backend="mock",
        model_flag=None,
        verifier_confinement="none",
        generator_write="none",
    )

    def build_argv(self, task: AgentTask, context: AgentContext) -> list[str]:
        return [sys.executable, "-c", MOCK_SRC, task.role, task.persona]


REGISTRY: dict[str, AgentRuntime] = {
    "mock": MockRuntime(),
    "rig": ClaudeCliRuntime("rig", gen_prefix=RIG_GEN_PREFIX, ver_prefix=RIG_VER_PREFIX),
    "claude": ClaudeCliRuntime("claude"),
    "codex": CodexRuntime(),
    "grok": GrokRuntime(),
    "cmd": CmdRuntime(),
}


def runtime_for(provider: str) -> AgentRuntime:
    try:
        return REGISTRY[provider]
    except KeyError:
        raise SystemExit(f"[ERROR] unknown provider: {provider}") from None


def backend_for(provider: str) -> str:
    """The execution backend a provider label resolves to, for separation-of-duty.

    Not every provider is a CLI adapter: HTTP model providers (`ollama`, `lmstudio`,
    `anthropic`) reach a model directly, and an operator can name a backend rig has never
    heard of. Those are their own backend — distinct from every other label, which is what
    an independence check needs. Only the CLI adapters collapse, and only where they
    genuinely share a binary (`rig` and `claude`).

    So this answers a question `runtime_for` must not: an unknown label is a legitimate
    answer here and a fatal error there, because you cannot build argv for a CLI that does
    not exist.
    """
    adapter = REGISTRY.get(provider)
    return adapter.capabilities.backend if adapter is not None else provider


def build_argv(provider: str, role: str, prompt: str, cfg: dict, persona: str = "",
               state: dict | None = None) -> list[str]:
    """Argv for one step, as this provider's runtime spells it."""
    return runtime_for(provider).build_argv(
        AgentTask(role=role, prompt=prompt, persona=persona),
        AgentContext(cfg=cfg, state=state))
