"""orchestrate commands: remaining cmd_* entry points (split from scripts/orchestrate.py)."""

import sys
import os
import json
import shlex
import pathlib
import subprocess

from . import config
from .recipes import (auto_orchestrate, git_diff_lines, load_manifest, load_steps,
                      parse_frontmatter, resolve_effective, resolve_extends,
                      resolve_plan_json, resolve_recipe)
from .runstate import compute_next, load_state, new_state, save_state
from .providers import run_loop
from .isolate import setup_isolation, teardown_isolation

# ── コマンド ──────────────────────────────────────────────────────────────────
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
        r = subprocess.run(cmd, shell=True, cwd=str(config.INVOCATION_CWD),
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

def cmd_run(args):
    if not args:
        print("[ERROR] usage: run <recipe> --provider <name> [--verifier-provider <name>] "
              "[--provider-cmd \"...{prompt}...\"] [--max-steps N] [--goal G] [--out f] [--isolate]")
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
    runs = _read_jsonl(config.RUNS_PATH)
    drills = _read_jsonl(config.DRILL_PATH)
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
    wiki_n = len(list((config.INVOCATION_CWD / ".claude" / "rig" / "knowledge" / "wiki").glob("*.md"))) \
        if (config.INVOCATION_CWD / ".claude" / "rig" / "knowledge" / "wiki").is_dir() else 0
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
        dash = pathlib.Path(__file__).resolve().parent.parent.parent / "scripts" / "dashboard.py"
        if not dash.exists():
            print(f"[ERROR] dashboard.py が見つかりません: {dash}")
            sys.exit(1)
        cmd = [sys.executable, str(dash), "--repo", str(config.INVOCATION_CWD),
               "--out", html_out, "--limit", str(limit)]
        if recipe:
            cmd += ["--recipe", recipe]
        if since:
            cmd += ["--since", since]
        rc = subprocess.run(cmd).returncode
        sys.exit(rc)
    if not config.RUNS_PATH.exists():
        print(f"実行記録がまだありません（{config.RUNS_PATH}）。orchestrate run / queue go、"
              "または manual backend のフロー完了（SKILL.md §6）で追記されます。")
        return
    rows = []
    for line in config.RUNS_PATH.read_text(encoding="utf-8").splitlines():
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
    src = config.RIG_HOME / ".claude-plugin" / "bin" / "rig"
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
    print(f"確認: `rig models` または `rig --help`（RIG_HOME={config.RIG_HOME}）")

