# rig セルフ磨き — design / 実装 spec

- 日付: 2026-06-21
- ブランチ: `chore/rig-self-polish`
- 種別: ドキュメント/ブリック整備（機能追加なし）

## 目的

rig を **rig 自身の敵対レビュー（lazy-senior / cognitive-economist）でドッグフード**した結果、`SKILL.md` に冗長・自己矛盾が検出された。これを反映して正典 `SKILL.md` の密度・可読性を上げ、併せて `design-first` recipe を `extends` 化して acceptance-gate の drift を構造的に封じる。

## 背景（ドッグフード結果）

2 reviewer とも verdict は **APPROVE_WITH_CONDITIONS**。両者一致の高インパクト指摘:

1. **規範の4重反復** — 「親が直接やるな・委譲しろ」が §6 red flags / §9 アンチパターン表 / §9.1 rationalization 表 / §9.2 red flags の4ブロックで反復（両者 top1）。
2. **shipped recipe 件数の stale** — 58・208行が「4件」表記だが実体は adversarial-review 込みで5件（両者 top2、`--list` の実害かつ自己矛盾）。これは rig 自身の ai-quirk `stale-provisional-note` に該当。
3. **用語の揺れ** — 「親」31 : 「オーケストレーター」16、「サブエージェント」1箇所、「軽さ優先」が3分散（cog top3）。
4. **但し書き・重複の散在** — §2 目録読み方 / §4 末尾「現フェーズの実挙動」/ §5 知識層注入の3連 / --plan・--capture の重複記述。

## スコープ判断

承認済み方針 = **推奨フルセット（下記 1–5 全部・保守的統合）**。
規範統合は「独自価値のあるセクションは残し、重複行だけ削る」保守路線。§9系の一表完全統合（最大圧縮案）は採らない。

## 変更セット

### 1. 規範統合（重複削減・保守的）
- **§9.2「red flags（即 STOP→委譲）」(SKILL.md:394-404) を削除。** §6 RUN 節の red flags（281-288）とほぼ同一。
- red flags は **§6（RUN 節）に一本化**して残す。
- §9 アンチパターン表（367-379）/ §9.1 rationalization 表（381-392）は**残す**（表/言い訳→現実→正しい応答 という独自フォーマットに固有価値）。両表の間で文言が完全一致する行があれば一方を削る程度に留める。
- 期待削減: 約40行。

### 2. 件数 stale の根治
- recipe 名のハードコード列挙（SKILL.md:58 の `--list` 説明、208 の §4 末尾注記、他に列挙があれば全て）を **「§2 のブリック目録を参照」に置換**。件数・名前の二重管理をやめ、再 stale を構造的に防ぐ。
- §2:26 は既に5件（adversarial-review 込み）なので正典として維持。

### 3. 用語統一
- 「オーケストレーター」→「親」。**初出箇所（§6 冒頭付近）でのみ「親（オーケストレーター）」と1度併記**し、以降は「親」に統一。
- `facets/knowledge/_layer.md:9` の「サブエージェント」→ `subagent`。
- 過剰な強調語（「必ず」「禁止」「厳守」「絶対」）は、**ハードルール（context-minimal / capture 承認）にのみ残し**、それ以外の飽和箇所を平文化。やり過ぎない（規範の効力を削がない範囲）。

### 4. 但し書き・重複の一掃
- §2:32「目録の読み方」blockquote を削除（§5 COMPOSE で同内容を詳述済み）。
- §4:206-208「現フェーズの実挙動」注記 → 暫定ニュアンスを除いた「動作仕様」へ圧縮（stale-provisional-note の残骸を断つ）。
- §5:243-246 知識層注入の説明（同一規範を1段落で3回）→ 1文に圧縮し「§5 冒頭の facet 配置順に従う」へ寄せる。
- `--plan` 停止仕様（§3 flag 表 と §5 末尾）、`--capture` ゲート挙動（§7.3 と §9.1）の重複は、正典側を1つ決め他方を参照1行に。

### 5. design-first の extends 化（#1 の本筋）
- `recipes/design-first.md` の frontmatter を **`extends: release-flow`** にする。
- 差分として上書きする step のみ残す:
  - `design` — `personas: [orchestrator, implementer, design-reviewer]` ＋ design 強化（grilling・承認ゲート）。**condition を付けない**（design-first は常に design ON）。
  - `review` — **condition を付けない**（design-first は常に review ON）。personas/gate/output_contract は release-flow 同様。
- `intake` / `implement` / `verify` / `pr` / `merge` は **親（release-flow）から継承**。これにより `verify` の `gate: acceptance-gate` と `review` の `gate: acceptance-gate` が自動継承され、design-first 固有の gate ズレ（verify に gate 無し・review が旧 review-gate）が解消する。
- 本文（使う場面・展開手順）は extends を踏まえて簡潔化。

## 受け入れ基準（acceptance-gate）

実装後、保持中の2 reviewer（lazy-senior / cognitive-economist）に修正版を再レビューさせ、以下を満たすまで収束:

1. 規範重複に関する **high 指摘が 0**（red-flag 系の反復が解消）。
2. recipe 件数・名前の **stale 表記が 0**（全列挙が §2 参照に置換、または5件で一致）。
3. **「オーケストレーター」単独使用が初出併記以外に無い**／「サブエージェント」表記が 0。
4. `design-first` が `extends: release-flow` で解決され、**verify が acceptance-gate を継承・review が無条件**になっている（RESOLVE 手順 §4.2.2 に整合）。
5. **意味的規範の保持** — context-minimal / native-first / capture 承認必須 / determinism-by-gate / size-aware の各規範が削除で失われていない。

## 非スコープ

- rig/sales 構想（別 spec）。
- pattern / facet の機能追加・新ブリック。
- README.md / README.ja.md（別途、version bump 時に同期）。
- plugin の version bump & 再 install（磨き収束後にまとめて実施）。
