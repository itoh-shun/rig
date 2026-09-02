---
description: "[experimental] 手元の材料から pack を起草し、宣言と検査を道具に任せ、承認は人に残す。URL は取りに行かない。"
argument-hint: "<材料の path...> [--type knowledge|skill] [--out <dir>] [--plan]"
---

# rig/pack-author — 材料を入れると、承認待ちの pack が出てくる

> この command asset は installed pack の呼び出し資料です。pack install だけでは
> ホストの slash command に自動登録されません。通常は
> `$rig --recipe pack-author` で起動します。project pack の初回実行では内容を確認し、
> `RIG_ALLOW_PROJECT_PACKS=1` を設定して asset trust を記録してください。

最初に `rig:engine` skill を起動し、PARSE → RESOLVE → COMPOSE → RUN、facet の配置順、
context-minimal の規律に従います。この command は入口だけを担い、規則は
`pack-author-rules` にあります。

## 導入と起動

```text
rig-wb pack install domain:pack-author --scope project --allow-unverified
RIG_ALLOW_PROJECT_PACKS=1 RIG_PACK_DIR=.rig/packs-drafts/company-security \
  $rig --recipe pack-author \
  "docs/security/運用設計書.md docs/security/情報セキュリティ規程.md を knowledge pack に。id は company-security"
```

この pack は `type: tool` です。`declare` step がホスト上で `rig-wb pack sync` /
`validate` / `doctor` / `test` を、`RIG_PACK_DIR` が指すディレクトリに対して実行します。
install する前に recipe の 5 行を読んでください。

## 受け取るもの・受け取らないもの

- **受け取る**：作業ディレクトリにある file。
- **受け取らない**：URL、`file://`、存在しない path。取得も推測もせず、file として置いて
  から渡してほしいと返します。

## 終わったとき残るもの

- 起草された pack ディレクトリ（asset に出典つき、`pack.yaml` は `sync` が書いたもの）
- `draft` の evaluation case
- `pack sync` / `validate` / `doctor` / `test` の出力（`test` は provider が無ければ
  `structural_only`＝未計測）
- 人に決めてもらう欄と、次に人が打つコマンド（`eval promote` / `pack install`）

install も promote もしていません。それは人の行為です。
