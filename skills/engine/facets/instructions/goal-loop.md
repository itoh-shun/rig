# instruction: goal-loop

高レベルな目標を受け取り、それを**受け入れ基準**に変換して、達成まで「現状把握 → 次手決定 → 既存フローへ委譲 → 受け入れ照合」のループを回す。goal は新しい制御機構を発明しない。**`patterns/acceptance-gate`（受け入れ基準＝ゴール）と `patterns/autonomous-loop`（`--autonomous` 時の周回駆動）を組むだけ**で成立する。実作業は委譲先（`/rig:dev` 等）と subagent が回す。goal は周回を駆動するだけ（context-minimal）。

> **ループ1周回＝5つの動き**（loop engineering・詳細は wiki `[[loop-engineering]]`＝`goal-driver` persona が `inject:` で自動注入）：**discovery**（②現状把握/③次手）→ **handoff**（④委譲）→ **verification**（⑤照合・**独立検証**）→ **persistence**（run-continuity）→ **scheduling**（⑥周回・`autonomous-loop`）。この層は harness の1つ上＝作業者をループから外す。最も壊れやすいのは verification（自己採点バイアス）。

## 手順

### ① 基準化（最初の1回だけ）

ゴールを**機械/観点で照合できる受け入れ基準のリスト**へ落とす。これが acceptance-gate の受け入れ contract になる。

- 例: 「ログイン不具合を直す」→ `["再現テストが green", "回帰テスト一式が green", "対象 Issue がクローズ可能", "review に REJECT が無い"]`。
- 機械検証（build/lint/test/grep 0 件）を優先し、無理なら観点検証（review-gate で REJECT 無し）を据える。
- **GitHub 連動基準（任意）**: ゴールが「PR を出せる状態に」「マージ可能まで」を含むなら、GitHub MCP で照合できる基準を据える — 例 `["対象 Issue がクローズ可能", "PR が open", "PR の CI が green", "未解決のレビュースレッドが無い"]`。これらは ⑤ で GitHub MCP（`pull_request_read` / `list_pull_requests` / `actions_*` / CI status 等）で機械照合する。push/PR 作成自体は委譲先（`/rig:dev` の pr/merge step）が行い、goal は状態を照合するだけ。
- **曖昧・対象不明なら1問だけ確認**して確定する。捏造で基準を埋めない。
- `--plan` 指定時 → ここで **基準＋想定ループ構成**（どの `/rig:*` を回しそうか）を人間可読で提示して**停止**。RUN しない。

### ② 現状把握

現状とゴールの **gap** を subagent 経由で調べる。親（goal-driver）は**要約だけ**受け取る（長い diff・ログ・ファイル全文を引き込まない）。当て推量で着手しない。

### ③ 次手決定（最小の1手）

gap を**最も縮める最小の1手**を決める。「どの `/rig:*` コマンドを、どの flag / recipe / 対象で、1回回すか」に翻訳する。

- 例: 実装が要る → `/rig:dev "<gap を埋める変更>"`。レビューだけ要る → `/rig:dev --only review`。設計から要る → `/rig:dev --design "<…>"`。
- **新しいフローを発明しない**。既存の `/rig:*`（dev/sales/…）へ委譲する。利用可能なコマンドは動的に確認する（新 pack は自動的に候補に入る）。
- 1周回で全部やろうとしない。1手 → 照合 → また1手。

### ④ 委譲実行

確定した `/rig:*` 起動文字列を該当コマンド経由で通常 engine（PARSE→RESOLVE→COMPOSE→RUN）へ渡す。**goal は engine 規則（context-minimal・facet 配置順・知識層注入）を再定義せず、委譲先にそのまま従わせる**。実作業は委譲先と subagent が回す。

### ⑤ 受け入れ照合（acceptance-gate・verification）

委譲結果を ① の受け入れ基準に照合する。`structured-report`（委譲先の出力）から機械抽出し、各基準の充足を判定する。**基準未達の成果物を「達成」とみなさない**。

- **採点者は生成者と別人**（`policies/independent-verification`）。照合を ④ の委譲先（生成者）に自己判定させない。機械検証（build/lint/test/grep・GitHub MCP の status）を一次根拠にし、観点照合が要る所は**生成と別の checker/reviewer を独立 dispatch** して verdict 行だけ読む。**「作って・自分で合格」を禁止**（自己採点バイアスでループが間違ったまま収束するのを防ぐ）。疑わしきは未達側に倒す。

- ローカル基準（build/lint/test/review）→ 委譲先の structured-report で照合。
- **GitHub 連動基準**（PR open / CI green / Issue クローズ可能 / レビュースレッド）→ **GitHub MCP** で照合する。照合は read 系ツール（`pull_request_read` / `list_pull_requests` / `actions_list` / `actions_get` 等）に限り、親 context に長いログを引き込まない（判定に要る status 行だけ読む）。CI が pending なら「未達（進捗待ち）」として次周回で再照合する（無限待ちは詰まりガードで停止）。

### ⑥ 周回 / 停止

| 照合結果 | 動作 |
|---|---|
| **全基準を充足** | 完了。何を達成したか（満たした基準）を**短く**報告して終了。**過剰実装しない**（基準を満たしたらそれ以上回さない）。 |
| **未達 & 進捗あり**（gap が縮んだ） | ②へ戻り次周回。既定（gated）は次周回前に gap と次手を提示して確認。`--autonomous` は `patterns/autonomous-loop` の `ScheduleWakeup` で次周回を予約（delaySeconds はキャッシュ温冷で 270 / 1200+）。 |
| **未達 & 進捗ゼロが2回 / K 超** | **停止して user にエスカレーション**（SKILL §6 詰まりガード／acceptance-gate の K 規約に連動）。何が詰まっているか・選択肢を提示する。**無限ループ禁止**。 |

## autonomy（2モード）

- **gated（既定）** — 各周回後に gap と次手を提示し、確認してから次へ。影響あるアクション（書込/push/merge）は委譲先の step ゲートで確認される。
- **autonomous（`--autonomous`）** — 周回ゲートを省き `autonomous-loop` で自走する。ただし **capture ゲートは解除されない**（学びの知識層書き込みは常に承認が要る）。

## 原則

- goal 自身は重い処理を抱えない。実装・レビュー・調査・検証は委譲先と subagent が context-minimal で回す。
- engine（SKILL.md）と dev / sales / talk のフローは変更しない。goal は周回ドライバであって engine の再定義ではない。
- **基準が羅針盤**。基準を増やすより**基準を明確にする**方が収束は速い（acceptance-gate K の目安に従う）。
- **検証は独立に**（`policies/independent-verification`）。生成者の自己採点を「達成」の根拠にしない。verification を生成と切り離すのが、自走ループの一番の事故防止。
