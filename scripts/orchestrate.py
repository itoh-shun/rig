#!/usr/bin/env python3
"""
rig 計算的オーケストレータ（deterministic orchestration runner）

recipe のステップ DAG を**コードが**解釈し、遷移・ゲート・停止条件・状態保持を
決定論的に強制する薄いランナー。rig engine（SKILL.md）の「制御ループを散文で
モデルに握らせる」弱点を埋める層＝舵をコードが握る（engine 不変・opt-in）。

モデルは各ステップの「作業」をするが、「次に何をするか」はこのランナーが決める：
  plan   <recipe.md>                 ステップ状態機械を決定論的に算出（モデル不要）
  init   <recipe.md> [--goal G]      run-state を作成し最初のアクションを出す
  check  <state.json>                現ステップの checks: (shell) を実行し pass/fail 記録（計算的センサー）
  verdict<state.json> --by N --pass  独立検証者の推論的判定を記録（採点者≠生成者を強制）
  next   <state.json>                次の遷移を決定論的に計算・適用して出力
  status <state.json>                現在の状態を出力
  selftest                           決定論の自己検証（同入力→同遷移を証明）

依存: Python3 + PyYAML（validate.py と同じ）。終了コード 0=正常 / 1=エラー・ESCALATE。
"""

import sys
import os
import json
import shlex
import threading
import pathlib
import subprocess
import concurrent.futures as futures

try:
    import yaml
except ImportError:
    print("[ERROR] PyYAML が見つかりません。`pip install pyyaml`。")
    sys.exit(1)

ROOT = pathlib.Path(__file__).parent.parent
RECIPES = ROOT / "skills" / "rig" / "recipes"
DEFAULT_K = 2  # acceptance-gate の既定リトライ上限（SKILL §3.5）

# ── recipe 読み込み ───────────────────────────────────────────────────────────
def parse_frontmatter(path: pathlib.Path) -> dict:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---"):
        return {}
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}
    return yaml.safe_load(parts[1]) or {}


def load_steps(fm: dict) -> list[dict]:
    """recipe frontmatter から決定論的なステップ定義列を抽出する（純関数）。"""
    out = []
    for s in (fm.get("steps") or []):
        if not isinstance(s, dict):
            continue
        gate = s.get("gate")
        out.append({
            "id": s.get("id"),
            "instruction": s.get("instruction"),
            "gate": None if gate in (None, "—", "-") else gate,
            "pattern": s.get("pattern"),
            "personas": list(s.get("personas") or []),       # 並列検証者のロール
            "needs": list(s.get("needs") or []),             # 任意: 依存 step（DAG 並列）
            "acceptance": list(s.get("acceptance") or []),
            "checks": list(s.get("checks") or []),          # 任意: 機械検証コマンド列
            "max_retries": s.get("max_retries") or DEFAULT_K,
            "output_contract": s.get("output_contract"),
        })
    return out


# ── run-state ────────────────────────────────────────────────────────────────
def new_state(recipe: str, steps: list[dict], goal: str | None) -> dict:
    return {
        "recipe": recipe,
        "goal": goal,
        "steps": steps,
        "cursor": 0,
        "step_state": {s["id"]: {"status": "pending", "retries": 0, "checks": [], "verdicts": []}
                       for s in steps},
        "stopped": None,
        "done": False,
        "history": [],
    }


def save_state(state: dict, path: pathlib.Path) -> None:
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def load_state(path: pathlib.Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


# ── ゲート評価（決定論・純関数）────────────────────────────────────────────────
def gate_outcome(step: dict, st: dict) -> str:
    """現ステップの合否を決定論的に判定する。
    返り値: pass | fail | incomplete | self-graded
    """
    gate = step["gate"]
    if not gate:
        return "pass"  # ゲート無し step は素通り

    declared = step["checks"]
    ran = st["checks"]
    verdicts = st["verdicts"]

    # 計算的センサー（checks）— 宣言があれば一次根拠。全件実行＆全 ok を要求。
    if declared:
        if len(ran) < len(declared):
            return "incomplete"        # まだ check していない
        if any(not c["ok"] for c in ran):
            return "fail"

    # 推論的検証（verdict）— acceptance-gate/review-gate は独立判定を要求（checks 未宣言時）。
    needs_verdict = gate in ("acceptance-gate", "review-gate") and not declared
    if needs_verdict and not verdicts:
        return "incomplete"            # 独立検証者の判定待ち

    # 採点者≠生成者の強制（self-grading バイアス防止・policies/independent-verification）
    if any(str(v.get("by", "")).lower() in ("", "self", "generator", "producer") for v in verdicts):
        return "self-graded"
    if any(not v["ok"] for v in verdicts):
        return "fail"

    return "pass"


def compute_next(state: dict) -> tuple[str, str]:
    """状態から次のアクションを決定論的に計算して適用する（state を破壊的更新）。
    返り値: (action_code, message)
    """
    if state["stopped"]:
        return "STOPPED", f"停止済み: {state['stopped']['reason']}"
    steps = state["steps"]
    if state["cursor"] >= len(steps):
        state["done"] = True
        return "DONE", "全ステップ完了。"

    step = steps[state["cursor"]]
    sid = step["id"]
    st = state["step_state"][sid]

    if st["status"] == "pending":
        st["status"] = "running"
        state["history"].append({"action": "START", "step": sid})
        gate = step["gate"] or "なし"
        need = []
        if step["checks"]:
            need.append(f"check（{len(step['checks'])} 件の機械検証）")
        if step["gate"] in ("acceptance-gate", "review-gate") and not step["checks"]:
            need.append("verdict（独立検証者の判定・採点者≠生成者）")
        need_s = " → ".join(need) if need else "（ゲート無し＝作業後そのまま next）"
        return "START", (f"step `{sid}` を実行（instruction: {step['instruction']} / gate: {gate}）。"
                         f"作業を委譲し、{need_s} を済ませてから `next`。")

    # status == "running"
    outcome = gate_outcome(step, st)
    if outcome == "incomplete":
        return "AWAIT", f"step `{sid}` はゲート評価待ち。`check` / `verdict` を実行してから `next`。"
    if outcome == "self-graded":
        return "BLOCKED", (f"step `{sid}`: 生成者自身の判定（by=self/generator）は不可。"
                           f"独立した検証者の `verdict` が必要（採点者≠生成者）。")
    if outcome == "pass":
        st["status"] = "passed"
        state["cursor"] += 1
        state["history"].append({"action": "PASS", "step": sid})
        if state["cursor"] >= len(steps):
            state["done"] = True
            return "DONE", f"step `{sid}` 合格。全ステップ完了。"
        nxt = steps[state["cursor"]]["id"]
        return "ADVANCE", f"step `{sid}` 合格 → 次は step `{nxt}`。`next` で開始。"
    # fail
    st["retries"] += 1
    K = step["max_retries"]
    state["history"].append({"action": "FAIL", "step": sid, "try": st["retries"]})
    if st["retries"] >= K:
        state["stopped"] = {"reason": f"step `{sid}` がゲート未達のまま {K} 回 → エスカレーション", "at": sid}
        return "ESCALATE", state["stopped"]["reason"] + "（無限ループ禁止・ユーザーへ）。"
    # リトライ: この step をやり直し（記録はリセット）
    st["status"] = "pending"
    st["checks"] = []
    st["verdicts"] = []
    return "RETRY", f"step `{sid}` 未達 → やり直し（try {st['retries']+1}/{K}）。指摘を反映して再実行。"


# ── コマンド ──────────────────────────────────────────────────────────────────
def resolve_recipe(name: str) -> pathlib.Path:
    p = pathlib.Path(name)
    if p.exists():
        return p
    cand = RECIPES / (name if name.endswith(".md") else f"{name}.md")
    if cand.exists():
        return cand
    print(f"[ERROR] recipe が見つかりません: {name}")
    sys.exit(1)


def render_plan(recipe: str, steps: list[dict]) -> str:
    lines = [f"## rig 計算的プラン: {recipe}", "",
             f"ステップ数: {len(steps)} ／ 遷移はコードが強制（決定論）", ""]
    for i, s in enumerate(steps):
        gate = s["gate"] or "なし"
        sensor = ("計算的センサー " + str(len(s["checks"])) + "件"
                  if s["checks"] else
                  ("独立 verdict 要" if s["gate"] in ("acceptance-gate", "review-gate") else "—"))
        lines.append(f"  [{i}] {s['id']}  gate={gate}  K={s['max_retries']}  検証={sensor}")
    lines.append("")
    lines.append("停止条件: 各 step はゲート未達が K 回でエスカレーション（無限ループ禁止）。")
    return "\n".join(lines)


def cmd_plan(args):
    path = resolve_recipe(args[0])
    fm = parse_frontmatter(path)
    steps = load_steps(fm)
    print(render_plan(fm.get("name", path.stem), steps))
    if "--json" in args:
        print("\n" + json.dumps({"recipe": fm.get("name"), "steps": steps}, ensure_ascii=False))


def _state_path(args, default="run-state.json") -> pathlib.Path:
    return pathlib.Path(args[0]) if args else pathlib.Path(default)


def cmd_init(args):
    path = resolve_recipe(args[0])
    fm = parse_frontmatter(path)
    steps = load_steps(fm)
    goal = None
    out = pathlib.Path("run-state.json")
    i = 1
    while i < len(args):
        if args[i] == "--goal" and i + 1 < len(args):
            goal = args[i + 1]; i += 2
        elif args[i] == "--out" and i + 1 < len(args):
            out = pathlib.Path(args[i + 1]); i += 2
        else:
            i += 1
    state = new_state(fm.get("name", path.stem), steps, goal)
    save_state(state, out)
    print(render_plan(state["recipe"], steps))
    print(f"\nrun-state: {out}")
    action, msg = compute_next(state)
    save_state(state, out)
    print(f"\n▶ {action}: {msg}")


def _current_running(state: dict):
    if state["cursor"] >= len(state["steps"]):
        return None, None
    step = state["steps"][state["cursor"]]
    st = state["step_state"][step["id"]]
    if st["status"] != "running":
        return None, None
    return step, st


def cmd_check(args):
    sp = _state_path(args)
    state = load_state(sp)
    step, st = _current_running(state)
    if not step:
        print("[ERROR] 実行中(running)の step がありません。先に `next` で START してください。")
        sys.exit(1)
    if not step["checks"]:
        print(f"step `{step['id']}` に checks: は未宣言（機械検証なし）。verdict を使ってください。")
        return
    print(f"## check: step `{step['id']}` の計算的センサー（{len(step['checks'])} 件）")
    st["checks"] = []
    all_ok = True
    for cmd in step["checks"]:
        r = subprocess.run(cmd, shell=True, cwd=str(ROOT),
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        ok = (r.returncode == 0)
        all_ok = all_ok and ok
        st["checks"].append({"cmd": cmd, "ok": ok})
        print(f"  [{'OK ' if ok else 'NG '}] {cmd}  (exit {r.returncode})")
    save_state(state, sp)
    print(f"→ {'全件 OK' if all_ok else 'NG あり'}。`next` で遷移を計算。")


def cmd_verdict(args):
    sp = _state_path(args)
    state = load_state(sp)
    step, st = _current_running(state)
    if not step:
        print("[ERROR] 実行中(running)の step がありません。")
        sys.exit(1)
    by, ok, note = None, None, ""
    i = 1
    while i < len(args):
        if args[i] == "--by" and i + 1 < len(args):
            by = args[i + 1]; i += 2
        elif args[i] == "--pass":
            ok = True; i += 1
        elif args[i] == "--fail":
            ok = False; i += 1
        elif args[i] == "--note" and i + 1 < len(args):
            note = args[i + 1]; i += 2
        else:
            i += 1
    if by is None or ok is None:
        print("[ERROR] --by <検証者名> と --pass|--fail が必須。")
        sys.exit(1)
    st["verdicts"].append({"by": by, "ok": ok, "note": note})
    save_state(state, sp)
    guard = "（独立）" if by.lower() not in ("self", "generator", "producer") else "（⚠ 生成者自身＝無効）"
    print(f"verdict 記録: step `{step['id']}` by={by}{guard} → {'PASS' if ok else 'FAIL'}。`next` へ。")


def cmd_next(args):
    sp = _state_path(args)
    state = load_state(sp)
    action, msg = compute_next(state)
    save_state(state, sp)
    print(f"▶ {action}: {msg}")
    if action == "ESCALATE":
        sys.exit(1)


def cmd_status(args):
    sp = _state_path(args)
    state = load_state(sp)
    print(f"## run: {state['recipe']}  cursor={state['cursor']}/{len(state['steps'])}  "
          f"done={state['done']}  stopped={bool(state['stopped'])}")
    for s in state["steps"]:
        st = state["step_state"][s["id"]]
        print(f"  {s['id']:<14} {st['status']:<9} retries={st['retries']} "
              f"checks={sum(1 for c in st['checks'] if c['ok'])}/{len(st['checks'])} "
              f"verdicts={len(st['verdicts'])}")


# ── 実行層（外部ランナー・プロバイダ抽象）──────────────────────────────────────
# 各 step を「別プロセスのエージェント」で実行する＝プロセス境界で context を隔離。
# 検証は「別プロバイダ/別プロセス」で回す＝構造的に採点者≠生成者（takt 型トポロジ）。
# 既定プロバイダは無し（明示必須）。本物の claude/codex は配線のみ、テストは mock。

MOCK_SRC = (
    "import sys\n"
    "role = sys.argv[1] if len(sys.argv) > 1 else 'generator'\n"
    "persona = sys.argv[2] if len(sys.argv) > 2 else ''\n"
    "if role == 'verifier':\n"
    "    print('独立検証（mock）: ' + persona)\n"
    "    print('VERDICT: ' + ('FAIL' if 'fail' in persona else 'PASS'))\n"
    "else:\n"
    "    print('## step 実行結果（mock）')\n"
    "    print('STATUS: done')\n"
)

RIG_GEN_PREFIX = ("`rig` skill を Skill ツールで起動し、その engine（PARSE→RESOLVE→COMPOSE→RUN・"
                  "context-minimal）に従って次の step を実行してください。\n")
RIG_VER_PREFIX = ("`rig` skill を Skill ツールで起動し、独立した検証者として（この step を生成した"
                  "エージェントとは別プロセス）受け入れ基準を判定し、最後に必ず 'VERDICT: PASS' か "
                  "'VERDICT: FAIL' を出力してください。\n")

def build_argv(provider: str, role: str, prompt: str, cfg: dict, persona: str = "") -> list[str]:
    if provider == "mock":
        return ["python3", "-c", MOCK_SRC, role, persona]
    if provider == "rig":
        # 各 step を「rig ハーネス」として claude ヘッドレスで起動（rig を名前で呼ぶ）。
        pre = RIG_VER_PREFIX if role == "verifier" else RIG_GEN_PREFIX
        return ["claude", "-p", pre + prompt, "--output-format", "text"]
    if provider == "claude":
        # ヘッドレス。実運用は権限モード等をユーザーが --provider-cmd で調整可。
        return ["claude", "-p", prompt, "--output-format", "text"]
    if provider == "codex":
        return ["codex", "exec", prompt]
    if provider == "cmd":
        tmpl = cfg.get("provider_cmd") or ""
        if not tmpl:
            raise SystemExit("[ERROR] --provider cmd には --provider-cmd \"... {prompt} ...\" が必須")
        # shlex で引用符・空白を尊重（実 codex 等のラッパーを安全に渡せる）
        return [a.replace("{prompt}", prompt).replace("{role}", role).replace("{persona}", persona)
                for a in shlex.split(tmpl)]
    raise SystemExit(f"[ERROR] 未知のプロバイダ: {provider}")


def run_provider(provider: str, role: str, prompt: str, cfg: dict, persona: str = "") -> tuple[int, str]:
    argv = build_argv(provider, role, prompt, cfg, persona)
    try:
        r = subprocess.run(argv, input=prompt if provider in ("cmd",) else None,
                           capture_output=True, text=True, timeout=cfg.get("timeout", 600))
    except FileNotFoundError:
        return 127, f"[provider not found: {provider}]"
    except subprocess.TimeoutExpired:
        return 124, "[provider timeout]"
    return r.returncode, (r.stdout or "")


def run_verifiers_parallel(ver: str, prompt: str, personas: list[str],
                           cfg: dict, max_parallel: int) -> list[dict]:
    """N 人の検証者を同時プロセスで走らせ、persona 名順（決定論）に結果を返す。"""
    import concurrent.futures as _f
    personas = personas or ["reviewer"]

    def _one(p):
        rc, out = run_provider(ver, "verifier", prompt, cfg, persona=p)
        ok = ("VERDICT: PASS" in out) and ("VERDICT: FAIL" not in out)
        return {"by": f"{ver}:{p}", "persona": p, "ok": ok, "note": f"exit {rc}"}

    if len(personas) == 1:
        return [_one(personas[0])]
    with _f.ThreadPoolExecutor(max_workers=max(1, max_parallel)) as ex:
        res = list(ex.map(_one, personas))
    return sorted(res, key=lambda r: r["persona"])  # 完了順に依らず決定論


def _build_prompt(state: dict, step: dict) -> str:
    return (f"あなたは rig のサブエージェント（{step['id']} 担当）。recipe '{state['recipe']}' の "
            f"step '{step['id']}'（instruction: {step['instruction']}）を実行してください。"
            f"ゴール: {state.get('goal') or '(なし)'}。完了したら最後に 'STATUS: done' を出力。")


def _build_verify_prompt(state: dict, step: dict, product: str) -> str:
    return (f"あなたは独立した検証者です（この step を生成したエージェントとは別プロセス・別ロール）。"
            f"step '{step['id']}' の成果が受け入れ基準を満たすか判定し、最後に必ず "
            f"'VERDICT: PASS' か 'VERDICT: FAIL' を出力してください。\n--- 成果 ---\n{product[:2000]}")


def _run_step_checks(step: dict, st: dict) -> None:
    st["checks"] = []
    for cmd in step["checks"]:
        r = subprocess.run(cmd, shell=True, cwd=str(ROOT),
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        st["checks"].append({"cmd": cmd, "ok": r.returncode == 0})


_HIST_LOCK = threading.Lock()


def _generate(state: dict, step: dict, gen_list: list[str], ver: str,
              cfg: dict, max_parallel: int) -> tuple[str | None, str, list[dict]]:
    """単独 or judge-panel で生成。複数なら全 generator を並列に走らせ、
    judge(ver) が最初に PASS した候補（generator 列の順＝決定論）を勝者に選ぶ。
    返り値: (winner_provider | None, product, judged[])。"""
    if len(gen_list) == 1:
        _, out = run_provider(gen_list[0], "generator", _build_prompt(state, step), cfg)
        return gen_list[0], out, []
    def _gen(p):
        rc, out = run_provider(p, "generator", _build_prompt(state, step), cfg)
        return {"provider": p, "rc": rc, "out": out}
    with futures.ThreadPoolExecutor(max_workers=max(1, max_parallel)) as ex:
        cands = list(ex.map(_gen, gen_list))
    cands.sort(key=lambda c: gen_list.index(c["provider"]))   # 生成順で評価＝決定論
    judged, winner, product = [], None, cands[0]["out"]
    for c in cands:
        _, jout = run_provider(ver, "verifier", _build_verify_prompt(state, step, c["out"]),
                               cfg, persona="judge")
        ok = ("VERDICT: PASS" in jout) and ("VERDICT: FAIL" not in jout)
        judged.append({"provider": c["provider"], "ok": ok})
        if ok and winner is None:
            winner, product = c["provider"], c["out"]
    return winner, product, judged


def _execute_step(state: dict, step: dict, st: dict, gen_list: list[str], ver: str,
                  cfg: dict, max_parallel: int, quorum: str, log) -> None:
    """1 step を実行：生成（別プロセス・judge-panel 可）→ ゲート根拠（checks or 並列検証）を記録。"""
    winner, out, judged = _generate(state, step, gen_list, ver, cfg, max_parallel)
    with _HIST_LOCK:
        state["history"].append({"action": "EXEC", "step": step["id"],
                                 "provider": winner or gen_list[0], "out": out[:200]})
    if judged:
        log(f"   ↳ judge-panel {len(judged)} 案 → 勝者: {winner or '(無し)'}")
    else:
        log(f"   ↳ {gen_list[0]}:generator")
    if step["checks"]:
        _run_step_checks(step, st)
        log(f"   ↳ checks: {sum(c['ok'] for c in st['checks'])}/{len(st['checks'])} ok")
        return
    if step["gate"] not in ("acceptance-gate", "review-gate"):
        return
    if judged:
        # judge-panel は judge が選定＝そのゲート判定を採用（勝者が居れば合格）
        st["verdicts"].append({"by": f"{ver}:judge-panel", "ok": winner is not None,
                               "note": "winner=" + str(winner)})
        return
    # 観点検証＝N 人の独立レビュアーを並列プロセスで（採点者≠生成者）
    personas = step["personas"] or ["independent"]
    results = run_verifiers_parallel(ver, _build_verify_prompt(state, step, out),
                                     personas, cfg, max_parallel)
    passes, total = sum(1 for r in results if r["ok"]), len(results)
    par = "並列" if total > 1 else "単独"
    log(f"   ↳ {par}検証 {total} 人: PASS {passes}/{total}（quorum={quorum}）")
    if quorum == "majority" and total > 1:
        st["verdicts"].append({
            "by": f"{ver}:quorum-majority", "ok": passes * 2 > total,
            "note": f"{passes}/{total} pass; " + ", ".join(
                f"{r['persona']}={'P' if r['ok'] else 'F'}" for r in results)})
    else:
        st["verdicts"].extend(results)


def run_loop(state: dict, sp: pathlib.Path | None, gen: str, ver: str,
             cfg: dict, max_steps: int, quiet: bool = False,
             max_parallel: int = 4, quorum: str = "all",
             generators: list[str] | None = None) -> str:
    """自走ループ。step に needs: があれば DAG 並列モードへ自動切替（独立 step を同時実行）。"""
    log = (lambda *a: None) if quiet else print
    gen_list = generators or [gen]
    if any(s["needs"] for s in state["steps"]):
        return run_dag(state, sp, gen_list, ver, cfg, max_steps, quiet, max_parallel, quorum)
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
    return last


def run_dag(state: dict, sp: pathlib.Path | None, gen_list: list[str], ver: str,
            cfg: dict, max_steps: int, quiet: bool, max_parallel: int, quorum: str) -> str:
    """step-DAG 並列ランナー。依存(needs)を満たした独立 step を同時プロセスで実行する。
    各 wave の ready 集合は id 順（決定論）、ゲート評価も id 順に適用。"""
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
            log("▶ DONE: 全 step 完了。")
            break
        ready = sorted((s for s in state["steps"]
                        if ss[s["id"]]["status"] == "pending"
                        and all(d in passed for d in s["needs"])),
                       key=lambda s: s["id"])
        if not ready:
            state["stopped"] = {"reason": "DAG: 実行可能な step が無い（依存未充足/失敗）",
                                "kind": "ESCALATE", "at": "—"}
            break
        ids = [s["id"] for s in ready]
        state["waves"].append(ids)
        log(f"▶ WAVE {waves}: {ids} を並列実行")
        for s in ready:
            ss[s["id"]]["status"] = "running"
        with futures.ThreadPoolExecutor(max_workers=max(1, max_parallel)) as ex:
            list(ex.map(lambda s: _execute_step(state, s, ss[s["id"]], gen_list, ver,
                                                cfg, max_parallel, quorum,
                                                (lambda *a: None)), ready))
        for s in ready:                       # ゲート評価を id 順に適用（決定論）
            st = ss[s["id"]]
            outcome = gate_outcome(s, st)
            if outcome == "pass":
                st["status"] = "passed"
                log(f"   ✓ {s['id']}")
            elif outcome == "self-graded":
                state["stopped"] = {"reason": f"{s['id']}: 自己採点（by=self）", "kind": "BLOCKED", "at": s["id"]}
            else:
                st["retries"] += 1
                if st["retries"] >= s["max_retries"]:
                    state["stopped"] = {"reason": f"{s['id']} がゲート未達 {s['max_retries']} 回",
                                        "kind": "ESCALATE", "at": s["id"]}
                else:
                    st["status"], st["checks"], st["verdicts"] = "pending", [], []
                    log(f"   ↻ {s['id']} retry（try {st['retries']+1}/{s['max_retries']}）")
        if sp:
            save_state(state, sp)
    if sp:
        save_state(state, sp)
    if state.get("done"):
        return "DONE"
    if state["stopped"]:
        return state["stopped"].get("kind", "ESCALATE")
    return "—"


def cmd_run(args):
    if not args:
        print("[ERROR] usage: run <recipe> --provider <name> [--verifier-provider <name>] "
              "[--provider-cmd \"...{prompt}...\"] [--max-steps N] [--goal G] [--out f]")
        sys.exit(1)
    path = resolve_recipe(args[0])
    fm = parse_frontmatter(path)
    steps = load_steps(fm)
    gen = ver = None
    generators: list[str] = []
    goal = None
    out = pathlib.Path("run-state.json")
    max_steps = 40
    max_parallel = 4
    quorum = "all"
    cfg: dict = {}
    i = 1
    while i < len(args):
        a = args[i]
        if a == "--provider" and i + 1 < len(args):
            gen = args[i + 1]; i += 2
        elif a == "--generators" and i + 1 < len(args):
            generators = [g.strip() for g in args[i + 1].split(",") if g.strip()]; i += 2
        elif a == "--verifier-provider" and i + 1 < len(args):
            ver = args[i + 1]; i += 2
        elif a == "--provider-cmd" and i + 1 < len(args):
            cfg["provider_cmd"] = args[i + 1]; i += 2
        elif a == "--goal" and i + 1 < len(args):
            goal = args[i + 1]; i += 2
        elif a == "--out" and i + 1 < len(args):
            out = pathlib.Path(args[i + 1]); i += 2
        elif a == "--max-steps" and i + 1 < len(args):
            max_steps = int(args[i + 1]); i += 2
        elif a == "--max-parallel" and i + 1 < len(args):
            max_parallel = int(args[i + 1]); i += 2
        elif a == "--quorum" and i + 1 < len(args):
            quorum = args[i + 1]; i += 2
        else:
            i += 1
    if not gen and generators:
        gen = generators[0]            # --generators だけでも可（先頭を代表に）
    if not gen:
        print("[ERROR] --provider <name>（または --generators a,b,c）が必須"
              "（rig|claude|codex|cmd|mock）。rig＝各 step を rig ハーネスとして起動（推奨）。テストは mock。")
        sys.exit(1)
    ver = ver or gen  # 未指定なら同プロバイダ（ただし別プロセス・別ロール）
    state = new_state(fm.get("name", path.stem), steps, goal)
    print(render_plan(state["recipe"], steps))
    panel = f" / judge-panel={','.join(generators)}" if len(generators) > 1 else ""
    dag = " / DAG並列" if any(s["needs"] for s in steps) else ""
    print(f"\n自走実行: provider={gen} / verifier={ver} / "
          f"max-steps={max_steps} / 並列={max_parallel} / quorum={quorum}{panel}{dag}\n")
    final = run_loop(state, out, gen, ver, cfg, max_steps,
                     max_parallel=max_parallel, quorum=quorum,
                     generators=(generators or None))
    print(f"\n=== 終了: {final} ===  run-state: {out}")
    sys.exit(1 if final in ("ESCALATE", "BLOCKED") else 0)


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
    print("\n" + ("PASS: 決定論オーケストレータは健全" if ok else "FAIL: セルフテスト不一致"))
    sys.exit(0 if ok else 1)


# ── エントリ ──────────────────────────────────────────────────────────────────
COMMANDS = {
    "plan": cmd_plan, "init": cmd_init, "check": cmd_check,
    "verdict": cmd_verdict, "next": cmd_next, "status": cmd_status,
    "run": cmd_run, "selftest": cmd_selftest,
}


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        print(__doc__)
        sys.exit(0 if len(sys.argv) < 2 else 1)
    COMMANDS[sys.argv[1]](sys.argv[2:])


if __name__ == "__main__":
    main()
