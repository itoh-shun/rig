---
description: rig/harness — プロジェクトの「エージェント開発ハーネス」を 2×2(計算的/推論的 × ガイド/センサー)で監査。空象限と「あるのに効いていない資産」(lint/testがループ外・ルールがprose止まり)を炙り出す。足すより繋ぐ・強制する・薄くするを出す。
argument-hint: [対象（省略時は現在のリポジトリ）] [--plan]
---

# rig/harness — ハーネス監査 🧭

**まず `rig` skill を Skill ツールで起動し、その SKILL.md（PARSE → RESOLVE → COMPOSE → RUN・context-minimal・facet 配置順・知識層注入）に従うこと。** このコマンドは入口であり、エンジン本体は skill 側にある（重複定義しない）。

起動後、`--recipe harness-audit` を既定として次の引数を対象に PARSE する:

```
$ARGUMENTS
```

引数が無ければカレントリポジトリを対象にする。

## やること

対象（リポジトリ＋ AI 開発設定）を `harness-audit` recipe に渡す。手順本体（①対象確定 →② `harness-taxonomy` 注入 →③棚卸し →④2×2 分類と穴出し →⑤手 →⑥ `harness-map` で構造化）は `facets/instructions/harness-audit` に従う。

- **2×2 で棚卸し**: 計算的ガイド（型/scaffold/CLI）／計算的センサー（lint・型・テスト・build・CI）／推論的ガイド（CLAUDE.md・Skills・persona）／推論的センサー（AI レビュー・review-gate）。**空の象限**を可視化する。
- **「ある」と「効いている」を区別**: テストや lint が存在するだけで、hook や acceptance-gate に繋がっていない（＝実行ループのバックプレッシャーになっていない）穴を最優先で拾う。prose 止まりのルールは未強制扱い。
- **足すより繋ぐ・強制する・薄くする**: 新ルールの追加は最後（善意のルール追加は逆効果になりうる＝Context Rot を警戒）。計算的センサーを一次・推論的レビューを二次に。
- 監査は read-only。修正は `/rig:dev`・hook 設定・`acceptance-gate` 基準・`/rig:goal` の独立検証へ委譲。

## flag

- `--plan` … 監査構成（何を棚卸しするか）を提示して停止（ドライラン）。

## 例

```
/rig:harness                  # 現在のリポジトリのハーネスを監査
/rig:harness --plan           # 監査構成だけ先に確認
/rig:harness ./packages/api   # 特定ディレクトリを対象に
```
