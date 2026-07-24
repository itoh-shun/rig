---
description: "rig/spd — SPD(院内物品物流管理)ドメイン・ハーネス。提案書/仕様/業務フロー/契約骨子を業界6ステークホルダー視点で並列評価(spd-review)するほか、--as <persona> で単一ペルソナへの相談に切り替える。医療×物流ドメインの平易な入口。"
argument-hint: "[評価対象の本文 or ファイルパス] [--as <persona>] [--plan] [--autonomous]"
---

# rig/spd — SPDステークホルダー・レビュー（＋ペルソナ相談）

**まず `rig:rig` skill を Skill ツールで起動し、その SKILL.md（PARSE → RESOLVE → COMPOSE → RUN・context-minimal・facet 配置順・知識層注入）に従うこと。** このコマンドは入口であり、エンジン本体は skill 側にある（重複定義しない）。dev / sales と同じ engine を SPD ドメインで使う。

## SPDとは（1行）

SPD = Supply Processing and Distribution。病院の診療材料・医薬品・消耗品の購買・在庫・供給・消費データ管理を一元化する業務。業界団体は一般社団法人 日本SPD協議会（https://www.spdjapan.org/ ）。ドメイン知識は `skills/rig/facets/knowledge/spd-domain/` が正本。

## モード（2 系統）

| 指定 | recipe | 何をする |
|---|---|---|
| （既定・評価対象を渡す） | `spd-review` | 対象を6ステークホルダー視点で並列評価し総合判定＋優先アクション |
| `--as <persona>` | （recipe なし） | 指定ペルソナ1体が知識層を背負ってその立場から質問・相談に答える |

`--as` に渡せる persona: `hospital-executive`（病院経営層）/ `materials-manager`（用度・材料部）/ `ward-nurse`（看護現場）/ `spd-operator`（SPD現場）/ `spd-vendor-manager`（SPD事業者経営）/ `distributor`（卸・流通）。

起動後、次の引数を PARSE する:

```
$ARGUMENTS
```

## やること

- **ステークホルダー・レビュー（既定）**: 引数（提案書・仕様・業務フロー・契約骨子などの本文 or ファイルパス）を `spd-review` recipe に渡す。手順本体（知識注入 → 6視点 `parallel-fanout` → `acceptance-gate` → 総合判定 GO/条件付きGO/要再検討 ＋視点別 ＋優先アクション ＋情報不足の集約提示）は `facets/instructions/spd-review` に従う。
- **ペルソナ相談（`--as`）**: fan-out せず、`facets/personas/spd/<persona>` に `spd-domain` 知識を注入して、その立場から会話体で答える（`facets/instructions/spd-review` の単一ペルソナ相談モード）。read-only 制約（実装しない・推測で埋めず情報不足を明示）は維持する。

## 自院 / 自社固有の評価

`skills/rig/facets/knowledge/spd-domain/_template.md` に施設プロフィール・運用実態・KPI・課題を記入しておくと、各ペルソナが固有文脈で評価する。未記入なら汎用の業界知識（`spd-basics` / `spd-industry` / `spd-glossary`）のみで動く。社外秘は `<repo>/.claude/rig/knowledge/domain/` 配下でもよい（SKILL.md §5 の project 層）。

## flag

- `--as <persona>` … 単一ペルソナ相談モードに切り替える。
- `--plan` … COMPOSE まで実行してハーネスを提示し停止（ドライラン）。
- `--autonomous` … 確認を省き完走。

## 例

```
# ステークホルダー・レビュー
/rig:spd ./docs/spd-proposal.md                     # SPD導入提案書を6視点レビュー
/rig:spd "定数管理をバーコードからIoT棚に置き換える計画。対象は…"   # メモ貼り付けで即レビュー

# ペルソナ相談
/rig:spd --as ward-nurse "夜間の臨時請求フローはこれで現場は困らない？"
/rig:spd --as spd-vendor-manager "この委託範囲・価格で事業として持続する？"
```

## run-continuity（SKILL.md §6）

RUN 中は各ターン冒頭に次の run-status ヘッダを1行必ず再掲すること。中断・質疑・tool 出力の直後でも省かない（可視化＝駆動の証拠）:

```
▸ rig | recipe: <name[tier]|ad-hoc> | step: <id> (<n>/<N>) | gate: <none|pending|passed|REJECT> | backend: <manual|workflow> | mode: <gated|autonomous>
```
