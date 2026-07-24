# instruction: spd-review

SPD（院内物品物流管理）に関わる対象——提案書・企画書・要件/仕様・業務フロー・契約骨子・システム設計など——を受け取り、**6つのステークホルダー視点**で並列評価して改善フィードバックへ集約する。

## 手順

### ① 対象の受理

`/rig:spd` の引数（本文 or ファイルパス）から評価対象を確定する。形式は問わない（寛容に受理）。欠落項目は各レビュアーが「情報不足」として指摘するため、**記入を強制しない・親が推測で補完しない**。

### ② 知識層の注入

`facets/knowledge/spd-domain/` を SKILL.md §5 の facet 配置順（User 先頭=Knowledge）に従って各レビュアー prompt に注入する。

- **汎用**: `spd-basics`（SPDの定義・業務・運用形態）、`spd-industry`（業界構造・日本SPD協議会・行政動向・業界課題）、`spd-glossary`（ユビキタス言語）。
- **固有**: `_template` に記入があれば（または project 層 `<repo>/.claude/rig/knowledge/domain/` にあれば）自院/自社文脈として併せて注入。未記入ならサイレントにスキップ。

### ③ 並列レビューの dispatch（`pattern: parallel-fanout`）

`pattern: parallel-fanout` に従い、1メッセージで6つの subagent を同時に起動する。各レビュアーには対象（＋注入された spd-domain 知識）を渡し、**自分の1視点だけ**を評価させる。

- **hospital-executive 視点**: `facets/personas/spd/hospital-executive`（病院経営層）
- **materials-manager 視点**: `facets/personas/spd/materials-manager`（用度・材料部）
- **ward-nurse 視点**: `facets/personas/spd/ward-nurse`（看護現場）
- **spd-operator 視点**: `facets/personas/spd/spd-operator`（SPD現場）
- **spd-vendor-manager 視点**: `facets/personas/spd/spd-vendor-manager`（SPD事業者経営）
- **distributor 視点**: `facets/personas/spd/distributor`（卸・流通）

各 subagent の出力形式は `output-contracts/spd-verdict`（① 視点レビュアー出力）に従わせること。

### ④ 集約（`gate: acceptance-gate`）

6つの視点 verdict が揃ったら `acceptance-gate` で受け入れ基準（全視点が判定済み・懸念点が実行可能な粒度・情報不足と事実誤認が明示済み）を満たすまで収束させる。判定行・根拠・懸念点だけを読み（対象全文を親 context に抱えない）、`output-contracts/spd-verdict`（② 親の集約レポート）の形式で組み立てる。

### ⑤ 提示

総合判定（GO / 条件付きGO / 要再検討）＋視点別テーブル＋優先アクション＋情報不足を提示する。情報不足が過半で評価不能なら「記述不足のため評価保留」とし、不足項目の追記を促す。

## 単一ペルソナ相談モード（`--as <persona>`）

`--as` が指定されたら fan-out せず、指定ペルソナ1体（例: `--as ward-nurse`）に知識層を注入して対象への質問・相談に**その立場から**答えさせる。この場合 output-contract は課さず、会話体で回答してよい。ただし persona の read-only 制約（実装しない・推測で埋めず情報不足を明示する）は維持する。
