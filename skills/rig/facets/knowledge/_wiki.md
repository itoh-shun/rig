# knowledge: _wiki

知識を **LLM-wiki**（相互リンクされた正準ページ群）として持つための構造・規約リファレンス。v2 Phase 2。

## なぜ wiki か

知識を persona のプロンプトに**埋め込む**と、各エージェントの**暗黙知**になって共有されず、人間が抱える知識サイロ問題を再発明する。これを防ぐため **「persona = 判断・声 / wiki = 事実」** を分離し、persona は事実を**参照**する（埋め込まない）。

## 置き場（base=global ＋ project overlay）

| tier | パス | 役割 |
|---|---|---|
| **user（global・一次）** | `~/.claude/rig/knowledge/wiki/` | 全プロジェクト共有の正準ページ |
| **project（overlay）** | `<repo>/.claude/rig/knowledge/wiki/` | 同 slug を上書き/追補（プロジェクト固有の差分） |
| **shipped（同梱・最低優先）** | `skills/rig/facets/knowledge/wiki/` | plugin 同梱の正準ページ。shipped persona の `inject:` 先＝**rig 自身が wiki 分離を dogfooding する層**。user/project の同 slug で上書き可 |

- 解決は **project overlay > global > shipped**（ページ単位＝同 slug があれば上位 tier 優先）。
- 既存の `knowledge/{methodology,domain,accumulated,ai-quirks}/` は維持（後方互換）。`wiki/` は正準な概念ページ層。
- ディレクトリが無ければ**サイレントにスキップ**。

## ページ・スキーマ（frontmatter ＋ 本文）

```markdown
---
title: 90年代の音楽
slug: music-era-90s          # ファイル名と一致（<slug>.md）
aliases: [90s, nineties]     # 別名（重複の正準化に使う）
tags: [music, era, history]
domain: music                # 横断レジストリ(C)の分類キー
status: canonical            # canonical | draft | deprecated
links: ["[[genre-house]]", "[[genre-grunge]]"]   # 関連ページ
sources: ["docs/...", "実コード", "外部出典"]      # 根拠（捏造禁止）
---
本文：この時代の音作りの傾向・不変条件。関連は [[genre-house]] を参照。
```

## 中核ルール（暗黙知化させない仕掛け）

1. **1概念 = 1正準ページ**（single source of truth）。重複は `aliases` で正準へ寄せ、古いものは `status: deprecated`。
2. **相互リンク `[[slug]]`** で知識をナビゲート/合成可能にする。
3. **派生索引 `INDEX.md`**（ページ一覧・タグ・backlink）で発見可能に。索引は**生成物**（手で同期しない＝ドリフトしない）。
4. **explicit（ファイル）**。事実をプロンプトに埋め込まない。
5. **捏造禁止**。`sources` に根拠を残す。

## persona からの参照（埋め込まない）

persona facet は frontmatter（または本文冒頭）に `inject:` で参照ページを宣言する。

```markdown
# persona: music-era-90s-taste
inject: ["[[music-era-90s]]", "[[genre-house]]", "[[effect-design-conventions]]"]

（語り口・審美の判断軸のみ。事実は wiki から注入される）
```

COMPOSE 時に engine が `inject:` の `[[...]]` を **tier 解決**（project overlay > global）して **Knowledge 位置**（User 先頭）へ注入する。リンク先が見つからなければ警告（`--validate` が検出）。

## `[[link]]` の解決

- `[[slug]]` または `[[slug|表示名]]`。`slug` を tier 解決でファイルに対応づける。
- 注入は**1ホップ**を既定とする（過剰展開を避ける）。深い依存は `links:` を辿る必要があるときだけ。
- 解決できない `[[...]]` は注入せず、`--validate` でリンク切れとして報告。

## 衛生（`--validate` が点検）

- **orphan**：どこからも `[[リンク]]` / `inject:` されないページ。
- **リンク切れ**：存在しない slug への `[[...]]`。
- **重複/矛盾**：同義（同 title/alias）で別 slug の正準ページが複数。
- **参照欠落**：persona の `inject:` 先が無い。
- **INDEX ドリフト**：実ファイルと `INDEX.md` の乖離。

## 成長（capture との連携）

RUN 後の capture（§7）は `accumulated/` に学びを落とし、承認の上で**蒸留して wiki ページへ昇格**する（`links`/backlink を維持）。これにより wiki が回を重ねるごとに充実する。
