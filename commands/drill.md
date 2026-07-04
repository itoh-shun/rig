---
description: "rig/drill — reviewer 検出率の実測（ミューテーション・ドリル）。既知のバグの種を worktree に注入して review fan-out を走らせ、どの reviewer が何を検出したかをスコアボード化。--replay でペルソナ編集後の回帰リプレイ（過去 diff への再実行で verdict 差分）。ペルソナ品質を意見でなく数字にする。"
argument-hint: "[--seeds <n>] [--personas <a,b,…>] [--verify-findings] [--replay [<persona>]]"
---

# rig/drill — reviewer 検出率の実測 🎯

**まず `rig` skill を Skill ツールで起動し、その SKILL.md（PARSE → RESOLVE → COMPOSE → RUN・context-minimal）に従うこと。** このコマンドは入口であり、手順本体は `facets/instructions/drill` にある（重複定義しない）。

起動後、`facets/instructions/drill` に従ってドリルを実行する:

```
$ARGUMENTS
```

## やること

- **実測**：観点対応の**バグの種**（認可漏れ/N+1/破壊的変更/片道 migration/テスト欠落…）を一時 worktree の合成 diff に注入 → review fan-out（`output-contracts/review-findings` で severity・file:line・Blocking/Non-blocking を強制）→ 答案キーと突き合わせて **検出/見逃し/誤検出＋severity精度＋説明品質のスコアボード**。`runs --personas` の間接指標を直接測定に格上げ。persona 単位の `Drill Result`（Score / Missed Issues / Improvement Suggestions）も出力。
- **`--verify-findings`**：反証者も同時採点（正しい種を REFUTED したら失点）。
- **`--replay <persona>`**：ペルソナ編集後、アーカイブ済み過去 diff へ再実行して**新旧 verdict の差分表**＝ペルソナ開発の snapshot テスト。
- 本物のコードは触らない（worktree・終了時破棄）。結果は `.rig/drill-results.jsonl` に蓄積。

## 例

```
/rig:drill                                  # 種5つ・既定 reviewer 集合で実測
/rig:drill --seeds 10 --verify-findings     # 反証者込みの本気の較正
/rig:drill --replay security-reviewer       # 観点を尖らせた後の回帰確認
```


## run-continuity（SKILL.md §6）

RUN 中は各ターン冒頭に次の run-status ヘッダを1行必ず再掲すること。中断・質疑・tool 出力の直後でも省かない（可視化＝駆動の証拠）:

```
▸ rig | recipe: <name[tier]|ad-hoc> | step: <id> (<n>/<N>) | gate: <none|pending|passed|REJECT> | backend: <manual|workflow> | mode: <gated|autonomous>
```
