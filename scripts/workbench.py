#!/usr/bin/env python3
"""
rig workbench — 品質保証つき AI 作業環境の決定論ランナー

`/rig "<task>"` 統一入口（facets/instructions/workbench.md）の裏で、
**状態管理・隔離 worktree・acceptance-gate 判定・accept/discard の安全**をコードが強制する。
タスク分類・recipe 選択・実装・レビューはモデルの仕事、状態と安全はこのスクリプトの仕事
（patterns/computational-orchestration と同じ「舵をコードが握る」思想の workbench 版）。

状態は `<repo>/.rig/runs/<task-id>/` に永続化する:
  task.json        タスクの正準メタ（入力・分類・base branch・worktree path・状態）
  steps.json       実行 step の進行状態
  acceptance.json  acceptance-gate の基準と合否（{task_id, status, checks[]}）
  review.json      review 系タスクの persona 別 verdict（stats のゴム印検知に使用・任意）
  plan.md / diff.md / log.md / final.md   モデルが書く散文成果物（本スクリプトは触らない。
                                          diff.md は `## Summary` / `## Risk` / `## Tests` /
                                          `## Unrelated diff` の見出しを持つと `diff` が構造化表示する）

終了コード: 0=成功 / 1=エラー（accept のゲート未達・worktree 不整合を含む）
依存: 標準ライブラリのみ（PyYAML 不要）
"""

import argparse
import contextlib
import datetime
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys

try:
    import fcntl  # POSIX: task 同時操作の排他 (task_lock)
except ImportError:
    fcntl = None  # type: ignore[assignment]  # Windows fallback（lock 無効）
from collections import Counter

# ── acceptance-gate プリセット（正本。instruction はここを参照する）───────────────
GATE_PRESETS: dict[str, list[str]] = {
    # 全 task_type 共通の標準 gate
    "standard": [
        "task_intent_satisfied",
        "no_unrelated_diff",
        "diff_summary_written",
        "risk_summary_written",
        "tests_pass_or_explained",
        "no_type_errors_or_explained",
        "no_secret_leak",
        "no_destructive_operation",
    ],
    # bugfix 専用（standard に上乗せ）
    "bugfix": [
        "bug_cause_identified",
        "fix_is_minimal",
        "regression_test_added_or_explained",
        "existing_behavior_preserved",
        "no_unrelated_refactor",
    ],
    # feature 専用（standard に上乗せ）
    "feature": [
        "requirement_summary_written",
        "implementation_matches_requirement",
        "tests_added_or_explained",
        "public_api_changes_documented",
        "migration_or_backward_compatibility_considered",
    ],
    # refactor 専用（standard に上乗せ）
    "refactor": [
        "behavior_boundaries_identified",
        "no_unintended_behavior_change",
        "tests_confirm_behavior_preserved",
        "no_unrelated_refactor",
        "public_api_changes_documented_if_any",
    ],
    # レビュータスク用（差分を作らないため standard を含まない）
    "review": [
        "findings_are_concrete",
        "severity_labeled",
        "file_references_included",
        "blocking_and_non_blocking_separated",
        "false_positive_risk_considered",
    ],
    # セキュリティ確認用（review に上乗せ）
    "security": [
        "authn_authz_impact_checked",
        "user_input_flow_checked",
        "secret_exposure_checked",
        "unsafe_eval_or_shell_checked",
        "dependency_risk_checked",
    ],
}

# task_type → 適用 gate プリセット（合成順に列挙。先頭が base、以降は上乗せ）
TASK_TYPES: dict[str, list[str]] = {
    "bugfix": ["standard", "bugfix"],
    "feature": ["standard", "feature"],
    "refactor": ["standard", "refactor"],
    "test": ["standard", "feature"],
    "performance": ["standard", "bugfix"],
    "documentation": ["standard"],
    "design": ["standard"],
    "investigation": ["standard"],
    "release_support": ["standard"],
    "review": ["review"],
    "security_review": ["review", "security"],
}

VALID_STEP_STATUS = ("pending", "running", "passed", "failed", "skipped")
VALID_CRITERION_STATUS = ("pending", "passed", "failed", "warning", "skipped")
VALID_VERDICT = ("APPROVE", "REJECT", "APPROVE_WITH_CONDITIONS")

STEP_ICON = {"passed": "✓", "failed": "✗", "running": "▸", "pending": "…", "skipped": "-"}
CHECK_ICON = {"passed": "✓", "failed": "✗", "warning": "⚠", "pending": "…", "skipped": "-"}

NEXT_ACTIONS = {
    "running": "実行中。完了後に gate を判定してください（workbench.py gate <id> --set …）",
    "gate_passed": "/rig diff で差分確認 → /rig accept で反映（または /rig discard で破棄）",
    "gate_failed": "未達基準を修正して gate を再判定（failed のままなら /rig discard）",
    "accepted": "git diff --staged を確認してコミット → /rig discard <id> で worktree を後片付け",
    "discarded": "終了済み（run log のみ保持）",
}

RECOMMENDATION = {
    "failed": "Fix the failed acceptance-gate criteria before accept（`workbench.py gate` で確認）。",
    "pending": "残りの acceptance 基準を判定してから accept してください。",
    "passed_with_warnings": "警告内容を確認したうえで、問題なければ accept してください。",
    "passed": "accept して問題ありません。",
    "skipped": "この task には gate 基準が設定されていません — 手動で確認してから accept してください。",
}


def now_iso() -> str:
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def die(msg: str) -> "NoReturn":  # noqa: F821
    print(f"[ERROR] {msg}", file=sys.stderr)
    sys.exit(1)


def warn(msg: str) -> None:
    print(f"[WARN] {msg}")


# ── git ヘルパー ──────────────────────────────────────────────────────────────
def git(args: list[str], cwd: pathlib.Path | None = None, check: bool = True) -> subprocess.CompletedProcess:
    proc = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    if check and proc.returncode != 0:
        die(f"git {' '.join(args)} が失敗しました: {proc.stderr.strip()}")
    return proc


def repo_root() -> pathlib.Path:
    proc = git(["rev-parse", "--show-toplevel"], check=False)
    if proc.returncode != 0:
        die("git リポジトリ内で実行してください")
    return pathlib.Path(proc.stdout.strip())


def current_branch(root: pathlib.Path) -> str:
    return git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=root).stdout.strip()


def runs_dir(root: pathlib.Path) -> pathlib.Path:
    return root / ".rig" / "runs"


def audit_path(root: pathlib.Path) -> pathlib.Path:
    return root / ".rig" / "audit.jsonl"


def locks_dir(root: pathlib.Path) -> pathlib.Path:
    return root / ".rig" / "locks"


@contextlib.contextmanager
def task_lock(root: pathlib.Path, task_id: str):
    """task 単位の排他制御（`accept`/`discard`/`gate`/`step`/`review` の同時実行を防ぐ）。

    fcntl.flock による非ブロッキング取得。取得失敗時は他プロセスが同 task を触っている
    ことが確定なので、`die` で明示エラー（サイレントに競合させない）。ロックは
    プロセス終了で自動解放（flock は fd tied なので kill 時も残らない）。
    fcntl 不在（Windows 等）ではロックせず素通り＝WSL/Linux での並列 rig:queue go の
    安全網。ファイルは残置（`.rig/` は gitignore 済み・空ファイル）。
    """
    if fcntl is None:
        yield
        return
    ld = locks_dir(root)
    ld.mkdir(parents=True, exist_ok=True)
    lock_file = ld / f"{task_id}.lock"
    with lock_file.open("a") as f:
        try:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            die(f"task '{task_id}' は他のプロセスが操作中です（{lock_file.relative_to(root)}）。"
                "完了を待つか、詰まっているなら該当プロセスを確認してください")
        try:
            yield
        finally:
            try:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass


def audit_append(root: pathlib.Path, event: dict) -> None:
    """`.rig/audit.jsonl` に 1 行 JSON で追記する。

    accept_requirements の force-proof を補う「gate 未達 --force 上書き」の恒久記録。
    差別化ポイントの物理的強度を可視化するための証拠ログ。読み取りは `workbench.py audit`。
    書き込み失敗はサイレントに握りつぶす（telemetry と同様の best-effort）。
    """
    try:
        p = audit_path(root)
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
    except Exception:
        pass


# ── run-state I/O ────────────────────────────────────────────────────────────
def run_dir(root: pathlib.Path, task_id: str) -> pathlib.Path:
    d = runs_dir(root) / task_id
    if not d.is_dir():
        die(f"task '{task_id}' が見つかりません（{d.relative_to(root)}）。`workbench.py log` で一覧できます")
    return d


def load_json(path: pathlib.Path, default: dict | None = None) -> dict:
    if not path.exists():
        if default is not None:
            return default
        die(f"{path} が存在しません")
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path: pathlib.Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def load_task(root: pathlib.Path, task_id: str) -> tuple[pathlib.Path, dict]:
    d = run_dir(root, task_id)
    return d, load_json(d / "task.json")


def save_task(d: pathlib.Path, task: dict) -> None:
    task["updated_at"] = now_iso()
    save_json(d / "task.json", task)


def latest_task_id(root: pathlib.Path) -> str | None:
    base = runs_dir(root)
    if not base.is_dir():
        return None
    candidates = sorted((p.name for p in base.iterdir() if (p / "task.json").exists()), reverse=True)
    return candidates[0] if candidates else None


def resolve_task_id(root: pathlib.Path, given: str | None) -> str:
    if given:
        return given
    tid = latest_task_id(root)
    if not tid:
        die("実行履歴がありません（.rig/runs/ が空）。まず `/rig \"<task>\"` を実行してください")
    return tid


# ── task-id / slug ───────────────────────────────────────────────────────────
def make_slug(text: str) -> str:
    words = re.findall(r"[A-Za-z0-9]+", text)
    slug = "-".join(w.lower() for w in words)[:32].strip("-")
    return slug or "task"


def make_task_id(slug: str) -> str:
    ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    return f"rig-{ts}-{slug}"


def budget_status(task: dict) -> tuple[float, float | None, bool]:
    """(経過分, 予算分 or None, 超過かどうか) を返す（#281）。`budget_minutes` 未設定の
    task では常に超過なし——見積もりを指定していないタスクを誤って警告しない。"""
    created = datetime.datetime.fromisoformat(task["created_at"])
    elapsed_min = (datetime.datetime.now().astimezone() - created).total_seconds() / 60.0
    budget = task.get("budget_minutes")
    over = bool(budget) and elapsed_min > budget
    return elapsed_min, budget, over


def load_access_control(root: pathlib.Path) -> dict:
    """`.rig/access.json`（accept を許可する識別子の allowlist・#282）を読む。
    形式: `{"default": ["alice","bob"], "<task_type>": [...]}`（`default` は該当 task_type
    専用キーが無い場合のフォールバック）。ファイルが無ければ制限なし（後方互換・単独利用では
    従来通り無制限で動作する）。壊れていても RUN は止めず、無制限側へフォールバックする。"""
    p = root / ".rig" / "access.json"
    if not p.is_file():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        warn(f"{p} が JSON として読めません。RBAC は無視します（無制限で動作）")
        return {}


def current_identity(root: pathlib.Path) -> str:
    """accept 操作者の識別子。`RIG_USER` 環境変数 → `git config user.name` の順に解決する。"""
    env = os.environ.get("RIG_USER")
    if env:
        return env
    proc = git(["config", "user.name"], cwd=root, check=False)
    return proc.stdout.strip() or "unknown"


def load_gate_extensions(root: pathlib.Path) -> dict:
    """`.rig/gate-extensions.json`（組織固有のacceptance基準・#283）を読む。
    形式: `{"<task_type>": ["custom_criterion", …], "*": [...]}`（`*` は全 task_type に適用）。
    無ければ空 dict（標準presetのみで動作＝後方互換）。壊れていても RUN は止めない。"""
    p = root / ".rig" / "gate-extensions.json"
    if not p.is_file():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        warn(f"{p} が JSON として読めません。カスタム基準は無視します")
        return {}


# ── gate 構築・判定 ──────────────────────────────────────────────────────────
def build_acceptance(task_id: str, task_type: str, root: pathlib.Path | None = None) -> dict:
    presets = TASK_TYPES[task_type]
    checks: list[dict] = []
    seen: set[str] = set()
    for preset in presets:
        for name in GATE_PRESETS[preset]:
            if name not in seen:
                seen.add(name)
                checks.append({"name": name, "status": "pending", "detail": ""})

    custom_presets: list[str] = []
    if root is not None:
        ext = load_gate_extensions(root)
        for key in ("*", task_type):
            for name in ext.get(key) or []:
                if name not in seen:
                    seen.add(name)
                    checks.append({"name": name, "status": "pending", "detail": "", "custom": True})
                    custom_presets.append(name)

    return {"task_id": task_id, "task_type": task_type, "presets": presets,
            "custom_criteria": custom_presets,
            "status": "pending", "checks": checks, "checked_at": None}


def gate_status(acc: dict) -> str:
    """failed > pending > (全件 skipped なら skipped) > warning > passed の優先順位で判定する。"""
    statuses = [c["status"] for c in acc["checks"]]
    if not statuses:
        return "skipped"
    if any(s == "failed" for s in statuses):
        return "failed"
    if any(s == "pending" for s in statuses):
        return "pending"
    if all(s == "skipped" for s in statuses):
        return "skipped"
    if any(s == "warning" for s in statuses):
        return "passed_with_warnings"
    return "passed"


# ── worktree ─────────────────────────────────────────────────────────────────
def default_worktree_path(root: pathlib.Path, task_id: str) -> pathlib.Path:
    import os
    wt_root = os.environ.get("RIG_WORKTREE_ROOT")
    base = pathlib.Path(wt_root) if wt_root else root.parent / "rig-worktrees" / root.name
    return base / task_id


def worktree_dirty(wt: pathlib.Path) -> list[str]:
    proc = git(["status", "--porcelain"], cwd=wt)
    return [line for line in proc.stdout.splitlines() if line.strip()]


# ── diff.md 構造化パーサ ─────────────────────────────────────────────────────
def parse_diff_md(text: str) -> dict[str, str]:
    """`## <heading>` 区切りの diff.md をセクション辞書に分解する（小文字キー）。"""
    sections: dict[str, str] = {}
    current: str | None = None
    buf: list[str] = []
    for line in text.splitlines():
        m = re.match(r"^##\s+(.+?)\s*$", line)
        if m:
            if current is not None:
                sections[current] = "\n".join(buf).strip()
            current = m.group(1).strip().lower()
            buf = []
        elif current is not None:
            buf.append(line)
    if current is not None:
        sections[current] = "\n".join(buf).strip()
    return sections


# ── サブコマンド実装 ──────────────────────────────────────────────────────────
def ensure_rig_gitignored(root: pathlib.Path) -> bool:
    """`.rig/` を repo の `.gitignore` に追記する（無ければ）。返り値 = 追記したか。

    `.rig/` には worktree state / runs / audit / lock が入るため、うっかり PR に紛れ
    込まないよう最初の task 作成時に自動追記する。既に `.rig/` / `.rig` / `/.rig/` の
    いずれかで無視されていれば何もしない（誤検知でユーザーの記述を壊さない）。
    `.gitignore` が無い場合は新規作成する。git 管理外の root では何もしない。
    """
    if not (root / ".git").exists():
        return False
    gi = root / ".gitignore"
    already = False
    lines: list[str] = []
    if gi.exists():
        lines = gi.read_text(encoding="utf-8").splitlines()
        for ln in lines:
            s = ln.strip()
            if s in (".rig/", ".rig", "/.rig/", "/.rig"):
                already = True
                break
    if already:
        return False
    with gi.open("a", encoding="utf-8") as f:
        # 既存末尾が改行で終わっているとは限らないので先頭にも改行を1つ
        f.write("\n# rig workbench state (task worktrees, telemetry, audit, locks)\n.rig/\n")
    return True


def cmd_new(args: argparse.Namespace) -> None:
    root = repo_root()
    if args.type not in TASK_TYPES:
        die(f"task_type '{args.type}' は不正です。有効: {', '.join(TASK_TYPES)}")
    slug = args.slug or make_slug(args.input)
    task_id = make_task_id(slug)
    d = runs_dir(root) / task_id
    if d.exists():
        die(f"task '{task_id}' は既に存在します")

    # `.rig/` を .gitignore に自動追記（無ければ）。誤って PR に混入するのを防ぐ保険。
    if ensure_rig_gitignored(root):
        print("◇ .gitignore に .rig/ を追記しました（PR 混入防止）")

    base_branch = args.base or current_branch(root)
    base_commit = git(["rev-parse", "HEAD"], cwd=root).stdout.strip()

    worktree_path: str | None = None
    branch: str | None = None
    if not args.no_worktree:
        wt = default_worktree_path(root, task_id)
        branch = f"rig/{task_id}"
        wt.parent.mkdir(parents=True, exist_ok=True)
        git(["worktree", "add", "-b", branch, str(wt), "HEAD"], cwd=root)
        worktree_path = str(wt)

    task = {
        "task_id": task_id,
        "input": args.input,
        "task_type": args.type,
        "recipe": args.recipe or "",
        "recipe_reason": args.reason or "",
        "base_branch": base_branch,
        "base_commit": base_commit,
        "branch": branch,
        "worktree_path": worktree_path,
        "status": "running",
        "created_at": now_iso(),
        "updated_at": now_iso(),
        "budget_minutes": args.budget_minutes,   # 任意（#281）。未指定なら None＝警告なし
    }
    d.mkdir(parents=True, exist_ok=True)
    save_json(d / "task.json", task)
    save_json(d / "steps.json", {"steps": []})
    acc = build_acceptance(task_id, args.type, root)
    save_json(d / "acceptance.json", acc)

    # ── 選択理由の可視化バナー（Phase 1 §3・散文任せにせずコードが確定出力する）───
    print("▸ rig")
    print(f"task: {args.input}")
    print(f"detected: {args.type}")
    print(f"recipe: {args.recipe or '(未指定)'}" + (f" — {args.reason}" if args.reason else ""))
    print(f"mode: {'isolated worktree' if worktree_path else 'not isolated (--no-worktree)'}")
    print(f"gate: {' + '.join(acc['presets'])}")
    print()
    print(f"task_id: {task_id}")
    print(f"base_branch: {base_branch} @ {base_commit[:12]}")
    if worktree_path:
        print(f"worktree: {worktree_path} (branch: {branch})")
    else:
        print("worktree: なし（--no-worktree 指定）")
    print(f"state: {d.relative_to(root)}/")


def cmd_step(args: argparse.Namespace) -> None:
    root = repo_root()
    task_id = resolve_task_id(root, args.task_id)
    with task_lock(root, task_id):
        d = run_dir(root, task_id)
        data = load_json(d / "steps.json", {"steps": []})
        for pair in args.set:
            if "=" not in pair:
                die(f"--set は <step>=<status> 形式で指定してください（値: {pair!r}）")
            name, status = pair.split("=", 1)
            if status not in VALID_STEP_STATUS:
                die(f"step status '{status}' は不正です。有効: {', '.join(VALID_STEP_STATUS)}")
            for step in data["steps"]:
                if step["name"] == name:
                    step["status"] = status
                    step["updated_at"] = now_iso()
                    break
            else:
                data["steps"].append({"name": name, "status": status, "updated_at": now_iso()})
        save_json(d / "steps.json", data)
        print(f"{task_id} steps: " + " ".join(f"{s['name']}={s['status']}" for s in data["steps"]))


def cmd_gate(args: argparse.Namespace) -> None:
    root = repo_root()
    task_id = resolve_task_id(root, args.task_id)
    with task_lock(root, task_id):
        d, task = load_task(root, task_id)
        acc = load_json(d / "acceptance.json", build_acceptance(task_id, task["task_type"], root))

        known = {c["name"]: c for c in acc["checks"]}
        for pair in args.set or []:
            if "=" not in pair:
                die(f"--set は <criterion>=<status>[:detail] 形式で指定してください（値: {pair!r}）")
            name, status = pair.split("=", 1)
            detail = ""
            if ":" in status:
                status, detail = status.split(":", 1)
            if status not in VALID_CRITERION_STATUS:
                die(f"criterion status '{status}' は不正です。有効: {', '.join(VALID_CRITERION_STATUS)}")
            if name not in known:
                die(f"criterion '{name}' はこの task の gate に存在しません。有効: {', '.join(known)}")
            known[name]["status"] = status
            if detail:
                known[name]["detail"] = detail

        acc["status"] = gate_status(acc)
        acc["checked_at"] = now_iso()
        save_json(d / "acceptance.json", acc)

        if task["status"] == "running" and acc["status"] in ("passed", "passed_with_warnings", "failed", "skipped"):
            task["status"] = "gate_failed" if acc["status"] == "failed" else "gate_passed"
            save_task(d, task)

        print(f"## acceptance-gate: {task_id}  [{acc['status'].upper()}]")
        print(f"presets: {' + '.join(acc['presets'])}")
        for c in acc["checks"]:
            detail = f" — {c['detail']}" if c.get("detail") else ""
            print(f"  {CHECK_ICON[c['status']]} {c['name']}{detail}")
        if acc["status"] == "failed":
            sys.exit(1)


def _diff_lines(root: pathlib.Path, task: dict) -> tuple[list[str], str, list[str]]:
    """(name-status 行, shortstat, worktree 未コミット行) を返す。"""
    wt = pathlib.Path(task["worktree_path"]) if task.get("worktree_path") else None
    if wt and wt.is_dir():
        base = task["base_commit"]
        names = git(["diff", "--name-status", f"{base}...HEAD"], cwd=wt).stdout.splitlines()
        stat = git(["diff", "--shortstat", f"{base}...HEAD"], cwd=wt).stdout.strip()
        dirty = worktree_dirty(wt)
        return names, stat, dirty
    # worktree なし RUN（レビュー等）はメイン作業ツリーの現状 diff を対象にする
    names = git(["diff", "--name-status", "HEAD"], cwd=root).stdout.splitlines()
    stat = git(["diff", "--shortstat", "HEAD"], cwd=root).stdout.strip()
    return names, stat, []


def cmd_diff(args: argparse.Namespace) -> None:
    root = repo_root()
    task_id = resolve_task_id(root, args.task_id)
    d, task = load_task(root, task_id)
    acc = load_json(d / "acceptance.json", build_acceptance(task_id, task["task_type"], root))
    names, stat, dirty = _diff_lines(root, task)

    print(f"## rig diff: {task_id}")
    print(f"base: {task['base_branch']} @ {task['base_commit'][:12]}")
    if task.get("branch"):
        print(f"branch: {task['branch']}")
    print()
    print("Changed files:")
    if not names and not dirty:
        print("  （変更なし）")
    for line in names:
        print(f"  {line}")
    if stat:
        print(f"  {stat}")
    if dirty:
        print(f"\n[WARN] worktree に未コミットの変更が {len(dirty)} 件あります（accept 前にコミットが必要）:")
        for line in dirty[:20]:
            print(f"  {line}")

    diff_md = d / "diff.md"
    sections = parse_diff_md(diff_md.read_text(encoding="utf-8")) if diff_md.exists() else {}
    for label, key in (("Summary", "summary"), ("Risk", "risk"), ("Tests", "tests")):
        print(f"\n{label}:")
        print(f"  {sections[key]}" if sections.get(key) else "  （未記載）")

    print("\nUnrelated diff:")
    unrelated = next((c for c in acc["checks"] if c["name"] == "no_unrelated_diff"), None)
    if "unrelated diff" in sections:
        print(f"  {sections['unrelated diff']}")
    elif unrelated:
        print(f"  {CHECK_ICON[unrelated['status']]} {unrelated['status']}"
              + (f" — {unrelated['detail']}" if unrelated.get("detail") else ""))
    else:
        print("  （未確認）")

    if not diff_md.exists():
        print(f"\n[NOTE] {diff_md.relative_to(root)} が未作成です。accept には diff summary の作成が必要です。")

    print(f"\nRecommended:\n  {RECOMMENDATION[gate_status(acc)]}")


def cmd_accept(args: argparse.Namespace) -> None:
    root = repo_root()
    task_id = resolve_task_id(root, args.task_id)
    with task_lock(root, task_id):
        _cmd_accept_locked(args, root, task_id)


def _cmd_accept_locked(args: argparse.Namespace, root: pathlib.Path, task_id: str) -> None:
    d, task = load_task(root, task_id)

    if task["status"] == "accepted":
        die(f"task '{task_id}' は既に accept 済みです")
    if task["status"] == "discarded":
        die(f"task '{task_id}' は discard 済みです")

    # ── RBAC（.rig/access.json があるときだけ効く・#282。単独利用では無制限のまま）───
    access = load_access_control(root)
    if access:
        allowed = access.get(task["task_type"]) or access.get("default") or []
        who = current_identity(root)
        if allowed and who not in allowed:
            die(f"'{who}' には task_type '{task['task_type']}' の accept 権限がありません"
                f"（許可: {', '.join(allowed)}）。`.rig/access.json` を確認するか、権限を持つ人に accept を依頼してください")

    acc = load_json(d / "acceptance.json", build_acceptance(task_id, task["task_type"], root))
    status = gate_status(acc)
    diff_md = d / "diff.md"
    diff_summary_ok = diff_md.exists() and diff_md.read_text(encoding="utf-8").strip() != ""
    unrelated = next((c for c in acc["checks"] if c["name"] == "no_unrelated_diff"), None)
    unrelated_ok = (unrelated is None) or (unrelated["status"] in ("passed", "warning", "skipped"))
    gate_ok = status in ("passed", "passed_with_warnings", "skipped")

    # ── accept_requirements チェックリスト（Phase 3・全件を先に見せてから判定する）───
    hard = [
        ("worktree_exists", bool(task.get("worktree_path")) and pathlib.Path(task["worktree_path"]).is_dir()),
        ("base_branch_recorded", bool(task.get("base_branch")) and bool(task.get("base_commit"))),
        ("diff_summary_generated", diff_summary_ok),
    ]
    soft = [
        ("acceptance_gate_not_failed", gate_ok),
        ("no_unrelated_diff", unrelated_ok),
    ]
    print(f"## rig accept: {task_id} — accept_requirements")
    for name, ok in hard + soft:
        print(f"  {'✓' if ok else '✗'} {name}")

    hard_fail = [name for name, ok in hard if not ok]
    if hard_fail:
        hints = {
            "worktree_exists": "この task には worktree がありません（--no-worktree RUN、または既に discard 済み）",
            "base_branch_recorded": "task.json に base_branch/base_commit が記録されていません（run-state 破損の可能性）",
            "diff_summary_generated": f"{diff_md.relative_to(root)} が未作成です。`/rig diff` の散文サマリを先に書いてください",
        }
        die("accept できません（構造的な前提が未達・--force でも上書き不可）:\n"
            + "\n".join(f"  - {n}: {hints[n]}" for n in hard_fail))

    soft_fail = [name for name, ok in soft if not ok]
    if soft_fail:
        if not args.force:
            failed_checks = [c["name"] for c in acc["checks"] if c["status"] in ("failed", "pending")]
            die(
                f"acceptance-gate が {status} のため accept できません（未達: {', '.join(failed_checks) or 'no_unrelated_diff'}）。\n"
                f"  基準を満たしてから `workbench.py gate {task_id} --set <criterion>=passed` で更新するか、\n"
                f"  リスクを理解した上で --force を付けてください（記録に残ります）"
            )
        warn(f"未達要件を --force で上書きして accept します（{', '.join(soft_fail)}）。task.json に forced: true を記録します")
        task["forced"] = True
        audit_append(root, {
            "ts": now_iso(),
            "action": "accept_force",
            "task_id": task_id,
            "task_type": task.get("task_type"),
            "recipe": task.get("recipe"),
            "bypassed": soft_fail,
            "gate_status": status,
            "failed_checks": [c["name"] for c in acc["checks"]
                              if c["status"] in ("failed", "pending")],
            "invoker": os.environ.get("RIG_INVOKER") or "direct",
        })
    if status == "passed_with_warnings":
        warns = [f"{c['name']}（{c.get('detail') or 'detail なし'}）" for c in acc["checks"] if c["status"] == "warning"]
        warn("未解決の警告つきで accept します: " + " / ".join(warns))

    if not task.get("worktree_path"):
        die("この task は worktree を持ちません（--no-worktree RUN）。accept 対象の差分がありません")

    # ② worktree の整合チェック
    wt = pathlib.Path(task["worktree_path"])
    if not wt.is_dir():
        die(f"worktree {wt} が存在しません")
    dirty = worktree_dirty(wt)
    if dirty:
        die(
            f"worktree に未コミットの変更が {len(dirty)} 件あります。"
            f"worktree 側でコミットしてから accept してください（git -C {wt} add -A && git -C {wt} commit）"
        )
    branch = task["branch"]
    ahead = git(["rev-list", "--count", f"{task['base_commit']}..{branch}"], cwd=root).stdout.strip()
    if ahead == "0":
        die(f"branch {branch} に base からのコミットがありません（accept する差分なし）")

    # ②-b メイン作業ツリーの整合チェック（squash merge 失敗時に `git reset --hard HEAD` で
    # 安全に巻き戻せることを事前に保証する。`git merge --squash` は失敗時に MERGE_HEAD を
    # 作らないため `git merge --abort` が効かず、事前チェックなしでは reset --hard がユーザーの
    # 既存の未コミット変更ごと消し飛ばしてしまう）
    root_dirty = git(["status", "--porcelain"], cwd=root).stdout.splitlines()
    if root_dirty:
        die(
            f"作業ツリーに未コミットの変更が {len(root_dirty)} 件あります。"
            f"accept は squash merge の安全な巻き戻しを保証するため、作業ツリーがクリーンな状態でのみ実行できます。"
            f"先にコミットするか stash してください（git status で確認）"
        )

    # ③ メイン作業ツリーへ squash merge（コミットはしない＝最終確定は人/モデルの明示操作）
    proc = git(["merge", "--squash", branch], cwd=root, check=False)
    if proc.returncode != 0:
        # コンフリクト: squash merge は MERGE_HEAD を作らず `merge --abort` が効かないため、
        # 直前に作業ツリーがクリーンだったことを保証した上で reset --hard で巻き戻す。
        git(["reset", "--hard", "HEAD"], cwd=root, check=False)
        die(
            f"squash merge がコンフリクトしました（base からの乖離）。作業ツリーは反映前の状態に戻しました:\n{proc.stderr.strip()}\n"
            f"  worktree 側で `git -C {wt} rebase {task['base_branch']}` してコンフリクトを解消してから再実行してください"
        )

    task["status"] = "accepted"
    task["accepted_at"] = now_iso()
    save_task(d, task)
    names, stat, _ = _diff_lines(root, task)
    print(f"\n## rig accept: {task_id} ✓")
    print(f"branch {branch} の変更（{ahead} commits）をメイン作業ツリーに **staged** として反映しました。")
    if stat:
        print(f"  {stat}")
    print("次のアクション:")
    print("  1) 内容確認: git diff --staged")
    print("  2) コミット: git commit")
    print(f"  3) 後片付け: workbench.py discard {task_id} --yes  （worktree と branch を削除・run log は残る）")


def cmd_discard(args: argparse.Namespace) -> None:
    root = repo_root()
    if not args.task_id:
        die("discard は誤爆防止のため task_id の明示が必須です（`workbench.py log` で確認できます）")
    with task_lock(root, args.task_id):
        _cmd_discard_locked(args, root)


def _cmd_discard_locked(args: argparse.Namespace, root: pathlib.Path) -> None:
    d, task = load_task(root, args.task_id)
    task_id = task["task_id"]

    names, stat, dirty = _diff_lines(root, task)
    print(f"## rig discard: {task_id}")
    print(f"input: {task['input']}")
    print("破棄対象の変更ファイル:")
    if names or dirty:
        for line in names:
            print(f"  {line}")
        for line in dirty:
            print(f"  {line}  (uncommitted)")
    else:
        print("  （変更なし）")

    if not args.yes:
        die("確認のため --yes を付けて再実行してください（変更は上に表示したとおり失われます）")

    wt = pathlib.Path(task["worktree_path"]) if task.get("worktree_path") else None
    if wt and wt.is_dir():
        git(["worktree", "remove", "--force", str(wt)], cwd=root)
    if task.get("branch"):
        proc = git(["rev-parse", "--verify", task["branch"]], cwd=root, check=False)
        if proc.returncode == 0:
            git(["branch", "-D", task["branch"]], cwd=root)
    if task["status"] != "accepted":  # accept 済みの後片付けは状態を維持する
        task["status"] = "discarded"
    task["cleaned_at"] = now_iso()
    task["worktree_path"] = None
    save_task(d, task)

    # 視覚検証の一時成果物（screenshot 等）は判断結果ではなく手段なので discard 時に即時削除する
    # （run log 本体の JSON/MD は残す。詳細は patterns/visual-artifacts）。
    visual_dir = d / "visual"
    visual_removed = visual_dir.is_dir()
    if visual_removed:
        shutil.rmtree(visual_dir, ignore_errors=True)

    print(f"worktree と branch を削除しました。run log は {d.relative_to(root)}/ に残ります。")
    if visual_removed:
        print(f"視覚検証の一時画像（{visual_dir.relative_to(root)}/）も削除しました。")


def _print_steps(d: pathlib.Path) -> None:
    steps = load_json(d / "steps.json", {"steps": []})["steps"]
    print("Steps:")
    if not steps:
        print("  （未記録）")
        return
    for s in steps:
        print(f"  {STEP_ICON.get(s['status'], '?')} {s['name']}"
              + (f" ({s['status']})" if s["status"] not in ("passed",) else ""))


def _print_checks(acc: dict) -> None:
    print(f"Gate: {acc['status'].upper()}  ({' + '.join(acc['presets'])})")
    for c in acc["checks"]:
        detail = f" — {c['detail']}" if c.get("detail") else ""
        print(f"  {CHECK_ICON[c['status']]} {c['name']}{detail}")


def cmd_status(args: argparse.Namespace) -> None:
    root = repo_root()
    task_id = resolve_task_id(root, args.task_id)
    d, task = load_task(root, task_id)
    acc = load_json(d / "acceptance.json", build_acceptance(task_id, task["task_type"], root))
    acc["status"] = gate_status(acc)

    print(f"## rig status: {task_id}")
    print(f"task:        {task['input']}")
    print(f"type:        {task['task_type']}" + (f" / recipe: {task['recipe']}" if task.get("recipe") else ""))
    print(f"status:      {task['status']}" + (" (forced)" if task.get("forced") else ""))
    print(f"mode:        {'isolated worktree' if task.get('worktree_path') else 'not isolated'}")
    print(f"base:        {task['base_branch']} @ {task['base_commit'][:12]}")
    if task.get("worktree_path"):
        print(f"worktree:    {task['worktree_path']} (branch: {task['branch']})")
    if task.get("budget_minutes"):
        elapsed, budget, over = budget_status(task)
        print(f"budget:      {elapsed:.0f}分 / {budget:.0f}分" + ("  ⚠ 予算超過" if over else ""))
    print()
    _print_steps(d)
    print()
    _print_checks(acc)
    print()
    if task["status"] not in ("accepted", "discarded"):
        names, stat, dirty = _diff_lines(root, task)
        pending = f"変更 {len(names)} ファイル" + (f"・未コミット {len(dirty)} 件" if dirty else "")
        print(f"未反映差分:  {pending if names or dirty else 'なし'}" + (f"（{stat}）" if stat else ""))
    print(f"Next: {NEXT_ACTIONS.get(task['status'], '-')}")


ACTIVE_STATUSES = ("running", "gate_passed", "gate_failed")


def cmd_board(args: argparse.Namespace) -> None:
    """全 task を一覧する単一ダッシュボード。

    `/rig:rig` を直接叩いた task も `/rig:queue go --provider rig` 経由で並列実行された
    task も同じ `.rig/runs/` に積まれるため、複数タスクを並行で進めていても**ターミナルを
    いくつも開かず1コマンドで全体像を見る**——「何をしていたか忘れる」を構造的に解消する。
    """
    root = repo_root()
    base = runs_dir(root)
    tasks: list[dict] = []
    if base.is_dir():
        for p in sorted(base.iterdir()):
            tj = p / "task.json"
            if tj.exists():
                tasks.append(load_json(tj))

    if not args.all:
        tasks = [t for t in tasks if t["status"] in ACTIVE_STATUSES]
    tasks.sort(key=lambda t: t["created_at"])

    scope = "全 task" if args.all else "アクティブ"
    print(f"## rig board（{scope}: {len(tasks)} 件）\n")
    if not tasks:
        print("アクティブな task がありません。" if not args.all else "task がありません（.rig/runs/ が空）。")
        print("\n新しいタスクを始めるには: /rig:rig \"<task>\"")
        return

    for t in tasks:
        d = base / t["task_id"]
        acc = load_json(d / "acceptance.json", {"checks": []})
        gs = gate_status(acc) if acc.get("checks") else "-"
        steps = load_json(d / "steps.json", {"steps": []})["steps"]
        last_step = f"{steps[-1]['name']}({steps[-1]['status']})" if steps else "-"
        mode = "isolated" if t.get("worktree_path") else "not-isolated"

        _, _, over_budget = budget_status(t)
        print(f"[{t['status']:<11}] {t['task_id']}" + ("  ⚠ 予算超過" if over_budget else ""))
        print(f"    {t['input'][:70]}{'…' if len(t['input']) > 70 else ''}")
        print(f"    type={t['task_type']:<14} recipe={t.get('recipe') or '-':<14} "
              f"mode={mode:<13} step={last_step:<20} gate={gs}")
    if not args.all:
        print(f"\n({sum(1 for t in tasks if t['status'] == 'gate_failed')} 件 gate_failed / "
              f"{sum(1 for t in tasks if t['status'] == 'gate_passed')} 件 diff/accept 待ち)")
        print("次のアクション: /rig:rig diff <task_id> · /rig:rig accept <task_id> · /rig:rig discard <task_id> --yes")


_RIG_HOOK_MARKER = "# rig-managed-hook:"


def cmd_install_git_hook(args: argparse.Namespace) -> None:
    """`.git/hooks/<name>` に rig 提供の pre-commit/pre-push フックをインストールする（#298）。

    `scripts/git-hooks/<name>` の実体をそのままコピーするだけ（新しい仕組みを発明しない）。
    build/lint/test はプロジェクト固有でプレーンな git hook からは知りようがないため、
    ここでカバーするのは acceptance-gate のうち機械的に判定できる部分（secret パターン
    スキャン＝`no_secret_leak` 相当）のみ——rig を経由しない通常の commit/push にも
    同じ最小限のセンサーを適用する opt-in オプション。

    既存の hook が rig 由来（ファイル先頭付近に `_RIG_HOOK_MARKER` を持つ）でなければ、
    ユーザーの既存 hook を黙って上書きしない（`--force` で明示上書き）。
    """
    root = repo_root()
    git_dir = root / ".git"
    if not git_dir.is_dir():
        die(".git ディレクトリが見つかりません（git worktree のルートで実行してください）")

    src_dir = pathlib.Path(__file__).resolve().parent / "git-hooks"
    names = ["pre-commit", "pre-push"] if args.which == "both" else [args.which]

    hooks_dir = git_dir / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)

    for name in names:
        src = src_dir / name
        if not src.is_file():
            die(f"hook テンプレートが見つかりません: {src}")
        dest = hooks_dir / name
        if dest.exists():
            existing = dest.read_text(encoding="utf-8", errors="ignore")
            is_ours = _RIG_HOOK_MARKER in existing[:400]
            if not is_ours and not args.force:
                first_line = existing.splitlines()[0] if existing.splitlines() else "(空)"
                print(f"[SKIP] {dest} は既に存在し、rig 由来ではありません（1行目: {first_line}）。"
                      "上書きするには --force を付けてください。")
                continue
        shutil.copyfile(src, dest)
        dest.chmod(dest.stat().st_mode | 0o111)
        print(f"✓ インストール: {dest}")

    print("\n無効化: rm " + " ".join(str(hooks_dir / n) for n in names))
    print("一時的にスキップ: git commit/push --no-verify")


def _load_drill(root: pathlib.Path) -> list[dict]:
    """`.rig/drill-results.jsonl`（/rig:drill の実測結果）を読む。
    フォーマットは `{"ts": …, "scores": [{"reviewer","detected","seeded","false_positives"}]}`
    （orchestrate.py の DRILL_PATH と同一ファイル・同一スキーマ）。"""
    p = root / ".rig" / "drill-results.jsonl"
    if not p.exists():
        return []
    out: list[dict] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def cmd_cockpit(args: argparse.Namespace) -> None:
    """board・gate・drill・audit を一画面に集約する read-only Mission Control（#307）。

    新しい常駐サービスや DB は持たない——既存の `.rig/runs/`・`drill-results.jsonl`・
    `audit.jsonl` を読むだけで完結する。accept/discard 等の破壊的操作はここでは行わず、
    次に打つべき既存コマンドを案内するだけ（v1 は read-only。集計ロジックは
    board/stats/audit と同じ関数をそのまま再利用し、二重実装しない）。
    """
    root = repo_root()
    base = runs_dir(root)
    tasks: list[dict] = []
    if base.is_dir():
        for p in sorted(base.iterdir()):
            tj = p / "task.json"
            if tj.exists():
                tasks.append(load_json(tj))
    tasks.sort(key=lambda t: t["created_at"])
    active = [t for t in tasks if t["status"] in ACTIVE_STATUSES]

    print("━━━ rig cockpit — Mission Control（read-only）━━━━━━━━━━━━━━")

    # ── Run timeline ──────────────────────────────────────────────────────
    print(f"\n┌─ Run timeline（アクティブ {len(active)} / 全 {len(tasks)} 件）")
    if not active:
        print("│ アクティブな task はありません。")
    for t in active:
        d = base / t["task_id"]
        acc = load_json(d / "acceptance.json", {"checks": []})
        gs = gate_status(acc) if acc.get("checks") else "-"
        label = t["input"][:44] + ("…" if len(t["input"]) > 44 else "")
        _, _, over_budget = budget_status(t)
        budget_flag = "  ⚠予算超過" if over_budget else ""
        print(f"│ [{t['status']:<11}] {t['task_id']:<28} gate={gs:<20} {label}{budget_flag}")

    # ── Gate radar ────────────────────────────────────────────────────────
    gate_counts: Counter[str] = Counter()
    for t in tasks:
        acc = load_json(base / t["task_id"] / "acceptance.json", {"checks": []})
        gate_counts[gate_status(acc) if acc.get("checks") else "skipped"] += 1
    print("├─ Gate radar")
    if tasks:
        for status in ("passed", "passed_with_warnings", "failed", "pending", "skipped"):
            if gate_counts.get(status):
                print(f"│ {status}: {gate_counts[status]}")
    else:
        print("│ 未実行（run がありません）")

    # ── Reviewer confidence（drill 実測）────────────────────────────────────
    print("├─ Reviewer confidence（drill 実測）")
    drills = _load_drill(root)
    atk: dict[str, dict] = {}
    for d in drills:
        for s in d.get("scores", []):
            a = atk.setdefault(s.get("reviewer", "?"), {"detected": 0, "seeded": 0, "fp": 0})
            a["detected"] += s.get("detected", 0)
            a["seeded"] += s.get("seeded", 0)
            a["fp"] += s.get("false_positives", 0)
    if not atk:
        print("│ 未計測（`/rig:drill` を実行すると persona 別の検出率が表示されます）")
    else:
        for name, a in sorted(atk.items()):
            if a["seeded"]:
                rate = a["detected"] / a["seeded"] * 100
                fp = f"・誤検出 {a['fp']}" if a["fp"] else ""
                print(f"│ {name}: 検出率 {rate:.0f}%（{a['detected']}/{a['seeded']}{fp}）")
            else:
                print(f"│ {name}: 未計測")

    # ── Cost meter（#271/#296 未実装の間は「未計測」を明示。空値を成功に見せない）──
    print("├─ Cost meter")
    print("│ 未計測（recipe/model 単位のコスト計測は #271/#296 で追加予定）")

    # ── Safety strip ──────────────────────────────────────────────────────
    audit_events = _load_audit(root)
    force_events = [e for e in audit_events if e.get("action") == "accept_force"]
    print("├─ Safety strip")
    if force_events:
        print(f"│ force-bypass: {len(force_events)} 件（詳細: `workbench.py audit`）")
    else:
        print("│ force-bypass の記録なし。")

    # ── Next action rail ──────────────────────────────────────────────────
    gate_passed = [t for t in active if t["status"] == "gate_passed"]
    gate_failed = [t for t in active if t["status"] == "gate_failed"]
    print("└─ Next action rail")
    if gate_passed:
        ids = ", ".join(t["task_id"] for t in gate_passed[:3])
        more = " …" if len(gate_passed) > 3 else ""
        print(f"  diff/accept 待ち {len(gate_passed)} 件: {ids}{more}")
        print("    → `workbench.py diff <id>` / `workbench.py accept <id>`")
    if gate_failed:
        ids = ", ".join(t["task_id"] for t in gate_failed[:3])
        more = " …" if len(gate_failed) > 3 else ""
        print(f"  gate 未達 {len(gate_failed)} 件: {ids}{more}")
        print("    → 未達基準を修正して再判定、または `workbench.py discard <id> --yes`")
    if not gate_passed and not gate_failed:
        print("  現時点で必要なアクションはありません。")

    if active:
        print("\nEvidence: 各 task の plan.md / diff.md / acceptance.json / review.json は "
              ".rig/runs/<task-id>/ 配下。")


def cmd_log(args: argparse.Namespace) -> None:
    root = repo_root()
    base = runs_dir(root)
    entries = []
    if base.is_dir():
        for p in sorted(base.iterdir(), reverse=True):
            tj = p / "task.json"
            if tj.exists():
                entries.append(load_json(tj))
    entries = entries[: args.limit]
    if args.json:
        print(json.dumps(entries, ensure_ascii=False, indent=2))
        return
    if not entries:
        print("実行履歴がありません（.rig/runs/ が空）")
        return
    print(f"## rig log（最新 {len(entries)} 件）\n")
    for t in entries:
        d = base / t["task_id"]
        acc = load_json(d / "acceptance.json", {"checks": [], "presets": []})
        gs = gate_status(acc) if acc.get("checks") else "-"
        print(f"- {t['task_id']}  [{t['status']}]")
        print(f"    input: {t['input'][:60]}{'…' if len(t['input']) > 60 else ''}")
        print(f"    type: {t['task_type']}"
              + (f" / recipe: {t['recipe']}" if t.get("recipe") else "")
              + f" / gate: {gs}"
              + f" / created: {t['created_at']}")


def cmd_gates(_args: argparse.Namespace) -> None:
    print("## acceptance-gate プリセット（正本）\n")
    for name, criteria in GATE_PRESETS.items():
        print(f"### {name}")
        for c in criteria:
            print(f"  - {c}")
        print()
    print("### task_type → presets")
    for tt, presets in TASK_TYPES.items():
        print(f"  {tt}: {' + '.join(presets)}")


def _dir_age_days(p: pathlib.Path) -> float:
    return (datetime.datetime.now().timestamp() - p.stat().st_mtime) / 86400.0


def cmd_gc(args: argparse.Namespace) -> None:
    """視覚検証の一時成果物（`patterns/visual-artifacts` 参照）を age-based に処分する。

    task の status（accepted/discarded/running）は問わない——画像は再生成可能な検証手段
    であり恒久記録ではないため。ソース・worktree・branch には一切触れない。
    """
    root = repo_root()
    threshold_days = 14
    if args.older_than:
        m = re.match(r"^(\d+)d$", args.older_than)
        if not m:
            die(f"--older-than は '<N>d' 形式で指定してください（例: 14d。値: {args.older_than!r}）")
        threshold_days = int(m.group(1))

    candidates: list[pathlib.Path] = []
    runs = runs_dir(root)
    if runs.is_dir():
        candidates.extend(p / "visual" for p in runs.iterdir() if (p / "visual").is_dir())
    adhoc = root / ".rig" / "visual" / "adhoc"
    if adhoc.is_dir():
        candidates.extend(p for p in adhoc.iterdir() if p.is_dir())

    to_remove = [p for p in candidates if _dir_age_days(p) >= threshold_days]

    print(f"## rig gc（閾値: {threshold_days}日{'・dry-run' if args.dry_run else ''}）")
    if not to_remove:
        print("削除対象はありません。")
        return
    for p in sorted(to_remove):
        rel = p.relative_to(root)
        age = _dir_age_days(p)
        prefix = "[dry-run] " if args.dry_run else ""
        print(f"  {prefix}削除: {rel}/（{age:.1f}日経過）")
        if not args.dry_run:
            shutil.rmtree(p, ignore_errors=True)
    verb = "が対象です（--dry-run のため未削除）" if args.dry_run else "を削除しました"
    print(f"\n{len(to_remove)} 件{verb}。")


def cmd_review(args: argparse.Namespace) -> None:
    """review 系タスクの persona 別 verdict を記録する（stats のゴム印検知に使用）。"""
    root = repo_root()
    task_id = resolve_task_id(root, args.task_id)
    with task_lock(root, task_id):
        d = run_dir(root, task_id)
        data = load_json(d / "review.json", {"task_id": task_id, "verdicts": []})
        by_persona = {v["persona"]: v for v in data["verdicts"]}
        for pair in args.set:
            if "=" not in pair:
                die(f"--set は <persona>=<APPROVE|REJECT|APPROVE_WITH_CONDITIONS> 形式で指定してください（値: {pair!r}）")
            persona, verdict = pair.split("=", 1)
            if verdict not in VALID_VERDICT:
                die(f"verdict '{verdict}' は不正です。有効: {', '.join(VALID_VERDICT)}")
            by_persona[persona] = {"persona": persona, "verdict": verdict, "recorded_at": now_iso()}
        data["verdicts"] = list(by_persona.values())
        save_json(d / "review.json", data)
        print(f"{task_id} review verdicts: " + " ".join(f"{v['persona']}={v['verdict']}" for v in data["verdicts"]))


def _load_audit(root: pathlib.Path) -> list[dict]:
    p = audit_path(root)
    if not p.exists():
        return []
    events: list[dict] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return events


def cmd_audit(args: argparse.Namespace) -> None:
    """`.rig/audit.jsonl` の force-bypass 記録を一覧する。

    accept_requirements の "--force で外せない" 前提とは別に、gate 未達を --force で
    上書きしたケースを恒久記録する監査ログ（差別化ポイントの物理的強度の証拠）。
    """
    root = repo_root()
    events = _load_audit(root)
    if args.action:
        events = [e for e in events if e.get("action") == args.action]
    if args.since:
        events = [e for e in events if (e.get("ts") or "")[:10] >= args.since]
    if not events:
        print("## rig audit\n\n記録がありません（`accept --force` で追記されます）。")
        return
    limit = args.limit if args.limit else len(events)
    shown = events[-limit:]
    print(f"## rig audit（直近 {len(shown)} / 全 {len(events)} 件）\n")
    for e in shown:
        ts = e.get("ts", "?")
        action = e.get("action", "?")
        tid = e.get("task_id", "?")
        by = ", ".join(e.get("bypassed") or [])
        gate = e.get("gate_status", "?")
        print(f"  {ts}  {action:16s}  task={tid}")
        print(f"    bypassed: {by}  gate: {gate}")
        if e.get("failed_checks"):
            print(f"    failed: {', '.join(e['failed_checks'])}")


def cmd_stats(args: argparse.Namespace) -> None:
    root = repo_root()
    base = runs_dir(root)
    tasks: list[dict] = []
    if base.is_dir():
        for p in sorted(base.iterdir()):
            tj = p / "task.json"
            if tj.exists():
                tasks.append(load_json(tj))

    if args.last:
        m = re.match(r"^(\d+)d$", args.last)
        if not m:
            die(f"--last は '<N>d' 形式で指定してください（例: 30d。値: {args.last!r}）")
        cutoff = datetime.datetime.now().astimezone() - datetime.timedelta(days=int(m.group(1)))
        tasks = [t for t in tasks if datetime.datetime.fromisoformat(t["created_at"]) >= cutoff]

    if args.recipe:
        tasks = [t for t in tasks if t.get("recipe") == args.recipe]

    def _load_reviews(task_list: list[dict]) -> dict[str, dict]:
        out: dict[str, dict] = {}
        for t in task_list:
            rj = base / t["task_id"] / "review.json"
            if rj.exists():
                out[t["task_id"]] = load_json(rj)
        return out

    if args.verifier:
        candidate_reviews = _load_reviews(tasks)
        tasks = [t for t in tasks
                 if any(v["persona"] == args.verifier
                        for v in candidate_reviews.get(t["task_id"], {}).get("verdicts", []))]

    # フィルタ確定後の最終 tasks に対してのみ review.json を読む（--verifier 適用前の
    # 候補集合を漏らさないよう、統計は必ず最終集合から作り直す）
    review_by_task = _load_reviews(tasks)

    if not tasks:
        print("## rig stats\n\n対象 run がありません（フィルタを確認するか、`/rig \"<task>\"` を実行してください）")
        return

    accepted = sum(1 for t in tasks if t["status"] == "accepted")
    discarded = sum(1 for t in tasks if t["status"] == "discarded")

    gate_counts: Counter[str] = Counter()
    for t in tasks:
        acc = load_json(base / t["task_id"] / "acceptance.json", {"checks": []})
        gate_counts[gate_status(acc) if acc.get("checks") else "skipped"] += 1
    failed_gate = gate_counts.get("failed", 0)

    recipe_counts = Counter(t.get("recipe") or f"(recipe未指定・{t['task_type']})" for t in tasks)

    verifier_stats: Counter[str] = Counter()
    verifier_rejects: Counter[str] = Counter()
    for tid, rv in review_by_task.items():
        for v in rv.get("verdicts", []):
            verifier_stats[v["persona"]] += 1
            if v["verdict"] == "REJECT":
                verifier_rejects[v["persona"]] += 1

    print("## rig stats\n")
    print(f"Runs: {len(tasks)}")
    print(f"Accepted: {accepted}")
    print(f"Discarded: {discarded}")
    print(f"Failed gate: {failed_gate}")

    print("\nMost used recipes:")
    for name, n in recipe_counts.most_common(5):
        print(f"- {name}: {n}")

    print("\nGate results:")
    for status in ("passed", "passed_with_warnings", "failed", "pending", "skipped"):
        if gate_counts.get(status):
            print(f"- {status}: {gate_counts[status]}")

    if verifier_stats:
        print("\nVerifier behavior:")
        rubber_stamp_warnings = []
        for persona, runs in sorted(verifier_stats.items(), key=lambda kv: -kv[1]):
            rejects = verifier_rejects.get(persona, 0)
            print(f"- {persona}: {runs} runs, {rejects} rejects")
            if runs >= 5 and rejects == 0:
                rubber_stamp_warnings.append(f"{persona} has 0 rejects across {runs} runs. Possible rubber-stamp behavior.")
        if rubber_stamp_warnings:
            print("\nWarning:")
            for w in rubber_stamp_warnings:
                print(w)
    else:
        print("\nVerifier behavior: （未記録。`workbench.py review <task_id> --set <persona>=<verdict>` で記録すると集計されます）")

    audit_events = _load_audit(root)
    if args.last:
        cutoff = datetime.datetime.now().astimezone() - datetime.timedelta(days=int(m.group(1)))
        audit_events = [e for e in audit_events
                        if e.get("ts") and datetime.datetime.fromisoformat(e["ts"]) >= cutoff]
    force_events = [e for e in audit_events if e.get("action") == "accept_force"]
    if force_events:
        by_bypass: Counter[str] = Counter()
        for e in force_events:
            for name in e.get("bypassed", []):
                by_bypass[name] += 1
        print(f"\nForce bypass ({len(force_events)} 件): "
              "`accept --force` は accept_requirements の hard 前提を外せない（構造的強度）が、"
              "soft 前提を上書きしたケースを記録している。")
        for name, n in by_bypass.most_common():
            print(f"- {name}: {n}")
        print("（詳細は `workbench.py audit` で参照）")


def main() -> None:
    parser = argparse.ArgumentParser(description="rig workbench — run-state / worktree / acceptance-gate manager")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("new", help="タスクを登録し isolated worktree を作成する")
    p.add_argument("input", help="ユーザーの自然文タスク")
    p.add_argument("--type", required=True, help=f"task_type（{', '.join(TASK_TYPES)}）")
    p.add_argument("--slug", help="task-id 用の短い英語 slug（省略時は input から導出）")
    p.add_argument("--base", help="base branch 名の明示（省略時は現在の branch）")
    p.add_argument("--recipe", help="選択した recipe 名")
    p.add_argument("--reason", help="recipe 選択理由（バナー・log 用）")
    p.add_argument("--no-worktree", action="store_true", help="worktree を作らない（review 等の読み取り専用 RUN）")
    p.add_argument("--budget-minutes", type=float, help="見積もり時間（分）。超過は status/board/cockpit で警告表示（#281。未指定なら警告なし）")
    p.set_defaults(func=cmd_new)

    p = sub.add_parser("step", help="step の進行状態を記録する")
    p.add_argument("task_id", nargs="?")
    p.add_argument("--set", action="append", required=True, metavar="STEP=STATUS",
                   help=f"status: {', '.join(VALID_STEP_STATUS)}（複数可）")
    p.set_defaults(func=cmd_step)

    p = sub.add_parser("gate", help="acceptance-gate 基準の合否を記録・判定する")
    p.add_argument("task_id", nargs="?")
    p.add_argument("--set", action="append", metavar="CRITERION=STATUS[:DETAIL]",
                   help=f"status: {', '.join(VALID_CRITERION_STATUS)}（DETAIL は : 区切りで付記）")
    p.set_defaults(func=cmd_gate)

    p = sub.add_parser("diff", help="base からの変更差分を構造化表示する")
    p.add_argument("task_id", nargs="?")
    p.set_defaults(func=cmd_diff)

    p = sub.add_parser("accept", help="accept_requirements と gate を確認してメイン作業ツリーへ squash 反映する")
    p.add_argument("task_id", nargs="?")
    p.add_argument("--force", action="store_true", help="gate 未達を上書きして反映（記録に残る。構造的前提の欠落は上書き不可）")
    p.set_defaults(func=cmd_accept)

    p = sub.add_parser("discard", help="worktree と branch を破棄する（run log は残す）")
    p.add_argument("task_id", nargs="?")
    p.add_argument("--yes", action="store_true", help="破棄の最終確認")
    p.set_defaults(func=cmd_discard)

    p = sub.add_parser("status", help="現在（または指定 task）の実行状態を表示する")
    p.add_argument("task_id", nargs="?")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("board", help="全 task を一覧するダッシュボード（既定はアクティブのみ）")
    p.add_argument("--all", action="store_true", help="accepted/discarded も含めて全 task を表示する")
    p.set_defaults(func=cmd_board)

    p = sub.add_parser("log", help="過去の実行ログを一覧する")
    p.add_argument("--limit", type=int, default=10)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_log)

    p = sub.add_parser("cockpit", help="board・gate・drill・audit を一画面に集約する Mission Control（read-only）")
    p.set_defaults(func=cmd_cockpit)

    p = sub.add_parser("install-git-hook",
                        help="acceptance-gate の secret-pattern scan を .git/hooks/ にインストールする（opt-in）")
    p.add_argument("--which", choices=("pre-commit", "pre-push", "both"), default="both",
                   help="インストール対象（既定: both）")
    p.add_argument("--force", action="store_true", help="rig 由来でない既存 hook を上書きする")
    p.set_defaults(func=cmd_install_git_hook)

    p = sub.add_parser("gates", help="acceptance-gate プリセット定義を表示する")
    p.set_defaults(func=cmd_gates)

    p = sub.add_parser("gc", help="視覚検証の一時画像（visual/）を age-based に処分する（patterns/visual-artifacts）")
    p.add_argument("--older-than", help="この日数を超えたものを削除する（例: 14d。既定 14d）")
    p.add_argument("--dry-run", action="store_true", help="削除せず対象だけ表示する")
    p.set_defaults(func=cmd_gc)

    p = sub.add_parser("review", help="review 系タスクの persona 別 verdict を記録する（stats 用）")
    p.add_argument("task_id", nargs="?")
    p.add_argument("--set", action="append", required=True, metavar="PERSONA=VERDICT",
                   help=f"verdict: {', '.join(VALID_VERDICT)}（複数可）")
    p.set_defaults(func=cmd_review)

    p = sub.add_parser("stats", help="過去 run を集計する（recipe 別・gate 別・verifier のゴム印検知）")
    p.add_argument("--recipe", help="recipe 名で絞り込み")
    p.add_argument("--verifier", help="persona 名で絞り込み（review.json に記録がある run のみ）")
    p.add_argument("--last", help="直近 N 日に絞り込み（例: 30d）")
    p.set_defaults(func=cmd_stats)

    p = sub.add_parser("audit", help="`accept --force` 等の監査ログを一覧する（`.rig/audit.jsonl`）")
    p.add_argument("--limit", type=int, help="直近 N 件のみ表示")
    p.add_argument("--action", help="action 名で絞り込み（例: accept_force）")
    p.add_argument("--since", help="YYYY-MM-DD 以降のみ表示")
    p.set_defaults(func=cmd_audit)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
