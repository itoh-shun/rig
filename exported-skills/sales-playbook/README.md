# sales-playbook

B2C・高額商材の対面営業の実務知識を、Claude Code / Claude Desktop の skill として使える形にまとめたもの。

## 何をする skill か

営業の相談（商談設計、トークスクリプト作成、レビュー、部下育成、組織づくり）を受けたときに、該当する領域の知識を読み込んで助言に使う。11トピック。

| ファイル | 内容 |
|---|---|
| `SKILL.md` | 入口。相談内容とページの対応表、使うときの注意 |
| `references/sales-referral-generation.md` | 紹介営業（リファラル獲得）の型 |
| `references/sales-prospecting-cold-calling.md` | 見込み客発掘とテレアポ |
| `references/sales-first-impression.md` | 第一印象・自己紹介・商談場所の設計 |
| `references/sales-discovery-questioning.md` | 購買心理とヒアリング（不満・不安の顕在化） |
| `references/sales-closing-techniques.md` | クロージングと反論処理 |
| `references/sales-price-negotiation.md` | 価格交渉（値下げ要求への対応） |
| `references/sales-talk-script-practice.md` | トークスクリプトと練習（再現性の作り方） |
| `references/sales-followup-retention.md` | 商談後フォロー |
| `references/sales-mindset-antipatterns.md` | 営業のメンタリティと売れない営業のアンチパターン |
| `references/sales-goal-management.md` | 目標設定とスケジュール管理 |
| `references/sales-management-coaching.md` | 営業マネジメントと部下育成 |

## インストール

```
# Claude Code のプラグイン/skill ディレクトリに配置する
cp -r sales-playbook ~/.claude/skills/
```

配置後、営業に関する相談をすると自動的に発動する。明示的に呼ぶ場合は `/sales-playbook`。

## 出所（provenance）

- **内容**: 対面営業の一般的な実務知識を、rig 側で書き起こしたもの。特定の書籍・講座・動画などの第三者著作物を要約・再構成したものではなく、第三者コンテンツを含まない。
- **数値の扱い**: 成約率・アポ率などの水準は業界・商材で桁が変わるため、本文では具体的な数値目標を置かない方針をとっている。統計を引く場合は、利用者側で公的統計や業界団体の調査を出典付き・最新値で確認すること。
- **既知の誤用への注記**: メラビアンの法則のような、営業の文脈で広く誤用されている主張には本文中で注記を置いている。
- **rig 由来**: この skill は [rig](https://github.com/itoh-shun/rig) の知識層（`knowledge/wiki`）11ページから `/rig:export` 相当の手順で書き出した。import 由来のブリックを含まないため、上流ライセンスの継承義務はない。

## ライセンス

`LICENSE` を参照（MIT）。
