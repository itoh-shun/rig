# pattern: acceptance-gate

**determinism-by-gate** — rig の品質保証の核。LLM/agent の実行は非決定的（同じ入力でも出力・品質がばらつく）。だが品質クリティカルな step を「明示した**受け入れ基準**＋検証」で挟めば、**経路は非決定的でも、最終出力の品質は決定的に一定の水準へ収束**する。

## 仕組み（品質収束ループ）

1. **acceptance contract を定義** — step の合否を機械的/観点的に判定できる基準を明示する。例：
   - 機械検証: 全テスト green / lint 0 / build 成功 / 指定 grep が 0 件
   - 観点検証: `review-gate` で 3-way レビューに REJECT が無い
   - 構造検証: `output-contract` の必須項目をすべて満たす
2. **step を実行** — subagent に dispatch（context-minimal）。
3. **基準に照合** — `structured-report` で機械抽出し、基準を満たすか判定。
4. **未達なら収束させる** — 指摘・失敗を反映して**別 subagent で再生成/精緻化を最大 K 回**。良い run も悪い run も「基準を満たすまで」回す。
5. **K 回で未達 → user にエスカレーション**（§6 の「2回詰まりガード」と連動）。**基準未達の出力は次 step へ通さない**（サブ基準の成果物を下流に流さない）。

## 効果

- 生成のばらつきを**ゲートが吸収**する。経路（何回回ったか・どの subagent か）は非決定的だが、**成果物の品質は毎回同じ水準**になる。
- 「たまたま良い回・悪い回」が無くなる＝レビュー往復や手戻りが減り、リリース品質が安定する。

## 既存ブリックとの関係（acceptance-gate は束ねる上位パターン）

| 部品 | 役割 |
|---|---|
| `output-contract` | 受け入れ基準の「形」（何を満たせば合格か） |
| `review-gate` | レビュー観点の合否（REJECT があれば未達） |
| `structured-report` | 結果を機械照合可能にする |
| stuck-guard（SKILL §6） | K 回超のエスカレーション |
| `parallel-fanout` | 検証観点を並列に回す |

acceptance-gate はこれらを「**生成（非決定的）→ 検証 → 未達なら収束ループ → 合格のみ通過**」という1つの品質ゲートに組み上げる。

## 使いどき

毎回ブレては困る step（本番影響変更 / 契約 API / DB migration / リリース / 高 stakes な実装）。recipe の step に `gate: acceptance-gate` を指定し、受け入れ基準を当該 step の `instruction` / `output_contract` に明記する。軽い変更（S/M）には付けない（軽さ既定）。

## K（再試行上限）の目安

- 既定 K=2（超えたら user 判断・無限ループ禁止）。step の **`max_retries` キー**（SKILL §3.5）で指定する。manifest の `default_max_retries` で全体既定も上書きできる。
- K と基準は recipe / manifest で調整可能（厳しい品質が要る step ほど基準を増やす。回数を増やすより**基準を明確にする**方が収束は速い）。
- **`max_retries` と stuck-guard（SKILL §6）の関係**：`max_retries` は acceptance-gate **内の収束ループ上限**（基準未達で何回再生成したか）。stuck-guard は親オーケストレーターが**同一エラーの繰り返し**で発動する**別カウンタ**。両者は独立だが、**どちらも最終的に user へエスカレーション**する。
