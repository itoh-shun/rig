# technical review（独立・read-only）

- 実施: rig の read-only reviewer agent `rig:docs-reviewer`（tools: Read, Grep, Glob, Bash のみ）
- モデル: 執筆側（Opus）とは別モデル（Sonnet）を指定。別プロセスで起動
- 対象: `articles/01-ai-manager/draft.md`（初稿）
- 判定: **APPROVE**（確信度: 高）

## プロバイダ分離についての注記

rig の既定は「実装を Claude、検証を Codex」のようなクロスプロバイダ検証（README.ja.md:23）だが、
**この実行環境には `codex` も `ollama` も入っていない**（`which codex ollama` が空）。
そのため今回の分離は **provider ではなく model + process レベル**にとどまる。
cross-provider verification を実施したとは記録しない。

## 指摘

blocking / should-fix の指摘は無し。

| # | severity | 箇所 | 問題 | 根拠 |
|---|---|---|---|---|
| — | — | — | 指摘なし | — |

## 検証して問題なかった主張

- `standard` プリセットの一覧（draft の貼り付け）は `python3 scripts/workbench.py gates` の
  実出力と項目・順序ともに完全一致（10項目）
- shipped / Beta の区分が README.ja.md:245-252 の Feature status 表と一致。
  acceptance-gate・isolated worktree・read-only verifier を Stable、
  drill・stats・board を Beta として扱えている
- `--allowedTools Read,Grep,Glob`（README.ja.md:206）、`codex --sandbox read-only`（同）、
  `.rig/audit.jsonl`（README.ja.md:22）、`.rig/gates.json` / `.rig/recipes/`（README.ja.md:172）、
  `/rig:go stats` のゴム印警告（README.ja.md:378-381）はいずれも実在
- `python3 scripts/workbench.py context` の記述は `skills/engine/SKILL.md:387-389` の
  「計測しないもの」の列挙と範囲が一致
- README の Drill Result のスコア（Detection rate 82% 等）を実測値として扱っていない。
  draft はサンプル出力であると明示しており、README 側もそれを実測データとは書いていない
- 速度・品質向上の数値主張は本文に無く、むしろ明示的に否定している
- 因果の過剰主張なし。「rig は AI を賢くしない / 品質を自動的に生まない」は
  README.ja.md:22 の position statement と同義
- 著者の記述は動機・所感にとどまり、具体的な事件・エラー文・数値の断定が無い
- `python3 scripts/workbench.py route --type bugfix --json` を実行し、sources.md の
  裏取り記録と齟齬が無いことを確認

## 反映

指摘が無いため、technical review 起因の本文修正は無し。
ただし本レポートの「プロバイダ分離についての注記」を assurance.json の
`independent_review_done` の但し書きとして残す。

---

# 追加レビュー（改稿で新設した章）

読者レビューの [必須] 指摘を受けて「既存の資産は、どこへ行くのか」の章を書き足したため、
追加分だけを同じ `rig:docs-reviewer`（read-only・別モデル）に再度かけた。

判定: **APPROVE_WITH_CONDITIONS** → 条件を反映して解消済み。

| # | severity | 主張 | 判定 |
|---|---|---|---|
| 1 | — | Claude Code のプリミティブ（slash command / skill / subagent / hook）だけで合成された薄いレイヤー | 正しい（README.ja.md:30） |
| 2 | — | `/rig:init` が CLAUDE.md の "Compact Instructions" 節を書き足す | 正しい（`commands/init.md:20`, README.ja.md:222） |
| 3 | — | persona の tier 解決は project → user → shipped で先勝ち | 正しい（`skills/engine/SKILL.md:326-335`） |
| 4 | — | `--persona <name>` で名指し投入できる | 正しい（`skills/engine/SKILL.md:125,344`） |
| 5 | should-fix | 「手元の Skill は rig のブリック目録とは別の場所にあるまま」 | **否定形が過度に一般化していて誤り** |

## #5 の内容と対応

`skills/engine/SKILL.md:634` が、ホスト組み込みの skill（`/code-review`・`/security-review`・
`/verify` 等）もブリック在庫に含め、セッションが公開していれば rig の対応フローが
**補助レーン**として自動的に使う、と明記している。したがって「rig 側は何もしない」と
読める書き方は誤り。

該当箇所を自分で `sed -n '628,640p' skills/engine/SKILL.md` で確認したうえで、
本文を次の内容に差し替えた。

- rig が手元の Skill を勝手に自分のものにするわけではない
- ただし組み込みの `/code-review`・`/security-review` がセッションに出ていれば補助レーンとして使う
- その票は `native-code-review` のような persona 名で記録され、stats / drill の計測対象になる
- 同じセッションと同じモデルで走るため、独立検証の本体は別のところに残す
- SKILL.md 形式の skill の持ち込みは `/rig:import`（Beta）

差し替え後の記述はすべて `skills/engine/SKILL.md:634` に対応している。
条件は解消したものとして扱う。
