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
reviewer には別 provider を指定します。headless 実行では PATH 上の名前を信用せず、利用者が
内容と所有者・mode を確認した executable の絶対 path と SHA-256 を local pin config に
記録します。script の場合は shebang interpreter も同様に pin してください。

`.rig/` は gitignore 対象です。`mkdir -p .rig && chmod 700 .rig` で local directory を
owner-only にしてから、次の schema だけを参考に、実機で確認した値を
`.rig/provider-pins.json`（directory 0700、file 0600）へ保存します。`<...>` は例示用の
placeholder であり、この pack に machine 固有の path や digest は同梱しません。

```json
{
  "schema_version": 1,
  "generator": {
    "executable": "<reviewed-absolute-claude-path>",
    "sha256": "<64 lowercase hex characters>",
    "interpreter": "<reviewed-absolute-interpreter-path-if-script>",
    "interpreter_sha256": "<64 lowercase hex characters if script>"
  },
  "verifier": {
    "executable": "<reviewed-absolute-codex-path>",
    "sha256": "<64 lowercase hex characters>",
    "interpreter": "<reviewed-absolute-interpreter-path-if-script>",
    "interpreter_sha256": "<64 lowercase hex characters if script>"
  }
}
```

native executable では `interpreter` 2項目を省略します。digest は、PATH 検索結果をそのまま
採用せず、絶対 path の実体を人が確認した後に `sha256sum <reviewed-absolute-path>` などで
取得します。API key や token はこの config に書きません。

親 process の argv に本文を残さないよう、goal も local の owner-only file で編集し、
stdin から一度だけ渡します。本文自体を shell の引数や command history に書かないで
ください。stdin が空、TTY、UTF-8 以外、または 1 MiB 超の場合は provider call 前に
停止します。

```text
umask 077
${EDITOR:?set EDITOR} "$PWD/.rig/japanese-goal.txt"
chmod 600 "$PWD/.rig/japanese-goal.txt"
rig-wb run japanese-writing \
  --provider claude \
  --verifier-provider codex \
  --secure-provider-config "$PWD/.rig/provider-pins.json" \
  --review-category incident_report \
  --material-profile none \
  --goal-stdin < "$PWD/.rig/japanese-goal.txt"
```

`--review-category` は必須です。通常文は `general`、障害報告は
`incident_report`、サポート返信は `support_reply` を明示します。本文からの推測や
暗黙の default は行いません。この値は run-state に固定され、resume 時の欠落・変更を
拒否します。`incident_report` と `support_reply` では安全性検査の `N/A` を合格扱いに
しません。

`--material-profile` は `none`（既定）、`technical`、`conversation` のいずれかです。
本文から推測しません。後二者は project-owned の短い文体素材を write prompt の Knowledge
位置に一つだけ注入します。素材は事実の根拠にせず、引用もしません。同じ素材を初稿と一度だけの
修正に使い、reviewer には渡しません。この選択と素材・出典 blob の hash は run-state に固定されます。
provider 起動前に選択済み bytes を run-state 隣接の owner-only snapshot へ封印し、初稿と修正は
同じ snapshot だけを読みます。実行中に pack asset が変わっても prompt を差し替えず、resume 時には
現在の asset/source provenance との不一致を拒否します。
出典全体は MIT の不活性 resource として pack 内に同梱され、実行時の照合はその封印済み blob に対して
行います。元の `/docs` checkout は実行時依存ではありません。

`claude` と `codex` は構成例です。prompt asset 自体は provider 固有の語彙や機能に
依存しません。別の組み合わせでも、生成者と最終 reviewer を異なるモデルまたは
provider に分けてください。pin が不足・不一致の場合、または独立 reviewer を用意できない
場合は provider call 前に停止し、合格扱いや安全性の低い実行への downgrade は行いません。
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
