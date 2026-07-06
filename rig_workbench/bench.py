"""rig-wb bench — bare vs rig の A/B ベンチマークランナー (MVP)。

同一 LLM で:
  - bare mode: 1 発 `claude -p` / `codex exec` / ollama HTTP 等でタスクを解かせる
  - rig mode:  `rig-wb run <recipe> --provider <same>` で recipe を回す

各 task を /tmp/ 下の scratch worktree に配置し、両モードで解いてから同一 test を実行、
metrics を JSON にまとめる。**既定 provider = `mock`** (フレームワーク動作確認用・課金なし)、
実測は `--provider claude` などを明示。

MVP 制限:
  - 外部 YAML spec は次回
  - --runs N は 1 タスクあたりの反復回数（分散を見たい時）

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
import tempfile
import time
import urllib.request

from . import __version__


# ── 組み込みタスク定義 ─────────────────────────────────────────────────


BUILTIN_TASKS: dict[str, dict] = {
    "divide-by-zero": {
        "difficulty": "simple",
        "files": {
            "buggy.py": (
                "def divide_all(numbers, divisor):\n"
                "    \"\"\"\n"
                "    リストの各要素を divisor で割った結果を返す。\n"
                "    ただし、divisor が 0 の場合は元の要素をそのまま返す。\n"
                "    \"\"\"\n"
                "    result = []\n"
                "    for n in numbers:\n"
                "        result.append(n / divisor)   # BUG: divisor==0 で ZeroDivisionError\n"
                "    return result\n"
            ),
            "test_divide.py": (
                "from buggy import divide_all\n\n"
                "def test_normal():\n"
                "    assert divide_all([10, 20, 30], 2) == [5.0, 10.0, 15.0]\n\n"
                "def test_zero_divisor():\n"
                "    # divisor が 0 なら元の要素をそのまま返す仕様\n"
                "    assert divide_all([1, 2, 3], 0) == [1, 2, 3]\n\n"
                "def test_empty():\n"
                "    assert divide_all([], 5) == []\n"
            ),
        },
        "target_file": "buggy.py",
        "test_cmd": ["python3", "-m", "pytest", "test_divide.py", "--tb=no", "-q"],
        "goal": "buggy.py のバグを直してください。tests は変更禁止。",
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
                "    重複を除去し、初出現の順序を保持したリストを返す。\n"
                "      dedup([3, 1, 2, 1, 3]) == [3, 1, 2]\n"
                "      dedup(['a', 'b', 'a', 'c']) == ['a', 'b', 'c']\n"
                "    \"\"\"\n"
                "    return list(set(items))   # BUG: set() は順序保持しない\n"
            ),
            "test_order_dedup.py": (
                "from order_dedup import dedup\n\n"
                "def test_unique():\n"
                "    # 弱いテスト: 集合として一致するかだけ\n"
                "    assert set(dedup([3, 1, 2, 1, 3])) == {1, 2, 3}\n\n"
                "def test_length():\n"
                "    assert len(dedup([3, 1, 2, 1, 3])) == 3\n"
            ),
        },
        "target_file": "order_dedup.py",
        "test_cmd": ["python3", "-m", "pytest", "test_order_dedup.py", "--tb=no", "-q"],
        "goal": "order_dedup.py にバグがあるかもしれません。修正が必要なら直してください。tests は変更禁止。",
        "spec_check_code": (
            "from order_dedup import dedup\n"
            "assert dedup([3, 1, 2, 1, 3]) == [3, 1, 2], 'spec violation (順序保持)'"
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
                "    \"\"\"users テーブルから name で 1 行返す。\n"
                "    ヒント: name はユーザー入力（信頼できない）。\n"
                "    \"\"\"\n"
                "    # BUG: 文字列連結による SQL injection の温床\n"
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
            "userdb.py にセキュリティ上の問題があります。修正してください。"
            "tests は変更禁止。修正した userdb.py の全文だけを ```python ... ``` 形式で返してください。"
        ),
        "spec_check_code": (
            # 悪意入力での DROP TABLE を実行させないか / 引用符の脱出で管理者化しないか
            "import sqlite3\n"
            "from userdb import get_user_by_name\n"
            "c = sqlite3.connect(':memory:')\n"
            "c.execute(\"CREATE TABLE users(id INTEGER, name TEXT, role TEXT)\")\n"
            "c.execute(\"INSERT INTO users VALUES(1, 'alice', 'admin')\")\n"
            "# SQL injection 試行 1: users テーブルの中身を全部返そうとする\n"
            "r1 = get_user_by_name(c, \"' OR '1'='1\")\n"
            "assert r1 is None, f'SQL injection 通ってる: {r1}'\n"
            "# SQL injection 試行 2: 別テーブルを消そうとする\n"
            "try:\n"
            "    get_user_by_name(c, \"'; DROP TABLE users; --\")\n"
            "except Exception:\n"
            "    pass\n"
            "assert c.execute(\"SELECT COUNT(*) FROM users\").fetchone()[0] == 1, "
            "'DROP TABLE 通ってる'\n"
        ),
        "mock_fix": (
            "import sqlite3\n\n"
            "def get_user_by_name(conn: sqlite3.Connection, name: str) -> tuple | None:\n"
            "    \"\"\"users テーブルから name で 1 行返す。parametrized query 使用。\"\"\"\n"
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
                "    \"\"\"国内配送料。0.5kg 刻みで切り上げ、1 単位 200 円、下限 500 円。\"\"\"\n"
                "    import math\n"
                "    units = math.ceil(weight_kg / 0.5)\n"
                "    return max(500, units * 200)\n\n"
                "def price_domestic_cool(weight_kg: float) -> int:\n"
                "    \"\"\"国内クール便。基本は price_domestic と同じロジックだが単価 300 円、下限 800 円。\"\"\"\n"
                "    # BUG: price_domestic と重複したロジックが手書きされ、しかも切り上げが天井関数抜け\n"
                "    units = int(weight_kg / 0.5)   # ← ここが切り捨てで仕様と合わない\n"
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
                "    # 弱い test: 中間値は書かれてない\n"
            ),
        },
        "target_file": "shipping.py",
        "test_cmd": ["python3", "-m", "pytest", "test_shipping.py", "--tb=no", "-q"],
        "goal": (
            "shipping.py にバグと重複コードがあります。修正してください。"
            "tests は変更禁止。修正した shipping.py の全文だけを ```python ... ``` 形式で返してください。"
        ),
        "spec_check_code": (
            "from shipping import price_domestic_cool\n"
            "# 仕様: 0.5kg 刻みで切り上げ、単価 300、下限 800\n"
            "assert price_domestic_cool(1.1) == 900, "
            "f'切り上げ抜けバグ残ってる: got {price_domestic_cool(1.1)} expected 900'\n"
            "assert price_domestic_cool(2.0) == 1200, "
            "f'{price_domestic_cool(2.0)} != 1200'\n"
        ),
        "mock_fix": (
            "import math\n\n"
            "def _price(weight_kg: float, unit_price: int, floor: int) -> int:\n"
            "    units = math.ceil(weight_kg / 0.5)\n"
            "    return max(floor, units * unit_price)\n\n"
            "def price_domestic(weight_kg: float) -> int:\n"
            "    \"\"\"国内配送料。0.5kg 刻みで切り上げ、1 単位 200 円、下限 500 円。\"\"\"\n"
            "    return _price(weight_kg, 200, 500)\n\n"
            "def price_domestic_cool(weight_kg: float) -> int:\n"
            "    \"\"\"国内クール便。単価 300 円、下限 800 円。\"\"\"\n"
            "    return _price(weight_kg, 300, 800)\n"
        ),
    },
}


_MOCK_FIX_FOR: dict[str, str] = {}   # 廃止 (task[\"mock_fix\"] を使う)


# ── task setup ─────────────────────────────────────────────────────────


def _setup_task_dir(task: dict) -> pathlib.Path:
    """task の files を新規 tmp dir に展開し、git 初期化＋初回 commit まで済ませる。"""
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
    # __pycache__ は無視
    for pc in d.rglob("__pycache__"):
        shutil.rmtree(pc, ignore_errors=True)


def _run_tests(task: dict, d: pathlib.Path) -> dict:
    r = subprocess.run(task["test_cmd"], cwd=d, capture_output=True, text=True, timeout=60)
    passed = int((re.search(r"(\d+) passed", r.stdout) or ["0", "0"])[1] if hasattr(re.search(r"(\d+) passed", r.stdout), "group") else 0)
    # 上のワンライナが読みにくいので愚直に
    m_p = re.search(r"(\d+) passed", r.stdout)
    m_f = re.search(r"(\d+) failed", r.stdout)
    passed = int(m_p.group(1)) if m_p else 0
    failed = int(m_f.group(1)) if m_f else 0
    return {"passed": passed, "failed": failed, "exit": r.returncode}


def _spec_check(task: dict, d: pathlib.Path) -> str:
    """spec_check_code を run。PASS / FAIL: <detail> を返す。"""
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


# ── mode: bare ─────────────────────────────────────────────────────────


def _build_bare_prompt(task: dict) -> str:
    files_text = "\n\n".join(
        f"# {name}\n{content}" for name, content in task["files"].items()
    )
    return (
        f"{task['goal']}\n\n"
        f"修正した {task['target_file']} の全文だけを ```python ... ``` 形式で返してください。他の説明は不要。\n\n"
        f"{files_text}"
    )


def _extract_code(text: str) -> str:
    m = re.search(r"```(?:python)?\s*(.*?)```", text, re.DOTALL)
    return (m.group(1) if m else text).strip()


def _call_provider(provider: str, prompt: str, model: str | None, allow_headless_in_cc: bool,
                    mock_fix: str = "") -> tuple[str, float]:
    """provider に prompt を投げて (response_text, elapsed_s) を返す。

    provider="mock" 時は `mock_fix` を code として返す（framework 動作確認用・LLM 呼ばない）。
    """
    t0 = time.time()
    if provider == "claude":
        if not allow_headless_in_cc and (os.environ.get("CLAUDECODE") or os.environ.get("CLAUDE_CODE_SESSION_ID")):
            raise SystemExit(
                "[bench] Claude Code 内から --provider claude は既定で BLOCK (課金安全)。"
                "実測なら --allow-headless-in-cc を明示してください。"
            )
        argv = ["claude", "-p", prompt, "--output-format", "text"]
        if model:
            argv += ["--model", model]
        r = subprocess.run(argv, capture_output=True, text=True, timeout=300)
        return r.stdout, time.time() - t0
    if provider == "codex":
        argv = ["codex", "exec", "--skip-git-repo-check"]
        if model:
            argv += ["-m", model]
        argv += [prompt]
        r = subprocess.run(argv, capture_output=True, text=True, timeout=300)
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
    raise SystemExit(f"[bench] 未知の provider: {provider}")


# ── mode: rig ──────────────────────────────────────────────────────────


def _rig_run(task: dict, workdir: pathlib.Path, provider: str, model: str | None,
             allow_headless_in_cc: bool, max_steps: int) -> tuple[str, float]:
    """rig-wb run bugfix を workdir で実行し、(stdout, elapsed_s) を返す。"""
    goal = task["goal"]
    env = dict(os.environ)
    # RIG_HOME が無ければパッケージ位置から推定
    if not env.get("RIG_HOME"):
        # rig_workbench/ の親を仮に repo root にする（開発版でしか通らない・pip 版は要 export）
        candidate = pathlib.Path(__file__).resolve().parent.parent
        if (candidate / "scripts" / "orchestrate.py").exists():
            env["RIG_HOME"] = str(candidate)
    t0 = time.time()
    argv = ["rig-wb", "run", "bugfix", "--provider", provider,
            "--max-steps", str(max_steps), "--out", str(workdir / "run-state.json"),
            "--goal", goal]
    if allow_headless_in_cc:
        argv += ["--allow-headless-in-cc"]
    if model:
        argv += ["--model", model]
    r = subprocess.run(argv, cwd=workdir, capture_output=True, text=True, timeout=1800, env=env)
    return r.stdout + r.stderr, time.time() - t0


def _rig_read_state(workdir: pathlib.Path) -> dict:
    sp = workdir / "run-state.json"
    if not sp.exists():
        return {}
    return json.loads(sp.read_text(encoding="utf-8"))


# mock provider の正解は task["mock_fix"] に定義済み（重複ブロック削除）


# ── 単一 task の bench 実行 ────────────────────────────────────────────


def _bench_task(task_id: str, task: dict, args: argparse.Namespace) -> dict:
    results = {"task_id": task_id, "difficulty": task["difficulty"], "runs": []}
    for run_idx in range(args.runs):
        run_result: dict = {"run": run_idx + 1, "modes": {}}

        # ── BARE ──
        if args.mode in ("both", "bare"):
            d = _setup_task_dir(task)
            try:
                prompt = _build_bare_prompt(task)
                resp, elapsed = _call_provider(args.provider, prompt, args.model,
                                                args.allow_headless_in_cc,
                                                mock_fix=task.get("mock_fix", ""))
                code = _extract_code(resp)
                (d / task["target_file"]).write_text(code, encoding="utf-8")
                t = _run_tests(task, d)
                sc = _spec_check(task, d)
                ud = _unrelated_diff(d, task["target_file"])
                run_result["modes"]["bare"] = {
                    "elapsed_s": round(elapsed, 1),
                    "calls": 1,
                    "test_pass": t["failed"] == 0 and t["passed"] > 0 and t["exit"] == 0,
                    "test_stats": t,
                    "spec_check": sc,
                    "unrelated_files": ud,
                    "gate_verdict": None,
                }
            finally:
                shutil.rmtree(d, ignore_errors=True)

        # ── RIG ──
        if args.mode in ("both", "rig"):
            d = _setup_task_dir(task)
            try:
                out, elapsed = _rig_run(task, d, args.provider, args.model,
                                        args.allow_headless_in_cc, args.max_steps)
                state = _rig_read_state(d)
                # step 数を calls の代理として使う
                calls = sum(1 for s in state.get("step_state", {}).values()
                            if s.get("status") in ("passed", "running"))
                t = _run_tests(task, d)
                sc = _spec_check(task, d)
                ud = _unrelated_diff(d, task["target_file"])
                # gate_verdict は review-diff / acceptance の verdict 集約
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
                    "gate_verdict": gate,
                    "reached_steps": [sid for sid, st in state.get("step_state", {}).items()
                                      if st.get("status") == "passed"],
                }
            finally:
                shutil.rmtree(d, ignore_errors=True)

        results["runs"].append(run_result)
    return results


# ── entry ──────────────────────────────────────────────────────────────


def cmd_bench(argv: list[str]) -> None:
    p = argparse.ArgumentParser(prog="rig-wb bench",
                                description="rig-wb bench — bare vs rig の A/B ベンチマーク (MVP)")
    p.add_argument("--tasks", nargs="+", choices=list(BUILTIN_TASKS.keys()) + ["all"],
                   default=["all"], help="実行タスク (default: all)")
    p.add_argument("--mode", choices=["bare", "rig", "both"], default="both")
    p.add_argument("--provider", default="mock",
                   help="claude / codex / ollama / lmstudio / mock (default: mock・課金なし)")
    p.add_argument("--model", help="model 名 (--provider claude|codex|ollama|lmstudio 用)")
    p.add_argument("--runs", type=int, default=1, help="1 タスクあたりの反復 (default: 1)")
    p.add_argument("--max-steps", type=int, default=14,
                   help="rig mode の max-steps (default: 14 — bugfix 全 step 到達を狙う)")
    p.add_argument("--html", help="HTML dashboard の出力先 (bench 結果を可視化)")
    p.add_argument("--allow-headless-in-cc", action="store_true",
                   help="Claude Code 内から claude/rig provider を使う場合の opt-in")
    p.add_argument("--out", help="JSON 出力ファイル (省略時 stdout)")
    args = p.parse_args(argv)

    task_ids = list(BUILTIN_TASKS.keys()) if args.tasks == ["all"] or "all" in args.tasks else args.tasks

    summary: dict = {
        "generated": datetime.datetime.now().isoformat(timespec="seconds"),
        "rig_wb_version": __version__,
        "provider": args.provider,
        "model": args.model,
        "runs_per_task": args.runs,
        "tasks": [],
    }
    for tid in task_ids:
        print(f"\n=== task: {tid} ({BUILTIN_TASKS[tid]['difficulty']}) ===", flush=True)
        r = _bench_task(tid, BUILTIN_TASKS[tid], args)
        summary["tasks"].append(r)
        # 要点だけ即時表示
        for run in r["runs"]:
            for mode, m in run["modes"].items():
                print(f"  run={run['run']} mode={mode:5s}  "
                      f"elapsed={m['elapsed_s']}s  calls={m['calls']}  "
                      f"test_pass={m['test_pass']}  spec={m['spec_check']}  "
                      f"unrelated={len(m['unrelated_files'])}  "
                      f"gate={m.get('gate_verdict', '-')}", flush=True)

    out_text = json.dumps(summary, ensure_ascii=False, indent=2)
    if args.out:
        pathlib.Path(args.out).write_text(out_text, encoding="utf-8")
        print(f"\n書き込み: {args.out}")
    else:
        print("\n" + out_text)

    if args.html:
        html = _render_html(summary)
        pathlib.Path(args.html).write_text(html, encoding="utf-8")
        print(f"HTML: {args.html}")


# ── HTML dashboard ─────────────────────────────────────────────────────


def _render_html(summary: dict) -> str:
    """bench 結果を単一 HTML で可視化する。stdlib のみ・外部依存なし。"""
    import html as _html

    def esc(s) -> str:
        return _html.escape(str(s), quote=True)

    tasks = summary.get("tasks", [])

    # 集計: bare vs rig の平均 elapsed / calls / test_pass 率 / spec_pass 率
    def aggregate(mode: str) -> dict:
        elapsed, calls, tests, specs, n = [], [], 0, 0, 0
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
                n += 1
        if n == 0:
            return {"n": 0}
        return {
            "n": n,
            "elapsed_avg": round(sum(elapsed) / n, 1),
            "calls_avg": round(sum(calls) / n, 1),
            "test_pass_rate": round(tests / n * 100, 0),
            "spec_pass_rate": round(specs / n * 100, 0),
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

    # per-task 表
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
            rows.append(
                f'<tr>'
                f'<td>{esc(t["task_id"])}<div class="sub">{esc(t["difficulty"])}</div></td>'
                f'<td>{cell(bare, "elapsed_s")}s</td><td>{cell(rig, "elapsed_s")}s</td>'
                f'<td>{cell(bare, "calls")}</td><td>{cell(rig, "calls")}</td>'
                f'<td>{testcell(bare)}</td><td>{testcell(rig)}</td>'
                f'<td>{speccell(bare)}</td><td>{speccell(rig)}</td>'
                f'<td>{cell(bare, "unrelated_files")}</td><td>{cell(rig, "unrelated_files")}</td>'
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
        '<th>bare unrel</th><th>rig unrel</th>'
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
        "<!doctype html><html lang='ja'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<title>rig-wb bench dashboard</title>"
        f"<style>{css}</style></head><body>"
        "<h1>rig-wb bench dashboard</h1>"
        f"{meta}"
        "<div class='kpi-grid'>"
        f"{kpi('平均 elapsed (s)', kpi_bare_avg_e, kpi_rig_avg_e)}"
        f"{kpi('平均 calls', kpi_bare_avg_c, kpi_rig_avg_c)}"
        f"{kpi('test pass 率 (%)', kpi_bare_tp, kpi_rig_tp)}"
        f"{kpi('spec pass 率 (%)', kpi_bare_sp, kpi_rig_sp)}"
        "</div>"
        f"{table}"
        "<footer>rig-wb bench · <code>rig-wb bench --html &lt;path&gt;</code></footer>"
        "</body></html>"
    )
