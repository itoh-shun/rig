# policy: pre-push-review

## facet: policy / pre-push-review

`git push` または PR 作成の直前に必ず実行するレビューポリシーです。プロンプト末尾への注入を前提とします。

### 適用タイミング

- `git push` を実行する前
- `gh pr create` を実行する前

### 手順

1. `pr-pre-push-review` skill を実行してコードレビューを行う。
2. レビュー結果に **BLOCKER** または **IMPORTANT** が含まれる場合は push を中止する。
3. 指摘事項を修正してから再度 `pr-pre-push-review` を実行し、クリアを確認する。
4. BLOCKER/IMPORTANT がゼロになった時点で push を行う。

### 禁止事項

- レビューを省略して push しない。
- BLOCKER/IMPORTANT を未修正のまま push しない。
- `--no-verify` 等でフックをスキップしない（明示的な許可がある場合を除く）。
