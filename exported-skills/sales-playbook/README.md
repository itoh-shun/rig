# sales-playbook

B2C・高額商材の対面営業の実務知識を、Claude Code / Claude Desktop の skill として使える形にまとめたもの。

## 何をする skill か

営業の相談（商談設計、トークスクリプト作成、レビュー、部下育成、組織づくり）を受けたときに、該当する領域の知識を読み込んで助言に使う。11トピック・約5万字。

| ファイル | 内容 |
|---|---|
| `SKILL.md` | 入口。相談内容とページの対応表、使うときの注意 |
| `references/sales-referral-generation.md` | 紹介営業（リファラル獲得）の型 |
| `references/sales-prospecting-cold-calling.md` | 見込み客発掘とテレアポ |
| `references/sales-first-impression.md` | 第一印象・自己紹介・商談場所の設計 |
| `references/sales-discovery-questioning.md` | 購買心理とヒアリング（不満・不安の顕在化） |
| `references/sales-closing-techniques.md` | クロージングと反論処理 |
| `references/sales-price-negotiation.md` | 価格交渉（値下げ要求への対応）※単一根拠 |
| `references/sales-talk-script-practice.md` | トークスクリプトと練習（再現性の作り方） |
| `references/sales-followup-retention.md` | 商談後フォロー ※単一根拠 |
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

- **一次ソース**: [インビクタス岡哲也の売れる営業学](https://www.youtube.com/@okapon5099) が公開する日本語の営業ノウハウ動画69本（自動生成字幕から蒸留）。岡哲也氏は元大手生命保険会社の営業・営業所長・支社長を経て営業研修会社を経営している。
- **蒸留の方法**: 動画1本＝1ページではなく**概念ごとに束ねて**再構成している。原文の逐語コピーではなく、要約と構造化。
- **信頼度の扱い**: 複数の動画で反復される主張のみを断定し、単一の証言に留まるものは `※要確認` を付けている。各ページ末尾の「出典」に、根拠となった動画のIDとタイトルを記載。
- **rig 由来**: この skill は [rig](https://github.com/itoh-shun/rig) の知識層（`knowledge/wiki`）11ページから `/rig:export` 相当の手順で書き出した。import 由来のブリックを含まないため、上流ライセンスの継承義務はない。

### 帰属と第三者コンテンツ

主要ソースは [インビクタス岡哲也の売れる営業学](https://www.youtube.com/@okapon5099) です。各reference末尾には、根拠にした動画のIDとタイトルを記載しています。

本skillは逐語録ではなく、複数動画の内容を概念単位で要約・再構成したものです。ただし、元動画とそこで示される経験・表現の権利がRigへ移転したわけではありません。元動画を引用・転載するときは、動画ごとの権利とYouTubeの利用条件を別途確認してください。Rigおよび本skillは、岡哲也氏や同氏の会社による承認・提携を示すものではありません。

## ライセンス

`LICENSE` を参照（MIT）。MITライセンスが対象とするのは、Rig側で作成した構成・指示・要約文です。元動画その他の第三者コンテンツに対する権利を付与するものではありません。
