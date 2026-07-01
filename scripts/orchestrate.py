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
  install-shim [--to PATH] [--force] ~/.local/bin/rig に shim を symlink（横断利用の入口・1回だけ）
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

def find_rig_home() -> pathlib.Path:
    """rig 資産（skills/, .claude-plugin/）の所在を解決する。
    優先順: $RIG_HOME → ~/.claude/plugins/data/rig-itoshun-local-plugins → __file__ 親（dev fallback）。
    cross-project 利用は plugin install パスで自動解決＝呼び出し元 cwd に依存しない。"""
    if env := os.environ.get("RIG_HOME"):
        p = pathlib.Path(env).expanduser()
        if (p / "skills" / "rig" / "SKILL.md").exists():
            return p
    installed = pathlib.Path.home() / ".claude" / "plugins" / "data" / "rig-itoshun-local-plugins"
    if (installed / "skills" / "rig" / "SKILL.md").exists():
        return installed
    return pathlib.Path(__file__).resolve().parent.parent


RIG_HOME = find_rig_home()
RECIPES = RIG_HOME / "skills" / "rig" / "recipes"
INVOCATION_CWD = pathlib.Path(os.getcwd()).resolve()
PROJECT_RECIPES = INVOCATION_CWD / ".rig" / "recipes"  # プロジェクト overlay
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
    """recipe を解決する。
    優先順: 絶対/相対パス実在 → cwd/.rig/recipes/<name>.md（プロジェクト overlay） → RIG_HOME/skills/rig/recipes/<name>.md（built-in）。
    overlay が built-in と同名なら overlay が勝つ＝プロジェクト固有レシピで上書き可能。"""
    p = pathlib.Path(name)
    if p.exists():
        return p
    fname = name if name.endswith(".md") else f"{name}.md"
    for base in (PROJECT_RECIPES, RECIPES):
        cand = base / fname
        if cand.exists():
            return cand
    print(f"[ERROR] recipe が見つかりません: {name}\n"
          f"  探索: {PROJECT_RECIPES}/{fname}, {RECIPES}/{fname}")
    sys.exit(1)


def auto_orchestrate(steps: list[dict], manifest_default: bool = False) -> tuple[bool, str]:
    """この recipe が --orchestrate を自動有効化するか（決定論・SKILL §4.3 と同じ規則）。"""
    has_checks = any(s["checks"] for s in steps)
    has_needs = any(s["needs"] for s in steps)
    if has_checks or has_needs:
        why = "・".join(x for x in (["checks"] if has_checks else []) + (["needs"] if has_needs else []))
        return True, f"recipe に {why} 宣言あり"
    if manifest_default:
        return True, "manifest default_orchestrate: true"
    return False, "明示 opt-in のみ（自動有効化なし）"


def render_plan(recipe: str, steps: list[dict]) -> str:
    auto, why = auto_orchestrate(steps)
    lines = [f"## rig 計算的プラン: {recipe}", "",
             f"ステップ数: {len(steps)} ／ 遷移はコードが強制（決定論）",
             f"自動 orchestrate: {'auto ON' if auto else 'off'}（{why}）", ""]
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
        r = subprocess.run(cmd, shell=True, cwd=str(INVOCATION_CWD),
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


# ── ローカル LLM（OpenAI 互換 HTTP）─────────────────────────────────────────────
# ollama / lmstudio はローカルサーバの OpenAI 互換エンドポイント（/v1 ルート）を叩く。
# 各リクエストは独立（ステートレス）＝context 隔離は保たれる。要: サーバ起動＋モデル。
_OPENAI_BASE = {
    "lmstudio": "http://localhost:1234/v1",    # LM Studio（Local Server を起動）
    "ollama":   "http://localhost:11434/v1",    # ollama serve（OpenAI 互換）
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
    """サーバの /v1/models から利用可能モデル id を取得（不可なら空）。"""
    data = _http_get_json(f"{_base_url(provider, cfg)}/models", cfg.get("timeout", 8))
    if not data:
        return []
    return [m.get("id") for m in (data.get("data") or []) if m.get("id")]


def resolve_http_model(provider: str, cfg: dict) -> str:
    """使用モデルを解決する。優先: --model → 保存設定 → サーバ実機の先頭 → 既定。
    --auto-model 指定時は実機から動的取得して設定する。"""
    if cfg.get("model"):
        return cfg["model"]
    if cfg.get("auto_model"):
        saved = _load_models_config().get(provider, {})
        if saved.get("default"):
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
    except Exception as e:                      # 接続不可・モデル無し等は rc!=0 で握り潰す
        return 1, f"[{provider} error: {e} @ {url}]"


def discover_models(cfg: dict) -> dict:
    """利用可能なプロバイダとモデルを動的に探索する（決定論的にソート）。"""
    import shutil
    out: dict = {}
    for p in sorted(_OPENAI_BASE):
        models = sorted(list_models(p, cfg))
        out[p] = {"kind": "local-http", "base_url": _base_url(p, cfg),
                  "reachable": bool(models), "models": models,
                  "default": models[0] if models else None}
    for p in ("claude", "codex"):               # CLI 系は presence のみ
        out[p] = {"kind": "cli", "available": shutil.which(p) is not None, "models": []}
    out["rig"] = {"kind": "cli", "available": shutil.which("claude") is not None,
                  "note": "各 step を rig ハーネス(claude)で起動", "models": []}
    return out


def cmd_models(args):
    cfg: dict = {}
    save = "--save" in args
    as_json = "--json" in args
    i = 0
    while i < len(args):
        if args[i] == "--base-url" and i + 1 < len(args):
            cfg["base_url"] = args[i + 1]; i += 2
        else:
            i += 1
    found = discover_models(cfg)
    if as_json:
        print(json.dumps(found, ensure_ascii=False, indent=2))
    else:
        print("## rig orchestrate: 利用可能モデル探索\n")
        for p, info in found.items():
            if info["kind"] == "local-http":
                status = (f"✓ {', '.join(info['models'])}" if info["reachable"]
                          else f"✗ サーバ未起動/モデル無し @ {info['base_url']}")
                print(f"  {p:<10} {status}")
            else:
                av = "✓ CLI あり" if info.get("available") else "✗ CLI 無し"
                print(f"  {p:<10} {av}{'  — ' + info['note'] if info.get('note') else ''}")
    if save:
        # local-http のみ設定保存（default モデルを次回 --auto-model で使う）
        conf = {p: {"base_url": d["base_url"], "default": d["default"], "models": d["models"]}
                for p, d in found.items() if d["kind"] == "local-http" and d["reachable"]}
        _MODELS_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _MODELS_CACHE_PATH.write_text(json.dumps(conf, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n保存: {_MODELS_CACHE_PATH}（{len(conf)} プロバイダ）— 次回 run --auto-model で使用")


def run_provider(provider: str, role: str, prompt: str, cfg: dict, persona: str = "") -> tuple[int, str]:
    if provider in _OPENAI_BASE:
        return run_http_provider(provider, prompt, cfg)
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
        r = subprocess.run(cmd, shell=True, cwd=str(INVOCATION_CWD),
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
        elif a == "--model" and i + 1 < len(args):
            cfg["model"] = args[i + 1]; i += 2
        elif a == "--base-url" and i + 1 < len(args):
            cfg["base_url"] = args[i + 1]; i += 2
        elif a in ("--auto-model", "--auto-model-setting"):
            cfg["auto_model"] = True; i += 1
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
              "（rig|claude|codex|ollama|lmstudio|cmd|mock）。rig＝各 step を rig ハーネスとして起動（推奨）。"
              "ollama/lmstudio＝ローカル LLM（要サーバ・--model でモデル指定）。テストは mock。")
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


# ── タスクキュー（積んで GO・管理ツール連携）──────────────────────────────────
# 「task を積む → まとめて GO」を、ローカル json か外部管理ツール(GitHub/GitLab Issue)で持つ。
# backend は差し替え式：local（.rig/queue.json）／github（gh CLI）／gitlab（glab CLI）。
# Issue 連携時はラベルで状態管理：rig-queue → rig-running → rig-done / rig-failed。
QUEUE_LABEL = "rig-queue"
# queue list が可視化すべき「アクティブ」ラベル（rig-done は close 済みのため対象外・#211）。
QUEUE_LABELS_ACTIVE = ["rig-queue", "rig-running", "rig-failed"]
QUEUE_PATH = INVOCATION_CWD / ".rig" / "queue.json"


def _gh_cli(backend: str) -> str:
    return {"github": "gh", "gitlab": "glab"}[backend]


def _cli_run(argv: list[str]) -> tuple[int, str, str]:
    """gh/glab を subprocess 実行。CLI 不在でも crash せず (127, "", err) を返す。"""
    try:
        r = subprocess.run(argv, capture_output=True, text=True)
        return r.returncode, r.stdout or "", r.stderr or ""
    except FileNotFoundError:
        return 127, "", f"{argv[0]} が見つかりません（CLI 未インストール）"


def _local_load() -> dict:
    try:
        return json.loads(QUEUE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"items": [], "next_id": 1}


def _local_save(q: dict) -> None:
    QUEUE_PATH.parent.mkdir(parents=True, exist_ok=True)
    QUEUE_PATH.write_text(json.dumps(q, ensure_ascii=False, indent=2), encoding="utf-8")


def queue_add(backend: str, task: str, cfg: dict) -> dict:
    if backend == "local":
        q = _local_load()
        item = {"id": q["next_id"], "task": task, "status": "queued", "note": ""}
        q["items"].append(item); q["next_id"] += 1
        _local_save(q)
        return item
    cli = _gh_cli(backend)
    argv = [cli, "issue", "create", "-t", task, "-l", QUEUE_LABEL, "-b", "rig queue task"]
    if cfg.get("repo"):
        argv += ["-R", cfg["repo"]]
    rc, out, err = _cli_run(argv)
    if rc != 0:
        return {"id": None, "task": task, "status": "error", "note": (err or out)[:200]}
    return {"id": out.strip().split("/")[-1] or "?", "task": task, "status": "queued"}


def queue_list(backend: str, cfg: dict) -> list[dict]:
    """アクティブな item（queued/running/failed）を全て返す。done（close 済み）は対象外。

    ラベル遷移（queue_set_status）で旧ラベルは外れるため、単一ラベルの `-l` 絞り込みだと
    running/failed に遷移した item が一覧から消える（#211）。QUEUE_LABELS_ACTIVE の各ラベルを
    個別に問い合わせて id（github）／行（gitlab, テキストのみ）で dedup・merge する。
    """
    if backend == "local":
        return _local_load()["items"]
    cli = _gh_cli(backend)
    R = (["-R", cfg["repo"]] if cfg.get("repo") else [])
    if backend == "github":
        seen: dict[object, dict] = {}
        for label in QUEUE_LABELS_ACTIVE:
            argv = [cli, "issue", "list", "-l", label, "--state", "open",
                    "--json", "number,title,labels"] + R
            rc, out, err = _cli_run(argv)
            if rc != 0:
                return [{"id": None, "task": f"[{cli} error: {(err or '')[:120]}]", "status": "error"}]
            try:
                rows = json.loads(out or "[]")
            except Exception:
                rows = []
            for x in rows:
                labels = {l.get("name") for l in (x.get("labels") or [])}
                st = ("running" if "rig-running" in labels
                      else "failed" if "rig-failed" in labels
                      else "queued")
                seen[x.get("number")] = {"id": x.get("number"), "task": x.get("title"), "status": st}
        return list(seen.values())
    # gitlab（glab）はテキスト出力のみで labels が取れないため、ラベルごとに問い合わせて
    # 行単位で dedup・merge する（status は従来どおり "queued" 固定。#211 の可視性回復が主目的）。
    seen_lines: dict[str, dict] = {}
    for label in QUEUE_LABELS_ACTIVE:
        argv = [cli, "issue", "list", "-l", label, "--state", "open"] + R
        rc, out, err = _cli_run(argv)
        if rc != 0:
            return [{"id": None, "task": f"[{cli} error: {(err or '')[:120]}]", "status": "error"}]
        for ln in out.splitlines():
            if ln.strip():
                seen_lines[ln] = {"id": None, "task": ln, "status": "queued"}
    return list(seen_lines.values())


def queue_set_status(backend: str, item_id, status: str, note: str, cfg: dict) -> None:
    if backend == "local":
        q = _local_load()
        for it in q["items"]:
            if str(it["id"]) == str(item_id):
                it["status"] = status; it["note"] = note[:300]
        _local_save(q)
        return
    cli = _gh_cli(backend)
    R = (["-R", cfg["repo"]] if cfg.get("repo") else [])
    label = {"running": "rig-running", "done": "rig-done", "failed": "rig-failed"}.get(status)
    if label:
        _cli_run([cli, "issue", "edit", str(item_id), "--add-label", label,
                  "--remove-label", QUEUE_LABEL] + R)
    if note:
        _cli_run([cli, "issue", "comment", str(item_id), "-b", note] + R)
    if status == "done":
        _cli_run([cli, "issue", "close", str(item_id)] + R)


def cmd_queue(args):
    if not args or args[0] not in ("add", "list", "go", "done"):
        print("[ERROR] usage: queue <add|list|go|done> [...] "
              "[--backend local|github|gitlab] [--repo owner/repo]")
        sys.exit(1)
    sub, rest = args[0], args[1:]
    backend, cfg = "local", {}
    gen, ver, max_parallel = "rig", None, 3
    free = []
    i = 0
    while i < len(rest):
        a = rest[i]
        if a == "--backend" and i + 1 < len(rest):
            backend = rest[i + 1]; i += 2
        elif a == "--repo" and i + 1 < len(rest):
            cfg["repo"] = rest[i + 1]; i += 2
        elif a == "--provider" and i + 1 < len(rest):
            gen = rest[i + 1]; i += 2
        elif a == "--verifier-provider" and i + 1 < len(rest):
            ver = rest[i + 1]; i += 2
        elif a == "--max-parallel" and i + 1 < len(rest):
            max_parallel = int(rest[i + 1]); i += 2
        elif a == "--provider-cmd" and i + 1 < len(rest):
            cfg["provider_cmd"] = rest[i + 1]; i += 2
        else:
            free.append(a); i += 1
    ver = ver or gen

    if sub == "add":
        if not free:
            print("[ERROR] queue add \"<task>\""); sys.exit(1)
        it = queue_add(backend, " ".join(free), cfg)
        print(f"積んだ [{backend}]: #{it['id']} {it['task']}  ({it['status']})"
              + (f" — {it.get('note','')}" if it.get("status") == "error" else ""))
        return
    if sub == "list":
        items = queue_list(backend, cfg)
        print(f"## rig queue [{backend}]  ({len(items)} 件)")
        for it in items:
            print(f"  [{it.get('status','?'):<8}] #{it.get('id')}  {it.get('task')}")
        return
    if sub == "done":
        if not free:
            print("[ERROR] queue done <id>"); sys.exit(1)
        queue_set_status(backend, free[0], "done", "手動で done", cfg)
        print(f"done [{backend}]: #{free[0]}")
        return
    # go: 積まれた task をまとめて実行（独立 task は並列・各 task をゲート）
    items = [it for it in queue_list(backend, cfg) if it.get("status") == "queued"]
    if not items:
        print(f"キューは空です [{backend}]。`queue add` で積んでください。")
        return
    print(f"## rig queue GO [{backend}]  {len(items)} 件 / provider={gen} / 並列={max_parallel}\n")

    def _run_one(it):
        task = it["task"]
        queue_set_status(backend, it["id"], "running", "", cfg)
        rc, out = run_provider(gen, "generator", _build_prompt(
            {"recipe": "queue", "goal": task}, {"id": "task", "instruction": task}), cfg)
        rc2, vout = run_provider(ver, "verifier", _build_verify_prompt(
            {"recipe": "queue"}, {"id": "task"}, out), cfg, persona="queue")
        ok = ("VERDICT: PASS" in vout) and ("VERDICT: FAIL" not in vout)
        queue_set_status(backend, it["id"], "done" if ok else "failed",
                         ("✅ rig: 完了" if ok else "❌ rig: 検証 FAIL") + f"（{gen}→{ver}）", cfg)
        return (it, ok)

    with futures.ThreadPoolExecutor(max_workers=max(1, max_parallel)) as ex:
        results = list(ex.map(_run_one, items))
    done = sum(1 for _, ok in results if ok)
    for it, ok in results:
        print(f"  [{'DONE' if ok else 'FAIL'}] #{it['id']}  {it['task']}")
    print(f"\n=== GO 完了: {done}/{len(results)} done [{backend}] ===")
    sys.exit(0 if done == len(results) else 1)


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


# ── プロバイダ疎通テスト ──────────────────────────────────────────────────────
def cmd_probe(args):
    """プロバイダを1回叩いて、実際のコマンド・出力・契約パースの可否を表示する。
    例: orchestrate.py probe --provider codex          （検証ロールで VERDICT を確認）
        orchestrate.py probe --provider codex --role generator
        orchestrate.py probe --provider ollama --model llama3.1"""
    provider, role, cfg = None, "verifier", {}
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--provider" and i + 1 < len(args):
            provider = args[i + 1]; i += 2
        elif a == "--role" and i + 1 < len(args):
            role = args[i + 1]; i += 2
        elif a == "--model" and i + 1 < len(args):
            cfg["model"] = args[i + 1]; i += 2
        elif a == "--base-url" and i + 1 < len(args):
            cfg["base_url"] = args[i + 1]; i += 2
        elif a == "--provider-cmd" and i + 1 < len(args):
            cfg["provider_cmd"] = args[i + 1]; i += 2
        else:
            i += 1
    if not provider:
        print("[ERROR] --provider <name> が必須（rig|claude|codex|ollama|lmstudio|cmd|mock）")
        sys.exit(1)
    prompt = ("ある成果が受け入れ基準を満たすか判定し、最後に必ず 'VERDICT: PASS' か "
              "'VERDICT: FAIL' を1行で出力してください。\n成果: 2 + 2 = 4"
              if role == "verifier" else
              "1 + 1 を計算し、最後に 'STATUS: done' を出力してください。")
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
    print("  --- 出力（先頭 600 字） ---")
    print("  " + (out or "")[:600].replace("\n", "\n  "))
    print(f"  → {sig} 検出: " + ("✓ パース可能（rig から使えます）" if found
                                else f"✗ 見つからない（プロンプト/フラグ調整が必要。cmd プロバイダで実コマンドを指定可）"))
    sys.exit(0 if (rc == 0 and found) else 1)


def cmd_install_shim(args):
    """~/.local/bin/rig （または --to で指定したパス）に shim を symlink で配置する。
    1 回実行すれば、以降どのディレクトリからでも `rig <subcommand>` で起動できる。"""
    target = pathlib.Path("~/.local/bin/rig").expanduser()
    force = False
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--to" and i + 1 < len(args):
            target = pathlib.Path(args[i + 1]).expanduser(); i += 2
        elif a in ("--force", "-f"):
            force = True; i += 1
        else:
            i += 1
    src = RIG_HOME / ".claude-plugin" / "bin" / "rig"
    if not src.exists():
        print(f"[ERROR] shim 元が見つかりません: {src}")
        sys.exit(1)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() or target.is_symlink():
        if not force:
            print(f"[ERROR] 既に存在: {target}（上書きは --force）")
            sys.exit(1)
        target.unlink()
    target.symlink_to(src)
    print(f"✓ symlink: {target} → {src}")
    path_dirs = (os.environ.get("PATH") or "").split(os.pathsep)
    if str(target.parent) not in path_dirs:
        print(f"⚠ {target.parent} が $PATH に無いようです。次を追加してください：")
        print(f"    export PATH=\"{target.parent}:$PATH\"")
    print(f"確認: `rig models` または `rig --help`（RIG_HOME={RIG_HOME}）")


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
    report("N probe: codex の command", build_argv("codex", "verifier", "P", {}), ["codex", "exec", "P"])
    _, out_n = run_provider("mock", "verifier", "x", {})
    report("N probe: 検証出力に VERDICT", "VERDICT" in out_n, True)
    # O: タスクキュー（local backend で積む→list→mock go・github は CLI 不在で graceful）
    global QUEUE_PATH
    _orig_qp = QUEUE_PATH
    import tempfile
    QUEUE_PATH = pathlib.Path(tempfile.gettempdir()) / "rig_queue_selftest.json"
    QUEUE_PATH.unlink(missing_ok=True)
    queue_add("local", "タスクA", {}); queue_add("local", "タスクB", {})
    q_items = queue_list("local", {})
    for it in q_items:                          # mock で go（生成→検証→done）
        _, vout = run_provider("mock", "verifier", "x", {}, persona="queue")
        queue_set_status("local", it["id"], "done" if "VERDICT: PASS" in vout else "failed", "", {})
    q_done = [it for it in queue_list("local", {}) if it["status"] == "done"]
    gh_item = queue_add("github", "t", {})      # gh 不在 → error（crash しない）
    QUEUE_PATH.unlink(missing_ok=True)
    QUEUE_PATH = _orig_qp
    report("O queue: 2件積んで list", len(q_items), 2)
    report("O queue: mock go で全 done", len(q_done), 2)
    report("O github backend は CLI 不在で graceful(error)", gh_item["status"], "error")
    print("\n" + ("PASS: 決定論オーケストレータは健全" if ok else "FAIL: セルフテスト不一致"))
    sys.exit(0 if ok else 1)


# ── エントリ ──────────────────────────────────────────────────────────────────
COMMANDS = {
    "plan": cmd_plan, "init": cmd_init, "check": cmd_check,
    "verdict": cmd_verdict, "next": cmd_next, "status": cmd_status,
    "run": cmd_run, "models": cmd_models, "probe": cmd_probe, "queue": cmd_queue,
    "install-shim": cmd_install_shim, "selftest": cmd_selftest,
}


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        print(__doc__)
        sys.exit(0 if len(sys.argv) < 2 else 1)
    COMMANDS[sys.argv[1]](sys.argv[2:])


if __name__ == "__main__":
    main()
