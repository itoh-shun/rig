---
description: "rig/setup — rig-wb CLI（pip 版）を pipx / uv / pip で自動導入する。skill として rig を使い始めるときの初回セットアップ。他のプロバイダ (Codex / Cursor / Copilot) からも同じ CLI に委譲するための共通土台。"
argument-hint: "[--yes 確認省略] [--force 再インストール] [--check 検出のみ] [--uninstall] [--ref <branch|tag|sha>]"
---

# rig/setup — rig-wb CLI インストーラ

**まず `rig` skill を Skill ツールで起動し、その SKILL.md（context-minimal・知識層・§6 run-continuity）に従うこと。** このコマンドは入口であり、実処理は `scripts/install.sh` にある（重複定義しない）。

起動後、次の引数を PARSE して installer に渡す:

```
$ARGUMENTS
```

## やること

`scripts/install.sh` を **Bash ツール経由**で実行する。installer は以下を順に行う:

1. **環境検知**: `pipx` / `uv` / `pip` のいずれが使えるか（優先順は pipx > uv > pip）。
2. **既存インストール確認**: `rig-wb version` が通れば skip（`--force` で再インストール）。
3. **確認**: どの方法で何を入れるか user に見せてから続行（`--yes` で省略）。
4. **インストール**: git+URL 経由で `github.com/itoh-shun/rig.git` から取得。
5. **検証**: `rig-wb version` が返ればOK、PATH に無ければ `pipx ensurepath` / `~/.local/bin` の追加を案内。

## なぜこれが要るか

rig は **Claude Code 内の skill として動く**（`/rig:rig`）だけでなく、`pip install rig-workbench` で入る **`rig-wb` CLI としても動く**。他プロバイダ（Codex plugin / Cursor rules / Copilot extension）の skill も同じ `rig-wb` を叩けば同一の workbench（recipe / gate / accept / dashboard）が使える。**「AI コーディングツールを乗り換えず、その中に skill として住む」** ための土台。

## flag

- `--yes` — 対話プロンプトを省略して install（skill の中で自動実行するときに使う）。
- `--force` — 既にインストール済でも再インストール。
- `--check` — install 方法の検出だけして終了（exit 0 = install 可能、exit 1 = 不可）。
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
