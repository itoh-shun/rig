# sources — 記事内の技術的主張とリポジトリ内の根拠

対象コミット: `babdef6`（`itoh-shun/rig` master 時点で確認）
確認日: 2026-08-22

記事に書いてよいのは shipped（実装済み）のものだけ。README.ja.md §7 Feature status を
shipped / roadmap の判定の正本とする。同節には "Planned" 行が無く、
「表に載っていないコマンドはまだ出荷されていない」と明記されている（README.ja.md:260）。

## 1. 統一入口と自動ルーティング

| 主張 | 根拠 |
|---|---|
| `/rig:go "<自然文タスク>"` が入口 | `commands/go.md`（frontmatter の `argument-hint`）, README.ja.md:63-67 |
| `/rig:rig` は互換エイリアス | README.ja.md:69, `commands/rig.md` |
| 分類 → recipe 選択 → 隔離 worktree → gate → サマリ | README.ja.md:79-116（§4 フロー図） |
| recipe 解決は `rig-wb wb route` の capability authority | `commands/go.md`「② 自然文タスク」節 |
| 実行して確認: `python3 scripts/workbench.py route --type bugfix --json` → `{"recipe": "bugfix", "tier": "core", "worktree": true, ...}` | 本作業中に実行済み |

Status: 自然文タスクルーティング = **Stable**（README.ja.md:243）

## 2. isolated worktree

| 主張 | 根拠 |
|---|---|
| task ごとに専用 worktree と使い捨てブランチを作り、作業ツリーには直接書かない | README.ja.md:120-124 |
| run state は `.rig/runs/<task_id>/`（task.json / steps.json / acceptance.json / diff.md ほか）に残る | README.ja.md:126-137 |
| review など読み取り専用 task は `--no-worktree` で worktree を省略 | README.ja.md:139 |
| discard 後も run log は残る | README.ja.md:210-212 |

Status: **Stable**（README.ja.md:244）

## 3. acceptance-gate（決定的な受け入れ判定）

| 主張 | 根拠 |
|---|---|
| モデルの「完了しました」では完了扱いにならない。failed / pending の gate があれば accept を止める | README.ja.md:159, 200 |
| 基準リストの正本は `scripts/workbench.py gates` | README.ja.md:161 |
| `standard` プリセット = `task_intent_satisfied` / `no_unrelated_diff` / `diff_summary_written` / `risk_summary_written` / `tests_pass_or_explained` / `no_type_errors_or_explained` / `no_secret_leak` / `no_gate_tampering` / `no_injection_markers` / `no_destructive_operation` | README.ja.md:165、および `python3 scripts/workbench.py gates` の実行出力（本作業中に実行し一致を確認） |
| 基準は機械センサーが裏付ける（secret scan / anti-tamper / injection marker / destructive command / OpenAPI schema-diff / prompt-regression / evidence-anchor） | README.ja.md:172 |
| `.rig/gates.json` からの拡張は**加算のみ**。組み込み基準の削除・緩和キーは拒否される | README.ja.md:172 |
| `no_gate_tampering` は `.rig/gates.json`・`.rig/recipes/`・CI workflow の編集を fail-grade で検出 | README.ja.md:172 |
| `prompt_regression_passed` は `--set` による手動上書きを拒否する唯一の基準 | README.ja.md:174 |
| `evidence_anchors_resolve` は **opt-in**。既定プリセットには入っていない | README.ja.md:174 |
| gate は `passed` / `passed_with_warnings` / `failed` / `pending` / `skipped` に集約 | README.ja.md:182 |
| determinism-by-gate = 非決定的な agent 実行を決定的な受け入れゲートで挟む | `skills/engine/SKILL.md:12` |

Status: **Stable**（README.ja.md:245）

注意（記事に書かないこと）: 「gate があるから品質が上がる」という因果。README.ja.md:22 は
「rig は品質を自動的に生むのではなく、あなたが定義した品質基準を AI に無視させず実行するツール」
と書いており、記事もこの範囲を超えない。

## 4. read-only verifier / クロスプロバイダ

| 主張 | 根拠 |
|---|---|
| 実装する AI と検証する AI を分離し、検証側はプロセスレベルで read-only に固定 | README.ja.md:202-206 |
| 具体的な強制方法は `claude --allowedTools Read,Grep,Glob` / `codex --sandbox read-only` | README.ja.md:204 |
| verifier は生成側の自己申告レポートではなく worktree の実際の git diff を一次証拠にする | README.ja.md:204 |
| 生成役と検証役は別プロセスで、provider を選べる（`claude` / `codex` / `ollama` / `lmstudio` / `cmd` / `mock` / ネストした `rig`） | README.ja.md:23 |
| `orchestrate.py probe` が read-only サンドボックスの実発動を provider ごとに確認 | README.ja.md:23, 206 |

Status: **Stable**（README.ja.md:248）

## 5. accept / discard

| 主張 | 根拠 |
|---|---|
| `accept` は `accept_requirements` チェックリストを先に表示する | README.ja.md:208-210 |
| `worktree_exists` / `base_branch_recorded` / `diff_summary_generated` は構造的前提で `--force` でも上書きできない | README.ja.md:20, 210 |
| `--force` が上書きできるのは soft な gate 未達だけで、`.rig/audit.jsonl` に記録が残る | README.ja.md:20 |
| accept は staged（未コミット）で反映し、コミットは常に人が行う | README.ja.md:210 |
| `discard` は task-id 明示と `--yes` 必須 | README.ja.md:210 |

Status: **Stable**（README.ja.md:246）

## 6. reviewer を測る（drill / stats / confidence）

| 主張 | 根拠 |
|---|---|
| `/rig:drill` が既知のバグ class を使い捨て diff に注入し、reviewer には見せない答案キーで採点 | README.ja.md:385-393, `commands/drill.md` |
| 出力は persona 単位の Drill Result（Score / Missed Issues / Recommended Persona Updates） | README.ja.md:393-412 |
| `Recommended Persona Updates` は固定4カテゴリからのみ選ぶ | README.ja.md:414 |
| `--replay` はペルソナ編集後にアーカイブ済み diff へ再実行し verdict 差分を出す | README.ja.md:414, `commands/drill.md` |
| 本物のコードには触れない（すべて使い捨て worktree） | README.ja.md:414 |
| `/rig:go stats` が run 履歴を集計し、reject 0 の reviewer をゴム印候補として警告する | README.ja.md:348-382 |
| `/rig:go confidence` は drill 実測の検出率を補助情報として出し、drill 未実施の persona は「未計測」のまま扱う | `commands/go.md`（サブコマンド表） |
| cockpit は未計測データを空欄でなく "Unmeasured" と明示する | README.ja.md:342 |

Status: reviewer drill = **Beta**、board / stats = **Beta**（README.ja.md:251-252）
→ 記事では「実測できる仕組みがある」までにとどめ、**数値・検出率の実績は書かない**。
README.ja.md:393-412 のスコア例は説明用のサンプル出力であり、実測値ではない。

注意（記事に書かないこと）: このリポジトリは現時点で drill / stats の数値を自動公開する
CI を持たない。README.ja.md:425 が「本リリースでは未実装」と明記している。

## 7. context の構成と計測

| 主張 | 根拠 |
|---|---|
| ブリック（persona / instruction / pattern / recipe）を起動時に合成してタスク専用ハーネスを作る | `skills/engine/SKILL.md:11`, README.ja.md:264 |
| PARSE → RESOLVE → COMPOSE → RUN の4段 | `skills/engine/SKILL.md:11` |
| context-minimal はハードルール。実作業は subagent に dispatch し、親は dispatch と集約と gate 判断だけ | `skills/engine/SKILL.md:381-385` |
| persona は `inject: [[slug]]` で wiki ページを参照する | `skills/engine/SKILL.md`（knowledge facet 行）, `commands/knowledge.md` |
| `/rig:go context` は rig が親セッションへ印字した stdout を invocation 単位で `.rig/context.jsonl` に記録して集計する | `commands/go.md`（サブコマンド表）, `skills/engine/SKILL.md:387` |
| 計測**しない**もの（セッション全体の context・会話・親が自分で読んだファイル・親が本当に dispatch したか）はレポート自身に明記される | `skills/engine/SKILL.md:389` |
| 実行して確認: `python3 scripts/workbench.py context` → "No records yet. …（`.rig/context.jsonl`）" | 本作業中に実行済み |

注意（記事に書かないこと）: 「rig を使うと context 消費が減る」。計測対象は
rig 自身の stdout だけで、セッション全体ではない（`skills/engine/SKILL.md:389`）。

## 8. 文章側のブリック（この記事の作成に使ったもの）

| 主張 | 根拠 |
|---|---|
| `facets/personas/styles/qiita-tech-writer` は語り口だけを担い、事実を足さない | `skills/engine/facets/personas/styles/qiita-tech-writer.md` |
| `japanese-writing` は opt-in の domain pack（`rig-wb pack install domain:japanese-writing --scope project --allow-unverified`） | `skills/engine/SKILL.md`（Extension Catalog）, `packs/domain/japanese-writing/` |
| `de-ai-smell` recipe は shipped（`scope: shipped`）で、`/rig:dev --recipe de-ai-smell` から起動する | `skills/engine/recipes/de-ai-smell.md`（frontmatter） |
| 徴候カタログは `facets/knowledge/ai-writing-smells`。表層 A〜I / 深層 J〜P,V / 日本語固有 Q〜U | `skills/engine/facets/knowledge/ai-writing-smells.md` |
| 表層マーカー主軸は脆いという実測（記号を消すと弁別力が落ちる）がカタログに記録されている | 同上（対比コーパスによる第2の独立実測 節） |

## 9. コマンド実在確認（commands_verified）

本作業中に実行し、出力を確認したもの:

- `python3 scripts/workbench.py --help`
- `python3 scripts/workbench.py gates`
- `python3 scripts/workbench.py route --type bugfix --json`
- `python3 scripts/workbench.py context`
- `python3 scripts/workbench.py context --help`

ファイルの存在を確認したスラッシュコマンド: `commands/go.md`（`/rig:go`）,
`commands/rig.md`（`/rig:rig`）, `commands/drill.md`（`/rig:drill`）,
`commands/dev.md`（`/rig:dev`）, `commands/talk.md`（`/rig:talk`）。

記事に書く実行例は、上記で確認できたものだけにする。

## 10. 既存資産と rig の関係（改稿で追加した章の根拠）

| 主張 | 根拠 |
|---|---|
| rig は Claude Code 自身のプリミティブ（slash command / skill / subagent / hook）だけで合成された薄いレイヤーで、別ツールへの乗り換えを要求しない | README.ja.md:30 |
| `/rig:init` は CLAUDE.md の "Compact Instructions" 節を scaffold する | README.ja.md:434, `skills/engine/SKILL.md`（init pack 行）, `commands/init.md` |
| persona facet の tier 解決は project → user → shipped で、先に見つかった tier が優先 | `skills/engine/SKILL.md:326`, 同 207（recipe の同順序）, 同 341, 350 |
| `--persona <name>` で reviewer を名指しで review に投入できる | `commands/persona.md`（frontmatter description）, `skills/engine/SKILL.md:350` |
| `/rig:import` は外部 skill の SKILL.md を rig ブリックへ翻訳し、出所とハッシュを `skills-lock.json` に記録する | `commands/import.md`（frontmatter description）, リポジトリ直下の `skills-lock.json` の実在 |
| ホスト組み込みの skill（`/code-review`・`/security-review`・`/verify` 等）もブリック在庫に含め、公開されていれば rig の対応フローが**補助レーン**として使う | `skills/engine/SKILL.md:634` |
| そのとき票は `native-code-review` のような persona 名で記録され、stats / drill の計測対象になる（測れないレビュアーを使わない） | `skills/engine/SKILL.md:634` |
| ネイティブ skill は独立検証の代替にしない。セッションと同じモデルで走るため、主クォーラムは persona / クロスプロバイダ側に残す | `skills/engine/SKILL.md:634` |
| import / persona / catalog は Beta | README.ja.md:253（knowledge import/export/persona/catalog/forge = Beta） |

注意: 記事は `/rig:import` を Beta と明記している。persona も同じ Beta 行に含まれるが、
本文で触れているのは tier 解決と `--persona <name>` という engine 側の解決規則
（`skills/engine/SKILL.md:326,344,350`）であり、生成コマンド `/rig:persona` の機能ではない。

改稿の経緯: 初稿の改稿版では「手元の Skill は rig のブリック目録とは別の場所にあるまま」と
書いていたが、`rig:docs-reviewer` の再照合で `skills/engine/SKILL.md:634` が
「ホスト組み込みの skill も在庫に含め、公開されていれば補助レーンとして使う」と
明記していることが判明したため、この否定形は誤りとして撤回し、上記の3行に差し替えた。
