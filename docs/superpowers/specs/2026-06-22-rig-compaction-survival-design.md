# rig 圧縮サバイバル（コンテキスト圧縮で rig 状態を失わない）— design / 実装 spec

- 日付: 2026-06-22
- ブランチ: `claude/rig-goal-loop-resolution-2nl735`
- 種別: engine §6 拡張＋プラグイン同梱フック＋init utility（run-continuity の圧縮境界への延長）

## 課題

コンテキストが大きくなると Claude Code は自動圧縮（`autoCompactEnabled`、既定 ON）を行い、古い tool 出力→会話要約の順に context を畳む。これは **rig 規律にとって最強の中断**で、要約後にハーネス状態（recipe/現 step/gate/受け入れ契約/context-minimal 規律）が落ち、親が静かに red flag（直接実装・ゲート省略）へ逸れる恐れがある。「中断後の不安」を §6 run-continuity で扱ったが、**圧縮境界**は未カバーだった。

## 確認した Claude Code 仕様（設計の前提）

| 機能 | 事実 | 利用 |
|---|---|---|
| 自動圧縮 | `autoCompactEnabled`（既定 true）。**ハーネス制御**、プラグインは置換不可 | rig は置換しない |
| `/compact [指示]` | 手動圧縮、保存指示を渡せる | — |
| `PreCompact` フック | 圧縮直前発火（trigger=manual/auto）。**exit 0 で stdout が“追加の圧縮指示”になる**／exit 2 でブロック | ✅ 本命レバー |
| `SessionStart(source=compact)` | source は存在するが stdout がコンテキストに注入されない**既知バグ** | ⚠️ 当てにしない |
| プラグインのフック同梱 | `hooks/hooks.json` で同梱・自動登録 | ✅ rig は plugin |
| 保存内容の制御 | アルゴリズムは不変だが、PreCompact stdout と CLAUDE.md "Compact Instructions" の2経路で「何を残すか」を誘導できる | ✅ 両経路を使う |

→ 「①圧縮そのもの」は不可、「②③ 圧縮で状態を失わない」は PreCompact フック＋ CLAUDE.md 節で**確実に実装可能**。

## 解決（フル＝フック＋§6＋init雛形）

### ① 保存：PreCompact フック同梱（本命・確実）

- `hooks/hooks.json` — `PreCompact`（matcher `*` で manual/auto 両方）→ `${CLAUDE_PLUGIN_ROOT}/hooks/preserve-rig-state.sh`。
- `hooks/preserve-rig-state.sh` — exit 0 で run-state 保全指示を stdout 出力（追加の圧縮指示として効く）。保全対象＝run-status・残/済 step・受け入れ契約・未解決 REJECT/条件・ゴール/決定・stuck-guard・context-minimal。フックは会話を読めないため**何を残すかの指示**に徹する（live 値は transcript 側にある）。

### ② 復帰：§6 run-continuity に「④ 圧縮境界」節

圧縮直後の最初の作業ターンに **② 再アンカー規則を必ず適用**（ヘッダ再掲＋状態再宣言→現 step に委譲で復帰）。`SessionStart(compact)` 自動再注入は既知バグのため当てにせず、再アンカーで確実に戻す。

### ③ 第2経路：`/rig:init` が CLAUDE.md "Compact Instructions" を scaffold

`/rig:init`（新 utility）が manifest・知識層 dir に加え、CLAUDE.md "Compact Instructions" 節（フックと同じ保全文・毎回自動適用）を**確認の上・冪等に**生成。フックが無い環境でも保全が効く belt-and-suspenders。

## 変更/追加ファイル

```
hooks/hooks.json                              新規・PreCompact 登録
hooks/preserve-rig-state.sh                   新規・保全指示を stdout（実行可能）
skills/rig/SKILL.md                           §6 run-continuity に「④ 圧縮境界」＋§2 に hooks/init 行
commands/init.md                              新規・/rig:init 入口
skills/rig/facets/instructions/init.md        新規・scaffold 手順（manifest/知識層/Compact Instructions）
README.md / README.ja.md                      /rig:init・compaction survival 追記
.claude-plugin/plugin.json                    version 0.7.0 → 0.8.0
```

> rig 初の実行ファイル（`hooks/*.sh`）導入。最小の POSIX sh・静的 echo のみ（外部依存・副作用なし）。

## 受け入れ基準

1. `PreCompact` フックが manual/auto 圧縮の直前に発火し、run-state 保全指示を stdout 出力する（exit 0）。
2. §6 に圧縮境界節があり、圧縮直後の最初の作業ターンで再アンカーが適用される。
3. `/rig:init` が manifest・知識層 dir・CLAUDE.md "Compact Instructions" を確認の上・冪等に scaffold する（既存は上書きしない・`--autonomous` でも書込確認解除されない）。
4. 圧縮そのものは置換せず（ハーネス制御を尊重）、`SessionStart(compact)` の既知バグに依存しない。
5. engine の既存フロー不変。README 両言語・version 同期。

## 非スコープ

- 圧縮アルゴリズム自体の変更（不可）。
- `SessionStart(compact)` での自動再注入（既知バグ。修正されたら復帰の自動化を検討）。
- 親が自分で `/compact` を起動する自動圧縮トリガ（スラッシュコマンドを agent から起動する手段が無い。context-minimal ＋フックで代替）。
