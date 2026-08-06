---
title: Migration の定石（expand-contract）
slug: migration-expand-contract
aliases: [parallel-change, db-migration-patterns]
tags: [migration, database, review]
domain: dev
status: canonical
links: ["[[api-compat-semver]]"]
reviewed_at: 2026-07-02
sources: ["Martin Fowler: Evolutionary Database Design / ParallelChange", "Refactoring Databases (Ambler & Sadalage)", "PostgreSQL docs: ALTER TABLE / CREATE INDEX CONCURRENTLY"]
---

migration-reviewer が使う**移行の定石カタログ**（事実）。移行中は**旧コードと新コードが同時に走る瞬間が必ずある**（ローリングデプロイ・ロールバック）を前提に読む。

## expand-contract（parallel change）の3段

1. **expand** — 新しい形を**追加**する（新カラム・新テーブル・二重書き込み開始）。旧形はそのまま。ここまでは常にロールバック可能。
2. **migrate** — データを埋め、読み取りを新形へ切り替える（feature flag / 段階的）。検証クエリで新旧一致を確認してから進む。
3. **contract** — 全コードが新形に移った**後**に旧形を削除する（別リリースに分ける。expand と contract を1リリースに入れない）。

一発の破壊的 ALTER（カラム改名・型変更・NOT NULL 追加を直接）はこの3段の圧縮＝新旧共存の瞬間に必ず壊れる。

## ロック・所要時間（本番データ量で考える）

- **テーブル書き換えを伴う ALTER**（型変更・デフォルト付き NOT NULL 追加の一部・行フォーマット変更）は全行コピー＝表ロック相当。大テーブルでは分オーダーの停止になり得る。
- **インデックス作成**は CONCURRENTLY（PostgreSQL）/ ONLINE（MySQL 8/InnoDB）系を使う。無印はロック。
- **全件 UPDATE/DELETE** はバッチ分割（数千〜数万行単位＋スリープ）。単発トランザクションは undo/replication 遅延も膨らむ。
- 見積りの根拠＝**本番の行数とトラフィック**。開発 DB では全部一瞬で終わる。

## 復路（down / ロールバック）

- up と対になる down を書く。**書けない移行（データ破壊を伴う）は「戻れない」と明示**し、直前バックアップ・段階導入を代替にする。
- コードとスキーマの**デプロイ順序依存**を明示する（migration 先か・コード先か・どちらでも安全か）。
- contract（削除）の down は「re-expand」＝旧形の再作成＋データ復元まで含めて初めて復路。

## データの正しさ

- 移行後の**機械検証**を用意する：件数一致・チェックサム・サンプル照合クエリ。「migration が exit 0 なら成功」は検証ではない。
- 二重書き込み期間は**新旧の突き合わせジョブ**（乖離検知）を回すのが定石。

## よく壊れる箇所（レビューで grep する場所）

- ORM の自動マイグレーション生成物に紛れた意図しない DROP/型変更。
- enum への値追加（DB によってはテーブル書き換え）・enum からの値削除（旧データが読めなくなる）。
- ユニーク制約の追加（既存重複データで失敗）— 事前の重複検査があるか。
- タイムゾーン/照合順序の変更（全行に影響・インデックス再構築）。
