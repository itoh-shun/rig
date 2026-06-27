# instruction: skill-author

説明文から **rig のブリック／パックを自作**する。「こういうフロー／レビュー観点／モードが欲しい」を受け取り、rig 規約に沿った brick（recipe / instruction / persona / output-contract / knowledge / command）を生成し、検証して保存する＝**rig が自分自身を拡張する**メタ能力。Superpowers の `writing-skills` 相当。**書き込みは影響あるアクションなので必ず提案→確認→書き込み**（`--autonomous` でも生成物の書き込み確認は解除しない）。起草は subagent に dispatch し、親は長文を抱えない（context-minimal）。

> 原則：**engine 不変・pack 上乗せ**。新しい制御機構を発明しない＝既存の pattern（acceptance-gate / review-gate / parallel-fanout / autonomous-loop）と facet 型を組むだけで成立させる。これが守れないなら、それは brick でなく engine の改造＝別議論。

## 入力

- 自由記述（例「コミットメッセージを規約準拠に直すフロー」「アクセシビリティ専門のレビュアー」「短歌を評価するモード」）。
- `--name <id>`（任意）：pack/recipe 名。省略時は説明から slug を提案（英小文字・ハイフン）。
- `--type <recipe|persona|knowledge|pack>`（任意）：作るものを明示。省略時は説明から判定（下記）。
- `--user`：global（user 層）に保存。既定は project（product 単位）。rig リポジトリ自身で作業中なら `--shipped` で同梱 tier に出す（§2 目録更新＋`scripts/validate.py`）。

## ① 何を作るか判定（brick の役割を知る）

rig のブリックは役割で分かれる。説明をこの型に割り当てる：

| 欲しいもの | 作る brick | 委譲 |
|---|---|---|
| レビュー観点・人格 | persona | **`/rig:persona` に委譲**（既存ジェネレータ） |
| ドメイン知識・観点カタログ | knowledge | **`/rig:knowledge` に委譲**（LLM-wiki） |
| 新しいフロー／モード（複数 step） | recipe ＋ 必要な instruction/output-contract | 本手順 |
| 入口コマンド | command（`commands/<name>.md`） | 本手順 |
| まとまった機能一式（pack） | 上記の組（persona＋knowledge＋instruction＋recipe＋output-contract＋command） | 本手順＋persona/knowledge は各ジェネレータへ |

**pack の定石**（このセッションの全 pack が踏襲）：**persona＝判断／knowledge＝観点カタログ（事実）／instruction＝routing（Native-first・作法は persona/knowledge に委譲し再掲しない）／recipe＝step の束（gate つき）／output-contract＝出力フォーマット／command＝入口**。

## ② 起草（subagent・既存 brick を雛形に）

- **既存の同型 brick を読んで形式を真似る**（recipe は §3.5 スキーマ、persona/instruction/output-contract は各 facet の見出し規約）。ゼロから様式を発明しない。
- recipe frontmatter は §3.5 準拠（`name`/`description`/`scope`/`steps`/`autonomy` 必須、step は `id`/`instruction`＋任意 `personas`/`policies`/`gate`/`acceptance`/`output_contract`/`checks`/`needs`）。
- **品質ゲートを必ず仕込む**：判定を伴う recipe は `gate: acceptance-gate`＋`acceptance[]`、レビュー系は `review-gate`／`parallel-fanout`。determinism-by-gate を外さない。
- **instruction は薄く**（Native-first）：作法は persona/knowledge に置き、instruction は「どの brick をどう繋ぐか」の routing に徹する。
- **空ワード・捏造禁止**：description は具体で。存在しない pattern/facet を参照しない。

## ③ 保存先（tier・§5 と整合）

| スコープ | パス |
|---|---|
| project（既定） | `<repo>/.claude/rig/{recipes,personas,instructions,output-contracts,knowledge}/<name>.md`・command は `<repo>/.claude/commands/`（または案内） |
| user（`--user`） | `~/.claude/rig/...` |
| shipped（`--shipped`・rig 本体作業時のみ） | `skills/rig/{recipes,facets/...}`・`commands/`・**SKILL.md §2 目録に1行追加** |

## ④ 検証（自己拡張は検証込みで完結）

- **参照解決を検証する**：rig 本体作業時は `python3 scripts/validate.py`、それ以外は rig の `--validate`（doctor）で、recipe→facet 参照切れ・frontmatter スキーマ逸脱が無いか確認。**FAIL があれば直してから完了**（壊れた brick を残さない）。
- 生成物が呼べることを確認：`--list`／`/rig:catalog` に出る（project/user）か、SKILL §2 に載る（shipped）。

## ⑤ 報告

何を作ったか（brick 名・tier・パス）と、**どう呼ぶか**（`/rig:dev --recipe <name>` や新 command）を短く示す。pack なら「次に persona を足すなら `/rig:persona`」等の育て方も。

## ガード

- **engine を改造しない**。新 pattern が要ると感じたら、それは既存 pattern の組み合わせで代替できないかをまず疑う（大半は acceptance-gate＋parallel-fanout＋委譲で足りる）。
- **書き込みは確認必須・冪等**（既存 brick を黙って上書きしない＝差分を見せる）。
- **検証を通してから完了**（参照切れ・スキーマ逸脱ゼロ）。捏造 brick を作らない。
- persona/knowledge は**専用ジェネレータへ委譲**（二重実装しない）。
- context-minimal：起草・検証は subagent、親は要点だけ。
