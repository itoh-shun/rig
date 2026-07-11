# policy: suppression-memory

## facet: policy / suppression-memory

レビュー所見の**却下学習**（dismissal-learning）ポリシー。反証済み・却下済みの所見をリポジトリローカルな記録として持ち、同じ「検証済みの非問題」を run をまたいで再指摘しない。review fan-out の dispatch 時と `finding-verifier` の検証後に適用する。

### 記録先（正本）

`.rig/review-suppressions.jsonl` — 1行1エントリの追記型 JSONL（`runs.jsonl` と同格のリポジトリローカル記録。レビュー可能・追記のみ）。

エントリ schema:

```json
{"pattern": "<所見の要旨・再照合可能な記述>", "path_glob": "<対象パスの glob>", "reason": "<なぜ非問題か・1文>", "evidence_anchor": "<file:line 等の証拠アンカー>", "source": "verifier|user", "ts": "<ISO8601>"}
```

### ルール

1. **追記契機**: 次のいずれかで1行追記する。(a) `finding-verifier` が所見を **REFUTED** と判定した時（`source: verifier`）、(b) user がレビュー条件・指摘を明示的に却下した時（`source: user`）。`reason` と `evidence_anchor` は必須 — **証拠なしの suppression は書かない**。
2. **dispatch 時の注入**: レビュー fan-out の dispatch 時、`.rig/review-suppressions.jsonl` の有効な suppression を各 reviewer prompt へ「**このリポジトリで検証済みの非問題 — 該当コードに実質的変更が無い限り再指摘しない**」として注入する。照合は `pattern` × `path_glob`。対象 diff が該当箇所に**実質的に**触れている場合は再指摘してよい（コードが変われば suppression の前提も変わる）。
3. **ライフサイクル・ガード（upheld な所見 > suppression）**: 後続 run で `finding-verifier` が **UPHELD** と判定した所見に既存 suppression がマッチした場合、その所見を**サイレントに落とさずゲートへ通し**、当該 suppression に期限切れフラグを付ける（expiry を報告に明示し、記録上も追記で残す）。衝突時は常に suppression が負ける。
4. **性格**: suppression は「検証済み非問題のドキュメント」であり、**ミュートボタンではない**。repo-local・レビュー可能・追記のみ（additive）。エントリの削除・書き換えは通常の diff レビューを通す。

### 禁止事項

- REFUTED / user 却下の裏付けなしに suppression を追記しない。
- UPHELD 所見を suppression を理由にゲートから外さない（期限切れフラグ＋通過が正）。
- suppression を「うるさい指摘を黙らせる」目的で書かない（reason・evidence_anchor が書けないものは対象外）。
