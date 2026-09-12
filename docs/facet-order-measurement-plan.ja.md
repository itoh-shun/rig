# facet 配置順は効くのか — `/rig:drill` による測定計画

作成日: 2026-09-10
状態: 計画のみ。**1回も実行していない**。実行可否の調査結果を含む。
経緯: `docs/v3-architecture-design-brief.ja.md` §8 の決定「Faceted Prompting は置き換えず、測る」に対応する
独立調査。5段の移行の依存ではない。

測る主張は1つだけである。`skills/engine/COMPOSE.md` §5 の表は次のように言う。

> **User 末尾 / Policy / recency が効く末尾にガードレール**

この「recency が効く」を裏づける計測は、このリポジトリに1件も存在しない
（`.rig/drill-results.jsonl` は無く、`benchmarks/` にも facet 配置順のアームは無い）。
本書は、その1行を数字にするための設計であって、結果ではない。

---

## 1. `drill` は何を測るか

`skills/engine/facets/instructions/drill.md` と `rig_workbench/workbench/detection_corpus.py` の実測。

**測る量**：既知の欠陥（種）を仕込んだ diff に reviewer を当て、**所見1件を単位に**、どの種を検出したかを
数える。7指標は `detected` / `false_positive` / `false_negative` / `clean_fp_rate` / `severity_accuracy` /
`blocking_accuracy` / `explanation_quality`（最後は未実装＝キーごと欠落）。

**検出の判定は2段**である（`detection_corpus.py:458` `score_violation`）。

| 段 | 判定者 | 内容 |
|---|---|---|
| 決定論 | Python | (a) 場所（`location` 正規表現 or `file:line` アンカーが種の行域に始まる）、(b) 同じ所見内に欠陥 class の概念語、(c) その所見が**他のどの種も指していない** |
| judge | 別プロバイダ | (d) その所見が欠陥を「ある」と主張しているか（`ASSERTS` / `DENIES` / `NEITHER`） |

judge は**減点方向にしか効かない**（`ASSERTS` 以外は検出を取り消す。`adjudication.py:350` `Adjudicator`）。
judge を付けない行は `adjudicated: false` になり、検出率として集計されない。

**入力**：`{case-id: {persona: レビュー本文 or @path}}` の JSON。
**出力**：`.rig/drill-results.jsonl` へ1 run＝1行 JSON（`scorer_version` は現在 4）。
`scores[]` に persona ごとの `detected` / `seeded` / `missed` / `clean_fp_rate` ほか、
`judge`（`{provider, model, prompt_version, offline, calls, cache_hits}`）、`corpus_digest` が付く。

**ここが計画上いちばん効く事実**：`rig-wb wb drill-corpus score` は**採点器であって実行器ではない**
（`detection_corpus.py:1126`）。レビュー本文は引数として渡すものであり、この CLI はプロバイダを1回も呼ばない。
呼ぶのは judge だけである。**つまり review fan-out（＝アームを作る部分）は、測定側ではなく呼び出し側の責任**で、
配置順を入れ替えたプロンプトを作る場所もそこにしかない。

作り置きコーパス（`skills/engine/corpora/fixture/`、`corpus_version: 3`）の実測値:

| ケース | 言語 | 仕込んだ欠陥 |
|---|---|---|
| `py-mixed-violations` | Python | 5 |
| `ts-mixed-violations` | TypeScript | 5 |
| `ts-behavioral-correctness` | TypeScript | 5 |
| `js-layout-gate` | JavaScript | 5 |
| `py-clean-refactor` | Python | 0（クリーン統制） |

合計 **20 種**。観点別の内訳は `behavioral-correctness` 5 / `layout-gate` 5 / `security` 3 /
`design` 2 / `observability` 2 / `performance` 1 / `test` 1 / `api-compat` 1。
diff は小さい（`head/` ツリーは 747〜3,512 バイト）。

---

## 2. ここで実行できるか — **できない**。塞いでいるものを名前で書く

結論から書く。**この sandbox で本番の drill run は完走しない。** 塞いでいるのは3つで、
どれも「試したら失敗した」ではなく「読んで分かる」種類のものである（実行はしていない）。

**① judge のプロバイダが無い。** 既定 judge は `codex`（`adjudication.py:89`）＝採点される reviewer（Claude）と
別のモデル系統であることが設計理由である。この環境に `codex` は無い（`shutil.which("codex")` → `None`）。
`claude` は `/opt/node22/bin/claude` にあるが、それを judge にすると reviewer と同系統になり、
`drill.md` ③-b が明示的に避けている「誤りが相関する」構成そのものになる。

**② judge の実行環境から資格情報が落ちる。** judge は環境変数の**許可リスト**で起動する
（`adjudication.py:111` `_ENV_KEEP`）。実際に `judge_env("claude")` を呼ぶと残るのは
`HOME` / `LC_CTYPE` / `PATH` / `SHELL` / `TERM` の5つだけで、この環境の `claude` が API に届くために要る
`ANTHROPIC_BASE_URL`・`NODE_EXTRA_CA_CERTS`・`HTTPS_PROXY` は**全部落ちる**。
`ANTHROPIC_API_KEY` も設定されていない。これは実装のバグではなく、答案キーへの手がかりを削るための設計であり、
「認証はプロキシ経由の環境変数で渡す」sandbox とは噛み合わない。

**③ `--replay` の材料が無い。** `--replay` はアーカイブ済みの過去 diff に再実行する機能だが、
`.rig/replay/` は存在せず、`.rig/drill-results.jsonl` も存在しない（リポジトリ全体を検索して0件）。
**過去の drill run は1回も無い。** §8 が想定した「`--replay` で過去の差分に再適用する」経路は、
再適用する対象がまだ無いという意味で、いま使えない。

オフラインで**できること**もある。judge の較正セットは、同梱台帳
（`skills/engine/corpora/fixture/judge-calibration-ledger.jsonl`、73 行・すべて `provider: codex`・
`prompt_version: 52758193db1bf838` ＝現行）で `--judge-offline` から再生できる。
ただし台帳から答えた run は `cache_hits > 0` になり、行は `measured: false` になる
（`drill.md` ③-b。replay は replay であって新しい観測ではない、という規約）。

**したがって本計画の実行には、`codex` 実行系（またはそれに準ずる別系統プロバイダ）と、
judge 許可リストが通る資格情報経路の、少なくとも一方を用意した環境が要る。**
本書はここで止めており、資格情報やネットワークを試しに叩くことはしていない。

---

## 3. 実験設計 — アームをどこで作るか

### 3.1 いちばんの争点：配置順はコードか、散文か

**両方ある。そして drill が普段通る側は散文のほうである。** これが設計の全体を決める。

| 経路 | 順序を決めているもの | 再現性 |
|---|---|---|
| headless（`rig-wb run` ほか） | **コード**：`rig_workbench/orchestrate/providers.py:1882` `_compose_prompt_sections` | 決定論。バイト単位で同じ |
| in-session（`/rig:go` などスキル経由） | **散文**：`skills/engine/COMPOSE.md` §5 の表を、オーケストレータ役のモデルが読んで守る | 守った保証は無い |
| 検証者ファンアウト | **別のコード**：`providers.py:1529` `run_verifiers_parallel` は persona 要約を前置し、facet 合成を通らない | 決定論だが §5 とも別順 |

コード側の順序は次のとおりで、`tests/test_headless_compose.py:55`
（`test_generator_prompt_composes_resolved_facets_in_canonical_order`）が凍結している。

```
## Persona → ## Knowledge → ## Instruction → ## Task Contract → ## Output Contract → ## Policy
```

ここで**散文と実装のずれが1件見つかった**。§5 の表は Persona を **System** に置くと書いているが、
コードにシステムプロンプトの口は存在しない（`--append-system-prompt` 等の grep は0件、
`ClaudeCliRuntime.build_argv`（`agent_runtime.py:337`）が渡すのは `claude -p <prompt>` の**単一文字列**）。
**System / User の別は散文にしか無い。** よって測れるのは「1本のプロンプト内での前後」だけであり、
§5 の1行目（Persona を System に固定する）は本実験の射程外である。

そして `drill` の review fan-out は `parallel-review` 経由＝**in-session の散文経路**である
（`recipes/drill.md` の展開、`drill.md` ③）。散文経路にはアームを作る場所が無い——
モデルに「今回は Policy を先頭に置け」と頼むことはできるが、それは**指示に従ったかどうかの測定**になり、
配置順の測定にならない。

**結論（アームの作り方）**：アームは**呼び出し側の小さなハーネスが自分でプロンプトを組む**ことで作る。
公開関数 `resolve_prompt_facets(step)`（`providers.py:1524`）が5つの facet 本文を辞書で返すので、
ハーネスはそれを受け取り、**アームごとに自分で連結して** `run_provider` を呼ぶ。
出荷コードは1行も変えない（`_compose_prompt_sections` を書き換えると `test_headless_compose.py` が落ち、
かつ実験のためにプロダクトの順序を動かすことになる）。
アーム A の連結は `_compose_prompt_sections` と**バイト一致することを実行前に assert する**——
一致しないアーム A は「出荷順」ではない。

### 3.2 アーム

単一次元アブレーション（`benchmarks/writing-tasks/jp-natural-writing` と同じ形）。動かす次元は Policy の位置だけ。

| アーム | 並び | 何を問うか |
|---|---|---|
| **A（出荷順）** | Persona → Knowledge → Instruction → Task Contract → Output Contract → **Policy** | 基準 |
| **A'（帰無対照）** | A と同一（バイト同一のプロンプトを再実行） | 判定のブレの床 |
| **B（Policy 先頭）** | **Policy** → Persona → Knowledge → Instruction → Task Contract → Output Contract | §5 の「recency が効く末尾」の主張の反対側 |

**A' は省略できない。** 同一入力での分散を測らずにアーム差を読むと、ノイズを効果と読み違える。
このリポジトリはその事故を実測済みである（バイト同一のテキストが 9 点・62 点・68 点を取り、
「劇的改善」として3コミット報告された。`drill.md` ⑤ の記述）。
**A' の差が B の差以上なら、B の差は読めない。**

拡張案（**先には走らせない**）：Output Contract を先頭に出すアーム C、Policy を中部に置くアーム D。
A と B に床が付いてから初めて意味を持つので、本計画には入れない。

### 3.3 reviewer と単位

persona は **`strict-senior-engineer` / `lazy-senior` / `cognitive-economist` の3枚**を使う。
理由は採点の分母である：`build_drill_row`（`detection_corpus.py:766`）は、persona の観点が
コーパスの観点集合に無いとき **20種すべてを分母にして `attribution: "all"` の印を付ける**。
観点一致の persona（例 `security-reviewer`）は分母が 3 種しかなく、1 run で何も言えない。

- 観測単位 ＝ **(ケース, 種, persona, パス)** の検出 0/1。
- 対応あり ＝ 同じ (ケース, 種, persona) を A と B で突き合わせる（McNemar 型）。
- 1パス（全5ケース × 3 persona）＝ **60 の対応あり試行**、レビュー呼び出しは 15 回。
- クリーンケース `py-clean-refactor` は毎パスに入り、`clean_fp_rate` を同時に測る
  （順序を変えて誤検出が増えるなら、検出率が動かなくてもそれは効果である）。

### 3.4 手順

```
# 0. 実行前（無料）：アーム A の連結が出荷コードとバイト一致することを確認する
# 1. ケースを使い捨て git リポジトリへ展開（本物の履歴は触らない）
rig-wb wb drill-corpus materialize <case-id> --into <tmp>
# 2. ハーネスが resolve_prompt_facets → アーム順に連結 → run_provider（reviewer には
#    アームであることも答案キーも渡さない）
# 3. 採点。--judge を必ず付ける（付けない行は検出率として集計されない）
rig-wb wb drill-corpus score --reviews <path.json> --judge codex \
  --judge-ledger .rig/judge-ledger.jsonl --workspace <case>=<tmp> \
  --append .rig/drill-results.jsonl
# 4. judge を信じる前に較正する（ideal/negative と attack/waiver は平均しない）
rig-wb wb drill-corpus calibrate-judge --judge codex
```

行は**アームごとに分けて記録する**。1行に混ぜない。
`corpus_digest` と `judge.*` は行に残るので、後から「どの答案キーで・本物の judge 呼び出しで測ったか」を監査できる。

---

## 4. サンプルサイズ — 何本走らせれば意味があるか

**方法は発明しない。** このリポジトリが日本語生成ベンチで使った対応あり二値の正規近似
（`benchmarks/writing-tasks/jp-natural-writing/mde_calibration.py:282`
`approximate_matched_trials(effect_points, adverse_rate, alpha=0.05, power=0.8)`）をそのまま使う。
`adverse_rate` は**帰無対照で逆方向に動く率**＝A' で測る量である。

同関数が返す必要試行数（両側 5%・検出力 80%）:

| 効果 | adverse 2% | 5% | 10% | 15% | 20% |
|---:|---:|---:|---:|---:|---:|
| 5pt | 275 | 464 | 778 | 1091 | 1405 |
| 10pt | 103 | 150 | 228 | 307 | 385 |
| 15pt | 59 | 80 | 115 | 150 | 185 |
| 20pt | 40 | 52 | 71 | 91 | 110 |
| 30pt | 22 | 28 | 36 | 45 | 54 |

1パス＝60 試行なので、パス数に直すと（1アームあたり）:

| 狙う効果 | adverse 5% | 10% | 15% |
|---:|---:|---:|---:|
| 10pt | 3 パス | 4 パス | 6 パス |
| 15pt | 2 パス | 2 パス | 3 パス |
| 20pt | 1 パス | 2 パス | 2 パス |

**手順は二段にする。**

1. **パイロット（A と A' を各1パス）** — 60 対応ありペアで `adverse_rate` と基準検出率を実測する。
   ここで `adverse_rate` が 20% を超えたら、**本体に進まず設計を止める**：判定のブレが 20pt 級の効果と
   区別できない測定器で 10pt を追っても答えは出ない。
2. **本体** — パイロットの `adverse_rate` を上表に入れてパス数を決める。既定の狙いは **10pt**
   （§5 の主張が本当なら、ガードレールを末尾から先頭へ動かして検出率が 10pt も動かないなら、
   その主張は実務的に意味を持たない、という線引き）。adverse 10% なら **A・B 各4パス**。

**この数字は下限である。** 同関数の docstring 自身が、クラスタ相関を無視した下限だと書いている。
本実験のクラスタは強い——1パス内の 60 試行は 5 本の diff と3枚の persona を共有する。
したがって**主解析は試行水準の McNemar、感度解析は種水準（20クラスタ）の符号順列検定**を併記し、
両方が同じ向きを出したときだけ「効いた」と読む。
n<10 の割合には Wilson 95% 区間を必ず付ける（`drill.md` ③-b の必須規則）。

---

## 5. 帰無結果の事前宣言 — 見る前に決めておく

データを見てから決めると、どんな結果でも何か言えてしまう。以下は**実行前に固定**する。

**主要指標**：A と B の**通算検出率の差**（Σdetected / Σseeded を合算カウントから再計算。
`aggregate_drill_confidence` の集計規則に従い、`scorer_version` 不一致・`adjudicated: false`・
`judge.offline` 真・`judge.calls == 0` の行は落とす）。

**「効かなかった」と宣言する条件（3つすべて）**:

1. |検出率(A) − 検出率(B)| が、帰無対照の差 |検出率(A) − 検出率(A')| **以下**である。
2. 種水準の符号順列検定で p ≥ 0.05。
3. 差の 95% 区間が ±10pt（§4 で狙った効果）の中に収まっている。

**「効いた」と宣言する条件**：上の1〜3がすべて破れ、かつ**向きがパイロットと本体で一致**していること。
向きが揺れた場合は「効いた」とは書かない（アーム名を伏せた再現1回で決める）。

**副次指標**（`clean_fp_rate` / `severity_accuracy` / `blocking_accuracy`）は**記録するが主要にしない**。
検出率が動かず副次だけ動いた場合、それは「所見の出し方が変わった」であって
「recency が効く」の証拠にはならない——別の仮説として次の調査に回す。

**事後に切り直さない**：persona 別・ケース別・severity 別の再集計は探索として報告してよいが、
帰無/対立の判定には使わない。

**帰無だった場合に何が言えるか（ここも先に書く）**：
言えるのは「**Policy を末尾に置くことは、この測定条件下で reviewer の検出率を動かさない**」までである。
§8 は「順序が効かないと出れば、5分割を捨てる根拠が実測として立つ」と書いているが、
本実験が測るのは**配置順であって5分割そのものではない**。帰無で直接落ちるのは
COMPOSE.md §5 の「recency が効く末尾にガードレール」という**理由づけ**であり、
persona / knowledge / instruction / output-contract / policy という**区分の是非は別の実験が要る**。
ここを混ぜると、測っていない主張がまた1つ増える。

---

## 6. コスト見積もり — 読んだ数字から

**プロバイダ呼び出し**（1パス・1アームあたり）:

| 種類 | 本数 | 根拠 |
|---|---:|---|
| レビュー | 15 | 5 ケース × 3 persona |
| judge | ≤ 60 | credit されたペアごとに1回。上限は 4 非クリーンケース × 5 種 × 3 persona |

パイロット（A・A' 各1パス）＋本体（A・B 各4パス）＝ 10 パス:

- レビュー呼び出し **150 回**
- judge 呼び出し **最大 600 回**（実際は credit されたペアだけなので、検出率 40% なら 240 回前後）
- 合計 **最大 750 回**

**入力サイズ（実測）**：`security-reviewer` + `parallel-review` + `review-findings` + `pre-push-review` を
実際に解決すると facet 本文は **11,515 バイト**（persona 2,423 / knowledge 1,855 / instruction 5,158 /
output-contract 1,567 / policy 512）。これに `py-mixed-violations` の base+head **5,855 バイト**が乗って
1レビュー呼び出し ≈ **17.4 KB ≈ 5.8k トークン**。judge プロンプトは実際に組んで **2,250 バイト ≈ 750 トークン**。

- レビュー入力合計 ≈ 150 × 5.8k ≈ **0.9M トークン**、出力は所見1本 1〜2k として ≈ 0.2M
- judge 入力合計 ≈ 600 × 0.75k ≈ **0.45M トークン**、出力は1語

**壁時計時間は、このリポジトリの数字からは出せない。** `.rig/runs.jsonl` の 427 行にある
`provider_generator` / `provider_verifier` の中央値は **1呼び出し 26〜27 ms** で、これは mock プロバイダの
run であり実プロバイダの待ち時間ではない。実測に近い唯一の記述は
`benchmarks/writing-tasks/jp-natural-writing/hidden_check.py:1831` の
「4アームを `MAX_PARALLEL=2` で回すと1時間超」＝32 生成 / 並列2 で **1呼び出し 3.75 分超**だが、
これは 2,500 字の長文生成であって 3 KB の diff レビューではない。
**したがって、たとえば「1呼び出し 60〜120 秒・並列2〜4」と置けば 750 呼び出しで 3〜12 時間**という
桁の見当は付くが、この係数は**測っていない仮定**である。実行する側が最初のパスで測って置き換えること。

---

## 7. 確認できなかったこと

正直に列挙する。以下はすべて「読んで分かった」ではなく「分からないまま残した」ものである。

- **本計画は1行も実行していない。** レビュー呼び出しも judge 呼び出しも0回。上の数値はすべて
  ソースとコーパスの読み取りと、プロバイダを呼ばない Python 計算（プロンプト連結・サイズ計測・
  サンプルサイズ関数）から出ている。
- **基準検出率が分からない。** drill の過去 run が0件なので、A の検出率がいくつになるかの見当が無い。
  床（誰も何も見つけない）や天井（全員が全部見つける）に張り付くと、順序の効果は測れても現れない。
  パイロットはこの確認も兼ねる。
- **`adverse_rate` が分からない。** 帰無対照を1回も走らせていないので、§4 の表のどの列を読むかは未定である。
- **judge の較正が現行プロバイダで通るか分からない。** 同梱台帳は `codex` の 73 行で、
  この環境で `codex` は動かない。`claude` を judge にしたときの `ideal`/`negative`/`attack`/`waiver` の
  一致率は未知である。
- **in-session 経路の順序遵守率が分からない。** 散文の §5 を、オーケストレータ役のモデルが実際に
  どのくらい守っているかは測っていない。守っていないなら、コード経路で得た結論の適用範囲はさらに狭い。
- **一般化できない範囲**：本実験はレビュー step の1タスク種・1モデル系統・5本の固定 diff・20 種でしか
  測らない。生成 step（`compose_step_prompt` の本来の用途）の配置順については何も言えない。
- **`--replay` は使わない。** アーカイブが無いためであり、機能が使えないという意味ではない。
  本計画が1回走れば `.rig/replay/` と `.rig/drill-results.jsonl` が生まれ、以後は §8 が想定した
  replay 形の再確認ができるようになる。
