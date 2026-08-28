# Pack vNext — 設計ブリーフ（#523）

対象 Issue: [#523](https://github.com/itoh-shun/rig/issues/523)
状態: **S1〜S4 実装済み・S5 は道具立てまで完了（リポジトリ作成は owner 判断）・S6 は未着手**。実装スライスの順序と、着手前に人が決めるべき問いを確定させるための文書。
§5 の4つの問いは推奨どおり（①git-only v1 ②S5 で lock 読みへ ③vendoring を正規運用 ④`type` 無しは拒否）に決定して着手した。

---

## 0. この文書が主張しないこと

Pack vNext は「Pack 基盤を作る」話ではない。**基盤の大半は既にある。** 下の §1 は推測ではなく現行コードの棚卸しで、
§2 の差分だけが新規実装である。ここを取り違えると、動いているものを作り直して移行だけが残る。

---

## 1. いま何があるか（実測）

| 領域 | 実体 | 現状 |
|---|---|---|
| manifest | `rig_workbench/packs/manifest.py` | `pack_schema_version` / `id` / `version` / `kind` / `engine` / `dependencies` / `capabilities` / `entrypoints` / `references` / `resources` / `assets` を検証 |
| lock | `rig_workbench/packs/lock.py` | `pack.lock.json` schema v2。`tree_hash` / `asset_hashes` / `engine_version` / `installed_at` / `dependencies` を保持 |
| 信頼 | `trust.py` ＋ `trust-roots.json`、`sign` / `keygen` | 署名検証と `--allow-unverified` の明示承認 |
| install | `installer.py` | ローカル dir / zip / tar / 同梱エイリアス（`domain:` `official:`）。**URL は明示的に拒否** |
| 解決 | `resolver.py` | tier 順 project > user > org > official > core のローカル探索 |
| 検査 | `validation.py` / `doctor.py` / `tester.py` | 依存の循環検査を含む |
| 配布 | `publisher.py` / `remover.py` / `catalog.py` / `evidence.py` | |
| CLI | `packs/cli.py` | `init` `validate` `doctor` `install` `test` `import-results` `sign` `keygen` `remove` `invoke` |
| 同梱 Pack | `packs/domain/{japanese-writing,sales,video-storytelling,decision-humor}` の4つ。`packs/official/` は `__init__.py` のみで実体が無い | |

つまり **manifest 契約・整合性ハッシュ・署名・ライフサイクル CLI・tier 解決は稼働済み**である。

---

## 2. 差分（Acceptance Criteria との突き合わせ）

| # | Acceptance Criteria | 現状 | 差分 |
|---|---|---|---|
| 1 | manifest / lifecycle / source contract の定義 | manifest と lifecycle は有 | **source contract のみ未定義** |
| 2 | 同梱 Pack 分離の移行方針 | なし | 新規 |
| 3 | 1つを独立 repo 化して install → invoke | なし | 新規（S5） |
| 4 | private Git から install | **URL は拒否実装**（`installer.py:41`） | 新規（S2） |
| 5 | credential を manifest/lock/log/run-state に残さない | 該当機構が無いので現状は真だが、設計で担保していない | 設計に組み込む（S2） |
| 6 | version を immutable revision / digest に解決して lock | `tree_hash` は有／commit 束縛は無 | 部分（S2） |
| 7 | install 済み Pack の source/version/integrity を CLI で説明 | `list` / `info` / `explain` が無い | 新規（S3） |
| 8 | type / capability が不要な実行権限を持たない | `capabilities` は自由な slug 列、`kind` は tier であって type ではない | **新規（S1・最重要）** |
| 9 | `validate` が type ごとの必須契約を検査 | なし | 新規（S1） |
| 10 | public / private で install 後の API が同一 | resolver が tier ベースなので**構造的に成立**する | 追加実装不要 |
| 11 | source 不在 / 認証失敗 / revision 不一致 / digest 不一致を fail-closed で**区別**して報告 | すべて `PackError` に潰れる | 新規（S2） |
| 12 | dependency の解決結果を lock | 依存は**記録**されるが解決・取得はされない | 部分（S4） |
| 13 | Pack owner が Rig 更新なしに release できる | 同梱なので不可 | S5/S6 で成立 |
| 14 | 既存利用者向け migration guide | なし | 新規（S5） |

---

## 3. 設計判断

### D1. `type` は新設し、`kind` に相乗りしない

`kind`（`core` / `official` / `domain` / `project`）は**解決の優先順位＝tier** を決めている値で、
Pack が何であるかは表していない。ここに `knowledge` / `skill` / … を混ぜると、tier と権限という
別々の軸が1フィールドに乗り、しかも**インストール済みの全 lock が読めなくなる**。

`type` を新しい必須フィールドとして追加する。値は `knowledge` / `skill` / `workflow` / `policy` /
`reviewer` / `tool`。`kind` は現状のまま据え置く。

### D2. type → 許可される asset 種別は表であり、install 時にも強制する

Issue の「JoyPla のナレッジを追加しただけなのに任意コード実行権限まで得る」を止めるのは、
**type ごとに持てる asset 種別を固定する表**である。

| type | 持てる asset | 実行 |
|---|---|---|
| `knowledge` | `facets/knowledge/**` `resources/**` | 不可（`commands/` `checks:` を持てない） |
| `skill` | ＋ `facets/instructions` `facets/personas` `recipes/**` `commands/**` `agents/**` | provider 呼び出しのみ |
| `workflow` | **`skill` と同一**（差は宣言された意図であって権限ではない） | provider 呼び出しのみ |
| `policy` | `facets/policies/**` | 不可 |
| `reviewer` | `facets/personas/**` `facets/output-contracts/**` | 不可 |
| `tool` | ＋ 実行可能 entrypoint | **可**。署名必須＋明示承認 |

**〔S1 実装時の訂正〕** この文書は当初「validate 時のみの検査は manifest を手で書き換えれば
抜けられるので install 側にも重複させる」と書いていた。実装して分かったのは、**重複は要らないし、
そもそも防御はそこではなかった**という2点である。

* `install_pack` は `validate_pack` を呼んでいる。install 経路は最初から同じ検査を通る。
* manifest を書き換えて `commands/` の宣言を消しても抜けられない。`validate_pack` は
  **宣言に無いファイルの存在を drift として拒否**し、宣言した全ファイルのハッシュを照合する。
  つまり `assets` は pack の内容の全量であり、隠した瞬間に別の理由で落ちる。

守っているのは「検査を2箇所に置いたこと」ではなく「宣言が全量であること」である。前者だけを
足して後者が無ければ、2箇所とも同じ嘘を読むだけになる。

**manifest が宣言できないものは1つある——recipe の `checks:`（orchestrator がホストで実行する
シェルコマンド）。** これだけは recipe ファイル本文（frontmatter ブロックのみ）を読んで判定し、
`tool` 以外の type では拒否する。散文中の `checks:` で落とすと、規則を回避する動機を作るだけなので
frontmatter に限定する。

### D3. source contract = (scheme, source_id, revision, digest)

scheme は `path` / `zip` / `tar`（現行）に `git+ssh` / `git+https` を足す。`registry` は §5 の問い1 次第。

**〔S2 実装時の追加〕** `git+file`（ローカル/マウント済みリポジトリ）も入れた。これは §5 の問い3
（オフライン/閉域）への回答でもある——zip/tar の vendoring だけでなく、ミラーした git リポジトリを
そのまま source にできる。テスト用の逃げ道ではなく、同じ pin と同じ拒否がかかる正規の scheme。

**manifest に URL を書かない。** `install product:joypla@1.4.0` は `.rig/sources.json` の
**名前付き source**（name → base URL テンプレート）を経由して解決する。URL を manifest に書くと、
Pack の中身と配布経路が結びついてしまい、fork も mirror も private 移設もできなくなる。

解決の順序は `version tag → commit SHA → tree digest` で、lock は**3つとも**保持する。
再 install は digest を照合し、不一致は D5 の専用エラーで落とす（`@1.4.0` が後から別物になる状態を許さない）。

### D4. credential は rig が持たない

rig は `git` を呼び、認証は **git 自身の機構に答えさせる**（SSH agent / credential helper / `gh auth` /
OS credential store / CI secret）。rig は token を読まず・promptせず・保存しない。

lock が持つのは `source_id`（`.rig/sources.json` の名前）＋ revision ＋ digest だけで、
**credential を埋め込んだ URL を保持しない**。これを「書かないよう気をつける」で担保しない——
lock / log / run-state を書く経路を1箇所に絞り、その writer が credential 形状の文字列の
シリアライズを拒否する。書き手の規律ではなく、書き込み口の検査にする。

### D5. 失敗は区別して fail-closed

現状は全て `PackError` に潰れる。Issue が求めているのは**区別**なので、理由コードを分ける。

```
source-unreachable     source に到達できない（ネットワーク・host 不明）
auth-failed            到達したが認証が通らない
revision-not-found     source にその tag / commit が無い
digest-mismatch        revision は取れたが tree digest が lock と違う
capability-refused     type が持てない asset を宣言している
engine-incompatible    engine 制約を満たさない
unverified-signature   署名が無い / 検証できない（--allow-unverified で明示承認可）
```

`auth-failed` と `source-unreachable` を混ぜないことには実務上の意味がある——前者は人が
`gh auth login` すれば直り、後者は直らない。

### D6. Rig 本体に domain pack を残さない。ただし fixture は残す

`packs/domain/*` の4つを外部リポジトリへ出す。代わりに **テスト用の最小 fixture pack** を置く
（`packs/official/` は現状 `__init__.py` だけで、実体を持っていない）。fixture まで外部化すると、
pack 機構のテストが外部リポジトリの可用性に依存する＝自分の CI を他人のリポジトリに人質に取らせる。

移行の先頭は **`japanese-writing`**。実運用されており、secure runtime を使う唯一の pack（該当3ファイル）
かつ `resources/` を持つので、外部化で壊れるものが最も多く出る＝最初に出すべき。`resources/` を持つ
pack は他に `video-storytelling` がある。

---

## 4. 実装スライス（各スライスは単独で出荷可能）

| S | 内容 | 依存 | 出荷判定 |
|---|---|---|---|
| ~~**S1**~~ **完了** | `type` フィールド追加（`pack_schema_version` 2）、type→asset 表、recipe `checks:` の type 制限、`pack init --type` 必須化 | なし | 同梱4 pack が `type: skill` で validate を通り、knowledge pack の `commands/` と skill pack の `checks:` がテストで落ちる |
| ~~**S2**~~ **完了** | source contract、`.rig/sources.json`、`git+ssh`/`git+https`/**`git+file`** install、tag→commit→digest の lock（schema 3）、D5 のエラー分類、lock writer の credential 拒否、`pack source add\|list\|remove` と `pack verify-sources` | S1 | 実 git リポジトリを source にした install が commit に固定され、tag 移動・未認証・到達不能・digest 不一致が別々の理由で報告される |
| ~~**S3**~~ **完了** | `pack list` / `info` / `explain` / `outdated` / `update`（`source add\|list\|remove` は S2 で先行実装） | S2 | `info` が source / revision / digest / engine / 依存を一度に答え、`outdated` が行ごとに理由を報告し、`update` の失敗が旧版を残す |
| ~~**S4**~~ **完了** | 依存の**解決結果**（range を満たした version と tier）を lock に記録（schema 4）し `pack info` で報告 | S2 | AC 12 |
| **S5**（部分完了） | `pack export`・移行ガイド（`docs/pack-migration.md`）・同梱 pack を export→git 化→tag→install する統合テスト | S2 | AC 14 は達成。AC 3 は**リポジトリ作成が owner 判断**のため保留 |

**〔S5 実装時に見つかったブロッカー〕** `_pack_root` が「ルートにファイルが1つでもあると拒否」していたため、
**README を持つ pack リポジトリが作れなかった**。pack ディレクトリは宣言外のファイルを持てない（S1 の
権限モデルはこの性質に乗っている）ので、pack をリポジトリのルートに置くと README 自体が宣言外ファイルになる。
解決は **pack を1階層下に置き、リポジトリの持ち物をその上に置く**こと。install が持っていくのは pack
ディレクトリだけなので、リポジトリ側のファイルは利用者に届かない。pack root が2つあるリポジトリは
「最初の1つ」を選ばず拒否する。
| **S6** | 残る domain pack の外部化と `packs/` からの削除 | S5 | AC 13 |

**〔S4 実装時の訂正〕** 当初は「source 横断で依存を自動取得する」と書いていたが、実装前に測ってやめた。
**依存の欠落・range 不一致・循環は install 時に既に拒否されている**（`validate_tiered_collection`）。
欠けていたのは取得ではなく**記録**で、AC 12 の文言（「解決結果を lock できる」）もそう読める。
自動取得を足すなら「`{id, range}` だけの依存がどの source から来るのか」を決める必要があり、候補は
(a) 全 source を探索＝2つが同じ id を持った瞬間に曖昧、(b) 依存に source を書く＝D3 の
「配布経路を内容に溶接しない」に反する、のどちらかになる。**依存は明示 install で pin する**方が、
どちらの筋の悪さも買わずに済む。

**S1 を先頭に置く理由**: remote から取れるようになる前に権限モデルを固めておかないと、
「private repo から任意コード実行 pack を入れられる」窓が S2 と S1 の間に開く。順序が安全性を決める。

---

## 5. 着手前に人が決めること

1. **registry を v1 のスコープに入れるか。** Issue の source 候補には private registry があるが、
   Non-goals には marketplace がある。**git-only で v1 を切る**ことを推奨する——private git で
   AC 4/5/6 は満たせ、registry は S2 の scheme を1つ足す形で後から入る。
2. **`japanese-writing` 外部化と release gate の関係。** `scripts/validate.py` の
   `[PASS] release: Japanese-writing pack 0.6.0 requires engine >=2.3.0` は**同梱 pack を読んでいる**。
   外部化するとこの検査は対象を失う。lock を読む形に移すのか、外部 pack 側の CI へ渡すのかを S5 で決める。
3. **オフライン / 閉域での install をどう扱うか。** git に到達できない環境では S2 の経路が使えない。
   zip/tar による vendoring を正規の運用として認めるかどうか。
4. **`type` 追加時の既存 lock の扱い。** `pack_schema_version` を上げて、`type` の無い manifest を
   どう遇するか（拒否 / `skill` とみなす / 警告つき受理）。**拒否**を推奨する——推測で type を
   与えることは、権限モデルを推測で与えることと同じ。

---

## 6. この設計が保証しないこと

- **Pack の中身の質を保証しない。** 検査するのは manifest 契約・整合性・権限・出所であって、
  knowledge の正しさではない。Non-goals の「中央チームによる内容承認」を採らない以上、
  「install が通った」は「内容が正しい」を意味しない。
- **private であることは信頼の根拠にならない。** private repo の pack も、public と同じ
  manifest 検証・digest 照合・capability 検査・secret scan を通す。
- **digest 束縛は供給元の乗っ取りを防がない。** 同じ digest が再現することしか言えない。
  乗っ取りに対して効くのは署名（既存の trust roots）であって digest ではない。

---

> 文書の言語について: 本文書は Issue #523 と `docs/` の既存文書に合わせて日本語で書いている。
> prompt 層の英語化を進める判断が出た場合、翻訳負債がまだ無いこの文書は最初に英語へ寄せる候補になる。
