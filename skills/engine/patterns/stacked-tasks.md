# pattern: stacked-tasks

**1タスク = 1ゲート = 1PR。大きい依頼は `/rig:tasks` で割って積む。**
積んだ層はそれぞれ独立した worktree・独立した acceptance-gate を持ち、親が動いたら
`rig-wb wb cascade` で子を追従させる。`patterns/isolated-worktree`（空間の隔離）と
`patterns/acceptance-gate`（品質の収束）を、**依存のある複数タスク**へ拡張したもの。

この規約は `gh stack` を前提に書かれていたが、その前提は実測で消えた（下記「なぜ自前か」）。
乗る先が変わったので、規約ごと書き直してある。

## なぜ層ごとにゲートを掛けるのか

**3層のスタックならゲートも3回かかる。そして層ごとに受け入れ基準が違ってよい。**
これが「まとめて1本のPRにする」との決定的な差になる。

実例（rig 自身の 1.29.0）——

| 層 | 変更 | その層の受け入れ基準 |
|---|---|---|
| 1 | skill ディレクトリのリネーム（旧 `rig` → `engine`） | **`rig:engine` skill が実際に呼べる** |
| 2 | drill コーパスの同梱 | **wheel にコーパスが入る**（`pip install` 後に materialize できる） |

1本にまとめていたら、通るのは「テストが緑」だけで、**層2の基準は絶対に測られなかった**。
層を割る価値は PR の粒度ではなく、**基準の粒度**にある。1つのゲートに複数の意図を
詰めると、意図の数だけ「測っていない主張」が増える。

逆に言えば、**層ごとに違う基準を書けないなら、その分割は分割の意味がない**（下記
「積まないほうがいい場合」）。

## モデル

rig はタスクごとに worktree を持つが、**タスク間の親子関係のモデルは長く無かった**。
それが `parent_task` / `stack_base` の2フィールド（`rig_workbench/workbench/cascade.py`）。

| フィールド | 意味 |
|---|---|
| `parent_task` | このタスクが積まれている親の task-id |
| `stack_base` | 分岐時点の**親ブランチの先端 sha**。cascade 成功のたびに更新する |
| `base_commit` | 従来どおり**登録時点の記録**（#312）。cascade は書き換えない |

`stack_base` を別に持つのは、`--onto` の upstream 引数が**後から復元できない**ため。
親の履歴が書き換えられた（amend / 親自身の rebase）場合、`merge-base(親, 子)` は
書き換え前の範囲より手前まで遡り、**親が捨てたコミットを子に再生してしまう**。
だから「当時の親の先端」を記録しておく。

`base_commit` を触らないのは、それが履歴的事実の記録であり、`effective_base` が
毎回ライブの merge-base を計算し直すから。**cascade はフィールドを足すだけで、
他のコードが信頼している値は書き換えない。**

## 手順

```bash
# 1. 依頼を層に割る（各層に「その層の受け入れ基準」を書けることを確認する）
/rig:tasks "<大きい依頼>"

# 2. 一番下の層を登録する
rig-wb wb new "layer 1: rename the skill dir" --type refactor

# 3. 上の層を親の上に積む（--base とは排他。--parent が base そのもの）
rig-wb wb new "layer 2: ship the drill corpus" --type feature --parent rig-2026...-layer-1

# 4. 各層は普通のタスクとして回す（隔離 worktree・独自の acceptance-gate）
rig-wb wb gate <task-id> --set ...

# 5. 親が動いたら子を追従させる
rig-wb wb cascade --dry-run     # 計画だけ表示
rig-wb wb cascade               # 実行
```

`rig-wb wb status <task-id>` は `stacked on:` と `children:` を表示する。

## なぜ自前か（`gh stack` を降ろした経緯）

`gh stack rebase` はブランチ切り替えを `git checkout` で行う。git は**他の worktree が
握っているブランチの checkout を拒否する**。rig はタスクごとに worktree を作るので、
対象ブランチは**常に**握られている:

```
$ gh stack rebase --no-trunk
✗ could not start rebase of task2 onto task1: failed to run git:
  fatal: 'task2' is already used by worktree at '.../wt2'
```

worktree 隔離は rig の安全性の中核で譲れない。だから**必須にした当の操作ができない側を
降ろした**（`commands/setup.md`：`gh` / `gh-stack` は advisory へ降格）。

素の git には同じ制約が無い。子の worktree の**中で**回せば checkout は一度も要らない:

```
git -C <子の worktree> rebase --onto <親の新しい先端> <記録した stack_base>
```

`gh stack` に今も価値があるのは**公開側**（stack の宣言・`submit` / `push`）だけ。

## 安全規則（cascade が守ること）

- **上から順に。** 親が動き終えてから子を動かす（幅優先）。逆順だと、孫はこれから
  変わる先端の上に再生されてしまう。
- **未コミットの子は拒否する。stash しない。** rebase はコミットされていない作業を
  動かす。勝手に退避すれば、失われたときに責任を負うのは rig になる。
- **衝突したら `git rebase --abort`。** 子は動かす前の状態のまま残り、**その子の
  サブツリーは丸ごとスキップ**する（動かなかった土台の上に再生しても衝突が増えるだけ）。
- **飛ばしたものは必ず出す。** 黙って半分だけ進んだスタックは、拒否より悪い。
- **親の discard は孤児を警告する。** 親のブランチが消えれば子の土台が消える。

## 積まないほうがいい場合

**否と言わない規約は飾りなので、ここを先に読むこと。**

| 状況 | なぜ積まないか | 代わりに |
|---|---|---|
| **層ごとに違う受け入れ基準を書けない** | 分割の唯一の利得が無い。ゲートを3回通しても、測っているものは1つ | 1タスクにまとめる |
| **層が互いのコードを行き来する**（層2で層1を書き換え、層1でまた層2を…） | 親が動くたび全子が rebase する。cascade は正しく動くが、衝突解決が本来の作業を上回る | 1タスクにまとめるか、境界を引き直す |
| **層1がレビューで却下されうる** | 上の層すべてが土台ごと消える。積んだ時間は全部無駄になる | 層1を先に単独で通してから積む |
| **並行に走らせたいだけ**（依存が無い） | スタックは依存の表現。依存が無いなら順序を強制する理由が無い | `/rig:queue add` → `go --provider rig`（独立タスクの並列） |
| **3層を超える** | cascade は動くが、下が1つ動くたび全層が rebase する。人間が全層の diff を同時に把握できる上限が実質3層 | 一番下を先にマージしてスタックを浅くする |
| **その日のうちにマージしない** | 積んだまま放置した層は、base の移動で毎日 rebase が要る。stale なスタックは負債 | 層を減らす／先に下をマージする |

判断の基準は一つ:
**「この層に、他の層とは違う受け入れ基準を書けるか」。書けないなら積まない。**

## 参照

| 用途 | 参照先 |
|---|---|
| 隔離 worktree・run state・accept/discard | `patterns/isolated-worktree` |
| 受け入れ基準と収束ループ | `patterns/acceptance-gate` |
| 依頼を層に割る | `facets/instructions/task-plan`（`/rig:tasks`） |
| 依存の無い並列実行 | `commands/queue`（`/rig:queue`） |
| `gh` / `gh-stack` の位置づけ | `commands/setup` |
| 実装（親子モデル・cascade・安全規則） | `rig_workbench/workbench/cascade.py` |
