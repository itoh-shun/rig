# rig V3 設計ブリーフ — 能力レジストリとポート化による内部再構成

作成日: 2026-09-09
状態: 合意済み（セクション別）。第 1 段（契約テストの補強）と第 2 段（能力レジストリの宣言）は
実装済み。第 3 段（6 ポート化）は進行中で、6 本の柱のうち govern・eval・`packs`・`orchestrate`
の 4 本が済んだ。第 2 柱で `packs` をまたぐ 12 モジュールの循環が消えて `packs` は単独で移せる柱に
なり、第 3 柱としてその移行も済んでいる。あわせて、柱として数えないと決めた入次数 0 のシェル
`validation` も同じ層の規則の下に入った——`tests/test_layering_contract.py` の `MIGRATED` は
いま `("govern", "eval", "packs", "validation", "orchestrate")` の 5 つである（柱 4 本＋シェル
1 本）。層の規則がディスク上で柱として数えるのは `registry/` を含む 7 ディレクトリで、そのうち
5 つが移行済みである。残る柱は `workbench` と `rig_workbench/` 直下の 2 本で、第 4〜5 段は未着手。
あわせて §10 のとおり、rig V3 は publisher 署名機構を全廃する。2 パスとも着地した。
rig は 3.0.0 を出した。リリースの範囲は「利用者から見える話が完結した時点で出す」と決めてあり、
`workbench` の移行はラチェットの下で 3.x に持ち越す。
経緯: `/rig:brainstorm` による壁打ち。当初の判断の根拠は、すべて本リポジトリに対する読み取り専用の
実測。第 2 段と第 3 段で書き足した数値は実行して確かめている（末尾「測定の限界」を参照）。

## 固めた狙い

rig の内部を、能力の宣言が1箇所に集まり、判定が副作用を持たず、推論と計算の境界が型で表現される構造に作り替える。観測可能な契約と PACKS 互換は凍結したまま行う。

## 前提・制約（実測値）

| 項目 | 実測 |
|---|---|
| Python | 173 ファイル / 63,012 行 |
| パッケージ内 import | 585 本 |
| 循環依存（強連結成分） | 実行時 5 件。ほかに `if TYPE_CHECKING:` でのみ成立するものが 1 件 |
| 最大モジュール | `orchestrate/providers.py` 3,644 行 / 責務 12 種 |
| ハブ | `workbench/state.py` を 37 モジュールが import |
| god モジュール | `workbench/cli.py` が 40 本の import 文で 39 モジュールに届く |
| 層 | `workbench/` と `orchestrate/` が双方向（10 対 3） |
| `print()` | 1,073 箇所（うち `workbench/` に 513） |
| `subprocess` | 98 箇所（うち `shell=True` 3 件。`orchestrate/providers.py` に 2 件、`orchestrate/commands.py` に 1 件） |
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

上表は着手時の姿である。柱を 4 本（＋シェル `validation`）移し §10 を通したいまの値は、同じ
walk（`tests/test_architecture_inventory.py` の `inventory()`）を今日の木に掛けて
`print` 710 / `subprocess` 49 / `open(w,a)` 14 / `write_text` 52 / `os.environ` 37 /
壁時計 25（`ports/` 自身を除く）。`orchestrate` の効果地点は層の規則に入れる前のパスで既に
落ちていたので、4 本目が済んでも上の 6 つは 1 つも動かない——動いたのは import であって
効果地点ではない。循環も表のとおり 6 件から 5 件で、今日の木で数え直した
（`BASELINE_RUNTIME_CYCLES` と同じ 5 件、`if TYPE_CHECKING:` だけのものが 1 件）。

凍結対象（観測可能な契約）:

- PACKS のディスク契約（`pack.yaml` 正準 JSON、キー集合完全一致、`ASSET_DIRS` 固定、5 層優先順位、資産ハッシュ検証）。§10 の決定がここに当たり、`pack.lock.json` 側は凍結したまま残す
- `.rig/` レイアウト 30 以上のパス
- バージョン付き JSON スキーマ ID（当初の見積もりは 40 種。第 1 段で `tests/test_schema_registry.py` が実際に凍結したのは 38 種）
- 終了コード（`OK=0` / `REJECTED=1` / `ERROR=2` と各コマンド固有コード）
- コンソールエントリポイント 5 本

talk は hook（`hooks/inject-talk-mode.sh`）で常時前段にあり、人が直接打つ入口は実質そこ 1 つ。120 の `rig-wb` サブコマンドと 30 の `/rig:*` は、主に talk が引く routing 表として機能している。

## 決めたこと

### 1. 不変条件

観測可能な契約のみ凍結し、Python の内部 API は自由とする。

根拠: 契約側の安全網は CLI 経由テスト 85 ファイル・2,405 関数分が既にあり、スキーマ ID 40 種と `.rig` レイアウトも押さえられている。ここを厚くしてから内部を切れば、内部結合した 1,536 関数は安全網ではなく荷物として扱える。

退けた案: import パスまで凍結する案は、実行時の循環と双方向依存がそのまま残り、リファクタリングではなく整頓に終わる。非推奨期間つきの段階移行案は、凍結対象を増やす理由が実測から出てこなかった。

### 2. 「統合」の意味

名前を減らすのではなく、能力レジストリ化する。全機能を 1 箇所に宣言し、CLI・talk・MCP・GitHub Action をその投影にする。実装がそこまで届いているのは、いまのところ `govern` の 10 件だけである（下記「表から生成されているのはどこか」）。

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
| 実行時の循環を関数内 import で回避（着手時 6 件、いま 5 件）、`workbench` と `orchestrate` が双方向 | 依存性逆転 |

依存性逆転が根。関数内 import は循環を消したのではなく隠している。

共通ポート 6 本（ポートを形づくった散らばりから逆算。数は着手時の値で、今日の値は
「前提・制約」の下に書いた）:

| ポート | ポートを形づくった散らばり | 既存の部分実装 |
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
`tests/test_layering_contract.py` である。ポートの形は最初の柱で決めきらず、柱が持ち込んだ
呼び出しから広げている（`ProcessRunner` の経緯は下記「2 本目の柱が `ProcessRunner` を広げた」）。
詳細は下記「第 3 段の実測」を参照。

退けた案: 縦割りのみの案は循環は消えるが、`print` 1,073 箇所と `subprocess` 98 箇所が柱の中に残る。柱の切り方だけを借用する。依存の向きだけ直す案は実行時の循環が消えるだけで、V3 の実体がない。

### 4. 推論と計算の境界

来歴を三値の型として能力レジストリに宣言し、`gate --set` の可否を型が決める。

- `computed` — 判定層のセンサーが決める。`--set` を拒否する。
- `attested` — モデルが判断し、生成者と別のプロバイダの検証者が確認する。誰が主張し誰が確認したかを記録する。
- `unobserved` — 誰も決めていない。`passed` に畳まない。

実測（`rig_workbench/workbench/config.py` の `GATE_PRESETS`、宣言は 35 エントリ・異なり名 34。
差の 1 は `no_unrelated_refactor` で、`bugfix` と `refactor` の 2 プリセットに書かれている。
以下の表は異なり名で数えた 34 である。なお 1 つの run が実際に向き合う数はどちらでもない——
ゲートは task type が引くプリセットの和で組まれるので、`bugfix` の run は標準 10 ＋ bugfix 5 の
15 基準を見る）:

| 状態 | 件数 |
|---|---|
| センサーが判定する | 6 |
| コードから参照はあるが状態を読むだけ | 2 |
| コードに現れず宣言だけで通る | 26 |

`no_gate_tampering` は改竄を検知するのに、`tests_pass_or_explained` は誰も検査していない。

**この形は外部から持ち込むものではない。** 同じ思想が既に本リポジトリ内に 3 回、別々に実装されている。

1. `rig_workbench/workbench/prompt_regression.py` — `--set` を拒否し、機械の eval にしか判定させない。
2. `rig_workbench/assurance/assurance.py` — 記録のない軸を `{"observed": false, "reason": ...}` として運び、成功に畳まない。
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
2. **能力レジストリを 1 本立てる。済。** 宣言だけ置き、実行は既存に委譲した。`rig_workbench/registry/` に 137 件を宣言し（当初 139。§10 で `pack sign` と `pack keygen` が落ちた）、CLI・MCP 2 種・`action.yml`・スラッシュコマンド 30 枚との照合をテストにした。**宣言が実際にパーサを生成しているのは `govern` の 10 件だけで、残りの表面はいまも手書きである**（下記「表から生成されているのはどこか」）。ブリック解決の宣言も `registry/bricks.py` に集めた。実行経路は 1 行も変えていない。ただし散文と実装のずれは**測って記録しただけで、解消していない**。`SKILL.md` と `resolve.md` の側は手つかずである。宣言を集めれば同時に解消できるという当初の見込みは外れた。§9 の「最初の 1 回に手順を要求しない」も、下げるべき値がもともと 0 だったと分かり、テストで固定した。
3. **6 ポートを入れ、判定層を柱ごとに切り出す。進行中。6 本の柱のうち 4 本が済んだ。** 1 柱ずつ、移すたびに緑を確認する。実行時の循環はこの段で構造的に消え、関数内 import による回避をやめられる。第 2 段が記録した表面のずれ（下記「第 2 段の実測」）を閉じるのも、この段の判断になる。済んだのは govern・eval・`packs`・`orchestrate` で、下記「第 3 段の実測」に何を測って選び、何に気づいたかを書いた。残る 2 本の見積もりは次のとおり。数値はすべて `tests/test_architecture_inventory.py` の走査と `pyproject.toml` の台帳の行から、本節を更新した時点で取り直してある。

   | 残りの柱 | モジュール | 効果地点の合計 | 台帳の行 |
   |---|---|---|---|
   | `workbench` | 58 | 562 | 513 print・31 banned |
   | `rig_workbench/` 直下（袋） | 32 | 285 | 27 行に分かれて 195 print・69 banned |

   `validation` はこの表に無い。入次数 0 のシェルであり、柱として扱わない（理由は下記）。
   ただし `MIGRATED` には入っており、柱 4 本と同じ層の規則を通っている——「柱として数えない」は
   「規則の外に置く」ではない。
   済んだ 4 本は govern（11 モジュール、効果地点 87 → 6）、eval（15 モジュール、48 → 1）、
   `packs`（23 モジュール、70 → 8。§10 で署名側のモジュールごと減った分を含む）、
   `orchestrate`（27 モジュール、272 → 21）で、`validation` は 18 モジュール・効果地点 4。
   **残る 2 本の合計 847 は、済んだ 4 本が移行前に持っていた合計 477 より大きい。**
   `orchestrate` の 21 が 0 でないのは移し残しではない。内訳は `config.py` の `os.environ` 3 と
   `time.time_ns()` 2（どちらも下記のとおり恒久的に認めた例外）、`open(w,a)` 2、`write_text` 10、
   `subprocess` 4 である。最後の 3 種は govern・eval・`packs` が記録したのと同じ「数え方の床」と
   「ポートが持たない操作」である。`print` は 0 になった。
4. **来歴型を入れ、34 基準（宣言は 35 エントリ）を分類する。未着手。** センサーを作るか、`attested` に落とすか、`unobserved` と認めるかを 1 件ずつ決める。
5. **旧経路を落とす。未着手。** 二重定義の期間を閉じる。

2 と 3 の間で一度リリースを切れる。レジストリだけ入った状態は互換を壊さないため、V3 を一度に出さずに済む。**実際にそこで切った。rig は 3.0.0 である**（`pyproject.toml`・`rig_workbench.__version__`・`packs.model.ENGINE_VERSION`・`plugin.json` の 4 つが 3.0.0、`eval.cases.EXECUTOR_VERSION` だけは 2.13.0 に据え置き——あれは「この証拠を出した実行器は、いま判定している実行器か」を答える値で、リリース番号に縛ると無関係な版上げのたびに既存の計測が無効になる）。リリースの範囲は「利用者から見える話——`/rig:go` から始まる入口と §10——が完結した時点で出す」であり、第 3 段の残り（`workbench` と直下の袋）はラチェットの下で 3.x に持ち越す。柱の移行は凍結した契約を 1 つも壊していない。終了コードもスキーマ ID も同じである。

   **表面の差分は実測してある。4 本目の柱のあとに測り直した。** 本ブランチの分岐点
   （`git merge-base HEAD origin/master` = `e341366`）と今日の木の両方で、同じ 140 本の
   `--help` を 1 ページずつ出力して突き合わせた（`COLUMNS=100` 固定、cwd と版番号は正規化）。
   動いたのは次の 3 つだけで、残りは 1 バイトも違わない。

   | 動いた表面 | 内訳 | 由来 |
   |---|---|---|
   | ページが 2 枚消えた | `pack sign`・`pack keygen`（消えたので今日の木では exit 2） | §10 |
   | 既存ページから flag が 1 本消えた | `pack install` と `pack update` の `--allow-unverified`、および `pack`・top-level・`pack source *` の一覧行（計 7 ページ） | §10 |
   | 文面が 7 行変わった | `govern` の 7 ページ（`approve` `audit` `can` `migrate` `policy` `rollup` `waiver`）で、サブコマンド選択肢と metavar に 1 行ずつ説明が付いた | 第 3 段（govern 柱） |

   **`orchestrate` の柱は `--help` を 1 ページも動かしていない。** この柱の `--help` は
   `orchestrate/cli.py` の module docstring そのもので、`main()` と `_usage_for` がそれを
   印字する。第 1 パスはその印字を `print` から `Presenter` へ替えた。それでも 140 本の出力は
   分岐点と 1 バイトも違わない。`eval`・`packs`・`validation` も同じく 0 ページで
   ある。なお `usage` と `validate` の 2 本は比較から外した。`--help` を解さずコマンドを実行して
   しまうためで、これは移行ではなくこの 2 本の argparse の形の話である。`validate` はその後
   `581e869` で直した。`usage` は開いたままである。

   つまり「変わったのは `--help` の文面 7 行だけ」は柱の移行について言うかぎり実測どおりで、
   §10 の削除分がその外に 2 種類ある。**追加された動詞は無い。** `ja-lint` と
   `wb scan-ja-prose` は 3.0.0 の新機能だが分岐点に既にあり、本ブランチの追加ではない
   （`Release 2.13.0` の後、分岐点の commit で入っている）。サブコマンドの集合が動いたのは
   第 3 段のせいではなく §10 の決定によるもので、`pack sign` と `pack keygen` の 2 本が抜けた分だけ
   `tests/test_cli_surface_contract.py` の凍結集合を下げてある。

退けた案: ストラングラー単独は二重定義の期間が長い。契約テスト先行単独は「一気に切る」区間が長く、その間は赤のままになる。両者を直列に組む。

### 8. Faceted Prompting は置き換えず、測る

借り物の分類法（persona / knowledge / instruction / output-contract / policy の 5 分割と、その配置順）を新しい手法に差し替える案を検討し、**採らない**と決めた。代わりに、その効果を `drill` で測る調査を独立に立てる。

根拠:

- **置き換えは目的に届かない。** この作業の狙いは「借りたものを、実測と検証をした自前の構成にする」ことである。決まっていない新手法への差し替えは、測っていない主張を別の測っていない主張に置き換えるだけで、性質が変わらない。
- **借りていること自体は弱点ではない。** README は出典を明記して採用している。問題は出典ではなく、採用後に一度も測っていないこと。「recency が効くから policy を末尾に置く」という主張を裏づける計測は、本リポジトリに存在しない。
- **実測は分類法を問題だと言っていない。** ペルソナが実際に使う frontmatter キーは 3 種（`name` / `description` / `inject`、`inject` は 67 件中 25 件）、レシピが実際に使うトップレベルキーは必須 5 種のみで、文書が挙げる任意キーは出荷・パックのレシピに 1 件も現れない。分類は軽い。壊れているのはセンサーのない 26 基準、実行時の循環、散らばった `print` 1,073 箇所であって、facet の分け方ではない。
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
| `README.md` | 1,385 行 |
| `skills/engine/SKILL.md` | 741 行 / 103,869 バイト |
| スラッシュコマンド | 30 |
| サブコマンド | 120（`tests/test_cli_surface_contract.py` が凍結する集合。トップレベル 24 ＋ 6 グループ 96） |
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
| 語彙が多い（30 + 120） | 直る。名前を覚える必要がなくなる |
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
「使いやすくした」と書くよりこちらのほうが有用で、かつ正確である。

**案内の側は 3.0.0 で直した。** README.md / README.ja.md / `skills/engine/SKILL.md` の 3 つとも、
最初に読者へ言うことが `/rig:go "<やりたいこと>"` になり、プラグイン以外の導入——`rig-wb` の
install、`/rig:setup`、`/rig:init` の manifest——は「後から、必要になったときに」の位置へ移した。
`/rig:setup` は消していない。並びを変えただけである。README は §2 が「まず、やりたいことを言う」、
§3 が「インストール」で、以前はこれが逆だった。あわせて、上表が測っている内容
（素の `git init` で exit 0・人に何も訊かない・run が残る、ただし測っているのは run を作る
コマンドであって後続の全 step ではない）を散文側にも書き、README が測定より広い約束を
しないようにしてある。

真の前提は 2 つだけである。git リポジトリであることと、`python3` があること。

これは §4（来歴を型にする）と同じ規律である。主張ではなく測定に置く。

### 10. publisher 署名機構を全廃する

rig V3 は publisher 署名の仕組みを丸ごと落とす。署名・検証・トラストルート・鍵生成・失効の
すべてである。構造の話ではなく、製品の形が変わる決定なのでここに置く。

根拠は §9 と同じところから来ている。これまでの版の弱点は導入の分かりにくさと使い始めるまでの
ハードルであり、§9 はそれを「最初の 1 回に手順を要求しない」として狙いに入れた。パックを
配る側に鍵の生成を求め、受け取る側に署名の有無を説明する仕組みは、その狙いと真正面から
ぶつかる。新規の利用者が最初に出会うものが鍵についての質問である、という状態を無くす。

**第 1 パスは着地している。**

- `pack sign` と `pack keygen` を削除した。レジストリと argparse を同じコミットで触っている
  のは、上記「表から生成されているのはどこか」のとおり `packs/cli.py` が生成物ではないためで、
  分けると `tests/test_capability_registry_vs_cli.py` が間で赤くなる。
- この 2 本だけが名指していた前提 5 種（signing-key・key-id-registered・key-path-free・
  trust-roots-path・key-id-free）も、木の他のどこからも名指されないので一緒に消えた。
- install / update が `verified-publisher` 以外を拒む規則と、その逃げ道 `--allow-unverified`
  を削除した。
- 宣言された能力は 139 から 137 になり、`pack` は 23 から 21 になった。凍結した CLI 表面も
  ちょうど 2 本だけ縮めてある。

**第 2 パスも着地した。** `pack.lock.json` の再検証が落ち、検証半分（`packs/signature.py`・
`packs/publisher.py`・`trust-roots.json`）と `cryptography` 依存が木から消えた。
`rig_workbench/packs/` は 22 モジュールから 20 になり、効果地点は 78 から 70 に下がった。

#### 凍結した不変条件との衝突と、その解き方

この決定は本ブリーフが凍結したもの —— PACKS のディスク契約 —— に当たる。正直に書くと、
**当たったうえで契約の側を優先した**。

`pack.lock.json` の `verification_status` は、3 つの値（`verified-publisher` /
`verified-local` / `unverified`）も、`publisher_key_id` と `signed_digest` の 2 列も、
そのまま残す。理由は互換ではなく fail-closed である。値を 1 つ落とせば、その値を持つ既存の
lock は `pack lock drift: invalid metadata` で拒まれる。列を落とせば、**すべての** lock が
`pack lock drift: invalid entry` で拒まれる。そして解決経路は fail-closed なので、拒まれるのは
`pack` サブコマンドだけではなく、ペルソナ・レシピ・wiki を引くあらゆる run である。
これは想定ではなく、実際に値と列を落として確かめてある。

したがって `verified-publisher` は、**何も書かず何も検査しない値**として残る。ディスク上の
語彙は変わらず、その語彙が指していた機構だけが無くなる。

#### 受け入れた安全上の代償

大げさにも控えめにもせず書く。**パックのバイト列を著者に結びつけるものは、もう何も無い。**
そしてその結びつきは、*最初の取得時*に効く唯一の保護だった。lock のハッシュ連鎖が証明できる
のは「install 後に何も変わっていない」ことだけで、install したものが誰の作かは言わない。
失効にも、もう機構が無い。鍵が漏れても取り消す先が無い。

残るのは 3 つである。

- `packs/trust.py` の TOFU 同意。project 層の資産が実行される前に人間の「はい」を 1 回求め、
  以後の変更を内容ハッシュで検知する。この仕組みは `signature` も `publisher` も import して
  おらず、今回の削除に触れていない。
- lock のハッシュ連鎖（`manifest_sha256`・`asset_hashes`・`eval_case_hashes`）。install 後の
  変更を検知する。
- `validate_pack` の宣言ずれ検知。マニフェストが言っていることと中身が食い違えば拒む。

3 つとも「install した後に変わったか」を見るものであり、「install しようとしているものが
誰の作か」は 1 つも見ない。導入コストを下げる代金としてこれを受け入れる、というのがこの
決定の中身である。

退けた案: 署名を残したまま既定で任意にする案は、最初の 1 回に鍵の話が出てくる状態を残し、
説明する語彙も残す。狙いに届かない。`verification_status` を lock ごと落とす案は、上記の
とおり既存利用者の解決経路をすべて止める。

### 11. 3.x — rig を rig のゲートに通し、その上で削り、足す

3.0.0 を出したあとの向きをここに決める。読み取り専用の実測 5 本から出した。数値は本節を書いた
時点で取り直してあり、取り直せなかったものは末尾「本節が確かめていないもの」に分けて置いた。

#### 発端 — 3.0.0 は rig 自身のゲートを 1 度も通っていない

台帳を読んだ。台帳は走行のたびに伸びるので、`ts` が `2026-09-12T07:26:48Z` 以前の記録だけを
数える。その切り口で `.rig/runs.jsonl` は 4,119 件、うち 4,114 件の backend が `orchestrate`。
recipe の上位は adaptive-bugfix 2,167 / writing 767 / japanese-writing 644（次いで dag 248・
bugfix 144）。窓は `2026-09-09T15:52:13Z` から `2026-09-12T07:26:48Z`。`invoker` は 3,985 件が
`direct` で、`rig-wb/2.13.0` が 111、`rig-wb/3.0.0` が 18。pack を引いた記録は 0 件。**テスト用の
走行がそのまま残った台帳であって、人が `/rig:go` を打った記録ではない。** 残る 5 件は backend も
`invoker` も `workbench`。下の「rig を rig に通した 18 本」の表の、最初の 5 行がその 5 件に
あたる。

V3 の作業そのものがどう回ったかは、この台帳の外にある。調整役は各レーンを生の Agent ツールで
直接ばらまいた。`go` も worktree も受け入れゲートも、1 度も走っていない。**自分のゲートを
通っていない製品が「ゲートで判定する」と言っている状態で 3.0.0 を出した。**

**決定: 3.x の残りは全部 `/rig:go` とゲートを通す。**

この決定は既に効き始めている。`.rig/runs/` の最初の 1 本は
`rig-20260912-052246-sessionstart-hook-hooks-inject-t` である。task.json の `recipe` は
`refactor`、`recipe_reason` は "task type `refactor` maps to the trusted core `refactor` workflow"。
下の決定 1（hook の訂正）はこの run で走り、`71280de` として着地した。
「`.rig/runs/` が無い」は測った時点では正しく、いまは正しくない。**この 1 本が、rig が rig を
通した最初の run である。**

退けた案: 3.x のあいだは今までどおり直接ばらまき、5 段が終わってから自分に掛ける案。ゲートに
歯が無いことが今回の調査で分かった以上（下記「足すもの」の 2）、通す run が無いままゲートを
直しても、直ったかどうかを見る手段が無い。順序が逆である。

#### 決めた 3 つ

**1. SessionStart hook は残し、訂正する。**

`hooks/inject-talk-mode.sh` は 32 行 1,913 バイトで、毎セッション stdout に 951 バイト
（`additionalContext` に載る指示文そのものは 851 バイト）を注入する。その文が名指すのは
`rig:rig` が 1 回、`rig:talk` が 2 回、そして `SKILL.md` を読めという指示が 1 回。**`/rig:go` は
0 回である。** README が最初に覚えろと言う名前が、常時前段にいる入口の文に 1 度も出てこない。
さらに `skills/engine/SKILL.md` は 742 行 104,492 バイトで、hook はそれを読み終えるまで
読み手に何も言わせない。入口の名前が最初の 1 分で 4 つ（`rig:rig`・`rig:talk`・`/rig:go`・
`SKILL.md`）現れる。

訂正は 2 点。名指すのを `/rig:go` にすること、入口に要る分だけを先に読ませること。
**talk 常時起動そのものは残す。** これは持ち主の実際の使い方であり、§6 のとおり talk は判定を
持たないので determinism-by-gate とも衝突しない。直す対象は入口の名前と読ませる量である。

読ませる量も測った。数え方は、見出しからその次の見出しの直前までを UTF-8 のバイトで数える。
分母は SKILL.md 全体の 104,492 バイト。

| 前倒しする | 範囲 | バイト |
|---|---|---|
| frontmatter | 先頭の `---` 囲み | 601 |
| `NON-INTERACTIVE-STOP` | タグから `## 1. Overview` の直前まで | 1,061 |
| §1 の入口 | `## 1. Overview` から `### 仕組み` の直前まで | 1,685 |
| §6 の run-continuity | `### run-continuity` から `### **red flags` の直前まで | 8,696 |
| **計** | | **12,043（全体の 11.5%）** |

| 後回しにできる | 範囲 | バイト |
|---|---|---|
| §2 | `## 2. ブリック目録` から `## 3. PARSE` の直前まで | 25,235 |
| §3.5 | `## 3.5.` から `## 4.` の直前まで | 7,671 |
| §4 | `## 4.` から `## 5.` の直前まで | 8,482 |
| §5 | `## 5.` から `## 6.` の直前まで | 11,727 |
| **計** | | **53,115（全体の 50.8%）** |

分けられない理由は Skill ツールの側ではない。`rig_workbench/validation/catalog.py` は
SKILL.md を 3 か所で読む（`:57`・`:508`・`:633`）。§2 の切り出しは 2 か所。`:58` の
`index("## 2.")` と、`:417` の `CATALOG_SECTION`。`grep -rl "SKILL.md" tests/*.py` は
21 モジュールを返す。それを `xargs grep -l "read_text\|open("` に通すと 16 本、SKILL.md と
同じ行で読むものに絞ると 5 本。中継された「14 本」は、どちらの数え方でも出ない。**だから前倒しは
hook の 1 行では終わらず、T14 という別の run になる。**

**2. workbench の assurance 一族は、消さずに単独のパッケージへ分ける。**

`rig_workbench/workbench/` は 58 ファイル 20,833 行、`print` 513 箇所（AST 走査、§7 の残り 2 柱の
表と同じ数え方）。中身は 1 つの柱ではなく 5 本である。

| 柱 | 中身 | ファイル | 行 | `print` |
|---|---|---|---|---|
| A | タスクのライフサイクル | 16 | 4,599 | 117 |
| B | 決定的センサー | 9 | 2,856 | 44 |
| C | 報告・テレメトリ表示 | 13 | 3,615 | **204** |
| D | 外部オーケストレータ向け assurance | 16 | **7,872** | 128 |
| E | drill 用コーパス | 2 | 1,304 | 20 |

D は `wb` の 18 動詞（import・receipt・contract・intent・intent-derive・assurance-target・
assurance-derive・knowledge-candidate・change-graph・anomaly-trigger・synthesise・dev-loop・
route-team・budget-plan・provenance・expected-outcome・effectiveness・compose-options）で、
A と状態を共有しない。**README が統合を約束しているので、消さずに分ける。** 分ければ第 5 段の
`workbench` 移行は 12,728 行の仕事になる（9c2d4fc 実測）。A の柱は残る 39 本の中にある。
どちらの数も下の T11 で数え直した（`wc -l`）。上の表の 20,833 は別の木で数えたもので、
同じ `wc -l` でも e341366 で 20,771、9c2d4fc で 21,631、b42a5f6 で 22,021 と動く。
いずれも 58 ファイルである。物差しではなく木が動いている。

T11 で着地した。切り出し先は `rig_workbench/assurance/` である。着地にあたって、
この段落の数字を 2 つ訂正する。

1 つ目。D は 16 ファイル 7,872 行ではなく、19 ファイル 8,903 行である（`wc -l`）。
同じ `wc -l` で 9c2d4fc の `workbench/` は 58 ファイル 21,631 行、残るのは 39 ファイル
12,728 行になる。上の表の 20,833 / 7,872 とは合わないが、突き合わせていない。
数え方はこうである。`workbench/cli.py` が 18 動詞それぞれの
`cmd_*` を import しているモジュールから始める。そこから import されるもののうち、D の外の誰も import して
いないもので閉じる。18 動詞は 18 の別モジュールだった。名前と動詞は 1 対 1 ではない
（`assurance.py` が receipt、`assurance_wiring.py` が assurance-derive）。閉包は
`org_knowledge.py` を 1 本足す。`knowledge_candidate.py` からしか届かないからである。
`print` は 128 ではなく 161 だった。AST 走査で、`tests/test_architecture_inventory.py`
と同じ数え方である。効果地点は 6 種すべてが足し算どおりに分かれた。workbench の
513/11/5/13/8/12 が、352/7/4/7/8/9 と 161/4/1/6/0/3 になる。和は動いていない。
その後 T7/T8 が `print` を 5 件落とし、うち 1 件が `import_task` のものだった。
main を取り込んだ時点では 348/7/4/7/8/9 と 160/4/1/6/0/3 で、和は 508 である。

2 つ目、そしてこちらの方が重い。**「A と状態を共有しない」は実測に耐えない。**
19 本のうち 10 本が `workbench.state` を import している。名指しで挙げる。

| import する先 | D 側のモジュール |
|---|---|
| `workbench.state` | `assurance`・`assurance_target`・`compose_options`・`contract`・`development_loop`・`import_task`・`knowledge_candidate`・`production_outcome`・`provenance_graph`・`workflow_effectiveness` |
| `workbench` の config・capabilities・flow_view・lifecycle・progress・runtime | `import_task` |
| orchestrate・packs・govern・パッケージ直下 | `compose_options`・`import_task` |

**それでも分ける判断は変えない。** 分離の根拠は上で言い直したとおり行数と到達経路であり、
A のライフサイクルが自分の柱に戻るという結果も変わらない。だが、その依存は切らずに
`..workbench.<name>` と綴り直して残した。だから `assurance` は
`tests/test_layering_contract.py` の `MIGRATED` に入らない。

ここで 1 つ訂正する。この決定の根拠として「D の 18 動詞は利用記録が 0」と言っていた。**それは
台帳を取り違えている。** `.rig/runs.jsonl` は recipe の走行しか記録しないので、`wb` の動詞が
0 件なのは当然である。`wb` の呼び出しを記録するのは `.rig/context.jsonl` のほうで、そちらは
19,430 件あり、**うち 18,010 件（92.7%）が D の 18 動詞である**。上位は `wb anomaly-trigger`
7,951、`wb change-graph` 4,159、`wb provenance` 1,264、`wb budget-plan` 1,066、`wb synthesise`
976、`wb knowledge-candidate` 968、`wb route-team` 708。ただし窓も `invoker` の偏りも
`runs.jsonl` と同じ（`direct` が 18,094）で、どちらも git の管理外（`.gitignore:26` の `.rig/`）
である。**正しい言い方は「利用記録が 0」ではなく「この 2 つの台帳からは、人の利用と生成物を
区別できない」である。** 分離の根拠は利用の多寡ではなく、行数と到達経路のほうに置く
（状態の共有については下の T11 を見よ）。

到達経路についても 1 つ直す。「`commands/go.md` の表の 1 行からしか届かない」と言っていたが、
実際は 18 動詞それぞれに `commands/go.md` の表行が 1 本ずつ、計 18 行ある。共有された 1 行では
ない。届きにくさの主張は弱まる。

**3. rig は以後、自分を自分で回す。** 上記のとおり。

#### 削るもの — 何が壊れるかを 1 件ずつ併記する

| 削る対象 | 実測 | 壊れるもの |
|---|---|---|
| `rig-wb list` / `rig-wb review` | どちらも exit 1 を返し、オーケストレータの module docstring を印字してハンドラに届かない（再現済み） | 何も壊れない。レジストリ自身の `intent` が既にそう言っている |
| `commands/rig.md` | 14 行。`go.md` を読んで同じに振る舞えと書いてあるだけの別名で、自分でそう名乗っている。本文の最後の 1 文は「別名は非推奨ではない」と明言する | 実測で覆った。`rig:rig` の綴りを持つ tracked file は、`1e2adad` で 23 枚（`git grep -l 'rig:rig'` から本ブリーフとこの 1 枚を引いた数）。うち 5 枚は `rig_workbench/` の中。T9 の結論は削除ではなく、4.0.0 まで残す非推奨 shim である |
| `agents/*.md` | 12 ファイル 327 行。`instructions/parallel-review.md` は `agents/<name>` が無ければ `facets/personas/<name>` を合成する経路を既に持つ。同期テストは無い | 事前宣言された `tools:` フィールド（12 枚とも持つ）。**加えて下記のとおり 2 枚は複製ではない** |
| `recipes/max-bugfix` | step の並びが `bugfix` と同一（inspect→reproduce→plan→implement→test→review-diff→acceptance）。違うのは 3 つの `checks:` ブロックだけ | フラグに畳めば何も壊れない |
| 初回の摩擦 | 下記 | 下記 |
| ヘルプに出ない 15 動詞 | §「第 2 段の実測」のとおり 39 中 15。`approve` / `next` / `check` / `verdict` は人のゲートに触る | 1 本ずつ監査して決める。まとめて消さない |

**`agents/*.md` は「12 of 39 personas の複製」ではない。** 数え直すと出荷ペルソナは
`skills/engine/facets/personas/` に 31 枚（木全体では packs 込みで 70 枚）で、39 ではない。
そして 12 枚のうち **2 枚——`cognitive-economist-reviewer` と `lazy-senior-reviewer`——は
`agents/` にしか存在しない。** 木を全部探して他に無い。まとめて消すとこの 2 枚が消える。
順序が要る: 先にこの 2 枚を `facets/personas/` へ移し、それから残り 10 枚を落とす。

**初回の摩擦は、素の git リポジトリで再現した。** 空のリポジトリを作り
`python3 scripts/workbench.py new "fix the login bug" --type bugfix` を stdin を閉じて実行した。

| 観測 | 実測 |
|---|---|
| 終了コード | 0。人に何も訊かない（§9 の測定どおり） |
| `.gitignore` | **同意を求めずに追記される。** `lifecycle.py:188` の `ensure_rig_gitignored` が無条件で、`◇ Appended .rig/ to .gitignore` と印字する |
| hostcheck | `commands/go.md:74` が明示している——「**every time, not once per session**」。「一度きり」と書くと実装の無い約束になるから毎回だ、と理由まで書いてある。その `state_ignored` の MISS は 2 回目から消える。rig が自分の見つけたものを黙って直したためである |
| 最後に印字される行 | `cd <worktree> && claude`。README §1 の「a separate tool への context switch ではない」（README.md:26）と正面からぶつかる |
| `new` のバナー | 「flow: 7 steps」、役名 7 種（orchestrator / debugger / implementer ＋ reviewer 4 種）、「最終ゲートは 15 基準」 |
| README の最初の実行可能コマンド | 52 行目。そこまでの語数は **917**（942 ではない） |

`tests/test_first_run_cost.py` が押さえているのは `new` が exit 0 で人に訊かないことだけ、では
なかった。テスト関数は 4 本ある（bare repo・core tier への degrade・hostcheck の advisory
degrade・未承認 manifest の 1 行警告）。ただし上表の `.gitignore` 無断追記も、hostcheck の毎回
実行も、最後の `cd … && claude` も、README の 917 語も、**4 本のどれも見ていない。**

**実測が否定したので、削らないもの。** 出荷ペルソナ 31 枚の総当たり difflib 比較で、最大の
類似度は `japanese-lint-reviewer` と `layout-gate-reviewer` の 0.438 である。0.70 を超える対は
1 組も無い。孤児の instruction・recipe・persona も無い（brace 展開が参照を全部解決する）。
「重複しているから畳める」という筋は、この 2 つについては実測が立てさせない。

#### 足すもの — どれも既にあるものの上にだけ建てる

**1. 来歴を commit ではなく handoff に付ける。**

直近 50 commit の数値トークンを数えた結果（255 個、うち測定コマンドが近くにあるもの 190、
無いもの 65、人に帰属するもの 0）は本節では再現していない。だが再現するまでもなく、**この
プロジェクトで実際に狂った数値は commit message ではなく handoff prompt の中にあった。**
その handoff を組むのは `rig_workbench/workbench/task_package.py:20` の `compose()` で、材料は
goal / constraints / acceptance criteria / 識別子 / 書き出し先だけである。**数値を運ぶ欄が無い。**

三値の型付けは、木の中に既に 5 回、別々に実装されている。§4 が数えた 3 回にさらに 2 つある。

| 既存 | 場所 | 語彙 |
|---|---|---|
| assurance | `assurance/assurance.py` の `unobserved()` / `observed()` | `observed` の真偽と理由 |
| intent | `assurance/intent.py`（`UNVERIFIABLE = "unverifiable"` は `:63`、根拠は `:26` と `:58`） | `unverifiable` は `unsatisfied` の弱い版ではない |
| assurance-target | `assurance/assurance_target.py:16-20` | `unobservable` はそれ自体が 1 つの結末 |
| production-outcome | `assurance/production_outcome.py` | measured / reported / estimated / unmeasured / inconclusive、`declared_by`・`declared_at`・`PRECEDENCE` つき |
| runstate | `orchestrate/runstate.py:751-753` | 判定者が自分なら `self-graded` |

最小の版: `compose()` に「中継した測定値」の節を足し、1 つの数値につき
`computed: <コマンド>` / `attested: <誰>` / `unobserved` のどれかを必ず書かせる。語彙は
`production_outcome` のものを借りる（5 語も `declared_by` も既にある）。受け手は `unobserved` と
書かれたものを自分で測り直してから使う。

退けた案: commit message のゲートにする案。**このプロジェクトで起きた誤りを 1 件も捕まえない。**
狂った数値は commit を経由せず、レーンからレーンへ prompt で渡っていた。

**2. recipe の acceptance 行を判定にかける。**

`lifecycle.py:456-463` は、recipe の `acceptance:` は「そのフローの作業リスト」であって受け入れ
条件ではない、と印字する。受け入れ条件のほうは `GATE_PRESETS`（`workbench/config.py`）が持つ
34 基準（宣言は 35 エントリ。§4 の訂正を参照）で、そのうちセンサーが判定するのは 6 つだけ——
`secrets.py:52`・`hardening.py:40`・`injection.py:42`・`destructive.py:53`・`schema_diff.py:28`、
呼ぶのは `lifecycle.py:398-432` である。残りは宣言だけで通る。`--set` が拒む基準はちょうど 1 つ
（`lifecycle.py:392` の `prompt_regression_passed`）。

そして `acceptance:` キーを持つ recipe は **26 枚**（出荷 32 枚中）。**26 枚が、何も評価しない
散文の条件を宣言している。** 最小の版: 各行を 34 基準のどれか 1 つに束ねるか、束ねられなければ
`unobserved` と印を付ける。束ねられない行が何枚に何本あるかが、そのまま第 4 段の棚卸しの
入力になる。

**3. 並列 dispatch の前に、触るファイルが重なっていないかを見る。**

必要な部品は全部ある。レーンごとの worktree（`orchestrate/isolate.py:18-37`）、N 変種の並列実行
（`orchestrate/commands.py:1220` の `_run_ab_variant` が ThreadPool で worktree ごとに回す。
max-parallel の既定 3 は `orchestrate/queueing.py:519`——`:696` ではない）、merge-back
（`workbench/accept.py:270-290`、squash merge と巻き戻しの前検査つき）。

**talk 由来の fan-out はこの 3 つを全部迂回し、dispatch 前にファイルの重なりを見る箇所がどこにも
無い。** 一方で `facets/output-contracts/task-plan.md:17` の表は「触るファイル」列を既に必須に
している。**誰も読まない列を、誰も守らないまま持っている。** 本プロジェクトでは実際に、
あるレーンの `git stash` / `reset` が別のレーンの編集を流した。

最小の版: あの列を parse し、依存が `—` のタスク同士で積を取り、重なったら拒むか隔離を強制する。
数十行で足りる。

#### `providers.py` — 正直に測り直した

| 測る対象 | 実測 |
|---|---|
| 行数 | **3,747**（`HEAD~30` では 3,645）。V3 のあいだに 102 行**増えて**いる。「触っていない」は誤り |
| トップレベル関数 | 97（AST） |
| クラス | 4 |
| モジュール変数 | 41 |
| `global` 文 | **0**。41 個は定数・正規表現・ロックであって、可変のグローバル状態ではない |
| 公開名 | 29 |

**凍結された `providers ↔ runstate` の循環を閉じている後ろ向きの辺は、ちょうど 1 本である。**
`runstate.py:624` が関数の中で `from .providers import japanese_material_metadata` を遅延 import
する、その 1 本だけ（`providers.py:1600` に定義、他に参照は無い）。この関数が属する合成の
クラスタを `orchestrate/composition.py` へ出せば、循環は 5 件から 4 件に減って**消える**。

分割の代償は monkeypatch の数で決まる。数え直すと `tests/**/*.py` で `providers` を差し替える
`monkeypatch.setattr` は **153 箇所**（136 ではない）、うち `run_provider` が **62 箇所**
（59 ではない）である。**`run_provider` を含む側を分けると 3 分の 1 以上のテストが黙って
落ちる。** 決定は変わらない: 合成のクラスタを出し、レビュー JSON の正規化も任意で出し、
`run_provider` を抱えるクラスタ群は分けない。**創立時の「12 責務」という苦情への答えは、
責務は確かに 12 あるが、それは泥団子ではなく層である、になる。**

#### 3.x のタスク表

ゲートに歯を入れる 3 本（足すものの 1・2・3）を先に置く。以後のタスクは全部そのゲートを通る
ので、歯が無いうちに通しても通ったことにならないためである。バケツは **決**（決めた 3 つ）/
**削**（削るもの）/ **足**（足すもの）。

| # | 目的（1 行） | 触るファイル | 検証 | 依存 | バケツ | 並列 |
|---|---|---|---|---|---|---|
| T0 | hook の指示文を `/rig:go` 名指しに直し、入口に要る分だけ先に読ませる | `hooks/inject-talk-mode.sh`・`skills/engine/SKILL.md` | `sh hooks/inject-talk-mode.sh` に `rig:rig` が出ず `/rig:go` が出る ＋ `pytest tests/test_first_run_cost.py -q` | — | 決 | 単独（着地済み） |
| T1 | `compose()` に「中継した測定値」節を足し、3 値を必須にする（検証テストは新設） | `rig_workbench/workbench/task_package.py`・`tests/test_task_package_provenance.py` | `pytest tests/test_task_package_provenance.py -q` | — | 足 | **P1**（着地済み） |
| T2 | 並列 dispatch 前に「触るファイル」列を parse して重なりを拒む（parser と dispatch 前検査を `orchestrate/` に置き、検証テストは新設） | `rig_workbench/orchestrate/`・`facets/output-contracts/task-plan.md`・`tests/test_disjoint_dispatch.py` | `pytest tests/test_disjoint_dispatch.py -q` | — | 足 | **P1**（着地済み） |
| T3 | recipe 26 枚の `acceptance:` 行を 34 基準に束ね、束ねられない行を `unobserved` と印す（検証テストは新設） | `skills/engine/recipes/*.md`・`tests/test_recipe_acceptance_binding.py` | `pytest tests/test_recipe_acceptance_binding.py -q` | — | 足 | **P1**（着地済み） |
| T4 | `rig-wb list` / `review` をディスパッチ表から落とす | `rig_workbench/cli.py`・`tests/test_capability_registry_vs_cli.py` | `pytest tests/test_capability_registry_vs_cli.py tests/test_cli_surface_contract.py -q` | T1–T3 | 削 | **P2**（着地済み。能力 137→135） |
| T5 | `agents/` 固有の 2 枚を `facets/personas/` へ移し、残り 10 枚を落とす | `agents/*.md`・`skills/engine/facets/personas/`・`facets/instructions/parallel-review.md` | `pytest tests/test_brick_resolution_declaration.py -q` ＋ 31＋2 枚の解決を確認 | T1–T3 | 削 | **P2**（着地済み。前提が崩れ、移動も削除も 0） |
| T6 | `max-bugfix` を `bugfix` ＋ `checks:` のフラグに畳む | `skills/engine/recipes/max-bugfix.md`・`skills/engine/recipes/bugfix.md` | `pytest -q -k recipe` ＋ `wb route --type bugfix --json` が同じ recipe を返す | T3 | 削 | **P2**（見送り。スキーマに `checks:` を任意にする表現が無い） |
| T7 | `.gitignore` の無断追記に同意を挟み、`hostcheck` の 2 回目以降の見え方を直す | `rig_workbench/workbench/lifecycle.py`・`tests/test_first_run_cost.py` | `pytest tests/test_first_run_cost.py -q`（新しい 2 本を足す） | T1–T3 | 削 | 単独（実行中） |
| T8 | 最後の行の `cd … && claude` と README §1 の約束を、どちらかに寄せる | `rig_workbench/workbench/lifecycle.py`・`README.md`・`README.ja.md` | `pytest tests/test_docs_registry.py tests/test_first_run_cost.py -q` | T7 | 削 | 単独（実行中） |
| T9 | `commands/rig.md` を落とし、`SKILL.md` の description 行を直す | `commands/rig.md`・`skills/engine/SKILL.md` | `pytest tests/test_capability_registry_vs_surfaces.py -q`（30 枚の凍結は 30 のまま） | T0 | 削 | 単独（着地済み。削除ではなく非推奨 shim へ） |
| T10 | ヘルプに出ない 15 動詞を 1 本ずつ監査する（`approve`/`next`/`check`/`verdict` は人のゲートに触るので最後） | `rig_workbench/cli.py`・`tests/test_capability_registry_vs_cli.py` | `pytest tests/test_capability_registry_vs_cli.py -q` | T4 | 削 | 単独（着地済み。13 本を監査し 9 載せ 4 残し 0 削除） |
| T11 | D を `rig_workbench/workbench/` から **`rig_workbench/assurance/`** へ切り出し、`pyproject.toml` の台帳を直す（切り出し先は本ブリーフでは決めていなかったので、この run で決めた）。実測は 16 ファイル 7,872 行ではなく **19 ファイル 8,903 行・`print` 161**（T7/T8 の着地後は 160。18 動詞は 18 モジュールで、閉包が `org_knowledge` を 1 本足す） | `rig_workbench/workbench/`・`rig_workbench/assurance/`・`pyproject.toml` | `pytest tests/test_architecture_inventory.py tests/test_layering_contract.py -q` ＋ `--help` 差分 0（実測 127 本：`rig-wb --help` と、`tests/test_cli_surface_contract.py` が `--help` を解すと宣言する subcommand 126 本。§3 の 140 本とは合わないが、突き合わせていない） | T1–T3, T4 | 決 | 単独（着地済み） |
| T12 | 合成クラスタを `orchestrate/composition.py`（新設）へ出し、`providers ↔ runstate` の循環を落とす | `rig_workbench/orchestrate/providers.py`・`runstate.py`・`rig_workbench/orchestrate/composition.py` | `pytest tests/test_architecture_inventory.py -q` で循環 5→4 ＋ 全件緑 | T1–T3 | 決 | 単独（着地済み） |
| T13 | センサー付き基準の判定をセンサーに戻し、`--set` は測定と一致するときだけ受け付ける | `rig_workbench/workbench/lifecycle.py`・`rig_workbench/workbench/secrets.py`・`rig_workbench/workbench/hardening.py`・`rig_workbench/workbench/injection.py`・`rig_workbench/workbench/destructive.py`・`rig_workbench/workbench/anchors.py`・`rig_workbench/workbench/ja_prose.py`・`tests/test_gate_sensor_authority.py`・`tests/test_secret_scan.py`・`tests/test_tamper_sensor.py`・`tests/test_injection_scan.py`・`tests/test_destructive_scan.py`・`tests/test_anchor_sensor.py`・`tests/test_ja_prose_gate.py` | `pytest tests/test_gate_sensor_authority.py tests/test_first_run_cost.py -q` | — | 足 | 単独（着地済み） |
| T14 | `SKILL.md` の後回しにできる 4 節を参照ファイルへ出し、切り出す側と本文を読むテストを追随させる（T0 の後半） | `skills/engine/SKILL.md`・`rig_workbench/validation/catalog.py`・`tests/` | `pytest tests/test_skills_spec.py tests/test_docs_registry.py -q` ＋ 再測した行数とバイト数 | T0 | 決 | 単独（着地済み。742 行→465 行） |

**並列に置けるのは P1 の 3 本（T1・T2・T3）と P2 の 3 本（T4・T5・T6）である。** P1 は
`workbench/task_package.py` / `orchestrate/` / `recipes/*.md` で 1 ファイルも重ならない。P2 は
`cli.py` / `agents/`＋`personas/` / `recipes/` で重ならない。それ以外は重なる——T7 と T8 と T11 は
`lifecycle.py` を、T0 と T9 は `SKILL.md` を共有するので、直列に置く。**この表そのものが T2 の
入力である。** 「触るファイル」列を書いておいて parse しないのでは、上で数えた `task-plan.md:17`
と同じことを繰り返す。

**T13 と T14 は、表を書いたあとに足した行である。** T13 は `workbench/` の 7 モジュールと
その 7 本のテストに触り、P1 の 3 本とも T0 とも 1 ファイルも重ならない。T14 は `SKILL.md` を
T0 と共有するので T0 に続ける。**id を `T0b` と書かなかったのは、T2 の読み手が `T<n>` 以外の
id を読めないためである。** `orchestrate/plan_dispatch.py:142` の `^T\d+$` に外れた行は、
読めなかった行として表ごと refuse される。番号を振り直すか読み手を広げるかは第 4 段で決める。

#### rig を rig に通した 18 本

T0・T1・T2・T3 と本ブリーフ自身の改稿は、`/rig:go` の workbench で走った。どれも隔離 worktree
で実装し、ゲートを通してから base branch へ着地した。`.rig/runs.jsonl` に backend も `invoker` も
`workbench` の記録が入ったのが、最初の 5 本。台帳の `backend` と `invoker` を引けば、この 5 件と
残り 4,114 件を、いま初めて別々に数えられる。下表はそのあと着地した 13 本を足した 18 本である。
レビュー欄は、run の diff.md が名指しした reviewer の persona 数と round 数。名指しの無い run は
`—` を置いた。機械の記録である `review.json` は、どの run も verdict を 1 件までしか持たない。

| run | 中身 | 着地 | ゲート | レビュー |
|---|---|---|---|---|
| `rig-20260912-052246` | T0（hook の名指しを `/rig:go` に） | `71280de` | passed_with_warnings | — |
| `rig-20260912-060330` | docs（3.x の表の触るファイル列を経路だけに） | `c5a8c8e` | passed | 1 / 1 |
| `rig-20260912-053306` | T2（並列 dispatch の重なり検査） | `e85ab87` | passed_with_warnings | — |
| `rig-20260912-053307` | T3（acceptance 行の束ね） | `0ce14a3` | failed。`--force` で着地 | 5 / 5 |
| `rig-20260912-053305` | T1（`compose()` の中継測定値） | `dfdcf64` | passed | 4 / 4 |
| `rig-20260912-073229` | CI 修正（§11 の表を凍結コーパスに） | `cd55737` | passed_with_warnings | 4 / 4 |
| `rig-20260912-075020` | T5（前提が崩れ、レビュー面をテストで固定） | `e0acc72` | passed | 2 / 2 |
| `rig-20260912-072823` | docs（§11 に最初の 5 本を記録） | `96a90b1` | passed | 2 / 4 |
| `rig-20260912-081136` | T4（`list` / `review` をディスパッチ表から） | `bb173a7` | passed_with_warnings | 3 / 3 |
| `rig-20260912-072824` | T14（`SKILL.md` を 5 枚に分割） | `ea9012f` | failed。`--force` で着地 | 5 / 8 |
| `rig-20260912-083911` | T10（ヘルプに出ない 13 動詞の監査） | `3ba176d` | passed | 3 / 3 |
| `rig-20260912-053818` | T13（判定をセンサーに戻す） | `96ec92b` | passed_with_warnings | 6 / 17 |
| `rig-20260912-085812` | S1（digest ラベル規則） | `d382330` | passed_with_warnings | 2 / 3 |
| `rig-20260912-095408` | CI 修正（print ラチェットと git identity） | `1fa4fd3` | passed_with_warnings | — |
| `rig-20260912-094453` | S2（鍵らしい前置きの拒否と `key=value` の分割） | `206774e` | passed_with_warnings | 2 / 3 |
| `rig-20260912-101630` | A1（squash 失敗の分類） | `7bebdc5` | passed_with_warnings | 2 / 2 |
| `rig-20260912-075021` | T12（`composition.py` と循環 5→4） | `1e2adad` | passed | 4 / 4 |
| `rig-20260912-092922` | T9（`/rig:rig` を非推奨 shim に） | `9c2d4fc` | failed。`--force` で着地 | 3 / 3 |

T3 が束ねたのは 152 項目。26 枚 29 ブロックの `acceptance:` が持つ項目数で、行に直すと 136 行
（うち 8 行は inline list で、24 項目を載せる）。内訳は 78 項目が基準に束ね、74 項目が
`unobserved`。いま数え直しても同じ数が出る。6 本目の run `rig-20260912-053818` は T13 で、
本節を書いた時点では未着地だった。いまは `96ec92b` として着地し、台帳にも
`2026-09-12T09:32:54Z` の 1 行が入っている。

**この時点で `--force` で着地したのは T3 だけである。** `prompt_regression_passed` は `--set` を受け付けない
唯一の基準で、合否は機械 eval ゲートが決める。T3 の diff は `skills/engine/SKILL.md` に触れる。
affected case は `style-persona-qiita-tech-writer` の 1 件。その case の `provider_policy` が
要求する `min_isolation: os-enforced` を、この箱は満たさない。証拠は `rig-wb hostcheck` の
`[MISS] process_isolation` の行。`.rig/audit.jsonl` に残ったのは 1 行で、action は
`accept_force`、bypassed は `acceptance_gate_not_failed`、gate_status は `failed`。落ちた基準は
`prompt_regression_passed` の 1 件、ts は `2026-09-12T07:26:47Z`。task.json と台帳の同じ run
にも `forced: true` が載る。

同じ run は、T13 の塞ぐ穴も使っている。T3 の acceptance.json では `no_secret_leak` が
`secret_override: true` のまま passed で、検出 4 件はそこに残っている。センサーが見つけたものを
宣言が上書きした形であり、T13 が止めるのはこれである。

**変異テストをするレビューは、共有 worktree では他のレビューを騙す。** T3 では、同時に
読んでいた側が変異を本物と見て REJECT を出した。スナップショットから読み直して撤回されている。
run が残した diff.md に記録されている数は 2 本。3 本という中継された数は、run の記録からは
裏が取れない。以後レビューは worktree を直接読まず、スナップショットから読む。

**機械 eval ゲートの言葉を分けているのは、呼び方ではなく diff である。** 同じ base
`5732531` から 3 通りに呼んだ。CLI の `--head <sha>`、CLI の `--head working`、そして
in-process の `evaluate_gate`。3 つ目は `prompt_regression.py` が呼ぶ経路そのものを叩いている。
3 つとも `infra_error` の exit 2 で、差は出ない。分かれるのは head の側だった。
`71280de`（hook の 1 行）は `noop`、`e85ab87`（`task-plan.md`）は `debt`。
`dfdcf64`（`SKILL.md` を含む）は `infra_error` を返す。そして `failed` は report の状態ではない。
`prompt_regression.py:123` が exit code 非 0 のときチェックに書く語である（`debt` のときは
warning に落とす）。**「呼び方で 3 通りに出る」は再現しない。**

今日の `infra_error` の理由は `trusted attestation key is unavailable` で、accept 時に記録された
provider 隔離とは別の理由。case を走らせられない点は変わらない。

#### 2 段目 — 13 本が着地し、1 本を見送った

台帳を数え直した。`.rig/runs.jsonl` は 5,042 件。窓は `2026-09-09T15:52:13Z` から
`2026-09-12T11:24:44Z`。backend と `invoker` がどちらも `workbench` の記録は 19 件。上表を
書いたときの切り口（`ts` が `2026-09-12T07:26:48Z` 以前）を当てれば、いまも 5 件が出る。
19 件のうち 18 件は commit を着地させた。残る 1 件は T6 で、着地させずに見送った。
`.rig/runs/` には本 run を含めて 5 本のディレクトリが `status` が `running` のまま残る。
どれも台帳にはまだ載っていない。

`--force` で着地したのは 3 本になった。`.rig/audit.jsonl` は 3 行で、action はどれも
`accept_force`。bypassed はどれも `acceptance_gate_not_failed` の 1 件。落ちた基準も
どれも `prompt_regression_passed` の 1 件だけ。T3 が `2026-09-12T07:26:47Z`、T14 が
`2026-09-12T09:21:17Z`、T9 が `2026-09-12T11:12:25Z`。T14 と T9 の acceptance.json では、
どちらもこの 1 件を除く 17 基準が passed か warning。
`prompt_regression_passed` の detail は 3 本とも `machine eval gate: infra_error` である。
この箱で機械 eval ゲートが出す答えは、case の合否ではない。case を走らせられないという
事実のほうで、測定は CI の側にある。

T14 は SKILL.md を 5 枚に割った。`skills/engine/SKILL.md` は `ea9012f` の直前で 742 行
104,492 バイト、`ea9012f` で 465 行 53,194 バイト（`wc -lc`）。出した先は 4 枚。BRICKS.md
107 行 26,316 バイト、RECIPE-SCHEMA.md 52 行 8,349 バイト。RESOLVE.md 86 行 9,159 バイト、
COMPOSE.md 109 行 12,389 バイト。`1e2adad` の 5 枚は `ea9012f` と 1 バイトも違わない。
毎セッション読ませる本文は 51,298 バイト減り、減り幅は元の 49.1%。上の「後回しにできる」表の
見積もり 53,115 バイトとは 1,817 バイト違う。差は stub を 4 つ残したぶん。出した先 4 枚の
合計は 56,213 バイトで、見積もりより 3,098 バイト多い。うち 2,583 バイトは 4 枚の前書きで、
見出し・出自コメント・抑止マーカーの 10 行ずつ。各ファイルの先頭から `## <節番号>` の行までを
バイトで数えた。残る 515 バイトの内訳は測っていない。

**T5 は前提のほうが落ちた。** `agents/` は `e0acc72` の前後どちらでも 12 ファイル 327 行で、
1 枚も減っていない。`agents/*.md` は 12 枚とも `tools:` を frontmatter に持つ。
`facets/personas/*.md` 31 枚は 1 枚も持たない（`grep -l '^tools:'`）。移す先とされた 2 枚には
`facets/personas/cognitive-economist.md` と `facets/personas/lazy-senior.md` が既にある。
移動は移動ではなく複製になる。run が足したのは `tests/test_reviewer_surface.py` 245 行
49 テストだけで、commit は 2 ファイル +251 行、削除 0 行。

動詞の表は T4 と T10 の 2 段で片づいた。T4 は `list` と `review` をディスパッチ表から
落とした。宣言された能力は 137 から 135 に、トップレベルは 39 から 37 になった（`registry` の
`CAPABILITIES` と `CLI_CAPABILITIES` の長さ）。T10 が監査したのは、その 37 のうち
`rig-wb --help` が名を出さない 13 本。9 本を `--help` に載せ、4 本を理由つきで残し、0 本を
消した。いま数えると 37 中 33 が `--help` に出る。残るのは `graph`・`install-shim`・`models`・
`probe` の 4 本。`tests/test_capability_registry_vs_cli.py` の
`TOP_LEVEL_VERBS_HIDDEN_WITH_REASON` はこの 4 本を鍵に持ち、値はそれぞれ理由の文である。

T13 は `--set` からセンサーの席を取り上げた。`lifecycle.py` の `REFUSABLE_CRITERIA` は
5 基準の frozenset。中身は secrets・tamper・injection・destructive・anchors。fail-grade の
センサーと食い違う宣言は、その基準に限って exit 2 で拒まれる。warning-grade の schema-diff と、
機械が持つ ja-lint 系は集合の外にある。各 check は `by` と `note` を持つようになった。
manuals の側は 6 ファイル。README ×2・PACKS.md・workbench-ops.md・acceptance-check.md・
japanese-textlint-rules.md の 6 枚。`96ec92b` がそこに +76/−16 行を書き替えた。
`tests/test_docs_registry.py` はこの 6 枚とコードの数を突き合わせ、いま 14 テストが緑である。

着地したあと、T13 は CI を 2 か所で赤くした。`print` 効果地点の 513 から 516 への増加と、
identity の無い runner での squash merge 失敗を `1fa4fd3` が直した。
`tests/test_architecture_inventory.py` のラチェットは、いまも 513 である。

secret scan は 3 本に分かれ、2 本が着地した。`d382330` は値の直前のキー名だけをラベルと
見る規則を入れた。`206774e` は鍵らしい前置きを digest キーから外し、引用符の無い `key=value`
を左辺と右辺に割った。`tests/test_secret_scan.py` は `bb173a7` で 43、`d382330` で 80、
`206774e` で 113 テスト（`pytest --collect-only`）。3 本目は除外規則の 2 件で、
`rig-20260912-102826` が実行中。

A1 は、`git merge --squash` の非ゼロ終了をすべて衝突と呼んでいた accept の文面を 3 つに
分けた。衝突は `--diff-filter=U` のパスを 20 件まで、identity 未設定は 3 つの綴りで見分け、
残りは git の出力をそのまま見せる。

T12 は循環を 1 つ落とした。`tests/test_architecture_inventory.py` の走査で、実行時循環は
`1e2adad^` が 5 件、`1e2adad` が 4 件。消えたのは
`orchestrate.providers ↔ orchestrate.runstate` の 1 組。凍結集合もその 1 組だけ縮んだ。
`providers.py` は 3,747 行から 3,102 行になった。トップレベル関数 97 から 82、クラス 4 から 3、
モジュール変数 41 から 34（AST、`global` 文は前後とも 0）。新しい `composition.py` は
722 行・関数 15・クラス 1・モジュール変数 7。

**T6 は畳めないと測って見送った。** `max-bugfix` と `bugfix` は step の並びが同じ。差は 2 つ。
1 つは `max-bugfix` 側の `checks:` 3 ブロック。もう 1 つは `bugfix` 側の acceptance step に
だけある `personas: [implementer]` の 1 行。`RECIPE-SCHEMA.md` の `checks` 行は、プロジェクト依存の
ため shipped recipe では未宣言と書く。`tests/test_perf_budgets.py` は `bugfix` を、
checks を持たない fixture として使う。畳めば既定の bugfix 経路が、Python 前提の検査を
無条件で走らせる。`checks_when:` のような鍵を足せば畳めるが、それはスキーマの機能追加である。
この行の仕事ではない。run のメモは
`.rig/runs/rig-20260912-092921-recipes-max-bugfix-md-recipes-bu/handoff.json` に残した。

T9 は削除ではなく非推奨の shim として `9c2d4fc` に着地した。`commands/rig.md` は残り、
description は `[deprecated: use /rig:go; removed in 4.0.0]` を先頭に持つ。削除は 4.0.0 に置く。
削除をやめたのは、この別名が一度も非推奨と告知されていなかったため。`/rig:go` へ改名した
1.11.0 の CHANGELOG は、互換エイリアスとして残すと書いている。`1e2adad` の
`commands/rig.md` 自身も、最後の 1 文で「別名は非推奨ではない」と明言していた。
範囲も違った。`git grep -l 'rig:rig'` から本ブリーフと `commands/rig.md` を引くと、
`1e2adad` で 23 ファイル。うち 5 ファイルは
`rig_workbench/` の中にあった。`orchestrate/queueing.py` は headless agent へ
`Run: /rig:rig "<task>"` と印字していた。`workbench/reporting.py` は人向けの次アクション行に
3 か所出していた。同じ引き算を `9c2d4fc` でやると 7 ファイルになり、`rig_workbench/` は 0。
残る 7 枚は README ×2・CHANGELOG ×2・過去の計画 1・テスト 2 で、どれも記録か凍結値である。
スラッシュコマンドは `commands/*.md` が両方の commit で 30 枚。T9 の検証欄が言っていた
29 への引き下げは起きない。

ja_textlint の抑止マーカーは、コードスパンの中でも効いていた。分割前の `SKILL.md` で
`textlint-disable` が現れるのは 97 行目の 1 か所だけ。そこは §2 の表のセルの中で、backtick で
囲われた文字列である。マーカーは 742 行目まで効き、646 行（文書の 87.1%）を黙らせていた。
抑止を外して `rig-wb ja-lint` を 5 枚に当てると、error は 101 件。内訳は SKILL.md 51・
BRICKS.md 19・RESOLVE.md 18・COMPOSE.md 12・RECIPE-SCHEMA.md 1 で、warning は 25 件。
直す run（`rig-20260912-092924`）は実行中で、着地していない。その run の 1 回目の修正は、
規範の強さか適用範囲が変わった hunk を 5 つ作った。`.rig/runs/` のその run の
`reviews/ai-smell-reviewer.md` が、head `9fbc2c9` への round 1 の REJECT として記録している。
節 (3) に S1〜S5 として並ぶ。S1 は `SKILL.md` の runner 代替を MUST から MAY に落とし、
S2 は `BRICKS.md` の主語欠落で、適用範囲のほうが動いた。残る 3 件も同じ記録の中にある。
トークンの多重集合の一致は、この変化を通した。

#### 各 run のレビューが残した先送り

レビューは、直さずに残すと決めたものを名指しで残した。

| run | 先送り | 実測 | いま（`1e2adad` で再測） |
|---|---|---|---|
| T1 | `offset_timestamp` が末尾 `Z` の timestamp を拒む | `production_outcome.py:252` は `datetime.fromisoformat` のまま。python3.10.20 は `2026-09-12T07:00:00Z` を `ValueError`、3.11.15 は通す。`pyproject.toml:10` は `>=3.10` | 開いたまま。`offset_timestamp` は `fromisoformat` のまま。3.10.20 は同じ `ValueError`、3.11.15 / 3.12.3 / 3.13.12 は通す |
| T1 | backtick 拒否が `reported` に過剰 | `task_package.py:161` は `source` 全体を拒むが、inline code span に入るのは `measured` の source だけで、`reported` の source は素の文に出る | 開いたまま。拒否の行は動いていない |
| T2 | runtime の呼び出し元が無い | `plan_dispatch` を import するのは `tests/` だけで、production からは 0 件 | 開いたまま。`git grep -ln plan_dispatch -- '*.py'` は `tests/test_disjoint_dispatch.py` の 1 枚だけを返す |
| T2 | glob 対 glob を比較しない | `plan_dispatch.py:72` が限界として明記している | 限界として残すと決めたまま。記述も残っている |
| T3 | 経路の無いレシピは全プリセット語彙で判定される | `acceptance:` を持つ 26 枚のうち 17 枚が `ROUTED_GATE_CRITERIA` に無い（中継された 15 枚は再現しない）。語彙は 34 基準 | 開いたまま。26 / 17 / 34 を数え直して一致 |
| T3 | `extends` の継承 step は親の経路で判定される | `recipes.py:716` が `ROUTED_GATE_CRITERIA.get(path.stem)` を渡す。該当は design-first → release-flow の 1 組だけ | 開いたまま。`extends:` を持つ出荷レシピは design-first の 1 枚だけ |
| T3 | pack のレシピは `check_recipe` を通らない | `validation/cli.py:97` が見るのは `RECIPES.glob("*.md")` だけ。`packs/` 配下は 12 枚、うち `acceptance:` 持ちは 3 枚 | 開いたまま。pack の recipe は 12 枚、`acceptance:` 持ちは 3 枚 |
| T13 | `rig_workbench/workbench/accept.py` の `gate_ok` が `skipped` を充足として数える | `status in ("passed", "passed_with_warnings", "skipped")` | 閉じた。`dd54baf` が `gate_ok` を 2 値にした。全件 `skipped` の gate は `accept --force` でしか通らず、1 件でも `skipped` があれば `gate_status` は `passed` を返さない |
| T13 | 退役した `--set` 迂回を案内する散文が残る | 6 ファイル 12 行。`README.md:234,236`・`README.ja.md:233,235`・`PACKS.md:31`・`workbench-ops.md:402,409,423,467,481`・`acceptance-check.md:68`・`japanese-textlint-rules.md:191` | 閉じた。`96ec92b` が 6 枚とも +76/−16 行で書き替え、`tests/test_docs_registry.py` が数を突き合わせる |

**決定: 9 件を 2 / 5 / 2 に分ける。** `accept.py` の `gate_ok` が `skipped` を充足に数える穴は、
T13 の着地直後に 1 run で塞ぐ。退役した `--set` 迂回を案内する 12 行も、続けて 1 run で塞ぐ。
3.x の後段へ持ち越すのは 5 件。`offset_timestamp` の末尾 `Z`・backtick の過剰拒否・経路の無い
17 枚・`extends` の継承 step・pack レシピの未検査である。runtime の呼び出し元が無い
`plan_dispatch` と glob 対 glob の 2 件は、限界として記録したまま残す。

**9 件のうち、いま閉じているのは 2 件。** 1 件目は退役した `--set` 迂回を案内する 12 行で、
T13 自身の最後の commit 群が 6 ファイルとも書き替えた。2 件目が `accept.py` の `gate_ok` で、
`status in ("passed", "passed_with_warnings")` になった。全 criterion を `skipped` と宣言した
gate は、accept_requirements の `acceptance_gate_not_failed` を満たさない。2 / 5 / 2 の見込みの
うち、最初の 2 は両方とも進んだ。残り 7 件は上表の「いま」欄のとおりで、どれも再測して同じ値が出た。

**新しい債務を 1 件記録する。** `govern.check_accept` は承認を worktree の HEAD に束ねている。
accept 側の head 判定が branch の先端を見るようになった後も、そちらは worktree の HEAD のままである。
detached worktree の抜け道は承認にも残る。本 run より前からある問題で、本 run では直していない。

#### CLI の答えになっていなかった 3 件

上表とは出どころが違う。レビューが残した先送りではなく、本節と §7 が散文の中に書いたまま
置いていた 3 件である。どれも「予測できる不在」に対して traceback や 89 行の manual を
返していた。3 件とも閉じた。実測は直す前と後の両方を実プロセスで取っている。

| 先送り | 実測（直す前） | いま |
|---|---|---|
| run-state が無いと 6 動詞が traceback を出す | 空のディレクトリで `check`・`next`・`verdict`・`status`・`resume`・`approve` が `FileNotFoundError: [Errno 2] No such file or directory: '<cwd>/run-state.json'` で落ちる。`scripts/orchestrate.py` 経由は exit 1、`rig-wb` 経由は `cli.py` の guard が拾って exit 2。`init` は引数無しで `IndexError`、`verdict --by alice` は `./--by` を開いて同じ形で落ちる | 閉じた。`399a60e` が `_state_path` の 1 箇所で `Refusal` に替えた。6 動詞とも `[ERROR] no run-state at run-state.json: ` + 作り方 1 行で exit 2 になる。両経路で打てるのは `check`・`next`・`verdict`・`approve` の 4 本で、`status` と `resume` は `_orch_delegates` に無く `scripts/orchestrate.py` 専用のまま——動詞は 1 本も増やしていない |
| `rig-wb validate --help` が usage を出さず検証を走らせる（§7 が「比較から外した」と書いた 2 本の片方） | `--help` は未知の引数として素通りし、92 行の報告と `PASS: 71 / WARN: 15 / FAIL: 0` を出して exit 0。1.29 秒。`python3 scripts/validate.py --help` も同じで 1.05 秒 | 閉じた。`581e869` が `cmd_validate` の先頭で答えるようにした。usage 10 行、0.10 秒、exit 0。木は読まない。もう 1 本の `usage` は開いたまま |
| `_usage_for` が `models`・`probe`・`queue` で module docstring に落ちる | 3 本とも 89 行を印字する。ほかの 18 本は 1〜9 行。落ちる原因は slicer ではなく、slicer が読む docstring に 3 本の記載が無いこと | 閉じた。`fa65ef3` が docstring に 3 本を足した。`models` 4 行・`probe` 4 行・`queue` 6 行になり、登録済み 21 動詞すべてに usage がある。最長は `plan` の 9 行。未知の動詞への fallback は残した |

**直さずに記録した 4 件。** どれもレビューが本 run の外だと判断したもので、実測だけ置く。

- `rig-wb validate --bogus` は未知の flag を黙って捨てる。92 行の検証を最後まで走らせ、exit 0 を返す。本 run が先回りして答える形にしたのは `--help` だけで、ほかの未知語はいま素通りである。
- `orchestrate/cli.py:103` の Exit code の行に 2 が無い。書いてあるのは `0=success / 1=error or ESCALATE / 3=run parked at a human gate` である。本 run が足した拒否はすべて 2 で終わる。行のほうが実装に追いついていない。
- `_state_path` は先頭が `-` の token を flag と見て既定値へ落とす。`-state.json` という名前のファイルは指定できなくなった。実在しない形なので直していない。
- `scripts/orchestrate.py` 側でこの拒否を固定しているのは `status` と `resume` の 2 本だけである（`tests/test_cli_smoke.py`）。ほかの 4 本は `rig-wb` の綴りで固定してある。同じ関数に同じ引数で入るので、経路ごとの二重掛けはしていない。

#### 本節で直した、本ブリーフ自身の数値

追記ではなく訂正である。

| 直したもの | 旧 | 新 |
|---|---|---|
| ゲート基準の数 | 34 | 宣言は 35 エントリ、異なり名は 34。差の 1 は `no_unrelated_refactor` が `bugfix` と `refactor` の両方に書かれているため。§4 の内訳の表（6 / 2 / 26）は異なり名で数えた 34 のままで正しい |
| `README.md` | 1,270 行超 | 1,385 行 |
| `skills/engine/SKILL.md` | 720 行 | 742 行 / 104,492 バイト（T3 が §3.5 に 1 行足したあとの再測） |

「`providers.py` は 3,645 行のまま触られていない」という記述は本ブリーフには無い。§3 と
「前提・制約」の 3,644 行は着手時の値として正しく、今日の値 3,747 は上表に書いた。

#### 2 段目で再現しなかった中継値

上の「本節で直した」表と同じ規律で、今度は本節自身が書いた数値を直す。どれも run が
実測で否定したか、本 run が数え直して別の値になったものである。

| 本節の記述 | 実測（`1e2adad`） | 数え方 |
|---|---|---|
| `agents/` の 2 枚は「`agents/` にしか存在しない」 | 誤り。`facets/personas/cognitive-economist.md` と `facets/personas/lazy-senior.md` が既にある。`-reviewer` の接尾辞が無い名前なので、名前での突き合わせが外した | ファイル名の直接確認と `tests/test_reviewer_surface.py::test_the_adversarial_pair_the_brief_called_unique_already_exists_as_personas` |
| `agents/*.md` を落としても「事前宣言された `tools:`」だけが壊れる | `tools:` を持つのは agents 12 枚すべてで、persona 31 枚は 0 枚。つまり移動では引き継げない。加えて `agents/<stem>.md` は plugin の `rig:<stem>` subagent type の出所でもある | `grep -l '^tools:' agents/*.md` と `… facets/personas/*.md` |
| ヘルプに出ない 15 動詞 | T4 の着地で 13、T10 の着地で 4。`graph`・`install-shim`・`models`・`probe` である | registry のトップレベル動詞 37 から、`rig-wb --help` が名を出す 33 を引く |
| 能力 137 / トップレベル 39 | 135 / 37 | `registry.CAPABILITIES` と `registry.CLI_CAPABILITIES` の長さ |
| `japanese_material_metadata` は `providers.py:1600` に定義、他に参照は無い | 定義は `composition.py` の同名関数へ移った。「他に参照は無い」は規則しだいで真にも偽にもなる | 2 通りに数えた。bridge 経由（`from …providers import <名前>` と `providers.<名前>`、`composition.py` 自身は除く）は `1e2adad` で 12 ファイル 59 か所、`tests/` 52・`benchmarks/` 7・`rig_workbench/` 0。import 元を問わず 20 名の呼び出しを数えると 16 ファイル 73 か所で、`rig_workbench/` に 9 か所入る。うち 6 か所は bridge を置く `providers.py` 自身、残る 3 か所は `commands.py`・`queueing.py`・`runstate.py` で、どれも `composition` から直接 import している。T12 の docstring が書く 58 / 51 は、どちらの規則でも出ない |
| `providers.py` 3,747 行 / 関数 97 / クラス 4 / モジュール変数 41 | 3,102 / 82 / 3 / 34。`global` 文は前後とも 0 | `wc -l` と AST のトップレベル走査 |
| `commands/rig.md` を落として壊れるのは「打つ人と description 1 行」 | `rig:rig` の綴りを持つ tracked file は、本ブリーフとこの 1 枚を除いて 23。うち 5 枚は `rig_workbench/` の中にある | `1e2adad` で `git grep -l 'rig:rig'` が返す 25 枚から 2 枚を引いた |
| T9 の検証欄「30 枚の凍結を 29 に下げる」 | 30 枚のまま。shim を残すので枚数は動かない | `ls commands/*.md` |

T5 の run が中継した 2 枚の類似度 0.82 / 0.81 は、本 run では数え直していない。同名の
persona が実在することのほうが結論を決めたので、類似度は判断に要らなかった。

#### 行番号ポインタは腐る — 以後シンボルに掛ける

本節は `<file>:<n>` の形のポインタを多用した。そのうち、同じ文がシンボル名も書いていて
機械的に当たれるものが 29 個ある。`1e2adad` で 1 つずつ当て直すと、シンボルがまだその行
（範囲指定なら範囲の中）にあるのは 14 個、外れたのは 15 個。半分が 3 日で腐った。
外れた 15 個のうち 13 個を下に分けて挙げる。残る 2 個はここに置く。
`prompt_regression.py:123` は 125 行目へ動いた。`accept.py:270-290` からは
`git merge --squash` の呼び出しが 306 行目へ抜けた。

外れ方は 3 種類ある。1 つ目はずれが 1〜9 行のもの。`commands.py:1220`・`queueing.py:519`・
`destructive.py:53`・`injection.py:42` がこれにあたる。`hardening.py:40`・`schema_diff.py:28`・
`secrets.py:52` も同じ。2 つ目は大きく動いたもの。`lifecycle.py:188` の `ensure_rig_gitignored` は
54 行目、`task_package.py:20` の `compose()` は 285 行目にある。`lifecycle.py:392` は 499 行目、
`accept.py:156` は 176 行目。3 つ目は指す先そのものが消えたもの。`runstate.py:624` の遅延
import は T12 が module 級に直した。`providers.py:1600` の定義は `composition.py` の
`japanese_material_metadata` へ移った。

腐らないポインタもある。T12 の run ディレクトリが持つ `providers.py:<n>` 形のポインタは
1 個だけ。`reviews/ai-smell-reviewer.md` の `providers.py:1207` である。レビューはこれを head
`80aeb15` で `run_verifiers_parallel` と突き合わせ、一致と書いた。`1e2adad` でも 1207 行目は
その定義のまま。当たり続けたのは、その行を触る commit が無かったから。読み手には、当たる
ポインタと腐ったポインタの区別がつかない。

**方針: 以後、本ブリーフの表と散文はシンボルに掛ける。** 書き方は
「`rig_workbench/workbench/accept.py` の `gate_ok`」で、行番号は付けない。行でしか指せない
ときは、行番号ではなく引用文を置く。この規律を明文で持っているのは、木の中では
`orchestrate/providers.py` の module docstring だけ。`git grep -il 'not by line'` が返すのは
この 1 ファイルである。行ではなく関数で名指すと書き、
理由も添えている。docstring の中に書いた行番号は、その docstring の先頭から数えられてしまう。
本節の既存のポインタは、触る行が出るたびに直す。まとめて直す run は立てない。

#### 本節が確かめていないもの

§「測定の限界」と同じ規律で分けて置く。以下は中継された値をそのまま書いたか、本節では
数え直していない。

- `workbench` の 5 本の柱（A〜E）のファイル数・行数・`print` 数の内訳。総計の 58 ファイル
  20,833 行と `print` 513 は数え直して一致した（`print` は AST 走査）。**どのファイルがどの柱かの
  割り当ては判断であって測定ではなく、本節では再現していない。**
- D の 18 動詞の「1 動詞あたり約 2 本のテスト」。名前が狭い 12 動詞は 1〜2 本で合う
  （anomaly-trigger 1・change-graph 1・budget-plan 1・route-team 1・assurance-derive 1・
  dev-loop 1・expected-outcome 1 など）が、`receipt`・`contract`・`import`・`intent` は
  英語の普通名詞なので名前では数えられない。未測定として置く。
- `providers.py` の AST 呼び出しグラフ（11 クラスタ・クラスタ間 54 辺・クラスタグラフが DAG・
  入次数 0 は 1 頂点・A/C/E/J は葉）。行数・関数数・クラス数・モジュール変数・`global` 0 は
  数え直した（上表）。**クラスタの切り方と辺の数は再現していない。**
- 「公開 29 名のうち production から呼ばれるのは 13」。29 は数え直して合う。13 は未測定。
- 分割時に落ちるテストの見積もり（2 / 136 と 112 / 136）。分母を数え直すと 136 ではなく 153、
  `run_provider` は 59 ではなく 62 だった。**分子の 2 と 112 は数え直していない。**
- 直近 50 commit の数値トークン 255 / 190 / 65 / 0。「数値トークン」と「近くに測定コマンドが
  ある」の定義が再現できる形で残っていないので、未測定とする。足すもの 1 の根拠は
  この数字ではなく、`compose()` が数値の欄を持たないという構造のほうに置いた。
- `.rig/runs.jsonl` と `.rig/context.jsonl` はどちらも git の管理外で、この作業ツリーにしか
  存在しない。**別のチェックアウトでは再現しない。**

## 第 2 段の実測 — 表にして分かったこと

能力レジストリ（`rig_workbench/registry/`）は宣言だけで、実行経路は 1 行も変えていない。表の規模と、
表が投影されるはずの表面との照合結果は次のとおり。

| 測る対象 | 実測 | 崩れたら落ちるテスト |
|---|---|---|
| 宣言された能力 | 137（トップレベル 39 / `wb` 51 / `govern` 10・`pack` 21・`eval` 10・`baseline` 3・`githooks` 3 で 47） | `tests/test_capability_registry.py::TestContainer::test_every_dispatchable_verb_is_declared_once` |
| 表と実 CLI の一致 | 6 つの親グループそれぞれ、およびトップレベルの動詞集合が完全一致。どちらの側も実行時に読む（凍結した写しは持たない） | `tests/test_capability_registry_vs_cli.py::test_each_parent_group_offers_exactly_the_verbs_the_registry_declares_under_it` / `::test_the_verbs_the_top_level_dispatcher_accepts_are_exactly_the_ones_the_registry_declares` |
| ヘルプに出ないトップレベル動詞 | 39 中 15。打てば答えるが、`--help` にも契約テストにも README にも出てこない | `tests/test_capability_registry_vs_cli.py::test_exactly_fifteen_dispatchable_top_level_verbs_are_missing_from_the_help_text` |
| 出力スキーマを宣言する能力 | 137 中 22。すべて第 1 段の凍結集合（38 種）の中にある | `tests/test_capability_registry_vs_surfaces.py::test_every_output_schema_a_capability_declares_is_an_id_the_frozen_registry_pins` |
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

## 第 3 段の実測 — 柱の切り方と、最初の 4 柱

### 柱を触る前に置いた 3 つのラチェット

第 3 段の作業量は 1 セッションに収まらない。進んだのか漂ったのかを言える状態にしてから
柱に触る必要があるので、先に 3 つ置いた。どれも「下げる方向にしか動かない」ものである。

| ラチェット | 凍結するもの | ファイル |
|---|---|---|
| 構造と効果の天井 | 実行時の循環と `if TYPE_CHECKING:` だけの循環 1 件を**集合として**（着手時 6 件、いま 5 件）、ハブ `workbench/state.py` の入次数 37、`workbench/cli.py` の出次数 39、パッケージ別の効果地点 6 種を天井として | `tests/test_architecture_inventory.py` |
| 禁止呼び出しの台帳 | `os.environ` から `time.clock_gettime_ns` まで 22 の名前を ruff の banned-api にし、`T20` と合わせて木全体で有効にする。未移行の経路は per-file-ignores 42 行で覆う | `pyproject.toml` |
| 層のルール | 移行済みの柱の判定層が import してよいのは、標準ライブラリ・自分の柱・`rig_workbench.ports` の 3 つだけ | `tests/test_layering_contract.py` |

循環を**件数ではなく集合**で凍結してあるのが要点である。件数なら、1 件消えて 1 件生えたときに
相殺されて何も起きない。集合なら新しい循環は必ず赤くなる。

台帳の行の意味も、ふつうの lint 抑制とは違う。到着時点では net-zero になるように未移行の経路を
覆ってあり、**柱がポートの背後に移ればその行が消える**。行の削除が移行の完了そのものであり、
横に書いてある件数がその代金である。実際、先頭にあった 5 本——`rig_workbench/govern/**`
（68 print・12 banned）、`rig_workbench/eval/**`（16 print・31 banned）、
`rig_workbench/packs/**`（51 print・11 banned）、`rig_workbench/validation/**`
（13 print・3 banned）、`rig_workbench/orchestrate/**`（0 print・5 banned）——はもう無い。
`tests/test_layering_contract.py` の `MIGRATED` がその 5 つになっているのと対になっている。
柱まるごとの行として台帳に残っているのは `rig_workbench/workbench/**`（513 print・31 banned）
1 本だけで、あとは直下の袋の 27 行（195 print・69 banned）と `scripts/` の 4 行である。
eval の行を消して初めて ruff が `eval/` を実際に検査するようになった点も
台帳に書いてある。行があるうちは `--select TID251` を足しても per-file-ignores は外れず、
それまでの緑は何も測っていなかった。消す前に禁止呼び出しを 1 つ戻し、規則が鳴ることを
確かめてある。

**`orchestrate` は柱まるごとの行が消えた代わりに、ファイル 1 本の行が残った。**
`rig_workbench/orchestrate/config.py` の `os.environ` 3 件である。これは負債ではなく決定で、
台帳の「恒久的に認めた場所」の側に、ディレクトリではなくこのファイルだけを指す行として
置いてある。理由は下記「`orchestrate` は 4 パスかかった」に書いた。
**台帳が記録するのは例外であってディレクトリではない**、というのがこの形の主張である。

### 柱はディレクトリ名と一致しない

第 3 段を「6 ポート、1 柱ずつ」としか書いていなかったので、柱の実体を測った。第 2 段の末尾で
`rig_workbench/` は 179 モジュール、パッケージ内の import 辺は 597 本だった。ポート 2 本と
`registry/parser.py` が加わった時点で 182 モジュール・617 辺、2 本目の柱が `eval` に 2 つの
アダプタを足し、§10 が `packs` から署名側を落とした時点で 183 モジュール・634 辺だった。
その後の柱がアダプタを足したいまは 193 モジュール・684 辺である（今日の木で再測。
`orchestrate` の分は 188 → 193 の 5 本で、`yaml_adapter.py` と 4 つの surface である）。アダプタを足すと辺は増える——柱をまたぐ辺を
消しているのではなく、**判定層から外へ出る辺を 1 か所に集めて向きを裏返している**からで、
数が減ることを移行の指標にしてはいけない。指標は `MIGRATED` と台帳の行である。この
graph を読むと、ディレクトリ名は柱の名前になっていない。

- **`validation/` は柱ではなくシェルである。** 他パッケージからこのディレクトリへ入る辺が
  **0 本**。誰も依存していないものを先に移しても、層のルールが効いている証拠にならない。
- **`packs` と `eval` と `orchestrate` は別々の柱である。** 着手時は 12 モジュールの実行時循環が
  この 3 つをまたいでいて（`packs` 8 本、`eval` 2 本、`orchestrate` 2 本＝`graph` と `recipes`）、
  循環の中にある以上は別々に移せなかった。第 2 柱でその成分を崩したので、いまは 3 つとも
  独立に移せる。崩し方は下記「12 モジュールの循環を 3 本の関数内 import で崩す」。
- **`rig_workbench/` 直下の 32 本は層ではなく袋である。** 共通の役割がない。台帳がこの 32 本を
  1 行の glob で書けず、27 行に分けて並べているのも同じ形の現れである。`rig_workbench/*.py`
  の `*` は `/` をまたぐため、柱の行を全部死文にしてしまう。
- `registry/` は宣言だけで効果地点 0、`ports/` はアダプタ層で、どちらも柱ではない。

結果として、測って出た単位は柱 6 本とシェル 1 つになる。柱は govern、`eval`、`packs`、
`orchestrate`、`workbench`、直下の 32 本。シェルは `validation` である。着手時に 5 本と
数えていたのは `eval`+`packs`+`orchestrate` の一部が循環で 1 本に縛られていたためで、
その縛りは第 2 柱で解けた。

この節の辺とモジュールの数は、すべて `tests/test_architecture_inventory.py` の走査で数えた。
実行時の強連結成分は同じファイルの `BASELINE_RUNTIME_CYCLES` に 1 本ずつ書き出してある。

### なぜ govern を最初にしたか

基準は 3 つ、どれも測った値である。

| 基準 | govern | ほかの柱 |
|---|---|---|
| 実行時の循環に入っていない | 入っていない | 当時 `eval`・`packs`・`orchestrate` は 12 モジュールの循環で繋がり、`workbench` は内部に 3 件の循環を持っていた |
| 他パッケージへの出辺が最小 | 2 本（`govern/cli.py` → `gitroot`、`conformance` → `workbench.reporting`） | `workbench` 40 本、`orchestrate` 31 本、`packs` 25 本 |
| 6 本のポートを全部使う最小の柱 | 11 モジュール・効果地点 87 で 6 種すべてが 1 以上 | 6 種そろうのは `orchestrate`（272）・`workbench`（562）・直下（285）のみで、どれも大きい |

3 つ目が決め手である。6 種そろっていない柱で始めると、使われないポートの設計が
最初の柱では検証されないまま残る。

効果地点の値は `BASELINE_EFFECT_SITES`、辺の数は同じファイルの走査である。移行済みかどうかは
`tests/test_layering_contract.py` の `MIGRATED` が持つ。

### govern を移して分かったこと

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

**govern の CLI は表の投影になった。** `rig_workbench/registry/parser.py` が
`children("govern")` から argparse を組む。`govern/cli.py` の `build_parser` はその生成物に
なった。ただし生成されているのは今日もここだけである（下記「表から生成されているのはどこか」）。出荷していた手書きの
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
  残り 2 柱で決める（`wb` は `workbench` の表面なので、次の柱がその場である）。
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

### 表から生成されているのはどこか

§2 は「全機能を 1 箇所に宣言し、CLI・talk・MCP・GitHub Action をその投影にする」と決めた。
**その決定が実装に届いているのは `govern` だけである。** 内訳は実測で次のとおり。

| グループ | 動詞 | パーサの出どころ |
|---|---|---|
| `govern` | 10 | 生成。`registry/parser.py` が `children("govern")` から組む |
| トップレベル | 39 | 手書き。`rig_workbench/cli.py` の dict と if/elif |
| `wb` | 51 | 手書き。`workbench/cli.py` の `build_parser` |
| `pack` | 21 | 手書き。`packs/cli.py` が `add_parser` を 20 回並べる |
| `eval` | 10 | 手書き。`eval/cli.py`。柱は移行済みだがパーサは移行の対象ではない |
| `baseline` | 3 | 手書き。`baseline.py` |
| `githooks` | 3 | 手書き。argparse ですらなく `argv` の直接走査 |

`registry/parser.py` に `children(` が現れるのは 1 箇所、docstring の用例のみで、
`build_group_parser` を呼ぶ出荷コードは `govern/cli.py` の 1 本だけである。つまり
`govern` 以外の 127 件は、レジストリと argparse の 2 箇所に同じ動詞が書いてある。

**その二重を正直に保っているのはテスト 1 本である。** `tests/test_capability_registry_vs_cli.py`
は凍結した写しを持たず、両側を実行時に読む — レジストリ側は `children(parent)`、CLI 側は
`rig-wb <parent> --help` を実プロセスとして起動して読む — そして集合が完全一致することを
要求する。第 3 段の残りで柱を移すとき、パーサを生成に切り替えるかどうかは柱ごとの判断であり、
切り替えない限りこのテストが唯一の接着剤である。§10 の第 1 パスがレジストリと argparse を
同じコミットで触らざるを得なかったのは、まさにこの形のためである。

### 2 本目の柱が `ProcessRunner` を広げた

6 本のポートは govern の呼び出しから形を取った。govern の `subprocess` は 2 箇所しかなく、
どちらも `git` を 1 回呼んでテキストを読むだけである。2 本目の柱の候補（当時は循環で 1 つに
縛られていた `eval` + `packs` + `orchestrate/recipes.py`）の 30 箇所が同じ形をしているかを、
移す前に数えた。

| 引数 | 30 箇所のうち | 値の内訳 |
|---|---|---|
| `cwd` | 30 | |
| `capture_output` | 30 | 全件 `True` |
| `timeout` | 30 | 5〜30 秒の定数 28 件と変数 2 件 |
| `text` | 27 | 全件 `True` |
| `encoding` | 25 | 全件 `"utf-8"` |
| `errors` | 25 | `"replace"` 24 件、`"surrogateescape"` 1 件（`eval/affected.py:381`） |
| `shell` | 24 | 全件 `False` |
| `check` | 4 | 全件 `False`、4 件とも `packs/publisher.py`（§10 で消えた側） |
| `input` | 3 | str 2 件（`eval/runner.py:305`・`:409`）、bytes 1 件（`eval/affected.py:412`） |
| `env` | 3 | |

分かったことは 4 つある。3 つはポートを広げる理由になり、1 つは広げない理由になった。
いまのポートの形は次のとおりである（`rig_workbench/ports/__init__.py`、アダプタは
`ports/local.py`）。

    run(argv, *, cwd=None, env=None, timeout=None,
        input: str | bytes | None = None, text: bool = True, errors: str = "replace")

`text=True` の腕は `CompletedProcess[str]`、`text=False` の腕は `CompletedProcess[bytes]` を
返すと `@overload` 2 本で述べてあるので、`.stdout` に手を伸ばした呼び出し側は、どちらを
持っているかを絞り込む前に教えられる。出力の捕捉は相変わらず必須で、そこだけが
`subprocess.run` より狭い。

- **text を必須にした前提が成り立たなかった。** 3 箇所は bytes のまま読む。`eval/affected.py:412` は
  `git cat-file --batch` で、長さ接頭辞つきの binary が返り、stdin にも bytes を流す。
  `eval/execution.py:43` は `git diff --binary` と `ls-files -z` の出力をそのまま
  sha256 に食わせて実行差分の同一性にしている。`eval/gate.py:45` は出力を誰も読まず
  `returncode` だけを見る。前の 2 つはテキストで復号した時点で値が壊れる。ポートの docstring は
  「capture も text も必須」と書いていたので、30 箇所のうち本当の非互換はここだけだった。
  `text` は必須ではなく既定値になり、規則ではなくなった。
- **復号の指定が抜けていた。** アダプタは `text=True` しか渡していない。これはロケール既定の
  エンコーディングと strict での復号であり、柱の 25 箇所が明示している
  `encoding="utf-8", errors="replace"` とは別物である。差が出るのは復号できない出力が来たとき
  だけだが、そのとき起きるのは値の違いではなく `UnicodeDecodeError` であり、呼び出しごと落ちる。
  git は別のエンコーディングのパス名や著者名がリポジトリに 1 つあれば、その出力を返す。
  アダプタ側を 2 引数に直した。
- **`input=` を足す時が来た。** ポートの docstring は「呼び出し側が持ってきたら足す」と書いて
  あった。3 件が持ってきた。うち 1 件が bytes なので、型は `str | bytes | None` である。
  text と型を合わせるのは呼び出し側の責任で、そこは `subprocess.run` と同じにしてある。
- **`check=` と `shell=` は足さなくてよい。** `check` は 4 件すべて `False` で、例外で失敗を
  伝える呼び出しは柱に 1 つも無い。`shell` は柱の 24 件がすべて明示の `False`、木全体の
  `shell=True` 3 件（`orchestrate/providers.py:2314`・`:2787`、`orchestrate/commands.py:248`）は
  どれも柱の外にある。

`cwd`・`env`・`timeout` は最初のポートのままで足りている。

**数え方。** `tests/test_architecture_inventory.py` の効果走査と同じ定義で AST を歩いた。
`subprocess` という名前への属性呼び出し（`run` / `Popen` / …）を 1 箇所と数え、その呼び出しの
keyword 引数を集計する。grep ではない。行マッチの値が AST の値と食い違った件は第 3 段で既に
起きており（末尾「測定の限界」）、同じ物差しで数えないとラチェットの天井とも突き合わせられない。
走査対象は `rig_workbench/eval/**`・`rig_workbench/packs/**`・`rig_workbench/orchestrate/recipes.py`
で、内訳は `eval` 24 件・`packs` 5 件・`recipes.py` 1 件の計 30 件。`shell=True` の 3 件は
同じ走査を `rig_workbench/` 全体に掛けて出した。

**残っていた 1 件は、ポートを広げて取り込んだ。** `eval/affected.py:381` の
`errors="surrogateescape"` は、text 復号を 1 種類に固定するポートでは表せなかった。そのファイルの
docstring が書いているとおり、`replace` で復号するとパス名の綴りが変わり、branch と base の
グラフが照合しなくなる。誤差ではなく誤答である。逃げ道は 2 つあった — 1 件だけ
`subprocess.run` をポートの外に残して注記を添えるか、`errors` を呼び出し側が選べるようにするか。
前者は 1 回の呼び出しのために柱の台帳の行を生かし続けることになり、例外が 1 つも無いことを
主張している場所に例外を置く。採ったのは後者で、`errors` は引数になり、既定は残り 24 箇所が
綴っている `"replace"` のままである。`errors` は text 腕にしか無い。`subprocess.run` は
`errors=` を text 要求として解釈するので、bytes 腕でそのまま渡すと腕そのものが無効になる。
`errors=` と `text=False` の同時指定は無視ではなく拒否する。

ポートのメソッドは名前からではなく呼び出し地点から書く、という §3 の決めごとを守った結果として
2 つ足して 2 つ足さなかったのであり、好みで選り分けたのではない。

### 12 モジュールの循環を 3 本の関数内 import で崩す

2 本目の柱に入る前に、`eval`・`packs`・`orchestrate` を 1 つに縛っていた 12 モジュールの
強連結成分を崩した。成分を閉じていたのは `rig_workbench/packs/` の中の関数内 import 3 本で、
どれも「必要になったら取りに行く」形だった。3 本とも、取りに行くのをやめて
**引数（既定値なしのキーワード）で受け取る**形に逆転させてある。

| 逆転した辺 | 述べ直した protocol | 消えた成分 |
|---|---|---|
| `lock` → `publisher` | `lock.PublisherVerifier`、`validate_lock_root(..., verify_publisher=)` | `eval.affected`・`eval.gate`・`orchestrate.graph`・`orchestrate.recipes`・`packs.sources`・`packs.tester` が循環から外れ、残りが {catalog, lock, resolver, validation} と {installer, publisher} に割れた |
| `publisher` → `installer` | `publisher.LocalQualityStatus` | {installer, publisher} |
| `validation` → `resolver` | `validation.CoreReferenceIds`、`validate_pack(..., core_ids=)` | {catalog, lock, resolver, validation} |

3 本目は選べる辺ではなかった。`validation` はその成分の唯一の出口であり、
`validation` → `resolver` はその成分のどの最小フィードバック辺集合にも入る。

**既定値を与えない**のが 3 本に共通の要点である。自然に読める既定値は
`resolver.core_reference_ids` であり `publisher.verify_publisher_signature` であって、
それはこの signature が消すために存在する import そのものだからである。既定値を書いた瞬間に
成分は反対側から閉じ直す。

結果、実行時の強連結成分は 6 件から 5 件になり、`rig_workbench/packs/` のモジュールは
そのどれにも入っていない。件数ではなく集合で凍結してあるので、1 件消えて 1 件生えても
相殺されない（`tests/test_architecture_inventory.py` の `BASELINE_RUNTIME_CYCLES`）。

### 循環を崩したところに fail-open が 1 つ生まれ、測って戻した

`lock` → `publisher` を切った直後、`resolver` は検証器を import できなくなり、
`verify_publisher=None` を渡す形になっていた。理由として書かれていたのは性能だった。

**これは節約ではなく fail-open である。** `pack.sig.json` はパックが持つファイルのうち
`manifest["hashes"]` が覆っていない唯一のもので（`validation.py` が非資産に分類する）、
検証器を外すと `validate_lock_root` の他のどのドリフト検査もこのファイルを見ない。実際、
署名バイトの差し替え・失効鍵の提示・署名ファイルが 1 つも無いまま `pack.lock.json` に
`verified-publisher` と書く、のいずれもが解決を素通りし、`resolved_collection` は 3 件とも
`verification_status='verified-publisher'` と報告した。失効も有効期間も、解決経路では
効かなくなっていた。

誰も気づかなかった理由も調べてある。木にあった改竄テストは 6 本とも
`lock.validate_lock_root` を直接呼び、検証器を自分で手渡していた。だから `resolver` を
丸腰にしたコミットは、その 6 本に `verify_publisher=verify_publisher_signature` を書き足す
だけで緑のままでいられた。改竄と `resolve_all` / `resolved_collection` —— ペルソナ・レシピ・
wiki のすべてが通る関数 —— を組み合わせたテストが 1 本も無かった。
`tests/test_pack_resolve_publisher_trust.py` の 5 本がその穴を塞いでいる。

**性能の言い分は測って退けた。** 署名済みパックを 1 本 install した一時プロジェクトで、
`verify_publisher_signature` 単体と `resolve_all` をそれぞれ `time.perf_counter` で計った
（各 30 回・20 回の中央値、4 回実行）。検証は 1 パックあたり中央値 0.38〜0.69 ms、同じ木の
`resolve_all` は中央値 25.5〜26.0 ms である。解決 1 回の数パーセントを、資産の出所を
確かめない理由にはできない。

直し方は、性能の言い分を受け入れずに構造で解いた。循環に入っていたのは**署名する側**である
（`publisher` が `resolver.core_reference_ids` と `tester.compose_case_prompt` を引き、
`tester` が `resolver` を引く）。**検証する側**は `.manifest`・`.model`・`__version__` しか
必要としないので、そのまま `packs/signature.py` という葉モジュールに移した。`verify_publisher`
は必須キーワードのままで、変わったのは出荷側の呼び出しに `None` が 1 つも無くなったことである。

その `signature.py` は §10 で機構ごと消えた。それでもこの節を残すのは、消えたのが機構であって
教訓ではないからである。循環を崩す作業は、切った辺の先にあった検査を黙って外すことがある。
検査を外した理由が性能なら、その性能は測ってから言う。

### eval をポートの背後に移す

| 種別 | 移行前 | いま |
|---|---|---|
| `print` | 16 | 0 |
| `subprocess` | 24 | 0 |
| `open(w,a)` | 0 | 0 |
| `write_text` | 1 | 1 |
| `os.environ` | 3 | 0 |
| 壁時計 | 4 | 0 |

**残った `write_text` の 1 は、govern の 6 と性質が違う。** govern の 6 はシェル自身の書き込み 4 と
ポート呼び出し 2 で、走査が受け手を解決せず属性名で数えるせいで残る数え方の産物だった。
eval の 1 は `affected.py` の `target.write_bytes(...)` で、`_graph_at` が revision を読むために
作る `TemporaryDirectory` へ blob を書く本物の `pathlib` 書き込みである（走査は `write_bytes` を
`write_text` と同じ籠に入れる）。`FileStore` に `write_bytes` は無く、書いてすぐ消す一時ファイル
1 件のために足すのは「名前から書いたポートメソッド」であり、ポートが自分で禁じている形になる。
この書き込みがポート呼び出しになる理由を持つまで、1 が床である。

**柱をまたぐ辺 2 本は、シェルではなくアダプタとして置いた。** `eval/source_graph.py` は
`affected.py` が必要とするものを `BrickGraphSource`（木を入れるとノードと辺が出る）として
述べ、オーケストレータ側の `RIG_HOME`・`build_brick_graph`・レシピ frontmatter を読んで
それを満たす。`eval/pack_layout.py` は `promote.py` の `PackCaseDir` を満たし、パックが評価
ケースをどこに置くかという packs 側の宣言を引き受ける。どちらも判定層から何も import しない
ので、逆転した辺は一方通行のままである。2 本とも `tests/test_layering_contract.py` の
`SHELL_MODULES` に理由つきで書いてある。

**シェルがポートを配るだけでは足りない。** govern が出荷日に持っていた欠陥が同じ形で再発しうる
—— ポートを受け取ったハンドラが、判定層を呼ぶときに渡し忘れると、呼ばれた側の**既定引数**、
つまり本物のアダプタが効いてしまう。本番では上から下まで本物を渡すので 2 つの本物の時計は
一致し、誰も気づかない。`tests/test_eval_forwarded_ports.py` は症状ではなく形で落ちるように
書いてある。`SystemClock`・`SubprocessRunner`・`OsEnv`・`ConsolePresenter` の 4 クラスを
**クラスごと丸腰にし**（全メソッドが送出する）、転送が途切れた場所でコマンドが死ぬようにした。
名前ではなくクラスを潰すのは、既定引数が `def` の実行時に値を束縛するためで、モジュール属性を
差し替えても signature が握っている実体は入れ替わらない。鳴らない仕掛けは無いより悪い。
この 4 通りで赤くなるのを見てから緑にしてある。`test_the_tripwire_is_armed` があるので、
緑が「罠を仕掛け忘れた」を意味することはない。

**`pyproject.toml` の台帳から eval の行が消えた。** 台帳の言うとおり、行の削除が移行の完了
そのものである。

### `orchestrate` は 4 パスかかった

4 本目の柱である。ほかの 3 本が 1〜2 パスで済んだのに対し、ここは 4 パスかかった。

| 種別 | 移行前（分岐点 `e341366`） | いま |
|---|---|---|
| `print` | 212 | 0 |
| `subprocess` | 21 | 4 |
| `open(w,a)` | 4 | 2 |
| `write_text` | 10 | 10 |
| `os.environ` | 19 | 3 |
| 壁時計 | 6 | 2 |

合計 272 → 21。移行前の値は分岐点の木に**今日の walk**を掛けて数え直したものであり、着手時の
表から引き写したのではない。

4 パスの内訳はこうである。

1. **コマンド表面の `print`。** `cli.py` の `main()` が `ConsolePresenter` を 1 つ組み、
   `COMMANDS[cmd](rest, out=out)` で全 21 コマンドに配る。212 → 20。
2. **判定層の `print`・環境・時計・サブプロセス・`import yaml`。** 残る 20 の `print` が消えた。
   一部は `Refusal` を投げ、`_reports_refusals` が同じ行・同じ stream・同じ終了コードに戻す形で
   ある。あわせて `os.environ` 19 → 3、壁時計 6 → 2、`subprocess` 21 → 4。PyYAML のガードは
   その場で `Presenter` 呼び出しにせず、`orchestrate/yaml_adapter.py` へ切り出した。
3. **柱跨ぎ import の逆転。** このパスの直前の木を、`orchestrate` が `MIGRATED` にあると仮定して
   走査した。判定層に柱跨ぎ import が **40 件**あった（`cli.py`・`config.py`・`yaml_adapter.py`
   を数に入れると 44。この 3 本はいまシェル宣言である）。4 つのアダプタ——`batch_surface`・
   `pack_surfaces`・`govern_surfaces`・`package_surfaces`——の背後に入れ、パス 2 で切り出した
   `yaml_adapter` と合わせて **0** になった。アダプタが 4 本に割れたのは graph の都合であり、
   読みやすさではない。workbench への 3 辺をほかと同じ場所に置くと、`orchestrate.config` を通る
   6 モジュールの新しい循環が閉じる。
4. **宣言・仕掛け・台帳。** `MIGRATED` への追加と `SHELL_MODULES` の記述。転送の仕掛け
   （`tests/test_orchestrate_forwarded_ports.py`、13 件）。`pyproject.toml` の台帳の書き換え。

**台帳は柱まるごとの行が消え、ファイル 1 本の行が残った。** 残ったのは
`rig_workbench/orchestrate/config.py` の `os.environ` 3 件である。`config.py` はシェル宣言で
ある。import 時に `RIG_HOME` / `RIG_GLOBAL_RUNS_PATH` / `RIG_CONVERGENCE_K` を読み、資産・
実行ログ・state root がどこかを答える。13 モジュールがこれを import している。`Env` の背後に
移すと **いつ解決するか** が import 時から初回呼び出し時へ変わる。何を答えるかは変わらない。
誰も頼んでいない挙動変更なので、移さないと決めた。

`time.time_ns()` の 2 件には、恒久的な `noqa` を各地点に理由つきで置いた。一方は一意性の源で
ある（凍結した時計は衝突になる）。もう一方は誰も読み返さないベンチマーク台帳の整数フィールドで
ある。そのために `now_ns()` を生やすのは、呼び出し地点ではなく名前から書いたポートになる。
**`Clock` にも、ほかのどのポートにも、この柱では 1 つも足していない。**

**`ProcessRunner` を通さないと決めた呼び出しが 5 件ある。** 走査に残る `subprocess` 4 件と、
既定引数なので走査が数えない 1 件である。どれも「まだ移していない」ではなく「移さない」で、
所有者の決定として書いておく。

- `shell=True` で出力を `DEVNULL` に捨てるもの 3 件（`commands.py` と `providers.py` の
  recipe の `checks:`、および informed-repair の検査）。ポートは `shell=` を持たず、常に捕捉する。
  捨てずに配管すれば、利用者が書いたコマンドの**上限のない出力**を rig が抱えることになる。
  読むのは終了ステータスだけなのに、である。
- 出力が端末へ届かなければならない dashboard 1 件。捕捉すると壊れる。
- 封をした fd を子へ渡す `pass_fds` 1 件（`secure_runtime.py`）。ポートは `pass_fds` を持たず、
  持たせれば他のすべての呼び出し側が記述子を漏らす道を継承する。

**例外クラスの辺 2 本を、逆向きに処理した。** この区別は書いておく価値がある。

| クラス | この柱での向き | 扱い |
|---|---|---|
| `PolicyError` | **捕まえるだけ**（`govern.policy` が投げ、この柱が `except` する） | `govern_surfaces` が拒否を戻り値として返し、クラスは境界で止まる |
| `PackError` | **投げる**（この柱が投げ、`packs/cli.py` の handler が `except` する） | 逆転できない。`pack_surfaces` から再公開する |

逆転できない理由は 1 つだけである。**`except` が比べるのは同一性**だから、別クラスを立てれば
handler は捕まえられなくなる。`PolicyError` は捕まえる側にしかいないので消せて、`PackError` は
投げる側なので消せない。向きが決めているのであって、好みで選り分けたのではない。

**仕掛けを書いていて欠陥が 1 つ出た。** `providers` の 2 つの関数（`_git_diff_evidence` と
`_git_changed_files`）は `ProcessRunner` を受け取る。だが 1 段下の `_git_untracked_files` を
呼ぶときに渡していなかった。注入した runner が黙って迂回され、差分の証拠は本物の `git` と
本物の cwd から来ていた。見つけたのは第 2 パスの監査で、修正もそこでしている。

`tests/test_orchestrate_forwarded_ports.py` は、この**種類**の見落としが再発したときに落ちる。
そのために 2 重に表明する。丸腰にしたアダプタが転送漏れを殺し、かつスタブ自身の呼び出しログに
入れ子の `git ls-files` が入っていなければならない。**最上位の引数だけを見る仕掛けなら、
この欠陥について何も言わない。**

**層の規則の非空テストは、宣言では動かない。** `test_the_scan_reads_real_files_and_finds_real_violations`
は走査の前に `migrated` を全柱に、`shell` を空に**置き換える**。だから `MIGRATED` に 1 つ
足しても、出る件数は変わらない。第 4 パスの前後で 111 件と 111 件だった（直前の `7cc3ff0` と
今日の木に、同じ走査を掛けた実測）。**この数が動くのは import が動いたときだけ**である。
第 4 パスは動かしていない。ラチェットが「何を測っているか」を取り違えないための区別である。

### 開いている設計の問い

**1. ポート 6 本が名前を持たないものが 3 つある。**

- **プロセスの作業ディレクトリ。** `Env` は `expanduser` を持つが cwd を持たない。`GitRepo` は
  cwd をメソッドの引数にしていて、ポートの状態にしていない。木には `Path.cwd()` が
  30 箇所、`os.getcwd()` が 2 箇所ある（今日の木で数え直した）。
- **glob と区別されるディレクトリ列挙。** `FileStore` は `glob`・`is_dir`・`mkdir` を持つが
  `iterdir` を持たない。木には 31 箇所ある。
- **終了ステータス。** `exitcodes.py` を直接読む形のままで、柱の外のシェルの仕事に置いてある。
  層のルールが `ports` に唯一許す自パッケージ import がこの `exitcodes` である。

どれも効果ラチェットの 6 種に入っていないので、増えても赤くならない。govern・eval・`packs`・
`orchestrate` の 4 柱では要らなかった。`orchestrate` は 6 本のポートのうち 5 本を使い、それでも
ポートに 1 つも足していない。残り 2 柱のどこかで必要になる。ポートのメソッドは呼び出し側から
書く、というやり方を守るなら、必要になった柱で足すのが筋である。

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

残り 2 柱はこの問いに必ず出会う。言語についての同じ答え（会話向けの文は日本語、CLI が印字する
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
| 依存の向きだけ修正 | 一方向化 | 実行時の循環が消えるだけ | 不採用 |
| 三値の来歴型 | computed / attested / unobserved | 34 基準（宣言 35 エントリ）の棚卸し | 採用 |
| 二値（計算以外は warning） | 単純 | クロスプロバイダ検証の価値が消える | 不採用 |
| 記録のみ（現状維持） | セット者を記録 | 26 件は自己申告のまま | 不採用 |
| Faceted Prompting を新手法に差し替え | 5 分割と配置順を捨てる | 手法が未定のため、未計測を未計測に置き換えるだけ。PACKS の `ASSET_DIRS` 凍結と衝突 | 不採用 |
| Faceted Prompting を drill で測る | 配置順を入れ替えたペルソナの検出率を比較 | drill の実行コスト | 採用（5 段の外の独立調査） |
| 意図を正本にする（会話一級） | 能力を意図で宣言し CLI を導く | 既存 argparse との二重管理を避ける設計が要る | 採用 |
| CLI を正本にする | argparse を正本、会話は説明文を解釈 | 会話が CLI の語彙に縛られ続ける。現状 | 不採用 |
| publisher 署名機構を全廃 | 署名・検証・トラストルート・鍵生成・失効を落とす | 初回取得時に出所を確かめる手段が無くなる。失効の機構も消える | 採用（§10） |
| 署名を残し既定で任意にする | 拒否をやめ、警告だけにする | 最初の 1 回に鍵の話が残り、説明する語彙も残る | 不採用 |
| `verification_status` ごと lock から落とす | ディスク契約も一緒に片づける | 既存 lock がすべて拒まれ、解決経路が fail-closed なので全 run が止まる | 不採用 |
| ストラングラー単独 | 新層を隣に建て 1 つずつ移す | 二重定義の期間が長い | 部分採用 |
| 契約テスト先行単独 | 網を厚くして一気に切る | 赤の区間が長い | 部分採用 |

## 未解決の問い

- **全件緑かどうかは、まだ確かめきれていない。** テストスイート自体は走らせた。第 2 段の時点で `python3 -m pytest -q -n auto` を通し、3,927 件が通過、2 件が落ちた。落ちた 2 件はどちらも実行環境の産物である。`tests/test_eval_runner.py` の read-only workspace は root で走らせると書けてしまう。`tests/test_japanese_writing.py` の出典検証は、attest された commit がこのチェックアウトの履歴に無い。第 1 段・第 2 段が足した契約テストはすべて緑。ただし `-x` で 2 件目の失敗時に打ち切ったため、「全件緑」を確かめたとは言えない。
- **外部から `rig_workbench` を直接 import している利用者の有無。** 契約凍結を採ったため実務上は守らない方針だが、確認はしていない。
- **26 基準それぞれの分類先。** センサーを作るか、`attested` に落とすか、`unobserved` と認めるかは 1 件ずつの判断で、まだ決めていない。
- **散文と実装のずれ（測定済み・未修正）。** 第 2 段で実行して確かめ、上記「ブリック解決 — 本ブリーフ自身の主張の訂正」のとおり内容を訂正した。残る問いは、どちらに寄せるか — `SKILL.md` と `resolve.md` を実装に合わせて直すのか、散文が約束している `~/.claude/rig/` の user 層を実装するのか。決めていない。
- **旧経路をいつ落とすか。** 版番号は決まった。2 と 3 の間で切り、それを 3.0.0 と呼んだ（§7）。範囲は「利用者から見える話が完結した時点」であり、`workbench` と直下の袋の移行はラチェットの下で 3.x に持ち越す。残る問いは第 5 段——二重定義の期間をいつ閉じるか——だけである。
- **`attested` を厳しい側で始めた場合に、いま通っている run のどれだけが落ちるか。** 実測していない。
- **署名を落とした後、初回取得時の出所をどう担保するか。** §10 は代償として受け入れた。失効に代わる機構も、初回に出所を確かめる機構も、いまのところ何も決まっていない。TOFU 同意とハッシュ連鎖はどちらも「後から変わったか」しか見ない。
- **ポートが名前を持たない 3 つを、いつ足すか。** 作業ディレクトリ、glob と区別されるディレクトリ列挙、終了ステータスである。govern・eval・`packs`・`orchestrate` の 4 柱では要らなかった。上記「第 3 段の実測」の「開いている設計の問い」を参照。
- **残り 2 柱をどの順で移すか。** 大きさは測ってある（`workbench` 562、直下の袋 285）。12 モジュールの循環をどこから崩すかという問いは、第 2 柱で崩して片づいた。`orchestrate` が第 4 柱として済んだので、残るのは `workbench` と直下 32 本の順序だけで、どちらも他の柱に縛られていない。ただし `workbench/` は内部に 3 件の実行時循環を持っており、そこは `orchestrate` に無かった仕事である。
- **残り 5 グループのパーサを生成に切り替えるか。** `govern` の 10 件だけが生成で、127 件はレジストリと argparse の 2 箇所に書いてある。接着剤は `tests/test_capability_registry_vs_cli.py` 1 本である。柱を移すたびに切り替えるのか、第 5 段でまとめるのかは決めていない。
- **署名を落とした跡地の後始末。** `pack.lock.json` に `verified-publisher` と書かれた既存の lock をいつまで受け続けるか、`verification_status` の 3 値をいつ畳むかは決めていない。畳めるとすれば、解決経路を止めずに移行できる手順を先に決めたときである。
- **`wb compose-options --diff` の `non_negative_diff` をどう宣言するか。** 自前の変換器は `Capability` のフィールドにならない。表は `type="int"` と言い、負値の拒否は宣言の外にある。

## 次の一手

第 1 段と第 2 段は済んだ。第 3 段は進行中で、6 本の柱のうち govern・eval・`packs`・`orchestrate` の 4 本が済んだ（`MIGRATED` にはシェルの `validation` も入っており 5 つである）。次は `workbench` である。残る 2 本のうち大きいほうで、効果地点 562（うち `print` 513）、モジュール 58 本、台帳の行は `rig_workbench/workbench/**` の 1 行。`orchestrate` に無かった仕事が 2 つある。ハブ `workbench/state.py` を 37 モジュールが引いていること（インタフェース分離）と、柱の内部に実行時循環が 3 件あることである。

`/rig:tasks "rig V3 移行の第 3 段 — 5 本目の柱 workbench を層の規則の下に入れる。orchestrate と同じ順序（先にコマンド表面の print、次に判定層の効果、次に柱跨ぎ import の逆転、最後に宣言と転送の仕掛けと台帳）で割り、内部の実行時循環 3 件をどこで崩すかを最初に決める"`

5 段の移行はどれも大きく、まとめて実装に入ると検証点が失われる。柱 1 本ずつを検証可能な小タスクに割る。あわせて、第 2 段が記録して直さなかったものをこの段で 1 件ずつ決める。届かない動詞 2 つ、ヘルプに出ない 15 本、`govern` の終了コード、MCP と CLI のずれ、散文と実装のずれ。それぞれ閉じるのか、記録のまま残すのかを決める。govern・eval・`packs`・`orchestrate` の 4 柱ではどれも閉じていない。

**独立した調査（5 段の依存ではない）:** `/rig:drill` で Faceted Prompting の配置順を測る。配置順を入れ替えたペルソナを `--replay` で過去の差分に再適用し、検出率が動くかを見る。§8 の決定に対応する。段の順序には入れない。結果が出た時点で、捨てるか残すかを改めて決める。

## 測定の限界

本ブリーフの数値のうち、第 2 段と第 3 段で書き足したものは実行して確かめ、テスト名か
ファイル名を併記してある。§9 の「測る」と「第 2 段の実測」と「第 3 段の実測」である。
§3 の散らばりの数と「前提・制約」の表も第 3 段で数え直した。以下は確かめていない。

- テストスイート（`pytest`）は第 2 段で走らせた（上記「未解決の問い」参照）が、失敗 2 件で打ち切っており、全件を通し切ってはいない。
- `sensor-bench` の 10/10・0/7、ja-lint の 64/64 といった既存の公称値は、ソースと文書から読んだだけで再現していない。
- 循環依存が実行時に `ImportError` を起こさないことは静的にしか確認していない。
- 効果カウント（`print` / `subprocess` 等）は第 3 段で AST 走査に置き換えた。docstring や文字列リテラル内の出現はもう含まない。上の実測表はその値に直してある。ただし `write_text` は受け手を解決せず属性名で数えるため、ポート経由の呼び出しも 1 件に数え、`write_bytes` も同じ籠に入る。
- 署名検証と `resolve_all` の所要時間（0.38〜0.69 ms / 25.5〜26.0 ms）は、このチェックアウトで署名済みパックを 1 本 install した一時プロジェクトを `time.perf_counter` で計った中央値である。木に残るテストではないので、別の機械では再現しない。桁の主張（検証は解決 1 回の数パーセント）だけが移植可能な部分であり、`packs/resolver.py` のコメントも同じことを「1 パックあたり 1 ミリ秒未満、`resolve_all` は数十ミリ秒」としか書いていない。
- §10 は 2 パスとも着地している。`pyproject.toml` の台帳にあった `rig_workbench/packs/**` の行は、`packs` を柱として移したときに消えた。行に書かれていた 54 print・18 banned は §10 前の値で、消す前に測り直すと 51・11 だった。残る `rig_workbench/workbench/**` と直下 27 行の件数は確かめていない。その柱を移すときに同じやり方で数え直す。
