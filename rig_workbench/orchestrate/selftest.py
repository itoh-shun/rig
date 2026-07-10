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
from .runstate import compute_next, new_state
from .providers import (_OPENAI_BASE, build_argv, discover_models,
                        resolve_http_model, run_loop, run_provider)
from .queueing import (_local_load, _queue_relabel_args, queue_add, queue_list,
                       queue_set_status)
from .isolate import setup_isolation, teardown_isolation
from .graph import build_brick_graph
from .commands import _current_running, cmd_party, cmd_runs

# ── 決定論セルフテスト ────────────────────────────────────────────────────────
def _drive(steps, script):
    """steps と (action_kind, payload) のスクリプトで run を進め、遷移列と最終状態を返す。"""
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
    # telemetry を一時ファイルへ退避（selftest が呼び出し元 cwd の .rig/runs.jsonl を汚さない）
    _orig_runs = config.RUNS_PATH
    import tempfile
    config.RUNS_PATH = pathlib.Path(tempfile.gettempdir()) / "rig_runs_selftest.jsonl"
    config.RUNS_PATH.unlink(missing_ok=True)

    import io, contextlib

    s = lambda **k: {"id": k["id"], "instruction": "x", "gate": k.get("gate"),
                     "pattern": k.get("pattern"), "personas": k.get("personas", []),
                     "needs": k.get("needs", []),
                     "acceptance": [], "checks": k.get("checks", []),
                     "max_retries": k.get("max_retries", DEFAULT_K), "output_contract": None}

    # シナリオ A: 正常系（no-gate → checks pass → verdict pass → DONE）
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

    # シナリオ B: 失敗系（checks fail → retry → 再START → fail → ESCALATE）
    stepsB = [s(id="verify", gate="acceptance-gate", checks=["false"], max_retries=2)]
    scriptB = [("next", None),                       # START
               ("check", False), ("next", None),     # fail → RETRY（pending に戻る）
               ("next", None),                       # 再 START
               ("check", False), ("next", None)]      # fail（try2/2）→ ESCALATE
    expectB = ["START", "RETRY", "START", "ESCALATE"]
    tB, _ = _drive(stepsB, scriptB)

    # シナリオ C: 自己採点ブロック（by=self → BLOCKED）
    stepsC = [s(id="review", gate="review-gate")]
    scriptC = [("next", None), ("verdict", ("self", True)), ("next", None)]
    expectC = ["START", "BLOCKED"]
    tC, _ = _drive(stepsC, scriptC)

    # シナリオ D: 外部ランナー（mock プロバイダ・別プロセス実行＋独立検証）
    stepsD = [s(id="implement"),
              s(id="review", gate="review-gate")]
    stateD = new_state("selftest-run", stepsD, "デモ")
    finalD = run_loop(stateD, None, "mock", "mock", {}, max_steps=20, quiet=True)
    rev_verdicts = stateD["step_state"]["review"]["verdicts"]
    d_indep = bool(rev_verdicts) and rev_verdicts[0]["by"] == "mock:independent" and rev_verdicts[0]["ok"]
    d_exec = any(h["action"] == "EXEC" for h in stateD["history"])

    # シナリオ E: 並列検証ファンアウト（3 人の独立レビュアーを同時プロセス・全員 PASS）
    stepsE = [s(id="review", gate="review-gate", pattern="parallel-fanout",
                personas=["correctness", "repro", "security"])]
    stateE1 = new_state("par", stepsE, None)
    finalE1 = run_loop(stateE1, None, "mock", "mock", {}, 20, quiet=True, max_parallel=3)
    vE1 = sorted(v["by"] for v in stateE1["step_state"]["review"]["verdicts"])
    stateE2 = new_state("par", stepsE, None)
    run_loop(stateE2, None, "mock", "mock", {}, 20, quiet=True, max_parallel=3)
    vE2 = sorted(v["by"] for v in stateE2["step_state"]["review"]["verdicts"])
    expectE = ["mock:correctness", "mock:repro", "mock:security"]

    # シナリオ F: 1 人 FAIL。quorum=majority は可決、quorum=all はゲート不合格→ESCALATE。
    stepsF = [s(id="review", gate="review-gate", personas=["a", "b", "fail-c"], max_retries=2)]
    stateF = new_state("maj", stepsF, None)
    finalF = run_loop(stateF, None, "mock", "mock", {}, 20, quiet=True,
                      max_parallel=3, quorum="majority")  # 2/3 pass → DONE
    stateG = new_state("all", stepsF, None)
    finalG = run_loop(stateG, None, "mock", "mock", {}, 20, quiet=True,
                      max_parallel=3, quorum="all")        # 1 FAIL → retry → ESCALATE

    # シナリオ I: judge-panel（複数 generator を並列生成→judge が勝者を決定論選択）
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

    # シナリオ J: step-DAG 並列（a,b は独立→同 wave／c は needs:[a,b]→次 wave）
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

    print("## orchestrate selftest（決定論の証明）")
    report("A 正常系の遷移列", tA1, expectA)
    report("A 反復で同一(決定論)", tA2, tA1, "同入力→同遷移")
    report("A 最終状態が同一", json.dumps(stA1, sort_keys=True), json.dumps(stA2, sort_keys=True))
    report("A done=True", stA1["done"], True)
    report("B 失敗→リトライ→エスカレーション", tB, expectB)
    report("C 自己採点ブロック", tC, expectC)
    report("D 外部ランナーが DONE まで自走", finalD, "DONE")
    report("D 別プロセスで step を実行(EXEC)", d_exec, True)
    report("D 検証が独立(by=mock:independent)", d_indep, True)
    report("E 並列3人で DONE", finalE1, "DONE")
    report("E 3人分の独立票を記録", vE1, expectE)
    report("E 並列でも決定論(完了順に依らず)", vE2, vE1, "同集合")
    report("F majority は1人FAILでも可決→DONE", finalF, "DONE")
    report("G all は1人FAILで不合格→ESCALATE", finalG, "ESCALATE")
    # H: rig プロバイダは各 step を rig ハーネスとして起動（rig を名前で呼ぶ）
    argv_gen = build_argv("rig", "generator", "step X", {})
    argv_ver = build_argv("rig", "verifier", "step X", {})
    report("H rig provider は claude で起動", argv_gen[0], "claude")
    report("H rig を名前で呼ぶ(生成)", "`rig` skill" in argv_gen[2], True)
    report("H rig 検証は VERDICT 契約", "VERDICT" in argv_ver[2] and "`rig` skill" in argv_ver[2], True)
    report("I judge-panel が勝者を選び DONE", finalI1, "DONE")
    report("I 判定は judge-panel 由来", i_panel, True)
    report("I judge-panel は決定論", i_det, True)
    report("J DAG: 独立 a,b は同 wave→c は次 wave", j_waves, [["a", "b"], ["c"]])
    report("J DAG が DONE", finalJ, "DONE")
    # K: 自動有効化（checks/needs/manifest で --orchestrate auto ON）
    report("K checks 宣言で auto ON", auto_orchestrate([s(id="v", checks=["true"])])[0], True)
    report("K needs 宣言で auto ON", auto_orchestrate([s(id="a"), s(id="b", needs=["a"])])[0], True)
    report("K 宣言なしは off", auto_orchestrate([s(id="x")])[0], False)
    report("K manifest 既定で auto ON", auto_orchestrate([s(id="x")], manifest_default=True)[0], True)
    # L: ローカル LLM（ollama/lmstudio）が OpenAI 互換 HTTP プロバイダとして配線されている
    report("L ollama/lmstudio が配線済み", set(_OPENAI_BASE) == {"lmstudio", "ollama"}, True)
    rc_l, _ = run_provider("lmstudio", "verifier", "x", {"base_url": "http://127.0.0.1:1/v1", "timeout": 2})
    report("L サーバ不在でも crash せず rc!=0", rc_l != 0, True)
    # M: 動的モデル探索（--auto-model）— サーバ不在でも crash せず graceful
    found = discover_models({"base_url": "http://127.0.0.1:1/v1", "timeout": 2})
    report("M discover が全プロバイダを返す", set(found) >= {"ollama", "lmstudio", "claude", "codex", "rig"}, True)
    report("M 不在サーバは reachable=False", found["ollama"]["reachable"], False)
    report("M auto-model 解決は既定にフォールバック",
           resolve_http_model("ollama", {"auto_model": True, "base_url": "http://127.0.0.1:1/v1", "timeout": 2}),
           "llama3.1")
    report("M --model 明示は最優先", resolve_http_model("ollama", {"auto_model": True, "model": "qwen2.5"}), "qwen2.5")
    # N: probe の土台（codex の実コマンドが正しい・検証出力から VERDICT を拾える）
    report("N probe: codex verifier は read-only サンドボックスを強制",
           build_argv("codex", "verifier", "P", {}),
           ["codex", "exec", "--skip-git-repo-check", "--sandbox", "read-only", "P"])
    report("N probe: codex generator は workspace-write サンドボックス",
           build_argv("codex", "generator", "P", {}),
           ["codex", "exec", "--skip-git-repo-check", "--sandbox", "workspace-write", "P"])
    report("N probe: claude verifier は allowedTools を強制",
           build_argv("claude", "verifier", "P", {}),
           ["claude", "-p", "P", "--output-format", "text", "--allowedTools", "Read,Grep,Glob"])
    report("N probe: claude generator は権限フラグなし",
           build_argv("claude", "generator", "P", {}), ["claude", "-p", "P", "--output-format", "text"])
    _, out_n = run_provider("mock", "verifier", "x", {})
    report("N probe: 検証出力に VERDICT", "VERDICT" in out_n, True)
    # O: タスクキュー（local backend で積む→list→mock go→note/retry/done-除外→github は CLI 不在で graceful）
    _orig_qp = queueing.QUEUE_PATH
    import tempfile
    queueing.QUEUE_PATH = pathlib.Path(tempfile.gettempdir()) / "rig_queue_selftest.json"
    queueing.QUEUE_PATH.unlink(missing_ok=True)
    queue_add("local", "タスクA", {}); queue_add("local", "タスクB", {})
    q_items = queue_list("local", {})
    for it in q_items:                          # mock で go（生成→検証→done）
        _, vout = run_provider("mock", "verifier", "x", {}, persona="queue")
        queue_set_status("local", it["id"], "done" if "VERDICT: PASS" in vout else "failed", "", {})
    q_done_raw = [it for it in _local_load()["items"] if it["status"] == "done"]  # 生ストアで確認
    q_done_in_list = [it for it in queue_list("local", {}) if it["status"] == "done"]  # #215: 出ない
    note_text = "❌ rig: 検証 FAIL（mock→mock）"
    target_id = q_items[0]["id"]
    queue_set_status("local", target_id, "failed", note_text, {})  # #214 用に明示的に failed+note
    q_note = next(it for it in queue_list("local", {}) if it["id"] == target_id)
    queue_set_status("local", target_id, "queued", "", {})         # #213: retry と同じ呼び出し
    q_retried = next(it for it in queue_list("local", {}) if it["id"] == target_id)
    relabel_failed = _queue_relabel_args("failed")
    relabel_removes = [relabel_failed[i + 1] for i in range(len(relabel_failed) - 1)
                        if relabel_failed[i] == "--remove-label"]
    gh_item = queue_add("github", "t", {})      # gh 不在 → error（crash しない）
    queueing.QUEUE_PATH.unlink(missing_ok=True)
    queueing.QUEUE_PATH = _orig_qp
    report("O queue: 2件積んで list", len(q_items), 2)
    report("O queue: mock go で全 done（生ストア確認）", len(q_done_raw), 2)
    report("O queue: done item は queue_list（local）に出ない（#215）", len(q_done_in_list), 0)
    report("O queue: failed item の note が list に反映（#214）", q_note.get("note"), note_text)
    report("O queue: retry で queued に戻る（#213）", q_retried["status"], "queued")
    report("O queue: retry で note がクリアされる（#213）", q_retried.get("note"), "")
    report("O queue: running→failed で rig-running が除去対象（#223）",
           "rig-running" in relabel_removes, True)
    report("O github backend は CLI 不在で graceful(error)", gh_item["status"], "error")
    # P: 実行テレメトリ（run_loop 経由の全シナリオが .rig/runs.jsonl に1行ずつ追記される）
    p_lines = []
    if config.RUNS_PATH.exists():
        p_lines = [json.loads(l) for l in config.RUNS_PATH.read_text(encoding="utf-8").splitlines() if l.strip()]
    p_first = p_lines[0] if p_lines else {}
    config.RUNS_PATH.unlink(missing_ok=True)
    config.RUNS_PATH = _orig_runs
    report("P telemetry: run_loop 8回分を記録", len(p_lines), 8)
    report("P telemetry: final と recipe を記録",
           (p_first.get("final"), p_first.get("recipe")), ("DONE", "selftest-run"))
    p_verdicts = sorted({v["by"] for r in p_lines for st in r.get("steps", [])
                         for v in st.get("verdicts", [])})
    report("P telemetry: 検証票（by）を記録", "mock:independent" in p_verdicts
           and "mock:correctness" in p_verdicts, True)
    # Q: RESOLVE 参照実装（extends マージ・remove・origin・badge 固定順・steps: フィールド）の golden 検証
    qdir = pathlib.Path(tempfile.mkdtemp(prefix="rig_resolve_selftest_"))
    (qdir / "base-flow.md").write_text(
        "---\nname: base-flow\ndescription: t\nscope: shipped\nautonomy: interactive\n"
        "steps:\n  - id: intake\n    instruction: intake\n"
        "  - id: design\n    instruction: design\n    condition: \"--design または size L+\"\n"
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
    report("Q resolve: 親の steps: フィールド（condition 略記）",
           q_base["steps_field"], "intake, design?[--design|L+], implement, verify")
    report("Q resolve: extends 確定 step 列（remove/override/added）",
           q1["steps_field"], "intake, implement, verify, pr")
    report("Q resolve: origin 判定",
           [s["origin"] for s in q1["steps"]], ["inherited", "inherited", "override", "added"])
    report("Q resolve: badge 固定順（tdd→gated→orchestrate(auto)→autonomous→verify-findings）",
           q1["badges"], ["tdd", "gated", "orchestrate(auto)", "autonomous", "verify-findings"])
    report("Q resolve: 決定論（同入力→同 JSON）",
           json.dumps(q1, sort_keys=True), json.dumps(q2, sort_keys=True))
    # R: RESOLVE フェーズ2（condition 評価・size 判定・スライス・flag 優先順位）の golden 検証
    rf = config.RECIPES / "release-flow.md"
    r_s = resolve_effective(rf, [], diff_lines=50)                       # size S: design/review OFF
    r_flag = resolve_effective(rf, ["--design"], diff_lines=50)          # flag が condition を解決
    r_l = resolve_effective(rf, [], diff_lines=300)                      # size L: design/review ON
    r_only_off = resolve_effective(rf, ["--only", "review"], diff_lines=50)   # ケース B: condition-OFF
    r_only_on = resolve_effective(rf, ["--only", "review", "--review"], diff_lines=50)
    r_range = resolve_effective(rf, ["--from", "implement", "--to", "verify"], diff_lines=50)
    r_rev = resolve_effective(rf, ["--from", "verify", "--to", "implement"], diff_lines=50)
    r_skipwin = resolve_effective(rf, ["--design", "--skip", "design"], diff_lines=50)
    r_onlyskip = resolve_effective(rf, ["--only", "verify", "--skip", "design"], diff_lines=50)
    r_gate = resolve_effective(rf, ["--skip", "verify"], diff_lines=50)  # acceptance-gate skip WARN
    r_typo = resolve_effective(rf, ["--only", "verifi"], diff_lines=50)  # ケース A: Levenshtein 候補
    r_det = (json.dumps(resolve_effective(rf, ["--design"], diff_lines=50), sort_keys=True)
             == json.dumps(resolve_effective(rf, ["--design"], diff_lines=50), sort_keys=True))
    report("R size S: design/review は condition-OFF",
           r_s["effective_steps"], ["intake", "implement", "verify", "pr", "merge"])
    report("R --design flag が condition を解決",
           r_flag["effective_steps"], ["intake", "design", "implement", "verify", "pr", "merge"])
    report("R size L+: design/review が自動 ON",
           r_l["effective_steps"], ["intake", "design", "implement", "verify", "review", "pr", "merge"])
    report("R --only condition-OFF step はエラー（ケース B）",
           any("condition" in e for e in r_only_off["errors"]), True)
    report("R --only + 有効化フラグで単独実行", r_only_on["effective_steps"], ["review"])
    report("R --from/--to 範囲スライス", r_range["effective_steps"], ["implement", "verify"])
    report("R --from/--to 順序逆はエラー", any("順序が逆" in e for e in r_rev["errors"]), True)
    report("R 明示 --skip が明示 ON に勝つ", "design" not in r_skipwin["effective_steps"], True)
    report("R --only は --skip を無視して WARN",
           any("--only 優先・--skip 無視" in w for w in r_onlyskip["warnings"]), True)
    report("R acceptance-gate step の --skip は WARN",
           any("acceptance-gate" in w for w in r_gate["warnings"]), True)
    report("R タイポは Levenshtein 候補つきエラー（ケース A）",
           any("もしかして: verify" in e for e in r_typo["errors"]), True)
    report("R resolve_effective は決定論", r_det, True)
    # S: フェーズ3（manifest 閾値の反映・git diff 自動測定の graceful）
    s_manifest = {"size_thresholds": {"S_max": 10, "M_max": 20, "L_max": 40}}
    r_s_th = resolve_effective(rf, [], diff_lines=30, manifest=s_manifest)  # 30行 > M_max:20 → L
    r_s_orch = resolve_effective(rf, [], diff_lines=5, manifest={"default_orchestrate": True})
    report("S manifest size_thresholds が size 判定に効く（30行→L で design ON）",
           "design" in r_s_th["effective_steps"], True)
    report("S manifest default_orchestrate で orchestrate auto",
           r_s_orch["mode"]["orchestrate"].startswith("auto"), True)
    report("S git_diff_lines は crash せず int|None", isinstance(git_diff_lines(), (int, type(None))), True)
    report("S load_manifest は常に dict", isinstance(load_manifest(), dict), True)
    # V: パーティ編成画面（party）＝ runs/drill から RPG シートを描画
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
    config.RUNS_PATH.unlink(missing_ok=True); config.DRILL_PATH.unlink(missing_ok=True)
    config.RUNS_PATH = _orig_runs; config.DRILL_PATH = _orig_drill
    report("V party: Lv と実績を描画", "Lv.1" in v_out and "🏆 初DONE" in v_out, True)
    report("V party: drill 検出率と出撃/REJECT を反映",
           "検出率 100%（drill 2/2）" in v_out and "出撃   1 / REJECT 1" in v_out, True)
    # U: モデル混成クォーラム（同一 persona を複数プロバイダで並走・票は provider:persona）
    stepsU = [s(id="review", gate="review-gate", personas=["x"])]
    stateU1 = new_state("mq", stepsU, None)
    finalU1 = run_loop(stateU1, None, "mock", ["mock", "mock"], {}, 20, quiet=True, max_parallel=2)
    vU1 = stateU1["step_state"]["review"]["verdicts"]
    stateU2 = new_state("mq", stepsU, None)
    run_loop(stateU2, None, "mock", ["mock", "mock"], {}, 20, quiet=True, max_parallel=2)
    u_det = (json.dumps(stateU1["step_state"], sort_keys=True)
             == json.dumps(stateU2["step_state"], sort_keys=True))
    report("U model-quorum: 2プロバイダ×1persona で2票", len(vU1), 2)
    report("U model-quorum: 票の by は provider:persona", all(v["by"] == "mock:x" for v in vU1), True)
    report("U model-quorum: DONE + 決定論", (finalU1, u_det), ("DONE", True))
    # T: ギャップ処方箋（同一 step のエスカレーション反復 → --discover 提案）
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
    report("T gap: エスカレーション2回で処方箋を提示", "ギャップ処方箋" in t_out and "--discover" in t_out, True)
    report("T gap: 対象 step を特定", "release-flow / verify: エスカレーション 2 回" in t_out, True)
    for f in qdir.iterdir():
        f.unlink()
    qdir.rmdir()
    # ── シナリオ X: worktree 隔離（setup/teardown の決定論的な後始末規則）────
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
    _g("add", "."); _g("commit", "-q", "-m", "base")
    config.INVOCATION_CWD = xroot
    # X-1: DONE + commit あり + clean → ff 合流して撤収
    iso1 = setup_isolation("demo")
    report("X isolate: worktree と branch が作られる",
           pathlib.Path(iso1["dir"]).is_dir() and iso1["branch"].startswith("rig/run-demo-"), True)
    (pathlib.Path(iso1["dir"]) / "made.txt").write_text("x\n")
    _g("add", ".", cwd=iso1["dir"]); _g("commit", "-q", "-m", "work", cwd=iso1["dir"])
    report("X isolate: DONE+commit+clean は ff 合流（merged）", teardown_isolation(iso1, "DONE"), "merged")
    report("X isolate: 合流後 root に成果が居る", (xroot / "made.txt").exists(), True)
    report("X isolate: 撤収後 worktree は消える", pathlib.Path(iso1["dir"]).exists(), False)
    # X-2: DONE でも dirty → 保全
    iso2 = setup_isolation("demo")
    (pathlib.Path(iso2["dir"]) / "wip.txt").write_text("wip\n")
    report("X isolate: dirty は保全（kept）", teardown_isolation(iso2, "DONE"), "kept")
    report("X isolate: 保全時 worktree が残る", pathlib.Path(iso2["dir"]).is_dir(), True)
    # X-3: 変更なしの DONE → 撤収のみ
    iso3 = setup_isolation("demo")
    report("X isolate: 変更なしは撤収のみ（clean-removed）", teardown_isolation(iso3, "DONE"), "clean-removed")
    # X-4: ESCALATE → 保全
    iso4 = setup_isolation("demo")
    report("X isolate: 未達（ESCALATE）は保全（kept）", teardown_isolation(iso4, "ESCALATE"), "kept")
    config.INVOCATION_CWD = _orig_cwd

    # ── シナリオ W: ブリック・グラフ（型付き関係の導出・golden アンカー）────
    gW1 = build_brick_graph()
    gW2 = build_brick_graph()
    report("W graph: 導出は決定論（同入力→同グラフ）", gW1 == gW2, True)
    eW = {(e["from"], e["rel"], e["to"]) for e in gW1["edges"]}
    report("W graph: persona inject が injects エッジになる",
           ("persona:security-reviewer", "injects", "wiki:appsec-checklist") in eW, True)
    report("W graph: recipe steps が gated-by/uses-persona エッジになる",
           ("recipe:review-only", "gated-by", "pattern:review-gate") in eW
           and ("recipe:review-only", "uses-persona", "persona:security-reviewer") in eW, True)
    report("W graph: wiki 相互リンクが links-to エッジになる",
           ("wiki:appsec-checklist", "links-to", "wiki:injection-patterns") in eW, True)
    report("W graph: agent は -reviewer 差を吸収して persona を mirrors",
           ("agent:lazy-senior-reviewer", "mirrors", "persona:lazy-senior") in eW, True)
    report("W graph: shipped tier に未解決エッジ 0",
           sum(1 for e in gW1["edges"] if not e["resolved"]), 0)

    print("\n" + ("PASS: 決定論オーケストレータは健全" if ok else "FAIL: セルフテスト不一致"))
    sys.exit(0 if ok else 1)

