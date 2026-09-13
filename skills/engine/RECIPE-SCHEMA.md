# rig — recipe スキーマ

**SKILL.md §3.5 の正本。** recipe ファイル（`recipes/*.md`）の frontmatter キーの全量を置く。

recipe を書くとき・読むとき・`extends` を辿るときに要る定義であり、起動の最初の1ターンには要らない。
SKILL.md 本体は1行要旨とこのファイルへの参照だけを残す。

## 3.5. Recipe スキーマ（正規定義）

recipe ファイル（`recipes/*.md`）は YAML frontmatter + 本文 Markdown で構成される。以下がエンジンが解釈するキーの全量。

### トップレベルキー

| キー | 必須 | 説明 |
|---|---|---|
| `name` | ✓ | recipe 識別子（ファイル名と一致させること） |
| `description` | ✓ | 使い分け説明（一行） |
| `scope` | ✓ | `shipped`（同梱）/ `user`（ユーザー保存）/ `project`（プロジェクト固有） |
| `steps[]` | ✓ | step オブジェクトの配列（下記） |
| `autonomy` | ✓ | `interactive`（各 step でゲート確認）/ `autonomous`（**step ゲートなし**。acceptance-gate 品質ループは維持） |
| `extends` | — | 継承元 recipe の bare 名。その steps をベースに差分だけ上書きする（**N 段継承・深さ上限 5**・`remove: true` で継承元 step を静的除外。`facets/instructions/resolve` 2.2 が正本） |
| `backend` | — | `manual`（既定）/ `workflow`。RUN の実行バックエンド宣言（§6） |
| **フラグ等価キー** | — | `tdd` / `no_default_personas` / `orchestrate` / `no_orchestrate` / `cross_llm` / `capture` / `no_capture` / `adversarial` / `visual` / `design` / `review` / `verify_findings` — すべて boolean（省略時 `false`）。**対応フラグと等価・`--save-recipe` で保存され再利用時にフラグなしで再現・有効時は `--plan`／完了レポートに修飾子と `--list` に badge が付く**という同一の一般則（§4.3）に従う。**各キーの効果・競合規則（`orchestrate`⇔`no_orchestrate` / `capture`⇔`no_capture`）の正本は `facets/instructions/resolve` 3.1** |

### step オブジェクトのキー

| キー | 必須 | 説明 |
|---|---|---|
| `id` | ✓ | step 識別子（例 `review` `design` `implement`） |
| `instruction` | ✓ | 委譲先 instruction facet 名（例 `parallel-review`） |
| `pattern` | — | 制御フロー（`serial` / `parallel-fanout` / `review-gate` 等） |
| `gate` | — | コードで実装された集約/受け入れゲート。現在は `review-gate`（レビュー集約）/ `acceptance-gate`（受け入れ基準まで品質収束）。Markdown pattern の存在だけでは実行可能な gate にならない。 |
| `acceptance` | — | `gate: acceptance-gate` 時の**受け入れ基準リスト**（合否判定の根拠。例 `["build が成功", "lint 0 件", "3-way review に REJECT が無い"]`）。基準を満たすまで収束させる |
| `acceptance_binding` | — | `acceptance[]` と**同じ長さ・同じ並び**のリスト。i 番目の受け入れ基準行を**観測するゲート基準 id**（`GATE_PRESETS` の 34 種・`rig-wb wb gates`）を書く。どの基準も実際には観測しない行には `unobserved` を書く。**ゲートの合否は `build_acceptance()` が preset から組む——recipe は読まれない**ので、束ねられていない行は「誰も判定しない散文」であり、それを明示するためのキー（`--validate` が欠落・長さ不一致・未定義 id・id-form 行との矛盾を FAIL） |
| `max_retries` | — | `gate: acceptance-gate` 時の**最大収束試行数 K**（≥1 の整数）。K 回で受け入れ基準を満たさなければ user へエスカレーション。**省略時フォールバック順：step 省略 → manifest `default_max_retries` → 2**（#100）。§6 stuck-guard（同一エラー反復で発動する別カウンタ）とは独立した上限。 |
| `model` | — | この step の **generator LLM モデル名**（例: `claude-sonnet-5` / `claude-opus-4-8` / `gpt-5`）。指定時、`build_argv` が `claude -p ... --model <name>` / `codex exec ... -m <name>` に引き渡す（ollama/lmstudio 系は既存の HTTP model resolve が優先）。**「親 = Sonnet / 深堀り step = Opus」を recipe で書ける**。省略時は run 時 flag の `--model` にフォールバック。run 時の **`--step-model <step-id>=<model>`**（繰り返し可）は特定 step だけを実行時に上書きする——優先順位は `--step-model` > recipe `model:` > `--model`（未知の step-id は実行前に ERROR）。 |
| `verifier_model` | — | この step の **verifier LLM モデル名**（`model:` と別にしたい時）。省略時は `model:` を再利用、それも無ければ run 時 flag。**「実装は Sonnet で書かせ、検証だけ Opus に厳しく見てもらう」**を step 内で分離できる。 |
| `personas` | — | 合成するペルソナ facet 名のリスト |
| `actor` | — | （v2.1）この step を**所有する組織ロール**（policy の `roles` のいずれか。`personas` が LLM の人格なのに対しこちらは人間側の役割）。`human_gate` の既定承認ロールになる。**実行はブロックしない**（CI で回す run を止めても安全性は上がらない＝rig が保証できるのは「アーキテクトが署名した」ことであって「アーキテクトが打鍵した」ことではない）。所有ロール外の実行は START 時に WARN と history に残る |
| `human_gate` | — | （v2.1）**人間の承認で止まる step**。`true`（quorum 1）か `{quorum, roles, separation_of_duties, expires_hours}`。ゲートが機械的に pass した後も承認が揃うまで `awaiting_approval` で駐機し、`orchestrate approve <step-id>` で解放される。承認の算術（quorum・資格ロール・**職務分離**＝実行者本人の承認は数えない・**鮮度**＝承認時コミットに束縛）は v2 の govern layer と同一実装。org policy の `approvals` に `stage:<step-id>` があれば**厳しい方に合成**される（recipe は policy を緩められない） |
| `policies` | — | 末尾注入するポリシー facet 名のリスト |
| `output_contract` | — | subagent 出力フォーマット定義 facet 名（例 `review-verdict`） |
| `condition` | — | 条件付き step。例：`--design または size L+ で有効` のように記述し、RESOLVE フェーズで ON/OFF を判断する |
| `checks` | — | （任意・`--orchestrate` 用）この step の**計算的センサー**＝決定論ランナーが実行する shell コマンド列（全件 exit 0 で合格）。**リスト必須・空文字列エントリ不可**（`--validate` が型・空エントリを検証 #200）。`gate` の一次根拠になる。プロジェクト依存のため shipped recipe では未宣言、manifest / user recipe で足すのが基本。`patterns/computational-orchestration` 参照 |
| `needs` | — | （任意・`orchestrate run` 用）依存する step-id のリスト。**DAG 並列**＝`needs` を満たした独立 step を同時プロセスで実行（依存の無い step は同一 wave で並走）。宣言が無ければ従来どおり直列。`patterns/computational-orchestration` 参照 |
| `remove` | — | （`extends` 専用）`true` の場合、継承元 recipe からこの `id` の step を**静的に除外**する。`extends` なし recipe での使用は WARN。`--skip`（実行時動的フィルタ）との差異：`remove: true` は recipe 定義の静的除外（毎回同じ）（#144） |

> **省略可能キーは省略してよい。** `review-only` は最小サブセットだけを使う。内訳は `id` / `instruction` / `pattern` / `gate` / `personas` / `output_contract` である。`release-flow` / `design-first` は `policies` / `condition` / `gate` / `acceptance` も使う。すべての recipe はこのスキーマに準拠する。

