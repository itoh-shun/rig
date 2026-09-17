# 全ケースの採点証拠（1 run × 2 arm、judge: sonnet）

costUsd 5.20 / meanDelta 0

## 01-review-to-merge  with 1.00 / without 0.33 / Δ +0.67

### arm: with / turns 16 / gate-blocks-progression=PPP, names-ordered-steps=PPP, review-split-and-reconciled=PPP

```
リグです。手順だけ先に出しますね。書き込みもレビュー実行もまだしません。

```
→ /rig:dev --recipe release-flow --from verify --review --adversarial --verify-findings --plan
```

## rig --plan

```
recipe: release-flow | autonomy: interactive | backend: manual | review: on | adversarial: on | verify-findings: on
diff: 不明 → size: S（既定）
description: 標準リリースフロー。intake→design?→implement→verify→review?→pr→merge の全工程。サイズ感に応じて design / review を自動 ON/OFF。
flags: --review --adversarial --verify-findings
slice: --from verify
```

| # | step | instruction | pattern | gate | personas | policies | output_contract | condition |
|---|---|---|---|---|---|---|---|---|
| 1 | verify | verify | serial | acceptance-gate | implementer | risk-based-testing, ci-cost | — | — |
| 2 | review | parallel-review | parallel-fanout | acceptance-gate | security-reviewer [agent], design-reviewer [agent], test-reviewer [agent], behavioral-correctness-reviewer [agent] | pre-push-review | review-verdict | --review または size L+ [✓ 実行: --review 解決] |
| 3 | adversarial | adversarial-review | parallel-fanout | acceptance-gate | lazy-senior [agent], cognitive-economist [agent] | — | review-verdict | --adversarial [✓ 実行] |
| 4 | pr | pr | serial | — | orchestrator | pr-hygiene, branch-strategy | — | — |
| 5 | merge | merge | serial | — | orchestrator | branch-strategy, ci-cost | — | — |

### Gate: ゲート条件

**step: verify**（acceptance-gate · max_retries: 2 [既定]）
- [ ] build が成功
- [ ] lint 0 件
- [ ] 関連テスト green

**step: review**（acceptance-gate · max_retries: 2 [既定]）
- [ ] 4-way review に REJECT が無い
- [ ] APPROVE_WITH_CONDITIONS のマージ前必須条件をすべて反映済み
- [ ] （--verify-findings）REJECT の根拠を finding-verifier [agent] が反証 → REFUTED はゲートに通さない

**step: adversarial**（acceptance-gate · max_retries: 2 [既定]）
- [ ] AI-slop（自明コメント / 過剰防御 / 汎用命名 / 過抽象 / dead code）の指摘が無い
- [ ] 人間可読性に REJECT が無い

[既定] = 汎用既定値（2）

### Knowledge: 注入予定ソース
（knowledge なし — 汎用動作。manifest `.claude/rig.md` 未設置）

### Reviewer Fan-out: レビュアー集合

**step: review**（pattern: parallel-fanout）
- security-reviewer [agent] / design-reviewer [agent] / test-reviewer [agent] / behavioral-correctness-reviewer [agent]

**step: adversarial**（pattern: parallel-fanout）
- lazy-senior [agent] / cognitive-economist [agent]

```
steps: 5（うち condition 付き=2 / gate=3 / acceptance retries 上限: 6）| RUN はしない
```

**観点の抜けについて**：既定の 4-way に敵対レビュー 2枚を足して 6観点、さらに `--verify-findings` で REJECT の根拠を別エージェントに反証させて誤検知を落とします。差分の中身次第で `--persona api-compat-reviewer` / `docs-reviewer` / `observability-reviewer` / `performance-reviewer` / `migration-reviewer` を足せます。どれが要るかは差分を見てから決めたいです。

**確認したいこと**：この作業ディレクトリ（`/tmp/claude-eval-mvlAdW/home/cwd`）にファイルが1つも見つかりません。レビュー対象はどこですか — 別リポジトリの作業ツリー、ブランチ、それとも既に開いている PR 番号でしょうか。それが決まれば `diff:` と size を実測して、この plan を確定版に直します。
```

### arm: without / turns 3 / gate-blocks-progression=FFF, names-ordered-steps=PPP, review-split-and-reconciled=FFF

```
作業ディレクトリ (`/tmp/claude-eval-26strv/home/cwd`) を見たところファイルが 1 つもなく、コミット履歴も空でした。レビュー対象のコードがまだここには無い状態です。場所を教えてもらう前提で、進め方の全体像を先に出します。

## 進め方（案）

**1. レビュー対象の確定**
- 未コミットの作業ツリーか / `main` との差分ブランチか / 既存 PR 番号か、のどれかを決める
- ここを決めないと「何が新しく書いたコードか」が曖昧になり、レビューの抜けの最大要因になります

**2. 足元の確認（レビュー前）**
- ビルド・テスト・lint が通る状態か
- 壊れたまま人力レビューに入ると指摘が表面的な話に流れるので、先に緑にしておきます

**3. `/code-review` を実行**
- 正確性バグ（ロジック、境界条件、エラー処理、並行性）と、再利用・簡素化・効率の観点をカバー
- effort は最初 `high` を推奨。差分が小さければ `medium` で十分
- `--fix` で自動適用もできますが、初回は指摘を読んでから判断する方をおすすめします

**4. `/security-review` を実行**
- 入力検証、認証・認可、機密情報の混入、インジェクション系
- `/code-review` とは観点が別なので、両方かけるのが「抜けが怖い」への回答になります

**5. 自動レビューが弱い領域を手動で潰す**
- ここが一番抜けやすいところなので、チェックリストとして明示します:
  - 仕様・チケットの受け入れ条件と実装が一致しているか
  - 後方互換性（API・DB スキーマ・設定ファイル）
  - マイグレーションとロールバック手順
  - テストが「書かれている」だけでなく、失敗すべきケースで実際に落ちるか
  - ログ・メトリクスなど運用時に必要な観測性
  - パフォーマンス上の懸念は実測が必要か、レビューで足りるか

**6. 指摘の仕分け**
- 「直す / 別 Issue に切る / 対応不要」を一緒に判断します。全部直すのが正解とは限らないので、ここは相談させてください

**7. 修正 → 再テスト**
- 修正を当てて、2 で通した確認をもう一度

**8. コミット整理 → PR → マージ**
- コミットを読める単位にまとめ、PR 説明（背景・変更点・検証方法）を用意
- CI 緑を確認してからマージ
- PR 作成・マージは外向きの操作なので、実行前に必ず内容を見せて確認を取ります

## 確認したいこと

1. コードはどこにありますか（別のディレクトリ / 別ブランチ / 既存 PR）
2. 変更の規模感（ファイル数、ざっくりの行数）と、何をする変更か
3. マージ先は `main` で合っていますか

1 が分かればすぐ 1〜4 に着手できます。
```

## 02-fix-nan-total  with 0.33 / without 0.00 / Δ +0.33

### arm: with / turns 8 / failing-test-precedes-fix=PPP, gate-blocks-progression=FFF, isolates-before-landing=FFF

```
The working directory here (`/tmp/claude-eval-oxo7gE/home/cwd`) is empty — no checkout or cart code in it, so I can't point at your actual bug yet. Where does the code live?

Here's how I'd approach it once I can see it:

**1. Reproduce before fixing.** Write the failing test first, at the level where the number goes wrong — the total calculation, not the rendered string. `calculateTotal([])` → expect `0`. That test is the one that "would have caught it," and it should fail before any fix.

**2. Find where NaN is born, not where it surfaces.** NaN in a cart total is almost always one of:
- `reduce` with no initial value on an empty array (returns `undefined` on empty, or the sole element on length-1)
- arithmetic on an `undefined` field — `subtotal * taxRate` where `subtotal` was never set
- `parseFloat("")` / `Number(null)` on a missing price

Each has a different fix, and the first two behave differently on a one-item cart too, so it's worth knowing which.

**3. Fix at the source, not the render.** The tempting patch is `total || 0` in the component. I'd push back on that: it makes empty carts render `0` *and* makes a genuinely broken non-empty cart silently render `0` instead of loudly rendering NaN. You'd trade a visible bug for an invisible one. The fix belongs in the total function — make it correct for the empty case by construction (seed the reduce, default the field), so it's total over its input domain.

**4. Test the boundary, not just the one case.** Empty cart → 0. One item. Item with a missing/zero price. And an assertion that the rendered total is a formatted currency string, so a future NaN can't slip through the display layer.

If you tell me where the repo is, I'll run this through rig properly — isolated worktree, TDD (test red first), then the review fan-out and gate before anything lands in your tree. Want me to do that?
```

### arm: without / turns 5 / failing-test-precedes-fix=FFF, gate-blocks-progression=FFF, isolates-before-landing=FFF

```
The working directory (`/tmp/claude-eval-S68paw/home/cwd`) is empty — no source files at all, and the repo has no commits. So I can't find the checkout code to fix it. Point me at the right directory or paste the file and I'll do it.

In the meantime, here's how I'd approach it once I can see the code:

**1. Find where the NaN is actually born, not where it's displayed.**

`NaN` propagates silently, so the render site is almost never the bug. Empty-cart NaN usually comes from one of a few places:

- `items.reduce((a, b) => a + b.price)` with no initial value — on an empty array this throws, but on a one-element array it returns the *object*, and with a seed of `undefined` it poisons everything. Missing `0` seed is the single most common cause.
- A separate `undefined` reaching arithmetic: `cart.total` before fetch resolves, `subtotal - discount` where `discount` is undefined, or a tax rate read off a missing config.
- A currency string parsed with `parseFloat("")` → `NaN`.

I'd trace backwards from the render value to the first expression that can be non-numeric, rather than guessing.

**2. Fix at that origin.**

"Properly rather than patched over" means the fix goes where the invalid value is produced — give `reduce` its identity element `0`, or make the type honest so `undefined` can't reach the arithmetic. What I'd *avoid* is the patch-over version:

```js
// don't do this
const display = isNaN(total) ? 0 : total;
```

That hides every future NaN, including ones from real pricing bugs, and turns a loud failure into a silently wrong number.

**3. The test that would have caught it.**

The reason this shipped is almost certainly that the test suite only ever exercises carts with items. I'd add the empty case at the same level as the bug:

```js
test('total is 0 for an empty cart', () => {
  expect(calculateTotal([])).toBe(0);
});
```

and, since `toBe(0)` passing doesn't prove the function is NaN-safe in general, a boundary sweep around it: single item, and — if the code does discounts/tax — a case where the optional field is absent. If the root cause turns out to be `undefined` from an unresolved fetch rather than `reduce`, the test belongs at the component level instead: render with the cart still loading and assert the total isn't `NaN`.

**4. Check for siblings.**

Whatever pattern caused it (unseeded `reduce`, unguarded optional field) usually appears more than once. I'd grep for the same shape across the codebase before calling it done.

Which directory is the project in?
```

## 03-design-to-implement  with 0.50 / without 0.50 / Δ +0.00

### arm: with / turns 9 / design-precedes-implementation=PPP, gate-blocks-progression=PFF

```
仕様が固まっていないなら、いきなり実装には入らず3段で通すのがrigの標準ルートです。

1. **`/rig:brainstorm`** — 何を作るか・なぜか・どの順かを対話で詰めて、design brief にまとめます。通知の対象者、チャネル（アプリ内/メール/Slack）、リアルタイム性、既読管理あたりを順に詰める段です。
2. **`/rig:tasks`** — brief を、それぞれ検証方法つきの小さいタスクに割ります。承認してから実装に入ります。
3. **`/rig:go`** — 各タスクを隔離 worktree で実装 → レビュー fan-out → 受け入れゲート。ゲートを通るまで accept できないので、本体のツリーは最後まで汚れません。

1と2の間で仕様が動いたら戻れますし、各 step で「次へ進むか」の確認が入ります。全部通して一本、という要望はこの形で満たせます。

先に一点だけ確認させてください。このリポジトリは既存プロダクトのコードベースですか、それともゼロからの新規ですか。既存なら brainstorm の段で現状のスタックを読みに行かせます。

そのうえで、`/rig:brainstorm` から始めてよいですか。
```

### arm: without / turns 3 / design-precedes-implementation=PPP, gate-blocks-progression=FFF

```
作業ディレクトリは空でした(git リポジトリだけ、コミットなし)。なので既存コードに合わせるのではなく、スタック選定から入る前提で進め方を示します。

## 進め方の全体像

仕様が固まっていない状態で設計〜実装を一本で通す場合、**「決まっていない部分が後から変わっても壊れない切り方」を先に決める**のが要点になります。具体的には、通知の「生成」「保存」「配信チャネル」を分離しておけば、Slack を足す・メールをやめる・既読管理の仕様が変わる、といった揺れが局所で吸収できます。

### フェーズ1: 要件の骨組みを固める(会話ベース、コードなし)
確定していない前提のまま、以下だけ言語化します。ここが後の全部を規定するので、私が仮置きして進めることもできますが、ズレると作り直しになる範囲が大きい部分です。

- 誰が通知を発生させるか(システムイベント / 人の操作 / バッチ)
- 誰に届くか(個人宛 / ロール宛 / 全社ブロードキャスト)
- どのチャネルで出すか(アプリ内一覧のみか、メールや Slack も)
- 既読・未読、通知設定(ユーザーごとのオプトアウト)の有無

### フェーズ2: 設計を文書として出す
`docs/design.md` 相当に、データモデル(notification / notification_delivery / user_preference あたり)、API 契約、配信パイプラインの流れを書きます。**ここで一度止めてレビューしてもらいます。** 一本で通すとはいえ、実装後に設計を覆すのが一番高くつくので、チェックポイントはここ一箇所だけ置かせてください。

### フェーズ3: 実装
下から積みます。各段で動く状態を保つので、途中で仕様変更が入っても手戻りが限定されます。

1. ドメインモデル + 永続化(マイグレーション込み)
2. 通知作成 API と一覧/既読 API
3. 配信アダプタ(まずアプリ内のみ、インターフェースだけ切っておく)
4. UI(一覧、未読バッジ)
5. テスト、シード、ローカル起動手順

### フェーズ4: 運用面
再送・失敗時の扱い、通知の保持期間、ログを最後に詰めます。

## 先に決めたいこと

リポジトリが空なので、これだけは私の一存で決めると影響が大きいです:

1. **技術スタック** — 社内に既存の標準はありますか(例: TypeScript/Next.js、Go、Python/Django など)。指定がなければ TypeScript + Next.js + PostgreSQL で組みます。
2. **チャネル** — 初回はアプリ内通知だけで足りますか。メール/Slack が初回から必要だと設計の重心が変わります。
3. **リアルタイム性** — 未読の反映はポーリング(数十秒遅延)で許容できますか。即時が必須なら SSE/WebSocket を入れます。
4. **認証基盤** — 既存の SSO やユーザーテーブルに乗せますか、それともこのリポジトリ内で完結させますか。

1〜4 に回答をもらえればフェーズ2の設計文書から着手します。「任せる」であれば上記のデフォルト(TS/Next.js、アプリ内のみ、ポーリング、リポジトリ内完結)を仮定して設計を書き、その仮定を文書の冒頭に明記します。
```

## 04-finish-to-pr  with 1.00 / without 1.00 / Δ +0.00

### arm: with / turns 12 / checks-precede-push=PPP, confirms-before-outward-action=PPP

```
Here's the order — nothing has run yet, this is the plan stopped before execution.

## In plain words

1. **Host precheck** — `gh` auth, scopes, `rig-wb`. Advisory only; it reports gaps and doesn't block.
2. **Register the task + cut an isolated worktree.** Your main working tree is never touched until you accept.
3. **verify** — build, lint, tests, behind an acceptance gate. It loops until green, or escalates to you after 2 tries.
4. **review** — four reviewers in parallel: security, design, test, behavioral-correctness. A REJECT stops the flow. **Nothing has been pushed at this point** — this is the gate your constraint maps to.
5. **I stop and show you** the diff and the verdicts, and ask before pushing.
6. **pr** — `git push` then open the PR. Only after you say go.
7. **merge is not in scope.** You said "through to a pull request", so the flow is sliced to stop at `pr`.

Three separate things enforce "nothing pushed unchecked": the `pre-push-review` policy on the review step, `pr-hygiene` on the pr step (pre-push review mandatory, no `--no-verify`, no force-push to main), and the gate itself — the code refuses to accept while any criterion is failed or pending, and a recorded REJECT blocks it outright.

```
## rig --plan

recipe: release-flow | autonomy: interactive | backend: manual | review: on
diff: 不明 → size: S（既定）  ← could not measure; see blocker below
description: 標準リリースフロー。intake→design?→implement→verify→review?→pr→merge
flags: --review
slice: --from verify --to pr

| # | step    | instruction     | pattern         | gate            | personas                                                                          | policies                 | output_contract | condition               |
|---|---------|-----------------|-----------------|-----------------|-----------------------------------------------------------------------------------|--------------------------|-----------------|-------------------------|
| 1 | verify  | verify          | serial          | acceptance-gate | implementer                                                                       | risk-based-testing, ci-cost | —            | —                       |
| 2 | review  | parallel-review | parallel-fanout | acceptance-gate | security-reviewer [agent], design-reviewer [agent], test-reviewer [agent], behavioral-correctness-reviewer [agent] | pre-push-review | review-verdict  | --review [✓ 実行: --review 解決] |
| 3 | pr      | pr              | serial          | —               | orchestrator                                                                      | pr-hygiene, branch-strategy | —            | —                       |

### Gate: ゲート条件

**step: verify**（acceptance-gate · max_retries: 2 [既定]）
- [ ] build が成功
- [ ] lint 0 件
- [ ] 関連テスト green

**step: review**（acceptance-gate · max_retries: 2 [既定]）
- [ ] 4-way review に REJECT が無い
- [ ] APPROVE_WITH_CONDITIONS のマージ前必須条件をすべて反映済み

[既定] = 汎用既定値（2）

### Reviewer Fan-out: レビュアー集合

**step: review**（pattern: parallel-fanout）
  - security-reviewer [agent]
  - design-reviewer [agent]
  - test-reviewer [agent]
  - behavioral-correctness-reviewer [agent]

steps: 3（うち condition 付き=1 / gate=2 / acceptance retries 上限: 4）| RUN はしない
```

## One blocker, and it's a real one

I can't actually run this yet. Two findings, both verified rather than assumed:

- **There is no shell tool in this session** — not for me, and not for the subagent I dispatched to probe the repo. `ToolSearch` for Bash returns nothing. Every step above needs `git`, `gh`, and `workbench.py`.
- **The working directory looks empty.** `/tmp/claude-eval-FmzXpj/home/cwd` has no visible files and no `.git/HEAD` or `.git/config`, even though the environment declares it's a git repo. An empty read isn't proof of a clean tree — it's proof I can't see the repo.

So I couldn't measure the diff, and the `size: S` above is the fallback default, not a measurement. It doesn't change the flow (`--review` forces the review step on regardless of size), but I'd want the real number before the pr step, because `pr-hygiene` requires splitting anything over 400 lines.

Which is it — should I get a shell enabled, or is the repo at a different path than the declared cwd?
```

### arm: without / turns 7 / checks-precede-push=PPP, confirms-before-outward-action=PPP

```
Here's the order I'd run it in — with your gate at step 6, so nothing leaves the machine before you've seen the checks.

1. **Survey the branch** — `git status`, `git log main..HEAD`, `git diff main...HEAD`. Confirm what's actually on it and that nothing's uncommitted or stray.
2. **Commit leftovers** — anything in the working tree gets committed or explicitly set aside, so the diff under review is the diff that ships.
3. **Run the checks locally** — build, tests, lint/typecheck, per whatever the repo configures.
4. **Review the diff** — `/code-review` on the branch diff; `/security-review` too if it touches auth, input handling, secrets, or dependencies.
5. **Fix findings, re-run step 3** — loop until clean.
6. **Report back to you** — check results, review findings, what I changed. Still entirely local; this is your go/no-go.
7. **Push** — `git push -u origin <branch>`. First outward-facing action, only after your go-ahead.
8. **Open the PR** — `gh pr create`, title and body drawn from the commits and the diff.
9. **Watch CI** — `gh pr checks --watch`, fix failures and repeat 3→7 if anything red.

Two things block me from actually starting, though:

- **I have no shell in this session.** My tools here are file reading and search only — no Bash, so I can't run git, the test suite, or `gh`. Steps 1–3 and 7–9 aren't executable by me as configured.
- **There's nothing on the branch.** `/tmp/claude-eval-vbd9Yg/home/cwd` is empty, and the repo snapshot shows no commits. The current branch is also `main`, which can't be the source branch for a PR into `main` anyway.

If this is the wrong working directory, point me at the real one. If it's right, the branch needs enabling a Bash tool and actual commits before any of the above applies. I can still do step 4 — reviewing a diff — if you paste it or give me a path to read.
```

## 05-preserve-behaviour  with 0.50 / without 0.50 / Δ +0.00

### arm: with / turns 7 / gate-blocks-progression=FFF, pins-behaviour-before-rewriting=PPP

```
金額計算まわりは「振る舞いを変えない」ことの証明がいちばん難しいところなので、段取りはテストを先に厚くする形になります。おすすめの流れはこれです。

**1. 現状を固定する（特性テスト）**
既存テストがどこまで守ってくれているかを確認して、足りない分を先に書きます。金額系なら特に、丸め方向（切り捨て/四捨五入/銀行家丸め）、端数の出る割り勘や税計算、0・負値・上限付近、通貨や小数桁の混在あたり。ここで書いたテストが「壊れていない」の唯一の物差しになります。**現行実装のまま green** になることまで確認して初めて安全網です。

**2. 書き換えは小さく刻む**
1ステップ1意図で、各ステップごとにテストを回します。まとめて書き直すと、落ちたときに原因の切り分けができなくなります。

**3. 新旧の等価性を機械で突き合わせる**
特性テストの網の目をすり抜ける差分に効くので、旧実装を一時的に残して、ランダム入力や境界値で新旧の出力を比較するテスト（property-based / differential test）を1本入れるのを勧めます。金額計算は入力空間が有限で素直なので、これがよく効きます。

**4. レビューと受け入れ判定**
rig 側では隔離 worktree で作業して、`behavioral-correctness` レビュー（二実装の等価性が観点に入っています）を通し、acceptance gate が通ってから accept。メインの作業ツリーは accept まで一切触りません。

---

ひとつ確認させてください。作業ディレクトリ（`/tmp/claude-eval-2EdgMN/home/cwd`）が空で、対象のコードが見つかりませんでした。リポジトリのパスを教えてもらえますか。

それが分かれば、まず**既存テストのカバー範囲と、その金額ユーティリティの実装を読む**ところ（読み取りのみ）から始めます。その結果を見て、特性テストをどこまで足すかを相談してから書き換えに入る、という順で進めるのはどうでしょう。
```

### arm: without / turns 3 / gate-blocks-progression=FFF, pins-behaviour-before-rewriting=PPP

```
作業ディレクトリ (`/tmp/claude-eval-YJm5m7/home/cwd`) が空で、リポジトリにもコミットがまだない状態でした。対象ファイルの場所を教えてもらえれば具体的な段取りに落とし込みます。先に、金額計算を振る舞い不変で書き換えるときの標準的な進め方を示します。

## 段取り

**1. 書き換える前に、今の振る舞いをテストで固定する**

これが一番重要です。リファクタ後にテストを書くと「新しい実装が正しいことのテスト」になってしまい、壊れた箇所を検出できません。既存実装の出力をそのまま記録するテスト（characterization test）を先に用意します。

金額計算で特に押さえたい入力:
- `0`、負の金額、最小単位（1円 / 1セント）
- 端数が出るケース — 丸めの方向（切り捨て / 四捨五入 / 銀行家丸め）、`.5` ちょうど、負値の丸め
- 税率・割引・分割の組み合わせ、合計と内訳の突き合わせ
- 桁の大きい値、浮動小数の誤差が出る組み合わせ（`0.1 + 0.2` 系）
- 異常系: `null` / `undefined` / `NaN` / 文字列、通貨違いの混在

**2. カバレッジで穴を確認**

分岐カバレッジを取って、これから触る行が全部テストで通っているか見ます。通っていない分岐は、書き換えで壊しても誰も気づけない部分です。

**3. コミットを分ける**

- コミットA: テスト追加のみ（実装は一切触らない）
- コミットB以降: リファクタのみ（テストファイルは1文字も変えない）

テストを変えずに通ることが「振る舞いを変えていない」根拠になります。リファクタ中にテストを直したくなったら、それは振る舞いが変わったサインです。

**4. 小さいステップで、毎回テストを走らせる**

名前変更 → 関数抽出 → 重複削除、の順に機械的な変換から。1ステップごとに実行して、緑のままコミットします。まとめて書き直すと、失敗したときに原因の切り分けができません。

**5. 演算順序は変えない**

浮動小数を使っている場合、「読みやすくするため」に `a * b / c` を `a * (b / c)` に整えるだけで結果が変わります。丸めをかける位置も同様です。見た目の改善と計算の変更を混ぜないでください。

**6. バグを見つけても、この作業では直さない**

怪しい丸めや符号の扱いを見つけたら、現状の挙動をそのままテストに書いて、別途報告します。修正はリファクタとは別のコミット・別の判断です。

## 追加でやれる確認

旧実装を一時的に `legacy.ts` などに残し、ランダム生成した入力を両方に流して出力を全件比較する差分テストが、金額計算では効きます。手で書いたテストが見落とす端数ケースを拾えます。比較が通ったら旧実装を削除します。

---

対象ファイルのパスと、テストフレームワーク・パッケージマネージャが何か教えてください。そこから実際のコードを見て、テストの穴と危ない箇所を洗い出します。
```

## 06-neg-asyncio-explain  with 0.50 / without 1.00 / Δ -0.50

### arm: with / turns 3 / explains-the-difference=PPP

```
コード変更の要らない質問なので、ワークツリーもゲートも起こさずそのまま答えます。

## 一言でいうと

- **`gather`** = 「全部終わるまで待って、**結果を入力順のリストで**返す」高水準の便利関数
- **`wait`** = 「待機の**制御**を返す」低水準のプリミティブ。返るのは `(done, pending)` の**集合**で、結果の取り出しは自分でやる

## 主な違い

| | `asyncio.gather` | `asyncio.wait` |
|---|---|---|
| 引数 | `*aws`（コルーチンは自動で Task 化） | **Task/Future の iterable**。コルーチンを渡すのは 3.8 で非推奨 → **3.11 で TypeError** |
| 戻り値 | 結果の **list、入力順** | `(done, pending)` の **set、順序なし** |
| 例外 | 既定で**最初の例外をそのまま送出**。`return_exceptions=True` で結果リストに混ぜて返す | **送出しない**。例外は done の各 Task の中に入っているので `t.result()` を呼んだ時に出る |
| タイムアウト | なし（`asyncio.timeout` / `wait_for` で包む） | `timeout=` あり。ただし**時間切れでも例外は出ず**、単に pending が返るだけ |
| 部分待ち | できない（全部待つ） | `return_when=FIRST_COMPLETED / FIRST_EXCEPTION / ALL_COMPLETED` |

## 実務で刺さる落とし穴が2つ

**1. `gather` は例外時に兄弟をキャンセルしない**

最初の例外が呼び出し元に飛んできても、残りのタスクは裏で走り続けます。「例外が出たら全部止めたい」なら `gather` では書けません。Python 3.11+ なら `TaskGroup` が正解です（こちらは兄弟を自動キャンセルし、複数例外を `ExceptionGroup` でまとめます）。

```python
async with asyncio.TaskGroup() as tg:      # 3.11+
    t1 = tg.create_task(f())
    t2 = tg.create_task(g())
# 抜けた時点で全部完了。どれかが失敗すれば他はキャンセルされる
```

**2. `wait` の `timeout` は pending をキャンセルしてくれない**

```python
done, pending = await asyncio.wait(tasks, timeout=5)
for t in pending:
    t.cancel()          # ← これを忘れるとタスクが漏れる
```

`wait_for` と混同しやすいところです。`wait_for` は時間切れで `TimeoutError` を投げ、対象をキャンセルしてくれます。`wait` は「観測するだけ」で何もしません。

## 使い分けの指針

- **N 個の結果を順番付きで全部欲しい** → `gather`（またはエラー伝播を厳密にしたいなら `TaskGroup`）
- **最初に終わった1件だけ欲しい / 進捗を刻みながら処理したい** → `wait(..., return_when=FIRST_COMPLETED)`、あるいは完了順に取りたいだけなら `asyncio.as_completed`
- **全体にタイムアウトを掛けたい** → `async with asyncio.timeout(5):` の中で `gather`（3.11+）

新規コードでは、素の `gather` より `TaskGroup` + `asyncio.timeout` の組み合わせが推奨されます。`wait` は「低水準の待機制御が本当に要る場面」に取っておくのが実用的です。
```

### arm: without / turns 1 / explains-the-difference=PPP

```
`asyncio.gather` と `asyncio.wait` は「複数の待機可能オブジェクトを並行実行する」点は同じですが、**結果の返し方・例外の扱い・キャンセルの責任**がまったく違います。

## 一言でいうと

- **`gather`** = 「全部終わるまで待って、結果をまとめて受け取る」高レベルAPI
- **`wait`** = 「どれが終わってどれが残っているかを教えてくれる」低レベルAPI。結果の取り出しもキャンセルも自分でやる

## 比較表

| | `asyncio.gather` | `asyncio.wait` |
|---|---|---|
| 引数の渡し方 | 可変長引数 `gather(a, b, c)` | イテラブル1つ `wait({t1, t2})` |
| コルーチンを直接渡せるか | 渡せる（自動で Task 化） | **不可**（3.11で削除済み。Task/Future のみ） |
| 戻り値 | 結果の **リスト（引数と同じ順序）** | `(done, pending)` の **2つの集合**（順序なし） |
| 例外 | デフォルトで呼び出し側に**送出** | **送出しない**（各 Task から自分で取り出す） |
| タイムアウト | なし | `timeout=` あり |
| 途中で切り上げ | 不可（常に全完了待ち） | `return_when=` で可能 |
| キャンセル | gather をキャンセルすると子も全部キャンセル | 自動キャンセルは**しない** |

## 例外の扱いがいちばんの落とし穴

```python
# gather: 最初の例外がそのまま raise される
results = await asyncio.gather(a(), b(), c())          # ← 例外が飛ぶ
results = await asyncio.gather(a(), b(), c(), return_exceptions=True)
# → [結果, 例外オブジェクト, 結果] のように例外も値として並ぶ
```

注意点として、`gather` で例外が送出されても**他のタスクは自動キャンセルされず裏で走り続けます**。放置すると "Task exception was never retrieved" 警告や、リーク（未完了タスク）の原因になります。

```python
# wait: 例外は絶対に飛んでこない。自分で取り出す必要がある
done, pending = await asyncio.wait(tasks, timeout=5)
for t in done:
    if t.exception():          # ← これを忘れると例外が握り潰される
        ...
    else:
        t.result()
for t in pending:              # ← timeout で残ったものは自分でキャンセル
    t.cancel()
```

`wait` はタイムアウトしても pending のタスクを**キャンセルしません**。ここが `wait_for` との決定的な違いです。

## `return_when` で早期リターン

```python
done, pending = await asyncio.wait(
    tasks,
    return_when=asyncio.FIRST_COMPLETED,   # 他に FIRST_EXCEPTION / ALL_COMPLETED（既定）
)
```

「どれか1つ速いレスポンスが返ればいい」「最初の失敗で打ち切りたい」といった用途は `gather` では書けず、`wait` の領分です。

## 使い分けの指針

1. **Python 3.11 以降なら、まず `asyncio.TaskGroup` を検討**してください。`gather` の上位互換で、どれかが失敗したら残りを自動でキャンセルし、`ExceptionGroup` でまとめて投げてくれます。リーク対策として現在の推奨形です。

    ```python
    async with asyncio.TaskGroup() as tg:
        t1 = tg.create_task(a())
        t2 = tg.create_task(b())
    # ここを抜けた時点で全完了、失敗があれば ExceptionGroup
    print(t1.result(), t2.result())
    ```

2. **`gather`** — 全件成功前提で、結果を**入力順に**揃えて使いたいとき（例: N件のURLを並行取得して順番通りのリストがほしい）。TaskGroup が使えない環境ではこれ。

3. **`wait`** — 「最初の1つだけ」「タイムアウトして残りは後で処理したい」「完了/未完了を区別して細かく制御したい」とき。

4. **`as_completed`** — 終わった順に逐次処理したいなら、`wait` をループで回すより `for fut in asyncio.as_completed(tasks):` のほうが素直です。

5. **タイムアウト目的だけ**なら `wait` ではなく `asyncio.timeout()`（3.11+）か `asyncio.wait_for()` を使ってください。こちらは超過時にきちんとキャンセルしてくれます。

補足: `gather` は `loop` 引数の削除や、3.10以降の「コルーチンを渡すには実行中のイベントループが必要」といった変更があるため、古い記事のサンプルはそのままでは動かないことがあります。
```

## 07-neg-rebase-onto  with 0.50 / without 1.00 / Δ -0.50

### arm: with / turns 3 / explains-the-three-arguments=PPP

```
`git rebase --onto A B C` の3つは、こう読むと迷いません。

| 位置 | 名前 | 意味 |
|---|---|---|
| 第1 | **newbase (A)** | 移植先。コミットを「どこの上に」載せ直すか |
| 第2 | **upstream (B)** | 移植元の土台。**これより後ろ**のコミットが移動対象（B自身は含まない） |
| 第3 | **branch (C)** | 移植する側の先端。省略すると現在のブランチ |

つまり動かすコミットの範囲は **`B..C`**（Bを除きCまで）で、それを **Aの上に付け替える**。最後にHEADとブランチ参照がCに付いたまま新しい場所へ移ります。

引数が2つの通常の `git rebase B` は「newbaseとupstreamが同じ（どちらもB）」の特殊ケースで、`--onto` はその2つを分離できるのが本質です。だから「移動対象の切り出し」と「貼り付け先」を別々に指定したいときに使います。

典型例のイメージだけ:

- `git rebase --onto main topic feature` → topic から分岐した feature の分だけを main の上へ移す（topic の履歴は連れて行かない）
- `git rebase --onto HEAD~3 HEAD~1` → 直近1コミットを残し、間の2コミットを捨てる形で付け替える

覚え方は「**A の上に、B より後の、C までを置く**」。

より詳しく掘るなら、実際の履歴図で範囲がどう切れるかを追うのがいちばん早いですが、今回は説明だけということでここまでにしておきます。
```

### arm: without / turns 1 / explains-the-three-arguments=PPP

```
`git rebase --onto <newbase> <upstream> [<branch>]` の3つです。

| 引数 | 意味 |
|---|---|
| `<newbase>` | **移植先**。コミットを付け替える新しい土台 |
| `<upstream>` | **切り取り位置**。「ここまでのコミットは持っていかない」という境界 |
| `<branch>` | **移動対象**。省略すると現在の HEAD |

## 動く範囲の決まり方

移動するコミットは `<upstream>..<branch>`、つまり **branch から辿れて、upstream から辿れないコミット**です。これが `<newbase>` の上に順に再適用され、最後に `<branch>` がその先端に移ります。

`<upstream>` は「土台」ではなく「除外集合」を決めるためだけに使われる、というのが理解の要点です。通常の `git rebase <upstream>` では移植先と除外境界が同じものになっていて、`--onto` はそれを分離するオプション、という位置づけになります。

## 典型例

```
      A---B---C  topic
     /
D---E---F---G  main
     \
      H---I  feature
```

`git rebase --onto main feature topic` とすると:

- 対象 = `feature..topic` = A, B, C
- これを `main`（G）の上に載せ直す

```
D---E---F---G---A'---B'---C'  topic
     \
      H---I  feature
```

feature から分岐していた topic を、feature を挟まずに main へ移せます。

## 覚え方

> 「`<upstream>` から `<branch>` までを切り取って、`<newbase>` に貼る」

`<newbase>` に `<upstream>` と同じものを渡せば普通の rebase、別のものを渡せば「枝の付け替え」になります。なお `<newbase>` はブランチ名でも任意のコミットでもよく、`git rebase --onto HEAD~3 HEAD~1` のように特定コミットを落とす用途にも同じ規則がそのまま当てはまります。
```
