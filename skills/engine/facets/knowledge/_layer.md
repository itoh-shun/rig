# knowledge: _layer

知識層の構造・ディレクトリ規約・注入ルールを記すリファレンス。

## 概要

知識層は **DESCRIPTIVE（記述的）** な知識の蓄積場所。「事実・観察・構造」を記述し、そこから導出される規範（「〜せよ」）は **Policy facet** として分離する。

COMPOSE フェーズが関連する知識ブリックを選択し、subagent prompt に注入することで、各 subagent は毎回ゼロから文脈を学ばずに済む。

---

## 2 階層構造

### User 層（ユーザー横断）

場所: `~/.claude/rig/knowledge/`

プロジェクトをまたいで共通して有効な知識を置く。

| カテゴリ | ディレクトリ | 内容 |
|---|---|---|
| **methodology** | `methodology/` | DDD / クリーンアーキテクチャ / SOLID / TDD 原則など、設計・開発手法の記述 |
| **ai-quirks** | `ai-quirks/` | AI（Claude）の既知の嘘・誤動作・失敗パターン。記述形と導出された規範形のペアで管理（後述） |

### Project 層（プロジェクト固有）

場所: `<repo>/.claude/rig/knowledge/`

そのリポジトリ固有の知識を置く。

| カテゴリ | ディレクトリ | 内容 |
|---|---|---|
| **domain** | `domain/` | ドメイン設計（ユビキタス言語 / 認証モデル / アーキテクチャ / ADR ポインタなど） |
| **accumulated** | `accumulated/` | フロー実行中に学習・蓄積された知識（実行履歴から抽出したパターン、過去の失敗知識など） |

---

## ai-quirks の二相管理

ai-quirks カテゴリは他のカテゴリと異なり、**記述形**と**導出規範形**の2つの形式を1つのエントリとして管理する。

### 記述形（Knowledge）
「AI がどのような誤動作をするか」を観察として記述する事実の陳述。  
→ COMPOSE 時に subagent prompt の **User 先頭**（Knowledge 位置）へ注入する。

### 導出規範形（derived Policy）
記述形から導かれる「だから〜せよ」という禁止・義務の命令文。  
→ COMPOSE 時に subagent prompt の **User 末尾**（Policy 位置、recency 効果を得る）に注入する。

この二相分離により、「何が起きるか（知識）」と「何をすべきか（規範）」を明確に分けて管理する。

### エントリ例（ai-quirks 書き方）

```markdown
## [quirk-id] 短いタイトル

**知識（記述形）**: Claude は ○○ という誤動作をすることがある。〔観察事実〕

**Policy（規範形）**: だから、〜せよ。〔行動義務〕
```

---

## 既存の知識 facet（shipped）

shipped の知識 facet は `facets/knowledge/` 以下に配置され、オーケストレーター自身の動作に関する汎用知識を提供している。

| facet | 内容 |
|---|---|
| `orchestration-patterns` | 制御フロー選択マトリクス・recipe 化指針・軽さ優先原則 |
| `harness-engineering` | ハーネス合成の工学的原則 |

これらは知識層の外部ディレクトリ（`~/.claude/rig/knowledge/` 等）とは別に、plugin に同梱され常時ロード可能な shipped facet である。user 層・project 層の知識は外部ファイルとして管理され、COMPOSE 時に動的に選択・注入される。

---

## ディレクトリが存在しない場合

user 層・project 層のいずれかまたは両方のディレクトリが存在しない場合は、**サイレントにスキップ**する。知識注入なしで通常通り COMPOSE を継続する。

---

## COMPOSE の注入規則（SKILL.md §5 の正本）

subagent prompt を組む前に、以下の順で関連する知識ブリックを選択し、facet 配置順（Persona=System /
Knowledge=User 先頭 / Instruction=User 中部 / Output Contract=User 構造部 / Policy=User 末尾）に沿って
注入する。

**選択対象（tier 順）:**

| tier | パス | カテゴリ |
|---|---|---|
| **user 層** | `~/.claude/rig/knowledge/methodology/` | 設計・開発手法（DDD / クリーンアーキテクチャ / SOLID 等） |
| **user 層** | `~/.claude/rig/knowledge/ai-quirks/` | AI の既知失敗パターン（二相管理、下記参照） |
| **project 層** | `<repo>/.claude/rig/knowledge/domain/` | ドメイン設計・ユビキタス言語・認証モデル・ADR |
| **project 層** | `<repo>/.claude/rig/knowledge/accumulated/` | 蓄積知識（実行履歴から抽出されたパターン・学び）→ User 先頭（Knowledge 位置）に注入 |
| **wiki（user＝global 一次）** | `~/.claude/rig/knowledge/wiki/` | 正準な概念ページ（相互リンク `[[slug]]`）。persona の `inject:` / `[[link]]` で参照 |
| **wiki（project＝overlay）** | `<repo>/.claude/rig/knowledge/wiki/` | 同 slug を上書き/追補（ページ単位で project 優先） |

いずれかの tier ディレクトリが存在しない場合は**サイレントにスキップ**する（エラーにしない）。

**wiki ページの参照と注入（`facets/knowledge/_wiki` 参照）:**

- persona facet が `inject: ["[[slug]]", …]` を宣言している場合、各 `[[slug]]` を **tier 解決**（project overlay > global > shipped `skills/engine/facets/knowledge/wiki/`）してページを取得し、**User 先頭（Knowledge 位置）に注入**する（1ホップ既定・過剰展開しない）。
- 本文中の `[[slug]]` も同様に解決対象。`[[slug|表示名]]` 記法可。解決できない `[[...]]` は**注入せず**、`--validate` がリンク切れとして報告する。
- wiki は「事実」、persona は「判断・声」。**persona は事実を埋め込まず wiki を参照する**（暗黙知サイロを避ける）。

**注入位置:**

- **methodology / domain** の知識ブリック → subagent prompt の **User 先頭**（Knowledge 位置）に注入する。
- **ai-quirks** は**二相注入**する：
  1. **記述形（知識）** → User 先頭の Knowledge 位置（他の知識ブリックと同列）に注入。
  2. **導出規範形（derived Policy）** → User 末尾の Policy 位置（recency が効く末尾）に注入。Policy facet（`facets/policies/`）と同じ位置に配置する。


