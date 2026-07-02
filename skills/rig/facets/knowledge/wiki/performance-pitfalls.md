---
title: Performance 落とし穴カタログ
slug: performance-pitfalls
aliases: [n-plus-one, perf-catalog]
tags: [performance, review]
domain: dev
status: canonical
links: ["[[observability-golden-signals]]"]
reviewed_at: 2026-07-02
sources: ["Use The Index, Luke (use-the-index-luke.com)", "High Performance Browser Networking (hpbn.co)", "各 ORM 公式ドキュメント（eager loading）"]
---

performance-reviewer が使う**スケールで壊れる形のカタログ**（事実）。判断は「このデータ量でこう壊れる」の見積りとセットで persona が下す。

## データ量に比例して壊れる形

- **N+1 クエリ** — 一覧取得後にループ内で関連を1件ずつ取得。ORM の遅延ロードが既定の場合、コード上は見えない（eager loading / JOIN / IN 句バッチが対処）。
- **全件ロード** — LIMIT なし SELECT・`findAll()`・全件を配列に載せてからアプリ側でフィルタ/ソート。ページネーション・ストリーミング・DB 側集計が対処。
- **ループ内 I/O** — ループ内の HTTP 呼び出し・ファイル open・DB 接続。バッチ API・接続再利用・まとめ読みが対処。
- **O(n²) 以上** — 二重ループでの突き合わせ（`for a in xs: if a in ys`＝リスト in はさらに×n）。set/dict 化・ソート済みマージが対処。
- **無制限の再帰/展開** — 深さ制限のないツリー走査・正規表現の破滅的バックトラック（ネストした量指定子）。

## ホットパスの無駄

- **直列 await** — 独立な I/O を順番に待つ（`await a(); await b()`）。並列化（gather/Promise.all）が対処。
- **不要な再計算** — ループ不変式の内側計算・毎リクエストの設定パース/正規表現コンパイル。
- **過剰なコピー/シリアライズ** — 大きなオブジェクトの clone・JSON 変換の往復・文字列連結の繰り返し（builder/join が対処）。
- **チャットな通信** — 1画面のために数十回の小さな API 呼び出し（バッチ/集約エンドポイントが対処）。

## リソースの扱い

- **解放漏れ** — 接続/ファイル/リスナーを close しない経路（例外パス含む）。with/try-finally/defer が対処。
- **無制限の成長** — 上限なしキャッシュ・キュー・メモ化 dict（LRU/TTL/上限が対処）。イベントリスナーの積み増し。
- **キャッシュの無効化漏れ** — 書き込み経路がキャッシュを更新しない→古い値を配り続ける（測るまで気づかない）。
- **コネクションプール枯渇** — プールサイズ < 並列度、長トランザクションによる占有。

## インデックス・クエリ

- **暗黙の全表走査** — WHERE 句の関数適用（`WHERE lower(email)=...`）・前方一致でない LIKE・型不一致で index が効かない。
- **複合インデックスの列順** — 等値→範囲の順でないと効かない。ORDER BY + LIMIT が index でカバーされているか。

## 測定の作法（指摘に添える根拠）

- 「遅そう」ではなく**データ量の見積り**（現在の行数×成長率）か**計測手段**（EXPLAIN・プロファイル・ベンチ）を添える。
- ホットパスでない箇所のマイクロ最適化は要求しない（可読性との交換に見合わない）。
