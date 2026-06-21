# rig/goal ゴール駆動ループ（目標→受け入れ基準→収束ループ）— design / 実装 spec

- 日付: 2026-06-21
- ブランチ: `claude/rig-goal-loop-resolution-2nl735`
- 種別: 新モード pack 追加（rig engine 共用）

## 目的

rig を「人が手順（recipe / step）を選んで回す」道具から、「**高レベルな目標を渡すと、それを受け入れ基準に変換して達成まで自分でループを回しきる**」goal-seeking ハーネスへ広げる。

ユーザーは `intake → design → … → merge` のような工程を意識せず、**ゴールだけを宣言**する。rig はゴールを機械照合可能な受け入れ基準に落とし、「現状把握 → 次アクション決定 → 既存フローへ委譲 → 受け入れ照合」のループを、ゴール達成（基準充足）まで回す。詰まったら止めて人に委ねる。

## 着想（既存ブリックの結婚）

goal は新しい制御機構を発明しない。**既存の2つの中核パターンを組むだけ**で成立する:

- `patterns/acceptance-gate` — 「明示した受け入れ基準＋検証で挟めば、経路は非決定的でも出力品質は決定的に一定水準へ収束する」。goal ではこの **受け入れ基準＝ユーザーのゴール**。各周回の「再生成」は **既存 rig フローへの委譲**（gap を縮める1手）。
- `patterns/autonomous-loop` — `ScheduleWakeup` でユーザー介入なしに次周回を予約する自律ループ。**opt-in（`--autonomous`）**。これを `--autonomous` 時のループ駆動に使う。

> goal = 「受け入れ基準がユーザーのゴール、生成ステップが委譲された rig 実行であるような acceptance-gate」。これが rig の LEGO 思想そのもの（engine に手を入れず、既存ブリックの合成で新モードを作る）。

## talk との違い（混同しない）

| | talk | goal |
|---|---|---|
| 役割 | 自然言語ルータ（PARSE の前段） | ゴール駆動ループ（RUN の周回ドライバ） |
| 入出力 | 1発話 → 1フローへ橋渡し | 1ゴール → 達成までの複数フローを回す |
| 終了 | 1ターンで委譲完了 | 受け入れ基準充足 or 詰まりで停止 |

talk は「どのフローを呼ぶか」を1回決める。goal は「ゴールに届くまで何を何回呼ぶか」を周回ごとに決める。

## スコープ

- **engine（SKILL.md）は無改変**。dev / sales / talk と同じ pack-add で成立させる（多ドメイン・engine 不変の実証を継続）。
- 入口は `/rig:goal`。既定 recipe は `goal-loop`。
- 新しい engine flag は足さない。既存 flag（`--autonomous` / `--plan` / `--capture` / `--recipe`）だけで成立させる。周回上限 K は acceptance-gate の規約どおり recipe / manifest 側で調整する。

## アーキテクチャ

engine（PARSE → RESOLVE → COMPOSE → RUN / context-minimal）は `skills/rig/SKILL.md` を共用。goal は以下を**追加**する（engine は触らない）:

```
commands/goal.md                              /rig:goal 入口（薄い。既定 recipe = goal-loop）
skills/rig/facets/personas/goal-driver.md     ループ・ドライバ人格（収束志向・止め際を知る・自分で作業しない）
skills/rig/facets/instructions/goal-loop.md   ①基準化 →②現状把握 →③次手決定 →④委譲 →⑤照合 →⑥周回/停止
skills/rig/recipes/goal-loop.md               ループ step を acceptance-gate で固定した recipe
```

> output-contract は新設しない。委譲先（dev/sales 等）が自前の構造化レポートを持つ。goal の周回レポートは `goal-loop` instruction 内に最小形式で定義する（過剰ブリックを作らない）。

## データフロー（1周回）

`/rig:goal "<目標>" [--autonomous] [--plan] [--capture]` 起動 → rig skill → `goal-loop` recipe を RESOLVE → `goal-loop` instruction に従う。

1. **① 基準化（最初の1回）** — ゴールを **機械/観点照合可能な受け入れ基準リスト**へ落とす（例「build 成功」「対象 Issue がクローズ可能」「指定の振る舞いがテストで green」）。曖昧なら**1問だけ**確認し、捏造しない。`--plan` ならここで基準＋想定ループ構成を提示して停止。
2. **② 現状把握** — 現状とゴールの **gap** を subagent で調べる（context-minimal、親は要約だけ受ける）。
3. **③ 次手決定** — gap を最も縮める **最小の1手**を決める。具体的には「どの `/rig:*` をどの引数で1回回すか」。新規フローを発明せず既存へ委譲する。
4. **④ 委譲実行** — 確定した `/rig:*` 起動文字列を通常 engine（PARSE→RESOLVE→COMPOSE→RUN）へ渡す。実作業は委譲先と subagent が回す（goal は周回を駆動するだけ）。
5. **⑤ 受け入れ照合** — 結果を ① の基準に照合する（acceptance-gate）。
6. **⑥ 周回 / 停止**:
   - **充足** → 完了。何を達成したかを短く報告して終了。
   - **未達 & 進捗あり** → ②へ戻り次周回（`--autonomous` なら `autonomous-loop` の `ScheduleWakeup` で予約／既定は周回ゲートで確認してから次へ）。
   - **詰まり**（未達かつ進捗ゼロの周回が2回 / K 超）→ **停止して user にエスカレーション**（SKILL §6 詰まりガード／acceptance-gate K 規約に連動）。無限ループ禁止。

## autonomy（2モード）

| モード | 起動 | ループ駆動 | 確認 |
|---|---|---|---|
| **gated（既定）** | flag なし | 各周回後に gap と次手を提示し確認 | 影響あるアクション（書込/push/merge）は委譲先の step ゲートで確認 |
| **autonomous** | `--autonomous` | `patterns/autonomous-loop`（`ScheduleWakeup`、delaySeconds はキャッシュ温冷で 270 / 1200+） | 周回ゲートは省くが **capture ゲートは解除されない** |

## 人格: goal-driver（既定・調整可）

- **収束志向**: 周回ごとに gap を縮める最小の1手だけ選ぶ。1周回で全部やろうとしない。
- **自分で作業しない**: 実装・レビュー・調査は委譲先 subagent に渡す。goal-driver は周回の舵だけ取る（context-minimal）。
- **止め際を知る**: 進捗ゼロが2回続いたら粘らず止めて人に委ねる。
- **基準に忠実**: 「だいたい達成」で止めない。基準未達なら次手へ。基準を満たしたら**それ以上やらない**（過剰実装しない）。

## ブリック詳細（実装ステップ）

1. `commands/goal.md` — frontmatter（description / argument-hint）+ 本文。dev/sales と同型で薄く。「まず rig skill を起動し SKILL.md に従う／既定 `--recipe goal-loop`／`goal-loop` instruction に従う／引数は $ARGUMENTS／影響あるアクションは委譲先で確認／無限ループ禁止」を記す。
2. `skills/rig/facets/personas/goal-driver.md` — `# persona: goal-driver` 形式。収束志向・委譲徹底・止め際・基準忠実を定義。
3. `skills/rig/facets/instructions/goal-loop.md` — 上記データフロー①〜⑥を手順化。基準化のやり方、現状把握の委譲、次手＝最小1手の選び方、`/rig:*` への委譲、acceptance-gate 照合、周回/停止条件、2モードを記す。
4. `skills/rig/recipes/goal-loop.md` — 単一ループ step（`instruction: goal-loop` / `gate: acceptance-gate` / `personas: [goal-driver, orchestrator]` / `policies: [branch-strategy, pr-hygiene]` / `acceptance` にゴール基準と詰まり停止条件）。`autonomy: interactive`。
5. README.md / README.ja.md に `/rig:goal` と goal pack・recipe 行を追記（dev/sales/talk と並べる）。
6. plugin.json version を 0.4.0 → 0.5.0。

## 受け入れ基準

1. `/rig:goal "<目標>"` でゴールが受け入れ基準に変換され、達成まで「現状把握→次手→委譲→照合」のループが回る。
2. 各周回は **既存フローへの委譲**で gap を縮める（goal が実装/レビューを直接やらない＝context-minimal を守る）。
3. 受け入れ基準充足で停止し、過剰実装しない。未達かつ進捗ゼロが2回で停止しエスカレーションする（無限ループ禁止）。
4. `--autonomous` で `autonomous-loop` 駆動（`ScheduleWakeup`）に切り替わるが、capture ゲートは解除されない。`--plan` で基準＋ループ構成を提示して停止する。
5. engine（SKILL.md）無改変・dev/sales/talk フロー不変。`/rig:goal` は薄い入口で engine を重複定義しない。

## 非スコープ

- engine flag の新設（K 上限・ゴール DSL 等）。K は acceptance-gate 規約どおり recipe / manifest で調整。
- ゴールの自動分解を多段ツリーで持つプランナ（v1 は「最小1手の逐次決定」で十分。木構造プランニングは将来層）。
- 並列に複数ゴールを同時追跡するマルチゴール・スケジューラ（v1 は単一ゴール）。
- manifest による goal-driver 口調・周回上限の上書き実装（acceptance-gate の K 調整余地は残すが、専用 UI は将来）。
