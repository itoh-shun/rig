"""orchestrate run-state: state + gate evaluation (split from scripts/orchestrate.py)."""

import os
import json
import datetime
import pathlib

from . import config

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
                                    for v in ss[s["id"]].get("verdicts", [])]}
                      for s in state["steps"]],
        }
        config.RUNS_PATH.parent.mkdir(parents=True, exist_ok=True)
        with config.RUNS_PATH.open("a", encoding="utf-8") as f:
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
        global_rec["project"] = str(config.INVOCATION_CWD)
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
    gate = step["gate"]
    if not gate:
        return "pass"  # ゲート無し step は素通り（checks が空なら）
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

