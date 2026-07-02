---
title: AppSec チェックリスト（攻撃面カタログ）
slug: appsec-checklist
aliases: [owasp-checklist, security-lenses]
tags: [security, review, owasp]
domain: dev
status: canonical
links: ["[[injection-patterns]]"]
reviewed_at: 2026-07-02
sources: ["OWASP Top 10 (2021)", "OWASP ASVS 4.0", "CWE Top 25"]
---

security-reviewer が diff を見るときの**攻撃面カタログ**（事実）。判断（REJECT するか）は persona が持つ。ここは「どこに何が潜むか」だけを並べる。

## 入力起点（外部データが危険な操作に届く経路）

- **SQL/NoSQL インジェクション** — 文字列連結でクエリ組み立て。パラメタライズ/プリペアドの不使用。ORM の raw クエリ逃げ道。
- **コマンドインジェクション** — `shell=True`・バッククォート・`exec` 系に外部入力。引数配列化・shlex.quote の不使用。
- **パス・トラバーサル** — 外部入力をパス結合（`../` 正規化漏れ）。zip 展開時の zip-slip。
- **XSS** — テンプレートの auto-escape 無効化・`innerHTML`/`dangerouslySetInnerHTML`・URL スキーム未検証（`javascript:`）。
- **SSRF** — 外部入力 URL への fetch。内部アドレス（169.254.169.254・localhost・プライベート帯）ブロックの欠如、リダイレクト追従。
- **デシリアライズ** — pickle/yaml.load/ObjectInputStream 等に外部データ。

## 認可・認証（コードよりも「分岐の欠如」に潜む）

- **IDOR** — ID 直指定で他者リソースに到達（オーナーシップ検査の欠如）。一覧 API では絞れているのに単体 GET/UPDATE で漏れる型が頻出。
- **昇格経路** — ロール検査が UI 側のみ・API 側に無い。管理系エンドポイントの認可ミドルウェア外し忘れ。
- **セッション/トークン** — 有効期限なし・失効機構なし・ログアウト後も有効。JWT の `alg: none`/署名未検証。
- **CSRF** — 状態変更 GET・トークン検査の欠如（SPA でも Cookie 認証なら対象）。

## 秘密情報

- **ハードコード** — API キー/接続文字列/秘密鍵のリテラル埋め込み（テストコード・サンプル設定に紛れやすい）。
- **ログ・エラーへの漏れ** — 例外メッセージにクエリ/トークン、デバッグログに PII、スタックトレースの本番露出。
- **コミット履歴** — 一度 push した秘密は削除コミットでは消えない（ローテーション必須）。

## 依存・ビルド

- **既知 CVE** — 新規依存・バージョン更新時の advisory 確認。lock ファイル無しの浮動バージョン。
- **サプライチェーン** — typosquat（1字違いパッケージ）・過剰権限の postinstall スクリプト・出所不明のバイナリ同梱。

## 暗号・乱数

- **自作暗号／弱いプリミティブ** — MD5/SHA-1 でのパスワード保存（bcrypt/scrypt/argon2 が定石）、ECB モード、固定 IV。
- **予測可能な乱数のセキュリティ用途** — `random`/`Math.random` をトークン・パスワードリセットに使用（CSPRNG が必要）。

## 監査

- **記録すべき操作** — 認証イベント・権限変更・データ削除/エクスポート・管理操作。**記録してはいけないもの** — 資格情報・生 PII・トークン。
