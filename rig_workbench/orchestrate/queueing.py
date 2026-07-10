"""orchestrate queueing: task queue + cmd_queue (split from scripts/orchestrate.py)."""

import sys
import json
import pathlib
import subprocess
import concurrent.futures as futures

from . import config
from .providers import _build_prompt, run_provider

# ── タスクキュー（積んで GO・管理ツール連携）──────────────────────────────────
# 「task を積む → まとめて GO」を、ローカル json か外部管理ツール(GitHub/GitLab Issue)で持つ。
# backend は差し替え式：local（.rig/queue.json）／github（gh CLI）／gitlab（glab CLI）。
# Issue 連携時はラベルで状態管理：rig-queue → rig-running → rig-done / rig-failed。
QUEUE_LABEL = "rig-queue"
# queue list が可視化すべき「アクティブ」ラベル（rig-done は close 済みのため対象外・#211）。
QUEUE_LABELS_ACTIVE = ["rig-queue", "rig-running", "rig-failed"]
# queue が扱う全状態ラベル（旧ラベルの除去対象の算出に使う・#223）。
QUEUE_LABELS_ALL = ["rig-queue", "rig-running", "rig-failed", "rig-done"]
QUEUE_PATH = config.INVOCATION_CWD / ".rig" / "queue.json"


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
        # done（close 済み相当）は対象外（#215：github/gitlab は --state open で自然に除外される
        # のに対し local だけ queue.json に永久に残っていた非対称を解消）。
        return [it for it in _local_load()["items"] if it.get("status") != "done"]
    cli = _gh_cli(backend)
    R = (["-R", cfg["repo"]] if cfg.get("repo") else [])
    if backend == "github":
        seen: dict[object, dict] = {}
        for label in QUEUE_LABELS_ACTIVE:
            argv = [cli, "issue", "list", "-l", label, "--state", "open",
                    "--json", "number,title,labels,comments"] + R
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
                # 直近コメント（queue_set_status が書き込む失敗理由/完了コメント）を note として
                # 表示に使う（#214：queue list が note を素通りしていた欠落を解消）。
                comments = x.get("comments") or []
                note = comments[-1].get("body", "") if comments else ""
                seen[x.get("number")] = {"id": x.get("number"), "task": x.get("title"),
                                          "status": st, "note": note}
        return list(seen.values())
    # gitlab（glab）はテキスト出力のみで labels/comments が取れないため、ラベルごとに問い合わせて
    # 行単位で dedup・merge する（status は従来どおり "queued" 固定。#211 の可視性回復が主目的）。
    # note 表示は gitlab 未対応（id を個別取得できない既存の制約と同根・#214）。
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


def _queue_relabel_args(status: str) -> list[str]:
    """新 status に対応する gh/glab のラベル差し替え引数（`--add-label X --remove-label Y ...`）。

    旧ラベルは QUEUE_LABEL 固定ではなく「新ラベル以外の全キューラベル」を除去対象にする
    （#223：running→failed/done 等の遷移で旧ラベルが固定除去のまま残留し、queue_list の
    ラベル→status 判定が誤った状態を返し続けるバグの修正）。フィールドを切り出すことで
    selftest が argv 構築を直接検証できる（実 CLI 呼び出しを伴わない）。
    """
    label = {"queued": "rig-queue", "running": "rig-running",
              "done": "rig-done", "failed": "rig-failed"}.get(status)
    if not label:
        return []
    args = ["--add-label", label]
    for old in QUEUE_LABELS_ALL:
        if old != label:
            args += ["--remove-label", old]
    return args


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
    relabel = _queue_relabel_args(status)
    if relabel:
        _cli_run([cli, "issue", "edit", str(item_id)] + relabel + R)
    if note:
        _cli_run([cli, "issue", "comment", str(item_id), "-b", note] + R)
    if status == "done":
        _cli_run([cli, "issue", "close", str(item_id)] + R)
    elif status == "queued":
        # retry（#213）：done で close 済みだった item も再度アクティブにするため reopen する
        # （既に open の場合は no-op 扱いで crash しない。close 済みでない大半のケース＝failed
        # からの retry では実質何もしない）。
        _cli_run([cli, "issue", "reopen", str(item_id)] + R)


def _build_queue_task_prompt(task: str, provider: str) -> str:
    """queue の各 item を dispatch する生成プロンプト。

    `rig`/`claude` provider は headless `claude -p` の**別プロセス**として並列実行される
    （`queue go --max-parallel N`）。複数プロセスが同じ作業ディレクトリを共有するため、
    workbench の isolated worktree（`/rig:rig`）を経由させないと**並列タスクがファイルを
    取り合う衝突リスク**がある。そのため rig/claude provider には `/rig:rig "<task>"` の
    実行を明示指示し、各タスクを自動的に専用 worktree へ隔離する。
    accept は queue の役目ではない（ユーザーが `/rig:rig board`→`accept` で個別に反映する）。
    """
    if provider in ("rig", "claude"):
        return (
            "`rig` skill を Skill ツールで起動し、`facets/instructions/workbench`"
            "（`/rig:rig` 統一入口）に従って次のタスクを isolated worktree で実行してください。"
            "他の queue 項目と並列に走っているため、**本体の作業ツリーには一切書き込まないこと**"
            "（accept はしない。isolated worktree 内で分類・実装・acceptance-gate 判定までを行い、"
            "反映は queue 完了後にユーザーが `/rig:rig board` で一覧し、個別に `/rig:rig accept` "
            "するまで待つ）。\n"
            f'実行: /rig:rig "{task}"\n'
            "gate が確定したら（passed/passed_with_warnings/failed のいずれか）、最後に "
            "'STATUS: done' を出力してください。"
        )
    return _build_prompt({"recipe": "queue", "goal": task}, {"id": "task", "instruction": task}, None)


def _build_queue_verify_prompt(task: str, product: str) -> str:
    return (f"あなたは独立した検証者です（この step を生成したエージェントとは別プロセス・別ロール）。"
            f"queue タスク「{task}」の実行結果について、(1) 受け入れ基準を満たしているか、"
            f"(2) **本体の作業ツリーを直接変更せず isolated worktree 内で完結しているか**"
            f"（accept 前に main へ書き込んでいないか）を判定し、最後に必ず "
            f"'VERDICT: PASS' か 'VERDICT: FAIL' を出力してください。\n--- 成果 ---\n{product[:2000]}")


def cmd_queue(args):
    if not args or args[0] not in ("add", "list", "go", "done", "retry"):
        print("[ERROR] usage: queue <add|list|go|done|retry> [...] "
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
            line = f"  [{it.get('status','?'):<8}] #{it.get('id')}  {it.get('task')}"
            note = it.get("note")
            if note:
                line += f" — {note}"
            print(line)
        return
    if sub == "done":
        if not free:
            print("[ERROR] queue done <id>"); sys.exit(1)
        queue_set_status(backend, free[0], "done", "手動で done", cfg)
        print(f"done [{backend}]: #{free[0]}")
        return
    if sub == "retry":
        if not free:
            print("[ERROR] queue retry <id>"); sys.exit(1)
        queue_set_status(backend, free[0], "queued", "", cfg)
        print(f"retry [{backend}]: #{free[0]} → queued")
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
        rc, out = run_provider(gen, "generator", _build_queue_task_prompt(task, gen), cfg)
        rc2, vout = run_provider(ver, "verifier", _build_queue_verify_prompt(task, out), cfg, persona="queue")
        ok = ("VERDICT: PASS" in vout) and ("VERDICT: FAIL" not in vout)
        note = ("✅ rig: gate 確定（要 /rig:rig board → accept）" if ok else "❌ rig: 検証 FAIL") + f"（{gen}→{ver}）"
        queue_set_status(backend, it["id"], "done" if ok else "failed", note, cfg)
        return (it, ok)

    with futures.ThreadPoolExecutor(max_workers=max(1, max_parallel)) as ex:
        results = list(ex.map(_run_one, items))
    done = sum(1 for _, ok in results if ok)
    for it, ok in results:
        print(f"  [{'DONE' if ok else 'FAIL'}] #{it['id']}  {it['task']}")
    print(f"\n=== GO 完了: {done}/{len(results)} done [{backend}] ===")
    sys.exit(0 if done == len(results) else 1)

