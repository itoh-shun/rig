---
title: Observability の定石（golden signals・ログ設計）
slug: observability-golden-signals
aliases: [sre-signals, logging-conventions]
tags: [observability, sre, review]
domain: dev
status: canonical
links: ["[[performance-pitfalls]]"]
reviewed_at: 2026-07-02
sources: ["Google SRE Book: Monitoring Distributed Systems（4 golden signals）", "OpenTelemetry docs（trace/metrics/logs）", "12-Factor App: XI. Logs"]
---

observability-reviewer が使う**運用可観測性の定石カタログ**（事実）。基準は「深夜3時の当番が、このログとメトリクスだけで5分以内に原因の見当をつけられるか」。

## 4 golden signals（何を測るか）

- **Latency** — 成功と失敗を分けて測る（失敗の速い応答が平均を歪める）。平均でなくパーセンタイル（p50/p95/p99）。
- **Traffic** — リクエスト率・処理件数。容量計画とアラート閾値の分母。
- **Errors** — 明示的エラー（5xx）＋暗黙的エラー（誤った内容の 200・遅すぎる成功）。
- **Saturation** — 資源の逼迫度（プール使用率・キュー長・メモリ）。壊れる**前**に上がる先行指標。

## 失敗の可視性（diff レビューで見る形）

- **例外の握りつぶし** — 空 catch・catch して info ログのみ・エラーを既定値で置換して続行（黙って間違った結果を返す型）。
- **失敗の行き先** — 失敗が「ログ・メトリクス・呼び出し元へのエラー」の**少なくとも1つ**に必ず現れるか。リトライで隠れる失敗はメトリクスに出す。
- **バックグラウンド処理** — 非同期ジョブ・fire-and-forget の失敗は特に消えやすい（完了/失敗メトリクスと dead letter が定石）。

## ログ設計

- **レベルの規約** — ERROR=人が対応すべき事象／WARN=自動回復したが異常／INFO=状態遷移／DEBUG=開発用。エラーを INFO で流さない・正常系を ERROR にしない（アラート疲れの原因）。
- **文脈 ID** — リクエスト ID・トレース ID・対象エンティティ ID を構造化フィールドで（grep でなくクエリで追える）。
- **書いてはいけないもの** — 資格情報・トークン・生 PII（appsec と同根）。
- **構造化** — key=value / JSON。人間向け文章だけのログは集計できない。

## メトリクス・アラートの追随（変更が壊すもの）

- 挙動・閾値・ラベルを変えたとき、**既存のダッシュボード/アラート/SLO が無意味化しないか**（メトリクス名変更は破壊的変更）。
- 新しい失敗モードには**新しい検知**を対にする（機能追加＝監視追加）。
- アラートは**症状ベース**（ユーザー影響）を一次に、原因ベースは診断用（Google SRE の定石）。

## デプロイ・復旧

- **切り戻し手段** — feature flag / ロールバック手順 / 段階導入（canary）。「戻せない変更」はその旨の明示と監視強化が対。
- **デプロイ順序依存** — migration とコードの順序・複数サービスの互換ウィンドウを明示する。
- **ヘルスチェック** — liveness（再起動判断）と readiness（トラフィック投入判断）の区別。依存先の疎通をどちらに含めるかは連鎖再起動のリスクと引き換え。
