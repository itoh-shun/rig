"""rig-wb bench — bare vs rig の A/B ベンチマークランナー (MVP)。

同一 LLM で:
  - bare mode: 1 発 `claude -p` / `codex exec` / ollama HTTP 等でタスクを解かせる
  - rig mode:  `rig-wb run <recipe> --provider <same>` で recipe を回す

各 task を /tmp/ 下の scratch worktree に配置し、両モードで解いてから同一 test を実行、
metrics を JSON にまとめる。**既定 provider = `mock`** (フレームワーク動作確認用・課金なし)、
実測は `--provider claude` などを明示。

MVP 制限:
  - 組み込みタスク 2 件のみ（外部 YAML spec は次回）
  - --runs N は 1 タスクあたりの反復回数（分散を見たい時）
  - HTML report は次回

Usage:
    rig-wb bench --provider claude --allow-headless-in-cc --out /tmp/bench.json
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
    p.add_argument("--max-steps", type=int, default=7, help="rig mode の max-steps (default: 7)")
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
