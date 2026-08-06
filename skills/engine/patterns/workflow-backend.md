# pattern: workflow-backend

## 概要

RUN フェーズの**実行バックエンド**を選択するパターン。  
既定バックエンドは **manual**（親が `Agent` ツールで subagent を手 dispatch）。  
`--workflow` フラグまたは ultracode on 時に **workflow** バックエンドを opt-in で使う。

---

## いつ workflow バックエンドを選ぶか

以下の条件を**複数満たす**ときのみ opt-in を検討する。**それ以外は manual を維持する。**

| 条件 | 目安 |
|---|---|
| 重い多段 fan-out（3 stage 以上の並列 dispatch） | review + design + implement + verify を同時進行するなど |
| 網羅的レビュー（観点数が多く全結果を待つ必要がある） | security / design / test / performance など多観点を barrier で集約 |
| 大規模 migration / リファクタリング（変更規模 L 以上） | 200行超・複数ファイル横断の変更 |
| バックグラウンド実行で親 context を節約したい | 長期タスクを親 context から切り離す |

**size-aware 既定との関係**：S / M サイズの変更では workflow を選んではいけない。manual で十分。

---

## 構造ブリック → Workflow 構文 対応表

COMPOSE 済みのハーネスを workflow バックエンドで実行する際の写像。

| 構造ブリック | Workflow 構文 | 説明 |
|---|---|---|
| `parallel-fanout` | `parallel([エージェントA, エージェントB, ...])` | 独立した観点・タスクを同時起動 |
| step の直列列（s1 → s2 → s3） | `pipeline(items, stage1, stage2, stage3)` | 順序依存の段階を順に処理 |
| `review-gate`（全観点集約→着手判断） | barrier + 集約関数 | 全並列エージェントの完了を待ち、ACCEPT / REJECT を判定 |
| loop-until（例: review が ACCEPT になるまで） | `while` ループ | REJECT 時に実装フィードバックを繰り返す |

> **注**: workflow バックエンドを選択した場合でも、COMPOSE フェーズで組み立てるブリック構造は変わらない。変わるのは RUN での実行手段だけ。

---

## workflow バックエンドが context-minimal と一致する理由

| 特性 | 理由 |
|---|---|
| **バックグラウンド実行** | subagent の tool 出力が親コンテキストに流れ込まない |
| **構造化された集約** | barrier / pipeline が完了通知だけを返すため、親は判定行のみ受け取れる |
| **CC ネイティブ primitive** | ultracode の Workflow ツールは Claude Code のネイティブ機能。外部 DSL ではない |

これは SKILL.md §6 の context-minimal ハードルールと同じ方向を指す。

---

## ガード（重厚なワークフローエンジン化の回避・opt-in 厳守）

> **これらのガードを破ったら即 STOP し manual に戻ること。**

1. **Workflow は明示 opt-in 必須**  
   `--workflow` フラグ、または ultracode が明示的に on になっている場合のみ使う。  
   **自動的に workflow へ切り替えることは禁止。**

2. **recipe を Workflow へ恒久コンパイルするエンジンを自作しない**  
   「recipe → Workflow スクリプトへの変換器」を実装することは重厚なワークフローエンジン化そのもの。  
   Workflow スクリプトはバックエンド選択時にモデルが**その場で**（ad hoc に）生成する。  
   永続的なコンパイル層は作らない。

3. **既定は常に manual**  
   `--workflow` なし、ultracode off の状態では manual バックエンドで動く。  
   この既定を変えてはならない。

4. **workflow 実際起動は ultracode 明示 opt-in 時のみ**  
   `--plan` や `--workflow` を指定しても ultracode が off のときは  
   バックエンド選択の提示（COMPOSE 出力）にとどめ、Workflow ツールを呼ばない。

---

## 参照

- `patterns/parallel-fanout` — manual バックエンドでの並列 dispatch
- `patterns/review-gate` — 集約・着手判断（manual / workflow 共通ロジック）
- SKILL.md §6 RUN（context-minimal ハードルール）
- SKILL.md §3 PARSE（flag 一覧の `--workflow` 行）
