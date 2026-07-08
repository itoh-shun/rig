#!/usr/bin/env python3
"""
rig openapi-diff — OpenAPIスキーマの差分検出（#288）

変更前後のOpenAPI仕様（JSON、または PyYAML があれば YAML も可）を比較し、
追加/削除/変更された `<METHOD> <path>` を列挙する。`public_api_changes_documented`
系のacceptance criteriaを機械的に裏付けるための軽量チェック——専用のOpenAPI
バリデータやコード生成・破壊的変更の意味論的判定（semverルール等）は行わない。

使い方:
  python3 scripts/openapi_diff.py <before.json|yaml> <after.json|yaml>
  python3 scripts/openapi_diff.py <before> <after> --json   # 機械可読出力
"""

from __future__ import annotations

import json
import pathlib
import sys

try:
    import yaml
except ImportError:
    yaml = None

_HTTP_METHODS = ("get", "post", "put", "patch", "delete", "options", "head")


def load_spec(path: str) -> dict:
    p = pathlib.Path(path)
    text = p.read_text(encoding="utf-8")
    if p.suffix in (".yaml", ".yml"):
        if yaml is None:
            print("[ERROR] PyYAMLが無いためYAML形式は読めません。JSON形式を使うか `pip install pyyaml`。",
                  file=sys.stderr)
            sys.exit(1)
        return yaml.safe_load(text) or {}
    return json.loads(text)


def extract_operations(spec: dict) -> dict[str, dict]:
    ops: dict[str, dict] = {}
    for path, methods in (spec.get("paths") or {}).items():
        for method, detail in (methods or {}).items():
            if method.lower() not in _HTTP_METHODS:
                continue
            ops[f"{method.upper()} {path}"] = detail or {}
    return ops


def diff_specs(before: dict, after: dict) -> dict:
    b, a = extract_operations(before), extract_operations(after)
    added = sorted(set(a) - set(b))
    removed = sorted(set(b) - set(a))
    changed = [key for key in sorted(set(a) & set(b))
              if json.dumps(a[key], sort_keys=True) != json.dumps(b[key], sort_keys=True)]
    return {"added": added, "removed": removed, "changed": changed}


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    as_json = "--json" in sys.argv[1:]
    if len(args) != 2:
        print("[ERROR] usage: openapi_diff.py <before> <after> [--json]", file=sys.stderr)
        sys.exit(1)

    result = diff_specs(load_spec(args[0]), load_spec(args[1]))

    if as_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    total = len(result["added"]) + len(result["removed"]) + len(result["changed"])
    print(f"## rig openapi-diff: {args[0]} → {args[1]}\n")
    if total == 0:
        print("公開APIの差分なし。")
        return
    if result["removed"]:
        print(f"削除（破壊的変更の可能性）: {len(result['removed'])}件")
        for op in result["removed"]:
            print(f"  - {op}")
    if result["changed"]:
        print(f"変更: {len(result['changed'])}件")
        for op in result["changed"]:
            print(f"  ~ {op}")
    if result["added"]:
        print(f"追加: {len(result['added'])}件")
        for op in result["added"]:
            print(f"  + {op}")


if __name__ == "__main__":
    main()
