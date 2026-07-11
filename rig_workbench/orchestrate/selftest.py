"""orchestrate selftest: cmd_selftest (split from scripts/orchestrate.py)."""

import sys
import json
import pathlib
import subprocess

from . import config
from . import queueing
from .config import DEFAULT_K
from .recipes import (auto_orchestrate, git_diff_lines, load_manifest,
                      resolve_effective, resolve_plan_json)
from .runstate import classify_failure, compute_next, new_state, save_state
from .providers import (_OPENAI_BASE, _judge_output, _parse_criteria, _verdict_ok,
                        build_argv, discover_models,
                        parse_step_model_spec, resolve_http_model, run_loop,
                        run_provider, unknown_step_model_ids)
from .queueing import (_local_load, _queue_relabel_args, queue_add, queue_list,
                       queue_set_status)
from .isolate import setup_isolation, teardown_isolation
from .graph import build_brick_graph
from .commands import _current_running, cmd_party, cmd_resume, cmd_runs

# ── Determinism selftest ──────────────────────────────────────────────────────
def _drive(steps, script):
    """Advance a run using steps and a script of (action_kind, payload); return the transition trace and final state."""
    state = new_state("selftest", steps, None)
    trace = []
    for kind, payload in script:
        if kind == "next":
            a, _ = compute_next(state)
            trace.append(a)
        elif kind == "check":
            step, st = _current_running(state)
            st["checks"] = [{"cmd": c, "ok": payload} for c in step["checks"]]
        elif kind == "verdict":
            step, st = _current_running(state)
            st["verdicts"].append({"by": payload[0], "ok": payload[1], "note": ""})
    return trace, state

def cmd_selftest(_args):
    # Divert telemetry to a temp file (selftest must not pollute the caller cwd's .rig/runs.jsonl)
    _orig_runs = config.RUNS_PATH
    import tempfile
    config.RUNS_PATH = pathlib.Path(tempfile.gettempdir()) / "rig_runs_selftest.jsonl"
    config.RUNS_PATH.unlink(missing_ok=True)

    import io
    import contextlib

    def s(**k):
        return {"id": k["id"], "instruction": "x", "gate": k.get("gate"),
                "pattern": k.get("pattern"), "personas": k.get("personas", []),
                "needs": k.get("needs", []),
                "acceptance": [], "checks": k.get("checks", []),
                "max_retries": k.get("max_retries", DEFAULT_K), "output_contract": None}

    # Scenario A: happy path (no-gate → checks pass → verdict pass → DONE)
    stepsA = [s(id="design"),
              s(id="verify", gate="acceptance-gate", checks=["true"]),
              s(id="review", gate="review-gate")]
    scriptA = [("next", None),                       # START design
               ("next", None),                       # design no-gate → ADVANCE verify
               ("next", None),                       # START verify
               ("check", True), ("next", None),      # verify checks ok → ADVANCE review
               ("next", None),                       # START review
               ("verdict", ("reviewer", True)), ("next", None)]  # review pass → DONE
    expectA = ["START", "ADVANCE", "START", "ADVANCE", "START", "DONE"]
    tA1, stA1 = _drive(stepsA, scriptA)
    tA2, stA2 = _drive(stepsA, scriptA)

    # Scenario B: failure path (checks fail → retry → re-START → fail → ESCALATE)
    stepsB = [s(id="verify", gate="acceptance-gate", checks=["false"], max_retries=2)]
    scriptB = [("next", None),                       # START
               ("check", False), ("next", None),     # fail → RETRY (back to pending)
               ("next", None),                       # re-START
               ("check", False), ("next", None)]      # fail (try2/2) → ESCALATE
    expectB = ["START", "RETRY", "START", "ESCALATE"]
    tB, _ = _drive(stepsB, scriptB)

    # Scenario C: self-grading block (by=self → BLOCKED)
    stepsC = [s(id="review", gate="review-gate")]
    scriptC = [("next", None), ("verdict", ("self", True)), ("next", None)]
    expectC = ["START", "BLOCKED"]
    tC, _ = _drive(stepsC, scriptC)

    # Scenario D: external runner (mock provider; separate-process execution + independent verification)
    stepsD = [s(id="implement"),
              s(id="review", gate="review-gate")]
    stateD = new_state("selftest-run", stepsD, "demo")
    finalD = run_loop(stateD, None, "mock", "mock", {}, max_steps=20, quiet=True)
    rev_verdicts = stateD["step_state"]["review"]["verdicts"]
    d_indep = bool(rev_verdicts) and rev_verdicts[0]["by"] == "mock:independent" and rev_verdicts[0]["ok"]
    d_exec = any(h["action"] == "EXEC" for h in stateD["history"])

    # Scenario E: parallel verification fan-out (3 independent reviewers in concurrent processes, all PASS)
    stepsE = [s(id="review", gate="review-gate", pattern="parallel-fanout",
                personas=["correctness", "repro", "security"])]
    stateE1 = new_state("par", stepsE, None)
    finalE1 = run_loop(stateE1, None, "mock", "mock", {}, 20, quiet=True, max_parallel=3)
    vE1 = sorted(v["by"] for v in stateE1["step_state"]["review"]["verdicts"])
    stateE2 = new_state("par", stepsE, None)
    run_loop(stateE2, None, "mock", "mock", {}, 20, quiet=True, max_parallel=3)
    vE2 = sorted(v["by"] for v in stateE2["step_state"]["review"]["verdicts"])
    expectE = ["mock:correctness", "mock:repro", "mock:security"]

    # Scenario F: one FAIL. quorum=majority passes; quorum=all fails the gate → ESCALATE.
    stepsF = [s(id="review", gate="review-gate", personas=["a", "b", "fail-c"], max_retries=2)]
    stateF = new_state("maj", stepsF, None)
    finalF = run_loop(stateF, None, "mock", "mock", {}, 20, quiet=True,
                      max_parallel=3, quorum="majority")  # 2/3 pass → DONE
    stateG = new_state("all", stepsF, None)
    finalG = run_loop(stateG, None, "mock", "mock", {}, 20, quiet=True,
                      max_parallel=3, quorum="all")        # 1 FAIL → retry → ESCALATE

    # Scenario I: judge-panel (multiple generators in parallel → judge picks the winner deterministically)
    stepsI = [s(id="impl", gate="acceptance-gate")]
    stateI1 = new_state("panel", stepsI, None)
    finalI1 = run_loop(stateI1, None, "mock", "mock", {}, 20, quiet=True,
                       max_parallel=3, generators=["mock", "mock", "mock"])
    vI1 = stateI1["step_state"]["impl"]["verdicts"]
    i_panel = bool(vI1) and vI1[0]["by"] == "mock:judge-panel" and vI1[0]["ok"]
    stateI2 = new_state("panel", stepsI, None)
    run_loop(stateI2, None, "mock", "mock", {}, 20, quiet=True,
             max_parallel=3, generators=["mock", "mock", "mock"])
    i_det = json.dumps(stateI1["step_state"], sort_keys=True) == json.dumps(stateI2["step_state"], sort_keys=True)

    # Scenario J: step-DAG parallelism (a,b are independent → same wave; c needs:[a,b] → next wave)
    stepsJ = [s(id="a"), s(id="b"),
              {"id": "c", "instruction": "x", "gate": None, "pattern": None, "personas": [],
               "needs": ["a", "b"], "acceptance": [], "checks": [], "max_retries": 2,
               "output_contract": None}]
    stateJ = new_state("dag", stepsJ, None)
    finalJ = run_loop(stateJ, None, "mock", "mock", {}, 20, quiet=True, max_parallel=2)
    j_waves = stateJ.get("waves")

    ok = True
    def report(name, got, exp, det=""):
        nonlocal ok
        good = (got == exp)
        ok = ok and good
        print(f"  [{'OK ' if good else 'NG '}] {name}: {got}{'' if good else f'  != {exp}'} {det}")

    print("## orchestrate selftest (proof of determinism)")
    report("A happy-path transition trace", tA1, expectA)
    report("A repeat run is identical (determinism)", tA2, tA1, "same input → same transitions")
    report("A final states are identical", json.dumps(stA1, sort_keys=True), json.dumps(stA2, sort_keys=True))
    report("A done=True", stA1["done"], True)
    report("B fail → retry → escalate", tB, expectB)
    report("C self-grading blocked", tC, expectC)
    report("D external runner self-drives to DONE", finalD, "DONE")
    report("D steps executed in a separate process (EXEC)", d_exec, True)
    report("D verification is independent (by=mock:independent)", d_indep, True)
    report("E 3 parallel verifiers reach DONE", finalE1, "DONE")
    report("E 3 independent votes recorded", vE1, expectE)
    report("E parallel yet deterministic (regardless of completion order)", vE2, vE1, "same set")
    report("F majority passes despite 1 FAIL → DONE", finalF, "DONE")
    report("G all fails on 1 FAIL → ESCALATE", finalG, "ESCALATE")
    # H: the rig provider launches each step as a rig harness (invokes rig by name)
    argv_gen = build_argv("rig", "generator", "step X", {})
    argv_ver = build_argv("rig", "verifier", "step X", {})
    report("H rig provider launches via claude", argv_gen[0], "claude")
    report("H rig invoked by name (generation)", "`rig` skill" in argv_gen[2], True)
    report("H rig verification uses the VERDICT contract", "VERDICT" in argv_ver[2] and "`rig` skill" in argv_ver[2], True)
    report("I judge-panel picks a winner and reaches DONE", finalI1, "DONE")
    report("I verdict comes from the judge-panel", i_panel, True)
    report("I judge-panel is deterministic", i_det, True)
    report("J DAG: independent a,b share a wave → c is next wave", j_waves, [["a", "b"], ["c"]])
    report("J DAG reaches DONE", finalJ, "DONE")
    # K: auto-enable (--orchestrate auto ON via checks/needs/manifest)
    report("K checks declaration → auto ON", auto_orchestrate([s(id="v", checks=["true"])])[0], True)
    report("K needs declaration → auto ON", auto_orchestrate([s(id="a"), s(id="b", needs=["a"])])[0], True)
    report("K no declaration → off", auto_orchestrate([s(id="x")])[0], False)
    report("K manifest default → auto ON", auto_orchestrate([s(id="x")], manifest_default=True)[0], True)
    # L: local LLMs (ollama/lmstudio) are wired as OpenAI-compatible HTTP providers
    report("L ollama/lmstudio are wired", set(_OPENAI_BASE) == {"lmstudio", "ollama"}, True)
    rc_l, _ = run_provider("lmstudio", "verifier", "x", {"base_url": "http://127.0.0.1:1/v1", "timeout": 2})
    report("L no crash without a server; rc!=0", rc_l != 0, True)
    # M: dynamic model discovery (--auto-model) — graceful, no crash, even without a server
    found = discover_models({"base_url": "http://127.0.0.1:1/v1", "timeout": 2})
    report("M discover returns every provider", set(found) >= {"ollama", "lmstudio", "claude", "codex", "rig"}, True)
    report("M absent server → reachable=False", found["ollama"]["reachable"], False)
    report("M auto-model resolution falls back to the default",
           resolve_http_model("ollama", {"auto_model": True, "base_url": "http://127.0.0.1:1/v1", "timeout": 2}),
           "llama3.1")
    report("M explicit --model wins", resolve_http_model("ollama", {"auto_model": True, "model": "qwen2.5"}), "qwen2.5")
    # N: probe foundations (codex command is correct; VERDICT is parsed from verifier output)
    report("N probe: codex verifier enforces the read-only sandbox",
           build_argv("codex", "verifier", "P", {}),
           ["codex", "exec", "--skip-git-repo-check", "--sandbox", "read-only", "P"])
    report("N probe: codex generator gets the workspace-write sandbox",
           build_argv("codex", "generator", "P", {}),
           ["codex", "exec", "--skip-git-repo-check", "--sandbox", "workspace-write", "P"])
    report("N probe: claude verifier enforces allowedTools",
           build_argv("claude", "verifier", "P", {}),
           ["claude", "-p", "P", "--output-format", "text", "--allowedTools", "Read,Grep,Glob"])
    report("N probe: claude generator has no permission flags",
           build_argv("claude", "generator", "P", {}), ["claude", "-p", "P", "--output-format", "text"])
    _, out_n = run_provider("mock", "verifier", "x", {})
    report("N probe: verifier output contains VERDICT", "VERDICT" in out_n, True)
    # O: task queue (local backend: add → list → mock go → note/retry/done-exclusion → github graceful without CLI)
    _orig_qp = queueing.QUEUE_PATH
    import tempfile
    queueing.QUEUE_PATH = pathlib.Path(tempfile.gettempdir()) / "rig_queue_selftest.json"
    queueing.QUEUE_PATH.unlink(missing_ok=True)
    queue_add("local", "task-A", {})
    queue_add("local", "task-B", {})
    q_items = queue_list("local", {})
    for it in q_items:                          # go with mock (generate → verify → done)
        _, vout = run_provider("mock", "verifier", "x", {}, persona="queue")
        queue_set_status("local", it["id"], "done" if "VERDICT: PASS" in vout else "failed", "", {})
    q_done_raw = [it for it in _local_load()["items"] if it["status"] == "done"]  # confirm in the raw store
    q_done_in_list = [it for it in queue_list("local", {}) if it["status"] == "done"]  # #215: absent
    note_text = "❌ rig: verification FAIL (mock→mock)"
    target_id = q_items[0]["id"]
    queue_set_status("local", target_id, "failed", note_text, {})  # explicit failed+note for #214
    q_note = next(it for it in queue_list("local", {}) if it["id"] == target_id)
    queue_set_status("local", target_id, "queued", "", {})         # #213: same call as retry
    q_retried = next(it for it in queue_list("local", {}) if it["id"] == target_id)
    relabel_failed = _queue_relabel_args("failed")
    relabel_removes = [relabel_failed[i + 1] for i in range(len(relabel_failed) - 1)
                        if relabel_failed[i] == "--remove-label"]
    gh_item = queue_add("github", "t", {})      # gh absent → error (no crash)
    queueing.QUEUE_PATH.unlink(missing_ok=True)
    queueing.QUEUE_PATH = _orig_qp
    report("O queue: 2 items added then listed", len(q_items), 2)
    report("O queue: mock go marks all done (raw store check)", len(q_done_raw), 2)
    report("O queue: done items absent from queue_list (local) (#215)", len(q_done_in_list), 0)
    report("O queue: failed item's note shows up in list (#214)", q_note.get("note"), note_text)
    report("O queue: retry returns item to queued (#213)", q_retried["status"], "queued")
    report("O queue: retry clears the note (#213)", q_retried.get("note"), "")
    report("O queue: running→failed removes rig-running (#223)",
           "rig-running" in relabel_removes, True)
    report("O github backend is graceful without the CLI (error)", gh_item["status"], "error")
    # P: run telemetry (every run_loop scenario appends one line to .rig/runs.jsonl)
    p_lines = []
    if config.RUNS_PATH.exists():
        p_lines = [json.loads(line) for line in config.RUNS_PATH.read_text(encoding="utf-8").splitlines() if line.strip()]
    p_first = p_lines[0] if p_lines else {}
    config.RUNS_PATH.unlink(missing_ok=True)
    config.RUNS_PATH = _orig_runs
    report("P telemetry: 8 run_loop executions recorded", len(p_lines), 8)
    report("P telemetry: final and recipe recorded",
           (p_first.get("final"), p_first.get("recipe")), ("DONE", "selftest-run"))
    p_verdicts = sorted({v["by"] for r in p_lines for st in r.get("steps", [])
                         for v in st.get("verdicts", [])})
    report("P telemetry: verifier votes (by) recorded", "mock:independent" in p_verdicts
           and "mock:correctness" in p_verdicts, True)
    # Q: golden verification of the RESOLVE reference implementation (extends merge, remove, origin, fixed badge order, steps: field)
    qdir = pathlib.Path(tempfile.mkdtemp(prefix="rig_resolve_selftest_"))
    (qdir / "base-flow.md").write_text(
        "---\nname: base-flow\ndescription: t\nscope: shipped\nautonomy: interactive\n"
        "steps:\n  - id: intake\n    instruction: intake\n"
        "  - id: design\n    instruction: design\n    condition: \"--design or size L+\"\n"
        "  - id: implement\n    instruction: implement\n"
        "  - id: verify\n    instruction: verify\n    gate: acceptance-gate\n"
        "    acceptance: [\"ok\"]\n---\n", encoding="utf-8")
    (qdir / "child-flow.md").write_text(
        "---\nname: child-flow\ndescription: t\nscope: project\nautonomy: autonomous\n"
        "extends: base-flow\ntdd: true\nverify_findings: true\n"
        "steps:\n  - id: design\n    remove: true\n"
        "  - id: verify\n    instruction: verify\n    gate: acceptance-gate\n    checks: [\"true\"]\n"
        "  - id: pr\n    instruction: pr\n---\n", encoding="utf-8")
    q_base = resolve_plan_json(qdir / "base-flow.md")
    q1 = resolve_plan_json(qdir / "child-flow.md")
    q2 = resolve_plan_json(qdir / "child-flow.md")
    report("Q resolve: parent's steps: field (condition abbreviation)",
           q_base["steps_field"], "intake, design?[--design|L+], implement, verify")
    report("Q resolve: finalized step list after extends (remove/override/added)",
           q1["steps_field"], "intake, implement, verify, pr")
    report("Q resolve: origin classification",
           [s["origin"] for s in q1["steps"]], ["inherited", "inherited", "override", "added"])
    report("Q resolve: fixed badge order (tdd→gated→orchestrate(auto)→autonomous→verify-findings)",
           q1["badges"], ["tdd", "gated", "orchestrate(auto)", "autonomous", "verify-findings"])
    report("Q resolve: deterministic (same input → same JSON)",
           json.dumps(q1, sort_keys=True), json.dumps(q2, sort_keys=True))
    # R: golden verification of RESOLVE phase 2 (condition evaluation, size classing, slicing, flag precedence)
    rf = config.RECIPES / "release-flow.md"
    r_s = resolve_effective(rf, [], diff_lines=50)                       # size S: design/review OFF
    r_flag = resolve_effective(rf, ["--design"], diff_lines=50)          # a flag resolves the condition
    r_l = resolve_effective(rf, [], diff_lines=300)                      # size L: design/review ON
    r_only_off = resolve_effective(rf, ["--only", "review"], diff_lines=50)   # case B: condition-OFF
    r_only_on = resolve_effective(rf, ["--only", "review", "--review"], diff_lines=50)
    r_range = resolve_effective(rf, ["--from", "implement", "--to", "verify"], diff_lines=50)
    r_rev = resolve_effective(rf, ["--from", "verify", "--to", "implement"], diff_lines=50)
    r_skipwin = resolve_effective(rf, ["--design", "--skip", "design"], diff_lines=50)
    r_onlyskip = resolve_effective(rf, ["--only", "verify", "--skip", "design"], diff_lines=50)
    r_gate = resolve_effective(rf, ["--skip", "verify"], diff_lines=50)  # acceptance-gate skip WARN
    r_typo = resolve_effective(rf, ["--only", "verifi"], diff_lines=50)  # case A: Levenshtein suggestions
    r_det = (json.dumps(resolve_effective(rf, ["--design"], diff_lines=50), sort_keys=True)
             == json.dumps(resolve_effective(rf, ["--design"], diff_lines=50), sort_keys=True))
    report("R size S: design/review are condition-OFF",
           r_s["effective_steps"], ["intake", "implement", "verify", "pr", "merge"])
    report("R --design flag resolves the condition",
           r_flag["effective_steps"], ["intake", "design", "implement", "verify", "pr", "merge"])
    report("R size L+: design/review auto ON",
           r_l["effective_steps"], ["intake", "design", "implement", "verify", "review", "pr", "merge"])
    report("R --only on a condition-OFF step errors (case B)",
           any("condition" in e for e in r_only_off["errors"]), True)
    report("R --only + enabling flag runs it solo", r_only_on["effective_steps"], ["review"])
    report("R --from/--to range slicing", r_range["effective_steps"], ["implement", "verify"])
    report("R --from/--to in reverse order errors", any("order is reversed" in e for e in r_rev["errors"]), True)
    report("R explicit --skip beats explicit ON", "design" not in r_skipwin["effective_steps"], True)
    report("R --only ignores --skip with a WARN",
           any("--only wins; --skip ignored" in w for w in r_onlyskip["warnings"]), True)
    report("R --skip on an acceptance-gate step WARNs",
           any("acceptance-gate" in w for w in r_gate["warnings"]), True)
    report("R typos error with Levenshtein suggestions (case A)",
           any("did you mean: verify" in e for e in r_typo["errors"]), True)
    report("R resolve_effective is deterministic", r_det, True)
    # S: phase 3 (manifest thresholds applied; git diff auto-measurement is graceful)
    s_manifest = {"size_thresholds": {"S_max": 10, "M_max": 20, "L_max": 40}}
    r_s_th = resolve_effective(rf, [], diff_lines=30, manifest=s_manifest)  # 30 lines > M_max:20 → L
    r_s_orch = resolve_effective(rf, [], diff_lines=5, manifest={"default_orchestrate": True})
    report("S manifest size_thresholds affect size classing (30 lines → L turns design ON)",
           "design" in r_s_th["effective_steps"], True)
    report("S manifest default_orchestrate enables orchestrate auto",
           r_s_orch["mode"]["orchestrate"].startswith("auto"), True)
    report("S git_diff_lines never crashes; returns int|None", isinstance(git_diff_lines(), (int, type(None))), True)
    report("S load_manifest always returns a dict", isinstance(load_manifest(), dict), True)
    # V: party roster screen (party) renders the RPG sheet from runs/drill
    _orig_drill = config.DRILL_PATH
    config.RUNS_PATH = pathlib.Path(tempfile.gettempdir()) / "rig_party_runs.jsonl"
    config.DRILL_PATH = pathlib.Path(tempfile.gettempdir()) / "rig_party_drill.jsonl"
    config.RUNS_PATH.write_text(json.dumps({
        "ts": "t", "recipe": "review-only", "backend": "orchestrate", "final": "DONE",
        "steps_total": 1, "steps_passed": 1, "retries": 0, "escalated_at": None,
        "steps": [{"id": "review", "status": "passed", "retries": 0,
                   "verdicts": [{"by": "mock:security-reviewer", "ok": False}]}]}) + "\n",
        encoding="utf-8")
    config.DRILL_PATH.write_text(json.dumps({
        "ts": "t", "scores": [{"reviewer": "security-reviewer", "detected": 2,
                               "seeded": 2, "false_positives": 0}]}) + "\n", encoding="utf-8")
    buf_v = io.StringIO()
    with contextlib.redirect_stdout(buf_v):
        cmd_party([])
    v_out = buf_v.getvalue()
    config.RUNS_PATH.unlink(missing_ok=True)
    config.DRILL_PATH.unlink(missing_ok=True)
    config.RUNS_PATH = _orig_runs
    config.DRILL_PATH = _orig_drill
    report("V party: renders Lv and achievements", "Lv.1" in v_out and "🏆 First DONE" in v_out, True)
    report("V party: reflects drill detection rate and sorties/REJECT",
           "detection 100% (drill 2/2)" in v_out and "sorties   1 / REJECT 1" in v_out, True)
    # U: mixed-model quorum (same persona across multiple providers; votes are provider:persona)
    stepsU = [s(id="review", gate="review-gate", personas=["x"])]
    stateU1 = new_state("mq", stepsU, None)
    finalU1 = run_loop(stateU1, None, "mock", ["mock", "mock"], {}, 20, quiet=True, max_parallel=2)
    vU1 = stateU1["step_state"]["review"]["verdicts"]
    stateU2 = new_state("mq", stepsU, None)
    run_loop(stateU2, None, "mock", ["mock", "mock"], {}, 20, quiet=True, max_parallel=2)
    u_det = (json.dumps(stateU1["step_state"], sort_keys=True)
             == json.dumps(stateU2["step_state"], sort_keys=True))
    report("U model-quorum: 2 providers x 1 persona = 2 votes", len(vU1), 2)
    report("U model-quorum: vote by is provider:persona", all(v["by"] == "mock:x" for v in vU1), True)
    report("U model-quorum: DONE + deterministic", (finalU1, u_det), ("DONE", True))
    # T: gap prescriptions (repeated escalations at the same step → --discover suggestion)
    config.RUNS_PATH = pathlib.Path(tempfile.gettempdir()) / "rig_runs_gap_selftest.jsonl"
    config.RUNS_PATH.unlink(missing_ok=True)
    with config.RUNS_PATH.open("w", encoding="utf-8") as f:
        for esc in ("verify", "verify", None):
            f.write(json.dumps({"ts": "t", "recipe": "release-flow", "backend": "orchestrate",
                                "final": "ESCALATE" if esc else "DONE", "steps_total": 1,
                                "steps_passed": 0 if esc else 1, "retries": 2 if esc else 0,
                                "escalated_at": esc, "steps": []}) + "\n")
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        cmd_runs([])
    t_out = buf.getvalue()
    config.RUNS_PATH.unlink(missing_ok=True)
    config.RUNS_PATH = _orig_runs
    report("T gap: 2 escalations produce a prescription", "Gap prescriptions" in t_out and "--discover" in t_out, True)
    report("T gap: pinpoints the step", "release-flow / verify: escalated 2 times" in t_out, True)
    for f in qdir.iterdir():
        f.unlink()
    qdir.rmdir()
    # ── Scenario X: worktree isolation (deterministic setup/teardown cleanup rules) ──
    import tempfile as _tmp
    _orig_cwd = config.INVOCATION_CWD
    xroot = pathlib.Path(_tmp.mkdtemp(prefix="rig-selftest-iso-"))
    def _g(*a, cwd=None):
        return subprocess.run(["git", "-C", str(cwd or xroot)] + list(a),
                              capture_output=True, text=True)
    _g("init", "-q", "-b", "main")
    _g("config", "user.email", "selftest@rig")
    _g("config", "user.name", "rig-selftest")
    (xroot / "base.txt").write_text("base\n")
    _g("add", ".")
    _g("commit", "-q", "-m", "base")
    config.INVOCATION_CWD = xroot
    # X-1: DONE with commits and clean → ff-merge and remove
    iso1 = setup_isolation("demo")
    report("X isolate: worktree and branch created",
           pathlib.Path(iso1["dir"]).is_dir() and iso1["branch"].startswith("rig/run-demo-"), True)
    (pathlib.Path(iso1["dir"]) / "made.txt").write_text("x\n")
    _g("add", ".", cwd=iso1["dir"])
    _g("commit", "-q", "-m", "work", cwd=iso1["dir"])
    report("X isolate: DONE+commit+clean ff-merges (merged)", teardown_isolation(iso1, "DONE"), "merged")
    report("X isolate: after merge the product is in root", (xroot / "made.txt").exists(), True)
    report("X isolate: worktree gone after removal", pathlib.Path(iso1["dir"]).exists(), False)
    # X-2: DONE but dirty → preserved
    iso2 = setup_isolation("demo")
    (pathlib.Path(iso2["dir"]) / "wip.txt").write_text("wip\n")
    report("X isolate: dirty is preserved (kept)", teardown_isolation(iso2, "DONE"), "kept")
    report("X isolate: worktree remains when preserved", pathlib.Path(iso2["dir"]).is_dir(), True)
    # X-3: DONE with no changes → removal only
    iso3 = setup_isolation("demo")
    report("X isolate: no changes → removal only (clean-removed)", teardown_isolation(iso3, "DONE"), "clean-removed")
    # X-4: ESCALATE → preserved
    iso4 = setup_isolation("demo")
    report("X isolate: unmet (ESCALATE) is preserved (kept)", teardown_isolation(iso4, "ESCALATE"), "kept")
    config.INVOCATION_CWD = _orig_cwd

    # ── Scenario W: brick graph (typed relation derivation; golden anchors) ──
    gW1 = build_brick_graph()
    gW2 = build_brick_graph()
    report("W graph: derivation is deterministic (same input → same graph)", gW1 == gW2, True)
    eW = {(e["from"], e["rel"], e["to"]) for e in gW1["edges"]}
    report("W graph: persona inject becomes an injects edge",
           ("persona:security-reviewer", "injects", "wiki:appsec-checklist") in eW, True)
    report("W graph: recipe steps become gated-by/uses-persona edges",
           ("recipe:review-only", "gated-by", "pattern:review-gate") in eW
           and ("recipe:review-only", "uses-persona", "persona:security-reviewer") in eW, True)
    report("W graph: wiki cross-links become links-to edges",
           ("wiki:appsec-checklist", "links-to", "wiki:injection-patterns") in eW, True)
    report("W graph: agent absorbs the -reviewer suffix and mirrors the persona",
           ("agent:lazy-senior-reviewer", "mirrors", "persona:lazy-senior") in eW, True)
    report("W graph: zero unresolved edges in the shipped tier",
           sum(1 for e in gW1["edges"] if not e["resolved"]), 0)

    # ── Scenario Z: runtime per-step model assignment (--step-model; #293) ──
    # Precedence: runtime --step-model > recipe frontmatter `model:` > global --model;
    # the actually-used model per step is recorded in run-state; unknown step ids are rejected.
    config.RUNS_PATH = pathlib.Path(tempfile.gettempdir()) / "rig_runs_stepmodel_selftest.jsonl"
    config.RUNS_PATH.unlink(missing_ok=True)
    stepsZ = [{**s(id="plan"), "model": "recipe-m"}, s(id="implement")]
    stateZ1 = new_state("stepmodel", stepsZ, None)
    finalZ1 = run_loop(stateZ1, None, "mock", "mock",
                       {"model": "global-m", "step_models": {"plan": "runtime-m"}}, 20, quiet=True)
    stateZ2 = new_state("stepmodel", stepsZ, None)
    run_loop(stateZ2, None, "mock", "mock", {"model": "global-m"}, 20, quiet=True)
    config.RUNS_PATH.unlink(missing_ok=True)
    config.RUNS_PATH = _orig_runs
    report("Z step-model: runtime override beats recipe model (recorded in run-state)",
           stateZ1["step_state"]["plan"].get("model"), "runtime-m")
    report("Z step-model: global --model is the fallback for unnamed steps",
           stateZ1["step_state"]["implement"].get("model"), "global-m")
    report("Z step-model: recipe model beats global --model when no override",
           stateZ2["step_state"]["plan"].get("model"), "recipe-m")
    report("Z step-model: overridden run still reaches DONE", finalZ1, "DONE")
    report("Z step-model: unknown step id is detected before execution",
           unknown_step_model_ids({"nope": "m", "plan": "m"}, stepsZ), ["nope"])
    report("Z step-model: malformed spec is rejected, valid spec parses",
           (parse_step_model_spec("plan"), parse_step_model_spec("plan=sonnet")),
           (None, ("plan", "sonnet")))

    # ── Scenario Y: judge hardening (evidence-first verdict / per-criterion lines / multi-PASS set) ──
    # Y-1: evidence-first review-verdict contract — the rationale quotes another verdict line,
    # yet the LAST 判定: line (contract-mandated final position) decides.
    y_ev = ("根拠:\n"
            "1. 過去レビューの引用 — a.py:1\n"
            "判定: REJECT\n"          # quoted verdict inside the rationale (must NOT decide)
            "2. 該当修正を確認 — b.py:2\n"
            "3. テスト追加を確認 — c.py:3\n"
            "判定: APPROVE\n確信度: 高")
    report("Y evidence-first: the LAST 判定 line wins over a quoted one", _verdict_ok(y_ev), True)
    y_machine = "evidence x — a.py:1\nthe report itself said \"VERDICT: FAIL\" once\nVERDICT: PASS"
    report("Y evidence-first: machine VERDICT prefers the final line", _verdict_ok(y_machine), True)
    # Y-2: per-criterion verdicts (tolerant parse; UNKNOWN escape; all-UNKNOWN+PASS fails closed)
    y_crit = ("reasoning — a.py:1\nCRITERION 1: PASS — a.py:1\n"
              "criterion 2: unknown - not observable\nVERDICT: PASS")
    ok_y, crit_y = _judge_output(y_crit)
    report("Y per-criterion lines parse (tolerant, UNKNOWN recorded)",
           (ok_y, [c["verdict"] for c in crit_y]), (True, ["PASS", "UNKNOWN"]))
    y_allunk = "reasoning\nCRITERION 1: UNKNOWN — n/a\nCRITERION 2: UNKNOWN — n/a\nVERDICT: PASS"
    report("Y all-UNKNOWN + VERDICT PASS fails closed", _judge_output(y_allunk)[0], False)
    report("Y old format (no CRITERION lines) keeps old behavior",
           _judge_output("short note\nVERDICT: PASS"), (True, []))
    # Y-3: mock end-to-end — criteria land in the verdict record; multi-PASS judge-panel
    # records order_sensitive + the pass-set (all candidates judged; first-in-list wins).
    config.RUNS_PATH = pathlib.Path(tempfile.gettempdir()) / "rig_runs_judge_selftest.jsonl"
    config.RUNS_PATH.unlink(missing_ok=True)
    stepsY = [s(id="review", gate="review-gate")]
    stateY = new_state("judge-harden", stepsY, None)
    finalY = run_loop(stateY, None, "mock", "mock", {}, 20, quiet=True)
    vY = stateY["step_state"]["review"]["verdicts"][0]
    stepsY2 = [s(id="impl", gate="acceptance-gate")]
    stateY2 = new_state("judge-multi", stepsY2, None)
    finalY2 = run_loop(stateY2, None, "mock", "mock", {}, 20, quiet=True,
                       max_parallel=2, generators=["mock", "mock"])
    vY2 = stateY2["step_state"]["impl"]["verdicts"][0]
    config.RUNS_PATH.unlink(missing_ok=True)
    config.RUNS_PATH = _orig_runs
    report("Y mock verifier's criteria are recorded in the verdict",
           (finalY, [c["verdict"] for c in vY.get("criteria", [])]), ("DONE", ["PASS"]))
    report("Y multi-PASS panel records order_sensitive + pass-set (deterministic winner)",
           (finalY2, vY2.get("order_sensitive"), vY2.get("pass_set")),
           ("DONE", True, ["mock", "mock"]))
    report("Y empty parse helper: no CRITERION lines → empty criteria", _parse_criteria("VERDICT: PASS"), [])

    # ── Scenario AA: verify-first resume ritual (re-verify machine checks before continuing) ──
    # A resumed run re-runs the current step's checks to confirm the world still matches the
    # recorded state: trivially-true check → digest + ADVANCE; a recorded pass that is now
    # false → "world drifted" → REFUSE to advance (exit non-zero). Deterministic (true/false).
    def _run_resume(state_dict):
        p = pathlib.Path(tempfile.mktemp(prefix="rig_resume_selftest_", suffix=".json"))
        save_state(state_dict, p)
        buf = io.StringIO()
        code = 0
        with contextlib.redirect_stdout(buf):
            try:
                cmd_resume([str(p)])
            except SystemExit as e:
                code = e.code or 0
        p.unlink(missing_ok=True)
        return buf.getvalue(), code

    stateAA1 = new_state("resume-ok", [s(id="verify", gate="acceptance-gate", checks=["true"]),
                                       s(id="review", gate="review-gate")], None)
    stateAA1["step_state"]["verify"]["status"] = "running"
    stateAA1["step_state"]["verify"]["checks"] = [{"cmd": "true", "ok": True}]
    outAA1, codeAA1 = _run_resume(stateAA1)
    stateAA2 = new_state("resume-drift", [s(id="verify", gate="acceptance-gate", checks=["false"])], None)
    stateAA2["step_state"]["verify"]["status"] = "running"
    stateAA2["step_state"]["verify"]["checks"] = [{"cmd": "false", "ok": True}]  # recorded as passing
    outAA2, codeAA2 = _run_resume(stateAA2)
    report("AA resume: digest reports recipe/cursor and re-verifies then ADVANCEs",
           ("cursor=0/2" in outAA1 and "re-verify" in outAA1 and "▶ ADVANCE" in outAA1, codeAA1),
           (True, 0))
    report("AA resume: world-drift (recorded pass now fails) refuses to advance (exit≠0)",
           ("WORLD DRIFTED" in outAA2 and "▶ ADVANCE" not in outAA2, codeAA2 != 0),
           (True, True))

    # ── Scenario FM: failure-mode taxonomy (deterministic classification from state) ──
    # classify_failure gives a reproducible MAST-style code from run-state alone:
    # a self-graded stop (BLOCKED) and a K-exhausted stop (ESCALATE) map to fixed codes;
    # a successful run carries no failure mode. See skills/rig/patterns/failure-taxonomy.md.
    _, stFM_self = _drive([s(id="review", gate="review-gate")],
                          [("next", None), ("verdict", ("self", True)), ("next", None)])
    _, stFM_kx = _drive([s(id="verify", gate="acceptance-gate", checks=["false"], max_retries=2)],
                        [("next", None), ("check", False), ("next", None),
                         ("next", None), ("check", False), ("next", None)])
    _, stFM_ok = _drive(stepsA, scriptA)
    report("FM classify: self-graded stop → verification:self-grading",
           classify_failure(stFM_self), "verification:self-grading")
    report("FM classify: K-exhausted checks stop → verification:incorrect-implementation",
           classify_failure(stFM_kx), "verification:incorrect-implementation")
    report("FM classify: successful run has no failure mode", classify_failure(stFM_ok), None)

    print("\n" + ("PASS: the deterministic orchestrator is healthy" if ok else "FAIL: selftest mismatch"))
    sys.exit(0 if ok else 1)
