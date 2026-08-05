---
title: API 互換と semver の判定表
slug: api-compat-semver
aliases: [breaking-changes, semver-rules]
tags: [api, compatibility, review]
domain: dev
status: canonical
links: ["[[migration-expand-contract]]"]
reviewed_at: 2026-07-02
sources: ["semver.org 2.0.0", "Keep a Changelog 1.1.0", "protobuf docs: Updating A Message Type", "Google API Design Guide: Compatibility"]
---

api-compat-reviewer が使う**破壊的変更の判定カタログ**（事実）。「誰が壊れるか」（外部利用者・旧クライアント・保存済みデータ）の特定は persona が行う。

## semver の骨子

- **MAJOR** — 互換性のない変更。**MINOR** — 後方互換な機能追加。**PATCH** — 後方互換なバグ修正。
- `0.y.z` は「何でも変わり得る」だが、利用者がいれば事実上の互換期待が生まれる（変更は changelog で明示）。
- **breaking を patch/minor で出すのが最悪の形**（自動更新で壊れる）。迷ったら MAJOR 側に倒す。

## 破壊的変更の判定表（公開 API）

| 変更 | 判定 |
|---|---|
| 関数/エンドポイント/フィールドの**削除・改名** | breaking |
| 必須パラメータの**追加**／任意→必須化 | breaking |
| 戻り値・レスポンス形状の変更（型変更・フィールド削除・null 化） | breaking |
| エラーの形式・ステータスコードの変更 | breaking（利用者はエラーも parse している） |
| 既定値の変更・暗黙の挙動変更 | breaking（signature 不変でも） |
| 任意パラメータ/フィールドの**追加** | 非 breaking（ただし「未知フィールド無視」が前提） |
| より広い入力の受理（寛容化） | 非 breaking |
| **「内部のつもり」の公開シンボル** | 利用実態で判定（grep してから）。公開されていれば契約 |

## ワイヤ/スキーマ互換（protobuf/JSON/DB を跨ぐ型）

- **後方互換**＝新コードが旧データを読める／**前方互換**＝旧コードが新データを読める。ローリングデプロイは**両方**が要る瞬間を持つ。
- フィールドは**削除でなく非推奨化**（protobuf はタグ番号を reserved に。再利用は事故）。
- enum への値追加は「未知値の扱い」が定義されていて初めて非 breaking。enum からの削除は保存済みデータが読めなくなる。
- 必須フィールドの追加はワイヤ互換を壊す定番（optional＋既定値が定石）。

## 非推奨（deprecation）の型

1. **告知** — deprecated マーク＋代替 API＋除去予定バージョンを docs/changelog に明示。
2. **移行期間** — 新旧併存。実行時警告（ログ/ヘッダ）で利用者に到達させる。
3. **除去** — MAJOR で削除。移行ガイドへのリンクを changelog に残す。

いきなり削除は 1→3 の圧縮＝利用者に移行時間ゼロを強いる。

## CHANGELOG の作法（Keep a Changelog）

- 人間向けに **Added / Changed / Deprecated / Removed / Fixed / Security** で分類。**Breaking は目立たせる**（利用者が upgrade 可否を changelog だけで判断できるのが合格線）。
- 「ユーザーに見える変更が changelog に無い」はドキュメント虚偽化の一種（docs-reviewer と重なる領域）。
