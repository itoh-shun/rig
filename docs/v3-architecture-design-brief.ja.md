# rig V3 設計ブリーフ — 能力レジストリとポート化による内部再構成

作成日: 2026-09-09
状態: 合意済み（セクション別）。第 1 段（契約テストの補強）と第 2 段（能力レジストリの宣言）は
実装済み。第 3 段（6 ポート化）は進行中で、5 本の柱のうち govern の 1 本が済んだ。残る 4 本と
第 4〜5 段は未着手。
経緯: `/rig:brainstorm` による壁打ち。当初の判断の根拠は、すべて本リポジトリに対する読み取り専用の
実測。第 2 段で書き足した数値は実行して確かめている（末尾「測定の限界」を参照）。

## 固めた狙い

rig の内部を、能力の宣言が1箇所に集まり、判定が副作用を持たず、推論と計算の境界が型で表現される構造に作り替える。観測可能な契約と PACKS 互換は凍結したまま行う。

## 前提・制約（実測値）

| 項目 | 実測 |
|---|---|
| Python | 173 ファイル / 63,012 行 |
| パッケージ内 import | 585 本 |
| 循環依存（強連結成分） | 実行時 6 件。ほかに `if TYPE_CHECKING:` でのみ成立するものが 1 件 |
| 最大モジュール | `orchestrate/providers.py` 3,644 行 / 責務 12 種 |
| ハブ | `workbench/state.py` を 37 モジュールが import |
| god モジュール | `workbench/cli.py` が 40 本の import 文で 39 モジュールに届く |
| 層 | `workbench/` と `orchestrate/` が双方向（10 対 3） |
| `print()` | 1,073 箇所（うち `workbench/` に 513） |
| `subprocess` | 98 箇所（うち `shell=True` 1 件） |
| `write_text` / `open(w,a)` | 53 / 17 |
| `os.environ` / `os.getenv` | 68 箇所 |
| 壁時計の読み | 43 箇所。綴りを 3 種から 13 種に広げて 47 箇所 |
| CLI 登録機構 | 3 階層で 3 方式（if/elif 連鎖、argparse、dict） |
| テスト | 206 ファイル / 73,776 行 / 4,165 関数 |
| 内部 API に直接結合 | 106 ファイル / 1,536 関数（リファクタで倒れる） |
| CLI 経由（サブプロセス） | 85 ファイル / 2,405 関数（リファクタを生き残る） |
| 散文とコードの比 | `skills/` 配下 201 `.md` 対 `rig_workbench/`+`scripts/` 187 `.py` |

効果の数は第 3 段で数え直した。行マッチをやめて AST 走査にしたため、docstring や文字列
リテラル内の出現を含まない。値は `tests/test_architecture_inventory.py` の
`BASELINE_EFFECT_SITES` が天井として凍結している。壁時計の読みは最初の調査で数えていなかった
項目であり、同じファイルの `CLOCK_READS` が何を読みと数えるかを定義している。

凍結対象（観測可能な契約）:

- PACKS のディスク契約（`pack.yaml` 正準 JSON、キー集合完全一致、`ASSET_DIRS` 固定、5 層優先順位、資産ハッシュ検証）
- `.rig/` レイアウト 30 以上のパス
- バージョン付き JSON スキーマ ID（当初の見積もりは 40 種。第 1 段で `tests/test_schema_registry.py` が実際に凍結したのは 38 種）
- 終了コード（`OK=0` / `REJECTED=1` / `ERROR=2` と各コマンド固有コード）
- コンソールエントリポイント 5 本

talk は hook（`hooks/inject-talk-mode.sh`）で常時前段にあり、人が直接打つ入口は実質そこ 1 つ。約 90 の `rig-wb` サブコマンドと 30 の `/rig:*` は、主に talk が引く routing 表として機能している。

## 決めたこと

### 1. 不変条件

観測可能な契約のみ凍結し、Python の内部 API は自由とする。

根拠: 契約側の安全網は CLI 経由テスト 85 ファイル・2,405 関数分が既にあり、スキーマ ID 40 種と `.rig` レイアウトも押さえられている。ここを厚くしてから内部を切れば、内部結合した 1,536 関数は安全網ではなく荷物として扱える。

退けた案: import パスまで凍結する案は、実行時の循環 6 件と双方向依存がそのまま残り、リファクタリングではなく整頓に終わる。非推奨期間つきの段階移行案は、凍結対象を増やす理由が実測から出てこなかった。

### 2. 「統合」の意味

名前を減らすのではなく、能力レジストリ化する。全機能を 1 箇所に宣言し、CLI・talk・MCP・GitHub Action をその投影にする。

根拠: 実測の症状はどれも機能の多さではなく、登録と実行の散らばりに由来する。名前を減らしても散らばりは直らない。宣言を 1 箇所に集めれば、talk の routing 表も CLI のパーサも MCP のツール定義も同じ表から引ける。現在それぞれ別々に手書きされている状態が消える。

退けた案: 約 90 のサブコマンドを十数個の動詞に畳む案は、85 テストファイルの土台を同時に畳む。かつレジストリが先にあれば「この能力は動詞 X に畳む」と宣言するだけで済むため、後段に回す。

### 3. 骨格

ポートとアダプタの 4 層（判定層 / ポート / アダプタ / シェル）。判定層は柱ごとに分け、ポートは共通とする。

SOLID との対応:

| 症状 | 破れている原則 |
|---|---|
| `providers.py` 3,644 行に 12 責務 | 単一責任 |
| サブコマンド追加が if/elif 連鎖の編集、センサー追加が 3 ファイルの編集 | 開放閉鎖 |
| `state.py` を 37 モジュールが引く | インタフェース分離 |
| 実行時の循環 6 件を関数内 import で回避、`workbench` と `orchestrate` が双方向 | 依存性逆転 |

依存性逆転が根。関数内 import は循環を消したのではなく隠している。

共通ポート 6 本（現状の散らばりから逆算）:

| ポート | いまの散らばり | 既存の部分実装 |
|---|---|---|
| Presenter | `print` 1,073 箇所 | なし |
| ProcessRunner | `subprocess` 98 箇所 | なし |
| FileStore | `write_text` 53 / `open(w,a)` 17 | `orchestrate/secure_fs.py` |
| Env | `os.environ` 68 箇所 | なし |
| GitRepo | `providers.py` / `hostcheck.py` / `detection_corpus.py` に分散 | なし |
| Clock | wiki の 180 日判定などが直接時刻を読む。43 箇所 | なし |

規律は 1 つだけ: 判定層にこの 6 本以外を持ち込ませない。これは散文ではなく import ルールとして機械的に検査する。rig 自身が抱える「散文止まりのルール」を新しく作らないため。

第 3 段で 6 本は `rig_workbench/ports/__init__.py` に protocol として入り、実装は
`ports/local.py` の 1 モジュールだけに置いた。import ルールの機械検査は
`tests/test_layering_contract.py` である。詳細は下記「第 3 段の実測」を参照。

退けた案: 縦割りのみの案は循環は消えるが、`print` 1,073 箇所と `subprocess` 98 箇所が柱の中に残る。柱の切り方だけを借用する。依存の向きだけ直す案は実行時の循環 6 件が消えるだけで、V3 の実体がない。

### 4. 推論と計算の境界

来歴を三値の型として能力レジストリに宣言し、`gate --set` の可否を型が決める。

- `computed` — 判定層のセンサーが決める。`--set` を拒否する。
- `attested` — モデルが判断し、生成者と別のプロバイダの検証者が確認する。誰が主張し誰が確認したかを記録する。
- `unobserved` — 誰も決めていない。`passed` に畳まない。

実測（`rig_workbench/workbench/config.py` の `GATE_PRESETS`、34 基準）:

| 状態 | 件数 |
|---|---|
| センサーが判定する | 6 |
| コードから参照はあるが状態を読むだけ | 2 |
| コードに現れず宣言だけで通る | 26 |

`no_gate_tampering` は改竄を検知するのに、`tests_pass_or_explained` は誰も検査していない。

**この形は外部から持ち込むものではない。** 同じ思想が既に本リポジトリ内に 3 回、別々に実装されている。

1. `rig_workbench/workbench/prompt_regression.py` — `--set` を拒否し、機械の eval にしか判定させない。
2. `rig_workbench/workbench/assurance.py` — 記録のない軸を `{"observed": false, "reason": ...}` として運び、成功に畳まない。
3. `rig_workbench/orchestrate/runstate.py` の `gate_outcome` — 判定者が `self` / `generator` / `producer` である票を自己採点として弾く。

V3 でやるのは新しい概念の導入ではなく、既にここで発明されたものを 1 つの型に畳んで全体に効かせること。

契約への影響: `grade` を新しいフィールドとして足す。既存の status 値もスキーマ ID も据え置きにできるため、凍結した契約は壊れない。

退けた案: センサー以外を一律 warning にする二値案は、クロスプロバイダ検証が自己申告と同格に落ち、rig の主要機能が死ぬ。記録のみの現状維持案は 26 件が自己申告のまま通り続ける。

### 5. `attested` の強度

独立検証つき（生成者と別プロバイダの検証者が確認したもの）のみ合格に数える。単一の主張しかないものは `unobserved` に落とす。

根拠: `gate_outcome` が既に自己採点票を弾く実装を持っており、その規則を全基準へ広げるだけで済む。新しい仕組みが要らない。

これは構造ではなく強度のつまみである。実装後に緩い側（単一主張の `attested` も当面は合格に数える）から始める余地を残す。

### 6. talk の位置

talk は意図を作るだけで、判定はしない。

会話の出力が決して等級を持たないため、入口が非決定的でも determinism-by-gate は崩れない。hook で talk が常時前段にいる構成と、rig の品質主張は両立する。

### 7. 移行順序

5 段。各段の終了条件は「木が緑であること」。

1. **契約テストの補強。済。** 内部を一切触らない。CLI 経由、スキーマ ID、`.rig` レイアウトの被覆を上げる。後続すべての土台。凍結したのは次の 6 本。
   - `tests/test_schema_registry.py` — スキーマ ID 38 種
   - `tests/test_exit_code_surface.py` — 実プロセスが返した終了コード
   - `tests/test_rig_layout_contract.py` — `.rig` のディスク上のレイアウト
   - `tests/test_pack_disk_contract.py` — PACKS のディスク契約
   - `tests/test_cli_surface_contract.py` — `--help` が挙げるサブコマンドの集合
   - `tests/test_entrypoint_contract.py` — コンソールエントリポイント 5 本
2. **能力レジストリを 1 本立てる。済。** 宣言だけ置き、実行は既存に委譲した。`rig_workbench/registry/` に 139 件を宣言し、CLI・MCP 2 種・`action.yml`・スラッシュコマンド 30 枚との照合をテストにした。ブリック解決の宣言も `registry/bricks.py` に集めた。実行経路は 1 行も変えていない。ただし散文と実装のずれは**測って記録しただけで、解消していない**。`SKILL.md` と `resolve.md` の側は手つかずである。宣言を集めれば同時に解消できるという当初の見込みは外れた。§9 の「最初の 1 回に手順を要求しない」も、下げるべき値がもともと 0 だったと分かり、テストで固定した。
3. **6 ポートを入れ、判定層を柱ごとに切り出す。進行中。5 本の柱のうち 1 本が済んだ。** 1 柱ずつ、移すたびに緑を確認する。実行時の循環 6 件はこの段で構造的に消え、関数内 import による回避をやめられる。第 2 段が記録した表面のずれ（下記「第 2 段の実測」）を閉じるのも、この段の判断になる。済んだのは govern で、下記「第 3 段の実測」に何を測って選び、何に気づいたかを書いた。残る 4 本の見積もりは次のとおり。数値はすべて `tests/test_architecture_inventory.py` の天井と `pyproject.toml` の台帳の行から取ってある。

   | 残りの柱 | モジュール | 効果地点の合計 | 台帳の行 |
   |---|---|---|---|
   | `eval` + `packs` + `orchestrate` の 2 本（12 モジュールの循環で 1 つに繋がる） | 36 | 129（eval 48 / packs 81） | 16 print・31 banned、54 print・18 banned |
   | `orchestrate` の残り | 20 | 272（ラチェットはディレクトリ単位で数えるため `orchestrate` 全体の値） | 211 print・47 banned |
   | `workbench` | 58 | 562 | 513 print・31 banned |
   | `rig_workbench/` 直下の 31 本（袋） | 31 | 285 | 27 行に分かれて 195 print・69 banned |

   ここに `validation` は無い。入次数 0 のシェルであり、柱として扱わない（理由は下記）。
   済んだ 1 本 govern は 11 モジュール・効果地点 87 で、4 本のどれよりも小さい。
   **第 3 段の 5 分の 4 はまだ始まっていない。**
4. **来歴型を入れ、34 基準を分類する。未着手。** センサーを作るか、`attested` に落とすか、`unobserved` と認めるかを 1 件ずつ決める。
5. **旧経路を落とす。未着手。** 二重定義の期間を閉じる。

2 と 3 の間で一度リリースを切れる。レジストリだけ入った状態は互換を壊さないため、V3 を一度に出さずに済む。第 3 段に入ったいまも、まだその位置にある。govern の 1 柱は凍結した契約を 1 つも壊していない。サブコマンドの集合も終了コードもスキーマ ID も同じで、変わったのは `--help` の文面 7 行だけである。

退けた案: ストラングラー単独は二重定義の期間が長い。契約テスト先行単独は「一気に切る」区間が長く、その間は赤のままになる。両者を直列に組む。

### 8. Faceted Prompting は置き換えず、測る

借り物の分類法（persona / knowledge / instruction / output-contract / policy の 5 分割と、その配置順）を新しい手法に差し替える案を検討し、**採らない**と決めた。代わりに、その効果を `drill` で測る調査を独立に立てる。

根拠:

- **置き換えは目的に届かない。** この作業の狙いは「借りたものを、実測と検証をした自前の構成にする」ことである。決まっていない新手法への差し替えは、測っていない主張を別の測っていない主張に置き換えるだけで、性質が変わらない。
- **借りていること自体は弱点ではない。** README は出典を明記して採用している。問題は出典ではなく、採用後に一度も測っていないこと。「recency が効くから policy を末尾に置く」という主張を裏づける計測は、本リポジトリに存在しない。
- **実測は分類法を問題だと言っていない。** ペルソナが実際に使う frontmatter キーは 3 種（`name` / `description` / `inject`、`inject` は 67 件中 25 件）、レシピが実際に使うトップレベルキーは必須 5 種のみで、文書が挙げる任意キーは出荷・パックのレシピに 1 件も現れない。分類は軽い。壊れているのはセンサーのない 26 基準、実行時の循環 6 件、散らばった `print` 1,073 箇所であって、facet の分け方ではない。
- **代償が大きい。** `facets/personas` ほか 5 つのディレクトリ名は PACKS のディスク契約（`ASSET_DIRS`）であり、`tests/test_pack_disk_contract.py` が凍結している。出荷ペルソナ 67 枚、instruction 59 枚、レシピ 32 枚、および利用者が自作したパックすべてが影響を受ける。
- **保管の形と合成の手法は分離できる。** 将来どうしても入れ替えるときも、ディレクトリ名を凍結したまま中身の意味と組み上げ方だけを差し替えれば、PACKS 互換は保てる。この逃げ道があるので、いま急ぐ理由がない。

測ることで、どちらに転んでも前に進む。順序が効かないと出れば、5 分割を捨てる根拠が実測として立つ。効くと出れば、この主張に数字を持つことになり、「借りたまま」ではなく「借りて、確かめた」に変わる。

これは §4（来歴を型にする）と同じ思想である。「効くと言われている」と「効くと測れた」を分ける規律に、プロンプト構成だけ例外を作る理由がない。

### 9. 会話を正本にし、最初の1回に何も要求しない

これまでの版の弱点は、導入した時の分かりにくさと、使い始めるまでのハードルの高さである。V3 の
狙いにこれを加える。あわせて §2 の能力レジストリの向きを決める。**正本は意図であり、CLI はその
投影である。**

#### 壁の実測

| 観測 | 値 |
|---|---|
| `README.md` | 1,270 行超 |
| `skills/engine/SKILL.md` | 720 行 |
| スラッシュコマンド | 30 |
| サブコマンド | 116 |
| 最初に案内される手順 | 2（`/rig:setup` で CLI 導入 → `/rig:init` で manifest 生成） |
| 実際に必要な手順 | 0（第 2 段で実測。下記「測る」を参照） |
| パックの利用 | 資産ごとの信頼承認と環境変数 |

決定的な観測がひとつある。**このブリーフを書き、第1段を実装し、全件テストを2回通した一連の作業は、
`rig-wb` を一度もインストールせずに完了した。** `scripts/*.py` が直接呼ばれるためである。つまり
Claude Code から使う限り CLI は最初から不要なのに、入口の案内は CLI の導入から始まっている。壁の
一部は、要らないものを要ると言うことで作られている。

#### レジストリの向き

| 案 | 正本 | 会話 | CLI |
|---|---|---|---|
| A | **意図**。「何をしたいか」と「その前に何が要るか」で宣言し、CLI 定義を導く | 一級 | 投影 |
| B | CLI。argparse を正本にし、会話は説明文を読んで振り分ける | 現状のまま | 現状維持 |
| C | 両方を手で書く | 二重管理 | 二重管理 |

**A を採用。** argparse が必要とするのはフラグ名・型・ヘルプ文だが、会話が必要とするのは
**意図の記述・前提条件・実行前に何が起きるかの一文**である。B で組むと、会話は CLI の都合で
決まった語彙を後から解釈し続けることになり、それが現状である。

#### レジストリだけでは壁は消えない

| 壁 | レジストリで直るか |
|---|---|
| 語彙が多い（30 + 116） | 直る。名前を覚える必要がなくなる |
| CLI の導入が最初の手順 | 直らない。案内の順序の問題 |
| manifest の生成が要る | 直らない。既定値で動くかの設計 |
| パックの信頼承認 | 直らない。必要になった瞬間に訊く設計 |

したがって第2段の目標に **「最初の1回に手順を要求しない」** を明示で入れる。`/rig:go "<やりたいこと>"`
と言えば、CLI 未導入でも manifest 無しでも最初の結果まで到達する。CLI・manifest・パックは
**必要になった瞬間に、理由を添えて提案される**。導入の案内をなくすのではなく、前倒しをやめる。

#### 測る（第 2 段の実測）

「使いやすくした」と散文で書かないため、達成を測れる形にする。以下は第 2 段で実測した値であり、
値が崩れたときに落ちるテストを 1 行ごとに併記する。テスト名のない数字はここに書かない。

| 測る対象 | 実測 | 崩れたら落ちるテスト |
|---|---|---|
| 最初の有用な結果までに人が踏む手順の数 | 0（案内は 2） | `tests/test_first_run_cost.py::test_a_first_run_in_a_bare_git_repository_needs_no_cli_install_no_manifest_and_no_human` |
| `rig-wb` 未導入で完走できるか | できる。PATH から `rig-wb` を持つ項目をすべて外した環境で `python3 scripts/workbench.py new "<やりたいこと>" --type bugfix` が exit 0 で run を作る | 同上（PATH の除去が効いていることを、子プロセスの `shutil.which` で先に確かめてから測る） |
| manifest 無しで完走できるか | できる。未承認の `.claude/rig.md` が置いてあっても stderr に警告 1 行を出して継続する（exit 2 の拒否ではない） | `tests/test_first_run_cost.py -k degrade` の `test_an_unconsented_project_manifest_degrades_to_one_warning_rather_than_stopping_the_run` |
| 実行前に人が決めなければならない項目の数 | 0。stdin を `/dev/null` にしても `new` の経路は何も訊かない | 上記 1 行目のテスト（`EOFError` と対話プロンプトの形の両方を見る） |
| パックの信頼承認 | 訊かれない。既定の routing が出荷の `core` 層で解決するため、承認する対象がそもそもない | `tests/test_first_run_cost.py -k degrade` の `test_the_first_run_degrades_to_the_shipped_core_tier_instead_of_demanding_pack_trust` |
| hostcheck | 助言にとどまる（exit 0 か 3）。1 は `--strict` を渡したときだけ | `tests/test_first_run_cost.py -k degrade` の `test_hostcheck_degrades_to_advisory_in_a_first_run_and_never_exits_the_strict_failure` |

**これは「第 2 段で手順を減らした」という話ではない。** 要求はもともと 0 で、案内だけが 2 を要求して
いた。第 2 段でやったのは、0 であることを一度きりの観察からテストに移し、手順が 1 つでも戻ったら
赤くなるようにしたことである。壁は技術ではなく文書の側にあった、というのが測定の結論であり、
「使いやすくした」と書くよりこちらのほうが有用で、かつ正確である。案内の側（README と
`/rig:setup` から始まる導線）はまだ直していない。

真の前提は 2 つだけである。git リポジトリであることと、`python3` があること。

これは §4（来歴を型にする）と同じ規律である。主張ではなく測定に置く。

## 第 2 段の実測 — 表にして分かったこと

能力レジストリ（`rig_workbench/registry/`）は宣言だけで、実行経路は 1 行も変えていない。表の規模と、
表が投影されるはずの表面との照合結果は次のとおり。

| 測る対象 | 実測 | 崩れたら落ちるテスト |
|---|---|---|
| 宣言された能力 | 139（トップレベル 39 / `wb` 51 / `govern` 10・`pack` 23・`eval` 10・`baseline` 3・`githooks` 3 で 49） | `tests/test_capability_registry.py::TestContainer::test_every_dispatchable_verb_is_declared_once` |
| 表と実 CLI の一致 | 6 つの親グループそれぞれ、およびトップレベルの動詞集合が完全一致。どちらの側も実行時に読む（凍結した写しは持たない） | `tests/test_capability_registry_vs_cli.py::test_each_parent_group_offers_exactly_the_verbs_the_registry_declares_under_it` / `::test_the_verbs_the_top_level_dispatcher_accepts_are_exactly_the_ones_the_registry_declares` |
| ヘルプに出ないトップレベル動詞 | 39 中 15。打てば答えるが、`--help` にも契約テストにも README にも出てこない | `tests/test_capability_registry_vs_cli.py::test_exactly_fifteen_dispatchable_top_level_verbs_are_missing_from_the_help_text` |
| 出力スキーマを宣言する能力 | 139 中 22。すべて第 1 段の凍結集合（38 種）の中にある | `tests/test_capability_registry_vs_surfaces.py::test_every_output_schema_a_capability_declares_is_an_id_the_frozen_registry_pins` |
| スラッシュコマンド | 30 枚。うち 9 枚はどの rig コマンドも名指さない。残り 21 枚が到達する能力は 26 種 | `tests/test_capability_registry_vs_surfaces.py` の `::test_the_slash_command_surface_is_still_the_thirty_files_the_brief_counted`、`::test_every_slash_command_names_at_least_one_declared_capability`、`::test_the_commands_recorded_as_naming_no_capability_still_name_none`、`::test_the_slash_command_scan_still_reaches_the_capability_table` |
| MCP のツール | stdio 14 本 / remote 7 本。stdio の 1 本には対応する能力がない | `tests/test_capability_registry_vs_surfaces.py::test_both_mcp_servers_still_publish_the_tool_lists_this_file_knows_how_to_read` / `::test_every_tool_the_stdio_mcp_server_hands_out_maps_to_a_declared_capability` |

見つかったものを、確かめられる形で並べる。第 2 段ではどれも直していない。閉じるのは第 3 段の判断である。

- **どのハンドラにも届かない動詞が 2 つ。** `list` と `review` は `rig_workbench/cli.py` の `_orch_delegates` にある。だが `rig_workbench/orchestrate/cli.py` の `COMMANDS` にはない。`python3 -m rig_workbench.cli list` はオーケストレータの module docstring を表示し、exit 1 を返す。（`tests/test_capability_registry_vs_cli.py`）
- **トップレベルの動詞 39 本のうち、凍結済みスキーマ ID を出すものが 1 本もない。** `plan --json` も `fleet --json` も `schema` キーのない裸の JSON を出す。`rig.fleet/v1` は `fleet` が読む設定の名前であり、出力の名前ではない。封筒つきの ID はすべて `wb` と `govern` のサブ動詞に属する。（`rig_workbench/registry/entries_cli.py` の 39 件はすべて `output_schema=None`）
- **問い合わせに見えて書く動詞。** `rig-wb check` は実行中のステップが宣言したチェックコマンドを走らせる。結果は run-state に書く（`effect_class="writes-state"`）。名前から読める挙動と、実行前に見せるべき一文が食い違う。
- **取りに行くように見えて何も取りに行かない動詞。** `rig-wb pack sync` がするのは、`pack.yaml` の `assets` と `hashes` の作り直しだけである。見るのはディスクの実体のみ。`rig_workbench/packs/sync.py` は標準ライブラリと自パッケージしか import しない（`network="never"`）。逆向きの誤読も記録してある。`pack install` は名前から `network` と読まれ、次に URL 拒否の 1 関数だけを見て `never` と読まれた。実際は `sometimes` である。`<source>:<pack>@<version>` 形式のときだけ `git ls-remote` と `git fetch` に出る。
- **`govern` だけ終了コードの語彙が違う。** `rig_workbench/govern/cli.py` は `EXIT_OK, EXIT_ERROR, EXIT_NONCONFORMANT = 0, 1, 3` を自前で定義する。`_err` は 1 を返す。共通の `exitcodes.py` で 1 は「rig が判定して否と答えた」であり、govern のエラーはその番号に載っている。
- **MCP が CLI にない動詞を出している。** `scripts/mcp_server.py` の `rig_orchestrate_status` は `scripts/orchestrate.py status` を叩く。`status` は orchestrate 側の `COMMANDS` にはあるが、`_orch_delegates` にはない。そのため `rig-wb` に `status` を渡すと、`Unknown sub-command` と答えて exit 2 になる。歴史的な入口からしか届かない動詞を、MCP だけが提供している。（`tests/test_capability_registry_vs_surfaces.py`）
- **スラッシュコマンド 30 枚のうち 9 枚は rig コマンドを 1 つも名指さない。** `drill` `export` `forge` `import` `init` `knowledge` `orchestrate` `persona` `talk` の 9 枚である。どれも `facets/instructions/*` にセッション内で仕事を渡す。入口の 3 割は CLI に届いていない。（`tests/test_capability_registry_vs_surfaces.py`）

### ブリック解決 — 本ブリーフ自身の主張の訂正

第 2 段でブリック解決順序を `rig_workbench/registry/bricks.py` に宣言した。
`tests/test_brick_resolution_declaration.py` は「実際に walk された経路」を実行時に観測し、
宣言と突き合わせる。本ブリーフが「未解決の問い」に書いていた指摘は、この観測によって
次のように変わる。追記ではなく訂正である。

| 訂正 | 実測 | 崩れたら落ちるテスト（すべて `tests/test_brick_resolution_declaration.py`） |
|---|---|---|
| user 層は存在する。散文が挙げる場所にないだけである | コードが読む user 層は `~/.rig/packs/<pack>/<asset_dir>`（`$RIG_USER_HOME`、無ければホーム）。散文が約束する `~/.claude/rig/recipes` と `~/.claude/rig/personas` はどの経路も読まない。「コードは project 層しか読んでいない」は不正確だった | `test_the_user_tier_the_prose_promises_is_not_read` — 約束された両方の path に実際にファイルを置き、resolver が到達しないことを見る |
| 語彙のずれは `shipped` 対 `core` だけではない | `official`（`$RIG_HOME/packs/official/<pack>/…`）はコードが walk する層でありながら、どの散文の表にも出てこない。実体は `TIER_ORDER = ("project", "user", "org", "official", "core")` の 5 層 | `test_the_tier_vocabulary_is_the_one_the_packs_use`、および `KNOWN_PROSE_DRIFT` の `vocabulary-code-only` 2 件（`core` と `official`） |
| recipe の解決は 1 周ではなく 2 周する | `packs.resolver.resolve_all` が空で戻ったときに限り、`orchestrate.recipes.resolve_recipe` が `.rig/recipes` → `<org>/recipes` → 出荷 recipes を自前で歩く。manifest の `org_dir:` が届くのは、この 2 周目の `<org>/recipes` だけ | `test_the_recipe_fallback_walk_is_the_one_the_resolver_prints` |
| project 層の中の優先順位は、意図ではなく文字列の並びで決まっている | `.claude/rig/recipes` と `.rig/recipes` は同じ project 層。コードは `.rig/recipes` を先に列挙するのに、ソートキーが source path に落ちるため `.claude/rig/` が勝つ。誰も決めていない順序が実際の優先順位になっている | `test_the_declared_walk_is_the_walk_the_resolver_takes[recipe]` |

散文（`SKILL.md` と `resolve.md`）はまだ直していない。ずれは
`tests/test_brick_resolution_declaration.py` の `KNOWN_PROSE_DRIFT` に 1 件ずつ記録してある。
文書を直したら該当項目を消す、消さずに直すと赤くなる、という形で縛ってある。

## 第 3 段の実測 — 柱の切り方と、最初の 1 柱

### 柱を触る前に置いた 3 つのラチェット

第 3 段の作業量は 1 セッションに収まらない。進んだのか漂ったのかを言える状態にしてから
柱に触る必要があるので、先に 3 つ置いた。どれも「下げる方向にしか動かない」ものである。

| ラチェット | 凍結するもの | ファイル |
|---|---|---|
| 構造と効果の天井 | 実行時の循環 6 件と `if TYPE_CHECKING:` だけの循環 1 件を**集合として**、ハブ `workbench/state.py` の入次数 37、`workbench/cli.py` の出次数 39、パッケージ別の効果地点 6 種を天井として | `tests/test_architecture_inventory.py` |
| 禁止呼び出しの台帳 | `os.environ` から `time.clock_gettime_ns` まで 22 の名前を ruff の banned-api にし、`T20` と合わせて木全体で有効にする。未移行の経路は per-file-ignores 42 行で覆う | `pyproject.toml` |
| 層のルール | 移行済みの柱の判定層が import してよいのは、標準ライブラリ・自分の柱・`rig_workbench.ports` の 3 つだけ | `tests/test_layering_contract.py` |

循環を**件数ではなく集合**で凍結してあるのが要点である。件数なら、1 件消えて 1 件生えたときに
相殺されて何も起きない。集合なら新しい循環は必ず赤くなる。

台帳の行の意味も、ふつうの lint 抑制とは違う。到着時点では net-zero になるように未移行の経路を
覆ってあり、**柱がポートの背後に移ればその行が消える**。行の削除が移行の完了そのものであり、
横に書いてある件数がその代金である。実際、先頭にあった `rig_workbench/govern/**`
（68 print・12 banned）はもう無い。`tests/test_layering_contract.py` の `MIGRATED` に
`govern` が入るのと対になっている。

### 柱はディレクトリ名と一致しない

第 3 段を「6 ポート、1 柱ずつ」としか書いていなかったので、柱の実体を測った。第 2 段の末尾で
`rig_workbench/` は 179 モジュール、パッケージ内の import 辺は 597 本だった。ポート 2 本と
`registry/parser.py` が加わったいまは 182 モジュール・617 辺である。この graph を読むと、
ディレクトリ名は柱の名前になっていない。

- **`validation/` は柱ではなくシェルである。** 他パッケージからこのディレクトリへ入る辺が
  **0 本**。誰も依存していないものを先に移しても、層のルールが効いている証拠にならない。
- **`packs` と `eval` と `orchestrate` の一部は 1 本の柱である。** 12 モジュールの実行時循環が
  この 3 つをまたぐ。内訳は `packs` 8 本、`eval` 2 本、`orchestrate` 2 本
  （`graph` と `recipes`）。循環の中にある以上、別々には移せない。
- **`rig_workbench/` 直下の 31 本は層ではなく袋である。** 共通の役割がない。台帳がこの 31 本を
  1 行の glob で書けず、27 行に分けて並べているのも同じ形の現れである。`rig_workbench/*.py`
  の `*` は `/` をまたぐため、柱の行を全部死文にしてしまう。
- `registry/` は宣言だけで効果地点 0、`ports/` はアダプタ層で、どちらも柱ではない。

結果として、測って出た単位は柱 5 本とシェル 1 つになる。柱は govern、
`eval`+`packs`+`orchestrate` の 2 本、`orchestrate` の残り、`workbench`、直下の 31 本。
シェルは `validation` である。

この節の辺とモジュールの数は、すべて `tests/test_architecture_inventory.py` の走査で数えた。
12 モジュールの循環の中身は同じファイルの `BASELINE_RUNTIME_CYCLES` に 1 本ずつ書き出してある。

### なぜ govern を最初にしたか

基準は 3 つ、どれも測った値である。

| 基準 | govern | ほかの柱 |
|---|---|---|
| 実行時の循環に入っていない | 入っていない | `eval`・`packs`・`orchestrate` は 12 モジュールの循環で繋がり、`workbench` は内部に 3 件の循環を持つ |
| 他パッケージへの出辺が最小 | 2 本（`govern/cli.py` → `gitroot`、`conformance` → `workbench.reporting`） | `workbench` 40 本、`orchestrate` 31 本、`packs` 25 本 |
| 6 本のポートを全部使う最小の柱 | 11 モジュール・効果地点 87 で 6 種すべてが 1 以上 | 6 種そろうのは `orchestrate`（272）・`workbench`（562）・直下（285）のみで、どれも大きい |

3 つ目が決め手である。6 種そろっていない柱で始めると、使われないポートの設計が
最初の柱では検証されないまま残る。

効果地点の値は `BASELINE_EFFECT_SITES`、辺の数は同じファイルの走査である。移行済みかどうかは
`tests/test_layering_contract.py` の `MIGRATED` が持つ。

### 移して分かったこと

**効果はすべてポートの背後に入った。** 天井は測った値に下げてある。

| 種別 | 移行前 | いま |
|---|---|---|
| `print` | 68 | 0 |
| `subprocess` | 2 | 0 |
| `open(w,a)` | 1 | 0 |
| `write_text` | 6 | 6 |
| `os.environ` | 3 | 0 |
| 壁時計 | 7（3 綴りの walk）／8（13 綴りの walk） | 0 |

`write_text` の 6 だけが 0 でない。正直に書くと、これは移し残しではなく数え方の性質である。
6 件のうち 4 件はシェル `govern/cli.py` 自身の `pathlib` 書き込みで、`init`・`migrate`・`--out`
が作るファイルである。残る 2 件は `files.write_text(...)` というポート呼び出しであり、AST の走査は
受け手を解決せず属性名で数えるため、ポート経由でも 1 件に数える。2 に下げるのはシェルについての
主張になるが、その主張はまだ誰もしていない。

**判定層の最後の 1 辺は、移したのではなく逆転させた。** `conformance.py` は採点する run 記録を
取りに `workbench.reporting.read_all_tasks` を import していた。これはシェルに移しても
ポートのメソッドにしてもいない。`conformance` が記録に何を求めるかを自前の protocol
（`RunRecords`）として述べ、引数で受け取る形にした。記録を採点するのが仕事であり、
取りに行くのは仕事ではないからである。渡すのはシェルと `evidence.py` である。

**CLI は表の投影になった。** `rig_workbench/registry/parser.py` が `children("govern")` から
argparse を組む。`govern/cli.py` の `build_parser` はその生成物になった。出荷していた手書きの
パーサは削除せず `tests/test_generated_parser_equivalence.py` に転記してある。同じ場所で
import し直すと比較が自分自身との比較になって 1 件も落ちなくなるためである。突き合わせは
4 層で、動詞集合・action ごとの 8 属性・11 画面のヘルプ文字列・実引数ベクタの namespace と
stderr を見る。テストは同ファイルに 60 件ある。表にできない唯一のものはハンドラの束縛である。`Capability` は callable を
持てないので、`set_defaults(func=...)` は呼び出し側に残る。

書き下してみて分かったものを並べる。

- **`dest` の衝突が 3 件あり、うち 1 件はデータを壊していた。** `govern audit` の `--action` は、
  argparse が派生させる `dest` が同じ動詞の `action` 位置引数とぶつかる。生成したパーサでは
  位置引数の値を黙って上書きしていた。`mutation` の `--report` と `--format` も同じ形である。
  こちらは出荷コードが `report_flag` と `format_flag` を書いて避けていたのに、宣言がそう
  言っていなかった。`Flag` に `dest` を足し、`Capability` が同じ属性に落ちる 2 本を拒否する
  ようにした。表の 580 フラグのうち `dest` を書いているのは 4 本だけである。派生と同じ `dest` を
  書くと拒否されるので、書いてある 4 本は「argparse の派生が誤り」という主張として読める。
- **別名を表が言えなかった。** `wb route` は `--recipe` のほかに `--explicit-recipe` も同じ
  action として受ける。`Flag` に `aliases` を足した。正準の綴りは `name` のままにしてある。
- **フィールドにできない変換器がある。** `wb compose-options` の `--diff` は
  `non_negative_diff` という自前の変換器を `type=` に渡す。`Capability` は callable を拒むので、
  これはフィールドにならない。表は `type="int"` と言っており、負値の拒否は宣言の外にある。
  残り 4 柱で決める。
- **壁時計の読みが `validation/` にもう 1 件あった。** `validation/catalog.py` の
  `datetime.date.today()` である。
- **1 件目を通したラチェットの穴。** 狭い walk は `now`・`utcnow`・`time.time` の 3 綴りしか
  見ていなかった。`govern/cli.py` に `datetime.date.today()` が残ったまま、govern の天井は 0 と
  読めていた。0 と答える理由が真でないラチェットは、無いより悪い。綴りを 13 に広げ、ruff の
  禁止表と AST の走査が同じ集合を指すことをテストで表明した。再計測で上がったのは 3 か所ある。
  `orchestrate` 4→6、`validation` 0→1、`tests` 674→682 である。どれも新しい効果地点ではない。
  前の物差しが見えていなかった、既存の呼び出しである。

**利用者から見えた変化は `--help` の 7 行だけである。** 動詞 10 本の要約は出荷時と 1 バイトも
違わない。フラグの行が 7 つ変わり、内訳は 2 種類しかない。6 行はもともとヘルプが無かった引数に
説明が付いたもので、失われていない。残る 1 行は `govern can` の `permission` で、生きた
`PERMISSIONS` を埋め込んだ列挙を失った。`choice` として宣言すれば列挙は戻るが、未知の名前の拒否が
govern の exit 1 から argparse の exit 2 に移る。呼び出し側に見える契約のほうが重いので、
列挙を手放した。

### 開いている設計の問い

**1. ポート 6 本が名前を持たないものが 3 つある。**

- **プロセスの作業ディレクトリ。** `Env` は `expanduser` を持つが cwd を持たない。`GitRepo` は
  cwd をメソッドの引数にしていて、ポートの状態にしていない。木には `pathlib.Path.cwd()` が
  31 箇所、`os.getcwd()` が 2 箇所ある。
- **glob と区別されるディレクトリ列挙。** `FileStore` は `glob`・`is_dir`・`mkdir` を持つが
  `iterdir` を持たない。木には 31 箇所ある。
- **終了ステータス。** `exitcodes.py` を直接読む形のままで、柱の外のシェルの仕事に置いてある。
  層のルールが `ports` に唯一許す自パッケージ import がこの `exitcodes` である。

どれも効果ラチェットの 6 種に入っていないので、増えても赤くならない。govern 1 柱では要らな
かったが、残り 4 柱のどこかで必要になる。ポートのメソッドは呼び出し側から書く、という
やり方を守るなら、必要になった柱で足すのが筋である。

**2. 読み手が 2 人いるとき、2 つ目のフィールドが答えである。**

生成に切り替えた瞬間、10 本の動詞の要約が `intent` に置き換わり、`govern can` の
「exit 0 allowed / 3 denied」が消えた。`intent` は会話が発話と突き合わせるための文であり、
その規則は機構を書くことを禁じている。`--help` の 1 行が欲しいのは逆で、手引きを持っている人が
どの動詞を打つかを決めるための最短の文であり、CI が呼ぶ理由になる終了コードのような運用の
細部を含んでよい。畳むと、列には長すぎて発話には具体的すぎる文になる。

どちらかに寄せず、`Capability` に `summary` を足した。規則としてはこうなる。**1 つのフィールドを
2 人の読み手が読んでいて、要求が割れたと分かったら、片方を我慢させるのではなく 2 つ目の
フィールドを足す。** `summary` は `Flag.dest` と同じく任意で、`intent` と同じ文を書くと拒否される。
書いてあること自体が「ここは読み手が本当に割れている」という主張になる。表の大半には要らない。

残り 4 柱はこの問いに必ず出会う。言語についての同じ答え（会話向けの文は日本語、CLI が印字する
文は英語）は第 2 段で既に出ていた。内容についての答えがこれである。

## 検討した代替案

| 案 | 概要 | トレードオフ | 採否 |
|---|---|---|---|
| 契約のみ凍結 | CLI・スキーマ・PACKS を固定、内部は自由 | in-process テスト 1,536 関数の書き直し | 採用 |
| import パスも凍結 | Python API を維持 | テスト無傷だが循環と god モジュールが残る | 不採用 |
| 契約凍結＋内部 API 段階移行 | 非推奨期間つき | 移行中の二重メンテ | 不採用 |
| 能力レジストリ化 | 宣言を 1 箇所に、表面は投影 | コマンド数は減らない | 採用 |
| 動詞への集約 | 約 90 を十数個へ | 85 テストファイルの書き換え | 後段へ延期 |
| talk の routing のみ整理 | 内部を触らない | 何も直らない | 不採用 |
| ポートとアダプタ | 判定層を純粋化 | 移行が最大 | 採用 |
| 縦割りのみ | ファサード経由で循環禁止 | 副作用は柱の中に残る | 部分採用（柱の切り方のみ） |
| 依存の向きだけ修正 | 一方向化 | 実行時の循環 6 件が消えるだけ | 不採用 |
| 三値の来歴型 | computed / attested / unobserved | 34 基準の棚卸し | 採用 |
| 二値（計算以外は warning） | 単純 | クロスプロバイダ検証の価値が消える | 不採用 |
| 記録のみ（現状維持） | セット者を記録 | 26 件は自己申告のまま | 不採用 |
| Faceted Prompting を新手法に差し替え | 5 分割と配置順を捨てる | 手法が未定のため、未計測を未計測に置き換えるだけ。PACKS の `ASSET_DIRS` 凍結と衝突 | 不採用 |
| Faceted Prompting を drill で測る | 配置順を入れ替えたペルソナの検出率を比較 | drill の実行コスト | 採用（5 段の外の独立調査） |
| 意図を正本にする（会話一級） | 能力を意図で宣言し CLI を導く | 既存 argparse との二重管理を避ける設計が要る | 採用 |
| CLI を正本にする | argparse を正本、会話は説明文を解釈 | 会話が CLI の語彙に縛られ続ける。現状 | 不採用 |
| ストラングラー単独 | 新層を隣に建て 1 つずつ移す | 二重定義の期間が長い | 部分採用 |
| 契約テスト先行単独 | 網を厚くして一気に切る | 赤の区間が長い | 部分採用 |

## 未解決の問い

- **全件緑かどうかは、まだ確かめきれていない。** テストスイート自体は走らせた。第 2 段の時点で `python3 -m pytest -q -n auto` を通し、3,927 件が通過、2 件が落ちた。落ちた 2 件はどちらも実行環境の産物である。`tests/test_eval_runner.py` の read-only workspace は root で走らせると書けてしまう。`tests/test_japanese_writing.py` の出典検証は、attest された commit がこのチェックアウトの履歴に無い。第 1 段・第 2 段が足した契約テストはすべて緑。ただし `-x` で 2 件目の失敗時に打ち切ったため、「全件緑」を確かめたとは言えない。
- **外部から `rig_workbench` を直接 import している利用者の有無。** 契約凍結を採ったため実務上は守らない方針だが、確認はしていない。
- **26 基準それぞれの分類先。** センサーを作るか、`attested` に落とすか、`unobserved` と認めるかは 1 件ずつの判断で、まだ決めていない。
- **散文と実装のずれ（測定済み・未修正）。** 第 2 段で実行して確かめ、上記「ブリック解決 — 本ブリーフ自身の主張の訂正」のとおり内容を訂正した。残る問いは、どちらに寄せるか — `SKILL.md` と `resolve.md` を実装に合わせて直すのか、散文が約束している `~/.claude/rig/` の user 層を実装するのか。決めていない。
- **旧経路をいつ落とすか、版番号をどう割り当てるか。** 2 と 3 の間で切れることは決めたが、何を 3.0 と呼ぶかは未定。
- **`attested` を厳しい側で始めた場合に、いま通っている run のどれだけが落ちるか。** 実測していない。
- **ポートが名前を持たない 3 つを、いつ足すか。** 作業ディレクトリ、glob と区別されるディレクトリ列挙、終了ステータスである。govern の 1 柱では要らなかった。上記「第 3 段の実測」の「開いている設計の問い」を参照。
- **残り 4 柱をどの順で移すか。** 大きさは測ってある。ただし `eval` と `packs` と `orchestrate` をまたぐ 12 モジュールの循環をどこから崩すかは決めていない。
- **`wb compose-options --diff` の `non_negative_diff` をどう宣言するか。** 自前の変換器は `Capability` のフィールドにならない。表は `type="int"` と言い、負値の拒否は宣言の外にある。

## 次の一手

第 1 段と第 2 段は済んだ。第 3 段は進行中で、5 本の柱のうち govern の 1 本が済んだ。次は 2 本目である。

`/rig:tasks "rig V3 移行の第 3 段 — 2 本目の柱をポートの背後に移す。ラチェットの天井を下げ、pyproject.toml の台帳からその行を消す"`

5 段の移行はどれも大きく、まとめて実装に入ると検証点が失われる。柱 1 本ずつを検証可能な小タスクに割る。あわせて、第 2 段が記録して直さなかったものをこの段で 1 件ずつ決める。届かない動詞 2 つ、ヘルプに出ない 15 本、`govern` の終了コード、MCP と CLI のずれ、散文と実装のずれ。それぞれ閉じるのか、記録のまま残すのかを決める。govern の 1 柱ではどれも閉じていない。

**独立した調査（5 段の依存ではない）:** `/rig:drill` で Faceted Prompting の配置順を測る。配置順を入れ替えたペルソナを `--replay` で過去の差分に再適用し、検出率が動くかを見る。§8 の決定に対応する。段の順序には入れない。結果が出た時点で、捨てるか残すかを改めて決める。

## 測定の限界

本ブリーフの数値のうち、第 2 段と第 3 段で書き足したものは実行して確かめ、テスト名か
ファイル名を併記してある。§9 の「測る」と「第 2 段の実測」と「第 3 段の実測」である。
§3 の散らばりの数と「前提・制約」の表も第 3 段で数え直した。以下は確かめていない。

- テストスイート（`pytest`）は第 2 段で走らせた（上記「未解決の問い」参照）が、失敗 2 件で打ち切っており、全件を通し切ってはいない。
- `sensor-bench` の 10/10・0/7、ja-lint の 64/64 といった既存の公称値は、ソースと文書から読んだだけで再現していない。
- 循環依存が実行時に `ImportError` を起こさないことは静的にしか確認していない。
- 効果カウント（`print` / `subprocess` 等）は第 3 段で AST 走査に置き換えた。docstring や文字列リテラル内の出現はもう含まない。上の実測表はその値に直してある。ただし `write_text` は受け手を解決せず属性名で数えるため、ポート経由の呼び出しも 1 件に数える。
