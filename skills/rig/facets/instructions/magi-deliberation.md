# instruction: magi-deliberation

MAGI 合議の **routing**。提案（go/no-go を問う決定・設計トレードオフ・リスクある変更の採否など）を 3 号機に並列で諮り、多数決で判決を得る。各号機の評価軸は委譲先（persona facet）が持つのでここには再掲しない（Native-first）。

**スコープ**: これは「決定」を下すモード。コードの逐条レビュー（security/design/test）は `parallel-review`、AI 臭除去は `adversarial-review`。MAGI は「**やるべきか／この案で行くか**」を 3 つの直交した観点（正しさ・守り・価値）で裁く。

## 手順

### ① 議題の確定

評価対象（提案文・設計案・diff・選択肢）を収集し、3 号機に渡す**議題**を1つに確定する。曖昧なら**1問だけ**確認する（捏造で埋めない）。長文（diff・ログ・ファイル全文）は親 context に引き込まず、要点を議題として渡す。

### ② 3 号機への並列諮問（`pattern: parallel-fanout`）

`pattern: parallel-fanout` に従い、1メッセージで 3 つの subagent を同時に起動する。各号機は独立 context で、互いの票を見ずに投票する。

- **MELCHIOR-1（科学者）**: `facets/personas/magi/melchior` を合成 — 技術的な正しさ・整合・実証で投票。
- **BALTHASAR-2（母）**: `facets/personas/magi/balthasar` を合成 — 被害半径・可逆性・安定・将来負担で投票。
- **CASPER-3（女）**: `facets/personas/magi/casper` を合成 — 価値・問題の同定・単純さ・直感で投票。

各号機の出力は `output-contracts/magi-verdict` に従わせる（`判定:` 行を先頭に・自分の評価軸に閉じる）。

### ③ 合議（`pattern: magi-consensus`）

3 票が揃ったら `pattern: magi-consensus` で多数決を集計し、正準出力（MAGI コンソール）で判決を提示する。

- **可決（全会一致 / 2:1）** → 進行。2:1 なら否決号機の懸念を保留事項として明示。
- **条件付可決** → 統合条件を提示し、充足を着手の前提にする。
- **否決** → 停止して否決理由を user へ。先へ進めない。
- **審議継続** → 票を捏造せず、不足情報を user に問うてから再合議。

### ④ 委譲（任意）

判決が可決で、議題が実装・PR などの後続作業を含む場合、その実作業は `/rig:dev` 等の既存フローと subagent に委譲する（MAGI は裁定するだけ・context-minimal）。
