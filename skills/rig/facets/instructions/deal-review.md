# instruction: deal-review

商談記録（`templates/deal-record` 形式またはバラバラなメモ）を受け取り、5観点を並列に評価して営業向けの改善フィードバックへ集約する。

## 手順

### ① 記録の収集

`/rig:sales` の引数（記録本文 or ファイルパス）から商談記録を確定する。決まった形でなくてよい（寛容に受理）。欠落項目は各レビュアーが「情報不足」として指摘するため、**記入を強制しない・親が推測で補完しない**。

### ② 知識層の注入

`facets/knowledge/sales-domain/`（自社固有：プロダクト強み・ICP・価格レンジ・競合・良い商談の型）が存在すれば、SKILL.md §5 の facet 配置順（User 先頭=Knowledge）に従って各レビュアー prompt に注入する。存在しなければサイレントにスキップし、汎用観点のみで評価する。

### ③ 並列レビューの dispatch（`pattern: parallel-fanout`）

`pattern: parallel-fanout` に従い、1メッセージで5つの subagent を同時に起動する。各レビュアーには商談記録（＋注入された sales-domain 知識）を渡し、**自分の1観点だけ**を評価させる。

- **hearing 観点**: `facets/personas/sales/hearing-reviewer` を合成して subagent に渡す。
- **needs 観点**: `facets/personas/sales/needs-reviewer` を合成して subagent に渡す。
- **proposal 観点**: `facets/personas/sales/proposal-reviewer` を合成して subagent に渡す。
- **closing 観点**: `facets/personas/sales/closing-reviewer` を合成して subagent に渡す。
- **next-action 観点**: `facets/personas/sales/next-action-reviewer` を合成して subagent に渡す。

各 subagent の出力形式は `output-contracts/deal-verdict`（① 観点レビュアー出力）に従わせること。

> reviewer agent（`agents/sales-*-reviewer`）は将来の任意拡張。現状は persona facet 合成で動かす。

### ④ 集約（`gate: acceptance-gate`）

5つの観点 verdict が揃ったら `acceptance-gate` で受け入れ基準（全観点が判定済み・改善必須点が実行可能な粒度・情報不足が明示済み）を満たすまで収束させる。判定行・根拠・改善必須点だけを読み（記録全文を親 context に抱えない）、`output-contracts/deal-verdict`（② 親の集約レポート）の形式で営業向けレポートを組み立てる。

### ⑤ 提示

総合評価（S/A/B/C）＋観点別テーブル＋次回の具体アクション（優先順）＋情報不足を提示する。情報不足が過半で評価不能なら「記録不足のため評価保留」とし、不足項目の記入を促す。
