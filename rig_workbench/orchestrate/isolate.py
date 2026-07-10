"""orchestrate isolate: worktree isolation (split from scripts/orchestrate.py)."""

import re
import datetime
import pathlib
import subprocess

from . import config

# ── worktree 隔離実行（--isolate）────────────────────────────────────────────
# run を使い捨ての git worktree に隔離する：作業ツリーを汚さず、ゲート green の
# 成果だけを元 branch へ ff 合流（未達・dirty・非 ff は branch を残して人へ）。
# 「非決定的な生成をゲートの外に出さない」determinism-by-gate の空間版。

_ISO_SEQ = 0


def setup_isolation(recipe_name: str) -> dict:
    r = subprocess.run(["git", "rev-parse", "--show-toplevel"],
                       capture_output=True, text=True, cwd=str(config.INVOCATION_CWD))
    if r.returncode != 0:
        raise SystemExit("[ERROR] --isolate は git リポジトリ内でのみ使えます")
    root = r.stdout.strip()
    ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    safe = re.sub(r"[^a-zA-Z0-9_-]", "-", recipe_name)
    # 同一秒の連続 run でも衝突しないよう連番を付す（プロセス内カウンタ＋既存 branch 回避）
    global _ISO_SEQ
    _ISO_SEQ += 1
    name = f"{safe}-{ts}-{_ISO_SEQ}"
    branch = f"rig/run-{name}"
    wdir = pathlib.Path(root) / ".rig" / "worktrees" / name
    wdir.parent.mkdir(parents=True, exist_ok=True)
    a = subprocess.run(["git", "-C", root, "worktree", "add", "-b", branch, str(wdir), "HEAD"],
                       capture_output=True, text=True)
    if a.returncode != 0:
        raise SystemExit(f"[ERROR] worktree 作成に失敗: {a.stderr.strip()[:200]}")
    return {"root": root, "dir": str(wdir), "branch": branch}


def teardown_isolation(iso: dict, final: str) -> str:
    """終了状態に応じて worktree を後始末し、結果ラベルを返す（純関数的・副作用は git のみ）。

    DONE かつ clean かつ commit あり → 元 branch へ ff 合流して撤収（merged）
    DONE かつ clean かつ commit なし → 撤収のみ（clean-removed）
    それ以外（未達 / dirty / 元が dirty / 非 ff）→ worktree と branch を残す（kept）
    """
    root, wdir, branch = iso["root"], iso["dir"], iso["branch"]
    dirty = subprocess.run(["git", "-C", wdir, "status", "--porcelain"],
                           capture_output=True, text=True).stdout.strip()
    ahead = subprocess.run(["git", "-C", root, "rev-list", "--count", f"HEAD..{branch}"],
                           capture_output=True, text=True).stdout.strip() or "0"
    root_dirty = subprocess.run(["git", "-C", root, "status", "--porcelain", "--untracked-files=no"],
                                capture_output=True, text=True).stdout.strip()

    def _remove(delete_branch: bool) -> None:
        subprocess.run(["git", "-C", root, "worktree", "remove", "--force", wdir],
                       capture_output=True, text=True)
        if delete_branch:
            subprocess.run(["git", "-C", root, "branch", "-D", branch],
                           capture_output=True, text=True)

    if final == "DONE" and not dirty:
        if ahead == "0":
            _remove(delete_branch=True)
            return "clean-removed"
        if not root_dirty:
            m = subprocess.run(["git", "-C", root, "merge", "--ff-only", branch],
                               capture_output=True, text=True)
            if m.returncode == 0:
                _remove(delete_branch=True)
                return "merged"
    return "kept"

