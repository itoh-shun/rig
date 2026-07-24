---
description: "rig/catalog — 横断レジストリ(統合管理)。全 tier(shipped＋global＋project)を走査し domain×pack×persona×wiki×recipe の地図を表示。「誰がどこで何してるか」を取り戻す。読み取り専用。"
argument-hint: "[--domain <tag>] [--json] [--graph [--focus <name>]]"
---

# rig/catalog — 横断レジストリ（統合管理ハーネス）

**まず `rig:engine` skill を Skill ツールで起動し、その SKILL.md（context-minimal・tier 解決・知識層）に従うこと。** このコマンドは入口であり、手順本体は `facets/instructions/catalog` にある（重複定義しない）。`--list --global` と同等。

起動後、`facets/instructions/catalog` に従って全 tier を走査し地図を出す:

```
$ARGUMENTS
```

## やること

shipped＋user(global)＋project(`<repo>`) を走査して、**domain ごとに pack / persona（→inject する wiki）/ wiki ページ / recipe** を、**tier（どこに居るか）**つきで地図表示する。レジストリは手で持たず**毎回走査して派生**（ドリフトしない）。**読み取り専用・副作用なし**。

domain/プロダクトが増えて「誰がどこで何をしているか把握できない」状態を解消するための統合管理ビュー。

## flag

- `--domain <tag>` … 当該 domain だけ表示。
- `--json` … 機械可読 JSON で出力（将来のグラフ可視化用。既定は Markdown の地図）。
- `--graph` … **型付きブリック・グラフ**を表示（一次実装は `scripts/orchestrate.py graph`）。injects / extends / uses-* / gated-by / mirrors 等11種の関係を frontmatter・steps: から**導出**する（手で書かない＝腐らない）。`--focus <name>` で1ホップ近傍（そのブリックが何を使い・誰に使われるか）、`--json` 併用で機械可読。

## 関連

- `/rig:dev --validate --global` … tier 横断の衛生点検（orphan・リンク切れ・参照欠落・重複）。
- `/rig:persona` / `/rig:knowledge` … 地図に並ぶ persona / wiki を増やすジェネレータ。

## 例

```
/rig:catalog                 # 全 domain の地図
/rig:catalog --domain music  # music ドメインだけ
/rig:catalog --json          # 機械可読
/rig:catalog --graph                          # 型付きグラフの全体サマリ
/rig:catalog --graph --focus security-reviewer  # 1ホップ近傍（誰に使われ何を注入するか）
```
