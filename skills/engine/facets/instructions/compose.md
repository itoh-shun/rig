# instruction: compose

`/rig:go compose <自然文>` と、引数なし／task_type を一意に分類できない入力の対話 composition。
この facet は選択肢を描いて訊くだけで、推薦・既定・状態を判定しない。

1. task_type を分類できるまで「何をしたい？」を一度だけ訊く（実装／レビュー／調査／PR まで）。
2. `rig-wb wb compose-options --type <task_type> [--diff <増減行数>] --json` を一度実行する。
   コマンドが拒否した軸を推測で埋めず、理由をそのまま示して停止する。
3. JSON の `axes` 順（RECIPE / STEP / GATE / BACKEND / MODE）に、全 `candidates` と
   `recommended`、`recommendation_reason` を表示して一度に選ばせる。候補が1件なら
   「選択肢はこれだけ」と明記する。`auto` は常に §4 RESOLVE へ委ねる意味であり、
   この facet で解決し直さない。
4. 選択を既存の recipe キー／等価 flag に写す。STEP は `--only`/`--from`/`--to`/`--skip`、
   BACKEND は `--workflow`/`--orchestrate`、MODE は `--autonomous`、GATE は選んだ gate を持つ
   step 構成として扱う。`--save-recipe` 指定時は §3.5 と resolve §5 の同じ保存経路を使う。
5. 合成後は `facets/instructions/plan` を読み、`--plan` の正準フォーマットを変更せずそのまま
   提示する。確認後だけ RUN する。

`--autonomous` が指定済みなら、この facet を起動せず通常の route → RESOLVE → COMPOSE → RUN
へ進む。対話を挟むことが自律実行の意味を変えてはならない。

この仕組みが保証するのは、選べる候補と推薦根拠を見せ、選択を既存 RESOLVE に渡すことだけ。
選択が良いこと、タスクが成功すること、安全に受け入れられることは保証しない。最後の判断は
acceptance-gate の責務である。
