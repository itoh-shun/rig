# instruction: scenario-write

短尺プロダクト動画の**シナリオ執筆**の routing。脚本の作法（フック・感情の弧・show-don't-tell・実機能紐づけ）は委譲先 persona（`scenario-writer`）が持つ（Native-first）。

**スコープ**: リリーストレーラー / before-after / 機能紹介などの動画シナリオ（ログライン・感情の弧・ビートシート・VO 草案・source 対応・目玉・CTA）を書く。この後段に**検閲**（`scenario-vet`）が続く。

## 手順

1. **目的・対象の確定** — 何の動画か（`trailer` / `before-after` / `explainer`）・尺・観客（開発者 / 経営層 等）を確定する。曖昧なら1問だけ確認。
2. **素材の収集** — 対象機能・リリースの根拠（CHANGELOG / README / `plugin.json` / コード）を集める。長文は親 context に引き込まず subagent に要点抽出させる（context-minimal）。
3. **執筆の dispatch** — `scenario-writer` を合成して subagent に渡し、上記スコープのシナリオを書かせる。**冒頭3秒のフック・感情の弧・各ビートの source（実機能）・目玉1つ・CTA1つ**を必ず含めさせる。空ワード・誇張・捏造機能は禁止（後段の検閲で弾かれる前提）。
4. **受け渡し** — 確定シナリオを `scenario-vet`（検閲）へ渡す。検閲を通ったシナリオは `/rig:movie`（`release-movie` の storyboard / `hyperframes-video` の SCENES）の設計図になる。

## ガード

- **実機能に紐づける**（各ビートに source・捏造機能を書かない）・**空ワード禁止**（「革命的」「次世代」等）。
- **show, don't tell**（「速い/便利」と言わず、実画面・実出力・before→after で見せる）。
- 尺・観客に情報量を合わせる（詰め込まない・目玉は1つ）。
