# rig/sales 商談レビュー・ハーネス — design / 実装 spec

- 日付: 2026-06-21
- ブランチ: `feat/rig-sales-deal-review`
- 種別: 新ドメイン pack 追加（rig engine 共用）

## 目的

営業の痛み「**商談の質が人依存**（できる営業とできない営業で差が出て標準化されない）」を解消する。実商談の記録を **多観点で評価し、型化された改善フィードバックを毎回一定品質で返す** ハーネスを rig 上に作る。従として痛み「商談が死蔵される」にも効く（評価の言語化が蓄積の入口になる）。

これは rig の `dev` review フローの **sales 版**であり、「同じ engine・同じ pattern に別ドメインが乗る」ことの最初の実証でもある。

## スコープ

- **事後レビューに絞る**（実商談の評価のみ）。ロープレ（事前訓練）・ナレッジ蓄積ループ・レビュー→ロープレ連携は非スコープ。
- **入口は `/rig:sales`**（営業メンバーも使う想定で flag 最小・出力は平易）。
- **第一実装は「CC 上で動くエンジン」**。営業への配布（Slack 等の UI ラップ）は将来層として切り離す。
- **core/domain 分離は最小**（pack 追加のみ）。engine（SKILL.md）の本格括り出しはしない（YAGNI）。

## アーキテクチャ

engine（PARSE → RESOLVE → COMPOSE → RUN / context-minimal / facet 配置順 / 知識層注入）は **rig 正典 `skills/rig/SKILL.md` を共用**。SKILL.md は既にドメイン非依存に書かれているため、**sales 用に書き換えない**。sales は以下を **追加**するだけ:

```
commands/sales.md                              /rig:sales 入口（薄い。dev.md と同型）
skills/rig/recipes/deal-review.md              商談1件を多観点評価→acceptance-gate で収束
skills/rig/facets/personas/sales/
  ├ hearing-reviewer.md                        ヒアリングの深さ
  ├ needs-reviewer.md                          顧客課題の把握精度
  ├ proposal-reviewer.md                       提案の的確さ
  ├ closing-reviewer.md                        クロージング（意思決定者・予算・期限）
  └ next-action-reviewer.md                    ネクストアクションの具体性
skills/rig/facets/instructions/deal-review.md  手順（記録読込→観点 fan-out→集約）
skills/rig/facets/output-contracts/deal-verdict.md  出力フォーマット
skills/rig/facets/knowledge/sales-domain/      自社固有（プロダクト強み・ICP・型）を外出し
skills/rig/templates/deal-record.md            商談記録の入力テンプレ（新規 templates/）
```

> agent 化（`agents/sales-*-reviewer.md`）は persona フォールバックで動くため**任意・将来**。まずは persona facet で実装する。

## データフロー

1. 営業が `templates/deal-record.md` を埋める（バラバラなメモでも可）。
2. `/rig:sales <記録 or ファイルパス>` を起動。
3. command が rig skill（engine）を起動し、`deal-review` recipe を RESOLVE/COMPOSE。
4. **parallel-fanout** で5観点 reviewer を subagent dispatch（context-minimal: 親は dispatch と集約のみ）。
5. 各 reviewer が `deal-verdict` 形式で評価を返す。
6. **acceptance-gate** で「全観点が合格 or 根拠ある改善必須点を提示」へ収束。
7. 親が営業向けレポート（総合評価＋観点別＋改善アクション＋情報不足）に集約して提示。

## 入力: 商談記録テンプレ（deal-record）

決まった形が無いので同梱する。寛容に受理し、欠落項目は reviewer が「情報不足」として指摘する（記入を強制しない）。項目（叩き台）:

- 顧客名 / 業種 / 規模
- 日時 / 商談フェーズ（初回・提案・クロージング）
- 今回の商談ゴール
- 参加者（相手の役職・意思決定への関与度）
- 相手の課題・現状（ヒアリングした内容）
- 提示した提案・デモ内容
- 相手の反応（ポジ／ネガ／懸念）
- 価格・予算の話
- 競合の有無
- ネクストアクション（誰が・いつ・何を）
- 未確認 / 積み残し

## レビュー観点（汎用5観点・叩き台）

| persona | 見るもの | 主な減点理由 |
|---|---|---|
| hearing-reviewer | ヒアリングの深さ | 表面的要望で止まり、課題の真因・背景・影響まで掘れていない |
| needs-reviewer | 顧客課題の把握精度 | 顕在ニーズのみ、潜在ニーズ・意思決定基準を捉えていない |
| proposal-reviewer | 提案の的確さ | 課題と提案が接続していない、価値訴求・差別化が弱い |
| closing-reviewer | クロージング | 意思決定者・予算・期限（BANT 相当）の確認不足、次の意思決定ステップが不明 |
| next-action-reviewer | ネクストアクションの具体性 | 誰が・いつ・何を・ボールの所在・期限が曖昧 |

各 reviewer には `knowledge/sales-domain/`（自社固有）を注入し、自社プロダクト文脈での評価を可能にする。

## 出力: deal-verdict

- **総合評価**: S / A / B / C
- **観点別**: ◎○△× ＋ 根拠1行 ＋ 良かった点 ＋ 改善必須点
- **次回の具体アクション**: 型化されたフィードバック（誰が次に何をすべきか）
- **情報不足**: deal-record の欠落で評価不能だった項目
- 機械抽出可能な構造（`review-verdict` と同じ思想で、親が集約しやすい形）

## 汎用 / 固有の分離

- **汎用**（どの会社でも使える）: engine・recipe・観点 persona・deal-verdict・deal-record テンプレ。
- **固有**（自社）: プロダクト強み・ICP（理想顧客像）・価格レンジ・競合・「良い商談の型」→ `facets/knowledge/sales-domain/` に外出しし reviewer prompt に注入。差し替えれば他社にも転用可。
- 初期は `sales-domain/` に**記入用の空テンプレ**を置き、中身（自社固有値）はユーザーが後から埋める。

## 受け入れ基準

1. `/rig:sales` に deal-record（またはバラバラメモ）を渡すと、5観点評価＋総合評価＋改善アクション＋情報不足が deal-verdict 形式で返る。
2. engine は SKILL.md 共用（RESOLVE/COMPOSE/context-minimal/知識層注入に従う）。**SKILL.md の dev 規則を一切壊さない**（dev フローは従来どおり動く）。
3. 自社固有が `knowledge/sales-domain/` に分離され、観点・recipe・engine は汎用のまま。
4. context-minimal 厳守: 各観点 reviewer は subagent dispatch、親は deal-verdict 集約のみ（記録全文を親 context に抱えない）。
5. acceptance-gate で「全観点が合格 or 根拠ある改善必須点」に収束する。
6. `/rig:sales` が dev.md と同型の薄い入口で、engine を重複定義していない。

## 非スコープ

- ロープレ（事前訓練）・レビュー→ロープレ連携。
- ナレッジ蓄積ループ（良い商談の型の自動蓄積・次回注入）。痛み2の本格対応は次フェーズ。
- Slack 等への UI 配布（営業が CC を直接叩かない運用）。
- CRM/SFA からの入力自動取り込み。
- engine の本格 core/domain 分離（SKILL.md からの dev pack 括り出し）。
- agent 化（`agents/sales-*-reviewer.md`）。persona facet で動くため将来。
