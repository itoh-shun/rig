# rig ドメイン・ハーネス・プラットフォーム v2（taste persona ＋ knowledge-wiki ＋ registry）— design spec

- 日付: 2026-06-22
- ブランチ: `claude/rig-goal-loop-resolution-2nl735`
- 種別: 大型設計（v1 `brick-generators` を内包・拡張）／ engine 改修＋新 pack
- ステータス: **design（未実装）**
- 前提: [`2026-06-22-rig-brick-generators-design.md`](./2026-06-22-rig-brick-generators-design.md)（v1。Phase 1 として内包）

## 背景（解くべき本当の問題）

「バグリストを潰す」系は易しい。難しいのは**プロダクトの審美・ドメイン判断**——「このミッション/ニーズに対してこの作りは妥当か」「極論これ売れるのか」「いや、これはださいが？」を**ドメインに詳しいエージェントがガチャガチャやってから完成させる**こと。これを音楽/映像/ゲーム…と増やすと、**誰がどこで何をしているか把握不能**になる。

知人の結論（＝本 spec の方針）:
1. **ハーネス機構は標準化**し、**ドメイン知識は user-global から注入**可能にする。
2. **知識ストレージは LLM-wiki 構造**にする。さもないと知識が各エージェントの**暗黙知**になり、人間がいま抱える知識サイロ問題を再発明する。
3. 横断を**統合管理**する層が要る。

rig は (1) を既に満たす（ドメイン非依存 engine ＋ pack ＋ tier 化 knowledge 注入）。本 spec は **(2) knowledge-wiki** と **(3) registry**、および審美 persona を足して完成させる。

## 3本柱

### A. 審美判断する domain persona ＋ acceptance-gate（ガチャガチャの正体）

- `/rig:persona` で**判断・声**としての taste reviewer を生成（例 `music-era-90s-taste`）。**事実は持たず wiki を参照**（下記 B の `inject:`）。
- acceptance-gate に**審美の受け入れ基準**を据える（例「`[[genre-house]]` に照らして時代考証が合う」「二番煎じでない」「ミッション/ニーズに対して妥当」）。taste reviewer は `review-verdict` で**具体理由つき REJECT** を返し、ゲートが**基準充足まで収束**＝「ださい→直す→再評価」のガチャガチャが決定的品質で終わる。
- 審美は主観だが、**基準が wiki ページを参照する**ので「ださい」が*文書化された根拠*を持つ（vibes でなく `[[...]]`）。

### B.（本丸）知識を LLM-wiki 化し、暗黙知をエージェントから剥がす

**原則：persona = 判断・声 ／ wiki = 事実。** persona は知識を**埋め込まず参照する**。

#### 置き場（base=global ＋ project overlay）
```
~/.claude/rig/knowledge/wiki/            ← global wiki（一次・全 product 共有）
├── INDEX.md                              派生索引（ページ一覧・タグ・backlink）
├── music-era-90s.md
├── genre-house.md
└── effect-design-conventions.md
<repo>/.claude/rig/knowledge/wiki/        ← project overlay（同 slug を上書き/追補）
```
- 既存の `knowledge/{methodology,domain,accumulated,ai-quirks}/` は維持（後方互換）。`wiki/` は**正準な概念ページ層**で、domain/methodology を相互リンク化した formalize 版。`accumulated/` は capture の受け皿として残し、蒸留して wiki ページへ昇格する。

#### ページ・スキーマ（frontmatter ＋ 本文）
```markdown
---
title: 90年代の音楽
slug: music-era-90s
aliases: [90s, nineties]
tags: [music, era, history]
domain: music
status: canonical          # canonical | draft | deprecated
links: ["[[genre-house]]", "[[genre-grunge]]"]
sources: ["docs/...", "実コード", "外部出典"]
---
本文：この時代の音作りの傾向・不変条件。関連は [[genre-house]] を参照。
```

#### wiki の中核ルール（これが「暗黙知化させない」仕掛け）
1. **1概念=1正準ページ**（single source of truth）。重複は `aliases`/`deprecated` で正準へ寄せる。
2. **相互リンク `[[slug]]`** で知識を**ナビゲート/合成可能**にする。
3. **派生索引 `INDEX.md`**（ページ・タグ・backlink）で発見可能に。索引は**生成物**（手で同期しない＝ドリフトしない）。
4. **global を一次共有**、project はページ単位で上書き/追補（tier 解決をページ単位で）。
5. **explicit（ファイル）**。プロンプトに事実を埋め込まない。

#### persona からの参照（埋め込まない）
```markdown
# persona: music-era-90s-taste
inject: ["[[music-era-90s]]", "[[genre-house]]", "[[effect-design-conventions]]"]
（語り口・審美の判断軸のみ。事実は wiki から注入される）
```
COMPOSE 時に engine が `inject:` の `[[...]]` を**tier 解決**して Knowledge 位置に注入。→ 同じ wiki を映像/ゲーム pack でも共有でき、知識がサイロ化しない。

#### 生成・成長・衛生
- `/rig:knowledge "<説明>"` / `--auto`（repo 解析）で**wiki ページ**を生成（frontmatter＋links 付き・確認必須・捏造禁止）。
- capture（§7）→ `accumulated/` → 蒸留して wiki ページへ（承認制・links/backlink 維持）。
- `--validate` 拡張：**orphan ページ・リンク切れ `[[...]]`・重複/矛盾概念・persona の参照先欠落**を検出（＝正準化・dedup。self-polish の発想を知識層へ）。

### C. 横断レジストリ＝統合管理ハーネス

**registry は手書きせず派生**（global＋project を走査して地図を描く＝ドリフトしない）。

- `/rig:catalog`（または `--list --global`）：**domain（タグ）× pack × persona（どの wiki を inject するか）× wiki ページ × recipe** を全 tier 横断で一覧。「domain=music: personas[music-era-90s-taste,…], wiki[12], pack[vst-plugin]」のように**誰がどこで何をしているかの地図**を出す。
- `--validate --global`：tier 横断で orphan/リンク切れ/重複/参照欠落/pack の登録漏れを検出。
- これが「ハーネス統合管理ハーネス」の実体（**派生ビュー＋横断 doctor**。別ストアを作らない）。

## tier 一覧（全ブリック共通：project overlay > global > shipped、base=global）

| ブリック | global（一次） | project（overlay） | shipped |
|---|---|---|---|
| persona | `~/.claude/rig/personas/` | `<repo>/.claude/rig/personas/` | `skills/rig/facets/personas/` |
| wiki ページ | `~/.claude/rig/knowledge/wiki/` | `<repo>/.claude/rig/knowledge/wiki/` | — |
| recipe / pack | `~/.claude/rig/recipes/` | `<repo>/.claude/rig/recipes/` | `skills/rig/recipes/` |
| registry | 派生（global＋project を走査） | — | — |

## engine 改修（標準化を保ったまま）

1. **persona の tier 解決**（v1）。
2. persona facet の **`inject:` ディレクティブ** → wiki `[[link]]` を COMPOSE で解決・注入。
3. **wiki `[[link]]` 解決**（tier-per-page）＋ backlink/INDEX 派生。
4. **`--validate` 拡張**（wiki 衛生＋ tier 横断）。
5. **`--global` スコープ** と **`/rig:catalog`**。
6. **`--persona <name>` flag**（v1。生成 persona を review に投入）。
7. ジェネレータ `/rig:persona` `/rig:knowledge`（wiki ページを links 付きで書く）。

## 段階リリース（大型なので独立した3 Phase）

- **Phase 1 — 基盤**：persona tier 解決 ＋ `--persona` flag ＋ `/rig:persona`。（≒ v1 spec）
- **Phase 2 — wiki（本丸 B）**：ページ・スキーマ ＋ `[[link]]`/`inject:` 解決 ＋ `/rig:knowledge`(+`--auto`) ＋ `--validate` 衛生。
- **Phase 3 — registry（C）**：`/rig:catalog` ＋ `--validate --global` 横断。

各 Phase は単独で価値が出る形で出す。engine は標準化を維持。

## 具体例：VST プラグイン・ハーネス（端から端まで）
```
pack: vst-plugin（標準 engine 上）
personas（生成）: music-era-90s-taste / genre-house-authenticity / mix-engineer ＋ 標準 security/test
wiki（global 共有）: [[music-era-90s]] [[music-era-00s]] [[genre-house]] [[effect-design-conventions]]
flow:
  implement(DSP) → acceptance-gate{
     taste reviewer が [[genre-house]] を参照→「00年代寄りでださい」REJECT＋具体理由 → 直す → 再評価 …
     合格: 技術(build/test) ＋ 審美(時代考証OK/二番煎じでない/ニーズに妥当) を両立
  } → 完成
```
persona は `[[...]]` を参照するだけなので、同じ wiki を映像/ゲーム pack でも使い回せる。

## 受け入れ基準（v2 全体）

1. persona/wiki/recipe が **global 一次・project overlay** で tier 解決される（project > global > shipped）。
2. persona は wiki を **`inject:` で参照**し、事実を埋め込まない（暗黙知化しない）。
3. wiki は 1概念=1正準ページ・`[[相互リンク]]`・派生 INDEX を持ち、`--validate` で衛生（orphan/リンク切れ/重複/参照欠落）を検出できる。
4. taste persona ＋ acceptance-gate で審美レビューが**基準充足まで収束**する。
5. `/rig:catalog` が全 tier 横断で domain×pack×persona×wiki×recipe の地図を出す。
6. 生成・書き込みは常に確認（global は特に明示・`--autonomous` でも解除されない）・冪等・捏造禁止。
7. engine は標準化を維持（pack/tier 注入で domain を足す。重い DSL/DB を持たない＝wiki/registry は markdown＋派生ビュー）。

## 非スコープ・正直な難所

- **審美は主観**。acceptance-gate は*品質のばらつき*を収束させるが「客観的な良い趣味」を保証はしない（基準で変動を減らすだけ）。
- **クロス domain の概念衝突**（同名・別ニュアンス）→ aliases＋domain タグ＋正準化で緩和するが**キュレーションは要る**。
- **native agent（subagent_type）の自動生成は非スコープ**（persona facet のみ。subagent System に合成）。
- wiki/registry は**ファイル＋規約＋派生ビュー**であり DB/グラフエンジンではない（rig の「重い engine を持たない」を維持）。グラフ機能は派生で出す。

## 未決（実装前に確定したい）
1. Phase 1 から着手でよいか（基盤→wiki→registry の順）。
2. `wiki/` 導入時、既存 `knowledge/{methodology,domain}/` は**当面併存**（後方互換）で良いか。
3. `/rig:catalog` の出力は Markdown レポート（地図）で良いか（別途グラフ可視化は将来）。
