#!/usr/bin/env python3
"""
rig 構造バリデータ（CI 用）

shipped tier の recipe frontmatter・step 参照・extends チェーンを機械的に検査する。
--validate instruction（facets/instructions/validate.md）の①②③サブセットを実装。
Claude 不要・ファイルシステムのみで完結。

終了コード: 0=合格 / 1=FAIL あり
"""

import sys
import re
import pathlib
import traceback

try:
    import yaml
except ImportError:
    print("[ERROR] PyYAML が見つかりません。`pip install pyyaml` で導入してください。")
    sys.exit(1)

# ── パス定数 ────────────────────────────────────────────────────────────────
ROOT     = pathlib.Path(__file__).parent.parent
SKILLS   = ROOT / "skills" / "rig"
RECIPES  = SKILLS / "recipes"
FACETS   = SKILLS / "facets"
PATTERNS = SKILLS / "patterns"
AGENTS   = ROOT / "agents"

# ── カウンタ ─────────────────────────────────────────────────────────────────
results: list[str] = []
_pass = _warn = _fail = 0


def _emit(level: str, msg: str) -> None:
    global _pass, _warn, _fail
    if level == "PASS":
        _pass += 1
    elif level == "WARN":
        _warn += 1
    elif level == "FAIL":
        _fail += 1
    results.append(f"[{level}] {msg}")


# ── frontmatter パーサ ────────────────────────────────────────────────────────
def parse_frontmatter(path: pathlib.Path) -> tuple[dict | None, str]:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---"):
        return None, text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return None, text
    try:
        fm = yaml.safe_load(parts[1]) or {}
        return fm, parts[2]
    except yaml.YAMLError as exc:
        return None, str(exc)


# ── 参照解決ヘルパー ──────────────────────────────────────────────────────────
def _check_exists(path: pathlib.Path, ctx: str, field: str) -> bool:
    if path.exists():
        return True
    rel = path.relative_to(ROOT) if ROOT in path.parents or path.is_relative_to(ROOT) else path
    _emit("FAIL", f"{ctx} — {field}: {rel} が存在しません")
    return False


def _resolve_persona(name: str, ctx: str) -> bool:
    """persona を shipped facets → agents の順で解決する（§5 tier 解決の shipped 相当）。"""
    # facets/personas/<name>.md（/ 区切りでサブディレクトリ可）
    facet_path = FACETS / "personas" / pathlib.Path(name.replace("/", "/") + ".md")
    if facet_path.exists():
        return True
    # agents/<name>.md（repo root 直下）
    agent_path = AGENTS / f"{name}.md"
    if agent_path.exists():
        return True
    _emit("FAIL", f"{ctx} — personas[{name!r}] が解決できません（facets/personas/ および agents/）")
    return False


def _check_pattern_or_gate(val: str | None, ctx: str, field: str) -> None:
    if not val or val in ("—", "-"):
        return
    _check_exists(PATTERNS / f"{val}.md", ctx, field)


# ── recipe 1件チェック ────────────────────────────────────────────────────────
def check_recipe(path: pathlib.Path) -> None:
    ctx = f"recipe {path.stem}"
    fm, raw = parse_frontmatter(path)

    if fm is None:
        _emit("FAIL", f"{ctx} — frontmatter が読めません（YAML エラー: {raw[:80]}）")
        return

    # ① 必須トップレベルキー（§3.5）
    required_top = ["name", "description", "scope", "steps", "autonomy"]
    missing = [k for k in required_top if k not in fm or fm[k] is None]
    if missing:
        for k in missing:
            _emit("FAIL", f"{ctx} — 必須フィールド `{k}` がありません")
        return  # 必須欠落は以降のチェックが意味をなさない

    # name ↔ ファイル名
    if fm["name"] != path.stem:
        _emit("WARN", f"{ctx} — name '{fm['name']}' がファイル名 '{path.stem}' と不一致")

    # scope 値域
    if fm["scope"] not in ("shipped", "user", "project"):
        _emit("FAIL", f"{ctx} — scope '{fm['scope']}' は shipped|user|project でなければなりません")

    # autonomy 値域
    if fm["autonomy"] not in ("interactive", "autonomous"):
        _emit("FAIL", f"{ctx} — autonomy '{fm['autonomy']}' は interactive|autonomous でなければなりません")

    # backend 値域（#52）
    backend_val = fm.get("backend")
    if backend_val is not None and backend_val not in ("manual", "workflow"):
        _emit("FAIL", f"{ctx} — backend '{backend_val}' は manual|workflow でなければなりません")

    # tdd 値域（#56）
    tdd_val = fm.get("tdd")
    if tdd_val is not None and not isinstance(tdd_val, bool):
        _emit("FAIL", f"{ctx} — tdd '{tdd_val!r}' は boolean (true/false) でなければなりません")

    # ② extends チェーン（§4.2.2 + validate.md ①）
    parent_step_ids: list[str] = []
    extends_name: str | None = fm.get("extends")
    if extends_name:
        parent_path = RECIPES / f"{extends_name}.md"
        if not parent_path.exists():
            _emit("FAIL", f"{ctx} — extends: '{extends_name}' が見つかりません")
        else:
            parent_fm, _ = parse_frontmatter(parent_path)
            if parent_fm:
                # 孫継承チェック（#42）
                if parent_fm.get("extends"):
                    _emit(
                        "WARN",
                        f"{ctx} (extends: {extends_name}) — {extends_name} も extends を持ちます"
                        f"（多段継承 = 孫継承。RUN 時に親の extends が無視されます。SKILL.md §4.2.2）",
                    )
                parent_step_ids = [
                    s.get("id", "")
                    for s in (parent_fm.get("steps") or [])
                    if isinstance(s, dict)
                ]

    # ③ steps チェック
    steps = fm.get("steps")
    if not isinstance(steps, list) or len(steps) == 0:
        _emit("FAIL", f"{ctx} — steps[] が空か不正です")
        _emit("PASS", f"{ctx}: 参照チェック省略（steps 不正）")
        return

    seen_ids: set[str] = set()
    for i, step in enumerate(steps):
        if not isinstance(step, dict):
            _emit("FAIL", f"{ctx} — steps[{i}] が dict でなければなりません")
            continue

        step_id = step.get("id") or f"[{i}]"
        step_ctx = f"{ctx}.{step_id}"

        # id 必須 & 一意性
        if not step.get("id"):
            _emit("FAIL", f"{ctx} — steps[{i}] に id がありません")
        else:
            if step_id in seen_ids:
                _emit("FAIL", f"{ctx} — steps[].id '{step_id}' が重複しています")
            seen_ids.add(step_id)

        # instruction 必須
        instr = step.get("instruction")
        if not instr:
            _emit("FAIL", f"{step_ctx} — instruction がありません")
        else:
            _check_exists(FACETS / "instructions" / f"{instr}.md", step_ctx, "instruction")

        # personas[]
        for persona in (step.get("personas") or []):
            _resolve_persona(persona, step_ctx)

        # policies[]
        for policy in (step.get("policies") or []):
            _check_exists(FACETS / "policies" / f"{policy}.md", step_ctx, f"policies[{policy}]")

        # output_contract
        oc = step.get("output_contract")
        if oc:
            _check_exists(FACETS / "output-contracts" / f"{oc}.md", step_ctx, "output_contract")

        # pattern / gate → patterns/
        _check_pattern_or_gate(step.get("pattern"), step_ctx, "pattern")
        _check_pattern_or_gate(step.get("gate"), step_ctx, "gate")

        # max_retries 型・値域（§3.5）
        max_retries = step.get("max_retries")
        if max_retries is not None:
            if not isinstance(max_retries, int) or max_retries < 1:
                _emit(
                    "FAIL",
                    f"{step_ctx} — max_retries は整数かつ ≥1 でなければなりません（値: {max_retries!r}）",
                )
            if step.get("gate") != "acceptance-gate":
                _emit(
                    "WARN",
                    f"{step_ctx} — max_retries が gate: acceptance-gate でない step に設定されています（無効コンテキスト）",
                )

        # acceptance-gate + acceptance[] 存在推奨
        if step.get("gate") == "acceptance-gate" and not step.get("acceptance"):
            _emit(
                "WARN",
                f"{step_ctx} — gate: acceptance-gate ですが acceptance[] が未定義（ゲートが常時通過する可能性）",
            )

        # extends 子 step ID 突き合わせ（#41）
        if parent_step_ids and step_id not in parent_step_ids and step.get("id"):
            _emit(
                "WARN",
                f"{ctx} (extends: {extends_name}) — child step `{step_id}` は parent に存在しません"
                f"（override タイポの可能性。新規 step として追加する意図なら無視可。SKILL.md §4.2.2）",
            )

    _emit("PASS", f"{ctx}: OK")


# ── メイン ────────────────────────────────────────────────────────────────────
def main() -> None:
    recipe_files = sorted(RECIPES.glob("*.md"))
    if not recipe_files:
        print("[WARN] recipes/ に .md ファイルが見つかりません")
        sys.exit(0)

    for recipe_path in recipe_files:
        try:
            check_recipe(recipe_path)
        except Exception:
            _emit("FAIL", f"recipe {recipe_path.stem} — 予期しないエラー:\n{traceback.format_exc()}")

    print("## rig --validate レポート（CI / shipped tier）\n")
    for line in results:
        print(line)
    print()
    print(f"PASS: {_pass} / WARN: {_warn} / FAIL: {_fail}")

    if _fail > 0:
        print("\n不合格: FAIL が1件以上あります")
        sys.exit(1)
    elif _warn > 0:
        print("\n合格（要対応 WARN あり）")
    else:
        print("\n合格")


if __name__ == "__main__":
    main()
