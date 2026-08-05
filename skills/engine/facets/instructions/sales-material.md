# instruction: sales-material

開発資材 → 営業1枚資料の生成 **routing**。コピーの構え（機能→ベネフィット・誇張禁止）は委譲先 persona（`sales/material-writer`）が持つのでここには再掲しない（Native-first）。

**スコープ**: README / CHANGELOG / コード構成 / リリースノート / `plugin.json` の説明等の**開発資材**を読み、実在機能を顧客価値に翻訳した**営業1枚資料**を生成する。

## 手順

1. **資材の収集** — 対象プロダクトの開発資材を集める（既定は現在のリポジトリ：`README*` / `CHANGELOG*` / `.claude-plugin/plugin.json` / `docs/` / 主要機能の入口）。`--from <path>` 指定があればそこを優先。長文・コード全文は親 context に引き込まず、subagent に対象を渡して要点を抽出させる（context-minimal）。
2. **固有知識の注入** — `facets/knowledge/sales-domain/`（自社固有：ICP・価格・差別化・競合）があれば材料として渡す（§5 COMPOSE の知識注入）。無ければ汎用で書き、ICP・価格は `[要記入]` プレースホルダにする。
3. **生成の dispatch** — `sales/material-writer` を合成して subagent に渡す。**実在機能のみ・機能→ベネフィット翻訳・課題ドリブン・AI 臭禁止**で1枚資料を書かせる。各訴求は出所（どの機能/リリース）を `出所` 列で示させる（誇張・捏造の防止）。
4. **構造化** — 出力は `output-contracts/sales-collateral`（A. 営業1枚資料）に従わせる。
5. **接続（任意）** — 生成後、`/rig:dev --recipe de-ai-smell` に通すと AI 臭をさらに落とせる（営業コピーの仕上げ）。荷電スクリプトも要るなら `call-script`（`/rig:sales --script`）へ。

## ガード

- **捏造機能・盛った実績を書かない**（資材に根拠が無い訴求は出さない）。
- **不明は `[要記入: …]`** にする（価格・実績・社名を埋めた風にしない）。
- 技術仕様の羅列にしない（買い手は結果を買う＝ベネフィットに翻訳する）。
