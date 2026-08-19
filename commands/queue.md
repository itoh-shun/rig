---
description: "rig/queue — タスクを積んで、まとめて GO。cancel で未実行のまま取り消せる。キューを管理ツール(GitHub/GitLab Issue)かローカルで持ち、go で全タスクを並列実行(各タスクをゲート通過)して結果を Issue に書き戻す。"
argument-hint: "<add \"task\" | list | go | done id | retry id | cancel id> [--depends-on ID] [--backend local|github|gitlab] [--repo owner/repo] [--provider rig] [--max-parallel N]"
---

# rig/queue — タスクキュー（積んで GO） 📋

**まず `rig:engine` skill を Skill ツールで起動し、その SKILL.md（context-minimal・計算的オーケストレーション §4.3）に従うこと。** キューの実体は `scripts/orchestrate.py queue`（決定論ランナー＝GO エンジン）。

```
$ARGUMENTS
```

## やること

「1依頼ずつ流す」から「**溜めて一括**」へ。タスクを積み、まとめて並列実行する。

```
orchestrate queue add "<やること>"        # 積む
orchestrate queue list                    # 確認（失敗理由・完了コメントは note として行末に表示）
orchestrate queue go --provider rig --max-parallel 3   # まとめて GO
orchestrate queue done <id>               # 手動で完了に
orchestrate queue retry <id>              # failed（検証 FAIL）の item を queued に戻して再 GO 対象にする
orchestrate queue cancel <id>             # 積んだが実行させない（未実行のまま取消・#459）
orchestrate queue add "<やること>" --depends-on <id> [--depends-on <id> ...]   # 依存を張る（#427）
```

- **go**＝積まれた全タスクを実行：独立タスクは**別プロセスで並列**、各タスクは生成→**独立検証（採点者≠生成者）**のゲートを通過、結果を一括レポート。中身は既存の orchestrate（並列・マルチプロバイダ・local LLM）をそのまま GO エンジンに使う。
- provider は `rig`（各タスクを rig ハーネスで実行・推奨）/ `claude` / `codex` / `ollama` / `lmstudio` / `cmd` / `mock`。
- **`--provider rig`（既定）は各 item を `/rig:go "<task>"` 経由で dispatch する**——`patterns/isolated-worktree` により各タスクが自動的に専用 worktree へ隔離されるため、**並列実行中の headless プロセス同士が同じファイルを取り合う心配がない**。queue の verifier は「gate まで確定したか」＋「本体の作業ツリーに書き込まず isolated worktree 内で完結したか」を判定するだけで、**accept はしない**（queue は隔離・実行・ゲートの層、反映はユーザーの明示操作）。
- **`queue list` は done を除くアクティブ item（queued/running/failed）のみ表示する**（`local`/`github`/`gitlab` 共通）。完了済みタスクで一覧が肥大化しない。
- **`queue cancel <id>` と `queue done <id>` は違う**。`done` は「実行して完了した」の記録で、
  `cockpit` がスループットとして数える数字に入る。タイポ・重複・「もう不要」で積んだものに
  `done` を付けると、**捨てた仕事が完了実績として数えられる**。`cancel` は「積んだが実行させない」
  専用の status で、`queue list` からは `done` と同様に消えるが、`cockpit` は
  `Nothing pending (3 done, 1 cancelled)` のように**別々に数える**。
  - **cancel できるのは `queued` / `waiting` / `blocked` / `failed`**。判定と書き込みは
    **1回のロック内の compare-and-set**で行う。分けると `queue go` の claim が間に割り込み、
    「queued を見た → claim が入る → cancelled を書く → provider が上書き」で
    **取消が黙って無効になる**（操作した側は効いたと思う）。
  - **`running` は拒否**。生きた provider がその item を所有していて、終了時に
    `done`/`failed` を書き込むので `cancelled` は消える。
    **`done` も拒否**——実行して完了したものを「一度も実行していない」と書き直すのは過去についての嘘。
    `failed` は cancel でき、note と出力は「実行はした」と分かる**別の文言**になる
    ——走ったものに「一度も実行していない」と言えば、この status が守ろうとしている監査そのものが歪む。
    どちらの文言も「もう戻せない」とは言わない（実際 retry できるので、言えば効く操作を思いとどまらせる）。
  - **cancelled は retry できる**。`queue retry <id>` でも Mission Control の Retry でも
    戻せる（片方だけ許すと、同じ item について CLI と画面が食い違う）。
  - **`cancel` は local backend 専用**（#459 の意図的なスコープ外）。Issue ラベルに
    「一度も実行していない」を表す状態が無く、`queue_set_status` はラベルも close も
    せずコメントだけを付けるため、**取消したつもりの item が queued のまま残る**。
  - 依存（#427）から見ると **cancelled は terminal**。二度と `done` にならないので、
    依存先が cancel された後続は `waiting` ではなく `blocked` になる。
- **`queue retry <id>`**＝検証 FAIL で `failed` になった item を `queued` に戻し、次の `queue go` の実行対象に含める。プロバイダの一時的なタイムアウト等で落ちたタスクをタスク文の打ち直し（＝別 id・別 Issue）なしに再試行できる。

## 依存を張る（acceptance を edge にする・#427）

```
/rig:queue add "DB migration"                              # → #1
/rig:queue add "API implementation" --depends-on 1         # → #2
/rig:queue add "Release candidate" --depends-on 2 --depends-on 3
```

**後続の開始条件は「前の agent が終わったこと」ではなく「前の成果物が rig の
acceptance boundary を通過したこと」**。ここが唯一にして本質的な違いで、
queue item が `done` になっても依存は満たされない——`done` は「ゲートが確定した」であって
「誰かが適用した」ではないから（`queue go` の verifier は accept しない）。
依存は workbench task の `status` を読む。

そのため **1回の `queue go` の中で後続が ready になることはない**。accept は人の操作で、
GO はそれを待たない。GO は ready なものを走らせ、残りを理由つきで `waiting` にして
そう報告する。accept したあと、もう一度 GO を回す。

| 状態 | 意味 |
|---|---|
| `queued` | 依存なし、または全依存が accepted。次の GO の対象 |
| `waiting` | まだ accept されていない依存がある。accept すれば次の GO で解ける |
| `blocked` | 依存が discarded / failed / 存在しない、または cycle。理由が `queue list` に出る |

`waiting`/`blocked` は**永続する status** であってフィルタではない。理由は2つ：
再起動をまたいで残ること（AC）と、detached worker が `queued` が尽きるまで回るので、
依存待ちの item を `queued` に置いたままにすると**worker が秒間数回の空転を続ける**こと。

- **拒否されるもの**（何も保存されない）: 存在しない id への依存 / 自己参照 /
  未定義の `dependency_policy`。CLI からは cycle を作れない（id は単調増加で、
  新規 item は既存 id しか参照できない＝辺は必ず過去向き）が、手編集された
  `.rig/queue.json` の cycle は検出して該当 item を `blocked` にする。
- **`--depends-on` は `local` backend 専用**。github/gitlab は状態を Issue ラベルで持つので
  辺のリストを置けない。黙って落とすと依存が無いものとして即実行されるため、**エラーで拒否する**。
- **policy は `accepted` の1種類だけ**。辺の条件を語彙にすると DAG 言語になり、
  それは rig の非目標。failed gate を `--force` で越えた accept は**満たす**が、
  receipt と同じく `forced` / `gate_status` を併記して隠さない。
- GO の exit code は従来どおり「このバッチの item が成功したか」。held は
  **このバッチの item ではない**（正しく始まっていない仕事）ので、失敗として数えない。
- **`queue retry` すると `task_id` の紐付けは切れる**。retry は「この item は**別の**成果物を
  出す」という宣言なので、古い紐付けを残すと後続が「差し替え中の成果物」に対して解放される。
  加えて、辺は**依存 item が `done` のときだけ**読む——記録された id は「何を作ったか」に
  答えるが、「それが今も作っているものか」に答えるのは item 自身の status だけ。
- **1 item は1回しか claim されない**。GO は従来 dispatch 時に無条件で `running` を書いており、
  `queue go` を2プロセス同時に起動すると同じ item を二重実行しえた（#427 以前からの性質）。
  依存があると害が増す——2つの run が2つの workbench task を作り、紐付くのは片方だけなので、
  誰も残さなかった成果物に対して後続が解放される。compare-and-set にした。
  GO が途中で死んだときの挙動は変わらない（claim 済みのものだけが `running` で残る）。

Mission Control は `rig.queue-dependencies/v1`（node/edge・色も座標も class も持たない）を
`durable_snapshot` から取得できる。

## 複数タスクを並行で進める（ターミナルを増やさず一括把握）

```
/rig:queue add "ログイン画面のバグを直して"
/rig:queue add "在庫一覧に検索機能を追加して"
/rig:queue go --provider rig --max-parallel 3   # 3タスクを並列 dispatch（各々 isolated worktree）

/rig:go board       # 今どのタスクがどこまで進んだか、1コマンドで一覧
/rig:go diff <id>   # 個別に差分確認 → /rig:go accept <id> で個別に反映
```

複数のターミナルを開いて「どれが何をしていたか忘れる」問題は、`/rig:go board` が単一の真実の情報源になることで解消する。

## バックエンド（キューをどこで持つか）

| backend | 実体 | 状態管理 |
|---|---|---|
| `local`（既定） | `<repo>/.rig/queue.json` | json の status |
| `github` | GitHub Issues（`gh` CLI） | ラベル `rig-queue→rig-running→rig-done`／コメントに結果／完了で close |
| `gitlab` | GitLab Issues（`glab` CLI） | 同上 |

`--backend github --repo owner/repo` で Issue 連携。**チームで共有・永続する backlog** になり、rig がそこから引いて実行・結果を Issue に書き戻す。要：`gh`/`glab` CLI が認証済み（未インストールでも crash せず error 表示）。

> **`local` の同時更新（#360）**：`queue.json` の更新は **flock（プロセス間）＋ threading.Lock（`queue go` のスレッド間）で直列化**し、書き込みは tmp＋`os.replace` で atomic に行う。`queue go` は既定 `--max-parallel 3` で並列に status を書くため、これが無いと更新が取りこぼされ「GO は DONE と言うのに `queue list` では `running` のまま残る」「`queued` に巻き戻った item が次の GO で二重実行される」が起きる。status の記録に失敗した場合は `[WARN] #<id>: could not record status ...` を出して**黙って捨てない**。`queue.json` が壊れて読めないときは**空で作り直さずエラー停止**する（空書き戻しは backlog の消失そのもの）。`github`/`gitlab` は状態を Issue label に持つのでこの経路とは無関係。

## 他フローとの連結

- `/rig:brainstorm` → `/rig:tasks` で割った各タスクを **queue add** で積む → `queue go` で一括実行。
- 「終わりのある仕事」を溜めて回す＝`/rig:goal`（達成収束）・`/rig:loop`（繰り返し）と別軸。

## 例

```
/rig:queue add "JWT リフレッシュを追加"
/rig:queue add "検索の N+1 を直す"
/rig:queue go --provider rig --max-parallel 3
/rig:queue go --backend github --repo itoh-shun/rig    # Issue から引いて実行・書き戻し
```


## run-continuity（SKILL.md §6）

RUN 中は各ターン冒頭に次の run-status ヘッダを1行必ず再掲すること。中断・質疑・tool 出力の直後でも省かない（可視化＝駆動の証拠）:

```
▸ rig | recipe: <name[tier]|ad-hoc> | step: <id> (<n>/<N>) | gate: <none|pending|passed|REJECT> | backend: <manual|workflow> | mode: <gated|autonomous>
```
