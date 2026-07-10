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

# ── 実行層（外部ランナー・プロバイダ抽象）──────────────────────────────────────
# 各 step を「別プロセスのエージェント」で実行する＝プロセス境界で context を隔離。
# 検証は「別プロバイダ/別プロセス」で回す＝構造的に採点者≠生成者。
# 既定プロバイダは無し（明示必須）。本物の claude/codex は配線のみ、テストは mock。

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
    "target = re.search(r'対象ファイル: ([^\\s]+)', prompt)\n"
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
    "    if 'order-dedup' in text or 'dedup(' in text or '順序保持' in text:\n"
    "        return 'def dedup(items):\\n    return list(dict.fromkeys(items))\\n'\n"
    "    if 'sql-inject' in text or 'SQL injection' in text or 'get_user_by_name' in text:\n"
    "        return (\n"
    "            'import sqlite3\\n\\n'\n"
    "            'def get_user_by_name(conn: sqlite3.Connection, name: str) -> tuple | None:\\n'\n"
    "            '    cur = conn.cursor()\\n'\n"
    "            '    cur.execute(\"SELECT id, name, role FROM users WHERE name = ?\", (name,))\\n'\n"
    "            '    return cur.fetchone()\\n'\n"
    "        )\n"
    "    if 'dry-refactor' in text or '切り上げ抜け' in text or 'price_domestic_cool' in text:\n"
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
    "    print('独立検証（mock）: ' + persona)\n"
    "    print('VERDICT: ' + ('FAIL' if 'fail' in persona else 'PASS'))\n"
    "else:\n"
    "    if step_id == 'implement' and target_file:\n"
    "        fix = fix_for(prompt)\n"
    "        if fix:\n"
    "            write(target_file, fix)\n"
    "    print('## step 実行結果（mock）')\n"
    "    print('STATUS: done')\n"
)

RIG_GEN_PREFIX = ("`rig` skill を Skill ツールで起動し、その engine（PARSE→RESOLVE→COMPOSE→RUN・"
                  "context-minimal）に従って次の step を実行してください。\n")
RIG_VER_PREFIX = ("`rig` skill を Skill ツールで起動し、独立した検証者として（この step を生成した"
                  "エージェントとは別プロセス）受け入れ基準を判定し、最後に必ず 'VERDICT: PASS' か "
                  "'VERDICT: FAIL' を出力してください。\n")

# 採点者≠生成者を「別プロセス」からさらに一段強制する：verifier ロールの CLI には
# **読み取り専用の権限フラグを argv で固定付与**する（プロンプトのお願いではなく機構）。
# 検証役は書けない＝レビュー中の成果物改変・自己修正の混入が構造的に起きない。
_READONLY_ENFCE = {
    "claude": ["--allowedTools", "Read,Grep,Glob"],   # headless の tool 許可リスト
    "codex":  ["--sandbox", "read-only"],              # codex exec のサンドボックス
}


def build_argv(provider: str, role: str, prompt: str, cfg: dict, persona: str = "") -> list[str]:
    if provider == "mock":
        return ["python3", "-c", MOCK_SRC, role, persona]
    if provider == "rig":
        # 各 step を「rig ハーネス」として claude ヘッドレスで起動（rig を名前で呼ぶ）。
        pre = RIG_VER_PREFIX if role == "verifier" else RIG_GEN_PREFIX
        argv = ["claude", "-p", pre + prompt, "--output-format", "text"]
        if cfg.get("model"):
            argv += ["--model", cfg["model"]]              # per-step model 対応
        return argv + _READONLY_ENFCE["claude"] if role == "verifier" else argv
    if provider == "claude":
        # ヘッドレス。実運用は権限モード等をユーザーが --provider-cmd で調整可。
        argv = ["claude", "-p", prompt, "--output-format", "text"]
        if cfg.get("model"):
            argv += ["--model", cfg["model"]]              # per-step model 対応
        return argv + _READONLY_ENFCE["claude"] if role == "verifier" else argv
    if provider == "codex":
        # --skip-git-repo-check: 非 git ディレクトリ（横断利用の overlay 先など）でも
        # codex が起動拒否しないように。サンドボックスは無効化しないので安全。
        argv = ["codex", "exec", "--skip-git-repo-check"]
        argv += ["--sandbox", "workspace-write" if role == "generator" else "read-only"]
        if cfg.get("model"):
            argv += ["-m", cfg["model"]]                   # per-step model 対応
        return argv + [prompt]
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
        # --base-url 明示時は、その endpoint と一致する保存設定だけを使う。
        # 別 endpoint の古い default で実機探索が汚染されるのを避ける。
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
    if "VERDICT: FAIL" in up or "判定: REJECT" in text:
        return False
    if "VERDICT: PASS" in up:
        return True
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("判定:"):
            continue
        verdict = line.split(":", 1)[1].strip().upper()
        return verdict in ("APPROVE", "APPROVE_WITH_CONDITIONS")
    return False


def run_verifiers_parallel(ver, prompt: str, personas: list[str],
                           cfg: dict, max_parallel: int) -> list[dict]:
    """N 人の検証者を同時プロセスで走らせ、(persona, provider) 順（決定論）に結果を返す。

    ver に list を渡すと**同じ persona を複数プロバイダで**走らせる＝モデル混成クォーラム
    （同型モデル N 票より異種票の方が相関が低い。不一致そのものがシグナル）。票の by は
    "provider:persona" 形式でテレメトリに残り、runs --personas でモデル別に監査できる。"""
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
    return sorted(res, key=lambda r: (r["persona"], r["provider"]))  # 完了順に依らず決定論


def _build_step_contract(state: dict, step: dict, st: dict | None = None) -> str:
    lines = [
        f"recipe: {state['recipe']}",
        f"step: {step['id']} ({step['instruction']})",
        f"goal: {state.get('goal') or '(なし)'}",
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
            "must: 実際にコードを編集すること。読むだけで終わらない。",
            "must: 変更は最小限に絞る。無関係な整形や広いリファクタは禁止。",
            "must: tests を変更しない。変更が必要なら理由を明示する。",
            "must: 差分が出るまで作業を続ける。no-op のまま終了しない。",
            "must: 可能な範囲で関連する test / lint を実行し、結果を確認する。",
            "report: CHANGED_FILES / COMMANDS_RUN / RESULT を簡潔に出す。",
        ]
    elif step["id"] == "test":
        lines += [
            "must: 実際に test コマンドを実行すること。",
            "must: 失敗したら原因を特定し、最小修正して再実行する。",
            "must: まだ失敗しているなら、次に何を変えるかを1行で明示する。",
            "must: pass / fail と実行したコマンドを明示する。",
            "report: COMMANDS_RUN / RESULT / REMAINING_RISK を簡潔に出す。",
        ]
    elif step["id"] == "acceptance":
        criteria = step.get("acceptance") or []
        lines += [
            "must: 最終確認のみを行い、受け入れ基準を機械的に照合する。",
            "must: 変更内容とテスト結果が基準を満たすかを明示する。",
            "must: 未達なら、何が不足かを具体的に書く。",
        ]
        if criteria:
            lines.append("acceptance_criteria:")
            lines.extend([f"- {c}" for c in criteria])
    else:
        lines += [
            "must: 依頼を実際に前進させる。分析だけで終わらない。",
        ]
    return "\n".join(lines)


def _build_prompt(state: dict, step: dict, st: dict | None = None) -> str:
    contract = _build_step_contract(state, step, st)
    return (
        f"あなたは rig のサブエージェント（{step['id']} 担当）。\n"
        f"{contract}\n"
        "出力は簡潔に。作業を完了したら最後に必ず 'STATUS: done' を出力。"
    )


def _build_verify_prompt(state: dict, step: dict, product: str) -> str:
    return (
        f"あなたは独立した検証者です（この step を生成したエージェントとは別プロセス・別ロール）。\n"
        f"step '{step['id']}' の成果が受け入れ基準を満たすか判定してください。\n"
        "出力の最後の1行は必ず次のどちらかだけにしてください:\n"
        "VERDICT: PASS\n"
        "VERDICT: FAIL\n"
        "説明はその前に短く書いて構いません。最後の1行に余計な文字、Markdown、句読点を付けないでください。\n"
        f"--- 成果 ---\n{product[:2000]}"
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
    """単独 or judge-panel で生成。複数なら全 generator を並列に走らせ、
    judge(ver) が最初に PASS した候補（generator 列の順＝決定論）を勝者に選ぶ。
    返り値: (winner_provider | None, product, judged[])。
    per-step の `model:` / `verifier_model:` があれば cfg のコピーに inject する（並列安全）。"""
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
    cands.sort(key=lambda c: gen_list.index(c["provider"]))   # 生成順で評価＝決定論
    judged, winner, product = [], None, cands[0]["out"]
    jver = ver[0] if isinstance(ver, list) else ver            # judge は先頭 verifier プロバイダ
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
        _run_step_checks(step, st, cfg)
        log(f"   ↳ checks: {sum(c['ok'] for c in st['checks'])}/{len(st['checks'])} ok")
        return
    if step["gate"] not in ("acceptance-gate", "review-gate"):
        return
    ver_label = "+".join(ver) if isinstance(ver, list) else ver
    if judged:
        # judge-panel は judge が選定＝そのゲート判定を採用（勝者が居れば合格）
        st["verdicts"].append({"by": f"{ver_label}:judge-panel", "ok": winner is not None,
                               "note": "winner=" + str(winner)})
        return
    # 観点検証＝N 人の独立レビュアーを並列プロセスで（採点者≠生成者）
    # per-step の `verifier_model:` があれば cfg のコピーに inject（generator 側とは独立）
    v_cfg = {**cfg, "model": step["verifier_model"] or step.get("model") or cfg.get("model")} \
            if (step.get("verifier_model") or step.get("model")) else cfg
    personas = step["personas"] or ["independent"]
    results = run_verifiers_parallel(ver, _build_verify_prompt(state, step, out),
                                     personas, v_cfg, max_parallel)
    passes, total = sum(1 for r in results if r["ok"]), len(results)
    par = "並列" if total > 1 else "単独"
    log(f"   ↳ {par}検証 {total} 人: PASS {passes}/{total}（quorum={quorum}）")
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
    """自走ループ。step に needs: があれば DAG 並列モードへ自動切替（独立 step を同時実行）。"""
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

