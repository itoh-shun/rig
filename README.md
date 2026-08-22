# rig-articles

[rig](https://github.com/itoh-shun/rig) についての記事と、その作成記録。

記事本体だけでなく、そこへ至る工程（意図の固定・根拠集め・レビュー・受け入れ判定）を
そのまま残しています。rig の思想を記事で書く以上、記事自体も同じ扱いを受けるべき、
という理由です。

## 記事

| | タイトル | 状態 |
|---|---|---|
| 01 | [Claude Codeで楽になるはずが、気づけばAIの面倒を見ていた](01-ai-manager/final.md) | READY（未投稿） |

## 各記事のディレクトリ構成

| ファイル | 役割 |
|---|---|
| `intent.md` | 想定読者・記事で扱う問い（1つだけ）・non-goal を先に固定したもの |
| `sources.md` | 記事中の技術的主張を rig リポジトリの file:line に紐付けた対応表。実行して出力を確認したコマンドの記録もここ |
| `outline.md` | 章ごとの役割と分量配分。機能一覧にならないための制約 |
| `draft.md` | 初稿 |
| `technical-review.md` | 事実照合レビュー（read-only・執筆側とは別モデル）の記録 |
| `reader-review.md` | 想定読者役レビューの記録と、指摘を反映した/しない理由 |
| `final.md` | 最終稿 |
| `assurance.json` | 受け入れ基準の判定と、その根拠 |

## 受け入れ基準

記事は12個の基準を満たすまで READY にしていません。判定は `assurance.json` にあります。

| 基準 | 内容 |
|---|---|
| `target_reader_defined` | 想定読者が特定されている |
| `single_thesis` | 扱う問いが1つに絞られている |
| `evidence_grounded` | 技術的主張がリポジトリの根拠に紐付いている |
| `no_invented_experience` | 体験談・数値・エラー文を捏造していない |
| `shipped_vs_roadmap_clear` | 実装済みと構想を混同していない |
| `commands_verified` | 記事中のコマンドが実在し、実行して確認済み |
| `independent_review_done` | 執筆者と別の系統で事実照合をしている |
| `target_reader_reviewed` | 想定読者役のレビューを受けている |
| `no_ai_smell` | AI 臭の徴候カタログに対する検査を通っている |
| `not_sales_copy` | 宣伝コピーになっていない |
| `actionable_cta` | CTA が Star 依頼で終わっていない |
| `no_secret_leak` | 秘密情報が含まれていない |

`no_invented_experience` を入れているのは、この種の記事で一番やりがちな失敗が
「文章を人間っぽく見せるために体験談を作ること」だからです。
書ける動機は実際に持っていたものだけ、書ける数値は実際に測ったものだけにしています。

## ライセンス

記事本文は [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)。
