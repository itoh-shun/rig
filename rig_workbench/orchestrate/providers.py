"""orchestrate providers: execution layer / provider abstraction / local LLM HTTP (split from scripts/orchestrate.py)."""

import sys
import os
import re
import json
import time
import shlex
import threading
import pathlib
import subprocess
import concurrent.futures as futures

from . import config
from .quarantine import wrap_untrusted
from .recipes import (git_diff_lines, learned_auto_route, load_manifest,
                      resolve_auto_route, size_class)
from .runstate import compute_next, gate_outcome, save_state, telemetry_append

# ── Execution layer (external runners, provider abstraction) ─────────────────
# Run each step as an "agent in a separate process" = context isolated at the process boundary.
# Verification runs on a "different provider / different process" = grader != generator by construction.
# No default provider (must be explicit). Real claude/codex are wiring only; tests use mock.

MOCK_SRC = (
    "import sys\n"
    "import os\n"
    "import re\n"
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
    "    return ''\n"
    "if role == 'verifier':\n"
    "    print('independent verification (mock): ' + persona)\n"
    "    print('evidence: mock inspection of the product - mock.py:1')\n"
    "    print('CRITERION 1: ' + ('FAIL' if 'fail' in persona else 'PASS') + ' - mock.py:1')\n"
    "    print('VERDICT: ' + ('FAIL' if 'fail' in persona else 'PASS'))\n"
    "else:\n"
    "    if step_id == 'implement' and target_file:\n"
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
# The verifier cannot write, so tampering with the product under review or sneaking in
# self-fixes is structurally impossible.
_READONLY_ENFCE = {
    "claude": ["--allowedTools", "Read,Grep,Glob"],   # headless tool allowlist
    "codex":  ["--sandbox", "read-only"],              # codex exec sandbox
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


def build_argv(provider: str, role: str, prompt: str, cfg: dict, persona: str = "") -> list[str]:
    if provider == "mock":
        return ["python3", "-c", MOCK_SRC, role, persona]
    if provider == "rig":
        # Launch each step as a "rig harness" via headless claude (invokes rig by name).
        pre = RIG_VER_PREFIX if role == "verifier" else RIG_GEN_PREFIX
        argv = ["claude", "-p", pre + prompt, "--output-format", "text"]
        if cfg.get("model"):
            argv += ["--model", cfg["model"]]              # per-step model support
        return argv + (_READONLY_ENFCE["claude"] if role == "verifier" else _GENERATOR_EDIT_ENFCE["claude"])
    if provider == "claude":
        # Headless. In production the user can tune permission modes etc. via --provider-cmd.
        argv = ["claude", "-p", prompt, "--output-format", "text"]
        if cfg.get("model"):
            argv += ["--model", cfg["model"]]              # per-step model support
        return argv + (_READONLY_ENFCE["claude"] if role == "verifier" else _GENERATOR_EDIT_ENFCE["claude"])
    if provider == "codex":
        # --skip-git-repo-check: keep codex from refusing to start in non-git directories
        # (e.g. overlay targets in cross-project use). The sandbox stays enabled, so this is safe.
        argv = ["codex", "exec", "--skip-git-repo-check"]
        argv += ["--sandbox", "workspace-write" if role == "generator" else "read-only"]
        if cfg.get("model"):
            argv += ["-m", cfg["model"]]                   # per-step model support
        return argv + [prompt]
    if provider == "grok":
        # grok-build headless (`grok -p`, claude-CLI-shaped syntax;
        # docs.x.ai/build/cli/headless-scripting). Honest gap (#328): no
        # read-only/sandbox flag is documented for grok headless, so the
        # verifier role's read-only stance rests on the prompt contract alone —
        # one enforcement layer thinner than claude (--allowedTools) or codex
        # (--sandbox read-only). Deliberately NOT passing --always-approve
        # (it auto-approves tool executions; a verifier must never get it, and
        # a generator that needs it can opt in via
        # --provider-cmd "grok -p {prompt} --always-approve").
        argv = ["grok", "-p", prompt, "--output-format", "plain"]
        if cfg.get("model"):
            argv += ["-m", cfg["model"]]                   # per-step model support
        return argv
    if provider == "cmd":
        tmpl = cfg.get("provider_cmd") or ""
        if not tmpl:
            raise SystemExit("[ERROR] --provider cmd requires --provider-cmd \"... {prompt} ...\"")
        # shlex respects quoting and whitespace (wrappers for real codex etc. pass through safely)
        return [a.replace("{prompt}", prompt).replace("{role}", role).replace("{persona}", persona)
                for a in shlex.split(tmpl)]
    raise SystemExit(f"[ERROR] unknown provider: {provider}")


# ── Local LLMs (OpenAI-compatible HTTP) ──────────────────────────────────────
# ollama / lmstudio hit the local server's OpenAI-compatible endpoint (the /v1 root).
# Each request is independent (stateless), so context isolation is preserved.
# Requires: a running server plus a model.
_OPENAI_BASE = {
    "lmstudio": "http://localhost:1234/v1",    # LM Studio (start its Local Server)
    "ollama":   "http://localhost:11434/v1",    # ollama serve (OpenAI-compatible)
}
_DEFAULT_MODEL = {"lmstudio": "local-model", "ollama": "llama3.1"}
_MODELS_CACHE_PATH = pathlib.Path(os.path.expanduser("~/.claude/rig/models.json"))


def _base_url(provider: str, cfg: dict) -> str:
    return (cfg.get("base_url") or _OPENAI_BASE[provider]).rstrip("/")


def _http_get_json(url: str, timeout: float) -> dict | None:
    import urllib.request
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception:
        return None


def list_models(provider: str, cfg: dict) -> list[str]:
    """Fetch available model ids from the server's /v1/models (empty if unavailable)."""
    data = _http_get_json(f"{_base_url(provider, cfg)}/models", cfg.get("timeout", 8))
    if not data:
        return []
    return [m.get("id") for m in (data.get("data") or []) if m.get("id")]


def resolve_http_model(provider: str, cfg: dict) -> str:
    """Resolve the model to use. Priority: --model -> saved config -> first live server model -> default.
    With --auto-model, fetch dynamically from the live server and use that."""
    if cfg.get("model"):
        return cfg["model"]
    if cfg.get("auto_model"):
        saved = _load_models_config().get(provider, {})
        # With an explicit --base-url, only use saved config matching that endpoint.
        # Avoids polluting live discovery with a stale default from another endpoint.
        if saved.get("default") and (not cfg.get("base_url") or saved.get("base_url", "").rstrip("/") == _base_url(provider, cfg)):
            return saved["default"]
        live = list_models(provider, cfg)
        if live:
            return live[0]
    return _DEFAULT_MODEL.get(provider, "local-model")


def _load_models_config() -> dict:
    try:
        return json.loads(_MODELS_CACHE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


_TOKEN_LOCK = threading.Lock()


def _record_token_usage(cfg: dict, provider: str, usage: dict) -> None:
    """Roll up an OpenAI-compatible `usage` payload into `cfg["_token_usage"]` (#271/#296).

    `cfg` is expected to carry a per-run (or, for `_run_ab_variant`, per-variant)
    accumulator dict — callers own that lifetime so usage never blends across runs.
    CLI-based providers (claude/codex) don't expose structured usage and stay out of
    scope here; Anthropic's Usage & Cost Admin API is the right tool for those instead
    of estimating.
    """
    acc = cfg.get("_token_usage")
    if acc is None:
        return
    with _TOKEN_LOCK:
        a = acc.setdefault(provider, {"prompt_tokens": 0, "completion_tokens": 0, "calls": 0})
        a["prompt_tokens"] += usage.get("prompt_tokens", 0) or 0
        a["completion_tokens"] += usage.get("completion_tokens", 0) or 0
        a["calls"] += 1


def run_http_provider(provider: str, prompt: str, cfg: dict) -> tuple[int, str]:
    import urllib.request
    url = f"{_base_url(provider, cfg)}/chat/completions"
    model = resolve_http_model(provider, cfg)
    body = json.dumps({"model": model, "temperature": 0,
                       "messages": [{"role": "user", "content": prompt}]}).encode("utf-8")
    req = urllib.request.Request(url, data=body,
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=cfg.get("timeout", 600)) as r:
            data = json.loads(r.read().decode("utf-8"))
        if isinstance(data.get("usage"), dict):
            _record_token_usage(cfg, provider, data["usage"])
        return 0, data["choices"][0]["message"]["content"]
    except Exception as e:                      # connection failures, missing models etc. become rc!=0
        return 1, f"[{provider} error: {e} @ {url}]"


def _record_anthropic_usage(cfg: dict, usage: dict) -> None:
    """Normalize a direct Anthropic-Messages-API usage payload into the same accumulator
    _record_token_usage uses (#297). Maps `input_tokens`->`prompt_tokens` and
    `output_tokens`->`completion_tokens`; `cache_read_input_tokens` (billed at 10% of base
    input tokens on a fallback) is accumulated in its own field — extends rather than
    breaks the OpenAI-compatible schema (#271/#296)."""
    acc = cfg.get("_token_usage")
    if acc is None:
        return
    with _TOKEN_LOCK:
        a = acc.setdefault("anthropic", {"prompt_tokens": 0, "completion_tokens": 0,
                                         "cache_read_input_tokens": 0, "calls": 0})
        a["prompt_tokens"] += usage.get("input_tokens", 0) or 0
        a["completion_tokens"] += usage.get("output_tokens", 0) or 0
        a["cache_read_input_tokens"] += usage.get("cache_read_input_tokens", 0) or 0
        a["calls"] += 1


def run_anthropic_provider(prompt: str, cfg: dict, state: dict | None = None,
                           step_id: str | None = None) -> tuple[int, str]:
    """Call the Anthropic Messages API directly (for Fable 5 refusal-classifier + fallback
    detection, #297).

    A separate schema from the OpenAI-compatible `run_http_provider` (ollama/lmstudio) —
    Anthropic's own content blocks / `stop_reason` / `stop_details`. The `claude`/`rig`
    CLI providers (via `claude -p --output-format text`) never expose a structured
    stop_reason at all, so they're out of scope; this provider is for hitting the
    Messages API directly over HTTP only.

    Setting `cfg.get("fallback_model")` requests the `server-side-fallback-2026-06-01`
    beta. When the server transparently falls back, `FABLE_FALLBACK` is recorded in
    `state["history"]` and **the step continues as a normal success** (never rejected —
    per #297's requirement). A direct refusal (no fallback configured, or fallback
    exhausted) records `FABLE_REFUSAL` and is reported to the caller as a failure
    (rc=1, with the category embedded in the text — never a silent failure).
    """
    import urllib.request
    base = (cfg.get("base_url") or "https://api.anthropic.com").rstrip("/")
    url = f"{base}/v1/messages"
    model = cfg.get("model") or "claude-fable-5"
    fallback_model = cfg.get("fallback_model")
    body: dict = {"model": model, "max_tokens": cfg.get("max_tokens", 1024),
                 "messages": [{"role": "user", "content": prompt}]}
    if fallback_model:
        body["fallbacks"] = [{"model": fallback_model}]
    headers = {"Content-Type": "application/json",
              "anthropic-version": cfg.get("anthropic_version", "2023-06-01"),
              "x-api-key": cfg.get("api_key") or os.environ.get("ANTHROPIC_API_KEY", "")}
    if fallback_model:
        headers["anthropic-beta"] = "server-side-fallback-2026-06-01"
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"),
                                 headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=cfg.get("timeout", 600)) as r:
            data = json.loads(r.read().decode("utf-8"))
    except Exception as e:
        return 1, f"[anthropic error: {e} @ {url}]"

    if isinstance(data.get("usage"), dict):
        _record_anthropic_usage(cfg, data["usage"])

    blocks = data.get("content") or []
    fallback_block = next((b for b in blocks if b.get("type") == "fallback"), None)
    text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")

    if data.get("stop_reason") == "refusal" and not fallback_block:
        details = data.get("stop_details") or {}
        category = details.get("category", "unknown")
        if state is not None and step_id is not None:
            with _HIST_LOCK:
                state["history"].append({"action": "FABLE_REFUSAL", "step": step_id,
                                         "category": category,
                                         "explanation": details.get("explanation", "")})
        return 1, f"[fable refusal: category={category}] {details.get('explanation', '')}"

    if fallback_block:
        if state is not None and step_id is not None:
            with _HIST_LOCK:
                state["history"].append({"action": "FABLE_FALLBACK", "step": step_id,
                                         "from_model": (fallback_block.get("from") or {}).get("model"),
                                         "to_model": (fallback_block.get("to") or {}).get("model")})
        return 0, text  # a fallback is treated as a transparent success (never blocks the gate; #297)

    return 0, text


def discover_models(cfg: dict) -> dict:
    """Dynamically discover available providers and models (deterministically sorted)."""
    import shutil
    out: dict = {}
    for p in sorted(_OPENAI_BASE):
        models = sorted(list_models(p, cfg))
        out[p] = {"kind": "local-http", "base_url": _base_url(p, cfg),
                  "reachable": bool(models), "models": models,
                  "default": models[0] if models else None}
    for p in ("claude", "codex", "grok"):       # CLI providers: presence only
        out[p] = {"kind": "cli", "available": shutil.which(p) is not None, "models": []}
    out["rig"] = {"kind": "cli", "available": shutil.which("claude") is not None,
                  "note": "launches each step as a rig harness (claude)", "models": []}
    out["anthropic"] = {"kind": "remote-api", "available": bool(os.environ.get("ANTHROPIC_API_KEY")),
                       "note": "direct Messages API calls (Fable 5 refusal-classifier + fallback "
                               "detection, #297); reachability is judged only by whether "
                               "ANTHROPIC_API_KEY is set, no live connectivity check",
                       "models": []}
    return out


def cmd_models(args):
    cfg: dict = {}
    save = "--save" in args
    as_json = "--json" in args
    i = 0
    while i < len(args):
        if args[i] == "--base-url" and i + 1 < len(args):
            cfg["base_url"] = args[i + 1]
            i += 2
        else:
            i += 1
    found = discover_models(cfg)
    if as_json:
        print(json.dumps(found, ensure_ascii=False, indent=2))
    else:
        print("## rig orchestrate: available model discovery\n")
        for p, info in found.items():
            if info["kind"] == "local-http":
                status = (f"✓ {', '.join(info['models'])}" if info["reachable"]
                          else f"✗ server down / no models @ {info['base_url']}")
                print(f"  {p:<10} {status}")
            else:
                av = "✓ CLI present" if info.get("available") else "✗ CLI missing"
                print(f"  {p:<10} {av}{'  — ' + info['note'] if info.get('note') else ''}")
    if save:
        # Save config for local-http only (the default model is used by the next --auto-model)
        conf = {p: {"base_url": d["base_url"], "default": d["default"], "models": d["models"]}
                for p, d in found.items() if d["kind"] == "local-http" and d["reachable"]}
        _MODELS_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _MODELS_CACHE_PATH.write_text(json.dumps(conf, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nSaved: {_MODELS_CACHE_PATH} ({len(conf)} providers) — used by the next run --auto-model")


def run_provider(provider: str, role: str, prompt: str, cfg: dict, persona: str = "",
                 state: dict | None = None, step_id: str | None = None) -> tuple[int, str]:
    if provider in _OPENAI_BASE:
        return run_http_provider(provider, prompt, cfg)
    if provider == "anthropic":
        return run_anthropic_provider(prompt, cfg, state, step_id)
    argv = build_argv(provider, role, prompt, cfg, persona)
    try:
        r = subprocess.run(argv, input=prompt if provider in ("cmd", "mock") else None,
                           capture_output=True, text=True, timeout=cfg.get("timeout", 600),
                           cwd=cfg.get("cwd") or None)
    except FileNotFoundError:
        return 127, f"[provider not found: {provider}]"
    except subprocess.TimeoutExpired:
        return 124, "[provider timeout]"
    out = r.stdout or ""
    if r.returncode != 0 and r.stderr:
        out = (out + "\n" + r.stderr).strip()
    return r.returncode, out


def _excerpt(text: str, limit: int = 240) -> str:
    return " ".join((text or "").split())[:limit]


# \u2500\u2500 Output truncation budget \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500
# Cap for provider outputs captured into state/history/prompts and for git-diff
# evidence embedded in verify prompts. Head+tail clip with an explicit marker;
# the full text is spooled to the run dir (next to the run-state file) if one exists.
OUTPUT_CAP_CHARS = 30_000


def _clip_output(text: str, cap: int = OUTPUT_CAP_CHARS, full_path: str | None = None) -> str:
    """Head+tail clip to cap chars with a '[...truncated N chars...]' marker (pure)."""
    text = text or ""
    if len(text) <= cap:
        return text
    head_n = cap * 2 // 3
    tail_n = cap - head_n
    where = f"; full output at {full_path}" if full_path else ""
    marker = f"\n[...truncated {len(text) - cap} chars{where}]\n"
    return text[:head_n] + marker + text[-tail_n:]


def _spool_full_output(text: str, cfg: dict, label: str) -> str | None:
    """Write the full text to <run_dir>/step-outputs/<label>.txt. None if no run dir (best-effort)."""
    run_dir = (cfg or {}).get("run_dir")
    if not run_dir:
        return None
    try:
        d = pathlib.Path(run_dir) / "step-outputs"
        d.mkdir(parents=True, exist_ok=True)
        p = d / f"{label}.txt"
        p.write_text(text, encoding="utf-8")
        return str(p)
    except Exception:
        return None


def _capture_output(text: str, cfg: dict, label: str) -> str:
    """Apply the truncation budget to a captured provider output; spool the full text if possible."""
    text = text or ""
    if len(text) <= OUTPUT_CAP_CHARS:
        return text
    return _clip_output(text, full_path=_spool_full_output(text, cfg, label))


def _verdict_ok(out: str) -> bool:
    """Parse verifier output across Rig's machine verdict and review-verdict contracts.

    Evidence-first: both contracts put reasoning BEFORE the verdict, so the rationale may
    quote another verdict line. Prefer the LAST line that starts with a verdict token
    (`VERDICT:` / \u5224\u5b9a:) \u2014 the contract-mandated final position \u2014 over any earlier
    quote. \u5224\u5b9a ("hantei") is the verdict-line label of the Japanese review-verdict
    output contract (facets/output-contracts/review-verdict.md); keep parsing it.
    Token vocabulary and semantics are unchanged (PASS/APPROVE/APPROVE_WITH_CONDITIONS pass;
    FAIL/REJECT/unparseable fail closed). Legacy whole-text scan remains as a fallback for
    outputs with no line-anchored verdict."""
    text = out or ""
    last = None
    for line in text.splitlines():
        line = line.strip()
        if line.upper().startswith("VERDICT:") or line.startswith("\u5224\u5b9a:"):
            last = line
    if last is not None:
        verdict = last.split(":", 1)[1].strip().upper()
        return verdict.startswith(("PASS", "APPROVE"))  # REJECT/FAIL/garbage \u2192 fail-closed
    up = text.upper()
    if "VERDICT: FAIL" in up or "\u5224\u5b9a: REJECT" in text:
        return False
    return "VERDICT: PASS" in up


# Per-criterion verdict lines (`CRITERION <n>: PASS|FAIL|UNKNOWN \u2014 <anchor>`), tolerant of
# dash/colon variants. UNKNOWN is the explicit escape hatch for "insufficient evidence"
# (prevents the judge guessing PASS when it could not verify; see demystifying-evals).
_CRITERION_RE = re.compile(
    r"^\s*CRITERION\s+(\d+)\s*:\s*(PASS|FAIL|UNKNOWN)\b[\s\u2014\u2013:-]*(.*)$",
    re.IGNORECASE)


def _parse_criteria(out: str) -> list[dict]:
    """Tolerant parse of per-criterion verdict lines. Missing lines = empty list (old-format
    tolerance = old behavior). Later duplicates win; result sorted by criterion number (pure)."""
    found: dict[int, dict] = {}
    for line in (out or "").splitlines():
        m = _CRITERION_RE.match(line)
        if m:
            found[int(m.group(1))] = {"n": int(m.group(1)), "verdict": m.group(2).upper(),
                                      "anchor": m.group(3).strip()}
    return [found[n] for n in sorted(found)]


def _judge_output(out: str) -> tuple[bool, list[dict]]:
    """Overall verdict + per-criterion verdicts for one verifier output.

    UNKNOWN on a single criterion does not fail the gate by itself (it is recorded), but
    all-UNKNOWN criteria combined with VERDICT PASS downgrades to fail-closed: a judge that
    could verify nothing yet passes is rubber-stamping (style-over-substance mitigation)."""
    criteria = _parse_criteria(out)
    ok = _verdict_ok(out)
    if ok and criteria and all(c["verdict"] == "UNKNOWN" for c in criteria):
        ok = False
    return ok, criteria


def run_verifiers_parallel(ver, prompt: str, personas: list[str],
                           cfg: dict, max_parallel: int,
                           state: dict | None = None, step_id: str | None = None) -> list[dict]:
    """Run N verifiers in concurrent processes and return results in (persona, provider) order (deterministic).

    Passing a list as ver runs **the same persona across multiple providers** = a mixed-model
    quorum (heterogeneous votes correlate less than N votes from identical models; disagreement
    itself is a signal). Each vote's by is recorded as "provider:persona" in telemetry and can
    be audited per model via runs --personas."""
    import concurrent.futures as _f
    vers = ver if isinstance(ver, list) else [ver]
    personas = personas or ["reviewer"]
    tasks = [(v, p) for p in personas for v in vers]

    def _one(task):
        v, p = task
        rc, out = run_provider(v, "verifier", prompt, cfg, persona=p, state=state, step_id=step_id)
        ok, criteria = _judge_output(out)
        return {"by": f"{v}:{p}", "persona": p, "provider": v, "ok": ok,
                "criteria": criteria, "note": f"exit {rc}; {_excerpt(out)}"}

    if len(tasks) == 1:
        return [_one(tasks[0])]
    with _f.ThreadPoolExecutor(max_workers=max(1, max_parallel)) as ex:
        res = list(ex.map(_one, tasks))
    return sorted(res, key=lambda r: (r["persona"], r["provider"]))  # deterministic regardless of completion order


_MANAGED_AGENTS_BETA = "managed-agents-2026-04-01"


def _managed_agents_request(base: str, path: str, cfg: dict, body: dict | None = None,
                            method: str = "POST") -> dict:
    """Thin HTTP wrapper over the (beta) Managed Agents API (#295).

    **Note**: endpoint paths (`/v1/agents` etc.) are inferred from the documented Python
    SDK method names (`client.beta.agents.create` etc.), not confirmed directly against an
    official REST reference (this script stays stdlib-only, so it hits the endpoints with
    urllib rather than depending on the SDK). Confirm the actual paths against the
    `anthropic` Python SDK source / official docs before relying on this in production.
    """
    import urllib.request
    url = f"{base}/{path.lstrip('/')}"
    headers = {"Content-Type": "application/json",
              "anthropic-version": cfg.get("anthropic_version", "2023-06-01"),
              "anthropic-beta": _MANAGED_AGENTS_BETA,
              "x-api-key": cfg.get("api_key") or os.environ.get("ANTHROPIC_API_KEY", "")}
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=cfg.get("timeout", 600)) as r:
        return json.loads(r.read().decode("utf-8"))


def run_managed_agents_fanout(prompt: str, personas: list[str], cfg: dict,
                              state: dict | None = None, step_id: str | None = None) -> list[dict]:
    """Delegate review fan-out to the Anthropic Managed Agents API (coordinator/worker beta;
    #295, opt-in experimental backend).

    Only called from `_execute_step` when `cfg.get("parallel_backend") == "managed-agents"`.
    The default (unset) stays on the existing `run_verifiers_parallel` (subprocess +
    ThreadPoolExecutor) — this backend is entirely opt-in and its failure never touches the
    existing path.

    One worker agent is created per persona; a judgment-only coordinator fans out to them.
    A worker's raw output (a large diff/log, etc.) stays inside its managed-environment
    thread — only the coordinator's distilled result crosses back. **That isolation itself
    is an Anthropic server-side property this client code cannot verify.** What this code
    does guarantee is that rig never requests, stores, or forwards raw worker output beyond
    the API's own returned result. `cfg["environment_id"]` is required (the Managed Agents
    host environment) — if unset, this errors immediately rather than failing silently.
    """
    base = (cfg.get("base_url") or "https://api.anthropic.com").rstrip("/")
    env_id = cfg.get("environment_id")
    if not env_id:
        return [{"by": "managed-agents:error", "persona": "-", "provider": "managed-agents",
                 "ok": False, "note": "cfg['environment_id'] is unset; cannot start Managed Agents"}]
    personas = personas or ["reviewer"]
    model = cfg.get("model") or "claude-sonnet-5"
    coordinator_model = cfg.get("coordinator_model") or model

    try:
        workers = []
        for p in personas:
            w = _managed_agents_request(base, "v1/agents", cfg, {
                "name": f"worker-{p}", "model": model, "tools": [],
                "system": f"You are the {p} reviewer worker. Return only your verdict via submit_result.",
                "betas": [_MANAGED_AGENTS_BETA],
            })
            workers.append((p, w["id"]))
        coordinator = _managed_agents_request(base, "v1/agents", cfg, {
            "name": "coordinator", "model": coordinator_model,
            "multiagent": {"type": "coordinator",
                          "agents": [{"type": "agent", "id": wid} for _, wid in workers]},
            "system": "Delegate one review to each worker and aggregate the results.",
            "betas": [_MANAGED_AGENTS_BETA],
        })
        session = _managed_agents_request(base, "v1/sessions", cfg,
                                          {"agent": coordinator["id"], "environment_id": env_id,
                                           "betas": [_MANAGED_AGENTS_BETA]})
        session_id = session["id"]
        _managed_agents_request(base, f"v1/sessions/{session_id}/events", cfg, {
            "betas": [_MANAGED_AGENTS_BETA],
            "events": [{"type": "user.message", "content": [{"type": "text", "text": prompt}]}],
        })

        max_polls = cfg.get("managed_agents_max_polls", 30)
        poll_interval = cfg.get("managed_agents_poll_interval", 2)
        threads: list = []
        for _ in range(max_polls):
            resp = _managed_agents_request(base, f"v1/sessions/{session_id}/threads", cfg,
                                           method="GET")
            threads = resp.get("data") or resp.get("threads") or []
            if len(threads) >= len(workers) + 1:  # workers + coordinator
                break
            time.sleep(poll_interval)

        total_usage = {"input_tokens": 0, "output_tokens": 0, "cache_read_input_tokens": 0}
        results = []
        by_agent_id = {wid: p for p, wid in workers}
        for t in threads:
            u = t.get("usage") or {}
            for k in total_usage:
                total_usage[k] += u.get(k, 0) or 0
            agent_id = t.get("agent_id") or t.get("agent", {}).get("id")
            persona = by_agent_id.get(agent_id)
            if persona is None:
                continue  # the coordinator's own thread (not a worker) isn't counted as a review vote
            text = "".join(b.get("text", "") for b in (t.get("content") or []) if b.get("type") == "text")
            ok = ("VERDICT: PASS" in text) and ("VERDICT: FAIL" not in text)
            results.append({"by": f"managed-agents:{persona}", "persona": persona,
                            "provider": "managed-agents", "ok": ok, "note": f"session={session_id}"})

        acc = cfg.get("_token_usage")
        if acc is not None:
            with _TOKEN_LOCK:
                a = acc.setdefault("managed-agents", {"prompt_tokens": 0, "completion_tokens": 0,
                                                       "cache_read_input_tokens": 0, "calls": 0})
                a["prompt_tokens"] += total_usage["input_tokens"]
                a["completion_tokens"] += total_usage["output_tokens"]
                a["cache_read_input_tokens"] += total_usage["cache_read_input_tokens"]
                a["calls"] += 1
        if state is not None and step_id is not None:
            with _HIST_LOCK:
                state["history"].append({"action": "MANAGED_AGENTS_SESSION", "step": step_id,
                                         "session_id": session_id, "workers": len(workers)})

        missing = [p for p, _ in workers if p not in {r["persona"] for r in results}]
        for p in missing:  # a worker that never reported in even after max_polls is marked "unmeasured", not silently dropped
            results.append({"by": f"managed-agents:{p}", "persona": p, "provider": "managed-agents",
                            "ok": False, "note": f"timeout (session={session_id}; not in after {max_polls} polls)"})
        return sorted(results, key=lambda r: r["persona"])  # deterministic (rig's own aggregation code only; the LLM outputs themselves are a separate concern)
    except Exception as e:
        return [{"by": "managed-agents:error", "persona": "-", "provider": "managed-agents",
                 "ok": False, "note": f"managed-agents error: {e}"}]


def _build_step_contract(state: dict, step: dict, st: dict | None = None) -> str:
    # The goal is external task text — it can originate from a GitHub Issue/PR
    # body or comment (via gh-flow) or a queue item, i.e. third-party-authored
    # content. Structurally quarantine it (wrap_untrusted) so an implementing
    # persona reads it as DATA describing the task, never as instructions that
    # override this harness (OWASP LLM01 / spotlighting / CaMeL). Absent goals
    # keep the original "(none)" sentinel — nothing external to fence.
    goal = state.get("goal")
    goal_line = wrap_untrusted(goal, "task text") if goal else "(none)"
    lines = [
        f"recipe: {state['recipe']}",
        f"step: {step['id']} ({step['instruction']})",
        f"goal: {goal_line}",
    ]
    if st is not None:
        attempt = int(st.get("retries", 0)) + 1
        lines.append(f"attempt: {attempt}")
        if st.get("last_failure"):
            lines.append(f"previous_failure: {st['last_failure']}")
        recent = state.get("history", [])[-3:]
        if recent:
            lines.append("recent_history:")
            lines.extend([f"- {h.get('action')}:{h.get('step')}" for h in recent])
    if step["id"] == "implement":
        lines += [
            "must: actually edit the code; do not stop at just reading.",
            "must: keep changes minimal; no unrelated formatting or broad refactors.",
            "must: do not change tests; if a change is needed, state the reason explicitly.",
            "must: keep working until a diff exists; do not finish as a no-op.",
            "must: run related tests / lint where possible and confirm the results.",
            "report: output CHANGED_FILES / COMMANDS_RUN / RESULT concisely.",
        ]
    elif step["id"] == "test":
        lines += [
            "must: actually run the test command.",
            "must: on failure, identify the cause, apply a minimal fix, and rerun.",
            "must: if it still fails, state in one line what you will change next.",
            "must: state pass / fail and the commands you ran.",
            "report: output COMMANDS_RUN / RESULT / REMAINING_RISK concisely.",
        ]
    elif step["id"] == "acceptance":
        criteria = step.get("acceptance") or []
        lines += [
            "must: perform final confirmation only; check the acceptance criteria mechanically.",
            "must: state explicitly whether the changes and test results meet the criteria.",
            "must: if unmet, write concretely what is missing.",
        ]
        if criteria:
            lines.append("acceptance_criteria:")
            lines.extend([f"- {c}" for c in criteria])
    else:
        lines += [
            "must: actually move the request forward; do not stop at analysis.",
        ]
    return "\n".join(lines)


def _build_prompt(state: dict, step: dict, st: dict | None = None) -> str:
    contract = _build_step_contract(state, step, st)
    return (
        f"You are a rig subagent (in charge of {step['id']}).\n"
        f"{contract}\n"
        "Keep output concise. When the work is complete, end with 'STATUS: done'."
    )


def _git_diff_evidence(cfg: dict) -> str | None:
    """Capture `git diff HEAD` (fallback: `git diff`) from the step's cwd/worktree as primary
    verification evidence, clipped to OUTPUT_CAP_CHARS (head+tail with a truncation marker).
    Returns None (→ caller falls back to report-only verification) when the step has no cwd,
    git is unavailable, or the diff is empty."""
    cwd = (cfg or {}).get("cwd")
    if not cwd:
        return None
    for args in (["git", "diff", "HEAD"], ["git", "diff"]):
        try:
            r = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=60)
        except (OSError, subprocess.SubprocessError):
            return None
        if r.returncode == 0:
            out = r.stdout or ""
            return _clip_output(out) if out.strip() else None
    return None


def _build_verify_prompt(state: dict, step: dict, product: str, diff: str | None = None) -> str:
    """Verify-prompt with the diff as primary evidence (when available) and the generator's
    report explicitly labeled as unverified claims — the judge must check claims against the
    diff instead of trusting the generator's transcript (CodeJudgeBench / MT-Bench findings).
    Contract: evidence-anchored reasoning first, per-criterion lines, VERDICT as the last line."""
    criteria = step.get("acceptance") or []
    lines = [
        "You are an independent verifier (a separate process and role from the agent that generated this step).",
        f"Judge whether the product of step '{step['id']}' meets the acceptance criteria.",
    ]
    if criteria:
        lines.append("Acceptance criteria:")
        lines += [f"  {n}. {c}" for n, c in enumerate(criteria, 1)]
    lines += [
        "Output format (strict):",
        "1. First, 2-5 lines of evidence-anchored reasoning (each line cites file:line or a short",
        "   quote of the evidence). Reason BEFORE judging; never state a verdict first.",
    ]
    if criteria:
        lines += [
            "2. Then exactly one line per acceptance criterion, in order:",
            "   CRITERION <n>: PASS|FAIL|UNKNOWN — <anchor>",
            "   Use UNKNOWN when the evidence is insufficient to judge that criterion; do not guess.",
        ]
    lines += [
        "Finally, the very last line of your output must be exactly one of:",
        "VERDICT: PASS",
        "VERDICT: FAIL",
        "Do not add extra characters, Markdown, or punctuation to the last line, and do not",
        "place the verdict before the reasoning.",
    ]
    if diff:
        lines += [
            "--- diff (primary evidence: the actual changes) ---",
            diff,
            "--- report below is the generator's own claims — verify them against the diff, do not trust them ---",
            (product or "")[:2000],
            "Check each claim in the report against the diff; a claim with no supporting evidence in the diff is unverified.",
        ]
    else:
        lines += ["--- product ---", (product or "")[:2000]]
    return "\n".join(lines)


def _run_step_checks(step: dict, st: dict, cfg: dict | None = None) -> None:
    st["checks"] = []
    cwd = (cfg or {}).get("cwd") or str(config.INVOCATION_CWD)
    for cmd in step["checks"]:
        r = subprocess.run(cmd, shell=True, cwd=cwd,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        st["checks"].append({"cmd": cmd, "ok": r.returncode == 0})
    failed = [c["cmd"] for c in st["checks"] if not c["ok"]]
    st["last_failure"] = None if not failed else "checks failed: " + "; ".join(failed)


_HIST_LOCK = threading.Lock()


def _read_runs_jsonl(path: pathlib.Path) -> list[dict]:
    """Local copy of commands.py's _read_jsonl (kept private to avoid a providers<->commands
    import cycle: commands.py already imports from providers.py)."""
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


# ── Per-step model assignment (issue #293) ────────────────────────────────────
def parse_step_model_spec(spec: str) -> tuple[str, str] | None:
    """Parse one --step-model value ("<step-id>=<model>"). Returns (step_id, model), or None if malformed (pure)."""
    sid, sep, model = spec.partition("=")
    if not sep or not sid.strip() or not model.strip():
        return None
    return sid.strip(), model.strip()


def unknown_step_model_ids(step_models: dict, steps: list[dict]) -> list[str]:
    """Step ids named by --step-model that do not exist in the recipe (pure; sorted).
    Non-empty means the run must abort before any execution (no silent ignores)."""
    known = {s["id"] for s in steps}
    return sorted(sid for sid in step_models if sid not in known)


def effective_step_models(step: dict, cfg: dict) -> tuple[str | None, str | None]:
    """Effective (generator_model, verifier_model) for one step (pure).
    Generator precedence: runtime --step-model > recipe `model:` > global --model.
    Verifier: recipe `verifier_model:` > the effective generator model.
    The same resolution point will host per-step *provider* assignment later (#293 follow-up)."""
    gen = ((cfg.get("step_models") or {}).get(step["id"])
           or step.get("model") or cfg.get("model"))
    ver = step.get("verifier_model") or gen
    return gen, ver


def _generate(state: dict, step: dict, gen_list: list[str], ver: str,
              cfg: dict, max_parallel: int) -> tuple[str | None, str, list[dict]]:
    """Generate solo or via judge-panel. With multiple generators, run them all in parallel and
    have the judge (ver) evaluate EVERY candidate (never stop at the first PASS — position
    bias / order effects, MT-Bench §3). Winner selection stays deterministic and documented:
    among all PASSing candidates, the first in generator-list order wins; the judged[] entries
    record the full pass-set so a multi-PASS (order-sensitive) pick is visible in telemetry.
    Returns: (winner_provider | None, product, judged[]); the winning judged entry is marked
    with "winner": True.
    Per-step models (runtime --step-model > recipe `model:`/`verifier_model:` > global --model)
    are injected into a copy of cfg (parallel-safe)."""
    gen_model, ver_model = effective_step_models(step, cfg)
    gen_cfg = {**cfg, "model": gen_model} if gen_model else cfg
    ver_cfg = {**cfg, "model": ver_model} if ver_model else cfg
    if len(gen_list) == 1:
        _, out = run_provider(gen_list[0], "generator", _build_prompt(state, step, state["step_state"][step["id"]]), gen_cfg,
                              state=state, step_id=step["id"])
        return gen_list[0], _capture_output(out, cfg, f"{step['id']}-{gen_list[0]}"), []
    def _gen(p):
        rc, out = run_provider(p, "generator", _build_prompt(state, step, state["step_state"][step["id"]]), gen_cfg,
                               state=state, step_id=step["id"])
        return {"provider": p, "rc": rc, "out": out}
    with futures.ThreadPoolExecutor(max_workers=max(1, max_parallel)) as ex:
        cands = list(ex.map(_gen, gen_list))
    cands.sort(key=lambda c: gen_list.index(c["provider"]))   # evaluate in generation order = deterministic
    for i, c in enumerate(cands):
        c["out"] = _capture_output(c["out"], cfg, f"{step['id']}-{c['provider']}-cand{i + 1}")
    judged, winner, product = [], None, cands[0]["out"]
    jver = ver[0] if isinstance(ver, list) else ver            # the judge is the first verifier provider
    diff = _git_diff_evidence(cfg)                             # verify the diff, not the transcript
    for c in cands:                                            # judge ALL candidates (no early stop)
        _, jout = run_provider(jver, "verifier", _build_verify_prompt(state, step, c["out"], diff),
                               ver_cfg, persona="judge", state=state, step_id=step["id"])
        ok, criteria = _judge_output(jout)
        judged.append({"provider": c["provider"], "ok": ok, "criteria": criteria,
                       "note": _excerpt(jout)})
        if ok and winner is None:
            winner, product = c["provider"], c["out"]
            judged[-1]["winner"] = True
    return winner, product, judged


def _execute_step(state: dict, step: dict, st: dict, gen_list: list[str], ver: str,
                  cfg: dict, max_parallel: int, quorum: str, log) -> None:
    """Execute one step: generate (separate process; judge-panel capable) -> record gate evidence (checks or parallel verification)."""
    effective_step = step
    # Cost-tier auto-routing (#264): only a fallback default. Runtime --step-model and the
    # recipe's own `model:` both still win outright — auto_route never overrides an explicit
    # choice, it only fills in when neither is set (sits between recipe model: and the global
    # --model default).
    if (cfg.get("auto_route") and step.get("auto_route") and not step.get("model")
            and not (cfg.get("step_models") or {}).get(step["id"])):
        size = size_class(git_diff_lines(), load_manifest().get("size_thresholds"))
        routed_model, reason = resolve_auto_route(step, size)
        applied_model, applied_reason = routed_model, reason  # #264's static pick (default/fallback)

        # #305: learned route from historical data. Default is shadow mode — the prediction is
        # always recorded in history, but only affects the actual choice under
        # --auto-route-mode active (staged rollout: shadow -> confidence threshold -> active).
        if cfg.get("auto_route_learn"):
            runs_rows = _read_runs_jsonl(config.RUNS_PATH)
            expl_key = f"{state['recipe']}:{step['id']}:{cfg.get('exploration_date', '')}"
            learned = learned_auto_route(state["recipe"], step["id"], step["auto_route"]["candidates"],
                                         runs_rows, exploration_key=expl_key,
                                         exploration_pct=cfg.get("exploration_pct", 0))
            active = cfg.get("auto_route_mode", "shadow") == "active"
            with _HIST_LOCK:
                state["history"].append({"action": "LEARNED_ROUTE_PREDICTION", "step": step["id"],
                                         "sufficient": learned["sufficient"],
                                         "predicted_model": learned.get("model"),
                                         "evidence": learned.get("evidence"),
                                         "explored_from": learned.get("explored_from"),
                                         "counterfactuals": learned["counterfactuals"], "applied": False})
            if learned["sufficient"] and active:
                applied_model, applied_reason = learned["model"], f"learned route (evidence: {learned['evidence']})"
                state["history"][-1]["applied"] = True  # upgrade the PREDICTION just pushed to "actually applied"
                log(f"   ↳ learned-route (active): {applied_model}")
            elif not learned["sufficient"]:
                log("   ↳ learned-route: insufficient sample, falling back to static auto-route")

        if applied_model:
            effective_step = {**step, "model": applied_model}
            with _HIST_LOCK:
                state["history"].append({"action": "AUTO_ROUTE", "step": step["id"],
                                         "model": applied_model, "reason": applied_reason})
            log(f"   ↳ auto-route: {applied_model} ({applied_reason})")
    gen_model, ver_model = effective_step_models(effective_step, cfg)
    if gen_model:
        st["model"] = gen_model                     # actually-used generator model (run-state/telemetry attribution)
    winner, out, judged = _generate(state, effective_step, gen_list, ver, cfg, max_parallel)
    with _HIST_LOCK:
        state["history"].append({"action": "EXEC", "step": step["id"],
                                 "provider": winner or gen_list[0], "out": out[:200],
                                 **({"model": gen_model} if gen_model else {})})
    if judged:
        log(f"   ↳ judge-panel {len(judged)} candidates → winner: {winner or '(none)'}")
    else:
        log(f"   ↳ {gen_list[0]}:generator")
    if step["checks"]:
        _run_step_checks(step, st, cfg)
        log(f"   ↳ checks: {sum(c['ok'] for c in st['checks'])}/{len(st['checks'])} ok")
        return
    if step["gate"] not in ("acceptance-gate", "review-gate"):
        return
    ver_label = "+".join(ver) if isinstance(ver, list) else ver
    if judged:
        # judge-panel: the judge selects, so its gate judgment is adopted (pass if there is a winner).
        # All candidates were judged; with multiple PASSes the first in generator-list order wins
        # (deterministic), and the full pass-set is recorded (order_sensitive) instead of
        # silently stopping at the first PASS.
        rec = {"by": f"{ver_label}:judge-panel", "ok": winner is not None,
               "criteria": next((j.get("criteria", []) for j in judged if j.get("winner")), []),
               "note": "winner=" + str(winner)}
        pass_set = [j["provider"] for j in judged if j["ok"]]
        if len(pass_set) > 1:
            rec["order_sensitive"] = True
            rec["pass_set"] = pass_set
            rec["note"] += f"; multi-pass {pass_set} → kept first in generator-list order"
        st["verdicts"].append(rec)
        return
    # Lens verification = N independent reviewers in parallel processes (grader != generator)
    # Per-step `verifier_model:` is injected into a copy of cfg (independent of the generator side)
    v_cfg = {**cfg, "model": ver_model} if ver_model else cfg
    personas = step["personas"] or ["independent"]
    verify_prompt = _build_verify_prompt(state, step, out, _git_diff_evidence(cfg))
    if cfg.get("parallel_backend") == "managed-agents":  # #295: opt-in experimental backend
        results = run_managed_agents_fanout(verify_prompt, personas, v_cfg,
                                            state=state, step_id=step["id"])
    else:
        results = run_verifiers_parallel(ver, verify_prompt,
                                         personas, v_cfg, max_parallel, state=state, step_id=step["id"])
    passes, total = sum(1 for r in results if r["ok"]), len(results)
    par = "parallel" if total > 1 else "solo"
    log(f"   ↳ {par} verification x{total}: PASS {passes}/{total} (quorum={quorum})")
    if quorum == "majority" and total > 1:
        st["verdicts"].append({
            "by": f"{ver_label}:quorum-majority", "ok": passes * 2 > total,
            "note": f"{passes}/{total} pass; " + ", ".join(
                f"{r['persona']}={'P' if r['ok'] else 'F'}" for r in results)})
    else:
        st["verdicts"].extend(results)


def run_loop(state: dict, sp: pathlib.Path | None, gen: str, ver: str,
             cfg: dict, max_steps: int, quiet: bool = False,
             max_parallel: int = 4, quorum: str = "all",
             generators: list[str] | None = None) -> str:
    """Autonomous loop. If any step has needs:, switch automatically to DAG-parallel mode (independent steps run concurrently)."""
    log = (lambda *a: None) if quiet else print
    gen_list = generators or [gen]
    if sp is not None:      # run dir = where the run-state lives; full over-budget outputs spool there
        cfg = {**cfg, "run_dir": cfg.get("run_dir") or str(pathlib.Path(sp).resolve().parent)}
    if any(s["needs"] for s in state["steps"]):
        final = run_dag(state, sp, gen_list, ver, cfg, max_steps, quiet, max_parallel, quorum)
        state["token_usage"] = cfg.get("_token_usage") or {}
        telemetry_append(state, final)
        return final
    iters, last = 0, "—"
    while iters < max_steps:
        iters += 1
        action, msg = compute_next(state)
        last = action
        log(f"▶ {action}: {msg}")
        if action == "START":
            step = state["steps"][state["cursor"]]
            _execute_step(state, step, state["step_state"][step["id"]],
                          gen_list, ver, cfg, max_parallel, quorum, log)
            if sp:
                save_state(state, sp)
            continue
        if action in ("ADVANCE", "RETRY", "AWAIT"):
            if sp:
                save_state(state, sp)
            continue
        break  # DONE / ESCALATE / BLOCKED / STOPPED
    if sp:
        save_state(state, sp)
    state["token_usage"] = cfg.get("_token_usage") or {}
    telemetry_append(state, last)
    return last


def run_dag(state: dict, sp: pathlib.Path | None, gen_list: list[str], ver: str,
            cfg: dict, max_steps: int, quiet: bool, max_parallel: int, quorum: str) -> str:
    """Step-DAG parallel runner. Independent steps whose dependencies (needs) are met run in concurrent processes.
    Each wave's ready set is in id order (deterministic); gate evaluation is applied in id order too."""
    log = (lambda *a: None) if quiet else print
    state.setdefault("waves", [])
    waves = 0
    while waves < max_steps:
        waves += 1
        if state["stopped"]:
            break
        ss = state["step_state"]
        passed = {sid for sid, st in ss.items() if st["status"] == "passed"}
        if len(passed) == len(state["steps"]):
            state["done"] = True
            log("▶ DONE: all steps complete.")
            break
        ready = sorted((s for s in state["steps"]
                        if ss[s["id"]]["status"] == "pending"
                        and all(d in passed for d in s["needs"])),
                       key=lambda s: s["id"])
        if not ready:
            state["stopped"] = {"reason": "DAG: no runnable steps (unmet dependencies / failures)",
                                "kind": "ESCALATE", "at": "—"}
            break
        ids = [s["id"] for s in ready]
        state["waves"].append(ids)
        log(f"▶ WAVE {waves}: running {ids} in parallel")
        for s in ready:
            ss[s["id"]]["status"] = "running"
        with futures.ThreadPoolExecutor(max_workers=max(1, max_parallel)) as ex:
            list(ex.map(lambda s: _execute_step(state, s, ss[s["id"]], gen_list, ver,
                                                cfg, max_parallel, quorum,
                                                (lambda *a: None)), ready))
        for s in ready:                       # apply gate evaluation in id order (deterministic)
            st = ss[s["id"]]
            outcome = gate_outcome(s, st)
            if outcome == "pass":
                st["status"] = "passed"
                log(f"   ✓ {s['id']}")
            elif outcome == "self-graded":
                state["stopped"] = {"reason": f"{s['id']}: self-graded (by=self)", "kind": "BLOCKED", "at": s["id"]}
            else:
                st["retries"] += 1
                if st["retries"] >= s["max_retries"]:
                    state["stopped"] = {"reason": f"{s['id']} failed the gate {s['max_retries']} times",
                                        "kind": "ESCALATE", "at": s["id"]}
                else:
                    st["status"], st["checks"], st["verdicts"] = "pending", [], []
                    log(f"   ↻ {s['id']} retry (try {st['retries']+1}/{s['max_retries']})")
        if sp:
            save_state(state, sp)
    if sp:
        save_state(state, sp)
    if state.get("done"):
        return "DONE"
    if state["stopped"]:
        return state["stopped"].get("kind", "ESCALATE")
    return "—"


# ── Provider connectivity test ───────────────────────────────────────────────
def cmd_probe(args):
    """Hit the provider once and show the actual command, output, and whether the contract parses.
    Examples: orchestrate.py probe --provider codex          (checks VERDICT in the verifier role)
              orchestrate.py probe --provider codex --role generator
              orchestrate.py probe --provider ollama --model llama3.1"""
    provider, role, cfg = None, "verifier", {}
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--provider" and i + 1 < len(args):
            provider = args[i + 1]
            i += 2
        elif a == "--role" and i + 1 < len(args):
            role = args[i + 1]
            i += 2
        elif a == "--model" and i + 1 < len(args):
            cfg["model"] = args[i + 1]
            i += 2
        elif a == "--base-url" and i + 1 < len(args):
            cfg["base_url"] = args[i + 1]
            i += 2
        elif a == "--provider-cmd" and i + 1 < len(args):
            cfg["provider_cmd"] = args[i + 1]
            i += 2
        else:
            i += 1
    if not provider:
        print("[ERROR] --provider <name> is required (rig|claude|codex|grok|ollama|lmstudio|anthropic|cmd|mock)")
        sys.exit(1)
    prompt = ("Judge whether a product meets its acceptance criteria and end with exactly one line: "
              "'VERDICT: PASS' or 'VERDICT: FAIL'.\nProduct: 2 + 2 = 4"
              if role == "verifier" else
              "Compute 1 + 1 and end with 'STATUS: done'.")
    sig = "VERDICT" if role == "verifier" else "STATUS"
    print(f"## probe: provider={provider} / role={role}")
    if provider in _OPENAI_BASE:
        print(f"  endpoint : {_base_url(provider, cfg)}/chat/completions")
        print(f"  model    : {resolve_http_model(provider, cfg)}")
    elif provider == "anthropic":
        base = (cfg.get("base_url") or "https://api.anthropic.com").rstrip("/")
        print(f"  endpoint : {base}/v1/messages")
        print(f"  model    : {cfg.get('model') or 'claude-fable-5'}")
    else:
        argv = build_argv(provider, role, "<PROMPT>", cfg, "probe")
        print("  command  : " + " ".join(shlex.quote(a) for a in argv))
    rc, out = run_provider(provider, role, prompt, cfg, persona="probe")
    found = sig in (out or "")
    print(f"  exit     : {rc}")
    print("  --- output (first 600 chars) ---")
    print("  " + (out or "")[:600].replace("\n", "\n  "))
    print(f"  → {sig} detected: " + ("✓ parseable (usable from rig)" if found
                                else "✗ not found (prompt/flag tuning needed; the cmd provider accepts an explicit command)"))
    sys.exit(0 if (rc == 0 and found) else 1)

