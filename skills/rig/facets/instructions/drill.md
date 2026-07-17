# instruction: drill

**reviewer 検出率の実測（ミューテーション・ドリル）＋ persona 回帰リプレイ。** `runs --personas` の剪定ヒント（REJECT ゼロ＝怪しい）は間接指標に過ぎない。drill は**既知のバグ（種）を意図的に注入した diff** で review fan-out を走らせ、**どの reviewer が何を・どれだけ正確に検出したか**を数字にする。ペルソナ品質を意見でなく検出率・重大度精度・説明の質で測る。

## 入力

- `--seeds <n>`（任意・既定 5）：注入する種の数。
- `--corpus standard|project|all`（任意・既定 `standard`・#270）：種の選定元。`standard`＝下記①の**標準コーパス**（plugin 同梱・リポジトリを問わず同じ物差し）のみ。`project`＝プロジェクト固有コーパス `.claude/rig/drill-corpus.md`（①と同じ列構成の表）のみ。`all`＝両方（結果の各 run 行は `corpus` タグで区別されるため混ざらない）。project コーパスが無いのに `project`/`all` を指定したら「無い」と明示して standard に fallback する。
- `--clean`（任意）：**クリーン・コントロール専用モード**（下記③-a）。バグの種を一切注入せず、no-bug diff だけで同じ fan-out を走らせて per-persona の `clean_fp_rate` を実測する。**省略時（既定）はミックスモード**＝種入りの合成 diff に加えてクリーン diff を1本混ぜ、検出率と `clean_fp_rate` を同一 run で測る。
- `--personas <a,b,…>`（任意）：試す reviewer 集合。省略時は 3-way＋manifest `default_personas`。
- `--replay [<persona>]`：**回帰リプレイモード**（下記④）。種を注入せず、アーカイブ済みの過去 diff に再実行して verdict の差分を見る。
- `--verify-findings`：反証者（`finding-verifier`）も同時に測る（正しい種を REFUTED したら反証者の失点）。

## 手順

### ① 種の選定（標準コーパス・観点対応表・期待 severity／blocking つき）

reviewer の観点に対応した**バグの種カタログ**から選ぶ（各種は「どの観点が検出すべきか」「本来つけるべき severity」「Blocking か Non-blocking か」の期待値を持つ＝答案キーの一部。**期待 blocking は期待 severity から機械的に導出**する：`Critical`/`High` → Blocking、`Medium`/`Low` → Non-blocking）。

下表は **標準コーパス**（#270）——plugin 同梱・言語非依存で、どのリポジトリでも同じ物差しとして使える正準の種セット。`corpus_version: 2`（v1＝コード系18 class、v2＝散文/設計系9 class を追加）。**更新手順**：行を追加/変更したら `corpus_version` を上げる → `validate.py` がコーパス整合性（severity/blocking の値域・cwe/odc と観点の非空・バージョンマーカーの存在）を機械チェックする。計測はローカル完結（コーパスは同梱データであり外部送信しない）：

| 種の class | 例 | cwe/odc | 検出すべき観点 | 期待 severity | 期待 blocking |
|---|---|---|---|---|---|
| 認可漏れ | ID 直指定で他者リソースに到達する分岐 | CWE-639 | security | High | Blocking |
| インジェクション | 外部入力を未エスケープで SQL/コマンドへ | CWE-89 | security | Critical | Blocking |
| XSS | 外部入力を未エスケープで HTML/DOM へ出力 | CWE-79 | security | High | Blocking |
| パストラバーサル | 外部入力を検証せず path 結合（`../` で外に出られる） | CWE-22 | security | High | Blocking |
| ハードコード秘密 | API キー・パスワードのソース直書き | CWE-798 | security | Critical | Blocking |
| 安全でないデシリアライズ | 信頼できない入力を pickle・eval 系で復元 | CWE-502 | security | Critical | Blocking |
| 認証欠落 | 認証必須のはずのエンドポイントに認証チェックなし | CWE-306 | security | Critical | Blocking |
| N+1 / 全件ロード | ループ内クエリ・LIMIT なし SELECT | ODC-performance | performance | Medium | Non-blocking |
| 無制限リソース消費 | 上限なしの読み込み・再帰・確保（zip 展開・無限リトライ） | CWE-400 | performance | Medium | Non-blocking |
| 例外の握りつぶし | 空 catch・エラーの黙殺 | CWE-390 | observability | Medium | Non-blocking |
| 競合 / TOCTOU | check と use の間に状態が変わる（存在確認→別プロセスが削除→使用） | ODC-timing（CWE-367） | design / observability | High | Blocking |
| 破壊的変更 | 公開 API の signature 変更（semver なし） | ODC-interface | api-compat | High | Blocking |
| 片道 migration | down なしの破壊的 ALTER | ODC-data | migration | High | Blocking |
| テスト欠落 | 金銭計算の分岐にテストなし | ODC-checking | test | Medium | Non-blocking |
| 境界 off-by-one / 条件反転 | `<`↔`<=`・`==`↔`!=`・境界値の取り違え | ROR・LCR（mutation operator） | test / cognitive-economist | High | Blocking |
| ドキュメント虚偽化 | README のコマンド例が動かなくなる変更 | ODC-documentation | docs | Low | Non-blocking |
| 過剰抽象 | 1箇所でしか使わない 3層の抽象 | ODC-design | design / lazy-senior | Low | Non-blocking |
| 誤誘導命名 | 実態と逆の意味の関数名 | ODC-design | cognitive-economist | Low | Non-blocking |
| AI臭マーカー混入 | 空疎な枕詞・根拠なし誇張・均質リズムを含む文章 diff | ODC-documentation | ai-smell | Medium | Non-blocking |
| UXヒューリスティック違反 | 破壊的操作の確認ダイアログ削除・現在地表示の欠落 | ODC-design（NN/g heuristics） | ux | High | Blocking |
| アクセシビリティ違反 | alt 欠落・コントラスト不足・フォーカス順の破壊 | WCAG 2.x（1.1.1 / 1.4.3 / 2.4.3） | a11y | High | Blocking |
| 根拠なし実績・誇大表現 | 出典のない数値・「業界No.1」等を含む投稿文 | ODC-documentation | sns-post | High | Blocking |
| エンゲージメント構造の欠陥 | 冒頭フックなし・CTA 過多・尻すぼみの投稿構成 | ODC-design | engagement | Low | Non-blocking |
| 一線越えの攻撃 | ユーモアでなく人格・属性攻撃になっている一節 | ODC-documentation | roast | High | Blocking |
| ヒアリング欠落 | 予算・決裁者・時期を未確認のまま提案へ進む商談メモ | ODC-checking | hearing / needs | High | Blocking |
| 提案不一致 | 顧客の課題と無関係な機能を推す提案骨子 | ODC-design | proposal | High | Blocking |
| クロージング不備 | 次アクション・期日なしで終わる商談ログ | ODC-checking | closing / next-action | Medium | Non-blocking |

`cwe/odc` 列は各種の**出所（provenance）**——CWE Top 25 2024・ODC（Orthogonal Defect Classification）欠陥タイプ・ミューテーション演算子（ROR＝関係演算子置換 / LCR＝論理結合子置換）・WCAG 達成基準へのマッピング。カタログの偏り（どの欠陥領域を測れていないか）を外部基準で監査できるようにするための列であり、種の合成時はこの分類に忠実な形で埋め込む。

**散文/設計系の種（v2 追加分・#266）**：drill の仕組み（欠陥を仕込んだ diff を合成→reviewer fan-out→答案キーで採点）はコード専用ではない。de-ai-smell・design/design-audit・sns-x-post/scenario・roast・deal-review のような**散文・設計物をレビューする recipe** では、その recipe が普段レビューする成果物の形（文章・画面仕様・投稿文・商談メモ）に種を埋め込む——採点方法（検出率・severity 精度・説明品質・クリーン統制）は同一。

### ② 注入（本物のコードは触らない）

**一時 worktree（または scratch ブランチ）**に、選んだ種を субagent が自然なコードとして埋め込んだ diff を合成する。種の位置と正解（`file:line`・class・期待 severity）は**答案キー**として親だけが保持し、reviewer には渡さない。

#### 種の妥当性ゲート（equivalent-mutant コントロール・採点前必須）

採点（③）の前に、合成した**各種**に対して `finding-verifier`（既存の反証者 agent）を dispatch する——「この diff の `<file:line>` に `<class 相当の指摘>` がある」という**一人の reviewer の主張として**渡し、**答案キー（自分たちが仕込んだ種）だとは知らせない**（知らせると追認バイアスがかかり、ゲートとして機能しない）。

- 反証者が**この文脈では無害**（デッドコード・呼び出し側で既にガード済み・到達不能・挙動が変わらない等価変異など）と REFUTED した種は、**検出率の分母（`seeded`）から除外**する。見逃しても reviewer の失点にせず、偶然指摘しても加点しない。
- 除外した種は `.rig/drill-results.jsonl` に `invalid_seeds` として**反証内容つきで**記録する（下記スキーマ）。
- class ごとの**種妥当性率**（valid / synthesized）を `seed_validity` として記録・追跡する。特定 class の妥当性率が恒常的に低い＝reviewer でなく**その class の種合成のやり方**を直す信号。
- `--verify-findings` の反証者採点（③）とは独立：この段の REFUTED は「種の無効化」であって、反証者自身の得点・失点にはしない。

根拠：生成されたミュータントの 4〜39% は equivalent（挙動を変えない）であり（arXiv:2408.01760）、equivalent 判定は一般に決定不能＝個別トリアージ以外の対処がない（Stryker mutation-testing docs）。無効な種を分母に残すと、検出率が系統的に過小評価される。

### ③ 実測とスコアボード

合成 diff に対し review fan-out（`parallel-review` と同じ経路）を実行する。**drill 実行時は dispatch する reviewer の `output_contract` を `review-findings`（`facets/output-contracts/review-findings`）に固定する**——per-finding の severity・file:line・Blocking/Non-blocking が無いと下記の severity_accuracy / detection 判定が機械的にできないため（通常の review fan-out で使う `review-verdict` からの一時的な上書き。engine 本体・他 recipe の contract 選択には影響しない）。

#### ③-a クリーン・コントロール（no-bug diff・`--clean` / 既定はミックス）

種入り diff とは別に、**バグを一切含まない、もっともらしい no-bug diff**（リファクタ/リネーム形＝変数名の改善・等価な関数抽出・整形・コメント追随など挙動不変の変更）を合成し、**同じ reviewer fan-out** にかける。クリーン diff は定義上バグゼロなので、**そこへの REJECT verdict・および全 finding は1件残らず誤検出**として数える：

- per-persona **`clean_fp_rate`** ＝ finding または REJECT を出したクリーン diff の割合（分母はクリーン diff 本数。生カウント `clean_findings`・`clean_rejects`・`clean_diffs` も記録し、履歴通算で再計算できるようにする）。
- **既定（ミックスモード）**は種入り diff にクリーン diff を1本混ぜて同一 run で測る。**`--clean`** はクリーン diff のみ（種ゼロ）の較正専用モード。どちらの diff かは reviewer に知らせない。
- **`add_false_positive_guard` の閾値判定（>10%）は、クリーン実測がある persona では `clean_fp_rate`（履歴通算）を正とする**——種入り diff 上の `false_positive_rate` は「種の近傍につられた誘導ノイズ」を含み過大評価しやすい。クリーン実測がまだ無い persona のみ従来の `false_positive_rate` に fallback する。

根拠：clean variant を混ぜた測定はミューテーション系ベンチマークの標準装備（arXiv:2512.22306 は clean 変種を同梱して出荷する）。実運用でも誤検出ノイズが支配的な失敗モード——curl の bug-bounty は AI 製誤報の洪水で崩壊し、SonarSource の実測では tuned 3.2% に対し未調整ツールは 40〜80% の FP 率。

#### ③-b スコアボード

**7指標**（答案キーとの突き合わせで算出。検出系の分母 `seeded` は**妥当性ゲート（②）を通過した有効種のみ**）：

| metric | 定義 |
|---|---|
| `true_positive` | 種の `file:line` を証拠アンカーつきで指摘した件数 |
| `false_positive` | 種でも実バグでもない指摘の件数（実バグの偶然発見は別枠で報告し加点） |
| `false_negative` | 見逃した種の件数（`seeded - true_positive`） |
| `clean_fp_rate` | クリーン diff（③-a）に finding または REJECT を出した割合（定義上すべて誤検出） |
| `severity_accuracy` | 検出できた種のうち、reviewer が付けた Severity が期待 severity と一致した割合（隣接 1 段差＝例えば期待 High に対し Critical/Medium＝は半点、2 段差以上は 0 点） |
| `blocking_accuracy` | 検出できた種のうち、reviewer が置いたセクション（`## Blocking`/`## Non-blocking`。`output-contracts/review-findings`）が期待 blocking と一致した割合（部分点なし＝二値のため） |
| `explanation_quality` | 検出できた種のうち、Impact/Suggested fix が「具体的で実行可能」と判定された割合（judge 基準は下記） |

**explanation_quality の判定基準**（`finding-verifier` または専用 judge subagent に1件ずつ判定させる）：
- ✓（具体的）＝Impact が「何が起きるか」を1文で言え、Suggested fix が「何をどう変えるか」まで踏み込んでいる。
- ✗（曖昧）＝「直してください」「気をつけてください」のような無内容な指示、または一般論の繰り返し。

集約表示：

```
## rig drill（seeds: 5（有効 4・invalid 1）/ clean: 1 / reviewers: 6）

| reviewer            | 検出 | 見逃し | 誤検出 | 検出率 | severity精度 | blocking精度 | 説明品質 |
|---------------------|-----:|------:|------:|------:|------:|------:|------:|
| security-reviewer   |  2/2 |     0 |     0 |  100% [34%,100%] |  100% |  100% |  100% |
| performance-reviewer|  0/1 |     1 |     0 |    0% [0%,79%] |     - |     - |     - |  ← 種: N+1（file:line）を素通し
| docs-reviewer       |  1/1 |     0 |     2 |  100% [21%,100%] |   50% |  100% |   50% |  ← 誤検出2件（ノイズ源）・severityを実際より重く付けた
…
見逃された種: N+1（src/orders.py:42）— performance-reviewer の観点1に該当するが未指摘
無効化された種: 例外の握りつぶし（src/util.py:17）— finding-verifier が反証（到達不能パス）＝分母から除外
```

- **small-n の正直さ（Wilson 95% 区間・必須）**：分母 n（その reviewer の有効種数）が **10 未満**のときは、検出率を点推定だけで書かず **Wilson 95% 区間**を併記する（上の例の `100% [34%,100%]`）。式（z = 1.96・p̂ = 検出数/n）：

  ```
  中心 = (p̂ + z²/2n) / (1 + z²/n)
  半幅 = z/(1 + z²/n) × √( p̂(1−p̂)/n + z²/4n² )
  区間 = [中心 − 半幅, 中心 + 半幅]（0〜1 に収めて % 表示）
  ```

  n=2 で 2/2 でも区間は [34%, 100%]＝「満点」でなく「まだほぼ何も言えない」を明示するのが目的。`clean_fp_rate` や severity 精度など他の割合指標にも同じ規則を適用してよい（検出率には必須）。
- **検出**＝種の `file:line` を証拠アンカーつきで指摘した。**誤検出**＝種でも実バグでもない指摘。
- `--verify-findings` 時は反証者も採点：正しい種の指摘を REFUTED にしたら失点、誤検出を REFUTED できたら得点。
- 結果は `.rig/drill-results.jsonl` に**1 run＝1行 JSON** で追記（テレメトリと同格・承認不要。`/rig:party` が読む正準スキーマ。以下は読みやすさのため改行しているが実体は1行）：
  ```json
  {"ts": "<ISO8601>", "corpus": "standard", "corpus_version": 2, "seeds": 5, "valid_seeds": 4, "clean_diffs": 1,
   "invalid_seeds": [{"class": "例外の握りつぶし", "file": "src/util.py", "line": 17, "refutation": "この catch は到達不能パス上にあり挙動を変えない（finding-verifier の反証全文）"}],
   "seed_validity": {"例外の握りつぶし": {"valid": 0, "synthesized": 1}, "認可漏れ": {"valid": 1, "synthesized": 1}},
   "scores": [{"reviewer": "security-reviewer", "detected": 2, "seeded": 2, "missed": [], "false_positives": 0, "severity_accuracy": 1.0, "blocking_accuracy": 1.0, "explanation_quality": 1.0, "clean_findings": 0, "clean_rejects": 0, "clean_fp_rate": 0.0}]}
  ```
  - `valid_seeds`／`invalid_seeds`／`seed_validity`：妥当性ゲート（②）の結果。`invalid_seeds` は反証内容つきで残す＝種合成レシピの改善材料。
  - `missed`：見逃した種の class 列挙（履歴通算での `add_checklist_item`／`strengthen_security_focus` 判定に使う）。
  - `clean_findings`・`clean_rejects`・`clean_diffs`・`clean_fp_rate`：クリーン・コントロール（③-a）。`--clean` 単独 run では `seeds: 0` で `scores` の検出系フィールドは 0/0。
  - `corpus`・`corpus_version`（#270）：この run の種の選定元（`standard`/`project`。`--corpus all` の run は選定元ごとに**行を分けて**記録し、標準スコアとプロジェクト固有スコアが1行に混ざらないようにする）。フィールドが無い過去の行は `standard` とみなす（#270 以前の run は標準カタログのみだったため）。
  - 追加フィールドはすべて additive（既存の読み手＝party/digest/dashboard は `detected`/`seeded` 系のみ参照するため互換）。スコアボードのヘッダにも `corpus: standard` の形で選定元を1項表示する。

#### Drill Result（persona 単位の詳細レポート・#新設）

per-reviewer 表に加え、reviewer（persona）ごとに次の詳細レポートを出す——`/rig:persona` での改善判断に直結する材料：

```
# Drill Result

Persona: strict_senior_engineer

## Score（通算 n=17 有効種・単一 run でなく履歴合算）

- Detection rate: 82% [59%,94%]（n<10 なら Wilson 95% 区間必須・n≥10 でも併記推奨）
- Clean FP rate: 12%（clean diff 通算 8 本）
- False positive rate (seeded diffs): 12%
- Severity accuracy: 76%
- Blocking accuracy: 81%
- Explanation quality: 70%

## Missed Issues

1. SQL injection risk in search query（src/search.py:88）
2. Missing authorization check in user update endpoint（src/api/users.py:120）

## False Positives

1. Reported null risk in path that is already guarded（src/orders.py:12）

## Recommended Persona Updates

- [strengthen_security_focus] security 系 class（injection / 認可漏れ）の見逃しが通算2件 — セキュリティ観点の優先順位を persona の評価軸で明示的に引き上げる
- [add_checklist_item] 同一 class（security）の見逃しが通算2件以上 — 認可チェック・入力検証の明示チェックリストを persona に追加する
- [adjust_severity_rule] 通算 severity_accuracy が 76%（閾値 80% 未満）— 重大度判断基準（Critical/High/Medium/Low の境界）を persona 内で明文化する
- [add_false_positive_guard] 通算 clean_fp_rate が 12%（閾値 10% 超・clean diff 8 本の実測）— 「ガード済みパスは指摘しない」等の誤検出抑制ルールを追加する
```

- `Missed Issues` は `false_negative` の種を `class（file:line）` 形式で列挙する。`False Positives` は誤検出を同形式で列挙する（0件ならセクションごと省略）。
- **`Recommended Persona Updates` は固定4カテゴリ（`persona_update_suggestions`）の中からのみ選び、`[category]` タグを先頭に付ける**（自由文の感想にしない＝機械集計・横展開しやすくする）。
- **発動判定は単一 run の値でなく、`.rig/drill-results.jsonl` の履歴通算で行う**（今回の run を追記した後の全行が対象）。集計方法：対象 persona が `scores` に現れる**全行**からカウントを合算する——`detected`・`seeded`・`false_positives`・`missed`（class 別に件数を合算）・`clean_findings`／`clean_rejects`／`clean_diffs`。比率はすべて**合算カウントから再計算**する（例：通算検出率 = Σdetected / Σseeded、通算 `clean_fp_rate` = 「finding か REJECT を出した clean diff の通算本数 / Σclean_diffs」）。`severity_accuracy`・`blocking_accuracy`・`explanation_quality` のように率でしか記録していない指標は、各 run の値を `detected` で重み付き平均する。1 run の n（既定 5 種）は小さすぎて、単発の偶然で閾値を跨ぐ——n=5 で 1 件の見逃しは検出率を 20pt 動かす——ため、単一 run 発動は禁止：

| category | 発動条件（`.rig/drill-results.jsonl` 履歴通算） | 意味 |
|---|---|---|
| `add_checklist_item` | 同一 class の見逃し（`missed` 合算）が通算2件以上 | その観点の明示チェックリストを persona に追加する |
| `adjust_severity_rule` | 通算 `severity_accuracy`（`detected` 重み付き平均）< 80%、かつ通算 detected ≥ 5 | 重大度判断基準を明文化・調整する |
| `add_false_positive_guard` | 通算 `clean_fp_rate` > 10%（Σclean_diffs ≥ 1 のとき正。クリーン実測が皆無の persona のみ fallback：通算 `false_positive_rate`（Σfalse_positive / (Σtrue_positive + Σfalse_positive)）> 10%） | 誤検出を抑える具体的なガード条件を追加する |
| `strengthen_security_focus` | security 系 class（認可漏れ・インジェクション・XSS・パストラバーサル・ハードコード秘密・安全でないデシリアライズ・認証欠落）の見逃しが通算2件以上 | セキュリティ観点の優先順位・注意力を引き上げる |

該当条件を満たさないカテゴリは出さない（0〜4件・**剪定の材料であり自動適用しない**、原則③と同じ）。

**低検出率の観点はペルソナの観点文を尖らせる示唆**（`/rig:persona` で編集→④で回帰確認）。

### ④ `--replay`（persona 回帰リプレイ）

ペルソナを編集したとき、判定が意図どおり変わったかを確認する：

1. **アーカイブ** — review fan-out の実行時、diff と各 verdict を `.rig/replay/<ts>/` に保存してよい（実行ログ・承認不要・`.rig/` は gitignore 済み）。drill の合成 diff も自動でアーカイブされる。
2. **再実行** — `--replay <persona>` は、アーカイブ済み diff 群へ**編集後のペルソナ**で再 dispatch し、**新旧 verdict の差分表**を出す：

```
## rig drill --replay security-reviewer（アーカイブ 8 件）

| diff             | 旧 verdict                  | 新 verdict | 変化 |
|------------------|-----------------------------|-----------|------|
| 2026-07-01/a3f…  | APPROVE                     | REJECT    | ⚠ 厳格化（意図どおり?） |
| 2026-06-28/91c…  | REJECT（確度高）             | REJECT    | 不変 |
```

3. 差分がゼロなら「編集は過去の判定に影響なし」、差分があれば**意図した方向か**を人が確認する（ペルソナ開発の snapshot テスト）。

## 原則

- **本物のコードベース・本物の履歴を汚さない**（worktree/scratch・終了時に破棄）。
- 種は**実在するバグ class のみ**（検出不可能な意地悪や曖昧な種で reviewer を貶めない＝測定の公正）。
- 期待 severity は目安であり絶対の正解ではない（隣接1段差は許容し半点にする＝過度に厳格な採点で有用な reviewer を不当に低評価しない）。
- 結果は剪定の**材料**であって自動処分ではない（外す/尖らせるの判断は人）。
