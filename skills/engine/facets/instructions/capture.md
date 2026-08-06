# instruction: capture

RUN 完了後、実行から得た**学び**を蒸留して既存のメモリ・知識層に書き戻す手順の正本
（SKILL.md §7 から切り出し）。次回 RUN の知識注入（SKILL.md §5 COMPOSE）が充実し、
システムが回を重ねるごとに賢くなる。

**capture は自動的にはファイルを書き込まない。** 承認ゲート（§7.3）は `--autonomous` でも解除されない。

## 7.1 捕捉対象（WHAT）

以下を「学び」として蒸留する。

| カテゴリ | 例 |
|---|---|
| **落とし穴（pitfall）** | 同じエラーで2回詰まった原因、試みが失敗した理由 |
| **決定記録（decision）** | 設計・実装上の判断とその根拠 |
| **新規約（convention）** | RUN 中に確立した新しいコーディング規約・命名規則 |
| **「2回詰まり」の原因（stuck-twice）** | 詰まりガード（§6）が発動した際の根本原因 |
| **AI 失敗パターン（ai-quirk）** | hallucination、ツール誤用、出力フォーマット崩れ等の再現性のある失敗 |

## 7.2 書き込み先（WHERE）

捕捉した学びは**既存のメモリ・知識層に統合**する。並列に別ストアを作ってはならない。

| 学びの種類 | 書き込み先 | メモ |
|---|---|---|
| **ai-quirk** | `~/.claude/rig/knowledge/ai-quirks/`（user 層） | **記述形＋導出規範形のペアとして保存**（二相。§5 の ai-quirks 二相注入と対応）。記述ファイル（`<name>-descriptive.md`）と規範ファイル（`<name>-policy.md`）を1セットで作成 |
| **プロジェクト・ドメイン学び（pitfall / decision / convention / stuck-twice）** | `<repo>/.claude/rig/knowledge/accumulated/` **および/または** `~/.claude/projects/<proj>/memory/`（`type=project` または `type=knowledge`） | **書き分けルール**：クロスプロジェクトで再利用価値のある学び → memory store（`~/.claude/projects/<proj>/memory/`）に `[[クロスリンク]]` 付きで記録（必要なら ai-quirks にも）。プロジェクト固有のドメイン学び → `<repo>/.claude/rig/knowledge/accumulated/` のみ。**両方に該当する場合のみ両方へ書き込む**（既定は片方への書き込み）。 |
| **MEMORY.md インデックス** | `~/.claude/projects/<proj>/memory/MEMORY.md` | memory store に追記した各ファイルへの**1行ポインタ**を追加する（正準フォーマットは下記・#26） |

> **MEMORY.md 1行ポインタの正準フォーマット（#26）**：`- [<category>] <filename> — <1行サマリ> (<YYYY-MM-DD>)`
> - `<category>`：§7.1 の5値のうち memory store に書くもの（`pitfall` / `decision` / `convention` / `stuck-twice`）。`ai-quirk` は user 層へ書き memory store に記録しないのでポインタ対象外。
> - `<filename>`：memory store 内の相対パス。`<1行サマリ>`：蒸留した学びの1文（§7.4 提案の内容草案から抽出）。`<日付>`：書き込み日（ISO 8601）。
> - 例：`- [pitfall] pitfall-jwt-refresh.md — リフレッシュ後に旧トークンが1秒残る (2026-06-23)`
> - MEMORY.md が無ければ見出し（`## captured learnings`）を作って初期化、あれば末尾に追記。run をまたいで**同一フォーマット**で積む（書式が揺れるとインデックスとして読めなくなる）。

> **accumulated/ ファイルの正準フォーマット（#101）**：`<repo>/.claude/rig/knowledge/accumulated/` に書くファイルは YAML frontmatter + Markdown 本文で構成する。
> ```
> ---
> category: pitfall|decision|convention|stuck-twice
> title: <MEMORY.md ポインタの <1行サマリ> と同一の文字列>
> date: <YYYY-MM-DD>
> ---
> ## 何が起きたか
> （具体的な状況・エラー・決定の経緯）
>
> ## 次回への示唆
> （次回 RUN で同じ状況に陥らないための学び）
> ```
> - `category`：§7.1 の capture カテゴリ（`ai-quirk` は user 層 `ai-quirks/` に書くため対象外）
> - `title`：MEMORY.md ポインタの `<1行サマリ>` と同一文字列にする（インデックスとの一貫性を保つ）
> - `date`：書き込み日（ISO 8601）。MEMORY.md ポインタの `<YYYY-MM-DD>` と同一
> - 本文の「何が起きたか」「次回への示唆」の2セクションは必須。追加セクションは任意。
> - §5 COMPOSE 時に `accumulated/` の各ファイルは frontmatter を除いた Markdown 本文が Knowledge 位置に注入される。

> **役割の区別**（混同しないこと）:
> - **memory store**（`~/.claude/projects/<proj>/memory/`）= 横断的な個人・フィードバック・プロジェクト事実のレコード。永続的なプロジェクト記憶。
> - **knowledge layer**（`rig/knowledge/`）= 次回 RUN の subagent prompt に注入するドメイン記述知識。
> 両者は `[[ファイル名]]` 形式のクロスリンクで参照し合う。一方が他方の代替にはならない。

## 7.3 ゲート（承認必須・サイレント書き込み禁止）

**捕捉は自動的にはファイルを書き込まない。** 以下の手順を厳守する。

1. RUN 完了後、親は蒸留した学びを**提案としてユーザーへ提示**する（書き込み先・ファイル名・内容草案を含む）。
2. ユーザーが**承認する**か、または起動時に `--capture` フラグを明示した場合にのみ、ファイルに書き込む。
3. 承認なしには memory store にも knowledge layer にもいかなるファイルも作成・変更しない。

`--autonomous` が指定された場合でも capture のゲートは解除されない。capture だけは**常に承認が必要**（`--capture` フラグが明示された場合を除く）。

`--capture` 指定時も、書き込む内容と書き込み先（提案）を必ず表示してから書き込み、書き込み後に何を書いたかを必ず報告する。`--capture` は確認ダイアログ（y/n）を省略するだけで、提案表示と事後報告は省略しない。

**`--no-capture` フラグ / `no_capture: true` 設定時（#137）**：RUN 後の capture 提案を**完全にスキップ**する（提案表示・承認ダイアログともに出さない）。`--capture` と `--no-capture` を同時に指定した場合は `--no-capture` 優先とし `[WARN] --capture と --no-capture が同時指定されています（--no-capture 優先）` を出す。`no_capture: true` は recipe の静的設定（毎回抑止）、`--no-capture` はフラグによる実行時抑止と等価であり、どちらが有効でも同じ挙動になる。`hotfix`/`debug` など「学びより速度が優先される軽量 recipe」への利用を想定する。**capture の抑止は学習サイクルを止める**ため、抑止が常態化しないよう軽量 recipe 以外への `no_capture: true` 設定は推奨しない。

## 7.4 提案フォーマット（承認前に提示する内容）

提案は次の形式でユーザーに見せる。

**書き込み先ファイルの実在確認（#45）**：各書き込み先のファイルが既存か否かを実在確認し、結果を提案に反映する。既存の場合は `（既存・上書き <YYYY-MM-DD>）` を付し、既存ファイルの冒頭 1〜2 行（または `title:` frontmatter があればその値）を付記する。新規の場合は `（新規）` またはパスのみ（従来フォーマット互換）。`--capture` フラグ指定時（確認ダイアログ省略）も既存・上書きの旨と既存概要を表示してから書き込む（§7.3「提案表示は省略しない」と同じ考え方）。

```
## capture 提案（承認してください）

## [1] ai-quirk — <quirk の短い名前>
- 書き込み先: ~/.claude/rig/knowledge/ai-quirks/<name>-descriptive.md（既存・上書き 2026-06-20）
               既存の先頭: "# ai-quirk: <name>\n何が起きたか..."
               ~/.claude/rig/knowledge/ai-quirks/<name>-policy.md（新規）
- 内容草案: ...（記述形：何が起きたか / 規範形：次回 prompt に注入するルール）

## [2] pitfall — <落とし穴の短い名前>
- 書き込み先: <repo>/.claude/rig/knowledge/accumulated/<name>.md（新規）
               ~/.claude/projects/<proj>/memory/<name>.md（既存・上書き 2026-06-18）
               既存の先頭: "# pitfall: <name>\n前回の学び..."
               MEMORY.md に1行ポインタ追加
- 内容草案: ...

承認しますか？ [y / 個別に選ぶ / skip]
```

ユーザーが個別選択した場合、選ばれた項目だけを書き込む。

## 7.5 事後レポートフォーマット（書き込み後・#20）

書き込み完了後（`--capture` 時も省略しない・§7.3）、何をどこに書いたかを正準フォーマットで報告する。

```
## capture 完了レポート

書き込み済: <N>件 / スキップ: <M>件

## [1] ai-quirk — <名前> ✓
- ~/.claude/rig/knowledge/ai-quirks/<name>-descriptive.md（新規作成）
- ~/.claude/rig/knowledge/ai-quirks/<name>-policy.md（新規作成）

## [2] pitfall — <名前> ✓
- <repo>/.claude/rig/knowledge/accumulated/<name>.md（新規作成）
- ~/.claude/projects/<proj>/memory/<name>.md（更新）
- MEMORY.md に1行ポインタ追加 ✓

## [3] decision — <名前> — スキップ（ユーザー指示）
```

- 先頭に `書き込み済: N件 / スキップ: M件` のサマリ行。
- 各書き込み項目は カテゴリ・名前・実ファイルパス（新規作成 or 更新）を列挙し末尾に `✓`。ai-quirk は記述形・規範形の2行。
- MEMORY.md ポインタは成否を明示（成功 `✓` / 失敗 `WARN: MEMORY.md 未更新`）。
- スキップ項目（「個別に選ぶ」で除外）は `— スキップ（ユーザー指示）` の1行のみ（草案は再掲しない）。
- 全件スキップなら `書き込み済: 0件 / スキップ: N件` ＋「capture は実施されませんでした」。

