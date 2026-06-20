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
