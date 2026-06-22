# rig run-continuity（中断後も rig が駆動中だと分かる）— design / 実装 spec

- 日付: 2026-06-21
- ブランチ: `claude/rig-goal-loop-resolution-2nl735`
- 種別: **engine（SKILL.md §6 RUN）への規律追加**（pack ではない・engine 本体を改変する）

## 課題

rig で開発をスタートし、**途中で質疑（ユーザーの質問・相談・脱線）**が挟まると、その後「ちゃんと rig が使われているか」が不安になる。

### 根本原因

rig の RUN 規律（context-minimal / 各 step ゲート / 実作業は subagent へ dispatch）は、SKILL.md の指示が**プロンプトの recency に効いている間**だけ強く保たれる。質疑応答ターンが入ると recency を質疑側が奪い、オーケストレータが **§6 の red flag そのもの**（親が直接コードを書き始める・ゲートを飛ばす）へ**静かに**陥りやすい。さらに**それが画面に出ない**ため、ユーザーは「今 rig が駆動中か／素の Claude に戻ったか」を**見分けられない**。

= rig には「**中断後に自分がハーネス状態へ戻る再アンカー機構**」と「**rig 駆動中だと一目で分かる可視マーカー**」が無い。red flag の定義はあるが、検出も自己復帰もユーザーに見えない。

## 解決（3点・engine §6 に薄く足す）

opt-in でなく**常時 ON の RUN 規律**として SKILL.md §6 に追加する（軽さ既定を壊さないよう、出力は1行ヘッダ＋ step 境界のみに限定）。

### ① 可視 run-status ヘッダ

RUN がアクティブな**各ターンの冒頭**に、現在のハーネス状態を1行で再掲する。

```
▸ rig | recipe: <name|ad-hoc> | step: <id> (<n>/<N>) | gate: <none|pending|passed|REJECT> | backend: <manual|workflow> | mode: <gated|autonomous>
```

- `recipe` は `--recipe`/manifest 由来名。対話合成なら `ad-hoc`。
- `step` は現在 step の id と位置（`(n/N)`）。`--only`/`--from` スライス時はスライス後の N。
- `gate` は現 step のゲート状態（`none`/`pending`/`passed`/`REJECT`）。
- これにより「**rig が今ここを駆動中**」が常に見える＝不安が消える。

### ② 再アンカー規則

質疑・脱線で**1ターン抜けた直後の作業ターン**では、作業に入る前に必ず：

1. ① の run-status ヘッダを**再掲**する。
2. アクティブなハーネス状態を**1行で再宣言**する（どの recipe のどの step を、どの委譲先で再開するか）。
3. **現 step から再開**する。**素の直接作業・ゲート省略へ静かに切り替えない**。

既存 red flag「親が直接コードを書き始める」を**「中断後に素の作業へ戻る」場合へ明示適用**する。

### ③ step 境界バナー

step の**開始 / 委譲 / ゲート / 完了**で、認識可能な印を1行出す。subagent dispatch とゲートが実際に起きていることを可視化する。

```
── step <id> ▸ dispatch → <agent|subagent>        # 委譲した
── step <id> ▸ gate: <acceptance-gate|review-gate> [<pending→passed|REJECT>]
── step <id> ▸ done                                # 完了・次へ
```

## スコープ・非スコープ

- **対象**: 合成ハーネスの RUN（dev / sales / goal、および workflow バックエンド）。
- **会話モード（talk）**: talk 自身の地の会話ターンにはヘッダを出さない（短い話し言葉を保つ）。talk が委譲した先のフローが RUN に入ったら、その RUN に①〜③が適用される。
- **軽さ既定を壊さない**: 出力は1行ヘッダ＋ step 境界のみ。長い再掲・冗長な進捗ログはしない（context-minimal と矛盾させない）。
- **非スコープ**: 永続的な run-state ファイル化／別ストアでの進捗管理（並列ストア禁止の原則に反する）。状態は会話 context 内で保持し、ヘッダで都度可視化するだけ。

## 変更ファイル

```
skills/rig/SKILL.md   §6 RUN に「run-continuity（可視マーカー＋再アンカー）」小節を追加。
                      red flags に1項・§9.1 rationalization 表に1行・§10 参照表に1行追記。
README.md / README.ja.md   「なぜ rig / 設計の勘所」に run-continuity を1点追記。
.claude-plugin/plugin.json version 0.5.0 → 0.6.0。
```

> ブリック（pattern）化はしない。run-continuity は step に合成する制御フローではなく、オーケストレータ全体に常時かかる RUN 規律のため、§6 engine 規律として置く（§2 在庫には増やさない）。

## 受け入れ基準

1. RUN 中は各ターン冒頭に run-status ヘッダが1行出て、recipe / 現 step / gate / mode が分かる。
2. 質疑・脱線の直後の作業ターンは、ヘッダ再掲＋ハーネス状態の再宣言をしてから現 step に戻る。素の直接作業へ静かに切り替えない。
3. step の開始/委譲/ゲート/完了で境界バナーが出て、subagent dispatch とゲートが可視化される。
4. talk の地の会話にはヘッダを出さない（短い話し言葉を維持）。委譲先 RUN には適用される。
5. 出力増は1行ヘッダ＋ step 境界に限定し、軽さ既定・context-minimal を壊さない。
