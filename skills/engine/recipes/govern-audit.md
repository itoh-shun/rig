---
name: govern-audit
description: 組織（org / team / project）の AI 品質ガバナンスを実測で監査する recipe。共通ポリシーが各チームのリポジトリに届いているか、権限・承認・例外・監査台帳が主張どおり効いているかを `rig-wb govern` の数値から診断し、乖離を重い順に出す。read-only。
scope: shipped
steps:
  - id: audit
    instruction: govern
    pattern: serial
    personas: [governance-auditor]
    output_contract: conformance-report
autonomy: interactive
---

# govern-audit

> **モード pack 注記**: rig engine（`SKILL.md`）を dev / harness-audit と**共用**する診断 pack の recipe。engine は書き換えず、`governance-auditor` persona・`quality-operating-system` knowledge・`govern` instruction・`conformance-report` output-contract を足すだけで成立する。`/rig:govern` から起動。判定そのものは `rig-wb govern`（決定論ランナー）が持ち、この recipe はその読み方だけを持つ。

## 使う場面

チームが複数あり、「**同じ品質基準で開発している**」が主張なのか実態なのかを確かめたい時。例:

- 「チーム A/B/C に共通ポリシーを配ったが、本当に効いている？」
- 「承認フローは回っているが、誰も止めたことがない気がする」（＝ゴム印の疑い）
- 「監査に『Q3 の override を全部出して』と言われた」
- 「`--force` が常用されている気がするが、数字で見たことがない」

## 何を見るか

| 概念 | 問い | 一次資料 |
|---|---|---|
| **policy** | 共通ポリシーは全リポジトリに届いているか | `govern policy show` / `rollup --json` |
| **permission** | 権限は配れているか（集中も空も無いか） | `govern whoami` / conformance `rbac_roles` `permission_holders` |
| **approval** | 承認は検証可能か（職務分離・鮮度） | conformance `approvals` |
| **waiver** | 例外は期限内か・恒久化していないか | `govern waiver list` / conformance `waivers` |
| **ledger** | 監査台帳は消せない形か | `govern audit verify` |
| **実行** | ゲートは満たされているか、回避されているか | conformance `force_rate`（**最重要の1数字**） |

`quality-operating-system` の優先順位で乖離を出す。最重は **ポリシー未到達**（比較対象が無ければ他の全指標が意味を失う）、次が **台帳の破損**（他の数字を信用できなくなる）。

## 展開

1. **対象確定** — 既定はカレントリポジトリ。チーム横断は `--all <親ディレクトリ>`（`govern rollup --scan`）。`--plan` で構成を提示して停止。
2. **実測** — `knowledge/quality-operating-system` を注入して `governance-auditor` を dispatch。`govern conformance --json` / `rollup --json` / `audit verify` の出力を一次資料にする（長い JSON は subagent で要点抽出＝context-minimal）。
3. **構造化提示** — `conformance-report`（総合行〔force 率を含む〕＋層の到達＋チーム別スコア表＋乖離〔重い順〕＋最優先の1手）。
4. **接続** — 修正は委譲：ポリシー改定は `rig-wb govern policy` と人間の判断、権限再配分は org 層の `roles`/`members`、例外の整理は `govern waiver revoke`、v1 資産の取り込みは `govern migrate`。監査自体は read-only。

手順本体は `facets/instructions/govern`、観点は `quality-operating-system`、出力は `output-contracts/conformance-report` に従う。

## ガード

- **「ある」と「効いている」を区別**（ポリシー文書の存在≠適合）。org 層が届いていなければ総合は「未統治」。
- **数字を一次に**（force 率・到達率・必須基準の実装率・例外の滞留・台帳の完全性）。未計測は「未計測」と書く（0% と書かない）。
- **quorum を上げる提案はしない**（効くのは職務分離と鮮度）。**承認でゲートを代替しない**。
- **緩和を黙って勧めない**。基準が実務に合っていないならポリシー改定として明示提案する。
- read-only（ポリシー・権限・台帳を勝手に書き換えない）。
