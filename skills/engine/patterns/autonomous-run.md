# pattern: autonomous-run

`--autonomous` の RUN を**止まらず完走させ、何を決めたかを後から追え、外れたら確実に止まる**ようにする規約。`--autonomous` が外すのは step ゲート（各 step 後の確認）だけで、そのゲートが担っていた2つの役目は残る。1つは「止める機会」、もう1つは「判断を人が見る機会」。この pattern は、人をループに戻さずにその2つを機械側で引き受ける。

- **止める機会** → 停止スイッチと上限を `rig-wb wb autonomy check` が journal から決定論で判定する（散文の自己申告で止めない）。
- **見る機会** → ゲートで見せるはずだった判断を、その場で journal（`.rig/autonomy/<run>.jsonl`）に記録する。終了時に `report` が「代わりに決めたこと／確認してほしいこと／戻れる地点」として手渡す。

**適用範囲**：`--autonomous`（または recipe `autonomy: autonomous`）の RUN すべて。`/rig:dev`・`/rig:go`・`/rig:goal`・pack コマンドも対象になる。`patterns/autonomous-loop`（`ScheduleWakeup` による周回駆動）は、この pattern の上で回す。

> **解除されないもの（従来どおり）**：acceptance-gate の品質収束ループ、capture ゲート、書き込み系 generator の上書き確認、PR への投稿確認、brainstorm の次段確認。これらは自律化の対象外。

## 1. 開始

RUN の最初の step に入る前に、run id を決めて journal を開く。run id には workbench の task id があればそれを使い、無ければ `<recipe>-<YYYYMMDD-HHMM>` を使う。

```bash
rig-wb wb autonomy start --run <run> --summary "<ゴール1行>" \
  [--max-steps 30] [--max-minutes 240] [--max-recoveries 6] [--max-recoveries-per-step 2]
```

- 上限は**ここで1回だけ**固定する。以後の `check` は journal 先頭の値を読む。途中で上限を引き上げる手段は無い（自走中のエージェントが自分の枠を広げられないようにするため）。
- ユーザーの依頼に「2時間以内」「10 step まで」などとあれば、それを上限に写す。無ければ既定値を使う。
- run id は、圧縮を跨いでも journal を見失わないように引き継ぐ。`patterns/autonomous-loop` の `<<autonomous-loop-dynamic>>` の6要素目と、PreCompact で保全する run 状態に入れる。run-status 行の形式は変えない。
- `rig-wb` が使えない環境では、同じ項目を `.rig/autonomy/<run>.md` に箇条書きで残す。上限判定は散文で行い、完了レポートに `[UNVERIFIED: autonomy CLI 不在]` と明記する。

## 2. step 境界（step ゲートの代わり）

step ゲートで確認を取る位置では、毎回次の3つを行う。

1. `log --kind step-done --step <id> --summary "<結果1行>"` を記録する。
2. 次の step が書き込みを伴う（implement / fix / migrate / pr など）なら、**チェックポイント**を取る（§5）。
3. `check` を呼ぶ。exit 0 なら次へ進み、exit 1 なら §6 の停止手順へ、exit 2 なら journal の不備として停止手順へ進む。

```bash
rig-wb wb autonomy log   --run <run> --kind step-done --step implement --summary "テスト 12 件追加・全 green"
rig-wb wb autonomy check --run <run>    # 0=続行 / 1=停止すべき / 2=判定不能
```

ゲートを省いたこと自体も1行残す（`--kind gate-skipped --summary "<from> → <to>: <見せるはずだった要点>"`）。step-done と同じ行にまとめてよい内容なら、step-done だけで足りる。

## 3. 質問の代わりに決めて記録する（判断ポリシー）

RUN 中に「人に聞きたい」が生じたら、まず次の表で分類する。**途中で質問して止まらない**ことが既定になる。

| 分類 | 例 | 動作 |
|---|---|---|
| **hard stop**（§4） | 破壊的操作・要件の根本的な曖昧さ・ポリシー承認・予算切れ・能力不足 | `log --kind hard-stop --reason <理由>` を記録して停止手順へ |
| **可逆な曖昧さ** | 命名・配置・既存規約で2案あり得る・スコープの境界 | **最小で保守的な案**を選ぶ（既存規約に合わせる・変更範囲を狭く取る・後から足せる方を選ぶ）。`--kind decision` に「選んだ案と根拠」、`--kind assumption` に「置いた前提」を残す |
| **人の好みで答えが変わる** | 文言・UI の見た目・API の公開範囲 | 保守的な案で進めたうえで、`--kind deferred-question` に「何を確認したいか・今はどちらで実装したか」を残す。質問は**完了時にまとめて**出す |

- 「保守的」の基準：既存挙動を壊さない、公開面を増やさない、削除より追加を選ぶ、後から容易に取り消せる。
- 推測で仕様を**捏造しない**。根拠の無い前提は、必ず assumption として表に出す。

## 4. hard stop（`--autonomous` でも自分では決めない）

次のどれかに当たったら、続行せず停止手順へ進む。理由は `assurance/development_loop` の escalation と同じ5種類に揃えている。

| `--reason` | 該当する操作・状況 |
|---|---|
| `destructive-operation` | `git push --force`（`--force-with-lease` を除く）・`git reset --hard`／`git clean -f` を作業 worktree 外で使う・`rm -rf` を絶対パスや変数展開に使う・`DROP`／`TRUNCATE`・既定ブランチへの push や merge・ブランチやタグの削除・デプロイ・パッケージ公開・外部への投稿（PR コメント・Issue・チャット） |
| `ambiguous-requirement` | 受け入れ基準そのものが決められない。どちらの案を選んでも、他方を選んだ場合の成果物が無駄になる |
| `policy-requires-approval` | org-policy・govern・CODEOWNERS などが人の承認を要求している |
| `budget-exhausted` | `--budget` やコスト上限に達した |
| `capability-missing` | 必要な権限・資格情報・ツール・ネットワークが無い |

ユーザーの依頼文が当該操作を**明示的に**許可している場合（例：「main に merge まで」）だけは、その操作を hard stop から外してよい。その場合も `--kind decision` に「依頼文のどの部分で許可されたか」を残す。

## 5. チェックポイント（戻れる地点）

書き込みを伴う step の直前に、作業ツリーを変えずに現在の状態を固定する。

```bash
ref=$(git stash create); ref=${ref:-$(git rev-parse HEAD)}
git update-ref "refs/rig/autonomy/<run>/<step>" "$ref"     # gc で消えないよう参照を張る
rig-wb wb autonomy log --run <run> --kind checkpoint --step <step> --ref "$ref" --summary "<step> 前"
```

- `git stash create` は stash を積まず、作業ツリーも変えない。未コミット変更が無ければ空を返すので、そのときは HEAD を使う。
- 巻き戻しは**人の判断**で行う（`git checkout <ref> -- .` など）。自走中に自分でチェックポイントへ戻すのは、隔離 worktree 内で失敗した step をやり直すときに限る。その場合は `--kind recovery` として記録する。

## 6. 自己回復ラダー（stuck-guard の autonomous 版）

gated では「同じ所で2回詰まったら user に a/b/c を聞く」。autonomous では、聞く前に**回復を段階的に試す**。

1. **回復1：再診断**。同じエラーや同じ REJECT が2回続いたら、新しい subagent（`debugger` persona）に根本原因を調べさせ、**前回と違うアプローチ**で再試行する。`--kind recovery --step <id> --summary "<変えた点>"`
2. **回復2：縮小と再計画**。それでも詰まったら、step のスコープを最小単位まで縮め（問題箇所だけを直す・失敗テスト1件から始める）、計画を立て直して再試行する。`--kind recovery`
3. **ラダー切れ**：`check` が `recovery ladder exhausted` で exit 1 を返す。ここで初めて停止手順に入る。

- 段数の上限は `--max-recoveries-per-step`（既定 2）、run 全体の上限は `--max-recoveries`（既定 6）で決まる。
- acceptance-gate の K 超も同じラダーに乗せる。回復を1段使って基準照合をやり直し、それでも未達なら停止手順へ進む。K 超エスカレーションの正準フォーマットは、停止レポートの中にそのまま入れる。
- **品質フィルタは緩めない**。テストのスキップ・無効化・アサーションの弱化・基準の書き換えで「回復」したことにしない。それは回復ではなく改竄にあたる。

## 7. 停止と完了（手渡し）

`check` が exit 1 を返したとき、hard stop を記録したとき、全 step を完了したときは、どれも同じ形で終える。

1. 完了したなら `log --kind finish --summary "<結果1行>"` を記録する。停止なら finish は書かず、停止理由を `check` の出力から取る。
2. `rig-wb wb autonomy report --run <run>` の出力を、そのままユーザーに提示する。項目は次の順に並ぶ。

```
## rig autonomous report: <run>

status: <finished|stopped|running> | goal: <ゴール>
usage: steps n/N · minutes m/M · recoveries r/R
stopped because: <理由>            ← 停止時のみ

### Questions deferred to you (k)    ← まず答えてほしいこと
### Assumptions to confirm (k)
### Decisions taken without asking (k)
### Step gates skipped (k)
### Recoveries tried (k)
### Checkpoints (roll back here) (k)
### Hard stops (k)
```

3. 停止時は、レポートの後に stuck-guard または K 超の正準エスカレーション（SKILL §6）を続けて出し、選択肢を提示する。**ここで初めて** user の判断を待つ。
4. capture 提案（SKILL §7）は、従来どおり承認を取る。

## 8. 停止スイッチ（人が外から止める）

- 全 run を止める：リポジトリ直下で `touch .rig/STOP` を実行する（別ターミナルからでよい）。
- 1つの run だけ止める：`rig-wb wb autonomy stop --run <run> --summary "<理由>"` を実行する。
- どちらも、次の step 境界または次の起床時の `check` で効く。実行中の step を中断するものではない。再開するときは停止ファイルを消し、新しい run id で始め直す。

## red flags

| 言い訳 | 実際 |
|---|---|
| 「journal を書く手間で遅くなる」 | 1 step 1〜2 行で足りる。記録の無い自走は、終わった後に誰も検証できない |
| 「上限に近いので check を飛ばす」 | 上限が効くのは check を呼んだときだけ。飛ばすのは上限の無効化と同じ |
| 「聞けば早いので途中で質問する」 | autonomous の既定は「保守的に決めて記録し、最後にまとめて聞く」。途中で質問してよいのは hard stop のときだけ |
| 「回復のためにテストを一時的に skip する」 | 品質フィルタの改竄にあたる。ラダーの段として数えず、即 hard stop と同じ扱いにする |
| 「依頼が『全部任せる』なので force push も可」 | 包括的な委任は、破壊的操作の明示的な許可にはならない。操作名が依頼文に無ければ hard stop |

## 関連ブリック

- `patterns/autonomous-loop` — `ScheduleWakeup` で周回を予約する。起床ごとに `check` を呼び、run id を次ターンへ引き継ぐ
- `patterns/acceptance-gate` — 品質収束。autonomous でも解除されない
- `patterns/isolated-worktree` — 自走の書き込みを隔離 worktree に閉じ込める（チェックポイントの前提）
- `rig-wb wb dev-loop` — 開発ループの記録を停止条件と照合する（同じ escalation 語彙を使う）
