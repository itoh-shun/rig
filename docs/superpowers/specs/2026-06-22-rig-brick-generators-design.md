# rig ブリック・ジェネレータ（persona / domain-knowledge を自動生成・product/global 保存）— design spec

- 日付: 2026-06-22
- ブランチ: `claude/rig-goal-loop-resolution-2nl735`
- 種別: 新 pack（ジェネレータ）＋ engine 小改修（persona の tier 解決＋`--persona` flag）
- ステータス: **design（未実装）**

## 目的

User 起点で、**説明文（または自動解析）から専用 persona（レビュアー）やドメイン知識を*自動生成***し、**product 単位（project 層）**または **global（user 層）**に保存できるようにする。「プロジェクトごとに専用エージェントを毎回手で書く」手間をなくし、ブリック・ライブラリを会話で育てられるようにする。

代表例:
```
/rig:persona "80年代の音楽を理解しているレビュアー"            # → project 層に生成
/rig:persona "セキュリティに厳しいシニア" --user               # → global(user 層) に生成
/rig:knowledge --auto                                          # repo を解析しドメイン知識を自動生成
/rig:knowledge "決済ドメインのユビキタス言語と不変条件"          # 説明から生成
```

## スコープと保存先（tier）

| brick | project（product 単位・既定） | user（global・`--user`） | shipped（同梱・参照のみ） |
|---|---|---|---|
| **persona** | `<repo>/.claude/rig/personas/<name>.md` | `~/.claude/rig/personas/<name>.md` | `skills/rig/facets/personas/<name>.md` |
| **knowledge** | `<repo>/.claude/rig/knowledge/domain/<topic>.md` | `~/.claude/rig/knowledge/methodology/<topic>.md` | （`facets/knowledge/`） |

- 既定は **project（product 単位）**。`--user` で global。これは既存の `--save-recipe [--user]` と同じ流儀。
- 解決の優先順位は recipe（§4.2.1）と同じく **project → user → shipped**。

## engine 小改修（2点）

### A. persona facet の tier 解決（mirror §4.2.1）

COMPOSE で persona 名を解決するとき、次の順でファイルを探す（先に見つかった tier 優先）:

| tier | パス |
|---|---|
| project | `<repo>/.claude/rig/personas/<name>.md` |
| user | `~/.claude/rig/personas/<name>.md` |
| shipped | `skills/rig/facets/personas/<name>.md` |

> これにより**生成した persona を名前で即使える**。knowledge は既に2 tier 解決済み（§5）。reviewer は従来どおり「agent 優先・persona facet フォールバック」だが、フォールバック解決にこの tier 検索を使う。

### B. `--persona <name>` flag（生成した persona を使う導線）

review fan-out に **名前指定のカスタム reviewer persona を追加**する。複数可（`--persona a --persona b`）。tier 解決（A）で解決し、`facets/personas/` の組み込み reviewer と同列に dispatch する。

→ 「生成（`/rig:persona`）→ 使用（`/rig:dev --only review --persona <name>`）」が端から端までつながる。

## ジェネレータ（新 pack のブリック）

### 入口コマンド

- `commands/persona.md` → `/rig:persona "<説明>" [--user] [--name <id>]`
- `commands/knowledge.md` → `/rig:knowledge ["<説明>" | --auto] [--user] [--name <id>]`

### instruction facet

- `facets/instructions/persona-gen` — 説明文を受け、**persona facet 形式**（`# persona: <name>` ＋ 語り口・観点・振る舞い）でドラフト → 提案 → 確認 → 指定 tier に書く。`--name` 省略時は説明から `<name>` を提案（例「80年代の音楽…」→ `music-era-80s-reviewer`）。
- `facets/instructions/knowledge-gen` — 2モード:
  - **説明モード**：与えた説明からドメイン知識ドラフトを生成。
  - **`--auto` モード**：subagent が **repo を解析**（コード構造・README・docs・命名）し、**ユビキタス言語／ドメインモデル／主要な規約／ADR 風の決定**を蒸留してドラフト化。
  - → 提案 → 確認 → project domain（既定）／ user methodology に書く。

### 生成メカニクス（context-minimal ＋ gate）

- ドラフト作成・repo 解析は **subagent に dispatch**（親は長文を抱えない・ドラフトだけ受ける）。
- **書き込みは必ず gate**：提案（保存先パス＋ドラフト全文）→ 確認 → 書き込み。
  - **`--user`（global）書き込みは影響が全プロジェクトに及ぶため、特に明示確認**。
  - `--autonomous` でも**生成物の書き込み確認は解除しない**（capture / init と同様）。
- **冪等・非破壊**：同名が既存なら上書きせず差分提案。
- **捏造禁止**：`--auto` は実在のコード/ドキュメントに基づく（無い概念をでっち上げない）。

## データフロー（persona の例）

1. `/rig:persona "80年代の音楽を理解しているレビュアー" [--user]` → rig skill → `persona-gen`。
2. `<name>` を提案（`music-era-80s-reviewer`）。
3. subagent が persona facet ドラフトを作成（人格・観点・レビュー時の着眼点）。
4. 保存先（既定 project / `--user` で global）とドラフトを提示し**確認**。
5. 承認後に書き込み、`--persona music-era-80s-reviewer` での使い方を案内。

## 受け入れ基準

1. `/rig:persona "<説明>"` で persona facet が生成され、既定 project／`--user` で global に保存される（確認必須・冪等）。
2. `/rig:knowledge "<説明>"` および `/rig:knowledge --auto`（repo 解析）でドメイン知識が生成され、project domain／`--user` methodology に保存される。
3. 生成した persona が **tier 解決で名前から使える**（`--persona <name>` で review に投入できる）。
4. 書き込みは常に提案→確認（global 書き込みは特に明示・`--autonomous` でも解除されない）。捏造しない。
5. engine の既存フロー不変。`--validate` が新 tier の persona も参照解決対象に含む。README 両言語・version 同期。

## 非スコープ（v1）

- **native `agents/`（subagent_type）の自動生成** — v1 は persona facet（subagent の System に合成）まで。CC ネイティブ agent 化は将来拡張。
- policy / pattern / output-contract のジェネレータ（将来）。
- 複数 product への一括生成。
- 生成 persona の自動品質ゲート（生成物も `--validate` / レビューに乗せられるが、本 spec では「生成＋保存＋使用」に限定）。

## 未決事項（実装前に確定したい）

1. コマンド名：`/rig:persona` ＋ `/rig:knowledge`（推奨・既存 `/rig:*` と整合） vs 1本化 `/rig:gen persona|knowledge`。
2. `--auto` ドメイン知識生成の既定の粒度（1ファイルにまとめる vs トピック別に複数）。
3. 生成 persona を「agent（native）」としても出すオプションを v1 に含めるか。
