# instruction: govern

組織ガバナンス（org / team / project）の routing。診断の作法（何を一級概念にすると何が直るか・どの数字が効くか）は委譲先 persona（`governance-auditor`）と knowledge（`quality-operating-system`）が持つのでここには再掲しない（Native-first）。**判定と記録の正本は `rig-wb govern`**（決定論ランナー）であり、この instruction は「どのコマンドをどの順で回し、結果をどう読むか」だけを持つ。

**スコープ**: 共通ポリシーが実際に届いているか、権限・承認・例外・監査台帳が主張どおり効いているかを**実測**し、乖離を出す。ポリシーの設計・改定を対話で助ける。**rig は判定をしない＝`govern` の出力を読む**（散文で適合を宣言しない）。

## サブモード

| 引数 | 何をするか |
|---|---|
| `audit`（既定） | このリポジトリ（`--all` でチーム横断）の適合性を実測し `conformance-report` で出す。read-only |
| `init` | org/team を束ね、共通ポリシーの雛形を作る。既存の `.rig/access.json` / `.rig/gates.json` があれば `migrate` を先に提案 |
| `policy` | 効いているポリシーの提示・lint（層が緩めていないかの検査）・改定の相談 |
| `approve` | 承認の付与/却下と状態表示 |
| `waiver` | 例外の発行/一覧/取消 |

## 手順

1. **対象確定** — 既定はカレントリポジトリ。`--all <dir>` ならその直下のリポジトリ群を横断（team A/B/C の比較はこちら）。`--plan` で構成を提示して停止。
2. **knowledge 注入必須** — `knowledge/quality-operating-system` を Knowledge 位置に注入して `governance-auditor` を合成。長いポリシー JSON・台帳は親 context に引き込まず subagent に要点抽出させる（context-minimal）。
3. **実測を取る**（rig は判定せず、出力を渡す）：
   ```
   rig-wb govern policy show              # 何層が届いているか
   rig-wb govern policy lint              # 層が上位を緩めていないか（exit 3 = 緩めている）
   rig-wb govern conformance --json       # 1リポジトリの適合性（exit 3 = FAIL あり）
   rig-wb govern rollup --scan <dir> --json   # チーム横断（team A/B/C → 共通ポリシー の表）
   rig-wb govern audit verify             # 台帳の連鎖検証（exit 3 = 改竄検出）
   ```
4. **乖離出し** — 数字を一次資料に、重い順で（`ポリシー未到達 > 台帳破損 > force 率 > 承認の形骸化 > 例外の恒久化 > 権限の集中/空 > 二重管理`）。**印象で採点しない**。
5. **手を出す** — 各乖離に「ポリシー改定 / 権限再配分 / 基準の現実化 / 例外の昇格」のいずれか。**quorum を上げる提案はしない**（効くのは職務分離と鮮度）。
6. **出力** — `output-contracts/conformance-report`（総合行＋層の到達＋チーム別表＋乖離＋最優先の1手）。

## ポリシー設計を相談されたときの型

**単調強化**が唯一の設計制約：org が床、team/project は床の上にしか建てられない（基準の追加・quorum の引き上げ・waiver の短縮・role の絞り込みは可、その逆はすべて `policy lint` が層とフィールドを名指して落とす）。相談ではこれを最初に伝える。

- **最初の org 層は薄く作る**。全社に効く基準だけを置き、チーム固有は team 層へ。org 層が厚いと team が例外を出し続ける運用になり、waiver の恒久化として conformance に出る。
- **`sealed_roles` を使う**。`quality-owner` のような権限の強いロールは封印し、下位層が自分をそこに書き込めないようにする（封印しないと権限管理は自己申告になる）。
- **`non_waivable` を先に決める**。`no_secret_leak` のように「どんな事情でも通さない」基準は、waiver 機構より先に決めておく。
- **共通ポリシーの配り方**: 1つの共有チェックアウトを `$RIG_POLICY_HOME` に置き、各リポジトリの `.rig/org.json` は同じ相対パスを書く。これで team A/B/C が**同一文書**を指す（コピーを配ると必ずドリフトする）。

## ガード

- **「ある」と「効いている」を区別**（ポリシー文書の存在≠適合）。prose のガバナンスは未強制として扱う。
- **承認をゲートの代わりにしない**。人間の承認は acceptance-gate の上乗せであって代替ではない（`determinism-by-gate` を捨てない）。
- **既定で不活性**を壊さない。`.rig/org.json` が無いリポジトリに勝手にガバナンスを入れない（個人開発者がガバナンスの税を払わないのが v1 互換の契約）。
- **根拠は具体箇所と数値**。未確認は「未確認」、計測できない指標は「未計測」と書く（0% と書かない・捏造しない）。
- 監査は read-only。ポリシー・権限・台帳を勝手に書き換えない。変更は `rig-wb govern` の各コマンドを**人間に提示**して実行させる。

## ステージ・ガバナンス（v2.1）

承認は accept だけの話ではない。recipe の step が `actor`（所有する組織ロール）と `human_gate`（人間の承認で止まる）を宣言でき、org policy は `approvals` の `stage:<step-id>` で **recipe が要求していない step にも承認を課せる**。両者は**厳しい方に合成**される（recipe は policy を緩められない＝単調強化の同じ規律）。

```yaml
steps:
  - id: architecture_review
    actor: architect          # このステージの所有ロール
    human_gate: true          # 資格者が署名するまで駐機
```

- 機械ゲートが pass した後も承認が揃うまで `awaiting_approval` で**駐機**する（run-state に永続＝プロセス・セッションを跨ぐ）。`next`/`resume`/`approve` は **exit 3**、`run` も駐機終了なら 3（**失敗ではなく人待ち**）。
- 解放は `rig-wb orchestrate approve <step-id> [--deny] [--note "..."]`。決定は run-state の `step_state[].approvals` と ledger の `stage.approve`/`stage.deny` の両方に残る。
- **職務分離はここでも効く**＝そのステージを実行した本人（`ran_as`）の承認は数えない。行き詰まったら「実行者以外の資格者」を探す。
- `actor` は**実行をブロックしない**（所有ロール外の実行は START 時 WARN と history に記録されるだけ）。これは仕様＝rig が保証できるのは「所有ロールが署名した」ことであって「所有ロールが打鍵した」ことではなく、実行を拒めば CI が壊れるだけで安全性は上がらない。**この点を「未強制の穴」として指摘しない**。

**設計相談で勧める型**：まず org policy の `stage:<id>` で「どの工程に人が要るか」を1〜2個だけ決める（多すぎる human gate は必ず素通し承認になる）。quorum は上げず、**職務分離と鮮度**に効かせる。`actor` だけ宣言して `human_gate` を付けない step は**強制されていない所有**であり、`--validate` が WARN を出す（「ある」と「効いている」の区別をここでも守る）。

## dev フローとの接続

ガバナンスは開発フローの外側ではなく、**accept の内側**にある。`/rig:go` の accept は、ポリシーがあれば①accept 権限 ②承認 quorum（職務分離・鮮度つき）③force 権限 ④例外の有効性 を通ってから squash merge に入る。したがって：

- 「accept できない」と言われたら、まず `rig-wb govern whoami` と `rig-wb govern approve status <task-id>` を読む（権限か承認かで手が違う）。
- 承認は `rig-wb govern approve grant <task-id>`（**著者本人の承認は数えない**・ブランチが動くと失効）。
- `--force` が要るときは、例外を先に取る（`rig-wb govern waiver grant`）。**理由と期限が必須**＝恒久例外はポリシー改定として扱う。
