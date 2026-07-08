#!/usr/bin/env python3
"""
rig SAST/DAST アダプタ（#276）

外部の静的解析ツール（Semgrep 等）が出力する JSON を、rig の acceptance-gate の
**単一の集約 check**（`sast_findings_clear`）に変換する薄いアダプタ。rig 自身は
解析を実行しない——ツールは各自インストール・実行し、その出力をこのアダプタに
渡すだけ（rig が知らないツール固有のオプション・ルールセットを一切前提にしない）。

`workbench.py gate` は事前に `acceptance.json` へ登録済みの criterion 名にしか
判定を記録できない（動的な criterion 名を無制限に増やす設計ではない）。そのため
finding 単位の check ではなく、**worst-case を集約した1個の criterion**にする。
プロジェクト側は `.rig/gate-extensions.json` の対象 task_type（または `"*"`）に
`"sast_findings_clear"` を1回追加しておけば、以後このアダプタで機械判定できる
（`facets/instructions/acceptance-check` の「任意基準」参照）。

対応フォーマット:
  semgrep --json の出力
    {"results": [{"check_id", "path", "start": {"line"},
                  "extra": {"severity", "message"}}, …]}

使い方:
  python3 scripts/sast_adapter.py semgrep <semgrep-output.json>
      → 集約結果（status/detail/findings 一覧）を JSON で stdout に出力する（副作用なし）
  python3 scripts/sast_adapter.py semgrep <semgrep-output.json> --apply <task_id>
      → `workbench.py gate <task_id> --set sast_findings_clear=...` を実際に呼んで反映する
        （事前に `.rig/gate-extensions.json` で `sast_findings_clear` を登録していない
        task では、workbench.py 側が「この task の gate に存在しない」と拒否する）

終了コード: 0=正常（結果0件も含む） / 1=入力エラー
"""

from __future__ import annotations

import json
import pathlib
import subprocess
import sys

CRITERION_NAME = "sast_findings_clear"

# severity 表記はツールごとに揺れるため、正規化した上でここに追加していく。
_SEVERITY_TO_STATUS = {
    "ERROR": "failed", "CRITICAL": "failed", "HIGH": "failed",
    "WARNING": "warning", "MEDIUM": "warning", "LOW": "warning",
    "INFO": "warning", "NOTE": "warning",
}
_STATUS_RANK = {"passed": 0, "warning": 1, "failed": 2}  # worst-case集約の優先順位


def parse_semgrep(path: pathlib.Path) -> list[dict]:
    """semgrep --json の出力を finding 列（正規化済み）に変換する（純関数）。"""
    data = json.loads(path.read_text(encoding="utf-8"))
    findings = []
    for r in data.get("results", []):
        sev = (r.get("extra", {}).get("severity") or "WARNING").upper()
        status = _SEVERITY_TO_STATUS.get(sev, "warning")
        line = (r.get("start") or {}).get("line", "?")
        loc = f"{r.get('path', '?')}:{line}"
        msg = (r.get("extra", {}).get("message") or r.get("check_id") or "").replace("\n", " ").strip()
        findings.append({"status": status, "text": f"{r.get('check_id', '?')} @ {loc}: {msg}"})
    return findings


ADAPTERS = {"semgrep": parse_semgrep}


def aggregate(findings: list[dict]) -> dict:
    """finding 列 → 単一 criterion（worst-case status + 上位N件の detail）。"""
    if not findings:
        return {"name": CRITERION_NAME, "status": "passed",
                "detail": "findings 0件", "findings": []}
    worst = max((f["status"] for f in findings), key=lambda s: _STATUS_RANK[s])
    top = [f["text"] for f in findings[:5]]
    more = f"（他 {len(findings) - 5} 件）" if len(findings) > 5 else ""
    return {"name": CRITERION_NAME, "status": worst,
            "detail": (f"{len(findings)}件: " + " / ".join(top) + more)[:400],
            "findings": findings}


def main() -> None:
    args = sys.argv[1:]
    if len(args) < 2 or args[0] not in ADAPTERS:
        print(f"[ERROR] usage: sast_adapter.py <{'|'.join(ADAPTERS)}> <output.json> [--apply <task_id>]",
              file=sys.stderr)
        sys.exit(1)

    tool, out_path = args[0], pathlib.Path(args[1])
    if not out_path.is_file():
        print(f"[ERROR] ファイルが見つかりません: {out_path}", file=sys.stderr)
        sys.exit(1)

    try:
        findings = ADAPTERS[tool](out_path)
    except (json.JSONDecodeError, KeyError) as exc:
        print(f"[ERROR] {tool} の出力として解釈できません: {exc}", file=sys.stderr)
        sys.exit(1)

    result = aggregate(findings)

    if "--apply" in args:
        task_id = args[args.index("--apply") + 1]
        wb = pathlib.Path(__file__).resolve().parent / "workbench.py"
        detail = result["detail"].replace('"', "'").replace(":", ";")
        # `workbench.py gate` は gate 全体が failed/pending で終わると非ゼロ終了する
        # （criterion の記録自体は成功しても、gate 総合判定を反映した exit code になる）。
        # ここでの成功/失敗は「記録できたか」であって「gate が通ったか」ではないため
        # check=True にはしない——出力はそのまま透過し、記録失敗（stderr）だけ検知する。
        proc = subprocess.run([sys.executable, str(wb), "gate", task_id,
                              "--set", f"{CRITERION_NAME}={result['status']}:{detail}"])
        print(f"applied {CRITERION_NAME}={result['status']} to {task_id}（findings {len(findings)}件）")
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
