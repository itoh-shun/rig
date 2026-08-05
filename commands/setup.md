---
description: "rig/setup — rig-wb CLI（pip 版）を pipx / uv / pip で自動導入する。skill として rig を使い始めるときの初回セットアップ。他のプロバイダ (Codex / Cursor / Copilot) からも同じ CLI に委譲するための共通土台。"
argument-hint: "[--yes 確認省略] [--force 再インストール] [--check 検出のみ] [--uninstall] [--ref <branch|tag|sha>]"
---

# rig/setup — rig-wb CLI インストーラ

**まず `rig:engine` skill を Skill ツールで起動し、その SKILL.md（context-minimal・知識層・§6 run-continuity）に従うこと。** このコマンドは入口であり、実処理は `scripts/install.sh` にある（重複定義しない）。

起動後、次の引数を PARSE して installer に渡す:

```
$ARGUMENTS
```

## やること

`scripts/install.sh` を **Bash ツール経由**で実行する。installer は以下を順に行う:

1. **GitHub CLI（任意）の確認**: `gh` 本体があるか / `github/gh-stack` 拡張が入っているか。
   拡張が無ければ導入を**提案**する（`--yes` で確認省略、`--force` で再導入、`--check` は検知のみ）。
   断っても、`gh` 本体が無くても、install はそのまま続く——**必須ではない**。`gh` 本体は system
   パッケージなので勝手には入れない。**認証も要件ではない**——状態を表示するだけで、
   `gh auth login` を実行することも要求することもしない。
2. **環境検知**: `pipx` / `uv` / `pip` のいずれが使えるか（優先順は pipx > uv > pip）。
3. **既存インストール確認**: `rig-wb version` が通れば skip（`--force` で再インストール）。
4. **確認**: どの方法で何を入れるか user に見せてから続行（`--yes` で省略）。
5. **インストール**: git+URL 経由で `github.com/itoh-shun/rig.git` から取得。
6. **検証**: `rig-wb version` が返ればOK、PATH に無ければ `pipx ensurepath` / `~/.local/bin` の追加を案内。

## gh + gh-stack は任意（必須ではない）

**`gh` 本体も `github/gh-stack` 拡張も rig の必須要件ではない。** 無くても
`workbench new` / `orchestrate run|init|ab` / `queue go` はそのまま動く。無いときは
stderr に**一行の案内**が出るだけで、止まらない。

かつては必須にしていたが、その根拠（stacked branch の cascade rebase を `gh stack` に委譲する）は
実測で崩れた。`gh stack` はブランチ切り替えを checkout で行うが、git は**他の worktree が握っている
ブランチの checkout を拒否する**。rig はタスクごとに worktree を作るため、対象ブランチは常に
worktree に握られている:

```
$ gh stack rebase --no-trunk
✗ could not start rebase of task2 onto task1: failed to run git:
  fatal: 'task2' is already used by worktree at '.../wt2'
```

worktree 隔離は rig の安全性の中核で譲れないので、必須にした当の操作ができない側を降ろした。
cascade は各 worktree の中で素の git（`git -C <child> rebase --onto ...`）で行う。
`gh stack` に今も価値があるのは**公開側**（stack の宣言・`submit` / `push`）で、PR を作らない
運用にはそもそも不要——だから「一行の案内」であって「ゲート」ではない。

**認証と remote も当然必須ではない。** `gh stack` のローカル操作は未認証・remote 無しで動き、
GitHub に触るのは `push` / `submit` / `sync` だけ。認証状態は `gh-check` が**表示するだけ**。

現在の状態はいつでも次で確認できる（これは明示的な問い合わせなので、答えは常に全部出る）:

```
rig-wb gh-check           # exit 0=OK / 3=gh 未導入 / 5=gh-stack 未導入（認証状態は表示のみ）
rig-wb gh-check --json
```

一行の案内が不要なら `RIG_SKIP_GH_CHECK=1` で黙らせる（黙るだけ——止めるものは元々無い。
`gh-check` と `/rig:setup` は「環境を教えろ」という明示の要求なので、この変数では黙らない）。
実装は `rig_workbench/gh_requirement.py`（single source of truth、install.sh は同じ状態名を
bash で再現）。

## なぜこれが要るか

rig は **Claude Code 内の skill として動く**（`/rig:go`）だけでなく、`pip install rig-workbench` で入る **`rig-wb` CLI としても動く**。他プロバイダ（Codex plugin / Cursor rules / Copilot extension）の skill も同じ `rig-wb` を叩けば同一の workbench（recipe / gate / accept / dashboard）が使える。**「AI コーディングツールを乗り換えず、その中に skill として住む」** ための土台。

## flag

- `--yes` — 対話プロンプトを省略して install（skill の中で自動実行するときに使う）。gh-stack の導入確認も省略。
- `--force` — 既にインストール済でも再インストール（gh-stack も `--force` で入れ直す）。
- `--check` — 検出だけして終了。exit 0 = install 方法がある、exit 1 = 無い。gh / gh-stack の状態は
  **表示するだけで exit code には影響しない**（任意なので）。
- `--uninstall` — `rig-workbench` を外す（pipx / uv / pip の入れ方に合わせて自動判定）。
- `--ref <ref>` — 特定 branch / tag / commit を指定（既定 `master`）。

## 例

```
/rig:setup                 # 対話で install（初回の推奨）
/rig:setup --yes           # 確認なしで install
/rig:setup --check         # 現環境で install できるかだけ調べる
/rig:setup --force         # 既に入っていても最新に更新
/rig:setup --uninstall     # 外す
/rig:setup --ref v1.3.0    # 特定タグで pin
```

## 実行後にできること

```
rig-wb --help                  # サブコマンド一覧
rig-wb wb board                # workbench の状態
rig-wb plan bugfix             # プラン提示
rig-wb runs --html /tmp/x.html # HTML dashboard
```

これで **Claude Code の外側**からも同じ workbench を叩けるようになる（Codex CLI / Cursor / plain terminal などから `rig-wb ...`）。

## run-continuity（SKILL.md §6）

RUN 中は各ターン冒頭に次の run-status ヘッダを1行必ず再掲すること。中断・質疑・tool 出力の直後でも省かない（可視化＝駆動の証拠）:

```
▸ rig | recipe: <name[tier]|ad-hoc> | step: <id> (<n>/<N>) | gate: <none|pending|passed|REJECT> | backend: <manual|workflow> | mode: <gated|autonomous>
```
