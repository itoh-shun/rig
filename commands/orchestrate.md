---
description: rig/orchestrate — 計算的オーケストレーション。recipe のステップ遷移・ゲート・リトライ・停止・状態保持を決定論ランナー(scripts/orchestrate.py)がコードで強制。run で各 step を別プロセスの rig ハーネスとして自走実行（並列検証・マルチプロバイダ）。
argument-hint: [recipe（省略時は現在の合成ハーネス）] [--run] [--provider rig|claude|mock] [--max-parallel N] [--quorum all|majority] [--plan]
---

# rig/orchestrate — 計算的オーケストレーション 🧭⚙️

**まず `rig` skill を Skill ツールで起動し、その SKILL.md に従うこと。** このコマンドは `--orchestrate` の入口＝**舵をコードが握る**モード。制御ループ（次の step・ゲート合否・リトライ・停止条件・状態保持）を散文でなく **`scripts/orchestrate.py`（決定論ランナー）** に強制させる。手順・契約は `patterns/computational-orchestration` に従う。

```
$ARGUMENTS
```

## 2つの使い方

**① 半自動（モデルが各 step の作業をする）**
ランナーが遷移を決め、モデルが各 step を委譲実行する：
```
python3 scripts/orchestrate.py plan   <recipe>            # ステップ状態機械を算出（--plan 相当・モデル不要）
python3 scripts/orchestrate.py init   <recipe> [--goal G] # run-state を作り最初のアクション
python3 scripts/orchestrate.py next   run-state.json      # 次の遷移を決定論的に計算（START/ADVANCE/RETRY/AWAIT/BLOCKED/ESCALATE/DONE）
python3 scripts/orchestrate.py check  run-state.json      # step の checks:（lint/test 等）を実行＝計算的センサー
python3 scripts/orchestrate.py verdict run-state.json --by <reviewer> --pass|--fail   # 独立検証者の判定（採点者≠生成者）
```

**② 全自動（各 step を別プロセスの rig ハーネスで実行）**
```
python3 scripts/orchestrate.py run <recipe> --provider rig \
    [--verifier-provider rig] [--max-parallel N] [--quorum all|majority] [--goal G]
```
- **`--provider rig`**：各 step を **`rig` skill で起動した別プロセス**として実行（rig を名前で呼ぶ＝再帰 rig ハーネス）。`claude`/`codex`/`cmd`/`mock` も選べる。
- **並列検証**：gated step の `personas` を同時プロセスでファンアウト（`--max-parallel`）。集約は決定論（`--quorum all`＝全員一致／`majority`＝過半数）。
- **構造的に採点者≠生成者**：検証は別プロセス（別プロバイダ可）の rig 検証者が `VERDICT: PASS|FAIL` を返す。

## 効く所

- **prose の制御ループ ≪ コードの強制**（`harness-taxonomy`）。遷移・停止・リトライをコードが握る。
- **状態は `run-state.json` に永続**＝圧縮・再起動を跨いで同じ状態機械を再開（run-continuity の計算版）。
- **opt-in＝engine 不変**。各 step の中身は従来の rig が回す（Thin Harness, Fat Skills）。

## 注意

- `--provider rig`/`claude` は**入れ子で claude が起動**＝コスト・再帰に注意。設計確認は `--provider mock`（別プロセスだが即返す決定論ダミー）。
- ゲート未達 K 回で `ESCALATE`（無限ループ禁止）、自己採点（by=self）は `BLOCKED`。
- 決定論は `python3 scripts/orchestrate.py selftest` で検証できる。
