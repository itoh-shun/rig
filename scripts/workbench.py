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
  acceptance.json  acceptance-gate の基準と合否
  plan.md / diff.md / log.md / final.md   モデルが書く散文成果物（本スクリプトは触らない）

終了コード: 0=成功 / 1=エラー（accept のゲート未達・worktree 不整合を含む）
依存: 標準ライブラリのみ（PyYAML 不要）
"""

import argparse
import datetime
import json
import pathlib
import re
import subprocess
import sys

# ── acceptance-gate プリセット（正本。instruction はここを参照する）───────────────
GATE_PRESETS: dict[str, list[str]] = {
    # 全 task_type 共通の標準 gate
    "standard": [
        "no_unrelated_diff",
        "tests_pass_or_reasonable_explanation",
        "no_type_errors",
        "no_lint_errors",
        "behavior_summary_written",
        "risk_summary_written",
    ],
    # 実装タスク用（standard に上乗せ）
    "implementation": [
        "implementation_matches_request",
        "tests_added_or_existing_tests_confirmed",
        "public_api_changes_documented",
        "no_unrelated_refactor",
        "no_secret_leak",
        "no_destructive_operation",
    ],
    # レビュータスク用（差分を作らないため standard を含まない）
    "review": [
        "concrete_findings_only",
        "severity_labeled",
        "file_and_line_references_included",
        "false_positive_risk_considered",
        "blocking_and_non_blocking_items_separated",
    ],
    # セキュリティ確認用（review に上乗せ）
    "security": [
        "input_validation_checked",
        "authz_authn_impact_checked",
        "secrets_not_exposed",
        "dependency_risk_checked",
        "unsafe_shell_or_eval_checked",
    ],
}

# task_type → 適用 gate プリセット（合成順に列挙）
TASK_TYPES: dict[str, list[str]] = {
    "bugfix": ["standard", "implementation"],
    "feature": ["standard", "implementation"],
    "refactor": ["standard", "implementation"],
    "test": ["standard", "implementation"],
    "performance": ["standard", "implementation"],
    "documentation": ["standard"],
    "design": ["standard"],
    "investigation": ["standard"],
    "release_support": ["standard", "implementation"],
    "review": ["review"],
    "security_review": ["review", "security"],
}

VALID_STEP_STATUS = ("pending", "running", "passed", "failed", "skipped")
VALID_CRITERION_STATUS = ("pending", "pass", "fail", "warn")


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


# ── gate 構築 ────────────────────────────────────────────────────────────────
def build_acceptance(task_type: str) -> dict:
    presets = TASK_TYPES[task_type]
    criteria: list[dict] = []
    seen: set[str] = set()
    for preset in presets:
        for cid in GATE_PRESETS[preset]:
            if cid not in seen:
                seen.add(cid)
                criteria.append({"id": cid, "status": "pending", "note": ""})
    return {"task_type": task_type, "presets": presets, "required": criteria,
            "result": "pending", "checked_at": None}


def gate_result(acc: dict) -> str:
    statuses = [c["status"] for c in acc["required"]]
    if any(s == "fail" for s in statuses):
        return "failed"
    if any(s == "pending" for s in statuses):
        return "pending"
    if any(s == "warn" for s in statuses):
        return "warning"
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


# ── サブコマンド実装 ──────────────────────────────────────────────────────────
def cmd_new(args: argparse.Namespace) -> None:
    root = repo_root()
    if args.type not in TASK_TYPES:
        die(f"task_type '{args.type}' は不正です。有効: {', '.join(TASK_TYPES)}")
    slug = args.slug or make_slug(args.input)
    task_id = make_task_id(slug)
    d = runs_dir(root) / task_id
    if d.exists():
        die(f"task '{task_id}' は既に存在します")

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
    }
    d.mkdir(parents=True, exist_ok=True)
    save_json(d / "task.json", task)
    save_json(d / "steps.json", {"steps": []})
    save_json(d / "acceptance.json", build_acceptance(args.type))
    print(f"task_id: {task_id}")
    print(f"task_type: {args.type}")
    print(f"base_branch: {base_branch} @ {base_commit[:12]}")
    if worktree_path:
        print(f"worktree: {worktree_path} (branch: {branch})")
    else:
        print("worktree: なし（--no-worktree 指定）")
    print(f"state: {d.relative_to(root)}/")


def cmd_step(args: argparse.Namespace) -> None:
    root = repo_root()
    task_id = resolve_task_id(root, args.task_id)
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
    d, task = load_task(root, task_id)
    acc = load_json(d / "acceptance.json", build_acceptance(task["task_type"]))

    known = {c["id"]: c for c in acc["required"]}
    for pair in args.set or []:
        if "=" not in pair:
            die(f"--set は <criterion>=<pass|fail|warn> 形式で指定してください（値: {pair!r}）")
        cid, status = pair.split("=", 1)
        note = ""
        if ":" in status:
            status, note = status.split(":", 1)
        if status not in VALID_CRITERION_STATUS:
            die(f"criterion status '{status}' は不正です。有効: {', '.join(VALID_CRITERION_STATUS)}")
        if cid not in known:
            die(f"criterion '{cid}' はこの task の gate に存在しません。有効: {', '.join(known)}")
        known[cid]["status"] = status
        if note:
            known[cid]["note"] = note

    acc["result"] = gate_result(acc)
    acc["checked_at"] = now_iso()
    save_json(d / "acceptance.json", acc)

    if task["status"] == "running" and acc["result"] in ("passed", "warning", "failed"):
        task["status"] = "gate_passed" if acc["result"] in ("passed", "warning") else "gate_failed"
        save_task(d, task)

    print(f"## acceptance-gate: {task_id}  [{acc['result'].upper()}]")
    print(f"presets: {' + '.join(acc['presets'])}")
    icon = {"pass": "✓", "fail": "✗", "warn": "⚠", "pending": "…"}
    for c in acc["required"]:
        note = f" — {c['note']}" if c.get("note") else ""
        print(f"  {icon[c['status']]} {c['id']}{note}")
    if acc["result"] == "failed":
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
    names, stat, dirty = _diff_lines(root, task)
    print(f"## rig diff: {task_id}")
    print(f"base: {task['base_branch']} @ {task['base_commit'][:12]}")
    if task.get("branch"):
        print(f"branch: {task['branch']}")
    print()
    if not names and not dirty:
        print("（変更なし）")
        return
    for line in names:
        print(f"  {line}")
    if stat:
        print(f"\n{stat}")
    if dirty:
        print(f"\n[WARN] worktree に未コミットの変更が {len(dirty)} 件あります（accept 前にコミットが必要）:")
        for line in dirty[:20]:
            print(f"  {line}")
    diff_md = d / "diff.md"
    if diff_md.exists():
        print(f"\n差分要約: {diff_md.relative_to(root)}（`/rig diff` の散文サマリ）")


def cmd_accept(args: argparse.Namespace) -> None:
    root = repo_root()
    task_id = resolve_task_id(root, args.task_id)
    d, task = load_task(root, task_id)

    if task["status"] == "accepted":
        die(f"task '{task_id}' は既に accept 済みです")
    if task["status"] == "discarded":
        die(f"task '{task_id}' は discard 済みです")
    if not task.get("worktree_path"):
        die("この task は worktree を持ちません（--no-worktree RUN）。accept 対象の差分がありません")

    # ① gate 判定（安全側に倒す: pass 以外は accept を止める）
    acc = load_json(d / "acceptance.json")
    result = gate_result(acc)
    if result not in ("passed", "warning"):
        if not args.force:
            failed = [c["id"] for c in acc["required"] if c["status"] in ("fail", "pending")]
            die(
                f"acceptance-gate が {result} のため accept できません（未達: {', '.join(failed)}）。\n"
                f"  基準を満たしてから `workbench.py gate {task_id} --set <criterion>=pass` で更新するか、\n"
                f"  リスクを理解した上で --force を付けてください（記録に残ります）"
            )
        warn(f"acceptance-gate {result} を --force で上書きして accept します（task.json に forced: true を記録）")
        task["forced"] = True
    if result == "warning":
        warns = [f"{c['id']}（{c.get('note') or 'note なし'}）" for c in acc["required"] if c["status"] == "warn"]
        warn("未解決の警告つきで accept します: " + " / ".join(warns))

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
    print(f"## rig accept: {task_id} ✓")
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
    print(f"worktree と branch を削除しました。run log は {d.relative_to(root)}/ に残ります。")


def _steps_summary(d: pathlib.Path) -> str:
    steps = load_json(d / "steps.json", {"steps": []})["steps"]
    if not steps:
        return "（未記録）"
    icon = {"passed": "✓", "failed": "✗", "running": "▸", "pending": "…", "skipped": "-"}
    return " → ".join(f"{s['name']}{icon.get(s['status'], '?')}" for s in steps)


NEXT_ACTIONS = {
    "running": "実行中。完了後に gate を判定してください（workbench.py gate <id> --set …）",
    "gate_passed": "/rig diff で差分確認 → /rig accept で反映（または /rig discard で破棄）",
    "gate_failed": "未達基準を修正して gate を再判定（fail のままなら /rig discard）",
    "accepted": "git diff --staged を確認してコミット → /rig discard <id> で worktree を後片付け",
    "discarded": "終了済み（run log のみ保持）",
}


def cmd_status(args: argparse.Namespace) -> None:
    root = repo_root()
    task_id = resolve_task_id(root, args.task_id)
    d, task = load_task(root, task_id)
    acc = load_json(d / "acceptance.json", build_acceptance(task["task_type"]))
    print(f"## rig status: {task_id}")
    print(f"input:       {task['input']}")
    print(f"task_type:   {task['task_type']}" + (f" / recipe: {task['recipe']}" if task.get("recipe") else ""))
    print(f"status:      {task['status']}" + (" (forced)" if task.get("forced") else ""))
    print(f"base:        {task['base_branch']} @ {task['base_commit'][:12]}")
    if task.get("worktree_path"):
        print(f"worktree:    {task['worktree_path']} (branch: {task['branch']})")
    print(f"steps:       {_steps_summary(d)}")
    print(f"gate:        {gate_result(acc)}  ({' + '.join(acc['presets'])})")
    names, stat, dirty = _diff_lines(root, task)
    if task["status"] not in ("accepted", "discarded"):
        pending = f"変更 {len(names)} ファイル" + (f"・未コミット {len(dirty)} 件" if dirty else "")
        print(f"未反映差分:  {pending if names or dirty else 'なし'}" + (f"（{stat}）" if stat else ""))
    print(f"next:        {NEXT_ACTIONS.get(task['status'], '-')}")


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
        acc = load_json(d / "acceptance.json", {"required": [], "presets": []})
        print(f"- {t['task_id']}  [{t['status']}]")
        print(f"    input: {t['input'][:60]}{'…' if len(t['input']) > 60 else ''}")
        print(f"    type: {t['task_type']}"
              + (f" / recipe: {t['recipe']}" if t.get("recipe") else "")
              + f" / gate: {gate_result(acc) if acc.get('required') else '-'}"
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


def main() -> None:
    parser = argparse.ArgumentParser(description="rig workbench — run-state / worktree / acceptance-gate manager")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("new", help="タスクを登録し isolated worktree を作成する")
    p.add_argument("input", help="ユーザーの自然文タスク")
    p.add_argument("--type", required=True, help=f"task_type（{', '.join(TASK_TYPES)}）")
    p.add_argument("--slug", help="task-id 用の短い英語 slug（省略時は input から導出）")
    p.add_argument("--base", help="base branch 名の明示（省略時は現在の branch）")
    p.add_argument("--recipe", help="選択した recipe 名")
    p.add_argument("--reason", help="recipe 選択理由（log 用）")
    p.add_argument("--no-worktree", action="store_true", help="worktree を作らない（review 等の読み取り専用 RUN）")
    p.set_defaults(func=cmd_new)

    p = sub.add_parser("step", help="step の進行状態を記録する")
    p.add_argument("task_id", nargs="?")
    p.add_argument("--set", action="append", required=True, metavar="STEP=STATUS",
                   help=f"status: {', '.join(VALID_STEP_STATUS)}（複数可）")
    p.set_defaults(func=cmd_step)

    p = sub.add_parser("gate", help="acceptance-gate 基準の合否を記録・判定する")
    p.add_argument("task_id", nargs="?")
    p.add_argument("--set", action="append", metavar="CRITERION=STATUS[:NOTE]",
                   help=f"status: {', '.join(VALID_CRITERION_STATUS)}（NOTE は : 区切りで付記）")
    p.set_defaults(func=cmd_gate)

    p = sub.add_parser("diff", help="base からの変更差分（機械部分）を表示する")
    p.add_argument("task_id", nargs="?")
    p.set_defaults(func=cmd_diff)

    p = sub.add_parser("accept", help="gate pass を確認してメイン作業ツリーへ squash 反映する")
    p.add_argument("task_id", nargs="?")
    p.add_argument("--force", action="store_true", help="gate 未達を上書きして反映（記録に残る）")
    p.set_defaults(func=cmd_accept)

    p = sub.add_parser("discard", help="worktree と branch を破棄する（run log は残す）")
    p.add_argument("task_id", nargs="?")
    p.add_argument("--yes", action="store_true", help="破棄の最終確認")
    p.set_defaults(func=cmd_discard)

    p = sub.add_parser("status", help="現在（または指定 task）の実行状態を表示する")
    p.add_argument("task_id", nargs="?")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("log", help="過去の実行ログを一覧する")
    p.add_argument("--limit", type=int, default=10)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_log)

    p = sub.add_parser("gates", help="acceptance-gate プリセット定義を表示する")
    p.set_defaults(func=cmd_gates)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
