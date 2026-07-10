"""orchestrate recipes: recipe loading + RESOLVE reference implementation (split from scripts/orchestrate.py)."""

import sys
import os
import re
import pathlib
import subprocess

try:
    import yaml
except ImportError:
    print("[ERROR] PyYAML が見つかりません。`pip install pyyaml`。")
    sys.exit(1)

from . import config

# ── recipe 読み込み ───────────────────────────────────────────────────────────
def parse_frontmatter(path: pathlib.Path) -> dict:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---"):
        return {}
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}
    return yaml.safe_load(parts[1]) or {}


def load_steps(fm: dict) -> list[dict]:
    """recipe frontmatter から決定論的なステップ定義列を抽出する（純関数）。"""
    out = []
    for s in (fm.get("steps") or []):
        if not isinstance(s, dict):
            continue
        gate = s.get("gate")
        out.append({
            "id": s.get("id"),
            "instruction": s.get("instruction"),
            "gate": None if gate in (None, "—", "-") else gate,
            "pattern": s.get("pattern"),
            "personas": list(s.get("personas") or []),       # 並列検証者のロール
            "needs": list(s.get("needs") or []),             # 任意: 依存 step（DAG 並列）
            "acceptance": list(s.get("acceptance") or []),
            "checks": list(s.get("checks") or []),          # 任意: 機械検証コマンド列
            "max_retries": s.get("max_retries") or config.DEFAULT_K,
            "model": s.get("model"),                        # 任意: この step の generator model
            "verifier_model": s.get("verifier_model"),      # 任意: この step の verifier model（分離指定用）
            "output_contract": s.get("output_contract"),
            "condition": s.get("condition"),                 # 任意: 条件付き step（size/flag）
        })
    return out


# ── RESOLVE 参照実装（extends マージ・badge/steps フィールド導出）─────────────
# SKILL.md §4.2.2（extends・1段のみ）と facets/instructions/list.md（badge 固定順・
# steps: フィールド）の決定論参照実装。散文エンジンの表示規則を CI（selftest Q）で
# golden 検証できるようにする＝RESOLVE コード化フェーズ1。

EXTENDS_MAX_DEPTH = 5  # 深すぎる継承は認知経済的に破綻するため CI で FAIL させる (#193)


def _resolve_extends_chain(fm: dict, recipe_path: pathlib.Path,
                            warnings: list[str]) -> list[tuple[str | None, dict]]:
    """extends チェーンを leaf → root の順に辿って [(name, fm), ...] を返す。

    循環（A→B→A 等）と深さ超過（EXTENDS_MAX_DEPTH）は警告を出しつつ切り上げる。
    ペアの name は途中で見つけた祖先の名前（leaf 自体は None）。
    """
    chain: list[tuple[str | None, dict]] = [(None, fm)]
    trail: list[str] = [recipe_path.stem]   # 順序付きの継承経路（循環メッセージ用）
    visited: set[str] = {recipe_path.stem}
    current_fm = fm
    current_path = recipe_path
    while True:
        parent_name = current_fm.get("extends")
        if not parent_name:
            return chain
        if parent_name in visited:
            warnings.append(f"extends: 循環継承を検知しました ({' → '.join(trail)} → {parent_name})。"
                            f"このリンクをここで切り上げます")
            return chain
        if len(chain) >= EXTENDS_MAX_DEPTH:
            warnings.append(f"extends: 継承深さの上限 {EXTENDS_MAX_DEPTH} を超えました "
                            f"（'{parent_name}' 以降を無視）。認知経済的に浅く保ってください")
            return chain
        parent_path = None
        fname = f"{parent_name}.md"
        for base in (current_path.parent, config.PROJECT_RECIPES, config.RECIPES):
            cand = base / fname
            if cand.exists():
                parent_path = cand
                break
        if parent_path is None:
            warnings.append(f"extends: '{parent_name}' が解決できません（{' → '.join(trail)} から辿った）")
            return chain
        parent_fm = parse_frontmatter(parent_path)
        chain.append((parent_name, parent_fm))
        visited.add(parent_name)
        trail.append(parent_name)
        current_fm = parent_fm
        current_path = parent_path


def resolve_extends(fm: dict, recipe_path: pathlib.Path) -> tuple[dict, list[str]]:
    """extends を N 段まで解決し、確定 steps を持つ frontmatter と警告列を返す（純関数的）。

    マージ規則（§4.2.2）:
      - チェーンを [leaf, parent, grandparent, ..., root] の順に集める
      - root の steps をベースに、祖先 → 親 → 子 の順に上書き適用する
      - 各段で `remove: true` は親側から静的除外、既存 id は override、新規 id は末尾追加
      - 循環継承・深さ上限 (EXTENDS_MAX_DEPTH) は警告で切り上げ
      - 継承時の origin マーカーは最終的に "inherited" / "override" / "added" として残る
    """
    warnings: list[str] = []
    raw_steps = [s for s in (fm.get("steps") or []) if isinstance(s, dict)]
    if not fm.get("extends"):
        for s in raw_steps:
            s.setdefault("_origin", None)
        return fm, warnings

    chain = _resolve_extends_chain(fm, recipe_path, warnings)
    if len(chain) == 1:
        # extends は宣言されているが解決失敗（未検出 / 循環 / 深さ超）
        for s in raw_steps:
            s.setdefault("_origin", None)
        return fm, warnings

    # root ancestor の steps を "inherited" として base にする
    root_fm = chain[-1][1]
    merged: list[dict] = []
    for ps in (root_fm.get("steps") or []):
        if isinstance(ps, dict):
            m = dict(ps)
            m["_origin"] = "inherited"
            merged.append(m)

    # 祖先 → 親 → 子 の順に上書き適用（chain は leaf 先頭、reverse で root 先頭に）
    for name, layer_fm in reversed(chain[:-1]):  # root 以外を root→leaf 順に
        index = {s.get("id"): i for i, s in enumerate(merged)}
        for cs in (layer_fm.get("steps") or []):
            if not isinstance(cs, dict):
                continue
            cid = cs.get("id")
            if cs.get("remove") is True:
                if cid in index:
                    merged = [s for s in merged if s.get("id") != cid]
                    index = {s.get("id"): i for i, s in enumerate(merged)}
                else:
                    layer_label = name or "leaf"
                    warnings.append(f"remove: true の step '{cid}' は継承元に存在しません "
                                    f"（{layer_label} 側指定・#144 WARN）")
                continue
            m = dict(cs)
            if cid in index:
                m["_origin"] = "override"
                merged[index[cid]] = m
            else:
                m["_origin"] = "added"
                merged.append(m)
                index[cid] = len(merged) - 1

    # トップレベルキーは leaf 優先で全チェーン重ね（root → ... → leaf）
    out: dict = {}
    for _, layer_fm in reversed(chain):
        out.update({k: v for k, v in layer_fm.items() if k != "steps"})
    out["steps"] = merged
    return out, warnings


def _abbrev_condition(cond: str) -> str:
    """condition 値を steps: フィールドの略記に変換する（list.md #160・20字以内）。"""
    flags = re.findall(r"--[a-z][a-z0-9-]*", cond or "")
    m = re.search(r"size\s*[:：]?\s*([SMLX]+\+?)", cond or "")
    parts = flags + ([m.group(1)] if m else [])
    return ("|".join(parts) or "cond")[:20]


def derive_badges(fm: dict, steps: list[dict]) -> list[str]:
    """--list badge を固定順で導出する（facets/instructions/list.md の並び順と1対1）。"""
    badges: list[str] = []
    if fm.get("tdd") is True:
        badges.append("tdd")
    if any(s.get("gate") == "acceptance-gate" for s in steps):
        badges.append("gated")
    if fm.get("backend") == "workflow":
        badges.append("workflow")
    if fm.get("no_default_personas") is True:
        badges.append("no-defaults")
    if fm.get("orchestrate") is True:
        badges.append("orchestrate")
    elif any(s.get("checks") or s.get("needs") for s in steps):
        badges.append("orchestrate(auto)")
    if fm.get("cross_llm") is True:
        badges.append("cross-llm")
    if fm.get("no_capture") is True:
        badges.append("no-capture")
    if fm.get("adversarial") is True:
        badges.append("adversarial")
    if fm.get("visual") is True:
        badges.append("visual")
    if fm.get("autonomy") == "autonomous":
        badges.append("autonomous")
    if fm.get("no_orchestrate") is True:
        badges.append("no-orchestrate")
    if fm.get("design") is True:
        badges.append("design")
    if fm.get("review") is True:
        badges.append("review")
    if fm.get("capture") is True:
        badges.append("capture")
    if fm.get("verify_findings") is True:
        badges.append("verify-findings")
    return badges


def derive_steps_field(steps: list[dict]) -> str:
    """--list / catalog の steps: フィールド（id 列＋condition 略記）を導出する（list.md #79/#160）。"""
    parts = []
    for s in steps:
        sid = s.get("id") or "?"
        cond = s.get("condition")
        parts.append(f"{sid}?[{_abbrev_condition(cond)}]" if cond else sid)
    return ", ".join(parts)


# ── RESOLVE 参照実装フェーズ2（condition 評価・size 判定・スライス・flag 優先順位）──
# SKILL.md §4.3（flag override）・§4.3.1（--only/--from/--to/--skip）・§4.4（size-aware）の
# 決定論参照実装。selftest R が golden 検証する。

_SIZE_RANK = {"S": 0, "M": 1, "L": 2, "XL": 3}

# recipe frontmatter キー → 等価フラグ（§4.3 の「キーの解釈」群）
_KEY_TO_FLAG = {
    "tdd": "--tdd", "design": "--design", "review": "--review", "visual": "--visual",
    "adversarial": "--adversarial", "cross_llm": "--cross-llm", "orchestrate": "--orchestrate",
    "no_orchestrate": "--no-orchestrate", "no_capture": "--no-capture", "capture": "--capture",
    "no_default_personas": "--no-default-personas", "verify_findings": "--verify-findings",
}


def git_diff_lines() -> int | None:
    """`git diff HEAD --numstat` の増減行数合計（staged + unstaged・§4.4/#185）。取得不能は None。"""
    try:
        r = subprocess.run(["git", "diff", "HEAD", "--numstat"],
                           capture_output=True, text=True, timeout=10, cwd=config.INVOCATION_CWD)
        if r.returncode != 0:
            return None
        total = 0
        for line in r.stdout.splitlines():
            cols = line.split("\t")
            if len(cols) >= 2:
                total += (int(cols[0]) if cols[0].isdigit() else 0)
                total += (int(cols[1]) if cols[1].isdigit() else 0)
        return total
    except Exception:
        return None


def load_manifest() -> dict:
    """`<cwd>/.claude/rig.md` の frontmatter を読む（無ければ空 dict・§4.1）。"""
    path = config.INVOCATION_CWD / ".claude" / "rig.md"
    if not path.exists():
        return {}
    try:
        fm = parse_frontmatter(path)
        return fm if isinstance(fm, dict) else {}
    except Exception:
        return {}


def size_class(diff_lines: int | None, thresholds: dict | None = None) -> str:
    """diff 増減行数 → size class（§4.4。diff 不明は S 既定）。"""
    th = {"S_max": 100, "M_max": 200, "L_max": 400}
    th.update(thresholds or {})
    if diff_lines is None:
        return "S"
    if diff_lines <= th["S_max"]:
        return "S"
    if diff_lines <= th["M_max"]:
        return "M"
    if diff_lines <= th["L_max"]:
        return "L"
    return "XL"


def evaluate_condition(cond: str | None, flags: set[str], size: str) -> tuple[bool, str]:
    """condition 式（例 "--design または size L+"）を評価する（フラグ成分 OR size 成分）。

    どちらの成分も無い・解釈できない condition は常時 OFF（--validate #109 と同じ扱い）。
    """
    if not cond:
        return True, "condition なし"
    cond_flags = re.findall(r"--[a-z][a-z0-9-]*", cond)
    hit = sorted(set(cond_flags) & flags)
    if hit:
        return True, f"flag 解決（{' '.join(hit)}）"
    m = re.search(r"size\s*[:：]?\s*([SMLX]+)\+?", cond)
    if m and m.group(1) in _SIZE_RANK:
        need = m.group(1)
        if _SIZE_RANK[size] >= _SIZE_RANK[need]:
            return True, f"size {need}+ 充足（size {size}）"
        return False, f"size {need}+ 未満（size {size}）"
    if cond_flags:
        return False, f"flag 未指定（{' '.join(sorted(set(cond_flags)))}）"
    return False, "condition 不正（常時 OFF・#109）"


def _levenshtein(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def _suggest(bad: str, ids: list[str]) -> list[str]:
    cands = sorted((d, i) for i in ids if (d := _levenshtein(bad, i)) <= 2)
    return [i for _, i in cands[:3]]


def resolve_effective(recipe_path: pathlib.Path, flags: list[str] | None = None,
                      diff_lines: int | None = None,
                      thresholds: dict | None = None,
                      manifest: dict | None = None) -> dict:
    """RESOLVE の確定結果＝flag override・condition 評価・スライス適用後の実行 step 集合を返す。

    §4.3/§4.3.1/§4.4 の決定論実装。errors があれば実行不能（散文エンジンの ERROR 停止と同じ）。
    manifest（§4.1）は size_thresholds と default_orchestrate を反映する。
    """
    plan = resolve_plan_json(recipe_path)
    fm = parse_frontmatter(recipe_path)
    manifest = manifest or {}
    if thresholds is None and isinstance(manifest.get("size_thresholds"), dict):
        thresholds = manifest["size_thresholds"]
    warnings = list(plan["warnings"])
    errors: list[str] = []

    # ① 有効フラグ集合 ＝ 明示フラグ ∪ recipe キー等価フラグ（§4.3「キーの解釈」）
    fset = set(flags or [])
    for key, flg in _KEY_TO_FLAG.items():
        if fm.get(key) is True:
            fset.add(flg)
    if fm.get("autonomy") == "autonomous":
        fset.add("--autonomous")
    if fm.get("backend") == "workflow":
        fset.add("--workflow")

    # ② size 判定（§4.4）と condition 評価
    size = size_class(diff_lines, thresholds)
    steps = [dict(s) for s in plan["steps"]]
    for s in steps:
        s["active"], s["why"] = evaluate_condition(s.get("condition"), fset, size)

    ids = [s["id"] for s in steps]
    active_ids = [s["id"] for s in steps if s["active"]]

    # ③ スライス（§4.3.1・condition 評価後のリストに適用）
    def _slice_val(name: str) -> str | None:
        lst = flags or []
        return lst[lst.index(name) + 1] if name in lst and lst.index(name) + 1 < len(lst) else None

    only, frm, to = _slice_val("--only"), _slice_val("--from"), _slice_val("--to")
    skips = [v for i, v in enumerate(flags or []) if i > 0 and (flags or [])[i - 1] == "--skip"]

    if only and frm:
        warnings.append("--only と --from の同時指定: --only 優先・--from 無視")
        frm = None
    if only and to:
        warnings.append("--only と --to の同時指定: --only 優先・--to 無視")
        to = None
    if only and skips:
        warnings.append("--only と --skip の同時指定: --only 優先・--skip 無視")
        skips = []

    def _check_id(sid: str, flag: str) -> bool:
        if sid in ids:
            return True
        sug = _suggest(sid, ids)
        errors.append(f"{flag} {sid}: step が見つかりません"
                      + (f"（もしかして: {', '.join(sug)}）" if sug else "")
                      + f"。実行可能な step-id: {', '.join(ids)}")
        return False

    for sid, flg in ((only, "--only"), (frm, "--from"), (to, "--to")):
        if sid:
            _check_id(sid, flg)
    if only and only in ids and only not in active_ids:
        cond = next(s.get("condition") for s in steps if s["id"] == only)
        errors.append(f"--only {only}: condition (\"{cond}\") が現在 OFF です"
                      f"（{next(s['why'] for s in steps if s['id'] == only)}）。"
                      f"有効化フラグを追加してください。")
    if frm and to and frm in ids and to in ids and ids.index(frm) > ids.index(to):
        errors.append(f"--from {frm} --to {to}: step 順序が逆です。実行可能な step-id: {', '.join(ids)}")

    for sid in skips:
        if sid not in ids:
            _check_id(sid, "--skip")  # 存在しない id は ERROR（ケース A）
            continue
        st = next(s for s in steps if s["id"] == sid)
        if not st["active"]:
            warnings.append(f"--skip {sid}: {sid} step はすでに condition-OFF です（--skip は不要）")
        if st.get("gate") == "acceptance-gate":
            warnings.append(f"--skip {sid}: {sid} step は gate: acceptance-gate を持ちます"
                            " — 品質収束ループがスキップされます")

    # ④ active/why の確定（スライス > --skip > condition。明示スキップが最終的に勝つ）
    for s in steps:
        sid = s["id"]
        if only:
            if sid != only:
                s["active"], s["why"] = False, "slice 範囲外（--only）"
        else:
            if frm and frm in ids and ids.index(sid) < ids.index(frm):
                s["active"], s["why"] = False, "slice 範囲外（--from）"
            if to and to in ids and ids.index(sid) > ids.index(to):
                s["active"], s["why"] = False, "slice 範囲外（--to）"
        if sid in skips:
            s["active"], s["why"] = False, "[SKIP: --skip flag]"

    # ⑤ モードサマリ（orchestrate は on > off(打ち消し) > auto の優先順・§4.3）
    auto, auto_why = auto_orchestrate(plan["steps"],
                                      manifest_default=manifest.get("default_orchestrate") is True)
    if "--orchestrate" in fset:
        orch = "on"
    elif "--no-orchestrate" in fset:
        orch = "off"
    elif auto:
        orch = f"auto（{auto_why}）"
    else:
        orch = "off"
    mode = {
        "autonomy": "autonomous" if "--autonomous" in fset else "interactive",
        "backend": "workflow" if "--workflow" in fset else "manual",
        "tdd": "--tdd" in fset,
        "orchestrate": orch,
        "capture": "off" if "--no-capture" in fset else ("auto" if "--capture" in fset else "ask"),
    }
    if "--capture" in fset and "--no-capture" in fset:
        warnings.append("--capture と --no-capture の同時指定: --no-capture 優先（§7.3）")

    plan.update({
        "flags": sorted(fset),
        "size": {"diff_lines": diff_lines, "class": size},
        "steps": steps,
        "effective_steps": [s["id"] for s in steps if s["active"]],
        "slice": {"only": only, "from": frm, "to": to, "skip": skips},
        "mode": mode,
        "warnings": warnings,
        "errors": errors,
    })
    return plan


def resolve_plan_json(recipe_path: pathlib.Path) -> dict:
    """recipe を RESOLVE し、--list/--plan 表示の計算フィールドを JSON で返す（決定論）。"""
    fm = parse_frontmatter(recipe_path)
    extends_name = fm.get("extends")
    resolved_fm, warnings = resolve_extends(fm, recipe_path)
    raw_steps = [s for s in (resolved_fm.get("steps") or []) if isinstance(s, dict)]
    steps = load_steps(resolved_fm)
    for s, raw in zip(steps, raw_steps):
        s["origin"] = raw.get("_origin")
    return {
        "recipe": fm.get("name", recipe_path.stem),
        "extends": extends_name,
        "autonomy": resolved_fm.get("autonomy"),
        "badges": derive_badges(resolved_fm, steps),
        "steps_field": derive_steps_field(steps),
        "n_steps": len(steps),
        "steps": steps,
        "warnings": warnings,
    }

def resolve_recipe(name: str) -> pathlib.Path:
    """recipe を解決する。
    優先順: 絶対/相対パス実在 → cwd/.rig/recipes/<name>.md（プロジェクト overlay） → RIG_HOME/skills/rig/recipes/<name>.md（built-in）。
    overlay が built-in と同名なら overlay が勝つ＝プロジェクト固有レシピで上書き可能。"""
    p = pathlib.Path(name)
    if p.exists():
        return p
    fname = name if name.endswith(".md") else f"{name}.md"
    bases = [config.PROJECT_RECIPES]
    org = os.environ.get("RIG_ORG_HOME") or (load_manifest().get("org_dir") or "")
    if org:
        bases.append(pathlib.Path(org).expanduser() / "recipes")  # org tier（チーム共有・§5）
    bases.append(config.RECIPES)
    for base in bases:
        cand = base / fname
        if cand.exists():
            return cand
    print(f"[ERROR] recipe が見つかりません: {name}\n"
          f"  探索: " + ", ".join(str(b / fname) for b in bases))
    sys.exit(1)


def auto_orchestrate(steps: list[dict], manifest_default: bool = False) -> tuple[bool, str]:
    """この recipe が --orchestrate を自動有効化するか（決定論・SKILL §4.3 と同じ規則）。"""
    has_checks = any(s["checks"] for s in steps)
    has_needs = any(s["needs"] for s in steps)
    if has_checks or has_needs:
        why = "・".join(x for x in (["checks"] if has_checks else []) + (["needs"] if has_needs else []))
        return True, f"recipe に {why} 宣言あり"
    if manifest_default:
        return True, "manifest default_orchestrate: true"
    return False, "明示 opt-in のみ（自動有効化なし）"

