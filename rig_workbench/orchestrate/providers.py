"""orchestrate providers: execution layer / provider abstraction / local LLM HTTP (split from scripts/orchestrate.py)."""

import sys
import os
import json
import shlex
import threading
import pathlib
import subprocess
import concurrent.futures as futures

from . import config
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


def build_argv(provider: str, role: str, prompt: str, cfg: dict, persona: str = "") -> list[str]:
    if provider == "mock":
        return ["python3", "-c", MOCK_SRC, role, persona]
    if provider == "rig":
        # Launch each step as a "rig harness" via headless claude (invokes rig by name).
        pre = RIG_VER_PREFIX if role == "verifier" else RIG_GEN_PREFIX
        argv = ["claude", "-p", pre + prompt, "--output-format", "text"]
        if cfg.get("model"):
            argv += ["--model", cfg["model"]]              # per-step model support
        return argv + _READONLY_ENFCE["claude"] if role == "verifier" else argv
    if provider == "claude":
        # Headless. In production the user can tune permission modes etc. via --provider-cmd.
        argv = ["claude", "-p", prompt, "--output-format", "text"]
        if cfg.get("model"):
            argv += ["--model", cfg["model"]]              # per-step model support
        return argv + _READONLY_ENFCE["claude"] if role == "verifier" else argv
    if provider == "codex":
        # --skip-git-repo-check: keep codex from refusing to start in non-git directories
        # (e.g. overlay targets in cross-project use). The sandbox stays enabled, so this is safe.
        argv = ["codex", "exec", "--skip-git-repo-check"]
        argv += ["--sandbox", "workspace-write" if role == "generator" else "read-only"]
        if cfg.get("model"):
            argv += ["-m", cfg["model"]]                   # per-step model support
        return argv + [prompt]
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
        return 0, data["choices"][0]["message"]["content"]
    except Exception as e:                      # connection failures, missing models etc. become rc!=0
        return 1, f"[{provider} error: {e} @ {url}]"


def discover_models(cfg: dict) -> dict:
    """Dynamically discover available providers and models (deterministically sorted)."""
    import shutil
    out: dict = {}
    for p in sorted(_OPENAI_BASE):
        models = sorted(list_models(p, cfg))
        out[p] = {"kind": "local-http", "base_url": _base_url(p, cfg),
                  "reachable": bool(models), "models": models,
                  "default": models[0] if models else None}
    for p in ("claude", "codex"):               # CLI providers: presence only
        out[p] = {"kind": "cli", "available": shutil.which(p) is not None, "models": []}
    out["rig"] = {"kind": "cli", "available": shutil.which("claude") is not None,
                  "note": "launches each step as a rig harness (claude)", "models": []}
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


def run_provider(provider: str, role: str, prompt: str, cfg: dict, persona: str = "") -> tuple[int, str]:
    if provider in _OPENAI_BASE:
        return run_http_provider(provider, prompt, cfg)
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


def _verdict_ok(out: str) -> bool:
    """Parse verifier output across Rig's machine verdict and review-verdict contracts."""
    text = out or ""
    up = text.upper()
    # \u5224\u5b9a ("hantei") is the verdict-line label of the Japanese review-verdict
    # output contract (facets/output-contracts/review-verdict.md); keep parsing it.
    if "VERDICT: FAIL" in up or "\u5224\u5b9a: REJECT" in text:
        return False
    if "VERDICT: PASS" in up:
        return True
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("\u5224\u5b9a:"):
            continue
        verdict = line.split(":", 1)[1].strip().upper()
        return verdict in ("APPROVE", "APPROVE_WITH_CONDITIONS")
    return False


def run_verifiers_parallel(ver, prompt: str, personas: list[str],
                           cfg: dict, max_parallel: int) -> list[dict]:
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
        rc, out = run_provider(v, "verifier", prompt, cfg, persona=p)
        ok = _verdict_ok(out)
        return {"by": f"{v}:{p}", "persona": p, "provider": v, "ok": ok,
                "note": f"exit {rc}; {_excerpt(out)}"}

    if len(tasks) == 1:
        return [_one(tasks[0])]
    with _f.ThreadPoolExecutor(max_workers=max(1, max_parallel)) as ex:
        res = list(ex.map(_one, tasks))
    return sorted(res, key=lambda r: (r["persona"], r["provider"]))  # deterministic regardless of completion order


def _build_step_contract(state: dict, step: dict, st: dict | None = None) -> str:
    lines = [
        f"recipe: {state['recipe']}",
        f"step: {step['id']} ({step['instruction']})",
        f"goal: {state.get('goal') or '(none)'}",
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


def _build_verify_prompt(state: dict, step: dict, product: str) -> str:
    return (
        f"You are an independent verifier (a separate process and role from the agent that generated this step).\n"
        f"Judge whether the product of step '{step['id']}' meets the acceptance criteria.\n"
        "The very last line of your output must be exactly one of:\n"
        "VERDICT: PASS\n"
        "VERDICT: FAIL\n"
        "A short explanation may precede it. Do not add extra characters, Markdown, or punctuation to the last line.\n"
        f"--- product ---\n{product[:2000]}"
    )


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


def _generate(state: dict, step: dict, gen_list: list[str], ver: str,
              cfg: dict, max_parallel: int) -> tuple[str | None, str, list[dict]]:
    """Generate solo or via judge-panel. With multiple generators, run them all in parallel and
    let the judge (ver) pick the first PASSing candidate (in generator-list order = deterministic)
    as the winner.
    Returns: (winner_provider | None, product, judged[]).
    Per-step `model:` / `verifier_model:` are injected into a copy of cfg (parallel-safe)."""
    gen_cfg = {**cfg, "model": step["model"]} if step.get("model") else cfg
    ver_cfg = {**cfg, "model": step["verifier_model"] or step.get("model") or cfg.get("model")} \
              if (step.get("verifier_model") or step.get("model")) else cfg
    if len(gen_list) == 1:
        _, out = run_provider(gen_list[0], "generator", _build_prompt(state, step, state["step_state"][step["id"]]), gen_cfg)
        return gen_list[0], out, []
    def _gen(p):
        rc, out = run_provider(p, "generator", _build_prompt(state, step, state["step_state"][step["id"]]), gen_cfg)
        return {"provider": p, "rc": rc, "out": out}
    with futures.ThreadPoolExecutor(max_workers=max(1, max_parallel)) as ex:
        cands = list(ex.map(_gen, gen_list))
    cands.sort(key=lambda c: gen_list.index(c["provider"]))   # evaluate in generation order = deterministic
    judged, winner, product = [], None, cands[0]["out"]
    jver = ver[0] if isinstance(ver, list) else ver            # the judge is the first verifier provider
    for c in cands:
        _, jout = run_provider(jver, "verifier", _build_verify_prompt(state, step, c["out"]),
                               ver_cfg, persona="judge")
        ok = _verdict_ok(jout)
        judged.append({"provider": c["provider"], "ok": ok, "note": _excerpt(jout)})
        if ok and winner is None:
            winner, product = c["provider"], c["out"]
    return winner, product, judged


def _execute_step(state: dict, step: dict, st: dict, gen_list: list[str], ver: str,
                  cfg: dict, max_parallel: int, quorum: str, log) -> None:
    """Execute one step: generate (separate process; judge-panel capable) -> record gate evidence (checks or parallel verification)."""
    winner, out, judged = _generate(state, step, gen_list, ver, cfg, max_parallel)
    with _HIST_LOCK:
        state["history"].append({"action": "EXEC", "step": step["id"],
                                 "provider": winner or gen_list[0], "out": out[:200]})
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
        # judge-panel: the judge selects, so its gate judgment is adopted (pass if there is a winner)
        st["verdicts"].append({"by": f"{ver_label}:judge-panel", "ok": winner is not None,
                               "note": "winner=" + str(winner)})
        return
    # Lens verification = N independent reviewers in parallel processes (grader != generator)
    # Per-step `verifier_model:` is injected into a copy of cfg (independent of the generator side)
    v_cfg = {**cfg, "model": step["verifier_model"] or step.get("model") or cfg.get("model")} \
            if (step.get("verifier_model") or step.get("model")) else cfg
    personas = step["personas"] or ["independent"]
    results = run_verifiers_parallel(ver, _build_verify_prompt(state, step, out),
                                     personas, v_cfg, max_parallel)
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
    if any(s["needs"] for s in state["steps"]):
        final = run_dag(state, sp, gen_list, ver, cfg, max_steps, quiet, max_parallel, quorum)
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
        print("[ERROR] --provider <name> is required (rig|claude|codex|ollama|lmstudio|cmd|mock)")
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

