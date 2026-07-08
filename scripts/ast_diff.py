#!/usr/bin/env python3
"""AST差分によるセマンティックdiffサマリ（#280）。

テキストdiffは意味的に重要な変更（シグネチャ変更・ロジック変更）とフォーマットのみの
変更を同列に扱ってしまう。このモジュールはPython標準の`ast`モジュールで関数/クラス
単位の構文木を比較し、意味のある変更を抽出する——既存のテキストdiff要約を置き換える
のではなく補強する（`diff.md`のSummaryは引き続き人/AIが書く）。

対応言語: Pythonのみ（stdlibの`ast`で完結するため）。非対応言語・parse失敗時は
`supported: False`を返し、呼び出し側（workbench.py cmd_diff）は従来のテキストdiff要約
にフォールバックする。
"""
from __future__ import annotations

import ast
import sys


def _qualified_defs(tree: ast.AST, prefix: str = "") -> dict:
    out: dict = {}
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            name = f"{prefix}{node.name}"
            out[name] = node
            if isinstance(node, ast.ClassDef):
                out.update(_qualified_defs(node, prefix=f"{name}."))
    return out


def _signature(node: ast.AST) -> str:
    if isinstance(node, ast.ClassDef):
        bases = [ast.dump(b) for b in node.bases]
        return f"class {node.name}({', '.join(bases)})"
    args = ast.unparse(node.args) if hasattr(ast, "unparse") else ast.dump(node.args)
    return f"def {node.name}({args})"


def semantic_diff(base_source: str, new_source: str) -> dict:
    """PythonソースをASTレベルで比較する。非Python/parse不能なら`supported=False`。"""
    try:
        base_tree = ast.parse(base_source)
        new_tree = ast.parse(new_source)
    except SyntaxError as e:
        return {"supported": False, "reason": f"parse error: {e}"}

    base_defs = _qualified_defs(base_tree)
    new_defs = _qualified_defs(new_tree)

    added = sorted(set(new_defs) - set(base_defs))
    removed = sorted(set(base_defs) - set(new_defs))
    common = set(base_defs) & set(new_defs)

    changed = []
    signature_changed = []
    for name in sorted(common):
        b, n = base_defs[name], new_defs[name]
        if ast.dump(b, annotate_fields=False) != ast.dump(n, annotate_fields=False):
            changed.append(name)
            if _signature(b) != _signature(n):
                signature_changed.append(name)

    cosmetic_only = (
        base_source != new_source
        and ast.dump(base_tree, annotate_fields=False) == ast.dump(new_tree, annotate_fields=False)
    )

    return {
        "supported": True,
        "added": added,
        "removed": removed,
        "changed": changed,
        "signature_changed": signature_changed,
        "cosmetic_only": cosmetic_only,
    }


def format_summary(result: dict, path: str) -> str:
    if not result.get("supported"):
        return f"  {path}: AST解析非対応（{result.get('reason', 'unknown')}）— テキストdiffのみ"
    if result["cosmetic_only"]:
        return f"  {path}: 意味的変更なし（フォーマット/コメントのみ、AST同一）"
    lines = [f"  {path}:"]
    if result["added"]:
        lines.append(f"    + 追加: {', '.join(result['added'])}")
    if result["removed"]:
        lines.append(f"    - 削除: {', '.join(result['removed'])}")
    if result["signature_changed"]:
        lines.append(f"    ~ シグネチャ変更: {', '.join(result['signature_changed'])}")
    body_only = [n for n in result["changed"] if n not in result["signature_changed"]]
    if body_only:
        lines.append(f"    ~ 本体変更（シグネチャ同一）: {', '.join(body_only)}")
    if not (result["added"] or result["removed"] or result["changed"]):
        lines.append("    変更なし（top-level def/classは同一）")
    return "\n".join(lines)


def main():
    if len(sys.argv) != 3:
        print("usage: ast_diff.py <base.py> <new.py>")
        sys.exit(1)
    with open(sys.argv[1], encoding="utf-8") as f:
        base_source = f.read()
    with open(sys.argv[2], encoding="utf-8") as f:
        new_source = f.read()
    result = semantic_diff(base_source, new_source)
    print(format_summary(result, sys.argv[2]))


if __name__ == "__main__":
    main()
