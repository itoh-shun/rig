# pattern: visual-artifacts

**視覚検証（スクリーンショット・DOM スナップショット等）で生成される画像の置き場と処分ルールの正本。** `facets/instructions/visual-verify`（UI diff の目視確認）・`facets/instructions/design-audit`（Playwright での画面取得）はいずれも生の画像を生成するが、これまで保存先・保持期間が定義されておらず、実行のたびにどこかへ溜まっていく余地があった。`patterns/isolated-worktree` が「コードの変更」を隔離・処分するのと対で、本 pattern は「見るためだけの一時成果物」を隔離・処分する。

## 置き場

| 状況 | パス |
|---|---|
| **workbench task に紐づく場合**（`/rig:rig` 経由・task_id が存在） | `<repo>/.rig/runs/<task-id>/visual/` |
| **ad-hoc**（task_id なし。例: `/rig:design <url>` を単独起動） | `<repo>/.rig/visual/adhoc/<YYYYMMDD-HHMMSS>-<slug>/` |

ファイル名は `<step-or-viewport>-<before|after>.png`（例: `verify-before.png` / `verify-after.png`、`design-audit-desktop.png` / `design-audit-mobile.png`）のように、何を撮ったか一目で分かる形にする。DOM スナップショット・axe 結果等の非画像成果物も同じディレクトリに `.json`/`.md` で置いてよい。

## 処分ルール

1. **`.gitignore` 対象**——`.rig/` 全体が gitignore 対象（`/rig:init` が導入先リポジトリへの追加を提案する。`patterns/isolated-worktree` と同じ扱い）なので、`visual/` 配下は自動的にコミット対象外になる。
2. **discard で即時削除**——`workbench.py discard` は worktree/branch に加えて `.rig/runs/<task-id>/visual/` も削除する。画像は「見て判断するための一時証拠」であり、判断結果（pass/fail・指摘内容）は `diff.md`/`log.md` に散文で残る。破棄する試みの生ピクセルまで残す理由はない（run log の JSON/MD 本体は discard 後も残る——画像だけが対象）。
3. **accept は画像を消さない**——accept された task の `visual/` は事後確認のためにその場では残すが、恒久保存を保証するものではない（③の age-based gc の対象になる）。
4. **age-based gc（既定14日）**——`workbench.py gc [--older-than <N>d] [--dry-run]` が `.rig/runs/*/visual/` と `.rig/visual/adhoc/*` を走査し、既定で作成から14日超のものを削除する。task の status（accepted/discarded/running）は問わない——画像は再生成可能な検証手段であり、恒久的な記録ではないため。`--dry-run` で削除対象を確認してから実行できる。承認ダイアログは不要（`.rig/` 配下の gitignore 済み disposable artifact のみを対象とし、ソースコード・worktree・branch には一切触れないため）。
5. **恒久保存が要る場合は明示的に退避する**——リグレッションの証拠として長期保存したい screenshot がある場合は、`visual/` の外（例えば `docs/screenshots/` 等バージョン管理する場所）へユーザーが明示的にコピーする。rig 側は「一時検証成果物は消える」を既定にする。

## 原則

- 画像は**判断のための手段**であり、判断結果そのものではない。結果は常に散文（diff.md・design-verdict 等）に残す——画像が消えても「何が確認されどう判断されたか」は追跡できる。
- 生成コストが低い（再度 Playwright を叩けば撮り直せる）ものは、保存コスト（ディスク容量・リポジトリ肥大化）より処分を優先する。
- 破壊的操作ではない——`gc`/discard が消すのは `.rig/` 配下の gitignore 済み一時ファイルのみ。ソース・コミット履歴・worktree の作業成果物には触れない。

## 既存ブリックとの関係

| 部品 | 役割 |
|---|---|
| `patterns/isolated-worktree` | コード変更の隔離・処分（空間軸）。本 pattern はその画像版 |
| `facets/instructions/visual-verify` | UI diff 確認時にこの置き場規約へ画像を保存する |
| `facets/instructions/design-audit` | Playwright 取得時にこの置き場規約へ画像を保存する |
| `scripts/workbench.py discard`/`gc` | 処分ルールの決定論実装 |
