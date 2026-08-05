---
title: ライセンス互換の基礎（import 判断用）
slug: license-compat-basics
aliases: [oss-license-basics]
tags: [license, import, legal]
domain: dev
status: canonical
links: []
reviewed_at: 2026-07-02
sources: ["choosealicense.com", "SPDX License List", "GNU: License compatibility FAQ", "tldrlegal.com"]
---

skill-import の判断（③）が使う**ライセンス実務の基礎カタログ**（事実）。これは法的助言ではない＝**迷ったら「委譲のみ・本文を持ち込まない」に倒す**のが rig の規約。

## 大分類（取り込み判断への影響順）

- **Public domain / CC0 / Unlicense** — 制約ほぼなし。翻訳・再配布可。
- **Permissive（MIT / Apache-2.0 / BSD）** — 翻訳・再配布可。**著作権表示とライセンス文の保持**が条件。Apache-2.0 は加えて変更の明示・NOTICE の継承・特許条項。
- **弱コピーレフト（MPL-2.0 / LGPL）** — ファイル/ライブラリ単位の相互主義。当該部分の改変はソース開示が要る。
- **強コピーレフト（GPL / AGPL）** — 派生物全体に同ライセンスが波及。**翻訳（＝派生物）を permissive なプロジェクトに持ち込むと汚染**になり得る。AGPL はネットワーク提供にも波及。
- **CC 系（文書に多い）** — CC-BY は表示で可・**CC-BY-NC は商用不可・CC-BY-ND は改変（＝翻訳）不可**。
- **ライセンス無し（LICENSE ファイルが無い）** — 「公開＝自由」ではない。**著作権は留保されている**＝再配布・翻訳の権利がない前提で扱う。

## rig の import 判断への対応表

| 上流ライセンス | 委譲（routing のみ） | 翻訳（ブリック化） | 知識のみ（wiki 化） |
|---|---|---|---|
| PD / CC0 | ○ | ○ | ○ |
| MIT / BSD / Apache-2.0 | ○ | ○（出所・ライセンス文を継承） | ○（出典明記） |
| MPL / LGPL | ○ | △（当該部分の条件を確認） | ○（出典明記） |
| GPL / AGPL | ○（参照は自由） | ×（波及リスク）→委譲へ | △（事実の要約は可・本文転載は不可） |
| CC-BY-NC / ND | ○ | ×（NC=用途制約・ND=改変不可） | △（要約のみ） |
| 表記なし | ○ | ×（権利なし前提） | ×（本文を持ち込まない） |

## 実務の注意

- **アイデア・事実 vs 表現** — 著作権が守るのは表現。手法や観点の「事実としての要約」は可でも、**本文の実質的コピーは翻訳でも派生物**。wiki 化は自分の言葉で・出典を明記。
- **デュアルライセンス／ファイル単位の混在** — リポジトリの LICENSE と個別ファイルのヘッダが異なることがある（個別ヘッダ優先）。
- **再 export 時の継承**（skill-export）— import 由来のブリックを書き出すときは上流のライセンス・クレジット継承義務がそのまま付いてくる。
- lock（`skills-lock.json`）に出所を残すのは、この判断を**後から監査可能**にするため。
