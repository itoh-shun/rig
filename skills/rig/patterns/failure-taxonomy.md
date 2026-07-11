# pattern: failure-taxonomy

**失敗を分類してゲート設計に還す** — rig は ESCALATE / BLOCKED / discard で run を止めるが、「なぜ落ちたか」を**型付きコード**で記録してこなかった。分類が無ければ、失敗はテレメトリの `final=ESCALATE` という一語に潰れ、**どのゲート基準／ブリックが取りこぼしたか**というフィードバックが recipe・gate 設計に返らない。このパターンは失敗に**共通語彙（taxonomy code）**を与え、各コードを「本来どのブリックが捕まえるべきだったか」に写像する＝失敗を**改善対象**に変える。

> 出典：**MAST**（Multi-Agent System failure Taxonomy, arXiv:2503.13657）。実運用のマルチエージェント失敗を **3 カテゴリ・14 モード**に整理し、アノテータ間一致 **κ=0.88（Cohen）**で再現性を担保した経験的分類。rig はこの 3 カテゴリを**忠実に**引き継ぎ、14 モードから rig の制御ループ（`computational-orchestration`）で観測可能な部分集合を採る。

## 3 カテゴリ（MAST 忠実）

| # | カテゴリ | 発生相 | rig での意味 |
|---|---|---|---|
| **FC1** | Specification & System Design（仕様・システム設計） | 実行前 | 基準・役割・停止条件の定義不良 |
| **FC2** | Inter-Agent Misalignment（エージェント間ミスアライメント） | 実行中 | 委譲・レビュー・情報授受の噛み合わせ不良 |
| **FC3** | Task Verification & Termination（検証・終了） | 実行後 | 検証の欠落・誤り・早すぎる終了 |

## 14 モード → rig コード → 捕まえるべきブリック

コードは `<category>:<mode>` 形式（`spec:` / `misalign:` / `verification:`）。「捕まえるべき」列は、その失敗を本来検出・抑止すべき rig のゲート基準またはブリック。

### FC1 — spec（仕様・システム設計）

| MAST モード | rig コード | 捕まえるべきゲート基準／ブリック |
|---|---|---|
| 1.1 Disobey task specification | `spec:disobey-task-spec` | **acceptance 基準が緩い**／`output-contract` の必須項目不足 → `patterns/acceptance-gate`（回数でなく基準を明確化） |
| 1.2 Disobey role specification | `spec:disobey-role-spec` | 採点者≠生成者の役割逸脱 → `policies/independent-verification`・verifier の read-only 権限（機構で強制） |
| 1.3 Step repetition | `spec:step-repetition` | 同一 step の空回り → stuck-guard（SKILL §6）・`max_retries` の上限 |
| 1.4 Loss of conversation history | `spec:loss-of-history` | context 圧縮での状態消失 → `run-state.json` 永続（`patterns/computational-orchestration`） |
| 1.5 Unaware of stopping conditions | `spec:unaware-of-termination` | 停止条件の未認識 → ランナーの `ESCALATE`／`--max-steps`（無限ループ禁止） |

### FC2 — misalign（エージェント間ミスアライメント）

| MAST モード | rig コード | 捕まえるべきゲート基準／ブリック |
|---|---|---|
| 2.1 Conversation reset | `misalign:conversation-reset` | プロセス隔離で文脈が飛ぶ副作用 → `run-state.json` に状態継承（隔離と継承を両立） |
| 2.2 Fail to ask for clarification | `misalign:fail-to-clarify` | 曖昧なまま実装へ → `/rig:brainstorm`・intake step（何を作るかを先に固める） |
| 2.3 Task derailment | `misalign:task-derailment` | 目標から逸れる → goal／acceptance 照合（`/rig:goal` の受け入れ照合） |
| 2.4 Information withholding | `misalign:info-withholding` | 判定根拠の握り込み → `patterns/structured-report`（根拠を機械抽出・サイレント消し禁止） |
| 2.5 Ignored other agent's input | `misalign:ignored-input` | REJECT／条件の握りつぶし → `patterns/review-gate`（条件統合・1 件でも未対応で着手しない） |
| 2.6 Reasoning-action mismatch | `misalign:reasoning-action-mismatch` | 推論と判定の不一致 → evidence-first verdict（最終 `判定:` 行が決定・judge hardening） |

### FC3 — verification（検証・終了）

| MAST モード | rig コード | 捕まえるべきゲート基準／ブリック |
|---|---|---|
| 3.1 Premature termination | `verification:premature-termination` | 「できました」の自己申告で早期終了 → `patterns/acceptance-gate`（基準未達は次 step へ通さない） |
| 3.2 No/incomplete verification | `verification:missing` | gated step に checks も独立 verdict も無い（no-verifier stall） → `checks:` 宣言 or `finding-verifier` |
| 3.3 Incorrect verification | `verification:incorrect-verification` | 誤検証・ゴム印 → `/rig:drill` 検出率ギャップ／`facets/personas/finding-verifier`（所見の反証段） |

### rig 固有の決定論派生（3.x の実装内訳）

MAST の 3.x を、rig の制御ループで**状態から決定論的に**判別できる 2 コードに具体化したもの（`runstate.classify_failure`）。

| rig コード | 由来 MAST | 決定論シグナル（state） | 捕まえるべき |
|---|---|---|---|
| `verification:self-grading` | 1.2 / 3.3 | ある step の verdict が `by=self/generator/producer/""` | 採点者≠生成者（`policies/independent-verification`・BLOCKED で停止） |
| `verification:incorrect-implementation` | 3.3 | 宣言 `checks` が失敗し続けて K 回で ESCALATE | 機械センサー（checks）が実装の誤りを検出＝収束せず（`patterns/acceptance-gate`） |

## `classify_failure`（runstate.py）— 決定論の分類器

`classify_failure(state) -> str | None` は run-state から**最善の推測**を 1 コードで返す純関数（モデル呼び出し無し）。ESCALATE/BLOCKED の run に対し taxonomy code を、成功／実行中の run には `None` を返す。判別できない停止は握りつぶさず `unclassified` を返す。結果は `telemetry_append` が **`failure_mode`** として `runs.jsonl` に**加算的に**記録する（成功 run には出ない）。

- **MODEL-suggested-but-deterministically-stored**：ここで記録するのは state から再現可能な決定論値。将来、エスカレーション時にモデルが供給するより richer な分類（例：レビュアーが根拠つきでコードを宣言）を caller が差し込めるが、**保存されるのは決定論値**を既定とする（テレメトリの再現性を壊さない）。
- **フィードバックループ**：`failure_mode` 分布はダッシュボード（`scripts/dashboard.py`）の panel と `runs` 集計に出る。特定コードが積み上がる＝そのカテゴリのゲート基準／ブリックが弱い、という**設計への差し戻し信号**になる（例：`verification:missing` が多い→ recipe に `checks:` か verifier を足す）。

## 使いどき

停止した run の事後分析・テレメトリ集計・gate/recipe 設計の見直し。分類は決定論なので selftest（FM シナリオ）が golden 検証する。軽い run に新たな儀式を足すものではない（記録は失敗時のみ・加算的）。

## 関連ブリック

- `patterns/acceptance-gate` — 基準未達を次 step へ通さない（3.1 の抑止）
- `patterns/review-gate` — 条件統合・所見の反証（2.5 / 3.3 の抑止）
- `patterns/computational-orchestration` — `run-state.json` 永続・ESCALATE/BLOCKED（1.4 / 1.5 の抑止・分類の観測点）
- `facets/personas/finding-verifier` — 誤検証（3.3）の反証段
