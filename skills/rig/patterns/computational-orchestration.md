# pattern: computational-orchestration

**舵をコードが握る** — rig engine（SKILL.md）の制御ループは既定では散文で、遷移を握るのは LLM（SKILL.md を読んで「次の step へ」と判断する）。このパターンは、**step の遷移・ゲート判定・リトライ・停止条件・状態保持を、決定論ランナー `scripts/orchestrate.py` に強制させる**。モデルは各 step の「作業」をするが、「次に何をするか」はコードが決める。**opt-in（`--orchestrate` 明示時のみ）。**

> なぜ要るか：acceptance-gate は「品質を収束させる」仕組みだが、その**ループを回す手綱**は依然 LLM が握っていた（prose 強制）。`harness-taxonomy` の基準＝「prose の依頼 ≪ コードの強制」を、rig 自身の制御ループにも当てる。計算的ガイドとして遷移を強制すれば、ループは context 圧縮・再起動を跨いでも同じ状態機械を辿る（persistence）。

## 何が決定論になるか

| 要素 | 既定（散文・LLM） | `--orchestrate`（コード・決定論） |
|---|---|---|
| 次の step | モデルが SKILL.md を読んで判断 | ランナーが cursor を進める |
| ゲート合否 | モデルが「通った」と判断 | 機械検証(checks)＋独立 verdict をコードが集計 |
| リトライ/停止 | モデルが K 回を数える | ランナーが retries/K・停止条件を強制 |
| 状態保持 | 文脈に依存（圧縮で消える） | `run-state.json` に永続（再開可能） |
| 採点者≠生成者 | 規律（守られないことがある） | by=self/generator の verdict を**ブロック** |

## ランナーの契約（`scripts/orchestrate.py`）

```
plan    <recipe>                 ステップ状態機械を算出（モデル不要・--json 可）
init    <recipe> [--goal G] [--out run-state.json]
                                 run-state を作り最初のアクションを出す
next    <run-state.json>         次の遷移を決定論的に計算・適用（START/ADVANCE/RETRY/AWAIT/BLOCKED/ESCALATE/DONE）
check   <run-state.json>         現 step の checks:（shell）を実行し pass/fail 記録（計算的センサー）
verdict <run-state.json> --by <名> --pass|--fail [--note ...]
                                 独立検証者の推論的判定を記録（採点者≠生成者）
status  <run-state.json>         現在の状態
selftest                         決定論の自己検証
```

## RUN ループ（モデル側の使い方）

1. `init <recipe>` で run-state を作る（`plan` で先に状態機械だけ見てもよい＝`--plan` 相当）。
2. ランナーが `START step X` を出す → モデルは X の作業を**委譲**（context-minimal・engine 規則どおり）。
3. ゲートのある step：
   - 機械検証があれば `check`（lint/test/build 等を実行＝**計算的センサー一次**）。
   - 観点検証が要れば、**生成者と別の reviewer** が `verdict --by <reviewer> --pass|--fail`（採点者≠生成者）。
4. `next` を呼ぶ → ランナーが決定論的に遷移を返す：
   - `ADVANCE`（合格）/ `RETRY`（未達・try n/K）/ `AWAIT`（check/verdict 待ち）/ `BLOCKED`（自己採点）/ `ESCALATE`（K 回未達→停止）/ `DONE`。
5. `ESCALATE`/`BLOCKED` は**進めない**（無限ループ禁止・自己採点禁止）。`DONE` まで 2〜4 を繰り返す。

## recipe 側の任意フィールド `checks:`（計算的センサーの接続点）

step に機械検証コマンドを宣言すると、ランナーが実行してゲートの一次根拠にする（宣言が無い gated step は独立 verdict を要求）。プロジェクト依存なので **manifest / user recipe** で足すのが基本（shipped recipe は汎用のため未宣言）。

```yaml
steps:
  - id: verify
    instruction: verify
    gate: acceptance-gate
    checks:                       # 任意・決定論的バックプレッシャー（全件 exit 0 で合格）
      - "npm test"
      - "npm run lint"
      - "npm run typecheck"
```

## ガード

- **opt-in**（`--orchestrate` 明示時のみ）。既定の散文ループは変えない（engine 不変）。
- ランナーは**遷移の強制**に徹し、各 step の中身（実装・レビュー・検証の作業）は従来どおり engine／委譲先が回す（Thin Harness, Fat Skills）。
- **機械検証(checks)を一次・推論 verdict を二次**（`harness-taxonomy`）。verdict は**生成者と別**（`policies/independent-verification`）— by=self/generator は BLOCKED。
- 状態は `run-state.json` に持つ＝**圧縮・再起動を跨いで同じ状態機械を再開**できる（run-continuity の計算版）。
