#!/usr/bin/env python3
"""
rig 計算的オーケストレータ（deterministic orchestration runner）

recipe のステップ DAG を**コードが**解釈し、遷移・ゲート・停止条件・状態保持を
決定論的に強制する薄いランナー。rig engine（SKILL.md）の「制御ループを散文で
モデルに握らせる」弱点を埋める層＝舵をコードが握る（engine 不変・opt-in）。

モデルは各ステップの「作業」をするが、「次に何をするか」はこのランナーが決める：
  plan   <recipe.md> [--json] [--with "<flags>"] [--diff-lines N | --diff-git]
                                     ステップ状態機械を決定論的に算出（モデル不要）。--json は RESOLVE
                                     一次実装＝extends マージ（remove/origin）・badge/steps: 導出・
                                     condition 評価・size 判定・スライス・flag 優先順位を機械出力。
                                     --diff-git は git diff HEAD から行数を自動測定、manifest
                                     （.claude/rig.md）の size_thresholds / default_orchestrate を反映
                                     （selftest Q/R/S が golden 検証・散文エンジンは RESOLVE 時にこれを呼ぶ）
  init   <recipe.md> [--goal G]      run-state を作成し最初のアクションを出す
  check  <state.json>                現ステップの checks: (shell) を実行し pass/fail 記録（計算的センサー）
  verdict<state.json> --by N --pass  独立検証者の推論的判定を記録（採点者≠生成者を強制）
  next   <state.json>                次の遷移を決定論的に計算・適用して出力
  status <state.json>                現在の状態を出力
  runs   [--limit N] [--recipe R] [--personas]  実行テレメトリ（.rig/runs.jsonl）の一覧・recipe 別集計・検証者別の票集計\n  party                              パーティ編成画面（/rig:party）＝テレメトリ/drill 実測から RPG 風ステータスを描画\n  run … --verifier-providers a,b,c   モデル混成クォーラム＝同じ検証 persona を異種プロバイダで並走（票は provider:persona）
  run … --isolate                    使い捨て git worktree に隔離して実行。ゲート green の commit だけ元 branch へ ff 合流、
                                     未達/dirty/非 ff は worktree と branch を保全（determinism-by-gate の空間版）。
                                     verifier ロールの CLI には読み取り専用権限を argv で固定付与（claude --allowedTools / codex --sandbox read-only）
  ab <recipe1> <recipe2>…            同一タスクを複数recipeバリアントで並走実行し、速度/リトライ/結果を比較する（#291）。
                                     各variantは自分専用の隔離worktreeで実行される（--isolateと同じ経路）。
  run … --auto-route                 recipe step の auto_route.candidates（{model,cost_tier,max_size}）から、
                                     現在の diff size（--diff-git 相当の自動測定）に応じて最安の候補 model を
                                     決定論的に選ぶ（#264）。選択理由は run-state の history と runs.jsonl の
                                     steps[].auto_route に記録される。auto_route 未宣言の step には影響しない。
  graph  [--json | --focus <name>]   shipped ブリック群から**型付きグラフ**（injects/extends/uses-*/mirrors 等11種）を導出。\n                                     手で書かない＝frontmatter が source of truth（validate check_graph が CI で整合を強制）\n  install-shim [--to PATH] [--force] ~/.local/bin/rig に shim を symlink（横断利用の入口・1回だけ）
  selftest                           決定論の自己検証（同入力→同遷移を証明）

依存: Python3 + PyYAML（validate.py と同じ）。終了コード 0=正常 / 1=エラー・ESCALATE。
"""

import sys
import os
import re
import json
import shlex
import datetime
import threading
import time
import pathlib
import subprocess
import concurrent.futures as futures

try:
    import yaml
except ImportError:
    print("[ERROR] PyYAML が見つかりません。`pip install pyyaml`。")
    sys.exit(1)

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
    return pathlib.Path(__file__).resolve().parent.parent


RIG_HOME = find_rig_home()
RECIPES = RIG_HOME / "skills" / "rig" / "recipes"
INVOCATION_CWD = pathlib.Path(os.getcwd()).resolve()
PROJECT_RECIPES = INVOCATION_CWD / ".rig" / "recipes"  # プロジェクト overlay
RUNS_PATH = INVOCATION_CWD / ".rig" / "runs.jsonl"     # 実行テレメトリ（run-state と同格の実行ログ）
DRILL_PATH = INVOCATION_CWD / ".rig" / "drill-results.jsonl"  # /rig:drill の実測結果（検出率）
DEFAULT_K = 2  # acceptance-gate の既定リトライ上限（SKILL §3.5）

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
            "max_retries": s.get("max_retries") or DEFAULT_K,
            "model": s.get("model"),                        # 任意: この step の generator model
            "verifier_model": s.get("verifier_model"),      # 任意: この step の verifier model（分離指定用）
            "output_contract": s.get("output_contract"),
            "condition": s.get("condition"),                 # 任意: 条件付き step（size/flag）
            "auto_route": s.get("auto_route"),               # 任意: --auto-route 時の候補群（#264）
        })
    return out


# ── auto-route（コストティア自動ルーティング・#264）───────────────────────────
_SIZE_ORDER = ["S", "M", "L", "XL"]


def resolve_auto_route(step: dict, size: str) -> tuple[str | None, str]:
    """`step["auto_route"]["candidates"]`（各 {model, cost_tier, max_size}）から、
    現在の size class に応じて最も安い候補を決定論的に選ぶ（純関数・selftest 対象）。

    候補は宣言順に見て、`max_size` が現在の size 以上の**最初の候補**を採用する
    （= 安価な候補から順に「これで間に合うか」を判定する早期採用）。該当が無ければ
    最後の候補（最も高性能）にフォールバックする。`auto_route` 未宣言や `candidates`
    空なら (None, 理由) を返し、呼び出し側は既存の `model:` / `--model` に委ねる。
    """
    ar = step.get("auto_route")
    if not isinstance(ar, dict) or not ar.get("candidates"):
        return None, "auto_route 未宣言"
    candidates = ar["candidates"]
    size_idx = _SIZE_ORDER.index(size) if size in _SIZE_ORDER else len(_SIZE_ORDER) - 1
    for c in candidates:
        c_max = c.get("max_size", "XL")
        c_idx = _SIZE_ORDER.index(c_max) if c_max in _SIZE_ORDER else len(_SIZE_ORDER) - 1
        if c_idx >= size_idx:
            return c.get("model"), f"size={size} → max_size={c_max} の候補（{c.get('cost_tier', '?')}）"
    last = candidates[-1]
    return last.get("model"), f"size={size} がどの候補の max_size も超過 → 最終候補にフォールバック（{last.get('cost_tier', '?')}）"


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
        for base in (current_path.parent, PROJECT_RECIPES, RECIPES):
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
                           capture_output=True, text=True, timeout=10, cwd=INVOCATION_CWD)
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
    path = INVOCATION_CWD / ".claude" / "rig.md"
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


# ── run-state ────────────────────────────────────────────────────────────────
def new_state(recipe: str, steps: list[dict], goal: str | None) -> dict:
    return {
        "recipe": recipe,
        "goal": goal,
        "steps": steps,
        "cursor": 0,
        "step_state": {s["id"]: {"status": "pending", "retries": 0, "checks": [], "verdicts": []}
                       for s in steps},
        "stopped": None,
        "done": False,
        "history": [],
    }


def save_state(state: dict, path: pathlib.Path) -> None:
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def telemetry_append(state: dict, final: str) -> None:
    """RUN 1回分のサマリを .rig/runs.jsonl に1行 JSON で追記する（実行テレメトリ）。

    run-state.json と同格の実行ログであり knowledge 層ではない（承認不要・.rig/ は gitignore 済み）。
    集計は `runs` サブコマンド。書き込み失敗で RUN の結果を壊さない（best-effort）。
    """
    try:
        ss = state["step_state"]
        # step id → 直近の auto-route 決定（#264。同一 step が retry で複数回通っても最後の決定を採用）
        auto_routed: dict[str, dict] = {}
        for h in state.get("history", []):
            if h.get("action") == "AUTO_ROUTE":
                auto_routed[h["step"]] = {"model": h.get("model"), "reason": h.get("reason")}
        rec = {
            "ts": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
            "recipe": state["recipe"],
            "backend": "orchestrate",
            "invoker": os.environ.get("RIG_INVOKER") or "direct",
            "final": final,
            "steps_total": len(state["steps"]),
            "steps_passed": sum(1 for st in ss.values() if st.get("status") == "passed"),
            "retries": sum(st.get("retries", 0) for st in ss.values()),
            "escalated_at": (state.get("stopped") or {}).get("at") if state.get("stopped") else None,
            "steps": [{"id": s["id"], "status": ss[s["id"]].get("status"),
                       "retries": ss[s["id"]].get("retries", 0),
                       "verdicts": [{"by": v.get("by"), "ok": bool(v.get("ok"))}
                                    for v in ss[s["id"]].get("verdicts", [])],
                       **({"auto_route": auto_routed[s["id"]]} if s["id"] in auto_routed else {})}
                      for s in state["steps"]],
        }
        RUNS_PATH.parent.mkdir(parents=True, exist_ok=True)
        with RUNS_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass

    # ── グローバル・インデックス（~/.rig/runs.jsonl）にもミラー ────────────
    # プロジェクト単位のログ（cwd/.rig）を残しつつ、「全プロジェクトで rig-wb を
    # どれだけ使ったか」を横断集計できるようにする。`project` フィールドで来歴を保持。
    # 書き込み失敗は握りつぶす（best-effort、cwd 側の記録が主）。
    try:
        global_path = pathlib.Path.home() / ".rig" / "runs.jsonl"
        global_path.parent.mkdir(parents=True, exist_ok=True)
        # cwd の記録が確定した後（rec が完成した状態）で project を付けて写す
        global_rec = dict(rec)
        global_rec["project"] = str(INVOCATION_CWD)
        with global_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(global_rec, ensure_ascii=False) + "\n")
    except Exception:
        pass


def load_state(path: pathlib.Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


# ── ゲート評価（決定論・純関数）────────────────────────────────────────────────
def gate_outcome(step: dict, st: dict) -> str:
    """現ステップの合否を決定論的に判定する。
    返り値: pass | fail | incomplete | self-graded
    """
    gate = step["gate"]
    if not gate:
        return "pass"  # ゲート無し step は素通り

    declared = step["checks"]
    ran = st["checks"]
    verdicts = st["verdicts"]

    # 計算的センサー（checks）— 宣言があれば一次根拠。全件実行＆全 ok を要求。
    if declared:
        if len(ran) < len(declared):
            return "incomplete"        # まだ check していない
        if any(not c["ok"] for c in ran):
            return "fail"

    # 推論的検証（verdict）— acceptance-gate/review-gate は独立判定を要求（checks 未宣言時）。
    needs_verdict = gate in ("acceptance-gate", "review-gate") and not declared
    if needs_verdict and not verdicts:
        return "incomplete"            # 独立検証者の判定待ち

    # 採点者≠生成者の強制（self-grading バイアス防止・policies/independent-verification）
    if any(str(v.get("by", "")).lower() in ("", "self", "generator", "producer") for v in verdicts):
        return "self-graded"
    if any(not v["ok"] for v in verdicts):
        return "fail"

    return "pass"


def compute_next(state: dict) -> tuple[str, str]:
    """状態から次のアクションを決定論的に計算して適用する（state を破壊的更新）。
    返り値: (action_code, message)
    """
    if state["stopped"]:
        return "STOPPED", f"停止済み: {state['stopped']['reason']}"
    steps = state["steps"]
    if state["cursor"] >= len(steps):
        state["done"] = True
        return "DONE", "全ステップ完了。"

    step = steps[state["cursor"]]
    sid = step["id"]
    st = state["step_state"][sid]

    if st["status"] == "pending":
        st["status"] = "running"
        state["history"].append({"action": "START", "step": sid})
        gate = step["gate"] or "なし"
        need = []
        if step["checks"]:
            need.append(f"check（{len(step['checks'])} 件の機械検証）")
        if step["gate"] in ("acceptance-gate", "review-gate") and not step["checks"]:
            need.append("verdict（独立検証者の判定・採点者≠生成者）")
        need_s = " → ".join(need) if need else "（ゲート無し＝作業後そのまま next）"
        return "START", (f"step `{sid}` を実行（instruction: {step['instruction']} / gate: {gate}）。"
                         f"作業を委譲し、{need_s} を済ませてから `next`。")

    # status == "running"
    outcome = gate_outcome(step, st)
    if outcome == "incomplete":
        return "AWAIT", f"step `{sid}` はゲート評価待ち。`check` / `verdict` を実行してから `next`。"
    if outcome == "self-graded":
        return "BLOCKED", (f"step `{sid}`: 生成者自身の判定（by=self/generator）は不可。"
                           f"独立した検証者の `verdict` が必要（採点者≠生成者）。")
    if outcome == "pass":
        st["status"] = "passed"
        state["cursor"] += 1
        state["history"].append({"action": "PASS", "step": sid})
        if state["cursor"] >= len(steps):
            state["done"] = True
            return "DONE", f"step `{sid}` 合格。全ステップ完了。"
        nxt = steps[state["cursor"]]["id"]
        return "ADVANCE", f"step `{sid}` 合格 → 次は step `{nxt}`。`next` で開始。"
    # fail
    st["retries"] += 1
    K = step["max_retries"]
    state["history"].append({"action": "FAIL", "step": sid, "try": st["retries"]})
    if st["retries"] >= K:
        state["stopped"] = {"reason": f"step `{sid}` がゲート未達のまま {K} 回 → エスカレーション", "at": sid}
        return "ESCALATE", state["stopped"]["reason"] + "（無限ループ禁止・ユーザーへ）。"
    # リトライ: この step をやり直し（記録はリセット）
    st["status"] = "pending"
    st["checks"] = []
    st["verdicts"] = []
    return "RETRY", f"step `{sid}` 未達 → やり直し（try {st['retries']+1}/{K}）。指摘を反映して再実行。"


# ── コマンド ──────────────────────────────────────────────────────────────────
def resolve_recipe(name: str) -> pathlib.Path:
    """recipe を解決する。
    優先順: 絶対/相対パス実在 → cwd/.rig/recipes/<name>.md（プロジェクト overlay） → RIG_HOME/skills/rig/recipes/<name>.md（built-in）。
    overlay が built-in と同名なら overlay が勝つ＝プロジェクト固有レシピで上書き可能。"""
    p = pathlib.Path(name)
    if p.exists():
        return p
    fname = name if name.endswith(".md") else f"{name}.md"
    bases = [PROJECT_RECIPES]
    org = os.environ.get("RIG_ORG_HOME") or (load_manifest().get("org_dir") or "")
    if org:
        bases.append(pathlib.Path(org).expanduser() / "recipes")  # org tier（チーム共有・§5）
    bases.append(RECIPES)
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


def render_plan(recipe: str, steps: list[dict]) -> str:
    auto, why = auto_orchestrate(steps)
    lines = [f"## rig 計算的プラン: {recipe}", "",
             f"ステップ数: {len(steps)} ／ 遷移はコードが強制（決定論）",
             f"自動 orchestrate: {'auto ON' if auto else 'off'}（{why}）", ""]
    for i, s in enumerate(steps):
        gate = s["gate"] or "なし"
        sensor = ("計算的センサー " + str(len(s["checks"])) + "件"
                  if s["checks"] else
                  ("独立 verdict 要" if s["gate"] in ("acceptance-gate", "review-gate") else "—"))
        lines.append(f"  [{i}] {s['id']}  gate={gate}  K={s['max_retries']}  検証={sensor}")
    lines.append("")
    lines.append("停止条件: 各 step はゲート未達が K 回でエスカレーション（無限ループ禁止）。")
    return "\n".join(lines)


def cmd_plan(args):
    path = resolve_recipe(args[0])
    with_flags: list[str] | None = None
    diff_lines: int | None = None
    use_git_diff = False
    i = 1
    while i < len(args):
        if args[i] == "--with" and i + 1 < len(args):
            with_flags = shlex.split(args[i + 1]); i += 2
        elif args[i] == "--diff-lines" and i + 1 < len(args):
            diff_lines = int(args[i + 1]); i += 2
        elif args[i] == "--diff-git":
            use_git_diff = True; i += 1
        else:
            i += 1
    if use_git_diff and diff_lines is None:
        diff_lines = git_diff_lines()  # 取得不能は None → size S 既定（#185）
    if with_flags is not None or diff_lines is not None or use_git_diff:
        plan = resolve_effective(path, with_flags, diff_lines, manifest=load_manifest())
    else:
        plan = resolve_plan_json(path)
    if "--json" in args:
        print(json.dumps(plan, ensure_ascii=False, indent=2))
        return
    print(render_plan(plan["recipe"], plan["steps"]))
    for w in plan.get("warnings", []):
        print(f"[WARN] {w}")
    for e in plan.get("errors", []):
        print(f"[ERROR] {e}")
    if plan.get("errors"):
        sys.exit(1)


def _state_path(args, default="run-state.json") -> pathlib.Path:
    return pathlib.Path(args[0]) if args else pathlib.Path(default)


def cmd_init(args):
    path = resolve_recipe(args[0])
    fm, _warns = resolve_extends(parse_frontmatter(path), path)
    steps = load_steps(fm)
    goal = None
    out = pathlib.Path("run-state.json")
    i = 1
    while i < len(args):
        if args[i] == "--goal" and i + 1 < len(args):
            goal = args[i + 1]; i += 2
        elif args[i] == "--out" and i + 1 < len(args):
            out = pathlib.Path(args[i + 1]); i += 2
        else:
            i += 1
    state = new_state(fm.get("name", path.stem), steps, goal)
    save_state(state, out)
    print(render_plan(state["recipe"], steps))
    print(f"\nrun-state: {out}")
    action, msg = compute_next(state)
    save_state(state, out)
    print(f"\n▶ {action}: {msg}")


def _current_running(state: dict):
    if state["cursor"] >= len(state["steps"]):
        return None, None
    step = state["steps"][state["cursor"]]
    st = state["step_state"][step["id"]]
    if st["status"] != "running":
        return None, None
    return step, st


def cmd_check(args):
    sp = _state_path(args)
    state = load_state(sp)
    step, st = _current_running(state)
    if not step:
        print("[ERROR] 実行中(running)の step がありません。先に `next` で START してください。")
        sys.exit(1)
    if not step["checks"]:
        print(f"step `{step['id']}` に checks: は未宣言（機械検証なし）。verdict を使ってください。")
        return
    print(f"## check: step `{step['id']}` の計算的センサー（{len(step['checks'])} 件）")
    st["checks"] = []
    all_ok = True
    for cmd in step["checks"]:
        r = subprocess.run(cmd, shell=True, cwd=str(INVOCATION_CWD),
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        ok = (r.returncode == 0)
        all_ok = all_ok and ok
        st["checks"].append({"cmd": cmd, "ok": ok})
        print(f"  [{'OK ' if ok else 'NG '}] {cmd}  (exit {r.returncode})")
    save_state(state, sp)
    print(f"→ {'全件 OK' if all_ok else 'NG あり'}。`next` で遷移を計算。")


def cmd_verdict(args):
    sp = _state_path(args)
    state = load_state(sp)
    step, st = _current_running(state)
    if not step:
        print("[ERROR] 実行中(running)の step がありません。")
        sys.exit(1)
    by, ok, note = None, None, ""
    i = 1
    while i < len(args):
        if args[i] == "--by" and i + 1 < len(args):
            by = args[i + 1]; i += 2
        elif args[i] == "--pass":
            ok = True; i += 1
        elif args[i] == "--fail":
            ok = False; i += 1
        elif args[i] == "--note" and i + 1 < len(args):
            note = args[i + 1]; i += 2
        else:
            i += 1
    if by is None or ok is None:
        print("[ERROR] --by <検証者名> と --pass|--fail が必須。")
        sys.exit(1)
    st["verdicts"].append({"by": by, "ok": ok, "note": note})
    save_state(state, sp)
    guard = "（独立）" if by.lower() not in ("self", "generator", "producer") else "（⚠ 生成者自身＝無効）"
    print(f"verdict 記録: step `{step['id']}` by={by}{guard} → {'PASS' if ok else 'FAIL'}。`next` へ。")


def cmd_next(args):
    sp = _state_path(args)
    state = load_state(sp)
    action, msg = compute_next(state)
    save_state(state, sp)
    print(f"▶ {action}: {msg}")
    if action == "ESCALATE":
        sys.exit(1)


def cmd_status(args):
    sp = _state_path(args)
    state = load_state(sp)
    print(f"## run: {state['recipe']}  cursor={state['cursor']}/{len(state['steps'])}  "
          f"done={state['done']}  stopped={bool(state['stopped'])}")
    for s in state["steps"]:
        st = state["step_state"][s["id"]]
        print(f"  {s['id']:<14} {st['status']:<9} retries={st['retries']} "
              f"checks={sum(1 for c in st['checks'] if c['ok'])}/{len(st['checks'])} "
              f"verdicts={len(st['verdicts'])}")


# ── 実行層（外部ランナー・プロバイダ抽象）──────────────────────────────────────
# 各 step を「別プロセスのエージェント」で実行する＝プロセス境界で context を隔離。
# 検証は「別プロバイダ/別プロセス」で回す＝構造的に採点者≠生成者。
# 既定プロバイダは無し（明示必須）。本物の claude/codex は配線のみ、テストは mock。

MOCK_SRC = (
    "import sys\n"
    "role = sys.argv[1] if len(sys.argv) > 1 else 'generator'\n"
    "persona = sys.argv[2] if len(sys.argv) > 2 else ''\n"
    "if role == 'verifier':\n"
    "    print('独立検証（mock）: ' + persona)\n"
    "    print('VERDICT: ' + ('FAIL' if 'fail' in persona else 'PASS'))\n"
    "else:\n"
    "    print('## step 実行結果（mock）')\n"
    "    print('STATUS: done')\n"
)

RIG_GEN_PREFIX = ("`rig` skill を Skill ツールで起動し、その engine（PARSE→RESOLVE→COMPOSE→RUN・"
                  "context-minimal）に従って次の step を実行してください。\n")
RIG_VER_PREFIX = ("`rig` skill を Skill ツールで起動し、独立した検証者として（この step を生成した"
                  "エージェントとは別プロセス）受け入れ基準を判定し、最後に必ず 'VERDICT: PASS' か "
                  "'VERDICT: FAIL' を出力してください。\n")

# 採点者≠生成者を「別プロセス」からさらに一段強制する：verifier ロールの CLI には
# **読み取り専用の権限フラグを argv で固定付与**する（プロンプトのお願いではなく機構）。
# 検証役は書けない＝レビュー中の成果物改変・自己修正の混入が構造的に起きない。
_READONLY_ENFCE = {
    "claude": ["--allowedTools", "Read,Grep,Glob"],   # headless の tool 許可リスト
    "codex":  ["--sandbox", "read-only"],              # codex exec のサンドボックス
}


def build_argv(provider: str, role: str, prompt: str, cfg: dict, persona: str = "") -> list[str]:
    if provider == "mock":
        return ["python3", "-c", MOCK_SRC, role, persona]
    if provider == "rig":
        # 各 step を「rig ハーネス」として claude ヘッドレスで起動（rig を名前で呼ぶ）。
        pre = RIG_VER_PREFIX if role == "verifier" else RIG_GEN_PREFIX
        argv = ["claude", "-p", pre + prompt, "--output-format", "text"]
        if cfg.get("model"):
            argv += ["--model", cfg["model"]]              # per-step model 対応
        return argv + _READONLY_ENFCE["claude"] if role == "verifier" else argv
    if provider == "claude":
        # ヘッドレス。実運用は権限モード等をユーザーが --provider-cmd で調整可。
        argv = ["claude", "-p", prompt, "--output-format", "text"]
        if cfg.get("model"):
            argv += ["--model", cfg["model"]]              # per-step model 対応
        return argv + _READONLY_ENFCE["claude"] if role == "verifier" else argv
    if provider == "codex":
        # --skip-git-repo-check: 非 git ディレクトリ（横断利用の overlay 先など）でも
        # codex が起動拒否しないように。サンドボックスは無効化しないので安全。
        argv = ["codex", "exec", "--skip-git-repo-check"]
        if cfg.get("model"):
            argv += ["-m", cfg["model"]]                   # per-step model 対応
        if role == "verifier":
            argv += _READONLY_ENFCE["codex"]
        return argv + [prompt]
    if provider == "cmd":
        tmpl = cfg.get("provider_cmd") or ""
        if not tmpl:
            raise SystemExit("[ERROR] --provider cmd には --provider-cmd \"... {prompt} ...\" が必須")
        # shlex で引用符・空白を尊重（実 codex 等のラッパーを安全に渡せる）
        return [a.replace("{prompt}", prompt).replace("{role}", role).replace("{persona}", persona)
                for a in shlex.split(tmpl)]
    raise SystemExit(f"[ERROR] 未知のプロバイダ: {provider}")


# ── ローカル LLM（OpenAI 互換 HTTP）─────────────────────────────────────────────
# ollama / lmstudio はローカルサーバの OpenAI 互換エンドポイント（/v1 ルート）を叩く。
# 各リクエストは独立（ステートレス）＝context 隔離は保たれる。要: サーバ起動＋モデル。
_OPENAI_BASE = {
    "lmstudio": "http://localhost:1234/v1",    # LM Studio（Local Server を起動）
    "ollama":   "http://localhost:11434/v1",    # ollama serve（OpenAI 互換）
}
_DEFAULT_MODEL = {"lmstudio": "local-model", "ollama": "llama3.1"}
_MODELS_CACHE_PATH = pathlib.Path(os.path.expanduser("~/.claude/rig/models.json"))


def _base_url(provider: str, cfg: dict) -> str:
    return (cfg.get("base_url") or _OPENAI_BASE[provider]).rstrip("/")


def _http_get_json(url: str, timeout: float) -> dict | None:
    import urllib.request
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception:
        return None


def list_models(provider: str, cfg: dict) -> list[str]:
    """サーバの /v1/models から利用可能モデル id を取得（不可なら空）。"""
    data = _http_get_json(f"{_base_url(provider, cfg)}/models", cfg.get("timeout", 8))
    if not data:
        return []
    return [m.get("id") for m in (data.get("data") or []) if m.get("id")]


def resolve_http_model(provider: str, cfg: dict) -> str:
    """使用モデルを解決する。優先: --model → 保存設定 → サーバ実機の先頭 → 既定。
    --auto-model 指定時は実機から動的取得して設定する。"""
    if cfg.get("model"):
        return cfg["model"]
    if cfg.get("auto_model"):
        saved = _load_models_config().get(provider, {})
        if saved.get("default"):
            return saved["default"]
        live = list_models(provider, cfg)
        if live:
            return live[0]
    return _DEFAULT_MODEL.get(provider, "local-model")


def _load_models_config() -> dict:
    try:
        return json.loads(_MODELS_CACHE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def run_http_provider(provider: str, prompt: str, cfg: dict) -> tuple[int, str]:
    import urllib.request
    url = f"{_base_url(provider, cfg)}/chat/completions"
    model = resolve_http_model(provider, cfg)
    body = json.dumps({"model": model, "temperature": 0,
                       "messages": [{"role": "user", "content": prompt}]}).encode("utf-8")
    req = urllib.request.Request(url, data=body,
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=cfg.get("timeout", 600)) as r:
            data = json.loads(r.read().decode("utf-8"))
        return 0, data["choices"][0]["message"]["content"]
    except Exception as e:                      # 接続不可・モデル無し等は rc!=0 で握り潰す
        return 1, f"[{provider} error: {e} @ {url}]"


def discover_models(cfg: dict) -> dict:
    """利用可能なプロバイダとモデルを動的に探索する（決定論的にソート）。"""
    import shutil
    out: dict = {}
    for p in sorted(_OPENAI_BASE):
        models = sorted(list_models(p, cfg))
        out[p] = {"kind": "local-http", "base_url": _base_url(p, cfg),
                  "reachable": bool(models), "models": models,
                  "default": models[0] if models else None}
    for p in ("claude", "codex"):               # CLI 系は presence のみ
        out[p] = {"kind": "cli", "available": shutil.which(p) is not None, "models": []}
    out["rig"] = {"kind": "cli", "available": shutil.which("claude") is not None,
                  "note": "各 step を rig ハーネス(claude)で起動", "models": []}
    return out


def cmd_models(args):
    cfg: dict = {}
    save = "--save" in args
    as_json = "--json" in args
    i = 0
    while i < len(args):
        if args[i] == "--base-url" and i + 1 < len(args):
            cfg["base_url"] = args[i + 1]; i += 2
        else:
            i += 1
    found = discover_models(cfg)
    if as_json:
        print(json.dumps(found, ensure_ascii=False, indent=2))
    else:
        print("## rig orchestrate: 利用可能モデル探索\n")
        for p, info in found.items():
            if info["kind"] == "local-http":
                status = (f"✓ {', '.join(info['models'])}" if info["reachable"]
                          else f"✗ サーバ未起動/モデル無し @ {info['base_url']}")
                print(f"  {p:<10} {status}")
            else:
                av = "✓ CLI あり" if info.get("available") else "✗ CLI 無し"
                print(f"  {p:<10} {av}{'  — ' + info['note'] if info.get('note') else ''}")
    if save:
        # local-http のみ設定保存（default モデルを次回 --auto-model で使う）
        conf = {p: {"base_url": d["base_url"], "default": d["default"], "models": d["models"]}
                for p, d in found.items() if d["kind"] == "local-http" and d["reachable"]}
        _MODELS_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _MODELS_CACHE_PATH.write_text(json.dumps(conf, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n保存: {_MODELS_CACHE_PATH}（{len(conf)} プロバイダ）— 次回 run --auto-model で使用")


def run_provider(provider: str, role: str, prompt: str, cfg: dict, persona: str = "") -> tuple[int, str]:
    if provider in _OPENAI_BASE:
        return run_http_provider(provider, prompt, cfg)
    argv = build_argv(provider, role, prompt, cfg, persona)
    try:
        r = subprocess.run(argv, input=prompt if provider in ("cmd",) else None,
                           capture_output=True, text=True, timeout=cfg.get("timeout", 600),
                           cwd=cfg.get("cwd") or None)
    except FileNotFoundError:
        return 127, f"[provider not found: {provider}]"
    except subprocess.TimeoutExpired:
        return 124, "[provider timeout]"
    return r.returncode, (r.stdout or "")


def run_verifiers_parallel(ver, prompt: str, personas: list[str],
                           cfg: dict, max_parallel: int) -> list[dict]:
    """N 人の検証者を同時プロセスで走らせ、(persona, provider) 順（決定論）に結果を返す。

    ver に list を渡すと**同じ persona を複数プロバイダで**走らせる＝モデル混成クォーラム
    （同型モデル N 票より異種票の方が相関が低い。不一致そのものがシグナル）。票の by は
    "provider:persona" 形式でテレメトリに残り、runs --personas でモデル別に監査できる。"""
    import concurrent.futures as _f
    vers = ver if isinstance(ver, list) else [ver]
    personas = personas or ["reviewer"]
    tasks = [(v, p) for p in personas for v in vers]

    def _one(task):
        v, p = task
        rc, out = run_provider(v, "verifier", prompt, cfg, persona=p)
        ok = ("VERDICT: PASS" in out) and ("VERDICT: FAIL" not in out)
        return {"by": f"{v}:{p}", "persona": p, "provider": v, "ok": ok, "note": f"exit {rc}"}

    if len(tasks) == 1:
        return [_one(tasks[0])]
    with _f.ThreadPoolExecutor(max_workers=max(1, max_parallel)) as ex:
        res = list(ex.map(_one, tasks))
    return sorted(res, key=lambda r: (r["persona"], r["provider"]))  # 完了順に依らず決定論


def _build_prompt(state: dict, step: dict) -> str:
    return (f"あなたは rig のサブエージェント（{step['id']} 担当）。recipe '{state['recipe']}' の "
            f"step '{step['id']}'（instruction: {step['instruction']}）を実行してください。"
            f"ゴール: {state.get('goal') or '(なし)'}。完了したら最後に 'STATUS: done' を出力。")


def _build_verify_prompt(state: dict, step: dict, product: str) -> str:
    return (f"あなたは独立した検証者です（この step を生成したエージェントとは別プロセス・別ロール）。"
            f"step '{step['id']}' の成果が受け入れ基準を満たすか判定し、最後に必ず "
            f"'VERDICT: PASS' か 'VERDICT: FAIL' を出力してください。\n--- 成果 ---\n{product[:2000]}")


def _run_step_checks(step: dict, st: dict, cfg: dict | None = None) -> None:
    st["checks"] = []
    cwd = (cfg or {}).get("cwd") or str(INVOCATION_CWD)
    for cmd in step["checks"]:
        r = subprocess.run(cmd, shell=True, cwd=cwd,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        st["checks"].append({"cmd": cmd, "ok": r.returncode == 0})


_HIST_LOCK = threading.Lock()


def _generate(state: dict, step: dict, gen_list: list[str], ver: str,
              cfg: dict, max_parallel: int) -> tuple[str | None, str, list[dict]]:
    """単独 or judge-panel で生成。複数なら全 generator を並列に走らせ、
    judge(ver) が最初に PASS した候補（generator 列の順＝決定論）を勝者に選ぶ。
    返り値: (winner_provider | None, product, judged[])。
    per-step の `model:` / `verifier_model:` があれば cfg のコピーに inject する（並列安全）。"""
    gen_cfg = {**cfg, "model": step["model"]} if step.get("model") else cfg
    ver_cfg = {**cfg, "model": step["verifier_model"] or step.get("model") or cfg.get("model")} \
              if (step.get("verifier_model") or step.get("model")) else cfg
    if len(gen_list) == 1:
        _, out = run_provider(gen_list[0], "generator", _build_prompt(state, step), gen_cfg)
        return gen_list[0], out, []
    def _gen(p):
        rc, out = run_provider(p, "generator", _build_prompt(state, step), gen_cfg)
        return {"provider": p, "rc": rc, "out": out}
    with futures.ThreadPoolExecutor(max_workers=max(1, max_parallel)) as ex:
        cands = list(ex.map(_gen, gen_list))
    cands.sort(key=lambda c: gen_list.index(c["provider"]))   # 生成順で評価＝決定論
    judged, winner, product = [], None, cands[0]["out"]
    jver = ver[0] if isinstance(ver, list) else ver            # judge は先頭 verifier プロバイダ
    for c in cands:
        _, jout = run_provider(jver, "verifier", _build_verify_prompt(state, step, c["out"]),
                               ver_cfg, persona="judge")
        ok = ("VERDICT: PASS" in jout) and ("VERDICT: FAIL" not in jout)
        judged.append({"provider": c["provider"], "ok": ok})
        if ok and winner is None:
            winner, product = c["provider"], c["out"]
    return winner, product, judged


def _execute_step(state: dict, step: dict, st: dict, gen_list: list[str], ver: str,
                  cfg: dict, max_parallel: int, quorum: str, log) -> None:
    """1 step を実行：生成（別プロセス・judge-panel 可）→ ゲート根拠（checks or 並列検証）を記録。"""
    effective_step = step
    if cfg.get("auto_route") and step.get("auto_route"):
        size = size_class(git_diff_lines(), load_manifest().get("size_thresholds"))
        routed_model, reason = resolve_auto_route(step, size)
        if routed_model:
            effective_step = {**step, "model": routed_model}
            with _HIST_LOCK:
                state["history"].append({"action": "AUTO_ROUTE", "step": step["id"],
                                         "model": routed_model, "reason": reason})
            log(f"   ↳ auto-route: {routed_model}（{reason}）")
    winner, out, judged = _generate(state, effective_step, gen_list, ver, cfg, max_parallel)
    with _HIST_LOCK:
        state["history"].append({"action": "EXEC", "step": step["id"],
                                 "provider": winner or gen_list[0], "out": out[:200]})
    if judged:
        log(f"   ↳ judge-panel {len(judged)} 案 → 勝者: {winner or '(無し)'}")
    else:
        log(f"   ↳ {gen_list[0]}:generator")
    if step["checks"]:
        _run_step_checks(step, st, cfg)
        log(f"   ↳ checks: {sum(c['ok'] for c in st['checks'])}/{len(st['checks'])} ok")
        return
    if step["gate"] not in ("acceptance-gate", "review-gate"):
        return
    ver_label = "+".join(ver) if isinstance(ver, list) else ver
    if judged:
        # judge-panel は judge が選定＝そのゲート判定を採用（勝者が居れば合格）
        st["verdicts"].append({"by": f"{ver_label}:judge-panel", "ok": winner is not None,
                               "note": "winner=" + str(winner)})
        return
    # 観点検証＝N 人の独立レビュアーを並列プロセスで（採点者≠生成者）
    # per-step の `verifier_model:` があれば cfg のコピーに inject（generator 側とは独立）
    v_cfg = {**cfg, "model": step["verifier_model"] or step.get("model") or cfg.get("model")} \
            if (step.get("verifier_model") or step.get("model")) else cfg
    personas = step["personas"] or ["independent"]
    results = run_verifiers_parallel(ver, _build_verify_prompt(state, step, out),
                                     personas, v_cfg, max_parallel)
    passes, total = sum(1 for r in results if r["ok"]), len(results)
    par = "並列" if total > 1 else "単独"
    log(f"   ↳ {par}検証 {total} 人: PASS {passes}/{total}（quorum={quorum}）")
    if quorum == "majority" and total > 1:
        st["verdicts"].append({
            "by": f"{ver_label}:quorum-majority", "ok": passes * 2 > total,
            "note": f"{passes}/{total} pass; " + ", ".join(
                f"{r['persona']}={'P' if r['ok'] else 'F'}" for r in results)})
    else:
        st["verdicts"].extend(results)


def run_loop(state: dict, sp: pathlib.Path | None, gen: str, ver: str,
             cfg: dict, max_steps: int, quiet: bool = False,
             max_parallel: int = 4, quorum: str = "all",
             generators: list[str] | None = None) -> str:
    """自走ループ。step に needs: があれば DAG 並列モードへ自動切替（独立 step を同時実行）。"""
    log = (lambda *a: None) if quiet else print
    gen_list = generators or [gen]
    if any(s["needs"] for s in state["steps"]):
        final = run_dag(state, sp, gen_list, ver, cfg, max_steps, quiet, max_parallel, quorum)
        telemetry_append(state, final)
        return final
    iters, last = 0, "—"
    while iters < max_steps:
        iters += 1
        action, msg = compute_next(state)
        last = action
        log(f"▶ {action}: {msg}")
        if action == "START":
            step = state["steps"][state["cursor"]]
            _execute_step(state, step, state["step_state"][step["id"]],
                          gen_list, ver, cfg, max_parallel, quorum, log)
            if sp:
                save_state(state, sp)
            continue
        if action in ("ADVANCE", "RETRY", "AWAIT"):
            if sp:
                save_state(state, sp)
            continue
        break  # DONE / ESCALATE / BLOCKED / STOPPED
    if sp:
        save_state(state, sp)
    telemetry_append(state, last)
    return last


def run_dag(state: dict, sp: pathlib.Path | None, gen_list: list[str], ver: str,
            cfg: dict, max_steps: int, quiet: bool, max_parallel: int, quorum: str) -> str:
    """step-DAG 並列ランナー。依存(needs)を満たした独立 step を同時プロセスで実行する。
    各 wave の ready 集合は id 順（決定論）、ゲート評価も id 順に適用。"""
    log = (lambda *a: None) if quiet else print
    state.setdefault("waves", [])
    waves = 0
    while waves < max_steps:
        waves += 1
        if state["stopped"]:
            break
        ss = state["step_state"]
        passed = {sid for sid, st in ss.items() if st["status"] == "passed"}
        if len(passed) == len(state["steps"]):
            state["done"] = True
            log("▶ DONE: 全 step 完了。")
            break
        ready = sorted((s for s in state["steps"]
                        if ss[s["id"]]["status"] == "pending"
                        and all(d in passed for d in s["needs"])),
                       key=lambda s: s["id"])
        if not ready:
            state["stopped"] = {"reason": "DAG: 実行可能な step が無い（依存未充足/失敗）",
                                "kind": "ESCALATE", "at": "—"}
            break
        ids = [s["id"] for s in ready]
        state["waves"].append(ids)
        log(f"▶ WAVE {waves}: {ids} を並列実行")
        for s in ready:
            ss[s["id"]]["status"] = "running"
        with futures.ThreadPoolExecutor(max_workers=max(1, max_parallel)) as ex:
            list(ex.map(lambda s: _execute_step(state, s, ss[s["id"]], gen_list, ver,
                                                cfg, max_parallel, quorum,
                                                (lambda *a: None)), ready))
        for s in ready:                       # ゲート評価を id 順に適用（決定論）
            st = ss[s["id"]]
            outcome = gate_outcome(s, st)
            if outcome == "pass":
                st["status"] = "passed"
                log(f"   ✓ {s['id']}")
            elif outcome == "self-graded":
                state["stopped"] = {"reason": f"{s['id']}: 自己採点（by=self）", "kind": "BLOCKED", "at": s["id"]}
            else:
                st["retries"] += 1
                if st["retries"] >= s["max_retries"]:
                    state["stopped"] = {"reason": f"{s['id']} がゲート未達 {s['max_retries']} 回",
                                        "kind": "ESCALATE", "at": s["id"]}
                else:
                    st["status"], st["checks"], st["verdicts"] = "pending", [], []
                    log(f"   ↻ {s['id']} retry（try {st['retries']+1}/{s['max_retries']}）")
        if sp:
            save_state(state, sp)
    if sp:
        save_state(state, sp)
    if state.get("done"):
        return "DONE"
    if state["stopped"]:
        return state["stopped"].get("kind", "ESCALATE")
    return "—"



# ── worktree 隔離実行（--isolate）────────────────────────────────────────────
# run を使い捨ての git worktree に隔離する：作業ツリーを汚さず、ゲート green の
# 成果だけを元 branch へ ff 合流（未達・dirty・非 ff は branch を残して人へ）。
# 「非決定的な生成をゲートの外に出さない」determinism-by-gate の空間版。

_ISO_SEQ = 0


def setup_isolation(recipe_name: str) -> dict:
    r = subprocess.run(["git", "rev-parse", "--show-toplevel"],
                       capture_output=True, text=True, cwd=str(INVOCATION_CWD))
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


def cmd_run(args):
    if not args:
        print("[ERROR] usage: run <recipe> --provider <name> [--verifier-provider <name>] "
              "[--provider-cmd \"...{prompt}...\"] [--max-steps N] [--goal G] [--out f] [--isolate] [--auto-route]")
        sys.exit(1)
    path = resolve_recipe(args[0])
    fm, _warns = resolve_extends(parse_frontmatter(path), path)
    steps = load_steps(fm)
    gen = ver = None
    generators: list[str] = []
    goal = None
    out = pathlib.Path("run-state.json")
    max_steps = 40
    max_parallel = 4
    quorum = "all"
    cfg: dict = {}
    i = 1
    while i < len(args):
        a = args[i]
        if a == "--provider" and i + 1 < len(args):
            gen = args[i + 1]; i += 2
        elif a == "--generators" and i + 1 < len(args):
            generators = [g.strip() for g in args[i + 1].split(",") if g.strip()]; i += 2
        elif a == "--verifier-provider" and i + 1 < len(args):
            ver = args[i + 1]; i += 2
        elif a == "--verifier-providers" and i + 1 < len(args):
            ver = [v.strip() for v in args[i + 1].split(",") if v.strip()]; i += 2
        elif a == "--provider-cmd" and i + 1 < len(args):
            cfg["provider_cmd"] = args[i + 1]; i += 2
        elif a == "--model" and i + 1 < len(args):
            cfg["model"] = args[i + 1]; i += 2
        elif a == "--base-url" and i + 1 < len(args):
            cfg["base_url"] = args[i + 1]; i += 2
        elif a in ("--auto-model", "--auto-model-setting"):
            cfg["auto_model"] = True; i += 1
        elif a == "--goal" and i + 1 < len(args):
            goal = args[i + 1]; i += 2
        elif a == "--out" and i + 1 < len(args):
            out = pathlib.Path(args[i + 1]); i += 2
        elif a == "--max-steps" and i + 1 < len(args):
            max_steps = int(args[i + 1]); i += 2
        elif a == "--max-parallel" and i + 1 < len(args):
            max_parallel = int(args[i + 1]); i += 2
        elif a == "--quorum" and i + 1 < len(args):
            quorum = args[i + 1]; i += 2
        elif a == "--isolate":
            cfg["isolate"] = True; i += 1
        elif a == "--allow-headless-in-cc":
            cfg["allow_headless_in_cc"] = True; i += 1
        elif a == "--auto-route":
            cfg["auto_route"] = True; i += 1
        else:
            i += 1
    if not gen and generators:
        gen = generators[0]            # --generators だけでも可（先頭を代表に）
    if not gen:
        print("[ERROR] --provider <name>（または --generators a,b,c）が必須"
              "（rig|claude|codex|ollama|lmstudio|cmd|mock）。rig＝各 step を rig ハーネスとして起動（推奨）。"
              "ollama/lmstudio＝ローカル LLM（要サーバ・--model でモデル指定）。テストは mock。")
        sys.exit(1)

    # ── Claude Code 内からの誤起動ガード ─────────────────────────────────────
    # Claude Code の session 内で `--provider claude` / `--provider rig` を使うと
    # `claude -p` を subprocess で spawn する。これは既に走っている session と
    # 別扱いになり、subscription の usage を別バケットに乗せるか、API キーが
    # 設定されていればそちらへ課金される可能性がある（環境依存）。
    # 明示的に `--allow-headless-in-cc` を付けなければ止める。
    _cc_env = os.environ.get("CLAUDECODE") or os.environ.get("CLAUDE_CODE_SESSION_ID")
    _headless_claude = gen in ("claude", "rig") or ver in ("claude", "rig") or \
        any(p in ("claude", "rig") for p in generators) or \
        (isinstance(ver, list) and any(p in ("claude", "rig") for p in ver))
    if _cc_env and _headless_claude and not cfg.get("allow_headless_in_cc"):
        print(
            "[BLOCKED] Claude Code の session 内で `--provider claude` / `--provider rig` は "
            "`claude -p` を別 subprocess で spawn します。\n"
            "\n"
            "既にこの session で Claude を使っているので二重発火・別バケット課金の"
            "可能性があります。次のどれかに切り替えてください:\n"
            "\n"
            "  1. `/rig:rig \"<task>\"` を使う (manual backend = Agent tool 経由・同 session)\n"
            "  2. `--provider ollama` / `--provider lmstudio` (ローカル・課金なし)\n"
            "  3. `--provider mock` (テスト用)\n"
            "  4. どうしても headless で回したいなら `--allow-headless-in-cc` を明示\n"
        )
        sys.exit(1)
    ver = ver or gen  # 未指定なら同プロバイダ（ただし別プロセス・別ロール）
    state = new_state(fm.get("name", path.stem), steps, goal)
    iso = None
    if cfg.get("isolate"):
        iso = setup_isolation(fm.get("name", path.stem))
        cfg["cwd"] = iso["dir"]
        state["isolation"] = iso
        print(f"◈ 隔離実行: worktree={iso['dir']} / branch={iso['branch']}")
    print(render_plan(state["recipe"], steps))
    panel = f" / judge-panel={','.join(generators)}" if len(generators) > 1 else ""
    if isinstance(ver, list):
        panel += f" / model-quorum={','.join(ver)}"
    dag = " / DAG並列" if any(s["needs"] for s in steps) else ""
    print(f"\n自走実行: provider={gen} / verifier={'+'.join(ver) if isinstance(ver, list) else ver} / "
          f"max-steps={max_steps} / 並列={max_parallel} / quorum={quorum}{panel}{dag}\n")
    final = run_loop(state, out, gen, ver, cfg, max_steps,
                     max_parallel=max_parallel, quorum=quorum,
                     generators=(generators or None))
    if iso:
        outcome = teardown_isolation(iso, final)
        state["isolation"]["outcome"] = outcome
        save_state(state, out)
        label = {"merged": f"ゲート green → {iso['branch']} を ff 合流して撤収",
                 "clean-removed": "変更なし → worktree を撤収",
                 "kept": f"worktree と branch を保全（検分してください）: {iso['dir']}"}[outcome]
        print(f"◈ 隔離実行の結末: {label}")
    print(f"\n=== 終了: {final} ===  run-state: {out}")
    sys.exit(1 if final in ("ESCALATE", "BLOCKED") else 0)


def _run_ab_variant(recipe_path: pathlib.Path, goal: str | None, gen: str, ver: str,
                    cfg: dict, max_steps: int, max_parallel: int, quorum: str,
                    out_path: pathlib.Path) -> dict:
    """1 variant（recipe）を隔離worktreeで実行し、比較用サマリを返す（#291 abのヘルパー）。
    cmd_run と同じ実行経路（setup_isolation→run_loop→teardown_isolation）を1関数にまとめ、
    ThreadPoolExecutor から複数variantを真に並走させられるようにする（各variantは自分専用の
    worktreeなのでファイル競合しない・quiet=Trueで出力の混線を防ぐ）。"""
    fm, _warns = resolve_extends(parse_frontmatter(recipe_path), recipe_path)
    steps = load_steps(fm)
    state = new_state(fm.get("name", recipe_path.stem), steps, goal)
    iso = setup_isolation(fm.get("name", recipe_path.stem))
    variant_cfg = {**cfg, "cwd": iso["dir"]}
    state["isolation"] = iso
    t0 = time.monotonic()
    final = run_loop(state, out_path, gen, ver, variant_cfg, max_steps,
                     quiet=True, max_parallel=max_parallel, quorum=quorum)
    elapsed = round(time.monotonic() - t0, 1)
    outcome = teardown_isolation(iso, final)
    state["isolation"]["outcome"] = outcome
    save_state(state, out_path)
    retries = sum(st.get("retries", 0) for st in state["step_state"].values())
    return {
        "recipe": recipe_path.stem,
        "final": final,
        "elapsed_sec": elapsed,
        "retries": retries,
        "worktree_outcome": outcome,
        "worktree_dir": iso["dir"] if outcome == "kept" else None,
    }


def cmd_ab(args):
    """同一タスクを複数recipeバリアントで並走実行し、速度/リトライ/結果を比較する（#291）。

    各variantは`cmd_run --isolate`と同じ隔離worktreeで独立に実行される（ファイル競合なし）ため、
    真に並走（ThreadPoolExecutor）させても安全。--providerで生成/検証roleを指定するのは
    `run`と同じ——比較したいのは「recipeの違い」であってmodel/providerの違いではない前提。
    """
    if len(args) < 2:
        print("[ERROR] usage: ab <recipe1> <recipe2> [...] --provider <name> --goal G "
              "[--verifier-provider V] [--max-steps N] [--model M]")
        sys.exit(1)
    recipes: list[str] = []
    i = 0
    while i < len(args) and not args[i].startswith("--"):
        recipes.append(args[i]); i += 1
    if len(recipes) < 2:
        print("[ERROR] 比較には recipe を2つ以上指定してください")
        sys.exit(1)

    gen = ver = None
    goal = None
    max_steps = 40
    max_parallel = 4
    quorum = "all"
    cfg: dict = {}
    while i < len(args):
        a = args[i]
        if a == "--provider" and i + 1 < len(args):
            gen = args[i + 1]; i += 2
        elif a == "--verifier-provider" and i + 1 < len(args):
            ver = args[i + 1]; i += 2
        elif a == "--goal" and i + 1 < len(args):
            goal = args[i + 1]; i += 2
        elif a == "--max-steps" and i + 1 < len(args):
            max_steps = int(args[i + 1]); i += 2
        elif a == "--model" and i + 1 < len(args):
            cfg["model"] = args[i + 1]; i += 2
        else:
            i += 1
    if not gen:
        print("[ERROR] --provider が必須（rig|claude|codex|ollama|lmstudio|cmd|mock）")
        sys.exit(1)
    ver = ver or gen

    resolved = [resolve_recipe(r) for r in recipes]
    results: list[dict | None] = [None] * len(resolved)
    print(f"◈ A/B実験: {' vs '.join(recipes)}（provider={gen}・並走 {len(resolved)} variant）\n")
    with futures.ThreadPoolExecutor(max_workers=len(resolved)) as ex:
        fut_to_idx = {
            ex.submit(_run_ab_variant, path, goal, gen, ver, dict(cfg), max_steps, max_parallel, quorum,
                     pathlib.Path(f"ab-{path.stem}-state.json")): idx
            for idx, path in enumerate(resolved)
        }
        for fut in futures.as_completed(fut_to_idx):
            results[fut_to_idx[fut]] = fut.result()

    print(f"## rig ab — {' vs '.join(recipes)}\n")
    print(f"{'recipe':<20} {'final':<10} {'elapsed(s)':<12} {'retries':<8} worktree")
    for r in results:
        wt = r["worktree_dir"] or "-"
        print(f"{r['recipe']:<20} {r['final']:<10} {r['elapsed_sec']:<12} {r['retries']:<8} {wt}")
    kept = [r for r in results if r["worktree_outcome"] == "kept"]
    if kept:
        print(f"\n{len(kept)} 件のworktreeが保全されています（未達/dirty）。検分後、"
              f"`git worktree remove --force <dir>`で片付けてください。")


# ── タスクキュー（積んで GO・管理ツール連携）──────────────────────────────────
# 「task を積む → まとめて GO」を、ローカル json か外部管理ツール(GitHub/GitLab Issue)で持つ。
# backend は差し替え式：local（.rig/queue.json）／github（gh CLI）／gitlab（glab CLI）。
# Issue 連携時はラベルで状態管理：rig-queue → rig-running → rig-done / rig-failed。
QUEUE_LABEL = "rig-queue"
# queue list が可視化すべき「アクティブ」ラベル（rig-done は close 済みのため対象外・#211）。
QUEUE_LABELS_ACTIVE = ["rig-queue", "rig-running", "rig-failed"]
# queue が扱う全状態ラベル（旧ラベルの除去対象の算出に使う・#223）。
QUEUE_LABELS_ALL = ["rig-queue", "rig-running", "rig-failed", "rig-done"]
QUEUE_PATH = INVOCATION_CWD / ".rig" / "queue.json"


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
    return _build_prompt({"recipe": "queue", "goal": task}, {"id": "task", "instruction": task})


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


# ── 決定論セルフテスト ────────────────────────────────────────────────────────
def _drive(steps, script):
    """steps と (action_kind, payload) のスクリプトで run を進め、遷移列と最終状態を返す。"""
    state = new_state("selftest", steps, None)
    trace = []
    for kind, payload in script:
        if kind == "next":
            a, _ = compute_next(state)
            trace.append(a)
        elif kind == "check":
            step, st = _current_running(state)
            st["checks"] = [{"cmd": c, "ok": payload} for c in step["checks"]]
        elif kind == "verdict":
            step, st = _current_running(state)
            st["verdicts"].append({"by": payload[0], "ok": payload[1], "note": ""})
    return trace, state


# ── プロバイダ疎通テスト ──────────────────────────────────────────────────────
def _read_jsonl(path: pathlib.Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def cmd_party(_args):
    """パーティ編成画面（/rig:party）: テレメトリ・drill 実測・ブリック在庫から RPG 風ステータスを描画する。

    ゲーム画面に見えるが全行が実データ（runs.jsonl / drill-results.jsonl / shipped ブリック）＝
    ハーネスの健康診断ダッシュボード。読み取り専用。"""
    runs = _read_jsonl(RUNS_PATH)
    drills = _read_jsonl(DRILL_PATH)
    done = sum(1 for r in runs if r.get("final") == "DONE")
    esc = sum(1 for r in runs if r.get("escalated_at"))
    total = len(runs)

    # 検証票の集計（出撃回数・REJECT 数。by は "provider:persona"）
    votes: dict[str, dict] = {}
    for r in runs:
        for st in r.get("steps", []):
            for v in st.get("verdicts", []):
                persona = (v.get("by") or "?").split(":", 1)[-1]
                a = votes.setdefault(persona, {"sorties": 0, "rejects": 0})
                a["sorties"] += 1
                a["rejects"] += 0 if v.get("ok") else 1

    # drill 検出率（drill.md のスキーマ: {"ts":…, "scores":[{"reviewer","detected","seeded","false_positives"}]}）
    atk: dict[str, dict] = {}
    for d in drills:
        for s in d.get("scores", []):
            a = atk.setdefault(s.get("reviewer", "?"), {"detected": 0, "seeded": 0, "fp": 0})
            a["detected"] += s.get("detected", 0)
            a["seeded"] += s.get("seeded", 0)
            a["fp"] += s.get("false_positives", 0)

    # 連続ノーエスカレーションの最長 streak（実績用）
    streak = best = 0
    for r in runs:
        streak = 0 if r.get("escalated_at") else streak + 1
        best = max(best, streak)

    def _line(name: str, bench: bool = False) -> str:
        v = votes.get(name, {"sorties": 0, "rejects": 0})
        a = atk.get(name)
        power = (f"⚔ 検出率 {a['detected'] / a['seeded'] * 100:3.0f}%（drill {a['detected']}/{a['seeded']}"
                 + (f"・誤検出 {a['fp']}" if a["fp"] else "") + "）") if a and a["seeded"] else "⚔ 検出率 未測定（/rig:drill で較正）"
        tag = "（控え）" if bench and v["sorties"] == 0 else ""
        return f"│ {name:22s} {power}  出撃 {v['sorties']:3d} / REJECT {v['rejects']}{tag}"

    party = ["security-reviewer", "design-reviewer", "test-reviewer"]
    bench = ["performance-reviewer", "observability-reviewer", "api-compat-reviewer",
             "migration-reviewer", "docs-reviewer"]
    for extra in (load_manifest().get("default_personas") or []):
        if extra not in party:
            party.append(extra)

    print("━━━ rig パーティ編成（/rig:party）━━━━━━━━━━━━━━━")
    rate = f"{esc / total * 100:.0f}%" if total else "—"
    print(f"Lv.{done}  出走 {total} 回 / DONE {done} / エスカレーション率 {rate}")
    print("┌─ パーティ（review fan-out）" + "─" * 30)
    for name in party:
        print(_line(name))
    if "finding-verifier" in votes:
        fv = votes["finding-verifier"]
        print(f"│ {'finding-verifier':22s} 🛡 反証 {fv['sorties']} 回（棄却の質は runs --personas で監査）")
    print("├─ 控え（--persona で出撃）" + "─" * 31)
    for name in bench:
        print(_line(name, bench=True))
    print("└" + "─" * 56)

    badges = []
    if done >= 1:
        badges.append("🏆 初DONE")
    if best >= 10:
        badges.append("🏆 十連戦無傷（10連続ノーエスカレーション）")
    if total >= 100:
        badges.append("🏆 百戦錬磨（100 runs）")
    if any(a["seeded"] and a["detected"] == a["seeded"] and a["seeded"] >= 2 for a in atk.values()):
        badges.append("🏆 満点狙撃手（drill 全種検出）")
    wiki_n = len(list((INVOCATION_CWD / ".claude" / "rig" / "knowledge" / "wiki").glob("*.md"))) \
        if (INVOCATION_CWD / ".claude" / "rig" / "knowledge" / "wiki").is_dir() else 0
    if wiki_n >= 10:
        badges.append(f"🏆 大図書館（project wiki {wiki_n} ページ）")
    print("実績: " + (" / ".join(badges) if badges else "（まだなし — まず1本 DONE させよう）"))
    if not runs:
        print("\n（テレメトリなし: RUN を回すと .rig/runs.jsonl に蓄積され、この画面が育つ）")
    if not drills:
        print("（検出率 未測定: /rig:drill で reviewer の攻撃力を較正できる）")


def cmd_runs(args):
    """実行テレメトリ一覧: runs [--limit N] [--recipe R] [--personas] [--html <path>] [--since YYYY-MM-DD]。

    .rig/runs.jsonl（telemetry_append が追記・manual backend は SKILL.md §6 が同形式で追記）を
    読み、直近 N 件の一覧と recipe 別の集計（回数・DONE 率・平均リトライ・エスカレーション数）を出す。
    --personas は検証者（verdict の by）別の票を集計し、剪定判断の材料を出す。
    --html <path> は scripts/dashboard.py に委譲して HTML ダッシュボードを書き出す（KPI・sparkline・
    recipe 別バー・verifier 票・直近 run 表を単一ファイル HTML で・外部依存なし）。
    読み取り専用（--list / --validate と同じ点検モード）。
    """
    limit, recipe, personas_mode, html_out, since = 10, None, False, None, None
    i = 0
    while i < len(args):
        if args[i] == "--limit" and i + 1 < len(args):
            limit = int(args[i + 1]); i += 2
        elif args[i] == "--recipe" and i + 1 < len(args):
            recipe = args[i + 1]; i += 2
        elif args[i] == "--personas":
            personas_mode = True; i += 1
        elif args[i] == "--html" and i + 1 < len(args):
            html_out = args[i + 1]; i += 2
        elif args[i] == "--since" and i + 1 < len(args):
            since = args[i + 1]; i += 2
        else:
            i += 1
    if html_out:
        dash = pathlib.Path(__file__).parent / "dashboard.py"
        if not dash.exists():
            print(f"[ERROR] dashboard.py が見つかりません: {dash}")
            sys.exit(1)
        cmd = [sys.executable, str(dash), "--repo", str(INVOCATION_CWD),
               "--out", html_out, "--limit", str(limit)]
        if recipe:
            cmd += ["--recipe", recipe]
        if since:
            cmd += ["--since", since]
        rc = subprocess.run(cmd).returncode
        sys.exit(rc)
    if not RUNS_PATH.exists():
        print(f"実行記録がまだありません（{RUNS_PATH}）。orchestrate run / queue go、"
              "または manual backend のフロー完了（SKILL.md §6）で追記されます。")
        return
    rows = []
    for line in RUNS_PATH.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue  # 壊れた行はスキップ（追記型ログの耐性）
    if recipe:
        rows = [r for r in rows if r.get("recipe") == recipe]
    if not rows:
        print("該当する実行記録がありません。")
        return

    if personas_mode:
        # 検証者別集計: 各 run の steps[].verdicts[] を by で集約する
        stats: dict[str, dict] = {}
        for r in rows:
            for st in r.get("steps", []):
                for v in st.get("verdicts", []):
                    by = v.get("by") or "?"
                    a = stats.setdefault(by, {"votes": 0, "ok": 0, "reject": 0})
                    a["votes"] += 1
                    a["ok" if v.get("ok") else "reject"] += 1
        if not stats:
            print("verdict 記録がまだありません（review-gate / acceptance-gate を通る run で蓄積されます）。")
            return
        print(f"## rig runs --personas（全 {len(rows)} run の検証票）\n")
        print(f"  {'verifier':28s} {'votes':>6s} {'PASS':>6s} {'REJECT':>7s} {'REJECT率':>8s}")
        for by in sorted(stats, key=lambda k: -stats[k]["votes"]):
            a = stats[by]
            print(f"  {by:28s} {a['votes']:6d} {a['ok']:6d} {a['reject']:7d} "
                  f"{a['reject'] / a['votes'] * 100:7.0f}%")
        rubber = [by for by, a in stats.items() if a["votes"] >= 5 and a["reject"] == 0]
        if rubber:
            print("\n  剪定ヒント: " + ", ".join(sorted(rubber))
                  + " は5票以上で一度も REJECT していません（ゴム印化 or 観点が効いていない可能性。"
                    "外すか観点を尖らせる検討材料）")
        return

    print(f"## rig runs（直近 {min(limit, len(rows))} / 全 {len(rows)} 件）\n")
    for r in rows[-limit:]:
        esc = f" / escalated@{r['escalated_at']}" if r.get("escalated_at") else ""
        print(f"  {r.get('ts', '?'):25s} {r.get('recipe', '?'):20s} {r.get('final', '?'):9s} "
              f"steps {r.get('steps_passed', '?')}/{r.get('steps_total', '?')} "
              f"retries {r.get('retries', 0)}{esc}")

    agg: dict[str, dict] = {}
    for r in rows:
        a = agg.setdefault(r.get("recipe", "?"), {"n": 0, "done": 0, "retries": 0, "esc": 0})
        a["n"] += 1
        a["done"] += 1 if r.get("final") == "DONE" else 0
        a["retries"] += r.get("retries", 0)
        a["esc"] += 1 if r.get("escalated_at") else 0
    print("\n## recipe 別集計\n")
    print(f"  {'recipe':20s} {'runs':>5s} {'DONE率':>7s} {'平均retry':>9s} {'esc':>4s}")
    for name in sorted(agg):
        a = agg[name]
        print(f"  {name:20s} {a['n']:5d} {a['done'] / a['n'] * 100:6.0f}% "
              f"{a['retries'] / a['n']:9.1f} {a['esc']:4d}")

    # ギャップ処方箋: 同一 (recipe, step) で2回以上エスカレーションしていたら能力調達を提案する
    # （テレメトリ → /rig:import --discover / /rig:harness への接続＝自己補完ループの入口）
    gaps: dict[tuple, int] = {}
    for r in rows:
        esc_at = r.get("escalated_at")
        if esc_at:
            gaps[(r.get("recipe", "?"), esc_at)] = gaps.get((r.get("recipe", "?"), esc_at), 0) + 1
    hot = {k: v for k, v in gaps.items() if v >= 2}
    if hot:
        print("\n## ギャップ処方箋（同一 step でのエスカレーション反復）\n")
        for (rcp, sid), n in sorted(hot.items(), key=lambda kv: -kv[1]):
            print(f"  {rcp} / {sid}: エスカレーション {n} 回 — 能力調達を検討:"
                  f" /rig:import --discover \"{sid} を強くする skill\""
                  f" ／ 棚卸しは /rig:harness")


def cmd_probe(args):
    """プロバイダを1回叩いて、実際のコマンド・出力・契約パースの可否を表示する。
    例: orchestrate.py probe --provider codex          （検証ロールで VERDICT を確認）
        orchestrate.py probe --provider codex --role generator
        orchestrate.py probe --provider ollama --model llama3.1"""
    provider, role, cfg = None, "verifier", {}
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--provider" and i + 1 < len(args):
            provider = args[i + 1]; i += 2
        elif a == "--role" and i + 1 < len(args):
            role = args[i + 1]; i += 2
        elif a == "--model" and i + 1 < len(args):
            cfg["model"] = args[i + 1]; i += 2
        elif a == "--base-url" and i + 1 < len(args):
            cfg["base_url"] = args[i + 1]; i += 2
        elif a == "--provider-cmd" and i + 1 < len(args):
            cfg["provider_cmd"] = args[i + 1]; i += 2
        else:
            i += 1
    if not provider:
        print("[ERROR] --provider <name> が必須（rig|claude|codex|ollama|lmstudio|cmd|mock）")
        sys.exit(1)
    prompt = ("ある成果が受け入れ基準を満たすか判定し、最後に必ず 'VERDICT: PASS' か "
              "'VERDICT: FAIL' を1行で出力してください。\n成果: 2 + 2 = 4"
              if role == "verifier" else
              "1 + 1 を計算し、最後に 'STATUS: done' を出力してください。")
    sig = "VERDICT" if role == "verifier" else "STATUS"
    print(f"## probe: provider={provider} / role={role}")
    if provider in _OPENAI_BASE:
        print(f"  endpoint : {_base_url(provider, cfg)}/chat/completions")
        print(f"  model    : {resolve_http_model(provider, cfg)}")
    else:
        argv = build_argv(provider, role, "<PROMPT>", cfg, "probe")
        print("  command  : " + " ".join(shlex.quote(a) for a in argv))
    rc, out = run_provider(provider, role, prompt, cfg, persona="probe")
    found = sig in (out or "")
    print(f"  exit     : {rc}")
    print("  --- 出力（先頭 600 字） ---")
    print("  " + (out or "")[:600].replace("\n", "\n  "))
    print(f"  → {sig} 検出: " + ("✓ パース可能（rig から使えます）" if found
                                else f"✗ 見つからない（プロンプト/フラグ調整が必要。cmd プロバイダで実コマンドを指定可）"))
    sys.exit(0 if (rc == 0 and found) else 1)


def cmd_install_shim(args):
    """~/.local/bin/rig （または --to で指定したパス）に shim を symlink で配置する。
    1 回実行すれば、以降どのディレクトリからでも `rig <subcommand>` で起動できる。"""
    target = pathlib.Path("~/.local/bin/rig").expanduser()
    force = False
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--to" and i + 1 < len(args):
            target = pathlib.Path(args[i + 1]).expanduser(); i += 2
        elif a in ("--force", "-f"):
            force = True; i += 1
        else:
            i += 1
    src = RIG_HOME / ".claude-plugin" / "bin" / "rig"
    if not src.exists():
        print(f"[ERROR] shim 元が見つかりません: {src}")
        sys.exit(1)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() or target.is_symlink():
        if not force:
            print(f"[ERROR] 既に存在: {target}（上書きは --force）")
            sys.exit(1)
        target.unlink()
    target.symlink_to(src)
    print(f"✓ symlink: {target} → {src}")
    path_dirs = (os.environ.get("PATH") or "").split(os.pathsep)
    if str(target.parent) not in path_dirs:
        print(f"⚠ {target.parent} が $PATH に無いようです。次を追加してください：")
        print(f"    export PATH=\"{target.parent}:$PATH\"")
    print(f"確認: `rig models` または `rig --help`（RIG_HOME={RIG_HOME}）")


def cmd_selftest(_args):
    # telemetry を一時ファイルへ退避（selftest が呼び出し元 cwd の .rig/runs.jsonl を汚さない）
    global RUNS_PATH
    _orig_runs = RUNS_PATH
    import tempfile
    RUNS_PATH = pathlib.Path(tempfile.gettempdir()) / "rig_runs_selftest.jsonl"
    RUNS_PATH.unlink(missing_ok=True)

    import io, contextlib

    s = lambda **k: {"id": k["id"], "instruction": "x", "gate": k.get("gate"),
                     "pattern": k.get("pattern"), "personas": k.get("personas", []),
                     "needs": k.get("needs", []),
                     "acceptance": [], "checks": k.get("checks", []),
                     "max_retries": k.get("max_retries", DEFAULT_K), "output_contract": None}

    # シナリオ A: 正常系（no-gate → checks pass → verdict pass → DONE）
    stepsA = [s(id="design"),
              s(id="verify", gate="acceptance-gate", checks=["true"]),
              s(id="review", gate="review-gate")]
    scriptA = [("next", None),                       # START design
               ("next", None),                       # design no-gate → ADVANCE verify
               ("next", None),                       # START verify
               ("check", True), ("next", None),      # verify checks ok → ADVANCE review
               ("next", None),                       # START review
               ("verdict", ("reviewer", True)), ("next", None)]  # review pass → DONE
    expectA = ["START", "ADVANCE", "START", "ADVANCE", "START", "DONE"]
    tA1, stA1 = _drive(stepsA, scriptA)
    tA2, stA2 = _drive(stepsA, scriptA)

    # シナリオ B: 失敗系（checks fail → retry → 再START → fail → ESCALATE）
    stepsB = [s(id="verify", gate="acceptance-gate", checks=["false"], max_retries=2)]
    scriptB = [("next", None),                       # START
               ("check", False), ("next", None),     # fail → RETRY（pending に戻る）
               ("next", None),                       # 再 START
               ("check", False), ("next", None)]      # fail（try2/2）→ ESCALATE
    expectB = ["START", "RETRY", "START", "ESCALATE"]
    tB, _ = _drive(stepsB, scriptB)

    # シナリオ C: 自己採点ブロック（by=self → BLOCKED）
    stepsC = [s(id="review", gate="review-gate")]
    scriptC = [("next", None), ("verdict", ("self", True)), ("next", None)]
    expectC = ["START", "BLOCKED"]
    tC, _ = _drive(stepsC, scriptC)

    # シナリオ D: 外部ランナー（mock プロバイダ・別プロセス実行＋独立検証）
    stepsD = [s(id="implement"),
              s(id="review", gate="review-gate")]
    stateD = new_state("selftest-run", stepsD, "デモ")
    finalD = run_loop(stateD, None, "mock", "mock", {}, max_steps=20, quiet=True)
    rev_verdicts = stateD["step_state"]["review"]["verdicts"]
    d_indep = bool(rev_verdicts) and rev_verdicts[0]["by"] == "mock:independent" and rev_verdicts[0]["ok"]
    d_exec = any(h["action"] == "EXEC" for h in stateD["history"])

    # シナリオ E: 並列検証ファンアウト（3 人の独立レビュアーを同時プロセス・全員 PASS）
    stepsE = [s(id="review", gate="review-gate", pattern="parallel-fanout",
                personas=["correctness", "repro", "security"])]
    stateE1 = new_state("par", stepsE, None)
    finalE1 = run_loop(stateE1, None, "mock", "mock", {}, 20, quiet=True, max_parallel=3)
    vE1 = sorted(v["by"] for v in stateE1["step_state"]["review"]["verdicts"])
    stateE2 = new_state("par", stepsE, None)
    run_loop(stateE2, None, "mock", "mock", {}, 20, quiet=True, max_parallel=3)
    vE2 = sorted(v["by"] for v in stateE2["step_state"]["review"]["verdicts"])
    expectE = ["mock:correctness", "mock:repro", "mock:security"]

    # シナリオ F: 1 人 FAIL。quorum=majority は可決、quorum=all はゲート不合格→ESCALATE。
    stepsF = [s(id="review", gate="review-gate", personas=["a", "b", "fail-c"], max_retries=2)]
    stateF = new_state("maj", stepsF, None)
    finalF = run_loop(stateF, None, "mock", "mock", {}, 20, quiet=True,
                      max_parallel=3, quorum="majority")  # 2/3 pass → DONE
    stateG = new_state("all", stepsF, None)
    finalG = run_loop(stateG, None, "mock", "mock", {}, 20, quiet=True,
                      max_parallel=3, quorum="all")        # 1 FAIL → retry → ESCALATE

    # シナリオ I: judge-panel（複数 generator を並列生成→judge が勝者を決定論選択）
    stepsI = [s(id="impl", gate="acceptance-gate")]
    stateI1 = new_state("panel", stepsI, None)
    finalI1 = run_loop(stateI1, None, "mock", "mock", {}, 20, quiet=True,
                       max_parallel=3, generators=["mock", "mock", "mock"])
    vI1 = stateI1["step_state"]["impl"]["verdicts"]
    i_panel = bool(vI1) and vI1[0]["by"] == "mock:judge-panel" and vI1[0]["ok"]
    stateI2 = new_state("panel", stepsI, None)
    run_loop(stateI2, None, "mock", "mock", {}, 20, quiet=True,
             max_parallel=3, generators=["mock", "mock", "mock"])
    i_det = json.dumps(stateI1["step_state"], sort_keys=True) == json.dumps(stateI2["step_state"], sort_keys=True)

    # シナリオ J: step-DAG 並列（a,b は独立→同 wave／c は needs:[a,b]→次 wave）
    stepsJ = [s(id="a"), s(id="b"),
              {"id": "c", "instruction": "x", "gate": None, "pattern": None, "personas": [],
               "needs": ["a", "b"], "acceptance": [], "checks": [], "max_retries": 2,
               "output_contract": None}]
    stateJ = new_state("dag", stepsJ, None)
    finalJ = run_loop(stateJ, None, "mock", "mock", {}, 20, quiet=True, max_parallel=2)
    j_waves = stateJ.get("waves")

    ok = True
    def report(name, got, exp, det=""):
        nonlocal ok
        good = (got == exp)
        ok = ok and good
        print(f"  [{'OK ' if good else 'NG '}] {name}: {got}{'' if good else f'  != {exp}'} {det}")

    print("## orchestrate selftest（決定論の証明）")
    report("A 正常系の遷移列", tA1, expectA)
    report("A 反復で同一(決定論)", tA2, tA1, "同入力→同遷移")
    report("A 最終状態が同一", json.dumps(stA1, sort_keys=True), json.dumps(stA2, sort_keys=True))
    report("A done=True", stA1["done"], True)
    report("B 失敗→リトライ→エスカレーション", tB, expectB)
    report("C 自己採点ブロック", tC, expectC)
    report("D 外部ランナーが DONE まで自走", finalD, "DONE")
    report("D 別プロセスで step を実行(EXEC)", d_exec, True)
    report("D 検証が独立(by=mock:independent)", d_indep, True)
    report("E 並列3人で DONE", finalE1, "DONE")
    report("E 3人分の独立票を記録", vE1, expectE)
    report("E 並列でも決定論(完了順に依らず)", vE2, vE1, "同集合")
    report("F majority は1人FAILでも可決→DONE", finalF, "DONE")
    report("G all は1人FAILで不合格→ESCALATE", finalG, "ESCALATE")
    # H: rig プロバイダは各 step を rig ハーネスとして起動（rig を名前で呼ぶ）
    argv_gen = build_argv("rig", "generator", "step X", {})
    argv_ver = build_argv("rig", "verifier", "step X", {})
    report("H rig provider は claude で起動", argv_gen[0], "claude")
    report("H rig を名前で呼ぶ(生成)", "`rig` skill" in argv_gen[2], True)
    report("H rig 検証は VERDICT 契約", "VERDICT" in argv_ver[2] and "`rig` skill" in argv_ver[2], True)
    report("I judge-panel が勝者を選び DONE", finalI1, "DONE")
    report("I 判定は judge-panel 由来", i_panel, True)
    report("I judge-panel は決定論", i_det, True)
    report("J DAG: 独立 a,b は同 wave→c は次 wave", j_waves, [["a", "b"], ["c"]])
    report("J DAG が DONE", finalJ, "DONE")
    # K: 自動有効化（checks/needs/manifest で --orchestrate auto ON）
    report("K checks 宣言で auto ON", auto_orchestrate([s(id="v", checks=["true"])])[0], True)
    report("K needs 宣言で auto ON", auto_orchestrate([s(id="a"), s(id="b", needs=["a"])])[0], True)
    report("K 宣言なしは off", auto_orchestrate([s(id="x")])[0], False)
    report("K manifest 既定で auto ON", auto_orchestrate([s(id="x")], manifest_default=True)[0], True)
    # L: ローカル LLM（ollama/lmstudio）が OpenAI 互換 HTTP プロバイダとして配線されている
    report("L ollama/lmstudio が配線済み", set(_OPENAI_BASE) == {"lmstudio", "ollama"}, True)
    rc_l, _ = run_provider("lmstudio", "verifier", "x", {"base_url": "http://127.0.0.1:1/v1", "timeout": 2})
    report("L サーバ不在でも crash せず rc!=0", rc_l != 0, True)
    # M: 動的モデル探索（--auto-model）— サーバ不在でも crash せず graceful
    found = discover_models({"base_url": "http://127.0.0.1:1/v1", "timeout": 2})
    report("M discover が全プロバイダを返す", set(found) >= {"ollama", "lmstudio", "claude", "codex", "rig"}, True)
    report("M 不在サーバは reachable=False", found["ollama"]["reachable"], False)
    report("M auto-model 解決は既定にフォールバック",
           resolve_http_model("ollama", {"auto_model": True, "base_url": "http://127.0.0.1:1/v1", "timeout": 2}),
           "llama3.1")
    report("M --model 明示は最優先", resolve_http_model("ollama", {"auto_model": True, "model": "qwen2.5"}), "qwen2.5")
    # N: probe の土台（codex の実コマンドが正しい・検証出力から VERDICT を拾える）
    report("N probe: codex verifier は read-only サンドボックスを強制",
           build_argv("codex", "verifier", "P", {}),
           ["codex", "exec", "--skip-git-repo-check", "--sandbox", "read-only", "P"])
    report("N probe: codex generator はサンドボックス強制なし",
           build_argv("codex", "generator", "P", {}), ["codex", "exec", "--skip-git-repo-check", "P"])
    report("N probe: claude verifier は allowedTools を強制",
           build_argv("claude", "verifier", "P", {}),
           ["claude", "-p", "P", "--output-format", "text", "--allowedTools", "Read,Grep,Glob"])
    report("N probe: claude generator は権限フラグなし",
           build_argv("claude", "generator", "P", {}), ["claude", "-p", "P", "--output-format", "text"])
    _, out_n = run_provider("mock", "verifier", "x", {})
    report("N probe: 検証出力に VERDICT", "VERDICT" in out_n, True)
    # O: タスクキュー（local backend で積む→list→mock go→note/retry/done-除外→github は CLI 不在で graceful）
    global QUEUE_PATH
    _orig_qp = QUEUE_PATH
    import tempfile
    QUEUE_PATH = pathlib.Path(tempfile.gettempdir()) / "rig_queue_selftest.json"
    QUEUE_PATH.unlink(missing_ok=True)
    queue_add("local", "タスクA", {}); queue_add("local", "タスクB", {})
    q_items = queue_list("local", {})
    for it in q_items:                          # mock で go（生成→検証→done）
        _, vout = run_provider("mock", "verifier", "x", {}, persona="queue")
        queue_set_status("local", it["id"], "done" if "VERDICT: PASS" in vout else "failed", "", {})
    q_done_raw = [it for it in _local_load()["items"] if it["status"] == "done"]  # 生ストアで確認
    q_done_in_list = [it for it in queue_list("local", {}) if it["status"] == "done"]  # #215: 出ない
    note_text = "❌ rig: 検証 FAIL（mock→mock）"
    target_id = q_items[0]["id"]
    queue_set_status("local", target_id, "failed", note_text, {})  # #214 用に明示的に failed+note
    q_note = next(it for it in queue_list("local", {}) if it["id"] == target_id)
    queue_set_status("local", target_id, "queued", "", {})         # #213: retry と同じ呼び出し
    q_retried = next(it for it in queue_list("local", {}) if it["id"] == target_id)
    relabel_failed = _queue_relabel_args("failed")
    relabel_removes = [relabel_failed[i + 1] for i in range(len(relabel_failed) - 1)
                        if relabel_failed[i] == "--remove-label"]
    gh_item = queue_add("github", "t", {})      # gh 不在 → error（crash しない）
    QUEUE_PATH.unlink(missing_ok=True)
    QUEUE_PATH = _orig_qp
    report("O queue: 2件積んで list", len(q_items), 2)
    report("O queue: mock go で全 done（生ストア確認）", len(q_done_raw), 2)
    report("O queue: done item は queue_list（local）に出ない（#215）", len(q_done_in_list), 0)
    report("O queue: failed item の note が list に反映（#214）", q_note.get("note"), note_text)
    report("O queue: retry で queued に戻る（#213）", q_retried["status"], "queued")
    report("O queue: retry で note がクリアされる（#213）", q_retried.get("note"), "")
    report("O queue: running→failed で rig-running が除去対象（#223）",
           "rig-running" in relabel_removes, True)
    report("O github backend は CLI 不在で graceful(error)", gh_item["status"], "error")
    # P: 実行テレメトリ（run_loop 経由の全シナリオが .rig/runs.jsonl に1行ずつ追記される）
    p_lines = []
    if RUNS_PATH.exists():
        p_lines = [json.loads(l) for l in RUNS_PATH.read_text(encoding="utf-8").splitlines() if l.strip()]
    p_first = p_lines[0] if p_lines else {}
    RUNS_PATH.unlink(missing_ok=True)
    RUNS_PATH = _orig_runs
    report("P telemetry: run_loop 8回分を記録", len(p_lines), 8)
    report("P telemetry: final と recipe を記録",
           (p_first.get("final"), p_first.get("recipe")), ("DONE", "selftest-run"))
    p_verdicts = sorted({v["by"] for r in p_lines for st in r.get("steps", [])
                         for v in st.get("verdicts", [])})
    report("P telemetry: 検証票（by）を記録", "mock:independent" in p_verdicts
           and "mock:correctness" in p_verdicts, True)
    # Q: RESOLVE 参照実装（extends マージ・remove・origin・badge 固定順・steps: フィールド）の golden 検証
    qdir = pathlib.Path(tempfile.mkdtemp(prefix="rig_resolve_selftest_"))
    (qdir / "base-flow.md").write_text(
        "---\nname: base-flow\ndescription: t\nscope: shipped\nautonomy: interactive\n"
        "steps:\n  - id: intake\n    instruction: intake\n"
        "  - id: design\n    instruction: design\n    condition: \"--design または size L+\"\n"
        "  - id: implement\n    instruction: implement\n"
        "  - id: verify\n    instruction: verify\n    gate: acceptance-gate\n"
        "    acceptance: [\"ok\"]\n---\n", encoding="utf-8")
    (qdir / "child-flow.md").write_text(
        "---\nname: child-flow\ndescription: t\nscope: project\nautonomy: autonomous\n"
        "extends: base-flow\ntdd: true\nverify_findings: true\n"
        "steps:\n  - id: design\n    remove: true\n"
        "  - id: verify\n    instruction: verify\n    gate: acceptance-gate\n    checks: [\"true\"]\n"
        "  - id: pr\n    instruction: pr\n---\n", encoding="utf-8")
    q_base = resolve_plan_json(qdir / "base-flow.md")
    q1 = resolve_plan_json(qdir / "child-flow.md")
    q2 = resolve_plan_json(qdir / "child-flow.md")
    report("Q resolve: 親の steps: フィールド（condition 略記）",
           q_base["steps_field"], "intake, design?[--design|L+], implement, verify")
    report("Q resolve: extends 確定 step 列（remove/override/added）",
           q1["steps_field"], "intake, implement, verify, pr")
    report("Q resolve: origin 判定",
           [s["origin"] for s in q1["steps"]], ["inherited", "inherited", "override", "added"])
    report("Q resolve: badge 固定順（tdd→gated→orchestrate(auto)→autonomous→verify-findings）",
           q1["badges"], ["tdd", "gated", "orchestrate(auto)", "autonomous", "verify-findings"])
    report("Q resolve: 決定論（同入力→同 JSON）",
           json.dumps(q1, sort_keys=True), json.dumps(q2, sort_keys=True))
    # R: RESOLVE フェーズ2（condition 評価・size 判定・スライス・flag 優先順位）の golden 検証
    rf = RECIPES / "release-flow.md"
    r_s = resolve_effective(rf, [], diff_lines=50)                       # size S: design/review OFF
    r_flag = resolve_effective(rf, ["--design"], diff_lines=50)          # flag が condition を解決
    r_l = resolve_effective(rf, [], diff_lines=300)                      # size L: design/review ON
    r_only_off = resolve_effective(rf, ["--only", "review"], diff_lines=50)   # ケース B: condition-OFF
    r_only_on = resolve_effective(rf, ["--only", "review", "--review"], diff_lines=50)
    r_range = resolve_effective(rf, ["--from", "implement", "--to", "verify"], diff_lines=50)
    r_rev = resolve_effective(rf, ["--from", "verify", "--to", "implement"], diff_lines=50)
    r_skipwin = resolve_effective(rf, ["--design", "--skip", "design"], diff_lines=50)
    r_onlyskip = resolve_effective(rf, ["--only", "verify", "--skip", "design"], diff_lines=50)
    r_gate = resolve_effective(rf, ["--skip", "verify"], diff_lines=50)  # acceptance-gate skip WARN
    r_typo = resolve_effective(rf, ["--only", "verifi"], diff_lines=50)  # ケース A: Levenshtein 候補
    r_det = (json.dumps(resolve_effective(rf, ["--design"], diff_lines=50), sort_keys=True)
             == json.dumps(resolve_effective(rf, ["--design"], diff_lines=50), sort_keys=True))
    report("R size S: design/review は condition-OFF",
           r_s["effective_steps"], ["intake", "implement", "verify", "pr", "merge"])
    report("R --design flag が condition を解決",
           r_flag["effective_steps"], ["intake", "design", "implement", "verify", "pr", "merge"])
    report("R size L+: design/review が自動 ON",
           r_l["effective_steps"], ["intake", "design", "implement", "verify", "review", "pr", "merge"])
    report("R --only condition-OFF step はエラー（ケース B）",
           any("condition" in e for e in r_only_off["errors"]), True)
    report("R --only + 有効化フラグで単独実行", r_only_on["effective_steps"], ["review"])
    report("R --from/--to 範囲スライス", r_range["effective_steps"], ["implement", "verify"])
    report("R --from/--to 順序逆はエラー", any("順序が逆" in e for e in r_rev["errors"]), True)
    report("R 明示 --skip が明示 ON に勝つ", "design" not in r_skipwin["effective_steps"], True)
    report("R --only は --skip を無視して WARN",
           any("--only 優先・--skip 無視" in w for w in r_onlyskip["warnings"]), True)
    report("R acceptance-gate step の --skip は WARN",
           any("acceptance-gate" in w for w in r_gate["warnings"]), True)
    report("R タイポは Levenshtein 候補つきエラー（ケース A）",
           any("もしかして: verify" in e for e in r_typo["errors"]), True)
    report("R resolve_effective は決定論", r_det, True)
    # S: フェーズ3（manifest 閾値の反映・git diff 自動測定の graceful）
    s_manifest = {"size_thresholds": {"S_max": 10, "M_max": 20, "L_max": 40}}
    r_s_th = resolve_effective(rf, [], diff_lines=30, manifest=s_manifest)  # 30行 > M_max:20 → L
    r_s_orch = resolve_effective(rf, [], diff_lines=5, manifest={"default_orchestrate": True})
    report("S manifest size_thresholds が size 判定に効く（30行→L で design ON）",
           "design" in r_s_th["effective_steps"], True)
    report("S manifest default_orchestrate で orchestrate auto",
           r_s_orch["mode"]["orchestrate"].startswith("auto"), True)
    report("S git_diff_lines は crash せず int|None", isinstance(git_diff_lines(), (int, type(None))), True)
    report("S load_manifest は常に dict", isinstance(load_manifest(), dict), True)
    # Y: auto-route（コストティア自動ルーティング・#264）は決定論的に候補を選ぶ
    y_step = {"auto_route": {"candidates": [
        {"model": "haiku", "cost_tier": "low", "max_size": "S"},
        {"model": "sonnet", "cost_tier": "medium", "max_size": "L"},
        {"model": "opus", "cost_tier": "high", "max_size": "XL"},
    ]}}
    y_s, _ = resolve_auto_route(y_step, "S")
    y_m, _ = resolve_auto_route(y_step, "M")
    y_l, _ = resolve_auto_route(y_step, "L")
    y_xl, _ = resolve_auto_route(y_step, "XL")
    y_s2, _ = resolve_auto_route(y_step, "S")   # 同入力を2回解決 → 同結果（決定論）
    report("Y auto-route: S size → 最安候補(haiku)", y_s, "haiku")
    report("Y auto-route: M size → S超過につき次点(sonnet)", y_m, "sonnet")
    report("Y auto-route: L size → sonnet(max_size=L)のまま", y_l, "sonnet")
    report("Y auto-route: XL size → 最終候補(opus)にフォールバック", y_xl, "opus")
    report("Y auto-route: 同入力→同選択（決定論）", y_s == y_s2, True)
    report("Y auto-route: auto_route 未宣言時は None",
           resolve_auto_route({}, "S")[0], None)
    # V: パーティ編成画面（party）＝ runs/drill から RPG シートを描画
    global DRILL_PATH
    _orig_drill = DRILL_PATH
    RUNS_PATH = pathlib.Path(tempfile.gettempdir()) / "rig_party_runs.jsonl"
    DRILL_PATH = pathlib.Path(tempfile.gettempdir()) / "rig_party_drill.jsonl"
    RUNS_PATH.write_text(json.dumps({
        "ts": "t", "recipe": "review-only", "backend": "orchestrate", "final": "DONE",
        "steps_total": 1, "steps_passed": 1, "retries": 0, "escalated_at": None,
        "steps": [{"id": "review", "status": "passed", "retries": 0,
                   "verdicts": [{"by": "mock:security-reviewer", "ok": False}]}]}) + "\n",
        encoding="utf-8")
    DRILL_PATH.write_text(json.dumps({
        "ts": "t", "scores": [{"reviewer": "security-reviewer", "detected": 2,
                               "seeded": 2, "false_positives": 0}]}) + "\n", encoding="utf-8")
    buf_v = io.StringIO()
    with contextlib.redirect_stdout(buf_v):
        cmd_party([])
    v_out = buf_v.getvalue()
    RUNS_PATH.unlink(missing_ok=True); DRILL_PATH.unlink(missing_ok=True)
    RUNS_PATH = _orig_runs; DRILL_PATH = _orig_drill
    report("V party: Lv と実績を描画", "Lv.1" in v_out and "🏆 初DONE" in v_out, True)
    report("V party: drill 検出率と出撃/REJECT を反映",
           "検出率 100%（drill 2/2）" in v_out and "出撃   1 / REJECT 1" in v_out, True)
    # U: モデル混成クォーラム（同一 persona を複数プロバイダで並走・票は provider:persona）
    stepsU = [s(id="review", gate="review-gate", personas=["x"])]
    stateU1 = new_state("mq", stepsU, None)
    finalU1 = run_loop(stateU1, None, "mock", ["mock", "mock"], {}, 20, quiet=True, max_parallel=2)
    vU1 = stateU1["step_state"]["review"]["verdicts"]
    stateU2 = new_state("mq", stepsU, None)
    run_loop(stateU2, None, "mock", ["mock", "mock"], {}, 20, quiet=True, max_parallel=2)
    u_det = (json.dumps(stateU1["step_state"], sort_keys=True)
             == json.dumps(stateU2["step_state"], sort_keys=True))
    report("U model-quorum: 2プロバイダ×1persona で2票", len(vU1), 2)
    report("U model-quorum: 票の by は provider:persona", all(v["by"] == "mock:x" for v in vU1), True)
    report("U model-quorum: DONE + 決定論", (finalU1, u_det), ("DONE", True))
    # T: ギャップ処方箋（同一 step のエスカレーション反復 → --discover 提案）
    RUNS_PATH = pathlib.Path(tempfile.gettempdir()) / "rig_runs_gap_selftest.jsonl"
    RUNS_PATH.unlink(missing_ok=True)
    with RUNS_PATH.open("w", encoding="utf-8") as f:
        for esc in ("verify", "verify", None):
            f.write(json.dumps({"ts": "t", "recipe": "release-flow", "backend": "orchestrate",
                                "final": "ESCALATE" if esc else "DONE", "steps_total": 1,
                                "steps_passed": 0 if esc else 1, "retries": 2 if esc else 0,
                                "escalated_at": esc, "steps": []}) + "\n")
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        cmd_runs([])
    t_out = buf.getvalue()
    RUNS_PATH.unlink(missing_ok=True)
    RUNS_PATH = _orig_runs
    report("T gap: エスカレーション2回で処方箋を提示", "ギャップ処方箋" in t_out and "--discover" in t_out, True)
    report("T gap: 対象 step を特定", "release-flow / verify: エスカレーション 2 回" in t_out, True)
    for f in qdir.iterdir():
        f.unlink()
    qdir.rmdir()
    # ── シナリオ X: worktree 隔離（setup/teardown の決定論的な後始末規則）────
    import tempfile as _tmp
    global INVOCATION_CWD
    _orig_cwd = INVOCATION_CWD
    xroot = pathlib.Path(_tmp.mkdtemp(prefix="rig-selftest-iso-"))
    def _g(*a, cwd=None):
        return subprocess.run(["git", "-C", str(cwd or xroot)] + list(a),
                              capture_output=True, text=True)
    _g("init", "-q", "-b", "main")
    _g("config", "user.email", "selftest@rig")
    _g("config", "user.name", "rig-selftest")
    (xroot / "base.txt").write_text("base\n")
    _g("add", "."); _g("commit", "-q", "-m", "base")
    INVOCATION_CWD = xroot
    # X-1: DONE + commit あり + clean → ff 合流して撤収
    iso1 = setup_isolation("demo")
    report("X isolate: worktree と branch が作られる",
           pathlib.Path(iso1["dir"]).is_dir() and iso1["branch"].startswith("rig/run-demo-"), True)
    (pathlib.Path(iso1["dir"]) / "made.txt").write_text("x\n")
    _g("add", ".", cwd=iso1["dir"]); _g("commit", "-q", "-m", "work", cwd=iso1["dir"])
    report("X isolate: DONE+commit+clean は ff 合流（merged）", teardown_isolation(iso1, "DONE"), "merged")
    report("X isolate: 合流後 root に成果が居る", (xroot / "made.txt").exists(), True)
    report("X isolate: 撤収後 worktree は消える", pathlib.Path(iso1["dir"]).exists(), False)
    # X-2: DONE でも dirty → 保全
    iso2 = setup_isolation("demo")
    (pathlib.Path(iso2["dir"]) / "wip.txt").write_text("wip\n")
    report("X isolate: dirty は保全（kept）", teardown_isolation(iso2, "DONE"), "kept")
    report("X isolate: 保全時 worktree が残る", pathlib.Path(iso2["dir"]).is_dir(), True)
    # X-3: 変更なしの DONE → 撤収のみ
    iso3 = setup_isolation("demo")
    report("X isolate: 変更なしは撤収のみ（clean-removed）", teardown_isolation(iso3, "DONE"), "clean-removed")
    # X-4: ESCALATE → 保全
    iso4 = setup_isolation("demo")
    report("X isolate: 未達（ESCALATE）は保全（kept）", teardown_isolation(iso4, "ESCALATE"), "kept")
    INVOCATION_CWD = _orig_cwd

    # ── シナリオ W: ブリック・グラフ（型付き関係の導出・golden アンカー）────
    gW1 = build_brick_graph()
    gW2 = build_brick_graph()
    report("W graph: 導出は決定論（同入力→同グラフ）", gW1 == gW2, True)
    eW = {(e["from"], e["rel"], e["to"]) for e in gW1["edges"]}
    report("W graph: persona inject が injects エッジになる",
           ("persona:security-reviewer", "injects", "wiki:appsec-checklist") in eW, True)
    report("W graph: recipe steps が gated-by/uses-persona エッジになる",
           ("recipe:review-only", "gated-by", "pattern:review-gate") in eW
           and ("recipe:review-only", "uses-persona", "persona:security-reviewer") in eW, True)
    report("W graph: wiki 相互リンクが links-to エッジになる",
           ("wiki:appsec-checklist", "links-to", "wiki:injection-patterns") in eW, True)
    report("W graph: agent は -reviewer 差を吸収して persona を mirrors",
           ("agent:lazy-senior-reviewer", "mirrors", "persona:lazy-senior") in eW, True)
    report("W graph: shipped tier に未解決エッジ 0",
           sum(1 for e in gW1["edges"] if not e["resolved"]), 0)

    print("\n" + ("PASS: 決定論オーケストレータは健全" if ok else "FAIL: セルフテスト不一致"))
    sys.exit(0 if ok else 1)



# ── ブリック・グラフ（型付き関係の導出＝オントロジー層・#graph）───────────────
# 「概念間の関係を型として明示する」オントロジーを、rig は**手で書かずコードで導出**する。
# source of truth は各ブリックの frontmatter / steps: 定義そのもの＝グラフは腐らない。
# オントロジー5要素との対応: クラス=kind / インスタンス=node / プロパティ=path /
# 関係=typed edge（11種）/ 制約=validate.py check_graph（CI）。

_WIKI_LINK_RE = re.compile(r"\[\[([a-z0-9-]+)(?:\|[^\]]*)?\]\]")


def _graph_body_links(path: pathlib.Path) -> list[str]:
    """本文＋frontmatter links: から [[slug]] 参照を重複なしで抽出する。"""
    text = path.read_text(encoding="utf-8")
    seen: list[str] = []
    for slug in _WIKI_LINK_RE.findall(text):
        if slug not in seen:
            seen.append(slug)
    return seen


def build_brick_graph() -> dict:
    """shipped ブリック群の既存メタデータから型付きグラフを導出する（純関数・決定論）。

    nodes: {id, kind, path} / edges: {from, rel, to, resolved}
    rel 語彙（固定11種）: extends / injects / links-to / uses-instruction / uses-pattern /
    gated-by / applies-policy / emits-contract / uses-persona / references / mirrors
    """
    skills = RIG_HOME / "skills" / "rig"
    facets = skills / "facets"
    dirs = {
        "persona": facets / "personas",
        "instruction": facets / "instructions",
        "pattern": skills / "patterns",
        "policy": facets / "policies",
        "contract": facets / "output-contracts",
        "wiki": facets / "knowledge" / "wiki",
        "recipe": skills / "recipes",
        "agent": RIG_HOME / "agents",
        "command": RIG_HOME / "commands",
    }
    nodes: dict[str, dict] = {}
    edges: list[dict] = []

    def add_edge(src: str, rel: str, dst: str) -> None:
        e = {"from": src, "rel": rel, "to": dst}
        if e not in edges:
            edges.append(e)

    for kind, d in dirs.items():
        if not d.is_dir():
            continue
        for f in sorted(d.rglob("*.md")):
            stem = str(f.relative_to(d).with_suffix(""))
            if stem.startswith("_") or "/" in stem and stem.split("/")[-1].startswith("_"):
                continue
            nodes[f"{kind}:{stem}"] = {"id": f"{kind}:{stem}", "kind": kind,
                                       "path": str(f.relative_to(RIG_HOME))}

    # basename → persona id（recipe の personas: は basename 指定を許容）
    persona_base: dict[str, list[str]] = {}
    for nid in nodes:
        if nid.startswith("persona:"):
            persona_base.setdefault(nid.split("/")[-1].split(":")[-1], []).append(nid)

    def persona_id(name: str) -> str:
        if f"persona:{name}" in nodes:
            return f"persona:{name}"
        hits = persona_base.get(name, [])
        return hits[0] if len(hits) == 1 else f"persona:{name}"

    # persona → wiki（injects）
    for nid, n in list(nodes.items()):
        if n["kind"] != "persona":
            continue
        fm = parse_frontmatter(RIG_HOME / n["path"])
        for entry in (fm.get("inject") or []):
            m = _WIKI_LINK_RE.fullmatch(str(entry))
            if m:
                add_edge(nid, "injects", f"wiki:{m.group(1)}")

    # wiki → wiki（links-to・frontmatter links: ＋ 本文 [[slug]]）
    for nid, n in list(nodes.items()):
        if n["kind"] != "wiki":
            continue
        for slug in _graph_body_links(RIG_HOME / n["path"]):
            if f"wiki:{slug}" != nid:
                add_edge(nid, "links-to", f"wiki:{slug}")

    # recipe → 各ブリック（steps: の生定義＝著者が書いた関係）
    for nid, n in list(nodes.items()):
        if n["kind"] != "recipe":
            continue
        fm = parse_frontmatter(RIG_HOME / n["path"])
        if fm.get("extends"):
            add_edge(nid, "extends", f"recipe:{fm['extends']}")
        for s in (fm.get("steps") or []):
            if not isinstance(s, dict):
                continue
            if s.get("instruction"):
                add_edge(nid, "uses-instruction", f"instruction:{s['instruction']}")
            if s.get("pattern"):
                add_edge(nid, "uses-pattern", f"pattern:{s['pattern']}")
            if s.get("gate") not in (None, "—", "-"):
                add_edge(nid, "gated-by", f"pattern:{s['gate']}")
            for p_ in (s.get("personas") or []):
                add_edge(nid, "uses-persona", persona_id(str(p_)))
            for pol in (s.get("policies") or []):
                add_edge(nid, "applies-policy", f"policy:{pol}")
            if s.get("output_contract"):
                add_edge(nid, "emits-contract", f"contract:{s['output_contract']}")

    # agent → persona（mirrors・native-first の対）
    for nid, n in list(nodes.items()):
        if n["kind"] != "agent":
            continue
        stem = nid.split(":", 1)[1]
        cand = [stem]
        if stem.endswith("-reviewer"):
            cand.append(stem[: -len("-reviewer")])
        dst = next((persona_id(c) for c in cand
                    if persona_id(c) in nodes), persona_id(cand[-1]))
        add_edge(nid, "mirrors", dst)

    # command → instruction（本文の `facets/instructions/<name>` 明示参照のみ＝散文推測しない）
    ref_re = re.compile(r"facets/instructions/([a-z0-9-]+)")
    for nid, n in list(nodes.items()):
        if n["kind"] != "command":
            continue
        text = (RIG_HOME / n["path"]).read_text(encoding="utf-8")
        for name in sorted(set(ref_re.findall(text))):
            add_edge(nid, "references", f"instruction:{name}")

    for e in edges:
        e["resolved"] = e["to"] in nodes
    return {
        "nodes": sorted(nodes.values(), key=lambda x: (x["kind"], x["id"])),
        "edges": sorted(edges, key=lambda x: (x["from"], x["rel"], x["to"])),
    }


def cmd_graph(args):
    """graph [--json] [--focus <name>]: 型付きブリック・グラフを導出して表示する。"""
    g = build_brick_graph()
    if "--json" in args:
        print(json.dumps(g, ensure_ascii=False, indent=2))
        return
    if "--focus" in args:
        name = args[args.index("--focus") + 1]
        ids = {n["id"] for n in g["nodes"] if n["id"] == name or n["id"].split(":", 1)[-1] == name
               or n["id"].split(":", 1)[-1].split("/")[-1] == name}
        if not ids:
            print(f"[graph] focus に一致するノードがありません: {name}")
            sys.exit(1)
        for nid in sorted(ids):
            print(f"◈ {nid}")
            for e in g["edges"]:
                if e["from"] == nid:
                    print(f"  → {e['rel']} → {e['to']}" + ("" if e["resolved"] else "  (未解決)"))
            for e in g["edges"]:
                if e["to"] == nid:
                    print(f"  ← {e['rel']} ← {e['from']}")
        return
    kinds: dict[str, int] = {}
    for n in g["nodes"]:
        kinds[n["kind"]] = kinds.get(n["kind"], 0) + 1
    rels: dict[str, int] = {}
    unresolved = [e for e in g["edges"] if not e["resolved"]]
    for e in g["edges"]:
        rels[e["rel"]] = rels.get(e["rel"], 0) + 1
    print("ブリック・グラフ（型付き・frontmatter/steps から導出＝手書きしない）")
    print(f"  nodes: {len(g['nodes'])}  (" + " / ".join(f"{k} {v}" for k, v in sorted(kinds.items())) + ")")
    print(f"  edges: {len(g['edges'])}  (" + " / ".join(f"{k} {v}" for k, v in sorted(rels.items())) + ")")
    print(f"  未解決エッジ: {len(unresolved)}")
    for e in unresolved:
        print(f"    ✗ {e['from']} → {e['rel']} → {e['to']}")
    print("  1ホップ探索: graph --focus <name> ／ 機械可読: graph --json")


# ── エントリ ──────────────────────────────────────────────────────────────────
COMMANDS = {
    "plan": cmd_plan, "init": cmd_init, "check": cmd_check,
    "verdict": cmd_verdict, "next": cmd_next, "status": cmd_status,
    "run": cmd_run, "models": cmd_models, "probe": cmd_probe, "queue": cmd_queue,
    "runs": cmd_runs, "party": cmd_party, "graph": cmd_graph,
    "install-shim": cmd_install_shim, "selftest": cmd_selftest, "ab": cmd_ab,
}


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        print(__doc__)
        sys.exit(0 if len(sys.argv) < 2 else 1)
    COMMANDS[sys.argv[1]](sys.argv[2:])


if __name__ == "__main__":
    main()
