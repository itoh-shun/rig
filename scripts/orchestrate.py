#!/usr/bin/env python3
"""互換 shim — 実装は rig_workbench/orchestrate/ パッケージへ移動した。

`python3 scripts/orchestrate.py <cmd>`（bin/orchestrate・.claude-plugin/bin/rig 経由を
含む）と、rig_workbench/cli.py が importlib でこのファイルを読み `.main()` を呼ぶ
既存経路の両方を維持する。usage 表示（`print(__doc__)`）は cli モジュール側にある。
"""

import pathlib
import sys

# repo root（scripts/ の親）を sys.path に入れて、どの cwd からでも
# rig_workbench パッケージを import できるようにする。
_REPO_ROOT = str(pathlib.Path(__file__).resolve().parent.parent)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from rig_workbench.orchestrate.cli import main  # noqa: E402

if __name__ == "__main__":
    main()
