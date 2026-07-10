"""orchestrate config: module-level constants/paths (split from scripts/orchestrate.py)."""

import os
import pathlib

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
    return pathlib.Path(__file__).resolve().parent.parent.parent


RIG_HOME = find_rig_home()
RECIPES = RIG_HOME / "skills" / "rig" / "recipes"
INVOCATION_CWD = pathlib.Path(os.getcwd()).resolve()
PROJECT_RECIPES = INVOCATION_CWD / ".rig" / "recipes"  # プロジェクト overlay
RUNS_PATH = INVOCATION_CWD / ".rig" / "runs.jsonl"     # 実行テレメトリ（run-state と同格の実行ログ）
DRILL_PATH = INVOCATION_CWD / ".rig" / "drill-results.jsonl"  # /rig:drill の実測結果（検出率）
DEFAULT_K = 2  # acceptance-gate の既定リトライ上限（SKILL §3.5）
