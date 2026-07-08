#!/usr/bin/env python3
"""
rig 構造バリデータ（CI 用）

shipped tier の recipe frontmatter・step 参照・extends チェーン・persona frontmatter を機械的に検査する。
--validate instruction（facets/instructions/validate.md）の①②③（＋③-b persona スキーマ）サブセットを実装。
Claude 不要・ファイルシステムのみで完結。

終了コード: 0=合格 / 1=FAIL あり
"""

import sys
import os
import re
import json
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
def _check_exists(path: pathlib.Path, ctx: str, field: str, hint_dir: pathlib.Path | None = None) -> bool:
    if path.exists():
        return True
    rel = path.relative_to(ROOT) if ROOT in path.parents or path.is_relative_to(ROOT) else path
    msg = f"{ctx} — {field}: {rel} が存在しません"
    if hint_dir is not None and hint_dir.is_dir():
        available = sorted(p.stem for p in hint_dir.glob("*.md"))
        if available:
            msg += f"（期待パス: {rel}／利用可能: {', '.join(available)}）"
    _emit("FAIL", msg)
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


_VALID_GATES = ("review-gate", "acceptance-gate", "magi-consensus")


def _check_gate(val: str | None, ctx: str, field: str) -> None:
    """gate は review-gate|acceptance-gate の2値のみ許容する（#198 の列挙値 FAIL・#227）。

    `pattern` フィールドは patterns/ 配下のブリック名すべてを許容するため
    `_check_pattern_or_gate`（実在チェック）を流用してよいが、`gate` は
    その2値限定であり別の判定基準が要る（patterns/serial.md 等の実在に釣られて
    誤って PASS させない）。
    """
    if not val or val in ("—", "-"):
        return
    if val not in _VALID_GATES:
        _emit(
            "FAIL",
            f"{ctx} — {field}: 値 '{val}' は不正な列挙値です。"
            f" 許容値: {', '.join(_VALID_GATES)}",
        )


_SIZE_TOKEN_RE = re.compile(r"\b(?:S|M|L|XL)\+")


def _check_condition(val: str | None, ctx: str, field: str) -> None:
    """condition は自由文中に size トークン（S+/M+/L+/XL+）を含むことを期待する（#109/#229/#230）。

    正準形式を `size:` プレフィックス必須ではなく「size トークンの存在」で判定する
    （release-flow.md の実運用値 `"--design または size L+"` を偽 WARN しないため）。
    """
    if val is None:
        return
    if not _SIZE_TOKEN_RE.search(str(val)):
        _emit(
            "WARN",
            f"{ctx} — {field}: '{val}' に有効な size トークン（S+/M+/L+/XL+）が見つかりません"
            f"（size-aware の RESOLVE 判定が意図通り働かない可能性）",
        )


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

    # name ↔ ファイル名（#216：validate.md が定義する FAIL に重大度を合わせる）
    if fm["name"] != path.stem:
        _emit("FAIL", f"{ctx} — name '{fm['name']}' がファイル名 '{path.stem}' と不一致")

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

    # no_default_personas 値域（#70）
    ndp_val = fm.get("no_default_personas")
    if ndp_val is not None and not isinstance(ndp_val, bool):
        _emit("FAIL", f"{ctx} — no_default_personas '{ndp_val!r}' は boolean (true/false) でなければなりません")

    # orchestrate 値域（#129/#151）
    orch_val = fm.get("orchestrate")
    if orch_val is not None and not isinstance(orch_val, bool):
        _emit("FAIL", f"{ctx} — orchestrate '{orch_val!r}' は boolean (true/false) でなければなりません")

    # cross_llm 値域（#130/#151）
    cross_llm_val = fm.get("cross_llm")
    if cross_llm_val is not None and not isinstance(cross_llm_val, bool):
        _emit("FAIL", f"{ctx} — cross_llm '{cross_llm_val!r}' は boolean (true/false) でなければなりません")

    # no_capture 値域（#137/#151）
    no_capture_val = fm.get("no_capture")
    if no_capture_val is not None and not isinstance(no_capture_val, bool):
        _emit("FAIL", f"{ctx} — no_capture '{no_capture_val!r}' は boolean (true/false) でなければなりません")

    # verify_findings 値域（review-gate 敵対的検証・§3.5）
    vf_val = fm.get("verify_findings")
    if vf_val is not None and not isinstance(vf_val, bool):
        _emit("FAIL", f"{ctx} — verify_findings '{vf_val!r}' は boolean (true/false) でなければなりません")

    # adversarial 値域（#172/#228）
    adversarial_val = fm.get("adversarial")
    if adversarial_val is not None and not isinstance(adversarial_val, bool):
        _emit("FAIL", f"{ctx} — adversarial '{adversarial_val!r}' は boolean (true/false) でなければなりません")

    # visual 値域（#174/#228）
    visual_val = fm.get("visual")
    if visual_val is not None and not isinstance(visual_val, bool):
        _emit("FAIL", f"{ctx} — visual '{visual_val!r}' は boolean (true/false) でなければなりません")

    # no_orchestrate 値域（#178/#228）
    no_orch_val = fm.get("no_orchestrate")
    if no_orch_val is not None and not isinstance(no_orch_val, bool):
        _emit("FAIL", f"{ctx} — no_orchestrate '{no_orch_val!r}' は boolean (true/false) でなければなりません")

    # design 値域（#182/#228）
    design_val = fm.get("design")
    if design_val is not None and not isinstance(design_val, bool):
        _emit("FAIL", f"{ctx} — design '{design_val!r}' は boolean (true/false) でなければなりません")

    # review 値域（#182/#228）
    review_val = fm.get("review")
    if review_val is not None and not isinstance(review_val, bool):
        _emit("FAIL", f"{ctx} — review '{review_val!r}' は boolean (true/false) でなければなりません")

    # capture 値域（#184/#228）
    capture_val = fm.get("capture")
    if capture_val is not None and not isinstance(capture_val, bool):
        _emit("FAIL", f"{ctx} — capture '{capture_val!r}' は boolean (true/false) でなければなりません")

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

        # id 必須・slug 形式（#197/#219）・一意性
        if not step.get("id"):
            _emit("FAIL", f"{ctx} — steps[{i}] に id がありません")
        else:
            if not re.fullmatch(r"[a-z][a-z0-9-]*", step_id):
                _emit(
                    "FAIL",
                    f"{step_ctx} — id '{step_id}' は不正な形式です。"
                    f" id は [a-z][a-z0-9-]*（小文字英数字・ハイフンのみ、小文字始まり）で指定してください",
                )
            if step_id in seen_ids:
                _emit("FAIL", f"{ctx} — steps[].id '{step_id}' が重複しています")
            seen_ids.add(step_id)

        # instruction 必須
        instr = step.get("instruction")
        if not instr:
            _emit("FAIL", f"{step_ctx} — instruction がありません")
        else:
            _check_exists(FACETS / "instructions" / f"{instr}.md", step_ctx, "instruction",
                          hint_dir=FACETS / "instructions")

        # personas[]
        for persona in (step.get("personas") or []):
            _resolve_persona(persona, step_ctx)

        # policies[]
        for policy in (step.get("policies") or []):
            _check_exists(FACETS / "policies" / f"{policy}.md", step_ctx, f"policies[{policy}]",
                          hint_dir=FACETS / "policies")

        # output_contract
        oc = step.get("output_contract")
        if oc:
            _check_exists(FACETS / "output-contracts" / f"{oc}.md", step_ctx, "output_contract",
                          hint_dir=FACETS / "output-contracts")

        # pattern → patterns/ 実在チェック（shipped tier のブリック名すべて許容）
        _check_pattern_or_gate(step.get("pattern"), step_ctx, "pattern")
        # gate → review-gate|acceptance-gate の2値のみ許容（#198 の列挙値 FAIL・#227）
        _check_gate(step.get("gate"), step_ctx, "gate")

        # checks: 型・空エントリ検証（#200 の CI 反映・#218）
        checks_val = step.get("checks")
        if checks_val is not None:
            if not isinstance(checks_val, list):
                _emit(
                    "FAIL",
                    f"{step_ctx} — checks の値がリストではありません（{checks_val!r}）。"
                    f" checks はシェルコマンドの配列で指定してください（例: [\"npm test\"]）",
                )
            else:
                for idx, cmd in enumerate(checks_val):
                    if cmd == "":
                        _emit(
                            "FAIL",
                            f"{step_ctx} — checks に空文字列エントリが含まれています（インデックス {idx}）",
                        )

        # condition 値検証（#109/#229/#230）
        _check_condition(step.get("condition"), step_ctx, "condition")

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

    # needs: 参照切れチェック（チェック A・#152）
    for step in steps:
        if not isinstance(step, dict):
            continue
        step_id = step.get("id") or "?"
        needs_list = step.get("needs")
        if not needs_list:
            continue
        for needed_id in needs_list:
            if not isinstance(needed_id, str):
                continue
            if needed_id not in seen_ids:
                _emit(
                    "FAIL",
                    f"{ctx}.{step_id} — needs に未定義の step-id {needed_id!r} が含まれます。"
                    f" 有効な step-id: {', '.join(sorted(seen_ids))}",
                )

    _emit("PASS", f"{ctx}: OK")


# ── persona facet スキーマチェック ────────────────────────────────────────────
def check_personas() -> None:
    """shipped persona facet の frontmatter スキーマを検査する。

    - frontmatter が存在し YAML として読めること（FAIL）
    - `name` が personas/ からの相対パス（拡張子なし・`/` 区切り）と一致すること（FAIL。
      recipe `personas[]` / `--persona <name>` の名前解決と整合しなくなるため）
    - `description` が非空文字列であること（FAIL。catalog / --list の表示に使う）
    - `inject` がある場合はリスト型であること（FAIL。wiki 参照 §5 の宣言形式）
    """
    personas_dir = FACETS / "personas"
    persona_files = sorted(personas_dir.rglob("*.md"))
    if not persona_files:
        _emit("WARN", "facets/personas/ に .md ファイルが見つかりません")
        return

    ok = 0
    for path in persona_files:
        rel_name = str(path.relative_to(personas_dir))[:-3].replace("\\", "/")
        ctx = f"persona {rel_name}"
        fm, raw = parse_frontmatter(path)

        if fm is None:
            if path.read_text(encoding="utf-8").startswith("---"):
                _emit("FAIL", f"{ctx} — frontmatter が読めません（YAML エラー: {raw[:80]}）")
            else:
                _emit("FAIL", f"{ctx} — frontmatter がありません（name/description が必須）")
            continue

        bad = False
        if fm.get("name") != rel_name:
            _emit("FAIL", f"{ctx} — name '{fm.get('name')}' が相対パス '{rel_name}' と不一致")
            bad = True
        desc = fm.get("description")
        if not isinstance(desc, str) or not desc.strip():
            _emit("FAIL", f"{ctx} — description が空または未定義です")
            bad = True
        inject = fm.get("inject")
        if inject is not None and not isinstance(inject, list):
            _emit("FAIL", f"{ctx} — inject はリスト型でなければなりません（値: {inject!r}）")
            bad = True
        elif isinstance(inject, list):
            # shipped persona の inject は shipped wiki tier に解決できなければならない
            # （user/project tier は新規インストール環境に存在しないため FAIL）
            wiki_dir = FACETS / "knowledge" / "wiki"
            for entry in inject:
                m = re.match(r"^\[\[([^\]|]+)(?:\|[^\]]*)?\]\]$", str(entry).strip())
                if not m:
                    _emit("FAIL", f"{ctx} — inject エントリ {entry!r} が [[slug]] 形式ではありません")
                    bad = True
                    continue
                slug = m.group(1)
                if not (wiki_dir / f"{slug}.md").exists():
                    _emit("FAIL", f"{ctx} — inject [[{slug}]] が shipped wiki"
                                  f"（skills/rig/facets/knowledge/wiki/{slug}.md）に解決できません")
                    bad = True
        if not bad:
            ok += 1

    _emit("PASS", f"personas: {ok}/{len(persona_files)} 件スキーマ OK")


# ── commands / agents frontmatter チェック ──────────────────────────────────
# v0.77（frontmatter YAML 不正で全コマンド未登録）・v0.78（予約名 skill 衝突）の
# 実バグ class を CI で再発防止する。
_RESERVED_COMMAND_NAMES = {"skill", "status"}  # 経験的に衝突（skill）/衝突回避で改名済み（status→party）


def check_commands() -> None:
    cmd_dir = ROOT / "commands"
    if not cmd_dir.is_dir():
        return
    ok = 0
    files = sorted(cmd_dir.glob("*.md"))
    for path in files:
        ctx = f"command {path.stem}"
        fm, raw = parse_frontmatter(path)
        bad = False
        if fm is None:
            _emit("FAIL", f"{ctx} — frontmatter が YAML として読めません（全コマンド未登録バグの再発 class）: {raw[:80]}")
            continue
        desc = fm.get("description")
        if not isinstance(desc, str) or not desc.strip():
            _emit("FAIL", f"{ctx} — description が空または文字列ではありません")
            bad = True
        ah = fm.get("argument-hint")
        if ah is not None and not isinstance(ah, str):
            _emit("FAIL", f"{ctx} — argument-hint '{ah!r}' は文字列でなければなりません（配列で書くと YAML 崩れの温床）")
            bad = True
        if path.stem in _RESERVED_COMMAND_NAMES:
            _emit("WARN", f"{ctx} — '{path.stem}' は CC 組み込みと衝突した実績のある名前です（skill→forge / status→party の前例）")
        if not bad:
            ok += 1
    _emit("PASS", f"commands: {ok}/{len(files)} 件 frontmatter OK")


def check_agents() -> None:
    if not AGENTS.is_dir():
        return
    ok = 0
    files = sorted(AGENTS.glob("*.md"))
    for path in files:
        ctx = f"agent {path.stem}"
        fm, raw = parse_frontmatter(path)
        bad = False
        if fm is None:
            _emit("FAIL", f"{ctx} — frontmatter が YAML として読めません: {raw[:80]}")
            continue
        if fm.get("name") != path.stem:
            _emit("FAIL", f"{ctx} — name '{fm.get('name')}' がファイル名 '{path.stem}' と不一致（subagent_type 解決が壊れる）")
            bad = True
        if not isinstance(fm.get("description"), str) or not fm["description"].strip():
            _emit("FAIL", f"{ctx} — description が空または未定義です")
            bad = True
        if not fm.get("tools"):
            _emit("WARN", f"{ctx} — tools が未定義（read-only reviewer は Read, Grep, Glob, Bash を明示推奨）")
        if not bad:
            ok += 1
    _emit("PASS", f"agents: {ok}/{len(files)} 件 frontmatter OK")


def check_drill_coverage() -> None:
    """新規 reviewer persona が drill の種カタログから漏れていないかを検査する（#266）。

    `agents/*-reviewer.md` のファイル名 stem（末尾 `-reviewer` を除いた部分）を、
    `facets/instructions/drill.md` の種カタログ表「検出すべき観点」列と突き合わせる。
    1件も対応する種が無ければ WARN——新しい reviewer を足したのに drill が較正できない
    まま放置される（#266 が懸念した「未計測のまま気づかれない」状態）を機械的に検知する。
    `finding-verifier` は検出役ではなく反証役（`--verify-findings` で別枠採点）のため対象外。
    """
    drill_path = SKILLS / "facets" / "instructions" / "drill.md"
    if not AGENTS.is_dir() or not drill_path.is_file():
        return
    drill_text = drill_path.read_text(encoding="utf-8")

    covered: set[str] = set()
    for line in drill_text.splitlines():
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) != 5 or cells[2] in ("検出すべき観点", "---"):
            continue
        for domain in re.split(r"\s*/\s*", cells[2]):
            if domain:
                covered.add(domain)

    reviewers = sorted(p.stem for p in AGENTS.glob("*-reviewer.md"))
    uncovered = [name for name in reviewers if name[: -len("-reviewer")] not in covered]
    if uncovered:
        _emit("WARN", f"drill 網羅性 — {', '.join(uncovered)} が drill.md の種カタログ「検出すべき観点」に"
              "見当たりません（未較正の可能性。/rig:drill で検出率を測れるよう種を追加してください）")
    _emit("PASS", f"drill 網羅性: reviewer {len(reviewers)} 件中 {len(reviewers) - len(uncovered)} 件が種カタログでカバー")


# ── §2 目録ドリフト（validate.md ④ の機械実装）────────────────────────────────
def _expand_braces(token: str) -> list[str]:
    """`a/{b,c}-d` → [`a/b-d`, `a/c-d`]（1段のみ・§2 の記法に十分）。"""
    m = re.search(r"\{([^{}]+)\}", token)
    if not m:
        return [token]
    out = []
    for part in m.group(1).split(","):
        out.extend(_expand_braces(token[:m.start()] + part.strip() + token[m.end():]))
    return out


def check_catalog_drift() -> None:
    """SKILL.md §2 のバッククォート・ブリック参照 → 実ファイル（幽霊エントリ＝FAIL）、
    実ファイル → SKILL.md 記載（追記漏れ＝WARN）を突き合わせる。"""
    skill = (SKILLS / "SKILL.md").read_text(encoding="utf-8")
    s2 = skill[skill.index("## 2."):skill.index("## 3.")]

    base_map = {
        "facets/": SKILLS / "facets", "recipes/": SKILLS / "recipes",
        "patterns/": SKILLS / "patterns", "manifests/": SKILLS / "manifests",
        "agents/": AGENTS, "commands/": ROOT / "commands",
        "hooks/": ROOT / "hooks", "scripts/": ROOT / "scripts",
        "web/": ROOT / "web",
    }
    ghosts = 0
    tokens = set()
    for raw_tok in re.findall(r"`([A-Za-z0-9_{},/.-]+)`", s2):
        for prefix, base in base_map.items():
            if raw_tok.startswith(prefix):
                for tok in _expand_braces(raw_tok):
                    tokens.add((tok, base / tok[len(prefix):]))
                break
    for tok, path in sorted(tokens):
        if tok.endswith("/"):
            exists = path.is_dir()
        else:
            exists = path.exists() or path.with_suffix(".md").exists()
        if not exists:
            _emit("FAIL", f"§2 目録 — `{tok}` が実ファイルに解決できません（幽霊エントリ）")
            ghosts += 1

    # brace 記法（{a,b}-reviewer 等）で登録されたブリックも展開済みトークンから照合する
    expanded_stems = {pathlib.Path(tok).stem for tok, _ in tokens}
    missing = 0
    for sub in ("recipes", "facets/instructions", "facets/personas"):
        for f in sorted((SKILLS / sub).rglob("*.md")):
            if f.stem.startswith("_"):
                continue
            if f.stem not in skill and f.stem not in expanded_stems:
                _emit("WARN", f"§2 目録 — {sub}/{f.relative_to(SKILLS / sub)} が SKILL.md に未記載（pack 追加分への追記漏れ?）")
                missing += 1
    _emit("PASS", f"§2 目録ドリフト: 参照 {len(tokens)} 件（幽霊 {ghosts}）／追記漏れ疑い {missing} 件")


# ── shipped wiki 衛生チェック（賞味期限含む）──────────────────────────────────
def check_wiki() -> None:
    """shipped wiki ページの frontmatter 衛生と賞味期限（reviewed_at・180日）を検査する。"""
    import datetime
    wiki_dir = FACETS / "knowledge" / "wiki"
    if not wiki_dir.is_dir():
        return
    ok = 0
    pages = sorted(wiki_dir.glob("*.md"))
    for path in pages:
        ctx = f"wiki {path.stem}"
        fm, raw = parse_frontmatter(path)
        bad = False
        if fm is None:
            _emit("FAIL", f"{ctx} — frontmatter が読めません（YAML エラー: {raw[:80]}）")
            continue
        if fm.get("slug") != path.stem:
            _emit("FAIL", f"{ctx} — slug '{fm.get('slug')}' がファイル名 '{path.stem}' と不一致")
            bad = True
        if fm.get("status") not in ("canonical", "draft", "deprecated"):
            _emit("FAIL", f"{ctx} — status '{fm.get('status')}' は canonical|draft|deprecated でなければなりません")
            bad = True
        ra = fm.get("reviewed_at")
        if ra is not None:
            try:
                d = ra if isinstance(ra, datetime.date) else datetime.date.fromisoformat(str(ra))
                if (datetime.date.today() - d).days > 180:
                    _emit("WARN", f"{ctx} — reviewed_at が180日超（{d}）: 内容を見直して更新するか deprecated に（知識の賞味期限）")
            except ValueError:
                _emit("FAIL", f"{ctx} — reviewed_at '{ra}' が YYYY-MM-DD 形式ではありません")
                bad = True
        if not bad:
            ok += 1
    _emit("PASS", f"wiki: {ok}/{len(pages)} 件スキーマ OK（shipped tier）")



# ── ブリック・グラフ整合チェック（オントロジー制約・#graph）─────────────────
def check_graph() -> None:
    """orchestrate.py graph --json（型付きグラフの一次実装）を呼び、未解決エッジを検査する。

    導出ロジックを再実装せず subprocess で一次実装を叩く（散文とコードの二重化を作らない）。
    他チェックが既に担う rel（injects=check_personas / uses-*=check_recipe）は二重報告を避けて
    スキップし、ここでは **links-to（wiki 相互リンク切れ）= FAIL / references・mirrors = WARN**
    のみを担当する。
    """
    import subprocess
    proc = subprocess.run(
        [sys.executable, str(pathlib.Path(__file__).parent / "orchestrate.py"), "graph", "--json"],
        capture_output=True, text=True, env={**os.environ, "RIG_HOME": str(ROOT)})
    if proc.returncode != 0:
        _emit("FAIL", f"graph — orchestrate.py graph --json が失敗しました: {proc.stderr[:200]}")
        return
    g = json.loads(proc.stdout)
    covered = {"injects", "uses-persona", "uses-instruction", "uses-pattern",
               "gated-by", "applies-policy", "emits-contract", "extends"}
    bad = 0
    for e in g["edges"]:
        if e["resolved"] or e["rel"] in covered:
            continue
        bad += 1
        if e["rel"] == "links-to":
            _emit("FAIL", f"graph — wiki リンク切れ: {e['from']} → [[{e['to'].split(':', 1)[1]}]] が存在しません")
        elif e["rel"] == "mirrors":
            _emit("WARN", f"graph — {e['from']} に対応する persona がありません（native-first の対が欠落）")
        else:
            _emit("WARN", f"graph — {e['from']} が {e['to']} を参照していますが解決できません")
    if bad == 0:
        _emit("PASS", f"graph: {len(g['nodes'])} nodes / {len(g['edges'])} edges — 型付きグラフに未解決なし")


# ── extends 循環参照チェック（#71・DFS）──────────────────────────────────────
def check_extends_cycles(recipe_files: list[pathlib.Path]) -> None:
    """A→B→…→A の循環を DFS で検出する（#42 の深さチェックと独立）。

    shipped tier のグラフのみを見る（cross-tier の循環は Claude 版 --validate が担当）。
    検出した循環は経路つきで 1 サイクル 1 回だけ FAIL 報告する。
    """
    parent: dict[str, str] = {}
    for path in recipe_files:
        fm, _ = parse_frontmatter(path)
        if fm and fm.get("extends"):
            parent[path.stem] = str(fm["extends"])

    reported: set[frozenset] = set()
    for start in parent:
        path_list: list[str] = []
        in_path: set[str] = set()
        node = start
        while node in parent:           # extends 先がある間だけ辿る
            if node in in_path:         # 現在の経路を再訪 = 循環
                cycle = path_list[path_list.index(node):] + [node]
                key = frozenset(cycle)
                if key not in reported:
                    reported.add(key)
                    _emit("FAIL", f"recipe:circular-extends — circular chain: {' → '.join(cycle)}")
                break
            path_list.append(node)
            in_path.add(node)
            node = parent[node]


# ── needs: 循環依存チェック（チェック B・#152・DFS）─────────────────────────────
def check_needs_cycles(recipe_files: list[pathlib.Path]) -> None:
    """各 recipe の needs: DAG を DFS で走査し、循環依存を検出する（#152）。

    shipped tier のグラフのみを見る（cross-tier の循環は Claude 版 --validate が担当）。
    check_extends_cycles と同じロジック・同じ severity（FAIL）。
    """
    for recipe_path in recipe_files:
        fm, _ = parse_frontmatter(recipe_path)
        if not fm or not isinstance(fm.get("steps"), list):
            continue

        steps = fm["steps"]
        graph: dict[str, list[str]] = {}
        valid_ids: set[str] = set()
        for step in steps:
            if isinstance(step, dict) and step.get("id"):
                sid = str(step["id"])
                valid_ids.add(sid)
                needs = step.get("needs") or []
                graph[sid] = [str(n) for n in needs if isinstance(n, str) and n in valid_ids or True]

        # DFS 色付けアルゴリズム（白=未訪問 / 灰=処理中 / 黒=完了）
        WHITE, GRAY, BLACK = 0, 1, 2
        color: dict[str, int] = {sid: WHITE for sid in valid_ids}
        reported: set[str] = set()

        def dfs(node: str, trail: list[str]) -> bool:
            color[node] = GRAY
            current_trail = trail + [node]
            for dep in graph.get(node, []):
                if dep not in valid_ids:
                    continue
                if color[dep] == GRAY:
                    cycle_start = current_trail.index(dep)
                    cycle = current_trail[cycle_start:] + [dep]
                    cycle_key = " → ".join(cycle)
                    if cycle_key not in reported:
                        reported.add(cycle_key)
                        _emit(
                            "FAIL",
                            f"recipe {recipe_path.stem}: needs 循環依存 — {cycle_key}",
                        )
                    return True
                if color[dep] == WHITE:
                    dfs(dep, current_trail)
            color[node] = BLACK
            return False

        for sid in list(valid_ids):
            if color[sid] == WHITE:
                dfs(sid, [])


# ── release メタデータ整合（plugin.json ⇄ CHANGELOG.md・#231）───────────────────
def check_release_metadata() -> None:
    """plugin.json の version に対応する CHANGELOG.md の `## [x.y.z]` 節が存在するか検査する。

    release.yml はこの対応が見つからない場合サイレントに auto-generated notes へ
    フォールバックする（release 自体は止めない設計）。--validate 側は「気づかず混入する」
    ことを防ぐために FAIL として検出する。
    """
    plugin_path = ROOT / ".claude-plugin" / "plugin.json"
    changelog_path = ROOT / "CHANGELOG.md"
    if not plugin_path.is_file() or not changelog_path.is_file():
        return
    try:
        version = json.loads(plugin_path.read_text(encoding="utf-8"))["version"]
    except Exception as exc:
        _emit("FAIL", f"release — .claude-plugin/plugin.json の version が読めません: {exc}")
        return
    changelog = changelog_path.read_text(encoding="utf-8")
    heading = f"## [{version}]"
    if heading not in changelog:
        _emit(
            "FAIL",
            f"release — CHANGELOG.md に plugin.json の version ({version}) に対応する"
            f' "{heading}" 節がありません',
        )
    else:
        _emit("PASS", f"release: plugin.json version ({version}) ⇄ CHANGELOG.md 対応節が一致")


# ── MCPサーバの静的脅威分析（#303）─────────────────────────────────────────────
def check_mcp_scan() -> None:
    """`orchestrate.py mcp-scan`（#303）をCIの一部として実行する。

    scripts/mcp_server.py（#263）が無い環境ではサイレントスキップ（他のオプトイン
    チェックと同じ方針）。判定ロジックはorchestrate.py側の`mcp_scan()`に一元化し、
    ここでは severity を FAIL/WARN/PASS にマッピングするだけ（二重実装しない）。
    """
    mcp_server_path = ROOT / "scripts" / "mcp_server.py"
    if not mcp_server_path.is_file():
        return
    sys.path.insert(0, str(ROOT / "scripts"))
    import orchestrate  # noqa: E402 — 遅延import（validate.pyの他チェックに影響させない）
    result = orchestrate.mcp_scan(mcp_server_path)
    if not result["available"]:
        _emit("WARN", f"mcp-scan — {result['reason']}")
        return
    sev = result["overall_severity"]
    n_tools = len(result["tool_findings"])
    if sev == "high":
        _emit("FAIL", f"mcp-scan — 総合判定 HIGH（{n_tools} ツール中に要対応の残存リスクあり。詳細は"
                      "`orchestrate.py mcp-scan`を実行して確認）")
    elif sev == "medium":
        _emit("WARN", f"mcp-scan — 総合判定 MEDIUM（{n_tools} ツール中に要確認の残存リスクあり。詳細は"
                      "`orchestrate.py mcp-scan`を実行して確認）")
    else:
        _emit("PASS", f"mcp-scan — 総合判定 LOW（{n_tools} ツール、3層対抗推論で残存リスク低）")


# ── skills-lock.json 整合（/rig:import の出所記録・#249）───────────────────────
_VALID_IMPORT_MODES = ("delegate", "translate", "knowledge")


def check_skills_lock() -> None:
    """skills-lock.json のスキーマ・importedAs 参照整合を検査する。

    ファイルが存在しない場合はサイレントスキップ（wiki/accumulated チェックと同じ方針）。
    第一段は project 層（呼び出し元リポジトリ直下）のみを対象とする。
    """
    lock_path = ROOT / "skills-lock.json"
    if not lock_path.is_file():
        return
    try:
        data = json.loads(lock_path.read_text(encoding="utf-8"))
    except Exception as exc:
        _emit("FAIL", f"skills-lock — JSON として読めません: {exc}")
        return
    if not isinstance(data, dict) or "version" not in data or "skills" not in data:
        _emit("FAIL", "skills-lock — トップレベルに version / skills キーが必要です")
        return

    skills = data["skills"]
    entries = skills.items() if isinstance(skills, dict) else enumerate(skills or [])
    ok = 0
    for key, entry in entries:
        ctx = f"skills-lock[{key}]"
        if not isinstance(entry, dict):
            _emit("FAIL", f"{ctx} — エントリが dict ではありません")
            continue
        bad = False
        for field in ("source", "sourceType", "skillPath", "computedHash"):
            if not entry.get(field):
                _emit("FAIL", f"{ctx} — 必須フィールド `{field}` がありません")
                bad = True
        mode = entry.get("mode")
        if mode is not None and mode not in _VALID_IMPORT_MODES:
            _emit("FAIL", f"{ctx} — mode '{mode}' は不正な値です。許容値: {', '.join(_VALID_IMPORT_MODES)}")
            bad = True
        imported_as = entry.get("importedAs")
        if imported_as is None:
            _emit("WARN", f"{ctx} — importedAs が未記載です（どのブリックに翻訳されたかの traceability 欠落）")
        else:
            for p in (imported_as if isinstance(imported_as, list) else [imported_as]):
                if not (ROOT / str(p)).exists():
                    _emit("FAIL", f"{ctx} — importedAs '{p}' がリポジトリに存在しません")
                    bad = True
        if not bad:
            ok += 1
    _emit("PASS", f"skills-lock: {ok}/{len(skills)} 件スキーマ OK")


# ── selftest（validate.py 自身の回帰テスト・#232）───────────────────────────────
def run_selftest() -> None:
    """FAIL/WARN 判定ロジックの実装ドリフトを、合成フィクスチャで回帰検出する。

    `orchestrate.py selftest` と同じ位置づけ（doctor 自身の doctor）。実ファイルではなく
    最小の recipe frontmatter を一時ディレクトリに書き出し、`check_recipe()` にそのままかけて
    期待どおり FAIL するか/しないかを確認する（`check_recipe` のシグネチャは変更しない）。
    第一段は #227（gate 列挙値）・#228（boolean 型・代表2件）・#219（id slug 形式）・
    #218（checks 型・空エントリ）という、既に実害が出ていた4系統に絞る。
    """
    import tempfile

    def recipe(name: str, extra_top: str, steps_yaml: str) -> str:
        return (
            f"---\nname: {name}\ndescription: selftest fixture\nscope: project\n"
            f"autonomy: interactive\n{extra_top}steps:\n{steps_yaml}---\n\n# {name}\n"
        )

    scenarios: list[tuple[str, bool, str]] = [
        ("gate-ok", False, recipe("gate-ok", "",
            "  - id: verify\n    instruction: verify\n    gate: acceptance-gate\n")),
        ("gate-bad-serial", True, recipe("gate-bad-serial", "",
            "  - id: verify\n    instruction: verify\n    gate: serial\n")),
        ("bool-bad-capture", True, recipe("bool-bad-capture", 'capture: "yes"\n',
            "  - id: implement\n    instruction: implement\n")),
        ("bool-bad-design", True, recipe("bool-bad-design", "design: 1\n",
            "  - id: implement\n    instruction: implement\n")),
        ("id-ok", False, recipe("id-ok", "",
            "  - id: valid-step-2\n    instruction: implement\n")),
        ("id-bad-space", True, recipe("id-bad-space", "",
            '  - id: "My Step"\n    instruction: implement\n')),
        ("checks-ok", False, recipe("checks-ok", "",
            '  - id: verify\n    instruction: verify\n    checks: ["npm test"]\n')),
        ("checks-bad-scalar", True, recipe("checks-bad-scalar", "",
            '  - id: verify\n    instruction: verify\n    checks: "npm test"\n')),
        ("checks-bad-empty", True, recipe("checks-bad-empty", "",
            '  - id: verify\n    instruction: verify\n    checks: ["npm test", ""]\n')),
    ]

    ok = 0
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = pathlib.Path(tmp)
        for stem, expect_fail, content in scenarios:
            fixture = tmp_path / f"{stem}.md"
            fixture.write_text(content, encoding="utf-8")
            start = len(results)
            try:
                check_recipe(fixture)
            except Exception:
                _emit("FAIL", f"selftest '{stem}' — check_recipe 実行時エラー:\n{traceback.format_exc()}")
            got_fail = any(line.startswith("[FAIL]") for line in results[start:])
            passed = got_fail == expect_fail
            ok += passed
            print(f"  [{'OK' if passed else 'NG'}] {stem}"
                  f"（期待: {'FAIL' if expect_fail else 'no-FAIL'} / 実際: {'FAIL' if got_fail else 'no-FAIL'}）")

    total = len(scenarios)
    print(f"\nselftest: {ok}/{total} シナリオ OK")
    sys.exit(0 if ok == total else 1)


# ── メイン ────────────────────────────────────────────────────────────────────
def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "selftest":
        run_selftest()
        return

    recipe_files = sorted(RECIPES.glob("*.md"))
    if not recipe_files:
        print("[WARN] recipes/ に .md ファイルが見つかりません")
        sys.exit(0)

    for recipe_path in recipe_files:
        try:
            check_recipe(recipe_path)
        except Exception:
            _emit("FAIL", f"recipe {recipe_path.stem} — 予期しないエラー:\n{traceback.format_exc()}")

    try:
        check_personas()
    except Exception:
        _emit("FAIL", f"persona スキーマチェック — 予期しないエラー:\n{traceback.format_exc()}")

    try:
        check_commands()
    except Exception:
        _emit("FAIL", f"commands チェック — 予期しないエラー:\n{traceback.format_exc()}")

    try:
        check_agents()
    except Exception:
        _emit("FAIL", f"agents チェック — 予期しないエラー:\n{traceback.format_exc()}")

    try:
        check_drill_coverage()
    except Exception:
        _emit("FAIL", f"drill 網羅性チェック — 予期しないエラー:\n{traceback.format_exc()}")

    try:
        check_catalog_drift()
    except Exception:
        _emit("FAIL", f"§2 目録ドリフトチェック — 予期しないエラー:\n{traceback.format_exc()}")

    try:
        check_wiki()
    except Exception:
        _emit("FAIL", f"wiki 衛生チェック — 予期しないエラー:\n{traceback.format_exc()}")

    try:
        check_graph()
    except Exception:
        _emit("FAIL", f"graph 整合チェック — 予期しないエラー:\n{traceback.format_exc()}")

    try:
        check_extends_cycles(recipe_files)
    except Exception:
        _emit("FAIL", f"extends 循環チェック — 予期しないエラー:\n{traceback.format_exc()}")

    try:
        check_needs_cycles(recipe_files)
    except Exception:
        _emit("FAIL", f"needs 循環チェック — 予期しないエラー:\n{traceback.format_exc()}")

    try:
        check_release_metadata()
    except Exception:
        _emit("FAIL", f"release メタデータチェック — 予期しないエラー:\n{traceback.format_exc()}")

    try:
        check_skills_lock()
    except Exception:
        _emit("FAIL", f"skills-lock チェック — 予期しないエラー:\n{traceback.format_exc()}")

    try:
        check_mcp_scan()
    except Exception:
        _emit("FAIL", f"mcp-scan チェック — 予期しないエラー:\n{traceback.format_exc()}")

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
