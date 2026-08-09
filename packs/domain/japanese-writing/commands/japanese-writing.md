---
description: "[experimental] 根拠を増やさず、宛先の形式と適切な敬語を守った日本語の完成稿を1つ返す。障害連絡・サポート返信にも対応し、別モデルの reviewer で検証する。"
argument-hint: "[用途・読み手・掲載先・明示された事実・下書き] [--plan]"
---

# rig/japanese-writing — 日本語の完成稿を作る

> この command asset は installed pack の呼び出し資料です。pack install だけでは
> ホストの slash command に自動登録されません。通常は
> `$rig --recipe japanese-writing` で起動します。project pack の初回実行では内容を
> 確認し、`RIG_ALLOW_PROJECT_PACKS=1` を設定して asset trust を記録してください。

最初に `rig:engine` skill を起動し、PARSE → RESOLVE → COMPOSE → RUN、facet の配置順、
context-minimal の規律に従います。この command は入口だけを担い、執筆規則は
`writing-delivery-contract` と `japanese-writing-rules-v2` にあります。

## 導入と起動

```text
rig-wb pack install domain:japanese-writing --scope project --allow-unverified
RIG_ALLOW_PROJECT_PACKS=1 $rig --recipe japanese-writing \
  "顧客向け障害連絡。掲載先はメール本文。確認済みの影響、時刻、対応状況は次のとおり: ..."
```

Claude を生成役にする headless 実行例です。最終判定を同じモデルへ戻さないため、
reviewer には別 provider を指定します。

```text
rig-wb run japanese-writing \
  --provider claude \
  --verifier-provider codex \
  --allow-paid-provider \
  --goal "FAQ の回答文。掲載先はプレーンテキスト。明示された事実: ..."
```

`claude` と `codex` は構成例です。prompt asset 自体は provider 固有の語彙や機能に
依存しません。別の組み合わせでも、生成者と最終 reviewer を異なるモデルまたは
provider に分けてください。独立 reviewer を用意できない場合は、合格とせず
「未検証」として扱います。

## 入力に含めるもの

- 何を書くか、誰が読むか、どこへ載せるか。
- 必ず残す固有名詞、数値、時刻、状態、手順。
- 指定済みの文体、敬称、禁止表現、出力形式。
- 障害連絡やサポート返信では、確認済みの影響、現在の状態、実施済み対応、未確認事項。

パスワード、API キー、トークン、認証コード、Cookie、秘密鍵は入力へ貼らないでください。
すでに含まれる場合も、成果物では値を再表示せず `[REDACTED]` に置き換えます。診断情報は、
バージョン、発生時刻、秘密情報を除去したエラー文など、非秘密の必要最小限だけを求めます。

不足情報を推測で埋めません。確認が必要で質問できる状況なら、執筆前に重要な一点だけを
確認します。完成稿を求められた最終出力では、前置き、複数案、解説、追伸を付けず、
指定先へそのまま貼れる一つの文章だけを返します。

## 例

```text
$rig --recipe japanese-writing "社内チャット用。メンテナンス終了の告知。事実: ..."
$rig --recipe japanese-writing "問い合わせ返信。です・ます調。確認済み手順: ..."
$rig --recipe japanese-writing --plan "プレス向け訂正文。明示された事実: ..."
```
