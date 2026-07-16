#!/usr/bin/env python3
"""AST-based semantic diff summary (#280).

A text diff treats a semantically meaningful change (a signature change, a logic
change) the same as a purely cosmetic one. This module compares function/class-level
syntax trees with the standard library's `ast` module to pull out what actually
changed — it augments the existing text-diff summary, it doesn't replace it
(`diff.md`'s Summary is still written by a human/model).

Supported language: Python only (this is entirely `ast`-module based). Unsupported
languages or a parse failure return `supported: False`; the caller (workbench.py's
`diff` command) falls back to the existing text-diff summary.
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
    """Compare Python source at the AST level. `supported=False` for non-Python or unparseable input."""
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
        return f"  {path}: AST parse unsupported ({result.get('reason', 'unknown')}) — text diff only"
    if result["cosmetic_only"]:
        return f"  {path}: no semantic change (formatting/comments only, AST identical)"
    lines = [f"  {path}:"]
    if result["added"]:
        lines.append(f"    + added: {', '.join(result['added'])}")
    if result["removed"]:
        lines.append(f"    - removed: {', '.join(result['removed'])}")
    if result["signature_changed"]:
        lines.append(f"    ~ signature changed: {', '.join(result['signature_changed'])}")
    body_only = [n for n in result["changed"] if n not in result["signature_changed"]]
    if body_only:
        lines.append(f"    ~ body changed (signature identical): {', '.join(body_only)}")
    if not (result["added"] or result["removed"] or result["changed"]):
        lines.append("    no change (top-level def/class identical)")
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
