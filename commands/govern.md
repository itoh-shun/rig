---
description: "rig/govern — 組織ガバナンス pack。共通ポリシー（org→team→project の単調強化）・権限管理・承認フロー・例外（waiver）・改竄検知つき監査台帳を一級概念として扱い、チーム横断の適合性を実測する。判定の正本は `rig-wb govern`。"
argument-hint: "[audit|init|policy|approve|waiver] [対象パス/task-id] [--all <dir>] [--plan]"
---

# rig/govern — 組織ガバナンス（AI Quality Operating System の org 層）🏛️📋

**まず `rig:engine` skill を Skill ツールで起動し、その SKILL.md（PARSE → RESOLVE → COMPOSE → RUN・context-minimal・facet 配置順・知識層注入）に従うこと。** このコマンドは入口であり、エンジン本体は skill 側にある（重複定義しない）。既存 engine を「1リポジトリの品質」から「**組織の品質**」へ広げる govern pack。

```
$ARGUMENTS
```

## この層が解く問題

rig v1 の資産（acceptance-gate・隔離 worktree・独立検証・force-proof accept）は**1人1リポジトリでは完成している**。壊れるのは同じものをチーム A・B・C に配ったときだけ：**基準が同期しない**（`.rig/gates.json` はリポジトリごとに独立）、**権限が二値しかない**（accept できる人の名簿）、**承認が会話で記録でない**、**監査ログが編集できる**。v2 はこの4つを一級概念にする。

```
チーム A ─┐
チーム B ─┼─→ 共通ポリシー ─→ 権限管理 → 承認フロー → 例外 → 監査
チーム C ─┘        （単調強化＝下位は締められるだけ）
```

## サブモード

| 引数 | 何をするか |
|---|---|
| `audit`（既定） | 適合性を実測（`govern-audit` recipe）。`--all <dir>` でチーム横断。read-only |
| `init` | org/team を束ね、共通ポリシーの雛形を作る（既存 `.rig/access.json`/`.rig/gates.json` があれば `migrate` を先に提案） |
| `policy` | 効いているポリシーの提示・lint（層が上位を緩めていないか）・改定の相談 |
| `approve` | 承認の付与/却下・状態表示 |
| `waiver` | 例外の発行/一覧/取消 |

引数の先頭が該当語ならそれを、無ければ `audit` を既定に、残りを対象として PARSE する。

## やること

対象を `govern-audit` recipe に渡す。手順本体は `facets/instructions/govern` が正本、観点は `facets/knowledge/quality-operating-system`、出力は `facets/output-contracts/conformance-report`。

- **rig は判定しない＝`rig-wb govern` の出力を読む**。散文で「適合しています」と宣言しない（数字が無い観点は「未計測」）。
- **実作業は subagent が回す**（context-minimal）。長いポリシー JSON・台帳を親に引き込まない。
- **read-only**。ポリシー・権限・台帳を勝手に書き換えない（変更コマンドは人間に提示して実行させる）。

## 決定論ランナー（判定と記録の正本）

```
rig-wb govern init --org acme --team team-a       # リポジトリを org/team に束ね、雛形ポリシーを作る
rig-wb govern migrate --org acme                  # v1 の access.json / gates.json をポリシー層へ畳む
rig-wb govern policy show|lint                    # 効いている層の提示 / 緩め検査（exit 3 = 緩めている）
rig-wb govern whoami                              # 自分のロールと権限
rig-wb govern can accept.force                    # 単一権限の確認（exit 3 = 拒否）
rig-wb govern approve status|grant|deny <task-id> # 承認フロー（著者本人の承認は数えない）
rig-wb govern waiver grant <id> --criterion <c> --reason "..." --expires YYYY-MM-DD
rig-wb govern audit log|verify|export --format csv   # 台帳の閲覧 / 連鎖検証 / 監査提出
rig-wb govern conformance [--json]                # 1リポジトリの適合性（exit 3 = FAIL あり）
rig-wb govern rollup --scan <dir> [--json]        # チーム横断（team A/B/C → 共通ポリシー の表）
```

**既定で不活性**：`.rig/org.json` が無いリポジトリでは、これらは「未統治」と答えるだけで rig の挙動を一切変えない（個人開発は v1 と同一）。

## 共通ポリシーの唯一の設計制約：単調強化

org → team → project の順に重なり、**下位層は上位層を締めることしかできない**。

| 可 | 不可（`policy lint` が層とフィールドを名指して落とす） |
|---|---|
| criterion の追加 | 上位が要求する criterion の削除（省略しても継承する） |
| quorum の引き上げ | quorum の引き下げ |
| 承認期限・waiver 期限の短縮 | 延長 |
| role の権限を絞る | role に権限を足す・org が委譲していない権限で新ロールを作る |
| `non_waivable` の追加 | `required_for_force` / `audit.chain_required` の無効化 |
| 封印されていない role の付与 | `sealed_roles` への自己登録 |

**壊れたポリシーは fail-closed**：v1 の `.rig/access.json` は壊れていたら「無制限」に落ちたが（1人なら安全側）、ポリシー層は accept を**止める**。カンマ1個で組織の規則が静かに消えるのが、この層で唯一許されない失敗。

## accept との関係（チョークポイントは増やさない）

承認は acceptance-gate の**上乗せであって代替ではない**。ポリシーがあるとき `accept` は①accept 権限 ②承認 quorum（**職務分離**＝著者の承認は数えない／**鮮度**＝ブランチが動いたら失効）③`--force` 権限 ④例外の有効性 を通ってから squash merge に入る。関門は accept ただ1つのまま（2つ作れば片方だけ通る抜け道が生まれる）。

## flag

- `--all <dir>` … その直下のリポジトリ群を横断監査（`govern rollup --scan`）。チーム比較はこちら。
- `--plan` … 監査構成を提示して停止（ドライラン）。

## 例

```
/rig:govern                          # このリポジトリの適合性を実測
/rig:govern audit --all ~/work/acme  # チーム A/B/C 横断でスコア表を出す
/rig:govern init                     # org/team を束ねて共通ポリシーの雛形を作る
/rig:govern policy                   # 効いている層と、緩めている層がないかを見る
/rig:govern approve rig-20260807-...  # 承認の状態を見る／付ける
/rig:govern waiver                   # 生きている例外の一覧（恒久化していないか）
```

## 差別化（rig を通す価値）

素の AI に「ガバナンスを整えて」と頼むと、ポリシー文書の**雛形**が返る。文書は主張であって測定ではない。govern pack は共通ポリシーを**単調強化が保証された実行可能な層**にし、権限・承認・例外を accept の内側で機械的に問い、監査台帳を**編集が検出される形**にして、最後に「主張どおり効いているか」を **force 率・到達率・必須基準の実装率**という数字で返す。ポリシーを書くことと、ポリシーが効いていることの差が、この pack の全部。

## run-continuity（SKILL.md §6）

RUN 中は各ターン冒頭に次の run-status ヘッダを1行必ず再掲すること。中断・質疑・tool 出力の直後でも省かない:

```
▸ rig | recipe: govern-audit | step: <id> (<n>/<N>) | gate: <none|pending|passed|REJECT> | backend: <manual|workflow> | mode: <gated|autonomous>
```
