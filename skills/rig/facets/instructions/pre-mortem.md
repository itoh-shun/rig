# instruction: pre-mortem

事前検死の **routing**。検死の観点（時制を未来に置く・カテゴリ網羅）は委譲先 persona（`pre-mortem-analyst`）が持つのでここには再掲しない（Native-first）。

**スコープ**: マージ/リリース前の変更・計画・PR を対象に、「**もう本番で壊れた**」前提で失敗モードを逆算し、各々に**最小ガードレール**を対で出す。magi（やるか＝go/no-go）の補完で、こちらは「**どう壊れるか**」を担当する。

## 手順

1. **対象の収集** — 変更（diff / PR / 設計案 / 計画）を1つに確定する。長文は親 context に引き込まず要点を渡す。曖昧なら1問だけ確認。
2. **事前検死の dispatch** — `pre-mortem-analyst` を合成して subagent に渡す（`pattern: serial`）。**本番影響の検知（manifest `production_impact` / auth・migration・security 等）を効かせる** — 影響半径の大きい箇所ほど厚く検死する。
3. **構造化** — 出力は `output-contracts/premortem-report` に従わせる（総合リスク＋失敗モードを可能性×影響でランク＋各モードにガードレール＋最も安く効く1手）。
4. **接続** — 検死結果は読み取り専用の提言。ガードレールの実装に進む場合はその実作業を `/rig:dev`（テスト追加・フラグ・段階導入）等へ委譲する（pre-mortem は炙り出しまで・context-minimal）。

## magi との連携（任意）

「やるべきか」を裁く `/rig:magi` の前後で使うと効く：magi に諮る前に pre-mortem で失敗モードを洗い、Balthasar（母＝守り）の判断材料にする／magi 可決後に出す前の最終保険として回す。

## ガード

- **断定形**で書く（「壊れるかも」でなく「壊れた：原因はこれ」）＝ prospective hindsight で発見率を上げる。
- **各失敗モードに最小ガードレールを対で出す**（恐怖の羅列にしない）。
- 捏造禁止（実際に起こりうるものだけ・誇張は可）。低確率×低影響の空想は載せない。
