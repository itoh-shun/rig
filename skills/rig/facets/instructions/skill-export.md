# instruction: skill-export

rig で育てたブリック（persona / recipe / pack 一式）を、**独立した Claude Code skill として書き出す**。import（吸収）の対＝**還元**。rig ユーザー以外もそのまま使える self-contained な skill にすることで、rig は「ネットから吸う」だけでなく「ネットに返す」ハブになる。

## 入力

- **対象ブリック**（必須）：`--persona <name>` / `--recipe <name>` / `--pack <名前>`（recipe＋instruction＋persona＋knowledge の一式）。tier 解決（project→user→shipped）で探す。
- `--to <dir>`（任意）：書き出し先ディレクトリ。省略時は `<cwd>/exported-skills/<slug>/` を提案。
- `--dry-run`：生成物の構成とプレビューを提示して停止（書き込みなし）。

## 手順

### ① self-contained 化（rig 依存の除去）

rig ブリックは engine（gate・tier 解決・output-contract 参照）を前提に書かれている。export では**単体で読める skill** に変換する：

- persona の `出力形式は output-contracts/... に従う` → 契約の**本文をインライン展開**（判定行・確信度・証拠アンカーの規律をそのまま埋め込む）。
- `inject: [[slug]]` の wiki 参照 → 参照先ページの内容を**同梱ファイル**（`references/<slug>.md`）にして相対参照に書き換える。
- recipe の step 構造 → SKILL.md の「手順」節に展開（gate は「この基準を満たすまで繰り返す」の散文に翻訳）。
- rig 固有の語彙（facet / tier / COMPOSE 等）を使わない。**rig を知らない読者が読んで動く**ことが合格基準。

### ② skill リポジトリ構成の生成

```
<slug>/
├── SKILL.md          # frontmatter: name / description（トリガー条件を含む）＋本文
├── README.md         # 何をする skill か・インストール方法・出所
├── references/       # 同梱知識（wiki 展開分。無ければ省略）
└── LICENSE           # 下記③
```

### ③ 出所とライセンス（import と対称の誠実さ）

- **provenance を README に明記**：この skill が rig のどのブリックから export されたか、元が import 品なら**さらに上流の出所**（skills-lock.json の source）と、その**ライセンスの継承義務**を確認する。上流ライセンスが再配布を許さない場合は export を**中止して報告**する（import 時の「委譲のみ」判断と同じ基準）。
- 自作ブリックの LICENSE はユーザーに選ばせる（提案既定: MIT）。

### ④ 検証と確認

- 生成した SKILL.md の frontmatter（name / description）が Claude Code の skill として妥当か確認（description に発動条件が書かれているか）。
- `--dry-run` でなければ、構成一覧とプレビューを提示して**承認後に書き込み**（確認必須・冪等・既存ディレクトリは上書きせず差分提案）。
- 完了報告に「公開する場合の次の一手」を添える：GitHub リポジトリ化 → 他の rig ユーザーは `/rig:import <owner>/<repo>` で取り込める（**export → import の輪**）。

## 原則

- **self-contained**：rig を知らなくても動く形でだけ出す（内輪の語彙・参照を残さない）。
- **出所の連鎖を切らない**：import 由来の再 export は上流ライセンス・クレジットを必ず継承する。
- 書き込み＝確認必須・冪等。公開そのもの（リポジトリ作成・push）はユーザーの操作（rig は生成まで）。
