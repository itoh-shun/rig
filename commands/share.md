---
description: "[experimental] 作業結果やメモを、スマホでも読める 1 枚の HTML にまとめ、センサーと別の reviewer を通してから Claude の Artifact として公開する。"
argument-hint: "[材料・読み手・目的] [--update <slug>] [--no-publish]"
---

# rig/share — 1 枚の HTML にして Artifact で共有する

最初に `rig:engine` skill を起動し、PARSE → RESOLVE → COMPOSE → RUN、facet の配置順、
context-minimal の規律に従います。この command は入口と公開の手順だけを担います。書き方の
規則は `share-page-rules` にあります。

エージェントの作業結果は、ターミナルの中やローカルのファイルに残ったままになりがちです。
スマホから読めず、人にも渡せません。この command は、それを **1 枚で完結する HTML** にし、
Claude の Artifact として公開します。サーバー、独自ドメイン、クラウドの権限は要りません。

## 起動

```text
$rig --recipe share "今週の調査結果を、チーム向けの 1 枚にまとめて。材料は notes/research.md"
$rig --recipe share --update weekly-report "先週のページに今週分を足して"
$rig --recipe share --no-publish "移行計画を HTML にしたい。公開はまだしない"
```

recipe `share` が `share/<slug>.html` を書き、`rig-wb share-check` と別の reviewer を通します。
gate を通って受け入れたページだけを、次の手順で公開します。

## 公開の手順（受け入れたあとに親 session が行う）

Artifact tool を持つのは親 session だけです。recipe の step ではなく、ここで行います。

1. **受け入れたファイルをもう一度検査します。** `rig-wb share-check share/<slug>.html` が
   exit 0 であることを確かめます。gate を通ったあとに手で直したなら、その版で検査し直します。
   検査を通っていない版は公開しません。
2. **Artifact tool があるかを確かめます。** 無ければ（Codex、Cursor、Artifact の無い
   環境）、ここで止めて、ページのパスを返します。代わりの公開先を勧めません。
3. **公開します。** Artifact tool の説明が求める skill（`artifact-design`）を先に読み込み、
   `file_path` に `share/<slug>.html`、`icon` に一般的な 1 語、`description` にページの
   要約を 1 文で渡します。
4. **同じページを直したときは同じ URL に上書きします。** 同じ session なら同じ
   `file_path` で公開し直します。前の session で公開したページは、台帳の `url` を
   Artifact tool の `read` で読んでから、その `url` を付けて公開します。
5. **台帳に 1 行足します。** `.rig/share/published.jsonl` に
   `{"slug", "path", "url", "sha256", "published_at"}` を書きます。`sha256` は公開した
   ファイルのものです。`--update <slug>` はこの台帳から `url` を引きます。
6. **URL を返します。** Artifact は作成時には非公開です。誰かに共有するかどうかは本人が
   決める、と一言添えます。pin や共有の設定は、頼まれない限り変えません。

`--no-publish` のときは 1〜2 を行い、パスを返して止まります。

## 公開しないもの

本物の組織や人物を装うページ、本物に見える記録や領収書、入力を集めるフォーム、特定の
個人を狙ったページは、gate を通っていても公開しません。秘密情報や個人情報が入っている
疑いがあれば、公開の前に本人に確認します。規則は `share-page-rules` の「出してはいけない
もの」にあります。

## 用意するもの

- `rig-wb`（`/rig:setup` で入ります）。センサーは Python の標準ライブラリだけで動きます。
- Claude Code の、Artifact tool が使える環境。無い環境でも、検査済みの HTML までは作れます。

## 例

```text
$rig --recipe share "このブランチの変更点を、レビューする人向けに 1 枚で"
$rig --recipe share "障害の時系列と影響範囲を、関係者向けに。材料は incident.md"
$rig --recipe share --update release-notes "3.5.0 の分を足して"
```
