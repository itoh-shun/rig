---
name: drill
description: reviewer 検出率の実測（ミューテーション・ドリル）。既知のバグの種を一時 worktree の合成 diff に注入して review fan-out を走らせ、検出/見逃し/誤検出のスコアボードを出す。--replay でペルソナ編集後の回帰リプレイ。本物のコードは触らない。
scope: shipped
steps:
  - id: drill
    instruction: drill
    pattern: serial
    acceptance:
      - "答案キー（種の file:line・class）と全 reviewer の verdict を突き合わせたスコアボードが出力される"
      - "一時 worktree/scratch が破棄され、本物のコードベース・履歴が汚れていない"
    gate: acceptance-gate
autonomy: interactive
---

# drill

> ペルソナ品質を意見でなく**検出率の数字**にする実測 pack。`runs --personas` の間接指標（REJECT ゼロ＝怪しい）を直接測定に格上げする。

## 使う場面

- reviewer 集合を増やした/変えた後の**較正**（どの観点が仕事をしているか）。
- ペルソナの観点文を尖らせた後の**回帰確認**（`--replay <persona>`＝過去 diff への再実行で verdict 差分）。
- `--verify-findings` の反証者が正しい所見まで棄却していないかの監査。

## 仕組み

観点対応の種カタログ（認可漏れ/N+1/破壊的変更/片道 migration/テスト欠落/ドキュメント虚偽化…）から種を選び、一時 worktree に自然なコードとして注入 → review fan-out → 答案キーと突き合わせ。結果は `.rig/drill-results.jsonl` に蓄積。詳細は `facets/instructions/drill`。
