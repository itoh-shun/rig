# rig V3 設計ブリーフ — 能力レジストリとポート化による内部再構成

作成日: 2026-09-09
状態: 合意済み（セクション別）。第 1 段（契約テストの補強）と第 2 段（能力レジストリの宣言）は
実装済み。第 3〜5 段は未着手。
経緯: `/rig:brainstorm` による壁打ち。当初の判断の根拠は、すべて本リポジトリに対する読み取り専用の
実測。第 2 段で書き足した数値は実行して確かめている（末尾「測定の限界」を参照）。

## 固めた狙い

rig の内部を、能力の宣言が1箇所に集まり、判定が副作用を持たず、推論と計算の境界が型で表現される構造に作り替える。観測可能な契約と PACKS 互換は凍結したまま行う。

## 前提・制約（実測値）

| 項目 | 実測 |
|---|---|
| Python | 173 ファイル / 63,012 行 |
| パッケージ内 import | 585 本 |
| 循環依存（強連結成分） | 7 件。すべて片側を関数内 import にして実行時に回避 |
| 最大モジュール | `orchestrate/providers.py` 3,644 行 / 責務 12 種 |
| ハブ | `workbench/state.py` を 37 モジュールが import |
| god モジュール | `workbench/cli.py` が 39 モジュールを import |
| 層 | `workbench/` と `orchestrate/` が双方向（10 対 3） |
| `print()` | 1,082 箇所（うち `workbench/` に 512） |
| `subprocess` | 98 箇所（うち `shell=True` 1 件） |
| `write_text` / `open(w,a)` | 63 / 27 |
| `os.environ` / `os.getenv` | 69 箇所 |
| CLI 登録機構 | 3 階層で 3 方式（if/elif 連鎖、argparse、dict） |
| テスト | 206 ファイル / 73,776 行 / 4,165 関数 |
| 内部 API に直接結合 | 106 ファイル / 1,536 関数（リファクタで倒れる） |
| CLI 経由（サブプロセス） | 85 ファイル / 2,405 関数（リファクタを生き残る） |
| 散文とコードの比 | `skills/` 配下 201 `.md` 対 `rig_workbench/`+`scripts/` 187 `.py` |

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

退けた案: import パスまで凍結する案は、循環 7 件と双方向依存がそのまま残り、リファクタリングではなく整頓に終わる。非推奨期間つきの段階移行案は、凍結対象を増やす理由が実測から出てこなかった。

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
| 循環 7 件を関数内 import で回避、`workbench` と `orchestrate` が双方向 | 依存性逆転 |

依存性逆転が根。関数内 import は循環を消したのではなく隠している。

共通ポート 6 本（現状の散らばりから逆算）:

| ポート | いまの散らばり | 既存の部分実装 |
|---|---|---|
| Presenter | `print` 1,082 箇所 | なし |
| ProcessRunner | `subprocess` 98 箇所 | なし |
| FileStore | `write_text` 63 / `open(w,a)` 27 | `orchestrate/secure_fs.py` |
| Env | `os.environ` 69 箇所 | なし |
| GitRepo | `providers.py` / `hostcheck.py` / `detection_corpus.py` に分散 | なし |
| Clock | wiki の 180 日判定などが直接時刻を読む | なし |

規律は 1 つだけ: 判定層にこの 6 本以外を持ち込ませない。これは散文ではなく import ルールとして機械的に検査する。rig 自身が抱える「散文止まりのルール」を新しく作らないため。

退けた案: 縦割りのみの案は循環は消えるが、`print` 1,082 箇所と `subprocess` 98 箇所が柱の中に残る。柱の切り方だけを借用する。依存の向きだけ直す案は循環 7 件が消えるだけで、V3 の実体がない。

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
3. **6 ポートを入れ、判定層を柱ごとに切り出す。未着手。** 1 柱ずつ、移すたびに緑を確認する。循環 7 件はこの段で構造的に消え、関数内 import による回避をやめられる。第 2 段が記録した表面のずれ（下記「第 2 段の実測」）を閉じるのも、この段の判断になる。
4. **来歴型を入れ、34 基準を分類する。未着手。** センサーを作るか、`attested` に落とすか、`unobserved` と認めるかを 1 件ずつ決める。
5. **旧経路を落とす。未着手。** 二重定義の期間を閉じる。

2 と 3 の間で一度リリースを切れる。レジストリだけ入った状態は互換を壊さないため、V3 を一度に出さずに済む。第 2 段まで終えたいまは、実際にその位置にいる。

退けた案: ストラングラー単独は二重定義の期間が長い。契約テスト先行単独は「一気に切る」区間が長く、その間は赤のままになる。両者を直列に組む。

### 8. Faceted Prompting は置き換えず、測る

借り物の分類法（persona / knowledge / instruction / output-contract / policy の 5 分割と、その配置順）を新しい手法に差し替える案を検討し、**採らない**と決めた。代わりに、その効果を `drill` で測る調査を独立に立てる。

根拠:

- **置き換えは目的に届かない。** この作業の狙いは「借りたものを、実測と検証をした自前の構成にする」ことである。決まっていない新手法への差し替えは、測っていない主張を別の測っていない主張に置き換えるだけで、性質が変わらない。
- **借りていること自体は弱点ではない。** README は出典を明記して採用している。問題は出典ではなく、採用後に一度も測っていないこと。「recency が効くから policy を末尾に置く」という主張を裏づける計測は、本リポジトリに存在しない。
- **実測は分類法を問題だと言っていない。** ペルソナが実際に使う frontmatter キーは 3 種（`name` / `description` / `inject`、`inject` は 67 件中 25 件）、レシピが実際に使うトップレベルキーは必須 5 種のみで、文書が挙げる任意キーは出荷・パックのレシピに 1 件も現れない。分類は軽い。壊れているのはセンサーのない 26 基準、循環 7 件、散らばった `print` 1,082 箇所であって、facet の分け方ではない。
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
| 依存の向きだけ修正 | 一方向化 | 循環 7 件が消えるだけ | 不採用 |
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

## 次の一手

第 1 段と第 2 段は済んだ。次は第 3 段である。

`/rig:tasks "rig V3 移行の第 3 段 — 6 ポート（Presenter / ProcessRunner / FileStore / Env / GitRepo / Clock）を入れ、判定層を柱ごとに切り出す。1 柱ずつ、移すたびに緑を確認する"`

5 段の移行はどれも大きく、まとめて実装に入ると検証点が失われる。第 3 段だけを検証可能な小タスクに割る。あわせて、第 2 段が記録して直さなかったものをこの段で 1 件ずつ決める。届かない動詞 2 つ、ヘルプに出ない 15 本、`govern` の終了コード、MCP と CLI のずれ、散文と実装のずれ。それぞれ閉じるのか、記録のまま残すのかを決める。

**独立した調査（5 段の依存ではない）:** `/rig:drill` で Faceted Prompting の配置順を測る。配置順を入れ替えたペルソナを `--replay` で過去の差分に再適用し、検出率が動くかを見る。§8 の決定に対応する。段の順序には入れない。結果が出た時点で、捨てるか残すかを改めて決める。

## 測定の限界

本ブリーフの数値のうち、第 2 段で書き足したもの（§9 の「測る」と「第 2 段の実測」）は実行して
確かめ、テスト名を併記してある。それ以外は最初の読み取り専用調査のままであり、以下は確かめて
いない。

- テストスイート（`pytest`）は第 2 段で走らせた（上記「未解決の問い」参照）が、失敗 2 件で打ち切っており、全件を通し切ってはいない。
- `sensor-bench` の 10/10・0/7、ja-lint の 64/64 といった既存の公称値は、ソースと文書から読んだだけで再現していない。
- 循環依存が実行時に `ImportError` を起こさないことは静的にしか確認していない。
- 効果カウント（`print` / `subprocess` 等）は行マッチであり、docstring や文字列リテラル内の出現を含む。
