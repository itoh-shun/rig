# instruction: scenario-vet

シナリオの**検閲（vet）**の routing。**新しい reviewer を作らず、既存ペルソナ＋知識を掛け合わせて**シナリオを検める（ユーザー指定の設計）。各レビュアーの評価軸は委譲先が持つ（Native-first）。

**スコープ**: `scenario-write` が書いたシナリオを、映像化の前に「AI 臭・誇張・フックの弱さ・ブランド/炎上リスク」で検閲し、収束させる。

## 検閲の構成（既存ブリックの掛け合わせ）

`pattern: parallel-fanout` で次の**既存**ペルソナを並列起動する：

1. **`ai-smell-reviewer`（＋ knowledge `ai-writing-smells`）** — VO・テロップ・ログラインの **AI 臭／空ワード（「革命的」「次世代」「シームレス」等）／テンプレ臭／過剰な煽り**を検出。`de-ai-smell` と同じ知識を効かせる（§5 COMPOSE の知識注入：記述形を Knowledge、規範形を Policy 末尾へ）。
2. **`sns-post-reviewer`** — **フック強度（最初の3秒で掴めるか）／ブランド整合／誇張・炎上・誤認リスク**を判定。投稿の hook/brand/risk レンズが動画シナリオにもそのまま効く。
3. **`engagement-reviewer`** — **動画としての面白さ（引き込み力）**を判定：掴み・テンポ/緩急・意外性・感情のペイオフ・記憶に残る山場・完走/再生/共有したくなるか。**「正しいが退屈」を撃つ専用軸**（AI 臭・ブランドが OK でも、平板で最後まで観ないなら不可）。面白さのための誇張は禁止（事実のまま、見せ方・順番・間で面白くする）。

加えて **source 対応の検閲**（実機能の裏打ち）：各ビートの `source`（実機能）が CHANGELOG/README/コードに**実在するか**を照合し、出荷していない機能・盛った数字を **誇張/捏造**として弾く。

## 手順

1. `scenario-write` の確定シナリオを受け取る（長文は親 context に引き込まない）。
2. 上記3ペルソナを `parallel-fanout` で並列起動（`ai-smell-reviewer` には `ai-writing-smells` 知識を注入）。各出力は `output-contracts/review-verdict` に従わせる（観点は `ai-smell` / `sns-post` / `engagement` を名乗らせる）。
3. **source 対応チェック**を併走（各ビートの実機能が実在するか照合）。
4. `gate: acceptance-gate` で次へ収束させる（未達なら指摘を反映してシナリオを書き直し＝`scenario-write` へ差し戻し、最大 K 回）：
   - **AI 臭・空ワードの指摘が無い**
   - **全ビートが実機能に対応（source 実在・誇張/捏造なし）**
   - **フックが効くと判定される（sns-post-reviewer が hook を可とする）**
   - **ブランド/炎上リスクが許容範囲**
   - **動画として面白い（engagement-reviewer が可）— 退屈でない・最後まで観たくなる・記憶に残る山場が1つある**
5. 通ったシナリオを確定し、`/rig:movie`（`release-movie` / `hyperframes-video`）へ渡せる形で提示する。

## ガード

- **検閲の土台は既存ブリックの掛け合わせ**（`ai-smell-reviewer`＋`ai-writing-smells` × `sns-post-reviewer`）。**面白さ（engagement）だけは既存に当てはまる軸が無いため `engagement-reviewer` を専用に足す**（「正しいが退屈」を撃つ別軸。むやみに reviewer を増やさず、欠けている軸のみ追加する方針）。
- 検閲は**通すための儀式にしない**。誇張・AI 臭・弱いフック・**退屈**が残るなら acceptance-gate で差し戻す（`scenario-write` に反映させて再走）。
- 収束しなければ user へエスカレーション（§6 詰まりガード）。
