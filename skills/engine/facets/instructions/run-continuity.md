# instruction: run-continuity

SKILL.md §6 run-continuity の**詳細仕様の正本**。run-status ヘッダの各フィールドの意味と出現条件、
acceptance-gate の criterion 単位表示、step ゲートと2つの独立カウンタ（stuck-guard / acceptance-gate K 超）の
エスカレーション正準フォーマット。

**SKILL.md §6 に残っているのは常時効く不変条件**（毎ターンのヘッダ再掲・再アンカー・step 境界バナー・
圧縮境界・red flags）。**このファイルは「ヘッダに何を書くか」「詰まったときに何を出すか」を確定する**——
ヘッダのフィールドを組み立てるとき、およびエスカレーションを出すときは必ずこれを読んで従う。

## 1. run-status ヘッダのフィールド

```
▸ rig | recipe: <name[tier]|ad-hoc> | step: <id> (<n>/<N>) | gate: <none|pending [(try N/K)]|passed|REJECT> [| stuck: N/2] | backend: <manual|workflow> [| orch: <on|auto>] | mode: <gated|autonomous> [| iter: X/N]
```


- `recipe`：`--recipe`/manifest 由来名。対話合成なら `ad-hoc`。**tier 表示ルールは `--plan`（#25）と統一する（#125）**：`project`/`user` tier の recipe は `recipe: <name> [project]` / `recipe: <name> [user]` と明示、`shipped` のみは省略可（`recipe: <name>` のまま——新規ユーザーへの静かな既定）、対話合成は tier なし（`recipe: ad-hoc`）。これにより `--plan`（事前）→ run-status（実行中）の全フェーズで tier 情報が追跡可能になる。`step`：現 step の id と位置（`--only`/`--from` スライス時はスライス後の N）。`gate`：現 step のゲート状態。
- **`gate: pending` の acceptance-gate 試行位置（#32）**：`gate: acceptance-gate` の step が収束ループ中（基準未達で retry に入った）は `pending (try N/K)` と試行回数を付す（`K` は当該 step の `max_retries`・RESOLVE 確定値で `--plan` の `（max_retries: N）` と同じ出所）。`step: (n/N)` が「全フロー中の位置」を示すのと対称に、`(try N/K)` は「この step 内の収束ループの位置」を示す。**初回実行（まだ retry に入っていない 0 回目）は `(try …)` を付けない**（素の `pending`。retry 1 回目から `(try 1/K)`）。`K 超`で `## rig acceptance-gate: K 超エスカレーション`（§6）へ。`gate: none|passed|REJECT` は確定状態のため `(try …)` を付けない（既存表記を維持）。
- **`orch:` フィールド（計算的オーケストレーション）**：この RUN が orchestrate を通るときだけ `backend:` の直後に付す＝**明示時 `orch: on` / 自動有効化時 `orch: auto`**（§4.3：recipe の `checks:`/`needs:` か manifest `default_orchestrate`）。オフ（従来の散文エンジン）なら**省略**（ヘッダ長を増やさない）。これで「今このフローは舵をコードが握っているか」が毎ターン一目で分かる。
- **自動有効化の一言通知**：orchestrate が**自動で**ON になった最初のターンに、run-status の直後へ1行で理由と戻し方を示す＝`🧭 計算的オーケストレーションで回します（理由: <recipe に needs 宣言 | recipe に checks 宣言 | manifest default_orchestrate>）。対話的な散文エンジンに戻すには --no-orchestrate。` 明示 `--orchestrate` 時は既に意図的なので通知しない。
- **`stuck: N/2` フィールド（#117）**：stuck-guard カウンタ（§6「step ゲートと詰まりガード」）が **1 以上**になったとき、`mode:` フィールドの直前に `| stuck: N/2` を追加する（`2` は stuck-guard の固定上限）。カウンタ = 0 のとき（通常時）は**省略する**（ヘッダ長を増やさない）。カウンタが #36 規則でリセットされたら `stuck:` フィールドも消える。`acceptance-gate` の `(try N/K)` が「収束ループの深さ」を示すのと対称に、`stuck: N/2` は「同一エラー反復の深さ」を示す（2つの独立カウンタが両方可視化される）。例：`gate: pending (try 1/2) | stuck: 1/2` は「acceptance-gate も stuck-guard も次でエスカレーション直前」を一目で示す。
- **`iter:` フィールド（`loop` レシピ専用・#176）**：`loop` レシピ（`facets/instructions/loop-driver` 経由）の RUN 中のみ、`mode:` フィールドの後に付す（他のレシピでは**省略**）。各 tick 開始時に更新する。フォーマットはループ設定によって変わる：`--times N` 指定時は `iter: X/N`（X = 現在の実行回数。例: `iter: 3/5`）、`--until <condition>` 単独時（回数上限なし）は `iter: X`（分母なし。例: `iter: 3`）、`--times N` + `--until` 併用時は `iter: X/N (監視中)`（例: `iter: 3/5 (監視中)`）。`--plan` の `### Loop Config:` ブロック（§5）が「予定」を示すのと対称に、`iter:` は「実行中の現在 tick」を示す。コンテキスト圧縮後の再開時（② 再アンカー）も `iter:` フィールドを含めて run-status ヘッダを再掲する（`loop-driver.md` ④「次 tick 予約の正準状態に経過 tick を含める」と対応し、圧縮をまたいでも tick 数が失われない）。
- これにより「**rig が今ここを駆動中**」と「次でエスカレーションが来るか」が常に可視化される。


## 2. acceptance-gate criterion 単位の合否表示

**acceptance-gate criterion 単位の合否表示（#159）**：`gate: acceptance-gate` を持つ step で基準が未達（`pending`）のとき、step 境界バナーの直下に各 criterion の合否（`✓`/`✗`）と未達の簡潔な根拠（1行以内、サブエージェントの structured-report から抽出したサマリ）を追記する。合格（`passed`）時は1行バナーのみ維持する（全件 ✓ のため列挙を省略し冗長を避ける）。`acceptance[]` が空配列の step では `（基準未設定 — WARN: ゲートが常時通過）` のみ表示する（`--validate ③` WARN と同義）。`--autonomous` 時も同様に表示する（オーケストレーターが状態を把握できるように）。

```
── step verify ▸ gate: acceptance-gate pending (try 1/2)
   ✓ build が成功
   ✗ lint 0 件 （3 errors found）
   ✓ 全テストが green
   → lint エラーを修正して再試行
── step verify ▸ done
```


## 3. step ゲートと詰まりガード（2つの独立カウンタ）

- `--autonomous` でない限り、各 step 後に結果を提示し**次へ進む確認**を取る（step ゲート）。
- **同じ所で2回詰まったら**（同じエラー・同じレビュー REJECT を2巡）勝手に試行を続けず、**正準フォーマットで user に判断を仰ぐ（#12）**：

```
## rig stuck-guard: エスカレーション

step: <id> (<n>/<total>) | gate: <none|acceptance-gate|review-gate> | 同一エラー繰り返し: 2回
エラー要約: <1行。テスト失敗なら「テスト N 件失敗」、REJECT なら「reviewer REJECT: <観点>」>

判断してください：
  a) 別のアプローチで再試行する（新しい指示を入力）
  b) この step をスキップして次の step へ進む
  c) このフローを終了する

入力: [a / b / c]
```

  - **エスカレーション後の stuck カウンタ規則（#36）**：user が a)「別のアプローチで再試行」を選んだら stuck カウンタを **0 にリセット**する（新しい指示による再試行は実質的に新しい試みなので、再び同一エラーが**2 回**続いた時にのみ次のエスカレーションを発動する＝「2 回」は a 選択をまたいで累算しない）。何度でも a→retry を繰り返せるが、2 回同一失敗が無ければエスカレーションしない品質フィルタは維持される。b)「スキップ」・c)「終了」選択時は step／flow が終了するためカウンタは irrelevant（リセット規則は適用しない）。なお acceptance-gate K 超の d)「max_retries を増やす」は acceptance-gate 側の K カウンタに作用し、stuck カウンタとは独立（本 §の「独立カウンタ」定義のとおり）。
  - **acceptance-gate の K 超エスカレーション**（独立カウンタ）は**別ヘッダの専用フォーマット**で出す（#28・どちらが発動したか一目で判別できるように）：

```
## rig acceptance-gate: K 超エスカレーション

step: <id> (<n>/<total>) | gate: acceptance-gate | 試行: <K>/<max_retries> 回超過
未達基準: <最後の試行で満たされなかった受け入れ基準>

判断してください：
  a) 別のアプローチで再試行する（新しい指示を入力）
  b) この step をスキップして次の step へ進む
  c) このフローを終了する
  d) max_retries を増やす / 受け入れ基準を見直す
```

   stuck-guard（同一エラー反復）と acceptance-gate K 超（毎回違う理由でも K 回未達）は**発動条件が違う独立カウンタ**なので、`同一エラー繰り返し:` フィールドは前者専用・後者では使わない（意味の誤用を避ける）。
  - **acceptance-gate K 超エスカレーション後も capture 提案（§7.1 `stuck-twice`）を自動提示する（#46）**：K 超は「受け入れ基準を K 回試みたが一度も満たせなかった」最も根の深い詰まりケースであり、stuck-guard と同様に `stuck-twice` capture を提案する。§7.3 の承認ゲートは維持される（`--capture` フラグで省略可）。
  - エスカレーション後は **capture 提案（§7.1 `stuck-twice`）を自動提示**し、詰まりの学びを次回 RUN に残す（a 選択後の再エスカレーションを含め、**エスカレーションが発生するたびに**提示する＝acceptance-gate K 超を含む。同じ根本原因が繰り返すほど学びの蓄積が重要）。
- reviewer は agent 優先（subagent_type 名で起動）・persona facet フォールバック。`review-gate` で REJECT があれば停止して user へ。

