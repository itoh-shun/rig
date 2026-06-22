---
description: rig — LEGO-style dev-flow orchestrator. Compose facet/pattern/step/recipe bricks at invocation into a task-specific agent harness (review / implement / PR, etc.). レゴ式ハーネス・オーケストレータ。
argument-hint: [--recipe review-only|release-flow|design-first|hotfix] [--only <step>] [--from <step>] [--issue <id>] [--design] [--review] [--tdd] [--visual] [--autonomous] [--workflow] [--plan] [--save-recipe <name>] [--capture] [--list] [--validate] [--adversarial] [--persona <name>] [自由記述]
---

# rig — dev-flow orchestrator

**まず `rig` skill を Skill ツールで起動し、その SKILL.md（PARSE → RESOLVE → COMPOSE → RUN の全規則・context-minimal・facet 配置順・recipe スキーマ・知識層注入）に厳密に従うこと。** このコマンドは入口であり、エンジン本体は skill 側にある（重複定義しない）。

起動後、次の引数を PARSE してハーネスを合成・実行する:

```
$ARGUMENTS
```

## クイックリファレンス（詳細は skill §3〜§6）

**shipped recipe**（`--recipe <name>`）:
- `review-only` — 現在の変更に 3-way 並列レビュー(security/design/test)だけ
- `release-flow` — intake→design?→implement→verify→review?→pr→merge（size-aware で ? は条件付き）
- `design-first` — 設計フェーズ厚め
- `hotfix` — 最短経路（intake→implement→verify→pr）

**よく使う flag**:
- `--plan` … COMPOSE まで実行してハーネスを提示し停止（実行しない・ドライラン）
- `--only <step>` / `--from <step>` … 実行範囲をスライス（例 `--only review`）
- `--design` / `--review` / `--tdd` … 該当 step を強制 ON（既定は size-aware）
- `--issue <id>` … 既存 Issue を intake 入力に
- `--autonomous` … step ゲートを省き完走（capture ゲートは解除されない）
- `--workflow` … 実行バックエンドを ultracode Workflow に（重い多段/網羅時のみ・opt-in）
- `--save-recipe <name>` … 今回の合成を recipe として保存（`--user` で user 層）
- `--capture` … RUN 後の学びを承認ダイアログなしで知識層へ（提案表示・事後報告は省略しない）
- `--list` … 利用可能なブリック・recipe・flag を一覧表示して停止（実行しない）
- `--adversarial` … 敵対的レビュー（AI の癖排除・人間可読性・不要コメント除去）step を合成に追加

## 例

```
/rig:dev --plan --only review "現在の変更"        # レビュー構成をドライラン確認
/rig:dev --only review                            # 3-way 並列レビューを実行
/rig:dev --recipe release-flow --design "新機能X" # フルフローを設計込みで
/rig:dev --recipe hotfix --issue 1234             # 緊急修正を最短経路で
```

## 規則（skill から要約・詳細は skill が正典）

- **引数が空 / 曖昧** → 対話 composition（何をしたいか訊き、ブリックを提案して選ばせ、ハーネス提示→確認）。
- **`--plan`** → COMPOSE で停止し合成ハーネスを人間可読で提示。RUN しない。
- **context-minimal（ハードルール）** → 実作業は必ず subagent に dispatch。親は dispatch＋集約＋ゲート判断のみ。長い出力を親 context に引き込まない。
- **size-aware 既定** → S/M は design/review/tdd を自動 OFF（明示 flag で ON）。
